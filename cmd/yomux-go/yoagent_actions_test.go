// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/testsupport"
	"github.com/keivenchang/yomux/internal/yoagent"
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
	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
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
	if err := os.WriteFile(transcript, append(initial, []byte("{\"payload\":{\"type\":\"agent_message\",\"message\":\"fresh result\"}}\n")...), 0o600); err != nil {
		t.Fatal(err)
	}
	items, _, err := readTranscriptItemsSince(transcript, int64(len(initial)))
	if err != nil || len(items) != 1 || items[0]["role"] != "assistant" || items[0]["text"] != "fresh result" {
		t.Fatalf("fresh transcript items=%#v err=%v", items, err)
	}
	store, err := yoagent.NewStore(filepath.Join(root, "state"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
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
	wait := nativeYOAgentActionWait{ID: "yar_fixture", Session: "agent-a", Transcript: transcript, StartedAt: time.Now(), Target: target, Offset: int64(len(initial))}
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
