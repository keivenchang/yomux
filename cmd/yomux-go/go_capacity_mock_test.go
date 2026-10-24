// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/testsupport"
)

// Drive the actual manual mock through the same classifier and guarded worker
// cycle used by YO, with the real process identity of its fixture-owned PID.
func TestGoYoloCapacityManualMock(t *testing.T) {
	for _, scenario := range []string{"", "pending", "2"} {
		t.Run("scenario="+scenario, func(t *testing.T) {
			root := testsupport.ShortTempDir(t)
			socket := filepath.Join(root, "tmux.sock")
			t.Setenv(productenv.Name("TMUX_SOCKET"), socket)
			runTmux := func(args ...string) string {
				t.Helper()
				output, err := exec.Command("tmux", append([]string{"-S", socket}, args...)...).CombinedOutput()
				if err != nil {
					t.Fatalf("private tmux %v: %v (%s)", args, err, output)
				}
				return string(output)
			}
			command := "exec env HOME=" + strconv.Quote(root) + " python3 " + strconv.Quote(testsupport.RepoPath(t, "tools/mockers/codex.py")) + " --mock"
			runTmux("new-session", "-d", "-s", "capacity-mock", "-x", "110", "-y", "45", command)
			t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
			target := strings.TrimSpace(runTmux("display-message", "-p", "-t", "capacity-mock", "#{pane_id}"))
			pid, err := strconv.Atoi(strings.TrimSpace(runTmux("display-message", "-p", "-t", target, "#{pane_pid}")))
			if err != nil {
				t.Fatal(err)
			}
			capture := func() string { return runTmux("capture-pane", "-p", "-t", target) }
			waitScreen := func(predicate func(string) bool) string {
				t.Helper()
				deadline := time.NewTimer(5 * time.Second)
				ticker := time.NewTicker(15 * time.Millisecond)
				defer deadline.Stop()
				defer ticker.Stop()
				for {
					screen := capture()
					if predicate(screen) {
						return screen
					}
					select {
					case <-deadline.C:
						t.Fatalf("mock did not reach expected screen: %q", screen)
					case <-ticker.C:
					}
				}
			}
			waitScreen(func(screen string) bool { return strings.Contains(screen, "Explain this codebase") })
			runTmux("send-keys", "-t", target, strings.TrimSpace("mock capacity "+scenario), "Enter")
			waitScreen(goCapacityNoticeCurrent)
			config := filepath.Join(root, "config")
			store, err := newGoYoloStore(config)
			if err != nil {
				t.Fatal(err)
			}
			defer store.close()
			process, err := stats.CurrentPlatformAgentStatusProcess(context.Background(), pid)
			if err != nil || !process.Alive || stats.ClassifyAgent(process.Command, process.Executable) != "codex" {
				t.Fatalf("manual mock is not discoverable: process=%+v err=%v", process, err)
			}
			processIdentity := stats.AgentStatusProcessInstanceID(stats.AgentRecord{PID: pid, StartedIdentity: process.StartedIdentity})
			srv := &server{root: testsupport.RepoRoot(t), config: config, state: filepath.Join(root, "state"), yolo: store,
				agentProcessSnapshot: stats.CurrentPlatformAgentStatusProcess,
				processEntriesFn: func() ([]procEntry, error) {
					current, err := stats.CurrentPlatformAgentStatusProcess(context.Background(), pid)
					return []procEntry{{PID: pid, Command: current.Command, Executable: current.Executable}}, err
				},
			}
			srv.agentStatusClassifier = func(ctx context.Context, request stats.AgentStatusRequest) stats.AgentStatusResult {
				return stats.ClassifyAgentStatus(ctx, request, stats.AgentStatusSources{
					Process: stats.CurrentPlatformAgentStatusProcess,
					CapturePane: func(context.Context, string) (stats.AgentStatusPane, error) {
						return stats.AgentStatusPane{VisibleText: capture(), ObservedAt: time.Now()}, nil
					},
				})
			}
			identity, err := srv.goYoloSessionIdentity(context.Background(), "capacity-mock")
			if err != nil {
				t.Fatal(err)
			}
			if err := store.set("capacity-mock", true, identity); err != nil {
				t.Fatal(err)
			}
			// Force publication lag once per paste. The real mock still receives
			// every byte, but the immediate Enter guard sees its pre-paste frame.
			var staleCapture string
			paintDeferrals := 0
			srv.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
				if args[0] == "paste-buffer" {
					staleCapture = capture()
				}
				if args[0] == "capture-pane" && staleCapture != "" {
					frame := staleCapture
					staleCapture = ""
					paintDeferrals++
					return exec.CommandContext(ctx, "printf", "%s", frame)
				}
				return exec.CommandContext(ctx, "tmux", append([]string{"-S", socket}, args...)...)
			}
			refreshMetadata := func() error {
				now := time.Now()
				agent := map[string]any{
					"kind": "codex", "pane_target": target, "pid": pid, "process_instance_id": processIdentity,
					"command": process.Command, "executable": process.Executable,
				}
				state, fresh := srv.goYoloAgentStatusNow(context.Background(), agent)
				if !fresh {
					return errors.New("private mock agent status is unavailable")
				}
				agent["status"] = state
				agent["status_observed_at"] = float64(now.UnixNano()) / 1e9
				srv.metadataMu.Lock()
				srv.metadataPayload = map[string]any{"sessions": map[string]any{"capacity-mock": map[string]any{"agents": []map[string]any{agent}}}}
				srv.metadataBuiltAt = now
				srv.metadataMu.Unlock()
				return nil
			}
			srv.goYoloMetadataRefreshFn = func(string) error { return refreshMetadata() }
			if err := refreshMetadata(); err != nil {
				t.Fatal(err)
			}
			t.Logf("capacity fixture scenario=%q root=%q config=%q state=%q socket=%q tmux_pid=%q pane=%q pane_pid=%d process_instance=%q session_identity=%q session_lock=%q pane_lock=%q browser=none predecessor=none(new-private-root-and-socket)", scenario, root, config, srv.state, socket, strings.TrimSpace(runTmux("display-message", "-p", "-t", target, "#{pid}")), target, pid, processIdentity, identity, goYoloTargetLockPath("capacity-mock"), goYoloTargetLockPath(target))
			if err := srv.startGoYoloWorker("capacity-mock"); err != nil {
				t.Fatal(err)
			}
			// Retire and join the worker before its store or private tmux server.
			defer srv.stopGoYoloWorker("capacity-mock")
			attempts := func() int {
				store.mu.Lock()
				defer store.mu.Unlock()
				return store.attempts["capacity-mock"]
			}
			advanceRetryClock := func() {
				now := time.Now()
				store.mu.Lock()
				for key := range store.lastSent {
					store.lastSent[key] = now.Add(-goYoloCapacityRetryDelay)
				}
				store.mu.Unlock()
			}
			for retry := 0; retry < 3; retry++ {
				if retry > 0 {
					advanceRetryClock()
				}
				if scenario == "pending" {
					waitScreen(func(screen string) bool { return strings.Contains(screen, "› continue") && attempts() == retry*2+1 })
					advanceRetryClock()
				}
				if scenario == "2" && retry == 2 {
					waitScreen(func(screen string) bool { return strings.Contains(screen, "• Working") && attempts() == 3 })
					waitScreen(func(screen string) bool { return strings.Contains(screen, "• Done.") })
					break
				}
				wantNotices := retry + 2
				wantAttempts := retry + 1
				if scenario == "pending" {
					wantAttempts *= 2
				}
				waitScreen(func(screen string) bool {
					return strings.Count(screen, "■ Selected model is at capacity.") == wantNotices && !strings.Contains(screen, "› continue\n\n› continue") && attempts() == wantAttempts
				})
			}
			srv.stopGoYoloWorker("capacity-mock")
			if paintDeferrals != 3 {
				t.Fatalf("post-paste stale captures = %d, want 3", paintDeferrals)
			}
			wantAttempts := 3
			if scenario == "pending" {
				wantAttempts = 6
			}
			if got := store.attempts["capacity-mock"]; got != wantAttempts {
				t.Fatalf("terminal deliveries = %d, want %d", got, wantAttempts)
			}
		})
	}
}
