// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const (
	maxSessionTranscriptLineBytes = 4 * 1024 * 1024
	maxHistoricalCodexFiles       = 256
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
	stat, err := os.Stat(path)
	if err != nil || stat.ModTime().Before(cutoff) {
		return map[string]*sessionFileAttribution{}, false
	}
	file, err := os.Open(path)
	if err != nil {
		return map[string]*sessionFileAttribution{}, false
	}
	defer file.Close()
	changes := make(map[string]*sessionFileAttribution)
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), maxSessionTranscriptLineBytes)
	for scanner.Scan() {
		line := scanner.Bytes()
		switch strings.ToLower(strings.TrimSpace(agent.Kind)) {
		case "claude":
			scanClaudeTranscriptLine(line, agent, stat.ModTime().Unix(), changes)
		case "codex":
			scanCodexTranscriptLine(line, agent, stat.ModTime().Unix(), changes)
		}
	}
	return changes, true
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
	text := string(line)
	for _, match := range codexPatchHeader.FindAllStringSubmatch(text, -1) {
		marker := map[string]string{"Add": "A", "Update": "M", "Delete": "D"}[match[1]]
		recordSessionTranscriptPath(changes, match[2], agent.CWD, agent, marker, mtime)
	}
	var record map[string]any
	if json.Unmarshal(line, &record) != nil {
		return
	}
	payload, _ := record["payload"].(map[string]any)
	toolType := stringValue(payload["type"])
	if toolType != "function_call" && toolType != "custom_tool_call" {
		return
	}
	tool := stringValue(payload["name"])
	if tool != "exec_command" && tool != "shell_command" && tool != "shell" {
		return
	}
	arguments := codexArguments(payload["arguments"])
	command := stringValue(arguments["cmd"])
	if command == "" {
		command = stringValue(arguments["command"])
	}
	cwd := stringValue(arguments["workdir"])
	if cwd == "" {
		cwd = stringValue(arguments["cwd"])
	}
	if cwd == "" {
		cwd = agent.CWD
	}
	for path, markers := range scanShellChanges(command, cwd) {
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
		for touchedPath, attribution := range mustScanSessionTranscript(path, agent, cutoff) {
			mergeSessionFileAttribution(changes, touchedPath, attribution)
		}
	}
	// A session can retain a clean repository after its Codex pane has exited. Match the bounded
	// recent rollout window by cwd so historical edits remain visible without scanning arbitrary old
	// transcripts forever.
	for _, cwd := range sessionFileTranscriptCWDs(agents, roots) {
		if ctx.Err() != nil {
			break
		}
		for _, transcript := range recentHistoricalCodexTranscripts(cwd, cutoff) {
			if seenTranscripts[transcript] {
				continue
			}
			seenTranscripts[transcript] = true
			agent := SessionFileAgent{Kind: "codex", CWD: cwd}
			for touchedPath, attribution := range mustScanSessionTranscript(transcript, agent, cutoff) {
				mergeSessionFileAttribution(changes, touchedPath, attribution)
			}
		}
	}
	return changes
}

func mustScanSessionTranscript(path string, agent SessionFileAgent, cutoff time.Time) map[string]*sessionFileAttribution {
	changes, _ := scanSessionTranscript(path, agent, cutoff)
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
			existing.windows = append(existing.windows, window)
		}
	}
	existing.status = sessionFileStatusMerge(existing.status, incoming.status)
	if incoming.mtime > existing.mtime {
		existing.mtime = incoming.mtime
	}
}

func recentHistoricalCodexTranscripts(cwd string, cutoff time.Time) []string {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil
	}
	root := filepath.Join(home, ".codex", "sessions")
	type candidate struct {
		path  string
		mtime time.Time
	}
	candidates := make([]candidate, 0)
	_ = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil || len(candidates) >= maxHistoricalCodexFiles || entry.IsDir() {
			return nil
		}
		if !strings.HasPrefix(entry.Name(), "rollout-") || !strings.HasSuffix(entry.Name(), ".jsonl") {
			return nil
		}
		info, infoErr := entry.Info()
		if infoErr == nil && !info.ModTime().Before(cutoff) {
			candidates = append(candidates, candidate{path: path, mtime: info.ModTime()})
		}
		return nil
	})
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].mtime.After(candidates[j].mtime) })
	result := make([]string, 0, len(candidates))
	for _, candidate := range candidates {
		agent := SessionFileAgent{Kind: "codex", CWD: cwd}
		changes, available := scanSessionTranscript(candidate.path, agent, cutoff)
		if !available {
			continue
		}
		for path := range changes {
			absolute, _ := filepath.Abs(path)
			relative, relErr := filepath.Rel(cwd, absolute)
			if relErr == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
				result = append(result, candidate.path)
				break
			}
		}
	}
	return result
}
