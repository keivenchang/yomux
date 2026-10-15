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
	"sync"
	"unicode/utf8"
)

const (
	maxSessionFileRoots           = 64
	sessionFilesRepositoryWorkers = 4
	minSessionFilesHours          = 0.25
	maxUntrackedLineCountSize     = 2 * 1024 * 1024
)

// SessionFiles projects the Git changes belonging to the supplied live session roots. The
// caller owns session discovery; this package owns repository authorization, Git execution, and
// the response shape. Keeping those boundaries separate means a later statusd/Tabber owner can
// provide richer session attribution without duplicating the Git security path.
func (s *Service) SessionFiles(ctx context.Context, session string, roots []string, hours float64, fromRef, toRef string, repoRefs map[string]map[string]string) (map[string]any, *Error) {
	return s.SessionFilesWithAgents(ctx, session, roots, nil, hours, fromRef, toRef, repoRefs)
}

type sessionFilesDiscovery struct {
	session          string
	hours            float64
	fromRef          string
	toRef            string
	repoRefs         map[string]map[string]string
	candidateRoots   []string
	repositories     []string
	touched          map[string]*sessionFileAttribution
	repositoryScopes map[string]*gitHistoryScope
}

func (discovery sessionFilesDiscovery) closeRepositoryScopes() {
	for _, scope := range discovery.repositoryScopes {
		scope.close()
	}
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

func sessionFilesRepositoryScopePath(rawPath string, missingIsFile bool) string {
	path := strings.TrimSpace(rawPath)
	if path == "" {
		return ""
	}
	info, err := os.Stat(path)
	if err == nil && info.IsDir() {
		return path
	}
	if err == nil || missingIsFile && os.IsNotExist(err) {
		return filepath.Dir(path)
	}
	return path
}

// Transcript paths often point below a repository already opened from a live pane. Reuse its
// pinned scope instead of rediscovering .git for every distinct transcript directory.
func sessionFilesKnownRepository(rawPath string, scopes map[string]*gitHistoryScope) (string, bool) {
	path := canonicalSessionFilesPath(rawPath)
	bestRepo := ""
	bestLength := -1
	for repo := range scopes {
		canonicalRepo := canonicalSessionFilesPath(repo)
		relative, err := filepath.Rel(canonicalRepo, path)
		if err != nil || filepath.IsAbs(relative) || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
			continue
		}
		if sessionFilesHasNestedGitBoundary(path, canonicalRepo) {
			continue
		}
		if len(canonicalRepo) > bestLength {
			bestRepo = repo
			bestLength = len(canonicalRepo)
		}
	}
	return bestRepo, bestLength >= 0
}

func sessionFilesHasNestedGitBoundary(directory, repo string) bool {
	for current := filepath.Clean(directory); current != repo; current = filepath.Dir(current) {
		if current == filepath.Dir(current) {
			return true
		}
		if _, err := os.Lstat(filepath.Join(current, ".git")); err == nil || !os.IsNotExist(err) {
			return true
		}
	}
	return false
}

func (s *Service) sessionFilesRepositoryForTouchedPath(ctx context.Context, rawPath string) (string, *Error) {
	root := sessionFilesRepositoryScopePath(rawPath, true)
	for root != "" {
		repo, finderErr := s.sessionFilesRepositoryForTarget(ctx, root, false)
		if finderErr == nil || finderErr.Code != "path_not_found" {
			return repo, finderErr
		}
		parent := filepath.Dir(root)
		if parent == root {
			return "", finderErr
		}
		root = parent
	}
	return "", notFound(rawPath)
}

func (s *Service) sessionFilesRepositoryForTarget(ctx context.Context, rawPath string, missingIsFile bool) (string, *Error) {
	if ctx.Err() != nil {
		return "", cancelled(rawPath)
	}
	root := sessionFilesRepositoryScopePath(rawPath, missingIsFile)
	if root == "" {
		return "", nil
	}
	scope, finderErr := s.openGitHistoryScopeWithOptions(ctx, root, gitHistoryScopeOptions{})
	if finderErr != nil {
		return "", finderErr
	}
	repo := filepath.Clean(scope.repo)
	scope.close()
	return repo, nil
}

func (s *Service) sessionFilesRepositoryViewsBounded(ctx context.Context, discovery sessionFilesDiscovery, paths []string, emit func(int, sessionFilesRepositoryView) error) ([]sessionFilesRepositoryView, map[string]int, *Error) {
	if len(paths) == 0 {
		return []sessionFilesRepositoryView{}, make(map[string]int), nil
	}
	type result struct {
		view       sessionFilesRepositoryView
		err        *Error
		duplicate  bool
		ownedScope *gitHistoryScope
	}
	if discovery.repositoryScopes == nil {
		discovery.repositoryScopes = make(map[string]*gitHistoryScope)
	}
	workerCount := min(sessionFilesRepositoryWorkers, len(paths))
	workerContext, cancel := context.WithCancel(ctx)
	defer cancel()
	jobs := make(chan string, len(paths))
	results := make(chan result, len(paths))
	var workers sync.WaitGroup
	var claimMu sync.Mutex
	claimed := make(map[string]bool, len(paths))
	for range workerCount {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for {
				var path string
				var ok bool
				select {
				case <-workerContext.Done():
					return
				case path, ok = <-jobs:
					if !ok {
						return
					}
				}
				if workerContext.Err() != nil {
					return
				}
				scopePath := sessionFilesRepositoryScopePath(path, false)
				scope := discovery.repositoryScopes[filepath.Clean(scopePath)]
				ownedScope := false
				if scope == nil {
					var finderErr *Error
					scope, finderErr = s.openGitHistoryScopeWithOptions(workerContext, scopePath, gitHistoryScopeOptions{})
					if finderErr != nil {
						results <- result{err: finderErr}
						continue
					}
					ownedScope = true
				}
				repo := filepath.Clean(scope.repo)
				claimMu.Lock()
				duplicate := claimed[repo]
				if !duplicate {
					claimed[repo] = true
				}
				claimMu.Unlock()
				if duplicate {
					if ownedScope {
						scope.close()
					}
					results <- result{duplicate: true}
					continue
				}
				view, repositoryErr := s.sessionFilesRepositoryWithScope(workerContext, discovery.session, repo, discovery.fromRef, discovery.toRef, discovery.repoRefs, discovery.touched, scope)
				if repositoryErr != nil {
					if ownedScope {
						scope.close()
					}
					results <- result{err: repositoryErr}
					continue
				}
				view.scope = scope
				item := result{view: view}
				if ownedScope {
					item.ownedScope = scope
				}
				results <- item
			}
		}()
	}
	for _, path := range paths {
		jobs <- path
	}
	close(jobs)
	go func() {
		workers.Wait()
		close(results)
	}()

	views := make([]sessionFilesRepositoryView, 0, len(paths))
	indexes := make(map[string]int, len(paths))
	ownedScopes := make(map[string]*gitHistoryScope, len(paths))
	var firstError *Error
	var emitError error
	for item := range results {
		if item.err != nil {
			if item.ownedScope != nil {
				item.ownedScope.close()
			}
			if item.err.Code != "not_git_repo" && firstError == nil {
				firstError = item.err
			}
			continue
		}
		if item.duplicate || item.view.repo == nil {
			if item.ownedScope != nil {
				item.ownedScope.close()
			}
			continue
		}
		repo, _ := item.view.repo["repo"].(string)
		if item.ownedScope != nil {
			ownedScopes[repo] = item.ownedScope
		}
		index := len(views)
		indexes[repo] = index
		views = append(views, item.view)
		if emit != nil && emitError == nil && ctx.Err() == nil {
			if err := emit(index, item.view); err != nil {
				emitError = err
				cancel()
			}
		}
	}
	for repo, scope := range ownedScopes {
		if existing := discovery.repositoryScopes[repo]; existing != nil {
			scope.close()
			continue
		}
		discovery.repositoryScopes[repo] = scope
	}
	if ctx.Err() != nil {
		return nil, nil, cancelled(discovery.session)
	}
	if emitError != nil {
		return nil, nil, &Error{Status: http.StatusInternalServerError, Code: "session_files_stream_failed", MessageKey: "common.requestFailed", Path: discovery.session, Retryable: true, Message: emitError.Error()}
	}
	if firstError != nil {
		return nil, nil, firstError
	}
	return views, indexes, nil
}

type sessionFilesViewEnricher func(context.Context, sessionFilesRepositoryView, *gitHistoryScope) sessionFilesRepositoryView

func cloneSessionFilesRepositoryView(view sessionFilesRepositoryView) sessionFilesRepositoryView {
	cloned := view
	cloned.files = make([]map[string]any, 0, len(view.files))
	for _, file := range view.files {
		clonedFile := make(map[string]any, len(file))
		for key, value := range file {
			clonedFile[key] = value
		}
		cloned.files = append(cloned.files, clonedFile)
	}
	cloned.repo = make(map[string]any, len(view.repo))
	for key, value := range view.repo {
		cloned.repo[key] = value
	}
	cloned.refs = append([]map[string]any(nil), view.refs...)
	return cloned
}

func (s *Service) enrichSessionFilesViewsStagedBounded(ctx context.Context, views []sessionFilesRepositoryView, stages []sessionFilesViewEnricher, emit func(int, sessionFilesRepositoryView) error) error {
	if len(views) == 0 || len(stages) == 0 {
		return nil
	}
	workerCount := min(sessionFilesRepositoryWorkers, len(views))
	workerContext, cancel := context.WithCancel(ctx)
	defer cancel()
	type result struct {
		index int
		view  sessionFilesRepositoryView
	}
	jobs := make(chan int, len(views))
	results := make(chan result, len(views)*len(stages))
	var workers sync.WaitGroup
	for range workerCount {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for {
				var index int
				var ok bool
				select {
				case <-workerContext.Done():
					return
				case index, ok = <-jobs:
					if !ok {
						return
					}
				}
				if workerContext.Err() != nil {
					return
				}
				view := cloneSessionFilesRepositoryView(views[index])
				for _, stage := range stages {
					if workerContext.Err() != nil {
						return
					}
					view = stage(workerContext, view, view.scope)
					results <- result{index: index, view: cloneSessionFilesRepositoryView(view)}
				}
			}
		}()
	}
	for index := range views {
		jobs <- index
	}
	close(jobs)
	go func() {
		workers.Wait()
		close(results)
	}()

	var emitError error
	for item := range results {
		views[item.index] = cloneSessionFilesRepositoryView(item.view)
		if emit == nil || emitError != nil || ctx.Err() != nil {
			continue
		}
		if err := emit(item.index, item.view); err != nil {
			emitError = err
			cancel()
		}
	}
	if emitError != nil {
		return emitError
	}
	return ctx.Err()
}

func (s *Service) enrichSessionFilesFileMetadata(ctx context.Context, view sessionFilesRepositoryView, _ *gitHistoryScope) sessionFilesRepositoryView {
	files := make([]map[string]any, 0, len(view.files))
	addedTotal, removedTotal := 0, 0
	repo, _ := view.repo["repo"].(string)
	for _, entry := range view.files {
		if ctx.Err() != nil {
			return view
		}
		relative, _ := entry["path"].(string)
		status, _ := entry["status"].(string)
		path := filepath.Join(repo, filepath.FromSlash(relative))
		info, finderErr := s.Info(ctx, path, false)
		if finderErr != nil {
			if finderErr.Status != http.StatusNotFound || status == "?" || status == "T" {
				continue
			}
			entry["missing"] = true
			entry["mtime"] = int64(0)
			entry["size"] = nil
		} else {
			entry["missing"] = false
			if value, ok := info["mtime"].(int64); ok {
				entry["mtime"] = value
			} else if value, ok := info["mtime"].(float64); ok {
				entry["mtime"] = int64(value)
			}
			entry["size"] = info["size"]
		}
		if status == "?" && entry["added"] == nil && entry["removed"] == nil {
			added, binary := s.untrackedLineCount(ctx, repo, relative)
			if binary {
				entry["added"] = nil
				entry["removed"] = nil
			} else {
				entry["added"] = added
				entry["removed"] = 0
			}
		}
		if status != "?" {
			addedTotal += sessionFilesNumber(entry["added"])
			removedTotal += sessionFilesNumber(entry["removed"])
		}
		files = append(files, entry)
	}
	view.files = files
	view.repo["count"] = countVisibleSessionFiles(files)
	view.repo["touched_count"] = len(files)
	view.repo["added"] = addedTotal
	view.repo["removed"] = removedTotal
	return view
}

func (s *Service) enrichSessionFilesRepository(ctx context.Context, view sessionFilesRepositoryView, scope *gitHistoryScope) sessionFilesRepositoryView {
	if scope == nil || ctx.Err() != nil {
		return view
	}
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	if output, _, err := runGitMetadataBounded(gitCtx, scope.directory, nil, []string{"symbolic-ref", "--quiet", "--short", "HEAD"}, 4096, scope.namespace); err == nil {
		view.repo["branch"] = strings.TrimSpace(string(output))
	}
	view.refs = gitRecentRefs(gitCtx, scope)
	for key, value := range gitAheadBehind(gitCtx, scope, view.selectedFrom, view.selectedTo, view.refsActive) {
		view.repo[key] = value
	}
	return view
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

func sessionFilesRepositoryChunk(discovery sessionFilesDiscovery, view sessionFilesRepositoryView, replace bool) map[string]any {
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
	}
}

// SessionFilesWithAgents is the parity owner used by the HTTP route. Roots provide the live
// filesystem scope; agents provide transcript evidence. The legacy SessionFiles wrapper remains
// intentionally equivalent for callers that only have pane roots.
func (s *Service) SessionFilesWithAgents(ctx context.Context, session string, roots []string, agents []SessionFileAgent, hours float64, fromRef, toRef string, repoRefs map[string]map[string]string) (map[string]any, *Error) {
	return s.SessionFilesWithAgentsStream(ctx, session, roots, agents, hours, fromRef, toRef, repoRefs, nil)
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
		candidateRoots: candidateRoots, touched: touched, repositoryScopes: make(map[string]*gitHistoryScope),
	}
	candidates := append([]string(nil), candidateRoots...)
	defer discovery.closeRepositoryScopes()
	views, viewIndexByRepo, finderErr := s.sessionFilesRepositoryViewsBounded(ctx, discovery, candidates, func(_ int, view sessionFilesRepositoryView) error {
		if emit == nil {
			return nil
		}
		return emit(sessionFilesRepositoryChunk(discovery, view, false))
	})
	if finderErr != nil {
		return nil, finderErr
	}
	enrichmentErr := s.enrichSessionFilesViewsStagedBounded(ctx, views, []sessionFilesViewEnricher{
		s.enrichSessionFilesFileMetadata,
		s.enrichSessionFilesRepository,
	}, func(_ int, view sessionFilesRepositoryView) error {
		if emit == nil {
			return nil
		}
		return emit(sessionFilesRepositoryChunk(discovery, view, true))
	})
	if enrichmentErr != nil {
		if ctx.Err() != nil {
			return nil, cancelled(session)
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "session_files_stream_failed", MessageKey: "common.requestFailed", Path: session, Retryable: true, Message: enrichmentErr.Error()}
	}
	// Attribution is deliberately a second phase. It can discover a repository that is no longer
	// represented by a live pane, or add agent/window metadata to files already emitted above. Those
	// updates replace one repository at a time, so the browser stays interactive throughout the scan.
	touched = collectSessionFileAttributions(ctx, agents, candidateRoots, boundedHours)
	if ctx.Err() != nil {
		return nil, cancelled(session)
	}
	discovery.touched = touched
	touchedRepos := make(map[string]bool)
	repoByDirectory := make(map[string]string)
	for path := range touched {
		if ctx.Err() != nil {
			return nil, cancelled(session)
		}
		directory := sessionFilesRepositoryScopePath(path, true)
		repo, resolved := repoByDirectory[directory]
		if !resolved {
			if knownRepo, ok := sessionFilesKnownRepository(directory, discovery.repositoryScopes); ok {
				repo = knownRepo
			} else {
				var finderErr *Error
				repo, finderErr = s.sessionFilesRepositoryForTouchedPath(ctx, path)
				if finderErr != nil {
					continue
				}
			}
			repoByDirectory[directory] = repo
		}
		touchedRepos[repo] = true
	}
	reposWithAttributions := make([]string, 0, len(touchedRepos))
	for repo := range touchedRepos {
		reposWithAttributions = append(reposWithAttributions, repo)
	}
	sort.Strings(reposWithAttributions)
	newRepos := make([]string, 0, len(reposWithAttributions))
	for _, repo := range reposWithAttributions {
		index, alreadyVisible := viewIndexByRepo[repo]
		if alreadyVisible {
			view, repositoryErr := s.sessionFilesRepositoryViewWithAttribution(ctx, session, cloneSessionFilesRepositoryView(views[index]), touched)
			if repositoryErr != nil {
				return nil, repositoryErr
			}
			views[index] = view
			if emit != nil {
				if err := emit(sessionFilesRepositoryChunk(discovery, view, true)); err != nil {
					if ctx.Err() != nil {
						return nil, cancelled(repo)
					}
					return nil, &Error{Status: http.StatusInternalServerError, Code: "session_files_stream_failed", MessageKey: "common.requestFailed", Path: repo, Retryable: true, Message: err.Error()}
				}
			}
			continue
		}
		newRepos = append(newRepos, repo)
	}
	newViewBase := len(views)
	newViews, newViewIndexes, finderErr := s.sessionFilesRepositoryViewsBounded(ctx, discovery, newRepos, func(index int, view sessionFilesRepositoryView) error {
		if emit == nil {
			return nil
		}
		return emit(sessionFilesRepositoryChunk(discovery, view, false))
	})
	if finderErr != nil {
		return nil, finderErr
	}
	for repo, index := range newViewIndexes {
		viewIndexByRepo[repo] = newViewBase + index
	}
	if enrichmentErr := s.enrichSessionFilesViewsStagedBounded(ctx, newViews, []sessionFilesViewEnricher{
		s.enrichSessionFilesFileMetadata,
		s.enrichSessionFilesRepository,
	}, func(_ int, view sessionFilesRepositoryView) error {
		if emit == nil {
			return nil
		}
		return emit(sessionFilesRepositoryChunk(discovery, view, true))
	}); enrichmentErr != nil {
		if ctx.Err() != nil {
			return nil, cancelled(session)
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "session_files_stream_failed", MessageKey: "common.requestFailed", Path: session, Retryable: true, Message: enrichmentErr.Error()}
	}
	views = append(views, newViews...)
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
					"error": "",
				}
				repos[key] = existing
			}
			for _, field := range []string{"count", "touched_count", "added", "removed"} {
				existing[field] = sessionFilesNumber(existing[field]) + sessionFilesNumber(row[field])
			}
			for _, field := range []string{"branch", "from_ref", "to_ref", "comparison_from_ref", "comparison_to_ref", "error", "error_message"} {
				if value, already := existing[field]; already && value != nil && strings.TrimSpace(fmt.Sprint(value)) != "" {
					continue
				}
				if value, present := row[field]; present && strings.TrimSpace(fmt.Sprint(value)) != "" {
					existing[field] = value
				}
			}
		}
	}
	for _, row := range repos {
		if value, ok := row["from_ref"].(string); !ok || strings.TrimSpace(value) == "" {
			row["from_ref"] = sessionFilesPayloadRef(fromRef, "default")
		}
		if value, ok := row["to_ref"].(string); !ok || strings.TrimSpace(value) == "" {
			row["to_ref"] = sessionFilesPayloadRef(toRef, "base")
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
	files        []map[string]any
	repo         map[string]any
	refs         []map[string]any
	warning      string
	scope        *gitHistoryScope
	refsActive   bool
	selectedFrom string
	selectedTo   string
}

func sessionFilesRepositoryPath(view sessionFilesRepositoryView) string {
	repo, _ := view.repo["repo"].(string)
	return repo
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
		canonicalRepo := canonicalSessionFilesPath(repo)
		matchedKey := ""
		matchedRank := 3
		for key := range repoRefs {
			if canonicalSessionFilesPath(key) != canonicalRepo {
				continue
			}
			rank := 2
			if key == repo {
				rank = 0
			} else if filepath.Clean(strings.TrimSpace(key)) == filepath.Clean(strings.TrimSpace(repo)) {
				rank = 1
			}
			if rank < matchedRank || rank == matchedRank && (matchedKey == "" || key < matchedKey) {
				matchedKey, matchedRank = key, rank
			}
		}
		if matchedRank < 3 {
			refs := repoRefs[matchedKey]
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

func (s *Service) sessionFilesRepositoryWithScope(ctx context.Context, session, repo, fromRef, toRef string, repoRefs map[string]map[string]string, touched map[string]*sessionFileAttribution, scope *gitHistoryScope) (sessionFilesRepositoryView, *Error) {
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()

	head := ""
	if output, _, err := runGitMetadataBounded(gitCtx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", "HEAD^{commit}"}, 4096, scope.namespace); err == nil {
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
				refsActive = false
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
	effectiveFrom, effectiveTo := "HEAD", "current"
	if len(diffArgs) > 0 {
		effectiveFrom = diffArgs[0]
	}
	if len(diffArgs) > 1 {
		effectiveTo = diffArgs[1]
	}

	statuses := make(map[string]string)
	numstat := make(map[string]gitCommitCount)
	if len(diffArgs) > 0 {
		diffOptions := []string{"diff", "--raw", "--numstat", "-z", "--find-renames", "--no-ext-diff", "--no-textconv"}
		runDiff := runGitMetadataBounded
		if len(diffArgs) < 2 {
			runDiff = runGitBounded
		}
		diffRaw, stderr, diffErr := runDiff(gitCtx, scope.directory, nil, append(diffOptions, diffArgs...), gitCommitMaxOutput, scope.namespace)
		if diffErr != nil {
			if errors.Is(diffErr, errGitNamespaceChanged) {
				return sessionFilesRepositoryView{}, gitNamespaceChanged(repo)
			}
			return sessionFilesRepositoryView{}, &Error{Status: http.StatusInternalServerError, Code: "git_diff_failed", MessageKey: "fs.error.gitDiffFailed", Path: repo, Retryable: true, Message: strings.TrimSpace(string(stderr))}
		}
		rows, counts, _, parseErr := parseGitRawNumstat(diffRaw, false)
		if parseErr != nil {
			return sessionFilesRepositoryView{}, &Error{Status: http.StatusInternalServerError, Code: "git_diff_failed", MessageKey: "fs.error.gitDiffFailed", Path: repo, Retryable: true, Message: parseErr.Error()}
		}
		for _, row := range rows {
			path := row["path"]
			if sessionFilesValidRelativePath(path) && !defaultSessionFileExcluded(filepath.Join(repo, filepath.FromSlash(path))) {
				statuses[path] = sessionFilesStatus(row["status"])
			}
		}
		numstat = counts
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
	numstatIndex := indexGitCommitCounts(numstat)
	for _, path := range sortedSessionFilePaths(statuses) {
		status := statuses[path]
		added, removed, binary := numstatForPath(numstatIndex, path)
		counts := gitCommitCount{added: added, removed: removed, binary: binary}
		attribution := touchedByRel[path]
		entry, rowAdded, rowRemoved := s.sessionFileEntryWithAttributionMode(ctx, session, repo, path, status, counts, attribution, "git", sessionFileMetadataDeferred)
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
		entry, _, _ := s.sessionFileEntryWithAttributionMode(ctx, session, repo, path, "T", gitCommitCount{}, attribution, "transcript", sessionFileMetadataDeferred)
		if entry != nil {
			files = append(files, entry)
		}
	}
	displayFrom, displayTo := "default", "base"
	if refsActive && warning == "" {
		displayFrom, displayTo = selectedFrom, selectedTo
	}
	repositoryPayload := map[string]any{
		"repo": repo, "branch": "", "count": countVisibleSessionFiles(files), "touched_count": len(files),
		"added": addedTotal, "removed": removedTotal, "from_ref": displayFrom,
		"to_ref": displayTo, "comparison_from_ref": effectiveFrom,
		"comparison_to_ref": effectiveTo, "error": warning,
	}
	return sessionFilesRepositoryView{
		files: files,
		repo:  repositoryPayload,
		refs:  []map[string]any{}, warning: warning,
		refsActive: refsActive, selectedFrom: selectedFrom, selectedTo: selectedTo,
	}, nil
}

func sessionFilesRefsValid(ctx context.Context, scope *gitHistoryScope, fromRef, toRef string) (bool, string) {
	for _, ref := range []struct{ value, label string }{{fromRef, "FROM"}, {toRef, "TO"}} {
		if ref.value == "current" {
			continue
		}
		if _, _, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", ref.value + "^{commit}"}, 4096, scope.namespace); err != nil {
			return false, "requested refs not found in this repo; showing default"
		}
		_ = ref.label
	}
	if toRef != "current" {
		if _, _, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"merge-base", "--is-ancestor", fromRef, toRef}, 4096, scope.namespace); err != nil {
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
	output, _, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{
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
		output, _, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"}, 4096, scope.namespace)
		if err != nil || strings.TrimSpace(string(output)) == "" {
			return map[string]any{}
		}
		leftRef, rightRef = strings.TrimSpace(string(output)), "HEAD"
	}
	if rightRef == "current" {
		rightRef = "HEAD"
	}
	for _, ref := range []string{leftRef, rightRef} {
		if _, _, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", ref + "^{commit}"}, 4096, scope.namespace); err != nil {
			return map[string]any{}
		}
	}
	output, _, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"rev-list", "--left-right", "--count", leftRef + "..." + rightRef}, 4096, scope.namespace)
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
	// Resolve candidate refs in one Git process; per-ref existence probes dominate this path on macOS.
	refsOutput, _, refsErr := runGitMetadataBounded(ctx, scope.directory, nil, []string{
		"for-each-ref", "--format=%(refname)%09%(symref)",
		"refs/remotes/origin/HEAD", "refs/remotes/origin/main", "refs/remotes/origin/master",
		"refs/heads/main", "refs/heads/master",
	}, 4096, scope.namespace)
	if refsErr != nil {
		return "HEAD"
	}
	present := make(map[string]bool)
	preferred := make([]string, 0, 5)
	for _, line := range strings.Split(string(refsOutput), "\n") {
		fields := strings.SplitN(line, "\t", 2)
		if len(fields) == 0 || fields[0] == "" {
			continue
		}
		present[fields[0]] = true
		if len(fields) == 2 && fields[1] != "" {
			preferred = append(preferred, fields[1])
		}
	}
	candidates := append(preferred,
		"refs/remotes/origin/main", "refs/remotes/origin/master", "refs/heads/main", "refs/heads/master",
	)
	seen := make(map[string]bool, len(candidates))
	for _, candidate := range candidates {
		if candidate == "" || seen[candidate] {
			continue
		}
		seen[candidate] = true
		if !strings.HasPrefix(candidate, "refs/") || !present[candidate] && !containsString(preferred, candidate) {
			continue
		}
		base, _, mergeErr := runGitMetadataBounded(ctx, scope.directory, nil, []string{"merge-base", candidate, "HEAD"}, 4096, scope.namespace)
		if mergeErr == nil && strings.TrimSpace(string(base)) != "" {
			return strings.TrimSpace(string(base))
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

func numstatForPath(numstat gitCommitCountIndex, path string) (any, any, bool) {
	counts, ok := numstat[path]
	if !ok {
		return nil, nil, false
	}
	return counts.added, counts.removed, counts.binary
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

func (s *Service) sessionFileEntryWithAttribution(ctx context.Context, session, repo, relative, status string, counts gitCommitCount, attribution *sessionFileAttribution, source string) (map[string]any, int, int) {
	return s.sessionFileEntryWithAttributionMode(ctx, session, repo, relative, status, counts, attribution, source, sessionFileMetadataComplete)
}

type sessionFileMetadataMode uint8

const (
	sessionFileMetadataDeferred sessionFileMetadataMode = iota
	sessionFileMetadataComplete
)

func (s *Service) sessionFileEntryWithAttributionMode(ctx context.Context, session, repo, relative, status string, counts gitCommitCount, attribution *sessionFileAttribution, source string, metadataMode sessionFileMetadataMode) (map[string]any, int, int) {
	path := filepath.Join(repo, filepath.FromSlash(relative))
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
	if metadataMode == sessionFileMetadataComplete {
		info, finderErr := s.Info(ctx, path, false)
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
	}
	if !counts.binary {
		entry["added"] = counts.added
		entry["removed"] = counts.removed
	}
	added, _ := counts.added.(int)
	removed, _ := counts.removed.(int)
	return entry, added, removed
}

func (s *Service) sessionFilesRepositoryViewWithAttribution(ctx context.Context, session string, view sessionFilesRepositoryView, touched map[string]*sessionFileAttribution) (sessionFilesRepositoryView, *Error) {
	repo, _ := view.repo["repo"].(string)
	touchedByRel := sessionFileAttributionsForRepo(touched, repo)
	if len(touchedByRel) == 0 {
		return view, nil
	}
	filesByPath := make(map[string]map[string]any, len(view.files))
	for _, entry := range view.files {
		path, _ := entry["path"].(string)
		if path != "" {
			filesByPath[path] = entry
		}
	}
	added := sessionFilesNumber(view.repo["added"])
	removed := sessionFilesNumber(view.repo["removed"])
	for path, attribution := range touchedByRel {
		entry, present := filesByPath[path]
		if present {
			oldStatus, _ := entry["status"].(string)
			status := oldStatus
			if attribution.status != "" {
				status = sessionFileStatusMerge(status, attribution.status)
			}
			if oldStatus == "?" && status != "?" {
				added += sessionFilesNumber(entry["added"])
				removed += sessionFilesNumber(entry["removed"])
			}
			entry["status"] = status
			agents := make([]any, 0, len(attribution.agents))
			for _, agent := range attribution.agents {
				agents = append(agents, agent)
			}
			entry["agents"] = agents
			if len(attribution.agents) > 0 {
				entry["agent"] = attribution.agents[0]
			} else {
				entry["agent"] = ""
			}
			entry["agent_windows"] = append([]map[string]any(nil), attribution.windows...)
			if attribution.mtime > int64(sessionFilesNumber(entry["mtime"])) {
				entry["mtime"] = attribution.mtime
			}
			entry["diff_tracked"] = entry["source"] == "git" && status != "?"
			continue
		}
		if view.refsActive {
			continue
		}
		entry, _, _ = s.sessionFileEntryWithAttribution(ctx, session, repo, path, "T", gitCommitCount{}, attribution, "transcript")
		if entry != nil {
			view.files = append(view.files, entry)
		}
	}
	sort.SliceStable(view.files, func(left, right int) bool {
		leftMtime := sessionFilesNumber(view.files[left]["mtime"])
		rightMtime := sessionFilesNumber(view.files[right]["mtime"])
		if leftMtime != rightMtime {
			return leftMtime > rightMtime
		}
		leftPath, _ := view.files[left]["path"].(string)
		rightPath, _ := view.files[right]["path"].(string)
		return leftPath < rightPath
	})
	view.repo["count"] = countVisibleSessionFiles(view.files)
	view.repo["touched_count"] = len(view.files)
	view.repo["added"] = added
	view.repo["removed"] = removed
	return view, nil
}
