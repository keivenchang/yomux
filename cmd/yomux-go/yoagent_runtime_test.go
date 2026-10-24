// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/chat"
	"github.com/keivenchang/yomux/internal/yoagent"
)

func TestNativeYOAgentAnswerPersistsAndPublishesConversation(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	root, configDir := t.TempDir(), t.TempDir()
	builtinDir := filepath.Join(root, "yomux_lib", "yoagent", "builtin_skills")
	if err := os.MkdirAll(builtinDir, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(builtinDir, "status.yaml"), []byte("name: status\nkind: workflow\ndescription: Ask for status.\ntools: [read_activity]\n"), 0600); err != nil {
		t.Fatal(err)
	}
	userContextDir := filepath.Join(configDir, "context.d")
	if err := os.MkdirAll(userContextDir, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(userContextDir, "project.md"), []byte("Project-specific context."), 0600); err != nil {
		t.Fatal(err)
	}
	var events []string
	settings := map[string]any{"backend": "codex", "system_prompt": "system"}
	agent, err := newNativeYOAgent(store, root, configDir, func(context.Context) (map[string]any, error) {
		return settings, nil
	}, func(kind string, payload map[string]any) error {
		phase, _ := payload["phase"].(string)
		events = append(events, kind+":"+phase)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	agent.run = func(_ context.Context, _ string, config map[string]any, backend, _ string, _ bool, prompt string, emit func(string) error) (map[string]any, string, error) {
		if backend != "codex" || config["system_prompt"] != "system" || !strings.Contains(prompt, "User message:\nhello") || !strings.Contains(prompt, "Project-specific context.") || !strings.Contains(prompt, "Ask for status.") {
			t.Fatalf("unexpected backend invocation: %q %v %q", backend, config, prompt)
		}
		if err := emit("hello "); err != nil {
			t.Fatal(err)
		}
		if err := emit("hello back"); err != nil {
			t.Fatal(err)
		}
		return map[string]any{"backend": backend}, "hello back", nil
	}
	answer, status, err := agent.Answer(context.Background(), "hello", "en", "request-1", "stream-1", "admin")
	if err != nil || answer != "hello back" || status["backend"] != "codex" {
		t.Fatalf("answer=%q status=%v err=%v", answer, status, err)
	}
	messages, issues, err := store.LoadMessages(context.Background(), yoagent.ConversationLimit)
	if err != nil || len(issues) != 0 || len(messages) != 2 || messages[0]["content"] != "hello" || messages[1]["content"] != "hello back" {
		t.Fatalf("messages=%v issues=%v err=%v", messages, issues, err)
	}
	wantEvents := []string{"yoagent_stream_delta:started", "yoagent_stream_delta:streaming", "yoagent_stream_delta:streaming", "yoagent_stream_delta:done", "yoagent_conversation_changed:"}
	if !reflect.DeepEqual(events, wantEvents) {
		t.Fatalf("events=%v want=%v", events, wantEvents)
	}
}

func TestNativeYOAgentModelIntentStoresValidatedJobWithoutStreamingPlan(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	root, configDir := t.TempDir(), t.TempDir()
	builtinDir := filepath.Join(root, "yomux_lib", "yoagent", "builtin_skills")
	if err := os.MkdirAll(builtinDir, 0700); err != nil {
		t.Fatal(err)
	}
	skill := `name: loop-send
kind: workflow
description: Schedule bounded repeated prompts.
tools: [read_activity]
triggers: [every, repeat]
model_intent:
  handler: loop-send
  confirmation: none
  defaults:
    interval_seconds: 30
    max_runs: 10
  candidate:
    any_of: [every, repeat]
    require_any: [ask, tell, send, run, have]
    min_known_sessions: 1
    session_scope: request
    priority: 100
  output:
    tag: yoagent-loop-plan
    schema: loop-send
`
	if err := os.WriteFile(filepath.Join(builtinDir, "loop-send.yaml"), []byte(skill), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "yomux_lib", "yoagent", "builtin_context"), 0700); err != nil {
		t.Fatal(err)
	}
	var streamed []map[string]any
	agent, err := newNativeYOAgent(store, root, configDir, func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(kind string, payload map[string]any) error {
		if kind == "yoagent_stream_delta" {
			streamed = append(streamed, payload)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	wantPlan := `{"session":"agent-a","prompt":"check status","interval_seconds":5,"max_runs":2}`
	var capturedPrompt string
	agent.run = func(_ context.Context, _ string, settings map[string]any, backend, sessionID string, resume bool, prompt string, emit func(string) error) (map[string]any, string, error) {
		if backend != "codex" || sessionID != "" || resume {
			t.Fatalf("model intent must use an isolated provider request: backend=%s session=%q resume=%v", backend, sessionID, resume)
		}
		capturedPrompt = prompt
		if _, ok := settings["_yoagent_model_intent_schema"].(map[string]any); !ok {
			t.Fatalf("provider schema missing from isolated request: %#v", settings)
		}
		if err := emit(wantPlan); err != nil {
			t.Fatal(err)
		}
		return map[string]any{"backend": backend, "session_id": "must-not-persist"}, wantPlan, nil
	}
	answer, status, err := agent.AnswerWithSessions(context.Background(), "Every 5 seconds have agent-a ask for status twice.", "en", "intent-request", "intent-stream", "admin", []string{"agent-a", "agent-b"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(capturedPrompt, `"agent-a","agent-b"`) || !strings.Contains(answer, "Bounded action saved as job") || status["structured_job"] != true || status["model_intent"] != "loop-send" {
		t.Fatalf("answer/status/prompt = %q / %#v / %q", answer, status, capturedPrompt)
	}
	for _, event := range streamed {
		if event["phase"] == "streaming" {
			t.Fatalf("planner JSON was exposed as streamed answer: %#v", event)
		}
	}
	if _, exists := status["session_id"]; exists {
		t.Fatalf("isolated planner session was persisted: %#v", status)
	}
	jobs, _, err := store.LoadJobs(context.Background())
	if err != nil || len(jobs) != 1 || jobs[0]["type"] != "loop_send" || jobs[0]["max_runs"] != json.Number("2") {
		t.Fatalf("durable planned jobs = %#v, %v", jobs, err)
	}
	messages, _, err := store.LoadMessages(context.Background(), yoagent.ConversationLimit)
	if err != nil || len(messages) != 2 || messages[1]["content"] != answer {
		t.Fatalf("persisted planner conversation = %#v, %v", messages, err)
	}
}

func TestNativeYOAgentRejectsInvalidModelIntentPlanWithoutCreatingJob(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	root, configDir := t.TempDir(), t.TempDir()
	builtinDir := filepath.Join(root, "yomux_lib", "yoagent", "builtin_skills")
	if err := os.MkdirAll(builtinDir, 0700); err != nil {
		t.Fatal(err)
	}
	skill := `name: loop-send
kind: workflow
description: Schedule bounded repeated prompts.
tools: [read_activity]
triggers: [every, repeat]
model_intent:
  handler: loop-send
  confirmation: none
  defaults: {interval_seconds: 30, max_runs: 10}
  candidate: {any_of: [every, repeat], require_any: [ask, tell, send, run, have], min_known_sessions: 1, session_scope: request, priority: 100}
  output: {tag: yoagent-loop-plan, schema: loop-send}
`
	if err := os.WriteFile(filepath.Join(builtinDir, "loop-send.yaml"), []byte(skill), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "yomux_lib", "yoagent", "builtin_context"), 0700); err != nil {
		t.Fatal(err)
	}
	agent, err := newNativeYOAgent(store, root, configDir, func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(string, map[string]any) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	agent.run = func(_ context.Context, _ string, _ map[string]any, backend, _ string, _ bool, _ string, _ func(string) error) (map[string]any, string, error) {
		return map[string]any{"backend": backend}, `{"session":"foreign","prompt":"do work","interval_seconds":5,"max_runs":2}`, nil
	}
	answer, status, err := agent.AnswerWithSessions(context.Background(), "Every 5 seconds have agent-a ask for status twice.", "en", "intent-invalid", "intent-invalid", "admin", []string{"agent-a"})
	if err != nil || status["structured_job"] != false || !strings.Contains(answer, "nothing was scheduled or sent") {
		t.Fatalf("invalid plan result = %q / %#v / %v", answer, status, err)
	}
	jobs, _, err := store.LoadJobs(context.Background())
	if err != nil || len(jobs) != 0 {
		t.Fatalf("invalid plan created jobs: %#v, %v", jobs, err)
	}
}

func TestNativeYOAgentResumesAndPersistsProviderSessionInDatabase(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	agent, err := newNativeYOAgent(store, t.TempDir(), t.TempDir(), func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(string, map[string]any) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	var calls int
	agent.run = func(_ context.Context, _ string, _ map[string]any, backend, sessionID string, resume bool, _ string, _ func(string) error) (map[string]any, string, error) {
		calls++
		if backend != "codex" {
			t.Fatalf("backend=%q", backend)
		}
		if calls == 1 && (sessionID != "" || resume) {
			t.Fatalf("first call unexpectedly resumed: session=%q resume=%v", sessionID, resume)
		}
		if calls == 2 && (sessionID != "provider-thread-1" || !resume) {
			t.Fatalf("second call did not resume provider state: session=%q resume=%v", sessionID, resume)
		}
		return map[string]any{"backend": backend, "session_id": "provider-thread-1"}, "answer", nil
	}
	for _, requestID := range []string{"request-1", "request-2"} {
		if _, _, err := agent.Answer(context.Background(), "hello", "en", requestID, requestID, "admin"); err != nil {
			t.Fatal(err)
		}
	}
	sessions, issues, err := store.LoadCLISessions(context.Background(), float64(time.Now().UnixNano())/1e9, yoagent.ManagedBackends())
	if err != nil || len(issues) != 0 || sessions["codex"].SessionID != "provider-thread-1" || calls != 2 {
		t.Fatalf("provider session state=%v issues=%v calls=%d err=%v", sessions, issues, calls, err)
	}
}

func TestNativeYOAgentRejectsUnknownRoleBeforePersisting(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	agent, err := newNativeYOAgent(store, t.TempDir(), t.TempDir(), func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(string, map[string]any) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := agent.Answer(context.Background(), "hello", "en", "request-1", "stream-1", "guest"); err == nil {
		t.Fatal("unrecognized role was admitted")
	}
	messages, _, err := store.LoadMessages(context.Background(), yoagent.ConversationLimit)
	if err != nil || len(messages) != 0 {
		t.Fatalf("unauthorized request changed conversation: %v %v", messages, err)
	}
}

func TestNativeYOAgentPublishesTerminalError(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	var terminal map[string]any
	agent, err := newNativeYOAgent(store, t.TempDir(), t.TempDir(), func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(kind string, payload map[string]any) error {
		if kind == "yoagent_stream_delta" && payload["phase"] == "error" {
			terminal = payload
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	wantErr := errors.New("backend stopped")
	agent.run = func(context.Context, string, map[string]any, string, string, bool, string, func(string) error) (map[string]any, string, error) {
		return nil, "", wantErr
	}
	_, _, err = agent.Answer(context.Background(), "hello", "en", "request-1", "stream-1", "admin")
	if !errors.Is(err, wantErr) || terminal["done"] != true || terminal["error"] != true || terminal["stream_id"] != "stream-1" {
		t.Fatalf("error=%v terminal=%v", err, terminal)
	}
}

func TestNativeYOAgentCancelStopsActiveRequest(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	started := make(chan struct{})
	terminal := make(chan map[string]any, 1)
	agent, err := newNativeYOAgent(store, t.TempDir(), t.TempDir(), func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(kind string, payload map[string]any) error {
		if kind == "yoagent_stream_delta" && payload["phase"] == "error" {
			terminal <- payload
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	agent.run = func(ctx context.Context, _ string, _ map[string]any, _ string, _ string, _ bool, _ string, _ func(string) error) (map[string]any, string, error) {
		close(started)
		<-ctx.Done()
		return nil, "", ctx.Err()
	}
	finished := make(chan error, 1)
	go func() {
		_, _, answerErr := agent.Answer(context.Background(), "hello", "en", "request-1", "stream-1", "admin")
		finished <- answerErr
	}()
	<-started
	if !agent.Cancel("request-1") {
		t.Fatal("active agent request was not cancellable")
	}
	if err := <-finished; !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled request returned %v", err)
	}
	if payload := <-terminal; payload["aborted"] != true || payload["done"] != true {
		t.Fatalf("cancel terminal event=%v", payload)
	}
}

func TestNativeYOAgentReadonlyUserCannotCancelRequest(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	started := make(chan struct{})
	finished := make(chan error, 1)
	agent, err := newNativeYOAgent(store, t.TempDir(), t.TempDir(), func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(string, map[string]any) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	agent.run = func(ctx context.Context, _ string, _ map[string]any, _ string, _ string, _ bool, _ string, _ func(string) error) (map[string]any, string, error) {
		close(started)
		<-ctx.Done()
		return nil, "", ctx.Err()
	}
	go func() {
		_, _, answerErr := agent.Answer(context.Background(), "hello", "en", "request-1", "stream-1", "admin")
		finished <- answerErr
	}()
	<-started
	s := &server{yoagent: agent}
	readonly := httptest.NewRequest(http.MethodPost, "/api/yoagent/chat/request-1/cancel", strings.NewReader(`{"request_id":"request-1"}`))
	readonly = readonly.WithContext(context.WithValue(readonly.Context(), authContextKey{}, authUser{role: "readonly"}))
	rejected := httptest.NewRecorder()
	s.yoagentChatCancel(rejected, readonly)
	if rejected.Code != http.StatusForbidden {
		t.Fatalf("readonly cancel status=%d body=%s", rejected.Code, rejected.Body.String())
	}
	if !agent.Cancel("request-1") {
		t.Fatal("readonly request unexpectedly cancelled the active agent request")
	}
	if err := <-finished; !errors.Is(err, context.Canceled) {
		t.Fatalf("admin cleanup cancellation returned %v", err)
	}
}

func TestNativeAgentAuthEndpointReturnsCachedBrowserContract(t *testing.T) {
	loggedIn := true
	loggedOut := false
	s := &server{nativePageState: nativePageState{
		agentAuth: map[string]pageAgentAuth{
			"claude": {Installed: true, LoggedIn: &loggedIn, Available: true},
			"codex":  {Installed: false, LoggedIn: &loggedOut, Available: false, Reason: "not-on-path"},
		},
		agentAuthExpires: time.Now().Add(time.Minute), agentAuthStaleUntil: time.Now().Add(time.Minute),
	}}
	request := httptest.NewRequest(http.MethodGet, "/api/agent-auth", nil)
	response := httptest.NewRecorder()
	s.agentAuthStatus(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("agent auth status=%d body=%s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	rows, ok := payload["agentAuth"].(map[string]any)
	if !ok || rows["claude"].(map[string]any)["available"] != true || rows["codex"].(map[string]any)["unavailable_reason"] != "not-on-path" {
		t.Fatalf("agent auth response does not match the browser contract: %v", payload)
	}
	if commands, ok := payload["availableAgents"].([]any); !ok || len(commands) == 0 || commands[len(commands)-1] != "term" {
		t.Fatalf("available agent command list=%v", payload["availableAgents"])
	}
}

func TestNativeYOAgentJobsEndpointReturnsRedactedDatabaseRecords(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := store.SaveJob(context.Background(), map[string]any{
		"id": "job-1", "status": "queued", "created_ts": time.Now().Unix(),
		"prompt": "send token=private", "action": map[string]any{"text": "password=private"},
	}); err != nil {
		t.Fatal(err)
	}
	s := &server{yoagentState: store}
	request := httptest.NewRequest(http.MethodGet, "/api/yoagent/jobs", nil)
	response := httptest.NewRecorder()
	s.yoagentJobs(response, request)
	var payload struct {
		Jobs []map[string]any `json:"jobs"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if response.Code != http.StatusOK || strings.Contains(response.Body.String(), "private") || len(payload.Jobs) != 1 || payload.Jobs[0]["prompt"] != "send token=<redacted>" {
		t.Fatalf("YO!agent jobs response=%d %s", response.Code, response.Body.String())
	}
}

func TestRunNativeAgentCancellationStopsProviderProcessGroup(t *testing.T) {
	home := t.TempDir()
	bin := filepath.Join(home, "bin")
	if err := os.Mkdir(bin, 0700); err != nil {
		t.Fatal(err)
	}
	pidPath := filepath.Join(home, "child.pid")
	program := "#!/bin/sh\n/bin/sleep 60 &\necho $! > \"$YOAGENT_CHILD_PID_FILE\"\nwait\n"
	if err := os.WriteFile(filepath.Join(bin, "codex"), []byte(program), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Setenv("PATH", bin)
	t.Setenv("YOAGENT_CHILD_PID_FILE", pidPath)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started := make(chan error, 1)
	go func() {
		_, _, runErr := runNativeAgent(ctx, home, map[string]any{}, "codex", "", false, "prompt", func(string) error { return nil })
		started <- runErr
	}()
	exitDeadline := time.Now().Add(5 * time.Second)
	deadline := time.NewTimer(time.Until(exitDeadline))
	defer deadline.Stop()
	poll := time.NewTicker(10 * time.Millisecond)
	defer poll.Stop()
	var childPID string
	for childPID == "" {
		select {
		case <-deadline.C:
			cancel()
			t.Fatal("provider did not start its child process")
		case <-poll.C:
			data, readErr := os.ReadFile(pidPath)
			if readErr == nil {
				childPID = strings.TrimSpace(string(data))
			} else if !errors.Is(readErr, os.ErrNotExist) {
				cancel()
				t.Fatal(readErr)
			}
		}
	}
	pid, err := strconv.Atoi(childPID)
	if err != nil {
		t.Fatalf("invalid child PID %q: %v", childPID, err)
	}
	// Observe this exact child generation before cancellation: waiting for the provider
	// leader does not wait for a signaled descendant to finish kernel exit processing.
	waitForExit, closeObserver, err := nativeAgentTestObserveProcessExit(pid, exitDeadline)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := closeObserver(); err != nil {
			t.Error(err)
		}
	}()
	cancel()
	select {
	case err := <-started:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("provider cancellation returned %v", err)
		}
	case <-deadline.C:
		t.Fatal("provider cancellation did not finish before the fixture deadline")
	}
	if err := waitForExit(); err != nil {
		t.Fatalf("provider child %d remains after cancellation: %v", pid, err)
	}
}

func TestNativeAgentProcessExitObserverRejectsLiveChild(t *testing.T) {
	child := exec.Command("/bin/sleep", "60")
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := child.Process.Kill(); err != nil && !errors.Is(err, os.ErrProcessDone) {
			t.Error(err)
		}
		var exitError *exec.ExitError
		if err := child.Wait(); !errors.As(err, &exitError) {
			t.Errorf("negative control child did not report its expected termination: %v", err)
		}
	}()
	waitForExit, closeObserver, err := nativeAgentTestObserveProcessExit(child.Process.Pid, time.Now().Add(50*time.Millisecond))
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := closeObserver(); err != nil {
			t.Error(err)
		}
	}()
	if err := waitForExit(); err == nil {
		t.Fatal("a live child satisfied the process-exit barrier")
	}
	if running, err := nativeAgentTestProcessRunning(child.Process.Pid); err != nil || !running {
		t.Fatalf("negative control child is not live: running=%v err=%v", running, err)
	}
}

func nativeAgentTestProcessRunning(pid int) (bool, error) {
	err := syscall.Kill(pid, 0)
	if errors.Is(err, syscall.ESRCH) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if runtime.GOOS != "linux" {
		return true, nil
	}
	stat, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "stat"))
	// The owned child can disappear after kill(0) succeeds but before procfs reads its status.
	if errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ESRCH) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	closing := strings.LastIndexByte(string(stat), ')')
	if closing < 0 || closing+2 >= len(stat) {
		return false, errors.New("provider child has a malformed process status")
	}
	return stat[closing+2] != 'Z' && stat[closing+2] != 'X', nil
}

func TestNativeYOAgentResetCancelsAndWaitsForActiveRequest(t *testing.T) {
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	started := make(chan struct{})
	agent, err := newNativeYOAgent(store, t.TempDir(), t.TempDir(), func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(string, map[string]any) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	agent.run = func(ctx context.Context, _ string, _ map[string]any, _ string, _ string, _ bool, _ string, _ func(string) error) (map[string]any, string, error) {
		close(started)
		<-ctx.Done()
		return nil, "", ctx.Err()
	}
	finished := make(chan error, 1)
	go func() {
		_, _, answerErr := agent.Answer(context.Background(), "hello", "en", "request-1", "stream-1", "admin")
		finished <- answerErr
	}()
	<-started
	backups, err := agent.Reset(context.Background())
	if err != nil || len(backups) != 1 {
		t.Fatalf("reset backups=%v err=%v", backups, err)
	}
	if err := <-finished; !errors.Is(err, context.Canceled) {
		t.Fatalf("reset did not cancel the active request: %v", err)
	}
	messages, issues, err := store.LoadMessages(context.Background(), yoagent.ConversationLimit)
	if err != nil || len(issues) != 0 || len(messages) != 0 {
		t.Fatalf("reset left an active request's conversation: messages=%v issues=%v err=%v", messages, issues, err)
	}
}

func TestNativeYOAgentConsumesClaudeStreamDeltasAndFinalResult(t *testing.T) {
	var answer strings.Builder
	var snapshots []string
	emit := func(value string) error {
		snapshots = append(snapshots, value)
		return nil
	}
	var sessionID string
	for _, event := range []string{
		`{"type":"system","subtype":"init","session_id":"claude-session"}`,
		`{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"hello "}}}`,
		`{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"world"}}}`,
		`{"type":"result","result":"hello world","is_error":false}`,
	} {
		if err := consumeNativeAgentEvent("claude", []byte(event), &answer, &sessionID, emit); err != nil {
			t.Fatal(err)
		}
	}
	if answer.String() != "hello world" || sessionID != "claude-session" || !reflect.DeepEqual(snapshots, []string{"hello ", "hello world"}) {
		t.Fatalf("answer=%q session=%q snapshots=%q", answer.String(), sessionID, snapshots)
	}
}

func TestNativeYOAgentConsumesClaudeStructuredResult(t *testing.T) {
	t.Run("null preserves prose", func(t *testing.T) {
		var answer strings.Builder
		var sessionID string
		if err := consumeNativeAgentEvent("claude", []byte(`{"type":"result","result":"ordinary answer","structured_output":null}`), &answer, &sessionID, func(string) error { return nil }); err != nil || answer.String() != "ordinary answer" {
			t.Fatalf("answer=%q err=%v", answer.String(), err)
		}
	})
	t.Run("error rejects structured output", func(t *testing.T) {
		var answer strings.Builder
		var sessionID string
		err := consumeNativeAgentEvent("claude", []byte(`{"type":"result","is_error":true,"structured_output":{"session":"agent-a"}}`), &answer, &sessionID, func(string) error { t.Fatal("error result was emitted"); return nil })
		if err == nil || answer.Len() != 0 {
			t.Fatalf("answer=%q err=%v", answer.String(), err)
		}
	})
	for _, display := range []string{"", "Plan prepared."} {
		t.Run(display, func(t *testing.T) {
			plan := map[string]any{"session": "agent-a", "prompt": "check status", "interval_seconds": float64(5), "max_runs": float64(2)}
			event, err := json.Marshal(map[string]any{"type": "result", "is_error": false, "result": display, "structured_output": plan})
			if err != nil {
				t.Fatal(err)
			}
			var answer strings.Builder
			answer.WriteString("Preparing a plan.")
			var sessionID, emitted string
			if err := consumeNativeAgentEvent("claude", event, &answer, &sessionID, func(value string) error { emitted = value; return nil }); err != nil {
				t.Fatal(err)
			}
			var decoded map[string]any
			if err := json.Unmarshal([]byte(answer.String()), &decoded); err != nil || !reflect.DeepEqual(decoded, plan) || emitted != answer.String() {
				t.Fatalf("answer=%q emitted=%q decoded=%v err=%v", answer.String(), emitted, decoded, err)
			}
		})
	}
	for _, structured := range []string{`"not an object"`, `[]`} {
		var answer strings.Builder
		var sessionID string
		event := []byte(`{"type":"result","is_error":false,"structured_output":` + structured + `}`)
		if err := consumeNativeAgentEvent("claude", event, &answer, &sessionID, func(string) error { t.Fatal("invalid plan was emitted"); return nil }); err == nil {
			t.Fatalf("accepted invalid structured output %s", structured)
		}
	}
}

func TestRunNativeAgentUsesReadOnlyCodexAndStreamsAnswer(t *testing.T) {
	home := t.TempDir()
	bin := filepath.Join(home, "bin")
	if err := os.Mkdir(bin, 0700); err != nil {
		t.Fatal(err)
	}
	program := "#!/bin/sh\n[ \"$1\" = exec ] || exit 10\nshift\ncase \"$*\" in *\"--sandbox read-only\"*) ;; *) exit 11 ;; esac\nprintf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"provider-thread-1\"}' '{\"type\":\"item.delta\",\"delta\":\"hello \"}' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"hello world\"}}'\n"
	if err := os.WriteFile(filepath.Join(bin, "codex"), []byte(program), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Setenv("PATH", bin)
	var snapshots []string
	status, answer, err := runNativeAgent(context.Background(), home, map[string]any{}, "codex", "", false, "prompt", func(content string) error {
		snapshots = append(snapshots, content)
		return nil
	})
	if err != nil || answer != "hello world" || status["transport"] != "codex-exec" || status["session_id"] != "provider-thread-1" {
		t.Fatalf("status=%v answer=%q err=%v", status, answer, err)
	}
	if !reflect.DeepEqual(snapshots, []string{"hello ", "hello world"}) {
		t.Fatalf("stream snapshots=%q", snapshots)
	}
}

func TestRunNativeAgentUsesClaudeWorkingDirectoryWithoutUnsupportedFlag(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test helper uses a POSIX shell")
	}
	home := t.TempDir()
	bin := filepath.Join(home, "bin")
	if err := os.Mkdir(bin, 0700); err != nil {
		t.Fatal(err)
	}
	program := "#!/bin/sh\n[ \"$PWD\" = \"$YOAGENT_EXPECTED_CWD\" ] || exit 20\n[ \"$1\" = --print ] || exit 21\nfor arg in \"$@\"; do [ \"$arg\" = --cwd ] && exit 22; done\ncase \" $* \" in *\" --model claude-sonnet-5-5 \"*) ;; *) exit 23 ;; esac\ncase \" $* \" in *\" --effort medium \"*) ;; *) exit 24 ;; esac\nprintf '%s\\n' '{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"claude-session\"}' '{\"type\":\"stream_event\",\"event\":{\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"OK\"}}}' '{\"type\":\"result\",\"result\":\"OK\",\"is_error\":false}'\n"
	if err := os.WriteFile(filepath.Join(bin, "claude"), []byte(program), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Setenv("PATH", bin)
	t.Setenv("YOAGENT_EXPECTED_CWD", home)
	status, answer, err := runNativeAgent(context.Background(), home, map[string]any{
		"claude_model": "claude-sonnet-5-5", "claude_effort": "medium",
	}, "claude", "", false, "prompt", func(string) error { return nil })
	if err != nil || answer != "OK" || status["transport"] != "claude-print" || status["session_id"] != "claude-session" {
		t.Fatalf("status=%v answer=%q err=%v", status, answer, err)
	}
}

func TestRunNativeAgentResumesCodexSession(t *testing.T) {
	home := t.TempDir()
	bin := filepath.Join(home, "bin")
	if err := os.Mkdir(bin, 0700); err != nil {
		t.Fatal(err)
	}
	program := "#!/bin/sh\n[ \"$1\" = exec ] && [ \"$2\" = resume ] || exit 10\ncase \"$*\" in *\"provider-thread-1 -\"*) ;; *) exit 11 ;; esac\nprintf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"provider-thread-1\"}' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"continued\"}}'\n"
	if err := os.WriteFile(filepath.Join(bin, "codex"), []byte(program), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Setenv("PATH", bin)
	status, answer, err := runNativeAgent(context.Background(), home, map[string]any{}, "codex", "provider-thread-1", true, "prompt", func(string) error { return nil })
	if err != nil || answer != "continued" || status["session_id"] != "provider-thread-1" || status["resumed"] != true {
		t.Fatalf("status=%v answer=%q err=%v", status, answer, err)
	}
}

func TestNativeChatHTTPYOAgentBridge(t *testing.T) {
	s, mux := nativeChatFixture(t, nil)
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	agent, err := newNativeYOAgent(store, t.TempDir(), t.TempDir(), func(context.Context) (map[string]any, error) {
		return map[string]any{"backend": "codex"}, nil
	}, func(string, map[string]any) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	agent.run = func(_ context.Context, _ string, _ map[string]any, backend, _ string, _ bool, _ string, emit func(string) error) (map[string]any, string, error) {
		if backend != "codex" {
			t.Fatalf("backend=%q", backend)
		}
		if err := emit("Answer from native YO!agent."); err != nil {
			return nil, "", err
		}
		return map[string]any{"backend": backend}, "Answer from native YO!agent.", nil
	}
	s.yoagentState, s.yoagent = store, agent
	s.chat.replies, err = chat.NewAgentReplies(s.chat.service, s.chat.events, func(ctx context.Context, request chat.AgentRequest) (string, error) {
		answer, _, err := agent.Answer(ctx, request.Message, request.Locale, request.RequestID, request.RequestID, request.AccessRole)
		return answer, err
	})
	if err != nil {
		t.Fatal(err)
	}
	chatHTTPCall(t, mux, "GET", "bootstrap?browser_instance_id=browser", "", 200)
	sent := chatHTTPCall(t, mux, "POST", "send", `{"browser_instance_id":"browser","client_message_uuid":"ask-1","body":"Where is the project root?"}`, 201)
	message := sent["message"].(map[string]any)
	reply := chatHTTPCall(t, mux, "POST", "yoagent", `{"browser_instance_id":"browser","message_id":`+strconv.FormatInt(int64(message["id"].(float64)), 10)+`}`, 201)
	if reply["message"].(map[string]any)["body"] != "Answer from native YO!agent." {
		t.Fatalf("YO!chat reply=%v", reply)
	}
	conversation, _, err := store.LoadMessages(context.Background(), yoagent.ConversationLimit)
	if err != nil || len(conversation) != 2 || conversation[0]["content"] != "Where is the project root?" || conversation[1]["content"] != "Answer from native YO!agent." {
		t.Fatalf("YO!agent conversation=%v err=%v", conversation, err)
	}
}
