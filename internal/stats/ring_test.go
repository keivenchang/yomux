package stats

import (
	"context"
	"path/filepath"
	"testing"
)

func TestRingSlotIndexAndFixedShape(t *testing.T) {
	if got, err := RingSlotIndex(10, 1800); err != nil || got != 0 {
		t.Fatalf("slot index = %d, err = %v", got, err)
	}
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	for resolution, capacity := range RingCapacities() {
		var count int
		if err := writer.db.QueryRow("SELECT count(*) FROM aggregate_ring_slots WHERE resolution_seconds=?", resolution).Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count != capacity {
			t.Fatalf("resolution %d has %d slots, want %d", resolution, count, capacity)
		}
	}
}

func TestRingPublicationReadAndInvalidation(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 100, BucketJSON: `{"value":1}`, Complete: true}}, 1, 200, true); err != nil {
		t.Fatal(err)
	}
	window, err := writer.ReadRingWindow(ctx, 10, 10, 110)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Rows) != 1 || window.Rows[0].BucketJSON != `{"value":1}` {
		t.Fatalf("ring window = %+v", window)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "late", Family: "cpu", SourceID: "host", ObservedAt: 105, EpochID: "e", Payload: map[string]any{"value": 2}}}}); err != nil {
		t.Fatal(err)
	}
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 {
		t.Fatalf("pending invalidations = %+v", pending)
	}
	window, err = writer.ReadRingWindow(ctx, 10, 10, 110)
	if err != nil {
		t.Fatal(err)
	}
	if len(window.Rows) != 0 || len(window.PendingInvalidations) != 1 {
		t.Fatalf("stale ring window = %+v", window)
	}
}

func TestRingDuplicateAppendDoesNotCreateNewGeneration(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	batch := AppendBatch{Observations: []Observation{{EventID: "same", Family: "cpu", SourceID: "host", ObservedAt: 10, EpochID: "e", Payload: map[string]any{"value": 1}}}}
	first, err := writer.AppendBatch(ctx, batch)
	if err != nil {
		t.Fatal(err)
	}
	second, err := writer.AppendBatch(ctx, batch)
	if err != nil {
		t.Fatal(err)
	}
	if first.Generation != 1 || second.Generation != 1 || second.ObservationsDuplicate != 1 {
		t.Fatalf("append generations = %+v then %+v", first, second)
	}
}
