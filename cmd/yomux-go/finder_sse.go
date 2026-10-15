// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

// jsonSSEWriter keeps wire framing consistent while each route owns its event protocol.
type jsonSSEWriter struct {
	mu           sync.Mutex
	writer       http.ResponseWriter
	controller   *http.ResponseController
	closed       atomic.Bool
	done         chan struct{}
	stopCancel   func() bool
	cancelDone   chan struct{}
	writeTimeout time.Duration
	context      context.Context
}

func newJSONSSEWriter(writer http.ResponseWriter, contexts ...context.Context) (*jsonSSEWriter, bool) {
	_, ok := writer.(http.Flusher)
	if !ok {
		return nil, false
	}
	writer.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	writer.Header().Set("Cache-Control", "no-store")
	writer.Header().Set("Connection", "keep-alive")
	writer.Header().Set("X-Accel-Buffering", "no")
	stream := &jsonSSEWriter{writer: writer, controller: http.NewResponseController(writer), done: make(chan struct{}), writeTimeout: 5 * time.Second}
	if len(contexts) > 0 {
		stream.context = contexts[0]
		stream.cancelDone = make(chan struct{})
		stream.stopCancel = context.AfterFunc(contexts[0], func() {
			defer close(stream.cancelDone)
			stream.markClosed()
			// net/http deadlines interrupt a concurrent blocked Write or Flush. This callback
			// is joined before handler return, so it never touches a released response.
			_ = stream.controller.SetWriteDeadline(time.Now())
		})
	}
	return stream, true
}

func (stream *jsonSSEWriter) markClosed() {
	if stream.closed.CompareAndSwap(false, true) {
		close(stream.done)
	}
}

func (stream *jsonSSEWriter) withWriteDeadline(write func() error) error {
	stream.mu.Lock()
	defer stream.mu.Unlock()
	if stream.context != nil && stream.context.Err() != nil {
		stream.markClosed()
	}
	if stream.closed.Load() {
		return io.ErrClosedPipe
	}
	if err := stream.controller.SetWriteDeadline(time.Now().Add(stream.writeTimeout)); err != nil && !errors.Is(err, http.ErrNotSupported) {
		stream.markClosed()
		return err
	}
	// Cancellation may have interrupted deadline setup; do not overwrite its deadline.
	if stream.closed.Load() {
		_ = stream.controller.SetWriteDeadline(time.Now())
		return io.ErrClosedPipe
	}
	err := write()
	if err != nil {
		stream.markClosed()
	}
	if !stream.closed.Load() {
		_ = stream.controller.SetWriteDeadline(time.Time{})
	}
	return err
}

func (stream *jsonSSEWriter) flush() error { return stream.withWriteDeadline(stream.controller.Flush) }

// close prevents scheduled publishers from using the request-owned writer after the handler returns.
func (stream *jsonSSEWriter) close() {
	stream.markClosed()
	if stream.stopCancel != nil && !stream.stopCancel() {
		<-stream.cancelDone
	}
	stream.mu.Lock()
	stream.mu.Unlock()
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
	return stream.withWriteDeadline(func() error {
		n, err := stream.writer.Write(frame)
		if err != nil {
			return err
		}
		if n != len(frame) {
			return io.ErrShortWrite
		}
		return stream.controller.Flush()
	})
}
