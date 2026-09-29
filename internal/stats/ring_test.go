package stats

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"
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

func TestRingBucketPersistsLargeNoDataPayload(t *testing.T) {
	var payload strings.Builder
	payload.WriteString(`{"no_data":[`)
	for index := 0; index < 14000; index++ {
		if index > 0 {
			payload.WriteByte(',')
		}
		fmt.Fprintf(&payload, `{"family":"agent_tokens","source_id":"retired-unavailable:source-%05d","epoch":"epoch-%02d","reason":"transcript cursor state exceeds its size bound","start":1790582400,"end":1790582700,"source_cadence_seconds":60}`, index, index%69)
	}
	payload.WriteString(`]}`)
	raw := payload.String()
	if len(raw) <= 256*1024 || len(raw) >= 8*1024*1024 {
		t.Fatalf("fixture JSON size = %d, want greater than 256 KiB and below the intended 8 MiB ring limit", len(raw))
	}

	encoded, err := encodeRingBucket(raw)
	if err != nil {
		t.Fatalf("encode large no-data bucket: %v", err)
	}
	decoded, err := decodeRingBucket(encoded)
	if err != nil || decoded != raw {
		t.Fatalf("large no-data bucket round trip: decoded %d bytes, err=%v", len(decoded), err)
	}
}

func TestDecodeRingBucketRejectsTrailingAndConcatenatedCompression(t *testing.T) {
	encodedValue, err := encodeRingBucket(`{"value":"` + strings.Repeat("a", 1024) + `"}`)
	if err != nil {
		t.Fatal(err)
	}
	encoded, ok := encodedValue.([]byte)
	if !ok {
		t.Fatal("fixture did not select compressed ring encoding")
	}
	for name, suffix := range map[string][]byte{
		"trailing bytes":    []byte("garbage"),
		"concatenated zlib": append([]byte(nil), encoded[5:]...),
	} {
		payload := append(append([]byte(nil), encoded...), suffix...)
		if _, err := decodeRingBucket(payload); err == nil {
			t.Errorf("decode %s: accepted trailing compressed data", name)
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

func TestRingPayloadReadRejectsPublicationThatChangedAfterMetadataRead(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	first, err := encodeRingBucketPayload(MaterializedBucket{
		Start: 100, Duration: 10, Complete: true,
		Series: []MaterializedSeries{{Name: "cpu_percent:host", Value: 1}},
	}, nil, 1, 100)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 100, BucketJSON: first, Complete: true}}, 1, 100, true); err != nil {
		t.Fatal(err)
	}
	oldWindow, err := writer.ReadRingWindowMetadata(ctx, 10, 10, 110)
	if err != nil || len(oldWindow.Rows) != 1 {
		t.Fatalf("read first publication metadata: rows=%+v err=%v", oldWindow.Rows, err)
	}
	second, err := encodeRingBucketPayload(MaterializedBucket{
		Start: 100, Duration: 10, Complete: true,
		Series: []MaterializedSeries{{Name: "cpu_percent:host", Value: 2}},
	}, nil, 2, 101)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 100, BucketJSON: second, Complete: true}}, 2, 101, true); err != nil {
		t.Fatal(err)
	}
	payloads, err := writer.readRingBucketPayloads(ctx, 10, []int64{100})
	if err != nil {
		t.Fatal(err)
	}
	payload, ok := payloads[100]
	if !ok {
		t.Fatal("replacement payload was not returned")
	}
	if ringBucketPayloadMatchesMetadata(oldWindow.Rows[0], payload) {
		t.Fatalf("payload from a newer publication matched old metadata: row=%+v payload=%+v", oldWindow.Rows[0], payload)
	}
	newWindow, err := writer.ReadRingWindowMetadata(ctx, 10, 10, 110)
	if err != nil || len(newWindow.Rows) != 1 || !ringBucketPayloadMatchesMetadata(newWindow.Rows[0], payload) {
		t.Fatalf("replacement metadata does not match its payload: rows=%+v payload=%+v err=%v", newWindow.Rows, payload, err)
	}
}

func TestRingWindowMetadataDoesNotDecodeBucketPayloads(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 100, BucketJSON: `{"value":1}`, Complete: true}}, 1, 200, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, "UPDATE aggregate_ring_slots SET bucket_json=? WHERE resolution_seconds=? AND bucket_start=?", []byte("bad-compressed-payload"), 10, 100); err != nil {
		t.Fatal(err)
	}
	metadata, err := writer.ReadRingWindowMetadata(ctx, 10, 10, 110)
	if err != nil {
		t.Fatalf("metadata read = %v", err)
	}
	if len(metadata.Rows) != 1 || metadata.Rows[0].BucketJSON != "" || !metadata.Rows[0].Complete {
		t.Fatalf("metadata window = %+v", metadata)
	}
	full, err := writer.ReadRingWindow(ctx, 10, 10, 110)
	if err == nil {
		t.Fatalf("full ring read accepted malformed bucket payload: %+v", full)
	}
}

func TestRingWindowPayloadsDefersCompressionDecode(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	raw := `{"value":"` + strings.Repeat("x", 4096) + `"}`
	encoded, err := encodeRingBucket(raw)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := encoded.([]byte); !ok {
		t.Fatalf("synthetic payload was not compressed: %T", encoded)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 100, BucketJSON: raw, Complete: true}}, 1, 200, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, "UPDATE aggregate_ring_slots SET bucket_json=? WHERE resolution_seconds=? AND bucket_start=?", encoded, 10, 100); err != nil {
		t.Fatal(err)
	}
	payloads, err := writer.ReadRingWindowPayloads(ctx, 10, 10, 110)
	if err != nil {
		t.Fatalf("raw payload read = %v", err)
	}
	if len(payloads.Rows) != 1 || payloads.Rows[0].BucketJSON == raw {
		t.Fatalf("payload window unexpectedly decoded: %+v", payloads)
	}
	decoded, err := decodeRingBucket(payloads.Rows[0].BucketJSON)
	if err != nil || decoded != raw {
		t.Fatalf("deferred payload decode = %q, err=%v", decoded, err)
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

func TestRingPublicationRetiresOnlyInvalidationsThroughBuiltGeneration(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "seed-generation", Family: "cpu", SourceID: "host", ObservedAt: 0,
		EpochID: "epoch", OwnerGeneration: 1, Payload: map[string]any{"value": 0},
	}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":1}`, Complete: true}}, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "late-generation", Family: "cpu", SourceID: "host", ObservedAt: 1,
		EpochID: "epoch", OwnerGeneration: 1, Payload: map[string]any{"value": 2},
	}}}); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 1 || got[0] != 0 {
		t.Fatalf("pending before publication = %v", got)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":2}`, Complete: true}}, 1, 2, true); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 1 || got[0] != 0 {
		t.Fatalf("older publication retired newer invalidation: %v", got)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":2}`, Complete: true}}, 2, 3, true); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 0 {
		t.Fatalf("current publication left invalidation pending: %v", got)
	}
}

func TestRingPublicationRemovesDisplacedSlotInvalidations(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":1}`, Complete: true}}, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "displaced", Family: "cpu", SourceID: "host", ObservedAt: 1,
		EpochID: "epoch", OwnerGeneration: 1, Payload: map[string]any{"value": 2},
	}}}); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 1 || got[0] != 0 {
		t.Fatalf("pending before displacement = %v", got)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 1800, BucketJSON: `{"value":3}`, Complete: true}}, 2, 2, true); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 0 {
		t.Fatalf("displaced invalidation survived slot wrap: %v", got)
	}
}

func TestOpenWriterReconcilesMoreThanSQLiteBindLimitWithSyntheticFacts(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	base := float64(time.Now().Unix()-3600) + 0.5
	seed := make([]RingBucketWrite, 0, len(ringCapacities))
	for _, resolution := range []int{1, 10, 60, 300} {
		seed = append(seed, RingBucketWrite{
			ResolutionSeconds: resolution,
			BucketStart:       invalidationBucket(resolution, base),
			BucketJSON:        `{"value":1}`,
			Complete:          true,
		})
	}
	if _, err := writer.PublishRingBuckets(ctx, seed, 1, base+1, true); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	// Simulate a legacy/direct owner: it committed many facts and advanced the
	// source watermark without leaving cell-level invalidations. 130 timestamps
	// produce more than 400 unique ring keys, forcing reconciliation to cross
	// the portable SQLite bind-variable boundary while remaining fixture-only.
	keys := make(map[[2]int64]struct{})
	for index := 0; index < 130; index++ {
		observedAt := base + float64(index*60)
		for resolution := range ringCapacities {
			keys[[2]int64{int64(resolution), invalidationBucket(resolution, observedAt)}] = struct{}{}
		}
		if _, err := writer.db.ExecContext(ctx, `INSERT INTO observations(event_id,family,source_id,observed_at,epoch_id,owner_generation,payload_json) VALUES(?,?,?,?,?,?,?)`, fmt.Sprintf("synthetic-%03d", index), "cpu", "synthetic", observedAt, "epoch", 1, `{"value":1}`); err != nil {
			_ = writer.Close()
			t.Fatal(err)
		}
	}
	if len(keys) <= maxRingInvalidationStatementKeys {
		t.Fatalf("synthetic reconciliation only generated %d keys, want more than %d", len(keys), maxRingInvalidationStatementKeys)
	}
	if _, err := writer.db.ExecContext(ctx, "DELETE FROM ring_invalidations"); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, "UPDATE schema_meta SET source_generation=2 WHERE singleton=1"); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatalf("synthetic startup reconciliation failed: %v", err)
	}
	defer reopened.Close()
	pending, err := reopened.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	byResolution := make(map[int]int)
	for _, cell := range pending {
		if cell.SourceGeneration != 2 {
			t.Fatalf("pending cell has source generation %d, want 2: %+v", cell.SourceGeneration, cell)
		}
		byResolution[cell.ResolutionSeconds]++
	}
	for _, resolution := range []int{1, 10, 60, 300} {
		if byResolution[resolution] == 0 {
			t.Fatalf("startup reconciliation did not retain seeded %ds cell: pending=%+v", resolution, pending)
		}
	}
}
