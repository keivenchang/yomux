// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

func isolateSessionFilesTestHome(t *testing.T) {
	t.Helper()
	t.Setenv("HOME", t.TempDir())
}

// indexSessionFileAgents attaches an attribution index that has fully prepared each agent's
// transcript. Production always queries this index; there is no direct transcript scan.
func indexSessionFileAgents(t *testing.T, service *Service, agents ...SessionFileAgent) *AttributionIndex {
	t.Helper()
	index, err := OpenAttributionIndex(t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if closeErr := index.Close(); closeErr != nil {
			t.Error(closeErr)
		}
	})
	for _, agent := range agents {
		_, _, complete, indexErr := index.index(context.Background(), AttributionSource{Path: agent.Transcript, Kind: agent.Kind}, 8<<20, 4096)
		if indexErr != nil || !complete {
			t.Fatalf("index %s: complete=%t err=%v", agent.Transcript, complete, indexErr)
		}
	}
	index.progress = AttributionProgress{State: "ready"}
	service.SetAttributionIndex(index)
	return index
}

func TestCodexPatchAndCommandPathsPreferRecordedWorkdirOverAgentDirectory(t *testing.T) {
	start := filepath.Join(t.TempDir(), "yomux-checkout")
	workdir := filepath.Join(t.TempDir(), "worked-repo")
	changes := make(map[string]*sessionFileAttribution)
	agent := SessionFileAgent{Kind: "codex", CWD: start}
	applyCodexTranscriptOperations(changes, agent, 1, codexTranscriptOperations{
		patches:  []transcriptFileOperation{{path: "patched.txt", marker: "M"}},
		commands: []codexShellCommand{{command: "git add staged.txt", cwd: workdir}},
	})
	applyCodexTranscriptOperations(changes, agent, 1, codexTranscriptOperations{
		patches:  []transcriptFileOperation{{path: "unrecorded.txt", marker: "A"}},
		commands: []codexShellCommand{{command: "true", cwd: "sub"}},
	})
	for _, want := range []string{
		filepath.Join(workdir, "patched.txt"),
		filepath.Join(workdir, "staged.txt"),
		filepath.Join(start, "sub", "unrecorded.txt"),
	} {
		if changes[want] == nil {
			t.Fatalf("attributions = %v, want %s", attributionPaths(changes), want)
		}
	}
	if len(changes) != 3 {
		t.Fatalf("attributions = %v, want exactly three paths", attributionPaths(changes))
	}
}

func attributionPaths(changes map[string]*sessionFileAttribution) []string {
	paths := make([]string, 0, len(changes))
	for path := range changes {
		paths = append(paths, path)
	}
	return paths
}

// Real Codex code-mode "exec" records from ~/.codex/sessions (2026-11-26). Only identifiers,
// timestamps, and absolute paths are redacted; the workdir is substituted per test. The first
// uses a bare workdir key and the second a quoted one, as current Codex writes both.
const codexCodeModeBareWorkdirRecord = `{"timestamp":"2026-11-26T19:11:04.919Z","ordinal":9312,"type":"response_item","payload":{"type":"custom_tool_call","id":"ctc_redacted","status":"completed","call_id":"call_redacted","name":"exec","input":"text(await tools.exec_command({cmd:\"if rg -n '^(<<<<<<<|=======|>>>>>>>)' acceptance/utils/tests/test_ci_coverage.py; then exit 1; fi && git add acceptance/utils/tests/test_ci_coverage.py && GIT_EDITOR=true git -c commit.gpgsign=true rebase --continue && /opt/tools/bin/clean-commit.sh && python3 /opt/tools/bin/git-delivery-preflight.py --delivery-base origin/main && git status --short --branch && git log --oneline --decorate --max-count=4\",workdir:\"__WORKDIR__\",yield_time_ms:1000,max_output_tokens:1000}))\n","internal_chat_message_metadata_passthrough":{"turn_id":"redacted","create_time":1795720261.7341475}},"metadata":{"history_sequence":4076,"sampled_through_sequence":4074,"client_authored":false,"model_search_state":"search_mcp"}}`

const codexCodeModeQuotedWorkdirRecord = `{"timestamp":"2026-11-26T19:32:40.656Z","ordinal":9922,"type":"response_item","payload":{"type":"custom_tool_call","id":"ctc_redacted","status":"completed","call_id":"call_redacted","name":"exec","input":"const r = await tools.exec_command({cmd:\"git diff --check && git add -- acceptance/utils/tests/test_browser_smoke.py && git diff --cached --check && git diff --cached -- acceptance/utils/tests/test_browser_smoke.py && git commit -S --signoff -m \\\"test: fix browser smoke ActionChains import\\\"\",\"workdir\":\"__WORKDIR__\",\"yield_time_ms\":1000,\"max_output_tokens\":2000});\ntext(JSON.stringify(r));\n","internal_chat_message_metadata_passthrough":{"turn_id":"redacted","create_time":1795721558.5467267}},"metadata":{"history_sequence":4346,"sampled_through_sequence":4342,"client_authored":false,"model_search_state":"search_mcp"}}`

func TestCodexCodeModeExecAttributesCommandsLikeExecCommand(t *testing.T) {
	start := filepath.Join(t.TempDir(), "yomux-checkout")
	workdir := filepath.Join(t.TempDir(), "parser-toolkits")
	agent := SessionFileAgent{Kind: "codex", CWD: start}
	for record, want := range map[string]string{
		codexCodeModeBareWorkdirRecord:   filepath.Join(workdir, "acceptance", "utils", "tests", "test_ci_coverage.py"),
		codexCodeModeQuotedWorkdirRecord: filepath.Join(workdir, "acceptance", "utils", "tests", "test_browser_smoke.py"),
	} {
		line := []byte(strings.ReplaceAll(record, "__WORKDIR__", workdir))
		changes := make(map[string]*sessionFileAttribution)
		applyCodexTranscriptOperations(changes, agent, 1, codexFileOperations.Read(nil, line))
		if len(changes) != 1 || changes[want] == nil || changes[want].status != "M" {
			t.Fatalf("code-mode attributions = %v, want only %s", attributionPaths(changes), want)
		}

		// The same command in the older function_call format attributes identically.
		operations := codexFileOperations.Read(nil, line)
		legacy, err := json.Marshal(codexExecRecordForTest(operations.commands[0].command, operations.commands[0].cwd))
		if err != nil {
			t.Fatal(err)
		}
		legacyChanges := make(map[string]*sessionFileAttribution)
		applyCodexTranscriptOperations(legacyChanges, agent, 1, codexFileOperations.Read(nil, legacy))
		if len(legacyChanges) != 1 || legacyChanges[want] == nil || legacyChanges[want].status != changes[want].status {
			t.Fatalf("function_call attributions = %v, want the code-mode result %s", attributionPaths(legacyChanges), want)
		}
	}
}

func TestCodexCodeModeCommandsReadOnlyLiteralArguments(t *testing.T) {
	script := "const dir = '/x';\n" +
		"await tools.exec_command({cmd:`git rm old.txt`, workdir:'/repo/a', yield_time_ms:1000});\n" +
		"await tools.exec_command({cmd, workdir});\n" +
		"await tools.exec_command({cmd:i===2?\"git add a\":\"git add b\", workdir:\"/repo/b\"});\n" +
		"await tools.exec_command({\n  \"cmd\": \"git mv \\\"a b.txt\\\" c.txt\",\n  max_output_tokens: f({x: [1, 2]}),\n  \"workdir\": \"/repo/\\u0063\"\n});\n" +
		"await tools.exec_command(args);\n"
	got := codexCodeModeCommands(script)
	want := []codexShellCommand{
		{command: "git rm old.txt", cwd: "/repo/a"},
		{command: `git mv "a b.txt" c.txt`, cwd: "/repo/c"},
	}
	if len(got) != len(want) {
		t.Fatalf("commands = %#v, want %#v", got, want)
	}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("command %d = %#v, want %#v", index, got[index], want[index])
		}
	}
}

func codexExecRecordForTest(command, workdir string) map[string]any {
	return map[string]any{"type": "response_item", "payload": map[string]any{
		"type": "function_call", "name": "exec_command", "arguments": map[string]any{"cmd": command, "workdir": workdir},
	}}
}
