// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGitCommandEnvironmentPreservesOnlyLockedBundleHelper(t *testing.T) {
	bundle := t.TempDir()
	gitCore := filepath.Join(bundle, "compiler", "libexec", "git-core")
	if err := os.MkdirAll(gitCore, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gitCore, "git-upload-pack"), []byte("fixture"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOMUX_NATIVE_BUNDLE", bundle)
	t.Setenv("GIT_EXEC_PATH", gitCore)
	t.Setenv("GIT_CONFIG_GLOBAL", "/operator/credentials")
	t.Setenv("GIT_DIR", "/foreign/repository")

	got := map[string]string{}
	for _, entry := range gitCommandEnvironment() {
		key, value, ok := strings.Cut(entry, "=")
		if ok {
			got[key] = value
		}
	}
	if got["GIT_EXEC_PATH"] != gitCore {
		t.Fatalf("bundled Git helper path = %q, want %q", got["GIT_EXEC_PATH"], gitCore)
	}
	if got["GIT_CONFIG_GLOBAL"] != os.DevNull {
		t.Fatalf("ambient global config was not replaced with a safe default: %q", got["GIT_CONFIG_GLOBAL"])
	}
	if _, exists := got["GIT_DIR"]; exists {
		t.Fatal("ambient GIT_DIR leaked into Git environment")
	}
}

func TestGitCommandEnvironmentDiscardsUntrustedHelperPath(t *testing.T) {
	bundle := t.TempDir()
	if err := os.MkdirAll(filepath.Join(bundle, "compiler", "libexec", "git-core"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOMUX_NATIVE_BUNDLE", bundle)
	t.Setenv("GIT_EXEC_PATH", "/operator/git-core")
	for _, entry := range gitCommandEnvironment() {
		if strings.HasPrefix(entry, "GIT_EXEC_PATH=") {
			t.Fatalf("untrusted helper path was preserved: %q", entry)
		}
	}
}

func TestGitCommandEnvironmentDiscardsBundleWithoutUploadPack(t *testing.T) {
	bundle := t.TempDir()
	gitCore := filepath.Join(bundle, "compiler", "libexec", "git-core")
	if err := os.MkdirAll(gitCore, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOMUX_NATIVE_BUNDLE", bundle)
	t.Setenv("GIT_EXEC_PATH", gitCore)
	for _, entry := range gitCommandEnvironment() {
		if strings.HasPrefix(entry, "GIT_EXEC_PATH=") {
			t.Fatalf("bundle without git-upload-pack was trusted: %q", entry)
		}
	}
}
