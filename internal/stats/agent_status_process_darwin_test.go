// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"os"
	"testing"
)

func TestDarwinAgentStatusProcessFindsCurrentProcess(t *testing.T) {
	process, err := platformAgentProcess(context.Background(), os.Getpid())
	if err != nil {
		t.Fatalf("current process lookup failed: %v", err)
	}
	if !process.Alive || process.Command == "" || process.Executable == "" {
		t.Fatalf("current process identity is incomplete: %+v", process)
	}
}

func TestDarwinAgentStatusProcessRejectsMissingProcess(t *testing.T) {
	if _, err := platformAgentProcess(context.Background(), 0); err == nil {
		t.Fatal("PID 0 unexpectedly passed process validation")
	}
	if _, err := platformAgentProcess(context.Background(), 2147483647); err == nil {
		t.Fatal("missing PID unexpectedly passed process validation")
	}
}
