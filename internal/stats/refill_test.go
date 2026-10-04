// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestRefillFrozenWindowClosedSessionsAndRepeatedTotals(t *testing.T) {
	ctx := context.Background()
	w := newAgentTokenTestWriter(t)
	root := t.TempDir()
	claude := filepath.Join(root, "claude")
	codex := filepath.Join(root, "codex")
	end := time.Now().Truncate(time.Second)
	start := end.Add(-24 * time.Hour)
	records := []map[string]any{}
	for index, at := range []time.Time{start.Add(-time.Second), start, end.Add(-time.Second), end} {
		records = append(records, map[string]any{"type": "assistant", "timestamp": at.UTC().Format(time.RFC3339), "message": map[string]any{"id": fmt.Sprint(index), "model": "claude-sonnet-4", "usage": map[string]any{"input_tokens": 3, "output_tokens": 5}}})
	}
	writeJSONL(t, filepath.Join(claude, "closed.jsonl"), records...)
	if err := os.Mkdir(codex, 0700); err != nil {
		t.Fatal(err)
	}
	open := filepath.Join(root, "missing.db")
	if err := os.WriteFile(open, nil, 0600); err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(w, nil, AgentTokenCollectorConfig{ClaudeTranscriptRoot: claude, CodexTranscriptRoot: codex, OpenCodeDatabase: open})
	existing := writerBatch().UsageAtoms[0]
	existing.ObservedAt = float64(end.Unix()) - 1
	existing.EventID = "opencode:preserved"
	existing.Payload["execution_source"] = "opencode"
	status := Observation{EventID: "run", Family: "agent_status", SourceID: "agent", EpochID: "before", ObservedAt: existing.ObservedAt, Payload: map[string]any{"run_agents": 1}}
	if _, err := w.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{existing}, Observations: []Observation{status}}); err != nil {
		t.Fatal(err)
	}
	var previous []UsageAtom
	for pass := 0; pass < 2; pass++ {
		phases := []string{}
		gaps, err := collector.RefillAgentHistory(ctx, w, end, func(phase string) { phases = append(phases, phase) })
		if err != nil || !reflect.DeepEqual(gaps.Gaps, []string{"opencode"}) {
			t.Fatalf("gaps=%v err=%v", gaps, err)
		}
		if !reflect.DeepEqual(phases, []string{"Reading history", "Rebuilding charts"}) {
			t.Fatalf("phases=%v", phases)
		}
		got, err := w.refillBaseline(ctx, float64(start.Unix()), float64(end.Unix()))
		if err != nil || len(got) != 5 {
			t.Fatalf("atoms=%+v err=%v", got, err)
		}
		if pass > 0 && !reflect.DeepEqual(previous, got) {
			t.Fatalf("repeat changed facts: %v / %v", previous, got)
		}
		previous = got
		var count int
		if err := w.db.QueryRow("SELECT COUNT(*) FROM observations WHERE event_id='run'").Scan(&count); err != nil || count != 1 {
			t.Fatalf("activity changed: %d %v", count, err)
		}
	}
	// Recovery discovers an OpenCode session that is absent from the roster.
	if err := os.Remove(open); err != nil {
		t.Fatal(err)
	}
	createOpenCodeFixtureWithTitle(t, open, "closed", "/closed", "closed", end.Add(-time.Minute).UnixMilli())
	gaps, err := collector.RefillAgentHistory(ctx, w, end, func(string) {})
	if err != nil || len(gaps.Gaps) != 0 {
		t.Fatalf("recovery=%v %v", gaps, err)
	}
	got, err := w.refillBaseline(ctx, float64(start.Unix()), float64(end.Unix()))
	if err != nil || len(got) != 7 {
		t.Fatalf("recovery atoms=%v %v", got, err)
	}
	// Costs and usage are derived from the same stable atom set.
	priced := Materializer{Price: func(UsageAtom) (int64, int64, *PriceEvidence, bool, error) {
		return 12, 20, &PriceEvidence{CatalogModel: "fixture"}, true, nil
	}}
	before, err := priced.Build(MaterializeRequest{Start: float64(start.Unix()), End: float64(end.Unix()), Resolution: 300}, nil, nil, nil, got)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := collector.RefillAgentHistory(ctx, w, end, func(string) {}); err != nil {
		t.Fatal(err)
	}
	afterAtoms, _ := w.refillBaseline(ctx, float64(start.Unix()), float64(end.Unix()))
	after, err := priced.Build(MaterializeRequest{Start: float64(start.Unix()), End: float64(end.Unix()), Resolution: 300}, nil, nil, nil, afterAtoms)
	if err != nil || !reflect.DeepEqual(before, after) {
		t.Fatalf("repeat changed charts/cost: %v", err)
	}
}

func TestRefillPreservesConcurrentArrivalsAndRollback(t *testing.T) {
	ctx := context.Background()
	w := newAgentTokenTestWriter(t)
	root := t.TempDir()
	claude := filepath.Join(root, "claude")
	end := time.Now().Truncate(time.Second)
	writeJSONL(t, filepath.Join(claude, "closed.jsonl"), map[string]any{"type": "assistant", "timestamp": end.Add(-time.Minute).Format(time.RFC3339), "message": map[string]any{"id": "msg", "model": "claude-sonnet-4", "usage": map[string]any{"output_tokens": 5}}})
	collector := NewAgentTokenCollector(w, nil, AgentTokenCollectorConfig{ClaudeTranscriptRoot: claude})
	late := writerBatch().UsageAtoms[0]
	late.EventID = "claude:late-arrival"
	late.ObservedAt = float64(end.Unix()) - 2
	late.Payload["execution_source"] = "claude"
	next := late
	next.EventID = "claude:after-window"
	next.ObservedAt = float64(end.Unix()) + 1
	_, err := collector.RefillAgentHistory(ctx, w, end, func(phase string) {
		if phase == "Rebuilding charts" {
			if _, err := w.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{late, next}}); err != nil {
				t.Fatal(err)
			}
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	before, _ := w.refillBaseline(ctx, float64(end.Unix())-86400, float64(end.Unix())+2)
	if len(before) != 3 {
		t.Fatalf("concurrent facts erased: %v", before)
	}
	if _, err := w.db.Exec("CREATE TRIGGER fail_refill BEFORE DELETE ON usage_atoms BEGIN SELECT RAISE(ABORT,'injected failure'); END"); err != nil {
		t.Fatal(err)
	}
	if _, err := collector.RefillAgentHistory(ctx, w, end, func(string) {}); err == nil {
		t.Fatal("expected transaction failure")
	}
	after, _ := w.refillBaseline(ctx, float64(end.Unix())-86400, float64(end.Unix())+2)
	if !reflect.DeepEqual(before, after) {
		t.Fatal("failed refill changed data")
	}
}

func TestRefillPreservesBaselineWhenSourceHasNoEvidence(t *testing.T) {
	ctx := context.Background()
	w := newAgentTokenTestWriter(t)
	claude := filepath.Join(t.TempDir(), "claude")
	if err := os.Mkdir(claude, 0700); err != nil {
		t.Fatal(err)
	}
	end := time.Now().Truncate(time.Second)
	baseline := writerBatch().UsageAtoms[0]
	baseline.EventID = "claude:preserved"
	baseline.ObservedAt = float64(end.Unix()) - 1
	baseline.Payload["execution_source"] = "claude"
	if _, err := w.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{baseline}}); err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(w, nil, AgentTokenCollectorConfig{ClaudeTranscriptRoot: claude})
	result, err := collector.RefillAgentHistory(ctx, w, end, func(string) {})
	if err == nil || !reflect.DeepEqual(result.Gaps, []string{"claude", "codex", "opencode"}) {
		t.Fatalf("empty source result=%+v err=%v", result, err)
	}
	after, err := w.refillBaseline(ctx, float64(end.Add(-24*time.Hour).Unix()), float64(end.Unix()))
	if err != nil || len(after) != 1 || after[0].EventID != baseline.EventID {
		t.Fatalf("empty source erased baseline: atoms=%+v err=%v", after, err)
	}
}

func TestRefillPreservesConcurrentUpdateToCapturedAtom(t *testing.T) {
	ctx := context.Background()
	w := newAgentTokenTestWriter(t)
	root := t.TempDir()
	claude := filepath.Join(root, "claude")
	end := time.Now().Truncate(time.Second)
	writeJSONL(t, filepath.Join(claude, "closed.jsonl"), map[string]any{"type": "assistant", "timestamp": end.Add(-time.Minute).Format(time.RFC3339), "message": map[string]any{"id": "msg", "model": "claude-sonnet-4", "usage": map[string]any{"output_tokens": 5}}})
	collector := NewAgentTokenCollector(w, nil, AgentTokenCollectorConfig{ClaudeTranscriptRoot: claude})
	rebuilt, err := collector.refillSource(ctx, "claude", claude, float64(end.Add(-24*time.Hour).Unix()), float64(end.Unix()))
	if err != nil || len(rebuilt) == 0 {
		t.Fatalf("scan source: atoms=%+v err=%v", rebuilt, err)
	}
	captured := rebuilt[0]
	captured.Payload["model"] = "unknown"
	if _, err := w.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{captured}}); err != nil {
		t.Fatal(err)
	}
	concurrent := captured
	concurrent.Payload = cloneUsagePayload(captured.Payload)
	concurrent.Payload["model"] = "claude-sonnet-4"
	concurrent.Payload["model_evidence"] = "live-update"
	_, err = collector.RefillAgentHistory(ctx, w, end, func(phase string) {
		if phase == "Rebuilding charts" {
			if _, appendErr := w.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{concurrent}}); appendErr != nil {
				t.Fatal(appendErr)
			}
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	var payload string
	if err := w.db.QueryRowContext(ctx, "SELECT payload_json FROM usage_atoms WHERE event_id=? AND direction=? AND modality=? AND cache_role=? AND unit=?", captured.EventID, captured.Direction, captured.Modality, captured.CacheRole, captured.Unit).Scan(&payload); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal([]byte(payload), &got); err != nil {
		t.Fatal(err)
	}
	if got["model_evidence"] != "live-update" {
		t.Fatalf("refill replaced concurrent model evidence: %v", got["model_evidence"])
	}
}

func TestRefillZeroByteOpenCodeDoesNotStopLiveCollection(t *testing.T) {
	ctx := context.Background()
	w := newAgentTokenTestWriter(t)
	root := t.TempDir()
	path := filepath.Join(root, "claude", "closed.jsonl")
	codexPath := filepath.Join(root, "codex", "closed.jsonl")
	open := filepath.Join(root, "open.db")
	now := time.Now()
	writeJSONL(t, path, map[string]any{"type": "assistant", "timestamp": now.Add(-time.Second).Format(time.RFC3339), "message": map[string]any{"id": "live", "model": "claude-sonnet-4", "usage": map[string]any{"input_tokens": 3, "output_tokens": 5}}})
	writeJSONL(t, codexPath, usageCodex(float64(now.Unix()-1), 9, 2, 4))
	if err := os.WriteFile(open, nil, 0600); err != nil {
		t.Fatal(err)
	}
	collector := NewAgentTokenCollector(w, nil, AgentTokenCollectorConfig{Inventory: func(context.Context) ([]AgentRecord, []error) { return []AgentRecord{{Kind: "claude"}}, nil }, TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) {
		return []TranscriptSource{{Path: path, Provider: "claude", SourceID: "claude", ThreadID: "claude"}, {Path: codexPath, Provider: "codex", SourceID: "codex", ThreadID: "codex"}}, nil
	}, OpenCodeDatabase: open})
	collector.RunOnce(ctx)
	atoms, _ := w.refillBaseline(ctx, float64(now.Unix())-86400, float64(now.Unix())+1)
	if len(atoms) != 5 {
		t.Fatalf("live Claude/Codex stopped: %v", atoms)
	}
	// A valid database returned at the same path must be opened on the next cycle.
	if err := os.Remove(open); err != nil {
		t.Fatal(err)
	}
	createOpenCodeFixtureWithTitle(t, open, "ses-live", "/repo", "live", now.Add(-time.Second).UnixMilli())
	collector.config.Inventory = func(context.Context) ([]AgentRecord, []error) {
		return []AgentRecord{{Kind: "claude"}, {Kind: "opencode", OpenCodeSession: "ses-live"}}, nil
	}
	collector.RunOnce(ctx)
	atoms, _ = w.refillBaseline(ctx, float64(now.Unix())-86400, float64(now.Unix())+1)
	if len(atoms) != 8 {
		t.Fatalf("OpenCode did not recover: %v", atoms)
	}
	collector.closeOpenCodeDatabase()
}

func TestRefillPublicationUsesFrozenTargetsDespiteNewCollection(t *testing.T) {
	ctx := context.Background()
	w := newAgentTokenTestWriter(t)
	now := time.Now().Truncate(time.Second)
	bucket := now.Unix() - 1
	warm := []RingBucketWrite{}
	for _, resolution := range []int{1, 10, 60, 300} {
		start := bucket / int64(resolution) * int64(resolution)
		warm = append(warm, RingBucketWrite{ResolutionSeconds: resolution, BucketStart: start, Complete: true, BucketJSON: emptyRingBucketPayload(t, float64(start), float64(resolution), 0, float64(now.Unix()))})
	}
	if _, err := w.PublishRingBuckets(ctx, warm, 0, float64(now.Unix()), true); err != nil {
		t.Fatal(err)
	}
	atom := writerBatch().UsageAtoms[0]
	atom.ObservedAt = float64(bucket)
	changed, err := w.appendReplacingUsage(ctx, AppendBatch{UsageAtoms: []UsageAtom{atom}}, nil, nil, []UsageAtom{})
	if err != nil || len(changed.PublicationTargets) == 0 {
		t.Fatalf("targets=%v error=%v", changed.PublicationTargets, err)
	}
	ready, err := w.RefillPublicationReady(ctx, changed.PublicationTargets, now)
	if err != nil || ready {
		t.Fatalf("unpublished target accepted: %v %v", ready, err)
	}
	beforePublication := atom
	beforePublication.EventID = "newer-before-publication"
	if _, err := w.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{beforePublication}}); err != nil {
		t.Fatal(err)
	}
	ready, err = w.RefillPublicationReady(ctx, changed.PublicationTargets, now)
	if err != nil || ready {
		t.Fatalf("coalesced target accepted before publication: %v %v", ready, err)
	}
	writes := []RingBucketWrite{}
	for _, target := range changed.PublicationTargets {
		writes = append(writes, RingBucketWrite{ResolutionSeconds: target.ResolutionSeconds, BucketStart: target.BucketStart, Complete: true, BucketJSON: emptyRingBucketPayload(t, float64(target.BucketStart), float64(target.ResolutionSeconds), changed.Generation, float64(now.Unix()))})
	}
	if _, err := w.PublishRingBuckets(ctx, writes, changed.Generation, float64(now.Unix()), true); err != nil {
		t.Fatal(err)
	}
	newer := atom
	newer.EventID = "newer-collection"
	if _, err := w.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{newer}}); err != nil {
		t.Fatal(err)
	}
	if w.PendingCount() == 0 {
		t.Fatal("expected new live invalidations")
	}
	ready, err = w.RefillPublicationReady(ctx, changed.PublicationTargets, now)
	if err != nil || !ready {
		t.Fatalf("new collection extended refill: %v %v", ready, err)
	}
	ready, err = w.RefillPublicationReady(ctx, changed.PublicationTargets, now.Add(72*time.Hour))
	if err != nil || !ready {
		t.Fatalf("expired targets block refill: %v %v", ready, err)
	}
}
