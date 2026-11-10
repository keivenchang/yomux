// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package main

import (
	"context"
	"encoding/json"
	"github.com/keivenchang/yomux/internal/stats"
	"log/slog"
	"time"
)

func (s *server) statsCollectorHealth() map[string]stats.CollectorHealth {
	result := map[string]stats.CollectorHealth{}
	merge := func(prefix string, states map[string]stats.CollectorHealth) {
		for name, state := range states {
			result[prefix+name] = state
		}
	}
	if s.hostCollector != nil {
		merge("host:", s.hostCollector.Health())
	}
	if s.agentStatusCollector != nil {
		merge("", s.agentStatusCollector.Health())
	}
	if s.tokenCollector != nil {
		merge("", s.tokenCollector.Health())
	}
	if s.statsRuntimeRecorder != nil {
		merge("", s.statsRuntimeRecorder.health.Snapshot())
	}
	return result
}

// Retain recording health on the authenticated core stream, independently of
// stats demand, snapshot publication, or heartbeat delivery.
func (s *server) publishStatsCollectorHealth(ctx context.Context) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	previous := ""
	for {
		health := s.statsCollectorHealth()
		encoded, err := json.Marshal(health)
		if err != nil {
			slog.Error("encode collector health", "error", err)
		} else if string(encoded) != previous {
			if _, err := s.events.publish("stats_collector_health", map[string]any{"collectors": health}); err != nil {
				slog.Error("publish collector health", "error", err)
			} else {
				previous = string(encoded)
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
