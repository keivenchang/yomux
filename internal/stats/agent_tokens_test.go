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
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yolomux/internal/testsupport"

	_ "github.com/mattn/go-sqlite3"
)

type countingTokenWriter struct{}

func (countingTokenWriter) AppendBatch(_ context.Context, batch AppendBatch) (AppendResult, error) {
	return AppendResult{UsageAccepted: len(batch.UsageAtoms), CoverageChanged: len(batch.CoverageEpochs), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
}

func TestAgentTokenCollectorCyclesRealShapedSourcesAndProjectsRate(t *testing.T) {
	root := t.TempDir()
	claudePath := copyObservedTranscript(t, "claude", "claude/projects/-fixture/session.jsonl")
	codexPath := copyObservedTranscript(t, "codex", "codex/sessions/2026/09/02/rollout.jsonl")
	opencodePath := copyOpenCodeFixture(t)
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{
				{Kind: "claude", TmuxSession: "claude"},
				{Kind: "codex", TmuxSession: "codex"},
				{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode"},
				{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode-child"},
			}, nil
		},
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{
				{Path: claudePath, Provider: "claude", SourceID: "claude", ThreadID: "claude"},
				{Path: codexPath, Provider: "codex", SourceID: "codex", ThreadID: "codex"},
			}, nil
		},
		OpenCodeDatabase: opencodePath,
		CursorPath:       filepath.Join(root, "opencode-cursors.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})
	ctx, cancel := context.WithCancel(context.Background())
	if err := collector.Start(ctx); err != nil {
		t.Fatal(err)
	}
	time.Sleep(100 * time.Millisecond)
	cancel()
	collector.Stop()
	first := collector.Counters()
	if first.UsageAccepted != 27 || first.Cycles != 1 {
		t.Fatalf("first counters = %+v", first)
	}

	clock.now = time.Unix(1788307310, 0)
	collector.RunOnce(context.Background())
	second := collector.Counters()
	if second.UsageAccepted != first.UsageAccepted {
		t.Fatalf("unchanged cycle accepted new atoms: %+v", second)
	}

	appendJSONL(t, claudePath, usageClaude(1788307311, "new-message", 5, 2), false)
	mutateSQLite(t, opencodePath, `INSERT INTO message VALUES ('msg-2', 'ses-1', 3000, 3000, '{"role":"assistant"}')`)
	mutateSQLite(t, opencodePath, `INSERT INTO part VALUES ('part-2', 'msg-2', 'ses-1', 3000, 3000, '{"type":"step-finish","tokens":{"total":4,"input":2,"output":2,"cache":{"read":0}}}')`)
	clock.now = time.Unix(1788307320, 0)
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 31 {
		t.Fatalf("incremental accepted = %d", got)
	}

	database, err := OpenReadOnly(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil || len(atoms) != 31 {
		t.Fatalf("usage rows = %d err=%v", len(atoms), err)
	}
	projection, err := ProjectTokens(ProjectionRequest{Start: 1788307200, End: 1788307260, Resolution: 60}, atoms)
	if err != nil {
		t.Fatal(err)
	}
	if got := projection.Buckets[0].Input + projection.Buckets[0].Output + projection.Buckets[0].Cache; got != 180 {
		t.Fatalf("token total = %g", got)
	}
	if got := projection.Buckets[0].Input + projection.Buckets[0].Output + projection.Buckets[0].Cache; got != 180 {
		t.Fatalf("tokens/minute projection = %g", got)
	}
}

func TestAgentTokenCollectorSnapshotIsBoundedAndTracksDiagnostics(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "source", "opencode.db")
	cursorPath := filepath.Join(root, "cursor.json")
	clock := &agentStatusTestClock{now: time.Unix(100, 123000000)}
	collector := NewAgentTokenCollector(countingTokenWriter{}, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		CursorPath:       cursorPath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode"}, {Kind: "opencode"}}, []error{errors.New(strings.Repeat("inventory-error ", 100))}
		},
		SkipTranscriptScan: true,
	})
	collector.RunOnce(context.Background())
	snapshot := collector.Snapshot()
	if snapshot.Cycles != 1 || snapshot.OpenCodeInventoryRecords != 2 || snapshot.OpenCodeInventoryErrors != 1 {
		t.Fatalf("snapshot counters = %+v", snapshot)
	}
	if snapshot.LastAttemptAt == "" || snapshot.LastSuccessAt == "" || snapshot.LastReason == "" {
		t.Fatalf("snapshot timing/reason = %+v", snapshot)
	}
	if len([]byte(snapshot.OpenCodeInventoryLastError)) > maxDiagnosticTextBytes || len([]byte(snapshot.LastReason)) > maxDiagnosticTextBytes {
		t.Fatalf("diagnostic strings exceed bound = %+v", snapshot)
	}
	if strings.Contains(snapshot.SourceDBPath, root) || strings.Contains(snapshot.SourceCursorPath, root) {
		t.Fatalf("absolute source path leaked = %+v", snapshot)
	}
	if snapshot.BatchUnavailableSpans != 1 || snapshot.BatchAtoms != 0 {
		t.Fatalf("batch diagnostics = %+v", snapshot)
	}
	if _, err := json.Marshal(snapshot); err != nil {
		t.Fatal(err)
	}
}

func TestAgentTokenCollectorRollsBackTranscriptAndOpenCodeCursor(t *testing.T) {
	root := t.TempDir()
	transcript := filepath.Join(root, "claude.jsonl")
	writeJSONL(t, transcript, usageClaude(10, "one", 4, 3))
	opencodePath := createOpenCodeFixture(t)
	cursorPath := filepath.Join(root, "cursor.json")
	clock := &agentStatusTestClock{now: time.Unix(20, 0)}
	collector := NewAgentTokenCollector(nil, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "claude"}, {Kind: "opencode", OpenCodeSession: "ses-1"}}, nil
		},
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{{Path: transcript, Provider: "claude", SourceID: "claude", ThreadID: "one"}}, nil
		},
		OpenCodeDatabase: opencodePath, CursorPath: cursorPath, EpochStartedAt: 1,
	})
	collector.RunOnce(context.Background())
	if collector.Counters().Errors != 1 {
		t.Fatalf("rollback counters = %+v", collector.Counters())
	}
	if _, err := NewOpenCodeTokenCursor(cursorPath).state(); err != nil {
		t.Fatalf("cursor remained pending after rollback: %v", err)
	}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	collector.writer = writer
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 5 {
		t.Fatalf("replayed usage after rollback = %d counters=%+v", got, collector.Counters())
	}
	writer.Close()
}

func TestOpenCodeCursorRejectsPendingJournal(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cursor.json")
	cursor := NewOpenCodeTokenCursor(path)
	if err := cursor.prepare(openCodeCursorState{Events: map[string]string{"a": "b"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := NewOpenCodeTokenCursor(path).state(); err == nil {
		t.Fatal("pending cursor accepted")
	}
}

func TestAgentTokenCollectorResolvesNormalOpenCodeTUIAndAttributesAgent(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode.db")
	createOpenCodeFixtureWithTitle(t, databasePath, "ses-normal", "/repo/live", "build", 1788307299000)
	openDB, err := OpenOpenCodeReadOnly(context.Background(), databasePath)
	if err != nil {
		t.Fatal(err)
	}
	resolved, resolveErr := openDB.ResolveSessionID(context.Background(), "/repo/live", "OC | \"build\"", 0, 1788307300)
	openDB.Close()
	if resolveErr != nil || resolved != "ses-normal" {
		t.Fatalf("resolved session = %q err=%v", resolved, resolveErr)
	}
	parts, err := openCodePartsForTest(t, databasePath, "ses-normal")
	if err != nil || len(parts) != 1 {
		t.Fatalf("fixture parts = %#v err=%v", parts, err)
	}
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", Directory: "/repo/live", Title: "OC | \"build\"", TmuxSession: "live-opencode"}}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "cursor.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})
	batch, receipts, collectErr := collector.collect(context.Background(), 1788307300, 10)
	if collectErr != nil {
		t.Fatal(collectErr)
	}
	if len(batch.UsageAtoms) != 3 {
		t.Fatalf("collected OpenCode batch = %#v unavailable=%#v", batch.UsageAtoms, batch.UnavailableSpans)
	}
	if _, err := writer.AppendBatch(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
	for _, receipt := range receipts {
		receipt.commit()
	}
	database, err := OpenReadOnly(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil || len(atoms) != 3 {
		t.Fatalf("stored OpenCode atoms = %d err=%v", len(atoms), err)
	}
	for _, atom := range atoms {
		if atom.Payload["provider"] != "openai" || atom.Payload["model"] != "gpt" || atom.Payload["agent_id"] != "live-opencode" {
			t.Fatalf("OpenCode attribution = %#v", atom.Payload)
		}
	}
	snapshot, err := BuildSnapshot(context.Background(), database, SnapshotRequest{
		Start: 1788307200, End: 1788307360, Resolution: 60,
	})
	if err != nil {
		t.Fatal(err)
	}
	series := map[string]float64{}
	for _, bucket := range snapshot.Materialized.Buckets {
		for _, item := range bucket.Series {
			series[item.Name] = item.Value
		}
	}
	if series["agent_tokens_per_minute:live-opencode"] <= 0 || series["model_tokens_per_minute:output:gpt"] <= 0 {
		t.Fatalf("OpenCode token series = %#v buckets=%#v", series, snapshot.Materialized.Buckets)
	}
}

func TestAgentTokenCollectorResolvesTruncatedCurrentOpenCodeTitle(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode.db")
	createOpenCodeFixtureWithTitle(t, databasePath, "ses-current", "/repo/current", "Fix live ps inventory and token identity", 1789364600000)
	clock := &agentStatusTestClock{now: time.Unix(1789364620, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", Directory: "/repo/current", Title: "OC | Fix live ps inventory and token ident…", TmuxSession: "current-opencode"}}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "cursor.json"),
		EpochStartedAt:   1789364500,
		OwnerGeneration:  1,
	})
	batch, receipts, collectErr := collector.collect(context.Background(), 1789364620, 10)
	if collectErr != nil {
		t.Fatal(collectErr)
	}
	if len(batch.UsageAtoms) != 3 || len(batch.UnavailableSpans) != 0 {
		t.Fatalf("current OpenCode batch = %#v unavailable=%#v", batch.UsageAtoms, batch.UnavailableSpans)
	}
	for _, receipt := range receipts {
		receipt.rollback()
	}
}

func TestAgentTokenCollectorDeduplicatesInvalidOpenCodeRecordsBeforeAppend(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeFixture(t)
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{
				{Kind: "opencode", OpenCodeSession: "ses-live", TmuxSession: "invalid-one"},
				{Kind: "opencode", OpenCodeSession: "ses-live", TmuxSession: "invalid-two"},
				{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "valid-root"},
			}, nil
		},
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{{
				Path:     filepath.Join(root, "missing-opencode-source.jsonl"),
				Provider: "opencode",
				SourceID: "opencode:ses-live",
			}}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "cursor.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})

	batch, receipts, collectErr := collector.collect(context.Background(), 1788307300, 10)
	if collectErr != nil {
		t.Fatal(collectErr)
	}
	if len(batch.UsageAtoms) != 3 {
		t.Fatalf("valid OpenCode root atoms = %d, want 3", len(batch.UsageAtoms))
	}
	if len(batch.UnavailableSpans) != 1 {
		t.Fatalf("deduplicated unavailable spans = %#v, want one span", batch.UnavailableSpans)
	}
	if batch.UnavailableSpans[0].SourceID != "opencode:ses-live" || batch.UnavailableSpans[0].Reason != "OpenCode root session not found" {
		t.Fatalf("invalid OpenCode span = %#v", batch.UnavailableSpans[0])
	}
	if _, err := writer.AppendBatch(context.Background(), batch); err != nil {
		t.Fatalf("append with duplicate invalid OpenCode records: %v", err)
	}
	for _, receipt := range receipts {
		receipt.commit()
	}
}

func TestAgentTokenCollectorAttributesCopiedActualOpenCodeRootAndChildren(t *testing.T) {
	root := t.TempDir()
	databasePath := copyActualOpenCodeFixture(t)
	clock := &agentStatusTestClock{now: time.Unix(1788307360, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			// No pane or tmux session: process identity must remain the chart key.
			return []AgentRecord{{PID: 4242, StartedIdentity: "proc:4242", Kind: "opencode", OpenCodeSession: "ses-root"}}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "cursor.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})
	collector.RunOnce(context.Background())

	database, err := OpenReadOnly(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(atoms) != 6 {
		t.Fatalf("root and child usage atoms = %d, want 6", len(atoms))
	}
	threads := map[string]bool{}
	for _, atom := range atoms {
		threads[atom.Payload["thread_id"].(string)] = true
		if atom.Payload["agent_id"] != "opencode-process:4242:proc:4242" {
			t.Fatalf("pane-less root attribution = %#v", atom.Payload)
		}
	}
	if !threads["ses-root"] || !threads["ses-child"] {
		t.Fatalf("child thread identities = %#v", threads)
	}
	rates, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(1788307301, 0), RateWindow: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	if rates.TokenRates.TotalPerSecond <= 0 {
		t.Fatalf("OpenCode token rate = %+v", rates.TokenRates)
	}
}

func TestAgentTokenCollectorKeepsFreshRowsWithManyHistoricalOpenCodeChildren(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeSQLFixture(t, "opencode_many_children.sql")
	clock := &agentStatusTestClock{now: time.Unix(1788307360, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{PID: 5252, StartedIdentity: "proc:5252", Kind: "opencode", OpenCodeSession: "ses-many-root"}}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "opencode-cursors.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})

	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 6 {
		t.Fatalf("fresh root/child atoms accepted = %d, want 6; counters=%+v", got, collector.Counters())
	}
	database, err := OpenReadOnly(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	coverage, err := database.ReadCoverageEpochs(context.Background(), 1788307300, 1788307370, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(coverage) != 0 {
		t.Fatalf("truncated tree reported complete coverage = %#v", coverage)
	}
	unavailable, err := database.ReadUnavailableSpans(context.Background(), 1788307300, 1788307370, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(unavailable) != 1 || unavailable[0].Reason != "opencode-session-tree-truncated" {
		t.Fatalf("truncated tree no-data = %#v", unavailable)
	}
	rates, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(1788307360, 0), RateWindow: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(rates.TokenRates.TotalPerSecond-0.4) > 1e-9 || math.Abs(rates.TokenRates.InputPerSecond-7.0/60.0) > 1e-9 || math.Abs(rates.TokenRates.OutputPerSecond-11.0/60.0) > 1e-9 || math.Abs(rates.TokenRates.CachePerSecond-6.0/60.0) > 1e-9 {
		t.Fatalf("fresh root/child token rates = %+v", rates.TokenRates)
	}
	snapshot, err := BuildSnapshot(context.Background(), database, SnapshotRequest{
		Start: 1788307300, End: 1788307370, Resolution: 10,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Materialized.NoData) != 1 || snapshot.Materialized.NoData[0].Reason != "opencode-session-tree-truncated" {
		t.Fatalf("truncated tree materialized no-data = %#v", snapshot.Materialized.NoData)
	}
	if snapshot.Materialized.Buckets[6].Complete {
		t.Fatalf("truncated tree bucket claimed complete: %#v", snapshot.Materialized.Buckets[6])
	}
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	threads := map[string]bool{}
	for _, atom := range atoms {
		threads[atom.Payload["thread_id"].(string)] = true
	}
	if !threads["ses-many-root"] || !threads["ses-many-fresh"] {
		t.Fatalf("fresh root/child thread identities = %#v", threads)
	}
}

func TestAgentTokenCollectorCommitsFreshOpenCodeAtomsWhenPartialMetadataHasDuplicateInvalidRows(t *testing.T) {
	root := t.TempDir()
	databasePath := writeOpenCodeTruncatedChildren(t, 65, 32)
	clock := &agentStatusTestClock{now: time.Unix(1788307360, 0)}
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{
				{Kind: "opencode", OpenCodeSession: "missing-root", TmuxSession: "invalid-one"},
				{Kind: "opencode", OpenCodeSession: "missing-root", TmuxSession: "invalid-two"},
				{Kind: "opencode", OpenCodeSession: "root", TmuxSession: "fresh-opencode"},
			}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "opencode-cursors.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})

	_, receipts, collectErr := collector.collect(context.Background(), 1788307360, 10)
	if collectErr != nil {
		t.Fatal(collectErr)
	}
	for _, receipt := range receipts {
		receipt.rollback()
	}
	collector.RunOnce(context.Background())
	counters := collector.Counters()
	if counters.UsageAccepted == 0 || counters.UnavailableAccepted != 2 || counters.Errors != 0 {
		t.Fatalf("fresh atoms and partial metadata counters = %+v", counters)
	}
	database, err := OpenReadOnly(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(atoms) == 0 {
		t.Fatal("durable fresh OpenCode atoms are missing")
	}
	unavailable, err := database.ReadUnavailableSpans(context.Background(), 1788307300, 1788307370, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(unavailable) != 2 {
		t.Fatalf("durable partial OpenCode metadata = %#v", unavailable)
	}
	reasons := map[string]bool{}
	for _, span := range unavailable {
		reasons[span.Reason] = true
	}
	if !reasons["OpenCode root session not found"] || !reasons["opencode-session-tree-truncated"] {
		t.Fatalf("partial OpenCode metadata reasons = %#v", unavailable)
	}
	rates, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(1788307360, 0), RateWindow: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	if rates.TokenRates.TotalPerSecond <= 0 || rates.TokenRates.InputPerSecond <= 0 || rates.TokenRates.OutputPerSecond <= 0 {
		t.Fatalf("fresh OpenCode token rates = %+v", rates.TokenRates)
	}
}

func writeOpenCodeTruncatedChildren(t *testing.T, children, partsPerChild int) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "opencode-truncated-children.db")
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
		`INSERT INTO session VALUES ('root', '/repo', 'build', '{"providerID":"openai","id":"gpt"}', 1, 1788307360000, 0, NULL)`,
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
	for childIndex := 0; childIndex < children; childIndex++ {
		childID := fmt.Sprintf("child-%03d", childIndex)
		updated := int64(childIndex + 1)
		if childIndex == children-1 {
			childID = "fresh-child"
			updated = 1788307359000
		}
		if _, err := tx.Exec(`INSERT INTO session VALUES (?, '/repo', 'build', '{"providerID":"openai","id":"gpt"}', ?, ?, 0, 'root')`, childID, updated, updated); err != nil {
			t.Fatal(err)
		}
		for partIndex := 0; partIndex < partsPerChild; partIndex++ {
			partTime := updated
			if childIndex != children-1 {
				partTime = int64(partIndex + 1)
			}
			messageID := fmt.Sprintf("msg-%03d-%02d", childIndex, partIndex)
			partID := fmt.Sprintf("part-%03d-%02d", childIndex, partIndex)
			if _, err := tx.Exec(`INSERT INTO message VALUES (?, ?, ?, ?, ?)`, messageID, childID, partTime, partTime, `{"role":"assistant","providerID":"openai","modelID":"gpt"}`); err != nil {
				t.Fatal(err)
			}
			if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)`, partID, messageID, childID, partTime, partTime, `{"type":"step-finish","tokens":{"input":2,"output":3,"cache":{"read":4}}}`); err != nil {
				t.Fatal(err)
			}
		}
	}
	if _, err := tx.Exec(`INSERT INTO message VALUES ('msg-root-fresh', 'root', 1788307360000, 1788307360000, '{"role":"assistant","providerID":"openai","modelID":"gpt"}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := tx.Exec(`INSERT INTO part VALUES ('part-root-fresh', 'msg-root-fresh', 'root', 1788307360000, 1788307360000, '{"type":"step-finish","tokens":{"input":5,"output":7,"cache":{"read":11}}}')`); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestAgentTokenCollectorRebuildsOpenCodeChildrenBetweenCollections(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeSQLFixture(t, "opencode_many_children.sql")
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &agentStatusTestClock{now: time.Unix(1788307360, 0)}
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{PID: 7272, StartedIdentity: "proc:7272", Kind: "opencode", OpenCodeSession: "ses-many-root"}}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "opencode-cursors.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})

	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 6 {
		t.Fatalf("first collection accepted = %d, want 6", got)
	}
	mutateSQLite(t, databasePath, `INSERT INTO session (id, project_id, workspace_id, parent_id, slug, directory, path, title, version, share_url, summary_additions, summary_deletions, summary_files, summary_diffs, metadata, cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, revert, permission, agent, model, time_created, time_updated, time_compacting, time_archived) VALUES ('ses-many-new', 'global', NULL, 'ses-many-root', 'many-new', '/repo/many', NULL, 'new child', '1.18.30', NULL, NULL, NULL, NULL, NULL, NULL, 0, 0, 0, 0, 0, 0, NULL, NULL, 'build', '{"providerID":"inferencehub","id":"openai/openai/gpt-5.6-luna"}', 1788307361000, 1788307361000, NULL, NULL)`)
	mutateSQLite(t, databasePath, `INSERT INTO message VALUES ('msg-many-new', 'ses-many-new', 1788307361000, 1788307361000, '{"role":"assistant","providerID":"inferencehub","modelID":"openai/openai/gpt-5.6-luna"}')`)
	mutateSQLite(t, databasePath, `INSERT INTO part VALUES ('part-many-new', 'msg-many-new', 'ses-many-new', 1788307361000, 1788307361000, '{"type":"step-finish","tokens":{"total":31,"input":7,"output":11,"reasoning":0,"cache":{"read":13,"write":0}},"cost":0}')`)
	readDB, err := OpenOpenCodeReadOnly(context.Background(), databasePath)
	if err != nil {
		t.Fatal(err)
	}
	tree, err := readDB.ReadSessionTreeWindow(context.Background(), "ses-many-root", MaxOpenCodeSessionTree)
	if err != nil {
		t.Fatal(err)
	}
	if len(tree.Sessions) != MaxOpenCodeSessionTree || tree.Sessions[1] != "ses-many-new" {
		t.Fatalf("rebuilt tree = %#v", tree)
	}
	readDB.Close()

	clock.now = time.Unix(1788307370, 0)
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 9 {
		t.Fatalf("second collection accepted = %d, want 9", got)
	}
	database, err := OpenReadOnly(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, atom := range atoms {
		if atom.Payload["thread_id"] == "ses-many-new" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("second collection omitted fresh child atom: %#v", atoms)
	}
	rates, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(1788307370, 0), RateWindow: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(rates.TokenRates.TotalPerSecond-45.0/60.0) > 1e-9 || math.Abs(rates.TokenRates.InputPerSecond-11.0/60.0) > 1e-9 || math.Abs(rates.TokenRates.OutputPerSecond-17.0/60.0) > 1e-9 || math.Abs(rates.TokenRates.CachePerSecond-17.0/60.0) > 1e-9 {
		t.Fatalf("fresh child token rates = %+v", rates.TokenRates)
	}
}

func TestAgentTokenCollectorManyChildrenCompletesBeforeSourceDeadline(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeSQLFixture(t, "opencode_many_children.sql")
	clock := &agentStatusTestClock{now: time.Unix(1788307360, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{PID: 6262, StartedIdentity: "proc:6262", Kind: "opencode", OpenCodeSession: "ses-many-root"}}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "opencode-cursors.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	batch, receipts, err := collector.collect(ctx, 1788307360, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.UsageAtoms) == 0 {
		t.Fatalf("bounded large-history collection produced no atoms: unavailable=%#v", batch.UnavailableSpans)
	}
	if _, err := writer.AppendBatch(ctx, batch); err != nil {
		t.Fatal(err)
	}
	for _, receipt := range receipts {
		receipt.commit()
	}
}

func TestAgentTokenCollectorManyOpenCodeRootsEmitsFreshAtomsBeforeSourceDeadline(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode-many-roots.db")
	writeOpenCodeManyRootHistory(t, databasePath, 64, 5000)
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			records := make([]AgentRecord, 0, 64)
			for index := 0; index < 64; index++ {
				records = append(records, AgentRecord{Kind: "opencode", OpenCodeSession: fmt.Sprintf("root-%02d", index), TmuxSession: fmt.Sprintf("opencode-%02d", index)})
			}
			return records, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "cursor.json"),
		EpochStartedAt:   1,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	started := time.Now()
	batch, receipts, err := collector.collect(ctx, 10000, 10)
	if err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed >= 2*time.Second {
		t.Fatalf("fresh multi-root collection took %s", elapsed)
	}
	if len(batch.UsageAtoms) == 0 {
		t.Fatalf("fresh multi-root collection produced no atoms: unavailable=%#v", batch.UnavailableSpans)
	}
	for _, receipt := range receipts {
		receipt.rollback()
	}
}

func TestAgentTokenCollectorRunOnceCommitsOpenCodeBeforeHugeHistoricalCodexDiscovery(t *testing.T) {
	root := t.TempDir()
	codexRoot := filepath.Join(root, "codex", "sessions")
	if err := os.MkdirAll(filepath.Join(codexRoot, "2099", "12", "31"), 0o700); err != nil {
		t.Fatal(err)
	}
	// Sparse historical rollouts model the production multi-gigabyte Codex
	// root without making the test allocate that storage. The newest directory
	// contains a current candidate; old directories must remain outside the
	// discovery budget.
	fresh := filepath.Join(codexRoot, "2099", "12", "31", "rollout-fresh.jsonl")
	writeJSONL(t, fresh, usageCodex(1788307359, 7, 2, 5))
	for year := 2000; year < 2011; year++ {
		path := filepath.Join(codexRoot, fmt.Sprintf("%04d", year), "01", "01", "rollout-historical.jsonl")
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		file, err := os.Create(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := file.Truncate(1 << 30); err != nil {
			file.Close()
			t.Fatal(err)
		}
		if err := file.Close(); err != nil {
			t.Fatal(err)
		}
	}
	databasePath := copyOpenCodeFixture(t)
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{
				{Kind: "codex", TmuxSession: "historical-codex"},
				{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "fresh-opencode"},
			}, nil
		},
		CodexTranscriptRoot:         codexRoot,
		OpenCodeDatabase:            databasePath,
		CursorPath:                  filepath.Join(root, "cursor.json"),
		EpochStartedAt:              1788307200,
		OwnerGeneration:             1,
		TranscriptDiscoveryMaxDirs:  4,
		TranscriptDiscoveryMaxFiles: 4,
		TranscriptDiscoveryMaxBytes: 8 * 1024 * 1024,
		TranscriptDiscoveryTimeout:  50 * time.Millisecond,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	started := time.Now()
	collector.RunOnce(ctx)
	if elapsed := time.Since(started); elapsed >= 500*time.Millisecond {
		t.Fatalf("RunOnce exceeded deadline: %s counters=%+v", elapsed, collector.Counters())
	}
	if got := collector.Counters().UsageAccepted; got != 3 {
		t.Fatalf("OpenCode atoms committed before transcript discovery: %d counters=%+v", got, collector.Counters())
	}
	database, err := OpenReadOnly(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(atoms) != 3 {
		t.Fatalf("durable OpenCode atoms = %d, want 3", len(atoms))
	}
}

func TestAgentTokenCollectorOpenCodeSurvivesSlowFullInventory(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeFixture(t)
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	fullInventoryStarted := make(chan struct{})
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			close(fullInventoryStarted)
			time.Sleep(2 * time.Second)
			return nil, []error{errors.New("synthetic full inventory failure")}
		},
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{PID: 9182, StartedIdentity: "proc:9182", Kind: "opencode", OpenCodeSession: "ses-1"}}, nil
		},
		OpenCodeDatabase:          databasePath,
		CursorPath:                filepath.Join(root, "cursor.json"),
		EpochStartedAt:            1788307200,
		OwnerGeneration:           1,
		OpenCodeInventoryTimeout:  100 * time.Millisecond,
		OpenCodeCollectionTimeout: time.Second,
		AuxiliaryInventoryTimeout: 10 * time.Millisecond,
	})
	started := time.Now()
	collector.RunOnce(context.Background())
	if elapsed := time.Since(started); elapsed >= time.Second {
		t.Fatalf("OpenCode append waited for full inventory: %s counters=%+v", elapsed, collector.Counters())
	}
	select {
	case <-fullInventoryStarted:
	default:
		t.Fatal("full inventory was not attempted as optional enrichment")
	}
	counters := collector.Counters()
	if counters.UsageAccepted != 3 || counters.AuxiliaryInventoryTimeouts != 1 || counters.AuxiliaryInventoryErrors == 0 {
		t.Fatalf("resilient collection counters = %+v", counters)
	}

	database, err := OpenReadOnly(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil || len(atoms) != 3 {
		t.Fatalf("durable OpenCode atoms = %d err=%v", len(atoms), err)
	}
	rates, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(3, 0), RateWindow: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	if rates.TokenRates.TotalPerSecond <= 0 {
		t.Fatalf("durable OpenCode token rates = %+v", rates.TokenRates)
	}
}

func TestRecentTranscriptCandidatesHonorsContextAndBounds(t *testing.T) {
	root := t.TempDir()
	for index := 0; index < 12; index++ {
		path := filepath.Join(root, fmt.Sprintf("%02d", index), "rollout.jsonl")
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := recentTranscriptCandidates(ctx, root, transcriptDiscoveryBounds{maxFiles: 20, maxDirs: 20, maxBytes: 1 << 20}); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled discovery error = %v", err)
	}
	candidates, err := recentTranscriptCandidates(context.Background(), root, transcriptDiscoveryBounds{maxFiles: 2, maxDirs: 4, maxBytes: 1 << 20})
	if err == nil || len(candidates) == 0 || len(candidates) > 2 {
		t.Fatalf("bounded candidates = %#v err=%v", candidates, err)
	}
}

func TestAgentTokenCollectorStartsWithCopiedPythonFixtures(t *testing.T) {
	root := t.TempDir()
	claudePath := copyObservedTranscript(t, "claude", "projects/live.jsonl")
	codexPath := copyObservedTranscript(t, "codex", "sessions/live.jsonl")
	claudeRoot := filepath.Dir(claudePath)
	codexRoot := filepath.Dir(codexPath)
	databasePath := copyOpenCodeFixture(t)
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	home := filepath.Join(root, "home")
	t.Setenv("HOME", home)
	t.Setenv("CODEX_HOME", filepath.Join(home, ".codex"))
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{
				{Kind: "claude", TmuxSession: "yo7221"},
				{Kind: "codex", TmuxSession: "yo7222"},
				{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "yo7222"},
			}, nil
		},
		ClaudeTranscriptRoot: claudeRoot,
		CodexTranscriptRoot:  codexRoot,
		OpenCodeDatabase:     databasePath,
		CursorPath:           filepath.Join(root, "cursor.json"),
		EpochStartedAt:       1788307200,
		OwnerGeneration:      1,
	})
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 27 {
		t.Fatalf("copied fixture usage rows = %d counters=%+v", got, collector.Counters())
	}
	database, err := OpenReadOnly(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil || len(atoms) != 27 {
		t.Fatalf("copied fixture usage atoms = %d err=%v", len(atoms), err)
	}
	snapshot, err := BuildSnapshot(context.Background(), database, SnapshotRequest{Start: 1788307200, End: 1788307360, Resolution: 60})
	if err != nil {
		t.Fatal(err)
	}
	series := map[string]float64{}
	for _, bucket := range snapshot.Materialized.Buckets {
		for _, item := range bucket.Series {
			series[item.Name] = item.Value
		}
	}
	if series["agent_tokens_per_minute:yo7221"] <= 0 || series["agent_tokens_per_minute:yo7222"] <= 0 {
		t.Fatalf("copied fixture token series = %#v", series)
	}
}

func openCodePartsForTest(t *testing.T, path, session string) ([]OpenCodeStepFinishPart, error) {
	t.Helper()
	db, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		return nil, err
	}
	defer db.Close()
	return db.ReadStepFinishParts(context.Background(), session, 0, 1788307300001, MaxOpenCodePartReadLimit)
}

func createOpenCodeFixtureWithTitle(t *testing.T, path, sessionID, directory, title string, updated int64) {
	t.Helper()
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, title TEXT)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		fmt.Sprintf(`INSERT INTO session VALUES ('%s', '%s', 'build', '{"providerID":"openai","id":"gpt"}', %d, %d, 0, '%s')`, sessionID, directory, updated-1000, updated, title),
		fmt.Sprintf(`INSERT INTO message VALUES ('msg-normal', '%s', %d, %d, '{"role":"assistant"}')`, sessionID, updated, updated),
		fmt.Sprintf(`INSERT INTO part VALUES ('part-normal', 'msg-normal', '%s', %d, %d, '{"type":"step-finish","tokens":{"total":10,"input":3,"output":5,"cache":{"read":2}}}')`, sessionID, updated, updated),
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
}

func copyActualOpenCodeFixture(t *testing.T) string {
	t.Helper()
	return copyOpenCodeSQLFixture(t, "opencode_parent_child.sql")
}

func copyOpenCodeSQLFixture(t *testing.T, name string) string {
	t.Helper()
	sqlFixture, err := os.ReadFile(testsupport.RepoPath(t, filepath.Join("internal", "stats", "testdata", name)))
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "opencode.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(string(sqlFixture)); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	return path
}
