// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"testing"
	"time"
)

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
