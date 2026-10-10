// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package capabilities

import (
	"fmt"
	"testing"
)

func TestSnapshotHasStableContractAndConservativeEntries(t *testing.T) {
	catalog := New("go-test")
	snapshot := catalog.Snapshot()
	contract, ok := snapshot["contract"].(map[string]any)
	if !ok || contract["version"] != ContractVersion || contract["backendBuild"] != "go-test" || contract["epoch"] == "" || contract["revision"] != uint64(1) {
		t.Fatalf("contract = %#v", snapshot["contract"])
	}
	if !catalog.Available("finder.read.v1") || !catalog.Available("pane.finder.v1") || !catalog.Available("pane.differ.v1") {
		t.Fatal("Finder read capability is not enabled")
	}
	if !catalog.Available("finder.write.v1") || !catalog.Available("finder.search.v1") || !catalog.Available("finder.batch.v1") || !catalog.Available("finder.preview.v1") || !catalog.Available("finder.git.v1") || !catalog.Available("pane.stats.v1") || !catalog.Available("pane.tabber.v1") || !catalog.Available("session.create.v1") || !catalog.Available("session.kill.v1") || !catalog.Available("session.rename.v1") {
		t.Fatal("capability availability does not match the implemented Finder, Tabber, and stats surfaces")
	}
	for _, name := range []string{"core.settings.v1", "core.integrations.v1", "pane.chat.v1", "yolo.auto-approve.v1"} {
		if catalog.Available(name) {
			t.Fatalf("unimplemented capability %q is unexpectedly available", name)
		}
	}
}

func TestSetAdvancesRevisionOnlyWhenStateChanges(t *testing.T) {
	catalog := New("go-test")
	if catalog.Set("pane.chat.v1", Entry{Available: true}) != true || catalog.Revision() != 2 {
		t.Fatalf("first transition revision = %d", catalog.Revision())
	}
	if catalog.Set("pane.chat.v1", Entry{Available: true}) {
		t.Fatal("identical transition changed the revision")
	}
}

func TestRouteCapabilityIsMethodAndOperationAware(t *testing.T) {
	checks := map[string]string{
		"GET /api/fs/read?path=/tmp/a":      "finder.read.v1",
		"POST /api/fs/write":                "finder.write.v1",
		"GET /api/fs/search?root=/tmp":      "finder.search.v1",
		"GET /api/fs/watch-diff?full=1":     "finder.watch.v1",
		"POST /api/watch/roots":             "finder.watch.v1",
		"GET /api/batch/zip?path=/tmp/root": "finder.preview.v1",
		"POST /api/fs/batch":                "finder.batch.v1",
		"POST /api/create-session":          "session.create.v1",
		"GET /api/yoagent/conversation":     "pane.agent.v1",
		"GET /api/system-status":            "core.diagnostics.v1",
		"GET /api/session-files?session=1":  "finder.git.v1",
		"GET /api/transcripts":              "core.bootstrap.v1",
		"GET /api/transcript?session=1":     "terminal.status.v1",
		"GET /api/context?session=1":        "terminal.status.v1",
		"GET /api/context-items?session=1":  "terminal.status.v1",
		"GET /api/context-stream?session=1": "terminal.status.v1",
		"GET /api/settings":                 "core.settings.v1",
		"POST /api/settings":                "core.settings.v1",
		"GET /api/update-status":            "core.integrations.v1",
		"GET /api/watched-prs":              "core.integrations.v1",
		"GET /api/auto-approve":             "yolo.auto-approve.v1",
		"POST /api/auto-approve":            "yolo.auto-approve.v1",
		"GET /api/yolo-rules":               "yolo.auto-approve.v1",
		"POST /api/yolo-rules/open":         "yolo.auto-approve.v1",
		"POST /api/yolo-rules/reload":       "yolo.auto-approve.v1",
		"POST /api/attention-ack":           "terminal.status.v1",
	}
	for request, want := range checks {
		var method, path string
		if _, err := fmt.Sscanf(request, "%s %s", &method, &path); err != nil {
			t.Fatal(err)
		}
		if got := RouteCapability(method, path); got != want {
			t.Errorf("RouteCapability(%q, %q) = %q, want %q", method, path, got, want)
		}
	}
	if got := RouteCapability("GET", "/api/fs/git-history-stats-stream?path=/tmp/repo&count=1&batch_size=1"); got != "finder.git.v1" {
		t.Fatalf("RouteCapability(git-history-stats-stream) = %q, want finder.git.v1", got)
	}
	catalog := New("go-test")
	if !catalog.Available("finder.git.v1") || !catalog.Available("pane.tabber.v1") {
		t.Fatal("implemented Finder and Tabber capabilities are not enabled")
	}
}
