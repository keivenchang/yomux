package stats

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"syscall"
	"time"
	"unicode"
)

const (
	DefaultTranscriptMaxBytes             int64 = 4 * 1024 * 1024
	DefaultTranscriptMaxRecords                 = 4096
	transcriptParsedTailBytes                   = 64 * 1024
	transcriptPrefixBytes                       = 64 * 1024
	transcriptMaxPendingLineBytes               = 8 * 1024 * 1024
	maxTranscriptCursorSidecarBytes             = 128 * 1024 * 1024
	maxLegacyTranscriptCursorSidecarBytes       = 128 * 1024 * 1024
	maxTranscriptCursorStates                   = 4096
	maxTranscriptCursorSampleBytes        int64 = 80 * 1024 * 1024
	maxCodexForkRepairFrontierBytes             = 8 * 1024 * 1024
	maxCodexForkRepairPathBytes                 = 4096
	maxCodexForkRepairNameBytes                 = 255
	maxCodexForkRepairPageEntries               = 4096
	maxClaudeMessageIDState                     = 4096
)

// TranscriptSource identifies one provider JSONL transcript and its attribution.
type TranscriptSource struct {
	Path     string
	Provider string
	SourceID string
	ThreadID string
	AgentID  string
	// CodexForkRepairOnly marks a one-time migration scan. It emits tombstones
	// for copied parent history and stops at the explicit child handoff.
	CodexForkRepairOnly bool
	// LiveCandidate identifies a process-owned transcript that may deserve
	// prompt initial Codex scanning before its first durable EOF checkpoint.
	LiveCandidate bool
}

// TranscriptScannerState is the durable cursor for one Claude/Codex JSONL
// source. Prefix and file identity make replacement/truncation detectable;
// usage folds make replay after a crash idempotent.
type TranscriptScannerState struct {
	checkpointRevision int64
	Path               string
	Device             uint64
	Inode              uint64
	// FileSize and FileModTimeNS are retained with the cursor as cheap source
	// metadata, but they are not an integrity proof: a same-size rewrite can
	// preserve a coarse mtime. The scanner therefore still checks the bounded
	// prefix and parsed tail whenever it reuses an offset.
	FileSize      int64
	FileModTimeNS int64
	Offset        int64
	// UsageCommittedEOFSize records the last physical file size fully consumed
	// through a JSONL record boundary. FileSize is only the newest observed size
	// and can include a pending, unterminated record.
	UsageCommittedEOFSize  int64
	UsageCommittedEOFKnown bool
	Prefix                 []byte
	PrefixCompressed       []byte `json:"prefix_compressed,omitempty"`
	PendingLine            []byte
	// ParsedTail is the bounded suffix immediately before Offset. Prefix alone
	// cannot detect an in-place rewrite after the first 64 KiB, which would make
	// the next incremental scan resume from a different record boundary.
	ParsedTail                         []byte
	ParsedTailCompressed               []byte `json:"parsed_tail_compressed,omitempty"`
	ResetCount                         int
	ClaudeUsage                        map[string]UsageCounters
	ClaudeOccurrences                  map[string]int64
	ClaudeMessageOrder                 []string
	ClaudeMessageOrderRebuildDone      bool
	ClaudeMessageOrderRebuildActive    bool
	ClaudeMessageOrderRebuildOffset    int64
	ClaudeMessageOrderRebuildBuffer    []byte
	ClaudeMessageOrderRebuildLine      []byte
	ClaudeMessageOrderRebuildSequence  int64
	ClaudeMessageOrderRebuildOrder     []string
	ClaudeMessageOrderRebuildOrderHead int
	ClaudeMessageOrderRebuildPresent   map[string]struct{}
	ClaudeSequence                     int64
	CodexUsage                         UsageCounters
	CodexModel                         string
	CodexModelEvidence                 string
	CodexEffort                        string
	CodexPricingProfile                string
	CodexServiceTier                   string
	CodexSuppressForkHistory           bool
	CodexThreadID                      string
	CodexForkChildThreadID             string
	CodexForkID                        string
	CodexForkRepairComplete            bool
	CodexSequence                      int64
}

type TranscriptScanOptions struct {
	Context             context.Context
	MaxBytes            int64
	MaxRecords          int
	CodexForkRepairOnly bool
	ForceReset          bool
}

type TranscriptScanResult struct {
	Atoms           []UsageAtom
	Tombstones      []UsageTombstone
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

// transcriptCursorDisk is decoded only while importing legacy committed cursors.

type transcriptCursorDisk struct {
	Version                           int                               `json:"version"`
	Generation                        uint64                            `json:"generation,omitempty"`
	States                            map[string]TranscriptScannerState `json:"states"`
	Pending                           map[string]TranscriptScannerState `json:"pending,omitempty"`
	NextSource                        string                            `json:"next_source,omitempty"`
	PendingNextSource                 string                            `json:"pending_next_source,omitempty"`
	NextHistoricalSource              string                            `json:"next_historical_source,omitempty"`
	PendingNextHistoricalSource       string                            `json:"pending_next_historical_source,omitempty"`
	LiveTranscriptPriorityRuns        int                               `json:"live_transcript_priority_runs,omitempty"`
	PendingLiveTranscriptPriorityRuns int                               `json:"pending_live_transcript_priority_runs,omitempty"`
	CodexForkRepair                   codexForkRepairFrontierState      `json:"codex_fork_repair,omitempty"`
	CodexForkRepairCompletedRoots     []string                          `json:"codex_fork_repair_completed_roots,omitempty"`
}

type transcriptCursorSchedule struct {
	NextSource                 string
	NextHistoricalSource       string
	LiveTranscriptPriorityRuns int
}

type codexForkRepairFrontierState struct {
	NextRoot   string                                 `json:"next_root,omitempty"`
	NextSource string                                 `json:"next_source,omitempty"`
	Roots      map[string]codexForkRepairCatalogState `json:"roots,omitempty"`
}

type codexForkRepairCatalogState struct {
	Root              string                          `json:"root"`
	PendingDirs       []string                        `json:"pending_dirs,omitempty"`
	PendingFiles      []string                        `json:"pending_files,omitempty"`
	BlockedFiles      []string                        `json:"blocked_files,omitempty"`
	Sources           []TranscriptSource              `json:"sources,omitempty"`
	Directory         *codexForkRepairDirectoryState  `json:"directory,omitempty"`
	PausedDirectories []codexForkRepairDirectoryState `json:"paused_directories,omitempty"`
	Blocked           bool                            `json:"blocked,omitempty"`
	Initialized       bool                            `json:"initialized,omitempty"`
	Complete          bool                            `json:"complete,omitempty"`
}

type codexForkRepairDirectoryState struct {
	Path           string   `json:"path"`
	Cookie         int64    `json:"cookie"`
	CursorVersion  int      `json:"cursor_version,omitempty"`
	CursorPlatform string   `json:"cursor_platform,omitempty"`
	Device         uint64   `json:"device,omitempty"`
	Inode          uint64   `json:"inode,omitempty"`
	ModTimeNS      int64    `json:"mtime_ns,omitempty"`
	ChangeTimeNS   int64    `json:"change_time_ns,omitempty"`
	PendingNames   []string `json:"pending_names,omitempty"`
}

func transcriptCursorKey(source TranscriptSource) string {
	if source.CodexForkRepairOnly {
		return "codex-fork-repair\x00" + filepath.Clean(source.Path)
	}
	return source.Provider + "\x00" + filepath.Clean(source.Path)
}

func readLegacyTranscriptCursor(path string, preferredKeys ...string) (transcriptCursorDisk, error) {
	info, statErr := os.Stat(path)
	if statErr != nil && !os.IsNotExist(statErr) {
		return transcriptCursorDisk{}, statErr
	}
	if statErr == nil && info.Size() > maxLegacyTranscriptCursorSidecarBytes {
		return transcriptCursorDisk{}, fmt.Errorf("transcript cursor state exceeds its size bound")
	}
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return transcriptCursorDisk{Version: 2, States: map[string]TranscriptScannerState{}, Pending: map[string]TranscriptScannerState{}}, nil
	}
	if err != nil {
		return transcriptCursorDisk{}, err
	}
	var disk transcriptCursorDisk
	if err := json.Unmarshal(data, &disk); err != nil || (disk.Version != 0 && disk.Version != 1 && disk.Version != 2) {
		return transcriptCursorDisk{}, fmt.Errorf("transcript cursor state is malformed")
	}
	disk.Pending = nil
	if disk.Version < 2 {
		// Older writers bounded serialized bytes but did not bound retained
		// cursor count. Prune retired committed sources before enforcing the v2
		// entry limit so source churn does not make a recoverable sidecar unreadable.
		pruneTranscriptCursorStates(&disk, preferredKeys...)
	}
	if len(disk.States)+len(disk.Pending) > maxTranscriptCursorStates {
		return transcriptCursorDisk{}, fmt.Errorf("transcript cursor state exceeds its entry bound")
	}
	remainingSampleBytes := maxTranscriptCursorSampleBytes
	for key, state := range disk.States {
		if err := decodeTranscriptCursorSamples(&state, &remainingSampleBytes); err != nil {
			return transcriptCursorDisk{}, err
		}
		disk.States[key] = state
	}
	if disk.LiveTranscriptPriorityRuns < 0 || disk.LiveTranscriptPriorityRuns > maxConsecutiveLiveTranscriptPriorityRuns {
		return transcriptCursorDisk{}, fmt.Errorf("transcript cursor priority schedule is malformed")
	}
	if disk.States == nil {
		disk.States = make(map[string]TranscriptScannerState)
	}
	if disk.CodexForkRepair.Roots == nil {
		disk.CodexForkRepair.Roots = make(map[string]codexForkRepairCatalogState)
	}
	if err := validateCodexForkRepairFrontier(disk.CodexForkRepair); err != nil {
		return transcriptCursorDisk{}, err
	}
	disk.CodexForkRepairCompletedRoots, err = mergeCodexForkRepairRootIDs(disk.CodexForkRepairCompletedRoots, nil)
	if err != nil {
		return transcriptCursorDisk{}, err
	}
	return disk, nil
}

func validateCodexForkRepairFrontier(frontier codexForkRepairFrontierState) error {
	if len(frontier.Roots) > maxCodexForkRepairCatalogRoots || len(frontier.NextRoot) > maxCodexForkRepairPathBytes || len(frontier.NextSource) > maxCodexForkRepairPathBytes {
		return fmt.Errorf("Codex fork repair frontier exceeds its bound")
	}
	for rootID, catalog := range frontier.Roots {
		if len(rootID) != 64 || len(catalog.Root) == 0 || len(catalog.Root) > maxCodexForkRepairPathBytes || !filepath.IsAbs(catalog.Root) || filepath.Clean(catalog.Root) != catalog.Root || codexForkRepairRootID(catalog.Root) != rootID {
			return fmt.Errorf("Codex fork repair root state is malformed")
		}
		if len(catalog.PendingDirs) > maxCodexForkRepairDirectoryFrontier || len(catalog.PendingFiles)+len(catalog.BlockedFiles) > maxCodexForkRepairFileFrontier || len(catalog.Sources) > maxCodexForkRepairSourcesPerRoot || len(catalog.PausedDirectories) > maxCodexForkRepairDirectoryFrontier {
			return fmt.Errorf("Codex fork repair catalog exceeds its bound")
		}
		for _, path := range catalog.PendingDirs {
			if path == "" || len(path) > maxCodexForkRepairPathBytes || !codexForkRepairPathWithinRoot(catalog.Root, path, true) {
				return fmt.Errorf("Codex fork repair directory path is malformed")
			}
		}
		for _, path := range catalog.PendingFiles {
			if path == "" || len(path) > maxCodexForkRepairPathBytes || !codexForkRepairPathWithinRoot(catalog.Root, path, false) {
				return fmt.Errorf("Codex fork repair file path is malformed")
			}
		}
		for _, path := range catalog.BlockedFiles {
			if path == "" || len(path) > maxCodexForkRepairPathBytes || !codexForkRepairPathWithinRoot(catalog.Root, path, false) {
				return fmt.Errorf("Codex fork repair blocked file path is malformed")
			}
		}
		for _, source := range catalog.Sources {
			if source.Provider != "codex" || !source.CodexForkRepairOnly || len(source.Path) == 0 || len(source.Path) > maxCodexForkRepairPathBytes || !codexForkRepairPathWithinRoot(catalog.Root, source.Path, false) || len(source.SourceID) > maxCodexForkRepairPathBytes || len(source.ThreadID) > maxCodexForkRepairPathBytes || len(source.AgentID) > maxCodexForkRepairPathBytes {
				return fmt.Errorf("Codex fork repair source is malformed")
			}
		}
		if err := validateCodexForkRepairDirectory(catalog.Root, catalog.Directory); err != nil {
			return err
		}
		for index := range catalog.PausedDirectories {
			if err := validateCodexForkRepairDirectory(catalog.Root, &catalog.PausedDirectories[index]); err != nil {
				return err
			}
		}
	}
	data, err := json.Marshal(frontier)
	if err != nil || len(data) > maxCodexForkRepairFrontierBytes {
		return fmt.Errorf("Codex fork repair frontier exceeds its serialized size bound")
	}
	return nil
}

func codexForkRepairPathWithinRoot(root, path string, allowRoot bool) bool {
	if !filepath.IsAbs(root) || !filepath.IsAbs(path) || filepath.Clean(root) != root || filepath.Clean(path) != path {
		return false
	}
	relative, err := filepath.Rel(root, path)
	if err != nil || filepath.IsAbs(relative) || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return false
	}
	return allowRoot || relative != "."
}

func validateCodexForkRepairDirectory(root string, directory *codexForkRepairDirectoryState) error {
	if directory == nil {
		return nil
	}
	if directory.Path == "" || len(directory.Path) > maxCodexForkRepairPathBytes || !codexForkRepairPathWithinRoot(root, directory.Path, true) || directory.Cookie < 0 || directory.CursorVersion < 0 || directory.CursorVersion > 2 || len(directory.CursorPlatform) > 16 || len(directory.PendingNames) > maxCodexForkRepairPageEntries {
		return fmt.Errorf("Codex fork repair directory continuation is malformed")
	}
	for _, name := range directory.PendingNames {
		if name == "" || name == "." || name == ".." || len(name) > maxCodexForkRepairNameBytes || strings.ContainsAny(name, `/\\`) {
			return fmt.Errorf("Codex fork repair directory entry is malformed")
		}
	}
	return nil
}

func decodeTranscriptCursorSamples(state *TranscriptScannerState, remainingBytes *int64) error {
	if len(state.Prefix) > transcriptPrefixBytes || len(state.ParsedTail) > transcriptParsedTailBytes {
		return fmt.Errorf("transcript cursor raw sample exceeds its per-field bound")
	}
	if int64(len(state.Prefix)+len(state.ParsedTail)) > *remainingBytes {
		return fmt.Errorf("transcript cursor samples exceed their aggregate size bound")
	}
	*remainingBytes -= int64(len(state.Prefix) + len(state.ParsedTail))
	if len(state.PrefixCompressed) > 0 {
		decoded, err := gunzipTranscriptCursorSample(state.PrefixCompressed, transcriptPrefixBytes, *remainingBytes)
		if err != nil {
			return fmt.Errorf("transcript cursor prefix sample is malformed: %w", err)
		}
		state.Prefix = decoded
		state.PrefixCompressed = nil
		*remainingBytes -= int64(len(decoded))
	}
	if len(state.ParsedTailCompressed) > 0 {
		decoded, err := gunzipTranscriptCursorSample(state.ParsedTailCompressed, transcriptParsedTailBytes, *remainingBytes)
		if err != nil {
			return fmt.Errorf("transcript cursor parsed-tail sample is malformed: %w", err)
		}
		state.ParsedTail = decoded
		state.ParsedTailCompressed = nil
		*remainingBytes -= int64(len(decoded))
	}
	return nil
}

func gunzipTranscriptCursorSample(sample []byte, maxBytes int, remainingBytes int64) ([]byte, error) {
	aggregateLimit := remainingBytes < int64(maxBytes)
	if remainingBytes < int64(maxBytes) {
		maxBytes = int(remainingBytes)
	}
	reader, err := gzip.NewReader(bytes.NewReader(sample))
	if err != nil {
		return nil, err
	}
	decoded, readErr := io.ReadAll(io.LimitReader(reader, int64(maxBytes)+1))
	closeErr := reader.Close()
	if readErr != nil {
		return nil, readErr
	}
	if closeErr != nil {
		return nil, closeErr
	}
	if len(decoded) > maxBytes {
		if aggregateLimit {
			return nil, fmt.Errorf("transcript cursor aggregate sample size exceeds its bound")
		}
		return nil, fmt.Errorf("decompressed transcript cursor sample exceeds its bound")
	}
	return decoded, nil
}

func pruneTranscriptCursorStates(disk *transcriptCursorDisk, preferredKeys ...string) {
	preferred := make(map[string]struct{}, len(preferredKeys))
	for _, key := range preferredKeys {
		preferred[key] = struct{}{}
	}
	if len(disk.States) > maxTranscriptCursorStates/8 {
		for key, state := range disk.States {
			if state.Path == "" {
				continue
			}
			if _, err := os.Stat(state.Path); os.IsNotExist(err) {
				delete(disk.States, key)
			}
		}
	}
	if len(disk.States)+len(disk.Pending) <= maxTranscriptCursorStates && transcriptCursorSampleBytes(disk) <= maxTranscriptCursorSampleBytes {
		return
	}
	keys := make([]string, 0, len(disk.States))
	for key := range disk.States {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		left, right := disk.States[keys[i]], disk.States[keys[j]]
		if left.FileModTimeNS != right.FileModTimeNS {
			return left.FileModTimeNS < right.FileModTimeNS
		}
		return keys[i] < keys[j]
	})
	for _, key := range keys {
		if len(disk.States)+len(disk.Pending) <= maxTranscriptCursorStates && transcriptCursorSampleBytes(disk) <= maxTranscriptCursorSampleBytes {
			break
		}
		if _, keep := preferred[key]; keep {
			continue
		}
		delete(disk.States, key)
	}
}

func cloneCodexForkRepairDirectory(directory codexForkRepairDirectoryState) codexForkRepairDirectoryState {
	directory.PendingNames = append([]string(nil), directory.PendingNames...)
	return directory
}

func codexForkRepairMarkerPath(cursorPath string) string {
	return cursorPath + ".codex-fork-repair-v1.done"
}

func mergeCodexForkRepairRootIDs(existing, additions []string) ([]string, error) {
	completed := make(map[string]struct{}, len(existing)+len(additions))
	for _, rootID := range append(append([]string(nil), existing...), additions...) {
		if len(rootID) != 64 {
			return nil, fmt.Errorf("Codex fork repair root ID is malformed")
		}
		completed[rootID] = struct{}{}
	}
	ordered := make([]string, 0, len(completed))
	for rootID := range completed {
		ordered = append(ordered, rootID)
	}
	sort.Strings(ordered)
	return ordered, nil
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
	repairOnly := source.CodexForkRepairOnly || options.CodexForkRepairOnly
	if repairOnly {
		if source.Provider != "codex" {
			return TranscriptScanResult{}, fmt.Errorf("Codex fork repair requires a Codex transcript")
		}
		root := codexSessionsRoot(source.Path)
		if root == "" {
			return TranscriptScanResult{}, fmt.Errorf("Codex fork repair transcript is outside a sessions root")
		}
	}
	if options.Context == nil {
		options.Context = context.Background()
	}
	if err := options.Context.Err(); err != nil {
		return TranscriptScanResult{}, err
	}
	if options.MaxBytes <= 0 {
		options.MaxBytes = DefaultTranscriptMaxBytes
	}
	if options.MaxRecords <= 0 {
		options.MaxRecords = DefaultTranscriptMaxRecords
	}
	if source.CodexForkRepairOnly {
		options.CodexForkRepairOnly = true
	}
	if options.CodexForkRepairOnly && state.CodexForkRepairComplete {
		return TranscriptScanResult{Receipt: &TranscriptScanReceipt{state, cloneTranscriptState(*state), cloneTranscriptState(*state), false}}, nil
	}
	var file *os.File
	var info os.FileInfo
	var err error
	if options.CodexForkRepairOnly {
		root := codexSessionsRoot(source.Path)
		file, info, err = openCodexForkRepairPathNoSymlinks(root, filepath.Clean(source.Path), false)
		if err == nil && !info.Mode().IsRegular() {
			_ = file.Close()
			file = nil
			err = fmt.Errorf("Codex fork repair transcript path is not a regular file")
		}
	} else {
		file, err = os.Open(filepath.Clean(source.Path))
		if err == nil {
			info, err = file.Stat()
		}
	}
	if err != nil {
		if file != nil {
			_ = file.Close()
		}
		return TranscriptScanResult{}, err
	}
	defer file.Close()

	before := cloneTranscriptState(*state)
	after := cloneTranscriptState(*state)
	after.Path = source.Path
	fileSize := info.Size()
	fileModTimeNS := info.ModTime().UnixNano()
	pendingEnd := after.Offset + int64(len(after.PendingLine))
	reset := options.ForceReset || (before.Path != "" && (after.Path != before.Path || after.Offset > fileSize || (len(after.PendingLine) > 0 && fileSize < pendingEnd) || identityChanged(after, info)))
	if reset {
		resetTranscriptContent(&after)
	} else if before.Path != "" && !after.UsageCommittedEOFKnown {
		if after.Offset == after.FileSize && len(after.PendingLine) == 0 {
			// Upgrade old cursor sidecars using their last complete EOF checkpoint.
			after.UsageCommittedEOFSize = after.Offset
			after.UsageCommittedEOFKnown = true
		} else if after.Offset > 0 && len(after.PendingLine) > 0 {
			// Older sidecars did not retain this watermark. Their committed offset
			// is still the last complete record boundary before the pending tail.
			after.UsageCommittedEOFSize = after.Offset
			after.UsageCommittedEOFKnown = true
		}
	}
	result := TranscriptScanResult{Reset: reset}
	remaining := options.MaxBytes
	readAt := func(offset int64, size int) ([]byte, bool, error) {
		if size <= 0 {
			return nil, true, nil
		}
		if int64(size) > remaining {
			return nil, false, nil
		}
		data := make([]byte, size)
		n, readErr := file.ReadAt(data, offset)
		result.BytesRead += int64(n)
		remaining -= int64(n)
		if readErr != nil && readErr != io.EOF {
			return nil, true, readErr
		}
		return data[:n], true, nil
	}
	// Small proportional samples preserve the source-replacement checks while
	// charging validation and fingerprint reads to the same aggregate budget as
	// transcript records. Production cycles retain samples up to 64 KiB.
	sampleBytes := options.MaxBytes / 8
	if sampleBytes > transcriptPrefixBytes {
		sampleBytes = transcriptPrefixBytes
	}
	if sampleBytes < 1 {
		sampleBytes = 1
	}
	if !reset && len(after.Prefix) > 0 {
		prefixLength := len(after.Prefix)
		if int64(prefixLength) > sampleBytes {
			prefixLength = int(sampleBytes)
		}
		current, admitted, readErr := readAt(0, prefixLength)
		if readErr != nil {
			return TranscriptScanResult{}, readErr
		}
		if !admitted {
			result.BudgetExhausted = true
			return result, nil
		}
		if !bytes.Equal(current, after.Prefix[:prefixLength]) {
			reset = true
			resetTranscriptContent(&after)
			result.Reset = true
		}
	}
	if !reset && len(after.ParsedTail) > 0 {
		tailLength := len(after.ParsedTail)
		if int64(tailLength) > sampleBytes {
			tailLength = int(sampleBytes)
		}
		current, admitted, readErr := readAt(after.Offset-int64(tailLength), tailLength)
		if readErr != nil {
			return TranscriptScanResult{}, readErr
		}
		if !admitted {
			result.BudgetExhausted = true
			return result, nil
		}
		if !bytes.Equal(current, after.ParsedTail[len(after.ParsedTail)-tailLength:]) {
			reset = true
			resetTranscriptContent(&after)
			result.Reset = true
		}
	}
	if !reset && len(after.PendingLine) > 0 {
		pendingTailLength := len(after.PendingLine)
		if int64(pendingTailLength) > sampleBytes {
			pendingTailLength = int(sampleBytes)
		}
		pendingTailOffset := after.Offset + int64(len(after.PendingLine)-pendingTailLength)
		current, admitted, readErr := readAt(pendingTailOffset, pendingTailLength)
		if readErr != nil {
			return TranscriptScanResult{}, readErr
		}
		if !admitted {
			result.BudgetExhausted = true
			return result, nil
		}
		if !bytes.Equal(current, after.PendingLine[len(after.PendingLine)-pendingTailLength:]) {
			reset = true
			resetTranscriptContent(&after)
			result.Reset = true
		}
	}
	if after.Prefix == nil {
		prefixSize := int(sampleBytes)
		if int64(prefixSize) > fileSize {
			prefixSize = int(fileSize)
		}
		prefix, admitted, readErr := readAt(0, prefixSize)
		if readErr != nil {
			return TranscriptScanResult{}, readErr
		}
		if !admitted {
			result.BudgetExhausted = true
			return result, nil
		}
		after.Prefix = prefix
	}
	if reset {
		after.Path = source.Path
	}
	if !reset && after.Offset > 0 && after.CodexModel != "" {
		// Context preceding a durable cursor is inherited, not re-read. Match
		// Python's explicit provenance for atoms emitted from that resumed state.
		after.CodexModelEvidence = "scan_state.resumed_model"
	}
	after.Device, after.Inode = fileIdentity(info)
	after.FileSize, after.FileModTimeNS = fileSize, fileModTimeNS
	if source.Provider == "claude" {
		if err := validateClaudeMessageOrderRebuildState(after); err != nil {
			return TranscriptScanResult{}, err
		}
	}
	if source.Provider == "claude" && claudeMessageOrderNeedsRebuild(&after) {
		complete, rebuildErr := rebuildLegacyClaudeMessageOrder(file, &after, options, &remaining, &result)
		if rebuildErr != nil {
			return TranscriptScanResult{}, rebuildErr
		}
		if !complete {
			result.BudgetExhausted = true
			result.FilesRead = 1
			result.Receipt = &TranscriptScanReceipt{state, before, after, false}
			return result, nil
		}
	}
	if source.Provider == "claude" {
		boundClaudeMessageState(&after)
	}
	if after.Offset == fileSize && len(after.PendingLine) == 0 {
		after.UsageCommittedEOFSize = fileSize
		after.UsageCommittedEOFKnown = true
		if err := options.Context.Err(); err != nil {
			return TranscriptScanResult{}, err
		}
		if result.BytesRead == 0 {
			result.FilesRead = 0
		}
		result.Receipt = &TranscriptScanReceipt{state, before, after, false}
		return result, nil
	}
	result.FilesRead = 1
	tailBudget := sampleBytes
	tailReserve := int64(0)
	if after.Offset > 0 && remaining > tailBudget {
		remaining -= tailBudget
		tailReserve = tailBudget
	} else if after.Offset > 0 {
		remaining = 0
	}
	contentStart := after.Offset + int64(len(after.PendingLine))
	snapshotContentLimit := fileSize - contentStart
	if snapshotContentLimit < 0 {
		snapshotContentLimit = 0
	}
	contentLimit := min(remaining, snapshotContentLimit)
	contentSource := &transcriptCountingReader{reader: io.NewSectionReader(file, contentStart, contentLimit)}
	reader := bufio.NewReader(io.LimitReader(contentSource, contentLimit))
	contentConsumed := int64(0)
	for result.RecordsParsed < options.MaxRecords {
		if err := options.Context.Err(); err != nil {
			return TranscriptScanResult{}, err
		}
		contentRemaining := contentLimit - contentConsumed
		if contentRemaining <= 0 {
			atSnapshotEOF := transcriptReadThroughSnapshotEOF(contentStart+contentConsumed, fileSize)
			if !atSnapshotEOF || len(after.PendingLine) == 0 {
				result.BudgetExhausted = !atSnapshotEOF
				break
			}
		}
		line, lineBytes, complete, budgetCut, readErr := readTranscriptLine(options.Context, reader, contentRemaining)
		if readErr != nil {
			return TranscriptScanResult{}, readErr
		}
		if len(line) == 0 && !complete {
			if len(after.PendingLine) == 0 || budgetCut || !transcriptReadThroughSnapshotEOF(contentStart+contentSource.read, fileSize) {
				break
			}
		}
		contentConsumed += lineBytes
		if len(after.PendingLine)+len(line) > transcriptMaxPendingLineBytes {
			return TranscriptScanResult{}, fmt.Errorf("transcript record exceeds %d-byte parser limit", transcriptMaxPendingLineBytes)
		}
		line = append(after.PendingLine, line...)
		trimmed := bytes.TrimSpace(line)
		// An EOF snapshot is not a record boundary: the writer may still append
		// bytes, and Python leaves every unterminated JSONL row pending.
		// A final CR is the one alternate terminator accepted by Python.
		if !complete && !budgetCut && len(line) > 0 && line[len(line)-1] == '\r' &&
			transcriptReadThroughSnapshotEOF(contentStart+contentSource.read, fileSize) {
			complete = true
		}
		if !complete {
			after.PendingLine = append(after.PendingLine[:0], line...)
			result.BudgetExhausted = budgetCut || !transcriptReadThroughSnapshotEOF(contentStart+contentSource.read, fileSize)
			break
		}
		after.Offset += int64(len(line))
		after.PendingLine = nil
		result.RecordsParsed++
		if complete && !transcriptLineMayContainUsage(source.Provider, trimmed) {
			if len(trimmed) > 0 && trimmed[0] == '{' && json.Valid(trimmed) {
				switch source.Provider {
				case "claude":
					after.ClaudeSequence++
				case "codex":
					after.CodexSequence++
				}
			}
			continue
		}
		var record map[string]any
		if err := json.Unmarshal(trimmed, &record); err != nil {
			continue
		}
		if source.Provider == "codex" {
			after.CodexSequence++
		} else if source.Provider == "claude" {
			after.ClaudeSequence++
		}
		atoms, err := normalizeTranscriptRecord(source, &after, record)
		if err != nil {
			return TranscriptScanResult{}, err
		}
		if source.Provider == "codex" {
			sequence := after.CodexSequence - 1
			threadID := transcriptThreadIdentity(source, &after)
			for index := range atoms {
				atoms[index].EventID = fmt.Sprintf("codex:%s:%d", threadID, sequence)
			}
		}
		if source.Provider == "codex" && after.CodexSuppressForkHistory {
			for _, atom := range atoms {
				if tombstone, ok := usageAtomTombstone(atom); ok {
					result.Tombstones = append(result.Tombstones, tombstone)
				}
			}
			atoms = nil
		}
		if options.CodexForkRepairOnly {
			atoms = nil
			if codexInterAgentHandoff(record) {
				after.CodexForkRepairComplete = true
				break
			}
		}
		result.Atoms = append(result.Atoms, atoms...)
	}
	if options.CodexForkRepairOnly && after.Offset >= fileSize && len(after.PendingLine) == 0 {
		after.CodexForkRepairComplete = true
	}
	if after.Offset == fileSize && len(after.PendingLine) == 0 {
		after.UsageCommittedEOFSize = fileSize
		after.UsageCommittedEOFKnown = true
	}
	result.BytesRead += contentSource.read
	remaining -= contentSource.read
	remaining += tailReserve
	if after.Offset > 0 {
		tailSize := int(sampleBytes)
		if int64(tailSize) > after.Offset {
			tailSize = int(after.Offset)
		}
		if int64(tailSize) <= remaining {
			tail, admitted, readErr := readAt(after.Offset-int64(tailSize), tailSize)
			if readErr != nil {
				return TranscriptScanResult{}, readErr
			}
			if admitted {
				after.ParsedTail = tail
			}
		}
	}
	if (result.RecordsParsed >= options.MaxRecords || result.BytesRead >= options.MaxBytes) &&
		after.Offset+int64(len(after.PendingLine)) < fileSize {
		result.BudgetExhausted = true
	}
	if err := options.Context.Err(); err != nil {
		return TranscriptScanResult{}, err
	}
	result.Receipt = &TranscriptScanReceipt{state, before, after, false}
	return result, nil
}

func transcriptReadThroughSnapshotEOF(readThrough, snapshotSize int64) bool {
	return readThrough >= snapshotSize
}

func resetTranscriptContent(state *TranscriptScannerState) {
	state.Offset = 0
	state.UsageCommittedEOFSize = 0
	state.UsageCommittedEOFKnown = false
	state.Prefix = nil
	state.PendingLine = nil
	state.ParsedTail = nil
	state.ClaudeUsage = nil
	state.ClaudeOccurrences = nil
	state.ClaudeMessageOrder = nil
	state.ClaudeMessageOrderRebuildDone = false
	state.ClaudeMessageOrderRebuildActive = false
	state.ClaudeMessageOrderRebuildOffset = 0
	state.ClaudeMessageOrderRebuildBuffer = nil
	state.ClaudeMessageOrderRebuildLine = nil
	state.ClaudeMessageOrderRebuildSequence = 0
	state.ClaudeMessageOrderRebuildOrder = nil
	state.ClaudeMessageOrderRebuildOrderHead = 0
	state.ClaudeMessageOrderRebuildPresent = nil
	state.ClaudeSequence = 0
	state.CodexUsage = nil
	state.CodexModel = ""
	state.CodexModelEvidence = ""
	state.CodexEffort = ""
	state.CodexPricingProfile = ""
	state.CodexServiceTier = ""
	state.CodexSuppressForkHistory = false
	state.CodexThreadID = ""
	state.CodexForkChildThreadID = ""
	state.CodexForkID = ""
	state.CodexForkRepairComplete = false
	state.CodexSequence = 0
	state.ResetCount++
}

type transcriptCountingReader struct {
	reader io.Reader
	read   int64
}

func (r *transcriptCountingReader) Read(buffer []byte) (int, error) {
	n, err := r.reader.Read(buffer)
	r.read += int64(n)
	return n, err
}

// readTranscriptLine returns a bounded prefix when a record crosses the
// caller's remaining budget. The caller persists that prefix and resumes at
// its end on the next cycle; read-ahead beyond the returned prefix is still
// charged through the wrapped reader and may be reread later.
func readTranscriptLine(ctx context.Context, reader *bufio.Reader, maxBytes int64) ([]byte, int64, bool, bool, error) {
	var line []byte
	var total int64
	for {
		if err := ctx.Err(); err != nil {
			return nil, total, false, false, err
		}
		fragment, err := reader.ReadSlice('\n')
		available := maxBytes - total
		if int64(len(fragment)) > available {
			if available > 0 {
				line = append(line, fragment[:available]...)
				total += available
			}
			return line, total, false, true, nil
		}
		line = append(line, fragment...)
		total += int64(len(fragment))
		if err == nil {
			return line, total, true, false, nil
		}
		if err == bufio.ErrBufferFull {
			continue
		}
		if err == io.EOF {
			return line, total, false, false, nil
		}
		return nil, total, false, false, err
	}
}

// transcriptLineMayContainUsage is a cheap lexical gate for the JSONL hot
// path. Most Codex rollout lines are progress/event records with no token
// counters, and most Claude lines have no message usage object. We still run
// the full decoder for every candidate, so this only skips provably irrelevant
// records and does not change cursor advancement or malformed-line handling.
func transcriptLineMayContainUsage(provider string, line []byte) bool {
	if provider == "claude" {
		return bytes.Contains(line, []byte(`"usage"`))
	}
	for _, marker := range [][]byte{
		[]byte(`"session_meta"`), []byte(`"turn_context"`), []byte(`"token_usage_record"`),
		[]byte(`"total_token_usage"`), []byte(`"last_token_usage"`), []byte(`"thread_token_usage"`),
		[]byte(`"thread_settings_applied"`), []byte(`"inter_agent_communication_metadata"`),
	} {
		if bytes.Contains(line, marker) {
			return true
		}
	}
	return false
}

// applyCodexContextRecord advances the context that applies to subsequent
// usage records. Codex has emitted both wrapped event_msg records and
// top-level context records over time, so both formats share this transition.
func applyCodexContextRecord(source TranscriptSource, state *TranscriptScannerState, record map[string]any) (bool, error) {
	typeName, _ := record["type"].(string)
	payload, _ := record["payload"].(map[string]any)
	eventType, _ := payload["type"].(string)
	if typeName == "session_meta" {
		if id := codexSessionMetaThreadID(payload); id != "" {
			state.CodexThreadID = id
		}
		if model, ok := payload["model"].(string); ok {
			if strings.TrimSpace(model) != "" {
				state.CodexModel = model
				state.CodexModelEvidence = "session_meta.payload.model"
			}
		}
		updateCodexUsageContext(state, payload, "effort")
		forkParent := ""
		explicitFork := false
		if parent, ok := payload["forked_from_id"].(string); ok {
			if threadSource, ok := payload["thread_source"].(string); ok && strings.EqualFold(strings.TrimSpace(threadSource), "subagent") {
				forkParent = strings.TrimSpace(parent)
				explicitFork = forkParent != ""
			}
		}
		if sourceInfo, ok := payload["source"].(map[string]any); ok {
			if subagent, ok := sourceInfo["subagent"].(map[string]any); ok {
				if spawn, ok := subagent["thread_spawn"].(map[string]any); ok {
					if parent, ok := spawn["parent_thread_id"].(string); ok {
						forkParent = strings.TrimSpace(parent)
					}
				}
			}
		}
		if forkParent != "" {
			state.CodexForkID = forkParent
		}
		if explicitFork {
			state.CodexSuppressForkHistory = true
			if id := codexSessionMetaThreadID(payload); id != "" {
				state.CodexForkChildThreadID = id
			}
		}
		return true, nil
	}
	if typeName == "turn_context" {
		if model, ok := payload["model"].(string); ok {
			if strings.TrimSpace(model) != "" {
				state.CodexModel = model
				state.CodexModelEvidence = "turn_context.payload.model"
			}
		}
		updateCodexUsageContext(state, payload, "effort")
		return true, nil
	}
	if typeName == "inter_agent_communication_metadata" || (typeName == "event_msg" && eventType == "inter_agent_communication_metadata") {
		state.CodexSuppressForkHistory = false
		if state.CodexForkChildThreadID != "" {
			state.CodexThreadID = state.CodexForkChildThreadID
		} else if source.ThreadID != "" {
			state.CodexThreadID = source.ThreadID
		}
		return true, nil
	}
	if typeName == "event_msg" && eventType == "thread_settings_applied" {
		settings, _ := payload["thread_settings"].(map[string]any)
		if model, ok := settings["model"].(string); ok && strings.TrimSpace(model) != "" {
			state.CodexModel = model
			state.CodexModelEvidence = "thread_settings_applied.thread_settings.model"
		}
		updateCodexUsageContext(state, settings, "reasoning_effort", "effort")
		return true, nil
	}
	return false, nil
}

func codexSessionMetaThreadID(payload map[string]any) string {
	id, _ := payload["id"].(string)
	if strings.TrimSpace(id) == "" {
		id, _ = payload["thread_id"].(string)
	}
	return strings.TrimSpace(id)
}

func codexInterAgentHandoff(record map[string]any) bool {
	typeName, _ := record["type"].(string)
	payload, _ := record["payload"].(map[string]any)
	eventType, _ := payload["type"].(string)
	return typeName == "inter_agent_communication_metadata" ||
		(typeName == "event_msg" && eventType == "inter_agent_communication_metadata")
}

func normalizeTranscriptRecord(source TranscriptSource, state *TranscriptScannerState, record map[string]any) ([]UsageAtom, error) {
	observed := transcriptTimestamp(record["timestamp"])
	metadata := sourceUsageMetadata(source.Provider, "unknown", "unknown", transcriptThreadIdentity(source, state), source.Provider)
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
		typeName, _ := record["type"].(string)
		if typeName != "assistant" || observed <= 0 {
			return nil, nil
		}
		metadata.Provider = "anthropic"
		metadata.Endpoint = "messages"
		message, ok := record["message"].(map[string]any)
		if !ok {
			return nil, nil
		}
		usage, _ := message["usage"].(map[string]any)
		counters := claudeRecordUsage(usage)
		if len(counters) == 0 {
			return nil, nil
		}
		if model, ok := message["model"].(string); ok {
			if strings.TrimSpace(model) != "" {
				metadata.Model = model
				metadata.ModelEvidence = "assistant.message.model"
			}
		}
		messageID, _ := message["id"].(string)
		messageID = strings.TrimSpace(messageID)
		if messageID == "" {
			messageID = fmt.Sprintf("line-%d", state.ClaudeSequence-1)
		}
		if len(messageID) > 256 {
			messageID = messageID[:256]
		}
		if state.ClaudeOccurrences == nil {
			state.ClaudeOccurrences = make(map[string]int64)
		}
		previous := state.ClaudeUsage
		if previous == nil {
			previous = make(map[string]UsageCounters)
			state.ClaudeUsage = previous
		}
		occurrence := state.ClaudeOccurrences[messageID]
		if _, hasOccurrence := state.ClaudeOccurrences[messageID]; !hasOccurrence {
			_, hasPreviousUsage := previous[messageID]
			recordSequence := state.ClaudeSequence - 1
			if hasPreviousUsage || recordSequence >= maxClaudeMessageIDState {
				// The bounded cursor may have evicted this ID's local revision count.
				// A global record sequence is persisted across restarts and cannot reuse
				// an earlier per-message revision identity.
				occurrence = recordSequence
			}
		}
		metadata.Effort = "unknown"
		effort, _ := message["effort"].(string)
		if strings.TrimSpace(effort) == "" {
			effort, _ = record["effort"].(string)
		}
		if strings.TrimSpace(effort) != "" {
			metadata.Effort = normalizeTranscriptEffort(effort)
		}
		if _, exists := previous[messageID]; !exists {
			state.ClaudeMessageOrder = append(state.ClaudeMessageOrder, messageID)
			state.ClaudeMessageOrderRebuildDone = true
		}
		delta, _, err := DeltaUsageCounters(previous[messageID], counters)
		if err != nil {
			return nil, err
		}
		state.ClaudeOccurrences[messageID] = occurrence + 1
		previous[messageID] = counters
		for len(state.ClaudeMessageOrder) > maxClaudeMessageIDState {
			retiredMessageID := state.ClaudeMessageOrder[0]
			state.ClaudeMessageOrder = state.ClaudeMessageOrder[1:]
			delete(previous, retiredMessageID)
			delete(state.ClaudeOccurrences, retiredMessageID)
		}
		atoms, err := NormalizeClaudeUsageMap(anyCounters(delta), observed, metadata)
		if err != nil {
			return nil, err
		}
		eventID := fmt.Sprintf("claude:%s:%s", metadata.ThreadID, messageID)
		if occurrence > 0 {
			eventID += fmt.Sprintf(":revision-%d", occurrence)
		}
		for index := range atoms {
			atoms[index].EventID = eventID
		}
		addAgentIDs(atoms, source.AgentIDOrThread())
		return atoms, err
	}
	payload, _ := record["payload"].(map[string]any)
	if handled, err := applyCodexContextRecord(source, state, record); handled || err != nil {
		return nil, err
	}
	if record["type"] == "token_usage_record" {
		// Current Codex rollouts publish per-response usage together with a
		// cumulative thread_token_usage snapshot. Prefer the cumulative thread
		// counter so incremental scans can derive the delta from prior records.
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
			state.CodexModelEvidence = "token_usage_record.payload.model"
		}
		updateCodexUsageContext(state, payload, "effort")
		if state.CodexForkChildThreadID != "" {
			metadata.ThreadID = state.CodexForkChildThreadID
		} else if state.CodexThreadID != "" {
			metadata.ThreadID = state.CodexThreadID
		}
		metadata.ForkID = state.CodexForkID
		metadata.Effort = firstNonEmpty(state.CodexEffort, "unknown")
		metadata.PricingProfile = firstNonEmpty(state.CodexPricingProfile, "default")
		metadata.ServiceTier = firstNonEmpty(state.CodexServiceTier, "default")
		metadata.ModelEvidence = firstNonEmpty(state.CodexModelEvidence, "unknown")
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
	if state.CodexForkChildThreadID != "" {
		metadata.ThreadID = state.CodexForkChildThreadID
	} else if state.CodexThreadID != "" {
		metadata.ThreadID = state.CodexThreadID
	}
	metadata.ForkID = state.CodexForkID
	metadata.Effort = firstNonEmpty(state.CodexEffort, "unknown")
	metadata.PricingProfile = firstNonEmpty(state.CodexPricingProfile, "default")
	metadata.ServiceTier = firstNonEmpty(state.CodexServiceTier, "default")
	metadata.ModelEvidence = firstNonEmpty(state.CodexModelEvidence, "unknown")
	counters := UsageCounters{}
	usageSource := total
	for key, value := range map[string]string{"input_tokens": "input_tokens", "cached_input_tokens": "cached_input_tokens", "output_tokens": "output_tokens"} {
		if number, ok := usageSource[value].(float64); ok {
			counters[key] = number
		}
	}
	return normalizeCodexUsageSnapshot(source, state, observed, metadata, counters, counters, len(total) > 0)
}

func boundClaudeMessageState(state *TranscriptScannerState) {
	if len(state.ClaudeUsage) == 0 {
		state.ClaudeMessageOrder = nil
		state.ClaudeMessageOrderRebuildDone = false
		state.ClaudeMessageOrderRebuildActive = false
		state.ClaudeMessageOrderRebuildOffset = 0
		state.ClaudeMessageOrderRebuildBuffer = nil
		state.ClaudeMessageOrderRebuildLine = nil
		state.ClaudeMessageOrderRebuildSequence = 0
		state.ClaudeMessageOrderRebuildOrder = nil
		state.ClaudeMessageOrderRebuildOrderHead = 0
		state.ClaudeMessageOrderRebuildPresent = nil
		state.ClaudeOccurrences = nil
		return
	}
	ordered := make([]string, 0, len(state.ClaudeUsage))
	seen := make(map[string]struct{}, len(state.ClaudeUsage))
	for _, messageID := range state.ClaudeMessageOrder {
		if _, exists := state.ClaudeUsage[messageID]; !exists {
			continue
		}
		if _, exists := seen[messageID]; exists {
			continue
		}
		seen[messageID] = struct{}{}
		ordered = append(ordered, messageID)
	}
	missing := make([]string, 0, len(state.ClaudeUsage)-len(ordered))
	for messageID := range state.ClaudeUsage {
		if _, exists := seen[messageID]; !exists {
			missing = append(missing, messageID)
		}
	}
	// Entries absent from an otherwise complete source-rebuilt order are stale
	// or malformed; keep their fallback deterministic.
	sort.Strings(missing)
	ordered = append(ordered, missing...)
	for len(ordered) > maxClaudeMessageIDState {
		retiredMessageID := ordered[0]
		ordered = ordered[1:]
		delete(state.ClaudeUsage, retiredMessageID)
		delete(state.ClaudeOccurrences, retiredMessageID)
	}
	for messageID := range state.ClaudeOccurrences {
		if _, exists := state.ClaudeUsage[messageID]; !exists {
			delete(state.ClaudeOccurrences, messageID)
		}
	}
	state.ClaudeMessageOrder = ordered
}

const claudeMessageOrderRebuildReadBytes = 64 * 1024

func claudeMessageOrderNeedsRebuild(state *TranscriptScannerState) bool {
	if state.ClaudeMessageOrderRebuildDone || len(state.ClaudeUsage) == 0 {
		return false
	}
	seen := make(map[string]struct{}, len(state.ClaudeMessageOrder))
	for _, messageID := range state.ClaudeMessageOrder {
		if _, exists := state.ClaudeUsage[messageID]; exists {
			seen[messageID] = struct{}{}
		}
	}
	return len(seen) < len(state.ClaudeUsage)
}

func validateClaudeMessageOrderRebuildState(state TranscriptScannerState) error {
	if state.ClaudeMessageOrderRebuildDone && state.ClaudeMessageOrderRebuildActive {
		return fmt.Errorf("Claude message-order rebuild state is inconsistent")
	}
	if !state.ClaudeMessageOrderRebuildActive {
		return nil
	}
	if state.ClaudeMessageOrderRebuildOffset < 0 || state.ClaudeMessageOrderRebuildOffset > state.Offset ||
		state.ClaudeMessageOrderRebuildSequence < 0 ||
		len(state.ClaudeMessageOrderRebuildBuffer) > claudeMessageOrderRebuildReadBytes ||
		len(state.ClaudeMessageOrderRebuildLine) > transcriptMaxPendingLineBytes ||
		len(state.ClaudeMessageOrderRebuildOrder) > maxClaudeMessageIDState ||
		len(state.ClaudeMessageOrderRebuildPresent) != len(state.ClaudeMessageOrderRebuildOrder) ||
		len(state.ClaudeMessageOrderRebuildOrder) == 0 && state.ClaudeMessageOrderRebuildOrderHead != 0 ||
		len(state.ClaudeMessageOrderRebuildOrder) > 0 && (state.ClaudeMessageOrderRebuildOrderHead < 0 || state.ClaudeMessageOrderRebuildOrderHead >= len(state.ClaudeMessageOrderRebuildOrder)) {
		return fmt.Errorf("Claude message-order rebuild state exceeds its bounds")
	}
	seen := make(map[string]struct{}, len(state.ClaudeMessageOrderRebuildOrder))
	for _, messageID := range state.ClaudeMessageOrderRebuildOrder {
		if len(messageID) == 0 || len(messageID) > 256 {
			return fmt.Errorf("Claude message-order rebuild ID is malformed")
		}
		if _, exists := seen[messageID]; exists {
			return fmt.Errorf("Claude message-order rebuild contains duplicate IDs")
		}
		if _, exists := state.ClaudeMessageOrderRebuildPresent[messageID]; !exists {
			return fmt.Errorf("Claude message-order rebuild index is inconsistent")
		}
		seen[messageID] = struct{}{}
	}
	return nil
}

// rebuildLegacyClaudeMessageOrder recovers the insertion order omitted by older
// Go cursor sidecars. It scans only through the already-committed byte offset,
// in the same byte/record budget as ordinary collection, and persists bounded
// progress so large transcripts do not turn an upgrade into one unbounded read.
func rebuildLegacyClaudeMessageOrder(file *os.File, state *TranscriptScannerState, options TranscriptScanOptions, remaining *int64, result *TranscriptScanResult) (bool, error) {
	if !state.ClaudeMessageOrderRebuildActive {
		state.ClaudeMessageOrderRebuildActive = true
		state.ClaudeMessageOrderRebuildOffset = 0
		state.ClaudeMessageOrderRebuildSequence = 0
		state.ClaudeMessageOrderRebuildBuffer = nil
		state.ClaudeMessageOrderRebuildLine = nil
		state.ClaudeMessageOrderRebuildOrder = nil
		state.ClaudeMessageOrderRebuildOrderHead = 0
		state.ClaudeMessageOrderRebuildPresent = make(map[string]struct{}, len(state.ClaudeUsage))
	}
	if state.ClaudeMessageOrderRebuildPresent == nil {
		state.ClaudeMessageOrderRebuildPresent = make(map[string]struct{}, len(state.ClaudeUsage))
	}
	processLine := func(line []byte) error {
		if len(line) > transcriptMaxPendingLineBytes {
			return fmt.Errorf("transcript record exceeds %d-byte parser limit", transcriptMaxPendingLineBytes)
		}
		result.RecordsParsed++
		trimmed := bytes.TrimSpace(line)
		if len(trimmed) == 0 || trimmed[0] != '{' || !json.Valid(trimmed) {
			return nil
		}
		var record map[string]any
		if err := json.Unmarshal(trimmed, &record); err != nil || record == nil {
			return nil
		}
		sequence := state.ClaudeMessageOrderRebuildSequence
		state.ClaudeMessageOrderRebuildSequence++
		if messageID, ok := claudeUsageMessageID(record, sequence); ok {
			if _, exists := state.ClaudeMessageOrderRebuildPresent[messageID]; !exists {
				if len(state.ClaudeMessageOrderRebuildOrder) < maxClaudeMessageIDState {
					state.ClaudeMessageOrderRebuildOrder = append(state.ClaudeMessageOrderRebuildOrder, messageID)
				} else {
					oldest := state.ClaudeMessageOrderRebuildOrder[state.ClaudeMessageOrderRebuildOrderHead]
					delete(state.ClaudeMessageOrderRebuildPresent, oldest)
					state.ClaudeMessageOrderRebuildOrder[state.ClaudeMessageOrderRebuildOrderHead] = messageID
					state.ClaudeMessageOrderRebuildOrderHead = (state.ClaudeMessageOrderRebuildOrderHead + 1) % maxClaudeMessageIDState
				}
				state.ClaudeMessageOrderRebuildPresent[messageID] = struct{}{}
			}
		}
		return nil
	}
	for state.ClaudeMessageOrderRebuildOffset < state.Offset || len(state.ClaudeMessageOrderRebuildBuffer) > 0 {
		if err := options.Context.Err(); err != nil {
			return false, err
		}
		for result.RecordsParsed < options.MaxRecords {
			newline := bytes.IndexByte(state.ClaudeMessageOrderRebuildBuffer, '\n')
			if newline < 0 {
				if len(state.ClaudeMessageOrderRebuildBuffer) > 0 {
					state.ClaudeMessageOrderRebuildLine = append(state.ClaudeMessageOrderRebuildLine, state.ClaudeMessageOrderRebuildBuffer...)
					state.ClaudeMessageOrderRebuildBuffer = nil
					if len(state.ClaudeMessageOrderRebuildLine) > transcriptMaxPendingLineBytes {
						return false, fmt.Errorf("transcript record exceeds %d-byte parser limit", transcriptMaxPendingLineBytes)
					}
				}
				break
			}
			line := make([]byte, 0, len(state.ClaudeMessageOrderRebuildLine)+newline+1)
			line = append(line, state.ClaudeMessageOrderRebuildLine...)
			line = append(line, state.ClaudeMessageOrderRebuildBuffer[:newline+1]...)
			state.ClaudeMessageOrderRebuildLine = nil
			state.ClaudeMessageOrderRebuildBuffer = state.ClaudeMessageOrderRebuildBuffer[newline+1:]
			if err := processLine(line); err != nil {
				return false, err
			}
		}
		if result.RecordsParsed >= options.MaxRecords {
			return false, nil
		}
		if len(state.ClaudeMessageOrderRebuildBuffer) > 0 {
			continue
		}
		if state.ClaudeMessageOrderRebuildOffset >= state.Offset {
			break
		}
		if *remaining <= 0 {
			return false, nil
		}
		readSize := int64(claudeMessageOrderRebuildReadBytes)
		if readSize > *remaining {
			readSize = *remaining
		}
		if left := state.Offset - state.ClaudeMessageOrderRebuildOffset; readSize > left {
			readSize = left
		}
		chunk := make([]byte, int(readSize))
		n, err := file.ReadAt(chunk, state.ClaudeMessageOrderRebuildOffset)
		if err != nil && err != io.EOF {
			return false, err
		}
		if n == 0 {
			return false, fmt.Errorf("legacy Claude cursor offset exceeds transcript during order migration")
		}
		chunk = chunk[:n]
		state.ClaudeMessageOrderRebuildOffset += int64(n)
		*remaining -= int64(n)
		result.BytesRead += int64(n)
		result.FilesRead = 1
		state.ClaudeMessageOrderRebuildBuffer = append(state.ClaudeMessageOrderRebuildBuffer, chunk...)
	}
	if len(state.ClaudeMessageOrderRebuildLine) > 0 {
		if json.Valid(bytes.TrimSpace(state.ClaudeMessageOrderRebuildLine)) {
			if result.RecordsParsed >= options.MaxRecords {
				return false, nil
			}
			if err := processLine(state.ClaudeMessageOrderRebuildLine); err != nil {
				return false, err
			}
		}
		state.ClaudeMessageOrderRebuildLine = nil
	}
	state.ClaudeMessageOrder = make([]string, 0, len(state.ClaudeMessageOrderRebuildOrder))
	retained := make(map[string]struct{}, len(state.ClaudeMessageOrderRebuildOrder))
	for index := range state.ClaudeMessageOrderRebuildOrder {
		messageID := state.ClaudeMessageOrderRebuildOrder[(state.ClaudeMessageOrderRebuildOrderHead+index)%len(state.ClaudeMessageOrderRebuildOrder)]
		if _, exists := state.ClaudeUsage[messageID]; !exists {
			continue
		}
		state.ClaudeMessageOrder = append(state.ClaudeMessageOrder, messageID)
		retained[messageID] = struct{}{}
	}
	for messageID := range state.ClaudeUsage {
		if _, exists := retained[messageID]; !exists {
			delete(state.ClaudeUsage, messageID)
			delete(state.ClaudeOccurrences, messageID)
		}
	}
	state.ClaudeSequence = state.ClaudeMessageOrderRebuildSequence
	state.ClaudeMessageOrderRebuildDone = true
	state.ClaudeMessageOrderRebuildActive = false
	state.ClaudeMessageOrderRebuildOffset = 0
	state.ClaudeMessageOrderRebuildBuffer = nil
	state.ClaudeMessageOrderRebuildLine = nil
	state.ClaudeMessageOrderRebuildSequence = 0
	state.ClaudeMessageOrderRebuildOrder = nil
	state.ClaudeMessageOrderRebuildOrderHead = 0
	state.ClaudeMessageOrderRebuildPresent = nil
	return true, nil
}

func claudeUsageMessageID(record map[string]any, sequence int64) (string, bool) {
	typeName, _ := record["type"].(string)
	if typeName != "assistant" || transcriptTimestamp(record["timestamp"]) <= 0 {
		return "", false
	}
	message, ok := record["message"].(map[string]any)
	if !ok {
		return "", false
	}
	usage, _ := message["usage"].(map[string]any)
	if len(claudeRecordUsage(usage)) == 0 {
		return "", false
	}
	messageID, _ := message["id"].(string)
	messageID = strings.TrimSpace(messageID)
	if messageID == "" {
		messageID = fmt.Sprintf("line-%d", sequence)
	}
	if len(messageID) > 256 {
		messageID = messageID[:256]
	}
	return messageID, true
}

func claudeUsageCounters(values map[string]any) UsageCounters {
	result := UsageCounters{}
	for _, key := range []string{"input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"} {
		if value := mapNumber(values, key); value != nil && finite(*value) && *value >= 0 {
			result[key] = *value
		}
	}
	if creation, ok := values["cache_creation"].(map[string]any); ok {
		for key, aliases := range map[string][]string{
			"cache_creation_input_tokens_5m": {"ephemeral_5m_input_tokens", "ephemeral5mInputTokens"},
			"cache_creation_input_tokens_1h": {"ephemeral_1h_input_tokens", "ephemeral1hInputTokens"},
		} {
			for _, alias := range aliases {
				if value := mapNumber(creation, alias); value != nil && finite(*value) && *value >= 0 {
					result[key] = *value
					break
				}
			}
		}
	}
	return result
}

func claudeRecordUsage(values map[string]any) UsageCounters {
	direct := claudeUsageCounters(values)
	if len(direct) > 0 {
		return direct
	}
	iterations, ok := values["iterations"].([]any)
	if !ok {
		return direct
	}
	totals := UsageCounters{}
	for _, item := range iterations {
		usage, ok := item.(map[string]any)
		if !ok {
			continue
		}
		for key, value := range claudeUsageCounters(usage) {
			totals[key] += value
		}
	}
	return totals
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

func transcriptThreadIdentity(source TranscriptSource, state *TranscriptScannerState) string {
	if source.Provider == "claude" {
		return filepath.Clean(source.Path)
	}
	if source.Provider == "codex" {
		if state.CodexForkChildThreadID != "" {
			return state.CodexForkChildThreadID
		}
		if state.CodexThreadID != "" {
			return state.CodexThreadID
		}
		return filepath.Clean(source.Path)
	}
	return source.AgentIDOrThread()
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
		value := strings.TrimSpace(typed)
		if value == "" {
			return 0
		}
		if parsed, err := time.Parse(time.RFC3339Nano, value); err == nil {
			return float64(parsed.UnixNano()) / 1e9
		}
		// Python's datetime.fromisoformat accepts naive ISO timestamps and treats
		// them as UTC for transcript accounting. Match that behavior for common
		// ISO date/time forms rather than silently dropping their usage records.
		for _, layout := range []string{
			"2006-01-02T15:04:05.999999999",
			"2006-01-02 15:04:05.999999999",
			"2006-01-02",
		} {
			parsed, err := time.ParseInLocation(layout, value, time.UTC)
			if err == nil {
				return float64(parsed.UnixNano()) / 1e9
			}
		}
	}
	return 0
}

func normalizeTranscriptEffort(value string) string {
	value = boundedTranscriptContext(value)
	if value == "" {
		return "unknown"
	}
	return value
}

func normalizeTranscriptPricingContext(value string) string {
	value = boundedTranscriptContext(value)
	if value == "" {
		return "default"
	}
	return value
}

// boundedTranscriptContext matches Python's transcript context normalizers,
// including their 64-code-point limit rather than a UTF-8 byte limit.
func boundedTranscriptContext(value string) string {
	value = pythonTranscriptLower(strings.TrimSpace(value))
	runes := []rune(value)
	if len(runes) > 64 {
		value = string(runes[:64])
	}
	return value
}

func pythonTranscriptLower(value string) string {
	var lowered strings.Builder
	for _, r := range value {
		if r == '\u0130' {
			// Python's full Unicode lowercase expands LATIN CAPITAL LETTER I
			// WITH DOT ABOVE before applying the transcript's code-point bound.
			lowered.WriteString("i\u0307")
			continue
		}
		lowered.WriteRune(unicode.ToLower(r))
	}
	return lowered.String()
}

func updateCodexUsageContext(state *TranscriptScannerState, payload map[string]any, effortFields ...string) {
	for _, field := range effortFields {
		if value, ok := payload[field]; ok {
			state.CodexEffort = normalizeTranscriptEffort(transcriptContextValue(value))
			break
		}
	}
	if value, ok := codexContextField(payload, "pricing_profile", "profile"); ok {
		state.CodexPricingProfile = normalizeTranscriptPricingContext(transcriptContextValue(value))
	}
	if value, ok := codexContextField(payload, "service_tier", "serviceTier"); ok {
		state.CodexServiceTier = normalizeTranscriptPricingContext(transcriptContextValue(value))
	}
}

func codexContextField(payload map[string]any, primary, fallback string) (any, bool) {
	primaryValue, primaryPresent := payload[primary]
	fallbackValue, fallbackPresent := payload[fallback]
	if !primaryPresent && !fallbackPresent {
		return nil, false
	}
	if transcriptContextTruthy(primaryValue) {
		return primaryValue, true
	}
	if fallbackPresent {
		return fallbackValue, true
	}
	return primaryValue, true
}

func transcriptContextValue(value any) string {
	if !transcriptContextTruthy(value) {
		return ""
	}
	return fmt.Sprint(value)
}

func transcriptContextTruthy(value any) bool {
	if value == nil {
		return false
	}
	switch typed := value.(type) {
	case string:
		return typed != ""
	case bool:
		return typed
	case int:
		return typed != 0
	case int8:
		return typed != 0
	case int16:
		return typed != 0
	case int32:
		return typed != 0
	case int64:
		return typed != 0
	case uint:
		return typed != 0
	case uint8:
		return typed != 0
	case uint16:
		return typed != 0
	case uint32:
		return typed != 0
	case uint64:
		return typed != 0
	case float32:
		return typed != 0
	case float64:
		return typed != 0
	}
	reflected := reflect.ValueOf(value)
	switch reflected.Kind() {
	case reflect.Array, reflect.Map, reflect.Slice, reflect.String:
		return reflected.Len() > 0
	case reflect.Chan, reflect.Func, reflect.Interface, reflect.Pointer:
		return !reflected.IsNil()
	default:
		return true
	}
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
	copyState.PrefixCompressed = append([]byte(nil), state.PrefixCompressed...)
	copyState.PendingLine = append([]byte(nil), state.PendingLine...)
	copyState.ParsedTail = append([]byte(nil), state.ParsedTail...)
	copyState.ParsedTailCompressed = append([]byte(nil), state.ParsedTailCompressed...)
	copyState.ClaudeUsage = make(map[string]UsageCounters, len(state.ClaudeUsage))
	for key, counters := range state.ClaudeUsage {
		copyState.ClaudeUsage[key] = cloneCounters(counters)
	}
	copyState.ClaudeOccurrences = make(map[string]int64, len(state.ClaudeOccurrences))
	for key, count := range state.ClaudeOccurrences {
		copyState.ClaudeOccurrences[key] = count
	}
	copyState.ClaudeMessageOrder = append([]string(nil), state.ClaudeMessageOrder...)
	copyState.ClaudeMessageOrderRebuildBuffer = append([]byte(nil), state.ClaudeMessageOrderRebuildBuffer...)
	copyState.ClaudeMessageOrderRebuildLine = append([]byte(nil), state.ClaudeMessageOrderRebuildLine...)
	copyState.ClaudeMessageOrderRebuildOrder = append([]string(nil), state.ClaudeMessageOrderRebuildOrder...)
	if state.ClaudeMessageOrderRebuildPresent != nil {
		copyState.ClaudeMessageOrderRebuildPresent = make(map[string]struct{}, len(state.ClaudeMessageOrderRebuildPresent))
		for key := range state.ClaudeMessageOrderRebuildPresent {
			copyState.ClaudeMessageOrderRebuildPresent[key] = struct{}{}
		}
	}
	copyState.ClaudeSequence = state.ClaudeSequence
	copyState.CodexUsage = cloneCounters(state.CodexUsage)
	copyState.CodexModel = state.CodexModel
	copyState.CodexModelEvidence = state.CodexModelEvidence
	copyState.CodexEffort = state.CodexEffort
	copyState.CodexPricingProfile = state.CodexPricingProfile
	copyState.CodexServiceTier = state.CodexServiceTier
	copyState.CodexSuppressForkHistory = state.CodexSuppressForkHistory
	copyState.CodexThreadID = state.CodexThreadID
	copyState.CodexForkChildThreadID = state.CodexForkChildThreadID
	copyState.CodexForkID = state.CodexForkID
	copyState.CodexForkRepairComplete = state.CodexForkRepairComplete
	copyState.CodexSequence = state.CodexSequence
	return copyState
}

func usageAtomTombstone(atom UsageAtom) (UsageTombstone, bool) {
	provider, _ := atom.Payload["provider"].(string)
	model, _ := atom.Payload["model"].(string)
	threadID, _ := atom.Payload["thread_id"].(string)
	quantity, ok := atom.Payload["quantity"].(float64)
	if provider != "openai" || model == "" || threadID == "" || !ok {
		return UsageTombstone{}, false
	}
	return UsageTombstone{
		EventID: atom.EventID, Direction: atom.Direction, Modality: atom.Modality,
		CacheRole: atom.CacheRole, Unit: atom.Unit, ObservedAt: atom.ObservedAt,
		Quantity: quantity, Provider: provider, Model: model, ThreadID: threadID,
	}, true
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

func transcriptCursorSampleBytes(disk *transcriptCursorDisk) int64 {
	var total int64
	for _, states := range []map[string]TranscriptScannerState{disk.States, disk.Pending} {
		for _, state := range states {
			total += int64(len(state.Prefix) + len(state.ParsedTail))
		}
	}
	return total
}
