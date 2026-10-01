package main

import (
	"context"
	"math"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

func TestStatsRuntimeLoadExcludesNestedLifecycleTimersFromCumulativeOwners(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{
		WindowSeconds: 1000,
		Now:           func() time.Time { return time.Unix(1999, 0) },
	})
	writer.AttachLifecycle(lifecycle)
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "runtime-load", Family: "cpu", SourceID: "host", ObservedAt: 1995, EpochID: "e",
		Payload: map[string]any{"process_percent": 2},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	server := &server{statsLifecycle: lifecycle}
	load := server.statsRuntimeLoad()
	for _, nested := range []string{"stats_lifecycle:sqlite", "stats_lifecycle:fact_read", "stats_lifecycle:selection", "stats_lifecycle:materialization"} {
		if _, ok := load[nested]; ok {
			t.Fatalf("cumulative runtime owners double-count nested timer %q: %#v", nested, load)
		}
	}
}

func TestStatsRuntimeLoadIncludesHostCollectorFamilies(t *testing.T) {
	ctx := context.Background()
	demanded := true
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := stats.NewHostCollector(
		stats.NewSyntheticHostSources("synthetic-process", "yolomux-go"),
		writer,
		nil,
		stats.HostCollectorConfig{
			ActiveCadence:    time.Second,
			MemoryCadence:    2 * time.Second,
			GPUActiveCadence: 3 * time.Second,
			EpochID:          "synthetic-host",
			EpochStartedAt:   100,
			ActiveFunc:       func() bool { return demanded },
		},
	)
	collector.RunOnce(ctx)
	load := (&server{hostCollector: collector}).statsRuntimeLoad()
	for _, family := range []string{"cpu", "system_memory", "gpu"} {
		key := "host_collector:" + family
		entry, ok := load[key].(map[string]any)
		if !ok {
			t.Fatalf("runtime load omitted %s: %#v", key, load)
		}
		unit, _ := entry["unit"].(string)
		percent, _ := entry["load_percent"].(float64)
		if unit != "runtime_occupancy" || percent < 0 {
			t.Fatalf("runtime load %s = %#v", key, entry)
		}
	}
	demanded = false
	load = (&server{hostCollector: collector}).statsRuntimeLoad()
	if _, ok := load["host_collector:cpu"]; !ok {
		t.Fatalf("background CPU occupancy disappeared after demand closed: %#v", load)
	}
	if _, ok := load["host_collector:gpu"]; ok {
		t.Fatalf("GPU occupancy replayed after demand closed: %#v", load)
	}
	if _, ok := load["host_collector:system_memory"]; !ok {
		t.Fatalf("system-memory occupancy disappeared after CPU/GPU demand closed: %#v", load)
	}
}

func TestStatsRuntimeServiceLoadSamplesIncludeOwnedProcessResources(t *testing.T) {
	cpu, rss := 17.5, uint64(4096)
	childCPU, childRSS := 4.25, uint64(2048)
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{CPUPercent: &cpu, RSSBytes: &rss, ChildProcesses: []goChildProcessMetric{
			{PID: 8123, State: "running", CPUPercent: &childCPU, RSSBytes: &childRSS},
			{PID: 8123, State: "running", CPUPercent: &childCPU, RSSBytes: &childRSS},
		}}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	if len(samples) != 2 || samples[0].SourceID != server.statsMainProcessSourceID() || samples[0].CPUPercent == nil || *samples[0].CPUPercent != cpu || samples[0].RSSBytes == nil || *samples[0].RSSBytes != float64(rss) {
		t.Fatalf("owned process service sample = %#v, want CPU=%v RSS=%d", samples, cpu, rss)
	}
	if samples[1].SourceID != "yomux-go:child:8123" || samples[1].CPUPercent == nil || *samples[1].CPUPercent != childCPU || samples[1].RSSBytes == nil || *samples[1].RSSBytes != float64(childRSS) {
		t.Fatalf("child process service sample = %#v, want CPU=%v RSS=%d", samples[1], childCPU, childRSS)
	}
}

func TestStatsRuntimeServiceLoadSamplesKeepRSSWhenCPUSampleIsUnavailable(t *testing.T) {
	rss, childRSS := uint64(4096), uint64(2048)
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{RSSBytes: &rss, ChildProcesses: []goChildProcessMetric{{PID: 8123, State: "running", RSSBytes: &childRSS}}}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	if len(samples) != 2 {
		t.Fatalf("RSS-only service samples = %#v, want process and child", samples)
	}
	for index, want := range []struct {
		source string
		rss    float64
	}{
		{source: server.statsMainProcessSourceID(), rss: float64(rss)},
		{source: "yomux-go:child:8123", rss: float64(childRSS)},
	} {
		if samples[index].SourceID != want.source || samples[index].CPUPercent != nil || samples[index].RSSBytes == nil || *samples[index].RSSBytes != want.rss {
			t.Fatalf("RSS-only sample[%d] = %#v, want source=%q RSS=%v without CPU", index, samples[index], want.source, want.rss)
		}
	}
}

func TestStatsRuntimeServiceLoadSamplesIncludeMeasuredGoThreads(t *testing.T) {
	cpu, rss, threadCPU := 17.5, uint64(4096), 3.25
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{
			CPUPercent: &cpu,
			RSSBytes:   &rss,
			Threads: []goThreadMetric{
				{TID: 41, Name: "runtime", State: "running", CPUPercent: &threadCPU},
				{TID: 42, Name: "runtime", State: "sleeping", CPUReasonCode: "thread_cpu_first_sample"},
			},
		}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	if len(samples) != 2 {
		t.Fatalf("thread service samples = %#v, want process plus one measured thread", samples)
	}
	if samples[1].SourceID != "yomux-go:thread:41" || samples[1].CPUPercent == nil || *samples[1].CPUPercent != threadCPU || !samples[1].Running {
		t.Fatalf("measured thread sample = %#v", samples[1])
	}
}

func TestStatsRuntimeServiceLoadSamplesDeduplicateMeasuredGoThreads(t *testing.T) {
	cpu, firstThreadCPU, secondThreadCPU := 17.5, 3.25, 4.5
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{CPUPercent: &cpu, Threads: []goThreadMetric{
			{TID: 41, Name: "runtime", State: "running", CPUPercent: &firstThreadCPU},
			{TID: 41, Name: "runtime-duplicate", State: "running", CPUPercent: &secondThreadCPU},
		}}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	threadSamples := 0
	for _, sample := range samples {
		if sample.SourceID == "yomux-go:thread:41" {
			threadSamples++
			if sample.CPUPercent == nil || *sample.CPUPercent != firstThreadCPU {
				t.Fatalf("deduplicated thread sample = %#v, want first measured row", sample)
			}
		}
	}
	if threadSamples != 1 {
		t.Fatalf("thread 41 sample count = %d, want one: %#v", threadSamples, samples)
	}
}

func TestStatsRuntimeServiceLoadSamplesKeepValidRowsAfterInvalidDuplicates(t *testing.T) {
	cpu, validThreadCPU, validChildRSS := 17.5, 3.25, uint64(2048)
	invalidCPU := math.NaN()
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{
			CPUPercent: &cpu,
			Threads: []goThreadMetric{
				{TID: 41, Name: "runtime-invalid", State: "running", CPUPercent: &invalidCPU},
				{TID: 41, Name: "runtime-valid", State: "running", CPUPercent: &validThreadCPU},
			},
			ChildProcesses: []goChildProcessMetric{
				{PID: 8123, State: "running"},
				{PID: 8123, State: "running", RSSBytes: &validChildRSS},
			},
		}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	threadFound, childFound := false, false
	for _, sample := range samples {
		switch sample.SourceID {
		case "yomux-go:thread:41":
			threadFound = true
			if sample.CPUPercent == nil || *sample.CPUPercent != validThreadCPU {
				t.Fatalf("invalid-first thread sample = %#v", sample)
			}
		case "yomux-go:child:8123":
			childFound = true
			if sample.RSSBytes == nil || *sample.RSSBytes != float64(validChildRSS) {
				t.Fatalf("invalid-first child sample = %#v", sample)
			}
		}
	}
	if !threadFound || !childFound {
		t.Fatalf("valid duplicate rows were suppressed: %#v", samples)
	}
}

func TestStatsRuntimeServiceLoadSamplesMergeComplementaryChildMetrics(t *testing.T) {
	cpu, rss := 1.5, uint64(4096)
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{ChildProcesses: []goChildProcessMetric{
			{PID: 8123, Identity: "8123:100", State: "running", CPUPercent: &cpu},
			{PID: 8123, Identity: "8123:100", State: "running", RSSBytes: &rss},
		}}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	var child *stats.ServiceLoadSample
	for index := range samples {
		if samples[index].SourceID == "yomux-go:child:8123:100" {
			child = &samples[index]
			break
		}
	}
	if child == nil || child.CPUPercent == nil || *child.CPUPercent != cpu || child.RSSBytes == nil || *child.RSSBytes != float64(rss) {
		t.Fatalf("complementary child metrics were not merged: %#v", samples)
	}
}

func TestStatsRuntimeServiceLoadSamplesKeepProcessInstanceIdentity(t *testing.T) {
	cpu := 4.0
	childCPU := 1.25
	threadCPU := 0.75
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{
			CPUPercent:     &cpu,
			Threads:        []goThreadMetric{{TID: 41, Identity: "41:9876", CPUPercent: &threadCPU}},
			ChildProcesses: []goChildProcessMetric{{PID: 8123, Identity: "8123:6543", State: "running", CPUPercent: &childCPU}},
		}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	seen := map[string]bool{}
	for _, sample := range samples {
		seen[sample.SourceID] = true
	}
	for _, want := range []string{"yomux-go:thread:41:9876", "yomux-go:child:8123:6543"} {
		if !seen[want] {
			t.Fatalf("missing process-instance source %q in %#v", want, samples)
		}
	}
	if first, second := goMetricSourceID("child", 8123, "8123:6543"), goMetricSourceID("child", 8123, "8123:9876"); first == second {
		t.Fatalf("reused child PID shared a historical source: %q", first)
	}
	if first, second := goMetricSourceID("child", 8123, "8123:Mon Sep 21 12:34:56 2026"), goMetricSourceID("child", 8123, "8123:Mon Sep 21 12:35:56 2026"); first == second {
		t.Fatalf("reused Darwin child PID shared a historical source: %q", first)
	}
}

func TestStatsRuntimeServiceLoadSamplesKeepDistinctThreadInstancesWithReusedTID(t *testing.T) {
	firstCPU, secondCPU := 0.5, 0.75
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{Threads: []goThreadMetric{
			{TID: 41, Identity: "41:100", State: "running", CPUPercent: &firstCPU},
			{TID: 41, Identity: "41:200", State: "running", CPUPercent: &secondCPU},
		}}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	seen := map[string]float64{}
	for _, sample := range samples {
		if sample.CPUPercent != nil {
			seen[sample.SourceID] = *sample.CPUPercent
		}
	}
	for source, want := range map[string]float64{
		"yomux-go:thread:41:100": firstCPU,
		"yomux-go:thread:41:200": secondCPU,
	} {
		if got, ok := seen[source]; !ok || got != want {
			t.Fatalf("reused thread TID lost process instance %q: samples=%#v", source, samples)
		}
	}
}

func TestStatsRuntimeServiceLoadSamplesKeepDistinctChildInstancesWithReusedPID(t *testing.T) {
	firstCPU, secondCPU := 1.25, 2.5
	server := &server{processMetricsFn: func(time.Time) goProcessMetrics {
		return goProcessMetrics{ChildProcesses: []goChildProcessMetric{
			{PID: 8123, Identity: "8123:100", State: "running", CPUPercent: &firstCPU},
			{PID: 8123, Identity: "8123:200", State: "running", CPUPercent: &secondCPU},
		}}
	}}
	samples := server.statsRuntimeServiceLoadSamples(time.Unix(2000, 0))
	seen := map[string]float64{}
	for _, sample := range samples {
		if sample.CPUPercent != nil {
			seen[sample.SourceID] = *sample.CPUPercent
		}
	}
	for source, want := range map[string]float64{
		"yomux-go:child:8123:100": firstCPU,
		"yomux-go:child:8123:200": secondCPU,
	} {
		if got, ok := seen[source]; !ok || got != want {
			t.Fatalf("reused child PID lost process instance %q: samples=%#v", source, samples)
		}
	}
}

func TestAugmentSnapshotWireRuntimeLoadAddsOnlyLiveOwnerLines(t *testing.T) {
	wire := map[string]any{
		"generated_at": 1999.0,
		"buckets": []map[string]any{
			{"open": false, "series": map[string]map[string]any{"service_cpu_percent:yolomux_go": {"value": 2.5, "source_count": 1}}},
			{"start": 1990.0, "duration": 10.0, "open": true, "series": map[string]map[string]any{}},
		},
	}
	load := map[string]any{
		"stats_lifecycle":           map[string]any{"load_percent": 12.5},
		"stats_lifecycle:sqlite":    map[string]any{"load_percent": 3.5},
		"stats_lifecycle:fact_read": map[string]any{"load_percent": 6.25},
		"agent_tokens":              map[string]any{"load_percent": 4.25},
		"invalid":                   map[string]any{"load_percent": -1.0},
	}
	got := augmentSnapshotWireRuntimeLoad(wire, load)
	buckets := got["buckets"].([]map[string]any)
	if _, ok := buckets[0]["series"].(map[string]map[string]any)["service_cpu_percent:runtime:stats_lifecycle"]; ok {
		t.Fatal("runtime owner line was added to a historical bucket")
	}
	series := buckets[1]["series"].(map[string]map[string]any)
	if got := series["service_cpu_percent:runtime:stats_lifecycle"]["value"]; got != 12.5 {
		t.Fatalf("lifecycle runtime value = %#v, want 12.5", got)
	}
	if got := series["service_cpu_percent:runtime:agent_tokens"]["value"]; got != 4.25 {
		t.Fatalf("token runtime value = %#v, want 4.25", got)
	}
	for key, want := range map[string]float64{
		"service_cpu_percent:runtime:stats_lifecycle:sqlite":    3.5,
		"service_cpu_percent:runtime:stats_lifecycle:fact_read": 6.25,
		"service_cpu_min_percent:runtime:stats_lifecycle":       12.5,
		"service_cpu_max_percent:runtime:stats_lifecycle":       12.5,
	} {
		if got := series[key]["value"]; got != want {
			t.Fatalf("runtime value %s = %#v, want %v", key, got, want)
		}
	}
	for key, item := range series {
		if key == "service_cpu_percent:yolomux_go" {
			continue
		}
		if item["source_count"] != 1 {
			t.Fatalf("runtime series %s source_count = %#v, want 1: %#v", key, item["source_count"], item)
		}
		if item["first_timestamp"] == nil || item["last_timestamp"] == nil {
			t.Fatalf("runtime series %s has no sample timestamps: %#v", key, item)
		}
	}
	source, ok := buckets[1]["source"].(map[string]any)
	if !ok || source["count"] != 1 || source["first_timestamp"] == nil || source["last_timestamp"] == nil {
		t.Fatalf("runtime source summary = %#v", buckets[1]["source"])
	}
	if _, ok := series["service_cpu_percent:runtime:invalid"]; ok {
		t.Fatal("invalid negative runtime load was published")
	}
}

func TestAugmentSnapshotWireRuntimeLoadSkipsClosedRightmostBucket(t *testing.T) {
	originalSource := map[string]any{"count": 1, "first_timestamp": 101.0, "last_timestamp": 105.0}
	originalSeries := map[string]map[string]any{"service_cpu_percent:yolomux_go": {"value": 2.5, "source_count": 1}}
	wire := map[string]any{
		"generated_at":   110.0,
		"rightmost_open": false,
		"buckets": []map[string]any{{
			"start": 100.0, "duration": 10.0, "open": false,
			"source": originalSource,
			"series": originalSeries,
		}},
	}
	load := map[string]any{"stats_lifecycle": map[string]any{"load_percent": 12.5}}

	got := augmentSnapshotWireRuntimeLoad(wire, load)
	buckets := got["buckets"].([]map[string]any)
	if buckets[0]["open"] != false || got["rightmost_open"] != false {
		t.Fatalf("closed bucket state changed: bucket=%#v rightmost=%#v", buckets[0]["open"], got["rightmost_open"])
	}
	series := buckets[0]["series"].(map[string]map[string]any)
	if _, ok := series["service_cpu_percent:runtime:stats_lifecycle"]; ok {
		t.Fatalf("runtime sample outside the half-open window was published: %#v", series)
	}
	source := buckets[0]["source"].(map[string]any)
	if source["count"] != 1 || source["first_timestamp"] != 101.0 || source["last_timestamp"] != 105.0 || series["service_cpu_percent:yolomux_go"]["value"] != 2.5 {
		t.Fatal("closed-bucket augmentation mutated retained source data")
	}
}

func TestStatsProcessSourceIdentitySeparatesEveryOwnerDimension(t *testing.T) {
	base := statsProcessSourceID(123, 7222, 12, "/bin/yolomux-go", "host-a", "boot-a", "proc:10", "owner-a")
	cases := []string{
		statsProcessSourceID(124, 7222, 12, "/bin/yolomux-go", "host-a", "boot-a", "proc:10", "owner-a"),
		statsProcessSourceID(123, 7223, 12, "/bin/yolomux-go", "host-a", "boot-a", "proc:10", "owner-a"),
		statsProcessSourceID(123, 7222, 12, "/bin/yolomux-go", "host-b", "boot-a", "proc:10", "owner-a"),
		statsProcessSourceID(123, 7222, 12, "/bin/yolomux-go", "host-a", "boot-b", "proc:10", "owner-a"),
		statsProcessSourceID(123, 7222, 12, "/bin/yolomux-go", "host-a", "boot-a", "proc:11", "owner-a"),
		statsProcessSourceID(123, 7222, 12, "/bin/yolomux-go", "host-a", "boot-a", "proc:10", "owner-b"),
	}
	long := statsProcessSourceID(123, 7222, 12, strings.Repeat("é", 512), strings.Repeat("h", 1024), "b", "s", "o")
	if len(long) > stats.MaxSeriesComponentBytes {
		t.Fatalf("source identity exceeds persistence limit: %d", len(long))
	}
	seen := map[string]bool{base: true}
	for _, candidate := range cases {
		if seen[candidate] {
			t.Fatalf("process identities collided: %s", candidate)
		}
		seen[candidate] = true
	}
	// Colons and separators in owner identity cannot collide with adjacent fields.
	a := statsProcessSourceID(123, 7222, 12, "x", "h", "b", "s:o", "p")
	b := statsProcessSourceID(123, 7222, 12, "x", "h", "b", "s", "o:p")
	if a == b {
		t.Fatal("ambiguous identity field framing")
	}
}

func TestStatsMainProcessSourceIsStableAcrossSamplesAndDistinctAcrossInstances(t *testing.T) {
	a := &server{port: 7222, startedAt: time.Unix(100, 0), background: &goBackgroundOwner{stableHostID: "host", bootID: "boot", processStartIdentity: "start-a", serverID: "owner"}}
	b := &server{port: 7223, startedAt: time.Unix(100, 0), background: &goBackgroundOwner{stableHostID: "host", bootID: "boot", processStartIdentity: "start-b", serverID: "owner"}}
	first := a.statsMainProcessSourceID()
	if first == "yolomux-go" || first != a.statsMainProcessSourceID() || first == b.statsMainProcessSourceID() {
		t.Fatalf("process attribution a=%q b=%q", first, b.statsMainProcessSourceID())
	}
}

func TestStatsMainProcessMissingStartSeparatesRestarts(t *testing.T) {
	makeServer := func(start int64) *server {
		return &server{port: 7222, startedAt: time.Unix(start, 0), background: &goBackgroundOwner{
			stableHostID: "same-host", bootID: "unknown-boot", serverID: "same-reused-pid",
		}}
	}
	first, restarted := makeServer(100), makeServer(200)
	identity := first.statsMainProcessSourceID()
	if identity != first.statsMainProcessSourceID() || identity == restarted.statsMainProcessSourceID() {
		t.Fatalf("missing OS start metadata failed stable restart attribution: first=%s restarted=%s", identity, restarted.statsMainProcessSourceID())
	}
}
