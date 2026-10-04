package stats

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestLifecycleStartupPreparesDefaultBeforeFirstRequest(t *testing.T) {
	ctx := context.Background()
	w, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	now := time.Unix(RetentionSeconds+1000, 0)
	products := []ProductKey{{1800, 10}, {300, 1}}
	l := NewLifecycle(w, LifecycleConfig{Now: func() time.Time { return now }, MaxCells: 64, StartupProducts: products, BackgroundBackfillCadence: 30 * time.Second, BackgroundBackfillInitialDelay: 30 * time.Second})
	t.Cleanup(l.Stop)
	service := NewStatsService(w, StatsServiceConfig{Now: func() time.Time { return now }, Products: products})
	for i := 0; i < 5; i++ {
		if err := l.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := service.CurrentSnapshot(ctx, 300, 1); err != nil {
		t.Fatalf("default graph not prepared before a request: %v", err)
	}
	if l.Counters().ByResolution[1].MaterializedBuckets != 300 {
		t.Fatal("default 5m/AUTO was not first")
	}
	for i := 0; i < 4; i++ {
		if err := l.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
	}
	state := l.Counters().StartupPreparation
	if !state.Complete || state.PendingCells != 0 || state.TotalCells != 480 || state.TimedOut {
		t.Fatalf("startup did not converge: %+v", state)
	}
	before := l.Counters().MaterializedBuckets
	if err := l.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	if l.Counters().MaterializedBuckets != before {
		t.Fatal("completed startup bypassed ordinary background cadence")
	}
}

func TestLifecycleStartupFrozenHorizonAndExplicitDemandPriority(t *testing.T) {
	ctx := context.Background()
	w, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	now := time.Unix(RetentionSeconds+1000, 0)
	l := NewLifecycle(w, LifecycleConfig{Now: func() time.Time { return now }, MaxCells: 1, StartupProducts: []ProductKey{{300, 1}}, BackgroundBackfillCadence: 30 * time.Second})
	t.Cleanup(l.Stop)
	cells, err := l.selectCells(ctx, now, 1)
	if err != nil || len(cells) != 1 || cells[0].ResolutionSeconds != 1 {
		t.Fatalf("first startup selection: %+v %v", cells, err)
	}
	initial := l.Counters().StartupPreparation
	now = now.Add(10 * time.Second)
	_, err = l.selectCells(ctx, now, 99)
	if err != nil {
		t.Fatal(err)
	}
	frozen := l.Counters().StartupPreparation
	if frozen.Horizon != initial.Horizon || frozen.SourceGeneration != 1 || frozen.TotalCells != 300 {
		t.Fatalf("startup target moved with ingestion: %+v", frozen)
	}
	l.DemandProduct(7200, 300)
	cells, err = l.selectCells(ctx, now, 99)
	if err != nil || len(cells) != 1 || cells[0].ResolutionSeconds != 300 {
		t.Fatalf("explicit request did not preempt startup: %+v %v", cells, err)
	}
}

func TestLifecycleStartupTimesOutWithoutExtendingBudget(t *testing.T) {
	ctx := context.Background()
	w, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	now := time.Unix(RetentionSeconds+1000, 0)
	l := NewLifecycle(w, LifecycleConfig{Now: func() time.Time { return now }, StartupProducts: []ProductKey{{300, 1}}, BackgroundBackfillCadence: 30 * time.Second})
	t.Cleanup(l.Stop)
	if _, err := l.selectCells(ctx, now, 0); err != nil {
		t.Fatal(err)
	}
	now = now.Add(120 * time.Second)
	cells, err := l.selectCells(ctx, now, 0)
	if err != nil || len(cells) != 0 {
		t.Fatalf("timeout kept accelerated work admitted: %+v %v", cells, err)
	}
	state := l.Counters().StartupPreparation
	if !state.TimedOut || state.Complete || state.Duration != 120*time.Second || state.PendingCells != 300 {
		t.Fatalf("timeout diagnostics: %+v", state)
	}
	now = now.Add(30 * time.Second)
	cells, err = l.selectCells(ctx, now, 0)
	if err != nil || len(cells) == 0 {
		t.Fatalf("ordinary background work did not resume: %+v %v", cells, err)
	}
}

func TestLifecycleStartupAdvancingClockPreparesDefault(t *testing.T) {
	ctx := context.Background()
	w, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	now := time.Unix(RetentionSeconds+1000, 0)
	products := []ProductKey{{1800, 10}, {300, 1}}
	l := NewLifecycle(w, LifecycleConfig{Now: func() time.Time { return now }, MaxCells: 64, StartupProducts: products, BackgroundBackfillCadence: 30 * time.Second, BackgroundBackfillInitialDelay: 30 * time.Second})
	t.Cleanup(l.Stop)
	service := NewStatsService(w, StatsServiceConfig{Now: func() time.Time { return now }, Products: products})
	for i := 0; i < 10; i++ {
		if err := l.RunOnce(ctx); err != nil {
			t.Fatal(err)
		}
		if err := service.Refresh(ctx); err != nil {
			t.Fatal(err)
		}
		if _, err := service.CurrentSnapshot(ctx, 300, 1); err == nil {
			return
		}
		now = now.Add(time.Second)
	}
	t.Fatalf("default remains unavailable after startup target completion: %+v; %+v", l.Counters().StartupPreparation, service.State())
}
