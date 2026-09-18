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
	return AgentStatusSources{
		CapturePane: linuxCaptureAgentPane,
		Process:     linuxAgentProcess,
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
	result, err := runBoundedCommand(commandContext, tmuxCommandArgs("capture-pane", "-p", "-J", "-S", "-12", "-t", target))
	if err != nil || result.ExitCode != 0 || len(result.Stdout) > maxAgentStatusCommandOutput {
		if err != nil {
			return AgentStatusPane{}, fmt.Errorf("pane-capture-failed: %w", err)
		}
		return AgentStatusPane{}, errors.New("pane-capture-failed")
	}
	return AgentStatusPane{Target: target, VisibleText: string(result.Stdout)}, nil
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
	return AgentStatusProcess{Command: strings.TrimSpace(commandText), Alive: true, Executable: executable}, nil
}
