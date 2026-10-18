// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package atomicfile

import (
	"bytes"
	"context"
	"errors"
	"fmt"
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

func TestRawSecretIndependentCreatorsPreserveBinaryFormat(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "chat-cursor.key")
	var workers sync.WaitGroup
	keys := make(chan []byte, 40)
	for range 40 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			key, err := LoadOrCreateRawSecret(context.Background(), path)
			if err != nil {
				t.Error(err)
				return
			}
			keys <- key
		}()
	}
	workers.Wait()
	close(keys)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(raw) != 32 {
		t.Fatalf("raw key length=%d", len(raw))
	}
	for key := range keys {
		if !bytes.Equal(key, raw) {
			t.Fatal("independent creators disagreed")
		}
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("key mode: %v %v", info, err)
	}
	long := bytes.Repeat([]byte{0, 255, 10, 32}, 20)
	if err := os.WriteFile(path, long, 0o600); err != nil {
		t.Fatal(err)
	}
	key, err := LoadOrCreateRawSecret(context.Background(), path)
	if err != nil || !bytes.Equal(key, long) {
		t.Fatal("long binary key changed")
	}
}

func TestRawSecretLegacyPublicationAndCancellation(t *testing.T) {
	for _, cancelled := range []bool{false, true} {
		t.Run(fmt.Sprint(cancelled), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "legacy-key")
			if err := os.WriteFile(path, nil, 0o600); err != nil {
				t.Fatal(err)
			}
			observed := make(chan struct{})
			var once sync.Once
			read := func() ([]byte, error) {
				raw, err := os.ReadFile(path)
				if err != nil {
					return nil, err
				}
				if len(raw) < 32 {
					once.Do(func() { close(observed) })
					return nil, errIncompleteSecret
				}
				return raw, nil
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan error, 1)
			go func() { _, err := waitForCompleteSecret(ctx, read); result <- err }()
			<-observed
			if cancelled {
				cancel()
			} else {
				if err := os.WriteFile(path, bytes.Repeat([]byte{1}, 32), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			err := <-result
			if cancelled && !errors.Is(err, context.Canceled) {
				t.Fatalf("cancellation=%v", err)
			}
			if !cancelled && err != nil {
				t.Fatalf("legacy publication=%v", err)
			}
			raw, readErr := os.ReadFile(path)
			if readErr != nil {
				t.Fatal(readErr)
			}
			expected := 0
			if !cancelled {
				expected = 32
			}
			if len(raw) != expected {
				t.Fatal("reader rewrote legacy key")
			}
		})
	}
}
