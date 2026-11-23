// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/cache"
	"github.com/keivenchang/yomux/internal/productenv"
)

type hostedIssueLink struct {
	Identifier string `json:"identifier"`
	Title      string `json:"title,omitempty"`
	URL        string `json:"url,omitempty"`
}

type hostedWorkRequest struct {
	Number                      int
	Provider, Title, URL, State string
	Linear                      []hostedIssueLink
	Draft                       bool
	Issues                      []hostedIssueLink
}

type hostedWorkKey struct{ provider, repo, source, branch string }

func (key hostedWorkKey) sourceRepo() string {
	if key.source != "" {
		return key.source
	}
	return key.repo
}

type hostedWorkEntry struct {
	requests []hostedWorkRequest
	expires  time.Time
}

type hostedWorkCache struct {
	mu      sync.Mutex
	entries *cache.LRU[hostedWorkKey, hostedWorkEntry]
	pending map[hostedWorkKey]bool
}

const hostedWorkLimit = 30

// Publish local metadata immediately. Hosted data joins the normal metadata
// refresh, under the same branch identity that initiated the lookup.
func (s *server) hostedWorkRequests(key hostedWorkKey) []hostedWorkRequest {
	if key.branch == "" || s.producerContext == nil {
		return nil
	}
	u, err := url.Parse(key.repo)
	if err != nil || (u.Host != "github.com" && u.Host != "gitlab.com") {
		return nil
	}
	c := &s.hostedWork
	c.mu.Lock()
	if c.entries == nil {
		c.entries = cache.NewLRU[hostedWorkKey, hostedWorkEntry](512)
		c.pending = make(map[hostedWorkKey]bool)
	}
	entry, _ := c.entries.Get(key)
	if time.Now().Before(entry.expires) || c.pending[key] || len(c.pending) >= 4 {
		c.mu.Unlock()
		return entry.requests
	}
	c.pending[key] = true
	c.mu.Unlock()
	if !s.startBackgroundWork(func() {
		ctx, cancel := context.WithTimeout(s.producerContext, 4*time.Second)
		defer cancel()
		requests, err := fetchHostedWork(ctx, key)
		next := hostedWorkEntry{requests: requests, expires: time.Now().Add(601 * time.Second)}
		if err != nil {
			// Don't retain an authenticated result after the provider stops
			// confirming access. Back off on errors and rate limits.
			next = hostedWorkEntry{expires: time.Now().Add(301 * time.Second)}
			slog.Debug("hosted work lookup unavailable", "provider", key.provider, "error", err)
		}
		c.mu.Lock()
		c.entries.Set(key, next)
		delete(c.pending, key)
		c.mu.Unlock()
		if len(next.requests) != 0 || len(entry.requests) != 0 {
			if topology, ok := s.cachedTmuxTopologySnapshot(); ok {
				s.queueMetadataRefresh(false, topology.sessions, topology.identities, nil, topology.generation)
			}
		}
	}) {
		c.mu.Lock()
		delete(c.pending, key)
		c.mu.Unlock()
	}
	return entry.requests
}

func fetchHostedWork(ctx context.Context, key hostedWorkKey) ([]hostedWorkRequest, error) {
	switch key.provider {
	case "github":
		return fetchGitHubWork(ctx, key)
	case "gitlab":
		return fetchGitLabWork(ctx, key)
	default:
		return nil, errors.New("unsupported hosted work provider")
	}
}

func fetchGitHubWork(ctx context.Context, key hostedWorkKey) ([]hostedWorkRequest, error) {
	if !hostedRepositoryAuthAllowed(key.repo) {
		return fetchPublicGitHubWork(ctx, key)
	}
	command := exec.CommandContext(ctx, "gh", "pr", "list", "--repo", key.repo, "--head="+key.branch, "--state", "all",
		"--limit", strconv.Itoa(hostedWorkLimit), "--json", "number,title,body,url,state,isDraft,headRefName,headRepository,headRepositoryOwner,closingIssuesReferences")
	command.Env = append(os.Environ(), "GH_PROMPT_DISABLED=1", "GH_NO_UPDATE_NOTIFIER=1", "GH_NO_EXTENSION_UPDATE_NOTIFIER=1")
	raw, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("GitHub CLI: %w", err)
	}
	return decodeGitHubWork(raw, key)
}

// An app viewer can read session metadata for more repositories than their own
// provider account can see. Operator credentials require explicit sharing.
func hostedRepositoryAuthAllowed(repo string) bool {
	for _, allowed := range strings.Split(productenv.Value("HOSTED_AUTH_REPOSITORIES"), ",") {
		if strings.EqualFold(strings.TrimSpace(allowed), repo) {
			return true
		}
	}
	return false
}

func fetchPublicGitHubWork(ctx context.Context, key hostedWorkKey) ([]hostedWorkRequest, error) {
	project := strings.TrimPrefix(key.repo, "https://github.com/")
	owner, _, _ := strings.Cut(strings.TrimPrefix(key.sourceRepo(), "https://github.com/"), "/")
	query := url.Values{"state": {"all"}, "head": {owner + ":" + key.branch}, "per_page": {strconv.Itoa(hostedWorkLimit)}}
	return decodePublicGitHubWork(ctx, "https://api.github.com/repos/"+project+"/pulls?"+query.Encode(), key)
}

func decodePublicGitHubWork(ctx context.Context, endpoint string, key hostedWorkKey) ([]hostedWorkRequest, error) {
	var rows []struct {
		Number             int
		Title, Body, State string
		URL                string     `json:"html_url"`
		MergedAt           *time.Time `json:"merged_at"`
		Draft              bool
		Head               struct {
			Ref  string
			Repo struct {
				FullName string `json:"full_name"`
			}
		}
	}
	if err := hostedWorkJSON(ctx, endpoint, "", &rows); err != nil {
		return nil, err
	}
	project := strings.TrimPrefix(key.sourceRepo(), "https://github.com/")
	requests := make([]hostedWorkRequest, 0, len(rows))
	for _, row := range rows {
		if row.Number < 1 || row.Head.Ref != key.branch || !strings.EqualFold(row.Head.Repo.FullName, project) ||
			!strings.EqualFold(row.URL, fmt.Sprintf("%s/pull/%d", key.repo, row.Number)) {
			continue
		}
		state := row.State
		if row.MergedAt != nil {
			state = "merged"
		}
		request := hostedWorkRequest{Number: row.Number, Provider: "github", Title: row.Title, URL: row.URL, State: state, Draft: row.Draft}
		request.addReferences(row.Body)
		requests = append(requests, request)
	}
	return requests, nil
}

func decodeGitHubWork(raw []byte, key hostedWorkKey) ([]hostedWorkRequest, error) {
	var rows []struct {
		Number                               int
		Title, Body, URL, State, HeadRefName string
		IsDraft                              bool
		HeadRepository                       struct{ Name string }
		HeadRepositoryOwner                  struct{ Login string }
		ClosingIssuesReferences              []struct {
			Number     int
			Title, URL string
		}
	}
	if err := json.Unmarshal(raw, &rows); err != nil {
		return nil, err
	}
	project := strings.TrimPrefix(key.sourceRepo(), "https://github.com/")
	requests := make([]hostedWorkRequest, 0, len(rows))
	for _, row := range rows {
		headProject := row.HeadRepositoryOwner.Login + "/" + row.HeadRepository.Name
		if row.Number < 1 || row.HeadRefName != key.branch || !strings.EqualFold(headProject, project) ||
			!strings.EqualFold(row.URL, fmt.Sprintf("%s/pull/%d", key.repo, row.Number)) {
			continue
		}
		request := hostedWorkRequest{Number: row.Number, Provider: key.provider, Title: row.Title, URL: row.URL, State: strings.ToLower(row.State), Draft: row.IsDraft}
		request.addReferences(row.Body)
		for _, issue := range row.ClosingIssuesReferences {
			if issue.Number > 0 && githubIssueURLPattern.MatchString(issue.URL) {
				request.Issues = append(request.Issues, hostedIssueLink{Identifier: "#" + strconv.Itoa(issue.Number), Title: issue.Title, URL: issue.URL})
			}
		}
		requests = append(requests, request)
	}
	return requests, nil
}

func fetchGitLabWork(ctx context.Context, key hostedWorkKey) ([]hostedWorkRequest, error) {
	project := strings.TrimPrefix(key.repo, "https://gitlab.com/")
	query := url.Values{"source_branch": {key.branch}, "scope": {"all"}, "per_page": {strconv.Itoa(hostedWorkLimit)}}
	endpoint := "https://gitlab.com/api/v4/projects/" + url.PathEscape(project) + "/merge_requests?" + query.Encode()
	token := ""
	if hostedRepositoryAuthAllowed(key.repo) {
		token = os.Getenv("GITLAB_TOKEN")
	}
	var sourceProject struct{ ID int }
	if key.sourceRepo() != key.repo {
		source := strings.TrimPrefix(key.sourceRepo(), "https://gitlab.com/")
		sourceToken := ""
		if hostedRepositoryAuthAllowed(key.sourceRepo()) {
			sourceToken = token
		}
		if err := hostedWorkJSON(ctx, "https://gitlab.com/api/v4/projects/"+url.PathEscape(source), sourceToken, &sourceProject); err != nil {
			return nil, err
		}
		if sourceProject.ID <= 0 {
			return nil, errors.New("GitLab source project has no ID")
		}
	}
	var rows []gitLabWorkRow
	if err := hostedWorkJSON(ctx, endpoint, token, &rows); err != nil {
		return nil, err
	}
	return gitLabWorkFromRows(rows, key, sourceProject.ID), nil
}

func hostedWorkJSON(ctx context.Context, endpoint, gitLabToken string, output any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	if gitLabToken != "" {
		request.Header.Set("PRIVATE-TOKEN", gitLabToken)
	}
	client := http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("hosted work HTTP %d", response.StatusCode)
	}
	return json.NewDecoder(io.LimitReader(response.Body, 2<<20)).Decode(output)
}

type gitLabWorkRow struct {
	IID                       int
	Title, Description, State string
	URL                       string `json:"web_url"`
	Branch                    string `json:"source_branch"`
	SourceProject             int    `json:"source_project_id"`
	TargetProject             int    `json:"target_project_id"`
	WorkInProgress            bool   `json:"work_in_progress"`
	Draft                     bool
}

func gitLabWorkFromRows(rows []gitLabWorkRow, key hostedWorkKey, sourceID int) []hostedWorkRequest {
	requests := make([]hostedWorkRequest, 0, len(rows))
	for _, row := range rows {
		expectedSource := sourceID
		if expectedSource == 0 {
			expectedSource = row.TargetProject
		}
		if row.IID < 1 || row.Branch != key.branch || row.SourceProject == 0 || row.SourceProject != expectedSource ||
			row.URL != fmt.Sprintf("%s/-/merge_requests/%d", key.repo, row.IID) {
			continue
		}
		state := strings.ToLower(row.State)
		if state == "opened" {
			state = "open"
		}
		request := hostedWorkRequest{Number: row.IID, Provider: key.provider, Title: row.Title, URL: row.URL, State: state, Draft: row.Draft || row.WorkInProgress}
		request.addReferences(row.Description)
		requests = append(requests, request)
	}
	return requests
}

var (
	githubIssueURLPattern = regexp.MustCompile(`https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*\b`)
	linearIssueURLPattern = regexp.MustCompile(`https://linear\.app/[A-Za-z0-9_-]+/issue/([A-Z][A-Z0-9]{1,11}-[1-9][0-9]*)(?:/[A-Za-z0-9_-]+)?`)
	linearIssueIDPattern  = regexp.MustCompile(`\b[A-Z][A-Z0-9]{1,11}-[1-9][0-9]*\b`)
)

func hostedBranchLinks(texts ...string) (linear map[string]hostedIssueLink, github map[string]hostedIssueLink) {
	linear, github = make(map[string]hostedIssueLink), make(map[string]hostedIssueLink)
	for _, text := range texts {
		for _, parts := range linearIssueURLPattern.FindAllStringSubmatch(text, hostedWorkLimit) {
			linear[parts[1]] = hostedIssueLink{Identifier: parts[1], URL: parts[0]}
		}
		for _, id := range linearIssueIDPattern.FindAllString(text, hostedWorkLimit) {
			if _, exists := linear[id]; !exists {
				linear[id] = hostedIssueLink{Identifier: id}
			}
		}
		for _, href := range githubIssueURLPattern.FindAllString(text, hostedWorkLimit) {
			_, number, _ := strings.Cut(href, "/issues/")
			github[href] = hostedIssueLink{Identifier: "#" + number, URL: href}
		}
	}
	return linear, github
}

func (s *server) addHostedBranchMetadata(graph map[string]any, snapshot goGitSnapshot, repository, worktree, branch map[string]any) {
	linear, issues := hostedBranchLinks(snapshot.Branch, snapshot.Head)
	repo, provider := snapshot.HostedRemote["base_url"], snapshot.HostedRemote["provider"]
	if repo != "" {
		repoID := goGraphID("hosted-repo", repo)
		graph["hosted_repositories"].(map[string]any)[repoID] = map[string]any{"id": repoID, "url": repo, "provider": provider}
		repository["hosted_repository_id"] = repoID
		worktree["hosted_repository_id"] = repoID
		requests := s.hostedWorkRequests(hostedWorkKey{provider: provider, repo: repo, branch: snapshot.Branch})
		upstream := snapshot.HostedUpstream["base_url"]
		if upstream != "" && upstream != repo && snapshot.HostedUpstream["provider"] == provider &&
			strings.HasPrefix(upstream, "https://"+provider+".com/") && strings.HasPrefix(repo, "https://"+provider+".com/") {
			upstreamRequests := s.hostedWorkRequests(hostedWorkKey{provider: provider, repo: upstream, source: repo, branch: snapshot.Branch})
			requests = append(append([]hostedWorkRequest(nil), requests...), upstreamRequests...)
		}
		for _, request := range requests {
			id := goGraphID("hosted-request", request.URL)
			graph["pull_requests"].(map[string]any)[id] = map[string]any{
				"id": id, "number": request.Number, "title": request.Title, "url": request.URL,
				"provider": request.Provider, "state": request.State, "draft": request.Draft,
				"merged": request.State == "merged",
			}
			appendStringValue(branch, "pull_request_ids", id)
			appendStringValue(worktree, "pull_request_ids", id)
			worktree["has_current_pull_request"] = true
			for _, issue := range request.Issues {
				issues[issue.URL] = issue
			}
			for _, issue := range request.Linear {
				id := issue.Identifier
				if existing, exists := linear[id]; !exists || (existing.URL == "" && issue.URL != "") {
					linear[id] = issue
				}
			}
		}
	}
	for _, key := range sortedHostedIssueKeys(linear) {
		id := goGraphID("linear-issue", key)
		graph["linear_issues"].(map[string]any)[id] = linear[key]
		appendStringValue(branch, "linear_issue_ids", id)
	}
	githubIssues := make([]hostedIssueLink, 0, len(issues))
	for _, href := range sortedHostedIssueKeys(issues) {
		githubIssues = append(githubIssues, issues[href])
	}
	branch["github_issues"] = githubIssues
}

func sortedHostedIssueKeys(issues map[string]hostedIssueLink) []string {
	keys := make([]string, 0, len(issues))
	for key := range issues {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func (request *hostedWorkRequest) addReferences(body string) {
	linear, github := hostedBranchLinks(request.Title, body)
	for _, key := range sortedHostedIssueKeys(linear) {
		request.Linear = append(request.Linear, linear[key])
	}
	for _, key := range sortedHostedIssueKeys(github) {
		request.Issues = append(request.Issues, github[key])
	}
}
