package main

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yolomux/internal/stats"
)

type runtimeRecorderFailOnceWriter struct {
	mu       sync.Mutex
	delegate stats.BatchWriter
	failed   bool
	seen     []stats.AppendBatch
}

type runtimeRecorderFailCountWriter struct {
	mu                sync.Mutex
	delegate          stats.BatchWriter
	failuresRemaining int
}

func (w *runtimeRecorderFailCountWriter) AppendBatch(ctx context.Context, batch stats.AppendBatch) (stats.AppendResult, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.failuresRemaining > 0 {
		w.failuresRemaining--
		return stats.AppendResult{}, errors.New("synthetic runtime append outage")
	}
	return w.delegate.AppendBatch(ctx, batch)
}

func (w *runtimeRecorderFailOnceWriter) AppendBatch(ctx context.Context, batch stats.AppendBatch) (stats.AppendResult, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if !w.failed {
		w.failed = true
		return stats.AppendResult{}, errors.New("synthetic runtime append failure")
	}
	w.seen = append(w.seen, batch)
	return w.delegate.AppendBatch(ctx, batch)
}

func TestStatsRuntimeRecorderPersistsSyntheticLoadWithoutBrowserDemand(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	writer.EnableCurrentFamilyValidation()
	defer writer.Close()
	at := time.Unix(2000, 0)
	lifecycleCPU := 12.5
	statusCPU := 3.0
	recorder := newStatsRuntimeRecorder(writer, func(time.Time) []stats.ServiceLoadSample {
		return []stats.ServiceLoadSample{
			{SourceID: "runtime:stats_lifecycle", Running: true, CPUPercent: &lifecycleCPU},
			{SourceID: "runtime:agent_status", Running: true, CPUPercent: &statusCPU},
		}
	}, func() time.Time { return at }, time.Second, "synthetic-runtime", 7222)
	recorder.RunOnce(ctx)
	at = at.Add(time.Second)
	recorder.RunOnce(ctx)

	database, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(ctx, 1999, 2002, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(observations) != 4 {
		t.Fatalf("persisted runtime observations = %#v, want four synthetic rows", observations)
	}
	for _, observation := range observations {
		if observation.Family != "service_load" || observation.SourceID == "" || observation.Payload["running"] != true {
			t.Fatalf("runtime observation = %#v", observation)
		}
	}
}

func TestStatsRuntimeRecorderRetriesFailedBackgroundBatch(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-runtime-retry.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	writer.EnableCurrentFamilyValidation()
	underlying := &runtimeRecorderFailOnceWriter{delegate: writer}
	at := time.Unix(2100, 0)
	cpu := 7.0
	recorder := newStatsRuntimeRecorder(underlying, func(time.Time) []stats.ServiceLoadSample {
		return []stats.ServiceLoadSample{{SourceID: "yolomux-go", Running: true, CPUPercent: &cpu}}
	}, func() time.Time { return at }, time.Second, "synthetic-runtime-retry", 7222)
	recorder.RunOnce(ctx)
	at = at.Add(time.Second)
	cpu = 9
	recorder.RunOnce(ctx)

	if len(underlying.seen) != 1 || len(underlying.seen[0].Observations) != 2 {
		t.Fatalf("retry append = %#v, want one two-sample batch", underlying.seen)
	}
	database, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(ctx, 2099, 2102, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(observations) != 2 {
		t.Fatalf("retried runtime observations = %#v, want both failed and current samples", observations)
	}
}

func TestStatsRuntimeRecorderQueueOverflowPersistsAnUnavailableGap(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-runtime-overflow.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	writer.EnableCurrentFamilyValidation()
	underlying := &runtimeRecorderFailCountWriter{delegate: writer, failuresRemaining: maxRuntimeRecorderPendingBatches + 1}
	at := time.Unix(2300, 0)
	cpu := 7.0
	recorder := newStatsRuntimeRecorder(underlying, func(time.Time) []stats.ServiceLoadSample {
		return []stats.ServiceLoadSample{{SourceID: "yolomux-go", Running: true, CPUPercent: &cpu}}
	}, func() time.Time { return at }, time.Second, "synthetic-runtime-overflow", 7222)
	for sample := 0; sample < maxRuntimeRecorderPendingBatches+2; sample++ {
		recorder.RunOnce(ctx)
		at = at.Add(time.Second)
	}

	if diagnostic := recorder.diagnostics(); diagnostic["dropped_batches"] != uint64(2) || diagnostic["pending_batches"] != 0 || diagnostic["pending_unavailable"] != 0 {
		t.Fatalf("overflow recovery diagnostics = %#v", diagnostic)
	}
	recorder.RunOnce(ctx)

	database, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(ctx, 2299, 2312, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(observations) != maxRuntimeRecorderPendingBatches+1 {
		t.Fatalf("persisted observations = %d, want eight retained plus the first post-gap sample: %#v", len(observations), observations)
	}
	spans, err := database.ReadUnavailableSpans(ctx, 2299, 2312, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) != 1 || spans[0].Family != "service_load" || spans[0].SourceID != "yolomux-go" || spans[0].Reason != runtimeRecorderOverflowReason {
		t.Fatalf("persisted overflow spans = %#v, want one explicit service-load gap", spans)
	}
	if spans[0].StartedAt != 2308 || spans[0].EndedAt != 2310 {
		t.Fatalf("overflow span = [%v,%v), want [2308,2310)", spans[0].StartedAt, spans[0].EndedAt)
	}
	coverage, err := database.ReadCoverageEpochs(ctx, 2299, 2312, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(coverage) != 2 || coverage[0].EndedAt == nil || *coverage[0].EndedAt != 2308 || coverage[1].StartedAt != 2310 {
		t.Fatalf("coverage epochs bridge the dropped interval: %#v", coverage)
	}
}

func TestStatsRuntimeRecorderProlongedWriterOutageKeepsUnavailableSpansBounded(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-runtime-prolonged-outage.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	writer.EnableCurrentFamilyValidation()
	outageSamples := maxRuntimeRecorderPendingBatches + 100
	underlying := &runtimeRecorderFailCountWriter{delegate: writer, failuresRemaining: outageSamples}
	at := time.Unix(2400, 0)
	cpu := 5.0
	recorder := newStatsRuntimeRecorder(underlying, func(time.Time) []stats.ServiceLoadSample {
		return []stats.ServiceLoadSample{{SourceID: "yolomux-go", Running: true, CPUPercent: &cpu}}
	}, func() time.Time { return at }, time.Second, "synthetic-runtime-prolonged-outage", 7222)
	for sample := 0; sample < outageSamples; sample++ {
		recorder.RunOnce(ctx)
		at = at.Add(time.Second)
	}
	diagnostic := recorder.diagnostics()
	if diagnostic["pending_batches"] != maxRuntimeRecorderPendingBatches || diagnostic["pending_unavailable"] != 1 {
		t.Fatalf("prolonged outage recorder queue grew without bound: %#v", diagnostic)
	}
	if diagnostic["dropped_batches"] != uint64(outageSamples-maxRuntimeRecorderPendingBatches) {
		t.Fatalf("prolonged outage dropped count = %#v, want %d", diagnostic["dropped_batches"], outageSamples-maxRuntimeRecorderPendingBatches)
	}

	// The next successful append must publish the retained samples and one coalesced gap.
	underlying.mu.Lock()
	underlying.failuresRemaining = 0
	underlying.mu.Unlock()
	recorder.RunOnce(ctx)
	diagnostic = recorder.diagnostics()
	if diagnostic["pending_batches"] != 0 || diagnostic["pending_unavailable"] != 0 {
		t.Fatalf("recovered recorder retained outage state: %#v", diagnostic)
	}
	database, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	spans, err := database.ReadUnavailableSpans(ctx, 2399, 2510, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) != 1 || spans[0].Reason != runtimeRecorderOverflowReason || spans[0].EndedAt-spans[0].StartedAt <= float64(outageSamples-maxRuntimeRecorderPendingBatches) {
		t.Fatalf("prolonged outage unavailable span = %#v, want one coalesced bounded gap", spans)
	}
}

func TestStatsRuntimeRecorderOutageBoundsChangingProcessSources(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-runtime-churn.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	writer.EnableCurrentFamilyValidation()
	const outageSamples = 256
	const spanBound = 64
	underlying := &runtimeRecorderFailCountWriter{delegate: writer, failuresRemaining: outageSamples}
	at := time.Unix(2600, 0)
	cpu := 5.0
	sampleID := 0
	recorder := newStatsRuntimeRecorder(underlying, func(time.Time) []stats.ServiceLoadSample {
		sampleID++
		return []stats.ServiceLoadSample{
			{SourceID: "yolomux-go", Running: true, CPUPercent: &cpu},
			{SourceID: fmt.Sprintf("child:%d:instance", sampleID), Running: true, CPUPercent: &cpu},
			{SourceID: fmt.Sprintf("thread:%d:instance", sampleID), Running: true, CPUPercent: &cpu},
		}
	}, func() time.Time { return at }, time.Second, "synthetic-runtime-churn", 7222)
	for sample := 0; sample < outageSamples; sample++ {
		recorder.RunOnce(ctx)
		at = at.Add(time.Second)
		if len(recorder.pendingUnavailable) > spanBound {
			t.Fatalf("outage source churn retained %d unavailable spans after %d cycles; bound=%d", len(recorder.pendingUnavailable), sample+1, spanBound)
		}
	}
	recorder.RunOnce(ctx)
	at = at.Add(time.Second)
	recorder.RunOnce(ctx)
	if got := recorder.diagnostics(); got["pending_batches"] != 0 || got["pending_unavailable"] != 0 {
		t.Fatalf("recovered recorder retained outage state: %#v", got)
	}
	database, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(ctx, 2599, 2900, 100)
	if err != nil {
		t.Fatal(err)
	}
	if len(observations) != (maxRuntimeRecorderPendingBatches+1)*3 {
		t.Fatalf("recovery lost retained or resumed process samples: got %d observations", len(observations))
	}
	spans, err := database.ReadUnavailableSpans(ctx, 2599, 2900, spanBound+1)
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) != spanBound {
		t.Fatalf("persisted unavailable span count=%d, want %d", len(spans), spanBound)
	}
	var aggregate *stats.UnavailableSpan
	for i := range spans {
		span := &spans[i]
		if span.Reason != runtimeRecorderOverflowReason {
			t.Fatalf("lost overflow reason: %#v", span)
		}
		if span.SourceID == "runtime" {
			aggregate = span
		}
	}
	if aggregate == nil || aggregate.EndedAt != 2600+outageSamples+1 {
		t.Fatalf("missing aggregate gap through last dropped cadence: %#v", aggregate)
	}
	// Every discarded process sample retains either its exact identity or the
	// recorder-wide gap when distinct process identities exceed the bound.
	for sample := maxRuntimeRecorderPendingBatches; sample <= outageSamples; sample++ {
		for _, kind := range []string{"child", "thread"} {
			source := fmt.Sprintf("%s:%d:instance", kind, sample+1)
			covered := false
			for _, span := range spans {
				if (span.SourceID == source || span.SourceID == "runtime") && span.StartedAt <= float64(2600+sample) && span.EndedAt >= float64(2601+sample) {
					covered = true
				}
			}
			if !covered {
				t.Fatalf("dropped source %q at %d has no persisted unavailable evidence", source, 2600+sample)
			}
		}
	}
}

func TestStatsRuntimeRecorderStopFlushesPendingBatch(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-runtime-stop-flush.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	writer.EnableCurrentFamilyValidation()
	defer writer.Close()
	underlying := &runtimeRecorderFailOnceWriter{delegate: writer}
	cpu := 13.0
	recorder := newStatsRuntimeRecorder(underlying, func(time.Time) []stats.ServiceLoadSample {
		return []stats.ServiceLoadSample{{SourceID: "yolomux-go", Running: true, CPUPercent: &cpu}}
	}, func() time.Time { return time.Unix(2150, 0) }, time.Second, "synthetic-runtime-stop-flush", 7222)

	// The first append fails, leaving the batch in the recorder's retry queue.
	recorder.RunOnce(ctx)
	if len(underlying.seen) != 0 {
		t.Fatal("synthetic failure unexpectedly reached the delegate")
	}
	diagnostic := recorder.diagnostics()
	if diagnostic["runs"] != uint64(1) || diagnostic["append_errors"] != uint64(1) || diagnostic["pending_batches"] != 1 {
		t.Fatalf("pending recorder diagnostics = %#v", diagnostic)
	}

	// Stop must drain a pending batch even when the loop was not started. This
	// models a graceful shutdown immediately after a transient writer failure.
	recorder.Stop()
	if len(underlying.seen) != 1 || len(underlying.seen[0].Observations) != 1 {
		t.Fatalf("shutdown flush = %#v, want one persisted observation", underlying.seen)
	}
	diagnostic = recorder.diagnostics()
	if diagnostic["append_successes"] != uint64(1) || diagnostic["pending_batches"] != 0 {
		t.Fatalf("flushed recorder diagnostics = %#v", diagnostic)
	}
	observations, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer observations.Close()
	rows, err := observations.ReadObservations(ctx, 2149, 2151, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].SourceID != "yolomux-go" {
		t.Fatalf("shutdown-flushed observations = %#v", rows)
	}
}

func TestStatsRuntimeDiagnosticsPublishesRecorderHealth(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-runtime-diagnostics.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	underlying := &runtimeRecorderFailOnceWriter{delegate: writer}
	cpu := 2.0
	recorder := newStatsRuntimeRecorder(underlying, func(time.Time) []stats.ServiceLoadSample {
		return []stats.ServiceLoadSample{{SourceID: "yolomux-go", Running: true, CPUPercent: &cpu}}
	}, func() time.Time { return time.Unix(2160, 0) }, time.Second, "synthetic-runtime-diagnostics", 7222)
	recorder.RunOnce(ctx)

	payload := (&server{statsRuntimeRecorder: recorder}).statsRuntimeDiagnostics()
	diagnostic, ok := payload["runtime_recorder"].(map[string]any)
	if !ok {
		t.Fatalf("runtime recorder diagnostics missing: %#v", payload)
	}
	if diagnostic["available"] != true || diagnostic["append_errors"] != uint64(1) || diagnostic["pending_batches"] != 1 {
		t.Fatalf("runtime recorder diagnostics = %#v", diagnostic)
	}
}

func TestStatsRuntimeRecorderPersistsGoProcessMetricsThroughReloadedProduct(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-process-stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	at := time.Unix(1990, 0)
	cpu := 11.0
	rss := uint64(128)
	childCPU := 3.0
	childRSS := uint64(64)
	threadCPU := 1.25
	recorder := newStatsRuntimeRecorder(writer, func(time.Time) []stats.ServiceLoadSample {
		return []stats.ServiceLoadSample{
			{SourceID: "yolomux-go", Running: true, CPUPercent: &cpu, RSSBytes: func() *float64 {
				value := float64(rss)
				return &value
			}()},
			{SourceID: "yolomux-go:child:8123", Running: true, CPUPercent: &childCPU, RSSBytes: func() *float64 {
				value := float64(childRSS)
				return &value
			}()},
			{SourceID: "yolomux-go:thread:41", Running: true, CPUPercent: &threadCPU},
		}
	}, func() time.Time { return at }, time.Second, "synthetic-process", 7222)
	recorder.RunOnce(ctx)
	at = at.Add(time.Second)
	cpu = 23.0
	rss = 256
	recorder.RunOnce(ctx)
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	now := time.Unix(2000, 0)
	lifecycle := stats.NewLifecycle(reopened, stats.LifecycleConfig{Cadence: time.Second, WindowSeconds: 120, Now: func() time.Time { return now }})
	lifecycle.DemandProduct(120, 10)
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	service := stats.NewStatsService(reopened, stats.StatsServiceConfig{
		Products: []stats.ProductKey{{RangeSeconds: 120, ResolutionSeconds: 10}},
		Now:      func() time.Time { return now },
	})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	snapshot, err := service.CurrentSnapshot(ctx, 120, 10)
	if err != nil {
		t.Fatal(err)
	}
	foundCPU, foundRSS, foundChildCPU, foundChildRSS, foundThreadCPU := false, false, false, false, false
	for _, bucket := range snapshot.Materialized.Buckets {
		for _, series := range bucket.Series {
			if series.Name == "service_cpu_percent:yolomux-go" && series.Value > 0 {
				foundCPU = true
			}
			if series.Name == "service_rss_bytes:yolomux-go" && series.Value > 0 {
				foundRSS = true
			}
			if series.Name == "service_cpu_percent:yolomux-go:child:8123" && series.Value > 0 {
				foundChildCPU = true
			}
			if series.Name == "service_rss_bytes:yolomux-go:child:8123" && series.Value > 0 {
				foundChildRSS = true
			}
			if series.Name == "service_cpu_percent:yolomux-go:thread:41" && series.Value > 0 {
				foundThreadCPU = true
			}
		}
	}
	if !foundCPU || !foundRSS || !foundChildCPU || !foundChildRSS || !foundThreadCPU {
		t.Fatalf("reloaded Daemons product omitted Go process metrics: cpu=%v rss=%v child_cpu=%v child_rss=%v thread_cpu=%v snapshot=%+v", foundCPU, foundRSS, foundChildCPU, foundChildRSS, foundThreadCPU, snapshot.Materialized)
	}
}

func TestStatsRuntimeRecorderMarksRSSOnlySampleUnavailable(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-rss-only-stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	writer.EnableCurrentFamilyValidation()
	rss := 128.0
	recorder := newStatsRuntimeRecorder(writer, func(time.Time) []stats.ServiceLoadSample {
		return []stats.ServiceLoadSample{{SourceID: "yolomux-go", Running: true, RSSBytes: &rss}}
	}, func() time.Time { return time.Unix(1990, 0) }, time.Second, "synthetic-rss-only", 7222)
	recorder.RunOnce(ctx)
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	observations, err := reopened.ReadObservations(ctx, 1989, 1992, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(observations) != 0 {
		t.Fatalf("RSS-only sample produced a Python-incompatible observation: %#v", observations)
	}
	spans, err := reopened.ReadUnavailableSpans(ctx, 1989, 1992, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) != 1 || spans[0].Family != "service_load" || spans[0].Reason != "runtime_sample_unavailable" {
		t.Fatalf("RSS-only sample unavailable span = %#v", spans)
	}
}

func TestStatsRuntimeRecorderPersistsUnavailableSpanWhenNoSampleIsUsable(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-runtime-unavailable.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	writer.EnableCurrentFamilyValidation()
	defer writer.Close()
	at := time.Unix(2200, 0)
	recorder := newStatsRuntimeRecorder(writer, func(time.Time) []stats.ServiceLoadSample {
		return nil
	}, func() time.Time { return at }, time.Second, "synthetic-runtime-unavailable", 7222)
	recorder.RunOnce(ctx)

	database, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	spans, err := database.ReadUnavailableSpans(ctx, 2199, 2202, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) != 1 {
		t.Fatalf("unavailable runtime spans = %#v, want one explicit span", spans)
	}
	span := spans[0]
	if span.Family != "service_load" || span.SourceID != "runtime" || span.Reason != "runtime_sample_unavailable" {
		t.Fatalf("unavailable runtime span = %#v", span)
	}
}

func TestStatsRuntimeRecorderDeduplicatesRetriedUnavailableInterval(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-runtime-unavailable-retry.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	writer.EnableCurrentFamilyValidation()
	defer writer.Close()
	underlying := &runtimeRecorderFailOnceWriter{delegate: writer}
	at := time.Unix(2210, 0)
	recorder := newStatsRuntimeRecorder(underlying, func(time.Time) []stats.ServiceLoadSample {
		return nil
	}, func() time.Time { return at }, time.Second, "synthetic-runtime-unavailable-retry", 7222)
	recorder.RunOnce(ctx)
	recorder.RunOnce(ctx)
	if diagnostic := recorder.diagnostics(); diagnostic["append_successes"] != uint64(1) || diagnostic["pending_batches"] != 0 {
		t.Fatalf("deduplicated unavailable retry diagnostics = %#v", diagnostic)
	}
	database, err := stats.OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	spans, err := database.ReadUnavailableSpans(ctx, 2209, 2212, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) != 1 {
		t.Fatalf("retried unavailable spans = %#v, want one deduplicated span", spans)
	}
}

func TestStatsRuntimeRecorderRejectsMissingDependencies(t *testing.T) {
	if err := (*statsRuntimeRecorder)(nil).Start(context.Background()); err == nil {
		t.Fatal("nil recorder unexpectedly started")
	}
}
