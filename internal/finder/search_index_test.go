// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestGoSearchIndexColdStartupAndRestartPreserveOwnedDatabase(t *testing.T) {
	state, root := t.TempDir(), t.TempDir()
	path := filepath.Join(state, "search-index.sqlite3")
	unrelated := filepath.Join(state, "unrelated.json")
	if err := os.WriteFile(unrelated, []byte("unrelated state"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "techblog.go"), []byte("fixture"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(path)
	if err != nil {
		t.Fatal(err)
	}
	var roots int
	if err := store.db.QueryRow("SELECT COUNT(*) FROM roots").Scan(&roots); err != nil || roots != 0 {
		t.Fatalf("cold roots=%d err=%v", roots, err)
	}
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}); err != nil {
		t.Fatal(err)
	}
	before, err := engine.search(root, fileID(info), "tech blog", 10, "", 0)
	if err != nil || before["count"] != 1 {
		t.Fatalf("initial search=%#v err=%v", before, err)
	}
	if err := store.db.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := openSearchIndexStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.db.Close()
	after, err := newSearchIndexEngine(reopened).search(root, fileID(info), "tech blog", 10, "", 0)
	if err != nil || after["count"] != before["count"] || before["generation"] == nil || after["generation"] != before["generation"] || after["indexed"] != true {
		t.Fatalf("restart lost indexed results: before=%#v after=%#v err=%v", before, after, err)
	}
	if data, err := os.ReadFile(unrelated); err != nil || string(data) != "unrelated state" {
		t.Fatalf("unrelated file changed: %q %v", data, err)
	}
}

func TestGoSearchIndexConcurrentInstancesOwnDisjointDatabases(t *testing.T) {
	var group sync.WaitGroup
	for _, name := range []string{"first", "second"} {
		state, root := t.TempDir(), t.TempDir()
		group.Add(1)
		go func() {
			defer group.Done()
			if err := os.WriteFile(filepath.Join(root, name+".go"), []byte(name), 0o600); err != nil {
				t.Error(err)
				return
			}
			info, err := os.Stat(root)
			if err != nil {
				t.Error(err)
				return
			}
			store, err := openSearchIndexStore(filepath.Join(state, "search-index.sqlite3"))
			if err != nil {
				t.Error(err)
				return
			}
			defer func() { store.db.Close() }()
			engine := newSearchIndexEngine(store)
			if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}); err != nil {
				t.Error(err)
				return
			}
			result, err := engine.search(root, fileID(info), "", 10, "", 0)
			if err != nil || result["count"] != 1 {
				t.Errorf("%s results=%#v err=%v", name, result, err)
				return
			}
			files := result["files"].([]map[string]any)
			if files[0]["name"] != name+".go" {
				t.Errorf("%s saw a peer's index: %#v", name, files)
			}
		}()
	}
	group.Wait()
}

func TestGoQuickOpenRefreshReflectsRenamesAndDeletions(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"rename.go", "delete.go"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("fixture"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "search-index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	build := indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}
	if err := engine.build(build); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Join(root, "rename.go"), filepath.Join(root, "renamed.go")); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(root, "delete.go")); err != nil {
		t.Fatal(err)
	}
	if err := engine.build(build); err != nil {
		t.Fatal(err)
	}
	result, err := engine.search(root, fileID(info), "", 10, "", 0)
	if err != nil || result["count"] != 1 {
		t.Fatalf("refreshed results=%#v err=%v", result, err)
	}
	files := result["files"].([]map[string]any)
	if files[0]["name"] != "renamed.go" || result["index_coverage"] != "complete" {
		t.Fatalf("stale Quick Open result: %#v", result)
	}
}

type unknownTypeDirEntry struct {
	name string
	info os.FileInfo
}

func (entry unknownTypeDirEntry) Name() string               { return entry.name }
func (entry unknownTypeDirEntry) IsDir() bool                { return false }
func (entry unknownTypeDirEntry) Type() os.FileMode          { return 0 }
func (entry unknownTypeDirEntry) Info() (os.FileInfo, error) { return entry.info, nil }

func TestSearchIndexStoreRejectsNewerSchema(t *testing.T) {
	path := filepath.Join(t.TempDir(), "index.sqlite3")
	store, err := openSearchIndexStore(path)
	if err != nil {
		t.Fatal(err)
	}
	var version int
	if err := store.db.QueryRow(`PRAGMA user_version`).Scan(&version); err != nil {
		t.Fatal(err)
	}
	if version != searchIndexSchemaVersion {
		t.Fatalf("schema version = %d", version)
	}
	if _, err := store.db.Exec(fmt.Sprintf(`PRAGMA user_version=%d`, searchIndexSchemaVersion+1)); err != nil {
		t.Fatal(err)
	}
	if err := store.db.Close(); err != nil {
		t.Fatal(err)
	}
	if newer, err := openSearchIndexStore(path); err == nil {
		_ = newer.db.Close()
		t.Fatal("opened a newer schema with the older worker")
	}
}

func TestSearchIndexExcludesDirectoryWhenDirEntryTypeIsUnknown(t *testing.T) {
	directory := t.TempDir()
	info, err := os.Stat(directory)
	if err != nil {
		t.Fatal(err)
	}
	entry := unknownTypeDirEntry{name: ".git", info: info}
	if entry.IsDir() || entry.Type() != 0 {
		t.Fatal("fixture must model a directory entry with unknown type")
	}
	_, excluded, err := indexEntryInfo(entry, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !excluded {
		t.Fatal("built-in directory exclusion was bypassed when DirEntry type was unknown")
	}
}

func TestSearchIndexDirectoryQueueEnforcesByteAndCountBudgets(t *testing.T) {
	if indexDirectoryQueueFits(indexQueueLimit, 0, "child", "child") {
		t.Fatal("directory queue exceeded its entry limit")
	}
	if indexDirectoryQueueFits(1, indexQueueByteLimit-4, "long", "path") {
		t.Fatal("directory queue exceeded its path-byte limit")
	}
	if !indexDirectoryQueueFits(1, indexQueueByteLimit-9, "long", "path") {
		t.Fatal("directory queue rejected an entry within both limits")
	}
}

func TestSearchIndexPreservesExplicitSnapshotBudget(t *testing.T) {
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	engine.activeBuild = true
	const budget = int64(1024 * 1024 * 1024)
	engine.schedule("/queued-root", "identity", 100, budget, 300, nil, nil, 0)
	if job := engine.pendingBuild["/queued-root"]; job.maxBytes != budget {
		t.Fatalf("explicit snapshot budget = %d, want %d", job.maxBytes, budget)
	}
}

func TestSearchIndexRejectsSchedulingWithFencedSettingsRevision(t *testing.T) {
	root := t.TempDir()
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if _, err := engine.handle(indexRequest{Action: "fence", Root: root, SettingsRevision: 42}); err != nil {
		t.Fatal(err)
	}
	engine.schedule(root, fileID(info), 100, DefaultIndexBytes, 0, nil, nil, 41)
	engine.mu.Lock()
	_, building := engine.builds[root]
	_, queued := engine.pendingBuild[root]
	engine.mu.Unlock()
	if building || queued {
		t.Fatal("request with a pre-fence settings revision started a build")
	}
}

func TestSearchIndexSettingsFenceCoversDescendantsAndWorkerRestart(t *testing.T) {
	configuredRoot := t.TempDir()
	indexedRoot := filepath.Join(configuredRoot, "nested")
	if err := os.Mkdir(indexedRoot, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(indexedRoot, "blog.md"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(indexedRoot)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: indexedRoot, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes, settingsRevision: 41}); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.handle(indexRequest{Action: "fence", Root: configuredRoot, SettingsRevision: 42}); err != nil {
		t.Fatal(err)
	}
	var rootsAfterFence int
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM roots WHERE root=?`, indexedRoot).Scan(&rootsAfterFence); err != nil {
		t.Fatal(err)
	}
	if rootsAfterFence != 0 {
		t.Fatalf("descendant snapshot count after settings fence = %d, want 0", rootsAfterFence)
	}

	restarted := newSearchIndexEngine(store)
	restarted.schedule(indexedRoot, fileID(info), 10, DefaultIndexBytes, 0, nil, nil, 41)
	restarted.mu.Lock()
	_, building := restarted.builds[indexedRoot]
	restarted.mu.Unlock()
	if building {
		t.Fatal("worker restart forgot the persisted ancestor settings fence")
	}
	response, err := restarted.search(indexedRoot, fileID(info), "blog", 10, "", 41)
	if err != nil {
		t.Fatal(err)
	}
	if response["indexed"] == true || response["index_state"] != "warming" || response["count"] != 0 {
		t.Fatalf("stale descendant search response = %#v", response)
	}
}

func TestSearchIndexPreservesSnapshotForCompatibleSettingsRefresh(t *testing.T) {
	configuredRoot := t.TempDir()
	root := filepath.Join(configuredRoot, "nested")
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "blog.md"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes, settingsRevision: 41}); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.fenceSettings([]string{configuredRoot}, 42, false, true); err != nil {
		t.Fatal(err)
	}
	response, err := engine.search(root, fileID(info), "blog", 10, "", 42)
	if err != nil {
		t.Fatal(err)
	}
	if response["indexed"] != true || response["count"] != 1 || response["index_state"] != "warming" {
		t.Fatalf("compatible settings refresh did not serve the prior snapshot: %#v", response)
	}
	var revision uint64
	if err := store.db.QueryRow(`SELECT settings_revision FROM roots WHERE root=?`, root).Scan(&revision); err != nil {
		t.Fatal(err)
	}
	if revision != 41 {
		t.Fatalf("preserved snapshot build revision = %d, want 41", revision)
	}
}

func TestSearchIndexNewSettingsRevisionSupersedesRemovedAncestorFence(t *testing.T) {
	configuredRoot := t.TempDir()
	nestedRoot := filepath.Join(configuredRoot, "nested")
	if err := os.Mkdir(nestedRoot, 0o700); err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if _, err := engine.fenceSettings([]string{configuredRoot}, 42, true); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.fenceSettings([]string{nestedRoot}, 43, true); err != nil {
		t.Fatal(err)
	}
	engine.mu.Lock()
	currentAccepted := engine.settingsRevisionAcceptedLocked(nestedRoot, 43)
	staleAccepted := engine.settingsRevisionAcceptedLocked(nestedRoot, 42)
	engine.mu.Unlock()
	if !currentAccepted || staleAccepted {
		t.Fatalf("revision acceptance after nested reconfiguration: current=%t stale=%t", currentAccepted, staleAccepted)
	}

	restarted := newSearchIndexEngine(store)
	restarted.mu.Lock()
	currentAccepted = restarted.settingsRevisionAcceptedLocked(nestedRoot, 43)
	restarted.mu.Unlock()
	if !currentAccepted {
		t.Fatal("worker restart did not load the latest global settings revision")
	}
}

func TestSearchIndexSettingsFenceRetryRepeatsCleanupAtSameRevision(t *testing.T) {
	configuredRoot := t.TempDir()
	nestedRoot := filepath.Join(configuredRoot, "nested")
	if err := os.Mkdir(nestedRoot, 0o700); err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if _, err := engine.fenceSettings([]string{configuredRoot}, 42, true); err != nil {
		t.Fatal(err)
	}
	if _, err := store.db.Exec(`INSERT INTO roots(root,identity,generation,state,coverage) VALUES(?,?,?,?,?)`, nestedRoot, "identity", 1, "ready", "complete"); err != nil {
		t.Fatal(err)
	}
	if err := engine.writeDiscoveredPaths(nestedRoot, 1, []discoveredIndexPath{{relative: "blog.md", name: "blog.md", identity: "file", size: 1, mtimeNS: 1}}); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.fenceSettings([]string{configuredRoot}, 42, true); err != nil {
		t.Fatal(err)
	}
	var rowsAfterRetry int
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM roots WHERE root=?`, nestedRoot).Scan(&rowsAfterRetry); err != nil {
		t.Fatal(err)
	}
	if rowsAfterRetry != 0 {
		t.Fatalf("descendant snapshots after same-revision retry = %d, want 0", rowsAfterRetry)
	}
}

func TestSearchIndexSettingsRevisionBlocksBuildWhenRemovedRootWasOmitted(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "blog.md"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if _, err := engine.fenceSettings(nil, 43, true); err != nil {
		t.Fatal(err)
	}
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes, settingsRevision: 42}); err != nil {
		t.Fatal(err)
	}
	var snapshotCount int
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM roots WHERE root=?`, root).Scan(&snapshotCount); err != nil {
		t.Fatal(err)
	}
	if snapshotCount != 0 {
		t.Fatalf("stale removed-root build published %d snapshots", snapshotCount)
	}
}

func TestSearchIndexStartupSyncRemovesUnconfiguredSnapshots(t *testing.T) {
	removedRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(removedRoot, "blog.md"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(removedRoot)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: removedRoot, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes, settingsRevision: 42}); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.fenceSettings(nil, 43, false); err != nil {
		t.Fatal(err)
	}
	var snapshotCount int
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM roots WHERE root=?`, removedRoot).Scan(&snapshotCount); err != nil {
		t.Fatal(err)
	}
	if snapshotCount != 0 {
		t.Fatalf("startup sync retained %d snapshots for an unconfigured root", snapshotCount)
	}
}

func TestSearchIndexStartupSyncPreservesMatchingConfiguredSnapshot(t *testing.T) {
	configuredRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(configuredRoot, "blog.md"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(configuredRoot)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: configuredRoot, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes, settingsRevision: 43}); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.fenceSettings([]string{configuredRoot}, 43, false); err != nil {
		t.Fatal(err)
	}
	var rootCount, entryCount int
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM roots WHERE root=?`, configuredRoot).Scan(&rootCount); err != nil {
		t.Fatal(err)
	}
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM entries WHERE root=(SELECT id FROM root_keys WHERE path=?)`, configuredRoot).Scan(&entryCount); err != nil {
		t.Fatal(err)
	}
	if rootCount != 1 || entryCount != 1 {
		t.Fatalf("matching configured snapshot retained roots=%d entries=%d, want 1 and 1", rootCount, entryCount)
	}
}

func TestSearchIndexCursorRequiresMatchingWorkerIncarnation(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "blog.md"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	first := newSearchIndexEngine(store)
	if err := first.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}); err != nil {
		t.Fatal(err)
	}
	initial, err := first.search(root, fileID(info), "blog", 10, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	second := newSearchIndexEngine(store)
	if first.incarnation == second.incarnation {
		t.Fatal("new worker incarnation reused its predecessor's identity")
	}
	refresh, err := second.search(root, fileID(info), "blog", 10, initial["cursor"].(string), 0)
	if err != nil {
		t.Fatal(err)
	}
	if refresh["rebase_required"] != true || refresh["worker_incarnation"] != second.incarnation {
		t.Fatalf("cross-incarnation cursor response = %#v", refresh)
	}
}

func TestIndexedOnlySearchForUnconfiguredRootReportsNotIndexed(t *testing.T) {
	root := t.TempDir()
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	for _, cursor := range []string{"", "stale-cursor"} {
		payload, finderErr := service.SearchWithOptions(context.Background(), root, "blog", 10, true, false, false, SearchOptions{IndexedOnly: true, Cursor: cursor})
		if finderErr != nil {
			t.Fatal(finderErr)
		}
		if payload["indexed"] != false || payload["index_state"] != "not_indexed" {
			t.Fatalf("unconfigured indexed-only status for cursor %q = %#v", cursor, payload)
		}
	}
}

func TestSearchIndexRemovalWaitsForSnapshotReader(t *testing.T) {
	for _, action := range []string{"unindex", "fence", "settings_fence", "purge"} {
		t.Run(action, func(t *testing.T) {
			root := t.TempDir()
			if err := os.WriteFile(filepath.Join(root, "techblog.md"), nil, 0o600); err != nil {
				t.Fatal(err)
			}
			info, err := os.Stat(root)
			if err != nil {
				t.Fatal(err)
			}
			store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { store.db.Close() }()
			engine := newSearchIndexEngine(store)
			if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}); err != nil {
				t.Fatal(err)
			}
			engine.crawlPause.Lock()
			locked := true
			defer func() {
				if locked {
					engine.crawlPause.Unlock()
				}
			}()
			done := make(chan error, 1)
			started := make(chan struct{})
			go func() {
				close(started)
				_, err := engine.handle(indexRequest{Action: action, Root: root, FenceRoots: []string{root}, SettingsRevision: 1, Invalidate: true})
				done <- err
			}()
			<-started
			select {
			case err := <-done:
				t.Fatalf("%s crossed active snapshot reader: %v", action, err)
			case <-time.After(100 * time.Millisecond):
			}
			var name string
			if err := store.db.QueryRow(`SELECT name FROM entries WHERE root=(SELECT id FROM root_keys WHERE path=?)`, root).Scan(&name); err != nil || name != "techblog.md" {
				t.Fatalf("pinned search lost techblog.md: name=%q err=%v", name, err)
			}
			engine.schedule(root, fileID(info), 10, DefaultIndexBytes, 0, nil, nil, 1)
			engine.mu.Lock()
			startedBuild := engine.builds[root]
			engine.mu.Unlock()
			engine.crawlPause.Unlock()
			locked = false
			select {
			case err := <-done:
				if err != nil {
					t.Fatal(err)
				}
			case <-time.After(5 * time.Second):
				t.Fatalf("%s did not resume after snapshot reader finished", action)
			}
			if err := engine.quiesceBuilds(); err != nil {
				t.Fatal(err)
			}
			if startedBuild {
				t.Fatalf("%s admitted a replacement crawl before removal finished", action)
			}
			engine.mu.Lock()
			admissionPaused := engine.snapshotMutations != 0
			engine.mu.Unlock()
			if admissionPaused {
				t.Fatalf("%s did not restore build admission", action)
			}
		})
	}
}

func TestSearchIndexDoesNotPublishMissingRoot(t *testing.T) {
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	published, err := engine.publishGeneration(indexBuildJob{root: "/missing", identity: "identity"}, 1, "ready", "complete", 0, 4096, "", 0, nil)
	if published || err == nil {
		t.Fatalf("missing root reported publication: published=%t err=%v", published, err)
	}
}

func TestSearchIndexPublicationWaitsForSnapshotReader(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "techblog.md"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	job := indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	var prior int
	if err := store.db.QueryRow(`SELECT generation FROM roots WHERE root=?`, root).Scan(&prior); err != nil {
		t.Fatal(err)
	}
	if _, err := store.db.Exec(`INSERT INTO entries SELECT root,?,directory,name,file_identity,size,mtime_ns FROM entries WHERE root=(SELECT id FROM root_keys WHERE path=?) AND generation=?`, prior+1, root, prior); err != nil {
		t.Fatal(err)
	}
	engine.crawlPause.Lock()
	locked := true
	defer func() {
		if locked {
			engine.crawlPause.Unlock()
		}
	}()
	done := make(chan error, 1)
	started := make(chan struct{})
	go func() {
		close(started)
		published, err := engine.publishGeneration(job, prior+1, "ready", "complete", 1, 4096, "", 0, nil)
		if err == nil && !published {
			err = errors.New("generation was unexpectedly fenced")
		}
		if err == nil {
			err = store.deleteGenerations(root, prior+1)
		}
		done <- err
	}()
	<-started
	select {
	case err := <-done:
		t.Fatalf("publication crossed active snapshot reader: %v", err)
	case <-time.After(100 * time.Millisecond):
	}
	var pinned int
	if err := store.db.QueryRow(`SELECT generation FROM roots WHERE root=?`, root).Scan(&pinned); err != nil || pinned != prior {
		t.Fatalf("pinned generation = %d, want %d: %v", pinned, prior, err)
	}
	var name string
	if err := store.db.QueryRow(`SELECT name FROM entries WHERE root=(SELECT id FROM root_keys WHERE path=?) AND generation=?`, root, pinned).Scan(&name); err != nil || name != "techblog.md" {
		t.Fatalf("pinned search lost techblog.md: name=%q err=%v", name, err)
	}
	engine.crawlPause.Unlock()
	locked = false
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("publication did not resume after snapshot reader finished")
	}
}

func TestSearchIndexPublishesSearchableGeneration(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "src"), 0o700); err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 1001; index++ {
		path := filepath.Join(root, strconv.Itoa(index)+"-entry.txt")
		if err := os.WriteFile(path, []byte("entry"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	for name, content := range map[string]string{
		"src/techblog.md":  "sentinel",
		"src/tech-blog.md": "sentinel",
		".git/ignored.md":  "sentinel",
	} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 2000, maxBytes: DefaultIndexBytes}); err != nil {
		t.Fatal(err)
	}
	result, err := engine.search(root, fileID(info), "tech blog", 20, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	files, ok := result["files"].([]map[string]any)
	found := false
	for _, file := range files {
		found = found || file["name"] == "techblog.md"
	}
	if !ok || len(files) != 2 || !found {
		t.Fatalf("indexed fuzzy search = %#v", result)
	}
	if result["index_coverage"] != "complete" || result["indexed"] != true {
		t.Fatalf("index coverage = %#v", result)
	}
	singleToken, err := engine.search(root, fileID(info), "blog", 20, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	files, ok = singleToken["files"].([]map[string]any)
	found = false
	for _, file := range files {
		found = found || file["name"] == "techblog.md"
	}
	if !ok || !found {
		t.Fatalf("single-token indexed search for blog = %#v", singleToken)
	}
}

func TestSearchIndexPathDiscoveryHonorsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, _, _, err := discoverIndexPaths(ctx, t.TempDir(), nil, nil, 10)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled discovery error = %v, want context.Canceled", err)
	}
}

func TestSearchIndexPathDiscoveryPreservesNamesAndSkipsSymlinks(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"line\nbreak.md", "café.md", "excluded/skip.md", "node_modules/drop.md"} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(filepath.Join(root, "café.md"), filepath.Join(root, "alias.md")); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	paths, coverage, _, err := discoverIndexPaths(context.Background(), root, nil, []string{"excluded"}, 100)
	if err != nil {
		t.Fatal(err)
	}
	if coverage != "complete" {
		t.Fatalf("coverage = %q", coverage)
	}
	got := make(map[string]bool, len(paths))
	for _, path := range paths {
		got[path.relativePath()] = true
	}
	for _, want := range []string{"line\nbreak.md", "café.md"} {
		if !got[want] {
			t.Errorf("discovery missed %q: %#v", want, got)
		}
	}
	for _, excluded := range []string{"alias.md", "excluded/skip.md", "node_modules/drop.md"} {
		if got[excluded] {
			t.Errorf("discovery included excluded or symlink path %q", excluded)
		}
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 100, maxBytes: DefaultIndexBytes, excludePaths: []string{"excluded"}}); err != nil {
		t.Fatal(err)
	}
	status, err := engine.status(root, fileID(info))
	if err != nil {
		t.Fatal(err)
	}
	if status["scan_duration_ms"].(float64) < 0 || status["last_successful_scan"] == "" {
		t.Fatalf("scan measurements missing: %#v", status)
	}
	for query, relative := range map[string]string{"line break": "line\nbreak.md", "café": "café.md"} {
		result, err := engine.search(root, fileID(info), query, 10, "", 0)
		if err != nil {
			t.Fatal(err)
		}
		files, _ := result["files"].([]map[string]any)
		found := false
		for _, file := range files {
			found = found || file["relative_path"] == relative
		}
		if !found {
			t.Errorf("indexed search for %q missed %q: %#v", query, relative, result)
		}
	}
}

func TestSearchIndexTraversesBeyondDirectSearchDirectoryLimit(t *testing.T) {
	root := t.TempDir()
	count := MaxSearchDirectories + 1
	for index := 0; index < count; index++ {
		directory := filepath.Join(root, strconv.Itoa(index))
		if err := os.Mkdir(directory, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	// The direct depth-first walk exhausts its cap before this lexically last child.
	deep := filepath.Join(root, "zzzz-target", "deep")
	if err := os.MkdirAll(deep, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(deep, "techblog.md"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: DefaultIndexedFiles, maxBytes: DefaultIndexBytes}); err != nil {
		t.Fatal(err)
	}
	status, err := engine.status(root, fileID(info))
	if err != nil {
		t.Fatal(err)
	}
	if status["count"] != 1 || status["index_coverage"] != "complete" {
		t.Fatalf("directory boundary coverage = %#v", status)
	}
	result, err := engine.search(root, fileID(info), "blog", 10, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	if result["count"] != 1 {
		t.Fatalf("search beyond directory boundary = %#v", result)
	}
}

func TestSearchIndexReplacementRootDoesNotReusePriorIdentity(t *testing.T) {
	parent := t.TempDir()
	root := filepath.Join(parent, "root")
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "oldblog.md"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	priorInfo, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	job := indexBuildJob{root: root, identity: fileID(priorInfo), maxFiles: 100, maxBytes: DefaultIndexBytes}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(root, filepath.Join(parent, "prior")); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "newblog.md"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	currentInfo, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	if fileID(priorInfo) == fileID(currentInfo) {
		t.Fatal("replacement fixture retained its original identity")
	}
	status, err := engine.status(root, fileID(currentInfo))
	if err != nil {
		t.Fatal(err)
	}
	if status["indexed"] == true {
		t.Fatal("replacement root served the prior identity's snapshot")
	}
	job.identity = fileID(currentInfo)
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	result, err := engine.search(root, job.identity, "blog", 10, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	files := result["files"].([]map[string]any)
	if len(files) != 1 || files[0]["name"] != "newblog.md" {
		t.Fatalf("replacement root search = %#v", result)
	}
}

func TestSearchIndexHonorsConfiguredGlobAndRegexExclusions(t *testing.T) {
	root := t.TempDir()
	for _, relative := range []string{"src/keep.md", "build/drop.md", "target/drop.md"} {
		path := filepath.Join(root, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if !excludedIndexPath(root, filepath.Join(root, "build"), []string{"glob:**/build/**"}) {
		t.Fatal("glob directory form was not pruned")
	}
	if !excludedIndexPath(root, filepath.Join(root, "target", "drop.md"), []string{"regex:(^|/)target(?:/|$)"}) {
		t.Fatal("regex path exclusion did not match")
	}
	if excludedIndexPath(root, filepath.Join(root, "src", "keep.md"), []string{"glob:**/build/**", "regex:(^|/)target(?:/|$)"}) {
		t.Fatal("unmatched path was excluded")
	}
}

func TestSearchIndexGenerationSurvivesUnindexAndResultLimitIsSeparate(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"needle-a.md", "needle-b.md", "needle-c.md"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	job := indexBuildJob{root: root, identity: fileID(info), maxFiles: 100, maxBytes: DefaultIndexBytes}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	first, err := engine.search(root, fileID(info), "needle", 1, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	if first["truncated"] != true || first["result_limit_hit"] != true || first["index_coverage"] != "complete" {
		t.Fatalf("result limit and coverage were conflated: %#v", first)
	}
	if _, err := engine.handle(indexRequest{Action: "unindex", Root: root}); err != nil {
		t.Fatal(err)
	}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	second, err := engine.search(root, fileID(info), "needle", 1, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	if first["generation"] == second["generation"] {
		t.Fatalf("generation repeated after unindex: first=%v second=%v", first["generation"], second["generation"])
	}
}

func TestSearchIndexRefreshInterval(t *testing.T) {
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	now := time.Now()
	if _, err := store.db.Exec(`INSERT INTO roots(root,identity,generation,state,coverage,count,bytes,updated_ns,settings_revision) VALUES(?, ?, 1, 'ready', 'complete', 1, 10, ?, 7)`, "/root", "identity", now.UnixNano()); err != nil {
		t.Fatal(err)
	}
	if engine.needsRefresh("/root", "identity", 300, 7, now.Add(time.Minute)) {
		t.Fatal("fresh snapshot requested an early crawl")
	}
	if _, err := store.db.Exec(`UPDATE roots SET state='warming',updated_ns=? WHERE root=?`, now.UnixNano(), "/root"); err != nil {
		t.Fatal(err)
	}
	if !engine.needsRefresh("/root", "identity", 300, 7, now.Add(time.Minute)) {
		t.Fatal("interrupted warming snapshot did not request an immediate recovery crawl")
	}
	if _, err := store.db.Exec(`UPDATE roots SET state='ready' WHERE root=?`, "/root"); err != nil {
		t.Fatal(err)
	}
	if !engine.needsRefresh("/root", "identity", 300, 7, now.Add(5*time.Minute)) {
		t.Fatal("expired snapshot did not request a crawl")
	}
	if engine.needsRefresh("/root", "identity", 0, 7, now) {
		t.Fatal("zero refresh interval requested a periodic crawl for a valid cached snapshot")
	}
	if !engine.needsRefresh("/missing", "identity", 0, 7, now) {
		t.Fatal("zero refresh interval suppressed initial on-search indexing")
	}
}

func TestSearchIndexSearchHonorsCanceledContext(t *testing.T) {
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = engine.searchContext(ctx, "/root", "identity", "query", 10, "", 0)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled search returned %v", err)
	}
}

func TestSearchIndexRequestRejectsCanceledContextBeforeWorkerStart(t *testing.T) {
	client := NewSearchIndexWorkerClient(t.TempDir())
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.Request(ctx, indexRequest{Action: "status", Root: "/root"}); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled request returned %v", err)
	}
	if client.cmd != nil {
		t.Fatal("canceled request started a worker")
	}
}

func TestSearchIndexWorkerEnvironmentCapsGoProcs(t *testing.T) {
	environment := withSearchIndexWorkerCPUCap([]string{"PATH=/bin", "GOMAXPROCS=8", "LANG=C"})
	var values []string
	for _, entry := range environment {
		if strings.HasPrefix(entry, "GOMAXPROCS=") {
			values = append(values, entry)
		}
	}
	if len(values) != 1 || values[0] != "GOMAXPROCS=1" {
		t.Fatalf("worker GOMAXPROCS entries = %v, want [GOMAXPROCS=1]", values)
	}
}

func TestSearchIndexPurgeQuiescesBuildsAndClearsRootMetadata(t *testing.T) {
	root := t.TempDir()
	for index := 0; index < 300; index++ {
		path := filepath.Join(root, "nested", "file-"+strconv.Itoa(index)+".txt")
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	engine.schedule(root, fileID(info), 1000, DefaultIndexBytes, 0, nil, nil, 1)
	if _, err := engine.handle(indexRequest{Action: "purge"}); err != nil {
		t.Fatal(err)
	}
	for _, table := range []string{"entries", "roots", "root_generations"} {
		var count int
		if err := store.db.QueryRow(`SELECT COUNT(*) FROM ` + table).Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count != 0 {
			t.Errorf("purge left %d rows in %s", count, table)
		}
	}
	engine.mu.Lock()
	activeBuild, pendingBuild := engine.activeBuild, len(engine.pendingBuild)
	engine.mu.Unlock()
	if activeBuild || pendingBuild != 0 {
		t.Fatalf("purge returned with builds active=%t pending=%d", activeBuild, pendingBuild)
	}
}

func TestSearchIndexTokensMatchOnePathSegmentEach(t *testing.T) {
	if _, matched := searchEntrySortKey("notes.md", "archive/d/i/s/notes.md", []string{"dis"}); matched {
		t.Fatal("query matched across path separators")
	}
	if _, matched := searchEntrySortKey("notes.md", "src/tech/blog/notes.md", []string{"tech", "blog"}); !matched {
		t.Fatal("multi-token query failed to match separate path segments")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "interviews/yo7110/20261012-022.png", []string{"/"}); !matched {
		t.Fatal("slash query failed to match a nested path")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "interviews/yo7110/20261012-022.png", []string{"interviews/yo7110"}); !matched {
		t.Fatal("contiguous path query failed to match directory segments")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "interviews/yo7110/20261012-022.png", []string{"yo7110/interviews"}); !matched {
		t.Fatal("reversed two-segment path query failed to match")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "interviews/yo7110/20261012-022.png", []string{"interviews/2026"}); !matched {
		t.Fatal("path query failed to match terms across directory and basename components")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "interviews/yo7110/20261012-022.png", []string{"2026/interviews"}); !matched {
		t.Fatal("reversed path query failed to match terms across directory and basename components")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "Interviews/yo7110/20261012-022.png", []string{"interviews/"}); !matched {
		t.Fatal("case-insensitive folder path query failed to match")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "interviews/yo7110/20261012-022.png", []string{"inter/"}); !matched {
		t.Fatal("partial trailing-slash folder query failed to match")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "Interviews/yo7110/20261012-022.png", []string{"inter/"}); !matched {
		t.Fatal("case-insensitive partial trailing-slash folder query failed to match")
	}
	if _, matched := searchEntrySortKey("interviews.md", "interviews.md", []string{"interviews/"}); matched {
		t.Fatal("trailing-slash folder query matched only a basename")
	}
	if _, matched := searchEntrySortKey("interviews.md", "interviews.md", []string{"inter/"}); matched {
		t.Fatal("partial trailing-slash folder query matched only a basename")
	}
	if _, matched := searchEntrySortKey("20261012-022.png", "20261012-022.png", []string{"/"}); matched {
		t.Fatal("slash query matched a root-level file without a path separator")
	}
}

func TestSearchIndexPublishesSlashQueriesAcrossNestedComponents(t *testing.T) {
	root := t.TempDir()
	relative := filepath.Join("interviews", "yo7110", "2026-interview.md")
	path := filepath.Join(root, relative)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("interview fixture"), 0o600); err != nil {
		t.Fatal(err)
	}
	doitPath := filepath.Join(root, "DOIT-123.md")
	if err := os.WriteFile(doitPath, []byte("doit fixture"), 0o600); err != nil {
		t.Fatal(err)
	}
	pathDoitFile := filepath.Join(root, "doit", "123.md")
	if err := os.MkdirAll(filepath.Dir(pathDoitFile), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(pathDoitFile, []byte("slash path fixture"), 0o600); err != nil {
		t.Fatal(err)
	}
	pathDoitDirectory := filepath.Join(root, "DOIT-123", "notes.md")
	if err := os.MkdirAll(filepath.Dir(pathDoitDirectory), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(pathDoitDirectory, []byte("directory decoy"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}); err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{"interviews/", "inter/", "inter/06", "inter/2026", "interv/20", "2026/interviews"} {
		result, err := engine.search(root, fileID(info), query, 10, "", 0)
		if err != nil {
			t.Fatalf("search %q: %v", query, err)
		}
		files, _ := result["files"].([]map[string]any)
		if len(files) != 1 || files[0]["relative_path"] != filepath.ToSlash(relative) {
			t.Errorf("search %q returned %#v, want %q", query, files, filepath.ToSlash(relative))
		}
	}
	result, err := engine.search(root, fileID(info), "doit/123", 10, "", 0)
	if err != nil {
		t.Fatalf("search doit/123: %v", err)
	}
	files, _ := result["files"].([]map[string]any)
	got := make(map[string]bool, len(files))
	for _, file := range files {
		got[file["relative_path"].(string)] = true
	}
	if len(got) != 2 || !got["DOIT-123.md"] || !got["doit/123.md"] || got["DOIT-123/notes.md"] {
		t.Errorf("search doit/123 returned %#v, want root-level DOIT-123.md and exact doit/123.md only", files)
	}
}

func TestSearchIndexReportsPartialCoverageAtFileLimit(t *testing.T) {
	root := t.TempDir()
	for index := 0; index < 4; index++ {
		path := filepath.Join(root, "file-"+string(rune('a'+index))+".txt")
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 2, maxBytes: DefaultIndexBytes}); err != nil {
		t.Fatal(err)
	}
	status, err := engine.status(root, fileID(info))
	if err != nil {
		t.Fatal(err)
	}
	if status["index_coverage"] != "partial" || status["count"] != 2 {
		t.Fatalf("bounded index status = %#v", status)
	}
}

func TestSearchIndexRefreshPublishesExpectedPartialCoverageAtFileLimit(t *testing.T) {
	root := t.TempDir()
	for index := 0; index < 4; index++ {
		path := filepath.Join(root, "file-"+string(rune('a'+index))+".txt")
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	job := indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	prior, err := engine.status(root, job.identity)
	if err != nil || prior["index_coverage"] != "complete" || prior["count"] != 4 {
		t.Fatalf("complete snapshot before bounded refresh = %#v, error=%v", prior, err)
	}
	job.maxFiles = 2
	if err := engine.build(job); err != nil {
		t.Fatalf("expected file-limit refresh to publish partial coverage, got error: %v", err)
	}
	status, err := engine.status(root, job.identity)
	if err != nil {
		t.Fatal(err)
	}
	if status["index_coverage"] != "partial" || status["count"] != 2 || status["error"] != indexCoverageFileLimit {
		t.Fatalf("bounded refresh status = %#v, want partial coverage for two entries", status)
	}
	if _, err := store.db.Exec(`UPDATE roots SET state='error' WHERE root=?`, root); err != nil {
		t.Fatal(err)
	}
	legacyStatus, err := engine.status(root, job.identity)
	if err != nil {
		t.Fatal(err)
	}
	if legacyStatus["index_state"] != "partial" || legacyStatus["stale"] != false || legacyStatus["error"] != indexCoverageFileLimit {
		t.Fatalf("legacy file-limit state projection = %#v, want searchable partial coverage", legacyStatus)
	}
	if err := store.markRootRefreshing(root, job.identity, legacyStatus["generation"].(int), time.Now().UnixNano()); err != nil {
		t.Fatal(err)
	}
	engine.mu.Lock()
	engine.builds[root] = true
	engine.mu.Unlock()
	refreshStatus, err := engine.status(root, job.identity)
	if err != nil {
		t.Fatal(err)
	}
	if refreshStatus["index_state"] != "partial" || refreshStatus["stale"] != false || refreshStatus["error"] != indexCoverageFileLimit || refreshStatus["building"] != true {
		t.Fatalf("legacy file-limit state during refresh = %#v, want current searchable partial coverage", refreshStatus)
	}
	engine.mu.Lock()
	delete(engine.builds, root)
	engine.mu.Unlock()
}

func TestSearchIndexCleansOrphanedGenerationsBeforeRefresh(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "current.md"), []byte("current"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	database := filepath.Join(t.TempDir(), "index.sqlite3")
	store, err := openSearchIndexStore(database)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	if _, err := store.db.Exec(`INSERT INTO roots(root,identity,generation,state,coverage) VALUES(?,?,0,'warming','pending')`, root, fileID(info)); err != nil {
		t.Fatal(err)
	}
	if _, err := store.db.Exec(`INSERT INTO root_generations(root,generation) VALUES(?,2)`, root); err != nil {
		t.Fatal(err)
	}
	tx, err := store.db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	paths := make([]discoveredIndexPath, 0, 8000)
	for index := 0; index < 8000; index++ {
		name := fmt.Sprintf("%04d-%s", index, strings.Repeat("x", 120))
		paths = append(paths, discoveredIndexPath{relative: name, name: name, identity: "orphan", size: 1, mtimeNS: 1})
	}
	if err := writeNormalizedPaths(tx, root, 2, paths); err != nil {
		tx.Rollback()
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	before, err := store.usedBytes()
	if err != nil {
		t.Fatal(err)
	}
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 100, maxBytes: 1024 * 1024}); err != nil {
		t.Fatalf("refresh with stale generations failed: %v", err)
	}
	after, err := store.usedBytes()
	if err != nil {
		t.Fatal(err)
	}
	status, err := engine.status(root, fileID(info))
	if err != nil {
		t.Fatal(err)
	}
	if status["generation"] != 3 || status["count"] != 1 || after >= before/2 {
		t.Fatalf("recovered index status=%#v size_before=%d size_after=%d", status, before, after)
	}
}

func TestSearchIndexReportsPartialCoverageBeforeSQLiteStorageLimit(t *testing.T) {
	root := t.TempDir()
	for index := 0; index < 3000; index++ {
		name := fmt.Sprintf("%04d-%0180d.txt", index, index)
		if err := os.WriteFile(filepath.Join(root, name), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 5000, maxBytes: 512 * 1024}); err != nil {
		t.Fatalf("storage-limited build returned an error instead of publishing partial coverage: %v", err)
	}
	status, err := engine.status(root, fileID(info))
	if err != nil {
		t.Fatal(err)
	}
	count, _ := status["count"].(int)
	if status["index_coverage"] != "partial" || count <= 0 || count >= 3000 {
		t.Fatalf("storage-limited index status = %#v, want partial coverage with some but not all files", status)
	}
}

func TestSearchIndexRefreshPreservesCoverageAtStorageLimit(t *testing.T) {
	root := t.TempDir()
	for index := 0; index < 12000; index++ {
		name := fmt.Sprintf("f%08d", index)
		if err := os.WriteFile(filepath.Join(root, name), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	job := indexBuildJob{root: root, identity: fileID(info), maxFiles: 15000, maxBytes: 4 * 1024 * 1024}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	prior, err := engine.status(root, job.identity)
	if err != nil || prior["index_coverage"] != "complete" || prior["count"] != 12000 {
		t.Fatalf("complete snapshot before storage-limited refresh = %#v, error=%v", prior, err)
	}
	job.maxBytes = 256 * 1024
	if err := engine.build(job); err == nil {
		t.Fatal("over-budget replacement was accepted")
	}
	status, err := engine.status(root, job.identity)
	if err != nil {
		t.Fatal(err)
	}
	if status["generation"] != prior["generation"] || status["count"] != 12000 || status["over_budget"] != true {
		t.Fatalf("rejected replacement lost retained snapshot: %#v", status)
	}
	results, err := engine.search(root, job.identity, "", 10, "", 0)
	if err != nil || results["count"] != 10 {
		t.Fatalf("retained snapshot not searchable: %#v %v", results, err)
	}
}

func TestSearchIndexRefreshUsesSharedBudgetAndPreservesRejectedGeneration(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < 6000; i++ {
		if err := os.WriteFile(filepath.Join(root, fmt.Sprintf("%04d-%s-techblog.md", i, strings.Repeat("x", 140))), []byte("x"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	job := indexBuildJob{root: root, identity: fileID(info), maxFiles: 8000, maxBytes: 2 * 1024 * 1024}
	for i := 0; i < 4; i++ {
		if err := engine.build(job); err != nil {
			t.Fatal(err)
		}
		status, err := engine.status(root, job.identity)
		if err != nil {
			t.Fatal(err)
		}
		if status["count"] != 6000 {
			t.Fatalf("refresh %d lost files: %#v", i, status)
		}
		allocated, err := store.allocatedBytes()
		if err != nil {
			t.Fatal(err)
		}
		if allocated > job.maxBytes {
			t.Fatalf("storage grew beyond budget: %d", allocated)
		}
	}
	// A second root must fit in the remaining snapshot budget even when the first uses over half.
	occupied, err := store.usedBytes()
	if err != nil {
		t.Fatal(err)
	}
	if occupied <= job.maxBytes/2 {
		t.Fatalf("fixture does not cross half-budget: %d", occupied)
	}
	secondRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(secondRoot, "second-techblog.md"), []byte("x"), 0600); err != nil {
		t.Fatal(err)
	}
	secondInfo, err := os.Stat(secondRoot)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.build(indexBuildJob{root: secondRoot, identity: fileID(secondInfo), maxFiles: 10, maxBytes: job.maxBytes}); err != nil {
		t.Fatal(err)
	}
	secondStatus, err := engine.status(secondRoot, fileID(secondInfo))
	if err != nil {
		t.Fatal(err)
	}
	if secondStatus["count"] != 1 {
		t.Fatalf("second root did not fit: %#v", secondStatus)
	}
	status, err := engine.status(root, job.identity)
	if err != nil {
		t.Fatal(err)
	}
	previousGeneration := status["generation"]
	job.maxFiles = 8000
	job.maxBytes = 256 * 1024
	if err := engine.build(job); err == nil {
		t.Fatal("replacement should be blocked")
	}
	status, err = engine.status(root, job.identity)
	if err != nil {
		t.Fatal(err)
	}
	if status["count"] != 6000 || status["generation"] != previousGeneration {
		t.Fatalf("rejected replacement lost coverage: %#v", status)
	}
	// A genuinely empty eligible scope must replace the old snapshot.
	job.excludePaths = []string{"glob:**"}
	job.maxFiles = 2000
	job.maxBytes = 2 * 1024 * 1024
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	status, err = engine.status(root, job.identity)
	if err != nil {
		t.Fatal(err)
	}
	if status["count"] != 0 || status["index_coverage"] != "complete" {
		t.Fatalf("empty root = %#v", status)
	}
}

// Synthetic ingestion measures database/ranking scale; it does not measure filesystem traversal.
func BenchmarkSearchIndexSyntheticScale(b *testing.B) {
	for _, count := range []int{20000, 50000, 100000, 1000000, 5000000} {
		b.Run(strconv.Itoa(count), func(b *testing.B) {
			for iteration := 0; iteration < b.N; iteration++ {
				store, err := openSearchIndexStore(filepath.Join(b.TempDir(), "index.sqlite3"))
				if err != nil {
					b.Fatal(err)
				}
				engine := newSearchIndexEngine(store)
				start := time.Now()
				for offset := 0; offset < count; offset += indexBatchSize {
					batch := make([][]any, 0, indexBatchSize)
					for i := offset; i < count && i < offset+indexBatchSize; i++ {
						name := fmt.Sprintf("%08d-techblog.md", i)
						batch = append(batch, []any{"/synthetic", 1, "notes/" + name, name, "identity", 1, 1})
					}
					if err := engine.writeBatch(batch); err != nil {
						b.Fatal(err)
					}
				}
				b.ReportMetric(float64(time.Since(start).Microseconds())/1000, "synthetic_ingest_ms")
				start = time.Now()
				if _, err := store.db.Exec(`INSERT INTO roots(root,identity,generation,state,coverage,count,bytes,updated_ns,settings_revision) VALUES('/synthetic','identity',1,'ready','complete',?,0,?,0)`, count, time.Now().UnixNano()); err != nil {
					b.Fatal(err)
				}
				b.ReportMetric(float64(time.Since(start).Microseconds())/1000, "publication_ms")
				start = time.Now()
				result, err := engine.search("/synthetic", "identity", "tech blog", 100, "", 0)
				if err != nil {
					b.Fatal(err)
				}
				if result["count"] != 100 {
					b.Fatalf("ranked results = %#v", result["count"])
				}
				b.ReportMetric(float64(time.Since(start).Microseconds())/1000, "search_ms")
				allocated, err := store.allocatedBytes()
				if err != nil {
					b.Fatal(err)
				}
				b.ReportMetric(float64(allocated)/(1024*1024), "storage_MiB")
				if status, err := os.ReadFile("/proc/self/status"); err == nil {
					for _, line := range strings.Split(string(status), "\n") {
						if strings.HasPrefix(line, "VmRSS:") {
							fields := strings.Fields(line)
							kb, _ := strconv.Atoi(fields[1])
							b.ReportMetric(float64(kb)/1024, "process_RSS_MiB")
						}
					}
				}
				if err := store.db.Close(); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

func BenchmarkSearchIndexCrawlScale(b *testing.B) {
	for _, count := range []int{20000, 50000, 100000, 1000000, 5000000} {
		b.Run(strconv.Itoa(count), func(b *testing.B) {
			for iteration := 0; iteration < b.N; iteration++ {
				root := filepath.Join(b.TempDir(), "root")
				if err := os.Mkdir(root, 0700); err != nil {
					b.Fatal(err)
				}
				begin := time.Now()
				for i := 0; i < count; i++ {
					file, err := os.Create(filepath.Join(root, fmt.Sprintf("%08d-techblog.md", i)))
					if err != nil {
						b.Fatal(err)
					}
					if err := file.Close(); err != nil {
						b.Fatal(err)
					}
				}
				b.ReportMetric(time.Since(begin).Seconds(), "fixture_seconds")
				info, err := os.Stat(root)
				if err != nil {
					b.Fatal(err)
				}
				store, err := openSearchIndexStore(filepath.Join(b.TempDir(), "index.sqlite3"))
				if err != nil {
					b.Fatal(err)
				}
				engine := newSearchIndexEngine(store)
				begin = time.Now()
				if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: DefaultIndexedFiles, maxBytes: DefaultIndexBytes}); err != nil {
					b.Fatal(err)
				}
				b.ReportMetric(time.Since(begin).Seconds(), "crawl_publish_seconds")
				status, err := engine.status(root, fileID(info))
				if err != nil {
					b.Fatal(err)
				}
				if status["count"] != min(count, DefaultIndexedFiles) {
					b.Fatalf("coverage: %#v", status)
				}
				b.ReportMetric(float64(status["count"].(int)), "covered_files")
				begin = time.Now()
				result, err := engine.search(root, fileID(info), "tech blog", 100, "", 0)
				if err != nil {
					b.Fatal(err)
				}
				if result["count"] != 100 {
					b.Fatalf("ranked count: %#v", result["count"])
				}
				b.ReportMetric(time.Since(begin).Seconds(), "search_seconds")
				if err := store.db.Close(); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}
