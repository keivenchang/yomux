// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package atomicfile

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestFileLockContentionCancellationAndPersistentIdentity(t *testing.T) {
	path := filepath.Join(t.TempDir(), "owner.lock")
	first, err := AcquireLock(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	if second, err := AcquireLock(ctx, path); !errors.Is(err, context.DeadlineExceeded) {
		if second != nil {
			second.Close()
		}
		t.Fatalf("contended lease error=%v", err)
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	second, err := AcquireLock(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := second.Close(); err != nil {
		t.Fatal(err)
	}
	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if !os.SameFile(before, after) {
		t.Fatal("lease changed persistent lock identity")
	}
}

func TestFileLockRejectsCancelledAndSymlinkTargets(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "owner.lock")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := AcquireLock(ctx, path); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancel error=%v", err)
	}
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		t.Fatalf("cancelled lease created a file: %v", err)
	}
	target := filepath.Join(root, "target")
	if err := os.WriteFile(target, []byte("preserved"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, path); err != nil {
		t.Fatal(err)
	}
	if lease, err := AcquireLock(context.Background(), path); err == nil {
		lease.Close()
		t.Fatal("symlink lease accepted")
	}
	raw, err := os.ReadFile(target)
	if err != nil || string(raw) != "preserved" {
		t.Fatalf("symlink target changed: %v", err)
	}
}
