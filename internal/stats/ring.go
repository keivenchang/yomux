package stats

import (
	"bytes"
	"compress/zlib"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"math"
)

const (
	RingPayloadVersion        = 2
	MaxRingBucketBytes        = 256 * 1024
	RetentionSeconds          = 2 * 24 * 60 * 60
	RetiredInvalidationLimit  = 5000
	WALAutocheckpointPages    = 1000
	WALAllocationCeilingBytes = 8 * 1024 * 1024
)

var ringCapacities = map[int]int{1: 300, 10: 180, 60: 480, 300: 288}

type RingBucketWrite struct {
	ResolutionSeconds int
	BucketStart       int64
	BucketJSON        string
	Complete          bool
}
type RingBucketRow struct {
	ResolutionSeconds                int
	BucketStart                      int64
	BucketJSON                       string
	Complete                         bool
	SourceGeneration, RingGeneration int64
	PublishedAt                      float64
}
type RingPublication struct {
	RingGeneration, SourceGeneration int64
	PublishedAt                      float64
	BucketsUpdated                   int
}
type RingWindow struct {
	RangeSeconds, ResolutionSeconds  int
	WindowStart, WindowEnd           int64
	Rows                             []RingBucketRow
	MissingBucketStarts              []int64
	SourceGeneration, RingGeneration int64
	PublishedAt                      float64
	StoreSourceGeneration            int64
	PendingInvalidations             []int64
}
type InvalidationCell struct {
	ResolutionSeconds             int
	BucketStart, SourceGeneration int64
}

func RingCapacities() map[int]int {
	result := make(map[int]int, len(ringCapacities))
	for k, v := range ringCapacities {
		result[k] = v
	}
	return result
}

func RingSlotIndex(resolutionSeconds int, bucketStart int64) (int, error) {
	capacity, ok := ringCapacities[resolutionSeconds]
	if !ok || resolutionSeconds <= 0 || bucketStart < 0 || bucketStart%int64(resolutionSeconds) != 0 {
		return 0, fmt.Errorf("invalid ring bucket address")
	}
	return int((bucketStart / int64(resolutionSeconds)) % int64(capacity)), nil
}

func encodeRingBucket(value string) (any, error) {
	if len([]byte(value)) > MaxRingBucketBytes {
		return nil, fmt.Errorf("ring bucket exceeds %d bytes", MaxRingBucketBytes)
	}
	var object map[string]any
	if err := json.Unmarshal([]byte(value), &object); err != nil || object == nil {
		return nil, fmt.Errorf("ring bucket must be a JSON object")
	}
	var compressed bytes.Buffer
	compressed.Write([]byte("YRB1\x00"))
	zw, err := zlib.NewWriterLevel(&compressed, zlib.BestSpeed)
	if err != nil {
		return nil, err
	}
	if _, err := zw.Write([]byte(value)); err != nil {
		return nil, err
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	if compressed.Len() < len([]byte(value)) {
		return compressed.Bytes(), nil
	}
	return value, nil
}

func decodeRingBucket(value any) (string, error) {
	switch raw := value.(type) {
	case string:
		return raw, nil
	case []byte:
		if len(raw) < 5 || string(raw[:5]) != "YRB1\x00" {
			return "", fmt.Errorf("invalid ring bucket encoding")
		}
		zr, err := zlib.NewReader(bytes.NewReader(raw[5:]))
		if err != nil {
			return "", fmt.Errorf("invalid ring bucket compression: %w", err)
		}
		limited := io.LimitReader(zr, MaxRingBucketBytes+1)
		decoded, readErr := io.ReadAll(limited)
		closeErr := zr.Close()
		if readErr != nil || closeErr != nil || len(decoded) > MaxRingBucketBytes {
			return "", fmt.Errorf("invalid ring bucket compression")
		}
		return string(decoded), nil
	default:
		return "", fmt.Errorf("invalid ring bucket value %T", value)
	}
}

func validateRingShape(ctx context.Context, db *sql.DB) error {
	for table, expected := range ringColumns {
		rows, err := db.QueryContext(ctx, "PRAGMA table_info("+table+")")
		if err != nil {
			return err
		}
		actual := []string{}
		for rows.Next() {
			var cid int
			var name string
			if err := rows.Scan(&cid, &name, new(any), new(any), new(any), new(any)); err != nil {
				_ = rows.Close()
				return err
			}
			actual = append(actual, name)
		}
		_ = rows.Close()
		if len(actual) != len(expected) {
			return &ValidationError{Reason: fmt.Sprintf("ring table %q has unexpected columns", table)}
		}
		for i := range expected {
			if actual[i] != expected[i] {
				return &ValidationError{Reason: fmt.Sprintf("ring table %q is missing column %q", table, expected[i])}
			}
		}
	}
	for resolution, capacity := range ringCapacities {
		var count int
		if err := db.QueryRowContext(ctx, "SELECT count(*) FROM aggregate_ring_slots WHERE resolution_seconds=?", resolution).Scan(&count); err != nil || count != capacity {
			return &ValidationError{Reason: fmt.Sprintf("ring %ds has invalid slot count", resolution)}
		}
		rows, err := db.QueryContext(ctx, "SELECT slot_index FROM aggregate_ring_slots WHERE resolution_seconds=? ORDER BY slot_index", resolution)
		if err != nil {
			return err
		}
		for index := 0; index < capacity; index++ {
			var got int
			if !rows.Next() || rows.Scan(&got) != nil || got != index {
				_ = rows.Close()
				return &ValidationError{Reason: fmt.Sprintf("ring %ds slots are not contiguous", resolution)}
			}
		}
		_ = rows.Close()
	}
	return nil
}

func (w *Writer) PublishRingBuckets(ctx context.Context, buckets []RingBucketWrite, sourceGeneration int64, publishedAt float64, advancePublication bool) (RingPublication, error) {
	if len(buckets) == 0 || sourceGeneration < 0 || math.IsNaN(publishedAt) || math.IsInf(publishedAt, 0) || publishedAt < 0 {
		return RingPublication{}, fmt.Errorf("invalid ring publication")
	}
	tx, err := w.db.BeginTx(ctx, nil)
	if err != nil {
		return RingPublication{}, err
	}
	rollback := func(err error) (RingPublication, error) { _ = tx.Rollback(); return RingPublication{}, err }
	var oldRing, oldSource int64
	var oldPublished float64
	if err = tx.QueryRowContext(ctx, "SELECT ring_generation,source_generation,published_at FROM aggregate_publication WHERE singleton=1").Scan(&oldRing, &oldSource, &oldPublished); err != nil {
		return rollback(err)
	}
	if sourceGeneration < oldSource || publishedAt < oldPublished {
		return rollback(fmt.Errorf("ring publication moves backwards"))
	}
	ringGeneration := oldRing + 1
	newest := map[int]int64{}
	for _, bucket := range buckets {
		slot, err := RingSlotIndex(bucket.ResolutionSeconds, bucket.BucketStart)
		if err != nil {
			return rollback(err)
		}
		encoded, err := encodeRingBucket(bucket.BucketJSON)
		if err != nil {
			return rollback(err)
		}
		result, err := tx.ExecContext(ctx, "UPDATE aggregate_ring_slots SET bucket_start=?,bucket_json=?,complete=?,source_generation=?,ring_generation=?,published_at=?,payload_version=? WHERE resolution_seconds=? AND slot_index=?", bucket.BucketStart, encoded, boolInt(bucket.Complete), sourceGeneration, ringGeneration, publishedAt, RingPayloadVersion, bucket.ResolutionSeconds, slot)
		if err != nil {
			return rollback(err)
		}
		affected, _ := result.RowsAffected()
		if affected != 1 {
			return rollback(fmt.Errorf("ring slot missing"))
		}
		if bucket.BucketStart > newest[bucket.ResolutionSeconds] {
			newest[bucket.ResolutionSeconds] = bucket.BucketStart
		}
		_, err = tx.ExecContext(ctx, "UPDATE ring_invalidations SET applied_at=? WHERE resolution_seconds=? AND bucket_start=? AND applied_at IS NULL AND source_generation<=?", publishedAt, bucket.ResolutionSeconds, bucket.BucketStart, sourceGeneration)
		if err != nil {
			return rollback(err)
		}
	}
	for resolution, head := range newest {
		if _, err = tx.ExecContext(ctx, "UPDATE aggregate_rings SET newest_bucket_start=CASE WHEN newest_bucket_start IS NULL OR newest_bucket_start<? THEN ? ELSE newest_bucket_start END WHERE resolution_seconds=?", head, head, resolution); err != nil {
			return rollback(err)
		}
		if _, err = tx.ExecContext(ctx, "UPDATE ring_replay_cursor SET folded_through_observed_at=MAX(folded_through_observed_at,?),folded_source_generation=?,updated_at=? WHERE resolution_seconds=? AND folded_source_generation<=?", float64(head+int64(resolution)), sourceGeneration, publishedAt, resolution, sourceGeneration); err != nil {
			return rollback(err)
		}
	}
	if advancePublication {
		if _, err = tx.ExecContext(ctx, "UPDATE aggregate_publication SET ring_generation=?,source_generation=?,published_at=? WHERE singleton=1", ringGeneration, sourceGeneration, publishedAt); err != nil {
			return rollback(err)
		}
	}
	if err = tx.Commit(); err != nil {
		return RingPublication{}, err
	}
	return RingPublication{ringGeneration, sourceGeneration, publishedAt, len(buckets)}, nil
}

func boolInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

func (w *Writer) PendingInvalidationCells(ctx context.Context) ([]InvalidationCell, error) {
	rows, err := w.db.QueryContext(ctx, "SELECT resolution_seconds,bucket_start,source_generation FROM ring_invalidations WHERE applied_at IS NULL ORDER BY resolution_seconds,bucket_start")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []InvalidationCell{}
	for rows.Next() {
		var cell InvalidationCell
		if err := rows.Scan(&cell.ResolutionSeconds, &cell.BucketStart, &cell.SourceGeneration); err != nil {
			return nil, err
		}
		result = append(result, cell)
	}
	return result, rows.Err()
}

func (w *Writer) ReadRingWindow(ctx context.Context, rangeSeconds, resolutionSeconds int, windowEnd int64) (RingWindow, error) {
	capacity, ok := ringCapacities[resolutionSeconds]
	if !ok || rangeSeconds <= 0 || windowEnd < int64(rangeSeconds) || windowEnd%int64(resolutionSeconds) != 0 || rangeSeconds > capacity*resolutionSeconds {
		return RingWindow{}, fmt.Errorf("unsupported ring window")
	}
	start := windowEnd - int64(rangeSeconds)
	result := RingWindow{RangeSeconds: rangeSeconds, ResolutionSeconds: resolutionSeconds, WindowStart: start, WindowEnd: windowEnd}
	var storeGeneration int64
	if err := w.db.QueryRowContext(ctx, "SELECT source_generation FROM schema_meta WHERE singleton=1").Scan(&storeGeneration); err != nil {
		return result, err
	}
	result.StoreSourceGeneration = storeGeneration
	rows, err := w.db.QueryContext(ctx, "SELECT slot_index,bucket_start,bucket_json,complete,source_generation,ring_generation,published_at,payload_version FROM aggregate_ring_slots WHERE resolution_seconds=?", resolutionSeconds)
	if err != nil {
		return result, err
	}
	defer rows.Close()
	slots := map[int64]RingBucketRow{}
	for rows.Next() {
		var slot int
		var bucket sql.NullInt64
		var payload any
		var complete, payloadVersion int
		var source, ring int64
		var published float64
		if err := rows.Scan(&slot, &bucket, &payload, &complete, &source, &ring, &published, &payloadVersion); err != nil {
			return result, err
		}
		if !bucket.Valid || payloadVersion != RingPayloadVersion {
			continue
		}
		decoded, err := decodeRingBucket(payload)
		if err != nil {
			return result, err
		}
		slots[bucket.Int64] = RingBucketRow{resolutionSeconds, bucket.Int64, decoded, complete != 0, source, ring, published}
	}
	for bucket := start; bucket < windowEnd; bucket += int64(resolutionSeconds) {
		var pending int
		_ = w.db.QueryRowContext(ctx, "SELECT count(*) FROM ring_invalidations WHERE resolution_seconds=? AND bucket_start=? AND applied_at IS NULL", resolutionSeconds, bucket).Scan(&pending)
		if pending != 0 {
			result.MissingBucketStarts = append(result.MissingBucketStarts, bucket)
			result.PendingInvalidations = append(result.PendingInvalidations, bucket)
			continue
		}
		row, exists := slots[bucket]
		if !exists {
			result.MissingBucketStarts = append(result.MissingBucketStarts, bucket)
		} else {
			result.Rows = append(result.Rows, row)
			if row.RingGeneration > result.RingGeneration {
				result.RingGeneration, result.SourceGeneration, result.PublishedAt = row.RingGeneration, row.SourceGeneration, row.PublishedAt
			}
		}
	}
	return result, rows.Err()
}

func (w *Writer) Checkpoint(ctx context.Context) error {
	_, err := w.db.ExecContext(ctx, fmt.Sprintf("PRAGMA wal_checkpoint(PASSIVE)"))
	return err
}
func (w *Writer) Vacuum(ctx context.Context, completedAt float64) error {
	if _, err := w.db.ExecContext(ctx, "VACUUM"); err != nil {
		return err
	}
	if _, err := w.db.ExecContext(ctx, "PRAGMA optimize"); err != nil {
		return err
	}
	_, err := w.db.ExecContext(ctx, "UPDATE schema_meta SET last_vacuumed_at=? WHERE singleton=1", completedAt)
	return err
}

func invalidationBucket(resolution int, timestamp float64) int64 {
	return int64(math.Floor(timestamp/float64(resolution))) * int64(resolution)
}
func recordFactInvalidations(ctx context.Context, tx *sql.Tx, generation int64, timestamps []float64, now float64) error {
	seen := map[[2]int64]bool{}
	for _, timestamp := range timestamps {
		if timestamp < 0 {
			continue
		}
		for resolution := range ringCapacities {
			bucket := invalidationBucket(resolution, timestamp)
			key := [2]int64{int64(resolution), bucket}
			if seen[key] {
				continue
			}
			seen[key] = true
			if _, err := tx.ExecContext(ctx, "INSERT OR IGNORE INTO ring_invalidations(resolution_seconds,bucket_start,source_generation,reason,created_at,applied_at) SELECT ?,?,?,?,?,NULL WHERE EXISTS (SELECT 1 FROM aggregate_ring_slots WHERE resolution_seconds=? AND bucket_start=? AND bucket_json IS NOT NULL)", resolution, bucket, generation, "fact_mutation", timestamp, resolution, bucket); err != nil {
				return err
			}
			if _, err := tx.ExecContext(ctx, "DELETE FROM ring_invalidations WHERE resolution_seconds=? AND bucket_start=? AND applied_at IS NULL AND source_generation<?", resolution, bucket, generation); err != nil {
				return err
			}
		}
	}
	return nil
}
