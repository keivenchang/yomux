// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestNativeYOAgentComposerTextRecognizesEmptyPlaceholderAndProtectsDrafts(t *testing.T) {
	for _, capture := range []string{
		"working output\n› Ask Codex to do anything\n\ngpt-5.5 xhigh",
		"working output\n› Ask Claude to do anything\n\nClaude Code",
	} {
		if text, err := nativeYOAgentComposerText(capture); err != nil || text != "" {
			t.Fatalf("placeholder capture produced text=%q err=%v", text, err)
		}
	}
	for _, capture := range []string{
		"› keep this draft",
		"› first part\n  second part\n\nClaude Code",
	} {
		if text, err := nativeYOAgentComposerText(capture); err == nil || !strings.Contains(err.Error(), "unsent draft") {
			t.Fatalf("draft capture produced text=%q err=%v", text, err)
		}
	}
	if _, err := nativeYOAgentComposerText("agent output with no composer evidence"); err == nil {
		t.Fatal("ambiguous pane capture was accepted")
	}
}

func TestNativeYOAgentActionRejectsComposerDraftBeforePreview(t *testing.T) {
	actions := newNativeYOAgentActions(&server{})
	actions.targetFn = func(context.Context, string) (nativeYOAgentActionTarget, error) {
		_, err := nativeYOAgentComposerText("› operator draft")
		return nativeYOAgentActionTarget{}, err
	}
	if payload, status := actions.preview(context.Background(), "agent-a", "run this", true, false, false); status != http.StatusConflict || payload["reason_code"] != "target-unavailable" {
		t.Fatalf("draft preview = status %d payload %#v", status, payload)
	}
}

func TestNativeYOAgentActionRechecksIdentityAndOnlySubmitsVerifiedComposer(t *testing.T) {
	store := newYOAgentTestStore(t, t.TempDir())
	s := &server{yoagentState: store, events: newEventBroker()}
	actions := newNativeYOAgentActions(s)
	base := nativeYOAgentActionTarget{
		Session: "agent-a", SessionIdentity: "session:1", PaneTarget: "%7",
		ProcessInstanceID: "process:1", AgentKind: "codex", Status: stats.AgentStatusIdle,
		VisibleText: "› Ask Codex to do anything\n\ngpt-5.5 xhigh",
	}
	current := base
	var targetCalls, pasteCalls, submitCalls int
	actions.targetFn = func(context.Context, string) (nativeYOAgentActionTarget, error) {
		targetCalls++
		return current, nil
	}
	actions.pasteFn = func(_ context.Context, pane, text string) error {
		pasteCalls++
		if pane != "%7" || text != "inspect status" {
			t.Fatalf("paste pane=%q text=%q", pane, text)
		}
		current.VisibleText = "› inspect status"
		return nil
	}
	actions.submitFn = func(_ context.Context, pane string) error {
		submitCalls++
		if pane != "%7" {
			t.Fatalf("submit pane=%q", pane)
		}
		return nil
	}
	preview, status := actions.preview(context.Background(), "agent-a", "inspect status", true, false, false)
	if status != http.StatusOK {
		t.Fatalf("preview status=%d payload=%#v", status, preview)
	}
	id, _ := preview["id"].(string)
	if id == "" {
		t.Fatalf("preview has no id: %#v", preview)
	}
	result, status := actions.execute(context.Background(), id)
	if status != http.StatusOK || result["sent"] != true || pasteCalls != 1 || submitCalls != 1 || targetCalls != 3 {
		t.Fatalf("execution status=%d result=%#v calls(target=%d paste=%d submit=%d)", status, result, targetCalls, pasteCalls, submitCalls)
	}

	current = base
	stalePreview, status := actions.preview(context.Background(), "agent-a", "inspect status", true, false, false)
	if status != http.StatusOK {
		t.Fatalf("stale preview status=%d payload=%#v", status, stalePreview)
	}
	staleID, _ := stalePreview["id"].(string)
	current.ProcessInstanceID = "process:replaced"
	result, status = actions.execute(context.Background(), staleID)
	if status != http.StatusConflict || result["reason_code"] != "stale-target" || pasteCalls != 1 || submitCalls != 1 {
		t.Fatalf("stale execution status=%d result=%#v calls(paste=%d submit=%d)", status, result, pasteCalls, submitCalls)
	}
	if time.Since(actions.previews[staleID].CreatedAt) > nativeYOAgentActionPreviewTTL {
		t.Fatal("fresh stale-target preview unexpectedly expired during test")
	}
}

func TestNativeYOAgentResultWatchPersistsTranscriptResultAndClearCancelsWait(t *testing.T) {
	root := t.TempDir()
	transcript := filepath.Join(root, "codex.jsonl")
	initial := []byte("{\"payload\":{\"type\":\"user_message\",\"message\":\"old prompt\"}}\n")
	if err := os.WriteFile(transcript, initial, 0o600); err != nil {
		t.Fatal(err)
	}
	reader := &server{}
	position, err := reader.checkpointTranscript(context.Background(), transcript)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(transcript, append(initial, []byte("{\"payload\":{\"type\":\"agent_message\",\"message\":\"fresh result\"}}\n")...), 0o600); err != nil {
		t.Fatal(err)
	}
	items, _, err := reader.readTranscriptItems(context.Background(), transcript, position)
	if err != nil || len(items) != 1 || items[0]["role"] != "assistant" || items[0]["text"] != "fresh result" {
		t.Fatalf("fresh transcript items=%#v err=%v", items, err)
	}
	store := newYOAgentTestStore(t, filepath.Join(root, "state"))
	var observations atomic.Int64
	s := &server{root: testsupport.RepoRoot(t), yoagentState: store, events: newEventBroker(), agentProcessSnapshot: func(context.Context, int) (stats.AgentStatusProcess, error) {
		return stats.AgentStatusProcess{Alive: true, StartedIdentity: "started:1", Command: "codex", Executable: "/usr/bin/codex"}, nil
	}, agentStatusClassifier: func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult {
		status := stats.AgentStatusRun
		if observations.Add(1) > 1 {
			status = stats.AgentStatusIdle
		}
		return stats.AgentStatusResult{Status: status}
	}}
	actions := newNativeYOAgentActions(s)
	target := nativeYOAgentActionTarget{
		Session: "agent-a", PaneTarget: "%7", AgentKind: "codex", AgentPID: 42,
		AgentCommand: "codex", AgentExecutable: "/usr/bin/codex", AgentTranscript: transcript,
		ProcessStarted: "started:1", CWD: root,
	}
	wait := nativeYOAgentActionWait{ID: "yar_fixture", Session: "agent-a", Transcript: transcript, StartedAt: time.Now(), Target: target, Position: position}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	actions.mu.Lock()
	actions.waits[wait.ID] = wait
	actions.mu.Unlock()
	go actions.watchResult(ctx, wait)

	deadline := time.After(5 * time.Second)
	for {
		messages, _, err := store.LoadMessages(context.Background(), 10)
		if err != nil {
			t.Fatal(err)
		}
		if len(messages) > 0 {
			if messages[len(messages)-1]["kind"] != "agent_result" || messages[len(messages)-1]["session"] != "agent-a" || !strings.Contains(messages[len(messages)-1]["content"].(string), "fresh result") {
				t.Fatalf("result message = %#v", messages[len(messages)-1])
			}
			break
		}
		select {
		case <-deadline:
			t.Fatal("result watcher did not persist the appended transcript message")
		case <-time.After(20 * time.Millisecond):
		}
	}

	waitCtx, waitCancel := context.WithCancel(context.Background())
	actions.mu.Lock()
	actions.waits["yar_clear"] = nativeYOAgentActionWait{ID: "yar_clear", Session: "agent-a", StartedAt: time.Now(), Cancel: waitCancel}
	actions.mu.Unlock()
	if !actions.clearWait("yar_clear") || len(actions.pendingWaits()) != 0 {
		t.Fatal("clear did not remove the pending result wait")
	}
	select {
	case <-waitCtx.Done():
	case <-time.After(time.Second):
		t.Fatal("clear did not cancel the pending result wait")
	}
	actions.close()
}

func TestNativeYOAgentQueuedActionCancellation(t *testing.T) {
	for _, mode := range []string{"cancel", "deadline", "shutdown"} {
		t.Run(mode, func(t *testing.T) {
			actions := newNativeYOAgentActions(&server{})
			defer actions.close()
			actions.previews["fixture"] = nativeYOAgentActionPreview{ID: "fixture", CreatedAt: time.Now(), Session: "agent-a"}
			var targets atomic.Int64
			actions.targetFn = func(context.Context, string) (nativeYOAgentActionTarget, error) {
				targets.Add(1)
				return nativeYOAgentActionTarget{}, os.ErrNotExist
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "deadline" {
				ctx, cancel = context.WithDeadline(ctx, time.Now().Add(-time.Second))
				defer cancel()
			} else if mode == "cancel" {
				cancel()
			} else {
				actions.close()
			}
			actions.sendMu.Lock()
			done := make(chan int, 1)
			go func() { _, status := actions.execute(ctx, "fixture"); done <- status }()
			select {
			case status := <-done:
				actions.sendMu.Unlock()
				if status != http.StatusRequestTimeout || targets.Load() != 0 {
					t.Fatalf("cancelled send status=%d targets=%d", status, targets.Load())
				}
			case <-time.After(time.Second):
				actions.sendMu.Unlock()
				<-done
				t.Fatal("cancelled action waited for an unrelated send")
			}
			if _, found := actions.previews["fixture"]; !found {
				t.Fatal("cancelled queued request consumed its preview")
			}
		})
	}
}

func TestNativeYOAgentConcurrentActionPreviewIsSingleUse(t *testing.T) {
	store := newYOAgentTestStore(t, t.TempDir())
	actions := newNativeYOAgentActions(&server{root: testsupport.RepoRoot(t), yoagentState: store, events: newEventBroker()})
	defer actions.close()
	target := nativeYOAgentActionTarget{Session: "agent-a", SessionIdentity: "one", PaneTarget: "%1", ProcessInstanceID: "one", AgentKind: "codex", VisibleText: "› inspect"}
	actions.previews["fixture"] = nativeYOAgentActionPreview{ID: "fixture", CreatedAt: time.Now(), Session: "agent-a", Target: target, Text: "inspect", Submit: true}
	actions.targetFn = func(context.Context, string) (nativeYOAgentActionTarget, error) { return target, nil }
	entered, release := make(chan struct{}, 2), make(chan struct{})
	defer func() {
		select {
		case <-release:
		default:
			close(release)
		}
	}()
	var pastes, submits atomic.Int64
	actions.pasteFn = func(context.Context, string, string) error {
		if pastes.Add(1) == 1 {
			entered <- struct{}{}
			<-release
		}
		return nil
	}
	actions.submitFn = func(context.Context, string) error { submits.Add(1); return nil }
	admitted := make(chan struct{}, 2)
	actions.now = func() time.Time { admitted <- struct{}{}; return time.Now() }
	results := make(chan int, 2)
	go func() { _, status := actions.execute(context.Background(), "fixture"); results <- status }()
	<-entered
	<-admitted
	go func() { _, status := actions.execute(context.Background(), "fixture"); results <- status }()
	// Hold the first send across the second request's admission attempt. The
	// old owner copied the preview here, before waiting for the send lock.
	select {
	case <-admitted:
	case <-time.After(100 * time.Millisecond):
	}
	close(release)
	first, second := <-results, <-results
	if pastes.Load() != 1 || submits.Load() != 1 || !((first == http.StatusOK && second == http.StatusNotFound) || (second == http.StatusOK && first == http.StatusNotFound)) {
		t.Fatalf("single-use preview pastes=%d submits=%d statuses=%d,%d", pastes.Load(), submits.Load(), first, second)
	}
}

func TestNativeYOAgentResultWatchReconcilesTranscriptGeneration(t *testing.T) {
	for _, mode := range []string{"truncate", "replace"} {
		t.Run(mode, func(t *testing.T) {
			store := newYOAgentTestStore(t, t.TempDir())
			path := filepath.Join(t.TempDir(), "transcript.jsonl")
			if err := os.WriteFile(path, []byte(strings.Repeat("old record\n", 100)), 0o600); err != nil {
				t.Fatal(err)
			}
			service := &server{root: testsupport.RepoRoot(t), yoagentState: store, events: newEventBroker(), agentProcessSnapshot: func(context.Context, int) (stats.AgentStatusProcess, error) {
				return stats.AgentStatusProcess{Alive: true, StartedIdentity: "one", Command: "codex", Executable: "codex"}, nil
			}, agentStatusClassifier: func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult {
				return stats.AgentStatusResult{Status: stats.AgentStatusIdle}
			}}
			position, err := service.checkpointTranscript(context.Background(), path)
			if err != nil {
				t.Fatal(err)
			}
			fresh := []byte(`{"payload":{"type":"agent_message","message":"fresh result"}}` + "\n")
			destination := path
			if mode == "replace" {
				destination += ".new"
			}
			if err := os.WriteFile(destination, fresh, 0o600); err != nil {
				t.Fatal(err)
			}
			if mode == "replace" {
				if err := os.Rename(destination, path); err != nil {
					t.Fatal(err)
				}
			}
			actions := newNativeYOAgentActions(service)
			defer actions.close()
			var ticks atomic.Int64
			actions.now = func() time.Time { return time.Now().Add(time.Duration(ticks.Add(1)) * 30 * time.Second) }
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			wait := nativeYOAgentActionWait{ID: "fixture", Session: "agent-a", Transcript: path, Position: position, StartedAt: time.Now().Add(-time.Minute), Cancel: cancel,
				Target: nativeYOAgentActionTarget{AgentPID: 1, AgentKind: "codex", AgentCommand: "codex", AgentExecutable: "codex", ProcessStarted: "one"}}
			actions.waits[wait.ID] = wait
			done := make(chan struct{})
			go func() { defer close(done); actions.watchResult(ctx, wait) }()
			select {
			case <-done:
			case <-time.After(time.Second):
				cancel()
				<-done
				t.Fatal("result watcher did not reconcile the new transcript generation")
			}
			messages, _, err := store.LoadMessages(context.Background(), 10)
			if err != nil || len(messages) != 1 || !strings.Contains(messages[0]["content"].(string), "fresh result") {
				t.Fatalf("result messages=%v err=%v", messages, err)
			}
		})
	}
}

func TestNativeYOAgentResultCheckpointExcludesPreSendPartialRecord(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	prefix := `{"payload":{"type":"agent_message","message":"old`
	if err := os.WriteFile(path, []byte(prefix), 0o600); err != nil {
		t.Fatal(err)
	}
	service := &server{}
	position, err := service.checkpointTranscript(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	data := prefix + ` answer"}}` + "\n" + `{"payload":{"type":"agent_message","message":"new answer"}}` + "\n"
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
	items, batch, err := service.readTranscriptItems(context.Background(), path, position)
	if err != nil || batch.Reset || len(items) != 1 || items[0]["text"] != "new answer" {
		t.Fatalf("pre-send partial result admitted: items=%v reset=%v err=%v", items, batch.Reset, err)
	}
}

func TestNativeYOAgentResultWatchDrainsBacklogAndDiscardsResetProjection(t *testing.T) {
	for _, mode := range []string{"backlog", "reset"} {
		t.Run(mode, func(t *testing.T) {
			store := newYOAgentTestStore(t, t.TempDir())
			path := filepath.Join(t.TempDir(), "transcript.jsonl")
			if err := os.WriteFile(path, nil, 0o600); err != nil {
				t.Fatal(err)
			}
			service := &server{root: testsupport.RepoRoot(t), yoagentState: store, events: newEventBroker()}
			position, err := service.checkpointTranscript(context.Background(), path)
			if err != nil {
				t.Fatal(err)
			}
			line := func(text string) string {
				return `{"payload":{"type":"agent_message","message":"` + text + `"}}` + "\n"
			}
			data := line("stale generation")
			if mode == "backlog" {
				data = strings.Repeat(line("intermediate"), maxContextTranscriptLines) + line("final result")
			}
			if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
				t.Fatal(err)
			}
			service.agentProcessSnapshot = func(context.Context, int) (stats.AgentStatusProcess, error) {
				return stats.AgentStatusProcess{Alive: true, StartedIdentity: "one", Command: "codex", Executable: "codex"}, nil
			}
			var observations atomic.Int64
			service.agentStatusClassifier = func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult {
				if mode == "reset" {
					switch observations.Add(1) {
					case 1:
						if err := os.WriteFile(path, []byte(`{"payload":{"type":"user_message","message":"new generation"}}`+"\n"), 0o600); err != nil {
							panic(err)
						}
						return stats.AgentStatusResult{Status: stats.AgentStatusRun}
					case 2:
						file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
						if err != nil {
							panic(err)
						}
						_, err = file.WriteString(line("final result"))
						closeErr := file.Close()
						if err != nil || closeErr != nil {
							panic(fmt.Sprintf("append=%v close=%v", err, closeErr))
						}
					}
				}
				return stats.AgentStatusResult{Status: stats.AgentStatusIdle}
			}
			actions := newNativeYOAgentActions(service)
			defer actions.close()
			var ticks atomic.Int64
			actions.now = func() time.Time { return time.Now().Add(time.Duration(ticks.Add(1)) * 30 * time.Second) }
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			wait := nativeYOAgentActionWait{ID: "fixture", Session: "agent-a", Transcript: path, Position: position, StartedAt: time.Now().Add(-time.Minute), Cancel: cancel,
				Target: nativeYOAgentActionTarget{AgentPID: 1, AgentKind: "codex", AgentCommand: "codex", AgentExecutable: "codex", ProcessStarted: "one"}}
			actions.waits[wait.ID] = wait
			done := make(chan struct{})
			go func() { defer close(done); actions.watchResult(ctx, wait) }()
			select {
			case <-done:
			case <-time.After(4 * time.Second):
				cancel()
				<-done
				t.Fatal("result backlog did not converge")
			}
			messages, _, err := store.LoadMessages(context.Background(), 10)
			if err != nil || len(messages) != 1 || !strings.Contains(messages[0]["content"].(string), "final result") || strings.Contains(messages[0]["content"].(string), "stale generation") {
				t.Fatalf("incorrect result projection messages=%v err=%v", messages, err)
			}
		})
	}
}
