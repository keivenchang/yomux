// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type nonFlushingResponseWriter struct {
	http.ResponseWriter
}

func TestJSONSSEWriterOwnsSharedHeadersAndFraming(t *testing.T) {
	recorder := httptest.NewRecorder()
	stream, ok := newJSONSSEWriter(recorder)
	if !ok {
		t.Fatal("httptest.ResponseRecorder does not expose http.Flusher")
	}
	if err := stream.write("ready", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	if err := stream.writeWithID("42", "item", map[string]any{"value": "x"}); err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string]string{
		"Content-Type":      "text/event-stream; charset=utf-8",
		"Cache-Control":     "no-store",
		"Connection":        "keep-alive",
		"X-Accel-Buffering": "no",
	} {
		if got := recorder.Header().Get(key); got != want {
			t.Errorf("%s = %q, want %q", key, got, want)
		}
	}
	want := "event: ready\ndata: {\"ok\":true}\n\nid: 42\nevent: item\ndata: {\"value\":\"x\"}\n\n"
	if got := recorder.Body.String(); !strings.Contains(got, want) {
		t.Fatalf("SSE body = %q, want frame %q", got, want)
	}
}

func TestJSONSSEWriterRejectsNonFlushingResponseWriter(t *testing.T) {
	writer := &nonFlushingResponseWriter{ResponseWriter: httptest.NewRecorder()}
	if _, ok := newJSONSSEWriter(writer); ok {
		t.Fatal("non-flushing response writer was accepted")
	}
}
