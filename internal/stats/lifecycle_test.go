package stats

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestLifecyclePublishesFactsAndRepairsMutation(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 1000, Now: func() time.Time { return time.Unix(2000, 0) }})
	writer.AttachLifecycle(lifecycle)
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "one", Family: "cpu", SourceID: "host", ObservedAt: 1995, EpochID: "e", Payload: map[string]any{"process_percent": 2}}}}); err != nil {
		t.Fatal(err)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	window, err := writer.ReadRingWindow(ctx, 10, 10, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Rows) == 0 || len(window.PendingInvalidations) != 0 {
		t.Fatalf("published window = %+v", window)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "two", Family: "cpu", SourceID: "host", ObservedAt: 1996, EpochID: "e", Payload: map[string]any{"process_percent": 4}}}}); err != nil {
		t.Fatal(err)
	}
	stale, err := writer.ReadRingWindow(ctx, 10, 10, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if len(stale.PendingInvalidations) != 1 {
		t.Fatalf("pending repair = %+v", stale)
	}
	if err := lifecycle.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	repaired, err := writer.ReadRingWindow(ctx, 10, 10, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if len(repaired.Rows) == 0 || len(repaired.PendingInvalidations) != 0 {
		t.Fatalf("repaired window = %+v", repaired)
	}
	if lifecycle.Counters().Publications < 2 || lifecycle.Counters().Repairs == 0 {
		t.Fatalf("counters = %+v", lifecycle.Counters())
	}
}
