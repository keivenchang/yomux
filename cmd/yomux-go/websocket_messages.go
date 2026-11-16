// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"fmt"
)

const websocketMessageLimit = 16 << 20

// One connection owns assembly across control frames and both bridge phases.
// Incomplete JSON must never reach the terminal's raw-input compatibility path.
type websocketMessageReader struct {
	reader  *bufio.Reader
	opcode  byte
	partial []byte
	reading bool
}

func (r *websocketMessageReader) incomplete() bool { return r.reading || r.opcode != 0 }

func (r *websocketMessageReader) read() (byte, []byte, error) {
	for {
		if _, err := r.reader.Peek(1); err != nil {
			return 0, nil, err
		}
		r.reading = true
		header, payload, err := readFrameFragment(r.reader)
		if err != nil {
			return 0, nil, err
		}
		r.reading = false
		opcode, final := header&0x0f, header&0x80 != 0
		if header&0x70 != 0 {
			return 0, nil, fmt.Errorf("unsupported websocket extension bits")
		}
		switch opcode {
		case 8, 9, 10:
			if !final || len(payload) > 125 {
				return 0, nil, fmt.Errorf("invalid websocket control frame")
			}
			return opcode, payload, nil
		case 0:
			if r.opcode == 0 {
				return 0, nil, fmt.Errorf("unexpected websocket continuation")
			}
		case 1, 2:
			if r.opcode != 0 {
				return 0, nil, fmt.Errorf("interrupted fragmented websocket message")
			}
			if final {
				return opcode, payload, nil
			}
			r.opcode = opcode
		default:
			return 0, nil, fmt.Errorf("unsupported websocket opcode %d", opcode)
		}
		if len(payload) > websocketMessageLimit-len(r.partial) {
			return 0, nil, fmt.Errorf("websocket message too large")
		}
		r.partial = append(r.partial, payload...)
		if final {
			opcode, payload = r.opcode, r.partial
			r.opcode, r.partial = 0, nil
			return opcode, payload, nil
		}
	}
}
