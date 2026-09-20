// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

// ErrProductPending means that the durable ring does not yet contain a
// complete product for the requested range. Callers should return the bounded
// pending response and wait for the lifecycle publication rather than folding
// SQLite facts on the request path.
var ErrProductPending = errors.New("YO!stats product is pending materialization")

// ProductKey identifies one immutable public ring view.
type ProductKey struct {
	RangeSeconds      int
	ResolutionSeconds int
}

type statsProduct struct {
	key              ProductKey
	windowEnd        int64
	sourceGeneration int64
	ringGeneration   int64
	cacheGeneration  int64
	publishedAt      float64
	snapshot         StatsSnapshot
}

type ringBucketCacheKey struct {
	resolution int
	bucket     int64
}

type ringBucketCacheEntry struct {
	ringGeneration  int64
	bucket          MaterializedBucket
	noData          []MaterializedNoData
	cacheGeneration int64
	generatedAt     float64
	hasMetadata     bool
}

// ServiceState is the lock-free status projection used by readiness and
// diagnostics. It deliberately contains no private fact values.
type ServiceState struct {
	Ready             bool
	PendingProducts   int
	LastError         string
	LastRefreshAt     float64
	SourceGeneration  int64
	CacheGeneration   int64
	PublishedProducts int
}

// StatsRefreshCounters attribute the remaining request-independent projection
// work after lifecycle materialization. In particular, they distinguish ring
// payload reads/decodes from the fact folds measured by LifecycleCounters.
type StatsRefreshCounters struct {
	Runs, Errors, RingReads, RingRows, BucketDecodes, DecodedBytes, ProductsBuilt uint64
	DurationTotal, LastDuration                                                   time.Duration
}

type statsRefreshCounters struct {
	runs, errors, ringReads, ringRows, bucketDecodes, decodedBytes, productsBuilt atomic.Uint64
	durationTotal, lastDuration                                                   atomic.Uint64
}

func (c *statsRefreshCounters) snapshot() StatsRefreshCounters {
	return StatsRefreshCounters{
		Runs: c.runs.Load(), Errors: c.errors.Load(), RingReads: c.ringReads.Load(), RingRows: c.ringRows.Load(),
		BucketDecodes: c.bucketDecodes.Load(), DecodedBytes: c.decodedBytes.Load(), ProductsBuilt: c.productsBuilt.Load(),
		DurationTotal: time.Duration(c.durationTotal.Load()), LastDuration: time.Duration(c.lastDuration.Load()),
	}
}

type StatsServiceConfig struct {
	Products                []ProductKey
	Price                   PriceResolver
	Now                     func() time.Time
	AgentStatus             func(context.Context) (AgentStatusSnapshot, error)
	AgentStatusCadence      time.Duration
	AgentStatusRetryCadence time.Duration
}

// StatsService is the sole owner of materialized read products. Lifecycle
// code calls Refresh after a durable publication; HTTP callers only read the
// immutable products held here.
type StatsService struct {
	writer                  *Writer
	price                   PriceResolver
	now                     func() time.Time
	agentStatus             func(context.Context) (AgentStatusSnapshot, error)
	mu                      sync.RWMutex
	products                map[ProductKey]statsProduct
	pending                 map[ProductKey]string
	state                   ServiceState
	keys                    []ProductKey
	ringBucketCache         map[ringBucketCacheKey]ringBucketCacheEntry
	lastRingGeneration      int64
	lastPublishedAt         float64
	ringStateReady          bool
	lastPendingSignature    uint64
	pendingStateReady       bool
	notify                  chan struct{}
	agentStatusMu           sync.RWMutex
	agentStatusReady        bool
	agentStatusValue        AgentStatusSnapshot
	agentStatusErr          error
	agentStatusNext         time.Time
	agentStatusCadence      time.Duration
	agentStatusRetryCadence time.Duration
	agentStatusRefreshMu    sync.Mutex
	refreshCounters         statsRefreshCounters
}

const (
	// Refreshing the status projection is independent of the one-second ring
	// lifecycle. Keep successful reads aligned with the active collector and
	// retry failures more quickly without creating a SQLite read storm.
	DefaultAgentStatusRefreshCadence = 10 * time.Second
	DefaultAgentStatusRetryCadence   = 1 * time.Second
)

// These are the current Python-owned families. A historical persisted open
// bucket is not allowed to expose its stale values, so the service projects an
// explicit no-data span for every family while retaining the dense window.
var persistedRingGapFamilies = [...]string{
	"cpu",
	"agent_status",
	"gpu",
	"service_load",
	"system_memory",
	"agent_tokens",
	"cost",
	"browser",
}

func persistedRingGapNoData(start int64, resolution int, ringGeneration int64) []MaterializedNoData {
	spans := make([]MaterializedNoData, 0, len(persistedRingGapFamilies))
	for _, family := range persistedRingGapFamilies {
		spans = append(spans, MaterializedNoData{
			Family:               family,
			SourceID:             "persisted-ring",
			EpochID:              fmt.Sprintf("ring-%d", ringGeneration),
			Start:                float64(start),
			End:                  float64(start + int64(resolution)),
			NativeCadenceSeconds: float64(resolution),
			Reason:               "incomplete_persisted_bucket",
		})
	}
	return spans
}

// mergeAdjacentNoData reconstructs the Python ring wire contract after a
// service read. A single source interval is stored on each bucket it touches,
// so decoding a multi-bucket window can produce adjacent fragments. Merge only
// fragments with the same semantic identity; different epochs or reasons must
// remain visible as separate gaps.
func mergeAdjacentNoData(spans []MaterializedNoData) []MaterializedNoData {
	ordered := append([]MaterializedNoData(nil), spans...)
	sort.SliceStable(ordered, func(i, j int) bool {
		left, right := ordered[i], ordered[j]
		if left.Family != right.Family {
			return left.Family < right.Family
		}
		if left.SourceID != right.SourceID {
			return left.SourceID < right.SourceID
		}
		if left.Start != right.Start {
			return left.Start < right.Start
		}
		if left.End != right.End {
			return left.End < right.End
		}
		return left.EpochID < right.EpochID
	})
	merged := make([]MaterializedNoData, 0, len(ordered))
	for _, span := range ordered {
		if len(merged) > 0 {
			previous := &merged[len(merged)-1]
			if previous.Family == span.Family && previous.SourceID == span.SourceID &&
				previous.EpochID == span.EpochID && previous.Reason == span.Reason &&
				previous.NativeCadenceSeconds == span.NativeCadenceSeconds && previous.End == span.Start {
				previous.End = span.End
				continue
			}
		}
		merged = append(merged, span)
	}
	return merged
}

func NewStatsService(writer *Writer, config StatsServiceConfig) *StatsService {
	now := config.Now
	if now == nil {
		now = time.Now
	}
	agentStatusCadence := config.AgentStatusCadence
	if agentStatusCadence <= 0 {
		agentStatusCadence = DefaultAgentStatusRefreshCadence
	}
	agentStatusRetryCadence := config.AgentStatusRetryCadence
	if agentStatusRetryCadence <= 0 {
		agentStatusRetryCadence = DefaultAgentStatusRetryCadence
	}
	keys := append([]ProductKey(nil), config.Products...)
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].RangeSeconds != keys[j].RangeSeconds {
			return keys[i].RangeSeconds < keys[j].RangeSeconds
		}
		return keys[i].ResolutionSeconds < keys[j].ResolutionSeconds
	})
	return &StatsService{
		writer:                  writer,
		price:                   config.Price,
		now:                     now,
		agentStatus:             config.AgentStatus,
		products:                make(map[ProductKey]statsProduct),
		pending:                 make(map[ProductKey]string),
		ringBucketCache:         make(map[ringBucketCacheKey]ringBucketCacheEntry),
		keys:                    keys,
		notify:                  make(chan struct{}),
		agentStatusCadence:      agentStatusCadence,
		agentStatusRetryCadence: agentStatusRetryCadence,
	}
}

// AgentStatus delegates to the stats-owner projection. The HTTP process may
// attach diagnostics, but it must not perform a fallback SQLite read.
func (s *StatsService) AgentStatus(ctx context.Context) (AgentStatusSnapshot, error) {
	if s == nil {
		return AgentStatusSnapshot{}, errors.New("agent status projection is unavailable")
	}
	s.agentStatusMu.RLock()
	defer s.agentStatusMu.RUnlock()
	if !s.agentStatusReady {
		return AgentStatusSnapshot{}, errors.New("agent status projection is not ready")
	}
	return s.agentStatusValue, s.agentStatusErr
}

// RefreshAgentStatus runs the configured status reader from the stats owner
// and stores its bounded result for request handlers. HTTP callers must use
// AgentStatus, which never invokes the callback or opens SQLite.
func (s *StatsService) RefreshAgentStatus(ctx context.Context) error {
	if s == nil {
		return errors.New("nil YO!stats service")
	}
	return s.refreshAgentStatus(ctx, true, s.now())
}

// RefreshAgentStatusIfDue advances only the bounded agent-status projection.
// The lifecycle owner can call this between ring publications without
// reopening ring metadata or decoding products that have not changed.
func (s *StatsService) RefreshAgentStatusIfDue(ctx context.Context) error {
	if s == nil {
		return errors.New("nil YO!stats service")
	}
	return s.refreshAgentStatus(ctx, false, s.now())
}

// refreshAgentStatus performs one serialized status-owner read. Explicit
// RefreshAgentStatus calls force a read; lifecycle Refresh calls this through
// the freshness gate so the SQLite reader cannot run every lifecycle tick.
func (s *StatsService) refreshAgentStatus(ctx context.Context, force bool, now time.Time) error {
	s.agentStatusRefreshMu.Lock()
	defer s.agentStatusRefreshMu.Unlock()
	if !force {
		s.agentStatusMu.RLock()
		ready := s.agentStatusReady
		next := s.agentStatusNext
		s.agentStatusMu.RUnlock()
		if ready && now.Before(next) {
			return nil
		}
	}
	if s.agentStatus == nil {
		err := errors.New("agent status projection is unavailable")
		s.agentStatusMu.Lock()
		s.agentStatusReady = true
		s.agentStatusValue = AgentStatusSnapshot{}
		s.agentStatusErr = err
		s.agentStatusNext = now.Add(s.agentStatusRetryCadence)
		s.agentStatusMu.Unlock()
		return err
	}
	value, err := s.agentStatus(ctx)
	s.agentStatusMu.Lock()
	s.agentStatusReady = true
	s.agentStatusValue = value
	s.agentStatusErr = err
	if err != nil {
		s.agentStatusNext = now.Add(s.agentStatusRetryCadence)
	} else {
		s.agentStatusNext = now.Add(s.agentStatusCadence)
	}
	s.agentStatusMu.Unlock()
	return err
}

func (s *StatsService) State() ServiceState {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.state
}

func (s *StatsService) RefreshCounters() StatsRefreshCounters {
	if s == nil {
		return StatsRefreshCounters{}
	}
	return s.refreshCounters.snapshot()
}

// Refresh reads only already-published ring cells. It is intended to run from
// the lifecycle owner, never from an HTTP handler.
func (s *StatsService) Refresh(ctx context.Context) (refreshErr error) {
	if s == nil || s.writer == nil {
		return errors.New("nil YO!stats service writer")
	}
	started := time.Now()
	s.refreshCounters.runs.Add(1)
	defer func() {
		elapsed := time.Since(started)
		s.refreshCounters.durationTotal.Add(uint64(elapsed.Nanoseconds()))
		s.refreshCounters.lastDuration.Store(uint64(elapsed.Nanoseconds()))
		if refreshErr != nil {
			s.refreshCounters.errors.Add(1)
		}
	}()
	now := s.now()
	// Status is a stats-owner projection, refreshed alongside ring products. The
	// lifecycle ticks every second, while the status product normally needs a
	// ten-second read and a bounded one-second retry after an error. A status
	// read failure is represented by the endpoint as unavailable and must not
	// make otherwise valid graph products fail readiness.
	_ = s.refreshAgentStatus(ctx, false, now)
	ringGeneration, publishedAt, err := s.writer.RingPublicationState(ctx)
	if err != nil {
		return err
	}
	s.mu.RLock()
	ringStateUnchanged := s.ringStateReady && s.lastRingGeneration == ringGeneration && s.lastPublishedAt == publishedAt
	s.mu.RUnlock()
	pendingCells, pendingErr := s.writer.PendingInvalidationCells(ctx)
	if pendingErr != nil {
		return pendingErr
	}
	pendingSignature := invalidationCellsSignature(pendingCells)
	s.mu.RLock()
	stateUnchanged := ringStateUnchanged && s.pendingStateReady && s.lastPendingSignature == pendingSignature
	s.mu.RUnlock()
	if stateUnchanged {
		// No ring bytes changed. The status projection above still follows its
		// own cadence, but avoid decoding and rebuilding every retained product
		// on each one-second lifecycle tick.
		s.mu.Lock()
		s.state.LastRefreshAt = float64(now.UnixNano()) / 1e9
		s.mu.Unlock()
		return nil
	}
	var firstErr error
	var storeGeneration int64
	refreshed := make(map[ProductKey]statsProduct)
	pending := make(map[ProductKey]string)
	byResolution := make(map[int][]ProductKey)
	for _, key := range s.keys {
		if key.RangeSeconds <= 0 || key.ResolutionSeconds <= 0 {
			pending[key] = "invalid product key"
			continue
		}
		byResolution[key.ResolutionSeconds] = append(byResolution[key.ResolutionSeconds], key)
	}
	resolutions := make([]int, 0, len(byResolution))
	for resolution := range byResolution {
		resolutions = append(resolutions, resolution)
	}
	sort.Ints(resolutions)
	for _, resolution := range resolutions {
		keys := byResolution[resolution]
		maxRange := 0
		for _, key := range keys {
			if key.RangeSeconds > maxRange {
				maxRange = key.RangeSeconds
			}
		}
		alignedEnd := NextWindowEnd(now, resolution)
		s.refreshCounters.ringReads.Add(1)
		baseWindow, err := s.writer.ReadRingWindowMetadata(ctx, maxRange, resolution, alignedEnd)
		if err != nil {
			for _, key := range keys {
				pending[key] = err.Error()
			}
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		if baseWindow.StoreSourceGeneration > storeGeneration {
			storeGeneration = baseWindow.StoreSourceGeneration
		}
		s.refreshCounters.ringRows.Add(uint64(len(baseWindow.Rows)))
		payloadStarts := make([]int64, 0, len(baseWindow.Rows))
		for _, row := range baseWindow.Rows {
			cacheKey := ringBucketCacheKey{resolution: resolution, bucket: row.BucketStart}
			cached, cacheHit := s.ringBucketCache[cacheKey]
			if !cacheHit || cached.ringGeneration != row.RingGeneration {
				payloadStarts = append(payloadStarts, row.BucketStart)
			}
		}
		payloads, err := s.writer.readRingBucketPayloads(ctx, resolution, payloadStarts)
		if err != nil {
			for _, key := range keys {
				pending[key] = err.Error()
			}
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		for _, key := range keys {
			window := narrowRingWindow(baseWindow, key.RangeSeconds)
			if len(window.PendingInvalidations) != 0 || len(window.MissingBucketStarts) != 0 {
				pending[key] = fmt.Sprintf("ring has %d missing or pending buckets", len(window.MissingBucketStarts))
				continue
			}
			buckets := make([]MaterializedBucket, 0, len(window.Rows))
			layerNoData := make([]MaterializedNoData, 0)
			cacheGeneration := int64(0)
			generatedAt := window.PublishedAt
			for _, row := range window.Rows {
				cacheKey := ringBucketCacheKey{resolution: resolution, bucket: row.BucketStart}
				cached, cacheHit := s.ringBucketCache[cacheKey]
				if !cacheHit || cached.ringGeneration != row.RingGeneration {
					s.refreshCounters.bucketDecodes.Add(1)
					rawPayload, payloadPresent := payloads[row.BucketStart]
					if !payloadPresent {
						err := fmt.Errorf("ring bucket %d payload was not returned", row.BucketStart)
						pending[key] = err.Error()
						if firstErr == nil {
							firstErr = err
						}
						break
					}
					payloadJSON, outerDecodeErr := decodeRingBucket(rawPayload)
					if outerDecodeErr != nil {
						pending[key] = fmt.Sprintf("decode ring bucket %d: %v", row.BucketStart, outerDecodeErr)
						if firstErr == nil {
							firstErr = outerDecodeErr
						}
						break
					}
					s.refreshCounters.decodedBytes.Add(uint64(len(payloadJSON)))
					decoded, decodeErr := decodeRingBucketPayloadWithMetadata(payloadJSON)
					if decodeErr != nil {
						pending[key] = fmt.Sprintf("decode ring bucket %d: %v", row.BucketStart, decodeErr)
						if firstErr == nil {
							firstErr = decodeErr
						}
						break
					}
					bucket, noData := decoded.bucket, decoded.noData
					payloadCache, payloadGeneratedAt, hasMetadata := decoded.cacheGeneration, decoded.generatedAt, decoded.hasMetadata
					if !hasMetadata {
						payloadCache = derivedCacheGeneration(row.PublishedAt, row.RingGeneration)
						payloadGeneratedAt = row.PublishedAt
					}
					cached = ringBucketCacheEntry{ringGeneration: row.RingGeneration, bucket: bucket, noData: noData, cacheGeneration: payloadCache, generatedAt: payloadGeneratedAt, hasMetadata: hasMetadata}
					s.ringBucketCache[cacheKey] = cached
				}
				bucket, noData := cached.bucket, cached.noData
				if bucket.Start != float64(row.BucketStart) || bucket.Duration != float64(key.ResolutionSeconds) || bucket.Complete != row.Complete {
					pending[key] = fmt.Sprintf("ring bucket %d has inconsistent bounds", row.BucketStart)
					if firstErr == nil {
						firstErr = errors.New("inconsistent ring bucket bounds")
					}
					break
				}
				if cacheGeneration == 0 || cached.cacheGeneration > cacheGeneration {
					cacheGeneration = cached.cacheGeneration
					generatedAt = cached.generatedAt
				}
				if !bucket.Complete && row.BucketStart != window.WindowEnd-int64(key.ResolutionSeconds) {
					// An open bit records that this row was the right edge when it was
					// published. Once it is interior to a later window, its values are
					// stale and must not be presented as historical truth. Keep the
					// dense bucket shape, but project an explicit persisted-ring gap.
					bucket = MaterializedBucket{Start: float64(row.BucketStart), Duration: float64(key.ResolutionSeconds), Complete: true}
					noData = append(noData, persistedRingGapNoData(row.BucketStart, key.ResolutionSeconds, window.RingGeneration)...)
				}
				buckets = append(buckets, bucket)
				layerNoData = append(layerNoData, noData...)
			}
			if _, blocked := pending[key]; blocked {
				continue
			}
			if cacheGeneration == 0 {
				cacheGeneration = derivedCacheGeneration(window.PublishedAt, window.RingGeneration)
			}
			snapshot := StatsSnapshot{
				SourceGeneration: window.SourceGeneration, CacheGeneration: cacheGeneration, GeneratedAt: generatedAt,
				Request:      SnapshotRequest{Start: float64(window.WindowStart), End: float64(window.WindowEnd), Resolution: float64(key.ResolutionSeconds)},
				Materialized: MaterializedLayer{Resolution: float64(key.ResolutionSeconds), Start: float64(window.WindowStart), End: float64(window.WindowEnd), Buckets: buckets, NoData: mergeAdjacentNoData(layerNoData)},
				Unavailable:  SnapshotAvailability{State: SnapshotAvailable},
			}
			refreshed[key] = statsProduct{key: key, windowEnd: window.WindowEnd, sourceGeneration: window.SourceGeneration, ringGeneration: window.RingGeneration, cacheGeneration: cacheGeneration, publishedAt: window.PublishedAt, snapshot: snapshot}
			s.refreshCounters.productsBuilt.Add(1)
		}
	}
	s.mu.Lock()
	for key, product := range refreshed {
		s.products[key] = product
	}
	s.pending = pending
	s.state.LastRefreshAt = float64(now.UnixNano()) / 1e9
	s.state.PendingProducts = len(pending)
	s.state.PublishedProducts = len(s.products)
	s.state.LastError = ""
	if firstErr != nil {
		s.state.LastError = firstErr.Error()
	}
	var generation = storeGeneration
	var cacheGeneration int64
	for _, product := range s.products {
		if product.sourceGeneration > generation {
			generation = product.sourceGeneration
		}
		if product.cacheGeneration > cacheGeneration {
			cacheGeneration = product.cacheGeneration
		}
	}
	s.state.SourceGeneration = generation
	s.state.CacheGeneration = cacheGeneration
	s.state.Ready = len(s.keys) > 0 && len(pending) == 0 && len(s.products) == len(s.keys) && firstErr == nil
	s.lastRingGeneration = ringGeneration
	s.lastPublishedAt = publishedAt
	s.ringStateReady = true
	s.lastPendingSignature = pendingSignature
	s.pendingStateReady = true
	oldNotify := s.notify
	s.notify = make(chan struct{})
	s.mu.Unlock()
	close(oldNotify)
	return firstErr
}

// narrowRingWindow derives a shorter product from a single widest read for a
// resolution. All products with the same resolution share the same aligned end
// and ring slots, so reopening SQLite and scanning the slot table for each
// range only repeats work.
func narrowRingWindow(base RingWindow, rangeSeconds int) RingWindow {
	start := base.WindowEnd - int64(rangeSeconds)
	window := base
	window.RangeSeconds = rangeSeconds
	window.WindowStart = start
	window.Rows = make([]RingBucketRow, 0, len(base.Rows))
	for _, row := range base.Rows {
		if row.BucketStart >= start && row.BucketStart < base.WindowEnd {
			window.Rows = append(window.Rows, row)
		}
	}
	window.MissingBucketStarts = filterRingBuckets(base.MissingBucketStarts, start, base.WindowEnd)
	window.PendingInvalidations = filterRingBuckets(base.PendingInvalidations, start, base.WindowEnd)
	return window
}

func filterRingBuckets(values []int64, start, end int64) []int64 {
	filtered := make([]int64, 0, len(values))
	for _, value := range values {
		if value >= start && value < end {
			filtered = append(filtered, value)
		}
	}
	return filtered
}

func invalidationCellsSignature(cells []InvalidationCell) uint64 {
	// The writer returns cells in a deterministic order. A compact rolling
	// signature lets Refresh distinguish an unchanged pending backlog from a
	// new invalidation without retaining or comparing the full slice.
	hash := uint64(1469598103934665603)
	for _, cell := range cells {
		for _, value := range []int64{int64(cell.ResolutionSeconds), cell.BucketStart, cell.SourceGeneration} {
			hash ^= uint64(value)
			hash *= 1099511628211
		}
	}
	return hash ^ uint64(len(cells))
}

// WaitForPublication blocks until Refresh has observed another lifecycle
// publication or the supplied heartbeat deadline expires. It lets SSE use
// publication notifications instead of repeatedly rebuilding a delta on a
// short polling ticker.
func (s *StatsService) WaitForPublication(ctx context.Context, afterGeneration int64, timeout time.Duration) (bool, error) {
	if s == nil {
		return false, errors.New("nil YO!stats service")
	}
	if timeout <= 0 {
		timeout = time.Second
	}
	timer := time.NewTimer(timeout)
	defer stopTimer(timer)
	for {
		s.mu.RLock()
		if s.state.CacheGeneration > afterGeneration {
			s.mu.RUnlock()
			return true, nil
		}
		notify := s.notify
		s.mu.RUnlock()
		select {
		case <-ctx.Done():
			return false, ctx.Err()
		case <-notify:
			// A publication can advance the ring while source facts remain
			// unchanged; emit a heartbeat in that case.
			continue
		case <-timer.C:
			return false, nil
		}
	}
}

// WaitForProductPublication is the product-scoped form used by SSE cursors.
// ServiceState.CacheGeneration is a diagnostic maximum across all products;
// using it for one stream lets an unrelated range wake that stream and makes
// its delivery cursor appear to advance when its own product did not change.
func (s *StatsService) WaitForProductPublication(ctx context.Context, key ProductKey, afterGeneration int64, timeout time.Duration) (bool, error) {
	if s == nil {
		return false, errors.New("nil YO!stats service")
	}
	if timeout <= 0 {
		timeout = time.Second
	}
	timer := time.NewTimer(timeout)
	defer stopTimer(timer)
	for {
		s.mu.RLock()
		product, ok := s.products[key]
		notify := s.notify
		s.mu.RUnlock()
		if ok && product.cacheGeneration > afterGeneration {
			return true, nil
		}
		select {
		case <-ctx.Done():
			return false, ctx.Err()
		case <-notify:
			continue
		case <-timer.C:
			return false, nil
		}
	}
}

func stopTimer(timer *time.Timer) {
	if !timer.Stop() {
		select {
		case <-timer.C:
		default:
		}
	}
}

// Snapshot returns the latest immutable product. The request bounds are
// retained in the returned metadata, while bucket bytes come exclusively from
// the product published by Refresh.
func (s *StatsService) Snapshot(_ context.Context, request SnapshotRequest) (StatsSnapshot, error) {
	if s == nil {
		return StatsSnapshot{}, errors.New("nil YO!stats service")
	}
	key := ProductKey{RangeSeconds: int(request.End - request.Start), ResolutionSeconds: int(request.Resolution)}
	s.mu.RLock()
	product, ok := s.products[key]
	reason := s.pending[key]
	s.mu.RUnlock()
	if !ok {
		if reason == "" {
			reason = "product has not been published"
		}
		return StatsSnapshot{}, fmt.Errorf("%w: %s", ErrProductPending, reason)
	}
	requestedEnd := int64(request.End)
	requestedStart := int64(request.Start)
	if float64(requestedEnd) != request.End || float64(requestedStart) != request.Start || requestedEnd != product.windowEnd || requestedStart != product.windowEnd-int64(key.RangeSeconds) {
		return StatsSnapshot{}, fmt.Errorf("%w: published product window does not match request", ErrProductPending)
	}
	snapshot := product.snapshot
	snapshot.Request = request
	snapshot.SourceGeneration = product.sourceGeneration
	snapshot.CacheGeneration = product.cacheGeneration
	snapshot.GeneratedAt = product.publishedAt
	return snapshot, nil
}

// CurrentSnapshot returns the newest complete product for a public key. HTTP
// requests do not carry an authoritative wall-clock window; the lifecycle may
// have published the product just before or just after the request crossed a
// bucket boundary. Returning the product's durable bounds keeps that retained
// cache useful instead of turning a normal rollover into a false pending
// response. Snapshot remains strict for callers that explicitly supply bounds.
func (s *StatsService) CurrentSnapshot(_ context.Context, rangeSeconds, resolutionSeconds int) (StatsSnapshot, error) {
	if s == nil {
		return StatsSnapshot{}, errors.New("nil YO!stats service")
	}
	key := ProductKey{RangeSeconds: rangeSeconds, ResolutionSeconds: resolutionSeconds}
	s.mu.RLock()
	product, ok := s.products[key]
	reason := s.pending[key]
	s.mu.RUnlock()
	if !ok {
		if reason == "" {
			reason = "product has not been published"
		}
		return StatsSnapshot{}, fmt.Errorf("%w: %s", ErrProductPending, reason)
	}
	snapshot := product.snapshot
	snapshot.Request = SnapshotRequest{
		Start:      float64(product.windowEnd - int64(key.RangeSeconds)),
		End:        float64(product.windowEnd),
		Resolution: float64(key.ResolutionSeconds),
	}
	snapshot.SourceGeneration = product.sourceGeneration
	snapshot.CacheGeneration = product.cacheGeneration
	snapshot.GeneratedAt = product.publishedAt
	return snapshot, nil
}
