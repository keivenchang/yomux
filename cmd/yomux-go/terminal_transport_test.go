// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/testsupport"
)

func terminalControlFixture(t *testing.T) (*server, string) {
	t.Helper()
	root := t.TempDir()
	if err := syscall.Mkfifo(filepath.Join(root, "release"), 0600); err != nil {
		t.Fatal(err)
	}
	script := `case "$1" in
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
  stty size > "$TERMINAL_CONTROL_FIXTURE/dimensions"
  : > "$TERMINAL_CONTROL_FIXTURE/attached"
  exec cat
  ;;
esac
`
	if err := os.WriteFile(filepath.Join(root, "tmux"), []byte(testsupport.ShellScript(t, script)), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", root+":"+os.Getenv("PATH"))
	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	t.Setenv("TERMINAL_CONTROL_FIXTURE", root)
	return &server{ignoreSizeChecked: true, ignoreSizeSupported: true, children: make(map[*exec.Cmd]struct{})}, root
}

func TestTerminalPTYDescriptorsDoNotLeakIntoUnrelatedChildren(t *testing.T) {
	pair, err := openPTY(36, 120)
	if err != nil {
		t.Fatal(err)
	}
	defer pair.close()
	for _, file := range []*os.File{pair.master, pair.slave} {
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, file.Fd(), syscall.F_GETFD, 0)
		if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
			t.Fatalf("PTY descriptor can escape into workers: %d %v", flags, errno)
		}
	}
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

func TestTerminalUnchangedOwnershipReconcilesWithoutSIGWINCH(t *testing.T) {
	s, _ := terminalControlFixture(t)
	pair, err := openPTY(36, 120)
	if err != nil {
		t.Fatal(err)
	}
	defer pair.close()
	controls := 0
	pair.requestControl = func(cols, rows, txn int, authority ...bool) {
		if cols != 120 || rows != 36 || txn != 0 {
			t.Fatalf("ownership dimensions: %d %d %d", cols, rows, txn)
		}
		controls++
	}
	signals := make(chan os.Signal, 4)
	signal.Notify(signals, syscall.SIGWINCH)
	defer signal.Stop(signals)
	process := &exec.Cmd{Process: mustFindTerminalTestProcess(t)}
	s.handleMessage(&bytes.Buffer{}, pair, process, nil, "fixture", "", []byte(`{"type":"resize","rows":36,"cols":120,"foreground":true,"activate":true}`), true)
	if controls != 1 {
		t.Fatalf("unchanged ownership was not reconciled: %d", controls)
	}
	select {
	case <-signals:
		t.Fatal("unchanged ownership sent SIGWINCH")
	case <-time.After(100 * time.Millisecond):
	}
	// Closing the descriptor makes a redundant ioctl observable as EBADF.
	if err := pair.master.Close(); err != nil {
		t.Fatal(err)
	}
	if err := pair.resize(36, 120); err != nil {
		t.Fatalf("unchanged dimensions performed ioctl: %v", err)
	}
	if err := pair.resize(40, 120); err == nil {
		t.Fatal("changed dimensions skipped the closed descriptor")
	}
}

func mustFindTerminalTestProcess(t *testing.T) *os.Process {
	t.Helper()
	process, err := os.FindProcess(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	return process
}

// The browser owns hyperlink interpretation. The native bridge must preserve
// escape targets and UTF-8 bytes even when PTY reads split them arbitrarily.
func TestTerminalHyperlinkByteFidelityAcrossEverySplit(t *testing.T) {
	target := "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array?q=a%20b&next=%2F#fragment"
	corpora := []string{
		"\x1b]8;;" + target + "\aArray label\x1b]8;;\a",
		"\x1b]8;id=first;" + target + "\x1b\\first\x1b]8;;\x1b\\",
		"\x1b]8;;" + target + "\asecond\x1b]8;;\a\x1b]8;;https://example.com/other\aother\x1b]8;;\a",
		"  https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/\r\n  Global_Objects/Array",
		"界 café 🦀 \x1b]8;;" + target + "\awide label\x1b]8;;\a",
		"\x1b[?1049h\x1b]8;;" + target + "\aalternate\x1b]8;;\a\x1b[2K\x1b[?1049l",
		strings.Repeat("https://example.com/path?q=%2F&x=y#fragment\r\n", 1800),
	}
	for _, jsonInput := range []bool{false, true} {
		t.Run(map[bool]string{false: "binary", true: "json"}[jsonInput], func(t *testing.T) {
			s, root := terminalControlFixture(t)
			scriptPath := filepath.Join(root, "tmux")
			script, err := os.ReadFile(scriptPath)
			if err != nil {
				t.Fatal(err)
			}
			script = []byte(strings.Replace(string(script), "exec cat", `stty raw -echo
  : > "$TERMINAL_CONTROL_FIXTURE/raw"
  exec cat`, 1))
			if err := os.WriteFile(scriptPath, script, 0700); err != nil {
				t.Fatal(err)
			}
			connection := terminalBridgeConnection(t, s)
			if err := writeFrame(connection, 1, []byte(`{"type":"resize","rows":60,"cols":75,"foreground":true}`)); err != nil {
				t.Fatal(err)
			}
			waitTerminalControlFile(t, filepath.Join(root, "raw"), 2*time.Second)
			reader := bufio.NewReader(connection)
			roundTrip := func(t *testing.T, chunk []byte) {
				t.Helper()
				payload, opcode := chunk, byte(2)
				if jsonInput {
					payload, err = json.Marshal(map[string]string{"type": "input", "data": string(chunk)})
					if err != nil {
						t.Fatal(err)
					}
					opcode = 1
				}
				// net.Pipe deliberately requires a reader while the server
				// delivers large output; don't block both directions on writes.
				written := make(chan error, 1)
				go func() { written <- writeFrame(connection, opcode, payload) }()
				if err := connection.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
					t.Fatal(err)
				}
				var received []byte
				for len(received) < len(chunk) {
					kind, frame, readErr := readFrame(reader)
					if readErr != nil {
						t.Fatal(readErr)
					}
					if kind == 2 {
						received = append(received, frame...)
					}
				}
				if err := <-written; err != nil {
					t.Fatal(err)
				}
				if !bytes.Equal(received, chunk) {
					t.Fatalf("PTY/WebSocket bytes changed: got %q want %q", received, chunk)
				}
			}
			for index, text := range corpora {
				data := []byte(text)
				// Every byte boundary for escape/Unicode cases; the large
				// corpus separately crosses the 32 KiB bridge buffer boundary.
				splits := []int{1, 32767, 32768, 32769, len(data) - 1}
				if len(data) < 1024 {
					splits = make([]int, len(data)-1)
					for i := range splits {
						splits[i] = i + 1
					}
				}
				for _, split := range splits {
					if jsonInput && (!utf8.Valid(data[:split]) || !utf8.Valid(data[split:])) {
						continue
					}
					t.Run(fmt.Sprintf("corpus%d/split%d", index, split), func(t *testing.T) {
						roundTrip(t, data[:split])
						roundTrip(t, data[split:])
					})
				}
			}
		})
	}
}
