package stats

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"time"
)

const (
	writerProtocol = 25
	writerBuild    = 8
)

// AppendResult describes the durable changes made by one transaction.
type AppendResult struct {
	Generation            int64
	ObservationsAccepted  int
	ObservationsDuplicate int
	CoverageChanged       int
	CoverageDuplicate     int
	UnavailableAccepted   int
	UnavailableDuplicate  int
	UsageAccepted         int
	UsageDuplicate        int
	TombstonesAccepted    int
	TombstonesDuplicate   int
}

// Writer owns the one write-capable SQLite connection for YO!stats.
type Writer struct {
	db        *sql.DB
	lease     *writerLease
	lifecycle *Lifecycle
}

// OpenWriter opens or creates the checked-in schema-8 core database.
func OpenWriter(ctx context.Context, path string) (*Writer, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := validateWriterFence(path); err != nil {
		return nil, err
	}
	lease, err := acquireWriterLease(path)
	if err != nil {
		return nil, err
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
	if err = validateWriterSchema(ctx, db); err != nil {
		return closeOnError(err)
	}
	if err := publishWriterFence(path); err != nil {
		return closeOnError(fmt.Errorf("publish YO!stats writer fence: %w", err))
	}
	return &Writer{db: db, lease: lease}, nil
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
	validated, err := batch.Validate()
	if err != nil {
		return result, err
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	tx, err := w.db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin stats append: %w", err)
	}
	rollback := func(err error) (AppendResult, error) {
		_ = tx.Rollback()
		return AppendResult{}, err
	}
	var generation int64
	if err := tx.QueryRowContext(ctx, "SELECT source_generation FROM schema_meta WHERE singleton = 1").Scan(&generation); err != nil {
		return rollback(fmt.Errorf("read stats generation: %w", err))
	}
	result.Generation = generation
	for _, item := range batch.Observations {
		payload, _ := json.Marshal(item.Payload)
		var oldAt float64
		var oldEpoch, oldPayload string
		var oldGeneration uint64
		err := tx.QueryRowContext(ctx, "SELECT observed_at, epoch_id, owner_generation, payload_json FROM observations WHERE family = ? AND source_id = ? AND event_id = ?", item.Family, item.SourceID, item.EventID).Scan(&oldAt, &oldEpoch, &oldGeneration, &oldPayload)
		if errors.Is(err, sql.ErrNoRows) {
			if _, err = tx.ExecContext(ctx, "INSERT INTO observations(event_id,family,source_id,observed_at,epoch_id,owner_generation,payload_json) VALUES(?,?,?,?,?,?,?)", item.EventID, item.Family, item.SourceID, item.ObservedAt, item.EpochID, item.OwnerGeneration, payload); err != nil {
				return rollback(fmt.Errorf("insert observation: %w", err))
			}
			result.ObservationsAccepted++
		} else if err != nil {
			return rollback(err)
		} else if oldAt != item.ObservedAt || oldEpoch != item.EpochID || oldGeneration != item.OwnerGeneration || oldPayload != string(payload) {
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
		} else if err != nil {
			return rollback(err)
		} else if started != item.StartedAt {
			return rollback(errors.New("coverage epoch identity conflicts with stored data"))
		} else if started == item.StartedAt && ((item.EndedAt == nil && !ended.Valid) || (item.EndedAt != nil && ended.Valid && ended.Float64 == *item.EndedAt)) && cadence == item.NativeCadenceSeconds && oldGen == item.OwnerGeneration {
			result.CoverageDuplicate++
		} else if cadence != item.NativeCadenceSeconds {
			return rollback(errors.New("coverage epoch start and cadence are immutable"))
		} else if started != item.StartedAt || (item.EndedAt == nil && ended.Valid) || (item.EndedAt != nil && (!ended.Valid || ended.Float64 != *item.EndedAt)) || oldGen != item.OwnerGeneration {
			if _, err = tx.ExecContext(ctx, "UPDATE coverage_epochs SET ended_at=?, owner_generation=? WHERE family=? AND source_id=? AND epoch_id=?", item.EndedAt, item.OwnerGeneration, item.Family, item.SourceID, item.EpochID); err != nil {
				return rollback(fmt.Errorf("update coverage epoch: %w", err))
			}
			result.CoverageChanged++
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
		var oldAt float64
		var oldPayload string
		err := tx.QueryRowContext(ctx, "SELECT observed_at,payload_json FROM usage_atoms WHERE event_id=? AND direction=? AND modality=? AND cache_role=? AND unit=?", item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit).Scan(&oldAt, &oldPayload)
		if errors.Is(err, sql.ErrNoRows) {
			if _, err = tx.ExecContext(ctx, "INSERT INTO usage_atoms VALUES(?,?,?,?,?,?,?)", item.EventID, item.Direction, item.Modality, item.CacheRole, item.Unit, item.ObservedAt, payload); err != nil {
				return rollback(err)
			}
			result.UsageAccepted++
		} else if err != nil {
			return rollback(err)
		} else if oldAt == item.ObservedAt && oldPayload == string(payload) {
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
	}
	changed := result.ObservationsAccepted + result.CoverageChanged + result.UnavailableAccepted + result.UsageAccepted + result.TombstonesAccepted
	if changed > 0 {
		result.Generation++
		if _, err = tx.ExecContext(ctx, "UPDATE schema_meta SET source_generation=? WHERE singleton=1", result.Generation); err != nil {
			return rollback(err)
		}
		timestamps := make([]float64, 0, len(batch.Observations)+len(batch.UsageAtoms)+len(batch.UsageTombstones))
		for _, item := range batch.Observations {
			timestamps = append(timestamps, item.ObservedAt)
		}
		for _, item := range batch.UsageAtoms {
			timestamps = append(timestamps, item.ObservedAt)
		}
		for _, item := range batch.UsageTombstones {
			timestamps = append(timestamps, item.ObservedAt)
		}
		if err = recordFactInvalidations(ctx, tx, result.Generation, timestamps, float64(time.Now().Unix())); err != nil {
			return rollback(err)
		}
	}
	if err = tx.Commit(); err != nil {
		return AppendResult{}, fmt.Errorf("commit stats append: %w", err)
	}
	_ = validated
	if w.lifecycle != nil && changed > 0 {
		w.lifecycle.NotifyAppend()
	}
	return result, nil
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
		"CREATE TABLE schema_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), minimum_writer_protocol INTEGER NOT NULL, minimum_writer_build INTEGER NOT NULL, source_generation INTEGER NOT NULL, last_vacuumed_at REAL NOT NULL DEFAULT 0)",
		"INSERT INTO schema_meta VALUES(1,25,8,0,0)",
		"CREATE TABLE observations(event_id TEXT NOT NULL,family TEXT NOT NULL,source_id TEXT NOT NULL,observed_at REAL NOT NULL,epoch_id TEXT NOT NULL,owner_generation INTEGER NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(family,source_id,event_id)) WITHOUT ROWID",
		"CREATE TABLE coverage_epochs(family TEXT NOT NULL,source_id TEXT NOT NULL,epoch_id TEXT NOT NULL,started_at REAL NOT NULL,ended_at REAL,native_cadence_seconds REAL NOT NULL,owner_generation INTEGER NOT NULL,PRIMARY KEY(family,source_id,epoch_id)) WITHOUT ROWID",
		"CREATE TABLE unavailable_spans(family TEXT NOT NULL,source_id TEXT NOT NULL,epoch_id TEXT NOT NULL,started_at REAL NOT NULL,ended_at REAL NOT NULL,native_cadence_seconds REAL NOT NULL,reason TEXT NOT NULL,owner_generation INTEGER NOT NULL,PRIMARY KEY(family,source_id,epoch_id,started_at,ended_at)) WITHOUT ROWID",
		"CREATE TABLE usage_atoms(event_id TEXT NOT NULL,direction TEXT NOT NULL,modality TEXT NOT NULL,cache_role TEXT NOT NULL,unit TEXT NOT NULL,observed_at REAL NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(event_id,direction,modality,cache_role,unit)) WITHOUT ROWID",
		"CREATE TABLE migration_reconciliation(migration_id TEXT PRIMARY KEY,completed_at REAL NOT NULL,source_digest TEXT NOT NULL,details_json TEXT NOT NULL) WITHOUT ROWID",
		"CREATE INDEX observations_time ON observations(observed_at)", "CREATE INDEX coverage_epochs_end ON coverage_epochs(ended_at)", "CREATE INDEX unavailable_spans_end ON unavailable_spans(ended_at)", "CREATE INDEX usage_atoms_time ON usage_atoms(observed_at)",
		"CREATE TABLE aggregate_publication(singleton INTEGER PRIMARY KEY CHECK(singleton=1), ring_generation INTEGER NOT NULL CHECK(ring_generation>=0), source_generation INTEGER NOT NULL CHECK(source_generation>=0), published_at REAL NOT NULL CHECK(published_at>=0))",
		"CREATE TABLE aggregate_rings(resolution_seconds INTEGER PRIMARY KEY, slot_count INTEGER NOT NULL CHECK(slot_count>0), newest_bucket_start INTEGER, CHECK(resolution_seconds>0), CHECK(newest_bucket_start IS NULL OR newest_bucket_start>=0), CHECK(newest_bucket_start IS NULL OR newest_bucket_start % resolution_seconds=0)) WITHOUT ROWID",
		"CREATE TABLE aggregate_ring_slots(resolution_seconds INTEGER NOT NULL, slot_index INTEGER NOT NULL CHECK(slot_index>=0), bucket_start INTEGER, bucket_json BLOB, complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)), source_generation INTEGER NOT NULL DEFAULT 0 CHECK(source_generation>=0), ring_generation INTEGER NOT NULL DEFAULT 0 CHECK(ring_generation>=0), published_at REAL NOT NULL DEFAULT 0 CHECK(published_at>=0), payload_version INTEGER NOT NULL DEFAULT 0 CHECK(payload_version>=0), PRIMARY KEY(resolution_seconds,slot_index), FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), CHECK(bucket_start IS NULL OR bucket_start>=0), CHECK(bucket_start IS NULL OR bucket_start % resolution_seconds=0), CHECK((bucket_start IS NULL AND bucket_json IS NULL AND complete=0 AND source_generation=0 AND ring_generation=0 AND published_at=0 AND payload_version=0) OR (bucket_start IS NOT NULL AND bucket_json IS NOT NULL AND payload_version>0))) WITHOUT ROWID",
		"CREATE TABLE ring_replay_cursor(resolution_seconds INTEGER PRIMARY KEY, folded_through_observed_at REAL NOT NULL DEFAULT 0 CHECK(folded_through_observed_at>=0), folded_source_generation INTEGER NOT NULL DEFAULT 0 CHECK(folded_source_generation>=0), updated_at REAL NOT NULL DEFAULT 0 CHECK(updated_at>=0), FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds)) WITHOUT ROWID",
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
	return tx.Commit()
}

func validateWriterSchema(ctx context.Context, db *sql.DB) error {
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
	if err := validateRingShape(ctx, db); err != nil {
		return err
	}
	return nil
}
