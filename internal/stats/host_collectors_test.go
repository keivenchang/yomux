package stats

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func hostFactContext() HostFactContext {
	return HostFactContext{EpochID: "epoch-1", EpochStartedAt: 100, ObservedAt: 101, CadenceSeconds: 1, OwnerGeneration: 7, SourceID: "host"}
}

func TestParseLinuxOnlineCPUCountUsesSyntheticTopology(t *testing.T) {
	for _, test := range []struct {
		name string
		text string
		want int
	}{
		{name: "ranges and individual CPUs", text: "0-3,5,7-8\n", want: 7},
		{name: "single CPU", text: "0", want: 1},
		{name: "empty", text: "", want: 0},
		{name: "overlap", text: "0-2,2-4", want: 0},
		{name: "reversed", text: "4-2", want: 0},
		{name: "invalid token", text: "0-3,x", want: 0},
		{name: "excessive range", text: "0-65536", want: 0},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := ParseLinuxOnlineCPUCount([]byte(test.text)); got != test.want {
				t.Fatalf("ParseLinuxOnlineCPUCount(%q) = %d, want %d", test.text, got, test.want)
			}
		})
	}
}

func TestReadDirNamesBoundedStopsAtRequestedSyntheticLimit(t *testing.T) {
	root := t.TempDir()
	for index := range 9 {
		if err := os.Mkdir(filepath.Join(root, fmt.Sprintf("entry-%02d", index)), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	names, err := ReadDirNamesBounded(root, 4)
	if err != nil {
		t.Fatal(err)
	}
	if len(names) != 4 {
		t.Fatalf("bounded directory names = %d, want at most 4", len(names))
	}
	allNames, err := ReadDirNamesBounded(root, 12)
	if err != nil || len(allNames) != 9 {
		t.Fatalf("complete synthetic directory names = %d, err=%v, want 9", len(allNames), err)
	}
}

func TestLinuxClockTickRateUsesSyntheticGetconfOutput(t *testing.T) {
	run := func(ctx context.Context, args []string) (CommandOutput, error) {
		if _, ok := ctx.Deadline(); !ok {
			t.Fatal("getconf fixture was not given a bounded context")
		}
		if !reflect.DeepEqual(args, []string{"getconf", "CLK_TCK"}) {
			t.Fatalf("getconf fixture args = %#v", args)
		}
		return CommandOutput{Stdout: []byte("250\n")}, nil
	}
	if got := linuxClockTickRate(run); got != 250 {
		t.Fatalf("synthetic Linux clock tick rate = %d, want 250", got)
	}
	for _, result := range []CommandOutput{
		{ExitCode: 1, Stdout: []byte("250\n")},
		{Stdout: []byte("0\n")},
		{Stdout: []byte("not-a-rate\n")},
		{Stdout: []byte(strings.Repeat("2", 65))},
	} {
		if got := linuxClockTickRate(func(context.Context, []string) (CommandOutput, error) { return result, nil }); got != 0 {
			t.Fatalf("invalid synthetic getconf result %#v produced rate %d", result, got)
		}
	}
	if got := linuxClockTickRate(nil); got != 0 {
		t.Fatalf("missing getconf owner produced rate %d", got)
	}
}

func TestLinuxSystemLogicalCPUsUsesInjectedOnlineList(t *testing.T) {
	reads := 0
	got := linuxSystemLogicalCPUs(func(path string) ([]byte, error) {
		reads++
		if path != "/sys/devices/system/cpu/online" {
			t.Fatalf("CPU topology read path = %q", path)
		}
		return []byte("0-7"), nil
	})
	if got != 8 || reads != 1 {
		t.Fatalf("synthetic Linux topology = CPUs:%d reads:%d, want 8 CPUs from one injected read", got, reads)
	}
}

func TestRunBoundedPSUsesStableSyntheticLocale(t *testing.T) {
	root := t.TempDir()
	ps := filepath.Join(root, "ps")
	if err := os.WriteFile(ps, []byte("#!/bin/sh\nprintf '%s\\n' \"$LC_ALL\"\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", root+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("LC_ALL", "fr_FR.UTF-8")

	result, err := runBoundedCommand(context.Background(), []string{"ps", "-axo", "synthetic"})
	if err != nil || result.ExitCode != 0 || string(result.Stdout) != "C\n" {
		t.Fatalf("synthetic ps locale = %q, err=%v exit=%d; want C locale", result.Stdout, err, result.ExitCode)
	}
}

func TestBuildCPUFactsMatchesPythonShape(t *testing.T) {
	batch := BuildCPUFacts(CPUSample{
		Host: CPUMeasurement{Value: 12.5, Valid: true}, Process: CPUMeasurement{Value: 3.25, Valid: true}, Reason: CPUValid,
	}, map[string]float64{"python": 3.25}, hostFactContext())
	if len(batch.Observations) != 1 || len(batch.CoverageEpochs) != 1 {
		t.Fatalf("facts = %#v", batch)
	}
	want := map[string]any{"process_percent": 3.25, "system_percent": 12.5, "process_cpu_percent": map[string]any{"python": 3.25}}
	if !reflect.DeepEqual(batch.Observations[0].Payload, want) {
		t.Fatalf("payload = %#v, want %#v", batch.Observations[0].Payload, want)
	}
	if batch.Observations[0].EventID != "obs-cpu-c086e13b2bf99ca964d59c4fc7d1d8a7563ffba6b52a83e75bd56ec47ca2910d" || batch.CoverageEpochs[0].EndedAt == nil || *batch.CoverageEpochs[0].EndedAt != 102 {
		t.Fatalf("identity/coverage = %#v / %#v", batch.Observations[0], batch.CoverageEpochs[0])
	}
	if _, err := batch.Validate(); err != nil {
		t.Fatalf("batch does not validate: %v", err)
	}
}

func TestBuildCPUFactsEmitsGroupedBinarySeries(t *testing.T) {
	batch := BuildCPUFacts(CPUSample{
		Host: hostMeasurement(12.5), Process: hostMeasurement(3.25), Reason: CPUValid,
		OwnedProcessCPUPercent: map[string]float64{"yolomux-go": 137.5},
	}, map[string]float64{"python": 8.5, "node": 2.25}, hostFactContext())
	series, ok := batch.Observations[0].Payload["process_cpu_percent"].(map[string]any)
	if !ok || !reflect.DeepEqual(series, map[string]any{"python": 8.5, "node": 2.25}) {
		t.Fatalf("series = %#v", batch.Observations[0].Payload["process_cpu_percent"])
	}
	owned, ok := batch.Observations[0].Payload["owned_process_cpu_percent"].(map[string]any)
	if !ok || !reflect.DeepEqual(owned, map[string]any{"yolomux-go": 137.5}) {
		t.Fatalf("owned series = %#v", batch.Observations[0].Payload["owned_process_cpu_percent"])
	}
	if _, err := batch.Validate(); err != nil {
		t.Fatalf("owned CPU payload does not validate: %v", err)
	}
}

func hostMeasurement(value float64) CPUMeasurement {
	return CPUMeasurement{Value: value, Valid: true}
}

func TestSyntheticHostSourcesUseOnlyReproducibleFixtureValues(t *testing.T) {
	sources := NewSyntheticHostSources("7222:", "yolomux-go")
	if topology, ok := sources.(HostCPUTopology); !ok || topology.SystemLogicalCPUs() != 8 {
		t.Fatalf("synthetic system CPU topology = %T/%v, want deterministic 8 CPUs", sources, topology)
	}
	first, err := sources.CPU(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	second, err := sources.CPU(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if first.Processes["7222:1"].CPU != 12 || second.Processes["7222:1"].CPU != 24 || second.Processes["synthetic-cotenant:1"].CPU != 100 {
		t.Fatalf("synthetic CPU counters = first=%+v second=%+v", first, second)
	}
	if first.Processes["7222:1"].Binary != "yolomux-go" || first.Processes["synthetic-python:1"].Binary != "python" {
		t.Fatalf("synthetic process identities = %+v", first.Processes)
	}
	memory, rows, err := sources.Memory(context.Background())
	if err != nil || memory.UsedBytes != 8<<20 || len(rows) != 3 {
		t.Fatalf("synthetic memory = %+v rows=%+v err=%v", memory, rows, err)
	}
	gpu, err := sources.GPU(context.Background())
	if err != nil || len(gpu) != 1 || gpu[0].Label != "Synthetic GPU" {
		t.Fatalf("synthetic GPU = %+v err=%v", gpu, err)
	}
}

func TestBuildHostFactsValidPayloads(t *testing.T) {
	context := hostFactContext()
	gpu := BuildGPUFacts([]GPUDevice{{SourceID: "gpu:0", Label: "GPU 0", Utilization: 80, MemoryUsedBytes: 10, MemoryTotalBytes: 20}}, context)
	if got := gpu.Observations[0].Payload; !reflect.DeepEqual(got, map[string]any{"util_percent": float64(80), "memory_used_bytes": uint64(10), "memory_capacity_bytes": uint64(20), "label": "GPU 0"}) {
		t.Fatalf("gpu payload = %#v", got)
	}
	memory := BuildSystemMemoryFacts(MemoryCounters{UsedBytes: 8, CapacityBytes: 16}, []ProcessCensusRow{{Identity: "123:1", Binary: "python", RSSBytes: 4}}, MacMemoryDetails{"memory_used_bytes": uint64(9), "pressure_level": nil}, context)
	wantMemory := map[string]any{"used_bytes": uint64(8), "capacity_bytes": uint64(16), "process_memory_bytes": map[string]any{"python": uint64(4)}, "mac_memory_used_bytes": uint64(9)}
	if !reflect.DeepEqual(memory.Observations[0].Payload, wantMemory) {
		t.Fatalf("memory payload = %#v, want %#v", memory.Observations[0].Payload, wantMemory)
	}
	if raw, err := json.Marshal(memory.Observations[0].Payload); err != nil || len(raw) > MaxPayloadBytes {
		t.Fatalf("memory payload bound: %v (%d bytes)", err, len(raw))
	}
}

func TestBuildServiceLoadFactsFiltersInvalidSamplesAndKeepsNullableRSS(t *testing.T) {
	context := hostFactContext()
	rss := 4096.0
	cpu := 12.5
	agentCPU := 2.0
	emptyCPU := 1.0
	nanCPU := math.NaN()
	negativeCPU := -1.0
	batch := BuildServiceLoadFacts([]ServiceLoadSample{
		{SourceID: "runtime:stats_lifecycle", Running: true, CPUPercent: &cpu, RSSBytes: &rss},
		{SourceID: "runtime:agent_status", Running: true, CPUPercent: &agentCPU},
		{SourceID: "", Running: true, CPUPercent: &emptyCPU},
		{SourceID: "runtime:nan", Running: true, CPUPercent: &nanCPU},
		{SourceID: "runtime:negative", Running: true, CPUPercent: &negativeCPU},
		{SourceID: "yolomux-go:rss-only", Running: true, RSSBytes: func() *float64 { value := 2048.0; return &value }()},
	}, context)
	if len(batch.Observations) != 3 || len(batch.CoverageEpochs) != 3 {
		t.Fatalf("service facts = %#v", batch)
	}
	if got := batch.Observations[0].Payload; !reflect.DeepEqual(got, map[string]any{"running": true, "cpu_percent": 12.5, "rss_bytes": 4096.0}) {
		t.Fatalf("measured service payload = %#v", got)
	}
	if got := batch.Observations[1].Payload; !reflect.DeepEqual(got, map[string]any{"running": true, "cpu_percent": 2.0, "rss_bytes": nil}) {
		t.Fatalf("nullable RSS payload = %#v", got)
	}
	if got := batch.Observations[2].Payload; !reflect.DeepEqual(got, map[string]any{"running": true, "rss_bytes": 2048.0}) {
		t.Fatalf("RSS-only payload = %#v", got)
	}
	if batch.Observations[0].SourceID != "runtime:stats_lifecycle" || batch.Observations[1].SourceID != "runtime:agent_status" {
		t.Fatalf("service sources = %#v", batch.Observations)
	}
	if _, err := batch.Validate(); err != nil {
		t.Fatalf("service batch does not validate: %v", err)
	}
}

func TestBuildHostFactsUnavailableForInvalidAndFirstSamples(t *testing.T) {
	context := hostFactContext()
	for _, sample := range []CPUSample{{Reason: CPUFirstSample}, {Reason: CPUInvalidDuration}, {Reason: CPUValid, Host: CPUMeasurement{Valid: true}}} {
		batch := BuildCPUFacts(sample, nil, context)
		if len(batch.Observations) != 0 || len(batch.UnavailableSpans) != 1 || batch.UnavailableSpans[0].Reason == "" {
			t.Fatalf("sample %#v produced %#v", sample, batch)
		}
	}
	for index, batch := range []AppendBatch{
		BuildGPUFacts(nil, context),
		BuildSystemMemoryFacts(MemoryCounters{UsedBytes: 9, CapacityBytes: 8}, nil, nil, context),
	} {
		if len(batch.Observations) != 0 || len(batch.UnavailableSpans) != 1 {
			t.Fatalf("invalid facts[%d] = %#v", index, batch)
		}
		if _, err := batch.Validate(); err != nil {
			t.Fatalf("unavailable batch does not validate: %v", err)
		}
	}
}

func TestParseLinuxCPU(t *testing.T) {
	tests := []struct {
		name string
		text string
		want CPUHostCounters
		ok   bool
	}{
		{"real proc stat", "cpu  100 20 30 400 50 6 7 8\ncpu0 1 2", CPUHostCounters{Busy: 171, Total: 621}, true},
		{"missing aggregate", "cpu0 1 2 3", CPUHostCounters{}, false},
		{"malformed", "cpu 1 nope 3", CPUHostCounters{}, false},
		{"zero", "cpu 0 0 0 0", CPUHostCounters{}, false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, ok := ParseLinuxCPU(test.text)
			if ok != test.ok || got != test.want {
				t.Fatalf("ParseLinuxCPU() = %#v, %t; want %#v, %t", got, ok, test.want, test.ok)
			}
		})
	}
}

func TestParseLinuxProcessStat(t *testing.T) {
	// The command name may contain ')' and proc stat offsets are relative to the
	// final closing parenthesis, not whitespace-separated fields from the start.
	stat := "123 (worker)part) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"
	got, ok := ParseLinuxProcessStat(stat, "/usr/bin/python3.12", 4096, 100)
	if !ok {
		t.Fatal("valid proc stat rejected")
	}
	want := ProcessCensusRow{PID: 123, Identity: "123:19", Binary: "python", CPUSeconds: 0.23, RSSBytes: 21 * 4096}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("row = %#v, want %#v", got, want)
	}
	for _, input := range []string{"", "123 missing", "123 (x) S 1 2"} {
		if _, ok := ParseLinuxProcessStat(input, "", 4096, 100); ok {
			t.Errorf("malformed stat accepted: %q", input)
		}
	}
}

func TestParseLinuxProcessStatDoesNotGuessUnknownClockTickRate(t *testing.T) {
	stat := "123 (worker) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"
	got, ok := ParseLinuxProcessStat(stat, "/usr/bin/python3.12", 4096, 0)
	if !ok {
		t.Fatal("valid synthetic proc stat was rejected when CPU tick units were unknown")
	}
	if got.CPUSeconds != 0 {
		t.Fatalf("unknown CPU tick rate produced %.3f CPU seconds, want no fabricated CPU value", got.CPUSeconds)
	}
	if got.RSSBytes != 21*4096 {
		t.Fatalf("unknown CPU tick rate suppressed independent RSS: got %d bytes", got.RSSBytes)
	}
}

func TestLinuxProcessCensusUsesInjectedBoundedInputs(t *testing.T) {
	files := map[string][]byte{"/proc/123/stat": []byte("123 (nodejs) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22")}
	inputs := HostCollectorInputs{
		ReadDir: func(name string) ([]string, error) {
			if name != "/proc" {
				t.Fatalf("unexpected directory %q", name)
			}
			return []string{"123", "self", "bad"}, nil
		},
		ReadFile: func(name string) ([]byte, error) { return files[name], nil },
		ReadLink: func(name string) (string, error) {
			if name != "/proc/123/exe" {
				t.Fatalf("unexpected link %q", name)
			}
			return "/usr/bin/nodejs", nil
		},
		PageSize: 4096, ClockTick: 100,
	}
	rows, ok := LinuxProcessCensus(inputs, "/proc")
	if !ok || len(rows) != 1 || rows[0].Binary != "node" {
		t.Fatalf("census = %#v, %t", rows, ok)
	}
}

func BenchmarkLinuxProcessCensusSynthetic(b *testing.B) {
	const processCount = 2000
	names := make([]string, 0, processCount)
	files := make(map[string][]byte, processCount)
	for pid := 1; pid <= processCount; pid++ {
		name := fmt.Sprintf("%d", pid)
		names = append(names, name)
		files["/proc/"+name+"/stat"] = []byte(fmt.Sprintf("%d (synthetic-worker) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22", pid))
	}
	inputs := HostCollectorInputs{
		ReadDir:  func(string) ([]string, error) { return names, nil },
		ReadFile: func(name string) ([]byte, error) { return files[name], nil },
		ReadLink: func(string) (string, error) { return "/usr/bin/synthetic-worker", nil },
		PageSize: 4096, ClockTick: 100, BinaryCache: make(map[string]string),
	}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		rows, ok := LinuxProcessCensus(inputs, "/proc")
		if !ok || len(rows) != processCount {
			b.Fatalf("synthetic census = %d rows, %t", len(rows), ok)
		}
	}
}

func TestLinuxHostSourcesKeepsProcessCounterRateWithSnapshot(t *testing.T) {
	files := map[string][]byte{
		"/proc/stat":     []byte("cpu  100 0 0 900 0\n"),
		"/proc/123/stat": []byte("123 (yolomux-go) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"),
	}
	sources := &LinuxHostSources{
		procRoot: "/proc", readFile: func(name string) ([]byte, error) { return files[name], nil },
		readDir:  func(string) ([]string, error) { return []string{"123"}, nil },
		readLink: func(string) (string, error) { return "/usr/bin/yolomux-go", nil },
		pageSize: 4096, clockTick: 250,
	}
	snapshot, err := sources.CPU(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if sources.ProcessTicksPerSecond() != 250 || snapshot.ProcessTicksPerSecond != 250 || snapshot.Processes["123:19"].CPU != 23 {
		t.Fatalf("snapshot = %#v, want 250 Hz and 23 ticks", snapshot)
	}
}

func TestLinuxHostSourcesKeepsHostCPUWhenClockTickRateIsUnavailable(t *testing.T) {
	sources := &LinuxHostSources{
		procRoot: "/synthetic-proc", clockTick: 0,
		readFile: func(name string) ([]byte, error) {
			if name != "/synthetic-proc/stat" {
				t.Fatalf("unexpected synthetic host read %q", name)
			}
			return []byte("cpu  100 0 0 900 0\n"), nil
		},
		readDir: func(string) ([]string, error) {
			t.Fatal("process census ran without a known clock tick rate")
			return nil, nil
		},
	}
	snapshot, err := sources.CPU(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Host != (CPUHostCounters{Busy: 100, Total: 1000}) || !snapshot.ProcessCensusIncomplete || len(snapshot.Processes) != 0 || snapshot.ProcessTicksPerSecond != 0 {
		t.Fatalf("unknown clock tick rate published an invalid sample: %#v", snapshot)
	}
}

func TestLinuxHostSourcesKeepsOwnedProcessWhenCensusIsTooLarge(t *testing.T) {
	const ownerPID = 4242
	ownerStat := []byte("4242 (yolomux-go) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22")
	sources := &LinuxHostSources{
		procRoot: "/proc", processPID: ownerPID,
		readFile: func(name string) ([]byte, error) {
			switch name {
			case "/proc/stat":
				return []byte("cpu  100 0 0 900 0\n"), nil
			case "/proc/4242/stat":
				return ownerStat, nil
			default:
				return nil, os.ErrNotExist
			}
		},
		readDir: func(string) ([]string, error) {
			// The oversized host list is synthetic; no host process table is read
			// by this test. The owner fallback must be the only retained row.
			return append([]string{"4242"}, make([]string, maxProcessEntries)...), nil
		},
		readLink: func(name string) (string, error) {
			if name != "/proc/4242/exe" {
				t.Fatalf("unexpected owner link %q", name)
			}
			return "/usr/bin/yolomux-go", nil
		},
		pageSize: 4096, clockTick: 100,
	}
	snapshot, err := sources.CPU(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Processes) != 1 {
		t.Fatalf("oversized census retained %d process rows, want only owner: %#v", len(snapshot.Processes), snapshot.Processes)
	}
	row, ok := snapshot.Processes["4242:19"]
	if !ok || row.Binary != "yolomux-go" || row.CPU != 23 {
		t.Fatalf("owned fallback row = %#v, want yolomux-go with 23 ticks", snapshot.Processes)
	}
	next := *cloneCPUSnapshot(snapshot)
	next.Host.Busy += 10
	next.Host.Total += 100
	process := next.Processes["4242:19"]
	process.CPU += 10
	next.Processes["4242:19"] = process
	sampler := NewCPUDeltaSampler("yolomux-go", nil, 1)
	sampler.processIdentity = "4242:19"
	sampler.Sample(snapshot, time.Second)
	sample := sampler.Sample(next, time.Second)
	facts := BuildCPUFacts(sample, sample.ProcessCPUPercent, hostFactContext())
	if got := facts.Observations[0].Payload["process_cpu_percent"]; got != nil {
		t.Fatalf("owner-only fallback was published as the complete host process CPU breakdown: %#v", got)
	}
	owned, ok := facts.Observations[0].Payload["owned_process_cpu_percent"].(map[string]any)
	ownedCPU, valueOK := owned["yolomux-go"].(float64)
	if !ok || !valueOK || math.Abs(ownedCPU-10) > 1e-9 {
		t.Fatalf("owned process CPU measurement = %#v, want yolomux-go=10%%", facts.Observations[0].Payload["owned_process_cpu_percent"])
	}
}

func TestLinuxHostSourcesMemoryKeepsOwnedProcessWhenCensusIsTooLarge(t *testing.T) {
	const ownerPID = 4242
	ownerStat := []byte("4242 (yolomux-go) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22")
	sources := &LinuxHostSources{
		procRoot: "/proc", processPID: ownerPID,
		readFile: func(name string) ([]byte, error) {
			switch name {
			case "/proc/meminfo":
				return []byte("MemTotal: 1024 kB\nMemAvailable: 512 kB\n"), nil
			case "/proc/4242/stat":
				return ownerStat, nil
			default:
				return nil, os.ErrNotExist
			}
		},
		readDir: func(string) ([]string, error) {
			return append([]string{"4242"}, make([]string, maxProcessEntries)...), nil
		},
		readLink: func(name string) (string, error) {
			if name != "/proc/4242/exe" {
				t.Fatalf("unexpected owner link %q", name)
			}
			return "/usr/bin/yolomux-go", nil
		},
		pageSize: 4096, clockTick: 100,
	}
	memory, rows, err := sources.Memory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if memory.CapacityBytes != 1024*1024 || memory.UsedBytes != 512*1024 || !memory.ProcessCensusIncomplete {
		t.Fatalf("memory counters = %+v", memory)
	}
	if len(rows) != 1 || rows[0].PID != ownerPID || rows[0].Binary != "yolomux-go" || rows[0].RSSBytes == 0 {
		t.Fatalf("oversized memory census rows = %#v, want the owned Go process", rows)
	}
	facts := BuildSystemMemoryFacts(memory, rows, nil, hostFactContext())
	if got := facts.Observations[0].Payload["process_memory_bytes"]; got != nil {
		t.Fatalf("owner-only fallback was published as the complete host process memory breakdown: %#v", got)
	}
}

func TestLinuxHostSourcesReplacesPartialCensusWhenOwnerRowIsMissing(t *testing.T) {
	const ownerPID = 4242
	ownerStat := []byte("4242 (yolomux-go) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22")
	ownerReads := 0
	sources := &LinuxHostSources{
		procRoot: "/proc", processPID: ownerPID,
		readFile: func(name string) ([]byte, error) {
			switch name {
			case "/proc/stat":
				return []byte("cpu  100 0 0 900 0\n"), nil
			case "/proc/4242/stat":
				ownerReads++
				if ownerReads == 1 {
					return nil, os.ErrNotExist
				}
				return ownerStat, nil
			case "/proc/99/stat":
				return []byte("99 (worker) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"), nil
			default:
				return nil, os.ErrNotExist
			}
		},
		readDir: func(string) ([]string, error) { return []string{"99", "4242"}, nil },
		readLink: func(name string) (string, error) {
			if name == "/proc/4242/exe" {
				return "/usr/bin/yolomux-go", nil
			}
			return "/usr/bin/worker", nil
		},
		pageSize: 4096, clockTick: 100,
	}
	snapshot, err := sources.CPU(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Processes) != 1 || ownerReads != 2 {
		t.Fatalf("partial census fallback = rows=%#v owner_reads=%d, want owner-only after retry", snapshot.Processes, ownerReads)
	}
	if _, ok := snapshot.Processes["4242:19"]; !ok {
		t.Fatalf("owner row missing after partial census fallback: %#v", snapshot.Processes)
	}
	if !snapshot.ProcessCensusIncomplete {
		t.Fatal("partial Linux census was marked complete after the owner fallback")
	}
}

func TestLinuxHostSourcesMarksPartialCensusIncompleteWhenOwnerWasFound(t *testing.T) {
	const ownerPID = 4242
	ownerStat := []byte("4242 (yolomux-go) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22")
	sources := &LinuxHostSources{
		procRoot: "/proc", processPID: ownerPID,
		readFile: func(name string) ([]byte, error) {
			switch name {
			case "/proc/stat":
				return []byte("cpu  100 0 0 900 0\n"), nil
			case "/proc/4242/stat":
				return ownerStat, nil
			case "/proc/99/stat":
				return []byte("malformed process row"), nil
			default:
				return nil, os.ErrNotExist
			}
		},
		readDir: func(string) ([]string, error) { return []string{"4242", "99"}, nil },
		readLink: func(name string) (string, error) {
			if name == "/proc/4242/exe" {
				return "/usr/bin/yolomux-go", nil
			}
			return "/usr/bin/worker", nil
		},
		pageSize: 4096, clockTick: 100,
	}
	snapshot, err := sources.CPU(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !snapshot.ProcessCensusIncomplete || len(snapshot.Processes) != 1 {
		t.Fatalf("partial census with owner present = incomplete:%t processes:%#v, want incomplete owner-only fallback", snapshot.ProcessCensusIncomplete, snapshot.Processes)
	}
}

func TestLinuxHostSourcesCachesStableProcessBinaryResolution(t *testing.T) {
	files := map[string][]byte{
		"/proc/stat":     []byte("cpu  100 0 0 900 0\n"),
		"/proc/123/stat": []byte("123 (yolomux-go) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"),
	}
	readLinks := 0
	sources := &LinuxHostSources{
		procRoot: "/proc", readFile: func(name string) ([]byte, error) { return files[name], nil },
		readDir:  func(string) ([]string, error) { return []string{"123"}, nil },
		readLink: func(string) (string, error) { readLinks++; return "/usr/bin/yolomux-go", nil },
		pageSize: 4096, clockTick: 100,
	}
	if _, err := sources.CPU(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := sources.CPU(context.Background()); err != nil {
		t.Fatal(err)
	}
	if readLinks != 1 {
		t.Fatalf("stable process binary was resolved %d times, want once", readLinks)
	}
}

func TestLinuxHostSourcesSharesOverlappingProcessCensusBetweenFamilies(t *testing.T) {
	files := map[string][]byte{
		"/proc/stat":     []byte("cpu  100 0 0 900 0\n"),
		"/proc/meminfo":  []byte("MemTotal: 1024 kB\nMemAvailable: 512 kB\n"),
		"/proc/123/stat": []byte("123 (yolomux-go) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"),
	}
	readDirs := 0
	sources := &LinuxHostSources{
		procRoot: "/proc", readFile: func(name string) ([]byte, error) { return files[name], nil },
		readDir:  func(string) ([]string, error) { readDirs++; return []string{"123"}, nil },
		readLink: func(string) (string, error) { return "/usr/bin/yolomux-go", nil },
		pageSize: 4096, clockTick: 100,
	}
	if _, err := sources.CPU(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, _, err := sources.Memory(context.Background()); err != nil {
		t.Fatal(err)
	}
	if readDirs != 1 {
		t.Fatalf("overlapping CPU/memory census reads = %d, want one shared read", readDirs)
	}
}

func TestLinuxProcessCensusPreservesIdentityAndMemoryRows(t *testing.T) {
	files := map[string][]byte{
		"/proc/123/stat": []byte("123 (python) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"),
		"/proc/456/stat": []byte("456 (python) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"),
	}
	inputs := HostCollectorInputs{
		ReadDir:  func(string) ([]string, error) { return []string{"123", "456"}, nil },
		ReadFile: func(name string) ([]byte, error) { return files[name], nil },
		ReadLink: func(name string) (string, error) { return "/usr/bin/python3", nil },
		PageSize: 4096, ClockTick: 100,
	}
	rows, ok := LinuxProcessCensus(inputs, "/proc")
	if !ok || len(rows) != 2 || rows[0].Identity == rows[1].Identity {
		t.Fatalf("census identities = %#v, %t", rows, ok)
	}
	batch := BuildSystemMemoryFacts(MemoryCounters{UsedBytes: 1, CapacityBytes: 2}, rows, nil, hostFactContext())
	series := batch.Observations[0].Payload["process_memory_bytes"].(map[string]any)
	if !reflect.DeepEqual(series, map[string]any{"python": uint64(2 * 21 * 4096)}) {
		t.Fatalf("memory series = %#v, want aggregated binary entry", series)
	}
}

func TestBuildSystemMemoryFactsAggregatesAndBoundsProcessMemory(t *testing.T) {
	rows := []ProcessCensusRow{
		{Identity: "a", Binary: "/usr/bin/python3.12", RSSBytes: 40},
		{Identity: "b", Binary: "/venv/bin/python", RSSBytes: 30},
		{Identity: "c", Binary: "zeta", RSSBytes: 20},
		{Identity: "d", Binary: "alpha", RSSBytes: 20},
		{Identity: "e", Binary: "beta", RSSBytes: 10},
		{Identity: "f", Binary: "gamma", RSSBytes: 9},
		{Identity: "g", Binary: "delta", RSSBytes: 8},
		{Identity: "h", Binary: "epsilon", RSSBytes: 7},
	}

	batch := BuildSystemMemoryFacts(MemoryCounters{UsedBytes: 123, CapacityBytes: 456}, rows, nil, hostFactContext())
	want := map[string]any{
		"used_bytes": uint64(123), "capacity_bytes": uint64(456),
		"process_memory_bytes": map[string]any{
			"python": uint64(70), "alpha": uint64(20), "zeta": uint64(20), "beta": uint64(10), "gamma": uint64(9),
		},
	}
	if got := batch.Observations[0].Payload; !reflect.DeepEqual(got, want) {
		t.Fatalf("payload = %#v, want %#v", got, want)
	}
	ordered := topProcessMemory(rows)
	for index, binary := range []string{"python", "alpha", "zeta", "beta", "gamma"} {
		if ordered[index].Binary != binary {
			t.Fatalf("rank[%d] = %q, want %q", index, ordered[index].Binary, binary)
		}
	}
	if len(ordered) != 5 {
		t.Fatalf("ranked process memory length = %d, want five", len(ordered))
	}
}

func TestBuildSystemMemoryFactsKeepsYoloMuxGoInstancesSeparate(t *testing.T) {
	rows := []ProcessCensusRow{
		{PID: 101, Identity: "101:10", Binary: "yolomux_go", RSSBytes: 400},
		{PID: 202, Identity: "202:20", Binary: "yolomux_go", RSSBytes: 600},
	}
	batch := BuildSystemMemoryFacts(MemoryCounters{UsedBytes: 1000, CapacityBytes: 2000}, rows, nil, hostFactContext())
	series := batch.Observations[0].Payload["process_memory_bytes"].(map[string]any)
	want := map[string]any{
		hostProcessInstanceSeriesKey("yolomux_go", "101:10"): uint64(400),
		hostProcessInstanceSeriesKey("yolomux_go", "202:20"): uint64(600),
	}
	if !reflect.DeepEqual(series, want) {
		t.Fatalf("Go instance memory series = %#v, want %#v", series, want)
	}
}

func TestNormalizeHostBinaryStripsDeletedSuffixCaseInsensitively(t *testing.T) {
	for _, value := range []string{"/usr/bin/nodejs (deleted)", "/usr/bin/nodejs (DELETED)"} {
		if got := normalizeHostBinary(value); got != "node" {
			t.Errorf("normalizeHostBinary(%q) = %q, want node", value, got)
		}
	}
}

func TestParseLinuxMeminfo(t *testing.T) {
	got, ok := ParseLinuxMeminfo("MemTotal:       16384 kB\nMemFree: 2048 kB\nMemAvailable: 4096 kB\nBuffers: 1 kB\n")
	want := MemoryCounters{CapacityBytes: 16384 * 1024, UsedBytes: 12288 * 1024}
	if !ok || got != want {
		t.Fatalf("memory = %#v, %t; want %#v, true", got, ok, want)
	}
	if _, ok := ParseLinuxMeminfo("MemTotal: 4 kB\nMemAvailable: 9 kB"); ok {
		t.Fatal("memory over-capacity accepted")
	}
}

func TestLinuxGPUDevicesReadGenericSysfsMetrics(t *testing.T) {
	files := map[string]string{
		"/sys/class/drm/card0/device/gpu_busy_percent":    "75\n",
		"/sys/class/drm/card0/device/mem_info_vram_used":  "4194304000\n",
		"/sys/class/drm/card0/device/mem_info_vram_total": "8388608000\n",
	}
	inputs := HostCollectorInputs{
		ReadDir: func(path string) ([]string, error) {
			if path != "/sys/class/drm" {
				t.Fatalf("unexpected directory %q", path)
			}
			return []string{"card0", "card1-DP-1", "renderD128"}, nil
		},
		ReadFile: func(path string) ([]byte, error) {
			value, ok := files[path]
			if !ok {
				return nil, os.ErrNotExist
			}
			return []byte(value), nil
		},
	}
	got, ok := LinuxGPUDevices(inputs, "/sys/class/drm")
	if !ok || len(got) != 1 {
		t.Fatalf("devices = %#v, %t", got, ok)
	}
	want := GPUDevice{SourceID: "gpu:0", Label: "GPU 0", Utilization: 75, MemoryUsedBytes: 4194304000, MemoryTotalBytes: 8388608000}
	if got[0] != want {
		t.Fatalf("device = %#v, want %#v", got[0], want)
	}
}

func TestLinuxGPUDevicesLeaveMissingMetricsUnavailable(t *testing.T) {
	files := map[string][]byte{"/sys/class/drm/card0/device/gpu_busy_percent": []byte("12\n")}
	inputs := HostCollectorInputs{
		ReadDir: func(string) ([]string, error) { return []string{"card0"}, nil },
		ReadFile: func(path string) ([]byte, error) {
			value, ok := files[path]
			if !ok {
				return nil, os.ErrNotExist
			}
			return value, nil
		},
	}
	if got, ok := LinuxGPUDevices(inputs, "/sys/class/drm"); ok || len(got) != 0 {
		t.Fatalf("devices = %#v, %t; want unavailable", got, ok)
	}
}

func TestParseMacOSInputs(t *testing.T) {
	ioreg := `[{"PerformanceStatistics":{"GPU Activity(%)":"8","In use video memory":12}}]`
	devices := ParseIOAcceleratorJSON([]byte(ioreg))
	if len(devices) != 1 || devices[0].SourceID != "gpu:0" || devices[0].Utilization != 8 || devices[0].MemoryUsedBytes != 12 {
		t.Fatalf("ioreg = %#v", devices)
	}
	metadata := ParseSystemProfilerMetadata([]byte(`{"SPHardwareDataType":[{"chip_type":"Apple M3","number_processors":"12 (6 performance and 6 efficiency)"}],"SPMemoryDataType":[{"dimm_type":"LPDDR5"}],"SPDisplaysDataType":[{"sppci_model":"Apple GPU"}]}`))
	want := SystemMetadata{CPULabel: "Apple M3 · 12 cores (6 performance + 6 efficiency)", GPULabel: "Apple GPU", SystemMemoryLabel: "LPDDR5 unified memory"}
	if metadata != want {
		t.Fatalf("metadata = %#v, want %#v", metadata, want)
	}
	plist := []byte(`<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><array><dict><key>PerformanceStatistics</key><dict><key>Device Utilization %</key><real>12.5</real><key>In use video memory</key><integer>99</integer></dict></dict></array></plist>`)
	devices = ParseIOAccelerator(plist)
	if len(devices) != 1 || devices[0].Utilization != 12.5 || devices[0].MemoryUsedBytes != 99 {
		t.Fatalf("plist ioreg = %#v", devices)
	}
	if len(ParseIOAccelerator([]byte(`[ {"PerformanceStatistics":{"GPU Activity(%)": "NaN"}} ]`))) != 0 {
		t.Fatal("non-finite GPU accepted")
	}
}

func TestParserBoundsAndPythonIdentity(t *testing.T) {
	stat := "7 (x) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"
	if _, ok := ParseLinuxProcessStat(stat, "/bin/x", ^uint64(0), 100); ok {
		t.Fatal("RSS overflow accepted")
	}
	if pythonFloatHex(math.Copysign(0, -1)) != "-0x0.0p+0" {
		t.Fatal("negative zero was normalized")
	}
}
