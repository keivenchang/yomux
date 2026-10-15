// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type nonFlushingResponseWriter struct {
	http.ResponseWriter
}

type blockingSSEResponseWriter struct {
	header         http.Header
	started        chan struct{}
	release        chan struct{}
	mu             sync.Mutex
	writes         int
	cancelObserved chan struct{}
}

func newBlockingSSEResponseWriter() *blockingSSEResponseWriter {
	return &blockingSSEResponseWriter{header: make(http.Header), started: make(chan struct{}), release: make(chan struct{}), cancelObserved: make(chan struct{}, 1)}
}

func (writer *blockingSSEResponseWriter) Header() http.Header { return writer.header }

func (writer *blockingSSEResponseWriter) WriteHeader(int) {}

func (writer *blockingSSEResponseWriter) Write(value []byte) (int, error) {
	writer.mu.Lock()
	writer.writes++
	writeNumber := writer.writes
	writer.mu.Unlock()
	if writeNumber == 2 {
		close(writer.started)
		<-writer.release
	}
	return len(value), nil
}

func (writer *blockingSSEResponseWriter) Flush() {}

func (writer *blockingSSEResponseWriter) SetWriteDeadline(deadline time.Time) error {
	if !deadline.IsZero() && !deadline.After(time.Now()) {
		select {
		case writer.cancelObserved <- struct{}{}:
		default:
		}
	}
	return nil
}

func (writer *blockingSSEResponseWriter) writeCount() int {
	writer.mu.Lock()
	defer writer.mu.Unlock()
	return writer.writes
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

func TestJSONSSEWriterCloseWaitsForInFlightPublishers(t *testing.T) {
	response := &blockingSSEResponseWriter{
		header:  make(http.Header),
		started: make(chan struct{}),
		release: make(chan struct{}),
	}
	stream, ok := newJSONSSEWriter(response)
	if !ok {
		t.Fatal("blocking response writer does not expose http.Flusher")
	}
	if err := stream.write("start", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	writeDone := make(chan error, 1)
	go func() { writeDone <- stream.write("chunk", map[string]any{"value": 1}) }()
	select {
	case <-response.started:
	case <-time.After(time.Second):
		t.Fatal("publisher did not enter the response writer")
	}
	closeStarted := make(chan struct{})
	closeDone := make(chan struct{})
	go func() {
		close(closeStarted)
		stream.close()
		close(closeDone)
	}()
	<-closeStarted
	select {
	case <-stream.done:
	case <-time.After(time.Second):
		t.Fatal("close did not begin")
	}
	select {
	case <-closeDone:
		t.Fatal("close returned while a publisher was using the response writer")
	default:
	}
	close(response.release)
	select {
	case err := <-writeDone:
		if err != nil {
			t.Fatalf("in-flight publish failed: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("in-flight publisher did not finish")
	}
	select {
	case <-closeDone:
	case <-time.After(time.Second):
		t.Fatal("close did not finish after the in-flight publisher")
	}
	if err := stream.write("late", map[string]any{"value": 2}); !errors.Is(err, io.ErrClosedPipe) {
		t.Fatalf("late publish error = %v, want closed pipe", err)
	}
	if got := response.writeCount(); got != 2 {
		t.Fatalf("underlying response writes = %d, want only start and in-flight chunk", got)
	}
}

// The transport blocks at the selected phase until cancellation advances its deadline.
type interruptibleSSEWriter struct {
	phase       string
	entered     chan struct{}
	interrupted chan struct{}
	once        sync.Once
}

func (w *interruptibleSSEWriter) Header() http.Header { return make(http.Header) }
func (w *interruptibleSSEWriter) WriteHeader(int)     {}
func (w *interruptibleSSEWriter) Write(p []byte) (int, error) {
	if w.phase == "write" {
		close(w.entered)
		<-w.interrupted
		return 0, context.Canceled
	}
	return len(p), nil
}
func (w *interruptibleSSEWriter) Flush() {}
func (w *interruptibleSSEWriter) FlushError() error {
	if w.phase == "flush" {
		close(w.entered)
		<-w.interrupted
		return context.Canceled
	}
	return nil
}
func (w *interruptibleSSEWriter) SetWriteDeadline(deadline time.Time) error {
	if !deadline.IsZero() && !deadline.After(time.Now()) {
		w.once.Do(func() { close(w.interrupted) })
	}
	return nil
}
func TestJSONSSECancellationInterruptsWriteAndFlush(t *testing.T) {
	for _, phase := range []string{"write", "flush"} {
		t.Run(phase, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			response := &interruptibleSSEWriter{phase: phase, entered: make(chan struct{}), interrupted: make(chan struct{})}
			stream, _ := newJSONSSEWriter(response, ctx)
			finished := make(chan error, 1)
			go func() { finished <- stream.write("chunk", true) }()
			select {
			case <-response.entered:
			case <-time.After(time.Second):
				t.Fatal("transport never entered " + phase)
			}
			cancel()
			select {
			case err := <-finished:
				if err == nil {
					t.Fatal("interrupted transport succeeded")
				}
			case <-time.After(time.Second):
				t.Fatal("cancellation did not interrupt " + phase)
			}
			stream.close()
			if err := stream.write("late", true); !errors.Is(err, io.ErrClosedPipe) {
				t.Fatalf("late callback: %v", err)
			}
		})
	}
}

type failingSSEWriter struct {
	*httptest.ResponseRecorder
	short   bool
	failure error
}

func (w failingSSEWriter) Write(p []byte) (int, error) {
	if w.short {
		return len(p) - 1, nil
	}
	return w.ResponseRecorder.Write(p)
}
func (w failingSSEWriter) FlushError() error { return w.failure }
func TestJSONSSETransportFailureIsTerminal(t *testing.T) {
	for _, short := range []bool{false, true} {
		t.Run(fmt.Sprint(short), func(t *testing.T) {
			failure := errors.New("flush failed")
			response := failingSSEWriter{httptest.NewRecorder(), short, failure}
			stream, _ := newJSONSSEWriter(response)
			want := failure
			if short {
				want = io.ErrShortWrite
			}
			if err := stream.write("chunk", true); !errors.Is(err, want) {
				t.Fatalf("error %v, want %v", err, want)
			}
			if err := stream.write("late", true); !errors.Is(err, io.ErrClosedPipe) {
				t.Fatalf("failure was not terminal: %v", err)
			}
			stream.close()
		})
	}
}
func TestJSONSSEConcurrentFramesRemainWhole(t *testing.T) {
	response := httptest.NewRecorder()
	stream, _ := newJSONSSEWriter(response)
	var writers sync.WaitGroup
	failures := make(chan error, 32)
	for i := range 32 {
		writers.Add(1)
		go func() {
			defer writers.Done()
			failures <- stream.writeWithID(fmt.Sprint(i), "chunk", map[string]int{"value": i})
		}()
	}
	writers.Wait()
	close(failures)
	stream.close()
	for err := range failures {
		if err != nil {
			t.Fatal(err)
		}
	}
	frames := strings.Split(strings.TrimSuffix(response.Body.String(), "\n\n"), "\n\n")
	if len(frames) != 32 {
		t.Fatalf("frame count %d", len(frames))
	}
	for _, frame := range frames {
		if len(strings.Split(frame, "\n")) != 3 || !strings.Contains(frame, "\nevent: chunk\ndata: ") {
			t.Fatalf("interleaved frame %q", frame)
		}
	}
}
