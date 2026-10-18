// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build !darwin && !linux

package main

import (
	"os"
	"os/exec"
	"syscall"
)

func nativeAgentProcessAttributes() *syscall.SysProcAttr {
	return nil
}

func stopNativeAgentProcess(command *exec.Cmd) error {
	if command.Process == nil {
		return os.ErrProcessDone
	}
	return command.Process.Kill()
}
