// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync/atomic"

	"golang.org/x/sys/unix"
)

var terminalResizeClaimSequence atomic.Uint64

// All servers sharing tmux use the kernel's boot clock, rather than a process
// origin or adjustable wall clock. Fixed-width strings compare exactly in tmux.
func newTerminalResizeClaim() (string, error) {
	var now unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &now); err != nil {
		return "", err
	}
	return fmt.Sprintf("%020d-%010d-%020d", now.Nano(), os.Getpid(), terminalResizeClaimSequence.Add(1)), nil
}

func validTerminalResizeClaim(claim string) bool {
	if len(claim) != 52 || claim[20] != '-' || claim[31] != '-' {
		return false
	}
	for index, value := range claim {
		if index != 20 && index != 31 && (value < '0' || value > '9') {
			return false
		}
	}
	return true
}

type terminalResizeClient struct {
	tty, session, window, pid, created string
	cols, rows                         int
}

func terminalResizeClients(output []byte) []terminalResizeClient {
	var clients []terminalResizeClient
	for _, line := range strings.Split(strings.TrimSpace(string(output)), "\n") {
		fields := strings.Split(line, "\t")
		if len(fields) != 7 || !strings.HasPrefix(fields[0], "/dev/") || !strings.HasPrefix(fields[4], "@") {
			continue
		}
		// These kernel/tmux identities are embedded into formats, not shell code.
		if strings.IndexFunc(fields[0], func(r rune) bool {
			return !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || strings.ContainsRune("/_.-", r))
		}) >= 0 {
			continue
		}
		pid, err := strconv.ParseUint(fields[5], 10, 63)
		valid := err == nil && pid > 0
		for _, value := range []string{fields[2], fields[3], strings.TrimPrefix(fields[4], "@"), fields[6]} {
			if _, err := strconv.ParseUint(value, 10, 63); err != nil {
				valid = false
			}
		}
		if !valid {
			continue
		}
		cols, _ := strconv.Atoi(fields[2])
		rows, _ := strconv.Atoi(fields[3])
		clients = append(clients, terminalResizeClient{fields[0], fields[1], fields[4], fields[5], fields[6], cols, rows})
	}
	return clients
}

func (client terminalResizeClient) currentFormat() string {
	// A client loop includes connections before tty identification. Match the
	// numeric identity first through tmux's lazy conditional; && expands both sides.
	return fmt.Sprintf("#{L:#{?#{&&:#{==:#{client_pid},%s},#{==:#{client_created},%s}},#{?#{&&:#{==:#{client_tty},%s},#{==:#{window_id},%s}},1,},}}", client.pid, client.created, client.tty, client.window)
}

func tmuxResizeCommand(arguments ...string) string {
	quoted := make([]string, len(arguments))
	for index, argument := range arguments {
		quoted[index] = strconv.Quote(argument)
	}
	return strings.Join(quoted, " ")
}

func terminalResizeClaimArgs(clients []terminalResizeClient, active terminalResizeClient, cols, rows int, claim string) []string {
	const owner = "@yomux_resize_authority"
	current := "#{" + owner + "}"
	condition := "#{&&:" + active.currentFormat() + ",#{>=:" + claim + "," + current + "}}"
	commands := []string{tmuxResizeCommand("set-option", "-w", "-t", active.window, owner, claim)}
	for _, client := range clients {
		if client.window != active.window || client.tty != active.tty && client.cols <= cols && client.rows <= rows {
			continue
		}
		flag := "ignore-size"
		if client.tty == active.tty {
			flag = "!ignore-size"
		}
		// Guard inside tmux as well as at publication: a canceled Go process cannot
		// retract a command already queued in the shared external server.
		guard := "#{&&:" + active.currentFormat() + ",#{&&:#{==:" + current + "," + claim + "}," + client.currentFormat() + "}}"
		commands = append(commands, tmuxResizeCommand("if-shell", "-F", "-t", active.window, guard,
			tmuxResizeCommand("refresh-client", "-t", client.tty, "-f", flag)))
	}
	return []string{"if-shell", "-F", "-t", active.window, condition, strings.Join(commands, " ; ")}
}
