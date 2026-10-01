// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package stats

/*
#include <mach/mach.h>
#include <mach/host_info.h>
#include <sys/proc_info.h>
#include <sys/sysctl.h>
#include <libproc.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

struct yomux_process {
	int pid;
	int ppid;
	uint64_t start_sec;
	uint64_t start_usec;
	uint64_t cpu_ns;
	uint64_t rss;
	char path[PROC_PIDPATHINFO_MAXSIZE];
	char cwd[MAXPATHLEN];
	char command[4096];
};

static int yomux_host_cpu(uint64_t *total, uint64_t *idle) {
	host_cpu_load_info_data_t info;
	mach_msg_type_number_t count = HOST_CPU_LOAD_INFO_COUNT;
	mach_port_t host = mach_host_self();
	kern_return_t status = host_statistics(host, HOST_CPU_LOAD_INFO, (host_info_t)&info, &count);
	mach_port_deallocate(mach_task_self(), host);
	if (status != KERN_SUCCESS) return -1;
	*total = 0;
	*idle = 0;
	for (int i = 0; i < CPU_STATE_MAX; i++) {
		*total += info.cpu_ticks[i];
		if (i == CPU_STATE_IDLE) *idle = info.cpu_ticks[i];
	}
	return *total ? 0 : -1;
}

static int yomux_open_files(int pid, char **paths, int capacity) {
	int bytes = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, NULL, 0);
	if (bytes <= 0) return 0;
	int count = bytes / sizeof(struct proc_fdinfo);
	if (count > capacity) count = capacity;
	struct proc_fdinfo *fds = calloc((size_t)count, sizeof(struct proc_fdinfo));
	if (!fds) return -1;
	bytes = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, fds, count * sizeof(struct proc_fdinfo));
	if (bytes < 0) { free(fds); return -1; }
	count = bytes / sizeof(struct proc_fdinfo);
	int used = 0;
	for (int i = 0; i < count && used < capacity; i++) {
		if (fds[i].proc_fdtype != PROX_FDTYPE_VNODE) continue;
		struct vnode_fdinfowithpath info;
		if (proc_pidfdinfo(pid, fds[i].proc_fd, PROC_PIDFDVNODEPATHINFO, &info, sizeof(info)) != sizeof(info)) continue;
		if (info.pvip.vip_path[0] == '\0') continue;
		paths[used++] = strdup(info.pvip.vip_path);
	}
	free(fds);
	return used;
}

static void yomux_command(int pid, const char *path, char *out, size_t capacity) {
	strlcpy(out, path, capacity);
	int mib[3] = { CTL_KERN, KERN_PROCARGS2, pid };
	size_t size = 65536;
	char *buffer = calloc(size, 1);
	if (!buffer) return;
	if (sysctl(mib, 3, buffer, &size, NULL, 0) != 0 || size <= sizeof(int)) { free(buffer); return; }
	int argc = 0;
	memcpy(&argc, buffer, sizeof(argc));
	if (argc < 1 || argc > 4096) { free(buffer); return; }
	char *cursor = buffer + sizeof(int);
	char *end = buffer + size;
	while (cursor < end && *cursor != '\0') cursor++;
	while (cursor < end && *cursor == '\0') cursor++;
	out[0] = '\0';
	for (int i = 0; i < argc && cursor < end; i++) {
		size_t used = strlen(out);
		if (used + 2 >= capacity) break;
		out[used++] = ' ';
		while (cursor < end && *cursor != '\0' && used + 1 < capacity) out[used++] = *cursor++;
		out[used] = '\0';
		while (cursor < end && *cursor == '\0') cursor++;
	}
	if (out[0] == '\0') strlcpy(out, path, capacity);
	free(buffer);
}

static int yomux_pids(pid_t **result, int capacity, int *complete) {
	*result = NULL;
	*complete = 0;
	int bytes = proc_listpids(PROC_ALL_PIDS, 0, NULL, 0);
	if (bytes <= 0) return -1;
	// Leave room for processes created between the sizing and collection calls.
	int count = bytes / sizeof(pid_t) + 32;
	if (count > capacity) count = capacity;
	pid_t *pids = calloc((size_t)count, sizeof(pid_t));
	if (!pids) return -1;
	bytes = proc_listpids(PROC_ALL_PIDS, 0, pids, count * sizeof(pid_t));
	if (bytes <= 0) { free(pids); return -1; }
	*complete = bytes < count * sizeof(pid_t);
	*result = pids;
	return bytes / sizeof(pid_t);
}

static int yomux_process(int pid, struct yomux_process *row) {
		if (pid <= 0) return 0;
		struct proc_taskallinfo info;
		errno = 0;
		int got = proc_pidinfo(pid, PROC_PIDTASKALLINFO, 0, &info, sizeof(info));
		if (got != sizeof(info)) return got <= 0 && errno == ESRCH ? 0 : -1;
		memset(row, 0, sizeof(*row));
		row->pid = (int)info.pbsd.pbi_pid;
		row->ppid = (int)info.pbsd.pbi_ppid;
		row->start_sec = info.pbsd.pbi_start_tvsec;
		row->start_usec = info.pbsd.pbi_start_tvusec;
		row->cpu_ns = info.ptinfo.pti_total_user + info.ptinfo.pti_total_system;
		row->rss = info.ptinfo.pti_resident_size;
		if (proc_pidpath(row->pid, row->path, sizeof(row->path)) <= 0) {
			strlcpy(row->path, info.pbsd.pbi_name, sizeof(row->path));
		}
		yomux_command(row->pid, row->path, row->command, sizeof(row->command));
		struct proc_vnodepathinfo vnode;
		if (proc_pidinfo(row->pid, PROC_PIDVNODEPATHINFO, 0, &vnode, sizeof(vnode)) == sizeof(vnode))
			strlcpy(row->cwd, vnode.pvi_cdir.vip_path, sizeof(row->cwd));
		return 1;
}

static int yomux_logical_cpus(void) {
	int count = 0;
	size_t size = sizeof(count);
	if (sysctlbyname("hw.logicalcpu", &count, &size, NULL, 0) != 0) return 0;
	return count;
}
*/
import "C"

import (
	"errors"
	"fmt"
	"os"
	"sort"
	"sync"
	"time"
	"unsafe"
)

const maxDarwinProcessEntries = 250_000
const darwinProcessSnapshotTTL = 500 * time.Millisecond

type darwinSnapshot struct {
	at          time.Time
	rows        []DarwinProcess
	complete    bool
	total, idle uint64
}

var currentDarwinSnapshot struct {
	sync.Mutex
	value darwinSnapshot
}

func DarwinProcessSnapshot() ([]DarwinProcess, bool) {
	snapshot := loadDarwinSnapshot()
	return append([]DarwinProcess(nil), snapshot.rows...), snapshot.complete
}

func loadDarwinSnapshot() darwinSnapshot {
	currentDarwinSnapshot.Lock()
	defer currentDarwinSnapshot.Unlock()
	if time.Since(currentDarwinSnapshot.value.at) >= 0 && time.Since(currentDarwinSnapshot.value.at) < darwinProcessSnapshotTTL {
		return currentDarwinSnapshot.value
	}
	value := readDarwinSnapshot()
	currentDarwinSnapshot.value = value
	return value
}

func readDarwinSnapshot() darwinSnapshot {
	var pids *C.pid_t
	complete := C.int(0)
	n := int(C.yomux_pids(&pids, maxDarwinProcessEntries, &complete))
	defer C.free(unsafe.Pointer(pids))
	if n < 0 {
		n = 0
		complete = 0
	}
	processIDs := make([]int, 0, n)
	for _, pid := range unsafe.Slice(pids, n) {
		processIDs = append(processIDs, int(pid))
	}
	result, censusComplete := assembleDarwinProcessSnapshot(processIDs, complete != 0, func(pid int) (DarwinProcess, error) {
		var row C.struct_yomux_process
		status := C.yomux_process(C.int(pid), &row)
		if status == 0 {
			return DarwinProcess{}, os.ErrNotExist
		}
		if status < 0 {
			return DarwinProcess{}, errors.New("Darwin process observation unavailable")
		}
		path := C.GoString(&row.path[0])
		start := fmt.Sprintf("%d:%d", uint64(row.start_sec), uint64(row.start_usec))
		startedAt := float64(uint64(row.start_sec)) + float64(uint64(row.start_usec))/1e6
		return DarwinProcess{PID: int(row.pid), PPID: int(row.ppid), Identity: fmt.Sprintf("%d:%s", int(row.pid), start), Command: C.GoString(&row.command[0]), Executable: path, Directory: C.GoString(&row.cwd[0]), CPUSeconds: float64(uint64(row.cpu_ns)) / 1e9, RSSBytes: uint64(row.rss), StartedAt: startedAt}, nil
	})
	var total, idle C.uint64_t
	C.yomux_host_cpu(&total, &idle)
	return darwinSnapshot{at: time.Now(), rows: result, complete: censusComplete, total: uint64(total), idle: uint64(idle)}
}

func darwinSystemLogicalCPUs() int { return int(C.yomux_logical_cpus()) }

func darwinHostCPU() (uint64, uint64, bool) {
	snapshot := loadDarwinSnapshot()
	return snapshot.total, snapshot.idle, snapshot.total > 0
}

func ProcessOpenFiles(pid int) []string {
	if pid <= 0 {
		return nil
	}
	paths := make([]*C.char, 4096)
	n := int(C.yomux_open_files(C.int(pid), (**C.char)(unsafe.Pointer(&paths[0])), C.int(len(paths))))
	if n <= 0 {
		return nil
	}
	result := make([]string, 0, n)
	for i := 0; i < n; i++ {
		result = append(result, C.GoString(paths[i]))
		C.free(unsafe.Pointer(paths[i]))
	}
	return result
}

func darwinProcessIdentity(pid int) (DarwinProcess, bool) {
	if pid <= 0 {
		return DarwinProcess{}, false
	}
	rows := loadDarwinSnapshot().rows
	index := sort.Search(len(rows), func(i int) bool { return rows[i].PID >= pid })
	if index >= len(rows) || rows[index].PID != pid {
		return DarwinProcess{}, false
	}
	return rows[index], true
}

func DarwinProcessMetricsForPID(pid int) (ProcessCensusRow, bool) {
	row, ok := darwinProcessIdentity(pid)
	if !ok {
		return ProcessCensusRow{}, false
	}
	return row.censusRow(), true
}
