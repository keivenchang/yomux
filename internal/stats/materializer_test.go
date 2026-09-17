package stats

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
)

func TestMaterializerDeclaredFamiliesAndPricing(t *testing.T) {
	ended := 20.0
	layer, err := (Materializer{Price: func(UsageAtom) (int64, int64, *PriceEvidence, bool, error) {
		return 12, 20, &PriceEvidence{CatalogModel: "fixture"}, true, nil
	}}).Build(MaterializeRequest{Start: 0, End: 20, Resolution: 10}, []Observation{
		{EventID: "cpu-1", Family: "cpu", SourceID: "host", ObservedAt: 1, EpochID: "cpu", Payload: map[string]any{"process_percent": 20.0, "system_percent": 40.0, "process_cpu_percent": map[string]any{"python": 4.0}}},
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
	if layer.Buckets[0].SourceCount != 3 {
		t.Fatalf("bucket source count = %d, want one count per input fact", layer.Buckets[0].SourceCount)
	}
	if got := seriesValue(layer.Buckets[0], "cpu_percent:host"); got != 20 {
		t.Fatalf("cpu = %v bucket=%+v", got, layer.Buckets[0])
	}
	if got := seriesValue(layer.Buckets[0], "process_cpu_percent:python"); got != 4 {
		t.Fatalf("per-binary cpu = %v bucket=%+v", got, layer.Buckets[0])
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
	if len(layer.Buckets[0].Cost.Dimensions) != len(costReportDimensions) || layer.Buckets[0].Cost.Dimensions[0].Dimension != "input" || layer.Buckets[0].Cost.Dimensions[0].Tokens != 10 || layer.Buckets[0].Cost.Dimensions[0].MicroUSD != 12 {
		t.Fatalf("cost dimensions = %+v", layer.Buckets[0].Cost.Dimensions)
	}
	if len(layer.Buckets[0].Cost.Models) != 1 || layer.Buckets[0].Cost.Models[0].Provider != "provider" || layer.Buckets[0].Cost.Models[0].TotalTokens != 10 || len(layer.Buckets[0].Cost.Agents) != 1 {
		t.Fatalf("cost attribution = models=%+v agents=%+v", layer.Buckets[0].Cost.Models, layer.Buckets[0].Cost.Agents)
	}
	if len(layer.Buckets[1].Series) != 0 {
		t.Fatalf("empty bucket has series: %+v", layer.Buckets[1])
	}
}

func TestMaterializerUnpricedAttributionDimensionsMatchTotals(t *testing.T) {
	layer, err := (Materializer{Price: func(UsageAtom) (int64, int64, *PriceEvidence, bool, error) {
		return 0, 0, nil, false, nil
	}}).Build(MaterializeRequest{Start: 0, End: 10, Resolution: 10}, nil, nil, nil, []UsageAtom{{
		EventID: "unpriced-usage", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 1,
		Payload: map[string]any{"quantity": 17.0, "agent_id": "agent", "model": "model", "provider": "provider", "execution_source": "codex"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.Buckets) != 1 || len(layer.Buckets[0].Cost.Agents) != 1 {
		t.Fatalf("unpriced cost attribution = %+v", layer.Buckets)
	}
	agent := layer.Buckets[0].Cost.Agents[0]
	if agent.TotalTokens != 17 || agent.UnpricedTokens != 17 || len(agent.Dimensions) != len(costReportDimensions) || agent.Dimensions[4].Dimension != "output" || agent.Dimensions[4].Tokens != 17 {
		t.Fatalf("unpriced agent dimensions = %+v", agent)
	}
	if len(agent.Sources) != 1 || agent.Sources[0].TotalTokens != 17 || len(agent.Sources[0].Dimensions) != len(costReportDimensions) || agent.Sources[0].Dimensions[4].Dimension != "output" || agent.Sources[0].Dimensions[4].Tokens != 17 {
		t.Fatalf("unpriced agent sources = %+v", agent.Sources)
	}
}

func TestMaterializerSourcewiseSeriesCountDistinctSources(t *testing.T) {
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 10, Resolution: 10}, []Observation{
		{EventID: "browser-a-1", Family: "browser", SourceID: "browser:a", ObservedAt: 1, EpochID: "a", Payload: map[string]any{"kind": "api", "latency_ms": 10.0}},
		{EventID: "browser-a-2", Family: "browser", SourceID: "browser:a", ObservedAt: 2, EpochID: "a", Payload: map[string]any{"kind": "api", "latency_ms": 20.0}},
		{EventID: "browser-b-1", Family: "browser", SourceID: "browser:b", ObservedAt: 3, EpochID: "b", Payload: map[string]any{"kind": "api", "latency_ms": 30.0}},
	}, nil, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	found := map[string]bool{}
	for _, series := range layer.Buckets[0].Series {
		if series.Name == "browser_latency_ms" || series.Name == "browser_api_per_second" {
			found[series.Name] = true
			if series.SourceCount != 2 {
				t.Fatalf("%s source count = %d, want distinct source count 2", series.Name, series.SourceCount)
			}
		}
	}
	if !found["browser_latency_ms"] || !found["browser_api_per_second"] {
		t.Fatalf("sourcewise series missing: %+v", layer.Buckets[0].Series)
	}
}

func TestMaterializerNilPriceResolverPreservesUnpricedCoverage(t *testing.T) {
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 10, Resolution: 10}, nil, nil, nil, []UsageAtom{{
		EventID: "unpriced-nil-resolver", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 1,
		Payload: map[string]any{"quantity": 17.0, "agent_id": "agent", "model": "model", "provider": "provider"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	cost := layer.Buckets[0].Cost
	if cost.UnpricedAtoms != 1 || cost.UnpricedTokens != 17 || cost.PricedAtoms != 0 || cost.PricedTokens != 0 {
		t.Fatalf("nil resolver coverage = %+v, want one unpriced atom", cost)
	}
	if len(cost.Agents) != 1 || cost.Agents[0].UnpricedTokens != 17 {
		t.Fatalf("nil resolver agent attribution = %+v", cost.Agents)
	}
}

func TestMaterializerDeduplicatesUsageAtomsBeforeProjection(t *testing.T) {
	atom := UsageAtom{
		EventID: "duplicate-usage", Direction: "input", Modality: "text",
		CacheRole: "none", Unit: "tokens", ObservedAt: 1,
		Payload: map[string]any{
			"quantity": 10.0, "agent_id": "agent", "model": "model", "provider": "provider",
		},
	}
	layer, err := (Materializer{Price: func(UsageAtom) (int64, int64, *PriceEvidence, bool, error) {
		return 7, 11, nil, true, nil
	}}).Build(MaterializeRequest{Start: 0, End: 10, Resolution: 10}, nil, nil, nil, []UsageAtom{atom, atom})
	if err != nil {
		t.Fatal(err)
	}
	if got := seriesValue(layer.Buckets[0], "usage_tokens"); got != 10 {
		t.Fatalf("duplicate usage tokens = %v, want 10", got)
	}
	if got := seriesValue(layer.Buckets[0], "cost_micro_usd"); got != 7 {
		t.Fatalf("duplicate usage cost = %v, want 7", got)
	}
	if layer.Buckets[0].SourceCount != 1 || layer.Buckets[0].Cost.PricedAtoms != 1 {
		t.Fatalf("duplicate usage provenance = source_count=%d cost=%+v, want one fact", layer.Buckets[0].SourceCount, layer.Buckets[0].Cost)
	}
}

func TestMaterializerCostAgentLabelDoesNotExposePrivateIdentity(t *testing.T) {
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 10, Resolution: 10}, nil, nil, nil, []UsageAtom{{
		EventID: "private-agent", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 1,
		Payload: map[string]any{
			"quantity": 7.0, "agent_id": "/Users/private/transcript", "model": "model", "provider": "provider",
			"execution_source": "codex",
		},
	}})
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.Buckets[0].Cost.Agents) != 1 {
		t.Fatalf("private agent attribution = %+v", layer.Buckets[0].Cost.Agents)
	}
	agent := layer.Buckets[0].Cost.Agents[0]
	if agent.Label == "/Users/private/transcript" || agent.Source != "codex" || !strings.HasPrefix(agent.Label, "codex:") {
		t.Fatalf("private agent label = %+v", agent)
	}
}

func TestMaterializerBucketCountsOnlyMetricFactsAndTracksBounds(t *testing.T) {
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 20, Resolution: 10}, []Observation{
		{EventID: "browser-failure", Family: "browser", SourceID: "browser:a", ObservedAt: 1, EpochID: "a", Payload: map[string]any{"kind": "error", "message": "boom"}},
		{EventID: "browser-api", Family: "browser", SourceID: "browser:a", ObservedAt: 2, EpochID: "a", Payload: map[string]any{"kind": "api", "latency_ms": 10.0}},
	}, nil, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := layer.Buckets[0]; got.SourceCount != 1 || got.FirstObservedAt == nil || *got.FirstObservedAt != 2 || got.LastObservedAt == nil || *got.LastObservedAt != 2 {
		t.Fatalf("metric bucket metadata = %+v, want one fact at t=2", got)
	}
	if got := layer.Buckets[1]; got.SourceCount != 0 || got.FirstObservedAt != nil || got.LastObservedAt != nil || len(got.Series) != 0 {
		t.Fatalf("diagnostic-only bucket metadata = %+v, want empty", got)
	}
}

func TestMaterializerCostAttributionRoundTripPreservesSourceDimensions(t *testing.T) {
	makeAtom := func(index int, direction string, quantity float64) UsageAtom {
		return UsageAtom{
			EventID: fmt.Sprintf("roundtrip-%s-%d", direction, index), Direction: direction,
			Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: float64(index),
			Payload: map[string]any{"quantity": quantity, "agent_id": "agent", "model": "model", "provider": "provider"},
		}
	}
	atoms := []UsageAtom{
		makeAtom(0, "input", 600), makeAtom(1, "input", 601), makeAtom(2, "input", 602),
		makeAtom(3, "output", 300), makeAtom(4, "output", 301), makeAtom(5, "output", 302),
	}
	layer, err := (Materializer{Price: func(UsageAtom) (int64, int64, *PriceEvidence, bool, error) {
		return 0, 0, nil, false, nil
	}}).Build(MaterializeRequest{Start: 0, End: 10, Resolution: 10}, nil, nil, nil, atoms)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := encodeRingBucketPayload(layer.Buckets[0], nil, 1, 1)
	if err != nil {
		t.Fatal(err)
	}
	decoded, _, err := decodeRingBucketPayload(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if len(decoded.Cost.Agents) != 1 || len(decoded.Cost.Agents[0].Sources) != 1 {
		t.Fatalf("round-trip attribution = %+v", decoded.Cost.Agents)
	}
	source := decoded.Cost.Agents[0].Sources[0]
	var dimensionTokens int64
	for _, dimension := range source.Dimensions {
		dimensionTokens += dimension.Tokens
	}
	if source.TotalTokens != 2706 || dimensionTokens != source.TotalTokens {
		t.Fatalf("source totals disagree after round-trip: %+v", source)
	}
}

func TestMaterializerUnavailableIsExplicit(t *testing.T) {
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 20, Resolution: 10}, nil, nil, []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "gpu", StartedAt: 5, EndedAt: 15, NativeCadenceSeconds: 10, Reason: "no driver"}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.NoData) != 1 || !layer.Buckets[0].Complete || !layer.Buckets[1].Complete {
		t.Fatalf("unavailable = %+v buckets = %+v", layer.NoData, layer.Buckets)
	}
}

func TestMaterializerCoverageEpochGapIsExplicit(t *testing.T) {
	firstEnd, secondEnd := 10.0, 30.0
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 30, Resolution: 10}, nil, []CoverageEpoch{
		{Family: "cpu", SourceID: "host", EpochID: "cpu-1", StartedAt: 0, EndedAt: &firstEnd, NativeCadenceSeconds: 1},
		{Family: "cpu", SourceID: "host", EpochID: "cpu-2", StartedAt: 20, EndedAt: &secondEnd, NativeCadenceSeconds: 1},
	}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.NoData) != 1 {
		t.Fatalf("coverage no-data = %+v, want one gap", layer.NoData)
	}
	gap := layer.NoData[0]
	if gap.Family != "cpu" || gap.SourceID != "host" || gap.EpochID != "cpu-1" || gap.Reason != "coverage_gap" || gap.Start != 10 || gap.End != 20 || gap.NativeCadenceSeconds != 1 {
		t.Fatalf("coverage gap = %+v", gap)
	}
}

func TestMaterializerCoverageGapsSubtractUnavailableAndPublishClosedTails(t *testing.T) {
	firstEnd, secondEnd := 10.0, 30.0
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 0, End: 40, Resolution: 10}, nil, []CoverageEpoch{
		{Family: "cpu", SourceID: "host", EpochID: "e1", StartedAt: 0, EndedAt: &firstEnd, NativeCadenceSeconds: 1},
		{Family: "cpu", SourceID: "host", EpochID: "e2", StartedAt: 20, EndedAt: &secondEnd, NativeCadenceSeconds: 1},
		{Family: "cpu", SourceID: "host2", EpochID: "other", StartedAt: 0, EndedAt: &secondEnd, NativeCadenceSeconds: 1},
	}, []UnavailableSpan{{Family: "cpu", SourceID: "host", EpochID: "gap", StartedAt: 12, EndedAt: 18, NativeCadenceSeconds: 1, Reason: "offline"}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	got := make([]string, 0, len(layer.NoData))
	for _, gap := range layer.NoData {
		got = append(got, fmt.Sprintf("%s/%s/%s/%s %.0f-%.0f", gap.Family, gap.SourceID, gap.EpochID, gap.Reason, gap.Start, gap.End))
	}
	want := []string{
		"cpu/host/e1/coverage_gap 10-12",
		"cpu/host/gap/offline 12-18",
		"cpu/host/e1/coverage_gap 18-20",
		"cpu/host/e2/coverage_gap 30-40",
		"cpu/host2/other/coverage_gap 30-40",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("coverage gaps = %v, want %v", got, want)
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
