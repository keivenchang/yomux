// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"net/http"
)

// jsonSSEWriter keeps wire framing consistent while each route owns its event protocol.
type jsonSSEWriter struct {
	writer  http.ResponseWriter
	flusher http.Flusher
}

func newJSONSSEWriter(writer http.ResponseWriter) (*jsonSSEWriter, bool) {
	flusher, ok := writer.(http.Flusher)
	if !ok {
		return nil, false
	}
	writer.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	writer.Header().Set("Cache-Control", "no-store")
	writer.Header().Set("Connection", "keep-alive")
	writer.Header().Set("X-Accel-Buffering", "no")
	return &jsonSSEWriter{writer: writer, flusher: flusher}, true
}

func (stream *jsonSSEWriter) flush() {
	stream.flusher.Flush()
}

func (stream *jsonSSEWriter) write(event string, payload any) error {
	return stream.writeWithID("", event, payload)
}

func (stream *jsonSSEWriter) writeWithID(id, event string, payload any) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	frame := make([]byte, 0, len(id)+len(event)+len(data)+20)
	if id != "" {
		frame = append(frame, "id: "...)
		frame = append(frame, id...)
		frame = append(frame, '\n')
	}
	frame = append(frame, "event: "...)
	frame = append(frame, event...)
	frame = append(frame, "\ndata: "...)
	frame = append(frame, data...)
	frame = append(frame, '\n', '\n')
	if _, err := stream.writer.Write(frame); err != nil {
		return err
	}
	stream.flusher.Flush()
	return nil
}
