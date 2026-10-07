package stats

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLifecycleStopClosesRunOnceReader(t *testing.T) {
	for _, started := range []bool{false, true} {
		t.Run(fmt.Sprintf("started=%t", started), func(t *testing.T) {
			ctx := context.Background()
			writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			lifecycle := NewLifecycle(writer, LifecycleConfig{
				WindowSeconds: 1, MaxCells: 1, Cadence: time.Hour,
				Now: func() time.Time { return time.Unix(1000, 0) },
			})
			t.Cleanup(lifecycle.Stop)
			if err := lifecycle.RunOnce(ctx); err != nil {
				t.Fatal(err)
			}
			reader := lifecycle.readDatabase
			if reader == nil || reader.sqlDB == nil {
				t.Fatal("RunOnce did not retain its read database")
			}
			if started {
				if err := lifecycle.Start(ctx); err != nil {
					t.Fatal(err)
				}
			}
			done := lifecycle.done
			lifecycle.Stop()
			if started {
				select {
				case <-done:
				default:
					t.Fatal("Stop returned before the lifecycle worker exited")
				}
			}
			if lifecycle.readDatabase != nil || reader.sqlDB.PingContext(ctx) == nil {
				t.Fatal("Stop retained an open lifecycle read database")
			}
			if err := writer.db.PingContext(ctx); err != nil {
				t.Fatalf("Stop closed the independent writer: %v", err)
			}
			lifecycle.Stop()
		})
	}
}

func TestLifecycleCountersRetainLastPhaseDurations(t *testing.T) {
	var counters lifecycleCounters
	counters.recordSelection(60, 3*time.Millisecond)
	counters.recordMaterialization(60, 7*time.Millisecond, 4, 128, 2)

	snapshot := counters.snapshot()
	if snapshot.LastSelectionDuration != 3*time.Millisecond {
		t.Fatalf("last selection duration = %s, want 3ms", snapshot.LastSelectionDuration)
	}
	if snapshot.LastMaterializationDuration != 7*time.Millisecond {
		t.Fatalf("last materialization duration = %s, want 7ms", snapshot.LastMaterializationDuration)
	}
	resolution := snapshot.ByResolution[60]
	if resolution.LastSelectionDuration != 3*time.Millisecond || resolution.LastMaterializationDuration != 7*time.Millisecond {
		t.Fatalf("resolution phase durations = %+v", resolution)
	}
}

func TestLifecycleDemandRefreshDoesNotReselectExistingProduct(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	lifecycle := NewLifecycle(writer, LifecycleConfig{Now: func() time.Time { return time.Unix(1000, 0) }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(900, 10)
	first := lifecycle.demandRevision.Load()
	lifecycle.DemandProduct(900, 10)
	if got := lifecycle.demandRevision.Load(); got != first {
		t.Fatalf("repeated demand changed revision: first=%d got=%d", first, got)
	}
	lifecycle.DemandProduct(300, 1)
	if got := lifecycle.demandRevision.Load(); got != first+1 {
		t.Fatalf("new product demand did not change revision: first=%d got=%d", first, got)
	}
}

func emptyRingBucketPayload(t *testing.T, start, duration float64, generation int64, generatedAt float64) string {
	t.Helper()
	payload, err := encodeRingBucketPayload(MaterializedBucket{Start: start, Duration: duration, Complete: true}, nil, generation, generatedAt)
	if err != nil {
		t.Fatalf("encode empty ring bucket: %v", err)
	}
	return payload
}

func TestLifecycleReconcilesExternalUnavailableSpanWhenGenerationIsReused(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	seed := make([]RingBucketWrite, 0, 90)
	for bucket := int64(100); bucket < 1000; bucket += 10 {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: 10,
			BucketStart:       bucket,
			BucketJSON:        emptyRingBucketPayload(t, float64(bucket), 10, 1, 1000),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, 1000, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	writer.sourceGeneration.Store(1)

	// A legacy/direct owner can commit facts through another connection without
	// advancing the generation. The SQLite data_version signal must preserve
	// reconciliation even when ordinary Go writes later reuse that watermark.
	external, err := sql.Open("sqlite3", writerURI(path))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := external.ExecContext(ctx, `INSERT INTO unavailable_spans VALUES ('gpu','gpu:0','same-generation',950,970,10,'driver unavailable',1)`); err != nil {
		_ = external.Close()
		t.Fatal(err)
	}
	if err := external.Close(); err != nil {
		t.Fatal(err)
	}

	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: 900,
		MaxCells:      2,
		Now:           func() time.Time { return time.Unix(1000, 0) },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(900, 10)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}

	window, err := writer.ReadRingWindow(ctx, 900, 10, 1000)
	if err != nil {
		t.Fatal(err)
	}
	byStart := make(map[int64]RingBucketRow, len(window.Rows))
	for _, row := range window.Rows {
		byStart[row.BucketStart] = row
	}
	for _, bucketStart := range []int64{950, 960} {
		row, ok := byStart[bucketStart]
		if !ok {
			t.Fatalf("reconciled unavailable bucket %d missing: %+v", bucketStart, window)
		}
		_, noData, decodeErr := decodeRingBucketPayload(row.BucketJSON)
		if decodeErr != nil {
			t.Fatal(decodeErr)
		}
		if len(noData) != 1 || noData[0].Family != "gpu" || noData[0].Start != float64(bucketStart) || noData[0].End != float64(bucketStart+10) {
			t.Fatalf("bucket %d no-data = %+v", bucketStart, noData)
		}
	}
}

func TestIndexedFactPagesAdvanceTheirTimeRangeWithTheCursor(t *testing.T) {
	for _, family := range []string{"observations", "usage_atoms"} {
		for _, cursorTime := range []float64{0, 25, 90} {
			t.Run(fmt.Sprintf("%s/%g", family, cursorTime), func(t *testing.T) {
				capture := &queryCaptureDB{}
				database := &Database{reader: capture}
				var err error
				if family == "observations" {
					_, _, err = database.ReadObservationsPage(context.Background(), 25, 100,
						&observationPageCursor{ObservedAt: cursorTime}, 17)
				} else {
					_, _, err = database.ReadUsageAtomsPage(context.Background(), 25, 100,
						&usagePageCursor{ObservedAt: cursorTime}, 17)
				}
				if err != nil {
					t.Fatal(err)
				}
				if got, want := capture.args[0], max(float64(25), cursorTime); got != want {
					t.Fatalf("indexed page lower bound=%v want=%v; previous pages would be scanned again", got, want)
				}
				if capture.args[1] != float64(100) || capture.args[len(capture.args)-1] != 17 {
					t.Fatalf("page changed its end or row bound: args=%v", capture.args)
				}
			})
		}
	}
}

func TestFactPagesUseStableKeysetsAcrossEqualTimestamps(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	for offset := 0; offset < 600; offset += 100 {
		batch := AppendBatch{Observations: make([]Observation, 0, 100)}
		for index := offset; index < offset+100; index++ {
			batch.Observations = append(batch.Observations, Observation{
				EventID: fmt.Sprintf("event-%04d", index), Family: "cpu", SourceID: "host", ObservedAt: 100, EpochID: "epoch",
				Payload: map[string]any{"process_percent": float64(index)},
			})
		}
		if _, err := writer.AppendBatch(ctx, batch); err != nil {
			t.Fatal(err)
		}
	}
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	var cursor *observationPageCursor
	seen := make(map[string]bool)
	last := ""
	for {
		page, next, pageErr := database.ReadObservationsPage(ctx, 0, 101, cursor, 17)
		if pageErr != nil {
			t.Fatal(pageErr)
		}
		if len(page) > 17 {
			t.Fatalf("page length = %d", len(page))
		}
		for _, item := range page {
			if item.EventID <= last {
				t.Fatalf("page order regressed: %q after %q", item.EventID, last)
			}
			if seen[item.EventID] {
				t.Fatalf("duplicate page item %q", item.EventID)
			}
			seen[item.EventID] = true
			last = item.EventID
		}
		if len(page) < 17 {
			break
		}
		if next == nil {
			t.Fatal("full page did not return a cursor")
		}
		cursor = next
	}
	if len(seen) != 600 {
		t.Fatalf("paged observations = %d, want 600", len(seen))
	}
	readMetrics := database.ReadMetrics()
	if readMetrics.Queries == 0 {
		t.Fatalf("typed SQLite read metrics recorded no queries: %+v", readMetrics)
	}
	if readMetrics.Duration < 0 || readMetrics.LastDuration < 0 {
		t.Fatalf("typed SQLite read metrics contain negative duration: %+v", readMetrics)
	}
}

func TestUnavailableSpanPagesUseStableEndTimeKeysets(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	spans := make([]UnavailableSpan, 600)
	for index := range spans {
		spans[index] = UnavailableSpan{
			Family: "cpu", SourceID: fmt.Sprintf("host-%03d", index), EpochID: "epoch", StartedAt: float64(index), EndedAt: 1000,
			NativeCadenceSeconds: 1, Reason: "fixture gap", OwnerGeneration: 1,
		}
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{UnavailableSpans: spans}); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	var cursor *unavailablePageCursor
	seen := make(map[float64]bool)
	lastStartedAt := -1.0
	for {
		page, next, pageErr := database.ReadUnavailableSpansPage(ctx, 0, 1001, cursor, 17)
		if pageErr != nil {
			t.Fatal(pageErr)
		}
		if len(page) > 17 {
			t.Fatalf("page length = %d", len(page))
		}
		for _, item := range page {
			if item.EndedAt != 1000 || item.StartedAt <= lastStartedAt {
				t.Fatalf("unavailable span order regressed: %+v after started_at=%v", item, lastStartedAt)
			}
			if seen[item.StartedAt] {
				t.Fatalf("duplicate unavailable span started_at=%v", item.StartedAt)
			}
			seen[item.StartedAt] = true
			lastStartedAt = item.StartedAt
		}
		if len(page) < 17 {
			break
		}
		if next == nil {
			t.Fatal("full unavailable-span page did not return a cursor")
		}
		cursor = next
	}
	if len(seen) != len(spans) {
		t.Fatalf("paged unavailable spans = %d, want %d", len(seen), len(spans))
	}
}

func TestLifecyclePublishesFactsAndRepairsMutation(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 1000, Now: func() time.Time { return time.Unix(1999, 0) }})
	t.Cleanup(lifecycle.Stop)
	writer.AttachLifecycle(lifecycle)
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "one", Family: "cpu", SourceID: "host", ObservedAt: 1995, EpochID: "e", Payload: map[string]any{"process_percent": 2}}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if metrics := lifecycle.ReadMetrics(); metrics.Queries == 0 {
		t.Fatalf("lifecycle SQLite read metrics recorded no queries: %+v", metrics)
	}
	window, err := writer.ReadRingWindow(ctx, 10, 10, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Rows) == 0 || len(window.PendingInvalidations) != 0 {
		t.Fatalf("published window = %+v", window)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "two", Family: "cpu", SourceID: "host", ObservedAt: 1996, EpochID: "e", Payload: map[string]any{"process_percent": 4}}}}); err != nil {
		t.Fatal(err)
	}
	stale, err := writer.ReadRingWindow(ctx, 10, 10, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if len(stale.PendingInvalidations) != 1 {
		t.Fatalf("pending repair = %+v", stale)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	repaired, err := writer.ReadRingWindow(ctx, 10, 10, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if len(repaired.Rows) == 0 || len(repaired.PendingInvalidations) != 0 {
		t.Fatalf("repaired window = %+v", repaired)
	}
	if lifecycle.Counters().Publications < 2 || lifecycle.Counters().Repairs == 0 {
		t.Fatalf("counters = %+v", lifecycle.Counters())
	}
}

func TestLifecycleAppendNotificationsDoNotBypassCadence(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		Cadence:       25 * time.Millisecond,
		WindowSeconds: 100,
		MaxCells:      1,
		Now:           time.Now,
	})
	t.Cleanup(lifecycle.Stop)
	writer.AttachLifecycle(lifecycle)
	if err := lifecycle.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer lifecycle.Stop()

	deadline := time.NewTimer(140 * time.Millisecond)
	defer deadline.Stop()
	appendTicker := time.NewTicker(time.Millisecond)
	defer appendTicker.Stop()
	for {
		select {
		case <-deadline.C:
			counters := lifecycle.Counters()
			// A trigger storm must not add one run per append. Allow one small
			// scheduling margin around the six expected cadence ticks.
			if counters.Runs > 8 {
				t.Fatalf("append notifications bypassed cadence: runs=%d counters=%+v", counters.Runs, counters)
			}
			return
		case <-appendTicker.C:
			if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
				EventID: fmt.Sprintf("storm-%d", time.Now().UnixNano()), Family: "cpu", SourceID: "host",
				ObservedAt: float64(time.Now().UnixNano()) / 1e9, EpochID: "storm",
				Payload: map[string]any{"process_percent": 1},
			}}}); err != nil {
				t.Fatal(err)
			}
		}
	}
}

func TestLifecycleDemandLeavesBudgetForUnrelatedPendingCells(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	buckets := make([]RingBucketWrite, 0, 4)
	for start := int64(100); start < 140; start += 10 {
		buckets = append(buckets, RingBucketWrite{
			ResolutionSeconds: 10,
			BucketStart:       start,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":10,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, start),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, buckets, 1, 1000, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO unavailable_spans(family,source_id,epoch_id,started_at,ended_at,native_cadence_seconds,reason,owner_generation) VALUES (?,?,?,?,?,?,?,?)`, "gpu", "gpu:0", "demand-gap", 100, 140, 10, "synthetic", 2); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO ring_invalidations(resolution_seconds,bucket_start,source_generation,reason,created_at,applied_at) VALUES (?,?,?,?,?,NULL)`, 1, 900, 2, "unrelated", 1000); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=2 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}

	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 900, MaxCells: 4, Now: func() time.Time { return time.Unix(1000, 0) }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(900, 10)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, cell := range pending {
		if cell.ResolutionSeconds == 1 && cell.BucketStart == 900 {
			t.Fatalf("demand-derived cells starved unrelated pending work: selected=%+v counters=%+v", pending, lifecycle.Counters())
		}
	}
}

func TestLifecycleSingleCellDemandBeatsUnrelatedPendingCell(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10,
		BucketStart:       990,
		BucketJSON:        `{"start":990,"duration":10,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`,
		Complete:          true,
	}}, 1, 1000, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO ring_invalidations(resolution_seconds,bucket_start,source_generation,reason,created_at,applied_at) VALUES (?,?,?,?,?,NULL)`, 1, 900, 2, "unrelated", 1000); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=2 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "demanded-single-cell", Family: "cpu", SourceID: "host", ObservedAt: 995, EpochID: "epoch",
		Payload: map[string]any{"process_percent": 7},
	}}}); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(1000, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 1000, MaxCells: 1, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(900, 10)
	cells, err := lifecycle.selectCells(ctx, now, writer.sourceGeneration.Load())
	if err != nil {
		t.Fatal(err)
	}
	if len(cells) != 1 || cells[0].ResolutionSeconds != 10 || cells[0].BucketStart != 990 {
		t.Fatalf("single-cell demand was displaced by unrelated pending work: cells=%+v", cells)
	}
}

func TestLifecycleDefersOpenCoarseCellUntilLiveCadence(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	now := time.Unix(2_000_000_000, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: 86400,
		MaxCells:      1,
		Now:           func() time.Time { return now },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(86400, 300)
	current := lifecycleCell{ResolutionSeconds: 300, BucketStart: NextWindowEnd(now, 300) - 300}
	lifecycle.lastMaterializedAt[[2]int64{300, current.BucketStart}] = now

	cells, err := lifecycle.selectCells(ctx, now, writer.sourceGeneration.Load())
	if err != nil {
		t.Fatal(err)
	}
	if len(cells) == 1 && cells[0] == current {
		t.Fatalf("open coarse cell was selected before its live cadence: cells=%+v", cells)
	}

	now = now.Add(60 * time.Second)
	if !lifecycle.openCellDue(now, current) {
		t.Fatalf("open coarse cell was still deferred at its live cadence")
	}
}

func TestLifecycleDefaultRetentionUsesEachRingHorizon(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	lifecycle := NewLifecycle(writer, LifecycleConfig{Now: func() time.Time { return time.Unix(2_000_000_000, 0) }})
	t.Cleanup(lifecycle.Stop)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatalf("default lifecycle run failed: %v", err)
	}
	if lifecycle.Counters().Publications != 1 {
		t.Fatalf("publications = %+v, want one coherent publication", lifecycle.Counters())
	}
}

func TestLifecycleDefersUnrequestedColdBackfill(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(2_000_000_000, 0)
	current := now
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds:                  RetentionSeconds,
		BackgroundBackfillCadence:      time.Minute,
		BackgroundBackfillInitialDelay: time.Minute,
		Now:                            func() time.Time { return current },
	})
	t.Cleanup(lifecycle.Stop)
	bucketStart := current.Unix() - 1
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 1,
		BucketStart:       bucketStart,
		BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":1,"series":{},"source":{"count":0},"open":false}`, bucketStart),
		Complete:          true,
	}}, writer.SourceGeneration(), float64(current.Unix()), true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "deferred-pending", Family: "cpu", SourceID: "host", ObservedAt: float64(bucketStart), EpochID: "epoch",
		Payload: map[string]any{"process_percent": 7},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if got := lifecycle.Counters().MaterializedBuckets; got != 0 {
		t.Fatalf("unrequested cold backfill materialized %d buckets before delay", got)
	}
	if cells, err := lifecycle.selectCells(ctx, current, writer.SourceGeneration()); err != nil {
		t.Fatal(err)
	} else if len(cells) != 0 {
		t.Fatalf("unrequested cold backfill selected cells before delay: %+v", cells)
	}

	// A real stats request bypasses the low-priority gate immediately.
	lifecycle.DemandProduct(300, 1)
	cells, err := lifecycle.selectCells(ctx, current, writer.SourceGeneration())
	if err != nil {
		t.Fatal(err)
	}
	if len(cells) == 0 {
		t.Fatal("explicit stats demand did not select a cold product")
	}

	// Without a request, eventual convergence resumes after the configured
	// delay rather than being abandoned permanently.
	lifecycle = NewLifecycle(writer, LifecycleConfig{
		WindowSeconds:                  RetentionSeconds,
		BackgroundBackfillCadence:      time.Minute,
		BackgroundBackfillInitialDelay: time.Minute,
		Now:                            func() time.Time { return current },
	})
	t.Cleanup(lifecycle.Stop)
	current = now.Add(time.Minute)
	cells, err = lifecycle.selectCells(ctx, current, writer.SourceGeneration())
	if err != nil {
		t.Fatal(err)
	}
	if len(cells) == 0 {
		t.Fatal("delayed background backfill did not resume")
	}
}

func TestLifecycleSelectCellsPrioritizesPendingAndHonorsCellBound(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 990, BucketJSON: `{"value":1}`, Complete: true}}, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "late", Family: "cpu", SourceID: "host", ObservedAt: 995, EpochID: "e", Payload: map[string]any{"process_percent": 2}}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 100, MaxCells: 2, Now: func() time.Time { return time.Unix(999, 0) }})
	t.Cleanup(lifecycle.Stop)
	cells, err := lifecycle.selectCells(ctx, time.Unix(999, 0), 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(cells) != 2 || cells[0].ResolutionSeconds != 10 || cells[0].BucketStart != 990 {
		t.Fatalf("selected cells = %+v, want pending 10s/990 first and two cells total", cells)
	}
}

func TestLifecycleDemandPrioritizesRequestedProductWindow(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1000, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 1000, MaxCells: 2, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(900, 10)
	cells, err := lifecycle.selectCells(ctx, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(cells) != 2 {
		t.Fatalf("selected cells = %+v, want two demanded cells", cells)
	}
	for _, cell := range cells {
		if cell.ResolutionSeconds != 10 || cell.BucketStart < 110 || cell.BucketStart >= 1010 {
			t.Fatalf("selected unrelated cold-ring cell = %+v", cell)
		}
	}
}

func TestLifecycleDemandDoesNotMaterializeUnrequestedColdResolutions(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	// Leave one demanded 300-second bucket missing, but make the rest of the
	// requested product ready. The other rings remain completely cold. A
	// bounded pass should spend its remaining cells on the demanded resolution,
	// not on generic carriers and missing cells from unrelated rings.
	buckets := make([]RingBucketWrite, 0, 11)
	for bucket := int64(6600); bucket < 9900; bucket += 300 {
		buckets = append(buckets, RingBucketWrite{
			ResolutionSeconds: 300,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":300,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, bucket),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, buckets, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(10000, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 3600, MaxCells: 4, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(3600, 300)
	cells, err := lifecycle.selectCells(ctx, now, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(cells) == 0 {
		t.Fatalf("selected cells = %+v, want the demanded product repair", cells)
	}
	for _, cell := range cells {
		if cell.ResolutionSeconds != 300 {
			t.Fatalf("selected unrequested cold resolution: cells=%+v", cells)
		}
	}
}

func TestLifecycleDemandDoesNotTreatUnaffectedRowsAsStaleDuringGenerationChurn(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	seed := make([]RingBucketWrite, 0, 300)
	for bucket := int64(700); bucket < 1000; bucket++ {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: 1,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":1,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, bucket),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, 1000, true); err != nil {
		t.Fatal(err)
	}
	result, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID:    "browser-churn",
		Family:     "browser",
		SourceID:   "client",
		ObservedAt: 999,
		EpochID:    "browser-epoch",
		Payload:    map[string]any{"kind": "api", "status": 200},
	}}})
	if err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 300, MaxCells: 64, Now: func() time.Time { return time.Unix(1000, 0) }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(300, 1)
	cells, err := lifecycle.selectCells(ctx, time.Unix(1000, 0), result.Generation)
	if err != nil {
		t.Fatal(err)
	}
	for _, cell := range cells {
		if cell.ResolutionSeconds == 1 && cell.BucketStart != 999 && cell.BucketStart != 1000 {
			t.Fatalf("generation churn selected unaffected 1s row: cells=%+v", cells)
		}
	}
}

func TestLifecycleReconcilesUntrackedGenerationAlongsidePendingTelemetry(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	seed := make([]RingBucketWrite, 0, 300)
	for bucket := int64(700); bucket < 1000; bucket++ {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: 1,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":1,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, bucket),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, 1000, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "telemetry", Family: "cpu", SourceID: "host", ObservedAt: 995, EpochID: "epoch",
		Payload: map[string]any{"process_percent": 2},
	}}}); err != nil {
		t.Fatal(err)
	}
	// Simulate an older/direct writer changing a historical source without
	// recording a per-cell ledger row. A later normal append must not make this
	// reconciliation disappear behind the valid pending telemetry cell.
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO unavailable_spans VALUES ('gpu','host','external',800,801,1,'driver unavailable',3)`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=3 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 300, MaxCells: 4, Now: func() time.Time { return time.Unix(1000, 0) }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(300, 1)
	cells, err := lifecycle.selectCells(ctx, time.Unix(1000, 0), 3)
	if err != nil {
		t.Fatal(err)
	}
	seenReconciliation, seenPending := false, false
	for _, cell := range cells {
		if cell.ResolutionSeconds != 1 {
			continue
		}
		seenReconciliation = seenReconciliation || cell.BucketStart == 998
		seenPending = seenPending || cell.BucketStart == 995
	}
	if !seenReconciliation || !seenPending {
		t.Fatalf("untracked historical cell was starved by pending telemetry: cells=%+v", cells)
	}
}

func TestLifecycleReconcilesHistoricalGenerationAfterWriterRestartAndTailChurn(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	base := time.Now().Unix()
	historicalBucket := base - 200
	seed := make([]RingBucketWrite, 0, 300)
	for bucket := base - 300; bucket < base; bucket++ {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: 1,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":1,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, bucket),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, float64(base), true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"restart-historical", "cpu", "host", float64(historicalBucket), "external", 2, `{"process_percent":77}`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=2 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writer, err = OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	foundHistorical := false
	for _, cell := range pending {
		if cell.ResolutionSeconds == 1 && cell.BucketStart == historicalBucket {
			foundHistorical = true
			break
		}
	}
	if !foundHistorical {
		t.Fatalf("restart did not persist historical reconciliation: pending=%+v", pending)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: 300,
		MaxCells:      4,
		Now:           func() time.Time { return time.Unix(base, 0) },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(300, 1)
	for attempt := 0; attempt < 8; attempt++ {
		if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
			EventID: fmt.Sprintf("restart-tail-%d", attempt), Family: "cpu", SourceID: "host",
			ObservedAt: float64(base - 5 + int64(attempt%2)), EpochID: "tail", Payload: map[string]any{"process_percent": 10 + attempt},
		}}}); err != nil {
			t.Fatal(err)
		}
		cells, err := lifecycle.selectCells(ctx, time.Unix(base, 0), writer.SourceGeneration())
		if err != nil {
			t.Fatal(err)
		}
		for _, cell := range cells {
			if cell.ResolutionSeconds == 1 && cell.BucketStart == historicalBucket {
				return
			}
		}
	}
	t.Fatalf("historical bucket %d was starved after writer restart and tail churn; source_generation=%d", historicalBucket, writer.SourceGeneration())
}

func TestLifecycleReconcilesOlderRowsWhenPublicationGenerationCatchesUp(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	base := time.Now().Unix()
	historicalBucket := base - 200
	seed := make([]RingBucketWrite, 0, 300)
	for bucket := base - 300; bucket < base; bucket++ {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: 1,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":1,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, bucket),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, float64(base), true); err != nil {
		t.Fatal(err)
	}
	// Simulate a prior owner that changed a historical fact, then published an
	// unrelated cell at the same source generation. The publication watermark
	// catches up, so a restart cannot rely on source_generation != publication.
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"publication-caught-up", "cpu", "host", float64(historicalBucket), "external", 2, `{"process_percent":77}`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=2 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 1,
		BucketStart:       base - 1,
		BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":1,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, base-1),
		Complete:          true,
	}}, 2, float64(base+1), true); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writer, err = OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	foundHistorical := false
	for _, cell := range pending {
		if cell.ResolutionSeconds == 1 && cell.BucketStart == historicalBucket {
			foundHistorical = true
			break
		}
	}
	if !foundHistorical {
		t.Fatalf("restart did not persist stale populated slot: pending=%+v", pending)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "publication-caught-up-tail", Family: "cpu", SourceID: "host",
		ObservedAt: float64(base - 5), EpochID: "tail", Payload: map[string]any{"process_percent": 10},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: 300,
		MaxCells:      4,
		Now:           func() time.Time { return time.Unix(base, 0) },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(300, 1)
	cells, err := lifecycle.selectCells(ctx, time.Unix(base, 0), writer.SourceGeneration())
	if err != nil {
		t.Fatal(err)
	}
	for _, cell := range cells {
		if cell.ResolutionSeconds == 1 && cell.BucketStart == historicalBucket {
			return
		}
	}
	t.Fatalf("historical bucket %d was starved after publication watermark caught up; cells=%+v", historicalBucket, cells)
}

func TestLifecyclePublishesAndSettlesStartupReconciliation(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	base := time.Now().Unix()
	historicalBucket := base - 200
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	seed := make([]RingBucketWrite, 0, 300)
	for bucket := base - 300; bucket < base; bucket++ {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: 1,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":1,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, bucket),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, float64(base)-1, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"settle-historical", "cpu", "host", float64(historicalBucket), "external", 2, `{"process_percent":77}`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=2 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 1,
		BucketStart:       base - 1,
		BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":1,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, base-1),
		Complete:          true,
	}}, 2, float64(base)-0.5, true); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writer, err = OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	hasHistoricalInvalidation := func(cells []InvalidationCell) bool {
		for _, cell := range cells {
			if cell.ResolutionSeconds == 1 && cell.BucketStart == historicalBucket {
				return true
			}
		}
		return false
	}
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !hasHistoricalInvalidation(pending) {
		t.Fatalf("restart did not persist historical reconciliation: pending=%+v", pending)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: 300,
		MaxCells:      2,
		Now:           func() time.Time { return time.Unix(base, 0) },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(300, 1)
	settled := false
	for attempt := 0; attempt < 4; attempt++ {
		if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
			EventID: fmt.Sprintf("settle-tail-%d", attempt), Family: "cpu", SourceID: "host",
			ObservedAt: float64(base - 5 + int64(attempt%2)), EpochID: "tail", Payload: map[string]any{"process_percent": 10 + attempt},
		}}}); err != nil {
			t.Fatal(err)
		}
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		pending, err = writer.PendingInvalidationCells(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if !hasHistoricalInvalidation(pending) {
			settled = true
			break
		}
	}
	if !settled {
		t.Fatalf("bounded lifecycle passes did not retire historical reconciliation: pending=%+v", pending)
	}
	window, err := writer.ReadRingWindow(ctx, 300, 1, base)
	if err != nil {
		t.Fatal(err)
	}
	findHistorical := func(candidate RingWindow) (RingBucketRow, bool) {
		for _, row := range candidate.Rows {
			if row.BucketStart == historicalBucket {
				return row, true
			}
		}
		return RingBucketRow{}, false
	}
	repaired, found := findHistorical(window)
	if !found {
		t.Fatalf("reconciliation did not publish historical bucket: rows=%+v", window.Rows)
	}
	bucket, _, err := decodeRingBucketPayload(repaired.BucketJSON)
	if err != nil {
		t.Fatal(err)
	}
	var foundSeries bool
	for _, series := range bucket.Series {
		if series.Name == "cpu_percent:host" {
			foundSeries = true
			if series.Value != 77 {
				t.Fatalf("repaired cpu value=%v, want 77", series.Value)
			}
		}
	}
	if !foundSeries {
		t.Fatalf("repaired bucket has no cpu series: %+v", bucket)
	}
	if repaired.SourceGeneration != writer.SourceGeneration() {
		t.Fatalf("repaired source generation=%d, writer=%d", repaired.SourceGeneration, writer.SourceGeneration())
	}
	repairedJSON := repaired.BucketJSON
	repairedSourceGeneration := repaired.SourceGeneration
	repairedRingGeneration := repaired.RingGeneration
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writer, err = OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	pending, err = writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if hasHistoricalInvalidation(pending) {
		t.Fatalf("settled historical invalidation reappeared after restart: %+v", pending)
	}
	reopenedWindow, err := writer.ReadRingWindow(ctx, 300, 1, base)
	if err != nil {
		t.Fatal(err)
	}
	reopened, found := findHistorical(reopenedWindow)
	if !found || reopened.BucketJSON != repairedJSON || reopened.SourceGeneration != repairedSourceGeneration || reopened.RingGeneration != repairedRingGeneration {
		t.Fatalf("repaired historical bucket changed across restart: before=%+v after=%+v found=%t", repaired, reopened, found)
	}
	secondLifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: 300,
		MaxCells:      1,
		Now:           func() time.Time { return time.Unix(base, 0) },
	})
	t.Cleanup(secondLifecycle.Stop)
	secondLifecycle.DemandProduct(300, 1)
	if err := secondLifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	stableWindow, err := writer.ReadRingWindow(ctx, 300, 1, base)
	if err != nil {
		t.Fatal(err)
	}
	stable, found := findHistorical(stableWindow)
	if !found || stable.BucketJSON != repairedJSON || stable.SourceGeneration != repairedSourceGeneration || stable.RingGeneration != repairedRingGeneration {
		t.Fatalf("no-change pass republished settled historical bucket: before=%+v after=%+v found=%t", repaired, stable, found)
	}
	pending, err = writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if hasHistoricalInvalidation(pending) {
		t.Fatalf("no-change pass recreated historical invalidation: %+v", pending)
	}
}

func TestLifecycleSelectCellsRepairsNewestStaleRowsBeforeCarrier(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	bucket := `{"start":980,"duration":10,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 980, BucketJSON: bucket, Complete: true}}, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 100, MaxCells: 1, Now: func() time.Time { return time.Unix(999, 0) }})
	t.Cleanup(lifecycle.Stop)
	cells, err := lifecycle.selectCells(ctx, time.Unix(999, 0), 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(cells) != 1 || cells[0].ResolutionSeconds != 10 || cells[0].BucketStart != 980 {
		t.Fatalf("selected cells = %+v, want newest stale 10s/980 before missing carrier", cells)
	}
}

func TestLifecycleSelectCellsDoesNotStarveCoarseResolutions(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 1000, MaxCells: 16, Now: func() time.Time { return time.Unix(1000, 0) }})
	t.Cleanup(lifecycle.Stop)
	cells, err := lifecycle.selectCells(ctx, time.Unix(1000, 0), 0)
	if err != nil {
		t.Fatal(err)
	}
	seen := make(map[int]bool)
	for _, cell := range cells {
		seen[cell.ResolutionSeconds] = true
	}
	for _, resolution := range lifecycleResolutions {
		if !seen[resolution] {
			t.Fatalf("resolution %d was starved by fine-ring repair: cells=%+v", resolution, cells)
		}
	}
	for index := 1; index < len(cells); index++ {
		previous, current := cells[index-1], cells[index]
		if previous.ResolutionSeconds > current.ResolutionSeconds ||
			(previous.ResolutionSeconds == current.ResolutionSeconds && previous.BucketStart > current.BucketStart) {
			t.Fatalf("selected cells are not materializer-ordered: %+v", cells)
		}
	}
}

func TestLifecycleCellBoundLeavesMissingRingWorkDurable(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 100, MaxCells: 2, Now: func() time.Time { return time.Unix(999, 0) }})
	t.Cleanup(lifecycle.Stop)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	window, err := writer.ReadRingWindow(ctx, 100, 10, 1000)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Rows) == 0 || len(window.MissingBucketStarts) == 0 {
		t.Fatalf("bounded publication did not leave durable remainder: %+v", window)
	}
}

func TestLifecycleNoOpDoesNotAdvanceRingPublication(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(999, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 10, MaxCells: 64, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	first, _, err := writer.RingPublicationState(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	second, _, err := writer.RingPublicationState(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if second != first {
		t.Fatalf("no-op lifecycle advanced ring generation from %d to %d", first, second)
	}
}

func TestLifecycleFactBudgetLeavesOversizedCellUnpublished(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	batch := AppendBatch{Observations: make([]Observation, 0, 300)}
	for index := 0; index < 300; index++ {
		batch.Observations = append(batch.Observations, Observation{
			EventID: fmt.Sprintf("event-%03d", index), Family: "cpu", SourceID: "host", ObservedAt: 995 + float64(index)/10000, EpochID: "epoch",
			Payload: map[string]any{"process_percent": float64(index)},
		})
	}
	if _, err := writer.AppendBatch(ctx, batch); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(999, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 100, MaxCells: 64, MaxFactRows: 2, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	window, err := writer.ReadRingWindow(ctx, 100, 10, 1000)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Rows) != 0 && len(window.MissingBucketStarts) == 0 {
		t.Fatalf("oversized cell was published without durable remainder: %+v", window)
	}
	if len(window.MissingBucketStarts) == 0 {
		t.Fatalf("oversized cell lost its durable missing state: %+v", window)
	}
}

func TestLifecycleCompletesOneBoundedCoarseCellPastRunBudget(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "coarse-cell", Family: "cpu", SourceID: "host", ObservedAt: 199900, EpochID: "epoch",
		Payload: map[string]any{"process_percent": 12},
	}}}); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(200000, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: RetentionSeconds,
		MaxCells:      1,
		MaxRunTime:    time.Nanosecond,
		Now:           func() time.Time { return now },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(3600, 300)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}

	window, err := writer.ReadRingWindow(ctx, 3600, 300, NextWindowEnd(now, 300))
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Rows) != 1 {
		t.Fatalf("one bounded coarse cell should publish even when it exceeds the pass budget: rows=%d missing=%d", len(window.Rows), len(window.MissingBucketStarts))
	}
	if len(window.MissingBucketStarts) != 11 {
		t.Fatalf("one-cell pass should leave the other 11 product buckets for later passes: missing=%d rows=%d", len(window.MissingBucketStarts), len(window.Rows))
	}
}

func TestLifecycleFactBudgetDoesNotStarveLaterCells(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	observations := make([]Observation, 0, 301)
	for index := 0; index < 300; index++ {
		observations = append(observations, Observation{
			EventID: fmt.Sprintf("dense-%03d", index), Family: "cpu", SourceID: "host", ObservedAt: 1000 + float64(index)/1000, EpochID: "epoch",
			Payload: map[string]any{"process_percent": float64(index)},
		})
	}
	observations = append(observations, Observation{
		EventID: "later-cell", Family: "cpu", SourceID: "host", ObservedAt: 1015, EpochID: "epoch",
		Payload: map[string]any{"process_percent": 7},
	})
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: observations}); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1100, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 200, MaxCells: 64, MaxFactRows: 2, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	window, err := writer.ReadRingWindow(ctx, 200, 10, 1100)
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range window.Rows {
		if row.BucketStart == 1010 {
			return
		}
	}
	t.Fatalf("later cell was starved by dense cell: rows=%+v missing=%v pending=%v", window.Rows, window.MissingBucketStarts, window.PendingInvalidations)
}

func TestLifecycleDemandedPendingCellPublishesBeforeColdRingBacklog(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1000, 0)
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10,
		BucketStart:       990,
		BucketJSON:        `{"start":990,"duration":10,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`,
		Complete:          true,
	}}, 1, 1000, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID:    "demanded",
		Family:     "cpu",
		SourceID:   "host",
		ObservedAt: 995,
		EpochID:    "epoch",
		Payload:    map[string]any{"process_percent": 7},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 1000, MaxCells: 1, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(900, 10)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, cell := range pending {
		if cell.ResolutionSeconds == 10 && cell.BucketStart == 990 {
			t.Fatalf("demanded pending cell was not published: %+v", pending)
		}
	}
}

func TestLifecycleFineDemandConvergesWithCatalogAndPriorDemand(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(200000, 0)
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{
		{EventID: "cpu-1", Family: "cpu", SourceID: "host", ObservedAt: 199880, EpochID: "cpu", Payload: map[string]any{"process_percent": 10}},
		{EventID: "cpu-2", Family: "cpu", SourceID: "host", ObservedAt: 199940, EpochID: "cpu", Payload: map[string]any{"process_percent": 20}},
		{EventID: "cpu-3", Family: "cpu", SourceID: "host", ObservedAt: 199999, EpochID: "cpu", Payload: map[string]any{"process_percent": 30}},
	}}); err != nil {
		t.Fatal(err)
	}
	keys := make([]ProductKey, 0)
	for _, window := range []int{300, 900, 1800, 3600, 7200, 14400, 28800, 57600, 86400} {
		for _, resolution := range lifecycleResolutions {
			if supportedProductKey(ProductKey{RangeSeconds: window, ResolutionSeconds: resolution}) {
				keys = append(keys, ProductKey{RangeSeconds: window, ResolutionSeconds: resolution})
			}
		}
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: RetentionSeconds, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := NewStatsService(writer, StatsServiceConfig{Products: keys, Now: func() time.Time { return now }})
	lifecycle.DemandProduct(900, 10)
	for attempt := 0; attempt < 64; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		lifecycle.DemandProduct(300, 1)
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, ErrProductPending) {
			t.Fatal(err)
		}
		if service.State().Ready {
			break
		}
	}
	if !service.State().Ready {
		t.Fatalf("catalog did not converge after bounded lifecycle passes: state=%+v counters=%+v", service.State(), lifecycle.Counters())
	}
	snapshot, err := service.Snapshot(ctx, SnapshotRequest{Start: 199701, End: 200001, Resolution: 1})
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Materialized.Buckets) != 300 {
		t.Fatalf("fine demanded product buckets=%d, want 300", len(snapshot.Materialized.Buckets))
	}
}

func TestLifecycleDemandedCoarseRangeSplitsOversizedFold(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	observations := make([]Observation, 0, 12000)
	for index := 0; index < cap(observations); index++ {
		observations = append(observations, Observation{
			EventID: fmt.Sprintf("coarse-%05d", index), Family: "cpu", SourceID: "host",
			ObservedAt: float64(180000 + index), EpochID: "coarse",
			Payload: map[string]any{"process_percent": float64(index % 100)},
		})
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: observations}); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(200000, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: RetentionSeconds,
		MaxCells:      40,
		Now:           func() time.Time { return now },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(86400, 300)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, cell := range pending {
		if cell.ResolutionSeconds == 300 && cell.BucketStart >= 180000 && cell.BucketStart < 192000 {
			t.Fatalf("demanded coarse cells remain pending after bounded folds: %+v", pending)
		}
	}
	service := NewStatsService(writer, StatsServiceConfig{
		Products: []ProductKey{{RangeSeconds: 86400, ResolutionSeconds: 300}},
		Now:      func() time.Time { return now },
	})
	for attempt := 0; attempt < 64; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, ErrProductPending) {
			t.Fatal(err)
		}
		window, err := writer.ReadRingWindowMetadata(ctx, 86400, 300, NextWindowEnd(now, 300))
		if err != nil {
			t.Fatal(err)
		}
		if len(window.MissingBucketStarts) == 0 && len(window.PendingInvalidations) == 0 {
			break
		}
	}
	window, err := writer.ReadRingWindowMetadata(ctx, 86400, 300, NextWindowEnd(now, 300))
	if err != nil {
		t.Fatal(err)
	}
	if len(window.MissingBucketStarts) != 0 || len(window.PendingInvalidations) != 0 {
		t.Fatalf("24-hour coarse ring remains incomplete: missing=%d pending=%d", len(window.MissingBucketStarts), len(window.PendingInvalidations))
	}
	if !service.State().Ready {
		t.Fatalf("24-hour coarse product did not converge: state=%+v counters=%+v", service.State(), lifecycle.Counters())
	}
	snapshot, err := service.CurrentSnapshot(ctx, 86400, 300)
	if err != nil {
		t.Fatal(err)
	}
	if got := len(snapshot.Materialized.Buckets); got != 288 {
		t.Fatalf("24-hour coarse bucket count = %d, want 288", got)
	}
	if len(snapshot.Materialized.NoData) != 0 {
		t.Fatalf("24-hour coarse product has unexpected no-data spans: %+v", snapshot.Materialized.NoData)
	}
	counters := lifecycle.Counters()
	if counters.SelectedCells == 0 || counters.MaterializedBuckets == 0 || counters.FactRowsRead < uint64(len(observations)) || counters.DecodedBytes == 0 {
		t.Fatalf("large-backlog work counters = %+v", counters)
	}
	if counters.SelectionDuration <= 0 || counters.MaterializationDuration <= 0 || counters.FactReadDuration <= 0 || counters.LastFactReadDuration <= 0 {
		t.Fatalf("large-backlog timing counters = %+v", counters)
	}
	resolutionCounters, ok := counters.ByResolution[300]
	if !ok || resolutionCounters.SelectionRuns == 0 || resolutionCounters.MaterializationRuns == 0 || resolutionCounters.SelectedCells == 0 || resolutionCounters.MaterializedBuckets == 0 || resolutionCounters.FactRowsRead < uint64(len(observations)) || resolutionCounters.DecodedBytes == 0 || resolutionCounters.SelectionDuration <= 0 || resolutionCounters.MaterializationDuration <= 0 {
		t.Fatalf("per-resolution work counters = %+v", counters.ByResolution)
	}
}

func BenchmarkEstimateDecodedBytes(b *testing.B) {
	payload := map[string]any{"process_percent": 42.5, "system_percent": 17.25, "rss_bytes": 123456, "binary": "yolomux-go"}
	observations := make([]Observation, 256)
	for index := range observations {
		observations[index] = Observation{EventID: fmt.Sprintf("event-%d", index), Family: "cpu", SourceID: "host", EpochID: "epoch", Payload: payload, payloadBytes: len(mustJSON(payload))}
	}
	b.Run("stored_payload_size", func(b *testing.B) {
		b.ReportAllocs()
		for index := 0; index < b.N; index++ {
			_ = estimateDecodedBytes(observations, nil, nil, nil)
		}
	})
	b.Run("legacy_remarshal", func(b *testing.B) {
		b.ReportAllocs()
		for index := 0; index < b.N; index++ {
			bytes := 0
			for _, value := range observations {
				bytes += len(mustJSON(value.Payload)) + len(value.EventID) + len(value.Family) + len(value.SourceID) + len(value.EpochID) + 64
			}
			_ = bytes
		}
	})
}

func TestLifecycleVacuumFailureHonorsRetryDeadline(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		VacuumCadence: time.Second,
		VacuumRetry:   time.Hour,
		Now:           func() time.Time { return time.Unix(100, 0) },
	})
	t.Cleanup(lifecycle.Stop)
	// Close the writer to make the rewrite fail deterministically. The scheduler
	// must retain the due state while suppressing the next one-second lifecycle
	// tick until the configured retry deadline.
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.vacuumIfDue(ctx, 100); err == nil {
		t.Fatal("vacuum failure was not reported")
	}
	if lifecycle.vacuumRetryAt != 100+time.Hour.Seconds() {
		t.Fatalf("vacuum retry deadline = %v, want %v", lifecycle.vacuumRetryAt, 100+time.Hour.Seconds())
	}
	if err := lifecycle.vacuumIfDue(ctx, 101); err != nil {
		t.Fatalf("retry guard returned an error before its deadline: %v", err)
	}
}

func TestLifecycleVacuumDefersOnActiveReaderDemandThenPersistsCompletion(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	seed := make([]Observation, 0, 512)
	for index := range 512 {
		seed = append(seed, Observation{EventID: fmt.Sprintf("vacuum-%04d", index), Family: "cpu", SourceID: "host", ObservedAt: 1, EpochID: "vacuum", Payload: map[string]any{"padding": strings.Repeat("x", 2048)}})
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: seed}); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = reader.Close() })
	snapshot, closeSnapshot, err := reader.beginReadSnapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(closeSnapshot)
	if rows, err := snapshot.ReadObservations(ctx, 0, 2, MaxFactReadLimit); err != nil || len(rows) != 512 {
		t.Fatalf("pin synthetic reader snapshot before vacuum: rows=%d err=%v", len(rows), err)
	}
	if _, err := writer.db.ExecContext(ctx, "DELETE FROM observations"); err != nil {
		t.Fatal(err)
	}
	if rows, err := snapshot.ReadObservations(ctx, 0, 2, MaxFactReadLimit); err != nil || len(rows) != 512 {
		t.Fatalf("held synthetic reader snapshot lost pre-delete rows: rows=%d err=%v", len(rows), err)
	}
	// The lifecycle's busy callback represents active read demand. Hold the
	// actual SQLite snapshot at the same time so the deferral/retry transition
	// runs while an older reader can still observe pre-delete facts.
	snapshotHeld := true
	lifecycle := NewLifecycle(writer, LifecycleConfig{
		VacuumCadence:  time.Second,
		VacuumMaxDefer: 10 * time.Second,
		VacuumRetry:    time.Second,
		Busy:           func() bool { return snapshotHeld },
		Now:            func() time.Time { return time.Unix(100, 0) },
	})
	t.Cleanup(lifecycle.Stop)
	if err := lifecycle.vacuumIfDue(ctx, 100); err != nil {
		t.Fatalf("busy-reader vacuum returned an error: %v", err)
	}
	if lifecycle.vacuumDueSince != 100 || lifecycle.lastVacuumAt != 0 {
		t.Fatalf("busy-reader vacuum state = due_since=%v last=%v", lifecycle.vacuumDueSince, lifecycle.lastVacuumAt)
	}
	marker, err := writer.LastVacuumedAt(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if marker != 0 {
		t.Fatalf("busy-reader vacuum persisted completion marker %v", marker)
	}

	snapshotHeld = false
	closeSnapshot()
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.vacuumIfDue(ctx, 101); err != nil {
		t.Fatalf("idle-reader vacuum failed: %v", err)
	}
	marker, err = writer.LastVacuumedAt(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if marker != 101 || lifecycle.lastVacuumAt != 101 || lifecycle.vacuumDueSince != 0 {
		t.Fatalf("idle-reader vacuum state = marker=%v last=%v due_since=%v", marker, lifecycle.lastVacuumAt, lifecycle.vacuumDueSince)
	}
	if ratio := writer.reclaimableRatioAtLastVacuum(); ratio < 0 || ratio > 1 {
		t.Fatalf("vacuum baseline ratio = %v", ratio)
	}
}

func TestLifecycleRepairsUnavailableSpanAfterExternalGenerationBump(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	// Seed a complete 15-minute 10-second ring, then mutate the source tables
	// through the legacy/direct-writer path that does not leave a ledger row.
	seed := make([]RingBucketWrite, 0, 90)
	for bucket := int64(100); bucket < 1000; bucket += 10 {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: 10,
			BucketStart:       bucket,
			BucketJSON:        emptyRingBucketPayload(t, float64(bucket), 10, 1, 1000),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, 1000, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO unavailable_spans VALUES ('gpu','gpu:0','legacy',950,970,10,'driver unavailable',2)`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=2 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}

	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: 900,
		MaxCells:      2,
		Now:           func() time.Time { return time.Unix(1000, 0) },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(900, 10)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}

	window, err := writer.ReadRingWindow(ctx, 900, 10, 1000)
	if err != nil {
		t.Fatal(err)
	}
	byStart := make(map[int64]RingBucketRow, len(window.Rows))
	for _, row := range window.Rows {
		byStart[row.BucketStart] = row
	}
	for _, bucketStart := range []int64{950, 960} {
		row, ok := byStart[bucketStart]
		if !ok {
			t.Fatalf("repaired unavailable bucket %d missing: %+v", bucketStart, window)
		}
		_, noData, decodeErr := decodeRingBucketPayload(row.BucketJSON)
		if decodeErr != nil {
			t.Fatal(decodeErr)
		}
		if len(noData) != 1 || noData[0].Family != "gpu" || noData[0].Start != float64(bucketStart) || noData[0].End != float64(bucketStart+10) {
			t.Fatalf("bucket %d no-data = %+v", bucketStart, noData)
		}
	}
}

func TestLifecycleDoesNotReselectIncompleteUnavailableHistoryAfterUnrelatedGenerationAdvance(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	now := time.Unix(10000, 0)
	windowEnd := NextWindowEnd(now, 300)
	targetBucket := windowEnd - 600
	seed := make([]RingBucketWrite, 0, 12)
	for bucket := windowEnd - 3600; bucket < windowEnd; bucket += 300 {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: 300,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":300,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, bucket),
			Complete:          bucket != targetBucket,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, float64(windowEnd), true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO unavailable_spans VALUES ('gpu','gpu:0','historical-gap',?,?,300,'driver unavailable',1)`, targetBucket, targetBucket+300); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	writer.sourceGeneration.Store(1)

	// Advance the source through an unrelated current cell. Its durable
	// invalidation must not make the old incomplete no-data cell a retry loop.
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "unrelated-current", Family: "cpu", SourceID: "host", ObservedAt: float64(windowEnd - 1), EpochID: "epoch",
		Payload: map[string]any{"process_percent": 7},
	}}}); err != nil {
		t.Fatal(err)
	}

	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 3600, MaxCells: 64, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(3600, 300)
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	snapshot, closeSnapshot, err := database.beginReadSnapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer closeSnapshot()
	generation, err := snapshot.snapshotGeneration(ctx)
	if err != nil {
		t.Fatal(err)
	}
	cells, err := lifecycle.selectCellsFromSnapshot(ctx, now, generation, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	for _, cell := range cells {
		if cell.ResolutionSeconds == 300 && cell.BucketStart == targetBucket {
			t.Fatalf("historical incomplete unavailable cell was reselected after unrelated generation advance: %+v", cells)
		}
	}
}

func TestLifecycleStartupReconciliationPublishesAndConvergesAfterRestart(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	now := time.Unix(time.Now().Unix(), 0)
	windowEnd := NextWindowEnd(now, 10)
	bucketStart := windowEnd - 10

	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10,
		BucketStart:       bucketStart,
		BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":10,"series":{},"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false}`, bucketStart),
		Complete:          true,
	}}, 1, float64(windowEnd-20), true); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	// Simulate a legacy/direct owner: the fact and source watermark advance,
	// but no cell-level invalidation is recorded before this process exits.
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO observations(event_id,family,source_id,observed_at,epoch_id,owner_generation,payload_json) VALUES (?,?,?,?,?,?,?)`,
		"restart-cpu", "cpu", "host", float64(bucketStart+1), "restart-epoch", 2, `{"process_percent":77}`); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `INSERT INTO coverage_epochs(family,source_id,epoch_id,started_at,ended_at,native_cadence_seconds,owner_generation) VALUES (?,?,?,?,?,?,?)`,
		"cpu", "host", "restart-epoch", float64(bucketStart), nil, 1, 2); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=2 WHERE singleton=1`); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	restarted, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(restarted, LifecycleConfig{
		WindowSeconds: 900,
		MaxCells:      1,
		Now:           func() time.Time { return now },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(900, 10)
	if err := lifecycle.RunOnce(ctx); err != nil {
		_ = restarted.Close()
		t.Fatal(err)
	}
	window, err := restarted.ReadRingWindow(ctx, 900, 10, windowEnd)
	if err != nil {
		_ = restarted.Close()
		t.Fatal(err)
	}
	if len(window.PendingInvalidations) != 0 {
		_ = restarted.Close()
		t.Fatalf("startup reconciliation left pending cells: %+v", window)
	}
	var repaired RingBucketRow
	for _, row := range window.Rows {
		if row.BucketStart == bucketStart {
			repaired = row
			break
		}
	}
	if repaired.BucketJSON == "" {
		_ = restarted.Close()
		t.Fatalf("reconciled bucket %d was not published: %+v", bucketStart, window)
	}
	bucket, _, err := decodeRingBucketPayload(repaired.BucketJSON)
	if err != nil {
		_ = restarted.Close()
		t.Fatal(err)
	}
	if got := seriesValue(bucket, "cpu_percent:host"); got != 77 {
		_ = restarted.Close()
		t.Fatalf("reconciled CPU value = %v, want 77", got)
	}
	repairedPayload := repaired.BucketJSON
	if err := restarted.Close(); err != nil {
		t.Fatal(err)
	}

	second, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	secondLifecycle := NewLifecycle(second, LifecycleConfig{WindowSeconds: 900, MaxCells: 1, Now: func() time.Time { return now }})
	t.Cleanup(secondLifecycle.Stop)
	secondLifecycle.DemandProduct(900, 10)
	if err := secondLifecycle.RunOnce(ctx); err != nil {
		_ = second.Close()
		t.Fatal(err)
	}
	secondWindow, err := second.ReadRingWindow(ctx, 900, 10, windowEnd)
	if err != nil {
		_ = second.Close()
		t.Fatal(err)
	}
	for _, cell := range secondWindow.PendingInvalidations {
		if cell == bucketStart {
			_ = second.Close()
			t.Fatalf("idempotent restart reintroduced pending target cell: %+v", secondWindow)
		}
	}
	var secondRepaired RingBucketRow
	for _, row := range secondWindow.Rows {
		if row.BucketStart == bucketStart {
			secondRepaired = row
			break
		}
	}
	if secondRepaired.BucketJSON != repairedPayload {
		_ = second.Close()
		t.Fatalf("idempotent restart changed repaired target payload\nfirst=%s\nsecond=%s", repairedPayload, secondRepaired.BucketJSON)
	}
	if err := second.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestLifecycleNewCoarseDemandRefreshesCachedOpenCell(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(2_000_000_000, 0)
	lifecycle := NewLifecycle(writer, LifecycleConfig{Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	cell := lifecycleCell{ResolutionSeconds: 300, BucketStart: NextWindowEnd(now, 300) - 300}
	lifecycle.lastMaterializedAt[[2]int64{300, cell.BucketStart}] = now.Add(-30 * time.Second)
	if lifecycle.openCellDue(now, cell) {
		t.Fatal("background fold bypassed ordinary cadence")
	}
	lifecycle.DemandProduct(86400, 300)
	if !lifecycle.openCellDue(now, cell) {
		t.Fatal("new coarse demand remained behind the sixty-second background cadence")
	}
	lifecycle.lastMaterializedAt[[2]int64{300, cell.BucketStart}] = now
	now = now.Add(time.Second)
	lifecycle.DemandProduct(86400, 300)
	if lifecycle.openCellDue(now, cell) {
		t.Fatal("polling the same product repeatedly forced a coarse fold")
	}
}
