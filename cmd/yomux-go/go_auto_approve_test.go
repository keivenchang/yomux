// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/capabilities"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/testsupport"
	"github.com/keivenchang/yomux/internal/yolo"
)

func isolateGoYoloTestTmuxSocket(t *testing.T) {
	t.Helper()
	t.Setenv(productenv.Name("TMUX_SOCKET"), filepath.Join(t.TempDir(), "tmux.sock"))
}

func goYoloTestProcess(pid int, start, command, executable string) stats.AgentStatusProcess {
	return stats.AgentStatusProcess{PID: pid, StartedIdentity: start, Command: command, Executable: executable, Alive: true}
}

func goYoloTestProcessID(pid int, start string) string {
	return stats.AgentStatusProcessInstanceID(stats.AgentRecord{PID: pid, StartedIdentity: start})
}

func TestGoCapacityRecoveryRequiresExactIdleCodexNoticeWithoutNewerPrompt(t *testing.T) {
	notice := "■ Selected model is at capacity. Please try a different model."
	emptyComposer := notice + "\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	cases := []struct {
		name  string
		state string
		text  string
		want  bool
	}{
		{name: "exact idle notice and empty composer", state: "idle", text: emptyComposer, want: true},
		{name: "extra whitespace after marker", state: "idle", text: "■  Selected model is at capacity. Please try a different model.\n›", want: true},
		{name: "no whitespace after marker", state: "idle", text: "■Selected model is at capacity. Please try a different model.\n›", want: true},
		{name: "current Codex composer placeholder", state: "idle", text: notice + "\n› Ask Codex to do anything\n\n  gpt-5.5 xhigh · ~/repo", want: true},
		{name: "current Claude composer placeholder", state: "idle", text: notice + "\n› Ask Claude to do anything\n\n  Claude Code · ~/repo", want: true},
		{name: "working pane", state: "run", text: emptyComposer},
		{name: "approval prompt", state: "idle", text: emptyComposer + "\nWould you like to run this command?"},
		{name: "question", state: "ask", text: emptyComposer},
		{name: "unsent composer text", state: "idle", text: notice + "\n› please do not send this"},
		{name: "wrapped unsent composer draft", state: "idle", text: notice + "\n› please keep this\n  draft unsent"},
		{name: "no composer evidence", state: "idle", text: notice},
		{name: "notice without marker", state: "idle", text: "Selected model is at capacity. Please try a different model."},
		{name: "different notice", state: "idle", text: "■ Selected model is at capacity. Try again later."},
		{name: "scrollback context", state: "idle", text: "older: " + emptyComposer},
		{name: "old notice followed by successful output", state: "idle", text: notice + "\nCompleted successfully\n\n›\n\n  gpt-5.5 xhigh · ~/repo"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if got := goCapacityRecoveryAllowed(test.state, test.text); got != test.want {
				t.Fatalf("goCapacityRecoveryAllowed(%q, %q) = %t, want %t", test.state, test.text, got, test.want)
			}
		})
	}
	if !goCapacityNoticeCurrent(notice + "\n› user draft") {
		t.Fatal("capacity retry deadline should survive a temporary user draft")
	}
	if goCapacityNoticeCurrent(notice + "\nCompleted successfully\n\n›\n\n  gpt-5.5 xhigh · ~/repo") {
		t.Fatal("capacity retry deadline should reset after newer output replaces the notice")
	}
}

func TestGoYoloPersistenceFailureDoesNotChangeMemory(t *testing.T) {
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	store.path = t.TempDir()
	if err := store.set("session", true, "$1"); err == nil {
		t.Fatal("setting enabled state with a directory as state path succeeded")
	}
	if store.isEnabled("session") || store.identity("session") != "" {
		t.Fatalf("failed persistence changed in-memory state: enabled=%t identity=%q", store.isEnabled("session"), store.identity("session"))
	}
}

func TestGoYoloProcessBelongsToPaneUsesCurrentProcessAncestry(t *testing.T) {
	processes := []procEntry{
		{PID: 100, PPID: 1},
		{PID: 101, PPID: 100},
		{PID: 102, PPID: 101},
		{PID: 200, PPID: 1},
	}
	for _, test := range []struct {
		name     string
		panePID  int
		agentPID int
		want     bool
	}{
		{name: "pane process", panePID: 100, agentPID: 100, want: true},
		{name: "nested live agent", panePID: 100, agentPID: 102, want: true},
		{name: "different pane process", panePID: 100, agentPID: 200},
		{name: "missing process", panePID: 100, agentPID: 300},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := goYoloProcessBelongsToPane(processes, test.panePID, test.agentPID); got != test.want {
				t.Fatalf("process belongs to pane = %t, want %t", got, test.want)
			}
		})
	}
}

func TestGoYoloMetadataRefreshDoesNotSupersedeEquivalentBuild(t *testing.T) {
	s := &server{}
	s.metadataBuilding = true
	s.metadataBuildingGeneration = 5
	s.metadataBuildingTopologyGeneration = 3
	s.metadataNextGeneration = 5
	if generation := s.queueMetadataRefresh(false, []string{"session"}, map[string]string{"session": "$1"}, nil, 3); generation != 0 {
		t.Fatalf("equivalent in-flight metadata refresh queued generation %d", generation)
	}
	if s.metadataQueuedGeneration != 0 {
		t.Fatalf("equivalent in-flight metadata refresh left queued generation %d", s.metadataQueuedGeneration)
	}
}

func TestGoYoloPersistsPaneProcessIdentityAndRefusesReplacement(t *testing.T) {
	config := t.TempDir()
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.set("codex-session", true, "socket\t$1\t100\t10"); err != nil {
		t.Fatal(err)
	}
	if bound, err := store.bindAgentInstance("codex-session", "socket\t$1\t100\t10", "%1", "pid=99:start=1000"); err != nil || !bound {
		t.Fatalf("bind initial pane process = (%t, %v), want (true, nil)", bound, err)
	}
	reloaded, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	if bound, err := reloaded.bindAgentInstance("codex-session", "socket\t$1\t100\t10", "%1", "pid=99:start=2000"); err != nil || bound {
		t.Fatalf("replacement pane process = (%t, %v), want (false, nil)", bound, err)
	}
	reloaded.close()
	store.close()
}

func TestGoYoloDryRunReadsOnlyTheYoloSetting(t *testing.T) {
	config := t.TempDir()
	s := &server{config: config}
	if dryRun, err := s.goYoloDryRun(); err != nil || dryRun {
		t.Fatalf("missing settings dry-run = (%t, %v), want (false, nil)", dryRun, err)
	}
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("general:\n  dry_run: true\nyolo:\n  dry_run: true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if dryRun, err := s.goYoloDryRun(); err != nil || !dryRun {
		t.Fatalf("configured dry-run = (%t, %v), want (true, nil)", dryRun, err)
	}
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("yolo:\n  dry_run: true\n  rule_file_path: ./rules.yaml\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	rulesPath, err := s.goYoloRulesPath()
	if err != nil || rulesPath != filepath.Join(config, "rules.yaml") {
		t.Fatalf("configured rules path = (%q, %v), want %q", rulesPath, err, filepath.Join(config, "rules.yaml"))
	}
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("yolo:\n  dry_run: maybe\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := s.goYoloDryRun(); err == nil {
		t.Fatal("invalid dry-run setting was accepted")
	}
}

func TestGoYoloPolicyStatusReportsInvalidAndConfiguredRules(t *testing.T) {
	config := t.TempDir()
	s := &server{config: config}
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("yolo:\n  rule_file_path: ./rules.yaml\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if payload := s.goYoloPolicyStatus(); payload["available"] != false || payload["path"] != filepath.Join(config, "rules.yaml") || payload["source"] != "error" || payload["exists"] != false || payload["display_path"] != "~/.config/yomux/yolo-rules.yaml" || payload["error"] == "" {
		t.Fatalf("missing rule status = %#v, want fail-closed error", payload)
	}
	if err := os.WriteFile(filepath.Join(config, "rules.yaml"), []byte("default: ask\nrules:\n  - type: command\n    match: touch\n    action: approve\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if payload := s.goYoloPolicyStatus(); payload["available"] != true || payload["source"] != "file" || payload["source_key"] != "yolo.rule.source.file" || payload["default"] != "ask" || payload["rule_count"] != 1 || payload["mtime_ns"].(int64) <= 0 || payload["dry_run"] != false {
		t.Fatalf("configured rule status = %#v, want validated one-rule policy", payload)
	}
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("yolo:\n  dry_run: true\n  rule_file_path: ./rules.yaml\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if payload := s.goYoloPolicyStatus(); payload["dry_run"] != true {
		t.Fatalf("configured dry-run state missing from browser rules payload: %#v", payload)
	}
}

func TestGoYoloRulesRoutesMatchPreferencesContract(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("yolo:\n  rule_file_path: ./rules.yaml\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: config}
	get := httptest.NewRecorder()
	s.yoloRules(get, httptest.NewRequest(http.MethodGet, "/api/yolo-rules", nil))
	var before map[string]any
	if err := json.Unmarshal(get.Body.Bytes(), &before); err != nil {
		t.Fatal(err)
	}
	if get.Code != http.StatusOK || before["source"] != "error" {
		t.Fatalf("initial rules status = %d %#v", get.Code, before)
	}

	opened := httptest.NewRecorder()
	s.yoloRulesOpen(opened, httptest.NewRequest(http.MethodPost, "/api/yolo-rules/open", nil))
	var created map[string]any
	if err := json.Unmarshal(opened.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	if opened.Code != http.StatusOK || created["available"] != true || created["default"] != "ask" || created["exists"] != true {
		t.Fatalf("open rules response = %d %#v", opened.Code, created)
	}
	contents, err := os.ReadFile(filepath.Join(config, "rules.yaml"))
	if err != nil || !strings.Contains(string(contents), "default: ask") {
		t.Fatalf("created safe rules file = %q, %v", contents, err)
	}

	reloaded := httptest.NewRecorder()
	s.yoloRulesReload(reloaded, httptest.NewRequest(http.MethodPost, "/api/yolo-rules/reload", nil))
	if reloaded.Code != http.StatusOK {
		t.Fatalf("reload status = %d, body=%s", reloaded.Code, reloaded.Body.String())
	}
}

func TestGoYoloApprovalUsesRulesAndVerifiesSubmission(t *testing.T) {
	config := t.TempDir()
	rulesPath := filepath.Join(config, "rules.yaml")
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("yolo:\n  rule_file_path: ./rules.yaml\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(rulesPath, []byte("default: ask\nrules:\n  - name: approve fixture command\n    type: command\n    match: touch\n    action: approve\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "socket\t$1\t100\t10"); err != nil {
		t.Fatal(err)
	}
	visible := "Would you like to run the following command?\n\n$ touch /tmp/yolo-fixture\n\n› 1. Yes, proceed (y)\n  2. Yes, and don't ask again for commands that start with `touch /tmp/yolo-fixture` (p)\n  3. No, and tell Codex what to do differently (esc)\n\nPress enter to confirm or esc to cancel"
	state := "needs-input"
	sends := make([]string, 0, 3)
	processIdentity := goYoloTestProcessID(99, "fixture")
	s := &server{config: config, yolo: store, goYoloPaneOwnsProcessFn: func(context.Context, string, int) (bool, error) { return true, nil }, agentProcessSnapshot: func(context.Context, int) (stats.AgentStatusProcess, error) {
		return goYoloTestProcess(99, "fixture", "codex", "/bin/codex"), nil
	}}
	s.agentStatusClassifier = func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult {
		status := stats.AgentStatusAsk
		if state == "working" {
			status = stats.AgentStatusRun
		}
		return stats.AgentStatusResult{Status: status, ObservedAt: time.Now()}
	}
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		switch args[0] {
		case "display-message":
			return exec.CommandContext(ctx, "printf", "socket\t$1\t100\t10")
		case "capture-pane":
			return exec.CommandContext(ctx, "printf", "%s", visible)
		case "send-keys":
			sends = append(sends, args[len(args)-1])
			if sends[len(sends)-1] == "Down" {
				visible = strings.Replace(visible, "› 1. Yes, proceed", "  1. Yes, proceed", 1)
				visible = strings.Replace(visible, "  2. Yes, and don't ask", "› 2. Yes, and don't ask", 1)
				visible = strings.Replace(visible, "  2. No, and tell Codex what to do differently", "› 2. No, and tell Codex what to do differently", 1)
			}
			if sends[len(sends)-1] == "Enter" {
				state = "working"
				visible = "Command started\n◦ Working (1s • esc to interrupt)"
			}
			return exec.CommandContext(ctx, "true")
		default:
			return exec.CommandContext(ctx, "true")
		}
	}
	agent := map[string]any{"kind": "codex", "pid": 99, "command": "codex", "executable": "/bin/codex", "pane_target": "%1", "process_instance_id": processIdentity, "status": "needs-input"}
	prompt := yolo.ClassifyPrompt(visible, "")
	if !prompt.Actionable || prompt.Command != "touch /tmp/yolo-fixture" {
		t.Fatalf("fixture approval prompt = %#v", prompt)
	}
	if err := s.goYoloApprovalCycle(context.Background(), "codex-session", "%1", "socket\t$1\t100\t10", agent, prompt); err != nil {
		t.Fatal(err)
	}
	if len(sends) != 1 || sends[0] != "Enter" {
		t.Fatalf("approval terminal input = %#v, want only Enter for selected safe option", sends)
	}
	if store.approved["codex-session"] != 1 || store.lastSuccess["codex-session"].IsZero() {
		t.Fatalf("approval status = approved %d, last success %v", store.approved["codex-session"], store.lastSuccess["codex-session"])
	}
	if err := os.WriteFile(rulesPath, []byte("default: ask\nrules:\n  - name: decline fixture command\n    type: command\n    match: touch\n    action: decline\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	state = "needs-input"
	visible = "Would you like to run the following command?\n\n$ touch /tmp/yolo-fixture-two\n\n› 1. Yes, proceed (y)\n  2. No, and tell Codex what to do differently (esc)\n\nPress enter to confirm or esc to cancel"
	declinePrompt := yolo.ClassifyPrompt(visible, "")
	if !declinePrompt.Actionable {
		t.Fatalf("fixture decline prompt = %#v", declinePrompt)
	}
	if err := s.goYoloApprovalCycle(context.Background(), "codex-session", "%1", "socket\t$1\t100\t10", agent, declinePrompt); err != nil {
		t.Fatal(err)
	}
	if store.approved["codex-session"] != 1 || store.declined["codex-session"] != 1 {
		t.Fatalf("approval counters = approved:%d declined:%d", store.approved["codex-session"], store.declined["codex-session"])
	}
}

func TestGoYoloApprovalDryRunAndAmbiguousPromptSendNoInput(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("yolo:\n  dry_run: true\n  rule_file_path: ./rules.yaml\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "rules.yaml"), []byte("default: approve\nrules: []\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "identity"); err != nil {
		t.Fatal(err)
	}
	sends := 0
	s := &server{config: config, yolo: store, tmuxCommandContextFn: func(ctx context.Context, args ...string) *exec.Cmd {
		if args[0] == "send-keys" {
			sends++
		}
		return exec.CommandContext(ctx, "true")
	}}
	visible := "Would you like to run the following command?\n$ touch /tmp/yolo-fixture\n› 1. Yes, proceed (y)\n  2. No, cancel\nPress enter to confirm or esc to cancel"
	prompt := yolo.ClassifyPrompt(visible, "")
	if !prompt.Actionable {
		t.Fatalf("fixture approval prompt = %#v", prompt)
	}
	if err := s.goYoloApprovalCycle(context.Background(), "codex-session", "%1", "identity", map[string]any{}, prompt); err != nil {
		t.Fatal(err)
	}
	if sends != 0 || !strings.HasPrefix(store.lastAction["codex-session"], "dry-run:") {
		t.Fatalf("dry-run sent %d keys and reported %q", sends, store.lastAction["codex-session"])
	}
	if yolo.ClassifyPrompt(visible+"\nCompleted successfully", "").Actionable {
		t.Fatal("stale approval menu followed by successful output remained actionable")
	}
}

func TestGoYoloTargetLockAllowsOnlyOneServerPerTmuxTarget(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	firstStore, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer firstStore.close()
	secondStore, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer secondStore.close()
	for _, store := range []*goYoloStore{firstStore, secondStore} {
		if err := store.set("same-session", true, "$1"); err != nil {
			t.Fatal(err)
		}
	}
	first := &server{yolo: firstStore, root: "/tmp/first"}
	second := &server{yolo: secondStore, root: "/tmp/second"}
	if err := first.startGoYoloWorker("same-session"); err != nil {
		t.Fatal(err)
	}
	if err := second.startGoYoloWorker("same-session"); err == nil {
		t.Fatal("second YOmux instance acquired the same tmux target")
	}
	status := second.goYoloSessionPayload("same-session")
	if status["worker_state"] != "locked" || status["locked"] != true {
		t.Fatalf("locked worker status = %#v", status)
	}
}

func TestGoYoloWorkerLockPathFailureReportsFailedState(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("TMPDIR", tmp)
	lockRoot := filepath.Join(tmp, "yomux-auto-approve-"+strconv.Itoa(os.Getuid()))
	if err := os.WriteFile(lockRoot, []byte("block directory creation"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("session", true, "identity"); err != nil {
		t.Fatal(err)
	}
	s := &server{yolo: store, root: "/tmp/go-yolo"}
	if err := s.startGoYoloWorker("session"); err == nil {
		t.Fatal("startGoYoloWorker() succeeded despite an unusable lock path")
	}
	status := s.goYoloSessionPayload("session")
	if status["worker_state"] != "failed" || status["error"] == "" {
		t.Fatalf("worker startup failure status = %#v, want failed with an error", status)
	}
	if err := store.set("session", false, ""); err != nil {
		t.Fatal(err)
	}
	broker := newEventBroker()
	catalog := capabilities.New("go-test")
	catalog.Set("yolo.auto-approve.v1", capabilities.Entry{Available: true})
	s.events = broker
	s.capabilities = catalog
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		switch args[0] {
		case "has-session":
			return exec.CommandContext(ctx, "true")
		case "display-message":
			return exec.CommandContext(ctx, "printf", "identity")
		default:
			return exec.CommandContext(ctx, "true")
		}
	}
	request := httptest.NewRequest(http.MethodPost, "/api/auto-approve?session=session&enabled=1", nil)
	record := httptest.NewRecorder()
	s.autoApprove(record, request)
	if record.Code != http.StatusConflict || store.isEnabled("session") {
		t.Fatalf("failed enable response = %d %s; persisted enabled=%t", record.Code, record.Body.String(), store.isEnabled("session"))
	}
	broker.mu.Lock()
	defer broker.mu.Unlock()
	if len(broker.events) < 2 {
		t.Fatalf("worker failure and rollback published %d events, want failure and final state", len(broker.events))
	}
	lastEvent := broker.events[len(broker.events)-1]
	changes, ok := lastEvent.Payload["changes"].(map[string]any)
	state, stateOK := changes["session"].(map[string]any)
	if lastEvent.Type != "auto_approve_changed" || !ok || !stateOK || state["enabled"] != false {
		t.Fatalf("final worker rollback event = %#v, want disabled session snapshot", lastEvent)
	}
}

func TestGoYoloPaneLockSharesPythonWorkerTargetOwnership(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	config := t.TempDir()
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "identity"); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	close(done)
	store.workers["codex-session"] = &goYoloWorker{cancel: func() {}, done: done, paneLocks: make(map[string]*os.File)}
	server := &server{yolo: store, root: "/tmp/go-yolo"}
	defer server.releaseStaleGoYoloPaneLocks("codex-session", map[string]bool{})

	pythonLock, _, err := server.acquireGoYoloTargetLock("%1")
	if err != nil || pythonLock == nil {
		t.Fatalf("simulated Python pane lock = (%v, %v), want acquired lock", pythonLock, err)
	}
	if server.ensureGoYoloPaneLock("codex-session", "%1") {
		t.Fatal("Go acquired a pane target already owned by the Python worker")
	}
	store.mu.Lock()
	owner := store.lockOwners["codex-session"]
	store.mu.Unlock()
	if owner["target"] != "%1" || owner["pid"] == nil {
		t.Fatalf("Python pane-lock owner payload = %#v", owner)
	}

	if err := syscall.Flock(int(pythonLock.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	if err := pythonLock.Close(); err != nil {
		t.Fatal(err)
	}
	if !server.ensureGoYoloPaneLock("codex-session", "%1") {
		t.Fatal("Go did not acquire the pane target after the Python worker released it")
	}
}

func TestGoCapacityRetryUsesIndependentThreeSecondCadence(t *testing.T) {
	now := time.Unix(100, 0)
	cases := []struct {
		name     string
		lastSent time.Time
		want     bool
	}{
		{name: "first send", lastSent: time.Time{}, want: true},
		{name: "too soon", lastSent: now.Add(-2999 * time.Millisecond), want: false},
		{name: "interval elapsed", lastSent: now.Add(-3 * time.Second), want: true},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if got := goCapacityRetryDue(now, test.lastSent); got != test.want {
				t.Fatalf("goCapacityRetryDue = %t, want %t", got, test.want)
			}
		})
	}
}

func TestGoYoloEnabledSessionsPersistAcrossManagerRestart(t *testing.T) {
	config := t.TempDir()
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.set("codex-session", true, "$1"); err != nil {
		t.Fatal(err)
	}
	store.close()

	restarted, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.close()
	if sessions := restarted.sessions(); len(sessions) != 1 || sessions[0] != "codex-session" {
		t.Fatalf("restored YO sessions = %#v", sessions)
	}
	if identity := restarted.identity("codex-session"); identity != "$1" {
		t.Fatalf("restored YO session identity = %q", identity)
	}
	if err := restarted.set("codex-session", false, ""); err != nil {
		t.Fatal(err)
	}
	if sessions := restarted.sessions(); len(sessions) != 0 {
		t.Fatalf("disabled YO sessions = %#v", sessions)
	}
}

func TestGoYoloStartupClearsPersistedStateWhenCapabilityIsUnavailable(t *testing.T) {
	config := t.TempDir()
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.set("codex-session", true, "socket\t$1\t100\t10"); err != nil {
		t.Fatal(err)
	}
	store.close()

	restarted, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.close()
	s := &server{yolo: restarted, capabilities: capabilities.New("go-test")}
	s.restoreGoYoloWorkers(context.Background())
	if restarted.isEnabled("codex-session") {
		t.Fatal("persisted YO state remained enabled without the capability owner")
	}
}

func TestGoYoloStartupKeepsWorkerRetryingAfterTransientTmuxIdentityFailure(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "socket\t$1\t100\t10"); err != nil {
		t.Fatal(err)
	}
	catalog := capabilities.New("go-test")
	catalog.Set("yolo.auto-approve.v1", capabilities.Entry{Available: true})
	s := &server{yolo: store, capabilities: catalog}
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		if args[0] == "display-message" {
			return exec.CommandContext(ctx, "sh", "-c", "exit 1")
		}
		return exec.CommandContext(ctx, "true")
	}
	s.restoreGoYoloWorkers(context.Background())
	store.mu.Lock()
	worker := store.workers["codex-session"]
	store.mu.Unlock()
	if !store.isEnabled("codex-session") || worker == nil {
		t.Fatalf("transient startup identity failure left YO without a retrying worker: enabled=%t worker=%t action=%q", store.isEnabled("codex-session"), worker != nil, store.lastAction["codex-session"])
	}
}

func TestGoYoloStatePublicationUsesSessionKeyedPatch(t *testing.T) {
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "$1"); err != nil {
		t.Fatal(err)
	}
	broker := newEventBroker()
	s := &server{yolo: store, events: broker}
	s.publishGoYoloState("codex-session")

	broker.mu.Lock()
	if len(broker.events) != 1 {
		broker.mu.Unlock()
		t.Fatalf("published YO events = %d, want 1", len(broker.events))
	}
	payload := broker.events[0].Payload
	broker.mu.Unlock()
	if payload["patch"] != true || payload["collection"] != "sessions" {
		t.Fatalf("YO event does not use the keyed sessions patch contract: %#v", payload)
	}
	changes, ok := payload["changes"].(map[string]any)
	if !ok {
		t.Fatalf("YO event changes = %#v, want keyed object", payload["changes"])
	}
	state, ok := changes["codex-session"].(map[string]any)
	if !ok || state["enabled"] != true {
		t.Fatalf("YO event session state = %#v, want enabled session patch", changes["codex-session"])
	}
	if _, ok := payload["data"]; ok {
		t.Fatalf("single-session event used full-snapshot data field: %#v", payload)
	}
}

func TestGoYoloIdentityMismatchDoesNotWaitOnToggleTransition(t *testing.T) {
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "socket\t$1\t100\t10"); err != nil {
		t.Fatal(err)
	}
	s := &server{yolo: store}
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		return exec.CommandContext(ctx, "printf", "socket\t$2\t100\t10")
	}
	unlock := store.lockTransition("codex-session")
	done := make(chan error, 1)
	go func() { done <- s.goYoloCapacityCycle(context.Background(), "codex-session") }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		unlock()
		t.Fatal("identity transition waited for the toggle mutex held by its caller")
	}
	unlock()
	if store.isEnabled("codex-session") {
		t.Fatal("changed tmux incarnation retained YO enablement")
	}
}

func TestGoYoloConfirmedRenameDisablesWorkerAndReleasesPaneLock(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("old-session", true, "socket\t$1\t100\t10"); err != nil {
		t.Fatal(err)
	}
	s := &server{yolo: store, events: newEventBroker()}
	lock, _, err := s.acquireGoYoloTargetLock("%1")
	if err != nil || lock == nil {
		t.Fatalf("acquire pane lock = (%v, %v)", lock, err)
	}
	done := make(chan struct{})
	close(done)
	store.workers["old-session"] = &goYoloWorker{cancel: func() {}, done: done, paneLocks: map[string]*os.File{"%1": lock}}
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		switch args[0] {
		case "display-message":
			return exec.CommandContext(ctx, "false")
		case "show-options":
			return exec.CommandContext(ctx, "true")
		case "list-sessions":
			return exec.CommandContext(ctx, "printf", "new-session\n")
		default:
			return exec.CommandContext(ctx, "true")
		}
	}
	if err := s.goYoloCycle(context.Background(), "old-session"); err != nil {
		t.Fatal(err)
	}
	if store.isEnabled("old-session") {
		t.Fatal("confirmed renamed session remained enabled")
	}
	probe, _, err := s.acquireGoYoloTargetLock("%1")
	if err != nil || probe == nil {
		t.Fatalf("pane lock remained held after confirmed rename: probe=%v err=%v", probe, err)
	}
	_ = syscall.Flock(int(probe.Fd()), syscall.LOCK_UN)
	_ = probe.Close()
}

func TestGoYoloTransientTmuxFailureSuspendsWithoutReleasingPaneLock(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("session", true, "socket\t$1\t100\t10"); err != nil {
		t.Fatal(err)
	}
	s := &server{yolo: store}
	lock, _, err := s.acquireGoYoloTargetLock("%1")
	if err != nil || lock == nil {
		t.Fatalf("acquire pane lock = (%v, %v)", lock, err)
	}
	done := make(chan struct{})
	close(done)
	store.workers["session"] = &goYoloWorker{cancel: func() {}, done: done, paneLocks: map[string]*os.File{"%1": lock}}
	s.tmuxCommandContextFn = func(ctx context.Context, _ ...string) *exec.Cmd {
		return exec.CommandContext(ctx, "false")
	}
	if err := s.goYoloCycle(context.Background(), "session"); err != nil {
		t.Fatal(err)
	}
	if !store.isEnabled("session") || !strings.HasPrefix(store.lastAction["session"], "suspended:") {
		t.Fatalf("transient tmux failure state = enabled:%t action:%q", store.isEnabled("session"), store.lastAction["session"])
	}
	probe, owner, err := s.acquireGoYoloTargetLock("%1")
	if err != nil || probe != nil || owner == nil {
		t.Fatalf("pane lock was not retained during transient failure: probe=%v owner=%v err=%v", probe, owner, err)
	}
}

func TestGoYoloCapacityDryRunIsDurablyRecordedWithoutTerminalInput(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "settings.yaml"), []byte("yolo:\n  dry_run: true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("session", true, "identity"); err != nil {
		t.Fatal(err)
	}
	screen := "■ Selected model is at capacity. Please try a different model.\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	s := &server{config: config, yolo: store}
	done := make(chan struct{})
	close(done)
	store.workers["session"] = &goYoloWorker{cancel: func() {}, done: done, paneLocks: make(map[string]*os.File)}
	defer s.releaseStaleGoYoloPaneLocks("session", map[string]bool{})
	s.metadataMu.Lock()
	s.metadataPayload = map[string]any{"sessions": map[string]any{"session": map[string]any{
		"agents": []map[string]any{{"kind": "codex", "pane_target": "%1", "process_instance_id": "pid=99:start=1000", "status": "idle", "status_observed_at": float64(time.Now().UnixNano()) / 1e9}},
	}}}
	s.metadataBuiltAt = time.Now()
	s.metadataMu.Unlock()
	sends := 0
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		switch args[0] {
		case "display-message":
			return exec.CommandContext(ctx, "printf", "identity")
		case "capture-pane":
			return exec.CommandContext(ctx, "printf", "%s", screen)
		case "send-keys":
			sends++
			return exec.CommandContext(ctx, "true")
		default:
			return exec.CommandContext(ctx, "true")
		}
	}
	if err := s.goYoloCycle(context.Background(), "session"); err != nil {
		t.Fatal(err)
	}
	if sends != 0 {
		t.Fatalf("dry-run sent %d terminal inputs; status action=%q error=%q owners=%#v", sends, store.lastAction["session"], store.lastError["session"], store.lockOwners["session"])
	}
	if store.attempts["session"] != 0 || !store.lastAttempt["session"].IsZero() {
		t.Fatalf("dry-run counted a terminal attempt: attempts=%d last_attempt=%v", store.attempts["session"], store.lastAttempt["session"])
	}
	rows := s.eventLogTail("session", 10)
	if len(rows) != 1 || rows[0]["type"] != "yolo_capacity_recovery_dry_run" {
		t.Fatalf("durable capacity dry-run rows = %#v", rows)
	}
	details, ok := rows[0]["details"].(map[string]any)
	if !ok || details["attempted"] != false {
		t.Fatalf("capacity dry-run event details = %#v", rows[0]["details"])
	}
}

func TestGoYoloAuditActionsReachDurableEventLogAndEventBroker(t *testing.T) {
	config := t.TempDir()
	broker := newEventBroker()
	s := &server{config: config, events: broker, eventLogPath: filepath.Join(config, "event-log.jsonl")}
	s.publishGoYoloAuditEvent("codex-session", "yolo_capacity_recovery_attempt", "submitted continue", map[string]any{"attempted": true, "succeeded": true})
	rows := s.eventLogTail("codex-session", 10)
	if len(rows) != 1 || rows[0]["type"] != "yolo_capacity_recovery_attempt" || rows[0]["message"] != "submitted continue" {
		t.Fatalf("durable YO event rows = %#v", rows)
	}
	broker.mu.Lock()
	defer broker.mu.Unlock()
	if len(broker.events) != 1 || broker.events[0].Type != "event_log_changed" {
		t.Fatalf("YO event-log notifications = %#v", broker.events)
	}
}

func TestGoYoloWorkerStatusPreservesSuspendedAndDryRunStates(t *testing.T) {
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "identity"); err != nil {
		t.Fatal(err)
	}
	store.workers["codex-session"] = &goYoloWorker{owner: map[string]any{"pid": 7}}
	s := &server{yolo: store}
	for _, test := range []struct {
		action string
		state  string
	}{
		{action: "suspended: fresh status unavailable", state: "suspended"},
		{action: "dry-run: would submit continue", state: "dry-run"},
		{action: "capacity recovery submission sent; agent response not yet confirmed", state: "awaiting-confirmation"},
		{action: "submitted continue", state: "watching"},
	} {
		store.lastAction["codex-session"] = test.action
		if got := s.goYoloSessionPayload("codex-session")["worker_state"]; got != test.state {
			t.Errorf("action %q reported state %v, want %q", test.action, got, test.state)
		}
	}
	delete(store.workers, "codex-session")
}

func TestGoAutoApproveStatusReturnsAuthoritativeEnabledState(t *testing.T) {
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "$1"); err != nil {
		t.Fatal(err)
	}
	s := &server{yolo: store, tmuxMetadataState: tmuxMetadataState{metadataTopologyGeneration: 7}}
	request := httptest.NewRequest(http.MethodGet, "/api/auto-approve?session="+url.QueryEscape("codex-session"), nil)
	record := httptest.NewRecorder()
	s.autoApprove(record, request)
	if record.Code != http.StatusOK {
		t.Fatalf("status = %d, body=%s", record.Code, record.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["enabled"] != true || payload["target"] != "codex-session" || payload["topology_generation"] != float64(7) {
		t.Fatalf("auto-approve status payload = %#v", payload)
	}
}

func TestGoAutoApproveToggleIsAuthenticatedPersistentAndReturnsMarkerState(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	catalog := capabilities.New("go-test")
	catalog.Set("yolo.auto-approve.v1", capabilities.Entry{Available: true})
	s := &server{config: config, events: newEventBroker(), capabilities: catalog, yolo: store}
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		if args[0] == "display-message" {
			return exec.CommandContext(ctx, "printf", "$1")
		}
		return exec.CommandContext(ctx, "true")
	}
	handler := s.protected(http.HandlerFunc(s.autoApprove))
	request := httptest.NewRequest(http.MethodPost, "/api/auto-approve?session=codex-session&enabled=1", nil)
	request.SetBasicAuth("admin", "secret")
	record := httptest.NewRecorder()
	handler.ServeHTTP(record, request)
	if record.Code != http.StatusOK {
		t.Fatalf("enable status = %d, body=%s", record.Code, record.Body.String())
	}
	var enabled map[string]any
	if err := json.Unmarshal(record.Body.Bytes(), &enabled); err != nil {
		t.Fatal(err)
	}
	if enabled["enabled"] != true || enabled["session"] != "codex-session" {
		t.Fatalf("enabled payload = %#v", enabled)
	}

	restarted, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	if !restarted.isEnabled("codex-session") {
		restarted.close()
		t.Fatal("enabled session was not persisted")
	}
	restarted.close()

	disabledRequest := httptest.NewRequest(http.MethodPost, "/api/auto-approve?session=codex-session&enabled=0", nil)
	disabledRequest.SetBasicAuth("admin", "secret")
	disabledRecord := httptest.NewRecorder()
	handler.ServeHTTP(disabledRecord, disabledRequest)
	if disabledRecord.Code != http.StatusOK || store.isEnabled("codex-session") {
		t.Fatalf("disable status = %d, enabled=%t, body=%s", disabledRecord.Code, store.isEnabled("codex-session"), disabledRecord.Body.String())
	}
}

func TestGoYoloCapacityCycleRetriesOnlyTheVisibleGuardedNotice(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set("codex-session", true, "$1"); err != nil {
		t.Fatal(err)
	}
	screen := "■ Selected model is at capacity. Please try a different model.\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	sends := 0
	sessionIdentity := "$1"
	paneSessionIdentity := "$1"
	processStart := "proc:1000"
	s := &server{yolo: store, goYoloPaneOwnsProcessFn: func(context.Context, string, int) (bool, error) { return true, nil }, agentProcessSnapshot: func(context.Context, int) (stats.AgentStatusProcess, error) {
		return goYoloTestProcess(99, processStart, "codex", "/bin/codex"), nil
	}}
	done := make(chan struct{})
	close(done)
	store.workers["codex-session"] = &goYoloWorker{cancel: func() {}, done: done, paneLocks: make(map[string]*os.File)}
	defer s.releaseStaleGoYoloPaneLocks("codex-session", map[string]bool{})
	s.metadataMu.Lock()
	s.metadataPayload = map[string]any{
		"sessions": map[string]any{
			"codex-session": map[string]any{
				"agents": []map[string]any{{"kind": "codex", "pane_target": "%1", "pid": 99, "command": "codex", "executable": "/bin/codex", "process_instance_id": goYoloTestProcessID(99, processStart), "status": "idle", "status_observed_at": float64(time.Now().UnixNano()) / 1e9}},
			},
		},
	}
	s.metadataBuiltAt = time.Now()
	s.metadataMu.Unlock()
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		switch args[0] {
		case "display-message":
			identity := sessionIdentity
			if !strings.HasPrefix(args[3], "=") {
				identity = paneSessionIdentity
			}
			return exec.CommandContext(ctx, "printf", identity)
		case "capture-pane":
			return exec.CommandContext(ctx, "printf", "%s", screen)
		case "send-keys":
			sends++
			if !reflect.DeepEqual(args[3:], []string{"continue", "Enter", "Enter"}) {
				t.Errorf("capacity recovery keys = %#v", args[3:])
			}
			return exec.CommandContext(ctx, "true")
		default:
			return exec.CommandContext(ctx, "true")
		}
	}

	cycle := func() {
		t.Helper()
		if err := s.goYoloCapacityCycle(context.Background(), "codex-session"); err != nil {
			t.Fatal(err)
		}
	}
	cycle()
	if sends != 1 {
		t.Fatalf("first visible capacity notice sends = %d, want 1; status action=%q error=%q owners=%#v", sends, store.lastAction["codex-session"], store.lastError["codex-session"], store.lockOwners["codex-session"])
	}
	if action := store.lastAction["codex-session"]; !strings.Contains(action, "not yet confirmed") || !store.lastSuccess["codex-session"].IsZero() {
		t.Fatalf("send-keys exit was reported as confirmed success: action=%q last_success=%v", action, store.lastSuccess["codex-session"])
	}
	cycle()
	if sends != 1 {
		t.Fatalf("retry before cadence sends = %d, want 1", sends)
	}

	key := "codex-session\x00%1"
	store.mu.Lock()
	store.lastSent[key] = time.Now().Add(-goYoloCapacityRetryDelay)
	store.mu.Unlock()
	cycle()
	if sends != 2 {
		t.Fatalf("visible notice after retry interval sends = %d, want 2", sends)
	}

	paneSessionIdentity = "$2"
	cycle()
	if sends != 2 {
		t.Fatalf("pane from a different tmux session sent a capacity retry: %d", sends)
	}
	paneSessionIdentity = "$1"

	screen = "■ Selected model is at capacity. Please try a different model.\n› do not submit my draft"
	cycle()
	screen = "■ Selected model is at capacity. Please try a different model.\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	cycle()
	if sends != 3 {
		t.Fatalf("retry after guarded draft disappears sends = %d, want 3", sends)
	}
	store.mu.Lock()
	store.lastSent[key] = time.Now()
	store.mu.Unlock()
	s.metadataMu.Lock()
	s.metadataPayload["sessions"].(map[string]any)["codex-session"].(map[string]any)["agents"].([]map[string]any)[0]["status"] = "run"
	s.metadataMu.Unlock()
	cycle()
	s.metadataMu.Lock()
	s.metadataPayload["sessions"].(map[string]any)["codex-session"].(map[string]any)["agents"].([]map[string]any)[0]["status"] = "idle"
	s.metadataMu.Unlock()
	cycle()
	if sends != 3 {
		t.Fatalf("temporary non-idle status reset the three-second retry deadline: sends=%d", sends)
	}

	sessionIdentity = "$2"
	cycle()
	if sends != 3 || store.isEnabled("codex-session") {
		t.Fatalf("replacement session retained YO or received a retry: sends=%d enabled=%t", sends, store.isEnabled("codex-session"))
	}

	if err := store.set("codex-session", false, ""); err != nil {
		t.Fatal(err)
	}
	cycle()
	if sends != 3 {
		t.Fatalf("disabled YO mode sent another capacity retry: %d", sends)
	}
}

func TestGoYoloCycleDisablesBeforeSendingToReplacementAgent(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	const sessionIdentity = "socket\t$1\t100\t10"
	if err := store.set("codex-session", true, sessionIdentity); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	close(done)
	store.workers["codex-session"] = &goYoloWorker{cancel: func() {}, done: done, paneLocks: make(map[string]*os.File)}
	defer func() { _ = store.set("codex-session", false, "") }()
	screen := "■ Selected model is at capacity. Please try a different model.\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	processStart := "proc:1000"
	processIdentity := goYoloTestProcessID(99, processStart)
	now := time.Now()
	s := &server{yolo: store, goYoloPaneOwnsProcessFn: func(context.Context, string, int) (bool, error) { return true, nil }, agentStatusClassifier: func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult {
		return stats.AgentStatusResult{Status: stats.AgentStatusIdle, ObservedAt: time.Now()}
	}, agentProcessSnapshot: func(context.Context, int) (stats.AgentStatusProcess, error) {
		return goYoloTestProcess(99, processStart, "codex", "/bin/codex"), nil
	}}
	setAgent := func() {
		s.metadataMu.Lock()
		s.metadataPayload = map[string]any{"sessions": map[string]any{"codex-session": map[string]any{
			"agents": []map[string]any{{
				"kind": "codex", "pane_target": "%1", "pid": 99, "command": "codex", "executable": "/bin/codex", "process_instance_id": processIdentity,
				"status": "idle", "status_observed_at": float64(now.UnixNano()) / 1e9,
			}},
		}}}
		s.metadataBuiltAt = time.Now()
		s.metadataMu.Unlock()
	}
	setAgent()
	sends := 0
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		switch args[0] {
		case "display-message":
			return exec.CommandContext(ctx, "printf", sessionIdentity)
		case "capture-pane":
			return exec.CommandContext(ctx, "printf", "%s", screen)
		case "send-keys":
			sends++
			return exec.CommandContext(ctx, "true")
		default:
			return exec.CommandContext(ctx, "true")
		}
	}
	if err := s.goYoloCycle(context.Background(), "codex-session"); err != nil {
		t.Fatal(err)
	}
	if sends != 1 {
		t.Fatalf("initial bound process submissions = %d, want 1", sends)
	}
	processStart = "proc:2000"
	processIdentity = goYoloTestProcessID(99, processStart)
	setAgent()
	if err := s.goYoloCycle(context.Background(), "codex-session"); err != nil {
		t.Fatal(err)
	}
	if sends != 1 || store.isEnabled("codex-session") || store.lastAction["codex-session"] != "disabled because the agent process identity changed" {
		t.Fatalf("replacement process inherited YO: sends=%d enabled=%t action=%q", sends, store.isEnabled("codex-session"), store.lastAction["codex-session"])
	}
}

func TestGoYoloRevalidatesProcessIdentityImmediatelyBeforeCapacitySend(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	const sessionIdentity = "socket\t$1\t100\t10"
	if err := store.set("codex-session", true, sessionIdentity); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	close(done)
	store.workers["codex-session"] = &goYoloWorker{cancel: func() {}, done: done, paneLocks: make(map[string]*os.File)}
	const processStart = "proc:original"
	processIdentity := goYoloTestProcessID(99, processStart)
	if bound, bindErr := store.bindAgentInstance("codex-session", sessionIdentity, "%1", processIdentity); bindErr != nil || !bound {
		t.Fatalf("bind original process = %t, %v", bound, bindErr)
	}
	screen := "■ Selected model is at capacity. Please try a different model.\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	s := &server{yolo: store, goYoloPaneOwnsProcessFn: func(context.Context, string, int) (bool, error) { return true, nil }, agentStatusClassifier: func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult {
		return stats.AgentStatusResult{Status: stats.AgentStatusIdle, ObservedAt: time.Now()}
	}, agentProcessSnapshot: func(context.Context, int) (stats.AgentStatusProcess, error) {
		return goYoloTestProcess(99, "proc:replacement", "codex", "/bin/codex"), nil
	}}
	s.metadataMu.Lock()
	s.metadataPayload = map[string]any{"sessions": map[string]any{"codex-session": map[string]any{
		"agents": []map[string]any{{"kind": "codex", "pane_target": "%1", "pid": 99, "command": "codex", "executable": "/bin/codex", "process_instance_id": processIdentity, "status": "idle", "status_observed_at": float64(time.Now().UnixNano()) / 1e9}},
	}}}
	s.metadataBuiltAt = time.Now()
	s.metadataMu.Unlock()
	sends := 0
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		switch args[0] {
		case "display-message":
			return exec.CommandContext(ctx, "printf", sessionIdentity)
		case "capture-pane":
			return exec.CommandContext(ctx, "printf", "%s", screen)
		case "send-keys":
			sends++
			return exec.CommandContext(ctx, "true")
		default:
			return exec.CommandContext(ctx, "true")
		}
	}
	if err := s.goYoloCycle(context.Background(), "codex-session"); err != nil {
		t.Fatal(err)
	}
	if sends != 0 || store.isEnabled("codex-session") || store.lastAction["codex-session"] != "disabled because the agent process identity changed" {
		t.Fatalf("replacement received capacity input: sends=%d enabled=%t action=%q", sends, store.isEnabled("codex-session"), store.lastAction["codex-session"])
	}
}

func TestGoYoloRevalidatesCommandAfterSamePIDExecReplacement(t *testing.T) {
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	const sessionIdentity = "socket\t$1\t100\t10"
	if err := store.set("codex-session", true, sessionIdentity); err != nil {
		t.Fatal(err)
	}
	processID := goYoloTestProcessID(99, "proc:same-start")
	if bound, bindErr := store.bindAgentInstance("codex-session", sessionIdentity, "%1", processID); bindErr != nil || !bound {
		t.Fatalf("bind original process = %t, %v", bound, bindErr)
	}
	s := &server{yolo: store, goYoloPaneOwnsProcessFn: func(context.Context, string, int) (bool, error) { return true, nil }, agentProcessSnapshot: func(context.Context, int) (stats.AgentStatusProcess, error) {
		return goYoloTestProcess(99, "proc:same-start", "claude", "/bin/claude"), nil
	}}
	agent := map[string]any{"pid": 99, "process_instance_id": processID, "command": "codex", "executable": "/bin/codex"}
	current, err := s.goYoloRevalidateAgentInstance(context.Background(), "codex-session", sessionIdentity, "%1", agent)
	if err != nil {
		t.Fatal(err)
	}
	if current || store.isEnabled("codex-session") || store.lastAction["codex-session"] != "disabled because the agent process identity changed" {
		t.Fatalf("same-PID image replacement retained YO: current=%t enabled=%t action=%q", current, store.isEnabled("codex-session"), store.lastAction["codex-session"])
	}
}

func TestGoYoloGuardedTransportChecksFreshScreenBeforeEveryWrite(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	const session = "guarded-session"
	const target = "%7"
	const sessionIdentity = "socket\t$7\t700\t70"
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	if err := store.set(session, true, sessionIdentity); err != nil {
		t.Fatal(err)
	}
	processIdentity := goYoloTestProcessID(99, "proc:guarded")
	if bound, bindErr := store.bindAgentInstance(session, sessionIdentity, target, processIdentity); bindErr != nil || !bound {
		t.Fatalf("bind process = %t, %v", bound, bindErr)
	}
	const capacity = "■ Selected model is at capacity. Please try a different model.\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	currentScreen := capacity
	var commandLog [][]string
	ownsPane := true
	s := &server{
		yolo:                    store,
		goYoloPaneOwnsProcessFn: func(context.Context, string, int) (bool, error) { return ownsPane, nil },
		agentStatusClassifier: func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult {
			return stats.AgentStatusResult{Status: stats.AgentStatusIdle, ObservedAt: time.Now()}
		},
		agentProcessSnapshot: func(context.Context, int) (stats.AgentStatusProcess, error) {
			return goYoloTestProcess(99, "proc:guarded", "codex", "/bin/codex"), nil
		},
	}
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		commandLog = append(commandLog, append([]string(nil), args...))
		switch args[0] {
		case "display-message":
			return exec.CommandContext(ctx, "printf", sessionIdentity)
		case "capture-pane":
			return exec.CommandContext(ctx, "printf", "%s", currentScreen)
		case "send-keys":
			return exec.CommandContext(ctx, "true")
		default:
			return exec.CommandContext(ctx, "true")
		}
	}
	agent := map[string]any{
		"kind": "codex", "pid": 99, "command": "codex", "executable": "/bin/codex",
		"pane_target": target, "process_instance_id": processIdentity,
	}
	guard := func(screen string) bool { return goCapacityRecoveryAllowed("idle", screen) }

	currentScreen = capacity + "\n\n› fix.py"
	commandLog = nil
	sent, err := s.goYoloGuardedSendKeys(context.Background(), session, target, sessionIdentity, agent, []string{"continue", "Enter", "Enter"}, []string{"idle"}, guard)
	if err != nil || sent {
		t.Fatalf("draft-protected send = sent %t, err %v; want no send", sent, err)
	}
	for _, command := range commandLog {
		if command[0] == "send-keys" {
			t.Fatalf("draft reached terminal through command %q", command)
		}
	}

	currentScreen = capacity
	commandLog = nil
	sent, err = s.goYoloGuardedSendKeys(context.Background(), session, target, sessionIdentity, agent, []string{"continue", "Enter", "Enter"}, []string{"idle"}, guard)
	if err != nil || !sent {
		t.Fatalf("current capacity send = sent %t, err %v; want guarded send", sent, err)
	}
	if len(commandLog) < 4 || commandLog[len(commandLog)-2][0] != "capture-pane" || commandLog[len(commandLog)-1][0] != "send-keys" {
		t.Fatalf("terminal write was not adjacent to its final capture: %#v", commandLog)
	}
	if got := commandLog[len(commandLog)-1]; !reflect.DeepEqual(got, []string{"send-keys", "-t", target, "continue", "Enter", "Enter"}) {
		t.Fatalf("guarded key sequence = %q", got)
	}
	ownsPane = false
	commandLog = nil
	if sent, err := s.goYoloGuardedSendKeys(context.Background(), session, target, sessionIdentity, agent, []string{"continue", "Enter", "Enter"}, []string{"idle"}, guard); err != nil || sent {
		t.Fatalf("replacement process outside pane = sent %t, err %v; want no send", sent, err)
	}
	for _, command := range commandLog {
		if command[0] == "send-keys" {
			t.Fatalf("process outside target pane reached terminal through command %q", command)
		}
	}
	if sent, err := s.goYoloGuardedSendKeys(context.Background(), session, target, sessionIdentity, agent, []string{"clear", "Enter"}, []string{"idle"}, guard); err == nil || sent {
		t.Fatalf("unsupported key sequence = sent %t, err %v; want rejected", sent, err)
	}
}

func TestGoYoloCapacityWorkerSubmitsThroughPrivateTmuxAPI(t *testing.T) {
	root := testsupport.ShortTempDir(t)
	socket := filepath.Join(root, "tmux.sock")
	t.Setenv(productenv.Name("TMUX_SOCKET"), socket)
	countPath := filepath.Join(root, "submissions")
	workingPath := filepath.Join(root, "working")
	scriptPath := filepath.Join(root, "codex-fixture.sh")
	script := fmt.Sprintf(`printf '\n\n\n\n\n\n\n\n\n\n\n\n\n\n'
printf '■ Selected model is at capacity. Please try a different model.\n\n›\n\n  gpt-5.5 xhigh · ~/repo\n'
while IFS= read -r line; do
  if [ "$line" = continue ]; then
    count=0
    if [ -r %q ]; then count=$(cat %q); fi
    count=$((count + 1))
    printf '%%s\n' "$count" > %q
    : > %q
    printf '\033[2J\033[H'
    printf '\n\n\n\n\n\n\n\n\n\n\n\n\n\n'
    printf '◦ Working on the request...\n\n›\n\n  gpt-5.5 xhigh · ~/repo\n'
  fi
done
`, countPath, countPath, countPath, workingPath)
	if err := os.WriteFile(scriptPath, []byte(testsupport.ShellScript(t, script)), 0o700); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", "yo-private", scriptPath).CombinedOutput(); err != nil {
		t.Fatalf("start private tmux session: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	session := "yo-private"
	targetBytes, err := exec.Command("tmux", "-S", socket, "display-message", "-p", "-t", "="+session+":0.0", "#{pane_id}").Output()
	if err != nil {
		t.Fatal(err)
	}
	target := strings.TrimSpace(string(targetBytes))
	pidBytes, err := exec.Command("tmux", "-S", socket, "display-message", "-p", "-t", target, "#{pane_pid}").Output()
	if err != nil {
		t.Fatal(err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(pidBytes)))
	if err != nil {
		t.Fatalf("parse private tmux pane PID: %v", err)
	}

	config := filepath.Join(root, "config")
	store, err := newGoYoloStore(config)
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	now := time.Now()
	srv := &server{
		config:                  config,
		state:                   filepath.Join(root, "state"),
		root:                    root,
		yolo:                    store,
		goYoloPaneOwnsProcessFn: func(context.Context, string, int) (bool, error) { return true, nil },
		agentProcessSnapshot: func(_ context.Context, pid int) (stats.AgentStatusProcess, error) {
			return goYoloTestProcess(pid, "fixture", "codex fixture", "/bin/codex"), nil
		},
		agentStatusClassifier: func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult {
			status := stats.AgentStatusIdle
			if _, statErr := os.Stat(workingPath); statErr == nil {
				status = stats.AgentStatusRun
			} else if !os.IsNotExist(statErr) {
				status = stats.AgentStatusUnavailable
			}
			return stats.AgentStatusResult{Status: status, ObservedAt: time.Now()}
		},
	}
	srv.metadataMu.Lock()
	srv.metadataPayload = map[string]any{"sessions": map[string]any{session: map[string]any{
		"agents": []map[string]any{{
			"kind": "codex", "pane_target": target, "pid": pid, "command": "codex fixture", "executable": "/bin/codex", "process_instance_id": goYoloTestProcessID(pid, "fixture"), "status": "idle",
			"status_observed_at": float64(now.UnixNano()) / 1e9,
		}},
	}}}
	srv.metadataBuiltAt = now
	srv.metadataMu.Unlock()
	refreshMetadata := func() {
		now := time.Now()
		status := "idle"
		if _, statErr := os.Stat(workingPath); statErr == nil {
			status = "working"
		} else if !os.IsNotExist(statErr) {
			t.Fatalf("read private tmux status marker: %v", statErr)
		}
		srv.metadataMu.Lock()
		srv.metadataPayload = map[string]any{"sessions": map[string]any{session: map[string]any{
			"agents": []map[string]any{{
				"kind": "codex", "pane_target": target, "pid": pid, "command": "codex fixture", "executable": "/bin/codex", "process_instance_id": goYoloTestProcessID(pid, "fixture"), "status": status,
				"status_observed_at": float64(now.UnixNano()) / 1e9,
			}},
		}}}
		srv.metadataBuiltAt = now
		srv.metadataMu.Unlock()
	}
	muxMux := http.NewServeMux()
	muxMux.HandleFunc("/api/auto-approve", srv.autoApprove)
	httpServer := httptest.NewServer(muxMux)
	defer httpServer.Close()
	client := httpServer.Client()
	request := func(method, path string) map[string]any {
		t.Helper()
		if method == http.MethodGet {
			refreshMetadata()
		}
		req, requestErr := http.NewRequest(method, httpServer.URL+path, nil)
		if requestErr != nil {
			t.Fatal(requestErr)
		}
		response, requestErr := client.Do(req)
		if requestErr != nil {
			t.Fatal(requestErr)
		}
		defer response.Body.Close()
		var payload map[string]any
		if decodeErr := json.NewDecoder(response.Body).Decode(&payload); decodeErr != nil {
			t.Fatal(decodeErr)
		}
		if response.StatusCode != http.StatusOK {
			t.Fatalf("%s %s status = %d, body=%#v", method, path, response.StatusCode, payload)
		}
		return payload
	}
	enabled := request(http.MethodPost, "/api/auto-approve?session=yo-private&enabled=1")
	if enabled["enabled"] != true || enabled["backend"] != "go" {
		t.Fatalf("enable response = %#v", enabled)
	}
	deadline := time.NewTimer(5 * time.Second)
	ticker := time.NewTicker(25 * time.Millisecond)
	defer deadline.Stop()
	defer ticker.Stop()
	for {
		status := request(http.MethodGet, "/api/auto-approve?session=yo-private")
		if status["attempts"] == float64(1) && strings.Contains(stringValue(status["last_action"]), "submitted continue") {
			break
		}
		select {
		case <-deadline.C:
			screen, _ := srv.goYoloCapture(context.Background(), target)
			t.Fatalf("private tmux worker did not submit and verify capacity recovery: status=%#v screen=%q", status, screen)
		case <-ticker.C:
		}
	}
	countBytes, err := os.ReadFile(countPath)
	if err != nil {
		t.Fatal(err)
	}
	if count := strings.TrimSpace(string(countBytes)); count != "1" {
		t.Fatalf("private tmux terminal received %q capacity submissions, want exactly one", count)
	}
	disabled := request(http.MethodPost, "/api/auto-approve?session=yo-private&enabled=0")
	if disabled["enabled"] != false || !strings.HasPrefix(stringValue(disabled["last_action"]), "off") {
		t.Fatalf("disable response = %#v", disabled)
	}
	if !store.isEnabled(session) {
		return
	}
	t.Fatal("YO remained enabled after the private tmux API disable transition")
}

func TestGoYoloSessionIdentityIncludesTmuxServerPID(t *testing.T) {
	serverPID := "10"
	s := &server{tmuxCommandContextFn: func(ctx context.Context, args ...string) *exec.Cmd {
		return exec.CommandContext(ctx, "printf", "socket\t$1\t100\t"+serverPID)
	}}
	first, err := s.goYoloSessionIdentity(context.Background(), "codex-session")
	if err != nil {
		t.Fatal(err)
	}
	serverPID = "11"
	second, err := s.goYoloSessionIdentity(context.Background(), "codex-session")
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatalf("tmux server restart reused YO identity %q", first)
	}
}

func TestGoYoloStatusPublishesChangedSuspension(t *testing.T) {
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	s := &server{yolo: store, events: newEventBroker()}
	s.recordGoYoloStatus("codex-session", "suspended: fresh agent status is unavailable", "", false, false)
	s.events.mu.Lock()
	firstCount := len(s.events.events)
	firstType := s.events.events[0].Type
	s.events.mu.Unlock()
	if firstCount != 1 || firstType != "auto_approve_changed" {
		t.Fatalf("first suspension event count/type = %d/%q, want 1/auto_approve_changed", firstCount, firstType)
	}
	s.recordGoYoloStatus("codex-session", "suspended: fresh agent status is unavailable", "", false, false)
	s.events.mu.Lock()
	unchangedCount := len(s.events.events)
	s.events.mu.Unlock()
	if unchangedCount != firstCount {
		t.Fatalf("unchanged suspension republished state: event count = %d, want %d", unchangedCount, firstCount)
	}
	s.recordGoYoloStatus("codex-session", "suspended: fresh agent status is unavailable", "capture failed", false, false)
	s.events.mu.Lock()
	changedCount := len(s.events.events)
	s.events.mu.Unlock()
	if changedCount != firstCount+1 {
		t.Fatalf("changed suspension status event count = %d, want %d", changedCount, firstCount+1)
	}
}

func TestGoYoloCycleRefreshesMissingMetadataWithoutBrowser(t *testing.T) {
	isolateGoYoloTestTmuxSocket(t)
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	const identity = "socket\t$1\t100\t10"
	if err := store.set("codex-session", true, identity); err != nil {
		t.Fatal(err)
	}
	refreshes := 0
	s := &server{
		yolo: store,
		goYoloMetadataRefreshFn: func(session string) error {
			if session != "codex-session" {
				t.Fatalf("metadata refresh session = %q", session)
			}
			refreshes++
			return nil
		},
		tmuxCommandContextFn: func(ctx context.Context, args ...string) *exec.Cmd {
			if args[0] == "display-message" {
				return exec.CommandContext(ctx, "printf", identity)
			}
			return exec.CommandContext(ctx, "true")
		},
	}
	if err := s.goYoloCycle(context.Background(), "codex-session"); err != nil {
		t.Fatal(err)
	}
	if refreshes != 1 || !strings.Contains(store.lastAction["codex-session"], "fresh agent status is unavailable") {
		t.Fatalf("browser-independent metadata refreshes=%d status=%q", refreshes, store.lastAction["codex-session"])
	}
}

func TestGoYoloSessionPayloadOmitsStaleAgentRows(t *testing.T) {
	store, err := newGoYoloStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.close()
	s := &server{yolo: store}
	s.metadataMu.Lock()
	s.metadataPayload = map[string]any{"sessions": map[string]any{"codex-session": map[string]any{
		"agents": []map[string]any{{"kind": "codex", "pane_target": "%1", "status": "idle"}},
	}}}
	s.metadataBuiltAt = time.Now().Add(-3 * goMetadataCacheTTL)
	s.metadataMu.Unlock()
	payload := s.goYoloSessionPayload("codex-session")
	rows, ok := payload["agent_windows"].([]any)
	if !ok || len(rows) != 0 {
		t.Fatalf("stale agent rows were published as canonical YO status: %#v", payload["agent_windows"])
	}
}
