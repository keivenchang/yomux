// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package stats

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestRetentionWithLiveCollectorsConcurrentAppendsAndRestart(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	path := filepath.Join(root, "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := writer.Close(); err != nil {
			t.Error(err)
		}
	}()
	start := float64(RetentionSeconds + 1000)
	clock := &collectorClock{now: time.Unix(int64(start), 0)}
	transcript := filepath.Join(root, "usage.jsonl")
	writeJSONL(t, transcript, usageClaude(start, "initial", 3, 2))
	config := AgentTokenCollectorConfig{EpochID: "tokens", EpochStartedAt: 100, SourceID: "host", Inventory: func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{Kind: "claude", TmuxSession: "fixture"}}, nil
	}, TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
		return []TranscriptSource{{Path: transcript, Provider: "claude", SourceID: "claude", ThreadID: "fixture"}}, nil
	}}
	tokens := NewAgentTokenCollector(writer, clock, config)
	sources := &capturedHostSources{}
	for i := 0; i < 6; i++ {
		sources.cpu = append(sources.cpu, snapshot(uint64(50+i*20), uint64(100+i*40), uint64(10+i*10)))
		sources.memory = append(sources.memory, MemoryCounters{UsedBytes: 8, CapacityBytes: 16})
		sources.gpu = append(sources.gpu, []GPUDevice{{SourceID: "gpu:0", Utilization: 10}})
	}
	host := NewHostCollector(sources, writer, clock, HostCollectorConfig{EpochID: "host", EpochStartedAt: 100, SourceID: "host", ActiveCadence: time.Second, MemoryCadence: time.Second, GPUActiveCadence: time.Second})
	expiredEnd := 200.0
	if _, err := writer.AppendBatch(ctx, AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "expired", EpochID: "expired", StartedAt: 1, EndedAt: &expiredEnd, NativeCadenceSeconds: 1}}}); err != nil {
		t.Fatal(err)
	}
	host.RunOnce(ctx)
	tokens.RunOnce(ctx)
	for i := 1; i <= 3; i++ {
		clock.advance(10 * time.Second)
		at := float64(clock.Now().Unix())
		file, err := os.OpenFile(transcript, os.O_APPEND|os.O_WRONLY, 0600)
		if err != nil {
			t.Fatal(err)
		}
		err = json.NewEncoder(file).Encode(usageClaude(at, fmt.Sprintf("after-prune-%d", i), 3, 2))
		closeErr := file.Close()
		if err != nil {
			t.Fatal(err)
		}
		if closeErr != nil {
			t.Fatal(closeErr)
		}
		var workers sync.WaitGroup
		workers.Add(3)
		pruneErrors := make(chan error, 1)
		go func() { defer workers.Done(); _, err := writer.Prune(ctx, at); pruneErrors <- err }()
		go func() { defer workers.Done(); host.RunOnce(ctx) }()
		go func() { defer workers.Done(); tokens.RunOnce(ctx) }()
		workers.Wait()
		if err := <-pruneErrors; err != nil {
			t.Fatal(err)
		}
		for _, states := range []map[string]CollectorHealth{host.Health(), tokens.Health()} {
			for name, state := range states {
				if state.ConsecutiveFailures != 0 || state.LastSuccessfulCommit != at {
					t.Fatalf("%s did not commit after pruning: %+v", name, state)
				}
			}
		}
	}
	var expired int
	if err := writer.db.QueryRow("SELECT count(*) FROM coverage_epochs WHERE epoch_id='expired'").Scan(&expired); err != nil || expired != 0 {
		t.Fatalf("expired epochs=%d err=%v", expired, err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writer, err = OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	clock.advance(10 * time.Second)
	config.EpochID = "restarted"
	config.EpochStartedAt = float64(clock.Now().Unix())
	restarted := NewAgentTokenCollector(writer, clock, config)
	restarted.RunOnce(ctx)
	if state := restarted.Health()["agent_tokens"]; state.ConsecutiveFailures != 0 || state.LastSuccessfulCommit == 0 {
		t.Fatalf("restart recording=%+v", state)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	usage, err := reader.ReadUsageAtoms(ctx, start, start+100, 100)
	if err != nil || len(usage) != 8 {
		t.Fatalf("durable usage atoms=%d err=%v, want four input/output pairs", len(usage), err)
	}
	epochs, err := reader.ReadCoverageEpochs(ctx, start, start+100, 100)
	if err != nil {
		t.Fatal(err)
	}
	for _, epoch := range epochs {
		if epoch.EpochID == "tokens" && epoch.StartedAt != 100 {
			t.Fatalf("retention rewrote identity: %+v", epoch)
		}
	}
}

func TestRetainedCoverageMaterializesOnlyRequestedWindow(t *testing.T) {
	end := 210.0
	layer, err := (Materializer{}).Build(MaterializeRequest{Start: 100, End: 220, Resolution: 10}, nil, []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "retained", StartedAt: 1, EndedAt: &end, NativeCadenceSeconds: 1}}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(layer.NoData) == 0 {
		t.Fatal("expected bounded sparse coverage gaps")
	}
	for _, gap := range layer.NoData {
		if gap.Start < 100 || gap.End > 220 {
			t.Fatalf("coverage escaped requested window: %+v", gap)
		}
	}
}
