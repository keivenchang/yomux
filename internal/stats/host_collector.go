package stats

import (
	"context"
	"errors"
	"fmt"
	"runtime"
	"sync"
	"sync/atomic"
	"time"
)

// HostSources is the deliberately small boundary between the runtime and the
// platform adapters. Implementations should honor ctx and return captured,
// already-parsed values.
type HostSources interface {
	CPU(context.Context) (CPUSnapshot, error)
	Memory(context.Context) (MemoryCounters, []ProcessCensusRow, error)
	GPU(context.Context) ([]GPUDevice, error)
}

// BatchWriter is satisfied by Writer and makes the collector independently
// testable without coupling it to SQLite.
type BatchWriter interface {
	AppendBatch(context.Context, AppendBatch) (AppendResult, error)
}

type HostCollectorClock interface {
	Now() time.Time
	NewTimer(time.Duration) HostCollectorTimer
}

type HostCollectorTimer interface {
	C() <-chan time.Time
	Stop() bool
}

type realHostCollectorClock struct{}

func (realHostCollectorClock) Now() time.Time { return time.Now() }
func (realHostCollectorClock) NewTimer(d time.Duration) HostCollectorTimer {
	return realHostCollectorTimer{time.NewTimer(d)}
}

type realHostCollectorTimer struct{ *time.Timer }

func (t realHostCollectorTimer) C() <-chan time.Time { return t.Timer.C }

// HostCollectorConfig controls one collector owner. ActiveFunc is sampled
// before each cycle; a nil function means active cadence.
type HostCollectorConfig struct {
	ActiveCadence    time.Duration
	IdleCadence      time.Duration
	GPUActiveCadence time.Duration
	GPUIdleCadence   time.Duration
	MemoryCadence    time.Duration
	SourceTimeout    time.Duration
	EpochID          string
	EpochStartedAt   float64
	OwnerGeneration  uint64
	SourceID         string
	ProcessKey       string
	GroupKeys        []string
	LogicalCPUs      int
	ActiveFunc       func() bool
}

const DefaultHostMemoryCadence = time.Minute

func (c HostCollectorConfig) withDefaults() HostCollectorConfig {
	if c.ActiveCadence <= 0 {
		c.ActiveCadence = time.Second
	}
	if c.IdleCadence <= 0 {
		c.IdleCadence = time.Minute
	}
	if c.SourceTimeout <= 0 {
		c.SourceTimeout = c.ActiveCadence
	}
	if c.GPUActiveCadence <= 0 {
		c.GPUActiveCadence = 10 * time.Second
	}
	if c.GPUIdleCadence <= 0 {
		c.GPUIdleCadence = time.Minute
	}
	if c.MemoryCadence <= 0 {
		c.MemoryCadence = DefaultHostMemoryCadence
	}
	if c.EpochID == "" {
		c.EpochID = "host"
	}
	if c.SourceID == "" {
		c.SourceID = "host"
	}
	return c
}

type HostCollectorCounters struct {
	Cycles               uint64
	AcceptedObservations uint64
	UnavailableSpans     uint64
	Errors               uint64
	LastDuration         time.Duration
}

type hostCollectorCounters struct {
	cycles, accepted, unavailable, errors atomic.Uint64
	lastDuration                          atomic.Int64
}

func (c *hostCollectorCounters) snapshot() HostCollectorCounters {
	return HostCollectorCounters{Cycles: c.cycles.Load(), AcceptedObservations: c.accepted.Load(), UnavailableSpans: c.unavailable.Load(), Errors: c.errors.Load(), LastDuration: time.Duration(c.lastDuration.Load())}
}

// HostCollector owns exactly one collection goroutine when started. RunOnce
// is also safe for deterministic callers, but must not be called concurrently
// with Start.
type HostCollector struct {
	sources      HostSources
	writer       BatchWriter
	clock        HostCollectorClock
	config       HostCollectorConfig
	sampler      *CPUDeltaSampler
	lastSample   time.Time
	stateMu      sync.Mutex
	familyEpochs map[string]hostEpochState
	counters     hostCollectorCounters
	mu           sync.Mutex
	run          bool
	stop         context.CancelFunc
	done         chan struct{}
}

type hostEpochState struct {
	epochID    string
	started    float64
	rotate     bool
	hadSuccess bool
}

func NewHostCollector(sources HostSources, writer BatchWriter, clock HostCollectorClock, config HostCollectorConfig) *HostCollector {
	if clock == nil {
		clock = realHostCollectorClock{}
	}
	config = config.withDefaults()
	logicalCPUs := config.LogicalCPUs
	if logicalCPUs <= 0 {
		logicalCPUs = runtime.NumCPU()
	}
	return &HostCollector{sources: sources, writer: writer, clock: clock, config: config, sampler: NewCPUDeltaSampler(config.ProcessKey, config.GroupKeys, logicalCPUs), familyEpochs: make(map[string]hostEpochState)}
}

func (c *HostCollector) Counters() HostCollectorCounters { return c.counters.snapshot() }

// Start launches the sole owner goroutine. Calling Start twice is an error.
func (c *HostCollector) Start(parent context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.run {
		return errors.New("host collector already started")
	}
	ctx, cancel := context.WithCancel(parent)
	c.stop, c.done, c.run = cancel, make(chan struct{}), true
	go c.loop(ctx)
	return nil
}

func (c *HostCollector) loop(ctx context.Context) {
	defer func() {
		c.mu.Lock()
		c.run = false
		close(c.done)
		c.mu.Unlock()
	}()
	var workers sync.WaitGroup
	workers.Add(3)
	go func() { defer workers.Done(); c.familyLoop(ctx, "cpu", c.config.ActiveCadence, c.config.IdleCadence) }()
	go func() {
		defer workers.Done()
		c.familyLoop(ctx, "system_memory", c.config.MemoryCadence, c.config.MemoryCadence)
	}()
	go func() {
		defer workers.Done()
		c.familyLoop(ctx, "gpu", c.config.GPUActiveCadence, c.config.GPUIdleCadence)
	}()
	workers.Wait()
}

func (c *HostCollector) familyLoop(ctx context.Context, family string, active, idle time.Duration) {
	first := true
	for {
		if !first {
			delay := active
			if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
				delay = idle
			}
			timer := c.clock.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C():
			}
		}
		first = false
		if ctx.Err() != nil {
			return
		}
		c.runFamily(ctx, family)
	}
}

func (c *HostCollector) runFamily(parent context.Context, family string) {
	c.runFamilyWithCadence(parent, family, c.familyCadence(family))
}

func (c *HostCollector) runFamilyWithCadence(parent context.Context, family string, cadence time.Duration) {
	started := c.clock.Now()
	if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
		if family != "system_memory" {
			cadence = c.familyIdleCadence(family)
		}
	}
	fact := c.familyFactContext(family, started, cadence)
	var batch AppendBatch
	if c.sources == nil {
		c.counters.errors.Add(1)
		batch = BuildUnavailableFacts(family, fact, "source_unavailable")
	} else {
		switch family {
		case "cpu":
			batch = c.collectCPU(parent, fact, started.Sub(c.lastSample))
			c.lastSample = started
		case "system_memory":
			batch = c.collectMemory(parent, fact)
		case "gpu":
			batch = c.collectGPU(parent, fact)
		}
	}
	batch = c.finishFamilyBatch(family, fact, batch)
	c.append(parent, batch)
	if family == "cpu" {
		c.counters.cycles.Add(1)
	}
}

// Stop cancels the owner and waits for its current bounded collection to end.
func (c *HostCollector) Stop() {
	c.mu.Lock()
	if !c.run {
		c.mu.Unlock()
		return
	}
	stop, done := c.stop, c.done
	c.mu.Unlock()
	stop()
	<-done
}

func (c *HostCollector) RunOnce(parent context.Context) {
	started := c.clock.Now()
	// Keep deterministic callers on the same independent family path as Start.
	// A bad interval in one family must not roll back sibling observations.
	c.runFamilyWithCadence(parent, "cpu", c.config.ActiveCadence)
	c.runFamilyWithCadence(parent, "system_memory", c.config.MemoryCadence)
	c.runFamilyWithCadence(parent, "gpu", c.config.ActiveCadence)
	c.counters.lastDuration.Store(c.clock.Now().Sub(started).Nanoseconds())
}

func (c *HostCollector) factContext(at time.Time, cadence time.Duration) HostFactContext {
	return HostFactContext{EpochID: c.config.EpochID, EpochStartedAt: c.config.EpochStartedAt, ObservedAt: float64(at.UnixNano()) / 1e9, CadenceSeconds: cadence.Seconds(), OwnerGeneration: c.config.OwnerGeneration, SourceID: c.config.SourceID}
}

func (c *HostCollector) familyFactContext(family string, at time.Time, cadence time.Duration) HostFactContext {
	c.stateMu.Lock()
	defer c.stateMu.Unlock()
	state, ok := c.familyEpochs[family]
	if !ok {
		state = hostEpochState{epochID: c.config.EpochID, started: c.config.EpochStartedAt}
	}
	if state.rotate {
		state.epochID = fmt.Sprintf("%s-%s-%d", c.config.EpochID, family, at.UnixNano())
		state.started = float64(at.UnixNano()) / 1e9
		state.rotate = false
		c.familyEpochs[family] = state
	}
	return HostFactContext{EpochID: state.epochID, EpochStartedAt: state.started, ObservedAt: float64(at.UnixNano()) / 1e9, CadenceSeconds: cadence.Seconds(), OwnerGeneration: c.config.OwnerGeneration, SourceID: c.config.SourceID}
}

func (c *HostCollector) finishFamilyBatch(family string, fact HostFactContext, batch AppendBatch) AppendBatch {
	c.stateMu.Lock()
	defer c.stateMu.Unlock()
	state := c.familyEpochs[family]
	if len(batch.UnavailableSpans) == 0 {
		if len(batch.Observations) > 0 {
			state.hadSuccess = true
			c.familyEpochs[family] = state
		}
		return batch
	}
	// Close the current epoch at the unavailable interval's start. The writer
	// applies this update before checking the unavailable span for overlap.
	if state.hadSuccess {
		ended := fact.ObservedAt
		batch.CoverageEpochs = append(batch.CoverageEpochs, CoverageEpoch{
			Family: family, SourceID: fact.SourceID, EpochID: fact.EpochID,
			StartedAt: fact.EpochStartedAt, EndedAt: &ended,
			NativeCadenceSeconds: fact.CadenceSeconds, OwnerGeneration: fact.OwnerGeneration,
		})
	}
	state.rotate = true
	c.familyEpochs[family] = state
	return batch
}

func (c *HostCollector) familyCadence(family string) time.Duration {
	switch family {
	case "system_memory":
		return c.config.MemoryCadence
	case "gpu":
		return c.config.GPUActiveCadence
	default:
		return c.config.ActiveCadence
	}
}

func (c *HostCollector) familyIdleCadence(family string) time.Duration {
	if family == "gpu" {
		return c.config.GPUIdleCadence
	}
	if family == "system_memory" {
		return c.config.MemoryCadence
	}
	return c.config.IdleCadence
}

func (c *HostCollector) append(ctx context.Context, batch AppendBatch) {
	if c.writer == nil {
		c.counters.errors.Add(1)
		return
	}
	result, err := c.writer.AppendBatch(ctx, batch)
	if err != nil {
		c.counters.errors.Add(1)
		return
	}
	c.counters.accepted.Add(uint64(result.ObservationsAccepted))
	c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
}

func (c *HostCollector) collectCPU(parent context.Context, fact HostFactContext, sampleDuration time.Duration) AppendBatch {
	ctx, cancel := context.WithTimeout(parent, c.config.SourceTimeout)
	defer cancel()
	snapshot, err := c.sources.CPU(ctx)
	if err != nil {
		c.counters.errors.Add(1)
		return BuildUnavailableFacts("cpu", fact, sourceReason(ctx, err))
	}
	sample := c.sampler.Sample(snapshot, sampleDuration)
	return BuildCPUFacts(sample, sample.ProcessCPUPercent, fact)
}

func (c *HostCollector) collectMemory(parent context.Context, fact HostFactContext) AppendBatch {
	ctx, cancel := context.WithTimeout(parent, c.config.SourceTimeout)
	defer cancel()
	memory, rows, err := c.sources.Memory(ctx)
	if err != nil {
		c.counters.errors.Add(1)
		return BuildUnavailableFacts("system_memory", fact, sourceReason(ctx, err))
	}
	return BuildSystemMemoryFacts(memory, rows, nil, fact)
}

func (c *HostCollector) collectGPU(parent context.Context, fact HostFactContext) AppendBatch {
	ctx, cancel := context.WithTimeout(parent, c.config.SourceTimeout)
	defer cancel()
	devices, err := c.sources.GPU(ctx)
	if err != nil {
		c.counters.errors.Add(1)
		return BuildUnavailableFacts("gpu", fact, sourceReason(ctx, err))
	}
	return BuildGPUFacts(devices, fact)
}

func sourceReason(ctx context.Context, err error) string {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return "deadline_exceeded"
	}
	if err == nil {
		return "source_unavailable"
	}
	return "source_error"
}

func appendBatch(result AppendBatch, batches ...AppendBatch) AppendBatch {
	for _, batch := range batches {
		result.Observations = append(result.Observations, batch.Observations...)
		result.CoverageEpochs = append(result.CoverageEpochs, batch.CoverageEpochs...)
		result.UnavailableSpans = append(result.UnavailableSpans, batch.UnavailableSpans...)
	}
	return result
}
