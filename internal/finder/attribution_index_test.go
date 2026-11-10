// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestAttributionIndexOversizedSourceRetryKeepsFailureVisible(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	path := filepath.Join(t.TempDir(), "rollout-oversized.jsonl")
	data := append([]byte(`{"type":"ignored","body":"`), bytes.Repeat([]byte("x"), maxSessionTranscriptLineBytes+1)...)
	data = append(data, []byte("\"}\n")...)
	if err = os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	source := AttributionSource{Path: path, Kind: "codex"}
	failure, retries := "", 0
	for round := 0; round < 100 && retries < 3; round++ {
		_, _, complete, scanErr := index.index(context.Background(), source, 256<<10, 4096)
		index.setSourceResult(path, complete, scanErr)
		if scanErr != nil {
			if scanErr.Error() != fmt.Sprintf("attribution record exceeds %d-byte limit", maxSessionTranscriptLineBytes) {
				t.Fatal("fixture failed before its oversized record", scanErr)
			}
			failure = index.Progress().Error
			if failure == "" {
				t.Fatal("oversized source error was hidden")
			}
		} else if failure != "" {
			if complete || index.Progress().Error != failure {
				t.Fatal("successful partial retry hid oversized source failure", complete, index.Progress())
			}
			retries++
		}
	}
	if failure == "" || retries != 3 {
		t.Fatal("fixture did not exercise failed source followed by partial retries", failure, retries)
	}
	if err = os.WriteFile(path, []byte("{}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	_, _, complete, err := index.index(context.Background(), source, 256<<10, 4096)
	index.setSourceResult(path, complete, err)
	if err != nil || !complete || index.Progress().Error != "" {
		t.Fatal("fully replayed replacement did not clear oversized source failure", complete, err, index.Progress())
	}
}

func TestAttributionIndexSourceFailurePersistsUntilCompleteRecovery(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	path := "/a-oversized.jsonl"
	index.setSourceResult(path, false, fmt.Errorf("transcript record exceeds attribution limit"))
	failure := index.Progress().Error
	if failure == "" {
		t.Fatal("source failure was not reported")
	}
	for round := 0; round < 3; round++ {
		index.setSourceResult(path, false, nil)
		index.setSourceResult("/z-healthy.jsonl", true, nil)
		if index.Progress().Error != failure || !index.Progress().Partial {
			t.Fatal("partial retry hid an unrecovered source failure", index.Progress())
		}
	}
	index.setSourceResult("/z-other.jsonl", false, fmt.Errorf("another source failed"))
	if index.Progress().Error != failure {
		t.Fatal("unrelated source changed the existing error")
	}
	index.setSourceResult(path, true, nil)
	if _, remains := index.failures[path]; remains || index.Progress().Error == "" {
		t.Fatal("complete recovery did not retire only its own failure", index.Progress())
	}
	index.setSourceResult("/z-other.jsonl", true, nil)
	if index.Progress().Error != "" {
		t.Fatal("complete recovery retained an error", index.Progress())
	}
}

func TestAttributionIndexRoutineDiscoveryPreservesCompletedCoverage(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	index.setDiscoveryProgress(ErrAttributionDiscoveryPending)
	if !index.discoveryPending || !index.Progress().Partial {
		t.Fatal("initial traversal advertised complete coverage")
	}
	index.setDiscoveryProgress(nil)
	index.progress = AttributionProgress{State: "ready"}
	for page := 0; page < 3; page++ {
		index.setDiscoveryProgress(ErrAttributionDiscoveryPending)
		if index.discoveryPending || index.Progress().Partial || index.Progress().State != "ready" {
			t.Fatal("routine pagination invalidated completed coverage", index.Progress())
		}
	}
	index.setDiscoveryProgress(fmt.Errorf("discovery read failed"))
	if index.Progress().Error == "" || !index.Progress().Partial {
		t.Fatal("substantive discovery error hidden")
	}
	index.setDiscoveryProgress(ErrAttributionDiscoveryPending)
	if index.Progress().Error != "" || index.discoveryPending {
		t.Fatal("recovered routine discovery retained its error", index.Progress())
	}
	path := filepath.Join(t.TempDir(), "rollout-changing.jsonl")
	if err = os.WriteFile(path, []byte("{}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	source := AttributionSource{Path: path, Kind: "codex"}
	if _, _, _, err = index.index(context.Background(), source, 4<<20, 1); err != nil {
		t.Fatal(err)
	}
	ready, err := index.preparedQueue(context.Background())
	if err != nil || !ready {
		t.Fatal("complete known source blocked readiness", ready, err)
	}
	if err = os.WriteFile(path, []byte("{}\n{}\n{}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(context.Background(), source, 4<<20, 1); err != nil {
		t.Fatal(err)
	}
	index.setDiscoveryProgress(ErrAttributionDiscoveryPending)
	ready, err = index.preparedQueue(context.Background())
	if err != nil || ready {
		t.Fatal("changed source with incomplete replay advertised ready", ready, err)
	}
	if _, _, _, err = index.index(context.Background(), source, 4<<20, 1); err != nil {
		t.Fatal(err)
	}
	ready, err = index.preparedQueue(context.Background())
	if err != nil || !ready {
		t.Fatal("completed replay did not recover readiness", ready, err)
	}
	index.Enqueue(AttributionSource{Path: "/unknown", Kind: "codex"}, false)
	ready, err = index.preparedQueue(context.Background())
	if err != nil || ready {
		t.Fatal("unknown admission advertised complete coverage", ready, err)
	}
	index.coverageIncomplete, index.overflowLost = true, true
	index.setSourceError("queue", fmt.Errorf("lost admission"))
	index.setDiscoveryProgress(nil)
	if !index.coverageIncomplete || !index.overflowLost || index.Progress().Error == "" {
		t.Fatal("completed traversal cleared independent missing coverage")
	}
}

func TestAttributionIndexParityRestartAndAppend(t *testing.T) {
	ctx := context.Background()
	home := t.TempDir()
	state := t.TempDir()
	path := filepath.Join(home, "rollout.jsonl")
	data := `{"type":"session_meta","payload":{"cwd":"` + home + `"}}
{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"touch one.py; git add two.py\"}"}}
`
	if err := os.WriteFile(path, []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
	index, err := OpenAttributionIndex(state, nil)
	if err != nil {
		t.Fatal(err)
	}
	source := AttributionSource{Path: path, Kind: "codex", Historical: true}
	_, _, complete, err := index.index(ctx, source, 8<<20, 4096)
	if err != nil || !complete {
		t.Fatal(complete, err)
	}
	got, err := index.query(ctx, nil, []string{home}, 24)
	if err != nil {
		t.Fatal(err)
	}
	expected, _ := scanSessionTranscriptContext(ctx, path, SessionFileAgent{Kind: "codex", CWD: home}, time.Now().Add(-24*time.Hour))
	if len(expected) == 0 || len(got) != len(expected) {
		t.Fatalf("parity counts: indexed %d legacy %d", len(got), len(expected))
	}
	for path, a := range expected {
		if got[path] == nil || got[path].status != a.status {
			t.Fatalf("parity at %q", path)
		}
	}
	if err = index.Close(); err != nil {
		t.Fatal(err)
	}
	index, err = OpenAttributionIndex(state, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.WriteString("{\"payload\":{\"type\":\"function_call\",\"name\":\"exec_command\",\"arguments\":\"{\\\"cmd\\\":\\\"git add three.py\\\"}\"}}\n"); err != nil {
		t.Fatal(err)
	}
	f.Close()
	_, n, complete, err := index.index(ctx, source, 8<<20, 4096)
	if err != nil || !complete || n != 1 {
		t.Fatal(n, complete, err)
	}
	got, err = index.query(ctx, nil, []string{home}, 24)
	if err != nil {
		t.Fatal(err)
	}
	if got[filepath.Join(home, "three.py")] == nil {
		t.Fatal("append missing")
	}
	var commandRows int
	if err = index.db.QueryRow("SELECT count(*) FROM attribution_operations WHERE path LIKE '%touch%' OR path LIKE '%git add%'").Scan(&commandRows); err != nil || commandRows != 0 {
		t.Fatal(commandRows, err)
	}
}
func TestAttributionIndexTransactionFailureDoesNotAdvance(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "rollout.jsonl")
	if err := os.WriteFile(path, []byte("{\"payload\":{\"type\":\"function_call\",\"name\":\"exec_command\",\"arguments\":\"{\\\"cmd\\\":\\\"git add a\\\"}\"}}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	if _, err = index.db.Exec("CREATE TRIGGER reject_operation BEFORE INSERT ON attribution_operations BEGIN SELECT RAISE(ABORT,'test interrupted commit'); END"); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(ctx, AttributionSource{Path: path, Kind: "codex", Historical: true}, 8<<20, 4096); err == nil {
		t.Fatal("commit unexpectedly succeeded")
	}
	var count int
	if err = index.db.QueryRow("SELECT count(*) FROM attribution_sources").Scan(&count); err != nil || count != 0 {
		t.Fatal("cursor led operation durability", count, err)
	}
	if _, err = index.db.Exec("DROP TRIGGER reject_operation"); err != nil {
		t.Fatal(err)
	}
	if _, _, complete, err := index.index(ctx, AttributionSource{Path: path, Kind: "codex", Historical: true}, 8<<20, 4096); err != nil || !complete {
		t.Fatal(complete, err)
	}
}

func TestAttributionIndexMtimeDeletionRecoveryAndProvenance(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	path := filepath.Join(root, "rollout-time.jsonl")
	data := []byte("{\"payload\":{\"type\":\"function_call\",\"name\":\"exec_command\",\"arguments\":\"{\\\"cmd\\\":\\\"git add file.py\\\"}\"}}\n")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-48 * time.Hour)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	source := AttributionSource{Path: path, Kind: "codex", Historical: true}
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	got, err := index.query(ctx, nil, []string{root}, 24)
	if err != nil || len(got) != 0 {
		t.Fatal(len(got), err)
	}
	now := time.Now()
	if err = os.Chtimes(path, now, now); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	got, err = index.query(ctx, nil, []string{root}, 24)
	if err != nil || len(got) != 1 {
		t.Fatal("mtime refresh missing", len(got), err)
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err == nil {
		t.Fatal("deleted source accepted")
	}
	got, err = index.query(ctx, nil, []string{root}, 24)
	if err != nil || len(got) != 0 {
		t.Fatal("deleted source still attributed", len(got), err)
	}
	if err = os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	got, err = index.query(ctx, nil, []string{root}, 24)
	if err != nil || len(got) != 1 {
		t.Fatal("recovery missing", len(got), err)
	}
	outside := filepath.Join(root, "temporary.jsonl")
	if err = os.WriteFile(outside, data, 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(ctx, AttributionSource{Path: outside, Kind: "codex"}, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	if _, err = index.db.Exec("DELETE FROM attribution_sources WHERE path=?", path); err != nil {
		t.Fatal(err)
	}
	got, err = index.query(ctx, nil, []string{root}, 24)
	if err != nil || len(got) != 0 {
		t.Fatal("non-archive live source leaked into history", len(got), err)
	}
	index.setSourceError("transient", context.DeadlineExceeded)
	index.setSourceError("transient", nil)
	if index.Progress().Error != "" {
		t.Fatal("successful retry retained transient error")
	}
}

func TestAttributionErrorSummaryIsStableAcrossUnrelatedProgress(t *testing.T) {
	index := &AttributionIndex{failures: make(map[string]error)}
	index.setSourceError("b", fmt.Errorf("second source failed"))
	index.setSourceError("a", fmt.Errorf("first source failed"))
	for n := 0; n < 100; n++ {
		index.setSourceError("successful-source", nil)
		if got := index.Progress().Error; got != "first source failed" {
			t.Fatalf("unchanged failures changed their summary: %q", got)
		}
	}
	index.setSourceError("a", nil)
	if got := index.Progress().Error; got != "second source failed" {
		t.Fatalf("resolved failure did not reveal the remaining error: %q", got)
	}
}

func TestDifferPreparedHomeOutsideGitStreamsHistoricalRepositories(t *testing.T) {
	ctx := context.Background()
	repo1, _ := newCommittedTestRepo(t)
	repo2, _ := newCommittedTestRepo(t)
	// Both repositories share the fixture test root; their immediate parent is
	// outside Git, matching a shell in home with no saved Differ view.
	for _, repo := range []string{repo1, repo2} {
		if err := os.WriteFile(filepath.Join(repo, "note.txt"), []byte("changed\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	home := filepath.Dir(repo1)
	archive := t.TempDir()
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	for n, repo := range []string{repo1, repo2} {
		path := filepath.Join(archive, fmt.Sprintf("rollout-%d.jsonl", n))
		record := map[string]any{"type": "response_item", "payload": map[string]any{"type": "function_call", "name": "exec_command", "arguments": map[string]any{"cmd": "git add note.txt", "workdir": repo}}}
		data, err := json.Marshal(record)
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(path, append(data, '\n'), 0600); err != nil {
			t.Fatal(err)
		}
		if _, _, _, err = index.index(ctx, AttributionSource{Path: path, Kind: "codex", Historical: true}, 4<<20, 4096); err != nil {
			t.Fatal(err)
		}
	}
	service, err := New(home, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	service.SetAttributionIndex(index)
	started := time.Now()
	first := time.Duration(0)
	emitted := map[string]bool{}
	payload, finderErr := service.SessionFilesWithAgentsStream(ctx, "1", []string{home}, nil, 24, "HEAD", "current", nil, func(chunk map[string]any) error {
		if first == 0 {
			first = time.Since(started)
		}
		for _, repo := range chunk["repos"].([]map[string]any) {
			emitted[repo["repo"].(string)] = true
		}
		return nil
	})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if !emitted[repo1] || !emitted[repo2] || len(payload["repos"].([]map[string]any)) != 2 {
		t.Fatal("home lost a historical repository", emitted)
	}
	t.Logf("prepared first repository=%s completion=%s", first, time.Since(started))
	if len(payload["files"].([]map[string]any)) != 2 {
		t.Fatal("prepared modified rows missing")
	}
	for _, file := range payload["files"].([]map[string]any) {
		if file["size"] == nil {
			t.Fatal("historical metadata enrichment omitted")
		}
	}
}

func TestAttributionIndexSourceEvictionRemainsIncompleteAfterNextCommitAndRestart(t *testing.T) {
	ctx := context.Background()
	directory := t.TempDir()
	index, err := OpenAttributionIndex(directory, nil)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := index.db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	for n := 0; n < attributionMaxSources; n++ {
		if _, err = tx.Exec("INSERT INTO attribution_sources VALUES(?,? ,?, '',?,1,0,'',1)", fmt.Sprintf("/old/%04d", n), "codex", []byte("{}"), n); err != nil {
			t.Fatal(err)
		}
	}
	if err = tx.Commit(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "rollout-new.jsonl")
	if err = os.WriteFile(path, []byte("{}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	source := AttributionSource{Path: path, Kind: "codex", Historical: true}
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal("new source blocked by old rows", err)
	}
	if !index.coverageIncomplete || index.Progress().Gaps == 0 {
		t.Fatal("eviction advertised complete coverage")
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.WriteString("{}\n"); err != nil {
		t.Fatal(err)
	}
	f.Close()
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	if !index.coverageIncomplete {
		t.Fatal("subsequent commit erased eviction coverage")
	}
	if err = index.Close(); err != nil {
		t.Fatal(err)
	}
	index, err = OpenAttributionIndex(directory, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	if !index.coverageIncomplete || !index.Progress().Partial {
		t.Fatal("restart erased durable eviction gap")
	}
}

func TestAttributionIndexPromotesLiveSourceAndReconcilesDeletedHistory(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	path := filepath.Join(root, "rollout-promoted.jsonl")
	if err := os.WriteFile(path, []byte("{\"payload\":{\"type\":\"function_call\",\"name\":\"exec_command\",\"arguments\":\"{\\\"cmd\\\":\\\"git add file.py\\\"}\"}}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	source := AttributionSource{Path: path, Kind: "codex"}
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	source.Historical = true
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	got, err := index.query(ctx, nil, []string{root}, 24)
	if err != nil || len(got) != 1 {
		t.Fatal("historical promotion missed", len(got), err)
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err = index.reconcilePrepared(ctx); err != nil {
		t.Fatal(err)
	}
	got, err = index.query(ctx, nil, []string{root}, 24)
	if err != nil || len(got) != 0 {
		t.Fatal("deleted historical source remained visible", len(got), err)
	}
}

func TestAttributionIndexOverflowRetainsUnadmittedSources(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	for n := 0; n < 520; n++ {
		index.Enqueue(AttributionSource{Path: fmt.Sprintf("/source/%d", n), Kind: "codex"}, false)
	}
	if len(index.queue) != 512 || len(index.deferred) != 8 || len(index.pending) != 520 {
		t.Fatal("discovery dropped overflow", len(index.queue), len(index.deferred), len(index.pending))
	}
	if !index.Progress().Partial {
		t.Fatal("overflow advertised complete")
	}
	index.setSourceError("query", nil)
	if progress := index.Progress(); progress.Error != "" || progress.State != "indexing" || index.overflowLost {
		t.Fatal("retained deferred backlog reported an error", progress)
	}
	index.Enqueue(AttributionSource{Path: "/source/519", Kind: "codex", Historical: true}, false)
	if !index.deferred[7].Historical {
		t.Fatal("deferred source lost historical promotion")
	}
	index.Enqueue(AttributionSource{Path: "/source/519", Kind: "codex"}, true)
	if index.queue[0].Path != "/source/519" || !index.queue[0].Historical || time.Since(index.active["/source/519"]) >= time.Minute {
		t.Fatal("visible deferred source was not promoted")
	}
	if len(index.queue) != 512 || len(index.deferred) != 8 || len(index.pending) != 520 {
		t.Fatal("priority promotion changed queue ownership")
	}
}

func TestAttributionIndexLostAdmissionRecoversOnlyKnownPaths(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	for n := 0; n < 512+attributionMaxSources+2; n++ {
		index.Enqueue(AttributionSource{Path: fmt.Sprintf("/source/%d", n), Kind: "codex", Historical: true}, false)
	}
	index.setSourceError("query", nil)
	if !index.overflowLost || index.Progress().Error == "" {
		t.Fatal("lost admission was not visible")
	}
	for n := 0; n < 2; n++ {
		index.dequeueLocked()
		index.Enqueue(AttributionSource{Path: fmt.Sprintf("/source/%d", 512+attributionMaxSources+n), Kind: "codex"}, false)
		if !index.deferred[len(index.deferred)-1].Historical {
			t.Fatal("rediscovery lost historical scope")
		}
		index.setSourceError("query", nil)
		if got := index.overflowLost; got != (n == 0) {
			t.Fatal("lost coverage cleared before all identities were readmitted", n, got)
		}
	}
	if progress := index.Progress(); progress.Error != "" || !progress.Partial {
		t.Fatal("recovered admission should remain ordinary partial backlog", progress)
	}
}

func TestAttributionIndexPriorityKeepsHistoricalShareAcrossCycles(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	index.Enqueue(AttributionSource{Path: "/archive", Kind: "codex"}, false)
	for n := 0; n < 3; n++ {
		index.Enqueue(AttributionSource{Path: fmt.Sprintf("/live/%d", n), Kind: "codex"}, true)
	}
	for n := 0; n < 3; n++ {
		source := index.dequeueLocked()
		if (source.Path == "/archive") != (n == 2) {
			t.Fatal("historical work starved behind live cycles", n, source.Path)
		}
	}
}

func TestAttributionIndexOperationEvictionInvalidatesCWDAndGeneration(t *testing.T) {
	ctx := context.Background()
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	root := t.TempDir()
	old := filepath.Join(root, "old.jsonl")
	source := AttributionSource{Path: old, Kind: "codex", Historical: true}
	initial := `{"type":"session_meta","payload":{"cwd":"/old"}}
{"payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add old.py"}}}
`
	if err = os.WriteFile(old, []byte(initial), 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	// Populate the budget without reading command bodies or retaining raw transcripts.
	_, err = index.db.Exec(`WITH RECURSIVE seq(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM seq WHERE n<250000)
      INSERT OR REPLACE INTO attribution_operations SELECT ?,n,'padding-'||n,'M',0 FROM seq`, old)
	if err != nil {
		t.Fatal(err)
	}
	next := filepath.Join(root, "new.jsonl")
	if err = os.WriteFile(next, []byte(`{"payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add new.py"}}}`+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(ctx, AttributionSource{Path: next, Kind: "codex"}, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	var cwd string
	var position []byte
	if err = index.db.QueryRow("SELECT cwd,position FROM attribution_sources WHERE path=?", old).Scan(&cwd, &position); err != nil {
		t.Fatal(err)
	}
	if cwd != "" {
		t.Fatal("eviction retained prior CWD", cwd)
	}
	var before map[string]any
	if err = json.Unmarshal(position, &before); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(old, []byte(`{"payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add replacement.py"}}}`+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err = index.index(ctx, source, 4<<20, 4096); err != nil {
		t.Fatal(err)
	}
	if err = index.db.QueryRow("SELECT cwd,position FROM attribution_sources WHERE path=?", old).Scan(&cwd, &position); err != nil {
		t.Fatal(err)
	}
	var after map[string]any
	if err = json.Unmarshal(position, &after); err != nil {
		t.Fatal(err)
	}
	if cwd != "" || after["Generation"].(float64) <= before["Generation"].(float64) {
		t.Fatal("evicted generation reused", cwd, before, after)
	}
}

func TestAttributionIndexAbsentAndUnsupportedTranscriptsDoNotBlockReadiness(t *testing.T) {
	for _, agent := range []SessionFileAgent{{Kind: "codex", CWD: "/home"}, {Kind: "opencode", Transcript: "/not-indexable", CWD: "/home"}} {
		index, err := OpenAttributionIndex(t.TempDir(), nil)
		if err != nil {
			t.Fatal(err)
		}
		index.progress = AttributionProgress{State: "ready", Partial: false}
		if _, err = index.query(context.Background(), []SessionFileAgent{agent}, []string{"/home"}, 24); err != nil {
			t.Fatal(err)
		}
		if index.Progress().Partial || len(index.queue) != 0 {
			t.Fatal("ineligible transcript blocked readiness", agent, index.Progress())
		}
		if err = index.Close(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAttributionIndexGenerationGapSurvivesReplayAndRestart(t *testing.T) {
	for _, replacement := range []bool{false, true} {
		t.Run(fmt.Sprint(replacement), func(t *testing.T) {
			directory, root := t.TempDir(), t.TempDir()
			path := filepath.Join(root, "rollout-gap.jsonl")
			if err := os.WriteFile(path, []byte("{}\n{}\n{}\n"), 0600); err != nil {
				t.Fatal(err)
			}
			index, err := OpenAttributionIndex(directory, nil)
			if err != nil {
				t.Fatal(err)
			}
			source := AttributionSource{Path: path, Kind: "codex", Historical: true}
			if _, _, _, err = index.index(context.Background(), source, 4<<20, 4096); err != nil {
				t.Fatal(err)
			}
			target := path
			if replacement {
				target = path + ".replacement"
			}
			if err = os.WriteFile(target, []byte("{}\n"), 0600); err != nil {
				t.Fatal(err)
			}
			if replacement {
				if err = os.Rename(target, path); err != nil {
					t.Fatal(err)
				}
			}
			if _, _, _, err = index.index(context.Background(), source, 4<<20, 4096); err != nil {
				t.Fatal(err)
			}
			if index.Progress().Gaps != 1 || index.coverageIncomplete {
				t.Fatal("generation gap hidden or replay falsely incomplete", index.Progress(), index.coverageIncomplete)
			}
			if err = index.Close(); err != nil {
				t.Fatal(err)
			}
			index, err = OpenAttributionIndex(directory, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer index.Close()
			if index.Progress().Gaps != 1 || index.coverageIncomplete {
				t.Fatal("restart lost generation gap", index.Progress(), index.coverageIncomplete)
			}
		})
	}
}

func TestAttributionQueuedReadsCompleteRecordsWhileLaterRowsAppend(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	path := filepath.Join(t.TempDir(), "large.jsonl")
	header, footer := []byte(`{"type":"ignored","body":"`), []byte("\"}\n")
	data := append(append(header, bytes.Repeat([]byte("x"), maxSessionTranscriptLineBytes-1-len(header)-len(footer))...), footer...)
	if !json.Valid(data) {
		t.Fatal("large source fixture must be valid JSON")
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	small := filepath.Join(t.TempDir(), "small.jsonl")
	if err := os.WriteFile(small, []byte("{}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	for cycle := 0; cycle < 3; cycle++ {
		file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
		if err != nil {
			t.Fatal(err)
		}
		_, writeErr := file.Write([]byte("{}\n"))
		closeErr := file.Close()
		if writeErr != nil {
			t.Fatal(writeErr)
		}
		if closeErr != nil {
			t.Fatal(closeErr)
		}
		index.Enqueue(AttributionSource{Path: small, Kind: "codex"}, false)
		index.Enqueue(AttributionSource{Path: path, Kind: "codex"}, false)
		before, _ := index.ingestion.Metrics()
		charged := index.indexQueuedSources(context.Background())
		after, _ := index.ingestion.Metrics()
		if charged < after-before || charged > int64((12<<20)+(256<<10)) {
			t.Fatalf("cycle charged=%d actual=%d", charged, after-before)
		}
		t.Logf("append cycle %d charged=%d physical=%d bound=%d", cycle, charged, after-before, int64((12<<20)+(256<<10)))
		var encoded []byte
		if err := index.db.QueryRow("SELECT position FROM attribution_sources WHERE path=?", path).Scan(&encoded); err != nil {
			t.Fatal(err)
		}
		var position struct{ Offset int64 }
		if err := json.Unmarshal(encoded, &position); err != nil {
			t.Fatal(err)
		}
		if position.Offset < int64(len(data)) {
			t.Fatalf("cycle%d complete %d-byte row stalled at offset%d under unrelated appends", cycle, len(data), position.Offset)
		}
	}
}

func TestAttributionQueuedOversizedErrorsChargeActualReads(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	path := filepath.Join(t.TempDir(), "oversized.jsonl")
	if err := os.WriteFile(path, append(bytes.Repeat([]byte("x"), (8<<20)+1), '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	// A second source must not get a fresh, uncharged read after the first error.
	second := filepath.Join(t.TempDir(), "second.jsonl")
	if err := os.WriteFile(second, []byte("{}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	index.Enqueue(AttributionSource{Path: path, Kind: "codex"}, false)
	index.Enqueue(AttributionSource{Path: second, Kind: "codex"}, false)
	before, _ := index.ingestion.Metrics()
	charged := index.indexQueuedSources(context.Background())
	after, _ := index.ingestion.Metrics()
	if index.Progress().Error == "" {
		t.Fatal("oversized record was never rejected in its bounded turn")
	}
	if charged < after-before || charged > int64((12<<20)+(256<<10)) {
		t.Fatalf("failed cycle charged=%d actual=%d", charged, after-before)
	}
	index.mu.Lock()
	remaining := len(index.queue)
	index.mu.Unlock()
	if remaining != 1 {
		t.Fatalf("error exhausted cycle but processed second source: pending=%d", remaining)
	}
}

func TestAttributionFullFrameTurnsPreserveHistoricalShare(t *testing.T) {
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer index.Close()
	root := t.TempDir()
	header, footer := []byte(`{"type":"ignored","body":"`), []byte("\"}\n")
	data := append(append(header, bytes.Repeat([]byte("x"), maxSessionTranscriptLineBytes-len(header)-len(footer))...), footer...)
	data = append(data, []byte("{}\n")...)
	sources := map[string]AttributionSource{}
	for _, name := range []string{"archive", "live1", "live2", "live3"} {
		path := filepath.Join(root, name+".jsonl")
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
		sources[name] = AttributionSource{Path: path, Kind: "codex"}
	}
	index.Enqueue(sources["archive"], false)
	index.Enqueue(sources["live1"], true)
	index.Enqueue(sources["live2"], true)
	for turn := 0; turn < 3; turn++ {
		if turn == 2 {
			index.Enqueue(sources["live3"], true)
		}
		charged := index.indexQueuedSources(context.Background())
		if charged <= attributionCycleReadAdmissionBytes {
			t.Fatalf("fixture did not consume a full source turn: charged=%d", charged)
		}
		if charged > int64((12<<20)+(256<<10)) {
			t.Fatalf("cycle exceeded physical bound: %d", charged)
		}
	}
	var archiveCount, live3Count int
	if err := index.db.QueryRow("SELECT count(*) FROM attribution_sources WHERE path=?", sources["archive"].Path).Scan(&archiveCount); err != nil {
		t.Fatal(err)
	}
	if err := index.db.QueryRow("SELECT count(*) FROM attribution_sources WHERE path=?", sources["live3"].Path).Scan(&live3Count); err != nil {
		t.Fatal(err)
	}
	if archiveCount != 1 || live3Count != 0 {
		t.Fatalf("third full source turn must serve archive: archive=%d newer-live=%d", archiveCount, live3Count)
	}
}
