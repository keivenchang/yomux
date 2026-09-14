package stats

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

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
		{name: "acknowledged ask idles", pane: AgentStatusPane{Status: "needs-input", Acknowledged: true}, request: AgentStatusRequest{Now: now}, want: AgentStatusIdle},
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
		"Allow this command?\n\nEsc to interrupt",
		"Task completed successfully.\n\nError: command failed",
	} {
		got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "claude", PaneTarget: "%1"}, AgentStatusSources{
			CapturePane: func(context.Context, string) (AgentStatusPane, error) { return AgentStatusPane{VisibleText: text}, nil },
		})
		if got.Status != AgentStatusUnavailable {
			t.Errorf("%q classified as %q", text, got.Status)
		}
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
	if counts["total"] != 1 || payload["snapshot_revision"] != uint64(4) {
		t.Fatalf("mixed pane metadata = counts=%#v revision=%#v", counts, payload["snapshot_revision"])
	}
}

func TestAgentStatusCollectorRevisionIsMonotonicAcrossRepeatedCycles(t *testing.T) {
	clock := &agentStatusTestClock{now: time.Unix(200, 0)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{Kind: "codex", TmuxSession: "session", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		return AgentStatusResult{Status: AgentStatusIdle}
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
	if first != uint64(1) || second != uint64(2) {
		t.Fatalf("revisions = %#v, %#v", first, second)
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
	collector := NewAgentStatusCollector(
		func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{PID: 42, Kind: "codex", StartedIdentity: "proc:42", TmuxSession: "synthetic", TmuxWindow: "0", TmuxPaneID: "%42"}}, nil
		},
		func(context.Context, AgentStatusRequest) AgentStatusResult {
			return AgentStatusResult{Status: AgentStatusIdle}
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
}
