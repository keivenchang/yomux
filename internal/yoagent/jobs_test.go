// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"context"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestBuildJobContractAndFailClosedValidation(t *testing.T) {
	now := time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC)
	known := []string{"agent-a", "agent-b"}
	cases := []struct {
		name     string
		payload  map[string]any
		wantType string
		wantErr  bool
	}{
		{name: "default idle notification", payload: map[string]any{"session": "agent-a"}, wantType: "notify_session_idle"},
		{name: "needs input alias", payload: map[string]any{"type": "notify_needs_input", "session": "agent-a"}, wantType: "notify_session_needs_input"},
		{name: "roster idle", payload: map[string]any{"type": "all_idle_summary", "roster": []any{"agent-a", "agent-b", "agent-a"}}, wantType: "notify_all_idle"},
		{name: "send alias honors explicit confirmation", payload: map[string]any{"type": "wait_then_run", "session": "agent-a", "text": "inspect status", "requires_confirmation": true}, wantType: "wait_then_send"},
		{name: "loop bounds", payload: map[string]any{"type": "loop_send", "session": "agent-a", "text": "inspect status", "interval_seconds": 10.0, "max_runs": 3}, wantType: "loop_send"},
		{name: "loop default max runs", payload: map[string]any{"type": "loop_send", "session": "agent-a", "text": "inspect status"}, wantType: "loop_send"},
		{name: "risky prompt requires confirmation and redacts credential", payload: map[string]any{"type": "wait_then_send", "session": "agent-a", "text": "review token=abc123"}, wantType: "wait_then_send"},
		{name: "unknown session", payload: map[string]any{"session": "foreign"}, wantErr: true},
		{name: "missing prompt", payload: map[string]any{"type": "wait_then_send", "session": "agent-a"}, wantErr: true},
		{name: "out of range loop", payload: map[string]any{"type": "loop_send", "session": "agent-a", "text": "inspect status", "interval_seconds": 1.0, "max_runs": 3}, wantErr: true},
		{name: "unknown job", payload: map[string]any{"type": "execute_shell", "session": "agent-a"}, wantErr: true},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			job, err := BuildJob(test.payload, known, "admin", now)
			if (err != nil) != test.wantErr {
				t.Fatalf("BuildJob error = %v, wantErr %v", err, test.wantErr)
			}
			if err != nil {
				return
			}
			if job["type"] != test.wantType || job["created_ts"] != float64(now.UnixNano())/1e9 {
				t.Fatalf("job type/time = %v/%v", job["type"], job["created_ts"])
			}
			if test.name == "send alias honors explicit confirmation" || test.name == "risky prompt requires confirmation and redacts credential" {
				if job["status"] != "pending_confirmation" || job["confirm_required"] != true {
					t.Fatalf("send job was not confirmation-gated: %#v", job)
				}
			}
			if test.name == "risky prompt requires confirmation and redacts credential" && object(job["action"])["text"] != "review token=<redacted>" {
				t.Fatalf("risky job retained credential text: %#v", job["action"])
			}
			if test.name == "loop default max runs" && job["max_runs"] != 10 {
				t.Fatalf("default loop max runs = %v, want 10", job["max_runs"])
			}
		})
	}
}

func TestStoreCreateJobDeduplicatesActiveJobsAtomically(t *testing.T) {
	store, err := NewStore(filepath.Join(t.TempDir(), "yoagent"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	base := map[string]any{
		"id": "yj_first", "type": "notify_session_idle", "target": map[string]any{"session": "agent-a"},
		"predicate": map[string]any{"type": "session_idle"}, "action": map[string]any{"type": "notify_user"},
		"status": "queued", "created_ts": 1.0, "idempotency_key": "same-request",
	}
	const workers = 8
	var wait sync.WaitGroup
	var mu sync.Mutex
	created := 0
	errs := make([]error, 0)
	for index := 0; index < workers; index++ {
		wait.Add(1)
		go func(index int) {
			defer wait.Done()
			job := make(map[string]any, len(base))
			for key, value := range base {
				job[key] = value
			}
			job["id"] = "yj_worker_" + string(rune('a'+index))
			_, inserted, createErr := store.CreateJob(context.Background(), job)
			mu.Lock()
			defer mu.Unlock()
			if createErr != nil {
				errs = append(errs, createErr)
			}
			if inserted {
				created++
			}
		}(index)
	}
	wait.Wait()
	if len(errs) != 0 || created != 1 {
		t.Fatalf("concurrent duplicate creation = created %d, errors %v", created, errs)
	}
	jobs, _, err := store.LoadJobs(context.Background())
	if err != nil || len(jobs) != 1 {
		t.Fatalf("persisted jobs = %d, %v", len(jobs), err)
	}
}
