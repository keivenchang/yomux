package stats

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestUnchangedCheckpointSourceUsesExactSamplesAndFallsBackForChanges(t *testing.T) {
	path := filepath.Join(t.TempDir(), "source.jsonl")
	content := strings.Repeat("x", 256<<10)
	if err := os.WriteFile(path, []byte(content), 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	device, inode := fileIdentity(info)
	metadata, err := json.Marshal(transcriptCheckpointMetadata{Path: path, UsageCommittedEOFSize: info.Size(), UsageCommittedEOFKnown: true, ClaudeMessageOrderRebuildDone: true})
	if err != nil {
		t.Fatal(err)
	}
	checkpoint := CheckpointMetadata{Key: "key", Kind: "transcript", SourceID: path, Revision: 1, Version: 1, Offset: info.Size(), FileSize: info.Size(), FileModTimeNS: info.ModTime().UnixNano(), Device: device, Inode: inode, Metadata: metadata}
	samples := CheckpointComparisonSamples{Prefix: []byte(content[:64<<10]), ParsedTail: []byte(content[len(content)-(64<<10):])}
	source := TranscriptSource{Path: path, Provider: "claude"}
	verification, err := unchangedCheckpointSource(source, checkpoint, samples, 4<<20)
	if err != nil || !verification.Unchanged {
		t.Fatalf("unchanged file was not accepted: verification=%+v err=%v", verification, err)
	}

	changed := []byte(content)
	changed[0] = 'y'
	if err := os.WriteFile(path, changed, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
		t.Fatal(err)
	}
	verification, err = unchangedCheckpointSource(source, checkpoint, samples, 4<<20)
	if err != nil || verification.Unchanged || !verification.SourceChanged {
		t.Fatalf("same-size prefix rewrite was not retained: verification=%+v err=%v", verification, err)
	}

	changed = []byte(content)
	changed[len(changed)-1] = 'y'
	if err := os.WriteFile(path, changed, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
		t.Fatal(err)
	}
	verification, err = unchangedCheckpointSource(source, checkpoint, samples, 4<<20)
	if err != nil || verification.Unchanged || !verification.SourceChanged {
		t.Fatalf("same-size tail rewrite was not retained: verification=%+v err=%v", verification, err)
	}

	if err := os.WriteFile(path, []byte(content+"append"), 0600); err != nil {
		t.Fatal(err)
	}
	verification, err = unchangedCheckpointSource(source, checkpoint, samples, 4<<20)
	if err != nil || verification.Unchanged || verification.SourceChanged {
		t.Fatalf("append fast-path classification: verification=%+v err=%v", verification, err)
	}

	metadata, err = json.Marshal(transcriptCheckpointMetadata{Path: path, UsageCommittedEOFSize: info.Size(), UsageCommittedEOFKnown: true})
	if err != nil {
		t.Fatal(err)
	}
	checkpoint.Metadata = metadata
	verification, err = unchangedCheckpointSource(source, checkpoint, samples, 4<<20)
	if err != nil || verification.Unchanged || verification.SourceChanged {
		t.Fatalf("incomplete legacy Claude parser state was skipped: verification=%+v err=%v", verification, err)
	}
}

func TestUnchangedCheckpointRewriteFallbackStaysWithinScanByteBudget(t *testing.T) {
	path := filepath.Join(t.TempDir(), "source.jsonl")
	content := []byte(strings.Repeat(" { }\n", 16<<10))
	if err := os.WriteFile(path, content, 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	device, inode := fileIdentity(info)
	source := TranscriptSource{Path: path, Provider: "codex", SourceID: "fixture"}
	state := TranscriptScannerState{Path: path, Offset: int64(len(content)), FileSize: int64(len(content)), FileModTimeNS: info.ModTime().UnixNano(), Device: device, Inode: inode, Prefix: append([]byte(nil), content...), ParsedTail: append([]byte(nil), content...), UsageCommittedEOFKnown: true, UsageCommittedEOFSize: int64(len(content))}
	checkpoint, err := encodeTranscriptCheckpoint(transcriptCursorKey(source), state, 0)
	if err != nil {
		t.Fatal(err)
	}
	metadata := CheckpointMetadata{Key: checkpoint.Key, Kind: checkpoint.Kind, SourceID: checkpoint.SourceID, Revision: 1, Version: checkpoint.Version, Offset: checkpoint.Offset, FileSize: checkpoint.FileSize, FileModTimeNS: checkpoint.FileModTimeNS, Device: checkpoint.Device, Inode: checkpoint.Inode, Metadata: checkpoint.Metadata}
	samples := CheckpointComparisonSamples{Prefix: checkpoint.Prefix, ParsedTail: checkpoint.ParsedTail}
	content[120] = '\t'
	if err := os.WriteFile(path, content, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
		t.Fatal(err)
	}
	const byteBudget int64 = 1024
	verification, err := unchangedCheckpointSource(source, metadata, samples, byteBudget)
	if err != nil || verification.Unchanged || !verification.SourceChanged {
		t.Fatalf("rewrite check: verification=%+v err=%v", verification, err)
	}
	remainingBytes := byteBudget - verification.BytesRead
	result, err := ScanTranscript(source, &state, TranscriptScanOptions{MaxBytes: remainingBytes, MaxRecords: 10000, ForceReset: verification.SourceChanged})
	if err != nil {
		t.Fatal(err)
	}
	if !result.Reset {
		t.Fatal("fallback scanner lost the prefix rewrite detected by fast verification")
	}
	if verification.BytesRead+result.BytesRead > byteBudget {
		t.Fatalf("verification plus fallback read %d bytes, budget %d", verification.BytesRead+result.BytesRead, byteBudget)
	}
}

func loadCollectorOpenCodeState(c *AgentTokenCollector) (openCodeCursorState, error) {
	var state openCodeCursorState
	_, err := c.transcriptCursor.load("opencode/"+c.config.OpenCodeDatabase, &state)
	return state, err
}

func TestDatabaseTranscriptCheckpointFragmentsRestartAndStaleCommit(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	writer, err := OpenWriter(ctx, filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	path := filepath.Join(root, "source.jsonl")
	writeJSONL(t, path, usageClaude(10, "one", 4, 3))
	config := AgentTokenCollectorConfig{CursorPath: filepath.Join(root, "old.json"), TranscriptCursorPath: filepath.Join(root, "old-transcripts.json"), TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
		return []TranscriptSource{{Path: path, Provider: "claude", SourceID: "fixture", ThreadID: "one"}}, nil
	}}
	first := NewAgentTokenCollector(writer, nil, config)
	collect := func(c *AgentTokenCollector) (AppendBatch, []agentTokenReceipt) {
		t.Helper()
		batch, receipts, err := c.collectTranscripts(ctx, 20, 5, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatal(err)
		}
		return batch, receipts
	}
	batch, pending := collect(first)
	second := NewAgentTokenCollector(writer, nil, config)
	staleBatch, stale := collect(second)
	if ok, err := first.appendCollected(ctx, batch, pending); !ok || err != nil {
		t.Fatalf("first commit: %v %v", ok, err)
	}
	if ok, err := second.appendCollected(ctx, staleBatch, stale); ok || !errors.Is(err, ErrCheckpointConflict) {
		t.Fatalf("stale commit: %v %v", ok, err)
	}
	source := TranscriptSource{Path: path, Provider: "claude"}
	state, err := first.transcriptCursor.State(source)
	if err != nil {
		t.Fatal(err)
	}
	cp, found, err := writer.LoadCheckpoint(ctx, transcriptCursorKey(source))
	if err != nil || !found {
		t.Fatalf("load: %v %v", found, err)
	}
	var serialized TranscriptScannerState
	if err := json.Unmarshal(cp.State, &serialized); err != nil {
		t.Fatal(err)
	}
	if len(serialized.Prefix) != 0 || len(serialized.ParsedTail) != 0 || !reflect.DeepEqual(cp.Prefix, state.Prefix) || !reflect.DeepEqual(cp.ParsedTail, state.ParsedTail) {
		t.Fatal("comparison bytes were not stored separately and exactly")
	}
	samples, err := writer.LoadCheckpointComparisonSamples(ctx, cp.Key)
	if err != nil || !reflect.DeepEqual(samples.Prefix, state.Prefix) || !reflect.DeepEqual(samples.ParsedTail, state.ParsedTail) {
		t.Fatalf("load exact comparison samples: %+v %v", samples, err)
	}
	restarted := NewAgentTokenCollector(writer, nil, config)
	reloaded, err := restarted.transcriptCursor.State(source)
	if err != nil || !reflect.DeepEqual(state, reloaded) {
		t.Fatalf("restart mismatch: %v", err)
	}
	for _, legacy := range []string{config.CursorPath, config.TranscriptCursorPath} {
		if _, err := os.Stat(legacy); !os.IsNotExist(err) {
			t.Fatalf("collection wrote legacy cursor %s: %v", legacy, err)
		}
	}
}

func TestDatabaseCheckpointMigrationIgnoresPendingAndPreservesLegacyFiles(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	cursor := filepath.Join(root, "old.json")
	source := TranscriptSource{Path: filepath.Join(root, "source.jsonl"), Provider: "claude"}
	committed := TranscriptScannerState{Path: source.Path, Offset: 17, Prefix: []byte("exact-prefix"), ParsedTail: []byte("exact-tail")}
	pending := committed
	pending.Offset = 99
	disk := transcriptCursorDisk{Version: 2, States: map[string]TranscriptScannerState{transcriptCursorKey(source): committed}, Pending: map[string]TranscriptScannerState{transcriptCursorKey(source): pending}}
	original, err := json.Marshal(disk)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cursor, original, 0600); err != nil {
		t.Fatal(err)
	}
	writer, err := OpenWriter(ctx, filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, nil, AgentTokenCollectorConfig{CursorPath: filepath.Join(root, "old-open.json"), TranscriptCursorPath: cursor})
	loaded, err := collector.transcriptCursor.State(source)
	if err != nil || loaded.Offset != 17 || string(loaded.Prefix) != "exact-prefix" {
		t.Fatalf("committed recovery: %+v %v", loaded, err)
	}
	after, err := os.ReadFile(cursor)
	if err != nil || string(after) != string(original) {
		t.Fatalf("migration modified legacy journal: %v", err)
	}
}

func TestDatabaseCheckpointAdmissionRotatesOversizedSources(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	writer, err := OpenWriter(ctx, filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	var sources []TranscriptSource
	for _, name := range []string{"a", "b", "c"} {
		path := filepath.Join(root, name+".jsonl")
		writeJSONL(t, path, usageClaude(10, name, 1, 1))
		sources = append(sources, TranscriptSource{Path: path, Provider: "claude", SourceID: name, ThreadID: name})
	}
	config := AgentTokenCollectorConfig{CursorPath: filepath.Join(root, "old.json"), TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) { return sources, nil }}
	first := NewAgentTokenCollector(writer, nil, config)
	if _, err := first.transcriptCursor.Schedule(); err != nil {
		t.Fatal(err)
	}
	for _, source := range sources {
		cp, err := encodeTranscriptCheckpoint(transcriptCursorKey(source), TranscriptScannerState{}, 0)
		if err != nil {
			t.Fatal(err)
		}
		cp.DecodedBytes = 40 << 20
		if _, err := writer.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{cp}); err != nil {
			t.Fatal(err)
		}
	}
	seen := map[string]bool{}
	for index := 0; index < 3; index++ {
		collector := NewAgentTokenCollector(writer, nil, config)
		batch, receipts, err := collector.collectTranscripts(ctx, 20+float64(index)*5, 5, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatal(err)
		}
		admitted := 0
		for _, receipt := range receipts {
			for _, cp := range receipt.checkpoints {
				if cp.Kind == "transcript" {
					admitted++
					seen[cp.Key] = true
				}
			}
		}
		if admitted != 1 {
			t.Fatalf("oversized checkpoint admitted with siblings: %d", admitted)
		}
		if collector.Counters().CheckpointOversized != 1 {
			t.Fatal("oversized exception missing from diagnostics")
		}
		if ok, err := collector.appendCollected(ctx, batch, receipts); !ok || err != nil {
			t.Fatalf("append: %v %v", ok, err)
		}
	}
	if len(seen) != 3 {
		t.Fatalf("historical checkpoint admission starved sources: %v", seen)
	}
}

func TestDatabaseTranscriptCheckpointRetiresRemovedSources(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	writer, err := OpenWriter(ctx, filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if err := writer.EnsureCheckpoints(ctx, nil); err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 40; index++ {
		key := fmt.Sprintf("claude:/retired/%03d.jsonl", index)
		checkpoint := Checkpoint{Key: key, Kind: "transcript", SourceID: key, Version: 1, State: []byte(`{"retired":true}`), Prefix: make([]byte, 1024*1024)}
		if _, err := writer.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{checkpoint}); err != nil {
			t.Fatal(err)
		}
	}
	path := filepath.Join(root, "active.jsonl")
	writeJSONL(t, path, usageClaude(10, "active", 4, 3))
	source := TranscriptSource{Path: path, Provider: "claude", SourceID: "active", ThreadID: "active"}
	collector := NewAgentTokenCollector(writer, nil, AgentTokenCollectorConfig{
		CursorPath:           filepath.Join(root, "old-open.json"),
		TranscriptCursorPath: filepath.Join(root, "old-transcripts.json"),
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{source}, nil
		},
	})
	batch, receipts, err := collector.collectTranscripts(ctx, 20, 5, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if ok, err := collector.appendCollected(ctx, batch, receipts); !ok || err != nil {
		t.Fatalf("append: %v %v", ok, err)
	}
	rows, err := writer.ListCheckpointMetadata(ctx, "transcript")
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].Key != transcriptCursorKey(source) {
		t.Fatalf("retained %d transcript checkpoints after complete source discovery; want only %q", len(rows), transcriptCursorKey(source))
	}
}

func TestDatabaseTranscriptCheckpointKeepsRetiredRowsOnIncompleteDiscovery(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	writer, err := OpenWriter(ctx, filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if err := writer.EnsureCheckpoints(ctx, nil); err != nil {
		t.Fatal(err)
	}
	retired := Checkpoint{Key: "claude\x00/retired.jsonl", Kind: "transcript", SourceID: "/retired.jsonl", Version: 1, State: []byte(`{"retired":true}`)}
	if _, err := writer.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{retired}); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "active.jsonl")
	writeJSONL(t, path, usageClaude(10, "active", 4, 3))
	source := TranscriptSource{Path: path, Provider: "claude", SourceID: "active", ThreadID: "active"}
	collector := NewAgentTokenCollector(writer, nil, AgentTokenCollectorConfig{
		CursorPath: filepath.Join(root, "old-open.json"),
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return []TranscriptSource{source}, errors.New("discovery was partial")
		},
	})
	batch, receipts, err := collector.collectTranscripts(ctx, 20, 5, nil, nil, AppendBatch{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if ok, err := collector.appendCollected(ctx, batch, receipts); !ok || err != nil {
		t.Fatalf("append: %v %v", ok, err)
	}
	rows, err := writer.ListCheckpointMetadata(ctx, "transcript")
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("incomplete discovery retired transcript checkpoints: retained %d, want 2", len(rows))
	}
}

func TestDatabaseTranscriptCheckpointResumesPartialLineAfterRestart(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	writer, err := OpenWriter(ctx, filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	path := filepath.Join(root, "source.jsonl")
	record := usageClaude(10, "one", 4, 3)
	raw, err := json.Marshal(record)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	source := TranscriptSource{Path: path, Provider: "claude", SourceID: "fixture", ThreadID: "one"}
	config := AgentTokenCollectorConfig{CursorPath: filepath.Join(root, "old.json"), TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
		return []TranscriptSource{source}, nil
	}}
	run := func() AppendBatch {
		t.Helper()
		collector := NewAgentTokenCollector(writer, nil, config)
		batch, receipts, err := collector.collectTranscripts(ctx, 20, 5, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatal(err)
		}
		if ok, err := collector.appendCollected(ctx, batch, receipts); !ok || err != nil {
			t.Fatalf("append: %v %v", ok, err)
		}
		return batch
	}
	if batch := run(); len(batch.UsageAtoms) != 0 {
		t.Fatal("unterminated line emitted usage")
	}
	cp, found, err := writer.LoadCheckpoint(ctx, transcriptCursorKey(source))
	if err != nil || !found || string(cp.PendingLine) != string(raw) || cp.Offset != 0 {
		t.Fatalf("partial checkpoint: found=%v offset=%d pending=%d err=%v", found, cp.Offset, len(cp.PendingLine), err)
	}
	if err := os.WriteFile(path, append(raw, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	if batch := run(); len(batch.UsageAtoms) != 2 {
		t.Fatalf("restarted partial line usage=%d", len(batch.UsageAtoms))
	}
	if batch := run(); len(batch.UsageAtoms) != 0 {
		t.Fatal("unchanged restarted checkpoint re-emitted usage")
	}
}
