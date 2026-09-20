package stats

import (
	"bytes"
	"context"
	"encoding/json"
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
		if metadata["model"] != "fallback-thread" || metadata["thread_id"] != "fallback-thread" {
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

func TestScanTranscriptFreshLargeCodexFileUsesBoundedTail(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout-large.jsonl")
	records := make([]map[string]any, 0, 700)
	records = append(records, map[string]any{"type": "session_meta", "payload": map[string]any{
		"id": "large-thread", "model": "gpt-large",
	}})
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
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "fallback-thread"}
	state := &TranscriptScannerState{}
	result, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 2048, MaxRecords: 64})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Atoms) != 3 {
		t.Fatalf("large fresh scan atoms = %#v result=%+v", result.Atoms, result)
	}
	result.Receipt.Commit()
	quantities := atomQuantities(result.Atoms)
	if quantities["input:none"] != 4 || quantities["input:read"] != 8 || quantities["output:none"] != 5 {
		t.Fatalf("large fresh scan quantities = %#v result=%+v state=%+v", quantities, result, *state)
	}
	if state.CodexModel != "gpt-large" || state.CodexThreadID != "large-thread" {
		t.Fatalf("large fresh scan context = %+v", *state)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if state.Offset != int64(len(data)) || result.BytesRead > 2048 || !result.BudgetExhausted {
		t.Fatalf("large fresh scan exceeded bounded tail: state=%+v result=%+v", *state, result)
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

func TestScanTranscriptSkipsOversizedCompleteRecordAndResumes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout.jsonl")
	writeJSONL(t, path, usageCodex(1, 1, 0, 1))
	source := TranscriptSource{Path: path, Provider: "codex", ThreadID: "thread"}
	state := &TranscriptScannerState{}
	initial, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1 << 20})
	if err != nil || len(initial.Atoms) != 2 {
		t.Fatalf("initial scan = %+v, err=%v", initial, err)
	}
	initial.Receipt.Commit()
	before := state.Offset
	oversized := usageCodex(2, 20, 3, 4)
	oversized["padding"] = strings.Repeat("x", 512)
	appendJSONL(t, path, oversized, false)
	appendJSONL(t, path, usageCodex(3, 30, 5, 6), false)

	skipped, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 128})
	if err != nil || len(skipped.Atoms) != 0 || !skipped.BudgetExhausted || skipped.RecordsParsed != 1 {
		t.Fatalf("oversized scan = %+v, err=%v", skipped, err)
	}
	if skipped.Receipt.after.Offset <= before {
		t.Fatalf("oversized record did not advance cursor: before=%d after=%d", before, skipped.Receipt.after.Offset)
	}
	skipped.Receipt.Commit()

	resumed, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1 << 20})
	if err != nil || len(resumed.Atoms) != 3 || resumed.RecordsParsed != 1 {
		t.Fatalf("resume after oversized scan = %+v, err=%v", resumed, err)
	}
	if got := atomQuantities(resumed.Atoms); got["input:none"] != 24 || got["input:read"] != 5 || got["output:none"] != 5 {
		t.Fatalf("resume quantities = %#v", got)
	}
}

func TestScanTranscriptReplacementUsesBoundedTail(t *testing.T) {
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
	result, err := ScanTranscript(source, state, TranscriptScanOptions{MaxBytes: 1024, MaxRecords: 64})
	if err != nil || len(result.Atoms) != 3 || !result.Reset {
		t.Fatalf("large replacement scan = %+v, err=%v", result, err)
	}
	if result.BytesRead > 1024 || !result.BudgetExhausted {
		t.Fatalf("large replacement exceeded bounded tail: %+v", result)
	}
}

func TestScanTranscriptAcceptsValidFinalRecordWithoutNewline(t *testing.T) {
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
	if err != nil || result.RecordsParsed != 1 || len(result.Atoms) != 3 {
		t.Fatalf("final non-newline record = %+v, err=%v", result, err)
	}
	result.Receipt.Commit()
	if state.Offset != int64(len(record)) {
		t.Fatalf("final non-newline cursor = %d, want %d", state.Offset, len(record))
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
	if err != nil || len(candidates) != 1 || candidates[0].path != newFile {
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
	if err := store.Prepare(source, scan.Receipt.before, scan.Receipt.after); err != nil {
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
