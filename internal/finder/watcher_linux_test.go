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
		if event.overflow && event.root == "/root" {
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
