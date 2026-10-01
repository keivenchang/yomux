// SPDX-FileCopyright-Text: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import "context"

func platformAgentProcess(ctx context.Context, pid int) (AgentStatusProcess, error) {
	return linuxAgentProcess(ctx, pid)
}
