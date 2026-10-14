//go:build linux

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestLinuxNativeWatcherRetainsOverflowMarkerWhenQueueSaturates(t *testing.T) {
	watcher := &linuxNativeWatcher{
		events: make(chan nativeWatchEvent, 2),
		closed: make(chan struct{}),
	}
	watcher.emit(nativeWatchEvent{path: "/root/one", root: "/root"})
	watcher.emit(nativeWatchEvent{path: "/root/two", root: "/root"})
	watcher.emit(nativeWatchEvent{path: "/root/three", root: "/root"})
	close(watcher.closed)
	close(watcher.events)

	foundOverflow := false
	for event := range watcher.events {
		if event.overflow && event.root == "" {
			foundOverflow = true
		}
	}
	if !foundOverflow {
		t.Fatal("saturated native queue dropped its overflow reconciliation marker")
	}
}

func TestLinuxNativeWatcherBoundsFlatDirectoryTraversal(t *testing.T) {
	root := t.TempDir()
	for index := 0; index <= maxNativeWatchEntries; index++ {
		path := filepath.Join(root, fmt.Sprintf("entry-%04d", index))
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	watcherValue, initialStatus := newNativeWatcher()
	if watcherValue == nil || !initialStatus.available {
		t.Skip("inotify unavailable")
	}
	watcher := watcherValue.(*linuxNativeWatcher)
	defer watcher.Close()
	status := watcher.SetRoots(context.Background(), []string{root})
	if !status.degraded || status.reason != "native-entry-capacity-exceeded" {
		t.Fatalf("flat traversal status = %+v", status)
	}
}

func TestLinuxNativeWatcherReconcilesOverlappingScopesAfterNestedOverflow(t *testing.T) {
	root := filepath.Join(t.TempDir(), "repo")
	nested := filepath.Join(root, "sub")
	child := filepath.Join(nested, "child")
	if err := os.MkdirAll(child, 0o700); err != nil {
		t.Fatal(err)
	}

	watcherValue, initialStatus := newNativeWatcher()
	if watcherValue == nil || !initialStatus.available {
		t.Skip("inotify unavailable")
	}
	watcher := watcherValue.(*linuxNativeWatcher)
	defer watcher.Close()

	scopes := map[string]bool{root: true, nested: false}
	status := watcher.SetScopes(context.Background(), scopes, false)
	if status.degraded {
		t.Fatalf("initial scope registration = %+v", status)
	}
	status = watcher.SetScopes(context.Background(), map[string]bool{root: true}, false)
	if status.degraded {
		t.Fatalf("scope release = %+v", status)
	}
	watcher.mu.Lock()
	childPreserved := false
	for descriptor, path := range watcher.watches {
		if path == child {
			childPreserved = true
			if owner := watcher.roots[descriptor]; owner != root {
				watcher.mu.Unlock()
				t.Fatalf("released nested scope left descendant owner %q, want %q", owner, root)
			}
		}
	}
	watcher.mu.Unlock()
	if !childPreserved {
		t.Fatal("releasing the nested scope removed a descendant still required by the recursive scope")
	}
	status = watcher.SetScopes(context.Background(), scopes, false)
	if status.degraded {
		t.Fatalf("nested scope re-registration = %+v", status)
	}

	watcher.InvalidateRoot(nested)
	status = watcher.SetScopes(context.Background(), scopes, false)
	if status.degraded || len(status.uncovered) != 0 {
		t.Fatalf("overlapping scope recovery = %+v", status)
	}

	watcher.mu.Lock()
	defer watcher.mu.Unlock()
	childWatched := false
	childOwner := ""
	for descriptor, path := range watcher.watches {
		if path == child {
			childWatched = true
			childOwner = watcher.roots[descriptor]
			break
		}
	}
	if !childWatched {
		t.Fatal("nested overflow recovery left a recursive descendant unwatched")
	}
	if childOwner != root {
		t.Fatalf("recursive descendant owner = %q, want %q", childOwner, root)
	}
}

func TestNativeWatcherLateIgnoredEventPreservesReplacementPath(t *testing.T) {
	watcher := &linuxNativeWatcher{watches: make(map[int]string)}
	watcher.rememberRegistration(1, "/root/recreated")
	watcher.rememberRegistration(2, "/root/recreated")
	watcher.forgetRegistration(1)
	if watcher.paths["/root/recreated"] != 2 || watcher.watches[2] != "/root/recreated" {
		t.Fatalf("replacement registration lost: %+v %+v", watcher.paths, watcher.watches)
	}
}
