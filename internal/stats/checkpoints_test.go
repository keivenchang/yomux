package stats

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func checkpointTestWriter(t *testing.T) (*Writer, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "stats-v9.sqlite3")
	w, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = w.Close() })
	if err := w.EnsureCheckpoints(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	return w, path
}

func TestCheckpointAtomicFactsAndRestart(t *testing.T) {
	ctx := context.Background()
	w, path := checkpointTestWriter(t)
	checkpoint := Checkpoint{Key: "claude:/fixture", Kind: "transcript", SourceID: "fixture", Version: 1, Offset: 42, FileSize: 51, Device: ^uint64(0), Inode: ^uint64(0), State: []byte(`{"line":2}`), Prefix: []byte{0, 255, 10}, PendingLine: []byte("partial"), Metadata: []byte(`{"eof":false}`)}
	result, err := w.AppendWithCheckpoints(ctx, writerBatch(), []Checkpoint{checkpoint})
	if err != nil {
		t.Fatal(err)
	}
	if result.Generation != 1 {
		t.Fatalf("generation=%d", result.Generation)
	}
	loaded, ok, err := w.LoadCheckpoint(ctx, checkpoint.Key)
	if err != nil || !ok {
		t.Fatalf("load=%+v %v %v", loaded, ok, err)
	}
	if loaded.Revision != 1 || loaded.Offset != 42 || !bytes.Equal(loaded.Prefix, checkpoint.Prefix) || loaded.Device != checkpoint.Device {
		t.Fatalf("checkpoint=%+v", loaded)
	}
	metadata, err := w.ListCheckpointMetadata(ctx, "transcript")
	if err != nil || len(metadata) != 1 || metadata[0].DecodedBytes < 14 {
		t.Fatalf("metadata=%+v %v", metadata, err)
	}
	loaded.Offset = 51
	result, err = w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{loaded})
	if err != nil {
		t.Fatal(err)
	}
	if result.Generation != 1 {
		t.Fatalf("checkpoint-only generation=%d", result.Generation)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	loaded, ok, err = reopened.LoadCheckpoint(ctx, checkpoint.Key)
	if err != nil || !ok || loaded.Revision != 2 || loaded.Offset != 51 {
		t.Fatalf("restarted checkpoint=%+v %v %v", loaded, ok, err)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	facts, err := reader.ReadUsageAtoms(ctx, 0, 100, 10)
	if err != nil || len(facts) != 1 {
		t.Fatalf("facts=%+v %v", facts, err)
	}
}

func TestCheckpointFailedFactAndStaleRevisionLeaveProgress(t *testing.T) {
	ctx := context.Background()
	w, _ := checkpointTestWriter(t)
	c := Checkpoint{Key: "a", Kind: "transcript", Version: 1}
	if _, err := w.AppendWithCheckpoints(ctx, writerBatch(), []Checkpoint{c}); err != nil {
		t.Fatal(err)
	}
	for _, expected := range []int64{0, 3} {
		c.ExpectedRevision = expected
		if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{c}); !errors.Is(err, ErrCheckpointConflict) {
			t.Fatalf("stale=%v", err)
		}
	}
	missing := Checkpoint{Key: "missing", Kind: "transcript", Version: 1, ExpectedRevision: 4}
	if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{missing}); !errors.Is(err, ErrCheckpointConflict) {
		t.Fatalf("missing stale=%v", err)
	}
	c.ExpectedRevision = 1
	c.Offset = 99
	bad := writerBatch()
	bad.UsageAtoms[0].Payload["quantity"] = 999.0
	if _, err := w.AppendWithCheckpoints(ctx, bad, []Checkpoint{c}); err == nil {
		t.Fatal("conflicting usage accepted")
	}
	loaded, ok, err := w.LoadCheckpoint(ctx, "a")
	if err != nil || !ok || loaded.Offset != 0 || loaded.Revision != 1 {
		t.Fatalf("rollback=%+v %v", loaded, err)
	}
	canceled, cancel := context.WithCancel(ctx)
	cancel()
	if _, err := w.AppendWithCheckpoints(canceled, AppendBatch{}, []Checkpoint{c}); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancel=%v", err)
	}
	var generation int64
	if err := w.db.QueryRow("SELECT source_generation FROM schema_meta").Scan(&generation); err != nil || generation != 1 {
		t.Fatalf("generation=%d %v", generation, err)
	}
}

func TestCheckpointMetadataForKeysDoesNotLoadPartialLineBlobs(t *testing.T) {
	ctx := context.Background()
	w, _ := checkpointTestWriter(t)
	var keys []string
	for index := 0; index < 10; index++ {
		source := TranscriptSource{Provider: "claude", Path: fmt.Sprintf("/fixture/partial-%d.jsonl", index)}
		key := transcriptCursorKey(source)
		keys = append(keys, key)
		checkpoint, err := encodeTranscriptCheckpoint(key, TranscriptScannerState{Path: source.Path, FileSize: 8 << 20, PendingLine: make([]byte, 8<<20)}, 0)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{checkpoint}); err != nil {
			t.Fatal(err)
		}
	}
	rows, err := w.ListCheckpointMetadataForKeys(ctx, "transcript", keys)
	if err != nil || len(rows) != len(keys) {
		t.Fatalf("metadata rows=%d err=%v", len(rows), err)
	}
	for _, row := range rows {
		source := TranscriptSource{Provider: "claude", Path: row.SourceID}
		if checkpointCanUseUnchangedFastPath(source, row) {
			t.Fatalf("partial checkpoint %q passed EOF fast-path classification", row.Key)
		}
	}
}

func TestCheckpointRetirementPreservesExistingSourceFiles(t *testing.T) {
	ctx := context.Background()
	w, databasePath := checkpointTestWriter(t)
	defer w.Close()
	sourcePath := filepath.Join(filepath.Dir(databasePath), "deferred.jsonl")
	if err := os.WriteFile(sourcePath, []byte("active"), 0600); err != nil {
		t.Fatal(err)
	}
	checkpoint := Checkpoint{Key: "claude\x00" + sourcePath, Kind: "transcript", SourceID: sourcePath, Version: 1, State: []byte(`{"offset":1}`)}
	if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{checkpoint}); err != nil {
		t.Fatal(err)
	}
	if _, err := w.AppendWithCheckpointsAndRetire(ctx, AppendBatch{}, nil, &CheckpointRetirement{Kind: "transcript", PreserveExistingSourceFiles: true}); err != nil {
		t.Fatal(err)
	}
	if _, found, err := w.LoadCheckpoint(ctx, checkpoint.Key); err != nil || !found {
		t.Fatalf("existing deferred source checkpoint found=%v err=%v", found, err)
	}
}

func TestCheckpointRetirementKeepsStaleRevisionFence(t *testing.T) {
	ctx := context.Background()
	w, _ := checkpointTestWriter(t)
	defer w.Close()
	original := Checkpoint{Key: "claude\x00/retired.jsonl", Kind: "transcript", SourceID: "/retired.jsonl", Version: 1, State: []byte(`{"owner":"original"}`)}
	if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{original}); err != nil {
		t.Fatal(err)
	}
	stale := original
	stale.ExpectedRevision = 1
	if _, err := w.AppendWithCheckpointsAndRetire(ctx, AppendBatch{}, nil, &CheckpointRetirement{Kind: "transcript"}); err != nil {
		t.Fatal(err)
	}
	retired, found, err := w.LoadCheckpoint(ctx, original.Key)
	if err != nil || !found || retired.Kind != "retired" || retired.Revision != 2 {
		t.Fatalf("retired checkpoint=%+v found=%v err=%v", retired, found, err)
	}
	if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{stale}); !errors.Is(err, ErrCheckpointConflict) {
		t.Fatalf("stale receipt after retirement error=%v, want revision conflict", err)
	}
	replacement := Checkpoint{Key: original.Key, Kind: "transcript", Version: 1, ExpectedRevision: retired.Revision, State: []byte(`{"owner":"replacement"}`)}
	if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{replacement}); err != nil {
		t.Fatalf("recreate retired checkpoint: %v", err)
	}
	if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{stale}); !errors.Is(err, ErrCheckpointConflict) {
		t.Fatalf("stale receipt after recreation error=%v, want revision conflict", err)
	}
}

func TestCheckpointConcurrentRevisionHasSingleWinner(t *testing.T) {
	ctx := context.Background()
	w, _ := checkpointTestWriter(t)
	var wg sync.WaitGroup
	results := make(chan error, 12)
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{{Key: "shared", Kind: "transcript", Version: 1}})
			results <- err
		}()
	}
	wg.Wait()
	close(results)
	wins := 0
	for err := range results {
		if err == nil {
			wins++
		} else if !errors.Is(err, ErrCheckpointConflict) {
			t.Fatal(err)
		}
	}
	if wins != 1 {
		t.Fatalf("winners=%d", wins)
	}
}

func TestCheckpointMigrationPairsBackupAndFencesOldWriter(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	path := filepath.Join(root, "stats.sqlite3")
	cursor := filepath.Join(root, "cursors.json")
	oldCursor := []byte(`{"state":{"committed":1},"pending":{"uncommitted":2}}`)
	if err := os.WriteFile(cursor, oldCursor, 0600); err != nil {
		t.Fatal(err)
	}
	w, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	if _, err := w.AppendBatch(ctx, writerBatch()); err != nil {
		t.Fatal(err)
	}
	if err := w.EnsureCheckpoints(ctx, []string{cursor, cursor}); err != nil {
		t.Fatal(err)
	}
	var backup string
	if err := w.db.QueryRow("SELECT source_digest FROM migration_reconciliation WHERE migration_id='go-checkpoints-v1'").Scan(&backup); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite3", writerReadOnlyURI(filepath.Join(backup, filepath.Base(path))))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var version, count int
	if err := db.QueryRow("PRAGMA user_version").Scan(&version); err != nil || version != 9 {
		t.Fatalf("backup schema=%d %v", version, err)
	}
	if err := db.QueryRow("SELECT count(*) FROM usage_atoms").Scan(&count); err != nil || count != 1 {
		t.Fatalf("backup usage=%d %v", count, err)
	}
	data, err := os.ReadFile(filepath.Join(backup, "cursor-manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest map[string]string
	if err := json.Unmarshal(data, &manifest); err != nil {
		t.Fatal(err)
	}
	saved, err := os.ReadFile(filepath.Join(backup, manifest[cursor]))
	if err != nil || !bytes.Equal(saved, oldCursor) {
		t.Fatalf("backup cursor=%s %v", saved, err)
	}
	fenceData, err := os.ReadFile(filepath.Join(root, writerFenceFilename))
	if err != nil {
		t.Fatal(err)
	}
	var fence writerFence
	if err := json.Unmarshal(fenceData, &fence); err != nil {
		t.Fatal(err)
	}
	if fence.SchemaVersion != 10 || fence.MinimumWriterProtocol <= writerProtocol {
		t.Fatalf("fence=%+v", fence)
	}
	if err := w.EnsureCheckpoints(ctx, []string{cursor}); err != nil {
		t.Fatal(err)
	}
	backups, err := filepath.Glob(path + ".pre-checkpoints-*")
	if err != nil || len(backups) != 1 {
		t.Fatalf("backups=%v %v", backups, err)
	}
}

func TestCheckpointMigrationPythonRejectionAndPairedRollback(t *testing.T) {
	ctx := context.Background()
	w, path := checkpointTestWriter(t)
	if _, err := w.AppendWithCheckpoints(ctx, writerBatch(), []Checkpoint{{Key: "new", Kind: "transcript", Version: 1}}); err != nil {
		t.Fatal(err)
	}
	var backup string
	if err := w.db.QueryRow("SELECT source_digest FROM migration_reconciliation WHERE migration_id='go-checkpoints-v1'").Scan(&backup); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(t.TempDir(), "migration.py")
	if err := os.WriteFile(script, []byte(`import sys
from yomux_lib.stats_current.storage import Store, SchemaTooNewError
try:
    store = Store.open(sys.argv[1], include_browser_diagnostics=False)
except SchemaTooNewError:
    assert sys.argv[2] == "reject"
    print("rejected")
else:
    assert sys.argv[2] == "accept"
    store.close()
    print("accepted")
`), 0600); err != nil {
		t.Fatal(err)
	}
	python := pythonExecutable(t)
	runPython(t, python, script, path, "reject")
	recovery := filepath.Join(t.TempDir(), "migrated")
	if err := os.Mkdir(recovery, 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{path, path + "-wal", path + "-shm", filepath.Join(filepath.Dir(path), writerFenceFilename)} {
		if _, err := os.Stat(name); os.IsNotExist(err) {
			continue
		} else if err != nil {
			t.Fatal(err)
		}
		if err := os.Rename(name, filepath.Join(recovery, filepath.Base(name))); err != nil {
			t.Fatal(err)
		}
	}
	saved, err := os.ReadFile(filepath.Join(backup, filepath.Base(path)))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, saved, 0600); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(backup, "cursor-manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest map[string]string
	if err := json.Unmarshal(data, &manifest); err != nil {
		t.Fatal(err)
	}
	for original, name := range manifest {
		data, err := os.ReadFile(filepath.Join(backup, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(original, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	runPython(t, python, script, path, "accept")
	preserved, err := OpenReadOnly(ctx, filepath.Join(recovery, filepath.Base(path)))
	if err != nil {
		t.Fatal(err)
	}
	defer preserved.Close()
	facts, err := preserved.ReadUsageAtoms(ctx, 0, 100, 10)
	if err != nil || len(facts) != 1 {
		t.Fatalf("preserved migrated facts=%v %v", facts, err)
	}
}

func TestCheckpointProgressDoesNotRepeatStartupFactReconciliation(t *testing.T) {
	for _, migrateBeforeRestart := range []bool{false, true} {
		for _, rejected := range []bool{false, true} {
			t.Run(fmt.Sprintf("migrated=%t/rejected=%t", migrateBeforeRestart, rejected), func(t *testing.T) {
				ctx := context.Background()
				path := filepath.Join(t.TempDir(), "stats.sqlite3")
				w, err := OpenWriter(ctx, path)
				if err != nil {
					t.Fatal(err)
				}
				if migrateBeforeRestart {
					if err := w.EnsureCheckpoints(ctx, nil); err != nil {
						t.Fatal(err)
					}
				}
				stamp := float64(time.Now().Unix())
				start := int64(stamp) / 10 * 10
				payload, err := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: 10, Complete: true}, nil, 1, stamp)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := w.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: start, BucketJSON: payload, Complete: true}}, 0, stamp, true); err != nil {
					t.Fatal(err)
				}
				fact := Observation{EventID: "recent", Family: "cpu", SourceID: "host", ObservedAt: stamp, EpochID: "epoch", Payload: map[string]any{"value": 1}}
				if _, err := w.AppendBatch(ctx, AppendBatch{Observations: []Observation{fact}}); err != nil {
					t.Fatal(err)
				}
				if err := w.Close(); err != nil {
					t.Fatal(err)
				}
				w, err = OpenWriter(ctx, path)
				if err != nil {
					t.Fatal(err)
				}
				defer w.Close()
				if !w.startupReconciledDataReady {
					t.Fatal("fixture did not establish startup reconciliation")
				}
				if !migrateBeforeRestart {
					if err := w.EnsureCheckpoints(ctx, nil); err != nil {
						t.Fatal(err)
					}
				}
				if _, err := w.db.ExecContext(ctx, `CREATE TRIGGER unexpected_reconciliation BEFORE INSERT ON ring_invalidations WHEN NEW.reason='external_generation_reconciliation' BEGIN SELECT RAISE(ABORT,'checkpoint-only append reran all-fact reconciliation'); END`); err != nil {
					t.Fatal(err)
				}
				checkpoint := Checkpoint{Key: "fixture", Kind: "transcript", Version: 1}
				if rejected {
					fact.Payload = map[string]any{"value": 2}
					if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{Observations: []Observation{fact}}, []Checkpoint{checkpoint}); err == nil {
						t.Fatal("conflicting fact accepted")
					}
				}
				if _, err := w.AppendWithCheckpoints(ctx, AppendBatch{}, []Checkpoint{checkpoint}); err != nil {
					t.Fatal(err)
				}
			})
		}
	}
}
