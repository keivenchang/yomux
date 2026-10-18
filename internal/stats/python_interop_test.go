package stats

import (
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

	"github.com/keivenchang/yomux/internal/testsupport"
	_ "github.com/mattn/go-sqlite3"
)

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
from yomux_lib.stats_current.storage import Store

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

func TestPythonGeneratedDeclaredFamilyFixtureMaterializesInGo(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	path := filepath.Join(root, "stats-v9.sqlite3")
	script := filepath.Join(root, "families.py")
	const fixture = `import sys
from yomux_lib.stats_current import collectors
from yomux_lib.stats_current.storage import Store, UsageAtom

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
from yomux_lib.stats_current import opencode

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
