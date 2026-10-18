package stats

import (
	"encoding/json"
	"fmt"

	"reflect"
	"sort"
)

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
