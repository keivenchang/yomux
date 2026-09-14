package stats

import (
	"fmt"
	"reflect"
	"testing"
)

func TestMaterializerDeclaredFamiliesAndPricing(t *testing.T) {
	ended := 20.0
	layer, err := (Materializer{Price: func(UsageAtom) (int64, int64, *PriceEvidence, bool, error) {
		return 12, 20, &PriceEvidence{CatalogModel: "fixture"}, true, nil
	}}).Build(MaterializeRequest{Start: 0, End: 20, Resolution: 10}, []Observation{
		{EventID: "cpu-1", Family: "cpu", SourceID: "host", ObservedAt: 1, EpochID: "cpu", Payload: map[string]any{"process_percent": 20.0, "system_percent": 40.0}},
		{EventID: "browser-1", Family: "browser", SourceID: "client", ObservedAt: 2, EpochID: "browser", Payload: map[string]any{"kind": "api", "latency_ms": 8.0, "bytes": 100.0}},
	}, []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "cpu", StartedAt: 0, EndedAt: &ended, NativeCadenceSeconds: 1}}, nil, []UsageAtom{
		{EventID: "usage-1", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 3, Payload: map[string]any{"quantity": 10.0, "agent_id": "agent", "model": "model", "provider": "provider"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.Buckets) != 2 {
		t.Fatalf("buckets = %d", len(layer.Buckets))
	}
	if got := seriesValue(layer.Buckets[0], "process_cpu_percent"); got != 20 {
		t.Fatalf("cpu = %v bucket=%+v", got, layer.Buckets[0])
	}
	if got := seriesValue(layer.Buckets[0], "browser_api_per_second"); got != .1 {
		t.Fatalf("browser rate = %v", got)
	}
	if got := seriesValue(layer.Buckets[0], "model_tokens_per_minute:input:model"); got != 60 {
		t.Fatalf("token rate = %v", got)
	}
	if layer.Buckets[0].Cost.MicroUSD != 12 || len(layer.Buckets[0].Cost.Evidence) != 1 {
		t.Fatalf("cost = %+v", layer.Buckets[0].Cost)
	}
	if len(layer.Buckets[1].Series) != 0 {
		t.Fatalf("empty bucket has series: %+v", layer.Buckets[1])
	}
}

func TestMaterializerUnavailableIsExplicit(t *testing.T) {
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 20, Resolution: 10}, nil, nil, []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "gpu", StartedAt: 5, EndedAt: 15, NativeCadenceSeconds: 10, Reason: "no driver"}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.NoData) != 1 || layer.Buckets[0].Complete || layer.Buckets[1].Complete {
		t.Fatalf("unavailable = %+v buckets = %+v", layer.NoData, layer.Buckets)
	}
}

func TestMaterializerNormalizesOverlappingUnavailableSpans(t *testing.T) {
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 20, Resolution: 10}, nil, nil, []UnavailableSpan{
		{Family: "gpu", SourceID: "gpu:0", EpochID: "later", StartedAt: 10, EndedAt: 20, NativeCadenceSeconds: 10, Reason: "later reason"},
		{Family: "gpu", SourceID: "gpu:0", EpochID: "earlier", StartedAt: 9, EndedAt: 11, NativeCadenceSeconds: 10, Reason: "earlier reason"},
		{Family: "gpu", SourceID: "gpu:1", EpochID: "other", StartedAt: 5, EndedAt: 6, NativeCadenceSeconds: 10, Reason: "other source"},
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	got := make([]string, 0, len(layer.NoData))
	for _, span := range layer.NoData {
		got = append(got, fmt.Sprintf("%s/%s %.0f-%.0f %s", span.Family, span.SourceID, span.Start, span.End, span.Reason))
	}
	want := []string{
		"gpu/gpu:0 9-11 earlier reason",
		"gpu/gpu:0 11-20 later reason",
		"gpu/gpu:1 5-6 other source",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("normalized no-data = %v, want %v", got, want)
	}
}

func TestMaterializerEmitsSystemAndProcessMemorySeries(t *testing.T) {
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 100, End: 160, Resolution: 60}, []Observation{
		{EventID: "memory-1", Family: "system_memory", SourceID: "host", ObservedAt: 120, EpochID: "memory", Payload: map[string]any{
			"used_bytes": uint64(8), "capacity_bytes": uint64(16),
			"process_memory_bytes": map[string]any{"python": uint64(4), "node": uint64(2)},
		}},
	}, []CoverageEpoch{{Family: "system_memory", SourceID: "host", EpochID: "memory", StartedAt: 0, EndedAt: nil, NativeCadenceSeconds: 60}}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := seriesValue(layer.Buckets[0], "system_memory_used_bytes"); got != 8 {
		t.Fatalf("system memory = %v", got)
	}
	if got := seriesValue(layer.Buckets[0], "system_memory_capacity_bytes"); got != 16 {
		t.Fatalf("memory capacity = %v", got)
	}
	if got := seriesValue(layer.Buckets[0], "process_memory_bytes:python"); got != 4 {
		t.Fatalf("python memory = %v", got)
	}
	if len(layer.NoData) != 0 || !layer.Buckets[0].Complete {
		t.Fatalf("memory coverage = no_data=%+v bucket=%+v", layer.NoData, layer.Buckets[0])
	}
}

func seriesValue(bucket MaterializedBucket, name string) float64 {
	for _, series := range bucket.Series {
		if series.Name == name {
			return series.Value
		}
	}
	return -1
}
