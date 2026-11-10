// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package stats

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"
)

// AppendError exposes interval identity without exposing source paths or payloads.
// Unwrap retains the original cause for local classification.
type AppendError struct {
	Collector       string   `json:"collector"`
	Family          string   `json:"family"`
	SourceID        string   `json:"source_id"`
	Epoch           string   `json:"epoch"`
	IncomingStart   float64  `json:"incoming_start"`
	StoredStart     *float64 `json:"stored_start"`
	OwnerGeneration uint64   `json:"owner_generation"`
	PruneCutoff     float64  `json:"prune_cutoff"`
	Reason          string   `json:"reason"`
	cause           error
}

func (e *AppendError) Error() string {
	stored := "absent"
	if e.StoredStart != nil {
		stored = fmt.Sprint(*e.StoredStart)
	}
	return fmt.Sprintf("%s append: %s (family=%s source=%s epoch=%s incoming=%g stored=%v owner=%d prune=%g)", e.Collector, e.Reason, e.Family, e.SourceID, e.Epoch, e.IncomingStart, stored, e.OwnerGeneration, e.PruneCutoff)
}
func (e *AppendError) Unwrap() error     { return e.cause }
func opaqueIdentity(value string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(value)))[:24] }

// CollectorHealth is shared by all recording owners. Error evidence survives
// recovery; only an acknowledged commit resets consecutive failures.
type CollectorHealth struct {
	LastAttempt          float64      `json:"last_attempt"`
	LastSuccessfulCommit float64      `json:"last_successful_commit"`
	ConsecutiveFailures  uint64       `json:"consecutive_failures"`
	Rejected             uint64       `json:"rejected"`
	Dropped              uint64       `json:"dropped"`
	QueueDepth           int          `json:"queue_depth"`
	FirstError           *AppendError `json:"first_error"`
	LatestError          *AppendError `json:"latest_error"`
}
type CollectorHealthTracker struct {
	mu      sync.Mutex
	states  map[string]CollectorHealth
	lastLog map[string]time.Time
}

func (h *CollectorHealthTracker) Attempt(name string, at time.Time) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.states == nil {
		h.states = make(map[string]CollectorHealth)
		h.lastLog = make(map[string]time.Time)
	}
	state := h.states[name]
	state.LastAttempt = float64(at.UnixNano()) / 1e9
	h.states[name] = state
}
func (h *CollectorHealthTracker) Record(name string, at time.Time, err error, queue int, dropped uint64) {
	h.Attempt(name, at)
	h.mu.Lock()
	defer h.mu.Unlock()
	state := h.states[name]
	state.QueueDepth = queue
	state.Dropped = dropped
	if err != nil {
		var detail *AppendError
		if !errors.As(err, &detail) {
			detail = &AppendError{Collector: name, Reason: "recording_failed", cause: err}
		}
		copyDetail := *detail
		copyDetail.Collector = name
		detail = &copyDetail
		changed := state.LatestError == nil || (state.LatestError.Reason != detail.Reason || state.LatestError.Family != detail.Family || state.LatestError.SourceID != detail.SourceID || state.LatestError.Epoch != detail.Epoch)
		state.ConsecutiveFailures++
		state.Rejected++
		if state.FirstError == nil {
			state.FirstError = detail
		}
		state.LatestError = detail
		if state.ConsecutiveFailures == 1 || changed || at.Sub(h.lastLog[name]) >= time.Minute {
			slog.Warn("stats collector recording failed", "collector", name, "detail", detail)
			h.lastLog[name] = at
		}
	} else {
		if state.ConsecutiveFailures > 0 {
			slog.Info("stats collector recording recovered", "collector", name)
		}
		state.LastSuccessfulCommit = float64(at.UnixNano()) / 1e9
		state.ConsecutiveFailures = 0
	}
	h.states[name] = state
}
func (h *CollectorHealthTracker) Snapshot() map[string]CollectorHealth {
	h.mu.Lock()
	defer h.mu.Unlock()
	result := make(map[string]CollectorHealth, len(h.states))
	for key, value := range h.states {
		result[key] = value
	}
	return result
}
