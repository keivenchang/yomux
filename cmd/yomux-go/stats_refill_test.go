// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/keivenchang/yomux/internal/stats"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

func TestRefillTerminalStatusWaitsForLeaseRelease(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	lock, err := os.OpenFile(filepath.Join(root, "owner.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		t.Fatal(err)
	}
	if err := writeRefillStatus(root, map[string]any{"state": "succeeded"}); err != nil {
		t.Fatal(err)
	}
	s := &server{refillRoot: root}
	check := func(want string) {
		t.Helper()
		out := httptest.NewRecorder()
		s.statsRefill(out, httptest.NewRequest("GET", "/api/stats-refill", nil))
		var status map[string]any
		if err := json.Unmarshal(out.Body.Bytes(), &status); err != nil {
			t.Fatal(err)
		}
		if status["state"] != want {
			t.Fatalf("state=%v, want %s", status["state"], want)
		}
	}
	check("running")
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	check("succeeded")
}

func TestRefillHostSingletonReloadProgressFailureAndRetry(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	var jobs atomic.Int64
	started := make(chan struct{}, 3)
	release := make(chan bool)
	run := func(ctx context.Context, end time.Time, progress func(string)) (stats.AgentHistoryRefillResult, error) {
		jobs.Add(1)
		started <- struct{}{}
		if time.Since(end) > time.Second {
			t.Error("window end was not frozen")
		}
		select {
		case success := <-release:
			if !success {
				return stats.AgentHistoryRefillResult{}, errors.New("injected failure")
			}
			progress("Rebuilding charts")
			return stats.AgentHistoryRefillResult{Gaps: []string{"opencode"}}, nil
		case <-ctx.Done():
			return stats.AgentHistoryRefillResult{}, ctx.Err()
		}
	}
	s := &server{events: newEventBroker(), refillRoot: root, refillRun: run, operationState: operationState{operationsPath: filepath.Join(root, "operations.json"), operationsJournal: filepath.Join(root, "journal.jsonl")}}
	other := &server{events: newEventBroker(), refillRoot: root, refillRun: run}
	request := func(s *server, method string) map[string]any {
		t.Helper()
		out := httptest.NewRecorder()
		s.statsRefill(out, httptest.NewRequest(method, "/api/stats-refill", nil))
		if out.Code != 200 && out.Code != 202 {
			t.Fatalf("status %d: %s", out.Code, out.Body.String())
		}
		var result map[string]any
		if err := json.Unmarshal(out.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	first := request(s, "POST")
	<-started
	end := first["window_end"].(float64)
	start := first["window_start"].(float64)
	if end-start != 86400 {
		t.Fatalf("window=%v", first)
	}
	for _, instance := range []*server{s, other} {
		if attach := request(instance, "POST"); attach["operation_id"] != first["operation_id"] {
			t.Fatalf("different job: %v", attach)
		}
		if reopened := request(instance, "GET"); reopened["state"] != "running" {
			t.Fatalf("reopen lost job: %v", reopened)
		}
	}
	if err := s.refillProgress(first["operation_id"].(string), "Rebuilding charts"); err != nil {
		t.Fatal(err)
	}
	op := httptest.NewRecorder()
	s.operation(op, httptest.NewRequest("GET", "/api/operations/"+first["operation_id"].(string), nil))
	var receipt map[string]any
	if err := json.Unmarshal(op.Body.Bytes(), &receipt); err != nil {
		t.Fatal(err)
	}
	if receipt["state"] != "running" || receipt["operation"].(map[string]any)["progress"].(map[string]any)["phase"] != "Rebuilding charts" {
		t.Fatalf("stale receipt: %s", op.Body.String())
	}
	if jobs.Load() != 1 {
		t.Fatalf("started %d jobs", jobs.Load())
	}
	release <- true
	wait := func(want string) map[string]any {
		t.Helper()
		deadline := time.After(3 * time.Second)
		ticker := time.NewTicker(time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-deadline:
				t.Fatal("job did not settle")
			case <-ticker.C:
				if got := request(s, "GET"); got["state"] == want {
					return got
				}
			}
		}
	}
	completed := wait("succeeded")
	if completed["message"] != "Agent history refilled with gaps. OpenCode history is unavailable." {
		t.Fatalf("message=%v", completed)
	}
	request(s, "POST")
	<-started
	release <- false
	failed := wait("failed")
	if failed["message"] != "Couldn’t refill agent history. Your previous data is unchanged." {
		t.Fatalf("failure=%v", failed)
	}
	request(s, "POST")
	<-started
	release <- true
	wait("succeeded")
	if jobs.Load() != 3 {
		t.Fatalf("retry started %d jobs", jobs.Load())
	}
}

func TestRefillEndpointRequiresAdminAndRejectsOtherMethods(t *testing.T) {
	s := &server{events: newEventBroker(), refillRoot: t.TempDir()}
	out := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.statsRefill), "admin").ServeHTTP(out, httptest.NewRequest("POST", "/api/stats-refill", nil))
	if out.Code != 401 {
		t.Fatalf("unauthenticated response=%d", out.Code)
	}
	out = httptest.NewRecorder()
	s.statsRefill(out, httptest.NewRequest("DELETE", "/api/stats-refill", nil))
	if out.Code != 405 {
		t.Fatalf("method response=%d", out.Code)
	}
}
