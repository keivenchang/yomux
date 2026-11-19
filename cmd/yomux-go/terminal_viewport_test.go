// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"fmt"
	"github.com/keivenchang/yomux/internal/testsupport"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestTerminalViewportNativeReflowKeepsCopyMode(t *testing.T) {
	for _, content := range []string{"short", "wrapped", "unicode", "repeated"} {
		t.Run(content, func(t *testing.T) {
			runtime, err := testsupport.MkdirShortTemp("tv-")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = os.RemoveAll(runtime) })
			socket := filepath.Join(runtime, "tmux.sock")
			t.Setenv("YOMUX_TMUX_SOCKET", socket)
			t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
			s := &server{ignoreSizeChecked: true}
			run := func(args ...string) string {
				t.Helper()
				out, err := s.tmuxCommand(args...).CombinedOutput()
				if err != nil {
					t.Fatalf("tmux %v: %v %s", args, err, out)
				}
				return strings.TrimSpace(string(out))
			}
			line := "printf '%s\\n' \"$i\""
			switch content {
			case "wrapped":
				line = "printf '%s-%0150d\\n' \"$i\" 0"
			case "unicode":
				line = "printf '%s-%s\\n' \"$i\" " + shellQuote(strings.Repeat("界", 90))
			case "repeated":
				line = "printf 'same\\n\\n'"
			}
			command := fmt.Sprintf("for i in $(seq 1 150); do %s; done; tmux -S %s wait-for -S ready; exec cat", line, shellQuote(socket))
			run("new-session", "-d", "-s", "source", "-x", "100", "-y", "30", command)
			t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
			run("wait-for", "ready")
			run("new-session", "-d", "-s", "reference", "-x", "100", "-y", "30", strings.Replace(command, "ready;", "ready-reference;", 1))
			run("wait-for", "ready-reference")
			run("copy-mode", "-t", "reference")
			run("send-keys", "-X", "-t", "reference", "page-up")
			run("resize-window", "-t", "reference", "-x", "160", "-y", "30")
			run("resize-window", "-t", "reference", "-x", "100", "-y", "30")
			expected, err := s.terminalViewportInventory(context.Background(), "=reference:")
			if err != nil || len(expected) != 1 {
				t.Fatalf("native reference: %v %v", expected, err)
			}
			run("copy-mode", "-t", "source")
			run("send-keys", "-X", "-t", "source", "page-up")
			before, err := s.terminalViewportInventory(context.Background(), "=source:")
			if err != nil || len(before) != 1 || before[0].scroll == 0 {
				t.Fatalf("copy mode fixture: %v %v", before, err)
			}
			run("new-session", "-d", "-s", "alias", "-t", "source")
			resize := func(rows, cols int) error {
				return s.tmuxCommand("resize-window", "-t", before[0].window, "-x", strconv.Itoa(cols), "-y", strconv.Itoa(rows)).Run()
			}
			if err := s.resizeTerminalNative(context.Background(), "source", "", 160, 30, resize, true); err != nil {
				t.Fatal(err)
			}
			wide, _ := s.terminalViewportInventory(context.Background(), "=source:")
			if err := s.resizeTerminalNative(context.Background(), "alias", "", 100, 30, resize, true); err != nil {
				t.Fatal(err)
			}
			after, err := s.terminalViewportInventory(context.Background(), "=source:")
			if err != nil || len(after) != 1 || after[0].mode != expected[0].mode || after[0].scroll != expected[0].scroll || after[0].x != expected[0].x || after[0].y != expected[0].y || after[0].width != expected[0].width {
				t.Fatalf("%s 100→160→100 differs from native reflow: before=%+v wide=%+v after=%+v err=%v", content, before, wide, after, err)
			}

		})
	}
}

func TestTerminalViewportCancelledTransactionDoesNotResize(t *testing.T) {
	s, _ := terminalControlFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	called := false
	s.resizeTerminalNative(ctx, "fixture", "", 100, 30, func(int, int) error { called = true; return nil }, true)
	if called {
		t.Fatal("superseded resize mutated the PTY")
	}
}

func TestTerminalViewportWaitsForRealPTYReflow(t *testing.T) {
	runtime, err := testsupport.MkdirShortTemp("tv-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	socket := filepath.Join(runtime, "tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	s := &server{ignoreSizeChecked: true, ignoreSizeSupported: true}
	command := "seq 1 150; tmux -S " + shellQuote(socket) + " wait-for -S ready; exec bash"
	if out, err := s.tmuxCommand("new-session", "-d", "-s", "source", "-x", "100", "-y", "30", command).CombinedOutput(); err != nil {
		t.Fatalf("fixture: %v %s", err, out)
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	readyCtx, cancelReady := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelReady()
	if err := s.tmuxCommandContext(readyCtx, "wait-for", "ready").Run(); err != nil {
		t.Fatal(err)
	}
	if err := s.tmuxCommand("set-option", "-t", "source", "window-size", "largest").Run(); err != nil {
		t.Fatal(err)
	}
	pair, err := openPTY(30, 100)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pair.close)
	clientCtx, cancelClient := context.WithCancel(context.Background())
	client := s.tmuxCommandContext(clientCtx, "attach-session", "-t", "=source:")
	client.SysProcAttr = terminalProcessAttributes()
	client.Env = append(client.Env, "TERM=xterm-256color")
	client.Stdin, client.Stdout, client.Stderr = pair.slave, pair.slave, pair.slave
	if err := client.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cancelClient()
		_ = client.Process.Signal(syscall.SIGCONT)
		_ = client.Process.Signal(syscall.SIGTERM)
		_ = client.Wait()
	})
	go func() { _, _ = io.Copy(io.Discard, pair.master) }()
	waitFixtureCondition(t, "attached client", time.Second, func() bool {
		out, _ := s.tmuxCommand("list-clients", "-F", "#{client_tty}").Output()
		return strings.Contains(string(out), pair.tty)
	})
	if err := s.tmuxCommand("copy-mode", "-t", "source").Run(); err != nil {
		t.Fatal(err)
	}
	if err := s.tmuxCommand("send-keys", "-X", "-t", "source", "page-up").Run(); err != nil {
		t.Fatal(err)
	}
	before, err := s.terminalViewportInventory(context.Background(), "=source:")
	if err != nil || len(before) != 1 || before[0].scroll == 0 {
		t.Fatalf("copy fixture: %v %v", before, err)
	}
	if err := client.Process.Signal(syscall.SIGSTOP); err != nil {
		t.Fatal(err)
	}
	resized := make(chan struct{})
	completed := make(chan error, 1)
	resize := func(rows, cols int) error {
		if err := pair.resize(rows, cols); err != nil {
			return err
		}
		if err := client.Process.Signal(syscall.SIGWINCH); err != nil {
			return err
		}
		return nil
	}
	go func() {
		completed <- s.resizeTerminalNative(context.Background(), "source", pair.tty, 160, 30, func(rows, cols int) error {
			err := resize(rows, cols)
			close(resized)
			return err
		}, true)
	}()
	select {
	case <-resized:
	case <-time.After(time.Second):
		t.Fatal("resize callback did not run")
	}
	select {
	case err := <-completed:
		t.Fatalf("transaction finished before fixture client consumed resize: %v", err)
	default:
	}
	if err := client.Process.Signal(syscall.SIGCONT); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-completed:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("resize did not converge after resuming client")
	}
	if err := s.resizeTerminalNative(context.Background(), "source", pair.tty, 100, 30, resize, true); err != nil {
		t.Fatal(err)
	}
	after, err := s.terminalViewportInventory(context.Background(), "=source:")
	if err != nil || len(after) != 1 || after[0].mode != "copy-mode" || after[0].width != 100 {
		t.Fatalf("PTY geometry or copy-mode convergence failed: before=%+v after=%+v err=%v", before, after, err)
	}
}

func TestTerminalViewportGeometryReadbackUsesRequestedClient(t *testing.T) {
	runtime, err := testsupport.MkdirShortTemp("tv-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	socket := filepath.Join(runtime, "tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	s := &server{ignoreSizeChecked: true}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	attach := func(name string, cols int) *ptyPair {
		t.Helper()
		if out, err := s.tmuxCommand("new-session", "-d", "-s", name, "-x", strconv.Itoa(cols), "-y", "30", "exec bash").CombinedOutput(); err != nil {
			t.Fatalf("fixture session: %v %s", err, out)
		}
		pair, err := openPTY(30, cols)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(pair.close)
		clientCtx, cancelClient := context.WithCancel(context.Background())
		client := s.tmuxCommandContext(clientCtx, "attach-session", "-t", s.exactTarget(name))
		client.SysProcAttr = terminalProcessAttributes()
		client.Env = append(client.Env, "TERM=xterm-256color")
		client.Stdin, client.Stdout, client.Stderr = pair.slave, pair.slave, pair.slave
		if err := client.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			cancelClient()
			_ = client.Process.Signal(syscall.SIGTERM)
			_ = client.Wait()
		})
		go func() { _, _ = io.Copy(io.Discard, pair.master) }()
		waitFixtureCondition(t, "attached "+name, time.Second, func() bool {
			out, _ := s.tmuxCommand("list-clients", "-F", "#{client_tty}\t#{client_width}").Output()
			return strings.Contains(string(out), fmt.Sprintf("%s\t%d", pair.tty, cols))
		})
		return pair
	}
	selected := attach("selected", 100)
	panes, err := s.terminalViewportInventory(context.Background(), "=selected:")
	if err != nil || len(panes) != 1 {
		t.Fatalf("selected fixture inventory: %v %v", panes, err)
	}
	// A different session is the most recently attached client. The -c display
	// destination alone does not bind tmux's independently resolved format target.
	_ = attach("unrelated", 160)
	if err := s.awaitTerminalClientSize(context.Background(), selected.tty, panes[0].window, 100, 30); err != nil {
		t.Fatalf("requested client's converged geometry was replaced by unrelated client: %v", err)
	}
}

func TestTerminalViewportNeverRepositionsCopyCursor(t *testing.T) {
	s := &server{ignoreSizeChecked: true}
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		if args[0] != "list-panes" {
			t.Fatalf("resize mutated tmux copy mode: %v", args)
		}
		return exec.CommandContext(ctx, "sh", "-c", "printf '%s' \"$1\"", "fixture", "%9\t@7\tcopy-mode\t100\t30\t12\t6\t0\t123:456\n")
	}
	if err := s.resizeTerminalNative(context.Background(), "fixture", "", 160, 30, func(int, int) error { return nil }, true); err != nil {
		t.Fatal(err)
	}
}

func TestTerminalResizeDoesNotOverwriteAnotherServersNavigation(t *testing.T) {
	runtime, err := testsupport.MkdirShortTemp("tv-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	socket := filepath.Join(runtime, "tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	a, b := &server{ignoreSizeChecked: true}, &server{ignoreSizeChecked: true}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	if out, err := a.tmuxCommand("new-session", "-d", "-s", "source", "-x", "100", "-y", "30", fmt.Sprintf("printf '%%080d\\n' 0; tmux -S %s wait-for -S ready; exec cat", shellQuote(socket))).CombinedOutput(); err != nil {
		t.Fatalf("fixture: %v %s", err, out)
	}
	readyCtx, cancelReady := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelReady()
	if err := a.tmuxCommandContext(readyCtx, "wait-for", "ready").Run(); err != nil {
		t.Fatal(err)
	}
	if err := a.tmuxCommand("copy-mode", "-t", "source").Run(); err != nil {
		t.Fatal(err)
	}
	var navigated terminalViewportPane
	err = a.resizeTerminalNative(context.Background(), "source", "", 160, 30, func(rows, cols int) error {
		if err := b.tmuxCommand("resize-window", "-t", "source", "-x", strconv.Itoa(cols), "-y", strconv.Itoa(rows)).Run(); err != nil {
			return err
		}
		if err := b.tmuxCommand("send-keys", "-X", "-t", "source", "top-line", ";", "send-keys", "-X", "-t", "source", "start-of-line", ";", "send-keys", "-X", "-t", "source", "-N", "20", "cursor-right").Run(); err != nil {
			return err
		}
		panes, err := b.terminalViewportInventory(context.Background(), "=source:")
		if err != nil {
			return err
		}
		if len(panes) != 1 {
			return fmt.Errorf("fixture panes: %v", panes)
		}
		navigated = panes[0]
		if navigated.x != 20 {
			return fmt.Errorf("foreign navigation did not move to x20: %+v", navigated)
		}
		return nil
	}, true)
	if err != nil {
		t.Fatal(err)
	}
	after, err := a.terminalViewportInventory(context.Background(), "=source:")
	if err != nil || len(after) != 1 || after[0] != navigated {
		t.Fatalf("foreign input overwritten: wanted=%+v after=%+v err=%v", navigated, after, err)
	}
}

func TestTerminalResizeWindowSwitchBeforeIOCTLDoesNotResizeDestination(t *testing.T) {
	runtime, err := testsupport.MkdirShortTemp("tv-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	socket := filepath.Join(runtime, "tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	s := &server{ignoreSizeChecked: true, ignoreSizeSupported: true}
	run := func(args ...string) string {
		t.Helper()
		out, err := s.tmuxCommand(args...).CombinedOutput()
		if err != nil {
			t.Fatalf("private tmux %v: %v %s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	run("-f", os.DevNull, "new-session", "-d", "-s", "source", "-x", "80", "-y", "24", "exec cat")
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	run("new-window", "-d", "-t", "=source:", "exec cat")
	run("new-session", "-d", "-s", "alias", "-t", "source")
	run("select-window", "-t", "=alias:1")
	run("set-option", "-g", "status", "off")
	for _, target := range []string{"=source:0", "=source:1"} {
		run("set-option", "-w", "-t", target, "window-size", "largest")
	}
	attach := func(session string, cols, rows int) (*ptyPair, *exec.Cmd) {
		t.Helper()
		pair, err := openPTY(rows, cols)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(pair.close)
		clientCtx, cancelClient := context.WithCancel(context.Background())
		client := s.tmuxCommandContext(clientCtx, "attach-session", "-f", "ignore-size", "-t", s.exactTarget(session))
		client.SysProcAttr = terminalProcessAttributes()
		client.Env = append(client.Env, "TERM=xterm-256color")
		client.Stdin, client.Stdout, client.Stderr = pair.slave, pair.slave, pair.slave
		if err := client.Start(); err != nil {
			cancelClient()
			t.Fatal(err)
		}
		t.Cleanup(func() { cancelClient(); _ = client.Wait() })
		go func() { _, _ = io.Copy(io.Discard, pair.master) }()
		waitFixtureCondition(t, "attached "+session, time.Second, func() bool {
			return strings.Contains(run("list-clients", "-F", "#{client_tty}"), pair.tty)
		})
		return pair, client
	}
	source, sourceClient := attach("source", 80, 24)
	destination, _ := attach("alias", 100, 40)
	sourceToken, err := newTerminalResizeClaim()
	if err != nil {
		t.Fatal(err)
	}
	destinationToken, err := newTerminalResizeClaim()
	if err != nil {
		t.Fatal(err)
	}
	sourceWindow := run("display-message", "-p", "-t", "=source:", "#{window_id}")
	destinationWindow := run("display-message", "-p", "-t", "=alias:", "#{window_id}")
	if sourceWindow == destinationWindow {
		t.Fatal("fixture did not select independent linked-session windows")
	}
	for _, owner := range []struct {
		session       string
		pair          *ptyPair
		cols, rows    int
		token, window string
	}{
		{"source", source, 80, 24, sourceToken, sourceWindow},
		{"alias", destination, 100, 40, destinationToken, destinationWindow},
	} {
		if err := s.claimResizeAuthority(context.Background(), owner.session, owner.pair.tty, owner.cols, owner.rows,
			terminalAuthorityClaim{token: owner.token, window: owner.window}); err != nil {
			t.Fatal(err)
		}
	}
	waitFixtureCondition(t, "destination owner dimensions", time.Second, func() bool {
		return run("display-message", "-p", "-t", "=alias:", "#{window_width} #{window_height}") == "100 40"
	})
	var result terminalAuthorityResult
	called, fenced := false, false
	err = s.resizeTerminalNative(context.Background(), "source", source.tty, 150, 60, func(rows, cols int) error {
		called = true
		for _, line := range strings.Split(run("list-clients", "-F", "#{client_tty}\t#{client_flags}"), "\n") {
			fields := strings.Split(line, "\t")
			if len(fields) == 2 && fields[0] == source.tty {
				fenced = strings.Contains(fields[1], "ignore-size")
			}
		}
		// Move after the production census and immediately before the stale PTY write.
		run("select-window", "-t", "=source:1")
		if err := source.resize(rows, cols); err != nil {
			return err
		}
		return sourceClient.Process.Signal(syscall.SIGWINCH)
	}, true, terminalAuthorityClaim{token: sourceToken, window: sourceWindow, result: &result, continuing: true})
	if err != nil {
		t.Fatal(err)
	}
	if !called {
		t.Fatal("fixture did not exercise the pre-ioctl window switch")
	}
	waitFixtureCondition(t, "stale client consumed PTY dimensions", time.Second, func() bool {
		return strings.Contains(run("list-clients", "-F", "#{client_tty}\t#{client_width}\t#{client_height}"), source.tty+"\t150\t60")
	})
	if got := run("display-message", "-p", "-t", "=alias:", "#{window_width} #{window_height}"); got != "100 40" {
		t.Fatalf("stale resize crossed the physical-window boundary: got=%s expected=100 40 fenced=%v", got, fenced)
	}
	if !fenced {
		t.Fatal("private client still participated in sizing during the PTY mutation")
	}
	if result.window != "" {
		t.Fatalf("stale resize published ownership of %s", result.window)
	}
	if got := run("show-options", "-wv", "-t", destinationWindow, "@yomux_resize_authority"); got != destinationToken {
		t.Fatalf("stale resize replaced the destination owner: %s", got)
	}
}
