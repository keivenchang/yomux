package stats

import (
	"context"
	"database/sql"
	"errors"
	"path/filepath"
	"testing"
)

func writerBatch() AppendBatch {
	end := 20.0
	return AppendBatch{
		Observations:     []Observation{{EventID: "obs-1", Family: "cpu", SourceID: "host", ObservedAt: 10, EpochID: "epoch-1", Payload: map[string]any{"value": 1}}},
		CoverageEpochs:   []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 10, EndedAt: &end, NativeCadenceSeconds: 1, OwnerGeneration: 1}},
		UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 10, EndedAt: 20, NativeCadenceSeconds: 5, Reason: "offline", OwnerGeneration: 1}},
		UsageAtoms:       []UsageAtom{{EventID: "codex:thread-1:event-1", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 10, Payload: map[string]any{"quantity": 4.0, "provider": "openai", "model": "gpt", "thread_id": "thread-1", "execution_source": "codex"}}},
	}
}

func TestOpenWriterCreatesSchema8AndReadback(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	result, err := writer.AppendBatch(context.Background(), writerBatch())
	if err != nil {
		t.Fatal(err)
	}
	if result.Generation != 1 || result.ObservationsAccepted != 1 || result.CoverageChanged != 1 || result.UnavailableAccepted != 1 || result.UsageAccepted != 1 {
		t.Fatalf("result = %+v", result)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	observations, err := reader.ReadObservations(context.Background(), 0, 100, 10)
	if err != nil || len(observations) != 1 || observations[0].EventID != "obs-1" {
		t.Fatalf("observations = %+v, err = %v", observations, err)
	}
	usage, err := reader.ReadUsageAtoms(context.Background(), 0, 100, 10)
	if err != nil || len(usage) != 1 {
		t.Fatalf("usage = %+v, err = %v", usage, err)
	}
}

func TestAppendBatchDuplicateReplayDoesNotAdvanceGeneration(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	batch := writerBatch()
	if _, err = writer.AppendBatch(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
	result, err := writer.AppendBatch(context.Background(), batch)
	if err != nil {
		t.Fatal(err)
	}
	if result.Generation != 1 || result.ObservationsDuplicate != 1 || result.CoverageDuplicate != 1 || result.UnavailableDuplicate != 1 || result.UsageDuplicate != 1 {
		t.Fatalf("replay result = %+v", result)
	}
}

func TestAppendBatchConflictRollsBackEarlierRows(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err = writer.AppendBatch(context.Background(), writerBatch()); err != nil {
		t.Fatal(err)
	}
	conflict := writerBatch()
	conflict.Observations = append(conflict.Observations, Observation{EventID: "new", Family: "cpu", SourceID: "host", ObservedAt: 11, EpochID: "epoch-1", Payload: map[string]any{"value": 2}})
	conflict.UsageAtoms[0].Payload = map[string]any{"quantity": 99.0, "provider": "openai", "model": "gpt", "thread_id": "thread-1", "execution_source": "codex"}
	if _, err = writer.AppendBatch(context.Background(), conflict); err == nil {
		t.Fatal("conflict accepted")
	}
	var count int
	if err = writer.db.QueryRow("SELECT count(*) FROM observations").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("rollback left %d observations", count)
	}
}

func TestCoverageUnavailableCoverageSameFamilySource(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	end := 10.0
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: &end, NativeCadenceSeconds: 1}}}); err != nil {
		t.Fatal(err)
	}
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 10, EndedAt: 20, NativeCadenceSeconds: 1, Reason: "offline"}}}); err != nil {
		t.Fatal(err)
	}
	end = 30
	result, err := writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-2", StartedAt: 20, EndedAt: &end, NativeCadenceSeconds: 1}}})
	if err != nil || result.CoverageChanged != 1 {
		t.Fatalf("success after unavailable interval = %+v, err = %v", result, err)
	}
}

func TestCoverageAndUnavailableIntervalConflictsRollBack(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	end := 10.0
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: &end, NativeCadenceSeconds: 1}}}); err != nil {
		t.Fatal(err)
	}
	conflict := AppendBatch{
		Observations:     []Observation{{EventID: "kept", Family: "cpu", SourceID: "host", ObservedAt: 1, EpochID: "epoch-1", Payload: map[string]any{"value": 1}}},
		UnavailableSpans: []UnavailableSpan{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 5, EndedAt: 15, NativeCadenceSeconds: 1, Reason: "offline"}},
	}
	if _, err = writer.AppendBatch(context.Background(), conflict); err == nil {
		t.Fatal("unavailable span overlapping coverage accepted")
	}
	var count int
	if err = writer.db.QueryRow("SELECT count(*) FROM observations").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("rollback left %d observations", count)
	}

	if _, err = writer.AppendBatch(context.Background(), AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: 10, NativeCadenceSeconds: 1, Reason: "offline"}}}); err != nil {
		t.Fatal(err)
	}
	conflict = AppendBatch{
		Observations:   []Observation{{EventID: "kept-2", Family: "gpu", SourceID: "host", ObservedAt: 1, EpochID: "epoch-1", Payload: map[string]any{"value": 1}}},
		CoverageEpochs: []CoverageEpoch{{Family: "gpu", SourceID: "host", EpochID: "epoch-2", StartedAt: 5, NativeCadenceSeconds: 1}},
	}
	if _, err = writer.AppendBatch(context.Background(), conflict); err == nil {
		t.Fatal("coverage overlapping unavailable accepted")
	}
	if err = writer.db.QueryRow("SELECT count(*) FROM observations").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("second rollback left %d observations", count)
	}
}

func TestCoverageAndUnavailableCadenceChangesAreRejected(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	end := 10.0
	coverage := CoverageEpoch{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: &end, NativeCadenceSeconds: 1}
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{coverage}}); err != nil {
		t.Fatal(err)
	}
	coverage.NativeCadenceSeconds = 2
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{coverage}}); err == nil {
		t.Fatal("coverage cadence change accepted")
	}
	span := UnavailableSpan{Family: "gpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: 10, NativeCadenceSeconds: 1, Reason: "offline"}
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{UnavailableSpans: []UnavailableSpan{span}}); err != nil {
		t.Fatal(err)
	}
	span.NativeCadenceSeconds = 2
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{UnavailableSpans: []UnavailableSpan{span}}); err == nil {
		t.Fatal("unavailable cadence change accepted")
	}
}

func TestUsageTombstoneDeletesMatchingAtomAndReplayIsDuplicate(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	batch := writerBatch()
	if _, err = writer.AppendBatch(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
	batch.Observations = nil
	batch.CoverageEpochs = nil
	batch.UnavailableSpans = nil
	batch.UsageAtoms = nil
	batch.UsageTombstones = []UsageTombstone{{EventID: "codex:thread-1:event-1", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 10, Quantity: 4, Provider: "openai", Model: "gpt", ThreadID: "thread-1"}}
	result, err := writer.AppendBatch(context.Background(), batch)
	if err != nil || result.TombstonesAccepted != 1 {
		t.Fatalf("tombstone result = %+v, err = %v", result, err)
	}
	result, err = writer.AppendBatch(context.Background(), batch)
	if err != nil || result.TombstonesDuplicate != 1 || result.Generation != 2 {
		t.Fatalf("tombstone replay = %+v, err = %v", result, err)
	}
	var count int
	if err = writer.db.QueryRow("SELECT count(*) FROM usage_atoms").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("tombstone left %d usage atoms", count)
	}
}

func TestWriterCanceledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3")); !errors.Is(err, context.Canceled) {
		t.Fatalf("open error = %v", err)
	}
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err = writer.AppendBatch(ctx, writerBatch()); !errors.Is(err, context.Canceled) {
		t.Fatalf("append error = %v", err)
	}
	var count int
	if err = writer.db.QueryRow("SELECT count(*) FROM observations").Scan(&count); err != nil && !errors.Is(err, sql.ErrNoRows) {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("canceled append wrote %d rows", count)
	}
}
