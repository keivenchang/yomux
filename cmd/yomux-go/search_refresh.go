// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"log/slog"
	"sort"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
)

type indexRefreshDeadline struct {
	next     time.Time
	active   bool
	cadence  time.Duration
	revision uint64
	scopes   []string
}

func indexAddedScopes(previous, current []string) bool {
	known := map[string]bool{}
	for _, path := range previous {
		known[path] = true
	}
	for _, path := range current {
		if !known[path] {
			return true
		}
	}
	return false
}

// Advancing a deadline is independent of wall-clock timers, so hidden cadence is testable.
func updateIndexDeadline(previous indexRefreshDeadline, exists, active bool, cadence time.Duration, revision uint64, now time.Time, stagger time.Duration, demandedScopes ...string) indexRefreshDeadline {
	if !exists || previous.active != active || previous.cadence != cadence || previous.revision != revision || active && indexAddedScopes(previous.scopes, demandedScopes) {
		next := now.Add(cadence + stagger)
		if active {
			next = now
		}
		if cadence == 0 && !active {
			next = time.Time{}
		}
		return indexRefreshDeadline{next: next, active: active, cadence: cadence, revision: revision, scopes: append([]string(nil), demandedScopes...)}
	}
	previous.scopes = append([]string(nil), demandedScopes...)
	return previous
}

func (s *server) reconcileFinderIndexes(ctx context.Context) {
	deadlines := map[string]indexRefreshDeadline{}
	timer := time.NewTimer(0)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-s.finder.DemandChanged():
		case <-s.indexRefreshWake:
		case <-timer.C:
		}
		now := time.Now()
		s.indexSettingsMu.RLock()
		settings := s.indexSettings
		pendingFence := s.indexSettingsFencePending
		s.indexSettingsMu.RUnlock()
		if pendingFence {
			continue
		}
		demand := s.finder.DemandRoots()
		if s.searchIndex != nil {
			requestCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
			if err := s.searchIndex.UpdateDemand(requestCtx, demand); err != nil && ctx.Err() == nil {
				slog.Warn("Finder demand update failed", "error", err)
			}
			cancel()
		}
		configured := map[string]bool{}
		wake := now.Add(24 * time.Hour)
		for position, root := range settings.roots {
			configured[root] = true
			active := finder.IndexRootDemanded(root, demand)
			seconds := settings.backgroundRefreshSecs
			if active {
				seconds = settings.refreshSecs
			}
			cadence := time.Duration(seconds) * time.Second
			prior, exists := deadlines[root]
			stagger := time.Duration(0)
			if !active && cadence > 0 {
				stagger = time.Duration(position) * cadence / time.Duration(max(10, len(settings.roots)*10))
			}
			scopes := []string{}
			for _, path := range demand {
				if finder.IndexRootDemanded(root, []string{path}) {
					scopes = append(scopes, path)
				}
			}
			sort.Strings(scopes)
			deadline := updateIndexDeadline(prior, exists, active, cadence, settings.revision, now, stagger, scopes...)
			if !deadline.next.IsZero() && !deadline.next.After(now) {
				options := s.finderSearchOptions(root, "", false)
				options.ForceRefresh = true
				options.DemandScoped = active
				if active && (!exists || !prior.active || indexAddedScopes(prior.scopes, scopes)) {
					for _, path := range demand {
						if finder.IndexRootDemanded(root, []string{path}) {
							options.ChangedPaths = append(options.ChangedPaths, path)
						}
					}
				}
				options.Background = !active
				requestContext, cancel := context.WithTimeout(ctx, 5*time.Second)
				_, err := s.ensureFinderIndex(requestContext, root, options)
				cancel()
				if err != nil && ctx.Err() == nil {
					slog.Warn("Finder scheduled refresh failed", "root", root, "error", err.Message)
				}
				deadline.next = now.Add(cadence)
				if cadence == 0 {
					deadline.next = time.Time{}
				}
			}
			deadlines[root] = deadline
			if !deadline.next.IsZero() && deadline.next.Before(wake) {
				wake = deadline.next
			}
		}
		for root := range deadlines {
			if !configured[root] {
				delete(deadlines, root)
			}
		}
		s.indexRefreshMu.Lock()
		s.indexRefreshDeadlines = deadlinesCopy(deadlines)
		s.indexRefreshMu.Unlock()
		if !timer.Stop() {
			select {
			case <-timer.C:
			default:
			}
		}
		timer.Reset(max(time.Millisecond, time.Until(wake)))
	}
}

func deadlinesCopy(source map[string]indexRefreshDeadline) map[string]indexRefreshDeadline {
	result := make(map[string]indexRefreshDeadline, len(source))
	for root, deadline := range source {
		result[root] = deadline
	}
	return result
}

func (s *server) indexRefreshDiagnostics(root string) map[string]any {
	s.indexRefreshMu.Lock()
	defer s.indexRefreshMu.Unlock()
	deadline := s.indexRefreshDeadlines[root]
	next := ""
	if !deadline.next.IsZero() {
		next = deadline.next.Format(time.RFC3339)
	}
	return map[string]any{"active_demand": deadline.active, "next_refresh": next, "refresh_seconds": deadline.cadence.Seconds()}
}
