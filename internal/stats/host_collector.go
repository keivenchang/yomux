package stats

import (
	"context"
	"errors"
	"fmt"
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

// HostDiskSources is optional so older and synthetic host adapters can omit
// disk telemetry without changing their unrelated collection contracts.
type HostDiskSources interface {
	Disk(context.Context) ([]DiskUsageSample, error)
}

// HostCPUTopology supplies the system-wide logical CPU count used to normalize
// grouped process CPU. Sources that cannot measure it leave the series absent
// instead of substituting the process affinity count.
type HostCPUTopology interface {
	SystemLogicalCPUs() int
}

// HostProcessTickRate supplies the measured unit for platform process CPU
// counters when another runtime view needs to interpret the same /proc rows.
type HostProcessTickRate interface {
	ProcessTicksPerSecond() uint64
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

// waitCollectorCadence waits for the next collection deadline while allowing
// a demand transition to wake an idle collector immediately. Without the
// bounded idle polls, a collector that selected its 60-second idle cadence
// before a browser opened stats would remain asleep for the whole interval.
// The polling is only a timer wakeup; it never performs a collection while
// demand remains absent.
func waitCollectorCadence(ctx context.Context, clock HostCollectorClock, active, idle time.Duration, activeFunc func() bool) bool {
	if active <= 0 {
		active = time.Second
	}
	if idle <= 0 {
		idle = active
	}
	if activeFunc == nil || activeFunc() {
		timer := clock.NewTimer(active)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return false
		case <-timer.C():
			return true
		}
	}
	remaining := idle
	for remaining > 0 {
		delay := remaining
		if delay > time.Second {
			delay = time.Second
		}
		timer := clock.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return false
		case <-timer.C():
		}
		remaining -= delay
		if activeFunc() {
			return true
		}
	}
	return true
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
	DiskCadence      time.Duration
	SourceTimeout    time.Duration
	EpochID          string
	EpochStartedAt   float64
	OwnerGeneration  uint64
	SourceID         string
	ProcessKey       string
	// ProcessIdentity is the verified PID/start-time identity of the owned
	// daemon. When set, CPU daemon series exclude same-named co-tenants.
	ProcessIdentity string
	GroupKeys       []string
	LogicalCPUs     int
	ActiveFunc      func() bool
}

const (
	DefaultHostCPUBackgroundCadence = 5 * time.Second
	DefaultHostMemoryCadence        = time.Minute
	DefaultHostDiskCadence          = 5 * time.Minute
)

func (c HostCollectorConfig) withDefaults() HostCollectorConfig {
	if c.ActiveCadence <= 0 {
		c.ActiveCadence = time.Second
	}
	if c.IdleCadence <= 0 {
		c.IdleCadence = DefaultHostCPUBackgroundCadence
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
	if c.DiskCadence <= 0 {
		c.DiskCadence = DefaultHostDiskCadence
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
	Cycles                uint64
	AcceptedObservations  uint64
	UnavailableSpans      uint64
	Errors                uint64
	LastDuration          time.Duration
	DurationTotal         time.Duration
	LastCPUProcessRows    uint64
	LastMemoryProcessRows uint64
	Family                map[string]CollectorTiming
}

// CollectorTiming is a runtime-only cost measure. It records wall time spent
// inside one collector owner, including bounded source reads and its append.
type CollectorTiming struct {
	Runs                uint64
	DurationTotal       time.Duration
	LastDuration        time.Duration
	SourceDurationTotal time.Duration
	LastSourceDuration  time.Duration
}

type hostCollectorCounters struct {
	cycles, accepted, unavailable, errors atomic.Uint64
	lastDuration                          atomic.Int64
	durationTotal                         atomic.Uint64
	lastCPUProcessRows                    atomic.Uint64
	lastMemoryProcessRows                 atomic.Uint64
	family                                [4]hostFamilyTiming
}

type hostFamilyTiming struct {
	runs, durationTotal, sourceDurationTotal atomic.Uint64
	lastDuration, lastSourceDuration         atomic.Int64
}

func (c *hostCollectorCounters) snapshot() HostCollectorCounters {
	family := make(map[string]CollectorTiming, len(hostCollectorFamilies))
	for index, name := range hostCollectorFamilies {
		family[name] = CollectorTiming{
			Runs: c.family[index].runs.Load(), DurationTotal: time.Duration(c.family[index].durationTotal.Load()), LastDuration: time.Duration(c.family[index].lastDuration.Load()),
			SourceDurationTotal: time.Duration(c.family[index].sourceDurationTotal.Load()), LastSourceDuration: time.Duration(c.family[index].lastSourceDuration.Load()),
		}
	}
	return HostCollectorCounters{
		Cycles: c.cycles.Load(), AcceptedObservations: c.accepted.Load(), UnavailableSpans: c.unavailable.Load(), Errors: c.errors.Load(),
		LastDuration: time.Duration(c.lastDuration.Load()), DurationTotal: time.Duration(c.durationTotal.Load()),
		LastCPUProcessRows: c.lastCPUProcessRows.Load(), LastMemoryProcessRows: c.lastMemoryProcessRows.Load(), Family: family,
	}
}

var hostCollectorFamilies = [...]string{"cpu", "system_memory", "system_disk", "gpu"}

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
	latestMu     sync.RWMutex
	latest       HostTelemetry
	mu           sync.Mutex
	run          bool
	stop         context.CancelFunc
	done         chan struct{}
}

// HostTelemetry is the latest measured host sample retained by the collector.
// It is a read-only status projection; durable history remains owned by the
// stats writer/materializer. Valid flags are explicit so an initial CPU sample
// or a failed source read cannot be mistaken for measured zero.
type HostTelemetry struct {
	CPUPercent          float64
	SystemCPUPercent    float64
	CPUValid            bool
	CPUObservedAt       float64
	CPUReason           string
	ProcessCPUPercent   map[string]float64
	MemoryUsedBytes     uint64
	MemoryCapacityBytes uint64
	MemoryValid         bool
	MemoryObservedAt    float64
	MemoryReason        string
	ProcessMemoryBytes  map[string]uint64
}

type hostEpochState struct {
	epochID    string
	started    float64
	cadence    float64
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
		if topology, ok := sources.(HostCPUTopology); ok {
			logicalCPUs = topology.SystemLogicalCPUs()
		}
	}
	var sampler *CPUDeltaSampler
	if logicalCPUs > 0 {
		sampler = NewCPUDeltaSampler(config.ProcessKey, config.GroupKeys, logicalCPUs)
	} else {
		sampler = NewCPUDeltaSampler(config.ProcessKey, config.GroupKeys)
	}
	sampler.processIdentity = config.ProcessIdentity
	return &HostCollector{sources: sources, writer: writer, clock: clock, config: config, sampler: sampler, familyEpochs: make(map[string]hostEpochState)}
}

func (c *HostCollector) Counters() HostCollectorCounters { return c.counters.snapshot() }

// FamilyCadence returns the configured native cadence for one host collector
// family. It is a read-only projection used by runtime diagnostics to express
// the family's measured wall-time occupancy without exposing collector state.
func (c *HostCollector) FamilyCadence(family string) time.Duration {
	if c == nil {
		return 0
	}
	return c.familyCadence(family)
}

// FamilyDemanded reports whether a host family currently runs. CPU, memory,
// and disk keep background history; GPU remains gated by stats-view demand.
func (c *HostCollector) FamilyDemanded(family string) bool {
	if c == nil {
		return false
	}
	if family != "gpu" || c.config.ActiveFunc == nil {
		return true
	}
	return c.config.ActiveFunc()
}

// Latest returns a defensive copy of the newest host samples. Callers may use
// it to build a lightweight status response without reading the host again.
func (c *HostCollector) Latest() HostTelemetry {
	c.latestMu.RLock()
	defer c.latestMu.RUnlock()
	latest := c.latest
	latest.ProcessCPUPercent = cloneFloatMap(latest.ProcessCPUPercent)
	latest.ProcessMemoryBytes = cloneUintMap(latest.ProcessMemoryBytes)
	return latest
}

func cloneFloatMap(values map[string]float64) map[string]float64 {
	if len(values) == 0 {
		return nil
	}
	copyValues := make(map[string]float64, len(values))
	for key, value := range values {
		copyValues[key] = value
	}
	return copyValues
}

func cloneUintMap(values map[string]uint64) map[string]uint64 {
	if len(values) == 0 {
		return nil
	}
	copyValues := make(map[string]uint64, len(values))
	for key, value := range values {
		copyValues[key] = value
	}
	return copyValues
}

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
	workers.Add(4)
	go func() {
		defer workers.Done()
		runWithStatsProfileLabels(ctx, "host_collector", "cpu", func(ctx context.Context) {
			c.familyLoop(ctx, "cpu", c.config.ActiveCadence, c.config.IdleCadence)
		})
	}()
	go func() {
		defer workers.Done()
		runWithStatsProfileLabels(ctx, "host_collector", "system_memory", func(ctx context.Context) {
			c.familyLoop(ctx, "system_memory", c.config.MemoryCadence, c.config.MemoryCadence)
		})
	}()
	go func() {
		defer workers.Done()
		runWithStatsProfileLabels(ctx, "host_collector", "system_disk", func(ctx context.Context) {
			c.familyLoop(ctx, "system_disk", c.config.DiskCadence, c.config.DiskCadence)
		})
	}()
	go func() {
		defer workers.Done()
		runWithStatsProfileLabels(ctx, "host_collector", "gpu", func(ctx context.Context) {
			c.familyLoop(ctx, "gpu", c.config.GPUActiveCadence, c.config.GPUIdleCadence)
		})
	}()
	workers.Wait()
}

func (c *HostCollector) familyLoop(ctx context.Context, family string, active, idle time.Duration) {
	first := true
	for {
		if !first {
			if !waitCollectorCadence(ctx, c.clock, active, idle, c.config.ActiveFunc) {
				return
			}
		}
		first = false
		if ctx.Err() != nil {
			return
		}
		// GPU remains demand-driven. CPU keeps a low-rate background history so
		// long-range charts do not depend on someone leaving YO!stats open.
		if family == "gpu" && c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
			continue
		}
		c.runFamily(ctx, family)
	}
}

func (c *HostCollector) runFamily(parent context.Context, family string) {
	c.runFamilyWithCadence(parent, family, c.familyCadence(family))
}

func (c *HostCollector) runFamilyWithCadence(parent context.Context, family string, cadence time.Duration) {
	startedWall := time.Now()
	var sourceStarted time.Time
	var sourceDuration time.Duration
	defer func() {
		elapsed := time.Since(startedWall)
		c.counters.durationTotal.Add(uint64(nonNegativeCollectorDuration(elapsed).Nanoseconds()))
		for index, name := range hostCollectorFamilies {
			if name != family {
				continue
			}
			c.counters.family[index].runs.Add(1)
			c.counters.family[index].durationTotal.Add(uint64(nonNegativeCollectorDuration(elapsed).Nanoseconds()))
			c.counters.family[index].lastDuration.Store(elapsed.Nanoseconds())
			c.counters.family[index].sourceDurationTotal.Add(uint64(nonNegativeCollectorDuration(sourceDuration).Nanoseconds()))
			c.counters.family[index].lastSourceDuration.Store(sourceDuration.Nanoseconds())
			break
		}
	}()
	started := c.clock.Now()
	if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
		if family != "system_memory" {
			cadence = c.familyIdleCadence(family)
		}
	}
	c.stateMu.Lock()
	previousEpoch, hadPreviousEpoch := c.familyEpochs[family]
	c.stateMu.Unlock()
	fact := c.familyFactContext(family, started, cadence)
	var batch AppendBatch
	var cpuSampled bool
	var samplerBefore *CPUDeltaSampler
	var lastSampleBefore time.Time
	if family == "cpu" {
		samplerBefore = c.sampler.clone()
		lastSampleBefore = c.lastSample
	}
	sourceStarted = time.Now()
	runWithStatsProfilePhase(parent, "host_collector", family, "source", func(ctx context.Context) {
		if c.sources == nil {
			c.counters.errors.Add(1)
			batch = BuildUnavailableFacts(family, fact, "source_unavailable")
			return
		}
		switch family {
		case "cpu":
			batch, cpuSampled = c.collectCPU(ctx, fact, started.Sub(lastSampleBefore))
		case "system_memory":
			batch = c.collectMemory(ctx, fact)
		case "system_disk":
			batch = c.collectDisk(ctx, fact)
		case "gpu":
			batch = c.collectGPU(ctx, fact)
		}
	})
	sourceDuration = time.Since(sourceStarted)
	runWithStatsProfilePhase(parent, "host_collector", family, "append", func(ctx context.Context) {
		batch = c.finishFamilyBatch(family, fact, batch)
		if c.append(ctx, batch) {
			if family == "cpu" && cpuSampled {
				c.lastSample = started
			}
			return
		}
		if family == "cpu" {
			c.sampler.restore(samplerBefore)
			c.lastSample = lastSampleBefore
		}
		c.stateMu.Lock()
		if hadPreviousEpoch {
			c.familyEpochs[family] = previousEpoch
		} else {
			delete(c.familyEpochs, family)
		}
		c.stateMu.Unlock()
	})
	if family == "cpu" {
		c.counters.cycles.Add(1)
	}
}

func nonNegativeCollectorDuration(value time.Duration) time.Duration {
	if value < 0 {
		return 0
	}
	return value
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
	c.runFamilyWithCadence(parent, "system_disk", c.config.DiskCadence)
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
	if state.cadence != 0 && state.cadence != cadence.Seconds() {
		state.rotate = true
	}
	if state.rotate {
		state.epochID = fmt.Sprintf("%s-%s-%d", c.config.EpochID, family, at.UnixNano())
		state.started = float64(at.UnixNano()) / 1e9
		state.rotate = false
	}
	state.cadence = cadence.Seconds()
	c.familyEpochs[family] = state
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
	case "system_disk":
		return c.config.DiskCadence
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
	if family == "system_disk" {
		return c.config.DiskCadence
	}
	return c.config.IdleCadence
}

func (c *HostCollector) collectDisk(parent context.Context, fact HostFactContext) AppendBatch {
	source, ok := c.sources.(HostDiskSources)
	if !ok {
		return AppendBatch{}
	}
	// Bound the single df invocation without changing the disk sampling cadence.
	ctx, cancel := context.WithTimeout(parent, 12*time.Second)
	defer cancel()
	samples, err := source.Disk(ctx)
	batch := BuildSystemDiskFacts(samples, fact)
	if err != nil {
		c.counters.errors.Add(1)
		unavailable := BuildUnavailableFacts("system_disk", fact, sourceReason(ctx, err))
		batch.UnavailableSpans = append(batch.UnavailableSpans, unavailable.UnavailableSpans...)
	}
	return batch
}

func (c *HostCollector) append(ctx context.Context, batch AppendBatch) bool {
	if c.writer == nil {
		c.counters.errors.Add(1)
		return false
	}
	result, err := c.writer.AppendBatch(ctx, batch)
	if err != nil {
		c.counters.errors.Add(1)
		return false
	}
	c.counters.accepted.Add(uint64(result.ObservationsAccepted))
	c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
	return true
}

func (c *HostCollector) collectCPU(parent context.Context, fact HostFactContext, sampleDuration time.Duration) (AppendBatch, bool) {
	ctx, cancel := context.WithTimeout(parent, c.config.SourceTimeout)
	defer cancel()
	snapshot, err := c.sources.CPU(ctx)
	if err != nil {
		c.counters.errors.Add(1)
		reason := sourceReason(ctx, err)
		c.latestMu.Lock()
		c.latest.CPUObservedAt = fact.ObservedAt
		c.latest.CPUValid = false
		c.latest.CPUReason = reason
		c.latest.ProcessCPUPercent = nil
		c.latestMu.Unlock()
		return BuildUnavailableFacts("cpu", fact, reason), false
	}
	c.counters.lastCPUProcessRows.Store(uint64(len(snapshot.Processes)))
	sample := c.sampler.Sample(snapshot, sampleDuration)
	c.latestMu.Lock()
	c.latest.CPUObservedAt = fact.ObservedAt
	c.latest.CPUValid = sample.Reason == CPUValid && sample.Host.Valid && sample.Process.Valid
	c.latest.CPUReason = ""
	c.latest.ProcessCPUPercent = nil
	if !c.latest.CPUValid {
		c.latest.CPUReason = string(sample.Reason)
	} else {
		c.latest.CPUPercent = sample.Process.Value
		c.latest.SystemCPUPercent = sample.Host.Value
		c.latest.ProcessCPUPercent = cloneFloatMap(sample.ProcessCPUPercent)
	}
	c.latestMu.Unlock()
	return BuildCPUFacts(sample, sample.ProcessCPUPercent, fact), true
}

func (c *HostCollector) collectMemory(parent context.Context, fact HostFactContext) AppendBatch {
	ctx, cancel := context.WithTimeout(parent, c.config.SourceTimeout)
	defer cancel()
	memory, rows, err := c.sources.Memory(ctx)
	if err != nil {
		c.counters.errors.Add(1)
		c.latestMu.Lock()
		c.latest.MemoryObservedAt = fact.ObservedAt
		c.latest.MemoryValid = false
		c.latest.MemoryReason = sourceReason(ctx, err)
		c.latest.ProcessMemoryBytes = nil
		c.latestMu.Unlock()
		return BuildUnavailableFacts("system_memory", fact, sourceReason(ctx, err))
	}
	c.counters.lastMemoryProcessRows.Store(uint64(len(rows)))
	c.latestMu.Lock()
	c.latest.MemoryObservedAt = fact.ObservedAt
	c.latest.MemoryValid = memory.CapacityBytes > 0 && memory.UsedBytes <= memory.CapacityBytes
	c.latest.MemoryReason = ""
	c.latest.ProcessMemoryBytes = nil
	if c.latest.MemoryValid {
		c.latest.MemoryUsedBytes = memory.UsedBytes
		c.latest.MemoryCapacityBytes = memory.CapacityBytes
		for _, row := range topProcessMemory(rows) {
			if row.Binary != "" && row.RSSBytes > 0 {
				if c.latest.ProcessMemoryBytes == nil {
					c.latest.ProcessMemoryBytes = make(map[string]uint64)
				}
				c.latest.ProcessMemoryBytes[row.Binary] = row.RSSBytes
			}
		}
	} else {
		c.latest.MemoryReason = "invalid_sample"
	}
	c.latestMu.Unlock()
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
