package stats

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const maxAgentStatusCommandOutput = MaxAgentPaneCaptureBytes

// LinuxAgentStatusSources returns bounded live-system adapters. Every adapter
// fails closed when its OS identity or tmux target cannot be established.
func LinuxAgentStatusSources(database string) AgentStatusSources {
	return agentStatusSources(database, linuxAgentProcess)
}

// CurrentPlatformAgentStatusSources returns bounded adapters for the host OS.
// The Tabber status path is used on macOS as well as Linux, while inventory
// collection remains Linux-only YO!stats functionality.
func CurrentPlatformAgentStatusSources(database string) AgentStatusSources {
	return agentStatusSources(database, platformAgentProcess)
}

// CurrentPlatformAgentStatusProcess returns the live process evidence used by
// the shared status classifier, including its start identity for guarded sends.
func CurrentPlatformAgentStatusProcess(ctx context.Context, pid int) (AgentStatusProcess, error) {
	return platformAgentProcess(ctx, pid)
}

func agentStatusSources(database string, process func(context.Context, int) (AgentStatusProcess, error)) AgentStatusSources {
	return AgentStatusSources{
		CapturePane: linuxCaptureAgentPane,
		Process:     process,
		OpenCodeState: func(ctx context.Context, request AgentStatusRequest) (string, []string, error) {
			return OpenCodeSQLiteStateReader(database)(ctx, request)
		},
	}
}

// SyntheticAgentStatusSources keeps status classification on fixture-owned
// tmux and SQLite state. Process identity is deliberately reported as an
// opaque alive result because the synthetic inventory already supplies the
// admitted command/executable; no host /proc lookup is allowed in fixtures.
func SyntheticAgentStatusSources(database string) AgentStatusSources {
	return AgentStatusSources{
		CapturePane: func(ctx context.Context, target string) (AgentStatusPane, error) {
			if !syntheticTmuxSocketConfigured() {
				return AgentStatusPane{}, errSyntheticTmuxSocketUnset
			}
			return linuxCaptureAgentPane(ctx, target)
		},
		Process: func(ctx context.Context, pid int) (AgentStatusProcess, error) {
			if err := ctx.Err(); err != nil || pid <= 0 {
				if err != nil {
					return AgentStatusProcess{}, err
				}
				return AgentStatusProcess{}, errors.New("agent-process-unavailable")
			}
			return AgentStatusProcess{Alive: true}, nil
		},
		OpenCodeState: func(ctx context.Context, request AgentStatusRequest) (string, []string, error) {
			return OpenCodeSQLiteStateReader(database)(ctx, request)
		},
	}
}

func linuxCaptureAgentPane(ctx context.Context, target string) (AgentStatusPane, error) {
	if strings.TrimSpace(target) == "" || len([]byte(target)) > MaxIdentityBytes {
		return AgentStatusPane{}, errors.New("pane-target-unavailable")
	}
	commandContext, cancel := context.WithTimeout(ctx, 900*time.Millisecond)
	defer cancel()
	// Capture the visible screen only. Scrollback contains completed turns and
	// stale prompts, so mixing it into the current-state classifier produces
	// false "conflicting-pane-evidence" results and silently drops live agents.
	result, err := runBoundedCommand(commandContext, tmuxCommandArgs(linuxAgentPaneCaptureArgs(target)...))
	if err != nil || result.ExitCode != 0 || len(result.Stdout) > maxAgentStatusCommandOutput {
		if err != nil {
			return AgentStatusPane{}, fmt.Errorf("pane-capture-failed: %w", err)
		}
		return AgentStatusPane{}, errors.New("pane-capture-failed")
	}
	return AgentStatusPane{Target: target, VisibleText: string(result.Stdout), ObservedAt: time.Now()}, nil
}

func linuxAgentPaneCaptureArgs(target string) []string {
	return []string{"capture-pane", "-p", "-J", "-t", target}
}

func linuxAgentProcess(ctx context.Context, pid int) (AgentStatusProcess, error) {
	if err := ctx.Err(); err != nil {
		return AgentStatusProcess{}, err
	}
	if pid <= 0 {
		return AgentStatusProcess{}, errors.New("agent-process-unavailable")
	}
	command, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "cmdline"))
	if err != nil {
		return AgentStatusProcess{}, errors.New("agent-process-unavailable")
	}
	commandText := strings.ReplaceAll(string(command), "\x00", " ")
	executable, err := os.Readlink(filepath.Join("/proc", strconv.Itoa(pid), "exe"))
	if err != nil {
		return AgentStatusProcess{}, errors.New("agent-process-identity-unavailable")
	}
	startedIdentity, err := linuxStartedIdentity(pid)
	if err != nil {
		return AgentStatusProcess{}, errors.New("agent-process-identity-unavailable")
	}
	return AgentStatusProcess{Command: strings.TrimSpace(commandText), Alive: true, Executable: executable, StartedIdentity: startedIdentity}, nil
}
