// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"testing"
)

func TestBrokenSymlinkTargetAppearingReleasesProbeDescriptor(t *testing.T) {
	fdRoot := "/proc/self/fd"
	if runtime.GOOS == "darwin" {
		fdRoot = "/dev/fd"
	} else if runtime.GOOS != "linux" {
		t.Skip("open descriptor census requires Linux procfs or Darwin devfs")
	}
	previousGC := debug.SetGCPercent(-1)
	defer debug.SetGCPercent(previousGC)
	root := t.TempDir()
	target, link := filepath.Join(root, "target"), filepath.Join(root, "link")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	service.beforeBrokenSymlinkRecheck = func() {
		if err := os.WriteFile(target, []byte("appeared after the missing-target probe"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	count := func() int {
		t.Helper()
		entries, err := os.ReadDir(fdRoot)
		if err != nil {
			t.Fatal(err)
		}
		return len(entries)
	}
	before := count()
	for range 32 {
		if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
			t.Fatal(err)
		}
		_, finderErr := service.contentInfo(context.Background(), link)
		if finderErr == nil || finderErr.Code != "path_not_found" {
			t.Fatalf("stale missing-target probe returned %v", finderErr)
		}
	}
	if after := count(); after != before {
		t.Fatalf("probe descriptors retained until garbage collection: before=%d after=%d", before, after)
	}
}
