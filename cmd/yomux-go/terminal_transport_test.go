// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
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
	"github.com/keivenchang/yomux/internal/tmuxcmd"
)

func terminalControlFixture(t *testing.T) (*server, string) {
	t.Helper()
	root := t.TempDir()
	if err := syscall.Mkfifo(filepath.Join(root, "release"), 0600); err != nil {
		t.Fatal(err)
	}
	script := `case "$1" in
list-clients)
  active=/dev/active
  if [ -f "$TERMINAL_CONTROL_FIXTURE/tty" ]; then read active < "$TERMINAL_CONTROL_FIXTURE/tty"; fi
  if [ -f "$TERMINAL_CONTROL_FIXTURE/registration-hold" ] && [ ! -f "$TERMINAL_CONTROL_FIXTURE/tty" ]; then : > "$TERMINAL_CONTROL_FIXTURE/registration-observed"; fi
  printf '%s\tfixture\t120\t36\t@1\t123\t1\n/dev/other\tfixture\t180\t40\t@1\t456\t1\n' "$active"
  ;;
refresh-client|if-shell)
  printf '%s\n' "$*" >> "$TERMINAL_CONTROL_FIXTURE/commands"
  if [ -f "$TERMINAL_CONTROL_FIXTURE/hold" ] && [ ! -f "$TERMINAL_CONTROL_FIXTURE/held" ]; then
    : > "$TERMINAL_CONTROL_FIXTURE/held"
    read release < "$TERMINAL_CONTROL_FIXTURE/release"
  fi
  : > "$TERMINAL_CONTROL_FIXTURE/completed"
  printf '%s\n' "$TERMINAL_CONTROL_CLAIM"
  ;;
attach-session)
  if [ -f "$TERMINAL_CONTROL_FIXTURE/attach-fail" ]; then exit 7; fi
  stty size > "$TERMINAL_CONTROL_FIXTURE/dimensions"
  if [ -f "$TERMINAL_CONTROL_FIXTURE/registration-hold" ]; then
    : > "$TERMINAL_CONTROL_FIXTURE/registration-held"
    read release < "$TERMINAL_CONTROL_FIXTURE/release"
  fi
  tty > "$TERMINAL_CONTROL_FIXTURE/tty"
  : > "$TERMINAL_CONTROL_FIXTURE/attached"
  exec cat
  ;;
esac
`
	if err := testsupport.WriteExecutable(filepath.Join(root, "tmux"), []byte(testsupport.ShellScript(t, script)), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", root+":"+os.Getenv("PATH"))
	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	t.Setenv("TERMINAL_CONTROL_FIXTURE", root)
	s := &server{ignoreSizeChecked: true, ignoreSizeSupported: true, children: make(map[*exec.Cmd]struct{})}
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		command := tmuxcmd.Command(ctx, tmuxcmd.Args(args...)[1:]...)
		token := ""
		for _, argument := range args {
			for offset := 0; offset+52 <= len(argument); offset++ {
				candidate := argument[offset : offset+52]
				if validTerminalResizeClaim(candidate) {
					token = candidate
					break
				}
			}
			if token != "" {
				break
			}
		}
		command.Env = append(command.Env, "TERMINAL_CONTROL_CLAIM="+token)
		return command
	}
	return s, root
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

func assertFixtureClientsRedrawn(t *testing.T, commands []byte) []string {
	t.Helper()
	lines := strings.Split(strings.TrimSpace(string(commands)), "\n")
	if len(lines) < 2 || lines[len(lines)-2] != "refresh-client -R -t /dev/active" || lines[len(lines)-1] != "refresh-client -R -t /dev/other" {
		t.Fatalf("full tmux redraw must reach both fixture clients: %s", commands)
	}
	return lines
}

func waitFixtureCondition(t *testing.T, description string, timeout time.Duration, ready func() bool, diagnostics ...func() string) {
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
			var details []string
			for _, diagnostic := range diagnostics {
				details = append(details, diagnostic())
			}
			t.Fatalf("fixture did not reach %s: %s", description, strings.Join(details, "; "))
		}
	}
}

func terminalBridgeConnection(t *testing.T, s *server, readonly ...bool) net.Conn {
	t.Helper()
	serverSide, clientSide := net.Pipe()
	done := make(chan struct{})
	go func() {
		defer close(done)
		s.bridge(&wsConn{Conn: serverSide}, "fixture", len(readonly) > 0 && readonly[0], "")
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

func TestTerminalReadonlyInitialDimensionsDoNotGrantAuthority(t *testing.T) {
	s, root := terminalControlFixture(t)
	connection := terminalBridgeConnection(t, s, true)
	if err := writeFrame(connection, 1, []byte(`{"type":"resize","rows":24,"cols":87,"foreground":true,"focus_claim":false}`)); err != nil {
		t.Fatal(err)
	}
	waitTerminalControlFile(t, filepath.Join(root, "attached"), 2*time.Second)
	dimensions, err := os.ReadFile(filepath.Join(root, "dimensions"))
	if err != nil || strings.TrimSpace(string(dimensions)) != "24 87" {
		t.Fatalf("readonly initial PTY dimensions: %q error=%v", dimensions, err)
	}
	if err := writeFrame(connection, 1, []byte(`{"type":"refresh","txn":702}`)); err != nil {
		t.Fatal(err)
	}
	reader := bufio.NewReader(connection)
	_ = connection.SetReadDeadline(time.Now().Add(2 * time.Second))
	for {
		_, payload, err := readFrame(reader)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(payload), `"txn":702`) {
			break
		}
	}
	commands, err := os.ReadFile(filepath.Join(root, "commands"))
	if err != nil || strings.Contains(string(commands), "@yomux_resize_authority") || strings.Contains(string(commands), "cursor-colour") {
		t.Fatalf("readonly initial sizing claimed authority: %s error=%v", commands, err)
	}
}

func TestTerminalFragmentedInputReachesPTYAsOneMessage(t *testing.T) {
	s, root := terminalControlFixture(t)
	client := terminalBridgeConnection(t, s)
	if err := writeFrame(client, 1, []byte(`{"type":"resize","rows":36,"cols":120}`)); err != nil {
		t.Fatal(err)
	}
	waitTerminalControlFile(t, filepath.Join(root, "attached"), time.Second)
	var wire bytes.Buffer
	if err := writeFrame(&wire, 1, []byte(`{"type":"input","data":"fragment`)); err != nil {
		t.Fatal(err)
	}
	wire.Bytes()[0] &^= 0x80
	if err := writeFrame(&wire, 0, []byte(`-complete\n","user_input":false}`)); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Write(wire.Bytes()); err != nil {
		t.Fatal(err)
	}
	if err := client.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	reader := bufio.NewReader(client)
	var output strings.Builder
	for !strings.Contains(output.String(), "fragment-complete") {
		_, payload, err := readFrame(reader)
		if err != nil {
			t.Fatalf("fragmented input was not reassembled: output=%q error=%v", output.String(), err)
		}
		output.Write(payload)
		if strings.Contains(output.String(), `{"type":"input"`) {
			t.Fatalf("partial JSON leaked into PTY: %q", output.String())
		}
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

func TestTerminalRefreshRedrawsTheTmuxClientScreen(t *testing.T) {
	s, root := terminalControlFixture(t)
	serverSide, clientSide := net.Pipe()
	defer clientSide.Close()
	request, stop := s.startTerminalControls(&wsConn{Conn: serverSide}, "fixture", "/dev/active", "", nil)
	defer stop()
	pty, err := openPTY(36, 120)
	if err != nil {
		t.Fatal(err)
	}
	defer pty.close()
	pty.requestControl = request
	var input bytes.Buffer
	s.handleMessage(&input, pty, &exec.Cmd{}, nil, "fixture", "", []byte(`{"type":"refresh","txn":1}`), true)
	_ = clientSide.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, payload, err := readFrame(bufio.NewReader(clientSide))
	if err != nil || string(payload) != `{"txn":1,"type":"refresh-ack"}` {
		t.Fatalf("refresh acknowledgement: payload=%s error=%v", payload, err)
	}
	waitTerminalControlFile(t, filepath.Join(root, "completed"), 2*time.Second)
	commands, err := os.ReadFile(filepath.Join(root, "commands"))
	if err != nil {
		t.Fatal(err)
	}
	assertFixtureClientsRedrawn(t, commands)
}

func TestTerminalRefreshDuringResizeStillRedrawsTheTmuxClientScreen(t *testing.T) {
	s, root := terminalControlFixture(t)
	if err := os.WriteFile(filepath.Join(root, "hold"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	serverSide, clientSide := net.Pipe()
	defer clientSide.Close()
	request, stop := s.startTerminalControls(&wsConn{Conn: serverSide}, "fixture", "/dev/active", "", nil)
	defer stop()
	request(120, 36, 0, terminalControlOptions{authority: true})
	waitTerminalControlFile(t, filepath.Join(root, "held"), 2*time.Second)
	request(0, 0, 17)
	_ = clientSide.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, payload, err := readFrame(bufio.NewReader(clientSide))
	if err != nil || string(payload) != `{"txn":17,"type":"refresh-ack"}` {
		t.Fatalf("refresh acknowledgement during resize: payload=%s error=%v", payload, err)
	}
	commands, err := os.ReadFile(filepath.Join(root, "commands"))
	if err != nil {
		t.Fatal(err)
	}
	assertFixtureClientsRedrawn(t, commands)
}

func TestTerminalRefreshAcknowledgesRedrawWhenPendingResizeFails(t *testing.T) {
	s, root := terminalControlFixture(t)
	serverSide, clientSide := net.Pipe()
	defer clientSide.Close()
	request, stop := s.startTerminalControls(&wsConn{Conn: serverSide}, "fixture", "/dev/active", "", nil, func(int, int) error {
		return errors.New("fixture resize failure")
	})
	defer stop()
	request(120, 36, 17, terminalControlOptions{authority: true})
	_ = clientSide.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, payload, err := readFrame(bufio.NewReader(clientSide))
	if err != nil || string(payload) != `{"txn":17,"type":"refresh-ack"}` {
		t.Fatalf("refresh acknowledgement after resize failure: payload=%s error=%v", payload, err)
	}
	commands, err := os.ReadFile(filepath.Join(root, "commands"))
	if err != nil {
		t.Fatal(err)
	}
	assertFixtureClientsRedrawn(t, commands)
}

func TestTerminalAppearanceValidationAndWritePermissions(t *testing.T) {
	s := &server{}
	var input bytes.Buffer
	var controls []terminalControlOptions
	pair := &ptyPair{requestControl: func(cols, rows, txn int, options ...terminalControlOptions) { controls = append(controls, options[0]) }}
	payload := []byte(`{"type":"resize","cols":120,"rows":36,"foreground":true,"activate":true,"focus_claim":true,"appearance":{"status_fg":"#ffffff","status_bg":"#0000ff","cursor":"#66ffff"}}`)
	s.handleMessage(&input, pair, nil, nil, "fixture", "", payload, false)
	if len(controls) != 0 || input.Len() != 0 {
		t.Fatal("readonly viewer mutated terminal")
	}
	s.handleMessage(&input, pair, nil, nil, "fixture", "", payload, true)
	if len(controls) != 1 || !controls[0].appearance.valid() || controls[0].passive {
		t.Fatal("valid appearance claim rejected")
	}
	malformed := bytes.Replace(payload, []byte(`"cursor":"#66ffff"`), []byte(`"cursor":42`), 1)
	s.handleMessage(&input, pair, nil, nil, "fixture", "", malformed, true)
	if len(controls) != 1 || input.Len() != 0 {
		t.Fatal("invalid typed resize became terminal input")
	}
	invalid := bytes.Replace(payload, []byte("#66ffff"), []byte("#nothex"), 1)
	s.handleMessage(&input, pair, nil, nil, "fixture", "", invalid, true)
	if len(controls) != 2 || controls[1].appearance != nil {
		t.Fatal("invalid color reached tmux")
	}
}

func TestTerminalClaimPublicationKeepsAppearanceUntilAccepted(t *testing.T) {
	token, err := newTerminalResizeClaim()
	if err != nil {
		t.Fatal(err)
	}
	colors := &terminalAppearance{StatusForeground: "#ffffff", StatusBackground: "#0000ff", Cursor: "#66ffff"}
	claim := terminalAuthorityClaim{token: token, appearance: colors, window: "@1"}
	claim.acknowledge(terminalAuthorityResult{})
	if claim.appearance != colors || claim.continuing || claim.token != token {
		t.Fatal("failed publication discarded fresh claim")
	}
	active := terminalResizeClient{tty: "/dev/active", window: "@1", pid: "123", created: "1"}
	if !strings.Contains(terminalResizeClaimCondition(active, claim.token, claim.continuing), "#{>=:") {
		t.Fatal("future passive dimensions cannot retry unpublished focus")
	}
	claim.acknowledge(terminalAuthorityResult{window: "@1"})
	if claim.appearance != nil || !claim.continuing || claim.token != token {
		t.Fatal("accepted publication failed to retire one-time appearance")
	}
	if strings.Contains(terminalResizeClaimCondition(active, claim.token, claim.continuing), "#{>=:") {
		t.Fatal("published continuation can renew authority")
	}
}

func TestTerminalPendingFocusSurvivesPassiveResize(t *testing.T) {
	s, root := terminalControlFixture(t)
	if err := os.WriteFile(filepath.Join(root, "hold"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	serverSide, clientSide := net.Pipe()
	defer clientSide.Close()
	request, stop := s.startTerminalControls(&wsConn{Conn: serverSide}, "fixture", "/dev/active", "", nil)
	defer stop()
	request(120, 36, 0, terminalControlOptions{authority: true})
	waitTerminalControlFile(t, filepath.Join(root, "held"), time.Second)
	request(100, 30, 0, terminalControlOptions{authority: true, passive: true})
	waitTerminalControlFile(t, filepath.Join(root, "completed"), time.Second)
	commands, err := os.ReadFile(filepath.Join(root, "commands"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(commands)), "\n")
	if len(lines) != 2 || !strings.Contains(lines[1], "#{>=:") {
		t.Fatalf("passive dimensions discarded pending focus: %s", commands)
	}
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
			request, stop := s.startTerminalControls(&wsConn{Conn: serverSide}, "fixture", "/dev/active", "", nil)
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

func TestTerminalPassiveInitialAttachDoesNotClaimResizeAuthority(t *testing.T) {
	for _, first := range []string{"", `{"type":"resize","rows":42,"cols":132,"foreground":false}`} {
		t.Run(fmt.Sprintf("first=%q", first), func(t *testing.T) {
			s, root := terminalControlFixture(t)
			connection := terminalBridgeConnection(t, s)
			if first != "" {
				if err := writeFrame(connection, 1, []byte(first)); err != nil {
					t.Fatal(err)
				}
			}
			waitTerminalControlFile(t, filepath.Join(root, "attached"), 2*time.Second)
			if err := writeFrame(connection, 1, []byte(`{"type":"refresh","txn":701}`)); err != nil {
				t.Fatal(err)
			}
			_ = connection.SetReadDeadline(time.Now().Add(2 * time.Second))
			reader := bufio.NewReader(connection)
			for {
				_, payload, err := readFrame(reader)
				if err != nil {
					t.Fatal(err)
				}
				if strings.Contains(string(payload), `"txn":701`) {
					break
				}
			}
			commands, err := os.ReadFile(filepath.Join(root, "commands"))
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(commands), "ignore-size") {
				t.Fatalf("passive attach changed another client's resize authority: %s", commands)
			}
		})
	}
}

func TestTerminalLegacyInitialResizePreservesAuthority(t *testing.T) {
	s, root := terminalControlFixture(t)
	connection := terminalBridgeConnection(t, s)
	if err := writeFrame(connection, 1, []byte(`{"type":"resize","rows":42,"cols":132}`)); err != nil {
		t.Fatal(err)
	}
	waitFixtureCondition(t, "legacy resize authority published", 2*time.Second, func() bool {
		commands, err := os.ReadFile(filepath.Join(root, "commands"))
		return err == nil && strings.Contains(string(commands), "@yomux_resize_authority")
	})
	commands, err := os.ReadFile(filepath.Join(root, "commands"))
	if err != nil || !strings.Contains(string(commands), "@yomux_resize_authority") {
		t.Fatalf("legacy initial resize lost foreground authority: %s %v", commands, err)
	}
	dimensions, err := os.ReadFile(filepath.Join(root, "dimensions"))
	if err != nil || strings.TrimSpace(string(dimensions)) != "42 132" {
		t.Fatalf("legacy initial resize lost dimensions: %s %v", dimensions, err)
	}
}

func TestTerminalInitialAuthorityWaitsForOwnedClientRegistration(t *testing.T) {
	s, root := terminalControlFixture(t)
	if err := os.WriteFile(filepath.Join(root, "registration-hold"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	release, err := os.OpenFile(filepath.Join(root, "release"), os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer release.Close()
	connection := terminalBridgeConnection(t, s)
	if err := writeFrame(connection, 1, []byte(`{"type":"resize","rows":42,"cols":132,"foreground":true}`)); err != nil {
		t.Fatal(err)
	}
	waitTerminalControlFile(t, filepath.Join(root, "registration-held"), 2*time.Second)
	waitTerminalControlFile(t, filepath.Join(root, "registration-observed"), 2*time.Second)
	if commands, err := os.ReadFile(filepath.Join(root, "commands")); err == nil {
		t.Fatalf("authority changed before its client registered: %s", commands)
	}
	if err := writeFrame(connection, 1, []byte(`{"type":"input","data":"registration-key\n"}`)); err != nil {
		t.Fatal(err)
	}
	_ = connection.SetReadDeadline(time.Now().Add(2 * time.Second))
	reader := bufio.NewReader(connection)
	for {
		_, payload, err := readFrame(reader)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(payload), "registration-key") {
			break
		}
	}
	if _, err := release.WriteString("release\n"); err != nil {
		t.Fatal(err)
	}
	waitTerminalControlFile(t, filepath.Join(root, "completed"), 2*time.Second)
}

func TestTerminalFailedAttachRetiresBridgeBeforeClientRegistration(t *testing.T) {
	s, root := terminalControlFixture(t)
	if err := os.WriteFile(filepath.Join(root, "attach-fail"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	serverSide, clientSide := net.Pipe()
	defer clientSide.Close()
	done := make(chan struct{})
	go func() {
		defer close(done)
		s.bridge(&wsConn{Conn: serverSide}, "fixture", false, "")
	}()
	if err := writeFrame(clientSide, 1, []byte(`{"type":"resize","rows":42,"cols":132,"foreground":true}`)); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("failed attach retained bridge or registration worker")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.children) != 0 {
		t.Fatalf("failed attach retained %d child owners", len(s.children))
	}
}

func TestTerminalNewerClientClaimRetiresOlderExternalMutation(t *testing.T) {
	for _, scope := range []string{"same-session", "linked-session", "departed-client", "adjacent-tickets", "older-smaller"} {
		t.Run(scope, func(t *testing.T) {
			root, err := testsupport.MkdirShortTemp("ta-")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = os.RemoveAll(root) })
			socket := filepath.Join(root, "s")
			t.Setenv("YOMUX_TMUX_SOCKET", socket)
			t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
			s := &server{ignoreSizeChecked: true, ignoreSizeSupported: true}
			run := func(args ...string) string {
				t.Helper()
				ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
				defer cancel()
				out, err := tmuxcmd.Command(ctx, tmuxcmd.Args(args...)[1:]...).CombinedOutput()
				if err != nil {
					t.Fatalf("private tmux %v: %v %s", args, err, out)
				}
				return strings.TrimSpace(string(out))
			}
			run("-f", os.DevNull, "new-session", "-d", "-s", "source", "-x", "120", "-y", "36", "/bin/cat")
			t.Cleanup(func() { _ = tmuxcmd.Command(context.Background(), "-S", socket, "kill-server").Run() })
			run("new-session", "-d", "-s", "other", "/bin/cat")
			run("new-session", "-d", "-s", "alias", "-t", "source")
			run("set-option", "-g", "status", "off")
			run("set-option", "-t", "=source:", "window-size", "largest")
			pairs := map[string]*ptyPair{}
			attach := func(session string, cols, rows int, ignored bool) string {
				t.Helper()
				pair, err := openPTY(rows, cols)
				if err != nil {
					t.Fatal(err)
				}
				ctx, cancel := context.WithCancel(context.Background())
				args := []string{"attach-session", "-t", "=" + session + ":"}
				if ignored {
					args = append(args, "-f", "ignore-size")
				}
				client := tmuxcmd.Command(ctx, tmuxcmd.Args(args...)[1:]...)
				client.SysProcAttr = terminalProcessAttributes()
				client.Env = append(client.Env, "TERM=xterm-256color")
				client.Stdin, client.Stdout, client.Stderr = pair.slave, pair.slave, pair.slave
				if err := client.Start(); err != nil {
					cancel()
					pair.close()
					t.Fatal(err)
				}
				t.Cleanup(func() { cancel(); _ = client.Wait(); pair.close() })
				go func() { _, _ = io.Copy(io.Discard, pair.master) }()
				waitFixtureCondition(t, "private authority client attached", 2*time.Second, func() bool {
					return strings.Contains(run("list-clients", "-F", "#{client_tty}"), pair.tty)
				})
				pairs[pair.tty] = pair
				return pair.tty
			}
			olderCols, olderRows := 120, 36
			if scope == "older-smaller" {
				olderCols, olderRows = 40, 8
			}
			olderTTY := attach("source", olderCols, olderRows, true)
			if scope == "older-smaller" {
				run("refresh-client", "-t", olderTTY, "-f", "!ignore-size")
			}
			newerSession := "source"
			if scope == "linked-session" {
				newerSession = "alias"
			}
			newerTTY := attach(newerSession, 59, 10, true)
			unrelatedTTY := attach("other", 200, 70, false)
			// Keep an accepted client before IDENTIFY_TTYNAME through every claim.
			// This is the server state concurrent short-lived commands can expose.
			unidentified, err := net.Dial("unix", socket)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = unidentified.Close() })
			waitFixtureCondition(t, "unidentified tmux client admitted", 2*time.Second, func() bool {
				return strings.Contains(run("display-message", "-p", "#{L:|#{client_pid}|}"), "|0|")
			})
			fifo := filepath.Join(root, "release")
			if err := syscall.Mkfifo(fifo, 0600); err != nil {
				t.Fatal(err)
			}
			release, err := os.OpenFile(fifo, os.O_RDWR, 0600)
			if err != nil {
				t.Fatal(err)
			}
			defer release.Close()
			held := filepath.Join(root, "held")
			shell := testsupport.ToolPath(t, "sh")
			s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
				base := tmuxcmd.Command(ctx, tmuxcmd.Args(args...)[1:]...)
				if args[0] != "if-shell" && !(args[0] == "refresh-client" && len(args) > 4 && args[2] == olderTTY && args[4] == "!ignore-size") {
					return base
				}
				// The delayed process has not submitted its stale write to tmux yet.
				argv := []string{"-c", "printf held > " + shellQuote(held) + "; read release < " + shellQuote(fifo) + "; exec \"$@\"", "held-claim", base.Path}
				argv = append(argv, base.Args[1:]...)
				command := exec.CommandContext(ctx, shell, argv...)
				command.Env = base.Env
				return command
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			olderDone := make(chan error, 1)
			olderToken, err := newTerminalResizeClaim()
			if err != nil {
				t.Fatal(err)
			}
			newerToken, err := newTerminalResizeClaim()
			if err != nil {
				t.Fatal(err)
			}
			yellow := &terminalAppearance{StatusForeground: "#000000", StatusBackground: "#ffff00", Cursor: "#ffff00"}
			blue := &terminalAppearance{StatusForeground: "#ffffff", StatusBackground: "#0000ff", Cursor: "#0000ff"}
			olderClaims := []terminalAuthorityClaim{{token: olderToken, appearance: yellow}}
			newerClaims := []terminalAuthorityClaim{{token: newerToken, appearance: blue}}
			if scope == "adjacent-tickets" {
				// Adjacent values beyond float64 precision must remain distinct in tmux.
				olderClaims = []terminalAuthorityClaim{{token: fmt.Sprintf("%020d-%010d-%020d", int64(9007199254740992), 1, 1), appearance: yellow}}
				newerClaims = []terminalAuthorityClaim{{token: fmt.Sprintf("%020d-%010d-%020d", int64(9007199254740993), 1, 1), appearance: blue}}
			}
			go func() { olderDone <- s.claimResizeAuthority(ctx, "source", olderTTY, 120, 36, olderClaims...) }()
			waitTerminalControlFile(t, held, 2*time.Second)
			if scope == "departed-client" {
				run("switch-client", "-c", olderTTY, "-t", "other")
			}
			// A separate server instance shares only tmux, with no Go registry or lock.
			newer := &server{ignoreSizeChecked: true, ignoreSizeSupported: true}
			if err := newer.claimResizeAuthority(ctx, newerSession, newerTTY, 59, 10, newerClaims...); err != nil {
				t.Fatal(err)
			}
			for _, session := range []string{"source", "alias"} {
				got := run("display-message", "-p", "-t", "="+session+":", "#{E:status-style}")
				if !strings.HasSuffix(got, "fg=#ffffff,bg=#0000ff") {
					t.Fatalf("%s status style: %q", session, got)
				}
			}
			if got := run("show-options", "-wv", "-t", "=source:", "cursor-colour"); got != "#0000ff" {
				t.Fatalf("cursor: %s", got)
			}
			if scope == "same-session" {
				clients := terminalResizeClients([]byte(run("list-clients", "-F", terminalResizeClientFormat)))
				var active terminalResizeClient
				for _, client := range clients {
					if client.tty == newerTTY {
						active = client
					}
				}
				if active.tty == "" {
					t.Fatal("newer private client missing")
				}
				for _, attempt := range []struct {
					token      string
					continuing bool
				}{
					{olderClaims[0].token, false},
					{strings.Repeat("9", 20) + "-" + strings.Repeat("9", 10) + "-" + strings.Repeat("9", 20), true},
				} {
					output, err := tmuxcmd.Command(ctx, tmuxcmd.Args(terminalResizePrepareArgs(active, attempt.token, attempt.continuing)...)[1:]...).CombinedOutput()
					if err != nil || len(output) != 0 {
						t.Fatalf("stale or forged preparation disabled winning client: %s %v", output, err)
					}
				}
			}
			before := run("list-clients", "-F", "#{client_tty}\t#{client_flags}")
			if _, err := release.WriteString("release\n"); err != nil {
				t.Fatal(err)
			}
			if err := <-olderDone; err != nil {
				t.Fatal(err)
			}
			after := run("list-clients", "-F", "#{client_tty}\t#{client_flags}")
			flags := func(tty string) string {
				for _, line := range strings.Split(after, "\n") {
					fields := strings.Split(line, "\t")
					if len(fields) == 2 && fields[0] == tty {
						return fields[1]
					}
				}
				t.Fatalf("missing retained client %s: %s", tty, after)
				return ""
			}
			if !strings.Contains(flags(olderTTY), "ignore-size") || strings.Contains(flags(newerTTY), "ignore-size") || strings.Contains(flags(unrelatedTTY), "ignore-size") {
				t.Fatalf("stale/shared-window claim changed foreground or unrelated ownership: before=%s after=%s", before, after)
			}
			if got := run("show-options", "-wv", "-t", "=source:", "cursor-colour"); got != "#0000ff" {
				t.Fatalf("stale claim overwrote cursor: %s", got)
			}
			if scope == "older-smaller" {
				if err := pairs[olderTTY].resize(60, 150); err != nil {
					t.Fatal(err)
				}
				waitFixtureCondition(t, "older passive dimensions received", time.Second, func() bool {
					return strings.Contains(run("list-clients", "-F", "#{client_tty}:#{client_width}"), olderTTY+":150")
				})
				if got := run("display-message", "-p", "-t", "=source:", "#{window_width} #{window_height}"); got != "59 10" {
					t.Fatalf("older passive resize stole dimensions: %s", got)
				}
			}
			// Native changes survive repeated dimensions from the same accepted claim.
			run("set-option", "-w", "-t", "=source:", "cursor-colour", "#123456")
			run("set-option", "-t", "=source:", "status-style", "fg=#654321,bg=#123456")
			if err := newer.claimResizeAuthority(ctx, newerSession, newerTTY, 59, 10, newerClaims...); err != nil {
				t.Fatal(err)
			}
			if got := run("show-options", "-wv", "-t", "=source:", "cursor-colour"); got != "#123456" {
				t.Fatalf("duplicate claim overwrote native cursor: %s", got)
			}
			if got := run("display-message", "-p", "-t", "=source:", "#{E:status-style}"); got != "fg=#654321,bg=#123456" {
				t.Fatalf("duplicate claim overwrote native status: %s", got)
			}
			for _, option := range terminalStatusOptions[1:] {
				if got := run("display-message", "-p", "-t", "=source:", "#{E:"+option+"}"); strings.Contains(got, "bg=#0000ff") {
					t.Fatalf("native general style left %s enforcing the claim: %s", option, got)
				}
			}
			run("new-window", "-d", "-t", "=source:", "-n", "unclaimed", "/bin/cat")
			run("select-window", "-t", "=alias:1")
			if got := run("display-message", "-p", "-t", "=alias:", "#{E:status-style}"); got != "bg=green,fg=black" {
				t.Fatalf("color spread to unclaimed window: %q", got)
			}
			run("select-window", "-t", "=alias:0")
			waitFixtureCondition(t, "new foreground window geometry", 2*time.Second, func() bool {
				return run("display-message", "-p", "-t", "=source:", "#{window_width} #{window_height}") == "59 10"
			})
			if scope == "same-session" {
				reconnected := attach("source", 90, 20, true)
				receipt := terminalAuthorityClaim{token: newerClaims[0].token, window: run("display-message", "-p", "-t", "=source:", "#{window_id}"), continuing: true}
				if err := newer.claimResizeAuthority(ctx, "source", reconnected, 90, 20, receipt); err != nil {
					t.Fatal(err)
				}
				waitFixtureCondition(t, "reconnected owner size", time.Second, func() bool {
					return run("display-message", "-p", "-t", "=source:", "#{window_width} #{window_height}") == "90 20"
				})
				if got := run("show-options", "-wv", "-t", "=source:", "cursor-colour"); got != "#123456" {
					t.Fatalf("resume reapplied colors: %s", got)
				}
				forged := receipt
				forged.token = strings.Repeat("9", 20) + "-" + strings.Repeat("9", 10) + "-" + strings.Repeat("9", 20)
				if err := newer.claimResizeAuthority(ctx, "source", olderTTY, 150, 60, forged); err != nil {
					t.Fatal(err)
				}
				if got := run("show-options", "-wv", "-t", "=source:", "@yomux_resize_authority"); got != receipt.token {
					t.Fatalf("forged resume acquired authority: %s", got)
				}
				// Independent windows share inherited owners. A native write must
				// survive another window's next claim, including section-only writes.
				run("select-window", "-t", "=alias:1")
				otherTTY := attach("alias", 70, 15, true)
				claim := func(session, tty string, colors *terminalAppearance) {
					t.Helper()
					token, err := newTerminalResizeClaim()
					if err != nil {
						t.Fatal(err)
					}
					if err := newer.claimResizeAuthority(context.Background(), session, tty, 70, 15, terminalAuthorityClaim{token: token, appearance: colors}); err != nil {
						t.Fatal(err)
					}
				}
				for _, option := range terminalStatusOptions {
					run("set-option", "-u", "-t", "=source:", "status-style")
					run("set-option", "-g", "status-style", "fg=white,bg=green")
					globalArgs := []string{"set-option", "-g"}
					localArgs := []string{"show-options", "-qv", "-t", "=alias:1"}
					if strings.HasPrefix(option, "window-") {
						globalArgs = append(globalArgs, "-w")
						localArgs = append(localArgs, "-w")
					}
					claim("source", newerTTY, yellow)
					claim("alias", otherTTY, blue)
					if got := run(append(localArgs, option)...); got != "" {
						t.Fatalf("%s inheritance was shadowed locally: %s", option, got)
					}
					run(append(globalArgs, option, "fg=white,bg=red")...)
					claim("source", newerTTY, yellow)
					if got := run("display-message", "-p", "-t", "=alias:1", "#{E:"+option+"}"); got != "fg=white,bg=red" {
						t.Fatalf("another window's focus resurrected %s: %s", option, got)
					}
					run(append(globalArgs, option, "fg=white,bg=green")...)
					if got := run("display-message", "-p", "-t", "=alias:1", "#{E:"+option+"}"); got != "fg=white,bg=green" {
						t.Fatalf("returning to a prior native value resurrected %s: %s", option, got)
					}
				}
				// Both first claims may inventory the same unwrapped global owner.
				// Their separate physical windows must both retain their own colors.
				for _, option := range terminalStatusOptions {
					args := []string{"set-option", "-g"}
					if strings.HasPrefix(option, "window-") {
						args = append(args, "-w")
					}
					run(append(args, option, "fg=white,bg=green")...)
				}
				clients := terminalResizeClients([]byte(run("list-clients", "-F", terminalResizeClientFormat)))
				var commands [][]string
				for _, request := range []struct {
					tty    string
					colors terminalAppearance
				}{{newerTTY, *yellow}, {otherTTY, *blue}} {
					for _, active := range clients {
						if active.tty != request.tty {
							continue
						}
						styles, err := newer.terminalAppearanceStyles(context.Background(), active.window)
						if err != nil {
							t.Fatal(err)
						}
						request.colors.styles = styles
						token, err := newTerminalResizeClaim()
						if err != nil {
							t.Fatal(err)
						}
						commands = append(commands, terminalResizeClaimArgs(clients, active, 70, 15, token, false, &request.colors))
					}
				}
				if len(commands) != 2 {
					t.Fatal("concurrent ownership fixture lost its clients")
				}
				for _, command := range commands {
					run(command...)
				}
				for target, color := range map[string]string{"=source:0": "bg=#ffff00", "=alias:1": "bg=#0000ff"} {
					for _, option := range terminalStatusOptions {
						if got := run("display-message", "-p", "-t", target, "#{E:"+option+"}"); !strings.HasSuffix(got, color) {
							t.Fatalf("concurrent owner lost %s %s color: %s", target, option, got)
						}
					}
				}
				run("select-window", "-t", "=alias:0")
				run("set-option", "-t", "=source:", "status-style", "bold,fg=white,bg=green")
				run("set-option", "-t", "=source:", "status-left-style", "underscore,fg=white,bg=green")
				run("set-option", "-w", "-t", "=source:0", "window-status-current-style", "italics,fg=white,bg=green")
				claim("source", newerTTY, yellow)
				for _, target := range []string{"=source:0", "=alias:0"} {
					for _, option := range terminalStatusOptions {
						if got := run("display-message", "-p", "-t", target, "#{E:"+option+"}"); !strings.HasSuffix(got, "bg=#ffff00") {
							t.Fatalf("linked view lost local/global %s owner: %s %s", option, target, got)
						}
					}
				}
				run("set-option", "-t", "=source:", "status-style", "fg=white,bg=red")
				for _, option := range terminalStatusOptions[1:] {
					if got := run("display-message", "-p", "-t", "=source:0", "#{E:"+option+"}"); strings.Contains(got, "bg=#ffff00") {
						t.Fatalf("local general release left %s enforcing yellow: %s", option, got)
					}
				}
				run("set-option", "-g", "status-style", "fg=white,bg=blue")
				run("select-window", "-t", "=alias:1")
				claim("alias", otherTTY, blue)
				run("select-window", "-t", "=alias:0")
				for target, expected := range map[string]string{"=source:0": "fg=white,bg=red", "=alias:0": "fg=white,bg=blue"} {
					if got := run("display-message", "-p", "-t", target, "#{E:status-style}"); got != expected {
						t.Fatalf("another window changed %s native local/global style: %s", target, got)
					}
				}
			}
		})
	}
}

func TestTerminalResizeClaimAndClientInputsAreBounded(t *testing.T) {
	first, err := newTerminalResizeClaim()
	if err != nil {
		t.Fatal(err)
	}
	second, err := newTerminalResizeClaim()
	if err != nil || !validTerminalResizeClaim(first) || !validTerminalResizeClaim(second) || second <= first {
		t.Fatalf("shared clock tokens are invalid or unordered: %q %q %v", first, second, err)
	}
	for _, color := range []string{"", "red", "#fff", "#123456;kill-server", "#{pid}", "#12345g"} {
		if (&terminalAppearance{StatusForeground: color, StatusBackground: "#000000", Cursor: "#ffffff"}).valid() {
			t.Fatalf("invalid appearance admitted: %q", color)
		}
	}
	for _, input := range []string{"", "#{window_id}", strings.Repeat("9", 52), first + "; kill-server"} {
		if validTerminalResizeClaim(input) {
			t.Fatalf("invalid token admitted: %q", input)
		}
	}
	valid := "/dev/pts/1\tfixture\t120\t36\t@1\t123\t456"
	if clients := terminalResizeClients([]byte(valid)); len(clients) != 1 {
		t.Fatalf("valid client rejected: %v", clients)
	}
	for _, input := range []string{
		strings.Replace(valid, "/dev/pts/1", "/dev/#{window_id}", 1),
		strings.Replace(valid, "@1", "@1};kill-server", 1),
		strings.Replace(valid, "123", "#{pid}", 1),
		strings.Replace(valid, "123", "0", 1),
		strings.Replace(valid, "123", "000", 1),
		strings.Replace(valid, "456", "-1", 1),
	} {
		if clients := terminalResizeClients([]byte(input)); len(clients) != 0 {
			t.Fatalf("format input admitted: %q", input)
		}
	}
}

func TestTerminalControlsCancelSupersededSizeAndAcknowledgeLatestRefresh(t *testing.T) {
	s, root := terminalControlFixture(t)
	if err := os.WriteFile(filepath.Join(root, "hold"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	serverSide, clientSide := net.Pipe()
	defer clientSide.Close()
	request, stop := s.startTerminalControls(&wsConn{Conn: serverSide}, "fixture", "/dev/active", "", nil)
	defer stop()
	request(120, 36, 0)
	waitTerminalControlFile(t, filepath.Join(root, "held"), 2*time.Second)
	// The latest viewport retains sole resize ownership even when another client is smaller.
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
	lines := assertFixtureClientsRedrawn(t, commands)
	if len(lines) != 3 || !strings.Contains(lines[0], "/dev/other") {
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
	pair.requestControl = func(cols, rows, txn int, options ...terminalControlOptions) {
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
			// This matrix owns byte transport; activity attribution has its own tests.
			// Avoid thousands of shell metadata probes for the same fixture session.
			s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
				if args[0] == "list-clients" || args[0] == "display-message" {
					return &exec.Cmd{Err: errors.New("activity metadata is outside the byte-transport fixture")}
				}
				return tmuxcmd.Command(ctx, tmuxcmd.Args(args...)[1:]...)
			}
			scriptPath := filepath.Join(root, "tmux")
			script, err := os.ReadFile(scriptPath)
			if err != nil {
				t.Fatal(err)
			}
			script = []byte(strings.Replace(string(script), "exec cat", `stty raw -echo
  : > "$TERMINAL_CONTROL_FIXTURE/raw"
  exec cat`, 1))
			if err := testsupport.WriteExecutable(scriptPath, script, 0700); err != nil {
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
