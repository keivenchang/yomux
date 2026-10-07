// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package atomicfile

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

type failedFile struct {
	phase  string
	closed bool
}

var failure = errors.New("injected persistence failure")

func (f *failedFile) Write(p []byte) (int, error) {
	if f.phase == "write" {
		return 0, failure
	}
	if f.phase == "short" {
		return 0, nil
	}
	return len(p), nil
}
func (f *failedFile) Sync() error {
	if f.phase == "sync" {
		return failure
	}
	return nil
}
func (f *failedFile) Close() error {
	f.closed = true
	if f.phase == "close" {
		return failure
	}
	return nil
}
func TestPrepareFailure(t *testing.T) {
	for _, phase := range []string{"write", "short", "sync", "close"} {
		f := &failedFile{phase: phase}
		if prepare(f, []byte("new")) == nil || !f.closed {
			t.Fatal(phase)
		}
	}
}
func TestStagingPreservesPreviousUntilCommit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state")
	if err := Write(path, []byte("old")); err != nil {
		t.Fatal(err)
	}
	first, err := Stage(path, []byte("new"))
	if err != nil {
		t.Fatal(err)
	}
	defer first.Cleanup()
	second, err := Stage(path, []byte("other"))
	if err != nil {
		t.Fatal(err)
	}
	defer second.Cleanup()
	if first.temporary == second.temporary {
		t.Fatal("shared temporary")
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "old" {
		t.Fatal("premature publication")
	}
	second.Cleanup()
	if err := second.Commit(); err == nil {
		t.Fatal("missing stage committed")
	}
	data, _ = os.ReadFile(path)
	if string(data) != "old" {
		t.Fatal("failed commit replaced state")
	}
	if err := first.Commit(); err != nil {
		t.Fatal(err)
	}
	data, _ = os.ReadFile(path)
	if string(data) != "new" {
		t.Fatal("commit unreadable")
	}
}

func TestSecretPublicationIsCompleteAndShared(t *testing.T) {
	path := filepath.Join(t.TempDir(), "secret")
	var wg sync.WaitGroup
	keys := make(chan []byte, 40)
	for i := 0; i < 40; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			key, err := LoadOrCreateSecret(path)
			if err != nil {
				t.Error(err)
				return
			}
			keys <- key
		}()
	}
	wg.Wait()
	close(keys)
	var first []byte
	for key := range keys {
		if len(key) != 32 {
			t.Fatal("partial secret")
		}
		if first == nil {
			first = key
		} else if !bytes.Equal(first, key) {
			t.Fatal("concurrent key replaced")
		}
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("secret mode: %v %v", info, err)
	}
	if err = os.WriteFile(path, []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = LoadOrCreateSecret(path); err == nil {
		t.Fatal("corrupt key replaced")
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != "corrupt" {
		t.Fatal("corrupt key mutated")
	}
}
