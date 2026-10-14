package stats

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/url"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const MaxOpenCodePartReadLimit = 2048
const MaxOpenCodeSessionTree = 64
const MaxOpenCodeContextItems = 200

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

// OpenCodeContextItem is the bounded, display-safe conversation projection used
// by the Go transcript surface. OpenCode stores this data in SQLite parts rather
// than in a JSONL transcript, so callers must not scan the database as text.
type OpenCodeContextItem struct {
	Role      string
	Timestamp string
	CWD       string
	Text      string
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
	RootErrors     map[string]string
}

// OpenCodeStepFinishPage is one bounded raw-row page through the part table.
// ScannedThroughRowID advances over every raw row, including rows that do not
// belong to a selected session or are not assistant step-finish parts.
type OpenCodeStepFinishPage struct {
	Candidates          []OpenCodeStepFinishPart
	Cursors             map[string]OpenCodePartCursor
	RootErrors          map[string]string
	ScannedThroughRowID int64
	HighWaterRowID      int64
	HasMore             bool
}

// OpenCodeStepFinishErrorRef identifies one source row whose malformed data
// made its root's historical coverage partial. Row IDs let later source
// versions revalidate only those rows instead of replaying the full history.
type OpenCodeStepFinishErrorRef struct {
	RootSessionID string
	SessionID     string
	RowID         int64
}

// OpenCodeStepFinishRootErrors reports validation failures isolated to one or
// more roots. The bounded SQL read and decoded rows remain usable for roots
// without an error; callers can publish those atoms while marking only the
// affected roots unavailable.
type OpenCodeStepFinishRootErrors struct {
	ByRoot map[string]string
}

func (e *OpenCodeStepFinishRootErrors) Error() string {
	if e == nil || len(e.ByRoot) == 0 {
		return "OpenCode step-finish root validation failed"
	}
	roots := make([]string, 0, len(e.ByRoot))
	for root := range e.ByRoot {
		roots = append(roots, root)
	}
	sort.Strings(roots)
	return fmt.Sprintf("OpenCode step-finish root validation failed for %s: %s", roots[0], e.ByRoot[roots[0]])
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
	reader                ReadOnlyDB
	close                 func() error
	parentColumn          string
	sessionCounterColumns map[string]bool
	partSessionIndex      bool
	partSessionTimeIndex  bool
	reads                 readMetrics
	sourceVersionMu       sync.Mutex
	sourceDataVersion     int64
	sourceVersionKnown    bool
	sourceScannedSessions map[string]struct{}
}

// timedReadOnlyDB keeps the compatibility reader narrow while measuring the
// elapsed cost of each source-database call. The duration includes SQLite
// stepping and database/sql conversion, but never exposes SQL text or source
// payloads.
type timedReadOnlyDB struct {
	ReadOnlyDB
	metrics *readMetrics
}

func (d *timedReadOnlyDB) Rows(ctx context.Context, query string, args ...any) (rows [][]any, err error) {
	started := time.Now()
	rows, err = d.ReadOnlyDB.Rows(ctx, query, args...)
	d.metrics.record(time.Since(started))
	return rows, err
}

func (d *timedReadOnlyDB) Scalar(ctx context.Context, query string, args ...any) (value any, err error) {
	started := time.Now()
	value, err = d.ReadOnlyDB.Scalar(ctx, query, args...)
	d.metrics.record(time.Since(started))
	return value, err
}

type OpenCodeSessionTreeWindow struct {
	Sessions      []string
	Complete      bool
	NewestUpdated int64
	OldestCreated int64
}

const (
	openCodeSessionMaxAge        = 24 * time.Hour
	openCodeStartSkew            = 5 * time.Minute
	openCodeFutureSkew           = 5 * time.Minute
	openCodeActiveWindow         = 5 * time.Minute
	openCodeInitialHistoryWindow = 5 * time.Minute
	openCodeActivePartRows       = MaxOpenCodePartReadLimit * 2
	maxOpenCodeSessions          = 64
	maxOpenCodeSelections        = maxAgentTokenSources * MaxOpenCodeSessionTree
)

var openCodeTokenCounterDimensions = []string{"input", "output", "reasoning", "cache_read", "cache_write"}

func (d *OpenCodeDatabase) Close() error {
	if d == nil || d.close == nil {
		return nil
	}
	return d.close()
}

// ReadMetrics reports bounded elapsed time spent issuing queries to the
// read-only OpenCode SQLite database. It is diagnostic data only: the token
// collector still owns the cadence and the query limits, while the daemon
// view uses these counters to distinguish source-database work from JSON and
// materialization work.
func (d *OpenCodeDatabase) ReadMetrics() ReadMetrics {
	if d == nil {
		return ReadMetrics{}
	}
	return d.reads.snapshot()
}

// ReadSourceDataVersion returns SQLite's connection-local external-commit
// counter. OpenCodeDatabase keeps one read connection for its lifetime, so an
// unchanged value proves that another process has not committed database
// changes since the last successful token scan.
func (d *OpenCodeDatabase) ReadSourceDataVersion(ctx context.Context) (int64, error) {
	value, err := d.reader.Scalar(ctx, "PRAGMA data_version")
	if err != nil {
		return 0, fmt.Errorf("read OpenCode data version: %w", err)
	}
	version, err := openCodeInteger(value)
	if err != nil || version < 0 {
		return 0, fmt.Errorf("read OpenCode data version: malformed value")
	}
	return version, nil
}

// SourceScanRequired checks whether the sampled database version or any
// selected session differs from the last successfully appended scan. The
// version and session set advance only from the append receipt's commit
// callback, so a failed facts append cannot suppress a retry.
func (d *OpenCodeDatabase) SourceScanRequired(version int64, sessionIDs []string, force bool) bool {
	d.sourceVersionMu.Lock()
	defer d.sourceVersionMu.Unlock()
	if force || !d.sourceVersionKnown || d.sourceDataVersion != version {
		return true
	}
	for _, sessionID := range sessionIDs {
		if _, ok := d.sourceScannedSessions[sessionID]; !ok {
			return true
		}
	}
	return false
}

func (d *OpenCodeDatabase) CommitSourceScan(version int64, sessionIDs []string) {
	d.sourceVersionMu.Lock()
	// Retain only this cycle's bounded selection frontier. A collector may
	// observe many short-lived sessions over one long-lived database handle.
	d.sourceScannedSessions = make(map[string]struct{}, len(sessionIDs))
	d.sourceDataVersion = version
	d.sourceVersionKnown = true
	for _, sessionID := range sessionIDs {
		d.sourceScannedSessions[sessionID] = struct{}{}
	}
	d.sourceVersionMu.Unlock()
}

// ReadConversationTitle reads the persisted agent title for one exact conversation.
func (d *OpenCodeDatabase) ReadConversationTitle(ctx context.Context, sessionID string) (string, error) {
	rows, err := d.reader.Rows(ctx, "SELECT title FROM session WHERE id = ?", sessionID)
	if err != nil {
		return "", fmt.Errorf("read OpenCode conversation title: %w", err)
	}
	if len(rows) == 0 {
		return "", nil
	}
	if len(rows) != 1 || len(rows[0]) != 1 {
		return "", fmt.Errorf("malformed OpenCode title row")
	}
	title, ok := openCodeString(rows[0][0])
	if !ok {
		return "", fmt.Errorf("malformed OpenCode title")
	}
	return title, nil
}

// ReadContextItems returns the newest bounded OpenCode conversation items for a
// single persisted session. It reads only message/part identity and display
// fields, and never mutates the external database.
func (d *OpenCodeDatabase) ReadContextItems(ctx context.Context, sessionID string, limit int) ([]OpenCodeContextItem, error) {
	if strings.TrimSpace(sessionID) == "" {
		return nil, fmt.Errorf("OpenCode session ID is required")
	}
	if limit <= 0 || limit > MaxOpenCodeContextItems {
		return nil, fmt.Errorf("invalid OpenCode context limit %d", limit)
	}
	rows, err := d.reader.Rows(ctx, `SELECT m.id, m.time_created, m.data, p.id, p.time_created, p.data, s.directory
FROM message AS m JOIN session AS s ON s.id = m.session_id LEFT JOIN part AS p ON p.message_id = m.id AND p.session_id = m.session_id
WHERE m.session_id = ?
ORDER BY COALESCE(p.time_created, m.time_created) DESC, m.id DESC, p.id DESC LIMIT ?`, sessionID, limit*8)
	if err != nil {
		return nil, fmt.Errorf("read OpenCode context: %w", err)
	}
	items := make([]OpenCodeContextItem, 0, limit)
	seenMessages := make(map[string]bool)
	for index := len(rows) - 1; index >= 0; index-- {
		row := rows[index]
		if len(row) != 7 {
			return nil, fmt.Errorf("malformed OpenCode context row %d", index)
		}
		messageID, messageOK := openCodeString(row[0])
		messageTime, timeErr := openCodeInteger(row[1])
		if !messageOK || messageID == "" || timeErr != nil {
			return nil, fmt.Errorf("malformed OpenCode context message")
		}
		directory, directoryOK := openCodeString(row[6])
		if !directoryOK {
			return nil, fmt.Errorf("malformed OpenCode context directory")
		}
		var message map[string]any
		if err := decodeOpenCodeObject(row[2], &message); err != nil {
			return nil, fmt.Errorf("malformed OpenCode context message JSON: %w", err)
		}
		role, _ := message["role"].(string)
		if !seenMessages[messageID] {
			seenMessages[messageID] = true
			if text, ok := openCodeTextValue(message["content"]); ok {
				items = append(items, OpenCodeContextItem{Role: firstNonEmpty(role, "assistant"), Timestamp: openCodeContextTimestamp(messageTime), CWD: directory, Text: boundedOpenCodeContextText(text, 5000)})
			}
		}
		partID, _ := openCodeString(row[3])
		if partID == "" {
			continue
		}
		partTime, partTimeErr := openCodeInteger(row[4])
		if partTimeErr != nil {
			return nil, fmt.Errorf("malformed OpenCode context part time")
		}
		var part map[string]any
		if err := decodeOpenCodeObject(row[5], &part); err != nil {
			return nil, fmt.Errorf("malformed OpenCode context part JSON: %w", err)
		}
		partType, _ := part["type"].(string)
		switch partType {
		case "text", "reasoning":
			if text, ok := openCodeTextValue(part["text"]); ok {
				items = append(items, OpenCodeContextItem{Role: firstNonEmpty(role, "assistant"), Timestamp: openCodeContextTimestamp(partTime), CWD: directory, Text: boundedOpenCodeContextText(text, 5000)})
			}
		case "tool":
			name, _ := part["tool"].(string)
			state, _ := part["state"].(map[string]any)
			if name == "" {
				name = "tool"
			}
			toolText := name
			if input, present := state["input"]; present && input != nil {
				if encoded, encodeErr := json.Marshal(input); encodeErr == nil && string(encoded) != "null" {
					toolText += "\n" + string(encoded)
				}
			}
			items = append(items, OpenCodeContextItem{Role: "tool_use", Timestamp: openCodeContextTimestamp(partTime), CWD: directory, Text: boundedOpenCodeContextText(toolText, 2200)})
			if output, ok := openCodeTextValue(state["output"]); ok {
				items = append(items, OpenCodeContextItem{Role: "tool_result", Timestamp: openCodeContextTimestamp(partTime), CWD: directory, Text: boundedOpenCodeContextText(output, 2200)})
			}
		}
	}
	if len(items) > limit {
		items = items[len(items)-limit:]
	}
	return items, nil
}

func openCodeTextValue(value any) (string, bool) {
	text, ok := value.(string)
	return text, ok && strings.TrimSpace(text) != ""
}

func openCodeContextTimestamp(milliseconds int64) string {
	return time.UnixMilli(milliseconds).UTC().Format(time.RFC3339)
}

func boundedOpenCodeContextText(value string, limit int) string {
	characters := []rune(value)
	if len(characters) <= limit {
		return value
	}
	return string(characters[:limit]) + "\n[truncated]"
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
	counterColumns, err := openCodeSessionCounterColumns(ctx, db)
	if err != nil {
		return closeWithError(err)
	}
	partSessionIndex, partSessionTimeIndex, err := openCodePartIndexes(ctx, db)
	if err != nil {
		return closeWithError(err)
	}
	result := &OpenCodeDatabase{reader: db, close: db.Close, parentColumn: parentColumn, sessionCounterColumns: counterColumns, partSessionIndex: partSessionIndex, partSessionTimeIndex: partSessionTimeIndex}
	result.reader = &timedReadOnlyDB{ReadOnlyDB: db, metrics: &result.reads}
	return result, nil
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

var openCodeSessionCounterColumnNames = map[string]string{
	"input":       "tokens_input",
	"output":      "tokens_output",
	"reasoning":   "tokens_reasoning",
	"cache_read":  "tokens_cache_read",
	"cache_write": "tokens_cache_write",
}

func openCodeSessionCounterColumns(ctx context.Context, db ReadOnlyDB) (map[string]bool, error) {
	rows, err := db.Rows(ctx, "PRAGMA table_info(session)")
	if err != nil {
		return nil, &OpenCodeSchemaError{"cannot inspect session token counters"}
	}
	columns := make(map[string]bool, len(rows))
	for _, row := range rows {
		if len(row) < 2 {
			return nil, &OpenCodeSchemaError{"session has malformed column metadata"}
		}
		name, ok := openCodeString(row[1])
		if !ok || name == "" {
			return nil, &OpenCodeSchemaError{"session has malformed column metadata"}
		}
		columns[name] = true
	}
	present := make(map[string]bool)
	for dimension, column := range openCodeSessionCounterColumnNames {
		if columns[column] {
			present[dimension] = true
		}
	}
	return present, nil
}

// openCodePartIndexes reports whether the source can seek parts by session and
// update time or at least by session and rowid. A LIMIT alone does not bound
// SQLite work when it must scan or sort a session's entire part history, so the
// collector uses bounded session tails and reports partial coverage when the
// source lacks the indexes needed to detect older revisions.
func openCodePartIndexes(ctx context.Context, db ReadOnlyDB) (bool, bool, error) {
	indexes, err := db.Rows(ctx, "PRAGMA index_list(part)")
	if err != nil {
		return false, false, &OpenCodeSchemaError{"cannot inspect part indexes"}
	}
	sessionIndex, sessionTimeIndex := false, false
	for _, index := range indexes {
		if len(index) < 5 {
			return false, false, &OpenCodeSchemaError{"part index metadata is malformed"}
		}
		name, ok := openCodeString(index[1])
		partial, partialErr := openCodeInteger(index[4])
		if !ok || name == "" || partialErr != nil {
			return false, false, &OpenCodeSchemaError{"part index metadata is malformed"}
		}
		if partial != 0 {
			continue
		}
		quotedName := `"` + strings.ReplaceAll(name, `"`, `""`) + `"`
		columns, err := db.Rows(ctx, "PRAGMA index_info("+quotedName+")")
		if err != nil {
			return false, false, &OpenCodeSchemaError{"cannot inspect part index columns"}
		}
		if len(columns) == 0 || len(columns[0]) < 3 {
			continue
		}
		first, firstOK := openCodeString(columns[0][2])
		if !firstOK || first != "session_id" {
			continue
		}
		// SQLite appends rowid to each secondary-index key, so only a
		// session_id-only index supports a bounded newest-rowid seek. A
		// composite index orders by its remaining columns before rowid.
		if len(columns) == 1 {
			sessionIndex = true
		}
		if len(columns) >= 2 && len(columns[1]) >= 3 {
			second, secondOK := openCodeString(columns[1][2])
			if secondOK && second == "time_updated" {
				sessionTimeIndex = true
			}
		}
	}
	return sessionIndex, sessionTimeIndex, nil
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

// ReadStepFinishPartsForSelections reads one capped newest-first result slice
// for all selected trees. The selection list is encoded as one JSON argument
// and joined through json_each, so a collection with many roots performs one
// SQLite query rather than one query per root. The per-root sentinel preserves
// fail-closed partial-history behavior without allowing a busy root to hide
// fresh rows from another root; SQLite work can still scale with selected
// session history when revision lookup is required.
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
	seenSelections := make(map[string]OpenCodeStepFinishSelection, len(selections))
	selectedRoots := make(map[string]bool, len(selections))
	allCursorZero := true
	for _, selection := range selections {
		if strings.TrimSpace(selection.SessionID) == "" || strings.TrimSpace(selection.RootSessionID) == "" || selection.ActiveSince < 0 || selection.CursorRowID < 0 {
			return OpenCodeStepFinishWindow{}, fmt.Errorf("malformed OpenCode step-finish selection")
		}
		if _, duplicate := seenSelections[selection.SessionID]; duplicate {
			// A session can belong to only one selected tree. Rejecting repeated
			// session IDs at the API boundary prevents selection_bounds from
			// multiplying rows and makes cursor/root attribution deterministic.
			return OpenCodeStepFinishWindow{}, fmt.Errorf("duplicate OpenCode step-finish selection for session %q", selection.SessionID)
		}
		seenSelections[selection.SessionID] = selection
		selectedSessions[selection.SessionID] = true
		selectedRoots[selection.RootSessionID] = true
		if selection.CursorRowID != 0 {
			allCursorZero = false
		}
	}
	selectionJSON, err := json.Marshal(selections)
	if err != nil {
		return OpenCodeStepFinishWindow{}, fmt.Errorf("encode OpenCode step-finish selections: %w", err)
	}
	// Initial history clipping only needs to distinguish <=limit from >limit.
	// Probe limit+1 rows and do it only for cursorless sessions. The revision
	// query additionally needs a (session_id, time_updated) index. Even with that
	// index, filtering sparse step-finish owners before LIMIT can visit many
	// entries; hard row-visit bounds need a durable page cursor so omitted rows
	// can be resumed without committing past them.
	historyBounds := `older_counts AS MATERIALIZED (
  SELECT newest.session_id,
         CASE WHEN EXISTS (
           SELECT 1
           FROM part AS older
           WHERE older.session_id = newest.session_id
             AND older.time_updated < ?
             AND older.time_updated < newest.newest_time - ?
             AND ` + openCodeStepFinishPartCandidatePredicate("older") + `
           LIMIT 1 OFFSET ?
         ) THEN 2147483647 ELSE 0 END AS part_count
  FROM bounds_newest AS newest
  WHERE EXISTS (
    SELECT 1 FROM selected
    WHERE selected.session_id = newest.session_id AND selected.cursor_rowid = 0
  )
)`
	boundsNewest := `bounds_newest AS MATERIALIZED (
  SELECT selected.session_id,
         CASE WHEN selected.cursor_rowid = 0 THEN
           (SELECT MAX(p.time_updated)
            FROM part AS p
            WHERE p.session_id = selected.session_id
              AND p.time_updated < ?
              AND ` + openCodeStepFinishPartCandidatePredicate("p") + `)
         END AS newest_time
  FROM selected
)`
	if !d.partSessionTimeIndex {
		// A cursorless source gets one bounded tail instead of an unindexed
		// MAX/older-history probe. The root is marked partial below because this
		// schema cannot prove that its older history was inspected.
		boundsNewest = `bounds_newest AS MATERIALIZED (
  SELECT selected.session_id, NULL AS newest_time
  FROM selected
)`
		historyBounds = `older_counts AS MATERIALIZED (
  SELECT newest.session_id, 0 AS part_count
  FROM bounds_newest AS newest
  WHERE 0
)`
	}
	// The bounded subqueries already select each session's row IDs. An outer
	// session_id join makes SQLite walk that session's whole history before
	// checking those IDs; CROSS JOIN keeps the bounded IDs ahead of row lookup.
	revisionRows := `revised_old_parts AS MATERIALIZED (
  SELECT p.rowid, p.id, p.message_id, p.session_id, p.time_created, p.time_updated,
         p.data AS part_data, selected.root_session_id
  FROM selection_bounds AS selected
  CROSS JOIN max_part
  CROSS JOIN part AS p
    ON p.rowid IN (
     SELECT revised.rowid
     FROM part AS revised
     WHERE revised.session_id = selected.session_id
       AND revised.rowid <= max_part.max_rowid - ?
       AND revised.time_updated >= selected.effective_since
       AND revised.time_updated < ?
       AND ` + openCodeStepFinishPartCandidatePredicate("revised") + `
       ORDER BY revised.time_updated DESC, revised.rowid DESC
       LIMIT ?
   )
  WHERE selected.effective_since = selected.active_since
    AND NOT EXISTS (
      SELECT 1
      FROM recent_part AS recent
      WHERE recent.rowid = p.rowid
    )
)`
	rootTruncation := ""
	if !d.partSessionTimeIndex {
		// Keep new rows flowing through the bounded rowid tail, but report that
		// arbitrary in-place revisions cannot be found safely on this schema.
		// Re-reading the entire part history on every external commit is not a
		// valid fallback for a read-only collector.
		rootTruncation = `
  UNION
  SELECT root_session_id
  FROM selection_bounds
  WHERE cursor_rowid >= 0`
		revisionRows = `revised_old_parts AS MATERIALIZED (
			SELECT CAST(NULL AS INTEGER) AS rowid, CAST(NULL AS TEXT) AS id,
			       CAST(NULL AS TEXT) AS message_id, CAST(NULL AS TEXT) AS session_id,
			       CAST(NULL AS INTEGER) AS time_created, CAST(NULL AS INTEGER) AS time_updated,
			       CAST(NULL AS TEXT) AS part_data, selected.root_session_id
		FROM selection_bounds AS selected
		WHERE 0
	)`
	}
	cursorPredicate := `p.time_updated >= selected.effective_since
         OR (selected.cursor_rowid > 0 AND p.rowid > selected.cursor_rowid)`
	if allCursorZero {
		cursorPredicate = `p.time_updated >= selected.effective_since`
	}
	recentPartPredicate := `p.time_updated < ?
    AND (` + cursorPredicate + `)`
	if allCursorZero && d.partSessionTimeIndex {
		// Initial scans must not materialize every part in a busy active window.
		// The time index can seek the newest rows and the overflow sentinel keeps
		// truncation visible when more than the aggregate budget is available.
		recentPartPredicate = `p.rowid IN (
      SELECT active.rowid
      FROM part AS active
      WHERE active.session_id = selected.session_id
        AND active.time_updated >= selected.effective_since
        AND active.time_updated < ?
        AND ` + openCodeStepFinishPartCandidatePredicate("active") + `
      ORDER BY active.time_updated DESC, active.rowid DESC
      LIMIT ?
    )`
	} else if !allCursorZero || !d.partSessionTimeIndex {
		if d.partSessionIndex {
			// The session index supports a bounded newest-rowid seek for this
			// session. A database-wide rowid tail can discard a fresh selected
			// part when unrelated sessions write more than the tail bound.
			recentPartPredicate = `p.rowid IN (
      SELECT tail.rowid
      FROM part AS tail
      WHERE tail.session_id = selected.session_id
        AND tail.time_updated < ?
        AND (tail.time_updated >= selected.effective_since
             OR (selected.cursor_rowid > 0 AND tail.rowid > selected.cursor_rowid))
        AND ` + openCodeStepFinishPartCandidatePredicate("tail") + `
      ORDER BY tail.rowid DESC
      LIMIT ?
    )
    AND (` + cursorPredicate + `)`
		} else {
			recentPartPredicate = `p.session_id = selected.session_id
    AND p.rowid > max_part.max_rowid - ?
    AND p.time_updated < ?
    AND (` + cursorPredicate + `)`
		}
	}
	query := `WITH
max_part AS (
  SELECT COALESCE(MAX(rowid), 0) AS max_rowid FROM part
),
selected(session_id, root_session_id, active_since, cursor_rowid) AS (
  SELECT json_extract(value, '$.session_id'),
         json_extract(value, '$.root_session_id'),
         json_extract(value, '$.active_since'),
         COALESCE(json_extract(value, '$.cursor_rowid'), 0)
  FROM json_each(?)
),
` + boundsNewest + `,
` + historyBounds + `,
bounds AS (
  SELECT newest.session_id, COALESCE(older.part_count, 0) AS part_count, newest.newest_time,
         newest.newest_time - ? AS initial_cutoff
  FROM bounds_newest AS newest
  LEFT JOIN older_counts AS older ON older.session_id = newest.session_id
),
selection_bounds AS MATERIALIZED (
  SELECT selected.session_id, selected.root_session_id, selected.active_since, selected.cursor_rowid,
         CASE WHEN selected.cursor_rowid = 0
                   AND bounds.part_count > ?
                   AND selected.active_since < bounds.initial_cutoff
              THEN bounds.initial_cutoff
              ELSE selected.active_since
         END AS effective_since
  FROM selected
  JOIN bounds ON bounds.session_id = selected.session_id
),
recent_part AS MATERIALIZED (
  SELECT p.rowid, p.id, p.message_id, p.session_id, p.time_created, p.time_updated,
         p.data AS part_data
  FROM selection_bounds AS selected
  CROSS JOIN max_part
  CROSS JOIN part AS p
  WHERE ` + recentPartPredicate + `
),
` + revisionRows + `,
candidates AS MATERIALIZED (
  SELECT candidate_rows.rowid, candidate_rows.id, candidate_rows.message_id,
         candidate_rows.session_id, candidate_rows.time_created, candidate_rows.time_updated,
         candidate_rows.part_data, m.data AS message_data, s.model, candidate_rows.root_session_id
  FROM (
    SELECT p.rowid, p.id, p.message_id, p.session_id, p.time_created, p.time_updated,
           p.part_data, selected.root_session_id
    FROM recent_part AS p
    JOIN selection_bounds AS selected ON selected.session_id = p.session_id
    WHERE (` + cursorPredicate + `)
    UNION ALL
    SELECT rowid, id, message_id, session_id, time_created, time_updated,
           part_data, root_session_id
    FROM revised_old_parts
  ) AS candidate_rows
  JOIN message AS m ON m.id = candidate_rows.message_id AND m.session_id = candidate_rows.session_id
  JOIN session AS s ON s.id = candidate_rows.session_id
  WHERE json_valid(candidate_rows.part_data) = 1
    AND json_extract(candidate_rows.part_data, '$.type') = 'step-finish'
    AND json_valid(m.data) = 1 AND json_extract(m.data, '$.role') = 'assistant'
),
root_ranked AS (
  SELECT candidates.*,
         ROW_NUMBER() OVER (
           PARTITION BY root_session_id
           ORDER BY time_updated DESC, rowid DESC
         ) AS root_rank
  FROM candidates
),
ranked AS (
  -- Reserve the first slot for every root before filling the remaining
  -- aggregate budget. This keeps a busy root from starving another root's
  -- freshest row while retaining one hard, global result limit.
  SELECT root_ranked.*,
         ROW_NUMBER() OVER (
           ORDER BY root_rank ASC, time_updated DESC, rowid DESC
         ) AS global_rank
  FROM root_ranked
),
global_top AS (
  SELECT rowid, id, message_id, session_id, time_created, time_updated,
         part_data, message_data, model, root_session_id, global_rank
  FROM ranked
  WHERE global_rank <= ?
),
root_bounds AS (
  SELECT root_session_id, COUNT(*) AS candidate_count, MAX(time_updated) AS newest_time
  FROM candidates
  GROUP BY root_session_id
),
root_newest AS (
  SELECT candidates.root_session_id, MAX(candidates.rowid) AS newest_rowid
  FROM candidates
  JOIN root_bounds ON root_bounds.root_session_id = candidates.root_session_id
                   AND root_bounds.newest_time = candidates.time_updated
  GROUP BY candidates.root_session_id
),
root_truncated AS (
  SELECT root_session_id
  FROM root_bounds
  WHERE candidate_count > ?
  UNION
  SELECT root_newest.root_session_id
  FROM root_newest
  LEFT JOIN global_top ON global_top.rowid = root_newest.newest_rowid
  WHERE global_top.rowid IS NULL
  UNION
  SELECT root_session_id
  FROM selection_bounds
  WHERE cursor_rowid = 0 AND effective_since > active_since
` + rootTruncation + `
),
malformed_roots AS (
  SELECT selected.root_session_id
  FROM selected
  JOIN selection_bounds AS bounded ON bounded.session_id = selected.session_id
  JOIN recent_part AS malformed ON malformed.session_id = bounded.session_id
  WHERE (malformed.time_updated >= bounded.effective_since
         OR (bounded.cursor_rowid > 0 AND malformed.rowid > bounded.cursor_rowid))
    AND json_valid(malformed.part_data) = 0
  GROUP BY selected.root_session_id
  UNION
  SELECT selected.root_session_id
  FROM selected
  JOIN selection_bounds AS bounded ON bounded.session_id = selected.session_id
  JOIN revised_old_parts AS malformed ON malformed.session_id = bounded.session_id
  WHERE bounded.effective_since = bounded.active_since
    AND json_valid(malformed.part_data) = 0
  GROUP BY selected.root_session_id
),
bounded AS (
  SELECT rowid, id, message_id, session_id, time_created, time_updated,
         part_data, message_data, model, root_session_id, global_rank, 1 AS root_rank
  FROM global_top
  UNION ALL
  SELECT NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
         root_session_id, ?, 1
  FROM root_truncated
  UNION ALL
  SELECT NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
         root_session_id, 0, 0
  FROM malformed_roots
)
SELECT rowid, id, message_id, session_id, time_created, time_updated,
	       part_data, message_data, model, root_session_id, global_rank, root_rank
FROM bounded`
	cutoffMillis := int64(openCodeInitialHistoryWindow / time.Millisecond)
	args := []any{string(selectionJSON)}
	if d.partSessionTimeIndex {
		args = append(args, end, end, cutoffMillis, limit, cutoffMillis, limit)
	} else {
		args = append(args, cutoffMillis, limit)
	}
	if allCursorZero && d.partSessionTimeIndex {
		args = append(args, end, limit+1)
	} else if d.partSessionIndex {
		// A session-specific index can safely retain only the aggregate result
		// budget plus one overflow sentinel. Keep the wider global rowid tail
		// below for schemas without that index, where unrelated sessions may
		// otherwise crowd out a fresh selected row.
		args = append(args, end, limit+1)
	} else {
		args = append(args, openCodeActivePartRows, end)
	}
	if d.partSessionTimeIndex {
		args = append(args, openCodeActivePartRows, end, limit+1)
	}
	args = append(args, limit, limit, limit+1)
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
	malformedRoots := make(map[string]string)
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
		if globalRank == 0 && rootRank == 0 {
			malformedRoots[root] = "malformed-transcript-json"
			continue
		}
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
		rootRank, _ := openCodeInteger(row[11])
		if globalRank == 0 && rootRank == 0 {
			continue
		}
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
	rootErrors := malformedRoots
	if len(d.sessionCounterColumns) > 0 {
		// Anchor validation is per root, not global to the query. A long-lived
		// collector commonly has one established root beside a newly discovered
		// root; the latter still needs its positive cumulative counters checked
		// against the bounded initial history before we publish complete coverage.
		selectionsByRoot := make(map[string][]OpenCodeStepFinishSelection)
		partsByRoot := make(map[string][]OpenCodeStepFinishPart)
		for _, selection := range selections {
			selectionsByRoot[selection.RootSessionID] = append(selectionsByRoot[selection.RootSessionID], selection)
		}
		for _, part := range result {
			partsByRoot[part.RootSessionID] = append(partsByRoot[part.RootSessionID], part)
		}
		initialSelectionsByRoot := make(map[string][]OpenCodeStepFinishSelection)
		sessionIDs := make([]string, 0, len(selections))
		for root, rootSelections := range selectionsByRoot {
			if _, failed := rootErrors[root]; failed {
				continue
			}
			if len(rootSelections) == 0 || truncatedRoots[root] {
				continue
			}
			initialRoot := true
			rootSessionIDs := make([]string, 0, len(rootSelections))
			for _, selection := range rootSelections {
				if selection.CursorRowID != 0 {
					initialRoot = false
					break
				}
				rootSessionIDs = append(rootSessionIDs, selection.SessionID)
			}
			if !initialRoot {
				continue
			}
			initialSelectionsByRoot[root] = rootSelections
			sessionIDs = append(sessionIDs, rootSessionIDs...)
		}
		if len(sessionIDs) > 0 {
			sort.Strings(sessionIDs)
			anchors, anchorErr := d.readOpenCodeSessionCounters(ctx, sessionIDs)
			if anchorErr != nil {
				return OpenCodeStepFinishWindow{}, anchorErr
			}
			for root, rootSelections := range initialSelectionsByRoot {
				if err := validateOpenCodeSessionCounters(rootSelections, anchors, partsByRoot[root]); err != nil {
					rootErrors[root] = err.Error()
				}
			}
		}
	}
	if len(rootErrors) > 0 {
		// Do not advance a failed root's cursor or emit its rows. A valid sibling
		// root must still be able to commit its atoms in this cadence.
		sessionRoots := make(map[string]string, len(selections))
		for _, selection := range selections {
			sessionRoots[selection.SessionID] = selection.RootSessionID
		}
		filtered := make([]OpenCodeStepFinishPart, 0, len(result))
		for _, part := range result {
			if _, failed := rootErrors[part.RootSessionID]; !failed {
				filtered = append(filtered, part)
			}
		}
		result = filtered
		for sessionID := range nextCursors {
			if root, ok := sessionRoots[sessionID]; ok {
				if _, failed := rootErrors[root]; failed {
					if original, existed := cursors[sessionID]; existed {
						nextCursors[sessionID] = original
					} else {
						delete(nextCursors, sessionID)
					}
				}
			}
		}
	}
	window := OpenCodeStepFinishWindow{Parts: result, Cursors: nextCursors, Truncated: truncated, TruncatedRoots: truncatedRoots, RootErrors: rootErrors}
	if len(rootErrors) > 0 {
		return window, &OpenCodeStepFinishRootErrors{ByRoot: rootErrors}
	}
	return window, nil
}

// ReadPartHighWaterRowID returns a stable upper boundary for a sequence of
// raw-row pages. Callers should capture it once and pass the same value to
// every ReadStepFinishPartsPage call in that scan.
func (d *OpenCodeDatabase) ReadPartHighWaterRowID(ctx context.Context) (int64, error) {
	value, err := d.reader.Scalar(ctx, `SELECT COALESCE(MAX(rowid), 0) FROM part`)
	if err != nil {
		return 0, fmt.Errorf("read OpenCode part high-water rowid: %w", err)
	}
	rowID, err := openCodeInteger(value)
	if err != nil {
		return 0, fmt.Errorf("malformed OpenCode part high-water rowid")
	}
	return rowID, nil
}

// ReadStepFinishPartsPage reads at most pageSize raw part rows after afterRowID
// and through highWaterRowID. The SQL page boundary is applied before any
// session, JSON, part-type, or message-role filtering, so even schemas without
// a session index have bounded row visits. The extra sentinel row reports
// whether another raw page exists; callers should persist ScannedThroughRowID
// only alongside the candidates and root errors returned by this page.
func (d *OpenCodeDatabase) ReadStepFinishPartsPage(ctx context.Context, selections []OpenCodeStepFinishSelection, afterRowID, highWaterRowID int64, pageSize int) (OpenCodeStepFinishPage, error) {
	if len(selections) == 0 || len(selections) > maxOpenCodeSelections {
		return OpenCodeStepFinishPage{}, fmt.Errorf("invalid OpenCode step-finish selection size %d", len(selections))
	}
	if afterRowID < 0 || highWaterRowID < afterRowID {
		return OpenCodeStepFinishPage{}, fmt.Errorf("invalid OpenCode part rowid range")
	}
	if pageSize <= 0 || pageSize > MaxOpenCodePartReadLimit {
		return OpenCodeStepFinishPage{}, fmt.Errorf("invalid OpenCode part page size %d", pageSize)
	}
	selectionBySession := make(map[string]OpenCodeStepFinishSelection, len(selections))
	for _, selection := range selections {
		if strings.TrimSpace(selection.SessionID) == "" || strings.TrimSpace(selection.RootSessionID) == "" || selection.ActiveSince < 0 || selection.CursorRowID < 0 {
			return OpenCodeStepFinishPage{}, fmt.Errorf("malformed OpenCode step-finish selection")
		}
		if _, duplicate := selectionBySession[selection.SessionID]; duplicate {
			return OpenCodeStepFinishPage{}, fmt.Errorf("duplicate OpenCode step-finish selection for session %q", selection.SessionID)
		}
		selectionBySession[selection.SessionID] = selection
	}

	rows, err := d.reader.Rows(ctx, `WITH raw_page AS MATERIALIZED (
  SELECT p.rowid, p.id, p.message_id, p.session_id, p.time_created,
         p.time_updated, p.data
  FROM part AS p
  WHERE p.rowid > ? AND p.rowid <= ?
  ORDER BY p.rowid ASC
  LIMIT ?
)
SELECT raw.rowid, raw.id, raw.message_id, raw.session_id,
       raw.time_created, raw.time_updated, raw.data, m.data, s.model
FROM raw_page AS raw
LEFT JOIN message AS m
  ON m.id = raw.message_id AND m.session_id = raw.session_id
LEFT JOIN session AS s ON s.id = raw.session_id
ORDER BY raw.rowid ASC`, afterRowID, highWaterRowID, pageSize+1)
	if err != nil {
		return OpenCodeStepFinishPage{}, fmt.Errorf("read OpenCode raw part page: %w", err)
	}
	capacity := len(rows)
	if capacity > pageSize {
		capacity = pageSize
	}
	page := OpenCodeStepFinishPage{
		Candidates:          make([]OpenCodeStepFinishPart, 0, capacity),
		Cursors:             make(map[string]OpenCodePartCursor),
		RootErrors:          make(map[string]string),
		ScannedThroughRowID: afterRowID,
		HighWaterRowID:      highWaterRowID,
		HasMore:             len(rows) > pageSize,
	}
	if len(rows) > pageSize {
		rows = rows[:pageSize]
	}
	for index, row := range rows {
		if len(row) != 9 {
			return OpenCodeStepFinishPage{}, fmt.Errorf("OpenCode raw part page row %d has %d columns", index, len(row))
		}
		rowID, rowIDErr := openCodeInteger(row[0])
		if rowIDErr != nil || rowID <= page.ScannedThroughRowID || rowID > highWaterRowID {
			return OpenCodeStepFinishPage{}, fmt.Errorf("malformed OpenCode raw part rowid")
		}
		page.ScannedThroughRowID = rowID
		sessionID, ok := openCodeString(row[3])
		if !ok {
			continue
		}
		selection, selected := selectionBySession[sessionID]
		if !selected {
			continue
		}

		var partData map[string]any
		if decodeErr := decodeOpenCodeObject(row[6], &partData); decodeErr != nil {
			addOpenCodePageError(&page, selection.RootSessionID)
			continue
		}
		partType, _ := partData["type"].(string)
		if partType != "step-finish" {
			continue
		}
		var messageData map[string]any
		if decodeErr := decodeOpenCodeObject(row[7], &messageData); decodeErr != nil {
			addOpenCodePageError(&page, selection.RootSessionID)
			continue
		}
		role, _ := messageData["role"].(string)
		if role != "assistant" {
			continue
		}
		part, decodeErr := decodeOpenCodeStepFinish(row[1:9], sessionID)
		if decodeErr != nil {
			addOpenCodePageError(&page, selection.RootSessionID)
			continue
		}
		part.RootSessionID = selection.RootSessionID
		page.Candidates = append(page.Candidates, part)
		current, exists := page.Cursors[sessionID]
		if !exists || rowID > current.RowID {
			page.Cursors[sessionID] = OpenCodePartCursor{TimeUpdated: part.CompletedAt, PartID: part.PartID, RowID: rowID}
		}
	}
	if !page.HasMore {
		// Every row through the captured boundary was examined, including
		// rowids removed after the high-water mark was recorded.
		page.ScannedThroughRowID = highWaterRowID
	}
	return page, nil
}

func addOpenCodePageError(page *OpenCodeStepFinishPage, rootSessionID string) {
	page.RootErrors[rootSessionID] = "malformed-transcript-json"
}

func (d *OpenCodeDatabase) readOpenCodeSessionCounters(ctx context.Context, sessionIDs []string) (map[string]OpenCodeTokenSnapshot, error) {
	if len(sessionIDs) == 0 || len(d.sessionCounterColumns) == 0 {
		return map[string]OpenCodeTokenSnapshot{}, nil
	}
	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(sessionIDs)), ",")
	selectColumns := make([]string, 0, len(openCodeTokenCounterDimensions))
	for _, dimension := range openCodeTokenCounterDimensions {
		column := openCodeSessionCounterColumnNames[dimension]
		if d.sessionCounterColumns[dimension] {
			selectColumns = append(selectColumns, quoteOpenCodeIdentifier(column))
		} else {
			selectColumns = append(selectColumns, "NULL")
		}
	}
	query := fmt.Sprintf("SELECT id, %s FROM session WHERE id IN (%s)", strings.Join(selectColumns, ", "), placeholders)
	args := make([]any, len(sessionIDs))
	for index, sessionID := range sessionIDs {
		args[index] = sessionID
	}
	rows, err := d.reader.Rows(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("read OpenCode session token counters: %w", err)
	}
	result := make(map[string]OpenCodeTokenSnapshot, len(rows))
	for index, row := range rows {
		if len(row) != len(openCodeTokenCounterDimensions)+1 {
			return nil, fmt.Errorf("malformed OpenCode session token counter row %d", index)
		}
		sessionID, ok := openCodeString(row[0])
		if !ok || sessionID == "" {
			return nil, fmt.Errorf("malformed OpenCode session token counter identity")
		}
		snapshot := OpenCodeTokenSnapshot{Present: make(map[string]bool)}
		for offset, dimension := range openCodeTokenCounterDimensions {
			if row[offset+1] == nil {
				continue
			}
			value, valueErr := openCodeTokenInteger(row[offset+1])
			if valueErr != nil {
				return nil, fmt.Errorf("malformed OpenCode session token counter %s: %w", dimension, valueErr)
			}
			snapshot.Present[dimension] = true
			switch dimension {
			case "input":
				snapshot.Input = value
			case "output":
				snapshot.Output = value
			case "reasoning":
				snapshot.Reasoning = value
			case "cache_read":
				snapshot.CacheRead = value
			case "cache_write":
				snapshot.CacheWrite = value
			}
		}
		if _, duplicate := result[sessionID]; duplicate {
			return nil, fmt.Errorf("ambiguous OpenCode session token counter identity")
		}
		result[sessionID] = snapshot
	}
	return result, nil
}

func validateOpenCodeSessionCounters(selections []OpenCodeStepFinishSelection, anchors map[string]OpenCodeTokenSnapshot, parts []OpenCodeStepFinishPart) error {
	rootBySession := make(map[string]string, len(selections))
	for _, selection := range selections {
		rootBySession[selection.SessionID] = selection.RootSessionID
	}
	rootExpected := make(map[string]OpenCodeTokenSnapshot)
	rootActual := make(map[string]OpenCodeTokenSnapshot)
	rootPresent := make(map[string]map[string]bool)
	rootParts := make(map[string]int)
	for _, selection := range selections {
		anchor, ok := anchors[selection.SessionID]
		if !ok {
			return fmt.Errorf("OpenCode session token counter missing for %s", selection.SessionID)
		}
		root := selection.RootSessionID
		total := rootExpected[root]
		if total.Present == nil {
			total.Present = make(map[string]bool)
		}
		for _, dimension := range openCodeTokenCounterDimensions {
			if !anchor.Present[dimension] {
				continue
			}
			total.Present[dimension] = true
			addOpenCodeTokenDimension(&total, dimension, openCodeTokenDimension(anchor, dimension))
		}
		rootExpected[root] = total
		if _, exists := rootPresent[root]; !exists {
			rootPresent[root] = make(map[string]bool)
			for _, dimension := range openCodeTokenCounterDimensions {
				rootPresent[root][dimension] = true
			}
		}
	}
	for _, part := range parts {
		root := part.RootSessionID
		if root == "" || rootBySession[part.SessionID] != root {
			return fmt.Errorf("OpenCode session token counter root mismatch")
		}
		rootParts[root]++
		present := rootPresent[root]
		for _, dimension := range openCodeTokenCounterDimensions {
			if !part.Tokens.Present[dimension] {
				present[dimension] = false
				continue
			}
			actual := rootActual[root]
			addOpenCodeTokenDimension(&actual, dimension, openCodeTokenDimension(part.Tokens, dimension))
			rootActual[root] = actual
		}
	}
	for root, anchor := range rootExpected {
		positiveAnchor := false
		for _, dimension := range openCodeTokenCounterDimensions {
			if anchor.Present[dimension] && openCodeTokenDimension(anchor, dimension) > 0 {
				positiveAnchor = true
				break
			}
		}
		// Some OpenCode schema copies expose cumulative columns but leave them at
		// zero while the durable step-finish parts carry the real history. A zero
		// anchor is therefore not evidence that the part history is wrong; only a
		// positive anchor can fail closed on missing or mismatched history.
		if !positiveAnchor {
			continue
		}
		if rootParts[root] == 0 {
			for _, dimension := range []string{"input", "output", "cache_read", "cache_write"} {
				if anchor.Present[dimension] && openCodeTokenDimension(anchor, dimension) > 0 {
					return fmt.Errorf("opencode-cumulative-history-anchor-missing")
				}
			}
			continue
		}
		for _, dimension := range openCodeTokenCounterDimensions {
			if !anchor.Present[dimension] {
				continue
			}
			// A positive reasoning counter is authoritative when the part history
			// contains reasoning values, but reasoning is not materialized as a
			// supported usage atom. Match Python's reader by allowing that optional
			// dimension to be absent while still rejecting a present mismatch.
			if openCodeTokenDimension(anchor, dimension) <= 0 {
				continue
			}
			if !rootPresent[root][dimension] {
				if dimension == "reasoning" {
					continue
				}
				return fmt.Errorf("opencode-incomplete-supported-token-history")
			}
			if openCodeTokenDimension(anchor, dimension) != openCodeTokenDimension(rootActual[root], dimension) {
				return fmt.Errorf("opencode-cumulative-anchor-mismatch")
			}
		}
	}
	return nil
}

func openCodeTokenDimension(snapshot OpenCodeTokenSnapshot, dimension string) int64 {
	switch dimension {
	case "input":
		return snapshot.Input
	case "output":
		return snapshot.Output
	case "reasoning":
		return snapshot.Reasoning
	case "cache_read":
		return snapshot.CacheRead
	case "cache_write":
		return snapshot.CacheWrite
	default:
		return 0
	}
}

func addOpenCodeTokenDimension(snapshot *OpenCodeTokenSnapshot, dimension string, value int64) {
	switch dimension {
	case "input":
		snapshot.Input += value
	case "output":
		snapshot.Output += value
	case "reasoning":
		snapshot.Reasoning += value
	case "cache_read":
		snapshot.CacheRead += value
	case "cache_write":
		snapshot.CacheWrite += value
	}
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
	if err := decodeOpenCodeOptionalObject(row[7], &model); err != nil {
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

// OpenCode schemas in the wild can leave session.model NULL or empty while
// persisting complete provider/model identity on the assistant message. The
// Python reader treats that column as an empty object; keep the same fallback
// without weakening validation for a non-empty malformed value.
func decodeOpenCodeOptionalObject(value any, target *map[string]any) error {
	if value == nil {
		*target = map[string]any{}
		return nil
	}
	switch typed := value.(type) {
	case string:
		if strings.TrimSpace(typed) == "" {
			*target = map[string]any{}
			return nil
		}
	case []byte:
		if len(bytes.TrimSpace(typed)) == 0 {
			*target = map[string]any{}
			return nil
		}
	}
	return decodeOpenCodeObject(value, target)
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

// openCodeStepFinishPartCandidatePredicate filters owner rows before their
// bounded SQL LIMIT so tool/text parts cannot starve newer token snapshots.
// Malformed part JSON remains in the window so the caller can mark that root
// unavailable rather than silently dropping the bad source row.
func openCodeStepFinishPartCandidatePredicate(partAlias string) string {
	messageAlias := "step_finish_message"
	return fmt.Sprintf(`(
  json_valid(%[1]s.data) = 0
  OR (
    json_valid(%[1]s.data) = 1
    AND json_extract(%[1]s.data, '$.type') = 'step-finish'
    AND EXISTS (
      SELECT 1
      FROM message AS %[2]s
      WHERE %[2]s.id = %[1]s.message_id
        AND %[2]s.session_id = %[1]s.session_id
        AND json_valid(%[2]s.data) = 1
        AND json_extract(%[2]s.data, '$.role') = 'assistant'
    )
  )
)`, partAlias, messageAlias)
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
