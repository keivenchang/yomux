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
	if !catalog.Available("finder.read.v1") || !catalog.Available("pane.finder.v1") {
		t.Fatal("Finder read capability is not enabled")
	}
	if !catalog.Available("finder.write.v1") || !catalog.Available("finder.search.v1") || !catalog.Available("finder.batch.v1") || !catalog.Available("finder.preview.v1") || !catalog.Available("terminal.status.v1") || !catalog.Available("session.create.v1") || !catalog.Available("session.kill.v1") || !catalog.Available("session.rename.v1") || catalog.Available("pane.stats.v1") {
		t.Fatal("capability availability does not match the implemented Finder surface")
	}
}

func TestSetAdvancesRevisionOnlyWhenStateChanges(t *testing.T) {
	catalog := New("go-test")
	if catalog.Set("pane.stats.v1", Entry{Available: true}) != true || catalog.Revision() != 2 {
		t.Fatalf("first transition revision = %d", catalog.Revision())
	}
	if catalog.Set("pane.stats.v1", Entry{Available: true}) {
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
	catalog := New("go-test")
	for _, capability := range []string{"finder.git.v1", "pane.tabber.v1"} {
		if catalog.Available(capability) {
			t.Errorf("later capability %q is unexpectedly available", capability)
		}
	}
}
