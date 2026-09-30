package stats

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
)

const transcriptCheckpointBudget int64 = 64 << 20

// The writer remains the sole durable owner. Scanner state is decoded only
// for admitted sources and released after its checkpoint is encoded.
type agentCheckpointWriter interface {
	AppendWithCheckpoints(context.Context, AppendBatch, []Checkpoint) (AppendResult, error)
	AppendWithCheckpointsAndRetire(context.Context, AppendBatch, []Checkpoint, *CheckpointRetirement) (AppendResult, error)
	LoadCheckpoint(context.Context, string) (Checkpoint, bool, error)
	ListCheckpointMetadata(context.Context, string) ([]CheckpointMetadata, error)
	ListCheckpointMetadataForKeys(context.Context, string, []string) ([]CheckpointMetadata, error)
	LoadCheckpointComparisonSamples(context.Context, string) (CheckpointComparisonSamples, error)
	EnsureCheckpoints(context.Context, []string) error
	UncoveredUnavailableSpans(context.Context, []UnavailableSpan) ([]UnavailableSpan, error)
}

type databaseTranscriptCursors struct {
	writer           agentCheckpointWriter
	config           AgentTokenCollectorConfig
	initMu           sync.Mutex
	initialized      bool
	scheduleRevision int64
}

type transcriptCheckpointMetadata struct {
	Path                          string
	UsageCommittedEOFSize         int64
	UsageCommittedEOFKnown        bool
	PendingLineBytes              int
	ClaudeMessageOrderRebuildDone bool
	CodexForkRepairComplete       bool
}

func encodeTranscriptCheckpoint(key string, state TranscriptScannerState, revision int64) (Checkpoint, error) {
	cp := Checkpoint{Key: key, Kind: "transcript", SourceID: state.Path, ExpectedRevision: revision, Version: 1, Offset: state.Offset, FileSize: state.FileSize, FileModTimeNS: state.FileModTimeNS, Device: state.Device, Inode: state.Inode, Prefix: state.Prefix, ParsedTail: state.ParsedTail, PendingLine: state.PendingLine, RebuildBuffer: state.ClaudeMessageOrderRebuildBuffer, RebuildLine: state.ClaudeMessageOrderRebuildLine}
	meta := transcriptCheckpointMetadata{Path: state.Path, UsageCommittedEOFSize: state.UsageCommittedEOFSize, UsageCommittedEOFKnown: state.UsageCommittedEOFKnown, PendingLineBytes: len(state.PendingLine), ClaudeMessageOrderRebuildDone: state.ClaudeMessageOrderRebuildDone, CodexForkRepairComplete: state.CodexForkRepairComplete}
	var err error
	cp.Metadata, err = json.Marshal(meta)
	if err != nil {
		return Checkpoint{}, err
	}
	state.Prefix = nil
	state.PrefixCompressed = nil
	state.ParsedTail = nil
	state.ParsedTailCompressed = nil
	state.PendingLine = nil
	state.ClaudeMessageOrderRebuildBuffer = nil
	state.ClaudeMessageOrderRebuildLine = nil
	cp.State, err = json.Marshal(state)
	// Maps and their string keys have larger decoded representations than JSON.
	// Include conservative map overhead alongside the exact byte fragments.
	cp.DecodedBytes = int64(len(cp.State)*4 + len(cp.Prefix) + len(cp.ParsedTail) + len(cp.PendingLine) + len(cp.RebuildBuffer) + len(cp.RebuildLine))
	return cp, err
}
func decodeTranscriptCheckpoint(cp Checkpoint) (TranscriptScannerState, error) {
	var state TranscriptScannerState
	if cp.Version != 1 {
		return state, fmt.Errorf("unsupported transcript checkpoint version %d", cp.Version)
	}
	if err := json.Unmarshal(cp.State, &state); err != nil {
		return state, err
	}
	state.Prefix = cp.Prefix
	state.ParsedTail = cp.ParsedTail
	state.PendingLine = cp.PendingLine
	state.ClaudeMessageOrderRebuildBuffer = cp.RebuildBuffer
	state.ClaudeMessageOrderRebuildLine = cp.RebuildLine
	state.checkpointRevision = cp.Revision
	return state, nil
}
func (s *databaseTranscriptCursors) ensure() error {
	s.initMu.Lock()
	defer s.initMu.Unlock()
	if s.writer == nil {
		return errors.New("stats checkpoint writer is unavailable")
	}
	if s.initialized {
		return nil
	}
	err := s.migrate()
	s.initialized = err == nil
	return err
}
func (s *databaseTranscriptCursors) migrate() error {
	ctx := context.Background()
	paths := []string{s.config.TranscriptCursorPath, codexForkRepairMarkerPath(s.config.TranscriptCursorPath)}
	if s.config.OpenCodeDatabase != "" {
		paths = append(paths, s.config.CursorPath)
	}
	if err := s.writer.EnsureCheckpoints(ctx, paths); err != nil {
		return err
	}
	if _, exists, err := s.writer.LoadCheckpoint(ctx, "collector/migration"); err != nil || exists {
		return err
	}
	// The paired backup precedes every decoder. Pending journal entries are
	// deliberately ignored: their facts may need replay after an interrupted append.
	disk, err := readLegacyTranscriptCursor(s.config.TranscriptCursorPath)
	if err != nil {
		return err
	}
	updates := make([]Checkpoint, 0, len(disk.States)+5)
	for key, state := range disk.States {
		cp, err := encodeTranscriptCheckpoint(key, state, 0)
		if err != nil {
			return err
		}
		updates = append(updates, cp)
	}
	add := func(key, kind string, value any) error {
		raw, err := json.Marshal(value)
		if err != nil {
			return err
		}
		updates = append(updates, Checkpoint{Key: key, Kind: kind, Version: 1, State: raw})
		return nil
	}
	if err := add("collector/schedule", "schedule", transcriptCursorSchedule{disk.NextSource, disk.NextHistoricalSource, disk.LiveTranscriptPriorityRuns}); err != nil {
		return err
	}
	if err := add("collector/frontier", "frontier", disk.CodexForkRepair); err != nil {
		return err
	}
	roots := disk.CodexForkRepairCompletedRoots
	marker, err := os.ReadFile(codexForkRepairMarkerPath(s.config.TranscriptCursorPath))
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if err == nil {
		var value struct {
			Version        int      `json:"version"`
			CompletedRoots []string `json:"completed_roots"`
		}
		if err := json.Unmarshal(marker, &value); err != nil || value.Version != 1 {
			return errors.New("Codex fork repair marker is malformed")
		}
		roots, err = mergeCodexForkRepairRootIDs(roots, value.CompletedRoots)
		if err != nil {
			return err
		}
	}
	if err := add("collector/completed-roots", "roots", roots); err != nil {
		return err
	}
	if s.config.OpenCodeDatabase != "" {
		open, err := readLegacyOpenCodeCursor(s.config.CursorPath)
		if err != nil {
			return err
		}
		if err := add("opencode/"+s.config.OpenCodeDatabase, "opencode", open.State); err != nil {
			return err
		}
	}
	if err := add("collector/migration", "migration", true); err != nil {
		return err
	}
	_, err = s.writer.AppendWithCheckpoints(ctx, AppendBatch{}, updates)
	return err
}
func (s *databaseTranscriptCursors) load(key string, target any) (int64, error) {
	if err := s.ensure(); err != nil {
		return 0, err
	}
	cp, exists, err := s.writer.LoadCheckpoint(context.Background(), key)
	if err != nil || !exists {
		return 0, err
	}
	if cp.Version != 1 {
		return 0, fmt.Errorf("unsupported collector checkpoint version %d", cp.Version)
	}
	return cp.Revision, json.Unmarshal(cp.State, target)
}
func (s *databaseTranscriptCursors) State(source TranscriptSource) (TranscriptScannerState, error) {
	if err := s.ensure(); err != nil {
		return TranscriptScannerState{}, err
	}
	cp, exists, err := s.writer.LoadCheckpoint(context.Background(), transcriptCursorKey(source))
	if err != nil {
		return TranscriptScannerState{}, err
	}
	if !exists {
		return TranscriptScannerState{}, nil
	}
	if cp.Kind == "retired" {
		return TranscriptScannerState{Path: source.Path, checkpointRevision: cp.Revision}, nil
	}
	return decodeTranscriptCheckpoint(cp)
}
func (s *databaseTranscriptCursors) Metadata(keys []string) (map[string]CheckpointMetadata, error) {
	if err := s.ensure(); err != nil {
		return nil, err
	}
	rows, err := s.writer.ListCheckpointMetadataForKeys(context.Background(), "transcript", keys)
	if err != nil {
		return nil, err
	}
	out := make(map[string]CheckpointMetadata, len(rows))
	for _, row := range rows {
		out[row.Key] = row
	}
	return out, nil
}
func metadataTranscriptState(row CheckpointMetadata) (TranscriptScannerState, error) {
	var meta transcriptCheckpointMetadata
	if len(row.Metadata) > 0 {
		if err := json.Unmarshal(row.Metadata, &meta); err != nil {
			return TranscriptScannerState{}, err
		}
	}
	state := TranscriptScannerState{Path: meta.Path, Offset: row.Offset, FileSize: row.FileSize, FileModTimeNS: row.FileModTimeNS, UsageCommittedEOFSize: meta.UsageCommittedEOFSize, UsageCommittedEOFKnown: meta.UsageCommittedEOFKnown, CodexForkRepairComplete: meta.CodexForkRepairComplete}
	if meta.PendingLineBytes > 0 {
		state.PendingLine = []byte{0}
	}
	return state, nil
}

func checkpointCanUseUnchangedFastPath(source TranscriptSource, checkpoint CheckpointMetadata) bool {
	if checkpoint.Revision == 0 || checkpoint.Kind != "transcript" || checkpoint.Version != 1 || checkpoint.SourceID != source.Path || checkpoint.Offset <= 0 || checkpoint.Offset != checkpoint.FileSize {
		return false
	}
	var metadata transcriptCheckpointMetadata
	if err := json.Unmarshal(checkpoint.Metadata, &metadata); err != nil {
		return false
	}
	if metadata.Path != source.Path || !metadata.UsageCommittedEOFKnown || metadata.UsageCommittedEOFSize != checkpoint.FileSize || metadata.PendingLineBytes != 0 {
		return false
	}
	if source.Provider == "claude" && !metadata.ClaudeMessageOrderRebuildDone {
		return false
	}
	if source.CodexForkRepairOnly && !metadata.CodexForkRepairComplete {
		return false
	}
	return true
}

// unchangedCheckpointSource verifies the same bounded samples as ScanTranscript
// without decoding the persisted parser state for an EOF source that has not
// changed. A caller falls back to the full scanner for every ineligible case.
type checkpointSourceVerification struct {
	Unchanged, SourceChanged bool
	BytesRead                int64
}

func unchangedCheckpointSource(source TranscriptSource, checkpoint CheckpointMetadata, samples CheckpointComparisonSamples, maxBytes int64) (checkpointSourceVerification, error) {
	if !checkpointCanUseUnchangedFastPath(source, checkpoint) {
		return checkpointSourceVerification{}, nil
	}
	if maxBytes <= 0 {
		return checkpointSourceVerification{}, nil
	}
	file, err := os.Open(source.Path)
	if err != nil {
		return checkpointSourceVerification{}, nil
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return checkpointSourceVerification{}, nil
	}
	device, inode := fileIdentity(info)
	if !info.Mode().IsRegular() || info.Size() != checkpoint.FileSize || info.ModTime().UnixNano() != checkpoint.FileModTimeNS || checkpoint.Device == 0 || checkpoint.Inode == 0 || checkpoint.Device != device || checkpoint.Inode != inode {
		return checkpointSourceVerification{}, nil
	}
	if len(samples.Prefix) == 0 && checkpoint.FileSize > 0 {
		return checkpointSourceVerification{}, nil
	}
	sampleBytes := maxBytes / 8
	if sampleBytes > transcriptPrefixBytes {
		sampleBytes = transcriptPrefixBytes
	}
	if sampleBytes < 1 {
		sampleBytes = 1
	}
	var bytesRead int64
	readAndCompare := func(offset int64, expected []byte) (bool, bool, error) {
		if len(expected) == 0 {
			return true, true, nil
		}
		length := len(expected)
		if int64(length) > sampleBytes {
			length = int(sampleBytes)
		}
		if int64(length) > maxBytes-bytesRead {
			return false, false, nil
		}
		actual := make([]byte, length)
		n, readErr := file.ReadAt(actual, offset)
		bytesRead += int64(n)
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			return false, false, readErr
		}
		return n == length && bytes.Equal(actual[:n], expected[:length]), n == length, nil
	}
	verification := checkpointSourceVerification{}
	if matched, compared, err := readAndCompare(0, samples.Prefix); err != nil {
		verification.BytesRead = bytesRead
		return verification, err
	} else if compared && !matched {
		verification.SourceChanged = true
		verification.BytesRead = bytesRead
		return verification, nil
	} else if !compared {
		verification.BytesRead = bytesRead
		return verification, nil
	}
	if len(samples.ParsedTail) > 0 {
		tailLength := len(samples.ParsedTail)
		if int64(tailLength) > sampleBytes {
			tailLength = int(sampleBytes)
		}
		if checkpoint.Offset < int64(tailLength) {
			verification.BytesRead = bytesRead
			return verification, nil
		}
		if matched, compared, err := readAndCompare(checkpoint.Offset-int64(tailLength), samples.ParsedTail[len(samples.ParsedTail)-tailLength:]); err != nil {
			verification.BytesRead = bytesRead
			return verification, err
		} else if compared && !matched {
			verification.SourceChanged = true
			verification.BytesRead = bytesRead
			return verification, nil
		} else if !compared {
			verification.BytesRead = bytesRead
			return verification, nil
		}
	}
	verification.Unchanged = true
	verification.BytesRead = bytesRead
	return verification, nil
}
func (s *databaseTranscriptCursors) Schedule() (transcriptCursorSchedule, error) {
	var value transcriptCursorSchedule
	rev, err := s.load("collector/schedule", &value)
	s.scheduleRevision = rev
	return value, err
}
func (s *databaseTranscriptCursors) NextSource() (string, error) {
	v, e := s.Schedule()
	return v.NextSource, e
}
func (s *databaseTranscriptCursors) CodexForkRepairFrontier() (codexForkRepairFrontierState, error) {
	var v codexForkRepairFrontierState
	_, e := s.load("collector/frontier", &v)
	return v, e
}
func (s *databaseTranscriptCursors) SaveCodexForkRepairFrontier(v codexForkRepairFrontierState) error {
	if err := validateCodexForkRepairFrontier(v); err != nil {
		return err
	}
	var old codexForkRepairFrontierState
	rev, err := s.load("collector/frontier", &old)
	if err != nil {
		return err
	}
	raw, err := json.Marshal(v)
	if err != nil {
		return err
	}
	_, err = s.writer.AppendWithCheckpoints(context.Background(), AppendBatch{}, []Checkpoint{{Key: "collector/frontier", Kind: "frontier", Version: 1, ExpectedRevision: rev, State: raw}})
	return err
}
func (s *databaseTranscriptCursors) CompletedCodexForkRepairRoots() (map[string]struct{}, error) {
	var roots []string
	_, err := s.load("collector/completed-roots", &roots)
	out := make(map[string]struct{}, len(roots))
	for _, root := range roots {
		out[root] = struct{}{}
	}
	return out, err
}
func (s *databaseTranscriptCursors) completedRootsCheckpoint(roots []string) (Checkpoint, error) {
	var existing []string
	rev, err := s.load("collector/completed-roots", &existing)
	if err != nil {
		return Checkpoint{}, err
	}
	merged, err := mergeCodexForkRepairRootIDs(existing, roots)
	if err != nil {
		return Checkpoint{}, err
	}
	raw, err := json.Marshal(merged)
	if err != nil {
		return Checkpoint{}, err
	}
	return Checkpoint{Key: "collector/completed-roots", Kind: "roots", Version: 1, ExpectedRevision: rev, State: raw}, nil
}

func (c *AgentTokenCollector) appendCheckpointBatch(ctx context.Context, batch AppendBatch, receipts []agentTokenReceipt) (AppendResult, error) {
	var checkpoints []Checkpoint
	var retirement *CheckpointRetirement
	for _, receipt := range receipts {
		checkpoints = append(checkpoints, receipt.checkpoints...)
		if receipt.retirement != nil {
			if retirement == nil {
				retirement = &CheckpointRetirement{Kind: receipt.retirement.Kind, PreserveExistingSourceFiles: receipt.retirement.PreserveExistingSourceFiles}
			} else if retirement.Kind != receipt.retirement.Kind {
				return AppendResult{}, errors.New("conflicting checkpoint retirement kinds")
			} else {
				retirement.PreserveExistingSourceFiles = retirement.PreserveExistingSourceFiles || receipt.retirement.PreserveExistingSourceFiles
			}
			retirement.KeepKeys = append(retirement.KeepKeys, receipt.retirement.KeepKeys...)
		}
	}
	if len(checkpoints) == 0 && retirement == nil {
		return c.writer.AppendBatch(ctx, batch)
	}
	writer, ok := c.writer.(agentCheckpointWriter)
	if !ok {
		return AppendResult{}, errors.New("checkpoint writer is unavailable")
	}
	return writer.AppendWithCheckpointsAndRetire(ctx, batch, checkpoints, retirement)
}

// UncoveredUnavailableSpans keeps recovery gaps outside intervals already
// recorded by this writer. Direct append remains strict: callers must append
// these fragments and their checkpoint in the same subsequent transaction,
// which rejects any conflicting writer that raced this snapshot.
func (w *Writer) UncoveredUnavailableSpans(ctx context.Context, spans []UnavailableSpan) ([]UnavailableSpan, error) {
	if w == nil || w.db == nil {
		return nil, errors.New("nil YO!stats writer")
	}
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	var uncovered []UnavailableSpan
	for _, span := range spans {
		if err := span.Validate(); err != nil {
			return nil, err
		}
		rows, err := w.db.QueryContext(ctx, `SELECT started_at,ended_at FROM coverage_epochs WHERE family=? AND source_id=? AND started_at<? AND (ended_at IS NULL OR ended_at>?)
   UNION ALL SELECT started_at,ended_at FROM unavailable_spans WHERE family=? AND source_id=? AND started_at<? AND ended_at>? ORDER BY 1`, span.Family, span.SourceID, span.EndedAt, span.StartedAt, span.Family, span.SourceID, span.EndedAt, span.StartedAt)
		if err != nil {
			return nil, err
		}
		start := span.StartedAt
		for rows.Next() {
			var coveredStart float64
			var coveredEnd sql.NullFloat64
			if err := rows.Scan(&coveredStart, &coveredEnd); err != nil {
				rows.Close()
				return nil, err
			}
			if coveredStart > start {
				fragment := span
				fragment.StartedAt = start
				fragment.EndedAt = min(coveredStart, span.EndedAt)
				uncovered = append(uncovered, fragment)
			}
			if !coveredEnd.Valid {
				start = span.EndedAt
			} else {
				start = max(start, coveredEnd.Float64)
			}
			if start >= span.EndedAt {
				break
			}
		}
		readErr := rows.Err()
		closeErr := rows.Close()
		if readErr != nil {
			return nil, readErr
		}
		if closeErr != nil {
			return nil, closeErr
		}
		if start < span.EndedAt {
			fragment := span
			fragment.StartedAt = start
			uncovered = append(uncovered, fragment)
		}
	}
	return uncovered, nil
}
