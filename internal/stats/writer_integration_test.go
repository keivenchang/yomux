package stats

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yolomux/internal/testsupport"
	"github.com/mattn/go-sqlite3"
)

func TestWriterIntegrationWithTranscriptAndOpenCodeFacts(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	claudePath := copyIntegrationFixture(t, filepath.Join(".claude", "projects", "-synthetic-workspace", "session-observed.jsonl"), "observed.claude.jsonl")
	codexPath := copyIntegrationFixture(t, filepath.Join(".codex", "sessions", "2026", "09", "02", "rollout-observed.jsonl"), "observed.codex.jsonl")

	claude, err := ScanTranscript(TranscriptSource{Path: claudePath, Provider: "claude", SourceID: "claude-observed", ThreadID: "claude-thread"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	codex, err := ScanTranscript(TranscriptSource{Path: codexPath, Provider: "codex", SourceID: "codex-observed", ThreadID: "codex-thread"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if claude.RecordsParsed != 6 || len(claude.Atoms) != 12 || atomQuantity(claude.Atoms, "input", "none") != 60 || atomQuantity(claude.Atoms, "output", "none") != 30 {
		t.Fatalf("Claude facts = records %d atoms %d quantities %#v", claude.RecordsParsed, len(claude.Atoms), atomQuantities(claude.Atoms))
	}
	if codex.RecordsParsed != 12 || len(codex.Atoms) != 12 || atomQuantity(codex.Atoms, "input", "none") != 60 || atomQuantity(codex.Atoms, "output", "none") != 30 {
		t.Fatalf("Codex facts = records %d atoms %d quantities %#v", codex.RecordsParsed, len(codex.Atoms), atomQuantities(codex.Atoms))
	}

	opencodePath := filepath.Join(root, ".local", "share", "opencode", "opencode.db")
	createIntegrationOpenCodeDB(t, opencodePath)
	opencode, err := OpenOpenCodeReadOnly(ctx, opencodePath)
	if err != nil {
		t.Fatal(err)
	}
	parts, err := opencode.ReadStepFinishParts(ctx, "ses-integration", 1000, 3000, 10)
	if closeErr := opencode.Close(); closeErr != nil {
		t.Fatal(closeErr)
	}
	if err != nil {
		t.Fatal(err)
	}
	if len(parts) != 1 || parts[0].Provider != "openai" || parts[0].Model != "gpt-integration" || parts[0].Tokens.Input != 3 || parts[0].Tokens.CacheRead != 2 || parts[0].Tokens.Output != 5 {
		t.Fatalf("OpenCode parts = %+v", parts)
	}

	batch := integrationBatch(claude.Atoms, codex.Atoms, parts)
	if len(batch.Observations) != 27 || len(batch.UsageAtoms) != 27 || totalQuantity(batch.UsageAtoms, "input") != 123 || totalQuantity(batch.UsageAtoms, "output") != 65 {
		t.Fatalf("combined parity facts: observations=%d usage=%d input=%g output=%g", len(batch.Observations), len(batch.UsageAtoms), totalQuantity(batch.UsageAtoms, "input"), totalQuantity(batch.UsageAtoms, "output"))
	}
	path := filepath.Join(root, "stats", "yostats.sqlite3")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	first, err := writer.AppendBatch(ctx, batch)
	if err != nil {
		t.Fatal(err)
	}
	if first.Generation != 1 || first.ObservationsAccepted != len(batch.Observations) || first.UsageAccepted != len(batch.UsageAtoms) {
		t.Fatalf("first append = %+v, batch sizes observations=%d usage=%d", first, len(batch.Observations), len(batch.UsageAtoms))
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	observations, err := reader.ReadObservations(ctx, 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	usage, err := reader.ReadUsageAtoms(ctx, 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if reader.Header.SchemaVersion != 9 || reader.Header.SourceGeneration != 1 || len(observations) != len(batch.Observations) || len(usage) != len(batch.UsageAtoms) {
		t.Fatalf("readback header=%+v observations=%d usage=%d", reader.Header, len(observations), len(usage))
	}
	if !sort.SliceIsSorted(observations, func(i, j int) bool {
		return observations[i].ObservedAt < observations[j].ObservedAt || (observations[i].ObservedAt == observations[j].ObservedAt && observations[i].Family < observations[j].Family) || (observations[i].ObservedAt == observations[j].ObservedAt && observations[i].Family == observations[j].Family && observations[i].SourceID < observations[j].SourceID) || (observations[i].ObservedAt == observations[j].ObservedAt && observations[i].Family == observations[j].Family && observations[i].SourceID == observations[j].SourceID && observations[i].EventID < observations[j].EventID)
	}) || !sort.SliceIsSorted(usage, func(i, j int) bool {
		return usage[i].ObservedAt < usage[j].ObservedAt || (usage[i].ObservedAt == usage[j].ObservedAt && usage[i].EventID < usage[j].EventID)
	}) {
		t.Fatal("readback rows are not in deterministic order")
	}
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}

	writer, err = OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := writer.AppendBatch(ctx, batch)
	if err != nil {
		t.Fatal(err)
	}
	if replay.Generation != 1 || replay.ObservationsDuplicate != len(batch.Observations) || replay.UsageDuplicate != len(batch.UsageAtoms) {
		t.Fatalf("replay = %+v", replay)
	}

	conflict := batch
	conflict.Observations = append([]Observation(nil), batch.Observations...)
	conflict.Observations[0].Payload = map[string]any{"changed": true}
	if _, err := writer.AppendBatch(ctx, conflict); err == nil {
		t.Fatal("identity payload conflict accepted")
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err = OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	unchanged, err := reader.ReadObservations(ctx, 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(observations, unchanged) || reader.Header.SourceGeneration != 1 {
		t.Fatalf("conflict changed readback: generation=%d rows=%d", reader.Header.SourceGeneration, len(unchanged))
	}
	schemaVersion, sourceGeneration := reader.Header.SchemaVersion, reader.Header.SourceGeneration
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}
	t.Logf("SQLite readback: schema=%d generation=%d observations=%d usage_atoms=%d input_tokens=%g output_tokens=%g", schemaVersion, sourceGeneration, len(observations), len(usage), totalQuantity(usage, "input"), totalQuantity(usage, "output"))
}

func TestTranscriptStatsCrossStoreCrashBoundariesReplayExactlyOnce(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	transcriptPath := filepath.Join(root, "claude.jsonl")
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	databasePath := filepath.Join(root, "stats.sqlite3")
	source := TranscriptSource{Path: transcriptPath, Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	writeJSONL(t, transcriptPath, usageClaude(1, "first", 10, 2))
	state := &TranscriptScannerState{}
	scan, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || scan.Receipt == nil || len(scan.Atoms) != 2 {
		t.Fatalf("initial transcript scan = %+v, err=%v", scan, err)
	}
	observations := make([]Observation, 0, len(scan.Atoms))
	for _, atom := range scan.Atoms {
		observations = append(observations, Observation{
			EventID: usageFactObservationID(atom), Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
			EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload,
		})
	}
	batch := AppendBatch{Observations: observations, UsageAtoms: scan.Atoms}

	// Crash after the cursor prepare but before the stats transaction: the new
	// owner must discard the pending sidecar state and replay the source.
	store := NewTranscriptCursorStore(cursorPath)
	if err := store.Prepare(source, scan.Receipt.before, scan.Receipt.after); err != nil {
		t.Fatal(err)
	}
	preparedBytes, err := os.ReadFile(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	var preparedDisk transcriptCursorDisk
	if err := json.Unmarshal(preparedBytes, &preparedDisk); err != nil {
		t.Fatalf("decode prepared transcript cursor: %v", err)
	}
	key := transcriptCursorKey(source)
	if preparedDisk.PendingTokens[key] == "" || preparedDisk.Pending[key].Offset != scan.Receipt.after.Offset || preparedDisk.States[key].Offset != scan.Receipt.before.Offset {
		t.Fatalf("cursor prepare was not durably staged: committed=%+v pending=%+v token=%q", preparedDisk.States[key], preparedDisk.Pending[key], preparedDisk.PendingTokens[key])
	}
	restarted := NewTranscriptCursorStore(cursorPath)
	loaded, err := restarted.State(source)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Offset != 0 {
		t.Fatalf("cursor advanced before stats append: %+v", loaded)
	}
	recoveredBytes, err := os.ReadFile(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	var recoveredDisk transcriptCursorDisk
	if err := json.Unmarshal(recoveredBytes, &recoveredDisk); err != nil {
		t.Fatalf("decode recovered transcript cursor: %v", err)
	}
	if len(recoveredDisk.Pending) != 0 || len(recoveredDisk.PendingTokens) != 0 {
		t.Fatalf("restarted cursor did not discard the uncommitted prepare: %+v", recoveredDisk)
	}
	writer, err := OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if result, appendErr := writer.AppendBatch(ctx, batch); appendErr != nil || result.UsageAccepted != len(scan.Atoms) {
		t.Fatalf("replayed first append = %+v, err=%v", result, appendErr)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := restarted.Prepare(source, loaded, scan.Receipt.after); err != nil {
		t.Fatal(err)
	}
	if err := restarted.Commit(source, scan.Receipt.after); err != nil {
		t.Fatal(err)
	}

	// Crash after the stats transaction but before cursor commit: replaying the
	// same source must be accepted as duplicate facts, then the cursor can be
	// committed without double counting.
	store = NewTranscriptCursorStore(cursorPath)
	committedBefore, err := store.State(source)
	if err != nil {
		t.Fatal(err)
	}
	appendJSONL(t, transcriptPath, usageClaude(2, "second", 15, 3), false)
	nextState := cloneTranscriptState(committedBefore)
	nextScan, err := ScanTranscript(source, &nextState, TranscriptScanOptions{})
	if err != nil || nextScan.Receipt == nil || len(nextScan.Atoms) != 2 {
		t.Fatalf("second transcript scan = %+v, err=%v", nextScan, err)
	}
	nextObservations := make([]Observation, 0, len(nextScan.Atoms))
	for _, atom := range nextScan.Atoms {
		nextObservations = append(nextObservations, Observation{
			EventID: usageFactObservationID(atom), Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
			EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload,
		})
	}
	if err := store.Prepare(source, nextScan.Receipt.before, nextScan.Receipt.after); err != nil {
		t.Fatal(err)
	}
	writer, err = OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	result, err := writer.AppendBatch(ctx, AppendBatch{Observations: nextObservations, UsageAtoms: nextScan.Atoms})
	if err != nil {
		t.Fatal(err)
	}
	if result.UsageAccepted != 2 || result.UsageDuplicate != 0 {
		t.Fatalf("second append before simulated crash = %+v", result)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	crashedOwner := NewTranscriptCursorStore(cursorPath)
	loaded, err = crashedOwner.State(source)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Offset != committedBefore.Offset {
		t.Fatalf("cursor advanced after stats-only commit: before=%d after=%d", committedBefore.Offset, loaded.Offset)
	}
	replayState := loaded
	replay, err := ScanTranscript(source, &replayState, TranscriptScanOptions{})
	if err != nil || replay.Receipt == nil || len(replay.Atoms) != 2 {
		t.Fatalf("post-crash replay scan = %+v, err=%v", replay, err)
	}
	writer, err = OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	result, err = writer.AppendBatch(ctx, AppendBatch{Observations: nextObservations, UsageAtoms: replay.Atoms})
	if err != nil {
		t.Fatal(err)
	}
	if result.UsageAccepted != 0 || result.UsageDuplicate != 2 || result.ObservationsAccepted != 0 || result.ObservationsDuplicate != 2 {
		t.Fatalf("post-crash duplicate replay = %+v", result)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := crashedOwner.Prepare(source, loaded, replay.Receipt.after); err != nil {
		t.Fatal(err)
	}
	if err := crashedOwner.Commit(source, replay.Receipt.after); err != nil {
		t.Fatal(err)
	}
	finalState, err := NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatal(err)
	}
	if finalState.Offset != replay.Receipt.after.Offset {
		t.Fatalf("final cursor offset = %d, want %d", finalState.Offset, replay.Receipt.after.Offset)
	}
	reader, err := OpenReadOnly(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	usage, err := reader.ReadUsageAtoms(ctx, 0, 10, MaxFactReadLimit)
	if closeErr := reader.Close(); err == nil && closeErr != nil {
		err = closeErr
	}
	if err != nil {
		t.Fatal(err)
	}
	if len(usage) != 4 {
		t.Fatalf("durable usage facts after both crash boundaries = %d, want 4", len(usage))
	}
}

func TestBudgetedTranscriptAppendAndCursorReplayAcrossCollectorRestarts(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	transcriptPath := filepath.Join(root, "claude.jsonl")
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	databasePath := filepath.Join(root, "stats.sqlite3")
	source := TranscriptSource{Path: transcriptPath, Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	writeJSONL(t, transcriptPath,
		usageClaude(1, "first", 10, 2),
		usageClaude(2, "second", 11, 3),
		usageClaude(3, "third", 12, 4),
	)
	config := AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{source}, nil
		},
		TranscriptCursorPath:         cursorPath,
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 1,
		EpochID:                      "synthetic-transcript",
		EpochStartedAt:               1,
		OwnerGeneration:              1,
	}
	eventIDFor := func(recordID string) string {
		return "claude:" + transcriptPath + ":" + recordID
	}
	collect := func(collector *AgentTokenCollector, observed float64) (AppendBatch, []agentTokenReceipt) {
		t.Helper()
		batch, receipts, err := collector.collectTranscripts(ctx, observed, 10, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatalf("collect synthetic transcript: %v", err)
		}
		if len(batch.UsageAtoms) > 2 {
			t.Fatalf("bounded cycle returned %d atoms, want at most one record's two token facts", len(batch.UsageAtoms))
		}
		return batch, receipts
	}

	writer, err := OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	firstOwner := NewAgentTokenCollector(writer, nil, config)
	firstBatch, firstReceipts := collect(firstOwner, 10)
	if len(firstBatch.UsageAtoms) != 2 || len(firstReceipts) == 0 {
		t.Fatalf("first bounded scan = atoms:%d receipts:%d, want 2 and a cursor receipt", len(firstBatch.UsageAtoms), len(firstReceipts))
	}
	for _, atom := range firstBatch.UsageAtoms {
		if atom.EventID != eventIDFor("first") {
			t.Fatalf("first bounded scan consumed event %q, want %s", atom.EventID, eventIDFor("first"))
		}
	}
	firstAppend, err := writer.AppendBatch(ctx, firstBatch)
	if err != nil || firstAppend.UsageAccepted != 2 {
		t.Fatalf("first stats append = %+v, err=%v", firstAppend, err)
	}
	// Simulate process death after durable facts commit but before cursor receipt
	// commit. A new collector must replay this bounded batch, deduplicate it, then
	// advance the cursor only after its own successful append.
	state, err := NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil || state.Offset != 0 {
		t.Fatalf("cursor advanced before receipt commit: state=%+v err=%v", state, err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	writer, err = OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	restarted := NewAgentTokenCollector(writer, nil, config)
	replayBatch, replayReceipts := collect(restarted, 11)
	if !reflect.DeepEqual(firstBatch.UsageAtoms, replayBatch.UsageAtoms) {
		t.Fatalf("post-crash bounded replay differs: first=%+v replay=%+v", firstBatch.UsageAtoms, replayBatch.UsageAtoms)
	}
	appendOK, appendErr := restarted.appendCollected(ctx, replayBatch, replayReceipts)
	if !appendOK || appendErr != nil {
		t.Fatalf("post-crash replay append = ok:%v err:%v", appendOK, appendErr)
	}
	if got := restarted.Counters().UsageDuplicates; got != 2 {
		t.Fatalf("post-crash duplicate count = %d, want 2", got)
	}
	state, err = NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatal(err)
	}
	priorOffset := state.Offset
	if priorOffset <= 0 || priorOffset >= transcriptFixtureSize(t, transcriptPath) {
		t.Fatalf("cursor after replay commit = %d, want a partial backfill offset", priorOffset)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	for cycle, recordID := range []string{"second", "third"} {
		writer, err = OpenWriter(ctx, databasePath)
		if err != nil {
			t.Fatal(err)
		}
		owner := NewAgentTokenCollector(writer, nil, config)
		batch, receipts := collect(owner, float64(12+cycle))
		if len(batch.UsageAtoms) != 2 || len(receipts) == 0 {
			t.Fatalf("bounded cycle %d = atoms:%d receipts:%d, want 2 and a cursor receipt", cycle, len(batch.UsageAtoms), len(receipts))
		}
		for _, atom := range batch.UsageAtoms {
			wantEventID := eventIDFor(recordID)
			if atom.EventID != wantEventID {
				t.Fatalf("bounded cycle %d consumed event %q, want %s", cycle, atom.EventID, wantEventID)
			}
		}
		appendOK, appendErr = owner.appendCollected(ctx, batch, receipts)
		if !appendOK || appendErr != nil {
			t.Fatalf("bounded cycle %d append = ok:%v err:%v", cycle, appendOK, appendErr)
		}
		if got := owner.Counters().UsageAccepted; got != 2 {
			t.Fatalf("bounded cycle %d accepted %d facts, want 2", cycle, got)
		}
		state, err = NewTranscriptCursorStore(cursorPath).State(source)
		if err != nil {
			t.Fatal(err)
		}
		if state.Offset <= priorOffset {
			t.Fatalf("bounded cycle %d cursor offset = %d, did not advance past %d", cycle, state.Offset, priorOffset)
		}
		priorOffset = state.Offset
		if err := writer.Close(); err != nil {
			t.Fatal(err)
		}
	}

	state, err = NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatal(err)
	}
	if size := transcriptFixtureSize(t, transcriptPath); state.Offset != size {
		t.Fatalf("bounded backfill cursor offset = %d, want %d", state.Offset, size)
	}
	appendJSONL(t, transcriptPath, usageClaude(4, "appended", 13, 5), false)
	writer, err = OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	appendedOwner := NewAgentTokenCollector(writer, nil, config)
	appendedBatch, appendedReceipts := collect(appendedOwner, 14)
	if len(appendedBatch.UsageAtoms) != 2 {
		t.Fatalf("post-backfill append scan returned %d atoms, want 2", len(appendedBatch.UsageAtoms))
	}
	for _, atom := range appendedBatch.UsageAtoms {
		if atom.EventID != eventIDFor("appended") {
			t.Fatalf("post-backfill append consumed event %q, want %s", atom.EventID, eventIDFor("appended"))
		}
	}
	appendOK, appendErr = appendedOwner.appendCollected(ctx, appendedBatch, appendedReceipts)
	if !appendOK || appendErr != nil || appendedOwner.Counters().UsageAccepted != 2 {
		t.Fatalf("post-backfill append = ok:%v err:%v counters:%+v", appendOK, appendErr, appendedOwner.Counters())
	}
	state, err = NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatal(err)
	}
	if size := transcriptFixtureSize(t, transcriptPath); state.Offset != size {
		t.Fatalf("cursor after appended record = %d, want %d", state.Offset, size)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	reader, err := OpenReadOnly(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	usage, err := reader.ReadUsageAtoms(ctx, 0, 10, MaxFactReadLimit)
	if closeErr := reader.Close(); err == nil && closeErr != nil {
		err = closeErr
	}
	if err != nil {
		t.Fatal(err)
	}
	if len(usage) != 8 {
		t.Fatalf("durable synthetic usage facts = %d, want 8 without replay double-counting", len(usage))
	}
	seen := make(map[string]struct{}, len(usage))
	appendedDirections := map[string]bool{}
	for _, atom := range usage {
		key := atom.EventID + "\x00" + atom.Direction
		if _, exists := seen[key]; exists {
			t.Fatalf("duplicate durable usage identity %q", key)
		}
		seen[key] = struct{}{}
		if atom.EventID == eventIDFor("appended") {
			quantity, ok := atom.Payload["quantity"].(float64)
			if !ok {
				t.Fatalf("appended %s quantity has type %T", atom.Direction, atom.Payload["quantity"])
			}
			want := map[string]float64{"input": 13, "output": 5}[atom.Direction]
			if quantity != want {
				t.Fatalf("appended %s quantity = %v, want %v", atom.Direction, quantity, want)
			}
			appendedDirections[atom.Direction] = true
		}
	}
	if !appendedDirections["input"] || !appendedDirections["output"] {
		t.Fatalf("durable facts omit the appended event's input/output pair: %#v", appendedDirections)
	}
}

func TestTranscriptStatsProcessCrashAfterAppendReplaysWithoutDoubleCount(t *testing.T) {
	if os.Getenv("YOLOMUX_TRANSCRIPT_CRASH_HELPER") == "1" {
		ctx := context.Background()
		source := TranscriptSource{Path: os.Getenv("YOLOMUX_TRANSCRIPT_CRASH_SOURCE"), Provider: "claude", SourceID: "claude", ThreadID: "thread"}
		scan, err := ScanTranscript(source, &TranscriptScannerState{}, TranscriptScanOptions{})
		if err != nil || scan.Receipt == nil {
			fmt.Fprintln(os.Stdout, "scan failed:", err)
			os.Exit(2)
		}
		cursor := NewTranscriptCursorStore(os.Getenv("YOLOMUX_TRANSCRIPT_CRASH_CURSOR"))
		if err := cursor.Prepare(source, scan.Receipt.before, scan.Receipt.after); err != nil {
			fmt.Fprintln(os.Stdout, "cursor prepare failed:", err)
			os.Exit(3)
		}
		observations := make([]Observation, 0, len(scan.Atoms))
		for _, atom := range scan.Atoms {
			observations = append(observations, Observation{
				EventID: usageFactObservationID(atom), Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
				EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload,
			})
		}
		writer, err := OpenWriter(ctx, os.Getenv("YOLOMUX_TRANSCRIPT_CRASH_DATABASE"))
		if err != nil {
			fmt.Fprintln(os.Stdout, "writer open failed:", err)
			os.Exit(4)
		}
		if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: observations, UsageAtoms: scan.Atoms}); err != nil {
			fmt.Fprintln(os.Stdout, "stats append failed:", err)
			os.Exit(5)
		}
		// Exit without closing the writer or committing the cursor. The OS releases
		// the lease, while SQLite keeps the committed facts and the cursor remains
		// at its pre-append position for the replacement owner to replay.
		fmt.Fprintln(os.Stdout, "appended")
		os.Exit(0)
	}

	root := t.TempDir()
	transcriptPath := filepath.Join(root, "claude.jsonl")
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	databasePath := filepath.Join(root, "stats.sqlite3")
	writeJSONL(t, transcriptPath, usageClaude(1, "process-crash", 10, 2))
	command := exec.Command(os.Args[0], "-test.run=^TestTranscriptStatsProcessCrashAfterAppendReplaysWithoutDoubleCount$")
	command.Env = testsupport.SyntheticEnvironment(
		"YOLOMUX_TRANSCRIPT_CRASH_HELPER=1",
		"YOLOMUX_TRANSCRIPT_CRASH_SOURCE="+transcriptPath,
		"YOLOMUX_TRANSCRIPT_CRASH_CURSOR="+cursorPath,
		"YOLOMUX_TRANSCRIPT_CRASH_DATABASE="+databasePath,
	)
	stdout, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil {
		t.Fatalf("crash helper did not announce append: %v", err)
	}
	if line != "appended\n" {
		t.Fatalf("crash helper announcement = %q", line)
	}
	if err := command.Wait(); err != nil {
		t.Fatalf("crash helper exited with append error: %v", err)
	}

	source := TranscriptSource{Path: transcriptPath, Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	cursor := NewTranscriptCursorStore(cursorPath)
	state, err := cursor.State(source)
	if err != nil {
		t.Fatal(err)
	}
	if state.Offset != 0 {
		t.Fatalf("crashed cursor advanced before commit: %+v", state)
	}
	scan, err := ScanTranscript(source, &state, TranscriptScanOptions{})
	if err != nil || scan.Receipt == nil || len(scan.Atoms) != 2 {
		t.Fatalf("restart replay scan = %+v, err=%v", scan, err)
	}
	observations := make([]Observation, 0, len(scan.Atoms))
	for _, atom := range scan.Atoms {
		observations = append(observations, Observation{
			EventID: usageFactObservationID(atom), Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
			EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload,
		})
	}
	writer, err := OpenWriter(context.Background(), databasePath)
	if err != nil {
		t.Fatal(err)
	}
	result, err := writer.AppendBatch(context.Background(), AppendBatch{Observations: observations, UsageAtoms: scan.Atoms})
	if err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if result.UsageAccepted != 0 || result.UsageDuplicate != len(scan.Atoms) || result.ObservationsAccepted != 0 || result.ObservationsDuplicate != len(observations) {
		_ = writer.Close()
		t.Fatalf("process-crash replay changed durable facts: %+v", result)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := cursor.Prepare(source, state, scan.Receipt.after); err != nil {
		t.Fatal(err)
	}
	if err := cursor.Commit(source, scan.Receipt.after); err != nil {
		t.Fatal(err)
	}
	committed, err := NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatal(err)
	}
	if committed.Offset != scan.Receipt.after.Offset {
		t.Fatalf("replayed cursor offset = %d, want %d", committed.Offset, scan.Receipt.after.Offset)
	}
}

func TestTranscriptStatsProcessCrashMatrixRecoversEveryBoundary(t *testing.T) {
	const helperTest = "^TestTranscriptStatsProcessCrashMatrixRecoversEveryBoundary$"
	if os.Getenv("YOLOMUX_TRANSCRIPT_MATRIX_HELPER") == "1" {
		ctx := context.Background()
		stage := os.Getenv("YOLOMUX_TRANSCRIPT_MATRIX_STAGE")
		source := TranscriptSource{Path: os.Getenv("YOLOMUX_TRANSCRIPT_MATRIX_SOURCE"), Provider: "claude", SourceID: "claude", ThreadID: "thread"}
		if stage == "before_source_read" {
			fmt.Fprintln(os.Stdout, stage)
			os.Exit(0)
		}
		scan, err := ScanTranscript(source, &TranscriptScannerState{}, TranscriptScanOptions{})
		if err != nil || scan.Receipt == nil || len(scan.Atoms) != 2 {
			fmt.Fprintln(os.Stdout, "scan failed:", err)
			os.Exit(2)
		}
		if stage == "after_source_read" {
			fmt.Fprintln(os.Stdout, stage)
			os.Exit(0)
		}
		cursor := NewTranscriptCursorStore(os.Getenv("YOLOMUX_TRANSCRIPT_MATRIX_CURSOR"))
		if err := cursor.Prepare(source, scan.Receipt.before, scan.Receipt.after); err != nil {
			fmt.Fprintln(os.Stdout, "cursor prepare failed:", err)
			os.Exit(3)
		}
		if stage == "after_cursor_prepare" {
			fmt.Fprintln(os.Stdout, stage)
			os.Exit(0)
		}
		observations := make([]Observation, 0, len(scan.Atoms))
		for _, atom := range scan.Atoms {
			observations = append(observations, Observation{
				EventID: usageFactObservationID(atom), Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
				EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload,
			})
		}
		writer, err := OpenWriter(ctx, os.Getenv("YOLOMUX_TRANSCRIPT_MATRIX_DATABASE"))
		if err != nil {
			fmt.Fprintln(os.Stdout, "writer open failed:", err)
			os.Exit(4)
		}
		if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: observations, UsageAtoms: scan.Atoms}); err != nil {
			fmt.Fprintln(os.Stdout, "stats append failed:", err)
			os.Exit(5)
		}
		// os.Exit intentionally skips writer close and cursor commit to model process loss.
		fmt.Fprintln(os.Stdout, "after_stats_append")
		os.Exit(0)
	}

	for _, stage := range []string{"before_source_read", "after_source_read", "after_cursor_prepare", "after_stats_append"} {
		t.Run(stage, func(t *testing.T) {
			root := t.TempDir()
			transcriptPath := filepath.Join(root, "claude.jsonl")
			cursorPath := filepath.Join(root, "transcript-cursors.json")
			databasePath := filepath.Join(root, "stats.sqlite3")
			writeJSONL(t, transcriptPath, usageClaude(1, "matrix-crash", 10, 2))
			command := exec.Command(os.Args[0], "-test.run="+helperTest)
			command.Env = testsupport.SyntheticEnvironment(
				"YOLOMUX_TRANSCRIPT_MATRIX_HELPER=1",
				"YOLOMUX_TRANSCRIPT_MATRIX_STAGE="+stage,
				"YOLOMUX_TRANSCRIPT_MATRIX_SOURCE="+transcriptPath,
				"YOLOMUX_TRANSCRIPT_MATRIX_CURSOR="+cursorPath,
				"YOLOMUX_TRANSCRIPT_MATRIX_DATABASE="+databasePath,
			)
			output, err := command.CombinedOutput()
			if err != nil {
				t.Fatalf("synthetic crash helper failed at %s: %v\n%s", stage, err, output)
			}
			if !bytes.Contains(output, []byte(stage+"\n")) {
				t.Fatalf("synthetic crash helper did not reach %s: %s", stage, output)
			}

			source := TranscriptSource{Path: transcriptPath, Provider: "claude", SourceID: "claude", ThreadID: "thread"}
			cursor := NewTranscriptCursorStore(cursorPath)
			state, err := cursor.State(source)
			if err != nil || state.Offset != 0 {
				t.Fatalf("cursor after restart at %s = %+v, err=%v; want unadvanced cursor", stage, state, err)
			}
			scan, err := ScanTranscript(source, &state, TranscriptScanOptions{})
			if err != nil || scan.Receipt == nil || len(scan.Atoms) != 2 {
				t.Fatalf("restart replay at %s = atoms:%d receipt:%t err:%v", stage, len(scan.Atoms), scan.Receipt != nil, err)
			}
			observations := make([]Observation, 0, len(scan.Atoms))
			for _, atom := range scan.Atoms {
				observations = append(observations, Observation{
					EventID: usageFactObservationID(atom), Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
					EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload,
				})
			}
			writer, err := OpenWriter(context.Background(), databasePath)
			if err != nil {
				t.Fatal(err)
			}
			result, err := writer.AppendBatch(context.Background(), AppendBatch{Observations: observations, UsageAtoms: scan.Atoms})
			if err != nil {
				_ = writer.Close()
				t.Fatal(err)
			}
			wantAccepted, wantDuplicates := 2, 0
			if stage == "after_stats_append" {
				wantAccepted, wantDuplicates = 0, 2
			}
			if result.UsageAccepted != wantAccepted || result.UsageDuplicate != wantDuplicates || result.ObservationsAccepted != wantAccepted || result.ObservationsDuplicate != wantDuplicates {
				_ = writer.Close()
				t.Fatalf("recovery append at %s = %+v, want accepted=%d duplicate=%d", stage, result, wantAccepted, wantDuplicates)
			}
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			if err := cursor.Prepare(source, scan.Receipt.before, scan.Receipt.after); err != nil {
				t.Fatal(err)
			}
			if err := cursor.Commit(source, scan.Receipt.after); err != nil {
				t.Fatal(err)
			}
			committed, err := NewTranscriptCursorStore(cursorPath).State(source)
			if err != nil || committed.Offset != scan.Receipt.after.Offset {
				t.Fatalf("recovered cursor at %s = %+v, err=%v", stage, committed, err)
			}
			reader, err := OpenReadOnly(context.Background(), databasePath)
			if err != nil {
				t.Fatal(err)
			}
			usage, readErr := reader.ReadUsageAtoms(context.Background(), 0, 10, MaxFactReadLimit)
			closeErr := reader.Close()
			if readErr != nil || closeErr != nil || len(usage) != 2 {
				t.Fatalf("durable facts at %s = %d, readErr=%v closeErr=%v; want exactly 2", stage, len(usage), readErr, closeErr)
			}
		})
	}
}

func TestOpenWriterTruncatesInheritedWALAfterProcessExit(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "stats.sqlite3")
	command := exec.Command(os.Args[0], "-test.run=^TestWriterWALCrashHelper$")
	command.Env = testsupport.SyntheticEnvironment(
		"YOLOMUX_WRITER_WAL_CRASH_HELPER=1",
		"YOLOMUX_WRITER_WAL_CRASH_DATABASE="+databasePath,
	)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("synthetic writer crash helper failed: %v\n%s", err, output)
	}
	walPath := databasePath + "-wal"
	before, err := os.Stat(walPath)
	if err != nil {
		t.Fatalf("synthetic crash did not leave its WAL: %v\n%s", err, output)
	}
	if before.Size() <= WALAllocationCeilingBytes {
		t.Fatalf("synthetic crash WAL is %d bytes, want more than %d\n%s", before.Size(), WALAllocationCeilingBytes, output)
	}

	writer, err := OpenWriter(context.Background(), databasePath)
	if err != nil {
		t.Fatalf("replacement writer could not recover the synthetic WAL: %v", err)
	}
	defer writer.Close()
	var limit int64
	if err := writer.db.QueryRowContext(context.Background(), "PRAGMA journal_size_limit").Scan(&limit); err != nil {
		t.Fatal(err)
	}
	if limit != WALAllocationCeilingBytes {
		t.Fatalf("journal_size_limit = %d, want %d", limit, WALAllocationCeilingBytes)
	}
	var retained int
	if err := writer.db.QueryRowContext(context.Background(), "SELECT count(*) FROM observations WHERE event_id LIKE 'wal-crash-%'").Scan(&retained); err != nil {
		t.Fatal(err)
	}
	if retained != 96 {
		t.Fatalf("facts recovered from synthetic WAL = %d, want 96", retained)
	}
	if after, err := os.Stat(walPath); err == nil && after.Size() > WALAllocationCeilingBytes {
		t.Fatalf("replacement writer left WAL at %d bytes, above %d-byte ceiling", after.Size(), WALAllocationCeilingBytes)
	} else if err != nil && !os.IsNotExist(err) {
		t.Fatalf("stat truncated WAL: %v", err)
	}
}

func TestWriterWALCrashHelper(t *testing.T) {
	if os.Getenv("YOLOMUX_WRITER_WAL_CRASH_HELPER") != "1" {
		return
	}
	databasePath := os.Getenv("YOLOMUX_WRITER_WAL_CRASH_DATABASE")
	writer, err := OpenWriter(context.Background(), databasePath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "open synthetic WAL writer:", err)
		os.Exit(2)
	}
	reader, err := OpenReadOnly(context.Background(), databasePath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "open synthetic WAL reader:", err)
		os.Exit(3)
	}
	snapshot, _, err := reader.beginReadSnapshot(context.Background())
	if err != nil {
		fmt.Fprintln(os.Stderr, "begin synthetic WAL reader snapshot:", err)
		os.Exit(4)
	}
	if _, err := snapshot.ReadObservations(context.Background(), 0, 20, 1); err != nil {
		fmt.Fprintln(os.Stderr, "pin synthetic WAL reader snapshot:", err)
		os.Exit(5)
	}
	padding := strings.Repeat("w", 128*1024)
	observations := make([]Observation, 96)
	for index := range observations {
		observations[index] = Observation{
			EventID: fmt.Sprintf("wal-crash-%03d", index), Family: "synthetic_wal", SourceID: "synthetic",
			ObservedAt: 10, EpochID: "synthetic", OwnerGeneration: 1,
			Payload: map[string]any{"padding": padding},
		}
	}
	if _, err := writer.AppendBatch(context.Background(), AppendBatch{Observations: observations}); err != nil {
		fmt.Fprintln(os.Stderr, "append synthetic WAL fixture:", err)
		os.Exit(6)
	}
	info, err := os.Stat(databasePath + "-wal")
	if err != nil || info.Size() <= WALAllocationCeilingBytes {
		fmt.Fprintf(os.Stderr, "synthetic WAL size=%v err=%v, want more than %d bytes\n", info, err, WALAllocationCeilingBytes)
		os.Exit(7)
	}
	fmt.Fprintf(os.Stdout, "synthetic WAL bytes before process exit: %d\n", info.Size())
	// Deliberately bypass database and reader close so the parent observes the
	// same inherited WAL left by process death, using only this synthetic DB.
	os.Exit(0)
}

func TestWriterPruneProcessCrashBeforeCommitRecoversAndRetries(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	databasePath := filepath.Join(root, "stats.sqlite3")
	pruneAt := float64(RetentionSeconds + 10_000)
	writer, err := OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = writer.Close() })
	endedAt := pruneAt - 1
	if _, err := writer.AppendBatch(ctx, AppendBatch{
		Observations: []Observation{
			{EventID: "prune-crash-old", Family: "cpu", SourceID: "synthetic", ObservedAt: 1, EpochID: "old", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 1}},
			{EventID: "prune-crash-new", Family: "cpu", SourceID: "synthetic", ObservedAt: pruneAt - 10, EpochID: "new", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 2}},
		},
		CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "synthetic", EpochID: "old", StartedAt: 1, EndedAt: &endedAt, NativeCadenceSeconds: 10, OwnerGeneration: 1}},
		UsageAtoms: []UsageAtom{
			{EventID: "prune-crash-old-usage", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 1, Payload: map[string]any{"quantity": 1.0, "provider": "synthetic", "model": "fixture"}},
			{EventID: "prune-crash-new-usage", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: pruneAt - 10, Payload: map[string]any{"quantity": 2.0, "provider": "synthetic", "model": "fixture"}},
		},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"synthetic":true}`, Complete: true,
	}}, writer.SourceGeneration(), 2, true); err != nil {
		t.Fatalf("publish synthetic pre-prune ring cell: %v", err)
	}
	priorGeneration := writer.SourceGeneration()
	if err := writer.recordPrunedAt(123); err != nil {
		t.Fatal(err)
	}
	priorInvalidations, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	command := exec.Command(os.Args[0], "-test.run=^TestWriterPruneCrashHelper$")
	command.Env = testsupport.SyntheticEnvironment(
		"YOLOMUX_PRUNE_CRASH_HELPER=1",
		"YOLOMUX_PRUNE_CRASH_DATABASE="+databasePath,
		"YOLOMUX_PRUNE_CRASH_AT="+fmt.Sprint(pruneAt),
	)
	killWriterChildAtCommit(t, command, "synthetic-prune-before-commit\n", "prune")

	writer, err = OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatalf("recover synthetic stats DB after prune crash: %v", err)
	}
	t.Cleanup(func() { _ = writer.Close() })
	defer writer.Close()
	assertPruneCounts := func(wantObservations, wantUsage int) {
		t.Helper()
		var observations, usage int
		if err := writer.db.QueryRowContext(ctx, "SELECT count(*) FROM observations").Scan(&observations); err != nil {
			t.Fatal(err)
		}
		if err := writer.db.QueryRowContext(ctx, "SELECT count(*) FROM usage_atoms").Scan(&usage); err != nil {
			t.Fatal(err)
		}
		if observations != wantObservations || usage != wantUsage {
			t.Fatalf("synthetic DB rows = observations:%d usage:%d, want observations:%d usage:%d", observations, usage, wantObservations, wantUsage)
		}
	}
	assertPruneCounts(2, 2)
	if writer.SourceGeneration() != priorGeneration || writer.LastPrunedAt() != 123 {
		t.Fatalf("crashed prune advanced durable completion: generation=%d want=%d last_pruned=%v", writer.SourceGeneration(), priorGeneration, writer.LastPrunedAt())
	}
	var coverageStart float64
	var coverageEnd sql.NullFloat64
	if err := writer.db.QueryRowContext(ctx, "SELECT started_at, ended_at FROM coverage_epochs WHERE family = 'cpu' AND source_id = 'synthetic' AND epoch_id = 'old'").Scan(&coverageStart, &coverageEnd); err != nil {
		t.Fatalf("read synthetic coverage after prune crash: %v", err)
	}
	if coverageStart != 1 || !coverageEnd.Valid || coverageEnd.Float64 != endedAt {
		t.Fatalf("crashed prune changed coverage epoch: started=%v ended=%+v, want started=1 ended=%v", coverageStart, coverageEnd, endedAt)
	}
	afterCrashInvalidations, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(afterCrashInvalidations, priorInvalidations) {
		t.Fatalf("crashed prune changed pending ring invalidations: before=%+v after=%+v", priorInvalidations, afterCrashInvalidations)
	}

	result, err := writer.Prune(ctx, pruneAt)
	if err != nil {
		t.Fatalf("retry prune after process crash: %v", err)
	}
	assertPruneCounts(1, 1)
	if result.Observations != 1 || result.UsageAtoms != 1 || result.Generation != priorGeneration+1 || writer.SourceGeneration() != priorGeneration+1 || writer.LastPrunedAt() != pruneAt {
		t.Fatalf("retried prune result=%+v generation=%d last_pruned=%v", result, writer.SourceGeneration(), writer.LastPrunedAt())
	}
	coverageStart = 0
	coverageEnd = sql.NullFloat64{}
	if err := writer.db.QueryRowContext(ctx, "SELECT started_at, ended_at FROM coverage_epochs WHERE family = 'cpu' AND source_id = 'synthetic' AND epoch_id = 'old'").Scan(&coverageStart, &coverageEnd); err != nil {
		t.Fatalf("read synthetic coverage after prune retry: %v", err)
	}
	if coverageStart != pruneAt-float64(RetentionSeconds) || !coverageEnd.Valid || coverageEnd.Float64 != endedAt || result.CoverageClipped != 1 {
		t.Fatalf("prune retry coverage = started:%v ended:%+v clipped:%d, want started:%v ended:%v clipped:1", coverageStart, coverageEnd, result.CoverageClipped, pruneAt-float64(RetentionSeconds), endedAt)
	}
	pendingAfterPrune, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	foundPrunedCell := false
	for _, cell := range pendingAfterPrune {
		if cell.ResolutionSeconds == 10 && cell.BucketStart == 0 && cell.SourceGeneration == priorGeneration+1 {
			foundPrunedCell = true
		}
	}
	if !foundPrunedCell {
		t.Fatalf("prune retry did not durably invalidate the expired synthetic ring cell: %+v", pendingAfterPrune)
	}
}

func TestWriterRingPublicationCrashBeforeCommitRecoversAndReplays(t *testing.T) {
	ctx := context.Background()
	databasePath := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = writer.Close() })
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "ring-crash-seed", Family: "cpu", SourceID: "synthetic", ObservedAt: 15,
		EpochID: "synthetic", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 10},
	}}}); err != nil {
		t.Fatal(err)
	}
	if writer.SourceGeneration() != 1 {
		t.Fatalf("seed source generation = %d, want 1", writer.SourceGeneration())
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10, BucketStart: 10, BucketJSON: `{"value":1}`, Complete: true,
	}}, 1, 20, true); err != nil {
		t.Fatalf("publish synthetic baseline ring: %v", err)
	}
	var priorStoredPayload string
	if err := writer.db.QueryRowContext(ctx, "SELECT bucket_json FROM aggregate_ring_slots WHERE resolution_seconds=10 AND bucket_start=10").Scan(&priorStoredPayload); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "ring-crash-mutation", Family: "cpu", SourceID: "synthetic", ObservedAt: 16,
		EpochID: "synthetic", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 20},
	}}}); err != nil {
		t.Fatal(err)
	}
	if writer.SourceGeneration() != 2 {
		t.Fatalf("mutated source generation = %d, want 2", writer.SourceGeneration())
	}
	pendingBeforeCrash, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	var priorReplayThrough float64
	var priorReplayGeneration int64
	if err := writer.db.QueryRowContext(ctx, "SELECT folded_through_observed_at,folded_source_generation FROM ring_replay_cursor WHERE resolution_seconds=10").Scan(&priorReplayThrough, &priorReplayGeneration); err != nil {
		t.Fatal(err)
	}
	priorRingGeneration, priorPublishedAt, err := writer.RingPublicationState(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	command := exec.Command(os.Args[0], "-test.run=^TestWriterRingPublicationCrashHelper$")
	command.Env = testsupport.SyntheticEnvironment(
		"YOLOMUX_RING_CRASH_HELPER=1",
		"YOLOMUX_RING_CRASH_DATABASE="+databasePath,
	)
	killWriterChildAtCommit(t, command, "synthetic-ring-before-commit\n", "ring publication")

	writer, err = OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatalf("recover synthetic stats DB after ring publication crash: %v", err)
	}
	t.Cleanup(func() { _ = writer.Close() })
	defer writer.Close()
	assertOldRing := func() {
		t.Helper()
		ringGeneration, publishedAt, err := writer.RingPublicationState(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if ringGeneration != priorRingGeneration || publishedAt != priorPublishedAt {
			t.Fatalf("crashed ring publication advanced publication: generation=%d published=%v, want generation=%d published=%v", ringGeneration, publishedAt, priorRingGeneration, priorPublishedAt)
		}
		window, err := writer.ReadRingWindowPayloads(ctx, 10, 10, 20)
		if err != nil || len(window.Rows) != 0 || len(window.PendingInvalidations) == 0 {
			t.Fatalf("ring after publication crash exposed a contradicted bucket: window=%+v err=%v", window, err)
		}
		// The prior bytes survive rollback, but the committed source mutation
		// already invalidated them. Request readers must hide them until replay.
		var storedPayload string
		if err := writer.db.QueryRowContext(ctx, "SELECT bucket_json FROM aggregate_ring_slots WHERE resolution_seconds=10 AND bucket_start=10").Scan(&storedPayload); err != nil {
			t.Fatal(err)
		}
		if storedPayload != priorStoredPayload {
			t.Fatalf("crashed publication changed stored bucket bytes: got %q, want %q", storedPayload, priorStoredPayload)
		}
		var replayThrough float64
		var replayGeneration int64
		if err := writer.db.QueryRowContext(ctx, "SELECT folded_through_observed_at,folded_source_generation FROM ring_replay_cursor WHERE resolution_seconds=10").Scan(&replayThrough, &replayGeneration); err != nil {
			t.Fatal(err)
		}
		if replayThrough != priorReplayThrough || replayGeneration != priorReplayGeneration {
			t.Fatalf("ring replay cursor after crash = through:%v generation:%d, want through:%v generation:%d", replayThrough, replayGeneration, priorReplayThrough, priorReplayGeneration)
		}
		pending, err := writer.PendingInvalidationCells(ctx)
		if err != nil || !reflect.DeepEqual(pending, pendingBeforeCrash) {
			t.Fatalf("ring invalidations after publication crash = %+v err=%v, want %+v", pending, err, pendingBeforeCrash)
		}
	}
	assertOldRing()

	publication, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10, BucketStart: 10, BucketJSON: `{"value":2}`, Complete: true,
	}}, 2, 21, true)
	if err != nil {
		t.Fatalf("replay synthetic ring publication after crash: %v", err)
	}
	if publication.RingGeneration != priorRingGeneration+1 || publication.SourceGeneration != 2 || publication.PublishedAt != 21 {
		t.Fatalf("replayed ring publication = %+v, want one generation advance at source 2", publication)
	}
	window, err := writer.ReadRingWindowPayloads(ctx, 10, 10, 20)
	if err != nil || len(window.Rows) != 1 || window.Rows[0].BucketJSON != `{"value":2}` {
		t.Fatalf("ring after publication replay = %+v err=%v", window.Rows, err)
	}
	var replayThrough float64
	var replayGeneration int64
	if err := writer.db.QueryRowContext(ctx, "SELECT folded_through_observed_at,folded_source_generation FROM ring_replay_cursor WHERE resolution_seconds=10").Scan(&replayThrough, &replayGeneration); err != nil {
		t.Fatal(err)
	}
	if replayThrough != 20 || replayGeneration != 2 {
		t.Fatalf("ring replay cursor after retry = through:%v generation:%d, want through:20 generation:2", replayThrough, replayGeneration)
	}
	pendingAfterReplay, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, cell := range pendingAfterReplay {
		if cell.ResolutionSeconds == 10 && cell.BucketStart == 10 {
			t.Fatalf("replayed ring cell remains pending: %+v", cell)
		}
	}
}

func TestWriterRingPublicationCrashHelper(t *testing.T) {
	if os.Getenv("YOLOMUX_RING_CRASH_HELPER") != "1" {
		return
	}
	writer, err := OpenWriter(context.Background(), os.Getenv("YOLOMUX_RING_CRASH_DATABASE"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "open synthetic ring writer:", err)
		os.Exit(2)
	}
	connection, err := writer.db.Conn(context.Background())
	if err != nil {
		fmt.Fprintln(os.Stderr, "reserve synthetic ring connection:", err)
		os.Exit(3)
	}
	err = connection.Raw(func(driverConnection any) error {
		sqliteConnection, ok := driverConnection.(*sqlite3.SQLiteConn)
		if !ok {
			return fmt.Errorf("synthetic ring connection type = %T", driverConnection)
		}
		sqliteConnection.RegisterCommitHook(func() int {
			_, _ = os.Stdout.Write([]byte("synthetic-ring-before-commit\n"))
			select {}
		})
		return nil
	})
	_ = connection.Close()
	if err != nil {
		fmt.Fprintln(os.Stderr, "install synthetic ring commit hook:", err)
		os.Exit(4)
	}
	if _, err := writer.PublishRingBuckets(context.Background(), []RingBucketWrite{{
		ResolutionSeconds: 10, BucketStart: 10, BucketJSON: `{"value":2}`, Complete: true,
	}}, 2, 21, true); err != nil {
		fmt.Fprintln(os.Stderr, "run synthetic ring publication:", err)
		os.Exit(5)
	}
	fmt.Fprintln(os.Stderr, "synthetic ring publication returned without reaching its commit hook")
	os.Exit(6)
}

func TestWriterPruneCrashHelper(t *testing.T) {
	if os.Getenv("YOLOMUX_PRUNE_CRASH_HELPER") != "1" {
		return
	}
	databasePath := os.Getenv("YOLOMUX_PRUNE_CRASH_DATABASE")
	pruneAt, err := strconv.ParseFloat(os.Getenv("YOLOMUX_PRUNE_CRASH_AT"), 64)
	if err != nil {
		fmt.Fprintln(os.Stderr, "decode synthetic prune time:", err)
		os.Exit(2)
	}
	writer, err := OpenWriter(context.Background(), databasePath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "open synthetic prune writer:", err)
		os.Exit(3)
	}
	connection, err := writer.db.Conn(context.Background())
	if err != nil {
		fmt.Fprintln(os.Stderr, "reserve synthetic prune connection:", err)
		os.Exit(4)
	}
	err = connection.Raw(func(driverConnection any) error {
		sqliteConnection, ok := driverConnection.(*sqlite3.SQLiteConn)
		if !ok {
			return fmt.Errorf("synthetic prune connection type = %T", driverConnection)
		}
		sqliteConnection.RegisterCommitHook(func() int {
			_, _ = os.Stdout.Write([]byte("synthetic-prune-before-commit\n"))
			select {}
		})
		return nil
	})
	_ = connection.Close()
	if err != nil {
		fmt.Fprintln(os.Stderr, "install synthetic prune commit hook:", err)
		os.Exit(5)
	}
	if _, err := writer.Prune(context.Background(), pruneAt); err != nil {
		fmt.Fprintln(os.Stderr, "run synthetic prune:", err)
		os.Exit(6)
	}
	fmt.Fprintln(os.Stderr, "synthetic prune returned without reaching its commit hook")
	os.Exit(7)
}

func killWriterChildAtCommit(t *testing.T, command *exec.Cmd, wantSignal, operation string) {
	t.Helper()
	stdout, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	command.Stderr = &stderr
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	lineResult := make(chan string, 1)
	go func() {
		line, _ := bufio.NewReader(stdout).ReadString('\n')
		lineResult <- line
	}()
	select {
	case line := <-lineResult:
		if line != wantSignal {
			_ = command.Process.Kill()
			_ = command.Wait()
			t.Fatalf("%s crash helper did not reach the transaction commit hook: stdout=%q stderr=%s", operation, line, stderr.String())
		}
	case <-time.After(10 * time.Second):
		_ = command.Process.Kill()
		_ = command.Wait()
		t.Fatalf("%s crash helper did not reach the transaction commit hook: stderr=%s", operation, stderr.String())
	}
	if err := command.Process.Kill(); err != nil {
		_ = command.Wait()
		t.Fatalf("kill %s helper at pre-commit hook: %v", operation, err)
	}
	if err := command.Wait(); err == nil {
		t.Fatalf("%s crash helper exited successfully after parent killed it", operation)
	}
}

func copyIntegrationFixture(t *testing.T, relative, destinationName string) string {
	t.Helper()
	source := testsupport.RepoPath(t, filepath.Join("tests", "fixtures", "current_model_usage", destinationName))
	destination := filepath.Join(t.TempDir(), relative)
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		t.Fatal(err)
	}
	in, err := os.Open(source)
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	out, err := os.OpenFile(destination, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
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

func integrationBatch(claude, codex []UsageAtom, parts []OpenCodeStepFinishPart) AppendBatch {
	atoms := append(append([]UsageAtom(nil), claude...), codex...)
	for _, part := range parts {
		input, cached, output := float64(part.Tokens.Input), float64(part.Tokens.CacheRead), float64(part.Tokens.Output)
		more, _ := NormalizeCodexUsage(CodexUsageRecord{ObservedAt: float64(part.CompletedAt), InputTokens: &input, CachedInputTokens: &cached, OutputTokens: &output, Metadata: UsageMetadata{Provider: part.Provider, Model: part.Model, ThreadID: part.SessionID}})
		atoms = append(atoms, more...)
	}
	sort.Slice(atoms, func(i, j int) bool {
		return atoms[i].ObservedAt < atoms[j].ObservedAt || (atoms[i].ObservedAt == atoms[j].ObservedAt && atoms[i].EventID < atoms[j].EventID)
	})
	observations := make([]Observation, 0, len(atoms))
	for _, atom := range atoms {
		observations = append(observations, Observation{EventID: usageFactObservationID(atom), Family: "usage", SourceID: atom.Payload["provider"].(string), ObservedAt: atom.ObservedAt, EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload})
	}
	return AppendBatch{Observations: observations, UsageAtoms: atoms}
}

func usageFactObservationID(atom UsageAtom) string {
	identity := fmt.Sprintf("%s\x00%s\x00%s\x00%s\x00%s", atom.EventID, atom.Direction, atom.Modality, atom.CacheRole, atom.Unit)
	digest := sha256.Sum256([]byte(identity))
	return "fact:" + hex.EncodeToString(digest[:])
}

func atomQuantity(atoms []UsageAtom, direction, cacheRole string) float64 {
	var total float64
	for _, atom := range atoms {
		if atom.Direction == direction && atom.CacheRole == cacheRole {
			total += atom.Payload["quantity"].(float64)
		}
	}
	return total
}

func totalQuantity(atoms []UsageAtom, direction string) float64 {
	var total float64
	for _, atom := range atoms {
		if atom.Direction == direction {
			total += atom.Payload["quantity"].(float64)
		}
	}
	return total
}

func createIntegrationOpenCodeDB(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`INSERT INTO session VALUES ('ses-integration', '/synthetic/workspace', 'build', '{"providerID":"openai","id":"gpt-integration"}', 900, 2500, 0)`,
		`INSERT INTO message VALUES ('msg-integration', 'ses-integration', 1000, 2500, '{"role":"assistant","providerID":"openai","modelID":"gpt-integration"}')`,
		`INSERT INTO part VALUES ('part-integration', 'msg-integration', 'ses-integration', 1100, 2000, '{"type":"step-finish","tokens":{"total":10,"input":3,"output":5,"reasoning":0,"cache":{"read":2,"write":0}}}')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
}
