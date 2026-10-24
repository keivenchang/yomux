// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"
)

func makeV1Index(t *testing.T, path, root string, count int) []discoveredIndexPath {
	t.Helper()
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, query := range []string{
		`PRAGMA user_version=1`,
		`CREATE TABLE roots(root TEXT PRIMARY KEY,identity TEXT NOT NULL,generation INTEGER NOT NULL,state TEXT NOT NULL,coverage TEXT NOT NULL,count INTEGER NOT NULL,bytes INTEGER NOT NULL,updated_ns INTEGER NOT NULL,error TEXT NOT NULL,settings_revision INTEGER NOT NULL)`,
		`CREATE TABLE root_generations(root TEXT PRIMARY KEY,generation INTEGER NOT NULL)`,
		`CREATE TABLE settings_fences(root TEXT PRIMARY KEY,revision INTEGER NOT NULL)`,
		`CREATE TABLE settings_state(singleton INTEGER PRIMARY KEY,revision INTEGER NOT NULL)`,
		`CREATE TABLE entries(root TEXT NOT NULL,generation INTEGER NOT NULL,relative_path TEXT NOT NULL,name TEXT NOT NULL,file_identity TEXT NOT NULL,size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,PRIMARY KEY(root,generation,relative_path)) WITHOUT ROWID`,
		`CREATE INDEX entries_by_name ON entries(root,generation,name)`,
		`CREATE TABLE obsolete_python_snapshots(content TEXT)`,
		`INSERT INTO obsolete_python_snapshots VALUES('retained original')`,
	} {
		if _, err := db.Exec(query); err != nil {
			t.Fatal(err)
		}
	}
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO roots VALUES(?,?,7,'ready','complete',?,0,0,'',41)`, root, fileID(info), count); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO root_generations VALUES(?,19)`, root); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO settings_state VALUES(1,41)`); err != nil {
		t.Fatal(err)
	}
	tx, err := db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	entries := make([]discoveredIndexPath, 0, count)
	for index := 0; index < count; index++ {
		name := fmt.Sprintf("%06d-techblog-café.md", index)
		if index == 0 {
			name = "2026.md"
		}
		relative := fmt.Sprintf("deep/shared-directory-prefix-%s/subdirectory-%03d/%s", strings.Repeat("long", 20), index%10, name)
		entry := discoveredIndexPath{relative: relative, name: name, identity: "fixture", size: int64(index), mtimeNS: int64(index) * 1000}
		entries = append(entries, entry)
		if _, err := tx.Exec(`INSERT INTO entries VALUES(?,7,?,?,?,?,?)`, root, relative, name, entry.identity, entry.size, entry.mtimeNS); err != nil {
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`VACUUM`); err != nil {
		t.Fatal(err)
	}
	return entries
}

func TestSearchIndexV2MigrationRecordsRankingSizeAndRollback(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(t.TempDir(), "index.sqlite3")
	entries := makeV1Index(t, path, root, 5000)
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	store, err := openSearchIndexStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	engine := newSearchIndexEngine(store)
	info, _ := os.Stat(root)
	for _, query := range []string{"", "2026.md", "tech blog", "tchblg", "shared-directory", "café", "subdirectory 2026"} {
		expected := []discoveredIndexPath{}
		for _, entry := range entries {
			if _, matched := v1EntrySortKey(entry.name, entry.relative, prepareSearchTokens(strings.Fields(query))); matched {
				expected = append(expected, entry)
			}
		}
		sort.Slice(expected, func(a, b int) bool {
			left, _ := v1EntrySortKey(expected[a].name, expected[a].relative, prepareSearchTokens(strings.Fields(query)))
			right, _ := v1EntrySortKey(expected[b].name, expected[b].relative, prepareSearchTokens(strings.Fields(query)))
			return compareSearchSortKey(left, right)
		})
		result, err := engine.search(root, fileID(info), query, MaxSearchResults, "", 41)
		if err != nil {
			t.Fatal(err)
		}
		rows := result["files"].([]map[string]any)
		if len(rows) != min(len(expected), MaxSearchResults) {
			t.Fatalf("query %q count mismatch", query)
		}
		for index, row := range rows {
			if row["relative_path"] != expected[index].relative {
				t.Fatalf("query %q row %d mismatch", query, index)
			}
		}
	}
	if err := checkpointIndex(store.db); err != nil {
		t.Fatal(err)
	}
	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if after.Size() >= before.Size() {
		t.Fatalf("v2=%d, compact v1=%d", after.Size(), before.Size())
	}
	t.Logf("identical 5000-entry corpus: compact v1=%d v2=%d", before.Size(), after.Size())
	backups, err := filepath.Glob(path + ".rollback-*")
	if err != nil || len(backups) != 1 {
		t.Fatalf("backup files: %v %v", backups, err)
	}
	original, err := readOnlyIndex(backups[0])
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	var originalData string
	if err := original.QueryRow(`SELECT content FROM obsolete_python_snapshots`).Scan(&originalData); err != nil || originalData != "retained original" {
		t.Fatalf("rollback data: %q %v", originalData, err)
	}
	var obsolete int
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE name='obsolete_python_snapshots'`).Scan(&obsolete); err != nil || obsolete != 0 {
		t.Fatalf("obsolete table copied: %d %v", obsolete, err)
	}
	var highWater int
	if err := store.db.QueryRow(`SELECT generation FROM root_generations WHERE root=?`, root).Scan(&highWater); err != nil || highWater != 19 {
		t.Fatalf("lost generation high water: %d %v", highWater, err)
	}
}

func TestSearchIndexV2ReclamationDoesNotRescanFilesPerDirectory(t *testing.T) {
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	rows, err := store.db.Query("EXPLAIN QUERY PLAN " + reclaimDirectoriesSQL)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	type planStep struct {
		parent int
		detail string
	}
	steps := make(map[int]planStep)
	for rows.Next() {
		var id, parent, unused int
		var detail string
		if err := rows.Scan(&id, &parent, &unused, &detail); err != nil {
			t.Fatal(err)
		}
		steps[id] = planStep{parent, detail}
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	fileScans := 0
	for _, step := range steps {
		if step.detail != "SCAN entries" && step.detail != "SCAN e" {
			continue
		}
		fileScans++
		for parent := step.parent; parent != 0; parent = steps[parent].parent {
			if strings.Contains(steps[parent].detail, "CORRELATED") {
				t.Fatalf("file ownership is scanned separately for each directory: %v", steps)
			}
		}
	}
	if fileScans != 1 {
		t.Fatalf("expected one measured file ownership scan: %v", steps)
	}
}

func TestSearchIndexV2MigrationInterruptedPhasesRecover(t *testing.T) {
	for _, phase := range []string{"building", "backed_up", "published"} {
		t.Run(phase, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "index.sqlite3")
			root := t.TempDir()
			makeV1Index(t, path, root, 20)
			originalHook := indexMigrationPhase
			indexMigrationPhase = func(current string) error {
				if current == phase {
					return errors.New("injected interruption")
				}
				return nil
			}
			_, err := openSearchIndexStore(path)
			indexMigrationPhase = originalHook
			if err == nil {
				t.Fatal("injected interruption was ignored")
			}
			store, err := openSearchIndexStore(path)
			if err != nil {
				t.Fatal(err)
			}
			defer store.db.Close()
			var count int
			if err := store.db.QueryRow(`SELECT COUNT(*) FROM entries`).Scan(&count); err != nil || count != 20 {
				t.Fatalf("recovery lost records: %d %v", count, err)
			}
		})
	}
}

func TestSearchIndexV2NewerSchemaRefusalDoesNotCreateSidecars(t *testing.T) {
	for _, walMode := range []bool{false, true} {
		t.Run(fmt.Sprint(walMode), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "index.sqlite3")
			db, err := sql.Open("sqlite3", path)
			if err != nil {
				t.Fatal(err)
			}
			if walMode {
				if _, err := db.Exec(`PRAGMA journal_mode=WAL`); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := db.Exec(`PRAGMA user_version=3`); err != nil {
				t.Fatal(err)
			}
			if !walMode {
				db.Close()
			}
			before := snapshotIndexInodes(t, path)
			beforeFiles, _ := os.ReadDir(filepath.Dir(path))
			_, err = openSearchIndexStore(path)
			if err == nil {
				t.Fatal("newer schema accepted")
			}
			after := snapshotIndexInodes(t, path)
			afterFiles, _ := os.ReadDir(filepath.Dir(path))
			if !reflect.DeepEqual(before, after) || len(beforeFiles) != len(afterFiles) {
				t.Fatal("newer schema inspection mutated source")
			}
			db.Close()
		})
	}
}

func TestSearchIndexV2SharedAncestryAndAcknowledgmentIdempotence(t *testing.T) {
	root := t.TempDir()
	nested := filepath.Join(root, "shared")
	os.Mkdir(nested, 0700)
	os.WriteFile(filepath.Join(nested, "2026.md"), nil, 0600)
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	for _, scope := range []string{root, nested} {
		info, _ := os.Stat(scope)
		if err := engine.build(indexBuildJob{root: scope, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes, settingsRevision: 41}); err != nil {
			t.Fatal(err)
		}
	}
	var directories int
	if err := store.db.QueryRow(`SELECT COUNT(DISTINCT directory) FROM entries`).Scan(&directories); err != nil || directories != 1 {
		t.Fatalf("ancestry not shared: %d %v", directories, err)
	}
	request := indexRequest{Action: "settings_fence", FenceRoots: []string{root}, SettingsRevision: 42, ApplicationEpoch: 2, PreserveSnapshots: true}
	if _, err := engine.handle(request); err != nil {
		t.Fatal(err)
	}
	version := engine.mutationGeneration
	if _, err := engine.handle(request); err != nil {
		t.Fatal(err)
	}
	if engine.mutationGeneration != version {
		t.Fatal("unchanged acknowledgment invalidated builds")
	}
	request.ApplicationEpoch = 1
	if _, err := engine.handle(request); err == nil {
		t.Fatal("older application epoch accepted")
	}
	info, _ := os.Stat(root)
	response, err := engine.search(root, fileID(info), "2026.md", 10, "", 42)
	if err != nil || response["snapshot_revision"] != "41" || response["active_revision"] != "42" || response["count"] != 1 {
		t.Fatalf("compatible snapshot: %#v %v", response, err)
	}
	if !engine.needsRefresh(root, fileID(info), 3600, 42, time.Now()) {
		t.Fatal("compatible policy did not request reconciliation")
	}
}

func TestSearchIndexV2ExclusionAndFallbackParity(t *testing.T) {
	root := t.TempDir()
	for name, data := range map[string][]byte{"2026.md": []byte("notes"), "script": []byte("#!/bin/sh\necho ok\n"), "program": []byte("\x7fELFfixture"), "image.png": []byte("asset"), "data.bin": []byte("asset"), "library.so": []byte("binary"), "code.o": []byte("binary"), "ignored/skip.md": nil, "node_modules/dependency.md": nil, "lookalike/HEAD": []byte("ref: refs/heads/main"), "lookalike/config": []byte("ordinary user config")} {
		path := filepath.Join(root, name)
		os.MkdirAll(filepath.Dir(path), 0700)
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	bare := filepath.Join(root, "bare")
	for _, directory := range []string{"objects/info", "objects/pack", "refs/heads"} {
		os.MkdirAll(filepath.Join(bare, directory), 0700)
	}
	os.WriteFile(filepath.Join(bare, "HEAD"), []byte("ref: refs/heads/main\n"), 0600)
	os.WriteFile(filepath.Join(bare, "config"), []byte("[core]\n bare = true\n"), 0600)
	os.Symlink(filepath.Join(root, "2026.md"), filepath.Join(root, "alias.md"))
	options := SearchOptions{ExcludePaths: []string{"glob:**/ignored/**"}}
	paths, coverage, reason, err := discoverIndexPaths(context.Background(), root, nil, options.ExcludePaths, 100)
	if err != nil || coverage != "complete" {
		t.Fatalf("discovery: %s %s %v", coverage, reason, err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	response, finderErr := service.SearchWithOptions(context.Background(), root, "", 100, true, false, true, options)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	actual := []string{}
	for _, row := range response["files"].([]map[string]any) {
		actual = append(actual, row["relative_path"].(string))
	}
	expected := []string{}
	for _, path := range paths {
		expected = append(expected, path.relativePath())
	}
	sort.Strings(actual)
	sort.Strings(expected)
	if !reflect.DeepEqual(actual, expected) {
		t.Fatalf("discovery %v fallback %v", expected, actual)
	}
	if !reflect.DeepEqual(actual, []string{"2026.md", "data.bin", "image.png", "lookalike/HEAD", "lookalike/config", "script"}) {
		t.Fatalf("eligible files: %v", actual)
	}
	for _, recursive := range []bool{false, true} {
		result, err := service.SearchWithOptions(context.Background(), bare, "", 100, recursive, false, true, options)
		if err != nil || result["count"] != 0 {
			t.Fatalf("bare root leaked: %#v %v", result, err)
		}
	}
}

func TestSearchIndexV2RetainedStagingBlocksGrowth(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "2026.md"), nil, 0600)
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	staging, err := os.Create(store.path + ".staging-abandoned")
	if err != nil {
		t.Fatal(err)
	}
	if err := staging.Truncate(indexTransientCeiling); err != nil {
		t.Fatal(err)
	}
	staging.Close()
	engine := newSearchIndexEngine(store)
	info, _ := os.Stat(root)
	if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}); err == nil {
		t.Fatal("abandoned staging did not block admission")
	}
	usage, err := store.diskUsage()
	if err != nil || usage["staging_bytes"] != indexTransientCeiling {
		t.Fatalf("staging usage: %v %v", usage, err)
	}
}

func TestSearchIndexV2RecoveryQuarantinesCorruptWAL(t *testing.T) {
	path := filepath.Join(t.TempDir(), "index.sqlite3")
	root := t.TempDir()
	makeV1Index(t, path, root, 1)
	store, err := openSearchIndexStore(path)
	if err != nil {
		t.Fatal(err)
	}
	store.db.Close()
	raw, _ := os.ReadFile(path + ".publication.json")
	var publication indexPublication
	json.Unmarshal(raw, &publication)
	if err := os.WriteFile(path, []byte("corrupt publication"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path+"-wal", []byte("unrelated corrupt WAL"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := recoverIndexPublication(path); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path + "-wal"); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("foreign WAL remained at restored basename")
	}
	original, err := readOnlyIndex(path)
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	var value string
	if err := original.QueryRow(`SELECT content FROM obsolete_python_snapshots`).Scan(&value); err != nil || value != "retained original" {
		t.Fatalf("wrong rollback contents: %q %v", value, err)
	}
}

func TestSearchIndexV2WorkerHelperProcess(t *testing.T) {
	if os.Getenv("YOMUX_TEST_INDEX_WORKER") != "1" {
		return
	}
	path := ""
	for index, arg := range os.Args {
		if strings.HasPrefix(arg, "--blocked-policy-worker=") {
			if arg == "--blocked-policy-worker=request" {
				if _, err := bufio.NewReader(os.Stdin).ReadBytes('\n'); err != nil {
					os.Exit(1)
				}
			}
			reader, writer, err := os.Pipe()
			if err != nil {
				os.Exit(1)
			}
			_, _ = reader.Read(make([]byte, 1))
			runtime.KeepAlive(writer)
			os.Exit(0)
		}
		if arg == "--notification-index-worker" {
			fmt.Fprintln(os.Stdout, `{"id":0,"ok":true,"payload":{"root":"/held"}}`)
			_, _ = io.Copy(io.Discard, os.Stdin)
			os.Exit(0)
		}
		if arg == "--unresponsive-index-worker" {
			_, _ = io.Copy(io.Discard, os.Stdin)
			os.Exit(0)
		}
		if arg == "--index-db" && index+1 < len(os.Args) {
			path = os.Args[index+1]
		}
	}
	if err := RunSearchIndexWorker(os.Stdin, os.Stdout, path); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	os.Exit(0)
}

func TestSearchIndexV2UnresponsiveStartupRespectsSettingsDeadline(t *testing.T) {
	t.Setenv("YOMUX_TEST_INDEX_WORKER", "1")
	originalRunner := execCommand
	execCommand = func(_ string, args ...string) *exec.Cmd {
		return exec.Command(os.Args[0], append([]string{"-test.run=^TestSearchIndexV2WorkerHelperProcess$", "--", "--unresponsive-index-worker"}, args...)...)
	}
	defer func() { execCommand = originalRunner }()
	client := NewSearchIndexWorkerClient(t.TempDir())
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	_, err := client.ApplySettings(ctx, nil, 41, 1, true, DefaultIndexBytes, true)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("unresponsive startup returned %v", err)
	}
	if client.cmd != nil {
		t.Fatal("canceled startup left an unresponsive worker installed")
	}
	entries, err := os.ReadDir(client.stateDir)
	if err != nil || len(entries) != 0 {
		t.Fatalf("unresponsive startup touched storage: %v %v", entries, err)
	}
}

func TestSearchIndexV2BlockedPolicyWritesRespectSettingsDeadline(t *testing.T) {
	t.Setenv("YOMUX_TEST_INDEX_WORKER", "1")
	originalRunner := execCommand
	defer func() { execCommand = originalRunner }()
	paths := make([]string, 256)
	for index := range paths {
		paths[index] = fmt.Sprintf("glob:**/%03d-%s", index, strings.Repeat("a", 1024))
	}
	for _, phase := range []string{"initial", "request"} {
		t.Run(phase, func(t *testing.T) {
			execCommand = func(_ string, args ...string) *exec.Cmd {
				return exec.Command(os.Args[0], append([]string{"-test.run=^TestSearchIndexV2WorkerHelperProcess$", "--", "--blocked-policy-worker=" + phase}, args...)...)
			}
			client := NewSearchIndexWorkerClient(t.TempDir())
			defer client.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
			defer cancel()
			_, err := client.ApplySettings(ctx, nil, 41, 1, true, DefaultIndexBytes, true, SearchOptions{ExcludePaths: paths})
			if !errors.Is(err, context.DeadlineExceeded) || client.cmd != nil {
				t.Fatalf("blocked %s frame did not retire its worker: %v", phase, err)
			}
			entries, err := os.ReadDir(client.stateDir)
			if err != nil || len(entries) != 0 {
				t.Fatalf("blocked bootstrap touched storage: %v %v", entries, err)
			}
		})
	}
}

func TestSearchIndexV2SettingsLockWaitRespectsDeadline(t *testing.T) {
	for _, lockName := range []string{"repair", "request"} {
		t.Run(lockName, func(t *testing.T) {
			client := NewSearchIndexWorkerClient(t.TempDir())
			var release func()
			if lockName == "repair" {
				client.repairMu.Lock()
				release = client.repairMu.Unlock
			} else {
				client.mu.Lock()
				release = client.mu.Unlock
			}

			ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
			defer cancel()
			started := time.Now()
			_, err := client.ApplySettings(ctx, nil, 41, 1, true, DefaultIndexBytes, true)
			release()
			client.Close()
			if !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("settings application waiting for %s lock returned %v", lockName, err)
			}
			if elapsed := time.Since(started); elapsed > 500*time.Millisecond {
				t.Fatalf("settings application waited %s for %s lock past its deadline", elapsed, lockName)
			}
			if client.cmd != nil {
				t.Fatal("settings timeout started a worker")
			}
		})
	}
}

func TestSearchIndexV2WorkerRequiresInitialPolicyBeforeStorageAccess(t *testing.T) {
	state := t.TempDir()
	path := filepath.Join(state, "search-index.sqlite3")
	original := []byte("unopened storage sentinel")
	if err := os.WriteFile(path, original, 0600); err != nil {
		t.Fatal(err)
	}
	client := NewSearchIndexWorkerClient(state)
	defer client.Close()
	for _, action := range []string{"status", "search", "ensure", "repair"} {
		_, err := client.Request(context.Background(), indexRequest{Action: action, Root: state})
		if err == nil || !strings.Contains(err.Error(), "initial policy is required") {
			t.Fatalf("%s without settings: %v", action, err)
		}
	}
	if client.cmd != nil {
		t.Fatal("unconfigured request started a worker")
	}
	contents, err := os.ReadFile(path)
	if err != nil || string(contents) != string(original) {
		t.Fatalf("unconfigured request changed storage: %v", err)
	}
	entries, err := os.ReadDir(state)
	if err != nil || len(entries) != 1 {
		t.Fatalf("unconfigured request created sidecars: %v %v", entries, err)
	}
}

func TestSearchIndexV2WorkerRestartAndPersistenceTogglePreserveDisk(t *testing.T) {
	t.Setenv("YOMUX_TEST_INDEX_WORKER", "1")
	originalRunner := execCommand
	execCommand = func(_ string, args ...string) *exec.Cmd {
		return exec.Command(os.Args[0], append([]string{"-test.run=^TestSearchIndexV2WorkerHelperProcess$", "--"}, args...)...)
	}
	defer func() { execCommand = originalRunner }()
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "2026.md"), nil, 0600)
	state := t.TempDir()
	client := NewSearchIndexWorkerClient(state)
	defer client.Close()
	options := SearchOptions{MaxFiles: 1000, RefreshSec: 60}
	ack, err := client.ApplySettings(context.Background(), []string{root}, 41, 1, true, DefaultIndexBytes, true, options)
	if err != nil {
		t.Fatal(err)
	}
	if ack["application_epoch"] != "1" {
		t.Fatalf("ack epoch: %v", ack)
	}
	info, _ := os.Stat(root)
	request := indexRequest{Action: "ensure", Root: root, Identity: fileID(info), SettingsRevision: 41, MaxFiles: 1000, MaxBytes: DefaultIndexBytes}
	if _, err := client.Request(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for {
		payload, err := client.Request(context.Background(), indexRequest{Action: "status", Root: root, Identity: fileID(info)})
		if err != nil {
			t.Fatal(err)
		}
		if payload["count"] == float64(1) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("worker did not publish: %v", payload)
		}
		runtime.Gosched()
	}
	client.mu.Lock()
	client.stopLocked()
	client.mu.Unlock()
	restarted, err := client.ApplySettings(context.Background(), []string{root}, 42, 2, true, DefaultIndexBytes, true, options)
	if err != nil {
		t.Fatal(err)
	}
	if restarted["worker_incarnation"] == ack["worker_incarnation"] {
		t.Fatal("restart did not change incarnation")
	}
	request.Action = "search"
	request.SettingsRevision = 42
	request.RefreshSec = 3600
	request.Query = "2026.md"
	request.Limit = 10
	result, err := client.Request(context.Background(), request)
	if err != nil || result["count"] != float64(1) {
		t.Fatalf("restart lost searchable snapshot: %v %v", result, err)
	}
	if _, err := client.ApplySettings(context.Background(), []string{root}, 43, 3, false, DefaultIndexBytes, true, options); err != nil {
		t.Fatal(err)
	}
	disk, err := readOnlyIndex(filepath.Join(state, "search-index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	var count int
	if err := disk.QueryRow(`SELECT COUNT(*) FROM entries`).Scan(&count); err != nil || count != 1 {
		disk.Close()
		t.Fatalf("persistence off purged disk: %d %v", count, err)
	}
	disk.Close()
	if _, err := client.ApplySettings(context.Background(), []string{root}, 44, 4, true, DefaultIndexBytes, true, options); err != nil {
		t.Fatal(err)
	}
	result, err = client.Request(context.Background(), indexRequest{Action: "status", Root: root, Identity: fileID(info)})
	if err != nil || result["count"] != float64(1) {
		t.Fatalf("persistence on lost disk snapshot: %v %v", result, err)
	}
}

// Frozen v1 path-ranking oracle from main at 49bade3cd81af96c02a4b04bce86d307e1274931.
// Basename ranking and token preparation were not changed by the v2 cutover.
func v1TokenRank(token preparedSearchToken, name, relative string) (int, bool) {
	if rank, ok := searchTokenNameRankPrepared(token, name); ok {
		return rank, true
	}
	for start := 0; start <= len(relative); {
		end := strings.IndexByte(relative[start:], '/')
		if end < 0 {
			end = len(relative)
		} else {
			end += start
		}
		segment := relative[start:end]
		if token.doitMode {
			if index := strings.Index(alnumSearchText(segment), token.doitNeedle); index >= 0 {
				return 90 + index, true
			}
		} else {
			segmentText := compactSearchText(segment)
			if index := strings.Index(segmentText, token.needle); index >= 0 {
				return 90 + index, true
			}
			if span, ok := fuzzySubsequenceSpanPrepared(token.needle, segmentText); ok {
				return 130 + strings.Count(relative, "/")*4 + span, true
			}
		}
		if end == len(relative) {
			break
		}
		start = end + 1
	}
	return 0, false
}

func v1EntrySortKey(name, relative string, tokens []preparedSearchToken) (searchSortKey, bool) {
	key := searchSortKey{minimum: 0, total: 0, negative: 0, depth: strings.Count(relative, "/"), length: len(relative), lowerName: strings.ToLower(relative)}
	if len(tokens) == 0 {
		return key, true
	}
	key.minimum = 1 << 30
	for _, token := range tokens {
		rank, ok := v1TokenRank(token, name, relative)
		if !ok {
			return searchSortKey{}, false
		}
		if rank < key.minimum {
			key.minimum = rank
		}
		key.total += rank
		if rank < 90 {
			key.negative--
		}
	}
	return key, true
}

func snapshotIndexInodes(t *testing.T, path string) map[string][]byte {
	t.Helper()
	result := make(map[string][]byte)
	for _, suffix := range []string{"", "-wal", "-shm"} {
		data, err := os.ReadFile(path + suffix)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		result[suffix] = data
	}
	return result
}

func TestSearchIndexV2CrashedNewerSchemaIsReadWithoutMutation(t *testing.T) {
	if os.Getenv("YOMUX_TEST_CRASHED_SCHEMA") != "" {
		db, err := sql.Open("sqlite3", os.Getenv("YOMUX_TEST_CRASHED_SCHEMA"))
		if err != nil {
			os.Exit(1)
		}
		if _, err := db.Exec(`PRAGMA journal_mode=WAL`); err != nil {
			os.Exit(2)
		}
		if _, err := db.Exec(`PRAGMA user_version=3`); err != nil {
			os.Exit(3)
		}
		os.Exit(0)
	}
	path := filepath.Join(t.TempDir(), "index.sqlite3")
	command := exec.Command(os.Args[0], "-test.run=^TestSearchIndexV2CrashedNewerSchemaIsReadWithoutMutation$")
	command.Env = append(withSearchIndexWorkerCPUCap(os.Environ()), "YOMUX_TEST_CRASHED_SCHEMA="+path)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("crash fixture: %s %v", output, err)
	}
	before := snapshotIndexInodes(t, path)
	if _, err := openSearchIndexStore(path); err == nil {
		t.Fatal("newer committed WAL schema accepted")
	}
	after := snapshotIndexInodes(t, path)
	if !reflect.DeepEqual(before, after) {
		t.Fatal("newer schema probe changed database/sidecars")
	}
}

func TestSearchIndexExclusionPathFollowsSymlinkRootAlias(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "excluded"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "excluded", "secret.md"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	canonicalRoot, err := CanonicalIndexRoot(root)
	if err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(t.TempDir(), "root-alias")
	if err := os.Symlink(canonicalRoot, alias); err != nil {
		t.Skipf("directory symlinks unavailable: %v", err)
	}
	paths, _, _, err := discoverIndexPaths(context.Background(), canonicalRoot, nil, []string{filepath.Join(alias, "excluded")}, 100)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range paths {
		if path.name == "secret.md" {
			t.Fatal("absolute exclusion through a symlink alias was not applied")
		}
	}
}

func TestSearchPolicyCacheTracksSymlinkRootRetargeting(t *testing.T) {
	firstRoot, secondRoot := t.TempDir(), t.TempDir()
	alias := filepath.Join(t.TempDir(), "root-alias")
	if err := os.Symlink(firstRoot, alias); err != nil {
		t.Skipf("directory symlinks unavailable: %v", err)
	}
	rule := filepath.Join(alias, "excluded")
	first, err := compileSearchPolicy(alias, nil, []string{rule})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(alias); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(secondRoot, alias); err != nil {
		t.Fatal(err)
	}
	second, err := compileSearchPolicy(alias, nil, []string{rule})
	if err != nil {
		t.Fatal(err)
	}
	canonicalSecond, err := CanonicalIndexRoot(secondRoot)
	if err != nil {
		t.Fatal(err)
	}
	if second.canonicalRoot != canonicalSecond || first == second {
		t.Fatalf("cached policy did not follow retargeted root: first=%q second=%q want=%q", first.canonicalRoot, second.canonicalRoot, canonicalSecond)
	}
	if !second.excludedPath(filepath.Join(alias, "excluded", "secret.md")) {
		t.Fatal("absolute exclusion through the retargeted alias was not applied")
	}
}

func TestSearchPolicyCacheTracksSymlinkExclusionRetargeting(t *testing.T) {
	root := t.TempDir()
	firstTarget := filepath.Join(root, "first")
	secondTarget := filepath.Join(root, "second")
	for _, target := range []string{firstTarget, secondTarget} {
		if err := os.Mkdir(target, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	alias := filepath.Join(root, "exclusion-alias")
	if err := os.Symlink(firstTarget, alias); err != nil {
		t.Skipf("directory symlinks unavailable: %v", err)
	}
	rule := []string{alias}
	first, err := compileSearchPolicy(root, nil, rule)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(alias); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(secondTarget, alias); err != nil {
		t.Fatal(err)
	}
	second, err := compileSearchPolicy(root, nil, rule)
	if err != nil {
		t.Fatal(err)
	}
	if first == second || !second.excludedPath(filepath.Join(secondTarget, "secret.md")) {
		t.Fatalf("cached policy did not follow retargeted exclusion: first=%q second=%q", first.rules[0].path, second.rules[0].path)
	}
}

func TestSearchPolicyBuildKeepsTheCacheKeysResolvedExclusion(t *testing.T) {
	root := t.TempDir()
	first, second := filepath.Join(root, "first"), filepath.Join(root, "second")
	for _, directory := range []string{first, second} {
		if err := os.Mkdir(directory, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	alias := filepath.Join(t.TempDir(), "exclusion")
	if err := os.Symlink(first, alias); err != nil {
		t.Skipf("directory symlinks unavailable: %v", err)
	}
	canonicalRoot, err := CanonicalIndexRoot(root)
	if err != nil {
		t.Fatal(err)
	}
	canonicalRules := []string{canonicalSearchPolicyRule(root, canonicalRoot, alias)}
	if err := os.Remove(alias); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(second, alias); err != nil {
		t.Fatal(err)
	}
	policy, err := buildSearchPolicy(root, canonicalRoot, nil, canonicalRules)
	if err != nil {
		t.Fatal(err)
	}
	if !policy.excludedPath(filepath.Join(first, "file.md")) || policy.excludedPath(filepath.Join(second, "file.md")) {
		t.Fatalf("policy re-resolved the cache key's exclusion: %q", policy.rules[0].path)
	}
}

func TestSearchPolicyUsesPinnedSymlinkRootForExclusions(t *testing.T) {
	firstRoot, secondRoot := t.TempDir(), t.TempDir()
	alias := filepath.Join(t.TempDir(), "root-alias")
	if err := os.Symlink(firstRoot, alias); err != nil {
		t.Skipf("directory symlinks unavailable: %v", err)
	}
	pinnedRoot, err := CanonicalIndexRoot(alias)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(alias); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(secondRoot, alias); err != nil {
		t.Fatal(err)
	}
	policy, err := compileSearchPolicyResolved(alias, pinnedRoot, nil, []string{filepath.Join(alias, "excluded")})
	if err != nil {
		t.Fatal(err)
	}
	if policy.canonicalRoot != pinnedRoot || !policy.excludedPath(filepath.Join(alias, "excluded", "secret.md")) {
		t.Fatalf("policy did not preserve pinned root and exclusion: root=%q rules=%#v", policy.canonicalRoot, policy.rules)
	}
}

func TestSearchIndexV2ExcludedRootStillAllowsFinderListing(t *testing.T) {
	for _, name := range []string{"node_modules", ".ssh", "build"} {
		t.Run(name, func(t *testing.T) {
			root := filepath.Join(t.TempDir(), name)
			os.Mkdir(root, 0700)
			os.WriteFile(filepath.Join(root, "2026.md"), nil, 0600)
			paths, coverage, _, err := discoverIndexPaths(context.Background(), root, nil, nil, 100)
			if err != nil || len(paths) != 0 || coverage != "complete" {
				t.Fatalf("excluded root discovery: %v %s %v", paths, coverage, err)
			}
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()
			for _, recursive := range []bool{false, true} {
				payload, err := service.SearchWithOptions(context.Background(), root, "", 100, recursive, false, true, SearchOptions{})
				if err != nil || payload["count"] != 0 {
					t.Fatalf("excluded root search: %v %v", payload, err)
				}
			}
			directory, _, _, finderErr := service.open(root, true)
			if finderErr != nil {
				t.Fatal(finderErr)
			}
			defer directory.Close()
			entries, err := directory.ReadDir(10)
			if err != nil || len(entries) != 1 {
				t.Fatalf("Finder listing was blocked: %v %v", entries, err)
			}
		})
	}
}

func TestSearchIndexV2MigrationSupportsFilesystemRoot(t *testing.T) {
	path := filepath.Join(t.TempDir(), "index.sqlite3")
	makeV1Index(t, path, "/", 1)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO entries VALUES('/',7,'2026.md','2026.md','fixture',1,1)`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`UPDATE roots SET count=2 WHERE root='/'`); err != nil {
		t.Fatal(err)
	}
	db.Close()
	store, err := openSearchIndexStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	paths, err := store.loadNormalizedPaths(context.Background(), "/", 7)
	if err != nil || len(paths) != 2 {
		t.Fatalf("root migration: %v %v", paths, err)
	}
	found := false
	for _, entry := range paths {
		if entry.directory+entry.name == "2026.md" {
			found = true
		}
	}
	if !found {
		t.Fatal("direct filesystem-root file lost")
	}
}

func TestSearchIndexV2MigrationKilledAtEachPublicationPhase(t *testing.T) {
	if phase := os.Getenv("YOMUX_TEST_MIGRATION_CRASH_PHASE"); phase != "" {
		indexMigrationPhase = func(current string) error {
			if current == phase {
				process, err := os.FindProcess(os.Getpid())
				if err != nil {
					os.Exit(9)
				}
				process.Kill()
				os.Exit(10)
			}
			return nil
		}
		if _, err := openSearchIndexStore(os.Getenv("YOMUX_TEST_MIGRATION_CRASH_DB")); err != nil {
			os.Exit(11)
		}
		os.Exit(12)
	}
	for _, phase := range []string{"building", "backed_up", "published"} {
		t.Run(phase, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "index.sqlite3")
			root := t.TempDir()
			makeV1Index(t, path, root, 20)
			command := exec.Command(os.Args[0], "-test.run=^TestSearchIndexV2MigrationKilledAtEachPublicationPhase$")
			command.Env = append(withSearchIndexWorkerCPUCap(os.Environ()), "YOMUX_TEST_MIGRATION_CRASH_PHASE="+phase, "YOMUX_TEST_MIGRATION_CRASH_DB="+path)
			if err := command.Run(); err == nil {
				t.Fatal("publication phase did not interrupt process")
			}
			store, err := openSearchIndexStore(path)
			if err != nil {
				t.Fatal(err)
			}
			defer store.db.Close()
			var count int
			if err := store.db.QueryRow(`SELECT COUNT(*) FROM entries`).Scan(&count); err != nil || count != 20 {
				t.Fatalf("crash recovery lost records: %d %v", count, err)
			}
		})
	}
}

func TestSearchIndexV2BlockedCheckpointKeepsPriorGeneration(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "2026.md"), nil, 0600)
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	info, _ := os.Stat(root)
	job := indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	prior, err := engine.status(root, job.identity)
	if err != nil {
		t.Fatal(err)
	}
	reader, err := readOnlyIndex(store.path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	transaction, err := reader.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer transaction.Rollback()
	var generation int
	if err := transaction.QueryRow(`SELECT generation FROM roots WHERE root=?`, root).Scan(&generation); err != nil {
		t.Fatal(err)
	}
	store.db.SetMaxOpenConns(1)
	if _, err := store.db.Exec(`PRAGMA busy_timeout=10`); err != nil {
		t.Fatal(err)
	}
	if err := engine.build(job); err == nil || !strings.Contains(err.Error(), "checkpoint") {
		t.Fatalf("blocked checkpoint accepted: %v", err)
	}
	retained, err := engine.search(root, job.identity, "2026.md", 10, "", 0)
	if err != nil || retained["count"] != 1 || retained["generation"] != prior["generation"] {
		t.Fatalf("blocked checkpoint lost prior snapshot: %v %v", retained, err)
	}
}

func TestSearchIndexV2NearDefaultBudgetSharedRoots(t *testing.T) {
	if os.Getenv("YOMUX_INDEX_STORAGE_QUALIFY") != "1" {
		t.Skip("explicit 960 MiB SQLite allocation-pressure qualification")
	}
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "2026.md"), nil, 0600)
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	info, _ := os.Stat(root)
	job := indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes}
	if err := engine.build(job); err != nil {
		t.Fatal(err)
	}
	// This fixture pressures SQLite allocation without manufacturing millions of filesystem files.
	if _, err := store.db.Exec(`CREATE TABLE allocation_probe(id INTEGER PRIMARY KEY,padding BLOB)`); err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 12; index++ {
		if _, err := store.db.Exec(`INSERT INTO allocation_probe(padding) VALUES(zeroblob(80*1024*1024))`); err != nil {
			t.Fatal(err)
		}
		if err := checkpointIndex(store.db); err != nil {
			t.Fatal(err)
		}
	}
	active, err := store.allocatedBytes()
	if err != nil {
		t.Fatal(err)
	}
	if active < 960*1024*1024 || active > DefaultIndexBytes {
		t.Fatalf("pressure allocation: %d", active)
	}
	second := t.TempDir()
	os.WriteFile(filepath.Join(second, "2026-second.md"), nil, 0600)
	secondInfo, _ := os.Stat(second)
	done := make(chan struct{})
	sampled := make(chan int64, 1)
	go func() {
		ticker := time.NewTicker(time.Millisecond)
		defer ticker.Stop()
		var peak int64
		for {
			select {
			case <-done:
				sampled <- peak
				return
			case <-ticker.C:
				usage, err := store.diskUsage()
				if err == nil {
					peak = max(peak, usage["database_bytes"]+usage["wal_bytes"]+usage["staging_bytes"])
				}
			}
		}
	}()
	started := time.Now()
	err = engine.build(indexBuildJob{root: second, identity: fileID(secondInfo), maxFiles: 10, maxBytes: DefaultIndexBytes})
	close(done)
	peak := <-sampled
	if err != nil {
		t.Fatal(err)
	}
	if peak > indexTransientCeiling {
		t.Fatalf("transient peak %d exceeds %d", peak, indexTransientCeiling)
	}
	allocated, err := store.allocatedBytes()
	if err != nil || allocated > DefaultIndexBytes {
		t.Fatalf("steady allocation %d %v", allocated, err)
	}
	for _, scope := range []string{root, second} {
		info, _ := os.Stat(scope)
		status, err := engine.status(scope, fileID(info))
		if err != nil || status["count"] != 1 {
			t.Fatalf("shared root lost snapshot: %v %v", status, err)
		}
	}
	t.Logf("960 MiB allocation pressure: active=%d peak=%d steady=%d replacement=%s", active, peak, allocated, time.Since(started))
}

func TestSearchIndexV2RootChangePreservesUnaffectedSnapshot(t *testing.T) {
	roots := []string{t.TempDir(), t.TempDir()}
	for _, root := range roots {
		os.WriteFile(filepath.Join(root, "2026.md"), nil, 0600)
	}
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if _, err := engine.handle(indexRequest{Action: "settings_fence", FenceRoots: roots, SettingsRevision: 41, ApplicationEpoch: 1}); err != nil {
		t.Fatal(err)
	}
	for _, root := range roots {
		info, _ := os.Stat(root)
		if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 10, maxBytes: DefaultIndexBytes, settingsRevision: 41}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := engine.handle(indexRequest{Action: "settings_fence", FenceRoots: roots[1:], SettingsRevision: 42, ApplicationEpoch: 2}); err != nil {
		t.Fatal(err)
	}
	info, _ := os.Stat(roots[1])
	result, err := engine.search(roots[1], fileID(info), "2026.md", 10, "", 42)
	if err != nil || result["count"] != 1 || result["snapshot_revision"] != "41" {
		t.Fatalf("unaffected root lost snapshot: %v %v", result, err)
	}
	removedInfo, _ := os.Stat(roots[0])
	removed, err := engine.status(roots[0], fileID(removedInfo))
	if err != nil || removed["indexed"] != false {
		t.Fatalf("removed root retained results: %v %v", removed, err)
	}
	if _, err := engine.handle(indexRequest{Action: "settings_fence", FenceRoots: roots, SettingsRevision: 43, ApplicationEpoch: 3}); err != nil {
		t.Fatal(err)
	}
	if err := engine.build(indexBuildJob{root: roots[0], identity: fileID(removedInfo), maxFiles: 10, maxBytes: DefaultIndexBytes, settingsRevision: 43}); err != nil {
		t.Fatal(err)
	}
	readded, err := engine.status(roots[0], fileID(removedInfo))
	if err != nil || readded["generation"] != 2 {
		t.Fatalf("readded root lost high-water generation: %v %v", readded, err)
	}
}

func TestSearchIndexClientRepairAdmissionAfterClose(t *testing.T) {
	client := NewSearchIndexWorkerClient(t.TempDir())
	client.Close()
	client.QueueRepair(indexRequest{Root: t.TempDir()}, nil)
	client.repairMu.Lock()
	count := len(client.repairs)
	client.repairMu.Unlock()
	client.Close()
	if count != 0 {
		t.Fatalf("closed client admitted %d repair timers", count)
	}
}

func TestSearchIndexClientPersistentWaitAbortsOnShutdown(t *testing.T) {
	client := NewSearchIndexWorkerClient(t.TempDir())
	client.repairMu.Lock()
	client.closeOnce.Do(func() { close(client.closing) })
	done := make(chan error, 1)
	go func() { done <- client.SetPersistent(false) }()
	select {
	case err := <-done:
		client.repairMu.Unlock()
		if err == nil {
			t.Fatal("closed client accepted a persistence update")
		}
	case <-time.After(time.Second):
		client.repairMu.Unlock()
		<-done
		t.Fatal("persistence update waited on repair ownership after shutdown")
	}
	client.Close()
}

func TestSearchIndexV2EngineSettingsLockWaitRespectsCancellation(t *testing.T) {
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	engine := newSearchIndexEngine(store)
	engine.settingsApplyMu.Lock()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	done := make(chan error, 1)
	go func() {
		_, err := engine.handleContext(ctx, indexRequest{Action: "settings_fence", PreserveSnapshots: true})
		done <- err
	}()
	select {
	case err := <-done:
		engine.settingsApplyMu.Unlock()
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("settings-fence lock result = %v", err)
		}
	case <-time.After(time.Second):
		engine.settingsApplyMu.Unlock()
		<-done
		t.Fatal("cancelled worker settings fence waited on another settings operation")
	}
}

func TestSearchIndexClientCloseJoinsNotificationCallback(t *testing.T) {
	previous := execCommand
	t.Cleanup(func() { execCommand = previous })
	t.Setenv("YOMUX_TEST_INDEX_WORKER", "1")
	execCommand = func(_ string, args ...string) *exec.Cmd {
		return exec.Command(os.Args[0], append([]string{"-test.run=^TestSearchIndexV2WorkerHelperProcess$", "--", "--notification-index-worker"}, args...)...)
	}
	client := NewSearchIndexWorkerClient(t.TempDir())
	started, release, callbackDone := make(chan struct{}), make(chan struct{}), make(chan struct{})
	client.OnComplete = func(map[string]any) {
		close(started)
		<-release
		close(callbackDone)
	}
	client.initialPolicy = &indexRequest{Action: "settings_fence"}
	client.mu.Lock()
	err := client.startLocked(context.Background())
	client.mu.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-started:
	case <-time.After(time.Second):
		close(release)
		client.Close()
		t.Fatal("worker notification did not arrive")
	}
	closed := make(chan struct{})
	go func() { client.Close(); close(closed) }()
	select {
	case <-closed:
		close(release)
		<-callbackDone
		t.Fatal("client Close returned while its notification callback still owned server dependencies")
	case <-time.After(100 * time.Millisecond):
	}
	close(release)
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("Close did not finish after callback release")
	}
}
