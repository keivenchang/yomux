package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yolomux/internal/capabilities"
	"github.com/keivenchang/yolomux/internal/finder"
	"github.com/keivenchang/yolomux/internal/stats"
	"github.com/keivenchang/yolomux/internal/testsupport"
	"github.com/keivenchang/yolomux/internal/work"
)

func TestUseMachineTmuxForOperatorLaunch(t *testing.T) {
	t.Setenv("TMUX", "/tmp/private-tmux,123,0")
	t.Setenv("TMUX_PANE", "%7")
	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	t.Setenv("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER", "")

	useMachineTmuxForOperatorLaunch()

	if _, ok := os.LookupEnv("TMUX"); ok {
		t.Fatal("TMUX must not select a nested/private server")
	}
	if _, ok := os.LookupEnv("TMUX_PANE"); ok {
		t.Fatal("TMUX_PANE must not select a nested/private client")
	}
	if got := os.Getenv("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER"); got != "1" {
		t.Fatalf("default-server opt-in = %q, want 1", got)
	}
}

func TestUseMachineTmuxPreservesExplicitFixtureSocket(t *testing.T) {
	t.Setenv("TMUX", "/tmp/private-tmux,123,0")
	t.Setenv("TMUX_PANE", "%7")
	t.Setenv("YOLOMUX_TMUX_SOCKET", "/tmp/private-yolomux-tmux.sock")
	t.Setenv("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER", "")

	useMachineTmuxForOperatorLaunch()

	if got := os.Getenv("TMUX"); got != "/tmp/private-tmux,123,0" {
		t.Fatalf("TMUX = %q, want explicit fixture environment preserved", got)
	}
	if got := os.Getenv("TMUX_PANE"); got != "%7" {
		t.Fatalf("TMUX_PANE = %q, want explicit fixture environment preserved", got)
	}
	if got := os.Getenv("YOLOMUX_TMUX_SOCKET"); got != "/tmp/private-yolomux-tmux.sock" {
		t.Fatalf("YOLOMUX_TMUX_SOCKET = %q, want explicit fixture socket", got)
	}
	if got := os.Getenv("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER"); got != "" {
		t.Fatalf("default-server opt-in = %q, want unset for fixture", got)
	}
}

func TestTmuxCommandContextClearsInheritedNestedClientForGlobalRoster(t *testing.T) {
	t.Setenv("TMUX", "/tmp/private-supervisor,123,0")
	t.Setenv("TMUX_PANE", "%7")
	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	t.Setenv("LANG", "")
	t.Setenv("LC_ALL", "")
	t.Setenv("LC_CTYPE", "")

	command := (&server{}).tmuxCommand("list-sessions", "-F", "#{session_name}")
	for _, value := range command.Env {
		if strings.HasPrefix(value, "TMUX=") || strings.HasPrefix(value, "TMUX_PANE=") {
			t.Fatalf("global tmux command inherited nested client variable %q", value)
		}
	}
	if !strings.Contains(strings.Join(command.Env, "\x00"), "LC_ALL=C.UTF-8") {
		t.Fatal("global tmux command did not set the hermetic locale")
	}
}

func TestTmuxCommandContextPreservesExplicitFixtureSocketEnvironment(t *testing.T) {
	t.Setenv("TMUX", "/tmp/private-supervisor,123,0")
	t.Setenv("TMUX_PANE", "%7")
	t.Setenv("YOLOMUX_TMUX_SOCKET", "/tmp/private-fixture.sock")

	command := (&server{}).tmuxCommand("list-sessions")
	joined := strings.Join(command.Env, "\x00")
	if !strings.Contains(joined, "TMUX=/tmp/private-supervisor,123,0") || !strings.Contains(joined, "TMUX_PANE=%7") {
		t.Fatalf("explicit fixture command changed inherited fixture environment: %s", joined)
	}
}

func TestPythonBridgeEnvironmentUsesReadOnlyAuthConfig(t *testing.T) {
	env := strings.Join(pythonBridgeEnvironment("/fixture/root", "/fixture/config"), "\x00")
	if !strings.Contains(env, "PYTHONPATH=/fixture/root") {
		t.Fatal("python bridge did not receive the fixture root")
	}
	if !strings.Contains(env, "YOLOMUX_CONFIG_DIR=/fixture/config") {
		t.Fatal("python bridge did not receive the fixture config")
	}
	if !strings.Contains(env, "YOLOMUX_AUTH_CONFIG_READ_ONLY=1") {
		t.Fatal("python bridge must not rewrite auth.yaml")
	}
}

func TestWriteAndReadMaskedFrame(t *testing.T) {
	var wire bytes.Buffer
	clientFrame := []byte{0x81, 0x83, 1, 2, 3, 4, 'h' ^ 1, 'i' ^ 2, '!' ^ 3}
	if err := writeFrame(&wire, 1, []byte("ok")); err != nil {
		t.Fatal(err)
	}
	if wire.Len() != 4 {
		t.Fatalf("server frame length = %d, want 4", wire.Len())
	}
	opcode, payload, err := readFrame(bufio.NewReader(bytes.NewReader(clientFrame)))
	if err != nil {
		t.Fatal(err)
	}
	if opcode != 1 || string(payload) != "hi!" {
		t.Fatalf("frame = opcode %d payload %q", opcode, payload)
	}
}

func TestHealthAndCapabilities(t *testing.T) {
	s := &server{root: t.TempDir(), children: make(map[*exec.Cmd]struct{})}
	record := httptest.NewRecorder()
	s.health(record, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if record.Code != http.StatusOK || !bytes.Contains(record.Body.Bytes(), []byte(`"backend":"go"`)) {
		t.Fatalf("health response = %d %s", record.Code, record.Body.String())
	}
}

func TestTmuxIgnoreSizeCapabilityProbeUsesTheRealAttachFlag(t *testing.T) {
	tmuxDir, err := os.MkdirTemp("/tmp", "yolomux-tmux-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(tmuxDir) })
	socket := filepath.Join(tmuxDir, "tmux.sock")
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", "probe").CombinedOutput(); err != nil {
		t.Fatalf("start private tmux server: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	s := &server{}
	if !s.supportsIgnoreSize() {
		t.Fatal("tmux 3.4 capability probe rejected the supported ignore-size attach flag")
	}
	if !s.supportsIgnoreSize() {
		t.Fatal("cached tmux capability probe changed between calls")
	}
}

func TestGoCreateSessionLaunchesShellAndAgentsWithExplicitDangerousFlags(t *testing.T) {
	tmuxDir, err := os.MkdirTemp("/tmp", "ym-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(tmuxDir) })
	tmuxSocket := filepath.Join(tmuxDir, "tmux.sock")
	launchBin := t.TempDir()
	for _, name := range []string{"claude", "codex"} {
		path := filepath.Join(launchBin, name)
		if err := os.WriteFile(path, []byte("#!/bin/sh\nsleep 60\n"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", launchBin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("YOLOMUX_TMUX_SOCKET", tmuxSocket)
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", tmuxSocket, "kill-server").Run() })

	srv := &server{root: t.TempDir(), dangerouslyYolo: true}
	cases := []struct {
		name  string
		query string
		agent string
		want  string
	}{
		{name: "bash", query: "agent=term&terminal=bash&dangerously_yolo=0", agent: "term", want: "bash"},
		{name: "claude", query: "agent=claude&dangerously_yolo=1", agent: "claude", want: "claude --dangerously-skip-permissions"},
		{name: "codex", query: "agent=codex&dangerously_yolo=1", agent: "codex", want: "codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, "/api/create-session?session=go-"+item.name+"&"+item.query, nil)
			record := httptest.NewRecorder()
			srv.createSession(record, request)
			if record.Code != http.StatusOK {
				t.Fatalf("create session status = %d, body=%s", record.Code, record.Body.String())
			}
			var payload map[string]any
			if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
				t.Fatal(err)
			}
			if payload["agent"] != item.agent || payload["command"] != item.want || payload["created"] != true {
				t.Fatalf("create session payload = %#v, want agent=%q command=%q", payload, item.agent, item.want)
			}
			pane, err := srv.tmuxCommand("list-panes", "-t", "="+"go-"+item.name+":", "-F", "#{pane_start_command}").Output()
			if err != nil || !strings.Contains(string(pane), item.want) {
				t.Fatalf("created pane command = %q, err=%v, want %q", pane, err, item.want)
			}
		})
	}

	denied := &server{root: t.TempDir(), dangerouslyYolo: false}
	request := httptest.NewRequest(http.MethodPost, "/api/create-session?agent=codex&dangerously_yolo=1", nil)
	record := httptest.NewRecorder()
	denied.createSession(record, request)
	if record.Code != http.StatusForbidden || !bytes.Contains(record.Body.Bytes(), []byte("--dangerously-yolo")) {
		t.Fatalf("dangerous launch without opt-in = %d %s", record.Code, record.Body.String())
	}
}

func TestGoCreateSessionPlanReturnsJavaScriptSafeMonotonicGeneration(t *testing.T) {
	srv := &server{}
	var generations []int64
	for range 2 {
		record := httptest.NewRecorder()
		srv.createSessionPlan(record, httptest.NewRequest(http.MethodGet, "/api/create-session-plan", nil))
		if record.Code != http.StatusOK {
			t.Fatalf("create session plan status = %d, body=%s", record.Code, record.Body.String())
		}
		var payload struct {
			Generation int64 `json:"generation"`
		}
		if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		generations = append(generations, payload.Generation)
	}
	const javascriptMaxSafeInteger = int64(9007199254740991)
	if generations[0] <= 0 || generations[0] > javascriptMaxSafeInteger {
		t.Fatalf("first plan generation = %d, want a positive JavaScript-safe integer", generations[0])
	}
	if generations[1] <= generations[0] || generations[1] > javascriptMaxSafeInteger {
		t.Fatalf("plan generations = %v, want strictly increasing JavaScript-safe integers", generations)
	}
}

func TestDefaultConfigDirUsesProductRoot(t *testing.T) {
	t.Setenv("YOLOMUX_CONFIG_DIR", "")
	t.Setenv("YOLOMUX_ROOT", "/tmp/yolomux-test-root")
	t.Setenv("HOME", "/tmp/yolomux-test-home")
	if got, want := defaultConfigDir(), "/tmp/yolomux-test-root/config"; got != want {
		t.Fatalf("defaultConfigDir() = %q, want %q", got, want)
	}
}

func TestDefaultConfigDirUsesExplicitConfig(t *testing.T) {
	t.Setenv("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-explicit-config")
	t.Setenv("YOLOMUX_ROOT", "/tmp/yolomux-test-root")
	if got, want := defaultConfigDir(), "/tmp/yolomux-explicit-config"; got != want {
		t.Fatalf("defaultConfigDir() = %q, want %q", got, want)
	}
}

func TestExplicitCollectorPathsRequireAbsoluteSyntheticOrProductionInputs(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("CODEX_HOME", filepath.Join(t.TempDir(), "codex"))
	t.Setenv("XDG_DATA_HOME", filepath.Join(t.TempDir(), "xdg-data"))
	for _, name := range []string{"YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", "YOLOMUX_CODEX_TRANSCRIPT_ROOT", "YOLOMUX_OPENCODE_DATABASE"} {
		t.Setenv(name, "")
	}
	if _, _, _, err := explicitCollectorPaths(); err == nil || !strings.Contains(err.Error(), "explicit YOLOMUX_CLAUDE_TRANSCRIPT_ROOT") {
		t.Fatalf("missing collector roots error = %v", err)
	}
	t.Setenv("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", "fixture/claude")
	t.Setenv("YOLOMUX_CODEX_TRANSCRIPT_ROOT", "/tmp/fixture/codex")
	t.Setenv("YOLOMUX_OPENCODE_DATABASE", "/tmp/fixture/opencode.db")
	if _, _, _, err := explicitCollectorPaths(); err == nil || !strings.Contains(err.Error(), "must be absolute") {
		t.Fatalf("relative collector root error = %v", err)
	}
	t.Setenv("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", "/tmp/fixture/claude")
	claude, codex, opencode, err := explicitCollectorPaths()
	if err != nil || claude != "/tmp/fixture/claude" || codex != "/tmp/fixture/codex" || opencode != "/tmp/fixture/opencode.db" {
		t.Fatalf("explicit collector paths = %q %q %q err=%v", claude, codex, opencode, err)
	}
}

func TestTLSFilesAndConfig(t *testing.T) {
	t.Setenv("TMPDIR", t.TempDir())
	certFile, keyFile, err := tlsFiles("", "", "127.0.0.1", 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(certFile); err != nil {
		t.Fatal(err)
	}
	if filepath.Ext(certFile) != ".crt" || filepath.Ext(keyFile) != ".key" {
		t.Fatalf("unexpected generated paths: %s %s", certFile, keyFile)
	}
	config, err := tlsConfig(certFile, keyFile)
	if err != nil {
		t.Fatal(err)
	}
	if config.MinVersion != 0x0303 {
		t.Fatalf("TLS minimum version = %#x", config.MinVersion)
	}
}

func TestEventBrokerPublishesAndReplays(t *testing.T) {
	b := newEventBroker()
	ch, unsubscribe := b.subscribe()
	defer unsubscribe()
	event, err := b.publish("files_changed", map[string]any{"revision": 3})
	if err != nil {
		t.Fatal(err)
	}
	got := <-ch
	if got.ID != event.ID || got.Type != "files_changed" || got.Revision != 1 || got.Base != 0 {
		t.Fatalf("event = %+v, want %+v", got, event)
	}
	if got.Epoch == "" || got.Resource != "files_changed" {
		t.Fatalf("event metadata = %+v", got)
	}
}

func TestPhase3ProtectedRoutes(t *testing.T) {
	s := &server{config: t.TempDir(), events: newEventBroker(), children: make(map[*exec.Cmd]struct{})}
	r := httptest.NewRecorder()
	s.systemStatus(r, httptest.NewRequest(http.MethodGet, "/api/system-status", nil))
	if r.Code != http.StatusOK || !bytes.Contains(r.Body.Bytes(), []byte(`"schema":"system-status-snapshot"`)) {
		t.Fatalf("system status = %d %s", r.Code, r.Body.String())
	}
	r = httptest.NewRecorder()
	s.operationAck(r, httptest.NewRequest(http.MethodPost, "/api/operations/ack", bytes.NewBufferString(`{"acks":[{"id":"op-1","cursor":{"epoch":"e","seq":1}}]}`)))
	if r.Code != http.StatusOK {
		t.Fatalf("operation ack = %d %s", r.Code, r.Body.String())
	}
}

func TestSystemStatusPublishesGoNativeLocalServicesSchema(t *testing.T) {
	started := time.Unix(1_700_000_000, 0)
	cpu := 12.5
	rss := uint64(64 * 1024 * 1024)
	threads := uint64(17)
	s := &server{
		startedAt: started,
		processMetricsFn: func(time.Time) goProcessMetrics {
			return goProcessMetrics{PID: 7222, StartedAt: started, CPUPercent: &cpu, RSSBytes: &rss, ThreadCount: &threads}
		},
	}
	t.Setenv("YOLOMUX_TMUX_SOCKET", filepath.Join(t.TempDir(), "no-server.sock"))
	record := httptest.NewRecorder()
	s.systemStatus(record, httptest.NewRequest(http.MethodGet, "/api/system-status", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("system status = %d %s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	local, ok := payload["local_services"].(map[string]any)
	if !ok || local["schema_version"] != float64(5) {
		t.Fatalf("local services payload = %#v", payload["local_services"])
	}
	if got := local["inventory"].([]any); len(got) != 1 || got[0] != "yolomux-go" {
		t.Fatalf("local service inventory = %#v", local["inventory"])
	}
	services := local["services"].([]any)
	if len(services) != 1 {
		t.Fatalf("local service rows = %#v", services)
	}
	service := services[0].(map[string]any)
	if service["parent_id"] != "web" || service["state"] != "running" || service["pid"] != float64(7222) {
		t.Fatalf("Go service identity = %#v", service)
	}
	serverPayload := payload["server"].(map[string]any)
	if serverPayload["pid"] != float64(7222) || serverPayload["started_at"] != float64(started.Unix()) {
		t.Fatalf("Go server identity = %#v", serverPayload)
	}
	serverCPU := serverPayload["cpu_percent"].(map[string]any)
	serverRSS := serverPayload["rss_bytes"].(map[string]any)
	serverUptime := serverPayload["uptime_seconds"].(map[string]any)
	if serverCPU["state"] != "measured" || serverRSS["state"] != "measured" || serverUptime["state"] != "measured" {
		t.Fatalf("Go server process metrics = %#v", serverPayload)
	}
	metrics := service["metrics"].(map[string]any)
	if metrics["cpu_now_percent"].(map[string]any)["state"] != "measured" || metrics["rss_bytes"].(map[string]any)["state"] != "measured" {
		t.Fatalf("Go service process metrics = %#v", metrics)
	}
	details := service["details"].(map[string]any)
	if details["cpu_scope"] != "Go process plus verified descendant processes" || details["cpu_normalization"] == "" {
		t.Fatalf("Go CPU semantics = %#v", details)
	}
	threadMetric := details["thread_count"].(map[string]any)
	if threadMetric["state"] != "measured" || threadMetric["value"] != float64(threads) {
		t.Fatalf("Go service thread metrics = %#v", details)
	}
	health := local["health"].(map[string]any)
	if health["available"] != true || health["reason_code"] != "" {
		t.Fatalf("Go service observer health = %#v", health)
	}
	if health["schema_version"] != float64(1) || health["observer_epoch_started_at"] != float64(started.Unix()) || health["observer_cycles"] != float64(1) {
		t.Fatalf("Go in-process health = %#v", health)
	}
	for _, field := range []string{"history_coverage", "history_reset_reason", "persistence_state", "persistence_reason_code"} {
		if _, ok := health[field]; !ok {
			t.Fatalf("Go health missing Python-compatible field %q: %#v", field, health)
		}
	}
	serviceHealth := service["health"].(map[string]any)
	for _, field := range []string{"recovery_outcome", "process_epoch", "pid", "observed_at", "since_revision", "since_wall_time", "state_age_seconds", "transitions_truncated", "errors_by_reason"} {
		if _, ok := serviceHealth[field]; !ok {
			t.Fatalf("Go service health missing Python-compatible field %q: %#v", field, serviceHealth)
		}
	}
	if serviceHealth["reason_code"] != "none" || serviceHealth["unavailable_reason_code"] != "" || serviceHealth["observed"] != true {
		t.Fatalf("Go service health reason fields = %#v", serviceHealth)
	}
	runtimeDiagnostics, ok := payload["stats_runtime"].(map[string]any)
	if !ok || runtimeDiagnostics["schema_version"] != float64(1) {
		t.Fatalf("Go stats runtime diagnostics = %#v", payload["stats_runtime"])
	}
	nestedRuntime, ok := details["stats_runtime"].(map[string]any)
	if !ok || nestedRuntime["schema_version"] != float64(1) {
		t.Fatalf("Go service runtime diagnostics = %#v", details["stats_runtime"])
	}
	coverage := serviceHealth["coverage"].(map[string]any)
	if coverage["counter_scope"] != "web_process" {
		t.Fatalf("Go service health coverage = %#v", coverage)
	}
	healthMetrics := service["health"].(map[string]any)["metrics"].(map[string]any)
	if healthMetrics["request_count"].(map[string]any)["state"] != "measured" || healthMetrics["request_count"].(map[string]any)["value"] != float64(0) {
		t.Fatalf("Go service request metric = %#v", healthMetrics["request_count"])
	}
	if healthMetrics["process_start_count"].(map[string]any)["value"] != float64(1) || healthMetrics["observations"].(map[string]any)["value"] != float64(1) {
		t.Fatalf("Go lifecycle metrics = %#v", healthMetrics)
	}
	if healthMetrics["restart_count"].(map[string]any)["state"] != "unavailable" || healthMetrics["restart_count"].(map[string]any)["reason_code"] != "go_health_history_unavailable" {
		t.Fatalf("Go restart metric = %#v", healthMetrics["restart_count"])
	}
	for _, field := range []string{"latency_average_ms", "latency_max_ms"} {
		metric := healthMetrics[field].(map[string]any)
		if metric["state"] != "unavailable" || metric["reason_code"] != "no_completed_request" {
			t.Fatalf("Go service latency metric %q = %#v", field, metric)
		}
	}
}

func TestProcessIdentityForRuntimeScopesHostCPUToThisPID(t *testing.T) {
	want := fmt.Sprintf("%d:", os.Getpid())
	if got := processIdentityForRuntime(); got != want {
		t.Fatalf("runtime process identity = %q, want %q", got, want)
	}
}

func TestStatsProjectionRefreshGateWaitsForDurablePublication(t *testing.T) {
	gate := statsProjectionRefreshGate{}
	first := stats.LifecycleCounters{Runs: 1}
	if !gate.needsRefresh(first) {
		t.Fatal("first lifecycle state must refresh the projection")
	}
	gate.record(first)
	if gate.needsRefresh(stats.LifecycleCounters{Runs: 2}) {
		t.Fatal("unchanged publication state must not reread ring products")
	}
	if !gate.needsRefresh(stats.LifecycleCounters{Runs: 3, Publications: 1}) {
		t.Fatal("new durable publication must refresh the projection")
	}
	gate.record(stats.LifecycleCounters{Runs: 3, Publications: 1})
	if !gate.needsRefresh(stats.LifecycleCounters{Runs: 4, Errors: 1}) {
		t.Fatal("new lifecycle error must retry projection refresh")
	}
}

func TestGoChildProcessMetricsCachesSyntheticRoster(t *testing.T) {
	var calls int
	rootPID := os.Getpid()
	s := &server{processEntriesFn: func() ([]procEntry, error) {
		calls++
		return []procEntry{{PID: rootPID + 1, PPID: rootPID, Command: "synthetic-child", Executable: "/bin/synthetic-child"}}, nil
	}}
	first := time.Unix(1_700_000_000, 0)
	if got := s.readGoChildProcessMetrics(first); len(got) != 1 || got[0].PID != rootPID+1 {
		t.Fatalf("first synthetic child metrics = %#v", got)
	}
	if got := s.readGoChildProcessMetrics(first.Add(time.Second)); len(got) != 1 || got[0].PID != rootPID+1 {
		t.Fatalf("cached synthetic child metrics = %#v", got)
	}
	if calls != 1 {
		t.Fatalf("synthetic process roster calls inside cache window = %d, want 1", calls)
	}
	if got := s.readGoChildProcessMetrics(first.Add(goProcessEntriesCacheTTL)); len(got) != 1 || got[0].PID != rootPID+1 {
		t.Fatalf("refreshed synthetic child metrics = %#v", got)
	}
	if calls != 2 {
		t.Fatalf("synthetic process roster calls after cache expiry = %d, want 2", calls)
	}
	s.processEntriesCacheMu.Lock()
	hits, misses := s.processEntriesCacheHits, s.processEntriesCacheMisses
	s.processEntriesCacheMu.Unlock()
	if hits != 1 || misses != 2 {
		t.Fatalf("synthetic roster cache counters = hits %d misses %d, want 1/2", hits, misses)
	}
}

func TestGoChildProcessMetricsUsesScopedSyntheticDescendantReader(t *testing.T) {
	rootPID := os.Getpid()
	var seenRoot int
	s := &server{childProcessEntriesFn: func(pid int) ([]procEntry, error) {
		seenRoot = pid
		return []procEntry{{PID: pid + 1, PPID: pid, Command: "synthetic-child"}}, nil
	}}
	metrics := s.readGoChildProcessMetrics(time.Unix(1_700_000_010, 0))
	if seenRoot != rootPID || len(metrics) != 1 || metrics[0].PID != rootPID+1 {
		t.Fatalf("scoped synthetic descendants = root %d metrics %#v, want root %d and one child", seenRoot, metrics, rootPID)
	}
}

func TestParseProcessChildrenAcceptsOnlyPositiveSyntheticPIDs(t *testing.T) {
	got := parseProcessChildren("101 202 bad -3 0 303")
	want := []int{101, 202, 303}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("synthetic children = %#v, want %#v", got, want)
	}
}

func TestGoHealthLedgerCountsConcurrentRequestsAndLatency(t *testing.T) {
	started := time.Unix(1_700_000_300, 0)
	ledger := newGoHealthLedger(started)
	const total = 64
	var wait sync.WaitGroup
	wait.Add(total)
	for index := 0; index < total; index++ {
		go func(index int) {
			defer wait.Done()
			finish := ledger.begin(false)
			if index%2 == 0 {
				finish(http.StatusOK, true, 2*time.Millisecond)
				return
			}
			finish(http.StatusBadGateway, false, 7*time.Millisecond)
		}(index)
	}
	wait.Wait()
	snapshot := ledger.snapshot()
	if snapshot.requestCount != total || snapshot.completedCount != total/2 || snapshot.errorCount != total/2 {
		t.Fatalf("ledger counters = %+v", snapshot)
	}
	if got := snapshot.latencyTotal / time.Millisecond; got != total/2*2 || snapshot.latencyMax != 2*time.Millisecond {
		t.Fatalf("ledger latency = total %s max %s", snapshot.latencyTotal, snapshot.latencyMax)
	}
	if snapshot.errorsByReason["http_5xx"] != total/2 {
		t.Fatalf("ledger errors by reason = %#v", snapshot.errorsByReason)
	}
}

func TestGoHealthLedgerExcludesProbeRoutesAndPublishesMeasuredLatency(t *testing.T) {
	started := time.Unix(1_700_000_400, 0)
	s := &server{startedAt: started, port: 7222, healthLedger: newGoHealthLedger(started)}
	handler := s.logging(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/error" {
			http.Error(w, "synthetic failure", http.StatusBadRequest)
			return
		}
		_, _ = w.Write([]byte("ok"))
	}))
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/ok", nil))
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/error", nil))
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/healthz", nil))
	snapshot := s.healthLedger.snapshot()
	if snapshot.requestCount != 2 || snapshot.completedCount != 1 || snapshot.errorCount != 1 {
		t.Fatalf("logged request counters = %+v", snapshot)
	}
	if snapshot.latencyTotal <= 0 || snapshot.latencyMax <= 0 {
		t.Fatalf("logged latency = total %s max %s", snapshot.latencyTotal, snapshot.latencyMax)
	}
}

func TestGoHealthLifecycleFailsClosedAcrossRestartHistory(t *testing.T) {
	started := time.Unix(1_700_000_500, 0)
	s := &server{startedAt: started, port: 7222, healthLedger: newGoHealthLedger(started)}
	payload := s.goLocalServicesPayloadFromMetrics(started.Add(4*time.Second), goProcessMetrics{PID: 7222, StartedAt: started})
	service := payload["services"].([]any)[0].(map[string]any)
	health := service["health"].(map[string]any)
	metrics := health["metrics"].(map[string]any)
	for _, name := range []string{"restart_count", "unexpected_restart_count"} {
		metric := metrics[name].(map[string]any)
		if metric["state"] != "unavailable" || metric["value"] != nil {
			t.Fatalf("%s should fail closed: %#v", name, metric)
		}
	}
	if metrics["process_start_count"].(map[string]any)["value"] != float64(1) || health["process_epoch"] == "" {
		t.Fatalf("current epoch measurement = %#v", health)
	}
}

func TestGoHealthLifecyclePersistsDemandAndUnexpectedRestarts(t *testing.T) {
	path := filepath.Join(t.TempDir(), "go-backend-health", "7222.json")
	firstStarted := time.Unix(1_700_001_000, 0)
	first, err := newGoHealthLifecycle(path, "pid:100:start:1", 100, firstStarted, "demand")
	if err != nil {
		t.Fatal(err)
	}
	if snapshot := first.snapshotNow(); !snapshot.Available || snapshot.RestartCount != 0 || snapshot.DemandStartCount != 0 || snapshot.UnexpectedRestartCount != 0 {
		t.Fatalf("initial lifecycle = %+v", snapshot)
	}
	first.markCleanShutdown()
	second, err := newGoHealthLifecycle(path, "pid:101:start:2", 101, firstStarted.Add(time.Minute), "demand")
	if err != nil {
		t.Fatal(err)
	}
	if snapshot := second.snapshotNow(); !snapshot.Available || snapshot.RestartCount != 1 || snapshot.DemandStartCount != 1 || snapshot.UnexpectedRestartCount != 0 {
		t.Fatalf("demand restart lifecycle = %+v", snapshot)
	}
	// Leave the second epoch unclean to model a crash before the next launch.
	third, err := newGoHealthLifecycle(path, "pid:102:start:3", 102, firstStarted.Add(2*time.Minute), "demand")
	if err != nil {
		t.Fatal(err)
	}
	snapshot := third.snapshotNow()
	if !snapshot.Available || snapshot.RestartCount != 2 || snapshot.DemandStartCount != 1 || snapshot.UnexpectedRestartCount != 1 {
		t.Fatalf("unexpected restart lifecycle = %+v", snapshot)
	}
	server := &server{port: 7222, healthLifecycle: third, startedAt: firstStarted.Add(2 * time.Minute), healthLedger: newGoHealthLedger(firstStarted.Add(2 * time.Minute))}
	payload := server.goLocalServicesPayloadFromMetrics(firstStarted.Add(2*time.Minute+time.Second), goProcessMetrics{PID: 102, StartedAt: firstStarted.Add(2 * time.Minute)})
	service := payload["services"].([]any)[0].(map[string]any)
	metrics := service["health"].(map[string]any)["metrics"].(map[string]any)
	for name, want := range map[string]float64{"restart_count": 2, "demand_start_count": 1, "unexpected_restart_count": 1} {
		metric := metrics[name].(map[string]any)
		if metric["state"] != "measured" || metric["value"] != want {
			t.Fatalf("%s = %#v", name, metric)
		}
	}
}

func TestGoLocalServicesThreadMetricFailsClosedWhenUnavailable(t *testing.T) {
	now := time.Unix(1_700_000_100, 0)
	payload := (&server{}).goLocalServicesPayloadFromMetrics(now, goProcessMetrics{PID: 7222, StartedAt: now})
	services := payload["services"].([]any)
	details := services[0].(map[string]any)["details"].(map[string]any)
	metric := details["thread_count"].(map[string]any)
	if metric["state"] != "unavailable" || metric["reason_code"] != "process_threads_unavailable" {
		t.Fatalf("unavailable Go thread metric = %#v", metric)
	}
}

func TestGoProcessMetricsCacheAvoidsRepeatedProcessTreeReads(t *testing.T) {
	now := time.Now()
	reads := 0
	s := &server{processEntriesFn: func() ([]procEntry, error) {
		reads++
		return nil, nil
	}}
	first := s.readGoProcessMetrics(now)
	if first.PID == 0 {
		t.Fatal("first process metrics sample has no PID")
	}
	_ = s.readGoProcessMetrics(now.Add(100 * time.Millisecond))
	if reads != 1 {
		t.Fatalf("process tree reads within one-second cache = %d, want 1", reads)
	}
	_ = s.readGoProcessMetrics(now.Add(goProcessEntriesCacheTTL + time.Millisecond))
	if reads != 2 {
		t.Fatalf("process tree reads after cache expiry = %d, want 2", reads)
	}
}

func TestGoAgentInventoryCacheSharesSyntheticSnapshot(t *testing.T) {
	processReads, paneReads := 0, 0
	s := &server{}
	sources := stats.AgentInventorySources{
		Processes: func(context.Context) ([]stats.AgentProcess, error) {
			processReads++
			return []stats.AgentProcess{{PID: 123, PPID: 1, Command: "claude --mock", Executable: "/usr/bin/claude"}}, nil
		},
		Panes: func(context.Context) ([]stats.AgentTmuxPane, error) {
			paneReads++
			return []stats.AgentTmuxPane{{Session: "synthetic", PaneID: "%1", PID: 123}}, nil
		},
	}
	first, firstErrs := s.cachedAgentInventory(context.Background(), sources)
	second, secondErrs := s.cachedAgentInventory(context.Background(), sources)
	if len(firstErrs) != 0 || len(secondErrs) != 0 || len(first) != 1 || len(second) != 1 {
		t.Fatalf("synthetic cached inventory = first=%+v/%v second=%+v/%v", first, firstErrs, second, secondErrs)
	}
	if processReads != 1 || paneReads != 1 || s.agentInventoryCacheHits != 1 || s.agentInventoryCacheMisses != 1 {
		t.Fatalf("inventory cache counters = process=%d panes=%d hits=%d misses=%d", processReads, paneReads, s.agentInventoryCacheHits, s.agentInventoryCacheMisses)
	}
	if s.agentInventoryLastDuration < 0 || s.agentInventoryDurationTotal < s.agentInventoryLastDuration {
		t.Fatalf("inventory timing counters = last=%s total=%s", s.agentInventoryLastDuration, s.agentInventoryDurationTotal)
	}
}

func TestFilterOpenCodeAgentRecordsPreservesSharedInventoryScope(t *testing.T) {
	records := filterOpenCodeAgentRecords([]stats.AgentRecord{
		{PID: 10, Kind: "claude", TmuxSession: "claude-session"},
		{PID: 20, Kind: "opencode", TmuxSession: "opencode-session"},
		{PID: 30, Kind: "codex", TmuxSession: "codex-session"},
		{PID: 40, Kind: "opencode", TmuxSession: "second-opencode-session"},
	})
	if got := []int{records[0].PID, records[1].PID}; !reflect.DeepEqual(got, []int{20, 40}) {
		t.Fatalf("filtered OpenCode inventory PIDs = %v, want [20 40]", got)
	}
}

func TestGoLocalServicesPublishesGoRuntimeAndChildProcessMetrics(t *testing.T) {
	now := time.Unix(1_700_000_200, 0)
	goroutines := uint64(23)
	threads := uint64(7)
	childCPU := 4.5
	childRSS := uint64(4096)
	payload := (&server{}).goLocalServicesPayloadFromMetrics(now, goProcessMetrics{
		PID: 7222, StartedAt: now, GoroutineCount: &goroutines, ThreadCount: &threads,
		ChildProcesses: []goChildProcessMetric{{PID: 7300, PPID: 7222, Label: "git", State: "running", CPUPercent: &childCPU, RSSBytes: &childRSS}},
	})
	services := payload["services"].([]any)
	service := services[0].(map[string]any)
	details := service["details"].(map[string]any)
	for name, want := range map[string]uint64{"process_count": 2, "goroutine_count": 23, "thread_count": 7} {
		metric := details[name].(map[string]any)
		if metric["state"] != "measured" || metric["value"] != want {
			t.Fatalf("Go runtime metric %q = %#v", name, metric)
		}
	}
	children := details["child_processes"].([]any)
	if len(children) != 1 {
		t.Fatalf("child process details = %#v", children)
	}
	child := children[0].(map[string]any)
	if child["pid"] != 7300 || child["parent_pid"] != 7222 || child["label"] != "git" || child["state"] != "running" {
		t.Fatalf("child process identity = %#v", child)
	}
	if child["cpu_now_percent"].(map[string]any)["value"] != childCPU || child["rss_bytes"].(map[string]any)["value"] != childRSS {
		t.Fatalf("child process resources = %#v", child)
	}
	if totals := payload["totals"].(map[string]any); totals["processes"] != uint64(2) {
		t.Fatalf("local service process total = %#v", totals)
	}
}

func TestGoLocalServicesTotalsExposeMultiCoreProcessCPU(t *testing.T) {
	now := time.Unix(1_700_000_250, 0)
	goCPU, childCPU := 180.0, 44.0
	payload := (&server{}).goLocalServicesPayloadFromMetrics(now, goProcessMetrics{
		PID: 7222, StartedAt: now, CPUPercent: &goCPU,
		ChildProcesses: []goChildProcessMetric{{PID: 7300, PPID: 7222, Label: "synthetic-child", CPUPercent: &childCPU}},
	})
	totals := payload["totals"].(map[string]any)
	if totals["cpu_percent"] != 224.0 {
		t.Fatalf("multi-core process total = %#v, want 224%%", totals["cpu_percent"])
	}
}

func TestLinuxChildProcessStateUsesSyntheticStatRows(t *testing.T) {
	for _, test := range []struct {
		state, want string
	}{
		{state: "R", want: "running"}, {state: "S", want: "sleeping"},
		{state: "T", want: "stopped"}, {state: "Z", want: "zombie"}, {state: "X", want: "dead"},
	} {
		stat := fmt.Sprintf("123 (synthetic child) %s 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22", test.state)
		if got := linuxChildProcessState(stat); got != test.want {
			t.Fatalf("state %q = %q, want %q", test.state, got, test.want)
		}
	}
}

func TestTmuxSignalWatcherStatusDescribesInProcessRoster(t *testing.T) {
	for _, test := range []struct {
		name       string
		sessions   []string
		err        error
		state      string
		healthy    bool
		reasonCode string
		thread     bool
	}{
		{name: "attached", sessions: []string{"yo7222"}, state: "attached", healthy: true, reasonCode: "attached", thread: true},
		{name: "empty", sessions: []string{}, state: "no-sessions", healthy: true, reasonCode: "no_sessions", thread: true},
		{name: "failed", err: errors.New("tmux unavailable"), state: "exited", healthy: false, reasonCode: "tmux_unavailable", thread: false},
	} {
		t.Run(test.name, func(t *testing.T) {
			status := tmuxSignalWatcherStatusFor(test.sessions, test.err)
			if status["state"] != test.state || status["healthy"] != test.healthy || status["reason_code"] != test.reasonCode || status["thread_alive"] != test.thread {
				t.Fatalf("status = %#v", status)
			}
			if status["process_pid"] != 0 || status["demanded"] != true {
				t.Fatalf("in-process identity = %#v", status)
			}
		})
	}
}

func TestTmuxSessionRosterTreatsMissingServerAsEmpty(t *testing.T) {
	t.Setenv("YOLOMUX_TMUX_SOCKET", shortSyntheticTmuxSocket(t))
	sessions, err := (&server{}).tmuxSessionRoster()
	if err != nil {
		t.Fatalf("tmuxSessionRoster() error = %v", err)
	}
	if len(sessions) != 0 {
		t.Fatalf("tmuxSessionRoster() = %#v, want empty", sessions)
	}
}

func TestTmuxRoutePublishesGlobalRosterAndPythonCompatibleCapture(t *testing.T) {
	socket := shortSyntheticTmuxSocket(t)
	root := t.TempDir()
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	for _, session := range []string{"synthetic-tmux-tab-a", "synthetic-tmux-tab-b"} {
		if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", session, "-c", root).CombinedOutput(); err != nil {
			t.Fatalf("create synthetic tmux session %q: %v (%s)", session, err, output)
		}
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	roster := httptest.NewRecorder()
	(&server{}).tmux(roster, httptest.NewRequest(http.MethodGet, "/api/tmux", nil))
	if roster.Code != http.StatusOK {
		t.Fatalf("tmux roster status = %d, body=%s", roster.Code, roster.Body.String())
	}
	var rosterPayload map[string]any
	if err := json.Unmarshal(roster.Body.Bytes(), &rosterPayload); err != nil {
		t.Fatal(err)
	}
	order, ok := rosterPayload["session_order"].([]any)
	if !ok || !reflect.DeepEqual(order, []any{"synthetic-tmux-tab-a", "synthetic-tmux-tab-b"}) {
		t.Fatalf("tmux roster session_order = %#v", rosterPayload["session_order"])
	}
	if rosterPayload["session_scope"] != "all" {
		t.Fatalf("tmux roster session_scope = %#v, want all", rosterPayload["session_scope"])
	}

	if output, err := exec.Command("tmux", "-S", socket, "send-keys", "-t", "=synthetic-tmux-tab-a:", "printf synthetic-capture", "Enter").CombinedOutput(); err != nil {
		t.Fatalf("write synthetic pane output: %v (%s)", err, output)
	}
	capture := httptest.NewRecorder()
	(&server{}).tmux(capture, httptest.NewRequest(http.MethodGet, "/api/tmux?session=synthetic-tmux-tab-a&lines=12", nil))
	if capture.Code != http.StatusOK {
		t.Fatalf("tmux capture status = %d, body=%s", capture.Code, capture.Body.String())
	}
	var capturePayload map[string]any
	if err := json.Unmarshal(capture.Body.Bytes(), &capturePayload); err != nil {
		t.Fatal(err)
	}
	if capturePayload["session"] != "synthetic-tmux-tab-a" || capturePayload["lines"] != float64(12) {
		t.Fatalf("tmux capture identity = %#v", capturePayload)
	}
	if _, ok := capturePayload["text"].(string); !ok {
		t.Fatalf("tmux capture text = %#v", capturePayload["text"])
	}
}

func shortSyntheticTmuxSocket(t *testing.T) string {
	t.Helper()
	directory, err := os.MkdirTemp("/tmp", "yo-go-tmux-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(directory) })
	return filepath.Join(directory, "tmux.sock")
}

func TestDiagnosticsPerformanceCapturesMarkedHTTPRequests(t *testing.T) {
	s := &server{}
	request := httptest.NewRequest(http.MethodGet, "/api/stats-snapshot", nil)
	request.Header.Set("X-YOLOmux-Measurement", "capture-test-marker")
	request.Header.Set("X-YOLOmux-Request-ID", "r-capture-test")
	s.recordPerformance(request, http.StatusOK, 123, 2*time.Millisecond)
	record := httptest.NewRecorder()
	s.diagnosticsPerformance(record, httptest.NewRequest(http.MethodGet, "/api/diagnostics/performance?measurement_scope=capture", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("diagnostics performance status = %d %s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	perf, ok := payload["perf"].(map[string]any)
	if !ok {
		t.Fatalf("diagnostics performance payload = %#v", payload)
	}
	recent, ok := perf["recent"].([]any)
	if !ok || len(recent) != 1 {
		t.Fatalf("diagnostics performance recent = %#v", perf["recent"])
	}
	entry, ok := recent[0].(map[string]any)
	if !ok || entry["surface"] != "GET /api/stats-snapshot" {
		t.Fatalf("diagnostics performance entry = %#v", recent[0])
	}
	details, ok := entry["details"].(map[string]any)
	if !ok || details["transport_request_id"] != "r-capture-test" || details["measurement_request_id"] != "06df7f1729931f89" {
		t.Fatalf("diagnostics performance details = %#v", entry["details"])
	}
}

func TestJSONResponseSetsNoStoreForAPIRepresentations(t *testing.T) {
	record := httptest.NewRecorder()
	jsonResponse(record, http.StatusOK, map[string]any{"ok": true})
	if got := record.Header().Get("Cache-Control"); got != "no-store" {
		t.Fatalf("Cache-Control = %q, want no-store", got)
	}
}

func TestFinderJobContextEnforcesDeadline(t *testing.T) {
	ctx, cancel := newFinderJobContext(context.Background(), time.Millisecond)
	defer cancel()
	select {
	case <-ctx.Done():
		if !errors.Is(ctx.Err(), context.DeadlineExceeded) {
			t.Fatalf("finder job context error = %v, want deadline exceeded", ctx.Err())
		}
	case <-time.After(time.Second):
		t.Fatal("finder job context did not enforce its deadline")
	}
}

func TestUnknownOperationUsesCanonicalPythonError(t *testing.T) {
	s := &server{operations: map[string]*goOperation{}}
	record := httptest.NewRecorder()
	s.operation(record, httptest.NewRequest(http.MethodGet, "/api/operations/missing", nil))
	if record.Code != http.StatusNotFound {
		t.Fatalf("unknown operation status = %d %s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["state"] != "failed" {
		t.Fatalf("unknown operation state = %#v", payload)
	}
	request, _ := payload["request"].(map[string]any)
	if !strings.HasPrefix(fmt.Sprint(request["id"]), "r-") {
		t.Fatalf("unknown operation request = %#v", request)
	}
	errorRecord, _ := payload["error"].(map[string]any)
	message, _ := errorRecord["message"].(map[string]any)
	if errorRecord["code"] != "operation_not_found" || errorRecord["origin"] != "server.http" || errorRecord["retryable"] != false || message["key"] != "common.notFound" {
		t.Fatalf("unknown operation error = %#v", errorRecord)
	}
	details, _ := errorRecord["details"].(map[string]any)
	if details["operation_id"] != "missing" {
		t.Fatalf("unknown operation details = %#v", details)
	}
}

func TestAuthRoleAndNextPathValidationMatchPython(t *testing.T) {
	for _, value := range []string{"//evil.example", "/\\evil.example", "https://evil.example", ""} {
		if got := safeLoginNextValue(value); got != "/" {
			t.Fatalf("unsafe login next %q became %q", value, got)
		}
	}
	if got := safeLoginNextValue("/panel?tab=events"); got != "/panel?tab=events" {
		t.Fatalf("safe login next changed to %q", got)
	}
	if !roleAllows(authUser{role: "readonly"}, "readonly") || !roleAllows(authUser{role: "admin"}, "readonly") || !roleAllows(authUser{role: "admin"}, "admin") {
		t.Fatal("valid authentication roles were rejected")
	}
	if roleAllows(authUser{role: "readonly"}, "admin") || roleAllows(authUser{role: "operator"}, "readonly") {
		t.Fatal("invalid authentication role was admitted")
	}
}

func TestProtectedRoutesUsePythonAuthenticationShapes(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "auth.yaml"), []byte("users:\n  - username: guest\n    password: guest\n    role: readonly\n  - username: admin\n    password: admin\n    role: admin\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "auth-cookie-secret"), []byte(strings.Repeat("11", 32)), 0600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: dir, port: 7221, events: newEventBroker(), children: make(map[*exec.Cmd]struct{})}
	api := s.protected(http.HandlerFunc(s.ping), "readonly")
	unauthorized := httptest.NewRecorder()
	api.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/api/ping", nil))
	var unauthorizedBody map[string]any
	if err := json.Unmarshal(unauthorized.Body.Bytes(), &unauthorizedBody); err != nil {
		t.Fatal(err)
	}
	if unauthorized.Code != http.StatusUnauthorized || unauthorizedBody["login_url"] != "/login?next=%2F" {
		t.Fatalf("unauthorized response = %d %+v", unauthorized.Code, unauthorizedBody)
	}
	message, _ := unauthorizedBody["user_message"].(map[string]any)
	if message["key"] != "auth.error.authenticationRequired" || message["fallback"] != "authentication required" {
		t.Fatalf("unauthorized message = %+v", message)
	}

	html := httptest.NewRecorder()
	htmlRequest := httptest.NewRequest(http.MethodGet, "/", nil)
	htmlRequest.Header.Set("Accept", "text/html")
	api.ServeHTTP(html, htmlRequest)
	if html.Code != http.StatusSeeOther || html.Header().Get("Location") != "/login?next=%2F" {
		t.Fatalf("HTML unauthorized redirect = %d %q", html.Code, html.Header().Get("Location"))
	}

	adminOnly := s.protected(http.HandlerFunc(s.ping), "admin")
	forbidden := httptest.NewRecorder()
	forbiddenRequest := httptest.NewRequest(http.MethodGet, "/api/admin", nil)
	forbiddenRequest.AddCookie(&http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(authUser{username: "guest", password: "guest", role: "readonly"})})
	adminOnly.ServeHTTP(forbidden, forbiddenRequest)
	var forbiddenBody map[string]any
	if err := json.Unmarshal(forbidden.Body.Bytes(), &forbiddenBody); err != nil {
		t.Fatal(err)
	}
	if forbidden.Code != http.StatusForbidden || forbiddenBody["role"] != "readonly" {
		t.Fatalf("forbidden response = %d %+v", forbidden.Code, forbiddenBody)
	}
	forbiddenMessage, _ := forbiddenBody["user_message"].(map[string]any)
	if forbiddenMessage["key"] != "auth.error.accessRequired" || forbiddenMessage["fallback"] != "admin access required" {
		t.Fatalf("forbidden message = %+v", forbiddenMessage)
	}
}

func TestProtectedFinderReadRoutesAllowReadonlyAndMutationsRemainAdminOnly(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: guest\n    password: guest\n    role: readonly\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "auth-cookie-secret"), []byte(strings.Repeat("11", 32)), 0600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: config, port: 7221, events: newEventBroker(), children: make(map[*exec.Cmd]struct{})}
	readonly := http.HandlerFunc(s.ping)
	readRequest := httptest.NewRequest(http.MethodGet, "/api/fs/list?path=/tmp", nil)
	readRequest.SetBasicAuth("guest", "guest")
	readResponse := httptest.NewRecorder()
	s.protected(readonly, "readonly").ServeHTTP(readResponse, readRequest)
	if readResponse.Code != http.StatusOK {
		t.Fatalf("readonly Finder read status = %d %s", readResponse.Code, readResponse.Body.String())
	}

	writeRequest := httptest.NewRequest(http.MethodPost, "/api/fs/write", strings.NewReader(`{"path":"/tmp/new.txt","content":"new"}`))
	writeRequest.SetBasicAuth("guest", "guest")
	writeResponse := httptest.NewRecorder()
	s.protected(readonly, "admin").ServeHTTP(writeResponse, writeRequest)
	if writeResponse.Code != http.StatusForbidden {
		t.Fatalf("readonly Finder write status = %d %s", writeResponse.Code, writeResponse.Body.String())
	}
}

func TestPhase45FinderHTTPRoutesUseReadAndMutationContracts(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("hello\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, finder: service, finderJobs: newFinderJobScheduler()}
	defer s.finderJobs.Close()

	listing := httptest.NewRecorder()
	s.fsList(listing, httptest.NewRequest(http.MethodGet, "/api/fs/list?path="+url.QueryEscape(root), nil))
	if listing.Code != http.StatusOK || !bytes.Contains(listing.Body.Bytes(), []byte(`"entries"`)) || bytes.Contains(listing.Body.Bytes(), []byte(`"operation"`)) {
		t.Fatalf("normal list route = %d %s", listing.Code, listing.Body.String())
	}

	fast := httptest.NewRecorder()
	s.fsFastList(fast, httptest.NewRequest(http.MethodGet, "/api/fs/fast/list?path="+url.QueryEscape(root), nil))
	if fast.Code != http.StatusOK || !bytes.Contains(fast.Body.Bytes(), []byte(`"entries"`)) {
		t.Fatalf("fast list route = %d %s", fast.Code, fast.Body.String())
	}

	read := httptest.NewRecorder()
	s.fsRead(read, httptest.NewRequest(http.MethodGet, "/api/fs/read?path="+url.QueryEscape(filepath.Join(root, "note.md")), nil))
	if read.Code != http.StatusOK || !bytes.Contains(read.Body.Bytes(), []byte(`"content":"hello\n"`)) {
		t.Fatalf("read route = %d %s", read.Code, read.Body.String())
	}

	raw := httptest.NewRecorder()
	rawRequest := httptest.NewRequest(http.MethodGet, "/api/fs/raw?path="+url.QueryEscape(filepath.Join(root, "note.md")), nil)
	rawRequest.Header.Set("Range", "bytes=1-3")
	s.fsRaw(raw, rawRequest)
	if raw.Code != http.StatusPartialContent || raw.Body.String() != "ell" || raw.Header().Get("Content-Range") != "bytes 1-3/6" || raw.Header().Get("Accept-Ranges") != "bytes" {
		t.Fatalf("raw range route = %d headers=%v body=%q", raw.Code, raw.Header(), raw.Body.String())
	}
	invalidRange := httptest.NewRecorder()
	invalidRangeRequest := httptest.NewRequest(http.MethodGet, "/api/fs/raw?path="+url.QueryEscape(filepath.Join(root, "note.md")), nil)
	invalidRangeRequest.Header.Set("Range", "bytes=99-100")
	s.fsRaw(invalidRange, invalidRangeRequest)
	if invalidRange.Code != http.StatusRequestedRangeNotSatisfiable || invalidRange.Header().Get("Content-Range") != "bytes */6" {
		t.Fatalf("invalid raw range = %d headers=%v body=%s", invalidRange.Code, invalidRange.Header(), invalidRange.Body.String())
	}
	emptyPath := filepath.Join(root, "empty.txt")
	if err := os.WriteFile(emptyPath, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	empty := httptest.NewRecorder()
	emptyRequest := httptest.NewRequest(http.MethodGet, "/api/fs/raw?path="+url.QueryEscape(emptyPath), nil)
	emptyRequest.Header.Set("Range", "bytes=0-0")
	s.fsRaw(empty, emptyRequest)
	if empty.Code != http.StatusRequestedRangeNotSatisfiable || empty.Header().Get("Content-Range") != "bytes */0" {
		t.Fatalf("empty raw range = %d headers=%v body=%s", empty.Code, empty.Header(), empty.Body.String())
	}

	write := httptest.NewRecorder()
	s.fsWrite(write, httptest.NewRequest(http.MethodPost, "/api/fs/write", bytes.NewBufferString(`{"path":"`+filepath.Join(root, "new.txt")+`","content":"new"}`)))
	if write.Code != http.StatusOK {
		t.Fatalf("write route = %d %s", write.Code, write.Body.String())
	}
	mkdir := httptest.NewRecorder()
	s.fsMkdir(mkdir, httptest.NewRequest(http.MethodPost, "/api/fs/mkdir", bytes.NewBufferString(`{"path":"`+filepath.Join(root, "folder")+`"}`)))
	if mkdir.Code != http.StatusOK {
		t.Fatalf("mkdir route = %d %s", mkdir.Code, mkdir.Body.String())
	}
	rename := httptest.NewRecorder()
	s.fsRename(rename, httptest.NewRequest(http.MethodPost, "/api/fs/rename", bytes.NewBufferString(`{"path":"`+filepath.Join(root, "new.txt")+`","new_name":"renamed.txt"}`)))
	if rename.Code != http.StatusOK {
		t.Fatalf("rename route = %d %s", rename.Code, rename.Body.String())
	}
	delete := httptest.NewRecorder()
	deleteRequest := httptest.NewRequest(http.MethodPost, "/api/fs/delete", bytes.NewBufferString(`{"path":"`+filepath.Join(root, "renamed.txt")+`"}`))
	s.fsDelete(delete, deleteRequest)
	if delete.Code != http.StatusOK {
		t.Fatalf("delete route = %d %s", delete.Code, delete.Body.String())
	}
}

func TestFastFinderListBypassesBusyFinderWorkers(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "instant.txt"), []byte("fast\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scheduler := newFinderJobScheduler()
	defer scheduler.Close()
	started := make(chan struct{}, finderJobWorkers)
	for index := 0; index < finderJobWorkers; index++ {
		if _, err := scheduler.Submit(context.Background(), fmt.Sprintf("busy-%d", index), func(ctx context.Context) (any, error) {
			started <- struct{}{}
			<-ctx.Done()
			return nil, ctx.Err()
		}); err != nil {
			t.Fatal(err)
		}
	}
	for index := 0; index < finderJobWorkers; index++ {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("Finder workers did not become busy")
		}
	}

	s := &server{root: root, finder: service, finderJobs: scheduler, events: newEventBroker()}
	record := httptest.NewRecorder()
	s.fsFastList(record, httptest.NewRequest(http.MethodGet, "/api/fs/fast/list?path="+url.QueryEscape(root), nil))
	if record.Code != http.StatusOK || !bytes.Contains(record.Body.Bytes(), []byte(`"entries"`)) || bytes.Contains(record.Body.Bytes(), []byte(`"state":"accepted"`)) {
		t.Fatalf("fast list waited for busy Finder workers: status=%d body=%s", record.Code, record.Body.String())
	}
}

func TestPhase45ExpectedMtimeAsyncWriteCompletes(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "note.go")
	if err := os.WriteFile(path, []byte("old\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scheduler := newFinderJobScheduler()
	defer scheduler.Close()
	s := &server{root: root, finder: service, events: newEventBroker(), operations: map[string]*goOperation{}, operationsPath: filepath.Join(root, "ops.json"), operationsJournal: filepath.Join(root, "ops.jsonl"), finderJobs: scheduler}
	info, ferr := service.Info(context.Background(), path)
	if ferr != nil {
		t.Fatal(ferr)
	}
	body := fmt.Sprintf(`{"path":%q,"content":"new\\n","expected_mtime":%d}`, path, info["mtime_ns"])
	record := httptest.NewRecorder()
	s.fsWrite(record, httptest.NewRequest(http.MethodPost, "/api/fs/write", strings.NewReader(body)))
	if record.Code != http.StatusAccepted {
		t.Fatalf("status=%d", record.Code)
	}
	var envelope map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	op := envelope["operation"].(map[string]any)["id"].(string)
	deadline := time.After(2 * time.Second)
	for {
		current := s.operationRecord(op)
		if current != nil && operationIsTerminal(current) {
			if current.State != "ready" {
				t.Fatalf("state=%s result=%#v", current.State, current.Result)
			}
			return
		}
		select {
		case <-deadline:
			t.Fatalf("operation remained pending: %#v", current)
		case <-time.After(5 * time.Millisecond):
		}
	}
}

func TestPhase45MalformedWatchRootsUsesFinderEnvelope(t *testing.T) {
	s := &server{}
	response := httptest.NewRecorder()
	s.watchRoots(response, httptest.NewRequest(http.MethodPost, "/api/watch/roots", strings.NewReader("not-json")))
	if response.Code != http.StatusBadRequest || !bytes.Contains(response.Body.Bytes(), []byte(`"state":"failed"`)) || !bytes.Contains(response.Body.Bytes(), []byte(`"error"`)) {
		t.Fatalf("malformed watch roots response = %d %s", response.Code, response.Body.String())
	}
}

func TestProtectedRejectsUnavailableCapabilityBeforeRouteFallback(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: config, port: 7221, capabilities: capabilities.New("go-test")}
	request := httptest.NewRequest(http.MethodGet, "/api/session-files?session=1", nil)
	request.SetBasicAuth("admin", "secret")
	record := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.unsupported), "readonly").ServeHTTP(record, request)
	if record.Code != http.StatusNotImplemented || !bytes.Contains(record.Body.Bytes(), []byte(`"error":"feature_unavailable"`)) || !bytes.Contains(record.Body.Bytes(), []byte(`"capability":"finder.git.v1"`)) {
		t.Fatalf("unavailable capability response = %d %s", record.Code, record.Body.String())
	}
}

func TestProtectedCapabilityGatesEveryKnownUnimplementedApplicationRoute(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	catalog := capabilities.New("go-test")
	s := &server{config: config, port: 7221, capabilities: catalog}
	cases := map[string]string{
		"/api/update-status":  "core.integrations.v1",
		"/api/watched-prs":    "core.integrations.v1",
		"/api/auto-approve":   "terminal.status.v1",
		"/api/chat/bootstrap": "pane.chat.v1",
	}
	for path, wantCapability := range cases {
		t.Run(path, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, path, nil)
			request.SetBasicAuth("admin", "secret")
			record := httptest.NewRecorder()
			s.protected(http.HandlerFunc(s.unsupported), "readonly").ServeHTTP(record, request)
			if record.Code != http.StatusNotImplemented {
				t.Fatalf("status = %d, want %d; body=%s", record.Code, http.StatusNotImplemented, record.Body.String())
			}
			var body map[string]any
			if err := json.Unmarshal(record.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if body["error"] != "feature_unavailable" || body["capability"] != wantCapability {
				t.Fatalf("unimplemented route envelope = %#v, want capability %q", body, wantCapability)
			}
		})
	}
}

func TestSettingsRoutePersistsSharedSchemaAndUpdatesGoOwnedSettings(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("general:\n  startup_tips: true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	root, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for range 5 {
		if _, statErr := os.Stat(filepath.Join(root, "yolomux_lib")); statErr == nil {
			break
		}
		root = filepath.Dir(root)
	}
	finderRoot := t.TempDir()
	service, err := finder.NewWithLimits(finderRoot, nil, finder.UploadLimits{MaxBytes: 4, MaxFiles: 1, FilenameTemplate: "{name}-{seq}{ext}"})
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, config: config, finder: service}

	get := httptest.NewRecorder()
	s.settings(get, httptest.NewRequest(http.MethodGet, "/api/settings", nil))
	if get.Code != http.StatusOK {
		t.Fatalf("GET /api/settings status = %d, body=%s", get.Code, get.Body.String())
	}
	var initial map[string]any
	if err := json.Unmarshal(get.Body.Bytes(), &initial); err != nil {
		t.Fatal(err)
	}
	if got := initial["settings"].(map[string]any)["general"].(map[string]any)["startup_tips"]; got != true {
		t.Fatalf("initial startup_tips = %#v, want true", got)
	}
	initialSettings := initial["settings"].(map[string]any)
	catalog, ok := initial["catalog"].(map[string]any)
	if !ok || len(catalog) == 0 {
		t.Fatalf("settings catalog is missing: %#v", initial["catalog"])
	}
	defaults, ok := initial["defaults"].(map[string]any)
	if !ok {
		t.Fatalf("settings defaults are missing: %#v", initial["defaults"])
	}
	defaultPaths := make(map[string]struct{})
	for sectionName, rawValues := range defaults {
		values, sectionOK := rawValues.(map[string]any)
		if !sectionOK {
			t.Fatalf("settings defaults section %q is not an object: %#v", sectionName, rawValues)
		}
		for key := range values {
			if strings.HasPrefix(key, "_") {
				continue
			}
			defaultPaths[sectionName+"."+key] = struct{}{}
		}
	}
	if len(catalog) != len(defaultPaths) {
		t.Fatalf("settings catalog has %d paths, defaults have %d paths", len(catalog), len(defaultPaths))
	}
	for path := range defaultPaths {
		if _, present := catalog[path]; !present {
			t.Fatalf("settings catalog is missing default path %q", path)
		}
	}
	allSettingsBody, err := json.Marshal(map[string]any{"settings": initialSettings})
	if err != nil {
		t.Fatal(err)
	}
	allSettingsRequest := httptest.NewRequest(http.MethodPost, "/api/settings", strings.NewReader(string(allSettingsBody)))
	allSettingsRequest = allSettingsRequest.WithContext(context.WithValue(allSettingsRequest.Context(), authContextKey{}, authUser{role: "admin"}))
	allSettingsResponse := httptest.NewRecorder()
	s.settings(allSettingsResponse, allSettingsRequest)
	if allSettingsResponse.Code != http.StatusOK {
		t.Fatalf("complete settings POST status = %d, body=%s", allSettingsResponse.Code, allSettingsResponse.Body.String())
	}
	var allSettingsPayload map[string]any
	if err := json.Unmarshal(allSettingsResponse.Body.Bytes(), &allSettingsPayload); err != nil {
		t.Fatal(err)
	}
	returnedSettings, ok := allSettingsPayload["settings"].(map[string]any)
	if !ok {
		t.Fatalf("complete settings response has no settings object: %#v", allSettingsPayload)
	}
	for path := range catalog {
		parts := strings.SplitN(path, ".", 2)
		if len(parts) != 2 {
			t.Fatalf("catalog path is not section.key: %q", path)
		}
		section, sectionOK := returnedSettings[parts[0]].(map[string]any)
		if !sectionOK {
			t.Fatalf("complete settings response is missing section %q for %q", parts[0], path)
		}
		if _, present := section[parts[1]]; !present {
			t.Fatalf("complete settings response is missing catalog path %q", path)
		}
	}

	request := httptest.NewRequest(http.MethodPost, "/api/settings", strings.NewReader(`{"settings":{"general":{"startup_tips":false,"language":"ja"},"appearance":{"theme":"light","active_color":"blue","ui_font_size":16},"file_explorer":{"indexed_dirs":["/tmp"]},"uploads":{"max_bytes":1048576,"filename_template":"{name}-{seq:03d}{ext}"}}}`))
	request = request.WithContext(context.WithValue(request.Context(), authContextKey{}, authUser{role: "admin"}))
	post := httptest.NewRecorder()
	s.settings(post, request)
	if post.Code != http.StatusOK {
		t.Fatalf("POST /api/settings status = %d, body=%s", post.Code, post.Body.String())
	}
	var saved map[string]any
	if err := json.Unmarshal(post.Body.Bytes(), &saved); err != nil {
		t.Fatal(err)
	}
	if got := saved["settings"].(map[string]any)["general"].(map[string]any)["startup_tips"]; got != false {
		t.Fatalf("saved startup_tips = %#v, want false", got)
	}
	if got := saved["settings"].(map[string]any)["general"].(map[string]any)["language"]; got != "ja" {
		t.Fatalf("saved language = %#v, want ja", got)
	}
	appearance := saved["settings"].(map[string]any)["appearance"].(map[string]any)
	if appearance["theme"] != "light" || appearance["active_color"] != "blue" || appearance["ui_font_size"] != float64(16) {
		t.Fatalf("saved appearance = %#v", appearance)
	}
	if got := saved["settings"].(map[string]any)["file_explorer"].(map[string]any)["indexed_dirs"]; !reflect.DeepEqual(got, []any{"/tmp"}) {
		t.Fatalf("saved indexed_dirs = %#v, want [/tmp]", got)
	}
	if got := saved["settings"].(map[string]any)["uploads"].(map[string]any)["max_bytes"]; got != float64(1048576) {
		t.Fatalf("saved upload max_bytes = %#v, want 1048576", got)
	}
	coercionRequest := httptest.NewRequest(http.MethodPost, "/api/settings", strings.NewReader(`{"settings":{"appearance":{"ui_font_size":999}}}`))
	coercionRequest = coercionRequest.WithContext(context.WithValue(coercionRequest.Context(), authContextKey{}, authUser{role: "admin"}))
	coercionResponse := httptest.NewRecorder()
	s.settings(coercionResponse, coercionRequest)
	if coercionResponse.Code != http.StatusOK {
		t.Fatalf("coerced settings POST status = %d, body=%s", coercionResponse.Code, coercionResponse.Body.String())
	}
	var coercionPayload map[string]any
	if err := json.Unmarshal(coercionResponse.Body.Bytes(), &coercionPayload); err != nil {
		t.Fatal(err)
	}
	if got := coercionPayload["settings"].(map[string]any)["appearance"].(map[string]any)["ui_font_size"]; got != float64(30) {
		t.Fatalf("coerced ui_font_size = %#v, want 30", got)
	}
	if coerced, ok := coercionPayload["coerced"].([]any); !ok || len(coerced) == 0 {
		t.Fatalf("coercion metadata = %#v, want a non-empty list", coercionPayload["coerced"])
	}
	uploadPayload, finderErr := service.Upload(context.Background(), "session", "user", "", finderRoot, []finder.UploadFile{{Name: "payload.txt", Data: []byte("more than four bytes")}})
	if finderErr != nil {
		t.Fatalf("updated Finder upload = %#v, error = %v", uploadPayload, finderErr)
	}
	uploaded := uploadPayload["files"].([]any)[0].(map[string]any)
	if uploaded["saved_name"] != "payload-001.txt" {
		t.Fatalf("updated Finder filename = %#v, want payload-001.txt", uploaded)
	}

	fresh := httptest.NewRecorder()
	s.settings(fresh, httptest.NewRequest(http.MethodGet, "/api/settings", nil))
	if fresh.Code != http.StatusOK {
		t.Fatalf("fresh GET /api/settings status = %d, body=%s", fresh.Code, fresh.Body.String())
	}
	var reloaded map[string]any
	if err := json.Unmarshal(fresh.Body.Bytes(), &reloaded); err != nil {
		t.Fatal(err)
	}
	if got := reloaded["settings"].(map[string]any)["general"].(map[string]any)["startup_tips"]; got != false {
		t.Fatalf("reloaded startup_tips = %#v, want false", got)
	}
	reloadedSettings := reloaded["settings"].(map[string]any)
	if reloadedSettings["appearance"].(map[string]any)["theme"] != "light" || reloadedSettings["general"].(map[string]any)["language"] != "ja" {
		t.Fatalf("reloaded representative settings = %#v", reloadedSettings)
	}
	malformedConfig := t.TempDir()
	if err := os.WriteFile(filepath.Join(malformedConfig, "settings.yaml"), []byte("general: [unterminated\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	malformed := httptest.NewRecorder()
	(&server{root: root, config: malformedConfig}).settings(malformed, httptest.NewRequest(http.MethodGet, "/api/settings", nil))
	if malformed.Code != http.StatusServiceUnavailable || !bytes.Contains(malformed.Body.Bytes(), []byte(`"code":"settings_file_malformed"`)) {
		t.Fatalf("malformed settings response = %d %s", malformed.Code, malformed.Body.String())
	}
}

func TestSettingsRouteRejectsReadonlyMutation(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: readonly\n    password: readonly\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	catalog := capabilities.New("go-test")
	catalog.Set("core.settings.v1", capabilities.Entry{Available: true})
	s := &server{root: "..", config: config, port: 7222, capabilities: catalog}
	request := httptest.NewRequest(http.MethodPost, "/api/settings", strings.NewReader(`{"settings":{"general":{"startup_tips":false}}}`))
	request.SetBasicAuth("readonly", "readonly")
	response := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.settings), "readonly").ServeHTTP(response, request)
	if response.Code != http.StatusForbidden {
		t.Fatalf("readonly settings mutation status = %d, body=%s", response.Code, response.Body.String())
	}
}

func TestActivityRouteIncludesEveryGlobalTmuxSession(t *testing.T) {
	socketDir := t.TempDir()
	socket := filepath.Join(socketDir, "tmux.sock")
	root := t.TempDir()
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	for _, session := range []string{"synthetic-activity-a", "synthetic-activity-b"} {
		if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", session, "-c", root).CombinedOutput(); err != nil {
			t.Fatalf("create synthetic tmux session %q: %v (%s)", session, err, output)
		}
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	record := httptest.NewRecorder()
	(&server{}).activity(record, httptest.NewRequest(http.MethodGet, "/api/activity?hours=48&visible=0&scope=all", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("activity status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	order, ok := payload["session_order"].([]any)
	if !ok || len(order) != 2 || order[0] != "synthetic-activity-a" || order[1] != "synthetic-activity-b" {
		t.Fatalf("activity session_order = %#v", payload["session_order"])
	}
	if _, ok := payload["activity"].(map[string]any); !ok {
		t.Fatalf("activity payload missing activity map: %#v", payload["activity"])
	}
	if got := payload["session_file_hours"]; got != float64(48) {
		t.Fatalf("activity session_file_hours = %#v, want 48", got)
	}
	if got := payload["session_scope"]; got != "all" {
		t.Fatalf("activity session_scope = %#v, want all", got)
	}
	metadata, ok := payload["sessions"].(map[string]any)
	if !ok || len(metadata) != 2 {
		t.Fatalf("activity payload sessions = %#v", payload["sessions"])
	}
	for _, session := range []string{"synthetic-activity-a", "synthetic-activity-b"} {
		info, ok := metadata[session].(map[string]any)
		if !ok {
			t.Fatalf("activity payload session %q = %#v", session, metadata[session])
		}
		if info["session"] != session || info["label"] != session {
			t.Fatalf("activity payload session %q identity = %#v", session, map[string]any{"session": info["session"], "label": info["label"]})
		}
		panes, ok := info["panes"].([]any)
		if !ok || len(panes) != 1 {
			t.Fatalf("activity payload session %q panes = %#v", session, info["panes"])
		}
	}
}

func TestLiveSessionMetadataPublishesWorkingAgentState(t *testing.T) {
	var classified stats.AgentStatusRequest
	s := &server{
		agentStatusClassifier: func(_ context.Context, request stats.AgentStatusRequest) stats.AgentStatusResult {
			classified = request
			return stats.AgentStatusResult{Status: stats.AgentStatusRun}
		},
	}
	output := []byte("synthetic-codex\t0\tmain\t0\t%42\t/tmp/synthetic\tcodex\tCodex\t42\t1\t1\n")
	info := s.liveSessionMetadataFromOutput("synthetic-codex", output, nil)
	agents, ok := info["agents"].([]map[string]any)
	if !ok || len(agents) != 1 {
		t.Fatalf("metadata agents = %#v", info["agents"])
	}
	if got := agents[0]["status"]; got != "working" {
		t.Fatalf("metadata agent status = %#v, want working", got)
	}
	if classified.Kind != "codex" || classified.PaneTarget != "%42" || classified.PID != 42 {
		t.Fatalf("classifier request = %+v", classified)
	}
}

func TestLiveSessionMetadataClassifiesNodeCodexWrapperAsWorking(t *testing.T) {
	var classified stats.AgentStatusRequest
	s := &server{
		processEntriesFn: func() ([]procEntry, error) {
			return []procEntry{{PID: 42, PPID: 10, Command: "node /home/test/.local/bin/codex --resume thread", Executable: "/usr/bin/node"}}, nil
		},
		agentStatusClassifier: func(_ context.Context, request stats.AgentStatusRequest) stats.AgentStatusResult {
			classified = request
			if request.Executable != "/usr/bin/node" {
				return stats.AgentStatusResult{Status: stats.AgentStatusUnavailable, Reason: "missing-wrapper-executable"}
			}
			return stats.AgentStatusResult{Status: stats.AgentStatusRun}
		},
	}
	output := []byte("synthetic-codex\t0\tmain\t0\t%42\t/tmp/synthetic\tnode\tCodex\t10\t1\t1\n")
	info := s.liveSessionMetadataFromOutput("synthetic-codex", output, nil)
	agents, ok := info["agents"].([]map[string]any)
	if !ok || len(agents) != 1 {
		t.Fatalf("metadata agents = %#v", info["agents"])
	}
	if got := agents[0]["status"]; got != "working" {
		t.Fatalf("wrapped Node Codex status = %#v, want working", got)
	}
	if classified.Kind != "codex" || classified.PID != 42 || classified.Executable != "/usr/bin/node" {
		t.Fatalf("wrapped Node Codex classifier request = %+v", classified)
	}
}

func TestActivityRouteKeepsRosterWhenActivityReadFails(t *testing.T) {
	socketDir := t.TempDir()
	socket := filepath.Join(socketDir, "tmux.sock")
	root := t.TempDir()
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	for _, session := range []string{"synthetic-activity-roster-a", "synthetic-activity-roster-b"} {
		if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", session, "-c", root).CombinedOutput(); err != nil {
			t.Fatalf("create synthetic tmux session %q: %v (%s)", session, err, output)
		}
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	s := &server{tmuxSessionActivityFn: func() (map[string]float64, error) {
		return nil, errors.New("synthetic activity read failed")
	}}
	record := httptest.NewRecorder()
	s.activity(record, httptest.NewRequest(http.MethodGet, "/api/activity?scope=all", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("activity status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	order, ok := payload["session_order"].([]any)
	if !ok || len(order) != 2 {
		t.Fatalf("activity roster disappeared after activity read failure: %#v", payload["session_order"])
	}
	errorsPayload, ok := payload["errors"].([]any)
	if !ok || len(errorsPayload) != 1 || errorsPayload[0] != "synthetic activity read failed" {
		t.Fatalf("activity read diagnostic = %#v", payload["errors"])
	}
}

func TestActivityRoutePublishesPerWindowTmuxRecency(t *testing.T) {
	s := &server{
		tmuxSessionRosterFn: func() ([]string, error) { return []string{"synthetic-window-activity"}, nil },
		tmuxSessionActivityFn: func() (map[string]float64, error) {
			return map[string]float64{"synthetic-window-activity": 100}, nil
		},
		tmuxWindowActivityFn: func() (map[string]float64, error) {
			return map[string]float64{"synthetic-window-activity:0": 125}, nil
		},
	}
	record := httptest.NewRecorder()
	s.activity(record, httptest.NewRequest(http.MethodGet, "/api/activity?scope=all", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("activity status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	activity, ok := payload["activity"].(map[string]any)
	if !ok {
		t.Fatalf("activity map = %#v", payload["activity"])
	}
	window, ok := activity["synthetic-window-activity:0"].(map[string]any)
	if !ok || window["active_recency_ts"] != float64(125) || window["last_output_ts"] != float64(125) {
		t.Fatalf("window recency = %#v", activity["synthetic-window-activity:0"])
	}
}

func TestSessionMetadataDoesNotClearRosterOnInitialDiscoveryFailure(t *testing.T) {
	s := &server{tmuxSessionRosterFn: func() ([]string, error) {
		return nil, errors.New("synthetic initial tmux roster failure")
	}}
	record := httptest.NewRecorder()
	s.sessionMetadata(record, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
	if record.Code != http.StatusServiceUnavailable {
		t.Fatalf("initial metadata failure status = %d, body=%s", record.Code, record.Body.String())
	}
	if !bytes.Contains(record.Body.Bytes(), []byte(`"status":"unavailable"`)) || !bytes.Contains(record.Body.Bytes(), []byte("synthetic initial tmux roster failure")) {
		t.Fatalf("initial metadata failure payload = %s", record.Body.String())
	}
}

func TestSessionMetadataKeepsRosterAndReportsPaneInventoryFailure(t *testing.T) {
	t.Setenv("YOLOMUX_TMUX_SOCKET", filepath.Join(t.TempDir(), "missing-tmux.sock"))
	s := &server{tmuxSessionRosterFn: func() ([]string, error) {
		return []string{"synthetic-pane-failure"}, nil
	}}
	record := httptest.NewRecorder()
	s.sessionMetadata(record, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("pane inventory failure status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	order, ok := payload["session_order"].([]any)
	if !ok || len(order) != 1 || order[0] != "synthetic-pane-failure" {
		t.Fatalf("pane inventory failure cleared roster: %#v", payload["session_order"])
	}
	errorsPayload, ok := payload["errors"].([]any)
	if !ok || len(errorsPayload) == 0 || strings.TrimSpace(fmt.Sprint(errorsPayload[0])) == "" {
		t.Fatalf("pane inventory failure diagnostic = %#v", payload["errors"])
	}
}

func TestTmuxSignalsRouteIncludesGlobalWindowsAndPanes(t *testing.T) {
	socketDir := t.TempDir()
	socket := filepath.Join(socketDir, "tmux.sock")
	root := t.TempDir()
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	for _, session := range []string{"synthetic-signal-a", "synthetic-signal-b"} {
		if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", session, "-c", root).CombinedOutput(); err != nil {
			t.Fatalf("create synthetic tmux session %q: %v (%s)", session, err, output)
		}
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	record := httptest.NewRecorder()
	(&server{}).tmuxSignals(record, httptest.NewRequest(http.MethodGet, "/api/tmux-signals?force=1", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("tmux signals status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["ok"] != true {
		t.Fatalf("tmux signals payload not ok: %#v", payload)
	}
	windows, ok := payload["windows"].([]any)
	if !ok || len(windows) != 2 {
		t.Fatalf("tmux signals windows = %#v", payload["windows"])
	}
	for _, raw := range windows {
		window, ok := raw.(map[string]any)
		if !ok || window["key"] == "" || window["session"] == "" {
			t.Fatalf("tmux signal window = %#v", raw)
		}
		for _, field := range []string{"activity_ts", "activity_flag", "bell_flag", "silence_flag", "active_client_details", "authoritative_client"} {
			if _, present := window[field]; !present {
				t.Fatalf("tmux signal window missing Python-compatible field %q: %#v", field, window)
			}
		}
		panes, ok := window["panes"].([]any)
		if !ok || len(panes) != 1 {
			t.Fatalf("tmux signal panes = %#v", window["panes"])
		}
	}
	filtered := httptest.NewRecorder()
	(&server{}).tmuxSignals(filtered, httptest.NewRequest(http.MethodGet, "/api/tmux-signals?session=synthetic-signal-b", nil))
	if filtered.Code != http.StatusOK {
		t.Fatalf("filtered tmux signals status = %d, body=%s", filtered.Code, filtered.Body.String())
	}
	var filteredPayload map[string]any
	if err := json.Unmarshal(filtered.Body.Bytes(), &filteredPayload); err != nil {
		t.Fatal(err)
	}
	filteredWindows, ok := filteredPayload["windows"].([]any)
	if !ok || len(filteredWindows) != 1 {
		t.Fatalf("filtered tmux signal windows = %#v", filteredPayload["windows"])
	}
}

func TestTmuxSignalsFilteredSessionUsesExactTmuxTarget(t *testing.T) {
	socketDir := t.TempDir()
	socket := filepath.Join(socketDir, "tmux.sock")
	root := t.TempDir()
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	for _, session := range []string{"1", "12"} {
		if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", session, "-c", root).CombinedOutput(); err != nil {
			t.Fatalf("create synthetic tmux session %q: %v (%s)", session, err, output)
		}
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	record := httptest.NewRecorder()
	(&server{}).tmuxSignals(record, httptest.NewRequest(http.MethodGet, "/api/tmux-signals?session=1", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("exact tmux signal status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	order, ok := payload["session_order"].([]any)
	if !ok || len(order) != 1 || order[0] != "1" {
		t.Fatalf("filtered tmux signal roster = %#v, want [1]", payload["session_order"])
	}
}

func TestTmuxWindowRouteSelectsExactWindow(t *testing.T) {
	socketDir := t.TempDir()
	socket := filepath.Join(socketDir, "tmux.sock")
	root := t.TempDir()
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", "1", "-c", root).CombinedOutput(); err != nil {
		t.Fatalf("create tmux session: %v (%s)", err, output)
	}
	if output, err := exec.Command("tmux", "-S", socket, "new-window", "-d", "-t", "=1:", "-n", "bash", "-c", root, "sleep", "60").CombinedOutput(); err != nil {
		t.Fatalf("create tmux window: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	server := &server{}
	record := httptest.NewRecorder()
	server.tmuxWindow(record, httptest.NewRequest(http.MethodPost, "/api/tmux-window?session=1&window=1", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("tmux window status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["ok"] != true || payload["session"] != "1" || payload["window"] != "1" {
		t.Fatalf("tmux window payload = %#v", payload)
	}
	selected, err := exec.Command("tmux", "-S", socket, "display-message", "-p", "-t", "=1:", "#{window_index}").Output()
	if err != nil {
		t.Fatalf("read selected tmux window: %v", err)
	}
	if strings.TrimSpace(string(selected)) != "1" {
		t.Fatalf("selected tmux window = %q, want 1", strings.TrimSpace(string(selected)))
	}

	badWindow := httptest.NewRecorder()
	server.tmuxWindow(badWindow, httptest.NewRequest(http.MethodPost, "/api/tmux-window?session=1&window=bad", nil))
	if badWindow.Code != http.StatusBadRequest {
		t.Fatalf("invalid tmux window status = %d, body=%s", badWindow.Code, badWindow.Body.String())
	}
}

func TestTmuxSessionSortKeyMatchesPythonRosterOrdering(t *testing.T) {
	got := []string{"zeta", "project10", "yolomux10", "project2", "yolomux2", "Alpha"}
	sort.SliceStable(got, func(i, j int) bool {
		left := tmuxSessionSortKey(got[i])
		right := tmuxSessionSortKey(got[j])
		if left.rank != right.rank {
			return left.rank < right.rank
		}
		if left.group != right.group {
			return left.group < right.group
		}
		return left.number < right.number
	})
	want := []string{"yolomux2", "yolomux10", "project2", "project10", "Alpha", "zeta"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("tmux session order = %#v, want %#v", got, want)
	}
}

func TestTmuxSessionRosterNormalizesInjectedAndCommandRows(t *testing.T) {
	s := &server{tmuxSessionRosterFn: func() ([]string, error) {
		return []string{" project10 ", "yolomux10", "project2", "", "project2", " yolomux2"}, nil
	}}
	got, err := s.tmuxSessionRoster()
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"yolomux2", "yolomux10", "project2", "project10"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("normalized tmux roster = %#v, want %#v", got, want)
	}
	cached, known := s.cachedTmuxSessionRoster()
	if !known || !reflect.DeepEqual(cached, want) {
		t.Fatalf("cached normalized tmux roster = %#v (known=%v), want %#v", cached, known, want)
	}
}

func TestActivityHoursMatchesPythonBounds(t *testing.T) {
	for _, test := range []struct {
		raw  string
		want float64
	}{
		{raw: "", want: 24},
		{raw: "48", want: 48},
		{raw: "99999", want: maxActivityHours},
	} {
		got, err := activityHours(test.raw)
		if err != nil || got != test.want {
			t.Fatalf("activityHours(%q) = %v, %v; want %v", test.raw, got, err, test.want)
		}
	}
	for _, raw := range []string{"-1", "NaN", "+Inf", "not-a-number"} {
		if _, err := activityHours(raw); err == nil {
			t.Fatalf("activityHours(%q) accepted invalid value", raw)
		}
	}
}

func TestSessionMetadataStartupRouteUsesAvailableBootstrapCapability(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	catalog := capabilities.New("go-test")
	s := &server{config: config, port: 7221, capabilities: catalog}
	request := httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil)
	request.SetBasicAuth("admin", "secret")
	record := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.sessionMetadata), "readonly").ServeHTTP(record, request)
	if record.Code != http.StatusOK {
		t.Fatalf("session metadata status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if _, ok := payload["metadata_identity"]; !ok {
		t.Fatalf("session metadata payload lacks identity: %#v", payload)
	}
	if got := capabilities.RouteCapability(http.MethodGet, "/api/session-metadata"); got != "core.bootstrap.v1" {
		t.Fatalf("session metadata capability = %q, want core.bootstrap.v1", got)
	}
}

func TestGoRootAdvertisesTheFullCapabilityGatedApplicationShell(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	catalog := capabilities.New("go-test")
	catalog.Set("pane.stats.v1", capabilities.Entry{Available: true})
	s := &server{root: testsupport.RepoRoot(t), config: config, port: 7221, capabilities: catalog}
	page := httptest.NewRecorder()
	s.index(page, httptest.NewRequest(http.MethodGet, "/", nil))
	if page.Code != http.StatusOK {
		t.Fatalf("full root status = %d, body=%s", page.Code, page.Body.String())
	}
	if !bytes.Contains(page.Body.Bytes(), []byte(`"versionStatus":"0.9.3`)) {
		t.Fatalf("Go root does not preserve the release version label: %s", page.Body.String())
	}
	if !bytes.Contains(page.Body.Bytes(), []byte(`"backendLabel":"Go `)) {
		t.Fatalf("Go root does not provide its backend label for the version popup: %s", page.Body.String())
	}
	wantBackendLabel := fmt.Sprintf(`"backendLabel":%q`, goRuntimeLabel())
	if !bytes.Contains(page.Body.Bytes(), []byte(wantBackendLabel)) {
		t.Fatalf("Go root backend label = %s, want %s: %s", goRuntimeLabel(), wantBackendLabel, page.Body.String())
	}
	if !bytes.Contains(page.Body.Bytes(), []byte(` [Go `)) {
		t.Fatalf("Go root version popup does not use the one-line backend format: %s", page.Body.String())
	}
	brandVersionStart := bytes.Index(page.Body.Bytes(), []byte(`<span class="brand-version"`))
	if brandVersionStart >= 0 {
		brandVersionTextStart := bytes.Index(page.Body.Bytes()[brandVersionStart:], []byte(`>`))
		brandVersionEnd := bytes.Index(page.Body.Bytes()[brandVersionStart:], []byte(`</span>`))
		if brandVersionTextStart >= 0 && brandVersionEnd > brandVersionTextStart && bytes.Contains(page.Body.Bytes()[brandVersionStart+brandVersionTextStart:brandVersionStart+brandVersionEnd], []byte(`[Go `)) {
			t.Fatalf("Go backend label leaked into the visible version text")
		}
	}
	const bootstrapPrefix = `<script id="yolomux-bootstrap" type="application/json">`
	start := bytes.Index(page.Body.Bytes(), []byte(bootstrapPrefix))
	if start < 0 {
		t.Fatal("root page omitted the bootstrap payload")
	}
	start += len(bootstrapPrefix)
	end := bytes.Index(page.Body.Bytes()[start:], []byte("</script>"))
	if end < 0 {
		t.Fatal("root page bootstrap payload has no closing script tag")
	}
	var bootstrap map[string]any
	if err := json.Unmarshal(page.Body.Bytes()[start:start+end], &bootstrap); err != nil {
		t.Fatalf("decode root bootstrap: %v", err)
	}
	if bootstrap["statsOnly"] != false {
		t.Fatalf("root bootstrap statsOnly = %#v, want false", bootstrap["statsOnly"])
	}
	statsPage := httptest.NewRecorder()
	s.index(statsPage, httptest.NewRequest(http.MethodGet, "/?stats_only=1", nil))
	if statsPage.Code != http.StatusOK || !bytes.Contains(statsPage.Body.Bytes(), []byte(`"statsOnly":true`)) {
		t.Fatalf("explicit stats-only root = %d, body=%s", statsPage.Code, statsPage.Body.String())
	}
	dangerous := &server{root: testsupport.RepoRoot(t), config: config, port: 7221, dangerouslyYolo: true, capabilities: catalog}
	dangerousPage := httptest.NewRecorder()
	dangerous.index(dangerousPage, httptest.NewRequest(http.MethodGet, "/", nil))
	if dangerousPage.Code != http.StatusOK || !bytes.Contains(dangerousPage.Body.Bytes(), []byte(`"dangerouslyYolo":true`)) || !bytes.Contains(dangerousPage.Body.Bytes(), []byte(`--dangerously-bypass-approvals-and-sandbox`)) {
		t.Fatalf("Go dangerous bootstrap = %d, body=%s", dangerousPage.Code, dangerousPage.Body.String())
	}
	capabilityDocument, ok := bootstrap["capabilities"].(map[string]any)
	if !ok {
		t.Fatalf("root bootstrap capabilities = %#v", bootstrap["capabilities"])
	}
	capabilityEntries, ok := capabilityDocument["capabilities"].(map[string]any)
	if !ok {
		t.Fatalf("root bootstrap capability entries = %#v", capabilityDocument["capabilities"])
	}
	statsEntry, ok := capabilityEntries["pane.stats.v1"].(map[string]any)
	if !ok || statsEntry["available"] != true {
		t.Fatalf("root bootstrap stats capability = %#v", capabilityEntries["pane.stats.v1"])
	}
	tabberEntry, ok := capabilityEntries["pane.tabber.v1"].(map[string]any)
	if !ok || tabberEntry["available"] != true {
		t.Fatalf("root bootstrap Tabber capability = %#v", capabilityEntries["pane.tabber.v1"])
	}

	request := httptest.NewRequest(http.MethodGet, "/api/activity", nil)
	request.SetBasicAuth("admin", "secret")
	response := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.activity), "readonly").ServeHTTP(response, request)
	if response.Code != http.StatusOK || !bytes.Contains(response.Body.Bytes(), []byte(`"session_order"`)) {
		t.Fatalf("Tabber activity route = %d %s", response.Code, response.Body.String())
	}
}

func TestProtectedAdmitsStatsRoutesWhileMaterializationIsPending(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	catalog := capabilities.New("go-test")
	catalog.Set("pane.stats.v1", capabilities.Entry{Available: true, Degraded: true, Reason: &capabilities.Reason{Code: "stats_pending", MessageKey: "capability.statsPending", MessageParams: map[string]any{}}})
	s := &server{config: config, port: 7221, capabilities: catalog}
	request := httptest.NewRequest(http.MethodGet, "/api/stats-capabilities", nil)
	request.SetBasicAuth("admin", "secret")
	record := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.statsCapabilities), "readonly").ServeHTTP(record, request)
	if record.Code != http.StatusOK || bytes.Contains(record.Body.Bytes(), []byte(`"error":"feature_unavailable"`)) {
		t.Fatalf("pending stats capability response = %d %s", record.Code, record.Body.String())
	}
}

func TestUploadUsesConfiguredLimitAndRejectsChunkedBodies(t *testing.T) {
	config := t.TempDir()
	previousFileLimit, fileLimitPresent := os.LookupEnv("YOLOMUX_FILE_TRANSFER_MAX_BYTES")
	previousUploadLimit, uploadLimitPresent := os.LookupEnv("YOLOMUX_UPLOAD_MAX_BYTES")
	previousTemplate, templatePresent := os.LookupEnv("YOLOMUX_UPLOAD_FILENAME_TEMPLATE")
	_ = os.Unsetenv("YOLOMUX_FILE_TRANSFER_MAX_BYTES")
	_ = os.Unsetenv("YOLOMUX_UPLOAD_MAX_BYTES")
	_ = os.Unsetenv("YOLOMUX_UPLOAD_FILENAME_TEMPLATE")
	t.Cleanup(func() {
		if fileLimitPresent {
			_ = os.Setenv("YOLOMUX_FILE_TRANSFER_MAX_BYTES", previousFileLimit)
		} else {
			_ = os.Unsetenv("YOLOMUX_FILE_TRANSFER_MAX_BYTES")
		}
		if uploadLimitPresent {
			_ = os.Setenv("YOLOMUX_UPLOAD_MAX_BYTES", previousUploadLimit)
		} else {
			_ = os.Unsetenv("YOLOMUX_UPLOAD_MAX_BYTES")
		}
		if templatePresent {
			_ = os.Setenv("YOLOMUX_UPLOAD_FILENAME_TEMPLATE", previousTemplate)
		} else {
			_ = os.Unsetenv("YOLOMUX_UPLOAD_FILENAME_TEMPLATE")
		}
	})
	if got := (&server{config: t.TempDir()}).fileTransferMaxBytes(); got != 300*1024*1024 {
		t.Fatalf("default transfer limit = %d, want %d", got, 300*1024*1024)
	}
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("uploads:\n  max_bytes: 1048576\n  filename_template: '{name}-{seq:03d}{ext}'\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: config}
	if got := s.fileTransferMaxBytes(); got != 1048576 {
		t.Fatalf("configured transfer limit = %d", got)
	}
	if got := s.fileTransferFilenameTemplate(); got != "{name}-{seq:03d}{ext}" {
		t.Fatalf("configured filename template = %q", got)
	}
	legacyConfig := t.TempDir()
	if err := os.WriteFile(filepath.Join(legacyConfig, "settings.yaml"), []byte("uploads:\n  max_bytes: 20971520\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := (&server{config: legacyConfig}).fileTransferMaxBytes(); got != 300*1024*1024 {
		t.Fatalf("legacy transfer limit = %d, want %d", got, 300*1024*1024)
	}
	t.Setenv("YOLOMUX_UPLOAD_MAX_BYTES", "4194304")
	if got := (&server{config: t.TempDir()}).fileTransferMaxBytes(); got != 4194304 {
		t.Fatalf("fallback environment transfer limit = %d, want %d", got, 4194304)
	}
	t.Setenv("YOLOMUX_FILE_TRANSFER_MAX_BYTES", "8388608")
	if got := (&server{config: t.TempDir()}).fileTransferMaxBytes(); got != 8388608 {
		t.Fatalf("primary environment transfer limit = %d, want %d", got, 8388608)
	}
	envLegacyConfig := t.TempDir()
	if err := os.WriteFile(filepath.Join(envLegacyConfig, "settings.yaml"), []byte("uploads:\n  max_bytes: 20971520\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := (&server{config: envLegacyConfig}).fileTransferMaxBytes(); got != 8388608 {
		t.Fatalf("legacy environment transfer limit = %d, want %d", got, 8388608)
	}
	for _, test := range []struct {
		value int64
		want  int64
	}{
		{value: 0, want: 1 * 1024 * 1024},
		{value: 1024 * 1024, want: 1 * 1024 * 1024},
		{value: 600 * 1024 * 1024, want: 512 * 1024 * 1024},
	} {
		boundedConfig := t.TempDir()
		settings := fmt.Sprintf("uploads:\n  max_bytes: %d\n", test.value)
		if err := os.WriteFile(filepath.Join(boundedConfig, "settings.yaml"), []byte(settings), 0o600); err != nil {
			t.Fatal(err)
		}
		if got := (&server{config: boundedConfig}).fileTransferMaxBytes(); got != test.want {
			t.Fatalf("bounded transfer limit for %d = %d, want %d", test.value, got, test.want)
		}
	}
	request := httptest.NewRequest(http.MethodPost, "/api/upload", strings.NewReader("chunked"))
	request.ContentLength = -1
	response := httptest.NewRecorder()
	s.upload(response, request)
	if response.Code != http.StatusLengthRequired || !bytes.Contains(response.Body.Bytes(), []byte(`"error_code":"length_required"`)) {
		t.Fatalf("chunked upload = %d %s", response.Code, response.Body.String())
	}
}

func TestUploadRejectsUnknownSessionBeforeFinderMutation(t *testing.T) {
	root := t.TempDir()
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, finder: service}
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("files", "note.txt")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := part.Write([]byte("content")); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/api/upload?session=missing", &body)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	record := httptest.NewRecorder()
	s.upload(record, request)
	if record.Code != http.StatusNotFound || !bytes.Contains(record.Body.Bytes(), []byte(`"error_code":"session_not_found"`)) {
		t.Fatalf("unknown upload session = %d %s", record.Code, record.Body.String())
	}
}

func TestUploadReturnsSynchronousCanonicalResultThroughFinderScheduler(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("hello\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scheduler := newFinderJobScheduler()
	defer scheduler.Close()
	s := &server{root: root, finder: service, finderJobs: scheduler}
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("files", "asset.txt")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := part.Write([]byte("uploaded")); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/api/upload?editor_path="+url.QueryEscape(filepath.Join(root, "note.md")), &body)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	record := httptest.NewRecorder()
	s.upload(record, request)
	if record.Code != http.StatusOK {
		t.Fatalf("synchronous upload = %d %s", record.Code, record.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	if envelope["state"] != "ready" {
		t.Fatalf("upload envelope = %#v", envelope)
	}
	data, ok := envelope["data"].(map[string]any)
	if !ok || data["target_source"] != "editor_file_directory" {
		t.Fatalf("upload data = %#v", envelope["data"])
	}
}

func TestPhase45FinderGitRoutesExposeDiffAndBlameContracts(t *testing.T) {
	root := t.TempDir()
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: readonly\n    password: readonly\n    role: readonly\n  - username: admin\n    password: admin\n    role: admin\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "auth-cookie-secret"), []byte(strings.Repeat("11", 32)), 0o600); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "note.txt")
	if err := os.WriteFile(path, []byte("one\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"init", "-q", root}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "config", "user.name", "Finder Test"}, {"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "initial"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(path, []byte("two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, config: config, port: 7221, finder: service, finderJobs: newFinderJobScheduler(), capabilities: capabilities.New("go-test")}
	defer s.finderJobs.Close()
	mux := http.NewServeMux()
	s.registerFilesystemRoutes(mux)
	serve := func(username, password, method, requestURL string) *httptest.ResponseRecorder {
		record := httptest.NewRecorder()
		request := httptest.NewRequest(method, requestURL, nil)
		if username != "" {
			request.SetBasicAuth(username, password)
		}
		mux.ServeHTTP(record, request)
		return record
	}

	diff := serve("readonly", "readonly", http.MethodGet, "/api/fs/diff?path="+url.QueryEscape(path)+"&from=HEAD&to=current")
	if diff.Code != http.StatusOK || !bytes.Contains(diff.Body.Bytes(), []byte(`"relative_path":"note.txt"`)) || !bytes.Contains(diff.Body.Bytes(), []byte(`+two`)) {
		t.Fatalf("readonly diff route = %d %s", diff.Code, diff.Body.String())
	}

	history := serve("readonly", "readonly", http.MethodGet, "/api/fs/git-history?path="+url.QueryEscape(root)+"&limit=1")
	if history.Code != http.StatusOK || !bytes.Contains(history.Body.Bytes(), []byte(`"commits"`)) || !bytes.Contains(history.Body.Bytes(), []byte(`"subject":"initial"`)) {
		t.Fatalf("readonly history route = %d %s", history.Code, history.Body.String())
	}
	var historyEnvelope map[string]any
	if err := json.Unmarshal(history.Body.Bytes(), &historyEnvelope); err != nil {
		t.Fatalf("history envelope: %v", err)
	}
	historyData, ok := historyEnvelope["data"].(map[string]any)
	if !ok || historyData["snapshot_cursor"] == nil {
		t.Fatalf("history snapshot cursor = %#v", historyEnvelope)
	}
	snapshotCursor, ok := historyData["snapshot_cursor"].(string)
	if !ok || snapshotCursor == "" {
		t.Fatalf("history snapshot cursor value = %#v", historyData["snapshot_cursor"])
	}
	historyStats := serve("readonly", "readonly", http.MethodGet, "/api/fs/git-history?path="+url.QueryEscape(root)+"&limit=1&stats=1")
	if historyStats.Code != http.StatusOK || !bytes.Contains(historyStats.Body.Bytes(), []byte(`"files":1`)) || !bytes.Contains(historyStats.Body.Bytes(), []byte(`"added":1`)) || !bytes.Contains(historyStats.Body.Bytes(), []byte(`"removed":0`)) {
		t.Fatalf("readonly history stats route = %d %s", historyStats.Code, historyStats.Body.String())
	}
	historyStatsWindow := serve("readonly", "readonly", http.MethodGet, "/api/fs/git-history?path="+url.QueryEscape(root)+"&limit=1&cursor="+url.QueryEscape(snapshotCursor)+"&stats=1&stats_offset=0")
	if historyStatsWindow.Code != http.StatusOK || !bytes.Contains(historyStatsWindow.Body.Bytes(), []byte(`"files":1`)) || !bytes.Contains(historyStatsWindow.Body.Bytes(), []byte(`"added":1`)) || !bytes.Contains(historyStatsWindow.Body.Bytes(), []byte(`"removed":0`)) {
		t.Fatalf("readonly history independent stats route = %d %s", historyStatsWindow.Code, historyStatsWindow.Body.String())
	}
	historyStatsStream := serve("readonly", "readonly", http.MethodGet, "/api/fs/git-history-stats-stream?path="+url.QueryEscape(root)+"&cursor="+url.QueryEscape(snapshotCursor)+"&count=1&batch_size=1")
	streamBody := historyStatsStream.Body.String()
	if historyStatsStream.Code != http.StatusOK || historyStatsStream.Header().Get("Content-Type") != "text/event-stream; charset=utf-8" || strings.Count(streamBody, "event: chunk") != 1 || !strings.Contains(streamBody, "event: start") || !strings.Contains(streamBody, "event: done") || !strings.Contains(streamBody, `"files":1`) {
		t.Fatalf("readonly history stats stream route = %d headers=%v body=%s", historyStatsStream.Code, historyStatsStream.Header(), streamBody)
	}
	scheduledHistoryStatsStream := serve("readonly", "readonly", http.MethodGet, "/api/fs/git-history-stats-stream?path="+url.QueryEscape(root)+"&cursor="+url.QueryEscape(snapshotCursor)+"&batch_sizes=1%2B2")
	scheduledStreamBody := scheduledHistoryStatsStream.Body.String()
	if scheduledHistoryStatsStream.Code != http.StatusOK || strings.Count(scheduledStreamBody, "event: chunk") != 1 || !strings.Contains(scheduledStreamBody, `"batch_sizes":[1,2]`) || !strings.Contains(scheduledStreamBody, `"files":1`) || !strings.Contains(scheduledStreamBody, "event: done") {
		t.Fatalf("scheduled readonly history stats stream route = %d headers=%v body=%s", scheduledHistoryStatsStream.Code, scheduledHistoryStatsStream.Header(), scheduledStreamBody)
	}
	invalidScheduledHistoryStatsStream := serve("readonly", "readonly", http.MethodGet, "/api/fs/git-history-stats-stream?path="+url.QueryEscape(root)+"&cursor="+url.QueryEscape(snapshotCursor)+"&batch_sizes=0")
	if invalidScheduledHistoryStatsStream.Code != http.StatusBadRequest {
		t.Fatalf("invalid scheduled history stats stream route = %d body=%s", invalidScheduledHistoryStatsStream.Code, invalidScheduledHistoryStatsStream.Body.String())
	}
	headOutput, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	sha := string(bytes.TrimSpace(headOutput))
	commit := serve("readonly", "readonly", http.MethodGet, "/api/fs/git-commit?path="+url.QueryEscape(root)+"&commit="+url.QueryEscape(sha)+"&head="+url.QueryEscape(sha))
	if commit.Code != http.StatusOK || !bytes.Contains(commit.Body.Bytes(), []byte(`"sha"`)) || !bytes.Contains(commit.Body.Bytes(), []byte(`"files"`)) {
		t.Fatalf("readonly commit route = %d %s", commit.Code, commit.Body.String())
	}

	blame := serve("admin", "admin", http.MethodGet, "/api/batch/blame?path="+url.QueryEscape(path))
	if blame.Code != http.StatusOK || !bytes.Contains(blame.Body.Bytes(), []byte(`"in_repo":true`)) || !bytes.Contains(blame.Body.Bytes(), []byte(`"1":`)) {
		t.Fatalf("admin blame route = %d %s", blame.Code, blame.Body.String())
	}

	readonlyBlame := serve("readonly", "readonly", http.MethodGet, "/api/batch/blame?path="+url.QueryEscape(path))
	if readonlyBlame.Code != http.StatusForbidden || !bytes.Contains(readonlyBlame.Body.Bytes(), []byte(`"role":"readonly"`)) {
		t.Fatalf("readonly blame route = %d %s", readonlyBlame.Code, readonlyBlame.Body.String())
	}
	missingBlamePath := serve("admin", "admin", http.MethodGet, "/api/batch/blame")
	if missingBlamePath.Code != http.StatusBadRequest || !bytes.Contains(missingBlamePath.Body.Bytes(), []byte(`"key":"request.error.pathRequired"`)) {
		t.Fatalf("missing blame path = %d %s", missingBlamePath.Code, missingBlamePath.Body.String())
	}
	unauthenticatedDiff := serve("", "", http.MethodGet, "/api/fs/diff?path="+url.QueryEscape(path))
	if unauthenticatedDiff.Code != http.StatusUnauthorized || !bytes.Contains(unauthenticatedDiff.Body.Bytes(), []byte(`"key":"auth.error.authenticationRequired"`)) {
		t.Fatalf("unauthenticated diff route = %d %s", unauthenticatedDiff.Code, unauthenticatedDiff.Body.String())
	}
	unauthenticatedBlame := serve("", "", http.MethodGet, "/api/batch/blame?path="+url.QueryEscape(path))
	if unauthenticatedBlame.Code != http.StatusUnauthorized || !bytes.Contains(unauthenticatedBlame.Body.Bytes(), []byte(`"key":"auth.error.authenticationRequired"`)) {
		t.Fatalf("unauthenticated blame route = %d %s", unauthenticatedBlame.Code, unauthenticatedBlame.Body.String())
	}

	if changed := s.capabilities.Set("finder.git.v1", capabilities.Entry{Reason: &capabilities.Reason{
		Code: "disabled_for_test", MessageKey: "test.capabilityDisabled", MessageParams: map[string]any{},
	}}); !changed {
		t.Fatal("disabling the Git capability did not change the catalog")
	}
	for _, route := range []string{
		"/api/fs/diff?path=" + url.QueryEscape(path),
		"/api/fs/git-history?path=" + url.QueryEscape(root),
		"/api/fs/git-commit?path=" + url.QueryEscape(root) + "&commit=" + url.QueryEscape(sha),
		"/api/batch/blame?path=" + url.QueryEscape(path),
	} {
		disabled := serve("admin", "admin", http.MethodGet, route)
		if disabled.Code != http.StatusNotImplemented || !bytes.Contains(disabled.Body.Bytes(), []byte(`"capability":"finder.git.v1"`)) {
			t.Fatalf("disabled Git route %s = %d %s", route, disabled.Code, disabled.Body.String())
		}
	}
}

func TestPhase8SessionFilesRoutesExposeParityEnvelopes(t *testing.T) {
	root := t.TempDir()
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: readonly\n    password: readonly\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "auth-cookie-secret"), []byte(strings.Repeat("22", 32)), 0o600); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "note.txt")
	if err := os.WriteFile(path, []byte("base\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"init", "-q", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
		{"-C", root, "add", "note.txt"},
		{"-C", root, "commit", "-qm", "initial"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(path, []byte("working\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	// Bazel's TEST_TMPDIR can be deeply nested. Keep the Unix socket itself under /tmp;
	// tmux rejects a longer pathname before the test reaches the route under test.
	tmuxSocketDir, err := os.MkdirTemp("/tmp", "yo7221-tmux-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(tmuxSocketDir) })
	tmuxSocket := filepath.Join(tmuxSocketDir, "tmux.sock")
	t.Setenv("YOLOMUX_TMUX_SOCKET", tmuxSocket)
	session := "go-phase8-session"
	if output, err := exec.Command("tmux", "-S", tmuxSocket, "new-session", "-d", "-s", session, "-c", root).CombinedOutput(); err != nil {
		t.Fatalf("tmux new-session: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", tmuxSocket, "kill-server").Run() })

	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, config: config, finder: service, capabilities: capabilities.New("go-test")}
	mux := http.NewServeMux()
	s.registerFilesystemRoutes(mux)
	serve := func(requestURL string) *httptest.ResponseRecorder {
		record := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodGet, requestURL, nil)
		request.SetBasicAuth("readonly", "readonly")
		mux.ServeHTTP(record, request)
		return record
	}
	decode := func(record *httptest.ResponseRecorder) map[string]any {
		t.Helper()
		if record.Code != http.StatusOK {
			t.Fatalf("session-files route = %d %s", record.Code, record.Body.String())
		}
		var envelope map[string]any
		if err := json.Unmarshal(record.Body.Bytes(), &envelope); err != nil {
			t.Fatalf("session-files envelope: %v (%s)", err, record.Body.String())
		}
		if envelope["state"] != "ready" || envelope["ok"] != true || envelope["terminal"] != true {
			t.Fatalf("session-files envelope = %#v", envelope)
		}
		return envelope
	}

	refs := url.QueryEscape(`{"` + root + `":{"from":"HEAD","to":"current"}}`)
	single := decode(serve("/api/session-files?session=" + url.QueryEscape(session) + "&hours=7.5&from=HEAD&to=current&refs=" + refs))
	data, ok := single["data"].(map[string]any)
	if !ok || data["session"] != session || data["hours"] != 7.5 || data["from_ref"] != "HEAD" || data["to_ref"] != "current" {
		t.Fatalf("single session-files data = %#v", single)
	}
	if files, ok := data["files"].([]any); !ok || len(files) != 1 {
		t.Fatalf("single session-files files = %#v", data["files"])
	}

	batch := decode(serve("/api/session-files-batch?session=" + url.QueryEscape(session) + "&session=" + url.QueryEscape(session)))
	batchData, ok := batch["data"].(map[string]any)
	if !ok {
		t.Fatalf("batch session-files data = %#v", batch)
	}
	sessions, ok := batchData["sessions"].(map[string]any)
	if !ok || len(sessions) != 1 {
		t.Fatalf("batch sessions = %#v", batchData["sessions"])
	}
	statuses, ok := batchData["statuses"].(map[string]any)
	if !ok || statuses[session] != float64(http.StatusOK) {
		t.Fatalf("batch statuses = %#v", batchData["statuses"])
	}

	aggregate := decode(serve("/api/session-files?hours=7.5&from=HEAD&to=current"))
	aggregateData, ok := aggregate["data"].(map[string]any)
	if !ok || aggregateData["session"] != "" || aggregateData["hours"] != 7.5 || aggregateData["from_ref"] != "HEAD" || aggregateData["to_ref"] != "current" {
		t.Fatalf("aggregate session-files data = %#v", aggregate)
	}
	if files, ok := aggregateData["files"].([]any); !ok || len(files) != 1 {
		t.Fatalf("aggregate session-files files = %#v", aggregateData["files"])
	}

	cacheOnly := serve("/api/session-files?session=" + url.QueryEscape(session) + "&hours=7.5&cache_only=1&cache_view=" + strings.Repeat("0", 64))
	if cacheOnly.Code != http.StatusAccepted {
		t.Fatalf("cache-only missing view status = %d %s", cacheOnly.Code, cacheOnly.Body.String())
	}
	var cacheOnlyPayload map[string]any
	if err := json.Unmarshal(cacheOnly.Body.Bytes(), &cacheOnlyPayload); err != nil {
		t.Fatalf("cache-only missing view envelope: %v (%s)", err, cacheOnly.Body.String())
	}
	if cacheOnlyPayload["state"] != "queued" || cacheOnlyPayload["status"] != "pending" || cacheOnlyPayload["terminal"] != false {
		t.Fatalf("cache-only missing view envelope = %#v", cacheOnlyPayload)
	}
}

func TestPhase8CacheOnlySessionFilesReadSkipsSessionDiscovery(t *testing.T) {
	s := &server{config: t.TempDir()}
	record := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/session-files?session=missing&cache_only=1&cache_view=not-an-opaque-view", nil)
	s.sessionFiles(record, request)
	if record.Code != http.StatusAccepted {
		t.Fatalf("cache-only response status = %d %s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["state"] != "queued" || payload["status"] != "pending" || payload["terminal"] != false {
		t.Fatalf("cache-only response = %#v", payload)
	}
}

func TestPhase8SessionFilesCachePersistsAndRejectsMismatchedViews(t *testing.T) {
	config := t.TempDir()
	request := sessionFilesRequest{session: "session-1", hours: 24, fromRef: "HEAD", toRef: "current"}
	descriptor := sessionFilesRequestDescriptor(request)
	viewID := sessionFilesCacheViewID(descriptor)
	payload := map[string]any{"session": request.session, "files": []any{"changed.py"}, "loaded": true}
	writer := &server{config: config}
	writer.writeSessionFilesCache(request, viewID, payload, http.StatusOK)

	reader := &server{config: config}
	record, ok := reader.readSessionFilesCache(request, viewID)
	if !ok || record.Payload["session"] != request.session {
		t.Fatalf("persisted cache read = %#v, ok=%t", record, ok)
	}
	mismatched := request
	mismatched.fromRef = "other"
	if _, ok := reader.readSessionFilesCache(mismatched, viewID); ok {
		t.Fatal("cache view was accepted for a mismatched request descriptor")
	}
}

func TestPhase8SessionFilesStaleRefreshIsSingleFlight(t *testing.T) {
	config := t.TempDir()
	request := sessionFilesRequest{session: "session-1", hours: 24}
	viewID := sessionFilesCacheViewID(sessionFilesRequestDescriptor(request))
	server := &server{config: config, finderBackgroundJobs: newFinderBackgroundJobScheduler()}
	defer server.finderBackgroundJobs.Close()
	started := make(chan struct{})
	release := make(chan struct{})
	task := func(ctx context.Context) (map[string]any, *finder.Error) {
		close(started)
		select {
		case <-release:
			return map[string]any{"session": request.session, "files": []any{"new.py"}}, nil
		case <-ctx.Done():
			return nil, &finder.Error{Status: http.StatusServiceUnavailable, Code: "cancelled", Message: ctx.Err().Error()}
		}
	}
	if !server.scheduleSessionFilesRefresh(request, viewID, task) {
		t.Fatal("first stale refresh was not admitted")
	}
	<-started
	if server.scheduleSessionFilesRefresh(request, viewID, task) {
		t.Fatal("duplicate stale refresh was admitted")
	}
	close(release)
	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	for {
		record, ok := server.readSessionFilesCache(request, viewID)
		if ok && len(record.Payload["files"].([]any)) == 1 && record.Payload["files"].([]any)[0] == "new.py" {
			return
		}
		select {
		case <-time.After(10 * time.Millisecond):
		case <-deadline.C:
			t.Fatalf("stale refresh did not publish cache: %#v", record)
		}
	}
}

func TestSessionFilesPaneInputsResolveClaudeTranscriptIdentity(t *testing.T) {
	root := t.TempDir()
	home := t.TempDir()
	bin := filepath.Join(t.TempDir(), "bin")
	if err := os.Mkdir(bin, 0o700); err != nil {
		t.Fatal(err)
	}
	claude := filepath.Join(bin, "claude")
	if err := os.Symlink("/bin/sleep", claude); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	// Bazel's TEST_TMPDIR can be deeply nested. Keep the Unix socket itself under /tmp;
	// tmux rejects a longer pathname before the test reaches the route under test.
	tmuxSocketDir, err := os.MkdirTemp("/tmp", "yo7221-tmux-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(tmuxSocketDir) })
	tmuxSocket := filepath.Join(tmuxSocketDir, "tmux.sock")
	t.Setenv("YOLOMUX_TMUX_SOCKET", tmuxSocket)
	session := "go-phase8-claude"
	if output, err := exec.Command("tmux", "-S", tmuxSocket, "new-session", "-d", "-s", session, "-c", root, claude, "30").CombinedOutput(); err != nil {
		t.Fatalf("tmux new-session: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", tmuxSocket, "kill-server").Run() })

	panePID := ""
	for deadline := time.Now().Add(time.Second); time.Now().Before(deadline); {
		output, err := exec.Command("tmux", "-S", tmuxSocket, "display-message", "-p", "-t", "="+session+":", "#{pane_pid}").Output()
		if err == nil && strings.TrimSpace(string(output)) != "" {
			panePID = strings.TrimSpace(string(output))
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if panePID == "" {
		t.Fatal("tmux pane PID was not available")
	}
	transcript := filepath.Join(home, ".claude", "projects", "fixture", "claude-session.jsonl")
	if err := os.MkdirAll(filepath.Dir(transcript), 0o700); err != nil {
		t.Fatal(err)
	}
	line := `{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"` + filepath.Join(root, "changed.txt") + `"}}]}}` + "\n"
	if err := os.WriteFile(transcript, []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
	metadata := filepath.Join(home, ".claude", "sessions", panePID+".json")
	if err := os.MkdirAll(filepath.Dir(metadata), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(metadata, []byte(`{"sessionId":"claude-session","cwd":"`+root+`"}`), 0o600); err != nil {
		t.Fatal(err)
	}

	s := &server{}
	_, agents, finderErr := s.sessionFilesPaneInputs(session)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if len(agents) != 1 || agents[0].Kind != "claude" || agents[0].CWD != root {
		t.Fatalf("Claude pane agent = %#v", agents)
	}
	if agents[0].SessionID != "claude-session" || agents[0].Transcript != transcript {
		t.Fatalf("Claude pane transcript identity = %#v, want session=%q transcript=%q", agents[0], "claude-session", transcript)
	}
	query := "?session=" + url.QueryEscape(session) + "&pane_target=" + url.QueryEscape(agents[0].PaneTarget)
	routeChecks := []struct {
		name string
		path string
		call func(http.ResponseWriter, *http.Request)
		want string
	}{
		{name: "raw transcript", path: "/api/transcript", call: s.transcript, want: `"path":"` + strings.ReplaceAll(transcript, `\`, `\\`)},
		{name: "compact context", path: "/api/context", call: s.context, want: "tool_use"},
		{name: "structured context", path: "/api/context-items", call: s.contextItems, want: "tool_use"},
	}
	for _, route := range routeChecks {
		route := route
		t.Run(route.name, func(t *testing.T) {
			record := httptest.NewRecorder()
			route.call(record, httptest.NewRequest(http.MethodGet, route.path+query, nil))
			if record.Code != http.StatusOK || !strings.Contains(record.Body.String(), route.want) {
				t.Fatalf("response = %d %s", record.Code, record.Body.String())
			}
		})
	}
}

func TestPhase45SynchronousFinderRoutesUseBoundedScheduler(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "note.txt")
	if err := os.WriteFile(path, []byte("needle\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	routes := []struct {
		name   string
		method string
		url    string
		body   string
		call   func(*server, http.ResponseWriter, *http.Request)
	}{
		{name: "diff", method: http.MethodGet, url: "/api/fs/diff?path=" + url.QueryEscape(path), call: (*server).fsDiff},
		{name: "direct search", method: http.MethodGet, url: "/api/fs/search?root=" + url.QueryEscape(root) + "&query=needle", call: (*server).fsSearch},
		{name: "index status", method: http.MethodGet, url: "/api/fs/index-status?root=" + url.QueryEscape(root), call: (*server).fsIndexStatus},
		{name: "resolve candidates", method: http.MethodPost, url: "/api/fs/resolve-candidates", body: `{"paths":["` + path + `"]}`, call: (*server).fsResolveCandidates},
	}
	for _, route := range routes {
		route := route
		t.Run(route.name, func(t *testing.T) {
			scheduler := work.New(1, 1)
			defer scheduler.Close()
			release := make(chan struct{})
			defer close(release)
			started := make(chan struct{})
			if _, err := scheduler.Submit(context.Background(), "held", func(context.Context) (any, error) {
				close(started)
				<-release
				return nil, nil
			}); err != nil {
				t.Fatal(err)
			}
			<-started
			if _, err := scheduler.Submit(context.Background(), "queued", func(context.Context) (any, error) { return nil, nil }); err != nil {
				t.Fatal(err)
			}

			s := &server{root: root, finder: service, finderJobs: scheduler}
			request := httptest.NewRequest(route.method, route.url, strings.NewReader(route.body))
			response := httptest.NewRecorder()
			route.call(s, response, request)
			if response.Code != http.StatusServiceUnavailable || !bytes.Contains(response.Body.Bytes(), []byte(`"error_code":"service_busy"`)) {
				t.Fatalf("saturated route = %d %s", response.Code, response.Body.String())
			}
		})
	}
}

func TestSessionMetadataRouteProvidesBootIdentity(t *testing.T) {
	s := &server{events: newEventBroker()}
	request := httptest.NewRequest(http.MethodGet, "/api/session-metadata?force=1", nil)
	record := httptest.NewRecorder()
	s.sessionMetadata(record, request)
	if record.Code != http.StatusOK {
		t.Fatalf("session metadata status = %d %s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	identity, ok := payload["metadata_identity"].(map[string]any)
	if !ok || identity["epoch"] == "" || identity["generation"] != float64(1) {
		t.Fatalf("metadata identity = %#v", payload["metadata_identity"])
	}
	cache, ok := payload["cache"].(map[string]any)
	if !ok || cache["pending_identity"] == nil || cache["stale"] != true {
		t.Fatalf("forced metadata cache = %#v", payload["cache"])
	}
	pending, ok := cache["pending_identity"].(map[string]any)
	pendingGeneration, pendingGenerationOK := pending["generation"].(float64)
	servedGeneration, servedGenerationOK := identity["generation"].(float64)
	if !ok || !pendingGenerationOK || !servedGenerationOK || pending["epoch"] != identity["epoch"] || pendingGeneration <= servedGeneration {
		t.Fatalf("forced metadata pending identity = %#v, served = %#v", pending, identity)
	}
}

func TestForcedSessionMetadataRefreshPublishesThePendingGeneration(t *testing.T) {
	s := &server{events: newEventBroker()}
	request := httptest.NewRequest(http.MethodGet, "/api/session-metadata?force=1", nil)
	record := httptest.NewRecorder()
	s.sessionMetadata(record, request)
	if record.Code != http.StatusOK {
		t.Fatalf("session metadata status = %d %s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	cache := payload["cache"].(map[string]any)
	pending := cache["pending_identity"].(map[string]any)
	wantGeneration := uint64(pending["generation"].(float64))
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	for {
		s.metadataMu.Lock()
		generation := s.metadataGeneration
		s.metadataMu.Unlock()
		if generation >= wantGeneration {
			break
		}
		select {
		case <-deadline.C:
			t.Fatalf("forced metadata generation did not publish: got %d, want %d", generation, wantGeneration)
		case <-time.After(10 * time.Millisecond):
		}
	}
	events := s.events.snapshot()
	if events["published"] == 0 {
		t.Fatal("forced metadata refresh did not publish a client event")
	}
}

func TestSessionMetadataIncludesEveryGlobalTmuxSessionAsTabSource(t *testing.T) {
	socket := shortSyntheticTmuxSocket(t)
	root := t.TempDir()
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	for _, session := range []string{"synthetic-tab-a", "synthetic-tab-b"} {
		if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", session, "-c", root).CombinedOutput(); err != nil {
			t.Fatalf("create synthetic tmux session %q: %v (%s)", session, err, output)
		}
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	record := httptest.NewRecorder()
	(&server{events: newEventBroker()}).sessionMetadata(record, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("session metadata status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	sessions, ok := payload["sessions"].(map[string]any)
	if !ok || len(sessions) != 2 {
		t.Fatalf("session metadata sessions = %#v", payload["sessions"])
	}
	order, ok := payload["session_order"].([]any)
	if !ok || len(order) != 2 || order[0] != "synthetic-tab-a" || order[1] != "synthetic-tab-b" {
		t.Fatalf("session metadata session_order = %#v", payload["session_order"])
	}
	if got := payload["session_scope"]; got != "all" {
		t.Fatalf("session metadata session_scope = %#v, want all", got)
	}
	for _, session := range []string{"synthetic-tab-a", "synthetic-tab-b"} {
		info, ok := sessions[session].(map[string]any)
		if !ok {
			t.Fatalf("session %q metadata = %#v", session, sessions[session])
		}
		panes, ok := info["panes"].([]any)
		if !ok || len(panes) != 1 {
			t.Fatalf("session %q panes = %#v", session, info["panes"])
		}
		windows, ok := info["window_metadata"].([]any)
		if !ok || len(windows) != 1 {
			t.Fatalf("session %q window metadata = %#v", session, info["window_metadata"])
		}
	}
	second := httptest.NewRecorder()
	(&server{events: newEventBroker()}).sessionMetadata(second, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
	if second.Code != http.StatusOK {
		t.Fatalf("second session metadata status = %d, body=%s", second.Code, second.Body.String())
	}
	var secondPayload map[string]any
	if err := json.Unmarshal(second.Body.Bytes(), &secondPayload); err != nil {
		t.Fatal(err)
	}
	if got := secondPayload["topology_generation"]; got != float64(1) {
		t.Fatalf("isolated second server topology generation = %#v, want 1", got)
	}

	shared := &server{events: newEventBroker()}
	firstShared := httptest.NewRecorder()
	shared.sessionMetadata(firstShared, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
	secondShared := httptest.NewRecorder()
	shared.sessionMetadata(secondShared, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
	var firstSharedPayload, secondSharedPayload map[string]any
	if err := json.Unmarshal(firstShared.Body.Bytes(), &firstSharedPayload); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(secondShared.Body.Bytes(), &secondSharedPayload); err != nil {
		t.Fatal(err)
	}
	if firstSharedPayload["topology_generation"] != float64(1) || secondSharedPayload["topology_generation"] != float64(2) {
		t.Fatalf("shared metadata topology generations = %#v then %#v, want 1 then 2", firstSharedPayload["topology_generation"], secondSharedPayload["topology_generation"])
	}
}

func TestSessionMetadataKeepsLastKnownGlobalRosterWhenDiscoveryFails(t *testing.T) {
	reads := 0
	s := &server{
		events: newEventBroker(),
		tmuxSessionRosterFn: func() ([]string, error) {
			reads++
			if reads == 1 {
				return []string{"synthetic-tab-a", "synthetic-tab-b"}, nil
			}
			return nil, errors.New("synthetic tmux roster read failed")
		},
	}
	first := httptest.NewRecorder()
	s.sessionMetadata(first, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
	if first.Code != http.StatusOK {
		t.Fatalf("first metadata status = %d, body=%s", first.Code, first.Body.String())
	}
	second := httptest.NewRecorder()
	s.sessionMetadata(second, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
	if second.Code != http.StatusOK {
		t.Fatalf("cached metadata status = %d, body=%s", second.Code, second.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(second.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	order, ok := payload["session_order"].([]any)
	if !ok || len(order) != 2 || order[0] != "synthetic-tab-a" || order[1] != "synthetic-tab-b" {
		t.Fatalf("cached metadata session_order = %#v", payload["session_order"])
	}
	errorsPayload, ok := payload["errors"].([]any)
	if !ok || len(errorsPayload) < 1 || len(errorsPayload) > 2 || errorsPayload[0] != "synthetic tmux roster read failed" {
		t.Fatalf("cached metadata errors = %#v", payload["errors"])
	}
	if len(errorsPayload) == 2 && errorsPayload[1] != "exit status 1" {
		t.Fatalf("cached metadata enrichment error = %#v", errorsPayload[1])
	}
	s.tmuxSessionActivityFn = func() (map[string]float64, error) { return map[string]float64{}, nil }
	activity := httptest.NewRecorder()
	s.activity(activity, httptest.NewRequest(http.MethodGet, "/api/activity?scope=all", nil))
	if activity.Code != http.StatusOK {
		t.Fatalf("cached activity status = %d, body=%s", activity.Code, activity.Body.String())
	}
	var activityPayload map[string]any
	if err := json.Unmarshal(activity.Body.Bytes(), &activityPayload); err != nil {
		t.Fatal(err)
	}
	activityOrder, ok := activityPayload["session_order"].([]any)
	if !ok || len(activityOrder) != 2 || activityOrder[0] != "synthetic-tab-a" || activityOrder[1] != "synthetic-tab-b" {
		t.Fatalf("cached activity session_order = %#v", activityPayload["session_order"])
	}
}

func TestSessionMetadataAndActivitySerializeRosterReadBeforeStampingTopologyGeneration(t *testing.T) {
	firstStarted := make(chan struct{})
	secondEntered := make(chan struct{})
	releaseFirst := make(chan struct{})
	var callsMu sync.Mutex
	calls := 0
	s := &server{events: newEventBroker(), tmuxSessionRosterFn: func() ([]string, error) {
		callsMu.Lock()
		calls++
		call := calls
		callsMu.Unlock()
		if call == 1 {
			close(firstStarted)
			<-releaseFirst
			return []string{"synthetic-old"}, nil
		}
		close(secondEntered)
		return []string{"synthetic-new"}, nil
	}, tmuxSessionActivityFn: func() (map[string]float64, error) {
		return map[string]float64{}, nil
	}, tmuxWindowActivityFn: func() (map[string]float64, error) {
		return map[string]float64{}, nil
	}}
	first := httptest.NewRecorder()
	second := httptest.NewRecorder()
	firstDone := make(chan struct{})
	secondDone := make(chan struct{})
	go func() {
		s.sessionMetadata(first, httptest.NewRequest(http.MethodGet, "/api/session-metadata", nil))
		close(firstDone)
	}()
	<-firstStarted
	go func() {
		s.activity(second, httptest.NewRequest(http.MethodGet, "/api/activity?scope=all", nil))
		close(secondDone)
	}()
	premature := false
	select {
	case <-secondEntered:
		premature = true
	case <-time.After(50 * time.Millisecond):
	}
	close(releaseFirst)
	<-firstDone
	<-secondDone
	if premature {
		t.Fatal("second roster read entered before the first read was assigned its generation")
	}
	var firstPayload, secondPayload map[string]any
	if err := json.Unmarshal(first.Body.Bytes(), &firstPayload); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(second.Body.Bytes(), &secondPayload); err != nil {
		t.Fatal(err)
	}
	if firstPayload["topology_generation"] != float64(1) || secondPayload["topology_generation"] != float64(2) {
		t.Fatalf("serialized topology generations = %#v then %#v, want 1 then 2", firstPayload["topology_generation"], secondPayload["topology_generation"])
	}
	if got := firstPayload["session_order"].([]any); len(got) != 1 || got[0] != "synthetic-old" {
		t.Fatalf("first roster = %#v", firstPayload["session_order"])
	}
	if got := secondPayload["session_order"].([]any); len(got) != 1 || got[0] != "synthetic-new" {
		t.Fatalf("second roster = %#v", secondPayload["session_order"])
	}
}

func TestPhase45FinderBatchSearchStreamAndWatchDiffHTTPRoutes(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "search-me.go"), []byte("package main\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "nested"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "nested", "search-me-deep.go"), []byte("package deep\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, finder: service}

	batch := httptest.NewRecorder()
	s.fsBatch(batch, httptest.NewRequest(http.MethodPost, "/api/fs/batch", bytes.NewBufferString(`{"requests":[{"id":"one","type":"list","path":"`+root+`"},{"id":"two","type":"info","path":"`+filepath.Join(root, "search-me.go")+`"}]}`)))
	if batch.Code != http.StatusOK || !bytes.Contains(batch.Body.Bytes(), []byte(`"id":"one"`)) || !bytes.Contains(batch.Body.Bytes(), []byte(`"id":"two"`)) {
		t.Fatalf("batch route = %d %s", batch.Code, batch.Body.String())
	}

	stream := httptest.NewRecorder()
	s.fsSearch(stream, httptest.NewRequest(http.MethodGet, "/api/fs/search-stream?root="+url.QueryEscape(root)+"&query=search&limit=10", nil))
	if stream.Code != http.StatusOK || stream.Header().Get("Content-Type") != "text/event-stream; charset=utf-8" || bytes.Count(stream.Body.Bytes(), []byte("event: chunk")) != 2 || !bytes.Contains(stream.Body.Bytes(), []byte("search-me-deep.go")) {
		t.Fatalf("search stream route = %d headers=%v body=%s", stream.Code, stream.Header(), stream.Body.String())
	}

	batchSearch := httptest.NewRecorder()
	s.fsSearch(batchSearch, httptest.NewRequest(http.MethodGet, "/api/batch/search?root="+url.QueryEscape(root)+"&query=search&limit=10", nil))
	if batchSearch.Code != http.StatusOK || !bytes.Contains(batchSearch.Body.Bytes(), []byte("search-me-deep.go")) || bytes.Contains(batchSearch.Body.Bytes(), []byte(`"index_state":"warming"`)) {
		t.Fatalf("batch search route = %d %s", batchSearch.Code, batchSearch.Body.String())
	}

	watch := httptest.NewRecorder()
	watchRequest := httptest.NewRequest(http.MethodPost, "/api/watch/roots", bytes.NewBufferString(`{"client_id":"http","roots":["`+root+`"]}`))
	s.watchRoots(watch, watchRequest)
	if watch.Code != http.StatusOK || !bytes.Contains(watch.Body.Bytes(), []byte(`"watch_token"`)) {
		t.Fatalf("watch registration = %d %s", watch.Code, watch.Body.String())
	}
	var registration map[string]any
	if err := json.Unmarshal(watch.Body.Bytes(), &registration); err != nil {
		t.Fatal(err)
	}
	diff := httptest.NewRecorder()
	s.fsWatchDiff(diff, httptest.NewRequest(http.MethodGet, "/api/fs/watch-diff?full=1", nil))
	if diff.Code != http.StatusOK || !bytes.Contains(diff.Body.Bytes(), []byte(`"mode":"full"`)) || !bytes.Contains(diff.Body.Bytes(), []byte(`search-me.go`)) {
		t.Fatalf("watch diff = %d %s", diff.Code, diff.Body.String())
	}
}

func TestPhase45FinderBatchMatchesPythonValidationAndPerformanceEnvelope(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("hello"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, finder: service}

	record := httptest.NewRecorder()
	s.fsBatch(record, httptest.NewRequest(http.MethodPost, "/api/fs/batch", bytes.NewBufferString(`{"client_revision":"r1","client_scope":"browser","requests":[{"id":"one","type":"info","path":"`+filepath.Join(root, "note.txt")+`","trigger_counts":{"explicit-user":2}},{"id":"bad","type":"bogus","path":"`+root+`"},{"id":"trigger","type":"list","path":"`+root+`","trigger":"not-allowed"}]}`)))
	if record.Code != http.StatusOK {
		t.Fatalf("batch status = %d %s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	performance, ok := payload["performance"].(map[string]any)
	if !ok || performance["batch_size"] != float64(3) || performance["client_revision"] != "r1" || performance["client_scope"] != "browser" {
		t.Fatalf("batch performance = %#v", payload["performance"])
	}
	responses := payload["responses"].([]any)
	if responses[0].(map[string]any)["ok"] != true || responses[1].(map[string]any)["ok"] != false || responses[2].(map[string]any)["ok"] != false {
		t.Fatalf("batch response validation = %#v", responses)
	}

	tooMany := make([]string, finder.MaxBatchRequests+1)
	for index := range tooMany {
		tooMany[index] = `{"type":"info","path":"` + root + `"}`
	}
	tooManyBody := `{"requests":[` + strings.Join(tooMany, ",") + `]}`
	tooManyRecord := httptest.NewRecorder()
	s.fsBatch(tooManyRecord, httptest.NewRequest(http.MethodPost, "/api/fs/batch", bytes.NewBufferString(tooManyBody)))
	if tooManyRecord.Code != http.StatusBadRequest || !bytes.Contains(tooManyRecord.Body.Bytes(), []byte(`request.error.tooManyItems`)) || !bytes.Contains(tooManyRecord.Body.Bytes(), []byte(`"ok":false`)) || !bytes.Contains(tooManyRecord.Body.Bytes(), []byte(`"terminal":true`)) || !bytes.Contains(tooManyRecord.Body.Bytes(), []byte(`"origin":"server.http"`)) || !bytes.Contains(tooManyRecord.Body.Bytes(), []byte(`"maximum":64`)) {
		t.Fatalf("too many batch = %d %s", tooManyRecord.Code, tooManyRecord.Body.String())
	}

	malformedRecord := httptest.NewRecorder()
	s.fsBatch(malformedRecord, httptest.NewRequest(http.MethodPost, "/api/fs/batch", bytes.NewBufferString(`{"requests":"not-a-list"}`)))
	if malformedRecord.Code != http.StatusBadRequest || !bytes.Contains(malformedRecord.Body.Bytes(), []byte(`"state":"failed"`)) || !bytes.Contains(malformedRecord.Body.Bytes(), []byte(`"user_message"`)) || !bytes.Contains(malformedRecord.Body.Bytes(), []byte(`"stack"`)) || !bytes.Contains(malformedRecord.Body.Bytes(), []byte(`request.error.list`)) || !bytes.Contains(malformedRecord.Body.Bytes(), []byte(`"requests_type":"string"`)) {
		t.Fatalf("malformed batch = %d %s", malformedRecord.Code, malformedRecord.Body.String())
	}
}

func TestPhase45FinderBatchReturnsDurableReceiptWhenWorkerIsBusy(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("hello"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scheduler := work.New(1, 1)
	defer scheduler.Close()
	release := make(chan struct{})
	started := make(chan struct{})
	if _, err := scheduler.Submit(context.Background(), "held", func(context.Context) (any, error) {
		close(started)
		<-release
		return nil, nil
	}); err != nil {
		t.Fatal(err)
	}
	<-started
	s := &server{root: root, finder: service, events: newEventBroker(), operations: map[string]*goOperation{}, finderJobs: scheduler}
	record := httptest.NewRecorder()
	s.fsBatch(record, httptest.NewRequest(http.MethodPost, "/api/fs/batch", bytes.NewBufferString(`{"requests":[{"id":"info","type":"info","path":"`+filepath.Join(root, "note.txt")+`"}]}`)))
	close(release)
	if record.Code != http.StatusAccepted {
		t.Fatalf("busy batch status = %d %s", record.Code, record.Body.String())
	}
	var receipt map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &receipt); err != nil {
		t.Fatal(err)
	}
	operation, ok := receipt["operation"].(map[string]any)
	if !ok || operation["id"] == "" {
		t.Fatalf("busy batch receipt = %#v", receipt)
	}
	operationID := operation["id"].(string)
	deadline := time.After(2 * time.Second)
	for {
		current := s.operationRecord(operationID)
		if current != nil && current.TerminalEvent != nil {
			if current.Result["state"] != "ready" {
				t.Fatalf("busy batch terminal result = %#v", current.Result)
			}
			break
		}
		select {
		case <-deadline:
			t.Fatal("busy batch operation did not terminalize")
		case <-time.After(5 * time.Millisecond):
		}
	}
}

func TestPhase45FinderBatchReturnsReadyProductWithinFastBudget(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("hello"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scheduler := newFinderJobScheduler()
	defer scheduler.Close()
	s := &server{
		root:              root,
		finder:            service,
		events:            newEventBroker(),
		operations:        map[string]*goOperation{},
		operationsPath:    filepath.Join(root, "operations.json"),
		operationsJournal: filepath.Join(root, "operations.jsonl"),
		finderJobs:        scheduler,
	}
	record := httptest.NewRecorder()
	s.fsBatch(record, httptest.NewRequest(http.MethodPost, "/api/fs/batch", bytes.NewBufferString(`{"requests":[{"id":"info","type":"info","path":"`+filepath.Join(root, "note.txt")+`"}]}`)))
	if record.Code != http.StatusOK {
		t.Fatalf("ready batch status = %d %s", record.Code, record.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	if envelope["state"] != "ready" || envelope["terminal"] != true {
		t.Fatalf("ready batch envelope = %#v", envelope)
	}
}

func TestPhase45FinderUnindexAndHTMLPreviewRoutes(t *testing.T) {
	root := t.TempDir()
	htmlPath := filepath.Join(root, "index.html")
	if err := os.WriteFile(htmlPath, []byte(`<script>document.body.textContent = "safe"</script>`), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, finder: service}

	indexStatus := httptest.NewRecorder()
	s.fsIndexStatus(indexStatus, httptest.NewRequest(http.MethodGet, "/api/fs/index-status?root="+url.QueryEscape(root), nil))
	if indexStatus.Code != http.StatusOK || !bytes.Contains(indexStatus.Body.Bytes(), []byte(`"index_state":"not_indexed"`)) || !bytes.Contains(indexStatus.Body.Bytes(), []byte(`"indexed":false`)) || !bytes.Contains(indexStatus.Body.Bytes(), []byte(`"ready":false`)) {
		t.Fatalf("index status = %d %s", indexStatus.Code, indexStatus.Body.String())
	}

	unindex := httptest.NewRecorder()
	s.fsUnindex(unindex, httptest.NewRequest(http.MethodPost, "/api/fs/unindex", bytes.NewBufferString(`{"root":"`+root+`"}`)))
	if unindex.Code != http.StatusOK || !bytes.Contains(unindex.Body.Bytes(), []byte(`"ok":true`)) || !bytes.Contains(unindex.Body.Bytes(), []byte(`"index_state":"not_indexed"`)) {
		t.Fatalf("unindex route = %d %s", unindex.Code, unindex.Body.String())
	}

	preview := httptest.NewRecorder()
	s.fsHTMLPreview(preview, httptest.NewRequest(http.MethodGet, "/api/batch/html-preview?path="+url.QueryEscape(htmlPath), nil))
	if preview.Code != http.StatusOK || preview.Header().Get("Content-Type") != "text/html; charset=utf-8" || !bytes.Contains(preview.Body.Bytes(), []byte(`sandbox="allow-scripts allow-forms allow-popups"`)) || !bytes.Contains(preview.Body.Bytes(), []byte(`&lt;script&gt;`)) {
		t.Fatalf("html preview route = %d headers=%v body=%s", preview.Code, preview.Header(), preview.Body.String())
	}

	nonHTML := httptest.NewRecorder()
	s.fsHTMLPreview(nonHTML, httptest.NewRequest(http.MethodGet, "/api/batch/html-preview?path="+url.QueryEscape(filepath.Join(root, "plain.txt")), nil))
	if nonHTML.Code != http.StatusBadRequest || !bytes.Contains(nonHTML.Body.Bytes(), []byte(`fs.error.htmlFileRequired`)) {
		t.Fatalf("non-html preview = %d %s", nonHTML.Code, nonHTML.Body.String())
	}
}

func TestPhase45ExpensiveFinderRoutesUseBoundedOperations(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "nested"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "nested", "needle.txt"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, finder: service, events: newEventBroker(), operations: map[string]*goOperation{}, finderJobs: newFinderJobScheduler()}
	defer s.finderJobs.Close()

	count := httptest.NewRecorder()
	s.fsCount(count, httptest.NewRequest(http.MethodGet, "/api/batch/count?path="+url.QueryEscape(root), nil))
	if count.Code != http.StatusAccepted {
		t.Fatalf("count status = %d %s", count.Code, count.Body.String())
	}
	var receipt map[string]any
	if err := json.Unmarshal(count.Body.Bytes(), &receipt); err != nil {
		t.Fatal(err)
	}
	operation, ok := receipt["operation"].(map[string]any)
	if !ok || operation["id"] == "" {
		t.Fatalf("count receipt = %#v", receipt)
	}
	countID := operation["id"].(string)
	countDeadline := time.After(2 * time.Second)
	countTicker := time.NewTicker(5 * time.Millisecond)
	defer countTicker.Stop()
	for {
		record := s.operationRecord(countID)
		if record != nil && record.TerminalEvent != nil {
			result := record.Result
			if result["state"] != "ready" || result["data"].(map[string]any)["files"] != float64(1) {
				t.Fatalf("count terminal result = %#v", result)
			}
			break
		}
		select {
		case <-countDeadline:
			t.Fatal("count operation did not terminalize")
		case <-countTicker.C:
		}
	}

	search := httptest.NewRecorder()
	s.fsSearch(search, httptest.NewRequest(http.MethodGet, "/api/fs/search?root="+url.QueryEscape(root)+"&query=needle&recursive=1", nil))
	if search.Code != http.StatusAccepted {
		t.Fatalf("recursive search status = %d %s", search.Code, search.Body.String())
	}
}

func TestRecursiveDeleteEscalatesToBulkLaneWhenBulkIsSaturated(t *testing.T) {
	root := t.TempDir()
	subtree := filepath.Join(root, "subtree")
	if err := os.Mkdir(subtree, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(subtree, "child.txt"), []byte("child"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	point := work.New(1, 2)
	bulk := work.New(1, 1)
	defer point.Close()
	defer bulk.Close()
	started := make(chan struct{})
	release := make(chan struct{})
	if _, err := bulk.Submit(context.Background(), "bulk-running", func(context.Context) (any, error) {
		close(started)
		<-release
		return nil, nil
	}); err != nil {
		t.Fatal(err)
	}
	<-started
	if _, err := bulk.Submit(context.Background(), "bulk-queued", func(context.Context) (any, error) { return nil, nil }); err != nil {
		t.Fatal(err)
	}
	s := &server{root: root, finder: service, events: newEventBroker(), operations: map[string]*goOperation{}, finderJobs: point, finderBulkJobs: bulk}
	record := httptest.NewRecorder()
	s.fsDelete(record, httptest.NewRequest(http.MethodPost, "/api/fs/delete", bytes.NewBufferString(`{"path":"`+subtree+`"}`)))
	if record.Code != http.StatusAccepted {
		t.Fatalf("recursive delete admission = %d %s", record.Code, record.Body.String())
	}
	var receipt map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &receipt); err != nil {
		t.Fatal(err)
	}
	operation := receipt["operation"].(map[string]any)
	id := operation["id"].(string)
	deadline := time.After(2 * time.Second)
	for {
		current := s.operationRecord(id)
		if current != nil && current.TerminalEvent != nil {
			if current.Result["state"] != "failed" || current.Result["error_code"] != "service_busy" {
				t.Fatalf("saturated bulk result = %#v", current.Result)
			}
			break
		}
		select {
		case <-deadline:
			t.Fatal("recursive delete did not terminalize after bulk saturation")
		case <-time.After(5 * time.Millisecond):
		}
	}
	close(release)
	if _, err := os.Stat(subtree); err != nil {
		t.Fatalf("bulk saturation unexpectedly removed subtree: %v", err)
	}
}

func TestFinderProductDeadlineIsEnforcedByItsWorkerContext(t *testing.T) {
	s := &server{finderJobs: newFinderJobScheduler()}
	defer s.finderJobs.Close()
	started := make(chan struct{})
	startedOnce := sync.Once{}
	startedAt := time.Now()
	_, finderErr := s.runFinderProductWithDeadline(context.Background(), 20*time.Millisecond, func(ctx context.Context) (finderProduct, *finder.Error) {
		startedOnce.Do(func() { close(started) })
		<-ctx.Done()
		return finderProduct{}, finderJobContextError(ctx.Err())
	})
	select {
	case <-started:
	default:
		t.Fatal("finder worker did not start")
	}
	if finderErr == nil || finderErr.Code != "deadline_exceeded" || finderErr.Status != http.StatusGatewayTimeout {
		t.Fatalf("deadline error = %#v", finderErr)
	}
	if elapsed := time.Since(startedAt); elapsed > time.Second {
		t.Fatalf("deadline returned too slowly: %s", elapsed)
	}
}

func TestFinderSlowStreamsDoNotOccupyFinderWorkers(t *testing.T) {
	finderJobs := work.New(1, 1)
	defer finderJobs.Close()
	streamJobs := work.New(2, 2)
	defer streamJobs.Close()
	s := &server{finderJobs: finderJobs, finderStreamJobs: streamJobs}
	started := make(chan struct{}, 2)
	release := make(chan struct{})
	streamTask := func(ctx context.Context) (finderProduct, *finder.Error) {
		started <- struct{}{}
		select {
		case <-release:
			return finderProduct{}, nil
		case <-ctx.Done():
			return finderProduct{}, finderJobContextError(ctx.Err())
		}
	}
	streamDone := make(chan error, 2)
	for range 2 {
		go func() {
			_, finderErr := s.runFinderStreamProduct(context.Background(), streamTask)
			streamDone <- func() error {
				if finderErr != nil {
					return finderErr
				}
				return nil
			}()
		}()
	}
	for range 2 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("slow stream did not start")
		}
	}
	normalDone := make(chan error, 1)
	go func() {
		_, finderErr := s.runFinderProduct(context.Background(), func(context.Context) (finderProduct, *finder.Error) {
			return finderProduct{}, nil
		})
		normalDone <- func() error {
			if finderErr != nil {
				return finderErr
			}
			return nil
		}()
	}()
	select {
	case err := <-normalDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("normal Finder work waited behind slow streams")
	}
	close(release)
	for range 2 {
		select {
		case err := <-streamDone:
			if err != nil {
				t.Fatal(err)
			}
		case <-time.After(time.Second):
			t.Fatal("slow stream did not finish")
		}
	}
}

type blockingFinderSSEWriter struct {
	header  http.Header
	blocked chan struct{}
	release chan struct{}
	mu      sync.Mutex
	writes  int
	status  int
}

func newBlockingFinderSSEWriter() *blockingFinderSSEWriter {
	return &blockingFinderSSEWriter{header: make(http.Header), blocked: make(chan struct{}), release: make(chan struct{})}
}

func (w *blockingFinderSSEWriter) Header() http.Header {
	return w.header
}

func (w *blockingFinderSSEWriter) WriteHeader(status int) {
	w.mu.Lock()
	w.status = status
	w.mu.Unlock()
}

func (w *blockingFinderSSEWriter) Status() int {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.status == 0 {
		return http.StatusOK
	}
	return w.status
}

func (w *blockingFinderSSEWriter) Write(value []byte) (int, error) {
	w.mu.Lock()
	w.writes++
	shouldBlock := w.writes == 2
	w.mu.Unlock()
	if shouldBlock {
		close(w.blocked)
		<-w.release
	}
	return len(value), nil
}

func (w *blockingFinderSSEWriter) Flush() {}

func TestFinderHTTPSlowStreamsDoNotOccupyFinderWorkers(t *testing.T) {
	root := t.TempDir()
	for index := range 40 {
		if err := os.WriteFile(filepath.Join(root, fmt.Sprintf("needle-%02d.txt", index)), []byte("needle"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	finderJobs := work.New(1, 1)
	defer finderJobs.Close()
	streamJobs := work.New(2, 2)
	defer streamJobs.Close()
	s := &server{root: root, finder: service, finderJobs: finderJobs, finderStreamJobs: streamJobs}
	writers := []*blockingFinderSSEWriter{newBlockingFinderSSEWriter(), newBlockingFinderSSEWriter()}
	streamDone := make(chan int, len(writers))
	for _, writer := range writers {
		go func(writer *blockingFinderSSEWriter) {
			s.fsSearch(writer, httptest.NewRequest(http.MethodGet, "/api/fs/search-stream?root="+url.QueryEscape(root)+"&query=needle&limit=40", nil))
			streamDone <- writer.Status()
		}(writer)
	}
	for _, writer := range writers {
		select {
		case <-writer.blocked:
		case <-time.After(time.Second):
			t.Fatal("slow HTTP stream did not reach response backpressure")
		}
	}
	normalDone := make(chan int, 1)
	go func() {
		record := httptest.NewRecorder()
		s.fsInfo(record, httptest.NewRequest(http.MethodGet, "/api/fs/info?path="+url.QueryEscape(filepath.Join(root, "needle-00.txt")), nil))
		normalDone <- record.Code
	}()
	select {
	case status := <-normalDone:
		if status != http.StatusOK {
			t.Fatalf("normal Finder request status = %d", status)
		}
	case <-time.After(time.Second):
		t.Fatal("normal Finder request waited behind slow HTTP streams")
	}
	for _, writer := range writers {
		close(writer.release)
	}
	for range writers {
		select {
		case status := <-streamDone:
			if status != http.StatusOK {
				t.Fatalf("slow stream status = %d", status)
			}
		case <-time.After(time.Second):
			t.Fatal("slow HTTP stream did not finish")
		}
	}
}

func TestFinderPointReadsDoNotBlockOnSpecialFiles(t *testing.T) {
	root := t.TempDir()
	fifo := filepath.Join(root, "blocked.fifo")
	if err := syscall.Mkfifo(fifo, 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{root: root, finder: service, events: newEventBroker(), operations: map[string]*goOperation{}, finderJobs: newFinderJobScheduler()}
	defer s.finderJobs.Close()
	handlers := []func(http.ResponseWriter, *http.Request){s.fsRead, s.fsInfo, s.fsRaw}
	for _, handler := range handlers {
		done := make(chan struct{})
		recorder := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodGet, "/api/fs/read?path="+url.QueryEscape(fifo), nil)
		go func() {
			handler(recorder, request)
			close(done)
		}()
		select {
		case <-done:
		case <-time.After(time.Second):
			t.Fatalf("Finder handler blocked on special file, status=%d", recorder.Code)
		}
	}
}

func TestPhase3EventChannelAndReplay(t *testing.T) {
	b := newEventBroker()
	if _, err := b.publish("backend_health_changed", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	ch, closeStream := b.subscribe()
	defer closeStream()
	e := <-ch
	if !e.Replay || e.Type != "backend_health_changed" {
		t.Fatalf("replay = %+v", e)
	}
	if !eventMatchesChannels("files_changed", []string{"files"}) {
		t.Fatal("files should match their channel")
	}
	if eventMatchesChannels("files_changed", []string{"status"}) {
		t.Fatal("status must not receive files")
	}
}

func TestEventBrokerSnapshotTracksHeartbeatTimestamp(t *testing.T) {
	b := newEventBroker()
	b.recordHeartbeat()
	snapshot := b.snapshot()
	if snapshot["heartbeat_events"] != 1 {
		t.Fatalf("heartbeat metrics = %+v", snapshot)
	}
	lastHeartbeat, ok := snapshot["last_heartbeat_at"].(float64)
	if !ok || lastHeartbeat <= 0 {
		t.Fatalf("heartbeat timestamp = %+v", snapshot["last_heartbeat_at"])
	}
}

func TestEventChannelFilterRejectsExplicitlyInvalidChannels(t *testing.T) {
	b := newEventBroker()
	stream, closeStream := b.subscribeClient("invalid-channel-client", []string{}...)
	defer closeStream()
	if _, err := b.publish("files_changed", map[string]any{"revision": 1}); err != nil {
		t.Fatal(err)
	}
	select {
	case event := <-stream:
		t.Fatalf("invalid channel received event: %+v", event)
	default:
	}
	if snapshot := b.snapshot(); snapshot["filtered_events"] != 1 {
		t.Fatalf("invalid channel filter metrics = %+v", snapshot)
	}
}

func TestPhase3JSONRoutesRejectTrailingAndOversizedBodies(t *testing.T) {
	s := &server{config: t.TempDir(), events: newEventBroker(), children: make(map[*exec.Cmd]struct{}), operations: map[string]*goOperation{}}
	trailing := httptest.NewRecorder()
	s.eventPost(trailing, httptest.NewRequest(http.MethodPost, "/api/event", strings.NewReader(`{"source":"one"}{"source":"two"}`)))
	if trailing.Code != http.StatusBadRequest {
		t.Fatalf("trailing event body status = %d %s", trailing.Code, trailing.Body.String())
	}
	oversized := httptest.NewRecorder()
	s.eventPost(oversized, httptest.NewRequest(http.MethodPost, "/api/event", strings.NewReader(`{"payload":"`+strings.Repeat("x", 64*1024)+`"}`)))
	if oversized.Code != http.StatusBadRequest {
		t.Fatalf("oversized event body status = %d %s", oversized.Code, oversized.Body.String())
	}
}

func TestOperationTerminalPersistsAndAcknowledgesExactCursor(t *testing.T) {
	dir := t.TempDir()
	s := &server{config: dir, events: newEventBroker(), children: make(map[*exec.Cmd]struct{}), operationsPath: filepath.Join(dir, "operations.json"), operationsJournal: filepath.Join(dir, "operations.jsonl"), operations: map[string]*goOperation{}}
	if err := s.acceptOperation("op-phase3"); err != nil {
		t.Fatal(err)
	}
	event, err := s.terminalizeOperation("op-phase3", map[string]any{"state": "succeeded", "value": "ok"}, http.StatusOK)
	if err != nil {
		t.Fatal(err)
	}
	if event.Type != "operation_terminal" || event.Resource != "operation_terminal:op-phase3" {
		t.Fatalf("terminal event = %+v", event)
	}
	reloaded := &server{config: dir, events: newEventBroker(), children: make(map[*exec.Cmd]struct{}), operationsPath: s.operationsPath, operationsJournal: s.operationsJournal, operations: loadOperationJournal(s.operationsJournal, loadOperations(s.operationsPath))}
	if record := reloaded.operationRecord("op-phase3"); record == nil || record.State != "succeeded" || record.Seq != 1 {
		t.Fatalf("reloaded operation = %+v", record)
	}
	record := reloaded.operationRecord("op-phase3")
	body := fmt.Sprintf(`{"acks":[{"id":"op-phase3","cursor":{"epoch":%q,"seq":1}}]}`, record.Epoch)
	response := httptest.NewRecorder()
	reloaded.operationAck(response, httptest.NewRequest(http.MethodPost, "/api/operations/ack", bytes.NewBufferString(body)))
	if response.Code != http.StatusOK || !bytes.Contains(response.Body.Bytes(), []byte(`"op-phase3"`)) {
		t.Fatalf("ack response = %d %s", response.Code, response.Body.String())
	}
	if !reloaded.operationRecord("op-phase3").Acknowledged {
		t.Fatal("operation was not marked acknowledged")
	}
}

func TestDurableEventJournalReloads(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "events.jsonl")
	b := loadEventBroker(path)
	if _, err := b.publish("backend_health_changed", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	reloaded := loadEventBroker(path)
	if len(reloaded.events) != 1 || reloaded.events[0].Type != "backend_health_changed" {
		t.Fatalf("reloaded events = %+v", reloaded.events)
	}
	ch, closeStream := reloaded.subscribe()
	defer closeStream()
	event := <-ch
	if !event.Replay || event.Epoch == "" {
		t.Fatalf("replayed event = %+v", event)
	}
}

func TestRetainedEventReplayUsesStableResourceOrder(t *testing.T) {
	for attempt := 0; attempt < 32; attempt++ {
		b := newEventBroker()
		if _, err := b.publish("search_progress", map[string]any{"scope_id": "scope-b", "coverage": 1}); err != nil {
			t.Fatal(err)
		}
		if _, err := b.publish("search_progress", map[string]any{"scope_id": "scope-a", "coverage": 1}); err != nil {
			t.Fatal(err)
		}
		stream, unsubscribe := b.subscribe("core")
		first, second := <-stream, <-stream
		unsubscribe()
		if first.Resource != "search_progress:scope-a" || second.Resource != "search_progress:scope-b" {
			t.Fatalf("retained replay order = %q, %q", first.Resource, second.Resource)
		}
	}
}

func TestUnknownOperationCannotBeTerminalized(t *testing.T) {
	s := &server{events: newEventBroker(), operations: map[string]*goOperation{}}
	if _, err := s.terminalizeOperation("missing", map[string]any{"state": "succeeded"}, http.StatusOK); err == nil {
		t.Fatal("unknown operation was terminalized")
	}
}

func TestOperationTerminalizationIsIdempotentAndCancellationPersists(t *testing.T) {
	dir := t.TempDir()
	s := &server{events: newEventBroker(), operations: map[string]*goOperation{}, operationsJournal: filepath.Join(dir, "ops.jsonl"), operationsPath: filepath.Join(dir, "ops.json")}
	if err := s.acceptOperation("op-cancel"); err != nil {
		t.Fatal(err)
	}
	if err := s.cancelOperation("op-cancel"); err != nil {
		t.Fatal(err)
	}
	if err := s.cancelOperation("op-cancel"); err == nil {
		t.Fatal("second cancel succeeded")
	}
	if s.operationRecord("op-cancel").State != "cancelled" {
		t.Fatal("cancel state missing")
	}
}

func TestEventOverflowTracksRepairResource(t *testing.T) {
	b := newEventBroker()
	b.mu.Lock()
	ch := make(chan clientEvent, 1)
	b.subscribers[ch] = struct{}{}
	b.pending[ch] = map[string]clientEvent{}
	b.mu.Unlock()
	if _, err := b.publish("files_changed", map[string]any{"n": 1}); err != nil {
		t.Fatal(err)
	}
	if _, err := b.publish("files_changed", map[string]any{"n": 2}); err != nil {
		t.Fatal(err)
	}
	b.mu.Lock()
	pending := b.pending[ch]["files_changed"]
	b.mu.Unlock()
	if pending.Type != "files_changed" {
		t.Fatalf("pending=%+v", pending)
	}
}

func TestOperationJournalReloadsAfterTerminalAndAck(t *testing.T) {
	dir := t.TempDir()
	s := &server{events: newEventBroker(), operations: map[string]*goOperation{}, operationsPath: filepath.Join(dir, "snapshot.json"), operationsJournal: filepath.Join(dir, "journal.jsonl")}
	if err := s.acceptOperation("op-journal"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.terminalizeOperation("op-journal", map[string]any{"state": "succeeded"}, 200); err != nil {
		t.Fatal(err)
	}
	record := s.operationRecord("op-journal")
	body := fmt.Sprintf(`{"acks":[{"id":"op-journal","cursor":{"epoch":%q,"seq":1}}]}`, record.Epoch)
	response := httptest.NewRecorder()
	s.operationAck(response, httptest.NewRequest(http.MethodPost, "/api/operations/ack", bytes.NewBufferString(body)))
	if response.Code != 200 {
		t.Fatal(response.Code)
	}
	reloaded := &server{events: newEventBroker(), operationsPath: s.operationsPath, operationsJournal: s.operationsJournal, operations: loadOperationJournal(s.operationsJournal, loadOperations(s.operationsPath))}
	if !reloaded.operationRecord("op-journal").Acknowledged {
		t.Fatal("ack not recovered")
	}
}

func TestJournalsDiscardTruncatedTail(t *testing.T) {
	dir := t.TempDir()
	eventPath := filepath.Join(dir, "events")
	_ = os.WriteFile(eventPath, []byte("{\"id\":1,\"type\":\"x\",\"epoch\":\"e\",\"resource\":\"x\",\"revision\":1,\"base_resource_revision\":0}\n{\"id\":2"), 0600)
	b := loadEventBroker(eventPath)
	if len(b.events) != 1 {
		t.Fatalf("events=%+v", b.events)
	}
	operationPath := filepath.Join(dir, "ops")
	_ = os.WriteFile(operationPath, []byte("{\"type\":\"operation\",\"record\":{\"id\":\"a\",\"state\":\"queued\",\"epoch\":\"e\"}}\n{\"type\":\"operation\""), 0600)
	records := loadOperationJournal(operationPath, map[string]*goOperation{})
	if records["a"] == nil {
		t.Fatal("operation missing")
	}
	data, _ := os.ReadFile(operationPath)
	if strings.Contains(string(data), "{\"type\":\"operation\"\n") {
		t.Fatal("truncated tail not removed")
	}
}

func TestJournalsDiscardSemanticallyMalformedTail(t *testing.T) {
	dir := t.TempDir()
	eventPath := filepath.Join(dir, "events")
	events := loadEventBroker(eventPath)
	if _, err := events.publish("fs_changed", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	eventPrefix, err := os.ReadFile(eventPath)
	if err != nil {
		t.Fatal(err)
	}
	eventFile, err := os.OpenFile(eventPath, os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	_, err = eventFile.WriteString(`{"version":1,"type":"event","event":{"id":2,"type":"","resource":"fs_changed","base_resource_revision":1,"resource_revision":2}}` + "\n")
	closeErr := eventFile.Close()
	if err != nil {
		t.Fatal(err)
	}
	if closeErr != nil {
		t.Fatal(closeErr)
	}
	if loaded := loadEventBroker(eventPath); len(loaded.events) != 1 {
		t.Fatalf("semantically malformed event tail was loaded: %+v", loaded.events)
	}
	if data, err := os.ReadFile(eventPath); err != nil || !bytes.Equal(data, eventPrefix) {
		t.Fatalf("event malformed tail was not removed: err=%v data=%q prefix=%q", err, data, eventPrefix)
	}

	operationPath := filepath.Join(dir, "operations")
	if err := appendOperationJournal(operationPath, map[string]any{
		"version": operationJournalVersion,
		"type":    "operation",
		"epoch":   "epoch",
		"record":  &goOperation{ID: "op-valid", State: "queued", Epoch: "epoch"},
	}); err != nil {
		t.Fatal(err)
	}
	operationPrefix, err := os.ReadFile(operationPath)
	if err != nil {
		t.Fatal(err)
	}
	operationFile, err := os.OpenFile(operationPath, os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operationFile.WriteString(`{"version":2,"type":"unknown","record":{"id":"op-tail","state":"queued"}}` + "\n")
	closeErr = operationFile.Close()
	if err != nil {
		t.Fatal(err)
	}
	if closeErr != nil {
		t.Fatal(closeErr)
	}
	loadedOperations := loadOperationJournal(operationPath, map[string]*goOperation{})
	if loadedOperations["op-valid"] == nil || loadedOperations["op-tail"] != nil {
		t.Fatalf("semantically malformed operation tail was loaded: %+v", loadedOperations)
	}
	if data, err := os.ReadFile(operationPath); err != nil || !bytes.Equal(data, operationPrefix) {
		t.Fatalf("operation malformed tail was not removed: err=%v data=%q prefix=%q", err, data, operationPrefix)
	}
}

func TestPhase3EventBrokerMatchesPythonDemandAndResourceContracts(t *testing.T) {
	b := newEventBroker()
	stream, unsubscribe := b.subscribe()
	defer unsubscribe()

	files, err := b.publish("fs_changed", map[string]any{"paths": []string{"/repo/a"}})
	if err != nil {
		t.Fatal(err)
	}
	if got := <-stream; got.Resource != "fs_changed" || got.Revision != 1 {
		t.Fatalf("default-channel event = %+v", got)
	}
	if !eventMatchesChannels("attention_acks_changed", []string{"attention"}) {
		t.Fatal("attention_acks_changed must belong to attention")
	}
	if eventMatchesChannels("stats_sample", []string{"core"}) {
		t.Fatal("stats_sample must not belong to core")
	}
	if !eventMatchesChannels("unknown_event", []string{"core"}) {
		t.Fatal("unknown event types must use the Python core fallback")
	}
	if got := b.readySnapshot(); got["fs_changed"] != files.Revision {
		t.Fatalf("ready snapshot = %+v", got)
	}
}

func TestPhase3OperationStatusAndAcknowledgementAreExact(t *testing.T) {
	dir := t.TempDir()
	s := &server{config: dir, events: newEventBroker(), children: make(map[*exec.Cmd]struct{}), operationsPath: filepath.Join(dir, "snapshot.json"), operationsJournal: filepath.Join(dir, "journal.jsonl"), operations: map[string]*goOperation{}}
	if err := s.acceptOperation("op-exact"); err != nil {
		t.Fatal(err)
	}
	status := httptest.NewRecorder()
	s.operation(status, httptest.NewRequest(http.MethodGet, "/api/operations/op-exact", nil))
	if status.Code != http.StatusAccepted || !bytes.Contains(status.Body.Bytes(), []byte(`"state":"queued"`)) || !bytes.Contains(status.Body.Bytes(), []byte(`"request"`)) {
		t.Fatalf("queued operation status = %d %s", status.Code, status.Body.String())
	}
	if _, err := s.terminalizeOperation("op-exact", map[string]any{"state": "ready", "value": "ok"}, http.StatusOK); err != nil {
		t.Fatal(err)
	}
	record := s.operationRecord("op-exact")
	bad := httptest.NewRecorder()
	badBody := fmt.Sprintf(`{"acks":[{"id":"op-exact","cursor":{"epoch":%q,"seq":1.5}}]}`, record.Epoch)
	s.operationAck(bad, httptest.NewRequest(http.MethodPost, "/api/operations/ack", strings.NewReader(badBody)))
	if bad.Code != http.StatusBadRequest {
		t.Fatalf("fractional acknowledgement = %d %s", bad.Code, bad.Body.String())
	}
	good := httptest.NewRecorder()
	goodBody := fmt.Sprintf(`{"acks":[{"id":"op-exact","cursor":{"epoch":%q,"seq":1},"extra":true}]}`, record.Epoch)
	s.operationAck(good, httptest.NewRequest(http.MethodPost, "/api/operations/ack", strings.NewReader(goodBody)))
	if good.Code != http.StatusBadRequest {
		t.Fatalf("extra acknowledgement field = %d %s", good.Code, good.Body.String())
	}
}

func TestOperationTerminalizationPreservesResultState(t *testing.T) {
	s := &server{events: newEventBroker(), operations: map[string]*goOperation{}}
	if err := s.acceptOperation("op-ready-state"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.terminalizeOperation("op-ready-state", map[string]any{"state": "ready", "value": "ok"}, http.StatusOK); err != nil {
		t.Fatal(err)
	}
	if record := s.operationRecord("op-ready-state"); record == nil || record.State != "ready" {
		t.Fatalf("terminal operation state = %+v, want ready", record)
	}
}

func TestOperationProducerUpdateRefreshesThePublicReceipt(t *testing.T) {
	s := &server{events: newEventBroker(), operations: map[string]*goOperation{}}
	if err := s.acceptOperationWithDetails("op-producer", "request", "GET /api/test", "test", 0, map[string]any{"phase": "queued"}, nil, nil); err != nil {
		t.Fatal(err)
	}
	updated, err := s.updateOperationProducer("op-producer", map[string]any{
		"service": "batchd",
		"chain":   []any{map[string]any{"stage": "running", "state": "active"}},
	}, 1)
	if err != nil || !updated {
		t.Fatalf("producer update = %v, %v", updated, err)
	}
	record := s.operationRecord("op-producer")
	operation, ok := record.Receipt["operation"].(map[string]any)
	if !ok {
		t.Fatalf("receipt operation = %+v", record.Receipt)
	}
	progress, ok := operation["progress"].(map[string]any)
	if !ok || progress["producer_stage"] != "running" || progress["producer_state"] != "active" {
		t.Fatalf("receipt producer progress = %+v", operation)
	}
}

func TestPhase3EventBrokerDeliversLatestRepairForKeyedPatch(t *testing.T) {
	b := newEventBroker()
	stream, unsubscribe := b.subscribe("status")
	defer unsubscribe()
	patch := map[string]any{"patch": true, "changes": map[string]any{"one": true}}
	if _, err := b.publish("auto_approve_changed", patch); err != nil {
		t.Fatal(err)
	}
	latest, err := b.publish("auto_approve_changed", patch)
	if err != nil {
		t.Fatal(err)
	}
	first := <-stream
	first = b.decorateEvent(stream, first)
	if first.ID != latest.ID || len(first.RepairResources) != 1 || first.RepairResources[0] != "auto_approve_changed" {
		t.Fatalf("patched coalesced event = %+v", first)
	}
}

func TestPhase3DiagnosticsHaveDurableShape(t *testing.T) {
	s := &server{config: t.TempDir(), events: newEventBroker(), children: make(map[*exec.Cmd]struct{})}
	background := httptest.NewRecorder()
	s.backgroundStatus(background, httptest.NewRequest(http.MethodGet, "/api/background/status", nil))
	for _, field := range []string{`"roles"`, `"counters"`, `"refresh_queue"`, `"search_index"`} {
		if !bytes.Contains(background.Body.Bytes(), []byte(field)) {
			t.Fatalf("background status missing %s: %s", field, background.Body.String())
		}
	}
	logs := httptest.NewRecorder()
	s.logs(logs, httptest.NewRequest(http.MethodGet, "/api/logs", nil))
	for _, field := range []string{`"ok":true`, `"epoch"`, `"capacity"`, `"dropped"`} {
		if !bytes.Contains(logs.Body.Bytes(), []byte(field)) {
			t.Fatalf("logs missing %s: %s", field, logs.Body.String())
		}
	}
}

func TestEventBrokerOverflowReportsRepairAndPerResourceMetrics(t *testing.T) {
	b := newEventBroker()
	ch := make(chan clientEvent, 1)
	b.mu.Lock()
	b.subscribers[ch] = struct{}{}
	b.channels[ch] = map[string]bool{}
	b.clientIDs[ch] = "overflow-test"
	b.clientStats[ch] = &eventSubscriberStats{}
	b.pending[ch] = map[string]clientEvent{}
	b.repair[ch] = map[string]bool{}
	b.mu.Unlock()
	if _, err := b.publish("files_changed", map[string]any{"resource": "a"}); err != nil {
		t.Fatal(err)
	}
	if _, err := b.publish("roots_changed", map[string]any{"resource": "b"}); err != nil {
		t.Fatal(err)
	}
	got := b.decorateEvent(ch, <-ch)
	if got.Type != "roots_changed" || len(got.RepairResources) != 1 || got.RepairResources[0] != "files_changed" {
		t.Fatalf("overflow repair = %+v", got)
	}
	snapshot := b.snapshot()
	if snapshot["dropped_events"] != 1 || snapshot["published_bytes"].(int) <= 0 {
		t.Fatalf("overflow metrics = %+v", snapshot)
	}
	dropped := snapshot["dropped_by_resource"].(map[string]map[string]int)
	if dropped["files_changed"]["events"] != 1 {
		t.Fatalf("dropped resource metrics = %+v", dropped)
	}
}

func TestEventBrokerKeepsRepairFenceUntilTheSubscriberDequeues(t *testing.T) {
	b := newEventBroker()
	ch := make(chan clientEvent, 1)
	b.mu.Lock()
	b.subscribers[ch] = struct{}{}
	b.channels[ch] = map[string]bool{}
	b.channelFilters[ch] = false
	b.clientStats[ch] = &eventSubscriberStats{}
	b.pending[ch] = map[string]clientEvent{}
	b.repair[ch] = map[string]bool{}
	b.mu.Unlock()

	if _, err := b.publish("files_changed", map[string]any{"value": 1}); err != nil {
		t.Fatal(err)
	}
	if _, err := b.publish("roots_changed", map[string]any{"value": 2}); err != nil {
		t.Fatal(err)
	}
	if _, err := b.publish("files_changed", map[string]any{"value": 3}); err != nil {
		t.Fatal(err)
	}

	event := b.decorateEvent(ch, <-ch)
	if event.Type != "files_changed" {
		t.Fatalf("latest event = %+v", event)
	}
	if got := event.RepairResources; len(got) != 2 || got[0] != "files_changed" || got[1] != "roots_changed" {
		t.Fatalf("repair fence = %+v", got)
	}
}

func TestCancellationPublishesTerminalEvent(t *testing.T) {
	s := &server{events: newEventBroker(), operations: map[string]*goOperation{}}
	ch, unsubscribe := s.events.subscribe()
	defer unsubscribe()
	if err := s.acceptOperation("op-cancel-event"); err != nil {
		t.Fatal(err)
	}
	if err := s.cancelOperation("op-cancel-event"); err != nil {
		t.Fatal(err)
	}
	event := <-ch
	if event.Type != "operation_terminal" || event.Payload["status"] != http.StatusOK {
		t.Fatalf("cancel event = %+v", event)
	}
	if record := s.operationRecord("op-cancel-event"); record == nil || record.State != "cancelled" || record.Seq != 1 {
		t.Fatalf("cancel record = %+v", record)
	}
}

func TestBackgroundFollowerCanClaimAfterIncumbentCloses(t *testing.T) {
	config := t.TempDir()
	incumbent := newGoBackgroundOwner(config, "/repo", 18011)
	follower := newGoBackgroundOwner(config, "/repo", 18012)
	if !incumbent.owner || follower.owner || follower.lockFile == nil {
		t.Fatalf("owner election incumbent=%+v follower=%+v", incumbent.owner, follower.owner)
	}
	if got := follower.statusPayload()["current_owner"].(map[string]any)["port"]; got != float64(18011) && got != 18011 {
		t.Fatalf("follower owner payload port = %#v", got)
	}
	incumbent.close()
	claim := follower.claim()
	if claim["claimed"] != true || follower.status != "local" || !follower.owner {
		t.Fatalf("claim = %+v", claim)
	}
	follower.close()
}

func TestBackgroundFollowerFencesFreshOwnerRecord(t *testing.T) {
	config := t.TempDir()
	incumbent := newGoBackgroundOwner(config, "/repo", 18013)
	follower := newGoBackgroundOwner(config, "/repo", 18014)
	if !incumbent.owner || follower.owner {
		t.Fatalf("owner election incumbent=%v follower=%v", incumbent.owner, follower.owner)
	}
	follower.mu.Lock()
	stop, done := follower.stopHeartbeat, follower.heartbeatDone
	follower.stopHeartbeat, follower.heartbeatDone = nil, nil
	follower.mu.Unlock()
	close(stop)
	<-done
	incumbent.releaseLock()
	claim := follower.claim()
	if claim["claimed"] == true || follower.owner || follower.status != "blocked_by_fresh_owner" {
		t.Fatalf("fresh-owner fence = %+v", claim)
	}
	incumbent.close()
	defer follower.close()
	claim = follower.claim()
	if claim["claimed"] != true || !follower.owner {
		t.Fatalf("post-stop takeover = %+v", claim)
	}
}

func TestBackgroundFollowerFencesForeignHostOwner(t *testing.T) {
	config := t.TempDir()
	incumbent := newGoBackgroundOwner(config, "/repo", 18015)
	follower := newGoBackgroundOwner(config, "/repo", 18016)
	if !incumbent.owner || follower.owner {
		t.Fatalf("owner election incumbent=%v follower=%v", incumbent.owner, follower.owner)
	}
	incumbent.stableHostID = "foreign-host"
	if err := incumbent.writeRecord(); err != nil {
		t.Fatal(err)
	}
	incumbent.releaseLock()
	defer incumbent.close()
	defer follower.close()
	claim := follower.claim()
	if claim["claimed"] == true || follower.status != "blocked_by_foreign_owner" {
		t.Fatalf("foreign-owner fence = %+v", claim)
	}
}

func TestBackgroundOwnerHeartbeatRefreshesRecord(t *testing.T) {
	dir := t.TempDir()
	owner := newGoBackgroundOwner(dir, "/repo", 18001)
	defer owner.close()
	before, _ := os.ReadFile(owner.ownerPath)
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	for {
		select {
		case <-ticker.C:
			after, err := os.ReadFile(owner.ownerPath)
			if err == nil && !bytes.Equal(before, after) {
				return
			}
		case <-deadline.C:
			t.Fatal("heartbeat did not refresh owner record")
		}
	}
}

func TestBackgroundOwnerTakeoverPublishesGenerationChange(t *testing.T) {
	dir := t.TempDir()
	broker := newEventBroker()
	stream, unsubscribe := broker.subscribe("core")
	defer unsubscribe()
	incumbent := newGoBackgroundOwner(dir, "/repo", 18001)
	follower := newGoBackgroundOwner(dir, "/repo", 18002)
	incumbent.setEventPublisher(func(kind string, payload map[string]any) error {
		_, err := broker.publish(kind, payload)
		return err
	})
	follower.setEventPublisher(func(kind string, payload map[string]any) error {
		_, err := broker.publish(kind, payload)
		return err
	})
	incumbent.close()
	defer follower.close()
	claim := follower.claim()
	if claim["claimed"] != true || !follower.owner || follower.status != "local" {
		t.Fatalf("takeover = %+v", claim)
	}
	latest, ok := follower.statusPayload()["latest_generation"].(map[string]any)
	if !ok || latest["generation_id"] != follower.generationID {
		t.Fatalf("latest generation = %+v", latest)
	}
	found := false
	deadline := time.NewTimer(500 * time.Millisecond)
	defer deadline.Stop()
	for {
		select {
		case event := <-stream:
			if event.Type == "background_owner_changed" {
				found = true
			}
		case <-deadline.C:
			if !found {
				t.Fatal("takeover did not publish background_owner_changed")
			}
			return
		}
	}
}

func TestBackgroundOwnerWriteFailureDemotesFailClosed(t *testing.T) {
	dir := t.TempDir()
	owner := newGoBackgroundOwner(dir, "/repo", 18003)
	defer owner.close()
	owner.mu.Lock()
	owner.writeFile = func(string, []byte) error { return os.ErrPermission }
	err := owner.writeRecord()
	if err == nil {
		owner.mu.Unlock()
		t.Fatal("fault injection did not fail owner record write")
	}
	owner.demoteLocked("owner_record_error", err)
	owner.mu.Unlock()
	status := owner.statusPayload()
	if status["owner"] != false || status["status"] != "owner_record_error" || status["last_error"] == "" {
		t.Fatalf("fail-closed status = %+v", status)
	}
}

func TestBackgroundOwnerWritesGenerationIndex(t *testing.T) {
	dir := t.TempDir()
	owner := newGoBackgroundOwner(dir, "/repo", 18004)
	defer owner.close()
	indexPath := filepath.Join(dir, "go-background-generations", "index.json")
	data, err := os.ReadFile(indexPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(data, []byte(`"version":1`)) || !bytes.Contains(data, []byte(owner.generationID)) {
		t.Fatalf("generation index = %s", data)
	}
}

func TestBackgroundStatusCarriesDurableRoleAndRefreshShape(t *testing.T) {
	owner := newGoBackgroundOwner(t.TempDir(), "/repo", 18005)
	defer owner.close()

	payload := owner.statusPayload()
	generation, ok := payload["latest_generation"].(map[string]any)
	if !ok {
		t.Fatalf("latest generation payload = %#v", payload["latest_generation"])
	}
	for _, key := range []string{"roles", "last_heartbeat", "owner", "status", "counters"} {
		if _, ok := generation[key]; !ok {
			t.Fatalf("generation omitted durable field %q: %#v", key, generation)
		}
	}
	roles, ok := payload["roles"].(map[string]any)
	if !ok || len(roles) != len(backgroundRoles) {
		t.Fatalf("role status = %#v", payload["roles"])
	}
	for _, role := range backgroundRoles {
		rolePayload, ok := roles[role].(map[string]any)
		if !ok {
			t.Fatalf("role %q payload = %#v", role, roles[role])
		}
		for _, key := range []string{"role", "owner", "status", "refresh_requests", "fallback_count", "last_error"} {
			if _, ok := rolePayload[key]; !ok {
				t.Fatalf("role %q omitted %q: %#v", role, key, rolePayload)
			}
		}
	}
	refreshQueue, ok := payload["refresh_queue"].(map[string]any)
	if !ok {
		t.Fatalf("refresh queue = %#v", payload["refresh_queue"])
	}
	if _, ok := refreshQueue["next_expires_seconds"]; !ok {
		t.Fatalf("refresh queue omitted expiry: %#v", refreshQueue)
	}
}

func TestBackgroundRefreshRequestsAreCoalescedAndCounted(t *testing.T) {
	owner := newGoBackgroundOwner(t.TempDir(), "/repo", 18006)
	defer owner.close()

	first := owner.requestRefresh("session-files", map[string]any{"cache_key": "same"})
	second := owner.requestRefresh("session-files", map[string]any{"cache_key": "same", "trigger": "watch"})
	if first["accepted"] != true || first["coalesced"] == true {
		t.Fatalf("first refresh request = %#v", first)
	}
	if second["accepted"] != true || second["coalesced"] != true || second["already_pending"] != true {
		t.Fatalf("coalesced refresh request = %#v", second)
	}
	status := owner.statusPayload()
	roles := status["roles"].(map[string]any)
	role := roles["session-files"].(map[string]any)
	if role["refresh_requests"] != 1 {
		t.Fatalf("refresh request count = %#v", role)
	}
	counters := status["counters"].(map[string]int)
	if counters["owner_refresh_requests"] != 1 || counters["coalesced_refresh_requests"] != 1 {
		t.Fatalf("refresh counters = %#v", counters)
	}
	queue := status["refresh_queue"].(map[string]any)
	if queue["recent_pending_count"] != 1 {
		t.Fatalf("refresh queue after coalescing = %#v", queue)
	}
}

func TestBackgroundOwnerCloseRemovesOwnedDurableRecords(t *testing.T) {
	dir := t.TempDir()
	owner := newGoBackgroundOwner(dir, "/repo", 18007)
	ownerPath, generationPath := owner.ownerPath, owner.generationPath
	if _, err := os.Stat(ownerPath); err != nil {
		t.Fatalf("owner record was not created: %v", err)
	}
	if _, err := os.Stat(generationPath); err != nil {
		t.Fatalf("generation record was not created: %v", err)
	}

	owner.close()
	if _, err := os.Stat(ownerPath); !os.IsNotExist(err) {
		t.Fatalf("owned owner record after close = %v", err)
	}
	if _, err := os.Stat(generationPath); !os.IsNotExist(err) {
		t.Fatalf("owned generation record after close = %v", err)
	}
}

func TestOperationJournalAppendAndCompactionShareTransactionLock(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "operations.jsonl")
	records := map[string]*goOperation{}
	for index := 0; index < 32; index++ {
		id := fmt.Sprintf("op-concurrent-%02d", index)
		records[id] = &goOperation{ID: id, State: "terminal", Epoch: "epoch", Seq: 1, TerminalAt: int64(index + 1)}
	}
	var group sync.WaitGroup
	for id := range records {
		id := id
		group.Add(1)
		go func() {
			defer group.Done()
			if err := appendOperationJournal(path, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": "epoch", "record": records[id]}); err != nil {
				t.Errorf("append %s: %v", id, err)
			}
		}()
	}
	for index := 0; index < 8; index++ {
		group.Add(1)
		go func() {
			defer group.Done()
			if err := compactOperationJournal(path, records); err != nil {
				t.Errorf("compact: %v", err)
			}
		}()
	}
	group.Wait()
	loaded := loadOperationJournal(path, map[string]*goOperation{})
	if len(loaded) != len(records) {
		t.Fatalf("concurrent journal lost records: got %d want %d", len(loaded), len(records))
	}
}

func TestLogRingReportsDroppedRangeAndLevels(t *testing.T) {
	logs := newGoLogRing()
	logs.capacity = 2
	logs.emit("info", "test", "one")
	logs.emit("warning", "test", "two")
	logs.emit("error", "test", "three")
	dropped := logs.payload()["dropped"].(map[string]any)
	if dropped["count"] != 1 || dropped["first_id"] != 1 || dropped["last_id"] != 1 {
		t.Fatalf("dropped metadata = %+v", dropped)
	}
	if dropped["by_level"].(map[string]int)["info"] != 1 {
		t.Fatalf("dropped levels = %+v", dropped)
	}
}

func TestAcknowledgedLargeOperationReplayIsBoundedAndJournalIsVersioned(t *testing.T) {
	dir := t.TempDir()
	s := &server{events: newEventBroker(), operations: map[string]*goOperation{}, operationsPath: filepath.Join(dir, "snapshot.json"), operationsJournal: filepath.Join(dir, "journal.jsonl")}
	if err := s.acceptOperation("op-large"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.terminalizeOperation("op-large", map[string]any{"state": "ready", "blob": strings.Repeat("x", operationReplayRecordBytes+1)}, http.StatusOK); err != nil {
		t.Fatal(err)
	}
	record := s.operationRecord("op-large")
	body := fmt.Sprintf(`{"acks":[{"id":"op-large","cursor":{"epoch":%q,"seq":1}}]}`, record.Epoch)
	response := httptest.NewRecorder()
	s.operationAck(response, httptest.NewRequest(http.MethodPost, "/api/operations/ack", strings.NewReader(body)))
	if response.Code != http.StatusOK || !s.operationRecord("op-large").DeliveryAcknowledged {
		t.Fatalf("ack response = %d %s", response.Code, response.Body.String())
	}
	status := httptest.NewRecorder()
	s.operation(status, httptest.NewRequest(http.MethodGet, "/api/operations/op-large", nil))
	if status.Code != http.StatusGone || !bytes.Contains(status.Body.Bytes(), []byte(`"operation_replay_evicted"`)) {
		t.Fatalf("bounded replay status = %d %s", status.Code, status.Body.String())
	}
	journal, err := os.ReadFile(s.operationsJournal)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(journal, []byte(`"version":2`)) || !bytes.Contains(journal, []byte(`"type":"snapshot"`)) {
		t.Fatalf("journal was not compacted to v2 snapshot: %s", journal)
	}
}

func TestEventJournalStartsANewProcessEpochOnReload(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "events.jsonl")
	first := loadEventBroker(path)
	if _, err := first.publish("backend_health_changed", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	oldEpoch := first.epoch
	second := loadEventBroker(path)
	if second.epoch == oldEpoch {
		t.Fatalf("reloaded broker reused persisted process epoch %q", second.epoch)
	}
	if second.events[0].Epoch != second.epoch {
		t.Fatalf("replayed event epoch = %q, broker epoch = %q", second.events[0].Epoch, second.epoch)
	}
}

func TestEventJournalUsesVersionedRecords(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "events.jsonl")
	b := loadEventBroker(path)
	if _, err := b.publish("backend_health_changed", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var entry map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(data), &entry); err != nil {
		t.Fatal(err)
	}
	if entry["version"] != float64(eventJournalVersion) || entry["type"] != "event" {
		t.Fatalf("event journal entry = %+v", entry)
	}
	if _, ok := entry["event"].(map[string]any); !ok {
		t.Fatalf("event journal payload = %+v", entry)
	}
}

func TestEventJournalCompactsWithRevisionSnapshotAndReloads(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "events.jsonl")
	b := loadEventBroker(path)
	for index := 0; index < eventJournalCompactEvery; index++ {
		if _, err := b.publish("fs_changed", map[string]any{"index": index}); err != nil {
			t.Fatal(err)
		}
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 1 {
		t.Fatalf("compacted event journal lines = %d", len(lines))
	}
	var entry struct {
		Version           int            `json:"version"`
		Type              string         `json:"type"`
		NextID            int            `json:"next_id"`
		ResourceRevisions map[string]int `json:"resource_revisions"`
		Events            []clientEvent  `json:"events"`
	}
	if err := json.Unmarshal([]byte(lines[0]), &entry); err != nil {
		t.Fatal(err)
	}
	if entry.Version != eventJournalSnapshotVersion || entry.Type != "snapshot" || entry.NextID != eventJournalCompactEvery || entry.ResourceRevisions["fs_changed"] != eventJournalCompactEvery || len(entry.Events) != eventJournalCompactEvery {
		t.Fatalf("event journal snapshot = %+v", entry)
	}
	reloaded := loadEventBroker(path)
	if reloaded.nextID != eventJournalCompactEvery || len(reloaded.events) != eventJournalCompactEvery || reloaded.revisions["fs_changed"] != eventJournalCompactEvery {
		t.Fatalf("reloaded event journal = next=%d events=%d revisions=%+v", reloaded.nextID, len(reloaded.events), reloaded.revisions)
	}
}

func TestCancelledOperationRecoveryPublishesItsTerminalEvent(t *testing.T) {
	dir := t.TempDir()
	events := newEventBroker()
	s := &server{
		config:            dir,
		events:            events,
		operationsPath:    filepath.Join(dir, "snapshot.json"),
		operationsJournal: filepath.Join(dir, "journal.jsonl"),
		operations:        map[string]*goOperation{},
	}
	s.operations["op-cancel-recovery"] = &goOperation{
		ID:            "op-cancel-recovery",
		State:         "cancelled",
		Epoch:         "epoch",
		TerminalEvent: map[string]any{"operation": map[string]any{"id": "op-cancel-recovery"}},
	}
	s.repairMissingTerminalEvents()
	if !events.hasResource("operation_terminal:op-cancel-recovery") {
		t.Fatal("cancelled operation terminal event was not recovered")
	}
}

func TestPublishedTerminalOperationIsNotRepublishedOnRestart(t *testing.T) {
	events := newEventBroker()
	s := &server{
		events:     events,
		operations: map[string]*goOperation{},
	}
	s.operations["op-published"] = &goOperation{
		ID:             "op-published",
		State:          "terminal",
		EventPublished: true,
		TerminalEvent:  map[string]any{"operation": map[string]any{"id": "op-published"}},
	}
	s.repairMissingTerminalEvents()
	if events.hasResource("operation_terminal:op-published") {
		t.Fatal("published terminal operation was republished")
	}
}

func TestBackgroundClaimFailureReturnsConflict(t *testing.T) {
	dir := t.TempDir()
	incumbent := newGoBackgroundOwner(dir, "/repo", 18021)
	follower := newGoBackgroundOwner(dir, "/repo", 18022)
	defer incumbent.close()
	defer follower.close()
	s := &server{config: dir, background: follower}
	response := httptest.NewRecorder()
	s.backgroundClaim(response, httptest.NewRequest(http.MethodPost, "/api/background/claim", nil))
	if response.Code != http.StatusConflict {
		t.Fatalf("failed background claim status = %d body=%s", response.Code, response.Body.String())
	}
	if bytes.Contains(response.Body.Bytes(), []byte(`"ok":true`)) {
		t.Fatalf("failed background claim reported success: %s", response.Body.String())
	}
}

func TestOlderBackgroundGenerationCannotRetakeFromNewerFollower(t *testing.T) {
	dir := t.TempDir()
	older := newGoBackgroundOwner(dir, "/repo", 18023)
	newer := newGoBackgroundOwner(dir, "/repo", 18024)
	defer older.close()
	defer newer.close()
	stopHeartbeat(t, older)
	stopHeartbeat(t, newer)
	older.mu.Lock()
	older.owner = false
	older.status = "follower"
	older.releaseLock()
	_ = older.writeGenerationRecord()
	claimed := older.tryTakeoverLocked()
	older.mu.Unlock()
	if claimed || older.owner || older.status != "blocked_by_newer_generation" {
		t.Fatalf("older generation takeover = claimed=%v owner=%v status=%q", claimed, older.owner, older.status)
	}
}

func stopHeartbeat(t *testing.T, owner *goBackgroundOwner) {
	t.Helper()
	owner.mu.Lock()
	stop, done := owner.stopHeartbeat, owner.heartbeatDone
	owner.stopHeartbeat, owner.heartbeatDone = nil, nil
	owner.mu.Unlock()
	if stop != nil {
		close(stop)
		<-done
	}
}

func TestBackgroundRefreshWithoutRoleUsesUnknownResource(t *testing.T) {
	if got := eventResource("background_refresh_done", map[string]any{}); got != "background:unknown" {
		t.Fatalf("background resource = %q", got)
	}
}

func TestOperationDemandIDsPreserveRequestedReplayOrder(t *testing.T) {
	got := operationDemandIDs("op-first", " op-second,op-first,op-third,")
	want := []string{"op-first", "op-second", "op-third"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("operation demand order = %#v, want %#v", got, want)
	}
}

func TestInstanceRootLeaseIsExclusive(t *testing.T) {
	root := t.TempDir()
	first, err := acquireInstanceRootLease(root)
	if err != nil {
		t.Fatal(err)
	}
	defer releaseInstanceRootLease(first)
	if second, err := acquireInstanceRootLease(root); err == nil {
		releaseInstanceRootLease(second)
		t.Fatal("second process acquired the instance root lease")
	}
	releaseInstanceRootLease(first)
	third, err := acquireInstanceRootLease(root)
	if err != nil {
		t.Fatalf("root lease was not released: %v", err)
	}
	releaseInstanceRootLease(third)
}

func TestOperationAcknowledgementJournalUsesPythonShape(t *testing.T) {
	path := filepath.Join(t.TempDir(), "operations.jsonl")
	if err := appendOperationJournal(path, map[string]any{
		"version": operationAckJournalVersion,
		"type":    "ack",
		"epoch":   "epoch",
		"acks":    []operationAck{{ID: "op-shape", Cursor: map[string]any{"epoch": "epoch", "seq": 1}}},
	}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var entry struct {
		Acks []map[string]json.RawMessage `json:"acks"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(data), &entry); err != nil {
		t.Fatal(err)
	}
	if len(entry.Acks) != 1 || len(entry.Acks[0]) != 2 {
		t.Fatalf("ack journal shape = %+v", entry.Acks)
	}
	if _, ok := entry.Acks[0]["epoch"]; ok {
		t.Fatalf("ack journal leaked duplicate epoch field = %+v", entry.Acks[0])
	}
}

func TestEventJournalMigratesLegacyRecords(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "events.jsonl")
	legacy := clientEvent{ID: 1, Type: "backend_health_changed", Time: 1, Payload: map[string]any{"ok": true}, Epoch: "old", Resource: "backend_health_changed", Revision: 1}
	data, err := json.Marshal(legacy)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(data, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	loaded := loadEventBroker(path)
	if len(loaded.events) != 1 {
		t.Fatalf("migrated events = %+v", loaded.events)
	}
	line, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var entry map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(line), &entry); err != nil {
		t.Fatal(err)
	}
	if entry["version"] != float64(eventJournalVersion) || entry["type"] != "event" {
		t.Fatalf("migrated journal entry = %+v", entry)
	}
}

func TestServerShutdownStopsBackgroundAndChildren(t *testing.T) {
	dir := t.TempDir()
	background := newGoBackgroundOwner(dir, "/repo", 18021)
	command := exec.Command("sleep", "30")
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	s := &server{children: map[*exec.Cmd]struct{}{command: {}}, background: background}
	s.shutdown()
	if err := command.Wait(); err == nil {
		t.Fatal("shutdown child was still running")
	}
	if background.stopHeartbeat != nil {
		t.Fatal("background heartbeat was not stopped")
	}
	if second := background.claim(); second["claimed"] == true {
		t.Fatalf("stopped background owner was reclaimable: %+v", second)
	}
}

func TestEventPostUsesPythonValidationAndEventLogShape(t *testing.T) {
	dir := t.TempDir()
	s := &server{config: dir, events: newEventBroker(), eventLogPath: filepath.Join(dir, "event-log.jsonl")}
	invalid := httptest.NewRecorder()
	s.eventPost(invalid, httptest.NewRequest(http.MethodPost, "/api/event", strings.NewReader(`{"payload":true}`)))
	if invalid.Code != http.StatusBadRequest {
		t.Fatalf("invalid event status = %d %s", invalid.Code, invalid.Body.String())
	}
	valid := httptest.NewRecorder()
	s.eventPost(valid, httptest.NewRequest(http.MethodPost, "/api/event", strings.NewReader(`{"session":"","type":"note","message":"saved","details":{"source":"test"}}`)))
	if valid.Code != http.StatusOK {
		t.Fatalf("valid event status = %d %s", valid.Code, valid.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(valid.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	event, ok := response["event"].(map[string]any)
	if !ok || event["type"] != "note" || event["message"] != "saved" || event["details"].(map[string]any)["source"] != "test" {
		t.Fatalf("event response = %#v", response)
	}
	if _, ok := event["resource"]; ok {
		t.Fatalf("event response leaked client-event envelope: %#v", event)
	}
}

func TestAuthCookiesArePortScopedAndHTTPSafe(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: dir, host: "127.0.0.1", port: 7221, httpMode: true}
	login := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/login", strings.NewReader("username=admin&password=secret&next=%2F"))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	s.login(login, req)
	setCookie := login.Header().Get("Set-Cookie")
	if !strings.HasPrefix(setCookie, "yolomux_auth_7221=") || strings.Contains(setCookie, "Secure") {
		t.Fatalf("HTTP login cookie = %q", setCookie)
	}
	valid := httptest.NewRequest(http.MethodGet, "/api/ping", nil)
	valid.Header.Set("Cookie", setCookie[:strings.IndexByte(setCookie, ';')])
	if _, ok := s.identity(valid); !ok {
		t.Fatal("scoped cookie was not accepted")
	}
	legacy := httptest.NewRequest(http.MethodGet, "/api/ping", nil)
	legacy.Header.Set("Cookie", "yolomux_auth="+s.cookieValue(authUser{username: "admin", password: "secret", role: "admin"}))
	if _, ok := s.identity(legacy); ok {
		t.Fatal("unscoped cookie was accepted")
	}
	logout := httptest.NewRecorder()
	s.logout(logout, httptest.NewRequest(http.MethodGet, "/logout", nil))
	logoutCookies := logout.Header().Values("Set-Cookie")
	if len(logoutCookies) < 2 || !strings.HasPrefix(logoutCookies[0], "yolomux_auth_7221=;") || !strings.Contains(strings.Join(logoutCookies, "\n"), "yolomux_logged_out=1") {
		t.Fatalf("logout cookies = %#v", logoutCookies)
	}
}
