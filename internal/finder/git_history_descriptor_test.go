// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/go-git/go-billy/v5"
)

func TestMain(m *testing.M) {
	if len(os.Args) == 2 && os.Args[1] == HistoryPrototypeHelperFlag {
		if err := RunHistoryPrototypeHelper(os.Stdin, os.Stdout); err != nil {
			os.Exit(2)
		}
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func prototypeFS(t *testing.T, root string) *descriptorGitFS {
	t.Helper()
	f, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = f.Close() })
	return &descriptorGitFS{git: f, common: f, objects: f}
}

func TestDescriptorGitFSBoundariesAndReadOnly(t *testing.T) {
	root, outside := t.TempDir(), t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "secret"), []byte("outside"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}
	fs := prototypeFS(t, root)
	for _, name := range []string{"../secret", outside + "/secret", "link/secret", "link"} {
		if f, err := fs.Open(name); err == nil {
			_ = f.Close()
			t.Fatalf("opened %s", name)
		}
		if _, err := fs.Stat(name); err == nil {
			t.Fatalf("stat accepted %s", name)
		}
	}
	for _, operation := range []func() error{
		func() error { _, e := fs.Create("new"); return e }, func() error { _, e := fs.OpenFile("new", os.O_CREATE, 0600); return e },
		func() error { _, e := fs.TempFile("", "new"); return e }, func() error { return fs.Remove("link") },
		func() error { return fs.Rename("link", "new") }, func() error { return fs.MkdirAll("new", 0700) }, func() error { return fs.Symlink(outside, "new") },
	} {
		if err := operation(); !errors.Is(err, billy.ErrReadOnly) {
			t.Fatalf("mutation: %v", err)
		}
	}
	if _, err := fs.Chroot(".."); err == nil {
		t.Fatal("Chroot escaped")
	}
	if err := os.WriteFile(filepath.Join(root, "file"), []byte("inside"), 0600); err != nil {
		t.Fatal(err)
	}
	f, err := fs.Open("file")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err = f.Write([]byte("changed")); !errors.Is(err, billy.ErrReadOnly) {
		t.Fatal(err)
	}
	if err = f.Truncate(0); !errors.Is(err, billy.ErrReadOnly) {
		t.Fatal(err)
	}
}

func TestDescriptorGitFSPinnedRootSwapAndRestore(t *testing.T) {
	parent, outside := t.TempDir(), t.TempDir()
	root := filepath.Join(parent, "root")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	for path, value := range map[string]string{filepath.Join(root, "value"): "authorized", filepath.Join(outside, "value"): "outside"} {
		if err := os.WriteFile(path, []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
	}
	fs := prototypeFS(t, root)
	if err := os.Rename(root, root+".saved"); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, root); err != nil {
		t.Fatal(err)
	}
	data, err := descriptorGitRead(fs, "value", 100)
	if err != nil || string(data) != "authorized" {
		t.Fatalf("swapped root: %q %v", data, err)
	}
	if err := os.Remove(root); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(root+".saved", root); err != nil {
		t.Fatal(err)
	}
	data, err = descriptorGitRead(fs, "value", 100)
	if err != nil || string(data) != "authorized" {
		t.Fatalf("restored root: %q %v", data, err)
	}
}

func TestDescriptorGitFSDescendantSwapAndRestore(t *testing.T) {
	root, outside := t.TempDir(), t.TempDir()
	dir := filepath.Join(root, "refs")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	for path, value := range map[string]string{filepath.Join(dir, "value"): "authorized", filepath.Join(outside, "value"): "outside"} {
		if err := os.WriteFile(path, []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
	}
	fs := prototypeFS(t, root)
	// Hold the descendant descriptor at the same boundary used by component
	// traversal. Restoring the name cannot redirect an already-open descendant.
	pinned, err := fs.open("refs")
	if err != nil {
		t.Fatal(err)
	}
	defer pinned.Close()
	if err := os.Rename(dir, dir+".saved"); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, dir); err != nil {
		t.Fatal(err)
	}
	if f, err := fs.Open("refs/value"); err == nil {
		f.Close()
		t.Fatal("followed swapped descendant")
	}
	child := &descriptorGitFS{git: pinned, common: pinned, objects: pinned}
	data, err := descriptorGitRead(child, "value", 100)
	if err != nil || string(data) != "authorized" {
		t.Fatalf("pinned descendant: %q %v", data, err)
	}
	if err := os.Remove(dir); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(dir+".saved", dir); err != nil {
		t.Fatal(err)
	}
	data, err = descriptorGitRead(fs, "refs/value", 100)
	if err != nil || string(data) != "authorized" {
		t.Fatalf("restored descendant: %q %v", data, err)
	}
}

func TestDescriptorGitHelperCancellation(t *testing.T) {
	fs := prototypeFS(t, t.TempDir())
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	start := time.Now()
	_, err := runHistoryPrototypeProcess(ctx, os.Args[0], &gitNamespace{gitDir: fs.git, commonDir: fs.common, objects: fs.objects}, HistoryPrototypeRequest{Limit: 50})
	if !errors.Is(err, context.Canceled) || time.Since(start) > time.Second {
		t.Fatalf("cancellation: %v", err)
	}
}

func TestDescriptorGitRunningHelperKilledAndReaped(t *testing.T) {
	fs := prototypeFS(t, t.TempDir())
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	command, err := newHistoryPrototypeCommand(ctx, os.Args[0], &gitNamespace{gitDir: fs.git, commonDir: fs.common, objects: fs.objects}, HistoryPrototypeRequest{Limit: 50})
	if err != nil {
		t.Fatal(err)
	}
	// A pipe descriptor inherited as stdin blocks the actual helper in its
	// bounded request read, without spawning any shell or descendant process.
	input, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	defer writer.Close()
	command.Stdin = input
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	pid := command.Process.Pid
	if err := syscall.Kill(pid, 0); err != nil {
		cancel()
		_ = command.Wait()
		t.Fatal(err)
	}
	cancel()
	if err := command.Wait(); err == nil {
		t.Fatal("cancelled running helper succeeded")
	}
	if command.ProcessState == nil {
		t.Fatal("helper was not waited")
	}
	if err := syscall.Kill(pid, 0); !errors.Is(err, syscall.ESRCH) {
		t.Fatalf("helper %d remains: %v", pid, err)
	}
	if _, err := fs.git.Stat(); err != nil {
		t.Fatalf("parent descriptor closed: %v", err)
	}
}

func TestDescriptorGitHelperRejectsMalformedBoundedRequest(t *testing.T) {
	fs := prototypeFS(t, t.TempDir())
	for _, input := range []string{`{"limit":50,"path":"/outside"}`, `{"limit":50} {}`, string(bytes.Repeat([]byte("x"), 4097))} {
		command, err := newHistoryPrototypeCommand(context.Background(), os.Args[0], &gitNamespace{gitDir: fs.git, commonDir: fs.common, objects: fs.objects}, HistoryPrototypeRequest{Limit: 50})
		if err != nil {
			t.Fatal(err)
		}
		command.Stdin = bytes.NewBufferString(input)
		output, err := command.Output()
		if err != nil {
			t.Fatal(err)
		}
		var result HistoryPrototypeResult
		if err := json.Unmarshal(output, &result); err != nil {
			t.Fatal(err)
		}
		if result.Error == nil || result.Page != nil || len(output) > gitHistoryMaxOutput {
			t.Fatalf("malformed request response: %s", output)
		}
	}
}

func TestDescriptorGitFileReadOnlyDoesNotChangeContent(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "value"), []byte("authorized"), 0600); err != nil {
		t.Fatal(err)
	}
	f, err := prototypeFS(t, root).Open("value")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := f.Lock(); !errors.Is(err, billy.ErrReadOnly) {
		t.Fatal(err)
	}
	data, err := io.ReadAll(f)
	if err != nil || string(data) != "authorized" {
		t.Fatalf("%q %v", data, err)
	}
}
