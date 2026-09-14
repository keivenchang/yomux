package stats

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// This test deliberately keeps a private tmux server and live-like client panes
// alive for the full integration budget while using the production adapters.
func TestAgentStatusLinuxAdaptersWithMockClient(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux /proc and tmux integration test")
	}
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is unavailable")
	}
	tmux, err := exec.LookPath("tmux")
	if err != nil {
		t.Skip("tmux is unavailable")
	}
	repoRoot := integrationRepoRoot(t)
	mock := filepath.Join(repoRoot, "tools", "mockers", "claude.py")
	if _, err := os.Stat(mock); err != nil {
		t.Skipf("mock client is unavailable: %v", err)
	}

	root := t.TempDir()
	socket := filepath.Join(root, "tmux.sock")
	originalSocket := os.Getenv("YOLOMUX_TMUX_SOCKET")
	if err := os.Setenv("YOLOMUX_TMUX_SOCKET", socket); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = os.Setenv("YOLOMUX_TMUX_SOCKET", originalSocket)
	})

	sessionPrefix := "yolo-go-status-" + strings.ToLower(strings.ReplaceAll(filepath.Base(root), "_", "-"))
	claudeSession := sessionPrefix + "-claude"
	codexSession := sessionPrefix + "-codex"
	env := append(os.Environ(), "PYTHONPATH="+repoRoot, "HOME="+root, "TERM=xterm-256color", "PYTHONUNBUFFERED=1")
	startMock := func(session, entryPoint, client string) {
		start := exec.Command(tmux, "-S", socket, "new-session", "-d", "-s", session, "-x", "100", "-y", "35", "bash", "-lc", "exec -a "+entryPoint+" "+shellQuote(python)+" -u "+shellQuote(client)+" --mock")
		start.Env = env
		if output, err := start.CombinedOutput(); err != nil {
			t.Skipf("private tmux server cannot start %s: %v: %s", entryPoint, err, output)
		}
	}
	startMock(claudeSession, "claude", filepath.Join(repoRoot, "tools", "mockers", "claude.py"))
	codexMock := filepath.Join(repoRoot, "tools", "mockers", "codex.py")
	if _, err := os.Stat(codexMock); err != nil {
		t.Skipf("codex mock client is unavailable: %v", err)
	}
	startMock(codexSession, "codex", codexMock)
	t.Cleanup(func() {
		_ = runTmux(tmux, socket, "kill-session", "-t", claudeSession)
		_ = runTmux(tmux, socket, "kill-session", "-t", codexSession)
		_ = runTmux(tmux, socket, "kill-server")
	})

	for _, session := range []string{claudeSession, codexSession} {
		if err := waitForPane(tmux, socket, session+":0.0"); err != nil {
			t.Fatal(err)
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	recordsBySession := make(map[string]AgentRecord)
	var inventoryErrors []error
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		inventory, errorsOut := BuildAgentInventory(ctx, LinuxAgentInventorySources())
		inventoryErrors = errorsOut
		for _, candidate := range inventory {
			if candidate.TmuxSession == claudeSession || candidate.TmuxSession == codexSession {
				recordsBySession[candidate.TmuxSession] = candidate
			}
		}
		if len(recordsBySession) == 2 && recordsBySession[claudeSession].PID > 0 && recordsBySession[codexSession].PID > 0 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if len(recordsBySession) != 2 || recordsBySession[claudeSession].TmuxPaneID == "" || recordsBySession[codexSession].TmuxPaneID == "" {
		panes, paneErr := linuxTmuxPanes(ctx)
		t.Fatalf("mock clients were not discovered in live inventory (errors=%v panes=%+v paneErr=%v records=%+v)", inventoryErrors, panes, paneErr, recordsBySession)
	}
	if got := len(recordsBySession); got != 2 {
		t.Fatalf("persistent-pane agent count = %d, want 2", got)
	}
	if recordsBySession[claudeSession].Kind != "claude" || recordsBySession[codexSession].Kind != "codex" {
		t.Fatalf("persistent-pane agent roster = %+v", recordsBySession)
	}
	for _, session := range []string{claudeSession, codexSession} {
		record := recordsBySession[session]
		if !strings.HasPrefix(record.TmuxPaneID, "%") {
			t.Fatalf("live pane ID session=%s target=%q", session, record.TmuxPaneID)
		}
		if output, err := exec.Command(tmux, "-S", socket, "capture-pane", "-p", "-J", "-S", "-12", "-t", record.TmuxPaneID).CombinedOutput(); err != nil || strings.TrimSpace(string(output)) == "" {
			t.Fatalf("direct PaneID capture session=%s target=%q err=%v output=%q", session, record.TmuxPaneID, err, output)
		}
	}
	for _, session := range []string{claudeSession, codexSession} {
		record := recordsBySession[session]
		if got, err := linuxCaptureAgentPane(ctx, record.TmuxPaneID); err != nil {
			t.Fatalf("live pane capture session=%s target=%q: %v", session, record.TmuxPaneID, err)
		} else {
			t.Logf("tmux args=%q inventory=%+v pane target=%q capture bytes=%d", tmuxCommandArgs("capture-pane", "-p", "-J", "-S", "-12", "-t", record.TmuxPaneID), record, got.Target, len(got.VisibleText))
			if strings.TrimSpace(got.VisibleText) == "" {
				t.Fatalf("live pane capture was empty for %s", session)
			}
		}
	}
	for _, session := range []string{claudeSession, codexSession} {
		record := recordsBySession[session]
		classified := ClassifyAgentStatus(ctx, AgentStatusRequest{Kind: record.Kind, PID: record.PID, Command: record.Command, Executable: record.Executable, PaneTarget: record.TmuxPaneID}, LinuxAgentStatusSources(filepath.Join(root, "missing-opencode.sqlite3")))
		if classified.Status == AgentStatusUnavailable || classified.Reason == "pane-capture-failed" {
			t.Fatalf("live agent status classification failed for %s: %+v", session, classified)
		}
	}

	databasePath := filepath.Join(root, "yostats.sqlite3")
	writer, err := OpenWriter(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	collector := NewAgentStatusCollector(
		func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{recordsBySession[claudeSession], recordsBySession[codexSession]}, nil
		},
		func(ctx context.Context, request AgentStatusRequest) AgentStatusResult {
			return ClassifyAgentStatus(ctx, request, LinuxAgentStatusSources(databasePath))
		},
		writer,
		nil,
		AgentStatusCollectorConfig{EpochID: "integration-epoch", EpochStartedAt: 100, SourceID: "linux-integration", OwnerGeneration: 7, SnapshotRevision: func() uint64 { return 19 }},
	)
	collector.RunOnce(ctx)
	if got := collector.Counters(); got.AcceptedObservations != 1 || got.UnavailableSpans != 0 {
		t.Fatalf("collector counters = %+v", got)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	database, err := OpenReadOnly(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if database.Header.SourceGeneration != 1 {
		t.Fatalf("source generation = %d, want 1", database.Header.SourceGeneration)
	}
	observations, err := database.ReadObservations(ctx, 0, 2e9, 10)
	if err != nil || len(observations) != 1 {
		t.Fatalf("agent_status observations = %v, err = %v", observations, err)
	}
	observation := observations[0]
	if observation.Family != "agent_status" || observation.OwnerGeneration != 7 {
		t.Fatalf("observation identity = %+v", observation)
	}
	var payload struct {
		States           map[string]string `json:"states"`
		SessionStates    map[string]string `json:"session_states"`
		SnapshotRevision uint64            `json:"snapshot_revision"`
	}
	raw, err := json.Marshal(observation.Payload)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	if payload.SnapshotRevision != 19 {
		t.Fatalf("agent_status payload = %#v", payload)
	}
	var counts struct {
		Total   int `json:"total"`
		Running int `json:"running"`
		Red     int `json:"red"`
		Yellow  int `json:"yellow"`
	}
	if err := json.Unmarshal(raw, &struct {
		Counts *struct {
			Total   int `json:"total"`
			Running int `json:"running"`
			Red     int `json:"red"`
			Yellow  int `json:"yellow"`
		} `json:"counts"`
	}{Counts: &counts}); err != nil {
		t.Fatal(err)
	}
	if counts.Total != 2 || counts.Running < 0 || counts.Red < 0 || counts.Yellow < 0 {
		t.Fatalf("agent_status counts = %+v, want total 2", counts)
	}
	var extended struct {
		Agents []map[string]any `json:"agents"`
	}
	if err := json.Unmarshal(raw, &extended); err != nil || len(extended.Agents) != 2 {
		t.Fatalf("agent_status roster = %#v", extended)
	}
	rosterSessions := map[string]bool{}
	for _, agent := range extended.Agents {
		rosterSessions[agent["session"].(string)] = true
	}
	if !rosterSessions[claudeSession] || !rosterSessions[codexSession] {
		t.Fatalf("agent_status roster sessions = %#v", extended.Agents)
	}
	t.Logf("agent_status counts=%v roster=%v", payloadCounts(raw), extended.Agents)
}

func payloadCounts(raw []byte) map[string]any {
	var payload struct {
		Counts map[string]any `json:"counts"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil
	}
	return payload.Counts
}

func integrationRepoRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "..", ".."))
}

func runTmux(binary, socket string, args ...string) error {
	return exec.Command(binary, append([]string{"-S", socket}, args...)...).Run()
}

func waitForPane(binary, socket, target string) error {
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		command := exec.Command(binary, "-S", socket, "capture-pane", "-p", "-J", "-t", target)
		if output, err := command.Output(); err == nil && strings.TrimSpace(string(output)) != "" {
			return nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	return errors.New("timed out waiting for mock client pane")
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}
