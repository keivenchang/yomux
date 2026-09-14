package stats

import (
	"context"
	"database/sql"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"testing"

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
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}
	t.Logf("SQLite readback: schema=%d generation=%d observations=%d usage_atoms=%d input_tokens=%g output_tokens=%g", 8, 1, len(observations), len(usage), totalQuantity(usage, "input"), totalQuantity(usage, "output"))
}

func copyIntegrationFixture(t *testing.T, relative, destinationName string) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	source := filepath.Join(filepath.Dir(thisFile), "..", "..", "tests", "fixtures", "current_model_usage", destinationName)
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
