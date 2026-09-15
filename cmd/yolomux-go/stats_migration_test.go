package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yolomux/internal/stats"
)

type statsStreamRecorder struct {
	recorder  *httptest.ResponseRecorder
	heartbeat chan struct{}
	once      sync.Once
}

func newStatsStreamRecorder() *statsStreamRecorder {
	return &statsStreamRecorder{recorder: httptest.NewRecorder(), heartbeat: make(chan struct{}, 1)}
}

func (r *statsStreamRecorder) Header() http.Header { return r.recorder.Header() }

func (r *statsStreamRecorder) WriteHeader(status int) { r.recorder.WriteHeader(status) }

func (r *statsStreamRecorder) Write(data []byte) (int, error) {
	n, err := r.recorder.Write(data)
	if bytes.Contains(data, []byte("event: heartbeat")) {
		r.once.Do(func() { close(r.heartbeat) })
	}
	return n, err
}

func (r *statsStreamRecorder) Flush() {}

func (r *statsStreamRecorder) Body() string { return r.recorder.Body.String() }

func TestStatsAgentStatusRequiresGET(t *testing.T) {
	s := &server{statsPath: filepath.Join(t.TempDir(), "missing.sqlite3")}
	record := httptest.NewRecorder()
	s.statsAgentStatus(record, httptest.NewRequest(http.MethodPost, "/api/stats-agent-status", nil))
	if record.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want %d", record.Code, http.StatusMethodNotAllowed)
	}
}

func TestStatsRoutesRequireAuthentication(t *testing.T) {
	s := &server{root: t.TempDir(), children: make(map[*exec.Cmd]struct{})}
	for _, path := range []string{"/api/stats-capabilities", "/api/stats-snapshot", "/api/stats-delta", "/api/stats-stream", "/api/pricing-catalog", "/readyz"} {
		record := httptest.NewRecorder()
		s.protected(http.HandlerFunc(s.statsCapabilities), "readonly").ServeHTTP(record, httptest.NewRequest(http.MethodGet, path, nil))
		if record.Code != http.StatusUnauthorized {
			t.Fatalf("%s status = %d, want unauthorized", path, record.Code)
		}
	}
}

func TestPricingCatalogHTTPServesPythonFixtureAndRefreshes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pricing.json")
	fixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"fixture","model":"model-a","aliases":["model-a"],"source":{"kind":"seed","url":"https://example.invalid/pricing"},"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"2.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(path, []byte(fixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := stats.LoadPricingCatalog(path)
	if err != nil {
		t.Fatal(err)
	}
	s := &server{pricing: catalog}
	for _, method := range []string{http.MethodGet, http.MethodPost} {
		record := httptest.NewRecorder()
		s.pricingCatalog(record, httptest.NewRequest(method, "/api/pricing-catalog", nil))
		if record.Code != http.StatusOK || !bytes.Contains(record.Body.Bytes(), []byte(`"catalog_revision":7`)) || !bytes.Contains(record.Body.Bytes(), []byte(`"model-a"`)) {
			t.Fatalf("method=%s status=%d body=%s", method, record.Code, record.Body.String())
		}
	}
}

func TestStatsQueryMatchesPythonBounds(t *testing.T) {
	for _, raw := range []string{
		"range_seconds=300&resolution=AUTO&client_id=browser-a",
		"range_seconds=900&resolution=10&client_id=browser-a&limit=100",
	} {
		r := httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?"+raw, nil)
		query, failure, ok := parseStatsQuery(r, false)
		if !ok || failure != nil || query.rangeSeconds == 0 || query.resolution == 0 {
			t.Fatalf("accepted query %q: query=%+v failure=%v", raw, query, failure)
		}
	}
	for _, raw := range []string{
		"range_seconds=301&resolution=AUTO&client_id=browser-a",
		"range_seconds=300&resolution=1&resolution=10&client_id=browser-a",
		"range_seconds=300&resolution=AUTO&client_id=browser-a&limit=10001",
	} {
		if _, failure, ok := parseStatsQuery(httptest.NewRequest(http.MethodGet, "/?"+raw, nil), false); ok || failure["status"] != "unsupported" {
			t.Fatalf("accepted invalid query %q: failure=%v", raw, failure)
		}
	}
}

func TestStatsWindowAlignsEndAndStartToResolution(t *testing.T) {
	now := time.Unix(1_789_359_333, 0)
	start, end := statsWindow(900, 10, now)
	if start != 1_789_358_440 || end != 1_789_359_340 {
		t.Fatalf("stats window = (%v, %v), want (1789358440, 1789359340)", start, end)
	}
	if end-start != 900 || int64(end)%10 != 0 || int64(start)%10 != 0 {
		t.Fatalf("stats window is not an aligned exact range: (%v, %v)", start, end)
	}
}

func TestStatsCapabilitiesWireShape(t *testing.T) {
	s := &server{}
	record := httptest.NewRecorder()
	s.statsCapabilities(record, httptest.NewRequest(http.MethodGet, "/api/stats-capabilities", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("status = %d", record.Code)
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["max_buckets"] != float64(600) {
		t.Fatalf("capabilities = %v", payload)
	}
}

func TestStatsObservationEnvelopeRequiresPythonFields(t *testing.T) {
	s := &server{statsWriter: nil}
	record := httptest.NewRecorder()
	s.statsObservations(record, httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(`{"observations":[]}`)))
	if record.Code != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", record.Code, record.Body.String())
	}
}

func TestStatsObservationsRejectsNonBrowserAndEmptyBatch(t *testing.T) {
	s := &server{config: t.TempDir()}
	if err := os.WriteFile(filepath.Join(s.config, "auth-cookie-secret"), []byte(strings.Repeat("a", 64)), 0o600); err != nil {
		t.Fatal(err)
	}
	handler := http.HandlerFunc(s.statsObservations)
	for _, body := range []string{
		`{"protocol_version":25,"schema_generation":9,"client_id":"browser-a","observations":[]}`,
		`{"protocol_version":25,"schema_generation":9,"client_id":"browser-a","observations":[{"event_id":"e","family":"cpu","source_id":"browser-a","observed_at":1,"epoch_id":"epoch","payload":{"kind":"heartbeat"}}]}`,
	} {
		record := httptest.NewRecorder()
		r := httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(body)).WithContext(context.WithValue(context.Background(), authContextKey{}, authUser{username: "alice"}))
		handler.ServeHTTP(record, r)
		if record.Code != http.StatusBadRequest {
			t.Fatalf("body=%s status=%d response=%s", body, record.Code, record.Body.String())
		}
	}
}

func TestStatsObservationsAcceptsAuthenticatedBrowserBatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth-cookie-secret"), []byte(strings.Repeat("a", 64)), 0o600); err != nil {
		t.Fatal(err)
	}
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	s := &server{config: config, statsWriter: writer}
	body := `{"protocol_version":25,"schema_generation":9,"client_id":"browser-a","observations":[{"event_id":"e","family":"browser","source_id":"browser-a","observed_at":1,"epoch_id":"epoch","payload":{"kind":"heartbeat","latency_ms":3}}]}`
	record := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(body)).WithContext(context.WithValue(context.Background(), authContextKey{}, authUser{username: "alice"}))
	s.statsObservations(record, r)
	if record.Code != http.StatusOK || !bytes.Contains(record.Body.Bytes(), []byte(`"accepted":1`)) {
		t.Fatalf("status=%d response=%s", record.Code, record.Body.String())
	}
	rows, err := stats.OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	observations, err := rows.ReadObservations(context.Background(), 0, 2, 10)
	if err != nil || len(observations) != 1 || observations[0].Family != "browser" || observations[0].SourceID == "browser-a" {
		t.Fatalf("stored observations=%+v err=%v", observations, err)
	}
}

func TestStatsObservationsAcceptsAuthenticatedFailureFieldsAndHeartbeat(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth-cookie-secret"), []byte(strings.Repeat("a", 64)), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: alice\n    password: secret\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	s := &server{config: config, statsWriter: writer}
	handler := s.protected(http.HandlerFunc(s.statsObservations), "readonly")
	unauthenticated := httptest.NewRecorder()
	handler.ServeHTTP(unauthenticated, httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(`{}`)))
	if unauthenticated.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated status=%d body=%s", unauthenticated.Code, unauthenticated.Body.String())
	}
	body := `{"protocol_version":25,"schema_generation":9,"client_id":"browser-a","observations":[
{"event_id":"error-event","family":"browser","source_id":"browser-a","observed_at":1,"epoch_id":"epoch","payload":{"kind":"error","journey_id":"journey-1","code_revision":"rev-1","browser_family":"chromium","signature":"jsf-deadbeef","message":"request failed","stack":"Error: request failed","source":"/api/stats","line":12,"column":4,"request_id":"r-error","route":"/api/stats","event_type":"fetch","wall_time":"2026-09-14 12:34:56 PDT","delivery_outcome":"failed","status":503}},
{"event_id":"warning-event","family":"browser","source_id":"browser-a","observed_at":2,"epoch_id":"epoch","payload":{"kind":"warning","signature":"jsf-cafebabe","message":"request stalled","source":"/api/stats-observations","event_type":"timeout","delivery_outcome":"stalled","status":408}},
{"event_id":"rejection-event","family":"browser","source_id":"browser-a","observed_at":3,"epoch_id":"epoch","payload":{"kind":"unhandledrejection","signature":"jsf-01234567","message":"promise rejected","source":"/app.js","provenance":"confirmed_real","event_type":"promise","delivery_outcome":"retrying","status":500}},
{"event_id":"heartbeat-event","family":"browser","source_id":"browser-a","observed_at":4,"epoch_id":"epoch","payload":{"kind":"heartbeat","latency_ms":3,"bytes":10,"upload_queue_depth":1,"upload_drops":0,"upload_retries":2,"instrumentation_cost_ms":0.5}}
]}`
	record := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(body))
	request.SetBasicAuth("alice", "secret")
	handler.ServeHTTP(record, request)
	if record.Code != http.StatusOK || !bytes.Contains(record.Body.Bytes(), []byte(`"accepted":4`)) {
		t.Fatalf("status=%d response=%s", record.Code, record.Body.String())
	}
	invalid := strings.Replace(body, `"event_id":"error-event"`, `"event_id":"arbitrary-field-event"`, 1)
	invalid = strings.Replace(invalid, `"status":503`, `"status":503,"arbitrary":true`, 1)
	rejected := httptest.NewRecorder()
	invalidRequest := httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(invalid))
	invalidRequest.SetBasicAuth("alice", "secret")
	handler.ServeHTTP(rejected, invalidRequest)
	if rejected.Code != http.StatusBadRequest {
		t.Fatalf("arbitrary field status=%d body=%s", rejected.Code, rejected.Body.String())
	}
}

func TestStatsStreamDisconnectsWithRequestContext(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	s := &server{statsPath: path}
	ctx, cancel := context.WithCancel(context.Background())
	request := httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=300&resolution=AUTO&client_id=browser-a", nil).WithContext(ctx)
	record := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { s.statsStream(record, request); close(done) }()
	time.Sleep(50 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("stream did not stop after disconnect")
	}
}

func TestStatsStreamAckIncludesRequestedResolution(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	server := &server{statsPath: path}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	recorder := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		server.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=900&resolution=AUTO&client_id=test&since_generation=0", nil).WithContext(ctx))
		close(done)
	}()
	time.Sleep(50 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("stats stream did not stop")
	}
	if !bytes.Contains(recorder.Body.Bytes(), []byte(`"requested_resolution":"AUTO"`)) {
		t.Fatalf("ack=%s", recorder.Body.String())
	}
}

func TestStatsStreamWithSinceGenerationKeepsDeltaStreamAlive(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	server := &server{statsPath: path}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	recorder := newStatsStreamRecorder()
	done := make(chan struct{})
	go func() {
		server.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=900&resolution=AUTO&client_id=test&since_generation=0", nil).WithContext(ctx))
		close(done)
	}()
	select {
	case <-recorder.heartbeat:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not emit a heartbeat")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not stop")
	}
	body := recorder.Body()
	if !strings.Contains(body, "event: heartbeat") {
		t.Fatalf("stream did not emit a heartbeat: %s", body)
	}
	if strings.Contains(body, "event: unavailable") {
		t.Fatalf("stream emitted unavailable after initial since_generation: %s", body)
	}
}

func TestStatsDeltaReturnsSnapshotAfterAppendAnd304WhenUnchanged(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	s := &server{statsPath: path}
	request := func(after int64) *http.Request {
		return httptest.NewRequest(http.MethodGet, "/api/stats-delta?range_seconds=300&resolution_seconds=1&client_id=test&after_cache_generation="+strconv.FormatInt(after, 10)+"&after_revision="+strconv.FormatInt(after, 10), nil)
	}
	first := httptest.NewRecorder()
	s.statsDelta(first, request(0))
	if first.Code != http.StatusNotModified {
		t.Fatalf("initial delta status = %d, want 304", first.Code)
	}
	if _, err := writer.AppendBatch(context.Background(), stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "delta-1", Family: "cpu", SourceID: "host", ObservedAt: float64(time.Now().Unix()), EpochID: "epoch", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 10},
	}}}); err != nil {
		t.Fatal(err)
	}
	changed := httptest.NewRecorder()
	s.statsDelta(changed, request(0))
	if changed.Code != http.StatusOK || !bytes.Contains(changed.Body.Bytes(), []byte(`"cache_generation":1`)) {
		t.Fatalf("changed delta = %d %s", changed.Code, changed.Body.String())
	}
	unchanged := httptest.NewRecorder()
	s.statsDelta(unchanged, request(1))
	if unchanged.Code != http.StatusNotModified {
		t.Fatalf("unchanged delta status = %d, want 304", unchanged.Code)
	}
}

func TestStatsStreamEmitsInitialEventsAndHeartbeat(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	writer.Close()
	s := &server{statsPath: path}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	record := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		s.statsStream(record, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=300&resolution=AUTO&client_id=browser-a", nil).WithContext(ctx))
		close(done)
	}()
	time.Sleep(250 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("stream did not stop")
	}
	body := record.Body.String()
	if !strings.Contains(body, "event: ack") || !strings.Contains(body, "event: snapshot") || !strings.Contains(body, "event: ready") || !strings.Contains(body, "event: heartbeat") {
		t.Fatalf("stream events = %s", body)
	}
}

func TestStatsStreamSnapshotMatchesBrowserWireContract(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	now := float64(time.Now().Unix()) - 1
	if _, err := writer.AppendBatch(context.Background(), stats.AppendBatch{
		Observations: []stats.Observation{{
			EventID: "wire-cpu", Family: "cpu", SourceID: "host", ObservedAt: now,
			EpochID: "wire-epoch", OwnerGeneration: 1,
			Payload: map[string]any{"process_percent": 10.0, "system_percent": 5.0},
		}},
	}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	s := &server{statsPath: path}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	recorder := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		s.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=300&resolution=AUTO&client_id=wire-test", nil).WithContext(ctx))
		close(done)
	}()
	time.Sleep(50 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not stop")
	}

	frames := parseSSEFrames(t, recorder.Body.String())
	var snapshot map[string]any
	for _, frame := range frames {
		if frame.event == "snapshot" {
			snapshot = frame.data
			break
		}
	}
	if snapshot == nil {
		t.Fatalf("snapshot frame missing: %s", recorder.Body.String())
	}
	if got, want := statsMapKeys(snapshot), []string{"buckets", "cache_generation", "cost_report", "generated_at", "no_data", "protocol_version", "range_seconds", "requested_resolution", "resolution_seconds", "rightmost_open", "source_generation", "window_end", "window_start"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("snapshot keys = %v, want %v", got, want)
	}
	buckets, ok := snapshot["buckets"].([]any)
	if !ok || len(buckets) == 0 {
		t.Fatalf("snapshot buckets = %#v", snapshot["buckets"])
	}
	for index, raw := range buckets {
		bucket, ok := raw.(map[string]any)
		if !ok {
			t.Fatalf("bucket %d = %#v", index, raw)
		}
		if _, ok := bucket["series"].(map[string]any); !ok {
			t.Fatalf("bucket %d series = %#v", index, bucket["series"])
		}
		open, ok := bucket["open"].(bool)
		if !ok || open != (index == len(buckets)-1) {
			t.Fatalf("bucket %d open = %#v", index, bucket["open"])
		}
	}
	cost, ok := snapshot["cost_report"].(map[string]any)
	if !ok || cost["schema_version"] != float64(4) {
		t.Fatalf("cost report = %#v", snapshot["cost_report"])
	}
	if _, ok := cost["dimensions"].(map[string]any); !ok {
		t.Fatalf("cost dimensions = %#v", cost["dimensions"])
	}
	if spans, ok := snapshot["no_data"].([]any); ok {
		for index, raw := range spans {
			span, ok := raw.(map[string]any)
			if !ok || span["family"] == nil || span["source_id"] == nil || span["epoch"] == nil || span["source_cadence_seconds"] == nil {
				t.Fatalf("no_data[%d] = %#v", index, raw)
			}
		}
	}
}

type sseFrame struct {
	event string
	data  map[string]any
}

func parseSSEFrames(t *testing.T, body string) []sseFrame {
	t.Helper()
	var frames []sseFrame
	for _, block := range strings.Split(strings.TrimSpace(body), "\n\n") {
		var event string
		var payload map[string]any
		for _, line := range strings.Split(block, "\n") {
			switch {
			case strings.HasPrefix(line, "event: "):
				event = strings.TrimPrefix(line, "event: ")
			case strings.HasPrefix(line, "data: "):
				if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &payload); err != nil {
					t.Fatal(err)
				}
			}
		}
		if event != "" && payload != nil {
			frames = append(frames, sseFrame{event: event, data: payload})
		}
	}
	return frames
}

func statsMapKeys(value map[string]any) []string {
	keys := make([]string, 0, len(value))
	for key := range value {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func TestStatsAgentStatusReadsSQLiteFixture(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	if err := os.WriteFile(path, []byte("not sqlite"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{statsPath: path}
	record := httptest.NewRecorder()
	s.statsAgentStatus(record, httptest.NewRequest(http.MethodGet, "/api/stats-agent-status", nil))
	if record.Code != http.StatusServiceUnavailable || !bytes.Contains(record.Body.Bytes(), []byte(`"available":false`)) {
		t.Fatalf("response = %d %s", record.Code, record.Body.String())
	}
}

func TestStatsAgentStatusReturnsSQLitePayload(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = writer.AppendBatch(context.Background(), stats.AppendBatch{
		Observations: []stats.Observation{{
			EventID: "status-1", Family: "agent_status", SourceID: "host", ObservedAt: float64(time.Now().Unix()),
			EpochID: "epoch", OwnerGeneration: 1,
			Payload: map[string]any{
				"counts": map[string]any{"total": 1},
				"agents": []any{map[string]any{"id": "agent-1"}},
			},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	s := &server{statsPath: path}
	record := httptest.NewRecorder()
	s.statsAgentStatus(record, httptest.NewRequest(http.MethodGet, "/api/stats-agent-status", nil))
	if record.Code != http.StatusOK || !bytes.Contains(record.Body.Bytes(), []byte(`"source_generation":1`)) || !bytes.Contains(record.Body.Bytes(), []byte(`"agent-1"`)) {
		t.Fatalf("response = %d %s", record.Code, record.Body.String())
	}
}

func TestStatsAgentStatusAddsTokenCollectionDiagnostics(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = writer.AppendBatch(context.Background(), stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "status-1", Family: "agent_status", SourceID: "host", ObservedAt: float64(time.Now().Unix()), EpochID: "epoch", OwnerGeneration: 1,
		Payload: map[string]any{"counts": map[string]any{"total": 1}},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	collector := stats.NewAgentTokenCollector(nil, nil, stats.AgentTokenCollectorConfig{})
	s := &server{statsPath: path, tokenCollector: collector}
	record := httptest.NewRecorder()
	s.statsAgentStatus(record, httptest.NewRequest(http.MethodGet, "/api/stats-agent-status", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("response = %d %s", record.Code, record.Body.String())
	}
	var payload map[string]json.RawMessage
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if _, ok := payload["token_collection"]; !ok {
		t.Fatalf("token_collection missing from response: %s", record.Body.String())
	}
	if _, ok := payload["token_rates"]; !ok {
		t.Fatalf("token_rates missing from response: %s", record.Body.String())
	}
}

func TestStatsSnapshotReturnsPersistedFacts(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	now := float64(time.Now().Unix()) - 1
	ended := now + 10
	_, err = writer.AppendBatch(ctx, stats.AppendBatch{
		Observations: []stats.Observation{{
			EventID: "cpu-1", Family: "cpu", SourceID: "host", ObservedAt: now,
			EpochID: "cpu-epoch", OwnerGeneration: 1,
			Payload: map[string]any{"process_percent": 37.5, "system_percent": 12.5},
		}},
		CoverageEpochs: []stats.CoverageEpoch{{
			Family: "cpu", SourceID: "host", EpochID: "cpu-epoch", StartedAt: now - 1,
			EndedAt: &ended, NativeCadenceSeconds: 1, OwnerGeneration: 1,
		}},
		UnavailableSpans: []stats.UnavailableSpan{{
			Family: "gpu", SourceID: "host", EpochID: "gpu-epoch", StartedAt: now - 2,
			EndedAt: now - 1, NativeCadenceSeconds: 1, Reason: "no driver", OwnerGeneration: 1,
		}},
		UsageAtoms: []stats.UsageAtom{{
			EventID: "usage-1", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: now,
			Payload: map[string]any{"quantity": 120.0, "agent_id": "agent-1", "model": "model-1", "provider": "provider-1", "micro_usd": 2400.0, "api_list_micro_usd": 3000.0},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	server := &server{statsPath: path}
	request := httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?range_seconds=300&resolution=1&client_id=fixture", nil)
	record := httptest.NewRecorder()
	server.statsSnapshot(record, request)
	if record.Code != http.StatusOK {
		t.Fatalf("status = %d body = %s", record.Code, record.Body.String())
	}
	var payload struct {
		SourceGeneration int64   `json:"source_generation"`
		CacheGeneration  int64   `json:"cache_generation"`
		GeneratedAt      float64 `json:"generated_at"`
		Buckets          []struct {
			Series map[string]struct {
				Value float64 `json:"value"`
			} `json:"series"`
		} `json:"buckets"`
		NoData     []map[string]any `json:"no_data"`
		CostReport struct {
			TotalTokens       int64 `json:"total_tokens"`
			TotalMicroUSD     int64 `json:"total_micro_usd"`
			TotalAPIListMicro int64 `json:"total_api_list_micro_usd"`
		} `json:"cost_report"`
	}
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.SourceGeneration != 1 || payload.CacheGeneration != 1 || payload.GeneratedAt <= 0 {
		t.Fatalf("generation metadata = %+v", payload)
	}
	var processCPU float64
	for _, bucket := range payload.Buckets {
		if series, ok := bucket.Series["process_cpu_percent"]; ok {
			processCPU = series.Value
		}
	}
	if processCPU != 37.5 {
		t.Fatalf("process CPU = %v, body = %s", processCPU, record.Body.String())
	}
	if payload.CostReport.TotalTokens != 120 || payload.CostReport.TotalMicroUSD != 2400 || payload.CostReport.TotalAPIListMicro != 3000 {
		t.Fatalf("cost report = %+v", payload.CostReport)
	}
	if len(payload.NoData) != 1 || payload.NoData[0]["reason"] != "no driver" {
		t.Fatalf("no-data = %+v", payload.NoData)
	}
}

func TestStatsSnapshotReturnsAgentStatusSeriesFromPersistedRows(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	now := float64(time.Now().Unix()) - 1
	if _, err = writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "agent-status-1", Family: "agent_status", SourceID: "host", ObservedAt: now,
		EpochID: "agent-status", OwnerGeneration: 1, Payload: map[string]any{
			"states":         map[string]any{"a": "ask", "b": "run", "c": "transition", "d": "idle"},
			"session_states": map[string]any{"session": "run"}, "snapshot_revision": 7,
		},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	record := httptest.NewRecorder()
	(&server{statsPath: path}).statsSnapshot(record, httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=fixture", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("status = %d body = %s", record.Code, record.Body.String())
	}
	var payload struct {
		Buckets []struct {
			Series map[string]struct {
				Value float64 `json:"value"`
			} `json:"series"`
		} `json:"buckets"`
	}
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	series := map[string]float64{}
	for _, bucket := range payload.Buckets {
		for name, item := range bucket.Series {
			series[name] = item.Value
		}
	}
	for _, name := range []string{"ask_agents", "run_agents", "transition_agents", "idle_agents"} {
		if series[name] != 1 {
			t.Fatalf("series[%q] = %v, all series = %#v", name, series[name], series)
		}
	}
}
