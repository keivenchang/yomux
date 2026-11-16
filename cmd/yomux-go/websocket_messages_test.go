// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"net"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func appendWebsocketFragment(t *testing.T, wire *bytes.Buffer, opcode byte, final bool, payload []byte) {
	t.Helper()
	start := wire.Len()
	if err := writeFrame(wire, opcode, payload); err != nil {
		t.Fatal(err)
	}
	if !final {
		wire.Bytes()[start] &^= 0x80
	}
}

func TestWebsocketMessagesEverySplitAndInterleavedControls(t *testing.T) {
	message := []byte(`{"type":"input","data":"echo café\n","user_input":false}`)
	for _, opcode := range []byte{1, 2} {
		for split := 0; split <= len(message); split++ {
			t.Run(fmt.Sprintf("opcode-%d/split-%d", opcode, split), func(t *testing.T) {
				var wire bytes.Buffer
				appendWebsocketFragment(t, &wire, opcode, false, message[:split])
				appendWebsocketFragment(t, &wire, 9, true, []byte("ping"))
				appendWebsocketFragment(t, &wire, 0, false, nil)
				appendWebsocketFragment(t, &wire, 10, true, []byte("pong"))
				appendWebsocketFragment(t, &wire, 0, true, message[split:])
				appendWebsocketFragment(t, &wire, 1, true, []byte("next message"))
				reader := websocketMessageReader{reader: bufio.NewReader(&wire)}
				for _, control := range []struct {
					opcode byte
					body   string
				}{{9, "ping"}, {10, "pong"}} {
					got, payload, err := reader.read()
					if err != nil || got != control.opcode || string(payload) != control.body || !reader.incomplete() {
						t.Fatalf("control: %d %q %v incomplete=%v", got, payload, err, reader.incomplete())
					}
				}
				got, payload, err := reader.read()
				if err != nil || got != opcode || !bytes.Equal(payload, message) || reader.incomplete() {
					t.Fatalf("assembled: %d %q %v incomplete=%v", got, payload, err, reader.incomplete())
				}
				got, payload, err = reader.read()
				if err != nil || got != 1 || string(payload) != "next message" {
					t.Fatalf("next: %d %q %v", got, payload, err)
				}
			})
		}
	}
}

func TestWebsocketMessagesRejectInvalidFragmentTransitions(t *testing.T) {
	for name, frames := range map[string][]byte{
		"orphan continuation":     {0x80, 0},
		"interrupted message":     {0x01, 0, 0x81, 0},
		"fragmented ping":         {0x09, 0},
		"reserved opcode":         {0x83, 0},
		"unsupported extension":   {0xc1, 0},
		"unfinished continuation": {0x01, 0, 0x00},
	} {
		t.Run(name, func(t *testing.T) {
			reader := websocketMessageReader{reader: bufio.NewReader(bytes.NewReader(frames))}
			if _, _, err := reader.read(); err == nil {
				t.Fatal("invalid fragmented message accepted")
			}
		})
	}
	var wire bytes.Buffer
	appendWebsocketFragment(t, &wire, 9, true, make([]byte, 126))
	reader := websocketMessageReader{reader: bufio.NewReader(&wire)}
	if _, _, err := reader.read(); err == nil {
		t.Fatal("oversized control frame accepted")
	}
	wire.Reset()
	appendWebsocketFragment(t, &wire, 1, false, make([]byte, websocketMessageLimit))
	appendWebsocketFragment(t, &wire, 0, true, []byte("x"))
	reader = websocketMessageReader{reader: bufio.NewReader(&wire)}
	if _, _, err := reader.read(); err == nil || !strings.Contains(err.Error(), "message too large") {
		t.Fatalf("fragmented message exceeded the aggregate bound: %v", err)
	}
}

func TestTerminalBridgeRetainsBufferedHandshakeInput(t *testing.T) {
	s, root := terminalControlFixture(t)
	var wire bytes.Buffer
	appendWebsocketFragment(t, &wire, 1, false, []byte(`{"type":"input","data":"buffered-`))
	appendWebsocketFragment(t, &wire, 0, true, []byte(`input\n","user_input":false}`))
	serverSide, client := net.Pipe()
	done := make(chan struct{})
	go func() {
		defer close(done)
		reader := bufio.NewReader(io.MultiReader(bytes.NewReader(wire.Bytes()), serverSide))
		s.bridge(&wsConn{Conn: serverSide, reader: reader}, "fixture", false, "")
	}()
	t.Cleanup(func() {
		_ = client.Close()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("buffered terminal bridge did not close")
		}
	})
	waitTerminalControlFile(t, filepath.Join(root, "attached"), time.Second)
	if err := client.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	reader := bufio.NewReader(client)
	var output strings.Builder
	for !strings.Contains(output.String(), "buffered-input") {
		_, payload, err := readFrame(reader)
		if err != nil {
			t.Fatalf("buffered fragmented input was lost: output=%q error=%v", output.String(), err)
		}
		output.Write(payload)
		if strings.Contains(output.String(), `{"type":"input"`) {
			t.Fatalf("partial buffered JSON leaked into PTY: %q", output.String())
		}
	}
}
