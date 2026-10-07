// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestProjectCostReportBoundsWindowDetailsWithoutMutatingBuckets(t *testing.T) {
	buckets := make([]MaterializedBucket, 288)
	for bucketIndex := range buckets {
		cost := MaterializedCostDetail{Tokens: 100, MicroUSD: 20, APIListMicroUSD: 30, PricedAtoms: 1, PricedTokens: 100}
		for index := 0; index < 20; index++ {
			key := string(rune('a' + index))
			cost.Models = append(cost.Models, MaterializedCostAttribution{Key: "model-" + key, TotalTokens: int64(index + 1), MicroUSD: int64(index), APIListMicroUSD: int64(index * 2)})
			cost.Agents = append(cost.Agents, MaterializedCostAttribution{Key: "agent-" + key, TotalTokens: int64(index + 1), MicroUSD: int64(index), APIListMicroUSD: int64(index * 2), Sources: []MaterializedCostSource{{Source: "claude", TotalTokens: int64(index + 1)}}})
		}
		for index := 0; index < 40; index++ {
			key := string(rune('a' + index))
			cost.Evidence = append(cost.Evidence, PriceEvidence{Key: "evidence-" + key, Tokens: int64(index + 1)})
		}
		buckets[bucketIndex] = MaterializedBucket{Start: float64(bucketIndex * 300), Duration: 300, Cost: cost}
	}
	before, err := json.Marshal(buckets)
	if err != nil {
		t.Fatal(err)
	}
	first := ProjectCostReport(buckets)
	second := ProjectCostReport(buckets)
	if !reflect.DeepEqual(first, second) {
		t.Fatal("repeated cost report projection changed its output")
	}
	if len(first.Models) != MaxCostReportModels || len(first.Agents) != MaxCostReportAgents || len(first.Evidence) != MaxCostReportEvidence {
		t.Fatalf("projected detail lengths = %d/%d/%d", len(first.Models), len(first.Agents), len(first.Evidence))
	}
	if first.OmittedModels != 4 || first.OmittedAgents != 4 || first.OmittedEvidence != 8 {
		t.Fatalf("omitted counts = %d/%d/%d", first.OmittedModels, first.OmittedAgents, first.OmittedEvidence)
	}
	if first.Models[0].Key != "model-t" || first.Agents[0].Key != "agent-t" {
		t.Fatalf("ranking did not prefer highest window total: model=%s agent=%s", first.Models[0].Key, first.Agents[0].Key)
	}
	if first.Models[0].TotalTokens != 288*20 || first.Agents[0].TotalTokens != 288*20 || first.Agents[0].Sources[0].TotalTokens != 288*20 {
		t.Fatalf("window totals were not merged: model=%+v agent=%+v", first.Models[0], first.Agents[0])
	}
	after, err := json.Marshal(buckets)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(before, after) {
		t.Fatal("projection mutated materialized bucket details")
	}
	if size, err := CostReportDetailBytes(buckets); err != nil || size <= 0 {
		t.Fatalf("cost report sizing = %d, %v", size, err)
	}
}

func TestProjectCostReportRankingIsIndependentOfBucketOrder(t *testing.T) {
	a := MaterializedBucket{Cost: MaterializedCostDetail{Models: []MaterializedCostAttribution{{Key: "a", TotalTokens: 2}, {Key: "b", TotalTokens: 1}}}}
	b := MaterializedBucket{Cost: MaterializedCostDetail{Models: []MaterializedCostAttribution{{Key: "a", TotalTokens: 3}, {Key: "b", TotalTokens: 5}}}}
	forward := ProjectCostReport([]MaterializedBucket{a, b})
	reverse := ProjectCostReport([]MaterializedBucket{b, a})
	if !reflect.DeepEqual(forward, reverse) {
		t.Fatalf("bucket order changed report: forward=%+v reverse=%+v", forward, reverse)
	}
}
