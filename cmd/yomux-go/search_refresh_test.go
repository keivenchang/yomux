// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"testing"
	"time"
)

func TestHiddenIndexDeadlinesAndReopening(t *testing.T) {
	now := time.Unix(1000, 0)
	deadline := updateIndexDeadline(indexRefreshDeadline{}, false, false, 900*time.Second, 1, now, 0)
	if !deadline.next.Equal(now.Add(15 * time.Minute)) {
		t.Fatal(deadline)
	}
	for second := 1; second < 900; second++ {
		current := now.Add(time.Duration(second) * time.Second)
		deadline = updateIndexDeadline(deadline, true, false, 900*time.Second, 1, current, 0)
		if !deadline.next.After(current) {
			t.Fatalf("early hidden crawl at %d", second)
		}
	}
	visible := updateIndexDeadline(deadline, true, true, 60*time.Second, 1, now.Add(time.Minute), 0)
	if !visible.next.Equal(now.Add(time.Minute)) {
		t.Fatal("reopening did not revalidate immediately")
	}
	hidden := updateIndexDeadline(visible, true, false, 900*time.Second, 1, now.Add(2*time.Minute), 0)
	if !hidden.next.Equal(now.Add(17 * time.Minute)) {
		t.Fatal(hidden)
	}
	disabled := updateIndexDeadline(hidden, true, false, 0, 1, now, 0)
	if !disabled.next.IsZero() {
		t.Fatal("zero background cadence retained work")
	}
}

func TestCadenceSettingsPreserveIndexIdentity(t *testing.T) {
	s := &server{}
	settings := map[string]any{"indexed_dirs": []any{"/example"}}
	payload := map[string]any{"settings": map[string]any{"file_explorer": settings}}
	if _, _, err := s.updateFinderIndexSettings(payload); err != nil {
		t.Fatal(err)
	}
	revision := s.indexSettings.revision
	if s.indexSettings.backgroundRefreshSecs != 900 {
		t.Fatal("default background cadence is not 15 minutes")
	}
	settings["index_background_refresh_seconds"] = float64(1800)
	settings["index_refresh_seconds"] = float64(0)
	if _, changed, err := s.updateFinderIndexSettings(payload); err != nil || !changed {
		t.Fatalf("settings update changed=%v err=%v", changed, err)
	}
	if revision != s.indexSettings.revision {
		t.Fatal("cadence-only settings invalidated cached generation")
	}
}

func TestAddedScopeRevalidatesButReleaseKeepsDeadline(t *testing.T) {
	now := time.Unix(1000, 0)
	initial := updateIndexDeadline(indexRefreshDeadline{}, false, true, time.Minute, 1, now, 0, "/root/a")
	initial.next = now.Add(time.Minute)
	added := updateIndexDeadline(initial, true, true, time.Minute, 1, now.Add(time.Second), 0, "/root/a", "/root/b")
	if !added.next.Equal(now.Add(time.Second)) {
		t.Fatal("new scope did not revalidate")
	}
	added.next = now.Add(2 * time.Minute)
	released := updateIndexDeadline(added, true, true, time.Minute, 1, now.Add(2*time.Second), 0, "/root/b")
	if !released.next.Equal(added.next) {
		t.Fatal("release triggered another crawl")
	}
}
