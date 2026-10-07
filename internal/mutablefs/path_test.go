// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package mutablefs

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestLocalAdmissionDoesNotCreateRejectedTarget(t *testing.T) {
	for _, kind := range []string{"nfs", "nfs4", "cifs", "smb", "smbfs", "9p", "fuse", "fuse.sshfs", "unknown", ""} {
		t.Run(kind, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "missing", "db.sqlite")
			probe := func(string) (string, error) { return kind, nil }
			if requireLocal(path, probe, false) == nil {
				t.Fatal("unsupported filesystem admitted")
			}
			if _, err := os.Stat(filepath.Dir(path)); !os.IsNotExist(err) {
				t.Fatalf("rejection created parent: %v", err)
			}
			if requireLocal(path, probe, true) != nil {
				t.Fatal("explicit override refused")
			}
		})
	}
	for _, kind := range []string{"apfs", "hfs", "btrfs", "ext2", "ext3", "ext4", "overlay", "tmpfs", "xfs", "zfs"} {
		if err := requireLocal(t.TempDir(), func(string) (string, error) { return kind, nil }, false); err != nil {
			t.Fatal(err)
		}
	}
	if requireLocal(t.TempDir(), func(string) (string, error) { return "ext4", errors.New("probe failed") }, false) == nil {
		t.Fatal("probe error admitted")
	}
}
func TestCanonicalMissingDescendantResolvesAncestorAlias(t *testing.T) {
	root := t.TempDir()
	real := filepath.Join(root, "real")
	alias := filepath.Join(root, "alias")
	if err := os.Mkdir(real, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(real, alias); err != nil {
		t.Fatal(err)
	}
	got, err := Canonical(filepath.Join(alias, "not", "yet", "created"))
	if err != nil {
		t.Fatal(err)
	}
	if got != filepath.Join(real, "not", "yet", "created") {
		t.Fatalf("canonical = %s", got)
	}
}
