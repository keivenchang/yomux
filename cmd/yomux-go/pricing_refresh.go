// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math/rand/v2"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/keivenchang/yomux/internal/productenv"
)

const pricingRefreshInterval = 24 * time.Hour
const pricingRefreshJitter = time.Hour
const pricingRetryMaximum = 6 * time.Hour

// State ownership precedes both offline restore and later provider publications.
func (s *server) initializePricingCache() (*os.File, error) {
	directory := filepath.Join(s.state, "go-pricing", strconv.Itoa(s.port))
	lease, err := acquireConfigLease(directory)
	if err != nil {
		return nil, err
	}
	s.pricing.SetRefreshCachePath(filepath.Join(directory, "pricing-refresh.json"))
	if err := s.pricing.RestoreRefreshCache(); err != nil {
		slog.Warn("pricing cache restore failed", "error", err)
		s.pricingRefreshState = map[string]any{"status": "failed", "error": err.Error()}
	} else if status, warnings := s.pricing.RefreshResult(); status != "" {
		s.pricingRefreshState = map[string]any{"status": status, "warnings": warnings}
	}
	return lease, nil
}

func pricingRetryDelay(failures int) time.Duration {
	delay := 5 * time.Minute
	for i := 1; i < failures && delay < pricingRetryMaximum; i++ {
		delay *= 2
	}
	return min(delay, pricingRetryMaximum)
}

func (s *server) pricingContext() context.Context {
	if s.refillContext != nil {
		return s.refillContext
	}
	return context.Background()
}

func (s *server) notifyPricingScheduleLocked() {
	if s.pricingWake != nil {
		select {
		case s.pricingWake <- struct{}{}:
		default:
		}
	}
}

// Provider publication and durable invalidation share one single-flight owner.
// Acknowledgement follows the writer so a crash cannot strand a new price revision.
func (s *server) settlePricingInvalidation(ctx context.Context) error {
	if !s.pricing.PricingInvalidationPending() {
		return nil
	}
	if s.pricingInvalidate != nil {
		if err := s.pricingInvalidate(ctx); err != nil {
			return err
		}
	} else {
		if s.statsWriter == nil {
			return errors.New("pricing invalidation writer unavailable")
		}
		if _, err := s.statsWriter.InvalidatePricingCatalog(ctx, float64(time.Now().UnixNano())/1e9); err != nil {
			return err
		}
	}
	return s.pricing.AcknowledgePricingInvalidation()
}

func (s *server) pricingStateLocked() map[string]any {
	state := map[string]any{"ok": true, "status": "idle"}
	for key, value := range s.pricingRefreshState {
		state[key] = value
	}
	state["failure_count"] = s.pricingFailures
	state["invalidation_failure_count"] = s.pricingInvalidationFailures
	state["provider_refresh_queued"] = s.pricingTrailingReason != ""
	state["next_refresh_at"] = nil
	if !s.pricingNextRefresh.IsZero() {
		state["next_refresh_at"] = float64(s.pricingNextRefresh.UnixNano()) / 1e9
	}
	state["next_invalidation_at"] = nil
	if !s.pricingNextInvalidation.IsZero() {
		state["next_invalidation_at"] = float64(s.pricingNextInvalidation.UnixNano()) / 1e9
	}
	return state
}

func (s *server) startPricingRefresh(reason string) (map[string]any, bool) {
	s.pricingRefreshMu.Lock()
	if s.pricingRefreshing {
		// Settlement-only work cannot satisfy a provider refresh. Preserve one trailing intent.
		if reason != "invalidation" && s.pricingRefreshState["reason"] == "invalidation" {
			if s.pricingTrailingReason == "" || reason == "manual" {
				s.pricingTrailingReason = reason
			}
		}
		state := s.pricingStateLocked()
		state["coalesced"] = true
		s.pricingRefreshMu.Unlock()
		return state, true
	}
	s.pricingRefreshEpoch++
	s.pricingRefreshing = true
	s.pricingRefreshState = map[string]any{
		"status": "running", "started_at": float64(time.Now().UnixNano()) / 1e9,
		"reason": reason, "key": "pricing-refresh", "epoch": s.pricingRefreshEpoch,
		"ticket": fmt.Sprintf("pricing-refresh-%d", s.pricingRefreshEpoch),
	}
	response := s.pricingStateLocked()
	response["coalesced"] = false
	s.pricingRefreshMu.Unlock()
	if !s.startBackgroundWork(func() { s.runPricingRefresh(reason) }) {
		s.pricingRefreshMu.Lock()
		s.pricingRefreshing = false
		s.pricingTrailingReason = ""
		s.pricingRefreshState["status"] = "failed"
		s.pricingRefreshState["error"] = "server is shutting down"
		s.notifyPricingScheduleLocked()
		s.pricingRefreshMu.Unlock()
		return map[string]any{"error": "server is shutting down"}, false
	}
	return response, true
}

func (s *server) runPricingRefresh(reason string) {
	ctx := s.pricingContext()
	// Restored pending work must retry even if provider fetching fails this round.
	settlementErr := s.settlePricingInvalidation(ctx)
	var refreshErr error
	if reason != "invalidation" {
		run := s.pricingRefreshRun
		if run == nil {
			run = s.pricing.RefreshFromSources
		}
		refreshErr = run(ctx)
		settlementErr = s.settlePricingInvalidation(ctx)
	}
	err := errors.Join(refreshErr, settlementErr)
	status, warnings := s.pricing.RefreshResult()
	now := time.Now()
	s.pricingRefreshMu.Lock()
	s.pricingRefreshState["finished_at"] = float64(now.UnixNano()) / 1e9
	s.pricingRefreshState["refresh_status"] = status
	s.pricingRefreshState["warnings"] = warnings
	s.pricingRefreshState["catalog_revision"] = s.pricing.Revision()
	if reason != "invalidation" {
		if refreshErr != nil {
			s.pricingFailures++
		} else {
			s.pricingFailures = 0
		}
	}
	if settlementErr != nil {
		s.pricingInvalidationFailures++
	} else {
		s.pricingInvalidationFailures = 0
	}
	if err != nil {
		s.pricingRefreshState["status"] = "failed"
		s.pricingRefreshState["error"] = err.Error()
	} else if reason == "invalidation" && status == "failed" {
		s.pricingRefreshState["status"] = "failed"
	} else {
		s.pricingRefreshState["status"] = "done"
	}
	withJitter := func(delay time.Duration) time.Duration {
		return delay + time.Duration(rand.Float64()*float64(min(delay, pricingRefreshJitter)))
	}
	retryWithJitter := func(failures int) time.Duration {
		return min(pricingRetryMaximum, withJitter(pricingRetryDelay(failures)))
	}
	if ctx.Err() != nil {
		s.pricingNextRefresh, s.pricingNextInvalidation = time.Time{}, time.Time{}
	} else {
		if reason != "invalidation" && s.pricingPeriodic {
			delay := withJitter(pricingRefreshInterval)
			if refreshErr != nil {
				delay = retryWithJitter(s.pricingFailures)
			}
			s.pricingNextRefresh = now.Add(delay)
		}
		s.pricingNextInvalidation = time.Time{}
		if s.pricing.PricingInvalidationPending() {
			s.pricingNextInvalidation = now.Add(retryWithJitter(s.pricingInvalidationFailures))
		}
	}
	state := s.pricingStateLocked()
	s.pricingRefreshMu.Unlock()
	if s.events != nil {
		if _, publishErr := s.events.publish("pricing_catalog_changed", state); publishErr != nil {
			slog.Warn("pricing refresh event publication failed", "error", publishErr)
			s.pricingRefreshMu.Lock()
			s.pricingRefreshState["event_error"] = publishErr.Error()
			s.pricingRefreshMu.Unlock()
		}
	}
	if err != nil {
		slog.Warn("pricing refresh failed", "reason", reason, "error", err)
	}
	s.pricingRefreshMu.Lock()
	s.pricingRefreshing = false
	trailing := s.pricingTrailingReason
	s.pricingTrailingReason = ""
	s.notifyPricingScheduleLocked()
	s.pricingRefreshMu.Unlock()
	if trailing != "" && ctx.Err() == nil {
		s.startPricingRefresh(trailing)
	}
}

func (s *server) startPricingLifecycle() {
	s.pricingRefreshMu.Lock()
	s.pricingWake = make(chan struct{}, 1)
	s.pricingPeriodic = productenv.Value("TEST_DISABLE_PRICING_REFRESH") != "1"
	if s.pricingPeriodic {
		delay := time.Duration(rand.Float64() * float64(pricingRefreshJitter))
		if !s.pricing.RefreshDue(time.Now()) {
			delay += pricingRefreshInterval
		}
		s.pricingNextRefresh = time.Now().Add(delay)
	}
	if s.pricing.PricingInvalidationPending() {
		s.pricingNextInvalidation = time.Now()
	}
	s.pricingRefreshMu.Unlock()
	s.startBackgroundWork(s.pricingScheduleLoop)
}

func (s *server) pricingScheduleLoop() {
	ctx := s.pricingContext()
	for {
		s.pricingRefreshMu.Lock()
		next, reason := s.pricingNextRefresh, "periodic"
		if !s.pricingNextInvalidation.IsZero() && (next.IsZero() || s.pricingNextInvalidation.Before(next)) {
			next, reason = s.pricingNextInvalidation, "invalidation"
		}
		running := s.pricingRefreshing
		wake := s.pricingWake
		s.pricingRefreshMu.Unlock()
		var tick <-chan time.Time
		var timer *time.Timer
		if !next.IsZero() && !running {
			timer = time.NewTimer(max(0, time.Until(next)))
			tick = timer.C
		}
		select {
		case <-ctx.Done():
			if timer != nil {
				timer.Stop()
			}
			s.pricingRefreshMu.Lock()
			s.pricingNextRefresh, s.pricingNextInvalidation = time.Time{}, time.Time{}
			s.pricingRefreshMu.Unlock()
			return
		case <-wake:
			if timer != nil {
				timer.Stop()
			}
		case <-tick:
			if _, accepted := s.startPricingRefresh(reason); !accepted {
				return
			}
		}
	}
}
