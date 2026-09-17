package stats

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"

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

func TestPythonGeneratedDeclaredFamilyFixtureMaterializesInGo(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	path := filepath.Join(root, "families.sqlite3")
	script := filepath.Join(root, "families.py")
	const fixture = `import json, sqlite3, sys
path = sys.argv[1]
c = sqlite3.connect(path)
c.executescript("""
PRAGMA application_id = 0x594F5354; PRAGMA user_version = 9;
CREATE TABLE schema_meta (singleton INTEGER PRIMARY KEY, minimum_writer_protocol INTEGER NOT NULL, minimum_writer_build INTEGER NOT NULL, source_generation INTEGER NOT NULL, last_vacuumed_at REAL NOT NULL DEFAULT 0);
CREATE TABLE observations (event_id TEXT NOT NULL, family TEXT NOT NULL, source_id TEXT NOT NULL, observed_at REAL NOT NULL, epoch_id TEXT NOT NULL, owner_generation INTEGER NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(family, source_id, event_id)) WITHOUT ROWID;
CREATE TABLE coverage_epochs (family TEXT NOT NULL, source_id TEXT NOT NULL, epoch_id TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, native_cadence_seconds REAL NOT NULL, owner_generation INTEGER NOT NULL, PRIMARY KEY(family, source_id, epoch_id)) WITHOUT ROWID;
CREATE TABLE unavailable_spans (family TEXT NOT NULL, source_id TEXT NOT NULL, epoch_id TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL NOT NULL, native_cadence_seconds REAL NOT NULL, reason TEXT NOT NULL, owner_generation INTEGER NOT NULL, PRIMARY KEY(family, source_id, epoch_id, started_at, ended_at)) WITHOUT ROWID;
CREATE TABLE usage_atoms (event_id TEXT NOT NULL, direction TEXT NOT NULL, modality TEXT NOT NULL, cache_role TEXT NOT NULL, unit TEXT NOT NULL, observed_at REAL NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(event_id, direction, modality, cache_role, unit)) WITHOUT ROWID;
CREATE TABLE migration_reconciliation (migration_id TEXT PRIMARY KEY, completed_at REAL NOT NULL, source_digest TEXT NOT NULL, details_json TEXT NOT NULL) WITHOUT ROWID;
INSERT INTO schema_meta VALUES (1,25,8,1,0);
""")
def add(event, family, source, at, payload):
    c.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)", (event, family, source, at, "epoch-"+family, 1, json.dumps(payload, separators=(",", ":"), sort_keys=True)))
families = [
	("cpu", "host", {"process_percent": 20, "system_percent": 40, "process_cpu_percent": {"python": 4}}),
    ("agent_status", "host", {"states": {"a": "run", "b": "idle"}, "session_states": {"s": "run"}}),
    ("gpu", "host", {"util_percent": 50, "memory_used_bytes": 100, "memory_capacity_bytes": 200, "label": "gpu"}),
    ("service_load", "statsd", {"running": True, "cpu_percent": 3, "rss_bytes": 1000}),
    ("system_memory", "host", {"used_bytes": 100, "capacity_bytes": 200}),
    ("browser", "client", {"kind": "api", "latency_ms": 8, "bytes": 100}),
]
for i, (family, source, payload) in enumerate(families): add(f"{family}-{i}", family, source, i + 1, payload)
c.execute("INSERT INTO coverage_epochs VALUES ('cpu','host','epoch-cpu',0,20,1,1)")
c.execute("INSERT INTO unavailable_spans VALUES ('gpu','host','epoch-gpu',5,15,10,'driver unavailable',1)")
c.execute("INSERT INTO usage_atoms VALUES ('usage-1','input','text','none','tokens',3,?)", (json.dumps({'quantity': 10, 'agent_id': 'a', 'model': 'm', 'provider': 'p'}, separators=(',', ':'), sort_keys=True),))
c.commit(); c.close()
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
	if len(layer.NoData) != 1 || layer.NoData[0].Reason != "driver unavailable" {
		t.Fatalf("no-data parity = %+v", layer.NoData)
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
