// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package main

import (
	"context"
	"strconv"

	"github.com/keivenchang/yomux/internal/stats"
)

type goAgentWindowProjection struct {
	topologyGeneration uint64
	revision           int64
	fresh              bool
	windows            map[string]any
	agents             []any
}

// Metadata owns identity and description; the collector owns current status.
// Expiring an unrelated repository scan must not erase a validated live run.
func (s *server) projectAgentWindows(sessions map[string]any, topologyGeneration uint64, metadataStale bool, snapshot stats.AgentStatusSnapshot) (goAgentWindowProjection, error) {
	result := goAgentWindowProjection{topologyGeneration: topologyGeneration, revision: snapshot.SourceGeneration, windows: map[string]any{}, agents: []any{}}
	overrides := activityAgentStatusOverrides(snapshot)
	now := s.attentionAckNow()
	collectorFresh := snapshot.Available && snapshot.Freshness != nil && snapshot.Freshness.ObservedAt > 0
	for session, raw := range sessions {
		info := raw.(map[string]any)
		rows, _ := info["agents"].([]map[string]any)
		retained := make([]map[string]any, 0, len(rows))
		for _, agent := range rows {
			if metadataStale || (s.statsService != nil && stringValue(agent["process_instance_id"]) != "") {
				override, exists := overrides[activityAgentStatusKey(session, stringValue(agent["pane_target"]), stringValue(agent["kind"]))]
				processID := stringValue(agent["process_instance_id"])
				if !collectorFresh || !exists || processID == "" || override.processInstanceID != processID || override.observedAt.IsZero() || !agentStatusOverrideObservationMatches(agent, override, now) || override.state == "unavailable" {
					continue
				}
			}
			retained = append(retained, agent)
		}
		info["agents"] = retained
	}
	if err := s.applyAgentStatusLifecycle(sessions, overrides); err != nil {
		return result, err
	}
	result.fresh = !metadataStale || collectorFresh
	for session, raw := range sessions {
		info := raw.(map[string]any)
		rows := info["agents"].([]map[string]any)
		windowRows := make([]any, 0, len(rows))
		for _, agent := range rows {
			window, _ := agent["window_index"].(int)
			row := map[string]any{"session": session, "window": strconv.Itoa(window), "window_index": window,
				"pane_target": agent["pane_target"], "kind": agent["kind"], "agent_kind": agent["kind"], "state": stringValue(agent["status"]),
				"current": agent["current"], "window_active": agent["window_active"]}
			for _, field := range []string{"process_instance_id", "status_observed_at", "working_stopped_ts", "transition_id", "cooldown_attention_key", "cooldown_acknowledged", "attention_key", "attention_signature", "attention_acknowledged", "queued_followup_question"} {
				if value, exists := agent[field]; exists {
					row[field] = value
				}
			}
			windowRows = append(windowRows, row)
			result.agents = append(result.agents, row)
		}
		if len(windowRows) > 0 {
			result.windows[session] = windowRows
		}
	}
	return result, nil
}

func (s *server) currentAgentWindowProjection(ctx context.Context, topology goTmuxTopology) goAgentWindowProjection {
	metadata, generation, stale, _ := s.metadataSnapshotWithBuiltAt()
	sessions := metadataSessionsForTopology(metadata, generation, topology)
	snapshot, err := s.currentAgentStatusSnapshot(ctx)
	if err != nil {
		snapshot = stats.AgentStatusSnapshot{}
	}
	projection, err := s.projectAgentWindows(sessions, topology.generation, metadata == nil || stale || metadataPayloadTopologyGeneration(metadata) != topology.generation, snapshot)
	if err != nil {
		return goAgentWindowProjection{topologyGeneration: topology.generation, windows: map[string]any{}, agents: []any{}}
	}
	return projection
}
