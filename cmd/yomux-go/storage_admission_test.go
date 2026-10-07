// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestConfigLeaseAliasesRejectWithoutChangingJournal(t *testing.T) {
	root := t.TempDir()
	real := filepath.Join(root, "real")
	alias := filepath.Join(root, "alias")
	if err := os.Mkdir(real, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(real, alias); err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(real, "missing", "config")
	first, err := acquireConfigLease(config)
	if err != nil {
		t.Fatal(err)
	}
	defer releaseInstanceRootLease(first)
	path := filepath.Join(config, "go-events.jsonl")
	original := []byte("unrepaired partial journal")
	if err = os.WriteFile(path, original, 0600); err != nil {
		t.Fatal(err)
	}
	if second, err := acquireConfigLease(filepath.Join(alias, "missing", "config")); err == nil {
		releaseInstanceRootLease(second)
		t.Fatal("alias admitted duplicate config owner")
	}
	data, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(data, original) {
		t.Fatal("rejection mutated journal")
	}
	releaseInstanceRootLease(first)
	first = nil
	next, err := acquireConfigLease(config)
	if err != nil {
		t.Fatal("released lease remained owned:", err)
	}
	releaseInstanceRootLease(next)
}
func TestConfigLeaseDoesNotFollowLockSymlink(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "other")
	original := []byte("preserve")
	if err := os.WriteFile(target, original, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, filepath.Join(dir, "go-storage.lock")); err != nil {
		t.Fatal(err)
	}
	if lock, err := acquireConfigLease(dir); err == nil {
		releaseInstanceRootLease(lock)
		t.Fatal("lock symlink admitted")
	}
	data, _ := os.ReadFile(target)
	if !bytes.Equal(data, original) {
		t.Fatal("symlink target mutated")
	}
}
