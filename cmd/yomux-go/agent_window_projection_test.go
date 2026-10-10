// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

func TestAgentWindowStatusSurvivesMetadataRefresh(t *testing.T) {
	now := time.Now()
	service := stats.NewStatsService(nil, stats.StatsServiceConfig{AgentStatus: func(context.Context) (stats.AgentStatusSnapshot, error) {
		return stats.AgentStatusSnapshot{Available: true, SourceGeneration: 23,
			Freshness: &stats.AgentStatusFreshness{ObservedAt: float64(now.UnixNano()) / 1e9},
			Agents:    []any{map[string]any{"session": "cached", "pane": "%7", "kind": "codex", "state": "run", "process_instance_id": "pid=42:start=100"}},
		}, nil
	}})
	if err := service.RefreshAgentStatus(context.Background()); err != nil {
		t.Fatal(err)
	}
	s := &server{statsService: service, tmuxWindowActivityFn: func() (map[string]float64, error) { return map[string]float64{}, nil },
		tmuxMetadataState: tmuxMetadataState{
			metadataGeneration: 7, metadataTopologyGeneration: 7,
			metadataTopologySignature: tmuxTopologySignature([]string{"cached"}, map[string]string{"cached": "cached"}),
			tmuxSessionRosterFn:       func() ([]string, error) { return []string{"cached"}, nil },
			tmuxSessionActivityFn:     func() (map[string]float64, error) { return map[string]float64{}, nil },
			metadataPayload: map[string]any{"topology_generation": uint64(7), "sessions": map[string]any{"cached": map[string]any{
				"session_identity": goTmuxSessionIdentity("cached", "cached"),
				"agents":           []map[string]any{{"window_index": 0, "pane_target": "%7", "kind": "codex", "status": "idle", "process_instance_id": "pid=42:start=100"}},
			}}}, metadataBuiltAt: now,
			// A pending replacement cannot publish until the fixture releases it.
			metadataBuilding: true, metadataBuildingGeneration: 8, metadataBuildingTopologyGeneration: 7,
		},
	}
	for _, phase := range []string{"warm", "refresh-pending", "complete"} {
		t.Run(phase, func(t *testing.T) {
			s.metadataMu.Lock()
			if phase == "refresh-pending" {
				s.metadataBuiltAt = now.Add(-3 * goMetadataCacheTTL)
			} else {
				s.metadataBuiltAt = now
			}
			s.metadataMu.Unlock()
			record := httptest.NewRecorder()
			s.activity(record, httptest.NewRequest(http.MethodGet, "/api/activity", nil))
			var activity map[string]any
			if err := json.Unmarshal(record.Body.Bytes(), &activity); err != nil {
				t.Fatal(err)
			}
			rows := activity["agent_windows"].(map[string]any)["cached"]
			if rows == nil {
				t.Fatalf("%s dropped fresh collector status: %s", phase, record.Body.String())
			}
			row := rows.([]any)[0].(map[string]any)
			if row["state"] != "working" || row["process_instance_id"] != "pid=42:start=100" {
				t.Fatalf("canonical row = %#v", row)
			}
			yo := s.goYoloSessionPayload("cached")
			encoded, err := json.Marshal(yo["agent_windows"])
			if err != nil {
				t.Fatal(err)
			}
			var yoRows []any
			if err := json.Unmarshal(encoded, &yoRows); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(rows, yoRows) {
				t.Fatalf("activity/YO rows differ: %#v / %#v", rows, yoRows)
			}
			if activity["agent_window_status_fresh"] != true || yo["agent_window_status_fresh"] != true {
				t.Fatalf("freshness missing: activity=%#v yo=%#v", activity, yo)
			}
		})
	}
}

func TestAgentWindowProjectionRejectsUnvalidatedEvidence(t *testing.T) {
	now := time.Now()
	for _, scenario := range []string{"fresh", "expired", "unavailable", "empty-process", "process-replaced", "pane-reused", "kind-changed", "session-recreated", "session-removed", "cold-boot", "older-observation", "same-state-older-sample", "idle-cadence"} {
		t.Run(scenario, func(t *testing.T) {
			row := map[string]any{"window_index": 0, "pane_target": "%7", "kind": "codex", "status": "working", "process_instance_id": "process:1"}
			sample := map[string]any{"session": "cached", "pane": "%7", "kind": "codex", "state": "run", "process_instance_id": "process:1"}
			observed := now
			available := true
			topology := goTmuxTopology{sessions: []string{"cached"}, identities: map[string]string{"cached": "cached"}}
			metadata := map[string]any{"sessions": map[string]any{"cached": map[string]any{"session_identity": goTmuxSessionIdentity("cached", "cached"), "agents": []map[string]any{row}}}}
			switch scenario {
			case "expired":
				observed = now.Add(-2 * stats.AgentStatusActiveCadence)
				available = false
			case "unavailable":
				available = false
			case "empty-process":
				sample["process_instance_id"] = ""
			case "process-replaced":
				sample["process_instance_id"] = "process:2"
			case "pane-reused":
				sample["pane"] = "%8"
			case "kind-changed":
				sample["kind"] = "claude"
			case "session-recreated":
				topology.identities["cached"] = "replacement"
			case "session-removed":
				topology.sessions = nil
			case "cold-boot":
				metadata = nil
			case "idle-cadence":
				observed = now.Add(-30 * time.Second)
				sample["state"] = "idle"
			case "same-state-older-sample":
				row["status_observed_at"] = float64(now.Add(time.Second).UnixNano()) / 1e9
			case "older-observation":
				row["status"] = "idle"
				row["status_observed_at"] = float64(now.Add(time.Second).UnixNano()) / 1e9
			}
			snapshot := stats.AgentStatusSnapshot{Available: available, SourceGeneration: 3, Freshness: &stats.AgentStatusFreshness{ObservedAt: float64(observed.UnixNano()) / 1e9}, Agents: []any{sample}}
			s := &server{}
			projection, err := s.projectAgentWindows(metadataSessionsForTopology(metadata, 1, topology), 1, true, snapshot)
			if err != nil {
				t.Fatal(err)
			}
			want := 0
			if scenario == "fresh" || scenario == "same-state-older-sample" || scenario == "idle-cadence" {
				want = 1
			}
			if len(projection.agents) != want {
				t.Fatalf("%s published %#v", scenario, projection.agents)
			}
		})
	}
}

func TestAgentWindowProjectionKeepsAttentionAndCompletionDuringRefresh(t *testing.T) {
	now := time.Now()
	s := &server{config: t.TempDir()}
	for _, scenario := range []string{"completion", "queued-question", "blocked", "clear-question"} {
		t.Run(scenario, func(t *testing.T) {
			row := map[string]any{"window_index": 0, "pane_target": "%7", "kind": "codex", "status": "working", "process_instance_id": "process:1", "attention_key": "old", "attention_signature": "old-question", "attention_pending": true, "status_observed_at": float64(now.Add(-time.Minute).UnixNano()) / 1e9, "queued_followup_question": true}
			sample := map[string]any{"session": "cached", "pane": "%7", "kind": "codex", "state": "run", "process_instance_id": "process:1"}
			wantState := "working"
			switch scenario {
			case "completion":
				sample["state"], sample["working_stopped_ts"] = "transition", float64(now.Add(-time.Second).UnixNano())/1e9
				sample["transition_id"], sample["cooldown_attention_key"] = "transition:1", "completion:1"
				wantState = "cooldown"
			case "queued-question":
				sample["attention_key"], sample["attention_signature"], sample["queued_followup_question"] = "question:1", "question", true
			case "blocked":
				sample["state"], sample["attention_key"], sample["attention_signature"] = "error", "blocked:1", "blocked"
				wantState = "blocked"
			}
			snapshot := stats.AgentStatusSnapshot{Available: true, SourceGeneration: 3, Freshness: &stats.AgentStatusFreshness{ObservedAt: float64(now.UnixNano()) / 1e9}, Agents: []any{sample}}
			project := func() map[string]any {
				projection, err := s.projectAgentWindows(map[string]any{"cached": map[string]any{"agents": []map[string]any{cloneMap(row)}}}, 1, true, snapshot)
				if err != nil {
					t.Fatal(err)
				}
				if len(projection.agents) != 1 {
					t.Fatalf("missing row: %#v", projection)
				}
				return projection.agents[0].(map[string]any)
			}
			result := project()
			if result["state"] != wantState || result["queued_followup_question"] != (scenario == "queued-question") || result["attention_pending"] == true {
				t.Fatalf("lifecycle: %#v", result)
			}
			key := stringValue(result["attention_key"])
			field := "attention_acknowledged"
			if scenario == "completion" {
				key, field = stringValue(result["cooldown_attention_key"]), "cooldown_acknowledged"
			}
			if key != "" {
				ack := httptest.NewRecorder()
				s.acknowledgeAttention(ack, httptest.NewRequest(http.MethodPost, "/api/attention-ack", strings.NewReader(`{"key":"`+key+`"}`)))
				if ack.Code != 200 {
					t.Fatalf("ack: %s", ack.Body.String())
				}
				result = project()
				if result[field] != true || result["state"] != wantState {
					t.Fatalf("ack lost state: %#v", result)
				}
			} else if _, exists := result["attention_key"]; exists {
				t.Fatalf("stale question retained: %#v", result)
			}
		})
	}
}
