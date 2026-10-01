// SPDX-FileCopyright-Text: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build !linux && !darwin

package stats

import (
	"context"
	"errors"
)

func platformAgentProcess(_ context.Context, _ int) (AgentStatusProcess, error) {
	return AgentStatusProcess{}, errors.New("agent-process-unavailable")
}
