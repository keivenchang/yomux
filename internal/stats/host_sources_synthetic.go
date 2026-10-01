package stats

import (
	"context"
	"fmt"
	"sync"
)

// NewSyntheticHostSources returns deterministic host facts for development
// and integration fixtures. It performs no filesystem, process-table, GPU,
// or command lookup; callers must opt in explicitly rather than changing the
// production source selected by NewHostSources.
func NewSyntheticHostSources(processIdentity, processKey string) HostSources {
	if processIdentity == "" {
		processIdentity = "synthetic-owned:"
	}
	if processKey == "" {
		processKey = "yolomux-go"
	}
	return &syntheticHostSources{processIdentity: processIdentity + "1", processKey: processKey}
}

type syntheticHostSources struct {
	mu              sync.Mutex
	cpuCalls        uint64
	memoryCalls     uint64
	gpuCalls        uint64
	processIdentity string
	processKey      string
}

// SystemLogicalCPUs returns the fixture's stable eight-CPU topology without
// consulting the machine that runs development or integration tests.
func (*syntheticHostSources) SystemLogicalCPUs() int { return 8 }

func (s *syntheticHostSources) CPU(ctx context.Context) (CPUSnapshot, error) {
	if err := ctx.Err(); err != nil {
		return CPUSnapshot{}, err
	}
	s.mu.Lock()
	s.cpuCalls++
	call := s.cpuCalls
	s.mu.Unlock()
	// The second and later samples represent a fixed synthetic workload: the
	// owned process advances by 12 ticks, a same-named co-tenant by 50 ticks,
	// and Python by 10 ticks. This makes ownership and host aggregation visible
	// without consulting the machine running the fixture.
	return CPUSnapshot{
		Host:                  CPUHostCounters{Busy: 100000 + call*30000, Total: 1000000 + call*100000},
		ProcessTicksPerSecond: 100,
		Processes: map[string]CPUProcessCounters{
			s.processIdentity:      {Identity: s.processIdentity, CPU: call * 12, Binary: s.processKey},
			"synthetic-cotenant:1": {Identity: "synthetic-cotenant:1", CPU: call * 50, Binary: s.processKey},
			"synthetic-python:1":   {Identity: "synthetic-python:1", CPU: call * 10, Binary: "python"},
		},
	}, nil
}

func (s *syntheticHostSources) Memory(ctx context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	if err := ctx.Err(); err != nil {
		return MemoryCounters{}, nil, err
	}
	s.mu.Lock()
	s.memoryCalls++
	s.mu.Unlock()
	return MemoryCounters{UsedBytes: 8 << 20, CapacityBytes: 16 << 20}, []ProcessCensusRow{
		{PID: 1001, Identity: s.processIdentity, Binary: s.processKey, CPUSeconds: 1, RSSBytes: 4 << 20},
		{PID: 1002, Identity: "synthetic-cotenant:1", Binary: s.processKey, CPUSeconds: 1, RSSBytes: 3 << 20},
		{PID: 1003, Identity: "synthetic-python:1", Binary: "python", CPUSeconds: 1, RSSBytes: 2 << 20},
	}, nil
}

func (s *syntheticHostSources) GPU(ctx context.Context) ([]GPUDevice, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	s.mu.Lock()
	s.gpuCalls++
	call := s.gpuCalls
	s.mu.Unlock()
	return []GPUDevice{{SourceID: fmt.Sprintf("synthetic-gpu:%d", call%2), Label: "Synthetic GPU", Utilization: 12, MemoryUsedBytes: 1 << 20, MemoryTotalBytes: 8 << 20}}, nil
}
