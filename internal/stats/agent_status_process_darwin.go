// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"errors"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func platformAgentProcess(ctx context.Context, pid int) (AgentStatusProcess, error) {
	if err := ctx.Err(); err != nil {
		return AgentStatusProcess{}, err
	}
	if pid <= 0 {
		return AgentStatusProcess{}, errors.New("agent-process-unavailable")
	}
	if runtime.GOOS == "darwin" {
		row, ok := darwinProcessIdentityFresh(pid)
		if !ok {
			return AgentStatusProcess{}, errors.New("agent-process-unavailable")
		}
		return AgentStatusProcess{Command: row.Command, Executable: row.Executable, StartedIdentity: row.Identity, Alive: true}, nil
	}
	commandContext, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
	defer cancel()
	result, err := runBoundedCommand(commandContext, []string{"/bin/ps", "-ww", "-p", strconv.Itoa(pid), "-o", "command="})
	if err != nil || result.ExitCode != 0 || len(result.Stdout) > MaxAgentPaneCaptureBytes {
		return AgentStatusProcess{}, errors.New("agent-process-unavailable")
	}
	command := strings.TrimSpace(string(result.Stdout))
	if command == "" {
		return AgentStatusProcess{}, errors.New("agent-process-unavailable")
	}
	argv := shellWords(command)
	if len(argv) == 0 {
		return AgentStatusProcess{}, errors.New("agent-process-identity-unavailable")
	}
	return AgentStatusProcess{Command: command, Executable: filepath.Clean(argv[0]), Alive: true}, nil
}
