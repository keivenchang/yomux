package stats

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
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

func TestTranscriptStatsCheckpointTransactionSurvivesCrashBoundaries(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	sourcePath := filepath.Join(root, "claude.jsonl")
	databasePath := filepath.Join(root, "stats-v9.sqlite3")
	source := TranscriptSource{Path: sourcePath, Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	writeJSONL(t, sourcePath, usageClaude(1, "first", 10, 2))
	writer, err := OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(writer, nil, AgentTokenCollectorConfig{TranscriptCursorPath: filepath.Join(root, "cursor.json")})
	initial, err := collector.transcriptCursor.State(source)
	if err != nil {
		t.Fatal(err)
	}
	state := cloneTranscriptState(initial)
	first, err := ScanTranscript(source, &state, TranscriptScanOptions{})
	if err != nil || first.Receipt == nil || len(first.Atoms) != 2 {
		t.Fatalf("initial scan=%+v err=%v", first, err)
	}
	checkpoint, err := encodeTranscriptCheckpoint(transcriptCursorKey(source), first.Receipt.after, initial.checkpointRevision)
	if err != nil {
		t.Fatal(err)
	}
	firstBatch := AppendBatch{UsageAtoms: first.Atoms}
	for _, atom := range first.Atoms {
		firstBatch.Observations = append(firstBatch.Observations, Observation{EventID: usageFactObservationID(atom), Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt, EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload})
	}
	if _, err := writer.AppendWithCheckpoints(ctx, firstBatch, []Checkpoint{checkpoint}); err != nil {
		t.Fatal(err)
	}
	first.Receipt.Commit()
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	// A crash before the next transaction leaves both facts and parser state at the last commit.
	writer, err = OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	collector = NewAgentTokenCollector(writer, nil, AgentTokenCollectorConfig{TranscriptCursorPath: filepath.Join(root, "cursor.json")})
	committed, err := collector.transcriptCursor.State(source)
	if err != nil || committed.Offset != first.Receipt.after.Offset {
		t.Fatalf("restart state=%+v err=%v", committed, err)
	}
	appendJSONL(t, sourcePath, usageClaude(2, "second", 15, 3), false)
	scanState := cloneTranscriptState(committed)
	second, err := ScanTranscript(source, &scanState, TranscriptScanOptions{})
	if err != nil || second.Receipt == nil || len(second.Atoms) != 2 {
		t.Fatalf("second scan=%+v err=%v", second, err)
	}
	batch := AppendBatch{UsageAtoms: second.Atoms}
	observations := make([]Observation, 0, len(second.Atoms))
	for _, atom := range second.Atoms {
		observations = append(observations, Observation{EventID: usageFactObservationID(atom), Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt, EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload})
	}
	batch.Observations = observations
	next, err := encodeTranscriptCheckpoint(transcriptCursorKey(source), second.Receipt.after, committed.checkpointRevision)
	if err != nil {
		t.Fatal(err)
	}
	// A failed fact append cannot publish its accompanying cursor.
	conflicting := batch
	conflicting.Observations = append([]Observation(nil), batch.Observations...)
	conflicting.Observations[0].EventID = usageFactObservationID(first.Atoms[0])
	conflicting.Observations[0].Payload = map[string]any{"changed": true}
	if _, err := writer.AppendWithCheckpoints(ctx, conflicting, []Checkpoint{next}); err == nil {
		t.Fatal("conflicting usage append succeeded")
	}
	afterFailure, err := collector.transcriptCursor.State(source)
	if err != nil || afterFailure.Offset != committed.Offset {
		t.Fatalf("failed append advanced cursor: %+v err=%v", afterFailure, err)
	}
	result, err := writer.AppendWithCheckpoints(ctx, batch, []Checkpoint{next})
	if err != nil || result.UsageAccepted != 2 {
		t.Fatalf("atomic append=%+v err=%v", result, err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	// Process loss after SQLite commits leaves both the facts and byte offset durable.
	writer, err = OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector = NewAgentTokenCollector(writer, nil, AgentTokenCollectorConfig{TranscriptCursorPath: filepath.Join(root, "cursor.json")})
	recovered, err := collector.transcriptCursor.State(source)
	if err != nil || recovered.Offset != second.Receipt.after.Offset {
		t.Fatalf("recovered state=%+v err=%v", recovered, err)
	}
	unchanged, err := ScanTranscript(source, &recovered, TranscriptScanOptions{})
	if err != nil || len(unchanged.Atoms) != 0 {
		t.Fatalf("unchanged recovery scan=%+v err=%v", unchanged, err)
	}
	result, err = writer.AppendBatch(ctx, batch)
	if err != nil || result.UsageAccepted != 0 || result.UsageDuplicate != 2 {
		t.Fatalf("duplicate fact replay=%+v err=%v", result, err)
	}
	reader, err := OpenReadOnly(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	usage, err := reader.ReadUsageAtoms(ctx, 0, 100, 10)
	if err != nil || len(usage) != 4 {
		t.Fatalf("durable usage=%d err=%v", len(usage), err)
	}
}

func TestBudgetedTranscriptCheckpointProgressAcrossCollectorRestarts(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	path := filepath.Join(root, "claude.jsonl")
	dbPath := filepath.Join(root, "stats-v9.sqlite3")
	writeJSONL(t, path, usageClaude(1, "first", 10, 2), usageClaude(2, "second", 11, 3), usageClaude(3, "third", 12, 4))
	source := TranscriptSource{Path: path, Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	config := AgentTokenCollectorConfig{TranscriptCursorPath: filepath.Join(root, "cursors.json"), TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
		return []TranscriptSource{source}, nil
	}, TranscriptMaxBytesPerCycle: 1 << 20, TranscriptMaxRecordsPerCycle: 1, EpochID: "synthetic-transcript", EpochStartedAt: 1, OwnerGeneration: 1}
	eventID := func(record string) string { return "claude:" + path + ":" + record }
	previousOffset := int64(0)
	for cycle, record := range []string{"first", "second", "third"} {
		writer, err := OpenWriter(ctx, dbPath)
		if err != nil {
			t.Fatal(err)
		}
		collector := NewAgentTokenCollector(writer, nil, config)
		batch, receipts, err := collector.collectTranscripts(ctx, float64(10+cycle), 10, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatal(err)
		}
		if len(batch.UsageAtoms) != 2 || len(receipts) == 0 {
			t.Fatalf("cycle %d atoms=%d receipts=%d", cycle, len(batch.UsageAtoms), len(receipts))
		}
		for _, atom := range batch.UsageAtoms {
			if atom.EventID != eventID(record) {
				t.Fatalf("cycle %d consumed %q want %q", cycle, atom.EventID, eventID(record))
			}
		}
		ok, err := collector.appendCollected(ctx, batch, receipts)
		if err != nil || !ok {
			t.Fatalf("cycle %d append=%t err=%v", cycle, ok, err)
		}
		state, err := collector.transcriptCursor.State(source)
		if err != nil {
			t.Fatal(err)
		}
		if state.Offset <= previousOffset {
			t.Fatalf("cycle %d offset %d did not advance past %d", cycle, state.Offset, previousOffset)
		}
		previousOffset = state.Offset
		if err := writer.Close(); err != nil {
			t.Fatal(err)
		}
	}
	if size := transcriptFixtureSize(t, path); previousOffset != size {
		t.Fatalf("final byte offset=%d want=%d", previousOffset, size)
	}
	writer, err := OpenWriter(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, nil, config)
	unchanged, receipts, err := collector.collectTranscripts(ctx, 20, 10, nil, nil, AppendBatch{}, nil)
	if err != nil || len(unchanged.UsageAtoms) != 0 {
		t.Fatalf("unchanged scan=%+v receipts=%d err=%v", unchanged, len(receipts), err)
	}
	for _, receipt := range receipts {
		for _, checkpoint := range receipt.checkpoints {
			if checkpoint.Kind == "transcript" {
				t.Fatal("unchanged transcript produced a source checkpoint")
			}
		}
	}
	if ok, err := collector.appendCollected(ctx, unchanged, receipts); err != nil || !ok {
		t.Fatalf("commit unchanged scheduling checkpoint=%t err=%v", ok, err)
	}
	appendJSONL(t, path, usageClaude(4, "appended", 13, 5), false)
	batch, receipts, err := collector.collectTranscripts(ctx, 21, 10, nil, nil, AppendBatch{}, nil)
	if err != nil || len(batch.UsageAtoms) != 2 || len(receipts) == 0 {
		t.Fatalf("appended scan=%+v receipts=%d err=%v", batch, len(receipts), err)
	}
	for _, atom := range batch.UsageAtoms {
		if atom.EventID != eventID("appended") {
			t.Fatalf("post-restart append consumed %q", atom.EventID)
		}
	}
	ok, err := collector.appendCollected(ctx, batch, receipts)
	if err != nil || !ok {
		t.Fatalf("append=%t err=%v", ok, err)
	}
	reader, err := OpenReadOnly(ctx, dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	usage, err := reader.ReadUsageAtoms(ctx, 0, 100, MaxFactReadLimit)
	if err != nil || len(usage) != 8 {
		t.Fatalf("durable usage=%d err=%v want 8", len(usage), err)
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
