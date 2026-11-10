// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"errors"
)

func platformAgentProcess(ctx context.Context, pid int) (AgentStatusProcess, error) {
	if err := ctx.Err(); err != nil {
		return AgentStatusProcess{}, err
	}
	if pid <= 0 {
		return AgentStatusProcess{}, errors.New("agent-process-unavailable")
	}
	row, ok := darwinProcessIdentityFresh(pid)
	if !ok {
		return AgentStatusProcess{}, errors.New("agent-process-unavailable")
	}
	return AgentStatusProcess{Command: row.Command, Executable: row.Executable, StartedIdentity: row.Identity, StartedAt: row.StartedAt, Alive: true}, nil
}
