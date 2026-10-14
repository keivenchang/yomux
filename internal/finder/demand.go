// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"path/filepath"
	"sort"
	"time"
)

// Demand is derived from the same leases that own native subscriptions.
func (s *Service) DemandRoots() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	roots := []string{}
	now := time.Now()
	for _, record := range s.watches {
		if record.ExpiresAt.After(now) {
			roots = append(roots, record.Roots...)
			roots = append(roots, record.SearchRoots...)
		}
	}
	return uniqueWatchPaths(roots)
}

func (s *Service) DemandChanged() <-chan struct{} { return s.demandChanged }
func (s *Service) notifyDemandChanged() {
	select {
	case s.demandChanged <- struct{}{}:
	default:
	}
}

func recursiveWatchRoot(record watchRecord, root string) bool {
	for _, row := range record.RootSurfaces {
		if row["path"] == root {
			surfaces, _ := row["surfaces"].([]string)
			for _, surface := range surfaces {
				if surface == "modified-files-repository" {
					return true
				}
			}
			return false
		}
	}
	// Older clients did not declare their surface, and expect recursive coverage.
	return true
}

func sameNativeScopes(left, right map[string]bool) bool {
	if len(left) != len(right) {
		return false
	}
	for root, recursive := range left {
		value, ok := right[root]
		if !ok || value != recursive {
			return false
		}
	}
	return true
}

func (s *Service) activeNativeScopesLocked() map[string]bool {
	scopes := map[string]bool{}
	for _, record := range s.watches {
		for _, root := range record.Roots {
			scopes[root] = scopes[root] || recursiveWatchRoot(record, root)
		}
		for _, path := range append(append([]string{}, record.Files...), record.BackgroundFiles...) {
			parent := filepath.Dir(path)
			if _, ok := scopes[parent]; !ok {
				scopes[parent] = false
			}
		}
	}
	return scopes
}

func (s *Service) pathNativelyCoveredLocked(path string) bool {
	if !s.nativeStatus.available || s.nativeEventsClosed {
		return false
	}
	for _, root := range s.nativeStatus.uncovered {
		if indexRootWithin(root, path) {
			return false
		}
	}
	// Test/legacy controllers have no scoped coverage protocol.
	if _, ok := s.native.(scopedNativeWatchController); !ok {
		return !s.nativeStatus.degraded && watchPathCoveredByRoots(path, s.activeWatchRootsLocked())
	}
	for root, recursive := range s.nativeScopes {
		if path == root || recursive && indexRootWithin(root, path) || filepath.Dir(path) == root {
			return true
		}
	}
	return false
}
func (s *Service) pathNativelyCovered(path string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.pathNativelyCoveredLocked(path)
}

func (s *Service) watchPathSignature(ctx context.Context, path string, record watchRecord) string {
	s.mu.Lock()
	s.signatureScans++
	s.mu.Unlock()
	for _, root := range record.Roots {
		if root != path || recursiveWatchRoot(record, root) {
			continue
		}
		directory, _, _, err := s.open(path, true)
		if err != nil {
			return "error:" + err.Code
		}
		defer directory.Close()
		entries, readErr := directory.ReadDir(maxWatchSignatureEntries + 1)
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			return "error:" + readErr.Error()
		}
		truncated := len(entries) > maxWatchSignatureEntries
		if truncated {
			entries = entries[:maxWatchSignatureEntries]
		}
		info, statErr := directory.Stat()
		if statErr != nil {
			return "error:" + statErr.Error()
		}
		signatures := []any{}
		for _, entry := range entries {
			if ctx.Err() != nil {
				return "cancelled"
			}
			info, e := lstatAt(directory, entry.Name())
			if e != nil {
				continue
			}
			signatures = append(signatures, []any{entry.Name(), info.Mode(), info.Size(), info.ModTime().UnixNano()})
		}
		sort.Slice(signatures, func(i, j int) bool { return signatures[i].([]any)[0].(string) < signatures[j].([]any)[0].(string) })
		encoded, _ := json.Marshal([]any{fileID(info), info.ModTime().UnixNano(), info.Size(), truncated, signatures})
		return string(encoded)
	}
	return s.signature(ctx, path)
}

func (s *Service) establishWatchBaselines(ctx context.Context, clientID string, generation uint64) {
	s.mu.Lock()
	record, ok := s.watches[clientID]
	s.mu.Unlock()
	if !ok {
		return
	}
	baselines := map[string]string{}
	for _, path := range record.SignaturePaths {
		if _, exists := record.Signatures[path]; exists || s.pathNativelyCovered(path) {
			continue
		}
		baselines[path] = s.watchPathSignature(ctx, path, record)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current, ok := s.watches[clientID]
	if !ok || current.Generation != generation {
		return
	}
	for path, signature := range baselines {
		current.Signatures[path] = signature
	}
	s.watches[clientID] = current
}

// Native file-parent subscriptions must still filter events to subscribed files.
func (s *Service) eventDemandedLocked(path string) bool {
	for _, record := range s.watches {
		for _, root := range record.Roots {
			if path == root || recursiveWatchRoot(record, root) && indexRootWithin(root, path) || filepath.Dir(path) == root {
				return true
			}
		}
		for _, file := range append(append([]string{}, record.Files...), record.BackgroundFiles...) {
			if path == file || path == filepath.Dir(file) {
				return true
			}
		}
	}
	return false
}

func (s *Service) ReleaseWatchClient(clientID string) {
	if clientID == "" {
		return
	}
	s.mu.Lock()
	_, exists := s.watches[clientID]
	delete(s.watches, clientID)
	s.mu.Unlock()
	if exists {
		_ = s.reconfigureNative(context.Background(), false)
		s.notifyDemandChanged()
	}
}
