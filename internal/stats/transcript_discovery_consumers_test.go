// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"sync"
	"testing"
	"time"
)

func writeDiscoveryFiles(t *testing.T, root string, names ...string) {
	t.Helper()
	for _, name := range names {
		if err := os.WriteFile(filepath.Join(root, name), []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func TestSharedDiscoveryCancellationBelongsToCaller(t *testing.T) {
	for _, cached := range []bool{false, true} {
		for _, deadline := range []bool{false, true} {
			t.Run(fmt.Sprintf("cached=%v/deadline=%v", cached, deadline), func(t *testing.T) {
				root := t.TempDir()
				writeDiscoveryFiles(t, root, "rollout-a.jsonl")
				d := &SharedTranscriptDiscovery{}
				if cached {
					if _, err := d.Paths(context.Background(), root); err != nil {
						t.Fatal(err)
					}
				}
				ctx, cancel := context.WithCancel(context.Background())
				want := context.Canceled
				if deadline {
					cancel()
					ctx, cancel = context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
					want = context.DeadlineExceeded
				}
				cancel()
				if _, err := d.Paths(ctx, root); !errors.Is(err, want) {
					t.Fatalf("caller cancellation=%v, want %v", err, want)
				}
				paths, err := d.Paths(context.Background(), root)
				if err != nil || !reflect.DeepEqual(paths, []string{filepath.Join(root, "rollout-a.jsonl")}) {
					t.Fatalf("healthy caller inherited cancellation: paths=%v err=%v", paths, err)
				}
			})
		}
	}
}

func TestSharedDiscoveryPreservesConsumerBoundsAndContinuation(t *testing.T) {
	for _, limits := range [][2]int{{1, 2}, {2, 1}, {1, 1}} {
		root := t.TempDir()
		writeDiscoveryFiles(t, root, "a.jsonl", "b.jsonl", "c.jsonl")
		d := &SharedTranscriptDiscovery{}
		shared := [2]*transcriptDiscoveryCursorStore{newTranscriptDiscoveryCursorStore(), newTranscriptDiscoveryCursorStore()}
		baseline := [2]*transcriptDiscoveryCursorStore{newTranscriptDiscoveryCursorStore(), newTranscriptDiscoveryCursorStore()}
		seen := [2]map[string]bool{{}, {}}
		for round := 0; round < 6; round++ {
			for owner, limit := range limits {
				bounds := transcriptDiscoveryBounds{maxFiles: limit, maxDirs: 8, maxEntries: limit + 1, maxBytes: 1 << 20}
				rows, err := d.candidates(context.Background(), root, bounds, shared[owner])
				want, wantErr := recentTranscriptCandidatesWithCursor(context.Background(), root, bounds, baseline[owner])
				if len(rows) > limit || !reflect.DeepEqual(rows, want) || !sameDiscoveryError(err, wantErr) {
					t.Fatalf("limits=%v round=%d owner=%d rows=%v err=%v want=%v err=%v", limits, round, owner, rows, err, want, wantErr)
				}
				for _, row := range rows {
					seen[owner][row.path] = true
				}
			}
		}
		for owner, paths := range seen {
			if len(paths) != 3 {
				t.Fatalf("limits=%v owner=%d discovered %d of three paths", limits, owner, len(paths))
			}
		}
	}
}

func TestSharedDiscoveryTokenAndPathConsumersKeepTheirPositions(t *testing.T) {
	root := t.TempDir()
	writeDiscoveryFiles(t, root, "a.jsonl", "b.jsonl", "c.jsonl")
	d := &SharedTranscriptDiscovery{}
	shared, baseline := newTranscriptDiscoveryCursorStore(), newTranscriptDiscoveryCursorStore()
	bounds := transcriptDiscoveryBounds{maxFiles: 1, maxDirs: 8, maxBytes: 1 << 20}
	records := []AgentRecord{{Kind: "claude", PID: 1, TmuxSession: "discovery-fixture"}}
	for round := 0; round < 4; round++ {
		if paths, err := d.Paths(context.Background(), root); err != nil || len(paths) != 3 {
			t.Fatalf("path consumer rows=%v err=%v", paths, err)
		}
		rows, err := defaultAgentTokenSourcesShared(context.Background(), records, bounds, shared, d, root)
		want, wantErr := defaultAgentTokenSourcesBoundedWithCursor(context.Background(), records, bounds, baseline, root)
		if !reflect.DeepEqual(rows, want) || !sameDiscoveryError(err, wantErr) {
			t.Fatalf("round=%d token rows=%v err=%v want=%v err=%v", round, rows, err, want, wantErr)
		}
	}
}

func TestSharedDiscoveryConcurrentConsumers(t *testing.T) {
	root := t.TempDir()
	writeDiscoveryFiles(t, root, "a.jsonl", "b.jsonl", "c.jsonl")
	d := &SharedTranscriptDiscovery{}
	var workers sync.WaitGroup
	for owner := 0; owner < 4; owner++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			cursors := newTranscriptDiscoveryCursorStore()
			seen := map[string]bool{}
			for round := 0; round < 6; round++ {
				rows, err := d.candidates(context.Background(), root, transcriptDiscoveryBounds{maxFiles: 1, maxDirs: 8, maxBytes: 1 << 20}, cursors)
				if err != nil && !TranscriptDiscoveryPending(err) {
					t.Errorf("consumer discovery: %v", err)
					return
				}
				for _, row := range rows {
					seen[row.path] = true
				}
			}
			if len(seen) != 3 {
				t.Errorf("consumer discovered %d of three paths", len(seen))
			}
		}()
	}
	workers.Wait()
}

func sameDiscoveryError(left, right error) bool {
	if left == nil || right == nil {
		return left == right
	}
	return left.Error() == right.Error()
}

func TestSharedDiscoveryCachedPageTracksDirectoryGeneration(t *testing.T) {
	root := filepath.Join(t.TempDir(), "archive")
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	writeDiscoveryFiles(t, root, "a.jsonl")
	d := &SharedTranscriptDiscovery{}
	check := func(want ...string) {
		t.Helper()
		paths, err := d.Paths(context.Background(), root)
		for index := range paths {
			paths[index] = filepath.Base(paths[index])
		}
		sort.Strings(paths)
		if err != nil || !reflect.DeepEqual(paths, want) {
			t.Fatalf("paths=%v err=%v want=%v", paths, err, want)
		}
	}
	check("a.jsonl")
	writeDiscoveryFiles(t, root, "b.jsonl")
	check("a.jsonl", "b.jsonl")
	if err := os.Rename(root, root+"-previous"); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	writeDiscoveryFiles(t, root, "replacement.jsonl")
	check("replacement.jsonl")
}

func TestSharedDiscoveryDirectoryPageCopies(t *testing.T) {
	root := t.TempDir()
	writeDiscoveryFiles(t, root, "a.jsonl")
	d := &SharedTranscriptDiscovery{}
	read := func() []string {
		t.Helper()
		file, err := os.Open(root)
		if err != nil {
			t.Fatal(err)
		}
		defer file.Close()
		names, _, _, err := d.readPage(file, 0)
		if err != nil {
			t.Fatal(err)
		}
		return names
	}
	names := read()
	if len(names) != 1 {
		t.Fatalf("names=%v", names)
	}
	names[0] = "mutated"
	if got := read(); !reflect.DeepEqual(got, []string{"a.jsonl"}) || len(d.pages) != 1 {
		t.Fatalf("cached names=%v pages=%d", got, len(d.pages))
	}
}
