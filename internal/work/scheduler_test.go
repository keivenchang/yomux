// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package work

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestSchedulerBoundsAdmissionAndRunsAcceptedTask(t *testing.T) {
	scheduler := New(1, 1)
	defer scheduler.Close()
	started := make(chan struct{})
	release := make(chan struct{})
	first, err := scheduler.Submit(context.Background(), "first", func(context.Context) (any, error) {
		close(started)
		<-release
		return "first", nil
	})
	if err != nil {
		t.Fatal(err)
	}
	<-started
	if _, err := scheduler.Submit(context.Background(), "second", func(context.Context) (any, error) { return "second", nil }); err != nil {
		t.Fatal(err)
	}
	if _, err := scheduler.Submit(context.Background(), "third", func(context.Context) (any, error) { return "third", nil }); !errors.Is(err, ErrBusy) {
		t.Fatalf("third admission error = %v, want ErrBusy", err)
	}
	close(release)
	select {
	case result := <-first:
		if result.Err != nil || result.Value != "first" {
			t.Fatalf("first result = %#v", result)
		}
	case <-time.After(time.Second):
		t.Fatal("first task did not finish")
	}
}

func TestSchedulerCancellationReachesQueuedTask(t *testing.T) {
	scheduler := New(1, 1)
	defer scheduler.Close()
	started := make(chan struct{})
	release := make(chan struct{})
	if _, err := scheduler.Submit(context.Background(), "running", func(context.Context) (any, error) {
		close(started)
		<-release
		return nil, nil
	}); err != nil {
		t.Fatal(err)
	}
	<-started
	result, err := scheduler.Submit(context.Background(), "queued", func(ctx context.Context) (any, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	})
	if err != nil {
		t.Fatal(err)
	}
	if !scheduler.Cancel("queued") {
		t.Fatal("queued task was not cancellable")
	}
	close(release)
	select {
	case value := <-result:
		if !errors.Is(value.Err, context.Canceled) {
			t.Fatalf("queued result error = %v, want context.Canceled", value.Err)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled task did not finish")
	}
}

func TestSchedulerRejectsDuplicateJobIDsWithoutReplacingCancellationOwnership(t *testing.T) {
	scheduler := New(1, 1)
	defer scheduler.Close()
	started := make(chan struct{})
	release := make(chan struct{})
	first, err := scheduler.Submit(context.Background(), "same", func(ctx context.Context) (any, error) {
		close(started)
		select {
		case <-release:
			return "first", nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	<-started
	if _, err := scheduler.Submit(context.Background(), "same", func(context.Context) (any, error) { return "second", nil }); !errors.Is(err, ErrDuplicate) {
		t.Fatalf("duplicate admission error = %v, want ErrDuplicate", err)
	}
	if !scheduler.Cancel("same") {
		t.Fatal("original job cancellation ownership was lost")
	}
	select {
	case result := <-first:
		if !errors.Is(result.Err, context.Canceled) {
			t.Fatalf("original job result = %#v, want context.Canceled", result)
		}
	case <-time.After(time.Second):
		t.Fatal("original job did not observe cancellation")
	}
	close(release)
}

func TestSchedulerCloseWithTimeoutBoundsUncooperativeTask(t *testing.T) {
	scheduler := New(1, 1)
	release := make(chan struct{})
	started := make(chan struct{})
	result, err := scheduler.Submit(context.Background(), "blocked", func(context.Context) (any, error) {
		close(started)
		<-release
		return nil, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	<-started
	if scheduler.CloseWithTimeout(20 * time.Millisecond) {
		t.Fatal("close reported success while task was blocked")
	}
	close(release)
	select {
	case <-result:
	case <-time.After(time.Second):
		t.Fatal("blocked task did not finish after release")
	}
	if !scheduler.CloseWithTimeout(time.Second) {
		t.Fatal("close did not observe worker completion after task release")
	}
}

func TestSchedulerCloseDoesNotRunQueuedTask(t *testing.T) {
	scheduler := New(1, 1)
	running := make(chan struct{})
	release := make(chan struct{})
	if _, err := scheduler.Submit(context.Background(), "running", func(context.Context) (any, error) {
		close(running)
		<-release
		return nil, nil
	}); err != nil {
		t.Fatal(err)
	}
	<-running
	queuedRan := make(chan struct{}, 1)
	queued, err := scheduler.Submit(context.Background(), "queued", func(context.Context) (any, error) {
		queuedRan <- struct{}{}
		return nil, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	closed := make(chan struct{})
	go func() {
		scheduler.Close()
		close(closed)
	}()
	select {
	case <-scheduler.stop:
	case <-time.After(time.Second):
		t.Fatal("scheduler close did not begin")
	}
	close(release)
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("scheduler close did not finish")
	}
	select {
	case <-queuedRan:
		t.Fatal("queued task ran during scheduler shutdown")
	case <-time.After(20 * time.Millisecond):
	}
	select {
	case result := <-queued:
		if !errors.Is(result.Err, context.Canceled) {
			t.Fatalf("queued shutdown result = %#v", result)
		}
	case <-time.After(time.Second):
		t.Fatal("queued shutdown result was not delivered")
	}
}

func TestCloseCancelsAnonymousTask(t *testing.T) {
	s := New(1, 1)
	started := make(chan struct{})
	result, err := s.Submit(context.Background(), "", func(ctx context.Context) (any, error) {
		close(started)
		<-ctx.Done()
		return nil, ctx.Err()
	})
	if err != nil {
		t.Fatal(err)
	}
	<-started
	if !s.CloseWithTimeout(time.Second) {
		t.Fatal("anonymous task survived shutdown")
	}
	if got := <-result; got.Err != context.Canceled {
		t.Fatalf("result: %v", got.Err)
	}
}
