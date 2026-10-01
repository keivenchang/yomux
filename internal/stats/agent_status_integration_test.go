package stats

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/testsupport"
)

// This test deliberately keeps a private tmux server and live-like client panes
// alive for the full integration budget while using synthetic process identity
// and status seams. The tmux server is fixture-owned; no host /proc snapshot is
// admitted into development or test runs.
func TestAgentStatusWithSyntheticInventoryAndMockClient(t *testing.T) {
	// tmux's control-format delimiters are locale-sensitive. Pin the adapter's process environment
	// so this fixture exercises the same UTF-8 contract in a Bazel action and a direct Go run.
	t.Setenv("LANG", "C.UTF-8")
	t.Setenv("LC_ALL", "C.UTF-8")
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatalf("python3 is unavailable: %v", err)
	}
	tmux, err := exec.LookPath("tmux")
	if err != nil {
		t.Fatalf("tmux is unavailable: %v", err)
	}
	repoRoot := testsupport.RepoRoot(t)
	mock := testsupport.RepoPath(t, "tools/mockers/claude.py")
	if _, err := os.Stat(mock); err != nil {
		t.Fatalf("mock client is unavailable: %v", err)
	}

	root := t.TempDir()
	// The mock client and the host-side test image may probe this vendor
	// directory during startup. Keep it inside the synthetic HOME so that a
	// missing optional host directory cannot become visible pane error text.
	if err := os.Mkdir(filepath.Join(root, ".GlobalProtect"), 0700); err != nil {
		t.Fatal(err)
	}
	socketRoot, err := os.MkdirTemp("/tmp", "ym-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(socketRoot); err != nil {
			t.Errorf("remove private tmux socket directory: %v", err)
		}
	})
	socket := filepath.Join(socketRoot, "tmux.sock")
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)

	sessionPrefix := "yolo-go-status-" + strings.ToLower(strings.ReplaceAll(filepath.Base(root), "_", "-"))
	claudeSession := sessionPrefix + "-claude"
	codexSession := sessionPrefix + "-codex"
	t.Cleanup(func() {
		if err := cleanupTmuxSession(tmux, socket, claudeSession); err != nil {
			t.Errorf("cleanup Claude tmux session: %v", err)
		}
		if err := cleanupTmuxSession(tmux, socket, codexSession); err != nil {
			t.Errorf("cleanup Codex tmux session: %v", err)
		}
		if err := cleanupTmuxServer(tmux, socket); err != nil {
			t.Errorf("cleanup private tmux server: %v", err)
		}
	})
	path := os.Getenv("PATH")
	if path == "" {
		t.Fatal("PATH is required for the private tmux fixture")
	}
	// Keep the mock client environment deterministic and separate from the Go/Bazel action. In
	// particular, do not leak the action's module cache, proxy, or test-only variables into panes.
	env := []string{
		"PATH=" + path,
		"PYTHONPATH=" + repoRoot,
		"PYTHONUSERBASE=" + os.Getenv("PYTHONUSERBASE"),
		"HOME=" + root,
		"TERM=xterm-256color",
		"LANG=C.UTF-8",
		"LC_ALL=C.UTF-8",
		"PYTHONUNBUFFERED=1",
	}
	startMock := func(session, entryPoint, client string) {
		start := exec.Command(tmux, "-S", socket, "new-session", "-d", "-s", session, "-x", "100", "-y", "35", "bash", "-c", "exec -a "+entryPoint+" "+shellQuote(python)+" -u "+shellQuote(client)+" --mock")
		start.Env = env
		if output, err := start.CombinedOutput(); err != nil {
			t.Fatalf("private tmux server cannot start %s: %v: %s", entryPoint, err, output)
		}
	}
	startMock(claudeSession, "claude", mock)
	codexMock := testsupport.RepoPath(t, "tools/mockers/codex.py")
	if _, err := os.Stat(codexMock); err != nil {
		t.Fatalf("codex mock client is unavailable: %v", err)
	}
	startMock(codexSession, "codex", codexMock)

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
	inventorySources := SyntheticAgentInventorySources()
	for time.Now().Before(deadline) {
		inventory, errorsOut := BuildAgentInventory(ctx, inventorySources)
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
		diagnosticCtx := context.Background()
		panes, paneErr := linuxTmuxPanes(diagnosticCtx)
		rawPanes, rawPaneErr := exec.Command(tmux, "-S", socket, "list-panes", "-a", "-F", "#{session_name}\t#{window_index}\t#{window_name}\t#{pane_index}\t#{pane_id}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_title}\t#{pane_pid}").CombinedOutput()
		t.Fatalf("mock clients were not discovered in synthetic inventory (errors=%v panes=%+v paneErr=%v records=%+v rawPanes=%q rawPaneErr=%v)", inventoryErrors, panes, paneErr, recordsBySession, rawPanes, rawPaneErr)
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
			t.Fatalf("fixture pane ID session=%s target=%q", session, record.TmuxPaneID)
		}
		captureArgs := append([]string{"-S", socket}, linuxAgentPaneCaptureArgs(record.TmuxPaneID)...)
		if output, err := exec.Command(tmux, captureArgs...).CombinedOutput(); err != nil || strings.TrimSpace(string(output)) == "" {
			t.Fatalf("direct fixture PaneID capture session=%s target=%q err=%v output=%q", session, record.TmuxPaneID, err, output)
		}
	}
	for _, session := range []string{claudeSession, codexSession} {
		record := recordsBySession[session]
		if got, err := linuxCaptureAgentPane(ctx, record.TmuxPaneID); err != nil {
			t.Fatalf("fixture pane capture session=%s target=%q: %v", session, record.TmuxPaneID, err)
		} else {
			t.Logf("fixture tmux args=%q inventory=%+v pane target=%q capture bytes=%d", tmuxCommandArgs(linuxAgentPaneCaptureArgs(record.TmuxPaneID)...), record, got.Target, len(got.VisibleText))
			if strings.TrimSpace(got.VisibleText) == "" {
				t.Fatalf("fixture pane capture was empty for %s", session)
			}
		}
	}
	for _, session := range []string{claudeSession, codexSession} {
		record := recordsBySession[session]
		classified := ClassifyAgentStatus(ctx, AgentStatusRequest{Kind: record.Kind, PID: record.PID, Command: record.Command, Executable: record.Executable, PaneTarget: record.TmuxPaneID}, SyntheticAgentStatusSources(filepath.Join(root, "missing-opencode.sqlite3")))
		if classified.Status == AgentStatusUnavailable || classified.Reason == "pane-capture-failed" {
			pane, paneErr := linuxCaptureAgentPane(ctx, record.TmuxPaneID)
			t.Fatalf("fixture agent status classification failed for %s: %+v pane_err=%v visible=%q", session, classified, paneErr, pane.VisibleText)
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
			return ClassifyAgentStatus(ctx, request, SyntheticAgentStatusSources(databasePath))
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

func tmuxStatus(binary, socket string, args ...string) (int, []byte) {
	command := exec.Command(binary, append([]string{"-S", socket}, args...)...)
	output, err := command.CombinedOutput()
	if err == nil {
		return 0, output
	}
	var exitError *exec.ExitError
	if errors.As(err, &exitError) {
		return exitError.ExitCode(), output
	}
	return -1, output
}

func cleanupTmuxSession(binary, socket, session string) error {
	killCode, killOutput := tmuxStatus(binary, socket, "kill-session", "-t", session)
	probeCode, probeOutput := tmuxStatus(binary, socket, "has-session", "-t", session)
	if probeCode == 0 {
		detail := strings.TrimSpace(string(killOutput))
		if detail == "" {
			detail = "kill-session did not retire the session"
		}
		return fmt.Errorf("session %s survived cleanup: %s", session, detail)
	}
	if probeCode != 1 {
		detail := strings.TrimSpace(string(probeOutput))
		if detail == "" {
			detail = fmt.Sprintf("has-session exited %d", probeCode)
		}
		return fmt.Errorf("could not prove session %s was retired: %s", session, detail)
	}
	if killCode < 0 {
		return fmt.Errorf("kill-session could not execute: %s", strings.TrimSpace(string(killOutput)))
	}
	return nil
}

func cleanupTmuxServer(binary, socket string) error {
	killCode, killOutput := tmuxStatus(binary, socket, "kill-server")
	probeCode, probeOutput := tmuxStatus(binary, socket, "list-sessions")
	if probeCode == 0 {
		detail := strings.TrimSpace(string(killOutput))
		if detail == "" {
			detail = "kill-server did not retire the server"
		}
		return fmt.Errorf("tmux server survived cleanup: %s", detail)
	}
	if probeCode != 1 {
		detail := strings.TrimSpace(string(probeOutput))
		if detail == "" {
			detail = fmt.Sprintf("list-sessions exited %d", probeCode)
		}
		return fmt.Errorf("could not prove tmux server was retired: %s", detail)
	}
	if killCode < 0 {
		return fmt.Errorf("kill-server could not execute: %s", strings.TrimSpace(string(killOutput)))
	}
	return nil
}

func waitForPane(binary, socket, target string) error {
	deadline := time.Now().Add(5 * time.Second)
	var lastOutput string
	var lastErr error
	for time.Now().Before(deadline) {
		command := exec.Command(binary, "-S", socket, "capture-pane", "-p", "-J", "-t", target)
		output, err := command.CombinedOutput()
		lastOutput, lastErr = string(output), err
		if err == nil && strings.TrimSpace(lastOutput) != "" {
			return nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	return fmt.Errorf("timed out waiting for mock client pane: last error=%v output=%q", lastErr, lastOutput)
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}
