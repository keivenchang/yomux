// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func codexTurnEvent(kind, turn string) string {
	return fmt.Sprintf("{\"type\":\"event_msg\",\"timestamp\":\"2026-10-28T05:00:00Z\",\"payload\":{\"type\":%q,\"turn_id\":%q}}\n", kind, turn)
}

func TestCodexPaginatedTurnUsesProcessWriterIdentity(t *testing.T) {
	home := t.TempDir()
	root := filepath.Join(home, "sessions")
	lockDir := filepath.Join(home, "thread-writer-locks")
	targets := []string{filepath.Join(lockDir, "thread-a.lock")}
	db, err := sql.Open("sqlite3", filepath.Join(home, "thread_history_1.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec(`CREATE TABLE thread_turns(thread_id TEXT, turn_id TEXT, rollout_ordinal INTEGER, status TEXT, started_at INTEGER); CREATE UNIQUE INDEX idx_thread_turns_page ON thread_turns(thread_id, rollout_ordinal); INSERT INTO thread_turns VALUES ('thread-a','a',1,'completed',100),('thread-a','b',2,'inProgress',200),('thread-other','c',100,'completed',300)`); err != nil {
		t.Fatal(err)
	}
	if got, known := codexPaginatedTurnState(context.Background(), root, targets, 150); !known || got != AgentStatusRun {
		t.Fatalf("exact thread became %+v known=%v", got, known)
	}
	if got, known := codexPaginatedTurnState(context.Background(), root+string(filepath.Separator), targets, 150); !known || got != AgentStatusRun {
		t.Fatalf("trailing separator changed identity: %s known=%v", got, known)
	}
	if got, _ := codexPaginatedTurnState(context.Background(), root, targets, 250); got != AgentStatusUnavailable {
		t.Fatalf("stale process turn became %s", got)
	}
	if thread := codexWriterThread(root, append(targets, filepath.Join(lockDir, "thread-other.lock"))); thread != "" {
		t.Fatalf("ambiguous writer locks picked %q", thread)
	}
	if thread := codexWriterThread(root, []string{filepath.Join(home, "foreign", "thread-a.lock"), filepath.Join(lockDir, ".coordination.lock")}); thread != "" {
		t.Fatalf("foreign or coordination lock picked %q", thread)
	}
	for _, tc := range []struct {
		status string
		want   AgentStatus
	}{{"completed", AgentStatusIdle}, {"interrupted", AgentStatusIdle}, {"failed", AgentStatusError}, {"future-status", AgentStatusUnavailable}} {
		if _, err := db.Exec("UPDATE thread_turns SET status=? WHERE thread_id=? AND turn_id=?", tc.status, "thread-a", "b"); err != nil {
			t.Fatal(err)
		}
		if got, _ := codexPaginatedTurnState(context.Background(), root, targets, 150); got != tc.want {
			t.Errorf("%s became %s", tc.status, got)
		}
	}
}

func TestCodexTurnTailState(t *testing.T) {
	start := codexTurnEvent("task_started", "a")
	item := codexTurnEvent("item_completed", "a")
	complete := codexTurnEvent("task_complete", "a")
	for _, tc := range []struct {
		name, text string
		want       AgentStatus
	}{
		{"start", start, AgentStatusRun},
		{"start outside tail", item, AgentStatusRun},
		{"streaming commentary", start + "{\"type\":\"response_item\",\"payload\":{\"type\":\"message\",\"role\":\"assistant\",\"phase\":\"commentary\"}}\n" + item, AgentStatusRun},
		{"empty completion message", item + complete, AgentStatusIdle},
		{"late completed item", item + complete + item, AgentStatusIdle},
		{"completed outside tail", complete, AgentStatusIdle},
		{"abort", item + codexTurnEvent("turn_aborted", "a"), AgentStatusIdle},
		{"wrong turn completion", item + codexTurnEvent("task_complete", "old"), AgentStatusRun},
		{"next turn", item + complete + codexTurnEvent("item_started", "b"), AgentStatusRun},
		{"partial completion", item + strings.TrimSuffix(complete, "\n"), AgentStatusRun},
		{"no turn evidence", "{}\n", AgentStatusUnavailable},
		{"malformed tail", item + "not-json\n", AgentStatusRun},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := codexTurnTailState(tc.text, 1); got != tc.want {
				t.Fatalf("state=%s want %s", got, tc.want)
			}
		})
	}
	if got := codexTurnTailState(item, 2e9); got != AgentStatusUnavailable {
		t.Fatalf("a previous process's transcript became active: %s", got)
	}
}

func TestCodexTurnReaderBoundedLongTurn(t *testing.T) {
	root := t.TempDir()
	if got := CodexTurnStateReader(root)(context.Background(), os.Getpid(), 1); got != AgentStatusUnavailable {
		t.Fatalf("no process-owned rollout must not imply idle: %s", got)
	}
	file, err := os.CreateTemp(root, "rollout-*.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if _, err := file.WriteString(codexTurnEvent("task_started", "a") + strings.Repeat("{}\n", maxCodexTurnTailBytes) + codexTurnEvent("item_completed", "a")); err != nil {
		t.Fatal(err)
	}
	read := CodexTurnStateReader(root)
	if got := read(context.Background(), os.Getpid(), 1); got != AgentStatusRun {
		t.Fatalf("long active turn=%s", got)
	}
	if _, err := file.WriteString(codexTurnEvent("task_complete", "a")); err != nil {
		t.Fatal(err)
	}
	if got := read(context.Background(), os.Getpid(), 1); got != AgentStatusIdle {
		t.Fatalf("completed turn=%s", got)
	}
}

func TestAgentStatusCodexStreamingCommentaryWithoutBusyRow(t *testing.T) {
	for _, tc := range []struct {
		pane    string
		unknown AgentStatus
	}{
		{"• The false completion is confirmed in both metadata and Chromium.\n\n  1 background terminal running · /ps to view · /stop to close\n\n› Ask Codex to do anything", AgentStatusUnavailable},
		{"Conversation interrupted - use /feedback\n• fresh streamed commentary\n› Ask Codex to do anything", AgentStatusUnavailable},
		{"Worked for 3s\n• commentary from the next turn\n› Ask Codex to do anything", AgentStatusTransition},
	} {
		pane := tc.pane
		for _, active := range []AgentStatus{AgentStatusRun, AgentStatusIdle, AgentStatusUnavailable, AgentStatusError} {
			got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PID: 1, PaneTarget: "%1"}, AgentStatusSources{
				CapturePane: func(context.Context, string) (AgentStatusPane, error) { return AgentStatusPane{VisibleText: pane}, nil },
				CodexTurn:   func(context.Context, int, float64) AgentStatus { return active },
			})
			if active == AgentStatusRun && got.Status != AgentStatusRun || active == AgentStatusUnavailable && got.Status != tc.unknown || active == AgentStatusIdle && got.Status == AgentStatusRun {
				t.Errorf("turn=%s pane=%q result=%+v", active, pane, got)
			}
			if active == AgentStatusError && got.Status != AgentStatusError {
				t.Errorf("failed turn became %+v", got)
			}
		}
	}
	for _, pane := range []string{"Approval required\nWould you like to run the following?\nEnter to select · Esc to cancel", "Error: model capacity reached"} {
		baseline := classifyVisibleAgentText(pane)
		got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PID: 1, PaneTarget: "%1"}, AgentStatusSources{
			CapturePane: func(context.Context, string) (AgentStatusPane, error) { return AgentStatusPane{VisibleText: pane}, nil },
			CodexTurn:   func(context.Context, int, float64) AgentStatus { return AgentStatusRun },
		})
		if got.Status != baseline.Status {
			t.Errorf("turn masked attention: %+v want %s", got, baseline.Status)
		}
	}
}

func TestCodexUnknownHistoryPreservesPositiveCompletionAndAcknowledgement(t *testing.T) {
	for _, tc := range []struct {
		name, text, raw string
		ack             bool
		stopped         int64
		want            AgentStatus
	}{
		{"goal", "OpenAI Codex\nGoal achieved", "", false, 0, AgentStatusTransition},
		{"acknowledged goal", "OpenAI Codex\nGoal achieved", "", true, 95, AgentStatusIdle},
		{"expired goal", "OpenAI Codex\nGoal achieved", "", false, 10, AgentStatusIdle},
		{"duration footer", "Worked for 3s\n›", "", false, 0, AgentStatusTransition},
		{"structured", "done", "transition", false, 0, AgentStatusTransition},
		{"acknowledged structured", "done", "transition", true, 95, AgentStatusIdle},
		{"ambiguous composer", "› Ask Codex to do anything", "", false, 0, AgentStatusUnavailable},
		{"inferred transition", "› Ask Codex to do anything", "", false, 95, AgentStatusUnavailable},
		{"stale completion prose", "Finished waiting\n└ No agents completed yet\n› Ask Codex to do anything", "", false, 0, AgentStatusUnavailable},
	} {
		t.Run(tc.name, func(t *testing.T) {
			request := AgentStatusRequest{Kind: "codex", PID: 1, PaneTarget: "%1", Acknowledged: tc.ack, Now: time.Unix(100, 0)}
			if tc.stopped != 0 {
				request.WorkingStoppedAt = time.Unix(tc.stopped, 0)
			}
			got := ClassifyAgentStatus(context.Background(), request, AgentStatusSources{
				CapturePane: func(context.Context, string) (AgentStatusPane, error) {
					return AgentStatusPane{VisibleText: tc.text, Status: tc.raw}, nil
				},
				CodexTurn: func(context.Context, int, float64) AgentStatus { return AgentStatusUnavailable },
			})
			if got.Status != tc.want {
				t.Fatalf("got %+v want %s", got, tc.want)
			}
		})
	}
}
