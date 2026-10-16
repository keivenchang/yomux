package main

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/testsupport"
)

// syntheticStatsEnvironment is the only environment builder used by this
// parity harness. Development and test processes must not inherit host
// product roots, collector inputs, or disable flags; each caller supplies its
// fixture-owned values explicitly. In particular, appending overrides to
// os.Environ is unsafe because exec.Cmd keeps the first duplicate key.
func syntheticStatsEnvironment(overrides ...string) []string {
	return testsupport.SyntheticEnvironment(overrides...)
}

func TestSyntheticStatsEnvironmentRemovesAmbientHostInputs(t *testing.T) {
	t.Setenv("HOME", "/host/home")
	t.Setenv("XDG_DATA_HOME", "/host/data")
	t.Setenv("CODEX_HOME", "/host/codex")
	t.Setenv("YOMUX_ROOT", "/host/yomux")
	t.Setenv("YOMUX_STATS_DISABLE_COLLECTORS", "0")
	t.Setenv("YOLOMUX_OPENCODE_DATABASE", "/host/opencode.db")
	t.Setenv("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", "/host/claude")
	t.Setenv("YOLOMUX_CODEX_TRANSCRIPT_ROOT", "/host/codex-sessions")
	t.Setenv("YOLOMUX_STATS_DISABLE_COLLECTORS", "0")
	environment := syntheticStatsEnvironment(
		"HOME=/fixture/home",
		"PYTHONPATH=/fixture/source",
		"YOLOMUX_STATS_DISABLE_COLLECTORS=1",
	)
	values := make(map[string][]string)
	for _, entry := range environment {
		key, value, ok := strings.Cut(entry, "=")
		if ok {
			values[key] = append(values[key], value)
		}
	}
	for _, key := range []string{
		"XDG_DATA_HOME", "CODEX_HOME", "YOMUX_ROOT", "YOMUX_STATS_DISABLE_COLLECTORS", "YOLOMUX_OPENCODE_DATABASE",
		"YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", "YOLOMUX_CODEX_TRANSCRIPT_ROOT",
	} {
		if _, present := values[key]; present {
			t.Fatalf("ambient host input %s survived synthetic environment: %v", key, values[key])
		}
	}
	for key, expected := range map[string]string{
		"HOME": "/fixture/home", "PYTHONPATH": "/fixture/source", "YOLOMUX_STATS_DISABLE_COLLECTORS": "1",
	} {
		if got := values[key]; !reflect.DeepEqual(got, []string{expected}) {
			t.Fatalf("synthetic override %s = %v, want [%q]", key, got, expected)
		}
	}
}

func checkStatsPythonAndExternalGoReceiveTheSameHTTPFixture(t *testing.T, binaryPath string) {
	ctx := context.Background()
	root := t.TempDir()
	configDir := filepath.Join(root, "config")
	if err := os.MkdirAll(configDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "auth.yaml"), []byte("users:\n  - username: alice\n    password: secret\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	now := time.Now().Truncate(time.Second)
	observations := []stats.Observation{
		{EventID: "external-cpu", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 5), EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 20.0, "system_percent": 40.0, "process_cpu_percent": map[string]any{"yolomux-go": 7.0, "python": 5.0}}},
		{EventID: "external-memory", Family: "system_memory", SourceID: "host", ObservedAt: float64(now.Unix() - 4), EpochID: "memory", OwnerGeneration: 1, Payload: map[string]any{"used_bytes": 100.0, "capacity_bytes": 200.0}},
	}
	coverage := []stats.CoverageEpoch{
		{Family: "cpu", SourceID: "host", EpochID: "cpu", StartedAt: float64(now.Unix() - 300), NativeCadenceSeconds: 1, OwnerGeneration: 1},
		{Family: "system_memory", SourceID: "host", EpochID: "memory", StartedAt: float64(now.Unix() - 300), NativeCadenceSeconds: 60, OwnerGeneration: 1},
	}
	atoms := []stats.UsageAtom{
		{EventID: "external-input", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: float64(now.Unix() - 3), Payload: map[string]any{"quantity": 10.0, "agent_id": "agent-a", "model": "gpt-5.6-luna", "provider": "openai", "execution_source": "codex", "telemetry_complete": true}},
		{EventID: "external-output", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: float64(now.Unix() - 2), Payload: map[string]any{"quantity": 5.0, "agent_id": "agent-a", "model": "gpt-5.6-luna", "provider": "openai", "execution_source": "codex", "telemetry_complete": true}},
		{EventID: "external-cache", Direction: "input", Modality: "text", CacheRole: "read", Unit: "tokens", ObservedAt: float64(now.Unix() - 1), Payload: map[string]any{"quantity": 7.0, "agent_id": "agent-a", "model": "gpt-5.6-luna", "provider": "openai", "execution_source": "codex", "telemetry_complete": true}},
	}
	databasePath := filepath.Join(root, "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: observations, CoverageEpochs: coverage, UsageAtoms: atoms}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	goURL, stopGo := startExternalGoStatsServer(t, binaryPath, root, configDir, databasePath)
	defer stopGo()
	query := "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=external-parity"
	goSnapshot := waitForAuthenticatedSnapshot(t, goURL+query)
	var goBody map[string]any
	if err := json.Unmarshal([]byte(goSnapshot.body), &goBody); err != nil {
		t.Fatalf("decode external Go snapshot: %v\n%s", err, goSnapshot.body)
	}
	observedUntil := goBody["observed_until"]
	if observedUntil == nil {
		observedUntil = goBody["generated_at"]
	}
	fixturePath := filepath.Join(root, "fixture.json")
	fixture := map[string]any{
		"observations": observations, "coverage": coverage, "atoms": atoms,
		"generated_at": goBody["generated_at"], "observed_until": observedUntil,
		"source_generation": goBody["source_generation"], "cache_generation": goBody["cache_generation"],
		"pricing_root": filepath.Join(root, "python-pricing"),
	}
	fixtureBytes, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(fixturePath, fixtureBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	pythonURL, stopPython := startPythonStatsParityServer(t, fixturePath)
	defer stopPython()
	pythonSnapshot := getRawResponseWithBasicAuth(t, pythonURL+query)
	if pythonSnapshot.status != http.StatusOK {
		t.Fatalf("Python parity snapshot status=%d body=%s", pythonSnapshot.status, pythonSnapshot.body)
	}
	var pythonBody map[string]any
	if err := json.Unmarshal([]byte(pythonSnapshot.body), &pythonBody); err != nil {
		t.Fatalf("decode Python parity snapshot: %v\n%s", err, pythonSnapshot.body)
	}
	for _, key := range []string{"protocol_version", "range_seconds", "resolution_seconds", "requested_resolution"} {
		if !reflect.DeepEqual(goBody[key], pythonBody[key]) {
			t.Fatalf("external snapshot %s mismatch: Go=%#v Python=%#v", key, goBody[key], pythonBody[key])
		}
	}
	// The transport comparison is intentionally wider than two representative
	// series. These fields are the complete rendered product for this fixture;
	// comparing them catches bucket completion, no-data ordering, provenance,
	// and cost-report drift at the compiled-server boundary.
	for _, key := range []string{"source_generation", "cache_generation", "buckets", "no_data", "cost_report"} {
		if !reflect.DeepEqual(goBody[key], pythonBody[key]) {
			t.Fatalf("external snapshot %s mismatch: Go=%#v Python=%#v", key, goBody[key], pythonBody[key])
		}
	}
	costReport, _ := goBody["cost_report"].(map[string]any)
	if totalTokens, _ := costReport["total_tokens"].(float64); totalTokens <= 0 {
		t.Fatalf("external Go cost report has no usage tokens: %#v", goBody["cost_report"])
	}
	if got, want := snapshotSeriesValue(goBody, "cpu_percent:host"), snapshotSeriesValue(pythonBody, "cpu_percent:host"); got != want || got != 20 {
		t.Fatalf("external CPU series mismatch: Go=%v Python=%v", got, want)
	}
	if got, want := snapshotSeriesValue(goBody, "system_cpu_percent"), snapshotSeriesValue(pythonBody, "system_cpu_percent"); got != want || got != 40 {
		t.Fatalf("external system CPU series mismatch: Go=%v Python=%v", got, want)
	}
	for _, series := range []struct {
		name string
		want float64
	}{
		{name: "process_cpu_percent:yolomux-go", want: 7},
		{name: "process_cpu_percent:python", want: 5},
	} {
		if got := snapshotSeriesValue(goBody, series.name); got != series.want {
			t.Fatalf("external Go top CPU series %s = %v, want %v", series.name, got, series.want)
		}
	}
	goCapabilities := getRawResponseWithBasicAuth(t, goURL+"/api/stats-capabilities")
	pythonCapabilities := getRawResponse(t, pythonURL+"/api/stats-capabilities")
	if goCapabilities.status != http.StatusOK || pythonCapabilities.status != http.StatusOK || normalizeReadyRequestID(goCapabilities.body) != normalizeReadyRequestID(pythonCapabilities.body) {
		t.Fatalf("external Python/Go capabilities mismatch\nGo: %+v\nPython: %+v", goCapabilities, pythonCapabilities)
	}
	// Keep malformed-query handling on the same authenticated boundary too. The
	// compiled server must reject the request before opening SQLite, with the
	// same status, content type, and typed unsupported envelope as Python.
	for _, path := range []string{
		"/api/stats-snapshot?range_seconds=300&resolution=AUTO&client_id=x&client_id=y",
		"/api/stats-snapshot?range_seconds=300&resolution=10&client_id=%ZZ",
		"/api/stats-delta?range_seconds=300&resolution_seconds=1&client_id=x&after_cache_generation=0&after_revision=0&client_id=y",
	} {
		goResponse := getRawResponseWithBasicAuth(t, goURL+path)
		pythonResponse := getRawResponse(t, pythonURL+path)
		if goResponse.status != pythonResponse.status || goResponse.contentType != pythonResponse.contentType || normalizeStatsParityError(goResponse.body) != pythonResponse.body {
			t.Fatalf("external malformed-query parity mismatch for %s\nGo: %+v\nPython: %+v", path, goResponse, pythonResponse)
		}
	}
}

func checkStatsProductionPythonAndGoReceiveTheSameHTTPFixture(t *testing.T, binaryPath string) {
	ctx := context.Background()
	root := t.TempDir()
	fixturePath := filepath.Join(root, "stats-v9.sqlite3")
	writer, err := stats.OpenWriter(ctx, fixturePath)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().Truncate(time.Second)
	observations := []stats.Observation{
		{EventID: "production-cpu", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 5), EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 20.0, "system_percent": 40.0}},
		{EventID: "production-memory", Family: "system_memory", SourceID: "host", ObservedAt: float64(now.Unix() - 4), EpochID: "memory", OwnerGeneration: 1, Payload: map[string]any{"used_bytes": 100.0, "capacity_bytes": 200.0}},
	}
	coverage := []stats.CoverageEpoch{
		{Family: "cpu", SourceID: "host", EpochID: "cpu", StartedAt: float64(now.Unix() - 300), NativeCadenceSeconds: 1, OwnerGeneration: 1},
		{Family: "system_memory", SourceID: "host", EpochID: "memory", StartedAt: float64(now.Unix() - 300), NativeCadenceSeconds: 60, OwnerGeneration: 1},
	}
	atoms := []stats.UsageAtom{
		{EventID: "production-input", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: float64(now.Unix() - 3), Payload: map[string]any{"quantity": 10.0, "agent_id": "agent-a", "model": "gpt-5.6-luna", "provider": "openai", "execution_source": "codex", "telemetry_complete": true}},
		{EventID: "production-output", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: float64(now.Unix() - 2), Payload: map[string]any{"quantity": 5.0, "agent_id": "agent-a", "model": "gpt-5.6-luna", "provider": "openai", "execution_source": "codex", "telemetry_complete": true}},
	}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: observations, CoverageEpochs: coverage, UsageAtoms: atoms}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	markFixtureMigrationReconciled(t, fixturePath, len(observations), len(coverage), len(atoms))

	goRoot, err := testsupport.MkdirShortTemp("yostats-go-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(goRoot) })
	goConfig := filepath.Join(goRoot, "config")
	goDatabase := filepath.Join(goRoot, "stats-v9.sqlite3")
	if err := os.MkdirAll(goConfig, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := copyStatsFixture(fixturePath, goDatabase); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(goConfig, "auth.yaml"), []byte("users:\n  - username: alice\n    password: secret\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	goURL, stopGo := startExternalGoStatsServer(t, binaryPath, goRoot, goConfig, goDatabase)
	defer stopGo()

	pythonRoot, err := testsupport.MkdirShortTemp("yostats-py-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(pythonRoot) })
	pythonDatabase := seedProductionPythonStatsDatabase(t, fixturePath, pythonRoot)
	pythonURL, stopPython := startExternalPythonStatsServer(t, pythonRoot)
	defer stopPython()

	query := "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=production-parity"
	goSnapshot := waitForAuthenticatedSnapshot(t, goURL+query)
	pythonSnapshot := waitForAuthenticatedSnapshot(t, pythonURL+query)
	t.Logf("production Python database=%s", pythonDatabase)
	var goBody, pythonBody map[string]any
	if err := json.Unmarshal([]byte(goSnapshot.body), &goBody); err != nil {
		t.Fatalf("decode production Go snapshot: %v\n%s", err, goSnapshot.body)
	}
	if err := json.Unmarshal([]byte(pythonSnapshot.body), &pythonBody); err != nil {
		t.Fatalf("decode production Python snapshot: %v\n%s", err, pythonSnapshot.body)
	}
	for _, key := range []string{"protocol_version", "range_seconds", "resolution_seconds", "requested_resolution", "cost_report"} {
		if !reflect.DeepEqual(goBody[key], pythonBody[key]) {
			t.Fatalf("production snapshot %s mismatch: Go=%#v Python=%#v", key, goBody[key], pythonBody[key])
		}
	}
	assertProductionNoDataTimingParity(t, goBody["no_data"], pythonBody["no_data"], "production snapshot")
	// The two independently scheduled materializers can straddle a 10-second
	// wall-clock boundary. Empty buckets then shift by one slot, but every
	// populated bucket and its series must remain identical; compare those
	// semantic buckets directly and retain a bounded timeline-size check.
	goBuckets := productionPopulatedBuckets(goBody["buckets"])
	pythonBuckets := productionPopulatedBuckets(pythonBody["buckets"])
	if !reflect.DeepEqual(goBuckets, pythonBuckets) {
		t.Fatalf("production snapshot populated buckets mismatch: Go=%#v Python=%#v", goBuckets, pythonBuckets)
	}
	if goAllBuckets, ok := goBody["buckets"].([]any); !ok || len(goAllBuckets) == 0 {
		t.Fatalf("production Go snapshot has no buckets: %#v", goBody["buckets"])
	} else if pythonAllBuckets, ok := pythonBody["buckets"].([]any); !ok || len(pythonAllBuckets) == 0 || absInt(len(goAllBuckets)-len(pythonAllBuckets)) > 1 {
		t.Fatalf("production bucket timeline lengths differ: Go=%T/%d Python=%T/%d", goBody["buckets"], len(goAllBuckets), pythonBody["buckets"], len(pythonAllBuckets))
	}
	if got := snapshotSeriesValue(goBody, "cpu_percent:host"); got != 20 {
		t.Fatalf("production Go CPU series = %v, want 20", got)
	}
	if got := snapshotSeriesValue(goBody, "system_cpu_percent"); got != 40 {
		t.Fatalf("production Go system CPU series = %v, want 40", got)
	}
	goCapabilities := getRawResponseWithBasicAuth(t, goURL+"/api/stats-capabilities")
	pythonCapabilities := getRawResponseWithBasicAuth(t, pythonURL+"/api/stats-capabilities")
	if goCapabilities.status != http.StatusOK || pythonCapabilities.status != http.StatusOK {
		t.Fatalf("production capabilities status: Go=%d Python=%d", goCapabilities.status, pythonCapabilities.status)
	}
	var goCapabilityBody, pythonCapabilityBody map[string]any
	if err := json.Unmarshal([]byte(goCapabilities.body), &goCapabilityBody); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(pythonCapabilities.body), &pythonCapabilityBody); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"protocol_version", "schema_generation", "ranges", "resolutions"} {
		if !reflect.DeepEqual(goCapabilityBody[key], pythonCapabilityBody[key]) {
			t.Fatalf("production capabilities %s mismatch: Go=%#v Python=%#v", key, goCapabilityBody[key], pythonCapabilityBody[key])
		}
	}
	// Exercise every advertised range and resolution, rather than letting one
	// representative 300/10 product stand in for the materializer matrix. The
	// two servers can take the request at different wall-clock instants, so
	// compare the populated semantic buckets and retain exact protocol, no-data,
	// and cost fields for each product.
	capabilityRanges, ok := goCapabilityBody["ranges"].([]any)
	if !ok || len(capabilityRanges) == 0 {
		t.Fatalf("production capabilities omit ranges: %#v", goCapabilityBody["ranges"])
	}
	for _, rawRange := range capabilityRanges {
		rangeEntry, ok := rawRange.(map[string]any)
		if !ok {
			t.Fatalf("production capability range is not an object: %#v", rawRange)
		}
		rangeSeconds, ok := rangeEntry["range_seconds"].(float64)
		if !ok || rangeSeconds <= 0 {
			t.Fatalf("production capability range is invalid: %#v", rangeEntry)
		}
		resolutions, ok := rangeEntry["explicit_resolution_seconds"].([]any)
		if !ok || len(resolutions) == 0 {
			t.Fatalf("production capability range omits explicit resolutions: %#v", rangeEntry)
		}
		for _, rawResolution := range resolutions {
			resolution, ok := rawResolution.(float64)
			if !ok || resolution <= 0 {
				t.Fatalf("production capability resolution is invalid: %#v", rawResolution)
			}
			query := fmt.Sprintf("/api/stats-snapshot?range_seconds=%d&resolution=%d&client_id=production-matrix", int(rangeSeconds), int(resolution))
			goMatrix := waitForAuthenticatedSnapshot(t, goURL+query)
			pythonMatrix := waitForAuthenticatedSnapshot(t, pythonURL+query)
			var goMatrixBody, pythonMatrixBody map[string]any
			if err := json.Unmarshal([]byte(goMatrix.body), &goMatrixBody); err != nil {
				t.Fatalf("decode production Go matrix snapshot %s: %v", query, err)
			}
			if err := json.Unmarshal([]byte(pythonMatrix.body), &pythonMatrixBody); err != nil {
				t.Fatalf("decode production Python matrix snapshot %s: %v", query, err)
			}
			for _, key := range []string{"protocol_version", "range_seconds", "resolution_seconds", "requested_resolution", "cost_report"} {
				if !reflect.DeepEqual(goMatrixBody[key], pythonMatrixBody[key]) {
					t.Fatalf("production matrix snapshot %s %s mismatch: Go=%#v Python=%#v", query, key, goMatrixBody[key], pythonMatrixBody[key])
				}
			}
			assertProductionNoDataTimingParity(t, goMatrixBody["no_data"], pythonMatrixBody["no_data"], query)
			if !reflect.DeepEqual(productionPopulatedBuckets(goMatrixBody["buckets"]), productionPopulatedBuckets(pythonMatrixBody["buckets"])) {
				t.Fatalf("production matrix populated buckets mismatch for %s: Go=%#v Python=%#v", query, productionPopulatedBuckets(goMatrixBody["buckets"]), productionPopulatedBuckets(pythonMatrixBody["buckets"]))
			}
		}
	}
	for _, path := range []string{"/api/pricing-catalog"} {
		goResponse := getRawResponseWithBasicAuth(t, goURL+path)
		pythonResponse := getRawResponseWithBasicAuth(t, pythonURL+path)
		if goResponse.status != pythonResponse.status || goResponse.contentType != pythonResponse.contentType || !reflect.DeepEqual(goResponse.headers, pythonResponse.headers) {
			t.Fatalf("production pricing status/header mismatch for %s\nGo: %+v\nPython: %+v", path, goResponse, pythonResponse)
		}
		if normalizeProductionResponseBody(goResponse.body) != normalizeProductionResponseBody(pythonResponse.body) {
			t.Fatalf("production pricing body mismatch for %s\nGo: %s\nPython: %s", path, normalizeProductionResponseBody(goResponse.body), normalizeProductionResponseBody(pythonResponse.body))
		}
	}
	// Error and reconnect responses are part of the production wire contract,
	// not just parser behavior. Compare the authenticated Python and compiled
	// Go owners before opening the stream; request IDs are process-local and are
	// normalized while status, content type, envelope, and reason remain exact.
	for _, path := range []string{
		"/api/stats-snapshot?range_seconds=300&resolution=10&client_id=production-parity&history=1",
		"/api/stats-snapshot?range_seconds=300&resolution=10&client_id=x&client_id=y",
		"/api/stats-snapshot?range_seconds=300&resolution=%ZZ&client_id=x",
		"/api/stats-delta?range_seconds=300&resolution_seconds=10&client_id=production-parity&after_cache_generation=0&after_revision=0&client_id=y",
		"/api/stats-stream?range_seconds=300&resolution=10&client_id=production-parity&history=1",
	} {
		goResponse := getRawResponseWithBasicAuth(t, goURL+path)
		pythonResponse := getRawResponseWithBasicAuth(t, pythonURL+path)
		if goResponse.status != pythonResponse.status || goResponse.contentType != pythonResponse.contentType {
			t.Fatalf("production HTTP status/header mismatch for %s\nGo: %+v\nPython: %+v", path, goResponse, pythonResponse)
		}
		goBody := normalizeProductionResponseBody(goResponse.body)
		pythonBody := normalizeProductionResponseBody(pythonResponse.body)
		if goBody != pythonBody {
			t.Fatalf("production HTTP envelope mismatch for %s\nGo: %s\nPython: %s", path, goBody, pythonBody)
		}
	}
	// A fresh delta cursor must produce the same first product as Python. This
	// exercises the compiled server's revision/base-generation framing rather
	// than only its snapshot projector.
	deltaQuery := "/api/stats-delta?range_seconds=300&resolution_seconds=10&client_id=production-parity&after_cache_generation=0&after_revision=0"
	goDelta := getRawResponseWithBasicAuth(t, goURL+deltaQuery)
	pythonDelta := getRawResponseWithBasicAuth(t, pythonURL+deltaQuery)
	if goDelta.status != pythonDelta.status || goDelta.contentType != pythonDelta.contentType || normalizeProductionResponseBody(goDelta.body) != normalizeProductionResponseBody(pythonDelta.body) {
		t.Fatalf("production delta mismatch\nGo: %+v\nPython: %+v", goDelta, pythonDelta)
	}
	// The matrix above intentionally warms several products, including the
	// one-second ring. Those products can legitimately publish while this test
	// is running, so take a fresh same-key snapshot immediately before probing
	// the not-modified cursor. This keeps the check about cursor semantics rather
	// than about a stale generation crossing a real bucket boundary.
	probeQuery := "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=production-parity"
	goProbe := waitForAuthenticatedSnapshot(t, goURL+probeQuery)
	pythonProbe := waitForAuthenticatedSnapshot(t, pythonURL+probeQuery)
	var goProbeBody, pythonProbeBody map[string]any
	if err := json.Unmarshal([]byte(goProbe.body), &goProbeBody); err != nil {
		t.Fatalf("decode production Go probe snapshot: %v", err)
	}
	if err := json.Unmarshal([]byte(pythonProbe.body), &pythonProbeBody); err != nil {
		t.Fatalf("decode production Python probe snapshot: %v", err)
	}
	var snapshotGeneration float64
	if value, ok := goProbeBody["cache_generation"].(float64); !ok || value <= 0 {
		t.Fatalf("production probe snapshot cache generation = %#v", goProbeBody["cache_generation"])
	} else {
		snapshotGeneration = value
	}
	pythonGeneration, ok := pythonProbeBody["cache_generation"].(float64)
	if !ok || pythonGeneration <= 0 {
		t.Fatalf("production Python probe snapshot cache generation = %#v", pythonProbeBody["cache_generation"])
	}
	goNotModifiedQuery := "/api/stats-delta?range_seconds=300&resolution_seconds=10&client_id=production-parity&after_cache_generation=" + strconv.FormatInt(int64(snapshotGeneration), 10) + "&after_revision=0"
	pythonNotModifiedQuery := "/api/stats-delta?range_seconds=300&resolution_seconds=10&client_id=production-parity&after_cache_generation=" + strconv.FormatInt(int64(pythonGeneration), 10) + "&after_revision=0"
	goNotModified := getRawResponseWithBasicAuth(t, goURL+goNotModifiedQuery)
	pythonNotModified := getRawResponseWithBasicAuth(t, pythonURL+pythonNotModifiedQuery)
	if goNotModified.status != pythonNotModified.status || goNotModified.contentType != pythonNotModified.contentType || normalizeProductionResponseBody(goNotModified.body) != normalizeProductionResponseBody(pythonNotModified.body) {
		t.Fatalf("production not-modified delta mismatch\nGo: %+v\nPython: %+v", goNotModified, pythonNotModified)
	}
	// A cursor ahead of the retained revision must fail closed with the same
	// repair-required response on both owners; silently treating it as a fresh
	// delta would let a reconnect apply an invalid base.
	repairQuery := "/api/stats-delta?range_seconds=300&resolution_seconds=10&client_id=production-parity&after_cache_generation=1&after_revision=999999"
	goRepair := getRawResponseWithBasicAuth(t, goURL+repairQuery)
	pythonRepair := getRawResponseWithBasicAuth(t, pythonURL+repairQuery)
	if goRepair.status != pythonRepair.status || goRepair.contentType != pythonRepair.contentType || normalizeProductionResponseBody(goRepair.body) != normalizeProductionResponseBody(pythonRepair.body) {
		t.Fatalf("production repair response mismatch\nGo: %+v\nPython: %+v", goRepair, pythonRepair)
	}
	// The preceding snapshot request warms the explicit 10-second product. Use
	// that same product here so this transport comparison measures Python/Go
	// framing rather than Go's intentionally bounded cold-ring pending events.
	streamQuery := "/api/stats-stream?range_seconds=300&resolution=10&client_id=production-parity"
	goSSE := getInitialSSEWithBasicAuth(t, goURL+streamQuery)
	pythonSSE := getInitialSSEWithBasicAuth(t, pythonURL+streamQuery)
	if goSSE.status != pythonSSE.status || goSSE.contentType != pythonSSE.contentType || !reflect.DeepEqual(goSSE.headers, pythonSSE.headers) {
		t.Fatalf("production SSE headers mismatch\nGo: %+v\nPython: %+v", goSSE, pythonSSE)
	}
	goSSEBody := normalizeProductionSSE(goSSE.body)
	pythonSSEBody := normalizeProductionSSE(pythonSSE.body)
	if goSSEBody != pythonSSEBody {
		t.Fatalf("production SSE initial frames mismatch\nGo: %s\nPython: %s", goSSEBody, pythonSSEBody)
	}
	// Each live publisher owns its generation. Reconnect from the accepted SSE
	// cursor, and validate replay against the returned generation; a publication
	// between requests can advance either server independently.
	goCursor := productionSSEGeneration(t, goSSE.body)
	pythonCursor := productionSSEGeneration(t, pythonSSE.body)
	goReconnectQuery := streamQuery + "&since_generation=" + strconv.FormatInt(int64(goCursor), 10)
	pythonReconnectQuery := streamQuery + "&since_generation=" + strconv.FormatInt(int64(pythonCursor), 10)
	goReconnect := getInitialSSEWithBasicAuth(t, goURL+goReconnectQuery)
	pythonReconnect := getInitialSSEWithBasicAuth(t, pythonURL+pythonReconnectQuery)
	if goReconnect.status != pythonReconnect.status || goReconnect.contentType != pythonReconnect.contentType || !reflect.DeepEqual(goReconnect.headers, pythonReconnect.headers) {
		t.Fatalf("production SSE reconnect headers mismatch\nGo: %+v\nPython: %+v", goReconnect, pythonReconnect)
	}
	goUnchanged := assertProductionSSEReconnect(t, goCursor, goSSE.body, goReconnect.body)
	pythonUnchanged := assertProductionSSEReconnect(t, pythonCursor, pythonSSE.body, pythonReconnect.body)
	if goUnchanged == pythonUnchanged && normalizeProductionSSE(goReconnect.body) != normalizeProductionSSE(pythonReconnect.body) {
		t.Fatalf("production SSE reconnect frames mismatch\nGo: %s\nPython: %s", normalizeProductionSSE(goReconnect.body), normalizeProductionSSE(pythonReconnect.body))
	}
}

// TestStatsProductionPythonAndGoObservationBoundaryParity compares the real
// Python and compiled Go HTTP owners at the browser-upload boundary.  The
// parser-only corpus is useful for fast feedback, but it cannot catch route
// status/header drift, writer readiness, duplicate receipts, or persistence
// differences between the two serving processes.
func checkStatsProductionPythonAndGoObservationBoundaryParity(t *testing.T, binaryPath string) {
	root, err := testsupport.MkdirShortTemp("yostats-observation-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	fixturePath := filepath.Join(root, "stats-v9.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), fixturePath)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	markFixtureMigrationReconciled(t, fixturePath, 0, 0, 0)

	goRoot, err := testsupport.MkdirShortTemp("yostats-observation-go-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(goRoot) })
	goConfig := filepath.Join(goRoot, "config")
	goDatabase := filepath.Join(goRoot, "stats-v9.sqlite3")
	if err := os.MkdirAll(goConfig, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := copyStatsFixture(fixturePath, goDatabase); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(goConfig, "auth.yaml"), []byte("users:\n  - username: alice\n    password: secret\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	goURL, stopGo := startExternalGoStatsServer(t, binaryPath, goRoot, goConfig, goDatabase)
	defer stopGo()

	pythonRoot, err := testsupport.MkdirShortTemp("yostats-observation-py-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(pythonRoot) })
	pythonDatabase := seedProductionPythonStatsDatabase(t, fixturePath, pythonRoot)
	pythonURL, stopPython := startExternalPythonStatsServer(t, pythonRoot)
	defer stopPython()

	// Force both owners through the same authenticated readiness path before
	// exercising uploads. This prevents a cold statsd response from being
	// mistaken for an observation-parser mismatch.
	for _, base := range []string{goURL, pythonURL} {
		_ = waitForAuthenticatedSnapshot(t, base+"/api/stats-snapshot?range_seconds=300&resolution=10&client_id=observation-parity")
	}

	valid := `{"protocol_version":25,"schema_generation":9,"client_id":"browser-a","observations":[{"event_id":"valid-event","family":"browser","source_id":"browser-a","observed_at":2000,"epoch_id":"epoch","payload":{"kind":"error","signature":"jsf-deadbeef","message":"Bearer abc-token password: plain-secret","source":"/app.js","status":503,"delivery_outcome":"failed"}}]}`
	cases := []struct {
		name string
		body string
	}{
		{name: "valid", body: valid},
		{name: "protocol", body: strings.Replace(valid, `"protocol_version":25`, `"protocol_version":24`, 1)},
		{name: "schema", body: strings.Replace(valid, `"schema_generation":9`, `"schema_generation":8`, 1)},
		{name: "wrong-family", body: strings.Replace(valid, `"family":"browser"`, `"family":"cpu"`, 1)},
		{name: "wrong-client", body: strings.Replace(valid, `"source_id":"browser-a"`, `"source_id":"other-client"`, 1)},
		{name: "extra-root-field", body: strings.Replace(valid, `{"protocol_version"`, `{"extra":true,"protocol_version"`, 1)},
		{name: "malformed-json", body: valid[:len(valid)-1]},
		{name: "trailing-json", body: valid + " {}"},
		{name: "empty-observations", body: strings.Replace(valid, `[{"event_id":"valid-event","family":"browser","source_id":"browser-a","observed_at":2000,"epoch_id":"epoch","payload":{"kind":"error","signature":"jsf-deadbeef","message":"Bearer abc-token password: plain-secret","source":"/app.js","status":503,"delivery_outcome":"failed"}}]`, `[]`, 1)},
		{name: "negative-timestamp", body: strings.Replace(valid, `"observed_at":2000`, `"observed_at":-1`, 1)},
		{name: "missing-payload-kind", body: strings.Replace(valid, `"kind":"error",`, ``, 1)},
		{name: "duplicate-event-ids", body: strings.Replace(valid, `]}`, `,{"event_id":"valid-event","family":"browser","source_id":"browser-a","observed_at":2001,"epoch_id":"epoch","payload":{"kind":"warning","signature":"jsf-duplicate","message":"duplicate","source":"/app.js"}}]}`, 1)},
		{name: "client-id-control", body: strings.Replace(valid, `"client_id":"browser-a"`, `"client_id":"browser-a\u0001"`, 1)},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			goResponse := postRawResponseWithBasicAuth(t, goURL+"/api/stats-observations", []byte(item.body))
			pythonResponse := postRawResponseWithBasicAuth(t, pythonURL+"/api/stats-observations", []byte(item.body))
			if goResponse.status != pythonResponse.status || goResponse.contentType != pythonResponse.contentType || !reflect.DeepEqual(goResponse.headers, pythonResponse.headers) {
				t.Fatalf("observation %s status/header mismatch\nGo: %+v\nPython: %+v", item.name, goResponse, pythonResponse)
			}
			if canonicalObservationResponse(goResponse.body) != canonicalObservationResponse(pythonResponse.body) {
				t.Fatalf("observation %s body mismatch\nGo: %s\nPython: %s", item.name, canonicalObservationResponse(goResponse.body), canonicalObservationResponse(pythonResponse.body))
			}
		})
	}

	// A second upload of the same accepted event must be a duplicate on both
	// owners, with one durable row rather than two.
	goDuplicate := postRawResponseWithBasicAuth(t, goURL+"/api/stats-observations", []byte(valid))
	pythonDuplicate := postRawResponseWithBasicAuth(t, pythonURL+"/api/stats-observations", []byte(valid))
	if goDuplicate.status != http.StatusOK || pythonDuplicate.status != http.StatusOK || !reflect.DeepEqual(goDuplicate.headers, pythonDuplicate.headers) ||
		canonicalObservationResponse(goDuplicate.body) != canonicalObservationResponse(pythonDuplicate.body) {
		t.Fatalf("duplicate observation mismatch\nGo: %+v\nPython: %+v", goDuplicate, pythonDuplicate)
	}
	if got := countObservationRows(t, goDatabase); got != 1 {
		t.Fatalf("Go persisted browser rows = %d, want 1", got)
	}
	if got := countObservationRows(t, pythonDatabase); got != 1 {
		t.Fatalf("Python persisted browser rows = %d, want 1", got)
	}
	for name, path := range map[string]string{"Go": goDatabase, "Python": pythonDatabase} {
		payload := browserObservationPayload(t, path)
		if strings.Contains(payload, "abc-token") || strings.Contains(payload, "plain-secret") || !strings.Contains(payload, "[redacted-secret]") {
			t.Fatalf("%s persisted observation was not sanitized: %q", name, payload)
		}
	}

	overSized := `{"protocol_version":25,"schema_generation":9,"client_id":"browser-a","observations":[{"event_id":"oversized","family":"browser","source_id":"browser-a","observed_at":2001,"epoch_id":"epoch","payload":{"kind":"warning","message":"` + strings.Repeat("x", 130*1024) + `"}}]}`
	goResponse := postRawResponseWithBasicAuth(t, goURL+"/api/stats-observations", []byte(overSized))
	pythonResponse := postRawResponseWithBasicAuth(t, pythonURL+"/api/stats-observations", []byte(overSized))
	if goResponse.status != pythonResponse.status || goResponse.contentType != pythonResponse.contentType || !reflect.DeepEqual(goResponse.headers, pythonResponse.headers) ||
		canonicalObservationResponse(goResponse.body) != canonicalObservationResponse(pythonResponse.body) {
		t.Fatalf("oversized observation mismatch\nGo: %+v\nPython: %+v", goResponse, pythonResponse)
	}
}

func canonicalObservationResponse(body string) string {
	var payload map[string]any
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		return body
	}
	// Writer generations are process-local identities. Every other response
	// field, including status/reason/receipts, remains part of the parity claim.
	delete(payload, "source_generation")
	if request, ok := payload["request"].(map[string]any); ok {
		request["id"] = "<request-id>"
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return body
	}
	return string(encoded)
}

func postRawResponseWithBasicAuth(t *testing.T, rawURL string, body []byte) rawHTTPResponse {
	t.Helper()
	request, err := http.NewRequest(http.MethodPost, rawURL, bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.SetBasicAuth("alice", "secret")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	data, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	return rawHTTPResponse{status: response.StatusCode, contentType: response.Header.Get("Content-Type"), headers: responseHeaders(response), body: string(data)}
}

func responseHeaders(response *http.Response) map[string]string {
	headers := map[string]string{}
	for _, name := range []string{"Cache-Control", "Connection", "X-Accel-Buffering", "Retry-After"} {
		if value := response.Header.Get(name); value != "" {
			headers[name] = value
		}
	}
	return headers
}

func countObservationRows(t *testing.T, path string) int {
	t.Helper()
	database, err := sql.Open("sqlite3", "file:"+filepath.ToSlash(path)+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	var count int
	if err := database.QueryRow("SELECT COUNT(*) FROM observations WHERE family = 'browser'").Scan(&count); err != nil {
		t.Fatal(err)
	}
	return count
}

func browserObservationPayload(t *testing.T, path string) string {
	t.Helper()
	database, err := sql.Open("sqlite3", "file:"+filepath.ToSlash(path)+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	var payload string
	if err := database.QueryRow("SELECT payload_json FROM observations WHERE family = 'browser' LIMIT 1").Scan(&payload); err != nil {
		t.Fatal(err)
	}
	return payload
}

func productionSSEGeneration(t *testing.T, body string) float64 {
	t.Helper()
	frames := parseSSEFrames(t, body)
	if len(frames) < 2 || frames[0].event != "ack" || frames[len(frames)-1].event != "ready" {
		t.Fatalf("invalid SSE frame sequence: %s", body)
	}
	generation, ok := frames[0].data["cache_generation"].(float64)
	if !ok || generation <= 0 || frames[len(frames)-1].data["cache_generation"] != generation {
		t.Fatalf("invalid SSE generation: %s", body)
	}
	return generation
}

func assertProductionSSEReconnect(t *testing.T, cursor float64, initial, body string) bool {
	t.Helper()
	generation := productionSSEGeneration(t, body)
	frames := parseSSEFrames(t, body)
	unchanged := generation == cursor
	if generation < cursor || frames[0].data["not_modified"] != unchanged {
		t.Fatalf("reconnect cursor %v disagrees with ack: %s", cursor, body)
	}
	snapshots := frames[1 : len(frames)-1]
	want := 0
	if !unchanged {
		want = 1
	}
	if len(snapshots) != want {
		t.Fatalf("reconnect cursor %v has %d snapshots, want %d: %s", cursor, len(snapshots), want, body)
	}
	for _, frame := range snapshots {
		if frame.event != "snapshot" || frame.data["cache_generation"] != generation {
			t.Fatalf("reconnect snapshot differs from ack: %s", body)
		}
	}
	// The fixture has no live collectors. Only wall-clock bucket boundaries and
	// generations may change; replay must preserve the accepted populated data.
	expected := parseSSEFrames(t, initial)
	expected[0].data["not_modified"] = unchanged
	if unchanged {
		expected = []sseFrame{expected[0], expected[len(expected)-1]}
	}
	normalized := make([]string, 2)
	for index, sequence := range [][]sseFrame{expected, frames} {
		var wire strings.Builder
		for _, frame := range sequence {
			data, err := json.Marshal(frame.data)
			if err != nil {
				t.Fatal(err)
			}
			fmt.Fprintf(&wire, "event: %s\ndata: %s\n\n", frame.event, data)
		}
		normalized[index] = normalizeProductionSSE(wire.String())
	}
	if normalized[0] != normalized[1] {
		t.Fatalf("reconnect payload changed for cursor %v\nAccepted: %s\nReconnect: %s", cursor, normalized[0], normalized[1])
	}
	return unchanged
}

func normalizeProductionSSE(body string) string {
	lines := strings.Split(body, "\n")
	event := ""
	for index, line := range lines {
		if strings.HasPrefix(line, "event: ") {
			event = strings.TrimPrefix(line, "event: ")
			continue
		}
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var payload any
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &payload); err != nil {
			continue
		}
		object, ok := payload.(map[string]any)
		if !ok {
			continue
		}
		if event == "ack" || event == "ready" || event == "snapshot" {
			if _, present := object["cache_generation"]; present {
				object["cache_generation"] = "<cache-generation>"
			}
		}
		if event == "snapshot" {
			normalizeProductionSSESnapshot(object)
		}
		encoded, err := json.Marshal(object)
		if err == nil {
			lines[index] = "data: " + string(encoded)
		}
	}
	return normalizeSSEIDs(strings.Join(lines, "\n"))
}

// normalizeProductionSSESnapshot removes only values that depend on the two
// independently scheduled test processes crossing a live bucket boundary.
// The populated bucket payload, source counts, series values, and cost report
// remain exact parity requirements.  HTTP snapshot parity separately checks
// no-data spans and populated bucket starts.  The stream request is made after
// that snapshot probe, so the two independently scheduled servers can cross a
// ten-second boundary between the probes; omitting no-data here prevents that
// wall-clock race from masquerading as an SSE framing mismatch.
func normalizeProductionSSESnapshot(object map[string]any) {
	if _, present := object["generated_at"]; present {
		object["generated_at"] = "<generated-at>"
	}
	if _, present := object["window_start"]; present {
		object["window_start"] = "<window-start>"
	}
	if _, present := object["window_end"]; present {
		object["window_end"] = "<window-end>"
	}
	delete(object, "no_data")
	// rightmost_open is a property of the sampled wall-clock edge.  The
	// semantic stream contract is the populated product, whose open/closed
	// state is covered by the direct snapshot gate.
	delete(object, "rightmost_open")
	buckets, ok := object["buckets"].([]any)
	if !ok {
		return
	}
	populated := make([]any, 0, len(buckets))
	for _, raw := range buckets {
		bucket, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		series, _ := bucket["series"].(map[string]any)
		source, _ := bucket["source"].(map[string]any)
		count, _ := source["count"].(float64)
		if len(series) == 0 && count == 0 {
			continue
		}
		delete(bucket, "open")
		delete(bucket, "start")
		populated = append(populated, bucket)
	}
	object["buckets"] = populated
}

func snapshotSeriesValue(snapshot map[string]any, key string) float64 {
	buckets, _ := snapshot["buckets"].([]any)
	for _, rawBucket := range buckets {
		bucket, _ := rawBucket.(map[string]any)
		series, _ := bucket["series"].(map[string]any)
		point, _ := series[key].(map[string]any)
		value, _ := point["value"].(float64)
		if point != nil {
			return value
		}
	}
	return 0
}

// productionNoDataShape validates the only timing-sensitive no-data form in
// the live production fixture. The two servers build their current ring on
// independent clocks, so an open persisted bucket may be historical for one
// request and still the right edge for the other. Real unavailable spans are
// not permitted by this fixture and therefore remain a hard test failure.
func productionNoDataShape(value any) (string, error) {
	spans, ok := value.([]any)
	if !ok {
		return "", fmt.Errorf("no_data is %T, want array", value)
	}
	if len(spans) == 0 {
		return "empty", nil
	}
	expectedFamilies := map[string]struct{}{
		"agent_status": {}, "agent_tokens": {}, "browser": {}, "cost": {},
		"cpu": {}, "gpu": {}, "service_load": {}, "system_memory": {}, "system_disk": {},
	}
	if len(spans) != len(expectedFamilies) {
		return "", fmt.Errorf("unexpected persisted-gap span count %d", len(spans))
	}
	seen := make(map[string]struct{}, len(spans))
	for _, raw := range spans {
		span, ok := raw.(map[string]any)
		if !ok {
			return "", fmt.Errorf("no_data span is %T", raw)
		}
		family, _ := span["family"].(string)
		if _, ok := expectedFamilies[family]; !ok {
			return "", fmt.Errorf("unexpected no_data family %q", family)
		}
		if _, duplicate := seen[family]; duplicate {
			return "", fmt.Errorf("duplicate no_data family %q", family)
		}
		seen[family] = struct{}{}
		if span["source_id"] != "persisted-ring" || span["reason"] != "incomplete_persisted_bucket" {
			return "", fmt.Errorf("unexpected persisted-gap provenance: %#v", span)
		}
		start, startOK := span["start"].(float64)
		end, endOK := span["end"].(float64)
		cadence, cadenceOK := span["source_cadence_seconds"].(float64)
		if !startOK || !endOK || !cadenceOK || cadence <= 0 || end <= start {
			return "", fmt.Errorf("invalid persisted-gap bounds: %#v", span)
		}
		bucketCount := (end - start) / cadence
		if bucketCount < 1 || math.Abs(bucketCount-math.Round(bucketCount)) > 1e-6 {
			return "", fmt.Errorf("invalid persisted-gap bounds: %#v", span)
		}
	}
	if len(seen) != len(expectedFamilies) {
		return "", fmt.Errorf("missing persisted-gap family: got %v", seen)
	}
	return "persisted-gap", nil
}

func assertProductionNoDataTimingParity(t *testing.T, goValue, pythonValue any, context string) {
	t.Helper()
	goShape, goErr := productionNoDataShape(goValue)
	pythonShape, pythonErr := productionNoDataShape(pythonValue)
	if goErr != nil || pythonErr != nil {
		t.Fatalf("%s invalid no_data shape: Go=%q/%v Python=%q/%v", context, goShape, goErr, pythonShape, pythonErr)
	}
	// Empty versus persisted-gap, or two persisted gaps at adjacent live
	// boundaries, is expected when independently scheduled servers cross a
	// resolution boundary. The exact no-data payload is checked by each owner
	// above; this assertion rejects every other divergence.
}

func productionPopulatedBuckets(value any) []any {
	buckets, ok := value.([]any)
	if !ok {
		return nil
	}
	result := make([]any, 0, len(buckets))
	for _, raw := range buckets {
		bucket, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		series, _ := bucket["series"].(map[string]any)
		source, _ := bucket["source"].(map[string]any)
		count, _ := source["count"].(float64)
		if len(series) == 0 && count == 0 {
			continue
		}
		// The two external servers take their first request at slightly different
		// instants; the right-edge bucket may therefore be marked open by one
		// owner and closed by the other. Its populated series are the parity
		// contract, while this scheduling bit is covered by the live stream tests.
		delete(bucket, "open")
		result = append(result, bucket)
	}
	return result
}

func absInt(value int) int {
	if value < 0 {
		return -value
	}
	return value
}

func copyStatsFixture(source, destination string) error {
	// The Go writer intentionally uses WAL mode, so a byte-for-byte copy of the
	// main file can omit committed facts that still live in the source -wal
	// sidecar. Checkpoint while the fixture is quiescent before copying; both
	// production servers then open the same durable database image rather than
	// accidentally receiving different subsets of the fixture.
	database, err := sql.Open("sqlite3", "file:"+filepath.ToSlash(source))
	if err != nil {
		return err
	}
	database.SetMaxOpenConns(1)
	if _, err := database.Exec("PRAGMA wal_checkpoint(TRUNCATE)"); err != nil {
		_ = database.Close()
		return fmt.Errorf("checkpoint stats fixture: %w", err)
	}
	if err := database.Close(); err != nil {
		return fmt.Errorf("close stats fixture: %w", err)
	}
	data, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		return err
	}
	return os.WriteFile(destination, data, 0o600)
}

func markFixtureMigrationReconciled(t *testing.T, path string, observations, coverage, atoms int) {
	t.Helper()
	database, err := sql.Open("sqlite3", "file:"+filepath.ToSlash(path))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	details := fmt.Sprintf(`{"format":1,"counts":{"observations":%d,"coverage_epochs":%d,"usage_atoms":%d,"unavailable_spans":0},"issues":[],"issue_counts":{},"issues_truncated":0,"sources":[]}`, observations, coverage, atoms)
	if _, err := database.Exec(`INSERT OR REPLACE INTO migration_reconciliation(migration_id, completed_at, source_digest, details_json) VALUES (?, ?, ?, ?)`, "stats-v5-current-only-v1", float64(time.Now().Unix()), strings.Repeat("0", 64), details); err != nil {
		t.Fatalf("mark fixture migration reconciled: %v", err)
	}
}

func seedProductionPythonStatsDatabase(t *testing.T, fixturePath, root string) string {
	t.Helper()
	repoRoot := testsupport.RepoRoot(t)
	script := `import os, shutil, sqlite3
from yomux_lib.stats_current.storage import default_database_path

destination = default_database_path()
destination.parent.mkdir(parents=True, exist_ok=True)
source = os.environ["YOMUX_STATS_FIXTURE"]
# The fixture was written by the Go owner in WAL mode. Checkpoint its committed
# rows before copying only the main file; otherwise Python would receive an
# empty base database while the source reader still sees facts in -wal.
with sqlite3.connect(source) as connection:
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
shutil.copyfile(source, destination)
print(destination)
`
	command := exec.Command("python3", "-c", script)
	command.Dir = repoRoot
	command.Env = syntheticStatsEnvironment("PYTHONPATH="+repoRoot, "YOMUX_ROOT="+root, "YOMUX_STATS_FIXTURE="+fixturePath)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("seed production Python stats database: %v\n%s", err, output)
	} else {
		t.Logf("seeded production Python database at %s", strings.TrimSpace(string(output)))
		return strings.TrimSpace(string(output))
	}
	return ""
}

type synchronizedBuffer struct {
	mu sync.Mutex
	bytes.Buffer
}

func (buffer *synchronizedBuffer) Write(value []byte) (int, error) {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.Buffer.Write(value)
}

func (buffer *synchronizedBuffer) ReadFrom(source io.Reader) (int64, error) {
	var total int64
	var chunk [32 * 1024]byte
	for {
		count, err := source.Read(chunk[:])
		if count > 0 {
			written, writeErr := buffer.Write(chunk[:count])
			total += int64(written)
			if writeErr != nil {
				return total, writeErr
			}
		}
		if err != nil {
			if err == io.EOF {
				return total, nil
			}
			return total, err
		}
	}
}

func (buffer *synchronizedBuffer) String() string {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.Buffer.String()
}

func startExternalPythonStatsServer(t *testing.T, root string) (string, func()) {
	t.Helper()
	repoRoot := testsupport.RepoRoot(t)
	configDir := filepath.Join(root, "config")
	if err := os.MkdirAll(configDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "auth.yaml"), []byte("users:\n  - username: alice\n    password: secret\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	command := exec.Command("python3", "yomux.py", "--backend", "python", "--http", "--host", "127.0.0.1", "--port", strconv.Itoa(port), "--sessions")
	command.Dir = repoRoot
	command.Env = syntheticStatsEnvironment("PYTHONPATH="+repoRoot, "YOMUX_ROOT="+root, "YOMUX_STATS_DISABLE_COLLECTORS=1", "YOMUX_TEST_DISABLE_PRICING_REFRESH=1", "PYTHONDONTWRITEBYTECODE=1", "YOMUX_TEST_AUTH_BYPASS=1")
	var output synchronizedBuffer
	command.Stdout = &output
	command.Stderr = &output
	process := startParityProcess(t, command, &output)
	baseURL := fmt.Sprintf("http://127.0.0.1:%d", port)
	deadline := time.Now().Add(parityServerTimeout())
	for time.Now().Before(deadline) {
		if process.exited() {
			t.Fatalf("production Python server exited before health check: %s", output.String())
		}
		response, requestErr := http.Get(baseURL + "/healthz")
		if requestErr == nil {
			_, _ = io.Copy(io.Discard, response.Body)
			_ = response.Body.Close()
			if response.StatusCode == http.StatusOK {
				t.Logf("production Python server output before health: %s", output.String())
				return baseURL, func() { process.checkStop(t) }
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	process.checkStop(t)
	t.Fatalf("production Python server did not become healthy: %s", output.String())
	return "", func() {}
}

func startExternalGoStatsServer(t *testing.T, binaryPath, root, configDir, databasePath string) (string, func()) {
	t.Helper()
	// The Python packaged seed records bundled rates as source_kind=seed. Build
	// the same explicit fixture for Go so the evidence metadata is comparable,
	// while keeping the production loader's official-source behavior unchanged.
	seed := loadProductionPricingFixture(t, testsupport.RepoRoot(t))
	seedFixturePath := filepath.Join(root, "pricing-seed.json")
	seedFixtureBytes, err := json.Marshal(seed)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(seedFixturePath, seedFixtureBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	command := exec.Command(binaryPath, "--http", "--host", "127.0.0.1", "--port", strconv.Itoa(port), "--root", root, "--config", configDir, "--stats", databasePath)
	command.Dir = root
	// This is a same-fixture transport test. Disable live collectors so the
	// compiled server cannot append host observations after the fixture DB is
	// prepared; production startup keeps all collectors enabled by default.
	command.Env = syntheticStatsEnvironment(
		"YOMUX_PRICING_CATALOG="+seedFixturePath,
		"YOMUX_STATS_DISABLE_COLLECTORS=1",
	)
	var output synchronizedBuffer
	command.Stdout = &output
	command.Stderr = &output
	process := startParityProcess(t, command, &output)
	baseURL := fmt.Sprintf("http://127.0.0.1:%d", port)
	deadline := time.Now().Add(parityServerTimeout())
	for time.Now().Before(deadline) {
		if process.exited() {
			t.Fatalf("external Go server exited before health check: %s", output.String())
		}
		response, requestErr := http.Get(baseURL + "/healthz")
		if requestErr == nil {
			_, _ = io.Copy(io.Discard, response.Body)
			_ = response.Body.Close()
			if response.StatusCode == http.StatusOK {
				return baseURL, func() { process.checkStop(t) }
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	process.checkStop(t)
	t.Fatalf("external Go server did not become healthy: %s", output.String())
	return "", func() {}
}

func loadProductionPricingFixture(t *testing.T, repoRoot string) map[string]any {
	t.Helper()
	load := func(name string) map[string]any {
		data, err := os.ReadFile(filepath.Join(repoRoot, "yomux_lib", "data", name))
		if err != nil {
			t.Fatal(err)
		}
		var value map[string]any
		if err := json.Unmarshal(data, &value); err != nil {
			t.Fatal(err)
		}
		return value
	}
	base := load("model_pricing.json")
	internal := load("internal_model_pricing.json")
	baseModels, ok := base["models"].([]any)
	if !ok {
		t.Fatal("packaged pricing seed models are not an array")
	}
	internalModels, ok := internal["models"].([]any)
	if !ok {
		t.Fatal("internal pricing seed models are not an array")
	}
	merged := make([]any, 0, len(baseModels)+len(internalModels))
	for _, raw := range baseModels {
		model, ok := raw.(map[string]any)
		if !ok {
			t.Fatal("packaged pricing model is not an object")
		}
		if model["provider"] == "openai" && model["model"] == "gpt-5.6-luna" {
			continue
		}
		merged = append(merged, model)
	}
	for _, raw := range internalModels {
		model, ok := raw.(map[string]any)
		if !ok {
			t.Fatal("internal pricing model is not an object")
		}
		if model["provider"] == "openai" && model["model"] == "gpt-5.6-luna" {
			// The internal model replaces the older public Luna rate.
			merged = append(merged, model)
			continue
		}
		aliases, _ := model["aliases"].([]any)
		duplicate := false
		for _, existing := range merged {
			candidate, candidateOK := existing.(map[string]any)
			if !candidateOK || candidate["provider"] != model["provider"] {
				continue
			}
			for _, alias := range aliases {
				for _, existingAlias := range candidate["aliases"].([]any) {
					if alias == existingAlias {
						duplicate = true
					}
				}
			}
		}
		if !duplicate {
			merged = append(merged, model)
		}
	}
	for _, raw := range merged {
		model := raw.(map[string]any)
		source, _ := model["source"].(map[string]any)
		if source == nil {
			source = map[string]any{}
			model["source"] = source
		}
		source["kind"] = "seed"
	}
	base["models"] = merged
	base["catalog_revision"] = internal["catalog_revision"]
	return base
}

// parityProcess owns Wait and output drainage, including early exits and shutdown failures.
type parityProcess struct {
	command  *exec.Cmd
	output   *synchronizedBuffer
	done     chan struct{}
	waitErr  error
	stopOnce sync.Once
	stopErr  error
}

func startParityProcess(t *testing.T, command *exec.Cmd, output *synchronizedBuffer) *parityProcess {
	t.Helper()
	command.WaitDelay = 2 * time.Second
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	process := &parityProcess{command: command, output: output, done: make(chan struct{})}
	go func() { process.waitErr = command.Wait(); close(process.done) }()
	t.Cleanup(func() { process.checkStop(t) })
	return process
}

func (process *parityProcess) exited() bool {
	select {
	case <-process.done:
		return true
	default:
		return false
	}
}

func (process *parityProcess) readyLine(timeout time.Duration) (string, error) {
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		line, _, complete := strings.Cut(process.output.String(), "\n")
		if process.exited() {
			return "", fmt.Errorf("child exited before readiness: %s", process.output.String())
		}
		if complete {
			return line, nil
		}
		select {
		case <-timer.C:
			return "", fmt.Errorf("child readiness timeout: %s", process.output.String())
		case <-ticker.C:
		}
	}
}

func (process *parityProcess) stop(timeout time.Duration) error {
	return process.stopWithSignal(timeout, func() error { return process.command.Process.Signal(os.Interrupt) })
}

func (process *parityProcess) stopWithSignal(timeout time.Duration, signal func() error) error {
	process.stopOnce.Do(func() {
		early := process.exited()
		var signalErr error
		forced := false
		if !early {
			signalErr = signal()
			timer := time.NewTimer(timeout)
			select {
			case <-process.done:
			case <-timer.C:
				forced = true
				_ = process.command.Process.Kill()
				<-process.done
			}
			timer.Stop()
		}
		output := process.output.String()
		if strings.Contains(output, "WARNING: DATA RACE") || strings.Contains(output, "ThreadSanitizer") {
			process.stopErr = fmt.Errorf("child race report: %s", output)
		} else if forced {
			process.stopErr = fmt.Errorf("child ignored shutdown signal: %s", output)
		} else if early {
			process.stopErr = fmt.Errorf("child exited before shutdown: %v: %s", process.waitErr, output)
		} else if signalErr != nil {
			process.stopErr = fmt.Errorf("child shutdown signal failed: %v: %s", signalErr, output)
		} else if process.waitErr != nil {
			exit, ok := process.waitErr.(*exec.ExitError)
			expectedInterrupt := false
			if ok {
				status, valid := exit.Sys().(syscall.WaitStatus)
				expectedInterrupt = valid && status.Signaled() && status.Signal() == syscall.SIGINT
			}
			if !expectedInterrupt {
				process.stopErr = fmt.Errorf("child failed: %v: %s", process.waitErr, output)
			}
		}
	})
	return process.stopErr
}

func (process *parityProcess) checkStop(t *testing.T) {
	t.Helper()
	if err := process.stop(5 * time.Second); err != nil {
		t.Error(err)
	}
}

func TestParityProcessShutdownControls(t *testing.T) {
	for _, test := range []struct {
		name, body, want string
		early            bool
	}{
		{"drain", "signal.signal(signal.SIGINT, lambda *_: (print('tail' * 65536, flush=True), sys.exit(0)))", "", false},
		{"exit66", "signal.signal(signal.SIGINT, lambda *_: sys.exit(66))", "child failed", false},
		{"raceAfterReady", "signal.signal(signal.SIGINT, lambda *_: (print('WARNING: DATA RACE', flush=True), sys.exit(0)))", "child race report", false},
		{"earlyExit", "sys.exit(0)", "before shutdown", true},
		{"ignoredInterrupt", "signal.signal(signal.SIGINT, signal.SIG_IGN)", "ignored shutdown", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			script := "import signal,sys,time\n" + test.body + "\nprint('READY', flush=True)\nwhile True: signal.pause()\n"
			command := exec.Command("python3", "-u", "-c", script)
			var output synchronizedBuffer
			command.Stdout, command.Stderr = &output, &output
			command.WaitDelay = time.Second
			if err := command.Start(); err != nil {
				t.Fatal(err)
			}
			process := &parityProcess{command: command, output: &output, done: make(chan struct{})}
			go func() { process.waitErr = command.Wait(); close(process.done) }()
			defer process.stop(time.Second)
			deadline := time.NewTimer(5 * time.Second)
			defer deadline.Stop()
			ticker := time.NewTicker(time.Millisecond)
			defer ticker.Stop()
			for !strings.Contains(output.String(), "READY") && !process.exited() {
				select {
				case <-deadline.C:
					t.Fatal("child readiness timeout")
				case <-ticker.C:
				}
			}
			if test.early {
				<-process.done
			}
			started := time.Now()
			err := process.stop(100 * time.Millisecond)
			if test.want == "" {
				if err != nil {
					t.Fatal(err)
				}
				if !strings.Contains(output.String(), strings.Repeat("tail", 65536)) {
					t.Fatal("child output was not drained")
				}
			} else if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("stop = %v, want %q", err, test.want)
			}
			if time.Since(started) > 3*time.Second {
				t.Fatal("shutdown exceeded bound")
			}
		})
	}
}

func TestParityProcessRejectsExitBeforeWaitNotification(t *testing.T) {
	command := exec.Command("python3", "-c", "pass")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	if err := command.Wait(); err != nil {
		t.Fatal(err)
	}
	// Wait can reap the process before its output-copy goroutines complete. Hold
	// the completion notification to reproduce shutdown during that interval.
	process := &parityProcess{command: command, output: &synchronizedBuffer{}, done: make(chan struct{})}
	err := process.stopWithSignal(time.Second, func() error {
		// The real OS signal must fail before the Wait notification is delivered.
		err := command.Process.Signal(os.Interrupt)
		close(process.done)
		return err
	})
	if err == nil || !strings.Contains(err.Error(), "shutdown signal failed") {
		t.Fatalf("stop accepted an exit before output drainage completed: %v", err)
	}
}

func parityServerTimeout() time.Duration {
	// A production snapshot can wait for the lifecycle worker to fill a missing
	// ring bucket. The 16-hour/300-second product exceeded 20 seconds once in
	// the container, while the same target completed within this measured bound.
	return 60 * time.Second
}

func waitForAuthenticatedSnapshot(t *testing.T, url string) rawHTTPResponse {
	t.Helper()
	deadline := time.Now().Add(parityServerTimeout())
	lastResponse := rawHTTPResponse{}
	for time.Now().Before(deadline) {
		response := getRawResponseWithBasicAuth(t, url)
		lastResponse = response
		if response.status == http.StatusOK {
			return response
		}
		if response.status != http.StatusAccepted {
			t.Fatalf("external Go snapshot status=%d body=%s", response.status, response.body)
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("external Go snapshot did not become ready: url=%s status=%d body=%s", url, lastResponse.status, lastResponse.body)
	return rawHTTPResponse{}
}

func getRawResponseWithBasicAuth(t *testing.T, rawURL string) rawHTTPResponse {
	t.Helper()
	request, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		t.Fatal(err)
	}
	request.SetBasicAuth("alice", "secret")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	return rawHTTPResponse{status: response.StatusCode, contentType: response.Header.Get("Content-Type"), body: string(body)}
}

func TestStatsConcurrentStreamsDoNotBlockSnapshotReads(t *testing.T) {
	ctx := context.Background()
	now := time.Unix(2000, 0)
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{
		Observations: []stats.Observation{{
			EventID: "stream-cpu", Family: "cpu", SourceID: "host", ObservedAt: 1990,
			EpochID: "cpu", OwnerGeneration: 1,
			Payload: map[string]any{"process_percent": 20.0, "system_percent": 40.0},
		}},
		CoverageEpochs: []stats.CoverageEpoch{{
			Family: "cpu", SourceID: "host", EpochID: "cpu", StartedAt: 1800,
			NativeCadenceSeconds: 1, OwnerGeneration: 1,
		}},
	}); err != nil {
		t.Fatal(err)
	}
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, now)
	server := httptest.NewServer(statsParityMux(&server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }, statsHeartbeat: time.Hour}))
	defer server.Close()

	const streamCount = 8
	release := make(chan struct{})
	ready := make(chan heldStatsStream, streamCount)
	for index := 0; index < streamCount; index++ {
		go holdStatsStream(t, server.URL+"/api/stats-stream?range_seconds=300&resolution=10&client_id=soak-"+strconv.Itoa(index), release, ready)
	}
	held := make([]heldStatsStream, 0, streamCount)
	for index := 0; index < streamCount; index++ {
		select {
		case stream := <-ready:
			held = append(held, stream)
		case <-time.After(5 * time.Second):
			close(release)
			t.Fatal("concurrent stats streams did not all become ready")
		}
	}
	started := time.Now()
	response := getRawResponse(t, server.URL+"/api/stats-snapshot?range_seconds=300&resolution=10&client_id=latency-probe")
	elapsed := time.Since(started)
	if response.status != http.StatusOK {
		t.Fatalf("snapshot read during concurrent streams = %+v", response)
	}
	if elapsed > time.Second {
		t.Fatalf("snapshot read blocked behind concurrent streams: %s", elapsed)
	}
	close(release)
	for _, stream := range held {
		stream.cancel()
		_ = stream.response.Body.Close()
	}
}

type heldStatsStream struct {
	cancel   context.CancelFunc
	response *http.Response
}

func holdStatsStream(t *testing.T, url string, release <-chan struct{}, ready chan<- heldStatsStream) {
	t.Helper()
	request, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		t.Errorf("build held stats stream request: %v", err)
		return
	}
	ctx, cancel := context.WithCancel(request.Context())
	request = request.WithContext(ctx)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		cancel()
		t.Errorf("open held stats stream: %v", err)
		return
	}
	if response.StatusCode != http.StatusOK {
		cancel()
		_ = response.Body.Close()
		t.Errorf("held stats stream status = %d", response.StatusCode)
		return
	}
	scanner := bufio.NewScanner(response.Body)
	for scanner.Scan() {
		if scanner.Text() == "event: ready" {
			ready <- heldStatsStream{cancel: cancel, response: response}
			<-release
			return
		}
	}
	cancel()
	_ = response.Body.Close()
	if err := scanner.Err(); err != nil && !strings.Contains(err.Error(), "use of closed network connection") {
		t.Errorf("held stats stream ended before ready: %v", err)
	}
}

type rawHTTPResponse struct {
	status      int
	contentType string
	headers     map[string]string
	body        string
}

func getRawResponse(t *testing.T, url string) rawHTTPResponse {
	t.Helper()
	response, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	headers := map[string]string{}
	for _, name := range []string{"Cache-Control", "Connection", "X-Accel-Buffering"} {
		if value := response.Header.Get(name); value != "" {
			headers[name] = value
		}
	}
	return rawHTTPResponse{status: response.StatusCode, contentType: response.Header.Get("Content-Type"), headers: headers, body: string(body)}
}

func normalizeReadyRequestID(body string) string {
	var payload map[string]any
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		return body
	}
	if request, ok := payload["request"].(map[string]any); ok {
		if _, present := request["id"]; present {
			request["id"] = "<request-id>"
		}
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return body
	}
	return string(encoded)
}

func normalizeProductionResponseBody(body string) string {
	var payload any
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		return body
	}
	var visit func(any)
	visit = func(value any) {
		switch item := value.(type) {
		case map[string]any:
			for key, child := range item {
				switch key {
				case "no_data":
					// Snapshot probes validate the exact synthetic no-data shape. The
					// later delta/reconnect probes can cross the live ring boundary.
					item[key] = "<no-data-timing>"
				case "cache_generation":
					item[key] = "<cache-generation>"
				case "generated_at":
					item[key] = "<generated-at>"
				case "database":
					item[key] = "<database>"
				case "next_refresh_at":
					// The external parity fixture disables Python's periodic
					// refresh to keep tests offline; the Go server still exposes
					// its scheduled timestamp. Compare catalog and refresh state,
					// not scheduler timing metadata.
					delete(item, key)
				default:
					visit(child)
				}
			}
			if item["status"] == "idle" {
				if _, present := item["failure_count"]; !present {
					item["failure_count"] = float64(0)
				}
			}
		case []any:
			for _, child := range item {
				visit(child)
			}
		}
	}
	visit(payload)
	if root, ok := payload.(map[string]any); ok {
		if request, requestOK := root["request"].(map[string]any); requestOK {
			request["id"] = "<request-id>"
		}
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return body
	}
	return string(encoded)
}

func normalizeStatsParityError(body string) string {
	var payload map[string]any
	if err := json.Unmarshal([]byte(body), &payload); err != nil || payload["state"] != "failed" {
		return body
	}
	errorRecord, ok := payload["error"].(map[string]any)
	if !ok {
		return body
	}
	details, ok := errorRecord["details"].(map[string]any)
	if !ok {
		return body
	}
	// The Python forwarder exposes this legacy discriminator directly; the
	// canonical failure envelope carries the same fields in error.details.
	if _, present := details["status"]; !present && details["valid_ranges"] != nil {
		details["status"] = "unsupported"
	}
	encoded, err := json.Marshal(details)
	if err != nil {
		return body
	}
	return string(encoded)
}

func statsParityMux(server *server) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/stats-capabilities", server.statsCapabilities)
	mux.HandleFunc("/api/stats-snapshot", server.statsSnapshot)
	mux.HandleFunc("/api/stats-delta", server.statsDelta)
	mux.HandleFunc("/api/stats-stream", server.statsStream)
	return mux
}

func getInitialSSE(t *testing.T, url string) rawHTTPResponse {
	t.Helper()
	request, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		t.Fatal(err)
	}
	return readInitialSSE(t, request)
}

func getInitialSSEWithBasicAuth(t *testing.T, url string) rawHTTPResponse {
	t.Helper()
	request, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		t.Fatal(err)
	}
	request.SetBasicAuth("alice", "secret")
	return readInitialSSE(t, request)
}

func readInitialSSE(t *testing.T, request *http.Request) rawHTTPResponse {
	t.Helper()
	ctx, cancel := context.WithCancel(request.Context())
	defer cancel()
	request = request.WithContext(ctx)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(response.Body)
		t.Fatalf("SSE status=%d body=%s", response.StatusCode, body)
	}
	scanner := bufio.NewScanner(response.Body)
	var builder strings.Builder
	ready := false
	for scanner.Scan() {
		line := scanner.Text()
		builder.WriteString(line)
		builder.WriteString("\n")
		if line == "event: ready" {
			ready = true
		}
		if ready && line == "" {
			cancel()
			break
		}
	}
	if err := scanner.Err(); err != nil && !strings.Contains(err.Error(), "use of closed network connection") {
		t.Fatal(err)
	}
	if !ready {
		t.Fatalf("Go SSE did not emit ready: %s", builder.String())
	}
	headers := map[string]string{}
	for _, name := range []string{"Cache-Control", "Connection", "X-Accel-Buffering"} {
		if value := response.Header.Get(name); value != "" {
			headers[name] = value
		}
	}
	return rawHTTPResponse{status: response.StatusCode, contentType: response.Header.Get("Content-Type"), headers: headers, body: builder.String()}
}

func startPythonStatsParityServer(t *testing.T, fixturePath string) (string, func()) {
	t.Helper()
	root := testsupport.RepoRoot(t)
	scriptPath := filepath.Join(t.TempDir(), "stats_http_server.py")
	script := `import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from yomux_lib.stats_current import http, materializer, pricing, storage, service
from yomux_lib.observability.pricing_catalog import PricingCatalog

fixture = json.load(open(sys.argv[1], encoding="utf-8"))
obs = tuple(storage.Observation(x["EventID"], x["Family"], x["SourceID"], x["ObservedAt"], x["EpochID"], x["OwnerGeneration"], x["Payload"]) for x in fixture["observations"])
coverage = tuple(storage.CoverageEpoch(x["Family"], x["SourceID"], x["EpochID"], x["StartedAt"], x.get("EndedAt"), x["NativeCadenceSeconds"], x["OwnerGeneration"]) for x in fixture["coverage"])
atoms = tuple(storage.UsageAtom(x["EventID"], x["Direction"], x["Modality"], x["CacheRole"], x["Unit"], x["ObservedAt"], x["Payload"]) for x in fixture["atoms"])
snapshot = storage.StoreSnapshot(storage.SchemaMetadata(9, 25, 8, 1), obs, coverage, atoms, (), ())
pricing_catalog = PricingCatalog(root=fixture["pricing_root"])
generation = materializer.build_generation(snapshot, source_generation=fixture["source_generation"], cache_generation=fixture["cache_generation"], generated_at=fixture["generated_at"], observed_until=fixture.get("observed_until", 2000.0), price_resolver=pricing.UsagePriceProjector(pricing_catalog))
layer = materializer.slice_generation(generation, 300, 10)
def go_shape(value):
    if isinstance(value, dict): return {str(key): go_shape(item) for key, item in value.items()}
    if isinstance(value, list): return [go_shape(item) for item in value]
    if isinstance(value, float) and value.is_integer(): return int(value)
    return value
body = json.dumps(go_shape(service._wire_snapshot(generation, layer, 300, 10, materializer.build_cost_report(layer))), sort_keys=True, separators=(",", ":")).encode()
class Client:
    def ensure_started(self): return True
    def retry(self): return True
    def status(self): return {"status": "ready"}
    def snapshot(self, request): return ({"ok": True, "content_type": "application/json", "cache_generation": fixture["cache_generation"], "chunk_count": 1}, body)
    def delta(self, request): raise AssertionError("delta is not used by this fixture")
adapter = http.StatsHttpForwarder(Client(), client_binding_secret=b"s" * 32)
def compact(value): return json.dumps(go_shape(value), sort_keys=True, separators=(",", ":")).encode()
def ready_envelope(payload):
    envelope = {"state": "ready", "request": {"id": "r-python-parity"}, "data": payload, "ok": True, "terminal": True}
    envelope.update({key: value for key, value in payload.items() if key not in envelope})
    return envelope
def sse(event, value): return b"id: py-" + event.encode() + b"\nevent: " + event.encode() + b"\ndata: " + (value if isinstance(value, bytes) else compact(value)) + b"\n\n"
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/api/stats-capabilities": status, content, payload = 200, "application/json; charset=utf-8", compact(ready_envelope(adapter.capabilities()))
        elif parsed.path == "/api/stats-snapshot":
            result = adapter.snapshot(parsed.query, authenticated_username="alice")
            status, content, payload = int(result.status), "application/json; charset=utf-8", result.body or compact(result.payload or {})
        elif parsed.path == "/api/stats-delta":
            result = adapter.delta(parsed.query, authenticated_username="alice")
            status, content, payload = int(result.status), "application/json; charset=utf-8", result.body or compact(result.payload or {})
        elif parsed.path == "/api/stats-stream":
            result = adapter.snapshot_stream(parsed.query, authenticated_username="alice")
            if result.status != 200: status, content, payload = int(result.status), "application/json; charset=utf-8", compact(result.metadata)
            else:
                query = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in parsed.query.split("&") if "=" in item}
                ack = {"cache_generation": result.metadata["cache_generation"], "chunk_count": result.metadata["chunk_count"], "not_modified": False, "range_seconds": int(query["range_seconds"]), "requested_resolution": int(query["resolution"]), "resolution_seconds": int(query["resolution"])}
                ready = {"cache_generation": result.metadata["cache_generation"], "revision": 0}
                status, content, payload = 200, "text/event-stream; charset=utf-8", sse("ack", ack) + sse("snapshot", result.body) + sse("ready", ready)
        else: status, content, payload = 404, "application/json; charset=utf-8", b"{}"
        self.send_response(status); self.send_header("Content-Type", content); self.send_header("Content-Length", str(len(payload))); self.send_header("Cache-Control", "no-store")
        if parsed.path == "/api/stats-stream" and status == 200:
            self.send_header("Cache-Control", "no-store"); self.send_header("Connection", "keep-alive"); self.send_header("X-Accel-Buffering", "no")
        self.end_headers(); self.wfile.write(payload)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(f"READY {server.server_address[1]}", flush=True)
server.serve_forever()
`
	if err := os.WriteFile(scriptPath, []byte(script), 0600); err != nil {
		t.Fatal(err)
	}
	command := exec.Command("python3", "-u", scriptPath, fixturePath)
	command.Dir = root
	// This test intentionally issues a dense sequence of authenticated requests
	// from one loopback identity. Bypass only the login throttle (not route auth)
	// so the comparison reaches every stats event without turning a test-client
	// admission policy into a false protocol failure.
	command.Env = syntheticStatsEnvironment("PYTHONDONTWRITEBYTECODE=1", "PYTHONPATH="+root, "YOMUX_TEST_AUTH_BYPASS=1")
	var output synchronizedBuffer
	command.Stdout = &output
	command.Stderr = &output
	process := startParityProcess(t, command, &output)
	line, err := process.readyLine(parityServerTimeout())
	if err != nil {
		t.Fatal(err)
	}
	fields := strings.Fields(line)
	if len(fields) != 2 || fields[0] != "READY" {
		t.Fatalf("Python parity server announcement = %q", line)
	}
	port, err := strconv.Atoi(fields[1])
	if err != nil {
		t.Fatal(err)
	}
	return fmt.Sprintf("http://127.0.0.1:%d", port), func() { process.checkStop(t) }
}
