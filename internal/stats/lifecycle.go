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
	backgroundBackfillNotBefore time.Time
	lastBackgroundBackfillAt    time.Time
	counters                    lifecycleCounters
}

type LifecycleConfig struct {
	Cadence         time.Duration
	PruneCadence    time.Duration
	VacuumCadence   time.Duration
	VacuumRetry     time.Duration
	VacuumMaxDefer  time.Duration
	Busy            func() bool
	WindowSeconds   int64
	MaxCells        int
	MaxFactRows     int
	MaxDecodedBytes int
	MaxRunTime      time.Duration
	// BackgroundBackfillCadence limits low-priority cold-ring repair when no
	// product is being requested. A zero value preserves the library's eager
	// behavior for fixtures and callers that explicitly want full convergence.
	// Go serving sets this to keep large historical stores from competing with
	// normal request/collector work at startup.
	BackgroundBackfillCadence      time.Duration
	BackgroundBackfillInitialDelay time.Duration
	Price                          PriceResolver
	Now                            func() time.Time
	StateFunc                      func(LifecycleCounters)
}

type LifecycleCounters struct {
	Runs, Publications, Repairs, Errors, Prunes, Vacuums           uint64
	BackgroundBackfillDeferrals                                    uint64
	SelectedCells, MaterializedBuckets, FactRowsRead, DecodedBytes uint64
	SelectionDuration, MaterializationDuration                     time.Duration
	LastGeneration                                                 int64
	LastRunSucceeded                                               bool
	LastRunError                                                   string
	ByResolution                                                   map[int]LifecycleResolutionCounters
}

// LifecycleResolutionCounters attributes bounded materializer work to the
// resolution that caused it. Aggregate counters alone cannot explain whether
// a CPU spike came from the one-second ring or a coarse cost ring.
type LifecycleResolutionCounters struct {
	SelectionRuns, MaterializationRuns                             uint64
	SelectedCells, MaterializedBuckets, FactRowsRead, DecodedBytes uint64
	SelectionDuration, MaterializationDuration                     time.Duration
}

type lifecycleResolutionCounters struct {
	selectionRuns, materializationRuns                             atomic.Uint64
	selectedCells, materializedBuckets, factRowsRead, decodedBytes atomic.Uint64
	selectionDuration, materializationDuration                     atomic.Uint64
}

func (c *lifecycleResolutionCounters) snapshot() LifecycleResolutionCounters {
	return LifecycleResolutionCounters{
		SelectionRuns: c.selectionRuns.Load(), MaterializationRuns: c.materializationRuns.Load(),
		SelectedCells: c.selectedCells.Load(), MaterializedBuckets: c.materializedBuckets.Load(),
		FactRowsRead: c.factRowsRead.Load(), DecodedBytes: c.decodedBytes.Load(),
		SelectionDuration:       time.Duration(c.selectionDuration.Load()),
		MaterializationDuration: time.Duration(c.materializationDuration.Load()),
	}
}

type lifecycleCounters struct {
	runs, publications, repairs, errors, prunes, vacuums           atomic.Uint64
	backgroundBackfillDeferrals                                    atomic.Uint64
	selectedCells, materializedBuckets, factRowsRead, decodedBytes atomic.Uint64
	selectionDuration, materializationDuration                     atomic.Uint64
	byResolution                                                   [4]lifecycleResolutionCounters
	lastGeneration                                                 atomic.Int64
	lastRunSucceeded                                               atomic.Bool
	lastRunErrorMu                                                 sync.RWMutex
	lastRunError                                                   string
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
		SelectionDuration: time.Duration(c.selectionDuration.Load()), MaterializationDuration: time.Duration(c.materializationDuration.Load()),
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
	if counters := c.resolution(resolution); counters != nil {
		counters.selectionRuns.Add(1)
		counters.selectionDuration.Add(uint64(nonNegativeDuration(elapsed).Nanoseconds()))
	}
}

func (c *lifecycleCounters) recordMaterialization(resolution int, elapsed time.Duration, rows, bytes, buckets int) {
	if counters := c.resolution(resolution); counters != nil {
		counters.materializationRuns.Add(1)
		counters.materializationDuration.Add(uint64(nonNegativeDuration(elapsed).Nanoseconds()))
		counters.factRowsRead.Add(uint64(nonNegativeInt(rows)))
		counters.decodedBytes.Add(uint64(nonNegativeInt(bytes)))
		counters.materializedBuckets.Add(uint64(nonNegativeInt(buckets)))
	}
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
		config.MaxFactRows = MaxFactReadLimit
	}
	if config.MaxDecodedBytes <= 0 {
		config.MaxDecodedBytes = 8 * 1024 * 1024
	}
	if config.MaxRunTime <= 0 {
		config.MaxRunTime = 500 * time.Millisecond
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
	return &Lifecycle{writer: writer, config: config, materializer: Materializer{Price: config.Price}, trigger: make(chan struct{}, 1), pruneEvery: config.PruneCadence, lastPruneAt: lastPruneAt, vacuumEvery: config.VacuumCadence, vacuumRetry: config.VacuumRetry, vacuumMaxDefer: config.VacuumMaxDefer, lastVacuumAt: lastVacuumAt, busy: config.Busy, demands: make(map[ProductKey]time.Time), lastMaterializedAt: make(map[[2]int64]time.Time), backgroundBackfillNotBefore: backgroundBackfillNotBefore}
}
func (l *Lifecycle) Counters() LifecycleCounters { return l.counters.snapshot() }
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
	l.demandRevision.Add(1)
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
}

func (l *Lifecycle) runAndCount(ctx context.Context) {
	err := l.RunOnce(ctx)
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
	cells, err := l.selectCellsFromSnapshotWithReconciliation(ctx, now, generation, snapshotDatabase, sourceGenerationBeforeMaintenance != generation)
	l.counters.selectionDuration.Add(uint64(time.Since(selectionStarted).Nanoseconds()))
	if err != nil {
		return err
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
	materializeCtx, cancel := context.WithTimeout(ctx, l.config.MaxRunTime)
	defer cancel()
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
		if len(allBuckets) >= l.config.MaxCells || time.Since(started) >= l.config.MaxRunTime {
			break
		}
		cell := cells[index]
		runEnd := cell.BucketStart + int64(cell.ResolutionSeconds)
		runLimit := index + 1
		for runLimit < len(cells) && runLimit-index < maxMaterializationRunCells && cells[runLimit].ResolutionSeconds == cell.ResolutionSeconds && cells[runLimit].BucketStart == runEnd {
			runEnd += int64(cell.ResolutionSeconds)
			runLimit++
		}
		var buckets []RingBucketWrite
		var bytesRead, rowsRead int
		var err error
		for {
			materializationStarted := time.Now()
			buckets, bytesRead, rowsRead, err = l.materializeResolution(materializeCtx, snapshotDatabase, cell.BucketStart, runEnd, cell.ResolutionSeconds, cacheGeneration, publishedAt)
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
		if _, err := l.writer.PublishRingBuckets(ctx, allBuckets, publicationGeneration, publishedAt, true); err != nil {
			return err
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
	if cadence <= 0 {
		return true
	}
	if now.Before(time.Unix(cell.BucketStart+int64(cell.ResolutionSeconds), 0)) {
		last, ok := l.lastMaterializedAt[[2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}]
		return !ok || now.Sub(last) >= cadence
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

var errLifecycleBudget = errors.New("stats lifecycle materialization budget exhausted")

const maxStaleRowsPerResolution = 4

// Keep one materialization fold below the fact/byte budgets even when a
// demanded product has a long contiguous cold range. A whole 24-hour coarse
// range can otherwise exceed MaxFactRows and remain pending forever.
const maxMaterializationRunCells = 4

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
	pendingKeys := make(map[[2]int64]bool, len(pending))
	for _, cell := range pending {
		pendingKeys[[2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}] = true
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
		selectionStarted := time.Now()
		window, err := l.writer.ReadRingWindowMetadata(ctx, int(horizon), resolution, end)
		l.counters.recordSelection(resolution, time.Since(selectionStarted))
		if err != nil {
			return nil, err
		}
		rows := make(map[int64]RingBucketRow, len(window.Rows))
		for _, row := range window.Rows {
			rows[row.BucketStart] = row
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
			// An incomplete historical bucket is not itself evidence of new work:
			// no-data/partial coverage can remain incomplete forever. A changed
			// source generation or a durable pending cell is the retry signal;
			// otherwise selecting the same 300s gaps every cadence rescans them
			// without changing the result.
			if generationFallback && row.SourceGeneration < generation {
				stale = append(stale, bucket)
			}
		}
		sort.Slice(stale, func(i, j int) bool { return stale[i] > stale[j] })
		windows[resolution] = ringWindow{start: start, end: end, missing: window.MissingBucketStarts, stale: stale, rows: rows}
	}
	seen := make(map[[2]int64]bool)
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
	if !backgroundBackfillDue && len(demands) == 0 && !generationMismatch {
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
			spans, readErr := sourceSnapshot.ReadUnavailableSpans(ctx, float64(bounds.start), float64(bounds.end), l.config.MaxFactRows)
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
		row, present := bounds.rows[carrier]
		if !present || row.SourceGeneration < generation || !row.Complete {
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
		row, present := bounds.rows[carrier]
		if !present || row.SourceGeneration < generation || !row.Complete {
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
		priority := make(map[int]int, len(demands)+len(pending))
		for _, demand := range demands {
			if current, ok := priority[demand.ResolutionSeconds]; !ok || current > 0 {
				priority[demand.ResolutionSeconds] = 0
			}
		}
		for _, cell := range pending {
			if _, demanded := priority[cell.ResolutionSeconds]; demanded {
				continue
			}
			if current, ok := priority[cell.ResolutionSeconds]; !ok || current > 1 {
				priority[cell.ResolutionSeconds] = 1
			}
		}
		sort.SliceStable(selected, func(i, j int) bool {
			left, right := selected[i], selected[j]
			leftPriority, leftKnown := priority[left.ResolutionSeconds]
			rightPriority, rightKnown := priority[right.ResolutionSeconds]
			if !leftKnown {
				leftPriority = 2
			}
			if !rightKnown {
				rightPriority = 2
			}
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
	accumulator, err := newMaterializerAccumulator(MaterializeRequest{Start: float64(start), End: float64(end), Resolution: float64(resolution)}, l.materializer.Price)
	if err != nil {
		return nil, 0, 0, err
	}
	rowsRead, decodedBytes := 0, 0
	account := func(count, bytes int) error {
		if count < 0 || bytes < 0 || rowsRead > l.config.MaxFactRows-count || decodedBytes > l.config.MaxDecodedBytes-bytes {
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
