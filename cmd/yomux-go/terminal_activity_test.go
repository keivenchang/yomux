// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestTerminalInputActivityClassification(t *testing.T) {
	if !terminalInputIsActivity([]byte("\x1b[1;2R"), true) {
		t.Fatal("native modified F3 excluded")
	}
	for _, page := range []string{"\x1b[5~", "\x1b[6~", "\x1b[5;2~", "\x1b[6;5~"} {
		if terminalInputIsActivity([]byte(page), true) || !terminalInputIsActivity([]byte(page+"a"), true) {
			t.Errorf("page navigation activity classification: %q", page)
		}
	}
	for column := 33; column <= 255; column++ {
		for row := 33; row <= 255; row++ {
			report := []byte{0x1b, '[', 'M', 0x60, byte(column), byte(row)}
			if terminalInputIsActivity(report) || !terminalInputIsActivity(append(report, 'a')) {
				t.Fatalf("legacy mouse activity classification at %d,%d", column, row)
			}
		}
	}
	for _, data := range []string{"", "\x1b[I", "\x1b[O", "\x1b[<0;4;8M", "\x1b[<0;4;8m", "\x1b[M !!", "\x1b[12;40R", "\x1b[?1;2c", "\x1b[4;600;800t", "\x1bP1$r0m\x1b\\", "\x1b]10;rgb:ffff/ffff/ffff\x07", "\x1b[200~\x1b[201~"} {
		if terminalInputIsActivity([]byte(data)) {
			t.Errorf("protocol counted as typing: %q", data)
		}
	}
	for _, data := range []string{"a", "\x00", "\r", "\t", "\x7f", "\x03", "\x1b[A", "\x1b[200~pasted text\x1b[201~", "\x1b[Ikey"} {
		if !terminalInputIsActivity([]byte(data)) {
			t.Errorf("keyboard/paste not counted: %q", data)
		}
	}
}

func TestTerminalTmuxScrollTargetsPaneBeforeCopyModeCommand(t *testing.T) {
	var commands [][]string
	s := &server{tmuxCommandContextFn: func(_ context.Context, args ...string) *exec.Cmd {
		commands = append(commands, append([]string(nil), args...))
		return exec.Command("true")
	}}
	s.handleMessage(io.Discard, nil, nil, nil, "fixture", "", []byte(`{"type":"tmux-scroll","direction":"up","lines":5}`), true, context.Background())
	want := [][]string{
		{"copy-mode", "-e", "-t", "=fixture:"},
		{"send-keys", "-X", "-t", "=fixture:", "-N", "5", "scroll-up"},
	}
	if !reflect.DeepEqual(commands, want) {
		t.Fatalf("tmux scroll commands = %#v, want %#v", commands, want)
	}
}

func TestTerminalTmuxScrollWorksWithMouseEnabledOrDisabled(t *testing.T) {
	for _, mouse := range []string{"off", "on"} {
		t.Run(mouse, func(t *testing.T) {
			socket := filepath.Join(t.TempDir(), "tmux.sock")
			t.Setenv("YOMUX_TMUX_SOCKET", socket)
			t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
			s := &server{ignoreSizeChecked: true}
			if output, err := s.tmuxCommand("new-session", "-d", "-s", "fixture", "seq 1 1000; echo YOMUX_SCROLL_FIXTURE_DONE; exec cat").CombinedOutput(); err != nil {
				t.Fatalf("create isolated tmux session: %v %s", err, output)
			}
			t.Cleanup(func() { _ = s.tmuxCommand("kill-server").Run() })
			for _, args := range [][]string{{"set-option", "-g", "history-limit", "2000"}, {"set-option", "-g", "mouse", mouse}} {
				if output, err := s.tmuxCommand(args...).CombinedOutput(); err != nil {
					t.Fatalf("configure isolated tmux: %v %s", err, output)
				}
			}
			waitFixtureCondition(t, "completed tmux scroll fixture", 5*time.Second, func() bool {
				output, err := s.tmuxCommand("capture-pane", "-p", "-t", "fixture:0.0").Output()
				return err == nil && strings.Contains(string(output), "YOMUX_SCROLL_FIXTURE_DONE")
			})
			s.handleMessage(io.Discard, nil, nil, nil, "fixture", "", []byte(`{"type":"tmux-scroll","direction":"up","lines":5}`), true, context.Background())
			output, err := s.tmuxCommand("display-message", "-p", "-t", "fixture:0.0", "#{pane_mode} #{scroll_position} #{history_size}").CombinedOutput()
			if err != nil {
				t.Fatalf("read tmux scroll position: %v %s", err, output)
			}
			if !strings.HasPrefix(string(output), "copy-mode 5 ") {
				t.Fatalf("scroll-up left the viewport unchanged with mouse %s: %s", mouse, output)
			}
			s.handleMessage(io.Discard, nil, nil, nil, "fixture", "", []byte(`{"type":"tmux-scroll","direction":"down","lines":5}`), true, context.Background())
			output, err = s.tmuxCommand("display-message", "-p", "-t", "fixture:0.0", "#{pane_mode}:#{scroll_position}:#{history_size}").CombinedOutput()
			if err != nil {
				t.Fatalf("read tmux bottom position: %v %s", err, output)
			}
			if !strings.HasPrefix(string(output), "::") {
				t.Fatalf("scroll-down did not return to live output with mouse %s: %s", mouse, output)
			}
		})
	}
}

type rejectedTerminalInput struct{}

func (rejectedTerminalInput) Write(data []byte) (int, error) { return 0, io.ErrClosedPipe }

func TestTerminalActivityRejectsUnacceptedInput(t *testing.T) {
	runtime, err := os.MkdirTemp("/tmp", "ta-reject-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	socket := filepath.Join(runtime, "tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	s := &server{ignoreSizeChecked: true}
	if output, err := s.tmuxCommand("new-session", "-d", "-s", "fixture", "sleep", "30").CombinedOutput(); err != nil {
		t.Fatalf("create isolated tmux session: %v %s", err, output)
	}
	t.Cleanup(func() { _ = s.tmuxCommand("kill-server").Run() })
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	for _, row := range []struct {
		data     string
		writable bool
		writer   io.Writer
		ctx      context.Context
	}{
		{`{"type":"input","data":"key"}`, false, &bytes.Buffer{}, context.Background()},
		{`{"type":"input","data":"key"}`, true, rejectedTerminalInput{}, context.Background()},
		{`{"type":"input","data":"key"}`, true, &bytes.Buffer{}, cancelled},
		{`{"type":"input","data":"key","user_input":false}`, true, &bytes.Buffer{}, context.Background()},
		{`{"type":"input","data":"\u001b[<0;4;8M","user_input":true}`, true, &bytes.Buffer{}, context.Background()},
	} {
		s.handleMessage(row.writer, nil, nil, nil, "fixture", "", []byte(row.data), row.writable, row.ctx)
		if len(s.terminalActivity.clocks) != 0 {
			t.Fatalf("unaccepted input recorded: %q", row.data)
		}
	}
	accepted := &bytes.Buffer{}
	s.handleMessage(accepted, nil, nil, nil, "fixture", "", []byte(`{"type":"input","data":"key","user_input":true}`), true)
	if accepted.String() != "key" {
		t.Fatalf("accepted input was not forwarded: %q", accepted.String())
	}
	activity := terminalActivityPayload(t, s)
	if activity["fixture"]["last_user_input_ts"] == nil || activity["fixture"]["active_recency_ts"] == nil {
		t.Fatalf("accepted input did not update the existing activity response: %#v", activity)
	}
}

func terminalActivityPayload(t *testing.T, s *server) map[string]map[string]any {
	t.Helper()
	response := httptest.NewRecorder()
	s.activity(response, httptest.NewRequest(http.MethodGet, "/api/activity", nil))
	if response.Code != 200 {
		t.Fatalf("activity status: %d %s", response.Code, response.Body.String())
	}
	var payload struct {
		Activity map[string]map[string]any `json:"activity"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	return payload.Activity
}

func TestTerminalActivityWebSocketNoEchoAndWindowIdentity(t *testing.T) {
	runtime, err := os.MkdirTemp("/tmp", "ta-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	socket := filepath.Join(runtime, "tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	s := &server{ignoreSizeChecked: true, children: make(map[*exec.Cmd]struct{})}
	run := func(args ...string) string {
		t.Helper()
		output, err := s.tmuxCommand(args...).CombinedOutput()
		if err != nil {
			t.Fatalf("tmux %v: %v %s", args, err, output)
		}
		return strings.TrimSpace(string(output))
	}
	command := "stty -echo -icanon; tmux -S " + shellQuote(socket) + " wait-for -S quiet-ready; exec cat >/dev/null"
	run("new-session", "-d", "-s", "fixture", "-n", "quiet", command)
	t.Cleanup(func() { _ = s.tmuxCommand("kill-server").Run() })
	run("wait-for", "quiet-ready")
	run("new-window", "-d", "-t", "=fixture:", "-n", "other")
	client := terminalBridgeConnection(t, s)
	drainDone := make(chan struct{})
	pong := make(chan struct{}, 1)
	go func() {
		defer close(drainDone)
		reader := bufio.NewReader(client)
		for {
			opcode, _, err := readFrame(reader)
			if err != nil {
				return
			}
			if opcode == 10 {
				pong <- struct{}{}
			}
		}
	}()
	t.Cleanup(func() { _ = client.Close(); <-drainDone })
	if err := writeFrame(client, 1, []byte(`{"type":"resize","rows":36,"cols":120,"foreground":true}`)); err != nil {
		t.Fatal(err)
	}
	waitFixtureCondition(t, "attached activity client", 3*time.Second, func() bool {
		output, _ := s.tmuxCommand("list-clients", "-F", "#{client_tty}").Output()
		return strings.TrimSpace(string(output)) != ""
	})
	send := func(data string) float64 {
		t.Helper()
		before := float64(time.Now().UnixNano()) / 1e9
		payload, _ := json.Marshal(map[string]any{"type": "input", "data": data, "user_input": true})
		if err := writeFrame(client, 1, payload); err != nil {
			t.Fatal(err)
		}
		waitFixtureCondition(t, "recorded accepted input", 3*time.Second, func() bool {
			s.terminalActivity.mu.Lock()
			defer s.terminalActivity.mu.Unlock()
			for _, ts := range s.terminalActivity.clocks {
				if ts >= before {
					return true
				}
			}
			return false
		})
		activity := terminalActivityPayload(t, s)
		window := activity["fixture:0"]
		ts, _ := window["last_user_input_ts"].(float64)
		if ts < before || window["active_recency_ts"].(float64) < ts {
			t.Fatalf("window input recency missing: %#v", activity)
		}
		if activity["fixture"]["active_recency_ts"].(float64) < ts {
			t.Fatalf("session did not bubble newest input: %#v", activity)
		}
		if activity["fixture:1"]["last_user_input_ts"] != nil {
			t.Fatalf("wrong window received input activity: %#v", activity)
		}
		if _, ok := window["last_output_ts"].(float64); !ok {
			t.Fatalf("existing output field lost: %#v", window)
		}
		return ts
	}
	send("k")
	pasteTS := send("\x1b[200~quiet-paste\x1b[201~")
	for _, data := range []string{"\x1b[<0;4;8M", "\x1b[I", "\x1b[12;40R"} {
		payload, _ := json.Marshal(map[string]any{"type": "input", "data": data, "user_input": true})
		if err := writeFrame(client, 1, payload); err != nil {
			t.Fatal(err)
		}
	}
	// A pong fences all preceding input handling without relying on timing or echo.
	if err := writeFrame(client, 9, []byte("activity-fence")); err != nil {
		t.Fatal(err)
	}
	select {
	case <-pong:
	case <-time.After(3 * time.Second):
		t.Fatal("input fence did not complete")
	}
	if activity := terminalActivityPayload(t, s); activity["fixture:0"]["last_user_input_ts"] != pasteTS {
		t.Fatalf("protocol bytes changed input activity: %#v", activity)
	}
	if captured := run("capture-pane", "-p", "-t", "=fixture:0"); strings.Contains(captured, "quiet-paste") {
		t.Fatalf("fixture echoed input: %q", captured)
	}
	run("move-window", "-s", "=fixture:0", "-t", "=fixture:3")
	activity := terminalActivityPayload(t, s)
	if activity["fixture:3"]["last_user_input_ts"] != pasteTS {
		t.Fatalf("renumber lost window input: %#v", activity)
	}
	run("kill-window", "-t", "=fixture:3")
	run("new-window", "-d", "-t", "=fixture:3", "-n", "replacement")
	activity = terminalActivityPayload(t, s)
	if activity["fixture:3"]["last_user_input_ts"] != nil {
		t.Fatalf("replacement inherited old input: %#v", activity)
	}
	s.tmuxSessionActivityFn = func() (map[string]float64, error) { return nil, errors.New("fixture activity unavailable") }
	activity = terminalActivityPayload(t, s)
	if activity["fixture"]["last_user_input_ts"] != pasteTS {
		t.Fatalf("native clock failure hid accepted session input: %#v", activity)
	}
}

func TestTabberActivityTouchAppearsInExistingActivitySnapshot(t *testing.T) {
	runtime, err := os.MkdirTemp("/tmp", "ta-touch-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	socket := filepath.Join(runtime, "tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	s := &server{ignoreSizeChecked: true}
	if output, err := s.tmuxCommand("new-session", "-d", "-s", "fixture", "sleep", "30").CombinedOutput(); err != nil {
		t.Fatalf("create isolated tmux session: %v %s", err, output)
	}
	t.Cleanup(func() { _ = s.tmuxCommand("kill-server").Run() })
	for _, item := range []string{"file:/tmp/2026.md", "gitdiff:%2Ftmp%2Frepo"} {
		request := httptest.NewRequest(http.MethodPost, "/api/activity/touch", strings.NewReader(`{"item":"`+item+`"}`))
		response := httptest.NewRecorder()
		s.activityTouch(response, request)
		if response.Code != http.StatusOK {
			t.Fatalf("touch %q: %d %s", item, response.Code, response.Body.String())
		}
	}
	for _, item := range []string{"file:/tmp/2026.md", "gitdiff:%2Ftmp%2Frepo"} {
		row := terminalActivityPayload(t, s)[item]
		if row["active_recency_ts"] == nil || row["last_user_input_ts"] == nil {
			t.Fatalf("activity snapshot omitted touched utility item %q: %#v", item, row)
		}
	}
	for _, invalid := range []string{"", "terminal:one", "file:", "gitdiff:"} {
		payload, _ := json.Marshal(map[string]string{"item": invalid})
		response := httptest.NewRecorder()
		s.activityTouch(response, httptest.NewRequest(http.MethodPost, "/api/activity/touch", bytes.NewReader(payload)))
		if response.Code != http.StatusBadRequest {
			t.Fatalf("invalid item %q status = %d, want 400", invalid, response.Code)
		}
	}
	methodResponse := httptest.NewRecorder()
	s.activityTouch(methodResponse, httptest.NewRequest(http.MethodGet, "/api/activity/touch", nil))
	if methodResponse.Code != http.StatusMethodNotAllowed {
		t.Fatalf("touch method status = %d, want 405", methodResponse.Code)
	}
}
