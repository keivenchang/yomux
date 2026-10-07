package stats

import (
	"context"
	"errors"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type capturedHostSources struct {
	mu       sync.Mutex
	cpu      []CPUSnapshot
	cpuErrs  []error
	memory   []MemoryCounters
	gpu      [][]GPUDevice
	gpuError error
}

type topologyHostSources struct {
	*capturedHostSources
	logicalCPUs int
}

type capturedDiskHostSources struct {
	*capturedHostSources
	diskSamples []DiskUsageSample
	diskErr     error
	diskCalls   int
}

func (s *capturedDiskHostSources) Disk(context.Context) ([]DiskUsageSample, error) {
	s.diskCalls++
	return append([]DiskUsageSample(nil), s.diskSamples...), s.diskErr
}

func (s topologyHostSources) SystemLogicalCPUs() int { return s.logicalCPUs }

func (s *capturedHostSources) CPU(context.Context) (CPUSnapshot, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.cpuErrs) > 0 {
		err := s.cpuErrs[0]
		s.cpuErrs = s.cpuErrs[1:]
		if err != nil {
			return CPUSnapshot{}, err
		}
	}
	item := s.cpu[0]
	s.cpu = s.cpu[1:]
	return item, nil
}
func (s *capturedHostSources) Memory(context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	item := s.memory[0]
	s.memory = s.memory[1:]
	return item, []ProcessCensusRow{{Identity: "pid-1", Binary: "python", RSSBytes: 4096}}, nil
}
func (s *capturedHostSources) GPU(context.Context) ([]GPUDevice, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.gpuError != nil {
		return nil, s.gpuError
	}
	item := s.gpu[0]
	s.gpu = s.gpu[1:]
	return item, nil
}

type collectorClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *collectorClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}
func (c *collectorClock) NewTimer(time.Duration) HostCollectorTimer {
	return &neverTimer{ch: make(chan time.Time)}
}
func (c *collectorClock) advance(d time.Duration) {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.mu.Unlock()
}

type neverTimer struct{ ch chan time.Time }

func (t *neverTimer) C() <-chan time.Time { return t.ch }
func (t *neverTimer) Stop() bool          { return true }

func snapshot(busy, total, process uint64) CPUSnapshot {
	return CPUSnapshot{Host: CPUHostCounters{Busy: busy, Total: total}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{"python": {Identity: "pid-1", CPU: process}}}
}

func TestHostCollectorRunOnceWritesCapturedFactsAndUnavailable(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "host.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &collectorClock{now: time.Unix(100, 0)}
	sources := &capturedHostSources{
		cpu:    []CPUSnapshot{snapshot(50, 100, 10), snapshot(70, 140, 25), snapshot(90, 180, 40)},
		memory: []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}, {UsedBytes: 10, CapacityBytes: 16}},
		gpu:    [][]GPUDevice{{{SourceID: "gpu:0", Label: "Synthetic GPU", Utilization: 20, MemoryUsedBytes: 1, MemoryTotalBytes: 2}}, nil, {{SourceID: "gpu:0", Label: "Synthetic GPU", Utilization: 30, MemoryUsedBytes: 1, MemoryTotalBytes: 2}}},
	}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{ActiveCadence: time.Second, SourceTimeout: time.Second, EpochID: "epoch", EpochStartedAt: 100, OwnerGeneration: 4, SourceID: "host", ProcessKey: "python"})
	collector.RunOnce(ctx)
	clock.advance(time.Second)
	collector.RunOnce(ctx)
	clock.advance(time.Second)
	collector.RunOnce(ctx)

	counters := collector.Counters()
	if counters.Cycles != 3 || counters.AcceptedObservations != 7 || counters.UnavailableSpans != 2 || counters.Errors != 0 {
		t.Fatalf("counters = %+v", counters)
	}
	if counters.LastCPUProcessRows != 1 || counters.LastMemoryProcessRows != 1 {
		t.Fatalf("process census counters = %+v", counters)
	}
	if timing := counters.Family["cpu"]; timing.Runs != 3 || timing.SourceDurationTotal <= 0 || timing.LastSourceDuration <= 0 {
		t.Fatalf("CPU source timing = %+v", timing)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	observations, err := reader.ReadObservations(ctx, 0, 1000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(observations) != 7 {
		t.Fatalf("observations = %d, want 7", len(observations))
	}
	unavailable, err := reader.ReadUnavailableSpans(ctx, 0, 1000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(unavailable) != 2 || unavailable[0].Family != "cpu" || unavailable[1].Family != "gpu" {
		t.Fatalf("unavailable = %+v", unavailable)
	}
	seen := map[string]int{}
	for _, observation := range observations {
		seen[observation.Family]++
	}
	if seen["cpu"] != 2 || seen["system_memory"] != 3 || seen["gpu"] != 2 {
		t.Fatalf("family rows = %#v", seen)
	}
}

func TestHostCollectorCollectsDiskOnFiveMinuteCadenceWithPerMountIdentity(t *testing.T) {
	sources := &capturedDiskHostSources{
		capturedHostSources: &capturedHostSources{
			cpu:    []CPUSnapshot{snapshot(50, 100, 10)},
			memory: []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}},
			gpu:    [][]GPUDevice{{{SourceID: "gpu:0", Label: "Synthetic GPU", Utilization: 5}}},
		},
		diskSamples: []DiskUsageSample{
			{SourceID: "mount:/", Mountpoint: "/", UsedBytes: 25, CapacityBytes: 100},
			{SourceID: "mount:/tmp", Mountpoint: "/tmp", UsedBytes: 0, CapacityBytes: 100},
		},
	}
	writer := &recordingBatchWriter{}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{
		ActiveCadence: time.Second, MemoryCadence: time.Minute, SourceTimeout: time.Second,
		EpochID: "disk-cadence", EpochStartedAt: 100, OwnerGeneration: 1, SourceID: "host", ProcessKey: "python",
	})
	if got := collector.FamilyCadence("system_disk"); got != 5*time.Minute {
		t.Fatalf("disk family cadence = %s, want five minutes", got)
	}
	collector.RunOnce(context.Background())
	var diskObservations []Observation
	for _, batch := range writer.snapshot() {
		for _, observation := range batch.Observations {
			if observation.Family == "system_disk" {
				diskObservations = append(diskObservations, observation)
			}
		}
	}
	if sources.diskCalls != 1 || len(diskObservations) != 2 {
		t.Fatalf("disk collection calls=%d observations=%+v", sources.diskCalls, diskObservations)
	}
	if diskObservations[0].SourceID != "mount:/" || diskObservations[1].SourceID != "mount:/tmp" || diskObservations[0].Payload["used_percent"] != 25.0 || diskObservations[1].Payload["used_percent"] != 0.0 {
		t.Fatalf("disk observations lost distinct mounts or valid zero: %+v", diskObservations)
	}
}

func TestHostCollectorWritesSyntheticTopProcessCPUUsers(t *testing.T) {
	sources := &capturedHostSources{
		cpu: []CPUSnapshot{
			{
				Host:                  CPUHostCounters{Busy: 100, Total: 200},
				ProcessTicksPerSecond: 100,
				Processes: map[string]CPUProcessCounters{
					"go:1":     {Identity: "go:1", CPU: 100, Binary: "yolomux-go"},
					"python:1": {Identity: "python:1", CPU: 50, Binary: "python"},
				},
			},
			{
				Host:                  CPUHostCounters{Busy: 140, Total: 240},
				ProcessTicksPerSecond: 100,
				Processes: map[string]CPUProcessCounters{
					"go:1":     {Identity: "go:1", CPU: 120, Binary: "yolomux-go"},
					"python:1": {Identity: "python:1", CPU: 60, Binary: "python"},
				},
			},
		},
		memory: []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}},
		gpu:    [][]GPUDevice{{{SourceID: "gpu:0", Label: "synthetic"}}, {{SourceID: "gpu:0", Label: "synthetic"}}},
	}
	writer := &recordingBatchWriter{}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{
		ActiveCadence: time.Second, SourceTimeout: time.Second, EpochID: "synthetic-top-cpu", EpochStartedAt: 100,
		OwnerGeneration: 7, SourceID: "host", ProcessKey: "yolomux-go", LogicalCPUs: 1,
	})
	collector.RunOnce(context.Background())
	clock.advance(time.Second)
	collector.RunOnce(context.Background())

	var cpuPayload map[string]any
	for _, batch := range writer.snapshot() {
		for _, observation := range batch.Observations {
			if observation.Family == "cpu" {
				cpuPayload = observation.Payload
			}
		}
	}
	if cpuPayload == nil {
		t.Fatal("synthetic collector emitted no CPU observation")
	}
	processes, ok := cpuPayload["process_cpu_percent"].(map[string]any)
	if !ok {
		t.Fatalf("synthetic top CPU series = %#v", cpuPayload["process_cpu_percent"])
	}
	if processes["yolomux-go"] != 20.0 || processes["python"] != 10.0 {
		t.Fatalf("synthetic top CPU series = %#v, want yolomux-go=20 and python=10", processes)
	}
}

func TestHostCollectorUsesSourceSystemLogicalCPUCount(t *testing.T) {
	sources := topologyHostSources{
		logicalCPUs: 8,
		capturedHostSources: &capturedHostSources{
			cpu: []CPUSnapshot{
				{Host: CPUHostCounters{Busy: 100, Total: 200}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{
					"python:1": {Identity: "python:1", CPU: 100, Binary: "python"},
				}},
				{Host: CPUHostCounters{Busy: 140, Total: 240}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{
					"python:1": {Identity: "python:1", CPU: 200, Binary: "python"},
				}},
			},
			memory: []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}},
			gpu:    [][]GPUDevice{{}, {}},
		},
	}
	writer := &recordingBatchWriter{}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{
		ActiveCadence: time.Second, SourceTimeout: time.Second, EpochID: "system-cpu-count", EpochStartedAt: 100,
		OwnerGeneration: 7, SourceID: "host", ProcessKey: "python",
	})
	collector.RunOnce(context.Background())
	clock.advance(time.Second)
	collector.RunOnce(context.Background())

	for _, batch := range writer.snapshot() {
		for _, observation := range batch.Observations {
			if observation.Family != "cpu" {
				continue
			}
			processes, ok := observation.Payload["process_cpu_percent"].(map[string]any)
			if !ok || processes["python"] != 12.5 {
				t.Fatalf("synthetic grouped CPU = %#v, want python=12.5%% for 8 system CPUs", observation.Payload["process_cpu_percent"])
			}
			return
		}
	}
	t.Fatal("collector emitted no CPU observation")
}

func TestHostCollectorOmitsGroupedCPUWhenTopologyIsUnavailable(t *testing.T) {
	sources := &capturedHostSources{
		cpu: []CPUSnapshot{
			{Host: CPUHostCounters{Busy: 100, Total: 200}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{
				"python:1": {Identity: "python:1", CPU: 100, Binary: "python"},
			}},
			{Host: CPUHostCounters{Busy: 140, Total: 240}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{
				"python:1": {Identity: "python:1", CPU: 200, Binary: "python"},
			}},
		},
		memory: []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}},
		gpu:    [][]GPUDevice{{}, {}},
	}
	writer := &recordingBatchWriter{}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{
		ActiveCadence: time.Second, SourceTimeout: time.Second, EpochID: "unknown-system-cpus", EpochStartedAt: 100,
		OwnerGeneration: 7, SourceID: "host", ProcessKey: "python",
	})
	collector.RunOnce(context.Background())
	clock.advance(time.Second)
	collector.RunOnce(context.Background())

	for _, batch := range writer.snapshot() {
		for _, observation := range batch.Observations {
			if observation.Family == "cpu" {
				if _, ok := observation.Payload["process_cpu_percent"]; ok {
					t.Fatalf("unknown system topology published grouped CPU: %#v", observation.Payload)
				}
				return
			}
		}
	}
	t.Fatal("collector emitted no CPU observation")
}

func TestHostCollectorLatestTelemetryIsMeasuredAndDefensive(t *testing.T) {
	sources := &capturedHostSources{
		cpu: []CPUSnapshot{
			{Host: CPUHostCounters{Busy: 50, Total: 100}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{"python": {Identity: "pid-1", CPU: 10, Binary: "python"}}},
			{Host: CPUHostCounters{Busy: 70, Total: 140}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{"python": {Identity: "pid-1", CPU: 25, Binary: "python"}}},
		},
		memory: []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}},
		gpu:    [][]GPUDevice{{}, {}},
	}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, countingWriter{}, clock, HostCollectorConfig{
		ActiveCadence: time.Second, MemoryCadence: time.Minute, SourceTimeout: time.Second,
		EpochID: "latest", EpochStartedAt: 100, SourceID: "host", ProcessKey: "python", LogicalCPUs: 1,
	})
	collector.RunOnce(context.Background())
	clock.advance(time.Second)
	collector.RunOnce(context.Background())

	latest := collector.Latest()
	if !latest.CPUValid || latest.CPUPercent != 15 || latest.SystemCPUPercent != 50 {
		t.Fatalf("latest CPU telemetry = %+v", latest)
	}
	if !latest.MemoryValid || latest.MemoryUsedBytes != 9 || latest.MemoryCapacityBytes != 16 {
		t.Fatalf("latest memory telemetry = %+v", latest)
	}
	processCPU := latest.ProcessCPUPercent["python"]
	if processCPU <= 0 || latest.ProcessMemoryBytes["python"] != 4096 {
		t.Fatalf("latest process telemetry = %+v", latest)
	}
	latest.ProcessCPUPercent["python"] = 0
	latest.ProcessMemoryBytes["python"] = 0
	again := collector.Latest()
	if again.ProcessCPUPercent["python"] != processCPU || again.ProcessMemoryBytes["python"] != 4096 {
		t.Fatalf("latest telemetry was not defensive: %+v", again)
	}
}

func TestHostCollectorLatestCPUInvalidatesAfterSourceError(t *testing.T) {
	sources := &capturedHostSources{
		cpu: []CPUSnapshot{
			{Host: CPUHostCounters{Busy: 50, Total: 100}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{"python": {Identity: "pid-1", CPU: 10, Binary: "python"}}},
			{Host: CPUHostCounters{Busy: 70, Total: 140}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{"python": {Identity: "pid-1", CPU: 25, Binary: "python"}}},
		},
		cpuErrs: []error{nil, nil, errors.New("synthetic CPU source failure")},
		memory:  []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}, {UsedBytes: 10, CapacityBytes: 16}},
		gpu:     [][]GPUDevice{{}, {}, {}},
	}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, countingWriter{}, clock, HostCollectorConfig{
		ActiveCadence: time.Second, SourceTimeout: time.Second, EpochID: "cpu-error", EpochStartedAt: 100,
		SourceID: "host", ProcessKey: "python", LogicalCPUs: 1,
	})
	collector.RunOnce(context.Background())
	clock.advance(time.Second)
	collector.RunOnce(context.Background())
	latest := collector.Latest()
	if !latest.CPUValid || latest.CPUPercent != 15 || latest.SystemCPUPercent != 50 || latest.ProcessCPUPercent["python"] <= 0 {
		t.Fatalf("valid CPU telemetry before failure = %+v", latest)
	}
	clock.advance(time.Second)
	collector.RunOnce(context.Background())
	latest = collector.Latest()
	if latest.CPUValid || latest.CPUReason != "source_error" || latest.CPUObservedAt != 102 || latest.ProcessCPUPercent != nil {
		t.Fatalf("CPU telemetry remained stale after source failure = %+v", latest)
	}
}

func TestHostCollectorSeparatesOwnedCPUFromSameNamedHostUsers(t *testing.T) {
	sources := &capturedHostSources{
		cpu: []CPUSnapshot{
			{Host: CPUHostCounters{Busy: 100, Total: 200}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{
				"101:10": {Identity: "101:10", CPU: 100, Binary: "yolomux-go"},
				"202:20": {Identity: "202:20", CPU: 200, Binary: "yolomux-go"},
				"303:30": {Identity: "303:30", CPU: 50, Binary: "python"},
			}},
			{Host: CPUHostCounters{Busy: 140, Total: 240}, ProcessTicksPerSecond: 100, Processes: map[string]CPUProcessCounters{
				"101:10": {Identity: "101:10", CPU: 110, Binary: "yolomux-go"},
				"202:20": {Identity: "202:20", CPU: 260, Binary: "yolomux-go"},
				"303:30": {Identity: "303:30", CPU: 70, Binary: "python"},
			}},
		},
		memory: []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}},
		gpu:    [][]GPUDevice{{{SourceID: "gpu:0", Label: "synthetic"}}, {{SourceID: "gpu:0", Label: "synthetic"}}},
	}
	writer := &recordingBatchWriter{}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{
		ActiveCadence: time.Second, SourceTimeout: time.Second, EpochID: "synthetic-owned-cpu", EpochStartedAt: 100,
		OwnerGeneration: 7, SourceID: "host", ProcessKey: "yolomux-go", ProcessIdentity: "101:", LogicalCPUs: 1,
	})
	collector.RunOnce(context.Background())
	clock.advance(time.Second)
	collector.RunOnce(context.Background())

	var cpuPayload map[string]any
	for _, batch := range writer.snapshot() {
		for _, observation := range batch.Observations {
			if observation.Family == "cpu" {
				cpuPayload = observation.Payload
			}
		}
	}
	if cpuPayload == nil {
		t.Fatal("synthetic collector emitted no CPU observation")
	}
	hostUsers, ok := cpuPayload["process_cpu_percent"].(map[string]any)
	if !ok || hostUsers[hostProcessInstanceSeriesKey("yolomux-go", "101:10")] != 10.0 || hostUsers[hostProcessInstanceSeriesKey("yolomux-go", "202:20")] != 60.0 || hostUsers["python"] != 20.0 {
		t.Fatalf("host CPU users = %#v, want separate Go instance series 101=10 and 202=60, plus python=20", cpuPayload["process_cpu_percent"])
	}
	owned, ok := cpuPayload["owned_process_cpu_percent"].(map[string]any)
	if !ok || owned["yolomux-go"] != 10.0 {
		t.Fatalf("owned CPU users = %#v, want yolomux-go=10", cpuPayload["owned_process_cpu_percent"])
	}
}

func TestHostCollectorKeepsOwnedMeasurementsWhenProcessCensusIsIncomplete(t *testing.T) {
	sources := &capturedHostSources{
		cpu: []CPUSnapshot{
			{Host: CPUHostCounters{Busy: 100, Total: 200}, ProcessTicksPerSecond: 100, ProcessCensusIncomplete: true, Processes: map[string]CPUProcessCounters{
				"101:10": {Identity: "101:10", CPU: 100, Binary: "yolomux-go"},
			}},
			{Host: CPUHostCounters{Busy: 120, Total: 300}, ProcessTicksPerSecond: 100, ProcessCensusIncomplete: true, Processes: map[string]CPUProcessCounters{
				"101:10": {Identity: "101:10", CPU: 110, Binary: "yolomux-go"},
			}},
		},
		memory: []MemoryCounters{
			{UsedBytes: 8, CapacityBytes: 16, ProcessCensusIncomplete: true},
			{UsedBytes: 9, CapacityBytes: 16, ProcessCensusIncomplete: true},
		},
		gpu: [][]GPUDevice{{}, {}},
	}
	writer := &recordingBatchWriter{}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{
		ActiveCadence: time.Second, SourceTimeout: time.Second, EpochID: "partial-census", EpochStartedAt: 100,
		OwnerGeneration: 7, SourceID: "host", ProcessKey: "yolomux-go", ProcessIdentity: "101:", LogicalCPUs: 1,
	})
	collector.RunOnce(context.Background())
	clock.advance(time.Second)
	collector.RunOnce(context.Background())

	var cpuPayload, memoryPayload map[string]any
	for _, batch := range writer.snapshot() {
		for _, observation := range batch.Observations {
			switch observation.Family {
			case "cpu":
				cpuPayload = observation.Payload
			case "system_memory":
				memoryPayload = observation.Payload
			}
		}
	}
	if cpuPayload == nil || memoryPayload == nil {
		t.Fatalf("partial census erased host measurements: cpu=%#v memory=%#v", cpuPayload, memoryPayload)
	}
	if _, ok := cpuPayload["process_cpu_percent"]; ok {
		t.Fatalf("partial CPU census published a host-wide process map: %#v", cpuPayload)
	}
	owned, ok := cpuPayload["owned_process_cpu_percent"].(map[string]any)
	if !ok || owned["yolomux-go"] != 10.0 {
		t.Fatalf("partial census lost the owned Go CPU reading: %#v", cpuPayload)
	}
	if _, ok := memoryPayload["process_memory_bytes"]; ok {
		t.Fatalf("partial memory census published a host-wide process map: %#v", memoryPayload)
	}
	latest := collector.Latest()
	if !latest.CPUValid || latest.ProcessCPUPercent != nil || !latest.MemoryValid || latest.ProcessMemoryBytes != nil {
		t.Fatalf("latest telemetry exposed incomplete process groups: %+v", latest)
	}
}

func TestHostCollectorSourceDeadlineAndStop(t *testing.T) {
	started := make(chan struct{})
	sources := blockingHostSources{started: started}
	collector := NewHostCollector(sources, countingWriter{}, nil, HostCollectorConfig{ActiveCadence: time.Hour, SourceTimeout: time.Millisecond})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := collector.Start(ctx); err != nil {
		t.Fatal(err)
	}
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("collector did not start source")
	}
	collector.Stop()
	if collector.Counters().Cycles != 1 || collector.Counters().UnavailableSpans == 0 || collector.Counters().Errors == 0 {
		t.Fatalf("deadline counters = %+v", collector.Counters())
	}
}

func TestHostCollectorLifecyclePersistsMultipleCycles(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "lifecycle.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	sources := &capturedHostSources{
		cpu:    []CPUSnapshot{snapshot(50, 100, 10), snapshot(70, 140, 25)},
		memory: []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}},
		gpu:    [][]GPUDevice{{{SourceID: "gpu:0", Label: "Synthetic GPU"}}, {{SourceID: "gpu:0", Label: "Synthetic GPU"}}},
	}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{
		ActiveCadence: time.Millisecond, SourceTimeout: time.Second, EpochID: "lifecycle", EpochStartedAt: 100,
		SourceID: "host", ProcessKey: "python", LogicalCPUs: 1, OwnerGeneration: 12,
	})
	collector.RunOnce(ctx)
	clock.advance(time.Second)
	collector.RunOnce(ctx)
	if counters := collector.Counters(); counters.Cycles != 2 || counters.AcceptedObservations != 5 || counters.UnavailableSpans != 1 || counters.Errors != 0 {
		t.Fatalf("counters = %+v", counters)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	observations, err := reader.ReadObservations(ctx, 0, 1000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(observations) != 5 {
		t.Fatalf("persisted observations = %d, want 5", len(observations))
	}
	seen := map[string]int{}
	for _, observation := range observations {
		seen[observation.Family]++
		if observation.OwnerGeneration != 12 {
			t.Fatalf("observation generation = %d, want 12", observation.OwnerGeneration)
		}
	}
	if seen["cpu"] != 1 || seen["system_memory"] != 2 || seen["gpu"] != 2 {
		t.Fatalf("family rows = %#v", seen)
	}
}

func TestHostCollectorDefaultMemoryCadenceMatchesPythonContract(t *testing.T) {
	config := (HostCollectorConfig{}).withDefaults()
	if config.MemoryCadence != time.Minute {
		t.Fatalf("memory cadence = %s, want 1m", config.MemoryCadence)
	}
	if config.MemoryCadence != DefaultHostMemoryCadence {
		t.Fatalf("default memory cadence drifted: %s != %s", config.MemoryCadence, DefaultHostMemoryCadence)
	}
}

func TestHostCollectorDefaultDiskCadenceIsFiveMinutes(t *testing.T) {
	config := (HostCollectorConfig{}).withDefaults()
	if config.DiskCadence != 5*time.Minute {
		t.Fatalf("disk cadence = %s, want 5m", config.DiskCadence)
	}
	if config.DiskCadence != DefaultHostDiskCadence {
		t.Fatalf("default disk cadence drifted: %s != %s", config.DiskCadence, DefaultHostDiskCadence)
	}
}

func TestHostCollectorDefaultCPUBackgroundCadenceIsFiveSeconds(t *testing.T) {
	if DefaultHostCPUBackgroundCadence != 5*time.Second {
		t.Fatalf("CPU background cadence = %s, want 5s", DefaultHostCPUBackgroundCadence)
	}
	if got := (HostCollectorConfig{}).withDefaults().IdleCadence; got != DefaultHostCPUBackgroundCadence {
		t.Fatalf("default CPU idle cadence = %s, want %s", got, DefaultHostCPUBackgroundCadence)
	}
}

func TestHostCollectorBlockedCPUDoesNotDelayOtherFamilies(t *testing.T) {
	sources := &cadenceHostSources{cpuStarted: make(chan struct{}), releaseCPU: make(chan struct{})}
	collector := NewHostCollector(sources, countingWriter{}, nil, HostCollectorConfig{
		ActiveCadence: time.Second, IdleCadence: 10 * time.Second, GPUActiveCadence: 10 * time.Millisecond,
		GPUIdleCadence: 10 * time.Millisecond, MemoryCadence: 10 * time.Millisecond, SourceTimeout: time.Second,
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := collector.Start(ctx); err != nil {
		t.Fatal(err)
	}
	select {
	case <-sources.cpuStarted:
	case <-time.After(time.Second):
		t.Fatal("CPU source did not start")
	}
	deadline := time.After(250 * time.Millisecond)
	for sources.memoryCalls() < 3 || sources.gpuCalls() < 3 {
		select {
		case <-deadline:
			t.Fatalf("independent cadence stalled: memory=%d gpu=%d", sources.memoryCalls(), sources.gpuCalls())
		default:
			time.Sleep(time.Millisecond)
		}
	}
	for family, times := range map[string][]time.Time{"memory": sources.cadence("memory"), "gpu": sources.cadence("gpu")} {
		for index := 1; index < len(times); index++ {
			if gap := times[index].Sub(times[index-1]); gap < 8*time.Millisecond {
				t.Fatalf("%s cadence gap = %s, want at least 8ms", family, gap)
			}
		}
	}
	close(sources.releaseCPU)
	collector.Stop()
	if got := collector.Counters().Cycles; got != 1 {
		t.Fatalf("CPU cycles = %d, want 1", got)
	}
}

type blockingHostSources struct{ started chan struct{} }

type cadenceHostSources struct {
	cpuStarted chan struct{}
	releaseCPU chan struct{}
	mu         sync.Mutex
	memory     int
	gpu        int
	memoryAt   []time.Time
	gpuAt      []time.Time
}

func (s *cadenceHostSources) CPU(context.Context) (CPUSnapshot, error) {
	select {
	case <-s.cpuStarted:
	default:
		close(s.cpuStarted)
	}
	<-s.releaseCPU
	return snapshot(1, 2, 1), nil
}
func (s *cadenceHostSources) Memory(context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	s.mu.Lock()
	s.memory++
	s.memoryAt = append(s.memoryAt, time.Now())
	s.mu.Unlock()
	return MemoryCounters{UsedBytes: 1, CapacityBytes: 2}, nil, nil
}
func (s *cadenceHostSources) GPU(context.Context) ([]GPUDevice, error) {
	s.mu.Lock()
	s.gpu++
	s.gpuAt = append(s.gpuAt, time.Now())
	s.mu.Unlock()
	return []GPUDevice{{SourceID: "gpu:0", Label: "test"}}, nil
}
func (s *cadenceHostSources) memoryCalls() int { s.mu.Lock(); defer s.mu.Unlock(); return s.memory }
func (s *cadenceHostSources) gpuCalls() int    { s.mu.Lock(); defer s.mu.Unlock(); return s.gpu }
func (s *cadenceHostSources) cadence(kind string) []time.Time {
	s.mu.Lock()
	defer s.mu.Unlock()
	if kind == "memory" {
		return append([]time.Time(nil), s.memoryAt...)
	}
	return append([]time.Time(nil), s.gpuAt...)
}

func (s blockingHostSources) CPU(ctx context.Context) (CPUSnapshot, error) {
	close(s.started)
	<-ctx.Done()
	return CPUSnapshot{}, ctx.Err()
}
func (blockingHostSources) Memory(context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	return MemoryCounters{}, nil, errors.New("not reached")
}
func (blockingHostSources) GPU(context.Context) ([]GPUDevice, error) {
	return nil, errors.New("not reached")
}

type countingWriter struct{}

func (countingWriter) AppendBatch(_ context.Context, batch AppendBatch) (AppendResult, error) {
	return AppendResult{ObservationsAccepted: len(batch.Observations), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
}

type hostFailingCPUWriter struct {
	mu      sync.Mutex
	failed  bool
	batches []AppendBatch
}

func (w *hostFailingCPUWriter) AppendBatch(_ context.Context, batch AppendBatch) (AppendResult, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	for _, observation := range batch.Observations {
		if observation.Family == "cpu" && !w.failed {
			w.failed = true
			return AppendResult{}, errors.New("synthetic CPU append failure")
		}
	}
	w.batches = append(w.batches, batch)
	return AppendResult{ObservationsAccepted: len(batch.Observations), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
}

func (w *hostFailingCPUWriter) snapshot() []AppendBatch {
	w.mu.Lock()
	defer w.mu.Unlock()
	return append([]AppendBatch(nil), w.batches...)
}

func TestHostCollectorReplaysCPUDeltaAfterAppendFailure(t *testing.T) {
	sources := &capturedHostSources{
		cpu: []CPUSnapshot{
			snapshot(50, 100, 10),
			snapshot(70, 140, 25),
			snapshot(90, 180, 40),
		},
		cpuErrs: []error{nil, nil, errors.New("synthetic CPU source failure"), nil},
		memory:  []MemoryCounters{{UsedBytes: 8, CapacityBytes: 16}, {UsedBytes: 9, CapacityBytes: 16}, {UsedBytes: 10, CapacityBytes: 16}, {UsedBytes: 11, CapacityBytes: 16}},
		gpu:     [][]GPUDevice{{}, {}, {}, {}},
	}
	writer := &hostFailingCPUWriter{}
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{
		ActiveCadence: time.Second, MemoryCadence: time.Second, GPUActiveCadence: time.Second,
		SourceTimeout: time.Second, EpochID: "cpu-replay", EpochStartedAt: 100,
		SourceID: "host", ProcessKey: "python", LogicalCPUs: 1,
	})
	for range 4 {
		collector.RunOnce(context.Background())
		clock.advance(time.Second)
	}

	var cpuObservations []Observation
	var cpuUnavailable []UnavailableSpan
	var cpuCoverage []CoverageEpoch
	for _, batch := range writer.snapshot() {
		for _, observation := range batch.Observations {
			if observation.Family == "cpu" {
				cpuObservations = append(cpuObservations, observation)
			}
		}
		for _, unavailable := range batch.UnavailableSpans {
			if unavailable.Family == "cpu" {
				cpuUnavailable = append(cpuUnavailable, unavailable)
			}
		}
		for _, epoch := range batch.CoverageEpochs {
			if epoch.Family == "cpu" {
				cpuCoverage = append(cpuCoverage, epoch)
			}
		}
	}
	if len(cpuObservations) != 1 || len(cpuUnavailable) != 2 || len(cpuCoverage) != 1 || cpuCoverage[0].StartedAt != 103 {
		t.Fatalf("CPU persistence after failed append = observations=%d unavailable=%d coverage=%d batches=%+v", len(cpuObservations), len(cpuUnavailable), len(cpuCoverage), writer.snapshot())
	}
	if got := cpuObservations[0].Payload["process_percent"]; got != 10.0 {
		t.Fatalf("replayed CPU delta = %#v, want 10%% over the full baseline-to-retry interval", got)
	}
	if got := collector.Counters(); got.Errors != 2 {
		t.Fatalf("CPU replay counters = %+v, want one source and one append error", got)
	}
}

func TestWaitCollectorCadenceWakesWhenDemandArrivesDuringIdleWindow(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var demanded atomic.Bool
	result := make(chan bool, 1)
	started := time.Now()
	go func() {
		result <- waitCollectorCadence(ctx, realHostCollectorClock{}, 10*time.Millisecond, 2*time.Second, demanded.Load)
	}()
	time.Sleep(20 * time.Millisecond)
	demanded.Store(true)
	select {
	case ok := <-result:
		if !ok {
			t.Fatal("idle cadence wait was cancelled")
		}
		if elapsed := time.Since(started); elapsed >= 2*time.Second {
			t.Fatalf("demand wake arrived after idle deadline: %s", elapsed)
		}
	case <-time.After(1500 * time.Millisecond):
		t.Fatal("idle cadence wait did not wake for demand")
	}
}

func TestHostCollectorDoesNotPersistIdleGPUGaps(t *testing.T) {
	sources := &capturedHostSources{
		cpu:    []CPUSnapshot{snapshot(10, 20, 2), snapshot(12, 22, 3), snapshot(14, 24, 4)},
		memory: []MemoryCounters{{UsedBytes: 1, CapacityBytes: 2}},
		gpu:    [][]GPUDevice{{{SourceID: "gpu:0", Label: "test"}}, {{SourceID: "gpu:0", Label: "test"}}},
	}
	writer := &recordingBatchWriter{}
	var demanded atomic.Bool
	collector := NewHostCollector(sources, writer, nil, HostCollectorConfig{
		ActiveCadence: 10 * time.Millisecond, IdleCadence: 100 * time.Millisecond,
		GPUActiveCadence: time.Hour, GPUIdleCadence: 100 * time.Millisecond,
		MemoryCadence: time.Hour, SourceTimeout: time.Second, ActiveFunc: demanded.Load,
		ProcessKey: "python", LogicalCPUs: 1,
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := collector.Start(ctx); err != nil {
		t.Fatal(err)
	}
	time.Sleep(30 * time.Millisecond)
	demanded.Store(true)
	deadline := time.Now().Add(500 * time.Millisecond)
	for time.Now().Before(deadline) {
		if collector.Counters().Cycles > 0 {
			break
		}
		time.Sleep(time.Millisecond)
	}
	collector.Stop()
	for _, batch := range writer.snapshot() {
		for _, gap := range batch.UnavailableSpans {
			if gap.Family == "gpu" && gap.NativeCadenceSeconds > 0.02 {
				t.Fatalf("idle gap persisted: %+v", gap)
			}
		}
	}
}

func TestHostCollectorPersistsCPUHistoryWhenStatsAreNotDemanded(t *testing.T) {
	sources := &capturedHostSources{
		cpu: []CPUSnapshot{
			snapshot(10, 20, 2), snapshot(12, 22, 3), snapshot(14, 24, 4),
		},
		memory: []MemoryCounters{{UsedBytes: 1, CapacityBytes: 2}},
		gpu:    [][]GPUDevice{{{SourceID: "gpu:0", Label: "test"}}},
	}
	statsPath := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	writerClosed := false
	t.Cleanup(func() {
		if !writerClosed {
			_ = writer.Close()
		}
	})
	var demanded atomic.Bool
	collector := NewHostCollector(sources, writer, nil, HostCollectorConfig{
		ActiveCadence: 5 * time.Millisecond, IdleCadence: 15 * time.Millisecond,
		GPUActiveCadence: time.Hour, GPUIdleCadence: time.Hour,
		MemoryCadence: time.Hour, SourceTimeout: time.Second, ActiveFunc: demanded.Load,
		ProcessKey: "python", LogicalCPUs: 1,
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := collector.Start(ctx); err != nil {
		t.Fatal(err)
	}
	deadline := time.NewTimer(time.Second)
	ticker := time.NewTicker(time.Millisecond)
	defer deadline.Stop()
	defer ticker.Stop()
	for collector.Counters().Cycles < 3 {
		select {
		case <-deadline.C:
			collector.Stop()
			t.Fatal("CPU collector did not sample again while stats were not demanded")
		case <-ticker.C:
		}
	}
	collector.Stop()
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writerClosed = true

	database, err := OpenReadOnly(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	cpuObservations := 0
	for _, observation := range observations {
		if observation.Family == "cpu" {
			cpuObservations++
			if observation.Payload["process_percent"] == nil {
				t.Fatalf("CPU observation has no process value: %+v", observation)
			}
		}
	}
	if cpuObservations < 2 {
		t.Fatalf("undemanded CPU observations persisted after reopen = %d, want at least 2: %+v", cpuObservations, observations)
	}
}

type recordingBatchWriter struct {
	mu      sync.Mutex
	batches []AppendBatch
}

func (w *recordingBatchWriter) AppendBatch(_ context.Context, batch AppendBatch) (AppendResult, error) {
	w.mu.Lock()
	w.batches = append(w.batches, batch)
	w.mu.Unlock()
	return AppendResult{ObservationsAccepted: len(batch.Observations), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
}

func (w *recordingBatchWriter) snapshot() []AppendBatch {
	w.mu.Lock()
	defer w.mu.Unlock()
	return append([]AppendBatch(nil), w.batches...)
}

type auditDiskAdapter struct {
	*capturedHostSources
	disk *LinuxHostSources
}

func (s auditDiskAdapter) Disk(ctx context.Context) ([]DiskUsageSample, error) {
	return s.disk.Disk(ctx)
}
func TestAuditPartialDiskPersistsHealthyRowsAndGaps(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "disk.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	source := auditDiskAdapter{disk: &LinuxHostSources{run: func(context.Context, []string) (CommandOutput, error) {
		return CommandOutput{ExitCode: 1, Stdout: []byte("Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/a 100 20 80 20% /mnt/a  b\n/dev/b 100 30 70 30% /mnt/a b\n/dev/c 0 0 0 N/A /missing\n")}, errors.New("partial df")
	}}}
	collector := NewHostCollector(source, writer, &collectorClock{now: time.Unix(100, 0)}, HostCollectorConfig{EpochID: "partial", EpochStartedAt: 100, OwnerGeneration: 1, SourceID: "host"})
	batch := collector.collectDisk(ctx, HostFactContext{EpochID: "partial", EpochStartedAt: 100, ObservedAt: 101, CadenceSeconds: 900, OwnerGeneration: 1, SourceID: "host"})
	if _, err := writer.AppendBatch(ctx, batch); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	rows, err := reader.ReadObservations(ctx, 0, 2000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 3 {
		t.Fatalf("lost disk rows: %d", len(rows))
	}
	ids := map[string]bool{}
	for _, row := range rows {
		ids[row.SourceID] = true
		if row.Payload["mountpoint"] == "/missing" {
			if _, exists := row.Payload["used_percent"]; exists {
				t.Fatal("missing sample became zero")
			}
		}
	}
	if len(ids) != 3 {
		t.Fatal("mount identities collapsed")
	}
	gaps, err := reader.ReadUnavailableSpans(ctx, 0, 2000, MaxFactReadLimit)
	if err != nil || len(gaps) != 2 {
		t.Fatalf("gaps: %+v, %v", gaps, err)
	}
}
