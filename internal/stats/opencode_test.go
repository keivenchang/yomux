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
	window, err := db.ReadStepFinishPartsForSelections(ctx, selections, 10_000_000, nil, MaxOpenCodePartReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed >= 2*time.Second {
		t.Fatalf("bounded multi-root query took %s", elapsed)
	}
	if len(window.Parts) != MaxOpenCodePartReadLimit {
		t.Fatalf("bounded multi-root rows = %d, want %d", len(window.Parts), MaxOpenCodePartReadLimit)
	}
	if !window.Truncated || len(window.TruncatedRoots) == 0 {
		t.Fatalf("large multi-root window was not marked partial: truncated=%v roots=%d", window.Truncated, len(window.TruncatedRoots))
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
