package stats

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"sync"
	"syscall"
	"time"
)

const (
	DefaultTranscriptMaxBytes   int64 = 4 * 1024 * 1024
	DefaultTranscriptMaxRecords       = 4096
	transcriptParsedTailBytes         = 64 * 1024
)

// TranscriptSource identifies one provider JSONL transcript and its attribution.
type TranscriptSource struct {
	Path     string
	Provider string
	SourceID string
	ThreadID string
	AgentID  string
}

// TranscriptScannerState is the durable cursor for one Claude/Codex JSONL
// source. Prefix and file identity make replacement/truncation detectable;
// usage folds make replay after a crash idempotent.
type TranscriptScannerState struct {
	Path   string
	Device uint64
	Inode  uint64
	Offset int64
	Prefix []byte
	// ParsedTail is the bounded suffix immediately before Offset. Prefix alone
	// cannot detect an in-place rewrite after the first 64 KiB, which would make
	// the next incremental scan resume from a different record boundary.
	ParsedTail    []byte
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

// TranscriptCursorStore journals Claude/Codex scanner state beside the runtime
// state. A prepared state is never promoted until the corresponding usage
// append has committed. If the process crashes between those operations, the
// next owner discards the prepared state and replays the source; SQLite's
// stable event IDs then deduplicate anything that reached the database.
type TranscriptCursorStore struct {
	path          string
	mu            sync.Mutex
	current       map[string]TranscriptScannerState
	pending       map[string]TranscriptScannerState
	pendingTokens map[string]string
	generation    uint64
}

type transcriptCursorDisk struct {
	Version       int                               `json:"version"`
	Generation    uint64                            `json:"generation,omitempty"`
	States        map[string]TranscriptScannerState `json:"states"`
	Pending       map[string]TranscriptScannerState `json:"pending,omitempty"`
	PendingTokens map[string]string                 `json:"pending_tokens,omitempty"`
}

func NewTranscriptCursorStore(path string) *TranscriptCursorStore {
	return &TranscriptCursorStore{path: path, current: make(map[string]TranscriptScannerState), pending: make(map[string]TranscriptScannerState), pendingTokens: make(map[string]string)}
}

func transcriptCursorKey(source TranscriptSource) string {
	return source.Provider + "\x00" + filepath.Clean(source.Path)
}

func (s *TranscriptCursorStore) readDiskLocked() (transcriptCursorDisk, error) {
	data, err := os.ReadFile(s.path)
	if os.IsNotExist(err) {
		return transcriptCursorDisk{Version: 1, States: map[string]TranscriptScannerState{}, Pending: map[string]TranscriptScannerState{}, PendingTokens: map[string]string{}}, nil
	}
	if err != nil {
		return transcriptCursorDisk{}, err
	}
	var disk transcriptCursorDisk
	if err := json.Unmarshal(data, &disk); err != nil || (disk.Version != 0 && disk.Version != 1) {
		return transcriptCursorDisk{}, fmt.Errorf("transcript cursor state is malformed")
	}
	if disk.States == nil {
		disk.States = make(map[string]TranscriptScannerState)
	}
	if disk.PendingTokens == nil {
		disk.PendingTokens = make(map[string]string)
	}
	return disk, nil
}

func (s *TranscriptCursorStore) State(source TranscriptSource) (TranscriptScannerState, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var result TranscriptScannerState
	err := withSidecarLock(s.path, func() error {
		disk, err := s.readDiskLocked()
		if err != nil {
			return err
		}
		if len(disk.Pending) > 0 && len(s.pending) == 0 {
			cleanGeneration := disk.Generation + 1
			if err := atomicSidecarWrite(s.path, transcriptCursorDisk{Version: 1, Generation: cleanGeneration, States: disk.States}); err != nil {
				return err
			}
			disk.Generation = cleanGeneration
		}
		s.current, s.generation = disk.States, disk.Generation
		// Keep pending operations owned by this store instance. A collector can
		// prepare one changed source, inspect another unchanged source, and then
		// commit all receipts together. Clearing the local token map during that
		// second State call would make the durable prepare impossible to commit.
		if len(s.pending) == 0 {
			s.pending, s.pendingTokens = make(map[string]TranscriptScannerState), make(map[string]string)
		}
		result = cloneTranscriptState(disk.States[transcriptCursorKey(source)])
		return nil
	})
	return result, err
}

func (s *TranscriptCursorStore) Prepare(source TranscriptSource, before, after TranscriptScannerState) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := transcriptCursorKey(source)
	if reflect.DeepEqual(before, after) {
		return nil
	}
	return withSidecarLock(s.path, func() error {
		disk, err := s.readDiskLocked()
		if err != nil {
			return err
		}
		if _, pending := disk.Pending[key]; pending {
			return fmt.Errorf("transcript cursor state is pending")
		}
		current := disk.States[key]
		if !reflect.DeepEqual(cloneTranscriptState(current), cloneTranscriptState(before)) {
			return fmt.Errorf("transcript cursor state is stale")
		}
		token := strconv.FormatUint(disk.Generation+1, 10) + "-" + strconv.FormatInt(time.Now().UnixNano(), 10)
		pending := make(map[string]TranscriptScannerState, len(disk.Pending)+1)
		for k, v := range disk.Pending {
			pending[k] = v
		}
		pending[key] = cloneTranscriptState(after)
		pendingTokens := make(map[string]string, len(disk.PendingTokens)+1)
		for k, v := range disk.PendingTokens {
			pendingTokens[k] = v
		}
		pendingTokens[key] = token
		staged := transcriptCursorDisk{Version: 1, Generation: disk.Generation + 1, States: disk.States, Pending: pending, PendingTokens: pendingTokens}
		if err := atomicSidecarWrite(s.path, staged); err != nil {
			return err
		}
		s.current, s.generation = disk.States, staged.Generation
		s.pending, s.pendingTokens = pending, pendingTokens
		return nil
	})
}

func (s *TranscriptCursorStore) Commit(source TranscriptSource, expected TranscriptScannerState) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := transcriptCursorKey(source)
	return withSidecarLock(s.path, func() error {
		disk, err := s.readDiskLocked()
		if err != nil {
			return err
		}
		token := s.pendingTokens[key]
		state, ok := disk.Pending[key]
		if !ok || token == "" || disk.PendingTokens[key] != token {
			return fmt.Errorf("transcript cursor state is stale")
		}
		if !reflect.DeepEqual(state, cloneTranscriptState(expected)) {
			return fmt.Errorf("transcript cursor state is stale")
		}
		states := make(map[string]TranscriptScannerState, len(disk.States)+1)
		for k, v := range disk.States {
			states[k] = v
		}
		states[key] = cloneTranscriptState(state)
		pending := make(map[string]TranscriptScannerState, len(disk.Pending))
		pendingTokens := make(map[string]string, len(disk.PendingTokens))
		for k, v := range disk.Pending {
			if k != key {
				pending[k] = v
			}
		}
		for k, v := range disk.PendingTokens {
			if k != key {
				pendingTokens[k] = v
			}
		}
		if err := atomicSidecarWrite(s.path, transcriptCursorDisk{Version: 1, Generation: disk.Generation + 1, States: states, Pending: pending, PendingTokens: pendingTokens}); err != nil {
			return err
		}
		s.current, s.generation = states, disk.Generation+1
		s.pending, s.pendingTokens = pending, pendingTokens
		return nil
	})
}

func (s *TranscriptCursorStore) Rollback(source TranscriptSource) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := transcriptCursorKey(source)
	return withSidecarLock(s.path, func() error {
		disk, err := s.readDiskLocked()
		if err != nil {
			return err
		}
		localToken := s.pendingTokens[key]
		diskToken, diskHasToken := disk.PendingTokens[key]
		_, diskHasState := disk.Pending[key]
		if localToken == "" {
			if diskHasToken || diskHasState {
				return fmt.Errorf("transcript cursor state is stale")
			}
			return nil
		}
		if !diskHasToken || !diskHasState || diskToken != localToken {
			return fmt.Errorf("transcript cursor state is stale")
		}
		delete(disk.Pending, key)
		delete(disk.PendingTokens, key)
		if err := atomicSidecarWrite(s.path, transcriptCursorDisk{Version: 1, Generation: disk.Generation + 1, States: disk.States, Pending: disk.Pending, PendingTokens: disk.PendingTokens}); err != nil {
			return err
		}
		delete(s.pending, key)
		delete(s.pendingTokens, key)
		return nil
	})
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
		after.ParsedTail = nil
		after.ClaudeUsage = nil
		after.CodexUsage = nil
		after.CodexModel = ""
		after.CodexThreadID = ""
		after.CodexForkID = ""
		after.ResetCount++
	}
	if !reset && after.Prefix != nil && !prefixMatches(file, after.Prefix) {
		reset = true
		after.Offset = 0
		after.Prefix = nil
		after.ParsedTail = nil
		after.ClaudeUsage = nil
		after.CodexUsage = nil
		after.CodexModel = ""
		after.CodexThreadID = ""
		after.CodexForkID = ""
		after.ResetCount++
	}
	if !reset && len(after.ParsedTail) > 0 && !parsedTailMatches(file, after.Offset, after.ParsedTail) {
		reset = true
		after.Offset = 0
		after.Prefix = nil
		after.ParsedTail = nil
		after.ClaudeUsage = nil
		after.CodexUsage = nil
		after.CodexModel = ""
		after.CodexThreadID = ""
		after.CodexForkID = ""
		after.ResetCount++
	}
	after.Device, after.Inode = fileIdentity(info)
	tailSeed := false
	if after.Offset == 0 && source.Provider == "codex" && info.Size() > options.MaxBytes {
		// A newly discovered Codex rollout can be hundreds of megabytes or
		// larger. Start at a bounded, complete-record tail so current usage is
		// visible in the first cadence instead of waiting for the scanner to
		// replay the entire historical file.
		tailSeed = true
		start := info.Size() - options.MaxBytes
		if start < 0 {
			start = 0
		}
		if err := seedTranscriptContext(file, source, &after); err != nil {
			return TranscriptScanResult{}, err
		}
		if _, err := file.Seek(start, io.SeekStart); err != nil {
			return TranscriptScanResult{}, err
		}
		prefixReader := bufio.NewReader(file)
		if start > 0 {
			discarded, complete, readErr := discardTranscriptLine(prefixReader)
			if readErr != nil {
				return TranscriptScanResult{}, readErr
			}
			if !complete && discarded == 0 {
				return TranscriptScanResult{}, io.ErrUnexpectedEOF
			}
			start += discarded
		}
		after.Offset = start
	}
	if after.Offset == info.Size() {
		return TranscriptScanResult{FilesRead: 0, Reset: reset, Receipt: &TranscriptScanReceipt{state, before, after, false}}, nil
	}
	if _, err := file.Seek(after.Offset, io.SeekStart); err != nil {
		return TranscriptScanResult{}, err
	}
	result := TranscriptScanResult{FilesRead: 1, Reset: reset, BudgetExhausted: tailSeed}
	reader := bufio.NewReader(file)
	for result.RecordsParsed < options.MaxRecords && result.BytesRead < options.MaxBytes {
		remaining := options.MaxBytes - result.BytesRead
		line, lineBytes, complete, oversized, readErr := readTranscriptLine(reader, remaining)
		if readErr != nil {
			return TranscriptScanResult{}, readErr
		}
		if len(line) == 0 && !complete {
			break
		}
		if oversized {
			if complete {
				after.Offset += lineBytes
				result.RecordsParsed++
			}
			result.BytesRead = options.MaxBytes
			result.BudgetExhausted = true
			break
		}
		result.BytesRead += lineBytes
		after.Offset += lineBytes
		result.RecordsParsed++
		var record map[string]any
		if err := json.Unmarshal(bytes.TrimSpace(line), &record); err != nil {
			if !complete {
				// A final non-newline fragment may be a valid complete JSON record,
				// but an actually partial write must remain retryable.
				result.BytesRead -= lineBytes
				after.Offset -= lineBytes
				result.RecordsParsed--
				break
			}
			continue
		}
		atoms, err := normalizeTranscriptRecord(source, &after, record, tailSeed && len(after.CodexUsage) == 0)
		if err != nil {
			return TranscriptScanResult{}, err
		}
		result.Atoms = append(result.Atoms, atoms...)
		if !complete {
			break
		}
	}
	result.BudgetExhausted = result.BudgetExhausted || result.RecordsParsed >= options.MaxRecords || result.BytesRead >= options.MaxBytes
	after.Prefix = readPrefix(source.Path)
	after.ParsedTail = readParsedTail(source.Path, after.Offset)
	result.Receipt = &TranscriptScanReceipt{state, before, after, false}
	return result, nil
}

// readTranscriptLine consumes one physical record without retaining more than
// the caller's remaining byte budget. Oversized records are drained to their
// newline and reported to the scanner for cursor advancement, while a final
// non-newline fragment is retained only when it fits the budget.
func readTranscriptLine(reader *bufio.Reader, maxBytes int64) ([]byte, int64, bool, bool, error) {
	var line []byte
	var total int64
	oversized := false
	for {
		fragment, err := reader.ReadSlice('\n')
		total += int64(len(fragment))
		if !oversized {
			if total > maxBytes {
				oversized = true
				line = nil
			} else {
				line = append(line, fragment...)
			}
		}
		if err == nil {
			return line, total, true, oversized, nil
		}
		if err == bufio.ErrBufferFull {
			continue
		}
		if err == io.EOF {
			return line, total, false, oversized, nil
		}
		return nil, total, false, oversized, err
	}
}

func discardTranscriptLine(reader *bufio.Reader) (int64, bool, error) {
	var total int64
	for {
		fragment, err := reader.ReadSlice('\n')
		total += int64(len(fragment))
		if err == nil {
			return total, true, nil
		}
		if err == bufio.ErrBufferFull {
			continue
		}
		if err == io.EOF {
			return total, false, nil
		}
		return total, false, err
	}
}

func seedTranscriptContext(file *os.File, source TranscriptSource, state *TranscriptScannerState) error {
	if source.Provider != "codex" {
		return nil
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return err
	}
	reader := bufio.NewReaderSize(file, transcriptParsedTailBytes)
	var read int
	for read < transcriptParsedTailBytes {
		line, lineBytes, complete, oversized, err := readTranscriptLine(reader, int64(transcriptParsedTailBytes-read))
		read += int(lineBytes)
		if err != nil {
			return err
		}
		if len(line) == 0 {
			if !complete {
				break
			}
			continue
		}
		if oversized {
			if !complete {
				break
			}
			continue
		}
		var record map[string]any
		if json.Unmarshal(bytes.TrimSpace(line), &record) != nil {
			if !complete {
				break
			}
			continue
		}
		typeName, _ := record["type"].(string)
		if typeName == "session_meta" || typeName == "turn_context" {
			if _, err := normalizeTranscriptRecord(source, state, record, false); err != nil {
				return err
			}
		}
		if !complete {
			break
		}
	}
	return nil
}

func normalizeTranscriptRecord(source TranscriptSource, state *TranscriptScannerState, record map[string]any, tailSeed bool) ([]UsageAtom, error) {
	observed := transcriptTimestamp(record["timestamp"])
	metadata := UsageMetadata{Provider: source.Provider, ThreadID: source.ThreadID, Model: source.ThreadID}
	if source.Provider == "codex" {
		// Python's Codex usage atoms use the OpenAI catalog identity even though
		// the source file is a Codex transcript. Keep the execution source and
		// endpoint explicit so materialization resolves the same pricing row and
		// preserves the provider attribution shown by Python.
		metadata.Provider = "openai"
		metadata.ExecutionSource = "codex"
		metadata.Endpoint = "responses"
	}
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
	if record["type"] == "token_usage_record" {
		// Current Codex rollouts publish per-response usage together with a
		// cumulative thread_token_usage snapshot. Prefer the cumulative thread
		// counter for incremental scans, while using the per-response usage when
		// a fresh large file was tail-seeded (the old event_msg path follows the
		// same rule with last_token_usage).
		threadID, _ := payload["thread_id"].(string)
		if threadID != "" {
			if state.CodexThreadID != "" && state.CodexThreadID != threadID {
				state.CodexUsage = nil
				state.CodexForkID = ""
			}
			state.CodexThreadID = threadID
		}
		if model, ok := payload["model"].(string); ok && model != "" {
			state.CodexModel = model
		}
		if state.CodexThreadID != "" {
			metadata.ThreadID = state.CodexThreadID
		}
		metadata.ForkID = state.CodexForkID
		if model := state.CodexModel; model != "" {
			metadata.Model = model
		}
		threadUsage, hasThreadUsage := payload["thread_token_usage"].(map[string]any)
		usage, hasUsage := payload["usage"].(map[string]any)
		turnUsage, hasTurnUsage := payload["turn_token_usage"].(map[string]any)
		current := UsageCounters{}
		baseline := UsageCounters{}
		cumulative := false
		switch {
		case hasThreadUsage:
			current = codexUsageCounters(threadUsage)
			baseline = current
			cumulative = true
			if tailSeed && hasUsage {
				current = codexUsageCounters(usage)
				cumulative = false
			}
		case hasUsage:
			current = codexUsageCounters(usage)
		case hasTurnUsage:
			current = codexUsageCounters(turnUsage)
		default:
			return nil, nil
		}
		return normalizeCodexUsageSnapshot(source, state, observed, metadata, current, baseline, cumulative)
	}
	info, _ := payload["info"].(map[string]any)
	total, _ := info["total_token_usage"].(map[string]any)
	last, _ := info["last_token_usage"].(map[string]any)
	if len(total) == 0 && len(last) == 0 {
		return nil, nil
	}
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
	counters := UsageCounters{}
	usageSource := total
	if tailSeed {
		if len(last) > 0 {
			usageSource = last
		}
	}
	for key, value := range map[string]string{"input_tokens": "input_tokens", "cached_input_tokens": "cached_input_tokens", "output_tokens": "output_tokens"} {
		if number, ok := usageSource[value].(float64); ok {
			counters[key] = number
		}
	}
	baseline := counters
	if tailSeed && len(total) > 0 {
		baseline = UsageCounters{}
		for key, value := range map[string]string{"input_tokens": "input_tokens", "cached_input_tokens": "cached_input_tokens", "output_tokens": "output_tokens"} {
			if number, ok := total[value].(float64); ok {
				baseline[key] = number
			}
		}
	}
	return normalizeCodexUsageSnapshot(source, state, observed, metadata, counters, baseline, len(total) > 0 && !(tailSeed && len(last) > 0))
}

func codexUsageCounters(values map[string]any) UsageCounters {
	result := UsageCounters{}
	for _, key := range []string{"input_tokens", "cached_input_tokens", "output_tokens"} {
		if value := mapNumber(values, key); value != nil {
			result[key] = *value
		}
	}
	return result
}

func normalizeCodexUsageSnapshot(source TranscriptSource, state *TranscriptScannerState, observed float64, metadata UsageMetadata, current, baseline UsageCounters, cumulative bool) ([]UsageAtom, error) {
	if state.CodexUsage == nil {
		state.CodexUsage = UsageCounters{}
	}
	delta := current
	if cumulative {
		var err error
		delta, _, err = DeltaUsageCounters(state.CodexUsage, current)
		if err != nil {
			return nil, err
		}
	}
	if cumulative || len(baseline) > 0 {
		state.CodexUsage = cloneCounters(baseline)
	}
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
	copyState.ParsedTail = append([]byte(nil), state.ParsedTail...)
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

func readParsedTail(path string, offset int64) []byte {
	if offset <= 0 {
		return nil
	}
	file, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer file.Close()
	start := offset - transcriptParsedTailBytes
	if start < 0 {
		start = 0
	}
	if _, err := file.Seek(start, io.SeekStart); err != nil {
		return nil
	}
	buffer := make([]byte, offset-start)
	count, err := io.ReadFull(file, buffer)
	if err != nil && err != io.ErrUnexpectedEOF {
		return nil
	}
	return buffer[:count]
}

func parsedTailMatches(file *os.File, offset int64, expected []byte) bool {
	if offset <= 0 || len(expected) == 0 {
		return true
	}
	start := offset - int64(len(expected))
	if start < 0 {
		return false
	}
	if _, err := file.Seek(start, io.SeekStart); err != nil {
		return false
	}
	current := make([]byte, len(expected))
	count, err := io.ReadFull(file, current)
	return err == nil && count == len(expected) && bytes.Equal(current, expected)
}
