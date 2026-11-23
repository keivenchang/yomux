// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yolo

import (
	"strconv"
	"strings"
	"testing"
)

func TestClassifyPromptFromRealCaptureShapes(t *testing.T) {
	cases := []struct {
		text        string
		agent       PromptAgent
		promptType  PromptType
		selected    int
		command     string
		optionCount int
	}{
		{
			text:        "Would you like to run the following command?\n$ touch /tmp/yolomux-codex-e2e-approval-20260620-1106\n› 1. Yes, proceed (y)\n  2. Yes, and don't ask again for commands that start with `touch /tmp/yolomux-codex-e2e-approval-20260620-1106` (p)\n  3. No, and tell Codex what to do differently (esc)\nPress enter to confirm or esc to cancel",
			agent:       AgentCodex,
			promptType:  PromptShell,
			selected:    1,
			command:     "touch /tmp/yolomux-codex-e2e-approval-20260620-1106",
			optionCount: 3,
		},
		{
			text:        "Claude has written up a plan and is ready to execute. Would you like to proceed?\n❯ 1. Yes, and use auto mode\n  2. Yes, manually approve edits\n  3. Tell Claude what to change\nshift+tab to approve with this feedback\nctrl+g to edit in VS Code",
			agent:       AgentClaude,
			promptType:  PromptPlan,
			selected:    1,
			optionCount: 3,
		},
	}
	for _, test := range cases {
		t.Run(string(test.promptType), func(t *testing.T) {
			prompt := ClassifyPrompt(test.text, "stale full-pane text is ignored")
			if !prompt.Actionable || !prompt.Supported || !prompt.Approval || prompt.Question {
				t.Fatalf("ClassifyPrompt() = %#v, want an actionable approval", prompt)
			}
			if prompt.Agent != test.agent || prompt.Type != test.promptType || prompt.SelectedOption != test.selected {
				t.Fatalf("ClassifyPrompt() identity = (%q, %q, %d), want (%q, %q, %d)", prompt.Agent, prompt.Type, prompt.SelectedOption, test.agent, test.promptType, test.selected)
			}
			if prompt.Command != test.command || len(prompt.Options) != test.optionCount || prompt.Hash == "" {
				t.Fatalf("ClassifyPrompt() details = %#v, want command %q, %d options, and a hash", prompt, test.command, test.optionCount)
			}
		})
	}
}

func TestClassifyPromptSupportsExplicitClaudeApprovalShapes(t *testing.T) {
	tests := []struct {
		name       string
		text       string
		promptType PromptType
		ruleInput  string
	}{
		{
			name:       "shell",
			text:       "Bash command\n  git status --short\nPermission rule Bash requires confirmation for this command.\nDo you want to proceed?\n ❯ 1. Yes\n   2. No\nEsc to cancel",
			promptType: PromptShell,
			ruleInput:  "git status --short",
		},
		{
			name:       "file",
			text:       "Do you want to make this edit to internal/yolo/prompt.go?\n❯ 1. Yes\n  2. No",
			promptType: PromptFile,
			ruleInput:  "Do you want to make this edit to internal/yolo/prompt.go?",
		},
		{
			name:       "tool",
			text:       "Do you want to allow WebFetch to access https://example.test/docs?\n❯ 1. Yes\n  2. No",
			promptType: PromptTool,
			ruleInput:  "Do you want to allow WebFetch to access https://example.test/docs?",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			prompt := ClassifyPrompt(test.text, "")
			if !prompt.Actionable || prompt.Agent != AgentClaude || prompt.Type != test.promptType || prompt.RuleInput != test.ruleInput {
				t.Fatalf("ClassifyPrompt() = %#v, want actionable Claude %q with input %q", prompt, test.promptType, test.ruleInput)
			}
		})
	}
}

func TestClassifyPromptSupportsExplicitCodexToolApproval(t *testing.T) {
	text := "Codex wants to use an MCP tool\nWould you like to allow mcp__github__get_pull_request to access repo#1?\n› 1. Yes, allow once\n  2. No"
	prompt := ClassifyPrompt(text, "")
	if !prompt.Actionable || prompt.Agent != AgentCodex || prompt.Type != PromptTool || prompt.SelectedOption != 1 {
		t.Fatalf("ClassifyPrompt() = %#v, want actionable Codex tool approval", prompt)
	}
}

func TestClassifyPromptSupportsCroppedCodexShellHeader(t *testing.T) {
	for _, header := range []string{
		"Would you like to run the following comm",
		"Would you like to run the following comman",
		"Would you like to run the following command",
	} {
		t.Run(header, func(t *testing.T) {
			text := header + "\n$ touch /tmp/fixture\n› 1. Yes, proceed\n  2. No\nPress enter to confirm or esc to cancel"
			prompt := ClassifyPrompt(text, "")
			if !prompt.Actionable || prompt.Agent != AgentCodex || prompt.Command != "touch /tmp/fixture" {
				t.Fatalf("ClassifyPrompt(%q) = %#v, want actionable Codex shell prompt with visible command", header, prompt)
			}
		})
	}
}

func TestClassifyPromptDoesNotJoinStaleShellApprovalToLaterMenu(t *testing.T) {
	for _, intervening := range []string{
		"● Ran git push origin feature\n  ⎿ remote updated\n\nSelect action:",
		"The command completed successfully.\n\nAvailable actions:",
	} {
		text := "Would you like to run the following command?\n$ git push --force origin feature\n" + intervening + "\n› 1. Yes, proceed\n  2. No\nEnter to select"
		prompt := ClassifyPrompt(text, "")
		if prompt.Actionable || prompt.Supported {
			t.Fatalf("ClassifyPrompt() = %#v, stale shell approval must not authorize later content %q", prompt, intervening)
		}
	}
}

func TestClassifyPromptAllowsStructuredCodexReasonBeforeCommand(t *testing.T) {
	text := "Would you like to run the following command?\n\nEnvironment: local\n\nReason:\n  This command reads the local test fixture.\n$ cat ./fixture.txt\n› 1. Yes, proceed\n  2. No\nPress enter to confirm or esc to cancel"
	prompt := ClassifyPrompt(text, "")
	if !prompt.Actionable || prompt.Command != "cat ./fixture.txt" {
		t.Fatalf("ClassifyPrompt() = %#v, want an actionable shell prompt with a structured reason and visible command", prompt)
	}
}

func TestClassifyPromptJoinsCodexCommandRows(t *testing.T) {
	header := "Would you like to run the following command?\n\nEnvironment: local\n\nReason: Inspect the monitor process.\n\n$ "
	menu := "\n\n› 1. Yes, proceed (y)\n  2. No, and tell Codex what to do differently (esc)\n\nPress enter to confirm or esc to cancel"
	rows := "python3 ~/scripts/inspect_process.py\n3178740 --expect-cwd\n/home/keivenc/dev/parser-toolkits --expect-argv /tmp/monitor.py"
	command := strings.ReplaceAll(rows, "\n", " ")
	wrapped := ClassifyPrompt(header+rows+menu, "")
	oneLine := ClassifyPrompt(header+command+menu, "")
	if !wrapped.Actionable || wrapped.Command != command || wrapped.RuleInput != command || wrapped.Hash == oneLine.Hash {
		t.Fatalf("wrapped = %#v, single line = %#v, want the same command with distinct row identities", wrapped, oneLine)
	}
	separated := ClassifyPrompt(header+strings.Replace(rows, "\n", "\n\n", 1)+menu, "")
	if separated.Actionable {
		t.Fatalf("separated = %#v, must not approve a command prefix across a blank line", separated)
	}
}

func TestCodexPromptIdentityPreservesRowBytes(t *testing.T) {
	header := "Would you like to run the following command?\nEnvironment: local\n$ "
	menu := "\n› 1. Yes, proceed (y)\n  2. No (esc)\nPress enter to confirm or esc to cancel"
	rows := "echo harmless\nrm -rf /"
	multi := ClassifyPrompt(header+rows+menu, "")
	for _, command := range []string{strconv.Quote(rows), strings.ReplaceAll(rows, "\n", " "), rows + " "} {
		other := ClassifyPrompt(header+command+menu, "")
		if !other.Actionable || multi.Hash == other.Hash {
			t.Fatalf("row identity collision: %+v and %+v", multi, other)
		}
	}
}

func TestClassifyPromptRequiresCodexShellConfirmationFooter(t *testing.T) {
	text := "Would you like to run the following command?\n$ git push origin feature\n› 1. Yes, proceed\n  2. No\nEnter to select"
	prompt := ClassifyPrompt(text, "")
	if prompt.Actionable || prompt.Supported {
		t.Fatalf("ClassifyPrompt() = %#v, a generic menu footer must not complete a Codex shell prompt", prompt)
	}
}

func TestClassifyPromptFailsClosedOnMissingSelectionAndStaleOutput(t *testing.T) {
	tests := []struct {
		name string
		text string
	}{
		{
			name: "no selected option",
			text: "Would you like to run the following command?\n$ echo safe\n  1. Yes, proceed\n  2. No",
		},
		{
			name: "successful output after approval",
			text: "Would you like to run the following command?\n$ echo done\n› 1. Yes, proceed\n  2. No\n● Ran echo done\n  ⎿ done",
		},
		{
			name: "working output after approval",
			text: "Would you like to run the following command?\n$ echo done\n› 1. Yes, proceed\n  2. No\n◦ Running echo done",
		},
		{
			name: "visible pane empty despite scrollback prompt",
			text: "",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			prompt := ClassifyPrompt(test.text, "Would you like to run the following command?\n$ rm -rf /\n› 1. Yes\n  2. No")
			if prompt.Actionable || (prompt.Approval && prompt.Supported) {
				t.Fatalf("ClassifyPrompt() = %#v, must fail closed", prompt)
			}
		})
	}
}

func TestClassifyPromptSeparatesQuestionsFromApprovals(t *testing.T) {
	text := "Which backend should I use for this verification?\n❯ 1. vSRV\n  2. QKServ\nEnter to select · ↑/↓ to navigate · Esc to cancel"
	prompt := ClassifyPrompt(text, "")
	if !prompt.Question || prompt.Approval || prompt.Actionable || prompt.Type != PromptQuestion || prompt.SelectedOption != 1 {
		t.Fatalf("ClassifyPrompt() = %#v, want a non-actionable question", prompt)
	}
}

func TestClassifyPromptRejectsUnqualifiedClaudeProceedQuestion(t *testing.T) {
	prompt := ClassifyPrompt("Do you want to proceed?\n❯ 1. Yes\n  2. No", "")
	if prompt.Actionable || prompt.Type != PromptUnknown {
		t.Fatalf("ClassifyPrompt() = %#v, want unsupported prompt", prompt)
	}
}

func TestClassifyPromptDoesNotGuessClientForGenericPlanPrompt(t *testing.T) {
	prompt := ClassifyPrompt("Would you like to proceed with this plan?\n› 1. Yes\n  2. No", "")
	if prompt.Approval || prompt.Actionable {
		t.Fatalf("ClassifyPrompt() = %#v, must not infer an approval client", prompt)
	}
}
