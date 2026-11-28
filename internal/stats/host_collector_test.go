package stats

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"reflect"
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

// Captured samples are finite: an extra scheduled cycle reports unavailable
// instead of inventing another sample or panicking before the observer stops it.
func popCapturedHostSample[T any](samples *[]T) (T, error) {
	if len(*samples) == 0 {
		var zero T
		return zero, io.EOF
	}
	item := (*samples)[0]
	*samples = (*samples)[1:]
	return item, nil
}

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
	return popCapturedHostSample(&s.cpu)
}
func (s *capturedHostSources) Memory(context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	item, err := popCapturedHostSample(&s.memory)
	if err != nil {
		return MemoryCounters{}, nil, err
	}
	return item, []ProcessCensusRow{{Identity: "pid-1", Binary: "python", RSSBytes: 4096}}, nil
}
func (s *capturedHostSources) GPU(context.Context) ([]GPUDevice, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.gpuError != nil {
		return nil, s.gpuError
	}
	return popCapturedHostSample(&s.gpu)
}

func TestCapturedHostSourcesReportFiniteSampleExhaustion(t *testing.T) {
	cpu := snapshot(10, 20, 2)
	memory := MemoryCounters{UsedBytes: 1, CapacityBytes: 2}
	gpu := []GPUDevice{{SourceID: "gpu:0", Label: "captured"}}
	for _, family := range []string{"cpu", "memory", "gpu"} {
		t.Run(family, func(t *testing.T) {
			source := &capturedHostSources{cpu: []CPUSnapshot{cpu}, memory: []MemoryCounters{memory}, gpu: [][]GPUDevice{gpu}}
			var read func() (any, error)
			var want, zero any
			switch family {
			case "cpu":
				want, zero = cpu, CPUSnapshot{}
				read = func() (any, error) { return source.CPU(context.Background()) }
			case "memory":
				want, zero = memory, MemoryCounters{}
				read = func() (any, error) {
					value, rows, err := source.Memory(context.Background())
					if err == nil && len(rows) != 1 || err != nil && rows != nil {
						t.Fatalf("memory census disagrees with sample availability: rows=%v err=%v", rows, err)
					}
					return value, err
				}
			case "gpu":
				want, zero = gpu, []GPUDevice(nil)
				read = func() (any, error) { return source.GPU(context.Background()) }
			}
			if got, err := read(); err != nil || !reflect.DeepEqual(got, want) {
				t.Fatalf("captured sample=%v err=%v want=%v", got, err, want)
			}
			for attempt := 0; attempt < 2; attempt++ {
				if got, err := read(); !errors.Is(err, io.EOF) || !reflect.DeepEqual(got, zero) {
					t.Fatalf("exhausted sample=%v err=%v want zero=%v and EOF", got, err, zero)
				}
			}
		})
	}
	failure := errors.New("configured source failure")
	source := &capturedHostSources{cpuErrs: []error{failure}, gpuError: failure}
	if _, err := source.CPU(context.Background()); !errors.Is(err, failure) {
		t.Fatalf("configured CPU failure lost to exhaustion: %v", err)
	}
	if _, err := source.GPU(context.Background()); !errors.Is(err, failure) {
		t.Fatalf("configured GPU failure lost to exhaustion: %v", err)
	}
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

func TestHostCollectorKeepsReadableProcessMeasurementsWhenCensusIsIncomplete(t *testing.T) {
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
	processCPU, ok := cpuPayload["process_cpu_percent"].(map[string]any)
	if !ok || processCPU[hostProcessInstanceSeriesKey("yolomux-go", "101:10")] != 10.0 || cpuPayload["process_census_incomplete"] != true {
		t.Fatalf("partial CPU census lost readable process data or its incomplete marker: %#v", cpuPayload)
	}
	owned, ok := cpuPayload["owned_process_cpu_percent"].(map[string]any)
	if !ok || owned["yolomux-go"] != 10.0 {
		t.Fatalf("partial census lost the owned Go CPU reading: %#v", cpuPayload)
	}
	processMemory, ok := memoryPayload["process_memory_bytes"].(map[string]any)
	if !ok || processMemory["python"] != uint64(4096) || memoryPayload["process_census_incomplete"] != true {
		t.Fatalf("partial memory census lost readable process data or its incomplete marker: %#v", memoryPayload)
	}
	latest := collector.Latest()
	if !latest.CPUValid || len(latest.ProcessCPUPercent) == 0 || !latest.MemoryValid || latest.ProcessMemoryBytes["python"] != 4096 {
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

func TestWaitCollectorCadenceKeepsFixedFamiliesIndependentOfDemand(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	clock := &agentStatusScheduleClock{timers: make(chan time.Duration, 1)}
	finished := make(chan bool, 1)
	go func() {
		finished <- waitCollectorCadence(ctx, clock, 5*time.Minute, 5*time.Minute, func() bool { return false })
	}()
	select {
	case delay := <-clock.timers:
		if delay != 5*time.Minute {
			cancel()
			<-finished
			t.Fatalf("fixed disk cadence used demand-sensitive timer %s, want 5m", delay)
		}
	case <-time.After(time.Second):
		t.Fatal("collector did not schedule its timer")
	}
	cancel()
	if <-finished {
		t.Fatal("cancelled cadence reported a collection deadline")
	}
}

type diskDemandTransitionClock struct {
	collectorClock
	demanded atomic.Bool
}

func (c *diskDemandTransitionClock) NewTimer(delay time.Duration) HostCollectorTimer {
	c.advance(delay)
	c.demanded.Store(true)
	timer := &neverTimer{ch: make(chan time.Time, 1)}
	timer.ch <- c.Now()
	return timer
}

func TestHostCollectorDiskDemandTransitionPreservesUnavailableIntervals(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "disk.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &diskDemandTransitionClock{collectorClock: collectorClock{now: time.Unix(100, 0)}}
	source := &capturedDiskHostSources{diskSamples: []DiskUsageSample{
		{SourceID: "mount:healthy", Mountpoint: "/", UsedBytes: 25, CapacityBytes: 100},
		{SourceID: "mount:unavailable", Mountpoint: "/unavailable", Reason: "invalid_sample"},
	}}
	collector := NewHostCollector(source, writer, clock, HostCollectorConfig{
		EpochID: "disk-demand", EpochStartedAt: 100, OwnerGeneration: 1, SourceID: "host",
		ActiveFunc: clock.demanded.Load,
	})
	collector.runFamily(ctx, "system_disk")
	if !waitCollectorCadence(ctx, clock, collector.familyCadence("system_disk"), collector.familyIdleCadence("system_disk"), collector.config.ActiveFunc) {
		t.Fatal("disk cadence was cancelled")
	}
	collector.runFamily(ctx, "system_disk")
	health := collector.Health()["system_disk"]
	if !clock.demanded.Load() || source.diskCalls != 2 || health.Rejected != 0 || health.LastSuccessfulCommit != 400 {
		t.Fatalf("demand interrupted disk persistence: calls=%d health=%+v", source.diskCalls, health)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	gaps, err := reader.ReadUnavailableSpans(ctx, 0, 1000, MaxFactReadLimit)
	if err != nil || len(gaps) != 2 || gaps[0].StartedAt != 100 || gaps[0].EndedAt != 400 || gaps[1].StartedAt != 400 || gaps[1].EndedAt != 700 {
		t.Fatalf("disk unavailable intervals = %+v, err=%v", gaps, err)
	}
	rows, err := reader.ReadObservations(ctx, 0, 1000, MaxFactReadLimit)
	if err != nil || len(rows) != 4 {
		t.Fatalf("disk history lost healthy or unavailable mount inventory: rows=%+v err=%v", rows, err)
	}
}

type diskHistoryTimer struct {
	delay time.Duration
	timer *neverTimer
}

type diskHistoryClock struct {
	collectorClock
	timers chan diskHistoryTimer
}

func (c *diskHistoryClock) NewTimer(delay time.Duration) HostCollectorTimer {
	timer := &neverTimer{ch: make(chan time.Time, 1)}
	c.timers <- diskHistoryTimer{delay, timer}
	return timer
}

func TestHostCollectorDiskRestartResumesAfterRecordedIntervals(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "disk.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	const disk = "system_disk"
	source := &capturedDiskHostSources{diskSamples: []DiskUsageSample{
		{SourceID: "mount:healthy", Mountpoint: "/", UsedBytes: 25, CapacityBytes: 100},
		{SourceID: "mount:unavailable", Mountpoint: "/unavailable", Reason: "invalid_sample"},
	}}
	first := NewHostCollector(source, writer, &collectorClock{now: time.Unix(100, 0)}, HostCollectorConfig{
		EpochID: "before-restart", EpochStartedAt: 100, OwnerGeneration: 1, SourceID: "host",
	})
	first.runFamily(ctx, disk)
	if health := first.Health()[disk]; health.LastSuccessfulCommit != 100 {
		t.Fatalf("initial disk write: %+v", health)
	}
	clock := &diskHistoryClock{collectorClock: collectorClock{now: time.Unix(150, 0)}, timers: make(chan diskHistoryTimer, 1)}
	restarted := NewHostCollector(source, writer, clock, HostCollectorConfig{
		EpochID: "after-restart", EpochStartedAt: 150, OwnerGeneration: 2, SourceID: "host",
	})
	runCtx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() {
		defer close(done)
		restarted.familyLoop(runCtx, disk, 5*time.Minute, 5*time.Minute)
	}()
	defer func() { cancel(); <-done }()
	nextTimer := func() diskHistoryTimer {
		select {
		case scheduled := <-clock.timers:
			return scheduled
		case <-time.After(2 * time.Second):
			t.Fatal("disk collector did not schedule")
			return diskHistoryTimer{}
		}
	}
	resume := nextTimer()
	if resume.delay != 250*time.Second || source.diskCalls != 1 {
		t.Fatalf("restart delay=%s calls=%d health=%+v", resume.delay, source.diskCalls, restarted.Health()[disk])
	}
	source.diskSamples[1] = DiskUsageSample{SourceID: "mount:unavailable", Mountpoint: "/unavailable", UsedBytes: 50, CapacityBytes: 100}
	clock.advance(resume.delay)
	resume.timer.ch <- clock.Now()
	cadence := nextTimer()
	if cadence.delay != 5*time.Minute || source.diskCalls != 2 {
		t.Fatalf("resumed delay=%s calls=%d", cadence.delay, source.diskCalls)
	}
	if health := restarted.Health()[disk]; health.Rejected != 0 || health.LastSuccessfulCommit != 400 {
		t.Fatalf("disk write after restart: %+v", health)
	}
	// The timer runs on monotonic time. A wall clock correction can put the
	// next sample just inside the previously committed interval.
	clock.advance(cadence.delay - 5*time.Millisecond)
	cadence.timer.ch <- clock.Now()
	correction := nextTimer()
	if correction.delay < 4*time.Millisecond || correction.delay > 6*time.Millisecond || source.diskCalls != 2 {
		t.Fatalf("clock correction delay=%s calls=%d", correction.delay, source.diskCalls)
	}
	clock.advance(5 * time.Millisecond)
	correction.timer.ch <- clock.Now()
	nextTimer()
	if health := restarted.Health()[disk]; health.Rejected != 0 || health.LastSuccessfulCommit != 700 {
		t.Fatalf("disk write after clock correction: %+v", health)
	}
}

// A backward wall clock jump leaves recorded disk history ahead of the current
// time. The collector must keep its cadence, start exactly one new epoch for
// the jump (not one per sample until the clock catches up), and the chart over
// the range both timelines cover must show one gauge value, not their sum.
func TestHostCollectorDiskSamplesAfterBackwardClockJump(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "disk.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	const disk = "system_disk"
	source := &capturedDiskHostSources{diskSamples: []DiskUsageSample{{SourceID: "mount:healthy", Mountpoint: "/", UsedBytes: 25, CapacityBytes: 100}}}
	clock := &diskHistoryClock{collectorClock: collectorClock{now: time.Unix(100000, 0)}, timers: make(chan diskHistoryTimer, 1)}
	collector := NewHostCollector(source, writer, clock, HostCollectorConfig{
		EpochID: "running", EpochStartedAt: 100000, OwnerGeneration: 1, SourceID: "host", DiskCadence: 5 * time.Minute,
	})
	runCtx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() {
		defer close(done)
		collector.familyLoop(runCtx, disk, 5*time.Minute, 5*time.Minute)
	}()
	defer func() { cancel(); <-done }()
	nextTimer := func() diskHistoryTimer {
		select {
		case scheduled := <-clock.timers:
			return scheduled
		case <-time.After(2 * time.Second):
			t.Fatal("disk collector did not schedule")
			return diskHistoryTimer{}
		}
	}
	// Each step fires the cadence timer after moving the wall clock by delta
	// and expects the next sample to commit at wall time commit.
	cadence := nextTimer()
	steps := []struct {
		delta  time.Duration
		commit float64
	}{
		{5 * time.Minute, 100300},
		{5 * time.Minute, 100600},
		// The clock moves back 15 minutes; recorded history now ends 20
		// minutes ahead, beyond one cadence.
		{-15 * time.Minute, 99700},
		{5 * time.Minute, 100000},
		{5 * time.Minute, 100300},
		{5 * time.Minute, 100600},
		{5 * time.Minute, 100900},
	}
	for index, step := range steps {
		if index == 2 {
			source.diskSamples[0].UsedBytes = 50
		}
		clock.advance(step.delta)
		cadence.timer.ch <- clock.Now()
		cadence = nextTimer()
		health := collector.Health()[disk]
		if cadence.delay != 5*time.Minute || health.Rejected != 0 || health.LastSuccessfulCommit != step.commit {
			t.Fatalf("step %d: delay=%s health=%+v", index, cadence.delay, health)
		}
	}
	if source.diskCalls != len(steps)+1 {
		t.Fatalf("disk samples = %d, want %d", source.diskCalls, len(steps)+1)
	}

	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	epochs, err := reader.ReadCoverageEpochs(ctx, 0, 200000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	diskEpochs := map[string]bool{}
	for _, epoch := range epochs {
		if epoch.Family == disk {
			diskEpochs[epoch.EpochID] = true
		}
	}
	if len(diskEpochs) != 2 {
		t.Fatalf("disk epochs = %v, want the original and exactly one after the jump", diskEpochs)
	}
	observations, err := reader.ReadObservations(ctx, 0, 200000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	unavailable, err := reader.ReadUnavailableSpans(ctx, 0, 200000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	// Both timelines sampled at 100000, 100300 and 100600.
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 99600, End: 101100, Resolution: 300}, observations, epochs, unavailable, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.Buckets) != 5 {
		t.Fatalf("buckets = %d", len(layer.Buckets))
	}
	for _, bucket := range layer.Buckets {
		if got := seriesValue(bucket, "system_disk_used_percent:/"); got != 25 && got != 50 {
			t.Fatalf("bucket %+v used percent = %v, want one sample's value", bucket.Start, got)
		}
	}
	for _, gap := range layer.NoData {
		if gap.Family == disk && gap.Start < 101100 && gap.End > 99700 {
			t.Fatalf("overlapping epochs left a coverage gap: %+v", gap)
		}
	}
}

// CPU and memory samples after a backward wall clock jump were rejected until
// the clock caught up with the epoch's recorded history. Each family must
// start exactly one new epoch for the jump and keep committing.
func TestHostCollectorCPUAndMemoryRotateOnceAfterBackwardClockJump(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "host.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	memory := MemoryCounters{UsedBytes: 1, CapacityBytes: 2}
	sources := &capturedHostSources{
		cpu:    []CPUSnapshot{snapshot(10, 20, 2), snapshot(12, 22, 3), snapshot(14, 24, 4), snapshot(16, 26, 5), snapshot(18, 28, 6), snapshot(20, 30, 7), snapshot(22, 32, 8)},
		memory: []MemoryCounters{memory, memory, memory, memory, memory, memory, memory},
	}
	clock := &collectorClock{now: time.Unix(100000, 0)}
	collector := NewHostCollector(sources, writer, clock, HostCollectorConfig{EpochID: "running", EpochStartedAt: 100000, OwnerGeneration: 1, SourceID: "host"})
	for index, step := range []struct {
		delta  time.Duration
		commit float64
	}{
		{0, 100000}, {5 * time.Second, 100005}, {-2 * time.Hour, 92805}, {5 * time.Second, 92810}, {5 * time.Second, 92815}, {5 * time.Second, 92820},
	} {
		clock.advance(step.delta)
		collector.runFamily(ctx, "cpu")
		collector.runFamily(ctx, "system_memory")
		for _, family := range []string{"cpu", "system_memory"} {
			// The first CPU snapshot only primes the sampler.
			if family == "cpu" && index == 0 {
				continue
			}
			if health := collector.Health()[family]; health.Rejected != 0 || health.LastSuccessfulCommit != step.commit {
				t.Fatalf("step %d %s: %+v", index, family, health)
			}
		}
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	epochs, err := reader.ReadCoverageEpochs(ctx, 0, 200000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	memoryEpochs := map[string]bool{}
	for _, epoch := range epochs {
		if epoch.Family == "system_memory" {
			memoryEpochs[epoch.EpochID] = true
		}
		// The test clock has no monotonic reading, so the first CPU sample after
		// the jump sees a negative interval, is recorded unavailable, and closes
		// its epoch as any unavailable CPU sample does. Later samples must stay
		// in the next epoch instead of rotating while the clock catches up.
		if epoch.Family == "cpu" && epoch.StartedAt > 92810 && epoch.StartedAt < 100000 {
			t.Fatalf("cpu rotated again after the jump: %+v", epoch)
		}
	}
	if len(memoryEpochs) != 2 {
		t.Fatalf("memory epochs = %v, want the original and exactly one after the jump", memoryEpochs)
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

type failingDiskHistoryWriter struct{ countingWriter }

func (failingDiskHistoryWriter) diskHistoryEnd(context.Context, float64) (float64, error) {
	return 0, errors.New("fixture history unavailable")
}

func TestDiskHistoryFailureAllowsSampling(t *testing.T) {
	collector := NewHostCollector(&capturedHostSources{}, failingDiskHistoryWriter{}, &collectorClock{now: time.Unix(100, 0)}, HostCollectorConfig{})
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if !collector.waitForDiskHistory(ctx) {
		t.Fatal("history read failure prevented a fresh disk sample")
	}
	if collector.Health()["system_disk"].LatestError == nil {
		t.Fatal("history failure diagnostic was lost")
	}
}
