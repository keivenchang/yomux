// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package stats

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCollectorHealthPreservesEvidenceAndRequiresCommit(t *testing.T) {
	var health CollectorHealthTracker
	at := time.Unix(100, 0)
	health.Record("cpu", at, nil, 0, 0)
	health.Record("cpu", at.Add(time.Second), errors.New("secret /transcripts/private.jsonl"), 8, 2)
	health.Record("cpu", at.Add(2*time.Second), errors.New("secret /transcripts/private.jsonl"), 8, 3)
	health.Record("gpu", at.Add(3*time.Second), nil, 0, 0)
	health.Attempt("cpu", at.Add(4*time.Second))
	state := health.Snapshot()["cpu"]
	if state.ConsecutiveFailures != 2 || state.LastSuccessfulCommit != 100 || state.QueueDepth != 8 || state.Dropped != 3 || state.Rejected != 2 {
		t.Fatalf("degraded state = %+v", state)
	}
	encoded, err := json.Marshal(state)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "private") || strings.Contains(string(encoded), "transcripts") {
		t.Fatalf("private source leaked: %s", encoded)
	}
	health.Record("cpu", at.Add(5*time.Second), nil, 0, 3)
	state = health.Snapshot()["cpu"]
	if state.ConsecutiveFailures != 0 || state.LastSuccessfulCommit != 105 || state.FirstError == nil || state.LatestError == nil || state.Dropped != 3 {
		t.Fatalf("recovered state lost evidence = %+v", state)
	}
}

func TestAppendErrorReportsConflictingEpochAfterPrune(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "errors.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	batch := writerBatch()
	end := float64(RetentionSeconds + 200)
	batch.CoverageEpochs[0].EndedAt = &end
	if _, err := writer.AppendBatch(ctx, batch); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Prune(ctx, float64(RetentionSeconds+100)); err != nil {
		t.Fatal(err)
	}
	batch.Observations = nil
	batch.UsageAtoms = nil
	batch.UnavailableSpans = nil
	batch.CoverageEpochs[0].StartedAt = 20
	_, err = writer.AppendBatch(ctx, batch)
	var detail *AppendError
	if !errors.As(err, &detail) || detail.StoredStart == nil || *detail.StoredStart != 10 || detail.IncomingStart != 20 || detail.PruneCutoff != 100 || detail.Family != "cpu" || detail.OwnerGeneration != 1 {
		t.Fatalf("conflict detail=%+v err=%v", detail, err)
	}
	if detail.SourceID == "host" || detail.Epoch == "epoch-1" || detail.Unwrap() == nil {
		t.Fatalf("unredacted or unchained error: %+v", detail)
	}
}

func TestAppendErrorAttributesActualObservationAndUnavailableSpan(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "identity.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	initial := writerBatch()
	if _, err := writer.AppendBatch(ctx, initial); err != nil {
		t.Fatal(err)
	}
	conflict := initial.Observations[0]
	conflict.ObservedAt = 11
	first := conflict
	first.SourceID = "unrelated"
	first.EventID = "unrelated"
	_, err = writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{first, conflict}})
	var detail *AppendError
	if !errors.As(err, &detail) || detail.SourceID != opaqueIdentity("host") || detail.IncomingStart != 11 {
		t.Fatalf("wrong observation attributed: %+v %v", detail, err)
	}
	span := UnavailableSpan{Family: "cpu", SourceID: "host", EpochID: "outage", StartedAt: 12, EndedAt: 15, NativeCadenceSeconds: 1, Reason: "offline", OwnerGeneration: 4}
	_, err = writer.AppendBatch(ctx, AppendBatch{UnavailableSpans: []UnavailableSpan{span}})
	if !errors.As(err, &detail) || detail.Family != "cpu" || detail.SourceID != opaqueIdentity("host") || detail.Epoch != opaqueIdentity("outage") || detail.IncomingStart != 12 || detail.OwnerGeneration != 4 {
		t.Fatalf("wrong unavailable span attributed: %+v %v", detail, err)
	}
}

func TestAppendErrorAttributesMixedUsageAndTombstoneConflicts(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "usage-errors.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	initial := writerBatch()
	if _, err := writer.AppendBatch(ctx, initial); err != nil {
		t.Fatal(err)
	}
	bad := initial.UsageAtoms[0]
	bad.ObservedAt = 11
	first := bad
	first.EventID = "unrelated-usage"
	for _, mixed := range []bool{false, true} {
		batch := AppendBatch{UsageAtoms: []UsageAtom{first, bad}}
		if mixed {
			batch.CoverageEpochs = initial.CoverageEpochs
			batch.UnavailableSpans = initial.UnavailableSpans
		}
		_, err := writer.AppendBatch(ctx, batch)
		var detail *AppendError
		if !errors.As(err, &detail) || detail.Family != "agent_tokens" || detail.SourceID != opaqueIdentity(bad.EventID) || detail.IncomingStart != 11 || detail.StoredStart == nil || *detail.StoredStart != 10 {
			t.Fatalf("usage mixed=%t detail=%+v err=%v", mixed, detail, err)
		}
	}
	_, err = writer.AppendBatch(ctx, AppendBatch{UsageTombstones: []UsageTombstone{{EventID: initial.UsageAtoms[0].EventID, Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 11, Quantity: 4, Provider: "openai", Model: "gpt", ThreadID: "thread-1"}}})
	var detail *AppendError
	if !errors.As(err, &detail) || detail.Family != "agent_tokens" || detail.SourceID != opaqueIdentity(bad.EventID) || detail.IncomingStart != 11 || detail.StoredStart == nil || *detail.StoredStart != 10 {
		t.Fatalf("tombstone detail=%+v err=%v", detail, err)
	}
}

func TestTokenHealthRequiresActualCommitAfterSuccessfulEmptyScan(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "empty-scan.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &collectorClock{now: time.Unix(100, 0)}
	collector := NewAgentTokenCollector(writer, clock, AgentTokenCollectorConfig{})
	collector.recordAppendError(errors.New("outage"), false)
	collector.setAttempt(clock.Now())
	ok, _, err := collector.appendOpenCodeCollected(ctx, AppendBatch{}, nil)
	if !ok || err != nil {
		t.Fatal(err)
	}
	collector.setSuccess(clock.Now())
	if state := collector.Health()["agent_tokens"]; state.ConsecutiveFailures == 0 || state.LastSuccessfulCommit != 0 {
		t.Fatalf("empty scan recovered without commit: %+v", state)
	}
	if _, err := collector.appendCheckpointBatch(ctx, AppendBatch{}, nil); err != nil {
		t.Fatal(err)
	}
	collector.setSuccess(clock.Now())
	if state := collector.Health()["agent_tokens"]; state.ConsecutiveFailures != 0 || state.LastSuccessfulCommit != 100 {
		t.Fatalf("actual commit did not recover: %+v", state)
	}
}

func requireAppendError(t testing.TB, err error) *AppendError {
	t.Helper()
	var detail *AppendError
	if !errors.As(err, &detail) || detail.Unwrap() == nil {
		t.Fatalf("expected a structured append error retaining its cause, got %v", err)
	}
	return detail
}
