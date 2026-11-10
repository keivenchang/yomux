// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type watchDiffProbeContext struct {
	context.Context
	checks atomic.Int64
}

func (c *watchDiffProbeContext) Done() <-chan struct{} {
	c.checks.Add(1)
	return c.Context.Done()
}

func TestWatchDiffDoesNotTraverseUnsubscribedDescendants(t *testing.T) {
	s, root := newTestService(t, nil)
	nested := filepath.Join(root, "src", "nested")
	if err := os.Mkdir(nested, 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := s.WatchRoots(context.Background(), map[string]any{
		"client_id": "finder", "roots": []any{root}, "root_surfaces_version": 1,
		"root_surfaces": []any{map[string]any{"path": root, "surfaces": []any{"finder"}}},
	}); err != nil {
		t.Fatal(err)
	}
	probe := func() int64 {
		ctx := &watchDiffProbeContext{Context: context.Background()}
		if _, err := s.WatchDiff(ctx, "", true); err != nil {
			t.Fatal(err)
		}
		return ctx.checks.Load()
	}
	before := probe()
	for i := 0; i < 64; i++ {
		if err := os.WriteFile(filepath.Join(nested, fmt.Sprintf("%d.data", i)), []byte("nested"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if after := probe(); after != before {
		t.Fatalf("full baseline work grew with unsubscribed descendants: %d -> %d context checks", before, after)
	}
}

type shutdownNativeWatcher struct {
	events  chan nativeWatchEvent
	started chan struct{}
	release chan struct{}
	once    sync.Once
}

func TestWatchRootsSupersededReplacementDoesNotRemoveActiveRoot(t *testing.T) {
	for _, empty := range []bool{false, true} {
		name := "active-root"
		if empty {
			name = "empty-v1"
		}
		t.Run(name, func(t *testing.T) { testWatchRootsSupersededReplacement(t, empty) })
	}
}

func testWatchRootsSupersededReplacement(t *testing.T, empty bool) {
	firstRoot, secondRoot := t.TempDir(), t.TempDir()
	native := &serializedNativeWatcher{events: make(chan nativeWatchEvent), firstStarted: make(chan struct{}), secondStarted: make(chan struct{}), releaseFirst: make(chan struct{})}
	service := newService(firstRoot, nil, native, nativeWatchStatus{available: true}, UploadLimits{})
	var releaseOnce sync.Once
	release := func() { releaseOnce.Do(func() { close(native.releaseFirst) }) }
	t.Cleanup(func() { release(); service.Close() })
	service.mu.Lock()
	service.watchGeneration = 1
	service.watches["replace"] = watchRecord{Roots: []string{firstRoot}, Signatures: map[string]string{}, Generation: 1}
	service.nativeRoots = []string{firstRoot}
	service.mu.Unlock()
	type result struct {
		payload map[string]any
		err     *Error
	}
	first := make(chan result, 1)
	go func() {
		payload, err := service.WatchRoots(context.Background(), map[string]any{"client_id": "replace", "roots": []any{secondRoot}})
		first <- result{payload, err}
	}()
	select {
	case <-native.firstStarted:
	case <-time.After(time.Second):
		t.Fatal("first replacement did not reach native registration")
	}
	expectedRoots := []string{firstRoot}
	newRoots := []any{firstRoot}
	newSurfaces := []any{map[string]any{"path": firstRoot, "surfaces": []any{"finder"}}}
	if empty {
		expectedRoots = []string{}
		newRoots = []any{}
		newSurfaces = []any{}
	}
	second := make(chan result, 1)
	go func() {
		payload, err := service.WatchRoots(context.Background(), map[string]any{"client_id": "replace", "roots": newRoots, "root_surfaces_version": 1, "root_surfaces": newSurfaces})
		second <- result{payload, err}
	}()
	deadline := time.NewTimer(time.Second)
	defer deadline.Stop()
	for {
		service.mu.Lock()
		record := service.watches["replace"]
		newer := record.Generation > 2 && reflect.DeepEqual(record.Roots, expectedRoots)
		service.mu.Unlock()
		if newer {
			break
		}
		select {
		case early := <-second:
			t.Fatalf("newer replacement returned before admission: %v", early.err)
		case <-deadline.C:
			t.Fatal("newer replacement did not publish its watch record")
		default:
			runtime.Gosched()
		}
	}
	release()
	for index, reply := range []<-chan result{first, second} {
		select {
		case response := <-reply:
			if response.err != nil {
				t.Fatal(response.err)
			}
			roots, ok := response.payload["roots"].([]string)
			if !ok || !reflect.DeepEqual(roots, expectedRoots) {
				t.Fatalf("stale registration response: %#v", response.payload)
			}
			if response.payload["root_surfaces_version"] != 1 {
				t.Fatalf("stale protocol version: %#v", response.payload)
			}
			if index == 0 && len(response.payload["removed_roots"].([]string)) != 0 {
				t.Fatalf("superseded response retained removals: %#v", response.payload)
			}
			if len(response.payload["root_surfaces"].([]map[string]any)) != len(newSurfaces) || len(response.payload["files"].([]string)) != 0 {
				t.Fatalf("stale descriptors: %#v", response.payload)
			}
		case <-time.After(time.Second):
			t.Fatal("replacement did not complete")
		}
	}
	service.mu.Lock()
	defer service.mu.Unlock()
	for _, change := range service.changes {
		for _, removed := range change.removedRoots {
			if removed == firstRoot {
				t.Fatalf("superseded replacement published active root as removed at revision %d: %q", change.revision, removed)
			}
		}
	}
}

func (w *shutdownNativeWatcher) Events() <-chan nativeWatchEvent { return w.events }
func (w *shutdownNativeWatcher) Close()                          { close(w.events) }
func (w *shutdownNativeWatcher) SetRoots(ctx context.Context, _ []string) nativeWatchStatus {
	w.once.Do(func() { close(w.started) })
	select {
	case <-ctx.Done():
	case <-w.release:
	}
	return nativeWatchStatus{available: true}
}
func TestWatchCloseCancelsActiveRegistration(t *testing.T) {
	native := &shutdownNativeWatcher{events: make(chan nativeWatchEvent), started: make(chan struct{}), release: make(chan struct{})}
	service := newService(t.TempDir(), nil, native, nativeWatchStatus{}, UploadLimits{})
	registrationDone := make(chan struct{})
	go func() { defer close(registrationDone); service.reconfigureNative(context.Background(), true) }()
	select {
	case <-native.started:
	case <-time.After(time.Second):
		t.Fatal("registration did not start")
	}
	closeDone := make(chan struct{})
	go func() { service.Close(); close(closeDone) }()
	select {
	case <-closeDone:
	case <-time.After(time.Second):
		close(native.release)
		<-closeDone
		t.Fatal("Close did not cancel registration")
	}
	<-registrationDone
}

func TestWatchDiffReportsRegisteredFileMissingAfterParentRename(t *testing.T) {
	s, root := newTestService(t, nil)
	parent := filepath.Join(root, "src")
	path := filepath.Join(parent, "watched.txt")
	if err := os.WriteFile(path, []byte("base"), 0600); err != nil {
		t.Fatal(err)
	}
	registration, failure := s.WatchRoots(context.Background(), map[string]any{
		"client_id": "parent-rename", "roots": []any{root}, "files": []any{path},
	})
	if failure != nil {
		t.Fatal(failure)
	}
	if err := os.Rename(parent, filepath.Join(root, "renamed-parent")); err != nil {
		t.Fatal(err)
	}
	s.noteChange([]string{path}, "rename")
	for _, full := range []bool{false, true} {
		result, failure := s.WatchDiff(context.Background(), registration["watch_token"].(string), full)
		if failure != nil {
			t.Fatalf("full=%v: vanished parent rejected watch diff: %v", full, failure)
		}
		files := result["files"].([]any)
		if len(files) != 1 || !reflect.DeepEqual(files[0].(map[string]any)["signature"], []any{path, "missing"}) {
			t.Fatalf("full=%v: missing signature = %v", full, files)
		}
	}
}
