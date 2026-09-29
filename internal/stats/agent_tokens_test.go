package stats

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/keivenchang/yolomux/internal/testsupport"

	_ "github.com/mattn/go-sqlite3"
)

func TestAgentTokenCollectorBoundsLongOpenCodeEventIDLikePython(t *testing.T) {
	longSessionID := strings.Repeat("session-", 80)
	longPartID := strings.Repeat("part-", 80)
	raw := "opencode:" + longSessionID + ":" + longPartID + ":input"
	digest := sha256.Sum256([]byte(raw))
	want := "opencode:" + hex.EncodeToString(digest[:]) + ":input"
	if len([]byte(raw)) <= MaxEventIDBytes {
		t.Fatalf("synthetic identity is not overlong: %d bytes", len([]byte(raw)))
	}
	collector := AgentTokenCollector{}
	cursor := openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}}
	var batch AppendBatch
	collector.addOpenCodePart(&batch, cursor, OpenCodeStepFinishPart{
		PartID: longPartID, SessionID: longSessionID, CompletedAt: 1000,
		Provider: "fixture", Model: "fixture-model",
		Tokens: OpenCodeTokenSnapshot{Input: 1, Present: map[string]bool{"input": true}},
	}, "fixture-agent", "fixture-root", 1000, 10)
	if len(batch.UsageAtoms) != 1 {
		t.Fatalf("long OpenCode identity produced %d usage atoms", len(batch.UsageAtoms))
	}
	if got := batch.UsageAtoms[0].EventID; got != want {
		t.Fatalf("long OpenCode event id = %q, want Python-compatible hash %q", got, want)
	}
	if len([]byte(batch.UsageAtoms[0].EventID)) > MaxEventIDBytes {
		t.Fatalf("hashed OpenCode event id is still too long: %d bytes", len([]byte(batch.UsageAtoms[0].EventID)))
	}
}

type countingTokenWriter struct{}

func (countingTokenWriter) AppendBatch(_ context.Context, batch AppendBatch) (AppendResult, error) {
	return AppendResult{UsageAccepted: len(batch.UsageAtoms), CoverageChanged: len(batch.CoverageEpochs), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
}

type tokenWriterFunc func(context.Context, AppendBatch) (AppendResult, error)

func (f tokenWriterFunc) AppendBatch(ctx context.Context, batch AppendBatch) (AppendResult, error) {
	return f(ctx, batch)
}

type failingCodexForkRepairMarkerStore struct {
	*TranscriptCursorStore
}

func (s failingCodexForkRepairMarkerStore) CommitBatchWithCodexForkRepairRoots([]transcriptCursorChange, []string) error {
	return errors.New("synthetic fork repair transaction failure")
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
	if first.OpenCodeDatabaseOpens != 1 || first.OpenCodeDatabaseReuses != 0 {
		t.Fatalf("first OpenCode database lifecycle = %+v", first)
	}
	if first.OpenCodeSQLiteQueries == 0 || first.OpenCodeSQLiteTotal <= 0 || first.OpenCodeSQLiteLast <= 0 {
		t.Fatalf("first OpenCode SQLite metrics = %+v, want measured query work", first)
	}
	if first.LastCadence != AgentTokenActiveCadence {
		t.Fatalf("first token cadence = %s, want %s", first.LastCadence, AgentTokenActiveCadence)
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
	third := collector.Counters()
	if got := third.UsageAccepted; got != 31 {
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
	for _, atom := range atoms {
		if atom.Payload["effort"] != "unknown" || atom.Payload["pricing_profile"] != "default" || atom.Payload["execution_source"] == nil {
			t.Fatalf("usage atom omitted Python-compatible source metadata: %+v", atom)
		}
		if strings.HasPrefix(atom.EventID, "opencode:") {
			if atom.Payload["execution_source"] != "opencode" || atom.Payload["thread_id"] != "ses-1" || atom.Payload["service_tier"] != "default" {
				t.Fatalf("OpenCode usage metadata = %#v", atom.Payload)
			}
			continue
		}
		if atom.Payload["service_tier"] != "default" {
			t.Fatalf("transcript usage metadata omitted the default service tier: %+v", atom)
		}
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

func TestAgentTokenCollectorKeepsFiveSecondBackgroundCadenceWithoutDemand(t *testing.T) {
	if AgentTokenActiveCadence != 5*time.Second || AgentTokenIdleCadence != 5*time.Second {
		t.Fatalf("token active/idle cadence = %s/%s, want 5s/5s", AgentTokenActiveCadence, AgentTokenIdleCadence)
	}
	root := t.TempDir()
	transcriptPath := copyObservedTranscript(t, "codex", "codex/sessions/2026/09/02/background.jsonl")
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	writerClosed := false
	t.Cleanup(func() {
		if !writerClosed {
			_ = writer.Close()
		}
	})
	clock := realHostCollectorClock{}
	var transcriptCalls atomic.Uint64
	var firstTranscriptCall, secondTranscriptCall atomic.Int64
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "codex", TmuxSession: "fixture"}}, nil
		},
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			now := time.Now().UnixNano()
			switch transcriptCalls.Add(1) {
			case 1:
				firstTranscriptCall.Store(now)
			case 2:
				secondTranscriptCall.Store(now)
			}
			return []TranscriptSource{{Path: transcriptPath, Provider: "codex", SourceID: "fixture", ThreadID: "fixture"}}, nil
		},
		ActiveCadence: 10 * time.Second, IdleCadence: 20 * time.Millisecond,
		ActiveFunc:           func() bool { return false },
		CursorPath:           filepath.Join(root, "opencode-cursors.json"),
		TranscriptCursorPath: filepath.Join(root, "transcript-cursors.json"),
		EpochID:              "background-token-test", EpochStartedAt: 1788307200, OwnerGeneration: 1,
	})
	ctx, cancel := context.WithCancel(context.Background())
	if err := collector.Start(ctx); err != nil {
		cancel()
		t.Fatal(err)
	}
	deadline := time.NewTimer(2 * time.Second)
	ticker := time.NewTicker(time.Millisecond)
	for collector.Counters().Cycles < 1 || collector.Counters().DurationTotal <= 0 {
		select {
		case <-deadline.C:
			cancel()
			collector.Stop()
			t.Fatal("token collector did not complete a second undemanded background cycle")
		case <-ticker.C:
		}
	}
	firstCompletedDuration := collector.Counters().DurationTotal
	for collector.Counters().Cycles < 2 || collector.Counters().DurationTotal <= firstCompletedDuration {
		select {
		case <-deadline.C:
			cancel()
			collector.Stop()
			t.Fatal("token collector did not complete a second undemanded idle cycle")
		case <-ticker.C:
		}
	}
	cancel()
	collector.Stop()
	deadline.Stop()
	ticker.Stop()

	counters := collector.Counters()
	if counters.LastCadence != 20*time.Millisecond {
		t.Fatalf("undemanded token cadence = %s, want 20ms fixture idle cadence", counters.LastCadence)
	}
	callGap := time.Duration(secondTranscriptCall.Load() - firstTranscriptCall.Load())
	if transcriptCalls.Load() < 2 || callGap < 15*time.Millisecond || callGap >= time.Second {
		t.Fatalf("completed idle transcript calls = %d with gap %s, want two calls separated by the 20ms idle cadence", transcriptCalls.Load(), callGap)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writerClosed = true
	database, err := OpenReadOnly(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
	if err != nil || len(atoms) == 0 {
		t.Fatalf("background collection usage atoms = %d, err=%v", len(atoms), err)
	}
}

func TestBoundedTranscriptSourcePagesRotatePastTheSourceLimit(t *testing.T) {
	sources := make([]TranscriptSource, 5)
	for index := range sources {
		path := filepath.Join(t.TempDir(), fmt.Sprintf("source-%d.jsonl", index))
		sources[index] = TranscriptSource{Path: path, Provider: "codex", SourceID: path}
	}
	startKey := ""
	visited := make(map[string]bool, len(sources))
	for cycle := 0; cycle < 3; cycle++ {
		ordered, page := boundedTranscriptSourcePage(sources, startKey, 2)
		if len(page) != 2 {
			t.Fatalf("cycle %d bounded page size = %d, want 2", cycle, len(page))
		}
		for _, source := range page {
			visited[transcriptCursorKey(source)] = true
		}
		startKey = nextTranscriptSourceKey(ordered, transcriptCursorKey(page[len(page)-1]))
	}
	if len(visited) != len(sources) {
		t.Fatalf("rotating source pages visited %d of %d candidates: %v", len(visited), len(sources), visited)
	}
}

func TestAgentTokenCollectorReusesOpenCodeDatabaseAcrossCycles(t *testing.T) {
	root := t.TempDir()
	opencodePath := copyOpenCodeFixture(t)
	collector := NewAgentTokenCollector(countingTokenWriter{}, realHostCollectorClock{}, AgentTokenCollectorConfig{
		OpenCodeDatabase: opencodePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode"}}, nil
		},
		CursorPath: filepath.Join(root, "cursor.json"), SkipTranscriptScan: true,
	})
	collector.RunOnce(context.Background())
	collector.RunOnce(context.Background())
	counters := collector.Counters()
	if counters.OpenCodeDatabaseOpens != 1 || counters.OpenCodeDatabaseReuses != 1 {
		t.Fatalf("OpenCode database lifecycle = %+v", counters)
	}
	for _, phase := range []string{"identity_cursor", "database_open", "claims", "session_tree", "step_finish", "cursor_prepare", "append"} {
		timing, ok := counters.OpenCodePhases[phase]
		if !ok || timing.Runs != 2 || timing.Last < 0 || timing.Total < timing.Last {
			t.Fatalf("OpenCode phase timing %q = %+v (all phases should run for both cycles)", phase, counters.OpenCodePhases)
		}
	}
	t.Logf("synthetic OpenCode phase timings: %+v", counters.OpenCodePhases)
	snapshot := collector.Snapshot()
	if len(snapshot.OpenCodePhases) != len(counters.OpenCodePhases) {
		t.Fatalf("snapshot omitted OpenCode phase timings: %+v", snapshot)
	}
	collector.Stop()
}

func TestAgentTokenCollectorReplaysOpenCodeAfterStatsDatabaseReplacement(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	opencodePath := copyOpenCodeFixture(t)
	statsPath := filepath.Join(root, "stats.sqlite3")
	replacementDir := filepath.Join(root, "replacement")
	if err := os.Mkdir(replacementDir, 0o700); err != nil {
		t.Fatal(err)
	}
	replacementPath := filepath.Join(replacementDir, "stats.sqlite3")
	cursorPath := filepath.Join(root, "opencode-cursor.json")
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	config := AgentTokenCollectorConfig{
		OpenCodeDatabase: opencodePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "synthetic-opencode"}}, nil
		},
		CursorPath: cursorPath, SkipTranscriptScan: true,
	}

	writerA, err := OpenWriter(ctx, statsPath)
	if err != nil {
		t.Fatal(err)
	}
	collectorA := NewAgentTokenCollector(writerA, clock, config)
	collectorA.RunOnce(ctx)
	first, err := OpenReadOnly(ctx, statsPath)
	if err != nil {
		t.Fatal(err)
	}
	atomsA, err := first.ReadUsageAtoms(ctx, 0, 2e9, MaxFactReadLimit)
	_ = first.Close()
	if err != nil || len(atomsA) == 0 {
		t.Fatalf("synthetic stats DB A OpenCode atoms = %d, err=%v", len(atomsA), err)
	}
	cursorBeforeOrdinaryAppend, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writerA.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{{
		EventID: "synthetic-unrelated-append", Direction: "input", Modality: "text",
		CacheRole: "none", Unit: "tokens", ObservedAt: 1788307301,
		Payload: map[string]any{"quantity": 1.0, "provider": "fixture", "model": "fixture-model"},
	}}}); err != nil {
		t.Fatalf("ordinary synthetic stats DB append: %v", err)
	}
	clock.now = time.Unix(1788307310, 0)
	collectorA.RunOnce(ctx)
	cursorAfterOrdinaryAppend, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(cursorAfterOrdinaryAppend, cursorBeforeOrdinaryAppend) {
		t.Fatalf("ordinary append to stats DB A reset OpenCode cursor: before=%+v after=%+v", cursorBeforeOrdinaryAppend, cursorAfterOrdinaryAppend)
	}
	collectorA.Stop()
	if err := writerA.Close(); err != nil {
		t.Fatal(err)
	}
	statsAInfo, err := os.Stat(statsPath)
	if err != nil {
		t.Fatal(err)
	}

	writerBSeed, err := OpenWriter(ctx, replacementPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := writerBSeed.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacementPath, statsPath); err != nil {
		t.Fatalf("atomically replace synthetic stats DB A with B: %v", err)
	}
	statsBInfo, err := os.Stat(statsPath)
	if err != nil {
		t.Fatal(err)
	}
	statsADevice, statsAInode := fileIdentity(statsAInfo)
	statsBDevice, statsBInode := fileIdentity(statsBInfo)
	if statsADevice == statsBDevice && statsAInode == statsBInode {
		t.Fatalf("synthetic stats DB replacement retained identity %d:%d", statsBDevice, statsBInode)
	}

	writerB, err := OpenWriter(ctx, statsPath)
	if err != nil {
		t.Fatal(err)
	}
	collectorB := NewAgentTokenCollector(writerB, clock, config)
	collectorB.RunOnce(ctx)
	collectorB.Stop()
	if err := writerB.Close(); err != nil {
		t.Fatal(err)
	}
	readerB, err := OpenReadOnly(ctx, statsPath)
	if err != nil {
		t.Fatal(err)
	}
	atomsB, err := readerB.ReadUsageAtoms(ctx, 0, 2e9, MaxFactReadLimit)
	_ = readerB.Close()
	if err != nil || len(atomsB) != len(atomsA) {
		t.Fatalf("fresh synthetic stats DB B replayed OpenCode atoms = %d, err=%v; want %d", len(atomsB), err, len(atomsA))
	}
}

func TestAgentTokenCollectorReopensReplacedOpenCodeDatabase(t *testing.T) {
	root := t.TempDir()
	opencodePath := copyOpenCodeFixture(t)
	replacement := copyOpenCodeFixture(t)
	collector := NewAgentTokenCollector(countingTokenWriter{}, realHostCollectorClock{}, AgentTokenCollectorConfig{
		OpenCodeDatabase: opencodePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode"}}, nil
		},
		CursorPath: filepath.Join(root, "cursor.json"), SkipTranscriptScan: true,
	})
	collector.RunOnce(context.Background())
	if err := os.Rename(opencodePath, opencodePath+".old"); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacement, opencodePath); err != nil {
		t.Fatal(err)
	}
	collector.RunOnce(context.Background())
	counters := collector.Counters()
	if counters.OpenCodeDatabaseOpens != 2 || counters.OpenCodeDatabaseReuses != 0 {
		t.Fatalf("replaced OpenCode database lifecycle = %+v", counters)
	}
	collector.Stop()
}

func TestAgentTokenCollectorReopensWhenOpenCodeWALAppears(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeFixture(t)
	mainBefore, err := os.Stat(databasePath)
	if err != nil {
		t.Fatal(err)
	}
	clock := &agentStatusTestClock{now: time.Unix(3, 0)}
	var emittedEventIDs []string
	collector := NewAgentTokenCollector(tokenWriterFunc(func(_ context.Context, batch AppendBatch) (AppendResult, error) {
		for _, atom := range batch.UsageAtoms {
			emittedEventIDs = append(emittedEventIDs, atom.EventID)
		}
		return AppendResult{UsageAccepted: len(batch.UsageAtoms)}, nil
	}), clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode"}}, nil
		},
		CursorPath: filepath.Join(root, "cursor.json"), SkipTranscriptScan: true,
	})
	collector.RunOnce(context.Background())
	if collector.openCodeDatabase == nil {
		t.Fatal("initial OpenCode database was not cached")
	}
	firstDatabase := collector.openCodeDatabase
	cursorPath := filepath.Join(root, "cursor.json")
	initialCursor, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if !initialCursor.RawScan.HasScan || initialCursor.RawScan.ScannedThroughRowID < initialCursor.RawScan.HighWaterRowID {
		t.Fatalf("initial synthetic history scan did not complete: %+v", initialCursor.RawScan)
	}
	if len(emittedEventIDs) != 3 {
		t.Fatalf("initial synthetic usage events = %v, want 3", emittedEventIDs)
	}
	initialEventIDs := append([]string(nil), emittedEventIDs...)

	writer, err := sql.Open("sqlite3", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.Exec("PRAGMA journal_mode=WAL"); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Exec("PRAGMA wal_autocheckpoint=0"); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Exec(`INSERT INTO message VALUES ('msg-wal', 'ses-1', 4000, 4000, '{"role":"assistant"}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Exec(`INSERT INTO part VALUES ('part-wal', 'msg-wal', 'ses-1', 4000, 4000, '{"type":"step-finish","tokens":{"total":4,"input":2,"output":2,"cache":{"read":0}}}')`); err != nil {
		t.Fatal(err)
	}
	walInfo, err := os.Stat(databasePath + "-wal")
	if err != nil {
		t.Fatalf("WAL fixture was not created: %v", err)
	}
	walDevice, walInode := fileIdentity(walInfo)
	if walDevice == 0 || walInode == 0 {
		t.Fatalf("WAL fixture has no usable identity: device=%d inode=%d", walDevice, walInode)
	}

	clock.now = time.Unix(4, 0)
	collector.RunOnce(context.Background())
	mainAfter, err := os.Stat(databasePath)
	if err != nil {
		t.Fatal(err)
	}
	mainBeforeDevice, mainBeforeInode := fileIdentity(mainBefore)
	mainAfterDevice, mainAfterInode := fileIdentity(mainAfter)
	if mainBeforeDevice != mainAfterDevice || mainBeforeInode != mainAfterInode {
		t.Fatalf("WAL-only source change replaced the main database: before=%d:%d after=%d:%d", mainBeforeDevice, mainBeforeInode, mainAfterDevice, mainAfterInode)
	}
	if collector.openCodeDatabase == firstDatabase {
		t.Fatal("cached OpenCode database survived a WAL identity change")
	}
	counters := collector.Counters()
	if counters.OpenCodeDatabaseOpens != 2 || counters.OpenCodeDatabaseReuses != 0 {
		t.Fatalf("WAL source lifecycle = %+v, want reopen instead of reuse", counters)
	}
	if counters.UsageAccepted != 5 {
		t.Fatalf("WAL source usage = %d, want original 3 plus new 2 without replay", counters.UsageAccepted)
	}
	if len(emittedEventIDs) != 5 {
		t.Fatalf("WAL source emitted %d events, want the original 3 plus 2 new events: %v", len(emittedEventIDs), emittedEventIDs)
	}
	for index, eventID := range initialEventIDs {
		if emittedEventIDs[index] != eventID {
			t.Fatalf("WAL reopen changed initial event %d: got %q want %q", index, emittedEventIDs[index], eventID)
		}
	}
	newWALParts := 0
	for _, eventID := range emittedEventIDs[len(initialEventIDs):] {
		if strings.HasPrefix(eventID, "opencode:ses-1:part-wal:") {
			newWALParts++
		}
	}
	if newWALParts != 2 {
		t.Fatalf("WAL reopen emitted %d new part-wal dimensions, want 2: %v", newWALParts, emittedEventIDs[len(initialEventIDs):])
	}
	state, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if state.RawScan.ScannedThroughRowID <= initialCursor.RawScan.ScannedThroughRowID {
		t.Fatalf("WAL reopen did not advance the saved rowid frontier: before=%+v after=%+v", initialCursor.RawScan, state.RawScan)
	}
	if state.WALDevice != walDevice || state.WALInode != walInode {
		t.Fatalf("cursor did not retain WAL identity: %+v, want %d:%d", state, walDevice, walInode)
	}
	collector.Stop()
}

func TestAgentTokenCollectorRecoversAfterSameInodeOpenCodeReadFailure(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeFixture(t)
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &agentStatusTestClock{now: time.Unix(3, 0)}
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode"}}, nil
		},
		CursorPath: filepath.Join(root, "cursor.json"), SkipTranscriptScan: true, EpochStartedAt: 1,
	})
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 3 {
		t.Fatalf("initial OpenCode usage = %d, want 3", got)
	}

	// Remove a required table in place so the path, device, and inode remain
	// unchanged. A cached read-only handle must not survive this source failure.
	mutateSQLite(t, databasePath, `DROP TABLE part`)
	clock.now = time.Unix(13, 0)
	collector.RunOnce(context.Background())

	mutateSQLite(t, databasePath, `CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`)
	mutateSQLite(t, databasePath, `INSERT INTO message VALUES ('msg-2', 'ses-1', 3000, 3000, '{"role":"assistant"}')`)
	mutateSQLite(t, databasePath, `INSERT INTO part VALUES ('part-2', 'msg-2', 'ses-1', 3000, 3000, '{"type":"step-finish","tokens":{"total":10,"input":3,"output":5,"reasoning":0,"cache":{"read":2,"write":0}}}')`)
	clock.now = time.Unix(23, 0)
	collector.RunOnce(context.Background())

	counters := collector.Counters()
	if counters.OpenCodeDatabaseOpens != 2 || counters.OpenCodeDatabaseReuses != 1 {
		t.Fatalf("same-inode OpenCode recovery lifecycle = %+v", counters)
	}
	if counters.UsageAccepted != 6 {
		t.Fatalf("same-inode OpenCode recovery usage = %d, want 6", counters.UsageAccepted)
	}
	statsDB, err := OpenReadOnly(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	coverage, err := statsDB.ReadCoverageEpochs(context.Background(), 0, 40, 100)
	if err != nil {
		_ = statsDB.Close()
		t.Fatal(err)
	}
	unavailable, err := statsDB.ReadUnavailableSpans(context.Background(), 0, 40, 100)
	if err != nil {
		_ = statsDB.Close()
		t.Fatal(err)
	}
	if err := statsDB.Close(); err != nil {
		t.Fatal(err)
	}
	var recovered *CoverageEpoch
	for _, epoch := range coverage {
		if epoch.SourceID == "opencode:ses-1" && epoch.StartedAt >= 23 {
			recovered = &epoch
		}
	}
	if recovered == nil {
		t.Fatalf("OpenCode recovery did not persist a post-outage coverage epoch: coverage=%+v unavailable=%+v", coverage, unavailable)
	}
	var outage *UnavailableSpan
	for _, span := range unavailable {
		if span.Family == "agent_tokens" && span.SourceID == recovered.SourceID && span.StartedAt == 13 {
			outage = &span
			break
		}
	}
	if outage == nil {
		t.Fatalf("OpenCode read failure interval was not retained for the recovered source: coverage=%+v unavailable=%+v", coverage, unavailable)
	}
	if outage.EndedAt > recovered.StartedAt || recovered.StartedAt-outage.EndedAt > outage.NativeCadenceSeconds {
		t.Fatalf("recovery coverage does not resume within one cadence of the matching outage end: outage=%+v recovered=%+v", *outage, *recovered)
	}
	collector.RunOnce(context.Background())
	statsDB, err = OpenReadOnly(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	atoms, err := statsDB.ReadUsageAtoms(context.Background(), 0, 40, 100)
	if err != nil {
		_ = statsDB.Close()
		t.Fatal(err)
	}
	if err := statsDB.Close(); err != nil {
		t.Fatal(err)
	}
	if len(atoms) != 6 {
		t.Fatalf("same-inode OpenCode recovery persisted %d usage atoms after replay, want 6", len(atoms))
	}
	collector.Stop()
}

func TestAgentTokenCollectorDoesNotReuseClosedOpenCodeHandleAfterSourceFailure(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode.db")
	database, err := sql.Open("sqlite3", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root-a', '/repo-a', 'build', '{"providerID":"fixture","id":"model"}', 1000, 2500, 0)`,
		`INSERT INTO session VALUES ('root-b', '/repo-b', 'build', '{"providerID":"fixture","id":"model"}', 1000, 2500, 0)`,
		`INSERT INTO message VALUES ('msg-a', 'root-a', 2000, 2000, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-b', 'root-b', 2100, 2100, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-a', 'msg-a', 'root-a', 2000, 2000, '{"type":"step-finish","tokens":{"input":2}}')`,
		`INSERT INTO part VALUES ('part-b', 'msg-b', 'root-b', 2100, 2100, '{"type":"step-finish","tokens":{"input":3}}')`,
	} {
		if _, err := database.Exec(statement); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}

	var batches []AppendBatch
	clock := &agentStatusTestClock{now: time.Unix(3, 0)}
	collector := NewAgentTokenCollector(tokenWriterFunc(func(_ context.Context, batch AppendBatch) (AppendResult, error) {
		batches = append(batches, batch)
		return AppendResult{UsageAccepted: len(batch.UsageAtoms), CoverageChanged: len(batch.CoverageEpochs), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
	}), clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{
				{Kind: "opencode", OpenCodeSession: "root-a"},
				{Kind: "opencode", OpenCodeSession: "root-b"},
			}, nil
		},
		CursorPath: filepath.Join(root, "cursor.json"), SkipTranscriptScan: true,
	})
	collector.RunOnce(context.Background())
	if len(batches) != 2 || len(batches[0].UsageAtoms) != 2 {
		t.Fatalf("initial synthetic OpenCode collection = %#v", batches)
	}

	// Preserve the cached handle, then remove the source table in place. The
	// first root now closes the handle; later roots must not issue reads through
	// that closed local pointer and manufacture a misleading sql.ErrConnDone.
	mutateSQLite(t, databasePath, `DROP TABLE session`)
	clock.now = time.Unix(4, 0)
	collector.RunOnce(context.Background())
	if len(batches) != 3 {
		t.Fatalf("source-failure collection count = %d, want one metadata append after two initial appends", len(batches))
	}
	if len(batches[2].UnavailableSpans) != 2 {
		t.Fatalf("source-failure unavailable spans = %#v, want one per root", batches[2].UnavailableSpans)
	}
	wantReason := batches[2].UnavailableSpans[0].Reason
	if wantReason == "" || strings.Contains(wantReason, "database is closed") {
		t.Fatalf("first source-failure reason = %q", wantReason)
	}
	wantSources := map[string]bool{"opencode:root-a": false, "opencode:root-b": false}
	for _, span := range batches[2].UnavailableSpans {
		if span.Reason != wantReason {
			t.Fatalf("source-failure reasons diverged after handle close: %#v", batches[2].UnavailableSpans)
		}
		if _, ok := wantSources[span.SourceID]; !ok {
			t.Fatalf("unexpected source-failure source: %#v", span)
		}
		wantSources[span.SourceID] = true
	}
	for source, seen := range wantSources {
		if !seen {
			t.Fatalf("source-failure omitted %s: %#v", source, batches[2].UnavailableSpans)
		}
	}
	collector.Stop()
}

type syntheticOpenCodeTreeFailureReader struct {
	failureRoot    string
	closed         bool
	rows           int
	rowsAfterClose int
}

func (r *syntheticOpenCodeTreeFailureReader) Scalar(_ context.Context, query string, _ ...any) (any, error) {
	if query != "PRAGMA data_version" {
		return nil, fmt.Errorf("unexpected synthetic OpenCode scalar query: %s", query)
	}
	return int64(1), nil
}

func (r *syntheticOpenCodeTreeFailureReader) Rows(_ context.Context, query string, args ...any) ([][]any, error) {
	r.rows++
	if r.closed {
		r.rowsAfterClose++
		return nil, errors.New("database is closed")
	}
	if len(args) == 0 {
		return nil, fmt.Errorf("synthetic OpenCode query has no arguments: %s", query)
	}
	root, ok := args[0].(string)
	if !ok {
		return nil, fmt.Errorf("synthetic OpenCode query root has type %T", args[0])
	}
	switch {
	case strings.Contains(query, "SELECT id FROM session WHERE id = ? LIMIT 1"):
		return [][]any{{root}}, nil
	case strings.Contains(query, "SELECT id FROM session WHERE id = ? LIMIT 2"):
		return [][]any{{root}}, nil
	case strings.Contains(query, "SELECT MIN(time_created), MAX(time_updated) FROM session"):
		if root == r.failureRoot {
			return nil, errors.New("synthetic tree source failure")
		}
		return [][]any{{int64(1000), int64(2500)}}, nil
	default:
		return nil, fmt.Errorf("unexpected synthetic OpenCode query: %s", query)
	}
}

func (r *syntheticOpenCodeTreeFailureReader) QueryOnly(context.Context) error { return nil }

func (r *syntheticOpenCodeTreeFailureReader) Close() error {
	r.closed = true
	return nil
}

func TestAgentTokenCollectorStopsAfterTreeSourceFailure(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "synthetic-opencode.db")
	if err := os.WriteFile(databasePath, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	identity, err := os.Stat(databasePath)
	if err != nil {
		t.Fatal(err)
	}
	reader := &syntheticOpenCodeTreeFailureReader{failureRoot: "root-b"}
	database := &OpenCodeDatabase{reader: reader, close: reader.Close}
	device, inode := fileIdentity(identity)
	collector := NewAgentTokenCollector(countingTokenWriter{}, &agentStatusTestClock{now: time.Unix(3, 0)}, AgentTokenCollectorConfig{
		OpenCodeDatabase:   databasePath,
		CursorPath:         filepath.Join(root, "cursor.json"),
		SkipTranscriptScan: true,
	})
	collector.openCodeDatabase = database
	collector.openCodeDatabasePath = databasePath
	collector.openCodeDatabaseDevice = device
	collector.openCodeDatabaseInode = inode

	batch, receipt, err := collector.collectOpenCode(context.Background(), 3, 10, []AgentRecord{
		{Kind: "opencode", OpenCodeSession: "root-a"},
		{Kind: "opencode", OpenCodeSession: "root-b"},
		{Kind: "opencode", OpenCodeSession: "root-c"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if receipt != nil || len(batch.UsageAtoms) != 0 || len(batch.CoverageEpochs) != 0 {
		t.Fatalf("tree source failure advanced data: atoms=%#v coverage=%#v receipt=%v", batch.UsageAtoms, batch.CoverageEpochs, receipt != nil)
	}
	if len(batch.UnavailableSpans) != 3 {
		t.Fatalf("tree source failure spans = %#v, want one per root", batch.UnavailableSpans)
	}
	wantSources := map[string]bool{"opencode:root-a": false, "opencode:root-b": false, "opencode:root-c": false}
	wantReason := batch.UnavailableSpans[0].Reason
	for _, span := range batch.UnavailableSpans {
		if span.Reason != wantReason {
			t.Fatalf("tree source failure reasons diverged: %#v", batch.UnavailableSpans)
		}
		if _, ok := wantSources[span.SourceID]; !ok {
			t.Fatalf("unexpected tree source failure source: %#v", span)
		}
		wantSources[span.SourceID] = true
	}
	for source, seen := range wantSources {
		if !seen {
			t.Fatalf("tree source failure omitted %s: %#v", source, batch.UnavailableSpans)
		}
	}
	if !reader.closed || reader.rowsAfterClose != 0 {
		t.Fatalf("tree failure reader lifecycle = closed=%v rows=%d rows_after_close=%d", reader.closed, reader.rows, reader.rowsAfterClose)
	}
}

func TestOpenCodeUnavailableReasonUsesStableDatabaseCategories(t *testing.T) {
	for _, test := range []struct {
		name string
		err  string
		want string
	}{
		{name: "malformed", err: "open OpenCode database read-only: file is not a database", want: "database-malformed"},
		{name: "disk-image", err: "database disk image is malformed", want: "database-malformed"},
		{name: "locked", err: "read OpenCode session tree: database is locked", want: "database-locked"},
		{name: "table-locked", err: "database table is locked: session", want: "database-locked"},
		{name: "detail-preserved", err: "OpenCode database schema mismatch: missing column", want: "OpenCode database schema mismatch: missing column"},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := openCodeUnavailableReason(errors.New(test.err)); got != test.want {
				t.Fatalf("stable OpenCode reason = %q, want %q", got, test.want)
			}
		})
	}
}

func TestAgentTokenCollectorPublishesTypedMalformedDatabaseGap(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode.db")
	if err := os.WriteFile(databasePath, []byte("not a sqlite database"), 0o600); err != nil {
		t.Fatal(err)
	}
	var batches []AppendBatch
	collector := NewAgentTokenCollector(tokenWriterFunc(func(_ context.Context, batch AppendBatch) (AppendResult, error) {
		batches = append(batches, batch)
		return AppendResult{UnavailableAccepted: len(batch.UnavailableSpans)}, nil
	}), &agentStatusTestClock{now: time.Unix(3, 0)}, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "root"}}, nil
		},
		CursorPath: filepath.Join(root, "cursor.json"), SkipTranscriptScan: true,
	})
	collector.RunOnce(context.Background())
	if len(batches) != 1 || len(batches[0].UnavailableSpans) != 1 {
		t.Fatalf("malformed database batch = %#v", batches)
	}
	if got := batches[0].UnavailableSpans[0].Reason; got != "database-malformed" {
		t.Fatalf("malformed database reason = %q, want database-malformed", got)
	}
}

func TestAgentTokenCollectorResourceSoakStaysBounded(t *testing.T) {
	root := t.TempDir()
	transcript := filepath.Join(root, "claude.jsonl")
	writeJSONL(t, transcript, usageClaude(1788307300, "soak", 4, 3))
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	collector := NewAgentTokenCollector(countingTokenWriter{}, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "claude", TmuxSession: "soak"}}, nil
		},
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{{Path: transcript, Provider: "claude", SourceID: "claude", ThreadID: "soak"}}, nil
		},
		CursorPath: filepath.Join(root, "cursor.json"), EpochStartedAt: 1788307200, OwnerGeneration: 1,
	})
	const cycles = 256
	var before, after runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&before)
	baseGoroutines := runtime.NumGoroutine()
	var maxDuration time.Duration
	for cycle := 0; cycle < cycles; cycle++ {
		started := time.Now()
		collector.RunOnce(context.Background())
		if duration := time.Since(started); duration > maxDuration {
			maxDuration = duration
		}
	}
	runtime.GC()
	runtime.ReadMemStats(&after)
	counters := collector.Counters()
	if counters.Cycles != cycles || counters.UsageAccepted != 2 || counters.Errors != 0 {
		t.Fatalf("resource soak counters = %+v, want %d cycles, two atoms, no errors", counters, cycles)
	}
	if delta := runtime.NumGoroutine() - baseGoroutines; delta > 2 {
		t.Fatalf("collector leaked goroutines: before=%d after=%d", baseGoroutines, runtime.NumGoroutine())
	}
	if maxDuration >= 500*time.Millisecond {
		t.Fatalf("collector cycle exceeded bounded duration: max=%s", maxDuration)
	}
	t.Logf("collector resource soak: cycles=%d max_cycle=%s heap_objects_before=%d heap_objects_after=%d heap_alloc_before=%d heap_alloc_after=%d goroutines_before=%d goroutines_after=%d", cycles, maxDuration, before.HeapObjects, after.HeapObjects, before.HeapAlloc, after.HeapAlloc, baseGoroutines, runtime.NumGoroutine())
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

func TestAgentTokenCollectorCadenceEpochIDsStayBoundedAcrossTransitions(t *testing.T) {
	active := true
	collector := NewAgentTokenCollector(countingTokenWriter{}, realHostCollectorClock{}, AgentTokenCollectorConfig{
		ActiveCadence:      time.Second,
		IdleCadence:        time.Minute,
		Inventory:          func(context.Context) ([]AgentRecord, []error) { return nil, nil },
		SkipTranscriptScan: true,
		EpochID:            strings.Repeat("epoch", 40),
		ActiveFunc:         func() bool { return active },
	})
	collector.RunOnce(context.Background())
	base := collector.config.EpochID
	if len([]byte(base)) > maxAgentTokenEpochIDBytes {
		t.Fatalf("initial epoch id exceeded bound: %d bytes", len([]byte(base)))
	}
	for index := 0; index < 200; index++ {
		active = !active
		collector.RunOnce(context.Background())
		if len([]byte(collector.config.EpochID)) > maxAgentTokenEpochIDBytes {
			t.Fatalf("epoch id grew beyond bound at transition %d: %d bytes", index, len([]byte(collector.config.EpochID)))
		}
	}
	if collector.config.EpochID == base || !strings.HasPrefix(collector.config.EpochID, strings.Repeat("epoch", 20)) {
		t.Fatalf("cadence transitions lost the bounded epoch identity: base=%q final=%q", base, collector.config.EpochID)
	}
}

func TestAgentTokenCollectorCadenceTransitionPreservesUTF8EpochIDs(t *testing.T) {
	active := true
	var batches []AppendBatch
	collector := NewAgentTokenCollector(tokenWriterFunc(func(_ context.Context, batch AppendBatch) (AppendResult, error) {
		batches = append(batches, batch)
		return AppendResult{CoverageChanged: len(batch.CoverageEpochs)}, nil
	}), realHostCollectorClock{}, AgentTokenCollectorConfig{
		ActiveCadence:      time.Second,
		IdleCadence:        time.Minute,
		Inventory:          func(context.Context) ([]AgentRecord, []error) { return nil, nil },
		SkipTranscriptScan: true,
		EpochID:            strings.Repeat("a", 125) + "€",
		ActiveFunc:         func() bool { return active },
	})
	collector.RunOnce(context.Background())
	active = false
	collector.RunOnce(context.Background())
	if len(batches) != 2 {
		t.Fatalf("cadence transition emitted unexpected batches: %+v", batches)
	}
	epochID := collector.config.EpochID
	if len([]byte(epochID)) > maxAgentTokenEpochIDBytes || !utf8.ValidString(epochID) {
		t.Fatalf("transition emitted invalid bounded epoch %q (%d bytes)", epochID, len([]byte(epochID)))
	}
}

func TestAgentTokenCollectorSplitPathDeduplicatesRepeatedInventoryUnavailableSpans(t *testing.T) {
	root := t.TempDir()
	databasePath := createOpenCodeFixture(t)
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "claude"}}, []error{errors.New("inventory-error-a"), errors.New("inventory-error-b")}
		},
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "open-code"}}, nil
		},
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return nil, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "cursor.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})
	batch, _, err := collector.collectTranscripts(context.Background(), 1788307300, 10, []AgentRecord{{Kind: "claude"}}, []error{errors.New("inventory-error-a"), errors.New("inventory-error-b")}, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.UnavailableSpans) != 1 {
		t.Fatalf("split-path inventory errors = %#v", batch.UnavailableSpans)
	}

	collector.RunOnce(context.Background())
	counters := collector.Counters()
	if counters.UsageAccepted == 0 {
		t.Fatal("split-path OpenCode atoms were not persisted")
	}
	if counters.AppendErrors != 0 || counters.Errors != 0 {
		t.Fatalf("coalesced inventory errors still failed append: %+v", counters)
	}
}

func TestAgentTokenTranscriptBudgetIsAggregateFairAndSurvivesRestart(t *testing.T) {
	root := t.TempDir()
	firstPath := filepath.Join(root, "first.jsonl")
	secondPath := filepath.Join(root, "second.jsonl")
	writeJSONL(t, firstPath,
		usageClaude(1, "first-1", 1, 1),
		usageClaude(2, "first-2", 2, 1),
		usageClaude(3, "first-3", 3, 1),
	)
	writeJSONL(t, secondPath,
		usageClaude(1, "second-1", 4, 1),
		usageClaude(2, "second-2", 5, 1),
		usageClaude(3, "second-3", 6, 1),
	)
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	sources := []TranscriptSource{
		{Path: firstPath, Provider: "claude", SourceID: "first", ThreadID: "first"},
		{Path: secondPath, Provider: "claude", SourceID: "second", ThreadID: "second"},
	}
	config := AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return sources, nil
		},
		TranscriptCursorPath:         cursorPath,
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 2,
	}
	seenAtoms := make(map[string]struct{})
	for cycle := 0; cycle < 4; cycle++ {
		collector := NewAgentTokenCollector(nil, nil, config)
		batch, receipts, err := collector.collectTranscripts(context.Background(), 10+float64(cycle), 1, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatalf("cycle %d collect transcripts: %v", cycle, err)
		}
		if len(batch.UsageAtoms) > 6 {
			t.Fatalf("cycle %d scanned more than the aggregate two-record budget: atoms=%d", cycle, len(batch.UsageAtoms))
		}
		for _, atom := range batch.UsageAtoms {
			identity := strings.Join([]string{atom.EventID, atom.Direction, atom.Modality, atom.CacheRole, atom.Unit}, "\x00")
			if _, exists := seenAtoms[identity]; exists {
				t.Fatalf("cycle %d replayed a previously committed atom: %q", cycle, identity)
			}
			seenAtoms[identity] = struct{}{}
		}
		if cycle == 1 && (len(batch.CoverageEpochs) != 1 || batch.CoverageEpochs[0].SourceID != "second") {
			t.Fatalf("second source was not scheduled after collector restart: %+v", batch.CoverageEpochs)
		}
		if len(receipts) != 0 {
			if err := receipts[0].commit(); err != nil {
				t.Fatalf("cycle %d commit cursor: %v", cycle, err)
			}
		}
	}
	cursor := NewTranscriptCursorStore(cursorPath)
	for _, source := range sources {
		state, err := cursor.State(source)
		if err != nil {
			t.Fatalf("load committed cursor for %s: %v", source.SourceID, err)
		}
		info, err := os.Stat(source.Path)
		if err != nil {
			t.Fatal(err)
		}
		if state.Offset != info.Size() {
			t.Fatalf("source %s did not finish bounded backfill: offset=%d size=%d", source.SourceID, state.Offset, info.Size())
		}
	}
}

func TestAgentTokenCollectorRecoversOversizedTranscriptCursorSidecar(t *testing.T) {
	root := t.TempDir()
	transcriptPath := filepath.Join(root, "claude.jsonl")
	writeJSONL(t, transcriptPath, usageClaude(1, "before-restart", 10, 2))
	source := TranscriptSource{Path: transcriptPath, Provider: "claude", SourceID: "fixture", ThreadID: "fixture"}
	state := &TranscriptScannerState{}
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || initial.Receipt == nil {
		t.Fatalf("initial transcript scan = %+v, err=%v", initial, err)
	}
	initial.Receipt.Commit()

	cursorPath := filepath.Join(root, "transcript-cursors.json")
	disk := transcriptCursorDisk{
		Version: 1,
		States:  map[string]TranscriptScannerState{transcriptCursorKey(source): cloneTranscriptState(*state)},
	}
	for index := 0; index < 1100; index++ {
		path := filepath.Join(root, fmt.Sprintf("retained-%03d.jsonl", index))
		key := "claude\x00" + path
		state := TranscriptScannerState{Path: path}
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			t.Fatalf("create retained cursor transcript: %v", err)
		}
		if index < 500 {
			state.Prefix = make([]byte, transcriptPrefixBytes)
			state.ParsedTail = make([]byte, transcriptParsedTailBytes)
			if _, err := rand.Read(state.Prefix); err != nil {
				t.Fatalf("fill high-entropy cursor prefix: %v", err)
			}
			if _, err := rand.Read(state.ParsedTail); err != nil {
				t.Fatalf("fill high-entropy cursor tail: %v", err)
			}
		}
		disk.States[key] = state
	}
	if err := atomicSidecarWrite(cursorPath, disk); err != nil {
		t.Fatalf("seed oversized transcript cursor sidecar: %v", err)
	}
	info, err := os.Stat(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	if info.Size() <= 80*1024*1024 {
		t.Fatalf("fixture sidecar size = %d bytes, want over 80 MiB to match the live legacy sidecar", info.Size())
	}

	appendJSONL(t, transcriptPath, usageClaude(2, "after-restart", 20, 4), false)
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath: cursorPath,
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{source}, nil
		},
	})
	batch, receipts, err := collector.collectTranscripts(context.Background(), 20, 5, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatalf("collect transcripts from oversized cursor sidecar: %v", err)
	}
	if len(batch.UsageAtoms) != 2 {
		t.Fatalf("new session token atoms = %d, want input and output; unavailable=%+v", len(batch.UsageAtoms), batch.UnavailableSpans)
	}
	for _, unavailable := range batch.UnavailableSpans {
		if strings.Contains(unavailable.Reason, "transcript cursor state exceeds its size bound") {
			t.Fatalf("collector rejected recoverable cursor sidecar: %+v", unavailable)
		}
	}
	if len(receipts) != 1 {
		t.Fatalf("cursor receipts = %d, want one", len(receipts))
	}
	if info, err := os.Stat(cursorPath); err != nil || info.Size() <= 50*1024*1024 {
		t.Fatalf("migrated high-entropy cursor sidecar info=%v err=%v, want samples preserved during migration", info, err)
	}
	for index := 0; index < 1100; index++ {
		path := filepath.Join(root, fmt.Sprintf("retained-%03d.jsonl", index))
		if err := os.Remove(path); err != nil {
			t.Fatalf("retire transcript source: %v", err)
		}
	}
	if err := receipts[0].commit(); err != nil {
		t.Fatalf("commit recovered transcript cursor: %v", err)
	}

	recovered := NewTranscriptCursorStore(cursorPath)
	recoveredState, err := recovered.State(source)
	if err != nil {
		t.Fatalf("reload recovered transcript cursor: %v", err)
	}
	transcriptInfo, err := os.Stat(transcriptPath)
	if err != nil {
		t.Fatal(err)
	}
	if recoveredState.Offset != transcriptInfo.Size() {
		t.Fatalf("recovered cursor offset = %d, want transcript size %d", recoveredState.Offset, transcriptInfo.Size())
	}
	if info, err := os.Stat(cursorPath); err != nil || info.Size() >= maxTranscriptCursorSidecarBytes {
		t.Fatalf("migrated cursor sidecar info=%v err=%v, want under %d bytes", info, err, maxTranscriptCursorSidecarBytes)
	}
	migratedJSON, err := os.ReadFile(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	var migrated transcriptCursorDisk
	if err := json.Unmarshal(migratedJSON, &migrated); err != nil {
		t.Fatalf("decode migrated cursor sidecar: %v", err)
	}
	if migrated.Version != 2 || len(migrated.States) != 1 {
		t.Fatalf("migrated cursor version/states = %d/%d, want v2 with retired sources pruned and current source retained", migrated.Version, len(migrated.States))
	}
}

func TestAgentTokenCollectorScansPartialSourcesAfterDiscoveryTimeout(t *testing.T) {
	root := t.TempDir()
	transcriptPath := filepath.Join(root, "claude.jsonl")
	writeJSONL(t, transcriptPath, usageClaude(1, "discovered-before-timeout", 12, 3))
	source := TranscriptSource{Path: transcriptPath, Provider: "claude", SourceID: "fixture", ThreadID: "fixture"}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptDiscoveryTimeout: 10 * time.Millisecond,
		TranscriptSources: func(ctx context.Context, _ []AgentRecord) ([]TranscriptSource, error) {
			<-ctx.Done()
			return []TranscriptSource{source}, ctx.Err()
		},
	})
	parent, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	batch, _, err := collector.collectTranscripts(parent, 20, 5, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatalf("collect partial transcript sources: %v", err)
	}
	if len(batch.UsageAtoms) != 2 {
		t.Fatalf("usage atoms after bounded discovery = %d, want input and output; unavailable=%+v", len(batch.UsageAtoms), batch.UnavailableSpans)
	}
	if len(batch.UnavailableSpans) != 1 || batch.UnavailableSpans[0].Reason != "transcript-discovery-timeout" {
		t.Fatalf("discovery timeout diagnostics = %+v, want one explicit timeout span", batch.UnavailableSpans)
	}
}

func TestTranscriptSourceLivePriorityExcludesCodexForkRepair(t *testing.T) {
	state := TranscriptScannerState{}
	source := TranscriptSource{
		Path: "/synthetic/rollout.jsonl", Provider: "codex", SourceID: "synthetic-repair",
		CodexForkRepairOnly: true, LiveCandidate: true,
	}
	if transcriptSourceIsLive(source, state, 128) {
		t.Fatal("Codex fork repair source received live priority before historical repair work")
	}
	source.CodexForkRepairOnly = false
	if !transcriptSourceIsLive(source, state, 128) {
		t.Fatal("direct Codex source with new bytes did not receive live priority")
	}
}

func TestAgentTokenCollectorPrioritizesLiveTranscriptThenServicesHistory(t *testing.T) {
	root := t.TempDir()
	livePath := filepath.Join(root, "z-live.jsonl")
	historicalSources := make([]TranscriptSource, 3)
	for index := range historicalSources {
		path := filepath.Join(root, fmt.Sprintf("a-history-%d.jsonl", index))
		writeJSONL(t, path, usageClaude(1, fmt.Sprintf("history-%d", index), 10+float64(index), 1))
		historicalSources[index] = TranscriptSource{Path: path, Provider: "claude", SourceID: fmt.Sprintf("history-%d", index), ThreadID: "history"}
	}
	writeJSONL(t, livePath, usageCodex(1, 1, 0, 1))
	liveSource := TranscriptSource{Path: livePath, Provider: "codex", SourceID: "live", ThreadID: "live", LiveCandidate: true}
	sources := append(append([]TranscriptSource(nil), historicalSources...), liveSource)
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return sources, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "opencode-cursor.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 1,
	})

	for cycle := 0; cycle < 6; cycle++ {
		batch, receipts, err := collector.collectTranscripts(context.Background(), 10+float64(cycle), 1, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatalf("cycle %d collect transcripts: %v", cycle, err)
		}
		if len(batch.CoverageEpochs) != 1 {
			t.Fatalf("cycle %d scanned sources = %+v, want exactly one under record budget", cycle, batch.CoverageEpochs)
		}
		wantSource := "live"
		if cycle == 2 {
			wantSource = "history-0"
		} else if cycle == 5 {
			wantSource = "history-1"
		}
		if got := batch.CoverageEpochs[0].SourceID; got != wantSource {
			t.Fatalf("cycle %d scheduled source = %q, want %q", cycle, got, wantSource)
		}
		if len(receipts) != 1 {
			t.Fatalf("cycle %d prepared cursor receipts = %d, want 1", cycle, len(receipts))
		}
		if err := receipts[0].commit(); err != nil {
			t.Fatalf("cycle %d commit cursor: %v", cycle, err)
		}
		if cycle < 5 {
			appendJSONL(t, livePath, usageCodex(float64(cycle+2), 1, 0, 1), false)
		}
	}
}

func TestClaudeTranscriptWithGrowingPendingLineKeepsLivePriority(t *testing.T) {
	root := t.TempDir()
	livePath := filepath.Join(root, "claude-live.jsonl")
	historyPath := filepath.Join(root, "claude-history.jsonl")
	liveSource := TranscriptSource{Path: livePath, Provider: "claude", SourceID: "claude-live", ThreadID: "live"}
	historySource := TranscriptSource{Path: historyPath, Provider: "claude", SourceID: "claude-history", ThreadID: "history"}
	cursorPath := filepath.Join(root, "transcript-cursor.json")
	sources := []TranscriptSource{liveSource}
	config := AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return append([]TranscriptSource(nil), sources...), nil
		},
		TranscriptCursorPath:         cursorPath,
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 1,
	}
	collector := NewAgentTokenCollector(nil, nil, config)
	collectAndCommit := func(cycle int) AppendBatch {
		t.Helper()
		batch, receipts, err := collector.collectTranscripts(context.Background(), 10+float64(cycle), 1, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatalf("cycle %d collect transcripts: %v", cycle, err)
		}
		for _, receipt := range receipts {
			if err := receipt.commit(); err != nil {
				t.Fatalf("cycle %d commit cursor receipt: %v", cycle, err)
			}
		}
		return batch
	}
	writeJSONL(t, livePath, usageClaude(1, "initial", 2, 1))
	writeJSONL(t, historyPath, usageClaude(1, "history", 20, 5))
	if seed := collectAndCommit(0); len(seed.CoverageEpochs) != 1 || seed.CoverageEpochs[0].SourceID != liveSource.SourceID {
		t.Fatalf("seed live transcript coverage = %+v, want %s", seed.CoverageEpochs, liveSource.SourceID)
	}
	sources = []TranscriptSource{historySource}
	if seed := collectAndCommit(1); len(seed.CoverageEpochs) != 1 || seed.CoverageEpochs[0].SourceID != historySource.SourceID {
		t.Fatalf("seed historical transcript coverage = %+v, want %s", seed.CoverageEpochs, historySource.SourceID)
	}
	sources = []TranscriptSource{liveSource, historySource}
	state, err := collector.transcriptCursor.State(liveSource)
	if err != nil {
		t.Fatalf("load seeded live cursor: %v", err)
	}
	committedEOF := state.Offset

	remaining := appendJSONL(t, livePath, usageClaude(2, "growing", 3, 2), true)
	collectAndCommit(2)
	state, err = collector.transcriptCursor.State(liveSource)
	if err != nil {
		t.Fatalf("load partial live cursor: %v", err)
	}
	if state.Offset != committedEOF || len(state.PendingLine) == 0 {
		t.Fatalf("partial Claude cursor = offset %d pending %d, want prior EOF %d and pending bytes", state.Offset, len(state.PendingLine), committedEOF)
	}
	if !state.UsageCommittedEOFKnown || state.UsageCommittedEOFSize != committedEOF {
		t.Fatalf("partial Claude cursor lost durable EOF watermark: %+v", state)
	}

	// A fresh collector proves source priority survives process restart along with
	// the committed EOF watermark, while an unchanged historical source is ready.
	collector = NewAgentTokenCollector(nil, nil, config)
	file, err := os.OpenFile(livePath, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write(remaining); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(livePath)
	if err != nil {
		t.Fatal(err)
	}
	if !transcriptSourceIsLive(liveSource, state, info.Size()) {
		t.Fatalf("Claude transcript that resumed a pending line lost live priority: state=%+v current_size=%d", state, info.Size())
	}
	legacyState := state
	legacyState.UsageCommittedEOFSize = 0
	legacyState.UsageCommittedEOFKnown = false
	if transcriptSourceIsLive(liveSource, legacyState, info.Size()) {
		t.Fatalf("legacy Claude partial cursor without an EOF watermark was incorrectly prioritized as live: state=%+v current_size=%d", legacyState, info.Size())
	}
	resumed := collectAndCommit(3)
	if len(resumed.CoverageEpochs) != 1 || resumed.CoverageEpochs[0].SourceID != liveSource.SourceID {
		t.Fatalf("resumed Claude source was not served before historical backlog: coverage=%+v", resumed.CoverageEpochs)
	}
}

func TestAgentTokenCollectorAdvancesHistoricalRotationWhenVisitedSourceIsAtEOF(t *testing.T) {
	root := t.TempDir()
	eofPath := filepath.Join(root, "a-history-eof.jsonl")
	backlogPath := filepath.Join(root, "b-history-backlog.jsonl")
	livePath := filepath.Join(root, "z-live.jsonl")
	writeJSONL(t, eofPath, usageClaude(1, "history-eof", 10, 1))
	writeJSONL(t, backlogPath, usageClaude(1, "history-backlog", 20, 1))
	writeJSONL(t, livePath, usageCodex(1, 1, 0, 1))
	eofSource := TranscriptSource{Path: eofPath, Provider: "claude", SourceID: "history-eof", ThreadID: "history"}
	backlogSource := TranscriptSource{Path: backlogPath, Provider: "claude", SourceID: "history-backlog", ThreadID: "history"}
	liveSource := TranscriptSource{Path: livePath, Provider: "codex", SourceID: "live", ThreadID: "live", LiveCandidate: true}
	sources := []TranscriptSource{eofSource}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return sources, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursor.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 1,
	})
	commitCycle := func(cycle int) *AppendBatch {
		t.Helper()
		batch, receipts, err := collector.collectTranscripts(context.Background(), 10+float64(cycle), 1, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatalf("cycle %d collect transcripts: %v", cycle, err)
		}
		for _, receipt := range receipts {
			if err := receipt.commit(); err != nil {
				t.Fatalf("cycle %d commit cursor: %v", cycle, err)
			}
		}
		return &batch
	}
	seed := commitCycle(0)
	if len(seed.CoverageEpochs) != 1 || seed.CoverageEpochs[0].SourceID != "history-eof" {
		t.Fatalf("seed coverage = %+v, want history-eof", seed.CoverageEpochs)
	}
	if got := collector.nextHistoricalTranscript; got != transcriptCursorKey(eofSource) {
		t.Fatalf("seed historical cursor = %q, want H0 %q", got, transcriptCursorKey(eofSource))
	}
	if err := os.WriteFile(eofPath, nil, 0o600); err != nil {
		t.Fatalf("truncate synthetic H0 to empty: %v", err)
	}
	sources = []TranscriptSource{eofSource, backlogSource, liveSource}
	for cycle := 1; cycle <= 4; cycle++ {
		if cycle > 1 {
			appendJSONL(t, livePath, usageCodex(float64(cycle+1), 1, 0, 1), false)
		}
		batch := commitCycle(cycle)
		if cycle == 3 {
			if len(batch.CoverageEpochs) != 2 || batch.CoverageEpochs[0].SourceID != "history-eof" || batch.CoverageEpochs[1].SourceID != "live" {
				t.Fatalf("reset fairness cycle coverage = %+v, want reset H0 then live", batch.CoverageEpochs)
			}
			if got := collector.nextHistoricalTranscript; got != transcriptCursorKey(backlogSource) {
				t.Fatalf("historical cursor after visiting reset H0 = %q, want H1 %q", got, transcriptCursorKey(backlogSource))
			}
		} else if cycle == 4 {
			if len(batch.CoverageEpochs) != 1 || batch.CoverageEpochs[0].SourceID != "history-backlog" {
				t.Fatalf("fairness cycle coverage = %+v, want H1 after visiting reset H0", batch.CoverageEpochs)
			}
		}
	}
}

func TestAgentTokenCollectorAdvancesFairnessPastUnchangedHistoricalEOF(t *testing.T) {
	root := t.TempDir()
	eofPath := filepath.Join(root, "a-history-eof.jsonl")
	backlogPath := filepath.Join(root, "b-history-backlog.jsonl")
	livePath := filepath.Join(root, "z-live.jsonl")
	writeJSONL(t, eofPath, usageClaude(1, "history-eof", 10, 1))
	writeJSONL(t, backlogPath, usageClaude(1, "history-backlog", 20, 1))
	writeJSONL(t, livePath, usageCodex(1, 1, 0, 1))
	eofSource := TranscriptSource{Path: eofPath, Provider: "claude", SourceID: "history-eof", ThreadID: "history"}
	backlogSource := TranscriptSource{Path: backlogPath, Provider: "claude", SourceID: "history-backlog", ThreadID: "history"}
	liveSource := TranscriptSource{Path: livePath, Provider: "codex", SourceID: "live", ThreadID: "live", LiveCandidate: true}
	sources := []TranscriptSource{eofSource}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return sources, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursor.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 1,
	})
	seed, receipts, err := collector.collectTranscripts(context.Background(), 10, 1, nil, nil, AppendBatch{}, nil)
	if err != nil || len(seed.CoverageEpochs) != 1 || seed.CoverageEpochs[0].SourceID != eofSource.SourceID {
		t.Fatalf("seed historical EOF cursor = %+v err=%v", seed.CoverageEpochs, err)
	}
	for _, receipt := range receipts {
		if err := receipt.commit(); err != nil {
			t.Fatalf("commit historical EOF seed: %v", err)
		}
	}

	sources = []TranscriptSource{eofSource, backlogSource, liveSource}
	newCollector := func() *AgentTokenCollector {
		return NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
			TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
				return sources, nil
			},
			TranscriptCursorPath:         filepath.Join(root, "transcript-cursor.json"),
			TranscriptMaxBytesPerCycle:   1 << 20,
			TranscriptMaxRecordsPerCycle: 1,
		})
	}
	commitCycle := func(cycle int, observed float64) *AgentTokenCollector {
		restarted := newCollector()
		batch, liveReceipts, collectErr := restarted.collectTranscripts(context.Background(), observed, 1, nil, nil, AppendBatch{}, nil)
		if collectErr != nil || len(batch.CoverageEpochs) == 0 || batch.CoverageEpochs[0].SourceID != liveSource.SourceID {
			t.Fatalf("restart fairness live cycle %d = coverage %+v err=%v", cycle, batch.CoverageEpochs, collectErr)
		}
		for _, receipt := range liveReceipts {
			if err := receipt.commit(); err != nil {
				t.Fatalf("commit restart fairness live cycle %d: %v", cycle, err)
			}
		}
		if got := restarted.liveTranscriptPriorityRuns; got != cycle+1 {
			t.Fatalf("persisted live-priority streak after restart cycle %d = %d, want %d", cycle, got, cycle+1)
		}
		return restarted
	}
	// Each live turn starts a fresh collector. The persisted streak must reach
	// its fairness bound despite restarts before the scheduler chooses H0.
	for cycle := 0; cycle < maxConsecutiveLiveTranscriptPriorityRuns; cycle++ {
		appendJSONL(t, livePath, usageCodex(float64(cycle+2), 1, 0, 1), false)
		commitCycle(cycle, float64(11+cycle))
	}
	collector = newCollector()
	collector.config.TranscriptMaxBytesPerCycle = 2 // H0's two one-byte integrity reads exhaust the fairness turn.
	cursor := collector.transcriptCursor.(*TranscriptCursorStore)
	injectPostRenameTranscriptCursorWriteFailure(t, cursor, transcriptCursorKey(backlogSource))
	fairnessTurn, receipts, err := collector.collectTranscripts(context.Background(), 13, 1, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatalf("visit unchanged historical EOF source: %v", err)
	}
	if len(fairnessTurn.UsageAtoms) != 0 || len(fairnessTurn.CoverageEpochs) != 0 || len(receipts) != 1 {
		t.Fatalf("unchanged-EOF fairness turn was not schedule-only: batch=%+v receipts=%d", fairnessTurn, len(receipts))
	}
	if err := commitReceipts(receipts); err == nil {
		t.Fatal("schedule-only fairness commit did not surface the post-rename error")
	}
	// The same collector must reload the committed H1 rotation and reset streak.
	collector.config.TranscriptMaxBytesPerCycle = 1 << 20
	for cycle := 0; cycle < maxConsecutiveLiveTranscriptPriorityRuns; cycle++ {
		appendJSONL(t, livePath, usageCodex(float64(cycle+4), 1, 0, 1), false)
		batch, liveReceipts, collectErr := collector.collectTranscripts(context.Background(), float64(14+cycle), 1, nil, nil, AppendBatch{}, nil)
		if collectErr != nil || len(batch.CoverageEpochs) == 0 || batch.CoverageEpochs[0].SourceID != liveSource.SourceID {
			t.Fatalf("same-collector post-error live cycle %d = coverage %+v err=%v", cycle, batch.CoverageEpochs, collectErr)
		}
		if err := commitReceipts(liveReceipts); err != nil {
			t.Fatalf("commit same-collector post-error live cycle %d: %v", cycle, err)
		}
	}
	batch, receipts, err := collector.collectTranscripts(context.Background(), 16, 1, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatalf("same-collector serve next historical source: %v", err)
	}
	if err := commitReceipts(receipts); err != nil {
		t.Fatalf("commit same-collector next historical source: %v", err)
	}
	if len(batch.CoverageEpochs) == 0 || batch.CoverageEpochs[0].SourceID != backlogSource.SourceID {
		t.Fatalf("later historical source was starved after ambiguous unchanged-EOF commit: coverage=%+v", batch.CoverageEpochs)
	}
}

func TestAgentTokenCollectorAdvancesHistoricalRotationAfterUnavailableSource(t *testing.T) {
	root := t.TempDir()
	historyPath := filepath.Join(root, "a-history-unavailable.jsonl")
	backlogPath := filepath.Join(root, "b-history-backlog.jsonl")
	livePath := filepath.Join(root, "z-live.jsonl")
	writeJSONL(t, historyPath, usageClaude(1, "history-unavailable", 10, 1))
	writeJSONL(t, backlogPath, usageClaude(1, "history-backlog", 20, 1))
	writeJSONL(t, livePath, usageCodex(1, 1, 0, 1))
	historySource := TranscriptSource{Path: historyPath, Provider: "claude", SourceID: "history-unavailable", ThreadID: "history"}
	backlogSource := TranscriptSource{Path: backlogPath, Provider: "claude", SourceID: "history-backlog", ThreadID: "history"}
	liveSource := TranscriptSource{Path: livePath, Provider: "codex", SourceID: "live", ThreadID: "live", LiveCandidate: true}
	sources := []TranscriptSource{historySource}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return sources, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursor.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 1,
	})
	commitCycle := func(cycle int) AppendBatch {
		t.Helper()
		batch, receipts, err := collector.collectTranscripts(context.Background(), 10+float64(cycle), 1, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatalf("cycle %d collect transcripts: %v", cycle, err)
		}
		for _, receipt := range receipts {
			if err := receipt.commit(); err != nil {
				t.Fatalf("cycle %d commit cursor: %v", cycle, err)
			}
		}
		return batch
	}
	commitCycle(0)
	if got := collector.nextHistoricalTranscript; got != transcriptCursorKey(historySource) {
		t.Fatalf("seed historical cursor = %q, want H0 %q", got, transcriptCursorKey(historySource))
	}
	if err := os.Remove(historyPath); err != nil {
		t.Fatalf("remove synthetic H0: %v", err)
	}
	sources = []TranscriptSource{historySource, backlogSource, liveSource}
	for cycle := 1; cycle <= 4; cycle++ {
		if cycle > 1 {
			appendJSONL(t, livePath, usageCodex(float64(cycle+1), 1, 0, 1), false)
		}
		batch := commitCycle(cycle)
		if cycle == 3 {
			if len(batch.UnavailableSpans) == 0 || batch.UnavailableSpans[0].SourceID != "history-unavailable" {
				t.Fatalf("failed fairness-cycle spans = %+v, want unavailable H0", batch.UnavailableSpans)
			}
			if got := collector.nextHistoricalTranscript; got != transcriptCursorKey(backlogSource) {
				t.Fatalf("historical cursor after attempting unavailable H0 = %q, want H1 %q", got, transcriptCursorKey(backlogSource))
			}
		}
		if cycle == 4 && (len(batch.CoverageEpochs) != 1 || batch.CoverageEpochs[0].SourceID != "history-backlog") {
			t.Fatalf("fairness cycle coverage = %+v, want H1 after unavailable H0", batch.CoverageEpochs)
		}
	}
}

func TestDefaultAgentTokenSourcesMarkProcessOwnedCodexTranscriptLive(t *testing.T) {
	path := filepath.Join(t.TempDir(), "live.jsonl")
	writeJSONL(t, path, usageCodex(1, 1, 0, 1))
	sources, err := defaultAgentTokenSourcesBounded(context.Background(), []AgentRecord{{
		Kind: "codex", TmuxSession: "synthetic", TranscriptPath: path,
	}}, transcriptDiscoveryBounds{maxFiles: 1, maxDirs: 1, maxBytes: 1 << 20})
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) != 1 || sources[0].Path != path || !sources[0].LiveCandidate {
		t.Fatalf("process-owned synthetic Codex transcript sources = %+v", sources)
	}
}

type failInitialTranscriptBatchCursor struct {
	*TranscriptCursorStore
	prepareBatchCalls int
}

func (c *failInitialTranscriptBatchCursor) PrepareBatchWithNextSource(changes []transcriptCursorChange, nextSource string) error {
	c.prepareBatchCalls++
	if c.prepareBatchCalls == 1 && len(changes) > 0 {
		return errors.New("synthetic batch-prepare conflict")
	}
	return c.TranscriptCursorStore.PrepareBatchWithNextSource(changes, nextSource)
}

func (c *failInitialTranscriptBatchCursor) PrepareBatchWithSchedules(changes []transcriptCursorChange, nextSource, nextHistoricalSource string, liveTranscriptPriorityRuns int) error {
	c.prepareBatchCalls++
	if c.prepareBatchCalls == 1 && len(changes) > 0 {
		return errors.New("synthetic batch-prepare conflict")
	}
	return c.TranscriptCursorStore.PrepareBatchWithSchedules(changes, nextSource, nextHistoricalSource, liveTranscriptPriorityRuns)
}

func (c *failInitialTranscriptBatchCursor) PrepareBatchWithHistoricalSchedule(changes []transcriptCursorChange, nextHistoricalSource string, liveTranscriptPriorityRuns int) error {
	return c.TranscriptCursorStore.PrepareBatchWithHistoricalSchedule(changes, nextHistoricalSource, liveTranscriptPriorityRuns)
}

func TestAgentTokenCollectorUsesPerSourcePrepareFallback(t *testing.T) {
	root := t.TempDir()
	transcript := filepath.Join(root, "claude.jsonl")
	writeJSONL(t, transcript, usageClaude(1, "fallback", 4, 2))
	source := TranscriptSource{Path: transcript, Provider: "claude", SourceID: "synthetic-claude", ThreadID: "fallback"}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{source}, nil
		},
		TranscriptCursorPath: filepath.Join(root, "transcript-cursor.json"),
	})
	cursor := &failInitialTranscriptBatchCursor{TranscriptCursorStore: NewTranscriptCursorStore(collector.config.TranscriptCursorPath)}
	collector.transcriptCursor = cursor
	batch, receipts, err := collector.collectTranscripts(context.Background(), 10, 1, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.UsageAtoms) != 2 || len(batch.UnavailableSpans) != 0 || len(receipts) != 1 || cursor.prepareBatchCalls != 2 {
		t.Fatalf("per-source fallback collection = batch=%+v receipts=%d batch_prepare_calls=%d", batch, len(receipts), cursor.prepareBatchCalls)
	}
	if err := receipts[0].commit(); err != nil {
		t.Fatal(err)
	}
	state, err := cursor.State(source)
	if err != nil || state.Offset != transcriptFixtureSize(t, transcript) {
		t.Fatalf("fallback cursor commit = %+v, err=%v", state, err)
	}
}

func TestAgentTokenCollectorRollsBackTranscriptAndOpenCodeCursor(t *testing.T) {
	root := t.TempDir()
	transcript := filepath.Join(root, "claude.jsonl")
	writeJSONL(t, transcript, usageClaude(10, "one", 4, 3))
	opencodePath := createOpenCodeFixture(t)
	cursorPath := filepath.Join(root, "cursor.json")
	transcriptCursorPath := filepath.Join(root, "transcript-cursors.json")
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.db.ExecContext(context.Background(), `CREATE TRIGGER synthetic_abort_usage_atom
		BEFORE INSERT ON usage_atoms
		BEGIN SELECT RAISE(ABORT, 'synthetic usage append failure'); END`); err != nil {
		t.Fatal(err)
	}
	clock := &agentStatusTestClock{now: time.Unix(20, 0)}
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "claude"}, {Kind: "opencode", OpenCodeSession: "ses-1"}}, nil
		},
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{{Path: transcript, Provider: "claude", SourceID: "claude", ThreadID: "one"}}, nil
		},
		OpenCodeDatabase: opencodePath, CursorPath: cursorPath,
		TranscriptCursorPath: transcriptCursorPath, EpochStartedAt: 1,
	})
	initialOpenCodeState, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	collector.RunOnce(context.Background())
	if counters := collector.Counters(); counters.Errors != 1 || counters.UsageAccepted != 0 {
		t.Fatalf("failed SQLite append counters = %+v, want one error and no accepted usage", counters)
	}
	transcriptState, err := collector.transcriptCursor.State(TranscriptSource{
		Path: transcript, Provider: "claude", SourceID: "claude", ThreadID: "one",
	})
	if err != nil || transcriptState.Offset != 0 {
		t.Fatalf("transcript cursor after failed SQLite append = %+v err=%v, want offset 0", transcriptState, err)
	}
	openCodeState, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil || !reflect.DeepEqual(openCodeState, initialOpenCodeState) {
		t.Fatalf("OpenCode cursor after failed SQLite append = %+v err=%v, want unchanged %+v", openCodeState, err, initialOpenCodeState)
	}
	var persistedUsage int
	if err := writer.db.QueryRowContext(context.Background(), "SELECT count(*) FROM usage_atoms").Scan(&persistedUsage); err != nil || persistedUsage != 0 {
		t.Fatalf("usage persisted after failed SQLite append = %d err=%v, want zero", persistedUsage, err)
	}
	if _, err := writer.db.ExecContext(context.Background(), "DROP TRIGGER synthetic_abort_usage_atom"); err != nil {
		t.Fatal(err)
	}
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 5 {
		t.Fatalf("replayed usage after rollback = %d counters=%+v", got, collector.Counters())
	}
	transcriptState, err = collector.transcriptCursor.State(TranscriptSource{
		Path: transcript, Provider: "claude", SourceID: "claude", ThreadID: "one",
	})
	if err != nil || transcriptState.Offset != transcriptFixtureSize(t, transcript) {
		t.Fatalf("transcript cursor after successful retry = %+v err=%v", transcriptState, err)
	}
	openCodeState, err = NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil || len(openCodeState.Events) == 0 {
		t.Fatalf("OpenCode cursor after successful retry = %+v err=%v", openCodeState, err)
	}
}

func TestOpenCodeCursorRepairsPendingJournalOnRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cursor.json")
	cursor := NewOpenCodeTokenCursor(path)
	pending := openCodeCursorState{
		Events: map[string]string{"a": "b"},
		RawScan: openCodeRawScanState{
			HasScan: true, SelectionKey: "synthetic-selection", ScannedThroughRowID: 20,
			HighWaterRowID: 30, SourceDataVersion: 4,
		},
	}
	if err := cursor.prepare(openCodeCursorState{}, pending); err != nil {
		t.Fatal(err)
	}
	restarted := NewOpenCodeTokenCursor(path)
	state, err := restarted.state()
	if err != nil {
		t.Fatalf("pending cursor was not repaired: %v", err)
	}
	if len(state.Events) != 0 || state.RawScan.HasScan {
		t.Fatalf("interrupted pending state was promoted instead of discarded: %+v", state)
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(contents), `"pending"`) {
		t.Fatalf("pending journal remained after restart: %s", contents)
	}
}

func TestOpenCodeCursorCommitsAndReloadsRawPageProgress(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cursor.json")
	cursor := NewOpenCodeTokenCursor(path)
	before := openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}}
	next := before.clone()
	next.RawScan = openCodeRawScanState{
		HasScan: true, SelectionKey: "synthetic-selection", ScannedThroughRowID: 12,
		HighWaterRowID: 20, SourceDataVersion: 7,
		RootErrors:     map[string]string{"root-synthetic": "malformed-transcript-json"},
		ScanRootErrors: map[string]string{"root-synthetic": "malformed-transcript-json"},
		ScanDirty:      true,
	}
	if err := cursor.prepare(before, next); err != nil {
		t.Fatal(err)
	}
	if err := cursor.commit(next); err != nil {
		t.Fatal(err)
	}
	reloaded, err := NewOpenCodeTokenCursor(path).state()
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(reloaded.RawScan, next.RawScan) {
		t.Fatalf("reloaded raw page progress = %+v, want %+v", reloaded.RawScan, next.RawScan)
	}
	reloaded.RawScan.RootErrors["root-synthetic"] = "mutated"
	if next.RawScan.RootErrors["root-synthetic"] != "malformed-transcript-json" {
		t.Fatal("cursor clone shared mutable raw-scan errors with caller state")
	}
}

func TestOpenCodeCursorMigratesLegacyMalformedRowRefsToBoundedRescan(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cursor.json")
	legacy := `{"version":1,"generation":4,"state":{"events":{},"parts":{},"raw_scan":{"has_scan":true,"selection_key":"synthetic-selection","scanned_through_rowid":4,"high_water_rowid":4,"source_data_version":7,"root_errors":{"root-synthetic":"malformed-transcript-json"},"error_refs":[{"RootSessionID":"root-synthetic","SessionID":"session-synthetic","RowID":4}],"error_check_after_rowid":4}}}`
	if err := os.WriteFile(path, []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}
	cursor := NewOpenCodeTokenCursor(path)
	state, err := cursor.state()
	if err != nil {
		t.Fatal(err)
	}
	if !state.RawScan.ForceRescan || state.RawScan.RootErrors["root-synthetic"] != "malformed-transcript-json" || len(state.RawScan.ErrorRefs) != 0 {
		t.Fatalf("legacy cursor migration = %+v, want preserved summary and forced rescan without row refs", state.RawScan)
	}
	next := state.clone()
	if err := cursor.prepare(state, next); err != nil {
		t.Fatal(err)
	}
	if err := cursor.commit(next); err != nil {
		disk, readErr := cursor.readDisk()
		t.Fatalf("commit migrated cursor: %v; readErr=%v disk=%+v pending=%+v expected=%+v", err, readErr, disk, disk.Pending, next)
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(contents), `"version":2`) || strings.Contains(string(contents), `"error_refs"`) {
		t.Fatalf("migrated cursor sidecar retained legacy row refs or version: %s", contents)
	}
}

func TestOpenCodeSelectionKeyIsStableAndTracksRootOwnership(t *testing.T) {
	first := []OpenCodeStepFinishSelection{
		{SessionID: "child-b", RootSessionID: "root-b"},
		{SessionID: "child-a", RootSessionID: "root-a"},
	}
	reordered := []OpenCodeStepFinishSelection{first[1], first[0]}
	if openCodeSelectionKey(first) != openCodeSelectionKey(reordered) {
		t.Fatal("selection key changed when equivalent selections were reordered")
	}
	reassigned := []OpenCodeStepFinishSelection{first[1], {SessionID: "child-b", RootSessionID: "root-a"}}
	if openCodeSelectionKey(first) == openCodeSelectionKey(reassigned) {
		t.Fatal("selection key ignored a changed root owner")
	}
}

func TestAgentTokenCollectorCommitsEmptyRawPagesAndResumesSyntheticHistory(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode-synthetic.db")
	sqlDB, err := sql.Open("sqlite3", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	now := int64(1788307300000)
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		fmt.Sprintf(`INSERT INTO session VALUES ('root', '/synthetic', 'build', '{"providerID":"synthetic","id":"model"}', %d, %d, 0, NULL)`, now-int64(time.Hour/time.Millisecond), now),
	} {
		if _, err := sqlDB.Exec(statement); err != nil {
			_ = sqlDB.Close()
			t.Fatal(err)
		}
	}
	tx, err := sqlDB.Begin()
	if err != nil {
		_ = sqlDB.Close()
		t.Fatal(err)
	}
	rowCount := MaxOpenCodePartReadLimit + 2
	for rowID := 1; rowID <= rowCount; rowID++ {
		messageID := fmt.Sprintf("message-%04d", rowID)
		partID := fmt.Sprintf("part-%04d", rowID)
		if _, err := tx.Exec(`INSERT INTO message VALUES (?, 'root', ?, ?, '{"role":"assistant"}')`, messageID, now, now); err != nil {
			_ = tx.Rollback()
			_ = sqlDB.Close()
			t.Fatal(err)
		}
		partData := `{"type":"tool","name":"shell"}`
		if rowID == 1 {
			partData = "{"
		}
		if rowID == MaxOpenCodePartReadLimit+1 {
			partData = `{"type":"step-finish","tokens":{"input":7,"output":2}}`
		}
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, 'root', ?, ?, ?)`, partID, messageID, now, now, partData); err != nil {
			_ = tx.Rollback()
			_ = sqlDB.Close()
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		_ = sqlDB.Close()
		t.Fatal(err)
	}
	if err := sqlDB.Close(); err != nil {
		t.Fatal(err)
	}
	clock := &agentStatusTestClock{now: time.Unix(now/1000, 0)}
	cursorPath := filepath.Join(root, "opencode-cursor.json")
	var lastBatch AppendBatch
	writer := tokenWriterFunc(func(_ context.Context, batch AppendBatch) (AppendResult, error) {
		lastBatch = batch
		return AppendResult{UsageAccepted: len(batch.UsageAtoms), CoverageChanged: len(batch.CoverageEpochs), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
	})
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "root", TmuxSession: "synthetic"}}, nil
		},
		CursorPath: cursorPath, SkipTranscriptScan: true, OpenCodeCollectionTimeout: 20 * time.Second,
	})
	collector.RunOnce(context.Background())
	first, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if first.RawScan.ScannedThroughRowID != MaxOpenCodePartReadLimit || first.RawScan.HighWaterRowID != int64(rowCount) {
		t.Fatalf("first raw page progress=%+v, want through=%d high-water=%d batch=%+v", first.RawScan, MaxOpenCodePartReadLimit, rowCount, lastBatch)
	}
	if first.RawScan.ScanRootErrors["root"] != "malformed-transcript-json" {
		t.Fatalf("first raw page errors=%v, want the malformed synthetic row recorded in the pending summary", first.RawScan.ScanRootErrors)
	}
	if len(first.RawScan.ErrorRefs) != 0 {
		t.Fatalf("first raw page persisted per-row error refs=%v, want bounded root summary only", first.RawScan.ErrorRefs)
	}
	if len(first.Events) != 0 {
		t.Fatalf("root with incomplete malformed history emitted usage dimensions=%d; batch=%+v state=%+v", len(first.Events), lastBatch, first)
	}

	mutateSQLite(t, databasePath, `INSERT INTO message VALUES ('message-tail', 'root', 1788307300000, 1788307300000, '{"role":"assistant"}')`)
	mutateSQLite(t, databasePath, `INSERT INTO part VALUES ('part-tail', 'message-tail', 'root', 1788307300000, 1788307300000, '{"type":"tool","name":"newer-noise"}')`)
	mutateSQLite(t, databasePath, `UPDATE part SET data='{' WHERE id='part-0002'`)
	clock.now = clock.now.Add(AgentTokenActiveCadence)
	collector.RunOnce(context.Background())
	completed, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if completed.RawScan.ScannedThroughRowID != 0 || completed.RawScan.HighWaterRowID != int64(rowCount+1) || completed.RawScan.ScanDirty || !completed.RawScan.ForceRescan {
		t.Fatalf("mid-scan source change did not start a clean pass at the expanded high-water mark: %+v", completed.RawScan)
	}
	for completed.RawScan.ScannedThroughRowID < completed.RawScan.HighWaterRowID {
		clock.now = clock.now.Add(AgentTokenActiveCadence)
		collector.RunOnce(context.Background())
		completed, err = NewOpenCodeTokenCursor(cursorPath).state()
		if err != nil {
			t.Fatal(err)
		}
	}
	if completed.RawScan.ScannedThroughRowID != int64(rowCount+1) || completed.RawScan.HighWaterRowID != int64(rowCount+1) {
		t.Fatalf("completed raw page progress=%+v, want through/high-water=%d after unrelated writes", completed.RawScan, rowCount+1)
	}
	if completed.RawScan.RootErrors["root"] != "malformed-transcript-json" {
		t.Fatalf("completed raw scan lost malformed-row evidence: %v", completed.RawScan.RootErrors)
	}
	mutateSQLite(t, databasePath, `UPDATE part SET data='{"type":"tool","name":"repaired"}' WHERE id IN ('part-0001', 'part-0002')`)
	clock.now = clock.now.Add(AgentTokenActiveCadence)
	collector.RunOnce(context.Background())
	repaired, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	for repaired.RawScan.ScannedThroughRowID < repaired.RawScan.HighWaterRowID {
		clock.now = clock.now.Add(AgentTokenActiveCadence)
		collector.RunOnce(context.Background())
		repaired, err = NewOpenCodeTokenCursor(cursorPath).state()
		if err != nil {
			t.Fatal(err)
		}
	}
	if len(repaired.RawScan.RootErrors) != 0 {
		t.Fatalf("repaired synthetic source retained stale raw-scan errors: %v", repaired.RawScan.RootErrors)
	}
	coverageForRoot := false
	malformedUnavailable := false
	for _, coverage := range lastBatch.CoverageEpochs {
		coverageForRoot = coverageForRoot || coverage.SourceID == "opencode:root"
	}
	for _, unavailable := range lastBatch.UnavailableSpans {
		malformedUnavailable = malformedUnavailable || unavailable.SourceID == "opencode:root" && unavailable.Reason == "malformed-transcript-json"
	}
	if !coverageForRoot || malformedUnavailable {
		t.Fatalf("repaired root coverage=%v malformed-unavailable=%v; batch=%+v", coverageForRoot, malformedUnavailable, lastBatch)
	}
	if got := collector.Counters().UsageAccepted; got != 2 {
		t.Fatalf("usage accepted after replaying synthetic history = %d, want stable input/output facts exactly once", got)
	}
	collector.Stop()
}

func TestAgentTokenCollectorKeepsMalformedHistorySummaryBoundedAndReconcilesRepairs(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode-synthetic.db")
	sqlDB, err := sql.Open("sqlite3", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	now := int64(1788307300000)
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE INDEX part_session_updated ON part(session_id, time_updated DESC)`,
		fmt.Sprintf(`INSERT INTO session VALUES ('root', '/synthetic', 'build', '{"providerID":"synthetic","id":"model"}', %d, %d, 0, NULL)`, now-int64(time.Hour/time.Millisecond), now),
	} {
		if _, err := sqlDB.Exec(statement); err != nil {
			_ = sqlDB.Close()
			t.Fatal(err)
		}
	}
	tx, err := sqlDB.Begin()
	if err != nil {
		_ = sqlDB.Close()
		t.Fatal(err)
	}
	rowCount := MaxOpenCodePartReadLimit + 17
	for rowID := 1; rowID <= rowCount; rowID++ {
		messageID := fmt.Sprintf("message-%04d", rowID)
		partID := fmt.Sprintf("part-%04d", rowID)
		if _, err := tx.Exec(`INSERT INTO message VALUES (?, 'root', ?, ?, '{"role":"assistant"}')`, messageID, now, now); err != nil {
			_ = tx.Rollback()
			_ = sqlDB.Close()
			t.Fatal(err)
		}
		if _, err := tx.Exec(`INSERT INTO part VALUES (?, ?, 'root', ?, ?, '{')`, partID, messageID, now, now); err != nil {
			_ = tx.Rollback()
			_ = sqlDB.Close()
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		_ = sqlDB.Close()
		t.Fatal(err)
	}
	if err := sqlDB.Close(); err != nil {
		t.Fatal(err)
	}
	clock := &agentStatusTestClock{now: time.Unix(now/1000, 0)}
	cursorPath := filepath.Join(root, "opencode-cursor.json")
	var lastBatch AppendBatch
	writer := tokenWriterFunc(func(_ context.Context, batch AppendBatch) (AppendResult, error) {
		lastBatch = batch
		return AppendResult{UsageAccepted: len(batch.UsageAtoms), CoverageChanged: len(batch.CoverageEpochs), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
	})
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "root", TmuxSession: "synthetic"}}, nil
		},
		CursorPath: cursorPath, SkipTranscriptScan: true, OpenCodeCollectionTimeout: 20 * time.Second,
	})
	collector.RunOnce(context.Background())
	initial, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if len(initial.RawScan.ErrorRefs) != 0 || len(initial.RawScan.RootErrors) != 0 || initial.RawScan.ScanRootErrors["root"] != "malformed-transcript-json" {
		t.Fatalf("first bounded scan summary refs=%d published=%v pending=%v batch=%+v, want no row refs and one in-progress root summary", len(initial.RawScan.ErrorRefs), initial.RawScan.RootErrors, initial.RawScan.ScanRootErrors, lastBatch)
	}
	if initial.RawScan.ScannedThroughRowID != MaxOpenCodePartReadLimit || initial.RawScan.HighWaterRowID != int64(rowCount) {
		t.Fatalf("first malformed-history page progress=%+v", initial.RawScan)
	}
	for initial.RawScan.ScannedThroughRowID < initial.RawScan.HighWaterRowID {
		clock.now = clock.now.Add(AgentTokenActiveCadence)
		collector.RunOnce(context.Background())
		initial, err = NewOpenCodeTokenCursor(cursorPath).state()
		if err != nil {
			t.Fatal(err)
		}
	}
	if initial.RawScan.RootErrors["root"] != "malformed-transcript-json" || len(initial.RawScan.ScanRootErrors) != 0 || len(initial.RawScan.ErrorRefs) != 0 {
		t.Fatalf("completed malformed-history summary=%+v, want one published root error and no per-row refs", initial.RawScan)
	}
	sidecar, err := os.ReadFile(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(sidecar) > 4096 || strings.Contains(string(sidecar), `"error_refs"`) {
		t.Fatalf("sidecar grew with %d malformed rows: bytes=%d contains legacy row refs=%t", rowCount, len(sidecar), strings.Contains(string(sidecar), `"error_refs"`))
	}

	mutateSQLite(t, databasePath, `UPDATE part SET data='{"type":"tool","name":"repaired"}'`)
	clock.now = clock.now.Add(AgentTokenActiveCadence)
	collector.RunOnce(context.Background())
	completed, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	for completed.RawScan.ScannedThroughRowID < completed.RawScan.HighWaterRowID {
		clock.now = clock.now.Add(AgentTokenActiveCadence)
		collector.RunOnce(context.Background())
		completed, err = NewOpenCodeTokenCursor(cursorPath).state()
		if err != nil {
			t.Fatal(err)
		}
	}
	if len(completed.RawScan.ErrorRefs) != 0 || len(completed.RawScan.RootErrors) != 0 || len(completed.RawScan.ScanRootErrors) != 0 {
		t.Fatalf("repaired synthetic source retained row refs or root errors: %+v; batch=%+v", completed.RawScan, lastBatch)
	}
	coverageForRoot := false
	malformedUnavailable := false
	for _, coverage := range lastBatch.CoverageEpochs {
		coverageForRoot = coverageForRoot || coverage.SourceID == "opencode:root"
	}
	for _, unavailable := range lastBatch.UnavailableSpans {
		malformedUnavailable = malformedUnavailable || unavailable.SourceID == "opencode:root" && unavailable.Reason == "malformed-transcript-json"
	}
	if !coverageForRoot || malformedUnavailable {
		t.Fatalf("reconciled root coverage=%v malformed-unavailable=%v; batch=%+v", coverageForRoot, malformedUnavailable, lastBatch)
	}
	collector.Stop()
}

func TestOpenCodeCursorDiscardedPendingJournalAdvancesGeneration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cursor.json")
	cursor := NewOpenCodeTokenCursor(path)
	next := openCodeCursorState{Events: map[string]string{"event": "next"}}
	if err := cursor.prepare(openCodeCursorState{}, next); err != nil {
		t.Fatal(err)
	}
	restarted := NewOpenCodeTokenCursor(path)
	if _, err := restarted.state(); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var disk openCodeCursorDisk
	if err := json.Unmarshal(data, &disk); err != nil {
		t.Fatal(err)
	}
	if disk.Generation != 2 {
		t.Fatalf("discarded pending generation = %d, want 2", disk.Generation)
	}
	if err := restarted.prepare(openCodeCursorState{}, next); err != nil {
		t.Fatalf("new owner could not prepare after pending recovery: %v", err)
	}
}

func TestOpenCodeCursorRejectsStaleCommitWithoutClobberingNewPrepare(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cursor.json")
	cursor := NewOpenCodeTokenCursor(path)
	first := openCodeCursorState{Events: map[string]string{"event": "first"}}
	second := openCodeCursorState{Events: map[string]string{"event": "second"}}
	third := openCodeCursorState{Events: map[string]string{"event": "third"}}
	if err := cursor.prepare(openCodeCursorState{}, first); err != nil {
		t.Fatal(err)
	}
	if err := cursor.commit(first); err != nil {
		t.Fatal(err)
	}
	if err := cursor.prepare(first, second); err != nil {
		t.Fatal(err)
	}
	if err := cursor.commit(third); err == nil {
		t.Fatal("stale OpenCode receipt committed a different pending state")
	}
	if err := cursor.commit(second); err != nil {
		t.Fatalf("new OpenCode receipt could not commit after stale rejection: %v", err)
	}
	loaded, err := NewOpenCodeTokenCursor(path).state()
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Events["event"] != "second" {
		t.Fatalf("stale commit changed durable OpenCode cursor: %+v", loaded)
	}
	if err := cursor.prepare(first, third); err == nil {
		t.Fatal("stale OpenCode operation prepared over the committed cursor")
	}
}

func TestOpenCodeCursorWriteFailureDoesNotAdvanceMemory(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "cursor.json")
	if err := os.Mkdir(path, 0o700); err != nil {
		t.Fatal(err)
	}
	cursor := NewOpenCodeTokenCursor(path)
	next := openCodeCursorState{Events: map[string]string{"event": "next"}}
	if err := cursor.prepare(openCodeCursorState{}, next); err == nil {
		t.Fatal("prepare unexpectedly succeeded with a directory sidecar path")
	}
	if cursor.pending != nil || cursor.pendingToken != "" {
		t.Fatalf("failed prepare advanced in-memory state: pending=%v token=%q", cursor.pending, cursor.pendingToken)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := cursor.prepare(openCodeCursorState{}, next); err != nil {
		t.Fatal(err)
	}
	if err := cursor.commit(next); err != nil {
		t.Fatal(err)
	}
}

func TestAgentTokenCollectorReplaysOpenCodeAfterDatabaseReplacement(t *testing.T) {
	root := t.TempDir()
	databasePath := createOpenCodeFixture(t)
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &agentStatusTestClock{now: time.Unix(3, 0)}
	session := "ses-1"
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", TmuxSession: "opencode", OpenCodeSession: session}}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "opencode-cursor.json"),
		EpochStartedAt:   1,
		OwnerGeneration:  1,
	})
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 3 {
		t.Fatalf("initial OpenCode usage = %d, want 3", got)
	}

	replacement := filepath.Join(root, "replacement.db")
	createOpenCodeFixtureWithTitle(t, replacement, "ses-replaced", "/repo", "replacement", 2000)
	if err := os.Rename(replacement, databasePath); err != nil {
		t.Fatal(err)
	}
	session = "ses-replaced"
	clock.now = time.Unix(4, 0)
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 6 {
		t.Fatalf("replacement OpenCode usage = %d, want 6", got)
	}
}

func TestAgentTokenCollectorUsesInitialHistoryWindowPerNewOpenCodeRoot(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode-mixed-roots.db")
	database, err := sql.Open("sqlite3", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('root-a', '/repo-a', 'build', '{"providerID":"fixture","id":"model"}', 999000, 999000, 0, NULL)`,
		`INSERT INTO session VALUES ('root-b', '/repo-b', 'build', '{"providerID":"fixture","id":"model"}', 100000, 1000000, 0, NULL)`,
		`INSERT INTO message VALUES ('msg-a', 'root-a', 999000, 999000, '{"role":"assistant"}')`,
		`INSERT INTO message VALUES ('msg-b', 'root-b', 100000, 100000, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-a', 'msg-a', 'root-a', 999000, 999000, '{"type":"step-finish","tokens":{"input":2}}')`,
		`INSERT INTO part VALUES ('part-b', 'msg-b', 'root-b', 100000, 100000, '{"type":"step-finish","tokens":{"input":7}}')`,
	} {
		if _, err := database.Exec(statement); err != nil {
			_ = database.Close()
			t.Fatal(err)
		}
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	clock := &agentStatusTestClock{now: time.Unix(1000, 0)}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	includeNewRoot := false
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			records := []AgentRecord{{Kind: "opencode", OpenCodeSession: "root-a", TmuxSession: "opencode-a"}}
			if includeNewRoot {
				records = append(records, AgentRecord{Kind: "opencode", OpenCodeSession: "root-b", TmuxSession: "opencode-b"})
			}
			return records, nil
		},
		CursorPath: filepath.Join(root, "opencode-cursors.json"), EpochStartedAt: 1, OwnerGeneration: 1,
	})
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 1 {
		t.Fatalf("established-root usage = %d, want 1", got)
	}
	includeNewRoot = true
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 2 {
		t.Fatalf("new-root historical usage = %d, want 2 total; per-root initial window was not used", got)
	}
}

func TestAgentTokenCollectorMarksInPlaceOpenCodeRevisionUnavailable(t *testing.T) {
	root := t.TempDir()
	databasePath := createOpenCodeFixture(t)
	clock := &agentStatusTestClock{now: time.Unix(3, 0)}
	collector := NewAgentTokenCollector(countingTokenWriter{}, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", TmuxSession: "opencode", OpenCodeSession: "ses-1"}}, nil
		},
		CursorPath: filepath.Join(root, "opencode-cursor.json"), EpochStartedAt: 1, OwnerGeneration: 1,
	})
	collector.RunOnce(context.Background())
	first := collector.Counters()
	if first.UsageAccepted != 3 || first.UnavailableAccepted != 0 {
		t.Fatalf("initial OpenCode counters = %+v", first)
	}

	// Rewrite the existing row in place, preserving the database inode. The
	// stable event identity must not double-count revised tokens; the collector
	// records an explicit unavailable interval so the browser never sees a
	// fabricated replacement total.
	mutateSQLite(t, databasePath, `UPDATE session SET time_updated = 4000 WHERE id = 'ses-1'`)
	mutateSQLite(t, databasePath, `UPDATE part SET time_updated = 4000, data = '{"type":"step-finish","tokens":{"total":20,"input":9,"output":8,"cache":{"read":3}}}' WHERE id = 'part-1'`)
	clock.now = time.Unix(5, 0)
	collector.RunOnce(context.Background())
	second := collector.Counters()
	if second.UsageAccepted != 3 || second.UnavailableAccepted != 1 {
		t.Fatalf("in-place OpenCode revision counters = %+v", second)
	}
}

func TestAgentTokenCollectorRescansOpenCodeAfterOpeningFreshReadHandle(t *testing.T) {
	root := t.TempDir()
	databasePath := createOpenCodeFixture(t)
	updated := int64(1788307300000)
	mutateSQLite(t, databasePath, fmt.Sprintf(`UPDATE session SET time_created = %d, time_updated = %d WHERE id = 'ses-1'`, updated-int64(time.Hour/time.Millisecond), updated))
	clock := &agentStatusTestClock{now: time.Unix(updated/1000, 0)}
	cursorPath := filepath.Join(root, "opencode-cursor.json")
	collector := NewAgentTokenCollector(countingTokenWriter{}, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", TmuxSession: "opencode", OpenCodeSession: "ses-1"}}, nil
		},
		CursorPath: cursorPath, EpochStartedAt: 1, OwnerGeneration: 1,
	})
	collector.RunOnce(context.Background())
	initial, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if !initial.RawScan.HasScan || initial.RawScan.ScannedThroughRowID < initial.RawScan.HighWaterRowID {
		t.Fatalf("initial synthetic history scan did not complete: %+v", initial.RawScan)
	}
	if got := collector.Counters().UsageAccepted; got != 3 {
		t.Fatalf("initial synthetic usage accepted = %d, want 3", got)
	}

	// Change an old row without changing its ordering timestamp. A fresh SQLite
	// handle starts with a connection-local data_version that can equal the
	// sidecar's saved value even though the handle has no prior scan history.
	mutateSQLite(t, databasePath, `UPDATE part SET data = '{"type":"step-finish","tokens":{"total":20,"input":9,"output":8,"cache":{"read":3}}}' WHERE id = 'part-1'`)
	freshDatabase, err := OpenOpenCodeReadOnly(context.Background(), databasePath)
	if err != nil {
		t.Fatal(err)
	}
	freshVersion, err := freshDatabase.ReadSourceDataVersion(context.Background())
	if closeErr := freshDatabase.Close(); closeErr != nil {
		t.Fatal(closeErr)
	}
	if err != nil {
		t.Fatal(err)
	}
	if freshVersion != initial.RawScan.SourceDataVersion {
		t.Fatalf("fresh connection data_version = %d, want saved connection-local value %d", freshVersion, initial.RawScan.SourceDataVersion)
	}
	collector.closeOpenCodeDatabase()
	clock.now = clock.now.Add(AgentTokenActiveCadence)
	collector.RunOnce(context.Background())
	got := collector.Counters()
	if got.UsageAccepted != 3 || got.UnavailableAccepted != 1 {
		t.Fatalf("fresh-handle source rescan counters = %+v, want unchanged usage and one revision warning", got)
	}
}

func TestOpenCodeCursorBoundsEventRevisionMap(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cursor.json")
	events := make(map[string]string, 100_001)
	for index := 0; index < 100_001; index++ {
		events[fmt.Sprintf("event-%06d", index)] = "revision"
	}
	oversizedKey := strings.Repeat("x", MaxEventIDBytes+1)
	events[oversizedKey] = "revision"
	events["oversized-value"] = strings.Repeat("v", maxOpenCodeRevisionBytes+1)
	state := openCodeCursorState{Events: events, Parts: map[string]OpenCodePartCursor{}}
	encoded, err := json.Marshal(openCodeCursorDisk{Version: 2, State: state})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	cursor := NewOpenCodeTokenCursor(path)
	loaded, err := cursor.state()
	if err != nil {
		t.Fatal(err)
	}
	if len(loaded.Events) != 100_000 {
		t.Fatalf("loaded event revisions = %d, want bounded 100000 entries", len(loaded.Events))
	}
	if _, ok := loaded.Events["event-000000"]; ok {
		t.Fatal("oldest lexicographic event revision was retained after bounded suffix compaction")
	}
	if _, ok := loaded.Events["event-100000"]; !ok {
		t.Fatal("newest lexicographic event revision was lost during bounded suffix compaction")
	}
	if _, ok := loaded.Events[oversizedKey]; ok {
		t.Fatal("oversized event revision key survived cursor compaction")
	}
	if _, ok := loaded.Events["oversized-value"]; ok {
		t.Fatal("oversized event revision value survived cursor compaction")
	}
	compactedSidecar, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(compactedSidecar), `"event-000000"`) || strings.Contains(string(compactedSidecar), oversizedKey) || strings.Contains(string(compactedSidecar), "oversized-value") {
		t.Fatal("cursor load did not persist event revision compaction")
	}

	next := loaded.clone()
	batch := AppendBatch{}
	collector := &AgentTokenCollector{}
	collector.addOpenCodePart(&batch, next, OpenCodeStepFinishPart{
		SessionID: "z-session", PartID: "z-part", Tokens: OpenCodeTokenSnapshot{
			Input: 1, Present: map[string]bool{"input": true},
		},
	}, "synthetic-agent", "synthetic-root", 1, 1)
	if len(next.Events) != 100_001 {
		t.Fatalf("event revisions before cursor prepare = %d, want one temporary entry above the persisted bound", len(next.Events))
	}
	if err := cursor.prepare(loaded, next); err != nil {
		t.Fatal(err)
	}
	if err := cursor.commit(next); err != nil {
		t.Fatal(err)
	}
	if len(next.Events) != 100_000 {
		t.Fatalf("event revisions after cursor prepare = %d, want bounded 100000 entries", len(next.Events))
	}
	if _, ok := next.Events["event-000001"]; ok {
		t.Fatal("new event insertion did not evict the oldest lexicographic revision at the cursor boundary")
	}

	pendingEvents := make(map[string]string, 100_001)
	for index := 0; index < 100_001; index++ {
		pendingEvents[fmt.Sprintf("pending-%06d", index)] = "revision"
	}
	pendingPayload, err := json.Marshal(openCodeCursorDisk{
		Version: 2, State: openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}},
		Pending: &openCodeCursorState{Events: pendingEvents, Parts: map[string]OpenCodePartCursor{}}, PendingToken: "synthetic-pending",
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, pendingPayload, 0o600); err != nil {
		t.Fatal(err)
	}
	disk, err := cursor.readDisk()
	if err != nil {
		t.Fatal(err)
	}
	if disk.Pending == nil {
		t.Fatal("cursor read dropped the synthetic pending state")
	}
	if !disk.EventsCompacted || len(disk.Pending.Events) != 100_000 {
		t.Fatalf("pending event revision compaction = compacted=%t events=%d, want bounded 100000-entry pending state", disk.EventsCompacted, len(disk.Pending.Events))
	}
}

func TestOpenCodeRevisionKeyBoundsLongIdentifiers(t *testing.T) {
	part := OpenCodeStepFinishPart{
		SessionID: strings.Repeat("s", MaxEventIDBytes),
		PartID:    strings.Repeat("p", MaxEventIDBytes),
		Tokens: OpenCodeTokenSnapshot{
			Input: 1, Present: map[string]bool{"input": true},
		},
	}
	state := openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}}
	(&AgentTokenCollector{}).addOpenCodePart(&AppendBatch{}, state, part, "synthetic-agent", "synthetic-root", 1, 1)
	for key := range state.Events {
		if len([]byte(key)) > MaxEventIDBytes {
			t.Fatalf("event revision key contains %d bytes, exceeds %d-byte contract", len([]byte(key)), MaxEventIDBytes)
		}
	}
	if len(state.Events) != 1 {
		t.Fatalf("long-identifier revision count = %d, want one", len(state.Events))
	}
}

func TestAgentTokenSourceScanRetriesAfterStatsAppendFailure(t *testing.T) {
	root := t.TempDir()
	databasePath := createOpenCodeFixture(t)
	clock := &agentStatusTestClock{now: time.Unix(3, 0)}
	writerCalls := 0
	writer := tokenWriterFunc(func(ctx context.Context, batch AppendBatch) (AppendResult, error) {
		writerCalls++
		if writerCalls == 1 {
			return AppendResult{}, errors.New("synthetic append failure")
		}
		return countingTokenWriter{}.AppendBatch(ctx, batch)
	})
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", TmuxSession: "opencode", OpenCodeSession: "ses-1"}}, nil
		},
		CursorPath: filepath.Join(root, "opencode-cursor.json"), EpochStartedAt: 1, OwnerGeneration: 1,
	})

	collector.RunOnce(context.Background())
	database := collector.openCodeReadMetricsDatabaseForTest()
	if database == nil {
		t.Fatal("OpenCode read database was not retained after synthetic append failure")
	}
	version, err := database.ReadSourceDataVersion(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !database.SourceScanRequired(version, []string{"ses-1"}, false) {
		t.Fatal("failed stats append committed the OpenCode source scan version")
	}
	beforeRetry := database.ReadMetrics()

	collector.RunOnce(context.Background())
	afterRetry := collector.openCodeReadMetricsForTest()
	if afterRetry.Queries <= beforeRetry.Queries {
		t.Fatalf("OpenCode queries did not repeat after failed stats append: before=%d after=%d", beforeRetry.Queries, afterRetry.Queries)
	}
	if database.SourceScanRequired(version, []string{"ses-1"}, false) {
		t.Fatal("successful stats append did not commit the OpenCode source scan version")
	}
}

func TestAgentTokenCollectorSkipsUnchangedOpenCodePartScan(t *testing.T) {
	root := t.TempDir()
	databasePath := createOpenCodeFixture(t)
	clock := &agentStatusTestClock{now: time.Unix(3, 0)}
	collector := NewAgentTokenCollector(countingTokenWriter{}, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", TmuxSession: "opencode", OpenCodeSession: "ses-1"}}, nil
		},
		CursorPath: filepath.Join(root, "opencode-cursor.json"), EpochStartedAt: 1, OwnerGeneration: 1,
	})

	collector.RunOnce(context.Background())
	collector.RunOnce(context.Background())
	beforeIdle := collector.openCodeReadMetricsForTest()
	collector.RunOnce(context.Background())
	afterIdle := collector.openCodeReadMetricsForTest()
	idleQueries := afterIdle.Queries - beforeIdle.Queries

	mutateSQLite(t, databasePath, `UPDATE session SET time_updated = time_updated + 1 WHERE id = 'ses-1'`)
	clock.now = time.Unix(5, 0)
	collector.RunOnce(context.Background())
	afterUpdate := collector.openCodeReadMetricsForTest()
	updatedQueries := afterUpdate.Queries - afterIdle.Queries
	if updatedQueries <= idleQueries {
		t.Fatalf("OpenCode query counts idle=%d after external update=%d; changed source should run the bounded part scan", idleQueries, updatedQueries)
	}
}

func TestAgentTokenCollectorDoesNotRestartCompletedOpenCodeRawScanOnUnrelatedCommit(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode-unrelated-commit.db")
	writeOpenCodeManyRootHistory(t, databasePath, 1, MaxOpenCodePartReadLimit)
	clock := &agentStatusTestClock{now: time.Unix(10_000, 0)}
	cursorPath := filepath.Join(root, "cursor.json")
	collector := NewAgentTokenCollector(countingTokenWriter{}, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "root-00", TmuxSession: "synthetic"}}, nil
		},
		CursorPath: cursorPath, SkipTranscriptScan: true,
	})
	t.Cleanup(collector.Stop)
	collector.RunOnce(context.Background())
	clock.now = clock.now.Add(AgentTokenActiveCadence)
	collector.RunOnce(context.Background())
	completed, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if !completed.RawScan.HasScan || completed.RawScan.ScannedThroughRowID < completed.RawScan.HighWaterRowID {
		t.Fatalf("synthetic raw scan did not complete: %+v", completed.RawScan)
	}

	mutateSQLite(t, databasePath, `UPDATE session SET time_updated = time_updated + 1 WHERE id = 'root-00'`)
	clock.now = clock.now.Add(AgentTokenActiveCadence)
	collector.RunOnce(context.Background())
	afterCommit, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if afterCommit.RawScan.ScannedThroughRowID != completed.RawScan.ScannedThroughRowID || afterCommit.RawScan.HighWaterRowID != completed.RawScan.HighWaterRowID {
		t.Fatalf("unrelated session commit restarted completed global part scan: before=%+v after=%+v", completed.RawScan, afterCommit.RawScan)
	}

	mutateSQLite(t, databasePath, `INSERT INTO message VALUES ('message-late-history', 'root-00', 1, 1, '{"role":"assistant"}')`)
	mutateSQLite(t, databasePath, `INSERT INTO part VALUES ('part-late-history', 'message-late-history', 'root-00', 1, 1, '{"type":"step-finish","tokens":{"input":2,"output":3,"cache":{"read":4}}}')`)
	clock.now = clock.now.Add(AgentTokenActiveCadence)
	collector.RunOnce(context.Background())
	newPart, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if newPart.RawScan.HighWaterRowID <= afterCommit.RawScan.HighWaterRowID || newPart.RawScan.ScannedThroughRowID != newPart.RawScan.HighWaterRowID {
		t.Fatalf("new historical row was not reconciled from the saved frontier: before=%+v after=%+v", afterCommit.RawScan, newPart.RawScan)
	}
}

func TestAgentTokenCollectorCompletesOpenCodeRawScanAfterTrailingRowsAreDeleted(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode-deleted-tail.db")
	writeOpenCodeManyRootHistory(t, databasePath, 1, MaxOpenCodePartReadLimit)
	clock := &agentStatusTestClock{now: time.Unix(10_000, 0)}
	cursorPath := filepath.Join(root, "cursor.json")
	collector := NewAgentTokenCollector(countingTokenWriter{}, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "root-00", TmuxSession: "synthetic"}}, nil
		},
		CursorPath: cursorPath, SkipTranscriptScan: true,
	})
	t.Cleanup(collector.Stop)
	collector.RunOnce(context.Background())
	pending, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	if pending.RawScan.ScannedThroughRowID >= pending.RawScan.HighWaterRowID {
		t.Fatalf("synthetic raw scan did not leave a pending page: %+v", pending.RawScan)
	}

	mutateSQLite(t, databasePath, `DELETE FROM part WHERE id = 'part-00-fresh'`)
	for attempt := 0; attempt < 3; attempt++ {
		clock.now = clock.now.Add(AgentTokenActiveCadence)
		collector.RunOnce(context.Background())
		completed, stateErr := NewOpenCodeTokenCursor(cursorPath).state()
		if stateErr != nil {
			t.Fatal(stateErr)
		}
		if completed.RawScan.ScannedThroughRowID == completed.RawScan.HighWaterRowID && !completed.RawScan.ScanDirty && !completed.RawScan.ForceRescan {
			return
		}
	}
	final, err := NewOpenCodeTokenCursor(cursorPath).state()
	if err != nil {
		t.Fatal(err)
	}
	t.Fatalf("deleted trailing row left the raw scan cursor pending: %+v", final.RawScan)
}

func TestAgentTokenCollectorReconcilesOldOpenCodeRevisionOnBoundedSchedule(t *testing.T) {
	root := t.TempDir()
	databasePath := createOpenCodeFixture(t)
	mutateSQLite(t, databasePath, `UPDATE session SET time_created = 9000000, time_updated = 10000000 WHERE id = 'ses-1'`)
	clock := &agentStatusTestClock{now: time.Unix(10_000, 0)}
	collector := NewAgentTokenCollector(countingTokenWriter{}, clock, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "synthetic"}}, nil
		},
		CursorPath: filepath.Join(root, "cursor.json"), SkipTranscriptScan: true,
	})
	t.Cleanup(collector.Stop)
	collector.RunOnce(context.Background())
	if got := collector.Counters().UsageAccepted; got != 3 {
		t.Fatalf("initial synthetic OpenCode usage = %d, want 3", got)
	}
	if got := collector.Counters().UnavailableAccepted; got != 0 {
		t.Fatalf("initial synthetic OpenCode unavailable count = %d, want 0", got)
	}

	mutateSQLite(t, databasePath, `UPDATE part SET data = '{"type":"step-finish","tokens":{"total":20,"input":9,"output":8,"cache":{"read":3}}}' WHERE id = 'part-1'`)
	clock.now = clock.now.Add(AgentTokenActiveCadence)
	collector.RunOnce(context.Background())
	if got := collector.Counters().UnavailableAccepted; got != 0 {
		t.Fatalf("old revision outside active window was reported before scheduled reconciliation: unavailable=%d", got)
	}

	clock.now = clock.now.Add(15 * time.Minute)
	collector.RunOnce(context.Background())
	if got := collector.Counters().UnavailableAccepted; got != 1 {
		t.Fatalf("scheduled synthetic history reconciliation unavailable count = %d, want one explicit revision warning", got)
	}
}

func (c *AgentTokenCollector) openCodeReadMetricsForTest() ReadMetrics {
	database := c.openCodeReadMetricsDatabaseForTest()
	if database == nil {
		return ReadMetrics{}
	}
	return database.ReadMetrics()
}

func (c *AgentTokenCollector) openCodeReadMetricsDatabaseForTest() *OpenCodeDatabase {
	c.openCodeDatabaseMu.Lock()
	defer c.openCodeDatabaseMu.Unlock()
	return c.openCodeDatabase
}

func TestCommitReceiptsSettlesEveryReceiptAfterAnError(t *testing.T) {
	committed := make([]int, 0, 3)
	receipts := []agentTokenReceipt{
		{commit: func() error { committed = append(committed, 1); return errors.New("first") }},
		{commit: func() error { committed = append(committed, 2); return errors.New("second") }},
		{commit: func() error { committed = append(committed, 3); return nil }},
	}
	if err := commitReceipts(receipts); err == nil {
		t.Fatal("receipt errors were lost")
	}
	if !reflect.DeepEqual(committed, []int{1, 2, 3}) {
		t.Fatalf("receipt settlement stopped early: %v", committed)
	}
}

func TestCommitReceiptsRollsBackAReceiptThatCannotCommit(t *testing.T) {
	commitErr := errors.New("cursor commit failed")
	rollbackErr := errors.New("cursor rollback failed")
	rolledBack := false
	err := commitReceipts([]agentTokenReceipt{{
		commit: func() error { return commitErr },
		rollback: func() error {
			rolledBack = true
			return rollbackErr
		},
	}})
	if !rolledBack || !errors.Is(err, commitErr) || !errors.Is(err, rollbackErr) {
		t.Fatalf("failed receipt settlement = rolledBack=%v err=%v", rolledBack, err)
	}
}

func TestAgentTokenCollectorCancellationAfterPrepareRollsBackAndRetries(t *testing.T) {
	root := t.TempDir()
	transcript := filepath.Join(root, "claude", "session.jsonl")
	writeJSONL(t, transcript, usageClaude(1, "cancel-retry", 7, 3))
	source := TranscriptSource{Path: transcript, Provider: "claude", SourceID: "synthetic-claude", ThreadID: "cancel-retry"}
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, nil, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{source}, nil
		},
		TranscriptCursorPath: cursorPath,
	})

	batch, receipts, err := collector.collectTranscripts(context.Background(), 10, 1, nil, nil, AppendBatch{}, nil)
	if err != nil || len(batch.UsageAtoms) != 2 || len(receipts) != 1 {
		t.Fatalf("prepared synthetic transcript batch = atoms=%d receipts=%d err=%v", len(batch.UsageAtoms), len(receipts), err)
	}
	assertDurableUsageAtoms := func(want int) {
		t.Helper()
		database, err := OpenReadOnly(context.Background(), statsPath)
		if err != nil {
			t.Fatal(err)
		}
		defer database.Close()
		atoms, err := database.ReadUsageAtoms(context.Background(), 0, 100, MaxFactReadLimit)
		if err != nil || len(atoms) != want {
			t.Fatalf("durable usage = %d atoms err=%v, want %d synthetic atoms", len(atoms), err, want)
		}
	}
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	appended, appendErr := collector.appendCollected(canceled, batch, receipts)
	if appended || !errors.Is(appendErr, context.Canceled) {
		t.Fatalf("canceled transcript append = appended=%v err=%v", appended, appendErr)
	}
	assertDurableUsageAtoms(0)
	if counters := collector.Counters(); counters.UsageAccepted != 0 || counters.UsageDuplicates != 0 {
		t.Fatalf("canceled append changed usage counters: %+v", counters)
	}
	cursorState, err := collector.transcriptCursor.State(source)
	if err != nil || cursorState.Offset != 0 {
		t.Fatalf("cursor after canceled append = %+v err=%v, want unpromoted offset 0", cursorState, err)
	}
	schedule, err := collector.transcriptCursor.Schedule()
	if err != nil || schedule.NextHistoricalSource != "" || schedule.LiveTranscriptPriorityRuns != 0 {
		t.Fatalf("schedule after canceled append = %+v err=%v, want no promoted fairness turn", schedule, err)
	}

	retryBatch, retryReceipts, err := collector.collectTranscripts(context.Background(), 11, 1, nil, nil, AppendBatch{}, nil)
	if err != nil || len(retryBatch.UsageAtoms) != 2 || len(retryReceipts) != 1 {
		t.Fatalf("retry after canceled append = atoms=%d receipts=%d err=%v", len(retryBatch.UsageAtoms), len(retryReceipts), err)
	}
	appended, appendErr = collector.appendCollected(context.Background(), retryBatch, retryReceipts)
	if !appended || appendErr != nil {
		t.Fatalf("retry transcript append = appended=%v err=%v", appended, appendErr)
	}
	if counters := collector.Counters(); counters.UsageAccepted != 2 || counters.UsageDuplicates != 0 {
		t.Fatalf("retry append counters = %+v, want two accepted and no duplicates", counters)
	}
	cursorState, err = collector.transcriptCursor.State(source)
	if err != nil || cursorState.Offset != transcriptFixtureSize(t, transcript) {
		t.Fatalf("cursor after successful retry = %+v err=%v", cursorState, err)
	}
	assertDurableUsageAtoms(2)
}

func TestAgentTokenCollectorReloadsCursorAfterAmbiguousPostRenameFailure(t *testing.T) {
	root := t.TempDir()
	transcript := filepath.Join(root, "history.jsonl")
	writeJSONL(t, transcript, usageClaude(1, "event-1", 3, 2))
	source := TranscriptSource{Path: transcript, Provider: "claude", SourceID: "history", ThreadID: "history"}
	config := AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{source}, nil
		},
		TranscriptCursorPath: filepath.Join(root, "transcript-cursor.json"),
	}
	collector := NewAgentTokenCollector(nil, nil, config)
	cursor := NewTranscriptCursorStore(config.TranscriptCursorPath)
	injectPostRenameTranscriptCursorWriteFailure(t, cursor, transcriptCursorKey(source))
	collector.transcriptCursor = cursor

	batch, receipts, err := collector.collectTranscripts(context.Background(), 10, 1, nil, nil, AppendBatch{}, nil)
	if err != nil || len(batch.UsageAtoms) == 0 || len(receipts) != 1 {
		t.Fatalf("initial synthetic transcript scan = atoms=%d receipts=%d unavailable=%+v err=%v", len(batch.UsageAtoms), len(receipts), batch.UnavailableSpans, err)
	}
	if err := commitReceipts(receipts); err == nil {
		t.Fatal("post-rename commit failure was not returned")
	}

	batch, receipts, err = collector.collectTranscripts(context.Background(), 11, 1, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatalf("same-collector retry after post-rename failure: %v", err)
	}
	if len(batch.UsageAtoms) != 0 {
		t.Fatalf("same-collector retry replayed committed transcript atoms: %+v", batch.UsageAtoms)
	}
	for _, span := range batch.UnavailableSpans {
		if strings.Contains(span.Reason, "stale") {
			t.Fatalf("same-collector retry retained a stale cursor: %+v", span)
		}
	}
	if err := commitReceipts(receipts); err != nil {
		t.Fatalf("same-collector retry receipt: %v", err)
	}
	state, err := cursor.State(source)
	if err != nil {
		t.Fatal(err)
	}
	if want := transcriptFixtureSize(t, transcript); state.Offset != want {
		t.Fatalf("reconciled cursor offset = %d, want %d", state.Offset, want)
	}
}

func injectPostRenameTranscriptCursorWriteFailure(t *testing.T, cursor *TranscriptCursorStore, nextHistoricalSource string) {
	t.Helper()
	writeSidecar := cursor.sidecarWriter
	failedAfterRename := false
	cursor.sidecarWriter = func(path string, value any) error {
		disk, ok := value.(transcriptCursorDisk)
		if !ok {
			t.Fatalf("cursor sidecar value has type %T", value)
		}
		injectFailure := !failedAfterRename && len(disk.States) > 0 && len(disk.Pending) == 0 && disk.NextHistoricalSource == nextHistoricalSource && disk.PendingNextSourceToken == "" && disk.PendingNextHistoricalSourceToken == ""
		if err := writeSidecar(path, value); err != nil {
			return err
		}
		if injectFailure {
			failedAfterRename = true
			return errors.New("synthetic sidecar error after atomic rename")
		}
		return nil
	}
}

func TestOpenCodeCursorReplaysAfterStatsCommitCrash(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeFixture(t)
	cursorPath := filepath.Join(root, "opencode-cursors.json")
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	config := AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode"}}, nil
		},
		OpenCodeDatabase:   databasePath,
		CursorPath:         cursorPath,
		SkipTranscriptScan: true,
		EpochStartedAt:     1788307200,
		OwnerGeneration:    1,
	}
	collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
	first, receipts, err := collector.collect(context.Background(), 1788307300, 10)
	if err != nil || len(first.UsageAtoms) == 0 || len(receipts) != 1 {
		t.Fatalf("initial OpenCode collection = atoms=%d receipts=%d err=%v", len(first.UsageAtoms), len(receipts), err)
	}
	if _, err := writer.AppendBatch(context.Background(), first); err != nil {
		t.Fatal(err)
	}
	// Simulate SIGKILL after the stats transaction committed but before the
	// cursor receipt committed. The restarted collector must discard pending
	// state and replay deterministic IDs into the writer's dedupe boundary.
	restarted := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
	replayed, replayReceipts, err := restarted.collect(context.Background(), 1788307300, 10)
	if err != nil || len(replayed.UsageAtoms) != len(first.UsageAtoms) || len(replayReceipts) != 1 {
		t.Fatalf("replayed OpenCode collection = atoms=%d receipts=%d err=%v", len(replayed.UsageAtoms), len(replayReceipts), err)
	}
	result, err := writer.AppendBatch(context.Background(), replayed)
	if err != nil {
		t.Fatal(err)
	}
	if result.UsageAccepted != 0 || result.UsageDuplicate != len(replayed.UsageAtoms) {
		t.Fatalf("replay changed durable usage: result=%+v atoms=%d", result, len(replayed.UsageAtoms))
	}
	for _, receipt := range replayReceipts {
		if err := receipt.commit(); err != nil {
			t.Fatal(err)
		}
	}

	mutateSQLite(t, databasePath, `INSERT INTO message VALUES ('msg-cursor-replay', 'ses-1', 1788307310000, 1788307310000, '{"role":"assistant"}')`)
	mutateSQLite(t, databasePath, `INSERT INTO part VALUES ('part-cursor-replay', 'msg-cursor-replay', 'ses-1', 1788307310000, 1788307310000, '{"type":"step-finish","tokens":{"total":4,"input":2,"output":2,"cache":{"read":0}}}')`)
	next, nextReceipts, err := restarted.collect(context.Background(), 1788307310, 10)
	if err != nil || len(next.UsageAtoms) != 2 || len(nextReceipts) != 1 {
		t.Fatalf("post-replay OpenCode collection = atoms=%d receipts=%d err=%v", len(next.UsageAtoms), len(nextReceipts), err)
	}
	result, err = writer.AppendBatch(context.Background(), next)
	if err != nil {
		t.Fatal(err)
	}
	if result.UsageAccepted != len(next.UsageAtoms) {
		t.Fatalf("new OpenCode part was not accepted after replay: result=%+v", result)
	}
	for _, receipt := range nextReceipts {
		if err := receipt.commit(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestOpenCodeCursorReplaysAfterCrashBeforeStatsAppend(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeFixture(t)
	cursorPath := filepath.Join(root, "opencode-cursors.json")
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	config := AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode"}}, nil
		},
		OpenCodeDatabase:   databasePath,
		CursorPath:         cursorPath,
		SkipTranscriptScan: true,
		EpochStartedAt:     1788307200,
		OwnerGeneration:    1,
	}
	collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
	first, _, err := collector.collect(context.Background(), 1788307300, 10)
	if err != nil || len(first.UsageAtoms) == 0 {
		t.Fatalf("initial OpenCode collection = atoms=%d err=%v", len(first.UsageAtoms), err)
	}
	// The prepared cursor is durable, but no stats transaction reached SQLite.
	// Restart must discard that prepare so the source rows are replayed.
	restarted := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
	replayed, receipts, err := restarted.collect(context.Background(), 1788307300, 10)
	if err != nil || len(replayed.UsageAtoms) != len(first.UsageAtoms) || len(receipts) != 1 {
		t.Fatalf("replayed OpenCode collection = atoms=%d receipts=%d err=%v", len(replayed.UsageAtoms), len(receipts), err)
	}
	result, err := writer.AppendBatch(context.Background(), replayed)
	if err != nil {
		t.Fatal(err)
	}
	if result.UsageAccepted != len(replayed.UsageAtoms) || result.UsageDuplicate != 0 {
		t.Fatalf("crash-before-append replay = result=%+v atoms=%d", result, len(replayed.UsageAtoms))
	}
	for _, receipt := range receipts {
		if err := receipt.commit(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestOpenCodeCursorProcessCrashMatrixReplaysExactlyOnce(t *testing.T) {
	const expectedUsageAtoms = 3
	for _, stage := range []string{"before_source_read", "after_source_read", "after_cursor_prepare", "after_stats_append"} {
		t.Run(stage, func(t *testing.T) {
			root := t.TempDir()
			databasePath := copyOpenCodeFixture(t)
			statsPath := filepath.Join(root, "stats.sqlite3")
			cursorPath := filepath.Join(root, "opencode-cursors.json")
			command := exec.Command(os.Args[0], "-test.run=^TestOpenCodeCursorCrashHelper$")
			command.Env = testsupport.SyntheticEnvironment(
				"YOLOMUX_OPENCODE_CRASH_HELPER=1",
				"YOLOMUX_OPENCODE_CRASH_STAGE="+stage,
				"YOLOMUX_OPENCODE_CRASH_DATABASE="+databasePath,
				"YOLOMUX_OPENCODE_CRASH_STATS="+statsPath,
				"YOLOMUX_OPENCODE_CRASH_CURSOR="+cursorPath,
			)
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("synthetic OpenCode crash helper failed at %s: %v\n%s", stage, err, output)
			}
			if !strings.Contains(string(output), "synthetic OpenCode process exited at "+stage) {
				t.Fatalf("crash helper did not reach %s: %s", stage, output)
			}
			if stage == "before_source_read" || stage == "after_source_read" {
				for _, path := range []string{statsPath, cursorPath} {
					if _, err := os.Stat(path); !os.IsNotExist(err) {
						t.Fatalf("OpenCode crash stage %s touched persistence path %s: %v", stage, path, err)
					}
				}
			}
			if stage == "after_cursor_prepare" || stage == "after_stats_append" {
				data, err := os.ReadFile(cursorPath)
				if err != nil {
					t.Fatalf("read prepared OpenCode cursor after %s: %v", stage, err)
				}
				var disk openCodeCursorDisk
				if err := json.Unmarshal(data, &disk); err != nil {
					t.Fatalf("decode prepared OpenCode cursor after %s: %v", stage, err)
				}
				pendingParts := 0
				if disk.Pending != nil {
					pendingParts = len(disk.Pending.Parts)
				}
				if pendingParts == 0 || disk.PendingToken == "" {
					t.Fatalf("OpenCode cursor after %s lacks a durable pending prepare: pending_parts=%d token=%t", stage, pendingParts, disk.PendingToken != "")
				}
			}

			writer, err := OpenWriter(context.Background(), statsPath)
			if err != nil {
				t.Fatalf("open replacement stats writer after %s: %v", stage, err)
			}
			config := AgentTokenCollectorConfig{
				Inventory: func(context.Context) ([]AgentRecord, []error) {
					return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "synthetic-opencode"}}, nil
				},
				OpenCodeDatabase: databasePath, CursorPath: cursorPath, SkipTranscriptScan: true,
				EpochStartedAt: 1788307200, OwnerGeneration: 1,
			}
			collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
			batch, receipts, err := collector.collect(context.Background(), 1788307300, 10)
			if err != nil || len(batch.UsageAtoms) != expectedUsageAtoms || len(receipts) != 1 {
				_ = writer.Close()
				t.Fatalf("OpenCode replay after %s = atoms=%d receipts=%d err=%v", stage, len(batch.UsageAtoms), len(receipts), err)
			}
			result, err := writer.AppendBatch(context.Background(), batch)
			if err != nil {
				_ = writer.Close()
				t.Fatalf("append OpenCode replay after %s: %v", stage, err)
			}
			wantAccepted, wantDuplicate := expectedUsageAtoms, 0
			if stage == "after_stats_append" {
				wantAccepted, wantDuplicate = 0, expectedUsageAtoms
			}
			if result.UsageAccepted != wantAccepted || result.UsageDuplicate != wantDuplicate {
				_ = writer.Close()
				t.Fatalf("OpenCode replay after %s = %+v, want accepted=%d duplicate=%d", stage, result, wantAccepted, wantDuplicate)
			}
			for _, receipt := range receipts {
				if err := receipt.commit(); err != nil {
					_ = writer.Close()
					t.Fatalf("commit OpenCode replay cursor after %s: %v", stage, err)
				}
			}
			if err := writer.Close(); err != nil {
				t.Fatalf("close replacement writer after %s: %v", stage, err)
			}
			reader, err := OpenReadOnly(context.Background(), statsPath)
			if err != nil {
				t.Fatalf("read recovered stats DB after %s: %v", stage, err)
			}
			atoms, err := reader.ReadUsageAtoms(context.Background(), 0, 2e9, MaxFactReadLimit)
			_ = reader.Close()
			if err != nil || len(atoms) != expectedUsageAtoms {
				t.Fatalf("durable OpenCode atoms after %s = %d err=%v, want exactly %d", stage, len(atoms), err, expectedUsageAtoms)
			}
		})
	}
}

func TestOpenCodeCursorCrashHelper(t *testing.T) {
	if os.Getenv("YOLOMUX_OPENCODE_CRASH_HELPER") != "1" {
		return
	}
	ctx := context.Background()
	stage := os.Getenv("YOLOMUX_OPENCODE_CRASH_STAGE")
	databasePath := os.Getenv("YOLOMUX_OPENCODE_CRASH_DATABASE")
	statsPath := os.Getenv("YOLOMUX_OPENCODE_CRASH_STATS")
	cursorPath := os.Getenv("YOLOMUX_OPENCODE_CRASH_CURSOR")
	if stage == "before_source_read" {
		fmt.Fprintln(os.Stdout, "synthetic OpenCode process exited at "+stage)
		os.Exit(0)
	}
	if stage == "after_source_read" {
		config := AgentTokenCollectorConfig{
			Inventory: func(context.Context) ([]AgentRecord, []error) {
				return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "synthetic-opencode"}}, nil
			},
			OpenCodeDatabase: databasePath, CursorPath: cursorPath, SkipTranscriptScan: true,
			EpochStartedAt: 1788307200, OwnerGeneration: 1,
		}
		collector := NewAgentTokenCollector(nil, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
		collector.openCodeAfterSourceRead = func(window OpenCodeStepFinishWindow, readErr error) {
			if readErr != nil || len(window.Parts) != 1 || len(window.RootErrors) != 0 {
				fmt.Fprintf(os.Stderr, "source-read crash boundary saw parts=%d root_errors=%d err=%v; want one clean synthetic part\n", len(window.Parts), len(window.RootErrors), readErr)
				os.Exit(5)
			}
			fmt.Fprintln(os.Stdout, "synthetic OpenCode process exited at "+stage)
			os.Exit(0)
		}
		if _, _, err := collector.collect(ctx, 1788307300, 10); err != nil {
			fmt.Fprintln(os.Stderr, "collect synthetic OpenCode source:", err)
			os.Exit(6)
		}
		fmt.Fprintln(os.Stderr, "OpenCode collector did not reach the source-read crash boundary")
		os.Exit(7)
	}
	writer, err := OpenWriter(ctx, statsPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "open synthetic stats writer:", err)
		os.Exit(6)
	}
	config := AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "synthetic-opencode"}}, nil
		},
		OpenCodeDatabase: databasePath, CursorPath: cursorPath, SkipTranscriptScan: true,
		EpochStartedAt: 1788307200, OwnerGeneration: 1,
	}
	collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
	batch, receipts, err := collector.collect(ctx, 1788307300, 10)
	if err != nil || len(batch.UsageAtoms) != 3 || len(receipts) != 1 {
		fmt.Fprintf(os.Stderr, "collect synthetic OpenCode atoms=%d receipts=%d err=%v\n", len(batch.UsageAtoms), len(receipts), err)
		os.Exit(7)
	}
	if stage == "after_cursor_prepare" {
		fmt.Fprintln(os.Stdout, "synthetic OpenCode process exited at "+stage)
		os.Exit(0)
	}
	if stage == "after_stats_append" {
		if _, err := writer.AppendBatch(ctx, batch); err != nil {
			fmt.Fprintln(os.Stderr, "append synthetic OpenCode stats:", err)
			os.Exit(8)
		}
		fmt.Fprintln(os.Stdout, "synthetic OpenCode process exited at "+stage)
		os.Exit(0)
	}
	fmt.Fprintln(os.Stderr, "unknown synthetic OpenCode crash stage:", stage)
	os.Exit(9)
}

func TestAgentTokenCollectorPropagatesReceiptErrors(t *testing.T) {
	commitErr := errors.New("cursor commit failed")
	collector := &AgentTokenCollector{writer: countingTokenWriter{}}
	ok, err := collector.appendCollected(context.Background(), AppendBatch{}, []agentTokenReceipt{{
		commit: func() error { return commitErr },
	}})
	if ok || !errors.Is(err, commitErr) {
		t.Fatalf("commit error = ok=%v err=%v", ok, err)
	}

	appendErr := errors.New("stats append failed")
	rollbackErr := errors.New("cursor rollback failed")
	rolledBack := false
	collector.writer = tokenWriterFunc(func(context.Context, AppendBatch) (AppendResult, error) {
		return AppendResult{}, appendErr
	})
	ok, err = collector.appendCollected(context.Background(), AppendBatch{}, []agentTokenReceipt{{
		rollback: func() error {
			rolledBack = true
			return rollbackErr
		},
	}})
	if ok || !rolledBack || !errors.Is(err, appendErr) || !errors.Is(err, rollbackErr) {
		t.Fatalf("append/rollback errors = ok=%v rolledBack=%v err=%v", ok, rolledBack, err)
	}
}

func TestAgentTokenCollectorRollsBackOpenCodeReceiptAfterCommitFailure(t *testing.T) {
	commitErr := errors.New("cursor commit failed")
	rolledBack := false
	collector := &AgentTokenCollector{writer: countingTokenWriter{}}
	ok, metadataError, err := collector.appendOpenCodeCollected(context.Background(), AppendBatch{
		UsageAtoms: []UsageAtom{{EventID: "opencode-usage", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 10, Payload: map[string]any{"quantity": 1.0}}},
	}, &agentTokenReceipt{
		commit: func() error { return commitErr },
		rollback: func() error {
			rolledBack = true
			return nil
		},
	})
	if ok || metadataError || !rolledBack || !errors.Is(err, commitErr) {
		t.Fatalf("OpenCode commit failure = ok=%v metadataError=%v rolledBack=%v err=%v", ok, metadataError, rolledBack, err)
	}
}

func TestAgentTokenCollectorPreservesOpenCodeUsageWhenMetadataConflicts(t *testing.T) {
	usage := UsageAtom{
		EventID:    "opencode-usage",
		Direction:  "input",
		Modality:   "text",
		CacheRole:  "none",
		Unit:       "tokens",
		ObservedAt: 10,
		Payload:    map[string]any{"quantity": 4.0, "provider": "openai", "model": "synthetic"},
	}
	metadata := UnavailableSpan{
		Family:               "agent_tokens",
		SourceID:             "opencode:root",
		EpochID:              "epoch",
		StartedAt:            10,
		EndedAt:              20,
		NativeCadenceSeconds: 10,
		Reason:               "opencode-session-parts-truncated",
		OwnerGeneration:      1,
	}
	calls := 0
	committed := 0
	rolledBack := 0
	collector := &AgentTokenCollector{writer: tokenWriterFunc(func(_ context.Context, batch AppendBatch) (AppendResult, error) {
		calls++
		switch calls {
		case 1:
			if len(batch.UsageAtoms) != 1 || len(batch.UnavailableSpans) != 0 {
				t.Fatalf("usage append batch = %+v", batch)
			}
			return AppendResult{UsageAccepted: 1}, nil
		case 2:
			if len(batch.UsageAtoms) != 0 || len(batch.UnavailableSpans) != 1 {
				t.Fatalf("metadata append batch = %+v", batch)
			}
			return AppendResult{}, errors.New("unavailable spans overlap")
		default:
			t.Fatalf("unexpected append call %d: %+v", calls, batch)
			return AppendResult{}, nil
		}
	})}
	ok, metadataError, err := collector.appendOpenCodeCollected(context.Background(), AppendBatch{
		UsageAtoms:       []UsageAtom{usage},
		UnavailableSpans: []UnavailableSpan{metadata},
	}, &agentTokenReceipt{
		commit:   func() error { committed++; return nil },
		rollback: func() error { rolledBack++; return nil },
	})
	if err != nil || !ok || metadataError || calls != 2 || committed != 1 || rolledBack != 0 {
		t.Fatalf("metadata conflict after usage = ok=%v metadataError=%v err=%v calls=%d committed=%d rolledBack=%d", ok, metadataError, err, calls, committed, rolledBack)
	}
	if got := collector.Counters().UsageAccepted; got != 1 {
		t.Fatalf("usage counter after metadata conflict = %d, want 1", got)
	}
}

func TestIsUnavailableIntervalConflictRecognizesBothWriterDirections(t *testing.T) {
	for _, message := range []string{
		"unavailable spans overlap",
		"coverage epoch overlaps an unavailable span",
		"unavailable span overlaps a coverage epoch",
	} {
		if !isUnavailableIntervalConflict(errors.New(message)) {
			t.Fatalf("metadata conflict %q was not recognized", message)
		}
	}
	if isUnavailableIntervalConflict(errors.New("coverage epoch identity conflicts with stored data")) {
		t.Fatal("non-overlap metadata conflict was incorrectly classified as idempotent")
	}
}

func TestAgentTokenCollectorKeepsUsageWhenCoverageConflictComesFromWriter(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "synthetic-open-code-conflict.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	end := 20.0
	if _, err := writer.AppendBatch(ctx, AppendBatch{CoverageEpochs: []CoverageEpoch{{
		Family: "agent_tokens", SourceID: "opencode:root", EpochID: "coverage", StartedAt: 10, EndedAt: &end,
		NativeCadenceSeconds: 10, OwnerGeneration: 1,
	}}}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	collector := &AgentTokenCollector{writer: writer}
	committed := 0
	ok, metadataError, err := collector.appendOpenCodeCollected(ctx, AppendBatch{
		UsageAtoms: []UsageAtom{{
			EventID: "opencode-conflict-usage", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 12,
			Payload: map[string]any{"quantity": 4.0, "provider": "openai", "model": "synthetic"},
		}},
		UnavailableSpans: []UnavailableSpan{{
			Family: "agent_tokens", SourceID: "opencode:root", EpochID: "unavailable", StartedAt: 12, EndedAt: 18,
			NativeCadenceSeconds: 10, Reason: "synthetic-partial-history", OwnerGeneration: 1,
		}},
	}, &agentTokenReceipt{commit: func() error { committed++; return nil }})
	if err != nil || !ok || metadataError || committed != 1 {
		_ = writer.Close()
		t.Fatalf("writer coverage conflict = ok=%v metadataError=%v err=%v committed=%d", ok, metadataError, err, committed)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	atoms, err := database.ReadUsageAtoms(ctx, 0, 20, 10)
	if err != nil || len(atoms) != 1 || atoms[0].EventID != "opencode-conflict-usage" {
		t.Fatalf("usage after writer coverage conflict = atoms=%#v err=%v", atoms, err)
	}
}

func TestAgentTokenCollectorCanRetryOpenCodeCursorAfterCommitFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode-cursor.json")
	cursor := NewOpenCodeTokenCursor(path)
	before := openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}}
	next := before.clone()
	next.Events["event-1"] = "part-1"
	if err := cursor.prepare(before, next); err != nil {
		t.Fatal(err)
	}
	wrong := next.clone()
	wrong.Events["event-1"] = "different-part"
	collector := &AgentTokenCollector{writer: countingTokenWriter{}}
	ok, metadataError, err := collector.appendOpenCodeCollected(context.Background(), AppendBatch{}, &agentTokenReceipt{
		commit:   func() error { return cursor.commit(wrong) },
		rollback: cursor.rollback,
	})
	if ok || metadataError || err == nil || !strings.Contains(err.Error(), "cursor-state-stale") {
		t.Fatalf("stale OpenCode commit = ok=%v metadataError=%v err=%v", ok, metadataError, err)
	}
	if _, err := cursor.state(); err != nil {
		t.Fatalf("cursor remained pending after failed commit rollback: %v", err)
	}
	if err := cursor.prepare(before, next); err != nil {
		t.Fatalf("same collector could not prepare retry: %v", err)
	}
	if err := cursor.commit(next); err != nil {
		t.Fatalf("same collector retry commit failed: %v", err)
	}
}

func TestAgentTokenCollectorCanRecoverWhenOpenCodeRollbackTemporarilyFails(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "opencode-cursor.json")
	cursor := NewOpenCodeTokenCursor(path)
	before := openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}}
	next := before.clone()
	next.Events["event-1"] = "part-1"
	if err := cursor.prepare(before, next); err != nil {
		t.Fatal(err)
	}
	backup := filepath.Join(root, "pending.json")
	if err := os.Rename(path, backup); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(path, 0o700); err != nil {
		t.Fatal(err)
	}
	collector := &AgentTokenCollector{writer: countingTokenWriter{}}
	ok, metadataError, err := collector.appendOpenCodeCollected(context.Background(), AppendBatch{}, &agentTokenReceipt{
		commit:   func() error { return cursor.commit(next) },
		rollback: cursor.rollback,
	})
	if ok || metadataError || err == nil {
		t.Fatalf("temporarily unavailable OpenCode sidecar = ok=%v metadataError=%v err=%v", ok, metadataError, err)
	}
	if cursor.pending != nil || cursor.pendingToken != "" {
		t.Fatalf("failed rollback left in-memory cursor pending: pending=%v token=%q", cursor.pending, cursor.pendingToken)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(backup, path); err != nil {
		t.Fatal(err)
	}
	if _, err := cursor.state(); err != nil {
		t.Fatalf("cursor did not recover after sidecar repair: %v", err)
	}
	if err := cursor.prepare(before, next); err != nil {
		t.Fatalf("cursor could not prepare after sidecar repair: %v", err)
	}
	if err := cursor.commit(next); err != nil {
		t.Fatalf("cursor could not commit after sidecar repair: %v", err)
	}
}

func TestAgentTokenCollectorPreservesUsageWhenUnavailableSpanIsAlreadyRecorded(t *testing.T) {
	usage := UsageAtom{EventID: "usage-1", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 10, Payload: map[string]any{"quantity": 3.0, "provider": "codex", "model": "model"}}
	span := UnavailableSpan{Family: "agent_tokens", SourceID: "source", EpochID: "epoch", StartedAt: 10, EndedAt: 20, NativeCadenceSeconds: 10, Reason: "inventory-unavailable", OwnerGeneration: 1}
	commits := 0
	calls := 0
	collector := &AgentTokenCollector{}
	collector.writer = tokenWriterFunc(func(_ context.Context, batch AppendBatch) (AppendResult, error) {
		calls++
		if calls == 1 {
			return AppendResult{}, errors.New("unavailable spans overlap")
		}
		if len(batch.UnavailableSpans) != 0 || len(batch.UsageAtoms) != 1 {
			t.Fatalf("fallback batch = %+v", batch)
		}
		return AppendResult{UsageAccepted: 1}, nil
	})
	ok, err := collector.appendCollected(context.Background(), AppendBatch{UsageAtoms: []UsageAtom{usage}, UnavailableSpans: []UnavailableSpan{span}}, []agentTokenReceipt{{commit: func() error { commits++; return nil }}})
	if err != nil || !ok || calls != 2 || commits != 1 || collector.Counters().UsageAccepted != 1 {
		t.Fatalf("overlap fallback = ok=%v err=%v calls=%d commits=%d counters=%+v", ok, err, calls, commits, collector.Counters())
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
		if atom.Payload["model_evidence"] == nil || atom.Payload["telemetry_complete"] != false {
			t.Fatalf("OpenCode evidence/completeness = %#v", atom.Payload)
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

func TestAgentTokenCollectorPrefersPaneAnchoredClaimForDuplicateOpenCodeRoot(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode.db")
	createOpenCodeFixtureWithTitle(t, databasePath, "ses-live", "/repo/live", "build", 1788307299000)
	collector := NewAgentTokenCollector(nil, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "cursor.json"),
		EpochID:          "epoch",
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})
	records := []AgentRecord{
		{PID: 100, StartedAt: 1788307290, Kind: "opencode", OpenCodeSession: "ses-live", StartedIdentity: "old"},
		{PID: 200, StartedAt: 1788307295, Kind: "opencode", OpenCodeSession: "ses-live", StartedIdentity: "current", TmuxSession: "live-opencode", TmuxPaneID: "%0"},
	}
	batch, _, err := collector.collectOpenCode(context.Background(), 1788307300, 10, records)
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.UsageAtoms) != 3 || len(batch.UnavailableSpans) != 0 {
		t.Fatalf("duplicate-root collection = atoms=%#v unavailable=%#v", batch.UsageAtoms, batch.UnavailableSpans)
	}
	for _, atom := range batch.UsageAtoms {
		if atom.Payload["agent_id"] != "live-opencode" {
			t.Fatalf("duplicate-root attribution = %#v", atom.Payload)
		}
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

func TestAgentTokenCollectorIgnoresStaleDeterministicOpenCodeMockRoot(t *testing.T) {
	root := t.TempDir()
	databasePath := copyOpenCodeFixture(t)
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	collector := NewAgentTokenCollector(nil, clock, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{
				{Kind: "opencode", Command: "opencode --mock --session ses-live", OpenCodeSession: "ses-live", TmuxSession: "stale-fixture"},
				{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "valid-root"},
			}, nil
		},
		OpenCodeDatabase: databasePath,
		CursorPath:       filepath.Join(root, "cursor.json"),
		EpochStartedAt:   1788307200,
		OwnerGeneration:  1,
	})

	batch, receipts, err := collector.collect(context.Background(), 1788307300, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.UsageAtoms) != 3 || len(batch.UnavailableSpans) != 0 {
		t.Fatalf("stale mock root polluted OpenCode batch: atoms=%d unavailable=%#v", len(batch.UsageAtoms), batch.UnavailableSpans)
	}
	for _, receipt := range receipts {
		if err := receipt.rollback(); err != nil {
			t.Fatal(err)
		}
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
	// Keep 64 independent roots and a substantial historical fixture, but leave
	// enough CPU headroom for the race target to run beside the other Bazel
	// actions without turning a source-deadline assertion into a host-load test.
	writeOpenCodeManyRootHistory(t, databasePath, 64, 2000)
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
	counters := collector.Counters()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		for _, receipt := range receipts {
			_ = receipt.rollback()
		}
	})
	elapsed := time.Since(started)
	if elapsed >= 2*time.Second {
		t.Fatalf("fresh multi-root collection took %s: SQLite queries=%d duration=%s phases=%+v", elapsed, counters.OpenCodeSQLiteQueries, counters.OpenCodeSQLiteTotal, counters.OpenCodePhases)
	}
	freshAtoms := make(map[string]bool, 64*3)
	for _, atom := range batch.UsageAtoms {
		freshAtoms[atom.EventID] = true
	}
	for index := 0; index < 64; index++ {
		rootID := fmt.Sprintf("root-%02d", index)
		partID := fmt.Sprintf("part-%02d-fresh", index)
		for _, dimension := range []string{"input", "cache_read", "output"} {
			if eventID := openCodeUsageEventID(rootID, partID, dimension); !freshAtoms[eventID] {
				t.Fatalf("fresh root %s emitted no %s atom before the source deadline: atoms=%d unavailable=%#v", rootID, dimension, len(batch.UsageAtoms), batch.UnavailableSpans)
			}
		}
		foundIncomplete := false
		for _, span := range batch.UnavailableSpans {
			if span.SourceID == "opencode:"+rootID && span.Reason == "opencode-session-parts-truncated" {
				foundIncomplete = true
				break
			}
		}
		if !foundIncomplete {
			t.Fatalf("root %s lost its incomplete-history marker while raw scan is pending: unavailable=%#v", rootID, batch.UnavailableSpans)
		}
	}
	if len(batch.CoverageEpochs) != 0 {
		t.Fatalf("pending historical scans reported complete coverage: %#v", batch.CoverageEpochs)
	}
	t.Logf("fresh multi-root collection took %s: SQLite queries=%d duration=%s phases=%+v", elapsed, counters.OpenCodeSQLiteQueries, counters.OpenCodeSQLiteTotal, counters.OpenCodePhases)
}

func TestAgentTokenCollectorRestartsPendingOpenCodeRawScanWhenSelectedRootsChange(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode-selection-change.db")
	writeOpenCodeManyRootHistory(t, databasePath, 2, MaxOpenCodePartReadLimit+10)
	selectedRoot := "root-01"
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		OpenCodeDatabase: databasePath,
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: selectedRoot, TmuxSession: "synthetic"}}, nil
		},
		CursorPath:                filepath.Join(root, "cursor.json"),
		SkipTranscriptScan:        true,
		OpenCodeCollectionTimeout: 20 * time.Second,
	})
	collect := func() AppendBatch {
		t.Helper()
		batch, receipts, err := collector.collect(context.Background(), 10000, 10)
		if err != nil {
			t.Fatal(err)
		}
		for _, receipt := range receipts {
			if err := receipt.commit(); err != nil {
				t.Fatalf("commit collection cursor: %v", err)
			}
		}
		return batch
	}

	firstBatch := collect()
	cursor := NewOpenCodeTokenCursor(filepath.Join(root, "cursor.json"))
	first, err := cursor.state()
	if err != nil {
		t.Fatal(err)
	}
	if !first.RawScan.HasScan || first.RawScan.ScannedThroughRowID >= first.RawScan.HighWaterRowID {
		t.Fatalf("initial selected root did not leave a pending raw scan: %+v; batch=%+v", first.RawScan, firstBatch)
	}
	firstSelection := first.RawScan.SelectionKey
	firstFrontier := first.RawScan.ScannedThroughRowID

	selectedRoot = "root-00"
	secondBatch := collect()
	second, err := cursor.state()
	if err != nil {
		t.Fatal(err)
	}
	wantSelection := openCodeSelectionKey([]OpenCodeStepFinishSelection{{SessionID: "root-00", RootSessionID: "root-00"}})
	if firstSelection == wantSelection || second.RawScan.SelectionKey != wantSelection {
		t.Fatalf("raw scan selection key did not move with inventory: first=%q second=%q want=%q", firstSelection, second.RawScan.SelectionKey, wantSelection)
	}
	if firstFrontier != MaxOpenCodePartReadLimit || second.RawScan.ScannedThroughRowID != MaxOpenCodePartReadLimit {
		t.Fatalf("selection change did not leave the new scan at its first page: old frontier=%d new scan=%+v", firstFrontier, second.RawScan)
	}
	wantEventIDs := map[string]bool{
		openCodeUsageEventID("root-00", "part-00-fresh", "input"): false,
		openCodeUsageEventID("root-00", "part-00-00000", "input"): false,
	}
	for _, atom := range secondBatch.UsageAtoms {
		if _, expected := wantEventIDs[atom.EventID]; expected {
			wantEventIDs[atom.EventID] = true
		}
	}
	for eventID, found := range wantEventIDs {
		if !found {
			t.Fatalf("newly selected root did not replay expected fresh and pre-frontier usage %q: batch=%+v", eventID, secondBatch)
		}
	}
	for _, epoch := range secondBatch.CoverageEpochs {
		if epoch.SourceID == "opencode:root-00" {
			t.Fatalf("newly selected root reported coverage while its history scan is pending: %+v", epoch)
		}
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
		CodexTranscriptRoot: codexRoot,
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{{Path: fresh, Provider: "codex", SourceID: "codex:fresh", ThreadID: "historical-codex"}}, nil
		},
		OpenCodeDatabase:                databasePath,
		CursorPath:                      filepath.Join(root, "cursor.json"),
		EpochStartedAt:                  1788307200,
		OwnerGeneration:                 1,
		TranscriptDiscoveryMaxDirs:      4,
		TranscriptDiscoveryMaxFiles:     4,
		TranscriptDiscoveryMaxBytes:     8 * 1024 * 1024,
		TranscriptDiscoveryTimeout:      50 * time.Millisecond,
		CodexForkRepairDiscoveryTimeout: time.Nanosecond,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	started := time.Now()
	collector.RunOnce(ctx)
	if elapsed := time.Since(started); elapsed >= 500*time.Millisecond {
		t.Fatalf("RunOnce exceeded deadline: %s counters=%+v", elapsed, collector.Counters())
	}
	if got := collector.Counters().UsageAccepted; got < 4 {
		t.Fatalf("OpenCode and active Codex atoms did not survive bounded fork-repair discovery: %d counters=%+v", got, collector.Counters())
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
	if len(atoms) < 4 {
		t.Fatalf("durable OpenCode and Codex atoms = %d, want at least 4", len(atoms))
	}
	foundCodex := false
	for _, atom := range atoms {
		if strings.HasPrefix(atom.EventID, "codex:") {
			foundCodex = true
			break
		}
	}
	if !foundCodex {
		t.Fatalf("bounded fork-repair discovery prevented active Codex usage persistence: %+v", atoms)
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
		Inventory: func(ctx context.Context) ([]AgentRecord, []error) {
			close(fullInventoryStarted)
			<-ctx.Done()
			return nil, []error{errors.Join(errors.New("synthetic full inventory failure"), ctx.Err())}
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

func TestDefaultTranscriptSourcesRequireExplicitSyntheticRoot(t *testing.T) {
	// A missing fixture root must never turn into a scan of the developer's
	// HOME/CODEX_HOME tree. Production passes roots explicitly; tests must do
	// the same or receive a bounded unavailable result.
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("CODEX_HOME", filepath.Join(home, ".codex"))
	sources, err := defaultAgentTokenSources(context.Background(), []AgentRecord{{Kind: "claude", TmuxSession: "fixture"}})
	if err == nil || err.Error() != "transcript-root-unconfigured" {
		t.Fatalf("default source resolution = sources=%#v err=%v, want explicit-root error", sources, err)
	}
	if len(sources) != 0 {
		t.Fatalf("unconfigured source resolution returned host-derived paths: %#v", sources)
	}
}

func TestNewestCodexTranscriptTargetUsesOnlyLiveRolloutsUnderRoot(t *testing.T) {
	root := t.TempDir()
	first := filepath.Join(root, "2026", "09", "17", "rollout-first.jsonl")
	second := filepath.Join(root, "2026", "09", "17", "rollout-second.jsonl")
	if err := os.MkdirAll(filepath.Dir(first), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(first, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(second, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(first, time.Unix(10, 0), time.Unix(10, 0)); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(second, time.Unix(20, 0), time.Unix(20, 0)); err != nil {
		t.Fatal(err)
	}
	got := newestCodexTranscriptTarget(root, []string{
		"/outside/rollout-other.jsonl",
		first,
		second,
		second + " (deleted)",
	})
	if got != second {
		t.Fatalf("newest Codex rollout = %q, want %q", got, second)
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
	if series["agent_tokens_per_minute:yo7221|claude"] != 30 || series["agent_tokens_per_minute:yo7222|codex"] != 30 {
		t.Fatalf("copied fixture token series = %#v", series)
	}
}

func TestAgentTokenCollectorRepairsOrphanedCodexForkOnce(t *testing.T) {
	root := t.TempDir()
	sessionsRoot := filepath.Join(root, ".codex", "sessions")
	active := filepath.Join(sessionsRoot, "2026", "09", "23", "rollout-active.jsonl")
	orphan := filepath.Join(sessionsRoot, "2026", "01", "01", "rollout-orphan.jsonl")
	writeJSONL(t, active, map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread"}})
	writeJSONL(t, orphan,
		map[string]any{"type": "session_meta", "payload": map[string]any{"thread_id": "orphan-child", "forked_from_id": "root-thread", "thread_source": "subagent"}},
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread", "model": "gpt-parent"}},
		usageCodex(2, 80, 30, 12),
		map[string]any{"type": "inter_agent_communication_metadata", "payload": map[string]any{}},
		usageCodex(3, 90, 35, 15),
	)
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	seededAtoms := []UsageAtom{
		{EventID: "codex:orphan-child:2", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 2, Payload: map[string]any{"quantity": 50.0, "provider": "openai", "model": "gpt-parent", "thread_id": "orphan-child", "execution_source": "codex"}},
		{EventID: "codex:orphan-child:2", Direction: "input", Modality: "text", CacheRole: "read", Unit: "tokens", ObservedAt: 2, Payload: map[string]any{"quantity": 30.0, "provider": "openai", "model": "gpt-parent", "thread_id": "orphan-child", "execution_source": "codex"}},
		{EventID: "codex:orphan-child:2", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 2, Payload: map[string]any{"quantity": 12.0, "provider": "openai", "model": "gpt-parent", "thread_id": "orphan-child", "execution_source": "codex"}},
	}
	if _, err := writer.AppendBatch(context.Background(), AppendBatch{UsageAtoms: seededAtoms}); err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{{Path: active, Provider: "codex", SourceID: "codex:active", ThreadID: "fixture"}}, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursors.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 100,
	})
	rows := []AgentRecord{{Kind: "codex", TmuxSession: "fixture"}}
	first, receipts, err := collector.collectTranscripts(context.Background(), 100, 10, rows, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(first.UsageTombstones) != 3 {
		t.Fatalf("orphan fork tombstones = %#v, want copied parent-history dimensions only", first.UsageTombstones)
	}
	if len(first.UsageAtoms) != 0 {
		t.Fatalf("repair scan imported child suffix atoms: %#v", first.UsageAtoms)
	}
	for _, tombstone := range first.UsageTombstones {
		if tombstone.ThreadID != "orphan-child" || tombstone.EventID != "codex:orphan-child:2" || tombstone.ObservedAt >= 3 {
			t.Fatalf("repair tombstone crossed into child suffix: %+v", tombstone)
		}
	}
	if err := rollbackReceipts(receipts); err != nil {
		t.Fatal(err)
	}
	if completed, err := collector.transcriptCursor.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 0 {
		t.Fatalf("rolled-back repair roots = %v err=%v", completed, err)
	}
	collector = NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, collector.config)
	replayed, receipts, err := collector.collectTranscripts(context.Background(), 100, 10, rows, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(replayed.UsageTombstones, first.UsageTombstones) {
		t.Fatalf("restart repair tombstones = %#v, want exact replay %#v", replayed.UsageTombstones, first.UsageTombstones)
	}
	if ok, appendErr := collector.appendCollected(context.Background(), replayed, receipts); !ok {
		t.Fatal(appendErr)
	}
	if completed, err := collector.transcriptCursor.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 1 {
		t.Fatalf("fork repair completion roots = %v err=%v", completed, err)
	}
	childState := TranscriptScannerState{}
	childSuffix, err := ScanTranscript(TranscriptSource{Path: orphan, Provider: "codex"}, &childState, TranscriptScanOptions{})
	if err != nil || len(childSuffix.Atoms) != 3 {
		t.Fatalf("normal scan after repair did not preserve child suffix: atoms=%#v err=%v", childSuffix.Atoms, err)
	}

	second, secondReceipts, err := collector.collectTranscripts(context.Background(), 110, 10, rows, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(second.UsageTombstones) != 0 {
		t.Fatalf("completed fork repair replayed tombstones: %#v", second.UsageTombstones)
	}
	if len(secondReceipts) > 0 {
		if ok, appendErr := collector.appendCollected(context.Background(), second, secondReceipts); !ok {
			t.Fatal(appendErr)
		}
	}
	var remainingAtoms int
	if err := writer.db.QueryRow("SELECT count(*) FROM usage_atoms").Scan(&remainingAtoms); err != nil {
		t.Fatal(err)
	}
	if remainingAtoms != 0 {
		t.Fatalf("copied parent-history atoms remain after repair: %d", remainingAtoms)
	}
}

func TestCodexForkRepairGetsASourceSlotWhenNormalSourcesFillTheCap(t *testing.T) {
	root := t.TempDir()
	sessionsRoot := filepath.Join(root, ".codex", "sessions")
	active := filepath.Join(sessionsRoot, "2026", "09", "23", "rollout-active.jsonl")
	orphan := filepath.Join(sessionsRoot, "2026", "01", "01", "rollout-orphan.jsonl")
	writeJSONL(t, active, map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread"}})
	writeJSONL(t, orphan,
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "orphan-child", "forked_from_id": "root-thread", "thread_source": "subagent"}},
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread"}},
		usageCodex(2, 80, 30, 12),
		map[string]any{"type": "inter_agent_communication_metadata", "payload": map[string]any{}},
	)
	ordinary := make([]TranscriptSource, 0, maxAgentTokenSources)
	if err := os.MkdirAll(filepath.Join(root, "claude"), 0o700); err != nil {
		t.Fatal(err)
	}
	for index := 0; index < maxAgentTokenSources-1; index++ {
		path := filepath.Join(root, "claude", fmt.Sprintf("%03d.jsonl", index))
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			t.Fatal(err)
		}
		ordinary = append(ordinary, TranscriptSource{Path: path, Provider: "claude", SourceID: fmt.Sprintf("claude:%03d", index)})
	}
	ordinary = append(ordinary, TranscriptSource{Path: active, Provider: "codex", SourceID: "codex:active", ThreadID: "root-thread"})
	collector := NewAgentTokenCollector(countingTokenWriter{}, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return ordinary, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursors.json"),
		TranscriptMaxBytesPerCycle:   4 * 1024 * 1024,
		TranscriptMaxRecordsPerCycle: 1 << 20,
	})
	batch, receipts, err := collector.collectTranscripts(context.Background(), 100, 10, []AgentRecord{{Kind: "codex", TmuxSession: "fixture"}}, nil, AppendBatch{}, nil)
	if err != nil || len(batch.UsageTombstones) != 3 {
		t.Fatalf("repair with full ordinary-source cap = tombstones=%#v err=%v", batch.UsageTombstones, err)
	}
	if ok, appendErr := collector.appendCollected(context.Background(), batch, receipts); !ok {
		t.Fatal(appendErr)
	}
	if completed, err := collector.transcriptCursor.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 1 {
		t.Fatalf("repair roots after full ordinary-source cap = %v err=%v", completed, err)
	}
}

func TestTranscriptCycleBudgetsMatchPythonCodexRepairThroughput(t *testing.T) {
	normalBytes, normalRecords := transcriptCycleBudgets(defaultTranscriptCycleMaxBytes, defaultTranscriptCycleMaxRecords, false)
	if normalBytes != defaultTranscriptCycleMaxBytes || normalRecords != defaultTranscriptCycleMaxRecords {
		t.Fatalf("normal transcript budgets = %d bytes/%d records, want defaults %d/%d", normalBytes, normalRecords, defaultTranscriptCycleMaxBytes, defaultTranscriptCycleMaxRecords)
	}

	repairBytes, repairRecords := transcriptCycleBudgets(defaultTranscriptCycleMaxBytes, defaultTranscriptCycleMaxRecords, true)
	if repairBytes != legacyCodexRepairCycleMaxBytes || repairRecords != legacyCodexRepairCycleMaxRecords {
		t.Fatalf("default Codex repair budgets = %d bytes/%d records, want Python-compatible %d/%d", repairBytes, repairRecords, legacyCodexRepairCycleMaxBytes, legacyCodexRepairCycleMaxRecords)
	}

	customBytes, customRecords := transcriptCycleBudgets(2<<20, 1024, true)
	if customBytes != 2<<20 || customRecords != 1024 {
		t.Fatalf("explicit synthetic repair budgets were overridden: %d bytes/%d records", customBytes, customRecords)
	}
}

func TestAgentTokenCollectorAppliesRepairBudgetToPendingRootWithoutOrphans(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	path := filepath.Join(root, "2026", "09", "24", "rollout-active.jsonl")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	var transcript bytes.Buffer
	encoder := json.NewEncoder(&transcript)
	if err := encoder.Encode(map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread"}}); err != nil {
		t.Fatal(err)
	}
	padding := strings.Repeat("x", 900)
	for index := 0; index < defaultTranscriptCycleMaxRecords+900; index++ {
		if err := encoder.Encode(map[string]any{"type": "event_msg", "payload": map[string]any{"padding": padding}}); err != nil {
			t.Fatal(err)
		}
	}
	if int64(transcript.Len()) <= defaultTranscriptCycleMaxBytes {
		t.Fatalf("synthetic transcript is only %d bytes; want more than the ordinary %d-byte cycle budget", transcript.Len(), defaultTranscriptCycleMaxBytes)
	}
	if int64(transcript.Len()) > legacyCodexRepairCycleMaxBytes {
		t.Fatalf("synthetic transcript is %d bytes; want it within the %d-byte repair budget", transcript.Len(), legacyCodexRepairCycleMaxBytes)
	}
	recordCount := defaultTranscriptCycleMaxRecords + 901 // one session header plus the padded rows
	if recordCount <= defaultTranscriptCycleMaxRecords || recordCount >= legacyCodexRepairCycleMaxRecords {
		t.Fatalf("synthetic transcript has %d records; want only the repair budget to cover it", recordCount)
	}
	if err := os.WriteFile(path, transcript.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}

	cursorPath := filepath.Join(t.TempDir(), "transcript-cursors.json")
	source := TranscriptSource{Path: path, Provider: "codex", SourceID: "codex:active", ThreadID: "root-thread"}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{source}, nil
		},
		TranscriptCursorPath: cursorPath,
	})
	_, receipts, err := collector.collectTranscripts(context.Background(), 100, 10, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatalf("collect synthetic pending-root transcript: %v", err)
	}
	if err := commitReceipts(receipts); err != nil {
		t.Fatalf("commit synthetic transcript cursor: %v", err)
	}
	state, err := NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatalf("read committed synthetic transcript cursor: %v", err)
	}
	if state.Offset != int64(transcript.Len()) {
		t.Fatalf("pending-root transcript offset = %d of %d bytes; want one-cycle repair budget to finish it", state.Offset, transcript.Len())
	}
}

func TestCodexForkRepairDoesNotMarkOverlappingNormalSourceCompleteBeforeHandoff(t *testing.T) {
	root := t.TempDir()
	sessionsRoot := filepath.Join(root, ".codex", "sessions")
	active := filepath.Join(sessionsRoot, "2026", "09", "23", "rollout-active.jsonl")
	orphan := filepath.Join(sessionsRoot, "2026", "01", "01", "rollout-orphan.jsonl")
	writeJSONL(t, active, map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread"}})
	writeJSONL(t, orphan,
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "orphan-child", "forked_from_id": "root-thread", "thread_source": "subagent"}},
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread"}},
		usageCodex(2, 80, 30, 12),
		map[string]any{"type": "inter_agent_communication_metadata", "payload": map[string]any{}},
	)
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	config := AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{
				{Path: active, Provider: "codex", SourceID: "codex:active", ThreadID: "root-thread"},
				{Path: orphan, Provider: "codex", SourceID: "codex:orphan", ThreadID: "orphan-child"},
			}, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursors.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 1,
	}
	collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
	rows := []AgentRecord{{Kind: "codex", TmuxSession: "fixture"}}
	first, receipts, err := collector.collectTranscripts(context.Background(), 100, 10, rows, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if ok, appendErr := collector.appendCollected(context.Background(), first, receipts); !ok {
		t.Fatal(appendErr)
	}
	if completed, err := collector.transcriptCursor.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 0 {
		t.Fatalf("overlapping normal source falsely completed repair before handoff: %v err=%v", completed, err)
	}
	collector.config.TranscriptMaxRecordsPerCycle = 100
	second, receipts, err := collector.collectTranscripts(context.Background(), 110, 10, rows, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if ok, appendErr := collector.appendCollected(context.Background(), second, receipts); !ok {
		t.Fatal(appendErr)
	}
	if completed, err := collector.transcriptCursor.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 1 {
		t.Fatalf("overlapping normal source repair roots after handoff = %v err=%v", completed, err)
	}
}

func TestCodexForkRepairTombstoneConflictDoesNotCompleteRoot(t *testing.T) {
	root := t.TempDir()
	sessionsRoot := filepath.Join(root, ".codex", "sessions")
	active := filepath.Join(sessionsRoot, "2026", "09", "23", "rollout-active.jsonl")
	firstOrphan := filepath.Join(sessionsRoot, "2026", "01", "01", "rollout-a.jsonl")
	secondOrphan := filepath.Join(sessionsRoot, "2026", "01", "02", "rollout-b.jsonl")
	writeJSONL(t, active, map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread"}})
	for path, quantity := range map[string]float64{firstOrphan: 80, secondOrphan: 81} {
		writeJSONL(t, path,
			map[string]any{"type": "session_meta", "payload": map[string]any{"id": "shared-child", "forked_from_id": "root-thread", "thread_source": "subagent"}},
			map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread", "model": "gpt-parent"}},
			usageCodex(2, quantity, 30, 12),
			map[string]any{"type": "inter_agent_communication_metadata", "payload": map[string]any{}},
		)
	}
	config := AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{{Path: active, Provider: "codex", SourceID: "codex:active", ThreadID: "root-thread"}}, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursors.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 100,
	}
	collector := NewAgentTokenCollector(countingTokenWriter{}, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
	batch, receipts, err := collector.collectTranscripts(context.Background(), 100, 10, []AgentRecord{{Kind: "codex", TmuxSession: "fixture"}}, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.UsageTombstones) != 0 {
		t.Fatalf("conflicting tombstones should not be applied: %#v", batch.UsageTombstones)
	}
	foundConflict := false
	for _, span := range batch.UnavailableSpans {
		if span.Reason == "transcript-tombstone-conflict" {
			foundConflict = true
		}
	}
	if !foundConflict {
		t.Fatalf("conflicting tombstones did not report an unavailable span: %#v", batch.UnavailableSpans)
	}
	if ok, appendErr := collector.appendCollected(context.Background(), batch, receipts); !ok {
		t.Fatal(appendErr)
	}
	if completed, err := collector.transcriptCursor.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 0 {
		t.Fatalf("tombstone conflict incorrectly completed repair: %v err=%v", completed, err)
	}
	for _, path := range []string{firstOrphan, secondOrphan} {
		source := TranscriptSource{Path: path, Provider: "codex", CodexForkRepairOnly: true}
		state, stateErr := collector.transcriptCursor.State(source)
		if stateErr != nil || state.Offset != 0 {
			t.Fatalf("conflicted repair cursor advanced for %s: state=%+v err=%v", path, state, stateErr)
		}
	}
}

func TestAgentTokenCollectorForkRepairCommitFailureRollsBackCursorForRetry(t *testing.T) {
	root := t.TempDir()
	sessionsRoot := filepath.Join(root, ".codex", "sessions")
	active := filepath.Join(sessionsRoot, "2026", "09", "23", "rollout-active.jsonl")
	orphan := filepath.Join(sessionsRoot, "2026", "01", "01", "rollout-orphan.jsonl")
	writeJSONL(t, active, map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread"}})
	writeJSONL(t, orphan,
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "orphan-child", "forked_from_id": "root-thread", "thread_source": "subagent"}},
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "root-thread", "model": "gpt-parent"}},
		usageCodex(2, 80, 30, 12),
		map[string]any{"type": "inter_agent_communication_metadata", "payload": map[string]any{}},
	)
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	config := AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{{Path: active, Provider: "codex", SourceID: "codex:active", ThreadID: "fixture"}}, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursors.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 100,
	}
	collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, config)
	baseStore := NewTranscriptCursorStore(config.TranscriptCursorPath)
	collector.transcriptCursor = failingCodexForkRepairMarkerStore{TranscriptCursorStore: baseStore}
	rows := []AgentRecord{{Kind: "codex", TmuxSession: "fixture"}}
	batch, receipts, err := collector.collectTranscripts(context.Background(), 100, 10, rows, nil, AppendBatch{}, nil)
	if err != nil || len(batch.UsageTombstones) != 3 {
		t.Fatalf("initial repair collection = tombstones=%#v err=%v", batch.UsageTombstones, err)
	}
	if ok, appendErr := collector.appendCollected(context.Background(), batch, receipts); ok || appendErr == nil || !strings.Contains(appendErr.Error(), "synthetic fork repair transaction failure") {
		t.Fatalf("append with repair transaction failure = ok %t err %v", ok, appendErr)
	}

	repairSource := TranscriptSource{Path: orphan, Provider: "codex", ThreadID: "orphan-child", CodexForkRepairOnly: true}
	persisted, err := baseStore.State(repairSource)
	if err != nil || persisted.Offset != 0 {
		t.Fatalf("cursor after repair transaction failure = %+v err=%v, want rolled-back offset 0", persisted, err)
	}
	if completed, err := baseStore.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 0 {
		t.Fatalf("completion root after injected failure = %v err=%v", completed, err)
	}

	retry := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307310, 0)}, config)
	replayed, retryReceipts, err := retry.collectTranscripts(context.Background(), 110, 10, rows, nil, AppendBatch{}, nil)
	if err != nil || len(replayed.UsageTombstones) != 3 {
		t.Fatalf("repair retry = tombstones=%#v err=%v", replayed.UsageTombstones, err)
	}
	if ok, appendErr := retry.appendCollected(context.Background(), replayed, retryReceipts); !ok {
		t.Fatal(appendErr)
	}
	if completed, err := retry.transcriptCursor.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 1 {
		t.Fatalf("completion marker after retry = %v err=%v", completed, err)
	}
}

func TestAgentTokenCollectorRepairsEachCodexSessionsRootIndependently(t *testing.T) {
	root := t.TempDir()
	statsPath := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	sessionsRoots := []string{
		filepath.Join(root, "first", ".codex", "sessions"),
		filepath.Join(root, "second", ".codex", "sessions"),
	}
	var activeSources []TranscriptSource
	var seededAtoms []UsageAtom
	for index, sessionsRoot := range sessionsRoots {
		child := fmt.Sprintf("orphan-child-%d", index+1)
		parent := fmt.Sprintf("root-thread-%d", index+1)
		active := filepath.Join(sessionsRoot, "2026", "09", "23", "rollout-active.jsonl")
		orphan := filepath.Join(sessionsRoot, "2026", "01", "01", "rollout-orphan.jsonl")
		writeJSONL(t, active, map[string]any{"type": "session_meta", "payload": map[string]any{"id": parent}})
		writeJSONL(t, orphan,
			map[string]any{"type": "session_meta", "payload": map[string]any{"id": child, "forked_from_id": parent, "thread_source": "subagent"}},
			map[string]any{"type": "session_meta", "payload": map[string]any{"id": parent, "model": "gpt-parent"}},
			usageCodex(2, 80, 30, 12),
			map[string]any{"type": "inter_agent_communication_metadata", "payload": map[string]any{}},
		)
		activeSources = append(activeSources, TranscriptSource{Path: active, Provider: "codex", SourceID: "codex:" + active, ThreadID: parent})
		seededAtoms = append(seededAtoms,
			UsageAtom{EventID: "codex:" + child + ":2", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 2, Payload: map[string]any{"quantity": 50.0, "provider": "openai", "model": "gpt-parent", "thread_id": child, "execution_source": "codex"}},
			UsageAtom{EventID: "codex:" + child + ":2", Direction: "input", Modality: "text", CacheRole: "read", Unit: "tokens", ObservedAt: 2, Payload: map[string]any{"quantity": 30.0, "provider": "openai", "model": "gpt-parent", "thread_id": child, "execution_source": "codex"}},
			UsageAtom{EventID: "codex:" + child + ":2", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 2, Payload: map[string]any{"quantity": 12.0, "provider": "openai", "model": "gpt-parent", "thread_id": child, "execution_source": "codex"}},
		)
	}
	if _, err := writer.AppendBatch(context.Background(), AppendBatch{UsageAtoms: seededAtoms}); err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(writer, &agentStatusTestClock{now: time.Unix(1788307300, 0)}, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return activeSources, nil
		},
		TranscriptCursorPath:         filepath.Join(root, "transcript-cursors.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 100,
	})
	rows := []AgentRecord{{Kind: "codex", TmuxSession: "fixture-one"}, {Kind: "codex", TmuxSession: "fixture-two"}}
	batch, receipts, err := collector.collectTranscripts(context.Background(), 100, 10, rows, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(batch.UsageTombstones) != 6 {
		t.Fatalf("multi-root tombstones = %#v, want 3 per sessions root", batch.UsageTombstones)
	}
	if ok, appendErr := collector.appendCollected(context.Background(), batch, receipts); !ok {
		t.Fatal(appendErr)
	}
	completed, err := collector.transcriptCursor.CompletedCodexForkRepairRoots()
	if err != nil {
		t.Fatal(err)
	}
	wantRoots := []string{codexForkRepairRootID(sessionsRoots[0]), codexForkRepairRootID(sessionsRoots[1])}
	sort.Strings(wantRoots)
	completedRoots := make([]string, 0, len(completed))
	for rootID := range completed {
		completedRoots = append(completedRoots, rootID)
	}
	sort.Strings(completedRoots)
	if !reflect.DeepEqual(completedRoots, wantRoots) {
		t.Fatalf("completed Codex roots = %+v, want %v", completedRoots, wantRoots)
	}
}

func TestCodexForkRepairDiscoveryResumesWithinDirectoryAndFileBudgets(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	for _, rollout := range []string{"a/rollout-a.jsonl", "b/rollout-b.jsonl"} {
		path := filepath.Join(root, rollout)
		child := strings.TrimSuffix(filepath.Base(path), ".jsonl")
		writeJSONL(t, path, map[string]any{"type": "session_meta", "payload": map[string]any{"id": child, "forked_from_id": "parent", "thread_source": "subagent"}})
	}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath:        filepath.Join(t.TempDir(), "cursor.json"),
		TranscriptDiscoveryMaxDirs:  1,
		TranscriptDiscoveryMaxFiles: 1,
	})
	rootID := codexForkRepairRootID(root)
	var sources []TranscriptSource
	complete := false
	for cycle := 0; cycle < 8 && !complete; cycle++ {
		var err error
		sources, complete, _, err = collector.codexForkRepairSources(context.Background(), root, rootID)
		if err != nil {
			t.Fatalf("repair discovery cycle %d: %v", cycle, err)
		}
	}
	if !complete || len(sources) != 2 {
		t.Fatalf("repair discovery = complete %t sources %#v, want both fork files after bounded pages", complete, sources)
	}
}

func TestCodexForkRepairDiscoveryCompletesWideBranchingTree(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	for parent := 0; parent < maxCodexForkRepairDirectoryFrontier+8; parent++ {
		for child := 0; child < 2; child++ {
			path := filepath.Join(root, fmt.Sprintf("parent-%04d", parent), fmt.Sprintf("child-%d", child))
			if err := os.MkdirAll(path, 0o700); err != nil {
				t.Fatal(err)
			}
		}
	}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath:       filepath.Join(t.TempDir(), "cursor.json"),
		TranscriptDiscoveryMaxDirs: maxCodexForkRepairDirectoryFrontier,
	})
	rootID := codexForkRepairRootID(root)
	for cycle := 0; cycle < 16; cycle++ {
		_, complete, _, err := collector.codexForkRepairSources(context.Background(), root, rootID)
		if err != nil {
			t.Fatalf("discovery cycle %d stopped on a finite branching tree: %v", cycle, err)
		}
		if complete {
			return
		}
	}
	t.Fatal("bounded discovery did not complete the finite branching tree")
}

func TestCodexForkRepairDiscoveryKeepsOverDepthTreeIncomplete(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	current := root
	for depth := 0; depth <= maxCodexForkRepairDirectoryFrontier; depth++ {
		current = filepath.Join(current, fmt.Sprintf("d%03d", depth))
		if err := os.MkdirAll(current, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath:       filepath.Join(t.TempDir(), "cursor.json"),
		TranscriptDiscoveryMaxDirs: maxCodexForkRepairDirectoryFrontier,
	})
	rootID := codexForkRepairRootID(root)
	for cycle := 0; cycle < 4; cycle++ {
		_, complete, _, err := collector.codexForkRepairSources(context.Background(), root, rootID)
		if err == nil {
			if complete {
				t.Fatal("discovery marked an over-depth tree complete")
			}
			continue
		}
		if !strings.Contains(err.Error(), "directory stack reached its bound") || complete {
			t.Fatalf("over-depth discovery = complete %t err %v, want bounded incomplete result", complete, err)
		}
		catalog := collector.codexForkRepairCatalog[rootID]
		if catalog == nil || catalog.directory == nil || len(catalog.directory.PendingNames) == 0 {
			t.Fatalf("over-depth discovery lost the unvisited child: %+v", catalog)
		}
		childPath := filepath.Join(catalog.directory.Path, catalog.directory.PendingNames[0])
		info, statErr := os.Stat(childPath)
		if statErr != nil || !info.IsDir() {
			t.Fatalf("over-depth child %q was not preserved: info=%v err=%v", childPath, info, statErr)
		}
		return
	}
	t.Fatal("over-depth discovery never reported its explicit bound")
}

func TestCodexForkRepairSidecarOverflowDoesNotReturnUndurableSources(t *testing.T) {
	cursorPath := filepath.Join(t.TempDir(), "cursor.json")
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	orphan := filepath.Join(root, "rollout-synthetic.jsonl")
	writeJSONL(t, orphan, map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "child", "forked_from_id": "parent", "thread_source": "subagent"},
	})
	source, _, budgetCut, err := codexForkRepairSource(root, orphan, 4096)
	if err != nil || budgetCut || source == nil {
		t.Fatalf("synthetic fork source = %#v, budget cut %t, err %v", source, budgetCut, err)
	}
	rootID := codexForkRepairRootID(root)
	pendingNames := make([]string, maxCodexForkRepairPageEntries-96)
	pendingNames[0] = filepath.Base(orphan)
	for index := 1; index < len(pendingNames); index++ {
		pendingNames[index] = strings.Repeat("n", maxCodexForkRepairNameBytes)
	}
	frontier := codexForkRepairFrontierState{Roots: map[string]codexForkRepairCatalogState{
		rootID: {
			Root: root, Initialized: true,
			Directory: &codexForkRepairDirectoryState{Path: root, PendingNames: pendingNames},
		},
	}}
	if err := validateCodexForkRepairFrontier(frontier); err != nil {
		t.Fatalf("large synthetic frontier is invalid: %v", err)
	}
	projectedFrontier := cloneCodexForkRepairFrontier(frontier)
	projectedCatalog := projectedFrontier.Roots[rootID]
	projectedCatalog.Directory.PendingNames = append([]string(nil), projectedCatalog.Directory.PendingNames[1:]...)
	projectedCatalog.Sources = append(projectedCatalog.Sources, *source)
	projectedFrontier.Roots[rootID] = projectedCatalog
	disk := func() transcriptCursorDisk {
		base := transcriptCursorDisk{
			Version: 1,
			States: map[string]TranscriptScannerState{
				"synthetic-large-cursor": {},
			},
			CodexForkRepair: frontier,
		}
		beforeEmpty, err := json.Marshal(base)
		if err != nil {
			t.Fatal(err)
		}
		projected := base
		projected.Generation++
		projected.CodexForkRepair = projectedFrontier
		afterEmpty, err := json.Marshal(projected)
		if err != nil {
			t.Fatal(err)
		}
		measure := func(pendingLineBytes int) (int, int) {
			encodedLineBytes := ((pendingLineBytes + 2) / 3) * 4
			return len(beforeEmpty) + encodedLineBytes - 2, len(afterEmpty) + encodedLineBytes - 2
		}
		low, high := 0, maxTranscriptCursorSidecarBytes
		for low+1 < high {
			middle := low + (high-low)/2
			beforeSize, _ := measure(middle)
			if beforeSize+1 <= maxTranscriptCursorSidecarBytes {
				low = middle
			} else {
				high = middle
			}
		}
		beforeSize, afterSize := measure(low)
		if beforeSize+1 > maxTranscriptCursorSidecarBytes || afterSize <= maxTranscriptCursorSidecarBytes {
			t.Fatalf("sidecar fixture does not straddle admission: before=%d after=%d bound=%d", beforeSize, afterSize, maxTranscriptCursorSidecarBytes)
		}
		state := base.States["synthetic-large-cursor"]
		state.PendingLine = make([]byte, low)
		base.States["synthetic-large-cursor"] = state
		return base
	}()
	store := NewTranscriptCursorStore(cursorPath)
	if err := store.writeDiskLocked(disk); err != nil {
		t.Fatalf("seed near-limit synthetic cursor sidecar: %v", err)
	}
	before, err := os.ReadFile(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath:        cursorPath,
		TranscriptDiscoveryMaxDirs:  1,
		TranscriptDiscoveryMaxFiles: 4,
	})
	sources, complete, _, err := collector.codexForkRepairSources(context.Background(), root, rootID)
	if err == nil || !strings.Contains(err.Error(), "exceeds its size bound") {
		t.Fatalf("aggregate sidecar admission error = %v, want size-bound rejection", err)
	}
	if complete || len(sources) != 0 {
		t.Fatalf("failed admission returned sources=%#v complete=%t", sources, complete)
	}
	if catalog := collector.codexForkRepairCatalog[rootID]; catalog == nil || catalog.directory == nil || len(catalog.sources) != 0 || len(catalog.directory.PendingNames) == 0 || catalog.directory.PendingNames[0] != filepath.Base(orphan) {
		t.Fatalf("failed admission changed in-memory catalog: %+v", catalog)
	}
	after, err := os.ReadFile(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(after, before) {
		t.Fatal("failed aggregate admission replaced the previous cursor sidecar")
	}
	fresh := NewTranscriptCursorStore(cursorPath)
	persisted, err := fresh.CodexForkRepairFrontier()
	if err != nil {
		t.Fatal(err)
	}
	if sources := persisted.Roots[rootID].Sources; len(sources) != 0 {
		t.Fatalf("fresh cursor store found undurable repair sources: %#v", sources)
	}
}

func TestCodexForkRepairDiscoveryBoundsAndResumesLargeHeaders(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	large := filepath.Join(root, "2026", "01", "01", "rollout-a-large.jsonl")
	regular := filepath.Join(root, "2026", "01", "01", "rollout-b-regular.jsonl")
	writeJSONL(t, large, map[string]any{
		"type": "session_meta",
		"payload": map[string]any{
			"id": "large-child", "forked_from_id": "parent", "thread_source": "subagent",
			"ignored_large_field": strings.Repeat("x", 2<<20),
		},
	})
	writeJSONL(t, regular, map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "regular-child", "forked_from_id": "parent", "thread_source": "subagent"},
	})
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath:        filepath.Join(t.TempDir(), "cursor.json"),
		TranscriptDiscoveryMaxDirs:  16,
		TranscriptDiscoveryMaxFiles: 8,
		TranscriptDiscoveryMaxBytes: 3 << 20,
	})
	rootID := codexForkRepairRootID(root)
	sources, complete, bytesRead, err := collector.codexForkRepairSources(context.Background(), root, rootID)
	if err != nil || !complete || len(sources) != 2 || bytesRead <= 1<<20 || bytesRead > 3<<20 {
		t.Fatalf("bounded large-header discovery = sources=%#v complete=%t bytes=%d err=%v", sources, complete, bytesRead, err)
	}

	limitedRoot := filepath.Join(t.TempDir(), ".codex", "sessions")
	limitedLarge := filepath.Join(limitedRoot, "2026", "01", "01", "rollout-a-large.jsonl")
	limitedRegular := filepath.Join(limitedRoot, "2026", "01", "01", "rollout-b-regular.jsonl")
	writeJSONL(t, limitedLarge, map[string]any{
		"type": "session_meta",
		"payload": map[string]any{
			"id": "large-child", "forked_from_id": "parent", "thread_source": "subagent",
			"ignored_large_field": strings.Repeat("x", 2<<20),
		},
	})
	writeJSONL(t, limitedRegular, map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "regular-child", "forked_from_id": "parent", "thread_source": "subagent"},
	})
	limited := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath:        filepath.Join(t.TempDir(), "cursor.json"),
		TranscriptDiscoveryMaxDirs:  16,
		TranscriptDiscoveryMaxFiles: 8,
		TranscriptDiscoveryMaxBytes: 1 << 20,
	})
	limitedRootID := codexForkRepairRootID(limitedRoot)
	first, firstComplete, firstBytes, firstErr := limited.codexForkRepairSources(context.Background(), limitedRoot, limitedRootID)
	if firstErr == nil || firstComplete || len(first) != 0 || firstBytes > 1<<20 {
		t.Fatalf("over-budget header discovery = sources=%#v complete=%t bytes=%d err=%v", first, firstComplete, firstBytes, firstErr)
	}
	second, secondComplete, secondBytes, secondErr := limited.codexForkRepairSources(context.Background(), limitedRoot, limitedRootID)
	if secondErr == nil || secondComplete || len(second) != 1 || second[0].ThreadID != "regular-child" || secondBytes > 1<<20 {
		t.Fatalf("discovery did not continue past over-budget header = sources=%#v complete=%t bytes=%d err=%v", second, secondComplete, secondBytes, secondErr)
	}
}

func TestCodexForkRepairDirectoryDiscoveryResumesAfterCollectorStop(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	if err := os.MkdirAll(filepath.Join(root, "2026", "01", "01"), 0o700); err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath:        filepath.Join(t.TempDir(), "cursor.json"),
		TranscriptDiscoveryMaxDirs:  1,
		TranscriptDiscoveryMaxFiles: 1,
	})
	rootID := codexForkRepairRootID(root)
	_, complete, _, err := collector.codexForkRepairSources(context.Background(), root, rootID)
	if err != nil || complete {
		t.Fatalf("initial bounded discovery = complete %t err=%v, want resumable cursor", complete, err)
	}
	collector.Stop()
	for cycle := 0; cycle < 16 && !complete; cycle++ {
		_, complete, _, err = collector.codexForkRepairSources(context.Background(), root, rootID)
		if err != nil {
			t.Fatalf("discovery after stop cycle %d: %v", cycle, err)
		}
	}
	if !complete {
		t.Fatal("directory discovery did not resume and complete after collector stop")
	}
}

func TestCodexForkRepairDirectoryMutationRestartsPortableContinuation(t *testing.T) {
	dir := t.TempDir()
	for index := 0; index < 1500; index++ {
		path := filepath.Join(dir, fmt.Sprintf("entry-%03d", index))
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	state, err := newCodexForkRepairDirectoryCursor(dir, dir)
	if err != nil {
		t.Fatal(err)
	}
	firstPage, eof, err := readCodexForkRepairDirectoryPage(dir, state)
	if err != nil || eof || len(firstPage) == 0 {
		t.Fatalf("initial directory page = %d names eof=%t err=%v", len(firstPage), eof, err)
	}
	legacyState, err := newCodexForkRepairDirectoryCursor(dir, dir)
	if err != nil {
		t.Fatal(err)
	}
	legacyState.CursorVersion = 0
	legacyState.CursorPlatform = ""
	legacyState.Cookie = int64(len(firstPage) / 2)
	legacyState.PendingNames = []string{"stale-page-entry"}
	legacyPage, legacyEOF, err := readCodexForkRepairDirectoryPage(dir, legacyState)
	if err != nil || legacyEOF || !reflect.DeepEqual(legacyPage, firstPage) {
		t.Fatalf("legacy raw-cookie continuation = %d names eof=%t err=%v, want replay from page start", len(legacyPage), legacyEOF, err)
	}
	beforeMutation, err := os.Stat(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "entry-added-after-page"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(dir, beforeMutation.ModTime(), beforeMutation.ModTime()); err != nil {
		t.Fatal(err)
	}
	if got := codexDirectoryChangeTimeNS(mustStat(t, dir)); got == state.ChangeTimeNS {
		t.Fatal("directory change time did not detect mutation after mtime restoration")
	}

	seen := make(map[string]struct{}, 1510)
	for cycle := 0; cycle < 8; cycle++ {
		names, complete, err := readCodexForkRepairDirectoryPage(dir, state)
		if err != nil {
			t.Fatal(err)
		}
		for _, name := range names {
			seen[name] = struct{}{}
		}
		if complete {
			break
		}
	}
	if _, ok := seen["entry-added-after-page"]; !ok {
		t.Fatal("portable continuation missed a file added between pages with the old mtime restored")
	}
}

func TestCodexForkRepairClearsTransientBlockAfterSuccessfulRetry(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	if err := os.MkdirAll(root, 0o700); err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath: filepath.Join(t.TempDir(), "cursor.json"),
	})
	rootID := codexForkRepairRootID(root)
	collector.codexForkRepairLoaded = true
	collector.codexForkRepairCatalog[rootID] = &codexForkRepairCatalog{
		root: root, pendingDirs: []string{root}, sources: make(map[string]TranscriptSource),
		blocked: true, initialized: true,
	}
	_, complete, _, err := collector.codexForkRepairSources(context.Background(), root, rootID)
	if err != nil || !complete {
		t.Fatalf("successful retry after transient directory error = complete %t err=%v", complete, err)
	}
}

func mustStat(t *testing.T, path string) os.FileInfo {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info
}

func TestCodexForkRepairDiscoveryResumesWithFreshCollector(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	forkPath := filepath.Join(root, "2026", "01", "01", "rollout-deep-fork.jsonl")
	writeJSONL(t, forkPath, map[string]any{
		"type": "session_meta",
		"payload": map[string]any{
			"id": "deep-child", "forked_from_id": "parent", "thread_source": "subagent",
		},
	})
	cursorPath := filepath.Join(t.TempDir(), "cursor.json")
	config := AgentTokenCollectorConfig{
		TranscriptCursorPath:        cursorPath,
		TranscriptDiscoveryMaxDirs:  1,
		TranscriptDiscoveryMaxFiles: 1,
	}
	first := NewAgentTokenCollector(nil, nil, config)
	if sources, complete, _, err := first.codexForkRepairSources(context.Background(), root, codexForkRepairRootID(root)); err != nil || complete || len(sources) != 0 {
		t.Fatalf("initial partial discovery = sources=%#v complete=%t err=%v", sources, complete, err)
	}

	restarted := NewAgentTokenCollector(nil, nil, config)
	var sources []TranscriptSource
	complete := false
	for cycle := 0; cycle < 16 && !complete; cycle++ {
		var err error
		sources, complete, _, err = restarted.codexForkRepairSources(context.Background(), root, codexForkRepairRootID(root))
		if err != nil {
			t.Fatalf("fresh-collector discovery cycle %d: %v", cycle, err)
		}
	}
	if !complete || len(sources) != 1 || sources[0].Path != forkPath || sources[0].ThreadID != "deep-child" {
		t.Fatalf("fresh-collector discovery = sources=%#v complete=%t, want the deep synthetic orphan", sources, complete)
	}
}

func TestCodexForkRepairDiscoverySurvivesRestartAfterEveryBoundedPage(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	forkPath := filepath.Join(root, "2026", "01", "01", "rollout-deep-fork.jsonl")
	writeJSONL(t, forkPath, map[string]any{
		"type": "session_meta",
		"payload": map[string]any{
			"id": "deep-child", "forked_from_id": "parent", "thread_source": "subagent",
		},
	})
	cursorPath := filepath.Join(t.TempDir(), "cursor.json")
	config := AgentTokenCollectorConfig{
		TranscriptCursorPath:        cursorPath,
		TranscriptDiscoveryMaxDirs:  1,
		TranscriptDiscoveryMaxFiles: 1,
	}
	rootID := codexForkRepairRootID(root)
	var sources []TranscriptSource
	complete := false
	for cycle := 0; cycle < 16 && !complete; cycle++ {
		collector := NewAgentTokenCollector(nil, nil, config)
		var err error
		sources, complete, _, err = collector.codexForkRepairSources(context.Background(), root, rootID)
		if err != nil {
			t.Fatalf("restart discovery page %d: %v", cycle, err)
		}
	}
	if !complete || len(sources) != 1 || sources[0].Path != forkPath || sources[0].ThreadID != "deep-child" {
		t.Fatalf("discovery across fresh collectors = sources=%#v complete=%t, want the deep synthetic orphan", sources, complete)
	}
}

func TestCodexForkRepairCatalogBoundsAndRotatesRoots(t *testing.T) {
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{})
	rootBase := t.TempDir()
	roots := make([]string, maxCodexForkRepairCatalogRoots+1)
	for index := range roots {
		roots[index] = filepath.Join(rootBase, fmt.Sprintf("sessions-%03d", index))
	}
	completed := make(map[string]struct{})
	first, err := collector.boundCodexForkRepairRoots(roots, completed)
	if err != nil {
		t.Fatalf("bound first root page: %v", err)
	}
	if len(first) != maxCodexForkRepairCatalogRoots || first[0] != roots[0] {
		t.Fatalf("first bounded root page = %d roots, starts at %q", len(first), first[0])
	}
	for _, root := range first {
		rootID := codexForkRepairRootID(root)
		collector.codexForkRepairCatalog[rootID] = &codexForkRepairCatalog{root: root, sources: make(map[string]TranscriptSource)}
	}
	second, err := collector.boundCodexForkRepairRoots(roots, completed)
	if err != nil {
		t.Fatalf("resume incomplete root page: %v", err)
	}
	if len(second) != maxCodexForkRepairCatalogRoots || second[0] != first[0] {
		t.Fatalf("incomplete bounded root page = %d roots, starts at %q; want retained page at %q", len(second), second[0], first[0])
	}
	if _, retained := collector.codexForkRepairCatalog[codexForkRepairRootID(roots[maxCodexForkRepairCatalogRoots-1])]; !retained {
		t.Fatal("incomplete selected root lost its catalog before discovery finished")
	}
	if _, retained := collector.codexForkRepairCatalog[codexForkRepairRootID(roots[0])]; !retained {
		t.Fatal("first selected root lost its catalog before discovery finished")
	}
	if len(collector.codexForkRepairCatalog) != maxCodexForkRepairCatalogRoots {
		t.Fatalf("catalog count while resuming incomplete page = %d, want %d", len(collector.codexForkRepairCatalog), maxCodexForkRepairCatalogRoots)
	}
	for _, root := range first {
		completed[codexForkRepairRootID(root)] = struct{}{}
	}
	third, err := collector.boundCodexForkRepairRoots(roots, completed)
	if err != nil {
		t.Fatalf("advance after completing root page: %v", err)
	}
	if len(third) != 1 || third[0] != roots[maxCodexForkRepairCatalogRoots] {
		t.Fatalf("completed bounded root page = %v, want final root %q", third, roots[maxCodexForkRepairCatalogRoots])
	}
	if _, retained := collector.codexForkRepairCatalog[codexForkRepairRootID(first[0])]; retained {
		t.Fatal("completed inactive root retained a catalog after advancing the page")
	}
	if len(collector.codexForkRepairCatalog) != 0 {
		t.Fatalf("catalog count after advancing to uninitialized final root = %d, want 0", len(collector.codexForkRepairCatalog))
	}
}

func TestCodexForkRepairDeadlineRotatesRootsUnderCatalogLimit(t *testing.T) {
	rootBase := t.TempDir()
	rootA := filepath.Join(rootBase, "a", "sessions")
	rootB := filepath.Join(rootBase, "b", "sessions")
	pathA := filepath.Join(rootA, "2026", "09", "27", "rollout-a.jsonl")
	pathB := filepath.Join(rootB, "2026", "09", "27", "rollout-b.jsonl")
	writeJSONL(t, pathA, usageCodex(1788307359, 7, 2, 5))
	writeJSONL(t, pathB, usageCodex(1788307359, 11, 3, 8))
	collector := NewAgentTokenCollector(countingTokenWriter{}, &agentStatusTestClock{now: time.Unix(1788307400, 0)}, AgentTokenCollectorConfig{
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "codex", TmuxSession: "codex-a"}, {Kind: "codex", TmuxSession: "codex-b"}}, nil
		},
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{
				{Path: pathA, Provider: "codex", SourceID: "codex:a"},
				{Path: pathB, Provider: "codex", SourceID: "codex:b"},
			}, nil
		},
		TranscriptCursorPath:       filepath.Join(rootBase, "cursor.json"),
		TranscriptDiscoveryTimeout: 200 * time.Millisecond, CodexForkRepairDiscoveryTimeout: time.Nanosecond,
		EpochStartedAt: 1788307200, OwnerGeneration: 1,
	})
	collector.RunOnce(context.Background())
	if collector.nextCodexForkRepairRoot != rootB {
		t.Fatalf("next repair root after first-root timeout = %q, want %q", collector.nextCodexForkRepairRoot, rootB)
	}
	page, err := collector.boundCodexForkRepairRoots([]string{rootA, rootB}, map[string]struct{}{})
	if err != nil {
		t.Fatalf("load rotated root page: %v", err)
	}
	if len(page) != 2 || page[0] != rootB {
		t.Fatalf("repair roots after timeout = %v, want root %q first", page, rootB)
	}
}

func TestCodexForkRepairCatalogBoundsSourcesPerRoot(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	for index := 0; index < maxCodexForkRepairSourcesPerRoot+1; index++ {
		path := filepath.Join(root, fmt.Sprintf("rollout-%03d.jsonl", index))
		writeJSONL(t, path, map[string]any{
			"type": "session_meta",
			"payload": map[string]any{
				"id": fmt.Sprintf("child-%03d", index), "forked_from_id": "parent", "thread_source": "subagent",
			},
		})
	}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptCursorPath:        filepath.Join(t.TempDir(), "cursor.json"),
		TranscriptDiscoveryMaxDirs:  16,
		TranscriptDiscoveryMaxFiles: maxCodexForkRepairSourcesPerRoot + 1,
	})
	rootID := codexForkRepairRootID(root)
	for cycle := 0; cycle < 8; cycle++ {
		if _, _, _, err := collector.codexForkRepairSources(context.Background(), root, rootID); err != nil {
			t.Fatalf("bounded catalog discovery cycle %d: %v", cycle, err)
		}
		if len(collector.codexForkRepairCatalog[rootID].sources) == maxCodexForkRepairSourcesPerRoot {
			break
		}
	}
	if _, _, _, err := collector.codexForkRepairSources(context.Background(), root, rootID); err != nil {
		t.Fatalf("discovery at full catalog cap: %v", err)
	}
	catalog := collector.codexForkRepairCatalog[rootID]
	if len(catalog.sources) != maxCodexForkRepairSourcesPerRoot || len(catalog.pendingFiles) == 0 {
		t.Fatalf("per-root catalog bound = %d sources, %d pending files; want cap %d and deferred work", len(catalog.sources), len(catalog.pendingFiles), maxCodexForkRepairSourcesPerRoot)
	}
}

func TestCodexForkRepairSourceSelectionRotatesPastThePerCycleCap(t *testing.T) {
	sources := make([]TranscriptSource, maxCodexForkRepairSourcesPerRoot+1)
	for index := range sources {
		sources[index] = TranscriptSource{
			Path:     filepath.Join(t.TempDir(), fmt.Sprintf("rollout-%03d.jsonl", index)),
			Provider: "codex",
		}
	}
	first, next := boundedCodexForkRepairSources(sources, "", maxCodexForkRepairSourcesPerRoot)
	if len(first) != maxCodexForkRepairSourcesPerRoot || next != transcriptCursorKey(sources[maxCodexForkRepairSourcesPerRoot]) {
		t.Fatalf("first repair source page = %d sources next=%q", len(first), next)
	}
	second, _ := boundedCodexForkRepairSources(sources, next, maxCodexForkRepairSourcesPerRoot)
	if len(second) != maxCodexForkRepairSourcesPerRoot || transcriptCursorKey(second[0]) != transcriptCursorKey(sources[maxCodexForkRepairSourcesPerRoot]) {
		t.Fatalf("rotated repair source page starts at %q, want %q", transcriptCursorKey(second[0]), transcriptCursorKey(sources[maxCodexForkRepairSourcesPerRoot]))
	}
	seen := make(map[string]struct{}, len(first)+len(second))
	for _, source := range append(first, second...) {
		seen[transcriptCursorKey(source)] = struct{}{}
	}
	if len(seen) != len(sources) {
		t.Fatalf("round-robin repair pages covered %d of %d sources", len(seen), len(sources))
	}
}

func TestCodexForkRepairDiscoveryRecoversBlockedFileAfterCrash(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	forkPath := filepath.Join(root, "a", "b", "rollout-fork.jsonl")
	writeJSONL(t, forkPath, map[string]any{
		"type": "session_meta",
		"payload": map[string]any{
			"id": "fork-child", "forked_from_id": "parent", "thread_source": "subagent",
			"ignored_large_field": strings.Repeat("x", 2<<20),
		},
	})
	cursorPath := filepath.Join(t.TempDir(), "cursor.json")
	config := AgentTokenCollectorConfig{
		TranscriptCursorPath:        cursorPath,
		TranscriptDiscoveryMaxDirs:  16,
		TranscriptDiscoveryMaxFiles: 8,
		TranscriptDiscoveryMaxBytes: 1 << 20,
	}
	collector := NewAgentTokenCollector(nil, nil, config)
	rootID := codexForkRepairRootID(root)
	_, complete, _, err := collector.codexForkRepairSources(context.Background(), root, rootID)
	if err == nil || complete || !strings.Contains(err.Error(), "header exceeds discovery byte budget") {
		t.Fatalf("over-budget synthetic header = complete %t err=%v", complete, err)
	}
	frontier, err := NewTranscriptCursorStore(cursorPath).CodexForkRepairFrontier()
	if err != nil {
		t.Fatal(err)
	}
	catalog, ok := frontier.Roots[rootID]
	if !ok || !catalog.Blocked || len(catalog.BlockedFiles) != 1 || catalog.BlockedFiles[0] != forkPath {
		t.Fatalf("synthetic interrupted collector did not persist the blocked transcript: catalog=%+v present=%t", catalog, ok)
	}
	config.TranscriptDiscoveryMaxBytes = 3 << 20
	restarted := NewAgentTokenCollector(nil, nil, config)
	var sources []TranscriptSource
	complete = false
	for cycle := 0; cycle < 16 && !complete; cycle++ {
		var err error
		sources, complete, _, err = restarted.codexForkRepairSources(context.Background(), root, rootID)
		if err != nil {
			t.Fatalf("discovery after Stop and collector reconstruction, cycle %d: %v", cycle, err)
		}
	}
	if !complete || len(sources) != 1 || sources[0].ThreadID != "fork-child" {
		t.Fatalf("re-enumeration after crash and collector reconstruction = complete %t sources=%#v", complete, sources)
	}
}

func TestCodexForkRepairSourceUsesThreadIDWhenSessionMetaHasNoID(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-thread-id.jsonl")
	writeJSONL(t, path, map[string]any{
		"type": "session_meta",
		"payload": map[string]any{
			"thread_id":      "child-from-thread-id",
			"forked_from_id": "parent-thread",
			"thread_source":  "subagent",
		},
	})

	source, _, budgetCut, err := codexForkRepairSource(filepath.Dir(path), path, 1<<20)
	if err != nil || budgetCut {
		t.Fatal(err)
	}
	if source == nil || source.ThreadID != "child-from-thread-id" || source.AgentID != "child-from-thread-id" {
		t.Fatalf("fork repair source = %+v, want child identity from thread_id", source)
	}
}

func TestCodexForkRepairSourceRejectsSymlinkedTranscript(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(t.TempDir(), "rollout-outside.jsonl")
	writeJSONL(t, target, map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "child", "forked_from_id": "parent", "thread_source": "subagent"},
	})
	link := filepath.Join(root, "rollout-linked.jsonl")
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("synthetic symlinks are unavailable: %v", err)
	}
	source, _, budgetCut, err := codexForkRepairSource(root, link, 1<<20)
	if err != nil || budgetCut || source != nil {
		t.Fatalf("symlinked transcript source = %+v budgetCut=%t err=%v, want no source", source, budgetCut, err)
	}
	state := TranscriptScannerState{}
	if _, err := ScanTranscript(TranscriptSource{Path: link, Provider: "codex", CodexForkRepairOnly: true}, &state, TranscriptScanOptions{}); err == nil {
		t.Fatal("fork-repair scanner followed a symlink to a transcript outside the sessions tree")
	}
}

func TestCodexForkRepairSourceRejectsSymlinkedAncestor(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	linkedDir := filepath.Join(root, "linked")
	targetDir := t.TempDir()
	if err := os.MkdirAll(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(targetDir, linkedDir); err != nil {
		t.Skipf("synthetic symlinks are unavailable: %v", err)
	}
	target := filepath.Join(targetDir, "rollout-ancestor.jsonl")
	writeJSONL(t, target, map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "child", "forked_from_id": "parent", "thread_source": "subagent"},
	})
	path := filepath.Join(linkedDir, filepath.Base(target))
	if _, _, _, err := codexForkRepairSource(root, path, 1<<20); err == nil {
		t.Fatal("repair source followed a symlinked ancestor inside the sessions root")
	}
	state := TranscriptScannerState{}
	if _, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", CodexForkRepairOnly: true}, &state, TranscriptScanOptions{}); err == nil {
		t.Fatal("fork-repair scanner followed a symlinked ancestor inside the sessions root")
	}
}

func TestCodexForkRepairDirectoryOpenRejectsSymlinkedChild(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	if err := os.MkdirAll(root, 0o700); err != nil {
		t.Fatal(err)
	}
	target := t.TempDir()
	link := filepath.Join(root, "linked-directory")
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("synthetic symlinks are unavailable: %v", err)
	}
	if _, _, err := openCodexForkRepairPathNoSymlinks(root, link, true); !errors.Is(err, errCodexForkRepairSymlink) && !errors.Is(err, errCodexForkRepairNotDirectory) {
		t.Fatalf("symlinked directory open error = %v, want no-follow rejection", err)
	}
}

func TestCodexForkRepairOpensRootThroughSymlinkedAncestor(t *testing.T) {
	physicalParent := t.TempDir()
	rootParent := t.TempDir()
	rootAlias := filepath.Join(rootParent, "root-alias")
	if err := os.Symlink(physicalParent, rootAlias); err != nil {
		t.Skipf("synthetic symlinks are unavailable: %v", err)
	}
	root := filepath.Join(rootAlias, "sessions")
	path := filepath.Join(root, "rollout-synthetic.jsonl")
	writeJSONL(t, path, map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "child", "forked_from_id": "parent", "thread_source": "subagent"},
	})
	source, _, budgetCut, err := codexForkRepairSource(root, path, 1<<20)
	if err != nil || budgetCut || source == nil {
		t.Fatalf("repair source beneath a symlinked ancestor = %+v budgetCut=%t err=%v", source, budgetCut, err)
	}
	state := TranscriptScannerState{}
	result, err := ScanTranscript(*source, &state, TranscriptScanOptions{})
	if err != nil || result.Receipt == nil {
		t.Fatalf("repair scan beneath a symlinked ancestor = %+v err=%v", result, err)
	}
}

func TestCodexForkRepairSourceKeepsNearLimitPathWithinFrontierBound(t *testing.T) {
	base := t.TempDir()
	filename := "rollout-long.jsonl"
	wantPathLength := maxCodexForkRepairPathBytes - 5
	directoryLength := wantPathLength - len(filename) - 1
	directory := base
	for len(directory) < directoryLength {
		componentLength := min(240, directoryLength-len(directory)-1)
		if componentLength < 1 {
			t.Fatalf("cannot construct synthetic path of %d bytes from base %d", wantPathLength, len(base))
		}
		directory = filepath.Join(directory, strings.Repeat("d", componentLength))
	}
	path := filepath.Join(directory, filename)
	if len(path) != wantPathLength {
		t.Fatalf("synthetic path length = %d, want %d", len(path), wantPathLength)
	}
	writeJSONL(t, path, map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "child", "forked_from_id": "parent", "thread_source": "subagent"},
	})
	source, _, budgetCut, err := codexForkRepairSource(base, path, 1<<20)
	if err != nil || budgetCut || source == nil {
		t.Fatalf("near-limit repair source = %+v budgetCut=%t err=%v", source, budgetCut, err)
	}
	rootID := codexForkRepairRootID(base)
	frontier := codexForkRepairFrontierState{Roots: map[string]codexForkRepairCatalogState{
		rootID: {Root: base, Sources: []TranscriptSource{*source}},
	}}
	if err := validateCodexForkRepairFrontier(frontier); err != nil {
		t.Fatalf("valid near-limit source was rejected by frontier validation: %v", err)
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
