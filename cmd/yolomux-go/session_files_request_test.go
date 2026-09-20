// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"net/url"
	"testing"
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

func TestSessionFilesStreamCandidatesPrioritizeVisibleRepositoryPaths(t *testing.T) {
	visible := []string{"/repos/alpha", "/repos/beta"}
	liveRoots := []string{"/home/user", "/repos/alpha"}
	candidates := sessionFilesStreamRoots(visible, liveRoots)
	if len(candidates) != 3 || candidates[0] != "/repos/alpha" || candidates[1] != "/repos/beta" || candidates[2] != "/home/user" {
		t.Fatalf("stream candidates = %#v, want visible paths first", candidates)
	}
}
