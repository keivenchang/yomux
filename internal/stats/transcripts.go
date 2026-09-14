package stats

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

const (
	DefaultTranscriptMaxBytes   int64 = 4 * 1024 * 1024
	DefaultTranscriptMaxRecords       = 4096
)

// TranscriptSource identifies one provider JSONL transcript and its attribution.
type TranscriptSource struct {
	Path     string
	Provider string
	SourceID string
	ThreadID string
	AgentID  string
}

// TranscriptScannerState is intentionally caller-owned and in-memory. It is the
// future persistence seam, but this phase does not write it to disk.
type TranscriptScannerState struct {
	Path          string
	Device        uint64
	Inode         uint64
	Offset        int64
	Prefix        []byte
	ResetCount    int
	ClaudeUsage   map[string]UsageCounters
	CodexUsage    UsageCounters
	CodexModel    string
	CodexThreadID string
	CodexForkID   string
}

type TranscriptScanOptions struct {
	MaxBytes   int64
	MaxRecords int
}

type TranscriptScanResult struct {
	Atoms           []UsageAtom
	BytesRead       int64
	RecordsParsed   int
	FilesRead       int
	Reset           bool
	BudgetExhausted bool
	Receipt         *TranscriptScanReceipt
}

// TranscriptScanReceipt preserves the rollback/commit seam without persisting
// scanner state. Commit is required before the caller reuses the state.
type TranscriptScanReceipt struct {
	state  *TranscriptScannerState
	before TranscriptScannerState
	after  TranscriptScannerState
	used   bool
}

func (r *TranscriptScanReceipt) Commit() {
	if r == nil || r.used {
		return
	}
	*r.state = cloneTranscriptState(r.after)
	r.used = true
}

func (r *TranscriptScanReceipt) Rollback() {
	if r == nil || r.used {
		return
	}
	*r.state = cloneTranscriptState(r.before)
	r.used = true
}

func ScanTranscript(source TranscriptSource, state *TranscriptScannerState, options TranscriptScanOptions) (TranscriptScanResult, error) {
	if state == nil {
		return TranscriptScanResult{}, fmt.Errorf("transcript scanner state is required")
	}
	if source.Provider != "claude" && source.Provider != "codex" {
		return TranscriptScanResult{}, fmt.Errorf("unsupported transcript provider %q", source.Provider)
	}
	if source.Path == "" {
		return TranscriptScanResult{}, fmt.Errorf("transcript path is required")
	}
	if options.MaxBytes <= 0 {
		options.MaxBytes = DefaultTranscriptMaxBytes
	}
	if options.MaxRecords <= 0 {
		options.MaxRecords = DefaultTranscriptMaxRecords
	}
	file, err := os.Open(filepath.Clean(source.Path))
	if err != nil {
		return TranscriptScanResult{}, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return TranscriptScanResult{}, err
	}

	before := cloneTranscriptState(*state)
	after := cloneTranscriptState(*state)
	after.Path = source.Path
	reset := before.Path != "" && (after.Path != before.Path || after.Offset > info.Size() || identityChanged(after, info))
	if reset {
		after.Offset = 0
		after.Prefix = nil
		after.ClaudeUsage = nil
		after.CodexUsage = nil
		after.ResetCount++
	}
	if !reset && after.Prefix != nil && !prefixMatches(file, after.Prefix) {
		reset = true
		after.Offset = 0
		after.Prefix = nil
		after.ClaudeUsage = nil
		after.CodexUsage = nil
		after.ResetCount++
	}
	after.Device, after.Inode = fileIdentity(info)
	if after.Offset == info.Size() {
		return TranscriptScanResult{FilesRead: 0, Reset: reset, Receipt: &TranscriptScanReceipt{state, before, after, false}}, nil
	}
	if _, err := file.Seek(after.Offset, io.SeekStart); err != nil {
		return TranscriptScanResult{}, err
	}
	result := TranscriptScanResult{FilesRead: 1, Reset: reset}
	reader := bufio.NewReader(file)
	for result.RecordsParsed < options.MaxRecords && result.BytesRead < options.MaxBytes {
		line, readErr := reader.ReadBytes('\n')
		if len(line) == 0 && readErr != nil {
			break
		}
		if !bytes.HasSuffix(line, []byte{'\n'}) {
			result.BudgetExhausted = result.BudgetExhausted || readErr == nil || result.BytesRead+int64(len(line)) >= options.MaxBytes
			break
		}
		if result.BytesRead+int64(len(line)) > options.MaxBytes {
			result.BudgetExhausted = true
			break
		}
		result.BytesRead += int64(len(line))
		after.Offset += int64(len(line))
		result.RecordsParsed++
		var record map[string]any
		if err := json.Unmarshal(bytes.TrimSpace(line), &record); err != nil {
			if readErr == io.EOF {
				break
			}
			continue
		}
		atoms, err := normalizeTranscriptRecord(source, &after, record)
		if err != nil {
			return TranscriptScanResult{}, err
		}
		result.Atoms = append(result.Atoms, atoms...)
		if readErr == io.EOF {
			break
		}
	}
	result.BudgetExhausted = result.BudgetExhausted || result.RecordsParsed >= options.MaxRecords || result.BytesRead >= options.MaxBytes
	after.Prefix = readPrefix(source.Path)
	result.Receipt = &TranscriptScanReceipt{state, before, after, false}
	return result, nil
}

func normalizeTranscriptRecord(source TranscriptSource, state *TranscriptScannerState, record map[string]any) ([]UsageAtom, error) {
	observed := transcriptTimestamp(record["timestamp"])
	metadata := UsageMetadata{Provider: source.Provider, ThreadID: source.ThreadID, Model: source.ThreadID}
	if source.Provider == "claude" {
		message, _ := record["message"].(map[string]any)
		usage, _ := message["usage"].(map[string]any)
		if model, ok := message["model"].(string); ok {
			metadata.Model = model
		}
		if id, ok := message["id"].(string); ok {
			metadata.ThreadID = id
		}
		counters := UsageCounters{}
		for _, key := range []string{"input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"} {
			if value, ok := usage[key].(float64); ok {
				counters[key] = value
			}
		}
		if creation, ok := usage["cache_creation"].(map[string]any); ok {
			for key, aliases := range map[string][]string{
				"cache_creation_input_tokens_5m": {"ephemeral_5m_input_tokens", "ephemeral5mInputTokens"},
				"cache_creation_input_tokens_1h": {"ephemeral_1h_input_tokens", "ephemeral1hInputTokens"},
			} {
				for _, alias := range aliases {
					if value, ok := creation[alias].(float64); ok {
						counters[key] = value
						break
					}
				}
			}
		}
		previous := state.ClaudeUsage
		if previous == nil {
			previous = make(map[string]UsageCounters)
			state.ClaudeUsage = previous
		}
		delta, _, err := DeltaUsageCounters(previous[metadata.ThreadID], counters)
		if err != nil {
			return nil, err
		}
		previous[metadata.ThreadID] = counters
		atoms, err := NormalizeClaudeUsageMap(anyCounters(delta), observed, metadata)
		addAgentIDs(atoms, source.AgentIDOrThread())
		return atoms, err
	}
	payload, _ := record["payload"].(map[string]any)
	if record["type"] == "session_meta" {
		if id, ok := payload["id"].(string); ok {
			state.CodexThreadID = id
		}
		if model, ok := payload["model"].(string); ok {
			state.CodexModel = model
		}
		if sourceInfo, ok := payload["source"].(map[string]any); ok {
			if subagent, ok := sourceInfo["subagent"].(map[string]any); ok {
				if spawn, ok := subagent["thread_spawn"].(map[string]any); ok {
					if parent, ok := spawn["parent_thread_id"].(string); ok {
						state.CodexForkID = parent
					}
				}
			}
		}
		return nil, nil
	}
	if record["type"] == "turn_context" {
		if model, ok := payload["model"].(string); ok {
			state.CodexModel = model
		}
		return nil, nil
	}
	info, _ := payload["info"].(map[string]any)
	total, _ := info["total_token_usage"].(map[string]any)
	model := state.CodexModel
	if model == "" {
		model = metadata.Model
	}
	if value, ok := payload["model"].(string); ok {
		model = value
	}
	metadata.Model = model
	if state.CodexThreadID != "" {
		metadata.ThreadID = state.CodexThreadID
	}
	metadata.ForkID = state.CodexForkID
	if state.CodexUsage == nil {
		state.CodexUsage = UsageCounters{}
	}
	counters := UsageCounters{}
	for key, value := range map[string]string{"input_tokens": "input_tokens", "cached_input_tokens": "cached_input_tokens", "output_tokens": "output_tokens"} {
		if number, ok := total[value].(float64); ok {
			counters[key] = number
		}
	}
	delta, _, err := DeltaUsageCounters(state.CodexUsage, counters)
	if err != nil {
		return nil, err
	}
	state.CodexUsage = counters
	atoms, err := NormalizeCodexUsageMap(anyCounters(delta), observed, metadata)
	addAgentIDs(atoms, source.AgentIDOrThread())
	return atoms, err
}

func (source TranscriptSource) AgentIDOrThread() string {
	if source.AgentID != "" {
		return source.AgentID
	}
	return source.ThreadID
}

func addAgentIDs(atoms []UsageAtom, agentID string) {
	if agentID == "" {
		return
	}
	for index := range atoms {
		atoms[index].Payload["agent_id"] = agentID
	}
}

func transcriptTimestamp(value any) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case int:
		return float64(typed)
	case string:
		parsed, err := time.Parse(time.RFC3339Nano, typed)
		if err == nil {
			return float64(parsed.UnixNano()) / 1e9
		}
	}
	return 0
}

func anyCounters(counters UsageCounters) map[string]any {
	values := make(map[string]any, len(counters))
	for key, value := range counters {
		values[key] = value
	}
	return values
}

func cloneTranscriptState(state TranscriptScannerState) TranscriptScannerState {
	copyState := state
	copyState.Prefix = append([]byte(nil), state.Prefix...)
	copyState.ClaudeUsage = make(map[string]UsageCounters, len(state.ClaudeUsage))
	for key, counters := range state.ClaudeUsage {
		copyState.ClaudeUsage[key] = cloneCounters(counters)
	}
	copyState.CodexUsage = cloneCounters(state.CodexUsage)
	copyState.CodexModel = state.CodexModel
	copyState.CodexThreadID = state.CodexThreadID
	copyState.CodexForkID = state.CodexForkID
	return copyState
}

func cloneCounters(counters UsageCounters) UsageCounters {
	copyCounters := UsageCounters{}
	for key, value := range counters {
		copyCounters[key] = value
	}
	return copyCounters
}

func fileIdentity(info os.FileInfo) (uint64, uint64) {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, 0
	}
	return uint64(stat.Dev), uint64(stat.Ino)
}
func identityChanged(state TranscriptScannerState, info os.FileInfo) bool {
	device, inode := fileIdentity(info)
	return state.Device != 0 && (state.Device != device || state.Inode != inode)
}
func readPrefix(path string) []byte {
	file, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer file.Close()
	buffer := make([]byte, 64*1024)
	count, _ := file.Read(buffer)
	return buffer[:count]
}
func prefixMatches(file *os.File, prefix []byte) bool {
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return false
	}
	current := make([]byte, len(prefix))
	count, _ := file.Read(current)
	return bytes.Equal(current[:count], prefix[:count])
}
