// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build linux

package stats

import (
	"os"
	"path/filepath"
	"strconv"
)

func ProcessOpenFiles(pid int) []string {
	if pid <= 0 {
		return nil
	}
	entries, err := os.ReadDir(filepath.Join("/proc", strconv.Itoa(pid), "fd"))
	if err != nil {
		return nil
	}
	paths := make([]string, 0, len(entries))
	for _, entry := range entries {
		if path, err := os.Readlink(filepath.Join("/proc", strconv.Itoa(pid), "fd", entry.Name())); err == nil {
			paths = append(paths, path)
		}
	}
	return paths
}
