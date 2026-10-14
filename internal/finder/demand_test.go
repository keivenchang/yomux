// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"
)

func TestDemandLeaseRenewalAndMultipleClients(t *testing.T) {
	watcher := &countingNativeWatcher{events: make(chan nativeWatchEvent), status: nativeWatchStatus{available: true}}
	service, root := newTestServiceWithNative(t, nil, watcher, watcher.status)
	for _, client := range []string{"a", "b", "a", "b"} {
		if _, err := service.WatchRoots(context.Background(), map[string]any{"client_id": client, "roots": []any{root}}); err != nil {
			t.Fatal(err)
		}
	}
	if service.signatureScans != 0 || watcher.calls != 1 {
		t.Fatalf("renewal scans=%d registrations=%d", service.signatureScans, watcher.calls)
	}
	if _, err := service.WatchRoots(context.Background(), map[string]any{"client_id": "a", "roots": []any{}}); err != nil {
		t.Fatal(err)
	}
	if roots := service.DemandRoots(); len(roots) != 1 || roots[0] != root {
		t.Fatalf("another client's demand was lost: %v", roots)
	}
	service.pruneExpiredWatches(time.Now().Add(watchLeaseDuration + time.Second))
	if roots := service.DemandRoots(); len(roots) != 0 {
		t.Fatalf("expired demand retained: %v", roots)
	}
}

func TestFinderFallbackListingDoesNotScanDescendants(t *testing.T) {
	service, root := newTestServiceWithNative(t, nil, nil, nativeWatchStatus{})
	sub := filepath.Join(root, "directory")
	if err := os.Mkdir(sub, 0700); err != nil {
		t.Fatal(err)
	}
	payload := map[string]any{"client_id": "finder", "roots": []any{root}, "root_surfaces_version": 1, "root_surfaces": []any{map[string]any{"path": root, "surfaces": []any{"finder"}}}}
	if _, err := service.WatchRoots(context.Background(), payload); err != nil {
		t.Fatal(err)
	}
	first := service.watches["finder"].Signatures[root]
	if err := os.WriteFile(filepath.Join(sub, "deep.go"), []byte("before"), 0600); err != nil {
		t.Fatal(err)
	}
	record := service.watches["finder"]
	baseline := service.watchPathSignature(context.Background(), root, record)
	if err := os.WriteFile(filepath.Join(sub, "deep.go"), []byte("after changed"), 0600); err != nil {
		t.Fatal(err)
	}
	if after := service.watchPathSignature(context.Background(), root, record); after != baseline {
		t.Fatalf("descendant contents changed listing signature: %q -> %q (initial %q)", baseline, after, first)
	}
	scans := service.signatureScans
	if _, err := service.WatchRoots(context.Background(), payload); err != nil {
		t.Fatal(err)
	}
	if service.signatureScans != scans {
		t.Fatal("renewal rebuilt fallback baseline")
	}
}

func TestIncrementalIndexMatchesFullDiscovery(t *testing.T) {
	for _, exclusion := range []string{"", "glob:skip/**", "regex:^skip/", "skip"} {
		t.Run(exclusion, func(t *testing.T) {
			root := t.TempDir()
			store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.db.Close()
			write := func(relative string) {
				path := filepath.Join(root, relative)
				if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte(relative), 0600); err != nil {
					t.Fatal(err)
				}
			}
			for _, path := range []string{"keep/a.go", "keep/b.go", "old/c.go", "skip/a.go"} {
				write(path)
			}
			info, err := os.Stat(root)
			if err != nil {
				t.Fatal(err)
			}
			job := indexBuildJob{root: root, identity: fileID(info), maxFiles: 100, maxBytes: DefaultIndexBytes, excludeDirs: map[string]struct{}{}, excludePaths: []string{exclusion}}
			engine := newSearchIndexEngine(store)
			if err := engine.build(job); err != nil {
				t.Fatal(err)
			}
			write("build.o")
			bare := filepath.Join(root, "bare")
			for _, directory := range []string{"objects/pack", "refs/heads"} {
				if err := os.MkdirAll(filepath.Join(bare, directory), 0700); err != nil {
					t.Fatal(err)
				}
			}
			for name, contents := range map[string]string{
				"HEAD":   "ref: refs/heads/main\n",
				"config": "[core]\n\tbare = true\n",
			} {
				if err := os.WriteFile(filepath.Join(bare, name), []byte(contents), 0600); err != nil {
					t.Fatal(err)
				}
			}
			write("bare/refs/heads/main")
			if err := os.Rename(filepath.Join(root, "old"), filepath.Join(root, "new")); err != nil {
				t.Fatal(err)
			}
			if err := os.Remove(filepath.Join(root, "keep/a.go")); err != nil {
				t.Fatal(err)
			}
			write("added/nested/d.go")
			write("skip/new.go")
			job.changedPaths = []string{filepath.Join(root, "old"), filepath.Join(root, "new"), filepath.Join(root, "keep/a.go"), filepath.Join(root, "added"), filepath.Join(root, "skip"), filepath.Join(root, "build.o"), bare}
			if err := engine.build(job); err != nil {
				t.Fatal(err)
			}
			var generation int
			if err := store.db.QueryRow(`SELECT generation FROM roots WHERE root=?`, root).Scan(&generation); err != nil {
				t.Fatal(err)
			}
			indexed, err := store.loadNormalizedPaths(context.Background(), root, generation)
			if err != nil {
				t.Fatal(err)
			}
			actual := indexed
			full, coverage, reason, err := discoverIndexPaths(context.Background(), root, job.excludeDirs, job.excludePaths, job.maxFiles)
			if err != nil || coverage != "complete" {
				t.Fatalf("full discovery %s %s %v", coverage, reason, err)
			}
			sort.Slice(full, func(i, j int) bool { return full[i].relativePath() < full[j].relativePath() })
			if !reflect.DeepEqual(actual, full) {
				t.Fatalf("incremental records=%+v full records=%+v", actual, full)
			}
			if engine.fullDiscoveries != 1 || engine.incrementalDiscoveries != 1 {
				t.Fatalf("discovery counters full=%d delta=%d", engine.fullDiscoveries, engine.incrementalDiscoveries)
			}
		})
	}
}

func TestIncrementalIndexReconcilesBareRepositoryPolicyTransitions(t *testing.T) {
	root := t.TempDir()
	repository := filepath.Join(root, "repo")
	for _, directory := range []string{"objects/pack", "refs/heads"} {
		if err := os.MkdirAll(filepath.Join(repository, directory), 0700); err != nil {
			t.Fatal(err)
		}
	}
	for name, contents := range map[string]string{
		"HEAD":   "ref: refs/heads/main\n",
		"config": "[core]\n\tbare = false\n",
	} {
		if err := os.WriteFile(filepath.Join(repository, name), []byte(contents), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for name, contents := range map[string]string{
		"refs/heads/main": "commit-id\n",
		"readme.txt":      "searchable repository contents\n",
	} {
		path := filepath.Join(repository, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(contents), 0600); err != nil {
			t.Fatal(err)
		}
	}
	rootInfo, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	engine := newSearchIndexEngine(store)
	job := indexBuildJob{root: root, identity: fileID(rootInfo), maxFiles: 100, maxBytes: DefaultIndexBytes, excludeDirs: map[string]struct{}{}}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(repository, "config")
	for _, bare := range []bool{true, false} {
		contents := "[core]\n\tbare = false\n"
		if bare {
			contents = "[core]\n\tbare = true\n"
		}
		if err := os.WriteFile(configPath, []byte(contents), 0600); err != nil {
			t.Fatal(err)
		}
		job.changedPaths = []string{configPath}
		if err := engine.build(job); err != nil {
			t.Fatal(err)
		}
		var generation int
		if err := store.db.QueryRow(`SELECT generation FROM roots WHERE root=?`, root).Scan(&generation); err != nil {
			t.Fatal(err)
		}
		incremental, err := store.loadNormalizedPaths(context.Background(), root, generation)
		if err != nil {
			t.Fatal(err)
		}
		full, coverage, reason, err := discoverIndexPaths(context.Background(), root, job.excludeDirs, job.excludePaths, job.maxFiles)
		if err != nil || coverage != "complete" {
			t.Fatalf("full discovery %s %s %v", coverage, reason, err)
		}
		sort.Slice(full, func(i, j int) bool { return full[i].relativePath() < full[j].relativePath() })
		if len(incremental) != len(full) || len(full) > 0 && !reflect.DeepEqual(incremental, full) {
			t.Fatalf("bare=%t incremental records=%+v full records=%+v", bare, incremental, full)
		}
	}
	if err := os.WriteFile(configPath, []byte("[core]\n\tbare = true\n"), 0600); err != nil {
		t.Fatal(err)
	}
	job.changedPaths = []string{configPath}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	headPath := filepath.Join(repository, "HEAD")
	for _, path := range []string{headPath, configPath} {
		if err := os.Remove(path); err != nil {
			t.Fatal(err)
		}
	}
	job.changedPaths = []string{headPath, configPath}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	var generation int
	if err := store.db.QueryRow(`SELECT generation FROM roots WHERE root=?`, root).Scan(&generation); err != nil {
		t.Fatal(err)
	}
	incremental, err := store.loadNormalizedPaths(context.Background(), root, generation)
	if err != nil {
		t.Fatal(err)
	}
	full, coverage, reason, err := discoverIndexPaths(context.Background(), root, job.excludeDirs, job.excludePaths, job.maxFiles)
	if err != nil || coverage != "complete" {
		t.Fatalf("full discovery after marker deletion %s %s %v", coverage, reason, err)
	}
	sort.Slice(full, func(i, j int) bool { return full[i].relativePath() < full[j].relativePath() })
	if len(incremental) != len(full) || len(full) > 0 && !reflect.DeepEqual(incremental, full) {
		t.Fatalf("marker deletion incremental records=%+v full records=%+v", incremental, full)
	}
	if engine.fullDiscoveries != 1 || engine.incrementalDiscoveries != 4 {
		t.Fatalf("discovery counters full=%d delta=%d", engine.fullDiscoveries, engine.incrementalDiscoveries)
	}
}

func TestFallbackPollingSharesIdenticalScopesAcrossClients(t *testing.T) {
	service, root := newTestServiceWithNative(t, nil, nil, nativeWatchStatus{})
	for _, client := range []string{"a", "b"} {
		if _, err := service.WatchRoots(context.Background(), map[string]any{"client_id": client, "roots": []any{root}}); err != nil {
			t.Fatal(err)
		}
	}
	service.mu.Lock()
	before := service.signatureScans
	service.mu.Unlock()
	service.pollWatches()
	service.mu.Lock()
	after := service.signatureScans
	service.mu.Unlock()
	if after-before != 1 {
		t.Fatalf("shared scope scanned %d times", after-before)
	}
}
