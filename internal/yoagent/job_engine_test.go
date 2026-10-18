// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"context"
	"encoding/json"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

type fixtureJobRuntime struct {
	observation JobObservation
	execution   JobExecution
	observeErr  error
	executeErr  error
	executes    atomic.Int32
}

func (runtime *fixtureJobRuntime) Observe(context.Context, map[string]any) (JobObservation, error) {
	return runtime.observation, runtime.observeErr
}

func (runtime *fixtureJobRuntime) Execute(context.Context, map[string]any) (JobExecution, error) {
	runtime.executes.Add(1)
	return runtime.execution, runtime.executeErr
}

func createEngineTestJob(t *testing.T, store *Store, payload map[string]any) map[string]any {
	t.Helper()
	job, err := BuildJob(payload, []string{"agent-a", "agent-b"}, "operator", time.Unix(100, 0))
	if err != nil {
		t.Fatal(err)
	}
	created, inserted, err := store.CreateJob(context.Background(), job)
	if err != nil || !inserted {
		t.Fatalf("create job: inserted=%v err=%v", inserted, err)
	}
	return created
}

func newFixtureJobEngine(t *testing.T, store *Store, runtime JobRuntime) (*JobEngine, *[]string) {
	t.Helper()
	var events []string
	engine, err := NewJobEngine(store, runtime, func(event string, _ map[string]any) error {
		events = append(events, event)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	engine.now = func() time.Time { return time.Unix(100, 0) }
	return engine, &events
}

func TestJobEngineClaimsAndFiresReadyJobOnce(t *testing.T) {
	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	job := createEngineTestJob(t, store, map[string]any{"type": "notify_session_idle", "session": "agent-a", "quiet_seconds": 0})
	runtime := &fixtureJobRuntime{
		observation: JobObservation{State: "idle", Ready: true},
		execution:   JobExecution{Attempted: true, Succeeded: true, Result: map[string]any{"ok": true}},
	}
	engine, events := newFixtureJobEngine(t, store, runtime)
	if err := engine.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	stored, err := store.LoadJob(context.Background(), text(job["id"]))
	if err != nil || stored["status"] != "fired" || runtime.executes.Load() != 1 {
		t.Fatalf("job=%#v executes=%d err=%v", stored, runtime.executes.Load(), err)
	}
	if len(*events) != 1 || (*events)[0] != "yoagent_job_fired" {
		t.Fatalf("events=%v", *events)
	}
	if err := engine.RunOnce(context.Background()); err != nil || runtime.executes.Load() != 1 {
		t.Fatalf("completed job was replayed: executes=%d err=%v", runtime.executes.Load(), err)
	}
}

type blockingJobRuntime struct {
	started chan struct{}
	release chan struct{}
}

func (runtime *blockingJobRuntime) Observe(context.Context, map[string]any) (JobObservation, error) {
	return JobObservation{State: "idle", Ready: true}, nil
}

func (runtime *blockingJobRuntime) Execute(ctx context.Context, _ map[string]any) (JobExecution, error) {
	close(runtime.started)
	select {
	case <-runtime.release:
		return JobExecution{Attempted: true, Succeeded: true}, nil
	case <-ctx.Done():
		return JobExecution{}, ctx.Err()
	}
}

func TestJobEngineDoesNotRecoverAnotherEnginesActiveExecution(t *testing.T) {
	directory := t.TempDir()
	first, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	second, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	job := createEngineTestJob(t, first, map[string]any{"type": "notify_session_idle", "session": "agent-a", "quiet_seconds": 0})
	runtime := &blockingJobRuntime{started: make(chan struct{}), release: make(chan struct{})}
	owner, _ := newFixtureJobEngine(t, first, runtime)
	contender, _ := newFixtureJobEngine(t, second, &fixtureJobRuntime{})
	done := make(chan error, 1)
	go func() { done <- owner.RunOnce(context.Background()) }()
	<-runtime.started
	defer func() {
		close(runtime.release)
		if err := <-done; err != nil {
			t.Errorf("owning engine failed: %v", err)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if err := contender.RunOnce(ctx); err != nil && !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	stored, err := first.LoadJob(context.Background(), text(job["id"]))
	if err != nil || stored["status"] != "firing" {
		t.Fatalf("second engine recovered an active execution: status=%v err=%v", stored["status"], err)
	}
}

func TestJobEngineBoundsLoopAndWaitsUntilNextRun(t *testing.T) {
	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	job := createEngineTestJob(t, store, map[string]any{"type": "loop_send", "session": "agent-a", "text": "status", "interval_seconds": 5, "max_runs": 2})
	runtime := &fixtureJobRuntime{observation: JobObservation{State: "idle", Ready: true}, execution: JobExecution{Attempted: true, Succeeded: true}}
	engine, _ := newFixtureJobEngine(t, store, runtime)
	if err := engine.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	stored, err := store.LoadJob(context.Background(), text(job["id"]))
	if err != nil || stored["status"] != "queued" || integer(stored["run_count"]) != 1 || number(stored["next_run_ts"]) != 105 {
		t.Fatalf("first loop run=%#v err=%v", stored, err)
	}
	if err := engine.RunOnce(context.Background()); err != nil || runtime.executes.Load() != 1 {
		t.Fatalf("loop ran before its interval: executes=%d err=%v", runtime.executes.Load(), err)
	}
	engine.now = func() time.Time { return time.Unix(106, 0) }
	if err := engine.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	stored, err = store.LoadJob(context.Background(), text(job["id"]))
	if err != nil || stored["status"] != "fired" || integer(stored["run_count"]) != 2 || runtime.executes.Load() != 2 {
		t.Fatalf("final loop run=%#v executes=%d err=%v", stored, runtime.executes.Load(), err)
	}
}

func TestJobEngineTimesOutAndRecoversIndeterminateFiringWithoutReplay(t *testing.T) {
	for _, test := range []struct {
		name   string
		status string
	}{
		{name: "timeout", status: "queued"},
		{name: "firing recovery", status: "firing"},
	} {
		t.Run(test.name, func(t *testing.T) {
			store, err := NewStore(t.TempDir())
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			job := createEngineTestJob(t, store, map[string]any{"type": "notify_session_idle", "session": "agent-a", "quiet_seconds": 0})
			if test.status == "queued" {
				job["timeout_ts"] = float64(99)
				if err := store.SaveJob(context.Background(), job); err != nil {
					t.Fatal(err)
				}
			} else {
				job["status"] = "firing"
				job["firing_at"] = time.Unix(99, 0).UTC().Format(time.RFC3339Nano)
				encoded, err := json.Marshal(job)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := store.db.ExecContext(context.Background(), `UPDATE yoagent_jobs SET status='firing',job_json=? WHERE job_id=?`, string(encoded), text(job["id"])); err != nil {
					t.Fatal(err)
				}
			}
			runtime := &fixtureJobRuntime{observation: JobObservation{State: "idle", Ready: true}, execution: JobExecution{Attempted: true, Succeeded: true}}
			engine, _ := newFixtureJobEngine(t, store, runtime)
			if err := engine.RunOnce(context.Background()); err != nil {
				t.Fatal(err)
			}
			stored, err := store.LoadJob(context.Background(), text(job["id"]))
			if err != nil || stored["status"] != "timed_out" && stored["status"] != "failed" || runtime.executes.Load() != 0 {
				t.Fatalf("reconciled job=%#v executes=%d err=%v", stored, runtime.executes.Load(), err)
			}
		})
	}
}

func TestJobEngineRetriesOnlyWhenActionWasNotAttempted(t *testing.T) {
	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	job := createEngineTestJob(t, store, map[string]any{"type": "notify_session_idle", "session": "agent-a", "quiet_seconds": 0})
	runtime := &fixtureJobRuntime{observation: JobObservation{State: "idle", Ready: true}, executeErr: errors.New("preflight changed")}
	engine, _ := newFixtureJobEngine(t, store, runtime)
	if err := engine.RunOnce(context.Background()); err == nil {
		t.Fatal("preflight failure was discarded")
	}
	stored, err := store.LoadJob(context.Background(), text(job["id"]))
	if err != nil || stored["status"] != "queued" || runtime.executes.Load() != 1 {
		t.Fatalf("unattempted action should return to queue: %#v executes=%d err=%v", stored, runtime.executes.Load(), err)
	}
}

func TestJobEnginePersistsReasonWhenAttemptedActionFailsWithoutError(t *testing.T) {
	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	job := createEngineTestJob(t, store, map[string]any{"type": "notify_session_idle", "session": "agent-a", "quiet_seconds": 0})
	runtime := &fixtureJobRuntime{
		observation: JobObservation{State: "idle", Ready: true},
		execution:   JobExecution{Attempted: true, Succeeded: false, Result: map[string]any{"reason": "verified sends are unavailable"}},
	}
	engine, _ := newFixtureJobEngine(t, store, runtime)
	if err := engine.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	stored, err := store.LoadJob(context.Background(), text(job["id"]))
	if err != nil || stored["status"] != "failed" || stored["failure_reason"] != "verified sends are unavailable" {
		t.Fatalf("failed action result was not visible: job=%#v err=%v", stored, err)
	}
}
