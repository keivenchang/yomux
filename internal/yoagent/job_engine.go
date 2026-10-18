// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
)

type JobObservation struct {
	State  string
	Ready  bool
	Fields map[string]any
}

type JobExecution struct {
	Attempted bool
	Succeeded bool
	Result    map[string]any
}

type JobRuntime interface {
	Observe(context.Context, map[string]any) (JobObservation, error)
	Execute(context.Context, map[string]any) (JobExecution, error)
}

type JobCycleRuntime interface {
	BeginCycle(context.Context, []map[string]any) error
}

type JobEventPublisher func(string, map[string]any) error

type JobEngine struct {
	store   *Store
	runtime JobRuntime
	publish JobEventPublisher
	now     func() time.Time
	cycleMu chan struct{}
}

func NewJobEngine(store *Store, runtime JobRuntime, publish JobEventPublisher) (*JobEngine, error) {
	if store == nil || runtime == nil || publish == nil {
		return nil, errors.New("YO!agent job engine requires a store, runtime, and event publisher")
	}
	return &JobEngine{store: store, runtime: runtime, publish: publish, now: time.Now, cycleMu: make(chan struct{}, 1)}, nil
}

// RunOnce reconciles queued work with a live runtime. A firing job found after
// restart is failed as indeterminate: replaying a send whose completion was
// not committed could duplicate an external action.
func (engine *JobEngine) RunOnce(ctx context.Context) (resultErr error) {
	select {
	case engine.cycleMu <- struct{}{}:
		defer func() { <-engine.cycleMu }()
	case <-ctx.Done():
		return ctx.Err()
	}
	// Ports sharing this canonical database must also share execution ownership.
	// Holding the lock through Execute distinguishes a live send from crash recovery;
	// the kernel releases it on process exit so abandoned firing jobs remain recoverable.
	lease, err := atomicfile.AcquireLock(ctx, engine.store.database+".worker.lock")
	if err != nil {
		return fmt.Errorf("acquire YO!agent job worker ownership: %w", err)
	}
	defer func() { resultErr = errors.Join(resultErr, lease.Close()) }()
	jobs, _, err := engine.store.LoadJobs(ctx)
	if err != nil {
		return fmt.Errorf("load YO!agent jobs: %w", err)
	}
	var observationPrepErr error
	if cycleRuntime, ok := engine.runtime.(JobCycleRuntime); ok && len(jobs) > 0 {
		observationPrepErr = cycleRuntime.BeginCycle(ctx, jobs)
	}
	var failures []error
	for _, job := range jobs {
		if err := ctx.Err(); err != nil {
			return errors.Join(errors.Join(failures...), err)
		}
		status := text(job["status"])
		id := text(job["id"])
		if id == "" {
			failures = append(failures, errors.New("YO!agent job has no ID"))
			continue
		}
		if status == "firing" {
			job["status"] = "failed"
			job["failed_at"] = engine.now().UTC().Format(time.RFC3339Nano)
			job["failure_reason"] = "execution outcome was not committed before restart"
			if err := engine.transitionAndPublish(ctx, id, []string{"firing"}, job, "yoagent_job_failed"); err != nil {
				failures = append(failures, err)
			}
			continue
		}
		if status != "queued" {
			continue
		}
		if engine.expired(job) {
			job["status"] = "timed_out"
			job["timed_out_at"] = engine.now().UTC().Format(time.RFC3339Nano)
			if err := engine.transitionAndPublish(ctx, id, []string{"queued"}, job, "yoagent_job_timed_out"); err != nil {
				failures = append(failures, err)
			}
			continue
		}
		if observationPrepErr != nil {
			failures = append(failures, fmt.Errorf("prepare observation for YO!agent job %s: %w", id, observationPrepErr))
			continue
		}
		observation, observeErr := engine.runtime.Observe(ctx, job)
		if observeErr != nil {
			failures = append(failures, fmt.Errorf("observe YO!agent job %s: %w", id, observeErr))
			continue
		}
		if strings.TrimSpace(observation.State) == "missing" {
			job["status"] = "failed"
			job["failed_at"] = engine.now().UTC().Format(time.RFC3339Nano)
			job["failure_reason"] = "target session is missing"
			if err := engine.transitionAndPublish(ctx, id, []string{"queued"}, job, "yoagent_job_failed"); err != nil {
				failures = append(failures, err)
			}
			continue
		}
		ready, transitionErr := engine.recordObservation(ctx, job, observation)
		if transitionErr != nil {
			failures = append(failures, fmt.Errorf("record YO!agent job %s observation: %w", id, transitionErr))
			continue
		}
		if !ready {
			continue
		}
		job["status"] = "firing"
		job["firing_at"] = engine.now().UTC().Format(time.RFC3339Nano)
		claimed, err := engine.store.TransitionJob(ctx, id, []string{"queued"}, job)
		if err != nil {
			failures = append(failures, fmt.Errorf("claim YO!agent job %s: %w", id, err))
			continue
		}
		if !claimed {
			continue
		}
		execution, executeErr := engine.runtime.Execute(ctx, job)
		if !execution.Attempted {
			job["status"] = "queued"
			delete(job, "firing_at")
			job["last_observed_state"] = map[string]any{"state": observation.State, "ready": false, "since_ts": float64(engine.now().UnixNano()) / 1e9}
			if err := engine.transitionAndPublish(ctx, id, []string{"firing"}, job, "yoagent_job_waiting"); err != nil {
				failures = append(failures, err)
			}
			if executeErr != nil {
				failures = append(failures, fmt.Errorf("execute YO!agent job %s before action attempt: %w", id, executeErr))
			}
			continue
		}
		if executeErr != nil || !execution.Succeeded {
			job["status"] = "failed"
			job["failed_at"] = engine.now().UTC().Format(time.RFC3339Nano)
			if executeErr != nil {
				job["failure_reason"] = executeErr.Error()
			} else if reason := text(object(execution.Result)["reason"]); reason != "" {
				job["failure_reason"] = reason
			} else {
				job["failure_reason"] = "job action did not succeed"
			}
			if execution.Result != nil {
				job["result"] = execution.Result
			}
			if err := engine.transitionAndPublish(ctx, id, []string{"firing"}, job, "yoagent_job_failed"); err != nil {
				failures = append(failures, err)
			}
			if executeErr != nil {
				failures = append(failures, fmt.Errorf("execute YO!agent job %s: %w", id, executeErr))
			}
			continue
		}
		if execution.Result != nil {
			job["result"] = execution.Result
		}
		if text(job["type"]) == "loop_send" {
			runCount := integer(job["run_count"]) + 1
			job["run_count"] = runCount
			maxRuns := integer(job["max_runs"])
			if maxRuns > runCount {
				predicate := object(job["predicate"])
				interval := numberOr(predicate["interval_seconds"], 30)
				job["status"] = "queued"
				job["next_run_ts"] = float64(engine.now().UnixNano())/1e9 + interval
				delete(job, "firing_at")
				job["last_observed_state"] = map[string]any{}
				if err := engine.transitionAndPublish(ctx, id, []string{"firing"}, job, "yoagent_job_looped"); err != nil {
					failures = append(failures, err)
				}
				continue
			}
		}
		job["status"] = "fired"
		job["fired_at"] = engine.now().UTC().Format(time.RFC3339Nano)
		if err := engine.transitionAndPublish(ctx, id, []string{"firing"}, job, "yoagent_job_fired"); err != nil {
			failures = append(failures, err)
		}
	}
	return errors.Join(failures...)
}

func (engine *JobEngine) expired(job map[string]any) bool {
	deadline := number(job["timeout_ts"])
	return deadline > 0 && float64(engine.now().UnixNano())/1e9 >= deadline
}

func (engine *JobEngine) recordObservation(ctx context.Context, job map[string]any, observation JobObservation) (bool, error) {
	now := float64(engine.now().UnixNano()) / 1e9
	predicate := object(job["predicate"])
	previous := object(job["last_observed_state"])
	state := strings.TrimSpace(observation.State)
	ready := observation.Ready
	since := number(previous["since_ts"])
	if previous["state"] != state || previous["ready"] != ready || since <= 0 {
		since = now
	}
	seenWorking := previous["seen_working"] == true || state == "working"
	fields := map[string]any{"state": state, "ready": ready, "since_ts": since, "observed_ts": now, "seen_working": seenWorking}
	for key, value := range observation.Fields {
		fields[key] = value
	}
	job["last_observed_state"] = fields
	jobReady := ready && now-since >= mathMaxZero(number(predicate["quiet_seconds"]))
	switch text(predicate["type"]) {
	case "session_idle", "session_needs_input", "session_blocked", "all_idle", "all_calm":
	case "session_done_after_working":
		jobReady = jobReady && seenWorking
	case "interval":
		jobReady = ready && now >= number(job["next_run_ts"])
	default:
		return false, fmt.Errorf("unsupported YO!agent job predicate %q", text(predicate["type"]))
	}
	if _, err := engine.store.TransitionJob(ctx, text(job["id"]), []string{"queued"}, job); err != nil {
		return false, err
	}
	return jobReady, nil
}

func (engine *JobEngine) transitionAndPublish(ctx context.Context, id string, expected []string, job map[string]any, event string) error {
	changed, err := engine.store.TransitionJob(ctx, id, expected, job)
	if err != nil {
		return fmt.Errorf("persist YO!agent job %s: %w", id, err)
	}
	if !changed {
		return nil
	}
	if err := engine.publish(event, job); err != nil {
		return fmt.Errorf("publish YO!agent job %s event: %w", id, err)
	}
	return nil
}

func mathMaxZero(value float64) float64 {
	if value < 0 {
		return 0
	}
	return value
}
