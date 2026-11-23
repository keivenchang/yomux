// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHostedPublicGitHubWorkKeepsTheForkAndMergedState(t *testing.T) {
	host := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "" {
			t.Error("public lookup sent credentials")
		}
		_, _ = w.Write([]byte(`[
			{"number":7,"title":"ABC-123","html_url":"https://github.com/team/repo/pull/7","state":"closed","merged_at":"2026-11-19T00:00:00Z","head":{"ref":"feature","repo":{"full_name":"fork/repo"}}},
			{"number":8,"html_url":"https://github.com/team/repo/pull/8","state":"open","head":{"ref":"feature","repo":{"full_name":"other/repo"}}}
		]`))
	}))
	defer host.Close()
	key := hostedWorkKey{provider: "github", repo: "https://github.com/team/repo", source: "https://github.com/fork/repo", branch: "feature"}
	requests, err := decodePublicGitHubWork(context.Background(), host.URL, key)
	if err != nil || len(requests) != 1 || requests[0].Number != 7 || requests[0].State != "merged" {
		t.Fatalf("public fork requests = %#v, error = %v", requests, err)
	}
}

func TestHostedWorkMatchesTheCurrentBranchAndRepository(t *testing.T) {
	github := hostedWorkKey{provider: "github", repo: "https://github.com/team/repo", branch: "team/ABC-123"}
	prs, err := decodeGitHubWork([]byte(`[
		{"number":7,"title":"ABC-123","url":"https://github.com/team/repo/pull/7","state":"MERGED","headRefName":"team/ABC-123","headRepository":{"name":"repo"},"headRepositoryOwner":{"login":"team"},"closingIssuesReferences":[{"number":42,"title":"Fix this","url":"https://github.com/team/repo/issues/42"}]},
		{"number":8,"url":"https://github.com/team/repo/pull/8","state":"OPEN","headRefName":"team/ABC-123","headRepository":{"name":"repo"},"headRepositoryOwner":{"login":"other"}}
	]`), github)
	if err != nil || len(prs) != 1 || prs[0].State != "merged" || len(prs[0].Issues) != 1 || prs[0].Issues[0].Identifier != "#42" {
		t.Fatalf("GitHub requests = %#v, error = %v", prs, err)
	}
	gitlab := hostedWorkKey{provider: "gitlab", repo: "https://gitlab.com/team/nested/repo", branch: "team/ABC-123"}
	var rows []gitLabWorkRow
	err = json.Unmarshal([]byte(`[
		{"iid":7,"web_url":"https://gitlab.com/team/nested/repo/-/merge_requests/7","state":"opened","source_branch":"team/ABC-123","source_project_id":3,"target_project_id":3,"draft":true},
		{"iid":8,"web_url":"https://gitlab.com/team/nested/repo/-/merge_requests/8","state":"opened","source_branch":"team/ABC-123","source_project_id":4,"target_project_id":3}
	]`), &rows)
	mrs := gitLabWorkFromRows(rows, gitlab, 0)
	if err != nil || len(mrs) != 1 || !mrs[0].Draft || mrs[0].State != "open" {
		t.Fatalf("GitLab requests = %#v, error = %v", mrs, err)
	}
}

func TestHostedBranchMetadataPublishesRepositoryAndIssueLinks(t *testing.T) {
	graph := emptyGoWorkGraph(1)
	repo, worktree, branch := map[string]any{}, map[string]any{}, map[string]any{}
	(&server{}).addHostedBranchMetadata(graph, goGitSnapshot{
		Branch:       "team/ABC-123",
		Head:         "See https://linear.app/workspace/issue/ABC-123 and https://github.com/team/repo/issues/72",
		HostedRemote: map[string]string{"provider": "gitlab", "base_url": "https://gitlab.com/team/repo"},
	}, repo, worktree, branch)
	if repo["hosted_repository_id"] == nil || repo["hosted_repository_id"] != worktree["hosted_repository_id"] {
		t.Fatalf("missing shared hosted repository edge: %v %v", repo, worktree)
	}
	linear := graph["linear_issues"].(map[string]any)
	if len(linear) != 1 || linear[goGraphID("linear-issue", "ABC-123")].(hostedIssueLink).URL != "https://linear.app/workspace/issue/ABC-123" {
		t.Fatalf("Linear links = %#v", linear)
	}
	issues := branch["github_issues"].([]hostedIssueLink)
	if len(issues) != 1 || issues[0].URL != "https://github.com/team/repo/issues/72" {
		t.Fatalf("GitHub issues = %#v", issues)
	}
}
