package stats

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/url"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const MaxOpenCodePartReadLimit = 2048
const MaxOpenCodeSessionTree = 64

var openCodeRequiredColumns = map[string][]string{
	"session": {"id", "directory", "agent", "model", "time_created", "time_updated", "cost"},
	"message": {"id", "session_id", "time_created", "time_updated", "data"},
	"part":    {"id", "message_id", "session_id", "time_created", "time_updated", "data"},
}

type OpenCodeSchemaError struct{ Reason string }

func (e *OpenCodeSchemaError) Error() string { return "OpenCode database schema mismatch: " + e.Reason }

type OpenCodeTokenSnapshot struct {
	Total      int64
	Input      int64
	Output     int64
	Reasoning  int64
	CacheRead  int64
	CacheWrite int64
	// Present distinguishes an omitted optional dimension from an explicit zero.
	// OpenCode's Python reader uses nil for omitted token fields.
	Present map[string]bool
}

type OpenCodeStepFinishPart struct {
	PartID        string
	MessageID     string
	SessionID     string
	RootSessionID string
	CreatedAt     int64
	CompletedAt   int64
	Provider      string
	Model         string
	ModelEvidence string
	Tokens        OpenCodeTokenSnapshot
}

// OpenCodePartCursor is the stable SQL boundary for one session's newest
// step-finish rows. Part IDs break ties when OpenCode writes multiple rows at
// the same millisecond.
type OpenCodePartCursor struct {
	TimeUpdated int64
	PartID      string
	RowID       int64
}

type OpenCodeStepFinishWindow struct {
	Parts          []OpenCodeStepFinishPart
	Cursors        map[string]OpenCodePartCursor
	Truncated      bool
	TruncatedRoots map[string]bool
}

// OpenCodeStepFinishSelection associates one persisted session with the root
// claim that owns its usage atoms. ActiveSince is per root because a fresh
// cursor can use the tree's oldest creation time while an established cursor
// only needs the recent rewrite window.
type OpenCodeStepFinishSelection struct {
	SessionID     string `json:"session_id"`
	RootSessionID string `json:"root_session_id"`
	ActiveSince   int64  `json:"active_since"`
	CursorRowID   int64  `json:"cursor_rowid"`
}

type OpenCodeDatabase struct {
	reader       ReadOnlyDB
	close        func() error
	parentColumn string
}

type OpenCodeSessionTreeWindow struct {
	Sessions      []string
	Complete      bool
	NewestUpdated int64
	OldestCreated int64
}

const (
	openCodeSessionMaxAge  = 24 * time.Hour
	openCodeStartSkew      = 5 * time.Minute
	openCodeFutureSkew     = 5 * time.Minute
	openCodeActiveWindow   = 5 * time.Minute
	openCodeActivePartRows = MaxOpenCodePartReadLimit * 2
	maxOpenCodeSessions    = 64
	maxOpenCodeSelections  = maxAgentTokenSources * MaxOpenCodeSessionTree
)

func (d *OpenCodeDatabase) Close() error {
	if d == nil || d.close == nil {
		return nil
	}
	return d.close()
}

// OpenOpenCodeReadOnly opens only the external OpenCode database. It never
// creates, migrates, or writes that database.
func OpenOpenCodeReadOnly(ctx context.Context, path string, openers ...OpenFunc) (*OpenCodeDatabase, error) {
	open := openOpenCodeSQLiteReadOnly
	if len(openers) > 0 && openers[0] != nil {
		open = openers[0]
	}
	db, err := open(openCodeReadOnlyURI(path))
	if err != nil {
		return nil, fmt.Errorf("open OpenCode database read-only: %w", err)
	}
	closeWithError := func(err error) (*OpenCodeDatabase, error) {
		_ = db.Close()
		return nil, err
	}
	if err := db.QueryOnly(ctx); err != nil {
		return closeWithError(fmt.Errorf("enable OpenCode SQLite query-only mode: %w", err))
	}
	if err := validateOpenCodeSchema(ctx, db); err != nil {
		return closeWithError(err)
	}
	parentColumn, err := openCodeParentColumn(ctx, db)
	if err != nil {
		return closeWithError(err)
	}
	return &OpenCodeDatabase{reader: db, close: db.Close, parentColumn: parentColumn}, nil
}

func openCodeReadOnlyURI(path string) string {
	abs, _ := filepath.Abs(path)
	return "file:" + url.PathEscape(abs) + "?mode=ro&_busy_timeout=1000"
}

func openOpenCodeSQLiteReadOnly(uri string) (ReadOnlyDB, error) { return openSQLiteReadOnly(uri) }

func validateOpenCodeSchema(ctx context.Context, db ReadOnlyDB) error {
	for table, required := range openCodeRequiredColumns {
		rows, err := db.Rows(ctx, "PRAGMA table_info("+table+")")
		if err != nil {
			return &OpenCodeSchemaError{fmt.Sprintf("cannot inspect table %q", table)}
		}
		columns := make(map[string]bool, len(rows))
		for _, row := range rows {
			if len(row) < 2 {
				return &OpenCodeSchemaError{fmt.Sprintf("table %q has malformed column metadata", table)}
			}
			name, ok := row[1].(string)
			if !ok || name == "" {
				return &OpenCodeSchemaError{fmt.Sprintf("table %q has malformed column metadata", table)}
			}
			columns[name] = true
		}
		for _, column := range required {
			if !columns[column] {
				return &OpenCodeSchemaError{fmt.Sprintf("table %q is missing column %q", table, column)}
			}
		}
	}
	return nil
}

func openCodeParentColumn(ctx context.Context, db ReadOnlyDB) (string, error) {
	rows, err := db.Rows(ctx, "PRAGMA table_info(session)")
	if err != nil {
		return "", &OpenCodeSchemaError{"cannot inspect session parent column"}
	}
	columns := make(map[string]bool, len(rows))
	for _, row := range rows {
		if len(row) < 2 {
			return "", &OpenCodeSchemaError{"session has malformed column metadata"}
		}
		name, ok := openCodeString(row[1])
		if !ok || name == "" {
			return "", &OpenCodeSchemaError{"session has malformed column metadata"}
		}
		columns[name] = true
	}
	for _, candidate := range []string{"parent_id", "parent_session_id", "parentID"} {
		if columns[candidate] {
			return candidate, nil
		}
	}
	return "", nil
}

// ReadSessionTree returns one root and a bounded newest-first slice of its
// persisted descendants. The child queries are individually bounded so a
// historical fan-out cannot make the whole root unavailable or force an
// unbounded recursive scan. Call ReadSessionTreeWindow when the caller needs
// to distinguish a complete tree from a bounded partial window. Older
// OpenCode schemas without a parent column retain the root-only behavior.
func (d *OpenCodeDatabase) ReadSessionTree(ctx context.Context, rootID string, limit int) ([]string, error) {
	window, err := d.ReadCurrentSessionDescendantFrontier(ctx, rootID, limit)
	if err != nil {
		return nil, err
	}
	return window.Sessions, nil
}

// ReadSessionTreeWindow returns a bounded newest-first session window and
// whether that window covers the complete persisted tree. A full result can
// still contain only the root for schemas that have no parent column.
func (d *OpenCodeDatabase) ReadSessionTreeWindow(ctx context.Context, rootID string, limit int) (OpenCodeSessionTreeWindow, error) {
	return d.readSessionTreeWindow(ctx, rootID, limit)
}

// ReadCurrentSessionDescendantFrontier rebuilds the bounded descendant
// frontier from the current session table. Callers that build a part-selection
// batch must use this entry point for every collection; it deliberately owns
// no cache or cursor so a child created after the previous collection can be
// selected even when older siblings fill the bound.
func (d *OpenCodeDatabase) ReadCurrentSessionDescendantFrontier(ctx context.Context, rootID string, limit int) (OpenCodeSessionTreeWindow, error) {
	return d.readSessionTreeWindow(ctx, rootID, limit)
}

func (d *OpenCodeDatabase) readSessionTreeWindow(ctx context.Context, rootID string, limit int) (OpenCodeSessionTreeWindow, error) {
	if strings.TrimSpace(rootID) == "" {
		return OpenCodeSessionTreeWindow{}, fmt.Errorf("OpenCode root session ID is required")
	}
	if limit <= 0 || limit > MaxOpenCodeSessionTree {
		return OpenCodeSessionTreeWindow{}, fmt.Errorf("invalid OpenCode session tree limit %d", limit)
	}
	if d.parentColumn == "" {
		rows, err := d.reader.Rows(ctx, `SELECT id FROM session WHERE id = ? LIMIT 2`, rootID)
		if err != nil {
			return OpenCodeSessionTreeWindow{}, fmt.Errorf("read OpenCode root session: %w", err)
		}
		if len(rows) == 0 {
			return OpenCodeSessionTreeWindow{}, fmt.Errorf("OpenCode root session not found")
		}
		if len(rows) > 1 {
			return OpenCodeSessionTreeWindow{}, fmt.Errorf("OpenCode session tree root is ambiguous")
		}
		newest, oldest, err := d.sessionTreeBounds(ctx, []string{rootID})
		if err != nil {
			return OpenCodeSessionTreeWindow{}, err
		}
		return OpenCodeSessionTreeWindow{Sessions: []string{rootID}, Complete: true, NewestUpdated: newest, OldestCreated: oldest}, nil
	}
	rootRows, err := d.reader.Rows(ctx, `SELECT id FROM session WHERE id = ? LIMIT 2`, rootID)
	if err != nil {
		return OpenCodeSessionTreeWindow{}, fmt.Errorf("read OpenCode root session: %w", err)
	}
	if len(rootRows) == 0 {
		return OpenCodeSessionTreeWindow{}, fmt.Errorf("OpenCode root session not found")
	}
	if len(rootRows) > 1 {
		return OpenCodeSessionTreeWindow{}, fmt.Errorf("OpenCode session tree root is ambiguous")
	}
	if len(rootRows[0]) != 1 {
		return OpenCodeSessionTreeWindow{}, fmt.Errorf("OpenCode root session row has %d columns", len(rootRows[0]))
	}
	root, ok := openCodeString(rootRows[0][0])
	if !ok || strings.TrimSpace(root) == "" || root != rootID {
		return OpenCodeSessionTreeWindow{}, fmt.Errorf("malformed OpenCode session tree root identity")
	}

	result := []string{rootID}
	seen := map[string]bool{rootID: true}
	frontier := []string{rootID}
	truncated := false
	for len(result) < limit && len(frontier) > 0 {
		remaining := limit - len(result)
		placeholders := make([]string, 0, len(frontier)+len(seen))
		args := make([]any, 0, len(frontier)+len(seen)+1)
		for _, parent := range frontier {
			placeholders = append(placeholders, "?")
			args = append(args, parent)
		}
		for id := range seen {
			placeholders = append(placeholders, "?")
			args = append(args, id)
		}
		parentArgs := strings.TrimSuffix(strings.Repeat("?,", len(frontier)), ",")
		excludedArgs := strings.TrimSuffix(strings.Repeat("?,", len(seen)), ",")
		query := fmt.Sprintf(`SELECT id, %s, time_updated FROM session
WHERE %s IN (%s) AND id NOT IN (%s)
 ORDER BY time_updated DESC, id DESC LIMIT ?`,
			quoteOpenCodeIdentifier(d.parentColumn), quoteOpenCodeIdentifier(d.parentColumn), parentArgs, excludedArgs)
		// Read one sentinel row so a result that fills the remaining bound is
		// distinguishable from a complete tree ending at that row.
		args = append(args, remaining+1)
		rows, err := d.reader.Rows(ctx, query, args...)
		if err != nil {
			return OpenCodeSessionTreeWindow{}, fmt.Errorf("read OpenCode session descendants: %w", err)
		}
		if len(rows) > remaining {
			truncated = true
			rows = rows[:remaining]
		}
		next := make([]string, 0, len(rows))
		selected := make(map[string]bool, len(rows))
		for index, row := range rows {
			if len(row) != 3 {
				return OpenCodeSessionTreeWindow{}, fmt.Errorf("OpenCode session tree row %d has %d columns", index, len(row))
			}
			id, idOK := openCodeString(row[0])
			parent, parentOK := openCodeString(row[1])
			if !idOK || strings.TrimSpace(id) == "" || !parentOK || strings.TrimSpace(parent) == "" {
				return OpenCodeSessionTreeWindow{}, fmt.Errorf("malformed OpenCode session tree identity")
			}
			if !seen[parent] || selected[id] {
				return OpenCodeSessionTreeWindow{}, fmt.Errorf("ambiguous or duplicate OpenCode session tree identity")
			}
			if _, err := openCodeInteger(row[2]); err != nil {
				return OpenCodeSessionTreeWindow{}, fmt.Errorf("malformed OpenCode session tree timestamp")
			}
			selected[id] = true
			seen[id] = true
			result = append(result, id)
			next = append(next, id)
		}
		frontier = next
	}
	if !truncated && len(result) >= limit && len(frontier) > 0 {
		// Filling the bound is not by itself evidence of truncation: the last
		// selected frontier may simply have no children. Check that boundary
		// without expanding the bounded window.
		placeholders := make([]string, 0, len(frontier)+len(seen))
		args := make([]any, 0, len(frontier)+len(seen)+1)
		for _, parent := range frontier {
			placeholders = append(placeholders, "?")
			args = append(args, parent)
		}
		for id := range seen {
			placeholders = append(placeholders, "?")
			args = append(args, id)
		}
		parentArgs := strings.TrimSuffix(strings.Repeat("?,", len(frontier)), ",")
		excludedArgs := strings.TrimSuffix(strings.Repeat("?,", len(seen)), ",")
		query := fmt.Sprintf(`SELECT id FROM session
WHERE %s IN (%s) AND id NOT IN (%s) LIMIT 1`,
			quoteOpenCodeIdentifier(d.parentColumn), parentArgs, excludedArgs)
		rows, err := d.reader.Rows(ctx, query, args...)
		if err != nil {
			return OpenCodeSessionTreeWindow{}, fmt.Errorf("check OpenCode session tree boundary: %w", err)
		}
		truncated = len(rows) > 0
	}
	newest, oldest, err := d.sessionTreeBounds(ctx, result)
	if err != nil {
		return OpenCodeSessionTreeWindow{}, err
	}
	return OpenCodeSessionTreeWindow{Sessions: result, Complete: !truncated, NewestUpdated: newest, OldestCreated: oldest}, nil
}

func (d *OpenCodeDatabase) sessionTreeBounds(ctx context.Context, sessionIDs []string) (int64, int64, error) {
	if len(sessionIDs) == 0 {
		return 0, 0, fmt.Errorf("OpenCode session tree is empty")
	}
	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(sessionIDs)), ",")
	args := make([]any, len(sessionIDs))
	for index, sessionID := range sessionIDs {
		args[index] = sessionID
	}
	rows, err := d.reader.Rows(ctx, fmt.Sprintf(`SELECT MIN(time_created), MAX(time_updated) FROM session WHERE id IN (%s)`, placeholders), args...)
	if err != nil {
		return 0, 0, fmt.Errorf("read OpenCode session tree bounds: %w", err)
	}
	if len(rows) != 1 || len(rows[0]) != 2 {
		return 0, 0, fmt.Errorf("malformed OpenCode session tree bounds")
	}
	oldest, err := openCodeInteger(rows[0][0])
	if err != nil {
		return 0, 0, fmt.Errorf("malformed OpenCode session creation time")
	}
	newest, err := openCodeInteger(rows[0][1])
	if err != nil {
		return 0, 0, fmt.Errorf("malformed OpenCode session update time")
	}
	return newest, oldest, nil
}

// RootSessionID normalizes an explicitly resolved session to the persisted
// root before process claims are deduplicated.
func (d *OpenCodeDatabase) RootSessionID(ctx context.Context, sessionID string, limit int) (string, error) {
	if strings.TrimSpace(sessionID) == "" {
		return "", fmt.Errorf("OpenCode root session ID is required")
	}
	if limit <= 0 || limit > MaxOpenCodeSessionTree {
		return "", fmt.Errorf("invalid OpenCode session ancestry limit %d", limit)
	}
	if d.parentColumn == "" {
		rows, err := d.reader.Rows(ctx, `SELECT id FROM session WHERE id = ? LIMIT 1`, sessionID)
		if err != nil {
			return "", fmt.Errorf("read OpenCode root session: %w", err)
		}
		if len(rows) != 1 {
			return "", fmt.Errorf("OpenCode root session not found")
		}
		return sessionID, nil
	}
	return d.rootSessionID(ctx, sessionID, limit)
}

func (d *OpenCodeDatabase) rootSessionID(ctx context.Context, sessionID string, limit int) (string, error) {
	query := fmt.Sprintf(`WITH RECURSIVE ancestors(id, parent_id, depth, path) AS (
 SELECT id, %s, 1, ',' || id || ',' FROM session WHERE id = ?
 UNION ALL
 SELECT parent.id, parent.%s, child.depth + 1, child.path || parent.id || ','
 FROM session AS parent JOIN ancestors AS child ON parent.id = child.parent_id
 WHERE child.depth < ? AND instr(child.path, ',' || parent.id || ',') = 0
)
SELECT id, parent_id, depth FROM ancestors ORDER BY depth DESC LIMIT ?`,
		quoteOpenCodeIdentifier(d.parentColumn), quoteOpenCodeIdentifier(d.parentColumn))
	rows, err := d.reader.Rows(ctx, query, sessionID, limit+1, limit+1)
	if err != nil {
		return "", fmt.Errorf("read OpenCode session ancestry: %w", err)
	}
	if len(rows) == 0 {
		return "", fmt.Errorf("OpenCode root session not found")
	}
	if len(rows) > limit {
		return "", fmt.Errorf("OpenCode session ancestry exceeds limit %d", limit)
	}
	row := rows[0]
	if len(row) != 3 {
		return "", fmt.Errorf("malformed OpenCode session ancestry row")
	}
	root, ok := openCodeString(row[0])
	if !ok || strings.TrimSpace(root) == "" {
		return "", fmt.Errorf("malformed OpenCode session ancestry identity")
	}
	if parent, hasParent := openCodeString(row[1]); hasParent && strings.TrimSpace(parent) != "" {
		return "", fmt.Errorf("ambiguous or cyclic OpenCode session ancestry")
	}
	return root, nil
}

func quoteOpenCodeIdentifier(value string) string {
	return `"` + strings.ReplaceAll(value, `"`, `""`) + `"`
}

// ReadStepFinishParts returns assistant step-finish parts for one exact session
// with start <= time_updated < end. The query is bounded before decoding rows.
func (d *OpenCodeDatabase) ReadStepFinishParts(ctx context.Context, sessionID string, start, end int64, limit int) ([]OpenCodeStepFinishPart, error) {
	if strings.TrimSpace(sessionID) == "" {
		return nil, fmt.Errorf("OpenCode session ID is required")
	}
	if start < 0 || end <= start {
		return nil, fmt.Errorf("invalid OpenCode time range")
	}
	if limit <= 0 || limit > MaxOpenCodePartReadLimit {
		return nil, fmt.Errorf("invalid OpenCode part limit %d", limit)
	}
	malformed, err := d.reader.Rows(ctx, `SELECT p.data FROM part AS p
WHERE p.session_id = ? AND p.time_updated >= ? AND p.time_updated < ? AND json_valid(p.data) = 0
LIMIT 1`, sessionID, start, end)
	if err != nil {
		return nil, fmt.Errorf("check OpenCode part JSON: %w", err)
	}
	if len(malformed) != 0 {
		return nil, fmt.Errorf("OpenCode step-finish row has malformed JSON")
	}
	rows, err := d.reader.Rows(ctx, `SELECT p.id, p.message_id, p.session_id, p.time_created, p.time_updated,
 p.data, m.data, s.model
 FROM part AS p JOIN message AS m ON m.id = p.message_id AND m.session_id = p.session_id
JOIN session AS s ON s.id = p.session_id
WHERE p.session_id = ? AND p.time_updated >= ? AND p.time_updated < ?
 AND json_extract(p.data, '$.type') = 'step-finish'
 AND json_extract(m.data, '$.role') = 'assistant'
ORDER BY p.time_updated ASC, p.id ASC LIMIT ?`, sessionID, start, end, limit+1)
	if err != nil {
		return nil, fmt.Errorf("read OpenCode step-finish parts: %w", err)
	}
	if len(rows) > limit {
		return nil, fmt.Errorf("OpenCode step-finish result exceeds limit %d", limit)
	}
	result := make([]OpenCodeStepFinishPart, 0, len(rows))
	for i, row := range rows {
		item, err := decodeOpenCodeStepFinish(row, sessionID)
		if err != nil {
			return nil, fmt.Errorf("OpenCode step-finish row %d: %w", i, err)
		}
		result = append(result, item)
	}
	return result, nil
}

// ReadStepFinishPartsWindow reads one bounded newest-first slice for an entire
// root tree. The active window is deliberately re-read even after a cursor so
// a rewritten recent part is visible to revision checking. Rows older than the
// per-session cursor are not scanned unless they are in that active window.
func (d *OpenCodeDatabase) ReadStepFinishPartsWindow(ctx context.Context, sessionIDs []string, start, end, activeSince int64, cursors map[string]OpenCodePartCursor, limit int) (OpenCodeStepFinishWindow, error) {
	if len(sessionIDs) == 0 || len(sessionIDs) > MaxOpenCodeSessionTree {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("invalid OpenCode session tree size %d", len(sessionIDs))
	}
	if start < 0 || end <= start || activeSince < 0 {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("invalid OpenCode time range")
	}
	if limit <= 0 || limit > MaxOpenCodePartReadLimit {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("invalid OpenCode part limit %d", limit)
	}
	selections := make([]OpenCodeStepFinishSelection, 0, len(sessionIDs))
	for _, sessionID := range sessionIDs {
		cursor := cursors[sessionID]
		selections = append(selections, OpenCodeStepFinishSelection{SessionID: sessionID, RootSessionID: sessionIDs[0], ActiveSince: activeSince, CursorRowID: cursor.RowID})
	}
	return d.ReadStepFinishPartsForSelections(ctx, selections, end, cursors, limit)
}

// ReadStepFinishPartsForSelections reads one bounded newest-first slice for all
// selected trees. The selection list is encoded as one JSON argument and
// joined through json_each, so a collection with many roots performs one
// bounded SQLite query rather than one large scan per root. One per-root
// sentinel preserves the old fail-closed partial-history behavior without
// allowing a busy root to hide fresh rows from another root.
func (d *OpenCodeDatabase) ReadStepFinishPartsForSelections(ctx context.Context, selections []OpenCodeStepFinishSelection, end int64, cursors map[string]OpenCodePartCursor, limit int) (OpenCodeStepFinishWindow, error) {
	if len(selections) == 0 || len(selections) > maxOpenCodeSelections {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("invalid OpenCode step-finish selection size %d", len(selections))
	}
	if end <= 0 {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("invalid OpenCode step-finish end time")
	}
	if limit <= 0 || limit > MaxOpenCodePartReadLimit {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("invalid OpenCode part limit %d", limit)
	}
	selectedSessions := make(map[string]bool, len(selections))
	selectedRoots := make(map[string]bool, len(selections))
	for _, selection := range selections {
		if strings.TrimSpace(selection.SessionID) == "" || strings.TrimSpace(selection.RootSessionID) == "" || selection.ActiveSince < 0 || selection.CursorRowID < 0 {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("malformed OpenCode step-finish selection")
		}
		selectedSessions[selection.SessionID] = true
		selectedRoots[selection.RootSessionID] = true
	}
	selectionJSON, err := json.Marshal(selections)
	if err != nil {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("encode OpenCode step-finish selections: %w", err)
	}
	query := `WITH
revision(max_rowid, row_floor) AS (
	SELECT COALESCE(MAX(rowid), 0),
	       CASE WHEN COALESCE(MAX(rowid), 0) > ? THEN COALESCE(MAX(rowid), 0) - ? ELSE 0 END
	FROM part
),
selected(session_id, root_session_id, active_since, cursor_rowid) AS (
  SELECT json_extract(value, '$.session_id'),
         json_extract(value, '$.root_session_id'),
         json_extract(value, '$.active_since'),
         COALESCE(json_extract(value, '$.cursor_rowid'), 0)
  FROM json_each(?)
),
candidates AS (
  SELECT p.rowid, p.id, p.message_id, p.session_id, p.time_created, p.time_updated,
         p.data AS part_data, m.data AS message_data, s.model, selected.root_session_id
  FROM part AS p
  JOIN selected ON selected.session_id = p.session_id
  CROSS JOIN revision
  JOIN message AS m ON m.id = p.message_id AND m.session_id = p.session_id
  JOIN session AS s ON s.id = p.session_id
  WHERE p.time_updated < ?
    AND p.rowid > revision.row_floor
    AND p.time_updated >= selected.active_since
    AND json_valid(p.data) = 1 AND json_extract(p.data, '$.type') = 'step-finish'
    AND json_valid(m.data) = 1 AND json_extract(m.data, '$.role') = 'assistant'
  UNION
  SELECT p.rowid, p.id, p.message_id, p.session_id, p.time_created, p.time_updated,
         p.data AS part_data, m.data AS message_data, s.model, selected.root_session_id
  FROM part AS p
  JOIN selected ON selected.session_id = p.session_id
  CROSS JOIN revision
  JOIN message AS m ON m.id = p.message_id AND m.session_id = p.session_id
  JOIN session AS s ON s.id = p.session_id
  WHERE selected.cursor_rowid > 0
    AND p.time_updated < ?
    AND p.rowid > CASE WHEN selected.cursor_rowid > revision.row_floor
                       THEN selected.cursor_rowid ELSE revision.row_floor END
    AND json_valid(p.data) = 1 AND json_extract(p.data, '$.type') = 'step-finish'
    AND json_valid(m.data) = 1 AND json_extract(m.data, '$.role') = 'assistant'
),
ranked AS (
  SELECT candidates.*, ROW_NUMBER() OVER (ORDER BY rowid DESC) AS global_rank,
         ROW_NUMBER() OVER (PARTITION BY root_session_id ORDER BY rowid DESC) AS root_rank
  FROM candidates
),
bounded AS (
  SELECT * FROM ranked WHERE global_rank <= ? OR root_rank = 1 OR root_rank = ?
)
SELECT rowid, id, message_id, session_id, time_created, time_updated,
	       part_data, message_data, model, root_session_id, global_rank, root_rank
FROM bounded`
	// The repeated row-count arguments keep row-floor computation inside the
	// same statement as the bounded read. This avoids a separate full-table
	// revision query while retaining the existing recent-row rewrite guard.
	args := []any{openCodeActivePartRows, openCodeActivePartRows, string(selectionJSON), end, end, limit, limit + 1}
	rows, err := d.reader.Rows(ctx, query, args...)
	if err != nil {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("read OpenCode step-finish selection window: %w", err)
	}
	sort.Slice(rows, func(left, right int) bool {
		leftID, _ := openCodeInteger(rows[left][0])
		rightID, _ := openCodeInteger(rows[right][0])
		return leftID > rightID
	})
	truncated := false
	result := make([]OpenCodeStepFinishPart, 0, len(rows))
	nextCursors := make(map[string]OpenCodePartCursor, len(cursors))
	for sessionID, cursor := range cursors {
		nextCursors[sessionID] = cursor
	}
	truncatedRoots := make(map[string]bool)
	for index := range rows {
		if len(rows[index]) != 12 {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("OpenCode step-finish row %d has %d columns", index, len(rows[index]))
		}
		root, ok := openCodeString(rows[index][9])
		if !ok || !selectedRoots[root] {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("missing or ambiguous OpenCode root identity")
		}
		globalRank, globalRankErr := openCodeInteger(rows[index][10])
		rootRank, rootRankErr := openCodeInteger(rows[index][11])
		if globalRankErr != nil || rootRankErr != nil || globalRank <= 0 || rootRank <= 0 {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("malformed OpenCode step-finish rank")
		}
		if globalRank > int64(limit) || rootRank == int64(limit+1) {
			truncatedRoots[root] = true
		}
	}
	truncated = len(truncatedRoots) > 0
	for index := len(rows) - 1; index >= 0; index-- {
		row := rows[index]
		if len(row) != 12 {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("OpenCode step-finish row %d has %d columns", index, len(row))
		}
		globalRank, _ := openCodeInteger(row[10])
		if globalRank > int64(limit) {
			continue
		}
		rowID, rowIDErr := openCodeInteger(row[0])
		if rowIDErr != nil || rowID <= 0 {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("malformed OpenCode part rowid")
		}
		sessionID, ok := openCodeString(row[3])
		if !ok || !selectedSessions[sessionID] {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("missing or ambiguous OpenCode session identity")
		}
		rootSessionID, rootOK := openCodeString(row[9])
		if !rootOK || !selectedRoots[rootSessionID] {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("missing or ambiguous OpenCode root identity")
		}
		part, decodeErr := decodeOpenCodeStepFinish(row[1:9], sessionID)
		if decodeErr != nil {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("OpenCode step-finish row %d: %w", index, decodeErr)
		}
		part.RootSessionID = rootSessionID
		result = append(result, part)
		current, exists := nextCursors[sessionID]
		if !exists || rowID > current.RowID {
			nextCursors[sessionID] = OpenCodePartCursor{TimeUpdated: part.CompletedAt, PartID: part.PartID, RowID: rowID}
		}
	}
	return OpenCodeStepFinishWindow{Parts: result, Cursors: nextCursors, Truncated: truncated, TruncatedRoots: truncatedRoots}, nil
}

// ResolveSessionID maps a native OpenCode process to its persisted session when
// argv does not carry --session. OpenCode's normal TUI invocation has no such
// argument, so directory and terminal title are the only available identity
// boundaries. Ambiguous matches fail closed instead of attributing a process to
// the newest unrelated session.
func (d *OpenCodeDatabase) ResolveSessionID(ctx context.Context, directory, title string, startedAt, now float64) (string, error) {
	if strings.TrimSpace(directory) == "" {
		return "", fmt.Errorf("session-identity-unavailable")
	}
	if now <= 0 {
		now = float64(time.Now().UnixNano()) / 1e9
	}
	if !finite(now) || (startedAt != 0 && !finite(startedAt)) {
		return "", fmt.Errorf("invalid-session-time")
	}
	canonical := canonicalDirectory(directory)
	lower := now - openCodeSessionMaxAge.Seconds()
	if startedAt > 0 && startedAt-openCodeStartSkew.Seconds() > lower {
		lower = startedAt - openCodeStartSkew.Seconds()
	}
	upper := now + openCodeFutureSkew.Seconds()
	if strings.TrimSpace(title) != "" {
		if id, err := d.resolveSessionTitle(ctx, canonical, title, lower, upper); err == nil && id != "" {
			return id, nil
		}
	}
	rows, err := d.reader.Rows(ctx, `SELECT id, directory, time_created, time_updated
 FROM session WHERE directory IN (?, ?) AND time_updated >= ? AND time_updated <= ?
 ORDER BY time_updated DESC, id DESC LIMIT ?`, directory, canonical, int64(lower*1000), int64(upper*1000), maxOpenCodeSessions+1)
	if err != nil {
		return "", fmt.Errorf("resolve OpenCode session: %w", err)
	}
	if len(rows) > maxOpenCodeSessions {
		return "", fmt.Errorf("ambiguous-session-identity")
	}
	ids := make([]string, 0, len(rows))
	for _, row := range rows {
		if len(row) != 4 {
			return "", fmt.Errorf("malformed-session-row")
		}
		id, ok := openCodeString(row[0])
		path, pathOK := openCodeString(row[1])
		if !ok || !pathOK || id == "" || canonicalDirectory(path) != canonical {
			continue
		}
		ids = append(ids, id)
	}
	if len(ids) != 1 {
		if len(ids) > 1 {
			return "", fmt.Errorf("ambiguous-session-identity")
		}
		return "", fmt.Errorf("session-identity-unavailable")
	}
	return ids[0], nil
}

func (d *OpenCodeDatabase) resolveSessionTitle(ctx context.Context, directory, title string, lower, upper float64) (string, error) {
	columns, err := d.reader.Rows(ctx, `PRAGMA table_info(session)`)
	if err != nil {
		return "", err
	}
	hasTitle := false
	for _, row := range columns {
		if len(row) > 1 {
			if name, ok := openCodeString(row[1]); ok && name == "title" {
				hasTitle = true
			}
		}
	}
	if !hasTitle {
		return "", nil
	}
	visible, truncated := openCodeVisibleTitle(title)
	if visible == "" {
		return "", nil
	}
	// A live TUI can keep a session open across the 24-hour recency bound.
	// Its directory plus visible title is still a valid identity boundary, so
	// evaluate that boundary before falling back to the bounded recency query.
	rows, err := d.reader.Rows(ctx, `SELECT id, directory, title, time_created, time_updated FROM session
	WHERE directory IN (?, ?) AND time_updated >= ? AND time_updated <= ?
	ORDER BY time_updated DESC, id DESC LIMIT ?`, directory, canonicalDirectory(directory),
		int64(lower*1000), int64(upper*1000), maxOpenCodeSessions+1)
	if err != nil {
		return "", err
	}
	matches := []string{}
	for _, row := range rows {
		if len(row) != 5 {
			return "", fmt.Errorf("malformed-session-title-row")
		}
		id, idOK := openCodeString(row[0])
		path, pathOK := openCodeString(row[1])
		candidate, titleOK := openCodeString(row[2])
		candidate = openCodeTitleValue(candidate)
		if idOK && pathOK && titleOK && canonicalDirectory(path) == directory &&
			((truncated && strings.HasPrefix(candidate, visible)) || (!truncated && candidate == visible)) {
			matches = append(matches, id)
		}
	}
	if len(matches) == 1 {
		return matches[0], nil
	}
	return "", nil
}

func openCodeVisibleTitle(title string) (string, bool) {
	visible := openCodeTitleValue(title)
	truncated := strings.HasSuffix(visible, "...") || strings.HasSuffix(visible, "…")
	if truncated {
		if strings.HasSuffix(visible, "...") {
			visible = strings.TrimSuffix(visible, "...")
		} else {
			visible = strings.TrimSuffix(visible, "…")
		}
	}
	return strings.TrimSpace(visible), truncated
}

func openCodeTitleValue(title string) string {
	value := strings.TrimSpace(strings.TrimPrefix(title, "OC | "))
	if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
		return value[1 : len(value)-1]
	}
	return strings.TrimRight(value, "\"")
}

func canonicalDirectory(value string) string {
	abs, err := filepath.Abs(value)
	if err != nil {
		return filepath.Clean(value)
	}
	return filepath.Clean(abs)
}

func decodeOpenCodeStepFinish(row []any, requestedSession string) (OpenCodeStepFinishPart, error) {
	if len(row) != 8 {
		return OpenCodeStepFinishPart{}, fmt.Errorf("expected 8 columns, got %d", len(row))
	}
	partID, ok1 := openCodeString(row[0])
	messageID, ok2 := openCodeString(row[1])
	sessionID, ok3 := openCodeString(row[2])
	if !ok1 || !ok2 || !ok3 || partID == "" || messageID == "" || sessionID == "" || sessionID != requestedSession {
		return OpenCodeStepFinishPart{}, fmt.Errorf("missing or ambiguous required identity")
	}
	created, err := openCodeInteger(row[3])
	if err != nil {
		return OpenCodeStepFinishPart{}, fmt.Errorf("invalid time_created")
	}
	completed, err := openCodeInteger(row[4])
	if err != nil || completed < 0 {
		return OpenCodeStepFinishPart{}, fmt.Errorf("invalid time_updated")
	}
	var part, message, model map[string]any
	if err := decodeOpenCodeObject(row[5], &part); err != nil {
		return OpenCodeStepFinishPart{}, fmt.Errorf("malformed part JSON: %w", err)
	}
	if err := decodeOpenCodeObject(row[6], &message); err != nil {
		return OpenCodeStepFinishPart{}, fmt.Errorf("malformed message JSON: %w", err)
	}
	if err := decodeOpenCodeObject(row[7], &model); err != nil {
		return OpenCodeStepFinishPart{}, fmt.Errorf("malformed session model JSON: %w", err)
	}
	if value, ok := message["role"].(string); !ok || value != "assistant" {
		return OpenCodeStepFinishPart{}, fmt.Errorf("step-finish message is not assistant")
	}
	tokens, err := decodeOpenCodeTokens(part["tokens"])
	if err != nil {
		return OpenCodeStepFinishPart{}, err
	}
	provider := firstOpenCodeString(message, "providerID", "provider_id")
	modelID := firstOpenCodeString(message, "modelID", "model_id")
	if nested, ok := message["model"].(map[string]any); ok {
		provider = firstNonEmpty(provider, firstOpenCodeString(nested, "providerID", "provider_id"))
		modelID = firstNonEmpty(modelID, firstOpenCodeString(nested, "id", "modelID", "model_id"))
	}
	provider = firstNonEmpty(provider, firstOpenCodeString(model, "providerID", "provider_id"))
	modelID = firstNonEmpty(modelID, firstOpenCodeString(model, "id", "modelID", "model_id"))
	if provider == "" || modelID == "" {
		return OpenCodeStepFinishPart{}, fmt.Errorf("missing provider/model identity")
	}
	evidence := "session.model"
	messageProvider := firstOpenCodeString(message, "providerID", "provider_id")
	messageModel := firstOpenCodeString(message, "modelID", "model_id")
	if messageProvider != "" && messageModel != "" {
		evidence = "message.providerID+message.modelID"
	} else if messageProvider != "" {
		evidence = "message.providerID+session.model"
	} else if messageModel != "" {
		evidence = "session.provider+message.modelID"
	}
	return OpenCodeStepFinishPart{PartID: partID, MessageID: messageID, SessionID: sessionID, CreatedAt: created, CompletedAt: completed, Provider: provider, Model: modelID, ModelEvidence: evidence, Tokens: tokens}, nil
}

func decodeOpenCodeObject(value any, target *map[string]any) error {
	var raw []byte
	switch value := value.(type) {
	case string:
		raw = []byte(value)
	case []byte:
		raw = value
	default:
		return fmt.Errorf("JSON is not text")
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	if err := decoder.Decode(target); err != nil || *target == nil {
		return fmt.Errorf("JSON object required")
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fmt.Errorf("trailing JSON")
	}
	return nil
}

func decodeOpenCodeTokens(value any) (OpenCodeTokenSnapshot, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return OpenCodeTokenSnapshot{}, fmt.Errorf("malformed token snapshot")
	}
	cache, cacheOK := object["cache"].(map[string]any)
	if raw, exists := object["cache"]; exists && !cacheOK && raw != nil {
		return OpenCodeTokenSnapshot{}, fmt.Errorf("malformed token snapshot")
	}
	present := make(map[string]bool)
	read := func(key string) (int64, error) {
		raw, exists := object[key]
		if !exists || raw == nil {
			return 0, nil
		}
		value, err := openCodeTokenInteger(raw)
		if err == nil {
			present[key] = true
		}
		return value, err
	}
	total, err := read("total")
	if err != nil {
		return OpenCodeTokenSnapshot{}, fmt.Errorf("malformed token snapshot")
	}
	input, err := read("input")
	if err != nil {
		return OpenCodeTokenSnapshot{}, fmt.Errorf("malformed token snapshot")
	}
	output, err := read("output")
	if err != nil {
		return OpenCodeTokenSnapshot{}, fmt.Errorf("malformed token snapshot")
	}
	reasoning, err := read("reasoning")
	if err != nil {
		return OpenCodeTokenSnapshot{}, fmt.Errorf("malformed token snapshot")
	}
	cacheRead, cacheWrite := int64(0), int64(0)
	if cache != nil {
		for key, target := range map[string]*int64{"read": &cacheRead, "write": &cacheWrite} {
			raw, exists := cache[key]
			if !exists || raw == nil {
				continue
			}
			value, err := openCodeTokenInteger(raw)
			if err != nil {
				return OpenCodeTokenSnapshot{}, fmt.Errorf("malformed token snapshot")
			}
			*target = value
			present["cache_"+key] = true
		}
	}
	return OpenCodeTokenSnapshot{Total: total, Input: input, Output: output, Reasoning: reasoning, CacheRead: cacheRead, CacheWrite: cacheWrite, Present: present}, nil
}

func openCodeTokenInteger(value any) (int64, error) {
	if value == nil {
		return 0, nil
	}
	return openCodeInteger(value)
}

func openCodeInteger(value any) (int64, error) {
	var n int64
	switch value := value.(type) {
	case int64:
		n = value
	case int:
		n = int64(value)
	case float64:
		if value < 0 || math.Trunc(value) != value || value > math.MaxInt64 {
			return 0, fmt.Errorf("not an integer")
		}
		n = int64(value)
	default:
		return 0, fmt.Errorf("not an integer")
	}
	if n < 0 {
		return 0, fmt.Errorf("negative integer")
	}
	return n, nil
}

func openCodeString(value any) (string, bool) {
	switch value := value.(type) {
	case string:
		return value, true
	case []byte:
		return string(value), true
	default:
		return "", false
	}
}

func firstOpenCodeString(values map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := values[key].(string); ok && strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
