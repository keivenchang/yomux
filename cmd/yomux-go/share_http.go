// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
)

// Network backpressure must never retain the registry or a document's mutex.
// Locked handlers encode into this bounded-by-product response, then publish
// after their unlock defers have run. Raw file delivery releases it first.
type shareHTTPResponse struct {
	context     context.Context
	cancel      context.CancelFunc
	stopScope   func() bool
	transport   *jsonSSEWriter
	destination http.ResponseWriter
	header      http.Header
	status      int
	body        bytes.Buffer
}

func newShareHTTPResponse(destination http.ResponseWriter, ctx context.Context) *shareHTTPResponse {
	return &shareHTTPResponse{destination: destination, header: destination.Header().Clone(), context: ctx}
}
func (reply *shareHTTPResponse) Header() http.Header { return reply.header }
func (reply *shareHTTPResponse) WriteHeader(status int) {
	if reply.status == 0 {
		reply.status = status
	}
}
func (reply *shareHTTPResponse) Write(data []byte) (int, error) {
	if reply.status == 0 {
		reply.status = http.StatusOK
	}
	return reply.body.Write(data)
}
func (reply *shareHTTPResponse) scope(lifetime context.Context) {
	if lifetime == nil {
		return
	}
	joined, cancel := context.WithCancel(reply.context)
	reply.context = joined
	reply.cancel = cancel
	reply.stopScope = context.AfterFunc(lifetime, cancel)
}
func (reply *shareHTTPResponse) prepare() {
	if reply.transport == nil {
		reply.transport = newResponseWriteOwner(reply.destination, reply.context)
	}
	for key, values := range reply.header {
		reply.destination.Header()[key] = values
	}
}
func (reply *shareHTTPResponse) release() http.ResponseWriter {
	reply.prepare()
	destination := &shareDeadlineWriter{ResponseWriter: reply.destination, owner: reply.transport}
	reply.destination = nil
	return destination
}
func (reply *shareHTTPResponse) publish() {
	defer func() {
		if reply.transport != nil {
			reply.transport.close()
		}
		if reply.stopScope != nil {
			reply.stopScope()
		}
		if reply.cancel != nil {
			reply.cancel()
		}
	}()
	if reply.destination == nil {
		return
	}
	reply.prepare()
	err := reply.transport.withWriteDeadline(func() error {
		if reply.status != 0 {
			reply.destination.WriteHeader(reply.status)
		}
		n, err := reply.destination.Write(reply.body.Bytes())
		if err == nil && n != reply.body.Len() {
			err = io.ErrShortWrite
		}
		return err
	})
	// Transport failures cannot acknowledge a disconnected browser. Accepted
	// edits already have their independent recovery journal.
	if err != nil {
		slog.Debug("share response delivery failed", "error", err)
	}
}

type shareDeadlineWriter struct {
	http.ResponseWriter
	owner *jsonSSEWriter
}

func (writer *shareDeadlineWriter) Unwrap() http.ResponseWriter { return writer.ResponseWriter }
func (writer *shareDeadlineWriter) Write(data []byte) (int, error) {
	n := 0
	err := writer.owner.withWriteDeadline(func() error { var err error; n, err = writer.ResponseWriter.Write(data); return err })
	return n, err
}
func readShareHTTPRequest(request *http.Request, limit int64) (*http.Request, error) {
	if request.Method != http.MethodPost && request.Method != http.MethodPatch {
		return request, nil
	}
	data, err := io.ReadAll(io.LimitReader(request.Body, limit+1))
	if err != nil {
		return nil, fmt.Errorf("read share request: %w", err)
	}
	if int64(len(data)) > limit {
		return nil, fmt.Errorf("share request exceeds limit")
	}
	clone := request.Clone(request.Context())
	clone.Body = io.NopCloser(bytes.NewReader(data))
	return clone, nil
}
