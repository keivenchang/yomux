// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"hash/maphash"
	"math"
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
	bucketVersions   []ringBucketVersion
}

type ringBucketVersion struct {
	start, generation int64
	noData            *ringNoDataSet
	closedTail        *ringNoDataSet
}

type ringBucketCacheKey struct {
	resolution int
	bucket     int64
}

type ringBucketCacheEntry struct {
	ringGeneration  int64
	bucket          MaterializedBucket
	noData          *ringNoDataSet
	wireBytes       int
	cacheBytes      int
	cacheGeneration int64
	generatedAt     float64
	hasMetadata     bool
}

const (
	maxRingBucketCacheEncodedBytes  = 256 * 1024 * 1024
	maxSnapshotEnvelopeReserveBytes = 8 * 1024 * 1024
	maxSnapshotBucketReserveBytes   = 64 * 1024
	maxSnapshotChunkReserveBytes    = 64 * 1024
)

// Bucket-relative intervals let repeated unavailable sources share one typed
// slice across a whole ring. Hashes only select candidates; exact comparison
// remains authoritative, including partial intervals and every identity field.
type ringNoDataSet struct {
	spans []MaterializedNoData
	bytes int
}

func (s *StatsService) internNoData(spans []MaterializedNoData, base float64) *ringNoDataSet {
	var hash maphash.Hash
	hash.SetSeed(s.noDataSeed)
	var number [8]byte
	for i := range spans {
		spans[i].Start -= base
		spans[i].End -= base
		for _, value := range []string{spans[i].Family, spans[i].SourceID, spans[i].EpochID, spans[i].Reason} {
			hash.WriteString(value)
			hash.WriteByte(0)
		}
		for _, value := range []float64{spans[i].Start, spans[i].End, spans[i].NativeCadenceSeconds} {
			bits := math.Float64bits(value)
			for j := range number {
				number[j] = byte(bits >> (8 * j))
			}
			hash.Write(number[:])
		}
	}
	key := hash.Sum64()
	for _, set := range s.noDataSets[key] {
		if len(set.spans) != len(spans) {
			continue
		}
		equal := true
		for i := range spans {
			if spans[i] != set.spans[i] {
				equal = false
				break
			}
		}
		if equal {
			return set
		}
	}
	bytes := len(spans) * 88
	for _, span := range spans {
		bytes += len(span.Family) + len(span.SourceID) + len(span.EpochID) + len(span.Reason)
	}
	set := &ringNoDataSet{spans: spans, bytes: bytes}
	s.noDataSets[key] = append(s.noDataSets[key], set)
	return set
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
	history                 map[ProductKey][]statsProduct
	noDataSeed              maphash.Seed
	noDataSets              map[uint64][]*ringNoDataSet
	refreshMu               sync.Mutex
	pending                 map[ProductKey]string
	state                   ServiceState
	keys                    []ProductKey
	ringBucketCache         map[ringBucketCacheKey]ringBucketCacheEntry
	ringBucketCacheBytes    int
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
	"system_disk",
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
	return mergeAdjacentNoDataInPlace(ordered)
}

func mergeAdjacentNoDataInPlace(ordered []MaterializedNoData) []MaterializedNoData {
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
	mergedCount := 0
	for _, span := range ordered {
		if mergedCount > 0 {
			previous := &ordered[mergedCount-1]
			if previous.Family == span.Family && previous.SourceID == span.SourceID &&
				previous.EpochID == span.EpochID && previous.Reason == span.Reason &&
				previous.NativeCadenceSeconds == span.NativeCadenceSeconds && previous.End == span.Start {
				previous.End = span.End
				continue
			}
		}
		ordered[mergedCount] = span
		mergedCount++
	}
	return ordered[:mergedCount]
}

type materializedNoDataKey struct {
	family, sourceID, epochID, reason string
	cadence                           float64
}

// materializedNoDataAccumulator merges adjacent bucket fragments as they are
// read. A long history can repeat the same unavailable source in every bucket;
// retaining all fragments until the final sort made the request path grow with
// bucketCount*sourceCount even though the response contains one span per source.
type materializedNoDataAccumulator struct {
	spans          []MaterializedNoData
	lastByKey      map[materializedNoDataKey]int
	estimatedBytes int
	sizeForWire    bool
}

func newMaterializedNoDataAccumulator() materializedNoDataAccumulator {
	return newMaterializedNoDataAccumulatorWithWireSizing(true)
}

func newMaterializedNoDataAccumulatorWithWireSizing(sizeForWire bool) materializedNoDataAccumulator {
	return materializedNoDataAccumulator{lastByKey: make(map[materializedNoDataKey]int), sizeForWire: sizeForWire}
}

func (a *materializedNoDataAccumulator) Append(spans []MaterializedNoData) {
	a.appendShifted(spans, 0)
}

func (a *materializedNoDataAccumulator) appendShifted(spans []MaterializedNoData, base float64) {
	for _, span := range spans {
		span.Start += base
		span.End += base
		key := materializedNoDataKey{family: span.Family, sourceID: span.SourceID, epochID: span.EpochID, reason: span.Reason, cadence: span.NativeCadenceSeconds}
		if index, exists := a.lastByKey[key]; exists && a.spans[index].End == span.Start {
			a.spans[index].End = span.End
			continue
		}
		a.lastByKey[key] = len(a.spans)
		a.spans = append(a.spans, span)
		if a.sizeForWire {
			a.estimatedBytes += estimatedNoDataSpanBytes(span)
		}
	}
}

func estimatedNoDataSpanBytes(span MaterializedNoData) int {
	encoded, err := json.Marshal(map[string]any{
		"family": span.Family, "source_id": span.SourceID, "start": span.Start, "end": span.End,
		"epoch": span.EpochID, "reason": span.Reason, "source_cadence_seconds": span.NativeCadenceSeconds,
	})
	if err == nil {
		return len(encoded) + 1
	}
	// Invalid floats cannot be emitted as JSON. Keep admission conservative so
	// the normal snapshot validation reports the malformed materialized value.
	return 1 + 6*(len(span.Family)+len(span.SourceID)+len(span.EpochID)+len(span.Reason)) + 128
}

func estimatedNoDataBytesInRange(spans []MaterializedNoData, sizes []int, start, end float64) int {
	total := 0
	for index, span := range spans {
		if span.End > start && span.Start < end {
			total += sizes[index]
		}
	}
	return total
}

func statsSnapshotChunksFit(bucketSizes []int, bucketStarts []int64, resolution int, noData []MaterializedNoData, costReportBytes int) bool {
	if len(bucketSizes) == 0 || len(bucketStarts) != len(bucketSizes) || resolution <= 0 || costReportBytes < 0 {
		return false
	}
	noDataSizes := make([]int, len(noData))
	noDataBytes := 0
	for index, span := range noData {
		noDataSizes[index] = estimatedNoDataSpanBytes(span)
		noDataBytes += noDataSizes[index]
	}
	totalBytes := noDataBytes + costReportBytes
	for _, size := range bucketSizes {
		if size < 0 {
			return false
		}
		totalBytes += size
	}
	if totalBytes <= MaxStatsSnapshotChunkTargetBytes {
		return true
	}
	chunkLimit := min(len(bucketSizes), MaxStatsSnapshotChunks)
	chunkCount := max(2, (totalBytes+MaxStatsSnapshotChunkTargetBytes-1)/MaxStatsSnapshotChunkTargetBytes)
	if chunkCount > chunkLimit {
		chunkCount = chunkLimit
	}
	for {
		largest := 0
		for index := 0; index < chunkCount; index++ {
			first := index * len(bucketSizes) / chunkCount
			last := (index + 1) * len(bucketSizes) / chunkCount
			chunkStart := float64(bucketStarts[first])
			chunkEnd := float64(bucketStarts[last-1] + int64(resolution))
			chunkBytes := estimatedNoDataBytesInRange(noData, noDataSizes, chunkStart, chunkEnd) + costReportBytes + maxSnapshotChunkReserveBytes
			for _, size := range bucketSizes[first:last] {
				chunkBytes += size
			}
			if chunkBytes > largest {
				largest = chunkBytes
			}
		}
		if largest <= MaxStatsSnapshotChunkBytes {
			return true
		}
		if chunkCount == chunkLimit {
			if chunkCount != len(bucketSizes) {
				return false
			}
			fragmentCount := chunkCount
			for index, bucketSize := range bucketSizes {
				chunkStart := float64(bucketStarts[index])
				chunkEnd := float64(bucketStarts[index] + int64(resolution))
				fixedBytes := bucketSize + costReportBytes + maxSnapshotChunkReserveBytes
				availableBytes := MaxStatsSnapshotChunkBytes - fixedBytes
				if availableBytes <= 0 {
					return false
				}
				bucketFragments := 1
				currentBytes := 2
				for spanIndex, span := range noData {
					if span.End <= chunkStart || span.Start >= chunkEnd {
						continue
					}
					spanBytes := noDataSizes[spanIndex] - 1
					itemBytes := spanBytes
					if currentBytes > 2 {
						itemBytes++
					}
					if currentBytes+itemBytes > availableBytes {
						bucketFragments++
						currentBytes = 2
						itemBytes = spanBytes
					}
					if currentBytes+itemBytes > availableBytes {
						return false
					}
					currentBytes += itemBytes
				}
				fragmentCount += bucketFragments - 1
				if fragmentCount > MaxStatsSnapshotChunks {
					return false
				}
			}
			return fragmentCount <= MaxStatsSnapshotChunks
		}
		next := (chunkCount*largest + MaxStatsSnapshotChunkTargetBytes - 1) / MaxStatsSnapshotChunkTargetBytes
		if next <= chunkCount {
			next = chunkCount + 1
		}
		chunkCount = min(next, chunkLimit)
	}
}

func (a materializedNoDataAccumulator) Sorted() []MaterializedNoData {
	return mergeAdjacentNoDataInPlace(a.spans)
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
		history:                 make(map[ProductKey][]statsProduct),
		noDataSeed:              maphash.MakeSeed(),
		noDataSets:              make(map[uint64][]*ringNoDataSet),
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
	s.refreshMu.Lock()
	defer s.refreshMu.Unlock()
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
	retryRingRefresh := false
	markCorruptPayload := func(row RingBucketRow, payload string, cause error) error {
		marked, markErr := s.writer.InvalidateCorruptRingPayload(ctx, row, payload, float64(now.UnixNano())/1e9)
		if markErr != nil {
			retryRingRefresh = true
			return errors.Join(cause, fmt.Errorf("persist ring repair request: %w", markErr))
		}
		if !marked {
			retryRingRefresh = true
			return errors.Join(cause, errors.New("ring slot changed before its repair request could be recorded"))
		}
		return cause
	}
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
			retryRingRefresh = true
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
		cacheRows := make([]RingBucketRow, 0, len(baseWindow.Rows)+len(baseWindow.PendingRows))
		cacheRows = append(cacheRows, baseWindow.Rows...)
		cacheRows = append(cacheRows, baseWindow.PendingRows...)
		s.pruneRingBucketCache(resolution, cacheRows)
		s.refreshCounters.ringRows.Add(uint64(len(baseWindow.Rows) + len(baseWindow.PendingRows)))
		payloadStarts := make([]int64, 0, len(baseWindow.Rows)+len(baseWindow.PendingRows))
		for _, row := range cacheRows {
			cacheKey := ringBucketCacheKey{resolution: resolution, bucket: row.BucketStart}
			cached, cacheHit := s.ringBucketCache[cacheKey]
			if !cacheHit || cached.ringGeneration != row.RingGeneration {
				payloadStarts = append(payloadStarts, row.BucketStart)
			}
		}
		payloads, err := s.writer.readRingBucketPayloads(ctx, resolution, payloadStarts)
		if err != nil {
			retryRingRefresh = true
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
				carrier, allowed := pendingOpenCarrier(window, now)
				if !allowed {
					// Missing slots are lifecycle backfill work even without a ledger
					// entry. Publishing them as no-data would make a partial startup
					// pass look complete despite facts available to rebuild the window.
					pending[key] = fmt.Sprintf("ring has %d missing buckets and %d pending invalidations", len(window.MissingBucketStarts), len(window.PendingInvalidations))
					continue
				}
				window.Rows = append(window.Rows, carrier)
			}
			versions := make([]ringBucketVersion, len(window.Rows))
			for i, row := range window.Rows {
				versions[i] = ringBucketVersion{start: row.BucketStart, generation: row.RingGeneration}
			}
			s.mu.RLock()
			previous, exists := s.products[key]
			s.mu.RUnlock()
			if exists && previous.windowEnd == window.WindowEnd && equalBucketVersions(previous.bucketVersions, versions) {
				refreshed[key] = previous
				continue
			}
			buckets := make([]MaterializedBucket, 0, len(window.Rows))
			layerNoData := newMaterializedNoDataAccumulator()
			bucketSizes := make([]int, 0, len(window.Rows))
			bucketStarts := make([]int64, 0, len(window.Rows))
			cacheGeneration := int64(0)
			generatedAt := window.PublishedAt
			windowBucketBytes := 0
			for rowIndex, row := range window.Rows {
				cacheKey := ringBucketCacheKey{resolution: resolution, bucket: row.BucketStart}
				cached, cacheHit := s.ringBucketCache[cacheKey]
				var bucketJSONBytes int
				if !cacheHit || cached.ringGeneration != row.RingGeneration {
					if cacheHit {
						s.ringBucketCacheBytes -= cached.cacheBytes
						delete(s.ringBucketCache, cacheKey)
					}
					s.refreshCounters.bucketDecodes.Add(1)
					payload, payloadPresent := payloads[row.BucketStart]
					if !payloadPresent {
						err := fmt.Errorf("ring bucket %d payload was not returned", row.BucketStart)
						retryRingRefresh = true
						pending[key] = err.Error()
						if firstErr == nil {
							firstErr = err
						}
						break
					}
					if !ringBucketPayloadMatchesMetadata(row, payload) {
						retryRingRefresh = true
						err := fmt.Errorf("ring bucket %d changed while its payload was being read", row.BucketStart)
						pending[key] = err.Error()
						if firstErr == nil {
							firstErr = err
						}
						break
					}
					payloadJSON, outerDecodeErr := decodeRingBucket(payload.Value)
					if outerDecodeErr != nil {
						pending[key] = fmt.Sprintf("decode ring bucket %d: %v", row.BucketStart, outerDecodeErr)
						outerDecodeErr = markCorruptPayload(row, payload.Value, outerDecodeErr)
						if firstErr == nil {
							firstErr = outerDecodeErr
						}
						break
					}
					s.refreshCounters.decodedBytes.Add(uint64(len(payloadJSON)))
					decoded, decodeErr := decodeRingBucketPayloadWithMetadata(payloadJSON)
					if decodeErr != nil {
						pending[key] = fmt.Sprintf("decode ring bucket %d: %v", row.BucketStart, decodeErr)
						decodeErr = markCorruptPayload(row, payload.Value, decodeErr)
						if firstErr == nil {
							firstErr = decodeErr
						}
						break
					}
					bucket, noData := decoded.bucket, decoded.noData
					bucketJSON, marshalErr := json.Marshal(bucket)
					if marshalErr != nil {
						pending[key] = fmt.Sprintf("encode ring bucket %d: %v", row.BucketStart, marshalErr)
						if firstErr == nil {
							firstErr = marshalErr
						}
						break
					}
					bucketJSONBytes = len(bucketJSON)
					payloadCache, payloadGeneratedAt, hasMetadata := decoded.cacheGeneration, decoded.generatedAt, decoded.hasMetadata
					if !hasMetadata {
						payloadCache = derivedCacheGeneration(row.PublishedAt, row.RingGeneration)
						payloadGeneratedAt = row.PublishedAt
					}
					wireBucket := bucket
					wireBucket.Cost = MaterializedCostDetail{}
					wireJSON, marshalErr := json.Marshal(wireBucket)
					if marshalErr != nil {
						return marshalErr
					}
					intervals := s.internNoData(noData, bucket.Start)
					cacheBytes := bucketJSONBytes + intervals.bytes
					cached = ringBucketCacheEntry{ringGeneration: row.RingGeneration, bucket: bucket, noData: intervals, wireBytes: len(wireJSON), cacheGeneration: payloadCache, generatedAt: payloadGeneratedAt, hasMetadata: hasMetadata}
					if cacheBytes <= maxRingBucketCacheEncodedBytes-s.ringBucketCacheBytes {
						cached.cacheBytes = cacheBytes
						s.ringBucketCache[cacheKey] = cached
						s.ringBucketCacheBytes += cacheBytes
					}
				}
				bucket := cached.bucket
				bucketJSONBytes = cached.wireBytes
				if bucketJSONBytes > MaxStatsSnapshotChunkBytes-maxSnapshotBucketReserveBytes {
					transportErr := fmt.Errorf("ring bucket %d exceeds the stats snapshot transport chunk limit", row.BucketStart)
					pending[key] = transportErr.Error()
					if firstErr == nil {
						firstErr = transportErr
					}
					break
				}
				windowBucketBytes += bucketJSONBytes + 1
				if bucket.Start != float64(row.BucketStart) || bucket.Duration != float64(key.ResolutionSeconds) || bucket.Complete != row.Complete {
					pending[key] = fmt.Sprintf("ring bucket %d has inconsistent bounds", row.BucketStart)
					boundErr := markCorruptPayload(row, payloads[row.BucketStart].Value, errors.New("inconsistent ring bucket bounds"))
					if firstErr == nil {
						firstErr = boundErr
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
					// known facts remain valid lower bounds; only the unobserved tail is
					// unknown. Close the bucket while retaining its series and cost, then
					// project an explicit persisted-ring gap for every family.
					bucket.Complete = true
					gap := s.internNoData(persistedRingGapNoData(row.BucketStart, key.ResolutionSeconds, row.RingGeneration), bucket.Start)
					versions[rowIndex].closedTail = gap
					layerNoData.appendShifted(gap.spans, bucket.Start)
				}
				buckets = append(buckets, bucket)
				bucketSizes = append(bucketSizes, bucketJSONBytes)
				bucketStarts = append(bucketStarts, row.BucketStart)
				layerNoData.appendShifted(cached.noData.spans, bucket.Start)
				versions[rowIndex].noData = cached.noData
				if windowBucketBytes+layerNoData.estimatedBytes > MaxStatsSnapshotBytes-maxSnapshotEnvelopeReserveBytes {
					transportErr := errors.New("materialized stats snapshot exceeds the transport size limit")
					pending[key] = transportErr.Error()
					if firstErr == nil {
						firstErr = transportErr
					}
					break
				}
			}
			if _, blocked := pending[key]; blocked {
				continue
			}
			windowCostBytes, costErr := CostReportDetailBytes(buckets)
			if costErr != nil {
				pending[key] = fmt.Sprintf("encode window cost report: %v", costErr)
				if firstErr == nil {
					firstErr = costErr
				}
				continue
			}
			if windowBucketBytes+layerNoData.estimatedBytes+windowCostBytes > MaxStatsSnapshotBytes-maxSnapshotEnvelopeReserveBytes {
				transportErr := errors.New("materialized stats snapshot exceeds the transport size limit")
				pending[key] = transportErr.Error()
				if firstErr == nil {
					firstErr = transportErr
				}
				continue
			}
			if !statsSnapshotChunksFit(bucketSizes, bucketStarts, key.ResolutionSeconds, layerNoData.spans, windowCostBytes) {
				transportErr := errors.New("materialized stats snapshot exceeds the transport chunk limit")
				pending[key] = transportErr.Error()
				if firstErr == nil {
					firstErr = transportErr
				}
				continue
			}
			sort.Slice(buckets, func(i, j int) bool { return buckets[i].Start < buckets[j].Start })
			if cacheGeneration == 0 {
				cacheGeneration = derivedCacheGeneration(window.PublishedAt, window.RingGeneration)
			}
			snapshot := StatsSnapshot{
				SourceGeneration: window.SourceGeneration, CacheGeneration: cacheGeneration, GeneratedAt: generatedAt,
				Request:      SnapshotRequest{Start: float64(window.WindowStart), End: float64(window.WindowEnd), Resolution: float64(key.ResolutionSeconds)},
				Materialized: MaterializedLayer{Resolution: float64(key.ResolutionSeconds), Start: float64(window.WindowStart), End: float64(window.WindowEnd), Buckets: buckets, NoData: layerNoData.Sorted()},
				Unavailable:  SnapshotAvailability{State: SnapshotAvailable},
			}
			if err := validateMaterializedCostRange(snapshot.Materialized); err != nil {
				pending[key] = err.Error()
				if firstErr == nil {
					firstErr = err
				}
				continue
			}
			refreshed[key] = statsProduct{key: key, windowEnd: window.WindowEnd, sourceGeneration: window.SourceGeneration, ringGeneration: window.RingGeneration, cacheGeneration: cacheGeneration, publishedAt: window.PublishedAt, snapshot: snapshot, bucketVersions: versions}
			s.refreshCounters.productsBuilt.Add(1)
		}
	}
	s.mu.Lock()
	for key, product := range refreshed {
		history := s.history[key]
		if len(history) == 0 || history[len(history)-1].cacheGeneration != product.cacheGeneration {
			historical := product
			historical.snapshot.Materialized.NoData = nil
			history = append(history, historical)
			if len(history) > 8 {
				history = append([]statsProduct(nil), history[len(history)-8:]...)
			}
			s.history[key] = history
		}
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
	// If the exact corruption report could not be durably recorded, do not let
	// this failure become an unchanged-state cache hit. The next owner refresh
	// must retry the durable mark before lifecycle can repair the slot.
	s.pendingStateReady = !retryRingRefresh
	oldNotify := s.notify
	s.notify = make(chan struct{})
	s.mu.Unlock()
	close(oldNotify)
	s.pruneNoDataSets()
	return firstErr
}

func (s *StatsService) pruneRingBucketCache(resolution int, rows []RingBucketRow) {
	retained := make(map[int64]int64, len(rows))
	for _, row := range rows {
		retained[row.BucketStart] = row.RingGeneration
	}
	for key, cached := range s.ringBucketCache {
		if key.resolution != resolution {
			continue
		}
		if generation, exists := retained[key.bucket]; exists && generation == cached.ringGeneration {
			continue
		}
		s.ringBucketCacheBytes -= cached.cacheBytes
		delete(s.ringBucketCache, key)
	}
}

func equalBucketVersions(left, right []ringBucketVersion) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i].start != right[i].start || left[i].generation != right[i].generation {
			return false
		}
	}
	return true
}

func (s *StatsService) pruneNoDataSets() {
	used := make(map[*ringNoDataSet]bool, len(s.ringBucketCache))
	for _, entry := range s.ringBucketCache {
		used[entry.noData] = true
	}
	for hash, sets := range s.noDataSets {
		retained := sets[:0]
		for _, set := range sets {
			if used[set] {
				retained = append(retained, set)
			}
		}
		clear(sets[len(retained):])
		if len(retained) == 0 {
			delete(s.noDataSets, hash)
		} else {
			s.noDataSets[hash] = retained
		}
	}
}

// GenerationSnapshot resolves a retained immutable product without keeping a
// second expanded history in HTTP. Returned values are read-only, like
// CurrentSnapshot; callers construct their own wire values before mutation.
func (s *StatsService) GenerationSnapshot(_ context.Context, rangeSeconds, resolutionSeconds int, cacheGeneration int64) (StatsSnapshot, bool) {
	if s == nil {
		return StatsSnapshot{}, false
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, product := range s.history[ProductKey{rangeSeconds, resolutionSeconds}] {
		if product.cacheGeneration == cacheGeneration {
			snapshot := snapshotWithCoverage(product)
			snapshot.SourceGeneration = product.sourceGeneration
			snapshot.CacheGeneration = product.cacheGeneration
			snapshot.GeneratedAt = product.publishedAt
			return snapshot, true
		}
	}
	return StatsSnapshot{}, false
}

func snapshotWithCoverage(product statsProduct) StatsSnapshot {
	snapshot := product.snapshot
	if snapshot.Materialized.NoData != nil {
		return snapshot
	}
	coverage := newMaterializedNoDataAccumulatorWithWireSizing(false)
	for _, bucket := range product.bucketVersions {
		if bucket.closedTail != nil {
			coverage.appendShifted(bucket.closedTail.spans, float64(bucket.start))
		}
		if bucket.noData != nil {
			coverage.appendShifted(bucket.noData.spans, float64(bucket.start))
		}
	}
	snapshot.Materialized.NoData = coverage.Sorted()
	return snapshot
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
	window.PendingRows = make([]RingBucketRow, 0, len(base.PendingRows))
	for _, row := range base.PendingRows {
		if row.BucketStart >= start && row.BucketStart < base.WindowEnd {
			window.PendingRows = append(window.PendingRows, row)
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

func pendingOpenCarrier(window RingWindow, now time.Time) (RingBucketRow, bool) {
	if window.ResolutionSeconds <= 0 || now.Unix() >= window.WindowEnd || len(window.MissingBucketStarts) != 1 || len(window.PendingInvalidations) != 1 {
		return RingBucketRow{}, false
	}
	carrierStart := window.WindowEnd - int64(window.ResolutionSeconds)
	if window.MissingBucketStarts[0] != carrierStart || window.PendingInvalidations[0] != carrierStart {
		return RingBucketRow{}, false
	}
	for _, row := range window.PendingRows {
		if row.BucketStart == carrierStart && !row.Complete {
			return row, true
		}
	}
	return RingBucketRow{}, false
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
		if err := ctx.Err(); err != nil {
			return false, err
		}
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
		if err := ctx.Err(); err != nil {
			return false, err
		}
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

func (s *StatsService) PricingRebuildComplete(generation int64) bool {
	if s == nil {
		return false
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.lastRingGeneration < generation {
		return false
	}
	for _, key := range s.keys {
		if s.pending[key] != "" {
			return false
		}
		if _, ok := s.products[key]; !ok {
			return false
		}
	}
	return true
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
	snapshot := snapshotWithCoverage(product)
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
	return s.currentSnapshot(rangeSeconds, resolutionSeconds, false)
}

// CurrentSnapshotIfChanged avoids expanding bucket-relative coverage when the
// caller already has the current immutable generation.
func (s *StatsService) CurrentSnapshotIfChanged(_ context.Context, rangeSeconds, resolutionSeconds int, afterCacheGeneration int64) (StatsSnapshot, bool, error) {
	if s == nil {
		return StatsSnapshot{}, false, errors.New("nil YO!stats service")
	}
	key := ProductKey{RangeSeconds: rangeSeconds, ResolutionSeconds: resolutionSeconds}
	s.mu.RLock()
	product, ok := s.products[key]
	reason := s.pending[key]
	if ok && product.cacheGeneration == afterCacheGeneration {
		s.mu.RUnlock()
		return StatsSnapshot{}, false, nil
	}
	s.mu.RUnlock()
	if !ok {
		if reason == "" {
			reason = "product has not been published"
		}
		return StatsSnapshot{}, false, fmt.Errorf("%w: %s", ErrProductPending, reason)
	}
	snapshot := snapshotWithCoverage(product)
	snapshot.Request = SnapshotRequest{
		Start:      float64(product.windowEnd - int64(key.RangeSeconds)),
		End:        float64(product.windowEnd),
		Resolution: float64(key.ResolutionSeconds),
	}
	snapshot.SourceGeneration = product.sourceGeneration
	snapshot.CacheGeneration = product.cacheGeneration
	snapshot.GeneratedAt = product.publishedAt
	return snapshot, true, nil
}

// CurrentSnapshotReady rejects a cached product while its requested range is
// being rebuilt. Pricing refreshes use this boundary so a catalog-wide refresh
// does not block unrelated, already-current windows or serve stale prices for
// a window with outstanding invalidations.
func (s *StatsService) CurrentSnapshotReady(_ context.Context, rangeSeconds, resolutionSeconds int) (StatsSnapshot, error) {
	return s.currentSnapshot(rangeSeconds, resolutionSeconds, true)
}

func (s *StatsService) currentSnapshot(rangeSeconds, resolutionSeconds int, requireReady bool) (StatsSnapshot, error) {
	if s == nil {
		return StatsSnapshot{}, errors.New("nil YO!stats service")
	}
	key := ProductKey{RangeSeconds: rangeSeconds, ResolutionSeconds: resolutionSeconds}
	s.mu.RLock()
	product, ok := s.products[key]
	reason := s.pending[key]
	s.mu.RUnlock()
	if requireReady && reason != "" {
		return StatsSnapshot{}, fmt.Errorf("%w: %s", ErrProductPending, reason)
	}
	if !ok {
		if reason == "" {
			reason = "product has not been published"
		}
		return StatsSnapshot{}, fmt.Errorf("%w: %s", ErrProductPending, reason)
	}
	snapshot := snapshotWithCoverage(product)
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
