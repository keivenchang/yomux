package stats

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yolomux/internal/testsupport"
)

func writeJSONL(t *testing.T, path string, records ...map[string]any) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	encoder := json.NewEncoder(file)
	for _, record := range records {
		if err := encoder.Encode(record); err != nil {
			t.Fatal(err)
		}
	}
}

func transcriptFixtureSize(t *testing.T, path string) int64 {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Size()
}

type cancelOnErrContext struct {
	context.Context
	cancel context.CancelFunc
	limit  int
	calls  int
}

func (c *cancelOnErrContext) Err() error {
	c.calls++
	if c.calls >= c.limit {
		c.cancel()
	}
	return c.Context.Err()
}

type appendOnErrContext struct {
	context.Context
	calls      int
	appendAt   int
	appendFunc func()
}

func (c *appendOnErrContext) Err() error {
	c.calls++
	if c.calls == c.appendAt && c.appendFunc != nil {
		c.appendFunc()
	}
	return c.Context.Err()
}

func appendJSONL(t *testing.T, path string, record map[string]any, partial bool) []byte {
	t.Helper()
	raw, err := json.Marshal(record)
	if err != nil {
		t.Fatal(err)
	}
	raw = append(raw, '\n')
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if !partial {
		if _, err := file.Write(raw); err != nil {
			t.Fatal(err)
		}
		return nil
	}
	cut := len(raw) / 2
	if _, err := file.Write(raw[:cut]); err != nil {
		t.Fatal(err)
	}
	return raw[cut:]
}

func TestScanTranscriptUsesInitialFileSizeWhenSourceAppendsDuringScan(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codex.jsonl")
	first := usageCodex(1, 10, 2, 3)
	second := usageCodex(2, 20, 4, 6)
	writeJSONL(t, path, first)
	initialSize := transcriptFixtureSize(t, path)
	secondBytes, err := json.Marshal(second)
	if err != nil {
		t.Fatal(err)
	}
	secondBytes = append(secondBytes, '\n')
	ctx := &appendOnErrContext{
		Context:  context.Background(),
		appendAt: 2, // ScanTranscript's first Err check is before open; the second follows the size snapshot.
		appendFunc: func() {
			file, openErr := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
			if openErr != nil {
				t.Fatal(openErr)
			}
			if _, writeErr := file.Write(secondBytes); writeErr != nil {
				t.Fatal(writeErr)
			}
			if closeErr := file.Close(); closeErr != nil {
				t.Fatal(closeErr)
			}
		},
	}
	state := &TranscriptScannerState{}
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "synthetic"}, state, TranscriptScanOptions{
		Context: ctx, MaxBytes: 64 * 1024, MaxRecords: 10,
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.RecordsParsed != 1 || len(result.Atoms) != 3 || result.Atoms[0].ObservedAt != 1 {
		t.Fatalf("first snapshot scan parsed records=%d atoms=%d, want one record and three atoms", result.RecordsParsed, len(result.Atoms))
	}
	if result.Receipt == nil || result.Receipt.after.Offset != initialSize || result.Receipt.after.FileSize != initialSize {
		t.Fatalf("first snapshot cursor offset/file size = %+v, want initial size %d", result.Receipt, initialSize)
	}
	result.Receipt.Commit()
	if got := transcriptFixtureSize(t, path); got != initialSize+int64(len(secondBytes)) {
		t.Fatalf("appended synthetic transcript size = %d, want %d", got, initialSize+int64(len(secondBytes)))
	}

	next, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "synthetic"}, state, TranscriptScanOptions{
		MaxBytes: 64 * 1024, MaxRecords: 10,
	})
	if err != nil {
		t.Fatal(err)
	}
	if next.RecordsParsed != 1 || len(next.Atoms) != 3 || next.Atoms[0].ObservedAt != 2 {
		t.Fatalf("next scan did not consume only the appended row: records=%d atoms=%+v", next.RecordsParsed, next.Atoms)
	}
}

func TestScanTranscriptKeepsPendingRecordAtSnapshotEOFUntilNewline(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codex.jsonl")
	line, err := json.Marshal(usageCodex(1, 10, 2, 3))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, line, 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	device, inode := fileIdentity(info)
	state := &TranscriptScannerState{
		Path: path, PendingLine: append([]byte(nil), line...), FileSize: int64(len(line)),
		Device: device, Inode: inode,
	}
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "synthetic"}, state, TranscriptScanOptions{})
	if err != nil || result.RecordsParsed != 0 || len(result.Atoms) != 0 || result.Receipt == nil {
		t.Fatalf("pending final record = %+v, err=%v", result, err)
	}
	if result.BudgetExhausted {
		t.Fatal("partial row at snapshot EOF was reported as exhausted budget")
	}
	if result.Receipt.after.Offset != 0 || !bytes.Equal(result.Receipt.after.PendingLine, line) {
		t.Fatalf("pending final record cursor = %+v, want offset 0 and preserved partial row", result.Receipt.after)
	}
	result.Receipt.Commit()
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write([]byte{10}); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	completed, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "synthetic"}, state, TranscriptScanOptions{})
	if err != nil || completed.RecordsParsed != 1 || len(completed.Atoms) != 3 || completed.Receipt == nil {
		t.Fatalf("newline-completed pending row = %+v, err=%v", completed, err)
	}
}

func usageClaude(timestamp float64, id string, input, output float64) map[string]any {
	return map[string]any{"timestamp": timestamp, "type": "assistant", "message": map[string]any{
		"id": id, "model": "claude-sonnet-synthetic", "usage": map[string]any{
			"input_tokens": input, "output_tokens": output,
		},
	}}
}

func usageClaudeNestedCache(timestamp float64, id string) map[string]any {
	record := usageClaude(timestamp, id, 10, 7)
	record["message"].(map[string]any)["usage"] = map[string]any{
		"input_tokens":                10.0,
		"cache_creation_input_tokens": 99.0,
		"cache_creation": map[string]any{
			"ephemeral_5m_input_tokens": 3.0,
			"ephemeral_1h_input_tokens": 4.0,
		},
		"output_tokens": 7.0,
	}
	return record
}

func usageCodex(timestamp, input, cached, output float64) map[string]any {
	return map[string]any{"timestamp": timestamp, "type": "event_msg", "payload": map[string]any{"info": map[string]any{
		"total_token_usage": map[string]any{"input_tokens": input, "cached_input_tokens": cached, "output_tokens": output},
	}}}
}

func usageCodexTokenUsageRecord(timestamp float64, threadID string, input, cached, output, threadInput, threadCached, threadOutput float64) map[string]any {
	usage := map[string]any{
		"input_tokens": input, "cached_input_tokens": cached, "output_tokens": output,
		"reasoning_output_tokens": output, "total_tokens": input + output,
	}
	threadUsage := map[string]any{
		"input_tokens": threadInput, "cached_input_tokens": threadCached, "output_tokens": threadOutput,
		"reasoning_output_tokens": threadOutput, "total_tokens": threadInput + threadOutput,
	}
	return map[string]any{"timestamp": timestamp, "type": "token_usage_record", "payload": map[string]any{
		"thread_id": threadID, "turn_id": "turn-synthetic", "response_id": "response-synthetic",
		"usage": usage, "turn_token_usage": usage, "thread_token_usage": threadUsage,
	}}
}

func copyObservedTranscript(t *testing.T, provider, relativePath string) string {
	t.Helper()
	fixture := testsupport.RepoPath(t, filepath.Join("tests", "fixtures", "current_model_usage", "observed."+provider+".jsonl"))
	data, err := os.ReadFile(fixture)
	if err != nil {
		t.Fatal(err)
	}
	destination := filepath.Join(t.TempDir(), relativePath)
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(destination, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return destination
}

func atomQuantities(atoms []UsageAtom) map[string]float64 {
	result := make(map[string]float64, len(atoms))
	for _, atom := range atoms {
		result[atom.Direction+":"+atom.CacheRole] += atom.Payload["quantity"].(float64)
	}
	return result
}

func atomIDs(atoms []UsageAtom) []string {
	ids := make([]string, 0, len(atoms))
	for _, atom := range atoms {
		ids = append(ids, atom.EventID)
	}
	sort.Strings(ids)
	return ids
}

func TestScanTranscriptObservedFixturesFromRealisticPaths(t *testing.T) {
	claudePath := copyObservedTranscript(t, "claude", "claude/projects/-synthetic-workspace/session-observed.jsonl")
	codexPath := copyObservedTranscript(t, "codex", "codex/sessions/2026/09/02/rollout-observed.jsonl")
	claudeSource := TranscriptSource{Path: claudePath, Provider: "claude", ThreadID: "synthetic-session"}
	codexSource := TranscriptSource{Path: codexPath, Provider: "codex", ThreadID: "fallback-thread"}
	claudeState, codexState := &TranscriptScannerState{}, &TranscriptScannerState{}
	claude, err := ScanTranscript(claudeSource, claudeState, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	codex, err := ScanTranscript(codexSource, codexState, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if claude.RecordsParsed != 6 || len(claude.Atoms) != 12 || claude.Atoms[0].ObservedAt != 1788307201 {
		t.Fatalf("Claude fixture result = %+v", claude)
	}
	if got := atomQuantities(claude.Atoms); got["input:none"] != 60 || got["output:none"] != 30 {
		t.Fatalf("Claude quantities = %#v", got)
	}
	if codex.RecordsParsed != 12 || len(codex.Atoms) != 12 {
		t.Fatalf("Codex fixture result = %+v", codex)
	}
	if codex.Atoms[0].Payload["model"] != "gpt-5.4" || codex.Atoms[2].Payload["model"] != "gpt-5.4-mini" || codex.Atoms[4].Payload["model"] != "gpt-5.5" || codex.Atoms[6].Payload["model"] != "gpt-5.6-luna" {
		t.Fatalf("Codex model context = %#v", codex.Atoms)
	}
	if got := atomQuantities(codex.Atoms); got["input:none"] != 60 || got["output:none"] != 30 {
		t.Fatalf("Codex quantities = %#v", got)
	}
	claudeIDs := atomIDs(claude.Atoms)
	claude.Receipt.Commit()
	second, err := ScanTranscript(claudeSource, claudeState, TranscriptScanOptions{})
	if err != nil || second.RecordsParsed != 0 || len(atomIDs(second.Atoms)) != 0 {
		t.Fatalf("unchanged fixture scan = %+v, err = %v", second, err)
	}
	second.Receipt.Commit()
	repeat, err := ScanTranscript(claudeSource, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil || !bytes.Equal([]byte(claudeIDs[0]), []byte(atomIDs(repeat.Atoms)[0])) {
		t.Fatalf("deterministic Claude IDs = %v / %v, err = %v", claudeIDs, atomIDs(repeat.Atoms), err)
	}
}

func TestScanTranscriptCodexTokenUsageRecord(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-token-usage-record.jsonl")
	writeJSONL(t, path,
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "thread-synthetic", "model": "gpt-synthetic"}},
		usageCodexTokenUsageRecord(1, "thread-synthetic", 10, 2, 5, 10, 2, 5),
	)
	state := &TranscriptScannerState{}
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "fallback-thread"}
	result, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if got := atomQuantities(result.Atoms); !reflect.DeepEqual(got, map[string]float64{"input:none": 8, "input:read": 2, "output:none": 5}) {
		t.Fatalf("token usage record quantities = %#v, result=%+v", got, result)
	}
	if len(result.Atoms) != 3 || result.Atoms[0].Payload["thread_id"] != "thread-synthetic" || result.Atoms[0].Payload["model"] != "gpt-synthetic" {
		t.Fatalf("token usage record metadata = %#v", result.Atoms)
	}
	for _, atom := range result.Atoms {
		if atom.Payload["provider"] != "openai" || atom.Payload["execution_source"] != "codex" || atom.Payload["endpoint"] != "responses" {
			t.Fatalf("Codex Python-compatible pricing identity = %#v", atom.Payload)
		}
	}
	result.Receipt.Commit()
	appendJSONL(t, path, usageCodexTokenUsageRecord(2, "thread-synthetic", 6, 3, 4, 16, 5, 9), false)
	resumed, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if got := atomQuantities(resumed.Atoms); !reflect.DeepEqual(got, map[string]float64{"input:none": 3, "input:read": 3, "output:none": 4}) {
		t.Fatalf("token usage record incremental quantities = %#v, result=%+v", got, resumed)
	}
}

func TestCodexTranscriptAtomsResolvePythonCatalogCost(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-priced.jsonl")
	observedAt := float64(1783728000)
	writeJSONL(t, path,
		map[string]any{"type": "session_meta", "timestamp": observedAt, "payload": map[string]any{"id": "thread-priced", "model": "gpt-4.1"}},
		usageCodexTokenUsageRecord(observedAt, "thread-priced", 100, 20, 10, 100, 20, 10),
	)
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread-priced"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadRepositoryPricingCatalog(testsupport.RepoRoot(t), "")
	if err != nil {
		t.Fatal(err)
	}
	layer, err := (Materializer{Price: PricingResolver(catalog)}).Build(
		MaterializeRequest{Start: observedAt - 1, End: observedAt + 10, Resolution: 10},
		nil, nil, nil, result.Atoms,
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.Buckets) != 2 || layer.Buckets[0].Cost.MicroUSD <= 0 || layer.Buckets[0].Cost.PricedAtoms != len(result.Atoms) {
		t.Fatalf("Codex catalog pricing = %+v, atoms=%+v", layer.Buckets, result.Atoms)
	}
}

func TestScanTranscriptSyntheticClaudeRevisionCodexForkAndResume(t *testing.T) {
	root := t.TempDir()
	claudePath := filepath.Join(root, ".claude", "projects", "-workspace", "session.jsonl")
	codexPath := filepath.Join(root, ".codex", "sessions", "2026", "09", "02", "rollout.jsonl")
	revision := usageClaude(1, "same-message", 10, 5)
	writeJSONL(t, claudePath, revision)
	claudeState := &TranscriptScannerState{}
	source := TranscriptSource{Path: claudePath, Provider: "claude", ThreadID: "thread"}
	first, err := ScanTranscript(source, claudeState, TranscriptScanOptions{})
	if err != nil || atomQuantities(first.Atoms)["input:none"] != 10 {
		t.Fatalf("initial Claude revision = %+v, err = %v", first, err)
	}
	first.Receipt.Commit()
	writeJSONL(t, claudePath, usageClaude(2, "same-message", 15, 7))
	revised, err := ScanTranscript(source, claudeState, TranscriptScanOptions{})
	if err != nil || !revised.Reset || atomQuantities(revised.Atoms)["input:none"] != 15 {
		t.Fatalf("revised Claude message = %+v, err = %v", revised, err)
	}
	revised.Receipt.Commit()

	writeJSONL(t, codexPath,
		map[string]any{"type": "session_meta", "timestamp": 1, "payload": map[string]any{"id": "child", "model": "gpt-child", "source": map[string]any{"subagent": map[string]any{"thread_spawn": map[string]any{"parent_thread_id": "parent"}}}}},
		map[string]any{"type": "turn_context", "timestamp": 2, "payload": map[string]any{"model": "gpt-handoff"}},
		usageCodex(3, 100, 40, 10), usageCodex(4, 130, 50, 14),
	)
	codexState := &TranscriptScannerState{}
	codexSource := TranscriptSource{Path: codexPath, Provider: "codex", ThreadID: "fallback"}
	limited, err := ScanTranscript(codexSource, codexState, TranscriptScanOptions{MaxRecords: 3})
	if err != nil || !limited.BudgetExhausted || len(limited.Atoms) != 3 {
		t.Fatalf("budget scan = %+v, err = %v", limited, err)
	}
	limited.Receipt.Commit()
	resumed, err := ScanTranscript(codexSource, codexState, TranscriptScanOptions{})
	if err != nil || resumed.RecordsParsed != 1 || atomQuantities(resumed.Atoms)["input:none"] != 20 || resumed.Atoms[0].Payload["thread_id"] != "child" || resumed.Atoms[0].Payload["fork_id"] != "parent" {
		t.Fatalf("Codex resume/fork = %+v, err = %v", resumed, err)
	}
	resumed.Receipt.Commit()
}

func TestClaudeCursorBoundsMessageIDStateAndMatchesPythonEviction(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "claude.jsonl")
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	source := TranscriptSource{Path: path, Provider: "claude", ThreadID: "synthetic-thread"}
	const messageLimit = 4096
	records := make([]map[string]any, 0, messageLimit+1)
	for index := 0; index <= messageLimit; index++ {
		records = append(records, usageClaude(float64(index+1), fmt.Sprintf("message-%04d", index), float64(index+1), 1))
	}
	writeJSONL(t, path, records...)
	cursor := NewTranscriptCursorStore(cursorPath)
	state := &TranscriptScannerState{}
	const maxRecordsPerPass = 512
	transcriptSize := transcriptFixtureSize(t, path)
	var originalAtoms []UsageAtom
	for cycle := 0; state.Offset < transcriptSize; cycle++ {
		beforeOffset := state.Offset
		scan, scanErr := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: maxRecordsPerPass})
		if scanErr != nil || scan.Receipt == nil {
			t.Fatalf("bounded Claude scan cycle %d = %+v, err = %v", cycle, scan, scanErr)
		}
		if scan.RecordsParsed == 0 || scan.RecordsParsed > maxRecordsPerPass || scan.BytesRead <= 0 {
			t.Fatalf("bounded Claude scan cycle %d made invalid progress: %+v", cycle, scan)
		}
		originalAtoms = append(originalAtoms, scan.Atoms...)
		if err := cursor.Prepare(source, scan.Receipt.before, scan.Receipt.after); err != nil {
			t.Fatalf("prepare Claude cursor cycle %d: %v", cycle, err)
		}
		if err := cursor.Commit(source, scan.Receipt.after); err != nil {
			t.Fatalf("commit Claude cursor cycle %d: %v", cycle, err)
		}

		// Reload after every durable batch so later scans exercise exactly the
		// cursor state another process would recover after a restart.
		cursor = NewTranscriptCursorStore(cursorPath)
		loaded, loadErr := cursor.State(source)
		if loadErr != nil {
			t.Fatalf("reload Claude cursor cycle %d: %v", cycle, loadErr)
		}
		if !reflect.DeepEqual(loaded, scan.Receipt.after) {
			got, want := reflect.ValueOf(loaded), reflect.ValueOf(scan.Receipt.after)
			differentFields := make([]string, 0, got.NumField())
			for index := 0; index < got.NumField(); index++ {
				if !reflect.DeepEqual(got.Field(index).Interface(), want.Field(index).Interface()) {
					differentFields = append(differentFields, got.Type().Field(index).Name)
				}
			}
			t.Fatalf("reloaded Claude cursor cycle %d differs from committed receipt in fields %v: got offset=%d sequence=%d, want offset=%d sequence=%d", cycle, differentFields, loaded.Offset, loaded.ClaudeSequence, scan.Receipt.after.Offset, scan.Receipt.after.ClaudeSequence)
		}
		if loaded.Offset <= beforeOffset {
			t.Fatalf("bounded Claude scan cycle %d did not advance: before=%d after=%d", cycle, beforeOffset, loaded.Offset)
		}
		state = &loaded
	}
	if len(originalAtoms) != 2*(messageLimit+1) {
		t.Fatalf("bounded Claude history produced %d atoms, want %d", len(originalAtoms), 2*(messageLimit+1))
	}
	restartedState := *state
	if len(restartedState.ClaudeUsage) != messageLimit || len(restartedState.ClaudeOccurrences) != messageLimit {
		t.Fatalf("Claude cursor maps were not bounded after restart: usage=%d occurrences=%d", len(restartedState.ClaudeUsage), len(restartedState.ClaudeOccurrences))
	}
	if len(restartedState.ClaudeMessageOrder) != messageLimit || restartedState.ClaudeMessageOrder[0] != "message-0001" {
		t.Fatalf("Claude cursor eviction order = first %q, count %d", restartedState.ClaudeMessageOrder[0], len(restartedState.ClaudeMessageOrder))
	}
	info, err := os.Stat(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	if info.Size() >= maxTranscriptCursorSidecarBytes {
		t.Fatalf("bounded Claude cursor sidecar size = %d, limit %d", info.Size(), maxTranscriptCursorSidecarBytes)
	}

	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	var originalMessageAtoms []UsageAtom
	originalMessageEventIDs := make(map[string]struct{}, 2)
	for _, atom := range originalAtoms {
		if atom.EventID == "claude:"+path+":message-0000" {
			originalMessageAtoms = append(originalMessageAtoms, atom)
			originalMessageEventIDs[atom.EventID] = struct{}{}
		}
	}
	if len(originalMessageAtoms) != 2 {
		t.Fatalf("initial Claude scan emitted %d atoms for the synthetic message, want input and output", len(originalMessageAtoms))
	}
	if _, err := writer.AppendBatch(context.Background(), AppendBatch{UsageAtoms: originalMessageAtoms}); err != nil {
		t.Fatalf("append initial Claude usage: %v", err)
	}

	appendJSONL(t, path, usageClaude(float64(messageLimit+2), "message-0000", 5000, 2), false)
	resumed, err := ScanTranscript(source, &restartedState, TranscriptScanOptions{})
	if err != nil || len(resumed.Atoms) != 2 {
		t.Fatalf("reappeared evicted message = %+v, err = %v", resumed, err)
	}
	expectedReappearedID := fmt.Sprintf("claude:%s:message-0000:revision-%d", path, messageLimit+1)
	if resumed.Atoms[0].EventID != expectedReappearedID || resumed.Atoms[1].EventID != expectedReappearedID || atomQuantities(resumed.Atoms)["input:none"] != 5000 || atomQuantities(resumed.Atoms)["output:none"] != 2 {
		t.Fatalf("reappeared evicted message did not restart its usage identity: %+v", resumed.Atoms)
	}
	if _, collides := originalMessageEventIDs[resumed.Atoms[0].EventID]; collides {
		t.Fatalf("reappeared evicted message reused its original event ID %q", resumed.Atoms[0].EventID)
	}
	if _, err := writer.AppendBatch(context.Background(), AppendBatch{UsageAtoms: resumed.Atoms}); err != nil {
		t.Fatalf("append resumed usage for evicted Claude message: %v", err)
	}
}

func TestTranscriptCursorStoreRejectsAggregateSampleExpansion(t *testing.T) {
	root := t.TempDir()
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	sample := bytes.Repeat([]byte("sample"), transcriptPrefixBytes/len("sample"))
	compressed, err := gzipTranscriptCursorSample(sample)
	if err != nil {
		t.Fatal(err)
	}
	disk := transcriptCursorDisk{Version: 2, States: make(map[string]TranscriptScannerState)}
	for index := 0; index < 700; index++ {
		key := fmt.Sprintf("claude\x00/source-%04d.jsonl", index)
		disk.States[key] = TranscriptScannerState{
			Path: "/source.jsonl", PrefixCompressed: compressed,
			ParsedTailCompressed: compressed,
		}
	}
	if err := atomicSidecarWrite(cursorPath, disk); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	if info.Size() >= maxTranscriptCursorSidecarBytes {
		t.Fatalf("compressed fixture size = %d, want under on-disk bound %d", info.Size(), maxTranscriptCursorSidecarBytes)
	}
	_, err = NewTranscriptCursorStore(cursorPath).State(TranscriptSource{Path: "/source.jsonl", Provider: "claude"})
	if err == nil || !strings.Contains(err.Error(), "aggregate sample size") {
		t.Fatalf("load compressed cursor with aggregate expansion: err=%v, want aggregate sample bound", err)
	}
}

func TestTranscriptCursorStoreRejectsOversizedLegacyRawSamplesBeforeMigration(t *testing.T) {
	root := t.TempDir()
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	sourcePath := filepath.Join(root, "source.jsonl")
	disk := transcriptCursorDisk{Version: 1, States: map[string]TranscriptScannerState{
		"claude\x00" + sourcePath: {
			Path: sourcePath, Prefix: bytes.Repeat([]byte("x"), transcriptPrefixBytes+1),
		},
	}}
	if err := atomicSidecarWrite(cursorPath, disk); err != nil {
		t.Fatal(err)
	}

	_, err := NewTranscriptCursorStore(cursorPath).State(TranscriptSource{Path: sourcePath, Provider: "claude"})
	if err == nil || !strings.Contains(err.Error(), "raw sample exceeds its per-field bound") {
		t.Fatalf("load oversized legacy cursor sample: err=%v, want per-field bound error", err)
	}
	var persisted transcriptCursorDisk
	data, err := os.ReadFile(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &persisted); err != nil {
		t.Fatalf("read legacy cursor sidecar after rejection: %v", err)
	}
	if persisted.Version != 1 {
		t.Fatalf("sidecar version after rejected migration = %d, want original v1 retained", persisted.Version)
	}
}

func TestTranscriptCursorStoreMigratesLegacySidecarWithRetiredEntries(t *testing.T) {
	root := t.TempDir()
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	currentPath := filepath.Join(root, "current.jsonl")
	if err := os.WriteFile(currentPath, []byte("current transcript"), 0o600); err != nil {
		t.Fatal(err)
	}
	disk := transcriptCursorDisk{Version: 1, States: make(map[string]TranscriptScannerState)}
	for index := 0; index < maxTranscriptCursorStates+100; index++ {
		path := filepath.Join(root, fmt.Sprintf("retired-%05d.jsonl", index))
		key := "claude\x00" + path
		disk.States[key] = TranscriptScannerState{Path: path, FileModTimeNS: int64(index)}
	}
	currentSource := TranscriptSource{Path: currentPath, Provider: "claude"}
	disk.States[transcriptCursorKey(currentSource)] = TranscriptScannerState{Path: currentPath, Offset: 7}
	if err := atomicSidecarWrite(cursorPath, disk); err != nil {
		t.Fatal(err)
	}

	store := NewTranscriptCursorStore(cursorPath)
	state, err := store.State(currentSource)
	if err != nil {
		t.Fatalf("load recoverable legacy cursor sidecar: %v", err)
	}
	if state.Offset != 7 {
		t.Fatalf("current transcript cursor offset = %d, want 7", state.Offset)
	}
	migrated, err := store.readDiskLocked()
	if err != nil {
		t.Fatalf("reload migrated cursor sidecar: %v", err)
	}
	if migrated.Version != 2 || len(migrated.States) != 1 {
		t.Fatalf("migrated sidecar version/states = %d/%d, want v2 with only current source", migrated.Version, len(migrated.States))
	}
}

func TestTranscriptCursorStorePinsRequestedCursorWhilePruningLegacyEntries(t *testing.T) {
	root := t.TempDir()
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	sourcePath := filepath.Join(root, "active.jsonl")
	if err := os.WriteFile(sourcePath, []byte("active transcript"), 0o600); err != nil {
		t.Fatal(err)
	}
	requested := TranscriptSource{Path: sourcePath, Provider: "provider-0000"}
	disk := transcriptCursorDisk{Version: 1, States: make(map[string]TranscriptScannerState)}
	for index := 0; index < maxTranscriptCursorStates+1; index++ {
		source := TranscriptSource{Path: sourcePath, Provider: fmt.Sprintf("provider-%04d", index)}
		state := TranscriptScannerState{Path: sourcePath, Offset: int64(index + 100), FileModTimeNS: int64(index)}
		disk.States[transcriptCursorKey(source)] = state
	}
	if err := atomicSidecarWrite(cursorPath, disk); err != nil {
		t.Fatal(err)
	}

	state, err := NewTranscriptCursorStore(cursorPath).State(requested)
	if err != nil {
		t.Fatalf("load legacy sidecar while pruning active cursors: %v", err)
	}
	if state.Offset != 100 {
		t.Fatalf("requested legacy cursor offset = %d, want pinned oldest cursor 100", state.Offset)
	}
	var migrated transcriptCursorDisk
	data, err := os.ReadFile(cursorPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &migrated); err != nil {
		t.Fatalf("decode migrated cursor sidecar: %v", err)
	}
	if migrated.Version != 2 || len(migrated.States) != maxTranscriptCursorStates {
		t.Fatalf("migrated version/state count = %d/%d, want v2 with exactly %d states", migrated.Version, len(migrated.States), maxTranscriptCursorStates)
	}
	if _, exists := migrated.States[transcriptCursorKey(requested)]; !exists {
		t.Fatal("migration pruned the requested active transcript cursor")
	}
}

func TestTranscriptCursorStoreRejectsPendingBatchBeyondEntryBound(t *testing.T) {
	root := t.TempDir()
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	changes := make([]transcriptCursorChange, maxTranscriptCursorStates+1)
	for index := range changes {
		path := filepath.Join(root, fmt.Sprintf("active-%04d.jsonl", index))
		changes[index] = transcriptCursorChange{
			source: TranscriptSource{Path: path, Provider: "claude"},
			after:  TranscriptScannerState{Path: path, Offset: 1},
		}
	}
	store := NewTranscriptCursorStore(cursorPath)
	if err := store.PrepareBatch(changes); err == nil || !strings.Contains(err.Error(), "entry bound") {
		t.Fatalf("prepare oversized cursor batch: err=%v, want entry bound", err)
	}
	if _, err := os.Stat(cursorPath); !os.IsNotExist(err) {
		t.Fatalf("rejected pending batch left a sidecar: stat err=%v", err)
	}
	state, err := NewTranscriptCursorStore(cursorPath).State(changes[0].source)
	if err != nil || state.Offset != 0 {
		t.Fatalf("read after rejected batch = %+v, err=%v; want empty readable cursor", state, err)
	}
}

func TestTranscriptCursorStorePrunesRetiredSourcesAcrossCycles(t *testing.T) {
	root := t.TempDir()
	store := NewTranscriptCursorStore(filepath.Join(root, "transcript-cursors.json"))
	disk := transcriptCursorDisk{Version: 2, States: make(map[string]TranscriptScannerState)}
	addSources := func(prefix string, start, count int) []string {
		paths := make([]string, 0, count)
		for index := start; index < start+count; index++ {
			path := filepath.Join(root, fmt.Sprintf("%s-%04d.jsonl", prefix, index))
			if err := os.WriteFile(path, nil, 0o600); err != nil {
				t.Fatalf("create source %s: %v", path, err)
			}
			key := "claude\x00" + path
			disk.States[key] = TranscriptScannerState{Path: path, FileModTimeNS: int64(index + start)}
			paths = append(paths, path)
		}
		return paths
	}
	retire := func(paths []string, start, end int) {
		for _, path := range paths[start:end] {
			if err := os.Remove(path); err != nil {
				t.Fatalf("retire source %s: %v", path, err)
			}
		}
	}
	oldPaths := addSources("old", 0, 600)
	if err := store.writeDiskLocked(disk); err != nil {
		t.Fatalf("write initial source cursors: %v", err)
	}
	retire(oldPaths, 0, 400)
	firstReplacement := addSources("replacement-a", 0, 400)
	if err := store.writeDiskLocked(disk); err != nil {
		t.Fatalf("write first replacement cycle: %v", err)
	}
	retire(oldPaths, 400, 600)
	retire(firstReplacement, 0, 200)
	secondReplacement := addSources("replacement-b", 0, 400)
	if err := store.writeDiskLocked(disk); err != nil {
		t.Fatalf("write second replacement cycle: %v", err)
	}

	recovered, err := store.readDiskLocked()
	if err != nil {
		t.Fatalf("reload cursor state after source churn: %v", err)
	}
	if len(recovered.States) != 600 {
		t.Fatalf("retained cursor states after source churn = %d, want 600 current sources", len(recovered.States))
	}
	for _, path := range secondReplacement {
		if _, ok := recovered.States["claude\x00"+path]; !ok {
			t.Fatalf("current source cursor %s was pruned", path)
		}
	}
	for _, path := range oldPaths[:400] {
		if _, ok := recovered.States["claude\x00"+path]; ok {
			t.Fatalf("retired source cursor %s was retained", path)
		}
	}
}

func TestLegacyClaudeCursorRebuildsMessageOrderBeforeEviction(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "claude-legacy-order.jsonl")
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	source := TranscriptSource{Path: path, Provider: "claude", ThreadID: "synthetic-thread"}
	const messageLimit = maxClaudeMessageIDState
	records := make([]map[string]any, 0, messageLimit+2)
	for index := messageLimit - 1; index >= 0; index-- {
		records = append(records, usageClaude(float64(messageLimit-index), fmt.Sprintf("message-%04d", index), float64(index+1), 1))
	}
	records = append(records, map[string]any{"type": "user", "timestamp": float64(messageLimit + 1)})
	// Updating an existing key changes its counters but does not move it to the
	// end of Python's insertion-ordered map.
	records = append(records, usageClaude(float64(messageLimit+2), "message-4095", 5000, 2))
	writeJSONL(t, path, records...)
	state := &TranscriptScannerState{}
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: messageLimit + 2})
	if err != nil || initial.Receipt == nil || initial.RecordsParsed != messageLimit+2 {
		t.Fatalf("initial Claude cursor setup = %+v, err = %v", initial, err)
	}
	store := NewTranscriptCursorStore(cursorPath)
	if err := store.Prepare(source, initial.Receipt.before, initial.Receipt.after); err != nil {
		t.Fatalf("prepare initial Claude cursor: %v", err)
	}
	if err := store.Commit(source, initial.Receipt.after); err != nil {
		t.Fatalf("commit initial Claude cursor: %v", err)
	}
	initial.Receipt.Commit()
	if len(state.ClaudeMessageOrder) != messageLimit || state.ClaudeMessageOrder[0] != "message-4095" {
		t.Fatalf("initial Claude order = first %q, count %d", state.ClaudeMessageOrder[0], len(state.ClaudeMessageOrder))
	}

	// Older cursor sidecars persisted the usage maps but not their insertion order.
	legacyBefore := cloneTranscriptState(*state)
	legacyAfter := cloneTranscriptState(*state)
	legacyAfter.ClaudeMessageOrder = nil
	legacyAfter.ClaudeMessageOrderRebuildDone = false
	legacyAfter.ClaudeSequence = 0
	store = NewTranscriptCursorStore(cursorPath)
	if err := store.Prepare(source, legacyBefore, legacyAfter); err != nil {
		t.Fatalf("prepare legacy Claude cursor: %v", err)
	}
	if err := store.Commit(source, legacyAfter); err != nil {
		t.Fatalf("commit legacy Claude cursor: %v", err)
	}
	persisted, err := NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatalf("reload legacy Claude cursor: %v", err)
	}
	state = &persisted
	appendJSONL(t, path, usageClaude(float64(messageLimit+3), "message-new", 1, 1), false)

	result, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 4096, MaxRecords: 32})
	if err != nil || result.Receipt == nil {
		t.Fatalf("legacy Claude order migration = %+v, err = %v", result, err)
	}
	if result.BytesRead > 4096 || result.RecordsParsed > 32 {
		t.Fatalf("first legacy Claude order pass exceeded its limits: bytes=%d records=%d", result.BytesRead, result.RecordsParsed)
	}
	if result.Receipt.after.Offset != state.Offset {
		t.Fatalf("legacy order scan advanced the usage cursor before migration completed: before=%d after=%d", state.Offset, result.Receipt.after.Offset)
	}
	store = NewTranscriptCursorStore(cursorPath)
	if err := store.Prepare(source, result.Receipt.before, result.Receipt.after); err != nil {
		t.Fatalf("prepare bounded legacy order progress: %v", err)
	}
	if err := store.Commit(source, result.Receipt.after); err != nil {
		t.Fatalf("commit bounded legacy order progress: %v", err)
	}
	persisted, err = NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatalf("reload bounded legacy order progress: %v", err)
	}
	state = &persisted

	for attempts := 0; !state.ClaudeMessageOrderRebuildDone; attempts++ {
		if attempts > 1000 {
			t.Fatal("legacy Claude order migration did not converge under repeated bounded scans")
		}
		result, err = ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 64 * 1024, MaxRecords: 512})
		if err != nil || result.Receipt == nil {
			t.Fatalf("legacy Claude order migration pass %d = %+v, err = %v", attempts, result, err)
		}
		if result.BytesRead > 64*1024 || result.RecordsParsed > 512 {
			t.Fatalf("legacy Claude order migration exceeded its per-pass limits: bytes=%d records=%d", result.BytesRead, result.RecordsParsed)
		}
		store = NewTranscriptCursorStore(cursorPath)
		if err := store.Prepare(source, result.Receipt.before, result.Receipt.after); err != nil {
			t.Fatalf("prepare legacy Claude order migration pass %d: %v", attempts, err)
		}
		if err := store.Commit(source, result.Receipt.after); err != nil {
			t.Fatalf("commit legacy Claude order migration pass %d: %v", attempts, err)
		}
		persisted, err = NewTranscriptCursorStore(cursorPath).State(source)
		if err != nil {
			t.Fatalf("reload legacy Claude cursor pass %d: %v", attempts, err)
		}
		state = &persisted
	}

	if _, retained := state.ClaudeUsage["message-4095"]; retained {
		t.Fatal("legacy Claude migration retained the oldest message after the new message arrived")
	}
	if _, retained := state.ClaudeUsage["message-0000"]; !retained {
		t.Fatal("legacy Claude migration evicted the newest message based on lexical ID order")
	}
	if len(state.ClaudeMessageOrder) != messageLimit || state.ClaudeMessageOrder[0] != "message-4094" || state.ClaudeMessageOrder[len(state.ClaudeMessageOrder)-1] != "message-new" {
		t.Fatalf("legacy Claude order after append = first %q, last %q, count %d", state.ClaudeMessageOrder[0], state.ClaudeMessageOrder[len(state.ClaudeMessageOrder)-1], len(state.ClaudeMessageOrder))
	}
	if state.ClaudeSequence != messageLimit+3 {
		t.Fatalf("legacy Claude sequence after bounded rebuild = %d, want %d", state.ClaudeSequence, messageLimit+3)
	}
	appendJSONL(t, path, usageClaude(float64(messageLimit+4), "message-0000", 9000, 2), false)
	updated, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || len(updated.Atoms) != 2 || atomQuantities(updated.Atoms)["input:none"] != 8999 {
		t.Fatalf("legacy cursor cumulative update should emit a delta from the recovered baseline: atoms=%+v err=%v", updated.Atoms, err)
	}
	updated.Receipt.Commit()
	appendJSONL(t, path, usageClaude(float64(messageLimit+5), "", 5, 1), false)
	withoutID, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || len(withoutID.Atoms) != 2 || !strings.Contains(withoutID.Atoms[0].EventID, ":line-4100:") {
		t.Fatalf("legacy cursor should resume the global Claude record sequence for missing IDs: atoms=%+v err=%v", withoutID.Atoms, err)
	}
}

func TestLegacyClaudeCursorRebuildDropsEntriesOutsideRecoveredBound(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "claude-legacy-overbound.jsonl")
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	source := TranscriptSource{Path: path, Provider: "claude", ThreadID: "synthetic-thread"}
	const historicalMessageCount = 5000
	records := make([]map[string]any, 0, historicalMessageCount)
	for index := 0; index < historicalMessageCount; index++ {
		records = append(records, usageClaude(float64(index+1), fmt.Sprintf("message-%04d", index), float64(index+1), 1))
	}
	writeJSONL(t, path, records...)
	state := &TranscriptScannerState{}
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: historicalMessageCount})
	if err != nil || initial.Receipt == nil || initial.RecordsParsed != historicalMessageCount {
		t.Fatalf("initial overbound Claude cursor setup = %+v, err = %v", initial, err)
	}
	store := NewTranscriptCursorStore(cursorPath)
	if err := store.Prepare(source, initial.Receipt.before, initial.Receipt.after); err != nil {
		t.Fatalf("prepare initial overbound cursor: %v", err)
	}
	if err := store.Commit(source, initial.Receipt.after); err != nil {
		t.Fatalf("commit initial overbound cursor: %v", err)
	}
	initial.Receipt.Commit()

	legacyBefore := cloneTranscriptState(*state)
	legacyAfter := cloneTranscriptState(*state)
	for index := 0; index < historicalMessageCount-maxClaudeMessageIDState; index++ {
		messageID := fmt.Sprintf("message-%04d", index)
		legacyAfter.ClaudeUsage[messageID] = UsageCounters{"input_tokens": float64(index + 1), "output_tokens": 1}
		legacyAfter.ClaudeOccurrences[messageID] = 1
	}
	legacyAfter.ClaudeMessageOrder = nil
	legacyAfter.ClaudeMessageOrderRebuildDone = false
	legacyAfter.ClaudeSequence = 0
	store = NewTranscriptCursorStore(cursorPath)
	if err := store.Prepare(source, legacyBefore, legacyAfter); err != nil {
		t.Fatalf("prepare synthetic legacy overbound cursor: %v", err)
	}
	if err := store.Commit(source, legacyAfter); err != nil {
		t.Fatalf("commit synthetic legacy overbound cursor: %v", err)
	}
	persisted, err := NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatalf("reload synthetic legacy overbound cursor: %v", err)
	}
	state = &persisted

	for attempts := 0; !state.ClaudeMessageOrderRebuildDone; attempts++ {
		if attempts > 1000 {
			t.Fatal("overbound legacy order rebuild did not converge")
		}
		result, scanErr := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 64 * 1024, MaxRecords: 512})
		if scanErr != nil || result.Receipt == nil {
			t.Fatalf("overbound legacy order pass %d = %+v, err = %v", attempts, result, scanErr)
		}
		if result.BytesRead > 64*1024 || result.RecordsParsed > 512 {
			t.Fatalf("overbound legacy migration exceeded its per-pass limits: bytes=%d records=%d", result.BytesRead, result.RecordsParsed)
		}
		store = NewTranscriptCursorStore(cursorPath)
		if err := store.Prepare(source, result.Receipt.before, result.Receipt.after); err != nil {
			t.Fatalf("prepare overbound legacy order pass %d: %v", attempts, err)
		}
		if err := store.Commit(source, result.Receipt.after); err != nil {
			t.Fatalf("commit overbound legacy order pass %d: %v", attempts, err)
		}
		persisted, err = NewTranscriptCursorStore(cursorPath).State(source)
		if err != nil {
			t.Fatalf("reload overbound legacy cursor pass %d: %v", attempts, err)
		}
		state = &persisted
	}
	if len(state.ClaudeUsage) != maxClaudeMessageIDState || len(state.ClaudeMessageOrder) != maxClaudeMessageIDState {
		t.Fatalf("rebuilt legacy Claude cursor exceeded its state bound: usage=%d order=%d", len(state.ClaudeUsage), len(state.ClaudeMessageOrder))
	}
	for index := 0; index < historicalMessageCount; index++ {
		messageID := fmt.Sprintf("message-%04d", index)
		_, retained := state.ClaudeUsage[messageID]
		if wantRetained := index >= historicalMessageCount-maxClaudeMessageIDState; retained != wantRetained {
			t.Fatalf("legacy cursor retained %q=%t, want %t", messageID, retained, wantRetained)
		}
	}
}

func TestLegacyClaudeOrderRebuildStateClearsWhenTranscriptResets(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "claude-legacy-reset.jsonl")
	source := TranscriptSource{Path: path, Provider: "claude", ThreadID: "synthetic-thread"}
	records := make([]map[string]any, 128)
	for index := range records {
		records[index] = usageClaude(float64(index+1), fmt.Sprintf("old-message-%03d", index), float64(index+1), 1)
	}
	writeJSONL(t, path, records...)
	state := &TranscriptScannerState{}
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: len(records)})
	if err != nil || initial.Receipt == nil {
		t.Fatalf("initial reset fixture scan = %+v err=%v", initial, err)
	}
	initial.Receipt.Commit()
	state.ClaudeMessageOrder = nil
	state.ClaudeMessageOrderRebuildDone = false
	state.ClaudeSequence = 0
	progress, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 4096, MaxRecords: 1})
	if err != nil || progress.Receipt == nil || !progress.Receipt.after.ClaudeMessageOrderRebuildActive || progress.Receipt.after.ClaudeMessageOrderRebuildOffset == 0 {
		t.Fatalf("legacy order rebuild did not persist bounded progress: result=%+v err=%v", progress, err)
	}
	progress.Receipt.Commit()
	writeJSONL(t, path, usageClaude(float64(len(records)+1), "replacement-message", 7, 1))
	reset, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || reset.Receipt == nil || !reset.Reset {
		t.Fatalf("legacy order rebuild did not recover from transcript replacement: result=%+v err=%v", reset, err)
	}
	if reset.Receipt.after.ClaudeMessageOrderRebuildActive || len(reset.Receipt.after.ClaudeMessageOrderRebuildOrder) != 0 {
		t.Fatalf("transcript reset retained stale rebuild state: %+v", reset.Receipt.after)
	}
	if len(reset.Receipt.after.ClaudeUsage) != 1 || reset.Receipt.after.ClaudeUsage["replacement-message"]["input_tokens"] != 7 {
		t.Fatalf("transcript reset retained stale Claude usage: %+v", reset.Receipt.after.ClaudeUsage)
	}
	if !reset.Receipt.after.UsageCommittedEOFKnown || reset.Receipt.after.UsageCommittedEOFSize != transcriptFixtureSize(t, path) {
		t.Fatalf("transcript reset retained or missed its committed EOF watermark: state=%+v", reset.Receipt.after)
	}
}

func TestScanTranscriptCodexEffortSurvivesCursorRestartAndSettingsEvents(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "rollout-effort.jsonl")
	cursorPath := filepath.Join(root, "transcript-cursors.json")
	writeJSONL(t, path,
		map[string]any{"type": "session_meta", "timestamp": 1, "payload": map[string]any{"id": "effort-thread"}},
		map[string]any{"type": "turn_context", "timestamp": 2, "payload": map[string]any{"model": "gpt-effort", "effort": "high"}},
		usageCodex(3, 100, 40, 10),
	)
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "fallback"}
	state := &TranscriptScannerState{}
	first, err := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: 2})
	if err != nil || first.RecordsParsed != 2 || len(first.Atoms) != 0 {
		t.Fatalf("context pass = %+v, err = %v", first, err)
	}
	store := NewTranscriptCursorStore(cursorPath)
	if err := store.Prepare(source, first.Receipt.before, first.Receipt.after); err != nil {
		t.Fatal(err)
	}
	if err := store.Commit(source, first.Receipt.after); err != nil {
		t.Fatal(err)
	}
	restartedState, err := NewTranscriptCursorStore(cursorPath).State(source)
	if err != nil {
		t.Fatal(err)
	}
	resumed, err := ScanTranscript(source, &restartedState, TranscriptScanOptions{})
	if err != nil || len(resumed.Atoms) != 3 {
		t.Fatalf("resumed scan = %+v, err = %v", resumed, err)
	}
	for _, atom := range resumed.Atoms {
		if atom.Payload["model"] != "gpt-effort" || atom.Payload["effort"] != "high" || atom.Payload["model_evidence"] != "scan_state.resumed_model" {
			t.Fatalf("resumed metadata = %#v", atom.Payload)
		}
	}

	settingsPath := filepath.Join(root, "rollout-settings.jsonl")
	writeJSONL(t, settingsPath,
		map[string]any{"type": "event_msg", "timestamp": 4, "payload": map[string]any{"type": "thread_settings_applied", "thread_settings": map[string]any{"model": "gpt-settings", "reasoning_effort": "medium", "pricing_profile": "batch", "service_tier": "flex"}}},
		usageCodex(5, 12, 2, 3),
	)
	settings, err := ScanTranscript(TranscriptSource{Path: settingsPath, Provider: "codex", ThreadID: "settings"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil || len(settings.Atoms) != 3 {
		t.Fatalf("settings scan = %+v, err = %v", settings, err)
	}
	for _, atom := range settings.Atoms {
		if atom.Payload["model"] != "gpt-settings" || atom.Payload["effort"] != "medium" || atom.Payload["pricing_profile"] != "batch" || atom.Payload["service_tier"] != "flex" {
			t.Fatalf("settings metadata = %#v", atom.Payload)
		}
	}
}

func TestTranscriptContextNormalizationMatchesPythonLengthBound(t *testing.T) {
	longASCII := "  " + strings.Repeat("H", 70) + "  "
	if got, want := normalizeTranscriptEffort(longASCII), strings.Repeat("h", 64); got != want {
		t.Fatalf("bounded effort = %q, want %q", got, want)
	}
	longUnicode := strings.Repeat("Ä", 70)
	got := normalizeTranscriptPricingContext(longUnicode)
	want := strings.Repeat("ä", 64)
	if got != want {
		t.Fatalf("bounded Unicode pricing context = %q, want %q", got, want)
	}
	if gotRunes := len([]rune(got)); gotRunes != 64 {
		t.Fatalf("bounded pricing context has %d code points, want 64", gotRunes)
	}
	fullLowerExpansion := "\u0130" + strings.Repeat("A", 63)
	wantExpanded := "i\u0307" + strings.Repeat("a", 62)
	if got := normalizeTranscriptPricingContext(fullLowerExpansion); got != wantExpanded {
		t.Fatalf("Python-compatible full lowercase before truncation = %q, want %q", got, wantExpanded)
	}
	if got := normalizeTranscriptEffort(fullLowerExpansion); got != wantExpanded {
		t.Fatalf("Python-compatible effort lowercase before truncation = %q, want %q", got, wantExpanded)
	}
	if got := normalizeTranscriptPricingContext(" \t "); got != "default" {
		t.Fatalf("empty pricing context = %q, want default", got)
	}
}

func TestScanTranscriptCodexExplicitEmptyContextResetsToDefaults(t *testing.T) {
	path := filepath.Join(t.TempDir(), "codex-context-reset.jsonl")
	writeJSONL(t, path,
		map[string]any{"type": "session_meta", "timestamp": 1, "payload": map[string]any{
			"id": "context-reset-thread", "effort": "high", "pricing_profile": "enterprise", "service_tier": "flex",
		}},
		usageCodex(2, 10, 2, 3),
		map[string]any{"type": "turn_context", "timestamp": 3, "payload": map[string]any{
			"effort": "", "pricing_profile": "", "service_tier": "",
		}},
		usageCodex(4, 20, 4, 6),
		map[string]any{"type": "event_msg", "timestamp": 5, "payload": map[string]any{"type": "thread_settings_applied", "thread_settings": map[string]any{
			"reasoning_effort": "medium", "pricing_profile": "batch", "service_tier": "scale",
		}}},
		usageCodex(6, 30, 6, 9),
		map[string]any{"type": "event_msg", "timestamp": 7, "payload": map[string]any{"type": "thread_settings_applied", "thread_settings": map[string]any{
			"reasoning_effort": "", "pricing_profile": "", "service_tier": "",
		}}},
		usageCodex(8, 40, 8, 12),
	)
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "fixture"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil || len(result.Atoms) != 12 {
		t.Fatalf("context-reset scan returned %d atoms, err=%v", len(result.Atoms), err)
	}
	wantByTime := map[float64]struct{ effort, profile, tier string }{
		2: {effort: "high", profile: "enterprise", tier: "flex"},
		4: {effort: "unknown", profile: "default", tier: "default"},
		6: {effort: "medium", profile: "batch", tier: "scale"},
		8: {effort: "unknown", profile: "default", tier: "default"},
	}
	for _, atom := range result.Atoms {
		want, ok := wantByTime[atom.ObservedAt]
		if !ok {
			t.Fatalf("unexpected usage atom timestamp %v: %#v", atom.ObservedAt, atom.Payload)
		}
		if atom.Payload["effort"] != want.effort || atom.Payload["pricing_profile"] != want.profile || atom.Payload["service_tier"] != want.tier {
			t.Errorf("context at %v = effort:%v profile:%v tier:%v, want %+v", atom.ObservedAt, atom.Payload["effort"], atom.Payload["pricing_profile"], atom.Payload["service_tier"], want)
		}
	}
}

func TestScanTranscriptCodexForkSuppressesCopiedHistory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-fork.jsonl")
	writeJSONL(t, path,
		map[string]any{"type": "session_meta", "timestamp": 1, "payload": map[string]any{"id": "child-thread", "forked_from_id": "parent-thread", "thread_source": "subagent"}},
		map[string]any{"type": "session_meta", "timestamp": 1.5, "payload": map[string]any{"id": "parent-thread"}},
		map[string]any{"type": "turn_context", "timestamp": 2, "payload": map[string]any{"model": "gpt-parent", "effort": "high"}},
		usageCodex(3, 100, 40, 10), usageCodex(4, 150, 70, 25),
		map[string]any{"type": "turn_context", "timestamp": 5, "payload": map[string]any{"model": "gpt-child", "effort": "medium"}},
		map[string]any{"type": "event_msg", "timestamp": 6, "payload": map[string]any{"type": "inter_agent_communication_metadata"}},
		usageCodex(7, 160, 75, 30),
	)
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "tmux-session"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if got := atomQuantities(result.Atoms); !reflect.DeepEqual(got, map[string]float64{"input:none": 5, "input:read": 5, "output:none": 5}) {
		t.Fatalf("fork child quantities = %#v, atoms=%#v", got, result.Atoms)
	}
	for _, atom := range result.Atoms {
		if atom.Payload["thread_id"] != "child-thread" {
			t.Fatalf("child atom retained tmux attribution: %#v", atom.Payload)
		}
	}
	if len(result.Tombstones) != 6 {
		t.Fatalf("fork tombstones = %#v, want 6", result.Tombstones)
	}
	var total float64
	for _, tombstone := range result.Tombstones {
		if tombstone.Model != "gpt-parent" || tombstone.ThreadID != "child-thread" {
			t.Fatalf("fork tombstone metadata = %+v", tombstone)
		}
		total += tombstone.Quantity
	}
	if total != 175 {
		t.Fatalf("fork tombstone quantity = %v, want 175", total)
	}
}

func TestScanTranscriptCodexCanonicalHandoffSuppressesCopiedHistory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-canonical-fork.jsonl")
	writeJSONL(t, path,
		map[string]any{"type": "session_meta", "timestamp": 1, "payload": map[string]any{"id": "child-thread", "forked_from_id": "parent-thread", "thread_source": "subagent"}},
		map[string]any{"type": "session_meta", "timestamp": 1.5, "payload": map[string]any{"id": "parent-thread"}},
		map[string]any{"type": "turn_context", "timestamp": 2, "payload": map[string]any{"model": "gpt-parent", "effort": "high"}},
		usageCodex(3, 100, 40, 10), usageCodex(4, 150, 70, 25),
		map[string]any{"type": "turn_context", "timestamp": 5, "payload": map[string]any{"model": "gpt-child", "effort": "medium"}},
		map[string]any{"type": "inter_agent_communication_metadata", "timestamp": 6, "payload": map[string]any{}},
		usageCodex(7, 160, 75, 30),
	)
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "tmux-session"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if got := atomQuantities(result.Atoms); !reflect.DeepEqual(got, map[string]float64{"input:none": 5, "input:read": 5, "output:none": 5}) {
		t.Fatalf("canonical fork child quantities = %#v, atoms=%#v", got, result.Atoms)
	}
	for _, atom := range result.Atoms {
		if atom.Payload["thread_id"] != "child-thread" {
			t.Fatalf("canonical child atom retained tmux attribution: %#v", atom.Payload)
		}
	}
	if len(result.Tombstones) != 6 {
		t.Fatalf("canonical fork tombstones = %#v, want 6", result.Tombstones)
	}
}

func TestScanTranscriptClaudeNestedCacheRolesAndZeroSuppression(t *testing.T) {
	path := filepath.Join(t.TempDir(), "claude.jsonl")
	record := usageClaudeNestedCache(1, "nested")
	usage := record["message"].(map[string]any)["usage"].(map[string]any)
	usage["cache_read_input_tokens"] = 0.0
	writeJSONL(t, path, record)
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "claude", ThreadID: "thread"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if got := atomQuantities(result.Atoms); !reflect.DeepEqual(got, map[string]float64{"input:none": 10, "input:write_5m": 3, "input:write_1h": 4, "output:none": 7}) {
		t.Fatalf("nested cache atoms = %#v", got)
	}
	if len(result.Atoms) != 4 || result.Atoms[2].EventID != result.Atoms[1].EventID {
		t.Fatalf("nested cache identities = %#v", result.Atoms)
	}
	repeat, err := ScanTranscript(TranscriptSource{Path: path, Provider: "claude", ThreadID: "thread"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil || !reflect.DeepEqual(atomIDs(result.Atoms), atomIDs(repeat.Atoms)) {
		t.Fatalf("non-deterministic IDs = %v / %v, err = %v", atomIDs(result.Atoms), atomIDs(repeat.Atoms), err)
	}
}

func TestScanTranscriptClaudeFallsBackToIterationUsageAndUnknownModel(t *testing.T) {
	path := filepath.Join(t.TempDir(), "claude-iterations.jsonl")
	writeJSONL(t, path, map[string]any{
		"timestamp": 1.0,
		"type":      "assistant",
		"message": map[string]any{
			"id": "iteration-message",
			"usage": map[string]any{"iterations": []any{
				map[string]any{"input_tokens": 4.0, "output_tokens": 2.0},
				map[string]any{"input_tokens": 3.0, "output_tokens": 5.0},
			}},
		},
	})
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "claude", ThreadID: "not-a-model"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if got := atomQuantities(result.Atoms); !reflect.DeepEqual(got, map[string]float64{"input:none": 7, "output:none": 7}) {
		t.Fatalf("iteration usage quantities = %#v", got)
	}
	for _, atom := range result.Atoms {
		if atom.Payload["model"] != "unknown" || atom.Payload["model_evidence"] != "unknown" || atom.Payload["telemetry_complete"] != true {
			t.Fatalf("iteration usage metadata = %#v", atom.Payload)
		}
	}
}

func TestScanTranscriptClaudeAndCodexIncrementalPartialAndReceipt(t *testing.T) {
	root := t.TempDir()
	claude := filepath.Join(root, "claude.jsonl")
	codex := filepath.Join(root, "rollout.jsonl")
	writeJSONL(t, claude, usageClaude(1, "claude-message-1", 20, 5))
	writeJSONL(t, codex, usageCodex(1, 30, 10, 7))
	claudeState := &TranscriptScannerState{}
	codexState := &TranscriptScannerState{}
	claudeSource := TranscriptSource{Path: claude, Provider: "claude", SourceID: "claude", ThreadID: "claude-thread"}
	codexSource := TranscriptSource{Path: codex, Provider: "codex", SourceID: "codex", ThreadID: "codex-thread"}
	first, err := ScanTranscript(claudeSource, claudeState, TranscriptScanOptions{})
	if err != nil || len(first.Atoms) != 2 {
		t.Fatalf("Claude scan = %+v, err = %v", first, err)
	}
	first.Receipt.Commit()
	first, err = ScanTranscript(codexSource, codexState, TranscriptScanOptions{})
	if err != nil || len(first.Atoms) != 3 {
		t.Fatalf("Codex scan = %+v, err = %v", first, err)
	}
	first.Receipt.Commit()

	remaining := appendJSONL(t, claude, usageClaude(2, "claude-message-2", 40, 9), true)
	partialFile, err := os.Stat(claude)
	if err != nil {
		t.Fatal(err)
	}
	partial, err := ScanTranscript(claudeSource, claudeState, TranscriptScanOptions{})
	partialData, readErr := os.ReadFile(claude)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if err != nil || partial.RecordsParsed != 0 || partial.BytesRead > DefaultTranscriptMaxBytes || partial.Receipt.after.Offset != claudeState.Offset || !bytes.Equal(partial.Receipt.after.PendingLine, partialData[claudeState.Offset:]) {
		t.Fatalf("partial scan = %+v, err = %v", partial, err)
	}
	if partialFile.Size() <= claudeState.Offset {
		t.Fatal("fixture did not append a partial transcript record")
	}
	partial.Receipt.Commit()
	if _, err := os.OpenFile(claude, os.O_APPEND|os.O_WRONLY, 0o600); err != nil {
		t.Fatal(err)
	}
	file, err := os.OpenFile(claude, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write(remaining); err != nil {
		t.Fatal(err)
	}
	file.Close()
	completed, err := ScanTranscript(claudeSource, claudeState, TranscriptScanOptions{})
	if err != nil || completed.RecordsParsed != 1 || len(completed.Atoms) != 2 {
		t.Fatalf("completed scan = %+v, err = %v", completed, err)
	}
	completed.Receipt.Commit()
	unchanged, err := ScanTranscript(claudeSource, claudeState, TranscriptScanOptions{})
	if err != nil || unchanged.RecordsParsed != 0 {
		t.Fatalf("unchanged scan = %+v, err = %v", unchanged, err)
	}
	unchanged.Receipt.Commit()
}

func TestScanTranscriptResetsOnReplacementAndHonorsBudget(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout.jsonl")
	writeJSONL(t, path, usageCodex(1, 10, 2, 3), usageCodex(2, 20, 4, 6), usageCodex(3, 30, 6, 9))
	state := &TranscriptScannerState{}
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}
	limited, err := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: 1, MaxBytes: 1 << 20})
	if err != nil || limited.RecordsParsed != 1 || !limited.BudgetExhausted {
		t.Fatalf("limited scan = %+v, err = %v", limited, err)
	}
	limited.Receipt.Commit()
	next, err := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: 10, MaxBytes: 1 << 20})
	if err != nil || next.RecordsParsed != 2 {
		t.Fatalf("resumed scan = %+v, err = %v", next, err)
	}
	next.Receipt.Commit()
	writeJSONL(t, path, usageCodex(9, 4, 1, 2))
	reset, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || !reset.Reset || reset.RecordsParsed != 1 {
		t.Fatalf("reset scan = %+v, err = %v", reset, err)
	}
	reset.Receipt.Commit()
	if state.ResetCount != 1 {
		t.Fatalf("reset count = %d", state.ResetCount)
	}
}

func TestScanTranscriptReplacementClearsCodexIdentityBeforeUsage(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout.jsonl")
	writeJSONL(t, path,
		map[string]any{"type": "session_meta", "payload": map[string]any{"id": "old-thread", "model": "old-model", "source": map[string]any{"subagent": map[string]any{"thread_spawn": map[string]any{"parent_thread_id": "old-parent"}}}}},
		usageCodex(1, 10, 2, 3),
	)
	state := &TranscriptScannerState{}
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "fallback-thread"}
	first, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || len(first.Atoms) != 3 {
		t.Fatalf("initial scan = %+v, err=%v", first, err)
	}
	first.Receipt.Commit()
	if state.CodexModel != "old-model" || state.CodexThreadID != "old-thread" || state.CodexForkID != "old-parent" {
		t.Fatalf("initial identity = %+v", *state)
	}
	writeJSONL(t, path, usageCodex(2, 4, 1, 2))
	replacement, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || !replacement.Reset || len(replacement.Atoms) != 3 {
		t.Fatalf("replacement scan = %+v, err=%v", replacement, err)
	}
	for _, atom := range replacement.Atoms {
		metadata := atom.Payload
		if metadata["model"] != "unknown" || metadata["thread_id"] != filepath.Clean(path) {
			t.Fatalf("replacement usage retained stale identity: %#v", metadata)
		}
		if _, present := metadata["fork_id"]; present {
			t.Fatalf("replacement usage retained stale fork identity: %#v", metadata)
		}
	}
}

func TestScanTranscriptResetsWhenCommittedTailIsRewrittenInPlace(t *testing.T) {
	path := filepath.Join(t.TempDir(), "long-rollout.jsonl")
	records := make([]map[string]any, 1000)
	for index := range records {
		record := usageCodex(float64(index+1), 10, 2, 3)
		record["padding"] = strings.Repeat("x", 120)
		records[index] = record
	}
	writeJSONL(t, path, records...)
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}
	state := &TranscriptScannerState{}
	first, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || first.RecordsParsed != len(records) {
		t.Fatalf("initial long scan = %+v, err=%v", first, err)
	}
	first.Receipt.Commit()
	original, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	marker := bytes.LastIndex(original, []byte("xxxxxxxx"))
	if marker < transcriptParsedTailBytes {
		t.Fatalf("test fixture did not place padding after the prefix: marker=%d bytes=%d", marker, len(original))
	}
	original[marker] = 'y'
	if err := os.WriteFile(path, original, 0o600); err != nil {
		t.Fatal(err)
	}
	rewritten, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || !rewritten.Reset || rewritten.RecordsParsed != len(records) {
		t.Fatalf("in-place tail rewrite was not replayed: %+v, err=%v", rewritten, err)
	}
}

func TestScanTranscriptDoesNotTrustSameSizeAndMtimeAfterInPlaceRewrite(t *testing.T) {
	path := filepath.Join(t.TempDir(), "same-stat-rollout.jsonl")
	records := make([]map[string]any, 512)
	for index := range records {
		record := usageCodex(float64(index+1), 10, 2, 3)
		record["padding"] = strings.Repeat("x", 120)
		records[index] = record
	}
	writeJSONL(t, path, records...)
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}
	state := &TranscriptScannerState{}
	first, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || first.RecordsParsed != len(records) {
		t.Fatalf("initial scan = %+v, err=%v", first, err)
	}
	first.Receipt.Commit()

	original, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	marker := bytes.LastIndex(original, []byte("xxxxxxxx"))
	if marker < transcriptParsedTailBytes {
		t.Fatalf("test fixture did not place padding after the prefix: marker=%d bytes=%d", marker, len(original))
	}
	original[marker] = 'y'
	if err := os.WriteFile(path, original, 0o600); err != nil {
		t.Fatal(err)
	}
	// A filesystem with coarse timestamp resolution can report the same mtime for
	// a same-size rewrite. Restore the exact prior mtime to force the scanner to
	// rely on its prefix/tail identity checks rather than stat metadata alone.
	if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
		t.Fatal(err)
	}

	rewritten, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || !rewritten.Reset || rewritten.RecordsParsed != len(records) {
		t.Fatalf("same-stat in-place rewrite was not replayed: %+v, err=%v", rewritten, err)
	}
}

func TestScanTranscriptFreshLargeCodexFileBackfillsWithinBounds(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-large.jsonl")
	records := make([]map[string]any, 0, 700)
	records = append(records, map[string]any{"type": "session_meta", "payload": map[string]any{
		"id": "large-thread", "model": "gpt-large",
	}})
	records = append(records, usageCodex(1789555190, 999988, 899992, 9995))
	for index := 0; index < 600; index++ {
		records = append(records, map[string]any{
			"type": "event_msg", "payload": map[string]any{"type": "note", "padding": strings.Repeat("x", 120)},
		})
	}
	latest := usageCodex(1789555200, 1000000, 900000, 10000)
	latest["payload"].(map[string]any)["info"].(map[string]any)["last_token_usage"] = map[string]any{
		"input_tokens": 12.0, "cached_input_tokens": 8.0, "output_tokens": 5.0,
	}
	records = append(records, latest)
	writeJSONL(t, path, records...)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "fallback-thread"}
	state := &TranscriptScannerState{}
	var atoms []UsageAtom
	firstBudgetExhausted := false
	firstBytesRead := int64(0)
	for cycle := 0; cycle < 100 && state.Offset < int64(len(data)); cycle++ {
		result, scanErr := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 2048, MaxRecords: 64})
		if scanErr != nil {
			t.Fatalf("large fresh scan cycle %d: %v", cycle, scanErr)
		}
		if result.BytesRead > 2048 || result.RecordsParsed > 64 {
			t.Fatalf("large fresh scan exceeded its budget: %+v", result)
		}
		if cycle == 0 {
			firstBudgetExhausted = result.BudgetExhausted
			firstBytesRead = result.BytesRead
		}
		atoms = append(atoms, result.Atoms...)
		result.Receipt.Commit()
	}
	if state.Offset != int64(len(data)) || !firstBudgetExhausted || firstBytesRead > 2048 {
		t.Fatalf("large fresh scan did not converge through bounded reads: state=%+v first_bytes=%d", *state, firstBytesRead)
	}
	var latestAtoms []UsageAtom
	for _, atom := range atoms {
		if atom.ObservedAt == 1789555200 {
			latestAtoms = append(latestAtoms, atom)
		}
	}
	if len(latestAtoms) != 3 {
		t.Fatalf("large fresh scan lost its latest usage delta: atoms=%#v all=%#v", latestAtoms, atoms)
	}
	if quantities := atomQuantities(latestAtoms); quantities["input:none"] != 4 || quantities["input:read"] != 8 || quantities["output:none"] != 5 {
		t.Fatalf("large fresh scan latest quantities = %#v", quantities)
	}
	if state.CodexModel != "gpt-large" || state.CodexThreadID != "large-thread" {
		t.Fatalf("large fresh scan context = %+v", *state)
	}
	next := usageCodex(1789555210, 1000010, 900005, 10003)
	next["payload"].(map[string]any)["info"].(map[string]any)["last_token_usage"] = map[string]any{
		"input_tokens": 10.0, "cached_input_tokens": 5.0, "output_tokens": 3.0,
	}
	appendJSONL(t, path, next, false)
	resumed, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 2048, MaxRecords: 64})
	if err != nil || len(resumed.Atoms) != 3 {
		t.Fatalf("large fresh scan resume = %#v err=%v", resumed, err)
	}
	if got := atomQuantities(resumed.Atoms); got["input:none"] != 5 || got["input:read"] != 5 || got["output:none"] != 3 {
		t.Fatalf("large fresh scan resume quantities = %#v", got)
	}
}

func TestScanTranscriptByteBudgetBoundsOversizedLineAndCancellation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "oversized.jsonl")
	line := `{"type":"assistant","message":{"id":"synthetic-large","usage":{"input_tokens":1},"padding":"` + strings.Repeat("x", 1<<20) + `"}}` + "\n"
	if err := os.WriteFile(path, []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
	source := TranscriptSource{Path: path, Provider: "claude", ThreadID: "synthetic"}
	state := &TranscriptScannerState{}
	result, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1024, MaxRecords: 10})
	if err != nil {
		t.Fatalf("bounded oversized-line scan: %v", err)
	}
	if result.BytesRead != 1024 || state.Offset != 0 || !result.BudgetExhausted {
		t.Fatalf("oversized line exceeded budget or advanced a partial-record cursor: result=%+v state=%+v", result, *state)
	}

	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	untouched := &TranscriptScannerState{}
	if _, err := ScanTranscript(source, untouched, TranscriptScanOptions{Context: canceled, MaxBytes: 1024, MaxRecords: 10}); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled scan error = %v, want context.Canceled", err)
	}
	if !reflect.DeepEqual(*untouched, TranscriptScannerState{}) {
		t.Fatalf("canceled scan advanced cursor state: %+v", *untouched)
	}
}

func TestScanTranscriptCountsIntegrityReadsWithinByteBudget(t *testing.T) {
	path := filepath.Join(t.TempDir(), "integrity.jsonl")
	writeJSONL(t, path, usageClaude(1, "integrity", 2, 1))
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	state := &TranscriptScannerState{}
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "claude"}, state, TranscriptScanOptions{MaxBytes: 1 << 20})
	if err != nil {
		t.Fatal(err)
	}
	if result.BytesRead != int64(3*len(data)) || result.BytesRead > 1<<20 {
		t.Fatalf("first-scan physical source reads = %d, want prefix + content + tail = %d", result.BytesRead, 3*len(data))
	}
	result.Receipt.Commit()
	unchanged, err := ScanTranscript(TranscriptSource{Path: path, Provider: "claude"}, state, TranscriptScanOptions{MaxBytes: 1 << 20})
	if err != nil {
		t.Fatal(err)
	}
	if unchanged.BytesRead != int64(2*len(data)) || unchanged.FilesRead != 0 {
		t.Fatalf("unchanged-file validation reads = %+v, want prefix+tail and no content scan", unchanged)
	}
}

func TestScanTranscriptKeepsValidUnterminatedRecordPendingAtExactBudgetBoundary(t *testing.T) {
	path := filepath.Join(t.TempDir(), "unterminated.jsonl")
	record, err := json.Marshal(usageClaude(1, "exact-boundary", 3, 2))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, record, 0o600); err != nil {
		t.Fatal(err)
	}
	maxBytes := int64(0)
	for candidate := int64(len(record)); candidate < int64(len(record))+128; candidate++ {
		if candidate-candidate/8 == int64(len(record)) {
			maxBytes = candidate
			break
		}
	}
	if maxBytes == 0 {
		t.Fatalf("could not construct exact scanner byte boundary for %d-byte JSON record", len(record))
	}
	state := &TranscriptScannerState{}
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "claude"}, state, TranscriptScanOptions{MaxBytes: maxBytes})
	if err != nil || result.RecordsParsed != 0 || len(result.Atoms) != 0 || result.Receipt == nil {
		t.Fatalf("exact-boundary unterminated scan = %+v err=%v", result, err)
	}
	if result.BudgetExhausted {
		t.Fatal("unterminated row ending at snapshot EOF was reported as exhausted budget")
	}
	if result.BytesRead != maxBytes || result.Receipt.after.Offset != 0 || !bytes.Equal(result.Receipt.after.PendingLine, record) {
		t.Fatalf("exact-boundary cursor/read accounting = %+v after=%+v want_budget=%d and a pending row", result, result.Receipt.after, maxBytes)
	}
}

func TestScanTranscriptCancellationDuringScanAndBeforeReceipt(t *testing.T) {
	for _, testCase := range []struct {
		name    string
		records int
		cancel  int
	}{
		{name: "between_records", records: 2, cancel: 5},
		{name: "before_receipt", records: 1, cancel: 5},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "cancel.jsonl")
			records := make([]map[string]any, testCase.records)
			for index := range records {
				records[index] = usageClaude(float64(index+1), fmt.Sprintf("cancel-%d", index), 1, 1)
			}
			writeJSONL(t, path, records...)
			base, cancel := context.WithCancel(context.Background())
			defer cancel()
			ctx := &cancelOnErrContext{Context: base, cancel: cancel, limit: testCase.cancel}
			state := &TranscriptScannerState{}
			result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "claude"}, state, TranscriptScanOptions{Context: ctx, MaxBytes: 1 << 20})
			if !errors.Is(err, context.Canceled) || result.Receipt != nil {
				t.Fatalf("canceled scan returned result=%+v err=%v calls=%d", result, err, ctx.calls)
			}
			if !reflect.DeepEqual(*state, TranscriptScannerState{}) {
				t.Fatalf("canceled scan mutated committed cursor: %+v", *state)
			}
		})
	}

	t.Run("legacy_claude_order_rebuild", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), "cancel-legacy-order.jsonl")
		source := TranscriptSource{Path: path, Provider: "claude", ThreadID: "synthetic-rebuild"}
		const recordCount = 600
		records := make([]map[string]any, 0, recordCount)
		for index := 0; index < recordCount; index++ {
			record := usageClaude(float64(index+1), fmt.Sprintf("rebuild-%d", index), 1, 1)
			record["padding"] = strings.Repeat("x", 256)
			records = append(records, record)
		}
		writeJSONL(t, path, records...)
		if size := transcriptFixtureSize(t, path); size <= 2*claudeMessageOrderRebuildReadBytes {
			t.Fatalf("synthetic legacy transcript size = %d, want multiple rebuild chunks", size)
		}

		state := &TranscriptScannerState{}
		initial, err := ScanTranscript(source, state, TranscriptScanOptions{
			MaxBytes: 1 << 20, MaxRecords: recordCount + 1,
		})
		if err != nil || initial.Receipt == nil || initial.RecordsParsed != recordCount {
			t.Fatalf("seed legacy Claude cursor = %+v, err=%v", initial, err)
		}
		initial.Receipt.Commit()
		if len(state.ClaudeUsage) != recordCount || len(state.ClaudeMessageOrder) != recordCount {
			t.Fatalf("seeded Claude state has usage=%d order=%d, want %d each", len(state.ClaudeUsage), len(state.ClaudeMessageOrder), recordCount)
		}

		// Model an older durable cursor that retained its usage map and file offset
		// but omitted the insertion-order index. Rebuilding it must scan multiple
		// chunks and still honor cancellation before it can prepare a receipt.
		legacyState := cloneTranscriptState(*state)
		legacyState.ClaudeMessageOrder = nil
		legacyState.ClaudeMessageOrderRebuildDone = false
		before := cloneTranscriptState(legacyState)
		base, cancel := context.WithCancel(context.Background())
		defer cancel()
		ctx := &cancelOnErrContext{Context: base, cancel: cancel, limit: 3}
		result, err := ScanTranscript(source, &legacyState, TranscriptScanOptions{
			Context: ctx, MaxBytes: 1 << 20, MaxRecords: recordCount + 1,
		})
		if !errors.Is(err, context.Canceled) || result.Receipt != nil || ctx.calls != 3 {
			t.Fatalf("canceled legacy reconstruction = result=%+v err=%v context_checks=%d, want cancellation after first chunk and no receipt", result, err, ctx.calls)
		}
		if !reflect.DeepEqual(legacyState, before) {
			t.Fatalf("canceled legacy reconstruction mutated committed cursor: got=%+v want=%+v", legacyState, before)
		}
	})
}

func TestScanTranscriptLargeCodexHistoryBackfillsWithinBounds(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-history.jsonl")
	records := make([]map[string]any, 0, 603)
	records = append(records, map[string]any{"type": "session_meta", "payload": map[string]any{"id": "history-thread"}})
	records = append(records, usageCodex(1, 10, 0, 5))
	for index := 0; index < 600; index++ {
		records = append(records, map[string]any{
			"type": "event_msg", "payload": map[string]any{"type": "note", "padding": strings.Repeat("x", 120)},
		})
	}
	records = append(records, usageCodex(2, 20, 0, 9))
	writeJSONL(t, path, records...)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}

	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "history-thread"}
	state := &TranscriptScannerState{}
	outputs := make(map[float64]float64)
	for cycle := 0; cycle < 100 && state.Offset < int64(len(data)); cycle++ {
		result, scanErr := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 2048, MaxRecords: 64})
		if scanErr != nil {
			t.Fatalf("scan cycle %d: %v", cycle, scanErr)
		}
		if result.BytesRead > 2048 || result.RecordsParsed > 64 {
			t.Fatalf("scan cycle %d exceeded its budget: %+v", cycle, result)
		}
		for _, atom := range result.Atoms {
			if atom.Direction == "output" {
				outputs[atom.ObservedAt] += atom.Payload["quantity"].(float64)
			}
		}
		if result.Receipt == nil {
			t.Fatalf("scan cycle %d did not return a cursor receipt", cycle)
		}
		result.Receipt.Commit()
	}
	if state.Offset != int64(len(data)) {
		t.Fatalf("bounded backfill did not reach EOF: offset=%d size=%d", state.Offset, len(data))
	}
	if len(outputs) != 2 || outputs[1] != 5 || outputs[2] != 4 {
		t.Fatalf("large transcript history was not fully backfilled: outputs=%v state=%+v", outputs, *state)
	}
}

func TestScanTranscriptFreshLargeCodexFileSeedsAllContextShapes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-large-context.jsonl")
	records := []map[string]any{
		{"type": "session_meta", "timestamp": 1, "payload": map[string]any{
			"id": "child-thread", "forked_from_id": "parent-thread", "thread_source": "subagent",
		}},
		{"type": "session_meta", "timestamp": 1.5, "payload": map[string]any{"id": "parent-thread"}},
		{"type": "event_msg", "timestamp": 2, "payload": map[string]any{
			"type": "thread_settings_applied", "thread_settings": map[string]any{
				"model": "gpt-tail-child", "reasoning_effort": "medium", "pricing_profile": "batch", "service_tier": "flex",
			},
		}},
		{"type": "inter_agent_communication_metadata", "timestamp": 3, "payload": map[string]any{}},
	}
	for index := 0; index < 600; index++ {
		records = append(records, map[string]any{
			"type": "event_msg", "payload": map[string]any{"type": "note", "padding": strings.Repeat("x", 120)},
		})
	}
	records = append(records, usageCodex(4, 1000, 200, 50))
	writeJSONL(t, path, records...)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "fallback-thread"}
	state := &TranscriptScannerState{}
	var atoms []UsageAtom
	for cycle := 0; cycle < 100 && state.Offset < int64(len(data)); cycle++ {
		result, scanErr := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 2048, MaxRecords: 64})
		if scanErr != nil {
			t.Fatalf("large context scan cycle %d: %v", cycle, scanErr)
		}
		if result.BytesRead > 2048 || result.RecordsParsed > 64 || len(result.Tombstones) != 0 {
			t.Fatalf("large context scan exceeded bounds or emitted tombstones: %+v", result)
		}
		atoms = append(atoms, result.Atoms...)
		result.Receipt.Commit()
	}
	if state.Offset != int64(len(data)) || len(atoms) != 3 {
		t.Fatalf("large context scan did not backfill to EOF: atoms=%#v state=%+v", atoms, *state)
	}
	for _, atom := range atoms {
		if atom.Payload["thread_id"] != "child-thread" || atom.Payload["model"] != "gpt-tail-child" || atom.Payload["effort"] != "medium" || atom.Payload["pricing_profile"] != "batch" || atom.Payload["service_tier"] != "flex" {
			t.Fatalf("large context metadata = %#v", atom.Payload)
		}
	}
}

func TestScanTranscriptDefersRecordLargerThanCycleBudgetAndResumes(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "rollout.jsonl")
	cursorPath := filepath.Join(root, "transcript-cursor.json")
	writeJSONL(t, path, usageCodex(1, 1, 0, 1))
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}
	state := &TranscriptScannerState{}
	cursor := NewTranscriptCursorStore(cursorPath)
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1 << 20})
	if err != nil || len(initial.Atoms) != 2 {
		t.Fatalf("initial scan = %+v, err=%v", initial, err)
	}
	if err := cursor.Prepare(source, initial.Receipt.before, initial.Receipt.after); err != nil {
		t.Fatal(err)
	}
	if err := cursor.Commit(source, initial.Receipt.after); err != nil {
		t.Fatal(err)
	}
	initial.Receipt.Commit()
	oversized := usageCodex(2, 20, 3, 4)
	oversized["padding"] = strings.Repeat("x", 512)
	appendJSONL(t, path, oversized, false)
	appendJSONL(t, path, usageCodex(3, 30, 5, 6), false)

	var resumed TranscriptScanResult
	var allAtoms []UsageAtom
	var parsedRecords int
	for cycle := 0; cycle < 20 && state.Offset < transcriptFixtureSize(t, path); cycle++ {
		cursor = NewTranscriptCursorStore(cursorPath)
		persisted, stateErr := cursor.State(source)
		if stateErr != nil {
			t.Fatalf("reload synthetic cursor on cycle %d: %v", cycle, stateErr)
		}
		state = &persisted
		resumed, err = ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 128})
		if err != nil {
			t.Fatalf("bounded resume cycle %d: %v", cycle, err)
		}
		if resumed.BytesRead > 128 {
			t.Fatalf("bounded resume cycle %d exceeded total I/O budget: %+v", cycle, resumed)
		}
		allAtoms = append(allAtoms, resumed.Atoms...)
		parsedRecords += resumed.RecordsParsed
		if resumed.Receipt != nil {
			if err := cursor.Prepare(source, resumed.Receipt.before, resumed.Receipt.after); err != nil {
				t.Fatalf("prepare cursor on cycle %d: %v", cycle, err)
			}
			if err := cursor.Commit(source, resumed.Receipt.after); err != nil {
				t.Fatalf("commit cursor on cycle %d: %v", cycle, err)
			}
			resumed.Receipt.Commit()
		}
	}
	if state.Offset != transcriptFixtureSize(t, path) || len(allAtoms) != 6 || parsedRecords != 2 {
		t.Fatalf("resume after repeated bounded scans = %+v state=%+v", resumed, *state)
	}
	if got := atomQuantities(allAtoms); got["input:none"] != 24 || got["input:read"] != 5 || got["output:none"] != 5 {
		t.Fatalf("resume quantities = %#v", got)
	}
}

func TestScanTranscriptResetsWhenPendingSuffixIsTruncatedAndRewritten(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pending-rewrite.jsonl")
	writeJSONL(t, path, usageClaude(1, "committed", 4, 2))
	source := TranscriptSource{Path: path, Provider: "claude", ThreadID: "synthetic"}
	state := &TranscriptScannerState{}
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1 << 20})
	if err != nil || len(initial.Atoms) != 2 {
		t.Fatalf("initial scan = %+v err=%v", initial, err)
	}
	initial.Receipt.Commit()
	committedOffset := state.Offset
	partial := usageClaude(2, "abandoned", 7, 3)
	partial["padding"] = strings.Repeat("x", 512)
	appendJSONL(t, path, partial, true)
	pending, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1 << 20})
	if err != nil || pending.RecordsParsed != 0 || len(pending.Receipt.after.PendingLine) == 0 {
		t.Fatalf("partial pending scan = %+v err=%v", pending, err)
	}
	pending.Receipt.Commit()
	oldPendingEnd := committedOffset + int64(len(state.PendingLine))
	replacement, err := json.Marshal(usageClaude(3, "replacement", 9, 5))
	if err != nil {
		t.Fatal(err)
	}
	replacement = append(replacement, '\n')
	original, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	newContents := append(append([]byte(nil), original[:committedOffset]...), replacement...)
	if int64(len(newContents)) <= committedOffset || int64(len(newContents)) >= oldPendingEnd {
		t.Fatalf("fixture must truncate only the pending suffix while keeping size past the committed offset: offset=%d old_pending_end=%d new_size=%d", committedOffset, oldPendingEnd, len(newContents))
	}
	if err := os.WriteFile(path, newContents, 0o600); err != nil {
		t.Fatal(err)
	}
	rewritten, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1 << 20})
	if err != nil || !rewritten.Reset || rewritten.Receipt.after.Offset != int64(len(newContents)) || len(rewritten.Atoms) != 4 {
		t.Fatalf("scan after pending-suffix rewrite = %+v err=%v", rewritten, err)
	}
	if got := atomQuantities(rewritten.Atoms); got["input:none"] != 13 || got["output:none"] != 7 {
		t.Fatalf("replayed committed and replacement usage = %#v", got)
	}
}

func TestScanTranscriptReplacementBackfillsWithinBounds(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout.jsonl")
	writeJSONL(t, path, usageCodex(1, 1, 0, 1))
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}
	state := &TranscriptScannerState{}
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || len(initial.Atoms) != 2 {
		t.Fatalf("initial scan = %+v, err=%v", initial, err)
	}
	initial.Receipt.Commit()
	replacement := make([]map[string]any, 0, 40)
	for index := 0; index < 35; index++ {
		replacement = append(replacement, map[string]any{"type": "event_msg", "payload": map[string]any{"padding": strings.Repeat("x", 120)}})
	}
	replacement = append(replacement, usageCodex(2, 20, 3, 4))
	writeJSONL(t, path, replacement...)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var atoms []UsageAtom
	reset := false
	for cycle := 0; cycle < 100 && state.Offset < int64(len(data)); cycle++ {
		result, scanErr := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1024, MaxRecords: 64})
		if scanErr != nil {
			t.Fatalf("large replacement scan cycle %d: %v", cycle, scanErr)
		}
		if result.BytesRead > 1024 || result.RecordsParsed > 64 {
			t.Fatalf("large replacement exceeded its scan budget: %+v", result)
		}
		reset = reset || result.Reset
		atoms = append(atoms, result.Atoms...)
		result.Receipt.Commit()
	}
	if state.Offset != int64(len(data)) || !reset || len(atoms) != 3 {
		t.Fatalf("large replacement did not backfill within bounds: reset=%t atoms=%#v state=%+v", reset, atoms, *state)
	}
}

func TestScanTranscriptWaitsForNewlineBeforeAcceptingValidFinalRecord(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout.jsonl")
	record, err := json.Marshal(usageCodex(1, 10, 2, 3))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, record, 0o600); err != nil {
		t.Fatal(err)
	}
	state := &TranscriptScannerState{}
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}, state, TranscriptScanOptions{})
	if err != nil || result.RecordsParsed != 0 || len(result.Atoms) != 0 || result.Receipt == nil {
		t.Fatalf("final non-newline record = %+v, err=%v", result, err)
	}
	if result.BudgetExhausted {
		t.Fatal("unterminated row at snapshot EOF was reported as exhausted budget")
	}
	result.Receipt.Commit()
	if state.Offset != 0 || !bytes.Equal(state.PendingLine, record) {
		t.Fatalf("final non-newline cursor = %+v, want offset 0 and pending row", state)
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write([]byte{10}); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	completed, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}, state, TranscriptScanOptions{})
	if err != nil || completed.RecordsParsed != 1 || len(completed.Atoms) != 3 {
		t.Fatalf("newline-completed final record = %+v, err=%v", completed, err)
	}
}

func TestScanTranscriptAcceptsFinalCarriageReturnTerminator(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-cr.jsonl")
	record, err := json.Marshal(usageCodex(1, 10, 2, 3))
	if err != nil {
		t.Fatal(err)
	}
	record = append(record, '\r')
	if err := os.WriteFile(path, record, 0o600); err != nil {
		t.Fatal(err)
	}
	state := &TranscriptScannerState{}
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}, state, TranscriptScanOptions{})
	if err != nil || result.RecordsParsed != 1 || len(result.Atoms) != 3 || result.Receipt == nil {
		t.Fatalf("CR-terminated final record = %+v, err=%v", result, err)
	}
	if result.Receipt.after.Offset != int64(len(record)) || len(result.Receipt.after.PendingLine) != 0 {
		t.Fatalf("CR-terminated cursor = %+v, want EOF offset %d and no pending bytes", result.Receipt.after, len(record))
	}
}

func TestScanTranscriptProducesSameResultAcrossEveryAppendSplit(t *testing.T) {
	row, err := json.Marshal(usageCodex(1, 10, 2, 3))
	if err != nil {
		t.Fatal(err)
	}
	for split := 0; split <= len(row); split++ {
		t.Run(fmt.Sprintf("split_%d", split), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "rollout.jsonl")
			if err := os.WriteFile(path, row[:split], 0o600); err != nil {
				t.Fatal(err)
			}
			state := &TranscriptScannerState{}
			partial, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}, state, TranscriptScanOptions{})
			if err != nil || partial.RecordsParsed != 0 || len(partial.Atoms) != 0 || partial.Receipt == nil {
				t.Fatalf("partial scan at byte %d = %+v, err=%v", split, partial, err)
			}
			if partial.BudgetExhausted {
				t.Fatalf("partial row at snapshot EOF was budget-exhausted at split %d", split)
			}
			partial.Receipt.Commit()
			file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := file.Write(append(append([]byte(nil), row[split:]...), '\n')); err != nil {
				_ = file.Close()
				t.Fatal(err)
			}
			if err := file.Close(); err != nil {
				t.Fatal(err)
			}
			complete, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}, state, TranscriptScanOptions{})
			if err != nil || complete.RecordsParsed != 1 || len(complete.Atoms) != 3 || complete.Receipt == nil {
				t.Fatalf("completed scan at byte %d = %+v, err=%v", split, complete, err)
			}
			if got := atomQuantities(complete.Atoms); !reflect.DeepEqual(got, map[string]float64{"input:none": 8, "input:read": 2, "output:none": 3}) {
				t.Fatalf("completed scan at byte %d quantities = %#v", split, got)
			}
			complete.Receipt.Commit()
			replay, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}, state, TranscriptScanOptions{})
			if err != nil || replay.RecordsParsed != 0 || len(replay.Atoms) != 0 {
				t.Fatalf("unchanged replay at byte %d = %+v, err=%v", split, replay, err)
			}
		})
	}
}

func TestScanTranscriptExactRecordLimitAtEOFDoesNotReportExhaustedBudget(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-exact-limit.jsonl")
	writeJSONL(t, path, usageCodex(1, 10, 2, 3))
	result, err := ScanTranscript(TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}, &TranscriptScannerState{}, TranscriptScanOptions{MaxRecords: 1})
	if err != nil || result.RecordsParsed != 1 || result.Receipt == nil {
		t.Fatalf("exact record-limit scan = %+v, err=%v", result, err)
	}
	if result.BudgetExhausted {
		t.Fatal("fully consumed snapshot was reported as exhausted budget")
	}
}

func TestRecentTranscriptCandidatesKeepsOversizedRecentFile(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "rollout.jsonl")
	if err := os.WriteFile(path, []byte(strings.Repeat("x", 4096)), 0o600); err != nil {
		t.Fatal(err)
	}
	candidates, err := recentTranscriptCandidates(context.Background(), root, transcriptDiscoveryBounds{maxFiles: 2, maxDirs: 2, maxBytes: 1024})
	if err == nil || !strings.Contains(err.Error(), "byte-budget") || len(candidates) != 1 || candidates[0].path != path {
		t.Fatalf("oversized recent candidate = %#v err=%v", candidates, err)
	}
}

func TestRecentTranscriptCandidatesResumesLargeDirectoryAcrossCadences(t *testing.T) {
	root := t.TempDir()
	const fileCount = 20
	for index := 0; index < fileCount; index++ {
		path := filepath.Join(root, fmt.Sprintf("rollout-%02d.jsonl", index))
		if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	limits := transcriptDiscoveryBounds{maxFiles: 20, maxDirs: 2, maxBytes: 1 << 20, maxEntries: 4}
	cursors := newTranscriptDiscoveryCursorStore()
	seen := make(map[string]struct{}, fileCount)
	for cadence := 0; cadence < fileCount; cadence++ {
		candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryEntryBudgetExhausted) {
			t.Fatalf("synthetic discovery cadence %d failed: %v", cadence, err)
		}
		if len(candidates) > limits.maxEntries {
			t.Fatalf("synthetic discovery cadence %d returned %d files, over its %d-entry budget", cadence, len(candidates), limits.maxEntries)
		}
		for _, candidate := range candidates {
			seen[candidate.path] = struct{}{}
		}
	}
	if len(seen) != fileCount {
		t.Fatalf("bounded synthetic discovery reached %d of %d files across cadences", len(seen), fileCount)
	}
	if _, ok := seen[filepath.Join(root, "rollout-19.jsonl")]; !ok {
		t.Fatal("repeated bounded discovery never reached the file beyond the initial page")
	}
}

func TestTranscriptDiscoveryCursorStoreBoundsRootState(t *testing.T) {
	parent := t.TempDir()
	cursors := newTranscriptDiscoveryCursorStore()
	firstRoot := filepath.Join(parent, "root-000")
	if err := os.Mkdir(firstRoot, 0o700); err != nil {
		t.Fatal(err)
	}
	cursors.save(firstRoot, transcriptDiscoveryDirectoryCursor{cookie: 7, pending: []string{"pending.jsonl"}})
	if err := cursors.saveFrontier(firstRoot, []string{firstRoot}); err != nil {
		t.Fatal(err)
	}
	for index := 1; index <= maxTranscriptDiscoveryFrontierRoots; index++ {
		root := filepath.Join(parent, fmt.Sprintf("root-%03d", index))
		if err := os.Mkdir(root, 0o700); err != nil {
			t.Fatal(err)
		}
		if err := cursors.saveFrontier(root, []string{root}); err != nil {
			t.Fatal(err)
		}
	}
	if len(cursors.frontiers) != maxTranscriptDiscoveryFrontierRoots {
		t.Fatalf("saved transcript roots = %d, want capped at %d", len(cursors.frontiers), maxTranscriptDiscoveryFrontierRoots)
	}
	if _, exists := cursors.frontiers[firstRoot]; exists {
		t.Fatal("oldest root frontier remained after bounded-cache eviction")
	}
	if _, exists := cursors.directories[firstRoot]; exists {
		t.Fatal("evicted root retained directory cursors that could skip names on restart")
	}
}

func TestTranscriptDiscoveryCursorReplacementDropsStaleSubtree(t *testing.T) {
	root := t.TempDir()
	child := filepath.Join(root, "child")
	grandchild := filepath.Join(child, "grandchild")
	if err := os.MkdirAll(grandchild, 0o700); err != nil {
		t.Fatal(err)
	}
	cursors := newTranscriptDiscoveryCursorStore()
	for path, cookie := range map[string]int64{child: 11, grandchild: 22} {
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		device, inode := fileIdentity(info)
		cursors.save(path, transcriptDiscoveryDirectoryCursor{
			cookie: cookie, device: device, inode: inode,
			modTimeNS: info.ModTime().UnixNano(), changeTimeNS: codexDirectoryChangeTimeNS(info),
			pending: []string{"rollout-stale.jsonl"},
		})
	}
	if err := os.RemoveAll(child); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(grandchild, 0o700); err != nil {
		t.Fatal(err)
	}
	replacementTime := time.Unix(1_800_000_000, 0)
	if err := os.Chtimes(child, replacementTime, replacementTime); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(child)
	if err != nil {
		t.Fatal(err)
	}
	if cursor := cursors.load(child, info); cursor.cookie != 0 || len(cursor.pending) != 0 {
		t.Fatalf("replacement child reused stale cursor: %+v", cursor)
	}
	if _, exists := cursors.directories[grandchild]; exists {
		t.Fatalf("replacement child retained stale descendant cursor: %+v", cursors.directories[grandchild])
	}
}

func TestTranscriptDiscoveryFrontierLeavesRoomForParentResume(t *testing.T) {
	queue := []transcriptPendingDirectory{{path: "parent"}}
	child := transcriptPendingDirectory{path: "child"}
	queue, scheduled := prependTranscriptDiscoveryChild(queue, child, 3)
	if !scheduled || len(queue) != 2 || queue[0].path != "child" {
		t.Fatalf("child scheduling = %v, queue=%+v, want prioritized child with one parent slot", scheduled, queue)
	}
	queue = append(queue[:1], append([]transcriptPendingDirectory{{path: "resumed-parent"}}, queue[1:]...)...)
	if len(queue) != 3 {
		t.Fatalf("frontier after parent resume = %d, want 3", len(queue))
	}
	queue, scheduled = prependTranscriptDiscoveryChild(queue, transcriptPendingDirectory{path: "too-deep"}, 3)
	if scheduled || len(queue) != 3 {
		t.Fatalf("frontier overran its cap: scheduled=%v queue=%+v", scheduled, queue)
	}
}

func TestRecentTranscriptCandidatesResumesAfterFileBudgetAcrossCadences(t *testing.T) {
	root := t.TempDir()
	const fileCount = 12
	for index := 0; index < fileCount; index++ {
		path := filepath.Join(root, fmt.Sprintf("rollout-%02d.jsonl", index))
		if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	limits := transcriptDiscoveryBounds{maxFiles: 3, maxDirs: 4, maxBytes: 1 << 20, maxEntries: 20}
	cursors := newTranscriptDiscoveryCursorStore()
	seen := make(map[string]struct{}, fileCount)
	for cadence := 0; cadence < fileCount; cadence++ {
		candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryFileBudgetExhausted) {
			t.Fatalf("synthetic discovery cadence %d failed: %v", cadence, err)
		}
		if len(candidates) > limits.maxFiles {
			t.Fatalf("synthetic discovery cadence %d returned %d files, over its %d-file budget", cadence, len(candidates), limits.maxFiles)
		}
		for _, candidate := range candidates {
			seen[candidate.path] = struct{}{}
		}
	}
	if len(seen) != fileCount {
		t.Fatalf("file-bounded synthetic discovery reached %d of %d files across cadences", len(seen), fileCount)
	}
}

func TestRecentTranscriptCandidatesFileBudgetDoesNotDuplicateQueuedSubdirectory(t *testing.T) {
	root := t.TempDir()
	child := filepath.Join(root, "z-child")
	if err := os.Mkdir(child, 0o700); err != nil {
		t.Fatal(err)
	}
	childTranscript := filepath.Join(child, "rollout-child.jsonl")
	if err := os.WriteFile(childTranscript, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"y-rollout-first.jsonl", "x-rollout-second.jsonl"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	limits := transcriptDiscoveryBounds{maxFiles: 1, maxDirs: 1, maxBytes: 1 << 20, maxEntries: 16}
	cursors := newTranscriptDiscoveryCursorStore()
	first, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
	if !errors.Is(err, errTranscriptDiscoveryFileBudgetExhausted) || len(first) != 1 {
		t.Fatalf("first bounded discovery = %#v, err=%v", first, err)
	}

	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	rootCursor := cursors.load(root, info)
	frontier := cursors.loadFrontier(root)
	childQueued := false
	for _, path := range frontier {
		childQueued = childQueued || path == child
	}
	childDeferred := false
	for _, name := range rootCursor.pending {
		childDeferred = childDeferred || name == filepath.Base(child)
	}
	if childQueued && childDeferred {
		t.Fatalf("child directory was both queued and deferred: frontier=%q pending=%q", frontier, rootCursor.pending)
	}

	for cadence := 0; cadence < 8; cadence++ {
		candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryFileBudgetExhausted) && !errors.Is(err, errTranscriptDiscoveryDirectoryBudgetExhausted) {
			t.Fatalf("synthetic continuation cadence %d failed: %v", cadence, err)
		}
		for _, candidate := range candidates {
			if candidate.path == childTranscript {
				return
			}
		}
	}
	t.Fatalf("bounded file-budget continuation never discovered child transcript %q", childTranscript)
}

func TestRecentTranscriptCandidatesRestartsAfterDeferredContinuationFailure(t *testing.T) {
	root := t.TempDir()
	child := filepath.Join(root, "z-child")
	if err := os.Mkdir(child, 0o700); err != nil {
		t.Fatal(err)
	}
	childTranscript := filepath.Join(child, "rollout-child.jsonl")
	if err := os.WriteFile(childTranscript, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	secondRootTranscript := filepath.Join(root, "x-rollout-second.jsonl")
	for _, name := range []string{"y-rollout-first.jsonl", "x-rollout-second.jsonl"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	cursors := newTranscriptDiscoveryCursorStore()
	failOnce := true
	cursors.deferNamesOverride = func(path string, names []string, complete bool, observed transcriptDiscoveryDirectoryCursor) error {
		if failOnce {
			failOnce = false
			return errors.New("synthetic deferred-name persistence failure")
		}
		return cursors.deferNamesDefault(path, names, complete, observed)
	}
	limits := transcriptDiscoveryBounds{maxFiles: 1, maxDirs: 1, maxBytes: 1 << 20, maxEntries: 16}
	if _, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors); err == nil || !strings.Contains(err.Error(), "synthetic deferred-name persistence failure") {
		t.Fatalf("synthetic deferred-name failure = %v", err)
	}

	want := map[string]struct{}{childTranscript: {}, secondRootTranscript: {}}
	seen := make(map[string]struct{}, len(want))
	for cadence := 0; cadence < 12 && len(seen) < len(want); cadence++ {
		candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryFileBudgetExhausted) && !errors.Is(err, errTranscriptDiscoveryDirectoryBudgetExhausted) {
			t.Fatalf("synthetic retry cadence %d failed: %v", cadence, err)
		}
		for _, candidate := range candidates {
			if _, wanted := want[candidate.path]; wanted {
				seen[candidate.path] = struct{}{}
			}
		}
	}
	if len(seen) != len(want) {
		t.Fatalf("retry after failed deferred-name persistence discovered %d of %d paths: seen=%q want=%q", len(seen), len(want), seen, want)
	}
}

func TestRecentTranscriptCandidatesRestartsWhenDirectoryChangesBeforeDeferral(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"z-child", "y-child"} {
		child := filepath.Join(root, name)
		if err := os.Mkdir(child, 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(child, "rollout-child.jsonl"), []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "a-rollout-existing.jsonl"), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	newTranscript := filepath.Join(root, "new-rollout-after-listing.jsonl")

	cursors := newTranscriptDiscoveryCursorStore()
	mutated := false
	cursors.deferNamesOverride = func(path string, names []string, complete bool, observed transcriptDiscoveryDirectoryCursor) error {
		if path == root && !mutated {
			mutated = true
			if err := os.WriteFile(newTranscript, []byte("{}\n"), 0o600); err != nil {
				return err
			}
		}
		return cursors.deferNamesDefault(path, names, complete, observed)
	}
	limits := transcriptDiscoveryBounds{maxFiles: 1, maxDirs: 1, maxBytes: 1 << 20, maxEntries: 16}
	_, firstErr := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
	if !mutated || !errors.Is(firstErr, errTranscriptDiscoveryDirectoryChanged) || !isTranscriptDiscoveryBudget(firstErr) {
		t.Fatalf("synthetic mutation between listing and deferral = mutated:%t err:%v; want partial directory-change error", mutated, firstErr)
	}

	for cadence := 0; cadence < 12; cadence++ {
		candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
		if err != nil && !isTranscriptDiscoveryBudget(err) {
			t.Fatalf("synthetic retry cadence %d failed: %v", cadence, err)
		}
		for _, candidate := range candidates {
			if candidate.path == newTranscript {
				return
			}
		}
	}
	t.Fatalf("discovery skipped transcript created between listing and deferred-name save: %s", newTranscript)
}

func TestRecentTranscriptCandidatesRestartsAfterChildFrontierPersistenceFailure(t *testing.T) {
	root := t.TempDir()
	want := make(map[string]struct{}, 2)
	for _, name := range []string{"z-child", "y-child"} {
		child := filepath.Join(root, name)
		if err := os.Mkdir(child, 0o700); err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(child, "rollout.jsonl")
		if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		want[path] = struct{}{}
	}

	cursors := newTranscriptDiscoveryCursorStore()
	failOnce := true
	cursors.deferNamesOverride = func(path string, names []string, complete bool, observed transcriptDiscoveryDirectoryCursor) error {
		if failOnce {
			failOnce = false
			return errors.New("synthetic child-frontier persistence failure")
		}
		return cursors.deferNamesDefault(path, names, complete, observed)
	}
	limits := transcriptDiscoveryBounds{maxFiles: 4, maxDirs: 1, maxBytes: 1 << 20, maxEntries: 16}
	if _, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors); err == nil || !strings.Contains(err.Error(), "synthetic child-frontier persistence failure") {
		t.Fatalf("synthetic child-frontier failure = %v", err)
	}

	seen := make(map[string]struct{}, len(want))
	for cadence := 0; cadence < 12 && len(seen) < len(want); cadence++ {
		candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryDirectoryBudgetExhausted) {
			t.Fatalf("synthetic child-frontier retry cadence %d failed: %v", cadence, err)
		}
		for _, candidate := range candidates {
			if _, wanted := want[candidate.path]; wanted {
				seen[candidate.path] = struct{}{}
			}
		}
	}
	if len(seen) != len(want) {
		t.Fatalf("retry after failed child-frontier persistence discovered %d of %d children: seen=%q want=%q", len(seen), len(want), seen, want)
	}
}

func TestRecentTranscriptCandidatesResumesDirectoryFrontierAcrossCadences(t *testing.T) {
	root := t.TempDir()
	const dirCount = 6
	for index := 0; index < dirCount; index++ {
		directory := filepath.Join(root, fmt.Sprintf("session-%02d", index))
		if err := os.Mkdir(directory, 0o700); err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(directory, "rollout.jsonl")
		if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	for _, maxDirs := range []int{1, 2} {
		t.Run(fmt.Sprintf("max_dirs_%d", maxDirs), func(t *testing.T) {
			limits := transcriptDiscoveryBounds{maxFiles: dirCount, maxDirs: maxDirs, maxBytes: 1 << 20, maxEntries: 64}
			cursors := newTranscriptDiscoveryCursorStore()
			seen := make(map[string]struct{}, dirCount)
			for cadence := 0; cadence < dirCount*2; cadence++ {
				candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
				if err != nil && !errors.Is(err, errTranscriptDiscoveryDirectoryBudgetExhausted) {
					t.Fatalf("synthetic directory-walk cadence %d failed: %v", cadence, err)
				}
				for _, candidate := range candidates {
					seen[candidate.path] = struct{}{}
				}
			}
			if len(seen) != dirCount {
				t.Fatalf("directory-bounded synthetic discovery reached %d of %d child directories across cadences", len(seen), dirCount)
			}
		})
	}
}

func TestRecentTranscriptCandidatesDepthFirstFrontierDoesNotStarveNestedDirectories(t *testing.T) {
	root := t.TempDir()
	nestedTranscript := filepath.Join(root, "z-child", "a-grandchild", "rollout-nested.jsonl")
	siblingTranscript := filepath.Join(root, "y-child", "rollout-sibling.jsonl")
	for _, path := range []string{nestedTranscript, siblingTranscript} {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Chtimes(filepath.Join(root, "z-child"), time.Unix(200, 0), time.Unix(200, 0)); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(filepath.Join(root, "y-child"), time.Unix(100, 0), time.Unix(100, 0)); err != nil {
		t.Fatal(err)
	}

	limits := transcriptDiscoveryBounds{maxFiles: 4, maxDirs: 1, maxBytes: 1 << 20, maxEntries: 16}
	cursors := newTranscriptDiscoveryCursorStore()
	seen := make(map[string]struct{}, 2)
	for cadence := 0; cadence < 12 && len(seen) < 2; cadence++ {
		candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryDirectoryBudgetExhausted) {
			t.Fatalf("synthetic nested discovery cadence %d failed: %v", cadence, err)
		}
		for _, candidate := range candidates {
			if candidate.path == nestedTranscript || candidate.path == siblingTranscript {
				seen[candidate.path] = struct{}{}
			}
		}
		if frontier := cursors.loadFrontier(root); len(frontier) > 3 {
			t.Fatalf("bounded depth-first frontier grew to %d entries: %q", len(frontier), frontier)
		}
	}
	if len(seen) != 2 {
		t.Fatalf("bounded discovery starved nested/sibling transcript: seen=%q want=%q", seen, []string{nestedTranscript, siblingTranscript})
	}
}

func TestRecentTranscriptCandidatesDeepTreeKeepsBoundedFrontierAndFindsDeferredSiblings(t *testing.T) {
	root := t.TempDir()
	const depth = 10
	want := make(map[string]struct{}, depth+1)
	parent := root
	for level := 0; level < depth; level++ {
		sibling := filepath.Join(parent, fmt.Sprintf("sibling-%02d", level))
		if err := os.Mkdir(sibling, 0o700); err != nil {
			t.Fatal(err)
		}
		siblingTranscript := filepath.Join(sibling, "rollout-sibling.jsonl")
		if err := os.WriteFile(siblingTranscript, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		want[siblingTranscript] = struct{}{}

		next := filepath.Join(parent, fmt.Sprintf("nested-%02d", level))
		if err := os.Mkdir(next, 0o700); err != nil {
			t.Fatal(err)
		}
		// Make the nested path the first DFS choice; siblings must remain queued
		// across directory-budget boundaries rather than being lost or starving.
		if err := os.Chtimes(next, time.Unix(int64(depth-level+100), 0), time.Unix(int64(depth-level+100), 0)); err != nil {
			t.Fatal(err)
		}
		parent = next
	}
	leafTranscript := filepath.Join(parent, "rollout-leaf.jsonl")
	if err := os.WriteFile(leafTranscript, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	want[leafTranscript] = struct{}{}

	limits := transcriptDiscoveryBounds{maxFiles: depth + 1, maxDirs: 1, maxBytes: 1 << 20, maxEntries: 64}
	cursors := newTranscriptDiscoveryCursorStore()
	seen := make(map[string]struct{}, len(want))
	maxFrontier := 0
	for cadence := 0; cadence < depth*4 && len(seen) < len(want); cadence++ {
		candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryDirectoryBudgetExhausted) {
			t.Fatalf("synthetic deep discovery cadence %d failed: %v", cadence, err)
		}
		for _, candidate := range candidates {
			if _, expected := want[candidate.path]; expected {
				seen[candidate.path] = struct{}{}
			}
		}
		frontier := cursors.loadFrontier(root)
		if len(frontier) > maxFrontier {
			maxFrontier = len(frontier)
		}
		if len(frontier) > depth*2+1 {
			t.Fatalf("synthetic deep frontier grew to %d paths for depth %d", len(frontier), depth)
		}
	}
	if len(seen) != len(want) {
		t.Fatalf("bounded deep traversal discovered %d of %d nested/sibling transcripts: seen=%q want=%q", len(seen), len(want), seen, want)
	}
	if maxFrontier <= 3 {
		t.Fatalf("synthetic deep fixture only exercised a shallow frontier: maximum=%d", maxFrontier)
	}
}

func TestRecentTranscriptCandidatesCancellationPreservesDiscoveredSubdirectory(t *testing.T) {
	root := t.TempDir()
	child := filepath.Join(root, "z-child")
	if err := os.Mkdir(child, 0o700); err != nil {
		t.Fatal(err)
	}
	childTranscript := filepath.Join(child, "rollout.jsonl")
	if err := os.WriteFile(childTranscript, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "a-rollout.jsonl"), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	limits := transcriptDiscoveryBounds{maxFiles: 8, maxDirs: 4, maxBytes: 1 << 20, maxEntries: 16}
	cursors := newTranscriptDiscoveryCursorStore()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	interrupted := &cancelOnErrContext{Context: ctx, cancel: cancel, limit: 7}
	if _, err := recentTranscriptCandidatesWithCursor(interrupted, root, limits, cursors); !errors.Is(err, context.Canceled) {
		t.Fatalf("synthetic discovery cancellation = %v, want context.Canceled", err)
	}
	candidates, err := recentTranscriptCandidatesWithCursor(context.Background(), root, limits, cursors)
	if err != nil {
		t.Fatalf("resume after synthetic cancellation: %v", err)
	}
	for _, candidate := range candidates {
		if candidate.path == childTranscript {
			return
		}
	}
	t.Fatalf("resumed discovery omitted the already-discovered child directory %q: %#v", childTranscript, candidates)
}

func TestTranscriptDiscoveryBudgetErrorsRemainPartialCoverage(t *testing.T) {
	for _, err := range []error{
		errTranscriptDiscoveryEntryBudgetExhausted,
		errTranscriptDiscoveryFileBudgetExhausted,
		errTranscriptDiscoveryDirectoryBudgetExhausted,
		errTranscriptDiscoveryFrontierBudgetExceeded,
		errTranscriptDiscoveryDirectoryChanged,
		errors.New("transcript-discovery-byte-budget-exhausted"),
	} {
		if !isTranscriptDiscoveryBudget(err) {
			t.Errorf("discovery budget %q was not classified as partial coverage", err)
		}
	}
}

func TestRecentTranscriptCandidatesSortsPastOversizedFilenameByModTime(t *testing.T) {
	root := t.TempDir()
	oldLarge := filepath.Join(root, "z-old-large.jsonl")
	newSmall := filepath.Join(root, "a-new-small.jsonl")
	if err := os.WriteFile(oldLarge, []byte(strings.Repeat("x", 4096)), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(newSmall, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	oldTime := time.Unix(100, 0)
	newTime := oldTime.Add(time.Hour)
	if err := os.Chtimes(oldLarge, oldTime, oldTime); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(newSmall, newTime, newTime); err != nil {
		t.Fatal(err)
	}
	candidates, err := recentTranscriptCandidates(context.Background(), root, transcriptDiscoveryBounds{maxFiles: 4, maxDirs: 2, maxBytes: 1024})
	if err == nil || !strings.Contains(err.Error(), "byte-budget") || len(candidates) != 2 {
		t.Fatalf("oversized discovery candidates = %#v err=%v", candidates, err)
	}
	if candidates[0].path != newSmall || candidates[1].path != oldLarge {
		t.Fatalf("candidates were not sorted by modification time: %#v", candidates)
	}
}

func TestRecentTranscriptCandidatesPrioritizesNewerDirectoryWithinBound(t *testing.T) {
	root := t.TempDir()
	oldDir := filepath.Join(root, "z-old")
	newDir := filepath.Join(root, "a-new")
	if err := os.MkdirAll(oldDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(newDir, 0o700); err != nil {
		t.Fatal(err)
	}
	oldFile := filepath.Join(oldDir, "old.jsonl")
	newFile := filepath.Join(newDir, "new.jsonl")
	if err := os.WriteFile(oldFile, []byte(strings.Repeat("x", 4096)), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(newFile, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	oldTime := time.Unix(100, 0)
	newTime := oldTime.Add(time.Hour)
	if err := os.Chtimes(oldDir, oldTime, oldTime); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(newDir, newTime, newTime); err != nil {
		t.Fatal(err)
	}
	candidates, err := recentTranscriptCandidates(context.Background(), root, transcriptDiscoveryBounds{maxFiles: 2, maxDirs: 2, maxBytes: 1024})
	if !errors.Is(err, errTranscriptDiscoveryDirectoryBudgetExhausted) || len(candidates) != 1 || candidates[0].path != newFile {
		t.Fatalf("newer directory was not prioritized: %#v err=%v", candidates, err)
	}
}

func TestTranscriptCursorStoreCrashReplayAndCommit(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "claude.jsonl")
	statePath := filepath.Join(root, "transcript-cursors.json")
	writeJSONL(t, path, usageClaude(1, "message", 10, 2))
	source := TranscriptSource{Path: path, Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	state := &TranscriptScannerState{}
	scan, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || scan.Receipt == nil {
		t.Fatalf("scan = %+v, err = %v", scan, err)
	}
	store := NewTranscriptCursorStore(statePath)
	if err := store.PrepareBatchWithNextSource([]transcriptCursorChange{{source: source, before: scan.Receipt.before, after: scan.Receipt.after}}, transcriptCursorKey(source)); err != nil {
		t.Fatal(err)
	}
	// A new owner must discard an uncommitted prepare. It cannot advance the
	// source cursor before the corresponding SQLite append is known durable.
	restarted := NewTranscriptCursorStore(statePath)
	loaded, err := restarted.State(source)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Offset != 0 || loaded.Path != "" {
		t.Fatalf("prepared cursor was promoted after restart: %+v", loaded)
	}
	if nextSource, err := restarted.NextSource(); err != nil || nextSource != "" {
		t.Fatalf("prepared source schedule was promoted after restart: next=%q err=%v", nextSource, err)
	}
	if err := store.Commit(source, scan.Receipt.after); err == nil {
		t.Fatal("stale pre-restart owner committed a discarded pending cursor")
	}
	if err := store.Rollback(source); err == nil {
		t.Fatal("stale pre-restart owner rolled back a newer cursor operation")
	}
	owner := NewTranscriptCursorStore(statePath)
	if err := owner.Prepare(source, TranscriptScannerState{}, scan.Receipt.after); err != nil {
		t.Fatal(err)
	}
	if err := owner.PrepareBatchWithNextSource(nil, transcriptCursorKey(source)); err != nil {
		t.Fatal(err)
	}
	if err := owner.Commit(source, scan.Receipt.after); err != nil {
		t.Fatal(err)
	}
	committed := NewTranscriptCursorStore(statePath)
	loaded, err = committed.State(source)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Offset != scan.Receipt.after.Offset || loaded.Inode == 0 {
		t.Fatalf("committed cursor = %+v, want offset %d", loaded, scan.Receipt.after.Offset)
	}
	if nextSource, err := committed.NextSource(); err != nil || nextSource != transcriptCursorKey(source) {
		t.Fatalf("fallback-prepared schedule = %q, err=%v", nextSource, err)
	}
}

func TestScanTranscriptReplaysSyntheticSourceAfterInodeReplacement(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "claude.jsonl")
	source := TranscriptSource{Path: path, Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	writeJSONL(t, path, usageClaude(1, "before-replacement", 10, 2))
	state := &TranscriptScannerState{}
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || initial.Receipt == nil || len(initial.Atoms) != 2 {
		t.Fatalf("initial synthetic scan = atoms:%d receipt:%t err:%v", len(initial.Atoms), initial.Receipt != nil, err)
	}
	initial.Receipt.Commit()
	oldInfo, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	oldDevice, oldInode := fileIdentity(oldInfo)

	temporary, err := os.CreateTemp(root, ".replacement-*.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	encoder := json.NewEncoder(temporary)
	if err := encoder.Encode(usageClaude(2, "after-replacement", 20, 4)); err != nil {
		t.Fatal(err)
	}
	if err := temporary.Sync(); err != nil {
		t.Fatal(err)
	}
	if err := temporary.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		t.Fatal(err)
	}
	newInfo, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	newDevice, newInode := fileIdentity(newInfo)
	if oldDevice == newDevice && oldInode == newInode {
		t.Skip("filesystem did not replace the synthetic transcript inode")
	}

	replacement, err := ScanTranscript(source, state, TranscriptScanOptions{})
	if err != nil || replacement.Receipt == nil || !replacement.Reset || len(replacement.Atoms) != 2 {
		t.Fatalf("replacement synthetic scan = reset:%t atoms:%d receipt:%t err:%v", replacement.Reset, len(replacement.Atoms), replacement.Receipt != nil, err)
	}
	if replacement.Atoms[0].EventID != "claude:"+path+":after-replacement" || replacement.Atoms[1].EventID != replacement.Atoms[0].EventID {
		t.Fatalf("replacement scan did not emit the new source event: %+v", replacement.Atoms)
	}
	replacement.Receipt.Commit()
	if state.Inode != newInode || state.Offset != newInfo.Size() || state.ResetCount != 1 {
		t.Fatalf("committed replacement cursor = inode:%d offset:%d resets:%d; want inode:%d offset:%d resets:1", state.Inode, state.Offset, state.ResetCount, newInode, newInfo.Size())
	}
}

func TestTranscriptCursorStoreScheduleOnlyCommitAndRollback(t *testing.T) {
	statePath := filepath.Join(t.TempDir(), "transcript-cursors.json")
	store := NewTranscriptCursorStore(statePath)
	if err := store.PrepareBatchWithSchedules(nil, "live-next", "history-next", 2); err != nil {
		t.Fatalf("prepare schedule-only fairness turn: %v", err)
	}
	if err := store.RollbackBatch(nil); err != nil {
		t.Fatalf("rollback schedule-only fairness turn: %v", err)
	}
	rolledBack, err := NewTranscriptCursorStore(statePath).Schedule()
	if err != nil {
		t.Fatalf("read rolled-back schedule: %v", err)
	}
	if rolledBack != (transcriptCursorSchedule{}) {
		t.Fatalf("schedule-only rollback changed durable fairness state: %+v", rolledBack)
	}
	if err := store.PrepareBatchWithSchedules(nil, "live-next", "history-next", 2); err != nil {
		t.Fatalf("prepare schedule-only fairness turn for commit: %v", err)
	}
	if err := store.CommitBatchWithCodexForkRepairRoots(nil, nil); err != nil {
		t.Fatalf("commit schedule-only fairness turn: %v", err)
	}
	committed, err := NewTranscriptCursorStore(statePath).Schedule()
	if err != nil {
		t.Fatalf("read committed schedule: %v", err)
	}
	want := transcriptCursorSchedule{NextSource: "live-next", NextHistoricalSource: "history-next", LiveTranscriptPriorityRuns: 2}
	if committed != want {
		t.Fatalf("committed fairness schedule = %+v, want %+v", committed, want)
	}
}

func TestTranscriptCursorRepairRootsCommitAtomicallyAndReadLegacyMarker(t *testing.T) {
	statePath := filepath.Join(t.TempDir(), "transcript-cursors.json")
	legacyRoot := strings.Repeat("a", 64)
	newRoot := strings.Repeat("b", 64)
	legacyPath := codexForkRepairMarkerPath(statePath)
	legacyMarker := struct {
		Version        int      `json:"version"`
		CompletedRoots []string `json:"completed_roots"`
	}{Version: 1, CompletedRoots: []string{legacyRoot}}
	data, err := json.Marshal(legacyMarker)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(legacyPath, data, 0o600); err != nil {
		t.Fatal(err)
	}
	store := NewTranscriptCursorStore(statePath)
	if completed, err := store.CompletedCodexForkRepairRoots(); err != nil || len(completed) != 1 {
		t.Fatalf("legacy completed roots = %v err=%v", completed, err)
	} else if _, ok := completed[legacyRoot]; !ok {
		t.Fatalf("legacy completed roots omitted %q: %v", legacyRoot, completed)
	}
	if err := os.Remove(legacyPath); err != nil {
		t.Fatal(err)
	}
	if err := store.CommitBatchWithCodexForkRepairRoots(nil, []string{newRoot}); err != nil {
		t.Fatal(err)
	}
	completed, err := NewTranscriptCursorStore(statePath).CompletedCodexForkRepairRoots()
	if err != nil {
		t.Fatal(err)
	}
	if len(completed) != 1 {
		t.Fatalf("new sidecar completed roots = %v, want exactly one", completed)
	}
	if _, ok := completed[newRoot]; !ok {
		t.Fatalf("new sidecar completed roots omitted %q: %v", newRoot, completed)
	}
	if _, err := os.Stat(legacyPath); !os.IsNotExist(err) {
		t.Fatalf("atomic cursor commit recreated the legacy marker: %v", err)
	}
}

func TestTranscriptCursorStoreStateKeepsOtherLocalPendingCommit(t *testing.T) {
	root := t.TempDir()
	statePath := filepath.Join(root, "transcript-cursors.json")
	sourceA := TranscriptSource{Path: filepath.Join(root, "claude-a.jsonl"), Provider: "claude"}
	sourceB := TranscriptSource{Path: filepath.Join(root, "claude-b.jsonl"), Provider: "claude"}
	nextA := TranscriptScannerState{Path: sourceA.Path, Offset: 17, Prefix: []byte("a")}
	store := NewTranscriptCursorStore(statePath)
	if err := store.Prepare(sourceA, TranscriptScannerState{}, nextA); err != nil {
		t.Fatal(err)
	}
	// The collector reads every source before committing the batch. An
	// unchanged second source must not discard source A's local prepare token.
	if _, err := store.State(sourceB); err != nil {
		t.Fatal(err)
	}
	if err := store.Commit(sourceA, nextA); err != nil {
		t.Fatalf("pending source A could not commit after source B state read: %v", err)
	}
	loaded, err := NewTranscriptCursorStore(statePath).State(sourceA)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Offset != nextA.Offset || string(loaded.Prefix) != string(nextA.Prefix) {
		t.Fatalf("source A cursor = %+v, want %+v", loaded, nextA)
	}
}

func TestTranscriptCursorStoreRejectsStaleCommitWithoutClobberingNewPrepare(t *testing.T) {
	root := t.TempDir()
	statePath := filepath.Join(root, "transcript-cursors.json")
	source := TranscriptSource{Path: filepath.Join(root, "claude.jsonl"), Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	first := TranscriptScannerState{Path: source.Path, Offset: 10, Prefix: []byte("first")}
	second := TranscriptScannerState{Path: source.Path, Offset: 20, Prefix: []byte("second")}
	third := TranscriptScannerState{Path: source.Path, Offset: 30, Prefix: []byte("third")}
	store := NewTranscriptCursorStore(statePath)
	if err := store.Prepare(source, TranscriptScannerState{}, first); err != nil {
		t.Fatal(err)
	}
	if err := store.Commit(source, first); err != nil {
		t.Fatal(err)
	}
	if err := store.Prepare(source, first, second); err != nil {
		t.Fatal(err)
	}
	if err := store.Prepare(source, first, third); err == nil {
		t.Fatal("stale transcript operation replaced a newer pending state")
	}
	if err := store.Commit(source, third); err == nil {
		t.Fatal("stale transcript receipt committed a different pending state")
	}
	if err := store.Commit(source, second); err != nil {
		t.Fatalf("new transcript receipt could not commit after stale rejection: %v", err)
	}
	loaded, err := NewTranscriptCursorStore(statePath).State(source)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Offset != second.Offset || string(loaded.Prefix) != string(second.Prefix) {
		t.Fatalf("stale commit changed durable cursor: %+v", loaded)
	}
	if err := store.Prepare(source, first, third); err == nil {
		t.Fatal("stale transcript operation prepared over the committed cursor")
	}
}

func TestTranscriptCursorStoreRejectsRollbackFromOwnerWithoutPendingToken(t *testing.T) {
	root := t.TempDir()
	statePath := filepath.Join(root, "transcript-cursors.json")
	source := TranscriptSource{Path: filepath.Join(root, "claude.jsonl"), Provider: "claude", SourceID: "claude", ThreadID: "thread"}
	next := TranscriptScannerState{Path: source.Path, Offset: 42, Prefix: []byte("prefix")}
	owner := NewTranscriptCursorStore(statePath)
	if err := owner.Prepare(source, TranscriptScannerState{}, next); err != nil {
		t.Fatal(err)
	}

	// A restarted or otherwise stale owner has no local operation token. It must
	// not be able to remove the active owner's pending state, or the next
	// restart would replay without a durable receipt boundary.
	stale := NewTranscriptCursorStore(statePath)
	if err := stale.Rollback(source); err == nil {
		t.Fatal("owner without a pending token removed another owner's cursor")
	}
	data, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatal(err)
	}
	var pendingDisk transcriptCursorDisk
	if err := json.Unmarshal(data, &pendingDisk); err != nil {
		t.Fatal(err)
	}
	if _, ok := pendingDisk.Pending[transcriptCursorKey(source)]; !ok {
		t.Fatalf("stale rollback removed the active pending cursor: %+v", pendingDisk)
	}
	if err := owner.Commit(source, next); err != nil {
		t.Fatalf("active owner could not commit after stale rollback refusal: %v", err)
	}
	committed, err := NewTranscriptCursorStore(statePath).State(source)
	if err != nil {
		t.Fatal(err)
	}
	if committed.Offset != next.Offset || string(committed.Prefix) != string(next.Prefix) {
		t.Fatalf("committed cursor = %+v, want %+v", committed, next)
	}
}

func TestTranscriptCursorStoreDiscardedPendingJournalAdvancesGeneration(t *testing.T) {
	root := t.TempDir()
	statePath := filepath.Join(root, "transcript-cursors.json")
	source := TranscriptSource{Path: filepath.Join(root, "claude.jsonl"), Provider: "claude"}
	next := TranscriptScannerState{Path: source.Path, Offset: 7}
	owner := NewTranscriptCursorStore(statePath)
	if err := owner.Prepare(source, TranscriptScannerState{}, next); err != nil {
		t.Fatal(err)
	}
	stale := NewTranscriptCursorStore(statePath)
	if _, err := stale.State(source); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatal(err)
	}
	var disk transcriptCursorDisk
	if err := json.Unmarshal(data, &disk); err != nil {
		t.Fatal(err)
	}
	if disk.Generation != 2 {
		t.Fatalf("discarded pending generation = %d, want 2", disk.Generation)
	}
	if err := owner.Commit(source, next); err == nil {
		t.Fatal("stale owner committed discarded pending cursor")
	}
	if err := stale.Prepare(source, TranscriptScannerState{}, next); err != nil {
		t.Fatalf("new owner could not prepare after pending recovery: %v", err)
	}
}

func TestTranscriptCursorStoreRejectsMalformedState(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript-cursors.json")
	if err := os.WriteFile(path, []byte(`{"version":99}`), 0o600); err != nil {
		t.Fatal(err)
	}
	store := NewTranscriptCursorStore(path)
	if _, err := store.State(TranscriptSource{Path: "x", Provider: "codex"}); err == nil {
		t.Fatal("malformed transcript cursor was accepted")
	}
}

func TestTranscriptCursorWriteFailureDoesNotAdvanceMemory(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "cursor.json")
	if err := os.Mkdir(path, 0o700); err != nil {
		t.Fatal(err)
	}
	source := TranscriptSource{Path: filepath.Join(root, "claude.jsonl"), Provider: "claude"}
	store := NewTranscriptCursorStore(path)
	next := TranscriptScannerState{Path: source.Path, Offset: 7}
	if err := store.Prepare(source, TranscriptScannerState{}, next); err == nil {
		t.Fatal("prepare unexpectedly succeeded with a directory sidecar path")
	}
	if len(store.pending) != 0 || len(store.pendingTokens) != 0 {
		t.Fatalf("failed prepare advanced in-memory state: pending=%v tokens=%v", store.pending, store.pendingTokens)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := store.Prepare(source, TranscriptScannerState{}, next); err != nil {
		t.Fatal(err)
	}
	if err := store.Commit(source, next); err != nil {
		t.Fatal(err)
	}
}

func TestTranscriptCursorRollbackFailureDoesNotWedgeTheOwner(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "cursor.json")
	source := TranscriptSource{Path: filepath.Join(root, "claude.jsonl"), Provider: "claude"}
	next := TranscriptScannerState{Path: source.Path, Offset: 7}
	store := NewTranscriptCursorStore(path)
	if err := store.Prepare(source, TranscriptScannerState{}, next); err != nil {
		t.Fatal(err)
	}
	backup := filepath.Join(root, "pending.json")
	if err := os.Rename(path, backup); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(path, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := store.Rollback(source); err == nil {
		t.Fatal("rollback unexpectedly succeeded with an unreadable sidecar")
	}
	if len(store.pending) != 0 || len(store.pendingTokens) != 0 {
		t.Fatalf("failed rollback left transcript cursor pending: pending=%v tokens=%v", store.pending, store.pendingTokens)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(backup, path); err != nil {
		t.Fatal(err)
	}
	loaded, err := store.State(source)
	if err != nil {
		t.Fatalf("cursor did not repair after sidecar recovery: %v", err)
	}
	if loaded.Offset != 0 {
		t.Fatalf("repaired cursor promoted pending state: %+v", loaded)
	}
	if err := store.Prepare(source, TranscriptScannerState{}, next); err != nil {
		t.Fatalf("cursor could not prepare after repair: %v", err)
	}
	if err := store.Commit(source, next); err != nil {
		t.Fatalf("cursor could not commit after repair: %v", err)
	}
}

func TestTranscriptCursorStoreStateReloadsSameSizeSameMtimeReplacement(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "cursor.json")
	source := TranscriptSource{Path: filepath.Join(root, "claude.jsonl"), Provider: "claude"}
	first := TranscriptScannerState{Path: source.Path, Offset: 1}
	second := TranscriptScannerState{Path: source.Path, Offset: 2}
	store := NewTranscriptCursorStore(path)
	if err := store.Prepare(source, TranscriptScannerState{}, first); err != nil {
		t.Fatal(err)
	}
	if err := store.Commit(source, first); err != nil {
		t.Fatal(err)
	}
	if got, err := store.State(source); err != nil || got.Offset != first.Offset {
		t.Fatalf("initial cursor state = %+v, err=%v", got, err)
	}
	oldInfo, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	oldDevice, oldInode := fileIdentity(oldInfo)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	replacement := bytes.Replace(data, []byte(`"Offset":1`), []byte(`"Offset":2`), 1)
	if len(replacement) != len(data) || bytes.Equal(replacement, data) {
		t.Fatalf("could not construct same-size cursor replacement: old=%q new=%q", data, replacement)
	}
	temp, err := os.CreateTemp(root, ".cursor-replacement-*")
	if err != nil {
		t.Fatal(err)
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if _, err := temp.Write(replacement); err != nil {
		t.Fatal(err)
	}
	if err := temp.Sync(); err != nil {
		t.Fatal(err)
	}
	if err := temp.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(tempName, path); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, oldInfo.ModTime(), oldInfo.ModTime()); err != nil {
		t.Fatal(err)
	}
	newInfo, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	newDevice, newInode := fileIdentity(newInfo)
	if oldDevice == newDevice && oldInode == newInode {
		t.Skip("filesystem did not replace the sidecar inode")
	}
	if oldInfo.Size() != newInfo.Size() || oldInfo.ModTime() != newInfo.ModTime() {
		t.Fatalf("replacement signature changed size/mtime: old=(%d,%s) new=(%d,%s)", oldInfo.Size(), oldInfo.ModTime(), newInfo.Size(), newInfo.ModTime())
	}
	got, err := store.State(source)
	if err != nil {
		t.Fatal(err)
	}
	if got.Offset != second.Offset {
		t.Fatalf("same-signature replacement returned stale cursor %+v, want %+v", got, second)
	}
}

func TestCodexForkRepairFrontierBoundsCombinedPendingAndBlockedFiles(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	rootID := codexForkRepairRootID(root)
	paths := make([]string, maxCodexForkRepairFileFrontier)
	for index := range paths {
		paths[index] = filepath.Join(root, "rollout-synthetic.jsonl")
	}
	frontier := codexForkRepairFrontierState{Roots: map[string]codexForkRepairCatalogState{
		rootID: {Root: root, PendingFiles: paths[:len(paths)/2], BlockedFiles: paths[len(paths)/2:]},
	}}
	if err := validateCodexForkRepairFrontier(frontier); err != nil {
		t.Fatalf("frontier at combined file bound was rejected: %v", err)
	}
	overBound := frontier.Roots[rootID]
	overBound.BlockedFiles = append(overBound.BlockedFiles, filepath.Join(root, "rollout-overflow.jsonl"))
	frontier.Roots[rootID] = overBound
	if err := validateCodexForkRepairFrontier(frontier); err == nil || !strings.Contains(err.Error(), "catalog exceeds its bound") {
		t.Fatalf("frontier over combined file bound was accepted: %v", err)
	}
}

func TestCodexForkRepairFrontierRejectsPathsOutsideCatalogAndNonRepairSources(t *testing.T) {
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	inside := filepath.Join(root, "child", "rollout-synthetic.jsonl")
	outside := filepath.Join(t.TempDir(), "rollout-outside.jsonl")
	rootID := codexForkRepairRootID(root)
	valid := codexForkRepairFrontierState{Roots: map[string]codexForkRepairCatalogState{
		rootID: {
			Root: root, PendingDirs: []string{root, filepath.Dir(inside)}, PendingFiles: []string{inside},
			BlockedFiles: []string{inside}, Sources: []TranscriptSource{{Path: inside, Provider: "codex", CodexForkRepairOnly: true}},
			Directory:         &codexForkRepairDirectoryState{Path: root},
			PausedDirectories: []codexForkRepairDirectoryState{{Path: filepath.Dir(inside)}},
		},
	}}
	if err := validateCodexForkRepairFrontier(valid); err != nil {
		t.Fatalf("valid synthetic frontier was rejected: %v", err)
	}
	tests := []struct {
		name   string
		mutate func(*codexForkRepairCatalogState)
	}{
		{name: "pending directory escapes root", mutate: func(catalog *codexForkRepairCatalogState) { catalog.PendingDirs = []string{outside} }},
		{name: "pending file escapes root", mutate: func(catalog *codexForkRepairCatalogState) { catalog.PendingFiles = []string{outside} }},
		{name: "blocked file escapes root", mutate: func(catalog *codexForkRepairCatalogState) { catalog.BlockedFiles = []string{outside} }},
		{name: "source escapes root", mutate: func(catalog *codexForkRepairCatalogState) { catalog.Sources[0].Path = outside }},
		{name: "source is not repair only", mutate: func(catalog *codexForkRepairCatalogState) { catalog.Sources[0].CodexForkRepairOnly = false }},
		{name: "active directory escapes root", mutate: func(catalog *codexForkRepairCatalogState) { catalog.Directory.Path = outside }},
		{name: "paused directory escapes root", mutate: func(catalog *codexForkRepairCatalogState) { catalog.PausedDirectories[0].Path = outside }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			frontier := valid
			catalog := frontier.Roots[rootID]
			catalog.PendingDirs = append([]string(nil), catalog.PendingDirs...)
			catalog.PendingFiles = append([]string(nil), catalog.PendingFiles...)
			catalog.BlockedFiles = append([]string(nil), catalog.BlockedFiles...)
			catalog.Sources = append([]TranscriptSource(nil), catalog.Sources...)
			catalog.PausedDirectories = append([]codexForkRepairDirectoryState(nil), catalog.PausedDirectories...)
			if catalog.Directory != nil {
				directory := *catalog.Directory
				catalog.Directory = &directory
			}
			test.mutate(&catalog)
			frontier.Roots[rootID] = catalog
			if err := validateCodexForkRepairFrontier(frontier); err == nil {
				t.Fatal("malformed synthetic frontier was accepted")
			}
		})
	}
}

func TestScanTranscriptRepairOptionCannotBypassSessionsRootValidation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-outside-sessions.jsonl")
	writeJSONL(t, path, map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "child", "forked_from_id": "parent", "thread_source": "subagent"},
	})
	for _, test := range []struct {
		name    string
		source  TranscriptSource
		options TranscriptScanOptions
	}{
		{name: "source flag", source: TranscriptSource{Path: path, Provider: "codex", CodexForkRepairOnly: true}},
		{name: "scan option", source: TranscriptSource{Path: path, Provider: "codex"}, options: TranscriptScanOptions{CodexForkRepairOnly: true}},
	} {
		t.Run(test.name, func(t *testing.T) {
			state := TranscriptScannerState{CodexUsage: UsageCounters{"input_tokens": 11}}
			before := cloneTranscriptState(state)
			if _, err := ScanTranscript(test.source, &state, test.options); err == nil {
				t.Fatal("repair mode accepted a transcript outside a sessions root")
			}
			if state.Path != before.Path || state.Offset != before.Offset || state.CodexUsage["input_tokens"] != before.CodexUsage["input_tokens"] {
				t.Fatalf("rejected repair scan mutated cursor state: got=%+v want=%+v", state, before)
			}
		})
	}
}

func TestCodexForkRepairFrontierOverflowPreservesExistingCursorSidecar(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cursor.json")
	store := NewTranscriptCursorStore(path)
	disk := transcriptCursorDisk{
		Version: 1,
		States: map[string]TranscriptScannerState{
			// Leave room under the enlarged sidecar cap but not for the frontier below.
			"synthetic-large-cursor": {PendingLine: []byte(strings.Repeat("x", 95<<20))},
		},
	}
	if err := store.writeDiskLocked(disk); err != nil {
		t.Fatalf("seed bounded synthetic cursor sidecar: %v", err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(t.TempDir(), ".codex", "sessions")
	rootID := codexForkRepairRootID(root)
	longPrefix := strings.Repeat("p", maxCodexForkRepairPathBytes-len(root)-1-3)
	pendingDirs := make([]string, 400)
	for index := range pendingDirs {
		pendingDirs[index] = filepath.Join(root, fmt.Sprintf("%s%03d", longPrefix, index))
	}
	frontier := codexForkRepairFrontierState{Roots: map[string]codexForkRepairCatalogState{
		rootID: {Root: root, PendingDirs: pendingDirs},
	}}
	if err := validateCodexForkRepairFrontier(frontier); err != nil {
		t.Fatalf("synthetic frontier itself should fit its limit: %v", err)
	}
	if err := store.SaveCodexForkRepairFrontier(frontier); err == nil {
		t.Fatal("frontier unexpectedly fit beside the existing large cursor state")
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(after, before) {
		t.Fatal("failed frontier save replaced the previously durable cursor sidecar")
	}
}
