// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
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
	service := newService(root, []string{root}, publish, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
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
	for _, args := range [][]string{{"init", "-q", root}, {"-C", root, "config", "gc.auto", "0"}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "config", "user.name", "Finder Test"}, {"-C", root, "add", "--", "note.txt"}, {"-C", root, "commit", "-qm", "initial"}} {
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

type fakeNativeWatcher struct {
	events chan nativeWatchEvent
	status nativeWatchStatus
	roots  []string
}

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

func TestAuthorizationTreatsFilesystemRootSlashAsContainingDescendants(t *testing.T) {
	service := &Service{roots: []string{string(os.PathSeparator)}}
	if !service.allowed(filepath.Join(string(os.PathSeparator), "tmp", "file")) {
		t.Fatal("filesystem root slash rejected a descendant")
	}
}

func TestWatchRootsSerializesNativeReconfiguration(t *testing.T) {
	root := t.TempDir()
	secondRoot := t.TempDir()
	native := &serializedNativeWatcher{events: make(chan nativeWatchEvent), firstStarted: make(chan struct{}), secondStarted: make(chan struct{}), releaseFirst: make(chan struct{})}
	service := newService(root, []string{root, secondRoot}, nil, native, nativeWatchStatus{available: true}, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
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

func TestAuthorizationRejectsOutsideAndSecretPaths(t *testing.T) {
	service, root := newTestService(t, nil)
	outside := filepath.Join(t.TempDir(), "outside.txt")
	if err := os.WriteFile(outside, []byte("no"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, finderErr := service.Read(context.Background(), outside); finderErr == nil || finderErr.Status != 403 {
		t.Fatalf("outside read error = %#v, want 403", finderErr)
	}
	secret := filepath.Join(root, ".ssh", "id_rsa")
	if _, finderErr := service.Info(context.Background(), secret); finderErr == nil || finderErr.Status != 403 {
		t.Fatalf("secret info error = %#v, want 403", finderErr)
	}
	if _, finderErr := service.Read(context.Background(), filepath.Join(root, "missing.txt")); finderErr == nil || finderErr.Status != 404 {
		t.Fatalf("missing read error = %#v, want 404", finderErr)
	}
}

func TestAuthorizationRejectsCredentialSubtreeDescendants(t *testing.T) {
	service, root := newTestService(t, nil)
	for _, relative := range []string{
		".config/gh/hosts.yml",
		".config/git/credentials",
		".docker/contexts/meta.json",
		".ngc/cache/token",
	} {
		path := filepath.Join(root, relative)
		if _, finderErr := service.Info(context.Background(), path); finderErr == nil || finderErr.Status != http.StatusForbidden {
			t.Fatalf("secret descendant %q error = %#v, want 403", relative, finderErr)
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

func TestSymlinkEscapeIsRejectedAfterOpen(t *testing.T) {
	service, root := newTestService(t, nil)
	outside := filepath.Join(t.TempDir(), "outside.txt")
	if err := os.WriteFile(outside, []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "escape.txt")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if _, finderErr := service.Read(context.Background(), link); finderErr == nil || finderErr.Status != 403 {
		t.Fatalf("symlink escape error = %#v, want 403", finderErr)
	}
}

func TestListUsesPinnedDirectoryAndSkipsEscapingSymlink(t *testing.T) {
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
	if _, ok := seen["outside-link"]; ok {
		t.Fatalf("escaping symlink leaked into listing: %#v", seen["outside-link"])
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
	if !ok || status["registrations"].(int) < 1 {
		t.Fatalf("native watch status = %#v", registration["watch_status"])
	}
	if registration["mode"] == "polling-fallback" {
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
	fake := &fakeNativeWatcher{events: make(chan nativeWatchEvent, 2), status: nativeWatchStatus{available: true}}
	service, root := newTestServiceWithNative(t, nil, fake, fake.status)
	baseline, finderErr := service.WatchRoots(context.Background(), map[string]any{"client_id": "overflow", "roots": []any{root}})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	service.handleNativeEvent(nativeWatchEvent{root: root, overflow: true})
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
	if _, finderErr = service.GitCommit(context.Background(), root, strings.Repeat("0", 40), latestHead); finderErr == nil || finderErr.Status != http.StatusNotFound {
		t.Fatalf("unknown commit = %v", finderErr)
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
	stop := make(chan struct{})
	go func() {
		defer close(stop)
		deadline := time.NewTimer(5 * time.Second)
		defer deadline.Stop()
		ticker := time.NewTicker(time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-deadline.C:
				return
			case <-ticker.C:
			}
			entries, readErr := os.ReadDir(root)
			if readErr == nil {
				for _, entry := range entries {
					if strings.HasPrefix(entry.Name(), ".yolomux-write-") {
						if writeErr := os.WriteFile(path, []byte("external edit\n"), 0o600); writeErr == nil {
							_ = os.Chtimes(path, time.Now().Add(time.Second), time.Now().Add(time.Second))
							close(targetChanged)
						}
						return
					}
				}
			}
		}
	}()
	expectedMtime := before.ModTime().UnixNano()
	_, finderErr := service.Write(context.Background(), path, content, &expectedMtime)
	<-stop
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
	native := &fakeNativeWatcher{events: make(chan nativeWatchEvent), status: nativeWatchStatus{available: true}}
	service := newService(root, []string{root, parent}, nil, native, native.status, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
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
	service := newService(root, []string{root}, nil, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
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
	service := newService(root, []string{root}, nil, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
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
	service := newService(root, []string{root}, nil, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
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
	service := newService(serviceRoot, []string{serviceRoot}, nil, native, nativeStatus, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
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
