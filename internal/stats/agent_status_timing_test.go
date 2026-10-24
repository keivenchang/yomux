// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package stats

import (
	"context"
	"database/sql"
	"errors"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

type agentStatusScheduleClock struct {
	collectorClock
	timers chan time.Duration
}

type agentStatusTimingWriter struct {
	BatchWriter
	t *testing.T
}

func (w agentStatusTimingWriter) AppendBatch(ctx context.Context, batch AppendBatch) (AppendResult, error) {
	result, err := w.BatchWriter.AppendBatch(ctx, batch)
	if err != nil {
		w.t.Errorf("timing fixture append: %v", err)
	}
	return result, err
}

func (c *agentStatusScheduleClock) NewTimer(delay time.Duration) HostCollectorTimer {
	c.timers <- delay
	return &neverTimer{ch: make(chan time.Time)}
}

func TestAgentStatusCollectorSchedulesFromCycleStart(t *testing.T) {
	clock := &agentStatusScheduleClock{collectorClock: collectorClock{now: time.Unix(100, 0)}, timers: make(chan time.Duration, 1)}
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		clock.advance(250 * time.Millisecond)
		return nil, nil
	}, nil, &recordingBatchWriter{}, clock, AgentStatusCollectorConfig{})
	if err := collector.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer collector.Stop()
	select {
	case delay := <-clock.timers:
		if delay != 9750*time.Millisecond {
			t.Fatalf("next scan waits %s after a 250ms scan, want 9.75s", delay)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("collector did not schedule its next scan")
	}
}

func TestAgentStatusRemainsFreshDuringBoundedSuccessorScan(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO observations VALUES ('status-working','agent_status','host',100,'epoch',7,'{"agents":[{"id":"worker","state":"run"}]}')`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(119, 800_000_000)})
	if err != nil || !result.Available || len(result.Agents) != 1 {
		t.Fatalf("working sample disappeared during a 9.8s successor scan: %+v, err=%v", result, err)
	}
	result, err = database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(121, 0)})
	if err != nil || result.Available || result.Unavailable == nil || result.Unavailable.Reason != "agent-status-stale" {
		t.Fatalf("missed successor scan retained working status: %+v, err=%v", result, err)
	}
}

func TestStatsServiceRetriesUnavailableAgentStatusAtBoundedCadence(t *testing.T) {
	now := time.Unix(100, 0)
	calls := 0
	service := NewStatsService(nil, StatsServiceConfig{Now: func() time.Time { return now }, AgentStatus: func(context.Context) (AgentStatusSnapshot, error) {
		calls++
		if calls == 1 {
			return AgentStatusSnapshot{Unavailable: &AgentStatusQueryUnavailable{Reason: "agent-status-stale"}}, nil
		}
		return AgentStatusSnapshot{Available: true}, nil
	}})
	if err := service.RefreshAgentStatus(context.Background()); err != nil {
		t.Fatal(err)
	}
	now = now.Add(time.Second)
	if err := service.RefreshAgentStatusIfDue(context.Background()); err != nil {
		t.Fatal(err)
	}
	result, err := service.AgentStatus(context.Background())
	if err != nil || !result.Available || calls != 2 {
		t.Fatalf("unavailable status did not recover after bounded retry: calls=%d snapshot=%+v err=%v", calls, result, err)
	}
}

func TestAgentStatusCommitSignalsBeforeAttentionMaintenance(t *testing.T) {
	notified := false
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) { return nil, nil }, nil, &recordingBatchWriter{}, &collectorClock{now: time.Unix(100, 0)}, AgentStatusCollectorConfig{
		Committed: func() { notified = true },
		AttentionCommitted: func([]string) error {
			if !notified {
				t.Error("durable status publication waited for attention-store maintenance")
			}
			return nil
		},
	})
	collector.RunOnce(context.Background())
}

func TestStatsServiceExpiresCachedAgentStatusWithoutReadingSQLite(t *testing.T) {
	now := time.Unix(111, 0)
	calls := 0
	service := NewStatsService(nil, StatsServiceConfig{Now: func() time.Time { return now }, AgentStatus: func(context.Context) (AgentStatusSnapshot, error) {
		calls++
		return AgentStatusSnapshot{Available: true, Freshness: &AgentStatusFreshness{ObservedAt: 100, ExpiresAt: 120}, Agents: []any{"worker"}, Payload: map[string]any{"working": true}, Counts: map[string]any{"running": 1}}, nil
	}})
	if err := service.RefreshAgentStatus(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !service.agentStatusNext.Equal(time.Unix(120, 0)) {
		t.Fatalf("next refresh %s exceeds cached expiry", service.agentStatusNext)
	}
	now = time.Unix(120, 1)
	result, err := service.AgentStatus(context.Background())
	if err != nil || result.Available || len(result.Agents) != 0 || len(result.Counts) != 0 || len(result.Payload) != 0 || result.Unavailable == nil || result.Unavailable.Reason != "agent-status-stale" || calls != 1 {
		t.Fatalf("stalled owner served working or performed a request-side read: %+v calls=%d err=%v", result, calls, err)
	}
	if !service.agentStatusValue.Available || service.agentStatusValue.Freshness.AgeSeconds != 0 {
		t.Fatal("request mutated the shared cached snapshot")
	}
}

func TestStatsServiceCommitDuringReadCannotLoseInvalidation(t *testing.T) {
	started, release := make(chan struct{}), make(chan struct{})
	calls := 0
	service := NewStatsService(nil, StatsServiceConfig{AgentStatus: func(context.Context) (AgentStatusSnapshot, error) {
		calls++
		if calls == 1 {
			close(started)
			<-release
			return AgentStatusSnapshot{Unavailable: &AgentStatusQueryUnavailable{Reason: "agent-status-stale"}}, nil
		}
		return AgentStatusSnapshot{Available: true}, nil
	}})
	done := make(chan error, 1)
	go func() { done <- service.RefreshAgentStatus(context.Background()) }()
	<-started
	service.InvalidateAgentStatus()
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if err := service.RefreshAgentStatusIfDue(context.Background()); err != nil {
		t.Fatal(err)
	}
	result, err := service.AgentStatus(context.Background())
	if err != nil || !result.Available || calls != 2 {
		t.Fatalf("newly committed status waited behind stale read: %+v calls=%d err=%v", result, calls, err)
	}
}

func TestAgentStatusCollectorCommitSignalRequiresDurableBatch(t *testing.T) {
	clock := &collectorClock{now: time.Unix(100, 0)}
	writer := &agentStatusFailingWriter{}
	commits := 0
	unavailable := false
	collector := NewAgentStatusCollector(func(ctx context.Context) ([]AgentRecord, []error) {
		deadline, ok := ctx.Deadline()
		if !ok || time.Until(deadline) > AgentStatusCollectionBudget {
			t.Error("inventory has no bounded collection deadline")
		}
		if unavailable {
			return nil, []error{errors.New("inventory unavailable")}
		}
		return nil, nil
	}, nil, writer, clock, AgentStatusCollectorConfig{Committed: func() { commits++ }})
	collector.RunOnce(context.Background())
	if commits != 0 {
		t.Fatal("failed append published a status notification")
	}
	clock.advance(10 * time.Second)
	collector.RunOnce(context.Background())
	unavailable = true
	clock.advance(10 * time.Second)
	collector.RunOnce(context.Background())
	if commits != 2 || len(writer.snapshot()[1].UnavailableSpans) != 1 {
		t.Fatalf("success and unavailable batches did not both notify: commits=%d", commits)
	}
}

func TestAgentStatusTimingPipelinePreservesWorkingAndPublishesTransitions(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	writer.EnableCurrentFamilyValidation()
	clock := &collectorClock{now: time.Unix(100, 0)}
	service := NewStatsService(nil, StatsServiceConfig{Now: clock.Now, AgentStatus: func(ctx context.Context) (AgentStatusSnapshot, error) {
		return ReadAgentStatus(ctx, path, AgentStatusQuery{Now: clock.Now()})
	}})
	state, duration := AgentStatusRun, 200*time.Millisecond
	inventoryUnavailable := false
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
		if inventoryUnavailable {
			return nil, []error{errors.New("inventory unavailable")}
		}
		return []AgentRecord{{PID: 42, Kind: "codex", StartedIdentity: "process-42", TmuxSession: "session", TmuxWindow: "0", TmuxPaneID: "%42"}}, nil
	}, func(context.Context, AgentStatusRequest) AgentStatusResult {
		clock.advance(duration)
		// Exercise an HTTP status read before the slow successor scan commits.
		if duration > time.Second {
			current, err := service.AgentStatus(ctx)
			if err != nil || !current.Available || current.Agents[0].(map[string]any)["state"] != "run" {
				t.Errorf("bounded successor scan hid working: %+v err=%v", current, err)
			}
		}
		return AgentStatusResult{Status: state, ObservedAt: clock.Now()}
	}, agentStatusTimingWriter{BatchWriter: writer, t: t}, clock, AgentStatusCollectorConfig{Committed: service.InvalidateAgentStatus})
	check := func(want AgentStatus, available bool) {
		t.Helper()
		if err := service.RefreshAgentStatusIfDue(ctx); err != nil {
			t.Fatal(err)
		}
		current, err := service.AgentStatus(ctx)
		if err != nil || current.Available != available {
			t.Fatalf("available=%t: %+v err=%v", available, current, err)
		}
		if available && (len(current.Agents) != 1 || current.Agents[0].(map[string]any)["state"] != string(want)) {
			t.Fatalf("committed %s did not replace prior status: %+v", want, current)
		}
	}
	collector.RunOnce(ctx)
	check(AgentStatusRun, true)
	clock.advance(9800 * time.Millisecond)
	duration = 9800 * time.Millisecond
	collector.RunOnce(ctx)
	check(AgentStatusRun, true)
	for _, nextState := range []AgentStatus{AgentStatusError, AgentStatusIdle, AgentStatusRun} {
		clock.advance(10 * time.Second)
		state, duration = nextState, 200*time.Millisecond
		collector.RunOnce(ctx)
		check(nextState, true)
	}
	clock.advance(10 * time.Second)
	inventoryUnavailable = true
	collector.RunOnce(ctx)
	check("", false)
	clock.advance(10 * time.Second)
	inventoryUnavailable = false
	collector.RunOnce(ctx)
	check(AgentStatusRun, true)
}

func TestAgentStatusCycleWaitHandlesOverrunsAndIdleDemand(t *testing.T) {
	for _, scenario := range []struct {
		name    string
		elapsed time.Duration
		active  bool
		wait    time.Duration
	}{
		{"slow-successor", 9800 * time.Millisecond, true, 200 * time.Millisecond},
		{"overrun", 12 * time.Second, true, 0},
		{"idle-demand-poll", 12 * time.Second, false, time.Second},
		{"idle-overrun", 61 * time.Second, false, 0},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			started := time.Unix(100, 0)
			clock := &agentStatusScheduleClock{collectorClock: collectorClock{now: started.Add(scenario.elapsed)}, timers: make(chan time.Duration, 1)}
			collector := NewAgentStatusCollector(nil, nil, nil, clock, AgentStatusCollectorConfig{ActiveFunc: func() bool { return scenario.active }})
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if scenario.wait == 0 {
				if !collector.waitForNextCycle(ctx, started) || len(clock.timers) != 0 {
					t.Fatal("overrun should start one successor without a catch-up timer")
				}
				return
			}
			done := make(chan bool, 1)
			go func() { done <- collector.waitForNextCycle(ctx, started) }()
			select {
			case delay := <-clock.timers:
				if delay != scenario.wait {
					t.Errorf("wait=%s want %s", delay, scenario.wait)
				}
			case <-time.After(2 * time.Second):
				t.Error("cycle did not schedule its deadline")
			}
			cancel()
			if <-done {
				t.Fatal("canceled cycle wait should stop")
			}
		})
	}
}

func TestStatsServiceStatusWorkerPublishesCommitsWithoutRingLifecycle(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	reads, releaseOldRead := make(chan int, 4), make(chan struct{})
	var state atomic.Int32
	state.Store(1)
	calls := 0
	service := NewStatsService(nil, StatsServiceConfig{AgentStatus: func(ctx context.Context) (AgentStatusSnapshot, error) {
		calls++
		captured := state.Load()
		reads <- calls
		if calls == 2 {
			select {
			case <-releaseOldRead:
			case <-ctx.Done():
				return AgentStatusSnapshot{}, ctx.Err()
			}
		}
		return AgentStatusSnapshot{Available: true, Agents: []any{captured}}, nil
	}})
	stopped := make(chan struct{})
	go func() { defer close(stopped); service.RunAgentStatusRefresh(ctx) }()
	defer func() { cancel(); <-stopped }()
	waitRead := func(want int) {
		t.Helper()
		select {
		case got := <-reads:
			if got != want {
				t.Fatalf("read=%d want %d", got, want)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("status read waited for an absent ring-lifecycle tick")
		}
	}
	waitRead(1)
	service.agentStatusRefreshMu.Lock()
	service.agentStatusRefreshMu.Unlock()
	service.InvalidateAgentStatus()
	waitRead(2)
	state.Store(2)
	collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) { return nil, nil }, nil, &recordingBatchWriter{}, &collectorClock{now: time.Unix(100, 0)}, AgentStatusCollectorConfig{Committed: service.InvalidateAgentStatus})
	committed := make(chan struct{})
	go func() { collector.RunOnce(ctx); close(committed) }()
	select {
	case <-committed:
	case <-time.After(2 * time.Second):
		t.Fatal("collector waited for an older projection read")
	}
	close(releaseOldRead)
	waitRead(3)
	service.agentStatusRefreshMu.Lock()
	service.agentStatusRefreshMu.Unlock()
	current, err := service.AgentStatus(ctx)
	if err != nil || !current.Available || current.Agents[0] != int32(2) {
		t.Fatalf("committed state did not publish without ring work: %+v err=%v", current, err)
	}
}
