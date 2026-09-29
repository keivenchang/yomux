package stats

import (
	"bytes"
	"compress/zlib"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	RingPayloadVersion        = 2
	MaxRingBucketBytes        = 8 * 1024 * 1024
	RetentionSeconds          = 2 * 24 * 60 * 60
	RetiredInvalidationLimit  = 5000
	WALAutocheckpointPages    = 1000
	WALAllocationCeilingBytes = 8 * 1024 * 1024
	// Keep both reconciliation statements below SQLite's portable bind-variable
	// limit. Each candidate contributes two variables, plus the generation/reason
	// fields on the insert and the generation predicate on the delete.
	maxRingInvalidationStatementKeys = 400
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

type ringCompressionState struct {
	buffer bytes.Buffer
	writer *zlib.Writer
}

// Ring publication can encode hundreds of buckets in one bounded pass. Reuse
// the zlib state between buckets so compressor setup and its backing buffers do
// not become another source of allocation and GC work.
var ringCompressionPool = sync.Pool{New: func() any {
	state := &ringCompressionState{}
	state.writer, _ = zlib.NewWriterLevel(&state.buffer, zlib.BestSpeed)
	return state
}}

type ringZlibReader interface {
	io.ReadCloser
	zlib.Resetter
}

// The reader is reset for each payload and returned only after Close, so a
// malformed or oversized payload cannot be observed by another decode.
var ringDecompressionPool sync.Pool

type RingBucketRow struct {
	ResolutionSeconds                int
	BucketStart                      int64
	BucketJSON                       string
	Complete                         bool
	SourceGeneration, RingGeneration int64
	PublishedAt                      float64
}
type ringBucketStoredPayload struct {
	Value                            string
	Complete                         bool
	SourceGeneration, RingGeneration int64
	PublishedAt                      float64
	PayloadVersion                   int
}

func ringBucketPayloadMatchesMetadata(row RingBucketRow, payload ringBucketStoredPayload) bool {
	return row.Complete == payload.Complete && row.SourceGeneration == payload.SourceGeneration &&
		row.RingGeneration == payload.RingGeneration && row.PublishedAt == payload.PublishedAt &&
		payload.PayloadVersion == RingPayloadVersion
}

type RingPublication struct {
	RingGeneration, SourceGeneration int64
	PublishedAt                      float64
	BucketsUpdated                   int
}
type WALCheckpointResult struct {
	Busy, LogFrames, CheckpointedFrames int64
}
type RingWindow struct {
	RangeSeconds, ResolutionSeconds  int
	WindowStart, WindowEnd           int64
	Rows                             []RingBucketRow
	PendingRows                      []RingBucketRow
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
	raw := []byte(value)
	if len(raw) > MaxRingBucketBytes {
		return nil, fmt.Errorf("ring bucket exceeds %d bytes", MaxRingBucketBytes)
	}
	var object map[string]any
	if err := json.Unmarshal(raw, &object); err != nil || object == nil {
		return nil, fmt.Errorf("ring bucket must be a JSON object")
	}
	state := ringCompressionPool.Get().(*ringCompressionState)
	state.buffer.Reset()
	state.buffer.WriteString("YRB1\x00")
	state.writer.Reset(&state.buffer)
	if _, err := state.writer.Write(raw); err != nil {
		ringCompressionPool.Put(state)
		return nil, err
	}
	if err := state.writer.Close(); err != nil {
		ringCompressionPool.Put(state)
		return nil, err
	}
	compressed := append([]byte(nil), state.buffer.Bytes()...)
	ringCompressionPool.Put(state)
	if len(compressed) < len(raw) {
		return compressed, nil
	}
	return value, nil
}

func decodeRingBucket(value any) (string, error) {
	switch raw := value.(type) {
	case string:
		// Raw payload reads preserve compressed SQLite blobs in the string field
		// used by RingBucketRow. Detect the framing marker before treating the
		// value as already-decoded JSON; ordinary JSON cannot begin with YRB1.
		if len(raw) >= 5 && raw[:5] == "YRB1\x00" {
			return decodeRingBucket([]byte(raw))
		}
		return raw, nil
	case []byte:
		if len(raw) < 5 || string(raw[:5]) != "YRB1\x00" {
			return "", fmt.Errorf("invalid ring bucket encoding")
		}
		compressed := bytes.NewReader(raw[5:])
		var zr ringZlibReader
		if pooled := ringDecompressionPool.Get(); pooled != nil {
			candidate := pooled.(ringZlibReader)
			if err := candidate.Reset(compressed, nil); err == nil {
				zr = candidate
			}
		}
		if zr == nil {
			reader, err := zlib.NewReader(compressed)
			if err != nil {
				return "", fmt.Errorf("invalid ring bucket compression: %w", err)
			}
			var ok bool
			zr, ok = reader.(ringZlibReader)
			if !ok {
				_ = reader.Close()
				return "", fmt.Errorf("invalid ring bucket compression reader")
			}
		}
		limited := io.LimitReader(zr, MaxRingBucketBytes+1)
		decoded, readErr := io.ReadAll(limited)
		closeErr := zr.Close()
		ringDecompressionPool.Put(zr)
		if readErr != nil || closeErr != nil || len(decoded) > MaxRingBucketBytes || compressed.Len() != 0 {
			return "", fmt.Errorf("invalid ring bucket compression")
		}
		return string(decoded), nil
	default:
		return "", fmt.Errorf("invalid ring bucket value %T", value)
	}
}

func rawRingBucketString(value any) (string, error) {
	switch raw := value.(type) {
	case string:
		return raw, nil
	case []byte:
		return string(raw), nil
	default:
		return "", fmt.Errorf("invalid ring bucket value %T", value)
	}
}

func validateRingShape(ctx context.Context, db *sql.DB, requireTriggers bool) error {
	tableRows, err := db.QueryContext(ctx, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
	if err != nil {
		return err
	}
	tableNames := make([][]any, 0, len(coreColumns)+len(ringColumns))
	for tableRows.Next() {
		var name string
		if err := tableRows.Scan(&name); err != nil {
			_ = tableRows.Close()
			return err
		}
		tableNames = append(tableNames, []any{name})
	}
	if err := tableRows.Err(); err != nil {
		_ = tableRows.Close()
		return err
	}
	if err := tableRows.Close(); err != nil {
		return err
	}
	if !requireTriggers {
		ringTables := 0
		for _, row := range tableNames {
			name, _ := row[0].(string)
			if _, exists := ringColumns[name]; exists {
				ringTables++
			}
		}
		if ringTables == 0 {
			return validateExactTableNames(tableNames, false)
		}
	}
	if err := validateExactTableNames(tableNames, true); err != nil {
		return err
	}
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
	var publicationCount int
	var publicationSingleton int
	if err := db.QueryRowContext(ctx, "SELECT count(*), COALESCE(min(singleton),0) FROM aggregate_publication").Scan(&publicationCount, &publicationSingleton); err != nil || publicationCount != 1 || publicationSingleton != 1 {
		return &ValidationError{Reason: "aggregate publication row set is invalid"}
	}
	resolutions := make([]int, 0, len(ringCapacities))
	for resolution := range ringCapacities {
		resolutions = append(resolutions, resolution)
	}
	sort.Ints(resolutions)
	for _, table := range []string{"aggregate_rings", "ring_replay_cursor"} {
		rows, err := db.QueryContext(ctx, "SELECT resolution_seconds FROM "+table+" ORDER BY resolution_seconds")
		if err != nil {
			return err
		}
		valid := true
		for _, want := range resolutions {
			var got int
			if !rows.Next() || rows.Scan(&got) != nil || got != want {
				valid = false
				break
			}
		}
		if valid && rows.Next() {
			valid = false
		}
		if err := rows.Err(); err != nil {
			valid = false
		}
		_ = rows.Close()
		if !valid {
			return &ValidationError{Reason: fmt.Sprintf("ring table %q has invalid fixed rows", table)}
		}
	}
	ringRows, err := db.QueryContext(ctx, "SELECT resolution_seconds, slot_count, newest_bucket_start FROM aggregate_rings ORDER BY resolution_seconds")
	if err != nil {
		return err
	}
	for _, resolution := range resolutions {
		var gotResolution, gotCapacity int
		var newest sql.NullInt64
		if !ringRows.Next() || ringRows.Scan(&gotResolution, &gotCapacity, &newest) != nil || gotResolution != resolution || gotCapacity != ringCapacities[resolution] {
			_ = ringRows.Close()
			return &ValidationError{Reason: "aggregate rings do not match the exact capacities"}
		}
		if newest.Valid && newest.Int64%int64(resolution) != 0 {
			_ = ringRows.Close()
			return &ValidationError{Reason: "aggregate ring head is not resolution-aligned"}
		}
	}
	if ringRows.Next() || ringRows.Err() != nil {
		_ = ringRows.Close()
		return &ValidationError{Reason: "aggregate rings do not match the exact capacities"}
	}
	_ = ringRows.Close()
	slotCounts, err := db.QueryContext(ctx, "SELECT resolution_seconds,count(*) FROM aggregate_ring_slots GROUP BY resolution_seconds ORDER BY resolution_seconds")
	if err != nil {
		return err
	}
	for _, resolution := range resolutions {
		var gotResolution, gotCount int
		if !slotCounts.Next() || slotCounts.Scan(&gotResolution, &gotCount) != nil || gotResolution != resolution || gotCount != ringCapacities[resolution] {
			_ = slotCounts.Close()
			return &ValidationError{Reason: "aggregate ring slot counts do not match the exact capacities"}
		}
	}
	if slotCounts.Next() || slotCounts.Err() != nil {
		_ = slotCounts.Close()
		return &ValidationError{Reason: "aggregate ring slot counts do not match the exact capacities"}
	}
	_ = slotCounts.Close()
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
		if rows.Next() || rows.Err() != nil {
			_ = rows.Close()
			return &ValidationError{Reason: fmt.Sprintf("ring %ds slots are not contiguous", resolution)}
		}
		_ = rows.Close()
	}
	rows, err := db.QueryContext(ctx, "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN (SELECT name FROM sqlite_master WHERE type='table')")
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
	expectedTriggers := ringTriggerNames()
	for name := range triggers {
		if _, expected := expectedTriggers[name]; !expected {
			return &ValidationError{Reason: "ring trigger set is not exact"}
		}
	}
	if requireTriggers {
		for name := range expectedTriggers {
			if !triggers[name] {
				return &ValidationError{Reason: fmt.Sprintf("ring trigger %q is missing", name)}
			}
		}
		if len(triggers) != len(expectedTriggers) {
			return &ValidationError{Reason: "ring trigger set is not exact"}
		}
	}
	return nil
}

func ringTriggerNames() map[string]struct{} {
	result := make(map[string]struct{}, 6)
	for _, table := range []string{"aggregate_publication", "aggregate_rings", "aggregate_ring_slots"} {
		for _, operation := range []string{"insert", "delete"} {
			result[table+"_reject_"+operation] = struct{}{}
		}
	}
	return result
}

func (w *Writer) PublishRingBuckets(ctx context.Context, buckets []RingBucketWrite, sourceGeneration int64, publishedAt float64, advancePublication bool) (RingPublication, error) {
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
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

// InvalidateCorruptRingPayload records repair work only while the exact
// published slot observed by a reader is still current. The byte comparison
// and both generations fence a concurrent replacement so a stale decode error
// cannot invalidate a healthy publication.
func (w *Writer) InvalidateCorruptRingPayload(ctx context.Context, row RingBucketRow, payload string, now float64) (bool, error) {
	if w == nil || w.db == nil {
		return false, errors.New("nil YO!stats writer")
	}
	if _, ok := ringCapacities[row.ResolutionSeconds]; !ok || row.BucketStart < 0 || row.BucketStart%int64(row.ResolutionSeconds) != 0 || row.SourceGeneration < 0 || row.RingGeneration <= 0 || math.IsNaN(now) || math.IsInf(now, 0) || now < 0 {
		return false, errors.New("invalid corrupt ring payload identity")
	}
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	result, err := w.db.ExecContext(ctx, `INSERT INTO ring_invalidations(
		resolution_seconds,bucket_start,source_generation,reason,created_at,applied_at)
		SELECT ?,?,MAX(meta.source_generation,?),'corrupt_ring_payload',?,NULL
		FROM schema_meta AS meta, aggregate_ring_slots AS slots
		WHERE meta.singleton=1 AND slots.resolution_seconds=? AND slots.bucket_start=?
		  AND slots.ring_generation=? AND slots.source_generation=? AND slots.bucket_json IS NOT NULL
		  AND CAST(slots.bucket_json AS BLOB)=CAST(? AS BLOB)
		ON CONFLICT(resolution_seconds,bucket_start,source_generation) DO UPDATE SET
			reason=excluded.reason,created_at=excluded.created_at,applied_at=NULL`,
		row.ResolutionSeconds, row.BucketStart, row.SourceGeneration, now,
		row.ResolutionSeconds, row.BucketStart, row.RingGeneration, row.SourceGeneration, payload)
	if err != nil {
		return false, fmt.Errorf("record corrupt ring payload invalidation: %w", err)
	}
	changed, err := result.RowsAffected()
	if err != nil {
		return false, fmt.Errorf("count corrupt ring payload invalidation: %w", err)
	}
	if changed == 0 {
		return false, nil
	}
	if err := w.refreshPendingCache(ctx); err != nil {
		return false, fmt.Errorf("refresh corrupt ring payload invalidation: %w", err)
	}
	return true, nil
}

func (w *Writer) ReadRingWindow(ctx context.Context, rangeSeconds, resolutionSeconds int, windowEnd int64) (RingWindow, error) {
	return w.readRingWindow(ctx, rangeSeconds, resolutionSeconds, windowEnd, true, true)
}

// ReadRingWindowMetadata returns the ring state needed by lifecycle scheduling without
// decoding bucket payloads. The scheduler only needs slot identity, completeness, and
// source generations to choose repair work; decompressing every cached bucket on every
// one-second pass made background scheduling pay the full product-read cost repeatedly.
func (w *Writer) ReadRingWindowMetadata(ctx context.Context, rangeSeconds, resolutionSeconds int, windowEnd int64) (RingWindow, error) {
	return w.readRingWindow(ctx, rangeSeconds, resolutionSeconds, windowEnd, false, false)
}

// ReadRingWindowPayloads returns the published slot bytes without decoding the
// optional YRB1 compression wrapper. StatsService uses the slot generation as
// its cache key and only needs to decompress payloads whose generation changed;
// decoding every retained bucket on each one-second publication was redundant
// CPU and allocation work even when the immutable product cache was unchanged.
func (w *Writer) ReadRingWindowPayloads(ctx context.Context, rangeSeconds, resolutionSeconds int, windowEnd int64) (RingWindow, error) {
	return w.readRingWindow(ctx, rangeSeconds, resolutionSeconds, windowEnd, true, false)
}

func (w *Writer) readRingWindow(ctx context.Context, rangeSeconds, resolutionSeconds int, windowEnd int64, includePayload, decodePayload bool) (RingWindow, error) {
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
	pendingBuckets, err := w.pendingRingBuckets(ctx, resolutionSeconds)
	if err != nil {
		return result, err
	}
	columns := "slot_index,bucket_start,complete,source_generation,ring_generation,published_at,payload_version"
	if includePayload {
		columns = "slot_index,bucket_start,bucket_json," + columns[len("slot_index,bucket_start,"):]
	}
	rows, err := w.db.QueryContext(ctx, "SELECT "+columns+" FROM aggregate_ring_slots WHERE resolution_seconds=? AND bucket_start IS NOT NULL AND bucket_start>=? AND bucket_start<?", resolutionSeconds, start, windowEnd)
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
		var scanErr error
		if includePayload {
			scanErr = rows.Scan(&slot, &bucket, &payload, &complete, &source, &ring, &published, &payloadVersion)
		} else {
			scanErr = rows.Scan(&slot, &bucket, &complete, &source, &ring, &published, &payloadVersion)
		}
		if scanErr != nil {
			return result, scanErr
		}
		if !bucket.Valid || payloadVersion != RingPayloadVersion {
			continue
		}
		decoded := ""
		if includePayload {
			if decodePayload {
				decoded, err = decodeRingBucket(payload)
				if err != nil {
					return result, err
				}
			} else {
				decoded, err = rawRingBucketString(payload)
				if err != nil {
					return result, err
				}
			}
		}
		slots[bucket.Int64] = RingBucketRow{resolutionSeconds, bucket.Int64, decoded, complete != 0, source, ring, published}
	}
	for bucket := start; bucket < windowEnd; bucket += int64(resolutionSeconds) {
		if _, pending := pendingBuckets[bucket]; pending {
			result.MissingBucketStarts = append(result.MissingBucketStarts, bucket)
			result.PendingInvalidations = append(result.PendingInvalidations, bucket)
			if row, exists := slots[bucket]; exists {
				result.PendingRows = append(result.PendingRows, row)
				if row.RingGeneration > result.RingGeneration {
					result.RingGeneration, result.SourceGeneration, result.PublishedAt = row.RingGeneration, row.SourceGeneration, row.PublishedAt
				}
			}
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

// readRingBucketPayloads fetches only the slot payloads whose metadata changed
// since the service's last immutable product. Metadata and pending state stay
// bounded by the fixed ring shape; this second query avoids copying every
// compressed bucket through cgo on each one-second publication.
func (w *Writer) readRingBucketPayloads(ctx context.Context, resolution int, bucketStarts []int64) (map[int64]ringBucketStoredPayload, error) {
	payloads := make(map[int64]ringBucketStoredPayload, len(bucketStarts))
	if len(bucketStarts) == 0 {
		return payloads, nil
	}
	if _, ok := ringCapacities[resolution]; !ok {
		return nil, fmt.Errorf("unsupported ring resolution %d", resolution)
	}
	unique := make([]int64, 0, len(bucketStarts))
	seen := make(map[int64]struct{}, len(bucketStarts))
	for _, bucket := range bucketStarts {
		if _, exists := seen[bucket]; exists {
			continue
		}
		seen[bucket] = struct{}{}
		unique = append(unique, bucket)
	}
	placeholders := strings.TrimRight(strings.Repeat("?,", len(unique)), ",")
	args := make([]any, 0, len(unique)+1)
	args = append(args, resolution)
	for _, bucket := range unique {
		args = append(args, bucket)
	}
	rows, err := w.db.QueryContext(ctx, "SELECT bucket_start,bucket_json,complete,source_generation,ring_generation,published_at,payload_version FROM aggregate_ring_slots WHERE resolution_seconds=? AND bucket_start IN ("+placeholders+")", args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var bucket int64
		var raw any
		var payload ringBucketStoredPayload
		if err := rows.Scan(&bucket, &raw, &payload.Complete, &payload.SourceGeneration, &payload.RingGeneration, &payload.PublishedAt, &payload.PayloadVersion); err != nil {
			return nil, err
		}
		value, err := rawRingBucketString(raw)
		if err != nil {
			return nil, err
		}
		payload.Value = value
		payloads[bucket] = payload
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return payloads, nil
}

// pendingRingBuckets reads the indexed contradiction set once per resolution.
// ReadRingWindow is called for every public range on every lifecycle refresh;
// querying ring_invalidations once per bucket multiplied that fixed matrix into
// thousands of SQLite round trips per refresh.
func (w *Writer) pendingRingBuckets(ctx context.Context, resolutionSeconds int) (map[int64]struct{}, error) {
	rows, err := w.db.QueryContext(ctx, "SELECT bucket_start FROM ring_invalidations WHERE resolution_seconds=? AND applied_at IS NULL", resolutionSeconds)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	pending := make(map[int64]struct{})
	for rows.Next() {
		var bucket int64
		if err := rows.Scan(&bucket); err != nil {
			return nil, err
		}
		pending[bucket] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return pending, nil
}

// Checkpoint runs a non-blocking PASSIVE checkpoint and reports its progress.
// A successful call may leave frames uncheckpointed while readers pin an older
// WAL snapshot; callers that require a complete checkpoint must inspect the
// returned frame counts or use the stricter truncateStatsWAL path.
func (w *Writer) Checkpoint(ctx context.Context) (WALCheckpointResult, error) {
	if w == nil || w.db == nil {
		return WALCheckpointResult{}, fmt.Errorf("nil YO!stats writer")
	}
	var result WALCheckpointResult
	if err := w.db.QueryRowContext(ctx, "PRAGMA wal_checkpoint(PASSIVE)").Scan(&result.Busy, &result.LogFrames, &result.CheckpointedFrames); err != nil {
		return WALCheckpointResult{}, err
	}
	return result, nil
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
	if err := truncateStatsWAL(ctx, w.db); err != nil {
		return fmt.Errorf("truncate stats WAL after vacuum optimization: %w", err)
	}
	if _, err := w.db.ExecContext(ctx, "UPDATE schema_meta SET last_vacuumed_at=? WHERE singleton=1", completedAt); err != nil {
		return err
	}
	if err := truncateStatsWAL(ctx, w.db); err != nil {
		return fmt.Errorf("truncate stats WAL after vacuum completion: %w", err)
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

// InvalidatePricingCatalog advances the source generation and dirties every
// populated ring cell containing a usage atom. Cost is derived from the live
// pricing catalog rather than stored as an immutable fact, so a catalog change
// must rebuild existing products before they are served again.
func (w *Writer) InvalidatePricingCatalog(ctx context.Context, now float64) (int64, error) {
	if w == nil || w.db == nil {
		return 0, errors.New("nil YO!stats writer")
	}
	if math.IsNaN(now) || math.IsInf(now, 0) || now < 0 {
		return 0, errors.New("invalid pricing invalidation time")
	}
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	tx, err := w.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	rollback := func(cause error) (int64, error) {
		_ = tx.Rollback()
		return 0, cause
	}
	var previous int64
	if err := tx.QueryRowContext(ctx, "SELECT source_generation FROM schema_meta WHERE singleton=1").Scan(&previous); err != nil {
		return rollback(fmt.Errorf("read pricing source generation: %w", err))
	}
	generation := previous + 1
	if _, err := tx.ExecContext(ctx, "UPDATE schema_meta SET source_generation=? WHERE singleton=1", generation); err != nil {
		return rollback(fmt.Errorf("advance pricing source generation: %w", err))
	}
	rows, err := tx.QueryContext(ctx, "SELECT observed_at FROM usage_atoms WHERE observed_at >= ?", now-float64(RetentionSeconds))
	if err != nil {
		return rollback(fmt.Errorf("read usage atoms for pricing invalidation: %w", err))
	}
	timestamps := make([]float64, 0)
	for rows.Next() {
		var observedAt float64
		if err := rows.Scan(&observedAt); err != nil {
			_ = rows.Close()
			return rollback(fmt.Errorf("scan usage atom for pricing invalidation: %w", err))
		}
		timestamps = append(timestamps, observedAt)
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return rollback(fmt.Errorf("read usage atoms for pricing invalidation: %w", err))
	}
	if err := rows.Close(); err != nil {
		return rollback(fmt.Errorf("close usage atoms for pricing invalidation: %w", err))
	}
	if err := recordRingInvalidations(ctx, tx, generation, now, "pricing_catalog_refresh", timestamps, nil); err != nil {
		return rollback(fmt.Errorf("record pricing invalidations: %w", err))
	}
	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("commit pricing invalidation: %w", err)
	}
	w.sourceGeneration.Store(generation)
	if err := w.refreshPendingCache(ctx); err != nil {
		return generation, fmt.Errorf("refresh pricing invalidation cache: %w", err)
	}
	if w.lifecycle != nil {
		w.lifecycle.NotifyAppend()
	}
	return generation, nil
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
	if len(keys) == 0 {
		return nil
	}
	// The primary key includes generation, but only the newest pending
	// contradiction is actionable. Delete older pending rows and insert the
	// complete candidate set in two statements instead of preparing and
	// executing two SQLite statements per ring cell. Reconciliation can discover
	// every ring slot at startup, so keep each statement below SQLite's portable
	// bind-variable limit.
	for start := 0; start < len(keys); start += maxRingInvalidationStatementKeys {
		end := start + maxRingInvalidationStatementKeys
		if end > len(keys) {
			end = len(keys)
		}
		chunk := keys[start:end]
		rowValues := strings.TrimRight(strings.Repeat("(?,?),", len(chunk)), ",")
		deleteArgs := make([]any, 0, len(chunk)*2+1)
		deleteArgs = append(deleteArgs, generation)
		for _, key := range chunk {
			deleteArgs = append(deleteArgs, key[0], key[1])
		}
		if _, err := tx.ExecContext(ctx, "DELETE FROM ring_invalidations WHERE applied_at IS NULL AND source_generation<? AND (resolution_seconds,bucket_start) IN ("+rowValues+")", deleteArgs...); err != nil {
			return err
		}
		insertArgs := make([]any, 0, len(chunk)*2+3)
		for _, key := range chunk {
			insertArgs = append(insertArgs, key[0], key[1])
		}
		insertArgs = append(insertArgs, generation, reason, now)
		if _, err := tx.ExecContext(ctx, "WITH candidates(resolution_seconds,bucket_start) AS (VALUES "+rowValues+") INSERT OR IGNORE INTO ring_invalidations(resolution_seconds,bucket_start,source_generation,reason,created_at,applied_at) SELECT candidates.resolution_seconds,candidates.bucket_start,?,?,?,NULL FROM candidates WHERE EXISTS (SELECT 1 FROM aggregate_ring_slots AS slots WHERE slots.resolution_seconds=candidates.resolution_seconds AND slots.bucket_start=candidates.bucket_start AND slots.bucket_json IS NOT NULL)", insertArgs...); err != nil {
			return err
		}
	}
	return nil
}
