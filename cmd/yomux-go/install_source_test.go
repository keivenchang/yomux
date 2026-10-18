// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
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
