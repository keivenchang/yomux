// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/keivenchang/yolomux/internal/finder"
)

const maxSessionFilesHours = 24.0 * 14.0
const maxClaudeTranscriptLookupEntries = 512
const sessionFilesCacheMaxEntries = 128
const sessionFilesCacheTTL = 30 * time.Second
const maxSessionFilesRepositoryPaths = 128

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
}

type sessionFilesCacheRecord struct {
	RequestDescriptor string         `json:"request_descriptor"`
	CacheViewID       string         `json:"cache_view_id"`
	StoredAt          int64          `json:"stored_at"`
	Status            int            `json:"status"`
	Payload           map[string]any `json:"payload"`
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
	transcriptsRoot := strings.TrimSpace(os.Getenv("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT"))
	if transcriptsRoot == "" {
		transcriptsRoot = filepath.Join(home, ".claude", "projects")
	}
	return sessionsRoot, transcriptsRoot
}

func findClaudeTranscript(transcriptsRoot, sessionID string) string {
	if strings.TrimSpace(transcriptsRoot) == "" || strings.TrimSpace(sessionID) == "" {
		return ""
	}
	wanted := sessionID + ".jsonl"
	entries := 0
	var found string
	_ = filepath.WalkDir(transcriptsRoot, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil || entry == nil || found != "" {
			return nil
		}
		if entry.IsDir() {
			return nil
		}
		entries++
		if entry.Name() == wanted {
			found = filepath.Clean(path)
			return filepath.SkipAll
		}
		if entries >= maxClaudeTranscriptLookupEntries {
			return filepath.SkipAll
		}
		return nil
	})
	return found
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
	return sessionFilesRequest{
		session:   strings.TrimSpace(query.Get("session")),
		hours:     hours,
		fromRef:   strings.TrimSpace(query.Get("from")),
		toRef:     strings.TrimSpace(query.Get("to")),
		repoRefs:  parseSessionFilesRepoRefs(query.Get("refs")),
		repoPaths: sessionFilesRepositoryPaths(query["repo_path"]),
		force:     queryTrue(query.Get("force")),
		freshGit:  queryTrue(query.Get("fresh_git")),
		cacheOnly: queryTrue(query.Get("cache_only")),
		cacheView: strings.TrimSpace(query.Get("cache_view")),
	}, nil
}

func sessionFilesRepositoryPaths(values []string) []string {
	paths := make([]string, 0, minInt(len(values), maxSessionFilesRepositoryPaths))
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
	roots := make([]string, 0, minInt(len(visiblePaths)+len(liveRoots), maxSessionFilesRepositoryPaths))
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

func sessionFilesRequestDescriptor(request sessionFilesRequest) string {
	hours := request.hours
	if hours == math.Trunc(hours) {
		// Match the browser/Python descriptor's JSON number normalization: 24 and 24.0
		// identify the same logical session-files view.
		hours = math.Trunc(hours)
	}
	canonical := struct {
		Kind      string                       `json:"kind"`
		Session   string                       `json:"session"`
		Hours     float64                      `json:"hours"`
		FromRef   string                       `json:"from_ref"`
		ToRef     string                       `json:"to_ref"`
		RepoRefs  map[string]map[string]string `json:"repo_refs,omitempty"`
		RepoPaths []string                     `json:"repo_paths,omitempty"`
	}{
		Kind: "session-files-request", Session: request.session, Hours: hours,
		FromRef: request.fromRef, ToRef: request.toRef, RepoRefs: request.repoRefs,
		RepoPaths: append([]string(nil), request.repoPaths...),
	}
	encoded, _ := json.Marshal(canonical)
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func sessionFilesCacheViewID(requestDescriptor string) string {
	digest := sha256.Sum256([]byte("session-files-view\x1f" + requestDescriptor))
	return hex.EncodeToString(digest[:])
}

func cloneSessionFilesPayload(payload map[string]any) map[string]any {
	if payload == nil {
		return nil
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return cloneFinderMap(payload)
	}
	var cloned map[string]any
	if json.Unmarshal(encoded, &cloned) != nil {
		return cloneFinderMap(payload)
	}
	return cloned
}

func sessionFilesCacheAge(record sessionFilesCacheRecord, now time.Time) float64 {
	if record.StoredAt <= 0 {
		return 0
	}
	age := now.Sub(time.Unix(0, record.StoredAt)).Seconds()
	if age < 0 {
		return 0
	}
	return age
}

func sessionFilesCacheMetadata(record sessionFilesCacheRecord, requestDescriptor, viewID string, hit, stale bool, now time.Time) map[string]any {
	return map[string]any{
		"hit":                hit,
		"stale":              stale,
		"age_seconds":        math.Round(sessionFilesCacheAge(record, now)*1000) / 1000,
		"refresh_seconds":    sessionFilesCacheTTL.Seconds(),
		"request_descriptor": requestDescriptor,
		"cache_view_id":      viewID,
	}
}

func (s *server) sessionFilesCacheDirectory() string {
	state := strings.TrimSpace(s.state)
	if state == "" {
		state = strings.TrimSpace(s.config)
	}
	if state == "" {
		return ""
	}
	return filepath.Join(state, "session-files-cache")
}

func (s *server) readSessionFilesCache(request sessionFilesRequest, viewID string) (sessionFilesCacheRecord, bool) {
	descriptor := sessionFilesRequestDescriptor(request)
	if len(viewID) != 64 || strings.Trim(viewID, "0123456789abcdef") != "" {
		return sessionFilesCacheRecord{}, false
	}
	s.sessionFilesCacheMu.Lock()
	record, ok := s.sessionFilesCache[viewID]
	s.sessionFilesCacheMu.Unlock()
	if ok && record.RequestDescriptor == descriptor && record.CacheViewID == viewID && record.StoredAt > 0 && record.Status >= http.StatusOK && record.Status < http.StatusMultipleChoices && record.Payload != nil {
		return sessionFilesCacheRecord{RequestDescriptor: record.RequestDescriptor, CacheViewID: record.CacheViewID, StoredAt: record.StoredAt, Status: record.Status, Payload: cloneSessionFilesPayload(record.Payload)}, true
	}
	directory := s.sessionFilesCacheDirectory()
	if directory == "" {
		return sessionFilesCacheRecord{}, false
	}
	path := filepath.Join(directory, viewID+".json")
	fileDescriptor, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return sessionFilesCacheRecord{}, false
	}
	file := os.NewFile(uintptr(fileDescriptor), path)
	if file == nil {
		_ = syscall.Close(fileDescriptor)
		return sessionFilesCacheRecord{}, false
	}
	fileInfo, statErr := file.Stat()
	if statErr != nil || !fileInfo.Mode().IsRegular() {
		_ = file.Close()
		return sessionFilesCacheRecord{}, false
	}
	data, err := io.ReadAll(io.LimitReader(file, 16<<20))
	closeErr := file.Close()
	if err != nil || closeErr != nil {
		return sessionFilesCacheRecord{}, false
	}
	var diskRecord sessionFilesCacheRecord
	if json.Unmarshal(data, &diskRecord) != nil || diskRecord.RequestDescriptor != descriptor || diskRecord.CacheViewID != viewID || diskRecord.Payload == nil {
		return sessionFilesCacheRecord{}, false
	}
	if diskRecord.StoredAt <= 0 || diskRecord.Status < http.StatusOK || diskRecord.Status >= http.StatusMultipleChoices {
		return sessionFilesCacheRecord{}, false
	}
	s.sessionFilesCacheMu.Lock()
	if s.sessionFilesCache == nil {
		s.sessionFilesCache = make(map[string]sessionFilesCacheRecord)
	}
	if len(s.sessionFilesCache) >= sessionFilesCacheMaxEntries {
		clear(s.sessionFilesCache)
	}
	s.sessionFilesCache[viewID] = sessionFilesCacheRecord{RequestDescriptor: diskRecord.RequestDescriptor, CacheViewID: diskRecord.CacheViewID, StoredAt: diskRecord.StoredAt, Status: diskRecord.Status, Payload: cloneSessionFilesPayload(diskRecord.Payload)}
	s.sessionFilesCacheMu.Unlock()
	return diskRecord, true
}

func sessionFilesPayloadWithCache(record sessionFilesCacheRecord, requestDescriptor, viewID string, hit, stale, refreshing bool, now time.Time) map[string]any {
	payload := cloneSessionFilesPayload(record.Payload)
	if payload == nil {
		payload = make(map[string]any)
	}
	metadata := sessionFilesCacheMetadata(record, requestDescriptor, viewID, hit, stale, now)
	if refreshing {
		metadata["refreshing"] = true
	}
	payload["cache"] = metadata
	return payload
}

func (s *server) beginSessionFilesRefresh(viewID string) bool {
	s.sessionFilesCacheMu.Lock()
	defer s.sessionFilesCacheMu.Unlock()
	if s.sessionFilesRefresh == nil {
		s.sessionFilesRefresh = make(map[string]bool)
	}
	if s.sessionFilesRefresh[viewID] {
		return false
	}
	s.sessionFilesRefresh[viewID] = true
	return true
}

func (s *server) finishSessionFilesRefresh(viewID string) {
	s.sessionFilesCacheMu.Lock()
	delete(s.sessionFilesRefresh, viewID)
	s.sessionFilesCacheMu.Unlock()
}

func (s *server) scheduleSessionFilesRefresh(request sessionFilesRequest, viewID string, task func(context.Context) (map[string]any, *finder.Error)) bool {
	if !s.beginSessionFilesRefresh(viewID) {
		return false
	}
	finish := func() { s.finishSessionFilesRefresh(viewID) }
	parent, cancel := newFinderJobContext(context.Background(), finderJobDeadline)
	scheduler := s.finderBackgroundScheduler()
	if scheduler == nil {
		go func() {
			defer cancel()
			defer finish()
			payload, finderErr := task(parent)
			if finderErr == nil {
				s.writeSessionFilesCache(request, viewID, payload, http.StatusOK)
			}
		}()
		return true
	}
	result, err := scheduler.Submit(parent, "", func(ctx context.Context) (any, error) {
		payload, finderErr := task(ctx)
		if finderErr != nil {
			return finderErr, nil
		}
		return payload, nil
	})
	if err != nil {
		cancel()
		finish()
		return false
	}
	go func() {
		defer cancel()
		defer finish()
		completed := <-result
		if completed.Err != nil {
			return
		}
		payload, ok := completed.Value.(map[string]any)
		if ok {
			s.writeSessionFilesCache(request, viewID, payload, http.StatusOK)
		}
	}()
	return true
}

func (s *server) writeSessionFilesCache(request sessionFilesRequest, viewID string, payload map[string]any, status int) {
	directory := s.sessionFilesCacheDirectory()
	if directory == "" || payload == nil || status < http.StatusOK || status >= http.StatusMultipleChoices {
		return
	}
	record := sessionFilesCacheRecord{
		RequestDescriptor: sessionFilesRequestDescriptor(request), CacheViewID: viewID,
		StoredAt: time.Now().UnixNano(), Status: status, Payload: cloneSessionFilesPayload(payload),
	}
	s.sessionFilesCacheMu.Lock()
	if s.sessionFilesCache == nil {
		s.sessionFilesCache = make(map[string]sessionFilesCacheRecord)
	}
	if len(s.sessionFilesCache) >= sessionFilesCacheMaxEntries {
		clear(s.sessionFilesCache)
	}
	s.sessionFilesCache[viewID] = sessionFilesCacheRecord{RequestDescriptor: record.RequestDescriptor, CacheViewID: record.CacheViewID, StoredAt: record.StoredAt, Status: record.Status, Payload: cloneSessionFilesPayload(record.Payload)}
	s.sessionFilesCacheMu.Unlock()
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return
	}
	data, err := json.Marshal(record)
	if err != nil {
		return
	}
	temporary, err := os.CreateTemp(directory, ".session-files-*.tmp")
	if err != nil {
		return
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(0o600); err == nil {
		if _, err = temporary.Write(data); err == nil {
			err = temporary.Sync()
		}
	}
	if closeErr := temporary.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		_ = os.Rename(temporaryName, filepath.Join(directory, viewID+".json"))
	}
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
		for _, key := range []string{"from", "to"} {
			if value, ok := values[key].(string); ok && strings.TrimSpace(value) != "" {
				refs[key] = strings.TrimSpace(value)
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

func (s *server) sessionFilesPaneRoots(session string) ([]string, *finder.Error) {
	roots, _, finderErr := s.sessionFilesPaneInputs(session)
	return roots, finderErr
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

func (s *server) aggregateSessionFiles(ctx context.Context, sessions []string, hours float64, fromRef, toRef string, repoRefs map[string]map[string]string) (map[string]any, *finder.Error) {
	payloads := make([]map[string]any, 0, len(sessions))
	for _, session := range sessions {
		roots, agents, rootErr := s.sessionFilesPaneInputs(session)
		if rootErr != nil {
			return nil, rootErr
		}
		payload, finderErr := s.finder.SessionFilesWithAgents(ctx, session, roots, agents, hours, fromRef, toRef, repoRefs)
		if finderErr != nil {
			return nil, finderErr
		}
		payloads = append(payloads, payload)
	}
	return finder.AggregateSessionFiles(payloads, hours, fromRef, toRef), nil
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
	descriptor := sessionFilesRequestDescriptor(request)
	viewID := sessionFilesCacheViewID(descriptor)
	if request.cacheOnly {
		record, ok := s.readSessionFilesCache(request, request.cacheView)
		if !ok || sessionFilesCacheAge(record, time.Now()) > sessionFilesCacheTTL.Seconds() {
			jsonResponse(w, http.StatusAccepted, sessionFilesPendingEnvelope(request.session))
			return
		}
		finderHTTPJSON(w, finderRequestIDForHTTP(), sessionFilesPayloadWithCache(record, descriptor, request.cacheView, true, false, false, time.Now()), nil)
		return
	}

	var task func(context.Context) (map[string]any, *finder.Error)
	if request.session == "" {
		sessions := s.sessionNames()
		task = func(ctx context.Context) (map[string]any, *finder.Error) {
			return s.aggregateSessionFiles(ctx, sessions, request.hours, request.fromRef, request.toRef, request.repoRefs)
		}
	} else {
		roots, agents, rootErr := s.sessionFilesPaneInputs(request.session)
		if rootErr != nil {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, rootErr)
			return
		}
		task = func(ctx context.Context) (map[string]any, *finder.Error) {
			return s.finder.SessionFilesWithAgents(ctx, request.session, roots, agents, request.hours, request.fromRef, request.toRef, request.repoRefs)
		}
	}

	if !request.freshGit {
		if record, ok := s.readSessionFilesCache(request, viewID); ok {
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
		payload, finderErr := task(ctx)
		if finderErr != nil {
			return payload, finderErr
		}
		s.writeSessionFilesCache(request, viewID, payload, http.StatusOK)
		if record, ok := s.readSessionFilesCache(request, viewID); ok {
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
	chunks := make([]map[string]any, 0)
	repos := sessionFilesPayloadRows(payload, "repos")
	for index, repo := range repos {
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
			"repo_index": index, "repo_total": len(repos),
		})
	}
	return chunks
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
	roots, agents, rootErr := s.sessionFilesPaneInputs(request.session)
	if rootErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, rootErr)
		return
	}
	// Finder supplies repository directories that were visible in its cheap first listing. Put those
	// explicit paths before live pane roots so an unrelated non-Git CWD cannot delay the first visible
	// repository chunk. The initial /api/fs/fast/list remains completely independent of this work.
	streamRoots := sessionFilesStreamRoots(request.repoPaths, roots)
	descriptor := sessionFilesRequestDescriptor(request)
	viewID := sessionFilesCacheViewID(descriptor)
	flusher, ok := w.(http.Flusher)
	if !ok {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusInternalServerError, Code: "streaming_unavailable", MessageKey: "common.requestFailed", Message: "streaming unavailable"})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	writeEvent := func(event string, payload any) error {
		data, marshalErr := json.Marshal(payload)
		if marshalErr != nil {
			return marshalErr
		}
		if _, writeErr := io.WriteString(w, "event: "+event+"\ndata: "+string(data)+"\n\n"); writeErr != nil {
			return writeErr
		}
		flusher.Flush()
		return nil
	}
	if err := writeEvent("start", map[string]any{"session": request.session, "from_ref": request.fromRef, "to_ref": request.toRef}); err != nil {
		return
	}

	if !request.freshGit {
		if record, cacheHit := s.readSessionFilesCache(request, viewID); cacheHit && sessionFilesCacheAge(record, time.Now()) <= sessionFilesCacheTTL.Seconds() {
			for _, chunk := range sessionFilesCachedChunks(record.Payload) {
				if err := writeEvent("repo", chunk); err != nil {
					return
				}
			}
			_ = writeEvent("done", map[string]any{
				"session": request.session, "loaded": true, "errors": []any{}, "warnings": record.Payload["warnings"],
				"from_ref": record.Payload["from_ref"], "to_ref": record.Payload["to_ref"],
				"cache": sessionFilesCacheMetadata(record, descriptor, viewID, true, false, time.Now()),
			})
			return
		}
	}

	payload, finderErr := s.finder.SessionFilesWithAgentsStream(r.Context(), request.session, streamRoots, agents, request.hours, request.fromRef, request.toRef, request.repoRefs, func(chunk map[string]any) error {
		return writeEvent("repo", chunk)
	})
	if finderErr != nil {
		if r.Context().Err() == nil {
			_ = writeEvent("error", finderErr.Payload())
		}
		return
	}
	s.writeSessionFilesCache(request, viewID, payload, http.StatusOK)
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
		payloads := make(map[string]any, len(sessions))
		statuses := make(map[string]any, len(sessions))
		for _, rawSession := range sessions {
			name := strings.TrimSpace(rawSession)
			if name == "" {
				continue
			}
			roots, agents, rootErr := s.sessionFilesPaneInputs(name)
			if rootErr != nil {
				messageKey := rootErr.MessageKey
				if rootErr.Code != "unknown_session" {
					messageKey = "diff.error.sessionUnavailable"
				}
				payloads[name] = map[string]any{
					"session": name,
					"errors":  []any{},
					"error":   rootErr.Message,
					"user_message": map[string]any{
						"key": messageKey, "params": map[string]any{"session": name}, "fallback": rootErr.Message,
					},
				}
				statuses[name] = rootErr.Status
				continue
			}
			itemRequest := request
			itemRequest.session = name
			descriptor := sessionFilesRequestDescriptor(itemRequest)
			viewID := sessionFilesCacheViewID(descriptor)
			if record, ok := s.readSessionFilesCache(itemRequest, viewID); ok {
				stale := sessionFilesCacheAge(record, time.Now()) > sessionFilesCacheTTL.Seconds()
				refreshing := false
				itemTask := func(refreshCtx context.Context) (map[string]any, *finder.Error) {
					return s.finder.SessionFilesWithAgents(refreshCtx, name, roots, agents, itemRequest.hours, itemRequest.fromRef, itemRequest.toRef, itemRequest.repoRefs)
				}
				if stale {
					refreshing = s.scheduleSessionFilesRefresh(itemRequest, viewID, itemTask)
				}
				payloads[name] = sessionFilesPayloadWithCache(record, descriptor, viewID, true, stale, refreshing, time.Now())
				statuses[name] = http.StatusOK
				continue
			}
			payload, finderErr := s.finder.SessionFilesWithAgents(ctx, name, roots, agents, itemRequest.hours, itemRequest.fromRef, itemRequest.toRef, itemRequest.repoRefs)
			if finderErr != nil {
				return nil, finderErr
			}
			s.writeSessionFilesCache(itemRequest, viewID, payload, http.StatusOK)
			if record, ok := s.readSessionFilesCache(itemRequest, viewID); ok {
				payload = sessionFilesPayloadWithCache(record, descriptor, viewID, false, false, false, time.Now())
			}
			payloads[name] = payload
			statuses[name] = http.StatusOK
		}
		return map[string]any{"sessions": payloads, "statuses": statuses, "errors": []any{}, "loaded": true}, nil
	})
}
