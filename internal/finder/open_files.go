// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// The watch service owns these descriptors independently of hot content polling.
// Entries are keyed by tracking ID, so two distinct aliases never merge by inode.
type openFileRecord struct {
	id       string
	path     string
	origin   string
	scope    string
	fileID   string
	revision uint64
	file     *os.File
}

func (s *Service) registerOpenFilesLocked(value any) (map[string]string, *Error) {
	refs := map[string]string{}
	if value == nil {
		return refs, nil
	}
	rows, ok := watchAnySlice(value)
	if !ok || len(rows) > maxActiveWatchFiles {
		return nil, invalid("", "open files must be a bounded list")
	}
	staged := map[string]*openFileRecord{}
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
			if len(s.openFiles)+len(staged) >= maxActiveWatchFiles {
				file.Close()
				return nil, &Error{Status: http.StatusTooManyRequests, Code: "watch_capacity_exceeded", MessageKey: "fs.error.watchCapacity", Retryable: true, Message: "open file capacity exceeded"}
			}
			s.openFileSequence++
			entry = &openFileRecord{id: fmt.Sprintf("%s-open-%d", s.epoch, s.openFileSequence), path: requested, origin: requested, scope: filepath.Dir(resolved), fileID: expected, revision: 1, file: file}
			staged[entry.id] = entry
		}
		refs[entry.id] = path
	}
	for id, entry := range staged {
		s.openFiles[id] = entry
	}
	success = true
	return refs, nil
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
		if directory && (entry.scope == path || filepath.Dir(registered) == path || filepath.Dir(entry.origin) == path) {
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
	s.openFileMutationMu.Lock()
	defer s.openFileMutationMu.Unlock()
	s.mu.Lock()
	scopes := make([]string, 0)
	for _, record := range s.watches {
		scopes = append(scopes, record.Roots...)
	}
	for _, entry := range s.openFiles {
		scopes = append(scopes, entry.scope)
	}
	events := make([]map[string]any, 0)
	for _, entry := range s.openFiles {
		// A surviving hardlink/symlink alias remains its own document path.
		current, _, _, currentErr := s.openAny(entry.path)
		if currentErr == nil {
			info, err := current.Stat()
			current.Close()
			if err == nil && fileID(info) == entry.fileID {
				continue
			}
		}
		destination, err := resolvedDescriptorPath(entry.file)
		if err != nil || destination == entry.path || strings.HasPrefix(filepath.Base(destination), ".yolomux-mutation-") || !filepath.IsAbs(destination) || !openFilePathInScopes(destination, scopes) {
			continue
		}
		target, requested, _, openErr := s.openAny(destination)
		if openErr != nil {
			continue
		}
		info, err := target.Stat()
		target.Close()
		if err != nil || fileID(info) != entry.fileID {
			continue
		}
		old := entry.path
		entry.path = requested
		// The verified destination becomes this open document's parent scope,
		// independently of whether Finder remains visible afterward.
		entry.scope = filepath.Dir(requested)
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

func openFilePathInScopes(path string, scopes []string) bool {
	for _, scope := range scopes {
		if path == scope || strings.HasPrefix(path, strings.TrimRight(scope, string(os.PathSeparator))+string(os.PathSeparator)) {
			return true
		}
	}
	return false
}

// Rotate from the newly written descriptor before publishing content changes. The
// old inode can already be unlinked, so reopening by pathname would introduce a race.
func (s *Service) rotateOpenFileDescriptors(path string, previous os.FileInfo, written *os.File) error {
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
		if entry.path != path || entry.fileID != fileID(previous) {
			continue
		}
		replacement, err := copyDescriptor(written)
		if err != nil {
			return err
		}
		entry.file.Close()
		entry.file = replacement
		entry.fileID = fileID(info)
	}
	return nil
}
