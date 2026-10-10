// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yolo

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strings"
)

type PromptAgent string

const (
	AgentClaude  PromptAgent = "claude"
	AgentCodex   PromptAgent = "codex"
	AgentUnknown PromptAgent = "unknown"
)

type PromptType string

const (
	PromptShell    PromptType = "shell"
	PromptFile     PromptType = "file"
	PromptTool     PromptType = "tool"
	PromptPlan     PromptType = "plan"
	PromptQuestion PromptType = "question"
	PromptUnknown  PromptType = "unknown"
)

type PromptOption struct {
	Index    int
	Label    string
	Selected bool
}

// Prompt is a read-only classification of the current visible pane. Actionable
// reports only explicit, supported approvals with a visible selected option.
type Prompt struct {
	Agent          PromptAgent
	Type           PromptType
	Hash           string
	Command        string
	RuleInput      string
	SelectedOption int
	Options        []PromptOption
	Approval       bool
	Question       bool
	Supported      bool
	Actionable     bool
	Reason         string
}

type promptCandidate struct {
	index int
	type_ PromptType
	agent PromptAgent
	text  string
}

var (
	ansiPromptRE        = regexp.MustCompile("\\x1b(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~])")
	controlPromptRE     = regexp.MustCompile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]")
	optionPromptRE      = regexp.MustCompile(`^\s*([❯›>]?)\s*(\d+)[.:]\s+(.+?)\s*$`)
	claudeShellPromptRE = regexp.MustCompile(`(?i)^\s*(?:[●•]\s*)?Do you want to proceed\?\s*$`)
	claudeFilePromptRE  = regexp.MustCompile(`(?i)Do you want to (?:make this )?(?:edit|create|overwrite|replace|rename|move)\b[^?]*\?|Would you like to make the following edits\?`)
	claudePlanPromptRE  = regexp.MustCompile(`(?i)(?:Do you want to (?:proceed with|approve|accept|use) (?:this )?plan\?|Would you like (?:me|Claude) to (?:start|proceed|make changes)(?: with this plan)?\?|Claude has written up a plan\b.*Would you like to proceed\?|Ready to (?:start|make changes)\?)`)
	claudeToolPromptRE  = regexp.MustCompile(`(?i)Do you want to allow\s+\S+`)
	codexShellPromptRE  = regexp.MustCompile(`(?i)Would you like to run the following comm(?:an(?:d)?)?\??`)
	codexShellHeaderRE  = regexp.MustCompile(`(?i)^\s*Codex wants to run a shell command\s*$`)
	codexToolHeaderRE   = regexp.MustCompile(`(?i)^\s*Codex wants to use an MCP tool\s*$`)
	codexToolPromptRE   = regexp.MustCompile(`(?i)Would you like to allow\s+\S+`)
	questionPromptRE    = regexp.MustCompile(`(?i)^\s*(?:What|Which|Who|Where|When|Why|How|Should|Would|Could|Can|Do|Does|Did|Is|Are|Will)\b.*\?\s*$`)
	claudeCommandRE     = regexp.MustCompile(`\s-[A-Za-z]`)
)

// ClassifyPrompt recognizes supported approval layouts from visibleText only.
// fullPaneText is intentionally not used: scrollback can contain stale command
// or prompt text, so missing visible evidence must stay non-actionable.
func ClassifyPrompt(visibleText, fullPaneText string) Prompt {
	_ = fullPaneText
	text := normalizePromptText(visibleText)
	lines := strings.Split(text, "\n")
	candidate := lastPromptCandidate(lines)
	if candidate == nil {
		return Prompt{Agent: AgentUnknown, Type: PromptUnknown, Reason: "no supported prompt header is visible"}
	}
	options, lastOptionLine, selected := promptOptions(lines, candidate.index)
	prompt := Prompt{Agent: candidate.agent, Type: candidate.type_, Options: options, SelectedOption: selected}
	if candidate.agent == AgentCodex && candidate.type_ == PromptShell && codexShellPromptHasBoundaryBeforeOptions(lines, candidate.index) {
		prompt.Type = PromptUnknown
		prompt.Reason = "visible output or another menu separates the shell prompt from its options"
		return prompt
	}
	if candidate.agent == AgentCodex && candidate.type_ == PromptShell && !hasCodexShellConfirmationFooter(lines, candidate.index) {
		prompt.Type = PromptUnknown
		prompt.Reason = "Codex shell confirmation footer is not visible"
		return prompt
	}
	if candidate.type_ == PromptQuestion {
		if hasLaterActivity(lines, lastOptionLine) {
			prompt.Type = PromptUnknown
			prompt.Reason = "visible output or working activity follows the question"
			return prompt
		}
		prompt.Question = true
		prompt.Supported = true
		prompt.RuleInput = candidate.text
		prompt.Hash = promptHash(candidate.agent, candidate.type_, candidate.text, "", options)
		prompt.Reason = "question prompts are never approvals"
		return prompt
	}
	prompt.Approval = true
	if hasLaterActivity(lines, lastOptionLine) {
		prompt.Type = PromptUnknown
		prompt.Approval = false
		prompt.Reason = "visible output or working activity follows the prompt"
		return prompt
	}
	if len(options) == 0 {
		prompt.Reason = "approval prompt has no supported option list"
		return prompt
	}
	if selected == 0 {
		prompt.Reason = "approval prompt has no explicitly selected option"
		return prompt
	}
	prompt.Supported = true
	prompt.RuleInput = candidate.text
	if candidate.type_ == PromptShell {
		prompt.Command = commandForPrompt(lines, candidate)
		if prompt.Command == "" {
			prompt.Supported = false
			prompt.Reason = "shell approval command is not fully visible"
			return prompt
		}
		prompt.RuleInput = prompt.Command
	}
	prompt.Hash = promptHash(candidate.agent, candidate.type_, candidate.text, prompt.RuleInput, options)
	prompt.Actionable = prompt.Supported && prompt.Approval && selected > 0
	if !prompt.Actionable {
		prompt.Reason = "approval evidence is incomplete"
	}
	return prompt
}

func hasCodexShellConfirmationFooter(lines []string, promptIndex int) bool {
	for _, line := range lines[promptIndex+1:] {
		lower := strings.ToLower(strings.TrimSpace(line))
		if strings.HasPrefix(lower, "press enter to confirm") || strings.HasPrefix(lower, "press y to approve") {
			return true
		}
	}
	return false
}

func codexShellPromptHasBoundaryBeforeOptions(lines []string, promptIndex int) bool {
	seenCommand := false
	seenReason := false
	for index := promptIndex + 1; index < len(lines); index++ {
		rawLine := lines[index]
		line := strings.TrimSpace(rawLine)
		if optionPromptRE.MatchString(rawLine) {
			return !seenCommand
		}
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "$ ") {
			if seenCommand {
				return true
			}
			seenCommand = true
			continue
		}
		if !seenCommand && strings.HasPrefix(strings.ToLower(line), "reason:") {
			seenReason = true
			continue
		}
		if !seenCommand && seenReason && strings.TrimLeft(rawLine, " \t") != rawLine {
			continue
		}
		// Codex captures join terminal wraps. Unknown rows before the choices are
		// therefore a different UI block or completed output, not command text.
		return true
	}
	return !seenCommand
}

func normalizePromptText(text string) string {
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	text = ansiPromptRE.ReplaceAllString(text, "")
	return controlPromptRE.ReplaceAllString(text, "")
}

func lastPromptCandidate(lines []string) *promptCandidate {
	var found *promptCandidate
	codexShellHeader := -1
	codexToolHeader := -1
	for index, line := range lines {
		trimmed := strings.TrimSpace(line)
		lower := strings.ToLower(trimmed)
		if codexShellHeaderRE.MatchString(line) {
			codexShellHeader = index
		}
		if codexToolHeaderRE.MatchString(line) {
			codexToolHeader = index
		}
		switch {
		case codexShellPromptRE.MatchString(line):
			found = &promptCandidate{index: index, type_: PromptShell, agent: AgentCodex, text: trimmed}
		case claudePlanPromptRE.MatchString(line) && hasClaudePlanIdentity(lines, index):
			found = &promptCandidate{index: index, type_: PromptPlan, agent: AgentClaude, text: trimmed}
		case claudeFilePromptRE.MatchString(line):
			found = &promptCandidate{index: index, type_: PromptFile, agent: AgentClaude, text: trimmed}
		case claudeShellPromptRE.MatchString(line):
			if containsClaudeShellEvidence(lines, index) {
				found = &promptCandidate{index: index, type_: PromptShell, agent: AgentClaude, text: trimmed}
			}
		case claudeToolPromptRE.MatchString(line) && codexShellHeader < 0 && codexToolHeader < 0:
			found = &promptCandidate{index: index, type_: PromptTool, agent: AgentClaude, text: trimmed}
		case codexToolPromptRE.MatchString(line) && codexToolHeader >= 0:
			found = &promptCandidate{index: index, type_: PromptTool, agent: AgentCodex, text: trimmed}
		case questionPromptRE.MatchString(line):
			found = &promptCandidate{index: index, type_: PromptQuestion, agent: inferPromptAgent(lines, index), text: trimmed}
		case strings.Contains(lower, "would you like to run the following comm"):
			// A cropped but incomplete Codex shell header is recognized as an
			// unsupported candidate; command text and selection still gate use.
			found = &promptCandidate{index: index, type_: PromptShell, agent: AgentCodex, text: trimmed}
		}
	}
	return found
}

func containsClaudeShellEvidence(lines []string, promptIndex int) bool {
	start := promptIndex - 1
	for start >= 0 && promptIndex-start < 16 {
		line := strings.TrimSpace(lines[start])
		if strings.HasPrefix(line, "Permission rule Bash") || line == "Bash command" || line == "Bash command (unsandboxed)" {
			return true
		}
		if strings.HasPrefix(line, "────") || strings.HasPrefix(line, "●") || strings.HasPrefix(line, "•") {
			break
		}
		start--
	}
	return false
}

func hasClaudePlanIdentity(lines []string, promptIndex int) bool {
	start := promptIndex - 8
	if start < 0 {
		start = 0
	}
	for _, line := range lines[start : promptIndex+1] {
		if strings.Contains(strings.ToLower(line), "claude") {
			return true
		}
	}
	return false
}

func inferPromptAgent(lines []string, promptIndex int) PromptAgent {
	start := promptIndex - 8
	if start < 0 {
		start = 0
	}
	for _, line := range lines[start : promptIndex+1] {
		if codexShellHeaderRE.MatchString(line) || codexToolHeaderRE.MatchString(line) || strings.Contains(strings.ToLower(line), "codex") {
			return AgentCodex
		}
	}
	return AgentClaude
}

func promptOptions(lines []string, start int) ([]PromptOption, int, int) {
	options := make([]PromptOption, 0, 3)
	lastOptionLine := -1
	selected := 0
	for index := start + 1; index < len(lines); index++ {
		line := lines[index]
		match := optionPromptRE.FindStringSubmatch(line)
		if match == nil {
			if len(options) > 0 && isPromptFooter(line) {
				lastOptionLine = index
				continue
			}
			if len(options) > 0 && hasUnclosedBacktick(options[len(options)-1].Label) && isOptionContinuation(line) {
				last := &options[len(options)-1]
				last.Label += " " + strings.TrimSpace(line)
				lastOptionLine = index
				continue
			}
			if len(options) > 0 && strings.TrimSpace(line) == "" {
				continue
			}
			if len(options) > 0 && isPromptFooter(line) {
				lastOptionLine = index
				continue
			}
			if len(options) > 0 {
				break
			}
			continue
		}
		indexValue := 0
		for _, digit := range match[2] {
			indexValue = indexValue*10 + int(digit-'0')
		}
		isSelected := match[1] != ""
		options = append(options, PromptOption{Index: indexValue, Label: strings.TrimSpace(match[3]), Selected: isSelected})
		if isSelected {
			if selected != 0 {
				selected = -1
			} else {
				selected = indexValue
			}
		}
		lastOptionLine = index
	}
	if selected < 0 {
		selected = 0
	}
	return options, lastOptionLine, selected
}

func hasUnclosedBacktick(text string) bool {
	count := 0
	for _, char := range text {
		if char == '`' {
			count++
		}
	}
	return count%2 != 0
}

func isOptionContinuation(line string) bool {
	trimmed := strings.TrimSpace(line)
	return trimmed != "" && !strings.HasPrefix(trimmed, "Press enter to confirm") && !strings.HasPrefix(trimmed, "Esc to cancel") && !strings.HasPrefix(trimmed, "❯") && !strings.HasPrefix(trimmed, "›") && !strings.HasPrefix(trimmed, "●") && !strings.HasPrefix(trimmed, "•") && !strings.HasPrefix(trimmed, "◦") && !strings.HasPrefix(trimmed, "✻") && !strings.HasPrefix(trimmed, "✢") && !strings.HasPrefix(trimmed, "⎿") && !optionPromptRE.MatchString(line)
}

func isPromptFooter(line string) bool {
	trimmed := strings.TrimSpace(line)
	lower := strings.ToLower(trimmed)
	return strings.HasPrefix(lower, "press enter to confirm") || strings.HasPrefix(lower, "esc to cancel") || strings.HasPrefix(lower, "enter to select") || strings.HasPrefix(lower, "shift+tab to") || strings.HasPrefix(lower, "ctrl+g to") || strings.HasPrefix(lower, "ctrl+g ")
}

func hasLaterActivity(lines []string, lastOptionLine int) bool {
	if lastOptionLine < 0 {
		return false
	}
	for _, line := range lines[lastOptionLine+1:] {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || isPromptFooter(line) {
			continue
		}
		lower := strings.ToLower(trimmed)
		if strings.HasPrefix(trimmed, "⎿") || strings.HasPrefix(trimmed, "●") || strings.HasPrefix(trimmed, "•") || strings.HasPrefix(trimmed, "◦") || strings.HasPrefix(trimmed, "✻") || strings.HasPrefix(lower, "running ") || strings.HasPrefix(lower, "ran ") || strings.HasPrefix(lower, "edited ") || strings.HasPrefix(lower, "read ") || strings.HasPrefix(lower, "wrote ") {
			return true
		}
		// An unrecognized non-footer row after a menu makes the visible state
		// ambiguous, so it cannot authorize a send.
		return true
	}
	return false
}

func commandForPrompt(lines []string, candidate *promptCandidate) string {
	if candidate.agent == AgentCodex {
		for index := candidate.index + 1; index < len(lines) && index <= candidate.index+12; index++ {
			trimmed := strings.TrimSpace(lines[index])
			if strings.HasPrefix(trimmed, "$ ") {
				parts := []string{strings.TrimSpace(strings.TrimPrefix(trimmed, "$ "))}
				for next := index + 1; next < len(lines); next++ {
					if optionPromptRE.MatchString(lines[next]) || isPromptFooter(lines[next]) {
						break
					}
					if continuation := strings.TrimSpace(lines[next]); continuation != "" {
						parts = append(parts, continuation)
					}
				}
				return strings.Join(parts, " ")
			}
			if optionPromptRE.MatchString(lines[index]) {
				break
			}
		}
		return ""
	}
	start := 0
	for index := candidate.index - 1; index >= 0 && candidate.index-index < 16; index-- {
		trimmed := strings.TrimSpace(lines[index])
		if strings.HasPrefix(trimmed, "Bash command") {
			start = index + 1
			break
		}
		if strings.HasPrefix(trimmed, "────") || strings.HasPrefix(trimmed, "●") || strings.HasPrefix(trimmed, "•") {
			start = index + 1
			break
		}
	}
	for index := start; index < candidate.index; index++ {
		line := strings.TrimSpace(lines[index])
		if strings.HasPrefix(line, "● Bash(") || strings.HasPrefix(line, "• Bash(") {
			open := strings.IndexByte(line, '(')
			close := strings.LastIndexByte(line, ')')
			if close > open {
				return strings.TrimSpace(line[open+1 : close])
			}
		}
	}
	var parts []string
	for index := start; index < candidate.index; index++ {
		line := strings.TrimSpace(lines[index])
		if line == "" || strings.HasPrefix(line, "Permission rule") || strings.HasPrefix(line, "Bash command") || strings.HasPrefix(line, "Running") || strings.HasPrefix(line, "Do you want") {
			continue
		}
		if strings.ContainsAny(line, "/|&;$=(>`~") || strings.Contains(line, "--") || claudeCommandRE.MatchString(line) {
			parts = append(parts, line)
		}
	}
	return strings.Join(parts, " ")
}

func promptHash(agent PromptAgent, promptType PromptType, question, ruleInput string, options []PromptOption) string {
	var parts []string
	parts = append(parts, string(agent), string(promptType), strings.TrimSpace(question), strings.TrimSpace(ruleInput))
	for _, option := range options {
		parts = append(parts, strings.TrimSpace(option.Label))
	}
	sum := sha256.Sum256([]byte(strings.Join(parts, "\n")))
	return hex.EncodeToString(sum[:])
}
