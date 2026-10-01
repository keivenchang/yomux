// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bufio"
	"container/heap"
	"context"
	"encoding/json"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/cache"
)

const (
	maxSessionTranscriptLineBytes             = 4 * 1024 * 1024
	maxHistoricalCodexFiles                   = 256
	maxHistoricalCodexAttributionCacheEntries = maxHistoricalCodexFiles
	maxHistoricalCodexAttributionCacheBytes   = 8 * 1024 * 1024
)

var codexPatchHeader = regexp.MustCompile(`\*\*\* (Add|Update|Delete) File: ([^"\\\n]+)`)
var generatedUploadFileNamePattern = regexp.MustCompile(`^\d{8}-\d{3}(?:-[^/]+)?\.[A-Za-z0-9]{1,12}$`)

// SessionFileAgent is the small, serialized subset of live agent discovery needed by Differ.
// The server owns process/tmux discovery; this package owns transcript interpretation and path
// attribution so all callers use one source of truth.
type SessionFileAgent struct {
	Kind         string
	CWD          string
	Transcript   string
	SessionID    string
	PaneTarget   string
	Command      string
	PID          int
	Window       string
	WindowIndex  int
	Pane         string
	Active       bool
	WindowActive bool
}

type sessionFileAttribution struct {
	agents  []string
	windows []map[string]any
	status  string
	mtime   int64
}

func (a *sessionFileAttribution) add(agent SessionFileAgent, marker string, mtime int64) {
	if agent.Kind != "" && !containsString(a.agents, agent.Kind) {
		a.agents = append(a.agents, agent.Kind)
	}
	window := map[string]any{
		"kind": agent.Kind, "window": agent.Window, "window_index": agent.WindowIndex,
		"pane": agent.Pane, "pane_target": agent.PaneTarget,
	}
	windowKey := agent.Kind + "\x00" + agent.Window + "\x00" + agent.Pane + "\x00" + agent.PaneTarget
	hasWindow := false
	for _, existing := range a.windows {
		key := strings.Join([]string{stringValue(existing["kind"]), stringValue(existing["window"]), stringValue(existing["pane"]), stringValue(existing["pane_target"])}, "\x00")
		if key == windowKey {
			hasWindow = true
			break
		}
	}
	if !hasWindow {
		a.windows = append(a.windows, window)
	}
	a.status = sessionFileStatusMerge(a.status, marker)
	if mtime > a.mtime {
		a.mtime = mtime
	}
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}

func sessionFileStatusMerge(existing, candidate string) string {
	if existing == "A" || candidate == "A" {
		return "A"
	}
	if existing == "D" || candidate == "D" {
		return "D"
	}
	return "M"
}

func resolveSessionTranscriptPath(raw, cwd string) string {
	path := strings.TrimSpace(raw)
	if path == "" || strings.Contains(path, "${") || strings.Contains(path, "$(") {
		return ""
	}
	if !filepath.IsAbs(path) {
		if strings.TrimSpace(cwd) == "" {
			return ""
		}
		path = filepath.Join(cwd, path)
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return ""
	}
	return filepath.Clean(absolute)
}

func recordSessionTranscriptPath(changes map[string]*sessionFileAttribution, raw, cwd string, agent SessionFileAgent, marker string, mtime int64) {
	path := resolveSessionTranscriptPath(raw, cwd)
	if path == "" {
		return
	}
	entry := changes[path]
	if entry == nil {
		entry = &sessionFileAttribution{}
		changes[path] = entry
	}
	entry.add(agent, marker, mtime)
}

func scanSessionTranscript(path string, agent SessionFileAgent, cutoff time.Time) (map[string]*sessionFileAttribution, bool) {
	return scanSessionTranscriptContext(context.Background(), path, agent, cutoff)
}

func scanSessionTranscriptContext(ctx context.Context, path string, agent SessionFileAgent, cutoff time.Time) (map[string]*sessionFileAttribution, bool) {
	if ctx.Err() != nil {
		return nil, false
	}
	stat, err := os.Stat(path)
	if err != nil || stat.ModTime().Before(cutoff) {
		return map[string]*sessionFileAttribution{}, false
	}
	file, err := os.Open(path)
	if err != nil {
		return map[string]*sessionFileAttribution{}, false
	}
	defer file.Close()
	return scanSessionTranscriptReader(ctx, file, agent, stat.ModTime().Unix())
}

func scanSessionTranscriptReader(ctx context.Context, source io.Reader, agent SessionFileAgent, mtime int64) (map[string]*sessionFileAttribution, bool) {
	changes := make(map[string]*sessionFileAttribution)
	complete := scanTranscriptLines(ctx, source, func(line []byte) {
		switch strings.ToLower(strings.TrimSpace(agent.Kind)) {
		case "claude":
			scanClaudeTranscriptLine(line, agent, mtime, changes)
		case "codex":
			scanCodexTranscriptLine(line, agent, mtime, changes)
		}
	})
	if !complete {
		return nil, false
	}
	return changes, true
}

func scanTranscriptLines(ctx context.Context, source io.Reader, visit func([]byte)) bool {
	scanner := bufio.NewScanner(contextReader{ctx: ctx, source: source})
	scanner.Buffer(make([]byte, 64*1024), maxSessionTranscriptLineBytes)
	for scanner.Scan() {
		if ctx.Err() != nil {
			return false
		}
		visit(scanner.Bytes())
	}
	return scanner.Err() == nil && ctx.Err() == nil
}

func scanClaudeTranscriptLine(line []byte, agent SessionFileAgent, mtime int64, changes map[string]*sessionFileAttribution) {
	var record map[string]any
	if json.Unmarshal(line, &record) != nil || stringValue(record["type"]) != "assistant" {
		return
	}
	message, _ := record["message"].(map[string]any)
	content, _ := message["content"].([]any)
	for _, rawItem := range content {
		item, _ := rawItem.(map[string]any)
		if stringValue(item["type"]) != "tool_use" {
			continue
		}
		tool := stringValue(item["name"])
		if tool != "Edit" && tool != "Write" && tool != "MultiEdit" && tool != "NotebookEdit" {
			continue
		}
		input, _ := item["input"].(map[string]any)
		marker := "M"
		if tool == "Write" {
			marker = "A"
		}
		recordSessionTranscriptPath(changes, stringValue(input["file_path"]), agent.CWD, agent, marker, mtime)
	}
}

func scanCodexTranscriptLine(line []byte, agent SessionFileAgent, mtime int64, changes map[string]*sessionFileAttribution) {
	applyCodexTranscriptOperations(changes, agent, mtime, parseCodexTranscriptOperations(line))
}

type codexTranscriptPatch struct {
	path   string
	marker string
}

type codexTranscriptOperations struct {
	patches []codexTranscriptPatch
	command string
	cwd     string
}

type historicalCodexTranscript struct {
	recordedCWD string
	operations  []codexTranscriptOperations
}

func parseCodexTranscriptOperations(line []byte) codexTranscriptOperations {
	operations := codexTranscriptOperations{}
	for _, match := range codexPatchHeader.FindAllStringSubmatch(string(line), -1) {
		marker := map[string]string{"Add": "A", "Update": "M", "Delete": "D"}[match[1]]
		operations.patches = append(operations.patches, codexTranscriptPatch{path: strings.Clone(match[2]), marker: marker})
	}
	var record map[string]any
	if json.Unmarshal(line, &record) != nil {
		return operations
	}
	payload, _ := record["payload"].(map[string]any)
	toolType := stringValue(payload["type"])
	if toolType != "function_call" && toolType != "custom_tool_call" {
		return operations
	}
	tool := stringValue(payload["name"])
	if tool != "exec_command" && tool != "shell_command" && tool != "shell" {
		return operations
	}
	arguments := codexArguments(payload["arguments"])
	operations.command = stringValue(arguments["cmd"])
	if operations.command == "" {
		operations.command = stringValue(arguments["command"])
	}
	operations.cwd = stringValue(arguments["workdir"])
	if operations.cwd == "" {
		operations.cwd = stringValue(arguments["cwd"])
	}
	return operations
}

func parseCodexTranscriptRecordedCWD(line []byte) string {
	var record map[string]any
	if json.Unmarshal(line, &record) != nil || stringValue(record["type"]) != "session_meta" {
		return ""
	}
	payload, _ := record["payload"].(map[string]any)
	return strings.TrimSpace(stringValue(payload["cwd"]))
}

func applyCodexTranscriptOperations(changes map[string]*sessionFileAttribution, agent SessionFileAgent, mtime int64, operations codexTranscriptOperations) {
	for _, patch := range operations.patches {
		recordSessionTranscriptPath(changes, patch.path, agent.CWD, agent, patch.marker, mtime)
	}
	if operations.command == "" {
		return
	}
	cwd := operations.cwd
	if cwd == "" {
		cwd = agent.CWD
	}
	for path, markers := range scanShellChanges(operations.command, cwd) {
		for _, marker := range markers {
			recordSessionTranscriptPath(changes, path, cwd, agent, marker, mtime)
		}
	}
}

func codexArguments(raw any) map[string]any {
	if arguments, ok := raw.(map[string]any); ok {
		return arguments
	}
	text, ok := raw.(string)
	if !ok || strings.TrimSpace(text) == "" {
		return nil
	}
	var arguments map[string]any
	if json.Unmarshal([]byte(text), &arguments) != nil {
		return nil
	}
	return arguments
}

func shellWords(command string) []string {
	words := make([]string, 0)
	current := strings.Builder{}
	quote := rune(0)
	escaped := false
	flush := func() {
		if current.Len() > 0 {
			words = append(words, current.String())
			current.Reset()
		}
	}
	for _, char := range command {
		if escaped {
			current.WriteRune(char)
			escaped = false
			continue
		}
		if char == '\\' && quote != '\'' {
			escaped = true
			continue
		}
		if quote != 0 {
			if char == quote {
				quote = 0
			} else {
				current.WriteRune(char)
			}
			continue
		}
		if char == '\'' || char == '"' {
			quote = char
			continue
		}
		if char == ';' || char == '|' || char == '&' || char == '\n' || char == '\t' || char == ' ' {
			flush()
			if char == ';' || char == '|' || char == '&' {
				if len(words) == 0 || words[len(words)-1] != string(char) {
					words = append(words, string(char))
				}
			}
			continue
		}
		current.WriteRune(char)
	}
	if escaped {
		current.WriteByte('\\')
	}
	flush()
	return words
}

func scanShellChanges(command, cwd string) map[string][]string {
	changes := make(map[string][]string)
	words := shellWords(command)
	segment := make([]string, 0)
	flush := func() {
		if len(segment) == 0 {
			return
		}
		if segment[0] == "cd" && len(segment) > 1 {
			if next := resolveSessionTranscriptPath(segment[1], cwd); next != "" {
				cwd = next
			}
			segment = segment[:0]
			return
		}
		if runner := inlineShellCommand(segment); runner != "" {
			for path, markers := range scanShellChanges(runner, cwd) {
				changes[path] = appendUniqueMarkers(changes[path], markers...)
			}
			segment = segment[:0]
			return
		}
		for path, markers := range scanGitChanges(segment, cwd) {
			changes[path] = appendUniqueMarkers(changes[path], markers...)
		}
		segment = segment[:0]
	}
	for _, word := range words {
		if word == ";" || word == "|" || word == "&" {
			flush()
			continue
		}
		segment = append(segment, word)
	}
	flush()
	return changes
}

func inlineShellCommand(words []string) string {
	if len(words) == 0 || (filepath.Base(words[0]) != "bash" && filepath.Base(words[0]) != "sh" && filepath.Base(words[0]) != "zsh") {
		return ""
	}
	for index := 1; index+1 < len(words); index++ {
		if words[index] == "-c" || (strings.HasPrefix(words[index], "-") && strings.Contains(words[index], "c")) {
			return words[index+1]
		}
	}
	return ""
}

func scanGitChanges(words []string, cwd string) map[string][]string {
	if len(words) == 0 || words[0] != "git" {
		return nil
	}
	index := 1
	for index < len(words) && strings.HasPrefix(words[index], "-") {
		if words[index] == "-C" && index+1 < len(words) {
			if next := resolveSessionTranscriptPath(words[index+1], cwd); next != "" {
				cwd = next
			}
			index += 2
			continue
		}
		index++
	}
	if index >= len(words) {
		return nil
	}
	subcommand := words[index]
	index++
	marker := ""
	switch subcommand {
	case "add":
		marker = "M"
	case "rm":
		marker = "D"
	case "mv":
		paths := gitPathArguments(words[index:])
		changes := make(map[string][]string)
		if len(paths) > 0 {
			if resolved := resolveSessionTranscriptPath(paths[0], cwd); resolved != "" {
				changes[resolved] = []string{"D"}
			}
		}
		if len(paths) > 1 {
			if resolved := resolveSessionTranscriptPath(paths[len(paths)-1], cwd); resolved != "" {
				changes[resolved] = appendUniqueMarkers(changes[resolved], "A")
			}
		}
		return changes
	default:
		return nil
	}
	changes := make(map[string][]string)
	for _, path := range gitPathArguments(words[index:]) {
		if resolved := resolveSessionTranscriptPath(path, cwd); resolved != "" {
			changes[resolved] = appendUniqueMarkers(changes[resolved], marker)
		}
	}
	return changes
}

func gitPathArguments(words []string) []string {
	paths := make([]string, 0)
	positional := false
	for index := 0; index < len(words); index++ {
		word := words[index]
		if word == "--" {
			positional = true
			continue
		}
		if word == ";" || word == "|" || word == "&" {
			break
		}
		if !positional && strings.HasPrefix(word, "-") {
			if word == "--pathspec-from-file" || word == "--chmod" {
				index++
			}
			continue
		}
		paths = append(paths, word)
	}
	return paths
}

func appendUniqueMarkers(existing []string, markers ...string) []string {
	for _, marker := range markers {
		if marker != "A" && marker != "M" && marker != "D" || containsString(existing, marker) {
			continue
		}
		existing = append(existing, marker)
	}
	return existing
}

func defaultSessionFileExcluded(path string) bool {
	for _, part := range strings.Split(filepath.Clean(path), string(os.PathSeparator)) {
		switch part {
		case ".git", ".hg", ".svn", ".jj", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".ngc", ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".venv", ".coverage", ".hypothesis", ".nox", ".pnpm-store", ".yarn", ".vscode", ".idea", "venv", "node_modules", "__pycache__", "dist", "build", "target", "coverage", "htmlcov":
			return true
		}
	}
	return false
}

func generatedSessionUploadName(path string) bool {
	return generatedUploadFileNamePattern.MatchString(filepath.Base(path))
}

func sessionFileAttributionsForRepo(changes map[string]*sessionFileAttribution, repo string) map[string]*sessionFileAttribution {
	result := make(map[string]*sessionFileAttribution)
	repoAbsolute, err := filepath.Abs(repo)
	if err != nil {
		return result
	}
	for path, attribution := range changes {
		pathAbsolute, pathErr := filepath.Abs(path)
		if pathErr != nil {
			continue
		}
		relative, relErr := filepath.Rel(repoAbsolute, pathAbsolute)
		if relErr != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
			continue
		}
		if defaultSessionFileExcluded(pathAbsolute) && !strings.Contains(pathAbsolute, string(os.PathSeparator)+".uploads"+string(os.PathSeparator)) {
			continue
		}
		result[filepath.ToSlash(relative)] = attribution
	}
	return result
}

func sessionFileTranscriptCWDs(agents []SessionFileAgent, roots []string) []string {
	values := make([]string, 0, len(agents)+len(roots))
	for _, agent := range agents {
		if agent.CWD != "" {
			values = append(values, agent.CWD)
		}
	}
	values = append(values, roots...)
	seen := make(map[string]bool)
	result := make([]string, 0, len(values))
	for _, value := range values {
		absolute, err := filepath.Abs(strings.TrimSpace(value))
		if err != nil || absolute == "" || seen[absolute] {
			continue
		}
		seen[absolute] = true
		result = append(result, absolute)
	}
	return result
}

func collectSessionFileAttributions(ctx context.Context, agents []SessionFileAgent, roots []string, hours float64) map[string]*sessionFileAttribution {
	cutoff := time.Now().Add(-time.Duration(hours * float64(time.Hour)))
	changes := make(map[string]*sessionFileAttribution)
	seenTranscripts := make(map[string]bool)
	for _, agent := range agents {
		if ctx.Err() != nil {
			return changes
		}
		if agent.Kind != "claude" && agent.Kind != "codex" || agent.Transcript == "" {
			continue
		}
		path, err := filepath.Abs(agent.Transcript)
		if err != nil || seenTranscripts[path] {
			continue
		}
		seenTranscripts[path] = true
		for touchedPath, attribution := range mustScanSessionTranscript(ctx, path, agent, cutoff) {
			mergeSessionFileAttribution(changes, touchedPath, attribution)
		}
	}
	// A session can retain a clean repository after its Codex pane has exited. Enumerate the
	// bounded archive once, parse each selected rollout once, and project its commands against the
	// session CWDs that can supply their missing working directory.
	if len(roots) > 0 && ctx.Err() == nil {
		home, err := os.UserHomeDir()
		if err == nil {
			historical := collectHistoricalCodexAttributions(ctx, filepath.Join(home, ".codex", "sessions"), sessionFileTranscriptCWDs(agents, roots), cutoff, seenTranscripts, func(path string) (io.ReadCloser, error) {
				return os.Open(path)
			})
			for touchedPath, attribution := range historical {
				mergeSessionFileAttribution(changes, touchedPath, attribution)
			}
		}
	}
	return changes
}

func mustScanSessionTranscript(ctx context.Context, path string, agent SessionFileAgent, cutoff time.Time) map[string]*sessionFileAttribution {
	changes, _ := scanSessionTranscriptContext(ctx, path, agent, cutoff)
	return changes
}

func mergeSessionFileAttribution(target map[string]*sessionFileAttribution, path string, incoming *sessionFileAttribution) {
	if incoming == nil || defaultSessionFileExcluded(path) && !strings.Contains(path, string(os.PathSeparator)+".uploads"+string(os.PathSeparator)) {
		return
	}
	existing := target[path]
	if existing == nil {
		existing = &sessionFileAttribution{}
		target[path] = existing
	}
	for _, agent := range incoming.agents {
		if !containsString(existing.agents, agent) {
			existing.agents = append(existing.agents, agent)
		}
	}
	for _, window := range incoming.windows {
		key := strings.Join([]string{stringValue(window["kind"]), stringValue(window["window"]), stringValue(window["pane"]), stringValue(window["pane_target"])}, "\x00")
		found := false
		for _, current := range existing.windows {
			currentKey := strings.Join([]string{stringValue(current["kind"]), stringValue(current["window"]), stringValue(current["pane"]), stringValue(current["pane_target"])}, "\x00")
			if currentKey == key {
				found = true
				break
			}
		}
		if !found {
			copy := make(map[string]any, len(window))
			for key, value := range window {
				copy[key] = value
			}
			existing.windows = append(existing.windows, copy)
		}
	}
	existing.status = sessionFileStatusMerge(existing.status, incoming.status)
	if incoming.mtime > existing.mtime {
		existing.mtime = incoming.mtime
	}
}

type historicalCodexTranscriptCandidate struct {
	path  string
	mtime time.Time
	info  os.FileInfo
}

type historicalCodexAttributionRecord struct {
	info       os.FileInfo
	transcript historicalCodexTranscript
	weight     int64
}

type historicalCodexAttributionCache struct {
	mu      sync.Mutex
	records *cache.LRU[string, historicalCodexAttributionRecord]
	bytes   int64
}

func newHistoricalCodexAttributionCache() *historicalCodexAttributionCache {
	return &historicalCodexAttributionCache{
		records: cache.NewLRU[string, historicalCodexAttributionRecord](maxHistoricalCodexAttributionCacheEntries),
	}
}

var historicalCodexAttributions = newHistoricalCodexAttributionCache()

type historicalCodexCandidateHeap []historicalCodexTranscriptCandidate

func (candidates historicalCodexCandidateHeap) Len() int { return len(candidates) }

func (candidates historicalCodexCandidateHeap) Less(i, j int) bool {
	left, right := candidates[i], candidates[j]
	if left.mtime.Equal(right.mtime) {
		return left.path > right.path
	}
	return left.mtime.Before(right.mtime)
}

func (candidates historicalCodexCandidateHeap) Swap(i, j int) {
	candidates[i], candidates[j] = candidates[j], candidates[i]
}

func (candidates *historicalCodexCandidateHeap) Push(value any) {
	*candidates = append(*candidates, value.(historicalCodexTranscriptCandidate))
}

func (candidates *historicalCodexCandidateHeap) Pop() any {
	last := len(*candidates) - 1
	value := (*candidates)[last]
	*candidates = (*candidates)[:last]
	return value
}

func historicalCodexTranscriptCandidates(ctx context.Context, root string, cutoff time.Time) ([]historicalCodexTranscriptCandidate, bool) {
	return historicalCodexTranscriptCandidatesWithWalker(ctx, root, cutoff, filepath.WalkDir)
}

func historicalCodexTranscriptCandidatesWithWalker(ctx context.Context, root string, cutoff time.Time, walk func(string, fs.WalkDirFunc) error) ([]historicalCodexTranscriptCandidate, bool) {
	candidates := make(historicalCodexCandidateHeap, 0, maxHistoricalCodexFiles)
	complete := true
	err := walk(root, func(path string, entry os.DirEntry, walkErr error) error {
		if ctx.Err() != nil {
			complete = false
			return ctx.Err()
		}
		if walkErr != nil {
			complete = false
			return nil
		}
		if entry.IsDir() {
			return nil
		}
		if !strings.HasPrefix(entry.Name(), "rollout-") || !strings.HasSuffix(entry.Name(), ".jsonl") {
			return nil
		}
		info, infoErr := entry.Info()
		if infoErr != nil {
			complete = false
			return nil
		}
		if !info.ModTime().Before(cutoff) {
			candidate := historicalCodexTranscriptCandidate{path: path, mtime: info.ModTime(), info: info}
			if len(candidates) < maxHistoricalCodexFiles {
				heap.Push(&candidates, candidate)
			} else if historicalCodexCandidateIsNewer(candidate, candidates[0]) {
				candidates[0] = candidate
				heap.Fix(&candidates, 0)
			}
		}
		return nil
	})
	if err != nil {
		complete = false
	}
	sort.Slice(candidates, func(i, j int) bool {
		return historicalCodexCandidateIsNewer(candidates[i], candidates[j])
	})
	return []historicalCodexTranscriptCandidate(candidates), complete && ctx.Err() == nil
}

func historicalCodexCandidateIsNewer(left, right historicalCodexTranscriptCandidate) bool {
	if left.mtime.Equal(right.mtime) {
		return left.path < right.path
	}
	return left.mtime.After(right.mtime)
}

func collectHistoricalCodexAttributions(ctx context.Context, archiveRoot string, cwds []string, cutoff time.Time, seen map[string]bool, openFile func(string) (io.ReadCloser, error)) map[string]*sessionFileAttribution {
	return collectHistoricalCodexAttributionsWithCache(ctx, historicalCodexAttributions, archiveRoot, cwds, cutoff, seen, openFile)
}

func collectHistoricalCodexAttributionsWithCache(ctx context.Context, transcripts *historicalCodexAttributionCache, archiveRoot string, cwds []string, cutoff time.Time, seen map[string]bool, openFile func(string) (io.ReadCloser, error)) map[string]*sessionFileAttribution {
	return collectHistoricalCodexAttributionsWithWalker(ctx, transcripts, archiveRoot, cwds, cutoff, seen, openFile, filepath.WalkDir)
}

func collectHistoricalCodexAttributionsWithWalker(ctx context.Context, transcripts *historicalCodexAttributionCache, archiveRoot string, cwds []string, cutoff time.Time, seen map[string]bool, openFile func(string) (io.ReadCloser, error), walk func(string, fs.WalkDirFunc) error) map[string]*sessionFileAttribution {
	result := make(map[string]*sessionFileAttribution)
	if len(cwds) == 0 || ctx.Err() != nil {
		return result
	}
	candidates, _ := historicalCodexTranscriptCandidatesWithWalker(ctx, archiveRoot, cutoff, walk)
	for _, candidate := range candidates {
		if ctx.Err() != nil {
			break
		}
		if seen[candidate.path] {
			continue
		}
		transcript, complete := transcripts.scan(ctx, candidate, openFile)
		if !complete {
			continue
		}
		seen[candidate.path] = true
		if recordedCWD := resolveSessionTranscriptPath(transcript.recordedCWD, ""); recordedCWD != "" {
			changes := projectHistoricalCodexOperations(ctx, transcript.operations, recordedCWD, candidate.mtime.Unix())
			for path, attribution := range historicalCodexChangesForRoots(changes, cwds) {
				mergeSessionFileAttribution(result, path, attribution)
			}
			continue
		}
		for _, cwd := range cwds {
			if ctx.Err() != nil {
				return result
			}
			changes := projectHistoricalCodexOperations(ctx, transcript.operations, cwd, candidate.mtime.Unix())
			if ctx.Err() != nil {
				return result
			}
			if !historicalCodexChangesTouchRoot(changes, cwd) {
				continue
			}
			for path, attribution := range changes {
				mergeSessionFileAttribution(result, path, attribution)
			}
			break
		}
	}
	return result
}

func (c *historicalCodexAttributionCache) scan(ctx context.Context, candidate historicalCodexTranscriptCandidate, openFile func(string) (io.ReadCloser, error)) (historicalCodexTranscript, bool) {
	key := candidate.path
	c.mu.Lock()
	record, ok := c.records.Get(key)
	if ok && sameTranscriptFile(record.info, candidate.info) {
		c.mu.Unlock()
		return record.transcript, true
	}
	if ok {
		c.deleteLocked(key)
	}
	c.mu.Unlock()

	if ctx.Err() != nil {
		return historicalCodexTranscript{}, false
	}
	file, err := openFile(candidate.path)
	if err != nil {
		return historicalCodexTranscript{}, false
	}
	openedInfo := candidate.info
	if statter, ok := file.(interface{ Stat() (os.FileInfo, error) }); ok {
		openedInfo, err = statter.Stat()
		if err != nil || !sameTranscriptFile(candidate.info, openedInfo) {
			_ = file.Close()
			return historicalCodexTranscript{}, false
		}
	}
	transcript, weight, complete := scanHistoricalCodexOperations(ctx, file)
	if statter, ok := file.(interface{ Stat() (os.FileInfo, error) }); ok {
		after, statErr := statter.Stat()
		complete = complete && statErr == nil && sameTranscriptFile(openedInfo, after)
	}
	_ = file.Close()
	if !complete {
		return historicalCodexTranscript{}, false
	}
	currentInfo, err := os.Stat(candidate.path)
	if err != nil || !sameTranscriptFile(openedInfo, currentInfo) {
		return historicalCodexTranscript{}, false
	}
	// Bound retained parsed operations by semantic weight; raw rollout size includes discarded UI and tool output.
	c.set(key, historicalCodexAttributionRecord{info: currentInfo, transcript: transcript, weight: weight})
	return transcript, true
}

func sameTranscriptFile(left, right os.FileInfo) bool {
	return left != nil && right != nil && os.SameFile(left, right) && left.Size() == right.Size() && left.ModTime().Equal(right.ModTime())
}

func (c *historicalCodexAttributionCache) deleteLocked(key string) {
	if record, ok := c.records.Peek(key); ok {
		c.bytes -= record.weight
	}
	c.records.Delete(key)
}

func (c *historicalCodexAttributionCache) set(key string, record historicalCodexAttributionRecord) {
	if record.info == nil || record.weight < 0 || record.weight > maxHistoricalCodexAttributionCacheBytes {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.deleteLocked(key)
	for c.records.Len() >= maxHistoricalCodexAttributionCacheEntries || c.bytes+record.weight > maxHistoricalCodexAttributionCacheBytes {
		keys := c.records.KeysLeastRecentFirst()
		if len(keys) == 0 {
			return
		}
		c.deleteLocked(keys[0])
	}
	c.records.Set(key, record)
	c.bytes += record.weight
}

func scanHistoricalCodexOperations(ctx context.Context, source io.Reader) (historicalCodexTranscript, int64, bool) {
	transcript := historicalCodexTranscript{operations: make([]codexTranscriptOperations, 0)}
	var weight int64
	complete := scanTranscriptLines(ctx, source, func(line []byte) {
		if cwd := parseCodexTranscriptRecordedCWD(line); cwd != "" {
			transcript.recordedCWD = cwd
			weight += int64(len(cwd))
		}
		operations := parseCodexTranscriptOperations(line)
		if len(operations.patches) == 0 && operations.command == "" {
			return
		}
		transcript.operations = append(transcript.operations, operations)
		weight += 128 + int64(len(operations.command)+len(operations.cwd)) + int64(len(operations.patches))*48
		for _, patch := range operations.patches {
			weight += int64(len(patch.path))
		}
	})
	if !complete {
		return historicalCodexTranscript{}, 0, false
	}
	return transcript, weight, true
}

type contextReader struct {
	ctx    context.Context
	source io.Reader
}

func (reader contextReader) Read(buffer []byte) (int, error) {
	if err := reader.ctx.Err(); err != nil {
		return 0, err
	}
	if len(buffer) > 64*1024 {
		buffer = buffer[:64*1024]
	}
	return reader.source.Read(buffer)
}

func projectHistoricalCodexOperations(ctx context.Context, operations []codexTranscriptOperations, cwd string, mtime int64) map[string]*sessionFileAttribution {
	changes := make(map[string]*sessionFileAttribution)
	agent := SessionFileAgent{Kind: "codex", CWD: cwd}
	for _, item := range operations {
		if ctx.Err() != nil {
			break
		}
		applyCodexTranscriptOperations(changes, agent, mtime, item)
	}
	return changes
}

func historicalCodexChangesTouchRoot(changes map[string]*sessionFileAttribution, root string) bool {
	for path := range changes {
		absolute, err := filepath.Abs(path)
		if err != nil {
			continue
		}
		relative, err := filepath.Rel(root, absolute)
		if err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
			return true
		}
	}
	return false
}

func historicalCodexChangesForRoots(changes map[string]*sessionFileAttribution, roots []string) map[string]*sessionFileAttribution {
	result := make(map[string]*sessionFileAttribution)
	for _, root := range roots {
		for path, attribution := range changes {
			if historicalCodexChangesTouchRoot(map[string]*sessionFileAttribution{path: attribution}, root) {
				result[path] = attribution
			}
		}
	}
	return result
}
