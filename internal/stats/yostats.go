package stats

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"path/filepath"

	_ "github.com/mattn/go-sqlite3"
)

const (
	ApplicationID uint32 = 0x594F5354
	SchemaVersion int64  = 9
)

var coreColumns = map[string][]string{
	"schema_meta": {
		"singleton", "minimum_writer_protocol", "minimum_writer_build", "source_generation", "last_vacuumed_at",
	},
	"observations": {
		"event_id", "family", "source_id", "observed_at", "epoch_id", "owner_generation", "payload_json",
	},
	"coverage_epochs": {
		"family", "source_id", "epoch_id", "started_at", "ended_at", "native_cadence_seconds", "owner_generation",
	},
	"unavailable_spans": {
		"family", "source_id", "epoch_id", "started_at", "ended_at", "native_cadence_seconds", "reason", "owner_generation",
	},
	"usage_atoms": {
		"event_id", "direction", "modality", "cache_role", "unit", "observed_at", "payload_json",
	},
	"migration_reconciliation": {
		"migration_id", "completed_at", "source_digest", "details_json",
	},
}

var ringColumns = map[string][]string{
	"aggregate_publication": {"singleton", "ring_generation", "source_generation", "published_at"},
	"aggregate_rings":       {"resolution_seconds", "slot_count", "newest_bucket_start"},
	"aggregate_ring_slots":  {"resolution_seconds", "slot_index", "bucket_start", "bucket_json", "complete", "source_generation", "ring_generation", "published_at", "payload_version"},
	"ring_replay_cursor":    {"resolution_seconds", "folded_through_observed_at", "folded_source_generation", "updated_at"},
	"ring_invalidations":    {"resolution_seconds", "bucket_start", "source_generation", "reason", "created_at", "applied_at"},
}

type ValidationError struct{ Reason string }

func (e *ValidationError) Error() string { return "YO!stats database is incompatible: " + e.Reason }

type SchemaTooNewError struct {
	Found     int64
	Supported int64
}

func (e *SchemaTooNewError) Error() string {
	return fmt.Sprintf("YO!stats schema %d is newer than supported schema %d", e.Found, e.Supported)
}

type Header struct {
	ApplicationID         uint32
	SchemaVersion         int64
	MinimumWriterProtocol int64
	MinimumWriterBuild    int64
	SourceGeneration      int64
}

type Database struct {
	Header Header
	close  func() error
	sqlDB  *sql.DB
	reader ReadOnlyDB
}

func (d *Database) Close() error {
	if d.close == nil {
		return nil
	}
	return d.close()
}

// ReadOnlyDB is the narrow database surface needed by compatibility validation.
// Implementations must not expose a write-capable connection to this package.
type ReadOnlyDB interface {
	Scalar(context.Context, string, ...any) (any, error)
	Rows(context.Context, string, ...any) ([][]any, error)
	QueryOnly(context.Context) error
	Close() error
}

type OpenFunc func(string) (ReadOnlyDB, error)

// SQLiteReadOnlyURI returns the SQLite URI used for copied databases. The
// caller's driver remains responsible for opening the URI in read-only mode.
func SQLiteReadOnlyURI(path string) string {
	abs, _ := filepath.Abs(path)
	return "file:" + url.PathEscape(abs) + "?mode=ro&_busy_timeout=5000"
}

// OpenReadOnly opens and validates a copied database. The optional opener keeps
// compatibility validation independent from database/sql while the default
// path exercises the real SQLite driver.
func OpenReadOnly(ctx context.Context, path string, openers ...OpenFunc) (*Database, error) {
	open := openSQLiteReadOnly
	if len(openers) > 0 && openers[0] != nil {
		open = openers[0]
	}
	db, err := open(SQLiteReadOnlyURI(path))
	if err != nil {
		return nil, fmt.Errorf("open YO!stats database read-only: %w", err)
	}
	closeWithError := func(err error) (*Database, error) {
		_ = db.Close()
		return nil, err
	}
	if err := db.QueryOnly(ctx); err != nil {
		return closeWithError(fmt.Errorf("enable SQLite query-only mode: %w", err))
	}
	header, err := readHeader(ctx, db)
	if err != nil {
		return closeWithError(err)
	}
	if err := validateCoreShape(ctx, db); err != nil {
		return closeWithError(err)
	}
	if err := validateOptionalRingShape(ctx, db); err != nil {
		return closeWithError(err)
	}
	result := &Database{Header: header, close: db.Close, reader: db}
	if sqliteDB, ok := db.(*sqliteReadOnlyDB); ok {
		result.sqlDB = sqliteDB.db
	}
	return result, nil
}

// validateOptionalRingShape treats the ring schema as an all-or-nothing
// contract. Older core-only databases remain readable, but a partially
// migrated ring is unsafe because callers could mistake missing slots for
// empty data.
func validateOptionalRingShape(ctx context.Context, db ReadOnlyDB) error {
	seen := 0
	for table := range ringColumns {
		rows, err := db.Rows(ctx, "PRAGMA table_info("+table+")")
		if err != nil {
			return &ValidationError{fmt.Sprintf("cannot inspect ring table %q", table)}
		}
		if len(rows) != 0 {
			seen++
		}
	}
	if seen == 0 {
		return nil
	}
	if seen != len(ringColumns) {
		return &ValidationError{"ring schema is partially present"}
	}
	// The ReadOnlyDB abstraction is intentionally query-only; validate the
	// complete shape through the same PRAGMA/row contract used by core tables.
	for table, expected := range ringColumns {
		rows, err := db.Rows(ctx, "PRAGMA table_info("+table+")")
		if err != nil || len(rows) != len(expected) {
			return &ValidationError{fmt.Sprintf("ring table %q has unexpected columns", table)}
		}
		for i, row := range rows {
			if len(row) < 2 {
				return &ValidationError{fmt.Sprintf("ring table %q has malformed column metadata", table)}
			}
			name, ok := row[1].(string)
			if !ok || name != expected[i] {
				return &ValidationError{fmt.Sprintf("ring table %q is missing column %q", table, expected[i])}
			}
		}
	}
	return nil
}

type sqliteReadOnlyDB struct{ db *sql.DB }

func openSQLiteReadOnly(uri string) (ReadOnlyDB, error) {
	db, err := sql.Open("sqlite3", uri)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	return &sqliteReadOnlyDB{db: db}, nil
}

func (d *sqliteReadOnlyDB) Scalar(ctx context.Context, query string, args ...any) (any, error) {
	var value any
	if err := d.db.QueryRowContext(ctx, query, args...).Scan(&value); err != nil {
		return nil, err
	}
	return value, nil
}

func (d *sqliteReadOnlyDB) Rows(ctx context.Context, query string, args ...any) ([][]any, error) {
	rows, err := d.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	columns, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	result := make([][]any, 0)
	for rows.Next() {
		values := make([]any, len(columns))
		pointers := make([]any, len(values))
		for i := range values {
			pointers[i] = &values[i]
		}
		if err := rows.Scan(pointers...); err != nil {
			return nil, err
		}
		result = append(result, values)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return result, nil
}

func (d *sqliteReadOnlyDB) QueryOnly(ctx context.Context) error {
	_, err := d.db.ExecContext(ctx, "PRAGMA query_only = ON")
	return err
}

func (d *sqliteReadOnlyDB) Close() error { return d.db.Close() }

func readHeader(ctx context.Context, db ReadOnlyDB) (Header, error) {
	applicationValue, err := db.Scalar(ctx, "PRAGMA application_id")
	if err != nil {
		return Header{}, &ValidationError{"cannot read application identity"}
	}
	applicationID, err := integer(applicationValue)
	if err != nil {
		return Header{}, &ValidationError{"invalid application identity"}
	}
	schemaValue, err := db.Scalar(ctx, "PRAGMA user_version")
	if err != nil {
		return Header{}, &ValidationError{"cannot read schema version"}
	}
	schemaVersion, err := integer(schemaValue)
	if err != nil {
		return Header{}, &ValidationError{"invalid schema version"}
	}
	if schemaVersion > SchemaVersion {
		return Header{}, &SchemaTooNewError{Found: schemaVersion, Supported: SchemaVersion}
	}
	if applicationID != int64(ApplicationID) || schemaVersion != SchemaVersion {
		return Header{}, &ValidationError{fmt.Sprintf("expected application %#x/schema %d, found %#x/schema %d", ApplicationID, SchemaVersion, applicationID, schemaVersion)}
	}
	row, err := db.Rows(ctx, "SELECT minimum_writer_protocol, minimum_writer_build, source_generation FROM schema_meta WHERE singleton = 1")
	if err != nil || len(row) != 1 || len(row[0]) != 3 {
		return Header{}, &ValidationError{"schema_meta singleton is missing"}
	}
	protocol, err := integer(row[0][0])
	if err != nil {
		return Header{}, &ValidationError{"invalid minimum writer protocol"}
	}
	build, err := integer(row[0][1])
	if err != nil {
		return Header{}, &ValidationError{"invalid minimum writer build"}
	}
	generation, err := integer(row[0][2])
	if err != nil {
		return Header{}, &ValidationError{"invalid source generation"}
	}
	return Header{uint32(applicationID), schemaVersion, protocol, build, generation}, nil
}

func validateCoreShape(ctx context.Context, db ReadOnlyDB) error {
	for table, expected := range coreColumns {
		rows, err := db.Rows(ctx, "PRAGMA table_info("+table+")")
		if err != nil || len(rows) == 0 {
			return &ValidationError{fmt.Sprintf("required table %q is missing", table)}
		}
		actual := make([]string, 0, len(rows))
		for _, row := range rows {
			if len(row) < 2 {
				return &ValidationError{fmt.Sprintf("table %q has malformed column metadata", table)}
			}
			name, ok := row[1].(string)
			if !ok {
				return &ValidationError{fmt.Sprintf("table %q has malformed column metadata", table)}
			}
			actual = append(actual, name)
		}
		if len(actual) != len(expected) {
			return &ValidationError{fmt.Sprintf("table %q has unexpected columns", table)}
		}
		for i := range expected {
			if actual[i] != expected[i] {
				return &ValidationError{fmt.Sprintf("table %q is missing column %q", table, expected[i])}
			}
		}
	}
	return nil
}

func integer(value any) (int64, error) {
	switch value := value.(type) {
	case int:
		return int64(value), nil
	case int64:
		return value, nil
	case int32:
		return int64(value), nil
	case uint64:
		if value <= ^uint64(0)>>1 {
			return int64(value), nil
		}
	case uint32:
		return int64(value), nil
	}
	return 0, fmt.Errorf("not an integer")
}
