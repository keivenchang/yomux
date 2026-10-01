package stats

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/testsupport"
	_ "github.com/mattn/go-sqlite3"
)

func TestWriterLeaseRejectsSecondOwnerAndReleasesOnClose(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats-v9.sqlite3")
	first, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := OpenWriter(context.Background(), path); err == nil {
		t.Fatal("second writer acquired the singleton lease")
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	second, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := second.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestWriterLeaseCrossProcessOwnership(t *testing.T) {
	if os.Getenv("YOLOMUX_WRITER_LEASE_HELPER") == "1" {
		path := os.Getenv("YOLOMUX_WRITER_LEASE_PATH")
		writer, err := OpenWriter(context.Background(), path)
		if err != nil {
			fmt.Fprintln(os.Stdout, "helper open failed:", err)
			os.Exit(2)
		}
		fmt.Fprintln(os.Stdout, "ready")
		_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
		if err := writer.Close(); err != nil {
			fmt.Fprintln(os.Stdout, "helper close failed:", err)
			os.Exit(3)
		}
		os.Exit(0)
	}

	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	command := exec.Command(os.Args[0], "-test.run=^TestWriterLeaseCrossProcessOwnership$")
	command.Env = testsupport.SyntheticEnvironment("YOLOMUX_WRITER_LEASE_HELPER=1", "YOLOMUX_WRITER_LEASE_PATH="+path)
	stdout, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdin, err := command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	cleanup := true
	t.Cleanup(func() {
		if cleanup {
			_ = stdin.Close()
			_ = command.Process.Kill()
			_ = command.Wait()
		}
	})
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil {
		t.Fatalf("helper did not announce ownership: %v", err)
	}
	if line != "ready\n" {
		t.Fatalf("helper announcement = %q", line)
	}
	if _, err := OpenWriter(context.Background(), path); err == nil {
		t.Fatal("second process acquired the writer lease")
	}
	if _, err := fmt.Fprintln(stdin, "release"); err != nil {
		t.Fatal(err)
	}
	if err := stdin.Close(); err != nil {
		t.Fatal(err)
	}
	if err := command.Wait(); err != nil {
		t.Fatalf("helper exited with ownership error: %v", err)
	}
	cleanup = false
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatalf("lease was not released after helper exit: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestWriterLeaseCrossLanguageOwnership(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats-v9.sqlite3")
	repoRoot, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	repoRoot = filepath.Join(repoRoot, "..", "..")
	python := `
import os, sys
from pathlib import Path
from yomux_lib.stats_current.storage import Store
store = Store.open(Path(os.environ["YOLOMUX_WRITER_LEASE_PATH"]))
print("ready", flush=True)
sys.stdin.readline()
store.close()
`
	command := exec.Command("python3", "-c", python)
	command.Dir = repoRoot
	command.Env = testsupport.SyntheticEnvironment("PYTHONPATH="+repoRoot, "YOLOMUX_WRITER_LEASE_PATH="+path)
	stdout, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdin, err := command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	cleanup := true
	t.Cleanup(func() {
		if cleanup {
			_ = stdin.Close()
			_ = command.Process.Kill()
			_ = command.Wait()
		}
	})
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil {
		t.Fatalf("Python helper did not announce ownership: %v", err)
	}
	if line != "ready\n" {
		t.Fatalf("Python helper announcement = %q", line)
	}
	if _, err := OpenWriter(context.Background(), path); err == nil {
		t.Fatal("Go acquired the writer lease held by Python")
	}
	if _, err := fmt.Fprintln(stdin, "release"); err != nil {
		t.Fatal(err)
	}
	if err := stdin.Close(); err != nil {
		t.Fatal(err)
	}
	if err := command.Wait(); err != nil {
		t.Fatalf("Python helper exited with ownership error: %v", err)
	}
	cleanup = false
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatalf("Go could not acquire lease after Python released it: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestWriterLeasePythonRejectsGoOwner(t *testing.T) {
	if os.Getenv("YOLOMUX_WRITER_LEASE_HELPER") == "1" {
		path := os.Getenv("YOLOMUX_WRITER_LEASE_PATH")
		writer, err := OpenWriter(context.Background(), path)
		if err != nil {
			fmt.Fprintln(os.Stdout, "helper open failed:", err)
			os.Exit(2)
		}
		fmt.Fprintln(os.Stdout, "ready")
		_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
		if err := writer.Close(); err != nil {
			fmt.Fprintln(os.Stdout, "helper close failed:", err)
			os.Exit(3)
		}
		os.Exit(0)
	}
	path := filepath.Join(t.TempDir(), "stats-v9.sqlite3")
	command := exec.Command(os.Args[0], "-test.run=^TestWriterLeasePythonRejectsGoOwner$")
	command.Env = testsupport.SyntheticEnvironment("YOLOMUX_WRITER_LEASE_HELPER=1", "YOLOMUX_WRITER_LEASE_PATH="+path)
	stdout, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdin, err := command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	cleanup := true
	t.Cleanup(func() {
		if cleanup {
			_ = stdin.Close()
			_ = command.Process.Kill()
			_ = command.Wait()
		}
	})
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil || line != "ready\n" {
		t.Fatalf("Go helper did not announce ownership: line=%q err=%v", line, err)
	}
	repoRoot, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	repoRoot = filepath.Join(repoRoot, "..", "..")
	python := `import os
from pathlib import Path
from yomux_lib.stats_current.storage import Store
try:
    Store.open(Path(os.environ["YOLOMUX_WRITER_LEASE_PATH"]))
except Exception as error:
    print(type(error).__name__ + ": " + str(error), flush=True)
else:
    raise SystemExit("Python acquired the Go-owned writer lease")
`
	probe := exec.Command("python3", "-c", python)
	probe.Dir = repoRoot
	probe.Env = testsupport.SyntheticEnvironment("PYTHONPATH="+repoRoot, "YOLOMUX_WRITER_LEASE_PATH="+path, "YOLOMUX_STATS_GO_WRITER=")
	output, err := probe.CombinedOutput()
	if err != nil {
		t.Fatalf("Python lease probe failed: %v output=%s", err, output)
	}
	if string(output) != "StatsCurrentError: YO!stats writer is already owned\n" {
		t.Fatalf("Python lease refusal = %q", output)
	}
	if _, err := fmt.Fprintln(stdin, "release"); err != nil {
		t.Fatal(err)
	}
	if err := stdin.Close(); err != nil {
		t.Fatal(err)
	}
	if err := command.Wait(); err != nil {
		t.Fatalf("Go helper exited with ownership error: %v", err)
	}
	cleanup = false
	acquire := exec.Command("python3", "-c", `import os
from pathlib import Path
from yomux_lib.stats_current.storage import Store
store = Store.open(Path(os.environ["YOLOMUX_WRITER_LEASE_PATH"]))
print("ready", flush=True)
store.close()
`)
	acquire.Dir = repoRoot
	acquire.Env = testsupport.SyntheticEnvironment("PYTHONPATH="+repoRoot, "YOLOMUX_WRITER_LEASE_PATH="+path, "YOLOMUX_STATS_GO_WRITER=")
	output, err = acquire.CombinedOutput()
	if err != nil || string(output) != "ready\n" {
		t.Fatalf("Python did not acquire lease after Go release: err=%v output=%q", err, output)
	}
}

func TestWriterPreflightReadsSchemaCommittedInWALAfterCrash(t *testing.T) {
	if os.Getenv("YOLOMUX_WRITER_CRASH_HELPER") == "1" {
		path := os.Getenv("YOLOMUX_WRITER_CRASH_PATH")
		if _, err := OpenWriter(context.Background(), path); err != nil {
			fmt.Fprintln(os.Stdout, "helper open failed:", err)
			os.Exit(2)
		}
		// Exit without Writer.Close to leave the just-created schema in SQLite's
		// WAL, matching a process crash during startup/restart.
		fmt.Fprintln(os.Stdout, "ready")
		os.Exit(0)
	}

	path := filepath.Join(t.TempDir(), "stats-v9.sqlite3")
	command := exec.Command(os.Args[0], "-test.run=^TestWriterPreflightReadsSchemaCommittedInWALAfterCrash$")
	command.Env = testsupport.SyntheticEnvironment("YOLOMUX_WRITER_CRASH_HELPER=1", "YOLOMUX_WRITER_CRASH_PATH="+path)
	stdout, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil {
		t.Fatalf("crash helper did not announce readiness: %v", err)
	}
	if line != "ready\n" {
		t.Fatalf("crash helper announcement = %q", line)
	}
	if err := command.Wait(); err != nil {
		t.Fatalf("crash helper exited with startup error: %v", err)
	}
	restarted, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatalf("writer rejected a valid WAL-backed schema after crash: %v", err)
	}
	if err := restarted.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestWriterLeaseResolvesParentDirectoryAliases(t *testing.T) {
	realDir := filepath.Join(t.TempDir(), "real")
	if err := os.Mkdir(realDir, 0o700); err != nil {
		t.Fatal(err)
	}
	aliasDir := filepath.Join(filepath.Dir(realDir), "alias")
	if err := os.Symlink(realDir, aliasDir); err != nil {
		t.Fatal(err)
	}
	realPath := filepath.Join(realDir, "stats.sqlite3")
	aliasPath := filepath.Join(aliasDir, "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), realPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := OpenWriter(context.Background(), aliasPath); err == nil {
		t.Fatal("writer acquired an aliased path while the canonical path was owned")
	}
}

func TestWriterFenceRejectsNewerBuildBeforeSQLiteMutation(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "stats.sqlite3")
	fence := writerFence{
		ApplicationID: ApplicationID, DatabaseFilename: filepath.Base(path),
		SchemaVersion: SchemaVersion, MinimumWriterProtocol: writerProtocol,
		MinimumWriterBuild: writerBuild + 1,
	}
	data, err := json.Marshal(fence)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, writerFenceFilename), data, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := OpenWriter(context.Background(), path); err == nil {
		t.Fatal("newer writer fence was accepted")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("writer touched database after fence rejection: %v", err)
	}
}

func TestWriterSchemaPreflightRejectsWithoutTouchingDatabaseOrWAL(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	// Make the existing file incompatible after it has a valid current fence.
	db, err := sql.Open("sqlite3", writerURI(path))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("PRAGMA journal_mode=DELETE"); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("PRAGMA user_version=10"); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	type fileState struct {
		data  []byte
		mode  os.FileMode
		mtime time.Time
	}
	state := make(map[string]fileState)
	for _, suffix := range []string{"", "-wal", "-shm"} {
		name := path + suffix
		info, statErr := os.Stat(name)
		if os.IsNotExist(statErr) {
			continue
		}
		if statErr != nil {
			t.Fatal(statErr)
		}
		data, readErr := os.ReadFile(name)
		if readErr != nil {
			t.Fatal(readErr)
		}
		state[suffix] = fileState{data: data, mode: info.Mode(), mtime: info.ModTime()}
	}
	if _, err := OpenWriter(context.Background(), path); err == nil {
		t.Fatal("incompatible schema was accepted")
	}
	for suffix, before := range state {
		name := path + suffix
		afterInfo, statErr := os.Stat(name)
		if statErr != nil {
			t.Fatalf("preflight removed %s: %v", name, statErr)
		}
		after, readErr := os.ReadFile(name)
		if readErr != nil {
			t.Fatal(readErr)
		}
		if string(after) != string(before.data) || afterInfo.Mode() != before.mode || !afterInfo.ModTime().Equal(before.mtime) {
			t.Fatalf("preflight mutated %s", name)
		}
	}
}

func TestOpenWriterQuarantinesPhysicallyCorruptDatabaseAndSidecars(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	parts := map[string][]byte{
		"":         []byte("not a sqlite database"),
		"-wal":     []byte("synthetic wal bytes"),
		"-shm":     []byte("synthetic shm bytes"),
		"-journal": []byte("synthetic journal bytes"),
	}
	for suffix, data := range parts {
		if err := os.WriteFile(path+suffix, data, 0o600); err != nil {
			t.Fatalf("write corrupt %s: %v", suffix, err)
		}
	}

	recovered, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatalf("physically corrupt database was not recovered: %v", err)
	}
	defer recovered.Close()
	if name := recovered.CorruptRecoveryName(); !strings.HasPrefix(name, "stats.sqlite3.corrupt-") {
		t.Fatalf("corrupt recovery name = %q", name)
	}

	quarantined, err := filepath.Glob(path + ".corrupt-*")
	if err != nil {
		t.Fatal(err)
	}
	if len(quarantined) != len(parts) {
		t.Fatalf("quarantined files = %v, want %d files", quarantined, len(parts))
	}
	var quarantineBase string
	for _, candidate := range quarantined {
		if !strings.HasSuffix(candidate, "-wal") && !strings.HasSuffix(candidate, "-shm") && !strings.HasSuffix(candidate, "-journal") {
			quarantineBase = candidate
			break
		}
	}
	if quarantineBase == "" {
		t.Fatalf("quarantine base file missing: %v", quarantined)
	}
	for suffix, want := range parts {
		got, err := os.ReadFile(quarantineBase + suffix)
		if err != nil {
			t.Fatal(err)
		}
		if string(got) != string(want) {
			t.Fatalf("quarantine %q = %q, want %q", suffix, got, want)
		}
	}
	if info, err := os.Stat(path); err != nil || info.Size() == 0 {
		t.Fatalf("recovered database was not recreated: info=%+v err=%v", info, err)
	}
}

func TestOpenWriterQuarantinesZeroByteDatabaseAndSidecars(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	parts := map[string][]byte{
		"":         nil,
		"-wal":     []byte("synthetic wal bytes"),
		"-shm":     []byte("synthetic shm bytes"),
		"-journal": []byte("synthetic journal bytes"),
	}
	for suffix, data := range parts {
		if err := os.WriteFile(path+suffix, data, 0o600); err != nil {
			t.Fatalf("write zero-byte fixture %s: %v", suffix, err)
		}
	}

	recovered, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatalf("zero-byte database was not recovered: %v", err)
	}
	defer recovered.Close()
	if name := recovered.CorruptRecoveryName(); !strings.HasPrefix(name, "stats.sqlite3.corrupt-") {
		t.Fatalf("corrupt recovery name = %q", name)
	}

	quarantined, err := filepath.Glob(path + ".corrupt-*")
	if err != nil {
		t.Fatal(err)
	}
	if len(quarantined) != len(parts) {
		t.Fatalf("quarantined files = %v, want %d files", quarantined, len(parts))
	}
	var quarantineBase string
	for _, candidate := range quarantined {
		if !strings.HasSuffix(candidate, "-wal") && !strings.HasSuffix(candidate, "-shm") && !strings.HasSuffix(candidate, "-journal") {
			quarantineBase = candidate
			break
		}
	}
	if quarantineBase == "" {
		t.Fatalf("quarantine base file missing: %v", quarantined)
	}
	for suffix, want := range parts {
		got, err := os.ReadFile(quarantineBase + suffix)
		if err != nil {
			t.Fatal(err)
		}
		if string(got) != string(want) {
			t.Fatalf("quarantine %q = %q, want %q", suffix, got, want)
		}
	}
	if info, err := os.Stat(path); err != nil || info.Size() == 0 {
		t.Fatalf("recovered database was not recreated: info=%+v err=%v", info, err)
	}
}

func TestOpenWriterQuarantinesZeroByteDatabaseWithValidWAL(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	python := `
import os
import sqlite3
import sys

database = sqlite3.connect(sys.argv[1])
database.execute("PRAGMA journal_mode=WAL")
database.execute("CREATE TABLE synthetic_facts (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
database.execute("INSERT INTO synthetic_facts(value) VALUES ('durable-before-crash')")
database.commit()
os._exit(0)
`
	command := exec.Command("python3", "-c", python, path)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("create synthetic WAL fixture: %v, output=%s", err, output)
	}
	wal, err := os.ReadFile(path + "-wal")
	if err != nil {
		t.Fatalf("read synthetic WAL fixture: %v", err)
	}
	if len(wal) < 32 {
		t.Fatalf("synthetic WAL fixture is too small: %d", len(wal))
	}
	if err := os.Truncate(path, 0); err != nil {
		t.Fatal(err)
	}

	recovered, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatalf("zero-byte WAL database was not recovered: %v", err)
	}
	defer recovered.Close()
	if name := recovered.CorruptRecoveryName(); !strings.HasPrefix(name, "stats.sqlite3.corrupt-") {
		t.Fatalf("corrupt recovery name = %q", name)
	}
	quarantined, err := filepath.Glob(path + ".corrupt-*-wal")
	if err != nil {
		t.Fatal(err)
	}
	if len(quarantined) != 1 {
		t.Fatalf("quarantined WAL files = %v, want one", quarantined)
	}
	preserved, err := os.ReadFile(quarantined[0])
	if err != nil {
		t.Fatal(err)
	}
	if string(preserved) != string(wal) {
		t.Fatal("valid WAL bytes were not preserved verbatim")
	}
	if info, err := os.Stat(path); err != nil || info.Size() == 0 {
		t.Fatalf("recovered database was not recreated: info=%+v err=%v", info, err)
	}
}

func TestOpenWriterQuarantinesOrphanedDatabaseSidecars(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	parts := map[string][]byte{
		"-wal":     []byte("orphaned synthetic wal"),
		"-shm":     []byte("orphaned synthetic shm"),
		"-journal": []byte("orphaned synthetic journal"),
	}
	for suffix, data := range parts {
		if err := os.WriteFile(path+suffix, data, 0o600); err != nil {
			t.Fatalf("write orphaned sidecar %s: %v", suffix, err)
		}
	}

	recovered, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatalf("orphaned sidecars were not recovered: %v", err)
	}
	defer recovered.Close()
	if name := recovered.CorruptRecoveryName(); !strings.HasPrefix(name, "stats.sqlite3.corrupt-") {
		t.Fatalf("corrupt recovery name = %q", name)
	}
	quarantined, err := filepath.Glob(path + ".corrupt-*")
	if err != nil {
		t.Fatal(err)
	}
	if len(quarantined) != len(parts) {
		t.Fatalf("quarantined files = %v, want %d files", quarantined, len(parts))
	}
	var quarantineBase string
	for _, candidate := range quarantined {
		if strings.HasSuffix(candidate, "-wal") {
			quarantineBase = strings.TrimSuffix(candidate, "-wal")
			break
		}
	}
	if quarantineBase == "" {
		t.Fatalf("quarantine base path missing: %v", quarantined)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("recovered database was not created: %v", err)
	}
	for suffix, want := range parts {
		got, err := os.ReadFile(quarantineBase + suffix)
		if err != nil {
			t.Fatal(err)
		}
		if string(got) != string(want) {
			t.Fatalf("quarantine %q = %q, want %q", suffix, got, want)
		}
	}
}

func TestOpenWriterQuarantinesCorruptSQLitePage(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, writerBatch()); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	damaged, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(damaged) < 8192 {
		t.Fatalf("synthetic database too small for page corruption: %d", len(damaged))
	}
	for index := len(damaged) / 2; index < len(damaged)/2+4096 && index < len(damaged); index++ {
		damaged[index] = 0xff
	}
	if err := os.WriteFile(path, damaged, 0o600); err != nil {
		t.Fatal(err)
	}

	recovered, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatalf("corrupt SQLite page was not recovered: %v", err)
	}
	defer recovered.Close()
	quarantined, err := filepath.Glob(path + ".corrupt-*")
	if err != nil {
		t.Fatal(err)
	}
	if len(quarantined) < 1 {
		t.Fatalf("quarantined files = %v, want at least one", quarantined)
	}
	var quarantineBase string
	for _, candidate := range quarantined {
		if !strings.HasSuffix(candidate, "-wal") && !strings.HasSuffix(candidate, "-shm") && !strings.HasSuffix(candidate, "-journal") {
			quarantineBase = candidate
			break
		}
	}
	if quarantineBase == "" {
		t.Fatalf("quarantine base file missing: %v", quarantined)
	}
	preserved, err := os.ReadFile(quarantineBase)
	if err != nil {
		t.Fatal(err)
	}
	if string(preserved) != string(damaged) {
		t.Fatal("corrupt SQLite bytes were not preserved verbatim")
	}
}

func TestOpenWriterPermissionFailureDoesNotQuarantine(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root can write a read-only fixture")
	}
	ctx := context.Background()
	root := t.TempDir()
	path := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	original, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	originalMode := func() os.FileMode {
		info, statErr := os.Stat(path)
		if statErr != nil {
			t.Fatal(statErr)
		}
		return info.Mode().Perm()
	}()
	originalDirectoryMode := func() os.FileMode {
		info, statErr := os.Stat(root)
		if statErr != nil {
			t.Fatal(statErr)
		}
		return info.Mode().Perm()
	}()
	if err := os.Chmod(path, 0o400); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(root, 0o500); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = os.Chmod(path, originalMode)
		_ = os.Chmod(root, originalDirectoryMode)
	}()

	if _, err := OpenWriter(ctx, path); err == nil {
		t.Fatal("read-only database was accepted")
	}
	if quarantined, globErr := filepath.Glob(path + ".corrupt-*"); globErr != nil {
		t.Fatal(globErr)
	} else if len(quarantined) != 0 {
		t.Fatalf("permission failure quarantined files: %v", quarantined)
	}
	current, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(current) != string(original) {
		t.Fatal("permission failure changed database bytes")
	}
}

func TestQuarantineRollsBackWhenDirectorySyncFails(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	parts := map[string][]byte{
		"":         []byte("synthetic corrupt database"),
		"-wal":     []byte("synthetic wal"),
		"-shm":     []byte("synthetic shm"),
		"-journal": []byte("synthetic journal"),
	}
	for suffix, data := range parts {
		if err := os.WriteFile(path+suffix, data, 0o600); err != nil {
			t.Fatalf("write quarantine fixture %s: %v", suffix, err)
		}
	}

	quarantine, err := quarantineCorruptDatabaseWithSync(path, func(string) error {
		return errors.New("synthetic directory sync failure")
	})
	if err == nil || !strings.Contains(err.Error(), "synthetic directory sync failure") {
		t.Fatalf("directory sync failure = %v", err)
	}
	if quarantine != "" {
		t.Fatalf("failed quarantine returned artifact %q", quarantine)
	}
	for suffix, want := range parts {
		got, readErr := os.ReadFile(path + suffix)
		if readErr != nil {
			t.Fatalf("rollback lost %q: %v", suffix, readErr)
		}
		if string(got) != string(want) {
			t.Fatalf("rollback changed %q: got %q want %q", suffix, got, want)
		}
	}
	if quarantined, globErr := filepath.Glob(path + ".corrupt-*"); globErr != nil {
		t.Fatal(globErr)
	} else if len(quarantined) != 0 {
		t.Fatalf("failed quarantine left artifacts: %v", quarantined)
	}
}
