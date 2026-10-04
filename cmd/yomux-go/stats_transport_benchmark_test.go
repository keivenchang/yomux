package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

// Keep this fixture fixed across the transport and service changes: 300 one-second
// buckets, one process observation per bucket, then one corrected observation.
func BenchmarkStatsTransport(b *testing.B) {
	ctx := context.Background()
	writer, err := stats.OpenWriter(ctx, filepath.Join(b.TempDir(), "stats.sqlite3"))
	if err != nil {
		b.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1800000300, 0)
	observations := make([]stats.Observation, 300)
	for i := range observations {
		observations[i] = stats.Observation{EventID: fmt.Sprintf("transport-%d", i), Family: "cpu", SourceID: "host", EpochID: "fixed", OwnerGeneration: 1, ObservedAt: float64(now.Unix() - 299 + int64(i)), Payload: map[string]any{"process_percent": float64(i % 100)}}
	}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: observations}); err != nil {
		b.Fatal(err)
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, Now: func() time.Time { return now }})
	b.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, Now: func() time.Time { return now }})
	publish := func() {
		for attempt := 0; attempt < 64; attempt++ {
			if err := lifecycle.RunOnce(ctx); err != nil {
				b.Fatal(err)
			}
			if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
				b.Fatal(err)
			}
			if service.State().Ready {
				return
			}
		}
		b.Fatal("fixture product is not ready")
	}
	publish()
	srv := &server{statsService: service, statsNow: func() time.Time { return now }}
	snapshotURL := "/api/stats-snapshot?range_seconds=300&resolution=1&client_id=benchmark"
	srv.statsSnapshot(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, snapshotURL, nil))
	previous, err := service.CurrentSnapshot(ctx, 300, 1)
	if err != nil {
		b.Fatal(err)
	}
	observations[299].EventID = "transport-correction"
	observations[299].Payload = map[string]any{"process_percent": 42.0}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: observations[299:]}); err != nil {
		b.Fatal(err)
	}
	publish()
	current, err := service.CurrentSnapshot(ctx, 300, 1)
	if err != nil {
		b.Fatal(err)
	}
	for _, test := range []struct {
		name, url string
		handler   http.HandlerFunc
	}{
		{"HTTP/current", snapshotURL, srv.statsSnapshot},
		{"HTTP/unchanged", fmt.Sprintf("%s&since_generation=%d", snapshotURL, current.CacheGeneration), srv.statsSnapshot},
		{"HTTP/delta", fmt.Sprintf("/api/stats-delta?range_seconds=300&resolution_seconds=1&client_id=benchmark&after_cache_generation=%d&after_revision=0", previous.CacheGeneration), srv.statsDelta},
		{"HTTP/delta-unchanged", fmt.Sprintf("/api/stats-delta?range_seconds=300&resolution_seconds=1&client_id=benchmark&after_cache_generation=%d&after_revision=0", current.CacheGeneration), srv.statsDelta},
	} {
		b.Run(test.name, func(b *testing.B) {
			request := httptest.NewRequest(http.MethodGet, test.url, nil)
			b.ReportAllocs()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				test.handler(httptest.NewRecorder(), request)
			}
		})
	}
	b.Run("SSE/initial", func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			ctx, cancel := context.WithCancel(ctx)
			recorder := &benchmarkStatsStreamWriter{ResponseRecorder: httptest.NewRecorder(), cancel: cancel}
			srv.statsStream(recorder, httptest.NewRequest(http.MethodGet, snapshotURL, nil).WithContext(ctx))
			cancel()
		}
	})
	b.Run("HTTP/concurrent", func(b *testing.B) {
		request := httptest.NewRequest(http.MethodGet, snapshotURL, nil)
		b.ReportAllocs()
		b.RunParallel(func(pb *testing.PB) {
			for pb.Next() {
				srv.statsSnapshot(httptest.NewRecorder(), request)
			}
		})
	})
}

type benchmarkStatsStreamWriter struct {
	*httptest.ResponseRecorder
	cancel context.CancelFunc
}

func (w *benchmarkStatsStreamWriter) Write(data []byte) (int, error) {
	if bytes.Contains(data, []byte("event: ready")) {
		w.cancel()
	}
	return w.ResponseRecorder.Write(data)
}
