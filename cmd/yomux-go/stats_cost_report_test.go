// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"fmt"
	"reflect"
	"testing"

	"github.com/keivenchang/yomux/internal/stats"
)

func TestSnapshotWireBoundsWindowCostRowsAndPreservesTotals(t *testing.T) {
	cost := stats.MaterializedCostDetail{
		Tokens: 210, MicroUSD: 210, APIListMicroUSD: 420, PricedAtoms: 20, PricedTokens: 210,
		Dimensions: []stats.MaterializedCostDimension{{Dimension: "output", Tokens: 210, MicroUSD: 210, APIListMicroUSD: 420}},
	}
	for index := 0; index < 20; index++ {
		key := fmt.Sprintf("%024x", index+1)
		value := int64(index + 1)
		attribution := stats.MaterializedCostAttribution{
			Key: key, Provider: "provider", Model: "model-" + fmt.Sprint(index),
			Source: "claude", Label: "agent-" + fmt.Sprint(index),
			TotalTokens: value, MicroUSD: value, APIListMicroUSD: 2 * value,
			PricedAtoms: 1, PricedTokens: value,
			Dimensions: []stats.MaterializedCostDimension{{Dimension: "output", Tokens: value, MicroUSD: value, APIListMicroUSD: 2 * value}},
		}
		cost.Models = append(cost.Models, attribution)
		cost.Agents = append(cost.Agents, attribution)
	}
	for index := 0; index < 40; index++ {
		value := int64(index + 1)
		cost.Evidence = append(cost.Evidence, stats.PriceEvidence{
			Key: fmt.Sprintf("%024x", 1000+index), Provider: "provider", Model: "model", Dimension: "output", Direction: "output",
			Modality: "text", CacheRole: "none", Unit: "tokens", PricingProfile: "default", ServiceTier: "standard",
			CatalogModel: "model", RateUSD: "1.00", RateScale: 1_000_000, EffectiveFrom: "1970-01-01T00:00:00Z",
			SourceKind: "catalog", SourceURL: "https://example.invalid/pricing", CatalogRevision: 1,
			Tokens: value, MicroUSD: value, APIListMicroUSD: 2 * value, PricedAtoms: 1,
		})
	}
	bucket := stats.MaterializedBucket{Start: 0, Duration: 10, Complete: true, Cost: cost}
	snapshot := stats.StatsSnapshot{
		Request:      stats.SnapshotRequest{Start: 0, End: 10, Resolution: 10},
		Materialized: stats.MaterializedLayer{Start: 0, End: 10, Resolution: 10, Buckets: []stats.MaterializedBucket{bucket}},
	}
	before, err := json.Marshal(snapshot.Materialized.Buckets[0])
	if err != nil {
		t.Fatal(err)
	}
	wire := snapshotWire(snapshot, statsQuery{rangeSeconds: 10, resolution: 10})
	report := wire["cost_report"].(map[string]any)
	if got := len(report["models"].([]any)); got != stats.MaxCostReportModels {
		t.Fatalf("wire model rows = %d, want %d", got, stats.MaxCostReportModels)
	}
	if got := len(report["agents"].([]any)); got != stats.MaxCostReportAgents {
		t.Fatalf("wire agent rows = %d, want %d", got, stats.MaxCostReportAgents)
	}
	if got := len(report["evidence"].([]any)); got != stats.MaxCostReportEvidence {
		t.Fatalf("wire evidence rows = %d, want %d", got, stats.MaxCostReportEvidence)
	}
	if report["total_tokens"] != int64(210) || report["total_micro_usd"] != int64(210) || report["total_api_list_micro_usd"] != int64(420) {
		t.Fatalf("aggregate totals were truncated with details: %#v", report)
	}
	omissions := report["omissions"].(map[string]any)
	if omissions["models"] != int64(4) || omissions["agents"] != int64(4) || omissions["evidence"] != int64(8) {
		t.Fatalf("omitted detail rows were not accounted for: %#v", omissions)
	}
	after, err := json.Marshal(snapshot.Materialized.Buckets[0])
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(before, after) {
		t.Fatal("wire projection mutated its source ring bucket")
	}
}
