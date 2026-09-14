package stats

import (
	"context"
	"errors"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

type capturedHostSources struct {
	mu       sync.Mutex
	cpu      []CPUSnapshot
	memory   []MemoryCounters
	gpu      [][]GPUDevice
	gpuError error
}

func (s *capturedHostSources) CPU(context.Context) (CPUSnapshot, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
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
	return CPUSnapshot{Host: CPUHostCounters{Busy: busy, Total: total}, Processes: map[string]CPUProcessCounters{"python": {Identity: "pid-1", CPU: process}}}
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
	if counters.Cycles != 3 || counters.AcceptedObservations != 4 || counters.UnavailableSpans != 1 || counters.Errors != 0 {
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
	if len(observations) != 4 {
		t.Fatalf("observations = %d, want 4", len(observations))
	}
	unavailable, err := reader.ReadUnavailableSpans(ctx, 0, 1000, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(unavailable) != 1 || unavailable[0].Family != "cpu" {
		t.Fatalf("unavailable = %+v", unavailable)
	}
	seen := map[string]int{}
	for _, observation := range observations {
		seen[observation.Family]++
	}
	if seen["cpu"] != 2 || seen["system_memory"] != 1 || seen["gpu"] != 1 {
		t.Fatalf("family rows = %#v", seen)
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
	if counters := collector.Counters(); counters.Cycles != 2 || counters.AcceptedObservations != 3 || counters.UnavailableSpans != 1 || counters.Errors != 0 {
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
	if len(observations) != 3 {
		t.Fatalf("persisted observations = %d, want 3", len(observations))
	}
	seen := map[string]int{}
	for _, observation := range observations {
		seen[observation.Family]++
		if observation.OwnerGeneration != 12 {
			t.Fatalf("observation generation = %d, want 12", observation.OwnerGeneration)
		}
	}
	if seen["cpu"] != 1 || seen["system_memory"] != 1 || seen["gpu"] != 1 {
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
