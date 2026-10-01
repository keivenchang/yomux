// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

func newTestService(t *testing.T, publish EventPublisher) (*Service, string) {
	return newTestServiceWithNative(t, publish, nil, nativeWatchStatus{})
}

func newTestServiceWithNative(t *testing.T, publish EventPublisher, native nativeWatchController, nativeStatus nativeWatchStatus) (*Service, string) {
	t.Helper()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("hello\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(root, "src"), 0o700); err != nil {
		t.Fatal(err)
	}
	if native == nil {
		native, nativeStatus = newNativeWatcher()
	}
	service := newService(root, publish, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
	t.Cleanup(service.Close)
	return service, root
}

func newCommittedTestRepo(t *testing.T) (string, string) {
	t.Helper()
	root := t.TempDir()
	file := filepath.Join(root, "note.txt")
	if err := os.WriteFile(file, []byte("one\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"init", "-q", "--initial-branch=master", root}, {"-C", root, "config", "gc.auto", "0"}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "config", "user.name", "Finder Test"}, {"-C", root, "add", "--", "note.txt"}, {"-C", root, "commit", "-qm", "initial"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	head, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	return root, strings.TrimSpace(string(head))
}

func TestGitCommitCountIndexLooksUpBothRenamePaths(t *testing.T) {
	index := indexGitCommitCounts(map[string]gitCommitCount{"old.txt\x00new.txt": {added: 3, removed: 1}})
	for _, path := range []string{"old.txt", "new.txt"} {
		added, removed, binary := numstatForPath(index, path)
		if added != 3 || removed != 1 || binary {
			t.Fatalf("numstat for %q = (%v, %v, %v), want (3, 1, false)", path, added, removed, binary)
		}
	}
	if added, removed, binary := numstatForPath(index, "missing.txt"); added != nil || removed != nil || binary {
		t.Fatalf("missing numstat = (%v, %v, %v), want (nil, nil, false)", added, removed, binary)
	}
}

type fakeNativeWatcher struct {
	events chan nativeWatchEvent
	status nativeWatchStatus
	roots  []string
}

type countingNativeWatcher struct {
	events chan nativeWatchEvent
	status nativeWatchStatus
	roots  []string
	calls  int
}

func (w *countingNativeWatcher) SetRoots(_ context.Context, roots []string) nativeWatchStatus {
	w.calls++
	w.roots = append([]string(nil), roots...)
	return w.status
}

func (w *countingNativeWatcher) Events() <-chan nativeWatchEvent { return w.events }

func (w *countingNativeWatcher) Close() { close(w.events) }

type serializedNativeWatcher struct {
	events        chan nativeWatchEvent
	mu            sync.Mutex
	callCount     int
	roots         []string
	firstStarted  chan struct{}
	secondStarted chan struct{}
	releaseFirst  chan struct{}
}

func (w *serializedNativeWatcher) SetRoots(_ context.Context, roots []string) nativeWatchStatus {
	w.mu.Lock()
	w.callCount++
	call := w.callCount
	w.mu.Unlock()
	if call == 1 {
		close(w.firstStarted)
		<-w.releaseFirst
	} else if call == 2 {
		close(w.secondStarted)
	}
	w.mu.Lock()
	w.roots = append([]string(nil), roots...)
	w.mu.Unlock()
	return nativeWatchStatus{available: true}
}

func (w *serializedNativeWatcher) Events() <-chan nativeWatchEvent { return w.events }

func (w *serializedNativeWatcher) Close() { close(w.events) }

type cancellingNativeWatcher struct {
	events    chan nativeWatchEvent
	mu        sync.Mutex
	callCount int
	roots     []string
	started   chan struct{}
}

type replacementCancellingNativeWatcher struct {
	events  chan nativeWatchEvent
	mu      sync.Mutex
	calls   int
	roots   []string
	started chan struct{}
}

func (w *replacementCancellingNativeWatcher) SetRoots(ctx context.Context, roots []string) nativeWatchStatus {
	w.mu.Lock()
	w.calls++
	call := w.calls
	if call == 2 {
		close(w.started)
	}
	w.roots = append([]string(nil), roots...)
	w.mu.Unlock()
	if call == 2 {
		<-ctx.Done()
		return nativeWatchStatus{available: true, degraded: true, reason: "registration-cancelled"}
	}
	return nativeWatchStatus{available: true}
}

func (w *replacementCancellingNativeWatcher) Events() <-chan nativeWatchEvent { return w.events }

func (w *replacementCancellingNativeWatcher) Close() { close(w.events) }

func (w *cancellingNativeWatcher) SetRoots(ctx context.Context, roots []string) nativeWatchStatus {
	w.mu.Lock()
	w.callCount++
	call := w.callCount
	w.mu.Unlock()
	if call == 1 {
		close(w.started)
		<-ctx.Done()
		return nativeWatchStatus{available: true, degraded: true, reason: "registration-cancelled"}
	}
	w.mu.Lock()
	w.roots = append([]string(nil), roots...)
	w.mu.Unlock()
	return nativeWatchStatus{available: true}
}

func (w *cancellingNativeWatcher) Events() <-chan nativeWatchEvent { return w.events }

func (w *cancellingNativeWatcher) Close() { close(w.events) }

func (w *fakeNativeWatcher) SetRoots(_ context.Context, roots []string) nativeWatchStatus {
	w.roots = append([]string(nil), roots...)
	return w.status
}

func (w *fakeNativeWatcher) Events() <-chan nativeWatchEvent { return w.events }

func (w *fakeNativeWatcher) Close() {
	if w.events != nil {
		close(w.events)
	}
}

func TestListAndReadExposeFinderContracts(t *testing.T) {
	service, root := newTestService(t, nil)
	listing, finderErr := service.FastList(context.Background(), root)
	if finderErr != nil {
		t.Fatalf("FastList() error = %v", finderErr)
	}
	if listing["path"] != root || listing["truncated"] != false {
		t.Fatalf("unexpected listing metadata: %#v", listing)
	}
	entries, ok := listing["entries"].([]map[string]any)
	if !ok || len(entries) != 2 {
		t.Fatalf("unexpected entries: %#v", listing["entries"])
	}
	read, finderErr := service.Read(context.Background(), filepath.Join(root, "note.md"))
	if finderErr != nil {
		t.Fatalf("Read() error = %v", finderErr)
	}
	if read["content"] != "hello\n" || read["extension"] != ".md" || read["file_id"] == "" {
		t.Fatalf("unexpected read payload: %#v", read)
	}
	info, finderErr := service.Info(context.Background(), filepath.Join(root, "note.md"))
	if finderErr != nil || info["kind"] != "file" || info["preview_mime"] != "" {
		t.Fatalf("unexpected info payload: %#v, error = %v", info, finderErr)
	}
	raw, contentType, filename, finderErr := service.Raw(context.Background(), filepath.Join(root, "note.md"), 0)
	if finderErr != nil || string(raw) != "hello\n" || filename != "note.md" || !strings.HasPrefix(contentType, "text/") {
		t.Fatalf("unexpected raw payload: %q %q %q, error = %v", raw, contentType, filename, finderErr)
	}
}

func TestFastListDefersRepositoryMetadata(t *testing.T) {
	service, root := newTestService(t, nil)
	repo := filepath.Join(root, "repo")
	if err := os.MkdirAll(filepath.Join(repo, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}

	fast, finderErr := service.FastList(context.Background(), root)
	if finderErr != nil {
		t.Fatalf("FastList() error = %v", finderErr)
	}
	fastEntries := fast["entries"].([]map[string]any)
	var fastRepo map[string]any
	for _, entry := range fastEntries {
		if entry["name"] == "repo" {
			fastRepo = entry
			break
		}
	}
	if fastRepo == nil || fastRepo["repo_info_deferred"] != true {
		t.Fatalf("FastList() did not defer repository metadata: %#v", fastEntries)
	}
	if fastRepo["is_repo"] != true {
		t.Fatalf("FastList() did not cheaply classify repository: %#v", fastRepo)
	}
	for _, entry := range fastEntries {
		if entry["name"] == "src" {
			if entry["is_repo"] != false {
				t.Fatalf("FastList() classified ordinary directory as repository: %#v", entry)
			}
			if _, deferred := entry["repo_info_deferred"]; deferred {
				t.Fatalf("FastList() unnecessarily deferred ordinary directory: %#v", entry)
			}
		}
	}

	full, finderErr := service.List(context.Background(), root)
	if finderErr != nil {
		t.Fatalf("List() error = %v", finderErr)
	}
	fullEntries := full["entries"].([]map[string]any)
	for _, entry := range fullEntries {
		if entry["name"] == "repo" {
			if entry["is_repo"] != true || entry["repo_info_deferred"] == true {
				t.Fatalf("List() repository metadata = %#v", entry)
			}
			return
		}
	}
	t.Fatal("List() did not return repo entry")
}

func TestSearchMatchesPythonDirectRowsAndRanking(t *testing.T) {
	service, root := newTestService(t, nil)
	for _, name := range []string{"needle.txt", "needle-long.txt", "xneedle.txt"} {
		path := filepath.Join(root, name)
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	nested := filepath.Join(root, "src", "nested.txt")
	if err := os.WriteFile(nested, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	direct, finderErr := service.Search(context.Background(), root, "", 20, false, false, false)
	if finderErr != nil {
		t.Fatalf("direct search = %v", finderErr)
	}
	rows := direct["files"].([]map[string]any)
	if len(rows) != 4 {
		t.Fatalf("ordinary non-recursive search returned unexpected rows: %#v", rows)
	}
	for _, row := range rows {
		if row["kind"] != "file" {
			t.Fatalf("ordinary non-recursive search returned a non-file row: %#v", rows)
		}
	}
	for _, row := range rows {
		if row["name"] == "nested.txt" {
			t.Fatal("non-recursive search traversed a child directory")
		}
	}
	ranked, finderErr := service.Search(context.Background(), root, "needle", 20, false, false, false)
	if finderErr != nil {
		t.Fatalf("ranked search = %v", finderErr)
	}
	rankedRows := ranked["files"].([]map[string]any)
	if len(rankedRows) < 3 || rankedRows[0]["name"] != "needle.txt" || rankedRows[0]["uploaded"] != false {
		t.Fatalf("Python ranking/row fields not preserved: %#v", rankedRows)
	}
}

func TestWatchRootsEnforcesGlobalClientCapacity(t *testing.T) {
	fake := &fakeNativeWatcher{status: nativeWatchStatus{available: true}}
	service, root := newTestServiceWithNative(t, nil, fake, fake.status)
	for index := 0; index < maxActiveWatchClients; index++ {
		if _, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": fmt.Sprintf("client-%d", index), "roots": []any{root}}); finderErr != nil {
			t.Fatalf("watch %d = %v", index, finderErr)
		}
	}
	if _, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "overflow", "roots": []any{root}}); finderErr == nil || finderErr.Code != "watch_capacity_exceeded" {
		t.Fatalf("global watch capacity error = %#v", finderErr)
	}
}

func TestWatchRootsCancellationRollsBackInstalledRecord(t *testing.T) {
	native := &cancellingNativeWatcher{events: make(chan nativeWatchEvent), started: make(chan struct{})}
	service, root := newTestServiceWithNative(t, nil, native, nativeWatchStatus{available: true})
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan *Error, 1)
	go func() {
		_, finderErr := service.WatchRoots(ctx, map[string]any{"client_id": "cancelled", "roots": []any{root}})
		result <- finderErr
	}()
	select {
	case <-native.started:
	case <-time.After(time.Second):
		t.Fatal("native registration did not start")
	}
	cancel()
	select {
	case finderErr := <-result:
		if finderErr == nil || finderErr.Code != "cancelled" {
			t.Fatalf("cancelled watch result = %#v", finderErr)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled watch did not finish")
	}
	service.mu.Lock()
	_, installed := service.watches["cancelled"]
	service.mu.Unlock()
	if installed {
		t.Fatal("cancelled watch record remained installed")
	}
	native.mu.Lock()
	defer native.mu.Unlock()
	if len(native.roots) != 0 {
		t.Fatalf("native roots after rollback = %#v", native.roots)
	}
}

func TestWatchRootsCancellationRestoresPreviousLease(t *testing.T) {
	native := &replacementCancellingNativeWatcher{events: make(chan nativeWatchEvent), started: make(chan struct{})}
	service, root := newTestServiceWithNative(t, nil, native, nativeWatchStatus{available: true})
	firstRoot := filepath.Join(root, "src")
	if _, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "replace", "roots": []any{firstRoot}}); finderErr != nil {
		t.Fatal(finderErr)
	}
	secondRoot := filepath.Join(root, "second")
	if err := os.Mkdir(secondRoot, 0o700); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan *Error, 1)
	go func() {
		_, finderErr := service.WatchRoots(ctx, map[string]any{"client_id": "replace", "roots": []any{secondRoot}})
		result <- finderErr
	}()
	select {
	case <-native.started:
	case <-time.After(time.Second):
		t.Fatal("replacement native registration did not start")
	}
	cancel()
	select {
	case finderErr := <-result:
		if finderErr == nil || finderErr.Code != "cancelled" {
			t.Fatalf("cancelled replacement result = %#v", finderErr)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled replacement did not finish")
	}
	service.mu.Lock()
	restored := append([]string(nil), service.watches["replace"].Roots...)
	service.mu.Unlock()
	if len(restored) != 1 || restored[0] != firstRoot {
		t.Fatalf("restored watch roots = %#v", restored)
	}
	native.mu.Lock()
	defer native.mu.Unlock()
	if len(native.roots) != 1 || native.roots[0] != firstRoot {
		t.Fatalf("restored native roots = %#v", native.roots)
	}
}

func TestWatchRootsCountsBackgroundFilesAgainstGlobalCapacity(t *testing.T) {
	fake := &fakeNativeWatcher{status: nativeWatchStatus{available: true}}
	service, root := newTestServiceWithNative(t, nil, fake, fake.status)
	backgroundFiles := func(client int) []any {
		files := make([]any, maxActiveWatchFiles/4)
		for index := range files {
			files[index] = filepath.Join(root, fmt.Sprintf("background-%d-%d.md", client, index))
		}
		return files
	}
	for client := 0; client < 4; client++ {
		if _, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": fmt.Sprintf("background-%d", client), "roots": []any{root}, "background_files": backgroundFiles(client)}); finderErr != nil {
			t.Fatalf("background watch %d = %v", client, finderErr)
		}
	}
	if _, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "background-overflow", "roots": []any{root}, "background_files": backgroundFiles(4)}); finderErr == nil || finderErr.Code != "watch_capacity_exceeded" {
		t.Fatalf("background watch capacity error = %#v", finderErr)
	}
}

func TestWatchRootsSerializesNativeReconfiguration(t *testing.T) {
	root := t.TempDir()
	secondRoot := t.TempDir()
	native := &serializedNativeWatcher{events: make(chan nativeWatchEvent), firstStarted: make(chan struct{}), secondStarted: make(chan struct{}), releaseFirst: make(chan struct{})}
	service := newService(root, nil, native, nativeWatchStatus{available: true}, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
	t.Cleanup(service.Close)
	first := make(chan *Error, 1)
	go func() {
		_, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "first", "roots": []any{root}})
		first <- finderErr
	}()
	select {
	case <-native.firstStarted:
	case <-time.After(time.Second):
		t.Fatal("first native registration did not start")
	}
	second := make(chan *Error, 1)
	go func() {
		_, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "first", "roots": []any{secondRoot}})
		second <- finderErr
	}()
	select {
	case <-native.secondStarted:
		t.Fatal("second native registration ran concurrently")
	case <-time.After(20 * time.Millisecond):
	}
	close(native.releaseFirst)
	if finderErr := <-first; finderErr != nil {
		t.Fatal(finderErr)
	}
	select {
	case <-native.secondStarted:
	case <-time.After(time.Second):
		t.Fatal("second native registration did not run")
	}
	if finderErr := <-second; finderErr != nil {
		t.Fatal(finderErr)
	}
	native.mu.Lock()
	defer native.mu.Unlock()
	if len(native.roots) != 1 || native.roots[0] != secondRoot {
		t.Fatalf("final native roots = %#v", native.roots)
	}
}

func TestAuthorizationAllowsAnyReadablePathAndSecretNames(t *testing.T) {
	service, root := newTestService(t, nil)
	externalRoot := filepath.Join(t.TempDir(), "external")
	if err := os.MkdirAll(filepath.Join(externalRoot, "nested"), 0o700); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(externalRoot, "outside.txt")
	if err := os.WriteFile(outside, []byte("no"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(externalRoot, "nested", "needle.txt"), []byte("needle\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	read, finderErr := service.Read(context.Background(), outside)
	if finderErr != nil || read["content"] != "no" {
		t.Fatalf("outside read = %#v, error = %#v", read, finderErr)
	}
	listing, finderErr := service.List(context.Background(), externalRoot)
	if finderErr != nil || listing["path"] != externalRoot {
		t.Fatalf("outside listing = %#v, error = %#v", listing, finderErr)
	}
	search, finderErr := service.Search(context.Background(), externalRoot, "needle", 20, true, false, false)
	if finderErr != nil || search["count"] != 1 {
		t.Fatalf("outside search = %#v, error = %#v", search, finderErr)
	}
	created := filepath.Join(externalRoot, "created.txt")
	if _, finderErr := service.Write(context.Background(), created, "created", nil); finderErr != nil {
		t.Fatalf("outside write error = %#v", finderErr)
	}
	if _, finderErr := service.Delete(context.Background(), created); finderErr != nil {
		t.Fatalf("outside delete error = %#v", finderErr)
	}
	secret := filepath.Join(root, ".ssh", "id_rsa")
	if err := os.MkdirAll(filepath.Dir(secret), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(secret, []byte("key"), 0o600); err != nil {
		t.Fatal(err)
	}
	secretRead, finderErr := service.Read(context.Background(), secret)
	if finderErr != nil || secretRead["content"] != "key" {
		t.Fatalf("secret read = %#v, error = %#v", secretRead, finderErr)
	}
	if _, finderErr := service.Read(context.Background(), filepath.Join(root, "missing.txt")); finderErr == nil || finderErr.Status != 404 {
		t.Fatalf("missing read error = %#v, want 404", finderErr)
	}
}

func TestAuthorizationAllowsCredentialSubtreeDescendants(t *testing.T) {
	service, root := newTestService(t, nil)
	for _, relative := range []string{
		".config/gh/hosts.yml",
		".config/git/credentials",
		".docker/contexts/meta.json",
		".ngc/cache/token",
	} {
		path := filepath.Join(root, relative)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(relative), 0o600); err != nil {
			t.Fatal(err)
		}
		info, finderErr := service.Info(context.Background(), path)
		if finderErr != nil || info["kind"] != "file" {
			t.Fatalf("credential descendant %q info = %#v, error = %#v", relative, info, finderErr)
		}
	}
}

func TestWatchRootsPreservesBoundedPythonDescriptorFields(t *testing.T) {
	service, root := newTestService(t, nil)
	background := filepath.Join(root, "background.md")
	active := filepath.Join(root, "note.md")
	payload := map[string]any{
		"client_id":             "watch-client",
		"roots":                 []any{root},
		"files":                 []any{active},
		"background_files":      []any{background, active, "relative", background},
		"root_surfaces_version": float64(1),
		"root_surfaces":         []any{map[string]any{"path": root, "surfaces": []any{"finder", "finder"}}},
		"context_items": []any{
			map[string]any{"session": "1", "messages": float64(999)},
			map[string]any{"session": "1", "messages": float64(999)},
		},
		"session_files": []any{map[string]any{"session": "1", "hours": float64(0.01), "from": "HEAD"}},
	}
	result, finderErr := service.WatchRoots(context.Background(), payload)
	if finderErr != nil {
		t.Fatalf("watch diff error = %#v", finderErr)
	}
	if got := result["background_files"].([]string); len(got) != 1 || got[0] != background {
		t.Fatalf("background files = %#v", got)
	}
	rootSurfaces := result["root_surfaces"].([]map[string]any)
	if result["root_surfaces_version"] != 1 || len(rootSurfaces) != 1 || rootSurfaces[0]["path"] != root {
		t.Fatalf("root surfaces = %#v", result["root_surfaces"])
	}
	contextItems := result["context_items"].([]map[string]any)
	if len(contextItems) != 1 || contextItems[0]["messages"] != 200 {
		t.Fatalf("context items = %#v", result["context_items"])
	}
	sessionFiles := result["session_files"].([]map[string]any)
	if len(sessionFiles) != 1 || sessionFiles[0]["hours"] != 0.25 || sessionFiles[0]["from_ref"] != "HEAD" {
		t.Fatalf("session files = %#v", result["session_files"])
	}
}

func TestWatchSignatureUsesPinnedDescriptorAndPythonShape(t *testing.T) {
	service, root := newTestService(t, nil)
	child := filepath.Join(root, "child.txt")
	if err := os.WriteFile(child, []byte("child"), 0o600); err != nil {
		t.Fatal(err)
	}
	signature, finderErr := service.WatchSignature(context.Background(), root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if len(signature) != 5 || signature[0] != root || signature[1] != "dir" {
		t.Fatalf("directory watch signature = %#v", signature)
	}
	children, ok := signature[4].([]any)
	if !ok || len(children) != 3 {
		t.Fatalf("directory watch children = %#v", signature[4])
	}
	missing, finderErr := service.WatchSignature(context.Background(), filepath.Join(root, "missing.txt"))
	if finderErr == nil || finderErr.Status != http.StatusNotFound || missing != nil {
		t.Fatalf("missing watch signature = %#v, error = %#v", missing, finderErr)
	}
}

func TestSymlinkToAnyReadablePathIsAllowedAfterOpen(t *testing.T) {
	service, root := newTestService(t, nil)
	outside := filepath.Join(t.TempDir(), "outside.txt")
	if err := os.WriteFile(outside, []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "escape.txt")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	read, finderErr := service.Read(context.Background(), link)
	if finderErr != nil || read["content"] != "secret" {
		t.Fatalf("symlink read = %#v, error = %#v", read, finderErr)
	}
}

func TestListUsesPinnedDirectoryAndIncludesSymlinksToAnyReadablePath(t *testing.T) {
	service, root := newTestService(t, nil)
	inside := filepath.Join(root, "inside.txt")
	if err := os.WriteFile(inside, []byte("inside"), 0o600); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "outside.txt")
	if err := os.WriteFile(outside, []byte("outside"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(inside, filepath.Join(root, "inside-link")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "outside-link")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	resolvedOutside, err := filepath.EvalSymlinks(outside)
	if err != nil {
		t.Fatal(err)
	}
	broken := filepath.Join(root, "broken-link")
	if err := os.Symlink(filepath.Join(root, "missing-target"), broken); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	listing, finderErr := service.List(context.Background(), root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	entries := listing["entries"].([]map[string]any)
	seen := map[string]map[string]any{}
	for _, entry := range entries {
		seen[entry["name"].(string)] = entry
	}
	if seen["outside-link"]["kind"] != "file" || seen["outside-link"]["realpath"] != resolvedOutside {
		t.Fatalf("external symlink metadata = %#v", seen["outside-link"])
	}
	if seen["inside-link"]["kind"] != "file" || seen["inside-link"]["symlink_target"] != inside {
		t.Fatalf("valid symlink metadata = %#v", seen["inside-link"])
	}
	if seen["broken-link"]["kind"] != "symlink-broken" || seen["broken-link"]["symlink_target"] != filepath.Join(root, "missing-target") {
		t.Fatalf("broken symlink metadata = %#v", seen["broken-link"])
	}
}

func TestDirectSearchDoesNotFollowSymlink(t *testing.T) {
	service, root := newTestService(t, nil)
	outside := filepath.Join(t.TempDir(), "outside.txt")
	if err := os.WriteFile(outside, []byte("outside"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "outside-link.txt")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	result, finderErr := service.Search(context.Background(), root, "", 20, false, true, false)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	for _, file := range result["files"].([]map[string]any) {
		if file["name"] == filepath.Base(link) {
			t.Fatalf("direct search followed an escaping symlink: %#v", file)
		}
	}
}

func TestReadConsumesTheOpenedDescriptorAfterNamespaceReplacement(t *testing.T) {
	service, root := newTestService(t, nil)
	path := filepath.Join(root, "note.md")
	opened, _, _, finderErr := service.open(path, false)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer opened.Close()
	oldPath := path + ".old"
	if err := os.Rename(path, oldPath); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("replacement\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	data, err := io.ReadAll(opened)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "hello\n" {
		t.Fatalf("opened descriptor read %q after replacement, want original", data)
	}
}

func TestStabilityCheckUsesOpenedDescriptorAfterNamespaceReplacement(t *testing.T) {
	service, root := newTestService(t, nil)
	path := filepath.Join(root, "note.md")
	opened, _, _, finderErr := service.open(path, false)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer opened.Close()
	before, err := opened.Stat()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(path, path+".old"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("other\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, before.ModTime(), before.ModTime()); err != nil {
		t.Fatal(err)
	}
	if !service.pathStillStable(opened, before) {
		t.Fatal("descriptor stability changed when only the namespace name was replaced")
	}
}

func TestWatchRootsPublishesExternalChange(t *testing.T) {
	var mu sync.Mutex
	var events []map[string]any
	service, root := newTestService(t, func(_ string, payload map[string]any) error {
		mu.Lock()
		defer mu.Unlock()
		events = append(events, payload)
		return nil
	})
	if _, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "client-1", "roots": []any{root}}); finderErr != nil {
		t.Fatalf("WatchRoots() error = %v", finderErr)
	}
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("changed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	deadline := time.NewTimer(3 * time.Second)
	ticker := time.NewTicker(25 * time.Millisecond)
	defer deadline.Stop()
	defer ticker.Stop()
	for {
		select {
		case <-deadline.C:
			t.Fatal("watch change was not published")
		case <-ticker.C:
			mu.Lock()
			count := len(events)
			mu.Unlock()
			if count > 0 {
				return
			}
		}
	}
}

func TestWatchSignaturePathsKeepAllExplicitRootsAndFiles(t *testing.T) {
	got := watchSignaturePaths(
		[]string{"/repo/src", "/other", "/repo/src", "/repo"},
		[]string{"/repo/src/file.txt", "/outside/file.txt"},
		[]string{"/repo/generated.txt", "/outside/background.txt"},
	)
	want := []string{
		"/other", "/outside/background.txt", "/outside/file.txt", "/repo", "/repo/generated.txt", "/repo/src", "/repo/src/file.txt",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("watchSignaturePaths() = %#v, want %#v", got, want)
	}
}

func TestWatchRootsPollsExplicitFileBeyondTruncatedRootSignature(t *testing.T) {
	fake := &fakeNativeWatcher{events: make(chan nativeWatchEvent), status: nativeWatchStatus{available: true}}
	service, root := newTestServiceWithNative(t, nil, fake, fake.status)
	for index := 0; index <= maxWatchSignatureEntries; index++ {
		path := filepath.Join(root, fmt.Sprintf("a-%05d.txt", index))
		if err := os.WriteFile(path, []byte("padding\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	target := filepath.Join(root, "z-explicit.txt")
	if err := os.WriteFile(target, []byte("before\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	registration, finderErr := service.WatchRoots(context.Background(), map[string]any{
		"client_id": "truncated-root", "roots": []any{root}, "files": []any{target},
	})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	baseline := registration["watch_token"].(string)
	if err := os.WriteFile(target, []byte("after\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service.pollWatches()
	diff, finderErr := service.WatchDiff(context.Background(), baseline, false)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if diff["mode"] != "delta" || diff["token"] == baseline {
		t.Fatalf("explicit file change beyond root signature was missed: %#v", diff)
	}
}

func TestWatchChangeOwnerCoalescesDuplicateSignals(t *testing.T) {
	service, root := newTestService(t, nil)
	path := filepath.Join(root, "note.md")
	service.recordChange([]string{path}, "native-event")
	service.mu.Lock()
	firstRevision := service.revision
	service.mu.Unlock()
	service.recordChange([]string{path}, "filesystem-change")
	service.mu.Lock()
	defer service.mu.Unlock()
	if service.revision != firstRevision {
		t.Fatalf("duplicate watch signal advanced revision from %d to %d", firstRevision, service.revision)
	}
}

func TestWatchRootsUsesNativeWatcherAndAdmitsNestedDirectories(t *testing.T) {
	var mu sync.Mutex
	var events []map[string]any
	service, root := newTestService(t, func(_ string, payload map[string]any) error {
		mu.Lock()
		defer mu.Unlock()
		events = append(events, payload)
		return nil
	})
	registration, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "native", "roots": []any{root}})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	status, ok := registration["watch_status"].(map[string]any)
	if runtime.GOOS != "linux" && registration["mode"] == "polling-fallback" {
		t.Skipf("native watcher unavailable in this environment: %#v", registration["watch_status"])
	}
	if !ok || status["registrations"].(int) < 1 {
		t.Fatalf("native watch status = %#v", registration["watch_status"])
	}
	if runtime.GOOS != "linux" && registration["mode"] == "polling-fallback" {
		t.Skipf("native watcher unavailable in this environment: %#v", registration)
	}
	nested := filepath.Join(root, "new", "deeper")
	if err := os.MkdirAll(nested, 0o700); err != nil {
		t.Fatal(err)
	}
	deadline := time.NewTimer(2 * time.Second)
	ticker := time.NewTicker(10 * time.Millisecond)
	defer deadline.Stop()
	defer ticker.Stop()
	for {
		select {
		case <-deadline.C:
			t.Fatal("native nested-directory event was not published")
		case <-ticker.C:
			mu.Lock()
			for _, event := range events {
				if event["reason"] == "native-event" && strings.Contains(strings.Join(event["paths"].([]string), "\n"), "new") {
					mu.Unlock()
					return
				}
			}
			mu.Unlock()
		}
	}
}

func TestWatchRootsSkipsUnchangedNativeConfigurationAndRetriesUnhealthyWatcher(t *testing.T) {
	t.Run("unchanged roots", func(t *testing.T) {
		watcher := &countingNativeWatcher{events: make(chan nativeWatchEvent), status: nativeWatchStatus{available: true}}
		service, root := newTestServiceWithNative(t, nil, watcher, watcher.status)
		payload := map[string]any{"client_id": "same-roots", "roots": []any{root}}
		for index := 0; index < 2; index++ {
			if _, finderErr := service.WatchRoots(context.Background(), payload); finderErr != nil {
				t.Fatal(finderErr)
			}
		}
		if watcher.calls != 1 || len(watcher.roots) != 1 || watcher.roots[0] != root {
			t.Fatalf("native SetRoots calls/roots = %d/%v, want one call with %q", watcher.calls, watcher.roots, root)
		}
	})

	t.Run("unhealthy watcher retries", func(t *testing.T) {
		watcher := &countingNativeWatcher{events: make(chan nativeWatchEvent)}
		service, root := newTestServiceWithNative(t, nil, watcher, watcher.status)
		payload := map[string]any{"client_id": "retry-roots", "roots": []any{root}}
		if _, finderErr := service.WatchRoots(context.Background(), payload); finderErr != nil {
			t.Fatal(finderErr)
		}
		watcher.status = nativeWatchStatus{available: true}
		if _, finderErr := service.WatchRoots(context.Background(), payload); finderErr != nil {
			t.Fatal(finderErr)
		}
		if watcher.calls != 2 {
			t.Fatalf("unhealthy native watcher SetRoots calls = %d, want retry on identical roots", watcher.calls)
		}
	})
}

func TestWatchLifecycleExpiresDormantClientsAndReportsCapacityDegradation(t *testing.T) {
	fake := &fakeNativeWatcher{events: make(chan nativeWatchEvent, 2), status: nativeWatchStatus{available: true}}
	service, root := newTestServiceWithNative(t, nil, fake, fake.status)
	registration, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "lease", "roots": []any{root}})
	if finderErr != nil || registration["mode"] != "native" {
		t.Fatalf("fake native registration = %#v, error = %v", registration, finderErr)
	}
	if len(fake.roots) != 1 || fake.roots[0] != root {
		t.Fatalf("native roots = %#v", fake.roots)
	}
	service.mu.Lock()
	record := service.watches["lease"]
	record.ExpiresAt = time.Now().Add(-time.Second)
	service.watches["lease"] = record
	service.mu.Unlock()
	service.pruneExpiredWatches(time.Now())
	service.mu.Lock()
	if len(service.watches) != 0 {
		service.mu.Unlock()
		t.Fatalf("expired watches = %#v", service.watches)
	}
	service.mu.Unlock()
	if len(fake.roots) != 0 {
		t.Fatalf("expired native roots = %#v", fake.roots)
	}

	fake.status = nativeWatchStatus{available: true, degraded: true, reason: "native-capacity-exceeded", registrations: maxNativeWatchRegistrations}
	registration, finderErr = service.WatchRoots(context.Background(), map[string]any{"client_id": "capacity", "roots": []any{root}})
	if finderErr != nil || registration["mode"] != "native-degraded" || registration["watch_degradation_reason"] != "native-capacity-exceeded" {
		t.Fatalf("degraded registration = %#v, error = %v", registration, finderErr)
	}
}

func TestWatchOverflowInvalidatesTheAffectedRoot(t *testing.T) {
	fake := &countingNativeWatcher{events: make(chan nativeWatchEvent, 2), status: nativeWatchStatus{available: true}}
	service, root := newTestServiceWithNative(t, nil, fake, fake.status)
	baseline, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "overflow", "roots": []any{root}})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	service.handleNativeEvent(nativeWatchEvent{root: root, overflow: true})
	if fake.calls != 2 {
		t.Fatalf("native SetRoots calls after overflow = %d, want initial registration plus forced reconciliation", fake.calls)
	}
	diff, finderErr := service.WatchDiff(context.Background(), baseline["watch_token"].(string), false)
	if finderErr != nil || diff["mode"] != "delta" || diff["refresh"] != false {
		t.Fatalf("overflow diff = %#v, error = %v", diff, finderErr)
	}
	if diff["watch_degraded"] != false {
		t.Fatalf("overflow watch status = %#v", diff)
	}
	changes := diff["change_summary"].(map[string]any)
	if changes["paths_changed"] != 1 {
		t.Fatalf("overflow change summary = %#v", changes)
	}
}

func TestReadInfoAndGitEnrichmentUsePythonFields(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "tracked.md")
	if err := os.WriteFile(file, []byte("tracked\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"init", "-q", root}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "config", "user.name", "Finder Test"}, {"-C", root, "add", "tracked.md"}, {"-C", root, "commit", "-qm", "initial"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	read, finderErr := service.Read(context.Background(), file, true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if read["git_root"] != root || read["git_tracked"] != true || read["git_has_history"] != true {
		t.Fatalf("read git payload = %#v", read)
	}
	history, ok := read["git_history"].([]any)
	if !ok || len(history) != 1 {
		t.Fatalf("read history = %#v", read["git_history"])
	}
	info, finderErr := service.Info(context.Background(), file, true)
	if finderErr != nil || info["relative_path"] != "tracked.md" || info["git_tracked"] != true {
		t.Fatalf("info git payload = %#v, error = %v", info, finderErr)
	}
	rootInfo, finderErr := service.Info(context.Background(), root, true)
	if finderErr != nil || rootInfo["git_root"] != root || rootInfo["relative_path"] != "" || rootInfo["git_enrichment"].(map[string]any)["available"] != true {
		t.Fatalf("repository-root info git payload = %#v, error = %v", rootInfo, finderErr)
	}
	if repo, ok := rootInfo["repo"].(map[string]any); !ok || repo["root"] != root {
		t.Fatalf("repository-root info repo payload = %#v", rootInfo["repo"])
	}
	if err := os.WriteFile(file, []byte("changed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	diff, finderErr := service.Diff(context.Background(), file, "HEAD", "current")
	if finderErr != nil || diff["repo"] != root || diff["relative_path"] != "tracked.md" || diff["untracked"] != false || !strings.Contains(diff["diff"].(string), "+changed") {
		t.Fatalf("diff payload = %#v, error = %v", diff, finderErr)
	}
	blame, finderErr := service.Blame(context.Background(), file, "")
	if finderErr != nil || blame["in_repo"] != true || len(blame["lines"].(map[string]map[string]any)) != 1 {
		t.Fatalf("blame payload = %#v, error = %v", blame, finderErr)
	}
}

func TestGitHistoryAndCommitRequireCurrentHeadObject(t *testing.T) {
	for _, operation := range []string{"history", "commit"} {
		t.Run(operation, func(t *testing.T) {
			root, head := newCommittedTestRepo(t)
			original := execCommandContext
			t.Cleanup(func() { execCommandContext = original })
			execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
				for _, arg := range args {
					if arg == "HEAD^{commit}" {
						command := exec.CommandContext(ctx, "false")
						command.ExtraFiles = extraFiles
						command.Env = gitCommandEnvironment()
						return command
					}
				}
				command := exec.CommandContext(ctx, "git", args...)
				command.ExtraFiles = extraFiles
				command.Env = gitCommandEnvironment()
				return command
			}
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()
			if operation == "history" {
				if _, finderErr := service.GitHistory(context.Background(), root, "1", ""); finderErr == nil || finderErr.MessageKey != "fs.error.gitHistoryFailed" {
					t.Fatalf("missing current HEAD object history error = %v", finderErr)
				}
				return
			}
			if _, finderErr := service.GitCommit(context.Background(), root, head, head); finderErr == nil || finderErr.MessageKey != "fs.error.gitCommitFailed" {
				t.Fatalf("missing current HEAD object commit error = %v", finderErr)
			}
		})
	}
}

func TestGitMetadataAndBlameTreatSpecialFilenamesLiterally(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	path := filepath.Join(root, "literal[abc]*.txt")
	if err := os.WriteFile(path, []byte("literal\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "-A").CombinedOutput(); err != nil {
		t.Fatalf("git add: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "special filename").CombinedOutput(); err != nil {
		t.Fatalf("git commit: %v (%s)", err, output)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	read, finderErr := service.Read(context.Background(), path, true)
	if finderErr != nil || read["git_tracked"] != true || read["git_has_history"] != true {
		t.Fatalf("literal metadata = %#v, error = %v", read, finderErr)
	}
	blame, finderErr := service.Blame(context.Background(), path, "")
	if finderErr != nil || len(blame["lines"].(map[string]map[string]any)) != 1 {
		t.Fatalf("literal blame = %#v, error = %v", blame, finderErr)
	}
}

func TestGitViewsUseLiteralPathspecsAndWorkingBlameRef(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	targetDir := filepath.Join(root, "literal*")
	decoyDir := filepath.Join(root, "literal-decoy")
	if err := os.MkdirAll(targetDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(decoyDir, 0o700); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(targetDir, "note.txt")
	decoy := filepath.Join(decoyDir, "note.txt")
	if err := os.WriteFile(target, []byte("target\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "--", target).CombinedOutput(); err != nil {
		t.Fatalf("git add target: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "target commit").CombinedOutput(); err != nil {
		t.Fatalf("git commit target: %v (%s)", err, output)
	}
	if err := os.WriteFile(decoy, []byte("decoy\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "--", decoy).CombinedOutput(); err != nil {
		t.Fatalf("git add decoy: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "decoy commit").CombinedOutput(); err != nil {
		t.Fatalf("git commit decoy: %v (%s)", err, output)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	history, finderErr := service.GitHistory(context.Background(), targetDir, "10", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	commits := history["commits"].([]map[string]any)
	if len(commits) != 1 || commits[0]["subject"] != "target commit" {
		t.Fatalf("literal target history = %#v", commits)
	}
	metadata, finderErr := service.Read(context.Background(), target, true)
	if finderErr != nil || metadata["git_tracked"] != true || metadata["git_has_history"] != true {
		t.Fatalf("nested literal metadata = %#v, error = %v", metadata, finderErr)
	}
	commitDetail, finderErr := service.GitCommit(context.Background(), targetDir, commits[0]["sha"].(string), history["head"].(string))
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	commitFiles, ok := commitDetail["files"].([]map[string]any)
	if !ok || len(commitFiles) != 1 || commitFiles[0]["path"] != "literal*/note.txt" {
		t.Fatalf("nested literal commit detail = %#v", commitDetail)
	}
	if err := os.WriteFile(target, []byte("target changed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	diff, finderErr := service.Diff(context.Background(), target, "HEAD", "current")
	if finderErr != nil || diff["untracked"] != false || diff["original"] != "target\n" || diff["working"] != "target changed\n" || !strings.Contains(diff["diff"].(string), "+target changed") {
		t.Fatalf("nested literal diff = %#v, error = %v", diff, finderErr)
	}
	blame, finderErr := service.Blame(context.Background(), target, "working")
	if finderErr != nil || blame["in_repo"] != true || len(blame["lines"].(map[string]map[string]any)) != 1 {
		t.Fatalf("working blame = %#v, error = %v", blame, finderErr)
	}
}

func TestGitHistoryAndCommitBindRepositoryIdentity(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	page, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	cursor, err := decodeGitHistoryCursor(page["snapshot_cursor"].(string))
	if err != nil {
		t.Fatal(err)
	}
	cursor.Identity = "replaced-repository"
	forgedCursor, err := encodeGitHistoryCursor(cursor)
	if err != nil {
		t.Fatal(err)
	}
	if _, finderErr = service.GitHistory(context.Background(), root, "1", forgedCursor); finderErr == nil || finderErr.Status != http.StatusBadRequest {
		t.Fatalf("forged repository cursor = %v", finderErr)
	}
	commit := page["commits"].([]map[string]any)[0]["sha"].(string)
	if _, finderErr = service.GitCommit(context.Background(), root, commit, page["head"].(string), "replaced-repository"); finderErr == nil || finderErr.Status != http.StatusConflict {
		t.Fatalf("forged repository detail identity = %v", finderErr)
	}
}

func TestGitHistoryCacheInvalidatesWhenDecoratingRefMoves(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	first, finderErr := service.GitHistory(context.Background(), root, "10", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	firstCommit := first["commits"].([]map[string]any)[0]
	if strings.Contains(firstCommit["decorations"].(string), "tag: moved-ref") {
		t.Fatalf("tag unexpectedly present before ref creation: %#v", firstCommit)
	}
	if output, err := exec.Command("git", "-C", root, "tag", "-m", "moved-ref", "moved-ref").CombinedOutput(); err != nil {
		t.Fatalf("create moved-ref: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("git", "-C", root, "tag", "-d", "moved-ref").Run() })

	second, finderErr := service.GitHistory(context.Background(), root, "10", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	secondCommit := second["commits"].([]map[string]any)[0]
	if !strings.Contains(secondCommit["decorations"].(string), "tag: moved-ref") {
		t.Fatalf("history cache did not observe new tag: %#v", secondCommit)
	}

	if output, err := exec.Command("git", "-C", root, "tag", "-d", "moved-ref").CombinedOutput(); err != nil {
		t.Fatalf("delete moved-ref: %v (%s)", err, output)
	}
	third, finderErr := service.GitHistory(context.Background(), root, "10", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	thirdCommit := third["commits"].([]map[string]any)[0]
	if strings.Contains(thirdCommit["decorations"].(string), "tag: moved-ref") {
		t.Fatalf("history cache retained deleted tag: %#v", thirdCommit)
	}
}

func TestGitHistoryRefSignatureIncludesStash(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	scope, finderErr := service.openGitHistoryScopeContext(context.Background(), root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer func() { scope.close() }()
	before, finderErr := gitHistoryRefSignature(context.Background(), scope)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if output, err := exec.Command("git", "-C", root, "update-ref", "refs/stash", head).CombinedOutput(); err != nil {
		t.Fatalf("create stash ref: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("git", "-C", root, "update-ref", "-d", "refs/stash").Run() })
	if runtime.GOOS == "darwin" {
		scope.close()
		scope, finderErr = service.openGitHistoryScopeContext(context.Background(), root)
		if finderErr != nil {
			t.Fatal(finderErr)
		}
	}

	after, finderErr := gitHistoryRefSignature(context.Background(), scope)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if before == after {
		t.Fatalf("history ref signature did not change when refs/stash moved: %s", before)
	}
}

func TestGitHistoryCacheRefreshesConfigDependentMetadata(t *testing.T) {
	t.Run("hosted remote", func(t *testing.T) {
		root, _ := newCommittedTestRepo(t)
		for _, args := range [][]string{{"-C", root, "remote", "add", "origin", "git@github.com:owner/old.git"}} {
			if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
				t.Fatalf("git %v: %v (%s)", args, err, output)
			}
		}
		service, err := New(root, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer service.Close()

		first, finderErr := service.GitHistory(context.Background(), root, "10", "")
		if finderErr != nil {
			t.Fatal(finderErr)
		}
		if got := first["hosted_remote"].(map[string]string)["base_url"]; got != "https://github.com/owner/old" {
			t.Fatalf("initial hosted remote = %q", got)
		}
		if output, err := exec.Command("git", "-C", root, "remote", "set-url", "origin", "git@github.com:owner/new.git").CombinedOutput(); err != nil {
			t.Fatalf("update hosted remote: %v (%s)", err, output)
		}
		second, finderErr := service.GitHistory(context.Background(), root, "10", "")
		if finderErr != nil {
			t.Fatal(finderErr)
		}
		if got := second["hosted_remote"].(map[string]string)["base_url"]; got != "https://github.com/owner/new" {
			t.Fatalf("cached hosted remote after config update = %q", got)
		}
	})

	t.Run("decoration exclusions", func(t *testing.T) {
		root, _ := newCommittedTestRepo(t)
		service, err := New(root, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer service.Close()

		first, finderErr := service.GitHistory(context.Background(), root, "10", "")
		if finderErr != nil {
			t.Fatal(finderErr)
		}
		firstDecoration := first["commits"].([]map[string]any)[0]["decorations"].(string)
		if !strings.Contains(firstDecoration, "master") {
			t.Fatalf("initial decoration = %q, want master branch", firstDecoration)
		}
		if output, err := exec.Command("git", "-C", root, "config", "log.excludeDecoration", "refs/heads").CombinedOutput(); err != nil {
			t.Fatalf("exclude branch decorations: %v (%s)", err, output)
		}
		second, finderErr := service.GitHistory(context.Background(), root, "10", "")
		if finderErr != nil {
			t.Fatal(finderErr)
		}
		secondDecoration := second["commits"].([]map[string]any)[0]["decorations"].(string)
		if strings.Contains(secondDecoration, "master") {
			t.Fatalf("cached decoration after config update = %q, still contains excluded branch", secondDecoration)
		}
	})
}

func TestGitHistoryCachedMetadataRefreshesSignatureStatus(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scope, finderErr := service.openGitHistoryScopeContext(context.Background(), root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer scope.close()
	commits := []map[string]any{{"sha": head, "decorations": "stale", "signature_status": "G"}}
	if finderErr := service.refreshGitHistoryCachedMetadata(context.Background(), scope, commits, true); finderErr != nil {
		t.Fatal(finderErr)
	}
	if commits[0]["signature_status"] != "N" || !strings.Contains(commits[0]["decorations"].(string), "master") {
		t.Fatalf("refreshed cached commit metadata = %#v, want unsigned status and current branch decoration", commits[0])
	}
}

func TestGitHistoryStatsCacheInvalidatesWhenDecoratingRefMoves(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	first, finderErr := service.GitHistory(context.Background(), root, "10", "", true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	firstCommit := first["commits"].([]map[string]any)[0]
	if strings.Contains(firstCommit["decorations"].(string), "tag: moved-ref") {
		t.Fatalf("tag unexpectedly present before ref creation: %#v", firstCommit)
	}
	if output, err := exec.Command("git", "-C", root, "tag", "-m", "moved-ref", "moved-ref").CombinedOutput(); err != nil {
		t.Fatalf("create moved-ref: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("git", "-C", root, "tag", "-d", "moved-ref").Run() })

	second, finderErr := service.GitHistory(context.Background(), root, "10", "", true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	secondCommit := second["commits"].([]map[string]any)[0]
	if !strings.Contains(secondCommit["decorations"].(string), "tag: moved-ref") {
		t.Fatalf("stats history cache did not observe new tag: %#v", secondCommit)
	}
}

func TestGitHistoryStatsStreamsInvalidateWhenDecoratingRefMoves(t *testing.T) {
	for _, mode := range []string{"sequential", "scheduled"} {
		t.Run(mode, func(t *testing.T) {
			root, _ := newCommittedTestRepo(t)
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()

			page, finderErr := service.GitHistory(context.Background(), root, "10", "")
			if finderErr != nil {
				t.Fatal(finderErr)
			}
			cursor := page["snapshot_cursor"].(string)
			stream := func() map[string]any {
				var result map[string]any
				emit := func(payload map[string]any) error {
					result = payload
					return nil
				}
				if mode == "scheduled" {
					_, finderErr = service.GitHistoryStatsScheduled(context.Background(), root, cursor, []int{1}, emit)
				} else {
					finderErr = service.GitHistoryStatsStream(context.Background(), root, cursor, 1, 1, emit)
				}
				if finderErr != nil {
					t.Fatal(finderErr)
				}
				return result
			}

			first := stream()["commits"].([]map[string]any)[0]
			if strings.Contains(first["decorations"].(string), "tag: moved-ref") {
				t.Fatalf("tag unexpectedly present before ref creation: %#v", first)
			}
			if output, err := exec.Command("git", "-C", root, "tag", "-m", "moved-ref", "moved-ref").CombinedOutput(); err != nil {
				t.Fatalf("create moved-ref: %v (%s)", err, output)
			}
			t.Cleanup(func() { _ = exec.Command("git", "-C", root, "tag", "-d", "moved-ref").Run() })

			second := stream()["commits"].([]map[string]any)[0]
			if !strings.Contains(second["decorations"].(string), "tag: moved-ref") {
				t.Fatalf("%s stats stream cache did not observe new tag: %#v", mode, second)
			}
		})
	}
}

func TestGitHistoryExposesDecorationsAndWorktreeStatus(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	for _, args := range [][]string{{"-c", "tag.gpgSign=false", "-C", root, "tag", "v-test"}, {"-C", root, "branch", "test-branch"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("changed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "untracked.txt"), []byte("new\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	history, finderErr := service.GitHistory(context.Background(), root, "10", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	commits := history["commits"].([]map[string]any)
	if len(commits) != 1 {
		t.Fatalf("history commits = %#v", commits)
	}
	branchOutput, err := exec.Command("git", "-C", root, "branch", "--show-current").Output()
	if err != nil || history["branch"] != strings.TrimSpace(string(branchOutput)) {
		t.Fatalf("history branch = %#v, git branch = %q, error = %v", history["branch"], strings.TrimSpace(string(branchOutput)), err)
	}
	if _, ok := commits[0]["signature_status"].(string); !ok {
		t.Fatalf("signature status missing = %#v", commits[0])
	}
	if !strings.Contains(commits[0]["decorations"].(string), "tag: v-test") {
		t.Fatalf("commit decorations = %#v", commits[0]["decorations"])
	}
	status, ok := history["worktree_status"].(map[string]any)
	if !ok || status["available"] != true || status["dirty"] != true {
		t.Fatalf("worktree status = %#v", history["worktree_status"])
	}
	entries := status["entries"].([]map[string]any)
	seen := map[string]string{}
	for _, entry := range entries {
		seen[entry["path"].(string)] = entry["status"].(string)
		if entry["path"] == "note.txt" {
			if entry["counts_available"] != true || entry["added"] != 1 || entry["removed"] != 1 || entry["binary"] != false {
				t.Fatalf("tracked worktree entry counts = %#v", entry)
			}
		}
		if entry["path"] == "untracked.txt" {
			if entry["counts_available"] != true || entry["added"] != 1 || entry["removed"] != 0 || entry["binary"] != false {
				t.Fatalf("untracked worktree entry counts = %#v", entry)
			}
		}
	}
	if seen["note.txt"] != "M" || seen["untracked.txt"] != "??" {
		t.Fatalf("worktree entries = %#v", entries)
	}
	if status["files"] != 2 || status["added"] != 1 || status["removed"] != 1 || status["binary_files"] != 0 || status["stats_available"] != true {
		t.Fatalf("worktree aggregate stats = %#v", status)
	}
	if status["modified_at"].(int64) == 0 {
		t.Fatalf("worktree latest modification time = %#v", status["modified_at"])
	}
	statusCounts, ok := status["status_counts"].(map[string]int)
	if !ok || statusCounts["M"] != 1 || statusCounts["??"] != 1 {
		t.Fatalf("worktree status counts = %#v", status["status_counts"])
	}

	snapshotCursor, ok := history["snapshot_cursor"].(string)
	if !ok || snapshotCursor == "" {
		t.Fatalf("snapshot cursor = %#v", history["snapshot_cursor"])
	}
	restored, finderErr := service.GitHistory(context.Background(), root, "10", snapshotCursor)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	restoredStatus, ok := restored["worktree_status"].(map[string]any)
	if !ok || restoredStatus["available"] != true || restoredStatus["dirty"] != true {
		t.Fatalf("restored worktree status = %#v", restored["worktree_status"])
	}
}

func TestGitViewsRejectNestedObjectSymlinks(t *testing.T) {
	tests := []struct {
		name  string
		setup func(t *testing.T, root, head string)
	}{
		{name: "loose", setup: func(t *testing.T, root, head string) {
			object := filepath.Join(root, ".git", "objects", head[:2], head[2:])
			external := filepath.Join(t.TempDir(), "object")
			if err := os.Rename(object, external); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(external, object); err != nil {
				t.Fatal(err)
			}
		}},
		{name: "pack", setup: func(t *testing.T, root, _ string) {
			if output, err := exec.Command("git", "-C", root, "gc", "--quiet").CombinedOutput(); err != nil {
				t.Fatalf("git gc: %v (%s)", err, output)
			}
			packs, err := filepath.Glob(filepath.Join(root, ".git", "objects", "pack", "*.pack"))
			if err != nil || len(packs) == 0 {
				t.Skip("Git did not create a pack")
			}
			external := filepath.Join(t.TempDir(), filepath.Base(packs[0]))
			if err := os.Rename(packs[0], external); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(external, packs[0]); err != nil {
				t.Fatal(err)
			}
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root, head := newCommittedTestRepo(t)
			test.setup(t, root, head)
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()
			if _, finderErr := service.GitHistory(context.Background(), root, "1", ""); finderErr == nil || finderErr.Status != http.StatusConflict {
				t.Fatalf("nested object symlink = %v", finderErr)
			}
		})
	}
}

func TestBlameOutputLimitIsTyped(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	path := filepath.Join(root, "note.txt")
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if arg == "blame" {
				command := exec.CommandContext(ctx, "head", "-c", strconv.Itoa(MaxReadBytes+1), "/dev/zero")
				command.ExtraFiles = extraFiles
				command.Env = gitCommandEnvironment()
				return command
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	if _, finderErr := service.Blame(context.Background(), path, ""); finderErr == nil || finderErr.Status != http.StatusRequestEntityTooLarge {
		t.Fatalf("blame output limit = %v", finderErr)
	}
}

func TestBlameUsesPythonFallbackWhenGitReturnsNoDiagnostic(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	path := filepath.Join(root, "note.txt")
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if arg == "blame" {
				command := exec.CommandContext(ctx, "false")
				command.ExtraFiles = extraFiles
				command.Env = gitCommandEnvironment()
				return command
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	payload, finderErr := service.Blame(context.Background(), path, "")
	if finderErr != nil || payload["error"] != "not committed yet" {
		t.Fatalf("undignosed blame failure = %#v, error = %v", payload, finderErr)
	}
}

func TestGitViewsIgnoreAmbientGitEnvironment(t *testing.T) {
	root := t.TempDir()
	workingRepo := filepath.Join(root, "working")
	ambientRepo := filepath.Join(root, "ambient")
	for _, repo := range []string{workingRepo, ambientRepo} {
		if err := os.Mkdir(repo, 0o700); err != nil {
			t.Fatal(err)
		}
		for _, args := range [][]string{{"init", "-q", repo}, {"-C", repo, "config", "user.email", "test@example.invalid"}, {"-C", repo, "config", "user.name", "Finder Test"}} {
			if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
				t.Fatalf("git %v: %v (%s)", args, err, output)
			}
		}
	}
	path := filepath.Join(workingRepo, "note.txt")
	if err := os.WriteFile(path, []byte("working\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", workingRepo, "add", "note.txt").CombinedOutput(); err != nil {
		t.Fatalf("git add: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", workingRepo, "commit", "-qm", "working").CombinedOutput(); err != nil {
		t.Fatalf("git commit: %v (%s)", err, output)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	t.Setenv("GIT_DIR", filepath.Join(ambientRepo, ".git"))
	t.Setenv("GIT_INDEX_FILE", filepath.Join(ambientRepo, ".git", "index"))
	info, finderErr := service.Info(context.Background(), path, true)
	if finderErr != nil || info["git_root"] != workingRepo || info["git_tracked"] != true {
		t.Fatalf("ambient Git environment changed Info: payload=%#v error=%v", info, finderErr)
	}
	diff, finderErr := service.Diff(context.Background(), path, "HEAD", "current")
	if finderErr != nil || diff["repo"] != workingRepo || diff["untracked"] != false || !strings.Contains(diff["original"].(string), "working") {
		t.Fatalf("ambient Git environment changed Diff: payload=%#v error=%v", diff, finderErr)
	}
}

func TestDiffFallsBackFromUnknownExplicitRefForTrackedFile(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	path := filepath.Join(root, "note.txt")
	if err := os.WriteFile(path, []byte("new\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	diff, finderErr := service.Diff(context.Background(), path, "not-in-this-repo", "current")
	if finderErr != nil {
		t.Fatalf("tracked diff = %#v", finderErr)
	}
	if diff["from_ref"] != "HEAD" || diff["to_ref"] != "current" || diff["untracked"] != false || !strings.Contains(diff["diff"].(string), "+new") {
		t.Fatalf("tracked diff fallback = %#v", diff)
	}
}

func TestGitViewsHandleHistoricalDeletedAndBinaryFiles(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{{"init", "-q", root}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "config", "user.name", "Finder Test"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	deleted := filepath.Join(root, "deleted.txt")
	binary := filepath.Join(root, "binary.dat")
	if err := os.WriteFile(deleted, []byte("old\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(binary, []byte("before\x00"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "deleted.txt", "binary.dat").CombinedOutput(); err != nil {
		t.Fatalf("git add: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "base").CombinedOutput(); err != nil {
		t.Fatalf("git commit: %v (%s)", err, output)
	}
	baseOutput, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	base := strings.TrimSpace(string(baseOutput))
	if err := os.Remove(deleted); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(binary, []byte("after\x00"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "-A").CombinedOutput(); err != nil {
		t.Fatalf("git add update: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "change").CombinedOutput(); err != nil {
		t.Fatalf("git commit update: %v (%s)", err, output)
	}
	changedOutput, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	changed := strings.TrimSpace(string(changedOutput))
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	deletedDiff, finderErr := service.Diff(context.Background(), deleted, base, changed)
	if finderErr != nil || deletedDiff["from_ref"] != base || deletedDiff["to_ref"] != changed || deletedDiff["working_missing"] != true || deletedDiff["original"] != "old\n" || !strings.Contains(deletedDiff["diff"].(string), "-old") {
		t.Fatalf("historical deleted diff = %#v, error = %v", deletedDiff, finderErr)
	}
	binaryDiff, finderErr := service.Diff(context.Background(), binary, base, changed)
	if finderErr != nil || binaryDiff["original_error"] == "" || binaryDiff["working_error"] == "" || !strings.Contains(binaryDiff["diff"].(string), "Binary files") {
		t.Fatalf("historical binary diff = %#v, error = %v", binaryDiff, finderErr)
	}
}

func TestDiffUsesPythonUnifiedContextShape(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	path := filepath.Join(root, "note.txt")
	original := "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"
	if err := os.WriteFile(path, []byte(original), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "note.txt").CombinedOutput(); err != nil {
		t.Fatalf("git add baseline: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "ten lines").CombinedOutput(); err != nil {
		t.Fatalf("git commit baseline: %v (%s)", err, output)
	}
	updated := strings.Replace(original, "eight\n", "EIGHT\n", 1)
	if err := os.WriteFile(path, []byte(updated), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	payload, finderErr := service.Diff(context.Background(), path, "HEAD", "current")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	want := "diff --git a/note.txt b/note.txt\n--- a/note.txt\n+++ b/note.txt\n@@ -5,6 +5,6 @@\n five\n six\n seven\n-eight\n+EIGHT\n nine\n ten\n"
	if payload["diff"] != want {
		t.Fatalf("Python unified diff shape = %q, want %q", payload["diff"], want)
	}
}

func TestGitViewsRejectExternalAlternateObjectStore(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "source")
	checkout := filepath.Join(root, "checkout")
	if err := os.Mkdir(source, 0o700); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"init", "-q", source}, {"-C", source, "config", "user.email", "test@example.invalid"}, {"-C", source, "config", "user.name", "Finder Test"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(source, "note.txt"), []byte("source\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", source, "add", "note.txt").CombinedOutput(); err != nil {
		t.Fatalf("git add: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", source, "commit", "-qm", "source").CombinedOutput(); err != nil {
		t.Fatalf("git commit: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "clone", "--shared", source, checkout).CombinedOutput(); err != nil {
		t.Fatalf("git clone: %v (%s)", err, output)
	}
	service, err := New(checkout, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	_, finderErr := service.Diff(context.Background(), filepath.Join(checkout, "note.txt"), "HEAD", "current")
	if finderErr == nil || finderErr.Status != http.StatusForbidden || finderErr.Code != "forbidden" {
		t.Fatalf("external alternate object store was not rejected: %#v", finderErr)
	}
}

func TestGitHistoryAndCommitContracts(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{{"init", "-q", root}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "config", "user.name", "Finder Test"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	file := filepath.Join(root, "note.txt")
	if err := os.WriteFile(file, []byte("one\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "note.txt").CombinedOutput(); err != nil {
		t.Fatalf("git add: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "first").CombinedOutput(); err != nil {
		t.Fatalf("git commit first: %v (%s)", err, output)
	}
	if err := os.WriteFile(file, []byte("one\ntwo\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "added.txt"), []byte("added\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "-A").CombinedOutput(); err != nil {
		t.Fatalf("git add second: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "second").CombinedOutput(); err != nil {
		t.Fatalf("git commit second: %v (%s)", err, output)
	}
	headOutput, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	head := strings.TrimSpace(string(headOutput))
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	firstPage, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	commits, ok := firstPage["commits"].([]map[string]any)
	if !ok || len(commits) != 1 || commits[0]["subject"] != "second" || firstPage["head"] != head || firstPage["next_cursor"] == "" {
		t.Fatalf("first history page = %#v", firstPage)
	}
	for _, key := range []string{"files", "added", "removed", "binary_files"} {
		if _, present := commits[0][key]; present {
			t.Fatalf("fast history metadata unexpectedly contains %q: %#v", key, commits[0])
		}
	}
	if commits[0]["metadata_truncated"] != false {
		t.Fatalf("history metadata truncation marker = %#v", commits[0]["metadata_truncated"])
	}
	statsPage, finderErr := service.GitHistory(context.Background(), root, "1", "", true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	statsCommits, ok := statsPage["commits"].([]map[string]any)
	if !ok || len(statsCommits) != 1 || statsCommits[0]["files"] != 2 || statsCommits[0]["added"] != 2 || statsCommits[0]["removed"] != 0 || statsCommits[0]["binary_files"] != 0 {
		t.Fatalf("Git history stats page = %#v", statsPage)
	}
	windowPage, finderErr := service.GitHistoryAtOffset(context.Background(), root, "1", firstPage["snapshot_cursor"].(string), 1)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	windowCommits, ok := windowPage["commits"].([]map[string]any)
	if !ok || len(windowCommits) != 1 || windowCommits[0]["subject"] != "first" || windowCommits[0]["files"] != 1 || windowCommits[0]["added"] != 1 || windowCommits[0]["removed"] != 0 {
		t.Fatalf("Git history independent stats offset = %#v", windowPage)
	}
	var oneAtATimeChunks []map[string]any
	if finderErr := service.GitHistoryStatsStream(context.Background(), root, firstPage["snapshot_cursor"].(string), 2, 1, func(payload map[string]any) error {
		oneAtATimeChunks = append(oneAtATimeChunks, payload)
		return nil
	}); finderErr != nil {
		t.Fatal(finderErr)
	}
	if len(oneAtATimeChunks) != 2 {
		t.Fatalf("one-at-a-time stats chunks = %#v", oneAtATimeChunks)
	}
	for index, chunk := range oneAtATimeChunks {
		chunkCommits, ok := chunk["commits"].([]map[string]any)
		if !ok || len(chunkCommits) != 1 || chunkCommits[0]["files"] == nil {
			t.Fatalf("one-at-a-time stats chunk %d = %#v", index, chunk)
		}
	}
	var batchedChunks []map[string]any
	if finderErr := service.GitHistoryStatsStream(context.Background(), root, firstPage["snapshot_cursor"].(string), 2, 10, func(payload map[string]any) error {
		batchedChunks = append(batchedChunks, payload)
		return nil
	}); finderErr != nil {
		t.Fatal(finderErr)
	}
	if len(batchedChunks) != 1 {
		t.Fatalf("batched stats chunks = %#v", batchedChunks)
	}
	batchedCommits, ok := batchedChunks[0]["commits"].([]map[string]any)
	if !ok || len(batchedCommits) != 2 || batchedCommits[0]["subject"] != "second" || batchedCommits[1]["subject"] != "first" {
		t.Fatalf("batched stats commits = %#v", batchedChunks[0])
	}
	var scheduledChunks []map[string]any
	nextScheduledCursor, finderErr := service.GitHistoryStatsScheduled(context.Background(), root, firstPage["snapshot_cursor"].(string), []int{1, 2, 4}, func(payload map[string]any) error {
		scheduledChunks = append(scheduledChunks, payload)
		return nil
	})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if nextScheduledCursor != "" || len(scheduledChunks) != 2 {
		t.Fatalf("scheduled stats chunks/cursor = %d/%q", len(scheduledChunks), nextScheduledCursor)
	}
	scheduledSHAs := make(map[string]bool)
	for index, chunk := range scheduledChunks {
		chunkCommits, ok := chunk["commits"].([]map[string]any)
		if !ok || len(chunkCommits) == 0 {
			t.Fatalf("scheduled stats chunk %d = %#v", index, chunk)
		}
		for _, commit := range chunkCommits {
			sha, _ := commit["sha"].(string)
			scheduledSHAs[sha] = true
			for _, key := range []string{"files", "added", "removed", "binary_files"} {
				if _, present := commit[key]; !present {
					t.Fatalf("scheduled stats commit %q missing %q: %#v", sha, key, commit)
				}
			}
		}
	}
	if len(scheduledSHAs) != 2 {
		t.Fatalf("scheduled stats SHAs = %#v", scheduledSHAs)
	}
	cursor := firstPage["next_cursor"].(string)
	secondPage, finderErr := service.GitHistory(context.Background(), root, "1", cursor)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	secondCommits, ok := secondPage["commits"].([]map[string]any)
	if !ok || len(secondCommits) != 1 || secondCommits[0]["subject"] != "first" || secondPage["next_cursor"] != "" {
		t.Fatalf("second history page = %#v", secondPage)
	}
	detail, finderErr := service.GitCommit(context.Background(), root, commits[0]["sha"].(string), head)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	files, ok := detail["files"].([]map[string]any)
	if !ok || len(files) != 2 || detail["from_ref"] != secondCommits[0]["sha"] || detail["to_ref"] != head {
		t.Fatalf("commit detail = %#v", detail)
	}
	if files[0]["added"] != 1 || files[0]["removed"] != 0 || files[0]["binary"] != false {
		t.Fatalf("commit file counts = %#v", files)
	}
	rootDetail, finderErr := service.GitCommit(context.Background(), root, secondCommits[0]["sha"].(string), head)
	if finderErr != nil || rootDetail["from_ref"] != "4b825dc642cb6eb9a060e54bf8d69288fbee4904" {
		t.Fatalf("root commit detail = %#v, error = %v", rootDetail, finderErr)
	}
	if _, finderErr = service.GitHistory(context.Background(), filepath.Join(root, "note.txt"), "1", ""); finderErr == nil || finderErr.MessageKey != "fs.error.gitHistoryDirectoryRequired" {
		t.Fatalf("file-valued history path = %v", finderErr)
	}
	if err := os.WriteFile(filepath.Join(root, "later.txt"), []byte("later\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "later.txt").CombinedOutput(); err != nil {
		t.Fatalf("git add later: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "later").CombinedOutput(); err != nil {
		t.Fatalf("git commit later: %v (%s)", err, output)
	}
	latestHeadOutput, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	latestHead := strings.TrimSpace(string(latestHeadOutput))
	continued, finderErr := service.GitHistory(context.Background(), root, "1", cursor)
	if finderErr != nil {
		t.Fatalf("ancestor history cursor after HEAD advance = %v", finderErr)
	}
	continuedCommits, ok := continued["commits"].([]map[string]any)
	if !ok || len(continuedCommits) != 1 || continuedCommits[0]["subject"] != "first" {
		t.Fatalf("ancestor history page after HEAD advance = %#v", continued)
	}
	showCallsAfterHeadAdvance := 0
	mergeBaseCallsAfterHeadAdvance := 0
	headObjectValidationsAfterHeadAdvance := 0
	commitObjectLookupsAfterHeadAdvance := 0
	originalCommand := execCommandContext
	t.Cleanup(func() { execCommandContext = originalCommand })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if arg == "show" {
				showCallsAfterHeadAdvance++
			}
			if arg == "merge-base" {
				mergeBaseCallsAfterHeadAdvance++
			}
			if arg == "HEAD^{commit}" {
				headObjectValidationsAfterHeadAdvance++
			} else if strings.HasSuffix(arg, "^{commit}") {
				commitObjectLookupsAfterHeadAdvance++
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	advancedDetail, finderErr := service.GitCommit(context.Background(), root, commits[0]["sha"].(string), latestHead)
	if finderErr != nil {
		t.Fatalf("cached commit detail after HEAD advance = %v", finderErr)
	}
	advancedFiles, ok := advancedDetail["files"].([]map[string]any)
	if !ok || len(advancedFiles) != len(files) || advancedDetail["to_ref"] != commits[0]["sha"] {
		t.Fatalf("commit detail after HEAD advance = %#v", advancedDetail)
	}
	if showCallsAfterHeadAdvance != 0 {
		t.Fatalf("cached commit detail after HEAD advance ran %d Git show commands", showCallsAfterHeadAdvance)
	}
	if mergeBaseCallsAfterHeadAdvance != 0 || headObjectValidationsAfterHeadAdvance != 1 || commitObjectLookupsAfterHeadAdvance != 0 {
		t.Fatalf("cached detail validation after HEAD advance used %d merge-base, %d HEAD validation, and %d candidate object lookup commands", mergeBaseCallsAfterHeadAdvance, headObjectValidationsAfterHeadAdvance, commitObjectLookupsAfterHeadAdvance)
	}
	if _, finderErr = service.GitCommit(context.Background(), root, strings.Repeat("0", 40), latestHead); finderErr == nil || finderErr.Status != http.StatusNotFound {
		t.Fatalf("unknown commit = %v", finderErr)
	}
}

func TestGitHistoryAtOffsetUsesTargetedStatsForRootRenameAndBinaryCommits(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{{"init", "-q", root}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "config", "user.name", "Finder Test"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	runGit := func(args ...string) {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	oldPath := filepath.Join(root, "old.txt")
	if err := os.WriteFile(oldPath, []byte("old\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runGit("-C", root, "add", "old.txt")
	runGit("-C", root, "commit", "-qm", "root")
	newPath := filepath.Join(root, "new.txt")
	if err := os.Rename(oldPath, newPath); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(newPath, []byte("new\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runGit("-C", root, "add", "-A")
	runGit("-C", root, "commit", "-qm", "rename")
	if err := os.WriteFile(filepath.Join(root, "binary.bin"), []byte{0x00, 0x01, 0x02, 0xff}, 0o600); err != nil {
		t.Fatal(err)
	}
	runGit("-C", root, "add", "binary.bin")
	runGit("-C", root, "commit", "-qm", "binary")
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	metadata, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	cursor, ok := metadata["snapshot_cursor"].(string)
	if !ok || cursor == "" {
		t.Fatalf("metadata snapshot cursor = %#v", metadata["snapshot_cursor"])
	}
	originalExec := execCommandContext
	var calls [][]string
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		calls = append(calls, append([]string(nil), args...))
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	t.Cleanup(func() { execCommandContext = originalExec })
	rows := make([]map[string]any, 0, 3)
	for offset := 0; offset < 3; offset++ {
		page, pageErr := service.GitHistoryAtOffset(context.Background(), root, "1", cursor, offset)
		if pageErr != nil {
			t.Fatalf("offset %d: %v", offset, pageErr)
		}
		commits, ok := page["commits"].([]map[string]any)
		if !ok || len(commits) != 1 {
			t.Fatalf("offset %d commits = %#v", offset, page["commits"])
		}
		rows = append(rows, commits[0])
	}
	if rows[0]["subject"] != "binary" || rows[0]["files"] != 1 || rows[0]["added"] != 0 || rows[0]["removed"] != 0 || rows[0]["binary_files"] != 1 {
		t.Fatalf("binary stats = %#v", rows[0])
	}
	if rows[1]["subject"] != "rename" || rows[1]["files"] != 2 || rows[1]["added"] != 1 || rows[1]["removed"] != 1 || rows[1]["binary_files"] != 0 {
		t.Fatalf("rename stats = %#v", rows[1])
	}
	if rows[2]["subject"] != "root" || rows[2]["files"] != 1 || rows[2]["added"] != 1 || rows[2]["removed"] != 0 || rows[2]["binary_files"] != 0 {
		t.Fatalf("root stats = %#v", rows[2])
	}
	var targetedStatsSHAs []string
	for _, args := range calls {
		isLog := false
		isShow := false
		hasNumstat := false
		for _, arg := range args {
			isLog = isLog || arg == "log"
			isShow = isShow || arg == "show"
			hasNumstat = hasNumstat || arg == "--numstat"
		}
		if isLog && hasNumstat {
			t.Fatalf("offset stats used history-wide numstat: %v", args)
		}
		if isShow && hasNumstat {
			for _, row := range rows {
				sha, _ := row["sha"].(string)
				for _, arg := range args {
					if arg == sha {
						targetedStatsSHAs = append(targetedStatsSHAs, sha)
					}
				}
			}
		}
	}
	if len(targetedStatsSHAs) != len(rows) {
		t.Fatalf("offset stats did not run one targeted batched-show product per visible SHA: SHAs=%v rows=%v calls=%v", targetedStatsSHAs, rows, calls)
	}
	historyHead, ok := metadata["head"].(string)
	if !ok || historyHead == "" {
		t.Fatalf("history head = %#v", metadata["head"])
	}
	for _, testCase := range []struct {
		name        string
		row         map[string]any
		wantPaths   map[string]struct{}
		wantAdded   int
		wantRemoved int
		wantBinary  int
	}{
		{name: "binary", row: rows[0], wantPaths: map[string]struct{}{"binary.bin": {}}, wantBinary: 1},
		{name: "rename", row: rows[1], wantPaths: map[string]struct{}{"old.txt": {}, "new.txt": {}}, wantAdded: 1, wantRemoved: 1},
		{name: "root", row: rows[2], wantPaths: map[string]struct{}{"old.txt": {}}, wantAdded: 1},
	} {
		sha, _ := testCase.row["sha"].(string)
		detail, detailErr := service.GitCommit(context.Background(), root, sha, historyHead)
		if detailErr != nil {
			t.Fatalf("%s commit detail: %v", testCase.name, detailErr)
		}
		files, ok := detail["files"].([]map[string]any)
		if !ok || len(files) != len(testCase.wantPaths) {
			t.Fatalf("%s detail files = %#v", testCase.name, detail["files"])
		}
		gotPaths := make(map[string]struct{}, len(files))
		added, removed, binary := 0, 0, 0
		for _, file := range files {
			path, _ := file["path"].(string)
			gotPaths[path] = struct{}{}
			if file["binary"] == true {
				binary++
			}
			if count, ok := file["added"].(int); ok {
				added += count
			}
			if count, ok := file["removed"].(int); ok {
				removed += count
			}
		}
		pathsMatch := len(gotPaths) == len(testCase.wantPaths)
		for path := range testCase.wantPaths {
			_, present := gotPaths[path]
			pathsMatch = pathsMatch && present
		}
		if detail["to_ref"] != sha || !pathsMatch || added != testCase.wantAdded || removed != testCase.wantRemoved || binary != testCase.wantBinary {
			t.Fatalf("%s detail = %#v, paths=%v added=%d removed=%d binary=%d", testCase.name, detail, gotPaths, added, removed, binary)
		}
	}
}

func TestGitHistoryDefaultAndMaximumLimitsMatchPythonContract(t *testing.T) {
	if got, err := gitHistoryLimit(""); err != nil || got != 200 {
		t.Fatalf("default Git history limit = %d, error = %v; want 200", got, err)
	}
	for _, raw := range []string{"0", "-1", "-999"} {
		if got, err := gitHistoryLimit(raw); err != nil || got != 1 {
			t.Fatalf("clamped Git history limit %q = %d, error = %v; want 1", raw, got, err)
		}
	}
	if got, err := gitHistoryLimit("999"); err != nil || got != 200 {
		t.Fatalf("clamped Git history limit = %d, error = %v; want 200", got, err)
	}
	if got, err := gitHistoryLimit("200"); err != nil || got != 200 {
		t.Fatalf("maximum Git history limit = %d, error = %v; want 200", got, err)
	}
}

func TestGitCommitCountsIgnoreLocalDiffAlgorithm(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	for _, content := range []string{"d\nc\nd\na\nd\na\n", "d\nc\nc\nd\nd\na\n"} {
		if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		for _, args := range [][]string{{"add", "note.txt"}, {"commit", "-m", "ambiguous change"}} {
			if output, err := exec.Command("git", append([]string{"-C", root}, args...)...).CombinedOutput(); err != nil {
				t.Fatalf("git %v: %v (%s)", args, err, output)
			}
		}
	}
	for _, initial := range []string{"histogram", "myers"} {
		t.Run(initial, func(t *testing.T) {
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()
			for _, algorithm := range []string{initial, "patience", "histogram", "myers"} {
				if output, err := exec.Command("git", "-C", root, "config", "diff.algorithm", algorithm).CombinedOutput(); err != nil {
					t.Fatalf("set diff algorithm: %v (%s)", err, output)
				}
				history, finderErr := service.GitHistory(context.Background(), root, "1", "", true)
				if finderErr != nil {
					t.Fatal(finderErr)
				}
				row := history["commits"].([]map[string]any)[0]
				if row["added"] != 1 || row["removed"] != 1 {
					t.Fatalf("%s history counts = %#v, want 1 added and 1 removed", algorithm, row)
				}
				sha := row["sha"].(string)
				detail, finderErr := service.GitCommit(context.Background(), root, sha, sha)
				if finderErr != nil {
					t.Fatal(finderErr)
				}
				files := detail["files"].([]map[string]any)
				if len(files) != 1 || files[0]["added"] != 1 || files[0]["removed"] != 1 {
					t.Fatalf("%s detail counts = %#v, want 1 added and 1 removed", algorithm, files)
				}
			}
		})
	}
}

func TestGitHistoryAndCommitCacheImmutableProducts(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	calls := map[string]int{}
	historyProducts, metadataRefreshes := 0, 0
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		showProduct := false
		hasRaw, hasNumstat := false, false
		for _, arg := range args {
			if arg == "log" || arg == "show" {
				calls[arg]++
			}
			if arg == "--topo-order" {
				historyProducts++
			}
			if arg == "--no-walk" {
				metadataRefreshes++
			}
			if arg == "show" {
				showProduct = true
			}
			hasRaw = hasRaw || arg == "--raw"
			hasNumstat = hasNumstat || arg == "--numstat"
			if arg == "diff" || arg == "diff-tree" {
				calls["diff"]++
			}
		}
		if showProduct && hasRaw && hasNumstat {
			calls["commit_product"]++
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}

	firstHistory, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	firstHistory["commits"].([]map[string]any)[0]["subject"] = "caller mutation"
	secondHistory, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if historyProducts != 1 || metadataRefreshes != 1 || secondHistory["commits"].([]map[string]any)[0]["subject"] != "initial" {
		t.Fatalf("history cache product calls=%d metadata refreshes=%d commands=%#v payload=%#v", historyProducts, metadataRefreshes, calls, secondHistory)
	}

	firstCommit, finderErr := service.GitCommit(context.Background(), root, head, head)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	firstCommit["subject"] = "caller mutation"
	secondCommit, finderErr := service.GitCommit(context.Background(), root, head, head)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if calls["show"] != 1 || calls["commit_product"] != 1 || calls["diff"] != 0 || secondCommit["subject"] != "initial" {
		t.Fatalf("commit cache calls=%#v payload=%#v", calls, secondCommit)
	}
}

func TestGitCommitCacheReusesValidatedAncestryForFrozenSHA(t *testing.T) {
	root, oldHead := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("next\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"add", "--", "note.txt"}, {"commit", "-m", "next"}} {
		command := exec.Command("git", append([]string{"-C", root}, args...)...)
		if output, err := command.CombinedOutput(); err != nil {
			t.Fatalf("git %s: %v (%s)", strings.Join(args, " "), err, output)
		}
	}
	output, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	currentHead := strings.TrimSpace(string(output))
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	mergeBaseCalls, commitProductCalls := 0, 0
	original := execCommandContext
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for index, arg := range args {
			if arg == "merge-base" && index+1 < len(args) && args[index+1] == "--is-ancestor" {
				mergeBaseCalls++
			}
		}
		hasShow, hasRaw, hasNumstat := false, false, false
		for _, arg := range args {
			hasShow = hasShow || arg == "show"
			hasRaw = hasRaw || arg == "--raw"
			hasNumstat = hasNumstat || arg == "--numstat"
		}
		if hasShow && hasRaw && hasNumstat {
			commitProductCalls++
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	t.Cleanup(func() { execCommandContext = original })

	for range 2 {
		if _, finderErr := service.GitCommit(context.Background(), root, oldHead, currentHead); finderErr != nil {
			t.Fatal(finderErr)
		}
	}
	if mergeBaseCalls != 1 || commitProductCalls != 1 {
		t.Fatalf("cached immutable detail commands = ancestry %d, product %d; want one of each", mergeBaseCalls, commitProductCalls)
	}
}

func TestGitCommitDoesNotRequireDisplayOnlyRemoteMetadata(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	padding := bytes.Repeat([]byte("#"+strings.Repeat("x", 1022)+"\n"), (gitConfigMaxBytes/1024)+2)
	config, err := os.OpenFile(filepath.Join(root, ".git", "config"), os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := config.Write(padding); err != nil {
		_ = config.Close()
		t.Fatal(err)
	}
	if err := config.Close(); err != nil {
		t.Fatal(err)
	}

	if _, finderErr := service.sessionFilesRepositoryForTarget(context.Background(), root, false); finderErr != nil {
		t.Fatalf("session-files repository discovery depended on display-only remote metadata: %v", finderErr)
	}
	if _, finderErr := service.GitHistoryWorktree(context.Background(), root); finderErr != nil {
		t.Fatalf("worktree status depended on display-only remote metadata: %v", finderErr)
	}
	detail, finderErr := service.GitCommit(context.Background(), root, head, head)
	if finderErr != nil {
		t.Fatalf("commit detail depended on display-only remote metadata: %v", finderErr)
	}
	if detail["sha"] != head {
		t.Fatalf("commit detail SHA = %#v, want %s", detail["sha"], head)
	}
}

func TestGitCommitStillRejectsSymlinkedGitMetadataFiles(t *testing.T) {
	for _, name := range []string{"config", "HEAD"} {
		t.Run(name, func(t *testing.T) {
			root, head := newCommittedTestRepo(t)
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()

			path := filepath.Join(root, ".git", name)
			regularPath := path + ".regular"
			target := filepath.Base(regularPath)
			if name == "HEAD" {
				ref, err := exec.Command("git", "-C", root, "symbolic-ref", "HEAD").Output()
				if err != nil {
					t.Fatal(err)
				}
				target = strings.TrimSpace(string(ref))
			}
			if err := os.Rename(path, regularPath); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(target, path); err != nil {
				t.Skipf("symlinks unavailable: %v", err)
			}

			if _, finderErr := service.GitCommit(context.Background(), root, head, head); finderErr == nil {
				t.Fatalf("Git commit accepted symlinked Git %s", name)
			} else if finderErr.Code != "git_repository_changed" {
				t.Fatalf("symlinked Git %s result = %v, want git_repository_changed", name, finderErr)
			}
		})
	}
}

func TestGitHistoryStatsCacheReusesSHAStatsAcrossHistoryProducts(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	firstPage, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	cursor, ok := firstPage["snapshot_cursor"].(string)
	if !ok || cursor == "" {
		t.Fatalf("history snapshot cursor = %#v", firstPage["snapshot_cursor"])
	}
	statsProductCalls := 0
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if arg == "--numstat" {
				statsProductCalls++
				break
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	if _, finderErr = service.GitHistoryAtOffset(context.Background(), root, "1", cursor, 0); finderErr != nil {
		t.Fatal(finderErr)
	}
	if _, finderErr = service.GitHistoryAtOffset(context.Background(), root, "2", cursor, 0); finderErr != nil {
		t.Fatal(finderErr)
	}
	if statsProductCalls != 1 {
		t.Fatalf("numstat products after changing only the history product shape = %d, want one cached SHA product", statsProductCalls)
	}
}

func TestGitHistoryStatsStreamPreservesSignatureStatusAcrossSHACacheHits(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	firstPage, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	cursor := firstPage["snapshot_cursor"].(string)
	statsProductCalls := 0
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if arg == "--numstat" {
				statsProductCalls++
				break
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	for _, limit := range []string{"1", "2"} {
		payload, finderErr := service.GitHistoryAtOffset(context.Background(), root, limit, cursor, 0)
		if finderErr != nil {
			t.Fatal(finderErr)
		}
		commit := payload["commits"].([]map[string]any)[0]
		if status, ok := commit["signature_status"].(string); !ok || status == "" {
			t.Fatalf("stats signature status = %#v, want a nonempty Git status", commit["signature_status"])
		}
	}
	if statsProductCalls != 1 {
		t.Fatalf("numstat products across differently shaped stats pages = %d, want one cached SHA product", statsProductCalls)
	}
}

func TestGitHistoryStatsCacheInvalidatesWhenShallowBoundaryMoves(t *testing.T) {
	source, _ := newCommittedTestRepo(t)
	addedPath := filepath.Join(source, "added.txt")
	if err := os.WriteFile(addedPath, []byte("two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", source, "add", "--", "added.txt"}, {"-C", source, "commit", "-qm", "second"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	shallow := filepath.Join(t.TempDir(), "shallow")
	sourceURL := (&url.URL{Scheme: "file", Path: source}).String()
	if output, err := exec.Command("git", "clone", "--quiet", "--depth=1", sourceURL, shallow).CombinedOutput(); err != nil {
		t.Fatalf("shallow clone: %v (%s)", err, output)
	}
	service, err := New(shallow, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	before, finderErr := service.GitHistory(context.Background(), shallow, "1", "", true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	beforeCommit := before["commits"].([]map[string]any)[0]
	if beforeCommit["files"] != 2 || beforeCommit["added"] != 2 {
		t.Fatalf("shallow commit stats = (%v files, %v added), want (2, 2)", beforeCommit["files"], beforeCommit["added"])
	}
	beforeDetail, finderErr := service.GitCommit(context.Background(), shallow, beforeCommit["sha"].(string), before["head"].(string))
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if files := beforeDetail["files"].([]map[string]any); len(files) != 2 {
		t.Fatalf("shallow commit detail files = %d, want 2: %#v", len(files), files)
	}
	if output, err := exec.Command("git", "-C", shallow, "fetch", "--quiet", "--unshallow").CombinedOutput(); err != nil {
		t.Fatalf("unshallow fetch: %v (%s)", err, output)
	}
	after, finderErr := service.GitHistory(context.Background(), shallow, "2", "", true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	afterCommit := after["commits"].([]map[string]any)[0]
	if afterCommit["sha"] != beforeCommit["sha"] {
		t.Fatalf("latest commit changed across deepen: before=%v after=%v", beforeCommit["sha"], afterCommit["sha"])
	}
	if afterCommit["files"] != 1 || afterCommit["added"] != 1 {
		t.Fatalf("deepened commit stats = (%v files, %v added), want (1, 1)", afterCommit["files"], afterCommit["added"])
	}
	afterDetail, finderErr := service.GitCommit(context.Background(), shallow, afterCommit["sha"].(string), after["head"].(string))
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if before["shallow_boundary"] == "" || before["shallow_boundary"] == after["shallow_boundary"] {
		t.Fatalf("history must expose changed shallow boundary: before=%v after=%v", before["shallow_boundary"], after["shallow_boundary"])
	}
	if beforeDetail["shallow_boundary"] != before["shallow_boundary"] || afterDetail["shallow_boundary"] != after["shallow_boundary"] {
		t.Fatal("commit detail and history must share the shallow boundary")
	}
	if files := afterDetail["files"].([]map[string]any); len(files) != 1 {
		t.Fatalf("deepened commit detail files = %d, want 1: %#v", len(files), files)
	}
}

func TestGitHistoryStatsRejectsShallowBoundaryChangeDuringProduct(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "second.txt"), []byte("two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "--", "second.txt"}, {"-C", root, "commit", "-qm", "second"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	page, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	shallowPath := filepath.Join(root, ".git", "shallow")
	original := execCommandContext
	changed := false
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if arg == "--numstat" && !changed {
				changed = true
				if err := os.WriteFile(shallowPath, []byte(page["head"].(string)+"\n"), 0o600); err != nil {
					t.Fatal(err)
				}
				break
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	if _, finderErr := service.GitHistoryAtOffset(context.Background(), root, "1", page["snapshot_cursor"].(string), 0); finderErr == nil || finderErr.Code != "git_repository_changed" {
		t.Fatalf("stats during shallow-boundary change = %v, want git_repository_changed", finderErr)
	}
	if err := os.Remove(shallowPath); err != nil {
		t.Fatal(err)
	}
	execCommandContext = original
	recovered, finderErr := service.GitHistoryAtOffset(context.Background(), root, "1", page["snapshot_cursor"].(string), 0)
	if finderErr != nil {
		t.Fatalf("stats after restoring the original shallow boundary: %v", finderErr)
	}
	commit := recovered["commits"].([]map[string]any)[0]
	if commit["files"] != 1 || commit["added"] != 1 {
		t.Fatalf("stats cache retained rejected shallow-boundary product: files=%v added=%v, want (1, 1)", commit["files"], commit["added"])
	}
}

func TestGitCommitReachabilityRejectsShallowBoundaryChangeWithoutCaching(t *testing.T) {
	root, ancestor := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "second.txt"), []byte("two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "--", "second.txt"}, {"-C", root, "commit", "-qm", "second"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	headRaw, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	head := strings.TrimSpace(string(headRaw))
	shallowPath := filepath.Join(root, ".git", "shallow")
	if err := os.WriteFile(shallowPath, []byte(head+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	original := execCommandContext
	changed := false
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if arg == "merge-base" && !changed {
				changed = true
				if err := os.Remove(shallowPath); err != nil {
					t.Fatal(err)
				}
				break
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	if _, finderErr := service.GitCommit(context.Background(), root, ancestor, head); finderErr == nil || finderErr.Code != "git_repository_changed" {
		t.Fatalf("detail during shallow-boundary change = %v, want git_repository_changed", finderErr)
	}
	if err := os.WriteFile(shallowPath, []byte(head+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	execCommandContext = original
	if _, finderErr := service.GitCommit(context.Background(), root, ancestor, head); finderErr == nil || finderErr.Code != "unknown_commit" {
		t.Fatalf("detail after restoring shallow boundary = %v, want unknown_commit", finderErr)
	}
}

func TestGitHistoryMetadataLeavesDeferredSignatureStatusIncompleteAfterSHAStatsCacheHit(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	page, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	cursor := page["snapshot_cursor"].(string)
	stats, finderErr := service.GitHistoryAtOffset(context.Background(), root, "1", cursor, 0)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	statsCommit := stats["commits"].([]map[string]any)[0]
	if status, ok := statsCommit["signature_status"].(string); !ok || status == "" {
		t.Fatalf("stats signature status = %#v, want complete nonempty status", statsCommit["signature_status"])
	}
	for _, request := range []struct {
		name string
		call func() (map[string]any, *Error)
	}{
		{name: "cold", call: func() (map[string]any, *Error) {
			return service.GitHistoryMetadata(context.Background(), root, "1", cursor, 0)
		}},
		{name: "cached", call: func() (map[string]any, *Error) {
			return service.GitHistoryMetadata(context.Background(), root, "1", cursor, 0)
		}},
	} {
		metadata, finderErr := request.call()
		if finderErr != nil {
			t.Fatalf("%s metadata request: %v", request.name, finderErr)
		}
		commit := metadata["commits"].([]map[string]any)[0]
		if status, ok := commit["signature_status"]; ok {
			t.Fatalf("%s metadata-only signature status = %#v, want omitted so the browser requests enrichment", request.name, status)
		}
		if commit["files"] != 1 || commit["added"] != 1 || commit["removed"] != 0 {
			t.Fatalf("%s metadata did not reuse numeric SHA stats: %#v", request.name, commit)
		}
	}
}

func TestGitHistoryStatsCacheRefreshesSignatureStatusOnJSONPageHit(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	signatureRefreshes := 0
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if strings.Contains(arg, "%G?") {
				signatureRefreshes++
				break
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	firstRefreshes := 0
	for index := 0; index < 2; index++ {
		payload, finderErr := service.GitHistory(context.Background(), root, "1", "", true)
		if finderErr != nil {
			t.Fatalf("stats request %d: %v", index+1, finderErr)
		}
		commit := payload["commits"].([]map[string]any)[0]
		if status, ok := commit["signature_status"].(string); !ok || status == "" {
			t.Fatalf("stats request %d signature status = %#v, want a current Git status", index+1, commit["signature_status"])
		}
		if index == 0 {
			firstRefreshes = signatureRefreshes
		}
	}
	if signatureRefreshes <= firstRefreshes {
		t.Fatalf("signature refresh commands across cold and cached JSON stats pages = %d after first request=%d, want the cached page to refresh signature status", signatureRefreshes, firstRefreshes)
	}
}

func TestGitReachabilityCacheInvalidatesWhenShallowBoundaryChanges(t *testing.T) {
	root, ancestor := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "second.txt"), []byte("two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "--", "second.txt"}, {"-C", root, "commit", "-qm", "second"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	headRaw, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	head := strings.TrimSpace(string(headRaw))
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	if _, finderErr := service.GitCommit(context.Background(), root, ancestor, head); finderErr != nil {
		t.Fatalf("ancestor detail before shallow boundary: %v", finderErr)
	}
	sourceURL := (&url.URL{Scheme: "file", Path: root}).String()
	if output, err := exec.Command("git", "-C", root, "fetch", "--quiet", "--depth=1", sourceURL, "HEAD").CombinedOutput(); err != nil {
		t.Fatalf("shallow fetch: %v (%s)", err, output)
	}
	if _, err := os.Stat(filepath.Join(root, ".git", "shallow")); err != nil {
		t.Fatalf("shallow boundary was not created: %v", err)
	}
	if _, finderErr := service.GitCommit(context.Background(), root, ancestor, head); finderErr == nil || finderErr.Code != "unknown_commit" {
		t.Fatalf("ancestor detail after shallow boundary = %v, want unknown_commit", finderErr)
	}
}

func TestGitHistoryAndCommitCacheRefreshRepositoryPathsAfterMove(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	parent := filepath.Dir(root)
	service, err := New(parent, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	firstHistory, finderErr := service.GitHistory(context.Background(), root, "10", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	firstStats, finderErr := service.GitHistory(context.Background(), root, "10", "", true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if _, finderErr = service.GitCommit(context.Background(), root, head, head); finderErr != nil {
		t.Fatal(finderErr)
	}
	moved := filepath.Join(parent, "moved-repo")
	if err := os.Rename(root, moved); err != nil {
		t.Fatal(err)
	}

	secondHistory, finderErr := service.GitHistory(context.Background(), moved, "10", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if secondHistory["path"] != moved || secondHistory["repo"] != moved {
		t.Fatalf("history after repository move has stale paths: before=%#v after=%#v", firstHistory, secondHistory)
	}
	secondStats, finderErr := service.GitHistory(context.Background(), moved, "10", "", true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if secondStats["path"] != moved || secondStats["repo"] != moved {
		t.Fatalf("stats history after repository move has stale paths: before=%#v after=%#v", firstStats, secondStats)
	}
	secondCommit, finderErr := service.GitCommit(context.Background(), moved, head, head)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if secondCommit["repo"] != moved {
		t.Fatalf("commit detail after repository move has stale repo path: %#v", secondCommit)
	}
}

func TestGitHistoryStatsCacheEvictsOldestSHAAndKeepsRecentEntries(t *testing.T) {
	service, _ := newTestService(t, nil)
	for index := 0; index < maxGitHistoryStatsCacheEntries; index++ {
		service.cacheGitHistoryStats(fmt.Sprintf("sha-%d", index), gitHistoryStatsInfo{files: index})
	}
	if service.gitHistoryStatsCache.Len() != maxGitHistoryStatsCacheEntries {
		t.Fatalf("stats cache size = %d, want %d", service.gitHistoryStatsCache.Len(), maxGitHistoryStatsCacheEntries)
	}
	if _, ok := service.cachedGitHistoryStats("sha-0"); !ok {
		t.Fatal("oldest entry was unexpectedly missing before the cache became full")
	}
	service.cacheGitHistoryStats("sha-1000", gitHistoryStatsInfo{files: 1000})
	if _, ok := service.cachedGitHistoryStats("sha-0"); !ok {
		t.Fatal("recently accessed entry was evicted instead of the oldest unused entry")
	}
	if _, ok := service.cachedGitHistoryStats("sha-1"); ok {
		t.Fatal("oldest unused entry remained after the cache exceeded 1000 entries")
	}
}

func TestGitHistoryAndCommitAllowExternalLinkedWorktree(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	linked := filepath.Join(t.TempDir(), "linked-worktree")
	if output, err := exec.Command("git", "-C", root, "worktree", "add", "--detach", linked, head).CombinedOutput(); err != nil {
		t.Fatalf("git worktree add: %v (%s)", err, output)
	}
	serviceRoot := t.TempDir()
	service, err := New(serviceRoot, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	history, finderErr := service.GitHistory(context.Background(), linked, "1", "")
	if finderErr != nil || history["repo"] != linked || history["head"] != head {
		t.Fatalf("external linked-worktree history = %#v, error = %v", history, finderErr)
	}
	commits, ok := history["commits"].([]map[string]any)
	if !ok || len(commits) != 1 || commits[0]["sha"] != head {
		t.Fatalf("external linked-worktree commits = %#v", history["commits"])
	}
	detail, finderErr := service.GitCommit(context.Background(), linked, head, head, history["repository_identity"].(string))
	if finderErr != nil || detail["sha"] != head || len(detail["files"].([]map[string]any)) != 1 {
		t.Fatalf("external linked-worktree detail = %#v, error = %v", detail, finderErr)
	}
}

func TestGitHistoryEmptyRepositoryExposesShallowBoundary(t *testing.T) {
	root := t.TempDir()
	if output, err := exec.Command("git", "init", "-q", root).CombinedOutput(); err != nil {
		t.Fatalf("git init: %v (%s)", err, output)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	history, finderErr := service.GitHistory(context.Background(), root, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if history["head"] != "" || len(history["commits"].([]map[string]any)) != 0 {
		t.Fatalf("empty repository history = %#v", history)
	}
	if boundary, ok := history["shallow_boundary"].(string); !ok || boundary == "" {
		t.Fatalf("empty repository shallow boundary = %#v", history["shallow_boundary"])
	}
}

func TestGitHistoryNamespacePathWithNewline(t *testing.T) {
	workspace := t.TempDir()
	root := filepath.Join(workspace, "repo\nwith-newline")
	linked := filepath.Join(workspace, "linked")
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("one\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"init", "-q", root}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "config", "user.name", "Finder Test"}, {"-C", root, "add", "--", "note.txt"}, {"-C", root, "commit", "-qm", "initial"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	head, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "worktree", "add", "--detach", linked, strings.TrimSpace(string(head))).CombinedOutput(); err != nil {
		t.Fatalf("git worktree add: %v (%s)", err, output)
	}
	pathDiscoveryCalls := 0
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		hasGitPath := false
		for index, arg := range args {
			if arg == "--git-path" && index+1 < len(args) && args[index+1] == "objects" {
				hasGitPath = true
			}
		}
		if hasGitPath {
			pathDiscoveryCalls++
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	service, err := New(workspace, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	history, finderErr := service.GitHistory(context.Background(), linked, "1", "")
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	commits, ok := history["commits"].([]map[string]any)
	if !ok || len(commits) != 1 || commits[0]["subject"] != "initial" {
		t.Fatalf("history for repository path with newline = %#v", history["commits"])
	}
	wantPathDiscoveryCalls := 2
	if runtime.GOOS == "darwin" {
		wantPathDiscoveryCalls = 0
	}
	if pathDiscoveryCalls != wantPathDiscoveryCalls {
		t.Fatalf("object path discovery commands = %d, want %d", pathDiscoveryCalls, wantPathDiscoveryCalls)
	}
}

func TestGitViewsHandleSparseCheckoutWorkingMissingFile(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	visible := filepath.Join(root, "visible.txt")
	omitted := filepath.Join(root, "omitted.txt")
	if err := os.WriteFile(visible, []byte("visible\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(omitted, []byte("omitted\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"-C", root, "add", "--", "visible.txt", "omitted.txt"},
		{"-C", root, "commit", "-qm", "sparse baseline"},
		{"-C", root, "sparse-checkout", "init", "--no-cone"},
		{"-C", root, "sparse-checkout", "set", "/visible.txt"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if _, err := os.Stat(omitted); !os.IsNotExist(err) {
		t.Fatalf("sparse checkout retained omitted file: err=%v", err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	diff, finderErr := service.Diff(context.Background(), omitted, "HEAD", "current")
	if finderErr != nil || diff["repo"] != root || diff["relative_path"] != "omitted.txt" || diff["untracked"] != false || diff["working_missing"] != true || diff["original"] != "omitted\n" || diff["working"] != "" || !strings.Contains(diff["diff"].(string), "-omitted") {
		t.Fatalf("sparse checkout diff = %#v, error = %v", diff, finderErr)
	}
}

func TestGitViewsHandleConflictedWorkingFile(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	path := filepath.Join(root, "conflict.txt")
	if err := os.WriteFile(path, []byte("base\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"-C", root, "add", "conflict.txt"},
		{"-C", root, "commit", "-qm", "base"},
		{"-C", root, "checkout", "-q", "-b", "other"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(path, []byte("other\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"-C", root, "add", "conflict.txt"},
		{"-C", root, "commit", "-qm", "other"},
		{"-C", root, "checkout", "-q", "-"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(path, []byte("main\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"-C", root, "add", "conflict.txt"},
		{"-C", root, "commit", "-qm", "main"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	merge := exec.Command("git", "-C", root, "merge", "other")
	if output, err := merge.CombinedOutput(); err == nil {
		t.Fatalf("conflicting merge unexpectedly succeeded: %s", output)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	diff, finderErr := service.Diff(context.Background(), path, "HEAD", "current")
	if finderErr != nil || diff["repo"] != root || diff["relative_path"] != "conflict.txt" || diff["untracked"] != false || diff["working_missing"] != false || !strings.Contains(diff["working"].(string), "<<<<<<<") || !strings.Contains(diff["diff"].(string), "<<<<<<<") {
		t.Fatalf("conflicted diff = %#v, error = %v", diff, finderErr)
	}
}

func TestGitViewsDiscoverNestedSubmoduleRepository(t *testing.T) {
	child := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", child},
		{"-C", child, "config", "user.email", "test@example.invalid"},
		{"-C", child, "config", "user.name", "Finder Test"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	childFile := filepath.Join(child, "nested.txt")
	if err := os.WriteFile(childFile, []byte("child\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", child, "add", "nested.txt"}, {"-C", child, "commit", "-qm", "child"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	outer := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", outer},
		{"-C", outer, "config", "user.email", "test@example.invalid"},
		{"-C", outer, "config", "user.name", "Finder Test"},
		{"-C", outer, "-c", "protocol.file.allow=always", "submodule", "add", "-q", child, "modules/child"},
		{"-C", outer, "add", "."},
		{"-C", outer, "commit", "-qm", "submodule"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	service, err := New(outer, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	childRoot := filepath.Join(outer, "modules/child")
	info, finderErr := service.Info(context.Background(), childRoot, true)
	// Python path_info decorates directories with repository identity but does not classify the
	// directory itself as a tracked file. The submodule's directory-level Git history remains
	// available through the dedicated history API below.
	if finderErr != nil || info["git_root"] != childRoot || info["git_tracked"] != false || info["git_has_history"] != false {
		t.Fatalf("nested submodule info = %#v, error = %v", info, finderErr)
	}
	history, finderErr := service.GitHistory(context.Background(), childRoot, "10", "")
	if finderErr != nil || history["repo"] != childRoot || len(history["commits"].([]map[string]any)) != 1 {
		t.Fatalf("nested submodule history = %#v, error = %v", history, finderErr)
	}
}

func TestGitHistoryExposesOnlySafeSupportedHostedRemote(t *testing.T) {
	tests := []struct {
		name string
		url  string
		want map[string]string
	}{
		{name: "github scp", url: "git@github.com:owner/project.git", want: map[string]string{"provider": "github", "base_url": "https://github.com/owner/project"}},
		{name: "gitlab ssh", url: "ssh://git@gitlab.example.com/group/subgroup/project.git", want: map[string]string{"provider": "gitlab", "base_url": "https://gitlab.example.com/group/subgroup/project"}},
		{name: "gitlab ssh port", url: "ssh://git@gitlab.example.com:2222/group/project.git", want: map[string]string{"provider": "gitlab", "base_url": "https://gitlab.example.com/group/project"}},
		{name: "unsupported host", url: "https://example.com/owner/project.git"},
		{name: "https credentials", url: "https://token@github.com/owner/project.git"},
		{name: "query credentials", url: "https://github.com/owner/project.git?token=secret"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root, _ := newCommittedTestRepo(t)
			if output, err := exec.Command("git", "-C", root, "remote", "add", "origin", test.url).CombinedOutput(); err != nil {
				t.Fatalf("git remote add: %v (%s)", err, output)
			}
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()
			history, finderErr := service.GitHistory(context.Background(), root, "1", "")
			if finderErr != nil {
				t.Fatal(finderErr)
			}
			if test.want == nil {
				got, typed := history["hosted_remote"].(map[string]string)
				if (typed && got != nil) || (!typed && history["hosted_remote"] != nil) {
					t.Fatalf("unsafe hosted remote was exposed: %#v", history["hosted_remote"])
				}
				return
			}
			got, ok := history["hosted_remote"].(map[string]string)
			if !ok || len(got) != len(test.want) {
				t.Fatalf("hosted remote = %#v, want %#v", history["hosted_remote"], test.want)
			}
			for key, want := range test.want {
				if got[key] != want {
					t.Fatalf("hosted remote = %#v, want %#v", got, test.want)
				}
			}
		})
	}
}

func TestGitCommitRowsAreCappedWithoutOffByOne(t *testing.T) {
	statusRows := make([]map[string]string, gitCommitMaxFiles+1)
	for index := range statusRows {
		statusRows[index] = map[string]string{"status": "M", "path": fmt.Sprintf("file-%d.txt", index)}
	}
	capped, truncated := capGitCommitRows(statusRows)
	if len(capped) != gitCommitMaxFiles || !truncated || capped[len(capped)-1]["path"] != fmt.Sprintf("file-%d.txt", gitCommitMaxFiles-1) {
		t.Fatalf("capped Git commit rows = len %d truncated %t last %#v", len(capped), truncated, capped[len(capped)-1])
	}
}

func TestGitCommitPreservesMessageTruncationContract(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("changed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	messagePath := filepath.Join(root, "commit-message.txt")
	message := strings.Repeat("message ", gitCommitMaxMessage/len("message ")+1024)
	if err := os.WriteFile(messagePath, []byte(message), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "note.txt").CombinedOutput(); err != nil {
		t.Fatalf("stage large message change: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-F", messagePath).CombinedOutput(); err != nil {
		t.Fatalf("large message commit: %v (%s)", err, output)
	}
	headBytes, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	head := strings.TrimSpace(string(headBytes))
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	detail, finderErr := service.GitCommit(context.Background(), root, head, head)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if detail["message_truncated"] != true || detail["truncated"] != true {
		t.Fatalf("large message truncation = %#v", detail)
	}
	if len(detail["message"].(string)) > gitCommitMaxMessage {
		t.Fatalf("large message length = %d, limit %d", len(detail["message"].(string)), gitCommitMaxMessage)
	}
}

func TestGitCommitParsersRejectMalformedPythonContractInputs(t *testing.T) {
	nameStatusCases := [][]byte{
		[]byte("R100\x00old\x00"),
		[]byte("M\x00path"),
		[]byte("M1000\x00path\x00"),
		[]byte("M\x00path\x00M\x00path\x00"),
	}
	for _, raw := range nameStatusCases {
		if _, _, err := parseGitNameStatus(raw, false); err == nil {
			t.Fatalf("name-status parser accepted malformed input %q", raw)
		}
	}

	numstatCases := [][]byte{
		[]byte("1\t-\tpath\x00"),
		[]byte("1\t2\tpath"),
		[]byte("1\t2\tpath\x001\t2\tpath\x00"),
	}
	for _, raw := range numstatCases {
		if _, _, err := parseGitNumstat(raw, false); err == nil {
			t.Fatalf("numstat parser accepted malformed input %q", raw)
		}
	}
}

func TestGitCommitParsersRetainCompleteRowsBeforeBoundedTail(t *testing.T) {
	statusRows, statusTruncated, err := parseGitNameStatus([]byte("M\x00complete.txt\x00M\x00partial"), true)
	if err != nil || !statusTruncated || len(statusRows) != 1 || statusRows[0]["path"] != "complete.txt" {
		t.Fatalf("truncated name-status = %#v, truncated=%t, error=%v", statusRows, statusTruncated, err)
	}
	counts, countsTruncated, err := parseGitNumstat([]byte("1\t2\tcomplete.txt\x001\t2\tpartial"), true)
	if err != nil || !countsTruncated || len(counts) != 1 || counts["complete.txt\x00complete.txt"].added != 1 || counts["complete.txt\x00complete.txt"].removed != 2 {
		t.Fatalf("truncated numstat = %#v, truncated=%t, error=%v", counts, countsTruncated, err)
	}
}

func TestGitRawNumstatParserCombinesStatusAndCounts(t *testing.T) {
	raw := []byte(":100644 100644 " + strings.Repeat("a", 40) + " " + strings.Repeat("b", 40) + " R100\x00old.txt\x00new.txt\x00-\t-\t\x00old.txt\x00new.txt\x00")
	rows, counts, truncated, err := parseGitRawNumstat(raw, false)
	if err != nil || truncated || len(rows) != 1 || len(counts) != 1 {
		t.Fatalf("combined Git diff parse = rows %#v counts %#v truncated %t error %v", rows, counts, truncated, err)
	}
	if rows[0]["status"] != "R" || rows[0]["old_path"] != "old.txt" || rows[0]["path"] != "new.txt" {
		t.Fatalf("combined Git status row = %#v", rows[0])
	}
	count, ok := counts["old.txt\x00new.txt"]
	if !ok || !count.binary {
		t.Fatalf("combined Git count = %#v", counts)
	}
}

func gitCommitProductFixture(commit string, diff []byte) []byte {
	metadata := []byte(fmt.Sprintf("%s\x00\x00Finder Test\x001\x00initial\x00initial\n\x00\x00\n", commit))
	return append(metadata, diff...)
}

func TestGitCommitReturnsTooLargeWhenTruncatedStatusHasNoCompleteRows(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	diffPath := filepath.Join(root, "diff-output")
	product := gitCommitProductFixture(head, []byte(":"+strings.Repeat("x", gitCommitProductMaxOutput)))
	if err := os.WriteFile(diffPath, product, 0o600); err != nil {
		t.Fatal(err)
	}
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		commandPath := ""
		for _, arg := range args {
			if strings.Contains(arg, "--raw") {
				commandPath = diffPath
				break
			}
		}
		if commandPath != "" {
			command := exec.CommandContext(ctx, "cat", commandPath)
			command.ExtraFiles = extraFiles
			command.Env = gitCommandEnvironment()
			return command
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	_, finderErr := service.GitCommit(context.Background(), root, head, head)
	if finderErr == nil || finderErr.Status != http.StatusRequestEntityTooLarge || finderErr.MessageKey != "fs.error.gitCommitTooLarge" {
		t.Fatalf("truncated status without a complete row = %#v", finderErr)
	}
}

func TestGitCommitTrimsRowsToPayloadLimit(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	diffPath := filepath.Join(root, "diff-output")
	status := make([]byte, 0, gitCommitMaxFiles*420)
	numstat := make([]byte, 0, gitCommitMaxFiles*420)
	for index := 0; index < gitCommitMaxFiles; index++ {
		path := fmt.Sprintf("scope/%s-%04d.txt", strings.Repeat("x", 360), index)
		status = append(status, []byte(fmt.Sprintf(":100644 100644 %040x %040x M\x00%s\x00", index+1, index+2, path))...)
		numstat = append(numstat, []byte("1\t2\t"+path+"\x00")...)
	}
	combined := gitCommitProductFixture(head, append(status, numstat...))
	if err := os.WriteFile(diffPath, combined, 0o600); err != nil {
		t.Fatal(err)
	}
	original := execCommandContext
	t.Cleanup(func() { execCommandContext = original })
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		commandPath := ""
		for _, arg := range args {
			if strings.Contains(arg, "--raw") {
				commandPath = diffPath
				break
			}
		}
		if commandPath != "" {
			command := exec.CommandContext(ctx, "cat", commandPath)
			command.ExtraFiles = extraFiles
			command.Env = gitCommandEnvironment()
			return command
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	detail, finderErr := service.GitCommit(context.Background(), root, head, head)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	files, ok := detail["files"].([]map[string]any)
	if !ok || len(files) == 0 || len(files) >= gitCommitMaxFiles || detail["files_truncated"] != true || detail["truncated"] != true {
		t.Fatalf("payload-trimmed commit = %#v", detail)
	}
	if len(files[0]["path"].(string)) != len(files[len(files)-1]["path"].(string)) {
		t.Fatalf("payload-trimmed rows changed shape = first=%#v last=%#v", files[0], files[len(files)-1])
	}
}

func TestBrokenSymlinkInfoAndRawRange(t *testing.T) {
	service, root := newTestService(t, nil)
	broken := filepath.Join(root, "broken.txt")
	if err := os.Symlink(filepath.Join(root, "missing-target"), broken); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	info, finderErr := service.Info(context.Background(), broken)
	if finderErr != nil || info["kind"] != "symlink-broken" {
		t.Fatalf("broken symlink info = %#v, error = %v", info, finderErr)
	}
	data, contentType, filename, size, finderErr := service.RawRange(context.Background(), filepath.Join(root, "note.md"), 0, 1, 3)
	if finderErr != nil || string(data) != "ell" || contentType == "" || filename != "note.md" || size != 6 {
		t.Fatalf("raw range = %q %q %q %d, error = %v", data, contentType, filename, size, finderErr)
	}
}

func TestFinderMutationsConflictAndCandidateResolution(t *testing.T) {
	service, root := newTestService(t, nil)
	created, finderErr := service.Write(context.Background(), filepath.Join(root, "created.txt"), "created", nil)
	if finderErr != nil || created["size"] != int64(7) {
		t.Fatalf("write = %#v, error = %v", created, finderErr)
	}
	read, finderErr := service.Read(context.Background(), filepath.Join(root, "created.txt"))
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	beforeInfo, err := os.Stat(filepath.Join(root, "created.txt"))
	if err != nil {
		t.Fatal(err)
	}
	expectedMtime := read["mtime_ns"].(int64)
	updated, finderErr := service.Write(context.Background(), filepath.Join(root, "created.txt"), "updated", &expectedMtime)
	if finderErr != nil || updated["size"] != int64(7) {
		t.Fatalf("atomic replacement = %#v, error = %v", updated, finderErr)
	}
	afterInfo, err := os.Stat(filepath.Join(root, "created.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if identityFor(beforeInfo).ino == identityFor(afterInfo).ino {
		t.Fatalf("write reused the old inode: before=%#v after=%#v", identityFor(beforeInfo), identityFor(afterInfo))
	}
	// Keep the deliberately stale value outside Python's 10 ms precision tolerance. A replacement
	// write can legitimately land 20 ms after the original read on a loaded test host.
	wrongMtime := int64(read["mtime_ns"].(int64) + 100_000_000)
	if _, finderErr = service.Write(context.Background(), filepath.Join(root, "created.txt"), "bad", &wrongMtime); finderErr == nil || finderErr.Status != 409 {
		t.Fatalf("write conflict = %#v", finderErr)
	}
	moved, finderErr := service.Rename(context.Background(), filepath.Join(root, "created.txt"), "moved.txt")
	if finderErr != nil || moved["path"] != filepath.Join(root, "moved.txt") {
		t.Fatalf("rename = %#v, error = %v", moved, finderErr)
	}
	candidates, finderErr := service.ResolveCandidates(context.Background(), []string{filepath.Join(root, "missing.txt"), filepath.Join(root, "moved.txt")})
	if finderErr != nil || candidates["path"] != filepath.Join(root, "moved.txt") {
		t.Fatalf("candidates = %#v, error = %v", candidates, finderErr)
	}
	if _, finderErr = service.ResolveCandidates(context.Background(), []string{filepath.Join(root, "moved.txt"), filepath.Join(root, "moved.txt")}); finderErr == nil || finderErr.Status != http.StatusBadRequest {
		t.Fatalf("duplicate candidates = %#v", finderErr)
	}
	if _, finderErr = service.Delete(context.Background(), filepath.Join(root, "moved.txt")); finderErr != nil {
		t.Fatal(finderErr)
	}
	subtree := filepath.Join(root, "subtree")
	if err := os.Mkdir(subtree, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(subtree, "nested"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(subtree, "nested", "child.txt"), []byte("child"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, finderErr = service.Delete(context.Background(), subtree, true); finderErr != nil {
		t.Fatalf("recursive delete = %v", finderErr)
	}
	if _, err := os.Stat(subtree); !os.IsNotExist(err) {
		t.Fatalf("recursive delete left subtree: %v", err)
	}
}

func TestRenameNoReplacePublishesRegularFilesWhenRenameat2IsUnsupported(t *testing.T) {
	root := t.TempDir()
	directory, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	if err := os.WriteFile(filepath.Join(root, "source.txt"), []byte("source"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := publishRegularNoReplace(directory, "source.txt", "target.txt"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "source.txt")); !os.IsNotExist(err) {
		t.Fatalf("source remains after publication: %v", err)
	}
	data, err := os.ReadFile(filepath.Join(root, "target.txt"))
	if err != nil || string(data) != "source" {
		t.Fatalf("published target = %q, error = %v", data, err)
	}
}

func TestRenameNoReplaceDoesNotOverwriteExistingTarget(t *testing.T) {
	root := t.TempDir()
	directory, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	for name, content := range map[string]string{"source.txt": "source", "target.txt": "target"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := publishRegularNoReplace(directory, "source.txt", "target.txt"); !errors.Is(err, syscall.EEXIST) {
		t.Fatalf("existing target error = %v, want EEXIST", err)
	}
	data, err := os.ReadFile(filepath.Join(root, "target.txt"))
	if err != nil || string(data) != "target" {
		t.Fatalf("existing target = %q, error = %v", data, err)
	}
}

func TestRenameAndDeleteDoNotRequireReadingTheTarget(t *testing.T) {
	service, root := newTestService(t, nil)
	path := filepath.Join(root, "unreadable.txt")
	if err := os.WriteFile(path, []byte("unreadable"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0); err != nil {
		t.Fatal(err)
	}
	if _, finderErr := service.Rename(context.Background(), path, "renamed.txt"); finderErr != nil {
		t.Fatalf("rename unreadable target: %#v", finderErr)
	}
	renamed := filepath.Join(root, "renamed.txt")
	if _, finderErr := service.Delete(context.Background(), renamed); finderErr != nil {
		t.Fatalf("delete unreadable target: %#v", finderErr)
	}
	if _, err := os.Lstat(renamed); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("renamed target after delete = %v, want not-exist", err)
	}
}

func TestRecursiveDeleteReportsPartialProgressAfterRestoringQuarantine(t *testing.T) {
	service, root := newTestService(t, nil)
	subtree := filepath.Join(root, "partial")
	if err := os.Mkdir(subtree, 0o700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"a-first.txt", "a-second.txt"} {
		if err := os.WriteFile(filepath.Join(subtree, name), []byte(name), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	large := filepath.Join(subtree, "z-large")
	if err := os.Mkdir(large, 0o700); err != nil {
		t.Fatal(err)
	}
	for index := 0; index <= MaxDirectoryEntries; index++ {
		name := filepath.Join(large, fmt.Sprintf("entry-%04d", index))
		if err := os.WriteFile(name, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	_, finderErr := service.Delete(context.Background(), subtree, true)
	if finderErr == nil || finderErr.DeleteReason != "limit_exceeded" || !finderErr.Partial {
		t.Fatalf("partial delete error = %#v", finderErr)
	}
	if len(finderErr.DeletedPaths) != 2 || finderErr.DeletedPaths[0] != filepath.Join(subtree, "a-first.txt") || finderErr.DeletedPaths[1] != filepath.Join(subtree, "a-second.txt") {
		t.Fatalf("partial delete paths = %#v", finderErr.DeletedPaths)
	}
	if finderErr.FailedPath != large || finderErr.Payload()["deleted_paths"] == nil {
		t.Fatalf("partial delete payload = %#v", finderErr.Payload())
	}
	if _, err := os.Stat(subtree); err != nil {
		t.Fatalf("restored partial subtree missing: %v", err)
	}
	if _, err := os.Stat(filepath.Join(subtree, "a-first.txt")); !os.IsNotExist(err) {
		t.Fatalf("first deleted entry still exists: %v", err)
	}
	if _, err := os.Stat(filepath.Join(subtree, "a-second.txt")); !os.IsNotExist(err) {
		t.Fatalf("second deleted entry still exists: %v", err)
	}
	if _, err := os.Stat(large); err != nil {
		t.Fatalf("unvisited large directory missing: %v", err)
	}
}

func TestFinderTrackedRenamePublishesGitIndexMove(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	ambient := t.TempDir()
	if output, err := exec.Command("git", "init", "-q", ambient).CombinedOutput(); err != nil {
		t.Fatalf("ambient Git init: %v (%s)", err, output)
	}
	t.Setenv("GIT_DIR", filepath.Join(ambient, ".git"))
	t.Setenv("GIT_INDEX_FILE", filepath.Join(ambient, ".git", "index"))
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("working-only\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	moved, finderErr := service.Rename(context.Background(), filepath.Join(root, "note.txt"), "renamed.txt")
	if finderErr != nil || moved["path"] != filepath.Join(root, "renamed.txt") {
		t.Fatalf("tracked rename = %#v, error = %v", moved, finderErr)
	}
	cleanEnvironment := gitCommandEnvironment()
	oldPath := exec.Command("git", "-C", root, "ls-files", "--error-unmatch", "--", "note.txt")
	oldPath.Env = cleanEnvironment
	if err := oldPath.Run(); err == nil {
		t.Fatal("tracked rename left the old path in Git's index")
	}
	newPath := exec.Command("git", "-C", root, "ls-files", "--error-unmatch", "--", "renamed.txt")
	newPath.Env = cleanEnvironment
	if output, err := newPath.CombinedOutput(); err != nil {
		t.Fatalf("tracked rename did not publish the new path: %v (%s)", err, output)
	}
	show := exec.Command("git", "-C", root, "show", ":renamed.txt")
	show.Env = cleanEnvironment
	if output, err := show.Output(); err != nil || string(output) != "one\n" {
		t.Fatalf("tracked rename staged working-tree content: %v (%q)", err, output)
	}
}

func TestFinderTrackedRenameRefusesExistingGitIndexLock(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	lock := filepath.Join(root, ".git", "index.lock")
	if err := os.WriteFile(lock, []byte("owned by another operation"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	_, finderErr := service.Rename(context.Background(), filepath.Join(root, "note.txt"), "renamed.txt")
	if finderErr == nil || finderErr.Status != http.StatusConflict || finderErr.MessageKey != "fs.error.gitRepositoryChanged" {
		t.Fatalf("existing index lock error = %#v", finderErr)
	}
	if _, err := os.Stat(filepath.Join(root, "note.txt")); err != nil {
		t.Fatalf("source changed despite existing index lock: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "renamed.txt")); !os.IsNotExist(err) {
		t.Fatalf("target exists despite existing index lock: %v", err)
	}
}

func TestFinderTrackedRenameRestoresFilesystemWhenGitPublicationFails(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	service.publishGitRename = func(ctx context.Context, plan *gitRenamePlan, path string) error {
		if err := plan.publish(ctx, path); err != nil {
			return err
		}
		return errors.New("forced post-publication failure")
	}

	_, finderErr := service.Rename(context.Background(), filepath.Join(root, "note.txt"), "renamed.txt")
	if finderErr == nil || finderErr.Status != http.StatusInternalServerError {
		t.Fatalf("publication failure = %#v, want typed filesystem failure", finderErr)
	}
	if _, err := os.Stat(filepath.Join(root, "note.txt")); err != nil {
		t.Fatalf("source was not restored after publication failure: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "renamed.txt")); !os.IsNotExist(err) {
		t.Fatalf("target exists after publication failure: %v", err)
	}
	oldPath := exec.Command("git", "-C", root, "ls-files", "--error-unmatch", "--", "note.txt")
	if err := oldPath.Run(); err != nil {
		t.Fatalf("Git index rollback lost the source path: %v", err)
	}
	newPath := exec.Command("git", "-C", root, "ls-files", "--error-unmatch", "--", "renamed.txt")
	if err := newPath.Run(); err == nil {
		t.Fatal("Git index rollback left the target path")
	}
}

func TestFinderTrackedRenameRollsBackGitIndexAfterRequestCancellation(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	service.publishGitRename = func(ctx context.Context, plan *gitRenamePlan, path string) error {
		if err := plan.publish(ctx, path); err != nil {
			return err
		}
		cancel()
		return errors.New("forced post-publication failure")
	}

	_, finderErr := service.Rename(ctx, filepath.Join(root, "note.txt"), "renamed.txt")
	if finderErr == nil || finderErr.Status != http.StatusInternalServerError {
		t.Fatalf("cancelled publication failure = %#v", finderErr)
	}
	oldPath := exec.Command("git", "-C", root, "ls-files", "--error-unmatch", "--", "note.txt")
	oldPath.Env = gitCommandEnvironment()
	if err := oldPath.Run(); err != nil {
		t.Fatalf("Git index rollback lost the source path: %v", err)
	}
	newPath := exec.Command("git", "-C", root, "ls-files", "--error-unmatch", "--", "renamed.txt")
	newPath.Env = gitCommandEnvironment()
	if err := newPath.Run(); err == nil {
		t.Fatal("Git index rollback left the target path")
	}
}

func TestFinderWriteRejectsInPlaceEditAfterInitialConflictCheck(t *testing.T) {
	service, root := newTestService(t, nil)
	path := filepath.Join(root, "note.md")
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	content := strings.Repeat("replacement\n", 100000)
	targetChanged := make(chan struct{})
	service.beforeWriteConflictRecheck = func() {
		if writeErr := os.WriteFile(path, []byte("external edit\n"), 0o600); writeErr != nil {
			t.Errorf("external edit: %v", writeErr)
			return
		}
		_ = os.Chtimes(path, time.Now().Add(time.Second), time.Now().Add(time.Second))
		close(targetChanged)
	}
	expectedMtime := before.ModTime().UnixNano()
	_, finderErr := service.Write(context.Background(), path, content, &expectedMtime)
	select {
	case <-targetChanged:
	default:
		t.Fatal("test did not observe the temporary write before completion")
	}
	if finderErr == nil || finderErr.Status != http.StatusConflict {
		t.Fatalf("in-place edit write result = %#v", finderErr)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "external edit\n" {
		t.Fatalf("in-place edit was overwritten: %q", got)
	}
}

func TestFinderTrackedRenamePreservesGitIndexFlags(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	for _, name := range []string{"assume.txt", "skip.txt"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte(name+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	for _, args := range [][]string{{"-C", root, "add", "assume.txt", "skip.txt"}, {"-C", root, "commit", "-qm", "flags"}, {"-C", root, "update-index", "--assume-unchanged", "assume.txt"}, {"-C", root, "update-index", "--skip-worktree", "skip.txt"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	for _, rename := range []struct{ old, new string }{{"assume.txt", "assume-renamed.txt"}, {"skip.txt", "skip-renamed.txt"}} {
		if _, finderErr := service.Rename(context.Background(), filepath.Join(root, rename.old), rename.new); finderErr != nil {
			t.Fatalf("flagged tracked rename = %#v", finderErr)
		}
	}
	assumeStatus, err := exec.Command("git", "-C", root, "ls-files", "-v", "--", "assume-renamed.txt").Output()
	if err != nil {
		t.Fatalf("git assume-unchanged status: %v", err)
	}
	skipStatus, err := exec.Command("git", "-C", root, "ls-files", "-t", "--", "skip-renamed.txt").Output()
	if err != nil {
		t.Fatalf("git skip-worktree status: %v", err)
	}
	if !strings.HasPrefix(string(assumeStatus), "h ") || !strings.HasPrefix(string(skipStatus), "S ") {
		t.Fatalf("tracked rename lost index flags: assume=%q skip=%q", assumeStatus, skipStatus)
	}
}

func TestUploadUsesEditorDirectoryAndSanitizesNames(t *testing.T) {
	service, root := newTestService(t, nil)
	missingEditorPath := filepath.Join(root, "new-note.md")
	missingPayload, finderErr := service.Upload(context.Background(), "session", "user", missingEditorPath, "", []UploadFile{{Name: "asset.txt", Data: []byte("image")}})
	if finderErr != nil || missingPayload["target_dir"] != filepath.Join(root, "new-note") {
		t.Fatalf("missing editor upload = %#v, error = %v", missingPayload, finderErr)
	}
	if _, err := os.Stat(filepath.Join(root, "new-note")); err != nil {
		t.Fatalf("missing editor upload did not create sibling directory: %v", err)
	}
	editorPath := filepath.Join(root, "note.md")
	payload, finderErr := service.Upload(context.Background(), "session", "user", editorPath, "", []UploadFile{{Name: "../paste image.png", Data: []byte("image")}})
	if finderErr != nil {
		t.Fatalf("upload = %#v, error = %v", payload, finderErr)
	}
	if payload["target_dir"] != filepath.Join(root, "note") || payload["target_source"] != "editor_file_directory" {
		t.Fatalf("upload target = %#v", payload)
	}
	files := payload["files"].([]any)
	saved := files[0].(map[string]any)
	savedName, _ := saved["saved_name"].(string)
	if len(savedName) != len("20260912-001-paste image.png") || saved["relative_path"] != "note/"+savedName || saved["size"] != 5 {
		t.Fatalf("upload file = %#v", saved)
	}
	data, err := os.ReadFile(filepath.Join(root, "note", savedName))
	if err != nil || string(data) != "image" {
		t.Fatalf("uploaded bytes = %q, error = %v", data, err)
	}
}

func TestUploadUsesAuthorizedBaseDirectoryAndRejectsPythonFileLimitBeforeWriting(t *testing.T) {
	service, root := newTestService(t, nil)
	baseDir := filepath.Join(root, "docs")
	if err := os.Mkdir(baseDir, 0o700); err != nil {
		t.Fatal(err)
	}
	payload, finderErr := service.Upload(context.Background(), "session", "user", "", baseDir, []UploadFile{{Name: "paste.txt", Data: []byte("image")}})
	if finderErr != nil {
		t.Fatalf("base directory upload = %#v, error = %v", payload, finderErr)
	}
	if payload["target_dir"] != baseDir || payload["target_source"] != "editor_base_directory" || payload["base_dir"] != baseDir {
		t.Fatalf("base directory payload = %#v", payload)
	}
	baseFiles := payload["files"].([]any)
	baseSaved := baseFiles[0].(map[string]any)
	baseSavedName, _ := baseSaved["saved_name"].(string)
	if _, err := os.Stat(filepath.Join(baseDir, baseSavedName)); err != nil {
		t.Fatal(err)
	}

	files := make([]UploadFile, MaxUploadFiles+1)
	for index := range files {
		files[index] = UploadFile{Name: filepath.Join("nested", string(rune('a'+index))) + ".txt", Data: []byte("x")}
	}
	if _, finderErr := service.Upload(context.Background(), "session", "user", "", baseDir, files); finderErr == nil || finderErr.Status != 413 || finderErr.Code != "too_many_files" {
		t.Fatalf("too many upload files = %#v, want 413 too_many_files", finderErr)
	}
}

func TestUploadRejectsAggregateSizeBeforeCreatingTarget(t *testing.T) {
	service, root := newTestService(t, nil)
	baseDir := filepath.Join(root, "uploads")
	if err := os.Mkdir(baseDir, 0o700); err != nil {
		t.Fatal(err)
	}
	files := []UploadFile{
		{Name: "one.bin", Data: make([]byte, MaxUploadBytes/2)},
		{Name: "two.bin", Data: make([]byte, MaxUploadBytes/2+1)},
	}
	if _, finderErr := service.Upload(context.Background(), "session", "user", "", baseDir, files); finderErr == nil || finderErr.Status != 413 || finderErr.Code != "too_large" {
		t.Fatalf("aggregate upload = %#v, want 413 too_large", finderErr)
	}
	if _, err := os.Stat(filepath.Join(baseDir, "one.bin")); !os.IsNotExist(err) {
		t.Fatalf("aggregate rejection wrote a file: %v", err)
	}
}

func TestUploadWriteStopsWhenContextIsCancelled(t *testing.T) {
	directoryPath := t.TempDir()
	directory, err := os.Open(directoryPath)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := saveUploadAt(ctx, directory, "cancelled.bin", []byte("data")); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled upload error = %v, want context.Canceled", err)
	}
	entries, err := os.ReadDir(directoryPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("cancelled upload left files: %#v", entries)
	}
}

func TestMutationQuarantineRejectsAReplacedTarget(t *testing.T) {
	service, root := newTestService(t, nil)
	target := filepath.Join(root, "target.txt")
	if err := os.WriteFile(target, []byte("original"), 0o600); err != nil {
		t.Fatal(err)
	}
	parent, finderErr := service.openMutationParent(target)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer parent.Close()
	replacement := filepath.Join(root, "replacement.txt")
	if err := os.WriteFile(replacement, []byte("replacement"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacement, target); err != nil {
		t.Fatal(err)
	}
	if _, finderErr := service.quarantineMutationTarget(parent); finderErr == nil || finderErr.Status != http.StatusConflict {
		t.Fatalf("replaced target error = %#v, want conflict", finderErr)
	}
	data, err := os.ReadFile(target)
	if err != nil || string(data) != "replacement" {
		t.Fatalf("replacement target after rejected mutation = %q, error = %v", data, err)
	}
}

func TestRecursiveDeleteQuarantineDoesNotDeleteAReplacementEntry(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "target.txt")
	if err := os.WriteFile(target, []byte("original"), 0o600); err != nil {
		t.Fatal(err)
	}
	originalInfo, err := os.Lstat(target)
	if err != nil {
		t.Fatal(err)
	}
	originalBackup := filepath.Join(root, "original.txt")
	if err := os.Rename(target, originalBackup); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte("replacement"), 0o600); err != nil {
		t.Fatal(err)
	}
	parent, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer parent.Close()
	_, quarantineErr := quarantineDeleteEntry(parent, "target.txt", identityFor(originalInfo))
	if !errors.Is(quarantineErr, errChangedOnDisk) {
		t.Fatalf("replacement quarantine error = %v", quarantineErr)
	}
	data, err := os.ReadFile(target)
	if err != nil || string(data) != "replacement" {
		t.Fatalf("replacement after rejected recursive delete = %q, error = %v", data, err)
	}
}

func TestMutationsRejectAConfiguredRootReachedThroughASymlinkAlias(t *testing.T) {
	parent := t.TempDir()
	root := filepath.Join(parent, "root")
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(parent, "root-alias")
	if err := os.Symlink(root, alias); err != nil {
		t.Fatal(err)
	}
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	native := &fakeNativeWatcher{events: make(chan nativeWatchEvent), status: nativeWatchStatus{available: true}}
	service := newService(resolvedRoot, nil, native, native.status, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
	t.Cleanup(service.Close)
	if _, finderErr := service.Write(context.Background(), alias, "must not replace root", nil); finderErr == nil || finderErr.Code != "forbidden" || finderErr.MessageKey != "fs.error.rootMutationBlocked" {
		t.Fatalf("root alias mutation error = %#v", finderErr)
	}
}

func TestFinderSearchAndWatchDiffAreBounded(t *testing.T) {
	service, root := newTestService(t, nil)
	if err := os.WriteFile(filepath.Join(root, "another.md"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	search, finderErr := service.Search(context.Background(), root, "ano", 10, true, false, false)
	if finderErr != nil || search["count"] != 1 {
		t.Fatalf("search = %#v, error = %v", search, finderErr)
	}
	baseline, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "watch", "roots": []any{root}, "files": []any{filepath.Join(root, "note.md")}})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	baselineToken := baseline["watch_token"].(string)
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("updated\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service.pollWatches()
	diff, finderErr := service.WatchDiff(context.Background(), baselineToken, false)
	if finderErr != nil || diff["mode"] != "delta" || diff["token"] == baselineToken {
		t.Fatalf("watch diff = %#v, error = %v", diff, finderErr)
	}
	full, finderErr := service.WatchDiff(context.Background(), "", true)
	if finderErr != nil || full["mode"] != "full" {
		t.Fatalf("full watch diff = %#v, error = %v", full, finderErr)
	}
}

func TestWatchRootReplacementAdvancesRevisionAndReportsRemovedRoots(t *testing.T) {
	service, root := newTestService(t, nil)
	firstRoot := filepath.Join(root, "src")
	secondRoot := filepath.Join(root, "other")
	if err := os.Mkdir(secondRoot, 0o700); err != nil {
		t.Fatal(err)
	}
	first, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "replace", "roots": []any{firstRoot}})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	firstToken := first["watch_token"].(string)
	second, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "replace", "roots": []any{secondRoot}})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if second["watch_token"] == firstToken || second["revision"].(uint64) <= first["revision"].(uint64) {
		t.Fatalf("replacement token/revision = %#v, first = %#v", second["watch_token"], first["watch_token"])
	}
	removed := second["removed_roots"].([]string)
	if len(removed) != 1 || removed[0] != firstRoot {
		t.Fatalf("removed roots = %#v", removed)
	}
	diff, finderErr := service.WatchDiff(context.Background(), firstToken, false)
	if finderErr != nil {
		t.Fatalf("watch diff replacement error = %#v", finderErr)
	}
	if got := diff["removed_roots"].([]string); len(got) != 1 || got[0] != firstRoot {
		t.Fatalf("watch diff removed roots = %#v", got)
	}
}

func TestSearchOptionsDoNotFallThroughToOnDemandTraversal(t *testing.T) {
	service, root := newTestService(t, nil)
	ignored := filepath.Join(root, "node_modules")
	if err := os.Mkdir(ignored, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ignored, "needle.txt"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	indexed, finderErr := service.SearchWithOptions(context.Background(), root, "needle", 10, true, false, false, SearchOptions{IndexedOnly: true})
	if finderErr != nil || indexed["index_state"] != "warming" || indexed["count"] != 0 {
		t.Fatalf("indexed-only search = %#v, error = %v", indexed, finderErr)
	}
	cursor, finderErr := service.SearchWithOptions(context.Background(), root, "needle", 10, true, false, false, SearchOptions{Cursor: "cursor"})
	if finderErr != nil || cursor["rebase_required"] != true || cursor["reason"] != "index_unavailable" {
		t.Fatalf("cursor search = %#v, error = %v", cursor, finderErr)
	}
	onDemand, finderErr := service.Search(context.Background(), root, "needle", 10, true, false, false)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if onDemand["count"] != 0 {
		t.Fatalf("excluded search result = %#v", onDemand)
	}
}

func TestDirectSearchBoundsDirectoryRowsAndCoverage(t *testing.T) {
	root := t.TempDir()
	native, nativeStatus := newNativeWatcher()
	service := newService(root, nil, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
	t.Cleanup(service.Close)
	for index := 0; index < MaxDirectoryEntries+5; index++ {
		if err := os.Mkdir(filepath.Join(root, fmt.Sprintf("match-dir-%04d", index)), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	search, finderErr := service.Search(context.Background(), root, "match-dir", MaxSearchResults, false, true, true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	rows := search["files"].([]map[string]any)
	if len(rows) != MaxDirectoryEntries || search["count"] != MaxDirectoryEntries || search["truncated"] != true {
		t.Fatalf("bounded direct search = %#v", search)
	}
	coverage := search["progressive_coverage"].(map[string]any)
	if coverage["visited_directories"] != MaxDirectoryEntries {
		t.Fatalf("direct search directory coverage = %#v", coverage)
	}
}

func TestRecursiveSearchBoundsEachDirectoryBeforeMaterializingAllEntries(t *testing.T) {
	root := t.TempDir()
	native, nativeStatus := newNativeWatcher()
	service := newService(root, nil, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
	t.Cleanup(service.Close)
	for index := 0; index < MaxDirectoryEntries+5; index++ {
		path := filepath.Join(root, fmt.Sprintf("match-file-%04d.txt", index))
		if err := os.WriteFile(path, []byte("match"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	search, finderErr := service.Search(context.Background(), root, "match-file", MaxSearchResults, true, false, true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if len(search["files"].([]map[string]any)) != MaxDirectoryEntries || search["truncated"] != true {
		t.Fatalf("bounded recursive search = %#v", search)
	}
}

func TestRecursiveSearchSkipsSpecialFilesWithoutBlocking(t *testing.T) {
	root := t.TempDir()
	native, nativeStatus := newNativeWatcher()
	service := newService(root, nil, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
	t.Cleanup(service.Close)
	fifo := filepath.Join(root, "blocked.fifo")
	if err := syscall.Mkfifo(fifo, 0o600); err != nil {
		t.Skipf("FIFO unavailable: %v", err)
	}
	search, finderErr := service.Search(context.Background(), root, "blocked", 10, true, false, true)
	if finderErr != nil || search["count"] != 0 {
		t.Fatalf("special-file search = %#v, error = %v", search, finderErr)
	}
}

func TestWatchDiffPollsBackgroundFilesOutsideWatchedRoots(t *testing.T) {
	serviceRoot := t.TempDir()
	root := filepath.Join(serviceRoot, "watched")
	backgroundRoot := filepath.Join(serviceRoot, "background")
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(backgroundRoot, 0o700); err != nil {
		t.Fatal(err)
	}
	background := filepath.Join(backgroundRoot, "background.md")
	if err := os.WriteFile(background, []byte("before\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	native, nativeStatus := newNativeWatcher()
	service := newService(serviceRoot, nil, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
	t.Cleanup(service.Close)
	registration, finderErr := service.WatchRoots(context.Background(), map[string]any{
		"client_id":        "background-watch",
		"roots":            []any{root},
		"background_files": []any{background},
	})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	baseline := registration["watch_token"].(string)
	if err := os.WriteFile(background, []byte("after\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service.pollWatches()
	diff, finderErr := service.WatchDiff(context.Background(), baseline, false)
	if finderErr != nil {
		t.Fatalf("watch diff error = %#v", finderErr)
	}
	if diff["mode"] != "delta" || diff["refresh"] != false || diff["token"] == baseline {
		t.Fatalf("background file diff = %#v", diff)
	}
	files := diff["files"].([]any)
	if len(files) != 1 || files[0].(map[string]any)["path"] != background {
		t.Fatalf("background file paths = %#v", files)
	}
}

func TestSearchStreamPublishesBoundedChunksDuringTraversal(t *testing.T) {
	service, root := newTestService(t, nil)
	for index := 0; index < 40; index++ {
		path := filepath.Join(root, "match-"+strconv.Itoa(index)+".txt")
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	chunks := 0
	seen := 0
	payload, finderErr := service.SearchStream(context.Background(), root, "match", 40, true, false, true, func(files []map[string]any, coverage map[string]any) error {
		if len(files) == 0 || coverage["visited_files"] == nil {
			t.Fatalf("stream chunk = %#v coverage = %#v", files, coverage)
		}
		chunks++
		seen += len(files)
		return nil
	})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if chunks == 0 || seen != 40 || payload["count"] != 40 {
		t.Fatalf("stream result chunks=%d seen=%d payload=%#v", chunks, seen, payload)
	}
	if files, ok := payload["files"].([]map[string]any); !ok || len(files) != 0 {
		t.Fatalf("stream retained all results in memory: %#v", payload["files"])
	}
}

func TestFinderUsesPythonFilesystemLimits(t *testing.T) {
	service, root := newTestService(t, nil)
	writeLimitContent := strings.Repeat("x", 5*1024*1024+1)
	if _, finderErr := service.Write(context.Background(), filepath.Join(root, "too-large.txt"), writeLimitContent, nil); finderErr == nil || finderErr.Status != 413 {
		t.Fatalf("write over Python limit = %#v, want 413", finderErr)
	}

	rawPath := filepath.Join(root, "raw.bin")
	if err := os.WriteFile(rawPath, []byte(strings.Repeat("r", 65*1024*1024)), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, finderErr := service.Raw(context.Background(), rawPath, 0); finderErr != nil {
		t.Fatalf("raw file under Python transfer limit = %#v", finderErr)
	}

	search, finderErr := service.Search(context.Background(), root, "", 500, false, false, true)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if got := search["limit"]; got != 500 {
		t.Fatalf("search limit = %v, want 500", got)
	}
}

func TestInfoReturnsDirectoryCapability(t *testing.T) {
	service, root := newTestService(t, nil)
	info, finderErr := service.Info(context.Background(), root, false)
	if finderErr != nil {
		t.Fatalf("directory info error = %v", finderErr)
	}
	if info["kind"] != "dir" || info["size"] != nil || info["mtime"] != nil {
		t.Fatalf("directory info = %#v", info)
	}
}
