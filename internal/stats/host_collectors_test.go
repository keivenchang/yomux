package stats

import (
	"context"
	"encoding/json"
	"math"
	"reflect"
	"testing"
)

func hostFactContext() HostFactContext {
	return HostFactContext{EpochID: "epoch-1", EpochStartedAt: 100, ObservedAt: 101, CadenceSeconds: 1, OwnerGeneration: 7, SourceID: "host"}
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
	}, map[string]float64{"python": 8.5, "node": 2.25}, hostFactContext())
	series, ok := batch.Observations[0].Payload["process_cpu_percent"].(map[string]any)
	if !ok || !reflect.DeepEqual(series, map[string]any{"python": 8.5, "node": 2.25}) {
		t.Fatalf("series = %#v", batch.Observations[0].Payload["process_cpu_percent"])
	}
}

func hostMeasurement(value float64) CPUMeasurement {
	return CPUMeasurement{Value: value, Valid: true}
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
	if snapshot.ProcessTicksPerSecond != 250 || snapshot.Processes["123:19"].CPU != 23 {
		t.Fatalf("snapshot = %#v, want 250 Hz and 23 ticks", snapshot)
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

func TestParseGPUCSV(t *testing.T) {
	got := ParseGPUCSV("0, GPU Model B, 120, -2, 4096\nmalformed\n1, , 4, 5, 6\n2, bad, nope, 1, 2\n")
	if len(got) != 2 {
		t.Fatalf("devices = %#v", got)
	}
	if got[0].SourceID != "gpu:0" || got[0].Label != "GPU 0 (GPU Model B)" || got[0].Utilization != 100 || got[0].MemoryUsedBytes != 0 || got[0].MemoryTotalBytes != 4096*1024*1024 {
		t.Fatalf("first device = %#v", got[0])
	}
	if got[1].Label != "GPU 1" {
		t.Fatalf("empty-name label = %#v", got[1])
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
