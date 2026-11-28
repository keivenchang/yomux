// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
)

// Mouse reports must bypass the attached tmux client's mouse decoder. Only a live pane in
// this WebSocket's session may receive the bounded, server-generated literal reports.
func (s *server) applicationScroll(ctx context.Context, session, pane, direction string, count, column, row int, page, global bool) error {
	if len(pane) < 2 || pane[0] != '%' || strings.Trim(pane[1:], "0123456789") != "" || (direction != "up" && direction != "down") || count < 1 || count > 12 || column < 1 || column > 500 || row < 1 || row > 300 {
		return fmt.Errorf("invalid application scroll")
	}
	output, err := s.tmuxPointCommand(ctx, "list-panes", "-s", "-t", s.exactTarget(session), "-F", "#{pane_id} #{pane_width} #{pane_height} #{pane_in_mode} #{pane_left} #{pane_top} #{mouse_sgr_flag}").Output()
	if err != nil {
		return fmt.Errorf("validate scroll session: %w", err)
	}
	for _, line := range strings.Split(string(output), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 7 || fields[0] != pane {
			continue
		}
		width, widthErr := strconv.Atoi(fields[1])
		height, heightErr := strconv.Atoi(fields[2])
		left, leftErr := strconv.Atoi(fields[4])
		top, topErr := strconv.Atoi(fields[5])
		if widthErr != nil || heightErr != nil || leftErr != nil || topErr != nil {
			return fmt.Errorf("invalid pane geometry")
		}
		if !page && global {
			column -= left
			row -= top
		}
		// The browser grid can differ from the tmux window when another client sized it last.
		// Wheel position does not matter to the application, so keep the report inside the pane.
		column = clampDimension(column, 1, width, 1)
		row = clampDimension(row, 1, height, 1)
		if fields[3] != "0" {
			if fields[3] != "1" {
				return fmt.Errorf("invalid pane mode")
			}
			if err := s.tmuxPointCommand(ctx, "send-keys", "-X", "-t", pane, "cancel").Run(); err != nil {
				return fmt.Errorf("leave copy mode: %w", err)
			}
		}
		button := 64
		if direction == "down" {
			button = 65
		}
		report := fmt.Sprintf("\x1b[<%d;%d;%dM", button, column, row)
		if fields[6] != "1" {
			// The pane did not ask for SGR reports, so use the original byte encoding (positions to 223).
			report = string([]byte{0x1b, '[', 'M', byte(32 + button), byte(32 + min(column, 223)), byte(32 + min(row, 223))})
		}
		if page {
			report = "\x1b[5~"
			if direction == "down" {
				report = "\x1b[6~"
			}
		}
		if err := s.tmuxPointCommand(ctx, "send-keys", "-l", "-t", pane, "--", strings.Repeat(report, count)).Run(); err != nil {
			return fmt.Errorf("deliver application scroll: %w", err)
		}
		return nil
	}
	return fmt.Errorf("scroll pane does not belong to session")
}

func (s *server) rejectApplicationScroll(conn *wsConn, session string, err error) {
	slog.Warn("application scroll rejected", "session", session, "error", err)
	if conn != nil {
		frame, marshalErr := json.Marshal(map[string]string{"type": "application-scroll-error", "reason": "scroll-rejected", "detail": err.Error()})
		if marshalErr != nil {
			slog.Warn("application scroll rejection not encoded", "session", session, "error", marshalErr)
			frame = []byte(`{"type":"application-scroll-error","reason":"scroll-rejected"}`)
		}
		if err := conn.frame(1, frame); err != nil {
			slog.Warn("application scroll rejection not delivered", "session", session, "error", err)
		}
	}
}
