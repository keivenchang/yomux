// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestFinderIndexSettingsRevisionFitsSQLiteInteger(t *testing.T) {
	var digest [32]byte
	for index := 0; index < 8; index++ {
		digest[index] = 0xff
	}
	if revision := finderIndexSettingsRevision(digest); revision != maxSQLiteSettingsRevision {
		t.Fatalf("settings revision = %d, want signed SQLite maximum %d", revision, maxSQLiteSettingsRevision)
	}
}

func TestFinderIndexSettingsDefaultRefreshCadenceIsOneMinute(t *testing.T) {
	settings := &server{}
	payload := map[string]any{"settings": map[string]any{"file_explorer": map[string]any{"indexed_dirs": []any{t.TempDir()}}}}
	if _, _, err := settings.updateFinderIndexSettings(payload); err != nil {
		t.Fatal(err)
	}
	if settings.indexSettings.refreshSecs != 60 {
		t.Fatalf("default refresh cadence = %d seconds, want 60", settings.indexSettings.refreshSecs)
	}
}

func TestFinderIndexSettingsPendingFenceRetainsRemovedRootsForRetry(t *testing.T) {
	removedRoot := filepath.Join(t.TempDir(), "removed")
	currentRoot := filepath.Join(t.TempDir(), "current")
	settings := &server{
		searchIndex:   finder.NewSearchIndexWorkerClient(t.TempDir()),
		indexSettings: finderIndexSettings{roots: []string{removedRoot}, maxFiles: finder.DefaultIndexedFiles, maxBytes: finder.DefaultIndexBytes, refreshSecs: 300, persist: true},
	}
	payload := map[string]any{"settings": map[string]any{"file_explorer": map[string]any{"indexed_dirs": []any{currentRoot}}}}
	if _, changed, err := settings.updateFinderIndexSettings(payload); err != nil || !changed {
		t.Fatalf("first settings update changed=%t err=%v", changed, err)
	}
	if got := uniqueFinderIndexRoots(settings.indexSettingsFenceRoots); len(got) != 2 || got[0] != removedRoot || got[1] != currentRoot {
		t.Fatalf("pending roots after removal = %#v", got)
	}
	if _, changed, err := settings.updateFinderIndexSettings(payload); err != nil || changed {
		t.Fatalf("retry settings update changed=%t err=%v", changed, err)
	}
	if !settings.indexSettingsFencePending || len(settings.indexSettingsFenceRoots) != 2 || settings.indexSettingsFenceRoots[0] != removedRoot {
		t.Fatalf("retry lost pending removed-root fence: pending=%t roots=%#v", settings.indexSettingsFencePending, settings.indexSettingsFenceRoots)
	}
}

func TestFinderIndexSettingsPreservesSnapshotForLimitOnlyUpdate(t *testing.T) {
	root := t.TempDir()
	settings := &server{
		searchIndex: finder.NewSearchIndexWorkerClient(t.TempDir()),
		indexSettings: finderIndexSettings{
			roots: []string{root}, excludeDirs: []string{}, excludePaths: []string{},
			maxFiles: 1000,
			maxBytes: finder.DefaultIndexBytes, refreshSecs: 300, persist: true, revision: 41,
		},
	}
	payload := map[string]any{"settings": map[string]any{"file_explorer": map[string]any{
		"indexed_dirs": []any{root}, "index_max_files": float64(finder.DefaultIndexedFiles),
	}}}
	if _, changed, err := settings.updateFinderIndexSettings(payload); err != nil || !changed {
		t.Fatalf("limit update changed=%t err=%v", changed, err)
	}
	if !settings.indexSettingsFencePending || !settings.indexSettingsFencePreserveSnapshots {
		t.Fatalf("limit-only update fence pending=%t preserve=%t", settings.indexSettingsFencePending, settings.indexSettingsFencePreserveSnapshots)
	}
}

func TestFinderIndexUsesOneFileLimitAndIgnoresLegacyPersistenceCap(t *testing.T) {
	root := t.TempDir()
	settings := &server{}
	payload := map[string]any{"settings": map[string]any{"file_explorer": map[string]any{
		"indexed_dirs": []any{root}, "index_max_files": float64(finder.DefaultIndexedFiles),
		"index_persist_max_files": float64(100_000), "index_persist": true,
	}}}
	if _, _, err := settings.updateFinderIndexSettings(payload); err != nil {
		t.Fatal(err)
	}
	if got := settings.finderSearchOptions(root, "", false).MaxFiles; got != finder.DefaultIndexedFiles {
		t.Fatalf("max files with legacy persistence cap = %d, want %d", got, finder.DefaultIndexedFiles)
	}
	payload["settings"].(map[string]any)["file_explorer"].(map[string]any)["index_persist"] = false
	if _, _, err := settings.updateFinderIndexSettings(payload); err != nil {
		t.Fatal(err)
	}
	if got := settings.finderSearchOptions(root, "", false).MaxFiles; got != finder.DefaultIndexedFiles {
		t.Fatalf("max files with persistence disabled = %d, want %d", got, finder.DefaultIndexedFiles)
	}
}

func TestFinderIndexSettingsExternalYAMLAndMalformedRetainLastValid(t *testing.T) {
	config := t.TempDir()
	root := testsupport.RepoRoot(t)
	path := filepath.Join(config, "settings.yaml")
	if err := os.WriteFile(path, []byte("file_explorer:\n  index_persist_max_mb: 512\n"), 0600); err != nil {
		t.Fatal(err)
	}
	settings := &server{root: root, config: config}
	if err := settings.reloadFinderIndexSettings(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	previous := settings.indexSettings.revision
	if settings.indexSettings.maxBytes != 512*1024*1024 {
		t.Fatal("saved policy was not loaded")
	}
	if err := os.WriteFile(path, []byte("file_explorer:\n  index_persist_max_mb: 1024\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := settings.reloadFinderIndexSettings(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	if settings.indexSettings.revision == previous || settings.indexSettings.maxBytes != finder.DefaultIndexBytes {
		t.Fatal("external policy change was missed")
	}
	validRevision := settings.indexSettings.revision
	if err := os.WriteFile(path, []byte("file_explorer: [broken YAML"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := settings.reloadFinderIndexSettings(context.Background(), true); err == nil {
		t.Fatal("malformed YAML accepted")
	}
	if settings.indexSettings.revision != validRevision || settings.finderIndexSettingsStatus()["synchronization_state"] != "error" {
		t.Fatal("malformed YAML replaced the last valid configuration")
	}
}

func TestFinderIndexSettingsRejectsStaleParsedPayload(t *testing.T) {
	config := t.TempDir()
	path := filepath.Join(config, "settings.yaml")
	if err := os.WriteFile(path, []byte("file_explorer:\n  index_persist_max_mb: 1024\n"), 0600); err != nil {
		t.Fatal(err)
	}
	settings := &server{config: config, indexSettings: finderIndexSettings{revision: 41, maxBytes: 512 * 1024 * 1024}}
	payload := map[string]any{"mtime_ns": float64(1), "settings": map[string]any{"file_explorer": map[string]any{"index_persist_max_mb": float64(64)}}}
	if err := settings.applyFinderIndexSettings(context.Background(), payload); err == nil {
		t.Fatal("stale parsed settings accepted")
	}
	if settings.indexSettings.revision != 41 {
		t.Fatal("stale parsed payload published configuration")
	}
}

func TestFinderIndexSettingsFailedAcknowledgmentRemainsPending(t *testing.T) {
	config := t.TempDir()
	path := filepath.Join(config, "settings.yaml")
	if err := os.WriteFile(path, []byte("file_explorer:\n  index_persist_max_mb: 1024\n"), 0600); err != nil {
		t.Fatal(err)
	}
	worker := finder.NewSearchIndexWorkerClient(t.TempDir())
	worker.Close()
	settings := &server{root: testsupport.RepoRoot(t), config: config, searchIndex: worker}
	for _, onlyChanged := range []bool{false, true} {
		if err := settings.reloadFinderIndexSettings(context.Background(), onlyChanged); err == nil {
			t.Fatal("closed worker acknowledged settings")
		}
		status := settings.finderIndexSettingsStatus()
		if !settings.indexSettingsFencePending || status["synchronization_state"] != "error" || status["active_revision"] != "0" {
			t.Fatalf("pending retry lost: %v", status)
		}
		if _, ok := status["configured_revision"].(string); !ok {
			t.Fatal("revision was not a decimal string")
		}
	}
}

func TestFinderIndexPendingRootChangeKeepsUnaffectedActivePolicy(t *testing.T) {
	retained, removed := t.TempDir(), t.TempDir()
	settings := &server{indexSettings: finderIndexSettings{roots: []string{retained}, revision: 42, maxFiles: 2000, maxBytes: finder.DefaultIndexBytes, refreshSecs: 60}, indexActiveSettings: finderIndexSettings{roots: []string{retained, removed}, revision: 41, maxFiles: 1000, maxBytes: 512 * 1024 * 1024, refreshSecs: 1800}, indexSettingsFencePending: true}
	options := settings.finderSearchOptions(retained, "", false)
	if options.SettingsRevision != 41 || !options.Indexed || options.IndexedOnly || options.MaxFiles != 1000 {
		t.Fatalf("unaffected root did not retain active policy: %#v", options)
	}
	if options := settings.finderSearchOptions(removed, "", false); !options.IndexedOnly {
		t.Fatal("removed root was not gated before acknowledgment")
	}
}

func TestFinderIndexSettingsCancelledWaiter(t *testing.T) {
	s := &server{}
	s.indexSettingsUpdateMu.Lock()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	done := make(chan error, 1)
	go func() { done <- s.reloadFinderIndexSettings(ctx, false) }()
	select {
	case err := <-done:
		s.indexSettingsUpdateMu.Unlock()
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancelled reload = %v, want context.Canceled", err)
		}
	case <-time.After(time.Second):
		s.indexSettingsUpdateMu.Unlock()
		<-done
		t.Fatal("cancelled settings reload waited for another operation's lock")
	}
}

func TestFinderIndexSettingsHTTPCancelledWaiter(t *testing.T) {
	s := &server{}
	s.indexSettingsUpdateMu.Lock()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	request := httptest.NewRequest(http.MethodGet, "/api/settings", nil).WithContext(ctx)
	response := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		s.settings(response, request)
		close(done)
	}()
	select {
	case <-done:
		s.indexSettingsUpdateMu.Unlock()
		if response.Code != http.StatusServiceUnavailable {
			t.Fatalf("cancelled settings status = %d", response.Code)
		}
	case <-time.After(time.Second):
		s.indexSettingsUpdateMu.Unlock()
		<-done
		t.Fatal("cancelled HTTP settings request waited for another operation's lock")
	}
}
