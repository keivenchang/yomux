// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"path/filepath"
	"testing"

	"github.com/keivenchang/yomux/internal/finder"
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
		indexSettings: finderIndexSettings{roots: []string{removedRoot}, maxFiles: finder.DefaultIndexedFiles, persistFiles: finder.DefaultIndexedFiles, maxBytes: finder.DefaultIndexBytes, refreshSecs: 300, persist: true},
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
			maxFiles: 1000, persistFiles: finder.DefaultIndexedFiles,
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
