package stats

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

// TestMaterializerMatchesPythonAcrossCurrentMatrix keeps the two materializers
// honest against one fixture. The comparison deliberately uses the complete
// typed layer shape rather than a handful of representative series: source
// provenance, completion, and cost attribution are part of the current wire
// contract too.
func TestMaterializerMatchesPythonAcrossCurrentMatrix(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	fixturePath := filepath.Join(root, "materializer-fixture.json")
	scriptPath := filepath.Join(root, "materializer-oracle.py")
	observations := []Observation{
		{EventID: "cpu-a", Family: "cpu", SourceID: "host", ObservedAt: 100, EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 20.0, "system_percent": 40.0, "process_cpu_percent": map[string]any{"python": 4.0}}},
		{EventID: "cpu-b", Family: "cpu", SourceID: "host", ObservedAt: 100.5, EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 30.0, "system_percent": 50.0, "process_cpu_percent": map[string]any{"python": 6.0}}},
		{EventID: "cpu-c", Family: "cpu", SourceID: "host-2", ObservedAt: 101, EpochID: "cpu-2", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 10.0, "system_percent": 20.0, "process_cpu_percent": map[string]any{"python": 2.0}}},
		{EventID: "service", Family: "service_load", SourceID: "statsd", ObservedAt: 102, EpochID: "service", OwnerGeneration: 1, Payload: map[string]any{"running": true, "cpu_percent": 3.0, "rss_bytes": 1000.0}},
		{EventID: "service-rss-only", Family: "service_load", SourceID: "yolomux-go", ObservedAt: 102.5, EpochID: "service", OwnerGeneration: 1, Payload: map[string]any{"running": true, "cpu_percent": 0.0, "rss_bytes": 2048.0}},
		{EventID: "gpu", Family: "gpu", SourceID: "gpu:0", ObservedAt: 103, EpochID: "gpu", OwnerGeneration: 1, Payload: map[string]any{"util_percent": 50.0, "memory_used_bytes": 100.0, "memory_capacity_bytes": 200.0, "label": "GPU 0"}},
		{EventID: "memory", Family: "system_memory", SourceID: "host", ObservedAt: 104, EpochID: "memory", OwnerGeneration: 1, Payload: map[string]any{"used_bytes": 100.0, "capacity_bytes": 200.0, "process_memory_bytes": map[string]any{"python": 7.0}}},
		{EventID: "agents", Family: "agent_status", SourceID: "host", ObservedAt: 105, EpochID: "agents", OwnerGeneration: 1, Payload: map[string]any{"states": map[string]any{"ask": "ask", "run": "run", "idle": "idle"}, "session_states": map[string]any{"session": "run"}, "snapshot_revision": 4.0}},
		{EventID: "browser-api", Family: "browser", SourceID: "browser:a", ObservedAt: 106, EpochID: "browser", OwnerGeneration: 1, Payload: map[string]any{"kind": "api", "latency_ms": 8.0, "bytes": 100.0, "queue_ms": 2.0}},
		{EventID: "browser-api-2", Family: "browser", SourceID: "browser:a", ObservedAt: 106.5, EpochID: "browser", OwnerGeneration: 1, Payload: map[string]any{"kind": "api", "latency_ms": 16.0, "bytes": 200.0, "queue_ms": 4.0}},
		{EventID: "browser-sse", Family: "browser", SourceID: "browser:b", ObservedAt: 107, EpochID: "browser", OwnerGeneration: 1, Payload: map[string]any{"kind": "sse", "latency_ms": 12.0, "bytes": 200.0}},
		{EventID: "browser-disconnect", Family: "browser", SourceID: "browser:a", ObservedAt: 108, EpochID: "browser", OwnerGeneration: 1, Payload: map[string]any{"kind": "disconnect", "duration_ms": 50.0}},
		{EventID: "browser-load", Family: "browser", SourceID: "browser:b", ObservedAt: 109, EpochID: "browser", OwnerGeneration: 1, Payload: map[string]any{"kind": "page_load", "endpoint": "/stats", "first_paint_ms": 20.0, "first_contentful_paint_ms": 30.0, "app_ready_ms": 40.0, "max_concurrency": 2.0}},
		{EventID: "browser-heartbeat", Family: "browser", SourceID: "browser:a", ObservedAt: 110, EpochID: "browser", OwnerGeneration: 1, Payload: map[string]any{"kind": "heartbeat", "upload_queue_depth": 1.0, "upload_drops": 0.0, "upload_retries": 1.0, "instrumentation_cost_ms": 3.0}},
		{EventID: "browser-interaction", Family: "browser", SourceID: "browser:b", ObservedAt: 111, EpochID: "browser", OwnerGeneration: 1, Payload: map[string]any{"kind": "interaction", "latency_ms": 5.0}},
	}
	coverage := make([]CoverageEpoch, 0, 8)
	for _, item := range []struct{ family, source string }{
		{"cpu", "host"}, {"service_load", "statsd"}, {"service_load", "yolomux-go"}, {"gpu", "gpu:0"},
		{"system_memory", "host"}, {"agent_status", "host"}, {"browser", "browser:a"}, {"browser", "browser:b"},
	} {
		cadence := 1.0
		if item.family != "cpu" && item.family != "browser" {
			cadence = 10
		}
		coverage = append(coverage, CoverageEpoch{Family: item.family, SourceID: item.source, EpochID: item.family + ":" + item.source, StartedAt: 0, NativeCadenceSeconds: cadence, OwnerGeneration: 1})
	}
	atoms := []UsageAtom{
		{EventID: "input", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 112, Payload: map[string]any{"quantity": 10.0, "agent_id": "agent-a", "model": "model-a", "provider": "provider-a", "execution_source": "codex", "telemetry_complete": true}},
		{EventID: "output", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 113, Payload: map[string]any{"quantity": 5.0, "agent_id": "agent-a", "model": "model-a", "provider": "provider-a", "execution_source": "codex", "telemetry_complete": true}},
		{EventID: "cache", Direction: "input", Modality: "text", CacheRole: "read", Unit: "tokens", ObservedAt: 114, Payload: map[string]any{"quantity": 7.0, "agent_id": "agent-b", "model": "model-b", "provider": "provider-b", "execution_source": "claude", "telemetry_complete": false}},
	}
	unavailable := []UnavailableSpan{
		{Family: "gpu", SourceID: "gpu:0", EpochID: "gpu-gap", StartedAt: 100.25, EndedAt: 100.75, NativeCadenceSeconds: 10, Reason: "driver unavailable", OwnerGeneration: 1},
		{Family: "gpu", SourceID: "gpu:0", EpochID: "gpu-gap-2", StartedAt: 100.75, EndedAt: 101.25, NativeCadenceSeconds: 10, Reason: "driver unavailable", OwnerGeneration: 1},
	}
	// Reach the current generated layer (whose 300-second horizon ends at 86400)
	// so every declared family and usage fold participates in the oracle.
	for index := range observations {
		observations[index].ObservedAt += 86300
	}
	for index := range atoms {
		atoms[index].ObservedAt += 86300
	}
	for index := range unavailable {
		unavailable[index].StartedAt += 86300
		unavailable[index].EndedAt += 86300
	}
	fixture := map[string]any{"observations": observations, "coverage": coverage, "atoms": atoms, "unavailable": unavailable}
	data, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(fixturePath, data, 0o600); err != nil {
		t.Fatal(err)
	}
	const oracle = `import dataclasses, json, sys
from yomux_lib.stats_current import materializer, storage

fixture = json.loads(open(sys.argv[1], encoding="utf-8").read())
observations = tuple(storage.Observation(
    item["EventID"], item["Family"], item["SourceID"], item["ObservedAt"],
    item["EpochID"], item["OwnerGeneration"], item["Payload"],
) for item in fixture["observations"])
coverage = tuple(storage.CoverageEpoch(
    item["Family"], item["SourceID"], item["EpochID"], item["StartedAt"],
    item.get("EndedAt"), item["NativeCadenceSeconds"], item["OwnerGeneration"],
) for item in fixture["coverage"])
atoms = tuple(storage.UsageAtom(
    item["EventID"], item["Direction"], item["Modality"], item["CacheRole"],
    item["Unit"], item["ObservedAt"], item["Payload"],
) for item in fixture["atoms"])
unavailable = tuple(storage.UnavailableSpan(
    item["Family"], item["SourceID"], item["EpochID"], item["StartedAt"],
    item["EndedAt"], item["NativeCadenceSeconds"], item["Reason"],
    item["OwnerGeneration"],
) for item in fixture["unavailable"])
result = {}
for range_seconds in materializer.RANGES:
    for requested in ("AUTO", *materializer.stats_resolution.explicit_resolutions(range_seconds)):
        resolution = materializer.resolve_resolution(range_seconds, requested)
        # Go's layer request ends on a bucket boundary and therefore marks its
        # rightmost bucket complete. Build Python's layer one bucket beyond the
        # same start/end pair so both sides describe the identical cells.
        end = 86400 + resolution
        snapshot = storage.StoreSnapshot(storage.SchemaMetadata(9, 25, 8, 1), observations, coverage, atoms, (), unavailable)
        generation = materializer.build_generation(snapshot, source_generation=1, cache_generation=1, generated_at=end, observed_until=end - 1e-6)
        layer = materializer.slice_generation(generation, range_seconds, requested)
        value = dataclasses.asdict(layer)
        # Python generations carry a strict observed-until ceiling while the
        # pure Go layer request carries the aligned half-open End boundary.
        # Normalize only this representational edge so the oracle compares
        # facts, not the epsilon used to keep Python's final bucket closed.
        for item in value["no_data"]:
            if abs(item["end"] - end) < 1e-5:
                item["end"] = end
        # Go's pure layer builder receives an already aligned half-open cell;
        # the serving wire independently marks the rightmost bucket open. Keep
        # this oracle focused on values and provenance, then cover wire-open
        # semantics in the HTTP/SSE contract gate.
        value["buckets"][-1]["complete"] = True
        result[f"{range_seconds}/{resolution}"] = value
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
`
	if err := os.WriteFile(scriptPath, []byte(oracle), 0o600); err != nil {
		t.Fatal(err)
	}
	expectedRaw := runPython(t, python, scriptPath, fixturePath)
	var expected map[string]any
	if err := json.Unmarshal([]byte(expectedRaw), &expected); err != nil {
		t.Fatalf("decode Python materializer oracle: %v\n%s", err, expectedRaw)
	}
	for _, rangeSeconds := range []int{300, 900, 1800, 3600, 7200, 14400, 28800, 57600, 86400} {
		for _, requested := range append([]any{"AUTO"}, explicitResolutionsForRange(rangeSeconds)...) {
			resolution := requestedResolutionForRange(rangeSeconds, requested)
			end := float64(86400 + resolution)
			start := end - float64(rangeSeconds)
			layer, err := (Materializer{}).Build(MaterializeRequest{Start: start, End: end, Resolution: float64(resolution)}, observations, coverage, unavailable, atoms)
			if err != nil {
				t.Fatalf("Go materializer %ds/%v: %v", rangeSeconds, requested, err)
			}
			gotRaw, err := json.Marshal(canonicalMaterializedLayer(layer))
			if err != nil {
				t.Fatalf("encode Go materializer %ds/%v: %v", rangeSeconds, requested, err)
			}
			var got any
			if err := json.Unmarshal(gotRaw, &got); err != nil {
				t.Fatal(err)
			}
			key := fmt.Sprintf("%d/%d", rangeSeconds, resolution)
			expectedValue, exists := expected[key]
			if !exists {
				t.Fatalf("Python materializer oracle omitted key %s (keys=%v)", key, sortedOracleKeys(expected))
			}
			if !reflect.DeepEqual(got, expectedValue) {
				gotLayer := got.(map[string]any)
				expectedLayer := expectedValue.(map[string]any)
				gotBuckets := gotLayer["buckets"].([]any)
				expectedBuckets := expectedLayer["buckets"].([]any)
				for index := range gotBuckets {
					if !reflect.DeepEqual(gotBuckets[index], expectedBuckets[index]) {
						t.Fatalf("materializer mismatch %s bucket=%d at %s\nGo: %s\nPython: %s", key, index, firstJSONDifference(gotBuckets[index], expectedBuckets[index], "$"), oracleJSON(gotBuckets[index]), oracleJSON(expectedBuckets[index]))
					}
				}
				for field, gotValue := range gotLayer {
					if expectedValue, exists := expectedLayer[field]; !exists || !reflect.DeepEqual(gotValue, expectedValue) {
						t.Logf("materializer mismatch %s field=%s Go=%s Python=%s", key, field, oracleJSON(gotValue), oracleJSON(expectedLayer[field]))
					}
				}
				t.Fatalf("materializer mismatch %s metadata\nGo: %s\nPython: %s", key, gotRaw, oracleJSON(expectedValue))
			}
		}
	}
}

// TestMaterializerFoldOrderingMatchesPython exercises values inside the generated
// layer window. The broad matrix above intentionally uses a compact fixture, but
// its observations predate the current layer horizon; this boundary case keeps
// the fold arithmetic and equal-timestamp tie-break under the cross-language
// oracle instead of allowing an empty layer to pass.
func TestMaterializerFoldOrderingMatchesPython(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	fixturePath := filepath.Join(root, "fold-fixture.json")
	scriptPath := filepath.Join(root, "fold-oracle.py")
	fixture := map[string]any{
		"observations": []Observation{
			{EventID: "memory-z", Family: "system_memory", SourceID: "z-source", ObservedAt: 5, EpochID: "memory", OwnerGeneration: 1, Payload: map[string]any{"used_bytes": 20.0, "capacity_bytes": 100.0}},
			{EventID: "memory-a", Family: "system_memory", SourceID: "a-source", ObservedAt: 5, EpochID: "memory", OwnerGeneration: 1, Payload: map[string]any{"used_bytes": 10.0, "capacity_bytes": 100.0}},
			{EventID: "cpu-1", Family: "cpu", SourceID: "host", ObservedAt: 6, EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 1.749301391338315, "system_percent": 1.749301391338315}},
			{EventID: "cpu-2", Family: "cpu", SourceID: "host", ObservedAt: 7, EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 157370630598212.66, "system_percent": 157370630598212.66}},
			{EventID: "cpu-3", Family: "cpu", SourceID: "host", ObservedAt: 8, EpochID: "cpu", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 98145381.85953039, "system_percent": 98145381.85953039}},
		},
	}
	data, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(fixturePath, data, 0o600); err != nil {
		t.Fatal(err)
	}
	const oracle = `import json, sys
from yomux_lib.stats_current import materializer, storage

fixture = json.loads(open(sys.argv[1], encoding="utf-8").read())
observations = tuple(storage.Observation(
    item["EventID"], item["Family"], item["SourceID"], item["ObservedAt"],
    item["EpochID"], item["OwnerGeneration"], item["Payload"],
) for item in fixture["observations"])
snapshot = storage.StoreSnapshot(storage.SchemaMetadata(9, 25, 8, 1), observations, (), (), (), ())
generation = materializer.build_generation(snapshot, source_generation=1, cache_generation=1, generated_at=10, observed_until=10)
layer = materializer.slice_generation(generation, 300, 1)
memory_bucket = next(item for item in layer.buckets if item.start <= 5 < item.start + item.duration)
cpu_bucket = next(item for item in layer.buckets if item.start <= 7 < item.start + item.duration)
memory_values = {item.name: item.value for item in memory_bucket.series}
cpu_values = {item.name: item.value for item in cpu_bucket.series}
print(json.dumps({"memory": memory_values["system_memory_used_bytes"], "cpu": cpu_values["system_cpu_percent"]}, sort_keys=True, separators=(",", ":")))
`
	if err := os.WriteFile(scriptPath, []byte(oracle), 0o600); err != nil {
		t.Fatal(err)
	}
	var expected map[string]float64
	if err := json.Unmarshal([]byte(runPython(t, python, scriptPath, fixturePath)), &expected); err != nil {
		t.Fatal(err)
	}
	observations := fixture["observations"].([]Observation)
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: -290, End: 10, Resolution: 1}, observations, nil, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]float64{
		"memory": seriesValue(layer.Buckets[295], "system_memory_used_bytes"),
		"cpu":    seriesValue(layer.Buckets[297], "system_cpu_percent"),
	}
	if !reflect.DeepEqual(got, expected) {
		t.Fatalf("fold arithmetic mismatch\nGo: %#v\nPython: %#v", got, expected)
	}
}

func TestMemoryNoDataMatchesPythonOracle(t *testing.T) {
	python := pythonExecutable(t)
	root := t.TempDir()
	fixturePath := filepath.Join(root, "memory-no-data-fixture.json")
	scriptPath := filepath.Join(root, "memory-no-data-oracle.py")
	fixture := map[string]any{
		"observations": []Observation{{
			EventID: "memory-before-window", Family: "system_memory", SourceID: "host",
			ObservedAt: 60, EpochID: "memory", OwnerGeneration: 1,
			Payload: map[string]any{"used_bytes": 8.0, "capacity_bytes": 16.0},
		}},
		"coverage": []CoverageEpoch{{
			Family: "system_memory", SourceID: "host", EpochID: "memory",
			StartedAt: 0, EndedAt: floatPointer(120), NativeCadenceSeconds: 60, OwnerGeneration: 1,
		}},
		"ranges": []int{300, 900},
	}
	data, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(fixturePath, data, 0o600); err != nil {
		t.Fatal(err)
	}
	const oracle = `import dataclasses, json, sys
from yomux_lib.stats_current import materializer, storage

fixture = json.loads(open(sys.argv[1], encoding="utf-8").read())
observations = tuple(storage.Observation(
    item["EventID"], item["Family"], item["SourceID"], item["ObservedAt"],
    item["EpochID"], item["OwnerGeneration"], item["Payload"],
) for item in fixture["observations"])
coverage = tuple(storage.CoverageEpoch(
    item["Family"], item["SourceID"], item["EpochID"], item["StartedAt"],
    item.get("EndedAt"), item["NativeCadenceSeconds"], item["OwnerGeneration"],
) for item in fixture["coverage"])
result = {}
for range_seconds in fixture["ranges"]:
    window_end = 120 + range_seconds
    resolution = 10 if range_seconds == 300 else 60
    for name in ("coverage_gap", "source_unavailable"):
        unavailable = ()
        if name == "source_unavailable":
            unavailable = (storage.UnavailableSpan(
                "system_memory", "host", "memory-unavailable", 120, window_end,
                60, "source_unavailable", 1,
            ),)
        snapshot = storage.StoreSnapshot(storage.SchemaMetadata(9, 25, 8, 1), observations, coverage, (), (), unavailable)
        generation = materializer.build_generation(snapshot, source_generation=1, cache_generation=1, generated_at=window_end, observed_until=window_end - 1e-6)
        layer = materializer.slice_generation(generation, range_seconds, resolution)
        spans = [dataclasses.asdict(item) for item in layer.no_data]
        for item in spans:
            if abs(item["end"] - window_end) < 1e-5:
                item["end"] = window_end
        result[f"{range_seconds}/{name}"] = spans
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
`
	if err := os.WriteFile(scriptPath, []byte(oracle), 0o600); err != nil {
		t.Fatal(err)
	}
	var expected map[string]any
	if err := json.Unmarshal([]byte(runPython(t, python, scriptPath, fixturePath)), &expected); err != nil {
		t.Fatal(err)
	}
	observations := fixture["observations"].([]Observation)
	coverage := fixture["coverage"].([]CoverageEpoch)
	for _, rangeSeconds := range fixture["ranges"].([]int) {
		for _, name := range []string{"coverage_gap", "source_unavailable"} {
			name := name
			t.Run(fmt.Sprintf("%d/%s", rangeSeconds, name), func(t *testing.T) {
				windowEnd := float64(120 + rangeSeconds)
				resolution := 10.0
				if rangeSeconds == 900 {
					resolution = 60
				}
				var unavailable []UnavailableSpan
				if name == "source_unavailable" {
					unavailable = []UnavailableSpan{{
						Family: "system_memory", SourceID: "host", EpochID: "memory-unavailable",
						StartedAt: 120, EndedAt: windowEnd, NativeCadenceSeconds: 60,
						Reason: "source_unavailable", OwnerGeneration: 1,
					}}
				}
				layer, err := (Materializer{}).Build(MaterializeRequest{Start: 120, End: windowEnd, Resolution: resolution}, observations, coverage, unavailable, nil)
				if err != nil {
					t.Fatal(err)
				}
				wantReason := name
				if len(layer.NoData) != 1 {
					t.Fatalf("memory no-data spans = %+v, want exactly one span", layer.NoData)
				}
				span := layer.NoData[0]
				if span.Family != "system_memory" || span.SourceID != "host" || span.Start != 120 || span.End != windowEnd || span.NativeCadenceSeconds != 60 || span.Reason != wantReason {
					t.Fatalf("memory no-data span = %+v, want system_memory/host %v-%v cadence=60 reason=%s", span, 120, windowEnd, wantReason)
				}
				gotData := make([]map[string]any, len(layer.NoData))
				for index, item := range layer.NoData {
					gotData[index] = map[string]any{
						"family": item.Family, "source_id": item.SourceID, "epoch_id": item.EpochID,
						"reason": item.Reason, "start": item.Start, "end": item.End,
						"native_cadence_seconds": item.NativeCadenceSeconds,
					}
				}
				gotJSON, err := json.Marshal(gotData)
				if err != nil {
					t.Fatal(err)
				}
				var got any
				if err := json.Unmarshal(gotJSON, &got); err != nil {
					t.Fatal(err)
				}
				key := fmt.Sprintf("%d/%s", rangeSeconds, name)
				if !reflect.DeepEqual(got, expected[key]) {
					t.Fatalf("memory no-data differs from Python %s\nGo: %s\nPython: %s", key, gotJSON, oracleJSON(expected[key]))
				}
			})
		}
	}
}

func explicitResolutionsForRange(rangeSeconds int) []any {
	choices := map[int][]int{
		300: {1, 10}, 900: {10, 60}, 1800: {10, 60},
		3600: {60, 300}, 7200: {60, 300}, 14400: {60, 300},
		28800: {60, 300}, 57600: {300}, 86400: {300},
	}
	values := make([]any, 0, len(choices[rangeSeconds]))
	for _, resolution := range choices[rangeSeconds] {
		values = append(values, resolution)
	}
	return values
}

func requestedResolutionForRange(rangeSeconds int, requested any) int {
	if requestedInt, ok := requested.(int); ok {
		return requestedInt
	}
	switch rangeSeconds {
	case 300:
		return 1
	case 900:
		return 10
	case 1800:
		return 60
	default:
		return 300
	}
}

func canonicalMaterializedLayer(layer MaterializedLayer) map[string]any {
	buckets := make([]map[string]any, len(layer.Buckets))
	for index, bucket := range layer.Buckets {
		series := make([]map[string]any, len(bucket.Series))
		for seriesIndex, value := range bucket.Series {
			series[seriesIndex] = map[string]any{"name": value.Name, "value": value.Value, "source_count": value.SourceCount, "first_observed_at": value.FirstObservedAt, "last_observed_at": value.LastObservedAt}
		}
		buckets[index] = map[string]any{"start": bucket.Start, "duration": bucket.Duration, "series": series, "source_count": bucket.SourceCount, "first_observed_at": bucket.FirstObservedAt, "last_observed_at": bucket.LastObservedAt, "complete": bucket.Complete, "cost_detail": canonicalCostDetail(bucket.Cost)}
	}
	noData := make([]map[string]any, len(layer.NoData))
	for index, value := range layer.NoData {
		noData[index] = map[string]any{"family": value.Family, "source_id": value.SourceID, "epoch_id": value.EpochID, "reason": value.Reason, "start": value.Start, "end": value.End, "native_cadence_seconds": value.NativeCadenceSeconds}
	}
	return map[string]any{"resolution": layer.Resolution, "start": layer.Start, "end": layer.End, "buckets": buckets, "no_data": noData}
}

func canonicalCostDetail(value MaterializedCostDetail) map[string]any {
	dimensions := make([]map[string]any, len(value.Dimensions))
	for index, item := range value.Dimensions {
		dimensions[index] = map[string]any{"dimension": item.Dimension, "tokens": item.Tokens, "micro_usd": item.MicroUSD, "api_list_micro_usd": item.APIListMicroUSD}
	}
	return map[string]any{"dimensions": dimensions, "priced": map[string]any{"atoms": value.PricedAtoms, "tokens": value.PricedTokens}, "unpriced": map[string]any{"atoms": value.UnpricedAtoms, "tokens": value.UnpricedTokens}, "models": canonicalAttributions(value.Models), "agents": canonicalAttributions(value.Agents), "evidence": canonicalEvidence(value.Evidence), "omitted_models": value.OmittedModels, "omitted_agents": value.OmittedAgents, "omitted_evidence": value.OmittedEvidence}
}

func canonicalAttributions(values []MaterializedCostAttribution) []map[string]any {
	result := make([]map[string]any, len(values))
	for index, value := range values {
		result[index] = map[string]any{"key": value.Key, "provider": value.Provider, "model": value.Model, "source": value.Source, "label": value.Label, "dimensions": canonicalDimensions(value.Dimensions), "priced": map[string]any{"atoms": value.PricedAtoms, "tokens": value.PricedTokens}, "unpriced": map[string]any{"atoms": value.UnpricedAtoms, "tokens": value.UnpricedTokens}, "sources": canonicalSources(value.Sources)}
	}
	return result
}

func canonicalDimensions(values []MaterializedCostDimension) []map[string]any {
	result := make([]map[string]any, len(values))
	for index, value := range values {
		result[index] = map[string]any{"dimension": value.Dimension, "tokens": value.Tokens, "micro_usd": value.MicroUSD, "api_list_micro_usd": value.APIListMicroUSD}
	}
	return result
}

func canonicalSources(values []MaterializedCostSource) []map[string]any {
	result := make([]map[string]any, len(values))
	for index, value := range values {
		result[index] = map[string]any{"source": value.Source, "total_tokens": value.TotalTokens, "total_micro_usd": value.MicroUSD, "total_api_list_micro_usd": value.APIListMicroUSD, "dimensions": canonicalDimensions(value.Dimensions), "priced": map[string]any{"atoms": value.PricedAtoms, "tokens": value.PricedTokens}, "unpriced": map[string]any{"atoms": value.UnpricedAtoms, "tokens": value.UnpricedTokens}}
	}
	return result
}

func canonicalEvidence(values []PriceEvidence) []map[string]any {
	result := make([]map[string]any, len(values))
	for index, value := range values {
		result[index] = map[string]any{"key": value.Key, "provider": value.Provider, "model": value.Model, "dimension": value.Dimension, "direction": value.Direction, "modality": value.Modality, "cache_role": value.CacheRole, "unit": value.Unit, "pricing_profile": value.PricingProfile, "service_tier": value.ServiceTier, "catalog_model": value.CatalogModel, "rate_usd": value.RateUSD, "rate_scale": value.RateScale, "effective_from": value.EffectiveFrom, "source_kind": value.SourceKind, "source_url": value.SourceURL, "catalog_revision": value.CatalogRevision, "tokens": value.Tokens, "micro_usd": value.MicroUSD, "api_list_micro_usd": value.APIListMicroUSD, "priced_atoms": value.PricedAtoms}
	}
	return result
}

func oracleJSON(value any) string {
	data, _ := json.Marshal(value)
	return string(data)
}

func sortedOracleKeys(values map[string]any) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	return keys
}

func firstJSONDifference(got, want any, path string) string {
	if reflect.DeepEqual(got, want) {
		return ""
	}
	switch left := got.(type) {
	case map[string]any:
		right, ok := want.(map[string]any)
		if !ok {
			return path + " (type)"
		}
		keys := make([]string, 0, len(left)+len(right))
		seen := map[string]bool{}
		for key := range left {
			seen[key] = true
			keys = append(keys, key)
		}
		for key := range right {
			if !seen[key] {
				keys = append(keys, key)
			}
		}
		sort.Strings(keys)
		for _, key := range keys {
			if difference := firstJSONDifference(left[key], right[key], path+"."+key); difference != "" {
				return difference
			}
		}
	case []any:
		right, ok := want.([]any)
		if !ok || len(left) != len(right) {
			return fmt.Sprintf("%s (length/type)", path)
		}
		for index := range left {
			if difference := firstJSONDifference(left[index], right[index], fmt.Sprintf("%s[%d]", path, index)); difference != "" {
				return difference
			}
		}
	default:
		return fmt.Sprintf("%s got=%v want=%v", path, got, want)
	}
	return path
}
