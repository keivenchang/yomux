// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	maxSessionFileRoots       = 64
	minSessionFilesHours      = 0.25
	maxUntrackedLineCountSize = 2 * 1024 * 1024
)

// SessionFiles projects the Git changes belonging to the supplied live session roots. The
// caller owns session discovery; this package owns repository authorization, Git execution, and
// the response shape. Keeping those boundaries separate means a later statusd/Tabber owner can
// provide richer session attribution without duplicating the Git security path.
func (s *Service) SessionFiles(ctx context.Context, session string, roots []string, hours float64, fromRef, toRef string, repoRefs map[string]map[string]string) (map[string]any, *Error) {
	return s.SessionFilesWithAgents(ctx, session, roots, nil, hours, fromRef, toRef, repoRefs)
}

type sessionFilesDiscovery struct {
	session        string
	hours          float64
	fromRef        string
	toRef          string
	repoRefs       map[string]map[string]string
	candidateRoots []string
	repositories   []string
	touched        map[string]*sessionFileAttribution
}

func sessionFilesCandidateRoots(roots []string, agents []SessionFileAgent) []string {
	candidateRoots := make([]string, 0, len(roots)+len(agents))
	seen := make(map[string]bool, len(roots)+len(agents))
	add := func(raw string) {
		root := strings.TrimSpace(raw)
		if root == "" || seen[root] {
			return
		}
		seen[root] = true
		candidateRoots = append(candidateRoots, root)
	}
	for _, root := range roots {
		add(root)
	}
	for _, agent := range agents {
		add(agent.CWD)
	}
	if len(candidateRoots) > maxSessionFileRoots {
		candidateRoots = candidateRoots[:maxSessionFileRoots]
	}
	return candidateRoots
}

func sessionFilesDiscoveryInputs(ctx context.Context, roots []string, agents []SessionFileAgent, hours float64) ([]string, map[string]*sessionFileAttribution) {
	candidateRoots := sessionFilesCandidateRoots(roots, agents)
	touched := collectSessionFileAttributions(ctx, agents, candidateRoots, hours)
	return candidateRoots, touched
}

func (s *Service) sessionFilesRepositoryForPath(ctx context.Context, rawRoot string) (string, *Error) {
	if ctx.Err() != nil {
		return "", cancelled(rawRoot)
	}
	root := strings.TrimSpace(rawRoot)
	if root == "" {
		return "", nil
	}
	scope, finderErr := s.openGitHistoryScope(root)
	if finderErr != nil {
		return "", finderErr
	}
	repo := filepath.Clean(scope.repo)
	scope.close()
	return repo, nil
}

func (s *Service) discoverSessionFiles(ctx context.Context, session string, roots []string, agents []SessionFileAgent, hours float64, fromRef, toRef string, repoRefs map[string]map[string]string) (sessionFilesDiscovery, *Error) {
	hours = boundedSessionFilesHours(hours)
	candidateRoots, touched := sessionFilesDiscoveryInputs(ctx, roots, agents, hours)
	repositories := make([]string, 0, len(candidateRoots))
	seen := make(map[string]bool)
	for _, rawRoot := range candidateRoots {
		root := strings.TrimSpace(rawRoot)
		if root == "" || seen[root] {
			continue
		}
		repo, finderErr := s.sessionFilesRepositoryForPath(ctx, root)
		if finderErr != nil {
			if finderErr.Code == "not_git_repo" {
				continue
			}
			return sessionFilesDiscovery{}, finderErr
		}
		if !seen[repo] {
			seen[repo] = true
			repositories = append(repositories, repo)
		}
	}
	// Transcript paths can identify a repository even when an agent was launched from $HOME or
	// its live pane has already disappeared. Resolve existing touched paths through the same Git
	// scope owner, while missing paths remain attributable through the known candidate roots.
	for path := range touched {
		if ctx.Err() != nil {
			break
		}
		repo, finderErr := s.sessionFilesRepositoryForPath(ctx, path)
		if finderErr != nil {
			continue
		}
		if !seen[repo] {
			seen[repo] = true
			repositories = append(repositories, repo)
		}
	}
	sort.Strings(repositories)
	return sessionFilesDiscovery{
		session: session, hours: hours, fromRef: fromRef, toRef: toRef, repoRefs: repoRefs,
		candidateRoots: candidateRoots, repositories: repositories, touched: touched,
	}, nil
}

func (s *Service) sessionFilesRepositoryViews(ctx context.Context, discovery sessionFilesDiscovery, emit func(int, int, sessionFilesRepositoryView) error) ([]sessionFilesRepositoryView, *Error) {
	views := make([]sessionFilesRepositoryView, 0, len(discovery.repositories))
	for index, repo := range discovery.repositories {
		view, finderErr := s.sessionFilesRepository(ctx, discovery.session, repo, discovery.fromRef, discovery.toRef, discovery.repoRefs, discovery.touched)
		if finderErr != nil {
			return nil, finderErr
		}
		views = append(views, view)
		if emit != nil {
			if err := emit(index, len(discovery.repositories), view); err != nil {
				if ctx.Err() != nil {
					return nil, cancelled(repo)
				}
				return nil, &Error{Status: http.StatusInternalServerError, Code: "session_files_stream_failed", MessageKey: "common.requestFailed", Path: repo, Retryable: true, Message: err.Error()}
			}
		}
	}
	return views, nil
}

func sessionFilesPayloadFromViews(discovery sessionFilesDiscovery, views []sessionFilesRepositoryView) map[string]any {
	files := make([]map[string]any, 0)
	repos := make([]map[string]any, 0, len(views))
	refsByRepo := make(map[string]any, len(views))
	warnings := make([]any, 0)
	for _, view := range views {
		files = append(files, view.files...)
		repos = append(repos, view.repo)
		repo, _ := view.repo["repo"].(string)
		refsByRepo[repo] = view.refs
		if view.warning != "" {
			warnings = append(warnings, view.warning)
		}
	}
	sort.SliceStable(files, func(i, j int) bool {
		left, _ := files[i]["mtime"].(int64)
		right, _ := files[j]["mtime"].(int64)
		if left != right {
			return left > right
		}
		leftPath, _ := files[i]["path"].(string)
		rightPath, _ := files[j]["path"].(string)
		return leftPath < rightPath
	})
	return map[string]any{
		"session":      discovery.session,
		"hours":        discovery.hours,
		"files":        files,
		"repos":        repos,
		"refs_by_repo": refsByRepo,
		"from_ref":     sessionFilesPayloadRef(discovery.fromRef, "default"),
		"to_ref":       sessionFilesPayloadRef(discovery.toRef, "base"),
		"errors":       []any{},
		"warnings":     warnings,
		"loaded":       true,
	}
}

func sessionFilesRepositoryChunk(discovery sessionFilesDiscovery, index, total int, view sessionFilesRepositoryView, replace bool) map[string]any {
	repo, _ := view.repo["repo"].(string)
	warnings := []any{}
	if view.warning != "" {
		warnings = append(warnings, view.warning)
	}
	return map[string]any{
		"session": discovery.session, "hours": discovery.hours, "files": view.files,
		"repos": []map[string]any{view.repo}, "refs_by_repo": map[string]any{repo: view.refs},
		"from_ref": sessionFilesPayloadRef(discovery.fromRef, "default"),
		"to_ref":   sessionFilesPayloadRef(discovery.toRef, "base"),
		"warnings": warnings, "errors": []any{}, "loaded": false,
		"replace_repo": replace,
		"repo_index":   index, "repo_total": total,
	}
}

// SessionFilesWithAgents is the parity owner used by the HTTP route. Roots provide the live
// filesystem scope; agents provide transcript evidence. The legacy SessionFiles wrapper remains
// intentionally equivalent for callers that only have pane roots.
func (s *Service) SessionFilesWithAgents(ctx context.Context, session string, roots []string, agents []SessionFileAgent, hours float64, fromRef, toRef string, repoRefs map[string]map[string]string) (map[string]any, *Error) {
	discovery, finderErr := s.discoverSessionFiles(ctx, session, roots, agents, hours, fromRef, toRef, repoRefs)
	if finderErr != nil {
		return nil, finderErr
	}
	views, finderErr := s.sessionFilesRepositoryViews(ctx, discovery, nil)
	if finderErr != nil {
		return nil, finderErr
	}
	return sessionFilesPayloadFromViews(discovery, views), nil
}

// SessionFilesWithAgentsStream uses the same discovery and repository computation owner as the
// aggregate route, but publishes one complete repository view as soon as that repository finishes.
// The returned aggregate payload is used by the HTTP owner to preserve the existing cache shape.
func (s *Service) SessionFilesWithAgentsStream(ctx context.Context, session string, roots []string, agents []SessionFileAgent, hours float64, fromRef, toRef string, repoRefs map[string]map[string]string, emit func(map[string]any) error) (map[string]any, *Error) {
	boundedHours := boundedSessionFilesHours(hours)
	candidateRoots := sessionFilesCandidateRoots(roots, agents)
	// Do not scan current or historical transcripts before the first Git repository has painted.
	// That scan is useful attribution enrichment, but it can walk many large JSONL files; the
	// Finder's first repository chunk must be driven by the cheap root discovery plus Git diff only.
	touched := make(map[string]*sessionFileAttribution)
	discovery := sessionFilesDiscovery{
		session: session, hours: boundedHours, fromRef: fromRef, toRef: toRef, repoRefs: repoRefs,
		candidateRoots: candidateRoots, touched: touched,
	}
	views := make([]sessionFilesRepositoryView, 0, len(candidateRoots))
	seen := make(map[string]bool)
	candidates := append([]string(nil), candidateRoots...)
	for path := range touched {
		candidates = append(candidates, path)
	}
	for _, rawRoot := range candidates {
		if ctx.Err() != nil {
			return nil, cancelled(session)
		}
		repo, finderErr := s.sessionFilesRepositoryForPath(ctx, rawRoot)
		if finderErr != nil {
			if finderErr.Code == "not_git_repo" {
				continue
			}
			return nil, finderErr
		}
		if seen[repo] {
			continue
		}
		seen[repo] = true
		view, finderErr := s.sessionFilesRepository(ctx, session, repo, fromRef, toRef, repoRefs, touched)
		if finderErr != nil {
			return nil, finderErr
		}
		index := len(views)
		views = append(views, view)
		if emit != nil {
			if err := emit(sessionFilesRepositoryChunk(discovery, index, 0, view, false)); err != nil {
				if ctx.Err() != nil {
					return nil, cancelled(repo)
				}
				return nil, &Error{Status: http.StatusInternalServerError, Code: "session_files_stream_failed", MessageKey: "common.requestFailed", Path: repo, Retryable: true, Message: err.Error()}
			}
		}
	}
	// Attribution is deliberately a second phase. It can discover a repository that is no longer
	// represented by a live pane, or add agent/window metadata to files already emitted above. Those
	// updates replace one repository at a time, so the browser stays interactive throughout the scan.
	touched = collectSessionFileAttributions(ctx, agents, candidateRoots, boundedHours)
	if ctx.Err() != nil {
		return nil, cancelled(session)
	}
	discovery.touched = touched
	for path := range touched {
		if ctx.Err() != nil {
			return nil, cancelled(session)
		}
		repo, finderErr := s.sessionFilesRepositoryForPath(ctx, path)
		if finderErr != nil {
			continue
		}
		if seen[repo] {
			index := -1
			for candidateIndex, view := range views {
				currentRepo, _ := view.repo["repo"].(string)
				if currentRepo == repo {
					index = candidateIndex
					break
				}
			}
			if index < 0 || len(sessionFileAttributionsForRepo(touched, repo)) == 0 {
				continue
			}
			view, repositoryErr := s.sessionFilesRepository(ctx, session, repo, fromRef, toRef, repoRefs, touched)
			if repositoryErr != nil {
				return nil, repositoryErr
			}
			views[index] = view
			if emit != nil {
				if err := emit(sessionFilesRepositoryChunk(discovery, index, 0, view, true)); err != nil {
					if ctx.Err() != nil {
						return nil, cancelled(repo)
					}
					return nil, &Error{Status: http.StatusInternalServerError, Code: "session_files_stream_failed", MessageKey: "common.requestFailed", Path: repo, Retryable: true, Message: err.Error()}
				}
			}
			continue
		}
		seen[repo] = true
		view, repositoryErr := s.sessionFilesRepository(ctx, session, repo, fromRef, toRef, repoRefs, touched)
		if repositoryErr != nil {
			return nil, repositoryErr
		}
		index := len(views)
		views = append(views, view)
		if emit != nil {
			if err := emit(sessionFilesRepositoryChunk(discovery, index, 0, view, false)); err != nil {
				if ctx.Err() != nil {
					return nil, cancelled(repo)
				}
				return nil, &Error{Status: http.StatusInternalServerError, Code: "session_files_stream_failed", MessageKey: "common.requestFailed", Path: repo, Retryable: true, Message: err.Error()}
			}
		}
	}
	sort.SliceStable(views, func(i, j int) bool {
		left, _ := views[i].repo["repo"].(string)
		right, _ := views[j].repo["repo"].(string)
		return left < right
	})
	discovery.repositories = make([]string, 0, len(views))
	for _, view := range views {
		repo, _ := view.repo["repo"].(string)
		discovery.repositories = append(discovery.repositories, repo)
	}
	return sessionFilesPayloadFromViews(discovery, views), nil
}

// AggregateSessionFiles combines the already-scoped session products into the same aggregate
// envelope Python returns when /api/session-files omits its session parameter. The server owns
// session discovery and calls SessionFiles once per live session; this owner only merges the
// bounded product rows and keeps the single-session and aggregate response shapes aligned.
func AggregateSessionFiles(payloads []map[string]any, hours float64, fromRef, toRef string) map[string]any {
	files := make([]map[string]any, 0)
	repos := make(map[string]map[string]any)
	refsByRepo := make(map[string]any)
	errorsPayload := make([]any, 0)
	warnings := make([]any, 0)
	for _, payload := range payloads {
		if payload == nil {
			continue
		}
		if rows, ok := payload["files"].([]map[string]any); ok {
			files = append(files, rows...)
		}
		if refs, ok := payload["refs_by_repo"].(map[string]any); ok {
			for repo, value := range refs {
				refsByRepo[repo] = value
			}
		}
		if values, ok := payload["errors"].([]any); ok {
			errorsPayload = append(errorsPayload, values...)
		}
		if values, ok := payload["warnings"].([]any); ok {
			warnings = append(warnings, values...)
		}
		rows, ok := payload["repos"].([]map[string]any)
		if !ok {
			continue
		}
		for _, row := range rows {
			key, _ := row["repo"].(string)
			existing, present := repos[key]
			if !present {
				existing = map[string]any{
					"repo": key, "count": 0, "touched_count": 0, "added": 0, "removed": 0,
					"from_ref": sessionFilesPayloadRef(fromRef, "default"),
					"to_ref":   sessionFilesPayloadRef(toRef, "base"), "error": "",
				}
				repos[key] = existing
			}
			for _, field := range []string{"count", "touched_count", "added", "removed"} {
				existing[field] = sessionFilesNumber(existing[field]) + sessionFilesNumber(row[field])
			}
			for _, field := range []string{"branch", "from_ref", "to_ref", "error", "error_message"} {
				if _, already := existing[field]; already && fmt.Sprint(existing[field]) != "" {
					continue
				}
				if value, present := row[field]; present {
					existing[field] = value
				}
			}
		}
	}
	sort.SliceStable(files, func(i, j int) bool {
		left := sessionFilesNumber(files[i]["mtime"])
		right := sessionFilesNumber(files[j]["mtime"])
		if left != right {
			return left > right
		}
		leftPath, _ := files[i]["path"].(string)
		rightPath, _ := files[j]["path"].(string)
		if leftPath != rightPath {
			return leftPath < rightPath
		}
		leftSession, _ := files[i]["session"].(string)
		rightSession, _ := files[j]["session"].(string)
		return leftSession < rightSession
	})
	repositoryRows := make([]map[string]any, 0, len(repos))
	for _, row := range repos {
		repositoryRows = append(repositoryRows, row)
	}
	sort.SliceStable(repositoryRows, func(i, j int) bool {
		left, _ := repositoryRows[i]["repo"].(string)
		right, _ := repositoryRows[j]["repo"].(string)
		return left < right
	})
	return map[string]any{
		"session": "", "hours": boundedSessionFilesHours(hours), "files": files,
		"repos": repositoryRows, "refs_by_repo": refsByRepo, "from_ref": sessionFilesPayloadRef(fromRef, "default"),
		"to_ref": sessionFilesPayloadRef(toRef, "base"), "errors": errorsPayload, "warnings": warnings, "loaded": true,
	}
}

func sessionFilesNumber(value any) int {
	switch number := value.(type) {
	case int:
		return number
	case int64:
		return int(number)
	case float64:
		return int(number)
	default:
		return 0
	}
}

type sessionFilesRepositoryView struct {
	files   []map[string]any
	repo    map[string]any
	refs    []map[string]any
	warning string
}

func sessionFilesPayloadRef(raw, fallback string) string {
	value := strings.TrimSpace(raw)
	if value == "" {
		return fallback
	}
	return value
}

func boundedSessionFilesHours(value float64) float64 {
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return 24.0
	}
	if value < minSessionFilesHours {
		return minSessionFilesHours
	}
	if value > maxSessionFilesHours {
		return maxSessionFilesHours
	}
	return value
}

func canonicalSessionFilesPath(raw string) string {
	path := strings.TrimSpace(raw)
	if path == "" {
		return ""
	}
	clean := filepath.Clean(path)
	if resolved, err := filepath.EvalSymlinks(clean); err == nil {
		return filepath.Clean(resolved)
	}
	if absolute, err := filepath.Abs(clean); err == nil {
		return filepath.Clean(absolute)
	}
	return clean
}

func sessionFilesRepoRefMatches(key, repo string) bool {
	return canonicalSessionFilesPath(key) == canonicalSessionFilesPath(repo)
}

func sessionFilesRefsForRepo(repo string, fromRef, toRef string, repoRefs map[string]map[string]string) (string, string) {
	if repoRefs != nil {
		for key, refs := range repoRefs {
			if !sessionFilesRepoRefMatches(key, repo) {
				continue
			}
			if value := strings.TrimSpace(refs["from"]); value != "" {
				fromRef = value
			}
			if value := strings.TrimSpace(refs["to"]); value != "" {
				toRef = value
			}
		}
	}
	if strings.TrimSpace(fromRef) == "" {
		fromRef = "HEAD"
	}
	if strings.TrimSpace(toRef) == "" {
		toRef = "current"
	}
	return fromRef, toRef
}

func (s *Service) sessionFilesRepository(ctx context.Context, session, repo, fromRef, toRef string, repoRefs map[string]map[string]string, touched map[string]*sessionFileAttribution) (sessionFilesRepositoryView, *Error) {
	scope, finderErr := s.openGitHistoryScope(repo)
	if finderErr != nil {
		return sessionFilesRepositoryView{}, finderErr
	}
	defer scope.close()
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()

	head := ""
	if output, _, err := runGitBounded(gitCtx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", "HEAD^{commit}"}, 4096, scope.namespace); err == nil {
		head = strings.TrimSpace(string(output))
	}
	selectedFrom, selectedTo := sessionFilesRefsForRepo(repo, fromRef, toRef, repoRefs)
	refsActive := strings.TrimSpace(fromRef) != "" || strings.TrimSpace(toRef) != ""
	if repoRefs != nil {
		for key, refs := range repoRefs {
			if sessionFilesRepoRefMatches(key, repo) && len(refs) > 0 {
				refsActive = true
				break
			}
		}
	}
	warning := ""
	diffArgs := []string{}
	if head != "" {
		if refsActive {
			if refErr := validateGitRef(selectedFrom, "common.unknownFromRef"); refErr != nil {
				return sessionFilesRepositoryView{}, refErr
			}
			if refErr := validateGitRef(selectedTo, "common.unknownToRef"); refErr != nil {
				return sessionFilesRepositoryView{}, refErr
			}
			valid, refError := sessionFilesRefsValid(gitCtx, scope, selectedFrom, selectedTo)
			if !valid {
				base := sessionFilesDefaultBase(gitCtx, scope)
				diffArgs = []string{base}
				selectedFrom, selectedTo = "", ""
				warning = refError
			} else if selectedTo == "current" {
				diffArgs = []string{selectedFrom}
			} else {
				diffArgs = []string{selectedFrom, selectedTo}
			}
		} else {
			diffArgs = []string{sessionFilesDefaultBase(gitCtx, scope)}
		}
	}

	statuses := make(map[string]string)
	numstat := make(map[string]gitCommitCount)
	if len(diffArgs) > 0 {
		statusRaw, stderr, statusErr := runGitBounded(gitCtx, scope.directory, nil, append([]string{"diff", "--name-status", "-z", "--find-renames"}, diffArgs...), gitCommitMaxOutput, scope.namespace)
		if statusErr != nil {
			if errors.Is(statusErr, errGitNamespaceChanged) {
				return sessionFilesRepositoryView{}, gitNamespaceChanged(repo)
			}
			return sessionFilesRepositoryView{}, &Error{Status: http.StatusInternalServerError, Code: "git_diff_failed", MessageKey: "fs.error.gitDiffFailed", Path: repo, Retryable: true, Message: strings.TrimSpace(string(stderr))}
		}
		rows, _, parseErr := parseGitNameStatus(statusRaw, false)
		if parseErr != nil {
			return sessionFilesRepositoryView{}, &Error{Status: http.StatusInternalServerError, Code: "git_diff_failed", MessageKey: "fs.error.gitDiffFailed", Path: repo, Retryable: true, Message: parseErr.Error()}
		}
		for _, row := range rows {
			path := row["path"]
			if sessionFilesValidRelativePath(path) && !defaultSessionFileExcluded(filepath.Join(repo, filepath.FromSlash(path))) {
				statuses[path] = sessionFilesStatus(row["status"])
			}
		}
		numstatRaw, _, numstatErr := runGitBounded(gitCtx, scope.directory, nil, append([]string{"diff", "--numstat", "-z", "--find-renames"}, diffArgs...), gitCommitMaxOutput, scope.namespace)
		if numstatErr == nil {
			parsed, _, parseErr := parseGitNumstat(numstatRaw, false)
			if parseErr == nil {
				numstat = parsed
			}
		}
	}
	if selectedTo == "current" || (!refsActive && head != "") || head == "" {
		untrackedRaw, _, untrackedErr := runGitBounded(gitCtx, scope.directory, nil, []string{"ls-files", "--others", "--exclude-standard", "-z"}, gitCommitMaxOutput, scope.namespace)
		if untrackedErr == nil {
			for _, path := range strings.Split(string(untrackedRaw), "\x00") {
				if sessionFilesValidRelativePath(path) && !defaultSessionFileExcluded(filepath.Join(repo, filepath.FromSlash(path))) {
					statuses[path] = "?"
				}
			}
		}
	}

	touchedByRel := sessionFileAttributionsForRepo(touched, repo)
	files := make([]map[string]any, 0, len(statuses)+len(touchedByRel))
	addedTotal, removedTotal := 0, 0
	for _, path := range sortedSessionFilePaths(statuses) {
		status := statuses[path]
		added, removed, binary := numstatForPath(numstat, path)
		if status == "?" && added == nil && removed == nil && !binary {
			added, binary = s.untrackedLineCount(ctx, repo, path)
			removed = 0
		}
		counts := gitCommitCount{added: added, removed: removed, binary: binary}
		attribution := touchedByRel[path]
		entry, rowAdded, rowRemoved := s.sessionFileEntryWithAttribution(ctx, session, repo, path, status, counts, attribution, "git")
		if entry == nil {
			continue
		}
		files = append(files, entry)
		if status != "?" {
			addedTotal += rowAdded
			removedTotal += rowRemoved
		}
	}
	for _, path := range sortedSessionAttributionPaths(touchedByRel) {
		if _, present := statuses[path]; present || refsActive {
			continue
		}
		attribution := touchedByRel[path]
		entry, _, _ := s.sessionFileEntryWithAttribution(ctx, session, repo, path, "T", gitCommitCount{}, attribution, "transcript")
		if entry != nil {
			files = append(files, entry)
		}
	}
	branch := ""
	if output, _, err := runGitBounded(gitCtx, scope.directory, nil, []string{"symbolic-ref", "--quiet", "--short", "HEAD"}, 4096, scope.namespace); err == nil {
		branch = strings.TrimSpace(string(output))
	}
	refs := gitRecentRefs(gitCtx, scope)
	aheadBehind := gitAheadBehind(gitCtx, scope, selectedFrom, selectedTo, refsActive)
	displayFrom, displayTo := "default", "base"
	if refsActive && warning == "" {
		displayFrom, displayTo = selectedFrom, selectedTo
	}
	repositoryPayload := map[string]any{
		"repo": repo, "branch": branch, "count": countVisibleSessionFiles(files), "touched_count": len(files),
		"added": addedTotal, "removed": removedTotal, "from_ref": displayFrom,
		"to_ref": displayTo, "error": warning,
	}
	for key, value := range aheadBehind {
		repositoryPayload[key] = value
	}
	return sessionFilesRepositoryView{
		files: files,
		repo:  repositoryPayload,
		refs:  refs, warning: warning,
	}, nil
}

func sessionFilesRefsValid(ctx context.Context, scope *gitHistoryScope, fromRef, toRef string) (bool, string) {
	for _, ref := range []struct{ value, label string }{{fromRef, "FROM"}, {toRef, "TO"}} {
		if ref.value == "current" {
			continue
		}
		if _, _, err := runGitBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", ref.value + "^{commit}"}, 4096, scope.namespace); err != nil {
			return false, "requested refs not found in this repo; showing default"
		}
		_ = ref.label
	}
	if toRef != "current" {
		if _, _, err := runGitBounded(ctx, scope.directory, nil, []string{"merge-base", "--is-ancestor", fromRef, toRef}, 4096, scope.namespace); err != nil {
			return false, "requested refs are not ordered; showing default"
		}
	}
	return true, ""
}

func gitDecorationAliases(decorations string, includeHead bool) []string {
	aliases := make([]string, 0, 4)
	if includeHead {
		aliases = append(aliases, "HEAD")
	}
	for _, raw := range strings.Split(decorations, ",") {
		for _, part := range strings.Split(strings.TrimSpace(raw), " -> ") {
			alias := strings.TrimSpace(part)
			alias = strings.TrimPrefix(alias, "tag: ")
			alias = strings.TrimPrefix(alias, "refs/heads/")
			alias = strings.TrimPrefix(alias, "refs/remotes/")
			if alias == "HEAD" && !includeHead || alias == "" || alias == "origin/HEAD" {
				continue
			}
			found := false
			for _, existing := range aliases {
				if existing == alias {
					found = true
					break
				}
			}
			if !found {
				aliases = append(aliases, alias)
			}
		}
	}
	sort.SliceStable(aliases, func(i, j int) bool {
		rank := func(alias string) int {
			switch {
			case alias == "HEAD":
				return 0
			case alias == "origin/main" || alias == "origin/master":
				return 1
			case alias == "main" || alias == "master":
				return 2
			case strings.HasPrefix(alias, "origin/"):
				return 3
			default:
				return 4
			}
		}
		left, right := rank(aliases[i]), rank(aliases[j])
		if left != right {
			return left < right
		}
		return aliases[i] < aliases[j]
	})
	return aliases
}

func gitRefLabel(short string, aliases []string) string {
	if len(aliases) == 0 {
		return short
	}
	return short + "/" + aliases[0] + func() string {
		if len(aliases) == 1 {
			return ""
		}
		return " " + strings.Join(aliases[1:], " ")
	}()
}

func gitRecentRefs(ctx context.Context, scope *gitHistoryScope) []map[string]any {
	refs := []map[string]any{{"ref": "HEAD", "short": "HEAD", "subject": "base commit"}, {"ref": "current", "short": "current", "subject": "working tree"}}
	output, _, err := runGitBounded(ctx, scope.directory, nil, []string{
		"log", "--decorate=short", "--max-count=200", "--pretty=format:%H%x1f%h%x1f%s%x1f%at%x1f%an%x1f%D",
	}, gitHistoryMaxOutput, scope.namespace)
	if err != nil {
		return refs
	}
	seen := map[string]bool{"HEAD": true, "current": true}
	headSeen := false
	for _, line := range strings.Split(string(output), "\n") {
		parts := strings.SplitN(line, "\x1f", 6)
		if len(parts) < 3 || parts[0] == "" || seen[parts[0]] {
			continue
		}
		aliases := gitDecorationAliases(func() string {
			if len(parts) >= 6 {
				return parts[5]
			}
			return ""
		}(), false)
		entry := map[string]any{"ref": parts[0], "short": gitRefLabel(parts[1], aliases), "subject": parts[2]}
		if len(aliases) > 0 {
			entry["aliases"] = aliases
		}
		if len(parts) >= 5 {
			entry["date"] = parts[3]
			entry["author"] = parts[4]
		}
		if !headSeen {
			headAliases := gitDecorationAliases(func() string {
				if len(parts) >= 6 {
					return parts[5]
				}
				return ""
			}(), true)
			head := map[string]any{"ref": "HEAD", "short": gitRefLabel(parts[1], headAliases), "subject": parts[2], "commit": parts[0], "aliases": headAliases}
			if len(parts) >= 5 {
				head["date"] = parts[3]
				head["author"] = parts[4]
			}
			refs[0] = head
			headSeen = true
		}
		refs = append(refs, entry)
		seen[parts[0]] = true
	}
	return refs
}

func gitAheadBehind(ctx context.Context, scope *gitHistoryScope, fromRef, toRef string, refsActive bool) map[string]any {
	leftRef, rightRef := fromRef, toRef
	if !refsActive {
		output, _, err := runGitBounded(ctx, scope.directory, nil, []string{"rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"}, 4096, scope.namespace)
		if err != nil || strings.TrimSpace(string(output)) == "" {
			return map[string]any{}
		}
		leftRef, rightRef = strings.TrimSpace(string(output)), "HEAD"
	}
	if rightRef == "current" {
		rightRef = "HEAD"
	}
	for _, ref := range []string{leftRef, rightRef} {
		if _, _, err := runGitBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", ref + "^{commit}"}, 4096, scope.namespace); err != nil {
			return map[string]any{}
		}
	}
	output, _, err := runGitBounded(ctx, scope.directory, nil, []string{"rev-list", "--left-right", "--count", leftRef + "..." + rightRef}, 4096, scope.namespace)
	if err != nil {
		return map[string]any{}
	}
	values := strings.Fields(string(output))
	if len(values) < 2 {
		return map[string]any{}
	}
	behind, behindErr := strconv.Atoi(values[0])
	ahead, aheadErr := strconv.Atoi(values[1])
	if behindErr != nil || aheadErr != nil {
		return map[string]any{}
	}
	return map[string]any{"behind": behind, "ahead": ahead}
}

func sessionFilesDefaultBase(ctx context.Context, scope *gitHistoryScope) string {
	for _, ref := range []struct {
		args []string
		name string
	}{
		{[]string{"symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"}, ""},
		{[]string{"show-ref", "--verify", "--quiet", "refs/remotes/origin/main"}, "origin/main"},
		{[]string{"show-ref", "--verify", "--quiet", "refs/remotes/origin/master"}, "origin/master"},
		{[]string{"show-ref", "--verify", "--quiet", "refs/heads/main"}, "main"},
		{[]string{"show-ref", "--verify", "--quiet", "refs/heads/master"}, "master"},
	} {
		output, _, err := runGitBounded(ctx, scope.directory, nil, ref.args, 4096, scope.namespace)
		if err == nil {
			candidate := strings.TrimSpace(string(output))
			if ref.name == "" {
				candidate = candidate
			}
			if candidate != "" {
				if base, _, mergeErr := runGitBounded(ctx, scope.directory, nil, []string{"merge-base", candidate, "HEAD"}, 4096, scope.namespace); mergeErr == nil && strings.TrimSpace(string(base)) != "" {
					return strings.TrimSpace(string(base))
				}
			}
			if ref.name != "" {
				if base, _, mergeErr := runGitBounded(ctx, scope.directory, nil, []string{"merge-base", ref.name, "HEAD"}, 4096, scope.namespace); mergeErr == nil && strings.TrimSpace(string(base)) != "" {
					return strings.TrimSpace(string(base))
				}
			}
		}
	}
	return "HEAD"
}

func sessionFilesStatus(raw string) string {
	if raw == "A" || raw == "D" || raw == "R" || raw == "C" {
		return raw
	}
	return "M"
}

func sessionFilesValidRelativePath(path string) bool {
	if path == "" || filepath.IsAbs(path) || strings.ContainsRune(path, 0) || !utf8.ValidString(path) {
		return false
	}
	clean := filepath.Clean(filepath.FromSlash(path))
	return clean != "." && clean != ".git" && clean != ".." && !strings.HasPrefix(clean, ".."+string(os.PathSeparator))
}

func sortedSessionFilePaths(statuses map[string]string) []string {
	paths := make([]string, 0, len(statuses))
	for path := range statuses {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	return paths
}

func sortedSessionAttributionPaths(attributions map[string]*sessionFileAttribution) []string {
	paths := make([]string, 0, len(attributions))
	for path := range attributions {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	return paths
}

func countVisibleSessionFiles(files []map[string]any) int {
	count := 0
	for _, file := range files {
		if file["status"] != "T" {
			count++
		}
	}
	return count
}

func numstatForPath(numstat map[string]gitCommitCount, path string) (any, any, bool) {
	for key, counts := range numstat {
		parts := strings.SplitN(key, "\x00", 2)
		if len(parts) == 2 && (parts[1] == path || parts[0] == path) {
			return counts.added, counts.removed, counts.binary
		}
	}
	return nil, nil, false
}

func (s *Service) untrackedLineCount(ctx context.Context, repo, relative string) (any, bool) {
	path := filepath.Join(repo, filepath.FromSlash(relative))
	file, _, _, finderErr := s.open(path, false)
	if finderErr != nil {
		return nil, false
	}
	defer file.Close()
	select {
	case <-ctx.Done():
		return nil, false
	default:
	}
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > maxUntrackedLineCountSize {
		return nil, false
	}
	data, err := io.ReadAll(io.LimitReader(file, maxUntrackedLineCountSize+1))
	if err != nil || int64(len(data)) > maxUntrackedLineCountSize {
		return nil, false
	}
	probeLength := len(data)
	if probeLength > 8192 {
		probeLength = 8192
	}
	if bytes.IndexByte(data[:probeLength], 0) >= 0 {
		return nil, true
	}
	count := bytes.Count(data, []byte{'\n'})
	if len(data) > 0 && data[len(data)-1] != '\n' {
		count++
	}
	return count, false
}

func (s *Service) sessionFileEntry(ctx context.Context, session, repo, relative, status string, counts gitCommitCount) (map[string]any, int, int) {
	return s.sessionFileEntryWithAttribution(ctx, session, repo, relative, status, counts, nil, "git")
}

func (s *Service) sessionFileEntryWithAttribution(ctx context.Context, session, repo, relative, status string, counts gitCommitCount, attribution *sessionFileAttribution, source string) (map[string]any, int, int) {
	path := filepath.Join(repo, filepath.FromSlash(relative))
	info, finderErr := s.Info(ctx, path, false)
	agents := []any{}
	windows := []map[string]any{}
	mtime := int64(0)
	if attribution != nil {
		for _, agent := range attribution.agents {
			agents = append(agents, agent)
		}
		windows = append(windows, attribution.windows...)
		mtime = attribution.mtime
		if source == "git" && attribution.status != "" {
			status = sessionFileStatusMerge(status, attribution.status)
		}
	}
	entry := map[string]any{
		"session": session, "agents": agents, "agent": "", "agent_windows": windows, "status": status,
		"repo": repo, "path": filepath.ToSlash(relative), "abs_path": path, "mtime": mtime, "size": nil,
		"missing": false, "source": source, "added": nil, "removed": nil, "diff_tracked": source == "git" && status != "?", "uploaded": generatedSessionUploadName(path),
	}
	if len(agents) > 0 {
		entry["agent"] = agents[0]
	}
	if finderErr != nil {
		if finderErr.Status != http.StatusNotFound || status == "?" || status == "T" {
			return nil, 0, 0
		}
		entry["missing"] = true
	} else {
		if value, ok := info["mtime"].(int64); ok {
			entry["mtime"] = value
		} else if value, ok := info["mtime"].(float64); ok {
			entry["mtime"] = int64(value)
		}
		entry["size"] = info["size"]
	}
	if finderErr != nil && attribution == nil {
		entry["mtime"] = int64(0)
	}
	if !counts.binary {
		entry["added"] = counts.added
		entry["removed"] = counts.removed
	}
	added, _ := counts.added.(int)
	removed, _ := counts.removed.(int)
	return entry, added, removed
}
