package stats

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/mattn/go-sqlite3"
)

const (
	writerProtocol = 25
	writerBuild    = 8
	pruneStateFile = "stats-prune-v9.json"
	vacuumBaseFile = "stats-vacuum-v9.json"
	// Seven insert columns stay below SQLite's portable 999-bind limit.
	maxUsageStatementRows = 128
)

// AppendResult describes the durable changes made by one transaction.
type AppendResult struct {
	Generation            int64
	PublicationTargets    []InvalidationCell
	ObservationsAccepted  int
	ObservationsDuplicate int
	// AcceptedObservationIDs preserves the request order-independent durable
	// identity needed by the authenticated browser-observation receipt.  The
	// raw browser event id is intentionally not stored here; the HTTP owner
	// maps this privacy-bound id back to the request's redacted receipt id.
	AcceptedObservationIDs []string
	CoverageChanged        int
	CoverageDuplicate      int
	UnavailableAccepted    int
	UnavailableDuplicate   int
	UsageAccepted          int
	UsageDuplicate         int
	TombstonesAccepted     int
	TombstonesDuplicate    int
}

// PruneResult describes the retention changes committed by one maintenance
// transaction. Coverage intervals that cross the cutoff are clipped so the
// first retained sample still has an owning epoch, matching the Python store.
type PruneResult struct {
	Observations, CoverageDeleted, CoverageClipped int
	UsageAtoms                                     int
	UnavailableDeleted, UnavailableClipped         int
	RetiredInvalidations                           int
	Generation                                     int64
}

func (r PruneResult) Changed() int {
	return r.Observations + r.CoverageDeleted + r.CoverageClipped + r.UsageAtoms +
		r.UnavailableDeleted + r.UnavailableClipped
}

// Writer owns the one write-capable SQLite connection for YO!stats.
type Writer struct {
	db                      *sql.DB
	lease                   *writerLease
	lifecycle               *Lifecycle
	validateCurrentFamilies bool
	mutationMu              sync.Mutex
	sourceGeneration        atomic.Int64
	dataVersion             int64
	dataVersionReady        bool
	// Startup reconciliation may deliberately leave the in-memory publication
	// floor below schema_meta. Remember the fully scanned generation so the
	// first append can avoid repeating that scan when SQLite has not changed.
	startupReconciledGeneration   int64
	startupReconciledDataVersion  int64
	startupReconciledTotalChanges int64
	startupReconciledDataReady    bool
	corruptRecoveryName           string
	pendingMu                     sync.RWMutex
	pending                       map[[2]int64]int64
	pendingReady                  bool
}

// MigrationStatus is the small, read-only projection needed by the HTTP
// capabilities response. The reconciliation row is authoritative for an
// already-active database; the full migration detail remains private to the
// migration owner.
type MigrationStatus struct {
	Present    bool
	State      string
	Result     string
	IssueKinds []string
}

// MigrationStatus reads the completed current-database reconciliation without
// opening a second connection or exposing migration internals to callers.
func (w *Writer) MigrationStatus(ctx context.Context) (MigrationStatus, error) {
	if w == nil || w.db == nil {
		return MigrationStatus{}, errors.New("nil YO!stats writer")
	}
	var detailsJSON string
	err := w.db.QueryRowContext(ctx,
		"SELECT details_json FROM migration_reconciliation WHERE migration_id = ?",
		"stats-v5-current-only-v1").Scan(&detailsJSON)
	if errors.Is(err, sql.ErrNoRows) {
		return MigrationStatus{}, nil
	}
	if err != nil {
		return MigrationStatus{}, fmt.Errorf("read YO!stats migration reconciliation: %w", err)
	}
	var details struct {
		Issues []struct {
			Kind string `json:"kind"`
		} `json:"issues"`
	}
	if err := json.Unmarshal([]byte(detailsJSON), &details); err != nil {
		return MigrationStatus{}, fmt.Errorf("decode YO!stats migration reconciliation: %w", err)
	}
	seen := make(map[string]struct{}, len(details.Issues))
	issueKinds := make([]string, 0, len(details.Issues))
	for _, issue := range details.Issues {
		if issue.Kind == "" {
			continue
		}
		if _, exists := seen[issue.Kind]; exists {
			continue
		}
		seen[issue.Kind] = struct{}{}
		issueKinds = append(issueKinds, issue.Kind)
	}
	sort.Strings(issueKinds)
	return MigrationStatus{Present: true, State: "ready", Result: "existing", IssueKinds: issueKinds}, nil
}

// EnableCurrentFamilyValidation makes this writer enforce the current typed
// observation payload contracts on new writes. It is enabled by the Go server
// after opening its writer. Tests and migration tooling intentionally leave it
// disabled so sparse/legacy fixtures remain readable and appendable for
// compatibility checks; persisted rows are never revalidated on read here.
func (w *Writer) EnableCurrentFamilyValidation() {
	if w == nil {
		return
	}
	w.mutationMu.Lock()
	w.validateCurrentFamilies = true
	w.mutationMu.Unlock()
}

// OpenWriter opens or creates the checked-in schema-9 core database.
func OpenWriter(ctx context.Context, path string) (*Writer, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	canonical, err := canonicalWriterPath(path)
	if err != nil {
		return nil, err
	}
	path = canonical
	// Take the cross-language lease before reading the fence. The fence and
	// read-only schema preflight are one ownership boundary; validating before
	// the lease allowed another writer to publish or replace the fence between
	// the check and acquisition.
	lease, err := acquireWriterLease(path)
	if err != nil {
		return nil, err
	}
	if err := validateWriterFence(path); err != nil {
		_ = lease.close()
		return nil, err
	}
	corruptRecoveryName := ""
	// Validate an existing database through an immutable read-only connection
	// before opening it writable. In particular, do not enable WAL, create
	// schema objects, or repair triggers for a database that will subsequently
	// be rejected as incompatible. This is the same destructive-open boundary
	// used by the Python owner and keeps DB/WAL/SHM untouched on failure.
	if info, statErr := os.Stat(path); statErr == nil {
		sidecars, sidecarErr := sqliteDatabaseSidecarsPresent(path)
		if sidecarErr != nil {
			_ = lease.close()
			return nil, sidecarErr
		}
		// A truncated main file can be empty while its WAL still contains the
		// last committed state. Opening it writable would let SQLite discard the
		// WAL, so preserve the whole artifact set before creating a replacement.
		if info.Size() == 0 && sidecars {
			quarantinePath, quarantineErr := quarantineCorruptDatabase(path)
			if quarantineErr != nil {
				_ = lease.close()
				return nil, fmt.Errorf("quarantine corrupt YO!stats database: %w", quarantineErr)
			}
			corruptRecoveryName = filepath.Base(quarantinePath)
		} else if info.Size() > 0 {
			preflightErr := error(nil)
			// A damaged header can make SQLite report a secondary read-only error
			// while it examines a stale journal. Check the immutable header before
			// opening the read-only connection so all physical-corruption shapes use
			// the same recoverable path.
			headerCorrupt := sqliteDatabaseHeaderCorrupt(path)
			if !headerCorrupt {
				preflightErr = validateWriterDatabaseReadOnly(ctx, path)
			}
			if headerCorrupt || (preflightErr != nil && sqlitePreflightCorrupt(preflightErr)) {
				quarantinePath, quarantineErr := quarantineCorruptDatabase(path)
				if quarantineErr != nil {
					_ = lease.close()
					return nil, fmt.Errorf("quarantine corrupt YO!stats database: %w", quarantineErr)
				}
				corruptRecoveryName = filepath.Base(quarantinePath)
			} else if preflightErr != nil {
				_ = lease.close()
				return nil, preflightErr
			}
		}
	} else if statErr != nil && os.IsNotExist(statErr) {
		// Orphaned sidecars beside a missing main file are also unsafe to let
		// SQLite reinterpret as a new database; retain them for forensics first.
		sidecars, sidecarErr := sqliteDatabaseSidecarsPresent(path)
		if sidecarErr != nil {
			_ = lease.close()
			return nil, sidecarErr
		}
		if sidecars {
			quarantinePath, quarantineErr := quarantineCorruptDatabase(path)
			if quarantineErr != nil {
				_ = lease.close()
				return nil, fmt.Errorf("quarantine orphaned YO!stats database sidecars: %w", quarantineErr)
			}
			corruptRecoveryName = filepath.Base(quarantinePath)
		}
	} else if statErr != nil {
		_ = lease.close()
		return nil, fmt.Errorf("stat YO!stats database before open: %w", statErr)
	}
	db, err := sql.Open("sqlite3", writerURI(path))
	if err != nil {
		_ = lease.close()
		return nil, fmt.Errorf("open YO!stats writer: %w", err)
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	closeOnError := func(err error) (*Writer, error) {
		_ = db.Close()
		_ = lease.close()
		return nil, err
	}
	for _, pragma := range []string{"PRAGMA busy_timeout = 5000", "PRAGMA foreign_keys = ON", "PRAGMA query_only = OFF", "PRAGMA journal_mode = WAL", "PRAGMA synchronous = NORMAL", "PRAGMA wal_autocheckpoint = 1000", fmt.Sprintf("PRAGMA journal_size_limit = %d", WALAllocationCeilingBytes)} {
		if _, err = db.ExecContext(ctx, pragma); err != nil {
			return closeOnError(fmt.Errorf("configure YO!stats writer: %w", err))
		}
	}
	if err = initializeSchema(ctx, db); err != nil {
		return closeOnError(err)
	}
	if err = initializeMissingRingSchema(ctx, db); err != nil {
		return closeOnError(fmt.Errorf("initialize YO!stats ring schema: %w", err))
	}
	if err = ensureRingTriggers(ctx, db); err != nil {
		return closeOnError(fmt.Errorf("ensure YO!stats ring triggers: %w", err))
	}
	if err = validateWriterSchema(ctx, db, true); err != nil {
		return closeOnError(err)
	}
	var fenceVersion int64
	if err := db.QueryRowContext(ctx, "PRAGMA user_version").Scan(&fenceVersion); err != nil {
		return closeOnError(err)
	}
	fenceProtocol := writerProtocol
	if fenceVersion == checkpointSchemaVersion {
		fenceProtocol = checkpointWriterProtocol
	}
	if err := publishWriterFenceVersion(path, fenceVersion, fenceProtocol); err != nil {
		return closeOnError(fmt.Errorf("publish YO!stats writer fence: %w", err))
	}
	writer := &Writer{db: db, lease: lease, corruptRecoveryName: corruptRecoveryName, pending: make(map[[2]int64]int64)}
	if err := writer.initializeDataVersion(ctx); err != nil {
		return closeOnError(fmt.Errorf("read YO!stats data version: %w", err))
	}
	var sourceGeneration, publishedGeneration int64
	if err := db.QueryRowContext(ctx, "SELECT source_generation FROM schema_meta WHERE singleton=1").Scan(&sourceGeneration); err != nil {
		return closeOnError(fmt.Errorf("read YO!stats source generation: %w", err))
	}
	if err := db.QueryRowContext(ctx, "SELECT source_generation FROM aggregate_publication WHERE singleton=1").Scan(&publishedGeneration); err != nil {
		return closeOnError(fmt.Errorf("read YO!stats published generation: %w", err))
	}
	var pendingInvalidations int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM ring_invalidations WHERE applied_at IS NULL").Scan(&pendingInvalidations); err != nil {
		return closeOnError(fmt.Errorf("read YO!stats pending invalidations: %w", err))
	}
	startupReconciledGeneration := int64(0)
	if publishedGeneration < sourceGeneration {
		// A prior owner advanced source_generation without recording per-cell
		// invalidations. Recreate those bounded invalidations while the writer
		// still owns startup, so a later tail append cannot erase the only
		// volatile signal that historical ring rows need repair.
		tx, txErr := db.BeginTx(ctx, nil)
		if txErr != nil {
			return closeOnError(fmt.Errorf("begin YO!stats startup reconciliation: %w", txErr))
		}
		now := float64(time.Now().UnixNano()) / 1e9
		if txErr = recordExternalGenerationInvalidations(ctx, tx, sourceGeneration, now); txErr != nil {
			_ = tx.Rollback()
			return closeOnError(fmt.Errorf("reconcile YO!stats startup generation: %w", txErr))
		}
		if txErr = tx.Commit(); txErr != nil {
			return closeOnError(fmt.Errorf("commit YO!stats startup reconciliation: %w", txErr))
		}
		startupReconciledGeneration = sourceGeneration
	} else if publishedGeneration == sourceGeneration && pendingInvalidations == 0 {
		// A prior owner may have advanced both watermarks while omitting the
		// cell-level invalidation. Scan the bounded populated ring once at
		// startup so a later tail append cannot hide that stale row behind its
		// precise pending cell.
		tx, txErr := db.BeginTx(ctx, nil)
		if txErr != nil {
			return closeOnError(fmt.Errorf("begin YO!stats stale-slot reconciliation: %w", txErr))
		}
		now := float64(time.Now().UnixNano()) / 1e9
		if txErr = recordStaleRingInvalidations(ctx, tx, sourceGeneration, now); txErr != nil {
			_ = tx.Rollback()
			return closeOnError(fmt.Errorf("reconcile YO!stats stale ring slots: %w", txErr))
		}
		if txErr = tx.Commit(); txErr != nil {
			return closeOnError(fmt.Errorf("commit YO!stats stale-slot reconciliation: %w", txErr))
		}
	}
	// The publication is the last generation whose ring cells are known to be
	// materialized. Start the in-memory reconciliation floor there, rather than
	// blindly trusting schema_meta after a legacy/direct writer changed facts
	// without recording ring invalidations while this process was down. The first
	// append still sees the mismatch, but skips the duplicate scan when the
	// connection's SQLite data_version proves no outside writer changed facts.
	if publishedGeneration < sourceGeneration {
		sourceGeneration = publishedGeneration
	}
	writer.sourceGeneration.Store(sourceGeneration)
	if startupReconciledGeneration > 0 && writer.dataVersionReady {
		var totalChanges int64
		if err := db.QueryRowContext(ctx, "SELECT total_changes()").Scan(&totalChanges); err != nil {
			return closeOnError(fmt.Errorf("read YO!stats connection changes: %w", err))
		}
		writer.startupReconciledGeneration = startupReconciledGeneration
		writer.startupReconciledDataVersion = writer.dataVersion
		writer.startupReconciledTotalChanges = totalChanges
		writer.startupReconciledDataReady = true
		// Do not refresh this baseline from a later post-commit observation:
		// an external same-generation commit before a writer-owned maintenance
		// mutation must remain visible and force the next append to reconcile.
	}
	if err := writer.refreshPendingCache(ctx); err != nil {
		return closeOnError(fmt.Errorf("read YO!stats pending invalidations: %w", err))
	}
	if err := truncateStatsWAL(ctx, db); err != nil {
		return closeOnError(fmt.Errorf("truncate inherited YO!stats WAL on writer startup: %w", err))
	}
	return writer, nil
}

func truncateStatsWAL(ctx context.Context, db *sql.DB) error {
	var busy, logFrames, checkpointedFrames int64
	if err := db.QueryRowContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)").Scan(&busy, &logFrames, &checkpointedFrames); err != nil {
		return fmt.Errorf("checkpoint WAL: %w", err)
	}
	if busy != 0 {
		return fmt.Errorf("truncate WAL remained busy (%d of %d frames checkpointed)", checkpointedFrames, logFrames)
	}
	return nil
}

// initializeDataVersion records SQLite's connection-local external-commit
// counter. SQLite does not advance this value for writes made through the
// same connection, but it does advance it after a legacy/direct writer commits
// through another connection. That distinction preserves reconciliation when
// an external mutation reuses the current generation and a normal Go append
// later advances the generation before the lifecycle tick runs.
func (w *Writer) initializeDataVersion(ctx context.Context) error {
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	return w.readDataVersionLocked(ctx, false)
}

func (w *Writer) readDataVersionLocked(ctx context.Context, detect bool) error {
	return w.readDataVersionLockedAt(ctx, detect, float64(time.Now().UnixNano())/1e9)
}

func (w *Writer) readDataVersionLockedAt(ctx context.Context, detect bool, now float64) error {
	if w == nil || w.db == nil {
		return errors.New("nil YO!stats writer")
	}
	if math.IsNaN(now) || math.IsInf(now, 0) || now < 0 {
		return errors.New("invalid YO!stats reconciliation time")
	}
	var version int64
	if err := w.db.QueryRowContext(ctx, "PRAGMA data_version").Scan(&version); err != nil {
		return err
	}
	changed := w.dataVersionReady && version != w.dataVersion
	w.dataVersion = version
	w.dataVersionReady = true
	if detect && changed {
		var generation, publishedGeneration int64
		if err := w.db.QueryRowContext(ctx, "SELECT source_generation FROM schema_meta WHERE singleton=1").Scan(&generation); err != nil {
			return err
		}
		if err := w.db.QueryRowContext(ctx, "SELECT source_generation FROM aggregate_publication WHERE singleton=1").Scan(&publishedGeneration); err != nil {
			return err
		}
		if publishedGeneration > generation {
			generation = publishedGeneration
		}
		tx, err := w.db.BeginTx(ctx, nil)
		if err != nil {
			return err
		}
		if err := recordExternalGenerationInvalidations(ctx, tx, generation, now); err != nil {
			_ = tx.Rollback()
			return err
		}
		if err := tx.Commit(); err != nil {
			return err
		}
		if err := w.refreshPendingCache(ctx); err != nil {
			return err
		}
	}
	return nil
}

// ReconcileExternalMutation notices a commit made outside this Writer and
// records bounded invalidations before the lifecycle selects work. The caller
// need not rely on source_generation changing: direct writers sometimes reuse
// the current watermark.
func (w *Writer) ReconcileExternalMutation(ctx context.Context) error {
	return w.ReconcileExternalMutationAt(ctx, float64(time.Now().UnixNano())/1e9)
}

// ReconcileExternalMutationAt is the deterministic-clock variant used by the
// lifecycle owner and synthetic fixtures. The reconciliation timestamp must
// use the same clock as bucket selection; otherwise a fixture timeline (or a
// restored logical clock) can be discarded as older than wall-clock retention
// before its external fact mutation is converted to ring invalidations.
func (w *Writer) ReconcileExternalMutationAt(ctx context.Context, now float64) error {
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	return w.readDataVersionLockedAt(ctx, true, now)
}

// validateWriterDatabaseReadOnly is intentionally separate from OpenReadOnly:
// the writer needs the stricter ring-shape checks but must not publish a
// reader-side connection or perform any compatibility upgrade. SQLite's
// immutable read-only URI prevents journal/WAL recovery from mutating the
// source while this preflight runs.
func validateWriterDatabaseReadOnly(ctx context.Context, path string) error {
	db, err := sql.Open("sqlite3", writerReadOnlyURI(path))
	if err != nil {
		return fmt.Errorf("open YO!stats database for read-only preflight: %w", err)
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	defer db.Close()
	var integrity string
	if err := db.QueryRowContext(ctx, "PRAGMA integrity_check").Scan(&integrity); err != nil {
		return fmt.Errorf("run YO!stats integrity preflight: %w", err)
	}
	if integrity != "ok" {
		return &sqliteCorruptionError{detail: integrity}
	}
	// Missing ring-protection triggers are repaired immediately after this
	// read-only shape check, while the writer lease is held. The read-only
	// boundary must not reject that repairable same-schema state before the
	// write-capable connection can reach ensureRingTriggers.
	if err := validateWriterSchema(ctx, db, false); err != nil {
		return fmt.Errorf("YO!stats database failed read-only preflight: %w", err)
	}
	return nil
}

const sqliteDatabaseHeader = "SQLite format 3\x00"

type sqliteCorruptionError struct{ detail string }

func (e *sqliteCorruptionError) Error() string {
	return fmt.Sprintf("YO!stats SQLite integrity check failed: %s", e.detail)
}

// sqliteDatabaseHeaderCorrupt identifies a non-empty file that cannot be a
// SQLite database without treating an I/O or permission failure as proof of
// corruption. The latter must remain a fail-closed startup error.
func sqliteDatabaseHeaderCorrupt(path string) bool {
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer file.Close()
	header := make([]byte, len(sqliteDatabaseHeader))
	if _, err := io.ReadFull(file, header); err != nil {
		return errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF)
	}
	return string(header) != sqliteDatabaseHeader
}

func sqliteDatabaseSidecarsPresent(path string) (bool, error) {
	for _, suffix := range []string{"-wal", "-shm", "-journal"} {
		_, err := os.Lstat(path + suffix)
		if err == nil {
			return true, nil
		}
		if !os.IsNotExist(err) {
			return false, fmt.Errorf("inspect YO!stats database sidecar %q: %w", filepath.Base(path+suffix), err)
		}
	}
	return false, nil
}

func sqlitePreflightCorrupt(err error) bool {
	var integrityErr *sqliteCorruptionError
	if errors.As(err, &integrityErr) {
		return true
	}
	var sqliteErr sqlite3.Error
	if !errors.As(err, &sqliteErr) {
		return false
	}
	return sqliteErr.Code == sqlite3.ErrCorrupt || sqliteErr.Code == sqlite3.ErrNotADB
}

type quarantineMove struct{ source, target string }

func quarantineCorruptDatabase(path string) (string, error) {
	return quarantineCorruptDatabaseWithSync(path, syncQuarantineDirectory)
}

func quarantineCorruptDatabaseWithSync(path string, syncDirectory func(string) error) (string, error) {
	if syncDirectory == nil {
		syncDirectory = syncQuarantineDirectory
	}
	target, err := freeCorruptDatabaseTarget(path)
	if err != nil {
		return "", err
	}
	suffixes := []string{"", "-wal", "-shm", "-journal"}
	moves := make([]quarantineMove, 0, len(suffixes))
	for _, suffix := range suffixes {
		source := path + suffix
		info, statErr := os.Lstat(source)
		if os.IsNotExist(statErr) {
			continue
		}
		if statErr != nil {
			return "", fmt.Errorf("inspect corrupt database artifact %q: %w", source, statErr)
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			return "", fmt.Errorf("corrupt database artifact is not a regular file: %s", filepath.Base(source))
		}
		moves = append(moves, quarantineMove{source: source, target: target + suffix})
	}
	if len(moves) == 0 {
		return "", errors.New("corrupt YO!stats database disappeared before quarantine")
	}
	moved := make([]quarantineMove, 0, len(moves))
	for _, item := range moves {
		if err := os.Rename(item.source, item.target); err != nil {
			return "", errors.Join(fmt.Errorf("move corrupt database artifact %q: %w", item.source, err), rollbackQuarantineMoves(moved))
		}
		moved = append(moved, item)
	}
	if err := syncDirectory(filepath.Dir(path)); err != nil {
		return "", errors.Join(fmt.Errorf("sync database directory after quarantine: %w", err), rollbackQuarantineMoves(moved))
	}
	return target, nil
}

func rollbackQuarantineMoves(moved []quarantineMove) error {
	var rollbackErr error
	for i := len(moved) - 1; i >= 0; i-- {
		if err := os.Rename(moved[i].target, moved[i].source); err != nil {
			rollbackErr = errors.Join(rollbackErr, err)
		}
	}
	return rollbackErr
}

func syncQuarantineDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("open database directory after quarantine: %w", err)
	}
	syncErr := directory.Sync()
	closeErr := directory.Close()
	if syncErr != nil {
		return fmt.Errorf("sync database directory after quarantine: %w", syncErr)
	}
	if closeErr != nil {
		return fmt.Errorf("close database directory after quarantine: %w", closeErr)
	}
	return nil
}

func freeCorruptDatabaseTarget(path string) (string, error) {
	base := fmt.Sprintf("%s.corrupt-%d-%d", path, time.Now().UnixNano(), os.Getpid())
	for attempt := 0; attempt < 100; attempt++ {
		candidate := base
		if attempt > 0 {
			candidate = fmt.Sprintf("%s.%d", base, attempt)
		}
		occupied := false
		for _, suffix := range []string{"", "-wal", "-shm", "-journal"} {
			if _, err := os.Lstat(candidate + suffix); err == nil {
				occupied = true
				break
			} else if !os.IsNotExist(err) {
				return "", fmt.Errorf("inspect quarantine target %q: %w", candidate+suffix, err)
			}
		}
		if !occupied {
			return candidate, nil
		}
	}
	return "", fmt.Errorf("no free corrupt database quarantine name beside %s", filepath.Base(path))
}

// SourceGeneration returns the latest generation observed by the writer
// owner. Request paths use this lock-free value to reject an immutable
// product that has already been superseded, without opening SQLite.
func (w *Writer) SourceGeneration() int64 {
	if w == nil {
		return 0
	}
	return w.sourceGeneration.Load()
}

// CorruptRecoveryName returns the basename of the forensic artifact created
// during this writer's startup, or an empty string when no recovery occurred.
// The name is intentionally basename-only so diagnostics do not expose the
// configured state-directory path.
func (w *Writer) CorruptRecoveryName() string {
	if w == nil {
		return ""
	}
	return w.corruptRecoveryName
}

// ProductFresh reports whether the published product may be served for the
// requested window. It consults only the writer-owned in-memory invalidation
// index; HTTP request paths must not open SQLite. A cache that has not been
// loaded successfully fails closed.
func (w *Writer) ProductFresh(resolution int, start, end int64) bool {
	if w == nil || resolution <= 0 || end <= start {
		return false
	}
	w.pendingMu.RLock()
	defer w.pendingMu.RUnlock()
	if !w.pendingReady {
		return false
	}
	for key := range w.pending {
		if key[0] == int64(resolution) && key[1] >= start && key[1] < end {
			return false
		}
	}
	return true
}

// PendingCount returns the number of currently actionable ring contradictions
// from the writer-owned in-memory index. Request paths use this projection
// instead of opening the database.
func (w *Writer) PendingCount() int {
	if w == nil {
		return 0
	}
	w.pendingMu.RLock()
	defer w.pendingMu.RUnlock()
	if !w.pendingReady {
		return -1
	}
	return len(w.pending)
}

func (w *Writer) setPendingCacheUnavailable() {
	w.pendingMu.Lock()
	w.pendingReady = false
	w.pendingMu.Unlock()
}

func (w *Writer) refreshPendingCache(ctx context.Context) error {
	if w == nil || w.db == nil {
		return errors.New("nil YO!stats writer")
	}
	rows, err := w.db.QueryContext(ctx, "SELECT resolution_seconds,bucket_start,source_generation FROM ring_invalidations WHERE applied_at IS NULL")
	if err != nil {
		w.setPendingCacheUnavailable()
		return err
	}
	next := make(map[[2]int64]int64)
	for rows.Next() {
		var resolution, bucket, generation int64
		if err := rows.Scan(&resolution, &bucket, &generation); err != nil {
			_ = rows.Close()
			w.setPendingCacheUnavailable()
			return err
		}
		key := [2]int64{resolution, bucket}
		if generation > next[key] {
			next[key] = generation
		}
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		w.setPendingCacheUnavailable()
		return err
	}
	if err := rows.Close(); err != nil {
		w.setPendingCacheUnavailable()
		return err
	}
	w.pendingMu.Lock()
	w.pending = next
	w.pendingReady = true
	w.pendingMu.Unlock()
	return nil
}

func writerURI(path string) string {
	abs, _ := filepath.Abs(path)
	return "file:" + filepath.ToSlash(abs) + "?_busy_timeout=5000"
}

func (w *Writer) Close() error {
	if w == nil || w.db == nil {
		return nil
	}
	db := w.db
	lease := w.lease
	w.db = nil
	w.lease = nil
	err := db.Close()
	leaseErr := lease.close()
	if err != nil {
		return err
	}
	return leaseErr
}

// statsDatabaseIdentity returns the current file incarnation for the database
// owned by this writer. It deliberately uses only device/inode identity so
// ordinary database writes do not invalidate external collector cursors.
func (w *Writer) statsDatabaseIdentity() (string, error) {
	if w == nil || w.lease == nil || w.lease.path == "" {
		return "", errors.New("YO!stats database identity is unavailable")
	}
	info, err := os.Stat(w.lease.path)
	if err != nil {
		return "", fmt.Errorf("stat YO!stats database identity: %w", err)
	}
	device, inode := fileIdentity(info)
	return fmt.Sprintf("%d:%d", device, inode), nil
}

// AppendBatch applies all facts and tombstones atomically. Tombstones are
// intentionally not persisted as rows: their proof is the matching atom's
// deletion, and replay of an already-applied tombstone is a duplicate.
func (w *Writer) AppendBatch(ctx context.Context, batch AppendBatch) (AppendResult, error) {
	return w.AppendWithCheckpoints(ctx, batch, nil)
}

// AppendWithCheckpoints advances collector progress only with its durable facts.
// Checkpoints do not invalidate graphs or advance their source generation.
func (w *Writer) AppendWithCheckpoints(ctx context.Context, batch AppendBatch, checkpoints []Checkpoint) (AppendResult, error) {
	return w.AppendWithCheckpointsAndRetire(ctx, batch, checkpoints, nil)
}

// AppendWithCheckpointsAndRetire commits facts, checkpoint updates, and safe
// checkpoint retirement atomically without changing graph generation for
// checkpoint-only work.
func (w *Writer) AppendWithCheckpointsAndRetire(ctx context.Context, batch AppendBatch, checkpoints []Checkpoint, retirement *CheckpointRetirement) (AppendResult, error) {
	return w.appendReplacingUsage(ctx, batch, checkpoints, retirement, nil)
}

func (w *Writer) appendReplacingUsage(ctx context.Context, batch AppendBatch, checkpoints []Checkpoint, retirement *CheckpointRetirement, replacement []UsageAtom) (AppendResult, error) {
	var result AppendResult
	if w == nil || w.db == nil {
		return result, errors.New("nil YO!stats writer")
	}
	// The generation read below is compared with the value committed in the
	// transaction to detect direct/legacy writers. Serialize all generation-
	// mutating transactions so a concurrent append from this same Writer cannot
	// be mistaken for an external owner and trigger a full reconciliation scan.
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	validated, err := batch.Validate()
	if err != nil {
		return result, err
	}
	if w.validateCurrentFamilies {
		if err := validateCurrentFamilyBatch(batch); err != nil {
			return result, err
		}
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	ownedChanges, err := w.startupOwnedChanges(ctx)
	if err != nil {
		return result, err
	}
	defer w.excludeOwnedStartupChanges(ownedChanges)
	knownGeneration := w.sourceGeneration.Load()
	tx, err := w.db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin stats append: %w", err)
	}
	defer tx.Rollback()
	rollback := func(err error) (AppendResult, error) {
		_ = tx.Rollback()
		return AppendResult{}, err
	}
	var generation, publishedGeneration int64
	if err := tx.QueryRowContext(ctx, "SELECT source_generation FROM schema_meta WHERE singleton = 1").Scan(&generation); err != nil {
		return rollback(fmt.Errorf("read stats generation: %w", err))
	}
	// A legacy/imported writer can leave schema_meta behind the last durable
	// ring publication. Never allocate a new fact generation below what the
	// published ring already represents; lifecycle stale-row repair relies on
	// source generations remaining monotonic across restarts and cutovers.
	if err := tx.QueryRowContext(ctx, "SELECT source_generation FROM aggregate_publication WHERE singleton = 1").Scan(&publishedGeneration); err != nil {
		return rollback(fmt.Errorf("read published stats generation: %w", err))
	}
	connectionDataVersionBeforeAppend := int64(0)
	connectionChangesBeforeAppend := int64(0)
	if w.startupReconciledDataReady {
		if err := tx.QueryRowContext(ctx, "PRAGMA data_version").Scan(&connectionDataVersionBeforeAppend); err != nil {
			return rollback(fmt.Errorf("read stats data version: %w", err))
		}
		if err := tx.QueryRowContext(ctx, "SELECT total_changes()").Scan(&connectionChangesBeforeAppend); err != nil {
			return rollback(fmt.Errorf("read stats connection changes: %w", err))
		}
	}
	if publishedGeneration > generation {
		generation = publishedGeneration
	}
	startupExternalMutation := false
	if w.startupReconciledDataReady {
		startupExternalMutation = w.startupReconciledGeneration != generation ||
			connectionDataVersionBeforeAppend != w.startupReconciledDataVersion ||
			connectionChangesBeforeAppend != w.startupReconciledTotalChanges
	}
	result.Generation = generation
	if err := writeCheckpoints(ctx, tx, checkpoints); err != nil {
		return rollback(err)
	}
	if err := writeCheckpointRetirement(ctx, tx, retirement); err != nil {
		return rollback(err)
	}
	acceptedTimestamps := make([]float64, 0, len(batch.Observations)+len(batch.UsageAtoms)+len(batch.UsageTombstones))
	changedIntervals := make([]ringInvalidationInterval, 0, len(batch.CoverageEpochs)+len(batch.UnavailableSpans))
	// Replacement and tombstone reads share one transaction-owned lookup.
	var usageLookup *sql.Stmt
	if len(replacement)+len(batch.UsageTombstones) > 0 {
		usageLookup, err = tx.PrepareContext(ctx, "SELECT observed_at,payload_json FROM usage_atoms WHERE event_id=? AND direction=? AND modality=? AND cache_role=? AND unit=?")
		if err != nil {
			return rollback(err)
		}
		defer usageLookup.Close()
	}
	// Only facts captured before the refill are retired. Concurrent collection
	// can add new facts without having them erased by historical publication.
	replacementConflicts := map[string]struct{}{}
	for _, item := range replacement {
		var currentAt float64
		var currentPayload string
		identity := item.IdempotencyKey()
		if err := usageLookup.QueryRowContext(ctx, item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit).Scan(&currentAt, &currentPayload); err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				replacementConflicts[identity] = struct{}{}
				continue
			}
			return rollback(err)
		}
		capturedPayload, marshalErr := json.Marshal(item.Payload)
		if marshalErr != nil {
			return rollback(marshalErr)
		}
		if currentAt != item.ObservedAt || currentPayload != string(capturedPayload) {
			replacementConflicts[identity] = struct{}{}
			continue
		}
		removed, removeErr := execRowsAffected(ctx, tx, "DELETE FROM usage_atoms WHERE event_id=? AND direction=? AND modality=? AND cache_role=? AND unit=? AND observed_at=? AND payload_json=?", item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit, item.ObservedAt, currentPayload)
		if removeErr != nil {
			return rollback(removeErr)
		}
		if removed > 0 {
			result.TombstonesAccepted += removed
			acceptedTimestamps = append(acceptedTimestamps, item.ObservedAt)
		}
	}

	for _, item := range batch.Observations {
		payload, _ := json.Marshal(item.Payload)
		payloadJSON := string(payload)
		var oldAt float64
		var oldEpoch, oldPayload string
		var oldGeneration uint64
		err := tx.QueryRowContext(ctx, "SELECT observed_at, epoch_id, owner_generation, payload_json FROM observations WHERE family = ? AND source_id = ? AND event_id = ?", item.Family, item.SourceID, item.EventID).Scan(&oldAt, &oldEpoch, &oldGeneration, &oldPayload)
		if errors.Is(err, sql.ErrNoRows) {
			if _, err = tx.ExecContext(ctx, "INSERT INTO observations(event_id,family,source_id,observed_at,epoch_id,owner_generation,payload_json) VALUES(?,?,?,?,?,?,?)", item.EventID, item.Family, item.SourceID, item.ObservedAt, item.EpochID, item.OwnerGeneration, payloadJSON); err != nil {
				return rollback(fmt.Errorf("insert observation: %w", err))
			}
			result.ObservationsAccepted++
			result.AcceptedObservationIDs = append(result.AcceptedObservationIDs, item.EventID)
			acceptedTimestamps = append(acceptedTimestamps, item.ObservedAt)
		} else if err != nil {
			return rollback(err)
		} else if oldAt != item.ObservedAt || oldEpoch != item.EpochID || oldGeneration != item.OwnerGeneration || oldPayload != payloadJSON {
			return rollback(fmt.Errorf("observation event identity conflicts with stored data"))
		} else {
			result.ObservationsDuplicate++
		}
	}
	for _, item := range batch.CoverageEpochs {
		var started, cadence float64
		var ended sql.NullFloat64
		var oldGen uint64
		var unavailableConflict int
		if err := tx.QueryRowContext(ctx, "SELECT 1 FROM unavailable_spans WHERE family=? AND source_id=? AND ended_at > ? AND (? IS NULL OR started_at < ?) LIMIT 1", item.Family, item.SourceID, item.StartedAt, item.EndedAt, item.EndedAt).Scan(&unavailableConflict); err == nil {
			return rollback(errors.New("coverage epoch overlaps an unavailable span"))
		} else if !errors.Is(err, sql.ErrNoRows) {
			return rollback(fmt.Errorf("check coverage interval: %w", err))
		}
		err := tx.QueryRowContext(ctx, "SELECT started_at, ended_at, native_cadence_seconds, owner_generation FROM coverage_epochs WHERE family=? AND source_id=? AND epoch_id=?", item.Family, item.SourceID, item.EpochID).Scan(&started, &ended, &cadence, &oldGen)
		if errors.Is(err, sql.ErrNoRows) {
			if _, err = tx.ExecContext(ctx, "INSERT INTO coverage_epochs VALUES(?,?,?,?,?,?,?)", item.Family, item.SourceID, item.EpochID, item.StartedAt, item.EndedAt, item.NativeCadenceSeconds, item.OwnerGeneration); err != nil {
				return rollback(fmt.Errorf("insert coverage epoch: %w", err))
			}
			result.CoverageChanged++
			changedIntervals = append(changedIntervals, ringInvalidationInterval{start: item.StartedAt, end: item.EndedAt})
		} else if err != nil {
			return rollback(err)
		} else if started != item.StartedAt {
			return rollback(errors.New("coverage epoch identity conflicts with stored data"))
		} else if ended.Valid && (item.EndedAt == nil || *item.EndedAt < ended.Float64) {
			return rollback(errors.New("coverage epoch end cannot move backward"))
		} else if started == item.StartedAt && ((item.EndedAt == nil && !ended.Valid) || (item.EndedAt != nil && ended.Valid && ended.Float64 == *item.EndedAt)) && cadence == item.NativeCadenceSeconds && oldGen == item.OwnerGeneration {
			result.CoverageDuplicate++
		} else if cadence != item.NativeCadenceSeconds {
			return rollback(errors.New("coverage epoch start and cadence are immutable"))
		} else if started != item.StartedAt || (item.EndedAt == nil && ended.Valid) || (item.EndedAt != nil && (!ended.Valid || ended.Float64 != *item.EndedAt)) || oldGen != item.OwnerGeneration {
			if _, err = tx.ExecContext(ctx, "UPDATE coverage_epochs SET ended_at=?, owner_generation=? WHERE family=? AND source_id=? AND epoch_id=?", item.EndedAt, item.OwnerGeneration, item.Family, item.SourceID, item.EpochID); err != nil {
				return rollback(fmt.Errorf("update coverage epoch: %w", err))
			}
			result.CoverageChanged++
			changedIntervals = append(changedIntervals, coverageChangeInterval(started, ended, oldGen, item))
		}
	}
	for _, item := range batch.UnavailableSpans {
		var cadence float64
		var oldReason string
		var oldGen uint64
		err := tx.QueryRowContext(ctx, "SELECT native_cadence_seconds,reason,owner_generation FROM unavailable_spans WHERE family=? AND source_id=? AND epoch_id=? AND started_at=? AND ended_at=?", item.Family, item.SourceID, item.EpochID, item.StartedAt, item.EndedAt).Scan(&cadence, &oldReason, &oldGen)
		if errors.Is(err, sql.ErrNoRows) {
			var coverageConflict int
			if err = tx.QueryRowContext(ctx, "SELECT 1 FROM coverage_epochs WHERE family=? AND source_id=? AND (epoch_id<>? OR started_at < ?) AND (ended_at IS NULL OR ended_at > ?) AND started_at < ? LIMIT 1", item.Family, item.SourceID, item.EpochID, item.StartedAt, item.StartedAt, item.EndedAt).Scan(&coverageConflict); err == nil {
				return rollback(errors.New("unavailable span overlaps a coverage epoch"))
			} else if !errors.Is(err, sql.ErrNoRows) {
				return rollback(fmt.Errorf("check unavailable coverage interval: %w", err))
			}
			var unavailableConflict int
			if err = tx.QueryRowContext(ctx, "SELECT 1 FROM unavailable_spans WHERE family=? AND source_id=? AND ended_at > ? AND started_at < ? LIMIT 1", item.Family, item.SourceID, item.StartedAt, item.EndedAt).Scan(&unavailableConflict); err == nil {
				return rollback(errors.New("unavailable spans overlap"))
			} else if !errors.Is(err, sql.ErrNoRows) {
				return rollback(fmt.Errorf("check unavailable interval: %w", err))
			}
			if _, err = tx.ExecContext(ctx, "INSERT INTO unavailable_spans VALUES(?,?,?,?,?,?,?,?)", item.Family, item.SourceID, item.EpochID, item.StartedAt, item.EndedAt, item.NativeCadenceSeconds, item.Reason, item.OwnerGeneration); err != nil {
				return rollback(err)
			}
			result.UnavailableAccepted++
			changedIntervals = append(changedIntervals, ringInvalidationInterval{start: item.StartedAt, end: &item.EndedAt})
		} else if err != nil {
			return rollback(err)
		} else if cadence == item.NativeCadenceSeconds && oldReason == item.Reason && oldGen == item.OwnerGeneration {
			result.UnavailableDuplicate++
		} else {
			return rollback(errors.New("unavailable span identity conflicts with stored data"))
		}
	}
	usageTimestamps, err := appendUsageAtoms(ctx, tx, batch.UsageAtoms, replacementConflicts, &result)
	if err != nil {
		return rollback(err)
	}
	acceptedTimestamps = append(acceptedTimestamps, usageTimestamps...)
	for _, item := range batch.UsageTombstones {
		var observed float64
		var payloadJSON string
		err = usageLookup.QueryRowContext(ctx, item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit).Scan(&observed, &payloadJSON)
		if errors.Is(err, sql.ErrNoRows) {
			result.TombstonesDuplicate++
			continue
		}
		if err != nil {
			return rollback(err)
		}
		var payload map[string]any
		if err = json.Unmarshal([]byte(payloadJSON), &payload); err != nil {
			return rollback(err)
		}
		if observed != item.ObservedAt || payload["quantity"] != item.Quantity || payload["provider"] != item.Provider || payload["model"] != item.Model || payload["thread_id"] != item.ThreadID || payload["execution_source"] != "codex" {
			return rollback(errors.New("usage tombstone does not match stored atom"))
		}
		if _, err = tx.ExecContext(ctx, "DELETE FROM usage_atoms WHERE event_id=? AND direction=? AND modality=? AND cache_role=? AND unit=?", item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit); err != nil {
			return rollback(err)
		}
		result.TombstonesAccepted++
		acceptedTimestamps = append(acceptedTimestamps, item.ObservedAt)
	}
	changed := result.ObservationsAccepted + result.CoverageChanged + result.UnavailableAccepted + result.UsageAccepted + result.TombstonesAccepted
	commitAt := float64(time.Now().UnixNano()) / 1e9
	externalGenerationAdvanced := generation > knownGeneration
	if changed > 0 {
		result.Generation++
		if _, err = tx.ExecContext(ctx, "UPDATE schema_meta SET source_generation=? WHERE singleton=1", result.Generation); err != nil {
			return rollback(err)
		}
		if err = recordRingInvalidations(ctx, tx, result.Generation, commitAt, "fact_mutation", acceptedTimestamps, changedIntervals); err != nil {
			return rollback(err)
		}
	}
	if externalGenerationAdvanced || startupExternalMutation {
		reconciled := false
		if !startupExternalMutation && w.startupReconciledDataReady && w.startupReconciledGeneration == generation {
			var dataVersion int64
			if err = tx.QueryRowContext(ctx, "PRAGMA data_version").Scan(&dataVersion); err != nil {
				return rollback(fmt.Errorf("read stats data version: %w", err))
			}
			reconciled = dataVersion == w.startupReconciledDataVersion && connectionChangesBeforeAppend == w.startupReconciledTotalChanges
		}
		if !reconciled {
			if err = recordExternalGenerationInvalidations(ctx, tx, result.Generation, commitAt); err != nil {
				return rollback(fmt.Errorf("record external-generation invalidations: %w", err))
			}
		}
	}
	if replacement != nil {
		rows, queryErr := tx.QueryContext(ctx, "SELECT resolution_seconds,bucket_start,source_generation FROM ring_invalidations WHERE source_generation=? AND applied_at IS NULL", result.Generation)
		if queryErr != nil {
			return rollback(queryErr)
		}
		for rows.Next() {
			var cell InvalidationCell
			if queryErr = rows.Scan(&cell.ResolutionSeconds, &cell.BucketStart, &cell.SourceGeneration); queryErr != nil {
				_ = rows.Close()
				return rollback(queryErr)
			}
			result.PublicationTargets = append(result.PublicationTargets, cell)
		}
		queryErr = rows.Err()
		_ = rows.Close()
		if queryErr != nil {
			return rollback(queryErr)
		}
	}
	if err = tx.Commit(); err != nil {
		return AppendResult{}, fmt.Errorf("commit stats append: %w", err)
	}
	w.sourceGeneration.Store(result.Generation)
	w.startupReconciledDataReady = false
	// Keep the request-side contradiction index synchronized after durability.
	// A failed refresh leaves the index fail-closed until the lifecycle owner
	// reloads it from SQLite.
	_ = w.refreshPendingCache(ctx)
	_ = validated
	if w.lifecycle != nil && changed > 0 {
		w.lifecycle.NotifyAppend()
	}
	return result, nil
}

// appendUsageAtoms batches identity reads and fresh inserts in the caller's
// transaction. Duplicate and model-repair decisions keep their existing policy.
func appendUsageAtoms(ctx context.Context, tx *sql.Tx, atoms []UsageAtom, skipped map[string]struct{}, result *AppendResult) ([]float64, error) {
	accepted := make([]float64, 0, len(atoms))
	for start := 0; start < len(atoms); start += maxUsageStatementRows {
		end := min(start+maxUsageStatementRows, len(atoms))
		chunk := atoms[start:end]
		lookupArgs := make([]any, 0, len(chunk)*6)
		for index, item := range chunk {
			lookupArgs = append(lookupArgs, index, item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit)
		}
		candidates := strings.TrimRight(strings.Repeat("(?,?,?,?,?,?),", len(chunk)), ",")
		rows, err := tx.QueryContext(ctx, "WITH candidates(row_index,event_id,direction,modality,cache_role,unit) AS (VALUES "+candidates+") SELECT candidates.row_index,usage.observed_at,usage.payload_json FROM candidates JOIN usage_atoms AS usage USING(event_id,direction,modality,cache_role,unit)", lookupArgs...)
		if err != nil {
			return nil, err
		}
		existing := make([]struct {
			found    bool
			observed float64
			payload  string
		}, len(chunk))
		for rows.Next() {
			var index int
			var observed float64
			var payload string
			if err := rows.Scan(&index, &observed, &payload); err != nil {
				rows.Close()
				return nil, err
			}
			existing[index].found, existing[index].observed, existing[index].payload = true, observed, payload
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, err
		}
		insertArgs := make([]any, 0, len(chunk)*7)
		for index, item := range chunk {
			if _, changedSinceSnapshot := skipped[item.IdempotencyKey()]; changedSinceSnapshot {
				continue
			}
			payload, err := json.Marshal(item.Payload)
			if err != nil {
				return nil, err
			}
			payloadJSON := string(payload)
			old := existing[index]
			if !old.found {
				insertArgs = append(insertArgs, item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit, item.ObservedAt, payloadJSON)
				result.UsageAccepted++
				accepted = append(accepted, item.ObservedAt)
				continue
			}
			// Validated, identical replay needs no JSON decoding. Metadata differences
			// still use the shared compatibility and repair policy.
			updatedPayload, repaired, compatible := "", false, old.observed == item.ObservedAt && old.payload == payloadJSON
			if !compatible {
				updatedPayload, repaired, compatible = usageReplayPayloadChange(item.EventID, old.observed, old.payload, item.ObservedAt, payloadJSON)
			}
			if !compatible {
				return nil, errors.New("usage atom identity conflicts with stored data")
			}
			if repaired {
				if _, err := tx.ExecContext(ctx, "UPDATE usage_atoms SET payload_json=? WHERE event_id=? AND direction=? AND modality=? AND cache_role=? AND unit=?", updatedPayload, item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit); err != nil {
					return nil, fmt.Errorf("repair unknown usage model: %w", err)
				}
				result.UsageAccepted++
				accepted = append(accepted, item.ObservedAt)
			} else {
				result.UsageDuplicate++
			}
		}
		if len(insertArgs) > 0 {
			values := strings.TrimRight(strings.Repeat("(?,?,?,?,?,?,?),", len(insertArgs)/7), ",")
			if _, err := tx.ExecContext(ctx, "INSERT INTO usage_atoms VALUES "+values, insertArgs...); err != nil {
				return nil, err
			}
		}
	}
	return accepted, nil
}

// Prune removes facts older than the retention window in one transaction.
// The generation and ring invalidations are committed with the deletes so a
// reader can never observe a durable generation whose published buckets still
// contain pruned facts.
func (w *Writer) Prune(ctx context.Context, now float64) (PruneResult, error) {
	var result PruneResult
	if w == nil || w.db == nil {
		return result, errors.New("nil YO!stats writer")
	}
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	if math.IsNaN(now) || math.IsInf(now, 0) || now < 0 {
		return result, errors.New("invalid retention time")
	}
	cutoff := now - float64(RetentionSeconds)
	tx, err := w.db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin stats prune: %w", err)
	}
	rollback := func(err error) (PruneResult, error) {
		_ = tx.Rollback()
		return PruneResult{}, err
	}
	var publishedGeneration int64
	if err := tx.QueryRowContext(ctx, "SELECT source_generation FROM schema_meta WHERE singleton=1").Scan(&result.Generation); err != nil {
		return rollback(fmt.Errorf("read stats generation: %w", err))
	}
	if err := tx.QueryRowContext(ctx, "SELECT source_generation FROM aggregate_publication WHERE singleton=1").Scan(&publishedGeneration); err != nil {
		return rollback(fmt.Errorf("read published stats generation: %w", err))
	}
	if publishedGeneration > result.Generation {
		result.Generation = publishedGeneration
	}
	if result.Observations, err = execRowsAffected(ctx, tx, "DELETE FROM observations WHERE observed_at < ?", cutoff); err != nil {
		return rollback(err)
	}
	if result.UsageAtoms, err = execRowsAffected(ctx, tx, "DELETE FROM usage_atoms WHERE observed_at < ?", cutoff); err != nil {
		return rollback(err)
	}
	if result.CoverageDeleted, err = execRowsAffected(ctx, tx, "DELETE FROM coverage_epochs WHERE ended_at IS NOT NULL AND ended_at <= ?", cutoff); err != nil {
		return rollback(err)
	}
	if result.CoverageClipped, err = execRowsAffected(ctx, tx, "UPDATE coverage_epochs SET started_at = ? WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)", cutoff, cutoff, cutoff); err != nil {
		return rollback(err)
	}
	if result.UnavailableDeleted, err = execRowsAffected(ctx, tx, "DELETE FROM unavailable_spans WHERE ended_at <= ?", cutoff); err != nil {
		return rollback(err)
	}
	if result.UnavailableClipped, err = execRowsAffected(ctx, tx, "UPDATE unavailable_spans SET started_at = ? WHERE started_at < ? AND ended_at > ?", cutoff, cutoff, cutoff); err != nil {
		return rollback(err)
	}
	if result.Changed() > 0 {
		result.Generation++
		if _, err = tx.ExecContext(ctx, "UPDATE schema_meta SET source_generation=? WHERE singleton=1", result.Generation); err != nil {
			return rollback(fmt.Errorf("advance stats generation after prune: %w", err))
		}
		// The retained boundary is an exclusive half-open mutation interval. The
		// shared owner intersects it with populated cells and records commit time,
		// so an inside-bucket cutoff dirties the containing cell while an aligned
		// cutoff does not dirty the first retained cell.
		if err = recordRingInvalidations(ctx, tx, result.Generation, now, "retention_prune", nil, []ringInvalidationInterval{{start: 0, end: &cutoff}}); err != nil {
			return rollback(fmt.Errorf("record prune ring invalidations: %w", err))
		}
	}
	// Retired ledger rows are no longer consulted by readers. Delete a bounded
	// batch so a large historical backlog cannot hold the writer lock too long.
	rows, queryErr := tx.QueryContext(ctx, `SELECT resolution_seconds,bucket_start,source_generation
		FROM ring_invalidations WHERE applied_at IS NOT NULL AND applied_at < ?
		ORDER BY applied_at LIMIT ?`, cutoff, RetiredInvalidationLimit)
	if queryErr != nil {
		return rollback(fmt.Errorf("select retired ring invalidations: %w", queryErr))
	}
	var retired [][3]int64
	for rows.Next() {
		var key [3]int64
		if scanErr := rows.Scan(&key[0], &key[1], &key[2]); scanErr != nil {
			_ = rows.Close()
			return rollback(fmt.Errorf("scan retired ring invalidation: %w", scanErr))
		}
		retired = append(retired, key)
	}
	if rowsErr := rows.Err(); rowsErr != nil {
		_ = rows.Close()
		return rollback(fmt.Errorf("read retired ring invalidations: %w", rowsErr))
	}
	_ = rows.Close()
	for _, key := range retired {
		if _, err = tx.ExecContext(ctx, `DELETE FROM ring_invalidations
			WHERE resolution_seconds=? AND bucket_start=? AND source_generation=?`, key[0], key[1], key[2]); err != nil {
			return rollback(fmt.Errorf("delete retired ring invalidation: %w", err))
		}
	}
	result.RetiredInvalidations = len(retired)
	if err = tx.Commit(); err != nil {
		return PruneResult{}, fmt.Errorf("commit stats prune: %w", err)
	}
	w.sourceGeneration.Store(result.Generation)
	_ = w.refreshPendingCache(ctx)
	if err := w.recordPrunedAt(now); err != nil {
		return result, fmt.Errorf("record stats prune completion: %w", err)
	}
	return result, nil
}

// LastPrunedAt returns the completion timestamp persisted by the maintenance
// owner. A missing, malformed, or invalid sidecar deliberately reads as zero:
// retention is then due after a restart rather than silently skipped.
func (w *Writer) LastPrunedAt() float64 {
	if w == nil || w.lease == nil {
		return 0
	}
	data, err := os.ReadFile(filepath.Join(filepath.Dir(w.lease.path), pruneStateFile))
	if err != nil {
		return 0
	}
	var value struct {
		LastPrunedAt float64 `json:"last_pruned_at"`
	}
	if err := json.Unmarshal(data, &value); err != nil || math.IsNaN(value.LastPrunedAt) || math.IsInf(value.LastPrunedAt, 0) || value.LastPrunedAt < 0 {
		return 0
	}
	return value.LastPrunedAt
}

func (w *Writer) recordPrunedAt(timestamp float64) error {
	if w == nil || w.lease == nil {
		return errors.New("nil YO!stats writer")
	}
	return atomicSidecarWrite(filepath.Join(filepath.Dir(w.lease.path), pruneStateFile), map[string]any{"last_pruned_at": timestamp})
}

// LastVacuumedAt reads the schema-owned completion marker. Unlike the prune
// schedule, this marker is part of schema_meta and therefore remains atomic
// with the successful VACUUM transaction.
func (w *Writer) LastVacuumedAt(ctx context.Context) (float64, error) {
	if w == nil || w.db == nil {
		return 0, errors.New("nil YO!stats writer")
	}
	var timestamp float64
	if err := w.db.QueryRowContext(ctx, "SELECT last_vacuumed_at FROM schema_meta WHERE singleton=1").Scan(&timestamp); err != nil {
		return 0, err
	}
	return timestamp, nil
}

// ReclaimableRatio measures free-list and in-page slack. It is queried only by
// the maintenance scheduler when a rewrite is due, never on HTTP paths.
func (w *Writer) ReclaimableRatio(ctx context.Context) (float64, error) {
	if w == nil || w.db == nil {
		return 0, errors.New("nil YO!stats writer")
	}
	var pageSize, pageCount, freelist int64
	if err := w.db.QueryRowContext(ctx, "PRAGMA page_size").Scan(&pageSize); err != nil {
		return 0, err
	}
	if err := w.db.QueryRowContext(ctx, "PRAGMA page_count").Scan(&pageCount); err != nil {
		return 0, err
	}
	if err := w.db.QueryRowContext(ctx, "PRAGMA freelist_count").Scan(&freelist); err != nil {
		return 0, err
	}
	if pageSize <= 0 || pageCount <= 0 {
		return 0, nil
	}
	var unused int64
	if err := w.db.QueryRowContext(ctx, "SELECT COALESCE(SUM(unused),0) FROM dbstat").Scan(&unused); err != nil {
		// Some SQLite builds omit the optional dbstat virtual table. The
		// freelist remains a conservative, deterministic lower bound for
		// reclaimable space, so maintenance can still make progress without
		// disabling the scheduler or turning a healthy store into an error.
		return float64(freelist) / float64(pageCount), nil
	}
	return float64(unused+freelist*pageSize) / float64(pageCount*pageSize), nil
}

func (w *Writer) reclaimableRatioAtLastVacuum() float64 {
	if w == nil || w.lease == nil {
		return 0
	}
	data, err := os.ReadFile(filepath.Join(filepath.Dir(w.lease.path), vacuumBaseFile))
	if err != nil {
		return 0
	}
	var value struct {
		LastVacuumedAt   float64 `json:"last_vacuumed_at"`
		ReclaimableRatio float64 `json:"reclaimable_ratio"`
	}
	if err := json.Unmarshal(data, &value); err != nil || value.ReclaimableRatio < 0 || value.ReclaimableRatio > 1 || math.IsNaN(value.ReclaimableRatio) || math.IsInf(value.ReclaimableRatio, 0) {
		return 0
	}
	marker, err := w.LastVacuumedAt(context.Background())
	if err != nil || marker != value.LastVacuumedAt {
		return 0
	}
	return value.ReclaimableRatio
}

func (w *Writer) recordVacuumBaseline(timestamp, ratio float64) error {
	if w == nil || w.lease == nil {
		return errors.New("nil YO!stats writer")
	}
	return atomicSidecarWrite(filepath.Join(filepath.Dir(w.lease.path), vacuumBaseFile), map[string]any{"last_vacuumed_at": timestamp, "reclaimable_ratio": ratio})
}

func execRowsAffected(ctx context.Context, tx *sql.Tx, query string, args ...any) (int, error) {
	result, err := tx.ExecContext(ctx, query, args...)
	if err != nil {
		return 0, err
	}
	count, err := result.RowsAffected()
	return int(count), err
}

func atomicSidecarWrite(path string, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	temp, err := os.CreateTemp(dir, ".stats-maintenance-*")
	if err != nil {
		return err
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if err := temp.Chmod(0o600); err != nil {
		_ = temp.Close()
		return err
	}
	if _, err := temp.Write(append(data, '\n')); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tempName, path); err != nil {
		return err
	}
	directory, err := os.Open(dir)
	if err != nil {
		return err
	}
	syncErr := directory.Sync()
	closeErr := directory.Close()
	if syncErr != nil {
		return syncErr
	}
	return closeErr
}

// AttachLifecycle installs the background publication owner. It is separate
// from OpenWriter so tests and embedders can choose lifecycle ownership.
func (w *Writer) AttachLifecycle(lifecycle *Lifecycle) { w.lifecycle = lifecycle }

func initializeSchema(ctx context.Context, db *sql.DB) error {
	var count int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='schema_meta'").Scan(&count); err != nil {
		return err
	}
	if count != 0 {
		return nil
	}
	statements := []string{
		"PRAGMA application_id = 0x594F5354", "PRAGMA user_version = 9",
		"CREATE TABLE schema_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), minimum_writer_protocol INTEGER NOT NULL CHECK(minimum_writer_protocol>=0), minimum_writer_build INTEGER NOT NULL CHECK(minimum_writer_build>=0), source_generation INTEGER NOT NULL CHECK(source_generation>=0), last_vacuumed_at REAL NOT NULL DEFAULT 0 CHECK(last_vacuumed_at>=0))",
		"INSERT INTO schema_meta VALUES(1,25,8,0,0)",
		"CREATE TABLE observations(event_id TEXT NOT NULL,family TEXT NOT NULL,source_id TEXT NOT NULL,observed_at REAL NOT NULL,epoch_id TEXT NOT NULL,owner_generation INTEGER NOT NULL CHECK(owner_generation>=0),payload_json TEXT NOT NULL,PRIMARY KEY(family,source_id,event_id)) WITHOUT ROWID",
		"CREATE TABLE coverage_epochs(family TEXT NOT NULL,source_id TEXT NOT NULL,epoch_id TEXT NOT NULL,started_at REAL NOT NULL,ended_at REAL,native_cadence_seconds REAL NOT NULL CHECK(native_cadence_seconds>0),owner_generation INTEGER NOT NULL CHECK(owner_generation>=0),CHECK(ended_at IS NULL OR ended_at>=started_at),PRIMARY KEY(family,source_id,epoch_id)) WITHOUT ROWID",
		"CREATE TABLE unavailable_spans(family TEXT NOT NULL,source_id TEXT NOT NULL,epoch_id TEXT NOT NULL,started_at REAL NOT NULL,ended_at REAL NOT NULL,native_cadence_seconds REAL NOT NULL CHECK(native_cadence_seconds>0),reason TEXT NOT NULL,owner_generation INTEGER NOT NULL CHECK(owner_generation>=0),CHECK(ended_at>started_at),PRIMARY KEY(family,source_id,epoch_id,started_at,ended_at)) WITHOUT ROWID",
		"CREATE TABLE usage_atoms(event_id TEXT NOT NULL,direction TEXT NOT NULL,modality TEXT NOT NULL,cache_role TEXT NOT NULL,unit TEXT NOT NULL,observed_at REAL NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(event_id,direction,modality,cache_role,unit)) WITHOUT ROWID",
		"CREATE TABLE migration_reconciliation(migration_id TEXT PRIMARY KEY,completed_at REAL NOT NULL,source_digest TEXT NOT NULL,details_json TEXT NOT NULL) WITHOUT ROWID",
		"CREATE INDEX observations_time ON observations(observed_at)", "CREATE INDEX coverage_epochs_end ON coverage_epochs(ended_at)", "CREATE INDEX unavailable_spans_end ON unavailable_spans(ended_at)", "CREATE INDEX usage_atoms_time ON usage_atoms(observed_at)",
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	for _, statement := range statements {
		if _, err = tx.ExecContext(ctx, statement); err != nil {
			_ = tx.Rollback()
			return fmt.Errorf("initialize stats schema: %w", err)
		}
	}
	if err := createRingSchema(ctx, tx); err != nil {
		_ = tx.Rollback()
		return fmt.Errorf("initialize YO!stats ring schema: %w", err)
	}
	return tx.Commit()
}

type schemaExecer interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

// initializeMissingRingSchema upgrades a valid legacy core-only store. The
// read-only preflight in OpenWriter has already rejected partial or malformed
// extensions before the writable connection is opened; this check keeps the
// migration itself all-or-nothing if the schema changes unexpectedly.
func initializeMissingRingSchema(ctx context.Context, db *sql.DB) error {
	var count int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('aggregate_publication','aggregate_rings','aggregate_ring_slots','ring_replay_cursor','ring_invalidations')").Scan(&count); err != nil {
		return err
	}
	if count == len(ringColumns) {
		return nil
	}
	if count != 0 {
		return &ValidationError{"ring schema is partially present"}
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	if err := createRingSchema(ctx, tx); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Commit()
}

func createRingSchema(ctx context.Context, execer schemaExecer) error {
	statements := []string{
		"CREATE TABLE aggregate_publication(singleton INTEGER PRIMARY KEY CHECK(singleton=1), ring_generation INTEGER NOT NULL CHECK(ring_generation>=0), source_generation INTEGER NOT NULL CHECK(source_generation>=0), published_at REAL NOT NULL CHECK(published_at>=0))",
		"CREATE TABLE aggregate_rings(resolution_seconds INTEGER PRIMARY KEY, slot_count INTEGER NOT NULL CHECK(slot_count>0), newest_bucket_start INTEGER, CHECK(resolution_seconds>0), CHECK(newest_bucket_start IS NULL OR newest_bucket_start>=0), CHECK(newest_bucket_start IS NULL OR newest_bucket_start % resolution_seconds=0)) WITHOUT ROWID",
		"CREATE TABLE aggregate_ring_slots(resolution_seconds INTEGER NOT NULL, slot_index INTEGER NOT NULL CHECK(slot_index>=0), bucket_start INTEGER, bucket_json TEXT, complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)), source_generation INTEGER NOT NULL DEFAULT 0 CHECK(source_generation>=0), ring_generation INTEGER NOT NULL DEFAULT 0 CHECK(ring_generation>=0), published_at REAL NOT NULL DEFAULT 0 CHECK(published_at>=0), payload_version INTEGER NOT NULL DEFAULT 0 CHECK(payload_version>=0), PRIMARY KEY(resolution_seconds,slot_index), FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), CHECK(bucket_start IS NULL OR bucket_start>=0), CHECK(bucket_start IS NULL OR bucket_start % resolution_seconds=0), CHECK((bucket_start IS NULL AND bucket_json IS NULL AND complete=0 AND source_generation=0 AND ring_generation=0 AND published_at=0 AND payload_version=0) OR (bucket_start IS NOT NULL AND bucket_json IS NOT NULL AND payload_version>0))) WITHOUT ROWID",
		"CREATE TABLE ring_replay_cursor(resolution_seconds INTEGER PRIMARY KEY, folded_through_observed_at REAL NOT NULL DEFAULT 0 CHECK(folded_through_observed_at>=0), folded_source_generation INTEGER NOT NULL DEFAULT 0 CHECK(folded_source_generation>=0), updated_at REAL NOT NULL DEFAULT 0 CHECK(updated_at>=0), CHECK(resolution_seconds>0), FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds)) WITHOUT ROWID",
		"CREATE TABLE ring_invalidations(resolution_seconds INTEGER NOT NULL, bucket_start INTEGER NOT NULL CHECK(bucket_start>=0), source_generation INTEGER NOT NULL CHECK(source_generation>=0), reason TEXT NOT NULL, created_at REAL NOT NULL CHECK(created_at>=0), applied_at REAL, PRIMARY KEY(resolution_seconds,bucket_start,source_generation), FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), CHECK(bucket_start % resolution_seconds=0), CHECK(applied_at IS NULL OR applied_at>=0)) WITHOUT ROWID",
		"CREATE INDEX ring_invalidations_pending ON ring_invalidations(resolution_seconds,bucket_start) WHERE applied_at IS NULL",
	}
	for _, statement := range statements {
		if _, err := execer.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	for resolution, capacity := range ringCapacities {
		if _, err := execer.ExecContext(ctx, "INSERT INTO aggregate_rings(resolution_seconds,slot_count) VALUES(?,?)", resolution, capacity); err != nil {
			return err
		}
		if _, err := execer.ExecContext(ctx, "INSERT INTO ring_replay_cursor(resolution_seconds) VALUES(?)", resolution); err != nil {
			return err
		}
		for slot := 0; slot < capacity; slot++ {
			if _, err := execer.ExecContext(ctx, "INSERT INTO aggregate_ring_slots(resolution_seconds,slot_index) VALUES(?,?)", resolution, slot); err != nil {
				return err
			}
		}
	}
	if _, err := execer.ExecContext(ctx, "INSERT INTO aggregate_publication(singleton,ring_generation,source_generation,published_at) VALUES(1,0,0,0)"); err != nil {
		return err
	}
	for _, table := range []string{"aggregate_publication", "aggregate_rings", "aggregate_ring_slots"} {
		for _, operation := range []string{"INSERT", "DELETE"} {
			trigger := fmt.Sprintf("%s_reject_%s", table, strings.ToLower(operation))
			statement := fmt.Sprintf("CREATE TRIGGER %s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END", trigger, operation, table)
			if _, err := execer.ExecContext(ctx, statement); err != nil {
				return err
			}
		}
	}
	return nil
}

func ensureRingTriggers(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx, "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('aggregate_publication','aggregate_rings','aggregate_ring_slots')")
	if err != nil {
		return err
	}
	seen := make(map[string]bool)
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			_ = rows.Close()
			return err
		}
		seen[name] = true
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	for _, table := range []string{"aggregate_publication", "aggregate_rings", "aggregate_ring_slots"} {
		for _, operation := range []string{"INSERT", "DELETE"} {
			name := fmt.Sprintf("%s_reject_%s", table, strings.ToLower(operation))
			if seen[name] {
				continue
			}
			statement := fmt.Sprintf("CREATE TRIGGER %s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END", name, operation, table)
			if _, err := db.ExecContext(ctx, statement); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateWriterSchema(ctx context.Context, db *sql.DB, requireRingTriggers bool) error {
	var app, version int64
	if err := db.QueryRowContext(ctx, "PRAGMA application_id").Scan(&app); err != nil {
		return err
	}
	if err := db.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return err
	}
	if app != int64(ApplicationID) || (version != SchemaVersion && version != checkpointSchemaVersion) {
		return fmt.Errorf("YO!stats database is incompatible: expected application %#x/schema %d", ApplicationID, SchemaVersion)
	}
	if err := validateCoreShape(ctx, &sqliteReadOnlyDB{db: db}); err != nil {
		return err
	}
	var protocol, build int64
	if err := db.QueryRowContext(ctx, "SELECT minimum_writer_protocol,minimum_writer_build FROM schema_meta WHERE singleton=1").Scan(&protocol, &build); err != nil {
		return err
	}
	if protocol > checkpointWriterProtocol || build > writerBuild {
		return &SchemaTooNewError{Found: SchemaVersion, Supported: SchemaVersion}
	}
	if version == checkpointSchemaVersion {
		rows, err := db.QueryContext(ctx, "SELECT checkpoint_key,kind,source_id,revision,version,byte_offset,file_size,file_mtime_ns,device,inode,metadata,decoded_bytes,state,prefix,parsed_tail,pending_line,rebuild_buffer,rebuild_line FROM collector_checkpoints LIMIT 0")
		if err != nil {
			return fmt.Errorf("invalid checkpoint schema: %w", err)
		}
		if err := rows.Close(); err != nil {
			return err
		}
		if protocol < checkpointWriterProtocol {
			return errors.New("checkpoint schema requires checkpoint writer fence")
		}
	}
	if err := validateRingShape(ctx, db, requireRingTriggers); err != nil {
		return err
	}
	return nil
}
