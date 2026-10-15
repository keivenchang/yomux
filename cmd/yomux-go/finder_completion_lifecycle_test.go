// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"runtime"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/work"
)

func TestFinderAcceptedCompletionRetainsLifetimeUntilTerminalDelivery(t *testing.T) {
	jobs := work.New(1, 1)
	defer jobs.Close()
	s := &server{finderJobs: jobs, events: newEventBroker(), operationState: operationState{operations: map[string]*goOperation{}}}
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	unblock := func() { once.Do(func() { close(release) }) }
	defer unblock()
	response := httptest.NewRecorder()
	s.enqueueFinderJSON(response, context.Background(), "fixture", "fixture", "/fixture", func(context.Context) (map[string]any, *finder.Error) {
		close(entered)
		<-release
		return map[string]any{"ok": true}, nil
	})
	if response.Code != http.StatusAccepted {
		t.Fatalf("status %d", response.Code)
	}
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("task did not start")
	}
	stopped := make(chan struct{})
	go func() { s.stopBackgroundWork(); close(stopped) }()
	deadline := time.NewTimer(time.Second)
	defer deadline.Stop()
	for !s.backgroundWorkAdmissionClosed() {
		select {
		case <-deadline.C:
			t.Fatal("stop did not close admission")
		default:
			runtime.Gosched()
		}
	}
	// Admission is now closed, and the accepted task remains explicitly held.
	select {
	case <-stopped:
		t.Fatal("accepted completion has no shutdown lifetime owner")
	case <-time.After(100 * time.Millisecond):
	}
	unblock()
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("terminal completion did not drain")
	}
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	for id, record := range s.operations {
		if !operationIsTerminal(record) || !record.EventPublished {
			t.Fatalf("operation %s not durably terminal at drain", id)
		}
	}
}
