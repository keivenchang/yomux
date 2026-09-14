package stats

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"testing"
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

func copyObservedTranscript(t *testing.T, provider, relativePath string) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	fixture := filepath.Join(filepath.Dir(thisFile), "..", "..", "tests", "fixtures", "current_model_usage", "observed."+provider+".jsonl")
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
	if len(result.Atoms) != 4 || result.Atoms[2].EventID == result.Atoms[1].EventID {
		t.Fatalf("nested cache identities = %#v", result.Atoms)
	}
	repeat, err := ScanTranscript(TranscriptSource{Path: path, Provider: "claude", ThreadID: "thread"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil || !reflect.DeepEqual(atomIDs(result.Atoms), atomIDs(repeat.Atoms)) {
		t.Fatalf("non-deterministic IDs = %v / %v, err = %v", atomIDs(result.Atoms), atomIDs(repeat.Atoms), err)
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
	partial, err := ScanTranscript(claudeSource, claudeState, TranscriptScanOptions{})
	if err != nil || partial.RecordsParsed != 0 || partial.BytesRead != 0 {
		t.Fatalf("partial scan = %+v, err = %v", partial, err)
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
