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
	}, nil, 1, 100)
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
	interior, err := encodeRingBucketPayload(MaterializedBucket{Start: 80, Duration: 10, Complete: false, SourceCount: 1, Series: []MaterializedSeries{{Name: "process_cpu_percent", Value: 4, SourceCount: 1, FirstObservedAt: 85, LastObservedAt: 85}}}, nil, 1, 100)
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
	if !snapshot.Materialized.Buckets[0].Complete || len(snapshot.Materialized.Buckets[0].Series) != 0 || snapshot.Materialized.Buckets[0].SourceCount != 0 {
		t.Fatalf("interior bucket was not projected to an empty closed gap: %+v", snapshot.Materialized.Buckets[0])
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
