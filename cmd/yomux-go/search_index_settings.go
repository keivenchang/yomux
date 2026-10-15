// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
)

type finderIndexSettings struct {
	roots                 []string
	excludeDirs           []string
	excludePaths          []string
	maxFiles              int
	maxBytes              int64
	refreshSecs           int
	backgroundRefreshSecs int
	persist               bool
	revision              uint64
	legacyRevision        uint64
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
	current := finderIndexSettings{maxFiles: finder.DefaultIndexedFiles, maxBytes: finder.DefaultIndexBytes, refreshSecs: 60, backgroundRefreshSecs: 900, persist: true}
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
	if value, ok := fileExplorer["index_persist"].(bool); ok {
		current.persist = value
	}
	if value, ok := fileExplorer["index_persist_max_mb"].(float64); ok && value >= 1 && value <= float64(finder.MaxIndexBytes/(1024*1024)) {
		current.maxBytes = int64(value) * 1024 * 1024
	}
	if value, ok := fileExplorer["index_refresh_seconds"].(float64); ok && value >= 0 && value <= 3600 {
		current.refreshSecs = int(value)
	}
	if value, ok := fileExplorer["index_background_refresh_seconds"].(float64); ok && value >= 0 && value <= 86400 {
		current.backgroundRefreshSecs = int(value)
	}
	current.roots = uniqueFinderIndexRoots(current.roots)
	sort.Strings(current.roots)
	revisionPayload, err := json.Marshal(struct {
		Roots        []string `json:"roots"`
		ExcludeDirs  []string `json:"exclude_dirs"`
		ExcludePaths []string `json:"exclude_paths"`
		MaxFiles     int      `json:"max_files"`
		MaxBytes     int64    `json:"max_bytes"`
		RefreshSecs  int      `json:"refresh_secs"`
		Persist      bool     `json:"persist"`
	}{current.roots, current.excludeDirs, current.excludePaths, current.maxFiles, current.maxBytes, 60, current.persist})
	if err != nil {
		return nil, false, err
	}
	revisionDigest := sha256.Sum256(revisionPayload)
	current.revision = finderIndexSettingsRevision(revisionDigest)
	revisionPayload, err = json.Marshal(struct {
		Roots        []string `json:"roots"`
		ExcludeDirs  []string `json:"exclude_dirs"`
		ExcludePaths []string `json:"exclude_paths"`
		MaxFiles     int      `json:"max_files"`
		MaxBytes     int64    `json:"max_bytes"`
		RefreshSecs  int      `json:"refresh_secs"`
		Persist      bool     `json:"persist"`
	}{current.roots, current.excludeDirs, current.excludePaths, current.maxFiles, current.maxBytes, current.refreshSecs, current.persist})
	if err != nil {
		return nil, false, err
	}
	current.legacyRevision = finderIndexSettingsRevision(sha256.Sum256(revisionPayload))
	s.indexSettingsMu.Lock()
	previous := s.indexSettings
	previousRoots := append([]string(nil), previous.roots...)
	changed := !(reflect.DeepEqual(current.roots, s.indexSettings.roots) && reflect.DeepEqual(current.excludeDirs, s.indexSettings.excludeDirs) && reflect.DeepEqual(current.excludePaths, s.indexSettings.excludePaths) && current.maxFiles == s.indexSettings.maxFiles && current.maxBytes == s.indexSettings.maxBytes && current.refreshSecs == s.indexSettings.refreshSecs && current.backgroundRefreshSecs == s.indexSettings.backgroundRefreshSecs && current.persist == s.indexSettings.persist)
	preserveSnapshots := previous.revision != 0 && reflect.DeepEqual(current.roots, previous.roots) && reflect.DeepEqual(current.excludeDirs, previous.excludeDirs) && reflect.DeepEqual(current.excludePaths, previous.excludePaths) && current.persist == previous.persist
	if changed {
		s.indexApplicationEpoch++
	}
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
	select {
	case s.indexRefreshWake <- struct{}{}:
	default:
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

// All settings entry points hold indexSettingsUpdateMu from YAML access through worker acknowledgment.
func (s *server) loadFinderIndexSettings() error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	return s.reloadFinderIndexSettings(ctx, false)
}

func (s *server) finderIndexFileDigest() ([32]byte, error) {
	digest, _, err := s.finderIndexFileSnapshot()
	return digest, err
}

func (s *server) finderIndexFileSnapshot() ([32]byte, float64, error) {
	file, err := os.Open(filepath.Join(s.config, "settings.yaml"))
	if err != nil {
		return [32]byte{}, 0, err
	}
	defer file.Close()
	before, err := file.Stat()
	if err != nil {
		return [32]byte{}, 0, err
	}
	data, err := io.ReadAll(io.LimitReader(file, 1<<20+1))
	if err != nil {
		return [32]byte{}, 0, err
	}
	if len(data) > 1<<20 {
		return [32]byte{}, 0, errors.New("settings YAML exceeds 1 MiB")
	}
	after, err := file.Stat()
	if err != nil {
		return [32]byte{}, 0, err
	}
	if before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return [32]byte{}, 0, errors.New("settings changed during content check")
	}
	return sha256.Sum256(data), float64(after.ModTime().UnixNano()), nil
}

func (s *server) reloadFinderIndexSettings(ctx context.Context, onlyChanged bool) error {
	if err := s.indexSettingsUpdateMu.LockContext(ctx, nil); err != nil {
		return err
	}
	defer s.indexSettingsUpdateMu.Unlock()
	digest, digestErr := s.finderIndexFileDigest()
	if onlyChanged && digestErr == nil && digest == s.indexSettingsFileDigest {
		return s.synchronizeFinderIndexSettings(ctx)
	}
	script := `import json
from yomux_lib.workspace.settings import settings_payload
print(json.dumps(settings_payload(), separators=(",", ":"), ensure_ascii=False), end="")`
	command := exec.CommandContext(ctx, "python3", "-c", script)
	command.Dir = s.root
	command.Env = pythonBridgeEnvironment(s.root, s.config)
	output, err := command.Output()
	var payload map[string]any
	if err == nil {
		err = json.Unmarshal(output, &payload)
	}
	if err == nil {
		if message, _ := payload["error"].(string); message != "" {
			err = errors.New(message)
		}
	}
	if err != nil {
		s.indexSettingsMu.Lock()
		s.indexSettingsError = err.Error()
		s.indexSettingsMu.Unlock()
		return err
	}
	after, afterErr := s.finderIndexFileDigest()
	if digestErr == nil && (afterErr != nil || after != digest) {
		return errors.New("settings changed while being read; reconciliation will retry")
	}
	return s.applyFinderIndexSettings(ctx, payload)
}

func (s *server) applyFinderIndexSettings(ctx context.Context, payload map[string]any) error {
	digest, mtime, err := s.finderIndexFileSnapshot()
	if err != nil {
		return err
	}
	if payloadMtime, ok := payload["mtime_ns"].(float64); ok && payloadMtime != mtime {
		return errors.New("settings changed before policy application")
	}
	if _, _, err := s.updateFinderIndexSettings(payload); err != nil {
		return err
	}
	// The digest belongs to the same opened inode and timestamp as the parsed
	// payload. A later external replacement remains visible to the next loop.
	s.indexSettingsFileDigest = digest
	return s.synchronizeFinderIndexSettings(ctx)
}

func (s *server) synchronizeFinderIndexSettings(ctx context.Context) error {
	if s.searchIndex == nil {
		return nil
	}
	s.indexSettingsMu.RLock()
	configured, epoch, pending, preserve := s.indexSettings, s.indexApplicationEpoch, s.indexSettingsFencePending, s.indexSettingsFencePreserveSnapshots
	s.indexSettingsMu.RUnlock()
	// Re-acknowledge even unchanged settings: the worker may have restarted since the last request.
	ack, err := s.searchIndex.ApplySettings(ctx, configured.roots, configured.revision, epoch, configured.persist, configured.maxBytes, preserve || !pending, finder.SearchOptions{MaxFiles: configured.maxFiles, RefreshSec: configured.refreshSecs, ExcludeDirs: configured.excludeDirs, ExcludePaths: configured.excludePaths, LegacySettingsRevision: configured.legacyRevision})
	s.indexSettingsMu.Lock()
	if err != nil {
		s.indexSettingsError = err.Error()
		s.indexSettingsFencePending = true
		s.indexSettingsMu.Unlock()
		return err
	}
	if epoch != s.indexApplicationEpoch || ack["application_epoch"] != strconv.FormatUint(epoch, 10) || ack["revision"] != strconv.FormatUint(configured.revision, 10) {
		s.indexSettingsError = "stale Finder settings acknowledgment"
		s.indexSettingsMu.Unlock()
		return errors.New("stale Finder settings acknowledgment")
	}
	incarnation, _ := ack["worker_incarnation"].(string)
	workerChanged := incarnation != s.indexWorkerIncarnation
	s.indexActiveSettings, s.indexWorkerIncarnation = configured, incarnation
	s.indexSettingsFencePending, s.indexSettingsFencePreserveSnapshots = false, false
	s.indexSettingsFenceRoots, s.indexSettingsError = nil, ""
	s.indexSettingsMu.Unlock()
	if pending || workerChanged {
		select {
		case s.indexRefreshWake <- struct{}{}:
		default:
		}
	}
	return nil
}

func (s *server) finderIndexSettingsStatus() map[string]any {
	s.indexSettingsMu.RLock()
	defer s.indexSettingsMu.RUnlock()
	state := "active"
	if s.indexSettingsFencePending {
		state = "pending"
	}
	if s.indexSettingsError != "" {
		state = "error"
	}
	return map[string]any{"configured_revision": strconv.FormatUint(s.indexSettings.revision, 10), "active_revision": strconv.FormatUint(s.indexActiveSettings.revision, 10), "application_epoch": strconv.FormatUint(s.indexApplicationEpoch, 10), "worker_incarnation": s.indexWorkerIncarnation, "synchronization_state": state, "synchronization_error": s.indexSettingsError, "configured_budget_bytes": s.indexSettings.maxBytes}
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
	active := s.indexActiveSettings
	pendingUnsafe := s.indexSettingsFencePending && !s.indexSettingsFencePreserveSnapshots
	s.indexSettingsMu.RUnlock()
	if pendingUnsafe && active.revision != 0 && reflect.DeepEqual(active.excludeDirs, settings.excludeDirs) && reflect.DeepEqual(active.excludePaths, settings.excludePaths) && finder.SearchRootConfigured(active.roots, absolute) && finder.SearchRootConfigured(settings.roots, absolute) {
		settings = active
		pendingUnsafe = false
	}
	options.ExcludeDirs = append([]string(nil), settings.excludeDirs...)
	options.ExcludePaths = append([]string(nil), settings.excludePaths...)
	if pendingUnsafe {
		options.IndexedOnly = true
	}
	if finder.SearchRootConfigured(settings.roots, absolute) {
		options.Indexed = true
		options.MaxFiles, options.MaxBytes, options.RefreshSec = settings.maxFiles, settings.maxBytes, settings.refreshSecs
		options.SettingsRevision = settings.revision
	}
	return options
}
