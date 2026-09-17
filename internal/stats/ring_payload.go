// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"sort"
)

// These DTOs are the persisted Python-compatible ring format. They are kept
// separate from materializer accumulators so private fields cannot leak into
// the restart contract.
type ringBucketPayload struct {
	Version         int                `json:"version"`
	GeneratedAt     float64            `json:"generated_at"`
	CacheGeneration int64              `json:"cache_generation"`
	Bucket          ringBucketWire     `json:"bucket"`
	NoData          []ringNoDataWire   `json:"no_data"`
	CostDetail      ringCostDetailWire `json:"cost_detail"`
}

// derivedCacheGeneration matches Python's durable-view fallback for a legacy
// row that predates the explicit payload envelope. The timestamp supplies a
// restart-stable monotonic base and the ring generation breaks same-millisecond
// ties.
func derivedCacheGeneration(generatedAt float64, ringGeneration int64) int64 {
	if !finiteNonNegative(generatedAt) || ringGeneration < 0 {
		return 0
	}
	scaled := generatedAt * 1000
	if scaled >= materializerMaxSafeInteger {
		return int64(materializerMaxSafeInteger)
	}
	base := int64(scaled)
	if ringGeneration > int64(materializerMaxSafeInteger)-base {
		return int64(materializerMaxSafeInteger)
	}
	return base + ringGeneration
}

// ringBucketPayloadMetadata reads only the publication metadata needed by the
// service cursor. Legacy development rows are reported as having no metadata so
// callers can apply the durable timestamp/ring fallback above.
func ringBucketPayloadMetadata(value string) (cacheGeneration int64, generatedAt float64, present bool, err error) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal([]byte(value), &raw); err != nil || raw == nil {
		return 0, 0, false, nil
	}
	if _, ok := raw["cache_generation"]; !ok {
		return 0, 0, false, nil
	}
	var version int
	var generated float64
	var cache int64
	if err := json.Unmarshal(raw["version"], &version); err != nil || version != RingPayloadVersion {
		return 0, 0, true, fmt.Errorf("invalid ring payload version")
	}
	if err := json.Unmarshal(raw["generated_at"], &generated); err != nil || !finiteNonNegative(generated) {
		return 0, 0, true, fmt.Errorf("invalid ring payload generated_at")
	}
	if err := json.Unmarshal(raw["cache_generation"], &cache); err != nil || cache < 0 {
		return 0, 0, true, fmt.Errorf("invalid ring payload cache_generation")
	}
	return cache, generated, true, nil
}

type ringBucketWire struct {
	Start    float64                   `json:"start"`
	Duration float64                   `json:"duration"`
	Series   map[string]ringSeriesWire `json:"series"`
	Source   ringSourceWire            `json:"source"`
	Open     bool                      `json:"open"`
}

type ringSeriesWire struct {
	Value          float64 `json:"value"`
	SourceCount    int     `json:"source_count"`
	FirstTimestamp float64 `json:"first_timestamp"`
	LastTimestamp  float64 `json:"last_timestamp"`
}

type ringSourceWire struct {
	FirstTimestamp *float64 `json:"first_timestamp"`
	LastTimestamp  *float64 `json:"last_timestamp"`
	Count          int      `json:"count"`
}

type ringNoDataWire struct {
	Family               string  `json:"family"`
	SourceID             string  `json:"source_id"`
	Start                float64 `json:"start"`
	End                  float64 `json:"end"`
	Epoch                string  `json:"epoch"`
	Reason               string  `json:"reason"`
	SourceCadenceSeconds float64 `json:"source_cadence_seconds"`
}

type ringCostDimensionWire struct {
	Dimension       string `json:"dimension"`
	Tokens          int64  `json:"tokens"`
	MicroUSD        int64  `json:"micro_usd"`
	APIListMicroUSD int64  `json:"api_list_micro_usd"`
}

type ringCostCoverageWire struct {
	Atoms  int   `json:"atoms"`
	Tokens int64 `json:"tokens"`
}

type ringCostSourceWire struct {
	Source            string                  `json:"source"`
	TotalTokens       int64                   `json:"total_tokens"`
	TotalMicroUSD     int64                   `json:"total_micro_usd"`
	TotalAPIListMicro int64                   `json:"total_api_list_micro_usd"`
	Dimensions        []ringCostDimensionWire `json:"dimensions"`
	Priced            ringCostCoverageWire    `json:"priced"`
	Unpriced          ringCostCoverageWire    `json:"unpriced"`
}

type ringCostAttributionWire struct {
	Key        string                  `json:"key"`
	Provider   string                  `json:"provider"`
	Model      string                  `json:"model"`
	Source     string                  `json:"source"`
	Label      string                  `json:"label"`
	Dimensions []ringCostDimensionWire `json:"dimensions"`
	Priced     ringCostCoverageWire    `json:"priced"`
	Unpriced   ringCostCoverageWire    `json:"unpriced"`
	Sources    []ringCostSourceWire    `json:"sources"`
}

type ringCostEvidenceWire struct {
	Key             string `json:"key"`
	Provider        string `json:"provider"`
	Model           string `json:"model"`
	Dimension       string `json:"dimension"`
	Direction       string `json:"direction"`
	Modality        string `json:"modality"`
	CacheRole       string `json:"cache_role"`
	Unit            string `json:"unit"`
	PricingProfile  string `json:"pricing_profile"`
	ServiceTier     string `json:"service_tier"`
	CatalogModel    string `json:"catalog_model"`
	RateUSD         string `json:"rate_usd"`
	RateScale       int64  `json:"rate_scale"`
	EffectiveFrom   string `json:"effective_from"`
	SourceKind      string `json:"source_kind"`
	SourceURL       string `json:"source_url"`
	CatalogRevision int64  `json:"catalog_revision"`
	Tokens          int64  `json:"tokens"`
	MicroUSD        int64  `json:"micro_usd"`
	APIListMicroUSD int64  `json:"api_list_micro_usd"`
	PricedAtoms     int64  `json:"priced_atoms"`
}

type ringCostDetailWire struct {
	Dimensions      []ringCostDimensionWire   `json:"dimensions"`
	Priced          ringCostCoverageWire      `json:"priced"`
	Unpriced        ringCostCoverageWire      `json:"unpriced"`
	Models          []ringCostAttributionWire `json:"models"`
	Agents          []ringCostAttributionWire `json:"agents"`
	Evidence        []ringCostEvidenceWire    `json:"evidence"`
	OmittedModels   int                       `json:"omitted_models"`
	OmittedAgents   int                       `json:"omitted_agents"`
	OmittedEvidence int                       `json:"omitted_evidence"`
}

func encodeRingBucketPayload(bucket MaterializedBucket, noData []MaterializedNoData, generation int64, generatedAt float64) (string, error) {
	if generation < 0 || !finiteNonNegative(generatedAt) {
		return "", fmt.Errorf("invalid ring bucket metadata")
	}
	payload := ringBucketPayload{
		Version: RingPayloadVersion, GeneratedAt: generatedAt, CacheGeneration: generation,
		Bucket: ringBucketToWire(bucket), NoData: noDataToWire(noData), CostDetail: costDetailToWire(bucket.Cost),
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("encode ring bucket payload: %w", err)
	}
	return string(encoded), nil
}

func decodeRingBucketPayload(value string) (MaterializedBucket, []MaterializedNoData, error) {
	var envelope ringBucketPayload
	decoder := json.NewDecoder(bytes.NewReader([]byte(value)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&envelope); err == nil && envelope.Bucket.Duration > 0 {
		if envelope.Version != RingPayloadVersion || envelope.CacheGeneration < 0 || !finiteNonNegative(envelope.GeneratedAt) {
			return MaterializedBucket{}, nil, fmt.Errorf("invalid ring bucket payload metadata")
		}
		bucket, err := ringBucketFromWire(envelope.Bucket)
		if err != nil {
			return MaterializedBucket{}, nil, err
		}
		cost, err := costDetailFromWire(envelope.CostDetail)
		if err != nil {
			return MaterializedBucket{}, nil, err
		}
		bucket.Cost = cost
		return bucket, noDataFromWire(envelope.NoData), nil
	}
	// Accept the pre-envelope development payload while old databases are
	// drained. New publications always use the explicit envelope above.
	var bucket MaterializedBucket
	if err := json.Unmarshal([]byte(value), &bucket); err != nil {
		return MaterializedBucket{}, nil, err
	}
	return bucket, nil, nil
}

func ringBucketToWire(bucket MaterializedBucket) ringBucketWire {
	series := make(map[string]ringSeriesWire, len(bucket.Series))
	for _, item := range bucket.Series {
		series[item.Name] = ringSeriesWire{Value: item.Value, SourceCount: item.SourceCount, FirstTimestamp: item.FirstObservedAt, LastTimestamp: item.LastObservedAt}
	}
	return ringBucketWire{Start: bucket.Start, Duration: bucket.Duration, Series: series, Source: ringSourceWire{FirstTimestamp: bucket.FirstObservedAt, LastTimestamp: bucket.LastObservedAt, Count: bucket.SourceCount}, Open: !bucket.Complete}
}

func ringBucketFromWire(wire ringBucketWire) (MaterializedBucket, error) {
	if !finiteNonNegative(wire.Start) || !finiteNonNegative(wire.Duration) || wire.Duration <= 0 || wire.Source.Count < 0 {
		return MaterializedBucket{}, fmt.Errorf("invalid ring bucket bounds")
	}
	series := make([]MaterializedSeries, 0, len(wire.Series))
	for name, item := range wire.Series {
		if name == "" || item.SourceCount < 0 || !finiteNumber(item.Value) || !finiteNonNegative(item.FirstTimestamp) || !finiteNonNegative(item.LastTimestamp) {
			return MaterializedBucket{}, fmt.Errorf("invalid ring series %q", name)
		}
		series = append(series, MaterializedSeries{Name: name, Value: item.Value, SourceCount: item.SourceCount, FirstObservedAt: item.FirstTimestamp, LastObservedAt: item.LastTimestamp})
	}
	sort.Slice(series, func(i, j int) bool { return series[i].Name < series[j].Name })
	return MaterializedBucket{Start: wire.Start, Duration: wire.Duration, Series: series, SourceCount: wire.Source.Count, FirstObservedAt: wire.Source.FirstTimestamp, LastObservedAt: wire.Source.LastTimestamp, Complete: !wire.Open}, nil
}

func noDataToWire(items []MaterializedNoData) []ringNoDataWire {
	result := make([]ringNoDataWire, 0, len(items))
	for _, item := range items {
		result = append(result, ringNoDataWire{Family: item.Family, SourceID: item.SourceID, Start: item.Start, End: item.End, Epoch: item.EpochID, Reason: item.Reason, SourceCadenceSeconds: item.NativeCadenceSeconds})
	}
	return result
}

func noDataFromWire(items []ringNoDataWire) []MaterializedNoData {
	result := make([]MaterializedNoData, 0, len(items))
	for _, item := range items {
		result = append(result, MaterializedNoData{Family: item.Family, SourceID: item.SourceID, Start: item.Start, End: item.End, EpochID: item.Epoch, Reason: item.Reason, NativeCadenceSeconds: item.SourceCadenceSeconds})
	}
	return result
}

func dimensionsToWire(items []MaterializedCostDimension) []ringCostDimensionWire {
	result := make([]ringCostDimensionWire, 0, len(items))
	for _, item := range items {
		result = append(result, ringCostDimensionWire{Dimension: item.Dimension, Tokens: item.Tokens, MicroUSD: item.MicroUSD, APIListMicroUSD: item.APIListMicroUSD})
	}
	return result
}

func dimensionsFromWire(items []ringCostDimensionWire) []MaterializedCostDimension {
	result := make([]MaterializedCostDimension, 0, len(items))
	for _, item := range items {
		result = append(result, MaterializedCostDimension{Dimension: item.Dimension, Tokens: item.Tokens, MicroUSD: item.MicroUSD, APIListMicroUSD: item.APIListMicroUSD})
	}
	return result
}

func coverageToWire(atoms int, tokens int64) ringCostCoverageWire {
	return ringCostCoverageWire{Atoms: atoms, Tokens: tokens}
}

func sourceToWire(item MaterializedCostSource) ringCostSourceWire {
	return ringCostSourceWire{Source: item.Source, TotalTokens: item.TotalTokens, TotalMicroUSD: item.MicroUSD, TotalAPIListMicro: item.APIListMicroUSD, Dimensions: dimensionsToWire(item.Dimensions), Priced: coverageToWire(item.PricedAtoms, item.PricedTokens), Unpriced: coverageToWire(item.UnpricedAtoms, item.UnpricedTokens)}
}

func sourceFromWire(item ringCostSourceWire) MaterializedCostSource {
	return MaterializedCostSource{Source: item.Source, TotalTokens: item.TotalTokens, MicroUSD: item.TotalMicroUSD, APIListMicroUSD: item.TotalAPIListMicro, Dimensions: dimensionsFromWire(item.Dimensions), PricedAtoms: item.Priced.Atoms, PricedTokens: item.Priced.Tokens, UnpricedAtoms: item.Unpriced.Atoms, UnpricedTokens: item.Unpriced.Tokens}
}

func attributionToWire(item MaterializedCostAttribution) ringCostAttributionWire {
	sources := make([]ringCostSourceWire, 0, len(item.Sources))
	for _, source := range item.Sources {
		sources = append(sources, sourceToWire(source))
	}
	return ringCostAttributionWire{Key: item.Key, Provider: item.Provider, Model: item.Model, Source: item.Source, Label: item.Label, Dimensions: dimensionsToWire(item.Dimensions), Priced: coverageToWire(item.PricedAtoms, item.PricedTokens), Unpriced: coverageToWire(item.UnpricedAtoms, item.UnpricedTokens), Sources: sources}
}

func attributionFromWire(item ringCostAttributionWire) MaterializedCostAttribution {
	result := MaterializedCostAttribution{Key: item.Key, Provider: item.Provider, Model: item.Model, Source: item.Source, Label: item.Label, Dimensions: dimensionsFromWire(item.Dimensions), PricedAtoms: item.Priced.Atoms, PricedTokens: item.Priced.Tokens, UnpricedAtoms: item.Unpriced.Atoms, UnpricedTokens: item.Unpriced.Tokens}
	// Python's persisted attribution shape derives totals from dimensions and
	// therefore does not duplicate total_tokens/price totals on the row. Rebuild
	// those aggregates while decoding so the public cost validator sees the same
	// invariant after a ring restart.
	for _, dimension := range result.Dimensions {
		result.TotalTokens += dimension.Tokens
		result.MicroUSD += dimension.MicroUSD
		result.APIListMicroUSD += dimension.APIListMicroUSD
	}
	for _, source := range item.Sources {
		result.Sources = append(result.Sources, sourceFromWire(source))
	}
	return result
}

func costDetailToWire(item MaterializedCostDetail) ringCostDetailWire {
	models := make([]ringCostAttributionWire, 0, len(item.Models))
	for _, model := range item.Models {
		models = append(models, attributionToWire(model))
	}
	agents := make([]ringCostAttributionWire, 0, len(item.Agents))
	for _, agent := range item.Agents {
		agents = append(agents, attributionToWire(agent))
	}
	evidence := make([]ringCostEvidenceWire, 0, len(item.Evidence))
	for _, value := range item.Evidence {
		evidence = append(evidence, ringCostEvidenceWire{Key: value.Key, Provider: value.Provider, Model: value.Model, Dimension: value.Dimension, Direction: value.Direction, Modality: value.Modality, CacheRole: value.CacheRole, Unit: value.Unit, PricingProfile: value.PricingProfile, ServiceTier: value.ServiceTier, CatalogModel: value.CatalogModel, RateUSD: value.RateUSD, RateScale: value.RateScale, EffectiveFrom: value.EffectiveFrom, SourceKind: value.SourceKind, SourceURL: value.SourceURL, CatalogRevision: value.CatalogRevision, Tokens: value.Tokens, MicroUSD: value.MicroUSD, APIListMicroUSD: value.APIListMicroUSD, PricedAtoms: value.PricedAtoms})
	}
	return ringCostDetailWire{Dimensions: dimensionsToWire(item.Dimensions), Priced: coverageToWire(item.PricedAtoms, item.PricedTokens), Unpriced: coverageToWire(item.UnpricedAtoms, item.UnpricedTokens), Models: models, Agents: agents, Evidence: evidence, OmittedModels: item.OmittedModels, OmittedAgents: item.OmittedAgents, OmittedEvidence: item.OmittedEvidence}
}

func costDetailFromWire(item ringCostDetailWire) (MaterializedCostDetail, error) {
	dimensions := dimensionsFromWire(item.Dimensions)
	result := MaterializedCostDetail{Dimensions: dimensions, PricedAtoms: item.Priced.Atoms, PricedTokens: item.Priced.Tokens, UnpricedAtoms: item.Unpriced.Atoms, UnpricedTokens: item.Unpriced.Tokens, OmittedModels: item.OmittedModels, OmittedAgents: item.OmittedAgents, OmittedEvidence: item.OmittedEvidence}
	// Python's persisted cost_detail intentionally stores totals through its
	// dimension rows rather than duplicating them at the top level. Rebuild the
	// aggregate fields while decoding so the request-facing cost report retains
	// those totals without changing the Python wire shape.
	for _, dimension := range dimensions {
		result.Tokens += dimension.Tokens
		result.MicroUSD += dimension.MicroUSD
		result.APIListMicroUSD += dimension.APIListMicroUSD
	}
	for _, model := range item.Models {
		result.Models = append(result.Models, attributionFromWire(model))
	}
	for _, agent := range item.Agents {
		result.Agents = append(result.Agents, attributionFromWire(agent))
	}
	for _, value := range item.Evidence {
		result.Evidence = append(result.Evidence, PriceEvidence{Key: value.Key, Provider: value.Provider, Model: value.Model, Dimension: value.Dimension, Direction: value.Direction, Modality: value.Modality, CacheRole: value.CacheRole, Unit: value.Unit, PricingProfile: value.PricingProfile, ServiceTier: value.ServiceTier, CatalogModel: value.CatalogModel, RateUSD: value.RateUSD, RateScale: value.RateScale, EffectiveFrom: value.EffectiveFrom, SourceKind: value.SourceKind, SourceURL: value.SourceURL, CatalogRevision: value.CatalogRevision, Tokens: value.Tokens, MicroUSD: value.MicroUSD, APIListMicroUSD: value.APIListMicroUSD, PricedAtoms: value.PricedAtoms})
	}
	return result, nil
}

func finiteNumber(value float64) bool      { return !math.IsNaN(value) && !math.IsInf(value, 0) }
func finiteNonNegative(value float64) bool { return finiteNumber(value) && value >= 0 }

func ringNoDataForBucket(layer MaterializedLayer, bucket MaterializedBucket) []MaterializedNoData {
	result := make([]MaterializedNoData, 0)
	for _, item := range layer.NoData {
		start := math.Max(item.Start, bucket.Start)
		end := math.Min(item.End, bucket.Start+bucket.Duration)
		if start >= end {
			continue
		}
		item.Start, item.End = start, end
		result = append(result, item)
	}
	return result
}
