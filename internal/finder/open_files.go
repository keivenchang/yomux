// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type openFileScope struct {
	path     string
	resolved string
}

// The watch service owns these descriptors independently of hot content polling.
// Entries are keyed by tracking ID, so two distinct aliases never merge by inode.
type openFileRecord struct {
	id            string
	path          string
	origin        string
	scope         openFileScope
	fileID        string
	revision      uint64
	file          *os.File
	pathAmbiguous bool
}

func (s *Service) registerOpenFilesLocked(clientID string, value any) (map[string]string, *Error) {
	refs := map[string]string{}
	if value == nil {
		return refs, nil
	}
	rows, ok := watchAnySlice(value)
	if !ok || len(rows) > maxActiveWatchFiles {
		return nil, invalid("", "open files must be a bounded list")
	}
	staged := map[string]*openFileRecord{}
	// Capacity belongs to the replacement registration and surviving clients.
	// Keep outgoing descriptors until validation succeeds so rejection is atomic.
	retained := map[string]bool{}
	for id, record := range s.watches {
		if id != clientID {
			for trackingID := range record.OpenFiles {
				retained[trackingID] = true
			}
		}
	}
	success := false
	defer func() {
		if !success {
			for _, entry := range staged {
				entry.file.Close()
			}
		}
	}()
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			return nil, invalid("", "open file must be an object")
		}
		path, _ := row["path"].(string)
		expected, _ := row["file_id"].(string)
		trackingID, _ := row["tracking_id"].(string)
		if !filepath.IsAbs(path) || expected == "" {
			return nil, invalid(path, "open file requires absolute path and file identity")
		}
		path = filepath.Clean(path)
		var entry *openFileRecord
		if candidate := s.openFiles[trackingID]; candidate != nil && (expected == candidate.fileID || path == candidate.path || path == candidate.origin || s.openFileRegisteredPathLocked(candidate.id, path)) {
			// A retained ID is authoritative across atomic saves, which change inode identity.
			entry = candidate
		}
		if entry == nil {
			for _, candidate := range s.openFiles {
				if candidate.fileID == expected && (candidate.path == path || candidate.origin == path) {
					entry = candidate
					break
				}
			}
		}
		if entry == nil {
			for _, candidate := range staged {
				if candidate.path == path && candidate.fileID == expected {
					entry = candidate
					break
				}
			}
		}
		if entry == nil {
			file, requested, resolved, openErr := s.openAny(path)
			if openErr != nil {
				// Reconnect after restart never guesses the destination of a missing file.
				if openErr.Status == http.StatusNotFound {
					continue
				}
				return nil, openErr
			}
			info, err := file.Stat()
			if err != nil {
				file.Close()
				return nil, filesystemError(path, err)
			}
			if !info.Mode().IsRegular() || fileID(info) != expected {
				file.Close()
				continue
			}
			s.openFileSequence++
			entry = &openFileRecord{id: fmt.Sprintf("%s-open-%d", s.epoch, s.openFileSequence), path: requested, origin: requested,
				scope: openFileScope{path: filepath.Dir(requested), resolved: filepath.Dir(resolved)}, fileID: expected, revision: 1, file: file}
			staged[entry.id] = entry
		}
		info, statErr := entry.file.Stat()
		if statErr != nil {
			return nil, filesystemError(path, statErr)
		}
		retained[entry.id] = true
		if len(retained) > maxActiveWatchFiles {
			return nil, &Error{Status: http.StatusTooManyRequests, Code: "watch_capacity_exceeded", MessageKey: "fs.error.watchCapacity", Retryable: true, Message: "open file capacity exceeded"}
		}
		s.observeOpenFileIdentityLocked(info, staged)
		refs[entry.id] = path
	}
	for id, entry := range staged {
		s.openFiles[id] = entry
	}
	success = true
	return refs, nil
}

func (s *Service) observeOpenFileIdentityLocked(info os.FileInfo, staged map[string]*openFileRecord) {
	if !descriptorPathMayAlias(info) {
		return
	}
	identity := fileID(info)
	// Path ambiguity belongs to the inode, including aliases admitted in this batch.
	for _, records := range []map[string]*openFileRecord{s.openFiles, staged} {
		for _, entry := range records {
			if entry.fileID == identity {
				entry.pathAmbiguous = true
			}
		}
	}
}

func (s *Service) openFileRegisteredPathLocked(id, path string) bool {
	for _, record := range s.watches {
		if record.OpenFiles[id] == path {
			return true
		}
	}
	return false
}

// A hidden Finder can drop its roots while an opened document retains the
// original parent scope. Missing paths are omitted from polling, never authorized.
func (s *Service) retainedOpenFileWatchPath(clientID, raw string, directory bool) bool {
	path := filepath.Clean(raw)
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, registered := range s.watches[clientID].OpenFiles {
		entry := s.openFiles[id]
		if entry == nil {
			continue
		}
		if !directory && registered == path {
			return true
		}
		if directory && (entry.scope.path == path || entry.scope.resolved == path || filepath.Dir(registered) == path || filepath.Dir(entry.origin) == path) {
			return true
		}
	}
	return false
}

func (s *Service) openFileSnapshotLocked(refs map[string]string) []map[string]any {
	rows := make([]map[string]any, 0, len(refs))
	for id, oldPath := range refs {
		if entry := s.openFiles[id]; entry != nil {
			rows = append(rows, map[string]any{"tracking_id": id, "revision": entry.revision, "old_path": oldPath, "path": entry.path, "file_id": entry.fileID})
		}
	}
	return rows
}

func (s *Service) releaseUnusedOpenFilesLocked() {
	used := map[string]bool{}
	for _, record := range s.watches {
		for id := range record.OpenFiles {
			used[id] = true
		}
	}
	for id, entry := range s.openFiles {
		if !used[id] {
			entry.file.Close()
			delete(s.openFiles, id)
		}
	}
}

func (s *Service) reconcileOpenFiles() {
	// A later poll observes the committed namespace; never follow an in-flight
	// atomic write or a rename whose Git publication can still roll back.
	if !s.openFileMutationMu.TryLock() {
		return
	}
	defer s.openFileMutationMu.Unlock()
	s.reconcileOpenFilesWithinMutation(nil)
}

// The caller holds openFileMutationMu through namespace commit or rollback.
func (s *Service) reconcileOpenFilesWithinMutation(committed *preparedRename) {
	s.mu.Lock()
	scopes := make([]openFileScope, 0)
	for _, record := range s.watches {
		scopes = append(scopes, record.RootScopes...)
	}
	for _, entry := range s.openFiles {
		scopes = append(scopes, entry.scope)
	}
	events := make([]map[string]any, 0)
	for _, entry := range s.openFiles {
		destination := committedOpenFileDestination(entry, committed)
		knownMove := destination != ""
		// A surviving hardlink/symlink alias remains its own document path.
		current, _, _, currentErr := s.openAny(entry.path)
		if currentErr == nil {
			info, err := current.Stat()
			current.Close()
			if err == nil && fileID(info) == entry.fileID {
				s.observeOpenFileIdentityLocked(info, nil)
				if !knownMove {
					continue
				}
			}
		}
		descriptorInfo, statErr := entry.file.Stat()
		if statErr != nil {
			continue
		}
		// Link-count drops cannot prove which name an ambiguous descriptor opened.
		s.observeOpenFileIdentityLocked(descriptorInfo, nil)
		if entry.pathAmbiguous && !knownMove {
			continue
		}
		var err error
		if !knownMove {
			destination, err = resolvedDescriptorPath(entry.file)
		}
		// Directory mutations quarantine every descendant under a private
		// ancestor; none of those intermediate names is a document relocation.
		if err != nil || destination == entry.path || strings.Contains(destination, string(filepath.Separator)+".yolomux-mutation-") || !filepath.IsAbs(destination) {
			continue
		}
		relocationScopes := scopes
		if knownMove {
			// The committed move authorizes this exact destination even when
			// disconnect retired broader roots. Lift the document's namespace
			// only for this mapping; reopening below rejects retargeted aliases.
			scope := entry.scope
			for !indexRootWithin(scope.resolved, destination) {
				scope.path = filepath.Dir(scope.path)
				scope.resolved = filepath.Dir(scope.resolved)
			}
			ancestor, _, resolved, ancestorErr := s.openAny(scope.path)
			if ancestorErr == nil {
				ancestor.Close()
			}
			if ancestorErr != nil || resolved != scope.resolved {
				// A source-directory alias need not expose the destination's
				// sibling namespace. Use the explicitly authorized target.
				scope = openFileScope{path: filepath.Dir(committed.target.requested), resolved: committed.target.resolvedParent}
			}
			relocationScopes = append([]openFileScope{scope}, scopes...)
		}
		mapped := openFileRelocationPath(entry, destination, relocationScopes)
		if mapped == "" || mapped == entry.path {
			continue
		}
		target, requested, resolved, openErr := s.openAny(mapped)
		if openErr != nil {
			continue
		}
		info, err := target.Stat()
		target.Close()
		if err != nil || !s.verifiedOpenFileRelocationLocked(entry, destination, resolved, info, knownMove) {
			continue
		}
		old := entry.path
		entry.path = requested
		// The verified destination becomes this open document's parent scope,
		// independently of whether Finder remains visible afterward.
		entry.scope = openFileScope{path: filepath.Dir(requested), resolved: filepath.Dir(resolved)}
		entry.revision++
		events = append(events, map[string]any{"tracking_id": entry.id, "revision": entry.revision, "old_path": old, "new_path": requested, "file_id": entry.fileID})
	}
	s.mu.Unlock()
	for _, event := range events {
		if s.publish != nil {
			if err := s.publish("file_relocated", event); err != nil {
				slog.Error("file relocation publication failed; registration snapshot retained", "tracking_id", event["tracking_id"], "error", err)
			}
		}
	}
}

func committedOpenFileDestination(entry *openFileRecord, committed *preparedRename) string {
	if committed == nil {
		return ""
	}
	source := filepath.Join(committed.source.resolvedParent, committed.source.name)
	previous := filepath.Join(entry.scope.resolved, filepath.Base(entry.path))
	if !indexRootWithin(source, previous) {
		return ""
	}
	relative, err := filepath.Rel(source, previous)
	if err != nil {
		return ""
	}
	// The completed namespace transaction identifies the moved name even when
	// Darwin's descriptor path cache can name a different hardlink instead.
	return filepath.Join(committed.target.resolvedParent, committed.target.name, relative)
}

func (s *Service) verifiedOpenFileRelocationLocked(entry *openFileRecord, destination, resolved string, info os.FileInfo, knownMove bool) bool {
	if resolved != destination || fileID(info) != entry.fileID {
		return false
	}
	s.observeOpenFileIdentityLocked(info, nil)
	return knownMove || !entry.pathAmbiguous
}

func openFileRelocationPath(entry *openFileRecord, destination string, scopes []openFileScope) string {
	candidates := append([]openFileScope{entry.scope}, scopes...)
	// Prefer the document's requested namespace over another client's alias of the
	// same directory. Reopening the mapped path below rejects a retargeted alias.
	sort.Slice(candidates, func(i, j int) bool {
		left, right := candidates[i], candidates[j]
		leftOwns := indexRootWithin(left.path, entry.path)
		rightOwns := indexRootWithin(right.path, entry.path)
		if leftOwns != rightOwns {
			return leftOwns
		}
		if len(left.path) != len(right.path) {
			return len(left.path) > len(right.path)
		}
		return left.path < right.path
	})
	for _, scope := range candidates {
		if scope.path == "" || scope.resolved == "" || !indexRootWithin(scope.resolved, destination) {
			continue
		}
		relative, err := filepath.Rel(scope.resolved, destination)
		if err == nil {
			return filepath.Join(scope.path, relative)
		}
	}
	return ""
}

// Rotate from the newly written descriptor before publishing content changes. The
// old inode can already be unlinked, so reopening by pathname would introduce a race.
func (s *Service) rotateOpenFileDescriptors(path, resolved string, previous os.FileInfo, written *os.File) error {
	if previous == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	info, err := written.Stat()
	if err != nil {
		return err
	}
	for _, entry := range s.openFiles {
		if entry.fileID != fileID(previous) {
			continue
		}
		if entry.path != path {
			alias, _, aliasResolved, aliasErr := s.openAny(entry.path)
			if aliasErr != nil {
				continue
			}
			aliasInfo, statErr := alias.Stat()
			alias.Close()
			if statErr != nil || aliasResolved != resolved || fileID(aliasInfo) != fileID(info) {
				continue
			}
		}
		replacement, err := copyDescriptor(written)
		if err != nil {
			return err
		}
		entry.file.Close()
		entry.file = replacement
		entry.fileID = fileID(info)
		entry.pathAmbiguous = false
	}
	s.observeOpenFileIdentityLocked(info, nil)
	return nil
}
