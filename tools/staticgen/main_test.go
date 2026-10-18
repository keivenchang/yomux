// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRejectsCheckoutPublicationThroughAliases(t *testing.T) {
	root := t.TempDir()
	source := t.TempDir()
	outside := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(outside, "alias")
	if err := os.Symlink(root, alias); err != nil {
		t.Fatal(err)
	}
	for _, destination := range []string{filepath.Join(root, "static"), filepath.Join(alias, "static")} {
		if err := run(source, destination, false); err == nil || !strings.Contains(err.Error(), "outside a Git worktree") {
			t.Fatalf("destination %s: %v", destination, err)
		}
	}
	if _, err := os.Stat(filepath.Join(root, "static")); !os.IsNotExist(err) {
		t.Fatalf("refusal wrote output: %v", err)
	}
}

func TestAllowsDeclaredBazelOutputTree(t *testing.T) {
	root := t.TempDir()
	output := filepath.Join(root, "bazel-out", "fastbuild", "bin", "frontend_assets")
	if err := os.MkdirAll(filepath.Join(root, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "static_src"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "static_src", "build-manifest.json"), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := runWithPolicy(root, output, false, true)
	if err == nil || !strings.Contains(err.Error(), "empty asset manifest") {
		t.Fatalf("Bazel output policy did not reach asset validation: %v", err)
	}
	if strings.Contains(err.Error(), "outside a Git worktree") {
		t.Fatalf("declared Bazel output was rejected: %v", err)
	}
}

func TestBazelOutputPolicyRejectsCheckoutPaths(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(root, "static_src"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "static_src", "build-manifest.json"), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := runWithPolicy(root, filepath.Join(root, "static"), false, true)
	if err == nil || !strings.Contains(err.Error(), "must stay under bazel-out") {
		t.Fatalf("Bazel output policy accepted a checkout destination: %v", err)
	}
}
