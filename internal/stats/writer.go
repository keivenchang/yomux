package stats

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const (
	writerProtocol = 25
	writerBuild    = 8
	pruneStateFile = "stats-prune-v9.json"
	vacuumBaseFile = "stats-vacuum-v9.json"
)

// AppendResult describes the durable changes made by one transaction.
type AppendResult struct {
	Generation            int64
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
	pendingMu               sync.RWMutex
	pending                 map[[2]int64]int64
	pendingReady            bool
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
	// Validate an existing database through an immutable read-only connection
	// before opening it writable. In particular, do not enable WAL, create
	// schema objects, or repair triggers for a database that will subsequently
	// be rejected as incompatible. This is the same destructive-open boundary
	// used by the Python owner and keeps DB/WAL/SHM untouched on failure.
	if info, statErr := os.Stat(path); statErr == nil && info.Size() > 0 {
		if preflightErr := validateWriterDatabaseReadOnly(ctx, path); preflightErr != nil {
			_ = lease.close()
			return nil, preflightErr
		}
	} else if statErr != nil && !os.IsNotExist(statErr) {
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
	for _, pragma := range []string{"PRAGMA busy_timeout = 5000", "PRAGMA foreign_keys = ON", "PRAGMA query_only = OFF", "PRAGMA journal_mode = WAL", "PRAGMA synchronous = NORMAL", "PRAGMA wal_autocheckpoint = 1000"} {
		if _, err = db.ExecContext(ctx, pragma); err != nil {
			return closeOnError(fmt.Errorf("configure YO!stats writer: %w", err))
		}
	}
	if err = initializeSchema(ctx, db); err != nil {
		return closeOnError(err)
	}
	if err = ensureRingTriggers(ctx, db); err != nil {
		return closeOnError(fmt.Errorf("ensure YO!stats ring triggers: %w", err))
	}
	if err = validateWriterSchema(ctx, db, true); err != nil {
		return closeOnError(err)
	}
	if err := publishWriterFence(path); err != nil {
		return closeOnError(fmt.Errorf("publish YO!stats writer fence: %w", err))
	}
	writer := &Writer{db: db, lease: lease, pending: make(map[[2]int64]int64)}
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
	// without recording ring invalidations while this process was down. The next
	// append then enters the existing external-generation reconciliation path;
	// lifecycle selection also sees the mismatch before that append arrives.
	if publishedGeneration < sourceGeneration {
		sourceGeneration = publishedGeneration
	}
	writer.sourceGeneration.Store(sourceGeneration)
	if err := writer.refreshPendingCache(ctx); err != nil {
		return closeOnError(fmt.Errorf("read YO!stats pending invalidations: %w", err))
	}
	return writer, nil
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
	if _, err := db.ExecContext(ctx, "PRAGMA query_only = ON"); err != nil {
		return fmt.Errorf("enable YO!stats read-only preflight: %w", err)
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

// SourceGeneration returns the latest generation observed by the writer
// owner. Request paths use this lock-free value to reject an immutable
// product that has already been superseded, without opening SQLite.
func (w *Writer) SourceGeneration() int64 {
	if w == nil {
		return 0
	}
	return w.sourceGeneration.Load()
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

// AppendBatch applies all facts and tombstones atomically. Tombstones are
// intentionally not persisted as rows: their proof is the matching atom's
// deletion, and replay of an already-applied tombstone is a duplicate.
func (w *Writer) AppendBatch(ctx context.Context, batch AppendBatch) (AppendResult, error) {
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
	knownGeneration := w.sourceGeneration.Load()
	tx, err := w.db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin stats append: %w", err)
	}
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
	if publishedGeneration > generation {
		generation = publishedGeneration
	}
	result.Generation = generation
	acceptedTimestamps := make([]float64, 0, len(batch.Observations)+len(batch.UsageAtoms)+len(batch.UsageTombstones))
	changedIntervals := make([]ringInvalidationInterval, 0, len(batch.CoverageEpochs)+len(batch.UnavailableSpans))
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
	for _, item := range batch.UsageAtoms {
		payload, _ := json.Marshal(item.Payload)
		payloadJSON := string(payload)
		var oldAt float64
		var oldPayload string
		err := tx.QueryRowContext(ctx, "SELECT observed_at,payload_json FROM usage_atoms WHERE event_id=? AND direction=? AND modality=? AND cache_role=? AND unit=?", item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit).Scan(&oldAt, &oldPayload)
		if errors.Is(err, sql.ErrNoRows) {
			if _, err = tx.ExecContext(ctx, "INSERT INTO usage_atoms VALUES(?,?,?,?,?,?,?)", item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit, item.ObservedAt, payloadJSON); err != nil {
				return rollback(err)
			}
			result.UsageAccepted++
			acceptedTimestamps = append(acceptedTimestamps, item.ObservedAt)
		} else if err != nil {
			return rollback(err)
		} else if oldAt == item.ObservedAt && oldPayload == payloadJSON {
			result.UsageDuplicate++
		} else {
			return rollback(errors.New("usage atom identity conflicts with stored data"))
		}
	}
	for _, item := range batch.UsageTombstones {
		var observed float64
		var payloadJSON string
		err = tx.QueryRowContext(ctx, "SELECT observed_at,payload_json FROM usage_atoms WHERE event_id=? AND direction=? AND modality=? AND cache_role=? AND unit=?", item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit).Scan(&observed, &payloadJSON)
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
	if externalGenerationAdvanced {
		if err = recordExternalGenerationInvalidations(ctx, tx, result.Generation, commitAt); err != nil {
			return rollback(fmt.Errorf("record external-generation invalidations: %w", err))
		}
	}
	if err = tx.Commit(); err != nil {
		return AppendResult{}, fmt.Errorf("commit stats append: %w", err)
	}
	w.sourceGeneration.Store(result.Generation)
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
		"CREATE TABLE aggregate_publication(singleton INTEGER PRIMARY KEY CHECK(singleton=1), ring_generation INTEGER NOT NULL CHECK(ring_generation>=0), source_generation INTEGER NOT NULL CHECK(source_generation>=0), published_at REAL NOT NULL CHECK(published_at>=0))",
		"CREATE TABLE aggregate_rings(resolution_seconds INTEGER PRIMARY KEY, slot_count INTEGER NOT NULL CHECK(slot_count>0), newest_bucket_start INTEGER, CHECK(resolution_seconds>0), CHECK(newest_bucket_start IS NULL OR newest_bucket_start>=0), CHECK(newest_bucket_start IS NULL OR newest_bucket_start % resolution_seconds=0)) WITHOUT ROWID",
		"CREATE TABLE aggregate_ring_slots(resolution_seconds INTEGER NOT NULL, slot_index INTEGER NOT NULL CHECK(slot_index>=0), bucket_start INTEGER, bucket_json TEXT, complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)), source_generation INTEGER NOT NULL DEFAULT 0 CHECK(source_generation>=0), ring_generation INTEGER NOT NULL DEFAULT 0 CHECK(ring_generation>=0), published_at REAL NOT NULL DEFAULT 0 CHECK(published_at>=0), payload_version INTEGER NOT NULL DEFAULT 0 CHECK(payload_version>=0), PRIMARY KEY(resolution_seconds,slot_index), FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), CHECK(bucket_start IS NULL OR bucket_start>=0), CHECK(bucket_start IS NULL OR bucket_start % resolution_seconds=0), CHECK((bucket_start IS NULL AND bucket_json IS NULL AND complete=0 AND source_generation=0 AND ring_generation=0 AND published_at=0 AND payload_version=0) OR (bucket_start IS NOT NULL AND bucket_json IS NOT NULL AND payload_version>0))) WITHOUT ROWID",
		"CREATE TABLE ring_replay_cursor(resolution_seconds INTEGER PRIMARY KEY, folded_through_observed_at REAL NOT NULL DEFAULT 0 CHECK(folded_through_observed_at>=0), folded_source_generation INTEGER NOT NULL DEFAULT 0 CHECK(folded_source_generation>=0), updated_at REAL NOT NULL DEFAULT 0 CHECK(updated_at>=0), CHECK(resolution_seconds>0), FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds)) WITHOUT ROWID",
		"CREATE TABLE ring_invalidations(resolution_seconds INTEGER NOT NULL, bucket_start INTEGER NOT NULL CHECK(bucket_start>=0), source_generation INTEGER NOT NULL CHECK(source_generation>=0), reason TEXT NOT NULL, created_at REAL NOT NULL CHECK(created_at>=0), applied_at REAL, PRIMARY KEY(resolution_seconds,bucket_start,source_generation), FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), CHECK(bucket_start % resolution_seconds=0), CHECK(applied_at IS NULL OR applied_at>=0)) WITHOUT ROWID",
		"CREATE INDEX ring_invalidations_pending ON ring_invalidations(resolution_seconds,bucket_start) WHERE applied_at IS NULL",
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
	for resolution, capacity := range ringCapacities {
		if _, err = tx.ExecContext(ctx, "INSERT INTO aggregate_rings(resolution_seconds,slot_count) VALUES(?,?)", resolution, capacity); err != nil {
			_ = tx.Rollback()
			return err
		}
		if _, err = tx.ExecContext(ctx, "INSERT INTO ring_replay_cursor(resolution_seconds) VALUES(?)", resolution); err != nil {
			_ = tx.Rollback()
			return err
		}
		for slot := 0; slot < capacity; slot++ {
			if _, err = tx.ExecContext(ctx, "INSERT INTO aggregate_ring_slots(resolution_seconds,slot_index) VALUES(?,?)", resolution, slot); err != nil {
				_ = tx.Rollback()
				return err
			}
		}
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO aggregate_publication(singleton,ring_generation,source_generation,published_at) VALUES(1,0,0,0)"); err != nil {
		_ = tx.Rollback()
		return err
	}
	for _, table := range []string{"aggregate_publication", "aggregate_rings", "aggregate_ring_slots"} {
		for _, operation := range []string{"INSERT", "DELETE"} {
			trigger := fmt.Sprintf("%s_reject_%s", table, strings.ToLower(operation))
			statement := fmt.Sprintf("CREATE TRIGGER %s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END", trigger, operation, table)
			if _, err = tx.ExecContext(ctx, statement); err != nil {
				_ = tx.Rollback()
				return err
			}
		}
	}
	return tx.Commit()
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
	if app != int64(ApplicationID) || version != SchemaVersion {
		return fmt.Errorf("YO!stats database is incompatible: expected application %#x/schema %d", ApplicationID, SchemaVersion)
	}
	var protocol, build int64
	if err := db.QueryRowContext(ctx, "SELECT minimum_writer_protocol,minimum_writer_build FROM schema_meta WHERE singleton=1").Scan(&protocol, &build); err != nil {
		return err
	}
	if protocol > writerProtocol || build > writerBuild {
		return &SchemaTooNewError{Found: SchemaVersion, Supported: SchemaVersion}
	}
	if err := validateRingShape(ctx, db, requireRingTriggers); err != nil {
		return err
	}
	return nil
}
