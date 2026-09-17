package stats

import (
	"bufio"
	"context"
	"database/sql"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	"github.com/keivenchang/yolomux/internal/testsupport"

	_ "github.com/mattn/go-sqlite3"
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
			EventID: "fact:" + atom.EventID, Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
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
	restarted := NewTranscriptCursorStore(cursorPath)
	loaded, err := restarted.State(source)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Offset != 0 {
		t.Fatalf("cursor advanced before stats append: %+v", loaded)
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
			EventID: "fact:" + atom.EventID, Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
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
				EventID: "fact:" + atom.EventID, Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
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
			EventID: "fact:" + atom.EventID, Family: "usage", SourceID: "claude", ObservedAt: atom.ObservedAt,
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
		observations = append(observations, Observation{EventID: "fact:" + atom.EventID, Family: "usage", SourceID: atom.Payload["provider"].(string), ObservedAt: atom.ObservedAt, EpochID: "usage-epoch", OwnerGeneration: 1, Payload: atom.Payload})
	}
	return AppendBatch{Observations: observations, UsageAtoms: atoms}
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
