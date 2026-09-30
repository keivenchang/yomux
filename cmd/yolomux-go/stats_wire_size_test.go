package main

import (
	"encoding/json"
	"github.com/keivenchang/yolomux/internal/stats"
	"math"
	"math/rand"
	"testing"
)

func TestStatsWireSizeMatchesJSON(t *testing.T) {
	random := rand.New(rand.NewSource(7222))
	values := []any{nil, true, false, int64(-9223372036854775808), uint64(18446744073709551615), math.Copysign(0, -1), "\x00\b\f\n\r\t\"\\<>&\u2028\u2029é😀\xff", []any{}, []any(nil), map[string]any{}, map[string]any(nil), []byte("binary")}
	for _, number := range []float64{1e-7, 1e-6, 1e20, 1e21, -1e-9, math.SmallestNonzeroFloat64, math.MaxFloat64} {
		values = append(values, number, float32(number))
	}
	for i := 0; i < 1000; i++ {
		value := math.Float64frombits(random.Uint64())
		if !math.IsNaN(value) && !math.IsInf(value, 0) {
			values = append(values, value)
		}
		data := make([]byte, i%128)
		random.Read(data)
		values = append(values, string(data))
	}
	for _, value := range values {
		for _, candidate := range []any{value, map[string]any{"value": value, "nested": []map[string]any{{"v": value}}}} {
			encoded, marshalErr := json.Marshal(candidate)
			measured, sizeErr := statsWireSize(candidate)
			if (marshalErr != nil) != (sizeErr != nil) || marshalErr == nil && measured != len(encoded) {
				t.Fatalf("size(%#v)=%d,%v; JSON=%d,%v", candidate, measured, sizeErr, len(encoded), marshalErr)
			}
		}
	}
}

func TestStatsTypedDeltaMatchesCompleteWireProjection(t *testing.T) {
	random := rand.New(rand.NewSource(7222))
	for iteration := 0; iteration < 100; iteration++ {
		previous := stats.StatsSnapshot{CacheGeneration: 1, Materialized: stats.MaterializedLayer{}}
		current := stats.StatsSnapshot{CacheGeneration: 2, Materialized: stats.MaterializedLayer{}}
		for i := 0; i < 30; i++ {
			bucket := stats.MaterializedBucket{Start: float64(i * 10), Duration: 10, Complete: true, SourceCount: 1, Series: []stats.MaterializedSeries{{Name: "cpu", Value: float64(i), SourceCount: 1, FirstObservedAt: float64(i * 10), LastObservedAt: float64(i*10 + 5)}}}
			previous.Materialized.Buckets = append(previous.Materialized.Buckets, bucket)
			if random.Intn(10) == 0 {
				continue
			}
			bucket.Series = append([]stats.MaterializedSeries(nil), bucket.Series...)
			if random.Intn(10) == 0 {
				bucket.Series[0].Value++
			}
			if random.Intn(10) == 0 {
				bucket.Complete = false
			}
			current.Materialized.Buckets = append(current.Materialized.Buckets, bucket)
		}
		previous.Materialized.NoData = []stats.MaterializedNoData{{Family: "cpu", SourceID: "h", EpochID: "e", Start: 1, End: 5, Reason: "old"}}
		current.Materialized.NoData = append([]stats.MaterializedNoData(nil), previous.Materialized.NoData...)
		if iteration%2 == 0 {
			current.Materialized.NoData[0].Reason = "new"
		}
		if iteration%3 == 0 {
			current.Materialized.NoData = nil
		}
		query := statsQuery{rangeSeconds: 300, resolution: 10, afterCacheGeneration: 1}
		full := deltaFromWireMaps(snapshotWire(previous, query), snapshotWire(current, query), query)
		before, after := statsDeltaWirePair(previous, current, query)
		selected := deltaFromWireMaps(before, after, query)
		want, _ := json.Marshal(full)
		got, _ := json.Marshal(selected)
		if string(want) != string(got) {
			t.Fatalf("typed delta differs at iteration %d\nwant %s\ngot %s", iteration, want, got)
		}
	}
}
