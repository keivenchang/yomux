// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/keivenchang/yomux/internal/tailfile"
)

const maxSessionTranscriptLineBytes = 4 * 1024 * 1024

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

var claudeFileOperations = tailfile.RegisterProjection(func(_ []byte, record map[string]any) []transcriptFileOperation {
	if stringValue(record["type"]) != "assistant" {
		return nil
	}
	var operations []transcriptFileOperation
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
		operations = append(operations, transcriptFileOperation{path: stringValue(input["file_path"]), marker: marker})
	}
	return operations
})

func applyClaudeFileOperations(changes map[string]*sessionFileAttribution, agent SessionFileAgent, mtime int64, operations []transcriptFileOperation) {
	for _, operation := range operations {
		recordSessionTranscriptPath(changes, operation.path, agent.CWD, agent, operation.marker, mtime)
	}
}

type transcriptFileOperation struct {
	path   string
	marker string
}

type codexTranscriptOperations struct {
	recordedCWD string
	patches     []transcriptFileOperation
	commands    []codexShellCommand
}

// codexShellCommand is one shell command and the workdir Codex recorded for it, if any.
type codexShellCommand struct {
	command string
	cwd     string
}

var codexFileOperations = tailfile.RegisterProjection(func(line []byte, record map[string]any) codexTranscriptOperations {
	operations := codexTranscriptOperations{}
	for _, match := range codexPatchHeader.FindAllStringSubmatch(string(line), -1) {
		marker := map[string]string{"Add": "A", "Update": "M", "Delete": "D"}[match[1]]
		operations.patches = append(operations.patches, transcriptFileOperation{path: strings.Clone(match[2]), marker: marker})
	}
	payload, _ := record["payload"].(map[string]any)
	if stringValue(record["type"]) == "session_meta" {
		operations.recordedCWD = strings.TrimSpace(stringValue(payload["cwd"]))
	}
	toolType := stringValue(payload["type"])
	if toolType != "function_call" && toolType != "custom_tool_call" {
		return operations
	}
	tool := stringValue(payload["name"])
	if tool == "exec" && toolType == "custom_tool_call" {
		operations.commands = codexCodeModeCommands(stringValue(payload["input"]))
		return operations
	}
	if tool != "exec_command" && tool != "shell_command" && tool != "shell" {
		return operations
	}
	arguments := codexArguments(payload["arguments"])
	command := newCodexShellCommand(func(key string) string { return stringValue(arguments[key]) })
	if command.command != "" {
		operations.commands = append(operations.commands, command)
	}
	return operations
})

// newCodexShellCommand reads one command from Codex tool arguments, which name the command
// "cmd" or "command" and its directory "workdir" or "cwd" depending on the tool.
func newCodexShellCommand(field func(string) string) codexShellCommand {
	command := codexShellCommand{command: field("cmd"), cwd: field("workdir")}
	if command.command == "" {
		command.command = field("command")
	}
	if command.cwd == "" {
		command.cwd = field("cwd")
	}
	return command
}

// codexCodeModeCommands reads the literal arguments of each tools.exec_command({...}) call in a
// Codex code-mode "exec" script. Only string and template literals are read; a value computed
// by script code (a variable or expression) cannot be known from the transcript and is skipped.
func codexCodeModeCommands(script string) []codexShellCommand {
	const call = "tools.exec_command("
	commands := make([]codexShellCommand, 0)
	for offset := 0; ; {
		start := strings.Index(script[offset:], call)
		if start < 0 {
			return commands
		}
		offset += start + len(call)
		fields, end := codexCodeModeObject(script, offset)
		offset = end
		command := newCodexShellCommand(func(key string) string { return fields[key] })
		if command.command != "" {
			commands = append(commands, command)
		}
	}
}

// codexCodeModeObject parses the object literal at offset and returns its literal-valued fields
// and the offset after it. Keys may be bare identifiers or quoted strings.
func codexCodeModeObject(script string, offset int) (map[string]string, int) {
	fields := make(map[string]string)
	index := codexSkipSpace(script, offset)
	if index >= len(script) || script[index] != '{' {
		return fields, index
	}
	index++
	for {
		index = codexSkipSpace(script, index)
		for index < len(script) && script[index] == ',' {
			index = codexSkipSpace(script, index+1)
		}
		if index >= len(script) {
			return fields, index
		}
		if script[index] == '}' {
			return fields, index + 1
		}
		key := ""
		if quote := script[index]; quote == '"' || quote == '\'' {
			key, index = codexStringLiteral(script, index)
		} else {
			keyStart := index
			for index < len(script) && (script[index] == '_' || script[index] == '$' || script[index] >= 'a' && script[index] <= 'z' || script[index] >= 'A' && script[index] <= 'Z' || script[index] >= '0' && script[index] <= '9') {
				index++
			}
			key = script[keyStart:index]
			if key == "" {
				// Not an object literal this reader understands, such as a spread.
				return fields, codexSkipExpression(script, index)
			}
		}
		index = codexSkipSpace(script, index)
		if index >= len(script) || script[index] != ':' {
			continue
		}
		index = codexSkipSpace(script, index+1)
		valueStart := index
		if index < len(script) && (script[index] == '"' || script[index] == '\'' || script[index] == '`') {
			value, valueEnd := codexStringLiteral(script, index)
			if next := codexSkipSpace(script, valueEnd); next < len(script) && (script[next] == ',' || script[next] == '}') {
				fields[key] = value
				index = valueEnd
				continue
			}
		}
		index = codexSkipExpression(script, valueStart)
	}
}

func codexSkipSpace(script string, index int) int {
	for index < len(script) && strings.ContainsRune(" \t\r\n", rune(script[index])) {
		index++
	}
	return index
}

// codexSkipExpression advances to the next top-level ',' or '}' of the enclosing object.
func codexSkipExpression(script string, index int) int {
	depth := 0
	for index < len(script) {
		switch character := script[index]; character {
		case '"', '\'', '`':
			_, index = codexStringLiteral(script, index)
			continue
		case '(', '[', '{':
			depth++
		case ')', ']', '}':
			if depth == 0 {
				return index
			}
			depth--
		case ',':
			if depth == 0 {
				return index
			}
		}
		index++
	}
	return index
}

// codexStringLiteral decodes the JavaScript string or template literal at index and returns the
// offset after its closing quote. Template substitutions are kept as their ${...} source text,
// which path resolution already refuses to interpret.
func codexStringLiteral(script string, index int) (string, int) {
	quote := script[index]
	var value strings.Builder
	for index++; index < len(script); index++ {
		character := script[index]
		if character == quote {
			return value.String(), index + 1
		}
		if character != '\\' || index+1 >= len(script) {
			value.WriteByte(character)
			continue
		}
		index++
		switch escaped := script[index]; escaped {
		case 'n':
			value.WriteByte('\n')
		case 't':
			value.WriteByte('\t')
		case 'r':
			value.WriteByte('\r')
		case 'u':
			if index+4 < len(script) {
				if code, err := strconv.ParseUint(script[index+1:index+5], 16, 32); err == nil {
					value.WriteRune(rune(code))
					index += 4
					continue
				}
			}
			value.WriteByte(escaped)
		case '\n':
			// A backslash-newline continues the literal without adding a character.
		default:
			value.WriteByte(escaped)
		}
	}
	return value.String(), index
}

func applyCodexTranscriptOperations(changes map[string]*sessionFileAttribution, agent SessionFileAgent, mtime int64, operations codexTranscriptOperations) {
	// A command's recorded workdir is where its relative paths were applied; the transcript
	// header directory is only the agent's start directory and is used when none was recorded.
	commandDirectory := func(command codexShellCommand) string {
		if recorded := resolveSessionTranscriptPath(command.cwd, agent.CWD); recorded != "" {
			return recorded
		}
		return agent.CWD
	}
	// Patch text carried inside a single command applies in that command's workdir. A code-mode
	// script with several commands has no single workdir for its patches.
	patchDirectory := agent.CWD
	if len(operations.commands) == 1 {
		patchDirectory = commandDirectory(operations.commands[0])
	}
	for _, patch := range operations.patches {
		recordSessionTranscriptPath(changes, patch.path, patchDirectory, agent, patch.marker, mtime)
	}
	for _, command := range operations.commands {
		cwd := commandDirectory(command)
		for path, markers := range scanShellChanges(command.command, cwd) {
			for _, marker := range markers {
				recordSessionTranscriptPath(changes, path, cwd, agent, marker, mtime)
			}
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

// HistoricalAttributionRoot is the Codex archive the attribution index pre-indexes so a live
// agent's transcript is usually prepared before Differ first asks for it.
func HistoricalAttributionRoot() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".codex", "sessions"), nil
}
