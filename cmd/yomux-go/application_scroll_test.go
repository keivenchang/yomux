// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestApplicationScrollLiteralPaneInput(t *testing.T) {
	for _, variant := range []struct {
		name, direction     string
		page, split, global bool
		pageRepeat          int
	}{
		{"wheel-up", "up", false, false, false, 1}, {"wheel-down", "down", false, false, false, 1},
		{"page-up", "up", true, false, false, 1}, {"page-down", "down", true, false, false, 1},
		{"batched-page-up", "up", true, false, false, 2},
		{"split-local-up", "up", false, true, false, 1}, {"split-local-down", "down", false, true, false, 1},
		{"split-global-up", "up", false, true, true, 1}, {"split-global-down", "down", false, true, true, 1},
	} {
		t.Run(variant.name, func(t *testing.T) {
			runtime, err := os.MkdirTemp("/tmp", "app-scroll-")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = os.RemoveAll(runtime) })
			socket := filepath.Join(runtime, "tmux.sock")
			t.Setenv("YOMUX_TMUX_SOCKET", socket)
			t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
			s := &server{}
			run := func(args ...string) string {
				t.Helper()
				output, err := s.tmuxCommand(args...).CombinedOutput()
				if err != nil {
					t.Fatalf("tmux %q: %v %s", args, err, output)
				}
				return strings.TrimSpace(string(output))
			}
			expected := strings.Repeat("\x1b[<64;17;9M", 2)
			if variant.direction == "down" {
				expected = strings.Repeat("\x1b[<65;17;9M", 2)
			}
			if variant.page {
				expected = strings.Repeat("\x1b[5~", variant.pageRepeat)
				if variant.direction == "down" {
					expected = strings.Repeat("\x1b[6~", variant.pageRepeat)
				}
			}
			ready := filepath.Join(runtime, "ready")
			received := filepath.Join(runtime, "received")
			script := fmt.Sprintf("import os,tty; from pathlib import Path; tty.setraw(0); Path(%q).touch(); data=b''\nwhile len(data)<%d: data+=os.read(0,%d-len(data))\noutput=Path(%q); temporary=output.with_suffix('.tmp'); temporary.write_bytes(data); temporary.replace(output)\n", ready, len(expected), len(expected), received)
			scriptPath := filepath.Join(runtime, "receiver.py")
			if err := os.WriteFile(scriptPath, []byte(script), 0600); err != nil {
				t.Fatal(err)
			}
			run("new-session", "-d", "-s", "fixture", "-x", "120", "-y", "40", "python3", scriptPath)
			t.Cleanup(func() { _ = s.tmuxCommand("kill-server").Run() })
			waitFile := func(path string) []byte {
				t.Helper()
				timer := time.NewTimer(5 * time.Second)
				defer timer.Stop()
				ticker := time.NewTicker(10 * time.Millisecond)
				defer ticker.Stop()
				for {
					if data, err := os.ReadFile(path); err == nil {
						return data
					} else if !os.IsNotExist(err) {
						t.Fatal(err)
					}
					select {
					case <-timer.C:
						t.Fatalf("timed out waiting for %s", path)
					case <-ticker.C:
					}
				}
			}
			waitFile(ready)
			pane := run("display-message", "-p", "-t", "=fixture:", "#{pane_id}")
			run("new-session", "-d", "-s", "foreign")
			foreign := run("display-message", "-p", "-t", "=foreign:", "#{pane_id}")
			for _, row := range []struct {
				pane, direction    string
				count, column, row int
			}{
				{foreign, "up", 1, 1, 1}, {pane, "left", 1, 1, 1}, {pane, "up", 0, 1, 1}, {pane, "up", 13, 1, 1},
				{pane, "up", 1, 0, 1}, {pane, "up", 1, 121, 1}, {pane, "up", 1, 1, 41}, {"=fixture:", "up", 1, 1, 1},
			} {
				if err := s.applicationScroll(context.Background(), "fixture", row.pane, row.direction, row.count, row.column, row.row, false, false); err == nil {
					t.Fatalf("accepted invalid scroll %+v", row)
				}
			}
			column, row, count := 17, 9, 2
			if variant.page {
				count = variant.pageRepeat
			}
			if variant.split {
				run("split-window", "-h", "-b", "-t", pane, "cat")
				run("split-window", "-v", "-b", "-t", pane, "cat")
			}
			if variant.global {
				offsets := strings.Fields(run("display-message", "-p", "-t", pane, "#{pane_left} #{pane_top}"))
				left, err := strconv.Atoi(offsets[0])
				if err != nil {
					t.Fatal(err)
				}
				top, err := strconv.Atoi(offsets[1])
				if err != nil {
					t.Fatal(err)
				}
				column += left
				row += top
			}
			run("copy-mode", "-t", pane)
			payload := []byte(fmt.Sprintf(`{"type":"application-scroll","pane":%q,"direction":%q,"count":%d,"column":%d,"row":%d,"page":%t,"global":%t}`, pane, variant.direction, count, column, row, variant.page, variant.global))
			s.handleMessage(io.Discard, nil, nil, nil, "fixture", "", payload, false, context.Background())
			if run("display-message", "-p", "-t", pane, "#{pane_in_mode}") != "1" {
				t.Fatal("readonly scroll left copy mode")
			}
			s.handleMessage(io.Discard, nil, nil, nil, "fixture", "", payload, true, context.Background())
			if got := string(waitFile(received)); got != expected {
				t.Fatalf("literal input = %q", got)
			}
			if len(s.terminalActivity.clocks) != 0 {
				t.Fatal("scroll recorded typing activity")
			}
		})
	}
}

func TestApplicationScrollMalformedFieldsNeverBecomeTyping(t *testing.T) {
	s := &server{}
	for _, field := range []string{`"count":"2"`, `"count":true`, `"row":1.5`, `"global":"true"`, `"page":1`} {
		var typed bytes.Buffer
		payload := []byte(`{"type":"application-scroll",` + field + `}`)
		s.handleMessage(&typed, nil, nil, nil, "fixture", "", payload, true, context.Background())
		if typed.Len() != 0 {
			t.Fatalf("invalid scroll sent as typing: %s", field)
		}
	}
}
