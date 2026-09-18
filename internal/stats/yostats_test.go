package stats

import (
	"context"
	"database/sql"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

type fixtureDB struct {
	applicationID any
	schemaVersion any
	meta          [][]any
	columns       map[string][][]any
	queryOnly     bool
	closed        bool
}

func (f *fixtureDB) Scalar(_ context.Context, query string, _ ...any) (any, error) {
	if strings.Contains(query, "application_id") {
		return f.applicationID, nil
	}
	return f.schemaVersion, nil
}
func (f *fixtureDB) Rows(_ context.Context, query string, _ ...any) ([][]any, error) {
	if strings.HasPrefix(query, "SELECT") {
		return f.meta, nil
	}
	for table, rows := range f.columns {
		if strings.Contains(query, "("+table+")") {
			return rows, nil
		}
	}
	return nil, errors.New("unknown query")
}
func (f *fixtureDB) QueryOnly(context.Context) error { f.queryOnly = true; return nil }
func (f *fixtureDB) Close() error                    { f.closed = true; return nil }

func validFixture() *fixtureDB {
	f := &fixtureDB{applicationID: int64(ApplicationID), schemaVersion: SchemaVersion, meta: [][]any{{int64(25), int64(8), int64(4)}}, columns: map[string][][]any{}}
	for table, columns := range coreColumns {
		rows := make([][]any, len(columns))
		for i, column := range columns {
			rows[i] = []any{int64(i), column}
		}
		f.columns[table] = rows
	}
	for table := range ringColumns {
		f.columns[table] = nil
	}
	return f
}

func TestOpenReadOnlyRejectsPartialRingContract(t *testing.T) {
	fixture := validFixture()
	fixture.columns["aggregate_publication"] = [][]any{{int64(0), "singleton"}}
	_, err := OpenReadOnly(context.Background(), "copy.sqlite3", func(string) (ReadOnlyDB, error) { return fixture, nil })
	var validation *ValidationError
	if !errors.As(err, &validation) || !strings.Contains(err.Error(), "partially present") || !fixture.closed {
		t.Fatalf("error = %v, closed = %t", err, fixture.closed)
	}
}

func TestOpenReadOnlyValidatesSchema(t *testing.T) {
	fixture := validFixture()
	var uri string
	database, err := OpenReadOnly(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"), func(value string) (ReadOnlyDB, error) {
		uri = value
		return fixture, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if !fixture.queryOnly || database.Header.SourceGeneration != 4 || !strings.Contains(uri, "mode=ro") {
		t.Fatalf("read-only setup/header/URI = %t/%+v/%q", fixture.queryOnly, database.Header, uri)
	}
	_ = database.Close()
}

func TestOpenReadOnlyRejectsMissingTableOrColumn(t *testing.T) {
	for name, mutate := range map[string]func(*fixtureDB){
		"missing table":  func(f *fixtureDB) { delete(f.columns, "observations") },
		"missing column": func(f *fixtureDB) { f.columns["usage_atoms"] = f.columns["usage_atoms"][:6] },
	} {
		t.Run(name, func(t *testing.T) {
			fixture := validFixture()
			mutate(fixture)
			_, err := OpenReadOnly(context.Background(), "copy.sqlite3", func(string) (ReadOnlyDB, error) { return fixture, nil })
			var validation *ValidationError
			if !errors.As(err, &validation) || !fixture.closed {
				t.Fatalf("error = %v, closed = %t", err, fixture.closed)
			}
		})
	}
}

func TestOpenReadOnlyRejectsNewerSchema(t *testing.T) {
	fixture := validFixture()
	fixture.schemaVersion = SchemaVersion + 1
	_, err := OpenReadOnly(context.Background(), "copy.sqlite3", func(string) (ReadOnlyDB, error) { return fixture, nil })
	var tooNew *SchemaTooNewError
	if !errors.As(err, &tooNew) || tooNew.Found != SchemaVersion+1 || !fixture.closed {
		t.Fatalf("error = %v, closed = %t", err, fixture.closed)
	}
}

func TestOpenReadOnlyRejectsMalformedDatabase(t *testing.T) {
	_, err := OpenReadOnly(context.Background(), "malformed.sqlite3", func(string) (ReadOnlyDB, error) { return nil, errors.New("file is not a database") })
	if err == nil || !strings.Contains(err.Error(), "read-only") {
		t.Fatalf("error = %v", err)
	}
}

func TestOpenReadOnlyUsesActualSQLiteFileAndRejectsWrites(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)

	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if database.sqlDB == nil {
		t.Fatal("real SQLite opener did not retain its connection")
	}
	if _, err := database.sqlDB.ExecContext(context.Background(), "CREATE TABLE should_fail (value TEXT)"); err == nil {
		t.Fatal("read-only database accepted a write")
	}
}

func TestFactReadersDecodeBoundsAndOrder(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO observations VALUES
('b','cpu','host',20,'e',1,'{"value":2}'),
('a','cpu','host',10,'e',1,'{"value":1}'),
('outside','cpu','host',30,'e',1,'{"value":3}')`)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`INSERT INTO coverage_epochs VALUES ('cpu','host','e',10,20,1,1)`); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`INSERT INTO unavailable_spans VALUES ('gpu','host','e',12,13,1,'offline',1)`); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`INSERT INTO usage_atoms VALUES ('u','input','text','none','tokens',11,'{"quantity":4}')`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	statsDB, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer statsDB.Close()
	observations, err := statsDB.ReadObservations(context.Background(), 10, 30, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(observations) != 2 || observations[0].EventID != "a" || observations[1].EventID != "b" || observations[0].Payload["value"] != float64(1) {
		t.Fatalf("observations = %+v", observations)
	}
	coverage, err := statsDB.ReadCoverageEpochs(context.Background(), 10, 11, 10)
	if err != nil || len(coverage) != 1 {
		t.Fatalf("coverage = %+v, err = %v", coverage, err)
	}
	unavailable, err := statsDB.ReadUnavailableSpans(context.Background(), 10, 13, 10)
	if err != nil || len(unavailable) != 1 {
		t.Fatalf("unavailable = %+v, err = %v", unavailable, err)
	}
	usage, err := statsDB.ReadUsageAtoms(context.Background(), 10, 12, 10)
	if err != nil || len(usage) != 1 || usage[0].Payload["quantity"] != float64(4) {
		t.Fatalf("usage = %+v, err = %v", usage, err)
	}
}

func TestFactReadersIncludeCoverageEpochStartedBeforeWindow(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO coverage_epochs VALUES ('system_memory','host','memory',0,20,60,1)`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	statsDB, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer statsDB.Close()
	coverage, err := statsDB.ReadCoverageEpochs(context.Background(), 10, 11, 10)
	if err != nil || len(coverage) != 1 || coverage[0].EpochID != "memory" {
		t.Fatalf("coverage = %+v, err = %v", coverage, err)
	}
}

func TestFactReadersRejectMalformedRowsAndLimits(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`INSERT INTO observations VALUES ('bad','cpu','host',10,'e',1,'not-json')`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	statsDB, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer statsDB.Close()
	if _, err := statsDB.ReadObservations(context.Background(), 0, 20, 1); err == nil {
		t.Fatal("malformed payload accepted")
	}
	if _, err := statsDB.ReadObservations(context.Background(), 0, 20, MaxFactReadLimit+1); err == nil {
		t.Fatal("unbounded limit accepted")
	}
	if _, err := statsDB.ReadObservations(context.Background(), 20, 10, 1); err == nil {
		t.Fatal("reversed range accepted")
	}
}

func TestFactReadersHonorCanceledContext(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	statsDB, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer statsDB.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := statsDB.ReadObservations(ctx, 0, 20, 1); err == nil || !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v", err)
	}
}

func TestOpenReadOnlyRejectsActualSQLiteVariants(t *testing.T) {
	tests := map[string]func(*sql.DB){
		"missing table": func(db *sql.DB) { _, _ = db.Exec("DROP TABLE observations") },
		"missing column": func(db *sql.DB) {
			_, _ = db.Exec("ALTER TABLE usage_atoms RENAME TO usage_atoms_old; CREATE TABLE usage_atoms (event_id TEXT, direction TEXT, modality TEXT, cache_role TEXT, unit TEXT, observed_at REAL); DROP TABLE usage_atoms_old")
		},
		"newer schema": func(db *sql.DB) { _, _ = db.Exec("PRAGMA user_version = 10") },
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "stats.sqlite3")
			createSQLiteFixture(t, path)
			mutable, err := sql.Open("sqlite3", path)
			if err != nil {
				t.Fatal(err)
			}
			mutate(mutable)
			if err := mutable.Close(); err != nil {
				t.Fatal(err)
			}
			_, err = OpenReadOnly(context.Background(), path)
			if err == nil {
				t.Fatal("incompatible SQLite file was accepted")
			}
		})
	}
}

func TestOpenReadOnlyRejectsActualMalformedSQLiteFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "malformed.sqlite3")
	if err := os.WriteFile(path, []byte("not a SQLite database"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := OpenReadOnly(context.Background(), path); err == nil {
		t.Fatal("malformed SQLite file was accepted")
	}
}

func createSQLiteFixture(t *testing.T, path string) {
	t.Helper()
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	statements := []string{
		"PRAGMA application_id = 0x594F5354",
		"PRAGMA user_version = 9",
		"CREATE TABLE schema_meta (singleton INTEGER, minimum_writer_protocol INTEGER, minimum_writer_build INTEGER, source_generation INTEGER, last_vacuumed_at REAL)",
		"INSERT INTO schema_meta VALUES (1, 25, 8, 4, 0)",
	}
	for table, columns := range coreColumns {
		if table == "schema_meta" {
			continue
		}
		definitions := make([]string, len(columns))
		for i, column := range columns {
			columnType := "TEXT"
			switch column {
			case "observed_at", "started_at", "ended_at", "native_cadence_seconds":
				columnType = "REAL"
			case "owner_generation":
				columnType = "INTEGER"
			}
			definitions[i] = column + " " + columnType
		}
		statements = append(statements, "CREATE TABLE "+table+" ("+strings.Join(definitions, ", ")+")")
	}
	for _, statement := range statements {
		if _, err := db.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
}
