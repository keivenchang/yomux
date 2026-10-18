// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import "os"

// Launch validation and browser descriptions must describe the same command.
func goAgentCommand(agent, terminal string, fullAccess bool) string {
	command := agent
	if agent == "term" {
		command = terminal
		if command == "" {
			command = os.Getenv("SHELL")
		}
		if command == "" {
			command = "bash"
		}
	}
	return formatGoAgentCommand(agent, command, fullAccess)
}

func formatGoAgentCommand(agent, command string, fullAccess bool) string {
	if fullAccess {
		switch agent {
		case "claude":
			command += " --dangerously-skip-permissions"
		case "codex":
			command += " --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"
		}
	}
	return command
}
