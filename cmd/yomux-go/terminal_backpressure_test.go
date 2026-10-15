// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestTerminalDisconnectDuringSlowChildInput(t *testing.T) {
	for _, jsonInput := range []bool{false, true} {
		t.Run(map[bool]string{false: "raw", true: "json"}[jsonInput], func(t *testing.T) {
			s, root := terminalControlFixture(t)
			s.terminalWriteTimeout = 100 * time.Millisecond
			s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
				command := exec.CommandContext(ctx, "tmux", args...)
				if args[0] == "attach-session" {
					command = exec.Command("tmux", args...)
				}
				command.Env = []string{"PATH=" + os.Getenv("PATH"), "TERMINAL_CONTROL_FIXTURE=" + root}
				return command
			}
			ctx, cancel := context.WithCancel(context.Background())
			scriptPath := filepath.Join(root, "tmux")
			script, err := os.ReadFile(scriptPath)
			if err != nil {
				t.Fatal(err)
			}
			script = []byte(strings.Replace(string(script), "exec /bin/cat", `stty raw -echo
  : > "$TERMINAL_CONTROL_FIXTURE/raw"
  exec sleep 30`, 1))
			if err := os.WriteFile(scriptPath, script, 0700); err != nil {
				t.Fatal(err)
			}
			serverSide, clientSide := net.Pipe()
			done := make(chan struct{})
			go func() { s.bridge(&wsConn{Conn: serverSide}, "fixture", false, "", ctx); close(done) }()
			defer func() {
				cancel()
				clientSide.Close()
				s.mu.Lock()
				for cmd := range s.children {
					cmd.Process.Kill()
				}
				s.mu.Unlock()
				<-done
			}()
			if err := writeFrame(clientSide, 1, []byte(`{"type":"resize","rows":36,"cols":120,"foreground":true}`)); err != nil {
				t.Fatal(err)
			}
			waitTerminalControlFile(t, filepath.Join(root, "raw"), time.Second)
			payload := bytes.Repeat([]byte("x"), 8<<20)
			if jsonInput {
				payload = append(append([]byte(`{"type":"input","data":"`), payload...), []byte(`"}`)...)
			}
			if err := writeFrame(clientSide, 1, payload); err != nil {
				t.Fatal(err)
			}
			clientSide.Close()
			select {
			case <-done:
			case <-time.After(6 * time.Second):
				t.Fatal("disconnected terminal retained blocked stdin writer")
			}
		})
	}
}

func TestTerminalSlowPeerFrameRecovers(t *testing.T) {
	serverSide, clientSide := net.Pipe()
	defer clientSide.Close()
	defer serverSide.Close()
	conn := &wsConn{Conn: serverSide, writeTimeout: 100 * time.Millisecond}
	done := make(chan error, 1)
	go func() { done <- conn.frame(2, []byte("output")) }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("slow peer write succeeded")
		}
	case <-time.After(6 * time.Second):
		clientSide.Close()
		<-done
		t.Fatal("slow peer retained frame writer")
	}
}
