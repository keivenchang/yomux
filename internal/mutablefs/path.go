// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package mutablefs admits local mutable storage before callers create artifacts.
package mutablefs

import (
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

	"github.com/keivenchang/yomux/internal/productenv"
)

// Canonical resolves even a missing descendant through its nearest existing ancestor.
func Canonical(path string) (string, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	current := absolute
	var suffix []string
	for {
		resolved, err := filepath.EvalSymlinks(current)
		if err == nil {
			for i := len(suffix) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, suffix[i])
			}
			return resolved, nil
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		parent := filepath.Dir(current)
		if parent == current {
			return "", err
		}
		// A dangling symlink must not be mistaken for a missing ordinary directory.
		if info, e := os.Lstat(current); e == nil && info.Mode()&os.ModeSymlink != 0 {
			return "", err
		}
		suffix = append(suffix, filepath.Base(current))
		current = parent
	}
}

func localType(kind string) bool {
	switch kind {
	case "apfs", "hfs", "btrfs", "ext2", "ext3", "ext4", "overlay", "tmpfs", "xfs", "zfs":
		return true
	}
	return false
}

func requireLocal(path string, probe func(string) (string, error), override bool) error {
	canonical, err := Canonical(path)
	if err != nil {
		return err
	}
	existing := canonical
	for {
		if _, err = os.Stat(existing); err == nil {
			break
		}
		if !os.IsNotExist(err) {
			return err
		}
		parent := filepath.Dir(existing)
		if parent == existing {
			return err
		}
		existing = parent
	}
	kind, err := probe(existing)
	if err == nil && localType(strings.ToLower(kind)) {
		return nil
	}
	if override {
		slog.Warn("Emergency override: mutable storage filesystem is unsupported", "path", canonical, "filesystem", kind, "error", err)
		return nil
	}
	return fmt.Errorf("mutable storage requires a supported local filesystem: %s (%s): %v", canonical, kind, err)
}

// RequireLocal never creates the target or its containing directory.
func RequireLocal(path string) error {
	return requireLocal(path, filesystemType, productenv.Value("ALLOW_NETWORK_FILESYSTEM_MUTABLE_ROOTS") == "1")
}
