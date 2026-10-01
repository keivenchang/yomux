package stats

import (
	"context"
	"fmt"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
	"unsafe"
)

// Fixed persisted coverage workload shared by the before/after measurements.
func graphBenchmarkFixture(b *testing.B) (*Writer, StatsServiceConfig) {
	b.Helper()
	ctx := context.Background()
	w, err := OpenWriter(ctx, filepath.Join(b.TempDir(), "stats.sqlite3"))
	if err != nil {
		b.Fatal(err)
	}
	b.Cleanup(func() { _ = w.Close() })
	const end = 2_000_000_100
	writes := make([]RingBucketWrite, 288)
	for i := range writes {
		start := int64(end - (288-i)*300)
		spans := make([]MaterializedNoData, 200)
		for j := range spans {
			spans[j] = MaterializedNoData{Family: "agent_tokens", SourceID: fmt.Sprintf("source-%04d", j), EpochID: "epoch", Reason: "source unavailable", Start: float64(start), End: float64(start + 300), NativeCadenceSeconds: 60}
		}
		payload, e := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: 300, Complete: true}, spans, 1, end)
		if e != nil {
			b.Fatal(e)
		}
		writes[i] = RingBucketWrite{ResolutionSeconds: 300, BucketStart: start, BucketJSON: payload, Complete: true}
	}
	if _, err := w.PublishRingBuckets(ctx, writes, 1, end, true); err != nil {
		b.Fatal(err)
	}
	return w, StatsServiceConfig{Products: []ProductKey{{86400, 300}, {7200, 300}}, Now: func() time.Time { return time.Unix(end-1, 0) }}
}

// Change every coverage identity, keeping widths and windows fixed so growth
// after the history fills represents retention, rather than a larger workload.
func TestStatsServiceCoverageChurnRetentionPlateaus(t *testing.T) {
	ctx := context.Background()
	w, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	const end, bucketCount, sources, generations = 2_000_000_100, 24, 96, 24
	keys := []ProductKey{{7200, 300}, {3600, 300}}
	s := NewStatsService(w, StatsServiceConfig{Products: keys, Now: func() time.Time { return time.Unix(end-1, 0) }})
	publish := func(generation int) {
		writes := make([]RingBucketWrite, bucketCount)
		for i := range writes {
			start := int64(end - (bucketCount-i)*300)
			spans := make([]MaterializedNoData, sources)
			for j := range spans {
				spans[j] = MaterializedNoData{Family: "agent_tokens", SourceID: fmt.Sprintf("generation-%02d-source-%03d", generation, j), EpochID: "epoch", Reason: "source unavailable", Start: float64(start), End: float64(start + 150), NativeCadenceSeconds: 60}
			}
			payload, err := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: 300, Complete: true}, spans, int64(generation), float64(end+generation))
			if err != nil {
				t.Fatal(err)
			}
			writes[i] = RingBucketWrite{ResolutionSeconds: 300, BucketStart: start, BucketJSON: payload, Complete: true}
		}
		if _, err := w.PublishRingBuckets(ctx, writes, int64(generation), float64(end+generation), true); err != nil {
			t.Fatal(err)
		}
		if err := s.Refresh(ctx); err != nil {
			t.Fatal(err)
		}
	}
	var plateauBytes int
	var plateauHeap uint64
	for generation := 1; generation <= generations; generation++ {
		publish(generation)
		for _, key := range keys {
			for retained := max(1, generation-7); retained <= generation; retained++ {
				snapshot, ok := s.GenerationSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds, int64(retained))
				if !ok || len(snapshot.Materialized.NoData) != key.RangeSeconds/300*sources {
					t.Fatalf("generation %d product %+v lost retained coverage %d", generation, key, retained)
				}
				for _, span := range snapshot.Materialized.NoData {
					if !strings.HasPrefix(span.SourceID, fmt.Sprintf("generation-%02d-", retained)) {
						t.Fatalf("generation %d coverage mutated: %s", retained, span.SourceID)
					}
				}
			}
			if generation > 8 {
				if _, ok := s.GenerationSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds, int64(generation-8)); ok {
					t.Fatalf("evicted generation %d remains readable", generation-8)
				}
			}
		}
		bytes := retainedCoverageBytes(t, s, max(1, generation-7))
		if generation == 8 {
			plateauBytes = bytes
		} else if generation > 8 && bytes != plateauBytes {
			t.Fatalf("retained coverage grew after history filled: generation=%d bytes=%d plateau=%d", generation, bytes, plateauBytes)
		}
		if generation%8 == 0 {
			runtime.GC()
			var memory runtime.MemStats
			runtime.ReadMemStats(&memory)
			if generation == 8 {
				plateauHeap = memory.HeapAlloc
			}
			// The exact owner traversal above is deterministic. HeapAlloc also
			// includes SQLite/Go test infrastructure, so allow 1 MiB of noise.
			if memory.HeapAlloc > plateauHeap+1<<20 {
				t.Fatalf("heap did not plateau: generation=%d heap=%d first_full_history=%d", generation, memory.HeapAlloc, plateauHeap)
			}
			t.Logf("coverage_churn generations=%d buckets=%d sources=%d retained_coverage_upper_bound_bytes=%d heap_alloc_bytes=%d", generation, bucketCount, sources, bytes, memory.HeapAlloc)
		}
	}
	runtime.KeepAlive(s)
}

// Include backing-array tails: a shortened slice can hide a still-reachable
// expired generation. Count shared coverage arrays once, with string lengths
// as a conservative upper bound because decoded strings can share storage.
func retainedCoverageBytes(t *testing.T, s *StatsService, oldest int) int {
	t.Helper()
	seen := make(map[*MaterializedNoData]bool)
	bytes := 0
	visit := func(spans []MaterializedNoData) {
		if cap(spans) == 0 {
			return
		}
		spans = spans[:cap(spans)]
		if seen[&spans[0]] {
			return
		}
		seen[&spans[0]] = true
		bytes += len(spans) * int(unsafe.Sizeof(MaterializedNoData{}))
		for _, span := range spans {
			if span.SourceID == "" {
				continue
			}
			var generation int
			if _, err := fmt.Sscanf(span.SourceID, "generation-%02d-source-", &generation); err != nil || generation < oldest {
				t.Fatalf("expired coverage remains reachable: source=%q oldest=%d error=%v", span.SourceID, oldest, err)
			}
			bytes += len(span.Family) + len(span.SourceID) + len(span.EpochID) + len(span.Reason)
		}
	}
	for _, product := range s.products {
		for _, bucket := range product.bucketVersions {
			if bucket.noData != nil {
				visit(bucket.noData.spans)
			}
			if bucket.closedTail != nil {
				visit(bucket.closedTail.spans)
			}
		}
		visit(product.snapshot.Materialized.NoData)
	}
	for _, history := range s.history {
		for _, product := range history[:cap(history)] {
			if product.snapshot.Materialized.NoData != nil {
				t.Fatalf("historical product retains expanded no-data coverage: %d spans", len(product.snapshot.Materialized.NoData))
			}
			for _, bucket := range product.bucketVersions {
				if bucket.noData != nil {
					visit(bucket.noData.spans)
				}
				if bucket.closedTail != nil {
					visit(bucket.closedTail.spans)
				}
			}
			visit(product.snapshot.Materialized.NoData)
		}
	}
	for _, bucket := range s.ringBucketCache {
		visit(bucket.noData.spans)
	}
	for _, sets := range s.noDataSets {
		for _, set := range sets[:cap(sets)] {
			if set != nil {
				visit(set.spans)
			}
		}
	}
	return bytes
}

func BenchmarkStatsGraph(b *testing.B) {
	w, cfg := graphBenchmarkFixture(b)
	ctx := context.Background()
	b.Run("Cold", func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			s := NewStatsService(w, cfg)
			if err := s.Refresh(ctx); err != nil {
				b.Fatal(err)
			}
			if _, err := s.CurrentSnapshot(ctx, 86400, 300); err != nil {
				b.Fatal(err)
			}
		}
	})
	s := NewStatsService(w, cfg)
	if err := s.Refresh(ctx); err != nil {
		b.Fatal(err)
	}
	b.Run("Warm", func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			if _, err := s.CurrentSnapshot(ctx, 86400, 300); err != nil {
				b.Fatal(err)
			}
		}
	})
	b.Run("Concurrent", func(b *testing.B) {
		b.ReportAllocs()
		b.RunParallel(func(pb *testing.PB) {
			for pb.Next() {
				if _, err := s.CurrentSnapshot(ctx, 86400, 300); err != nil {
					b.Error(err)
				}
			}
		})
	})
	b.Run("UnchangedRefresh", func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			if err := s.Refresh(ctx); err != nil {
				b.Fatal(err)
			}
		}
	})
	b.Run("Retained", func(b *testing.B) {
		runtime.GC()
		var before, after runtime.MemStats
		runtime.ReadMemStats(&before)
		retained := make([]*StatsService, b.N)
		for i := range retained {
			retained[i] = NewStatsService(w, cfg)
			if err := retained[i].Refresh(ctx); err != nil {
				b.Fatal(err)
			}
		}
		runtime.GC()
		runtime.ReadMemStats(&after)
		b.ReportMetric(float64(int64(after.HeapAlloc)-int64(before.HeapAlloc))/float64(b.N), "retained-B/op")
		runtime.KeepAlive(retained)
	})
}
