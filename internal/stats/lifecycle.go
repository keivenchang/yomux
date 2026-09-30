package stats

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

// Lifecycle owns the bounded background materialization pass. AppendBatch
// remains synchronous for durability; NotifyAppend records that work exists
// for the next fixed-cadence pass. It must not turn every producer append into
// an extra materialization run while a repair backlog is already active.
type Lifecycle struct {
	writer         *Writer
	config         LifecycleConfig
	materializer   Materializer
	readMu         sync.Mutex
	readDatabase   *Database
	trigger        chan struct{}
	mu             sync.Mutex
	completionMu   sync.Mutex
	completedRuns  uint64
	completionWake chan struct{}
	stop           context.CancelFunc
	done           chan struct{}
	running        bool
	pruneEvery     time.Duration
	lastPruneAt    float64
	vacuumEvery    time.Duration
	vacuumRetry    time.Duration
	vacuumMaxDefer time.Duration
	lastVacuumAt   float64
	vacuumDueSince float64
	vacuumRetryAt  float64
	busy           func() bool
	vacuumMu       sync.Mutex
	demandMu       sync.Mutex
	demands        map[ProductKey]time.Time
	demandRevision atomic.Uint64
	// Open buckets are invalidated by every native sample, but the browser only
	// consumes a concrete resolution at min(resolution, 60s). Retain the last
	// successful fold so a 300s bucket is not rescanned once per second.
	lastMaterializedAt          map[[2]int64]time.Time
	windowMetadataCache         map[int]lifecycleWindowMetadataCache
	backgroundBackfillNotBefore time.Time
	lastBackgroundBackfillAt    time.Time
	startupMu                   sync.Mutex
	startup                     StartupPreparationState
	startupStarted              time.Time
	startupTargets              map[[2]int64]startupPreparationTarget
	counters                    lifecycleCounters
}

type LifecycleConfig struct {
	Cadence           time.Duration
	PruneCadence      time.Duration
	VacuumCadence     time.Duration
	VacuumRetry       time.Duration
	VacuumMaxDefer    time.Duration
	Busy              func() bool
	WindowSeconds     int64
	MaxCells          int
	MaxFactRows       int // maximum decoded rows for 1s, 10s, and 60s folds
	MaxCoarseFactRows int // maximum decoded rows for an individual 300s fold
	MaxDecodedBytes   int
	MaxRunTime        time.Duration
	// BackgroundBackfillCadence limits low-priority cold-ring repair when no
	// product is being requested. A zero value preserves the library's eager
	// behavior for fixtures and callers that explicitly want full convergence.
	// Go serving sets this to keep large historical stores from competing with
	// normal request/collector work at startup.
	BackgroundBackfillCadence      time.Duration
	BackgroundBackfillInitialDelay time.Duration
	// StartupProducts prepares a fixed launch horizon on the existing lifecycle
	// ticks. Empty preserves ordinary background admission for library callers.
	StartupProducts []ProductKey
	Price           PriceResolver
	Now             func() time.Time
	StateFunc       func(LifecycleCounters)
}

type LifecycleCounters struct {
	Runs, Publications, Repairs, Errors, Prunes, Vacuums           uint64
	BackgroundBackfillDeferrals                                    uint64
	SelectedCells, MaterializedBuckets, FactRowsRead, DecodedBytes uint64
	SelectionDuration, MaterializationDuration, FactReadDuration   time.Duration
	LastDuration, LastFactReadDuration, LastSelectionDuration,
	LastMaterializationDuration time.Duration
	LastGeneration     int64
	LastRunSucceeded   bool
	LastRunError       string
	ByResolution       map[int]LifecycleResolutionCounters
	StartupPreparation StartupPreparationState
}

// StartupPreparationState is safe for diagnostics and contains no source data.
type StartupPreparationState struct {
	StartedAt, Horizon       float64
	SourceGeneration         int64
	TotalCells, PendingCells int
	Complete, TimedOut       bool
	Duration                 time.Duration
}

type startupPreparationTarget struct {
	cell            lifecycleCell
	generation      int64
	priority        int
	requireComplete bool
}

// LifecycleResolutionCounters attributes bounded materializer work to the
// resolution that caused it. Aggregate counters alone cannot explain whether
// a CPU spike came from the one-second ring or a coarse cost ring.
type LifecycleResolutionCounters struct {
	SelectionRuns, MaterializationRuns                             uint64
	SelectionCacheHits                                             uint64
	SelectedCells, MaterializedBuckets, FactRowsRead, DecodedBytes uint64
	SelectionDuration, MaterializationDuration                     time.Duration
	LastSelectionDuration, LastMaterializationDuration             time.Duration
}

type lifecycleResolutionCounters struct {
	selectionRuns, materializationRuns                             atomic.Uint64
	selectionCacheHits                                             atomic.Uint64
	selectedCells, materializedBuckets, factRowsRead, decodedBytes atomic.Uint64
	selectionDuration, materializationDuration                     atomic.Uint64
	lastSelectionDuration, lastMaterializationDuration             atomic.Uint64
}

func (c *lifecycleResolutionCounters) snapshot() LifecycleResolutionCounters {
	return LifecycleResolutionCounters{
		SelectionRuns: c.selectionRuns.Load(), MaterializationRuns: c.materializationRuns.Load(),
		SelectionCacheHits: c.selectionCacheHits.Load(),
		SelectedCells:      c.selectedCells.Load(), MaterializedBuckets: c.materializedBuckets.Load(),
		FactRowsRead: c.factRowsRead.Load(), DecodedBytes: c.decodedBytes.Load(),
		SelectionDuration:           time.Duration(c.selectionDuration.Load()),
		MaterializationDuration:     time.Duration(c.materializationDuration.Load()),
		LastSelectionDuration:       time.Duration(c.lastSelectionDuration.Load()),
		LastMaterializationDuration: time.Duration(c.lastMaterializationDuration.Load()),
	}
}

type lifecycleCounters struct {
	runs, publications, repairs, errors, prunes, vacuums           atomic.Uint64
	backgroundBackfillDeferrals                                    atomic.Uint64
	selectedCells, materializedBuckets, factRowsRead, decodedBytes atomic.Uint64
	selectionDuration, materializationDuration, factReadDuration   atomic.Uint64
	lastDuration, lastFactReadDuration, lastSelectionDuration,
	lastMaterializationDuration atomic.Uint64
	byResolution     [4]lifecycleResolutionCounters
	lastGeneration   atomic.Int64
	lastRunSucceeded atomic.Bool
	lastRunErrorMu   sync.RWMutex
	lastRunError     string
}

func (c *lifecycleCounters) snapshot() LifecycleCounters {
	c.lastRunErrorMu.RLock()
	lastRunError := c.lastRunError
	c.lastRunErrorMu.RUnlock()
	byResolution := make(map[int]LifecycleResolutionCounters, len(lifecycleResolutions))
	for index, resolution := range lifecycleResolutions {
		byResolution[resolution] = c.byResolution[index].snapshot()
	}
	return LifecycleCounters{
		Runs: c.runs.Load(), Publications: c.publications.Load(), Repairs: c.repairs.Load(), Errors: c.errors.Load(), Prunes: c.prunes.Load(), Vacuums: c.vacuums.Load(),
		BackgroundBackfillDeferrals: c.backgroundBackfillDeferrals.Load(),
		SelectedCells:               c.selectedCells.Load(), MaterializedBuckets: c.materializedBuckets.Load(), FactRowsRead: c.factRowsRead.Load(), DecodedBytes: c.decodedBytes.Load(),
		SelectionDuration: time.Duration(c.selectionDuration.Load()), MaterializationDuration: time.Duration(c.materializationDuration.Load()), FactReadDuration: time.Duration(c.factReadDuration.Load()),
		LastDuration: time.Duration(c.lastDuration.Load()), LastFactReadDuration: time.Duration(c.lastFactReadDuration.Load()),
		LastSelectionDuration: time.Duration(c.lastSelectionDuration.Load()), LastMaterializationDuration: time.Duration(c.lastMaterializationDuration.Load()),
		LastGeneration: c.lastGeneration.Load(), LastRunSucceeded: c.lastRunSucceeded.Load(), LastRunError: lastRunError, ByResolution: byResolution,
	}
}

func (c *lifecycleCounters) resolution(resolution int) *lifecycleResolutionCounters {
	for index, candidate := range lifecycleResolutions {
		if candidate == resolution {
			return &c.byResolution[index]
		}
	}
	return nil
}

func (c *lifecycleCounters) recordSelection(resolution int, elapsed time.Duration) {
	nanoseconds := uint64(nonNegativeDuration(elapsed).Nanoseconds())
	c.lastSelectionDuration.Store(nanoseconds)
	if counters := c.resolution(resolution); counters != nil {
		counters.selectionRuns.Add(1)
		counters.selectionDuration.Add(nanoseconds)
		counters.lastSelectionDuration.Store(nanoseconds)
	}
}

func (c *lifecycleCounters) recordMaterialization(resolution int, elapsed time.Duration, rows, bytes, buckets int) {
	nanoseconds := uint64(nonNegativeDuration(elapsed).Nanoseconds())
	c.lastMaterializationDuration.Store(nanoseconds)
	if counters := c.resolution(resolution); counters != nil {
		counters.materializationRuns.Add(1)
		counters.materializationDuration.Add(nanoseconds)
		counters.lastMaterializationDuration.Store(nanoseconds)
		counters.factRowsRead.Add(uint64(nonNegativeInt(rows)))
		counters.decodedBytes.Add(uint64(nonNegativeInt(bytes)))
		counters.materializedBuckets.Add(uint64(nonNegativeInt(buckets)))
	}
}

// recordFactRead measures the phase that pulls and decodes persisted facts
// before the in-memory materializer folds them. It includes SQLite stepping,
// database/sql row conversion, and JSON decoding; it deliberately does not
// call that whole interval CPU because SQLite may be waiting on storage.
func (c *lifecycleCounters) recordFactRead(elapsed time.Duration) {
	nanoseconds := uint64(nonNegativeDuration(elapsed).Nanoseconds())
	c.factReadDuration.Add(nanoseconds)
	c.lastFactReadDuration.Store(nanoseconds)
}

func nonNegativeDuration(value time.Duration) time.Duration {
	if value < 0 {
		return 0
	}
	return value
}

func nonNegativeInt(value int) int {
	if value < 0 {
		return 0
	}
	return value
}

func (c *lifecycleCounters) setLastRun(err error) {
	c.lastRunErrorMu.Lock()
	if err == nil {
		c.lastRunError = ""
	} else {
		c.lastRunError = err.Error()
	}
	c.lastRunErrorMu.Unlock()
	c.lastRunSucceeded.Store(err == nil)
}

func NewLifecycle(writer *Writer, config LifecycleConfig) *Lifecycle {
	if config.Cadence <= 0 {
		config.Cadence = time.Second
	}
	if config.PruneCadence <= 0 {
		config.PruneCadence = 24 * time.Hour
	}
	if config.VacuumCadence <= 0 {
		config.VacuumCadence = time.Hour
	}
	if config.VacuumRetry <= 0 {
		config.VacuumRetry = 5 * time.Minute
	}
	if config.VacuumMaxDefer <= 0 {
		config.VacuumMaxDefer = time.Hour
	}
	if config.WindowSeconds <= 0 {
		config.WindowSeconds = RetentionSeconds
	}
	if config.MaxCells <= 0 {
		config.MaxCells = 64
	}
	if config.MaxFactRows <= 0 {
		config.MaxFactRows = defaultLifecycleMaxFactRows
	}
	if config.MaxCoarseFactRows <= 0 {
		config.MaxCoarseFactRows = defaultLifecycleMaxCoarseFactRows
	}
	if config.MaxDecodedBytes <= 0 {
		config.MaxDecodedBytes = 8 * 1024 * 1024
	}
	if config.MaxRunTime <= 0 {
		config.MaxRunTime = defaultLifecycleMaxRunTime
	}
	if config.Now == nil {
		config.Now = time.Now
	}
	backgroundBackfillNotBefore := time.Time{}
	if config.BackgroundBackfillCadence > 0 {
		if config.BackgroundBackfillInitialDelay <= 0 {
			config.BackgroundBackfillInitialDelay = config.BackgroundBackfillCadence
		}
		backgroundBackfillNotBefore = config.Now().Add(config.BackgroundBackfillInitialDelay)
	}
	lastPruneAt := float64(0)
	lastVacuumAt := float64(0)
	if writer != nil {
		lastPruneAt = writer.LastPrunedAt()
		if persisted, err := writer.LastVacuumedAt(context.Background()); err == nil {
			lastVacuumAt = persisted
		}
	}
	return &Lifecycle{writer: writer, config: config, materializer: Materializer{Price: config.Price}, trigger: make(chan struct{}, 1), completionWake: make(chan struct{}), pruneEvery: config.PruneCadence, lastPruneAt: lastPruneAt, vacuumEvery: config.VacuumCadence, vacuumRetry: config.VacuumRetry, vacuumMaxDefer: config.VacuumMaxDefer, lastVacuumAt: lastVacuumAt, busy: config.Busy, demands: make(map[ProductKey]time.Time), lastMaterializedAt: make(map[[2]int64]time.Time), windowMetadataCache: make(map[int]lifecycleWindowMetadataCache), backgroundBackfillNotBefore: backgroundBackfillNotBefore}
}
func (l *Lifecycle) Counters() LifecycleCounters {
	result := l.counters.snapshot()
	l.startupMu.Lock()
	result.StartupPreparation = l.startup
	l.startupMu.Unlock()
	return result
}

func (l *Lifecycle) CompletedRuns() uint64 {
	if l == nil {
		return 0
	}
	l.completionMu.Lock()
	defer l.completionMu.Unlock()
	return l.completedRuns
}

// WaitForRunAfter waits until a complete lifecycle pass, including StateFunc,
// has finished after the supplied completion sequence.
func (l *Lifecycle) WaitForRunAfter(ctx context.Context, sequence uint64) error {
	if l == nil {
		return errors.New("nil stats lifecycle")
	}
	for {
		l.completionMu.Lock()
		if l.completedRuns > sequence {
			l.completionMu.Unlock()
			return nil
		}
		wake := l.completionWake
		l.completionMu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-wake:
		}
	}
}

func (l *Lifecycle) markRunComplete() {
	l.completionMu.Lock()
	l.completedRuns++
	wake := l.completionWake
	l.completionWake = make(chan struct{})
	l.completionMu.Unlock()
	close(wake)
}

// ReadMetrics returns measurements from the lifecycle's long-lived validated
// SQLite reader. It is a diagnostic snapshot only; a zero value is expected
// before the first materialization pass or after shutdown closes the reader.
func (l *Lifecycle) ReadMetrics() ReadMetrics {
	if l == nil {
		return ReadMetrics{}
	}
	l.readMu.Lock()
	database := l.readDatabase
	l.readMu.Unlock()
	if database == nil {
		return ReadMetrics{}
	}
	return database.ReadMetrics()
}

func (l *Lifecycle) Cadence() time.Duration {
	if l == nil || l.config.Cadence <= 0 {
		return time.Second
	}
	return l.config.Cadence
}
func (l *Lifecycle) NotifyAppend() {
	select {
	case l.trigger <- struct{}{}:
	default:
	}
}

// DemandProduct records a bounded, short-lived request for one public product.
// A cold ring can contain more repair work than one cadence can complete; the
// active browser product must be repaired first so a moving window cannot keep
// returning pending forever while unrelated resolutions drain. The demand is a
// scheduling hint only: HTTP still reads immutable service products and never
// performs materialization itself.
func (l *Lifecycle) DemandProduct(rangeSeconds, resolutionSeconds int) {
	if l == nil || rangeSeconds <= 0 || resolutionSeconds <= 0 {
		return
	}
	key := ProductKey{RangeSeconds: rangeSeconds, ResolutionSeconds: resolutionSeconds}
	if !supportedProductKey(key) {
		return
	}
	now := l.config.Now()
	l.demandMu.Lock()
	for demanded, expiry := range l.demands {
		if !expiry.After(now) {
			delete(l.demands, demanded)
		}
	}
	_, alreadyDemanded := l.demands[key]
	// Product keys are a fixed finite catalog, but keep the map bounded if a
	// caller supplies a future catalog extension before this owner is updated.
	if len(l.demands) >= 64 {
		var oldest ProductKey
		var oldestExpiry time.Time
		for demanded, expiry := range l.demands {
			if oldestExpiry.IsZero() || expiry.Before(oldestExpiry) {
				oldest, oldestExpiry = demanded, expiry
			}
		}
		if !oldestExpiry.IsZero() {
			delete(l.demands, oldest)
		}
	}
	l.demands[key] = now.Add(2 * time.Minute)
	// Refreshing an existing browser demand only extends its lease. It does not
	// change the selected product, so avoid forcing an in-flight bounded pass to
	// reselect on every polling request. A new key still interrupts cold-ring
	// work so the newly requested product reaches the front of the queue.
	if !alreadyDemanded {
		l.demandRevision.Add(1)
	}
	l.demandMu.Unlock()
	l.NotifyAppend()
}

func supportedProductKey(key ProductKey) bool {
	capacity, ok := ringCapacities[key.ResolutionSeconds]
	if !ok || key.RangeSeconds <= 0 || key.RangeSeconds%key.ResolutionSeconds != 0 {
		return false
	}
	buckets := key.RangeSeconds / key.ResolutionSeconds
	return buckets >= 12 && buckets <= capacity
}

func (l *Lifecycle) demandedProducts(now time.Time) []ProductKey {
	l.demandMu.Lock()
	defer l.demandMu.Unlock()
	products := make([]ProductKey, 0, len(l.demands))
	for key, expiry := range l.demands {
		if !expiry.After(now) {
			delete(l.demands, key)
			continue
		}
		products = append(products, key)
	}
	sort.Slice(products, func(i, j int) bool {
		if products[i].RangeSeconds != products[j].RangeSeconds {
			return products[i].RangeSeconds < products[j].RangeSeconds
		}
		return products[i].ResolutionSeconds < products[j].ResolutionSeconds
	})
	return products
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
		l.closeReadDatabase()
		return
	}
	stop, done := l.stop, l.done
	l.mu.Unlock()
	stop()
	<-done
	l.closeReadDatabase()
}
func (l *Lifecycle) loop(ctx context.Context) {
	runWithStatsProfileLabels(ctx, "stats_lifecycle", "", func(ctx context.Context) {
		defer func() { l.mu.Lock(); l.running = false; close(l.done); l.mu.Unlock() }()
		ticker := time.NewTicker(l.config.Cadence)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				l.runAndCount(ctx)
				// Append notifications are deliberately coalesced. The ticker is the
				// sole scheduler, so a burst of collectors or a large repair backlog
				// cannot create a busy loop of back-to-back SQLite materializations.
				select {
				case <-l.trigger:
				default:
				}
			}
		}
	})
}

func (l *Lifecycle) runAndCount(ctx context.Context) {
	started := time.Now()
	err := l.RunOnce(ctx)
	elapsed := time.Since(started)
	if elapsed < 0 {
		elapsed = 0
	}
	l.counters.lastDuration.Store(uint64(elapsed.Nanoseconds()))
	if err != nil && !errors.Is(err, context.Canceled) {
		l.counters.errors.Add(1)
	}
	if errors.Is(err, context.Canceled) {
		return
	}
	l.counters.setLastRun(err)
	if l.config.StateFunc != nil {
		l.config.StateFunc(l.Counters())
	}
	l.markRunComplete()
}

func (l *Lifecycle) RunOnce(ctx context.Context) error {
	l.counters.runs.Add(1)
	now := l.config.Now()
	nowSeconds := float64(now.UnixNano()) / 1e9
	if err := l.writer.ReconcileExternalMutationAt(ctx, nowSeconds); err != nil {
		return err
	}
	sourceGenerationBeforeMaintenance := l.writer.sourceGeneration.Load()
	if l.lastPruneAt <= 0 || nowSeconds-l.lastPruneAt >= l.pruneEvery.Seconds() {
		if err := l.Prune(ctx); err != nil {
			return err
		}
		l.lastPruneAt = nowSeconds
	}
	// Maintenance must run before opening this pass's read snapshot: SQLite
	// cannot rewrite the file while a read transaction remains open. The owner
	// itself is the only caller, and HTTP readers consume immutable products.
	if err := l.vacuumIfDue(ctx, nowSeconds); err != nil {
		return err
	}
	database, err := l.openReadDatabase(ctx)
	if err != nil {
		return err
	}
	snapshotDatabase, closeSnapshot, err := database.beginReadSnapshot(ctx)
	if err != nil {
		return err
	}
	defer closeSnapshot()
	generation, err := snapshotDatabase.snapshotGeneration(ctx)
	if err != nil {
		return err
	}
	l.counters.lastGeneration.Store(generation)
	publishedAt := float64(now.UnixNano()) / 1e9
	ringGeneration, _, err := l.writer.RingPublicationState(ctx)
	if err != nil {
		return fmt.Errorf("read ring publication state: %w", err)
	}
	// A direct/legacy writer can leave the in-memory watermark ahead of the
	// read snapshot (for example after a fixture rewrites the source header).
	// Fold the snapshot facts, but publish at the highest already-known
	// generation so RingPublicationState never moves backward.
	publicationGeneration := generation
	if sourceGenerationBeforeMaintenance > publicationGeneration {
		publicationGeneration = sourceGenerationBeforeMaintenance
	}
	cacheGeneration := derivedCacheGeneration(publishedAt, ringGeneration+1)
	pendingBefore, err := l.writer.PendingInvalidationCells(ctx)
	if err != nil {
		return err
	}
	selectionStarted := time.Now()
	// A direct/legacy writer may move the on-disk watermark in either direction
	// without recording ring invalidations. Treat any mismatch with the
	// pre-snapshot watermark as an external generation event so a retry can
	// reconcile interval facts even when a fixture or older owner reused a
	// lower generation number.
	var cells []lifecycleCell
	var selectionErr error
	runWithStatsProfilePhase(ctx, "stats_lifecycle", "stats", "selection", func(phaseCtx context.Context) {
		cells, selectionErr = l.selectCellsFromSnapshotWithReconciliation(phaseCtx, now, generation, snapshotDatabase, sourceGenerationBeforeMaintenance != generation)
	})
	l.counters.selectionDuration.Add(uint64(time.Since(selectionStarted).Nanoseconds()))
	if selectionErr != nil {
		return selectionErr
	}
	l.counters.selectedCells.Add(uint64(len(cells)))
	for _, cell := range cells {
		if counters := l.counters.resolution(cell.ResolutionSeconds); counters != nil {
			counters.selectedCells.Add(1)
		}
	}
	allBuckets := make([]RingBucketWrite, 0, len(cells))
	processed := make(map[[2]int64]bool, len(cells))
	demandRevision := l.demandRevision.Load()
	started := time.Now()
	// MaxRunTime schedules bounded cells; it must not cancel a cell midway.
	// Row and decoded-byte limits bound each atomic fold, and the parent context
	// still cancels promptly during shutdown.
	materializeCtx := ctx
	decodedBytes := 0
	for index := 0; index < len(cells); {
		// HTTP demand can arrive after this pass selected its cold-start repair
		// queue. Reselect before consuming the next cell so the requested product
		// cannot wait behind unrelated resolutions. If the current bounded batch
		// is already full, leave it intact and let the queued notification start a
		// fresh pass with the demand at the head of the selector.
		if currentRevision := l.demandRevision.Load(); currentRevision != demandRevision {
			if len(allBuckets) >= l.config.MaxCells {
				break
			}
			refreshed, selectErr := l.selectCells(ctx, now, generation)
			if selectErr != nil {
				return selectErr
			}
			merged := make([]lifecycleCell, 0, len(refreshed)+len(cells)-index)
			mergedSeen := make(map[[2]int64]bool, len(refreshed)+len(cells)-index)
			appendUnprocessed := func(items []lifecycleCell) {
				for _, candidate := range items {
					key := [2]int64{int64(candidate.ResolutionSeconds), candidate.BucketStart}
					if processed[key] || mergedSeen[key] {
						continue
					}
					mergedSeen[key] = true
					merged = append(merged, candidate)
				}
			}
			appendUnprocessed(refreshed)
			appendUnprocessed(cells[index:])
			cells = merged
			index = 0
			demandRevision = currentRevision
		}
		if len(allBuckets) >= l.config.MaxCells || (len(processed) > 0 && time.Since(started) >= l.config.MaxRunTime) {
			break
		}
		cell := cells[index]
		runEnd := cell.BucketStart + int64(cell.ResolutionSeconds)
		runLimit := index + 1
		maxRunCells := maxMaterializationRunCells
		for runLimit < len(cells) && runLimit-index < maxRunCells && cells[runLimit].ResolutionSeconds == cell.ResolutionSeconds && cells[runLimit].BucketStart == runEnd {
			runEnd += int64(cell.ResolutionSeconds)
			runLimit++
		}
		var buckets []RingBucketWrite
		var bytesRead, rowsRead int
		var err error
		for {
			materializationStarted := time.Now()
			runWithStatsProfilePhase(materializeCtx, "stats_lifecycle", "stats", "materialization", func(phaseCtx context.Context) {
				buckets, bytesRead, rowsRead, err = l.materializeResolution(phaseCtx, snapshotDatabase, cell.BucketStart, runEnd, cell.ResolutionSeconds, cacheGeneration, publishedAt)
			})
			materializationElapsed := time.Since(materializationStarted)
			l.counters.materializationDuration.Add(uint64(nonNegativeDuration(materializationElapsed).Nanoseconds()))
			l.counters.factRowsRead.Add(uint64(rowsRead))
			l.counters.decodedBytes.Add(uint64(bytesRead))
			l.counters.recordMaterialization(cell.ResolutionSeconds, materializationElapsed, rowsRead, bytesRead, len(buckets))
			if !errors.Is(err, errLifecycleBudget) || runLimit-index <= 1 {
				break
			}
			// A contiguous fold can exceed the read budget even when each
			// constituent cell is independently materializable. Narrow the fold
			// before abandoning the whole pass; this keeps dense and sparse cells
			// from blocking one another.
			runLimit = index + (runLimit-index+1)/2
			runEnd = cell.BucketStart + int64(cell.ResolutionSeconds)*int64(runLimit-index)
		}
		if err != nil {
			if errors.Is(err, errLifecycleBudget) {
				// One cell is irreducibly over budget. Leave its durable pending
				// marker for a future retry, but advance this pass so later cells
				// can publish instead of being starved behind it forever.
				for processedIndex := index; processedIndex < runLimit; processedIndex++ {
					processed[[2]int64{int64(cells[processedIndex].ResolutionSeconds), cells[processedIndex].BucketStart}] = true
				}
				index = runLimit
				continue
			}
			if errors.Is(err, context.DeadlineExceeded) && ctx.Err() == nil {
				break
			}
			return err
		}
		if bytesRead > l.config.MaxDecodedBytes || decodedBytes > l.config.MaxDecodedBytes-bytesRead {
			break
		}
		decodedBytes += bytesRead
		selected := make(map[int64]bool, runLimit-index)
		for selectedIndex := index; selectedIndex < runLimit; selectedIndex++ {
			selected[cells[selectedIndex].BucketStart] = true
		}
		for _, bucket := range buckets {
			if selected[bucket.BucketStart] {
				allBuckets = append(allBuckets, bucket)
			}
		}
		l.counters.materializedBuckets.Add(uint64(len(buckets)))
		for processedIndex := index; processedIndex < runLimit; processedIndex++ {
			processed[[2]int64{int64(cells[processedIndex].ResolutionSeconds), cells[processedIndex].BucketStart}] = true
		}
		index = runLimit
	}
	if len(allBuckets) > 0 {
		var publishErr error
		runWithStatsProfilePhase(ctx, "stats_lifecycle", "stats", "publication", func(phaseCtx context.Context) {
			_, publishErr = l.writer.PublishRingBuckets(phaseCtx, allBuckets, publicationGeneration, publishedAt, true)
		})
		if publishErr != nil {
			return publishErr
		}
		for _, bucket := range allBuckets {
			l.lastMaterializedAt[[2]int64{int64(bucket.ResolutionSeconds), bucket.BucketStart}] = now
		}
		l.counters.publications.Add(1)
		if len(pendingBefore) > 0 || len(cells) > len(allBuckets) {
			l.counters.repairs.Add(1)
		}
	}
	return nil
}

func (l *Lifecycle) writerPath() string { return l.writer.lease.path }

// openReadDatabase keeps one validated read-only SQLite connection for the
// lifecycle owner. Reopening it on every one-second pass discarded SQLite's
// pager cache and repeated the full schema validation, turning a bounded ring
// refresh into a continuous cold-read loop. Each pass still gets its own read
// transaction through beginReadSnapshot, so facts remain generation-consistent.
func (l *Lifecycle) openReadDatabase(ctx context.Context) (*Database, error) {
	l.readMu.Lock()
	defer l.readMu.Unlock()
	if l.readDatabase != nil {
		return l.readDatabase, nil
	}
	database, err := OpenReadOnly(ctx, l.writerPath())
	if err != nil {
		return nil, err
	}
	l.readDatabase = database
	return database, nil
}

func (l *Lifecycle) closeReadDatabase() {
	l.readMu.Lock()
	database := l.readDatabase
	l.readDatabase = nil
	l.readMu.Unlock()
	if database != nil {
		_ = database.Close()
	}
}

var lifecycleResolutions = []int{1, 10, 60, 300}

type lifecycleCell struct {
	ResolutionSeconds int
	BucketStart       int64
}

type lifecycleWindowMetadataCache struct {
	Horizon      int64
	End          int64
	Publications uint64
	Rows         map[int64]RingBucketRow
	Missing      []int64
}

func openCellMaterializationCadence(resolution int) time.Duration {
	// Fine resolutions are cheap enough to repair immediately when their
	// invalidation is explicit. The expensive open 300s fold is the measured
	// hot path; its public delivery cadence is 60s.
	if resolution != 300 {
		return 0
	}
	return 60 * time.Second
}

func (l *Lifecycle) openCellDue(now time.Time, cell lifecycleCell) bool {
	cadence := openCellMaterializationCadence(cell.ResolutionSeconds)
	if now.Before(time.Unix(cell.BucketStart+int64(cell.ResolutionSeconds), 0)) {
		last, ok := l.lastMaterializedAt[[2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}]
		if !ok {
			return true
		}
		return cadence <= 0 || now.Sub(last) >= cadence
	}
	return true
}

// backgroundBackfillDue gates low-priority missing/stale ring repair and
// unrequested invalidation repair. A demanded product and restart
// reconciliation bypass this gate. The gate prevents a large cold history
// from being decoded at every one-second lifecycle tick when the server is
// serving other panels.
func (l *Lifecycle) backgroundBackfillDue(now time.Time) bool {
	if l.config.BackgroundBackfillCadence <= 0 {
		return true
	}
	if !l.backgroundBackfillNotBefore.IsZero() && now.Before(l.backgroundBackfillNotBefore) {
		return false
	}
	return l.lastBackgroundBackfillAt.IsZero() || now.Sub(l.lastBackgroundBackfillAt) >= l.config.BackgroundBackfillCadence
}

// startupPreparationCells freezes the launch windows and required revisions.
// Later appends can repair those cells but never extend this finite queue.
func (l *Lifecycle) startupPreparationCells(ctx context.Context, now time.Time, generation int64, pending []InvalidationCell) ([]lifecycleCell, bool, error) {
	if len(l.config.StartupProducts) == 0 {
		return nil, false, nil
	}
	l.startupMu.Lock()
	defer l.startupMu.Unlock()
	if l.startup.Complete || l.startup.TimedOut {
		return nil, false, nil
	}
	first := l.startupTargets == nil
	if l.startupStarted.IsZero() {
		l.startupStarted = now
		l.startup = StartupPreparationState{StartedAt: float64(now.UnixNano()) / 1e9, Horizon: float64(now.UnixNano()) / 1e9, SourceGeneration: generation}
	}
	l.startup.Duration = now.Sub(l.startupStarted)
	if l.startup.Duration >= 120*time.Second {
		l.startup.TimedOut = true
		l.startupTargets = nil
		// Resume ordinary low-priority recovery after its normal cadence.
		l.lastBackgroundBackfillAt = now
		return nil, false, nil
	}
	horizon := l.startupStarted
	ranges := make(map[int]int)
	for _, key := range l.config.StartupProducts {
		if !supportedProductKey(key) {
			continue
		}
		if key.RangeSeconds > ranges[key.ResolutionSeconds] {
			ranges[key.ResolutionSeconds] = key.RangeSeconds
		}
	}
	rows := make(map[[2]int64]RingBucketRow)
	for resolution, windowRange := range ranges {
		window, err := l.writer.ReadRingWindowMetadata(ctx, windowRange, resolution, NextWindowEnd(horizon, resolution))
		if err != nil {
			return nil, false, err
		}
		for _, row := range window.Rows {
			rows[[2]int64{int64(resolution), row.BucketStart}] = row
		}
		for _, row := range window.PendingRows {
			rows[[2]int64{int64(resolution), row.BucketStart}] = row
		}
	}
	pendingGenerations := make(map[[2]int64]int64, len(pending))
	for _, cell := range pending {
		pendingGenerations[[2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}] = cell.SourceGeneration
	}
	if first {
		l.startupTargets = make(map[[2]int64]startupPreparationTarget)
		all := make(map[[2]int64]bool)
		for priority, key := range l.config.StartupProducts {
			if !supportedProductKey(key) {
				continue
			}
			if key == (ProductKey{300, 1}) {
				priority = -1
			}
			end := NextWindowEnd(horizon, key.ResolutionSeconds)
			for start := end - int64(key.RangeSeconds); start < end; start += int64(key.ResolutionSeconds) {
				identity := [2]int64{int64(key.ResolutionSeconds), start}
				if all[identity] {
					if old, ok := l.startupTargets[identity]; ok && priority < old.priority {
						old.priority = priority
						l.startupTargets[identity] = old
					}
					continue
				}
				all[identity] = true
				row, exists := rows[identity]
				required, invalid := pendingGenerations[identity]
				elapsedIncomplete := exists && !row.Complete && start+int64(key.ResolutionSeconds) <= horizon.Unix() && row.PublishedAt < float64(start+int64(key.ResolutionSeconds))
				if exists && !invalid && !elapsedIncomplete {
					continue
				}
				if !invalid {
					required = l.startup.SourceGeneration
				}
				l.startupTargets[identity] = startupPreparationTarget{cell: lifecycleCell{ResolutionSeconds: key.ResolutionSeconds, BucketStart: start}, generation: required, priority: priority, requireComplete: elapsedIncomplete}
			}
		}
		l.startup.TotalCells = len(all)
	} else {
		for identity, target := range l.startupTargets {
			row, exists := rows[identity]
			pendingGeneration, invalid := pendingGenerations[identity]
			if exists && row.SourceGeneration >= target.generation && (!target.requireComplete || row.Complete) && (!invalid || pendingGeneration > l.startup.SourceGeneration) {
				delete(l.startupTargets, identity)
			}
		}
	}
	l.startup.PendingCells = len(l.startupTargets)
	if len(l.startupTargets) == 0 {
		l.startup.Complete = true
		l.startupTargets = nil
		l.lastBackgroundBackfillAt = now
		return nil, false, nil
	}
	targets := make([]startupPreparationTarget, 0, len(l.startupTargets))
	for _, target := range l.startupTargets {
		targets = append(targets, target)
	}
	sort.Slice(targets, func(i, j int) bool {
		if targets[i].priority != targets[j].priority {
			return targets[i].priority < targets[j].priority
		}
		if targets[i].cell.ResolutionSeconds != targets[j].cell.ResolutionSeconds {
			return targets[i].cell.ResolutionSeconds < targets[j].cell.ResolutionSeconds
		}
		return targets[i].cell.BucketStart < targets[j].cell.BucketStart
	})
	cells := make([]lifecycleCell, 0, min(len(targets), l.config.MaxCells))
	for _, target := range targets {
		if l.openCellDue(now, target.cell) {
			cells = append(cells, target.cell)
		}
		if len(cells) >= l.config.MaxCells {
			break
		}
	}
	return cells, true, nil
}

var errLifecycleBudget = errors.New("stats lifecycle materialization budget exhausted")

const maxStaleRowsPerResolution = 4

// Keep one materialization fold below the fact/byte budgets even when a
// demanded product has a long contiguous cold range. A whole 24-hour coarse
// range can otherwise exceed MaxFactRows and remain pending forever.
const maxMaterializationRunCells = 4

// Live service-load history can contain one observation per OS thread each
// second. A 300-second cell can exceed the public 10,000-row read limit while
// still fitting the lifecycle's bounded 8 MiB decoded-byte budget.
const defaultLifecycleMaxFactRows = 25000

// MaxRunTime limits how much work a pass starts. A bounded cell already in
// progress is allowed to finish atomically before the next pass yields.
const defaultLifecycleMaxRunTime = 500 * time.Millisecond

// A five-minute CPU bucket can hold one process/thread observation per second.
// Give that single coarse fold room for dense hosts while retaining the decoded
// byte and wall-time limits that bound its memory and foreground cost.
const defaultLifecycleMaxCoarseFactRows = 50000

// selectCells is the sole restart/rebuild work selector. Pending ledger rows
// are authoritative contradictions; missing slots are durable work even when
// no ledger row exists; the current carrier keeps the visible tail fresh.
func (l *Lifecycle) selectCells(ctx context.Context, now time.Time, generation int64) ([]lifecycleCell, error) {
	return l.selectCellsFromSnapshot(ctx, now, generation, nil)
}

// selectCellsFromSnapshot is the lifecycle work selector. When a consistent
// source snapshot is available, interval facts are also used to target stale
// demanded cells; this is needed for restart/reconciliation cases where an old
// writer advanced source_generation without leaving a per-cell ledger row.
func (l *Lifecycle) selectCellsFromSnapshot(ctx context.Context, now time.Time, generation int64, sourceSnapshot *Database) ([]lifecycleCell, error) {
	return l.selectCellsFromSnapshotWithReconciliation(ctx, now, generation, sourceSnapshot, false)
}

func (l *Lifecycle) selectCellsFromSnapshotWithReconciliation(ctx context.Context, now time.Time, generation int64, sourceSnapshot *Database, externalGeneration bool) ([]lifecycleCell, error) {
	pending, err := l.writer.PendingInvalidationCells(ctx)
	if err != nil {
		return nil, err
	}
	startupCells, preparing, err := l.startupPreparationCells(ctx, now, generation, pending)
	if err != nil {
		return nil, err
	}
	pendingKeys := make(map[[2]int64]bool, len(pending))
	pendingResolutions := make(map[int]bool)
	for _, cell := range pending {
		pendingKeys[[2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}] = true
		pendingResolutions[cell.ResolutionSeconds] = true
	}
	// A direct/legacy writer can advance schema_meta without updating this
	// writer's in-memory generation or recording cell-level invalidations. Keep
	// a bounded reconciliation queue alive even when ordinary pending telemetry
	// is present; otherwise the global generation mismatch can be hidden forever
	// behind a continuously refreshed pending cell.
	writerGeneration := l.writer.sourceGeneration.Load()
	// A mismatch asks the interval reconciler to inspect direct/legacy facts;
	// only a writer watermark behind the read snapshot makes every older ring
	// row stale. Keeping those cases separate avoids turning a lower legacy
	// watermark into a full cold-ring rebuild.
	generationMismatch := externalGeneration || writerGeneration != generation
	untrackedGeneration := writerGeneration < generation
	type ringWindow struct {
		start, end int64
		missing    []int64
		stale      []int64
		rows       map[int64]RingBucketRow
	}
	windows := make(map[int]ringWindow, len(lifecycleResolutions))
	for _, resolution := range lifecycleResolutions {
		horizon := l.config.WindowSeconds
		if ringHorizon := int64(ringCapacities[resolution] * resolution); horizon > ringHorizon {
			horizon = ringHorizon
		}
		if horizon < int64(resolution) {
			horizon = int64(resolution)
		}
		// ReadRingWindow enumerates bucket identities from windowEnd-horizon;
		// the horizon itself must therefore be resolution-aligned or the first
		// generated address would not be a legal ring bucket.
		horizon -= horizon % int64(resolution)
		if horizon < int64(resolution) {
			horizon = int64(resolution)
		}
		end := NextWindowEnd(now, resolution)
		start := end - horizon
		if remainder := start % int64(resolution); remainder != 0 {
			if remainder < 0 {
				remainder += int64(resolution)
			}
			start -= remainder
		}
		var rows map[int64]RingBucketRow
		var missing []int64
		cacheable := !generationMismatch && !pendingResolutions[resolution]
		publicationCount := l.counters.publications.Load()
		if cacheable {
			if cached, ok := l.windowMetadataCache[resolution]; ok && cached.Horizon == horizon && cached.End == end && cached.Publications == publicationCount {
				rows = cached.Rows
				missing = cached.Missing
				if counters := l.counters.resolution(resolution); counters != nil {
					counters.selectionCacheHits.Add(1)
				}
			}
		}
		if rows == nil {
			selectionStarted := time.Now()
			window, readErr := l.writer.ReadRingWindowMetadata(ctx, int(horizon), resolution, end)
			l.counters.recordSelection(resolution, time.Since(selectionStarted))
			if readErr != nil {
				return nil, readErr
			}
			rows = make(map[int64]RingBucketRow, len(window.Rows))
			for _, row := range window.Rows {
				rows[row.BucketStart] = row
			}
			missing = window.MissingBucketStarts
			if cacheable {
				l.windowMetadataCache[resolution] = lifecycleWindowMetadataCache{Horizon: horizon, End: end, Publications: publicationCount, Rows: rows, Missing: missing}
			}
		}
		stale := make([]int64, 0, len(rows))
		// A source generation is global, but a normal append records the exact
		// affected cells in the durable invalidation ledger. Once that ledger is
		// present, treating every older row as stale turns unrelated historical
		// rows into an endless repair queue while browser telemetry advances the
		// generation each cadence. Keep the generation fallback only for a ring
		// with no ledger at all (legacy/direct-writer reconciliation); normal
		// mutations are selected by their precise pending cells below.
		generationFallback := len(pending) == 0 || untrackedGeneration
		for bucket, row := range rows {
			// A ring cell published before its bucket ended stays incomplete after
			// it ages out of the live edge. Fold that elapsed cell once more so its
			// final facts are retained without a synthetic persisted-ring gap. The
			// publication time distinguishes that lifecycle cutoff from legacy
			// incomplete rows that were intentionally retained after their interval.
			bucketEnd := bucket + int64(resolution)
			elapsedIncomplete := !row.Complete && bucketEnd <= now.Unix() && row.PublishedAt < float64(bucketEnd)
			if elapsedIncomplete || (generationFallback && row.SourceGeneration < generation) {
				stale = append(stale, bucket)
			}
		}
		sort.Slice(stale, func(i, j int) bool { return stale[i] > stale[j] })
		windows[resolution] = ringWindow{start: start, end: end, missing: missing, stale: stale, rows: rows}
	}
	seen := make(map[[2]int64]bool)
	carrierNeedsWork := func(rows map[int64]RingBucketRow, carrier int64) bool {
		row, present := rows[carrier]
		// An open bucket can remain incomplete without any new source facts.
		// Explicit contradictions are selected separately from the ledger.
		return !present || row.SourceGeneration < generation
	}
	add := func(result *[]lifecycleCell, resolution int, start int64) {
		key := [2]int64{int64(resolution), start}
		if seen[key] {
			return
		}
		cell := lifecycleCell{ResolutionSeconds: resolution, BucketStart: start}
		if !l.openCellDue(now, cell) {
			return
		}
		seen[key] = true
		*result = append(*result, cell)
	}
	selected := make([]lifecycleCell, 0, l.config.MaxCells)
	demands := l.demandedProducts(now)
	if preparing && len(demands) == 0 {
		// The finite launch horizon bounds background work, while the normal
		// demand path also keeps the default graph's advancing live edge ready.
		// A real request always takes precedence over this startup-only hint.
		for _, key := range l.config.StartupProducts {
			if key == (ProductKey{300, 1}) {
				demands = append(demands, key)
				break
			}
		}
	}
	backgroundBackfillDue := l.backgroundBackfillDue(now)
	if backgroundBackfillDue && l.config.BackgroundBackfillCadence > 0 {
		// Consume the low-priority scheduling slot once per cadence. Explicit
		// demands and invalidations do not consume this budget.
		l.lastBackgroundBackfillAt = now
	}
	demandedResolutions := make(map[int]struct{}, len(demands))
	for _, demand := range demands {
		demandedResolutions[demand.ResolutionSeconds] = struct{}{}
	}
	if !preparing && !backgroundBackfillDue && len(demands) == 0 && !generationMismatch {
		// Pending cells are still durable and will be repaired on the next
		// low-priority slot. Without a requested product, spending the full
		// materialization budget on a cold or interrupted history is unnecessary
		// foreground work and was the measured source of the synthetic CPU spike.
		l.counters.backgroundBackfillDeferrals.Add(1)
		return selected, nil
	}
	demandedBuckets := make(map[ProductKey]map[int64]bool, len(demands))
	intervalBuckets := make(map[ProductKey]map[int64]bool, len(demands))
	intervalSpans := make(map[int][]UnavailableSpan)
	intervalSpanLoaded := make(map[int]bool)
	for _, demand := range demands {
		bounds, ok := windows[demand.ResolutionSeconds]
		if !ok {
			continue
		}
		windowStart := bounds.end - int64(demand.RangeSeconds)
		buckets := make(map[int64]bool)
		for _, cell := range pending {
			if cell.ResolutionSeconds == demand.ResolutionSeconds && cell.BucketStart >= windowStart && cell.BucketStart < bounds.end {
				buckets[cell.BucketStart] = true
			}
		}
		demandedBuckets[demand] = buckets
		if sourceSnapshot == nil {
			continue
		}
		if !intervalSpanLoaded[demand.ResolutionSeconds] {
			selectionStarted := time.Now()
			spans, readErr := sourceSnapshot.ReadUnavailableSpans(ctx, float64(bounds.start), float64(bounds.end), min(l.config.MaxFactRows, MaxFactReadLimit))
			l.counters.recordSelection(demand.ResolutionSeconds, time.Since(selectionStarted))
			if readErr != nil {
				return nil, readErr
			}
			intervalSpans[demand.ResolutionSeconds] = spans
			intervalSpanLoaded[demand.ResolutionSeconds] = true
		}
		affected := make(map[int64]bool)
		spans := intervalSpans[demand.ResolutionSeconds]
		for _, span := range spans {
			start := math.Max(float64(bounds.start), span.StartedAt)
			end := math.Min(float64(bounds.end), span.EndedAt)
			if start >= end {
				continue
			}
			first := invalidationBucket(demand.ResolutionSeconds, start)
			last := invalidationBucket(demand.ResolutionSeconds, math.Nextafter(end, start))
			for bucket := first; bucket <= last; bucket += int64(demand.ResolutionSeconds) {
				row, present := bounds.rows[bucket]
				key := [2]int64{int64(demand.ResolutionSeconds), bucket}
				// A persistent incomplete row is not actionable by itself: bounded
				// no-data/token history can remain incomplete indefinitely. Normal
				// writes record the exact cell in ring_invalidations; only a missing
				// row, that explicit contradiction, or an untracked external
				// generation bump justifies another interval-derived fold.
				// A lower legacy watermark can make a repaired ring row look newer
				// than the read snapshot. The mismatch itself is the evidence that an
				// interval fact may be absent from that row, so force its cell back
				// through the materializer instead of trusting the row generation.
				forceExternalInterval := generationMismatch && writerGeneration > generation
				reconcile := forceExternalInterval || (generationMismatch && (!present || row.SourceGeneration < generation || !row.Complete))
				if !present || pendingKeys[key] || reconcile {
					affected[bucket] = true
				}
			}
		}
		intervalBuckets[demand] = affected
	}
	// Keep unrelated cold-ring repair behind an active product demand only
	// while that demanded product still needs work. Once every requested bucket
	// is current, normal catalog backfill may resume; otherwise a short-lived
	// browser request could permanently prevent the other advertised products
	// from converging.
	demandNeedsWork := false
	for _, demand := range demands {
		bounds, ok := windows[demand.ResolutionSeconds]
		if !ok {
			continue
		}
		windowStart := bounds.end - int64(demand.RangeSeconds)
		if len(demandedBuckets[demand]) != 0 || len(intervalBuckets[demand]) != 0 {
			demandNeedsWork = true
			break
		}
		for _, bucket := range bounds.missing {
			if bucket >= windowStart && bucket < bounds.end {
				demandNeedsWork = true
				break
			}
		}
		if demandNeedsWork {
			break
		}
		for _, bucket := range bounds.stale {
			if bucket >= windowStart && bucket < bounds.end {
				demandNeedsWork = true
				break
			}
		}
		if demandNeedsWork {
			break
		}
		carrier := bounds.end - int64(demand.ResolutionSeconds)
		if carrierNeedsWork(bounds.rows, carrier) {
			demandNeedsWork = true
			break
		}
	}
	// Keep a small fairness reserve for durable contradictions outside the
	// currently demanded product. Interval-derived no-data repair is important,
	// but allowing it to consume the whole bounded pass leaves unrelated ledger
	// rows pending forever and makes the lifecycle reread the same demand cells
	// on every cadence.
	claimedByDemand := func(cell InvalidationCell) bool {
		for _, demand := range demands {
			if cell.ResolutionSeconds == demand.ResolutionSeconds && demandedBuckets[demand][cell.BucketStart] {
				return true
			}
		}
		return false
	}
	if len(pending) > 0 && l.config.MaxCells > 0 {
		reserve := l.config.MaxCells / 4
		if reserve < 1 {
			reserve = 1
		}
		// A one-cell pass cannot satisfy both fairness and an active product
		// demand. Keep the sole slot for the demanded product; larger passes
		// retain the bounded unrelated-work reserve above.
		if len(demands) != 0 && reserve >= l.config.MaxCells {
			reserve = l.config.MaxCells - 1
		}
		if reserve > 0 {
			reserved := 0
			for _, cell := range pending {
				if claimedByDemand(cell) {
					continue
				}
				bounds, ok := windows[cell.ResolutionSeconds]
				if !ok || cell.BucketStart < bounds.start || cell.BucketStart >= bounds.end {
					continue
				}
				add(&selected, cell.ResolutionSeconds, cell.BucketStart)
				reserved++
				if reserved >= reserve || len(selected) >= l.config.MaxCells {
					break
				}
			}
		}
	}
	// Interval-derived cells are selected before generic stale repair. This
	// preserves exact no-data boundaries for a demanded product without making
	// all stale rows in a cold ring equally urgent.
	for _, demand := range demands {
		buckets := make([]int64, 0, len(intervalBuckets[demand]))
		for bucket := range intervalBuckets[demand] {
			buckets = append(buckets, bucket)
		}
		sort.Slice(buckets, func(i, j int) bool { return buckets[i] < buckets[j] })
		for _, bucket := range buckets {
			add(&selected, demand.ResolutionSeconds, bucket)
			if len(selected) >= l.config.MaxCells {
				break
			}
		}
		if len(selected) >= l.config.MaxCells {
			break
		}
	}
	// Contradictions in a demanded product are first, followed by its missing
	// cells. Other pending cells remain durable and are serviced afterward; they
	// must not starve the product blocking the browser.
	for _, demand := range demands {
		for _, cell := range pending {
			if !demandedBuckets[demand][cell.BucketStart] || cell.ResolutionSeconds != demand.ResolutionSeconds {
				continue
			}
			add(&selected, demand.ResolutionSeconds, cell.BucketStart)
			if len(selected) >= l.config.MaxCells {
				break
			}
		}
		if len(selected) >= l.config.MaxCells {
			break
		}
	}
	for _, demand := range demands {
		if len(selected) >= l.config.MaxCells {
			break
		}
		bounds, ok := windows[demand.ResolutionSeconds]
		if !ok {
			continue
		}
		windowStart := bounds.end - int64(demand.RangeSeconds)
		for _, bucket := range bounds.missing {
			if bucket < windowStart || bucket >= bounds.end {
				continue
			}
			add(&selected, demand.ResolutionSeconds, bucket)
			if len(selected) >= l.config.MaxCells {
				break
			}
		}
	}
	// A demanded product also owns stale populated rows when a source
	// generation advanced without an invalidation ledger entry. Repair its
	// newest stale cells before unrelated fine-resolution work can consume the
	// bounded pass.
	for _, demand := range demands {
		if len(selected) >= l.config.MaxCells {
			break
		}
		bounds, ok := windows[demand.ResolutionSeconds]
		if !ok {
			continue
		}
		windowStart := bounds.end - int64(demand.RangeSeconds)
		for _, bucket := range bounds.stale {
			if bucket < windowStart || bucket >= bounds.end {
				continue
			}
			add(&selected, demand.ResolutionSeconds, bucket)
			if len(selected) >= l.config.MaxCells {
				break
			}
		}
	}
	// Admitted startup history follows current product work and precedes
	// unrelated repair. Its fixed targets never grow with incoming samples.
	for _, cell := range startupCells {
		if len(selected) >= l.config.MaxCells {
			break
		}
		add(&selected, cell.ResolutionSeconds, cell.BucketStart)
	}
	for _, cell := range pending {
		claimed := false
		for _, demand := range demands {
			if demandedBuckets[demand][cell.BucketStart] && cell.ResolutionSeconds == demand.ResolutionSeconds {
				claimed = true
				break
			}
		}
		if claimed {
			continue
		}
		bounds, ok := windows[cell.ResolutionSeconds]
		if !ok || cell.BucketStart < bounds.start || cell.BucketStart >= bounds.end {
			continue
		}
		add(&selected, cell.ResolutionSeconds, cell.BucketStart)
		if len(selected) >= l.config.MaxCells {
			break
		}
	}
	// A source generation can advance without a ledger row when recovering a
	// database written by an older owner or after an interrupted migration.
	// Treat populated rows from an older source generation as durable repair
	// work as well. Prefer the newest rows so the active browser window catches
	// up within the bounded cell budget, then sort the final set for contiguous
	// materialization below.
	for _, resolution := range lifecycleResolutions {
		if len(selected) >= l.config.MaxCells {
			break
		}
		if !backgroundBackfillDue {
			break
		}
		if (len(pending) != 0 || len(demands) != 0) && !untrackedGeneration {
			// While an active demand or contradiction exists, the precise pending
			// and missing/carrier queues above are authoritative. A global source
			// generation does not make every older row stale, and selecting those
			// rows here would starve the requested window under telemetry churn.
			break
		}
		bounds := windows[resolution]
		stale := append([]int64(nil), bounds.stale...)
		remaining := l.config.MaxCells - len(selected)
		if remaining > maxStaleRowsPerResolution {
			remaining = maxStaleRowsPerResolution
		}
		if len(stale) > remaining {
			stale = stale[:remaining]
		}
		sort.Slice(stale, func(i, j int) bool { return stale[i] < stale[j] })
		for _, bucket := range stale {
			add(&selected, resolution, bucket)
			if len(selected) >= l.config.MaxCells {
				break
			}
		}
		if len(selected) >= l.config.MaxCells {
			break
		}
	}
	// Reserve the current carrier before the older missing backlog so a cold
	// ring can produce a current browser product while repair drains gradually.
	for _, resolution := range lifecycleResolutions {
		if !backgroundBackfillDue && len(demands) == 0 && !generationMismatch {
			break
		}
		bounds := windows[resolution]
		if demandNeedsWork {
			if _, demanded := demandedResolutions[resolution]; !demanded {
				continue
			}
		}
		carrier := bounds.end - int64(resolution)
		if carrierNeedsWork(bounds.rows, carrier) {
			add(&selected, resolution, carrier)
		}
	}
	// Drain missing cells round-robin instead of exhausting the finest ring
	// first. A cold start must make every demanded resolution usable within a
	// bounded number of passes; otherwise the 1s backlog can starve the 10s,
	// 60s, and 300s products that the browser actually requests.
	if !backgroundBackfillDue && len(demands) == 0 && !generationMismatch {
		return selected, nil
	}
	missingIndex := make(map[int]int, len(lifecycleResolutions))
	for len(selected) < l.config.MaxCells {
		progress := false
		for _, resolution := range lifecycleResolutions {
			if demandNeedsWork {
				if _, demanded := demandedResolutions[resolution]; !demanded {
					continue
				}
			}
			missing := windows[resolution].missing
			consumed := missingIndex[resolution]
			if consumed >= len(missing) {
				continue
			}
			// The newest cells are the ones a returning browser can request now;
			// repair the cold tail after the active window is complete.
			add(&selected, resolution, missing[len(missing)-1-consumed])
			missingIndex[resolution] = consumed + 1
			progress = true
			if len(selected) >= l.config.MaxCells {
				break
			}
		}
		if !progress {
			break
		}
	}
	if len(selected) > l.config.MaxCells {
		selected = selected[:l.config.MaxCells]
	}
	// Keep the ordinary cold-start queue materializer-ordered so adjacent cells
	// can share one bounded fold. Once a demand or contradiction exists, retain
	// that priority across resolutions: sorting solely by resolution would put a
	// missing 1s carrier ahead of a demanded 10s contradiction and make the
	// request wait behind unrelated repair. Within each priority class, keep
	// cells ordered by resolution and bucket for contiguous materialization.
	if len(pending) == 0 && len(demands) == 0 {
		sort.SliceStable(selected, func(i, j int) bool {
			if selected[i].ResolutionSeconds != selected[j].ResolutionSeconds {
				return selected[i].ResolutionSeconds < selected[j].ResolutionSeconds
			}
			return selected[i].BucketStart < selected[j].BucketStart
		})
	} else {
		pendingCells := make(map[[2]int64]bool, len(pending))
		for _, cell := range pending {
			pendingCells[[2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}] = true
		}
		cellPriority := func(cell lifecycleCell) int {
			for _, demand := range demands {
				if cell.ResolutionSeconds != demand.ResolutionSeconds {
					continue
				}
				bounds := windows[demand.ResolutionSeconds]
				windowStart := bounds.end - int64(demand.RangeSeconds)
				if cell.BucketStart < windowStart || cell.BucketStart >= bounds.end {
					continue
				}
				key := [2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}
				if demandedBuckets[demand][cell.BucketStart] || intervalBuckets[demand][cell.BucketStart] {
					return 0
				}
				for _, bucket := range bounds.missing {
					if bucket == cell.BucketStart {
						return 0
					}
				}
				for _, bucket := range bounds.stale {
					if bucket == cell.BucketStart {
						return 1
					}
				}
				if pendingCells[key] || cell.BucketStart == bounds.end-int64(cell.ResolutionSeconds) {
					return 1
				}
				if carrierNeedsWork(bounds.rows, cell.BucketStart) {
					return 1
				}
			}
			if pendingCells[[2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}] {
				return 2
			}
			return 3
		}
		sort.SliceStable(selected, func(i, j int) bool {
			left, right := selected[i], selected[j]
			leftPriority, rightPriority := cellPriority(left), cellPriority(right)
			if leftPriority != rightPriority {
				return leftPriority < rightPriority
			}
			if left.ResolutionSeconds != right.ResolutionSeconds {
				return left.ResolutionSeconds < right.ResolutionSeconds
			}
			return left.BucketStart < right.BucketStart
		})
	}
	return selected, nil
}

func (l *Lifecycle) materializeResolution(ctx context.Context, database *Database, start, end int64, resolution int, cacheGeneration int64, publishedAt float64) ([]RingBucketWrite, int, int, error) {
	accumulator, err := newMaterializerAccumulator(MaterializeRequest{Start: float64(start), End: float64(end), Resolution: float64(resolution), ObservedUntil: &publishedAt}, l.materializer.Price)
	if err != nil {
		return nil, 0, 0, err
	}
	rowsRead, decodedBytes := 0, 0
	maxFactRows := l.config.MaxFactRows
	if resolution == 300 {
		maxFactRows = l.config.MaxCoarseFactRows
	}
	factReadStarted := time.Now()
	factReadRecorded := false
	defer func() {
		if !factReadRecorded {
			l.counters.recordFactRead(time.Since(factReadStarted))
		}
	}()
	account := func(count, bytes int) error {
		if count < 0 || bytes < 0 || rowsRead > maxFactRows-count || decodedBytes > l.config.MaxDecodedBytes-bytes {
			return errLifecycleBudget
		}
		rowsRead += count
		decodedBytes += bytes
		return nil
	}
	var observationCursor *observationPageCursor
	for {
		page, next, readErr := database.ReadObservationsPage(ctx, float64(start), float64(end), observationCursor, FactPageSize)
		if readErr != nil {
			return nil, decodedBytes, rowsRead, readErr
		}
		pageBytes := estimateDecodedBytes(page, nil, nil, nil)
		if err := account(len(page), pageBytes); err != nil {
			return nil, decodedBytes, rowsRead, err
		}
		if err := accumulator.addObservations(page, rowsRead-len(page)); err != nil {
			return nil, decodedBytes, rowsRead, err
		}
		if len(page) < FactPageSize {
			break
		}
		observationCursor = next
	}
	var coverageCursor *coveragePageCursor
	for {
		page, next, readErr := database.ReadCoverageEpochsPage(ctx, float64(start), float64(end), coverageCursor, FactPageSize)
		if readErr != nil {
			return nil, decodedBytes, rowsRead, readErr
		}
		pageBytes := estimateDecodedBytes(nil, page, nil, nil)
		if err := account(len(page), pageBytes); err != nil {
			return nil, decodedBytes, rowsRead, err
		}
		accumulator.addCoverage(page)
		if len(page) < FactPageSize {
			break
		}
		coverageCursor = next
	}
	var unavailableCursor *unavailablePageCursor
	for {
		page, next, readErr := database.ReadUnavailableSpansPage(ctx, float64(start), float64(end), unavailableCursor, FactPageSize)
		if readErr != nil {
			return nil, decodedBytes, rowsRead, readErr
		}
		pageBytes := estimateDecodedBytes(nil, nil, page, nil)
		if err := account(len(page), pageBytes); err != nil {
			return nil, decodedBytes, rowsRead, err
		}
		if err := accumulator.addUnavailable(page); err != nil {
			return nil, decodedBytes, rowsRead, err
		}
		if len(page) < FactPageSize {
			break
		}
		unavailableCursor = next
	}
	var usageCursor *usagePageCursor
	for {
		page, next, readErr := database.ReadUsageAtomsPage(ctx, float64(start), float64(end), usageCursor, FactPageSize)
		if readErr != nil {
			return nil, decodedBytes, rowsRead, readErr
		}
		pageBytes := estimateDecodedBytes(nil, nil, nil, page)
		if err := account(len(page), pageBytes); err != nil {
			return nil, decodedBytes, rowsRead, err
		}
		if err := accumulator.addUsage(page, rowsRead-len(page)); err != nil {
			return nil, decodedBytes, rowsRead, err
		}
		if len(page) < FactPageSize {
			break
		}
		usageCursor = next
	}
	l.counters.recordFactRead(time.Since(factReadStarted))
	factReadRecorded = true
	layer := accumulator.finish()
	if len(layer.Buckets) == 0 {
		return nil, decodedBytes, rowsRead, nil
	}
	buckets := make([]RingBucketWrite, 0, len(layer.Buckets))
	for _, bucket := range layer.Buckets {
		payload, encodeErr := encodeRingBucketPayload(bucket, ringNoDataForBucket(layer, bucket), cacheGeneration, publishedAt)
		if encodeErr != nil {
			return nil, 0, rowsRead, fmt.Errorf("encode materialized bucket: %w", encodeErr)
		}
		buckets = append(buckets, RingBucketWrite{ResolutionSeconds: resolution, BucketStart: int64(bucket.Start), BucketJSON: payload, Complete: bucket.Complete})
	}
	return buckets, decodedBytes, rowsRead, nil
}

func estimateDecodedBytes(observations []Observation, epochs []CoverageEpoch, spans []UnavailableSpan, atoms []UsageAtom) int {
	bytes := 0
	for _, value := range observations {
		payloadBytes := value.payloadBytes
		if payloadBytes == 0 {
			// Synthetic/in-memory callers do not have a storage-side byte count.
			// Preserve their accounting without imposing a second JSON encode on
			// rows decoded from SQLite.
			payloadBytes = len(mustJSON(value.Payload))
		}
		bytes += payloadBytes + len(value.EventID) + len(value.Family) + len(value.SourceID) + len(value.EpochID) + 64
	}
	for _, value := range epochs {
		bytes += len(value.Family) + len(value.SourceID) + len(value.EpochID) + 64
	}
	for _, value := range spans {
		bytes += len(value.Family) + len(value.SourceID) + len(value.EpochID) + len(value.Reason) + 80
	}
	for _, value := range atoms {
		payloadBytes := value.payloadBytes
		if payloadBytes == 0 {
			payloadBytes = len(mustJSON(value.Payload))
		}
		bytes += len(value.EventID) + len(value.Direction) + len(value.Modality) + len(value.CacheRole) + len(value.Unit) + payloadBytes + 64
	}
	return bytes
}

func mustJSON(value map[string]any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return "{}"
	}
	return string(encoded)
}

// Prune and Vacuum are explicit maintenance hooks so scheduling policy stays
// outside the fact writer and can be exercised deterministically by services.
func (l *Lifecycle) Prune(ctx context.Context) error {
	l.counters.prunes.Add(1)
	now := float64(l.config.Now().UnixNano()) / 1e9
	_, err := l.writer.Prune(ctx, now)
	if err == nil {
		l.lastPruneAt = now
	}
	return err
}
func (l *Lifecycle) Vacuum(ctx context.Context) error {
	l.counters.vacuums.Add(1)
	completedAt := float64(l.config.Now().UnixNano()) / 1e9
	err := l.writer.Vacuum(ctx, completedAt)
	if err == nil {
		l.lastVacuumAt = completedAt
		l.vacuumDueSince = 0
		l.vacuumRetryAt = 0
	}
	return err
}

func (l *Lifecycle) vacuumIfDue(ctx context.Context, now float64) error {
	if l == nil || l.writer == nil || l.vacuumEvery <= 0 {
		return nil
	}
	l.vacuumMu.Lock()
	defer l.vacuumMu.Unlock()
	if l.lastVacuumAt > 0 && now-l.lastVacuumAt < l.vacuumEvery.Seconds() {
		return nil
	}
	// A failed rewrite is still due, but it must not turn the normal one-second
	// materialization cadence into a busy retry loop. Keep the due clock alive
	// so the max-defer policy remains meaningful, and retry only at the explicit
	// maintenance retry deadline.
	if l.vacuumRetryAt > now {
		return nil
	}
	if l.vacuumDueSince == 0 {
		l.vacuumDueSince = now
	}
	busy := l.busy != nil && l.busy()
	deferred := busy && now-l.vacuumDueSince < l.vacuumMaxDefer.Seconds()
	if deferred {
		return nil
	}
	// Avoid rewriting healthy stores. If dbstat is unavailable, fail open and
	// let Vacuum report the concrete SQLite error instead of silently disabling
	// maintenance forever.
	if ratio, err := l.writer.ReclaimableRatio(ctx); err == nil {
		baseline := l.writer.reclaimableRatioAtLastVacuum()
		if ratio-baseline < 0.15 && !busy {
			l.lastVacuumAt = now
			l.vacuumDueSince = 0
			l.vacuumRetryAt = 0
			return nil
		}
	}
	if err := l.writer.Vacuum(ctx, now); err != nil {
		l.vacuumRetryAt = now + l.vacuumRetry.Seconds()
		return err
	}
	l.counters.vacuums.Add(1)
	l.lastVacuumAt = now
	l.vacuumDueSince = 0
	l.vacuumRetryAt = 0
	return nil
}
