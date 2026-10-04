// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"bytes"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func terminalControlFixture(t *testing.T) (*server, string) {
	t.Helper()
	root := t.TempDir()
	if err := syscall.Mkfifo(filepath.Join(root, "release"), 0600); err != nil {
		t.Fatal(err)
	}
	script := `#!/bin/sh
case "$1" in
list-clients) printf '/dev/other\tfixture\t180\t40\n';;
refresh-client)
  printf '%s\n' "$*" >> "$TERMINAL_CONTROL_FIXTURE/commands"
  if [ -f "$TERMINAL_CONTROL_FIXTURE/hold" ] && [ ! -f "$TERMINAL_CONTROL_FIXTURE/held" ]; then
    : > "$TERMINAL_CONTROL_FIXTURE/held"
    read release < "$TERMINAL_CONTROL_FIXTURE/release"
  fi
  : > "$TERMINAL_CONTROL_FIXTURE/completed"
  ;;
attach-session)
  /bin/stty size > "$TERMINAL_CONTROL_FIXTURE/dimensions"
  : > "$TERMINAL_CONTROL_FIXTURE/attached"
  exec /bin/cat
  ;;
esac
`
	if err := os.WriteFile(filepath.Join(root, "tmux"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", root+":"+os.Getenv("PATH"))
	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	t.Setenv("TERMINAL_CONTROL_FIXTURE", root)
	return &server{ignoreSizeChecked: true, ignoreSizeSupported: true, children: make(map[*exec.Cmd]struct{})}, root
}

func waitTerminalControlFile(t *testing.T, path string, timeout time.Duration) {
	t.Helper()
	waitFixtureCondition(t, filepath.Base(path), timeout, func() bool {
		_, err := os.Stat(path)
		return err == nil
	})
}

func waitFixtureCondition(t *testing.T, description string, timeout time.Duration, ready func() bool) {
	t.Helper()
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	ticker := time.NewTicker(time.Millisecond)
	defer ticker.Stop()
	for {
		if ready() {
			return
		}
		select {
		case <-ticker.C:
		case <-timer.C:
			t.Fatalf("fixture did not reach %s", description)
		}
	}
}

func terminalBridgeConnection(t *testing.T, s *server) net.Conn {
	t.Helper()
	serverSide, clientSide := net.Pipe()
	done := make(chan struct{})
	go func() {
		defer close(done)
		s.bridge(&wsConn{Conn: serverSide}, "fixture", false, "")
	}()
	t.Cleanup(func() {
		_ = clientSide.Close()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("terminal bridge did not close")
		}
	})
	return clientSide
}

func TestTerminalInitialResizeStartsAttachWithoutWaitingForInput(t *testing.T) {
	s, root := terminalControlFixture(t)
	clientSide := terminalBridgeConnection(t, s)
	if err := writeFrame(clientSide, 1, []byte(`{"type":"resize","rows":42,"cols":132,"foreground":true}`)); err != nil {
		t.Fatal(err)
	}
	// The old pre-attach loop waits 750 ms even after receiving valid dimensions.
	waitTerminalControlFile(t, filepath.Join(root, "attached"), 500*time.Millisecond)
	dimensions, err := os.ReadFile(filepath.Join(root, "dimensions"))
	if err != nil || strings.TrimSpace(string(dimensions)) != "42 132" {
		t.Fatalf("initial PTY dimensions: %q error=%v", dimensions, err)
	}
	reader := bufio.NewReader(clientSide)
	if err := writeFrame(clientSide, 1, []byte(`{"type":"input","data":"visible-key\n"}`)); err != nil {
		t.Fatal(err)
	}
	_ = clientSide.SetReadDeadline(time.Now().Add(2 * time.Second))
	var output strings.Builder
	for !strings.Contains(output.String(), "visible-key") {
		_, payload, err := readFrame(reader)
		if err != nil {
			t.Fatal(err)
		}
		output.Write(payload)
	}
}

func TestTerminalRefreshAcknowledgesAfterHeldCommandWhileKeyboardEchoes(t *testing.T) {
	s, root := terminalControlFixture(t)
	clientSide := terminalBridgeConnection(t, s)
	reader := bufio.NewReader(clientSide)
	send := func(message string) {
		t.Helper()
		if err := writeFrame(clientSide, 1, []byte(message)); err != nil {
			t.Fatal(err)
		}
	}
	readUntil := func(want string) {
		t.Helper()
		_ = clientSide.SetReadDeadline(time.Now().Add(2 * time.Second))
		var output strings.Builder
		for !strings.Contains(output.String(), want) {
			_, payload, err := readFrame(reader)
			if err != nil {
				t.Fatalf("terminal did not deliver %q: %v", want, err)
			}
			output.Write(payload)
		}
	}
	send(`{"type":"resize","rows":36,"cols":120,"foreground":true}`)
	send(`{"type":"refresh","txn":1}`)
	readUntil(`"txn":1`)
	if err := os.WriteFile(filepath.Join(root, "hold"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	send(`{"type":"refresh","txn":7}`)
	waitTerminalControlFile(t, filepath.Join(root, "held"), 2*time.Second)
	send(`{"type":"input","data":"echo-before-refresh\n"}`)
	readUntil("echo-before-refresh")
	// Opening this FIFO releases the exact refresh after the echoed input arrived.
	release, err := os.OpenFile(filepath.Join(root, "release"), os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := release.WriteString("release\n"); err != nil {
		_ = release.Close()
		t.Fatal(err)
	}
	_ = release.Close()
	readUntil(`"txn":7`)
}

func TestTerminalHeldControlsLeaveKeyboardDeliveryAvailable(t *testing.T) {
	for _, control := range []string{
		`{"type":"resize","rows":36,"cols":120,"foreground":true,"activate":true}`,
		`{"type":"refresh","txn":7}`,
	} {
		t.Run(control, func(t *testing.T) {
			s, root := terminalControlFixture(t)
			if err := os.WriteFile(filepath.Join(root, "hold"), nil, 0600); err != nil {
				t.Fatal(err)
			}
			serverSide, clientSide := net.Pipe()
			defer clientSide.Close()
			request, stop := s.startTerminalControls(&wsConn{Conn: serverSide}, "fixture", "/dev/active")
			defer stop()
			pty, err := openPTY(36, 120)
			if err != nil {
				t.Fatal(err)
			}
			defer pty.close()
			pty.requestControl = request
			var input bytes.Buffer
			s.handleMessage(&input, pty, &exec.Cmd{}, nil, "fixture", "", []byte(control), true)
			waitTerminalControlFile(t, filepath.Join(root, "held"), 2*time.Second)
			s.handleMessage(&input, pty, &exec.Cmd{}, nil, "fixture", "", []byte(`{"type":"input","data":"abc"}`), true)
			if input.String() != "abc" {
				t.Fatalf("keyboard delivery while tmux is held: %q", input.String())
			}
			if _, err := os.Stat(filepath.Join(root, "completed")); err == nil {
				t.Fatal("negative control did not hold the tmux command")
			}
			// Cancellation must reap the held command without releasing its FIFO.
			stopped := make(chan struct{})
			go func() {
				stop()
				close(stopped)
			}()
			select {
			case <-stopped:
			case <-time.After(2 * time.Second):
				t.Fatal("connection teardown did not reap held terminal controls")
			}
		})
	}
}

func TestTerminalClosedBeforeSizingDoesNotAttach(t *testing.T) {
	s, root := terminalControlFixture(t)
	serverSide, clientSide := net.Pipe()
	_ = clientSide.Close()
	s.bridge(&wsConn{Conn: serverSide}, "fixture", false, "")
	if _, err := os.Stat(filepath.Join(root, "attached")); err == nil {
		t.Fatal("closed connection started a tmux client")
	}
}

func TestTerminalControlsCancelSupersededSizeAndAcknowledgeLatestRefresh(t *testing.T) {
	s, root := terminalControlFixture(t)
	if err := os.WriteFile(filepath.Join(root, "hold"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	serverSide, clientSide := net.Pipe()
	defer clientSide.Close()
	request, stop := s.startTerminalControls(&wsConn{Conn: serverSide}, "fixture", "/dev/active")
	defer stop()
	request(120, 36, 0)
	waitTerminalControlFile(t, filepath.Join(root, "held"), 2*time.Second)
	// A larger latest viewport no longer needs to ignore the other 180x40 client.
	request(200, 60, 9)
	_ = clientSide.SetReadDeadline(time.Now().Add(2 * time.Second))
	opcode, payload, err := readFrame(bufio.NewReader(clientSide))
	if err != nil || opcode != 1 || string(payload) != `{"txn":9,"type":"refresh-ack"}` {
		t.Fatalf("latest refresh acknowledgement: opcode=%d payload=%s error=%v", opcode, payload, err)
	}
	commands, err := os.ReadFile(filepath.Join(root, "commands"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(commands), "/dev/other") != 1 || !strings.HasSuffix(string(commands), "refresh-client -t =fixture:\n") {
		t.Fatalf("superseded authority ran after the latest size: %s", commands)
	}
}
