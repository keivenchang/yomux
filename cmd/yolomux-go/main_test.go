package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

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
