// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"net/url"
	"sync/atomic"
	"testing"
	"time"
)

func TestSessionFilesRequestCarriesVisibleRepositoryPathsIntoItsCacheIdentity(t *testing.T) {
	query := url.Values{}
	query.Set("session", "finder")
	query.Add("repo_path", "/repos/zeta")
	query.Add("repo_path", "/repos/alpha")
	query.Add("repo_path", "/repos/zeta")
	query.Add("repo_path", " ")
	request, finderErr := sessionFilesRequestFromQuery(query)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if len(request.repoPaths) != 2 || request.repoPaths[0] != "/repos/alpha" || request.repoPaths[1] != "/repos/zeta" {
		t.Fatalf("repository paths = %#v, want sorted unique paths", request.repoPaths)
	}
	withoutPaths := request
	withoutPaths.repoPaths = nil
	if sessionFilesRequestDescriptor(request) == sessionFilesRequestDescriptor(withoutPaths) {
		t.Fatal("visible repository paths do not participate in the session-files cache identity")
	}
}

func TestSessionFilesRequestNormalizesLegacyRefAliasesWithCanonicalPrecedence(t *testing.T) {
	query := url.Values{
		"from_ref":  {"alias-from"},
		"to_ref":    {"alias-to"},
		"repo_refs": {`{"/repo":{"from_ref":"nested-from","to_ref":"nested-to"}}`},
	}
	request, finderErr := sessionFilesRequestFromQuery(query)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if request.fromRef != "alias-from" || request.toRef != "alias-to" || request.repoRefs["/repo"]["from"] != "nested-from" || request.repoRefs["/repo"]["to"] != "nested-to" {
		t.Fatalf("legacy refs = %#v", request)
	}
	query.Set("from", "canonical-from")
	query.Set("to", "canonical-to")
	query.Set("refs", `{"/repo":{"from":"canonical-nested-from","from_ref":"ignored","to":"canonical-nested-to"}}`)
	request, finderErr = sessionFilesRequestFromQuery(query)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if request.fromRef != "canonical-from" || request.toRef != "canonical-to" || request.repoRefs["/repo"]["from"] != "canonical-nested-from" || request.repoRefs["/repo"]["to"] != "canonical-nested-to" {
		t.Fatalf("canonical refs did not win: %#v", request)
	}
}

func TestSessionFilesStreamCandidatesPrioritizeVisibleRepositoryPaths(t *testing.T) {
	visible := []string{"/repos/alpha", "/repos/beta"}
	liveRoots := []string{"/home/user", "/repos/alpha"}
	candidates := sessionFilesStreamRoots(visible, liveRoots)
	if len(candidates) != 3 || candidates[0] != "/repos/alpha" || candidates[1] != "/repos/beta" || candidates[2] != "/home/user" {
		t.Fatalf("stream candidates = %#v, want visible paths first", candidates)
	}
	plan := newSessionFilesPlan(sessionFilesRequest{repoPaths: visible})
	if got := sessionFilesPlanRoots(plan, liveRoots); len(got) != len(candidates) || got[0] != candidates[0] || got[1] != candidates[1] || got[2] != candidates[2] {
		t.Fatalf("canonical plan roots = %#v, want %#v", got, candidates)
	}
}

func TestSessionFilesCachedChunksOmitUnusedProgressMetadata(t *testing.T) {
	payload := map[string]any{
		"session": "finder",
		"hours":   float64(24),
		"files": []map[string]any{
			{"repo": "/repos/alpha", "path": "a.go"},
			{"repo": "/repos/beta", "path": "b.go"},
		},
		"repos": []map[string]any{
			{"repo": "/repos/alpha"},
			{"repo": "/repos/beta"},
		},
		"refs_by_repo": map[string]any{},
	}
	chunks := sessionFilesCachedChunks(payload)
	if len(chunks) != 2 {
		t.Fatalf("cached chunks = %#v, want one per repository", chunks)
	}
	for index, chunk := range chunks {
		if _, present := chunk["repo_index"]; present {
			t.Fatalf("cached chunk %d exposes unused repo_index metadata: %#v", index, chunk)
		}
		if _, present := chunk["repo_total"]; present {
			t.Fatalf("cached chunk %d exposes unused repo_total metadata: %#v", index, chunk)
		}
		files, ok := chunk["files"].([]map[string]any)
		if !ok || len(files) != 1 || files[0]["repo"] != chunk["repos"].([]map[string]any)[0]["repo"] {
			t.Fatalf("cached chunk %d does not contain only its repository's files: %#v", index, chunk)
		}
	}
}

func TestRunSessionFilesBatchBoundsWorkersAndKeepsSiblingResults(t *testing.T) {
	sessions := []string{"bad", "one", "two", "three", "four", "five"}
	started := make(chan struct{}, len(sessions))
	release := make(chan struct{})
	var active atomic.Int32
	var maximum atomic.Int32
	result := make(chan struct {
		payloads map[string]any
		statuses map[string]any
		err      error
	}, 1)
	go func() {
		payloads, statuses, err := runSessionFilesBatch(context.Background(), sessions, func(_ context.Context, session string) (map[string]any, int) {
			current := active.Add(1)
			for observed := maximum.Load(); current > observed && !maximum.CompareAndSwap(observed, current); observed = maximum.Load() {
			}
			started <- struct{}{}
			<-release
			active.Add(-1)
			if session == "bad" {
				return map[string]any{"session": session, "error": "unavailable"}, 503
			}
			return map[string]any{"session": session}, 200
		})
		result <- struct {
			payloads map[string]any
			statuses map[string]any
			err      error
		}{payloads: payloads, statuses: statuses, err: err}
	}()
	for range sessionFilesBatchWorkers {
		select {
		case <-started:
		case <-time.After(time.Second):
			close(release)
			t.Fatal("batch did not start the bounded worker set")
		}
	}
	select {
	case <-started:
		close(release)
		t.Fatal("batch exceeded its worker bound before any item completed")
	default:
	}
	close(release)
	completed := <-result
	if completed.err != nil {
		t.Fatal(completed.err)
	}
	if maximum.Load() != sessionFilesBatchWorkers {
		t.Fatalf("maximum concurrent batch items = %d, want %d", maximum.Load(), sessionFilesBatchWorkers)
	}
	if completed.statuses["bad"] != 503 || completed.statuses["one"] != 200 || len(completed.payloads) != len(sessions) {
		t.Fatalf("batch dropped sibling results: statuses=%#v payloads=%#v", completed.statuses, completed.payloads)
	}
}
