// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type recordedChatEvent struct {
	kind    string
	payload map[string]any
}
type chatEventRecorder struct {
	mu          sync.Mutex
	values      []recordedChatEvent
	failMessage atomic.Bool
	failTyping  atomic.Bool
}

func (recorder *chatEventRecorder) publish(_ context.Context, kind string, payload map[string]any) error {
	if kind == "chat_messages_changed" && recorder.failMessage.CompareAndSwap(true, false) {
		return errors.New("injected publication failure")
	}
	if kind == "chat_typing_changed" && recorder.failTyping.CompareAndSwap(true, false) {
		return errors.New("injected typing publication failure")
	}
	recorder.mu.Lock()
	defer recorder.mu.Unlock()
	recorder.values = append(recorder.values, recordedChatEvent{kind: kind, payload: payload})
	return nil
}

func (recorder *chatEventRecorder) ofType(kind string) []recordedChatEvent {
	recorder.mu.Lock()
	defer recorder.mu.Unlock()
	result := []recordedChatEvent{}
	for _, event := range recorder.values {
		if event.kind == kind {
			result = append(result, event)
		}
	}
	return result
}

func testChatEvents(t *testing.T, store *Store, recorder *chatEventRecorder) *Events {
	t.Helper()
	events, err := NewEvents(store, recorder.publish)
	if err != nil {
		t.Fatal(err)
	}
	return events
}

func insertEventMessage(t *testing.T, store *Store, uuid string) {
	t.Helper()
	if _, _, err := store.Insert(context.Background(), NewMessage{Message: Message{MessagePayload: MessagePayload{Username: "alice", SenderInstance: "browser", ClientUUID: uuid, Body: "committed"}}}); err != nil {
		t.Fatal(err)
	}
}

func TestEventsRepairPublicationWithoutRepeatingSend(t *testing.T) {
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return 1700000000 })
	recorder := &chatEventRecorder{}
	events := testChatEvents(t, store, recorder)
	ctx := context.Background()
	if err := events.Flush(ctx); err != nil {
		t.Fatal(err)
	}
	insertEventMessage(t, store, "one")
	recorder.failMessage.Store(true)
	if err := events.Flush(ctx); err == nil {
		t.Fatal("publication failure was hidden")
	}
	if err := events.Flush(ctx); err != nil {
		t.Fatal(err)
	}
	if err := events.Flush(ctx); err != nil {
		t.Fatal(err)
	}
	notices := recorder.ofType("chat_messages_changed")
	if len(notices) != 1 || notices[0].payload["revision"] != int64(1) {
		t.Fatalf("repaired notices=%v", notices)
	}
	page, err := store.PageBefore(ctx, DefaultRoom, nil, PageOptions{})
	if err != nil || len(page.Messages) != 1 {
		t.Fatalf("publication repair changed messages: count=%d err=%v", len(page.Messages), err)
	}
}

func TestEventsKeepIndependentProgressAcrossPortsAndCoalesceTail(t *testing.T) {
	path := filepath.Join(t.TempDir(), "chat.sqlite3")
	first := openTestStore(t, path, func() float64 { return 1700000000 })
	second := openTestStore(t, path, func() float64 { return 1700000000 })
	a, b := &chatEventRecorder{}, &chatEventRecorder{}
	one, two := testChatEvents(t, first, a), testChatEvents(t, second, b)
	insertEventMessage(t, first, "one")
	insertEventMessage(t, second, "two")
	for _, events := range []*Events{one, two} {
		if err := events.Flush(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	for _, recorder := range []*chatEventRecorder{a, b} {
		notices := recorder.ofType("chat_messages_changed")
		if len(notices) != 1 || notices[0].payload["revision"] != int64(2) {
			t.Fatalf("per-server tail notices=%v", notices)
		}
	}
	if _, _, err := second.Insert(context.Background(), NewMessage{Message: Message{Room: "other", MessagePayload: MessagePayload{Username: "alice", SenderInstance: "browser", ClientUUID: "private", Body: "other room"}}}); err != nil {
		t.Fatal(err)
	}
	if err := one.Flush(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(a.ofType("chat_messages_changed")) != 1 {
		t.Fatal("another room changed the global-room event tail")
	}
	for _, store := range []*Store{first, second} {
		messages, err := store.MessagesAfter(context.Background(), DefaultRoom, 0, PageOptions{})
		if err != nil || len(messages) != 2 {
			t.Fatalf("coalesced notice did not cover full delta: rows=%d err=%v", len(messages), err)
		}
	}
}

func TestEventsTypingRefreshExpiryAndSendClear(t *testing.T) {
	now := float64(1700000000)
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return now })
	recorder := &chatEventRecorder{}
	events := testChatEvents(t, store, recorder)
	ctx := context.Background()
	flush := func(want int) {
		t.Helper()
		if err := events.Flush(ctx); err != nil {
			t.Fatal(err)
		}
		if got := len(recorder.ofType("chat_typing_changed")); got != want {
			t.Fatalf("typing notices=%d want %d", got, want)
		}
	}
	flush(1)
	if _, err := store.SetTyping(ctx, DefaultRoom, "alice", "browser", true); err != nil {
		t.Fatal(err)
	}
	flush(2)
	now += 4
	if _, err := store.SetTyping(ctx, DefaultRoom, "alice", "browser", true); err != nil {
		t.Fatal(err)
	}
	flush(3)
	now += 2
	flush(3)
	now += 3
	flush(4)
	if _, err := store.SetTyping(ctx, DefaultRoom, "alice", "browser", true); err != nil {
		t.Fatal(err)
	}
	flush(5)
	insertEventMessage(t, store, "clears-typing")
	flush(6)
}

func TestEventsRunSupervisesFailureRejectsDuplicateAndJoins(t *testing.T) {
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return 1700000000 })
	var attempts atomic.Int64
	delivered := make(chan struct{}, 1)
	reported := make(chan error, 1)
	events, err := NewEvents(store, func(context.Context, string, map[string]any) error {
		if attempts.Add(1) == 1 {
			return errors.New("unit failed")
		}
		delivered <- struct{}{}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- events.Run(ctx, time.Millisecond, func(err error) { reported <- err }) }()
	select {
	case <-reported:
	case <-time.After(2 * time.Second):
		t.Fatal("event failure was not reported")
	}
	if err := events.Run(ctx, time.Hour, func(error) {}); err == nil || !strings.Contains(err.Error(), "already running") {
		t.Fatalf("duplicate loop error=%v", err)
	}
	select {
	case <-delivered:
	case <-time.After(2 * time.Second):
		t.Fatal("event owner did not recover after publication failure")
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("event owner did not join")
	}
}

func TestEventsQueuedFlushCanCancelAndRetry(t *testing.T) {
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return 1700000000 })
	insertEventMessage(t, store, "one")
	entered := make(chan struct{}, 1)
	release := make(chan struct{})
	events, err := NewEvents(store, func(ctx context.Context, _ string, _ map[string]any) error {
		select {
		case entered <- struct{}{}:
		default:
		}
		select {
		case <-release:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- events.Flush(ctx) }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("publisher did not enter")
	}
	queued, stop := context.WithCancel(context.Background())
	waiting := make(chan struct{})
	queuedDone := make(chan error, 1)
	go func() { queuedDone <- events.Flush(&waitingChatContext{Context: queued, waiting: waiting}) }()
	select {
	case <-waiting:
	case <-time.After(2 * time.Second):
		t.Fatal("queued flush did not wait for mutex")
	}
	stop()
	select {
	case err := <-queuedDone:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("queued cancellation error=%v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("queued flush did not cancel while publisher held mutex")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("publication cancellation error=%v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("cancelled publisher did not join")
	}
	close(release)
	if err := events.Flush(context.Background()); err != nil {
		t.Fatal(err)
	}
}

// Done signals entry into the mutex select, after its initial Err check.
type waitingChatContext struct {
	context.Context
	waiting chan struct{}
	once    sync.Once
}

func (ctx *waitingChatContext) Done() <-chan struct{} {
	ctx.once.Do(func() { close(ctx.waiting) })
	return ctx.Context.Done()
}

func TestEventsTypingFailurePreservesMessageProgress(t *testing.T) {
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return 1700000000 })
	recorder := &chatEventRecorder{}
	events := testChatEvents(t, store, recorder)
	insertEventMessage(t, store, "one")
	recorder.failTyping.Store(true)
	if err := events.Flush(context.Background()); err == nil {
		t.Fatal("typing failure was hidden")
	}
	if len(recorder.ofType("chat_messages_changed")) != 1 {
		t.Fatal("message was not published before typing failed")
	}
	if err := events.Flush(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(recorder.ofType("chat_messages_changed")) != 1 || len(recorder.ofType("chat_typing_changed")) != 1 {
		t.Fatal("retry repeated message or lost typing notice")
	}
}

func TestEventsConcurrentFlushDeduplicates(t *testing.T) {
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return 1700000000 })
	recorder := &chatEventRecorder{}
	events := testChatEvents(t, store, recorder)
	insertEventMessage(t, store, "one")
	start := make(chan struct{})
	results := make(chan error, 16)
	for i := 0; i < cap(results); i++ {
		go func() { <-start; results <- events.Flush(context.Background()) }()
	}
	close(start)
	for i := 0; i < cap(results); i++ {
		if err := <-results; err != nil {
			t.Fatal(err)
		}
	}
	if len(recorder.ofType("chat_messages_changed")) != 1 || len(recorder.ofType("chat_typing_changed")) != 1 {
		t.Fatal("concurrent flushes duplicated notices")
	}
}
