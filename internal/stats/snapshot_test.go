package stats

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
)

func TestBuildSnapshotFromTemporaryWriter(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	batch := AppendBatch{
		Observations: []Observation{
			{EventID: "host-1", Family: "cpu", SourceID: "host", ObservedAt: 10, EpochID: "host-epoch", OwnerGeneration: 1, Payload: map[string]any{"busy": 3}},
			{EventID: "transcript-1", Family: "agent", SourceID: "claude", ObservedAt: 70, EpochID: "agent-epoch", OwnerGeneration: 1, Payload: map[string]any{"state": "working"}},
			{EventID: "opencode-1", Family: "agent", SourceID: "opencode", ObservedAt: 130, EpochID: "agent-epoch", OwnerGeneration: 1, Payload: map[string]any{"model": "gpt"}},
		},
		UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "gpu-epoch", StartedAt: 45, EndedAt: 55, NativeCadenceSeconds: 10, Reason: "permission denied", OwnerGeneration: 1}},
		UsageAtoms: []UsageAtom{
			{EventID: "claude-input", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 20, Payload: map[string]any{"quantity": 4.0}},
			{EventID: "opencode-cache", Direction: "input", Modality: "text", CacheRole: "read", Unit: "tokens", ObservedAt: 125, Payload: map[string]any{"quantity": 2.0}},
			{EventID: "opencode-output", Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 130, Payload: map[string]any{"quantity": 5.0}},
		},
	}
	appendResult, err := writer.AppendBatch(ctx, batch)
	if err != nil || appendResult.Generation != 1 {
		t.Fatalf("append = %+v, err = %v", appendResult, err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()

	snapshot, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 0, End: 180, Resolution: 60})
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.SourceGeneration != 1 || snapshot.Generated.SchemaVersion != SchemaVersion || snapshot.Generated.MinimumWriterProtocol != 25 {
		t.Fatalf("metadata = %+v, header = %+v", snapshot.Generated, database.Header)
	}
	if len(snapshot.Observations.Buckets) != 3 || len(snapshot.Observations.Buckets[0].Observations) != 1 || len(snapshot.Observations.Buckets[2].Observations) != 1 {
		t.Fatalf("observation buckets = %+v", snapshot.Observations.Buckets)
	}
	if len(snapshot.Observations.Buckets[0].Unavailable) != 1 || snapshot.Observations.Buckets[0].Unavailable[0].Start != 45 || snapshot.Observations.Buckets[0].Unavailable[0].End != 55 {
		t.Fatalf("unavailable buckets = %+v", snapshot.Observations.Buckets)
	}
	if snapshot.Unavailable.State != SnapshotUnavailable || snapshot.Unavailable.Reason != "permission denied" {
		t.Fatalf("availability = %+v", snapshot.Unavailable)
	}
	if snapshot.Tokens.Buckets[0].Input != 4 || snapshot.Tokens.Buckets[2].Cache != 2 || snapshot.Tokens.Buckets[2].Output != 5 {
		t.Fatalf("token buckets = %+v", snapshot.Tokens.Buckets)
	}
}

func TestBuildSnapshotPreservesNoDataAndRejectsBounds(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{
		{EventID: "one", Family: "cpu", SourceID: "host", ObservedAt: 1, EpochID: "e", OwnerGeneration: 1, Payload: map[string]any{}},
		{EventID: "two", Family: "cpu", SourceID: "host", ObservedAt: 2, EpochID: "e", OwnerGeneration: 1, Payload: map[string]any{}},
	}}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()

	noData, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 100, End: 160, Resolution: 60})
	if err != nil {
		t.Fatal(err)
	}
	if noData.Unavailable.State != SnapshotNoData || !noData.Observations.Buckets[0].NoData {
		t.Fatalf("no-data snapshot = %+v", noData)
	}
	if _, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 0, End: float64(MaxProjectionInputs + 1), Resolution: 1}); err == nil || !strings.Contains(err.Error(), "exceeds") {
		t.Fatalf("excessive range error = %v", err)
	}
	if _, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 0, End: 10, Resolution: 10, FactLimit: 0}); err != nil {
		t.Fatal(err)
	}
	if _, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 0, End: 10, Resolution: 1, FactLimit: MaxFactReadLimit + 1}); err == nil {
		t.Fatal("oversized fact limit accepted")
	}
	if _, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 0, End: 10, Resolution: 1, FactLimit: 1}); err == nil || !strings.Contains(err.Error(), "exceed bound") {
		t.Fatalf("excessive fact input error = %v", err)
	}
}

func TestBuildSnapshotMarksStaleAgentStatusIntervalAsNoData(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{
		Observations: []Observation{{
			EventID: "status-old", Family: "agent_status", SourceID: "host", ObservedAt: 100,
			EpochID: "status-old", OwnerGeneration: 1,
			Payload: map[string]any{"states": map[string]any{"agent-1": "run"}},
		}},
		UnavailableSpans: []UnavailableSpan{{
			Family: "agent_status", SourceID: "host", EpochID: "status-current",
			StartedAt: 110, EndedAt: 130, NativeCadenceSeconds: 10,
			Reason: "pane-capture-unavailable", OwnerGeneration: 2,
		}},
	}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()

	snapshot, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 90, End: 130, Resolution: 10})
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Unavailable.State != SnapshotUnavailable {
		t.Fatalf("snapshot availability = %+v", snapshot.Unavailable)
	}
	if len(snapshot.Materialized.NoData) != 1 {
		t.Fatalf("materialized no-data = %+v", snapshot.Materialized.NoData)
	}
	noData := snapshot.Materialized.NoData[0]
	if noData.Family != "agent_status" || noData.Start != 110 || noData.End != 130 || noData.Reason != "pane-capture-unavailable" {
		t.Fatalf("agent status no-data = %+v", noData)
	}
	if !snapshot.Observations.Buckets[2].NoData || len(snapshot.Observations.Buckets[2].Unavailable) != 1 {
		t.Fatalf("agent status observation coverage = %+v", snapshot.Observations.Buckets)
	}
}

func TestBuildSnapshotKeepsValidDataWhenAnotherFamilyIsUnavailable(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	ended := 160.0
	if _, err := writer.AppendBatch(ctx, AppendBatch{
		Observations: []Observation{{
			EventID: "memory-1", Family: "system_memory", SourceID: "host", ObservedAt: 120,
			EpochID: "memory", OwnerGeneration: 1,
			Payload: map[string]any{"used_bytes": uint64(8), "capacity_bytes": uint64(16), "process_memory_bytes": map[string]any{"python": uint64(4)}},
		}},
		CoverageEpochs:   []CoverageEpoch{{Family: "system_memory", SourceID: "host", EpochID: "memory", StartedAt: 0, EndedAt: &ended, NativeCadenceSeconds: 60, OwnerGeneration: 1}},
		UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "gpu", StartedAt: 120, EndedAt: 130, NativeCadenceSeconds: 10, Reason: "no driver", OwnerGeneration: 1}},
	}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	snapshot, err := BuildSnapshot(ctx, database, SnapshotRequest{Start: 100, End: 160, Resolution: 60})
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Unavailable.State != SnapshotUnavailable {
		t.Fatalf("availability = %+v", snapshot.Unavailable)
	}
	if got := seriesValue(snapshot.Materialized.Buckets[0], "process_memory_bytes:python"); got != 4 {
		t.Fatalf("process memory = %v", got)
	}
}

func TestBuildSnapshotCarriesSparseMemoryIntoFiveAndFifteenMinuteViews(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	for _, at := range []float64{60, 120, 180} {
		if _, err := writer.AppendBatch(ctx, AppendBatch{
			Observations: []Observation{{
				EventID: fmt.Sprintf("memory-%.0f", at),
				Family:  "system_memory", SourceID: "host", ObservedAt: at, EpochID: "memory", OwnerGeneration: 1,
				Payload: map[string]any{
					"used_bytes":           uint64(8_000_000_000) + uint64(at)*1_000_000,
					"capacity_bytes":       uint64(16_000_000_000),
					"process_memory_bytes": map[string]any{"python": uint64(4_000_000_000) + uint64(at)*1_000_000},
				},
			}},
			CoverageEpochs: []CoverageEpoch{{Family: "system_memory", SourceID: "host", EpochID: "memory", StartedAt: 0, NativeCadenceSeconds: 60, OwnerGeneration: 1}},
		}); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()

	for _, request := range []SnapshotRequest{{Start: 0, End: 300, Resolution: 60}, {Start: 0, End: 900, Resolution: 60}} {
		snapshot, err := BuildSnapshot(ctx, database, request)
		if err != nil {
			t.Fatalf("request=%+v: %v", request, err)
		}
		if snapshot.Unavailable.State != SnapshotAvailable {
			t.Fatalf("request=%+v availability=%+v", request, snapshot.Unavailable)
		}
		seen := map[string]bool{}
		for _, bucket := range snapshot.Materialized.Buckets {
			for _, series := range bucket.Series {
				seen[series.Name] = true
			}
		}
		for _, name := range []string{"system_memory_used_bytes", "system_memory_capacity_bytes", "process_memory_bytes:python"} {
			if !seen[name] {
				t.Fatalf("request=%+v missing %q in %#v", request, name, seen)
			}
		}
	}
}
