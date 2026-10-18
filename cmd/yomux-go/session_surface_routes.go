// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"io"
	"math"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/tailfile"
)

const (
	maxContextTranscriptBytes = maxContextTranscriptLines * 64 * 1024
	maxContextItemText        = 5000
	maxToolItemText           = 2200
	maxCompactTranscriptItems = 200
	maxProcEntries            = 4096
	maxTranscriptEntries      = 512
	maxContextTranscriptLines = 5000
)

func splitTmuxFields(line string) []string {
	// tmux commands, paths, and window names may contain '|'; tabs are the
	// established field boundary used by the inventory readers.
	return strings.Split(line, "\t")
}

type goGitSnapshot struct {
	Root       string
	Branch     string
	Head       string
	HeadSHA    string
	Upstream   string
	DirtyCount int
	Ahead      any
	Behind     any
}

type procEntry struct {
	PID        int
	PPID       int
	Command    string
	Executable string
	Identity   string
	CPUSeconds float64
	RSSBytes   uint64
}

type processEntryIndex struct {
	byPID                    map[int]procEntry
	children                 map[int][]procEntry
	agentKindByPID           map[int]string
	allowPaneCommandFallback bool
}

func indexProcessEntries(processes []procEntry) *processEntryIndex {
	index := &processEntryIndex{
		byPID:                    make(map[int]procEntry, len(processes)),
		children:                 make(map[int][]procEntry),
		allowPaneCommandFallback: true,
	}
	agentProcesses := make([]stats.AgentProcess, 0, len(processes))
	for _, process := range processes {
		index.byPID[process.PID] = process
		index.children[process.PPID] = append(index.children[process.PPID], process)
		agentProcesses = append(agentProcesses, stats.AgentProcess{
			PID: process.PID, PPID: process.PPID, Command: process.Command, Executable: process.Executable,
		})
	}
	index.agentKindByPID = stats.ClassifyAgentProcesses(agentProcesses)
	return index
}

func runGitMetadata(path string, args ...string) (string, bool) {
	if strings.TrimSpace(path) == "" {
		return "", false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 400*time.Millisecond)
	defer cancel()
	commandArgs := append([]string{"-C", path}, args...)
	command := exec.CommandContext(ctx, "git", commandArgs...)
	command.Env = append(os.Environ(), "GIT_OPTIONAL_LOCKS=0", "GIT_TERMINAL_PROMPT=0")
	output, err := command.Output()
	if err != nil {
		return "", false
	}
	return strings.TrimSpace(string(output)), true
}

func gitSnapshotForPath(path string) (goGitSnapshot, bool) {
	root, ok := runGitMetadata(path, "rev-parse", "--show-toplevel")
	if !ok || root == "" {
		return goGitSnapshot{}, false
	}
	snapshot := goGitSnapshot{Root: filepath.Clean(root), Ahead: nil, Behind: nil}
	snapshot.Branch, _ = runGitMetadata(path, "symbolic-ref", "--short", "HEAD")
	snapshot.HeadSHA, _ = runGitMetadata(path, "rev-parse", "HEAD")
	snapshot.Head, _ = runGitMetadata(path, "show", "-s", "--format=%s", "HEAD")
	snapshot.Upstream, _ = runGitMetadata(path, "rev-parse", "--abbrev-ref", "@{upstream}")
	if status, statusOK := runGitMetadata(path, "status", "--porcelain"); statusOK && status != "" {
		snapshot.DirtyCount = len(strings.Split(status, "\n"))
	}
	if snapshot.Upstream != "" {
		if counts, countsOK := runGitMetadata(path, "rev-list", "--left-right", "--count", snapshot.Upstream+"...HEAD"); countsOK {
			fields := strings.Fields(counts)
			if len(fields) == 2 {
				behind, behindErr := strconv.Atoi(fields[0])
				ahead, aheadErr := strconv.Atoi(fields[1])
				if behindErr == nil && aheadErr == nil {
					snapshot.Behind, snapshot.Ahead = behind, ahead
				}
			}
		}
	}
	return snapshot, true
}

func goGraphID(kind, value string) string {
	// Graph identifiers are only internal joins. Keep them stable within a host while avoiding
	// embedding arbitrary filesystem/session text into object keys consumed by the browser.
	var hash uint64 = 1469598103934665603
	for _, character := range kind + "\x1f" + value {
		hash ^= uint64(character)
		hash *= 1099511628211
	}
	return fmt.Sprintf("go:%s:%x", kind, hash)
}

func emptyGoWorkGraph(generation uint64) map[string]any {
	return map[string]any{
		"version": 1, "loading": false, "generation": generation,
		"tmux_sessions": map[string]any{}, "tmux_windows": map[string]any{}, "tmux_panes": map[string]any{},
		"runtime_actors": map[string]any{}, "path_observations": map[string]any{},
		"git_worktrees": map[string]any{}, "local_repositories": map[string]any{}, "hosted_repositories": map[string]any{},
		"local_branches": map[string]any{}, "pull_requests": map[string]any{}, "linear_issues": map[string]any{},
		"worktree_branch_activity": map[string]any{},
	}
}

func appendStringValue(values map[string]any, key, value string) {
	items, _ := values[key].([]string)
	if value != "" {
		for _, existing := range items {
			if existing == value {
				return
			}
		}
		values[key] = append(items, value)
	}
}

func (s *server) sessionWorkGraph(session string, info map[string]any, generation uint64) map[string]any {
	return s.sessionWorkGraphWithGitCache(session, info, generation, map[string]goGitSnapshotResult{})
}

type goGitSnapshotResult struct {
	snapshot  goGitSnapshot
	available bool
}

func (s *server) gitSnapshot(path string) (goGitSnapshot, bool) {
	if s.gitMetadataSnapshotFn != nil {
		return s.gitMetadataSnapshotFn(path)
	}
	return gitSnapshotForPath(path)
}

func gitWorktreePathHint(path string) string {
	resolved, err := filepath.Abs(path)
	if err != nil {
		return filepath.Clean(path)
	}
	if realPath, err := filepath.EvalSymlinks(resolved); err == nil {
		resolved = realPath
	}
	for candidate := filepath.Clean(resolved); ; candidate = filepath.Dir(candidate) {
		if _, err := os.Stat(filepath.Join(candidate, ".git")); err == nil {
			return candidate
		}
		parent := filepath.Dir(candidate)
		if parent == candidate {
			return filepath.Clean(resolved)
		}
	}
}

func (s *server) sessionWorkGraphWithGitCache(session string, info map[string]any, generation uint64, gitCache map[string]goGitSnapshotResult) map[string]any {
	graph := emptyGoWorkGraph(generation)
	sessionID := goGraphID("tmux-session", session)
	tmuxSessions := graph["tmux_sessions"].(map[string]any)
	tmuxWindows := graph["tmux_windows"].(map[string]any)
	tmuxPanes := graph["tmux_panes"].(map[string]any)
	runtimeActors := graph["runtime_actors"].(map[string]any)
	observations := graph["path_observations"].(map[string]any)
	worktrees := graph["git_worktrees"].(map[string]any)
	repositories := graph["local_repositories"].(map[string]any)
	branches := graph["local_branches"].(map[string]any)

	tmuxSessions[sessionID] = map[string]any{
		"id": sessionID, "name": session, "tmux_window_ids": []string{}, "tmux_pane_ids": []string{}, "runtime_actor_ids": []string{}, "path_observation_ids": []string{},
	}
	panes, _ := info["panes"].([]map[string]any)
	paneIDs := map[string]string{}
	observationIDs := map[string]string{}
	for _, pane := range panes {
		target, _ := pane["target"].(string)
		window, _ := pane["window"].(string)
		path, _ := pane["current_path"].(string)
		paneID := goGraphID("tmux-pane", session+"|"+target)
		windowID := goGraphID("tmux-window", session+"|"+window)
		paneIDs[target] = paneID
		tmuxPanes[paneID] = map[string]any{
			"id": paneID, "target": target, "pane_id": target, "index": pane["pane"], "current_path": path,
			"command": pane["command"], "pid": pane["pid"], "active": pane["active"], "window_active": pane["window_active"],
			"tmux_window_id": windowID, "runtime_actor_ids": []string{}, "path_observation_ids": []string{},
		}
		if _, exists := tmuxWindows[windowID]; !exists {
			tmuxWindows[windowID] = map[string]any{"id": windowID, "tmux_session_id": sessionID, "index": window, "name": pane["window_name"], "tmux_pane_ids": []string{}}
			appendStringValue(tmuxSessions[sessionID].(map[string]any), "tmux_window_ids", windowID)
		}
		appendStringValue(tmuxWindows[windowID].(map[string]any), "tmux_pane_ids", paneID)
		appendStringValue(tmuxSessions[sessionID].(map[string]any), "tmux_pane_ids", paneID)

		if path == "" {
			continue
		}
		observationID := goGraphID("path-observation", session+"|"+target+"|"+path)
		observationIDs[target] = observationID
		observation := map[string]any{"id": observationID, "path": path, "path_snapshot": path, "exists": true, "source": "pane-cwd", "priority": 10, "tmux_pane_id": paneID, "runtime_actor_id": nil, "git_worktree_id": nil}
		observations[observationID] = observation
		appendStringValue(tmuxPanes[paneID].(map[string]any), "path_observation_ids", observationID)
		appendStringValue(tmuxSessions[sessionID].(map[string]any), "path_observation_ids", observationID)

		cacheKey := gitWorktreePathHint(path)
		result, cached := gitCache[cacheKey]
		if !cached {
			result.snapshot, result.available = s.gitSnapshot(cacheKey)
			gitCache[cacheKey] = result
		}
		if !result.available {
			continue
		}
		snapshot := result.snapshot
		worktreeID := goGraphID("git-worktree", snapshot.Root)
		repositoryID := goGraphID("local-repository", snapshot.Root)
		branchID := goGraphID("local-branch", snapshot.Root+"|"+snapshot.Branch)
		if _, exists := repositories[repositoryID]; !exists {
			repositories[repositoryID] = map[string]any{"id": repositoryID, "common_git_dir": filepath.Join(snapshot.Root, ".git"), "git_worktree_ids": []string{}, "local_branch_ids": []string{}, "hosted_repository_id": nil}
		}
		if _, exists := worktrees[worktreeID]; !exists {
			worktrees[worktreeID] = map[string]any{
				"id": worktreeID, "root": snapshot.Root, "git_dir": filepath.Join(snapshot.Root, ".git"), "kind": "worktree",
				"local_repository_id": repositoryID, "hosted_repository_id": nil, "current_branch_id": branchID,
				"branch_activity_ids": []string{}, "path_observation_ids": []string{}, "pull_request_ids": []string{},
				"activity_priority": 10, "has_actor_cwd": false, "activity_ts": 0, "activity_source": "",
				"has_current_pull_request": false, "has_ranked_pull_request": false,
				"git": map[string]any{"root": snapshot.Root, "cwd": path, "branch": snapshot.Branch, "head": snapshot.Head, "head_sha": snapshot.HeadSHA, "upstream": snapshot.Upstream, "dirty_count": snapshot.DirtyCount, "ahead": snapshot.Ahead, "behind": snapshot.Behind},
			}
			appendStringValue(repositories[repositoryID].(map[string]any), "git_worktree_ids", worktreeID)
		}
		appendStringValue(worktrees[worktreeID].(map[string]any), "path_observation_ids", observationID)
		observation["git_worktree_id"] = worktreeID
		if _, exists := branches[branchID]; !exists {
			branches[branchID] = map[string]any{"id": branchID, "local_repository_id": repositoryID, "name": snapshot.Branch, "sha": snapshot.HeadSHA, "head_sha": snapshot.HeadSHA, "subject": snapshot.Head, "current": true, "remote": false, "checked_out_worktree_ids": []string{worktreeID}, "pull_request_ids": []string{}, "linear_issue_ids": []string{}, "updated_ts": 0}
			appendStringValue(repositories[repositoryID].(map[string]any), "local_branch_ids", branchID)
		}
	}

	agents, _ := info["agents"].([]map[string]any)
	claimed := map[string]bool{}
	for _, agent := range agents {
		target, _ := agent["pane_target"].(string)
		paneID := paneIDs[target]
		if paneID == "" {
			continue
		}
		claimed[target] = true
		actorID := goGraphID("runtime-actor", session+"|"+target+"|agent")
		runtimeActors[actorID] = map[string]any{"id": actorID, "tmux_pane_id": paneID, "kind": agent["kind"], "pid": agent["pid"], "command": agent["command"], "cwd": agent["cwd"], "status": agent["status"], "agent_session_id": agent["session_id"], "transcript": agent["transcript"], "error": agent["error"], "path_observation_ids": []string{}}
		appendStringValue(tmuxPanes[paneID].(map[string]any), "runtime_actor_ids", actorID)
		appendStringValue(tmuxSessions[sessionID].(map[string]any), "runtime_actor_ids", actorID)
		if observationID := observationIDs[target]; observationID != "" {
			runtimeActors[actorID].(map[string]any)["path_observation_ids"] = []string{observationID}
			observations[observationID].(map[string]any)["runtime_actor_id"] = actorID
			worktreeID, _ := observations[observationID].(map[string]any)["git_worktree_id"].(string)
			if worktree := worktrees[worktreeID]; worktree != nil {
				worktree.(map[string]any)["has_actor_cwd"] = true
			}
		}
	}
	for target, paneID := range paneIDs {
		if claimed[target] {
			continue
		}
		actorID := goGraphID("runtime-actor", session+"|"+target+"|shell")
		runtimeActors[actorID] = map[string]any{"id": actorID, "tmux_pane_id": paneID, "kind": "shell", "pid": tmuxPanes[paneID].(map[string]any)["pid"], "command": tmuxPanes[paneID].(map[string]any)["command"], "cwd": tmuxPanes[paneID].(map[string]any)["current_path"], "status": nil, "agent_session_id": nil, "transcript": nil, "error": nil, "path_observation_ids": []string{}}
		appendStringValue(tmuxPanes[paneID].(map[string]any), "runtime_actor_ids", actorID)
		appendStringValue(tmuxSessions[sessionID].(map[string]any), "runtime_actor_ids", actorID)
		if observationID := observationIDs[target]; observationID != "" {
			runtimeActors[actorID].(map[string]any)["path_observation_ids"] = []string{observationID}
		}
	}
	return graph
}

func agentKindFromCommand(command string) string {
	fields := strings.Fields(command)
	if len(fields) == 0 {
		return ""
	}
	first := strings.ToLower(filepath.Base(strings.Trim(fields[0], "\"'")))
	switch first {
	case "claude", "codex", "opencode":
		return first
	}
	if first != "node" && first != "nodejs" && first != "python" && first != "python3" {
		return ""
	}
	for _, raw := range fields[1:] {
		word := strings.Trim(raw, "\"'")
		if !strings.ContainsRune(word, '/') {
			continue
		}
		switch base := strings.ToLower(filepath.Base(word)); base {
		case "claude", "codex", "opencode":
			return base
		case "claude.py", "codex.py", "opencode.py":
			if strings.Contains(" "+strings.Join(fields, " ")+" ", " --mock ") {
				return strings.TrimSuffix(base, ".py")
			}
		}
	}
	return ""
}

func processCommand(pid int) string {
	if pid <= 0 {
		return ""
	}
	data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "cmdline"))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(strings.ReplaceAll(string(data), "\x00", " "))
}

func processParent(pid int) int {
	data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "status"))
	if err != nil {
		return 0
	}
	for _, line := range strings.Split(string(data), "\n") {
		if !strings.HasPrefix(line, "PPid:") {
			continue
		}
		parent, _ := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(line, "PPid:")))
		return parent
	}
	return 0
}

func processEntries() ([]procEntry, error) {
	if runtime.GOOS == "darwin" {
		return processEntriesFromDarwinInventory(stats.DarwinProcessInventory)
	}
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return nil, err
	}
	processes := make([]procEntry, 0)
	for _, entry := range entries {
		if len(processes) >= maxProcEntries {
			break
		}
		pid, parseErr := strconv.Atoi(entry.Name())
		if parseErr != nil || pid <= 0 {
			continue
		}
		command := processCommand(pid)
		if command == "" {
			continue
		}
		executable, _ := os.Readlink(filepath.Join("/proc", entry.Name(), "exe"))
		processes = append(processes, procEntry{PID: pid, PPID: processParent(pid), Command: command, Executable: executable})
	}
	return processes, nil
}

func processEntriesFromDarwinInventory(inventory func() ([]stats.DarwinProcess, error)) ([]procEntry, error) {
	rows, err := inventory()
	if err != nil {
		return nil, err
	}
	result := make([]procEntry, 0, len(rows))
	for _, row := range rows {
		result = append(result, procEntry{PID: row.PID, PPID: row.PPID, Command: row.Command, Executable: row.Executable, Identity: row.Identity, CPUSeconds: row.CPUSeconds, RSSBytes: row.RSSBytes})
	}
	return result, nil
}

// descendantProcessEntries walks Linux's per-process children files instead
// of scanning every process on the machine. The Go Daemons panel only needs
// descendants of this server; a machine-wide roster here duplicated the much
// more expensive agent inventory scan and was a major avoidable contributor to
// high CPU while /api/system-status was polled.
func descendantProcessEntries(rootPID int) ([]procEntry, error) {
	if runtime.GOOS == "darwin" {
		return darwinDescendantProcessEntries(rootPID)
	}
	if rootPID <= 0 || runtime.GOOS != "linux" {
		return nil, errors.New("descendant process walk unavailable")
	}
	queue := []int{rootPID}
	seen := map[int]bool{rootPID: true}
	result := make([]procEntry, 0)
	for len(queue) > 0 && len(result) < maxProcEntries {
		parent := queue[0]
		queue = queue[1:]
		children, err := processChildren(parent)
		if err != nil {
			if parent == rootPID {
				return nil, err
			}
			continue
		}
		for _, pid := range children {
			if pid <= 0 || seen[pid] {
				continue
			}
			seen[pid] = true
			command := processCommand(pid)
			executable, _ := os.Readlink(filepath.Join("/proc", strconv.Itoa(pid), "exe"))
			if command != "" {
				result = append(result, procEntry{PID: pid, PPID: parent, Command: command, Executable: executable})
			}
			queue = append(queue, pid)
			if len(result) >= maxProcEntries {
				break
			}
		}
	}
	return result, nil
}

func processChildren(pid int) ([]int, error) {
	data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "task", strconv.Itoa(pid), "children"))
	if err != nil {
		return nil, err
	}
	return parseProcessChildren(string(data)), nil
}

func parseProcessChildren(text string) []int {
	fields := strings.Fields(text)
	children := make([]int, 0, len(fields))
	for _, field := range fields {
		child, parseErr := strconv.Atoi(field)
		if parseErr == nil && child > 0 {
			children = append(children, child)
		}
	}
	return children
}

func (s *server) paneAgentProcess(panePID int, paneCommand string) (string, int, string, string) {
	if s.processEntriesFn == nil {
		if kind := agentKindFromCommand(paneCommand); kind != "" {
			return kind, panePID, paneCommand, ""
		}
		return "", 0, "", ""
	}
	processes, err := s.processEntriesFn()
	if err != nil {
		return "", 0, "", ""
	}
	return paneAgentProcessFromIndex(panePID, paneCommand, indexProcessEntries(processes))
}

func paneAgentProcessFromIndex(panePID int, paneCommand string, processIndex *processEntryIndex) (string, int, string, string) {
	if processIndex == nil {
		if kind := agentKindFromCommand(paneCommand); kind != "" {
			return kind, panePID, paneCommand, ""
		}
		return "", 0, "", ""
	}
	if process, ok := processIndex.byPID[panePID]; ok {
		if kind := processIndex.agentKindByPID[process.PID]; kind != "" {
			return kind, process.PID, process.Command, process.Executable
		}
	} else if processIndex.allowPaneCommandFallback {
		if kind := agentKindFromCommand(paneCommand); kind != "" {
			return kind, panePID, paneCommand, ""
		}
	}
	queue := append([]procEntry(nil), processIndex.children[panePID]...)
	seen := make(map[int]struct{}, len(processIndex.byPID))
	seen[panePID] = struct{}{}
	for len(queue) > 0 {
		process := queue[0]
		queue = queue[1:]
		if _, visited := seen[process.PID]; visited {
			continue
		}
		seen[process.PID] = struct{}{}
		if kind := processIndex.agentKindByPID[process.PID]; kind != "" {
			return kind, process.PID, process.Command, process.Executable
		}
		queue = append(queue, processIndex.children[process.PID]...)
	}
	return "", 0, "", ""
}

// tmux signal rows expose pane_current_command, which is only the shell's
// short label.  Codex's launcher is commonly reported as "node" even though
// its child command is the real Codex entry point.  Reuse the same bounded
// process-tree identity check as session metadata so the signal state can
// drive the green working marker for wrapped Codex panes too.
func (s *server) tmuxSignalPaneAgent(panePID int, paneCommand string) (string, int, string) {
	kind, pid, command, _ := s.paneAgentProcess(panePID, paneCommand)
	if kind != "claude" && kind != "codex" {
		return "", 0, ""
	}
	return kind, pid, command
}

func transcriptRoot(kind string) string {
	if kind == "claude" {
		if root := strings.TrimSpace(productenv.Value("CLAUDE_TRANSCRIPT_ROOT")); root != "" {
			return root
		}
		home, _ := os.UserHomeDir()
		return filepath.Join(home, ".claude", "projects")
	}
	if kind == "opencode" {
		return ""
	}
	if root := strings.TrimSpace(productenv.Value("CODEX_TRANSCRIPT_ROOT")); root != "" {
		return root
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".codex", "sessions")
}

func transcriptCWD(path string) string {
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()
	decoder := json.NewDecoder(file)
	var record map[string]any
	if decoder.Decode(&record) != nil {
		return ""
	}
	if cwd, _ := record["cwd"].(string); cwd != "" {
		return cwd
	}
	payload, _ := record["payload"].(map[string]any)
	cwd, _ := payload["cwd"].(string)
	return cwd
}

func transcriptSessionID(path string) string {
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()
	decoder := json.NewDecoder(file)
	for index := 0; index < 20; index++ {
		var record map[string]any
		if decoder.Decode(&record) != nil {
			break
		}
		if record["type"] != "session_meta" {
			continue
		}
		payload, _ := record["payload"].(map[string]any)
		id, _ := payload["id"].(string)
		if id != "" {
			return id
		}
	}
	return ""
}

func newestTranscriptForCWD(root, cwd string) string {
	if root == "" {
		return ""
	}
	var newestPath string
	var newestTime time.Time
	entries := 0
	_ = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil || entry == nil || entry.IsDir() {
			return nil
		}
		if !strings.HasSuffix(entry.Name(), ".jsonl") {
			return nil
		}
		entries++
		if entries > maxTranscriptEntries {
			return filepath.SkipAll
		}
		if cwd != "" && transcriptCWD(path) != cwd {
			return nil
		}
		info, statErr := entry.Info()
		if statErr == nil && (newestPath == "" || info.ModTime().After(newestTime)) {
			newestPath, newestTime = filepath.Clean(path), info.ModTime()
		}
		return nil
	})
	return newestPath
}

func openTranscriptForProcess(pid int, kind string) string {
	if pid <= 0 {
		return ""
	}
	if kind == "codex" {
		return stats.CodexTranscriptFromProcess(pid, transcriptRoot(kind))
	}
	root := filepath.Clean(transcriptRoot(kind))
	for _, target := range stats.ProcessOpenFiles(pid) {
		if !strings.HasSuffix(target, ".jsonl") || !strings.HasPrefix(filepath.Clean(target), root+string(os.PathSeparator)) {
			continue
		}
		return filepath.Clean(target)
	}
	return ""
}

func agentTranscript(kind string, pid int, cwd string) (string, string, string) {
	return agentTranscriptWithTitle(kind, pid, cwd, "")
}

func agentTranscriptWithTitle(kind string, pid int, cwd, title string) (string, string, string) {
	return agentTranscriptWithCommand(kind, pid, cwd, title, "")
}

func agentTranscriptWithCommand(kind string, pid int, cwd, title, command string) (string, string, string) {
	if kind == "opencode" {
		databasePath := strings.TrimSpace(productenv.Value("OPENCODE_DATABASE"))
		if databasePath == "" {
			return "", "", cwd
		}
		database, err := stats.OpenOpenCodeReadOnly(context.Background(), databasePath)
		if err != nil {
			return "", databasePath, cwd
		}
		defer database.Close()
		sessionID := explicitOpenCodeSessionID(command)
		if sessionID == "" {
			sessionID, _ = database.ResolveSessionID(context.Background(), cwd, title, 0, float64(time.Now().UnixNano())/1e9)
		}
		return sessionID, databasePath, cwd
	}
	if kind == "claude" {
		sessionID, transcript, resolvedCWD := resolveClaudePaneIdentity(strconv.Itoa(pid), cwd)
		if sessionID != "" || transcript != "" {
			return sessionID, transcript, resolvedCWD
		}
	}
	transcript := openTranscriptForProcess(pid, kind)
	if transcript == "" && kind != "codex" {
		transcript = newestTranscriptForCWD(transcriptRoot(kind), cwd)
	}
	if transcript == "" {
		return "", "", cwd
	}
	return transcriptSessionID(transcript), transcript, transcriptCWD(transcript)
}

func explicitOpenCodeSessionID(command string) string {
	fields := strings.Fields(command)
	for index, field := range fields {
		field = strings.Trim(field, "\"'")
		if strings.HasPrefix(field, "--session=") {
			return strings.TrimPrefix(field, "--session=")
		}
		if (field == "--session" || field == "-s") && index+1 < len(fields) {
			return strings.Trim(fields[index+1], "\"'")
		}
	}
	return ""
}

func (s *server) tmuxStatus(w http.ResponseWriter, r *http.Request) {
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "session is required"})
		return
	}
	if s.tmuxCommand("has-session", "-t", session).Run() != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "unknown session", "session": session})
		return
	}
	if r.Method == http.MethodPost {
		user, status := s.identityStatus(r)
		if status != 0 {
			if !rejectCredentialAdmission(w, status) {
				s.rejectUnauthorized(w, r)
			}
			return
		}
		if user.role != "admin" {
			s.rejectForbidden(w, user, "admin")
			return
		}
		mode, err := s.readTmuxStatusMode(session)
		if err != nil {
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": err.Error(), "session": session})
			return
		}
		next := map[string]string{"top": "bottom", "bottom": "none", "none": "top"}[mode]
		if next == "none" {
			err = s.tmuxCommand("set-option", "-t", s.exactTarget(session), "status", "off").Run()
		} else {
			err = s.tmuxCommand("set-option", "-t", s.exactTarget(session), "status", "on").Run()
			if err == nil {
				err = s.tmuxCommand("set-option", "-t", s.exactTarget(session), "status-position", next).Run()
			}
		}
		if err != nil {
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "tmux status update failed", "session": session})
			return
		}
		jsonResponse(w, http.StatusOK, map[string]any{"session": session, "status": next})
		return
	}
	if r.Method != http.MethodGet {
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	mode, err := s.readTmuxStatusMode(session)
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": err.Error(), "session": session})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"session": session, "status": mode})
}

func (s *server) readTmuxStatusMode(session string) (string, error) {
	target := s.exactTarget(session)
	status, err := s.tmuxCommand("show-options", "-A", "-t", target, "-v", "status").Output()
	if err != nil {
		return "", fmt.Errorf("tmux status read failed")
	}
	if strings.TrimSpace(string(status)) != "on" {
		return "none", nil
	}
	position, err := s.tmuxCommand("show-options", "-A", "-t", target, "-v", "status-position").Output()
	if err != nil {
		return "", fmt.Errorf("tmux status position read failed")
	}
	mode := strings.ToLower(strings.TrimSpace(string(position)))
	if mode != "top" && mode != "bottom" {
		mode = "bottom"
	}
	return mode, nil
}

func (s *server) eventLogTail(session string, limit int) []map[string]any {
	if limit < 1 {
		limit = 1
	}
	if limit > 1000 {
		limit = 1000
	}
	path := s.eventLogPath
	if path == "" && s.config != "" {
		path = filepath.Join(s.config, "event-log.jsonl")
	}
	file, err := os.Open(path)
	if err != nil {
		return []map[string]any{}
	}
	defer file.Close()
	values := make([]map[string]any, 0, limit)
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		var event map[string]any
		if json.Unmarshal(scanner.Bytes(), &event) != nil || event == nil {
			continue
		}
		eventSession, _ := event["session"].(string)
		if session != "" && eventSession != "" && eventSession != session {
			continue
		}
		values = append(values, event)
		if len(values) > limit {
			copy(values, values[len(values)-limit:])
			values = values[:limit]
		}
	}
	return values
}

func (s *server) contextStream(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session == "" || s.tmuxCommand("has-session", "-t", session).Run() != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "error": "unknown session"})
		return
	}
	limit, ok := transcriptQueryLimit(w, r, "messages", 40, maxCompactTranscriptItems)
	if !ok {
		return
	}
	path, agent, selectionError := s.sessionTranscriptForRequest(session, r.URL.Query().Get("pane_target"), r.URL.Query().Get("window"))
	if path == "" {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "error": selectionError})
		return
	}
	items, err := readContextItemsForAgent(path, agent, limit)
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"session": session, "error": "transcript read failed"})
		return
	}
	stream, ok := newJSONSSEWriter(w, r.Context())
	if !ok {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "streaming unavailable"})
		return
	}
	defer stream.close()
	writeSSE := func(event string, value any) bool {
		return stream.write(event, value) == nil
	}
	if !writeSSE("reset", map[string]any{"session": session, "path": path, "items": items, "pending": false, "stale": false, "agent": agent, "errors": []any{}}) {
		return
	}
	lastSize, lastMod := transcriptSignature(path)
	isOpenCode := false
	if kind, _ := agent["kind"].(string); kind == "opencode" {
		isOpenCode = true
	}
	var offset int64
	var pending []byte
	var lastFileID string
	if !isOpenCode {
		offset, _, lastFileID = transcriptPathIdentity(path)
	}
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	ping := time.NewTicker(15 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			size, mod := transcriptSignature(path)
			if size == lastSize && mod == lastMod {
				continue
			}
			_, _, fileID := transcriptPathIdentity(path)
			if !isOpenCode && fileID == lastFileID && size > offset {
				updated, nextOffset, nextPending, readErr := readTranscriptItemsSinceWithPending(path, offset, pending)
				if readErr != nil {
					return
				}
				offset, pending = nextOffset, nextPending
				lastFileID = fileID
				lastSize, lastMod = size, mod
				if len(updated) > 0 && !writeSSE("items", map[string]any{"items": updated}) {
					return
				}
				continue
			}
			updated, readErr := readContextItemsForAgent(path, agent, limit)
			if readErr != nil {
				return
			}
			lastSize, lastMod = size, mod
			if !isOpenCode {
				offset, pending, lastFileID = size, nil, fileID
			}
			if !writeSSE("reset", map[string]any{"session": session, "path": path, "items": updated, "pending": false, "stale": false, "agent": agent, "errors": []any{}}) {
				return
			}
		case <-ping.C:
			if !writeSSE("ping", map[string]any{"time": time.Now().UTC().Format(time.RFC3339)}) {
				return
			}
		}
	}
}

func (s *server) contextItems(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session == "" || s.tmuxCommand("has-session", "-t", session).Run() != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "error": "unknown session"})
		return
	}
	limit, ok := transcriptQueryLimit(w, r, "messages", 40, maxCompactTranscriptItems)
	if !ok {
		return
	}
	path, agent, selectionError := s.sessionTranscriptForRequest(session, r.URL.Query().Get("pane_target"), r.URL.Query().Get("window"))
	if path == "" {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "error": selectionError})
		return
	}
	items, err := readContextItemsForAgent(path, agent, limit)
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"session": session, "error": "transcript read failed"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"session": session, "path": path, "messages": limit, "items": items, "pending": false, "stale": false, "agent": agent, "errors": []any{}})
}

func (s *server) transcript(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session == "" || s.tmuxCommand("has-session", "-t", session).Run() != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "error": "unknown session"})
		return
	}
	lines, ok := transcriptQueryLimit(w, r, "lines", 120, 5000)
	if !ok {
		return
	}
	path, agent, selectionError := s.sessionTranscriptForRequest(session, r.URL.Query().Get("pane_target"), r.URL.Query().Get("window"))
	if path == "" {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "error": selectionError, "agent": agent, "errors": []any{}})
		return
	}
	text, contextItems, err := readTranscriptTextForAgent(path, agent, lines)
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"session": session, "agent": agent, "error": "transcript read failed", "errors": []any{}})
		return
	}
	response := map[string]any{"session": session, "agent": agent, "path": path, "lines": lines, "text": text, "errors": []any{}}
	if contextItems != nil {
		response["items"] = contextItems
		response["source"] = "opencode"
	}
	jsonResponse(w, http.StatusOK, response)
}

func (s *server) context(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session == "" || s.tmuxCommand("has-session", "-t", session).Run() != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "error": "unknown session"})
		return
	}
	messages, ok := transcriptQueryLimit(w, r, "messages", 40, maxCompactTranscriptItems)
	if !ok {
		return
	}
	path, agent, selectionError := s.sessionTranscriptForRequest(session, r.URL.Query().Get("pane_target"), r.URL.Query().Get("window"))
	if path == "" {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "error": selectionError, "agent": agent, "errors": []any{}})
		return
	}
	items, err := readContextItemsForAgent(path, agent, messages)
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"session": session, "agent": agent, "error": "transcript read failed", "errors": []any{}})
		return
	}
	lines := make([]string, 0, len(items))
	for _, item := range items {
		lines = append(lines, formatTranscriptItem(item))
	}
	jsonResponse(w, http.StatusOK, map[string]any{"session": session, "path": path, "messages": messages, "text": strings.Join(lines, "\n\n"), "pending": false, "stale": false, "agent": agent, "errors": []any{}})
}

func transcriptQueryLimit(w http.ResponseWriter, r *http.Request, name string, defaultValue, maximum int) (int, bool) {
	raw, present := r.URL.Query()[name]
	if !present || len(raw) == 0 || raw[0] == "" {
		return defaultValue, true
	}
	value, err := strconv.Atoi(raw[0])
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{
			"error": "" + name + " must be an integer", "field": name,
		})
		return 0, false
	}
	if value < 1 {
		jsonResponse(w, http.StatusBadRequest, map[string]any{
			"error": "" + name + " must be at least 1", "field": name, "min": 1,
		})
		return 0, false
	}
	if value > maximum {
		value = maximum
	}
	return value, true
}

func (s *server) sessionTranscriptForRequest(session, requestedPane, requestedWindow string) (string, map[string]any, string) {
	_, agents, finderErr := s.sessionFilesPaneInputs(session)
	if finderErr != nil {
		return "", nil, finderErr.Message
	}
	activeTarget := s.activeTmuxPaneTarget(session)
	candidate, reason := chooseSessionTranscript(agents, requestedPane, requestedWindow, activeTarget)
	if candidate == nil {
		return "", nil, reason
	}
	if candidate.Transcript == "" {
		return "", transcriptAgentPayload(session, *candidate), reason
	}
	return candidate.Transcript, transcriptAgentPayload(session, *candidate), ""
}

func chooseSessionTranscript(agents []finder.SessionFileAgent, requestedPane, requestedWindow, activeTarget string) (*finder.SessionFileAgent, string) {
	requestedPane = strings.TrimSpace(requestedPane)
	requestedWindow = strings.TrimSpace(requestedWindow)
	if requestedPane != "" {
		for index := range agents {
			if agents[index].PaneTarget == requestedPane {
				if agents[index].Transcript == "" {
					return &agents[index], "selected pane has no agent transcript"
				}
				return &agents[index], ""
			}
		}
		return nil, "selected pane has no agent transcript"
	}
	if requestedWindow != "" {
		var windowAgents []*finder.SessionFileAgent
		for index := range agents {
			if agents[index].Window == requestedWindow {
				windowAgents = append(windowAgents, &agents[index])
			}
		}
		if len(windowAgents) == 1 {
			if windowAgents[0].Transcript == "" {
				return windowAgents[0], "selected window has no agent transcript"
			}
			return windowAgents[0], ""
		}
		for _, candidate := range windowAgents {
			if !candidate.Active {
				continue
			}
			if candidate.Transcript == "" {
				return candidate, "selected window has no agent transcript"
			}
			return candidate, ""
		}
		if len(windowAgents) > 1 {
			return nil, "selected window has ambiguous agent transcripts"
		}
		return nil, "selected window has no agent transcript"
	}
	activeTarget = strings.TrimSpace(activeTarget)
	for index := range agents {
		if activeTarget != "" && agents[index].PaneTarget == activeTarget {
			if agents[index].Transcript == "" {
				return &agents[index], "active pane has no agent transcript"
			}
			return &agents[index], ""
		}
	}
	return nil, "active pane has no agent transcript"
}

func (s *server) activeTmuxPaneTarget(session string) string {
	output, err := s.tmuxCommand("display-message", "-p", "-t", s.exactTarget(session), "#{pane_id}").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(output))
}

func transcriptAgentPayload(session string, agent finder.SessionFileAgent) map[string]any {
	return map[string]any{
		"session": session, "kind": agent.Kind, "pid": agent.PID, "pane_target": agent.PaneTarget,
		"command": agent.Command, "cwd": agent.CWD, "status": "idle", "session_id": agent.SessionID,
		"transcript": agent.Transcript, "error": "", "window": agent.Window, "window_index": agent.WindowIndex,
		"pane": agent.Pane, "window_active": agent.WindowActive, "current": agent.Active && agent.WindowActive,
	}
}

func readTranscriptTail(path string, lines int) (string, error) {
	return readTranscriptTailWithByteLimit(path, lines, 0)
}

func readTranscriptTailWithByteLimit(path string, lines int, byteLimit int64) (string, error) {
	return tailfile.Read(path, lines, byteLimit)
}

func formatTranscriptItem(item map[string]any) string {
	role, _ := item["role"].(string)
	if role == "" {
		role = "message"
	}
	metadata := make([]string, 0, 2)
	for _, key := range []string{"timestamp", "cwd"} {
		if value, _ := item[key].(string); value != "" {
			metadata = append(metadata, value)
		}
	}
	header := role
	if len(metadata) > 0 {
		header += " (" + strings.Join(metadata, ", ") + ")"
	}
	text, _ := item["text"].(string)
	return header + "\n" + text
}

func readTranscriptTextForAgent(path string, agent map[string]any, lines int) (string, []map[string]any, error) {
	if kind, _ := agent["kind"].(string); kind == "opencode" {
		itemLimit := lines
		if itemLimit > maxCompactTranscriptItems {
			itemLimit = maxCompactTranscriptItems
		}
		items, err := readContextItemsForAgent(path, agent, itemLimit)
		if err != nil {
			return "", nil, err
		}
		formatted := make([]string, 0, len(items))
		for _, item := range items {
			formatted = append(formatted, formatTranscriptItem(item))
		}
		return strings.Join(formatted, "\n\n"), items, nil
	}
	text, err := readTranscriptTail(path, lines)
	return text, nil, err
}

const liveSessionMetadataTmuxFormat = "#{session_name}\t#{window_index}\t#{window_name}\t#{pane_index}\t#{pane_id}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_title}\t#{pane_pid}\t#{pane_active}\t#{window_active}\t#{window_id}\t#{@yomux_description}"

const (
	goTmuxSignalWindowFormat = "#{session_name}\t#{session_id}\t#{session_activity}\t#{session_last_attached}\t#{session_attached}\t#{session_attached_list}\t#{window_index}\t#{window_id}\t#{window_name}\t#{window_active}\t#{window_activity}\t#{window_activity_flag}\t#{window_bell_flag}\t#{window_silence_flag}\t#{window_active_clients}\t#{window_active_clients_list}\t#{window_panes}\t#{window_width}\t#{window_height}\t#{window_zoomed_flag}\t#{window_layout}\t#{window_visible_layout}"
	goTmuxSignalPaneFormat   = "#{session_name}\t#{window_index}\t#{window_id}\t#{pane_index}\t#{pane_id}\t#{pane_active}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_title}\t#{pane_dead}\t#{pane_dead_status}\t#{pane_dead_signal}\t#{pane_dead_time}\t#{alternate_on}\t#{pane_in_mode}\t#{pane_mode}\t#{pane_input_off}\t#{pane_synchronized}\t#{pane_pid}\t#{pane_width}\t#{pane_height}\t#{history_size}\t#{history_bytes}"
	goTmuxSignalClientFormat = "#{client_name}\t#{client_session}\t#{client_activity}\t#{client_width}\t#{client_height}\t#{client_flags}\t#{client_control_mode}\t#{client_readonly}\t#{client_user}"
)

func (s *server) liveSessionMetadata(session string) map[string]any {
	return s.liveSessionMetadataForSessions([]string{session})[session]
}

func (s *server) liveSessionMetadataForSessions(sessions []string) map[string]map[string]any {
	result, _ := s.liveSessionMetadataForSessionsWithError(sessions)
	return result
}

// liveSessionMetadataForSessionsWithError keeps the roster and pane inventory as
// separate observations.  A transient list-panes failure must not turn a known
// global session roster into a healthy-looking empty topology.
func (s *server) liveSessionMetadataForSessionsWithError(sessions []string) (map[string]map[string]any, error) {
	// Use one machine-wide inventory, matching Python's discover_sessions() path.
	// Operator launches must see every session on the global tmux server.
	// Keep the global pane scan bounded just like the session roster scan. A wedged
	// tmux server must degrade metadata to empty panes, not hold Tabber's request
	// open indefinitely.
	result := make(map[string]map[string]any, len(sessions))
	if len(sessions) == 0 {
		return result, nil
	}
	commandContext, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	output, err := s.tmuxCommandContext(commandContext, "list-panes", "-a", "-F", liveSessionMetadataTmuxFormat).Output()
	return s.liveSessionMetadataForSessionsFromOutput(sessions, output, err), err
}

func (s *server) liveSessionMetadataForSessionsFromOutput(sessions []string, output []byte, err error) map[string]map[string]any {
	result := make(map[string]map[string]any, len(sessions))
	var processIndex *processEntryIndex
	if err == nil && s.processEntriesFn != nil {
		if processes, processErr := s.processEntriesFn(); processErr == nil {
			processIndex = indexProcessEntries(processes)
		} else {
			processIndex = &processEntryIndex{}
		}
	}
	for _, session := range sessions {
		result[session] = s.liveSessionMetadataFromOutputWithIndex(session, output, err, processIndex)
	}
	return result
}

func (s *server) liveSessionMetadataFromOutput(session string, output []byte, err error) map[string]any {
	var processIndex *processEntryIndex
	if err == nil && s.processEntriesFn != nil {
		if processes, processErr := s.processEntriesFn(); processErr == nil {
			processIndex = indexProcessEntries(processes)
		} else {
			processIndex = &processEntryIndex{}
		}
	}
	return s.liveSessionMetadataFromOutputWithIndex(session, output, err, processIndex)
}

func (s *server) liveSessionMetadataFromOutputWithIndex(session string, output []byte, err error, processIndex *processEntryIndex) map[string]any {
	if err != nil {
		return map[string]any{
			"panes":           []map[string]any{},
			"selected_pane":   nil,
			"agents":          []map[string]any{},
			"window_metadata": []map[string]any{},
		}
	}
	panes := make([]map[string]any, 0)
	agents := make([]map[string]any, 0)
	windows := make(map[string]map[string]any)
	var selected map[string]any
	for _, line := range strings.Split(strings.Trim(string(output), "\r\n"), "\n") {
		parts := splitTmuxFields(line)
		if len(parts) < 11 || parts[0] != session {
			continue
		}
		pid, _ := strconv.Atoi(parts[8])
		paneIndex, _ := strconv.Atoi(parts[3])
		windowIndex, _ := strconv.Atoi(parts[1])
		active := parts[9] == "1"
		windowActive := parts[10] == "1"
		pane := map[string]any{
			"session": session, "window": parts[1], "window_index": windowIndex, "pane": parts[3],
			"pane_index": paneIndex, "pane_id": parts[4], "target": parts[4], "current_path": parts[5],
			"command": parts[6], "active": active, "window_active": windowActive, "title": parts[7], "pid": pid,
			"window_name": parts[2],
		}
		if len(parts) >= 12 {
			pane["window_id"] = parts[11]
		}
		panes = append(panes, pane)
		if windowActive && active {
			selected = pane
		}
		if selected == nil && active {
			selected = pane
		}
		if _, ok := windows[parts[1]]; !ok {
			windows[parts[1]] = map[string]any{
				"window": parts[1], "window_index": windowIndex, "window_name": parts[2],
				"path": parts[5], "pane": parts[3], "pane_target": parts[4],
			}
			if len(parts) >= 13 {
				windows[parts[1]]["window_id"] = parts[11]
				tabberDescriptionFields(windows[parts[1]], parts[12])
			}
		}
		kind, agentPID, agentCommand, agentExecutable := paneAgentProcessFromIndex(pid, parts[6], processIndex)
		if kind == "" {
			continue
		}
		cwd := parts[5]
		sessionID, transcript, resolvedCWD := agentTranscriptWithCommand(kind, agentPID, cwd, parts[7], agentCommand)
		if resolvedCWD != "" {
			cwd = resolvedCWD
		}
		if len(parts) >= 13 && parts[12] == "" && transcript != "" {
			if description := tabberConversationDescription(kind, transcript, sessionID); description != "" {
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				raw, saveErr := s.saveTabberDescription(ctx, parts[11], description, false)
				cancel()
				if saveErr == nil {
					tabberDescriptionFields(windows[parts[1]], raw)
				}
			}
		}
		status := "idle"
		queuedFollowupQuestion := false
		attentionSignature := ""
		statusObservedAt := time.Now()
		if s.agentStatusClassifier != nil {
			classified := s.agentStatusClassifier(context.Background(), stats.AgentStatusRequest{
				Kind: kind, PID: agentPID, Command: agentCommand, Executable: agentExecutable, PaneTarget: parts[4], Directory: cwd,
			})
			status = liveAgentWindowState(classified.Status)
			queuedFollowupQuestion = classified.QueuedFollowupQuestion
			attentionSignature = classified.AttentionSignature
			if !classified.ObservedAt.IsZero() {
				statusObservedAt = classified.ObservedAt
			}
		}
		processInstanceID := ""
		if s.agentStatusCollector != nil {
			sources := stats.CurrentPlatformAgentInventorySources()
			if sources.Started != nil {
				if started, err := sources.Started(agentPID); err == nil {
					processInstanceID = stats.AgentStatusProcessInstanceID(stats.AgentRecord{PID: agentPID, StartedIdentity: started})
				}
			}
		}
		agents = append(agents, map[string]any{
			"session": session, "kind": kind, "pid": agentPID, "pane_target": parts[4], "command": agentCommand, "executable": agentExecutable,
			"cwd": cwd, "status": status, "queued_followup_question": queuedFollowupQuestion, "attention_signature": attentionSignature,
			"status_observed_at": float64(statusObservedAt.UnixNano()) / 1e9, "session_id": sessionID, "transcript": transcript,
			"process_instance_id": processInstanceID, "attention_pending": s.agentStatusCollector != nil && attentionSignature != "",
			"error": "", "window": parts[1], "window_index": windowIndex, "pane": parts[3],
			"window_active": windowActive, "current": active && windowActive,
		})
	}
	windowMetadata := make([]map[string]any, 0, len(windows))
	for _, item := range windows {
		windowMetadata = append(windowMetadata, item)
	}
	sort.SliceStable(windowMetadata, func(i, j int) bool {
		left, _ := windowMetadata[i]["window_index"].(int)
		right, _ := windowMetadata[j]["window_index"].(int)
		return left < right
	})
	return map[string]any{"panes": panes, "selected_pane": selected, "agents": agents, "window_metadata": windowMetadata}
}

func liveAgentWindowState(status stats.AgentStatus) string {
	switch status {
	case stats.AgentStatusRun:
		return "working"
	case stats.AgentStatusAsk:
		return "needs-input"
	case stats.AgentStatusTransition:
		return "cooldown"
	case stats.AgentStatusError:
		return "blocked"
	case stats.AgentStatusUnavailable:
		return "unavailable"
	default:
		return "idle"
	}
}

func activityAgentStatusKey(session, pane, kind string) string {
	return strings.Join([]string{session, pane, strings.ToLower(strings.TrimSpace(kind))}, "\x00")
}

func activityStatusObservedAt(seconds float64) time.Time {
	wholeSeconds := int64(seconds)
	nanoseconds := int64((seconds - float64(wholeSeconds)) * 1e9)
	return time.Unix(wholeSeconds, nanoseconds)
}

type goAgentStatusOverride struct {
	processInstanceID      string
	state                  string
	attentionKind          string
	observedAt             time.Time
	workingStoppedTS       float64
	transitionID           string
	cooldownAttentionKey   string
	attentionKey           string
	attentionSignature     string
	attentionAcknowledged  bool
	queuedFollowupQuestion bool
}

func activityAgentStatusOverrides(snapshot stats.AgentStatusSnapshot) map[string]goAgentStatusOverride {
	if !snapshot.Available {
		return nil
	}
	result := make(map[string]goAgentStatusOverride, len(snapshot.Agents))
	cycleObservedAt := time.Time{}
	if snapshot.Freshness != nil {
		cycleObservedAt = activityStatusObservedAt(snapshot.Freshness.ObservedAt)
	}
	for _, raw := range snapshot.Agents {
		row, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		session := stringValue(row["session"])
		pane := stringValue(row["pane"])
		kind := stringValue(row["kind"])
		state := stats.AgentStatus(stringValue(row["state"]))
		if session == "" || pane == "" || kind == "" || state == "" {
			continue
		}
		key := activityAgentStatusKey(session, pane, kind)
		observedAt := cycleObservedAt
		if value, ok := row["observed_at"].(float64); ok && value > 0 {
			observedAt = activityStatusObservedAt(value)
		}
		workingStoppedTS, _ := row["working_stopped_ts"].(float64)
		result[key] = goAgentStatusOverride{
			state: liveAgentWindowState(state), attentionKind: stringValue(row["attention_kind"]), observedAt: observedAt, processInstanceID: stringValue(row["process_instance_id"]),
			workingStoppedTS:     workingStoppedTS,
			transitionID:         stringValue(row["transition_id"]),
			cooldownAttentionKey: stringValue(row["cooldown_attention_key"]),
			attentionKey:         stringValue(row["attention_key"]), attentionSignature: stringValue(row["attention_signature"]),
			attentionAcknowledged: row["attention_acknowledged"] == true, queuedFollowupQuestion: row["queued_followup_question"] == true,
		}
	}
	return result
}

func (s *server) currentAgentStatusOverrides(ctx context.Context) (map[string]goAgentStatusOverride, error) {
	snapshot, err := s.currentAgentStatusSnapshot(ctx)
	if err != nil {
		return nil, err
	}
	return activityAgentStatusOverrides(snapshot), nil
}

func (s *server) currentAgentStatusSnapshot(ctx context.Context) (stats.AgentStatusSnapshot, error) {
	if s.statsService == nil {
		return stats.AgentStatusSnapshot{}, nil
	}
	return s.statsService.AgentStatus(ctx)
}

// A newer metadata capture can confirm the same state, or the idle side of a
// retained completion, without invalidating the collector lifecycle identity.
func agentStatusOverrideObservationMatches(agent map[string]any, override goAgentStatusOverride, now time.Time) bool {
	observed, _ := agent["status_observed_at"].(float64)
	if observed <= 0 || !override.observedAt.Before(activityStatusObservedAt(observed)) {
		return true
	}
	currentState := stringValue(agent["status"])
	stoppedAt := time.Unix(0, int64(override.workingStoppedTS*1e9))
	completionIsCurrent := (override.state == "cooldown" || override.state == "idle") && currentState == "idle" && override.workingStoppedTS > 0 && !now.Before(stoppedAt)
	return completionIsCurrent || (currentState == override.state && stringValue(agent["attention_signature"]) == override.attentionSignature)
}

func (s *server) applyAgentStatusLifecycle(sessions map[string]any, overrides map[string]goAgentStatusOverride) error {
	for session, raw := range sessions {
		info, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		agents, _ := info["agents"].([]map[string]any)
		for _, agent := range agents {
			key := activityAgentStatusKey(session, stringValue(agent["pane_target"]), stringValue(agent["kind"]))
			override, exists := overrides[key]
			if exists && stringValue(agent["process_instance_id"]) != "" && override.processInstanceID != "" && override.processInstanceID != stringValue(agent["process_instance_id"]) {
				exists = false
			}
			if agent["attention_pending"] == true {
				observed, _ := agent["status_observed_at"].(float64)
				newerSameProcess := exists && override.processInstanceID != "" && override.processInstanceID == stringValue(agent["process_instance_id"]) && observed > 0 && override.observedAt.After(activityStatusObservedAt(observed))
				if !newerSameProcess && (!exists || override.processInstanceID == "" || override.processInstanceID != stringValue(agent["process_instance_id"]) || override.attentionKey == "" || override.attentionSignature != stringValue(agent["attention_signature"])) {
					if agent["status"] != "working" {
						agent["status"] = "unavailable"
					}
					agent["queued_followup_question"] = false
					continue
				}
			}
			if !exists {
				continue
			}
			if !agentStatusOverrideObservationMatches(agent, override, s.attentionAckNow()) {
				continue
			}
			delete(agent, "attention_pending")
			delete(agent, "attention_key")
			delete(agent, "attention_signature")
			delete(agent, "attention_acknowledged")
			agent["queued_followup_question"] = override.queuedFollowupQuestion
			if override.attentionKey != "" {
				_, acknowledged, err := s.attentionAckStore().acknowledged(override.attentionKey, s.attentionAckNow())
				if err != nil {
					return err
				}
				agent["attention_key"] = override.attentionKey
				agent["attention_signature"] = override.attentionSignature
				agent["attention_acknowledged"] = acknowledged || override.attentionAcknowledged
			}
			agent["status"] = override.state
			agent["attention_kind"] = override.attentionKind
			agent["status_observed_at"] = float64(override.observedAt.UnixNano()) / 1e9
			delete(agent, "working_stopped_ts")
			delete(agent, "transition_id")
			delete(agent, "cooldown_attention_key")
			delete(agent, "cooldown_acknowledged")
			if override.workingStoppedTS <= 0 || override.cooldownAttentionKey == "" {
				continue
			}
			agent["working_stopped_ts"] = override.workingStoppedTS
			agent["transition_id"] = override.transitionID
			agent["cooldown_attention_key"] = override.cooldownAttentionKey
			_, acknowledged, err := s.attentionAckStore().acknowledged(override.cooldownAttentionKey, s.attentionAckNow())
			if err != nil {
				return err
			}
			agent["cooldown_acknowledged"] = acknowledged
		}
	}
	return nil
}

func goTmuxSignalFields(line string, want int) ([]string, bool) {
	parts := splitTmuxFields(line)
	return parts, len(parts) == want
}

func goTmuxSignalInt(value string) int {
	parsed, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil {
		return 0
	}
	return parsed
}

func goTmuxSignalNullableInt(value string) any {
	parsed, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil || strings.TrimSpace(value) == "" {
		return nil
	}
	return parsed
}

func goTmuxSignalBool(value string) bool {
	return strings.TrimSpace(value) == "1"
}

func goTmuxSignalList(value string) []string {
	value = strings.NewReplacer(",", " ").Replace(value)
	items := strings.Fields(value)
	if len(items) == 0 {
		return []string{}
	}
	return items
}

func goTmuxSignalWindowKey(session, window string) string {
	return session + ":" + window
}

func goTmuxSignalPaneKey(session, window, pane string) string {
	return goTmuxSignalWindowKey(session, window) + "." + pane
}

func goTmuxSignalCommandError(label string, err error, output []byte) string {
	detail := strings.TrimSpace(string(output))
	if detail == "" {
		detail = err.Error()
	}
	return label + ": " + detail
}

func (s *server) tmuxSignalSnapshot(session string) (map[string]any, int) {
	started := time.Now()
	target := strings.TrimSpace(session)
	targetArgs := []string{}
	if target != "" {
		// Match Python's tmux_session_target(): a bare name permits tmux's
		// prefix/glob resolution (for example, `1` can select `12`).  Signal
		// snapshots are read-only, but selecting the wrong session still makes
		// Tabber display the wrong window, so filtered reads must be exact.
		targetArgs = []string{"-t", "=" + target + ":"}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	errorsPayload := make([]string, 0)
	read := func(label, format string, args ...string) []byte {
		commandArgs := append([]string{}, args...)
		commandArgs = append(commandArgs, "-F", format)
		output, err := s.tmuxCommandContext(ctx, commandArgs...).CombinedOutput()
		if err != nil {
			errorsPayload = append(errorsPayload, goTmuxSignalCommandError(label, err, output))
			return nil
		}
		return output
	}
	windowArgs := []string{"list-windows"}
	if target == "" {
		windowArgs = append(windowArgs, "-a")
	} else {
		windowArgs = append(windowArgs, targetArgs...)
	}
	paneArgs := []string{"list-panes"}
	if target == "" {
		paneArgs = append(paneArgs, "-a")
	} else {
		paneArgs = append(paneArgs, targetArgs...)
	}
	clientArgs := []string{"list-clients"}
	if target != "" {
		clientArgs = append(clientArgs, targetArgs...)
	}
	windowsOutput := read("tmux list-windows failed", goTmuxSignalWindowFormat, windowArgs...)
	panesOutput := read("tmux list-panes failed", goTmuxSignalPaneFormat, paneArgs...)
	clientsOutput := read("tmux list-clients failed", goTmuxSignalClientFormat, clientArgs...)

	windows := make([]map[string]any, 0)
	windowsByKey := make(map[string]map[string]any)
	sessions := make(map[string]map[string]any)
	for _, line := range strings.Split(strings.TrimSpace(string(windowsOutput)), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		parts, ok := goTmuxSignalFields(line, 22)
		if !ok {
			errorsPayload = append(errorsPayload, "invalid tmux sub-window signal row")
			continue
		}
		sessionName, windowIndex := parts[0], parts[6]
		activityTS := goTmuxSignalInt(parts[10])
		window := map[string]any{
			"key": goTmuxSignalWindowKey(sessionName, windowIndex), "session": sessionName, "session_name": sessionName,
			"session_id": parts[1], "session_activity_ts": goTmuxSignalInt(parts[2]), "session_last_attached_ts": goTmuxSignalInt(parts[3]),
			"session_attached": goTmuxSignalInt(parts[4]), "session_attached_list": parts[5], "window_index": windowIndex,
			"window": windowIndex, "window_id": parts[7], "window_name": parts[8], "active": goTmuxSignalBool(parts[9]),
			"activity_ts": activityTS, "window_activity": activityTS, "activity_age_seconds": nil,
			"activity_flag": goTmuxSignalBool(parts[11]), "bell_flag": goTmuxSignalBool(parts[12]), "silence_flag": goTmuxSignalBool(parts[13]),
			"active_clients": goTmuxSignalInt(parts[14]), "active_clients_list": parts[15], "pane_count": goTmuxSignalInt(parts[16]),
			"width": goTmuxSignalInt(parts[17]), "height": goTmuxSignalInt(parts[18]), "zoomed": goTmuxSignalBool(parts[19]),
			"layout": parts[20], "visible_layout": parts[21], "panes": []map[string]any{},
			"active_client_details": []map[string]any{}, "authoritative_client": nil,
		}
		if activityTS > 0 {
			window["activity_age_seconds"] = math.Max(0, time.Now().Sub(time.Unix(int64(activityTS), 0)).Seconds())
		}
		windows = append(windows, window)
		windowsByKey[window["key"].(string)] = window
		if sessions[sessionName] == nil {
			sessions[sessionName] = map[string]any{
				"name": sessionName, "session_id": parts[1], "activity_ts": goTmuxSignalInt(parts[2]),
				"last_attached_ts": goTmuxSignalInt(parts[3]), "attached": goTmuxSignalInt(parts[4]),
				"attached_list": parts[5], "windows": []string{},
			}
		}
		sessions[sessionName]["windows"] = append(sessions[sessionName]["windows"].([]string), window["key"].(string))
	}

	panes := make([]map[string]any, 0)
	orphanPanes := make([]map[string]any, 0)
	agents := make([]map[string]any, 0)
	for _, line := range strings.Split(strings.TrimSpace(string(panesOutput)), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		parts, ok := goTmuxSignalFields(line, 23)
		if !ok {
			errorsPayload = append(errorsPayload, "invalid tmux pane signal row")
			continue
		}
		sessionName, windowIndex, paneIndex, paneID := parts[0], parts[1], parts[3], parts[4]
		command := parts[7]
		panePID := goTmuxSignalInt(parts[18])
		pane := map[string]any{
			"key": goTmuxSignalPaneKey(sessionName, windowIndex, paneIndex), "window_key": goTmuxSignalWindowKey(sessionName, windowIndex),
			"session": sessionName, "window_index": windowIndex, "window": windowIndex, "window_id": parts[2], "pane_index": paneIndex,
			"pane": paneIndex, "pane_id": paneID, "target": paneID, "active": goTmuxSignalBool(parts[5]), "current_path": parts[6],
			"current_command": command, "command": command, "title": parts[8], "dead": goTmuxSignalBool(parts[9]),
			"dead_status": goTmuxSignalNullableInt(parts[10]), "dead_signal": goTmuxSignalNullableInt(parts[11]), "dead_time": goTmuxSignalNullableInt(parts[12]),
			"alternate_on": goTmuxSignalBool(parts[13]), "in_mode": goTmuxSignalBool(parts[14]), "mode": parts[15],
			"input_off": goTmuxSignalBool(parts[16]), "synchronized": goTmuxSignalBool(parts[17]), "pid": goTmuxSignalNullableInt(parts[18]),
			"width": goTmuxSignalInt(parts[19]), "height": goTmuxSignalInt(parts[20]), "history_size": goTmuxSignalInt(parts[21]),
			"history_bytes": goTmuxSignalInt(parts[22]),
		}
		if kind, agentPID, agentCommand := s.tmuxSignalPaneAgent(panePID, command); kind != "" {
			pane["agent"] = kind
			agents = append(agents, map[string]any{"session": sessionName, "window_index": windowIndex, "pane_id": paneID, "target": paneID, "agent": kind, "pid": agentPID, "command": agentCommand, "current_path": parts[6], "alternate_on": pane["alternate_on"], "dead": pane["dead"]})
		} else {
			pane["agent"] = ""
		}
		panes = append(panes, pane)
		window := windowsByKey[goTmuxSignalWindowKey(sessionName, windowIndex)]
		if window == nil {
			orphanPanes = append(orphanPanes, pane)
			continue
		}
		window["panes"] = append(window["panes"].([]map[string]any), pane)
	}

	clients := make([]map[string]any, 0)
	for _, line := range strings.Split(strings.TrimSpace(string(clientsOutput)), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		parts, ok := goTmuxSignalFields(line, 9)
		if !ok {
			errorsPayload = append(errorsPayload, "invalid tmux client signal row")
			continue
		}
		clients = append(clients, map[string]any{"name": parts[0], "session": parts[1], "activity_ts": goTmuxSignalInt(parts[2]), "width": goTmuxSignalInt(parts[3]), "height": goTmuxSignalInt(parts[4]), "flags": parts[5], "control_mode": goTmuxSignalBool(parts[6]), "readonly": goTmuxSignalBool(parts[7]), "user": parts[8]})
	}

	for _, window := range windows {
		sort.SliceStable(window["panes"].([]map[string]any), func(i, j int) bool {
			left := goTmuxSignalInt(window["panes"].([]map[string]any)[i]["pane_index"].(string))
			right := goTmuxSignalInt(window["panes"].([]map[string]any)[j]["pane_index"].(string))
			return left < right
		})
		names := make(map[string]struct{})
		for _, name := range goTmuxSignalList(window["active_clients_list"].(string)) {
			names[name] = struct{}{}
		}
		active := make([]map[string]any, 0)
		var authoritative map[string]any
		for _, client := range clients {
			if client["session"] != window["session"] {
				continue
			}
			if _, ok := names[client["name"].(string)]; !ok {
				continue
			}
			active = append(active, client)
			if client["control_mode"].(bool) || client["width"].(int) <= 0 || client["height"].(int) <= 0 {
				continue
			}
			if authoritative == nil || client["activity_ts"].(int) > authoritative["activity_ts"].(int) || (client["activity_ts"].(int) == authoritative["activity_ts"].(int) && client["width"].(int)*client["height"].(int) > authoritative["width"].(int)*authoritative["height"].(int)) {
				authoritative = client
			}
		}
		window["active_client_details"] = active
		if authoritative != nil {
			window["authoritative_client"] = map[string]any{"client_name": authoritative["name"], "client_user": authoritative["user"], "activity_ts": authoritative["activity_ts"], "width": authoritative["width"], "height": authoritative["height"], "readonly": authoritative["readonly"], "flags": authoritative["flags"], "reason": "most-recent-active-viewer"}
		}
	}

	sessionNames := make([]string, 0, len(sessions))
	for name := range sessions {
		sessionNames = append(sessionNames, name)
	}
	sort.SliceStable(sessionNames, func(i, j int) bool {
		left, right := tmuxSessionSortKey(sessionNames[i]), tmuxSessionSortKey(sessionNames[j])
		if left.rank != right.rank {
			return left.rank < right.rank
		}
		if left.group != right.group {
			return left.group < right.group
		}
		return left.number < right.number
	})
	sortedSessions := make(map[string]any, len(sessionNames))
	for _, name := range sessionNames {
		sortedSessions[name] = sessions[name]
	}
	sort.SliceStable(windows, func(i, j int) bool {
		left, right := tmuxSessionSortKey(windows[i]["session"].(string)), tmuxSessionSortKey(windows[j]["session"].(string))
		if left.rank != right.rank {
			return left.rank < right.rank
		}
		if left.group != right.group {
			return left.group < right.group
		}
		if left.number != right.number {
			return left.number < right.number
		}
		return goTmuxSignalInt(windows[i]["window_index"].(string)) < goTmuxSignalInt(windows[j]["window_index"].(string))
	})
	status := http.StatusOK
	if len(errorsPayload) > 0 {
		status = http.StatusServiceUnavailable
	}
	return map[string]any{
		"ok": len(errorsPayload) == 0, "generated_at": float64(time.Now().UnixNano()) / 1e9,
		"compute_ms": time.Since(started).Seconds() * 1000, "window_count": len(windows), "pane_count": len(panes),
		"client_count": len(clients), "agent_count": len(agents), "sessions": sortedSessions, "session_order": sessionNames,
		"windows": windows, "clients": clients, "orphan_panes": orphanPanes, "agents": agents, "errors": errorsPayload,
	}, status
}

func transcriptSignature(path string) (int64, int64) {
	// SQLite commits can land in the WAL while the main database remains unchanged.
	// Include both sidecars so an OpenCode stream observes those commits as well as
	// ordinary JSONL appends and replacements.
	paths := []string{path, path + "-wal", path + "-shm"}
	var size int64
	hasher := fnv.New64a()
	for index, candidate := range paths {
		info, err := os.Stat(candidate)
		if err != nil {
			continue
		}
		size += info.Size() + int64(index)
		_, _ = fmt.Fprintf(hasher, "%d:%s:%d:%d:%v\x00", index, candidate, info.Size(), info.ModTime().UnixNano(), info.Sys())
	}
	return size, int64(hasher.Sum64())
}

func readTranscriptItemsSince(path string, offset int64) ([]map[string]any, int64, error) {
	items, nextOffset, _, err := readTranscriptItemsSinceWithPending(path, offset, nil)
	return items, nextOffset, err
}

func readTranscriptItemsSinceWithPending(path string, offset int64, pending []byte) ([]map[string]any, int64, []byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, offset, pending, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, offset, pending, err
	}
	readStart := offset + int64(len(pending))
	if offset < 0 || readStart < offset || readStart > info.Size() {
		return nil, 0, nil, fmt.Errorf("transcript offset is outside file")
	}
	if _, err := file.Seek(readStart, io.SeekStart); err != nil {
		return nil, offset, pending, err
	}
	data, err := io.ReadAll(file)
	if err != nil {
		return nil, offset, pending, err
	}
	data = append(append([]byte(nil), pending...), data...)
	items := make([]map[string]any, 0)
	nextOffset := offset
	consumedTotal := 0
	for {
		newline := bytes.IndexByte(data, '\n')
		if newline < 0 {
			break
		}
		line := data[:newline+1]
		var record map[string]any
		if json.Unmarshal(line, &record) == nil && record != nil {
			items = append(items, transcriptItems(record)...)
		}
		consumed := newline + 1
		consumedTotal += consumed
		nextOffset = offset + int64(consumedTotal)
		data = data[consumed:]
	}
	return items, nextOffset, data, nil
}

func transcriptPathIdentity(path string) (int64, int64, string) {
	info, err := os.Stat(path)
	if err != nil {
		return 0, 0, ""
	}
	return info.Size(), info.ModTime().UnixNano(), fmt.Sprintf("%d:%v", info.Size(), info.Sys())
}

func readContextItems(path string, limit int) ([]map[string]any, error) {
	data, err := readTranscriptTailWithByteLimit(path, maxContextTranscriptLines, maxContextTranscriptBytes)
	if err != nil {
		return nil, err
	}
	all := make([]map[string]any, 0, limit)
	for _, line := range strings.Split(data, "\n") {
		var record map[string]any
		if json.Unmarshal([]byte(line), &record) != nil || record == nil {
			continue
		}
		all = append(all, transcriptItems(record)...)
	}
	if len(all) > limit {
		all = all[len(all)-limit:]
	}
	return all, nil
}

func readContextItemsForAgent(path string, agent map[string]any, limit int) ([]map[string]any, error) {
	if kind, _ := agent["kind"].(string); kind == "opencode" {
		sessionID, _ := agent["session_id"].(string)
		if sessionID == "" {
			return nil, fmt.Errorf("OpenCode session identity unavailable")
		}
		database, err := stats.OpenOpenCodeReadOnly(context.Background(), path)
		if err != nil {
			return nil, err
		}
		defer database.Close()
		contextItems, err := database.ReadContextItems(context.Background(), sessionID, limit)
		if err != nil {
			return nil, err
		}
		items := make([]map[string]any, 0, len(contextItems))
		for _, item := range contextItems {
			items = append(items, map[string]any{
				"role": item.Role, "timestamp": item.Timestamp, "cwd": item.CWD, "text": item.Text,
			})
		}
		return items, nil
	}
	return readContextItems(path, limit)
}

func transcriptItems(record map[string]any) []map[string]any {
	timestamp, _ := record["timestamp"].(string)
	cwd, _ := record["cwd"].(string)
	entryType, _ := record["type"].(string)
	if message, ok := record["message"].(map[string]any); ok {
		role, _ := message["role"].(string)
		if role == "" {
			role = entryType
			if role == "" {
				role = "message"
			}
		}
		return transcriptContentBlocks(message["content"], role, timestamp, cwd)
	}
	payload, _ := record["payload"].(map[string]any)
	if payload == nil {
		return nil
	}
	payloadType, _ := payload["type"].(string)
	if payloadType == "message" {
		role, _ := payload["role"].(string)
		if role == "" {
			role = "message"
		}
		return transcriptContentBlocks(payload["content"], role, timestamp, cwd)
	}
	if payloadType == "function_call" || payloadType == "custom_tool_call" {
		name, _ := payload["name"].(string)
		if name == "" {
			name = "tool"
		}
		argumentKey := "arguments"
		if payloadType == "custom_tool_call" {
			argumentKey = "input"
		}
		encoded, _ := json.Marshal(payload[argumentKey])
		return []map[string]any{{"role": "tool_use", "timestamp": timestamp, "cwd": cwd, "text": boundedText(name+"\n"+string(encoded), maxToolItemText)}}
	}
	if payloadType == "function_call_output" || payloadType == "custom_tool_call_output" {
		return []map[string]any{{"role": "tool_result", "timestamp": timestamp, "cwd": cwd, "text": boundedText(transcriptValueText(payload["output"]), maxToolItemText)}}
	}
	if payloadType == "agent_message" || payloadType == "user_message" {
		message, _ := payload["message"].(string)
		role := "assistant"
		if payloadType == "user_message" {
			role = "user"
		}
		if strings.TrimSpace(message) == "" {
			return nil
		}
		return []map[string]any{{"role": role, "timestamp": timestamp, "cwd": cwd, "text": boundedText(message, maxContextItemText)}}
	}
	if payloadType == "task_started" || payloadType == "task_complete" {
		field := "turn_id"
		if payloadType == "task_complete" {
			field = "last_agent_message"
		}
		message, _ := payload[field].(string)
		if message == "" {
			return nil
		}
		return []map[string]any{{"role": payloadType, "timestamp": timestamp, "cwd": cwd, "text": boundedText(message, maxToolItemText)}}
	}
	if payloadType == "patch_apply_end" {
		stdout, _ := payload["stdout"].(string)
		stderr, _ := payload["stderr"].(string)
		parts := make([]string, 0, 2)
		if strings.TrimSpace(stdout) != "" {
			parts = append(parts, strings.TrimSpace(stdout))
		}
		if strings.TrimSpace(stderr) != "" {
			parts = append(parts, strings.TrimSpace(stderr))
		}
		if len(parts) == 0 {
			return nil
		}
		return []map[string]any{{"role": "tool_result", "timestamp": timestamp, "cwd": cwd, "text": boundedText(strings.Join(parts, "\n"), maxToolItemText)}}
	}
	return nil
}

func transcriptContentBlocks(raw any, role, timestamp, cwd string) []map[string]any {
	if text, ok := raw.(string); ok && strings.TrimSpace(text) != "" {
		return []map[string]any{{"role": role, "timestamp": timestamp, "cwd": cwd, "text": boundedText(text, maxContextItemText)}}
	}
	blocks, ok := raw.([]any)
	if !ok {
		return nil
	}
	items := make([]map[string]any, 0, len(blocks))
	for _, rawBlock := range blocks {
		block, _ := rawBlock.(map[string]any)
		if block == nil {
			continue
		}
		kind, _ := block["type"].(string)
		if kind == "text" || kind == "input_text" || kind == "output_text" {
			text, _ := block["text"].(string)
			if strings.TrimSpace(text) != "" {
				items = append(items, map[string]any{"role": role, "timestamp": timestamp, "cwd": cwd, "text": boundedText(text, maxContextItemText)})
			}
		} else if kind == "tool_use" {
			name, _ := block["name"].(string)
			encoded, _ := json.Marshal(block["input"])
			items = append(items, map[string]any{"role": "tool_use", "timestamp": timestamp, "cwd": cwd, "text": boundedText(name+"\n"+string(encoded), maxToolItemText)})
		} else if kind == "tool_result" {
			text := transcriptValueText(block["content"])
			items = append(items, map[string]any{"role": "tool_result", "timestamp": timestamp, "cwd": cwd, "text": boundedText(text, maxToolItemText)})
		}
	}
	return items
}

func transcriptValueText(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	return string(encoded)
}
