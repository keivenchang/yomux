// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

//go:build linux

package main

import (
	"log/slog"
	"os"
	"runtime"
	"strconv"
	"syscall"

	"golang.org/x/sys/unix"
)

func lowerSearchIndexWorkerPriority() {
	// Linux stores nice and I/O priority per thread. Go may move crawl work onto any
	// runtime thread, including threads created before main. Future threads inherit these priorities.
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	lowerSearchIndexThreadPriority(0)
	threads, err := os.ReadDir("/proc/self/task")
	if err != nil {
		slog.Warn("Finder worker thread priorities could not be enumerated", "error", err)
		return
	}
	for _, thread := range threads {
		tid, err := strconv.Atoi(thread.Name())
		if err != nil {
			continue
		}
		lowerSearchIndexThreadPriority(tid)
	}
}

func lowerSearchIndexThreadPriority(tid int) {
	if err := syscall.Setpriority(syscall.PRIO_PROCESS, tid, 19); err != nil && err != syscall.ESRCH {
		slog.Warn("Finder worker nice priority could not be lowered", "thread", tid, "error", err)
	}
	const ioprioClassIdle = 3
	const ioprioWhoProcess = 1
	priority := uintptr(ioprioClassIdle << 13)
	if _, _, errno := unix.Syscall(unix.SYS_IOPRIO_SET, ioprioWhoProcess, uintptr(tid), priority); errno != 0 && errno != syscall.ESRCH {
		slog.Warn("Finder worker I/O priority could not be lowered", "thread", tid, "error", errno)
	}
}
