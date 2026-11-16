// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNativeInstallPublicationKeepsConcurrentReadersOnCompleteFiles(t *testing.T) {
	directory := t.TempDir()
	oldData := bytes.Repeat([]byte("old-payload\n"), 1<<17)
	newData := bytes.Repeat([]byte("new-payload\n"), 1<<17)
	oldSource, newSource := filepath.Join(directory, "old"), filepath.Join(directory, "new")
	target := filepath.Join(directory, "served.js")
	for path, data := range map[string][]byte{oldSource: oldData, newSource: newData, target: oldData} {
		if err := os.WriteFile(path, data, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	stop, ready := make(chan struct{}), make(chan struct{})
	result := make(chan error, 1)
	go func() {
		first := true
		for {
			data, err := os.ReadFile(target)
			if first {
				close(ready)
				first = false
			}
			if err != nil || (!bytes.Equal(data, oldData) && !bytes.Equal(data, newData)) {
				result <- fmt.Errorf("reader observed a partial publication: bytes=%d, error=%v", len(data), err)
				return
			}
			select {
			case <-stop:
				result <- nil
				return
			default:
			}
		}
	}()
	<-ready
	t.Cleanup(func() {
		close(stop)
		if err := <-result; err != nil {
			t.Error(err)
		}
	})
	for range 3 {
		for _, source := range []string{newSource, oldSource} {
			if err := copyAtomic(source, target, 0o600); err != nil {
				t.Fatal(err)
			}
		}
	}
	// A read error after staging must leave the previously served file intact.
	if err := copyAtomic(directory, target, 0o600); err == nil {
		t.Fatal("directory source unexpectedly published")
	}
	if data, err := os.ReadFile(target); err != nil || !bytes.Equal(data, oldData) {
		t.Fatalf("failed publication changed served bytes: %d bytes, %v", len(data), err)
	}
}

func TestInstallationUpdateSourceRequiresMatchingPrefixAndGitRoot(t *testing.T) {
	source := t.TempDir()
	gitRun(t, source, "init", "--initial-branch=main")
	prefix := t.TempDir()
	resources := filepath.Join(prefix, "lib", "yomux")
	if err := os.MkdirAll(resources, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := recordNativeInstallSource(resources, source, prefix); err != nil {
		t.Fatal(err)
	}
	resolved, metadata, err := nativeUpdateSource(resources)
	if err != nil || resolved != source || metadata == nil || metadata.Prefix != prefix {
		t.Fatalf("installed source = %q, %#v, %v", resolved, metadata, err)
	}
	metadata.Prefix = t.TempDir()
	data, err := json.Marshal(metadata)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(resources, "install-source.json"), data, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := nativeUpdateSource(resources); err == nil {
		t.Fatal("a different installation prefix authorized an update")
	}
	if err := recordNativeInstallSource(resources, filepath.Join(source, "missing"), prefix); err == nil {
		t.Fatal("an unavailable checkout authorized installation provenance")
	}
	if _, err := verifyUpdateCheckout(filepath.Dir(source)); err == nil || !strings.Contains(err.Error(), "checkout") {
		t.Fatalf("non-checkout source error = %v", err)
	}
}

func TestInstallationRuntimeIdentityDoesNotRequireUpdateSource(t *testing.T) {
	source := t.TempDir()
	gitRun(t, source, "init", "--initial-branch=main")
	prefix := t.TempDir()
	root := filepath.Join(prefix, "lib", "yomux")
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatal(err)
	}
	if err := recordNativeInstallSource(root, source, prefix); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(source, source+"-moved"); err != nil {
		t.Fatal(err)
	}
	installation, err := readNativeInstallation(root)
	if err != nil || installation == nil || installation.SourceRoot != source {
		t.Fatalf("runtime followed unavailable update source: %#v %v", installation, err)
	}
	if _, _, err := nativeUpdateSource(root); err == nil {
		t.Fatal("unavailable update source authorized an update")
	}
}

func TestInstallationRuntimeIdentityRejectsInvalidProvenance(t *testing.T) {
	for _, variant := range []string{"malformed", "trailing", "unknown", "relative source", "wrong prefix", "symlink", "directory"} {
		t.Run(variant, func(t *testing.T) {
			prefix := t.TempDir()
			root := filepath.Join(prefix, "lib", "yomux")
			if err := os.MkdirAll(root, 0700); err != nil {
				t.Fatal(err)
			}
			metadata := nativeInstallSource{Schema: 1, SourceRoot: filepath.Join(t.TempDir(), "absent"), Prefix: prefix}
			if variant == "relative source" {
				metadata.SourceRoot = "relative"
			}
			if variant == "wrong prefix" {
				metadata.Prefix = t.TempDir()
			}
			data, err := json.Marshal(metadata)
			if err != nil {
				t.Fatal(err)
			}
			switch variant {
			case "malformed":
				data = []byte("{")
			case "trailing":
				data = append(data, []byte(" {}")...)
			case "unknown":
				data = []byte(`{"schema":1,"unexpected":true}`)
			}
			path := filepath.Join(root, "install-source.json")
			if variant == "symlink" {
				err = os.Symlink(filepath.Join(root, "absent"), path)
			} else if variant == "directory" {
				err = os.Mkdir(path, 0700)
			} else {
				err = os.WriteFile(path, data, 0600)
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := readNativeInstallation(root); err == nil {
				t.Fatal("invalid runtime provenance accepted")
			}
			if err := nativeStartupAssets(root); err == nil {
				t.Fatal("invalid provenance fell back to source startup")
			}
		})
	}
}

func TestNativeRepositoryPathsIgnoreAmbientGitConfiguration(t *testing.T) {
	root := t.TempDir()
	gitRun(t, root, "init", "--initial-branch=main")
	gitRun(t, root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "fixture")
	gitRun(t, root, "tag", "v"+version)
	head := strings.TrimSpace(gitRun(t, root, "rev-parse", "--short=12", "HEAD"))
	linked := filepath.Join(t.TempDir(), "linked")
	gitRun(t, root, "worktree", "add", "--detach", linked)
	linkedGitDir := strings.TrimSpace(gitRun(t, linked, "rev-parse", "--absolute-git-dir"))
	config := filepath.Join(t.TempDir(), "invalid-config")
	if err := os.WriteFile(config, []byte("[unterminated\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GIT_CONFIG_GLOBAL", config)
	t.Setenv("GIT_CONFIG_SYSTEM", config)
	t.Setenv("GIT_DIR", filepath.Join(t.TempDir(), "foreign.git"))
	t.Setenv("GIT_WORK_TREE", t.TempDir())
	for _, checkout := range []struct{ root, gitDir string }{
		{root, filepath.Join(root, ".git")}, {linked, linkedGitDir},
	} {
		for option, expected := range map[string]string{
			"--absolute-git-dir": checkout.gitDir,
			"--show-toplevel":    checkout.root,
		} {
			t.Run(filepath.Base(checkout.root)+option, func(t *testing.T) {
				output, err := nativeRepositoryPath(checkout.root, option)
				if err != nil || strings.TrimSpace(string(output)) != expected {
					t.Fatalf("repository path %s = %q, %v; want %q", option, output, err, expected)
				}
			})
		}
		if resolved, ok := runGitMetadata(checkout.root, "rev-parse", "--show-toplevel"); !ok || resolved != checkout.root {
			t.Fatalf("runtime Git root = %q, %v; want %q", resolved, ok, checkout.root)
		}
		metadata := (&server{root: checkout.root}).pageVersion(context.Background())
		if metadata.Error != "" || metadata.SHA != head || metadata.Count != 1 || metadata.Status != version {
			t.Fatalf("version metadata used ambient Git state: %+v", metadata)
		}
	}
	if resolved, err := verifyUpdateCheckout(root); err != nil || resolved != root {
		t.Fatalf("source checkout = %q, %v; want %q", resolved, err, root)
	}
}
