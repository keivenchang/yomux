// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestStatsServiceReadsOnlyPublishedRingProducts(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	key := ProductKey{RangeSeconds: 10, ResolutionSeconds: 10}
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return time.Unix(99, 0) }})
	request := SnapshotRequest{Start: 90, End: 100, Resolution: 10}
	if _, err := service.Snapshot(ctx, request); !errors.Is(err, ErrProductPending) {
		t.Fatalf("cold snapshot error = %v, want ErrProductPending", err)
	}

	bucket, err := json.Marshal(MaterializedBucket{Start: 90, Duration: 10, Complete: true, Series: []MaterializedSeries{{Name: "process_cpu_percent", Value: 7, SourceCount: 1, FirstObservedAt: 95, LastObservedAt: 95}}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 90, BucketJSON: string(bucket), Complete: true}}, 1, 100, true); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	snapshot, err := service.Snapshot(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.SourceGeneration != 1 || len(snapshot.Materialized.Buckets) != 1 || snapshot.Materialized.Buckets[0].Series[0].Value != 7 {
		t.Fatalf("published snapshot = %+v", snapshot)
	}
	refreshCounters := service.RefreshCounters()
	if refreshCounters.Runs != 1 || refreshCounters.RingReads != 1 || refreshCounters.RingRows != 1 || refreshCounters.BucketDecodes != 1 || refreshCounters.ProductsBuilt != 1 || refreshCounters.DecodedBytes == 0 || refreshCounters.LastDuration <= 0 {
		t.Fatalf("refresh counters = %+v", refreshCounters)
	}

	// A durable fact mutation does not alter the served product until the
	// lifecycle republishes the affected ring cell.
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "late", Family: "cpu", SourceID: "host", ObservedAt: 95, EpochID: "e", Payload: map[string]any{"process_percent": 99}}}}); err != nil {
		t.Fatal(err)
	}
	unchanged, err := service.Snapshot(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	if unchanged.Materialized.Buckets[0].Series[0].Value != 7 {
		t.Fatalf("request path folded unpublished fact: %+v", unchanged.Materialized.Buckets[0])
	}
}

func TestStatsServiceSkipsCoverageProjectionForUnchangedGeneration(t *testing.T) {
	coverage := &ringNoDataSet{spans: []MaterializedNoData{{Family: "gpu", SourceID: "host", EpochID: "e", Reason: "offline", Start: 0, End: 5}}}
	key := ProductKey{RangeSeconds: 10, ResolutionSeconds: 10}
	service := NewStatsService(nil, StatsServiceConfig{Products: []ProductKey{key}})
	service.products[key] = statsProduct{
		key: key, windowEnd: 20, cacheGeneration: 7,
		bucketVersions: []ringBucketVersion{{start: 10, generation: 3, noData: coverage}},
	}

	snapshot, changed, err := service.CurrentSnapshotIfChanged(context.Background(), 10, 10, 7)
	if err != nil || changed || len(snapshot.Materialized.NoData) != 0 {
		t.Fatalf("unchanged generation projected coverage: changed=%t snapshot=%+v err=%v", changed, snapshot, err)
	}

	snapshot, changed, err = service.CurrentSnapshotIfChanged(context.Background(), 10, 10, 6)
	if err != nil || !changed || len(snapshot.Materialized.NoData) != 1 || snapshot.Materialized.NoData[0].Start != 10 || snapshot.Materialized.NoData[0].End != 15 {
		t.Fatalf("changed generation snapshot = %+v changed=%t err=%v", snapshot, changed, err)
	}
}

func TestHistoricalCoveragePreservesCurrentIntervalOrdering(t *testing.T) {
	closedTail := &ringNoDataSet{spans: []MaterializedNoData{{Family: "cpu", SourceID: "host", EpochID: "e", Reason: "persisted ring gap", Start: 0, End: 10}}}
	stored := &ringNoDataSet{spans: []MaterializedNoData{{Family: "cpu", SourceID: "host", EpochID: "e", Reason: "coverage gap", Start: 0, End: 10}}}
	current := newMaterializedNoDataAccumulatorWithWireSizing(false)
	current.appendShifted(closedTail.spans, 100)
	current.appendShifted(stored.spans, 100)

	product := statsProduct{
		snapshot:       StatsSnapshot{Materialized: MaterializedLayer{NoData: current.Sorted()}},
		bucketVersions: []ringBucketVersion{{start: 100, closedTail: closedTail, noData: stored}},
	}
	expected := append([]MaterializedNoData(nil), product.snapshot.Materialized.NoData...)
	product.snapshot.Materialized.NoData = nil
	historical := snapshotWithCoverage(product)
	if !reflect.DeepEqual(historical.Materialized.NoData, expected) {
		t.Fatalf("historical coverage order = %+v, current order = %+v", historical.Materialized.NoData, expected)
	}
}

func TestStatsServiceSharesOneWidestRingReadPerResolution(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1000, 0)
	writes := make([]RingBucketWrite, 0, 30)
	for start := int64(700); start < 1010; start += 10 {
		payload, encodeErr := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: 10, Complete: true}, nil, 1, float64(now.Unix()))
		if encodeErr != nil {
			t.Fatal(encodeErr)
		}
		writes = append(writes, RingBucketWrite{ResolutionSeconds: 10, BucketStart: start, BucketJSON: payload, Complete: true})
	}
	if _, err := writer.PublishRingBuckets(ctx, writes, 1, float64(now.Unix()), true); err != nil {
		t.Fatal(err)
	}
	service := NewStatsService(writer, StatsServiceConfig{
		Products: []ProductKey{{RangeSeconds: 120, ResolutionSeconds: 10}, {RangeSeconds: 300, ResolutionSeconds: 10}},
		Now:      func() time.Time { return now },
	})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	for _, request := range []SnapshotRequest{{Start: 890, End: 1010, Resolution: 10}, {Start: 710, End: 1010, Resolution: 10}} {
		snapshot, snapshotErr := service.Snapshot(ctx, request)
		if snapshotErr != nil {
			t.Fatalf("snapshot %+v: %v", request, snapshotErr)
		}
		want := int((request.End - request.Start) / request.Resolution)
		if len(snapshot.Materialized.Buckets) != want || snapshot.Materialized.Start != request.Start || snapshot.Materialized.End != request.End {
			t.Fatalf("snapshot %+v = buckets=%d layer=%+v", request, len(snapshot.Materialized.Buckets), snapshot.Materialized)
		}
	}
}

func TestStatsServiceFetchesOnlyChangedRingPayloads(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1000, 0)
	writes := make([]RingBucketWrite, 0, 30)
	for start := int64(710); start < 1010; start += 10 {
		payload, encodeErr := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: 10, Complete: true}, nil, 1, float64(now.Unix()))
		if encodeErr != nil {
			t.Fatal(encodeErr)
		}
		writes = append(writes, RingBucketWrite{ResolutionSeconds: 10, BucketStart: start, BucketJSON: payload, Complete: true})
	}
	if _, err := writer.PublishRingBuckets(ctx, writes, 1, float64(now.Unix()), true); err != nil {
		t.Fatal(err)
	}
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, Now: func() time.Time { return now }})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	first := service.RefreshCounters()
	if first.BucketDecodes != 30 {
		t.Fatalf("initial bucket decodes = %d, want 30", first.BucketDecodes)
	}
	changed, err := encodeRingBucketPayload(MaterializedBucket{Start: 1000, Duration: 10, Complete: true, Series: []MaterializedSeries{{Name: "process_percent", Value: 9}}}, nil, 2, float64(now.Unix()+10))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 1000, BucketJSON: changed, Complete: true}}, 2, float64(now.Unix()+10), true); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	second := service.RefreshCounters()
	if second.BucketDecodes != 31 {
		t.Fatalf("changed publication decoded %d buckets, want one additional decode after 30", second.BucketDecodes)
	}
}

func TestStatsServiceRetainsSharedGenerationsAndSkipsUnaffectedProducts(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{{20, 10}, {10, 10}}, Now: func() time.Time { return time.Unix(99, 0) }})
	publish := func(start, generation int64) {
		t.Helper()
		payload, err := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: 10, Complete: true, Series: []MaterializedSeries{{Name: "cpu", Value: float64(generation)}}}, nil, generation, float64(100+generation))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: start, BucketJSON: payload, Complete: true}}, generation, float64(100+generation), true); err != nil {
			t.Fatal(err)
		}
	}
	publish(80, 1)
	publish(90, 1)
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	first, err := service.CurrentSnapshot(ctx, 20, 10)
	if err != nil {
		t.Fatal(err)
	}
	short, err := service.CurrentSnapshot(ctx, 10, 10)
	if err != nil {
		t.Fatal(err)
	}
	for generation := int64(2); generation <= 10; generation++ {
		publish(80, generation)
		before := service.RefreshCounters()
		if err := service.Refresh(ctx); err != nil {
			t.Fatal(err)
		}
		if service.RefreshCounters().ProductsBuilt-before.ProductsBuilt != 1 {
			t.Fatal("unaffected short product rebuilt")
		}
	}
	if first.Materialized.Buckets[0].Series[0].Value != 1 {
		t.Fatal("publication mutated prior generation")
	}
	latest, err := service.CurrentSnapshot(ctx, 20, 10)
	if err != nil {
		t.Fatal(err)
	}
	if &first.Materialized.Buckets[1].Series[0] != &latest.Materialized.Buckets[1].Series[0] {
		t.Fatal("unchanged bucket series was copied")
	}
	if _, ok := service.GenerationSnapshot(ctx, 20, 10, 1); ok {
		t.Fatal("expired generation retained")
	}
	for generation := int64(3); generation <= 10; generation++ {
		snapshot, ok := service.GenerationSnapshot(ctx, 20, 10, generation)
		if !ok || snapshot.Materialized.Buckets[0].Series[0].Value != float64(generation) {
			t.Fatalf("generation %d missing or changed", generation)
		}
	}
	unchanged, err := service.CurrentSnapshot(ctx, 10, 10)
	if err != nil || unchanged.CacheGeneration != short.CacheGeneration {
		t.Fatalf("unaffected product generation changed: %+v %v", unchanged, err)
	}
	var readers sync.WaitGroup
	for i := 0; i < 8; i++ {
		readers.Add(1)
		go func() {
			defer readers.Done()
			for j := 0; j < 100; j++ {
				if _, ok := service.GenerationSnapshot(ctx, 20, 10, 10); !ok {
					t.Error("concurrent generation disappeared")
				}
			}
		}()
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	readers.Wait()
}

func TestStatsServiceSkipsRingDecodeWhenPublicationAndBacklogAreUnchanged(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	payload, err := encodeRingBucketPayload(MaterializedBucket{
		Start: 90, Duration: 10, Complete: true,
		Series: []MaterializedSeries{{Name: "cpu_percent:host", Value: 7, SourceCount: 1, FirstObservedAt: 95, LastObservedAt: 95}},
	}, nil, 1, 99)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 90, BucketJSON: payload, Complete: true}}, 1, 100, true); err != nil {
		t.Fatal(err)
	}
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}}, Now: func() time.Time { return time.Unix(90, 0) }})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	// Corrupt the stored payload without changing publication metadata. A
	// second refresh must use the immutable in-memory product instead of paying
	// the full decode cost again; a new publication would invalidate this cache.
	if _, err := writer.db.ExecContext(ctx, "UPDATE aggregate_ring_slots SET bucket_json=? WHERE resolution_seconds=? AND bucket_start=?", "not-json", 10, 90); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatalf("unchanged publication forced a ring decode: %v", err)
	}
	snapshot, err := service.CurrentSnapshot(ctx, 10, 10)
	if err != nil {
		t.Fatal(err)
	}
	if got := snapshot.Materialized.Buckets[0].Series[0].Value; got != 7 {
		t.Fatalf("cached product value = %v, want 7", got)
	}
}

func TestStatsServiceDurablyInvalidatesCorruptRingPayloadForLifecycleRepair(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "synthetic-cpu", Family: "cpu", SourceID: "host", ObservedAt: 95,
		EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 2.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	wrongPayload, err := encodeRingBucketPayload(MaterializedBucket{
		Start: 90, Duration: 10, Complete: true,
		Series: []MaterializedSeries{{Name: "process_cpu_percent:host", Value: 7}},
	}, nil, 1, 99)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 90, BucketJSON: wrongPayload, Complete: true}}, 1, 99, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, "UPDATE aggregate_ring_slots SET bucket_json=? WHERE resolution_seconds=? AND bucket_start=?", "not-json", 10, 90); err != nil {
		t.Fatal(err)
	}
	corruptWindow, err := writer.ReadRingWindowMetadata(ctx, 10, 10, 100)
	if err != nil || len(corruptWindow.Rows) != 1 {
		t.Fatalf("read corrupt ring metadata: rows=%+v err=%v", corruptWindow.Rows, err)
	}
	corruptPayloads, err := writer.readRingBucketPayloads(ctx, 10, []int64{90})
	if err != nil {
		t.Fatal(err)
	}
	staleCorruptRow := corruptWindow.Rows[0]
	staleCorruptPayload := corruptPayloads[90].Value
	if _, err := writer.db.ExecContext(ctx, `CREATE TRIGGER reject_corrupt_ring_invalidation
		BEFORE INSERT ON ring_invalidations WHEN NEW.reason='corrupt_ring_payload'
		BEGIN SELECT RAISE(ABORT, 'synthetic invalidation failure'); END`); err != nil {
		t.Fatal(err)
	}

	service := NewStatsService(writer, StatsServiceConfig{
		Products: []ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}},
		Now:      func() time.Time { return time.Unix(99, 0) },
	})
	if err := service.Refresh(ctx); err == nil {
		t.Fatal("cold service accepted a malformed persisted ring payload")
	}
	if pending, err := writer.PendingInvalidationCells(ctx); err != nil || len(pending) != 0 {
		t.Fatalf("failed synthetic invalidation unexpectedly persisted repair work: pending=%+v err=%v", pending, err)
	}
	if _, err := writer.db.ExecContext(ctx, "DROP TRIGGER reject_corrupt_ring_invalidation"); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err == nil {
		t.Fatal("service did not retry the failed durable corruption mark")
	}
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 || pending[0].ResolutionSeconds != 10 || pending[0].BucketStart != 90 {
		t.Fatalf("corrupt ring payload did not create one exact durable repair request: %+v", pending)
	}

	lifecycle := NewLifecycle(writer, LifecycleConfig{
		WindowSeconds: 10, MaxCells: 1, Now: func() time.Time { return time.Unix(99, 0) },
	})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(10, 10)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatalf("lifecycle repair: %v", err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatalf("service refresh after durable repair: %v", err)
	}
	snapshot, err := service.Snapshot(ctx, SnapshotRequest{Start: 90, End: 100, Resolution: 10})
	if err != nil {
		t.Fatal(err)
	}
	if got := seriesValue(snapshot.Materialized.Buckets[0], "cpu_percent:host"); got != 2 {
		t.Fatalf("repaired synthetic bucket value = %v, want source-fact value 2: %+v", got, snapshot.Materialized.Buckets[0])
	}
	if marked, err := writer.InvalidateCorruptRingPayload(ctx, staleCorruptRow, staleCorruptPayload, 100); err != nil || marked {
		t.Fatalf("stale corruption report invalidated the repaired slot: marked=%t err=%v", marked, err)
	}
	pending, err = writer.PendingInvalidationCells(ctx)
	if err != nil || len(pending) != 0 {
		t.Fatalf("stale corruption report recreated invalidation after repair: pending=%+v err=%v", pending, err)
	}
}

func TestStatsServiceCachesAgentStatusOutsideRequestPath(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	calls := 0
	service := NewStatsService(writer, StatsServiceConfig{AgentStatus: func(context.Context) (AgentStatusSnapshot, error) {
		calls++
		return AgentStatusSnapshot{Available: true, SourceGeneration: 7}, nil
	}})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := service.AgentStatus(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := service.AgentStatus(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("agent status callback calls = %d, want one owner refresh", calls)
	}
}

func TestPricingRebuildRequiresEveryConfiguredProductToBePublished(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	validPayload, err := encodeRingBucketPayload(MaterializedBucket{Start: 90, Duration: 10, Complete: true}, nil, 1, 99)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10, BucketStart: 90, BucketJSON: validPayload, Complete: true,
	}}, 1, 99, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, "UPDATE aggregate_ring_slots SET bucket_json=? WHERE resolution_seconds=? AND bucket_start=?", "not-json", 10, 90); err != nil {
		t.Fatal(err)
	}
	service := NewStatsService(writer, StatsServiceConfig{
		Products: []ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}},
		Now:      func() time.Time { return time.Unix(99, 0) },
	})
	if err := service.Refresh(ctx); err == nil {
		t.Fatal("malformed synthetic product unexpectedly refreshed")
	}
	if service.PricingRebuildComplete(1) {
		t.Fatal("pricing rebuild reported complete while its configured product is pending and unpublished")
	}
}

func TestStatsServiceBoundsAgentStatusRefreshCadence(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1000, 0)
	calls := 0
	service := NewStatsService(writer, StatsServiceConfig{
		Now:                func() time.Time { return now },
		AgentStatusCadence: 10 * time.Second, AgentStatusRetryCadence: time.Second,
		AgentStatus: func(context.Context) (AgentStatusSnapshot, error) {
			calls++
			return AgentStatusSnapshot{Available: true, SourceGeneration: int64(calls)}, nil
		},
	})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("initial status reads = %d, want 1", calls)
	}
	now = now.Add(9 * time.Second)
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("status was reread before cadence: calls=%d", calls)
	}
	now = now.Add(time.Second)
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("status was not reread at cadence: calls=%d", calls)
	}
	if err := service.RefreshAgentStatus(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != 3 {
		t.Fatalf("explicit status refresh did not force a read: calls=%d", calls)
	}
}

func TestStatsServiceRetriesAgentStatusErrorsAtBoundedCadence(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(2000, 0)
	calls := 0
	service := NewStatsService(writer, StatsServiceConfig{
		Now:                func() time.Time { return now },
		AgentStatusCadence: 10 * time.Second, AgentStatusRetryCadence: time.Second,
		AgentStatus: func(context.Context) (AgentStatusSnapshot, error) {
			calls++
			if calls == 1 {
				return AgentStatusSnapshot{}, errors.New("synthetic sqlite unavailable")
			}
			return AgentStatusSnapshot{Available: true}, nil
		},
	})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("initial failed status reads = %d, want 1", calls)
	}
	now = now.Add(500 * time.Millisecond)
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("retry happened before bounded retry cadence: calls=%d", calls)
	}
	now = now.Add(500 * time.Millisecond)
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("retry did not happen at bounded cadence: calls=%d", calls)
	}
}

func TestStatsServiceServesLastCompleteProductWhileInvalidationIsPending(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	key := ProductKey{RangeSeconds: 10, ResolutionSeconds: 10}
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return time.Unix(99, 0) }})
	bucket, err := json.Marshal(MaterializedBucket{Start: 90, Duration: 10, Complete: true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 90, BucketJSON: string(bucket), Complete: true}}, 1, 100, true); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "late", Family: "cpu", SourceID: "host", ObservedAt: 95, EpochID: "e", Payload: map[string]any{"process_percent": 99}}}}); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	snapshot, err := service.Snapshot(ctx, SnapshotRequest{Start: 90, End: 100, Resolution: 10})
	if err != nil {
		t.Fatalf("last complete snapshot error = %v", err)
	}
	if snapshot.SourceGeneration != 1 || len(snapshot.Materialized.Buckets) != 1 || len(snapshot.Materialized.Buckets[0].Series) != 0 {
		t.Fatalf("last complete snapshot = %+v", snapshot)
	}
	state := service.State()
	if state.Ready || state.PendingProducts != 1 {
		t.Fatalf("pending service state = %+v", state)
	}
}

func TestStatsServiceProjectsInteriorOpenRingBucketAsPersistedGap(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	key := ProductKey{RangeSeconds: 20, ResolutionSeconds: 10}
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return time.Unix(99, 0) }})
	interior, err := encodeRingBucketPayload(MaterializedBucket{
		Start: 80, Duration: 10, Complete: false, SourceCount: 1,
		Series: []MaterializedSeries{{Name: "process_cpu_percent", Value: 4, SourceCount: 1, FirstObservedAt: 85, LastObservedAt: 85}},
		Cost:   MaterializedCostDetail{Tokens: 12, UnpricedTokens: 12, Dimensions: []MaterializedCostDimension{{Dimension: "input", Tokens: 12}}},
	}, nil, 1, 100)
	if err != nil {
		t.Fatal(err)
	}
	rightEdge, err := encodeRingBucketPayload(MaterializedBucket{Start: 90, Duration: 10, Complete: true}, nil, 1, 100)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{
		{ResolutionSeconds: 10, BucketStart: 80, BucketJSON: interior, Complete: false},
		{ResolutionSeconds: 10, BucketStart: 90, BucketJSON: rightEdge, Complete: true},
	}, 1, 100, true); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	snapshot, err := service.Snapshot(ctx, SnapshotRequest{Start: 80, End: 100, Resolution: 10})
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Materialized.Buckets) != 2 {
		t.Fatalf("bucket count = %d, want 2", len(snapshot.Materialized.Buckets))
	}
	interiorBucket := snapshot.Materialized.Buckets[0]
	if !interiorBucket.Complete || len(interiorBucket.Series) != 1 || interiorBucket.Series[0].Name != "process_cpu_percent" || interiorBucket.Series[0].Value != 4 || interiorBucket.SourceCount != 1 || interiorBucket.Cost.Tokens != 12 {
		t.Fatalf("interior bucket did not retain known facts while closing its unknown tail: %+v", interiorBucket)
	}
	if snapshot.Materialized.Buckets[1].Complete != true {
		t.Fatalf("right-edge bucket changed unexpectedly: %+v", snapshot.Materialized.Buckets[1])
	}
	if got, want := len(snapshot.Materialized.NoData), 8; got != want {
		t.Fatalf("persisted gap no-data spans = %d, want %d", got, want)
	}
	for _, span := range snapshot.Materialized.NoData {
		if span.Start != 80 || span.End != 90 || span.SourceID != "persisted-ring" || span.Reason != "incomplete_persisted_bucket" || span.NativeCadenceSeconds != 10 {
			t.Fatalf("persisted gap span = %+v", span)
		}
	}
}

func TestStatsServiceMergesAdjacentNoDataAcrossRingBuckets(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	key := ProductKey{RangeSeconds: 20, ResolutionSeconds: 10}
	now := time.Unix(99, 0)
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return now }})
	first, err := encodeRingBucketPayload(
		MaterializedBucket{Start: 80, Duration: 10, Complete: true},
		[]MaterializedNoData{{Family: "gpu", SourceID: "gpu:0", EpochID: "epoch", Start: 82, End: 90, NativeCadenceSeconds: 10, Reason: "driver unavailable"}},
		1,
		100,
	)
	if err != nil {
		t.Fatal(err)
	}
	second, err := encodeRingBucketPayload(
		MaterializedBucket{Start: 90, Duration: 10, Complete: true},
		[]MaterializedNoData{{Family: "gpu", SourceID: "gpu:0", EpochID: "epoch", Start: 90, End: 94, NativeCadenceSeconds: 10, Reason: "driver unavailable"}},
		1,
		100,
	)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{
		{ResolutionSeconds: 10, BucketStart: 80, BucketJSON: first, Complete: true},
		{ResolutionSeconds: 10, BucketStart: 90, BucketJSON: second, Complete: true},
	}, 1, 100, true); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	snapshot, err := service.Snapshot(ctx, SnapshotRequest{Start: 80, End: 100, Resolution: 10})
	if err != nil {
		t.Fatal(err)
	}
	if got := snapshot.Materialized.NoData; len(got) != 1 || got[0].Family != "gpu" || got[0].SourceID != "gpu:0" || got[0].EpochID != "epoch" || got[0].Start != 82 || got[0].End != 94 || got[0].Reason != "driver unavailable" {
		t.Fatalf("merged ring no-data = %+v", got)
	}
}

func TestStatsServiceBuilds24HourProductWithLargeUnavailableHistory(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	const now = int64(2_000_000_100)
	const bucketCount = 288
	const sourceCount = 2000
	costDetail := MaterializedCostDetail{Evidence: make([]PriceEvidence, 4)}
	for index := range costDetail.Evidence {
		costDetail.Evidence[index] = PriceEvidence{
			Key: fmt.Sprintf("fixture-price-evidence-%d", index), Provider: "provider", Model: "model", Dimension: "input", Direction: "input",
			Modality: "text", CacheRole: "none", Unit: "tokens", PricingProfile: "default", ServiceTier: "standard",
			CatalogModel: "model", RateUSD: "0", EffectiveFrom: "1970-01-01T00:00:00Z", SourceKind: "fixture",
			SourceURL: strings.Repeat("x", 4096),
		}
	}
	costJSON, err := json.Marshal(costDetail)
	if err != nil {
		t.Fatal(err)
	}
	bucketSizes := make([]int, bucketCount)
	bucketStarts := make([]int64, bucketCount)
	costEvidence := make(map[string]struct{})
	costReportBytes := 0
	for index := range bucketSizes {
		start := now - int64(bucketCount-index)*300
		bucketStarts[index] = start
		bucket := MaterializedBucket{Start: float64(start), Duration: 300, Complete: true}
		bucketJSON, marshalErr := json.Marshal(bucket)
		if marshalErr != nil {
			t.Fatal(marshalErr)
		}
		bucketSizes[index] = len(bucketJSON)
		costBytes, costErr := statsCostReportBytes(costDetail, costEvidence)
		if costErr != nil {
			t.Fatal(costErr)
		}
		costReportBytes += costBytes
	}
	mergedNoData := make([]MaterializedNoData, sourceCount)
	for sourceIndex := range mergedNoData {
		mergedNoData[sourceIndex] = MaterializedNoData{
			Family: "agent_tokens", SourceID: fmt.Sprintf("source-%04d", sourceIndex), EpochID: "epoch",
			Start: float64(now - bucketCount*300), End: float64(now), NativeCadenceSeconds: 60,
			Reason: "transcript cursor state exceeds its size bound",
		}
	}
	if bucketCount*len(costJSON) <= MaxStatsSnapshotChunkBytes {
		t.Fatal("fixture does not distinguish repeated evidence from the shared report")
	}
	if !statsSnapshotChunksFit(bucketSizes, bucketStarts, 300, mergedNoData, costReportBytes) {
		t.Fatalf("24-hour product with repeated cost evidence did not fit its shared report bound: report=%d bytes", costReportBytes)
	}
	rows := make([]RingBucketWrite, 0, bucketCount)
	for bucketIndex := 0; bucketIndex < bucketCount; bucketIndex++ {
		start := now - int64(bucketCount-bucketIndex)*300
		noData := make([]MaterializedNoData, 0, sourceCount)
		for sourceIndex := 0; sourceIndex < sourceCount; sourceIndex++ {
			noData = append(noData, MaterializedNoData{
				Family: "agent_tokens", SourceID: fmt.Sprintf("source-%04d", sourceIndex), EpochID: "epoch",
				Start: float64(start), End: float64(start + 300), NativeCadenceSeconds: 60,
				Reason: "transcript cursor state exceeds its size bound",
			})
		}
		payload, err := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: 300, Complete: true, Cost: costDetail}, noData, 1, float64(now))
		if err != nil {
			t.Fatalf("encode bucket %d: %v", bucketIndex, err)
		}
		rows = append(rows, RingBucketWrite{ResolutionSeconds: 300, BucketStart: start, BucketJSON: payload, Complete: true})
	}
	if _, err := writer.PublishRingBuckets(ctx, rows, 1, float64(now), true); err != nil {
		t.Fatal(err)
	}
	service := NewStatsService(writer, StatsServiceConfig{
		Products: []ProductKey{{RangeSeconds: 86400, ResolutionSeconds: 300}, {RangeSeconds: 7200, ResolutionSeconds: 300}},
		Now:      func() time.Time { return time.Unix(now-1, 0) },
	})
	if err := service.Refresh(ctx); err != nil {
		t.Fatalf("refresh 24-hour product: %v", err)
	}
	snapshot, err := service.CurrentSnapshot(ctx, 86400, 300)
	if err != nil {
		t.Fatalf("read 24-hour product: %v (state=%+v)", err, service.State())
	}
	if got := len(snapshot.Materialized.Buckets); got != bucketCount {
		t.Fatalf("24-hour bucket count = %d, want %d", got, bucketCount)
	}
	if got := len(snapshot.Materialized.NoData); got != sourceCount {
		t.Fatalf("merged unavailable spans = %d, want %d", got, sourceCount)
	}
	sets := make(map[*ringNoDataSet]bool)
	cachedNoDataBytes := 0
	for _, entry := range service.ringBucketCache {
		if entry.noData == nil {
			t.Fatal("ring cache missing typed no-data")
		}
		if !sets[entry.noData] {
			sets[entry.noData] = true
			cachedNoDataBytes += entry.noData.bytes
		}
	}
	if cachedNoDataBytes >= MaxRingBucketBytes*4 {
		t.Fatalf("typed 24-hour no-data cache = %d bytes, want below 32 MiB", cachedNoDataBytes)
	}
	if len(sets) != 1 {
		t.Fatalf("repeated no-data retained %d interval sets, want 1", len(sets))
	}
	shortSnapshot, err := service.CurrentSnapshot(ctx, 7200, 300)
	if err != nil {
		t.Fatalf("read shared-cache 2-hour product: %v", err)
	}
	if got := len(shortSnapshot.Materialized.NoData); got != sourceCount {
		t.Fatalf("2-hour unavailable spans decoded from cache = %d, want %d", got, sourceCount)
	}
}

func TestStatsServiceRejectsIndivisibleBucketBeyondTransportLimit(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	const now = int64(2_000_000_100)
	start := now - 10
	payload, err := encodeRingBucketPayload(MaterializedBucket{
		Start: float64(start), Duration: 10, Complete: true,
		Series: []MaterializedSeries{{Name: strings.Repeat("x", MaxStatsSnapshotChunkBytes), Value: 1}},
	}, nil, 1, float64(now))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: start, BucketJSON: payload, Complete: true}}, 1, float64(now), true); err != nil {
		t.Fatal(err)
	}
	service := NewStatsService(writer, StatsServiceConfig{
		Products: []ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}},
		Now:      func() time.Time { return time.Unix(now-1, 0) },
	})
	if err := service.Refresh(ctx); err == nil || !strings.Contains(err.Error(), "transport chunk limit") {
		t.Fatalf("oversized bucket refresh error = %v, want transport chunk limit", err)
	}
	state := service.State()
	if state.PendingProducts != 1 || !strings.Contains(state.LastError, "transport chunk limit") {
		t.Fatalf("oversized bucket state = %+v, want an explicit pending transport-limit error", state)
	}
}

func TestStatsSnapshotChunkFitUsesBucketGroupsAndSharedCost(t *testing.T) {
	buckets := make([]int, 288)
	starts := make([]int64, len(buckets))
	for index := range buckets {
		buckets[index] = 810 * 1024
		starts[index] = int64(index * 300)
	}
	if !statsSnapshotChunksFit(buckets, starts, 300, nil, 0) {
		t.Fatal("rejected 810 KiB buckets that fit when the expanded chunk limit allows finer bucket groups")
	}
	for index := range buckets {
		buckets[index] = 700 * 1024
	}
	if !statsSnapshotChunksFit(buckets, starts, 300, nil, 0) {
		t.Fatal("rejected bucket groups that fit when no shared cost report is present")
	}
	if statsSnapshotChunksFit(buckets, starts, 300, nil, 3500*1024) {
		t.Fatal("accepted a shared cost report that pushes one-bucket chunks over the transport limit")
	}
}

func TestStatsSnapshotChunkFitLocalizesDistributedNoData(t *testing.T) {
	buckets := make([]int, 288)
	starts := make([]int64, len(buckets))
	noData := make([]MaterializedNoData, len(buckets))
	for index := range buckets {
		buckets[index] = 64 * 1024
		starts[index] = int64(index * 300)
		noData[index] = MaterializedNoData{
			Family: "daemon", SourceID: fmt.Sprintf("source-%03d-%s", index, strings.Repeat("x", 30_000)),
			EpochID: "epoch", Start: float64(starts[index]), End: float64(starts[index] + 300),
			NativeCadenceSeconds: 300, Reason: "unavailable",
		}
	}
	if !statsSnapshotChunksFit(buckets, starts, 300, noData, 0) {
		t.Fatal("rejected distributed no-data spans that fit after per-chunk clipping")
	}
}

func TestStatsSnapshotChunkFitUsesEncodedNoDataSize(t *testing.T) {
	singleBucketNoData := make([]MaterializedNoData, 14_000)
	for index := range singleBucketNoData {
		singleBucketNoData[index] = MaterializedNoData{
			Family: "agent_tokens", SourceID: fmt.Sprintf("retired-unavailable:source-%05d", index),
			EpochID: fmt.Sprintf("go-token-retired-%05d", index), Start: 0, End: 300,
			NativeCadenceSeconds: 1, Reason: "transcript cursor state exceeds its size bound",
		}
	}
	if !statsSnapshotChunksFit([]int{358}, []int64{0}, 300, singleBucketNoData, 0) {
		t.Fatal("rejected a single-bucket snapshot that fits in one transport payload")
	}

	buckets := make([]int, 288)
	starts := make([]int64, len(buckets))
	for index := range buckets {
		buckets[index] = 64 * 1024
		starts[index] = int64(index * 300)
	}
	noData := make([]MaterializedNoData, 14_000)
	for index := range noData {
		noData[index] = MaterializedNoData{
			Family: "agent_tokens", SourceID: fmt.Sprintf("retired-unavailable:source-%05d", index),
			EpochID: fmt.Sprintf("go-token-retired-%05d", index), Start: float64(starts[1]), End: float64(starts[1] + 300),
			NativeCadenceSeconds: 1, Reason: "transcript cursor state exceeds its size bound",
		}
	}
	if !statsSnapshotChunksFit(buckets, starts, 300, noData, 0) {
		t.Fatal("rejected 14,000 normally encoded no-data spans that fit in their one transport chunk")
	}
	noData = make([]MaterializedNoData, 30_000)
	for index := range noData {
		noData[index] = MaterializedNoData{
			Family: "agent_tokens", SourceID: fmt.Sprintf("retired-unavailable:source-%05d", index),
			EpochID: fmt.Sprintf("go-token-retired-%05d", index), Start: float64(starts[1]), End: float64(starts[1] + 300),
			NativeCadenceSeconds: 1, Reason: "transcript cursor state exceeds its size bound",
		}
	}
	if !statsSnapshotChunksFit(buckets, starts, 300, noData, 0) {
		t.Fatal("rejected no-data spans that can be split across transport fragments for one bucket")
	}
}

func TestStatsSnapshotChunkFitCountsIndivisibleNoDataFragments(t *testing.T) {
	const bucketCount = 511
	buckets := make([]int, bucketCount)
	starts := make([]int64, bucketCount)
	for index := range buckets {
		buckets[index] = 980 * 1024
		starts[index] = int64(index * 300)
	}
	noData := make([]MaterializedNoData, 3)
	for index := range noData {
		noData[index] = MaterializedNoData{
			Family: "agent_tokens", SourceID: strings.Repeat("s", 2*1024*1024),
			EpochID: fmt.Sprintf("epoch-%d", index), Start: 0, End: 300,
			NativeCadenceSeconds: 5, Reason: "cursor unavailable",
		}
	}
	if statsSnapshotChunksFit(buckets, starts, 300, noData, 0) {
		t.Fatal("admitted 511 bucket chunks plus three indivisible no-data fragments, which exceeds the 512 chunk limit")
	}

	for index := range buckets {
		buckets[index] = 117
	}
	for index := range noData {
		noData[index] = MaterializedNoData{
			Family: "agent_tokens", SourceID: strings.Repeat("s", 2*1024*1024+135),
			EpochID: fmt.Sprintf("epoch-%d", index), Start: 0, End: 300,
			NativeCadenceSeconds: 5, Reason: "cursor unavailable",
		}
	}
	if statsSnapshotChunksFit(buckets, starts, 300, noData, 0) {
		t.Fatal("admitted a 511-bucket snapshot whose three large no-data fragments raise the chunk count to 513")
	}
}

func TestStatsServicePrunesExpiredBucketCacheEntries(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(99, 0)
	service := NewStatsService(writer, StatsServiceConfig{
		Products: []ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}},
		Now:      func() time.Time { return now },
	})
	publish := func(start int64, generation int64) {
		t.Helper()
		payload, err := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: 10, Complete: true}, nil, generation, float64(start+10))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: start, BucketJSON: payload, Complete: true}}, generation, float64(start+10), true); err != nil {
			t.Fatal(err)
		}
	}
	publish(90, 1)
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if len(service.ringBucketCache) != 1 {
		t.Fatalf("initial cache entries = %d, want 1", len(service.ringBucketCache))
	}
	now = time.Unix(109, 0)
	publish(100, 2)
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if len(service.ringBucketCache) != 1 || service.ringBucketCacheBytes != service.ringBucketCache[ringBucketCacheKey{resolution: 10, bucket: 100}].cacheBytes {
		t.Fatalf("cache retained expired bucket: entries=%d bytes=%d cache=%+v", len(service.ringBucketCache), service.ringBucketCacheBytes, service.ringBucketCache)
	}
}

func TestLifecycleRingPayloadRetainsUnavailableCoverage(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	const now = int64(2_000_000_000)
	if _, err := writer.AppendBatch(ctx, AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "gpu-e", StartedAt: float64(now - 10), EndedAt: float64(now), NativeCadenceSeconds: 1, Reason: "no-driver"}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 10, Now: func() time.Time { return time.Unix(now-1, 0) }})
	t.Cleanup(lifecycle.Stop)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}}, Now: func() time.Time { return time.Unix(now-1, 0) }})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	snapshot, err := service.Snapshot(ctx, SnapshotRequest{Start: float64(now - 10), End: float64(now), Resolution: 10})
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Materialized.NoData) != 1 || snapshot.Materialized.NoData[0].Reason != "no-driver" {
		t.Fatalf("ring no-data = %+v", snapshot.Materialized.NoData)
	}
}

func TestStatsServiceConcurrentSnapshotsRemainBoundedDuringPublication(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(2_000_000_000, 0)
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "resource-soak-initial", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 20),
		EpochID: "resource-soak", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 10.0, "system_percent": 5.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	key := ProductKey{RangeSeconds: 300, ResolutionSeconds: 10}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 300, MaxCells: 64, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 16 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("resource-soak product did not become ready: %+v", service.State())
	}
	windowEnd := NextWindowEnd(now, key.ResolutionSeconds)

	const clientCount = 32
	const readsPerClient = 40
	start := make(chan struct{})
	latencies := make(chan time.Duration, clientCount*readsPerClient)
	errorsSeen := make(chan error, clientCount)
	var clients sync.WaitGroup
	for client := 0; client < clientCount; client++ {
		clients.Add(1)
		go func() {
			defer clients.Done()
			<-start
			for read := 0; read < readsPerClient; read++ {
				started := time.Now()
				_, snapshotErr := service.Snapshot(ctx, SnapshotRequest{Start: float64(windowEnd - int64(key.RangeSeconds)), End: float64(windowEnd), Resolution: 10})
				latencies <- time.Since(started)
				if snapshotErr != nil {
					select {
					case errorsSeen <- snapshotErr:
					default:
					}
					return
				}
			}
		}()
	}
	close(start)
	for update := 0; update < 8; update++ {
		if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
			EventID: fmt.Sprintf("resource-soak-update-%02d", update), Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 19 + int64(update)),
			EpochID: "resource-soak", OwnerGeneration: 1,
			Payload: map[string]any{"process_percent": float64(11 + update), "system_percent": 6.0},
		}}}); err != nil {
			t.Fatal(err)
		}
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, ErrProductPending) {
			t.Fatal(err)
		}
	}
	clients.Wait()
	close(errorsSeen)
	for snapshotErr := range errorsSeen {
		t.Fatalf("concurrent immutable snapshot failed: %v", snapshotErr)
	}
	close(latencies)
	var maximum time.Duration
	count := 0
	for latency := range latencies {
		count++
		if latency > maximum {
			maximum = latency
		}
	}
	if count != clientCount*readsPerClient {
		t.Fatalf("snapshot count = %d, want %d", count, clientCount*readsPerClient)
	}
	if maximum >= 2*time.Second {
		t.Fatalf("snapshot latency was not bounded during publication: max=%s", maximum)
	}
}

func TestStatsServiceWaitForPublicationUsesGenerationNotification(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	key := ProductKey{RangeSeconds: 10, ResolutionSeconds: 10}
	now := time.Unix(2_000_000_000-1, 0)
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return now }})
	bucket, err := encodeRingBucketPayload(MaterializedBucket{Start: 1_999_999_990, Duration: 10, Complete: true}, nil, 0, 1_999_999_999)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 1_999_999_990, BucketJSON: bucket, Complete: true}}, 0, 1_999_999_999, true); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	initialCache := service.State().CacheGeneration
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 10, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	changed := make(chan bool, 1)
	go func() {
		value, _ := service.WaitForPublication(ctx, initialCache, time.Second)
		changed <- value
	}()
	_, err = writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "next", Family: "cpu", SourceID: "host", ObservedAt: 1_999_999_995, EpochID: "e", Payload: map[string]any{"process_percent": 1}}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	select {
	case value := <-changed:
		if !value {
			t.Fatal("publication notification reported no generation change")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("publication notification did not arrive")
	}
}

func TestStatsServiceWaitForProductPublicationIgnoresUnrelatedGeneration(t *testing.T) {
	service := NewStatsService(nil, StatsServiceConfig{Products: []ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}}})
	key := ProductKey{RangeSeconds: 10, ResolutionSeconds: 10}
	service.mu.Lock()
	service.products[key] = statsProduct{key: key, cacheGeneration: 3}
	service.state.CacheGeneration = 9
	service.notify = make(chan struct{})
	service.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	changed, err := service.WaitForProductPublication(ctx, key, 3, 100*time.Millisecond)
	if changed || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("unrelated generation woke product wait: changed=%v err=%v", changed, err)
	}

	service.mu.Lock()
	oldNotify := service.notify
	service.products[key] = statsProduct{key: key, cacheGeneration: 4}
	service.notify = make(chan struct{})
	service.mu.Unlock()
	close(oldNotify)
	changed, err = service.WaitForProductPublication(context.Background(), key, 3, time.Second)
	if err != nil || !changed {
		t.Fatalf("product generation was not observed: changed=%v err=%v", changed, err)
	}
}

func TestStatsServiceWaitForProductPublicationHonorsCancellationBeforeNewGeneration(t *testing.T) {
	key := ProductKey{RangeSeconds: 10, ResolutionSeconds: 10}
	service := NewStatsService(nil, StatsServiceConfig{Products: []ProductKey{key}})
	service.mu.Lock()
	service.products[key] = statsProduct{key: key, cacheGeneration: 4}
	service.notify = make(chan struct{})
	service.mu.Unlock()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	changed, err := service.WaitForProductPublication(ctx, key, 3, time.Second)
	if changed || !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled product wait = changed:%v err:%v, want canceled without reporting a publication", changed, err)
	}
}

func TestStatsServiceWaitForProductPublicationCancellationInterruptsWait(t *testing.T) {
	key := ProductKey{RangeSeconds: 10, ResolutionSeconds: 10}
	service := NewStatsService(nil, StatsServiceConfig{Products: []ProductKey{key}})
	service.mu.Lock()
	service.products[key] = statsProduct{key: key, cacheGeneration: 3}
	service.notify = make(chan struct{})
	service.mu.Unlock()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started := make(chan struct{})
	done := make(chan struct {
		changed bool
		err     error
	}, 1)
	go func() {
		close(started)
		changed, err := service.WaitForProductPublication(ctx, key, 3, 5*time.Second)
		done <- struct {
			changed bool
			err     error
		}{changed: changed, err: err}
	}()
	<-started
	activeWait := time.NewTimer(20 * time.Millisecond)
	select {
	case result := <-done:
		activeWait.Stop()
		t.Fatalf("product wait returned before cancellation: changed=%v err=%v", result.changed, result.err)
	case <-activeWait.C:
	}
	cancel()
	select {
	case result := <-done:
		if result.changed || !errors.Is(result.err, context.Canceled) {
			t.Fatalf("canceled active product wait = changed:%v err:%v", result.changed, result.err)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("active product wait did not stop promptly after cancellation")
	}
}

func TestStatsServiceWaitForProductPublicationHeartbeatDeadlineIsAbsolute(t *testing.T) {
	service := NewStatsService(nil, StatsServiceConfig{Products: []ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}}})
	key := ProductKey{RangeSeconds: 10, ResolutionSeconds: 10}
	service.mu.Lock()
	service.products[key] = statsProduct{key: key, cacheGeneration: 3}
	service.notify = make(chan struct{})
	service.mu.Unlock()

	done := make(chan struct {
		changed bool
		err     error
	}, 1)
	go func() {
		changed, err := service.WaitForProductPublication(context.Background(), key, 3, 40*time.Millisecond)
		done <- struct {
			changed bool
			err     error
		}{changed, err}
	}()

	// Refresh closes and replaces the notifier. Keep producing unrelated
	// notifications; each one must not restart the heartbeat deadline.
	ticker := time.NewTicker(time.Millisecond)
	defer ticker.Stop()
	deadline := time.NewTimer(200 * time.Millisecond)
	defer stopTimer(deadline)
	for {
		select {
		case <-ticker.C:
			service.mu.Lock()
			oldNotify := service.notify
			service.notify = make(chan struct{})
			service.mu.Unlock()
			close(oldNotify)
		case result := <-done:
			if result.changed || result.err != nil {
				t.Fatalf("heartbeat wait did not honor absolute deadline: changed=%v err=%v", result.changed, result.err)
			}
			return
		case <-deadline.C:
			t.Fatal("heartbeat wait restarted its deadline after notifications")
		}
	}
}

func TestRingBucketPayloadUsesPythonWireShapeAndRoundTripsCostEvidence(t *testing.T) {
	first, last := 101.0, 109.0
	bucket := MaterializedBucket{
		Start: 100, Duration: 10, Complete: false, SourceCount: 2,
		FirstObservedAt: &first, LastObservedAt: &last,
		Series: []MaterializedSeries{{Name: "usage_tokens", Value: 12, SourceCount: 1, FirstObservedAt: 105, LastObservedAt: 105}},
		Cost: MaterializedCostDetail{
			PricedAtoms: 1, PricedTokens: 12,
			Models:     []MaterializedCostAttribution{{Key: "model-key", Provider: "p", Model: "m", TotalTokens: 12, Dimensions: []MaterializedCostDimension{{Dimension: "output", Tokens: 12, MicroUSD: 3, APIListMicroUSD: 4}}, PricedAtoms: 1, PricedTokens: 12}},
			Dimensions: []MaterializedCostDimension{{Dimension: "output", Tokens: 12, MicroUSD: 3, APIListMicroUSD: 4}},
			Evidence:   []PriceEvidence{{Key: "evidence-key", Provider: "p", Model: "m", Dimension: "output", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", PricingProfile: "default", ServiceTier: "standard", CatalogModel: "m", RateUSD: "1.00", RateScale: 1000000, EffectiveFrom: "1970-01-01T00:00:00Z", SourceKind: "catalog", SourceURL: "https://example.invalid/catalog", CatalogRevision: 7, Tokens: 12, MicroUSD: 3, APIListMicroUSD: 4, PricedAtoms: 1}},
		},
	}
	encoded, err := encodeRingBucketPayload(bucket, []MaterializedNoData{{Family: "gpu", SourceID: "host", EpochID: "epoch", Start: 103, End: 107, NativeCadenceSeconds: 10, Reason: "driver unavailable"}}, 4, 110)
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := json.Unmarshal([]byte(encoded), &raw); err != nil {
		t.Fatal(err)
	}
	if got, want := sortedJSONKeys(raw), []string{"bucket", "cache_generation", "cost_detail", "generated_at", "no_data", "version"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("ring payload keys = %v, want %v", got, want)
	}
	decoded, gaps, err := decodeRingBucketPayload(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if decoded.Start != 100 || decoded.Duration != 10 || decoded.Complete || decoded.SourceCount != 2 || len(decoded.Cost.Evidence) != 1 || decoded.Cost.Evidence[0].CatalogRevision != 7 {
		t.Fatalf("decoded ring bucket = %+v", decoded)
	}
	if len(decoded.Cost.Models) != 1 || decoded.Cost.Models[0].TotalTokens != 12 || decoded.Cost.Models[0].MicroUSD != 3 || decoded.Cost.Models[0].APIListMicroUSD != 4 {
		t.Fatalf("decoded model attribution totals = %+v", decoded.Cost.Models)
	}
	if len(gaps) != 1 || gaps[0].Reason != "driver unavailable" {
		t.Fatalf("decoded ring no-data = %+v", gaps)
	}
}

func TestDecodeRingBucketPayloadRejectsUnsafeAttributionDimensionTotals(t *testing.T) {
	payload := ringBucketPayload{
		Version: RingPayloadVersion, GeneratedAt: 1, CacheGeneration: 1,
		Bucket: ringBucketWire{Start: 0, Duration: 10},
		CostDetail: ringCostDetailWire{Models: []ringCostAttributionWire{{
			Dimensions: []ringCostDimensionWire{
				{Dimension: "input", Tokens: maxSafeJSONInteger},
				{Dimension: "output", Tokens: 1},
			},
		}}},
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := decodeRingBucketPayload(string(encoded)); err == nil {
		t.Fatal("ring decoder accepted attribution token dimensions above the JSON-safe integer limit")
	}
}

func TestDecodeRingBucketPayloadWithMetadataReturnsEnvelopeFields(t *testing.T) {
	bucket := MaterializedBucket{Start: 10, Duration: 10, Complete: true}
	encoded, err := encodeRingBucketPayload(bucket, nil, 17, 23)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := decodeRingBucketPayloadWithMetadata(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if !decoded.hasMetadata || decoded.cacheGeneration != 17 || decoded.generatedAt != 23 {
		t.Fatalf("decoded metadata = %+v", decoded)
	}
	if decoded.bucket.Start != bucket.Start || decoded.bucket.Duration != bucket.Duration || !decoded.bucket.Complete {
		t.Fatalf("decoded bucket = %+v", decoded.bucket)
	}
	legacy, err := json.Marshal(bucket)
	if err != nil {
		t.Fatal(err)
	}
	legacyDecoded, err := decodeRingBucketPayloadWithMetadata(string(legacy))
	if err != nil {
		t.Fatal(err)
	}
	if legacyDecoded.hasMetadata || legacyDecoded.bucket.Start != bucket.Start {
		t.Fatalf("legacy decoded payload = %+v", legacyDecoded)
	}
}

func sortedJSONKeys(value map[string]any) []string {
	keys := make([]string, 0, len(value))
	for key := range value {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func TestDebugStatsServiceRefreshTiming(t *testing.T) {
	if !testing.Verbose() {
		t.Skip("diagnostic repro")
	}
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	keys := make([]ProductKey, 0, 13)
	for _, window := range []int{300, 900, 1800, 3600, 7200, 14400, 28800, 57600, 86400} {
		for _, resolution := range []int{1, 10, 60, 300} {
			if capacity, ok := ringCapacities[resolution]; ok && window%resolution == 0 && window/resolution <= capacity {
				keys = append(keys, ProductKey{RangeSeconds: window, ResolutionSeconds: resolution})
			}
		}
	}
	service := NewStatsService(writer, StatsServiceConfig{Products: keys, Now: func() time.Time { return time.Unix(2_000_000_000, 0) }})
	started := time.Now()
	err = service.Refresh(ctx)
	t.Logf("keys=%d elapsed=%s err=%v state=%+v", len(keys), time.Since(started), err, service.State())
}
