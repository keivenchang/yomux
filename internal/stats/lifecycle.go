package stats

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"
)

// Lifecycle owns the bounded background materialization pass. AppendBatch
// remains synchronous for durability; NotifyAppend only wakes this owner.
type Lifecycle struct {
	writer       *Writer
	config       LifecycleConfig
	materializer Materializer
	trigger      chan struct{}
	mu           sync.Mutex
	stop         context.CancelFunc
	done         chan struct{}
	running      bool
	counters     lifecycleCounters
}

type LifecycleConfig struct {
	Cadence       time.Duration
	WindowSeconds int64
	Price         PriceResolver
	Now           func() time.Time
}

type LifecycleCounters struct {
	Runs, Publications, Repairs, Errors, Prunes, Vacuums uint64
	LastGeneration                                       int64
}
type lifecycleCounters struct {
	runs, publications, repairs, errors, prunes, vacuums atomic.Uint64
	lastGeneration                                       atomic.Int64
}

func (c *lifecycleCounters) snapshot() LifecycleCounters {
	return LifecycleCounters{c.runs.Load(), c.publications.Load(), c.repairs.Load(), c.errors.Load(), c.prunes.Load(), c.vacuums.Load(), c.lastGeneration.Load()}
}

func NewLifecycle(writer *Writer, config LifecycleConfig) *Lifecycle {
	if config.Cadence <= 0 {
		config.Cadence = time.Second
	}
	if config.WindowSeconds <= 0 {
		config.WindowSeconds = RetentionSeconds
	}
	if config.Now == nil {
		config.Now = time.Now
	}
	return &Lifecycle{writer: writer, config: config, materializer: Materializer{Price: config.Price}, trigger: make(chan struct{}, 1)}
}
func (l *Lifecycle) Counters() LifecycleCounters { return l.counters.snapshot() }
func (l *Lifecycle) NotifyAppend() {
	select {
	case l.trigger <- struct{}{}:
	default:
	}
}
func (l *Lifecycle) Start(parent context.Context) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.writer == nil {
		return errors.New("nil stats lifecycle writer")
	}
	if l.running {
		return errors.New("stats lifecycle already started")
	}
	ctx, cancel := context.WithCancel(parent)
	l.stop, l.done, l.running = cancel, make(chan struct{}), true
	go l.loop(ctx)
	return nil
}
func (l *Lifecycle) Stop() {
	l.mu.Lock()
	if !l.running {
		l.mu.Unlock()
		return
	}
	stop, done := l.stop, l.done
	l.mu.Unlock()
	stop()
	<-done
}
func (l *Lifecycle) loop(ctx context.Context) {
	defer func() { l.mu.Lock(); l.running = false; close(l.done); l.mu.Unlock() }()
	ticker := time.NewTicker(l.config.Cadence)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			l.runAndCount(ctx)
		case <-l.trigger:
			l.runAndCount(ctx)
		}
	}
}

func (l *Lifecycle) runAndCount(ctx context.Context) {
	if err := l.RunOnce(ctx); err != nil && !errors.Is(err, context.Canceled) {
		l.counters.errors.Add(1)
	}
}

func (l *Lifecycle) RunOnce(ctx context.Context) error {
	l.counters.runs.Add(1)
	now := l.config.Now()
	end := now.Unix()
	start := end - l.config.WindowSeconds
	database, err := OpenReadOnly(ctx, l.writerPath())
	if err != nil {
		return err
	}
	defer database.Close()
	generation := database.Header.SourceGeneration
	l.counters.lastGeneration.Store(generation)
	for resolution := range ringCapacities {
		if err := l.materializeResolution(ctx, database, start, end, generation, resolution, now); err != nil {
			return err
		}
	}
	return nil
}

func (l *Lifecycle) writerPath() string { return l.writer.lease.path }

func (l *Lifecycle) materializeResolution(ctx context.Context, database *Database, start, end int64, generation int64, resolution int, now time.Time) error {
	end = end / int64(resolution) * int64(resolution)
	start = start / int64(resolution) * int64(resolution)
	obs, err := database.ReadObservations(ctx, float64(start), float64(end), MaxFactReadLimit)
	if err != nil {
		return err
	}
	epochs, err := database.ReadCoverageEpochs(ctx, float64(start), float64(end), MaxFactReadLimit)
	if err != nil {
		return err
	}
	spans, err := database.ReadUnavailableSpans(ctx, float64(start), float64(end), MaxFactReadLimit)
	if err != nil {
		return err
	}
	atoms, err := database.ReadUsageAtoms(ctx, float64(start), float64(end), MaxFactReadLimit)
	if err != nil {
		return err
	}
	layer, err := l.materializer.Build(MaterializeRequest{Start: float64(start), End: float64(end), Resolution: float64(resolution)}, obs, epochs, spans, atoms)
	if err != nil {
		return err
	}
	buckets := make([]RingBucketWrite, 0, len(layer.Buckets))
	for _, bucket := range layer.Buckets {
		payload, err := json.Marshal(bucket)
		if err != nil {
			return fmt.Errorf("encode materialized bucket: %w", err)
		}
		buckets = append(buckets, RingBucketWrite{ResolutionSeconds: resolution, BucketStart: int64(bucket.Start), BucketJSON: string(payload), Complete: bucket.Complete})
	}
	if len(buckets) == 0 {
		return nil
	}
	publication, err := l.writer.PublishRingBuckets(ctx, buckets, generation, float64(now.UnixNano())/1e9, true)
	if err != nil {
		return err
	}
	l.counters.publications.Add(1)
	if pending, pendingErr := l.writer.PendingInvalidationCells(ctx); pendingErr == nil && len(pending) > 0 {
		l.counters.repairs.Add(1)
	}
	_ = publication
	return nil
}

// Prune and Vacuum are explicit maintenance hooks so scheduling policy stays
// outside the fact writer and can be exercised deterministically by services.
func (l *Lifecycle) Prune(ctx context.Context) error { l.counters.prunes.Add(1); return nil }
func (l *Lifecycle) Vacuum(ctx context.Context) error {
	l.counters.vacuums.Add(1)
	return l.writer.Vacuum(ctx, float64(l.config.Now().UnixNano())/1e9)
}
