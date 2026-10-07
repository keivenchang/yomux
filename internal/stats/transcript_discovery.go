// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type transcriptDiscoveryBounds struct {
	maxFiles   int
	maxDirs    int
	maxBytes   int64
	maxEntries int
}

type transcriptDiscoveryDirectoryCursor struct {
	cookie       int64
	device       uint64
	inode        uint64
	modTimeNS    int64
	changeTimeNS int64
	pending      []string
	complete     bool
}

type transcriptDiscoveryCursorStore struct {
	mu                 sync.Mutex
	directories        map[string]transcriptDiscoveryDirectoryCursor
	frontiers          map[string][]string
	frontierOrder      []string
	deferNamesOverride func(string, []string, bool, transcriptDiscoveryDirectoryCursor) error
}

type transcriptPendingDirectory struct {
	path    string
	modTime time.Time
}

func newTranscriptDiscoveryCursorStore() *transcriptDiscoveryCursorStore {
	return &transcriptDiscoveryCursorStore{directories: make(map[string]transcriptDiscoveryDirectoryCursor), frontiers: make(map[string][]string)}
}

func (s *transcriptDiscoveryCursorStore) load(path string, info os.FileInfo) transcriptDiscoveryDirectoryCursor {
	device, inode := fileIdentity(info)
	changeTimeNS := codexDirectoryChangeTimeNS(info)
	s.mu.Lock()
	defer s.mu.Unlock()
	cursor, ok := s.directories[path]
	if !ok || cursor.device != device || cursor.inode != inode || cursor.modTimeNS != info.ModTime().UnixNano() || cursor.changeTimeNS != changeTimeNS {
		s.deleteDirectorySubtreeLocked(path)
		return transcriptDiscoveryDirectoryCursor{device: device, inode: inode, modTimeNS: info.ModTime().UnixNano(), changeTimeNS: changeTimeNS}
	}
	cursor.pending = append([]string(nil), cursor.pending...)
	return cursor
}

func (s *transcriptDiscoveryCursorStore) save(path string, cursor transcriptDiscoveryDirectoryCursor) {
	s.mu.Lock()
	defer s.mu.Unlock()
	cursor.pending = append([]string(nil), cursor.pending...)
	if cursor.cookie == 0 && len(cursor.pending) == 0 {
		delete(s.directories, path)
		return
	}
	s.directories[path] = cursor
}

func (s *transcriptDiscoveryCursorStore) deferNames(path string, names []string, complete bool, observed transcriptDiscoveryDirectoryCursor) error {
	if s.deferNamesOverride != nil {
		return s.deferNamesOverride(path, names, complete, observed)
	}
	return s.deferNamesDefault(path, names, complete, observed)
}

func (s *transcriptDiscoveryCursorStore) deferNamesDefault(path string, names []string, complete bool, observed transcriptDiscoveryDirectoryCursor) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	info, err := directory.Stat()
	if err != nil {
		return err
	}
	device, inode := fileIdentity(info)
	if device != observed.device || inode != observed.inode || info.ModTime().UnixNano() != observed.modTimeNS || codexDirectoryChangeTimeNS(info) != observed.changeTimeNS {
		// The names were read from a different directory version. Do not attach
		// that stale page to the replacement/current cursor or mark it complete;
		// the caller will resume the directory and enumerate the current version.
		s.save(path, transcriptDiscoveryDirectoryCursor{})
		return errTranscriptDiscoveryDirectoryChanged
	}
	cursor := s.load(path, info)
	cursor.pending = append(append([]string(nil), names...), cursor.pending...)
	cursor.complete = cursor.complete || complete
	s.save(path, cursor)
	return nil
}

func (s *transcriptDiscoveryCursorStore) loadFrontier(root string) []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	paths, ok := s.frontiers[root]
	if !ok {
		return nil
	}
	s.touchFrontierRootLocked(root)
	return append([]string(nil), paths...)
}

func (s *transcriptDiscoveryCursorStore) saveFrontier(root string, paths []string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(paths) == 0 {
		s.deleteFrontierRootLocked(root)
		return nil
	}
	if len(paths) > maxTranscriptDiscoveryFrontierPaths {
		return errTranscriptDiscoveryFrontierBudgetExceeded
	}
	if _, exists := s.frontiers[root]; !exists && len(s.frontierOrder) >= maxTranscriptDiscoveryFrontierRoots {
		s.deleteFrontierRootLocked(s.frontierOrder[0])
	}
	s.frontiers[root] = append([]string(nil), paths...)
	s.touchFrontierRootLocked(root)
	s.pruneDirectoryCursorsLocked()
	return nil
}

func (s *transcriptDiscoveryCursorStore) touchFrontierRootLocked(root string) {
	for index, current := range s.frontierOrder {
		if current == root {
			s.frontierOrder = append(s.frontierOrder[:index], s.frontierOrder[index+1:]...)
			break
		}
	}
	s.frontierOrder = append(s.frontierOrder, root)
}

func (s *transcriptDiscoveryCursorStore) deleteFrontierRootLocked(root string) {
	delete(s.frontiers, root)
	for index, current := range s.frontierOrder {
		if current == root {
			s.frontierOrder = append(s.frontierOrder[:index], s.frontierOrder[index+1:]...)
			break
		}
	}
	s.deleteDirectorySubtreeLocked(root)
}

func (s *transcriptDiscoveryCursorStore) deleteDirectorySubtreeLocked(root string) {
	for path := range s.directories {
		relative, err := filepath.Rel(root, path)
		if err == nil && (relative == "." || (relative != ".." && !strings.HasPrefix(relative, ".."+string(os.PathSeparator)))) {
			delete(s.directories, path)
		}
	}
}

func (s *transcriptDiscoveryCursorStore) discardDirectorySubtree(path string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.deleteDirectorySubtreeLocked(path)
}

func (s *transcriptDiscoveryCursorStore) pruneDirectoryCursorsLocked() {
	retained := make(map[string]struct{})
	for _, paths := range s.frontiers {
		for _, path := range paths {
			retained[path] = struct{}{}
		}
	}
	for path := range s.directories {
		if _, keep := retained[path]; !keep {
			delete(s.directories, path)
		}
	}
}

func prependTranscriptDiscoveryChild(queue []transcriptPendingDirectory, child transcriptPendingDirectory, maxFrontier int) ([]transcriptPendingDirectory, bool) {
	// Leave one slot for the parent directory that must be resumed after its
	// prioritized child; otherwise a deep tree could grow the saved frontier on
	// every descent despite a per-cycle directory limit.
	if maxFrontier < 2 || len(queue) >= maxFrontier-1 {
		return queue, false
	}
	return append([]transcriptPendingDirectory{child}, queue...), true
}

const defaultTranscriptDiscoveryEntries = 8192
const maxTranscriptDiscoveryFrontierPaths = 4096 // above the maximum depth of a valid POSIX path
const maxTranscriptDiscoveryFrontierRoots = maxAgentTokenSources

type transcriptCandidate struct {
	path    string
	modTime time.Time
}

// defaultAgentTokenSources remains the fixture-friendly entry point. Production
// configuration supplies the same bounds through AgentTokenCollectorConfig.
func defaultAgentTokenSources(ctx context.Context, records []AgentRecord, configuredRoots ...string) ([]TranscriptSource, error) {
	return defaultAgentTokenSourcesBounded(ctx, records, transcriptDiscoveryBounds{
		maxFiles: defaultTranscriptDiscoveryFiles,
		maxDirs:  defaultTranscriptDiscoveryDirs,
		maxBytes: defaultTranscriptDiscoveryBytes,
	}, configuredRoots...)
}

// recentTranscriptCandidates never reads transcript contents. It visits a
// bounded recent-first directory frontier, so an old Codex tree cannot turn
// token collection into an 11GB recursive walk.
func recentTranscriptCandidates(ctx context.Context, root string, bounds transcriptDiscoveryBounds) ([]transcriptCandidate, error) {
	return recentTranscriptCandidatesWithCursor(ctx, root, bounds, newTranscriptDiscoveryCursorStore())
}

func recentTranscriptCandidatesWithCursor(ctx context.Context, root string, bounds transcriptDiscoveryBounds, cursors *transcriptDiscoveryCursorStore) ([]transcriptCandidate, error) {
	if bounds.maxFiles <= 0 || bounds.maxDirs <= 0 || bounds.maxBytes <= 0 {
		return nil, errors.New("transcript-discovery-bound-invalid")
	}
	if cursors == nil {
		cursors = newTranscriptDiscoveryCursorStore()
	}
	if bounds.maxEntries <= 0 {
		bounds.maxEntries = defaultTranscriptDiscoveryEntries
	}
	root = filepath.Clean(root)
	queue := make([]transcriptPendingDirectory, 0, min(bounds.maxDirs, maxTranscriptDiscoveryFrontierPaths))
	for _, path := range cursors.loadFrontier(root) {
		info, err := os.Stat(path)
		if err == nil && info.IsDir() {
			queue = append(queue, transcriptPendingDirectory{path: path, modTime: info.ModTime()})
		} else {
			cursors.discardDirectorySubtree(path)
		}
	}
	if len(queue) == 0 {
		queue = append(queue, transcriptPendingDirectory{path: root})
	}
	saveFrontier := func(directories []transcriptPendingDirectory) error {
		paths := make([]string, 0, len(directories))
		for _, directory := range directories {
			paths = append(paths, directory.path)
		}
		return cursors.saveFrontier(root, paths)
	}
	candidates := make([]transcriptCandidate, 0, bounds.maxFiles)
	inspectedDirs, inspectedFiles, inspectedEntries := 0, 0, 0
	var inspectedBytes int64
	byteBudgetExceeded := false
	var discoveryErr error
	sortCandidates := func() {
		sort.Slice(candidates, func(i, j int) bool {
			if !candidates[i].modTime.Equal(candidates[j].modTime) {
				return candidates[i].modTime.After(candidates[j].modTime)
			}
			return candidates[i].path < candidates[j].path
		})
	}
	savePartial := func(discoveryErr error) ([]transcriptCandidate, error) {
		if saveErr := saveFrontier(queue); saveErr != nil {
			discoveryErr = errors.Join(discoveryErr, saveErr)
		}
		sortCandidates()
		return candidates, discoveryErr
	}
	for len(queue) > 0 {
		if err := ctx.Err(); err != nil {
			return savePartial(err)
		}
		if inspectedDirs >= bounds.maxDirs {
			return savePartial(errTranscriptDiscoveryDirectoryBudgetExhausted)
		}
		dir := queue[0]
		queue = queue[1:]
		inspectedDirs++
		if bounds.maxEntries-inspectedEntries <= 0 {
			queue = append([]transcriptPendingDirectory{dir}, queue...)
			return savePartial(errTranscriptDiscoveryEntryBudgetExhausted)
		}
		var directoryVersion transcriptDiscoveryDirectoryCursor
		entries, err := readTranscriptDirectoryBounded(ctx, dir.path, bounds.maxEntries-inspectedEntries, cursors, &directoryVersion)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryEntryBudgetExhausted) && !isTranscriptDiscoveryInterrupted(err) {
			if os.IsNotExist(err) {
				continue
			}
			queue = append([]transcriptPendingDirectory{dir}, queue...)
			return savePartial(err)
		}
		inspectedEntries += len(entries)
		readComplete := err == nil
		sort.Slice(entries, func(i, j int) bool { return entries[i].name > entries[j].name })
		childDirs := make([]transcriptPendingDirectory, 0)
		preserveChildren := func(names []string) (bool, bool, error) {
			sort.Slice(childDirs, func(i, j int) bool {
				if !childDirs[i].modTime.Equal(childDirs[j].modTime) {
					return childDirs[i].modTime.After(childDirs[j].modTime)
				}
				return childDirs[i].path < childDirs[j].path
			})
			queued := make(map[string]struct{}, len(queue))
			for _, pending := range queue {
				queued[pending.path] = struct{}{}
			}
			deferredChildren := false
			scheduledChild := false
			for _, child := range childDirs {
				if _, exists := queued[child.path]; exists {
					continue
				}
				if !scheduledChild {
					var scheduled bool
					queue, scheduled = prependTranscriptDiscoveryChild(queue, child, maxTranscriptDiscoveryFrontierPaths)
					if !scheduled {
						names = append(names, filepath.Base(child.path))
						deferredChildren = true
						continue
					}
					queued[child.path] = struct{}{}
					scheduledChild = true
					continue
				}
				names = append(names, filepath.Base(child.path))
				deferredChildren = true
			}
			if len(names) > 0 {
				if err := cursors.deferNames(dir.path, names, readComplete, directoryVersion); err != nil {
					// The directory reader may already have advanced its page cursor.
					// Restart this directory so an unpersisted continuation cannot skip
					// names when the parent is retried.
					cursors.save(dir.path, transcriptDiscoveryDirectoryCursor{})
					return deferredChildren, scheduledChild, err
				}
			}
			childDirs = nil
			return deferredChildren, scheduledChild, nil
		}
		resumeDirectory := func(scheduledChild bool) {
			if scheduledChild && len(queue) > 0 {
				queue = append(queue[:1], append([]transcriptPendingDirectory{dir}, queue[1:]...)...)
				return
			}
			queue = append([]transcriptPendingDirectory{dir}, queue...)
		}
		fileBudgetExceeded := false
		currentChildScheduled := false
		for entryIndex, entry := range entries {
			if err := ctx.Err(); err != nil {
				remaining := make([]string, 0, len(entries)-entryIndex)
				for _, pending := range entries[entryIndex:] {
					remaining = append(remaining, pending.name)
				}
				_, currentChildScheduled, deferErr := preserveChildren(remaining)
				if deferErr != nil {
					resumeDirectory(currentChildScheduled)
					return savePartial(deferErr)
				}
				resumeDirectory(currentChildScheduled)
				return savePartial(err)
			}
			if entry.err != nil {
				if discoveryErr == nil {
					discoveryErr = entry.err
				}
				continue
			}
			path := filepath.Join(dir.path, entry.name)
			info := entry.info
			if info.IsDir() {
				childDirs = append(childDirs, transcriptPendingDirectory{path: path, modTime: info.ModTime()})
				continue
			}
			if filepath.Ext(path) != ".jsonl" || inspectedFiles >= bounds.maxFiles {
				if inspectedFiles >= bounds.maxFiles {
					remaining := make([]string, 0, len(entries)-entryIndex)
					for _, pending := range entries[entryIndex:] {
						remaining = append(remaining, pending.name)
					}
					var deferErr error
					_, currentChildScheduled, deferErr = preserveChildren(remaining)
					if deferErr != nil {
						resumeDirectory(currentChildScheduled)
						return savePartial(deferErr)
					}
					fileBudgetExceeded = true
					break
				}
				continue
			}
			if info.Size() > bounds.maxBytes-inspectedBytes {
				// Keep the newest candidate even when its historical size exceeds
				// the discovery budget. ScanTranscript applies the per-cadence
				// budget to a bounded tail, so rejecting this path would hide a
				// source carrying current usage. Continue stat'ing other bounded
				// candidates so filename order cannot hide a newer transcript.
				inspectedFiles++
				candidates = append(candidates, transcriptCandidate{path: path, modTime: info.ModTime()})
				byteBudgetExceeded = true
				continue
			}
			inspectedFiles++
			inspectedBytes += info.Size()
			candidates = append(candidates, transcriptCandidate{path: path, modTime: info.ModTime()})
			if inspectedBytes >= bounds.maxBytes {
				byteBudgetExceeded = true
			}
		}
		deferredChildren, childScheduled, deferErr := preserveChildren(nil)
		if deferErr != nil {
			resumeDirectory(currentChildScheduled || childScheduled)
			return savePartial(deferErr)
		}
		resumeDir := deferredChildren
		resumeDirBeforeQueue := false
		if errors.Is(err, errTranscriptDiscoveryEntryBudgetExhausted) || fileBudgetExceeded || isTranscriptDiscoveryInterrupted(err) {
			resumeDir = true
			resumeDirBeforeQueue = true
		}
		if resumeDir {
			if resumeDirBeforeQueue {
				resumeDirectory(currentChildScheduled || childScheduled)
			} else {
				resumeDirectory(childScheduled)
			}
		}
		if errors.Is(err, errTranscriptDiscoveryEntryBudgetExhausted) || fileBudgetExceeded || isTranscriptDiscoveryInterrupted(err) {
			if fileBudgetExceeded {
				return savePartial(errTranscriptDiscoveryFileBudgetExhausted)
			}
			return savePartial(err)
		}
	}
	if err := saveFrontier(nil); err != nil {
		return candidates, err
	}
	sortCandidates()
	if discoveryErr != nil {
		return candidates, discoveryErr
	}
	if byteBudgetExceeded {
		return candidates, errors.New("transcript-discovery-byte-budget-exhausted")
	}
	return candidates, nil
}

var errTranscriptDiscoveryEntryBudgetExhausted = errors.New("transcript-discovery-entry-budget-exhausted")
var errTranscriptDiscoveryFileBudgetExhausted = errors.New("transcript-discovery-file-budget-exhausted")
var errTranscriptDiscoveryDirectoryBudgetExhausted = errors.New("transcript-discovery-directory-budget-exhausted")
var errTranscriptDiscoveryFrontierBudgetExceeded = errors.New("transcript-discovery-frontier-budget-exceeded")
var errTranscriptDiscoveryDirectoryChanged = errors.New("transcript-discovery-directory-changed-during-deferral")

func isTranscriptDiscoveryInterrupted(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
}

type transcriptDirectoryEntry struct {
	name string
	info os.FileInfo
	err  error
}

func readTranscriptDirectoryBounded(ctx context.Context, path string, maxEntries int, cursors *transcriptDiscoveryCursorStore, observed *transcriptDiscoveryDirectoryCursor) ([]transcriptDirectoryEntry, error) {
	if maxEntries <= 0 {
		return nil, errTranscriptDiscoveryEntryBudgetExhausted
	}
	directory, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer directory.Close()
	info, err := directory.Stat()
	if err != nil {
		return nil, err
	}
	cursor := cursors.load(path, info)
	if observed != nil {
		*observed = transcriptDiscoveryDirectoryCursor{
			device: cursor.device, inode: cursor.inode, modTimeNS: cursor.modTimeNS, changeTimeNS: cursor.changeTimeNS,
		}
	}

	entries := make([]transcriptDirectoryEntry, 0, min(maxEntries, 256))
	appendName := func(name string) {
		entryInfo, statErr := os.Lstat(filepath.Join(path, name))
		entries = append(entries, transcriptDirectoryEntry{name: name, info: entryInfo, err: statErr})
	}
	if cursor.complete && len(cursor.pending) == 0 {
		cursors.save(path, transcriptDiscoveryDirectoryCursor{})
		return entries, nil
	}
	for len(cursor.pending) > 0 {
		if err := ctx.Err(); err != nil {
			cursors.save(path, cursor)
			return entries, err
		}
		if len(entries) >= maxEntries {
			cursors.save(path, cursor)
			return entries, errTranscriptDiscoveryEntryBudgetExhausted
		}
		name := cursor.pending[0]
		cursor.pending = cursor.pending[1:]
		if name != "." && name != ".." {
			appendName(name)
		}
	}
	if cursor.complete {
		cursors.save(path, transcriptDiscoveryDirectoryCursor{})
		return entries, nil
	}
	for {
		if err := ctx.Err(); err != nil {
			cursors.save(path, cursor)
			return entries, err
		}
		previousCookie := cursor.cookie
		names, nextCookie, done, err := readCodexDirectoryPage(directory, previousCookie)
		if err != nil {
			cursors.save(path, cursor)
			return entries, err
		}
		sort.Strings(names)
		cursor.cookie = nextCookie
		cursor.complete = done
		for index, name := range names {
			if name == "." || name == ".." {
				continue
			}
			if err := ctx.Err(); err != nil {
				cursor.pending = append(cursor.pending, names[index:]...)
				cursors.save(path, cursor)
				return entries, err
			}
			if len(entries) >= maxEntries {
				cursor.pending = append(cursor.pending, names[index:]...)
				cursors.save(path, cursor)
				return entries, errTranscriptDiscoveryEntryBudgetExhausted
			}
			appendName(name)
		}
		if done {
			cursor.complete = true
			cursors.save(path, cursor)
			return entries, nil
		}
		if nextCookie == previousCookie && len(names) == 0 {
			cursors.save(path, cursor)
			return entries, errors.New("transcript-directory-page-did-not-advance")
		}
	}
}

// TranscriptPathDiscovery shares bounded filesystem traversal with token discovery,
// but neither reads transcript bodies nor assigns attribution.
type TranscriptPathDiscovery struct {
	mu      sync.Mutex
	cursors *transcriptDiscoveryCursorStore
}

func (d *TranscriptPathDiscovery) Next(ctx context.Context, root string, limit int) ([]string, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.cursors == nil {
		d.cursors = newTranscriptDiscoveryCursorStore()
	}
	candidates, err := recentTranscriptCandidatesWithCursor(ctx, root, transcriptDiscoveryBounds{maxFiles: limit, maxDirs: limit, maxEntries: limit, maxBytes: 1 << 62}, d.cursors)
	paths := make([]string, 0, len(candidates))
	for _, candidate := range candidates {
		paths = append(paths, candidate.path)
	}
	return paths, err
}
