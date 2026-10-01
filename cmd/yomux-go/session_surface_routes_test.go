// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/stats"
	_ "github.com/mattn/go-sqlite3"
)

func TestReadContextItemsMatchesClaudeAndCodexWireShapes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	records := []map[string]any{
		{"type": "user", "timestamp": "2026-09-17T10:00:00Z", "cwd": "/repo", "message": map[string]any{"role": "user", "content": "inspect this"}},
		{"type": "assistant", "timestamp": "2026-09-17T10:00:01Z", "cwd": "/repo", "message": map[string]any{"role": "assistant", "content": []any{map[string]any{"type": "text", "text": "I will inspect it."}, map[string]any{"type": "tool_use", "name": "Read", "input": map[string]any{"path": "main.go"}}}}},
		{"type": "response_item", "timestamp": "2026-09-17T10:00:02Z", "payload": map[string]any{"type": "function_call_output", "output": "package main"}},
	}
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	encoder := json.NewEncoder(file)
	for _, record := range records {
		if err := encoder.Encode(record); err != nil {
			t.Fatal(err)
		}
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	items, err := readContextItems(path, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 4 {
		t.Fatalf("context items = %#v", items)
	}
	if items[0]["role"] != "user" || items[1]["role"] != "assistant" || items[2]["role"] != "tool_use" || items[3]["role"] != "tool_result" {
		t.Fatalf("context roles = %#v", items)
	}
}

func TestReadContextItemsUsesMessageRoleFallback(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	if err := os.WriteFile(path, []byte(`{"type":"message","message":{"content":"fallback role"}}
`), 0o600); err != nil {
		t.Fatal(err)
	}
	items, err := readContextItems(path, 20)
	if err != nil || len(items) != 1 || items[0]["role"] != "message" {
		t.Fatalf("message role fallback = %#v, err=%v", items, err)
	}
}

func TestTranscriptParserKeepsCodexLifecycleAndPatchItems(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	records := []map[string]any{
		{"type": "event_msg", "timestamp": "2026-09-17T10:00:03Z", "payload": map[string]any{"type": "task_started", "turn_id": "turn-1"}},
		{"type": "event_msg", "timestamp": "2026-09-17T10:00:04Z", "payload": map[string]any{"type": "patch_apply_end", "stdout": "changed", "stderr": ""}},
		{"type": "event_msg", "timestamp": "2026-09-17T10:00:05Z", "payload": map[string]any{"type": "task_complete", "last_agent_message": "done"}},
	}
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	encoder := json.NewEncoder(file)
	for _, record := range records {
		if err := encoder.Encode(record); err != nil {
			t.Fatal(err)
		}
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	items, err := readContextItems(path, 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 3 || items[0]["role"] != "task_started" || items[1]["role"] != "tool_result" || items[2]["role"] != "task_complete" {
		t.Fatalf("lifecycle items = %#v", items)
	}
}

func TestTranscriptQueryLimitMatchesPythonValidationAndClamp(t *testing.T) {
	checks := []struct {
		name       string
		query      string
		wantStatus int
		wantValue  int
	}{
		{name: "default", query: "/api/context", wantStatus: http.StatusOK, wantValue: 40},
		{name: "clamp", query: "/api/context?messages=999", wantStatus: http.StatusOK, wantValue: maxCompactTranscriptItems},
		{name: "invalid", query: "/api/context?messages=bad", wantStatus: http.StatusBadRequest},
		{name: "below minimum", query: "/api/context?messages=0", wantStatus: http.StatusBadRequest},
	}
	for _, check := range checks {
		check := check
		t.Run(check.name, func(t *testing.T) {
			record := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodGet, check.query, nil)
			value, ok := transcriptQueryLimit(record, request, "messages", 40, maxCompactTranscriptItems)
			if record.Code != check.wantStatus || (check.wantStatus == http.StatusOK && (!ok || value != check.wantValue)) {
				t.Fatalf("limit = (%d, %t), response=%d %s", value, ok, record.Code, record.Body.String())
			}
		})
	}
}

func TestChooseSessionTranscriptStaysOnRequestedTmuxWindow(t *testing.T) {
	agents := []finder.SessionFileAgent{
		{Kind: "claude", PaneTarget: "%1", Window: "0", WindowIndex: 0, Transcript: "/tmp/window-0.jsonl"},
		{Kind: "claude", PaneTarget: "%2", Window: "1", WindowIndex: 1, Transcript: "/tmp/window-1.jsonl"},
	}
	selected, reason := chooseSessionTranscript(agents, "%2", "", "%1")
	if selected == nil || selected.Transcript != "/tmp/window-1.jsonl" || reason != "" {
		t.Fatalf("explicit pane selection = %#v, reason=%q", selected, reason)
	}
	selected, reason = chooseSessionTranscript(agents, "", "1", "%1")
	if selected == nil || selected.PaneTarget != "%2" || reason != "" {
		t.Fatalf("explicit window selection = %#v, reason=%q", selected, reason)
	}
	selected, reason = chooseSessionTranscript(agents, "", "", "%2")
	if selected == nil || selected.PaneTarget != "%2" || reason != "" {
		t.Fatalf("active pane selection = %#v, reason=%q", selected, reason)
	}
	withoutTranscript := append([]finder.SessionFileAgent(nil), agents...)
	withoutTranscript[1].Transcript = ""
	selected, reason = chooseSessionTranscript(withoutTranscript, "%2", "", "%1")
	if selected == nil || selected.PaneTarget != "%2" || reason != "selected pane has no agent transcript" {
		t.Fatalf("missing selected transcript = %#v, reason=%q", selected, reason)
	}
}

func TestReadTranscriptTailPreservesBoundedRawLines(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	if err := os.WriteFile(path, []byte("one\ntwo\nthree\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	text, err := readTranscriptTail(path, 2)
	if err != nil || text != "two\nthree\n" {
		t.Fatalf("tail = %q, err=%v", text, err)
	}
}

func TestReadContextItemsUsesBoundedTailForSparseLargeTranscript(t *testing.T) {
	path := filepath.Join(t.TempDir(), "large.jsonl")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(100 * 1024 * 1024); err != nil {
		file.Close()
		t.Fatal(err)
	}
	if _, err := file.Seek(-128, io.SeekEnd); err != nil {
		file.Close()
		t.Fatal(err)
	}
	line := `
{"payload":{"type":"agent_message","message":"tail-only"}}
`
	if _, err := file.WriteString(line); err != nil {
		file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	items, err := readContextItems(path, 4)
	if err != nil || len(items) != 1 || items[0]["text"] != "tail-only" {
		t.Fatalf("large transcript context = %#v, err=%v", items, err)
	}
}

func TestReadTranscriptItemsSinceReadsOnlyAppendedRecords(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	initial := `{"payload":{"type":"agent_message","message":"initial"}}
`
	if err := os.WriteFile(path, []byte(initial), 0o600); err != nil {
		t.Fatal(err)
	}
	initialInfo, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(initial+`{"payload":{"type":"agent_message","message":"appended"}}
`), 0o600); err != nil {
		t.Fatal(err)
	}
	items, nextOffset, err := readTranscriptItemsSince(path, initialInfo.Size())
	if err != nil || nextOffset <= initialInfo.Size() || len(items) != 1 || items[0]["text"] != "appended" {
		t.Fatalf("appended transcript items = %#v, offset=%d, err=%v", items, nextOffset, err)
	}
}

func TestReadTranscriptItemsSinceBuffersSplitJSONLRecords(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	initial := []byte(`{"payload":{"type":"agent_message","message":"initial"}}
`)
	if err := os.WriteFile(path, initial, 0o600); err != nil {
		t.Fatal(err)
	}
	offset := int64(len(initial))
	partial := []byte(`{"payload":{"type":"agent_message","message":"app`)
	if err := os.WriteFile(path, append(initial, partial...), 0o600); err != nil {
		t.Fatal(err)
	}
	items, nextOffset, pending, err := readTranscriptItemsSinceWithPending(path, offset, nil)
	if err != nil || len(items) != 0 || nextOffset != offset || string(pending) != string(partial) {
		t.Fatalf("partial transcript read = items=%#v offset=%d pending=%q err=%v", items, nextOffset, pending, err)
	}
	completion := []byte(`ended"}}
`)
	if err := os.WriteFile(path, append(append(initial, partial...), completion...), 0o600); err != nil {
		t.Fatal(err)
	}
	items, nextOffset, pending, err = readTranscriptItemsSinceWithPending(path, nextOffset, pending)
	if err != nil || len(items) != 1 || items[0]["text"] != "appended" || nextOffset != int64(len(initial)+len(partial)+len(completion)) || len(pending) != 0 {
		t.Fatalf("completed split transcript read = items=%#v offset=%d pending=%q err=%v", items, nextOffset, pending, err)
	}
}

func TestTranscriptSignatureIncludesOpenCodeWALAndSHM(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "opencode.db")
	if err := os.WriteFile(path, []byte("database"), 0o600); err != nil {
		t.Fatal(err)
	}
	beforeSize, beforeMod := transcriptSignature(path)
	if err := os.WriteFile(path+"-wal", []byte("pending commit"), 0o600); err != nil {
		t.Fatal(err)
	}
	afterWALSize, afterWALMod := transcriptSignature(path)
	if afterWALSize == beforeSize && afterWALMod == beforeMod {
		t.Fatal("WAL creation did not change transcript signature")
	}
	if err := os.WriteFile(path+"-shm", []byte("shared state"), 0o600); err != nil {
		t.Fatal(err)
	}
	afterSHMSize, afterSHMMod := transcriptSignature(path)
	if afterSHMSize == afterWALSize && afterSHMMod == afterWALMod {
		t.Fatal("SHM creation did not change transcript signature")
	}
}

func TestAgentTranscriptUsesExplicitOpenCodeSessionID(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode.db")
	createSessionSurfaceOpenCodeDB(t, databasePath)
	t.Setenv("YOLOMUX_OPENCODE_DATABASE", databasePath)
	sessionID, gotPath, gotCWD := agentTranscriptWithCommand("opencode", 0, "/repo", "OC | build", "opencode --session ses-open")
	if sessionID != "ses-open" || gotPath != databasePath || gotCWD != "/repo" {
		t.Fatalf("explicit OpenCode identity = (%q, %q, %q)", sessionID, gotPath, gotCWD)
	}
}

func TestChooseSessionTranscriptRejectsAmbiguousWindow(t *testing.T) {
	agents := []finder.SessionFileAgent{
		{Kind: "claude", PaneTarget: "%1", Window: "2", WindowIndex: 2, Transcript: "/tmp/window-2-a.jsonl"},
		{Kind: "codex", PaneTarget: "%2", Window: "2", WindowIndex: 2, Transcript: "/tmp/window-2-b.jsonl"},
	}
	selected, reason := chooseSessionTranscript(agents, "", "2", "%9")
	if selected != nil || reason != "selected window has ambiguous agent transcripts" {
		t.Fatalf("ambiguous window selection = %#v, reason=%q", selected, reason)
	}
	agents[1].Active = true
	selected, reason = chooseSessionTranscript(agents, "", "2", "%9")
	if selected == nil || selected.PaneTarget != "%2" || reason != "" {
		t.Fatalf("active window selection = %#v, reason=%q", selected, reason)
	}
}

func TestFormatTranscriptItemMatchesPythonCompactHeader(t *testing.T) {
	item := map[string]any{"role": "assistant", "timestamp": "2026-09-17T10:00:01Z", "cwd": "/repo", "text": "ready"}
	if got := formatTranscriptItem(item); got != "assistant (2026-09-17T10:00:01Z, /repo)\nready" {
		t.Fatalf("formatted item = %q", got)
	}
}

func TestEventsListReadsPersistedSessionEvents(t *testing.T) {
	path := filepath.Join(t.TempDir(), "event-log.jsonl")
	content := "{\"session\":\"one\",\"type\":\"first\",\"message\":\"one\"}\n" +
		"{\"session\":\"two\",\"type\":\"second\",\"message\":\"two\"}\n" +
		"{\"session\":\"\",\"type\":\"global\",\"message\":\"global\"}\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{eventLogPath: path}
	record := httptest.NewRecorder()
	s.eventsList(record, httptest.NewRequest(http.MethodGet, "/api/events?limit=10", nil))
	if record.Code != http.StatusOK || len(s.eventLogTail("one", 10)) != 2 {
		t.Fatalf("events response = %d %s", record.Code, record.Body.String())
	}
}

func TestAgentKindFromCommandRecognizesWrappedAgents(t *testing.T) {
	tests := []struct {
		name    string
		command string
		want    string
	}{
		{name: "node codex wrapper", command: "node /workspace/node_modules/@openai/codex/bin/codex", want: "codex"},
		{name: "node opencode wrapper", command: "node /workspace/node_modules/opencode/bin/opencode", want: "opencode"},
		{name: "direct claude", command: "claude --resume session", want: "claude"},
		{name: "shell mentions codex", command: "bash -lc 'printf codex'", want: ""},
		{name: "python mock wrapper", command: "python3 /workspace/tools/mockers/claude.py --mock", want: "claude"},
		{name: "shell", command: "bash -lc 'printf ready'", want: ""},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := agentKindFromCommand(test.command); got != test.want {
				t.Fatalf("agentKindFromCommand(%q) = %q, want %q", test.command, got, test.want)
			}
		})
	}
}

func TestTmuxSignalPaneAgentRecognizesCodexBelowNodeLauncher(t *testing.T) {
	s := &server{processEntriesFn: func() ([]procEntry, error) {
		return []procEntry{{PID: 200, PPID: 100, Command: "node /home/keivenc/.local/bin/codex --resume thread"}}, nil
	}}
	kind, pid, command := s.tmuxSignalPaneAgent(100, "node")
	if kind != "codex" || pid != 200 || command != "node /home/keivenc/.local/bin/codex --resume thread" {
		t.Fatalf("tmux signal agent = (%q, %d, %q), want wrapped Codex child", kind, pid, command)
	}
}

func TestDarwinProcessEntriesRetainUsableAgentInventory(t *testing.T) {
	rows := []stats.DarwinProcess{
		{PID: 42, Identity: "42:1:0", Command: "bash", Executable: "/bin/bash", RSSBytes: 100},
		{PID: 43, PPID: 42, Identity: "43:1:0", Command: "codex --resume thread", Executable: "/usr/bin/codex", CPUSeconds: 1, RSSBytes: 200},
	}
	entries, err := processEntriesFromDarwinInventory(func() ([]stats.DarwinProcess, error) { return rows, nil })
	if err != nil || len(entries) != 2 || entries[1].Identity != rows[1].Identity || entries[1].CPUSeconds != 1 || entries[1].RSSBytes != 200 {
		t.Fatalf("Darwin observed process entries=%+v err=%v, want measured shell and native agent", entries, err)
	}
	index := indexProcessEntries(entries)
	kind, pid, _, _ := paneAgentProcessFromIndex(42, "bash", index)
	if kind != "codex" || pid != 43 {
		t.Fatalf("partial observable inventory pane agent=(%q, %d), want native Codex 43", kind, pid)
	}
	_, err = processEntriesFromDarwinInventory(func() ([]stats.DarwinProcess, error) { return nil, os.ErrPermission })
	if err != os.ErrPermission {
		t.Fatalf("unavailable inventory err=%v, want preserved observation failure", err)
	}
}

func TestPaneAgentProcessUsesSharedCodexProcessOwner(t *testing.T) {
	launcher := procEntry{
		PID: 20, PPID: 10,
		Command:    "node /home/test/.local/share/node_modules/@openai/codex/bin/codex.js --resume thread",
		Executable: "/usr/bin/node",
	}
	native := procEntry{PID: 21, PPID: 20, Command: "/home/test/.local/bin/codex", Executable: "/home/test/.local/bin/codex"}
	for _, test := range []struct {
		name      string
		processes []procEntry
		wantPID   int
		wantCmd   string
		wantExe   string
	}{
		{
			name:      "launcher remains owner before native child appears",
			processes: []procEntry{launcher},
			wantPID:   launcher.PID,
			wantCmd:   launcher.Command,
			wantExe:   launcher.Executable,
		},
		{
			name:      "native child replaces launcher as owner",
			processes: []procEntry{launcher, native},
			wantPID:   native.PID,
			wantCmd:   native.Command,
			wantExe:   native.Executable,
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			s := &server{processEntriesFn: func() ([]procEntry, error) { return test.processes, nil }}
			kind, pid, command, executable := s.paneAgentProcess(10, "bash")
			if kind != "codex" || pid != test.wantPID || command != test.wantCmd {
				t.Fatalf("pane agent = (%q, %d, %q), want Codex process %d", kind, pid, command, test.wantPID)
			}
			if executable != test.wantExe {
				t.Fatalf("pane agent executable = %q, want %q", executable, test.wantExe)
			}
		})
	}
}

func TestPaneAgentProcessTerminatesOnCyclicProcessSnapshot(t *testing.T) {
	processes := []procEntry{
		{PID: 20, PPID: 10, Command: "bash"},
		{PID: 30, PPID: 20, Command: "bash"},
		{PID: 40, PPID: 30, Command: "bash"},
		{PID: 20, PPID: 40, Command: "bash"},
	}
	s := &server{processEntriesFn: func() ([]procEntry, error) { return processes, nil }}
	type selection struct {
		kind       string
		pid        int
		command    string
		executable string
	}
	completed := make(chan selection, 1)
	go func() {
		kind, pid, command, executable := s.paneAgentProcess(10, "bash")
		completed <- selection{kind: kind, pid: pid, command: command, executable: executable}
	}()
	select {
	case got := <-completed:
		if got != (selection{}) {
			t.Fatalf("cyclic process snapshot selected an agent: %#v", got)
		}
	case <-time.After(time.Second):
		t.Fatal("pane process traversal did not terminate on a cycle")
	}
}

func TestAgentTranscriptFallsBackToClaudeCWD(t *testing.T) {
	root := t.TempDir()
	transcript := filepath.Join(root, "project", "claude-session.jsonl")
	if err := os.MkdirAll(filepath.Dir(transcript), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(transcript, []byte(`{"type":"assistant","cwd":"/repo","message":{"role":"assistant","content":"ready"}}
`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", root)
	sessionID, gotPath, gotCWD := agentTranscript("claude", 0, "/repo")
	if sessionID != "" || gotPath != transcript || gotCWD != "/repo" {
		t.Fatalf("agentTranscript fallback = (%q, %q, %q), want (%q, %q, %q)", sessionID, gotPath, gotCWD, "", transcript, "/repo")
	}
}

func TestAgentTranscriptUsesConfiguredOpenCodeDatabase(t *testing.T) {
	root := t.TempDir()
	databasePath := filepath.Join(root, "opencode.db")
	createSessionSurfaceOpenCodeDB(t, databasePath)
	t.Setenv("YOLOMUX_OPENCODE_DATABASE", databasePath)
	sessionID, gotPath, gotCWD := agentTranscriptWithTitle("opencode", 0, "/repo", "OC | build")
	if sessionID != "ses-open" || gotPath != databasePath || gotCWD != "/repo" {
		t.Fatalf("OpenCode identity = (%q, %q, %q)", sessionID, gotPath, gotCWD)
	}
	items, err := readContextItemsForAgent(databasePath, map[string]any{"kind": "opencode", "session_id": sessionID}, 20)
	if err != nil || len(items) != 4 || items[0]["role"] != "user" || items[1]["role"] != "assistant" || items[2]["role"] != "tool_use" || items[3]["role"] != "tool_result" {
		t.Fatalf("OpenCode context = %#v err=%v", items, err)
	}
	text, structured, err := readTranscriptTextForAgent(databasePath, map[string]any{"kind": "opencode", "session_id": sessionID}, 5000)
	if err != nil || len(structured) != 4 || strings.Contains(text, "SQLite format 3") || !strings.Contains(text, "inspect this") {
		t.Fatalf("OpenCode transcript projection = text %q items %#v err=%v", text, structured, err)
	}
	readOnly, err := stats.OpenOpenCodeReadOnly(context.Background(), databasePath)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	if _, err := readOnly.ReadContextItems(context.Background(), sessionID, stats.MaxOpenCodeContextItems); err != nil {
		t.Fatalf("shared 200-item context limit rejected by OpenCode reader: %v", err)
	}
	if _, err := readOnly.ReadContextItems(context.Background(), sessionID, stats.MaxOpenCodeContextItems+1); err == nil {
		t.Fatal("OpenCode reader accepted a context limit above the shared 200-item contract")
	}
}

func TestSessionWorkGraphContainsTmuxPathAndActorEdges(t *testing.T) {
	info := map[string]any{
		"panes": []map[string]any{{
			"target": "%0", "window": "0", "pane": "0", "window_name": "agent", "pid": 42,
			"command": "node /workspace/node_modules/opencode/bin/opencode", "current_path": "/tmp/project",
			"active": true, "window_active": true,
		}},
		"agents": []map[string]any{{
			"pane_target": "%0", "kind": "opencode", "pid": 42, "command": "opencode",
			"cwd": "/tmp/project", "status": "idle", "session_id": "session-1", "transcript": "/tmp/session.jsonl", "error": "",
		}},
	}
	graph := (&server{}).sessionWorkGraph("yo", info, 7)
	if graph["version"] != 1 || graph["generation"] != uint64(7) {
		t.Fatalf("graph identity = %#v", graph)
	}
	for _, key := range []string{"tmux_sessions", "tmux_windows", "tmux_panes", "runtime_actors", "path_observations", "git_worktrees", "local_repositories", "hosted_repositories", "local_branches", "pull_requests", "linear_issues", "worktree_branch_activity"} {
		if value, ok := graph[key].(map[string]any); !ok || value == nil {
			t.Fatalf("graph[%q] = %#v, want object", key, graph[key])
		}
	}
	if len(graph["tmux_sessions"].(map[string]any)) != 1 || len(graph["tmux_panes"].(map[string]any)) != 1 || len(graph["runtime_actors"].(map[string]any)) != 1 || len(graph["path_observations"].(map[string]any)) != 1 {
		t.Fatalf("graph nodes = %#v", graph)
	}
	for id, pane := range graph["tmux_panes"].(map[string]any) {
		if pane.(map[string]any)["runtime_actor_ids"].([]string)[0] == "" || id == "" {
			t.Fatalf("pane edge = %#v", pane)
		}
	}
}

func TestMetadataRefreshReadsGitSnapshotOncePerWorktree(t *testing.T) {
	root := t.TempDir()
	root, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatalf("resolve temporary worktree path: %v", err)
	}
	nested := filepath.Join(root, "nested")
	if err := os.MkdirAll(filepath.Join(root, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(nested, 0o700); err != nil {
		t.Fatal(err)
	}
	makeInfo := func(target string, cwd string) map[string]any {
		return map[string]any{
			"panes": []map[string]any{{
				"target": target, "window": "0", "pane": "0", "window_name": "repo", "pid": 1,
				"command": "bash", "current_path": cwd, "active": true, "window_active": true,
			}},
			"agents": []map[string]any{},
		}
	}
	gitCalls := 0
	s := &server{
		gitMetadataSnapshotFn: func(path string) (goGitSnapshot, bool) {
			gitCalls++
			if path != root {
				t.Fatalf("Git snapshot path = %q, want shared worktree root %q", path, root)
			}
			return goGitSnapshot{Root: root, Branch: "main", HeadSHA: "abc", Head: "subject"}, true
		},
		metadataEnrichmentFn: func([]string) (map[string]map[string]any, error) {
			return map[string]map[string]any{
				"first":  makeInfo("%1", root),
				"second": makeInfo("%2", nested),
			}, nil
		},
	}
	s.buildMetadataPayload(1, []string{"first", "second"}, map[string]string{"first": "first", "second": "second"}, nil, 1)
	if gitCalls != 1 {
		t.Fatalf("Git snapshot calls = %d, want one per worktree in the refresh", gitCalls)
	}
}

func TestSessionWorkGraphCarriesPanePIDToShellActor(t *testing.T) {
	info := map[string]any{"panes": []map[string]any{{"target": "%1", "window": "2", "pane": "0", "window_name": "shell|window", "pid": 4242, "command": "bash -lc 'printf a|b'", "current_path": "/tmp"}}}
	graph := (&server{}).sessionWorkGraph("yo", info, 1)
	actors := graph["runtime_actors"].(map[string]any)
	if len(actors) != 1 {
		t.Fatalf("runtime actors = %#v", actors)
	}
	for _, actor := range actors {
		if actor.(map[string]any)["kind"] != "shell" || actor.(map[string]any)["pid"] != 4242 {
			t.Fatalf("shell actor = %#v", actor)
		}
	}
}

func TestTmuxFieldsPreservePipeCharacters(t *testing.T) {
	line := "/repo/a|b\tbash -lc 'printf x|y'\t%1\t2\t0\t4242"
	parts := splitTmuxFields(line)
	if len(parts) != 6 || parts[0] != "/repo/a|b" || parts[1] != "bash -lc 'printf x|y'" {
		t.Fatalf("tmux fields = %#v", parts)
	}
}

func createSessionSurfaceOpenCodeDB(t *testing.T, path string) {
	t.Helper()
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	now := time.Now().UnixMilli()
	statements := []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, title TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		fmt.Sprintf(`INSERT INTO session VALUES ('ses-open', '/repo', 'build', '{}', %d, %d, 0, 'build')`, now-1000, now),
		`INSERT INTO message VALUES ('msg-user', 'ses-open', 1100, 1100, '{"role":"user"}')`,
		`INSERT INTO message VALUES ('msg-assistant', 'ses-open', 2000, 2000, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-user', 'msg-user', 'ses-open', 1200, 1200, '{"type":"text","text":"inspect this"}')`,
		`INSERT INTO part VALUES ('part-assistant', 'msg-assistant', 'ses-open', 2100, 2100, '{"type":"text","text":"I will inspect it"}')`,
		`INSERT INTO part VALUES ('part-tool', 'msg-assistant', 'ses-open', 2200, 2200, '{"type":"tool","tool":"read","state":{"input":{"path":"main.go"},"output":"package main"}}')`,
	}
	for _, statement := range statements {
		if _, err := db.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
}
