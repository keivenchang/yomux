// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestReviewFixedAndRollingRetention(t *testing.T) {
	s, root := newTestService(t, nil)
	path := filepath.Join(root, "outside.txt")
	write := func(text string) {
		t.Helper()
		if err := os.WriteFile(path, []byte(text), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("before\n")
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "browser", path, fileID(info), "", []any{root})
	now := time.Now()
	s.mu.Lock()
	s.reviewClock = func() time.Time { return now }
	s.mu.Unlock()
	request := map[string]any{"client_id": "browser", "tracking_id": row["tracking_id"], "action": "start", "minutes": float64(0)}
	call := func() map[string]any {
		t.Helper()
		result, e := s.Review(request)
		if e != nil {
			t.Fatal(e)
		}
		return result
	}
	started := call()
	request["review_id"] = started["review_id"]
	request["action"] = "read"
	advance := func(d time.Duration) {
		s.mu.Lock()
		now = now.Add(d)
		w := s.watches["browser"]
		w.ExpiresAt = now.Add(time.Hour)
		s.watches["browser"] = w
		s.mu.Unlock()
	}
	advance(time.Minute)
	write("after\n")
	call()
	call()
	s.mu.Lock()
	count := len(s.reviews[started["review_id"].(string)].versions)
	s.mu.Unlock()
	if count != 1 {
		t.Fatalf("duplicate observations: %d", count)
	}
	advance(31 * time.Minute)
	if got := call()["original"]; got != "before\n" {
		t.Fatalf("full review lost original: %q", got)
	}
	for _, minute := range []int{1, 5, 15, 30} {
		request["minutes"] = float64(minute)
		if got := call()["original"]; got != "after\n" {
			t.Fatalf("rolling %d: %q", minute, got)
		}
	}
	request["minutes"] = float64(0)
	if got := call()["original"]; got != "before\n" {
		t.Fatalf("switch back: %q", got)
	}
	request["action"] = "reset"
	reset := call()
	if reset["original"] != "after\n" || reset["review_id"] == started["review_id"] {
		t.Fatalf("reset=%v", reset)
	}
	request["review_id"] = reset["review_id"]
	request["action"] = "end"
	call()
	request["action"] = "read"
	if _, e := s.Review(request); e == nil {
		t.Fatal("ended review readable")
	}
}

func TestReviewIdentityLeaseAndLimits(t *testing.T) {
	s, root := newTestService(t, nil)
	path := filepath.Join(root, "notes.md")
	if err := os.WriteFile(path, []byte("# First\n"), 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "browser", path, fileID(info), "", []any{root})
	request := map[string]any{"client_id": "browser", "tracking_id": row["tracking_id"], "action": "start"}
	started, e := s.Review(request)
	if e != nil {
		t.Fatal(e)
	}
	request["review_id"] = started["review_id"]
	request["action"] = "read"
	replacement := path + ".tmp"
	if err := os.WriteFile(replacement, []byte("# Replaced\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacement, path); err != nil {
		t.Fatal(err)
	}
	if _, e := s.Review(request); e != nil {
		t.Fatal(e)
	}
	moved := filepath.Join(root, "renamed.md")
	if err := os.Rename(path, moved); err != nil {
		t.Fatal(err)
	}
	got, e := s.Review(request)
	if e != nil {
		t.Fatal(e)
	}
	if got["path"] != moved || got["original"] != "# First\n" {
		t.Fatalf("rename lost review: %v", got)
	}
	request["client_id"] = "other"
	if _, e := s.Review(request); e == nil {
		t.Fatal("other browser read review")
	}
	request["client_id"] = "browser"
	for i := 0; i < 40; i++ {
		if err := os.WriteFile(moved, []byte(strings.Repeat("x", i+1)), 0600); err != nil {
			t.Fatal(err)
		}
		if _, e := s.Review(request); e != nil {
			t.Fatal(e)
		}
	}
	s.mu.Lock()
	r := s.reviews[started["review_id"].(string)]
	count := len(r.versions)
	s.mu.Unlock()
	if count != reviewSnapshotLimit {
		t.Fatalf("snapshots=%d", count)
	}
	request["minutes"] = float64(15)
	got, e = s.Review(request)
	if e != nil {
		t.Fatal(e)
	}
	if got["complete"] != false {
		t.Fatal("missing coverage not reported")
	}
	if err := os.WriteFile(moved, []byte(strings.Repeat("x", reviewFileLimit+1)), 0600); err != nil {
		t.Fatal(err)
	}
	request["action"] = "reset"
	if _, e := s.Review(request); e == nil {
		t.Fatal("oversize baseline accepted")
	}
	s.mu.Lock()
	retained := s.reviews[started["review_id"].(string)]
	s.mu.Unlock()
	if retained == nil {
		t.Fatal("failed reset lost fixed baseline")
	}
	s.pruneExpiredWatches(time.Now().Add(watchLeaseDuration + time.Second))
	s.mu.Lock()
	count = len(s.reviews)
	s.mu.Unlock()
	if count != 0 {
		t.Fatalf("expired histories=%d", count)
	}
}

func TestReviewGlobalMemoryAdmission(t *testing.T) {
	s, _ := newTestService(t, nil)
	s.mu.Lock()
	defer s.mu.Unlock()
	s.reviews = map[string]*fileReview{"full": {baseline: reviewVersion{text: strings.Repeat("x", reviewMemoryLimit)}}}
	if s.trimReviewsLocked(1) {
		t.Fatal("over-budget baseline admitted")
	}
	r := s.reviews["full"]
	r.baseline.text = strings.Repeat("x", reviewMemoryLimit-2)
	r.versions = []reviewVersion{{text: "a"}, {at: time.Now(), text: "b"}}
	if !s.trimReviewsLocked(1) || len(r.versions) != 1 || s.reviewBytesLocked() > reviewMemoryLimit-1 {
		t.Fatal("rolling snapshots not trimmed before fixed baseline")
	}
}

func TestReviewReconnectThroughEmptyRegistration(t *testing.T) {
	s, root := newTestService(t, nil)
	path := filepath.Join(root, "reload.txt")
	if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "browser", path, fileID(info), "", []any{root})
	request := map[string]any{"client_id": "browser", "tracking_id": row["tracking_id"], "action": "start"}
	started, e := s.Review(request)
	if e != nil {
		t.Fatal(e)
	}
	s.ReleaseWatchClient("browser")
	if _, e := s.WatchRoots(context.Background(), map[string]any{"client_id": "browser"}); e != nil {
		t.Fatal(e)
	}
	s.mu.Lock()
	retained := s.reviews[started["review_id"].(string)]
	entry := s.openFiles[row["tracking_id"].(string)]
	s.mu.Unlock()
	if retained == nil || entry == nil {
		t.Fatal("reload initialization retired a valid review lease")
	}
	registerTestOpenFile(t, s, "browser", path, fileID(info), row["tracking_id"].(string), []any{root})
	request["action"] = "read"
	request["review_id"] = started["review_id"]
	got, e := s.Review(request)
	if e != nil {
		t.Fatal(e)
	}
	if got["original"] != "original" {
		t.Fatalf("baseline=%v", got)
	}
}

func TestReviewLeasesParticipateInOpenFileAdmission(t *testing.T) {
	s, root := newTestService(t, nil)
	rows := make([]any, 0, maxActiveWatchFiles)
	for i := 0; i < maxActiveWatchFiles; i++ {
		path := filepath.Join(root, fmt.Sprintf("empty-%d", i))
		if err := os.WriteFile(path, nil, 0600); err != nil {
			t.Fatal(err)
		}
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, map[string]any{"path": path, "file_id": fileID(info)})
	}
	response, e := s.WatchRoots(context.Background(), map[string]any{"client_id": "browser", "open_files": rows})
	if e != nil {
		t.Fatal(e)
	}
	s.mu.Lock()
	s.reviews = map[string]*fileReview{}
	for _, row := range response["open_files"].([]map[string]any) {
		id := row["tracking_id"].(string)
		s.reviews[id] = &fileReview{id: id, client: "browser", tracking: id, expiresAt: s.watches["browser"].ExpiresAt}
	}
	s.mu.Unlock()
	if _, e := s.WatchRoots(context.Background(), map[string]any{"client_id": "browser"}); e != nil {
		t.Fatal(e)
	}
	next := filepath.Join(root, "one-more")
	if err := os.WriteFile(next, nil, 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(next)
	if err != nil {
		t.Fatal(err)
	}
	_, e = s.WatchRoots(context.Background(), map[string]any{"client_id": "browser", "open_files": []any{map[string]any{"path": next, "file_id": fileID(info)}}})
	if e == nil || e.Status != 429 {
		t.Fatalf("retained reviews bypassed descriptor cap: %v", e)
	}
	s.mu.Lock()
	count := len(s.openFiles)
	s.mu.Unlock()
	if count != maxActiveWatchFiles {
		t.Fatalf("retained descriptors=%d", count)
	}
}
