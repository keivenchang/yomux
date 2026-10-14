// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"
)

type terminalViewportPane struct {
	id, window, mode, incarnation string
	width, height, scroll, x, y   int
}

func (s *server) terminalViewportInventory(ctx context.Context, target string) ([]terminalViewportPane, error) {
	const format = "#{pane_id}\t#{window_id}\t#{pane_mode}\t#{pane_width}\t#{pane_height}\t#{scroll_position}\t#{copy_cursor_x}\t#{copy_cursor_y}\t#{pid}:#{start_time}"
	out, err := s.tmuxCommandContext(ctx, "list-panes", "-t", target, "-F", format).Output()
	if err != nil {
		return nil, err
	}
	var panes []terminalViewportPane
	for _, line := range strings.Split(strings.TrimSuffix(string(out), "\n"), "\n") {
		if line == "" {
			continue
		}
		fields := strings.Split(line, "\t")
		if len(fields) != 9 || !strings.HasPrefix(fields[0], "%") || !strings.HasPrefix(fields[1], "@") {
			return nil, fmt.Errorf("invalid terminal viewport inventory")
		}
		pane := terminalViewportPane{id: fields[0], window: fields[1], mode: fields[2], incarnation: fields[8]}
		values := []*int{&pane.width, &pane.height, &pane.scroll, &pane.x, &pane.y}
		for i, value := range values {
			if fields[i+3] == "" && pane.mode != "copy-mode" && i >= 2 {
				continue
			}
			parsed, err := strconv.Atoi(fields[i+3])
			if err != nil || parsed < 0 {
				return nil, fmt.Errorf("invalid terminal viewport coordinate")
			}
			*value = parsed
		}
		panes = append(panes, pane)
	}
	return panes, nil
}

func (s *server) awaitTerminalClientSize(ctx context.Context, tty, window string, cols, rows int) error {
	if tty == "" {
		return nil
	}
	waitCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	for waitCtx.Err() == nil {
		out, err := s.tmuxCommandContext(waitCtx, "list-clients", "-F", "#{client_tty}\t#{client_width}\t#{client_height}\t#{window_id}\t#{window_width}\t#{window_height}\t#{status}").Output()
		if err != nil {
			return err
		}
		var fields []string
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			candidate := strings.Split(line, "\t")
			if len(candidate) == 7 && candidate[0] == tty {
				fields = candidate[1:]
				break
			}
		}
		if len(fields) != 6 {
			return fmt.Errorf("invalid terminal geometry readback")
		}
		width, _ := strconv.Atoi(fields[0])
		height, _ := strconv.Atoi(fields[1])
		windowWidth, _ := strconv.Atoi(fields[3])
		windowHeight, _ := strconv.Atoi(fields[4])
		statusLines, _ := strconv.Atoi(fields[5])
		if fields[5] == "on" {
			statusLines = 1
		}
		if fields[2] != window {
			return fmt.Errorf("terminal active window changed during resize")
		}
		if width == cols && height == rows && windowWidth == cols && windowHeight == rows-statusLines {
			return nil
		}
	}
	return waitCtx.Err()
}

// tmux owns copy-mode reflow. Repositioning its cursor here can overwrite input
// from another YOmux server or a native tmux client, which cannot be fenced locally.
func (s *server) resizeTerminalNative(ctx context.Context, session, tty string, cols, rows int, resize func(int, int) error, authority bool) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	panes, err := s.terminalViewportInventory(ctx, s.exactTarget(session))
	if err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := resize(rows, cols); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if authority {
		s.claimResizeAuthority(ctx, session, tty, cols, rows)
		if len(panes) > 0 {
			return s.awaitTerminalClientSize(ctx, tty, panes[0].window, cols, rows)
		}
	}
	return ctx.Err()
}
