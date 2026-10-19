package stats

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func TestLinuxAgentPaneCaptureUsesVisibleScreenOnly(t *testing.T) {
	got := linuxAgentPaneCaptureArgs("%26")
	want := []string{"capture-pane", "-p", "-J", "-t", "%26"}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("capture args = %v, want %v", got, want)
	}
	for _, value := range got {
		if value == "-S" || value == "-12" {
			t.Fatalf("capture args still include scrollback selector: %v", got)
		}
	}
}

func TestSyntheticAgentStatusRequiresFixtureTmuxSocket(t *testing.T) {
	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	sources := SyntheticAgentStatusSources("")
	if _, err := sources.CapturePane(context.Background(), "%1"); !errors.Is(err, errSyntheticTmuxSocketUnset) {
		t.Fatalf("synthetic pane capture error = %v, want fixture socket admission error", err)
	}
}

func TestClassifyAgentStatusUsesBoundedSyntheticPaneEvidence(t *testing.T) {
	tests := []struct {
		name string
		kind string
		text string
		want AgentStatus
	}{
		{"claude asks", "claude", "Allow this command?\n1. Yes\n2. No\nEnter to select", AgentStatusAsk},
		{"codex runs", "codex", "Working\nEsc to interrupt\n3 tool uses", AgentStatusRun},
		{"claude transitions", "claude", "Goal achieved (12s)", AgentStatusTransition},
		{"codex idle", "codex", "Ask anything (/? for help)", AgentStatusIdle},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: tc.kind, PID: 10, PaneTarget: "%1"}, AgentStatusSources{
				Process: func(context.Context, int) (AgentStatusProcess, error) { return AgentStatusProcess{Alive: true}, nil },
				CapturePane: func(context.Context, string) (AgentStatusPane, error) {
					return AgentStatusPane{VisibleText: tc.text}, nil
				},
			})
			if got.Status != tc.want {
				t.Fatalf("status = %q, want %q (%+v)", got.Status, tc.want, got)
			}
		})
	}
}

func TestClassifyAgentStatusRealShapedClaudeCodexOpenCodePanes(t *testing.T) {
	tests := []struct {
		name string
		kind string
		text string
		want AgentStatus
	}{
		{"claude running", "claude", "Claude Code\n\nWorking...\nEsc to interrupt", AgentStatusRun},
		{"codex done", "codex", "OpenAI Codex\n\nTask completed successfully.\n› ", AgentStatusTransition},
		{"claude awaiting", "claude", "Claude Code\n\nAllow this command?\n1. Yes\n2. No\nEnter to select", AgentStatusAsk},
		{"codex error", "codex", "OpenAI Codex\n\nError: command failed with exit code 1", AgentStatusError},
		{"opencode ambiguous", "opencode", "OpenCode\n\nassistant output without a prompt", AgentStatusUnavailable},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			sources := AgentStatusSources{CapturePane: func(context.Context, string) (AgentStatusPane, error) {
				return AgentStatusPane{VisibleText: tc.text}, nil
			}}
			if tc.kind == "opencode" {
				sources.OpenCodeState = func(context.Context, AgentStatusRequest) (string, []string, error) {
					return "unknown", nil, nil
				}
			}
			got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: tc.kind, PaneTarget: "%1"}, sources)
			if got.Status != tc.want {
				t.Fatalf("status = %q, want %q (%+v)", got.Status, tc.want, got)
			}
		})
	}
}

func TestCodexCapacityNoticeIsBlockedAndClearsWithNewerOutput(t *testing.T) {
	notice := "■ Selected model is at capacity. Please try a different model."
	current := notice + "\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	stale := notice + "\nCompleted successfully\n\n›\n\n  gpt-5.5 xhigh · ~/repo"
	classify := func(visible string) AgentStatusResult {
		return ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PaneTarget: "%1"}, AgentStatusSources{
			CapturePane: func(context.Context, string) (AgentStatusPane, error) {
				return AgentStatusPane{Status: "idle", VisibleText: visible}, nil
			},
		})
	}

	blocked := classify(current)
	if blocked.Status != AgentStatusError || blocked.AttentionKind != "unknown" || blocked.AttentionSignature == "" {
		t.Fatalf("current capacity notice = %+v, want blocked attention with a signature", blocked)
	}
	if cleared := classify(stale); cleared.Status != AgentStatusIdle {
		t.Fatalf("capacity notice followed by newer output = %+v, want idle", cleared)
	}
	for _, variant := range []string{
		"■  Selected model is at capacity. Please try a different model.",
		"■Selected model is at capacity. Please try a different model.",
		"\t■\tSelected model is at capacity. Please try a different model.\t",
	} {
		if !CapacityNoticeCurrent(variant + "\n›") {
			t.Errorf("capacity notice variant %q was not recognized", variant)
		}
	}
}

func TestClassifyVisibleAgentTextPreservesWorkingWithQueuedCodexFollowupQuestion(t *testing.T) {
	tests := []struct {
		name                  string
		text                  string
		want                  AgentStatus
		wantQueuedFollowupAsk bool
	}{
		{
			name: "queued question while working",
			text: `◦ Working (50m 22s • esc to interrupt)

• Queued follow-up inputs
  ? 1 question
    shift+← to answer

	› Ask Codex to do anything`,
			want:                  AgentStatusRun,
			wantQueuedFollowupAsk: true,
		},
		{
			name: "queued statement is still working",
			text: "Working (1m • esc to interrupt)\n• Queued follow-up inputs\n↳ keep going\nshift+← to answer",
			want: AgentStatusRun,
		},
		{
			name: "old queued question above current working footer is stale",
			text: "Working (1m • esc to interrupt)\n• Queued follow-up inputs\n  ? 1 question\n  shift+← to answer\n\nWorking (2m • esc to interrupt)",
			want: AgentStatusRun,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := classifyVisibleAgentText(tc.text)
			if got.Status != tc.want || got.QueuedFollowupQuestion != tc.wantQueuedFollowupAsk {
				t.Fatalf("queued follow-up classification = %+v, want status %q and queued question %t", got, tc.want, tc.wantQueuedFollowupAsk)
			}
		})
	}
}

func TestClassifyAgentStatusAcceptsAdmittedNodeCodexWrapper(t *testing.T) {
	got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{
		Kind: "codex", PID: 10, Executable: "/usr/bin/node", PaneTarget: "%1",
	}, AgentStatusSources{
		Process: func(context.Context, int) (AgentStatusProcess, error) {
			return AgentStatusProcess{Alive: true, Command: "node /home/test/.npm/_npx/@openai/codex/bin/codex.js --resume thread", Executable: "/usr/bin/node"}, nil
		},
		CapturePane: func(context.Context, string) (AgentStatusPane, error) {
			return AgentStatusPane{VisibleText: "Working\nEsc to interrupt"}, nil
		},
	})
	if got.Status != AgentStatusRun {
		t.Fatalf("Node Codex wrapper status = %+v, want run", got)
	}
}

func TestClassifyVisibleAgentTextUsesCurrentCodexComposerOverStaleCompletion(t *testing.T) {
	text := `Finished waiting
  └ No agents completed yet

────────────────────────────────────────────────────────────────────────
› Ask Codex to do anything

gpt-5.6-luna high · /workspace · Main`

	got := classifyVisibleAgentText(text)
	if got.Status != AgentStatusIdle {
		t.Fatalf("status = %+v, want idle for the current composer", got)
	}
}

func TestClassifyVisibleAgentTextRecognizesCurrentCodexWorkingCounter(t *testing.T) {
	text := `• Ran 2 commands · ctrl + t to view transcript

◦ Working (27s • esc to interrupt) · 1 background terminal running · /ps to view · /stop to close
› Ask Codex to do anything`

	got := classifyVisibleAgentText(text)
	if got.Status != AgentStatusRun {
		t.Fatalf("status = %+v, want run for the current working counter", got)
	}
}

func TestCodexScreenFixturesCoverStatusLifecycleInputs(t *testing.T) {
	fixtures := []struct {
		name                  string
		text                  string
		captureError          error
		want                  AgentStatus
		wantQueuedFollowupAsk bool
	}{
		{name: "working with queued follow-up", text: "◦ Working (12s • esc to interrupt)\n• Queued follow-up inputs\n  ? 1 question\n  shift+← to answer\n› Ask Codex to do anything", want: AgentStatusRun, wantQueuedFollowupAsk: true},
		{name: "compacting context is working", text: "Error: previous command failed\nGoal achieved (4h ago)\n◦ Compacting context (49s)\n  └ Making room to continue.\n› Ask Codex to do anything", want: AgentStatusRun},
		{name: "waiting for background terminal is working", text: "◦ Waiting for background terminal (1m 10s)\n› Ask Codex to do anything", want: AgentStatusRun},
		{name: "interrupted working pane returns idle", text: "◦ Working (3m 08s • esc to interrupt)\nConversation interrupted - use /feedback if something went wrong\n› Ask Codex to do anything", want: AgentStatusIdle},
		{name: "new work after interruption is working", text: "Conversation interrupted - use /feedback if something went wrong\n◦ Working (1s • esc to interrupt)\n› Ask Codex to do anything", want: AgentStatusRun},
		{name: "worked-for footer completes the current turn", text: "The old Working footer was current and the Conversation interrupted line came later. I added an interruption boundary, so the stale footer now yields idle while a newer work marker still yields green; approval prompts containing only Esc to interrupt remain ASK.\nWorked for 17m 52s · 5:11 PM\n› Ask Codex to do anything", want: AgentStatusTransition},
		{name: "worked-for footer supersedes stale working row", text: "◦ Working (3m 08s • esc to interrupt)\nWorked for 14m 54s · 5:31 PM\n› Ask Codex to do anything", want: AgentStatusTransition},
		{name: "error after worked-for footer remains current", text: "Worked for 1s\nError: authentication failed\n› Ask Codex to do anything", want: AgentStatusError},
		{name: "approval after worked-for footer remains current", text: "Worked for 1s\nAllow this command?\nEnter to select · Esc to cancel", want: AgentStatusAsk},
		{name: "working after worked-for footer remains current", text: "Worked for 1s\n◦ Working (3s • esc to interrupt)\n› Ask Codex to do anything", want: AgentStatusRun},
		{name: "worked-for prose without a duration is not a completion footer", text: "Worked for the right fix by adding a regression.\n› Ask Codex to do anything", want: AgentStatusIdle},
		{name: "ordinary idle completion", text: "• Finished task\n› Ask Codex to do anything", want: AgentStatusIdle},
		{name: "ask", text: "Allow this command?\nEnter to select · Esc to cancel", want: AgentStatusAsk},
		{name: "blocked", text: "Error: command failed with exit code 1", want: AgentStatusError},
		{name: "capture unavailable", captureError: errors.New("pane disappeared"), want: AgentStatusUnavailable},
	}
	for _, fixture := range fixtures {
		t.Run(fixture.name, func(t *testing.T) {
			sources := AgentStatusSources{CapturePane: func(_ context.Context, target string) (AgentStatusPane, error) {
				if target != "%7" {
					t.Fatalf("captured pane target = %q, want %%7", target)
				}
				return AgentStatusPane{VisibleText: fixture.text, ObservedAt: time.Unix(100, 0)}, fixture.captureError
			}}
			got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PaneTarget: "%7", Now: time.Unix(100, 0)}, sources)
			if got.Status != fixture.want || got.QueuedFollowupQuestion != fixture.wantQueuedFollowupAsk {
				t.Fatalf("classification = %+v, want %q and queued follow-up %t", got, fixture.want, fixture.wantQueuedFollowupAsk)
			}
		})
	}
	missing := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PaneTarget: "%7"}, AgentStatusSources{})
	if missing.Status != AgentStatusUnavailable {
		t.Fatalf("missing pane source = %+v, want unavailable", missing)
	}
}

func TestClassifyVisibleAgentTextIgnoresStaleAutoApproveDiscussionBeforeWorkingCounter(t *testing.T) {
	text := `• Ran node tests/layout_async.test.js
	assert.equal(model.agents[0].state, "working", "auto-approve fallback preserves the green marker")

◦ Working (26m 16s • esc to interrupt) · 1 background terminal running · /ps to view · /stop to close
› Ask Codex to do anything`

	got := classifyVisibleAgentText(text)
	if got.Status != AgentStatusRun {
		t.Fatalf("status = %+v, want run when stale auto-approve prose precedes the current working counter", got)
	}
}

func TestClassifyVisibleAgentTextIgnoresStaleToolErrorBeforeWorkingFooter(t *testing.T) {
	text := `• Ran python3 - <<'PY'
  File "<stdin>", line 17
    print(json.dumps(...))
          ^
SyntaxError: unterminated triple-quoted string literal

◦ Working (20m 46s • esc to interrupt) · 1 background terminal running · /ps to view · /stop to close
› Ask Codex to do anything`

	got := classifyVisibleAgentText(text)
	if got.Status != AgentStatusRun {
		t.Fatalf("status = %+v, want run for a current working footer after stale tool error", got)
	}
}

func TestClassifyVisibleAgentTextIgnoresStaleCompletionBeforeWorkingFooter(t *testing.T) {
	text := `Goal achieved (2m)

◦ Working (28m 12s • esc to interrupt) · 2 background terminals running · /ps to view · /stop to close
› Ask Codex to do anything`

	got := classifyVisibleAgentText(text)
	if got.Status != AgentStatusRun {
		t.Fatalf("status = %+v, want run when stale completion precedes the current working footer", got)
	}
}

func TestClassifyAgentStatusStructuredPythonParity(t *testing.T) {
	now := time.Unix(1000, 0)
	recent := now.Add(-10 * time.Second)
	old := now.Add(-61 * time.Second)
	tests := []struct {
		name    string
		pane    AgentStatusPane
		request AgentStatusRequest
		want    AgentStatus
	}{
		{name: "active approval asks", pane: AgentStatusPane{Status: "approval"}, request: AgentStatusRequest{Now: now}, want: AgentStatusAsk},
		{name: "needs input asks", pane: AgentStatusPane{Status: "needs-input"}, request: AgentStatusRequest{Now: now}, want: AgentStatusAsk},
		{name: "working runs", pane: AgentStatusPane{Status: "working"}, request: AgentStatusRequest{Now: now}, want: AgentStatusRun},
		{name: "cooldown transitions", pane: AgentStatusPane{Status: "cooldown"}, request: AgentStatusRequest{Now: now}, want: AgentStatusTransition},
		{name: "recent completion transitions", pane: AgentStatusPane{Status: "idle", WorkingStoppedAt: recent}, request: AgentStatusRequest{Now: now}, want: AgentStatusTransition},
		{name: "settled completion idles", pane: AgentStatusPane{Status: "idle", WorkingStoppedAt: old}, request: AgentStatusRequest{Now: now}, want: AgentStatusIdle},
		{name: "completion screen settles after transition window", pane: AgentStatusPane{VisibleText: "Task completed successfully", WorkingStoppedAt: old}, request: AgentStatusRequest{Now: now}, want: AgentStatusIdle},
		{name: "acknowledged completion screen settles immediately", pane: AgentStatusPane{VisibleText: "Task completed successfully"}, request: AgentStatusRequest{Now: now, Acknowledged: true}, want: AgentStatusIdle},
		{name: "acknowledged ask remains red", pane: AgentStatusPane{Status: "needs-input", Acknowledged: true}, request: AgentStatusRequest{Now: now}, want: AgentStatusAsk},
		{name: "acknowledged cooldown idles", pane: AgentStatusPane{Status: "cooldown", Acknowledged: true}, request: AgentStatusRequest{Now: now}, want: AgentStatusIdle},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := classifyPaneStatus(tc.pane, tc.request)
			if got.Status != tc.want {
				t.Fatalf("status = %q, want %q (%+v)", got.Status, tc.want, got)
			}
		})
	}
}

func TestClassifyAgentStatusFailsClosedOnAmbiguityAndBounds(t *testing.T) {
	for _, text := range []string{
		"some output with no prompt or spinner",
		"working and goal achieved",
		"Task completed successfully.\n\nError: command failed",
	} {
		got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "claude", PaneTarget: "%1"}, AgentStatusSources{
			CapturePane: func(context.Context, string) (AgentStatusPane, error) { return AgentStatusPane{VisibleText: text}, nil },
		})
		if got.Status != AgentStatusUnavailable {
			t.Errorf("%q classified as %q", text, got.Status)
		}
	}
	gotApproval := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "claude", PaneTarget: "%1"}, AgentStatusSources{
		CapturePane: func(context.Context, string) (AgentStatusPane, error) {
			return AgentStatusPane{VisibleText: "Allow this command?\n\nEsc to interrupt"}, nil
		},
	})
	if gotApproval.Status != AgentStatusAsk {
		t.Fatalf("approval prompt with interrupt affordance = %q, want %q (%+v)", gotApproval.Status, AgentStatusAsk, gotApproval)
	}
	got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PaneTarget: "%1"}, AgentStatusSources{
		CapturePane: func(context.Context, string) (AgentStatusPane, error) {
			return AgentStatusPane{VisibleText: string(make([]byte, MaxAgentPaneCaptureBytes+1))}, nil
		},
	})
	if got.Status != AgentStatusUnavailable || got.Reason != "pane-capture-bound-exceeded" {
		t.Fatalf("oversized result = %+v", got)
	}
}

func TestOpenCodeSQLiteStateReaderUsesTemporaryCompatibleSchema(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	statements := []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE session_input (id TEXT, session_id TEXT, delivery TEXT, admitted_seq INTEGER, promoted_seq INTEGER, time_created INTEGER)`,
		`INSERT INTO session VALUES ('ses-1', '/repo', 'build', '{}', 1, 2, 0)`,
		`INSERT INTO message VALUES ('msg-1', 'ses-1', 1, 2, '{"role":"assistant"}')`,
		`INSERT INTO part VALUES ('part-1', 'msg-1', 'ses-1', 1, 2, '{"type":"step-finish"}')`,
		`INSERT INTO session_input VALUES ('input-1', 'ses-1', 'queue', 1, NULL, 2)`,
	}
	for _, statement := range statements {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	reader := OpenCodeSQLiteStateReader(path)
	state, evidence, err := reader(context.Background(), AgentStatusRequest{OpenCodeSession: "ses-1"})
	if err != nil || state != "paused" || len(evidence) != 1 || evidence[0] != "queued-input" {
		t.Fatalf("state = %q evidence = %v err = %v", state, evidence, err)
	}
	got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "opencode", OpenCodeSession: "ses-1"}, AgentStatusSources{OpenCodeState: reader})
	if got.Status != AgentStatusAsk {
		t.Fatalf("OpenCode status = %+v", got)
	}
}

func TestOpenCodeSQLiteStateReaderRejectsExplicitSessionMissingFromDatabase(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE session_input (id TEXT, session_id TEXT, delivery TEXT, admitted_seq INTEGER, promoted_seq INTEGER, time_created INTEGER)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	reader := OpenCodeSQLiteStateReader(path)
	state, evidence, err := reader(context.Background(), AgentStatusRequest{OpenCodeSession: "ses-live"})
	if err == nil || err.Error() != "opencode-session-unavailable" || state != "" || evidence != nil {
		t.Fatalf("missing explicit session = state %q evidence=%v err=%v", state, evidence, err)
	}
}

func TestOpenCodeSQLiteStateReaderResolvesSessionByDirectory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "opencode.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	statements := []string{
		`CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL)`,
		`CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE session_input (id TEXT, session_id TEXT, delivery TEXT, admitted_seq INTEGER, promoted_seq INTEGER, time_created INTEGER)`,
		`INSERT INTO session VALUES ('ses-old', '/repo', 'build', '{}', 1, 2, 0)`,
		`INSERT INTO session VALUES ('ses-new', '/repo', 'build', '{}', 3, 4, 0)`,
		`INSERT INTO part VALUES ('part-1', 'msg-1', 'ses-new', 1, 2, '{"type":"tool","state":{"status":"running"}}')`,
	}
	for _, statement := range statements {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	reader := OpenCodeSQLiteStateReader(path)
	state, evidence, err := reader(context.Background(), AgentStatusRequest{Directory: "/repo"})
	if err != nil || state != "working" || len(evidence) != 1 || evidence[0] != "running-tool" {
		t.Fatalf("directory session state = %q evidence=%v err=%v", state, evidence, err)
	}
}

type agentStatusTestClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *agentStatusTestClock) Now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.now }
func (*agentStatusTestClock) NewTimer(duration time.Duration) HostCollectorTimer {
	return realHostCollectorTimer{time.NewTimer(duration)}
}

type agentStatusFailingWriter struct {
	mu      sync.Mutex
	calls   int
	batches []AppendBatch
}

func (w *agentStatusFailingWriter) AppendBatch(_ context.Context, batch AppendBatch) (AppendResult, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.calls++
	if w.calls == 1 {
		return AppendResult{}, errors.New("synthetic stats append failure")
	}
	w.batches = append(w.batches, batch)
	return AppendResult{ObservationsAccepted: len(batch.Observations), UnavailableAccepted: len(batch.UnavailableSpans)}, nil
}

func (w *agentStatusFailingWriter) snapshot() []AppendBatch {
	w.mu.Lock()
	defer w.mu.Unlock()
	return append([]AppendBatch(nil), w.batches...)
}

func TestAgentStatusCollectorPersistsCyclesGenerationAndDuplicateReplay(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	inventory := func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{PID: 10, Kind: "codex", StartedIdentity: "proc:1", TmuxSession: "ses-1", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
	}
	classifier := func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusRun}
	}
	collector := NewAgentStatusCollector(inventory, classifier, writer, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "host-test", OwnerGeneration: 3, SnapshotRevision: func() uint64 { return 17 }})
	collector.RunOnce(context.Background())
	first := collector.Counters()
	clock.now = time.Unix(210, 0)
	collector.RunOnce(context.Background())
	second := collector.Counters()
	if first.Cycles != 1 || second.Cycles != 2 || second.AcceptedObservations != 2 {
		t.Fatalf("counters = %+v then %+v", first, second)
	}
	if first.LastCadence != 10*time.Second {
		t.Fatalf("status cadence = %s, want 10s", first.LastCadence)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(context.Background(), 0, 300, 20)
	if err != nil || len(observations) != 2 {
		t.Fatalf("observations = %v err=%v", observations, err)
	}
	if observations[0].Payload["snapshot_revision"] != float64(17) {
		t.Fatalf("payload = %#v", observations[0].Payload)
	}
	var states map[string]any
	raw, _ := json.Marshal(observations[0].Payload["states"])
	if json.Unmarshal(raw, &states) != nil || states["ses-1|0|%1|codex"] != "run" {
		t.Fatalf("states = %#v", states)
	}
	result, err := writer.AppendBatch(context.Background(), AppendBatch{Observations: []Observation{{EventID: observations[0].EventID, Family: observations[0].Family, SourceID: observations[0].SourceID, ObservedAt: observations[0].ObservedAt, EpochID: observations[0].EpochID, OwnerGeneration: observations[0].OwnerGeneration, Payload: observations[0].Payload}}})
	if err != nil || result.ObservationsDuplicate != 1 || result.Generation != 2 {
		t.Fatalf("duplicate result = %+v err=%v", result, err)
	}
}

func TestAgentStatusCollectorDoesNotCloseUnpersistedEpochAfterAppendFailure(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	writer := &agentStatusFailingWriter{}
	available := true
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		if !available {
			return nil, []error{errors.New("synthetic inventory unavailable")}
		}
		return []AgentRecord{{Kind: "codex", TmuxSession: "synthetic", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusRun}
	}, writer, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "test"})

	collector.RunOnce(context.Background())
	available = false
	clock.now = time.Unix(210, 0)
	collector.RunOnce(context.Background())
	batches := writer.snapshot()
	if len(batches) != 1 || len(batches[0].Observations) != 0 || len(batches[0].CoverageEpochs) != 0 || len(batches[0].UnavailableSpans) != 1 {
		t.Fatalf("post-failure unavailable batch = %+v, want only one unavailable span", batches)
	}
	if got := collector.Counters(); got.AcceptedObservations != 0 || got.Errors != 2 {
		t.Fatalf("post-failure counters = %+v", got)
	}
}

func TestAgentStatusCollectorDoesNotRetainUnpersistedStatusCacheAfterAppendFailure(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	writer := &agentStatusFailingWriter{}
	status := AgentStatusRun
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{Kind: "codex", TmuxSession: "synthetic", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: status}
	}, writer, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "test"})

	// The first positive sample is rejected by the writer. It must not seed the
	// transition cache because no corresponding fact exists in SQLite.
	collector.RunOnce(context.Background())
	status = AgentStatusIdle
	clock.now = time.Unix(210, 0)
	collector.RunOnce(context.Background())
	batches := writer.snapshot()
	if len(batches) != 1 || len(batches[0].Observations) != 1 {
		t.Fatalf("successful retry batches = %+v", batches)
	}
	payload := batches[0].Observations[0].Payload
	states, ok := payload["states"].(map[string]any)
	if !ok || states["synthetic|0|%1|codex"] != "idle" {
		t.Fatalf("retry status = %#v, want idle without fabricated transition", payload)
	}
}

func TestAgentStatusCollectorMissingPaneProducesUnavailableSpan(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{PID: 10, Kind: "codex"}}, nil
	}, nil, writer, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "host-test"})
	collector.RunOnce(context.Background())
	if got := collector.Counters(); got.UnavailableSpans != 1 || got.Errors != 0 {
		t.Fatalf("counters = %+v", got)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	spans, err := database.ReadUnavailableSpans(context.Background(), 0, 300, 10)
	if err != nil || len(spans) != 1 || spans[0].Reason != "pane-capture-unavailable" {
		t.Fatalf("spans = %+v err=%v", spans, err)
	}
}

func TestAgentStatusCollectorSkipsNoPaneRecordsWhenPhysicalPaneExists(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{
			{PID: 10, Kind: "codex"},
			{PID: 11, Kind: "claude", TmuxSession: "s", TmuxWindow: "0", TmuxPaneID: "%1"},
		}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusIdle}
	}, nil, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "host-test"})
	facts := collector.collect(context.Background(), HostFactContext{EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "host-test"})
	if len(facts.Observations) != 1 || len(facts.UnavailableSpans) != 0 {
		t.Fatalf("facts = %+v", facts)
	}
	if got := len(facts.Observations[0].Payload["agents"].([]map[string]any)); got != 1 {
		t.Fatalf("roster length = %d, want 1", got)
	}
}

func TestAgentStatusIdentityUsesPhysicalFieldsAndAgentFallback(t *testing.T) {
	if got, ok := agentStatusIdentity(AgentRecord{TmuxSession: "s", TmuxWindow: "3", TmuxPaneID: "%7", Kind: "Codex"}, 4); !ok || got != "s|3|%7|codex" {
		t.Fatalf("physical identity = %q, %v", got, ok)
	}
	if got, ok := agentStatusIdentity(AgentRecord{}, 4); !ok || got != "agent-4" {
		t.Fatalf("fallback identity = %q, %v", got, ok)
	}
	if got, ok := agentStatusIdentity(AgentRecord{TmuxSession: "s", TmuxWindowName: "named", TmuxPane: "1", Kind: "claude"}, 0); !ok || got != "s|named|1|claude" {
		t.Fatalf("field fallback identity = %q, %v", got, ok)
	}
}

func TestAgentStatusCollectorMatchesPythonPrecedenceEmptyRosterAndRevision(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{
			{Kind: "codex", TmuxSession: "s", TmuxWindow: "2", TmuxPaneID: "%2"},
			{Kind: "claude", TmuxSession: "s", TmuxWindow: "1", TmuxPaneID: "%1"},
			{Kind: "claude", TmuxSession: "s", TmuxWindow: "1", TmuxPaneID: "%3"},
		}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusIdle}
	}, nil, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "test"})
	facts := collector.collect(context.Background(), HostFactContext{EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test"})
	if len(facts.Observations) != 1 {
		t.Fatalf("observations = %+v", facts)
	}
	payload := facts.Observations[0].Payload
	if payload["snapshot_revision"] != uint64(0) {
		t.Fatalf("snapshot revision = %#v", payload["snapshot_revision"])
	}
	if got := payload["session_states"].(map[string]any)["s"]; got != "idle" {
		t.Fatalf("empty precedence baseline = %#v", got)
	}

	collector.classifier = func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusAsk}
	}
	facts = collector.collect(context.Background(), HostFactContext{EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 201, CadenceSeconds: 10, SourceID: "test"})
	if got := facts.Observations[0].Payload["session_states"].(map[string]any)["s"]; got != "ask" {
		t.Fatalf("ask precedence = %#v", got)
	}

	empty := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) { return nil, nil }, nil, nil, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "test"})
	facts = empty.collect(context.Background(), HostFactContext{EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test"})
	if len(facts.Observations) != 1 || len(facts.Observations[0].Payload["states"].(map[string]any)) != 0 {
		t.Fatalf("empty roster = %+v", facts)
	}
}

func TestAgentStatusCollectorRetainsValidPaneWhenAnotherPaneConflicts(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{
			{Kind: "claude", TmuxSession: "valid", TmuxWindow: "0", TmuxPaneID: "%1"},
			{Kind: "codex", TmuxSession: "conflict", TmuxWindow: "0", TmuxPaneID: "%2"},
		}, nil
	}, func(_ context.Context, request AgentStatusRequest) AgentStatusResult {
		if request.PaneTarget == "%2" {
			return AgentStatusResult{Status: AgentStatusUnavailable, Reason: "conflicting-pane-evidence"}
		}
		return AgentStatusResult{Status: AgentStatusRun}
	}, nil, clock, AgentStatusCollectorConfig{
		EpochID: "epoch", EpochStartedAt: 100, SourceID: "test",
		SnapshotRevision: func() uint64 { return 4 },
	})

	facts := collector.collect(context.Background(), HostFactContext{
		EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test",
	})
	if len(facts.Observations) != 1 || len(facts.UnavailableSpans) != 0 {
		t.Fatalf("mixed pane facts = %+v", facts)
	}
	payload := facts.Observations[0].Payload
	states := payload["states"].(map[string]any)
	if len(states) != 1 || states["valid|0|%1|claude"] != "run" {
		t.Fatalf("mixed pane states = %#v", states)
	}
	counts := payload["counts"].(map[string]any)
	if counts["total"] != 2 || payload["snapshot_revision"] != uint64(4) {
		t.Fatalf("mixed pane metadata = counts=%#v revision=%#v", counts, payload["snapshot_revision"])
	}
	roster := payload["agents"].([]map[string]any)
	if len(roster) != 2 || roster[0]["state"] != "unavailable" || roster[1]["state"] != "run" {
		t.Fatalf("mixed pane roster = %#v", roster)
	}
}

func TestAgentStatusCollectorRetainsValidRosterWithNonfatalInventoryError(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{Kind: "codex", TmuxSession: "valid", TmuxWindow: "0", TmuxPaneID: "%1"}}, []error{errors.New("tmux enrichment failed")}
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusRun}
	}, nil, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "test"})

	facts := collector.collect(context.Background(), HostFactContext{
		EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test",
	})
	if len(facts.UnavailableSpans) != 0 || len(facts.Observations) != 1 {
		t.Fatalf("nonfatal inventory facts = %+v", facts)
	}
	states := facts.Observations[0].Payload["states"].(map[string]any)
	if states["valid|0|%1|codex"] != "run" {
		t.Fatalf("nonfatal inventory states = %#v", states)
	}
	if got := collector.Counters().Errors; got != 1 {
		t.Fatalf("nonfatal inventory errors = %d, want 1 diagnostic error", got)
	}
}

func TestAgentStatusCollectorKeepsFamilyUnavailableOnFatalEmptyInventory(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return nil, []error{errors.New("process inventory unavailable")}
	}, nil, nil, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "test"})

	facts := collector.collect(context.Background(), HostFactContext{
		EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test",
	})
	if len(facts.Observations) != 0 || len(facts.UnavailableSpans) != 1 || facts.UnavailableSpans[0].Reason != "inventory-unavailable" {
		t.Fatalf("fatal inventory facts = %+v", facts)
	}
}

func TestAgentStatusCollectorRetainsRosterWhenEveryPaneIsUnavailable(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{Kind: "opencode", TmuxSession: "yo7220", TmuxWindow: "1", TmuxPaneID: "%7"}}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusUnavailable, Reason: "opencode-session-unavailable"}
	}, nil, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "test"})

	facts := collector.collect(context.Background(), HostFactContext{EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test"})
	if len(facts.UnavailableSpans) != 0 || len(facts.Observations) != 1 {
		t.Fatalf("unavailable roster facts = %+v", facts)
	}
	payload := facts.Observations[0].Payload
	roster := payload["agents"].([]map[string]any)
	if len(roster) != 1 || roster[0]["state"] != "unavailable" {
		t.Fatalf("unavailable roster = %#v", roster)
	}
	counts := payload["counts"].(map[string]any)
	if counts["total"] != 1 || counts["running"] != 0 {
		t.Fatalf("unavailable counts = %#v", counts)
	}
}

func TestAgentStatusCollectorRevisionStaysStableAcrossUnchangedCycles(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	status := AgentStatusIdle
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{Kind: "codex", TmuxSession: "session", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: status}
	}, nil, clock, AgentStatusCollectorConfig{
		EpochID: "epoch", EpochStartedAt: 100, SourceID: "test",
		SnapshotRevision: func() uint64 { return 1 },
	})

	facts := collector.collect(context.Background(), HostFactContext{
		EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test",
	})
	first := facts.Observations[0].Payload["snapshot_revision"]
	facts = collector.collect(context.Background(), HostFactContext{
		EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 210, CadenceSeconds: 10, SourceID: "test",
	})
	second := facts.Observations[0].Payload["snapshot_revision"]
	if first != uint64(1) || second != uint64(1) {
		t.Fatalf("revisions = %#v, %#v", first, second)
	}
	status = AgentStatusRun
	facts = collector.collect(context.Background(), HostFactContext{
		EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 220, CadenceSeconds: 10, SourceID: "test",
	})
	third := facts.Observations[0].Payload["snapshot_revision"]
	if third == uint64(1) {
		t.Fatalf("changed status did not advance revision: %#v", third)
	}
}

func TestAgentStatusCollectorPublishesOnlyObservedCompletionWindow(t *testing.T) {
	base := time.Unix(200, 0)
	clock := &agentStatusTestClock{now: base}
	status := AgentStatusRun
	collector := NewAgentStatusCollector(
		func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "codex", TmuxSession: "session", TmuxWindow: "3", TmuxPaneID: "%7"}}, nil
		},
		func(_ context.Context, request AgentStatusRequest) AgentStatusResult {
			if status == AgentStatusIdle && !request.WorkingStoppedAt.IsZero() && request.Now.Sub(request.WorkingStoppedAt) < DefaultAgentTransitionWindow {
				return AgentStatusResult{Status: AgentStatusTransition, WorkingStoppedAt: request.WorkingStoppedAt, ObservedAt: request.Now}
			}
			return AgentStatusResult{Status: status, ObservedAt: request.Now}
		}, nil, clock, AgentStatusCollectorConfig{EpochID: "lifecycle", EpochStartedAt: float64(base.Unix()), SourceID: "test"},
	)
	collect := func(at time.Time) map[string]any {
		clock.mu.Lock()
		clock.now = at
		clock.mu.Unlock()
		batch := collector.collect(context.Background(), HostFactContext{EpochID: "lifecycle", EpochStartedAt: float64(base.Unix()), ObservedAt: float64(at.UnixNano()) / 1e9, CadenceSeconds: 10, SourceID: "test"})
		if len(batch.Observations) != 1 {
			t.Fatalf("observation count at %s = %d", at, len(batch.Observations))
		}
		rows, ok := batch.Observations[0].Payload["agents"].([]map[string]any)
		if !ok || len(rows) != 1 {
			t.Fatalf("agent roster at %s = %#v", at, batch.Observations[0].Payload["agents"])
		}
		return rows[0]
	}
	if row := collect(base); row["state"] != "run" || row["working_stopped_ts"] != nil {
		t.Fatalf("working sample = %#v, want run without a completion timestamp", row)
	}
	status = AgentStatusIdle
	completedAt := base.Add(10 * time.Second)
	row := collect(completedAt)
	if row["state"] != "transition" || row["working_stopped_ts"] != float64(completedAt.UnixNano())/1e9 {
		t.Fatalf("first observed idle = %#v, want transition stamped at %s", row, completedAt)
	}
	transitionID, _ := row["transition_id"].(string)
	attentionKey, _ := row["cooldown_attention_key"].(string)
	if transitionID == "" || attentionKey == "" {
		t.Fatalf("completion identity missing from row: %#v", row)
	}
	row = collect(completedAt.Add(DefaultAgentTransitionWindow - time.Nanosecond))
	if row["state"] != "transition" || row["transition_id"] != transitionID || row["cooldown_attention_key"] != attentionKey {
		t.Fatalf("recent completion = %#v, want same transition until its deadline", row)
	}
	row = collect(completedAt.Add(DefaultAgentTransitionWindow))
	if row["state"] != "idle" || row["working_stopped_ts"] == nil || row["transition_id"] != transitionID || row["cooldown_attention_key"] != attentionKey || row["completion_settled"] != nil {
		t.Fatalf("expired completion = %#v, want idle with its solid yellow reminder identity", row)
	}
}

func TestAgentStatusCollectorIdentifiesDirectCompletionScreen(t *testing.T) {
	base := time.Unix(400, 0)
	clock := &agentStatusTestClock{now: base}
	visibleText := "Worked for 14m 54s · 5:31 PM\n› Ask Codex to do anything"
	collector := NewAgentStatusCollector(
		func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "codex", TmuxSession: "session", TmuxWindow: "3", TmuxPaneID: "%7"}}, nil
		},
		func(ctx context.Context, request AgentStatusRequest) AgentStatusResult {
			return ClassifyAgentStatus(ctx, request, AgentStatusSources{
				CapturePane: func(context.Context, string) (AgentStatusPane, error) {
					return AgentStatusPane{VisibleText: visibleText, ObservedAt: request.Now}, nil
				},
			})
		}, nil, clock, AgentStatusCollectorConfig{EpochID: "direct-completion", EpochStartedAt: float64(base.Unix()), SourceID: "test"},
	)
	collect := func(at time.Time) map[string]any {
		clock.mu.Lock()
		clock.now = at
		clock.mu.Unlock()
		batch := collector.collect(context.Background(), HostFactContext{EpochID: "direct-completion", EpochStartedAt: float64(base.Unix()), ObservedAt: float64(at.UnixNano()) / 1e9, CadenceSeconds: 10, SourceID: "test"})
		rows, ok := batch.Observations[0].Payload["agents"].([]map[string]any)
		if !ok || len(rows) != 1 {
			t.Fatalf("agent roster at %s = %#v", at, batch.Observations[0].Payload["agents"])
		}
		return rows[0]
	}
	row := collect(base)
	if row["state"] != "transition" || row["working_stopped_ts"] != float64(base.UnixNano())/1e9 {
		t.Fatalf("direct completion = %#v, want lifecycle start at %s", row, base)
	}
	transitionID, _ := row["transition_id"].(string)
	attentionKey, _ := row["cooldown_attention_key"].(string)
	if transitionID == "" || attentionKey == "" {
		t.Fatalf("direct completion identity missing: %#v", row)
	}
	visibleText = "Worked for 14m 54s · 5:31 PM\nError: authentication failed\n› Ask Codex to do anything"
	row = collect(base.Add(10 * time.Second))
	if row["state"] != "error" {
		t.Fatalf("fresh pane error during completion cooldown = %#v, want error", row)
	}
	visibleText = "Worked for 14m 54s · 5:31 PM\n› Ask Codex to do anything"
	row = collect(base.Add(DefaultAgentTransitionWindow - time.Nanosecond))
	if row["transition_id"] != transitionID || row["cooldown_attention_key"] != attentionKey {
		t.Fatalf("direct completion identity changed during transition: %#v", row)
	}
	row = collect(base.Add(DefaultAgentTransitionWindow))
	if row["state"] != "idle" || row["working_stopped_ts"] == nil || row["transition_id"] != transitionID || row["cooldown_attention_key"] != attentionKey || row["completion_settled"] == true {
		t.Fatalf("direct completion did not expire at its deadline: %#v", row)
	}
	row = collect(base.Add(DefaultAgentTransitionWindow + 10*time.Second))
	if row["state"] != "idle" || row["working_stopped_ts"] == nil || row["transition_id"] != transitionID || row["cooldown_attention_key"] != attentionKey || row["completion_settled"] == true {
		t.Fatalf("persistent completion screen re-armed after expiry: %#v", row)
	}
}

func TestAgentStatusCollectorRestoresCompletionIdentityAcrossRestart(t *testing.T) {
	base := time.Unix(500, 123456789)
	clock := &agentStatusTestClock{now: base.Add(time.Second)}
	wantTransitionID := "go:transition:restored-exact-key"
	wantAttentionKey := "go:cooldown:restored-exact-key"
	record := AgentRecord{PID: 701, StartedIdentity: "proc:701:old", Kind: "codex", TmuxSession: "session", TmuxWindow: "3", TmuxPaneID: "%7"}
	collector := NewAgentStatusCollector(
		func(context.Context) ([]AgentRecord, []error) { return []AgentRecord{record}, nil },
		func(ctx context.Context, request AgentStatusRequest) AgentStatusResult {
			return ClassifyAgentStatus(ctx, request, AgentStatusSources{
				CapturePane: func(context.Context, string) (AgentStatusPane, error) {
					return AgentStatusPane{VisibleText: "Task completed successfully", ObservedAt: request.Now}, nil
				},
			})
		}, nil, clock, AgentStatusCollectorConfig{EpochID: "restart", EpochStartedAt: float64(base.Unix()), SourceID: "test"},
	)
	collector.RestoreRecentLifecycles([]AgentStatusLifecycleState{{
		ID: "session|3|%7|codex", ObservedAt: base, WorkingStoppedAt: base,
		TransitionID: wantTransitionID, CooldownAttentionKey: wantAttentionKey,
	}})
	batch := collector.collect(context.Background(), HostFactContext{
		EpochID: "restart", EpochStartedAt: float64(base.Unix()), ObservedAt: float64(clock.now.UnixNano()) / 1e9,
		CadenceSeconds: 10, SourceID: "test",
	})
	rows, ok := batch.Observations[0].Payload["agents"].([]map[string]any)
	if !ok || len(rows) != 1 {
		t.Fatalf("restored collector roster = %#v", batch.Observations[0].Payload["agents"])
	}
	row := rows[0]
	wantStoppedAt := float64(base.UnixNano()) / 1e9
	if row["state"] != "transition" || row["working_stopped_ts"] != wantStoppedAt {
		t.Fatalf("restored completion = %#v, want original transition timestamp %.9f", row, wantStoppedAt)
	}
	if row["transition_id"] != wantTransitionID || row["cooldown_attention_key"] != wantAttentionKey {
		t.Fatalf("restored completion identity changed across restart: %#v", row)
	}
}

func TestReadRecentAgentStatusLifecyclesRetainsAcknowledgedCompletionIdentity(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	completedAt := time.Unix(600, 123456789)
	record := AgentRecord{Kind: "codex", TmuxSession: "session", TmuxWindow: "3", TmuxPaneID: "%7"}
	collector := NewAgentStatusCollector(
		func(context.Context) ([]AgentRecord, []error) { return []AgentRecord{record}, nil },
		func(ctx context.Context, request AgentStatusRequest) AgentStatusResult {
			return ClassifyAgentStatus(ctx, request, AgentStatusSources{
				CapturePane: func(context.Context, string) (AgentStatusPane, error) {
					return AgentStatusPane{VisibleText: "Task completed successfully", ObservedAt: request.Now}, nil
				},
			})
		}, nil, &agentStatusTestClock{now: completedAt}, AgentStatusCollectorConfig{EpochID: "persisted-transition", EpochStartedAt: float64(completedAt.Unix()), SourceID: "test"},
	)
	batch := collector.collect(ctx, HostFactContext{EpochID: "persisted-transition", EpochStartedAt: float64(completedAt.Unix()), ObservedAt: float64(completedAt.UnixNano()) / 1e9, CadenceSeconds: 10, SourceID: "test"})
	if _, err := writer.AppendBatch(ctx, batch); err != nil {
		t.Fatal(err)
	}
	rows, ok := batch.Observations[0].Payload["agents"].([]map[string]any)
	if !ok || len(rows) != 1 {
		t.Fatalf("persisted completion roster = %#v", batch.Observations[0].Payload["agents"])
	}
	wantTransitionID, _ := rows[0]["transition_id"].(string)
	wantAttentionKey, _ := rows[0]["cooldown_attention_key"].(string)
	restored, err := ReadRecentAgentStatusLifecycles(ctx, path, completedAt.Add(10*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if len(restored) != 1 || restored[0].ID != "session|3|%7|codex" || restored[0].TransitionID != wantTransitionID || restored[0].CooldownAttentionKey != wantAttentionKey {
		t.Fatalf("restored lifecycles = %+v, want the original in-window completion", restored)
	}
	expired, err := ReadRecentAgentStatusLifecycles(ctx, path, completedAt.Add(DefaultAgentTransitionWindow))
	if err != nil {
		t.Fatal(err)
	}
	if len(expired) != 1 || expired[0].CooldownAttentionKey != wantAttentionKey {
		t.Fatalf("solid reminder recovery = %+v, want original identity", expired)
	}
}

func TestAgentStatusSettledCompletionDoesNotRearmAfterRestart(t *testing.T) {
	ctx := context.Background()
	base := time.Unix(700, 456789123)
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	record := AgentRecord{PID: 701, StartedIdentity: "proc:701:old", Kind: "codex", TmuxSession: "session", TmuxWindow: "3", TmuxPaneID: "%7"}
	screen := "Task completed successfully"
	newCollector := func(clock *agentStatusTestClock, epoch string) *AgentStatusCollector {
		return NewAgentStatusCollector(
			func(context.Context) ([]AgentRecord, []error) { return []AgentRecord{record}, nil },
			func(ctx context.Context, request AgentStatusRequest) AgentStatusResult {
				return ClassifyAgentStatus(ctx, request, AgentStatusSources{
					CapturePane: func(context.Context, string) (AgentStatusPane, error) {
						return AgentStatusPane{VisibleText: screen, ObservedAt: request.Now}, nil
					},
				})
			}, nil, clock, AgentStatusCollectorConfig{EpochID: epoch, EpochStartedAt: float64(base.Unix()), SourceID: "test"},
		)
	}
	collect := func(collector *AgentStatusCollector, clock *agentStatusTestClock, at time.Time, epoch string) (AppendBatch, map[string]any) {
		clock.mu.Lock()
		clock.now = at
		clock.mu.Unlock()
		batch := collector.collect(ctx, HostFactContext{EpochID: epoch, EpochStartedAt: float64(base.Unix()), ObservedAt: float64(at.UnixNano()) / 1e9, CadenceSeconds: 10, SourceID: "test"})
		rows, ok := batch.Observations[0].Payload["agents"].([]map[string]any)
		if !ok || len(rows) != 1 {
			t.Fatalf("agent roster at %s = %#v", at, batch.Observations[0].Payload["agents"])
		}
		return batch, rows[0]
	}
	clock := &agentStatusTestClock{now: base}
	collector := newCollector(clock, "before-restart")
	firstBatch, firstRow := collect(collector, clock, base, "before-restart")
	if firstRow["state"] != "transition" {
		t.Fatalf("initial completion = %#v, want transition", firstRow)
	}
	if _, err := writer.AppendBatch(ctx, firstBatch); err != nil {
		t.Fatal(err)
	}
	collector.config.AttentionAcknowledged = func(key string) (bool, error) { return key == firstRow["cooldown_attention_key"], nil }
	settledAt := base.Add(DefaultAgentTransitionWindow)
	settledBatch, settledRow := collect(collector, clock, settledAt, "before-restart")
	if settledRow["state"] != "idle" || settledRow["completion_settled"] != true {
		t.Fatalf("expired completion = %#v, want persisted settled marker", settledRow)
	}
	processInstanceID, _ := settledRow["process_instance_id"].(string)
	if processInstanceID == "" {
		t.Fatalf("settled completion has no process identity: %#v", settledRow)
	}
	if _, err := writer.AppendBatch(ctx, settledBatch); err != nil {
		t.Fatal(err)
	}

	// A red prompt can be the latest persisted sample while the collector
	// still remembers that this completion screen has already settled.
	screen = "Allow this command?\nEnter to select · Esc to cancel"
	askAt := settledAt.Add(10 * time.Second)
	askBatch, askRow := collect(collector, clock, askAt, "before-restart")
	if askRow["state"] != "ask" || askRow["completion_settled"] != true {
		t.Fatalf("ASK after settled completion = %#v, want red state with suppression marker", askRow)
	}
	if _, err := writer.AppendBatch(ctx, askBatch); err != nil {
		t.Fatal(err)
	}

	restartedAt := askAt.Add(time.Hour)
	lifecycles, err := ReadRecentAgentStatusLifecycles(ctx, path, restartedAt)
	if err != nil {
		t.Fatal(err)
	}
	if len(lifecycles) != 1 || !lifecycles[0].CompletionSettled || lifecycles[0].ProcessInstanceID != processInstanceID {
		t.Fatalf("restored settled completion = %+v, want its suppression marker", lifecycles)
	}
	screen = "Task completed successfully"
	restartedClock := &agentStatusTestClock{now: restartedAt}
	restarted := newCollector(restartedClock, "after-restart")
	restarted.RestoreRecentLifecycles(lifecycles)
	_, row := collect(restarted, restartedClock, restartedAt, "after-restart")
	if row["state"] != "idle" || row["completion_settled"] != true || row["working_stopped_ts"] != nil {
		t.Fatalf("completion re-armed after restart: %#v", row)
	}

	// A new process in the same window owns a new completion lifecycle.
	record.PID = 702
	record.StartedIdentity = "proc:702:new"
	newProcessClock := &agentStatusTestClock{now: restartedAt}
	newProcess := newCollector(newProcessClock, "after-process-replacement")
	newProcess.RestoreRecentLifecycles(lifecycles)
	_, row = collect(newProcess, newProcessClock, restartedAt, "after-process-replacement")
	if row["state"] != "transition" || row["completion_settled"] == true {
		t.Fatalf("new process inherited settled completion: %#v", row)
	}
}

func TestAgentStatusPayloadIncludesBoundedCountsAndRoster(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	statuses := []AgentStatus{AgentStatusRun, AgentStatusAsk, AgentStatusError, AgentStatusTransition, AgentStatusIdle}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{
			{Kind: "claude", TmuxSession: "s", TmuxWindow: "0", TmuxPaneID: "%1"},
			{Kind: "codex", TmuxSession: "s", TmuxWindow: "1", TmuxPaneID: "%2"},
			{Kind: "codex", TmuxSession: "s", TmuxWindow: "2", TmuxPaneID: "%3"},
			{Kind: "opencode", OpenCodeSession: "ses-4", TmuxPaneID: "%4"},
			{Kind: "claude", TmuxSession: "s", TmuxWindow: "3", TmuxPaneID: "%5"},
		}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		status := statuses[0]
		statuses = statuses[1:]
		return AgentStatusResult{Status: status}
	}, nil, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "test"})
	facts := collector.collect(context.Background(), HostFactContext{EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test"})
	payload := facts.Observations[0].Payload
	counts := payload["counts"].(map[string]any)
	if counts["total"] != 5 || counts["running"] != 1 || counts["red"] != 2 || counts["yellow"] != 1 {
		t.Fatalf("counts = %+v", counts)
	}
	if len(payload["agents"].([]map[string]any)) != 5 {
		t.Fatalf("roster = %#v", payload["agents"])
	}
	if len(payload["states"].(map[string]any)) != 4 {
		t.Fatalf("metric states = %#v", payload["states"])
	}
}

func TestAgentStatusCollectorUsesExactFamilyCadenceAndBound(t *testing.T) {
	config := (AgentStatusCollectorConfig{ActiveCadence: time.Second, IdleCadence: time.Hour}).withDefaults()
	if config.ActiveCadence != 10*time.Second || config.IdleCadence != time.Minute {
		t.Fatalf("cadence defaults = %v/%v", config.ActiveCadence, config.IdleCadence)
	}
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		rows := make([]AgentRecord, MaxAgentStatusEntries+1)
		for i := range rows {
			rows[i] = AgentRecord{Kind: "codex", TmuxSession: "s", TmuxWindow: fmt.Sprint(i), TmuxPaneID: fmt.Sprintf("%%%d", i)}
		}
		return rows, nil
	}, nil, nil, clock, config)
	facts := collector.collect(context.Background(), HostFactContext{EpochID: "epoch", EpochStartedAt: 100, ObservedAt: 200, CadenceSeconds: 10, SourceID: "test"})
	if len(facts.UnavailableSpans) != 1 || facts.UnavailableSpans[0].Reason != "agent-status-bound-exceeded" {
		t.Fatalf("bound result = %+v", facts)
	}
}

func TestAgentStatusCollectorUsesDemandCadence(t *testing.T) {
	demanded := false
	writer := &recordingBatchWriter{}
	collector := NewAgentStatusCollector(
		func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "codex", TmuxSession: "synthetic", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
		},
		func(context.Context, AgentStatusRequest) AgentStatusResult {
			return AgentStatusResult{Status: AgentStatusIdle}
		},
		writer,
		&agentStatusTestClock{now: time.Unix(200, 0)},
		AgentStatusCollectorConfig{EpochID: "demand", EpochStartedAt: 100, SourceID: "test", ActiveFunc: func() bool { return demanded }},
	)
	collector.RunOnce(context.Background())
	if batches := writer.snapshot(); len(batches) != 1 || len(batches[0].CoverageEpochs) != 1 || batches[0].CoverageEpochs[0].NativeCadenceSeconds != 60 {
		t.Fatalf("idle cadence batch = %+v, want one 60-second coverage epoch", batches)
	}
	demanded = true
	collector.RunOnce(context.Background())
	batches := writer.snapshot()
	if len(batches) != 2 || len(batches[1].CoverageEpochs) != 1 || batches[1].CoverageEpochs[0].NativeCadenceSeconds != 10 {
		t.Fatalf("active cadence batch = %+v, want one 10-second coverage epoch", batches)
	}
}

func TestAgentStatusCollectorStartStopCancelsSingleRuntime(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	started := make(chan struct{})
	collector := NewAgentStatusCollector(func(ctx context.Context) ([]AgentRecord, []error) {
		close(started)
		<-ctx.Done()
		return nil, []error{ctx.Err()}
	}, nil, nil, clock, AgentStatusCollectorConfig{EpochID: "epoch", EpochStartedAt: 100, SourceID: "host-test"})
	if err := collector.Start(ctx); err != nil {
		t.Fatal(err)
	}
	if err := collector.Start(ctx); err == nil {
		t.Fatal("second Start unexpectedly succeeded")
	}
	<-started
	collector.Stop()
	if got := collector.Counters(); got.Cycles != 1 || got.Errors < 2 {
		t.Fatalf("counters = %+v", got)
	}
}

func TestAgentStatusCollectorLifecyclePersistsSyntheticInventory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	writer.EnableCurrentFamilyValidation()
	evidenceTime := time.Unix(123, 456)
	collector := NewAgentStatusCollector(
		func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{PID: 42, Kind: "codex", StartedIdentity: "proc:42", TmuxSession: "synthetic", TmuxWindow: "0", TmuxPaneID: "%42"}}, nil
		},
		func(context.Context, AgentStatusRequest) AgentStatusResult {
			return AgentStatusResult{Status: AgentStatusIdle, ObservedAt: evidenceTime}
		},
		writer,
		nil,
		AgentStatusCollectorConfig{ActiveCadence: time.Hour, IdleCadence: time.Hour, EpochID: "lifecycle", EpochStartedAt: 100, SourceID: "synthetic"},
	)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := collector.Start(ctx); err != nil {
		t.Fatal(err)
	}
	deadline := time.After(2 * time.Second)
	for collector.Counters().AcceptedObservations == 0 {
		select {
		case <-deadline:
			collector.Stop()
			t.Fatal("synthetic lifecycle did not persist an observation")
		default:
			time.Sleep(time.Millisecond)
		}
	}
	collector.Stop()
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	observations, err := database.ReadObservations(context.Background(), 0, float64(time.Now().Add(time.Hour).Unix()), 10)
	if err != nil || len(observations) != 1 {
		t.Fatalf("observations = %v err=%v", observations, err)
	}
	roster := observations[0].Payload["agents"].([]any)
	if got := roster[0].(map[string]any)["observed_at"]; got != float64(evidenceTime.UnixNano())/1e9 {
		t.Fatalf("persisted pane observation time = %v, want %v", got, evidenceTime)
	}
}

func TestAgentStatusPayloadSignatureIgnoresPerAgentObservationTime(t *testing.T) {
	states := map[string]any{"session|0|%1|codex": "run"}
	sessions := map[string]AgentStatus{"session": AgentStatusRun}
	counts := AgentStatusCounts{Total: 1, Running: 1}
	first := []map[string]any{{"id": "session|0|%1|codex", "kind": "codex", "state": "run", "session": "session", "pane": "%1", "observed_at": 10.0}}
	second := []map[string]any{{"id": "session|0|%1|codex", "kind": "codex", "state": "run", "session": "session", "pane": "%1", "observed_at": 20.0}}
	if firstSignature, secondSignature := agentStatusPayloadSignature(states, sessions, counts, first), agentStatusPayloadSignature(states, sessions, counts, second); firstSignature != secondSignature {
		t.Fatalf("status signature changed with only observed_at: first=%q second=%q", firstSignature, secondSignature)
	}
}

func TestClassifyAgentStatusPreservesPaneObservationTime(t *testing.T) {
	observedAt := time.Unix(123, 456)
	result := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PaneTarget: "%1"}, AgentStatusSources{
		CapturePane: func(context.Context, string) (AgentStatusPane, error) {
			return AgentStatusPane{VisibleText: "Working (1m 43s • esc to interrupt)", ObservedAt: observedAt}, nil
		},
	})
	if !result.ObservedAt.Equal(observedAt) {
		t.Fatalf("pane observation time = %v, want %v", result.ObservedAt, observedAt)
	}
}

func TestClassifyAgentStatusTimestampsOpenCodeReadAndUnavailableEvidence(t *testing.T) {
	t.Run("OpenCode state reader", func(t *testing.T) {
		startedAt := time.Now()
		got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "opencode"}, AgentStatusSources{
			OpenCodeState: func(context.Context, AgentStatusRequest) (string, []string, error) {
				return "paused", []string{"queued-input"}, nil
			},
		})
		finishedAt := time.Now()
		if got.Status != AgentStatusAsk || got.ObservedAt.Before(startedAt) || got.ObservedAt.After(finishedAt) {
			t.Fatalf("OpenCode classification = %+v, expected ask observed during [%v, %v]", got, startedAt, finishedAt)
		}
	})

	t.Run("process unavailable", func(t *testing.T) {
		got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PID: 42, PaneTarget: "%1"}, AgentStatusSources{
			Process: func(context.Context, int) (AgentStatusProcess, error) {
				return AgentStatusProcess{}, nil
			},
			CapturePane: func(context.Context, string) (AgentStatusPane, error) {
				t.Fatal("pane must not be read after process evidence says unavailable")
				return AgentStatusPane{}, nil
			},
		})
		if got.Status != AgentStatusUnavailable || got.ObservedAt.IsZero() {
			t.Fatalf("unavailable process classification = %+v, want a timestamped unavailable result", got)
		}
	})

	t.Run("pane capture error", func(t *testing.T) {
		observedAt := time.Unix(456, 789)
		got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PaneTarget: "%1"}, AgentStatusSources{
			CapturePane: func(context.Context, string) (AgentStatusPane, error) {
				return AgentStatusPane{ObservedAt: observedAt}, fmt.Errorf("capture failed")
			},
		})
		if got.Status != AgentStatusUnavailable || !got.ObservedAt.Equal(observedAt) {
			t.Fatalf("pane error classification = %+v, want timestamp %v", got, observedAt)
		}
	})
}

func TestCurrentQuestionControlsIgnoreQuotedAndStaleQuestions(t *testing.T) {
	for _, item := range []struct {
		text string
		want AgentStatus
	}{
		{"Which implementation should I use?\n\n› Ask Codex to do anything", AgentStatusAsk},
		{"Should I keep working?\n› Ask Codex to do anything", AgentStatusAsk},
		{"Would you like to run the following command?\n  python3 working.py\n  1. Yes\nEnter to select · Esc to cancel", AgentStatusAsk},
		{"Would you like to run the following command?\n  echo finished\n  1. Yes\nEnter to select · Esc to cancel", AgentStatusAsk},
		{"Would you like to run the following command?\n  echo 'Error: command failed'\n  1. Yes\nEnter to select · Esc to cancel", AgentStatusAsk},
		{"Allow this command?\nEnter to select · Esc to cancel\n• All done\n› Ask Codex to do anything", AgentStatusIdle},
		{"Allow this command?\nEnter to select · Esc to cancel\n• Finished\n› Ask Codex to do anything", AgentStatusIdle},
		{"• All done\nAllow this command?\nEnter to select · Esc to cancel\n› Ask Codex to do anything", AgentStatusAsk},
		{"• Finished\nAllow this command?\nEnter to select · Esc to cancel\n› Ask Codex to do anything", AgentStatusAsk},
		{"• Finished\nAllow this command?\nEnter to select · Esc to cancel\nError: command failed\n› Ask Codex to do anything", AgentStatusUnavailable},
		{"Error: command failed\n• Finished\nAllow this command?\nEnter to select · Esc to cancel\n› Ask Codex to do anything", AgentStatusAsk},
		{"Allow this command?\nEnter to select · Esc to cancel\nError: command failed\n› Ask Codex to do anything", AgentStatusUnavailable},
		{"Allow this command?\nEnter to select · Esc to cancel\n• Working (12s • esc to interrupt)\n› Ask Codex to do anything", AgentStatusRun},
		{"> Which implementation should I use?\n\n› Ask Codex to do anything", AgentStatusIdle},
		{"> Would you like to run the following?\n› Ask Codex to do anything", AgentStatusIdle},
		{"> approval required\n› Ask Codex to do anything", AgentStatusIdle},
		{"Which implementation should I use?\n• Working (12s • esc to interrupt)\n› Ask Codex to do anything", AgentStatusRun},
	} {
		for _, text := range []string{item.text, item.text + strings.Repeat("\n", 40)} {
			if got := classifyVisibleAgentText(text); got.Status != item.want {
				t.Fatalf("%q: got %s want %s", text, got.Status, item.want)
			}
		}
	}
}

func TestAgentAttentionKindSeparatesQuestionsFromApprovals(t *testing.T) {
	tests := []struct {
		name string
		text string
		want string
	}{
		{name: "question", text: "Which implementation should I use?\n\n› Ask Codex to do anything", want: "question"},
		{name: "current question wins over stale approval text", text: "Allow this command?\n1. Yes\nEnter to select\nWhich implementation should I use?\n› Ask Codex to do anything", want: "question"},
		{name: "approval", text: "Allow this command?\n1. Yes\n2. No\nEnter to select · Esc to cancel", want: "approval"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := classifyVisibleAgentText(test.text)
			if got.Status != AgentStatusAsk || got.AttentionKind != test.want {
				t.Fatalf("classification = %+v, want ask/%s", got, test.want)
			}
		})
	}
	got := classifyPaneStatus(AgentStatusPane{Status: "needs-input"}, AgentStatusRequest{})
	if got.Status != AgentStatusAsk || got.AttentionKind != "unknown" {
		t.Fatalf("unclassified structured ask = %+v, want ask/unknown", got)
	}
}

func TestAttentionSignaturePreservesCommandArgumentsAndIgnoresTerminalPadding(t *testing.T) {
	classify := func(text string) AgentStatusResult {
		return ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PaneTarget: "%1"}, AgentStatusSources{
			CapturePane: func(context.Context, string) (AgentStatusPane, error) {
				return AgentStatusPane{VisibleText: text}, nil
			},
		})
	}
	first := "Would you like to run the following command?\n  printf '%s' 'a  b'\nEnter to select · Esc to cancel"
	second := strings.Replace(first, "a  b", "a b", 1)
	a, b := classify(first), classify(second)
	if a.Status != AgentStatusAsk || b.Status != AgentStatusAsk || a.AttentionSignature == b.AttentionSignature {
		t.Fatalf("changed command arguments must create distinct ASK identities: %+v %+v", a, b)
	}
	padded := strings.ReplaceAll(first, "\n", " \t\r\n") + strings.Repeat(" \t\r\n", 40)
	if got := classify(padded); got.Status != a.Status || got.AttentionSignature != a.AttentionSignature {
		t.Fatalf("terminal padding changed attention identity: %+v vs %+v", got, a)
	}
}

func TestAttentionSettlementSurvivesExpiryRestartAndRearmsNewEvents(t *testing.T) {
	ctx := context.Background()
	base := time.Unix(20000, 0)
	clock := &agentStatusTestClock{now: base}
	record := AgentRecord{PID: 11, StartedIdentity: "first", Kind: "codex", TmuxSession: "session", TmuxWindow: "0", TmuxPaneID: "%1"}
	result := AgentStatusResult{Status: AgentStatusRun, QueuedFollowupQuestion: true, AttentionSignature: "queued:which-option"}
	ack := map[string]bool{}
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	config := AgentStatusCollectorConfig{EpochID: "attention", EpochStartedAt: float64(base.Unix()), SourceID: "test", AttentionAcknowledged: func(key string) (bool, error) { return ack[key], nil }}
	create := func() *AgentStatusCollector {
		return NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) { return []AgentRecord{record}, nil }, func(context.Context, AgentStatusRequest) AgentStatusResult { return result }, writer, clock, config)
	}
	collector := create()
	row := func(c *AgentStatusCollector) map[string]any {
		batch := c.collect(ctx, HostFactContext{EpochID: "attention", EpochStartedAt: float64(base.Unix()), ObservedAt: float64(clock.Now().Unix()), CadenceSeconds: 10, SourceID: "test"})
		if len(batch.Observations) != 1 {
			t.Fatalf("missing observation: %+v", batch)
		}
		if _, err := writer.AppendBatch(ctx, batch); err != nil {
			t.Fatal(err)
		}
		return batch.Observations[0].Payload["agents"].([]map[string]any)[0]
	}
	first := row(collector)
	key := first["attention_key"].(string)
	ack[key] = true
	clock.now = base.Add(10 * time.Second)
	settled := row(collector)
	if settled["state"] != "run" || settled["attention_acknowledged"] != true {
		t.Fatalf("queued ACK lost run: %#v", settled)
	}
	delete(ack, key) // Emulate TTL/cap pruning after snapshot settlement.
	clock.now = base.Add(8 * 24 * time.Hour)
	lifecycles, err := ReadRecentAgentStatusLifecycles(ctx, path, clock.now)
	if err != nil {
		t.Fatal(err)
	}
	restarted := create()
	restarted.RestoreRecentLifecycles(lifecycles)
	if got := row(restarted); got["attention_key"] != key || got["attention_acknowledged"] != true {
		t.Fatalf("settled prompt resurrected: %#v", got)
	}
	result.QueuedFollowupQuestion, result.AttentionSignature = false, ""
	clock.now = clock.now.Add(10 * time.Second)
	row(restarted)
	result.QueuedFollowupQuestion, result.AttentionSignature = true, "queued:which-option"
	clock.now = clock.now.Add(10 * time.Second)
	if got := row(restarted); got["attention_key"] == key || got["attention_acknowledged"] != false {
		t.Fatalf("new identical question did not rearm: %#v", got)
	}
	record.PID, record.StartedIdentity = 12, "replacement"
	clock.now = clock.now.Add(10 * time.Second)
	if got := row(restarted); got["attention_key"] == key || got["attention_acknowledged"] != false {
		t.Fatalf("replacement inherited ACK: %#v", got)
	}
}

func TestAttentionProtectionWaitsForCommittedObservation(t *testing.T) {
	base := time.Unix(21000, 0)
	clock := &agentStatusTestClock{now: base}
	writer := &agentStatusFailingWriter{}
	commits := 0
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{Kind: "codex", TmuxSession: "session", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusAsk, AttentionSignature: "question"}
	}, writer, clock, AgentStatusCollectorConfig{AttentionAcknowledged: func(string) (bool, error) { return true, nil }, AttentionCommitted: func([]string) error { commits++; return nil }})
	collector.RunOnce(context.Background())
	if commits != 0 {
		t.Fatal("failed append released protection")
	}
	clock.now = base.Add(10 * time.Second)
	collector.RunOnce(context.Background())
	if commits != 1 {
		t.Fatal("committed settlement did not release protection")
	}
	collector.config.AttentionAcknowledged = func(string) (bool, error) { return false, errors.New("ACK read failed") }
	clock.now = base.Add(20 * time.Second)
	collector.RunOnce(context.Background())
	if commits != 1 {
		t.Fatal("unavailable append released protection")
	}
}
