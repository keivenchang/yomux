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
	"sort"
	"time"
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

// maxStartupReconciliationCells bounds the one-time scan used when a prior
// owner advanced the publication watermark without recording the affected
// ring cells. The limit is larger than the complete current ring, so a
// healthy schema-9 store can reconcile every populated slot in one startup
// without retaining source facts in memory.
const maxStartupReconciliationCells = 2048

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

type ringInvalidationInterval struct {
	start float64
	end   *float64
}

func coverageChangeInterval(start float64, previousEnd sql.NullFloat64, previousGeneration uint64, current CoverageEpoch) ringInvalidationInterval {
	currentEnd := current.EndedAt
	if previousGeneration != current.OwnerGeneration {
		if previousEnd.Valid && currentEnd != nil {
			end := math.Max(previousEnd.Float64, *currentEnd)
			return ringInvalidationInterval{start: start, end: &end}
		}
		return ringInvalidationInterval{start: start, end: nil}
	}
	if previousEnd.Valid == (currentEnd != nil) {
		if !previousEnd.Valid {
			return ringInvalidationInterval{start: start, end: nil}
		}
		if previousEnd.Float64 == *currentEnd {
			return ringInvalidationInterval{start: start, end: currentEnd}
		}
		left, right := previousEnd.Float64, *currentEnd
		if left > right {
			left, right = right, left
		}
		return ringInvalidationInterval{start: left, end: &right}
	}
	if previousEnd.Valid {
		// A closed epoch was reopened. The changed tail is unbounded.
		return ringInvalidationInterval{start: previousEnd.Float64, end: nil}
	}
	// An open epoch was closed; only the retracted tail changed.
	if currentEnd == nil {
		return ringInvalidationInterval{start: start, end: nil}
	}
	return ringInvalidationInterval{start: *currentEnd, end: nil}
}

// NextWindowEnd returns the first bucket boundary strictly after the current
// wall-clock second. Python's stats reader uses the same half-open window
// convention, including when the clock is exactly on a boundary.
func NextWindowEnd(now time.Time, resolutionSeconds int) int64 {
	if resolutionSeconds <= 0 {
		return 0
	}
	seconds := now.Unix()
	remainder := seconds % int64(resolutionSeconds)
	if remainder < 0 {
		remainder += int64(resolutionSeconds)
	}
	return seconds - remainder + int64(resolutionSeconds)
}

// RingPublicationState returns the last durable publication metadata. The
// lifecycle uses it to derive the language-neutral cache generation before
// encoding the next batch of bucket payloads.
func (w *Writer) RingPublicationState(ctx context.Context) (int64, float64, error) {
	if w == nil || w.db == nil {
		return 0, 0, fmt.Errorf("nil YO!stats writer")
	}
	var ringGeneration int64
	var publishedAt float64
	if err := w.db.QueryRowContext(ctx, "SELECT ring_generation,published_at FROM aggregate_publication WHERE singleton=1").Scan(&ringGeneration, &publishedAt); err != nil {
		return 0, 0, err
	}
	return ringGeneration, publishedAt, nil
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
	rows, err := db.QueryContext(ctx, "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('aggregate_publication','aggregate_rings','aggregate_ring_slots')")
	if err != nil {
		return err
	}
	triggers := make(map[string]bool)
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			_ = rows.Close()
			return err
		}
		triggers[name] = true
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	for _, table := range []string{"aggregate_publication", "aggregate_rings", "aggregate_ring_slots"} {
		for _, operation := range []string{"insert", "delete"} {
			name := table + "_reject_" + operation
			if !triggers[name] {
				return &ValidationError{Reason: fmt.Sprintf("ring trigger %q is missing", name)}
			}
		}
	}
	if len(triggers) != 6 {
		return &ValidationError{Reason: "ring trigger set is not exact"}
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
	// A builder publishes from one consistent read snapshot. Facts may commit
	// while it folds; those newer generations remain represented by pending
	// invalidations and must not make this valid partial publication fail.
	if sourceGeneration < oldSource || publishedAt < oldPublished {
		return rollback(fmt.Errorf("ring publication moves backwards"))
	}
	ringGeneration := oldRing + 1
	newest := map[int]int64{}
	for _, bucket := range buckets {
		slot, err := RingSlotIndex(bucket.ResolutionSeconds, bucket.BucketStart)
		if err != nil {
			return rollback(fmt.Errorf("ring bucket %d/%d: %w", bucket.ResolutionSeconds, bucket.BucketStart, err))
		}
		var displaced sql.NullInt64
		if err := tx.QueryRowContext(ctx, "SELECT bucket_start FROM aggregate_ring_slots WHERE resolution_seconds=? AND slot_index=?", bucket.ResolutionSeconds, slot).Scan(&displaced); err != nil {
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
		if displaced.Valid && displaced.Int64 != bucket.BucketStart {
			// A lapped slot can no longer be rebuilt for its old bucket. Remove
			// only that displaced bucket's pending rows; the new occupant's
			// invalidation is retired below in this same transaction.
			if _, err := tx.ExecContext(ctx, "DELETE FROM ring_invalidations WHERE resolution_seconds=? AND bucket_start=? AND applied_at IS NULL", bucket.ResolutionSeconds, displaced.Int64); err != nil {
				return rollback(err)
			}
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
	_ = w.refreshPendingCache(ctx)
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
		w.setPendingCacheUnavailable()
		return nil, err
	}
	defer rows.Close()
	result := []InvalidationCell{}
	pending := make(map[[2]int64]int64)
	for rows.Next() {
		var cell InvalidationCell
		if err := rows.Scan(&cell.ResolutionSeconds, &cell.BucketStart, &cell.SourceGeneration); err != nil {
			w.setPendingCacheUnavailable()
			return nil, err
		}
		result = append(result, cell)
		key := [2]int64{int64(cell.ResolutionSeconds), cell.BucketStart}
		if cell.SourceGeneration > pending[key] {
			pending[key] = cell.SourceGeneration
		}
	}
	if err := rows.Err(); err != nil {
		w.setPendingCacheUnavailable()
		return nil, err
	}
	w.pendingMu.Lock()
	w.pending = pending
	w.pendingReady = true
	w.pendingMu.Unlock()
	return result, nil
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
	if w == nil || w.db == nil {
		return fmt.Errorf("nil YO!stats writer")
	}
	_, err := w.db.ExecContext(ctx, "PRAGMA wal_checkpoint(PASSIVE)")
	return err
}
func (w *Writer) Vacuum(ctx context.Context, completedAt float64) error {
	if w == nil || w.db == nil {
		return fmt.Errorf("nil YO!stats writer")
	}
	if math.IsNaN(completedAt) || math.IsInf(completedAt, 0) || completedAt < 0 {
		return fmt.Errorf("invalid vacuum completion time")
	}
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	if _, err := w.db.ExecContext(ctx, "VACUUM"); err != nil {
		return err
	}
	if _, err := w.db.ExecContext(ctx, "PRAGMA optimize"); err != nil {
		return err
	}
	if _, err := w.db.ExecContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)"); err != nil {
		return err
	}
	if _, err := w.db.ExecContext(ctx, "UPDATE schema_meta SET last_vacuumed_at=? WHERE singleton=1", completedAt); err != nil {
		return err
	}
	if _, err := w.db.ExecContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)"); err != nil {
		return err
	}
	ratio, err := w.ReclaimableRatio(ctx)
	if err != nil {
		return err
	}
	return w.recordVacuumBaseline(completedAt, ratio)
}

func invalidationBucket(resolution int, timestamp float64) int64 {
	return int64(math.Floor(timestamp/float64(resolution))) * int64(resolution)
}
func recordFactInvalidations(ctx context.Context, tx *sql.Tx, generation int64, timestamps []float64, now float64) error {
	return recordRingInvalidations(ctx, tx, generation, now, "fact_mutation", timestamps, nil)
}

// recordExternalGenerationInvalidations covers a legacy/direct writer that
// advanced schema_meta without recording its dirty cells. The normal writer
// path never needs this scan: accepted mutations already provide exact points
// and intervals. Keeping the reconciliation inside the owner transaction lets
// the request path fail closed before an old product can be served.
func recordExternalGenerationInvalidations(ctx context.Context, tx *sql.Tx, generation int64, now float64) error {
	cutoff := now - float64(RetentionSeconds)
	timestamps := make([]float64, 0)
	for _, table := range []string{"observations", "usage_atoms"} {
		rows, err := tx.QueryContext(ctx, "SELECT observed_at FROM "+table+" WHERE observed_at >= ?", cutoff)
		if err != nil {
			return err
		}
		for rows.Next() {
			var timestamp float64
			if err := rows.Scan(&timestamp); err != nil {
				_ = rows.Close()
				return err
			}
			timestamps = append(timestamps, timestamp)
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return err
		}
		if err := rows.Close(); err != nil {
			return err
		}
	}
	intervals := make([]ringInvalidationInterval, 0)
	for _, table := range []string{"coverage_epochs", "unavailable_spans"} {
		rows, err := tx.QueryContext(ctx, "SELECT started_at,ended_at FROM "+table+" WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)", now, cutoff)
		if err != nil {
			return err
		}
		for rows.Next() {
			var start float64
			var end sql.NullFloat64
			if err := rows.Scan(&start, &end); err != nil {
				_ = rows.Close()
				return err
			}
			interval := ringInvalidationInterval{start: math.Max(start, cutoff)}
			if end.Valid {
				clippedEnd := math.Min(end.Float64, now)
				interval.end = &clippedEnd
			}
			intervals = append(intervals, interval)
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return err
		}
		if err := rows.Close(); err != nil {
			return err
		}
	}
	return recordRingInvalidations(ctx, tx, generation, now, "external_generation_reconciliation", timestamps, intervals)
}

// recordStaleRingInvalidations repairs the case where an older/direct owner
// advanced both source_generation and the publication watermark but omitted a
// cell-level invalidation. It is intentionally bounded to populated slots and
// runs only during writer startup; normal append paths already record exact
// dirty cells. Rebuilding an older but still valid slot is safe because the
// ring is only a cache of the original facts, while missing a contradicted
// slot would expose stale data indefinitely after the next tail append.
func recordStaleRingInvalidations(ctx context.Context, tx *sql.Tx, generation int64, now float64) error {
	rows, err := tx.QueryContext(ctx, `SELECT resolution_seconds,bucket_start
		FROM aggregate_ring_slots AS slots
		WHERE slots.bucket_json IS NOT NULL AND slots.source_generation < ?
		  AND (
			EXISTS (SELECT 1 FROM observations
				WHERE observed_at >= slots.bucket_start
				  AND observed_at < slots.bucket_start + slots.resolution_seconds)
			OR EXISTS (SELECT 1 FROM usage_atoms
				WHERE observed_at >= slots.bucket_start
				  AND observed_at < slots.bucket_start + slots.resolution_seconds)
			OR EXISTS (SELECT 1 FROM coverage_epochs
				WHERE started_at < slots.bucket_start + slots.resolution_seconds
				  AND (ended_at IS NULL OR ended_at > slots.bucket_start))
			OR EXISTS (SELECT 1 FROM unavailable_spans
				WHERE started_at < slots.bucket_start + slots.resolution_seconds
				  AND ended_at > slots.bucket_start)
		  )
		ORDER BY resolution_seconds ASC, bucket_start ASC
		LIMIT ?`, generation, maxStartupReconciliationCells)
	if err != nil {
		return err
	}
	keys := make([][2]int64, 0, maxStartupReconciliationCells)
	for rows.Next() {
		var resolution, bucket int64
		if err := rows.Scan(&resolution, &bucket); err != nil {
			_ = rows.Close()
			return err
		}
		keys = append(keys, [2]int64{resolution, bucket})
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	for _, key := range keys {
		if _, err := tx.ExecContext(ctx, `INSERT OR IGNORE INTO ring_invalidations(
			resolution_seconds,bucket_start,source_generation,reason,created_at,applied_at)
			VALUES(?,?,?,?,?,NULL)`, key[0], key[1], generation,
			"external_generation_reconciliation", now); err != nil {
			return err
		}
	}
	return nil
}

// recordRingInvalidations derives the one durable dirty-cell set for all fact
// mutations. Point facts select their own bucket at every resolution; interval
// changes intersect only currently populated half-open ring cells. The caller
// owns the surrounding fact transaction, so a committed source generation can
// never exist without the corresponding durable contradiction record.
func recordRingInvalidations(ctx context.Context, tx *sql.Tx, generation int64, now float64, reason string, timestamps []float64, intervals []ringInvalidationInterval) error {
	seen := map[[2]int64]bool{}
	for _, timestamp := range timestamps {
		if timestamp < 0 || math.IsNaN(timestamp) || math.IsInf(timestamp, 0) {
			continue
		}
		for resolution := range ringCapacities {
			bucket := invalidationBucket(resolution, timestamp)
			key := [2]int64{int64(resolution), bucket}
			seen[key] = true
		}
	}
	for _, interval := range intervals {
		if math.IsNaN(interval.start) || math.IsInf(interval.start, 0) {
			continue
		}
		start := math.Max(0, interval.start)
		if interval.end != nil && (*interval.end <= start || math.IsNaN(*interval.end) || math.IsInf(*interval.end, 0)) {
			continue
		}
		var rows *sql.Rows
		var err error
		if interval.end == nil {
			rows, err = tx.QueryContext(ctx, `SELECT resolution_seconds,bucket_start
				FROM aggregate_ring_slots
				WHERE bucket_start IS NOT NULL AND bucket_json IS NOT NULL
				  AND bucket_start + resolution_seconds > ?`, start)
		} else {
			rows, err = tx.QueryContext(ctx, `SELECT resolution_seconds,bucket_start
				FROM aggregate_ring_slots
				WHERE bucket_start IS NOT NULL AND bucket_json IS NOT NULL
				  AND bucket_start + resolution_seconds > ? AND bucket_start < ?`, start, *interval.end)
		}
		if err != nil {
			return err
		}
		for rows.Next() {
			var resolution, bucket int64
			if err := rows.Scan(&resolution, &bucket); err != nil {
				_ = rows.Close()
				return err
			}
			seen[[2]int64{resolution, bucket}] = true
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return err
		}
		_ = rows.Close()
	}
	keys := make([][2]int64, 0, len(seen))
	for key := range seen {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i][0] != keys[j][0] {
			return keys[i][0] < keys[j][0]
		}
		return keys[i][1] < keys[j][1]
	})
	for _, key := range keys {
		resolution, bucket := key[0], key[1]
		// The primary key includes generation for retired history, but only
		// the newest pending contradiction is actionable.
		if _, err := tx.ExecContext(ctx, "DELETE FROM ring_invalidations WHERE resolution_seconds=? AND bucket_start=? AND applied_at IS NULL AND source_generation<?", resolution, bucket, generation); err != nil {
			return err
		}
		if _, err := tx.ExecContext(ctx, "INSERT OR IGNORE INTO ring_invalidations(resolution_seconds,bucket_start,source_generation,reason,created_at,applied_at) SELECT ?,?,?,?,?,NULL WHERE EXISTS (SELECT 1 FROM aggregate_ring_slots WHERE resolution_seconds=? AND bucket_start=? AND bucket_json IS NOT NULL)", resolution, bucket, generation, reason, now, resolution, bucket); err != nil {
			return err
		}
	}
	return nil
}
