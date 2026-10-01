package stats

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"os"
	"runtime/pprof"
	"testing"
	"time"
)

func TestRunWithStatsProfileLabelsSetsComponentAndFamily(t *testing.T) {
	called := false
	runWithStatsProfileLabels(context.Background(), "host_collector", "cpu", func(ctx context.Context) {
		called = true
		component, componentOK := pprof.Label(ctx, "stats_component")
		family, familyOK := pprof.Label(ctx, "stats_family")
		if !componentOK || component != "host_collector" {
			t.Fatalf("stats_component = %q, %v; want host_collector", component, componentOK)
		}
		if !familyOK || family != "cpu" {
			t.Fatalf("stats_family = %q, %v; want cpu", family, familyOK)
		}
	})
	if !called {
		t.Fatal("profile-label callback was not called")
	}
}

func TestRunWithStatsProfileLabelsSetsComponentWithoutFamily(t *testing.T) {
	runWithStatsProfileLabels(context.Background(), "stats_lifecycle", "", func(ctx context.Context) {
		component, componentOK := pprof.Label(ctx, "stats_component")
		if !componentOK || component != "stats_lifecycle" {
			t.Fatalf("stats_component = %q, %v; want stats_lifecycle", component, componentOK)
		}
		if family, familyOK := pprof.Label(ctx, "stats_family"); familyOK || family != "" {
			t.Fatalf("stats_family = %q, %v; want absent", family, familyOK)
		}
	})
}

func TestRunWithStatsProfilePhaseSetsPhase(t *testing.T) {
	runWithStatsProfilePhase(context.Background(), "host_collector", "system_memory", "append", func(ctx context.Context) {
		if phase, phaseOK := pprof.Label(ctx, "stats_phase"); !phaseOK || phase != "append" {
			t.Fatalf("stats_phase = %q, %v; want append", phase, phaseOK)
		}
		if family, familyOK := pprof.Label(ctx, "stats_family"); !familyOK || family != "system_memory" {
			t.Fatalf("stats_family = %q, %v; want system_memory", family, familyOK)
		}
	})
}

func TestRunWithStatsProfilePhaseWritesLabelsIntoCPUProfile(t *testing.T) {
	profile := captureCPUProfile(t, func() {
		started := time.Now()
		runWithStatsProfilePhase(context.Background(), "synthetic_collector", "synthetic_family", "synthetic_phase", func(context.Context) {
			// Keep the labeled region CPU-bound long enough for the 100 Hz sampler to
			// emit at least one sample; this remains a bounded synthetic fixture.
			for time.Since(started) < 250*time.Millisecond {
				_ = 1 + 1
			}
		})
	})
	assertCPUProfileHasLabels(t, profile, "stats_component", "synthetic_collector", "stats_family", "synthetic_family", "stats_phase", "synthetic_phase")
}

type profileLabelHostSources struct{}

func (profileLabelHostSources) CPU(context.Context) (CPUSnapshot, error) {
	started := time.Now()
	var work uint64
	for time.Since(started) < 250*time.Millisecond {
		work++
	}
	return CPUSnapshot{
		Host:                  CPUHostCounters{Busy: work / 2, Total: work},
		ProcessTicksPerSecond: 100,
		Processes:             map[string]CPUProcessCounters{"synthetic-process:1": {Identity: "synthetic-process:1", CPU: work, Binary: "synthetic-worker"}},
	}, nil
}

func (profileLabelHostSources) Memory(context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	return MemoryCounters{UsedBytes: 1, CapacityBytes: 2}, nil, nil
}

func (profileLabelHostSources) GPU(context.Context) ([]GPUDevice, error) { return nil, nil }

func TestHostCollectorCPUProfileCarriesOwnerAndPhaseLabels(t *testing.T) {
	clock := &collectorClock{now: time.Now()}
	collector := NewHostCollector(profileLabelHostSources{}, nil, clock, HostCollectorConfig{
		ActiveCadence: time.Second, MemoryCadence: time.Minute, SourceTimeout: time.Second,
		EpochID: "synthetic-profile", EpochStartedAt: float64(clock.Now().Unix()),
		OwnerGeneration: 1, SourceID: "synthetic-host", ProcessKey: "synthetic-worker",
	})
	profile := captureCPUProfile(t, func() {
		collector.runFamily(context.Background(), "cpu")
	})
	assertCPUProfileHasLabels(t, profile, "stats_component", "host_collector", "stats_family", "cpu", "stats_phase", "source")
}

func captureCPUProfile(t *testing.T, run func()) []byte {
	t.Helper()
	file, err := os.CreateTemp(t.TempDir(), "cpu-*.pprof")
	if err != nil {
		t.Fatal(err)
	}
	if err := pprof.StartCPUProfile(file); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	run()
	pprof.StopCPUProfile()
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	encoded, err := os.ReadFile(file.Name())
	if err != nil {
		t.Fatal(err)
	}
	reader, err := gzip.NewReader(bytes.NewReader(encoded))
	if err != nil {
		t.Fatal(err)
	}
	profile, err := io.ReadAll(reader)
	_ = reader.Close()
	if err != nil {
		t.Fatal(err)
	}
	return profile
}

func assertCPUProfileHasLabels(t *testing.T, profile []byte, labels ...string) {
	t.Helper()
	for _, label := range labels {
		if !bytes.Contains(profile, []byte(label)) {
			t.Fatalf("CPU profile omitted pprof label %q", label)
		}
	}
}
