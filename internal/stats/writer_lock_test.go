package stats

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/keivenchang/yolomux/internal/testsupport"
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
from yolomux_lib.stats_current.storage import Store
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
from yolomux_lib.stats_current.storage import Store
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
from yolomux_lib.stats_current.storage import Store
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
