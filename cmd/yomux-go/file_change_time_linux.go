// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"os"
	"syscall"
)

// fileChangeTimeNS returns the inode change time, or 0 when the platform does not report it.
// Unlike the modification time, user tools cannot set it, and every content change moves it.
func fileChangeTimeNS(info os.FileInfo) int64 {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0
	}
	return stat.Ctim.Sec*1e9 + stat.Ctim.Nsec
}
