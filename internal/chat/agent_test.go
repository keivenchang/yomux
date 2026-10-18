// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func testAgentSource(t *testing.T, service *Service) MessagePayload {
	t.Helper()
	sent, err := service.Send(context.Background(), "alice", SendRequest{BrowserInstance: "browser", ClientUUID: "question", Body: "/yo first line\nsecond line"}, "fr", "")
	if err != nil {
		t.Fatal(err)
	}
	return sent.Message
}

func testAgentOwner(t *testing.T, service *Service, publish EventPublisher, respond AgentResponder) *AgentReplies {
	t.Helper()
	events, err := NewEvents(service.store, publish)
	if err != nil {
		t.Fatal(err)
	}
	owner, err := NewAgentReplies(service, events, respond)
	if err != nil {
		t.Fatal(err)
	}
	return owner
}

func assertNoAgentTyping(t *testing.T, service *Service) {
	t.Helper()
	leases, err := service.store.TypingSnapshot(context.Background(), DefaultRoom)
	if err != nil {
		t.Fatal(err)
	}
	for _, lease := range leases {
		if lease.Username == AgentUsername {
			t.Fatal("agent typing lease escaped completed request")
		}
	}
}

func TestAgentRepliesRefreshJoinAndPersistIdempotently(t *testing.T) {
	now := float64(1700000000)
	service := testService(t, &now)
	source := testAgentSource(t, service)
	var clock atomic.Int64
	clock.Store(1700000000000)
	service.store.clock = func() float64 { return float64(clock.Add(1)) / 1000 }
	refreshed := make(chan struct{})
	var once sync.Once
	var notices atomic.Int64
	owner := testAgentOwner(t, service, func(_ context.Context, kind string, _ map[string]any) error {
		if kind == "chat_typing_changed" && notices.Add(1) >= 2 {
			once.Do(func() { close(refreshed) })
		}
		return nil
	}, func(ctx context.Context, request AgentRequest) (string, error) {
		if request.RequestID != "yochat-1" || request.Message != "first line\nsecond line" || request.Locale != "fr" || request.AccessRole != "readonly" {
			t.Errorf("controller context changed: %+v", request)
		}
		select {
		case <-refreshed:
		case <-ctx.Done():
			return "", ctx.Err()
		}
		leases, err := service.store.TypingSnapshot(ctx, DefaultRoom)
		if err != nil {
			return "", err
		}
		if len(leases) != 1 || leases[0].Username != AgentUsername || !strings.HasPrefix(leases[0].BrowserInstance, AgentInstance+"-1-") {
			t.Error("agent typing absent during response")
		}
		return "  answer  ", nil
	})
	owner.refreshInterval = time.Millisecond
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	first, err := owner.Reply(ctx, "alice", "browser", source.ID, "fr", "readonly")
	if err != nil {
		t.Fatal(err)
	}
	if !first.Created || first.SourceID != source.ID || first.Message.Body != "answer" {
		t.Fatal("agent reply changed")
	}
	assertNoAgentTyping(t, service)
	second, err := owner.Reply(ctx, "alice", "browser", source.ID, "fr", "readonly")
	if err != nil {
		t.Fatal(err)
	}
	if second.Created || second.Message.ID != first.Message.ID {
		t.Fatal("agent retry repeated insertion")
	}
	assertNoAgentTyping(t, service)
}

func TestAgentRepliesOwnershipFailureDoesNotInvokeResponder(t *testing.T) {
	now := float64(1700000000)
	service := testService(t, &now)
	source := testAgentSource(t, service)
	owner := testAgentOwner(t, service, func(context.Context, string, map[string]any) error { return nil }, func(context.Context, AgentRequest) (string, error) {
		t.Fatal("foreign source invoked responder")
		return "", nil
	})
	_, err := owner.Reply(context.Background(), "bob", "browser", source.ID, "en", "admin")
	var protocol *Error
	if !errors.As(err, &protocol) || protocol.Code != "forbidden" {
		t.Fatalf("ownership error=%v", err)
	}
	assertNoAgentTyping(t, service)
}

func TestAgentRepliesCancellationClearsLeaseAndJoins(t *testing.T) {
	now := float64(1700000000)
	service := testService(t, &now)
	source := testAgentSource(t, service)
	entered := make(chan struct{})
	owner := testAgentOwner(t, service, func(context.Context, string, map[string]any) error { return nil }, func(ctx context.Context, _ AgentRequest) (string, error) {
		close(entered)
		<-ctx.Done()
		return "", ctx.Err()
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := owner.Reply(ctx, "alice", "browser", source.ID, "en", "admin"); done <- err }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("responder did not enter")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancel error=%v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("request did not join")
	}
	assertNoAgentTyping(t, service)
	messages, err := service.store.MessagesAfter(context.Background(), DefaultRoom, 0, PageOptions{})
	if err != nil || len(messages) != 1 {
		t.Fatalf("cancelled responder wrote answer: rows=%d err=%v", len(messages), err)
	}
}

func TestAgentRepliesFailuresPropagateAndClearTyping(t *testing.T) {
	for _, phase := range []string{"start", "refresh", "response", "cleanup"} {
		t.Run(phase, func(t *testing.T) {
			now := float64(1700000000)
			service := testService(t, &now)
			source := testAgentSource(t, service)
			failure := errors.New("injected " + phase + " failure")
			var clock atomic.Int64
			clock.Store(1700000000000)
			service.store.clock = func() float64 { return float64(clock.Add(1)) / 1000 }
			var typings atomic.Int64
			owner := testAgentOwner(t, service, func(_ context.Context, kind string, _ map[string]any) error {
				if kind != "chat_typing_changed" {
					return nil
				}
				count := typings.Add(1)
				if (phase == "start" && count == 1) || ((phase == "refresh" || phase == "cleanup") && count == 2) {
					return failure
				}
				return nil
			}, func(ctx context.Context, _ AgentRequest) (string, error) {
				if phase == "start" {
					t.Fatal("failed start invoked responder")
				}
				if phase == "refresh" {
					<-ctx.Done()
					return "", ctx.Err()
				}
				if phase == "response" {
					return "", failure
				}
				return "answer", nil
			})
			if phase == "refresh" {
				owner.refreshInterval = time.Millisecond
			} else {
				owner.refreshInterval = time.Hour
			}
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			_, err := owner.Reply(ctx, "alice", "browser", source.ID, "en", "admin")
			if !errors.Is(err, failure) {
				t.Fatalf("%s failure=%v", phase, err)
			}
			assertNoAgentTyping(t, service)
		})
	}
}

func TestAgentRepliesConcurrentRequestsOwnIndependentTyping(t *testing.T) {
	now := float64(1700000000)
	service := testService(t, &now)
	source := testAgentSource(t, service)
	entered := make(chan int, 2)
	release := []chan struct{}{make(chan struct{}), make(chan struct{})}
	var calls atomic.Int64
	owner := testAgentOwner(t, service, func(context.Context, string, map[string]any) error { return nil }, func(ctx context.Context, _ AgentRequest) (string, error) {
		index := int(calls.Add(1)) - 1
		entered <- index
		select {
		case <-release[index]:
			return "answer", nil
		case <-ctx.Done():
			return "", ctx.Err()
		}
	})
	owner.refreshInterval = time.Hour
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	results := make(chan error, 2)
	for i := 0; i < 2; i++ {
		go func() { _, err := owner.Reply(ctx, "alice", "browser", source.ID, "en", "admin"); results <- err }()
	}
	for i := 0; i < 2; i++ {
		select {
		case <-entered:
		case <-ctx.Done():
			t.Fatal("concurrent responders did not enter")
		}
	}
	leases, err := service.store.TypingSnapshot(ctx, DefaultRoom)
	if err != nil || len(leases) != 2 {
		t.Fatalf("concurrent leases=%d err=%v", len(leases), err)
	}
	close(release[0])
	if err := <-results; err != nil {
		t.Fatal(err)
	}
	leases, err = service.store.TypingSnapshot(ctx, DefaultRoom)
	if err != nil || len(leases) != 1 {
		t.Fatalf("first completion cleared other's typing: leases=%d err=%v", len(leases), err)
	}
	close(release[1])
	if err := <-results; err != nil {
		t.Fatal(err)
	}
	assertNoAgentTyping(t, service)
	messages, err := service.store.MessagesAfter(ctx, DefaultRoom, 0, PageOptions{})
	if err != nil || len(messages) != 2 {
		t.Fatalf("concurrent replies repeated insertion: rows=%d err=%v", len(messages), err)
	}
}

func TestAgentRepliesResponderPanicJoinsAndClearsLease(t *testing.T) {
	now := float64(1700000000)
	service := testService(t, &now)
	source := testAgentSource(t, service)
	failure := errors.New("injected responder panic")
	owner := testAgentOwner(t, service, func(context.Context, string, map[string]any) error { return nil }, func(context.Context, AgentRequest) (string, error) { panic(failure) })
	func() {
		defer func() {
			if recovered := recover(); recovered != failure {
				t.Fatalf("responder panic=%v", recovered)
			}
		}()
		_, _ = owner.Reply(context.Background(), "alice", "browser", source.ID, "en", "admin")
	}()
	assertNoAgentTyping(t, service)
}

func TestAgentRepliesCommittedAnswerPublicationFailureAndRetry(t *testing.T) {
	now := float64(1700000000)
	service := testService(t, &now)
	source := testAgentSource(t, service)
	failure := errors.New("injected answer publication failure")
	var fail atomic.Bool
	fail.Store(true)
	var deliveries atomic.Int64
	var calls atomic.Int64
	owner := testAgentOwner(t, service, func(_ context.Context, kind string, payload map[string]any) error {
		if kind == "chat_messages_changed" && payload["message_id"] == int64(2) {
			if fail.CompareAndSwap(true, false) {
				return failure
			}
			deliveries.Add(1)
		}
		return nil
	}, func(context.Context, AgentRequest) (string, error) {
		if calls.Add(1) == 1 {
			return "first answer", nil
		}
		return "retried answer", nil
	})
	if _, err := owner.Reply(context.Background(), "alice", "browser", source.ID, "en", "admin"); !errors.Is(err, failure) {
		t.Fatalf("publication error=%v", err)
	}
	retried, err := owner.Reply(context.Background(), "alice", "browser", source.ID, "en", "admin")
	if err != nil {
		t.Fatal(err)
	}
	if retried.Created || retried.Message.ID != 2 || retried.Message.Body != "first answer" || deliveries.Load() != 1 {
		t.Fatal("retry duplicated, replaced, or failed to publish committed answer")
	}
	assertNoAgentTyping(t, service)
}
