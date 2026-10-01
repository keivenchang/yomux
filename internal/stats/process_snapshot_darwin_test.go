// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package stats

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestDarwinNativeSnapshotIncludesCurrentProcessAndOpenFiles(t *testing.T) {
	file, err := os.CreateTemp(t.TempDir(), "codex-rollout-*.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	command := exec.Command("/bin/cat")
	command.Stdin = reader
	if err := command.Start(); err != nil {
		_ = reader.Close()
		_ = writer.Close()
		t.Fatal(err)
	}
	_ = reader.Close()
	defer func() {
		_ = writer.Close()
		_ = command.Process.Kill()
		_ = command.Wait()
	}()
	// Earlier tests may have primed the shared 500ms snapshot before this short-lived child exists.
	currentDarwinSnapshot.Lock()
	currentDarwinSnapshot.value = darwinSnapshot{}
	currentDarwinSnapshot.Unlock()
	rows, complete := DarwinProcessSnapshot()
	if len(rows) == 0 {
		t.Fatal("native process snapshot contains no observable processes")
	}
	var self *DarwinProcess
	for i := range rows {
		if rows[i].PID == os.Getpid() {
			self = &rows[i]
			break
		}
	}
	if self == nil {
		t.Fatal("native process snapshot omitted the current process")
	}
	var child *DarwinProcess
	for i := range rows {
		if rows[i].PID == command.Process.Pid {
			child = &rows[i]
			break
		}
	}
	if child == nil || !strings.Contains(child.Command, "/bin/cat") {
		t.Fatalf("one-argument process command snapshot = %#v, want /bin/cat", child)
	}
	if self.Identity == "" || self.Executable == "" || self.StartedAt <= 0 || self.RSSBytes == 0 || self.CPUSeconds < 0 {
		t.Fatalf("current process snapshot is incomplete: %#v", *self)
	}
	found := false
	for _, path := range ProcessOpenFiles(os.Getpid()) {
		if strings.Contains(path, file.Name()) {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("libproc open-file snapshot omitted %q", file.Name())
	}
	if total, idle, ok := darwinHostCPU(); !ok || total == 0 || idle > total {
		t.Fatalf("Mach host CPU counters = total:%d idle:%d available:%v", total, idle, ok)
	}
	sources, ok := NewHostSources().(*DarwinHostSources)
	if !ok {
		t.Fatalf("Darwin host sources unavailable: %T", NewHostSources())
	}
	if sources.SystemLogicalCPUs() < 1 {
		t.Fatal("Darwin logical CPU count unavailable")
	}
	cpu, err := sources.CPU(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if cpu.ProcessTicksPerSecond != darwinProcessTicksPerSecond || cpu.Processes[self.Identity].Identity != self.Identity || cpu.ProcessCensusIncomplete == complete {
		t.Fatalf("Darwin CPU source units/process sample = %+v, want ns/s and self identity %q", cpu, self.Identity)
	}
	memory, processRows, err := sources.Memory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if memory.CapacityBytes == 0 || memory.UsedBytes == 0 || memory.ProcessCensusIncomplete == complete {
		t.Fatalf("Darwin memory source = %+v, want host memory and snapshot completeness=%t", memory, complete)
	}
	for _, row := range processRows {
		if row.Identity == self.Identity && row.RSSBytes == self.RSSBytes {
			return
		}
	}
	t.Fatalf("Darwin memory source omitted current process %q", self.Identity)
}

func TestDarwinCodexRolloutsFollowTheirOwnOpenProcessFiles(t *testing.T) {
	root := t.TempDir()
	paths := []string{filepath.Join(root, "rollout-one.jsonl"), filepath.Join(root, "rollout-two.jsonl")}
	processes := make([]*exec.Cmd, 0, len(paths))
	for _, path := range paths {
		file, err := os.Create(path)
		if err != nil {
			t.Fatal(err)
		}
		command := exec.Command("/bin/sleep", "30")
		command.ExtraFiles = []*os.File{file}
		if err := command.Start(); err != nil {
			_ = file.Close()
			t.Fatal(err)
		}
		_ = file.Close()
		processes = append(processes, command)
	}
	t.Cleanup(func() {
		for _, command := range processes {
			_ = command.Process.Kill()
			_ = command.Wait()
		}
	})
	resolvedPaths := make([]string, len(paths))
	for i, path := range paths {
		resolvedPath, err := filepath.EvalSymlinks(path)
		if err != nil {
			t.Fatal(err)
		}
		resolvedPaths[i] = resolvedPath
	}
	for i, command := range processes {
		got := CodexTranscriptFromProcess(command.Process.Pid, root)
		if got != resolvedPaths[i] {
			t.Fatalf("process %d rollout = %q, want its own open file %q", command.Process.Pid, got, resolvedPaths[i])
		}
	}
}
