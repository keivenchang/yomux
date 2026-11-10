// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
)

func TestSessionFilesPartialCacheAdvancesWhileRetainingAbsentRepositories(t *testing.T) {
	srv := &server{state: t.TempDir()}
	request := sessionFilesRequest{session: "1", sessionIdentity: "current", hours: 24, fromRef: "HEAD", toRef: "current"}
	viewID := sessionFilesCacheViewID(sessionFilesRequestDescriptor(request))
	makePayload := func(paths ...string) map[string]any {
		repos, files := []map[string]any{}, []map[string]any{}
		refs := map[string]any{}
		for _, path := range paths {
			repos = append(repos, map[string]any{"repo": path})
			files = append(files, map[string]any{"repo": path, "path": "old.py"})
			refs[path] = "saved"
		}
		return map[string]any{"session": "1", "repos": repos, "files": files, "refs_by_repo": refs,
			"preparation": finder.AttributionProgress{Partial: true, Error: "unrelated source failed"}}
	}
	if !srv.writeSessionFilesCache(request, viewID, makePayload("/retained", "/replaced"), http.StatusOK) {
		t.Fatal("initial partial result was not cached")
	}
	prior, _ := srv.sessionFilesCache.records.Peek(viewID)
	next := makePayload("/replaced", "/new")
	next["files"].([]map[string]any)[0]["path"] = "fresh.py"
	next["repos"].([]map[string]any)[0]["added"] = 7
	next["refs_by_repo"].(map[string]any)["/replaced"] = "fresh"
	before, _ := json.Marshal(next)
	if !srv.writeSessionFilesCache(request, viewID, next, http.StatusOK) {
		t.Fatal("partial cache froze while an unrelated source remained incomplete")
	}
	record, _ := srv.sessionFilesCache.records.Peek(viewID)
	payload := sessionFilesCachePayload(record)
	if record.StoredAt <= prior.StoredAt || len(sessionFilesPayloadRows(payload, "repos")) != 3 {
		t.Fatal("partial result did not advance with retained repositories", payload)
	}
	for _, repo := range sessionFilesPayloadRows(payload, "repos") {
		if repo["repo"] == "/replaced" && repo["added"] != float64(7) {
			t.Fatal("partial result retained obsolete Git counts", repo)
		}
	}
	paths := map[string]bool{}
	for _, file := range sessionFilesPayloadRows(payload, "files") {
		paths[file["repo"].(string)+"/"+file["path"].(string)] = true
	}
	if len(paths) != 4 || !paths["/retained/old.py"] || !paths["/replaced/fresh.py"] || !paths["/replaced/old.py"] || !paths["/new/old.py"] {
		t.Fatal("partial snapshots did not replace by repository", paths)
	}
	refs := payload["refs_by_repo"].(map[string]any)
	if refs["/retained"] != "saved" || refs["/replaced"] != "fresh" {
		t.Fatal("repository refs did not follow their snapshots", refs)
	}
	after, _ := json.Marshal(next)
	if string(before) != string(after) {
		t.Fatal("cache merge mutated its producer payload")
	}
	ready := makePayload("/new")
	ready["preparation"] = finder.AttributionProgress{State: "ready"}
	if !srv.writeSessionFilesCache(request, viewID, ready, http.StatusOK) {
		t.Fatal("complete result was not cached")
	}
	record, _ = srv.sessionFilesCache.records.Peek(viewID)
	if repos := sessionFilesPayloadRows(sessionFilesCachePayload(record), "repos"); len(repos) != 1 || repos[0]["repo"] != "/new" {
		t.Fatal("complete result retained obsolete repositories", repos)
	}
}

func TestLiveSessionFilesDiskCacheRechecksSessionAfterRead(t *testing.T) {
	request := sessionFilesRequest{session: "session-1"}
	identities := []string{"incarnation-before-read", "incarnation-after-read"}
	identityCalls := 0
	readIdentity := func(string) (string, bool) {
		identity := identities[identityCalls]
		identityCalls++
		return identity, true
	}
	readCache := func(request sessionFilesRequest, _ string) (sessionFilesCacheRecord, bool, bool) {
		if request.sessionIdentity != "incarnation-before-read" {
			t.Fatalf("cache read session identity = %q", request.sessionIdentity)
		}
		return sessionFilesCacheRecord{SessionIdentity: request.sessionIdentity}, true, true
	}

	_, _, hit := readLiveSessionFilesCacheWith(request, "view", readIdentity, readCache)
	if hit {
		t.Fatal("disk cache hit was admitted after the session was replaced during the read")
	}
	if identityCalls != 2 {
		t.Fatalf("session identity reads = %d, want pre-read and post-read checks", identityCalls)
	}
}

func TestLiveSessionFilesMemoryCacheHitUsesOneSessionIdentityRead(t *testing.T) {
	request := sessionFilesRequest{session: "session-1"}
	identityCalls := 0
	readIdentity := func(string) (string, bool) {
		identityCalls++
		return "current-incarnation", true
	}
	readCache := func(request sessionFilesRequest, _ string) (sessionFilesCacheRecord, bool, bool) {
		if request.sessionIdentity != "current-incarnation" {
			t.Fatalf("cache read session identity = %q", request.sessionIdentity)
		}
		return sessionFilesCacheRecord{SessionIdentity: request.sessionIdentity}, true, false
	}

	_, _, hit := readLiveSessionFilesCacheWith(request, "view", readIdentity, readCache)
	if !hit {
		t.Fatal("current memory cache hit was rejected")
	}
	if identityCalls != 1 {
		t.Fatalf("session identity reads = %d, want one for a warm memory hit", identityCalls)
	}
}

func TestSessionFilesCompletionGenerationChangesForTheSameView(t *testing.T) {
	startedAt := time.Date(2026, 9, 25, 12, 0, 0, 0, time.UTC)
	srv := &server{events: newEventBroker(), startedAt: startedAt}
	request := sessionFilesRequest{session: "1", hours: 24}
	viewID := sessionFilesCacheViewID(sessionFilesRequestDescriptor(request))
	for range 2 {
		generation, admitted := srv.beginSessionFilesRefresh(viewID)
		if !admitted {
			t.Fatal("same-view refresh was not admitted after its predecessor finished")
		}
		srv.publishSessionFilesRefreshCompleted(request, viewID, generation)
		srv.finishSessionFilesRefresh(viewID, generation)
	}
	if len(srv.events.events) != 2 {
		t.Fatalf("same-view completion events = %d, want 2", len(srv.events.events))
	}
	first, second := srv.events.events[0].Payload, srv.events.events[1].Payload
	if first["cache_view_id"] != viewID || second["cache_view_id"] != viewID {
		t.Fatalf("completion view IDs = %#v then %#v, want %q", first["cache_view_id"], second["cache_view_id"], viewID)
	}
	if first["completion_generation"] == second["completion_generation"] {
		t.Fatalf("same-view completion generations did not advance: %#v then %#v", first["completion_generation"], second["completion_generation"])
	}

	restarted := &server{events: newEventBroker(), startedAt: startedAt.Add(time.Second)}
	generation, admitted := restarted.beginSessionFilesRefresh(viewID)
	if !admitted {
		t.Fatal("same-view refresh was not admitted after backend restart")
	}
	restarted.publishSessionFilesRefreshCompleted(request, viewID, generation)
	if got := restarted.events.events[0].Payload["completion_generation"]; got == first["completion_generation"] {
		t.Fatalf("backend restart reused completion generation %q", got)
	}
}

func TestSessionFilesPartialCacheIncomingRowsWinWithoutDroppingSavedSiblings(t *testing.T) {
	srv := &server{state: t.TempDir()}
	request := sessionFilesRequest{session: "1", sessionIdentity: "current", hours: 24, fromRef: "HEAD", toRef: "current"}
	viewID := newSessionFilesPlan(request).viewID
	row := func(path string, added int) map[string]any {
		return map[string]any{"repo": "/repo", "path": path, "added": added}
	}
	payload := func(partial bool, rows ...map[string]any) map[string]any {
		return map[string]any{"session": "1", "repos": []map[string]any{{"repo": "/repo"}}, "files": rows,
			"preparation": finder.AttributionProgress{Partial: partial}}
	}
	if !srv.writeSessionFilesCache(request, viewID, payload(false, row("kept.py", 1), row("saved-only.py", 2)), http.StatusOK) {
		t.Fatal("initial write failed")
	}
	for repeat := 0; repeat < 2; repeat++ {
		if !srv.writeSessionFilesCache(request, viewID, payload(true, row("kept.py", 9), row("new.py", 3)), http.StatusOK) {
			t.Fatal("partial write failed")
		}
		record, _ := srv.sessionFilesCache.records.Peek(viewID)
		rows := sessionFilesPayloadRows(sessionFilesCachePayload(record), "files")
		counts := map[string]float64{}
		for _, file := range rows {
			counts[file["path"].(string)] = file["added"].(float64)
		}
		if len(rows) != 3 || len(counts) != 3 || counts["kept.py"] != 9 || counts["saved-only.py"] != 2 || counts["new.py"] != 3 {
			t.Fatal("partial rows lost saved data or current metadata", rows)
		}
	}
	if !srv.writeSessionFilesCache(request, viewID, payload(false, row("kept.py", 9), row("new.py", 3)), http.StatusOK) {
		t.Fatal("complete write failed")
	}
	record, _ := srv.sessionFilesCache.records.Peek(viewID)
	if rows := sessionFilesPayloadRows(sessionFilesCachePayload(record), "files"); len(rows) != 2 {
		t.Fatal("complete inventory retained stale rows", rows)
	}
	other := request
	other.session = "other-session"
	other.fromRef = "different-comparison"
	otherViewID := newSessionFilesPlan(other).viewID
	otherPayload := payload(true, row("other.py", 4))
	otherPayload["session"] = other.session
	if !srv.writeSessionFilesCache(other, otherViewID, otherPayload, http.StatusOK) {
		t.Fatal("other comparison write failed")
	}
	record, _ = srv.sessionFilesCache.records.Peek(otherViewID)
	if rows := sessionFilesPayloadRows(sessionFilesCachePayload(record), "files"); len(rows) != 1 || rows[0]["path"] != "other.py" {
		t.Fatal("partial merge crossed session/comparison", rows)
	}
}
