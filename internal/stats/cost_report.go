// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"encoding/json"
	"sort"
)

const (
	MaxCostReportModels   = 16
	MaxCostReportAgents   = 16
	MaxCostReportEvidence = 32
)

// CostReportProjection is the bounded, window-wide detail used by both the
// HTTP encoder and snapshot admission. It never mutates its source buckets.
type CostReportProjection struct {
	Models, Agents                                []MaterializedCostAttribution
	Evidence                                      []PriceEvidence
	PricedTokens, UnpricedTokens                  int64
	OmittedModels, OmittedAgents, OmittedEvidence int
}

// ProjectCostReport selects deterministic top rows after merging identities
// across the whole window. Totals remain the responsibility of the aggregate
// fields in each immutable bucket; this projection only bounds detail rows.
func ProjectCostReport(buckets []MaterializedBucket) CostReportProjection {
	models := make(map[string]MaterializedCostAttribution)
	agents := make(map[string]MaterializedCostAttribution)
	evidence := make(map[string]PriceEvidence)
	projection := CostReportProjection{}
	for _, bucket := range buckets {
		for _, value := range bucket.Cost.Models {
			mergeCostAttribution(models, value)
		}
		for _, value := range bucket.Cost.Agents {
			mergeCostAttribution(agents, value)
		}
		for _, value := range bucket.Cost.Evidence {
			mergePriceEvidence(evidence, value)
		}
		projection.OmittedModels += bucket.Cost.OmittedModels
		projection.OmittedAgents += bucket.Cost.OmittedAgents
		projection.OmittedEvidence += bucket.Cost.OmittedEvidence
	}
	projection.Models = sortedCostAttributions(models)
	projection.Agents = sortedCostAttributions(agents)
	for _, value := range projection.Models {
		projection.PricedTokens += value.PricedTokens
		projection.UnpricedTokens += value.UnpricedTokens
	}
	projection.Evidence = make([]PriceEvidence, 0, len(evidence))
	for _, value := range evidence {
		projection.Evidence = append(projection.Evidence, value)
	}
	sort.Slice(projection.Evidence, func(i, j int) bool {
		left, right := projection.Evidence[i], projection.Evidence[j]
		if left.Tokens != right.Tokens {
			return left.Tokens > right.Tokens
		}
		if left.APIListMicroUSD != right.APIListMicroUSD {
			return left.APIListMicroUSD > right.APIListMicroUSD
		}
		if left.MicroUSD != right.MicroUSD {
			return left.MicroUSD > right.MicroUSD
		}
		return left.Key < right.Key
	})
	projection.OmittedModels += max(0, len(projection.Models)-MaxCostReportModels)
	projection.OmittedAgents += max(0, len(projection.Agents)-MaxCostReportAgents)
	projection.OmittedEvidence += max(0, len(projection.Evidence)-MaxCostReportEvidence)
	projection.Models = projection.Models[:min(len(projection.Models), MaxCostReportModels)]
	projection.Agents = projection.Agents[:min(len(projection.Agents), MaxCostReportAgents)]
	projection.Evidence = projection.Evidence[:min(len(projection.Evidence), MaxCostReportEvidence)]
	return projection
}

// CostReportDetailBytes encodes the exact report returned by the HTTP path for
// service transport admission.
func CostReportDetailBytes(buckets []MaterializedBucket) (int, error) {
	encoded, err := json.Marshal(CostReportWire(buckets))
	if err != nil {
		return 0, err
	}
	return len(encoded), nil
}

// CostReportWire builds the exact bounded report returned by the snapshot API.
// Keep this beside service admission so both paths encode the same projection.
func CostReportWire(buckets []MaterializedBucket) map[string]any {
	var totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced int64
	dimensionTotals := map[string]map[string]int64{}
	for _, bucket := range buckets {
		cost := bucket.Cost
		totalTokens += cost.Tokens
		totalMicroUSD += cost.MicroUSD
		totalAPIListMicroUSD += cost.APIListMicroUSD
		priced += int64(cost.PricedAtoms)
		unpriced += int64(cost.UnpricedAtoms)
		for _, dimension := range cost.Dimensions {
			totals := dimensionTotals[dimension.Dimension]
			if totals == nil {
				totals = map[string]int64{"tokens": 0, "micro_usd": 0, "api_list_micro_usd": 0}
				dimensionTotals[dimension.Dimension] = totals
			}
			totals["tokens"] += dimension.Tokens
			totals["micro_usd"] += dimension.MicroUSD
			totals["api_list_micro_usd"] += dimension.APIListMicroUSD
		}
	}
	projection := ProjectCostReport(buckets)
	models := make(map[string]MaterializedCostAttribution, len(projection.Models))
	for _, value := range projection.Models {
		models[value.Key] = value
	}
	agents := make(map[string]MaterializedCostAttribution, len(projection.Agents))
	for _, value := range projection.Agents {
		agents[value.Key] = value
	}
	evidence := make(map[string]PriceEvidence, len(projection.Evidence))
	for _, value := range projection.Evidence {
		evidence[value.Key] = value
	}
	return costReportWireWithEvidence(totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced, projection.PricedTokens, projection.UnpricedTokens, dimensionTotals, models, agents, evidence,
		int64(projection.OmittedModels), int64(projection.OmittedAgents), int64(projection.OmittedEvidence))
}

func costReportWireWithEvidence(totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced, pricedTokens, unpricedTokens int64, dimensionTotals map[string]map[string]int64, modelTotals, agentTotals map[string]MaterializedCostAttribution, evidenceTotals map[string]PriceEvidence, omittedModels, omittedAgents, omittedEvidence int64) map[string]any {
	dimensions := map[string]any{}
	for _, name := range costReportDimensions {
		dimensions[name] = map[string]any{"tokens": int64(0), "micro_usd": int64(0), "api_list_micro_usd": int64(0)}
	}
	for name, totals := range dimensionTotals {
		if _, known := dimensions[name]; known {
			dimensions[name] = map[string]any{"tokens": totals["tokens"], "micro_usd": totals["micro_usd"], "api_list_micro_usd": totals["api_list_micro_usd"]}
		}
	}
	knownTokens, knownMicroUSD, knownAPIListMicroUSD := int64(0), int64(0), int64(0)
	for _, value := range dimensions {
		row := value.(map[string]any)
		knownTokens += row["tokens"].(int64)
		knownMicroUSD += row["micro_usd"].(int64)
		knownAPIListMicroUSD += row["api_list_micro_usd"].(int64)
	}
	if knownTokens < totalTokens || knownMicroUSD < totalMicroUSD || knownAPIListMicroUSD < totalAPIListMicroUSD {
		dimensions["other"] = map[string]any{"tokens": totalTokens - knownTokens, "micro_usd": totalMicroUSD - knownMicroUSD, "api_list_micro_usd": totalAPIListMicroUSD - knownAPIListMicroUSD}
	}
	if pricedTokens+unpricedTokens < totalTokens {
		if unpriced == 0 {
			pricedTokens += totalTokens - pricedTokens - unpricedTokens
		} else {
			unpricedTokens += totalTokens - pricedTokens - unpricedTokens
		}
	}
	evidence := make([]PriceEvidence, 0, len(evidenceTotals))
	for _, value := range evidenceTotals {
		evidence = append(evidence, value)
	}
	sort.Slice(evidence, func(i, j int) bool {
		left, right := evidence[i], evidence[j]
		if left.Tokens != right.Tokens {
			return left.Tokens > right.Tokens
		}
		if left.APIListMicroUSD != right.APIListMicroUSD {
			return left.APIListMicroUSD > right.APIListMicroUSD
		}
		if left.MicroUSD != right.MicroUSD {
			return left.MicroUSD > right.MicroUSD
		}
		return left.Key < right.Key
	})
	if len(evidence) > MaxCostReportEvidence {
		omittedEvidence += int64(len(evidence) - MaxCostReportEvidence)
		evidence = evidence[:MaxCostReportEvidence]
	}
	evidenceRows := make([]any, 0, len(evidence))
	catalogRevision := int64(0)
	for _, value := range evidence {
		if value.CatalogRevision > catalogRevision {
			catalogRevision = value.CatalogRevision
		}
		evidenceRows = append(evidenceRows, map[string]any{
			"key": value.Key, "provider": value.Provider, "model": value.Model, "dimension": value.Dimension,
			"direction": value.Direction, "modality": value.Modality, "cache_role": value.CacheRole, "unit": value.Unit,
			"pricing_profile": value.PricingProfile, "service_tier": value.ServiceTier, "catalog_model": value.CatalogModel,
			"rate_usd": value.RateUSD, "rate_scale": value.RateScale, "effective_from": value.EffectiveFrom,
			"source_kind": value.SourceKind, "source_url": value.SourceURL, "catalog_revision": value.CatalogRevision,
			"tokens": value.Tokens, "micro_usd": value.MicroUSD, "api_list_micro_usd": value.APIListMicroUSD, "priced_atoms": value.PricedAtoms,
		})
	}
	return map[string]any{
		"schema_version": 4, "total_micro_usd": totalMicroUSD, "total_api_list_micro_usd": totalAPIListMicroUSD, "total_tokens": totalTokens,
		"dimensions": dimensions, "priced": map[string]any{"atoms": priced, "tokens": pricedTokens},
		"unpriced": map[string]any{"atoms": unpriced, "tokens": unpricedTokens},
		"models":   costAttributionWire(modelTotals, false), "agents": costAttributionWire(agentTotals, true), "evidence": evidenceRows,
		"catalog_revision":    catalogRevision,
		"omissions":           map[string]any{"models": omittedModels, "agents": omittedAgents, "evidence": omittedEvidence},
		"reasoning_available": false,
	}
}

func costAttributionWire(values map[string]MaterializedCostAttribution, agent bool) []any {
	rows := sortedCostAttributions(values)
	result := make([]any, 0, len(rows))
	for _, value := range rows {
		dimensions := costAttributionDimensionsWire(value.Dimensions)
		row := map[string]any{"key": value.Key, "total_tokens": value.TotalTokens, "total_micro_usd": value.MicroUSD, "total_api_list_micro_usd": value.APIListMicroUSD, "dimensions": dimensions,
			"priced": map[string]any{"atoms": value.PricedAtoms, "tokens": value.PricedTokens}, "unpriced": map[string]any{"atoms": value.UnpricedAtoms, "tokens": value.UnpricedTokens}}
		if agent {
			source := costSourceKind(value.Source)
			label := value.Label
			if label == "" {
				label = value.Key
			}
			row["source"], row["label"] = source, label
			row["sources"] = costSourceRows(value, source, dimensions)
		} else {
			row["provider"], row["model"] = value.Provider, value.Model
		}
		result = append(result, row)
	}
	return result
}

func costAttributionDimensionsWire(values []MaterializedCostDimension) map[string]any {
	dimensions := make(map[string]any, len(costReportDimensions))
	for _, name := range costReportDimensions {
		dimensions[name] = map[string]any{"tokens": int64(0), "micro_usd": int64(0), "api_list_micro_usd": int64(0)}
	}
	for _, value := range values {
		dimensions[value.Dimension] = map[string]any{"tokens": value.Tokens, "micro_usd": value.MicroUSD, "api_list_micro_usd": value.APIListMicroUSD}
	}
	return dimensions
}

func costSourceRows(value MaterializedCostAttribution, fallbackSource string, fallbackDimensions map[string]any) []any {
	sources := append([]MaterializedCostSource(nil), value.Sources...)
	if len(sources) == 0 {
		sources = append(sources, MaterializedCostSource{Source: fallbackSource, TotalTokens: value.TotalTokens, MicroUSD: value.MicroUSD, APIListMicroUSD: value.APIListMicroUSD, Dimensions: value.Dimensions, PricedAtoms: value.PricedAtoms, UnpricedAtoms: value.UnpricedAtoms, PricedTokens: value.PricedTokens, UnpricedTokens: value.UnpricedTokens})
	}
	sort.Slice(sources, func(i, j int) bool {
		left, right := sources[i], sources[j]
		if left.TotalTokens != right.TotalTokens {
			return left.TotalTokens > right.TotalTokens
		}
		if left.APIListMicroUSD != right.APIListMicroUSD {
			return left.APIListMicroUSD > right.APIListMicroUSD
		}
		if left.MicroUSD != right.MicroUSD {
			return left.MicroUSD > right.MicroUSD
		}
		return left.Source < right.Source
	})
	rows := make([]any, 0, len(sources))
	for _, source := range sources {
		dimensions := fallbackDimensions
		if len(source.Dimensions) > 0 {
			dimensions = costAttributionDimensionsWire(source.Dimensions)
		}
		rows = append(rows, map[string]any{"source": costSourceKind(source.Source), "total_tokens": source.TotalTokens, "total_micro_usd": source.MicroUSD, "total_api_list_micro_usd": source.APIListMicroUSD,
			"dimensions": dimensions, "priced": map[string]any{"atoms": source.PricedAtoms, "tokens": source.PricedTokens}, "unpriced": map[string]any{"atoms": source.UnpricedAtoms, "tokens": source.UnpricedTokens}})
	}
	return rows
}

func costSourceKind(value string) string {
	switch value {
	case "claude", "codex", "opencode", "yoagent", "summary", "images", "responses", "legacy", "unknown":
		return value
	default:
		return "unknown"
	}
}

func sortedCostAttributions(values map[string]MaterializedCostAttribution) []MaterializedCostAttribution {
	rows := make([]MaterializedCostAttribution, 0, len(values))
	for _, value := range values {
		rows = append(rows, value)
	}
	sort.Slice(rows, func(i, j int) bool {
		left, right := rows[i], rows[j]
		if left.TotalTokens != right.TotalTokens {
			return left.TotalTokens > right.TotalTokens
		}
		if left.APIListMicroUSD != right.APIListMicroUSD {
			return left.APIListMicroUSD > right.APIListMicroUSD
		}
		if left.MicroUSD != right.MicroUSD {
			return left.MicroUSD > right.MicroUSD
		}
		return left.Key < right.Key
	})
	return rows
}

func mergeCostAttribution(target map[string]MaterializedCostAttribution, value MaterializedCostAttribution) {
	current := target[value.Key]
	current.Key, current.Provider, current.Model, current.Source, current.Label = value.Key, value.Provider, value.Model, value.Source, value.Label
	current.TotalTokens += value.TotalTokens
	current.MicroUSD += value.MicroUSD
	current.APIListMicroUSD += value.APIListMicroUSD
	current.PricedAtoms += value.PricedAtoms
	current.UnpricedAtoms += value.UnpricedAtoms
	current.PricedTokens += value.PricedTokens
	current.UnpricedTokens += value.UnpricedTokens
	mergeCostDimensions(&current.Dimensions, value.Dimensions)
	for _, source := range value.Sources {
		mergeCostSource(&current, source)
	}
	target[value.Key] = current
}

func mergeCostSource(target *MaterializedCostAttribution, value MaterializedCostSource) {
	for index := range target.Sources {
		current := &target.Sources[index]
		if current.Source != value.Source {
			continue
		}
		current.TotalTokens += value.TotalTokens
		current.MicroUSD += value.MicroUSD
		current.APIListMicroUSD += value.APIListMicroUSD
		current.PricedAtoms += value.PricedAtoms
		current.UnpricedAtoms += value.UnpricedAtoms
		current.PricedTokens += value.PricedTokens
		current.UnpricedTokens += value.UnpricedTokens
		mergeCostDimensions(&current.Dimensions, value.Dimensions)
		return
	}
	value.Dimensions = append([]MaterializedCostDimension(nil), value.Dimensions...)
	target.Sources = append(target.Sources, value)
}

func mergeCostDimensions(target *[]MaterializedCostDimension, values []MaterializedCostDimension) {
	for _, value := range values {
		for index := range *target {
			if (*target)[index].Dimension != value.Dimension {
				continue
			}
			(*target)[index].Tokens += value.Tokens
			(*target)[index].MicroUSD += value.MicroUSD
			(*target)[index].APIListMicroUSD += value.APIListMicroUSD
			value.Dimension = ""
			break
		}
		if value.Dimension != "" {
			*target = append(*target, value)
		}
	}
}

func mergePriceEvidence(target map[string]PriceEvidence, value PriceEvidence) {
	if current, exists := target[value.Key]; exists {
		current.Tokens += value.Tokens
		current.MicroUSD += value.MicroUSD
		current.APIListMicroUSD += value.APIListMicroUSD
		current.PricedAtoms += value.PricedAtoms
		target[value.Key] = current
		return
	}
	target[value.Key] = value
}
