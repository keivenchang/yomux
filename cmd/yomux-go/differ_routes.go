// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/cache"
	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/stats"
)

const maxSessionFilesHours = 24.0 * 14.0
const maxClaudeTranscriptLookupEntries = 512
const maxClaudeTranscriptPathCacheEntries = 512
const maxSessionFilesRepositoryPaths = 128

type claudeTranscriptLookupCache struct {
	mu          sync.Mutex
	paths       *cache.LRU[string, string]
	find        func(string, string) string
	discoveries *cache.LRU[string, *stats.TranscriptPathDiscovery]
}

func newClaudeTranscriptLookupCache(find func(string, string) string) *claudeTranscriptLookupCache {
	lookup := &claudeTranscriptLookupCache{
		paths:       cache.NewLRU[string, string](maxClaudeTranscriptPathCacheEntries),
		find:        find,
		discoveries: cache.NewLRU[string, *stats.TranscriptPathDiscovery](maxClaudeTranscriptPathCacheEntries),
	}
	if lookup.find == nil {
		lookup.find = lookup.findOnDisk
	}
	return lookup
}

func (lookup *claudeTranscriptLookupCache) transcriptPath(root, sessionID string) string {
	root = filepath.Clean(strings.TrimSpace(root))
	sessionID = strings.TrimSpace(sessionID)
	if root == "." || root == "" || sessionID == "" {
		return ""
	}
	key := root + "\x00" + sessionID
	lookup.mu.Lock()
	cachedPath, ok := lookup.paths.Get(key)
	lookup.mu.Unlock()
	if ok {
		if info, err := os.Stat(cachedPath); err == nil && !info.IsDir() {
			return cachedPath
		}
		lookup.mu.Lock()
		if current, exists := lookup.paths.Get(key); exists && current == cachedPath {
			lookup.paths.Delete(key)
		}
		lookup.mu.Unlock()
	}
	path := lookup.find(root, sessionID)
	if path != "" {
		lookup.mu.Lock()
		lookup.paths.Set(key, path)
		lookup.mu.Unlock()
	}
	return path
}

var claudeTranscriptPaths = newClaudeTranscriptLookupCache(nil)

type sessionFilesRequest struct {
	session   string
	hours     float64
	fromRef   string
	toRef     string
	repoRefs  map[string]map[string]string
	repoPaths []string
	force     bool
	freshGit  bool
	cacheOnly bool
	cacheView string
	// sessionIdentity is filled from tmux immediately before session-scoped work. It is
	// deliberately excluded from the browser request descriptor.
	sessionIdentity string
}

type claudeSessionMetadata struct {
	SessionID string `json:"sessionId"`
	CWD       string `json:"cwd"`
}

func claudeSessionRoots() (string, string) {
	home := strings.TrimSpace(os.Getenv("HOME"))
	if home == "" {
		home, _ = os.UserHomeDir()
	}
	sessionsRoot := filepath.Join(home, ".claude", "sessions")
	transcriptsRoot := strings.TrimSpace(productenv.Value("CLAUDE_TRANSCRIPT_ROOT"))
	if transcriptsRoot == "" {
		transcriptsRoot = filepath.Join(home, ".claude", "projects")
	}
	return sessionsRoot, transcriptsRoot
}

func findClaudeTranscript(transcriptsRoot, sessionID string) string {
	if strings.TrimSpace(transcriptsRoot) == "" || strings.TrimSpace(sessionID) == "" {
		return ""
	}
	return claudeTranscriptPaths.transcriptPath(transcriptsRoot, sessionID)
}

func findClaudeTranscriptOnDisk(transcriptsRoot, sessionID string) string {
	return claudeTranscriptPaths.findOnDisk(transcriptsRoot, sessionID)
}

func (lookup *claudeTranscriptLookupCache) findOnDisk(transcriptsRoot, sessionID string) string {
	key := transcriptsRoot + "\x00" + sessionID
	lookup.mu.Lock()
	discovery, ok := lookup.discoveries.Get(key)
	if !ok {
		discovery = &stats.TranscriptPathDiscovery{}
		lookup.discoveries.Set(key, discovery)
	}
	lookup.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	paths, _ := discovery.Next(ctx, transcriptsRoot, maxClaudeTranscriptLookupEntries)
	for _, path := range paths {
		if filepath.Base(path) == sessionID+".jsonl" {
			return path
		}
	}
	return ""
}

func resolveClaudePaneIdentity(pid, fallbackCWD string) (string, string, string) {
	sessionsRoot, transcriptsRoot := claudeSessionRoots()
	metadataPath := filepath.Join(sessionsRoot, strings.TrimSpace(pid)+".json")
	metadataBytes, err := os.ReadFile(metadataPath)
	if err != nil {
		return "", "", ""
	}
	var metadata claudeSessionMetadata
	if json.Unmarshal(metadataBytes, &metadata) != nil || strings.TrimSpace(metadata.SessionID) == "" {
		return "", "", ""
	}
	cwd := strings.TrimSpace(metadata.CWD)
	if cwd == "" {
		cwd = fallbackCWD
	}
	return metadata.SessionID, findClaudeTranscript(transcriptsRoot, metadata.SessionID), cwd
}

func sessionFilesHours(raw string) (float64, *finder.Error) {
	if strings.TrimSpace(raw) == "" {
		return 24.0, nil
	}
	hours, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsNaN(hours) || math.IsInf(hours, 0) || hours < 0 {
		return 0, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_hours", MessageKey: "request.error.invalid", Message: "hours must be a non-negative number"}
	}
	if hours < 0.25 {
		hours = 0.25
	}
	if hours > maxSessionFilesHours {
		hours = maxSessionFilesHours
	}
	return hours, nil
}

func sessionFilesRequestFromQuery(query url.Values) (sessionFilesRequest, *finder.Error) {
	hours, hoursErr := sessionFilesHours(query.Get("hours"))
	if hoursErr != nil {
		return sessionFilesRequest{}, hoursErr
	}
	fromRaw, hasFrom := query["from"]
	if !hasFrom {
		fromRaw = query["from_ref"]
	}
	toRaw, hasTo := query["to"]
	if !hasTo {
		toRaw = query["to_ref"]
	}
	refsRaw, hasRefs := query["refs"]
	if !hasRefs {
		refsRaw = query["repo_refs"]
	}
	return sessionFilesRequest{
		session:   strings.TrimSpace(query.Get("session")),
		hours:     hours,
		fromRef:   firstQueryValue(fromRaw),
		toRef:     firstQueryValue(toRaw),
		repoRefs:  parseSessionFilesRepoRefs(firstQueryValue(refsRaw)),
		repoPaths: sessionFilesRepositoryPaths(query["repo_path"]),
		force:     queryTrue(query.Get("force")),
		freshGit:  queryTrue(query.Get("fresh_git")),
		cacheOnly: queryTrue(query.Get("cache_only")),
		cacheView: strings.TrimSpace(query.Get("cache_view")),
	}, nil
}

func firstQueryValue(values []string) string {
	if len(values) == 0 {
		return ""
	}
	return strings.TrimSpace(values[0])
}

func sessionFilesRepositoryPaths(values []string) []string {
	paths := make([]string, 0, min(len(values), maxSessionFilesRepositoryPaths))
	seen := make(map[string]bool, len(values))
	for _, raw := range values {
		path := strings.TrimSpace(raw)
		if path == "" || seen[path] {
			continue
		}
		seen[path] = true
		paths = append(paths, path)
		if len(paths) == maxSessionFilesRepositoryPaths {
			break
		}
	}
	sort.Strings(paths)
	return paths
}

func sessionFilesStreamRoots(visiblePaths, liveRoots []string) []string {
	roots := make([]string, 0, min(len(visiblePaths)+len(liveRoots), maxSessionFilesRepositoryPaths))
	seen := make(map[string]bool, len(visiblePaths)+len(liveRoots))
	for _, values := range [][]string{visiblePaths, liveRoots} {
		for _, raw := range values {
			path := strings.TrimSpace(raw)
			if path == "" || seen[path] {
				continue
			}
			seen[path] = true
			roots = append(roots, path)
			if len(roots) == maxSessionFilesRepositoryPaths {
				return roots
			}
		}
	}
	return roots
}

func sessionFilesPendingEnvelope(session string) map[string]any {
	return map[string]any{
		"state": "queued", "request": map[string]any{"id": finderRequestID("session-files-cache")},
		"status": "pending", "retry_after_seconds": 1,
		"reason": "the requested session-files cache view is not ready", "session": session,
		"ok": true, "terminal": false,
	}
}

func parseSessionFilesRepoRefs(raw string) map[string]map[string]string {
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	var decoded map[string]map[string]any
	if json.Unmarshal([]byte(raw), &decoded) != nil {
		return nil
	}
	result := make(map[string]map[string]string)
	for repo, values := range decoded {
		refs := make(map[string]string, 2)
		for _, keys := range [][2]string{{"from", "from_ref"}, {"to", "to_ref"}} {
			value, exists := values[keys[0]]
			if !exists {
				value = values[keys[1]]
			}
			if text, ok := value.(string); ok && strings.TrimSpace(text) != "" {
				refs[keys[0]] = strings.TrimSpace(text)
			}
		}
		if len(refs) > 0 {
			result[repo] = refs
		}
	}
	if len(result) == 0 {
		return nil
	}
	return result
}

func (s *server) sessionFilesSessionIdentity(session string) (string, bool) {
	if strings.TrimSpace(session) == "" {
		return "", false
	}
	output, err := s.tmuxCommand("display-message", "-p", "-t", s.exactTarget(session), "#{session_id}\t#{session_created}\t#{pid}").Output()
	if err != nil {
		return "", false
	}
	fields := strings.SplitN(strings.TrimSpace(string(output)), "\t", 3)
	if len(fields) != 3 || strings.TrimSpace(fields[0]) == "" || strings.TrimSpace(fields[1]) == "" || strings.TrimSpace(fields[2]) == "" {
		return "", false
	}
	return goTmuxSessionIdentity(session, strings.Join([]string{
		strings.TrimSpace(fields[0]), strings.TrimSpace(fields[1]), strings.TrimSpace(fields[2]),
	}, "\x1f")), true
}

func (s *server) sessionFilesIdentityMatches(session, expected string) bool {
	if expected == "" {
		return false
	}
	current, ok := s.sessionFilesSessionIdentity(session)
	return ok && current == expected
}

func sessionFilesIncarnationChangedError(session string) *finder.Error {
	return &finder.Error{Status: http.StatusServiceUnavailable, Code: "session_incarnation_changed", MessageKey: "common.requestFailed", Path: session, Retryable: true, Message: "session changed while session files were being read"}
}

func (s *server) sessionFilesIdentityUnavailableError(session string) *finder.Error {
	if s.tmuxCommand("has-session", "-t", s.exactTarget(session)).Run() != nil {
		return &finder.Error{Status: http.StatusNotFound, Code: "unknown_session", MessageKey: "status.sessionEnded", Path: session, Message: "unknown session"}
	}
	return sessionFilesIncarnationChangedError(session)
}

func (s *server) sessionFilesPaneInputs(session string) ([]string, []finder.SessionFileAgent, *finder.Error) {
	if strings.TrimSpace(session) == "" {
		return nil, nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_session", MessageKey: "request.error.invalid", Message: "session is required"}
	}
	if s.tmuxCommand("has-session", "-t", session).Run() != nil {
		return nil, nil, &finder.Error{Status: http.StatusNotFound, Code: "unknown_session", MessageKey: "status.sessionEnded", Path: session, Message: "unknown session"}
	}
	format := "#{pane_current_path}\t#{pane_current_command}\t#{pane_id}\t#{window_index}\t#{pane_index}\t#{pane_pid}\t#{pane_title}\t#{pane_active}\t#{window_active}"
	output, err := s.tmuxCommand("list-panes", "-s", "-t", s.exactTarget(session), "-F", format).Output()
	if err != nil {
		return nil, nil, &finder.Error{Status: http.StatusServiceUnavailable, Code: "session_roots_unavailable", MessageKey: "common.requestFailed", Path: session, Retryable: true, Message: "session roots unavailable"}
	}
	roots := make([]string, 0)
	agents := make([]finder.SessionFileAgent, 0)
	seen := make(map[string]bool)
	for _, line := range strings.Split(string(output), "\n") {
		parts := splitTmuxFields(line)
		root := ""
		command := ""
		paneTarget := ""
		window := ""
		pane := ""
		panePID := ""
		title := ""
		active := false
		windowActive := false
		if len(parts) >= 6 {
			root, command, paneTarget, window, pane = strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1]), strings.TrimSpace(parts[2]), strings.TrimSpace(parts[3]), strings.TrimSpace(parts[4])
			panePID = strings.TrimSpace(parts[5])
			if len(parts) >= 9 {
				title = strings.TrimSpace(parts[6])
				active = strings.TrimSpace(parts[7]) == "1"
				windowActive = strings.TrimSpace(parts[8]) == "1"
			}
		} else if len(parts) >= 5 {
			root, command, paneTarget, window, pane = strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1]), strings.TrimSpace(parts[2]), strings.TrimSpace(parts[3]), strings.TrimSpace(parts[4])
		} else {
			root = strings.TrimSpace(line)
		}
		if root == "" || seen[root] {
			if root == "" {
				continue
			}
		} else {
			seen[root] = true
			roots = append(roots, root)
		}
		panePIDValue, _ := strconv.Atoi(panePID)
		kind, agentPID, _, _ := s.paneAgentProcess(panePIDValue, command)
		if kind != "" {
			windowIndex, _ := strconv.Atoi(window)
			agent := finder.SessionFileAgent{Kind: kind, CWD: root, PaneTarget: paneTarget, Command: command, PID: agentPID, Window: window, WindowIndex: windowIndex, Pane: pane, Active: active, WindowActive: windowActive}
			if sessionID, transcript, cwd := agentTranscriptWithTitle(kind, agentPID, root, title); sessionID != "" || transcript != "" {
				agent.SessionID, agent.Transcript, agent.CWD = sessionID, transcript, cwd
			}
			agents = append(agents, agent)
		}
	}
	return roots, agents, nil
}

func (s *server) aggregateSessionFiles(ctx context.Context, sessions []string, request sessionFilesRequest) (map[string]any, *finder.Error) {
	payloads := make([]map[string]any, 0, len(sessions))
	for _, session := range sessions {
		itemRequest := request
		itemRequest.session = session
		plan := newSessionFilesPlan(itemRequest)
		roots, agents, rootErr := s.sessionFilesPlanInputs(plan)
		if rootErr != nil {
			return nil, rootErr
		}
		payload, finderErr := s.sessionFilesPlanTaskWithInputs(plan, roots, agents)(ctx)
		if finderErr != nil {
			return nil, finderErr
		}
		payloads = append(payloads, payload)
	}
	return finder.AggregateSessionFiles(payloads, request.hours, request.fromRef, request.toRef), nil
}

func (s *server) sessionFiles(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	request, requestErr := sessionFilesRequestFromQuery(r.URL.Query())
	if requestErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, requestErr)
		return
	}
	plan := newSessionFilesPlan(request)
	descriptor, viewID := plan.descriptor, plan.viewID
	if request.cacheOnly {
		var record sessionFilesCacheRecord
		var ok bool
		if request.session != "" {
			request, record, ok = s.readLiveSessionFilesCache(request, request.cacheView)
		} else {
			record, ok = s.readSessionFilesCache(request, request.cacheView)
		}
		if !ok || sessionFilesCacheAge(record, time.Now()) > sessionFilesCacheTTL.Seconds() {
			jsonResponse(w, http.StatusAccepted, sessionFilesPendingEnvelope(request.session))
			return
		}
		finderHTTPJSON(w, finderRequestIDForHTTP(), sessionFilesPayloadWithCache(record, descriptor, request.cacheView, true, false, false, time.Now()), nil)
		return
	}

	if request.session != "" {
		admission := s.admitLiveSessionFilesCache(request, viewID)
		request = admission.request
		if admission.hit {
			finderHTTPJSON(w, finderRequestIDForHTTP(), sessionFilesPayloadWithCache(admission.record, descriptor, viewID, true, admission.stale, admission.refreshing, time.Now()), nil)
			return
		}
	}

	var task func(context.Context) (map[string]any, *finder.Error)
	var planRoots []string
	var planAgents []finder.SessionFileAgent
	if request.session == "" {
		sessions := s.sessionNames()
		task = func(ctx context.Context) (map[string]any, *finder.Error) {
			return s.aggregateSessionFiles(ctx, sessions, request)
		}
	} else {
		if request.sessionIdentity == "" {
			identity, ok := s.sessionFilesSessionIdentity(request.session)
			if !ok {
				finderHTTPJSON(w, finderRequestIDForHTTP(), nil, s.sessionFilesIdentityUnavailableError(request.session))
				return
			}
			request.sessionIdentity = identity
		}
		var rootErr *finder.Error
		planRoots, planAgents, rootErr = s.sessionFilesPlanInputs(plan)
		if rootErr != nil {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, rootErr)
			return
		}
		if request.sessionIdentity != "" && !s.sessionFilesIdentityMatches(request.session, request.sessionIdentity) {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, sessionFilesIncarnationChangedError(request.session))
			return
		}
		plan.request = request
		task = s.sessionFilesPlanTaskWithInputs(plan, planRoots, planAgents)
	}

	if !request.freshGit && !request.force && request.session == "" {
		if record, ok := s.readSessionFilesCache(request, viewID); ok && sessionFilesCacheRecordMatchesRequestIdentity(request, record) {
			stale := sessionFilesCacheAge(record, time.Now()) > sessionFilesCacheTTL.Seconds()
			refreshing := false
			if stale {
				refreshing = s.scheduleSessionFilesRefresh(request, viewID, task)
			}
			finderHTTPJSON(w, finderRequestIDForHTTP(), sessionFilesPayloadWithCache(record, descriptor, viewID, true, stale, refreshing, time.Now()), nil)
			return
		}
	}

	compute := func(ctx context.Context) (map[string]any, *finder.Error) {
		var payload map[string]any
		var finderErr *finder.Error
		if request.session != "" {
			payload, finderErr = s.sessionFilesPlanProduceAndCacheWithInputs(ctx, plan, planRoots, planAgents, nil)
		} else {
			generation := s.beginSessionFilesCacheWrite(viewID)
			payload, finderErr = task(ctx)
			if finderErr == nil {
				s.writeSessionFilesCache(request, viewID, payload, http.StatusOK, generation)
			}
		}
		if finderErr != nil {
			return payload, finderErr
		}
		if record, ok := s.readSessionFilesCache(request, viewID); ok && sessionFilesCacheRecordMatchesRequestIdentity(request, record) {
			return sessionFilesPayloadWithCache(record, descriptor, viewID, false, false, false, time.Now()), nil
		}
		payload["cache"] = sessionFilesCacheMetadata(sessionFilesCacheRecord{StoredAt: time.Now().UnixNano()}, descriptor, viewID, false, false, time.Now())
		return payload, nil
	}
	s.enqueueFinderJSONWithScheduler(w, r.Context(), s.finderBackgroundScheduler(), r.URL.Path, "session_files", s.root, 0, compute)
}

func sessionFilesPayloadRows(payload map[string]any, key string) []map[string]any {
	rows := make([]map[string]any, 0)
	switch values := payload[key].(type) {
	case []map[string]any:
		return append(rows, values...)
	case []any:
		for _, value := range values {
			if row, ok := value.(map[string]any); ok {
				rows = append(rows, row)
			}
		}
	}
	return rows
}

func sessionFilesCachedChunks(payload map[string]any) []map[string]any {
	filesByRepo := make(map[string][]map[string]any)
	for _, file := range sessionFilesPayloadRows(payload, "files") {
		repo, _ := file["repo"].(string)
		filesByRepo[repo] = append(filesByRepo[repo], file)
	}
	refsByRepo, _ := payload["refs_by_repo"].(map[string]any)
	repos := sessionFilesPayloadRows(payload, "repos")
	chunks := make([]map[string]any, 0, len(repos))
	for _, repo := range repos {
		repoPath, _ := repo["repo"].(string)
		refs := any(nil)
		if refsByRepo != nil {
			refs = refsByRepo[repoPath]
		}
		chunks = append(chunks, map[string]any{
			"session": payload["session"], "hours": payload["hours"],
			"files": filesByRepo[repoPath], "repos": []map[string]any{repo},
			"refs_by_repo": map[string]any{repoPath: refs},
			"from_ref":     payload["from_ref"], "to_ref": payload["to_ref"],
			"warnings": payload["warnings"], "errors": []any{}, "loaded": false,
		})
	}
	return chunks
}

func sessionFilesStreamCacheMetadata(record sessionFilesCacheRecord, requestDescriptor, viewID string, stale, refreshing bool, now time.Time) map[string]any {
	metadata := sessionFilesCacheMetadata(record, requestDescriptor, viewID, true, stale, now)
	if refreshing {
		metadata["refreshing"] = true
	}
	return metadata
}

func (s *server) sessionFilesStream(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	request, requestErr := sessionFilesRequestFromQuery(r.URL.Query())
	if requestErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, requestErr)
		return
	}
	if request.session == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{
			Status: http.StatusBadRequest, Code: "session_required", MessageKey: "request.error.invalidQuery",
			Message: "session is required for session-files streaming",
		})
		return
	}
	plan := newSessionFilesPlan(request)
	descriptor, viewID := plan.descriptor, plan.viewID
	stream, ok := newJSONSSEWriter(w, r.Context())
	if !ok {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusInternalServerError, Code: "streaming_unavailable", MessageKey: "common.requestFailed", Message: "streaming unavailable"})
		return
	}
	defer stream.close()
	writeEvent := stream.write
	if err := writeEvent("start", map[string]any{"session": request.session, "from_ref": request.fromRef, "to_ref": request.toRef}); err != nil {
		return
	}

	admission := s.admitLiveSessionFilesCache(request, viewID)
	request = admission.request
	if admission.hit {
		payload := sessionFilesCachePayload(admission.record)
		if payload == nil {
			_ = writeEvent("error", map[string]any{"error": "cached session-files payload is invalid"})
			return
		}
		for _, chunk := range sessionFilesCachedChunks(payload) {
			if err := writeEvent("repo", chunk); err != nil {
				return
			}
		}
		_ = writeEvent("done", map[string]any{
			"session": request.session, "loaded": true, "errors": []any{}, "warnings": payload["warnings"],
			"from_ref": payload["from_ref"], "to_ref": payload["to_ref"],
			"cache": sessionFilesStreamCacheMetadata(admission.record, descriptor, viewID, admission.stale, admission.refreshing, time.Now()),
		})
		return
	}
	if request.sessionIdentity == "" {
		identity, ok := s.sessionFilesSessionIdentity(request.session)
		if !ok {
			_ = writeEvent("error", s.sessionFilesIdentityUnavailableError(request.session).Payload())
			return
		}
		request.sessionIdentity = identity
	}
	plan.request = request
	roots, agents, rootErr := s.sessionFilesPlanInputs(plan)
	if rootErr != nil {
		_ = writeEvent("error", rootErr.Payload())
		return
	}
	if !s.sessionFilesIdentityMatches(request.session, request.sessionIdentity) {
		_ = writeEvent("error", sessionFilesIncarnationChangedError(request.session).Payload())
		return
	}
	// sessionFilesPlanInputs puts explicitly visible repositories before pane roots for every
	// producer of this cache view. The initial /api/fs/fast/list remains independent of this work.
	payload, finderErr := s.sessionFilesPlanProduceAndCacheWithInputs(r.Context(), plan, roots, agents, func(chunk map[string]any) error {
		return writeEvent("repo", chunk)
	})
	if finderErr != nil {
		if r.Context().Err() == nil {
			_ = writeEvent("error", finderErr.Payload())
		}
		return
	}
	_ = writeEvent("done", map[string]any{
		"session": request.session, "loaded": true, "errors": payload["errors"], "warnings": payload["warnings"],
		"from_ref": payload["from_ref"], "to_ref": payload["to_ref"],
		"cache": sessionFilesCacheMetadata(sessionFilesCacheRecord{StoredAt: time.Now().UnixNano()}, descriptor, viewID, false, false, time.Now()),
	})
}

func (s *server) sessionFilesBatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	request, requestErr := sessionFilesRequestFromQuery(r.URL.Query())
	if requestErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, requestErr)
		return
	}
	sessions := append([]string(nil), r.URL.Query()["session"]...)
	if len(sessions) == 0 {
		sessions = append(sessions, r.URL.Query()["sessions"]...)
	}
	if len(sessions) == 0 {
		sessions = s.sessionNames()
	}
	deduped := make([]string, 0, len(sessions))
	seen := make(map[string]bool, len(sessions))
	for _, raw := range sessions {
		name := strings.TrimSpace(raw)
		if name == "" || seen[name] {
			continue
		}
		seen[name] = true
		deduped = append(deduped, name)
	}
	sessions = deduped
	request.session = ""
	s.enqueueFinderJSONWithScheduler(w, r.Context(), s.finderBackgroundScheduler(), r.URL.Path, "session_files_batch", s.root, 0, func(ctx context.Context) (map[string]any, *finder.Error) {
		payloads, statuses, batchErr := runSessionFilesBatch(ctx, sessions, func(ctx context.Context, rawSession string) (map[string]any, int) {
			name := strings.TrimSpace(rawSession)
			if name == "" {
				return nil, 0
			}
			itemRequest := request
			itemRequest.session = name
			descriptor := sessionFilesRequestDescriptor(itemRequest)
			viewID := sessionFilesCacheViewID(descriptor)
			if !itemRequest.freshGit && !itemRequest.force {
				admission := s.admitLiveSessionFilesCache(itemRequest, viewID)
				itemRequest = admission.request
				if admission.hit {
					return sessionFilesPayloadWithCache(admission.record, descriptor, viewID, true, admission.stale, admission.refreshing, time.Now()), http.StatusOK
				}
			}
			if itemRequest.sessionIdentity == "" {
				identity, alive := s.sessionFilesSessionIdentity(name)
				if !alive {
					rootErr := s.sessionFilesIdentityUnavailableError(name)
					return sessionFilesBatchErrorPayload(name, rootErr), rootErr.Status
				}
				itemRequest.sessionIdentity = identity
			}
			itemPlan := newSessionFilesPlan(itemRequest)
			roots, agents, rootErr := s.sessionFilesPlanInputs(itemPlan)
			if rootErr != nil {
				return sessionFilesBatchErrorPayload(name, rootErr), rootErr.Status
			}
			if !s.sessionFilesIdentityMatches(name, itemRequest.sessionIdentity) {
				rootErr = sessionFilesIncarnationChangedError(name)
				return sessionFilesBatchErrorPayload(name, rootErr), rootErr.Status
			}
			payload, finderErr := s.sessionFilesPlanProduceAndCacheWithInputs(ctx, itemPlan, roots, agents, nil)
			if finderErr != nil {
				return sessionFilesBatchErrorPayload(name, finderErr), finderErr.Status
			}
			if record, ok := s.readSessionFilesCache(itemRequest, viewID); ok && sessionFilesCacheRecordMatchesRequestIdentity(itemRequest, record) {
				payload = sessionFilesPayloadWithCache(record, descriptor, viewID, false, false, false, time.Now())
			}
			return payload, http.StatusOK
		})
		if batchErr != nil {
			return nil, &finder.Error{Status: http.StatusRequestTimeout, Code: "cancelled", MessageKey: "request.error.cancelled", Retryable: true, Message: batchErr.Error()}
		}
		return map[string]any{"sessions": payloads, "statuses": statuses, "errors": []any{}, "loaded": true}, nil
	})
}
