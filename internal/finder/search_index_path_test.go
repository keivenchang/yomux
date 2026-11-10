// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func TestSearchIndexPreservesLiteralDatabasePaths(t *testing.T) {
	for _, name := range []string{"search?mode=memory.sqlite3", "search#other.sqlite3", "search%25.sqlite3", "search space.sqlite3"} {
		for _, migrate := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/migrate=%t", name, migrate), func(t *testing.T) {
				parent := filepath.Join(t.TempDir(), "cache?#% space")
				if err := os.Mkdir(parent, 0o700); err != nil {
					t.Fatal(err)
				}
				path := filepath.Join(parent, name)
				want := 0
				if migrate {
					legacy := filepath.Join(t.TempDir(), "legacy.sqlite3")
					makeV1Index(t, legacy, t.TempDir(), 2)
					if err := os.Rename(legacy, path); err != nil {
						t.Fatal(err)
					}
					want = 2
				}
				for attempt := 0; attempt < 2; attempt++ {
					store, err := openSearchIndexStore(path)
					if err != nil {
						t.Fatalf("open attempt %d: %v", attempt, err)
					}
					var count int
					if err := store.db.QueryRow("SELECT count(*) FROM entries").Scan(&count); err != nil || count != want {
						store.db.Close()
						t.Fatalf("open attempt %d entries=%d want=%d err=%v", attempt, count, want, err)
					}
					reader, err := readOnlyIndex(path)
					if err != nil {
						store.db.Close()
						t.Fatal(err)
					}
					readErr := reader.QueryRow("SELECT count(*) FROM entries").Scan(&count)
					_, writeErr := reader.Exec("CREATE TABLE unexpected_write (id INTEGER)")
					closeErr := reader.Close()
					storeCloseErr := store.db.Close()
					if readErr != nil || count != want || writeErr == nil || closeErr != nil || storeCloseErr != nil {
						t.Fatalf("reader count=%d read=%v write=%v close=%v storeClose=%v", count, readErr, writeErr, closeErr, storeCloseErr)
					}
					if _, err := os.Stat(path); err != nil {
						t.Fatalf("literal database absent: %v", err)
					}
					if err := validateIndex(path); err != nil {
						t.Fatalf("immutable publication validation: %v", err)
					}
				}
			})
		}
	}
}

func TestSearchIndexMemoryDatabaseRemainsPrivate(t *testing.T) {
	first, err := createSearchIndexStore(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer first.db.Close()
	first.db.SetMaxOpenConns(1)
	if _, err := first.db.Exec("INSERT INTO root_keys(path) VALUES('/private-memory-fixture')"); err != nil {
		t.Fatal(err)
	}
	second, err := createSearchIndexStore(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer second.db.Close()
	second.db.SetMaxOpenConns(1)
	var count int
	if err := second.db.QueryRow("SELECT count(*) FROM root_keys").Scan(&count); err != nil || count != 0 {
		t.Fatalf("independent memory database count=%d err=%v", count, err)
	}
}

func TestIndexStatusConcurrentSettings(t *testing.T) {
	store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	engine := newSearchIndexEngine(store)
	var workers sync.WaitGroup
	workers.Add(2)
	go func() {
		defer workers.Done()
		for revision := 0; revision < 100; revision++ {
			if _, err := engine.fenceSettings(nil, uint64(revision), false, true); err != nil {
				t.Error(err)
			}
		}
	}()
	go func() {
		defer workers.Done()
		for attempt := 0; attempt < 100; attempt++ {
			if _, err := engine.status("/missing", "identity"); err != nil {
				t.Error(err)
			}
		}
	}()
	workers.Wait()
}
