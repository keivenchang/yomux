// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// Keep the production deadline path while shortening only the fixture transport budget.
type shortDeadlineSSEWriter struct{ http.ResponseWriter }

func (w shortDeadlineSSEWriter) Flush() { w.ResponseWriter.(http.Flusher).Flush() }
func (w shortDeadlineSSEWriter) SetWriteDeadline(deadline time.Time) error {
	if !deadline.IsZero() && deadline.After(time.Now()) {
		deadline = time.Now().Add(100 * time.Millisecond)
	}
	return http.NewResponseController(w.ResponseWriter).SetWriteDeadline(deadline)
}
func (w shortDeadlineSSEWriter) FlushError() error {
	return http.NewResponseController(w.ResponseWriter).Flush()
}

func TestJSONSSERealSlowReaderRecovers(t *testing.T) {
	started := make(chan struct{})
	finished := make(chan error, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		stream, _ := newJSONSSEWriter(&loggingResponseWriter{ResponseWriter: shortDeadlineSSEWriter{w}})
		defer stream.close()
		if err := stream.write("ready", true); err != nil {
			finished <- err
			return
		}
		close(started)
		finished <- stream.write("chunk", strings.Repeat("x", 8<<20))
	}))
	defer server.Close()
	conn, err := net.Dial("tcp", strings.TrimPrefix(server.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := io.WriteString(conn, "GET /stream HTTP/1.1\r\nHost: fixture\r\n\r\n"); err != nil {
		t.Fatal(err)
	}
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("stream did not start")
	}
	// The peer deliberately stops reading. A real TCP write must fail within its frame budget.
	select {
	case err := <-finished:
		if err == nil {
			t.Fatal("oversized frame unexpectedly reached a non-reading peer")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("slow reader retained the stream worker beyond its write budget")
	}
	response, err := server.Client().Get(server.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNoContent {
		t.Fatalf("unrelated request status %d", response.StatusCode)
	}
}

func TestJSONSSERealDisconnectCancelsAndJoins(t *testing.T) {
	finished := make(chan error, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		stream, _ := newJSONSSEWriter(w, r.Context())
		defer stream.close()
		if err := stream.write("ready", true); err != nil {
			finished <- err
			return
		}
		<-r.Context().Done()
		// Late callbacks are refused before touching the response.
		finished <- stream.write("late", true)
	}))
	defer server.Close()
	conn, err := net.Dial("tcp", strings.TrimPrefix(server.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(2 * time.Second))
	io.WriteString(conn, "GET / HTTP/1.1\r\nHost: fixture\r\n\r\n")
	reader := bufio.NewReader(conn)
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(line, "data: true") {
			break
		}
	}
	conn.Close()
	select {
	case err := <-finished:
		if !errors.Is(err, io.ErrClosedPipe) {
			t.Fatalf("late write: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("disconnect did not recover handler")
	}
}

func TestJSONSSECancelledBeforeWrite(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	recorder := httptest.NewRecorder()
	stream, _ := newJSONSSEWriter(recorder, ctx)
	cancel()
	select {
	case <-stream.done:
	case <-time.After(time.Second):
		t.Fatal("cancellation not observed")
	}
	stream.close()
	if err := stream.write("late", true); !errors.Is(err, io.ErrClosedPipe) {
		t.Fatalf("write: %v", err)
	}
	if recorder.Body.Len() != 0 {
		t.Fatal("cancelled writer emitted bytes")
	}
}
