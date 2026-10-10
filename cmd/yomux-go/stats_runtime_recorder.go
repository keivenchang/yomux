package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

const maxRuntimeRecorderPendingBatches = 8
const maxRuntimeRecorderPendingUnavailable = 64
const runtimeRecorderStopFlushTimeout = 2 * time.Second
const runtimeRecorderOverflowReason = "runtime_recorder_queue_overflow"

// statsRuntimeRecorder persists the in-process Daemons load independently of
// browser demand. The HTTP snapshot/SSE path may still add a fresh live sample,
// but this owner is what makes the history survive a closed YO!stats panel.
type statsRuntimeRecorder struct {
	writer          stats.BatchWriter
	sample          func(time.Time) []stats.ServiceLoadSample
	now             func() time.Time
	cadence         time.Duration
	epochID         string
	epochStarted    float64
	epochSequence   uint64
	overflowEpochID string
	owner           uint64

	mu   sync.Mutex
	run  bool
	stop context.CancelFunc
	done chan struct{}

	pendingMu          sync.Mutex
	pendingBatches     []stats.AppendBatch
	pendingUnavailable []stats.UnavailableSpan
	metricsMu          sync.Mutex
	lastAppendErr      string
	runs               atomic.Uint64
	appendSuccess      atomic.Uint64
	appendErrors       atomic.Uint64
	droppedBatches     atomic.Uint64
}

// diagnostics returns recorder health without exposing sample payloads. The
// queue counts make a live persistence failure distinguishable from a quiet
// recorder, while the last error remains bounded and operator-readable.
func (r *statsRuntimeRecorder) diagnostics() map[string]any {
	if r == nil {
		return map[string]any{"available": false, "reason": "runtime_recorder_unavailable"}
	}
	r.pendingMu.Lock()
	pendingBatches := len(r.pendingBatches)
	pendingObservations := 0
	pendingCoverage := 0
	pendingUnavailable := 0
	for _, batch := range r.pendingBatches {
		pendingObservations += len(batch.Observations)
		pendingCoverage += len(batch.CoverageEpochs)
		pendingUnavailable += len(batch.UnavailableSpans)
	}
	pendingUnavailable += len(r.pendingUnavailable)
	r.pendingMu.Unlock()
	r.metricsMu.Lock()
	lastError := r.lastAppendErr
	r.metricsMu.Unlock()
	result := map[string]any{
		"available":            true,
		"runs":                 r.runs.Load(),
		"append_successes":     r.appendSuccess.Load(),
		"append_errors":        r.appendErrors.Load(),
		"dropped_batches":      r.droppedBatches.Load(),
		"pending_batches":      pendingBatches,
		"pending_observations": pendingObservations,
		"pending_coverage":     pendingCoverage,
		"pending_unavailable":  pendingUnavailable,
	}
	if lastError != "" {
		result["last_append_error"] = lastError
	}
	return result
}

func (r *statsRuntimeRecorder) recordAppendError(err error) {
	r.appendErrors.Add(1)
	message := "append failed"
	if err != nil {
		message = err.Error()
	}
	if len(message) > 512 {
		message = message[:512]
	}
	r.metricsMu.Lock()
	r.lastAppendErr = message
	r.metricsMu.Unlock()
}

func newStatsRuntimeRecorder(writer stats.BatchWriter, sample func(time.Time) []stats.ServiceLoadSample, now func() time.Time, cadence time.Duration, epochID string, owner uint64) *statsRuntimeRecorder {
	if now == nil {
		now = time.Now
	}
	if cadence <= 0 {
		cadence = time.Second
	}
	started := now()
	return &statsRuntimeRecorder{
		writer: writer, sample: sample, now: now, cadence: cadence,
		epochID: epochID, epochStarted: float64(started.UnixNano()) / 1e9, owner: owner,
	}
}

func (r *statsRuntimeRecorder) Start(parent context.Context) error {
	if r == nil || r.writer == nil || r.sample == nil {
		return errors.New("runtime recorder requires a writer and sample function")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.run {
		return errors.New("runtime recorder already started")
	}
	ctx, cancel := context.WithCancel(parent)
	r.stop, r.done, r.run = cancel, make(chan struct{}), true
	go r.loop(ctx)
	return nil
}

func (r *statsRuntimeRecorder) loop(ctx context.Context) {
	defer func() {
		r.mu.Lock()
		r.run = false
		close(r.done)
		r.mu.Unlock()
	}()
	// Capture immediately so a server that is already doing work has a durable
	// sample before the first cadence boundary, then continue independently of
	// statsDemand and of any browser/SSE connection.
	r.RunOnce(ctx)
	ticker := time.NewTicker(r.cadence)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			r.RunOnce(ctx)
		}
	}
}

func (r *statsRuntimeRecorder) Stop() error {
	if r == nil {
		return nil
	}
	r.mu.Lock()
	running := r.run
	stop, done := r.stop, r.done
	r.mu.Unlock()
	if running {
		stop()
		<-done
	}
	// A transient writer failure leaves the batch in memory for the next
	// cadence. Drain that queue on every graceful stop, including a recorder
	// that was never started, so shutdown does not silently discard the last
	// measured Daemons sample.
	flushCtx, cancel := context.WithTimeout(context.Background(), runtimeRecorderStopFlushTimeout)
	err := r.flushPending(flushCtx)
	cancel()
	if err != nil {
		slog.Warn("stats runtime recorder shutdown flush failed", "error", err)
	}
	return err
}

func (r *statsRuntimeRecorder) flushPending(ctx context.Context) error {
	if r == nil {
		return errors.New("runtime recorder writer is unavailable")
	}
	r.pendingMu.Lock()
	defer r.pendingMu.Unlock()
	if len(r.pendingBatches) == 0 && len(r.pendingUnavailable) == 0 {
		return nil
	}
	if r.writer == nil {
		return errors.New("runtime recorder writer is unavailable")
	}
	batch := r.pendingBatchLocked()
	if _, err := r.writer.AppendBatch(ctx, batch); err != nil {
		r.recordAppendError(err)
		return err
	}
	r.pendingBatches = nil
	r.pendingUnavailable = nil
	r.overflowEpochID = ""
	r.appendSuccess.Add(1)
	return nil
}

func (r *statsRuntimeRecorder) RunOnce(ctx context.Context) {
	if r == nil || r.writer == nil || r.sample == nil {
		return
	}
	r.runs.Add(1)
	at := r.now()
	samples := r.sample(at)
	// Source order is intentionally normalized so synthetic fixtures and
	// duplicate/replayed appends have stable request shapes.
	sortServiceLoadSamples(samples)
	factContext := stats.HostFactContext{
		EpochID: r.epochID, EpochStartedAt: r.epochStarted,
		ObservedAt: float64(at.UnixNano()) / 1e9, CadenceSeconds: r.cadence.Seconds(),
		OwnerGeneration: r.owner, SourceID: "runtime",
	}
	current := stats.BuildServiceLoadFacts(samples, factContext)
	if len(current.Observations) == 0 && len(current.UnavailableSpans) == 0 {
		// A recorder cadence with no usable process/runtime sample is a measured
		// gap, not an implicit zero and not permission to leave stale history
		// looking current. Persist the same explicit unavailable contract used by
		// the host collectors so the browser can explain the missing interval.
		current = stats.BuildUnavailableFacts("service_load", factContext, "runtime_sample_unavailable")
	}
	r.pendingMu.Lock()
	defer r.pendingMu.Unlock()
	if len(current.Observations) > 0 || len(current.CoverageEpochs) > 0 || len(current.UnavailableSpans) > 0 {
		if len(r.pendingBatches) < maxRuntimeRecorderPendingBatches {
			r.pendingBatches = append(r.pendingBatches, current)
		} else {
			// Keep the oldest window and persist an explicit unavailable span before
			// a later observation can extend coverage past this dropped cadence.
			r.recordDroppedBatchLocked(current, factContext.ObservedAt, factContext.CadenceSeconds)
			slog.Warn("stats runtime recorder pending queue full", "dropped_newest_batch", true)
			r.droppedBatches.Add(1)
		}
	}
	if len(r.pendingBatches) == 0 && len(r.pendingUnavailable) == 0 {
		return
	}
	batch := r.pendingBatchLocked()
	if _, err := r.writer.AppendBatch(ctx, batch); err != nil {
		r.recordAppendError(err)
		slog.Warn("stats runtime recorder append failed", "error", err)
		return
	}
	r.pendingBatches = nil
	r.pendingUnavailable = nil
	r.overflowEpochID = ""
	r.appendSuccess.Add(1)
}

func (r *statsRuntimeRecorder) pendingBatchLocked() stats.AppendBatch {
	batches := make([]stats.AppendBatch, 0, len(r.pendingBatches)+1)
	batches = append(batches, r.pendingBatches...)
	if len(r.pendingUnavailable) > 0 {
		batches = append(batches, stats.AppendBatch{UnavailableSpans: append([]stats.UnavailableSpan(nil), r.pendingUnavailable...)})
	}
	return mergeRuntimeRecorderBatches(batches)
}

func (r *statsRuntimeRecorder) recordDroppedBatchLocked(batch stats.AppendBatch, observedAt, cadence float64) {
	if r.overflowEpochID == "" {
		r.overflowEpochID = r.epochID
		r.epochSequence++
		suffix := ":gap-" + strconv.FormatUint(r.epochSequence, 10)
		baseLimit := stats.MaxSeriesComponentBytes - len(suffix)
		base := r.epochID
		if len(base) > baseLimit {
			base = base[:baseLimit]
		}
		r.epochID = base + suffix
	}
	end := observedAt + cadence
	bySource := make(map[string]float64, len(batch.Observations))
	for _, observation := range batch.Observations {
		bySource[observation.Family+"\x00"+observation.SourceID] = observation.ObservedAt
	}
	for _, epoch := range batch.CoverageEpochs {
		start, ok := bySource[epoch.Family+"\x00"+epoch.SourceID]
		if !ok {
			continue
		}
		spanEnd := start + cadence
		if epoch.EndedAt != nil {
			spanEnd = *epoch.EndedAt
		}
		if spanEnd <= start {
			continue
		}
		r.appendPendingOverflowSpanLocked(stats.UnavailableSpan{
			Family: epoch.Family, SourceID: epoch.SourceID, EpochID: r.overflowEpochID,
			StartedAt: start, EndedAt: spanEnd, NativeCadenceSeconds: epoch.NativeCadenceSeconds,
			Reason: runtimeRecorderOverflowReason, OwnerGeneration: epoch.OwnerGeneration,
		})
		if spanEnd > end {
			end = spanEnd
		}
	}
	for _, span := range batch.UnavailableSpans {
		span.EpochID = r.overflowEpochID
		span.Reason = runtimeRecorderOverflowReason
		r.appendPendingOverflowSpanLocked(span)
		if span.EndedAt > end {
			end = span.EndedAt
		}
	}
	r.epochStarted = end
}

func (r *statsRuntimeRecorder) appendPendingOverflowSpanLocked(span stats.UnavailableSpan) {
	if len(r.pendingUnavailable) >= maxRuntimeRecorderPendingUnavailable-1 {
		knownSource := false
		for _, previous := range r.pendingUnavailable {
			if previous.SourceID == span.SourceID {
				knownSource = true
				break
			}
		}
		if !knownSource {
			// Process and thread identities can churn throughout an outage. Keep
			// the admitted source gaps and reserve one slot for the recorder-wide
			// missing interval, so discarded identities never grow this queue or
			// silently erase evidence that history was unavailable.
			span.SourceID = "runtime"
		}
	}
	for index := range r.pendingUnavailable {
		previous := &r.pendingUnavailable[index]
		if previous.Family != span.Family || previous.SourceID != span.SourceID || previous.EpochID != span.EpochID ||
			previous.NativeCadenceSeconds != span.NativeCadenceSeconds || previous.OwnerGeneration != span.OwnerGeneration ||
			previous.Reason != span.Reason {
			continue
		}
		if span.StartedAt < previous.StartedAt {
			previous.StartedAt = span.StartedAt
		}
		if span.EndedAt > previous.EndedAt {
			previous.EndedAt = span.EndedAt
		}
		return
	}
	r.pendingUnavailable = append(r.pendingUnavailable, span)
}

func mergeRuntimeRecorderBatches(batches []stats.AppendBatch) stats.AppendBatch {
	var merged stats.AppendBatch
	coverage := make(map[string]stats.CoverageEpoch)
	coverageOrder := make([]string, 0)
	for _, batch := range batches {
		merged.Observations = append(merged.Observations, batch.Observations...)
		for _, epoch := range batch.CoverageEpochs {
			key := fmt.Sprintf("%s\x00%s\x00%s\x00%s", epoch.Family, epoch.SourceID, epoch.EpochID, strconv.FormatFloat(epoch.StartedAt, 'g', -1, 64))
			previous, exists := coverage[key]
			if !exists {
				coverageOrder = append(coverageOrder, key)
				coverage[key] = epoch
				continue
			}
			if previous.EndedAt == nil || (epoch.EndedAt != nil && *epoch.EndedAt > *previous.EndedAt) {
				previous.EndedAt = epoch.EndedAt
				coverage[key] = previous
			}
		}
		merged.UnavailableSpans = append(merged.UnavailableSpans, batch.UnavailableSpans...)
		merged.UsageAtoms = append(merged.UsageAtoms, batch.UsageAtoms...)
		merged.UsageTombstones = append(merged.UsageTombstones, batch.UsageTombstones...)
	}
	for _, key := range coverageOrder {
		merged.CoverageEpochs = append(merged.CoverageEpochs, coverage[key])
	}
	merged.UnavailableSpans = coalesceRuntimeRecorderUnavailable(merged.UnavailableSpans)
	return merged
}

func coalesceRuntimeRecorderUnavailable(spans []stats.UnavailableSpan) []stats.UnavailableSpan {
	if len(spans) < 2 {
		return spans
	}
	sort.SliceStable(spans, func(left, right int) bool {
		first, second := spans[left], spans[right]
		if first.Family != second.Family {
			return first.Family < second.Family
		}
		if first.SourceID != second.SourceID {
			return first.SourceID < second.SourceID
		}
		if first.EpochID != second.EpochID {
			return first.EpochID < second.EpochID
		}
		if first.NativeCadenceSeconds != second.NativeCadenceSeconds {
			return first.NativeCadenceSeconds < second.NativeCadenceSeconds
		}
		if first.OwnerGeneration != second.OwnerGeneration {
			return first.OwnerGeneration < second.OwnerGeneration
		}
		if first.StartedAt != second.StartedAt {
			return first.StartedAt < second.StartedAt
		}
		return first.EndedAt < second.EndedAt
	})
	coalesced := make([]stats.UnavailableSpan, 0, len(spans))
	for _, span := range spans {
		if len(coalesced) > 0 {
			previous := &coalesced[len(coalesced)-1]
			if previous.Family == span.Family && previous.SourceID == span.SourceID &&
				previous.EpochID == span.EpochID && previous.NativeCadenceSeconds == span.NativeCadenceSeconds &&
				previous.OwnerGeneration == span.OwnerGeneration && span.StartedAt <= previous.EndedAt {
				if span.EndedAt > previous.EndedAt {
					previous.EndedAt = span.EndedAt
				}
				if span.Reason < previous.Reason {
					previous.Reason = span.Reason
				}
				continue
			}
		}
		coalesced = append(coalesced, span)
	}
	return coalesced
}

func sortServiceLoadSamples(samples []stats.ServiceLoadSample) {
	for i := 1; i < len(samples); i++ {
		for j := i; j > 0 && samples[j].SourceID < samples[j-1].SourceID; j-- {
			samples[j], samples[j-1] = samples[j-1], samples[j]
		}
	}
}
