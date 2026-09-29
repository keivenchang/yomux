package stats

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yolomux/internal/testsupport"
	_ "github.com/mattn/go-sqlite3"
)

// TestPythonInterop exercises the durable boundary with Python's current-only
// store. The helper is deliberately temporary so this test never touches user
// state, transcripts, or the configured OpenCode database.
func TestPythonInterop(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	statsPath := filepath.Join(root, "yostats.sqlite3")
	opencodePythonPath := filepath.Join(root, "opencode-python.db")
	opencodeGoPath := filepath.Join(root, "opencode-go.db")
	script := filepath.Join(root, "interop.py")
	writePythonInteropScript(t, script)

	runPython(t, python, script, "make-stats", statsPath)
	ctx := context.Background()
	database, err := OpenReadOnly(ctx, statsPath)
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 100, End: 200, Resolution: 10, FactLimit: 100})
	if err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if snapshot.Generated.SchemaVersion != 9 || snapshot.SourceGeneration != 1 {
		t.Fatalf("Python-created stats header = %+v", snapshot)
	}
	if len(snapshot.Observations.Buckets) == 0 || snapshot.Unavailable.State != SnapshotUnavailable {
		t.Fatalf("Python-created stats snapshot = %+v", snapshot)
	}
	if len(snapshot.Observations.Buckets[0].Observations) != 1 || snapshot.Observations.Buckets[0].Observations[0].Family != "host_facts" || snapshot.Observations.Buckets[0].Observations[0].Payload["hostname"] != "test-host" {
		t.Fatalf("Python-created host facts projection = %+v", snapshot.Observations)
	}
	if len(snapshot.Observations.Buckets[1].Observations) != 1 || snapshot.Observations.Buckets[1].Observations[0].Family != "agent_status" || snapshot.Observations.Buckets[1].Observations[0].Payload["host"] != "test-host" {
		t.Fatalf("Python-created agent status projection = %+v", snapshot.Observations)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}

	writer, err := OpenWriter(ctx, filepath.Join(root, "go-written.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	ended := 150.0
	hostEnded := 115.0
	batch := AppendBatch{
		Observations: []Observation{
			{EventID: "agent-status-1", Family: "agent_status", SourceID: "host-test", ObservedAt: 110, EpochID: "agent-epoch", OwnerGeneration: 1, Payload: map[string]any{"states": map[string]any{"ses-1": "working"}, "host": "test-host"}},
			{EventID: "host-facts-1", Family: "host_facts", SourceID: "host-test", ObservedAt: 115, EpochID: "host-epoch", OwnerGeneration: 1, Payload: map[string]any{"hostname": "test-host", "memory_bytes": 4096}},
		},
		CoverageEpochs:   []CoverageEpoch{{Family: "agent_status", SourceID: "host-test", EpochID: "agent-epoch", StartedAt: 100, EndedAt: &ended, NativeCadenceSeconds: 10, OwnerGeneration: 1}, {Family: "host_facts", SourceID: "host-test", EpochID: "host-epoch", StartedAt: 100, EndedAt: &hostEnded, NativeCadenceSeconds: 10, OwnerGeneration: 1}},
		UnavailableSpans: []UnavailableSpan{{Family: "host_facts", SourceID: "host-test", EpochID: "host-epoch", StartedAt: 120, EndedAt: 130, NativeCadenceSeconds: 10, Reason: "fixture unavailable", OwnerGeneration: 1}},
	}
	if _, err := writer.AppendBatch(ctx, batch); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	goWrittenPath := filepath.Join(root, "go-written.sqlite3")
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	result := runPython(t, python, script, "check-stats", goWrittenPath)
	if !strings.Contains(result, `"schema": 9`) || !strings.Contains(result, `"source_generation": 1`) || !strings.Contains(result, `"observations": 2`) || !strings.Contains(result, `"coverage": 2`) || !strings.Contains(result, `"payloads"`) {
		t.Fatalf("Python readback = %s", result)
	}

	runPython(t, python, script, "make-opencode", opencodePythonPath)
	checkOpenCodeWithGo(t, opencodePythonPath)
	createOpenCodeFixtureWithGo(t, opencodeGoPath)
	result = runPython(t, python, script, "check-opencode", opencodeGoPath)
	if !strings.Contains(result, `"provider": "openai"`) || !strings.Contains(result, `"input": 3`) || !strings.Contains(result, `"state": "paused"`) {
		t.Fatalf("Python OpenCode readback = %s", result)
	}
}

func TestPythonGoCutoverRollbackPreservesFactsAndGeneration(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	statsPath := filepath.Join(root, "stats-v9.sqlite3")
	script := filepath.Join(root, "cutover.py")
	const scriptBody = `import json, sqlite3, sys
from yolomux_lib.stats_current.storage import Observation, Store, UsageAtom

path, operation = sys.argv[1:3]
if operation == "python-write-first":
    store = Store.open(path, include_browser_diagnostics=False)
    result = store.append_batch(
        observations=(Observation("python-before", "cpu", "host", 100, "cpu", 1, {"process_percent": 3}),),
        usage_atoms=(UsageAtom("python-usage-before", "input", "text", "none", "tokens", 101, {"quantity": 4, "provider": "openai", "model": "fixture", "agent_id": "agent-python"}),),
    )
    store.close()
    print(json.dumps({"generation": result.source_generation, "observations": result.observations_accepted, "usage": result.usage_atoms_accepted}, separators=(",", ":")))
elif operation == "python-write-rollback":
    store = Store.open(path, include_browser_diagnostics=False)
    result = store.append_batch(
        observations=(Observation("python-after", "memory", "host", 103, "memory", 1, {"used_bytes": 8, "capacity_bytes": 16}),),
        usage_atoms=(UsageAtom("python-usage-after", "output", "text", "none", "tokens", 104, {"quantity": 2, "provider": "openai", "model": "fixture", "agent_id": "agent-python"}),),
    )
    store.close()
    print(json.dumps({"generation": result.source_generation, "observations": result.observations_accepted, "usage": result.usage_atoms_accepted}, separators=(",", ":")))
elif operation == "python-read":
    connection = sqlite3.connect(path)
    generation = connection.execute("SELECT source_generation FROM schema_meta WHERE singleton=1").fetchone()[0]
    observations = [row[0] for row in connection.execute("SELECT event_id FROM observations ORDER BY event_id")]
    usage = [row[0] for row in connection.execute("SELECT event_id FROM usage_atoms ORDER BY event_id")]
    connection.close()
    print(json.dumps({"generation": generation, "observations": observations, "usage": usage}, separators=(",", ":")))
else:
    raise SystemExit(f"unknown operation: {operation}")
`
	if err := os.WriteFile(script, []byte(scriptBody), 0o600); err != nil {
		t.Fatal(err)
	}
	first := runPython(t, python, script, statsPath, "python-write-first")
	if !strings.Contains(first, `"generation":1`) || !strings.Contains(first, `"observations":1`) || !strings.Contains(first, `"usage":1`) {
		t.Fatalf("Python first cutover append = %s", first)
	}

	goWriter, err := OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatalf("Go could not take ownership after Python: %v", err)
	}
	goResult, err := goWriter.AppendBatch(context.Background(), AppendBatch{
		Observations: []Observation{{EventID: "go-during-cutover", Family: "cpu", SourceID: "host", ObservedAt: 102, EpochID: "cpu", OwnerGeneration: 2, Payload: map[string]any{"process_percent": 7}}},
		UsageAtoms:   []UsageAtom{{EventID: "go-usage-during-cutover", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 102.5, Payload: map[string]any{"quantity": 5.0, "provider": "openai", "model": "fixture", "agent_id": "agent-go"}}},
	})
	if err != nil {
		_ = goWriter.Close()
		t.Fatalf("Go cutover append failed: %v", err)
	}
	if goResult.Generation != 2 || goResult.ObservationsAccepted != 1 || goResult.UsageAccepted != 1 {
		_ = goWriter.Close()
		t.Fatalf("Go cutover append = %+v", goResult)
	}
	if err := goWriter.Close(); err != nil {
		t.Fatal(err)
	}

	pythonRead := runPython(t, python, script, statsPath, "python-read")
	if !strings.Contains(pythonRead, `"generation":2`) || !strings.Contains(pythonRead, "python-before") || !strings.Contains(pythonRead, "go-during-cutover") || !strings.Contains(pythonRead, "python-usage-before") || !strings.Contains(pythonRead, "go-usage-during-cutover") {
		t.Fatalf("Python readback after Go ownership = %s", pythonRead)
	}
	rollback := runPython(t, python, script, statsPath, "python-write-rollback")
	if !strings.Contains(rollback, `"generation":3`) || !strings.Contains(rollback, `"observations":1`) || !strings.Contains(rollback, `"usage":1`) {
		t.Fatalf("Python rollback append = %s", rollback)
	}

	goWriter, err = OpenWriter(context.Background(), statsPath)
	if err != nil {
		t.Fatalf("Go could not reclaim ownership after Python rollback: %v", err)
	}
	if err := goWriter.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenReadOnly(context.Background(), statsPath)
	if err != nil {
		t.Fatalf("open Go readback after rollback: %v", err)
	}
	observations, err := reader.ReadObservations(context.Background(), 0, 200, MaxFactReadLimit)
	if err != nil {
		_ = reader.Close()
		t.Fatal(err)
	}
	usage, err := reader.ReadUsageAtoms(context.Background(), 0, 200, MaxFactReadLimit)
	if err != nil {
		_ = reader.Close()
		t.Fatal(err)
	}
	if len(observations) != 3 || len(usage) != 3 {
		_ = reader.Close()
		t.Fatalf("Go readback after rollback: observations=%d usage=%d", len(observations), len(usage))
	}
	if reader.Header.SourceGeneration != 3 {
		_ = reader.Close()
		t.Fatalf("Go generation after rollback = %d, want 3", reader.Header.SourceGeneration)
	}
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestPythonAndGoSchema9TopologyIsBidirectionallyCompatible(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	script := filepath.Join(root, "schema-topology.py")
	writePythonSchemaTopologyScript(t, script)
	pythonPath := filepath.Join(root, "python", "stats-v9.sqlite3")
	goPath := filepath.Join(root, "go", "stats-v9.sqlite3")

	runPython(t, python, script, "create", pythonPath)
	wantRaw := runPython(t, python, script, "topology", pythonPath)
	var want []string
	if err := json.Unmarshal([]byte(wantRaw), &want); err != nil {
		t.Fatalf("decode Python schema topology: %v\n%s", err, wantRaw)
	}

	writer, err := OpenWriter(context.Background(), pythonPath)
	if err != nil {
		t.Fatalf("Go rejected Python-created schema-9 database: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	gotPythonPath := goSchemaTopology(t, pythonPath)
	if !sameSchemaTopology(gotPythonPath, want) {
		wantJSON, _ := json.Marshal(want)
		t.Fatalf("Go changed Python schema topology\nGo: %s\nPython: %s", gotPythonPath, wantJSON)
	}

	if err := os.MkdirAll(filepath.Dir(goPath), 0o700); err != nil {
		t.Fatal(err)
	}
	writer, err = OpenWriter(context.Background(), goPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	gotGoPath := goSchemaTopology(t, goPath)
	if !sameSchemaTopology(gotGoPath, want) {
		wantJSON, _ := json.Marshal(want)
		t.Fatalf("Go-created schema topology differs from Python\nGo: %s\nPython: %s", gotGoPath, wantJSON)
	}
	runPython(t, python, script, "open-reader", goPath)
}

func TestPythonAndGoCompressedRingPayloadsAreBidirectionallyCompatible(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	payload := `{"start":1000,"duration":10,"series":{"metric":"` + strings.Repeat("stable-value,", 2000) + `"},"source":{"count":1,"first_timestamp":1001,"last_timestamp":1001},"open":false}`
	goPath := filepath.Join(root, "go", "stats-v9.sqlite3")
	if err := os.MkdirAll(filepath.Dir(goPath), 0o700); err != nil {
		t.Fatal(err)
	}
	writer, err := OpenWriter(context.Background(), goPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(context.Background(), []RingBucketWrite{{
		ResolutionSeconds: 10,
		BucketStart:       1000,
		BucketJSON:        payload,
		Complete:          true,
	}}, 1, 110, true); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	var stored any
	if err := writer.db.QueryRowContext(context.Background(), `SELECT bucket_json FROM aggregate_ring_slots WHERE resolution_seconds=10 AND bucket_start=1000`).Scan(&stored); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	compressed, ok := stored.([]byte)
	if !ok || len(compressed) < len("YRB1\x00") || string(compressed[:len("YRB1\x00")]) != "YRB1\x00" {
		_ = writer.Close()
		t.Fatalf("Go ring payload storage = %T %v, want YRB1 compressed blob", stored, stored)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	goReadScript := filepath.Join(root, "read-ring.py")
	const readRing = `import json, sqlite3, sys
from yolomux_lib.stats_current.storage import Store
path = sys.argv[1]
connection = sqlite3.connect(path)
kind, value = connection.execute("SELECT typeof(bucket_json), bucket_json FROM aggregate_ring_slots WHERE resolution_seconds=10 AND bucket_start=1000").fetchone()
connection.close()
if kind != "blob" or not isinstance(value, bytes) or value[:5] != b"YRB1\0":
    raise SystemExit(f"unexpected ring storage: {kind!r} {type(value)!r} {value[:5] if isinstance(value, bytes) else value!r}")
store = Store.open_reader(path, include_browser_diagnostics=False)
window = store.read_ring_window(range_seconds=300, resolution_seconds=10, window_end=1300)
store.close()
if len(window.rows) != 1 or window.rows[0].bucket_json != sys.argv[2]:
    raise SystemExit("Python did not decode the Go ring payload")
print(json.dumps({"kind": kind, "magic": value[:5].decode("ascii", "strict"), "bucket": window.rows[0].bucket_json}, separators=(",", ":")))
`
	if err := os.WriteFile(goReadScript, []byte(readRing), 0o600); err != nil {
		t.Fatal(err)
	}
	goRead := runPython(t, python, goReadScript, goPath, payload)
	var readback map[string]string
	if err := json.Unmarshal([]byte(goRead), &readback); err != nil {
		t.Fatalf("decode Python compressed-ring readback: %v\n%s", err, goRead)
	}
	if readback["magic"] != "YRB1\x00" || readback["bucket"] != payload {
		t.Fatalf("Python compressed-ring readback = %#v", readback)
	}

	pythonPath := filepath.Join(root, "python", "stats-v9.sqlite3")
	if err := os.MkdirAll(filepath.Dir(pythonPath), 0o700); err != nil {
		t.Fatal(err)
	}
	pythonWriteScript := filepath.Join(root, "write-ring.py")
	const writeRing = `import sys
from yolomux_lib.stats_current.storage import RingBucketWrite, Store
path, payload = sys.argv[1], sys.argv[2]
store = Store.open(path, include_browser_diagnostics=False)
store.initialize_ring_storage()
store.publish_ring_buckets(buckets=(RingBucketWrite(10, 1000, payload, True),), source_generation=1, published_at=1120)
store.close()
`
	if err := os.WriteFile(pythonWriteScript, []byte(writeRing), 0o600); err != nil {
		t.Fatal(err)
	}
	runPython(t, python, pythonWriteScript, pythonPath, payload)
	reopened, err := OpenWriter(context.Background(), pythonPath)
	if err != nil {
		t.Fatal(err)
	}
	window, err := reopened.ReadRingWindow(context.Background(), 300, 10, 1300)
	if err != nil {
		_ = reopened.Close()
		t.Fatal(err)
	}
	if len(window.Rows) != 1 || window.Rows[0].BucketJSON != payload || window.Rows[0].SourceGeneration != 1 || window.Rows[0].RingGeneration != 1 {
		_ = reopened.Close()
		t.Fatalf("Go Python-ring readback = %+v", window)
	}
	if err := reopened.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestPythonAndGoInvalidationLedgersMatchForSparseMutations(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	pythonPath := filepath.Join(root, "python", "stats-v9.sqlite3")
	goPath := filepath.Join(root, "go", "stats-v9.sqlite3")
	script := filepath.Join(root, "invalidation-ledger.py")
	if err := os.MkdirAll(filepath.Dir(pythonPath), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(goPath), 0o700); err != nil {
		t.Fatal(err)
	}
	const scriptBody = `import json, sys
from yolomux_lib.stats_current.storage import CoverageEpoch, Observation, RingBucketWrite, Store, UnavailableSpan, UsageAtom, UsageAtomTombstone

path = sys.argv[1]
store = Store.open(path, include_browser_diagnostics=False)
seed = [(1, 1003), (1, 1012), (1, 1040), (1, 1070), (10, 1000), (10, 1020), (10, 1050), (10, 1060), (60, 960), (60, 1020), (300, 900)]
store.publish_ring_buckets(
    buckets=tuple(RingBucketWrite(resolution, bucket, '{"value":1}', True) for resolution, bucket in seed),
    source_generation=0,
    published_at=1,
)
store.append_batch(observations=(Observation('obs', 'cpu', 'host', 1003, 'cpu', 1, {'process_percent': 10}),))
atom = UsageAtom('codex:thread:atom', 'input', 'text', 'none', 'tokens', 1012, {'quantity': 4, 'provider': 'openai', 'model': 'm', 'thread_id': 'thread', 'execution_source': 'codex'})
store.append_batch(usage_atoms=(UsageAtom('codex:thread:atom', 'input', 'text', 'none', 'tokens', 1012, atom.payload),))
store.append_batch(usage_tombstones=(UsageAtomTombstone('codex:thread:atom', 'input', 'text', 'none', 'tokens', 1012, 4, 'openai', 'm', 'thread'),))
store.append_batch(coverage_epochs=(CoverageEpoch('cpu', 'host', 'epoch', 1000, 1060, 10, 1),))
store.append_batch(unavailable_spans=(UnavailableSpan('gpu', 'gpu:0', 'gap', 1060, 1080, 10, 'driver unavailable', 1),))
store.publish_ring_buckets(buckets=(RingBucketWrite(1, 1003, '{"value":2}', True),), source_generation=5, published_at=2)
rows = [(resolution, bucket, generation, reason, bool(applied)) for resolution, bucket, generation, reason, applied in store._connection().execute('SELECT resolution_seconds, bucket_start, source_generation, reason, applied_at IS NOT NULL FROM ring_invalidations ORDER BY resolution_seconds, bucket_start')]
print(json.dumps(rows, separators=(',', ':')))
store.close()
`
	if err := os.WriteFile(script, []byte(scriptBody), 0o600); err != nil {
		t.Fatal(err)
	}
	var pythonRows [][]any
	if err := json.Unmarshal([]byte(runPython(t, python, script, pythonPath)), &pythonRows); err != nil {
		t.Fatalf("decode Python invalidation ledger: %v", err)
	}
	seed := []RingBucketWrite{
		{1, 1003, `{"value":1}`, true}, {1, 1012, `{"value":1}`, true}, {1, 1040, `{"value":1}`, true}, {1, 1070, `{"value":1}`, true},
		{10, 1000, `{"value":1}`, true}, {10, 1020, `{"value":1}`, true}, {10, 1050, `{"value":1}`, true}, {10, 1060, `{"value":1}`, true},
		{60, 960, `{"value":1}`, true}, {60, 1020, `{"value":1}`, true}, {300, 900, `{"value":1}`, true},
	}
	writer, err := OpenWriter(context.Background(), goPath)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if _, err := writer.PublishRingBuckets(ctx, seed, 0, 1, true); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "obs", Family: "cpu", SourceID: "host", ObservedAt: 1003, EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 10}}}}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{{EventID: "codex:thread:atom", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 1012, Payload: map[string]any{"quantity": 4.0, "provider": "openai", "model": "m", "thread_id": "thread", "execution_source": "codex"}}}}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{UsageTombstones: []UsageTombstone{{EventID: "codex:thread:atom", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 1012, Quantity: 4, Provider: "openai", Model: "m", ThreadID: "thread"}}}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch", StartedAt: 1000, EndedAt: floatPointer(1060), NativeCadenceSeconds: 10, OwnerGeneration: 1}}}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "gpu:0", EpochID: "gap", StartedAt: 1060, EndedAt: 1080, NativeCadenceSeconds: 10, Reason: "driver unavailable", OwnerGeneration: 1}}}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{1, 1003, `{"value":2}`, true}}, 5, 2, true); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	rows, err := writer.db.QueryContext(ctx, "SELECT resolution_seconds, bucket_start, source_generation, reason, applied_at IS NOT NULL FROM ring_invalidations ORDER BY resolution_seconds, bucket_start")
	if err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	var goRows [][]any
	for rows.Next() {
		var resolution, bucket, generation int64
		var reason string
		var applied bool
		if err := rows.Scan(&resolution, &bucket, &generation, &reason, &applied); err != nil {
			_ = rows.Close()
			_ = writer.Close()
			t.Fatal(err)
		}
		goRows = append(goRows, []any{resolution, bucket, generation, reason, applied})
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		_ = writer.Close()
		t.Fatal(err)
	}
	if err := rows.Close(); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	goJSON, _ := json.Marshal(goRows)
	pythonJSON, _ := json.Marshal(pythonRows)
	if string(goJSON) != string(pythonJSON) {
		t.Fatalf("Python/Go invalidation ledger mismatch\nGo: %s\nPython: %s", goJSON, pythonJSON)
	}
}

func sameSchemaTopology(got []byte, want []string) bool {
	var actual []string
	if json.Unmarshal(got, &actual) != nil {
		return false
	}
	return len(actual) == len(want) && strings.Join(actual, "\n") == strings.Join(want, "\n")
}

func goSchemaTopology(t *testing.T, path string) []byte {
	t.Helper()
	database, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	rows, err := database.Query("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name")
	if err != nil {
		t.Fatal(err)
	}
	var lines []string
	var objects []struct{ kind, name string }
	for rows.Next() {
		var kind, name string
		if err := rows.Scan(&kind, &name); err != nil {
			_ = rows.Close()
			t.Fatal(err)
		}
		objects = append(objects, struct{ kind, name string }{kind, name})
		lines = append(lines, fmt.Sprintf("object|%s|%s", kind, name))
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		t.Fatal(err)
	}
	if err := rows.Close(); err != nil {
		t.Fatal(err)
	}
	for _, object := range objects {
		if object.kind != "table" {
			continue
		}
		columns, err := database.Query("PRAGMA table_info(" + object.name + ")")
		if err != nil {
			t.Fatal(err)
		}
		for columns.Next() {
			var cid int
			var name, columnType string
			var notNull, primaryKey int
			var defaultValue any
			if err := columns.Scan(&cid, &name, &columnType, &notNull, &defaultValue, &primaryKey); err != nil {
				_ = columns.Close()
				t.Fatal(err)
			}
			lines = append(lines, fmt.Sprintf("column|%s|%d|%s|%s|%d|%v|%d", object.name, cid, name, columnType, notNull, defaultValue, primaryKey))
		}
		if err := columns.Close(); err != nil {
			t.Fatal(err)
		}
		indexes, err := database.Query("PRAGMA index_list(" + object.name + ")")
		if err != nil {
			t.Fatal(err)
		}
		for indexes.Next() {
			var sequence, unique, partial int
			var name, origin string
			if err := indexes.Scan(&sequence, &name, &unique, &origin, &partial); err != nil {
				_ = indexes.Close()
				t.Fatal(err)
			}
			lines = append(lines, fmt.Sprintf("index|%s|%d|%s|%d|%s|%d", object.name, sequence, name, unique, origin, partial))
			indexColumns, err := database.Query("PRAGMA index_info(" + name + ")")
			if err != nil {
				_ = indexes.Close()
				t.Fatal(err)
			}
			for indexColumns.Next() {
				var indexSequence, columnNumber int
				var columnName sql.NullString
				if err := indexColumns.Scan(&indexSequence, &columnNumber, &columnName); err != nil {
					_ = indexColumns.Close()
					_ = indexes.Close()
					t.Fatal(err)
				}
				lines = append(lines, fmt.Sprintf("index-column|%s|%s|%d|%d|%s", object.name, name, indexSequence, columnNumber, columnName.String))
			}
			if err := indexColumns.Close(); err != nil {
				_ = indexes.Close()
				t.Fatal(err)
			}
		}
		if err := indexes.Close(); err != nil {
			t.Fatal(err)
		}
	}
	sort.Strings(lines)
	encoded, err := json.Marshal(lines)
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}

func writePythonSchemaTopologyScript(t *testing.T, path string) {
	t.Helper()
	const script = `import json, sqlite3, sys
from pathlib import Path
from yolomux_lib.stats_current.storage import Store

def topology(path):
    connection = sqlite3.connect(path)
    lines = []
    objects = connection.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    for kind, name in objects:
        lines.append(f"object|{kind}|{name}")
    for kind, name in objects:
        if kind != "table":
            continue
        for cid, column, column_type, not_null, default_value, primary_key in connection.execute(f"PRAGMA table_info({name})"):
            lines.append(f"column|{name}|{cid}|{column}|{column_type}|{not_null}|{default_value if default_value is not None else '<nil>'}|{primary_key}")
        for sequence, index, unique, origin, partial in connection.execute(f"PRAGMA index_list({name})"):
            lines.append(f"index|{name}|{sequence}|{index}|{unique}|{origin}|{partial}")
            for index_sequence, column_number, column in connection.execute(f"PRAGMA index_info({index})"):
                lines.append(f"index-column|{name}|{index}|{index_sequence}|{column_number}|{column or ''}")
    connection.close()
    print(json.dumps(sorted(lines), separators=(",", ":")))

mode, path = sys.argv[1], Path(sys.argv[2])
if mode == "create":
    path.parent.mkdir(parents=True, exist_ok=True)
    store = Store.open(path, include_browser_diagnostics=False)
    store.close()
elif mode == "open-reader":
    store = Store.open_reader(path, include_browser_diagnostics=False)
    store.close()
elif mode == "topology":
    topology(path)
else:
    raise SystemExit(f"unknown mode: {mode}")
`
	if err := os.WriteFile(path, []byte(script), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestFakeClientUsagePersistsAndProjectsTokens(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	claudePath := filepath.Join(root, "claude.jsonl")
	codexPath := filepath.Join(root, "codex.jsonl")
	script := filepath.Join(root, "interop.py")
	writePythonInteropScript(t, script)
	writeJSONL(t, claudePath, usageClaude(100, "fake-claude", 4, 6))
	writeJSONL(t, codexPath, usageCodex(105, 5, 2, 5))

	claude, err := ScanTranscript(TranscriptSource{Path: claudePath, Provider: "claude", ThreadID: "fake-claude"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	codex, err := ScanTranscript(TranscriptSource{Path: codexPath, Provider: "codex", ThreadID: "fake-codex"}, &TranscriptScannerState{}, TranscriptScanOptions{})
	if err != nil {
		t.Fatal(err)
	}
	atoms := append(claude.Atoms, codex.Atoms...)
	if len(atoms) != 5 || atomQuantity(atoms, "input", "none") != 7 || atomQuantity(atoms, "input", "read") != 2 || atomQuantity(atoms, "output", "none") != 11 {
		t.Fatalf("fake client atoms = %#v", atomQuantities(atoms))
	}

	path := filepath.Join(root, "yostats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	result, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: atoms})
	if err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if result.Generation != 1 || result.UsageAccepted != len(atoms) {
		t.Fatalf("fake client append = %+v", result)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 100, End: 110, Resolution: 10})
	if err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Tokens.Buckets) != 1 {
		t.Fatalf("fake client token buckets = %+v", snapshot.Tokens.Buckets)
	}
	bucket := snapshot.Tokens.Buckets[0]
	if bucket.Input != 7 || bucket.Cache != 2 || bucket.Output != 11 || bucket.Input+bucket.Cache+bucket.Output != 20 {
		t.Fatalf("fake client token roles = %+v", bucket)
	}
	if got := (bucket.Input + bucket.Cache + bucket.Output) * 6; got != 120 {
		t.Fatalf("fake client tokens/minute = %g", got)
	}
	_ = runPython(t, pythonExecutable(t), script, "check-fake-stats", path)
}

func TestSystemMemoryCollectorPayloadMatchesPythonTopFiveTieContract(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	inputPath := filepath.Join(root, "memory-input.json")
	scriptPath := filepath.Join(root, "memory-oracle.py")
	type processRow struct {
		Binary   string `json:"binary"`
		RSSBytes uint64 `json:"rss_bytes"`
	}
	rows := []processRow{
		{Binary: "/usr/bin/python3.12", RSSBytes: 40},
		{Binary: "/venv/bin/python", RSSBytes: 30},
		{Binary: "zeta", RSSBytes: 20},
		{Binary: "alpha", RSSBytes: 20},
		{Binary: "/usr/bin/nodejs (DELETED)", RSSBytes: 15},
		{Binary: "beta", RSSBytes: 10},
		{Binary: "delta", RSSBytes: 10},
		{Binary: "epsilon", RSSBytes: 8},
		{Binary: "gamma", RSSBytes: 7},
	}
	input := map[string]any{
		"epoch_id": "memory-parity-epoch", "epoch_started_at": 100.0,
		"observed_at": 160.0, "cadence_seconds": 60.0,
		"owner_generation": uint64(7), "source_id": "host",
		"used_bytes": uint64(123), "capacity_bytes": uint64(456),
		"rows": rows,
	}
	inputJSON, err := json.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(inputPath, inputJSON, 0o600); err != nil {
		t.Fatal(err)
	}
	const oracle = `import json, sys
from pathlib import Path
from yolomux_lib.stats_current import collectors
from yolomux_lib.stats_current.process_memory import aggregate_process_memory_by_binary

source = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
process_memory = aggregate_process_memory_by_binary(
    (row["binary"], row["rss_bytes"]) for row in source["rows"]
)
facts = collectors.system_memory_success(
    epoch_id=source["epoch_id"], epoch_started_at=source["epoch_started_at"],
    observed_at=source["observed_at"], cadence_seconds=source["cadence_seconds"],
    owner_generation=source["owner_generation"], source_id=source["source_id"],
    used_bytes=source["used_bytes"], capacity_bytes=source["capacity_bytes"],
    process_memory_bytes=process_memory,
)
observation = facts.observations[0]
coverage_epoch = facts.coverage_epochs[0]
print(json.dumps({
    "observation": {
        "event_id": observation.event_id, "family": observation.family,
        "source_id": observation.source_id, "observed_at": observation.observed_at,
        "epoch_id": observation.epoch_id, "owner_generation": observation.owner_generation,
        "payload": dict(observation.payload),
    },
    "coverage_epoch": {
        "family": coverage_epoch.family, "source_id": coverage_epoch.source_id,
        "epoch_id": coverage_epoch.epoch_id, "started_at": coverage_epoch.started_at,
        "ended_at": coverage_epoch.ended_at,
        "native_cadence_seconds": coverage_epoch.native_cadence_seconds,
        "owner_generation": coverage_epoch.owner_generation,
    },
}, sort_keys=True, separators=(",", ":")))
`
	if err := os.WriteFile(scriptPath, []byte(oracle), 0o600); err != nil {
		t.Fatal(err)
	}
	pythonOutput := strings.TrimSpace(runPython(t, python, scriptPath, inputPath))
	var want map[string]any
	if err := json.Unmarshal([]byte(pythonOutput), &want); err != nil {
		t.Fatalf("decode Python memory facts %q: %v", pythonOutput, err)
	}
	processRows := make([]ProcessCensusRow, 0, len(rows))
	for index, row := range rows {
		processRows = append(processRows, ProcessCensusRow{
			Identity: fmt.Sprintf("synthetic-%d", index), Binary: row.Binary, RSSBytes: row.RSSBytes,
		})
	}
	context := HostFactContext{
		EpochID: "memory-parity-epoch", EpochStartedAt: 100, ObservedAt: 160,
		CadenceSeconds: 60, OwnerGeneration: 7, SourceID: "host",
	}
	facts := BuildSystemMemoryFacts(MemoryCounters{UsedBytes: 123, CapacityBytes: 456}, processRows, nil, context)
	if len(facts.Observations) != 1 || len(facts.CoverageEpochs) != 1 || len(facts.UnavailableSpans) != 0 {
		t.Fatalf("valid synthetic memory sample facts = %+v", facts)
	}
	observation := facts.Observations[0]
	coverage := facts.CoverageEpochs[0]
	got := map[string]any{
		"observation": map[string]any{
			"event_id": observation.EventID, "family": observation.Family,
			"source_id": observation.SourceID, "observed_at": observation.ObservedAt,
			"epoch_id": observation.EpochID, "owner_generation": observation.OwnerGeneration,
			"payload": observation.Payload,
		},
		"coverage_epoch": map[string]any{
			"family": coverage.Family, "source_id": coverage.SourceID, "epoch_id": coverage.EpochID,
			"started_at": coverage.StartedAt, "ended_at": coverage.EndedAt,
			"native_cadence_seconds": coverage.NativeCadenceSeconds,
			"owner_generation":       coverage.OwnerGeneration,
		},
	}
	gotJSON, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	var normalizedGot map[string]any
	if err := json.Unmarshal(gotJSON, &normalizedGot); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(normalizedGot, want) {
		t.Fatalf("Go system-memory facts differ from Python collector\nGo:     %s\nPython: %s", gotJSON, pythonOutput)
	}
	processMemory := observation.Payload["process_memory_bytes"]
	wantProcessMemory := map[string]any{
		"python": uint64(70), "alpha": uint64(20), "zeta": uint64(20),
		"node": uint64(15), "beta": uint64(10),
	}
	if !reflect.DeepEqual(processMemory, wantProcessMemory) {
		t.Fatalf("process-memory top-five series = %#v, want Python's tied cutoff %#v", processMemory, wantProcessMemory)
	}
}

func TestPythonGeneratedDeclaredFamilyFixtureMaterializesInGo(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	path := filepath.Join(root, "stats-v9.sqlite3")
	script := filepath.Join(root, "families.py")
	const fixture = `import sys
from yolomux_lib.stats_current import collectors
from yolomux_lib.stats_current.storage import Store, UsageAtom

path = sys.argv[1]
store = Store.open(path)
context = {
    "epoch_started_at": 0,
    "owner_generation": 1,
}
facts = [
    collectors.cpu_success(
        **context, epoch_id="epoch-cpu", observed_at=1,
        cadence_seconds=1, source_id="host", process_percent=20,
        system_percent=40, process_cpu_percent={"python": 4},
    ),
    collectors.agent_status_success(
        **context, epoch_id="epoch-agent_status", observed_at=2,
        cadence_seconds=10, source_id="host", states={"a": "run", "b": "idle"},
        session_states={"s": "run"},
    ),
    collectors.gpu_devices_success(
        [collectors.GpuDeviceSample("gpu:0", 50, 100, 200, "gpu")],
        **context, epoch_id="epoch-gpu", observed_at=3,
        cadence_seconds=10,
    ),
    collectors.service_load_success(
        [collectors.ServiceLoadSample("statsd", True, 3, 1000)],
        **context, epoch_id="epoch-service_load", observed_at=4,
        cadence_seconds=1,
    ),
    collectors.system_memory_success(
        **context, epoch_id="epoch-system_memory", observed_at=5,
        cadence_seconds=60, source_id="host", used_bytes=100,
        capacity_bytes=200,
    ),
    collectors.browser_event_success(
        **context, epoch_id="epoch-browser", observed_at=6,
        cadence_seconds=1, source_id="client", event_key="api-1",
        kind="api", latency_ms=8, bytes_count=100,
    ),
    collectors.usage_scan_success(
        [UsageAtom("usage-1", "input", "text", "none", "tokens", 3,
                   {"quantity": 10, "agent_id": "a", "model": "m", "provider": "p",
                    "telemetry_complete": False})],
        epoch_id="epoch-agent_tokens", epoch_started_at=0, observed_at=6,
        cadence_seconds=10, owner_generation=1, source_id="host",
    ),
    collectors.collector_unavailable(
        family="gpu", source_id="gpu-gap", epoch_id="epoch-gpu-gap",
        epoch_started_at=0, observed_at=5, cadence_seconds=10,
        owner_generation=1, reason="driver unavailable",
    ),
]
store.append_batch(
    observations=(item for fact in facts for item in fact.observations),
    coverage_epochs=(item for fact in facts for item in fact.coverage_epochs),
    usage_atoms=(item for fact in facts for item in fact.usage_atoms),
    unavailable_spans=(item for fact in facts for item in fact.unavailable_spans),
)
store.close()
`
	if err := os.WriteFile(script, []byte(fixture), 0o600); err != nil {
		t.Fatal(err)
	}
	runPython(t, python, script, path)
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(context.Background(), 0, 20, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	epochs, err := database.ReadCoverageEpochs(context.Background(), 0, 20, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	epochEnd := func(value float64) *float64 { return &value }
	expectedEpochs := []CoverageEpoch{
		{Family: "agent_status", SourceID: "host", EpochID: "epoch-agent_status", StartedAt: 0, EndedAt: epochEnd(12), NativeCadenceSeconds: 10, OwnerGeneration: 1},
		{Family: "agent_tokens", SourceID: "host", EpochID: "epoch-agent_tokens", StartedAt: 0, EndedAt: epochEnd(16), NativeCadenceSeconds: 10, OwnerGeneration: 1},
		{Family: "browser", SourceID: "client", EpochID: "epoch-browser", StartedAt: 0, EndedAt: epochEnd(7), NativeCadenceSeconds: 1, OwnerGeneration: 1},
		{Family: "cpu", SourceID: "host", EpochID: "epoch-cpu", StartedAt: 0, EndedAt: epochEnd(2), NativeCadenceSeconds: 1, OwnerGeneration: 1},
		{Family: "gpu", SourceID: "gpu:0", EpochID: "epoch-gpu", StartedAt: 0, EndedAt: epochEnd(13), NativeCadenceSeconds: 10, OwnerGeneration: 1},
		{Family: "service_load", SourceID: "statsd", EpochID: "epoch-service_load", StartedAt: 0, EndedAt: epochEnd(5), NativeCadenceSeconds: 1, OwnerGeneration: 1},
		{Family: "system_memory", SourceID: "host", EpochID: "epoch-system_memory", StartedAt: 0, EndedAt: epochEnd(65), NativeCadenceSeconds: 60, OwnerGeneration: 1},
	}
	if !reflect.DeepEqual(epochs, expectedEpochs) {
		t.Fatalf("Python collector coverage epochs = %+v, want %+v", epochs, expectedEpochs)
	}
	spans, err := database.ReadUnavailableSpans(context.Background(), 0, 20, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	atoms, err := database.ReadUsageAtoms(context.Background(), 0, 20, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 20, Resolution: 10}, observations, epochs, spans, atoms)
	if err != nil {
		t.Fatal(err)
	}
	wantNoData := []MaterializedNoData{
		{Family: "agent_status", SourceID: "host", EpochID: "epoch-agent_status", Start: 12, End: 20, NativeCadenceSeconds: 10, Reason: "coverage_gap"},
		{Family: "browser", SourceID: "client", EpochID: "epoch-browser", Start: 7, End: 20, NativeCadenceSeconds: 1, Reason: "coverage_gap"},
		{Family: "cpu", SourceID: "host", EpochID: "epoch-cpu", Start: 2, End: 20, NativeCadenceSeconds: 1, Reason: "coverage_gap"},
		{Family: "gpu", SourceID: "gpu-gap", EpochID: "epoch-gpu-gap", Start: 5, End: 15, NativeCadenceSeconds: 10, Reason: "driver unavailable"},
		{Family: "gpu", SourceID: "gpu:0", EpochID: "epoch-gpu", Start: 13, End: 20, NativeCadenceSeconds: 10, Reason: "coverage_gap"},
		{Family: "service_load", SourceID: "statsd", EpochID: "epoch-service_load", Start: 5, End: 20, NativeCadenceSeconds: 1, Reason: "coverage_gap"},
	}
	if !reflect.DeepEqual(layer.NoData, wantNoData) {
		t.Fatalf("Python collector no-data parity = %+v, want %+v", layer.NoData, wantNoData)
	}
	if got := seriesValue(layer.Buckets[0], "cpu_percent:host"); got != 20 {
		t.Fatalf("CPU parity = %v", got)
	}
	if got := seriesValue(layer.Buckets[0], "process_cpu_percent:python"); got != 4 {
		t.Fatalf("per-binary CPU parity = %v", got)
	}
	if got := seriesValue(layer.Buckets[0], "model_tokens_per_minute:input:m"); got != 60 {
		t.Fatalf("token parity = %v", got)
	}
	for _, check := range []struct {
		series string
		want   float64
	}{
		{series: "run_agents", want: 1},
		{series: "idle_agents", want: 1},
		{series: "run_sessions", want: 1},
		{series: "gpu_util_percent:gpu:0", want: 50},
		{series: "gpu_memory_bytes:gpu:0", want: 100},
		{series: "service_cpu_percent:statsd", want: 3},
		{series: "service_rss_bytes:statsd", want: 1000},
		{series: "system_memory_used_bytes", want: 100},
		{series: "system_memory_capacity_bytes", want: 200},
		{series: "system_cpu_percent", want: 40},
		{series: "browser_api_per_second", want: .1},
		{series: "browser_bandwidth_bytes_per_second", want: 10},
		{series: "browser_latency_ms", want: 8},
	} {
		if got := seriesValue(layer.Buckets[0], check.series); got != check.want {
			t.Errorf("Python collector family %s materialized as %v, want %v", check.series, got, check.want)
		}
	}
}

func TestPythonAndGoTranscriptScannersMatchSyntheticAtoms(t *testing.T) {
	type parityAtom struct {
		EventID         string  `json:"event_id"`
		Provider        string  `json:"provider"`
		ExecutionSource string  `json:"execution_source"`
		Endpoint        string  `json:"endpoint"`
		Model           string  `json:"model"`
		ModelEvidence   string  `json:"model_evidence"`
		Effort          string  `json:"effort"`
		PricingProfile  string  `json:"pricing_profile"`
		ServiceTier     string  `json:"service_tier"`
		AgentID         string  `json:"agent_id"`
		ThreadID        string  `json:"thread_id"`
		Direction       string  `json:"direction"`
		Modality        string  `json:"modality"`
		CacheRole       string  `json:"cache_role"`
		Unit            string  `json:"unit"`
		Quantity        float64 `json:"quantity"`
		ObservedAt      float64 `json:"observed_at"`
	}
	type parityTombstone struct {
		EventID    string  `json:"event_id"`
		Provider   string  `json:"provider"`
		Model      string  `json:"model"`
		ThreadID   string  `json:"thread_id"`
		Direction  string  `json:"direction"`
		Modality   string  `json:"modality"`
		CacheRole  string  `json:"cache_role"`
		Unit       string  `json:"unit"`
		Quantity   float64 `json:"quantity"`
		ObservedAt float64 `json:"observed_at"`
	}
	type parityResult struct {
		Atoms     []parityAtom      `json:"atoms"`
		Tombstone []parityTombstone `json:"tombstones"`
	}
	pythonScript := filepath.Join(t.TempDir(), "scan_transcript.py")
	const script = `import json, sys
from pathlib import Path
from yolomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yolomux_lib.stats_current.usage import legacy_fork_usage_tombstone_from_source, usage_atom_from_source

path, kind, split_at, byte_budget = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
lines = path.read_bytes().splitlines(keepends=True)
path.write_bytes(b"".join(lines[:split_at]))
scanner = StatsCurrentTranscriptUsageScanner(
    max_bytes_per_scan=byte_budget or 4 * 1024 * 1024,
    max_records_per_scan=1 if byte_budget else 4096,
)
tmux_key = "synthetic|0|" + kind
rows = [{"key": tmux_key, "kind": kind, "transcript": str(path)}]
all_items, all_tombstones = [], []
first = scanner.scan(rows)
scanner.commit(first.receipt_id)
all_items.extend(first.items)
all_tombstones.extend(first.tombstones)
with path.open("ab") as transcript:
    transcript.write(b"".join(lines[split_at:]))
for cycle in range(64):
    result = scanner.scan(rows)
    scanner.commit(result.receipt_id)
    all_items.extend(result.items)
    all_tombstones.extend(result.tombstones)
    if not result.budget_exhausted:
        break
else:
    raise RuntimeError("bounded Python transcript scan did not converge")
def atom_row(item):
    fields = dict(vars(item.atom))
    fields["tmux_key"] = item.tmux_key
    fields["agent_kind"] = item.agent_kind
    atom = usage_atom_from_source(fields)
    return {
        "event_id": atom.event_id,
        "provider": atom.payload["provider"],
        "execution_source": atom.payload.get("execution_source", ""),
        "endpoint": fields.get("endpoint", ""),
        "model": atom.payload["model"],
        "model_evidence": atom.payload.get("model_evidence", "unknown"),
        "effort": atom.payload.get("effort", "unknown"),
        "pricing_profile": atom.payload.get("pricing_profile", "default"),
        "service_tier": atom.payload.get("service_tier", "default"),
        "agent_id": atom.payload["agent_id"],
        "thread_id": atom.payload.get("thread_id", ""),
        "direction": atom.direction,
        "modality": atom.modality,
        "cache_role": atom.cache_role,
        "unit": atom.unit,
        "quantity": atom.payload["quantity"],
        "observed_at": atom.observed_at,
    }
def tombstone_row(item):
    fields = dict(vars(item.atom))
    fields["tmux_key"] = item.tmux_key
    fields["agent_kind"] = item.agent_kind
    tombstone = legacy_fork_usage_tombstone_from_source(fields)
    return {
        "event_id": tombstone.event_id,
        "provider": tombstone.provider,
        "model": tombstone.model,
        "thread_id": tombstone.thread_id,
        "direction": tombstone.direction,
        "modality": tombstone.modality,
        "cache_role": tombstone.cache_role,
        "unit": tombstone.unit,
        "quantity": tombstone.quantity,
        "observed_at": tombstone.observed_at,
    }
payload = {
    "atoms": sorted((atom_row(item) for item in all_items), key=lambda row: (row["event_id"], row["direction"], row["cache_role"])),
    "tombstones": sorted((tombstone_row(item) for item in all_tombstones), key=lambda row: (row["event_id"], row["direction"], row["cache_role"])),
}
json.dump(payload, sys.stdout, sort_keys=True, separators=(",", ":"))
`
	if err := os.WriteFile(pythonScript, []byte(script), 0o600); err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	fixtures := []struct {
		name       string
		provider   string
		path       string
		splitAt    int
		byteBudget int64
		records    []map[string]any
	}{
		{
			name: "claude cumulative message revision", provider: "claude",
			path:    filepath.Join(root, "claude", "session.jsonl"),
			splitAt: 1,
			records: []map[string]any{
				{"type": "user", "timestamp": 0.5, "message": map[string]any{"id": "ignored-user", "model": "claude-sonnet-synthetic", "usage": map[string]any{"input_tokens": 9, "output_tokens": 9}}},
				{"type": "assistant", "message": map[string]any{"id": "missing-timestamp", "model": "claude-sonnet-synthetic", "usage": map[string]any{"input_tokens": 8, "output_tokens": 8}}},
				{"type": "assistant", "timestamp": "2026-09-23T10:00:00", "message": map[string]any{"id": "naive-iso-timestamp", "model": "claude-sonnet-synthetic", "usage": map[string]any{"input_tokens": 11, "output_tokens": 3}}},
				usageClaude(1, "message-one", 20, 10),
				usageClaude(2, "message-one", 25, 15),
			},
		},
		{
			name: "codex fork handoff and cumulative usage", provider: "codex",
			path:       filepath.Join(root, "codex", "rollout-synthetic.jsonl"),
			splitAt:    4,
			byteBudget: 1024,
			records: []map[string]any{
				{"type": "session_meta", "timestamp": 1, "payload": map[string]any{"id": "child-thread", "forked_from_id": "parent-thread", "thread_source": "subagent"}},
				{"type": "session_meta", "timestamp": 1.5, "payload": map[string]any{"id": "parent-thread"}},
				{"type": "turn_context", "timestamp": 2, "payload": map[string]any{"model": "gpt-parent", "effort": "high", "pricing_profile": "subscription", "service_tier": "flex"}},
				usageCodex(3, 100, 40, 10),
				{"type": "turn_context", "timestamp": 3.5, "payload": map[string]any{"effort": "", "pricing_profile": "", "service_tier": ""}},
				usageCodex(4, 150, 70, 25),
				{"type": "turn_context", "timestamp": 5, "payload": map[string]any{"model": "gpt-child", "effort": "medium", "pricing_profile": "batch", "service_tier": "scale"}},
				{"type": "inter_agent_communication_metadata", "timestamp": 6, "payload": map[string]any{}},
				usageCodex(7, 160, 75, 30),
				{"type": "event_msg", "timestamp": 7.5, "payload": map[string]any{"type": "thread_settings_applied", "thread_settings": map[string]any{"reasoning_effort": "", "pricing_profile": "", "service_tier": ""}}},
				usageCodex(8, 170, 80, 35),
			},
		},
	}
	for _, fixture := range fixtures {
		t.Run(fixture.name, func(t *testing.T) {
			if fixture.byteBudget > 0 {
				fixture.records[fixture.splitAt]["padding"] = strings.Repeat("x", 8*1024)
			}
			source := TranscriptSource{Path: fixture.path, Provider: fixture.provider, AgentID: "synthetic|0|" + fixture.provider}
			writeJSONL(t, fixture.path, fixture.records[:fixture.splitAt]...)
			state := &TranscriptScannerState{}
			firstOptions := TranscriptScanOptions{}
			if fixture.byteBudget > 0 {
				firstOptions = TranscriptScanOptions{MaxBytes: fixture.byteBudget, MaxRecords: 1}
			}
			first, err := ScanTranscript(source, state, firstOptions)
			if err != nil {
				t.Fatal(err)
			}
			if first.Receipt == nil {
				t.Fatal("initial scan returned no cursor receipt")
			}
			cursorPath := filepath.Join(root, fixture.provider+"-cursor.json")
			cursor := NewTranscriptCursorStore(cursorPath)
			if err := cursor.Prepare(source, first.Receipt.before, first.Receipt.after); err != nil {
				t.Fatal(err)
			}
			if err := cursor.Commit(source, first.Receipt.after); err != nil {
				t.Fatal(err)
			}
			first.Receipt.Commit()
			for _, record := range fixture.records[fixture.splitAt:] {
				appendJSONL(t, fixture.path, record, false)
			}
			secondAtoms := make([]UsageAtom, 0)
			secondTombstones := make([]UsageTombstone, 0)
			if fixture.byteBudget > 0 {
				for cycle := 0; cycle < 64; cycle++ {
					cursor = NewTranscriptCursorStore(cursorPath)
					before, stateErr := cursor.State(source)
					if stateErr != nil {
						t.Fatalf("reload synthetic cursor on cycle %d: %v", cycle, stateErr)
					}
					if before.Offset == transcriptFixtureSize(t, fixture.path) && len(before.PendingLine) == 0 {
						break
					}
					partial, scanErr := ScanTranscript(source, &before, TranscriptScanOptions{MaxBytes: fixture.byteBudget, MaxRecords: 1})
					if scanErr != nil {
						t.Fatalf("bounded scan cycle %d: %v", cycle, scanErr)
					}
					if partial.BytesRead > fixture.byteBudget {
						t.Fatalf("bounded scan cycle %d read %d bytes with budget %d", cycle, partial.BytesRead, fixture.byteBudget)
					}
					after := partial.Receipt.after
					fixtureBytes, readErr := os.ReadFile(fixture.path)
					if readErr != nil {
						t.Fatal(readErr)
					}
					if len(after.ParsedTail) > 0 {
						tailStart := after.Offset - int64(len(after.ParsedTail))
						if tailStart < 0 || after.Offset > int64(len(fixtureBytes)) || !bytes.Equal(fixtureBytes[tailStart:after.Offset], after.ParsedTail) {
							t.Fatalf("parsed tail does not match bytes before the committed cursor on cycle %d: offset=%d tail=%d file=%d", cycle, after.Offset, len(after.ParsedTail), len(fixtureBytes))
						}
					}
					if len(after.PendingLine) > 0 {
						// A scan can finish and commit the prior pending record, then
						// retain the next incomplete record. Offset therefore advances
						// whenever the complete prefix advances; it is not required to
						// remain equal to before.Offset while PendingLine is non-empty.
						if after.Offset < before.Offset {
							info, statErr := os.Stat(fixture.path)
							if statErr != nil {
								t.Fatal(statErr)
							}
							device, inode := fileIdentity(info)
							fileBytes, readErr := os.ReadFile(fixture.path)
							if readErr != nil {
								t.Fatal(readErr)
							}
							sampleBytes := int(fixture.byteBudget / 8)
							prefixLength := min(len(before.Prefix), sampleBytes)
							tailLength := min(len(before.ParsedTail), sampleBytes)
							pendingTailLength := min(len(before.PendingLine), sampleBytes)
							prefixMatches := prefixLength == 0 || len(fileBytes) >= prefixLength && bytes.Equal(fileBytes[:prefixLength], before.Prefix[:prefixLength])
							tailStart := before.Offset - int64(tailLength)
							tailMatches := tailLength == 0 || tailStart >= 0 && int64(len(fileBytes)) >= before.Offset && bytes.Equal(fileBytes[tailStart:before.Offset], before.ParsedTail[len(before.ParsedTail)-tailLength:])
							pendingStart := before.Offset + int64(len(before.PendingLine)-pendingTailLength)
							pendingMatches := pendingTailLength == 0 || int64(len(fileBytes)) >= pendingStart+int64(pendingTailLength) && bytes.Equal(fileBytes[pendingStart:pendingStart+int64(pendingTailLength)], before.PendingLine[len(before.PendingLine)-pendingTailLength:])
							t.Fatalf("bounded cursor reset on cycle %d: reset=%t prefix_matches=%t parsed_tail_matches=%t pending_matches=%t identity_changed=%t before={offset:%d size:%d mtime:%d device:%d inode:%d pending:%d} after={offset:%d size:%d mtime:%d device:%d inode:%d pending:%d} file={size:%d mtime:%d device:%d inode:%d}", cycle, partial.Reset, prefixMatches, tailMatches, pendingMatches, identityChanged(before, info), before.Offset, before.FileSize, before.FileModTimeNS, before.Device, before.Inode, len(before.PendingLine), after.Offset, after.FileSize, after.FileModTimeNS, after.Device, after.Inode, len(after.PendingLine), info.Size(), info.ModTime().UnixNano(), device, inode)
						}
						pendingEnd := after.Offset + int64(len(after.PendingLine))
						if pendingEnd > int64(len(fixtureBytes)) || !bytes.Equal(fixtureBytes[after.Offset:pendingEnd], after.PendingLine) {
							t.Fatalf("pending record does not match the uncommitted file suffix on cycle %d: offset=%d pending=%d file=%d", cycle, after.Offset, len(after.PendingLine), len(fixtureBytes))
						}
						if bytes.ContainsAny(after.PendingLine, "\r\n") || after.Offset > 0 && fixtureBytes[after.Offset-1] != '\n' {
							t.Fatalf("cursor boundary splits a complete record on cycle %d: offset=%d pending=%d", cycle, after.Offset, len(after.PendingLine))
						}
						if after.Offset == before.Offset && len(after.PendingLine) <= len(before.PendingLine) {
							t.Fatalf("partial record made no forward progress on cycle %d: before offset=%d pending=%d after offset=%d pending=%d", cycle, before.Offset, len(before.PendingLine), after.Offset, len(after.PendingLine))
						}
					}
					secondAtoms = append(secondAtoms, partial.Atoms...)
					secondTombstones = append(secondTombstones, partial.Tombstones...)
					if partial.Receipt != nil {
						if err := cursor.Prepare(source, partial.Receipt.before, partial.Receipt.after); err != nil {
							t.Fatalf("prepare cursor on cycle %d: %v", cycle, err)
						}
						if err := cursor.Commit(source, partial.Receipt.after); err != nil {
							t.Fatalf("commit cursor on cycle %d: %v", cycle, err)
						}
						partial.Receipt.Commit()
					}
					if cycle == 63 {
						t.Fatal("bounded transcript scan did not converge")
					}
				}
			} else {
				restartedState, stateErr := NewTranscriptCursorStore(cursorPath).State(source)
				if stateErr != nil {
					t.Fatal(stateErr)
				}
				second, scanErr := ScanTranscript(source, &restartedState, TranscriptScanOptions{})
				if scanErr != nil {
					t.Fatal(scanErr)
				}
				secondAtoms = append(secondAtoms, second.Atoms...)
				secondTombstones = append(secondTombstones, second.Tombstones...)
				if second.Receipt != nil {
					second.Receipt.Commit()
				}
			}
			result := TranscriptScanResult{
				Atoms:      append(append([]UsageAtom{}, first.Atoms...), secondAtoms...),
				Tombstones: append(append([]UsageTombstone{}, first.Tombstones...), secondTombstones...),
			}
			goResult := parityResult{Atoms: make([]parityAtom, 0), Tombstone: make([]parityTombstone, 0)}
			for _, atom := range result.Atoms {
				quantity, _ := atom.Payload["quantity"].(float64)
				goResult.Atoms = append(goResult.Atoms, parityAtom{
					EventID: atom.EventID, Provider: stringPayload(atom.Payload, "provider"), ExecutionSource: stringPayload(atom.Payload, "execution_source"), Endpoint: stringPayload(atom.Payload, "endpoint"), Model: stringPayload(atom.Payload, "model"),
					ModelEvidence: stringPayload(atom.Payload, "model_evidence"), Effort: stringPayload(atom.Payload, "effort"),
					PricingProfile: stringPayload(atom.Payload, "pricing_profile"), ServiceTier: stringPayload(atom.Payload, "service_tier"),
					AgentID: stringPayload(atom.Payload, "agent_id"), ThreadID: stringPayload(atom.Payload, "thread_id"),
					Direction: atom.Direction, Modality: atom.Modality, CacheRole: atom.CacheRole, Unit: atom.Unit,
					Quantity: quantity, ObservedAt: atom.ObservedAt,
				})
			}
			for _, tombstone := range result.Tombstones {
				goResult.Tombstone = append(goResult.Tombstone, parityTombstone{
					EventID: tombstone.EventID, Provider: tombstone.Provider, Model: tombstone.Model, ThreadID: tombstone.ThreadID,
					Direction: tombstone.Direction, Modality: tombstone.Modality, CacheRole: tombstone.CacheRole,
					Unit: tombstone.Unit, Quantity: tombstone.Quantity, ObservedAt: tombstone.ObservedAt,
				})
			}
			pythonOutput := runPython(t, pythonExecutable(t), pythonScript, fixture.path, fixture.provider, fmt.Sprintf("%d", fixture.splitAt), fmt.Sprintf("%d", fixture.byteBudget))
			var pythonResult parityResult
			if err := json.Unmarshal([]byte(pythonOutput), &pythonResult); err != nil {
				t.Fatalf("decode Python scanner result %q: %v", pythonOutput, err)
			}
			parityKey := func(left, right parityAtom) bool {
				if left.EventID != right.EventID {
					return left.EventID < right.EventID
				}
				if left.Direction != right.Direction {
					return left.Direction < right.Direction
				}
				return left.CacheRole < right.CacheRole
			}
			parityTombstoneKey := func(left, right parityTombstone) bool {
				if left.EventID != right.EventID {
					return left.EventID < right.EventID
				}
				if left.Direction != right.Direction {
					return left.Direction < right.Direction
				}
				return left.CacheRole < right.CacheRole
			}
			sort.Slice(goResult.Atoms, func(i, j int) bool { return parityKey(goResult.Atoms[i], goResult.Atoms[j]) })
			sort.Slice(goResult.Tombstone, func(i, j int) bool { return parityTombstoneKey(goResult.Tombstone[i], goResult.Tombstone[j]) })
			for index := 1; index < len(goResult.Atoms); index++ {
				previous, current := goResult.Atoms[index-1], goResult.Atoms[index]
				if previous.EventID == current.EventID && previous.Direction == current.Direction && previous.CacheRole == current.CacheRole {
					t.Fatalf("Go emitted duplicate atom after cursor reload: %+v", current)
				}
			}
			if !reflect.DeepEqual(goResult, pythonResult) {
				t.Fatalf("Python/Go transcript outputs differ\nGo:     %+v\nPython: %+v", goResult, pythonResult)
			}
		})
	}
}

func TestPythonAndGoTranscriptSchedulersMatchSyntheticLiveHistoryOrder(t *testing.T) {
	const pythonScanScript = `import json, sys
from yolomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner

live_path, h0_path, h1_path = sys.argv[1:4]
rows = [
    {"key": "live", "kind": "codex", "transcript": live_path},
    {"key": "h0", "kind": "claude", "transcript": h0_path},
    {"key": "h1", "kind": "claude", "transcript": h1_path},
]
scanner = StatsCurrentTranscriptUsageScanner(max_bytes_per_scan=1 << 20, max_records_per_scan=1)
selected = []
for cycle in range(6):
    result = scanner.scan(rows)
    if not result.items:
        raise RuntimeError(f"cycle {cycle} selected no usage source")
    selected.append(result.items[0].tmux_key)
    scanner.commit(result.receipt_id)
    if cycle < 5:
        record = {
            "timestamp": cycle + 2,
            "type": "event_msg",
            "payload": {"info": {"total_token_usage": {
                "input_tokens": (cycle + 2) * 10,
                "cached_input_tokens": 0,
                "output_tokens": cycle + 2,
            }}},
        }
        with open(live_path, "a", encoding="utf-8") as transcript:
            transcript.write(json.dumps(record, separators=(",", ":")) + "\n")
json.dump(selected, sys.stdout, separators=(",", ":"))
`
	pythonRoot := t.TempDir()
	goRoot := t.TempDir()
	pythonScript := filepath.Join(pythonRoot, "scan_scheduler.py")
	if err := os.WriteFile(pythonScript, []byte(pythonScanScript), 0o600); err != nil {
		t.Fatal(err)
	}
	pythonPaths := []string{
		filepath.Join(pythonRoot, "live", "session.jsonl"),
		filepath.Join(pythonRoot, "h0", "session.jsonl"),
		filepath.Join(pythonRoot, "h1", "session.jsonl"),
	}
	goPaths := []string{
		filepath.Join(goRoot, "live", "session.jsonl"),
		filepath.Join(goRoot, "h0", "session.jsonl"),
		filepath.Join(goRoot, "h1", "session.jsonl"),
	}
	initialRecords := [][]map[string]any{
		{usageCodex(1, 10, 0, 1)},
		{usageClaude(1, "history-0", 20, 2)},
		{usageClaude(1, "history-1", 30, 3)},
	}
	for index := range pythonPaths {
		writeJSONL(t, pythonPaths[index], initialRecords[index]...)
		writeJSONL(t, goPaths[index], initialRecords[index]...)
	}

	pythonEnvRoot := filepath.Join(pythonRoot, "instance")
	pythonHome := filepath.Join(pythonRoot, "home")
	pythonTemp := filepath.Join(pythonRoot, "tmp")
	pythonXDG := filepath.Join(pythonRoot, "xdg")
	for _, directory := range []string{pythonEnvRoot, pythonHome, pythonTemp, pythonXDG} {
		if err := os.MkdirAll(directory, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	python := exec.Command(pythonExecutable(t), append([]string{pythonScript}, pythonPaths...)...)
	python.Dir = repositoryRoot(t)
	python.Env = testsupport.SyntheticEnvironment(
		"PYTHONDONTWRITEBYTECODE=1",
		"PYTHONPATH="+repositoryRoot(t),
		"HOME="+pythonHome,
		"TMPDIR="+pythonTemp,
		"YOLOMUX_CONFIG_DIR="+filepath.Join(pythonEnvRoot, "config"),
		"YOLOMUX_STATE_DIR="+filepath.Join(pythonEnvRoot, "state"),
		"YOLOMUX_CACHE_DIR="+filepath.Join(pythonEnvRoot, "cache"),
		"YOLOMUX_RUNTIME_DIR="+filepath.Join(pythonEnvRoot, "runtime"),
		"XDG_CONFIG_HOME="+filepath.Join(pythonXDG, "config"),
		"XDG_CACHE_HOME="+filepath.Join(pythonXDG, "cache"),
		"XDG_STATE_HOME="+filepath.Join(pythonXDG, "state"),
		"XDG_DATA_HOME="+filepath.Join(pythonXDG, "data"),
	)
	pythonOutputBytes, err := python.CombinedOutput()
	if err != nil {
		t.Fatalf("run synthetic Python transcript scheduler: %v\n%s", err, pythonOutputBytes)
	}
	pythonOutput := string(pythonOutputBytes)
	var pythonSelected []string
	if err := json.Unmarshal([]byte(pythonOutput), &pythonSelected); err != nil {
		t.Fatalf("decode Python scheduler result %q: %v", pythonOutput, err)
	}

	sources := []TranscriptSource{
		{Path: goPaths[0], Provider: "codex", SourceID: "live", ThreadID: "live", LiveCandidate: true},
		{Path: goPaths[1], Provider: "claude", SourceID: "h0", ThreadID: "history-0"},
		{Path: goPaths[2], Provider: "claude", SourceID: "h1", ThreadID: "history-1"},
	}
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
			return sources, nil
		},
		TranscriptCursorPath:         filepath.Join(goRoot, "transcript-cursors.json"),
		TranscriptMaxBytesPerCycle:   1 << 20,
		TranscriptMaxRecordsPerCycle: 1,
	})
	goSelected := make([]string, 0, 6)
	for cycle := 0; cycle < 6; cycle++ {
		batch, receipts, err := collector.collectTranscripts(context.Background(), float64(cycle+10), 1, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatalf("Go scheduler cycle %d: %v", cycle, err)
		}
		if len(batch.UsageAtoms) == 0 || len(batch.CoverageEpochs) != 1 {
			t.Fatalf("Go scheduler cycle %d did not serve exactly one synthetic source: atoms=%d coverage=%+v", cycle, len(batch.UsageAtoms), batch.CoverageEpochs)
		}
		goSelected = append(goSelected, batch.CoverageEpochs[0].SourceID)
		if err := commitReceipts(receipts); err != nil {
			t.Fatalf("commit Go scheduler cycle %d: %v", cycle, err)
		}
		if cycle < 5 {
			appendJSONL(t, goPaths[0], usageCodex(float64(cycle+2), float64((cycle+2)*10), 0, float64(cycle+2)), false)
		}
	}
	want := []string{"live", "live", "h0", "live", "live", "h1"}
	if !reflect.DeepEqual(pythonSelected, want) {
		t.Fatalf("Python scheduler source order = %v, want %v", pythonSelected, want)
	}
	if !reflect.DeepEqual(goSelected, want) {
		t.Fatalf("Go scheduler source order = %v, want %v", goSelected, want)
	}
}

func TestPythonAndGoTranscriptContextNormalizationMatchSyntheticUnicode(t *testing.T) {
	root := t.TempDir()
	scriptPath := filepath.Join(root, "normalize_transcript_context.py")
	const script = `import json
from yolomux_lib.workspace.session_files import transcript_effort, transcript_pricing_context

value = "\u0130" + "A" * 63
print(json.dumps([transcript_effort(value), transcript_pricing_context(value)], ensure_ascii=False))
`
	if err := os.WriteFile(scriptPath, []byte(script), 0o600); err != nil {
		t.Fatal(err)
	}
	output := runPython(t, pythonExecutable(t), scriptPath)
	var pythonValues []string
	if err := json.Unmarshal([]byte(output), &pythonValues); err != nil {
		t.Fatalf("decode Python transcript normalization %q: %v", output, err)
	}
	input := "\u0130" + strings.Repeat("A", 63)
	goValues := []string{normalizeTranscriptEffort(input), normalizeTranscriptPricingContext(input)}
	if !reflect.DeepEqual(goValues, pythonValues) {
		t.Fatalf("transcript context normalization differs: Go=%q Python=%q", goValues, pythonValues)
	}
}

func TestPythonAndGoTranscriptSchedulersPrioritizeRecentClaudeHistory(t *testing.T) {
	root := t.TempDir()
	oldPath := filepath.Join(root, "aaa-old.jsonl")
	newPath := filepath.Join(root, "zzz-new.jsonl")
	writeJSONL(t, oldPath, usageClaude(1, "old-history", 10, 1))
	writeJSONL(t, newPath, usageClaude(2, "new-history", 20, 2))
	oldTime := time.Unix(1_700_000_000, 0)
	newTime := time.Unix(1_700_001_000, 0)
	if err := os.Chtimes(oldPath, oldTime, oldTime); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(newPath, newTime, newTime); err != nil {
		t.Fatal(err)
	}

	pythonScript := filepath.Join(t.TempDir(), "scan_claude_priority.py")
	const script = `import json, sys
from yolomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner

old_path, new_path = sys.argv[1:3]
scanner = StatsCurrentTranscriptUsageScanner(max_bytes_per_scan=1 << 20, max_records_per_scan=1)
rows = [
    {"key": "old", "kind": "claude", "transcript": old_path},
    {"key": "new", "kind": "claude", "transcript": new_path},
]
result = scanner.scan(rows)
if not result.items:
    raise RuntimeError("Python scanner selected no synthetic Claude history")
json.dump(result.items[0].tmux_key, sys.stdout)
scanner.commit(result.receipt_id)
`
	if err := os.WriteFile(pythonScript, []byte(script), 0o600); err != nil {
		t.Fatal(err)
	}
	pythonOutput := strings.TrimSpace(runPython(t, pythonExecutable(t), pythonScript, oldPath, newPath))
	var pythonSelected string
	if err := json.Unmarshal([]byte(pythonOutput), &pythonSelected); err != nil {
		t.Fatalf("decode Python source priority %q: %v", pythonOutput, err)
	}
	if pythonSelected != "new" {
		t.Fatalf("Python selected Claude history %q, want newest source", pythonSelected)
	}

	for _, scenario := range []struct {
		name    string
		records []AgentRecord
	}{
		{
			name:    "fallback histories",
			records: []AgentRecord{{Kind: "claude", TmuxSession: "synthetic"}},
		},
		{
			name: "direct old history and fallback",
			records: []AgentRecord{
				{Kind: "claude", TmuxSession: "direct", TranscriptPath: oldPath},
				{Kind: "claude", TmuxSession: "fallback"},
			},
		},
		{
			name: "direct histories",
			records: []AgentRecord{
				{Kind: "claude", TmuxSession: "old-direct", TranscriptPath: oldPath},
				{Kind: "claude", TmuxSession: "new-direct", TranscriptPath: newPath},
			},
		},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
				TranscriptSources: func(ctx context.Context, records []AgentRecord) ([]TranscriptSource, error) {
					return defaultAgentTokenSourcesBounded(ctx, records, transcriptDiscoveryBounds{
						maxFiles: 10,
						maxDirs:  10,
						maxBytes: 1 << 20,
					}, root)
				},
				TranscriptCursorPath:         filepath.Join(t.TempDir(), "transcript-cursors.json"),
				TranscriptMaxBytesPerCycle:   1 << 20,
				TranscriptMaxRecordsPerCycle: 1,
			})
			batch, _, err := collector.collectTranscripts(context.Background(), 10, 1, scenario.records, nil, AppendBatch{}, nil)
			if err != nil {
				t.Fatal(err)
			}
			if len(batch.CoverageEpochs) != 1 {
				t.Fatalf("Go scheduler coverage = %+v, want one selected historical source", batch.CoverageEpochs)
			}
			wantSourceID := "claude:" + newPath
			if batch.CoverageEpochs[0].SourceID != wantSourceID {
				t.Fatalf("Go selected Claude history %q, want newest source %q", batch.CoverageEpochs[0].SourceID, wantSourceID)
			}
		})
	}
}

func stringPayload(payload map[string]any, key string) string {
	value, _ := payload[key].(string)
	return value
}

func pythonExecutable(t *testing.T) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 is required for the Python interoperability harness")
	}
	return "python3"
}

func runPython(t *testing.T, python, script string, args ...string) string {
	t.Helper()
	cmd := exec.Command(python, append([]string{script}, args...)...)
	cmd.Dir = repositoryRoot(t)
	cmd.Env = testsupport.SyntheticEnvironment("PYTHONDONTWRITEBYTECODE=1", "PYTHONPATH="+repositoryRoot(t))
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("python %s: %v\n%s", strings.Join(args, " "), err, output)
	}
	return string(output)
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	return testsupport.RepoRoot(t)
}

func writePythonInteropScript(t *testing.T, path string) {
	t.Helper()
	const script = `import json, sqlite3, sys
from pathlib import Path
from yolomux_lib.stats_current import opencode

def make_stats(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
    PRAGMA application_id = 0x594F5354;
    PRAGMA user_version = 9;
    CREATE TABLE schema_meta (singleton INTEGER PRIMARY KEY, minimum_writer_protocol INTEGER NOT NULL, minimum_writer_build INTEGER NOT NULL, source_generation INTEGER NOT NULL, last_vacuumed_at REAL NOT NULL DEFAULT 0);
    CREATE TABLE observations (event_id TEXT NOT NULL, family TEXT NOT NULL, source_id TEXT NOT NULL, observed_at REAL NOT NULL, epoch_id TEXT NOT NULL, owner_generation INTEGER NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(family, source_id, event_id)) WITHOUT ROWID;
    CREATE TABLE coverage_epochs (family TEXT NOT NULL, source_id TEXT NOT NULL, epoch_id TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, native_cadence_seconds REAL NOT NULL, owner_generation INTEGER NOT NULL, PRIMARY KEY(family, source_id, epoch_id)) WITHOUT ROWID;
    CREATE TABLE unavailable_spans (family TEXT NOT NULL, source_id TEXT NOT NULL, epoch_id TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL NOT NULL, native_cadence_seconds REAL NOT NULL, reason TEXT NOT NULL, owner_generation INTEGER NOT NULL, PRIMARY KEY(family, source_id, epoch_id, started_at, ended_at)) WITHOUT ROWID;
    CREATE TABLE usage_atoms (event_id TEXT NOT NULL, direction TEXT NOT NULL, modality TEXT NOT NULL, cache_role TEXT NOT NULL, unit TEXT NOT NULL, observed_at REAL NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(event_id, direction, modality, cache_role, unit)) WITHOUT ROWID;
    CREATE TABLE migration_reconciliation (migration_id TEXT PRIMARY KEY, completed_at REAL NOT NULL, source_digest TEXT NOT NULL, details_json TEXT NOT NULL) WITHOUT ROWID;
    INSERT INTO schema_meta VALUES (1, 25, 8, 1, 0);
    INSERT INTO observations VALUES ('agent-status-1', 'agent_status', 'host-test', 110, 'agent-epoch', 1, '{"states":{"ses-1":"working"},"host":"test-host"}');
    INSERT INTO observations VALUES ('host-facts-1', 'host_facts', 'host-test', 105, 'host-epoch', 1, '{"hostname":"test-host","memory_bytes":4096}');
    INSERT INTO coverage_epochs VALUES ('agent_status', 'host-test', 'agent-epoch', 100, 150, 10, 1);
    INSERT INTO coverage_epochs VALUES ('host_facts', 'host-test', 'host-epoch', 100, 150, 10, 1);
    INSERT INTO unavailable_spans VALUES ('host_facts', 'host-test', 'host-epoch', 120, 130, 10, 'fixture unavailable', 1);
    """)
    connection.commit(); connection.close()

def check_stats(path):
    connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    schema = connection.execute("PRAGMA user_version").fetchone()[0]
    generation = connection.execute("SELECT source_generation FROM schema_meta WHERE singleton=1").fetchone()[0]
    row = connection.execute("SELECT payload_json FROM observations WHERE event_id='agent-status-1'").fetchone()
    assert schema == 9 and generation == 1
    rows = connection.execute("SELECT event_id, family, source_id, observed_at, epoch_id, owner_generation FROM observations ORDER BY observed_at").fetchall()
    assert rows == [
        ("agent-status-1", "agent_status", "host-test", 110.0, "agent-epoch", 1),
        ("host-facts-1", "host_facts", "host-test", 115.0, "host-epoch", 1),
    ]
    assert connection.execute("SELECT family, source_id, epoch_id, started_at, ended_at, native_cadence_seconds, owner_generation FROM coverage_epochs ORDER BY family").fetchall() == [
        ("agent_status", "host-test", "agent-epoch", 100.0, 150.0, 10.0, 1),
        ("host_facts", "host-test", "host-epoch", 100.0, 115.0, 10.0, 1),
    ]
    assert connection.execute("SELECT family, source_id, epoch_id, started_at, ended_at, native_cadence_seconds, reason, owner_generation FROM unavailable_spans").fetchall() == [
        ("host_facts", "host-test", "host-epoch", 120.0, 130.0, 10.0, "fixture unavailable", 1),
    ]
    assert json.loads(row[0]) == {"states": {"ses-1": "working"}, "host": "test-host"}
    host_row = connection.execute("SELECT payload_json FROM observations WHERE event_id='host-facts-1'").fetchone()
    assert json.loads(host_row[0]) == {"hostname": "test-host", "memory_bytes": 4096}
    assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
    assert connection.execute("SELECT count(*) FROM coverage_epochs").fetchone()[0] == 2
    assert connection.execute("SELECT count(*) FROM unavailable_spans").fetchone()[0] == 1
    print(json.dumps({"schema": schema, "source_generation": generation, "observations": 2, "coverage": 2, "unavailable": 1, "payload": json.loads(row[0]), "payloads": {"agent_status": json.loads(row[0]), "host_facts": json.loads(host_row[0])}}, sort_keys=True))
    connection.close()

def check_fake_stats(path):
    connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
    rows = connection.execute("SELECT direction, cache_role, unit, observed_at, json_extract(payload_json, '$.quantity') FROM usage_atoms ORDER BY observed_at, direction, cache_role").fetchall()
    assert rows == [
        ("input", "none", "tokens", 100.0, 4.0),
        ("output", "none", "tokens", 100.0, 6.0),
        ("input", "none", "tokens", 105.0, 3.0),
        ("input", "read", "tokens", 105.0, 2.0),
        ("output", "none", "tokens", 105.0, 5.0),
    ]
    assert sum(row[4] for row in rows) == 20.0
    print(json.dumps({"schema": 9, "usage_atoms": len(rows), "source_tokens": 20, "tokens_per_minute": 120}, sort_keys=True))
    connection.close()

def make_opencode(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
    CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL);
    CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
    CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
    INSERT INTO session VALUES ('ses-python', '/interop', 'build', '{"providerID":"openai","id":"gpt-python"}', 900, 2500, 0);
    INSERT INTO message VALUES ('msg-python', 'ses-python', 1000, 2500, '{"role":"assistant","providerID":"openai","modelID":"gpt-python"}');
    INSERT INTO part VALUES ('part-python', 'msg-python', 'ses-python', 1100, 2000, '{"type":"step-finish","tokens":{"total":10,"input":3,"output":5,"reasoning":0,"cache":{"read":2,"write":0}}}');
    CREATE TABLE session_input (id TEXT, session_id TEXT, delivery TEXT, admitted_seq INTEGER, promoted_seq INTEGER, time_created INTEGER);
    INSERT INTO session_input VALUES ('input-python', 'ses-python', 'queue', 1, NULL, 2000);
    """)
    connection.commit(); connection.close()

def check_opencode(path):
    result = opencode.read_usage(Path(path), session_id="ses-go", now=10, max_session_age_seconds=100)
    if not isinstance(result, opencode.OpenCodeReadSuccess):
        raise AssertionError(result)
    values = {component.dimension: component.tokens for component in result.components}
    assert result.session.provider == "openai" and result.session.model == "gpt-go"
    assert values["input"] == 3 and values["output"] == 5 and values["cache_read"] == 2
    state = opencode.read_state(Path(path), session_id="ses-go", now=3)
    assert isinstance(state, opencode.OpenCodeStateSuccess)
    assert state.state == "paused" and state.evidence == ("queued-input",)
    print(json.dumps({"provider": result.session.provider, "model": result.session.model, "state": state.state, "evidence": list(state.evidence), **values}, sort_keys=True))

command, database = sys.argv[1:]
{"make-stats": make_stats, "check-stats": check_stats, "check-fake-stats": check_fake_stats, "make-opencode": make_opencode, "check-opencode": check_opencode}[command](database)
`
	if err := os.WriteFile(path, []byte(script), 0o600); err != nil {
		t.Fatal(err)
	}
}

func checkOpenCodeWithGo(t *testing.T, path string) {
	t.Helper()
	database, err := OpenOpenCodeReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	parts, err := database.ReadStepFinishParts(context.Background(), "ses-python", 1000, 3000, 2)
	if closeErr := database.Close(); closeErr != nil {
		t.Fatal(closeErr)
	}
	if err != nil {
		t.Fatal(err)
	}
	if len(parts) != 1 || parts[0].Provider != "openai" || parts[0].Model != "gpt-python" || parts[0].Tokens.Input != 3 || parts[0].Tokens.CacheRead != 2 || parts[0].Tokens.Output != 5 {
		t.Fatalf("Go OpenCode readback = %+v", parts)
	}
	stateReader := OpenCodeSQLiteStateReader(path)
	state, evidence, err := stateReader(context.Background(), AgentStatusRequest{OpenCodeSession: "ses-python"})
	if err != nil || state != "paused" || len(evidence) != 1 || evidence[0] != "queued-input" {
		t.Fatalf("Go OpenCode state readback = state %q evidence %v err %v", state, evidence, err)
	}
}

func createOpenCodeFixtureWithGo(t *testing.T, path string) {
	t.Helper()
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE session_input (id TEXT, session_id TEXT, delivery TEXT, admitted_seq INTEGER, promoted_seq INTEGER, time_created INTEGER)`,
		`INSERT INTO session VALUES ('ses-go', '/interop', 'build', '{"providerID":"openai","id":"gpt-go"}', 900, 2500, 0)`,
		`INSERT INTO message VALUES ('msg-go', 'ses-go', 1000, 2500, '{"role":"assistant","providerID":"openai","modelID":"gpt-go"}')`,
		`INSERT INTO part VALUES ('part-go', 'msg-go', 'ses-go', 1100, 2000, '{"type":"step-finish","tokens":{"total":10,"input":3,"output":5,"reasoning":0,"cache":{"read":2,"write":0}}}')`,
		`INSERT INTO session_input VALUES ('input-go', 'ses-go', 'queue', 1, NULL, 2000)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
}
