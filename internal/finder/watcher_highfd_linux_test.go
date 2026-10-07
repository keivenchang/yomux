//go:build linux

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package finder

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestWatcherDescriptorAboveSelectLimit(t *testing.T) {
	var files []*os.File
	defer func() {
		for _, f := range files {
			f.Close()
		}
	}()
	for {
		f, err := os.Open("/dev/null")
		if err != nil {
			t.Fatal(err)
		}
		files = append(files, f)
		if f.Fd() >= 1024 {
			break
		}
	}
	controller, status := newNativeWatcher()
	if !status.available {
		t.Fatal(status)
	}
	defer controller.Close()
	watcher := controller.(*linuxNativeWatcher)
	if watcher.fd < 1024 {
		t.Fatal("descriptor fixture too low")
	}
	root := t.TempDir()
	controller.SetRoots(context.Background(), []string{root})
	if err := os.WriteFile(filepath.Join(root, "changed"), []byte("data"), 0600); err != nil {
		t.Fatal(err)
	}
	select {
	case <-controller.Events():
	case <-time.After(time.Second):
		t.Fatal("native event missing")
	}
}
