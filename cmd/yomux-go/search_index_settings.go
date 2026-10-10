// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"log/slog"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"

	"github.com/keivenchang/yomux/internal/finder"
)

type finderIndexSettings struct {
	roots        []string
	excludeDirs  []string
	excludePaths []string
	maxFiles     int
	persistFiles int
	maxBytes     int64
	refreshSecs  int
	persist      bool
	revision     uint64
}

const maxSQLiteSettingsRevision = uint64(1<<63 - 1)

func finderIndexSettingsRevision(digest [32]byte) uint64 {
	return binary.BigEndian.Uint64(digest[:8]) & maxSQLiteSettingsRevision
}

func (s *server) updateFinderIndexSettings(payload map[string]any) ([]string, bool, error) {
	settings, _ := payload["settings"].(map[string]any)
	fileExplorer, _ := settings["file_explorer"].(map[string]any)
	if fileExplorer == nil {
		return nil, false, nil
	}
	current := finderIndexSettings{maxFiles: finder.DefaultIndexedFiles, persistFiles: finder.DefaultIndexedFiles, maxBytes: finder.DefaultIndexBytes, refreshSecs: 60, persist: true}
	current.excludeDirs = stringSettings(fileExplorer["index_exclude_dir_names"])
	current.excludePaths = stringSettings(fileExplorer["index_exclude_paths"])
	if roots, ok := fileExplorer["indexed_dirs"].([]any); ok {
		for _, value := range roots {
			root, ok := value.(string)
			if !ok || strings.TrimSpace(root) == "" {
				continue
			}
			absolute, err := filepath.Abs(root)
			if err == nil {
				current.roots = append(current.roots, filepath.Clean(absolute))
			}
		}
	}
	if value, ok := fileExplorer["index_max_files"].(float64); ok && value >= 1000 && value <= finder.DefaultIndexedFiles {
		current.maxFiles = int(value)
	}
	if value, ok := fileExplorer["index_persist_max_files"].(float64); ok && value >= 1000 && value <= finder.DefaultIndexedFiles {
		current.persistFiles = int(value)
	}
	if value, ok := fileExplorer["index_persist"].(bool); ok {
		current.persist = value
	}
	if value, ok := fileExplorer["index_persist_max_mb"].(float64); ok && value >= 1 && value <= float64(finder.MaxIndexBytes/(1024*1024)) {
		current.maxBytes = int64(value) * 1024 * 1024
	}
	if value, ok := fileExplorer["index_refresh_seconds"].(float64); ok && value >= 0 && value <= 3600 {
		current.refreshSecs = int(value)
	}
	revisionPayload, err := json.Marshal(struct {
		Roots        []string `json:"roots"`
		ExcludeDirs  []string `json:"exclude_dirs"`
		ExcludePaths []string `json:"exclude_paths"`
		MaxFiles     int      `json:"max_files"`
		PersistFiles int      `json:"persist_files"`
		MaxBytes     int64    `json:"max_bytes"`
		RefreshSecs  int      `json:"refresh_secs"`
		Persist      bool     `json:"persist"`
	}{current.roots, current.excludeDirs, current.excludePaths, current.maxFiles, current.persistFiles, current.maxBytes, current.refreshSecs, current.persist})
	if err != nil {
		return nil, false, err
	}
	revisionDigest := sha256.Sum256(revisionPayload)
	current.revision = finderIndexSettingsRevision(revisionDigest)
	s.indexSettingsMu.Lock()
	previous := s.indexSettings
	previousRoots := append([]string(nil), previous.roots...)
	changed := !(reflect.DeepEqual(current.roots, s.indexSettings.roots) && reflect.DeepEqual(current.excludeDirs, s.indexSettings.excludeDirs) && reflect.DeepEqual(current.excludePaths, s.indexSettings.excludePaths) && current.maxFiles == s.indexSettings.maxFiles && current.persistFiles == s.indexSettings.persistFiles && current.maxBytes == s.indexSettings.maxBytes && current.refreshSecs == s.indexSettings.refreshSecs && current.persist == s.indexSettings.persist)
	preserveSnapshots := previous.revision != 0 && reflect.DeepEqual(current.roots, previous.roots) && reflect.DeepEqual(current.excludeDirs, previous.excludeDirs) && reflect.DeepEqual(current.excludePaths, previous.excludePaths) && current.persist == previous.persist
	s.indexSettings = current
	if changed && s.searchIndex != nil {
		if s.indexSettingsFencePending {
			s.indexSettingsFencePreserveSnapshots = s.indexSettingsFencePreserveSnapshots && preserveSnapshots
		} else {
			s.indexSettingsFencePreserveSnapshots = preserveSnapshots
		}
		s.indexSettingsFencePending = true
		s.indexSettingsFenceRoots = uniqueFinderIndexRoots(append(append(s.indexSettingsFenceRoots, previousRoots...), current.roots...))
	}
	s.indexSettingsMu.Unlock()
	if s.searchIndex != nil {
		if err := s.searchIndex.SetPersistent(current.persist); err != nil {
			return previousRoots, changed, err
		}
	}
	return previousRoots, changed, nil
}

func uniqueFinderIndexRoots(roots []string) []string {
	seen := make(map[string]struct{}, len(roots))
	unique := make([]string, 0, len(roots))
	for _, root := range roots {
		if _, exists := seen[root]; exists {
			continue
		}
		seen[root] = struct{}{}
		unique = append(unique, root)
	}
	return unique
}

func stringSettings(raw any) []string {
	values, _ := raw.([]any)
	result := make([]string, 0, len(values))
	for _, value := range values {
		if text, ok := value.(string); ok && strings.TrimSpace(text) != "" {
			result = append(result, strings.TrimSpace(text))
		}
	}
	return result
}

func (s *server) loadFinderIndexSettings() error {
	script := `import json
from yomux_lib.workspace.settings import settings_payload
print(json.dumps(settings_payload(), separators=(",", ":"), ensure_ascii=False), end="")`
	command := exec.CommandContext(context.Background(), "python3", "-c", script)
	command.Dir = s.root
	command.Env = pythonBridgeEnvironment(s.root, s.config)
	output, err := command.Output()
	if err != nil {
		return err
	}
	var payload map[string]any
	if err := json.Unmarshal(output, &payload); err != nil {
		return err
	}
	_, _, err = s.updateFinderIndexSettings(payload)
	if err != nil {
		return err
	}
	s.indexSettingsMu.RLock()
	roots := append([]string(nil), s.indexSettings.roots...)
	revision := s.indexSettings.revision
	s.indexSettingsMu.RUnlock()
	if s.searchIndex != nil {
		if err := s.searchIndex.SyncSettings(context.Background(), roots, revision); err != nil {
			return err
		}
		s.indexSettingsMu.Lock()
		s.indexSettingsFencePending = false
		s.indexSettingsFenceRoots = nil
		s.indexSettingsMu.Unlock()
	}
	for _, root := range roots {
		options := s.finderSearchOptions(root, "", false)
		if _, finderErr := s.ensureFinderIndex(context.Background(), root, options); finderErr != nil {
			slog.Warn("Finder index startup refresh could not be scheduled", "root", root, "error", finderErr.Message)
		}
	}
	return nil
}

func (s *server) finderSearchOptions(root, cursor string, indexedOnly bool) finder.SearchOptions {
	options := finder.SearchOptions{Cursor: cursor, IndexedOnly: indexedOnly}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return options
	}
	absolute = filepath.Clean(absolute)
	s.indexSettingsMu.RLock()
	settings := s.indexSettings
	s.indexSettingsMu.RUnlock()
	for _, configured := range settings.roots {
		relative, relErr := filepath.Rel(configured, absolute)
		if relErr == nil && (relative == "." || (relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)))) {
			options.Indexed = true
			options.MaxFiles = min(settings.maxFiles, settings.persistFiles)
			options.MaxBytes = settings.maxBytes
			options.RefreshSec = settings.refreshSecs
			options.ExcludeDirs = append([]string(nil), settings.excludeDirs...)
			options.ExcludePaths = append([]string(nil), settings.excludePaths...)
			options.SettingsRevision = settings.revision
			break
		}
	}
	return options
}
