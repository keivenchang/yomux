// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"testing"

	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/yoagent"
)

func TestNativeYOAgentJobRuntimeRequiresFreshKnownSession(t *testing.T) {
	reads := 0
	s := &server{tmuxMetadataState: tmuxMetadataState{tmuxSessionRosterFn: func() ([]string, error) {
		reads++
		return []string{"agent-a"}, nil
	}}}
	runtime := &nativeYOAgentJobRuntime{server: s}
	job := map[string]any{"id": "job-1", "status": "queued", "type": "notify_session_idle", "target": map[string]any{"session": "agent-missing"}, "predicate": map[string]any{"type": "session_idle"}}
	if err := runtime.BeginCycle(context.Background(), []map[string]any{job}); err != nil {
		t.Fatal(err)
	}
	observation, err := runtime.Observe(context.Background(), job)
	if err != nil || observation.State != "missing" || reads != 1 {
		t.Fatalf("observation=%#v reads=%d err=%v", observation, reads, err)
	}
}

func TestNativeYOAgentSessionStateUsesMostUrgentObservedState(t *testing.T) {
	tests := []struct {
		name  string
		input []map[string]any
		want  string
	}{
		{name: "no verified agent", want: "unavailable"},
		{name: "all idle", input: []map[string]any{{"status": "idle"}, {"status": "idle"}}, want: "idle"},
		{name: "question takes precedence", input: []map[string]any{{"status": "blocked"}, {"status": "needs-input", "attention_kind": "question"}}, want: "needs-input"},
		{name: "approval is blocked", input: []map[string]any{{"status": "needs-input", "attention_kind": "approval"}}, want: "blocked"},
		{name: "ambiguous ask fails closed", input: []map[string]any{{"status": "needs-input"}}, want: "unavailable"},
		{name: "working prevents idle", input: []map[string]any{{"status": "idle"}, {"status": "working"}}, want: "working"},
		{name: "unknown state fails closed", input: []map[string]any{{"status": "cooldown"}}, want: "unavailable"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := nativeYOAgentSessionState(map[string]any{"agents": test.input})
			if got != test.want {
				t.Fatalf("state=%q want %q", got, test.want)
			}
		})
	}
}

func TestNativeYOAgentRosterPredicatesWaitForEverySessionToBeIdle(t *testing.T) {
	tests := []struct {
		name      string
		predicate string
		states    map[string]string
	}{
		{name: "all idle", predicate: "all_idle", states: map[string]string{"agent-a": "idle", "agent-b": "working"}},
		{name: "all calm", predicate: "all_calm", states: map[string]string{"agent-a": "idle", "agent-b": "needs-input"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runtime := &nativeYOAgentJobRuntime{server: &server{}, known: map[string]bool{"agent-a": true, "agent-b": true}, status: test.states}
			job := map[string]any{
				"type": "notify_all_idle", "target": map[string]any{"roster": []any{"agent-a", "agent-b"}},
				"predicate": map[string]any{"type": test.predicate},
			}
			observation, err := runtime.Observe(context.Background(), job)
			if err != nil || observation.Ready || observation.State != "waiting" {
				t.Fatalf("observation=%#v err=%v", observation, err)
			}
		})
	}
}

func TestNativeYOAgentJobRuntimePublishesNotificationAndUsesVerifiedSendOwner(t *testing.T) {
	events := newEventBroker()
	runtime := &nativeYOAgentJobRuntime{server: &server{events: events}}
	job := map[string]any{
		"id": "job-1", "type": "notify_session_idle", "target": map[string]any{"session": "agent-a"},
		"action": map[string]any{"type": "notify_user", "message": "agent is idle"},
	}
	execution, err := runtime.Execute(context.Background(), job)
	if err != nil || !execution.Attempted || !execution.Succeeded {
		t.Fatalf("notification execution=%#v err=%v", execution, err)
	}
	events.mu.Lock()
	if len(events.events) != 1 || events.events[0].Type != "yoagent_jobs_changed" || events.events[0].Payload["notification"] == nil {
		events.mu.Unlock()
		t.Fatalf("notification events=%#v", events.events)
	}
	events.mu.Unlock()

	store, err := yoagent.NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	current := nativeYOAgentActionTarget{
		Session: "agent-a", SessionIdentity: "session:1", PaneTarget: "%7",
		ProcessInstanceID: "process:1", AgentKind: "codex", Status: stats.AgentStatusIdle,
		VisibleText: "› Ask Codex to do anything",
	}
	actions := newNativeYOAgentActions(&server{yoagentState: store, events: newEventBroker()})
	actions.targetFn = func(context.Context, string) (nativeYOAgentActionTarget, error) { return current, nil }
	actions.pasteFn = func(_ context.Context, pane, text string) error {
		if pane != "%7" || text != "do work" {
			t.Fatalf("scheduled prompt pane=%q text=%q", pane, text)
		}
		current.VisibleText = "› do work"
		return nil
	}
	actions.submitFn = func(context.Context, string) error { return nil }
	runtime.server.agentActions = actions
	job["action"] = map[string]any{"type": "send_prompt", "session": "agent-a", "text": "do work", "submit": true}
	execution, err = runtime.Execute(context.Background(), job)
	if err != nil || !execution.Attempted || !execution.Succeeded || execution.Result["send"] == nil {
		t.Fatalf("verified send execution=%#v err=%v", execution, err)
	}
}
