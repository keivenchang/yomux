package stats

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

type opencodeQueryCaptureDB struct {
	query string
	args  []any
}

func (*opencodeQueryCaptureDB) Scalar(context.Context, string, ...any) (any, error) {
	return nil, errors.New("unexpected scalar query")
}

func (db *opencodeQueryCaptureDB) Rows(_ context.Context, query string, args ...any) ([][]any, error) {
	db.query = query
	db.args = append([]any(nil), args...)
	return nil, nil
}

func (*opencodeQueryCaptureDB) QueryOnly(context.Context) error { return nil }
func (*opencodeQueryCaptureDB) Close() error                    { return nil }

func TestOpenCodeSelectionQueryAvoidsHistoryCountAndCapsRevisionRows(t *testing.T) {
	reader := &opencodeQueryCaptureDB{}
	database := &OpenCodeDatabase{reader: reader, sessionCounterColumns: map[string]bool{}, partSessionTimeIndex: true}
	_, err := database.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		{SessionID: "root", RootSessionID: "root", ActiveSince: 100, CursorRowID: 42},
	}, 1000, map[string]OpenCodePartCursor{"root": {RowID: 42}}, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.ToUpper(reader.query), "COUNT(P.ROWID)") {
		t.Fatalf("step-finish selection query counts unbounded history: %s", reader.query)
	}
	for _, fragment := range []string{
		"selected.cursor_rowid = 0",
		"ORDER BY revised.time_updated DESC, revised.rowid DESC",
		"LIMIT ?",
	} {
		if !strings.Contains(reader.query, fragment) {
			t.Fatalf("step-finish selection query lacks bounded-work clause %q: %s", fragment, reader.query)
		}
	}
	if placeholders := strings.Count(reader.query, "?"); placeholders != len(reader.args) {
		t.Fatalf("step-finish query placeholders=%d args=%d", placeholders, len(reader.args))
	}
	revisionLimit, revisionLimitErr := openCodeInteger(reader.args[11])
	if revisionLimitErr != nil || revisionLimit != MaxOpenCodePartReadLimit+1 {
		t.Fatalf("revision query limit = %v, want global result limit plus overflow sentinel (%d)", reader.args[11], MaxOpenCodePartReadLimit+1)
	}
	reader = &opencodeQueryCaptureDB{}
	database = &OpenCodeDatabase{reader: reader, sessionCounterColumns: map[string]bool{}, partSessionTimeIndex: true}
	_, err = database.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		{SessionID: "root", RootSessionID: "root", ActiveSince: 100},
	}, 1000, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(reader.query, "active.session_id = selected.session_id") || !strings.Contains(reader.query, "ORDER BY active.time_updated DESC, active.rowid DESC") {
		t.Fatalf("initial indexed scan is not newest-first and session-bounded: %s", reader.query)
	}
	if placeholders := strings.Count(reader.query, "?"); placeholders != len(reader.args) {
		t.Fatalf("initial indexed step-finish query placeholders=%d args=%d", placeholders, len(reader.args))
	}
	initialTailLimit, initialTailLimitErr := openCodeInteger(reader.args[8])
	if initialTailLimitErr != nil || initialTailLimit != MaxOpenCodePartReadLimit+1 {
		t.Fatalf("initial indexed tail limit = %v, want result limit plus overflow sentinel (%d)", reader.args[8], MaxOpenCodePartReadLimit+1)
	}
	reader = &opencodeQueryCaptureDB{}
	database = &OpenCodeDatabase{reader: reader, sessionCounterColumns: map[string]bool{}}
	_, err = database.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		{SessionID: "root", RootSessionID: "root", ActiveSince: 100},
	}, 1000, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(reader.query, "MAX(p.time_updated)") || strings.Contains(reader.query, "FROM part AS revised") || !strings.Contains(reader.query, "p.rowid > max_part.max_rowid - ?") {
		t.Fatalf("unindexed query is not limited to the bounded rowid tail: %s", reader.query)
	}
	if placeholders := strings.Count(reader.query, "?"); placeholders != len(reader.args) {
		t.Fatalf("unindexed step-finish query placeholders=%d args=%d", placeholders, len(reader.args))
	}
	globalTailLimit, globalTailLimitErr := openCodeInteger(reader.args[3])
	if globalTailLimitErr != nil || globalTailLimit != openCodeActivePartRows {
		t.Fatalf("unindexed global rowid tail limit = %v, want conservative bound %d", reader.args[3], openCodeActivePartRows)
	}
	reader = &opencodeQueryCaptureDB{}
	database = &OpenCodeDatabase{reader: reader, sessionCounterColumns: map[string]bool{}, partSessionIndex: true}
	_, err = database.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		{SessionID: "root", RootSessionID: "root", ActiveSince: 100},
	}, 1000, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(reader.query, "WHERE tail.session_id = selected.session_id") || !strings.Contains(reader.query, "ORDER BY tail.rowid DESC") || strings.Contains(reader.query, "p.rowid > max_part.max_rowid - ?") {
		t.Fatalf("session-index fallback is not using a per-session rowid tail: %s", reader.query)
	}
	if placeholders := strings.Count(reader.query, "?"); placeholders != len(reader.args) {
		t.Fatalf("per-session step-finish query placeholders=%d args=%d", placeholders, len(reader.args))
	}
	sessionTailLimit, sessionTailLimitErr := openCodeInteger(reader.args[4])
	if sessionTailLimitErr != nil || sessionTailLimit != MaxOpenCodePartReadLimit+1 {
		t.Fatalf("per-session tail limit = %v, want result limit plus overflow sentinel (%d)", reader.args[4], MaxOpenCodePartReadLimit+1)
	}
}

func TestOpenCodeRawPartPagesBoundBeforeFilteringAndAdvanceAcrossNoise(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-pages.db")
	sqlDB, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	statements := []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('selected', '/synthetic', '', '{"providerID":"synthetic","id":"model"}', 1, 10, 0)`,
		`INSERT INTO session VALUES ('unselected', '/synthetic', '', '{"providerID":"synthetic","id":"model"}', 1, 10, 0)`,
		`INSERT INTO message VALUES ('msg-selected', 'selected', 1, 10, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-unselected', 'unselected', 1, 10, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('noise-tool', 'msg-selected', 'selected', 1, 1, '{"type":"tool","name":"shell"}')`,
		`INSERT INTO part VALUES ('noise-text', 'msg-selected', 'selected', 2, 2, '{"type":"text","text":"thinking"}')`,
		`INSERT INTO part VALUES ('noise-other-session', 'msg-unselected', 'unselected', 3, 3, '{"type":"step-finish","tokens":{"input":99}}')`,
		`INSERT INTO part VALUES ('eligible', 'msg-selected', 'selected', 4, 4, '{"type":"step-finish","tokens":{"input":7,"output":2}}')`,
	}
	for _, statement := range statements {
		if _, err := sqlDB.Exec(statement); err != nil {
			_ = sqlDB.Close()
			t.Fatal(err)
		}
	}
	if err := sqlDB.Close(); err != nil {
		t.Fatal(err)
	}

	database, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if database.partSessionIndex || database.partSessionTimeIndex {
		t.Fatal("synthetic page fixture unexpectedly has a session index")
	}
	highWater, err := database.ReadPartHighWaterRowID(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if highWater != 4 {
		t.Fatalf("high-water rowid=%d, want 4", highWater)
	}
	selections := []OpenCodeStepFinishSelection{{SessionID: "selected", RootSessionID: "root-selected"}}
	first, err := database.ReadStepFinishPartsPage(context.Background(), selections, 0, highWater, 3)
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Candidates) != 0 || len(first.Cursors) != 0 || len(first.RootErrors) != 0 {
		t.Fatalf("noise-only page emitted data: %+v", first)
	}
	if first.ScannedThroughRowID != 3 || !first.HasMore || first.HighWaterRowID != highWater {
		t.Fatalf("noise-only page cursor/high-water/more = %d/%d/%t, want 3/%d/true", first.ScannedThroughRowID, first.HighWaterRowID, first.HasMore, highWater)
	}
	second, err := database.ReadStepFinishPartsPage(context.Background(), selections, first.ScannedThroughRowID, first.HighWaterRowID, 3)
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Candidates) != 1 || second.Candidates[0].PartID != "eligible" || second.Candidates[0].RootSessionID != "root-selected" {
		t.Fatalf("selected eligible candidate/root = %+v, want eligible under root-selected", second.Candidates)
	}
	if cursor := second.Cursors["selected"]; cursor.RowID != 4 || cursor.PartID != "eligible" {
		t.Fatalf("eligible cursor = %+v, want rowid 4 / eligible", cursor)
	}
	if second.ScannedThroughRowID != 4 || second.HasMore || second.HighWaterRowID != highWater {
		t.Fatalf("final page cursor/high-water/more = %d/%d/%t, want 4/%d/false", second.ScannedThroughRowID, second.HighWaterRowID, second.HasMore, highWater)
	}
}

func TestOpenCodeRawPartPageSentinelAndMalformedPartRootError(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-page-sentinel.db")
	sqlDB, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	statements := []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('selected', '/synthetic', '', '{"providerID":"synthetic","id":"model"}', 1, 10, 0)`,
		`INSERT INTO message VALUES ('msg-selected', 'selected', 1, 10, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('bad-json', 'msg-selected', 'selected', 1, 1, '{')`,
		`INSERT INTO part VALUES ('second-row', 'msg-selected', 'selected', 2, 2, '{"type":"text","text":"noise"}')`,
		`INSERT INTO part VALUES ('third-row', 'msg-selected', 'selected', 3, 3, '{"type":"text","text":"noise"}')`,
	}
	for _, statement := range statements {
		if _, err := sqlDB.Exec(statement); err != nil {
			_ = sqlDB.Close()
			t.Fatal(err)
		}
	}
	if err := sqlDB.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	page, err := database.ReadStepFinishPartsPage(context.Background(), []OpenCodeStepFinishSelection{{SessionID: "selected", RootSessionID: "root-selected"}}, 0, 3, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(page.Candidates) != 0 || page.RootErrors["root-selected"] != "malformed-transcript-json" {
		t.Fatalf("malformed selected part root error = %+v, want malformed-transcript-json", page.RootErrors)
	}
	if page.ScannedThroughRowID != 2 || !page.HasMore {
		t.Fatalf("sentinel page scanned-through/more = %d/%t, want 2/true", page.ScannedThroughRowID, page.HasMore)
	}
}

func TestOpenCodeRawPartPageRejectsInvalidBoundsAndSelectionIdentity(t *testing.T) {
	database := &OpenCodeDatabase{reader: &opencodeQueryCaptureDB{}}
	valid := []OpenCodeStepFinishSelection{{SessionID: "session", RootSessionID: "root"}}
	tests := []struct {
		name       string
		selections []OpenCodeStepFinishSelection
		after      int64
		highWater  int64
		pageSize   int
	}{
		{name: "empty selections", after: 0, highWater: 0, pageSize: 1},
		{name: "negative after", selections: valid, after: -1, highWater: 0, pageSize: 1},
		{name: "high water before after", selections: valid, after: 2, highWater: 1, pageSize: 1},
		{name: "zero page size", selections: valid, after: 0, highWater: 0, pageSize: 0},
		{name: "blank session", selections: []OpenCodeStepFinishSelection{{SessionID: " ", RootSessionID: "root"}}, after: 0, highWater: 0, pageSize: 1},
		{name: "blank root", selections: []OpenCodeStepFinishSelection{{SessionID: "session", RootSessionID: " "}}, after: 0, highWater: 0, pageSize: 1},
		{name: "duplicate session", selections: append(valid, valid[0]), after: 0, highWater: 0, pageSize: 1},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := database.ReadStepFinishPartsPage(context.Background(), test.selections, test.after, test.highWater, test.pageSize)
			if err == nil {
				t.Fatal("invalid page request was accepted")
			}
		})
	}
}

func TestOpenCodePartSessionTimeIndexDetection(t *testing.T) {
	path := createOpenCodeFixture(t)
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	if !readOnly.partSessionTimeIndex {
		t.Fatal("matching synthetic session/time index was not detected")
	}
	if readOnly.partSessionIndex {
		t.Fatal("composite session/time index was incorrectly treated as a rowid-seek index")
	}
	planRows, err := readOnly.reader.Rows(context.Background(), `EXPLAIN QUERY PLAN
SELECT revised.rowid
FROM part AS revised
WHERE revised.session_id = ?
  AND revised.rowid <= ?
  AND revised.time_updated >= ?
  AND revised.time_updated < ?
ORDER BY revised.time_updated DESC, revised.rowid DESC
LIMIT ?`, "ses-1", 100, 1000, 3000, 10)
	if err != nil {
		t.Fatal(err)
	}
	var queryPlan strings.Builder
	for _, row := range planRows {
		if len(row) != 4 {
			t.Fatalf("revision query plan row has %d columns", len(row))
		}
		detail, ok := openCodeString(row[3])
		if !ok {
			t.Fatalf("revision query plan detail is malformed: %#v", row[3])
		}
		queryPlan.WriteString(detail)
		queryPlan.WriteByte('\n')
	}
	plan := queryPlan.String()
	partialOrderSort := strings.Contains(plan, "USE TEMP B-TREE FOR RIGHT PART OF ORDER BY") ||
		strings.Contains(plan, "USE TEMP B-TREE FOR LAST TERM OF ORDER BY")
	if !strings.Contains(plan, "part_session_updated") || !partialOrderSort || strings.Contains(plan, "USE TEMP B-TREE FOR ORDER BY") {
		t.Fatalf("revision lookup must use the time index and only sort rowid ties: %s", plan)
	}
}

func TestOpenCodeUnindexedRevisionLookupIsBoundedAndPartial(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-unindexed-revisions.db")
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_idx ON part(session_id)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 1000, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-revised', 'root', 1, 900, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-revised', 'msg-revised', 'root', 1, 100, '{"type":"step-finish","tokens":{"input":9}}')`,
	} {
		if _, err := database.Exec(statement); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	for index := 0; index < openCodeActivePartRows+1; index++ {
		messageID := fmt.Sprintf("msg-filler-%04d", index)
		partID := fmt.Sprintf("part-filler-%04d", index)
		if _, err := database.Exec(`INSERT INTO message VALUES (?, 'root', 100, 100, '{"role":"assistant"}')`, messageID); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
		if _, err := database.Exec(`INSERT INTO part VALUES (?, ?, 'root', 100, 100, '{"type":"step-finish","tokens":{"input":1}}')`, partID, messageID); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	var cursorRowID int64
	if err := database.QueryRow(`SELECT MAX(rowid) FROM part WHERE session_id = 'root'`).Scan(&cursorRowID); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if _, err := database.Exec(`INSERT INTO message VALUES ('msg-fresh', 'root', 950, 950, '{"role":"assistant"}')`); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if _, err := database.Exec(`INSERT INTO part VALUES ('part-fresh', 'msg-fresh', 'root', 950, 950, '{"type":"step-finish","tokens":{"input":2}}')`); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	// Unrelated sessions can advance the database-wide rowid far beyond the
	// selected session's fresh part. The session index should let the bounded
	// fallback retain that part instead of treating global rowid as a session
	// cursor.
	tx, err := database.Begin()
	if err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	for index := 0; index < openCodeActivePartRows*3; index++ {
		partID := fmt.Sprintf("part-unrelated-%05d", index)
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, 'msg-unrelated', 'unrelated', 950, 950, '{"type":"step-finish","tokens":{"input":1}}')`, partID); err != nil {
			_ = tx.Rollback()
			_ = database.Close()
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	if readOnly.partSessionTimeIndex {
		t.Fatal("session-only synthetic index was treated as a bounded revision index")
	}
	if !readOnly.partSessionIndex {
		t.Fatal("session-only synthetic index was not detected for bounded rowid seeks")
	}
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "root", RootSessionID: "root", ActiveSince: 800, CursorRowID: cursorRowID,
	}}, 1000, map[string]OpenCodePartCursor{"root": {RowID: cursorRowID}}, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-fresh" {
		t.Fatalf("unindexed bounded rows = %#v, want only the fresh append", window.Parts)
	}
	if !window.TruncatedRoots["root"] {
		t.Fatalf("unindexed revision scan was not reported partial: %#v", window.TruncatedRoots)
	}
	initialWindow, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "root", RootSessionID: "root", ActiveSince: 1,
	}}, 1000, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(initialWindow.Parts) > MaxOpenCodePartReadLimit || !initialWindow.TruncatedRoots["root"] {
		t.Fatalf("unindexed initial history = parts %d, truncated roots %#v", len(initialWindow.Parts), initialWindow.TruncatedRoots)
	}
}

func TestOpenCodeSessionTailDoesNotDuplicateIndexedRevisionRows(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-indexed-revision-tail.db")
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_idx ON part(session_id)`,
		`CREATE INDEX part_session_time_idx ON part(session_id, time_updated)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 1000, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-cursor', 'root', 700, 700, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-cursor', 'msg-cursor', 'root', 700, 700, '{"type":"step-finish","tokens":{"input":1}}')`,
		`INSERT INTO message VALUES ('msg-revised', 'root', 900, 900, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-revised', 'msg-revised', 'root', 900, 900, '{"type":"step-finish","tokens":{"input":9}}')`,
	} {
		if _, err := database.Exec(statement); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	planRows, err := database.Query(`EXPLAIN QUERY PLAN
SELECT tail.rowid
FROM part AS tail
WHERE tail.session_id = ?
ORDER BY tail.rowid DESC
LIMIT ?`, "root", openCodeActivePartRows)
	if err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	var queryPlan strings.Builder
	for planRows.Next() {
		var id, parent, auxiliary int
		var detail string
		if err := planRows.Scan(&id, &parent, &auxiliary, &detail); err != nil {
			_ = planRows.Close()
			_ = database.Close()
			t.Fatal(err)
		}
		queryPlan.WriteString(detail)
		queryPlan.WriteByte('\n')
	}
	if err := planRows.Err(); err != nil {
		_ = planRows.Close()
		_ = database.Close()
		t.Fatal(err)
	}
	if err := planRows.Close(); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if plan := queryPlan.String(); !strings.Contains(plan, "part_session_idx") || strings.Contains(plan, "TEMP B-TREE") {
		_ = database.Close()
		t.Fatalf("per-session rowid tail plan is not index-bounded: %s", plan)
	}
	tx, err := database.Begin()
	if err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	for index := 0; index < openCodeActivePartRows*3; index++ {
		partID := fmt.Sprintf("part-unrelated-%05d", index)
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, 'msg-unrelated', 'unrelated', 900, 900, '{"type":"step-finish","tokens":{"input":1}}')`, partID); err != nil {
			_ = tx.Rollback()
			_ = database.Close()
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}

	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	if !readOnly.partSessionTimeIndex {
		t.Fatal("synthetic session/time index was not detected")
	}
	if !readOnly.partSessionIndex {
		t.Fatal("synthetic session-only index was not detected for rowid seek")
	}
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "root", RootSessionID: "root", ActiveSince: 800, CursorRowID: 1,
	}}, 1000, map[string]OpenCodePartCursor{"root": {RowID: 1}}, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-revised" {
		t.Fatalf("indexed per-session tail duplicated or lost a revised row: %#v", window.Parts)
	}
}

func TestOpenCodeReadOnlyReadsStepFinishParts(t *testing.T) {
	path := copyOpenCodeFixture(t)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	parts, err := db.ReadStepFinishParts(context.Background(), "ses-1", 1000, 3000, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(parts) != 1 || parts[0].PartID != "part-1" || parts[0].Tokens.Input != 3 || parts[0].Tokens.CacheRead != 2 || parts[0].Provider != "openai" || parts[0].Model != "gpt" {
		t.Fatalf("parts = %+v", parts)
	}
	metrics := db.ReadMetrics()
	if metrics.Queries == 0 || metrics.Duration <= 0 || metrics.LastDuration <= 0 {
		t.Fatalf("OpenCode SQLite metrics = %+v, want measured query work", metrics)
	}
}

func TestOpenCodeSourceScanVersionRequiresChangedOrUnseenSource(t *testing.T) {
	path := createOpenCodeFixture(t)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	version, err := db.ReadSourceDataVersion(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !db.SourceScanRequired(version, []string{"ses-1"}, false) {
		t.Fatal("first OpenCode source scan was incorrectly skipped")
	}
	db.CommitSourceScan(version, []string{"ses-1"})
	if db.SourceScanRequired(version, []string{"ses-1"}, false) {
		t.Fatal("unchanged, previously scanned OpenCode session requested another scan")
	}
	if !db.SourceScanRequired(version, []string{"ses-1", "new-child"}, false) {
		t.Fatal("newly selected OpenCode session did not request a scan")
	}
	db.CommitSourceScan(version, []string{"new-child"})
	if !db.SourceScanRequired(version, []string{"ses-1"}, false) {
		t.Fatal("session outside the retained selection frontier was treated as scanned")
	}

	mutateSQLite(t, path, `UPDATE session SET time_updated = time_updated + 1 WHERE id = 'ses-1'`)
	updatedVersion, err := db.ReadSourceDataVersion(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if updatedVersion == version {
		t.Fatalf("OpenCode data_version did not advance after external commit: %d", version)
	}
	if !db.SourceScanRequired(updatedVersion, []string{"ses-1"}, false) {
		t.Fatal("external OpenCode commit did not request a source scan")
	}
}

func TestOpenCodeSourceDataVersionSeesExternalWALCommit(t *testing.T) {
	path := createOpenCodeFixture(t)
	writer, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	var journalMode string
	if err := writer.QueryRow(`PRAGMA journal_mode = WAL`).Scan(&journalMode); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if journalMode != "wal" {
		_ = writer.Close()
		t.Fatalf("synthetic OpenCode journal mode = %q, want wal", journalMode)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	reader, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	before, err := reader.ReadSourceDataVersion(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	mutateSQLite(t, path, `UPDATE session SET time_updated = time_updated + 1 WHERE id = 'ses-1'`)
	after, err := reader.ReadSourceDataVersion(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if after == before {
		t.Fatalf("OpenCode data_version did not advance after external WAL commit: %d", before)
	}
}

func TestOpenCodeReadOnlyFallsBackToMessageModelWhenSessionModelIsNull(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-null-session-model.db")
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('ses-null-model', '/fixture', 'build', NULL, 1, 100, 0)`,
		`INSERT INTO message VALUES ('msg-null-model', 'ses-null-model', 90, 90, '{"role":"assistant","providerID":"openai","modelID":"gpt-message"}')`,
		`INSERT INTO part VALUES ('part-null-model', 'msg-null-model', 'ses-null-model', 90, 90, '{"type":"step-finish","tokens":{"input":3,"output":2}}')`,
	} {
		if _, err := database.Exec(statement); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	parts, err := readOnly.ReadStepFinishParts(context.Background(), "ses-null-model", 1, 1000, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(parts) != 1 || parts[0].Provider != "openai" || parts[0].Model != "gpt-message" || parts[0].Tokens.Input != 3 || parts[0].Tokens.Output != 2 {
		t.Fatalf("message model fallback = %#v", parts)
	}
}

func TestOpenCodeInitialWindowRejectsPositiveCumulativeAnchorMismatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-anchor-mismatch.db")
	createOpenCodeCounterFixture(t, path, 10, true)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "ses-anchor", RootSessionID: "ses-anchor", ActiveSince: 1,
	}}, 1000, nil, MaxOpenCodePartReadLimit)
	if err == nil || !strings.Contains(err.Error(), "opencode-cumulative-anchor-mismatch") {
		t.Fatalf("positive cumulative mismatch error = %v", err)
	}
}

func TestOpenCodeInitialWindowRejectsPositiveReasoningAnchorMismatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-reasoning-anchor-mismatch.db")
	createOpenCodeCounterFixture(t, path, 0, true)
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.Exec(`UPDATE session SET tokens_reasoning = 5`); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if _, err := database.Exec(`UPDATE part SET data = '{"type":"step-finish","tokens":{"input":3,"reasoning":1}}'`); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "ses-anchor", RootSessionID: "ses-anchor", ActiveSince: 1,
	}}, 1000, nil, MaxOpenCodePartReadLimit)
	if err == nil || !strings.Contains(err.Error(), "opencode-cumulative-anchor-mismatch") {
		t.Fatalf("positive reasoning cumulative mismatch error = %v", err)
	}
}

func TestOpenCodeInitialWindowRejectsMissingPositiveCumulativeHistory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-anchor-missing.db")
	createOpenCodeCounterFixture(t, path, 10, false)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "ses-anchor", RootSessionID: "ses-anchor", ActiveSince: 1,
	}}, 1000, nil, MaxOpenCodePartReadLimit)
	if err == nil || !strings.Contains(err.Error(), "opencode-cumulative-history-anchor-missing") {
		t.Fatalf("missing positive cumulative history error = %v", err)
	}
}

func createOpenCodeCounterFixture(t *testing.T, path string, input int, includePart bool) {
	t.Helper()
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT, tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
	} {
		if _, err := database.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := database.Exec(`INSERT INTO session VALUES ('ses-anchor', '/fixture', 'build', '{"providerID":"fixture","id":"model"}', 1, 100, 0, NULL, ?, 0, 0, 0, 0)`, input); err != nil {
		t.Fatal(err)
	}
	if includePart {
		if _, err := database.Exec(`INSERT INTO message VALUES ('msg-anchor', 'ses-anchor', 10, 10, '{"role":"assistant"}')`); err != nil {
			t.Fatal(err)
		}
		if _, err := database.Exec(`INSERT INTO part VALUES ('part-anchor', 'msg-anchor', 'ses-anchor', 10, 10, '{"type":"step-finish","tokens":{"input":3}}')`); err != nil {
			t.Fatal(err)
		}
	}
}

func TestOpenCodeReadStepFinishPartsWindowReadsFreshFixtureRows(t *testing.T) {
	path := copyOpenCodeFixture(t)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	window, err := db.ReadStepFinishPartsWindow(
		context.Background(), []string{"ses-1"}, 900, 3000, 900, nil, MaxOpenCodePartReadLimit,
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-1" {
		t.Fatalf("window parts = %#v", window.Parts)
	}
}

func TestOpenCodeReadStepFinishPartsWindowBoundsLargeHistory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-large.db")
	writeOpenCodeLargeHistory(t, path, 65)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	window, err := db.ReadStepFinishPartsWindow(ctx, []string{"root"}, 1, 10_000_000, 9_900, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "fresh" {
		t.Fatalf("large-history window = %#v", window.Parts)
	}
}

func TestOpenCodeReadStepFinishPartsWindowStaysBoundedOnLongSyntheticHistory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-very-large.db")
	// This fixture is intentionally much larger than the active read bound. The
	// newest row must be returned without turning one collector cadence into a
	// full historical scan. It is entirely synthetic and contains no host
	// OpenCode path or database input.
	writeOpenCodeManyRootHistory(t, path, 1, 100_000)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	window, err := db.ReadStepFinishPartsForSelections(ctx, []OpenCodeStepFinishSelection{{
		SessionID: "root-00", RootSessionID: "root-00", ActiveSince: 9_900_000,
	}}, 10_000_001, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatalf("long-history bounded read failed: %v", err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-00-fresh" || window.Truncated {
		t.Fatalf("long-history window = parts=%d first=%#v truncated=%v", len(window.Parts), window.Parts, window.Truncated)
	}
	metrics := db.ReadMetrics()
	if metrics.Queries != 1 || metrics.LastDuration <= 0 {
		t.Fatalf("long-history SQLite metrics = %+v, want one measured bounded query", metrics)
	}
}

func TestOpenCodeInitialOldHistoryIsCappedAndMarkedPartial(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-old-initial-history.db")
	writeOpenCodeManyRootHistory(t, path, 1, 100_000)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	window, err := db.ReadStepFinishPartsForSelections(ctx, []OpenCodeStepFinishSelection{{
		SessionID: "root-00", RootSessionID: "root-00", ActiveSince: 1,
	}}, 10_000_001, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatalf("old initial history read failed: %v", err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-00-fresh" {
		t.Fatalf("old initial history parts = %#v, want only the fresh bounded row", window.Parts)
	}
	if !window.Truncated || !window.TruncatedRoots["root-00"] {
		t.Fatalf("old initial history was not marked partial: truncated=%v roots=%v", window.Truncated, window.TruncatedRoots)
	}
}

func TestOpenCodeInitialBoundCountsStepFinishOwnersNotToolRows(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-initial-tool-noise.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 700, 1000, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-step', 'root', 900, 900, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-step', 'msg-step', 'root', 900, 900, '{"type":"step-finish","tokens":{"input":7}}')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	for index := 0; index < 4; index++ {
		messageID := fmt.Sprintf("msg-tool-%d", index)
		partID := fmt.Sprintf("part-tool-%d", index)
		if _, err := db.Exec(`INSERT INTO message VALUES (?, 'root', ?, ?, '{"role":"tool"}')`, messageID, 991+index, 991+index); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
		if _, err := db.Exec(`INSERT INTO part VALUES (?, ?, 'root', ?, ?, '{"type":"tool"}')`, partID, messageID, 991+index, 991+index); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "root", RootSessionID: "root", ActiveSince: 800,
	}}, 1000, nil, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-step" || window.TruncatedRoots["root"] {
		t.Fatalf("initial owner-filtered window = parts=%#v truncated=%v; want the step-finish row without false truncation", window.Parts, window.TruncatedRoots)
	}
}

func TestOpenCodeOldToolRowsDoNotMoveInitialHistoryCutoff(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-old-tool-cutoff.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 1000000, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-step', 'root', 100, 100, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-step', 'msg-step', 'root', 100, 100, '{"type":"step-finish","tokens":{"input":7}}')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	for index := 0; index < 8; index++ {
		messageID := fmt.Sprintf("msg-old-noise-%d", index)
		partID := fmt.Sprintf("part-old-noise-%d", index)
		messageData := `{"role":"tool"}`
		partData := `{"type":"tool"}`
		if index%2 == 1 {
			messageData = `{"role":"assistant"}`
			partData = `{"type":"text","text":"not token usage"}`
		}
		if _, err := db.Exec(`INSERT INTO message VALUES (?, 'root', 400000, 400000, ?)`, messageID, messageData); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
		if _, err := db.Exec(`INSERT INTO part VALUES (?, ?, 'root', 400000, 400000, ?)`, partID, messageID, partData); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if _, err := db.Exec(`INSERT INTO message VALUES ('msg-new-tool', 'root', 900000, 900000, '{"role":"tool"}')`); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO part VALUES ('part-new-tool', 'msg-new-tool', 'root', 900000, 900000, '{"type":"tool"}')`); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "root", RootSessionID: "root", ActiveSince: 1,
	}}, 1000000, nil, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-step" {
		t.Fatalf("old tool rows moved initial cutoff and hid eligible history: parts=%#v truncated=%v", window.Parts, window.TruncatedRoots)
	}
	if window.TruncatedRoots["root"] {
		t.Fatalf("tool rows incorrectly marked eligible initial history partial: %#v", window.TruncatedRoots)
	}
}

func TestOpenCodeReadStepFinishPartsForSelectionsSharesBoundAndAttribution(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-multi-root.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root-a', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 100, 0, NULL)`,
		`INSERT INTO session VALUES ('root-b', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 100, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-a', 'root-a', 90, 90, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-b', 'root-b', 91, 91, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-a', 'msg-a', 'root-a', 90, 90, '{"type":"step-finish","tokens":{"input":2}}')`,
		`INSERT INTO part VALUES ('part-b', 'msg-b', 'root-b', 91, 91, '{"type":"step-finish","tokens":{"output":3}}')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		{SessionID: "root-a", RootSessionID: "root-a", ActiveSince: 1},
		{SessionID: "root-b", RootSessionID: "root-b", ActiveSince: 1},
	}, 1000, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != 2 || window.Parts[0].PartID != "part-a" || window.Parts[1].PartID != "part-b" {
		t.Fatalf("multi-root parts = %#v", window.Parts)
	}
	if window.Parts[0].RootSessionID != "root-a" || window.Parts[1].RootSessionID != "root-b" {
		t.Fatalf("multi-root attribution = %#v", window.Parts)
	}
	if window.Cursors["root-a"].PartID != "part-a" || window.Cursors["root-b"].PartID != "part-b" {
		t.Fatalf("multi-root cursors = %#v", window.Cursors)
	}
}

func TestOpenCodeReadStepFinishPartsForSelectionsPreventsBusyRootStarvation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-skewed-roots.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root-a', '/repo-a', 'build', '{"providerID":"openai","id":"gpt"}', 1, 300000, 0, NULL)`,
		`INSERT INTO session VALUES ('root-b', '/repo-b', 'build', '{"providerID":"openai","id":"gpt"}', 1, 10000, 0, NULL)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	tx, err := db.Begin()
	if err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	for index := 0; index <= MaxOpenCodePartReadLimit; index++ {
		messageID := fmt.Sprintf("msg-a-%04d", index)
		partID := fmt.Sprintf("part-a-%04d", index)
		updated := int64(100_000 + index)
		if _, err := tx.Exec(`INSERT INTO message VALUES (?, 'root-a', ?, ?, '{"role":"assistant"}')`, messageID, updated, updated); err != nil {
			_ = tx.Rollback()
			_ = db.Close()
			t.Fatal(err)
		}
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, 'root-a', ?, ?, '{"type":"step-finish","tokens":{"input":1}}')`, partID, messageID, updated, updated); err != nil {
			_ = tx.Rollback()
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if _, err := tx.Exec(`INSERT INTO message VALUES ('msg-b-fresh', 'root-b', 10000, 10000, '{"role":"assistant"}')`); err != nil {
		_ = tx.Rollback()
		_ = db.Close()
		t.Fatal(err)
	}
	if _, err := tx.Exec(`INSERT INTO part VALUES ('part-b-fresh', 'msg-b-fresh', 'root-b', 10000, 10000, '{"type":"step-finish","tokens":{"output":3}}')`); err != nil {
		_ = tx.Rollback()
		_ = db.Close()
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		{SessionID: "root-a", RootSessionID: "root-a", ActiveSince: 1},
		{SessionID: "root-b", RootSessionID: "root-b", ActiveSince: 1},
	}, 300_001, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) > MaxOpenCodePartReadLimit {
		t.Fatalf("skewed bounded rows = %d, want aggregate cap %d", len(window.Parts), MaxOpenCodePartReadLimit)
	}
	for _, part := range window.Parts {
		if part.PartID == "part-b-fresh" {
			if !window.TruncatedRoots["root-a"] {
				t.Fatalf("busy root truncation sentinel missing: %#v", window.TruncatedRoots)
			}
			return
		}
	}
	t.Fatalf("fresh root-b part was starved by newer root-a history; parts=%d truncated=%#v", len(window.Parts), window.TruncatedRoots)
}

func TestOpenCodeReadStepFinishPartsForSelectionsLargeTreeWithCursor(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-large-tree-cursor.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	for index := 0; index < 9; index++ {
		sessionID := fmt.Sprintf("root-%02d", index)
		messageID := "msg-" + sessionID
		partID := "part-" + sessionID
		for _, statement := range []struct {
			query string
			args  []any
		}{
			{`INSERT INTO session VALUES (?, '/fixture', 'build', '{"providerID":"openai","id":"gpt"}', 1, 100, 0, NULL)`, []any{sessionID}},
			{`INSERT INTO message VALUES (?, ?, 100, 100, '{"role":"assistant"}')`, []any{messageID, sessionID}},
			{`INSERT INTO part VALUES (?, ?, ?, 100, 100, '{"type":"step-finish","tokens":{"input":1}}')`, []any{partID, messageID, sessionID}},
		} {
			if _, err := db.Exec(statement.query, statement.args...); err != nil {
				_ = db.Close()
				t.Fatal(err)
			}
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	selections := make([]OpenCodeStepFinishSelection, 0, 9)
	for index := 0; index < 9; index++ {
		rootID := fmt.Sprintf("root-%02d", index)
		selection := OpenCodeStepFinishSelection{SessionID: rootID, RootSessionID: rootID, ActiveSince: 1}
		if index == 0 {
			selection.CursorRowID = 1
		}
		selections = append(selections, selection)
	}
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), selections, 1000, map[string]OpenCodePartCursor{"root-00": {RowID: 1}}, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != len(selections) {
		t.Fatalf("large mixed-cursor parts = %d, want one per selected root: %#v", len(window.Parts), window.Parts)
	}
	seenRoots := make(map[string]bool, len(window.Parts))
	for _, part := range window.Parts {
		seenRoots[part.RootSessionID] = true
	}
	if len(seenRoots) != len(selections) {
		t.Fatalf("large mixed-cursor roots = %#v, want all %d roots", seenRoots, len(selections))
	}
}

func TestOpenCodeReadStepFinishPartsForSelectionsRejectsDuplicateSessionIDs(t *testing.T) {
	readOnly := &OpenCodeDatabase{}
	for name, selections := range map[string][]OpenCodeStepFinishSelection{
		"exact duplicate": {
			{SessionID: "session-1", RootSessionID: "root-1", ActiveSince: 1},
			{SessionID: "session-1", RootSessionID: "root-1", ActiveSince: 1},
		},
		"conflicting root": {
			{SessionID: "session-1", RootSessionID: "root-1", ActiveSince: 1},
			{SessionID: "session-1", RootSessionID: "root-2", ActiveSince: 1},
		},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), selections, 1000, nil, MaxOpenCodePartReadLimit)
			if err == nil || !strings.Contains(err.Error(), "duplicate OpenCode step-finish selection") {
				t.Fatalf("duplicate selection error = %v", err)
			}
		})
	}
}

func TestOpenCodeReadStepFinishPartsForSelectionsRejectsMalformedSiblingRootOnly(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-malformed-sibling.db")
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root-a', '/repo-a', 'build', '{"providerID":"openai","id":"gpt"}', 1, 100, 0, NULL)`,
		`INSERT INTO session VALUES ('root-b', '/repo-b', 'build', '{"providerID":"openai","id":"gpt"}', 1, 100, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-a-good', 'root-a', 90, 90, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-b', 'root-b', 91, 91, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-a-good', 'msg-a-good', 'root-a', 90, 90, '{"type":"step-finish","tokens":{"input":2}}')`,
		`INSERT INTO part VALUES ('part-a-bad', 'msg-a-good', 'root-a', 91, 91, '{')`,
		`INSERT INTO part VALUES ('part-b', 'msg-b', 'root-b', 92, 92, '{"type":"step-finish","tokens":{"output":3}}')`,
	} {
		if _, err := database.Exec(statement); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		{SessionID: "root-a", RootSessionID: "root-a", ActiveSince: 1},
		{SessionID: "root-b", RootSessionID: "root-b", ActiveSince: 1},
	}, 1000, nil, MaxOpenCodePartReadLimit)
	if err == nil || !strings.Contains(err.Error(), "root-a") {
		t.Fatalf("malformed root error = %v", err)
	}
	if window.RootErrors["root-a"] != "malformed-transcript-json" {
		t.Fatalf("malformed root details = %#v", window.RootErrors)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-b" {
		t.Fatalf("valid sibling parts = %#v", window.Parts)
	}
	if _, exists := window.Cursors["root-a"]; exists {
		t.Fatalf("malformed root cursor advanced = %#v", window.Cursors)
	}
	if window.Cursors["root-b"].PartID != "part-b" {
		t.Fatalf("valid sibling cursor = %#v", window.Cursors)
	}
}

func TestOpenCodeInitialWindowValidatesNewRootAlongsideEstablishedRoot(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-mixed-root-counters.db")
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT, tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		`INSERT INTO session VALUES ('root-a', '/repo-a', 'build', '{"providerID":"openai","id":"gpt"}', 1, 100, 0, NULL, 3, 0, 0, 0, 0)`,
		`INSERT INTO session VALUES ('root-b', '/repo-b', 'build', '{"providerID":"openai","id":"gpt"}', 1, 100, 0, NULL, 10, 0, 0, 0, 0)`,
		`INSERT INTO message VALUES ('msg-a', 'root-a', 90, 90, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-b', 'root-b', 91, 91, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-a', 'msg-a', 'root-a', 90, 90, '{"type":"step-finish","tokens":{"input":3}}')`,
		`INSERT INTO part VALUES ('part-b', 'msg-b', 'root-b', 91, 91, '{"type":"step-finish","tokens":{"input":3}}')`,
	} {
		if _, err := database.Exec(statement); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	_, err = readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		// Root A is already established; its cursor must not suppress validation
		// for the newly discovered root B in the same query.
		{SessionID: "root-a", RootSessionID: "root-a", ActiveSince: 900, CursorRowID: 1},
		{SessionID: "root-b", RootSessionID: "root-b", ActiveSince: 1},
	}, 1000, map[string]OpenCodePartCursor{"root-a": {RowID: 1}}, MaxOpenCodePartReadLimit)
	if err == nil || !strings.Contains(err.Error(), "opencode-cumulative-anchor-mismatch") {
		t.Fatalf("new-root cumulative mismatch was not rejected: %v", err)
	}
}

func TestOpenCodeRootValidationKeepsValidSiblingRows(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-root-validation-isolated.db")
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT, tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		`INSERT INTO session VALUES ('root-a', '/repo-a', 'build', '{"providerID":"openai","id":"gpt"}', 1, 900, 0, NULL, 4, 0, 0, 0, 0)`,
		`INSERT INTO session VALUES ('root-b', '/repo-b', 'build', '{"providerID":"openai","id":"gpt"}', 1, 100, 0, NULL, 10, 0, 0, 0, 0)`,
		`INSERT INTO message VALUES ('msg-a-old', 'root-a', 90, 90, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-a-new', 'root-a', 900, 900, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-b', 'root-b', 91, 91, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-a-old', 'msg-a-old', 'root-a', 90, 90, '{"type":"step-finish","tokens":{"input":1}}')`,
		`INSERT INTO part VALUES ('part-a-new', 'msg-a-new', 'root-a', 900, 900, '{"type":"step-finish","tokens":{"input":4}}')`,
		`INSERT INTO part VALUES ('part-b', 'msg-b', 'root-b', 91, 91, '{"type":"step-finish","tokens":{"input":3}}')`,
	} {
		if _, err := database.Exec(statement); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{
		{SessionID: "root-a", RootSessionID: "root-a", ActiveSince: 800, CursorRowID: 1},
		{SessionID: "root-b", RootSessionID: "root-b", ActiveSince: 1},
	}, 1000, map[string]OpenCodePartCursor{"root-a": {RowID: 1}}, MaxOpenCodePartReadLimit)
	if err == nil || !strings.Contains(err.Error(), "root-b") {
		t.Fatalf("root-scoped validation error = %v", err)
	}
	if len(window.Parts) != 1 || window.Parts[0].PartID != "part-a-new" {
		t.Fatalf("valid sibling parts = %#v", window.Parts)
	}
	if window.Cursors["root-a"].PartID != "part-a-new" {
		t.Fatalf("valid sibling cursor = %#v", window.Cursors)
	}
	if _, exists := window.Cursors["root-b"]; exists {
		t.Fatalf("failed root cursor advanced = %#v", window.Cursors)
	}
	if window.RootErrors["root-b"] != "opencode-cumulative-anchor-mismatch" {
		t.Fatalf("root validation details = %#v", window.RootErrors)
	}
}

func TestOpenCodeReadStepFinishPartsForSelectionsCombinesActiveAndCursorRows(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-cursor-window.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 1000, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-old', 'root', 100, 100, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-active', 'root', 900, 900, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-cursor', 'root', 100, 100, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-old', 'msg-old', 'root', 100, 100, '{"type":"step-finish","tokens":{"input":1}}')`,
		`INSERT INTO part VALUES ('part-active', 'msg-active', 'root', 900, 900, '{"type":"step-finish","tokens":{"input":2}}')`,
		`INSERT INTO part VALUES ('part-cursor', 'msg-cursor', 'root', 100, 100, '{"type":"step-finish","tokens":{"input":3}}')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "root", RootSessionID: "root", ActiveSince: 800, CursorRowID: 1,
	}}, 1000, map[string]OpenCodePartCursor{"root": {RowID: 1}}, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Parts) != 2 {
		t.Fatalf("cursor window rows = %#v, want active and cursor rows exactly once", window.Parts)
	}
	if window.Parts[0].PartID != "part-active" || window.Parts[1].PartID != "part-cursor" {
		t.Fatalf("cursor window rows = %#v", window.Parts)
	}
}

func TestOpenCodeReadStepFinishPartsForSelectionsIncludesRevisedOldActiveRow(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-revised-old-row.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 1000, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-old-tied', 'root', 100, 100, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-old-tied', 'msg-old-tied', 'root', 100, 100, '{"type":"step-finish","tokens":{"input":1}}')`,
		`INSERT INTO message VALUES ('msg-old-lower', 'root', 100, 100, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-old-lower', 'msg-old-lower', 'root', 100, 100, '{"type":"step-finish","tokens":{"input":1}}')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	tx, err := db.Begin()
	if err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	for index := 0; index <= openCodeActivePartRows; index++ {
		messageID := fmt.Sprintf("msg-filler-%04d", index)
		partID := fmt.Sprintf("part-filler-%04d", index)
		if _, err := tx.Exec(`INSERT INTO message VALUES (?, 'root', 100, 100, '{"role":"assistant"}')`, messageID); err != nil {
			_ = tx.Rollback()
			_ = db.Close()
			t.Fatal(err)
		}
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, 'root', 100, 100, '{"type":"step-finish","tokens":{"input":1}}')`, partID, messageID); err != nil {
			_ = tx.Rollback()
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if _, err := db.Exec(`UPDATE part SET time_updated=100 WHERE id LIKE 'part-filler-%'`); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	for _, revision := range []struct {
		id      string
		updated int64
		input   int
	}{
		{id: "part-old-tied", updated: 900, input: 9},
		{id: "part-old-lower", updated: 899, input: 8},
	} {
		data := fmt.Sprintf(`{"type":"step-finish","tokens":{"input":%d}}`, revision.input)
		if _, err := db.Exec(`UPDATE part SET time_updated=?, data=? WHERE id=?`, revision.updated, data, revision.id); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	for index := 0; index < 3; index++ {
		messageID := fmt.Sprintf("msg-active-%d", index)
		partID := fmt.Sprintf("part-active-%d", index)
		if _, err := db.Exec(`INSERT INTO message VALUES (?, 'root', 900, 900, '{"role":"assistant"}')`, messageID); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
		if _, err := db.Exec(`INSERT INTO part VALUES (?, ?, 'root', 900, 900, '{"type":"step-finish","tokens":{"input":2}}')`, partID, messageID); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "root", RootSessionID: "root", ActiveSince: 800, CursorRowID: 1,
	}}, 1000, map[string]OpenCodePartCursor{"root": {RowID: 1}}, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	found := make(map[string]bool, 2)
	for _, part := range window.Parts {
		if part.PartID == "part-old-tied" && part.Tokens.Input == 9 {
			found[part.PartID] = true
		}
		if part.PartID == "part-old-lower" && part.Tokens.Input == 8 {
			found[part.PartID] = true
		}
	}
	if len(found) != 2 || len(window.Parts) > MaxOpenCodePartReadLimit {
		t.Fatalf("revised old active rows found=%v count=%d, want bounded result containing tied and lower updated parts", found, len(window.Parts))
	}
}

func TestOpenCodeRevisionOverflowKeepsNewestTiedRowsAndMarksPartial(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-revision-overflow.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 1000, 0, NULL)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	tx, err := db.Begin()
	if err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	revisionCount := MaxOpenCodePartReadLimit + 2
	for index := 0; index < revisionCount; index++ {
		messageID := fmt.Sprintf("msg-revision-%04d", index)
		partID := fmt.Sprintf("part-revision-%04d", index)
		if _, err := tx.Exec(`INSERT INTO message VALUES (?, 'root', 100, 100, '{"role":"assistant"}')`, messageID); err != nil {
			_ = tx.Rollback()
			_ = db.Close()
			t.Fatal(err)
		}
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, 'root', 100, 100, '{"type":"step-finish","tokens":{"input":1}}')`, partID, messageID); err != nil {
			_ = tx.Rollback()
			_ = db.Close()
			t.Fatal(err)
		}
	}
	for index := 0; index < openCodeActivePartRows; index++ {
		messageID := fmt.Sprintf("msg-filler-%04d", index)
		partID := fmt.Sprintf("part-filler-%04d", index)
		if _, err := tx.Exec(`INSERT INTO message VALUES (?, 'root', 100, 100, '{"role":"assistant"}')`, messageID); err != nil {
			_ = tx.Rollback()
			_ = db.Close()
			t.Fatal(err)
		}
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, 'root', 100, 100, '{"type":"step-finish","tokens":{"input":1}}')`, partID, messageID); err != nil {
			_ = tx.Rollback()
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if _, err := db.Exec(`UPDATE part SET time_updated=900, data='{"type":"step-finish","tokens":{"input":9}}' WHERE id LIKE 'part-revision-%'`); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	window, err := readOnly.ReadStepFinishPartsForSelections(context.Background(), []OpenCodeStepFinishSelection{{
		SessionID: "root", RootSessionID: "root", ActiveSince: 800, CursorRowID: 1,
	}}, 1000, map[string]OpenCodePartCursor{"root": {RowID: 1}}, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	foundNewestTie, foundOldestTie := false, false
	for _, part := range window.Parts {
		foundNewestTie = foundNewestTie || part.PartID == fmt.Sprintf("part-revision-%04d", revisionCount-1)
		foundOldestTie = foundOldestTie || part.PartID == "part-revision-0000"
	}
	if len(window.Parts) != MaxOpenCodePartReadLimit || !foundNewestTie || foundOldestTie || !window.TruncatedRoots["root"] {
		t.Fatalf("revision overflow result count=%d newest_tie=%v oldest_tie=%v truncated_roots=%v; want capped newest ties and a partial root", len(window.Parts), foundNewestTie, foundOldestTie, window.TruncatedRoots)
	}
}

func TestOpenCodeReadStepFinishPartsForSelectionsBoundsActualShapedHistory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-many-roots.db")
	writeOpenCodeManyRootHistory(t, path, 64, 5000)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	selections := make([]OpenCodeStepFinishSelection, 0, 64)
	for index := 0; index < 64; index++ {
		rootID := fmt.Sprintf("root-%02d", index)
		selections = append(selections, OpenCodeStepFinishSelection{SessionID: rootID, RootSessionID: rootID, ActiveSince: 1})
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	started := time.Now()
	window, err := db.ReadStepFinishPartsForSelections(ctx, selections, 10_000_001, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed >= 2*time.Second {
		t.Fatalf("bounded multi-root query took %s", elapsed)
	}
	if len(window.Parts) != len(selections) {
		t.Fatalf("bounded multi-root rows = %d, want one fresh row per root (%d)", len(window.Parts), len(selections))
	}
	freshRoots := make(map[string]bool, len(window.Parts))
	for _, part := range window.Parts {
		if strings.HasSuffix(part.PartID, "-fresh") {
			freshRoots[part.RootSessionID] = true
		}
	}
	if len(freshRoots) != len(selections) {
		t.Fatalf("bounded multi-root fresh roots = %d, want %d", len(freshRoots), len(selections))
	}
	if !window.Truncated || len(window.TruncatedRoots) != len(selections) {
		t.Fatalf("large multi-root window was not marked partial for every root: truncated=%v roots=%d want=%d", window.Truncated, len(window.TruncatedRoots), len(selections))
	}
}

func writeOpenCodeLargeHistory(t *testing.T, path string, children int) {
	t.Helper()
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC, id DESC)`,
		`CREATE INDEX message_session ON message(session_id, id)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 10000, 0, NULL)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	for index := 0; index < children; index++ {
		sessionID := fmt.Sprintf("child-%03d", index)
		if _, err := db.Exec(`INSERT INTO session VALUES (?, '/repo', 'build', '{"providerID":"openai","id":"gpt"}', ?, ?, 0, 'root')`, sessionID, index+1, index+1); err != nil {
			t.Fatal(err)
		}
		messageID := "msg-" + sessionID
		if _, err := db.Exec(`INSERT INTO message VALUES (?, ?, ?, ?, ?)`, messageID, sessionID, index+1, index+1, `{"role":"assistant"}`); err != nil {
			t.Fatal(err)
		}
		if _, err := db.Exec(`INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)`, "part-"+sessionID, messageID, sessionID, index+1, index+1, `{"type":"step-finish","tokens":{"input":1,"output":1,"cache":{"read":1}}}`); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := db.Exec(`INSERT INTO message VALUES ('msg-fresh', 'root', 10000, 10000, '{"role":"assistant"}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO part VALUES ('fresh', 'msg-fresh', 'root', 10000, 10000, '{"type":"step-finish","tokens":{"input":2,"output":3,"cache":{"read":4}},"cost":0}')`); err != nil {
		t.Fatal(err)
	}
}

func writeOpenCodeManyRootHistory(t *testing.T, path string, roots, partsPerRoot int) {
	t.Helper()
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, statement := range []string{
		`PRAGMA journal_mode=OFF`,
		`PRAGMA synchronous=OFF`,
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC, id DESC)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	tx, err := db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	for rootIndex := 0; rootIndex < roots; rootIndex++ {
		rootID := fmt.Sprintf("root-%02d", rootIndex)
		if _, err := tx.Exec(`INSERT INTO session VALUES (?, '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 1000000, 0, NULL)`, rootID); err != nil {
			t.Fatal(err)
		}
		for partIndex := 0; partIndex < partsPerRoot; partIndex++ {
			messageID := fmt.Sprintf("msg-%02d-%05d", rootIndex, partIndex)
			partID := fmt.Sprintf("part-%02d-%05d", rootIndex, partIndex)
			if _, err := tx.Exec(`INSERT INTO message VALUES (?, ?, ?, ?, ?)`, messageID, rootID, partIndex+1, partIndex+1, `{"role":"assistant"}`); err != nil {
				t.Fatal(err)
			}
			if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)`, partID, messageID, rootID, partIndex+1, partIndex+1, `{"type":"step-finish","tokens":{"input":1,"output":1,"cache":{"read":1}}}`); err != nil {
				t.Fatal(err)
			}
		}
		freshMessageID := fmt.Sprintf("msg-%02d-fresh", rootIndex)
		freshPartID := fmt.Sprintf("part-%02d-fresh", rootIndex)
		if _, err := tx.Exec(`INSERT INTO message VALUES (?, ?, ?, ?, ?)`, freshMessageID, rootID, 10_000_000, 10_000_000, `{"role":"assistant"}`); err != nil {
			t.Fatal(err)
		}
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)`, freshPartID, freshMessageID, rootID, 10_000_000, 10_000_000, `{"type":"step-finish","tokens":{"input":2,"output":3,"cache":{"read":4}}}`); err != nil {
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestOpenCodeReadOnlyReadsCopiedLiveRowShape(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-live-copy.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT, parent_id TEXT, slug TEXT NOT NULL, directory TEXT NOT NULL, path TEXT, title TEXT NOT NULL, version TEXT NOT NULL, share_url TEXT, summary_additions INTEGER, summary_deletions INTEGER, summary_files INTEGER, summary_diffs TEXT, metadata TEXT, cost REAL DEFAULT 0 NOT NULL, tokens_input INTEGER DEFAULT 0 NOT NULL, tokens_output INTEGER DEFAULT 0 NOT NULL, tokens_reasoning INTEGER DEFAULT 0 NOT NULL, tokens_cache_read INTEGER DEFAULT 0 NOT NULL, tokens_cache_write INTEGER DEFAULT 0 NOT NULL, revert TEXT, permission TEXT, agent TEXT, model TEXT, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, time_compacting INTEGER, time_archived INTEGER)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL)`,
		`INSERT INTO session (id, project_id, slug, directory, title, version, model, agent, time_created, time_updated) VALUES ('ses_fixture', 'global', 'synthetic-session', '/fixture/workspace/yo7222', 'synthetic OpenCode session', '1.18.30', '{"id":"switchyard/openai/gpt-5.6-luna","providerID":"inferencehub"}', 'build', 1789197166435, 1789246752375)`,
		`INSERT INTO message VALUES ('msg_fixture', 'ses_fixture', 1789246750000, 1789246752140, '{"parentID":"msg-parent","role":"assistant","mode":"build","agent":"build","path":{"cwd":"/fixture/workspace/yo7222","root":"/"},"cost":0,"tokens":{"input":8032,"output":135,"reasoning":0,"cache":{"write":0,"read":295233}},"modelID":"openai/openai/gpt-5.6-luna","providerID":"inferencehub","time":{"created":1789246750000,"completed":1789246752141},"finish":"stop"}')`,
		`INSERT INTO part VALUES ('prt_fixture', 'msg_fixture', 'ses_fixture', 1789246752141, 1789246752141, '{"snapshot":"665a7c375544594e6779bb518c0002fa629f2747","type":"step-finish","tokens":{"total":303400,"input":8032,"output":135,"reasoning":0,"cache":{"write":0,"read":295233}},"cost":0}')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	resolved, err := readOnly.ResolveSessionID(context.Background(), "/fixture/workspace/yo7222", "synthetic OpenCode session", 1789197150.86, 1789248000)
	if err != nil || resolved != "ses_fixture" {
		t.Fatalf("resolved copied live session = %q err=%v", resolved, err)
	}
	parts, err := readOnly.ReadStepFinishParts(context.Background(), resolved, 1789246750000, 1789246760000, MaxOpenCodePartReadLimit)
	if err != nil || len(parts) != 1 {
		t.Fatalf("copied live parts = %#v err=%v", parts, err)
	}
	if got := parts[0]; got.Provider != "inferencehub" || got.Model != "openai/openai/gpt-5.6-luna" || got.Tokens.Input != 8032 || got.Tokens.CacheRead != 295233 || got.CompletedAt != 1789246752141 {
		t.Fatalf("copied live decoded part = %+v", got)
	}
}

func TestOpenCodeReadSessionTreeIsBoundedAndCycleSafe(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-tree.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{}', 1, 2, 0, 'child')`,
		`INSERT INTO session VALUES ('child', '/repo', 'build', '{}', 1, 2, 0, 'root')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	tree, err := readOnly.ReadSessionTree(context.Background(), "root", 2)
	if err != nil || len(tree) != 2 || tree[0] != "root" || tree[1] != "child" {
		t.Fatalf("cycle-safe tree = %#v err=%v", tree, err)
	}
	limited, err := readOnly.ReadSessionTree(context.Background(), "root", 1)
	if err != nil || len(limited) != 1 || limited[0] != "root" {
		t.Fatalf("bounded root-only tree = %#v err=%v", limited, err)
	}
}

func TestOpenCodeReadSessionTreeKeepsNewestChildrenWithinBound(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-many-children.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{}', 1, 2000, 0, NULL)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	for index := 0; index < 173; index++ {
		id := fmt.Sprintf("child-%03d", index)
		if _, err := db.Exec(`INSERT INTO session VALUES (?, '/repo', 'build', '{}', ?, ?, 0, 'root')`, id, index+1, index+1); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	tree, err := readOnly.ReadSessionTree(context.Background(), "root", MaxOpenCodeSessionTree)
	if err != nil {
		t.Fatal(err)
	}
	if len(tree) != MaxOpenCodeSessionTree || tree[0] != "root" {
		t.Fatalf("bounded many-child tree = len %d tree %#v", len(tree), tree)
	}
	for index, id := range tree[1:] {
		want := fmt.Sprintf("child-%03d", 172-index)
		if id != want {
			t.Fatalf("newest child at index %d = %q, want %q", index, id, want)
		}
	}
	window, err := readOnly.ReadSessionTreeWindow(context.Background(), "root", MaxOpenCodeSessionTree)
	if err != nil {
		t.Fatal(err)
	}
	if window.Complete {
		t.Fatal("bounded 173-child tree reported complete coverage")
	}
}

func TestOpenCodeRootSessionIDFindsParentForResolvedChild(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-parent.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{}', 1, 2, 0, NULL)`,
		`INSERT INTO session VALUES ('child', '/repo', 'build', '{}', 1, 2, 0, 'root')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	readOnly, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	root, err := readOnly.RootSessionID(context.Background(), "child", 4)
	if err != nil || root != "root" {
		t.Fatalf("root for child = %q err=%v", root, err)
	}
	tree, err := readOnly.ReadSessionTree(context.Background(), root, 4)
	if err != nil || len(tree) != 2 || tree[0] != "root" || tree[1] != "child" {
		t.Fatalf("root tree = %#v err=%v", tree, err)
	}
}

func TestOpenCodeReadOnlyFiltersNonAssistantAndPreservesAllTokenDimensions(t *testing.T) {
	path := copyOpenCodeFixture(t)
	mutateSQLite(t, path, `INSERT INTO message VALUES ('msg-user', 'ses-1', 1200, 2600, '{"role":"user"}')`)
	mutateSQLite(t, path, `INSERT INTO part VALUES ('part-user', 'msg-user', 'ses-1', 1200, 2100, '{"type":"step-finish","tokens":{"total":99,"input":90,"output":1,"reasoning":2,"cache":{"read":3,"write":4}}}')`)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	parts, err := db.ReadStepFinishParts(context.Background(), "ses-1", 1000, 3000, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(parts) != 1 || parts[0].PartID != "part-1" {
		t.Fatalf("parts = %+v", parts)
	}
	got := parts[0].Tokens
	if got.Input != 3 || got.CacheRead != 2 || got.CacheWrite != 0 || got.Output != 5 || got.Reasoning != 0 || !got.Present["total"] {
		t.Fatalf("tokens = %+v", got)
	}
}

func TestOpenCodeReadOnlyOptionalTokenFieldsAreOmittedNotErrors(t *testing.T) {
	path := copyOpenCodeFixture(t)
	mutateSQLite(t, path, `UPDATE part SET data = '{"type":"step-finish","tokens":{"input":3,"output":5,"cache":{"read":2}}}' WHERE id = 'part-1'`)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	parts, err := db.ReadStepFinishParts(context.Background(), "ses-1", 1000, 3000, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(parts) != 1 || parts[0].Tokens.Present["reasoning"] || parts[0].Tokens.Present["cache_write"] || parts[0].Tokens.Present["total"] {
		t.Fatalf("presence = %#v", parts[0].Tokens.Present)
	}
}

func TestOpenCodeReadOnlyRejectsMissingTableOrColumn(t *testing.T) {
	for name, statement := range map[string]string{
		"missing table":  "DROP TABLE part",
		"missing column": "ALTER TABLE message RENAME TO message_old; CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER); DROP TABLE message_old",
	} {
		t.Run(name, func(t *testing.T) {
			path := copyOpenCodeFixture(t)
			mutateSQLite(t, path, statement)
			_, err := OpenOpenCodeReadOnly(context.Background(), path)
			var schemaErr *OpenCodeSchemaError
			if !errors.As(err, &schemaErr) {
				t.Fatalf("error = %v", err)
			}
		})
	}
}

func TestOpenCodeReadOnlyRejectsMalformedJSONAndIdentity(t *testing.T) {
	path := copyOpenCodeFixture(t)
	mutateSQLite(t, path, `UPDATE part SET data = 'not-json' WHERE id = 'part-1'`)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.ReadStepFinishParts(context.Background(), "ses-1", 0, 3000, 2); err == nil || !strings.Contains(err.Error(), "step-finish") {
		t.Fatalf("malformed row error = %v", err)
	}

	path = copyOpenCodeFixture(t)
	mutateSQLite(t, path, `UPDATE part SET id = '' WHERE id = 'part-1'`)
	db, err = OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.ReadStepFinishParts(context.Background(), "ses-1", 0, 3000, 2); err == nil {
		t.Fatal("ambiguous identity accepted")
	}
}

func TestOpenCodeReadOnlyHonorsBoundsAndCancellation(t *testing.T) {
	path := copyOpenCodeFixture(t)
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, test := range []struct {
		name       string
		start, end int64
		limit      int
	}{
		{"reversed time", 3, 2, 1},
		{"zero limit", 0, 2, 0},
		{"over limit", 0, 2, MaxOpenCodePartReadLimit + 1},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, err := db.ReadStepFinishParts(context.Background(), "ses-1", test.start, test.end, test.limit); err == nil {
				t.Fatal("invalid bounds accepted")
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := db.ReadStepFinishParts(ctx, "ses-1", 0, 3000, 1); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled query error = %v", err)
	}
}

func TestOpenCodeReadOnlyRejectsWrites(t *testing.T) {
	path := copyOpenCodeFixture(t)
	uri := openCodeReadOnlyURI(path)
	db, err := sql.Open("sqlite3", uri)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec("CREATE TABLE must_fail (id TEXT)"); err == nil {
		t.Fatal("read-only OpenCode URI accepted a write")
	}
}

func createOpenCodeFixture(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "opencode.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	statements := []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		`INSERT INTO session VALUES ('ses-1', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 900, 2500, 0)`,
		`INSERT INTO message VALUES ('msg-1', 'ses-1', 1000, 2500, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-1', 'msg-1', 'ses-1', 1100, 2000, '{"type":"step-finish","tokens":{"total":10,"input":3,"output":5,"reasoning":0,"cache":{"read":2,"write":0}}}')`,
	}
	for _, statement := range statements {
		if _, err := db.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	return path
}

func copyOpenCodeFixture(t *testing.T) string {
	t.Helper()
	source := createOpenCodeFixture(t)
	destination := filepath.Join(t.TempDir(), "opencode-copy.db")
	in, err := os.Open(source)
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	out, err := os.Create(destination)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		t.Fatal(err)
	}
	if err := out.Close(); err != nil {
		t.Fatal(err)
	}
	return destination
}

func mutateSQLite(t *testing.T, path, statement string) {
	t.Helper()
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec(statement); err != nil {
		t.Fatal(err)
	}
}
