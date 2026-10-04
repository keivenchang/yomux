//go:build !race

package stats

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"testing"
	"time"
)

func TestLifecycleDemanded300SecondProductConvergesWhenCellExceedsDefaultRowBudget(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	observations := make([]Observation, 0, defaultLifecycleMaxFactRows+1)
	base := float64(RetentionSeconds)
	for index := 0; index < cap(observations); index++ {
		observations = append(observations, Observation{
			EventID: fmt.Sprintf("dense-300s-%05d", index), Family: "cpu", SourceID: "host",
			ObservedAt: base + 9900 + float64(index)/100, EpochID: "dense-300s",
			Payload: map[string]any{"process_percent": float64(index % 100)},
		})
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{
		Observations: observations,
		UsageAtoms: []UsageAtom{{
			EventID: "dense-300s-session-token", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens",
			ObservedAt: base + 9950,
			Payload:    map[string]any{"quantity": 42.0, "agent_id": "fixture-session", "model": "fixture-model", "provider": "fixture-provider", "execution_source": "codex"},
		}},
	}); err != nil {
		t.Fatal(err)
	}

	now := time.Unix(int64(base)+10200, 0)
	seeded := make([]RingBucketWrite, 0, 23)
	for bucket := int64(base) + 3300; bucket < int64(base)+10500; bucket += 300 {
		if bucket == int64(base)+9900 {
			continue
		}
		seeded = append(seeded, RingBucketWrite{
			ResolutionSeconds: 300,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":300,"series":[],"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false,"complete":true}`, bucket),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seeded, 1, float64(now.Unix()), true); err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: RetentionSeconds, MaxCells: 8, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(7200, 300)
	service := NewStatsService(writer, StatsServiceConfig{
		Products: []ProductKey{{RangeSeconds: 7200, ResolutionSeconds: 300}},
		Now:      func() time.Time { return now },
	})
	for attempt := 0; attempt < 8; attempt++ {
		beforeBytes := lifecycle.Counters().ByResolution[300].DecodedBytes
		beforeDuration := lifecycle.Counters().ByResolution[300].MaterializationDuration
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if duration := lifecycle.Counters().ByResolution[300].MaterializationDuration - beforeDuration; duration > defaultLifecycleMaxRunTime {
			t.Fatalf("fixture 300s materialization took %s, over fixture performance budget %s", duration, defaultLifecycleMaxRunTime)
		}
		afterBytes := lifecycle.Counters().ByResolution[300].DecodedBytes
		if afterBytes-beforeBytes > 8*1024*1024 {
			t.Fatalf("one lifecycle pass decoded %d bytes at 300s, over the 8 MiB bound", afterBytes-beforeBytes)
		}
		err := service.Refresh(ctx)
		if err == nil {
			snapshot, snapshotErr := service.CurrentSnapshot(ctx, 7200, 300)
			if errors.Is(snapshotErr, ErrProductPending) {
				continue
			}
			if snapshotErr != nil {
				t.Fatal(snapshotErr)
			}
			if len(snapshot.Materialized.Buckets) != 24 {
				t.Fatalf("demanded 300s buckets = %d, want 24", len(snapshot.Materialized.Buckets))
			}
			var tokenValue float64
			for _, bucket := range snapshot.Materialized.Buckets {
				if bucket.Start != base+9900 {
					continue
				}
				for _, series := range bucket.Series {
					if series.Name == "agent_tokens_per_minute:fixture-session" {
						tokenValue = series.Value
					}
				}
			}
			if tokenValue <= 0 {
				t.Fatalf("session token series missing from repaired 300s bucket; buckets=%+v", snapshot.Materialized.Buckets)
			}
			return
		}
		if !errors.Is(err, ErrProductPending) {
			t.Fatalf("refresh attempt %d: %v", attempt, err)
		}
	}
	t.Fatalf("demanded 300s product remained pending after repeated bounded lifecycle passes; counters=%+v", lifecycle.Counters())
}

func TestLifecycleBatchesAdjacentDemandedCoarseCellsPastRunBudget(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	now := time.Unix(int64(RetentionSeconds)+10200, 0)
	windowEnd := NextWindowEnd(now, 300)
	windowStart := windowEnd - 3600
	targetStart := windowEnd - 1200
	seeded := make([]RingBucketWrite, 0, 8)
	for bucket := windowStart; bucket < targetStart; bucket += 300 {
		seeded = append(seeded, RingBucketWrite{
			ResolutionSeconds: 300,
			BucketStart:       bucket,
			BucketJSON:        fmt.Sprintf(`{"start":%d,"duration":300,"series":[],"source":{"count":0,"first_timestamp":null,"last_timestamp":null},"open":false,"complete":true}`, bucket),
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seeded, 1, float64(now.Unix()), true); err != nil {
		t.Fatal(err)
	}

	usageAtoms := make([]UsageAtom, 0, 4)
	for bucketIndex := 0; bucketIndex < 4; bucketIndex++ {
		bucketStart := targetStart + int64(bucketIndex*300)
		usageAtoms = append(usageAtoms, UsageAtom{
			EventID: fmt.Sprintf("dense-batch-token-%02d", bucketIndex), Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens",
			ObservedAt: float64(bucketStart + 150),
			Payload:    map[string]any{"quantity": 42.0, "agent_id": "fixture-session", "model": "fixture-model", "provider": "fixture-provider", "execution_source": "codex"},
		})
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: usageAtoms}); err != nil {
		t.Fatal(err)
	}

	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: RetentionSeconds,
		MaxCells:      8,
		MaxRunTime:    time.Nanosecond,
		Now:           func() time.Time { return now },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(3600, 300)
	selected, err := lifecycle.selectCells(ctx, now, writer.sourceGeneration.Load())
	if err != nil {
		t.Fatal(err)
	}
	selectedCells := make([]string, 0, len(selected))
	for _, cell := range selected {
		selectedCells = append(selectedCells, fmt.Sprintf("%d:%d", cell.ResolutionSeconds, cell.BucketStart))
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}

	window, err := writer.ReadRingWindowMetadata(ctx, 3600, 300, windowEnd)
	if err != nil {
		t.Fatal(err)
	}
	if got := lifecycle.Counters().ByResolution[300].MaterializedBuckets; got != 4 {
		t.Fatalf("one bounded pass should materialize four adjacent demanded token buckets: got=%d missing=%d pending=%d rows=%d counters=%+v", got, len(window.MissingBucketStarts), len(window.PendingInvalidations), len(window.Rows), lifecycle.Counters())
	}
	if len(window.MissingBucketStarts) != 0 || len(window.PendingInvalidations) != 0 {
		rowStarts := make([]int64, 0, len(window.Rows))
		for _, row := range window.Rows {
			rowStarts = append(rowStarts, row.BucketStart)
		}
		t.Fatalf("four adjacent demanded coarse buckets remain unavailable: window=[%d,%d) target_start=%d selected=%v materialized=%d missing=%v pending=%v row_starts=%v", window.WindowStart, window.WindowEnd, targetStart, selectedCells, lifecycle.Counters().ByResolution[300].MaterializedBuckets, window.MissingBucketStarts, window.PendingInvalidations, rowStarts)
	}
}
