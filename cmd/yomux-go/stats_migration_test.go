package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"

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

	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/testsupport"
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
	s := &server{pricing: catalog, pricingRefreshRun: func(context.Context) error { return nil }}
	record := httptest.NewRecorder()
	s.pricingCatalog(record, httptest.NewRequest(http.MethodGet, "/api/pricing-catalog", nil))
	if record.Code != http.StatusOK || !bytes.Contains(record.Body.Bytes(), []byte(`"catalog_revision":7`)) || bytes.Contains(record.Body.Bytes(), []byte(`"model-a"`)) || !bytes.Contains(record.Body.Bytes(), []byte(`"sources":[]`)) {
		t.Fatalf("catalog status=%d body=%s", record.Code, record.Body.String())
	}
	refresh := httptest.NewRecorder()
	s.pricingCatalogRefresh(refresh, httptest.NewRequest(http.MethodPost, "/api/pricing-catalog/refresh", nil))
	if refresh.Code != http.StatusAccepted || !bytes.Contains(refresh.Body.Bytes(), []byte(`"status":"running"`)) || !bytes.Contains(refresh.Body.Bytes(), []byte(`"ticket":"pricing-refresh-1"`)) {
		t.Fatalf("refresh status=%d body=%s", refresh.Code, refresh.Body.String())
	}
}

func TestPricingCatalogHTTPRetainsRefreshFailureForExistingJSStatusPoll(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pricing.json")
	fixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"fixture","model":"model-a","aliases":["model-a"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"2.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(path, []byte(fixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := stats.LoadPricingCatalog(path)
	if err != nil {
		t.Fatal(err)
	}
	s := &server{pricing: catalog, pricingRefreshRun: func(context.Context) error { return fmt.Errorf("read pricing catalog: fixture failure") }}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	refresh := httptest.NewRecorder()
	s.pricingCatalogRefresh(refresh, httptest.NewRequest(http.MethodPost, "/api/pricing-catalog/refresh", nil))
	if refresh.Code != http.StatusAccepted {
		t.Fatalf("refresh status=%d body=%s", refresh.Code, refresh.Body.String())
	}
	deadline := time.NewTimer(time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			record := httptest.NewRecorder()
			s.pricingCatalog(record, httptest.NewRequest(http.MethodGet, "/api/pricing-catalog", nil))
			if bytes.Contains(record.Body.Bytes(), []byte(`"status":"failed"`)) {
				if !bytes.Contains(record.Body.Bytes(), []byte(`"error":"read pricing catalog`)) {
					t.Fatalf("failed refresh omitted error detail: %s", record.Body.String())
				}
				return
			}
		case <-deadline.C:
			t.Fatal("refresh failure did not remain visible to status polling")
		}
	}
}

func TestStatsQueryBounds(t *testing.T) {
	for _, raw := range []string{
		"range_seconds=300&resolution=AUTO&client_id=browser-a",
		"range_seconds=900&resolution=10&client_id=browser-a",
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
		"range_seconds=300&resolution=AUTO&client_id=browser-a&limit=100",
		"range_seconds=300&resolution=AUTO&client_id=%00",
		"range_seconds=300&resolution=AUTO&client_id=%20",
		"range_seconds=300&resolution=AUTO&client_id=browser-a&since_generation=9007199254740992",
	} {
		if _, failure, ok := parseStatsQuery(httptest.NewRequest(http.MethodGet, "/?"+raw, nil), false); ok || failure["status"] != "unsupported" {
			t.Fatalf("accepted invalid query %q: failure=%v", raw, failure)
		}
	}
	chunked, failure, ok := parseStatsQuery(httptest.NewRequest(http.MethodGet, "/?range_seconds=900&resolution=10&client_id=browser-a&chunk_index=1&chunk_generation=7", nil), false)
	if !ok || failure != nil || !chunked.hasChunkIndex || chunked.chunkIndex != 1 || chunked.chunkGeneration != 7 {
		t.Fatalf("accepted chunk query = %+v failure=%v", chunked, failure)
	}
	chunked, failure, ok = parseStatsQuery(httptest.NewRequest(http.MethodGet, "/?range_seconds=900&resolution=10&client_id=browser-a&chunk_index=511&chunk_generation=7", nil), false)
	if !ok || failure != nil || !chunked.hasChunkIndex || chunked.chunkIndex != stats.MaxStatsSnapshotChunks-1 {
		t.Fatalf("accepted final snapshot chunk query = %+v failure=%v", chunked, failure)
	}
	for _, raw := range []string{
		"range_seconds=900&resolution=10&client_id=browser-a&chunk_index=1",
		"range_seconds=900&resolution=10&client_id=browser-a&chunk_generation=7",
		"range_seconds=900&resolution=10&client_id=browser-a&chunk_index=512&chunk_generation=7",
		"range_seconds=900&resolution=10&client_id=browser-a&chunk_index=1&chunk_generation=0",
	} {
		if _, failure, ok := parseStatsQuery(httptest.NewRequest(http.MethodGet, "/?"+raw, nil), false); ok || failure == nil {
			t.Fatalf("accepted invalid chunk query %q: failure=%v", raw, failure)
		}
	}
}

func TestStatsQueryErrorCorpusMatchesPythonProtocol(t *testing.T) {
	tests := []struct {
		name, raw, reason string
		delta             bool
		rangeSeconds      any
	}{
		{name: "missing snapshot fields", raw: "", reason: "missing query parameters: ['client_id', 'range_seconds', 'resolution']"},
		{name: "missing delta fields", raw: "", delta: true, reason: "missing delta parameters: ['after_cache_generation', 'after_revision', 'client_id', 'range_seconds', 'resolution_seconds']"},
		{name: "duplicate", raw: "range_seconds=300&resolution=AUTO&client_id=x&resolution=1", reason: "duplicate query parameters: ['resolution']"},
		{name: "unknown", raw: "range_seconds=300&resolution=AUTO&client_id=x&limit=1", reason: "unknown query parameters: ['limit']"},
		{name: "retired", raw: "range_seconds=300&resolution=AUTO&client_id=x&history=1", reason: "retired query parameters: ['history']"},
		{name: "empty client", raw: "range_seconds=300&resolution=AUTO&client_id=", reason: "client_id must be a non-empty string", rangeSeconds: 300},
		{name: "malformed escape", raw: "range_seconds=300&resolution=AUTO&client_id=%ZZ", reason: "query contains a malformed escape"},
		{name: "unsupported range", raw: "range_seconds=301&resolution=AUTO&client_id=x", reason: "unsupported range_seconds 301", rangeSeconds: 301},
		{name: "unsupported resolution", raw: "range_seconds=900&resolution=1&client_id=x", reason: "unsupported resolution 1 for range 900s; offered (10, 60)", rangeSeconds: 900},
		{name: "generation overflow", raw: "range_seconds=300&resolution=AUTO&client_id=x&since_generation=9007199254740992", reason: "since_generation exceeds the exact JSON integer range", rangeSeconds: 300},
		{name: "delta key", raw: "range_seconds=900&resolution_seconds=1&client_id=x&after_cache_generation=0&after_revision=0", delta: true, reason: "delta has an unsupported Range/Resolution key", rangeSeconds: 900},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, failure, ok := parseStatsQuery(httptest.NewRequest(http.MethodGet, "/?"+test.raw, nil), test.delta)
			if ok || failure == nil {
				t.Fatalf("query accepted: %#v", failure)
			}
			if got := failure["reason"]; got != test.reason {
				t.Fatalf("reason=%#v, want %#v; payload=%#v", got, test.reason, failure)
			}
			if got := failure["range_seconds"]; got != test.rangeSeconds {
				t.Fatalf("range_seconds=%#v, want %#v", got, test.rangeSeconds)
			}
		})
	}
}

func TestSnapshotChunkWirePreservesBoundsAndClipsNoData(t *testing.T) {
	buckets := make([]map[string]any, 12)
	for index := range buckets {
		buckets[index] = map[string]any{
			"start": float64(index), "duration": float64(1), "series": map[string]any{},
			"source": map[string]any{"count": float64(0)}, "open": index == 11,
		}
	}
	full := map[string]any{
		"protocol_version": float64(statsWireVersion), "range_seconds": float64(12),
		"requested_resolution": float64(1), "resolution_seconds": float64(1),
		"window_start": float64(0), "window_end": float64(12), "generated_at": float64(12),
		"source_generation": float64(4), "cache_generation": float64(7), "rightmost_open": true,
		"buckets": buckets, "no_data": []map[string]any{{
			"family": "gpu", "source_id": "gpu:0", "start": float64(2), "end": float64(10),
			"epoch": "gap", "reason": "driver unavailable", "source_cadence_seconds": float64(10),
		}},
		"cost_report": map[string]any{},
	}
	chunk, err := snapshotChunkWire(full, 1, 3)
	if err != nil {
		t.Fatal(err)
	}
	if chunk["chunk_index"] != 1 || chunk["chunk_count"] != 3 || chunk["chunk_start"] != float64(4) || chunk["chunk_end"] != float64(8) {
		t.Fatalf("chunk bounds = %#v", chunk)
	}
	if got := len(chunk["buckets"].([]map[string]any)); got != 4 {
		t.Fatalf("chunk bucket count = %d, want 4", got)
	}
	spans := chunk["no_data"].([]map[string]any)
	if len(spans) != 1 || spans[0]["start"] != float64(4) || spans[0]["end"] != float64(8) {
		t.Fatalf("clipped no-data = %#v", spans)
	}
	if chunk["rightmost_open"] != true || chunk["buckets"].([]map[string]any)[3]["open"] != false {
		t.Fatalf("non-final chunk open state = %#v", chunk)
	}
}

func TestSnapshotChunkAllowsSingleBucketUpToTransportLimit(t *testing.T) {
	buildSnapshot := func(spanCount int) stats.StatsSnapshot {
		noData := make([]stats.MaterializedNoData, spanCount)
		for index := range noData {
			noData[index] = stats.MaterializedNoData{
				Family: "agent_tokens", SourceID: fmt.Sprintf("retired-unavailable:source-%05d", index),
				EpochID: fmt.Sprintf("epoch-%02d", index%69), Reason: "transcript cursor state exceeds its size bound",
				Start: 0, End: 300, NativeCadenceSeconds: 60,
			}
		}
		return stats.StatsSnapshot{
			SourceGeneration: 1, CacheGeneration: 1,
			Request: stats.SnapshotRequest{Start: 0, End: 600, Resolution: 300},
			Materialized: stats.MaterializedLayer{
				Start: 0, End: 600, Resolution: 300,
				Buckets: []stats.MaterializedBucket{
					{Start: 0, Duration: 300, Complete: true},
					{Start: 300, Duration: 300, Complete: true},
				},
				NoData: noData,
			},
		}
	}
	query := statsQuery{rangeSeconds: 600, resolution: 300, resolutionAuto: true}

	first, chunkCount, err := snapshotPayloadForQuery(buildSnapshot(14_000), query)
	if err != nil {
		t.Fatalf("chunk a single oversized bucket below the transport limit: %v", err)
	}
	encoded, err := json.Marshal(first)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) <= statsSnapshotChunkTargetBytes || len(encoded) > statsSnapshotChunkMaxBytes {
		t.Fatalf("large first chunk bytes = %d, want (%d, %d]", len(encoded), statsSnapshotChunkTargetBytes, statsSnapshotChunkMaxBytes)
	}
	if chunkCount != 2 || first["chunk_count"] != 2 {
		t.Fatalf("chunk count = %d, payload=%#v; want two bucket-aligned chunks", chunkCount, first["chunk_count"])
	}
	if got := len(first["no_data"].([]map[string]any)); got != 14_000 {
		t.Fatalf("large first chunk no-data entries = %d, want 14000", got)
	}

	first, chunkCount, err = snapshotPayloadForQuery(buildSnapshot(30_000), query)
	if err != nil {
		t.Fatalf("split an oversized bucket's no-data spans: %v", err)
	}
	if chunkCount < 3 || first["chunk_count"] != chunkCount {
		t.Fatalf("fragmented chunk count = %d, first payload=%#v; want multiple fragments", chunkCount, first["chunk_count"])
	}
	spanCount := 0
	for index := 0; index < chunkCount; index++ {
		query.hasChunkIndex = true
		query.chunkIndex = index
		query.chunkGeneration = 1
		fragment, actualCount, fragmentErr := snapshotPayloadForQuery(buildSnapshot(30_000), query)
		if fragmentErr != nil {
			t.Fatalf("load fragment %d: %v", index, fragmentErr)
		}
		if actualCount != chunkCount {
			t.Fatalf("fragment %d count = %d, want %d", index, actualCount, chunkCount)
		}
		encodedFragment, marshalErr := json.Marshal(fragment)
		if marshalErr != nil || len(encodedFragment) > statsSnapshotChunkMaxBytes {
			t.Fatalf("fragment %d bytes = %d, marshal error = %v", index, len(encodedFragment), marshalErr)
		}
		spanCount += len(fragment["no_data"].([]map[string]any))
	}
	if spanCount != 30_000 {
		t.Fatalf("fragmented no-data spans = %d, want 30000", spanCount)
	}
}

func TestSingleBucketSnapshotUsesOneValidTransportPayload(t *testing.T) {
	noData := make([]stats.MaterializedNoData, 14_000)
	for index := range noData {
		noData[index] = stats.MaterializedNoData{
			Family: "agent_tokens", SourceID: fmt.Sprintf("retired-unavailable:source-%05d", index),
			EpochID: fmt.Sprintf("epoch-%02d", index%69), Reason: "transcript cursor state exceeds its size bound",
			Start: 0, End: 300, NativeCadenceSeconds: 60,
		}
	}
	snapshot := stats.StatsSnapshot{
		CacheGeneration: 1,
		Request:         stats.SnapshotRequest{Start: 0, End: 300, Resolution: 300},
		Materialized: stats.MaterializedLayer{
			Start: 0, End: 300, Resolution: 300,
			Buckets: []stats.MaterializedBucket{{Start: 0, Duration: 300, Complete: true}},
			NoData:  noData,
		},
	}
	payload, chunks, err := snapshotPayloadForQuery(snapshot, statsQuery{rangeSeconds: 300, resolution: 300, resolutionAuto: true})
	if err != nil {
		t.Fatalf("single-bucket snapshot did not fit one transport payload: %v", err)
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	if chunks != 1 || len(encoded) <= statsSnapshotChunkTargetBytes || len(encoded) > statsSnapshotChunkMaxBytes {
		t.Fatalf("single-bucket payload count=%d bytes=%d, want one payload in (%d,%d]", chunks, len(encoded), statsSnapshotChunkTargetBytes, statsSnapshotChunkMaxBytes)
	}
	if _, isChunk := payload["chunk_count"]; isChunk {
		t.Fatal("single-bucket payload advertised chunk metadata that the browser cannot assemble")
	}
}

func TestChunkedStreamKeepsTypedSnapshotForTheNextDelta(t *testing.T) {
	buckets := make([]stats.MaterializedBucket, 300)
	largeSeriesName := "series-" + strings.Repeat("x", 5000)
	for index := range buckets {
		buckets[index] = stats.MaterializedBucket{
			Start: float64(index), Duration: 1, Complete: true,
			Series:      []stats.MaterializedSeries{{Name: largeSeriesName, Value: float64(index + 1), SourceCount: 1, FirstObservedAt: float64(index), LastObservedAt: float64(index)}},
			SourceCount: 1,
		}
	}
	snapshot := stats.StatsSnapshot{
		CacheGeneration: 7,
		Request:         stats.SnapshotRequest{Start: 0, End: 300, Resolution: 1},
		Materialized:    stats.MaterializedLayer{Start: 0, End: 300, Resolution: 1, Buckets: buckets},
	}
	query := statsQuery{rangeSeconds: 300, resolution: 1, resolutionAuto: true}
	payloads, err := snapshotPayloadsForQuery(snapshot, query)
	if err != nil {
		t.Fatal(err)
	}
	chunkCount := len(payloads)
	if chunkCount < 2 {
		t.Fatalf("large snapshot was not chunked: chunks=%d", chunkCount)
	}
	first := payloads[0]
	firstBuckets, ok := first["buckets"].([]map[string]any)
	if !ok || len(firstBuckets) >= len(buckets) {
		t.Fatalf("first stream payload did not contain one chunk: %T len=%d", first["buckets"], len(firstBuckets))
	}
	for index := 1; index < chunkCount; index++ {
		chunkBuckets, ok := payloads[index]["buckets"].([]map[string]any)
		if !ok || len(chunkBuckets) == 0 {
			t.Fatalf("prepared stream chunk %d is empty: %T", index, payloads[index]["buckets"])
		}
	}

	next := snapshot
	next.CacheGeneration = 8
	next.Materialized.Buckets = append([]stats.MaterializedBucket(nil), buckets...)
	next.Materialized.Buckets[150].Series = []stats.MaterializedSeries{{Name: largeSeriesName, Value: 999, SourceCount: 1, FirstObservedAt: 150, LastObservedAt: 150}}
	previousWire, currentWire := statsDeltaWirePair(snapshot, next, query)
	delta := deltaFromWireMaps(previousWire, currentWire, statsQuery{rangeSeconds: 300, resolution: 1, afterCacheGeneration: 7})
	deltaBuckets, ok := delta["buckets"].([]map[string]any)
	if !ok || len(deltaBuckets) != 1 || deltaBuckets[0]["start"] != float64(150) {
		t.Fatalf("chunked stream delta was not minimal: buckets=%#v", delta["buckets"])
	}
}

func TestStatsSnapshotServesRequestedChunkAndPendingStaleGeneration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(time.Now().Unix(), 0)
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, now)
	defer writer.Close()
	s := &server{statsPath: path, statsWriter: writer, statsService: service, statsNow: func() time.Time { return now }}
	generation := service.State().CacheGeneration
	request := func(chunkGeneration int64) *httptest.ResponseRecorder {
		record := httptest.NewRecorder()
		raw := fmt.Sprintf("range_seconds=300&resolution=1&client_id=chunk-test&chunk_index=1&chunk_generation=%d", chunkGeneration)
		s.statsSnapshot(record, httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?"+raw, nil))
		return record
	}
	chunk := request(generation)
	if chunk.Code != http.StatusOK {
		t.Fatalf("chunk response status=%d body=%s", chunk.Code, chunk.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(chunk.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["chunk_index"] != float64(1) || payload["chunk_count"] != float64(2) {
		t.Fatalf("chunk payload = %#v", payload)
	}
	stale := request(generation - 1)
	if stale.Code != http.StatusAccepted || !bytes.Contains(stale.Body.Bytes(), []byte(`"status":"pending"`)) {
		t.Fatalf("stale chunk response status=%d body=%s", stale.Code, stale.Body.String())
	}
}

func TestStatsPendingWireIncludesPythonRequestKey(t *testing.T) {
	query := statsQuery{rangeSeconds: 900, resolution: 10, resolutionAuto: true}
	payload := statsPending(query, "materialization is not ready")
	if payload["status"] != "pending" || payload["protocol_version"] != statsWireVersion {
		t.Fatalf("pending payload = %#v", payload)
	}
	if payload["range_seconds"] != 900 || payload["requested_resolution"] != "AUTO" || payload["resolution_seconds"] != 10 || payload["retry_after_seconds"] != 1 {
		t.Fatalf("pending request key = %#v", payload)
	}
}

func TestStatsUnsupportedWireMatchesPythonResolutionChoices(t *testing.T) {
	for _, test := range []struct {
		name, raw string
		delta     bool
		want      []any
	}{
		{name: "snapshot", raw: "range_seconds=900&resolution=1&client_id=browser-a", want: []any{"AUTO", 10, 60}},
		{name: "delta", raw: "range_seconds=900&resolution_seconds=1&client_id=browser-a&after_cache_generation=0&after_revision=0", delta: true, want: []any{10, 60}},
		{name: "unknown snapshot range", raw: "range_seconds=301&resolution=AUTO&client_id=browser-a", want: []any{"AUTO", 1, 10, 60, 300}},
		{name: "unknown delta range", raw: "range_seconds=301&resolution_seconds=1&client_id=browser-a&after_cache_generation=0&after_revision=0", delta: true, want: []any{1, 10, 60, 300}},
	} {
		t.Run(test.name, func(t *testing.T) {
			_, failure, ok := parseStatsQuery(httptest.NewRequest(http.MethodGet, "/?"+test.raw, nil), test.delta)
			if ok || failure == nil {
				t.Fatalf("query accepted: failure=%#v", failure)
			}
			if !reflect.DeepEqual(failure["valid_resolutions"], test.want) {
				t.Fatalf("valid resolutions = %#v, want %#v", failure["valid_resolutions"], test.want)
			}
			if test.name == "snapshot" || test.name == "delta" {
				if failure["range_seconds"] != 900 {
					t.Fatalf("range_seconds = %#v, want 900", failure["range_seconds"])
				}
			}
		})
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
	if payload["state"] != "ready" || payload["ok"] != true || payload["terminal"] != true {
		t.Fatalf("capabilities envelope = %v", payload)
	}
	data, ok := payload["data"].(map[string]any)
	if !ok || data["max_buckets"] != float64(600) || payload["max_buckets"] != float64(600) {
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
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: alice\n    password: secret\n    role: readonly\n"), 0o600); err != nil {
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
	if !bytes.Contains(record.Body.Bytes(), []byte(`"observation_receipts":[{"disposition":"accepted","event_id":"e"}]`)) {
		t.Fatalf("browser receipt response=%s", record.Body.String())
	}
	duplicateRecord := httptest.NewRecorder()
	duplicateRequest := httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(body)).WithContext(context.WithValue(context.Background(), authContextKey{}, authUser{username: "alice"}))
	s.statsObservations(duplicateRecord, duplicateRequest)
	if duplicateRecord.Code != http.StatusOK || !bytes.Contains(duplicateRecord.Body.Bytes(), []byte(`"duplicates":1`)) || !bytes.Contains(duplicateRecord.Body.Bytes(), []byte(`"observation_receipts":[{"disposition":"duplicate","event_id":"e"}]`)) {
		t.Fatalf("browser duplicate receipt response status=%d body=%s", duplicateRecord.Code, duplicateRecord.Body.String())
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

func TestStatsObservationsRejectsStaleFenceOversizedAndDuplicateRequests(t *testing.T) {
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
	request := func(body string) *httptest.ResponseRecorder {
		record := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(body))
		req.SetBasicAuth("alice", "secret")
		handler.ServeHTTP(record, req)
		return record
	}
	base := `{"protocol_version":25,"schema_generation":9,"client_id":"browser-a","observations":[{"event_id":"e","family":"browser","source_id":"browser-a","observed_at":1,"epoch_id":"epoch","payload":{"kind":"heartbeat","latency_ms":3}}]}`
	if got := request(strings.Replace(base, `"protocol_version":25`, `"protocol_version":24`, 1)); got.Code != http.StatusUpgradeRequired {
		t.Fatalf("stale protocol status=%d body=%s", got.Code, got.Body.String())
	} else if !bytes.Contains(got.Body.Bytes(), []byte(`"error":{"code":"upgrade_required"`)) || !bytes.Contains(got.Body.Bytes(), []byte(`"required_protocol_version":25`)) {
		t.Fatalf("stale protocol envelope=%s", got.Body.String())
	}
	if got := request(strings.Replace(base, `"schema_generation":9`, `"schema_generation":8`, 1)); got.Code != http.StatusUpgradeRequired {
		t.Fatalf("stale schema status=%d body=%s", got.Code, got.Body.String())
	} else if !bytes.Contains(got.Body.Bytes(), []byte(`"error":{"code":"upgrade_required"`)) || !bytes.Contains(got.Body.Bytes(), []byte(`"required_schema_generation":9`)) {
		t.Fatalf("stale schema envelope=%s", got.Body.String())
	}
	longID := strings.Repeat("x", 129)
	if got := request(strings.Replace(base, `"client_id":"browser-a"`, `"client_id":"`+longID+`"`, 1)); got.Code != http.StatusBadRequest {
		t.Fatalf("oversized client id status=%d body=%s", got.Code, got.Body.String())
	}
	duplicate := strings.Replace(base, `{"event_id":"e"`, `{"event_id":"e"`, 1)
	duplicate = strings.Replace(duplicate, `}]}`, `},{"event_id":"e","family":"browser","source_id":"browser-a","observed_at":1,"epoch_id":"epoch","payload":{"kind":"heartbeat","latency_ms":3}}]}`, 1)
	if got := request(duplicate); got.Code != http.StatusBadRequest || !bytes.Contains(got.Body.Bytes(), []byte("unique within one upload")) {
		t.Fatalf("duplicate request status=%d body=%s", got.Code, got.Body.String())
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

func TestStatsObservationsRedactsRetainedFailureSecrets(t *testing.T) {
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
	body := `{"protocol_version":25,"schema_generation":9,"client_id":"browser-secret","observations":[{"event_id":"secret-event","family":"browser","source_id":"browser-secret","observed_at":1,"epoch_id":"epoch","payload":{"kind":"error","signature":"secret-signature","message":"Bearer abc-token password: plain-secret token=inline-secret","source":"/api/stats"}}]}`
	record := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/stats-observations", strings.NewReader(body)).WithContext(context.WithValue(context.Background(), authContextKey{}, authUser{username: "alice"}))
	s.statsObservations(record, request)
	if record.Code != http.StatusOK {
		t.Fatalf("status=%d response=%s", record.Code, record.Body.String())
	}
	database, err := stats.OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(context.Background(), 0, 2, 10)
	if err != nil || len(observations) != 1 {
		t.Fatalf("stored observations=%+v err=%v", observations, err)
	}
	message, _ := observations[0].Payload["message"].(string)
	if strings.Contains(message, "abc-token") || strings.Contains(message, "plain-secret") || strings.Contains(message, "inline-secret") || !strings.Contains(message, "[redacted-secret]") {
		t.Fatalf("retained failure message was not redacted: %q", message)
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
	s := &server{statsService: agentStatusStatsService(path)}
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

func TestStatsStreamRejectsInvalidQueryBeforeOpeningSSE(t *testing.T) {
	recorder := httptest.NewRecorder()
	server := &server{}
	server.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=900&resolution=1&client_id=browser-a", nil))
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("stream status=%d, want %d; body=%s", recorder.Code, http.StatusBadRequest, recorder.Body.String())
	}
	if contentType := recorder.Header().Get("Content-Type"); !strings.HasPrefix(contentType, "application/json") {
		t.Fatalf("invalid stream content type=%q, want application/json", contentType)
	}
	if strings.Contains(recorder.Body.String(), "event:") {
		t.Fatalf("invalid stream opened SSE: %s", recorder.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["status"] != float64(http.StatusBadRequest) || payload["reason"] != "unsupported resolution 1 for range 900s; offered (10, 60)" {
		t.Fatalf("invalid stream payload=%#v", payload)
	}
	errorRecord, ok := payload["error"].(map[string]any)
	if !ok || errorRecord["code"] != "invalid_request" {
		t.Fatalf("invalid stream error envelope=%#v", payload)
	}
}

func TestStatsStreamHonorsSinceGenerationWithoutResendingSnapshot(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(time.Now().Unix(), 0)
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "stream-not-modified", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 1), EpochID: "stream", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 12.0},
	}}}); err != nil {
		writer.Close()
		t.Fatal(err)
	}
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, now)
	defer writer.Close()
	generation := service.State().CacheGeneration
	streamCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	recorder := newSSETestRecorder()
	done := make(chan struct{})
	go func() {
		s := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }}
		s.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=300&resolution=1&client_id=not-modified&since_generation="+strconv.FormatInt(generation, 10), nil).WithContext(streamCtx))
		close(done)
	}()
	select {
	case <-recorder.ready:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not publish ready")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("not-modified stream did not stop")
	}
	frames := parseSSEFrames(t, recorder.bodyString())
	if len(frames) < 2 || frames[0].event != "ack" || frames[1].event != "ready" {
		t.Fatalf("not-modified stream frames=%+v body=%s", frames, recorder.bodyString())
	}
	if frames[0].data["not_modified"] != true || frames[0].data["chunk_count"] != float64(1) {
		t.Fatalf("not-modified ack=%#v", frames[0].data)
	}
	for _, frame := range frames {
		if frame.event == "snapshot" {
			t.Fatalf("not-modified stream resent snapshot: %+v", frames)
		}
	}
}

func TestStatsStreamAckIncludesRequestedResolution(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(time.Now().Unix()/10*10, 0)
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 900, ResolutionSeconds: 10}}, now)
	defer writer.Close()
	server := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	recorder := newSSETestRecorder()
	done := make(chan struct{})
	go func() {
		server.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=900&resolution=AUTO&client_id=test&since_generation=0", nil).WithContext(ctx))
		close(done)
	}()
	select {
	case <-recorder.ready:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not publish ready")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("stats stream did not stop")
	}
	if !strings.Contains(recorder.bodyString(), `"requested_resolution":"AUTO"`) {
		t.Fatalf("ack=%s", recorder.bodyString())
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
	now := time.Unix(time.Now().Unix(), 0)
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 64 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(context.Background()); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(context.Background()); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("initial stats product did not become ready: %+v", service.State())
	}
	s := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }}
	requestWithRevision := func(after, revision int64) *http.Request {
		return httptest.NewRequest(http.MethodGet, "/api/stats-delta?range_seconds=300&resolution_seconds=1&client_id=test&after_cache_generation="+strconv.FormatInt(after, 10)+"&after_revision="+strconv.FormatInt(revision, 10), nil)
	}
	request := func(after int64) *http.Request {
		return requestWithRevision(after, after)
	}
	first := httptest.NewRecorder()
	s.statsDelta(first, request(0))
	if first.Code != http.StatusConflict {
		t.Fatalf("initial delta status = %d, want 409", first.Code)
	}
	var initial map[string]any
	if err := json.Unmarshal(first.Body.Bytes(), &initial); err != nil {
		t.Fatal(err)
	}
	if initial["status"] != float64(http.StatusConflict) || initial["reason"] != "delta cursor is outside the retained exact chain" {
		t.Fatalf("initial delta repair envelope = %#v", initial)
	}
	initialGeneration, ok := initial["cache_generation"].(float64)
	if !ok || initialGeneration <= 0 {
		t.Fatalf("initial delta cache generation = %#v", initial["cache_generation"])
	}
	if _, err := writer.AppendBatch(context.Background(), stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "delta-1", Family: "cpu", SourceID: "host", ObservedAt: float64(time.Now().Unix()), EpochID: "epoch", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 10},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(context.Background()); err != nil {
		t.Fatal(err)
	}
	changed := httptest.NewRecorder()
	s.statsDelta(changed, request(int64(initialGeneration)))
	if changed.Code != http.StatusOK {
		t.Fatalf("changed delta = %d %s", changed.Code, changed.Body.String())
	}
	var delta map[string]any
	if err := json.Unmarshal(changed.Body.Bytes(), &delta); err != nil {
		t.Fatal(err)
	}
	if buckets, ok := delta["buckets"].([]any); !ok || len(buckets) > 2 {
		t.Fatalf("delta was not incremental: buckets=%v", delta["buckets"])
	}
	changedGeneration, ok := delta["cache_generation"].(float64)
	if !ok || changedGeneration <= initialGeneration {
		t.Fatalf("changed delta generations = initial=%v changed=%v", initialGeneration, delta["cache_generation"])
	}
	unchanged := httptest.NewRecorder()
	// A same-generation cursor is unchanged even when its delivery revision is
	// behind the server's internal revision; Python returns 304 before checking
	// the retained delta chain in this case.
	s.statsDelta(unchanged, requestWithRevision(int64(changedGeneration), 0))
	if unchanged.Code != http.StatusNotModified {
		t.Fatalf("unchanged delta status = %d, want 304", unchanged.Code)
	}
}

func TestStatsSnapshotHonorsSinceGeneration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.AppendBatch(context.Background(), stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "snapshot-1", Family: "cpu", SourceID: "host", ObservedAt: float64(time.Now().Unix()), EpochID: "epoch", Payload: map[string]any{"process_percent": 10},
	}}}); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(time.Now().Unix(), 0)
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, now)
	s := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }}
	record := httptest.NewRecorder()
	first := httptest.NewRecorder()
	s.statsSnapshot(first, httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?range_seconds=300&resolution=1&client_id=fixture", nil))
	if first.Code != http.StatusOK {
		t.Fatalf("initial snapshot status=%d body=%s", first.Code, first.Body.String())
	}
	var initial struct {
		CacheGeneration int64 `json:"cache_generation"`
	}
	if err := json.Unmarshal(first.Body.Bytes(), &initial); err != nil {
		t.Fatal(err)
	}
	s.statsSnapshot(record, httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?range_seconds=300&resolution=1&client_id=fixture&since_generation="+strconv.FormatInt(initial.CacheGeneration, 10), nil))
	if record.Code != http.StatusNotModified || !bytes.Contains(record.Body.Bytes(), []byte(`"not_modified":true`)) {
		t.Fatalf("snapshot status=%d body=%s", record.Code, record.Body.String())
	}
}

func TestStatsSnapshotUsesPublishedServiceProduct(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	rows := make([]stats.RingBucketWrite, 0, 30)
	for start := int64(0); start < 300; start += 10 {
		payload, err := json.Marshal(stats.MaterializedBucket{Start: float64(start), Duration: 10, Complete: true})
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, stats.RingBucketWrite{ResolutionSeconds: 10, BucketStart: start, BucketJSON: string(payload), Complete: true})
	}
	if _, err := writer.PublishRingBuckets(ctx, rows, 1, 300, true); err != nil {
		t.Fatal(err)
	}
	fixedNow := time.Unix(299, 0)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{
		Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}},
		Now:      func() time.Time { return fixedNow },
	})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	s := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return fixedNow }}
	record := httptest.NewRecorder()
	s.statsSnapshot(record, httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=service", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", record.Code, record.Body.String())
	}
	if !bytes.Contains(record.Body.Bytes(), []byte(`"source_generation":1`)) {
		t.Fatalf("service snapshot did not expose published generation: %s", record.Body.String())
	}
}

func TestCostReportPreservesMixedPricedAndUnpricedTokens(t *testing.T) {
	priced := stats.MaterializedCostAttribution{
		Key: "000000000000000000000001", Provider: "provider", Model: "priced", TotalTokens: 10,
		MicroUSD: 10, APIListMicroUSD: 12, PricedAtoms: 1, PricedTokens: 10,
		Dimensions: []stats.MaterializedCostDimension{{Dimension: "input", Tokens: 10, MicroUSD: 10, APIListMicroUSD: 12}},
	}
	unpriced := stats.MaterializedCostAttribution{
		Key: "000000000000000000000002", Provider: "provider", Model: "unpriced", TotalTokens: 20,
		UnpricedAtoms: 1, UnpricedTokens: 20,
		Dimensions: []stats.MaterializedCostDimension{{Dimension: "input", Tokens: 20}},
	}
	report := stats.CostReportWire([]stats.MaterializedBucket{{Cost: stats.MaterializedCostDetail{
		Tokens: 30, MicroUSD: 10, APIListMicroUSD: 12, PricedAtoms: 1, UnpricedAtoms: 1,
		Dimensions: []stats.MaterializedCostDimension{{Dimension: "input", Tokens: 30, MicroUSD: 10, APIListMicroUSD: 12}},
		Models:     []stats.MaterializedCostAttribution{priced, unpriced},
	}}})
	pricedCoverage := report["priced"].(map[string]any)
	unpricedCoverage := report["unpriced"].(map[string]any)
	if pricedCoverage["tokens"] != int64(10) || unpricedCoverage["tokens"] != int64(20) {
		t.Fatalf("mixed cost coverage = priced=%v unpriced=%v", pricedCoverage, unpricedCoverage)
	}
}

func TestSnapshotWireCostSourcesReconcileWithDimensions(t *testing.T) {
	price := stats.PricingResolver(nil)
	buckets := make([]stats.MaterializedBucket, 0, 3)
	for index, values := range []struct {
		direction string
		quantity  float64
	}{
		{"input", 600}, {"input", 601}, {"output", 300},
	} {
		layer, err := (stats.Materializer{Price: price}).Build(stats.MaterializeRequest{Start: float64(index * 10), End: float64(index*10 + 10), Resolution: 10}, nil, nil, nil, []stats.UsageAtom{{
			EventID: fmt.Sprintf("wire-cost-%d", index), Direction: values.direction, Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: float64(index*10 + 1),
			Payload: map[string]any{"quantity": values.quantity, "agent_id": "agent", "model": "model", "provider": "provider"},
		}})
		if err != nil {
			t.Fatal(err)
		}
		buckets = append(buckets, layer.Buckets[0])
	}
	snapshot := stats.StatsSnapshot{Request: stats.SnapshotRequest{Start: 0, End: 30, Resolution: 10}, Materialized: stats.MaterializedLayer{Buckets: buckets}}
	for attempt := 0; attempt < 2; attempt++ {
		wire := snapshotWire(snapshot, statsQuery{rangeSeconds: 30, resolution: 10})
		report := wire["cost_report"].(map[string]any)
		agent := report["agents"].([]any)[0].(map[string]any)
		source := agent["sources"].([]any)[0].(map[string]any)
		dimensions := source["dimensions"].(map[string]any)
		var dimensionTokens int64
		for _, value := range dimensions {
			dimensionTokens += value.(map[string]any)["tokens"].(int64)
		}
		if source["total_tokens"].(int64) != dimensionTokens || source["total_tokens"].(int64) != int64(1501) || dimensions["input"].(map[string]any)["tokens"] != int64(1201) || dimensions["output"].(map[string]any)["tokens"] != int64(300) {
			t.Fatalf("source totals disagree with dimensions on attempt %d: source=%v dimensions=%v", attempt, source["total_tokens"], dimensions)
		}
	}
}

func TestSnapshotWireCostSourcesReconcileAfterLifecycleAndRingDecode(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1789425142, 0)
	resolver := func(stats.UsageAtom) (int64, int64, *stats.PriceEvidence, bool, error) {
		return 1, 2, nil, true, nil
	}
	atoms := make([]stats.UsageAtom, 0, 6)
	for index, value := range []struct {
		direction string
		quantity  int
	}{
		{"input", 600}, {"input", 601}, {"input", 602},
		{"output", 300}, {"output", 301}, {"output", 302},
	} {
		atoms = append(atoms, stats.UsageAtom{
			EventID: fmt.Sprintf("ring-wire-cost-%d", index), Direction: value.direction, Modality: "text", CacheRole: "none", Unit: "tokens",
			ObservedAt: float64(1789425120 + index),
			Payload:    map[string]any{"quantity": value.quantity, "agent_id": "agent", "model": "model", "provider": "provider", "execution_source": "codex"},
		})
	}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{UsageAtoms: atoms}); err != nil {
		t.Fatal(err)
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 30, MaxCells: 64, Price: resolver, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(30, 10)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 30, ResolutionSeconds: 10}}, Price: resolver, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 16; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
		if service.State().Ready {
			break
		}
	}
	if !service.State().Ready {
		t.Fatalf("published service is not ready: %+v", service.State())
	}
	snapshot, err := service.Snapshot(ctx, stats.SnapshotRequest{Start: 1789425120, End: 1789425150, Resolution: 10})
	if err != nil {
		t.Fatal(err)
	}
	wire := snapshotWire(snapshot, statsQuery{rangeSeconds: 30, resolution: 10})
	report := wire["cost_report"].(map[string]any)
	agent := report["agents"].([]any)[0].(map[string]any)
	source := agent["sources"].([]any)[0].(map[string]any)
	dimensions := source["dimensions"].(map[string]any)
	var dimensionTokens int64
	for _, value := range dimensions {
		dimensionTokens += value.(map[string]any)["tokens"].(int64)
	}
	if source["total_tokens"].(int64) != dimensionTokens {
		t.Fatalf("published source totals disagree with dimensions: source=%v dimensions=%v", source["total_tokens"], dimensions)
	}
}

func TestDebugSnapshotWireFromDatabase(t *testing.T) {
	path := os.Getenv("YOSTATS_DEBUG_DB")
	if path == "" {
		t.Skip("set YOSTATS_DEBUG_DB")
	}
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1789432767, 0)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 900, ResolutionSeconds: 10}}, Now: func() time.Time { return now }})
	if err := service.Refresh(context.Background()); err != nil {
		t.Fatal(err)
	}
	snapshot, err := service.Snapshot(context.Background(), stats.SnapshotRequest{Start: float64(1789431870), End: float64(1789432770), Resolution: 10})
	if err != nil {
		t.Fatal(err)
	}
	wire := snapshotWire(snapshot, statsQuery{rangeSeconds: 900, resolution: 10})
	report := wire["cost_report"].(map[string]any)
	t.Logf("cost report=%+v", report)
}

func TestDeltaFromWireMapsEmitsChangesAndTombstones(t *testing.T) {
	previous := map[string]any{
		"source_generation": float64(1), "cache_generation": float64(1),
		"buckets":     []map[string]any{{"start": float64(0), "duration": float64(10), "series": map[string]any{"x": 1}}},
		"no_data":     []map[string]any{{"family": "gpu", "source_id": "gpu:0", "epoch": "e", "start": float64(0), "end": float64(10), "reason": "offline"}},
		"cost_report": map[string]any{},
	}
	current := map[string]any{
		"source_generation": float64(2), "cache_generation": float64(2),
		"buckets": []map[string]any{{"start": float64(10), "duration": float64(10), "series": map[string]any{"x": 2}}},
		"no_data": []map[string]any{}, "cost_report": map[string]any{},
	}
	delta := deltaFromWireMaps(previous, current, statsQuery{rangeSeconds: 20, resolution: 10, afterCacheGeneration: 1})
	if len(delta["buckets"].([]map[string]any)) != 1 || len(delta["no_data"].([]map[string]any)) != 0 {
		t.Fatalf("delta changes = %+v", delta)
	}
	tombstones := delta["tombstones"].([]any)
	if len(tombstones) != 2 {
		t.Fatalf("delta tombstones = %+v", tombstones)
	}
}

func TestStatsStreamEmitsInitialEventsAndReadyLiveness(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(time.Now().Unix(), 0)
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, now)
	defer writer.Close()
	s := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }, statsHeartbeat: 20 * time.Millisecond}
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
	if !strings.Contains(body, "event: ack") || !strings.Contains(body, "event: snapshot") || !strings.Contains(body, "event: ready") || strings.Contains(body, "event: heartbeat") {
		t.Fatalf("stream events = %s", body)
	}
	if strings.Count(body, "event: ready") < 3 {
		t.Fatalf("stream did not emit repeated ready liveness frames: %s", body)
	}
	ids := strings.Count(body, "id: ")
	frames := strings.Count(body, "event: ")
	if ids != frames || ids < 4 {
		t.Fatalf("stream SSE ids=%d frames=%d body=%s", ids, frames, body)
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
	nowTime := time.Unix(int64(now), 0)
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, nowTime)
	defer writer.Close()
	s := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return nowTime }}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	recorder := newSSETestRecorder()
	done := make(chan struct{})
	go func() {
		s.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=300&resolution=AUTO&client_id=wire-test", nil).WithContext(ctx))
		close(done)
	}()
	select {
	case <-recorder.ready:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not publish ready")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not stop")
	}

	frames := parseSSEFrames(t, recorder.bodyString())
	var snapshot map[string]any
	for _, frame := range frames {
		if frame.event == "snapshot" {
			snapshot = frame.data
			break
		}
	}
	if snapshot == nil {
		t.Fatalf("snapshot frame missing: %s", recorder.bodyString())
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

func normalizeSSECacheGeneration(value map[string]any) {
	for _, key := range []string{"ack", "snapshot", "ready"} {
		if payload, ok := value[key].(map[string]any); ok {
			if _, exists := payload["cache_generation"]; exists {
				payload["cache_generation"] = float64(0)
			}
		}
	}
}

func matrixRequestedResolution(rangeSeconds int, requested any) int {
	if value, ok := requested.(int); ok {
		return value
	}
	return autoResolution(rangeSeconds)
}

func normalizeSSEIDs(body string) string {
	lines := strings.Split(body, "\n")
	for index, line := range lines {
		if strings.HasPrefix(line, "id: ") {
			lines[index] = "id: <process-relative>"
		}
	}
	return strings.Join(lines, "\n")
}

func matrixOracleJSON(value any) string {
	data, _ := json.Marshal(value)
	return string(data)
}

func TestStatsDeltaUsesPythonNoDataIdentityAndOrdering(t *testing.T) {
	bucket := map[string]any{"start": float64(100), "duration": float64(10), "open": false, "series": map[string]any{}, "source": map[string]any{"count": float64(0)}}
	previous := map[string]any{
		"buckets": []map[string]any{bucket}, "no_data": []map[string]any{
			{"family": "gpu", "source_id": "gpu:0", "epoch": "gpu", "start": float64(10), "end": float64(20), "reason": "old"},
			{"family": "agent_status", "source_id": "host", "epoch": "agent", "start": float64(20), "end": float64(30), "reason": "gone"},
			{"family": "system_memory", "source_id": "host", "epoch": "memory", "start": float64(30), "end": float64(40), "reason": "gone"},
		},
		"cache_generation": float64(1), "source_generation": float64(1), "revision": float64(0), "cost_report": map[string]any{},
	}
	current := map[string]any{
		"buckets": []map[string]any{bucket}, "no_data": []map[string]any{
			{"family": "gpu", "source_id": "gpu:0", "epoch": "gpu", "start": float64(10), "end": float64(20), "reason": "new"},
		},
		"cache_generation": float64(2), "source_generation": float64(2), "revision": float64(1), "cost_report": map[string]any{},
	}
	delta := deltaFromWireMaps(previous, current, statsQuery{rangeSeconds: 300, resolution: 10, afterCacheGeneration: 1})
	noData := delta["no_data"].([]map[string]any)
	if len(noData) != 1 || noData[0]["family"] != "gpu" || noData[0]["reason"] != "new" {
		t.Fatalf("changed no-data span = %#v, want one replacement", noData)
	}
	tombstones := delta["tombstones"].([]any)
	if len(tombstones) != 2 {
		t.Fatalf("no-data tombstones = %#v, want two", tombstones)
	}
	first, ok := tombstones[0].(map[string]any)
	if !ok || first["family"] != "agent_status" {
		t.Fatalf("first tombstone = %#v, want agent_status", tombstones[0])
	}
	second, ok := tombstones[1].(map[string]any)
	if !ok || second["family"] != "system_memory" {
		t.Fatalf("second tombstone = %#v, want system_memory", tombstones[1])
	}
}

func TestStatsSnapshotSeedsDeltaHistoryForSubsequentPublication(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(time.Now().Unix(), 0)
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "snapshot-seed-initial", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 20), EpochID: "snapshot-seed", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 10.0, "system_percent": 5.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, MaxCells: 64, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 8 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("initial product is not ready: %+v", service.State())
	}
	s := &server{statsPath: path, statsWriter: writer, statsService: service, statsNow: func() time.Time { return now }, statsHeartbeat: time.Second}
	snapshotRecorder := httptest.NewRecorder()
	s.statsSnapshot(snapshotRecorder, httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=snapshot-seed", nil))
	if snapshotRecorder.Code != http.StatusOK {
		t.Fatalf("initial snapshot status=%d body=%s", snapshotRecorder.Code, snapshotRecorder.Body.String())
	}
	var snapshot map[string]any
	if err := json.Unmarshal(snapshotRecorder.Body.Bytes(), &snapshot); err != nil {
		t.Fatal(err)
	}
	baseGeneration, ok := snapshot["cache_generation"].(float64)
	if !ok || baseGeneration <= 0 {
		t.Fatalf("initial snapshot cache generation=%#v", snapshot["cache_generation"])
	}
	// A reconnect may receive a 304 when its generation is still current. It
	// must seed the same predecessor as a 200 snapshot before the next delta.
	s.statsDeltaMu.Lock()
	s.statsDeltaRevisions = nil
	s.statsDeltaNextRevision = nil
	s.statsDeltaMu.Unlock()
	unchanged := httptest.NewRecorder()
	s.statsSnapshot(unchanged, httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/stats-snapshot?range_seconds=300&resolution=10&client_id=snapshot-seed&since_generation=%d", int64(baseGeneration)), nil))
	if unchanged.Code != http.StatusNotModified {
		t.Fatalf("unchanged snapshot status=%d body=%s", unchanged.Code, unchanged.Body.String())
	}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "snapshot-seed-next", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 10), EpochID: "snapshot-seed", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 22.0, "system_percent": 7.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
		t.Fatal(err)
	}
	deltaRecorder := httptest.NewRecorder()
	query := fmt.Sprintf("/api/stats-delta?range_seconds=300&resolution_seconds=10&client_id=snapshot-seed&after_cache_generation=%d&after_revision=0", int64(baseGeneration))
	s.statsDelta(deltaRecorder, httptest.NewRequest(http.MethodGet, query, nil))
	if deltaRecorder.Code != http.StatusOK {
		t.Fatalf("delta after snapshot status=%d body=%s", deltaRecorder.Code, deltaRecorder.Body.String())
	}
	var delta map[string]any
	if err := json.Unmarshal(deltaRecorder.Body.Bytes(), &delta); err != nil {
		t.Fatal(err)
	}
	if delta["base_cache_generation"] != baseGeneration {
		t.Fatalf("delta base generation=%#v want %v", delta["base_cache_generation"], baseGeneration)
	}
}

func TestStatsStreamUsesReadyRevisionForFirstDelta(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(time.Now().Unix(), 0)
	initialAt := float64(now.Unix() - 20)
	if _, err := writer.AppendBatch(context.Background(), stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "stream-initial", Family: "cpu", SourceID: "host", ObservedAt: initialAt,
		EpochID: "stream-epoch", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 10.0, "system_percent": 5.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, MaxCells: 64, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 8 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(context.Background()); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(context.Background()); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("initial stats product is not ready: %+v", service.State())
	}
	s := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }, statsHeartbeat: time.Second}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	recorder := newSSETestRecorder()
	done := make(chan struct{})
	go func() {
		s.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=300&resolution=10&client_id=revision-test", nil).WithContext(ctx))
		close(done)
	}()
	select {
	case <-recorder.ready:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not emit ready")
	}
	if _, err := writer.AppendBatch(context.Background(), stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "stream-next", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 10),
		EpochID: "stream-epoch", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 22.0, "system_percent": 7.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(context.Background()); err != nil && !errors.Is(err, stats.ErrProductPending) {
		t.Fatal(err)
	}
	select {
	case event := <-recorder.changed:
		if event != "delta" {
			t.Fatalf("first post-ready event = %q, want delta", event)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not emit a delta")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not stop")
	}
	frames := parseSSEFrames(t, recorder.bodyString())
	var ready map[string]any
	var delta map[string]any
	for _, frame := range frames {
		switch frame.event {
		case "ready":
			ready = frame.data
		case "delta":
			delta = frame.data
		case "unavailable", "repair":
			t.Fatalf("stream terminated with %s: %#v", frame.event, frame.data)
		}
	}
	if ready == nil || ready["revision"] != float64(0) {
		t.Fatalf("ready revision = %#v, want 0", ready)
	}
	if delta == nil || delta["revision"] != float64(1) {
		t.Fatalf("delta revision = %#v, want 1", delta)
	}
}

func TestStatsStreamConcurrentClientsReceiveOnePublication(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(time.Now().Unix(), 0)
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "concurrent-initial", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 20),
		EpochID: "concurrent-epoch", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 10.0, "system_percent": 5.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, MaxCells: 64, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 8 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("initial stats product is not ready: %+v", service.State())
	}
	server := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }, statsHeartbeat: time.Second}
	const clientCount = 8
	cancels := make([]context.CancelFunc, 0, clientCount)
	dones := make([]chan struct{}, 0, clientCount)
	recorders := make([]*sseTestRecorder, 0, clientCount)
	for index := 0; index < clientCount; index++ {
		streamContext, cancel := context.WithCancel(ctx)
		cancels = append(cancels, cancel)
		recorder := newSSETestRecorder()
		recorders = append(recorders, recorder)
		done := make(chan struct{})
		dones = append(dones, done)
		clientID := fmt.Sprintf("concurrent-%d", index)
		go func() {
			server.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=300&resolution=10&client_id="+clientID, nil).WithContext(streamContext))
			close(done)
		}()
	}
	for index, recorder := range recorders {
		select {
		case <-recorder.ready:
		case <-time.After(2 * time.Second):
			for _, cancel := range cancels {
				cancel()
			}
			t.Fatalf("SSE client %d did not become ready", index)
		}
	}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "concurrent-next", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 10),
		EpochID: "concurrent-epoch", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 22.0, "system_percent": 7.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
		t.Fatal(err)
	}
	for index, recorder := range recorders {
		select {
		case event := <-recorder.changed:
			if event != "delta" {
				t.Fatalf("SSE client %d received %q, want delta", index, event)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("SSE client %d did not receive the publication delta", index)
		}
	}
	for _, cancel := range cancels {
		cancel()
	}
	for index, done := range dones {
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Fatalf("SSE client %d did not stop", index)
		}
	}
	for index, recorder := range recorders {
		for _, frame := range parseSSEFrames(t, recorder.bodyString()) {
			if frame.event == "repair" || frame.event == "unavailable" {
				t.Fatalf("SSE client %d terminated with %s: %#v", index, frame.event, frame.data)
			}
		}
	}
}

func TestStatsHTTPAndSSEProcessSoakStaysBounded(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(time.Now().Unix(), 0)
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "http-soak-initial", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 20),
		EpochID: "http-soak", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 10.0, "system_percent": 5.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, MaxCells: 64, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 8 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("HTTP soak product did not become ready: %+v", service.State())
	}
	diagnostics := newStatsSoakDiagnostics(t, path)
	server := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }, statsHeartbeat: 20 * time.Millisecond}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/stats-snapshot", diagnostics.handler(server.statsSnapshot))
	mux.HandleFunc("/api/stats-stream", diagnostics.handler(server.statsStream))
	httpServer := httptest.NewServer(mux)
	diagnostics.listener = httpServer.Listener.Addr().String()
	defer httpServer.Close()

	const streamClients = 8
	streamContext, cancelStreams := context.WithCancel(context.Background())
	defer cancelStreams()
	ready := make(chan struct{}, streamClients)
	delta := make(chan struct{}, streamClients)
	streamErrors := make(chan error, streamClients)
	streamDone := make(chan struct{}, streamClients)
	for index := 0; index < streamClients; index++ {
		go func(clientID string) {
			defer func() { streamDone <- struct{}{} }()
			request, requestErr := http.NewRequestWithContext(streamContext, http.MethodGet, httpServer.URL+"/api/stats-stream?range_seconds=300&resolution=10&client_id="+clientID, nil)
			if requestErr != nil {
				streamErrors <- requestErr
				return
			}
			response, requestErr := http.DefaultClient.Do(request)
			if requestErr != nil {
				if streamContext.Err() == nil {
					streamErrors <- requestErr
				}
				return
			}
			defer response.Body.Close()
			if response.StatusCode != http.StatusOK || !strings.HasPrefix(response.Header.Get("Content-Type"), "text/event-stream") {
				streamErrors <- fmt.Errorf("stream status/content-type = %d/%q", response.StatusCode, response.Header.Get("Content-Type"))
				return
			}
			scanner := bufio.NewScanner(response.Body)
			event := ""
			for scanner.Scan() {
				line := scanner.Text()
				if strings.HasPrefix(line, "event: ") {
					event = strings.TrimPrefix(line, "event: ")
				} else if line == "" {
					switch event {
					case "ready":
						select {
						case ready <- struct{}{}:
						default:
						}
					case "delta":
						select {
						case delta <- struct{}{}:
						default:
						}
					case "repair", "unavailable":
						streamErrors <- fmt.Errorf("stream emitted terminal %s", event)
					}
					event = ""
				}
			}
			if scanErr := scanner.Err(); scanErr != nil && streamContext.Err() == nil {
				streamErrors <- scanErr
			}
		}(fmt.Sprintf("http-soak-%d", index))
	}
	for index := 0; index < streamClients; index++ {
		select {
		case <-ready:
		case requestErr := <-streamErrors:
			cancelStreams()
			t.Fatalf("SSE client failed before ready: %v", requestErr)
		case <-time.After(2 * time.Second):
			cancelStreams()
			t.Fatal("SSE process client did not become ready")
		}
	}

	const snapshotClients = 16
	const snapshotsPerClient = 12
	httpClient := &http.Client{Timeout: 2 * time.Second}
	latencies := make(chan time.Duration, snapshotClients*snapshotsPerClient)
	snapshotErrors := make(chan error, snapshotClients)
	var workers sync.WaitGroup
	for index := 0; index < snapshotClients; index++ {
		workers.Add(1)
		go func(clientID string) {
			defer workers.Done()
			for requestIndex := 0; requestIndex < snapshotsPerClient; requestIndex++ {
				id := fmt.Sprintf("%s-%d", clientID, requestIndex)
				diagnostics.mark(context.Background(), id, "request_start", "")
				started := time.Now()
				request, finishTask, requestErr := diagnostics.request(httpServer.URL+"/api/stats-snapshot?range_seconds=300&resolution=10&client_id="+clientID, id)
				if requestErr != nil {
					latencies <- time.Since(started)
					snapshotErrors <- requestErr
					return
				}
				response, requestErr := httpClient.Do(request)
				elapsed := time.Since(started)
				latencies <- elapsed
				diagnostics.mark(request.Context(), id, "headers_returned", fmt.Sprintf("duration_ns=%d error=%v", elapsed.Nanoseconds(), requestErr))
				finishTask()
				if requestErr != nil {
					snapshotErrors <- requestErr
					return
				}
				var payload map[string]any
				decodeErr := json.NewDecoder(response.Body).Decode(&payload)
				response.Body.Close()
				if response.StatusCode != http.StatusOK || decodeErr != nil || payload["cache_generation"] == nil {
					snapshotErrors <- fmt.Errorf("snapshot status/decode/generation = %d/%v/%v", response.StatusCode, decodeErr, payload["cache_generation"])
					return
				}
			}
		}(fmt.Sprintf("snapshot-%d", index))
	}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "http-soak-next", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 10),
		EpochID: "http-soak", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 22.0, "system_percent": 7.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
		t.Fatal(err)
	}
	for index := 0; index < streamClients; index++ {
		select {
		case <-delta:
		case requestErr := <-streamErrors:
			cancelStreams()
			t.Fatalf("SSE client failed during publication: %v", requestErr)
		case <-time.After(2 * time.Second):
			cancelStreams()
			t.Fatal("SSE process client did not receive publication delta")
		}
	}
	workers.Wait()
	close(snapshotErrors)
	for requestErr := range snapshotErrors {
		cancelStreams()
		t.Fatalf("HTTP snapshot process soak failed: %v", requestErr)
	}
	close(latencies)
	var maximum time.Duration
	count := 0
	for latency := range latencies {
		count++
		if latency > maximum {
			maximum = latency
		}
	}
	if count != snapshotClients*snapshotsPerClient {
		cancelStreams()
		t.Fatalf("HTTP snapshot count = %d, want %d", count, snapshotClients*snapshotsPerClient)
	}
	if maximum >= 500*time.Millisecond {
		cancelStreams()
		t.Fatalf("HTTP snapshot latency exceeded bound: max=%s", maximum)
	}
	cancelStreams()
	for index := 0; index < streamClients; index++ {
		select {
		case <-streamDone:
		case <-time.After(2 * time.Second):
			t.Fatal("SSE process client did not stop")
		}
	}
	t.Logf("HTTP/SSE process soak: streams=%d snapshots=%d max_snapshot_latency=%s", streamClients, count, maximum)
}

func TestStatsStreamEmitsRepairWhenDeltaBaseIsEvicted(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(time.Now().Unix(), 0)
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "stream-repair-initial", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 20),
		EpochID: "stream-repair-epoch", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 10.0, "system_percent": 5.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, MaxCells: 64, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 16 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("initial stats product is not ready: %+v", service.State())
	}
	s := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }, statsHeartbeat: 10 * time.Millisecond}
	streamCtx, cancel := context.WithCancel(ctx)
	recorder := newSSETestRecorder()
	done := make(chan struct{})
	go func() {
		s.statsStream(recorder, httptest.NewRequest(http.MethodGet, "/api/stats-stream?range_seconds=300&resolution=1&client_id=repair-test", nil).WithContext(streamCtx))
		close(done)
	}()
	select {
	case <-recorder.ready:
	case <-time.After(2 * time.Second):
		cancel()
		t.Fatal("stats stream did not emit ready")
	}
	frames := parseSSEFrames(t, recorder.bodyString())
	var ready map[string]any
	for _, frame := range frames {
		if frame.event == "ready" {
			ready = frame.data
		}
	}
	if ready == nil {
		cancel()
		t.Fatal("ready frame was not retained")
	}
	baseGeneration, ok := ready["cache_generation"].(float64)
	if !ok || baseGeneration <= 0 {
		cancel()
		t.Fatalf("ready cache generation = %#v", ready["cache_generation"])
	}
	key := statsDeltaKey{rangeSeconds: 300, resolution: 1}
	s.statsDeltaMu.Lock()
	delete(s.statsDeltaRevisions[key], int64(baseGeneration))
	s.statsDeltaMu.Unlock()
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "stream-repair-next", Family: "cpu", SourceID: "host", ObservedAt: float64(now.Unix() - 10),
		EpochID: "stream-repair-epoch", OwnerGeneration: 1,
		Payload: map[string]any{"process_percent": 22.0, "system_percent": 7.0},
	}}}); err != nil {
		cancel()
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		cancel()
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
		cancel()
		t.Fatal(err)
	}
	select {
	case event := <-recorder.changed:
		if event != "repair" {
			cancel()
			t.Fatalf("evicted delta event = %q, want repair", event)
		}
	case <-time.After(2 * time.Second):
		cancel()
		t.Fatal("stats stream did not emit repair")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("stats stream did not stop after repair")
	}
	seenRepair, seenUnavailable := false, false
	for _, frame := range parseSSEFrames(t, recorder.bodyString()) {
		switch frame.event {
		case "repair":
			seenRepair = true
			if frame.data["status"] != "repair_required" {
				t.Fatalf("repair frame = %#v", frame.data)
			}
		case "unavailable":
			seenUnavailable = true
		}
	}
	if !seenRepair || seenUnavailable {
		t.Fatalf("repair frames: seenRepair=%v seenUnavailable=%v body=%s", seenRepair, seenUnavailable, recorder.bodyString())
	}
}

type sseTestRecorder struct {
	mu        sync.Mutex
	header    http.Header
	body      bytes.Buffer
	ready     chan struct{}
	changed   chan string
	readyOnce sync.Once
}

func newSSETestRecorder() *sseTestRecorder {
	return &sseTestRecorder{header: make(http.Header), ready: make(chan struct{}), changed: make(chan string, 1)}
}

func (r *sseTestRecorder) Header() http.Header    { return r.header }
func (r *sseTestRecorder) WriteHeader(status int) {}
func (r *sseTestRecorder) Flush()                 {}
func (r *sseTestRecorder) Write(data []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.header == nil {
		r.header = make(http.Header)
	}
	_, err := r.body.Write(data)
	text := string(data)
	if strings.Contains(text, "event: ready") {
		r.readyOnce.Do(func() { close(r.ready) })
	}
	for _, event := range []string{"delta", "unavailable", "repair"} {
		if strings.Contains(text, "event: "+event) {
			select {
			case r.changed <- event:
			default:
			}
			break
		}
	}
	return len(data), err
}
func (r *sseTestRecorder) bodyString() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.body.String()
}

func TestStatsReadyReflectsLifecycleState(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, MaxCells: 512, Now: func() time.Time { return time.Unix(1000, 0) }})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, Now: func() time.Time { return time.Unix(1000, 0) }})
	s := &server{statsPath: path, statsWriter: writer, statsService: service, statsLifecycle: lifecycle}
	before := httptest.NewRecorder()
	s.ready(before, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if before.Code != http.StatusServiceUnavailable || !bytes.Contains(before.Body.Bytes(), []byte(`"ready":false`)) {
		t.Fatalf("before materializer run: status=%d body=%s", before.Code, before.Body.String())
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	after := httptest.NewRecorder()
	s.ready(after, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if after.Code != http.StatusOK || !bytes.Contains(after.Body.Bytes(), []byte(`"ready":true`)) {
		t.Fatalf("after materializer run: status=%d body=%s", after.Code, after.Body.String())
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

func runPythonWireOracle(t *testing.T, scriptPath, fixturePath string) []byte {
	t.Helper()
	root := testsupport.RepoRoot(t)
	command := exec.Command("python3", scriptPath, fixturePath)
	command.Dir = root
	command.Env = testsupport.SyntheticEnvironment("PYTHONDONTWRITEBYTECODE=1", "PYTHONPATH="+root)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("Python wire oracle failed: %v\n%s", err, output)
	}
	return output
}

func statsMapKeys(value map[string]any) []string {
	keys := make([]string, 0, len(value))
	for key := range value {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func publishedStatsService(t *testing.T, writer *stats.Writer, keys []stats.ProductKey, now time.Time) *stats.StatsService {
	t.Helper()
	window := 1
	for _, key := range keys {
		if key.RangeSeconds > window {
			window = key.RangeSeconds
		}
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: int64(window), Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: keys, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 64; attempt++ {
		if err := lifecycle.RunOnce(context.Background()); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(context.Background()); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
		if service.State().Ready {
			return service
		}
	}
	t.Fatalf("stats products did not become ready after bounded rebuilds: %+v", service.State())
	return nil
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

func TestStatsAgentStatusDoesNotReadSQLiteFromHTTPHandler(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
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
	s := &server{statsService: agentStatusStatsService(path)}
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
	// Keep this route-shape test hermetic.  It only serializes diagnostics; it must
	// never resolve the developer's HOME/XDG roots or probe a real OpenCode DB.
	fixtureRoot := t.TempDir()
	collector := stats.NewAgentTokenCollector(nil, nil, stats.AgentTokenCollectorConfig{
		ClaudeTranscriptRoot: fixtureRoot,
		CodexTranscriptRoot:  fixtureRoot,
		OpenCodeDatabase:     filepath.Join(fixtureRoot, "opencode.db"),
		CursorPath:           filepath.Join(fixtureRoot, "opencode-cursors.json"),
		SkipTranscriptScan:   true,
		Inventory:            func(context.Context) ([]stats.AgentRecord, []error) { return nil, nil },
		OpenCodeInventory:    func(context.Context) ([]stats.AgentRecord, []error) { return nil, nil },
	})
	s := &server{statsService: agentStatusStatsService(path), tokenCollector: collector}
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
	nowTime := time.Unix(int64(now), 0)
	price := func(atom stats.UsageAtom) (int64, int64, *stats.PriceEvidence, bool, error) {
		if atom.EventID != "usage-1" {
			return 0, 0, nil, false, nil
		}
		return 2400, 3000, nil, true, nil
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, Now: func() time.Time { return nowTime }, Price: price})
	t.Cleanup(lifecycle.Stop)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 1}}, Now: func() time.Time { return nowTime }, Price: price})
	for attempt := 0; attempt < 64 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("persisted stats product did not become ready: %+v", service.State())
	}
	defer writer.Close()
	server := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return nowTime }}
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
	if payload.SourceGeneration != 1 || payload.CacheGeneration <= 0 || payload.GeneratedAt <= 0 {
		t.Fatalf("generation metadata = %+v", payload)
	}
	var processCPU float64
	for _, bucket := range payload.Buckets {
		if series, ok := bucket.Series["cpu_percent:host"]; ok {
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

func TestStatsSnapshotCostUsesPythonShapedPricingCatalog(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	catalogPath := filepath.Join(root, "synthetic-pricing.json")
	catalogJSON := `{"schema_version":1,"catalog_revision":42,"models":[{"provider":"synthetic-provider","model":"synthetic-model","aliases":["synthetic-model"],"source":{"kind":"fixture","url":"https://example.invalid/synthetic-pricing"},"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"2.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(catalogPath, []byte(catalogJSON), 0o600); err != nil {
		t.Fatal(err)
	}
	catalog, err := stats.LoadRepositoryPricingCatalog("", catalogPath)
	if err != nil {
		t.Fatal(err)
	}
	resolver := stats.PricingResolver(catalog)
	path := filepath.Join(root, "synthetic-stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1000, 0)
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{UsageAtoms: []stats.UsageAtom{{
		EventID: "synthetic-priced-usage", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 990,
		Payload: map[string]any{"quantity": 1000.0, "provider": "synthetic-provider", "model": "synthetic-model", "agent_id": "synthetic-agent", "thread_id": "synthetic-thread"},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := stats.NewLifecycle(writer, stats.LifecycleConfig{WindowSeconds: 300, MaxCells: 64, Price: resolver, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(300, 10)
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, Price: resolver, Now: func() time.Time { return now }})
	for attempt := 0; attempt < 64 && !service.State().Ready; attempt++ {
		if err := lifecycle.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
			t.Fatal(err)
		}
	}
	if !service.State().Ready {
		t.Fatalf("synthetic priced product did not become ready: %+v", service.State())
	}
	server := &server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }}
	record := httptest.NewRecorder()
	server.statsSnapshot(record, httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=synthetic-cost", nil))
	if record.Code != http.StatusOK {
		t.Fatalf("status = %d body = %s", record.Code, record.Body.String())
	}
	var payload struct {
		CostReport struct {
			TotalTokens       int64 `json:"total_tokens"`
			TotalMicroUSD     int64 `json:"total_micro_usd"`
			TotalAPIListMicro int64 `json:"total_api_list_micro_usd"`
			Evidence          []struct {
				CatalogRevision int64 `json:"catalog_revision"`
			} `json:"evidence"`
		} `json:"cost_report"`
	}
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.CostReport.TotalTokens != 1000 || payload.CostReport.TotalMicroUSD != 2000 || payload.CostReport.TotalAPIListMicro != 2000 {
		t.Fatalf("synthetic catalog cost report = %+v body=%s", payload.CostReport, record.Body.String())
	}
	if len(payload.CostReport.Evidence) == 0 || payload.CostReport.Evidence[0].CatalogRevision != 42 {
		t.Fatalf("synthetic catalog evidence = %+v body=%s", payload.CostReport.Evidence, record.Body.String())
	}
}

func TestCurrentStatsSnapshotAllowsUnrelatedSourceGeneration(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1000, 0)
	seed := make([]stats.RingBucketWrite, 0, 91)
	for bucketStart := int64(100); bucketStart < 1010; bucketStart += 10 {
		payload, encodeErr := json.Marshal(stats.MaterializedBucket{Start: float64(bucketStart), Duration: 10, Complete: true})
		if encodeErr != nil {
			t.Fatal(encodeErr)
		}
		seed = append(seed, stats.RingBucketWrite{ResolutionSeconds: 10, BucketStart: bucketStart, BucketJSON: string(payload), Complete: true})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, 1000, true); err != nil {
		t.Fatal(err)
	}
	keys := []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}, {RangeSeconds: 900, ResolutionSeconds: 10}}
	service := stats.NewStatsService(writer, stats.StatsServiceConfig{Products: keys, Now: func() time.Time { return now }})
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if !service.State().Ready {
		t.Fatalf("initial service state = %+v", service.State())
	}
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{Observations: []stats.Observation{{
		EventID: "outside-short-window", Family: "cpu", SourceID: "host", ObservedAt: 500, EpochID: "epoch",
		Payload: map[string]any{"process_percent": 9},
	}}}); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	server := &server{statsWriter: writer, statsService: service, statsNow: func() time.Time { return now }}
	short, err := server.currentStatsSnapshot(ctx, statsQuery{rangeSeconds: 300, resolution: 10, clientID: "fixture"})
	if err != nil {
		t.Fatalf("unaffected product rejected after unrelated source write: %v", err)
	}
	if short.Request.End != 1010 || short.Request.Start != 710 {
		t.Fatalf("unaffected product window = %+v", short.Request)
	}
	// HTTP asks for the newest retained product by key. Crossing a wall-clock
	// bucket boundary after Refresh must not turn that complete product into a
	// false pending response.
	server.statsNow = func() time.Time { return time.Unix(1011, 0) }
	rolled, err := server.currentStatsSnapshot(ctx, statsQuery{rangeSeconds: 300, resolution: 10, clientID: "fixture"})
	if err != nil {
		t.Fatalf("retained product rejected after clock rollover: %v", err)
	}
	if rolled.Request != short.Request {
		t.Fatalf("clock rollover changed retained product bounds: before=%+v after=%+v", short.Request, rolled.Request)
	}
	affected, err := server.currentStatsSnapshot(ctx, statsQuery{rangeSeconds: 900, resolution: 10, clientID: "fixture"})
	if err != nil {
		t.Fatalf("affected product rejected while rebuilding its newer generation: %v", err)
	}
	if affected.SourceGeneration != 1 {
		t.Fatalf("affected product served an uncommitted generation: %+v", affected)
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
	nowTime := time.Unix(int64(now), 0)
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, nowTime)
	defer writer.Close()
	record := httptest.NewRecorder()
	(&server{statsPath: path, statsService: service, statsNow: func() time.Time { return nowTime }}).statsSnapshot(record, httptest.NewRequest(http.MethodGet, "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=fixture", nil))
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

func agentStatusStatsService(path string) *stats.StatsService {
	service := stats.NewStatsService(nil, stats.StatsServiceConfig{
		AgentStatus: func(ctx context.Context) (stats.AgentStatusSnapshot, error) {
			return stats.ReadAgentStatus(ctx, path, stats.AgentStatusQuery{})
		},
	})
	_ = service.RefreshAgentStatus(context.Background())
	return service
}

func TestStatsObservationDelayedBodyPhasesAndParallelPing(t *testing.T) {
	s := &server{}
	reader, writer := io.Pipe()
	defer reader.Close()
	defer writer.Close()
	request := httptest.NewRequest(http.MethodPost, "/api/stats-observations", reader)
	request.Header.Set("X-YOmux-Request-ID", "r-held-body")
	record := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { defer close(done); s.statsObservations(record, request) }()
	// A completed pipe write proves the handler is reading the partial body.
	if _, err := writer.Write([]byte(`{"observations":`)); err != nil {
		t.Fatal(err)
	}
	ping := httptest.NewRecorder()
	s.ping(ping, httptest.NewRequest(http.MethodGet, "/api/ping", nil))
	if ping.Code != 200 {
		t.Fatalf("parallel ping = %d", ping.Code)
	}
	select {
	case <-done:
		t.Fatal("partial body unexpectedly completed")
	default:
	}
	writer.CloseWithError(io.ErrUnexpectedEOF)
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("body failure did not complete")
	}
	if record.Code != 400 || record.Header().Get("X-YOmux-Request-ID") != "r-held-body" {
		t.Fatalf("response = %d %v", record.Code, record.Header())
	}
	timing := record.Header().Get("Server-Timing")
	for _, phase := range []string{"body;dur=", "validation;dur=", "writer_wait;dur=", "writer_commit;dur="} {
		if !strings.Contains(timing, phase) {
			t.Fatalf("missing %s in %s", phase, timing)
		}
	}
}

func TestBrowserDiagnosticPhaseAndReplayPayloads(t *testing.T) {
	for _, payload := range []map[string]any{
		{"kind": "sse", "latency_ms": 780000.0, "server_event_id": 7.0, "publication_time_ms": 120000.0, "clock_ahead_ms": 0.0, "replay_count": 1.0, "server_epoch": "epoch"},
		{"kind": "api", "server_request_id": "r-upload", "server_body_ms": 17523.0, "server_validation_ms": 1.0, "server_writer_wait_ms": 0.0, "server_writer_validation_ms": 0.0, "server_writer_transaction_ms": 0.0, "server_writer_commit_ms": 0.0},
	} {
		if err := validateBrowserPayload(payload); err != nil {
			t.Fatal(err)
		}
	}
}
