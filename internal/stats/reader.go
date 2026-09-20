package stats

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
)

const MaxFactReadLimit = 10000

// FactPageSize is deliberately small so a lifecycle pass never retains a
// whole history range merely to fold one ring cell. Callers may issue as many
// pages as their explicit row/byte/time budget allows.
const FactPageSize = 256

type observationPageCursor struct {
	ObservedAt       float64
	Family, SourceID string
	EventID          string
}

type coveragePageCursor struct {
	StartedAt        float64
	Family, SourceID string
	EpochID          string
}

type unavailablePageCursor struct {
	StartedAt        float64
	Family, SourceID string
	EpochID          string
	EndedAt          float64
}

type usagePageCursor struct {
	ObservedAt                   float64
	EventID, Direction, Modality string
	CacheRole, Unit              string
}

func pageLimit(limit int) error {
	if limit <= 0 || limit > MaxFactReadLimit {
		return fmt.Errorf("invalid page limit %d", limit)
	}
	return nil
}

func (d *Database) ReadObservationsPage(ctx context.Context, start, end float64, after *observationPageCursor, limit int) ([]Observation, *observationPageCursor, error) {
	if err := validateReadRange(start, end, MaxFactReadLimit); err != nil {
		return nil, nil, err
	}
	if err := pageLimit(limit); err != nil {
		return nil, nil, err
	}
	query := `SELECT event_id, family, source_id, observed_at, epoch_id, owner_generation, payload_json
FROM observations WHERE observed_at >= ? AND observed_at < ?`
	args := []any{start, end}
	if after != nil {
		query += ` AND (observed_at > ? OR (observed_at = ? AND (family > ? OR (family = ? AND (source_id > ? OR (source_id = ? AND event_id > ?))))))`
		args = append(args, after.ObservedAt, after.ObservedAt, after.Family, after.Family, after.SourceID, after.SourceID, after.EventID)
	}
	query += ` ORDER BY observed_at ASC, family ASC, source_id ASC, event_id ASC LIMIT ?`
	args = append(args, limit)
	rows, err := d.reader.Rows(ctx, query, args...)
	if err != nil {
		return nil, nil, fmt.Errorf("read observation page: %w", err)
	}
	result := make([]Observation, 0, len(rows))
	var cursor *observationPageCursor
	for i, row := range rows {
		item, decodeErr := decodeObservation(row)
		if decodeErr != nil {
			return nil, nil, fmt.Errorf("observations page row %d: %w", i, decodeErr)
		}
		result = append(result, item)
		cursor = &observationPageCursor{ObservedAt: item.ObservedAt, Family: item.Family, SourceID: item.SourceID, EventID: item.EventID}
	}
	return result, cursor, nil
}

func (d *Database) ReadCoverageEpochsPage(ctx context.Context, start, end float64, after *coveragePageCursor, limit int) ([]CoverageEpoch, *coveragePageCursor, error) {
	if err := validateReadRange(start, end, MaxFactReadLimit); err != nil {
		return nil, nil, err
	}
	if err := pageLimit(limit); err != nil {
		return nil, nil, err
	}
	query := `SELECT family, source_id, epoch_id, started_at, ended_at, native_cadence_seconds, owner_generation
FROM coverage_epochs WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)`
	args := []any{end, start}
	if after != nil {
		query += ` AND (started_at > ? OR (started_at = ? AND (family > ? OR (family = ? AND (source_id > ? OR (source_id = ? AND epoch_id > ?))))))`
		args = append(args, after.StartedAt, after.StartedAt, after.Family, after.Family, after.SourceID, after.SourceID, after.EpochID)
	}
	query += ` ORDER BY started_at ASC, family ASC, source_id ASC, epoch_id ASC LIMIT ?`
	args = append(args, limit)
	rows, err := d.reader.Rows(ctx, query, args...)
	if err != nil {
		return nil, nil, fmt.Errorf("read coverage epoch page: %w", err)
	}
	result := make([]CoverageEpoch, 0, len(rows))
	var cursor *coveragePageCursor
	for i, row := range rows {
		item, decodeErr := decodeCoverageEpoch(row)
		if decodeErr != nil {
			return nil, nil, fmt.Errorf("coverage page row %d: %w", i, decodeErr)
		}
		result = append(result, item)
		cursor = &coveragePageCursor{StartedAt: item.StartedAt, Family: item.Family, SourceID: item.SourceID, EpochID: item.EpochID}
	}
	return result, cursor, nil
}

func (d *Database) ReadUnavailableSpansPage(ctx context.Context, start, end float64, after *unavailablePageCursor, limit int) ([]UnavailableSpan, *unavailablePageCursor, error) {
	if err := validateReadRange(start, end, MaxFactReadLimit); err != nil {
		return nil, nil, err
	}
	if err := pageLimit(limit); err != nil {
		return nil, nil, err
	}
	query := `SELECT family, source_id, epoch_id, started_at, ended_at, native_cadence_seconds, reason, owner_generation
FROM unavailable_spans WHERE ended_at > ? AND started_at < ?`
	args := []any{start, end}
	if after != nil {
		query += ` AND (started_at > ? OR (started_at = ? AND (family > ? OR (family = ? AND (source_id > ? OR (source_id = ? AND (epoch_id > ? OR (epoch_id = ? AND ended_at > ?))))))))`
		args = append(args, after.StartedAt, after.StartedAt, after.Family, after.Family, after.SourceID, after.SourceID, after.EpochID, after.EpochID, after.EndedAt)
	}
	query += ` ORDER BY started_at ASC, family ASC, source_id ASC, epoch_id ASC, ended_at ASC LIMIT ?`
	args = append(args, limit)
	rows, err := d.reader.Rows(ctx, query, args...)
	if err != nil {
		return nil, nil, fmt.Errorf("read unavailable page: %w", err)
	}
	result := make([]UnavailableSpan, 0, len(rows))
	var cursor *unavailablePageCursor
	for i, row := range rows {
		item, decodeErr := decodeUnavailableSpan(row)
		if decodeErr != nil {
			return nil, nil, fmt.Errorf("unavailable page row %d: %w", i, decodeErr)
		}
		result = append(result, item)
		cursor = &unavailablePageCursor{StartedAt: item.StartedAt, Family: item.Family, SourceID: item.SourceID, EpochID: item.EpochID, EndedAt: item.EndedAt}
	}
	return result, cursor, nil
}

func (d *Database) ReadUsageAtomsPage(ctx context.Context, start, end float64, after *usagePageCursor, limit int) ([]UsageAtom, *usagePageCursor, error) {
	if err := validateReadRange(start, end, MaxFactReadLimit); err != nil {
		return nil, nil, err
	}
	if err := pageLimit(limit); err != nil {
		return nil, nil, err
	}
	query := `SELECT event_id, direction, modality, cache_role, unit, observed_at, payload_json
FROM usage_atoms WHERE observed_at >= ? AND observed_at < ?`
	args := []any{start, end}
	if after != nil {
		query += ` AND (observed_at > ? OR (observed_at = ? AND (event_id > ? OR (event_id = ? AND (direction > ? OR (direction = ? AND (modality > ? OR (modality = ? AND (cache_role > ? OR (cache_role = ? AND unit > ?))))))))))`
		args = append(args, after.ObservedAt, after.ObservedAt, after.EventID, after.EventID, after.Direction, after.Direction, after.Modality, after.Modality, after.CacheRole, after.CacheRole, after.Unit)
	}
	query += ` ORDER BY observed_at ASC, event_id ASC, direction ASC, modality ASC, cache_role ASC, unit ASC LIMIT ?`
	args = append(args, limit)
	rows, err := d.reader.Rows(ctx, query, args...)
	if err != nil {
		return nil, nil, fmt.Errorf("read usage page: %w", err)
	}
	result := make([]UsageAtom, 0, len(rows))
	var cursor *usagePageCursor
	for i, row := range rows {
		item, decodeErr := decodeUsageAtom(row)
		if decodeErr != nil {
			return nil, nil, fmt.Errorf("usage page row %d: %w", i, decodeErr)
		}
		result = append(result, item)
		cursor = &usagePageCursor{ObservedAt: item.ObservedAt, EventID: item.EventID, Direction: item.Direction, Modality: item.Modality, CacheRole: item.CacheRole, Unit: item.Unit}
	}
	return result, cursor, nil
}

// ReadObservations returns observations with start <= observed_at < end.
func (d *Database) ReadObservations(ctx context.Context, start, end float64, limit int) ([]Observation, error) {
	if err := validateReadRange(start, end, limit); err != nil {
		return nil, err
	}
	rows, err := d.reader.Rows(ctx, `SELECT event_id, family, source_id, observed_at, epoch_id, owner_generation, payload_json
FROM observations WHERE observed_at >= ? AND observed_at < ?
ORDER BY observed_at ASC, family ASC, source_id ASC, event_id ASC LIMIT ?`, start, end, limit)
	if err != nil {
		return nil, fmt.Errorf("read observations: %w", err)
	}
	result := make([]Observation, 0, len(rows))
	for i, row := range rows {
		item, err := decodeObservation(row)
		if err != nil {
			return nil, fmt.Errorf("observations row %d: %w", i, err)
		}
		result = append(result, item)
	}
	return result, nil
}

// ReadCoverageEpochs returns coverage epochs that overlap the range. An epoch
// may start before the requested window and still be the coverage that makes
// a later sparse observation meaningful.
func (d *Database) ReadCoverageEpochs(ctx context.Context, start, end float64, limit int) ([]CoverageEpoch, error) {
	if err := validateReadRange(start, end, limit); err != nil {
		return nil, err
	}
	rows, err := d.reader.Rows(ctx, `SELECT family, source_id, epoch_id, started_at, ended_at, native_cadence_seconds, owner_generation
FROM coverage_epochs WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)
ORDER BY started_at ASC, family ASC, source_id ASC, epoch_id ASC LIMIT ?`, end, start, limit)
	if err != nil {
		return nil, fmt.Errorf("read coverage epochs: %w", err)
	}
	result := make([]CoverageEpoch, 0, len(rows))
	for i, row := range rows {
		item, err := decodeCoverageEpoch(row)
		if err != nil {
			return nil, fmt.Errorf("coverage_epochs row %d: %w", i, err)
		}
		result = append(result, item)
	}
	return result, nil
}

// ReadUnavailableSpans returns unavailable spans whose start is in the range.
func (d *Database) ReadUnavailableSpans(ctx context.Context, start, end float64, limit int) ([]UnavailableSpan, error) {
	if err := validateReadRange(start, end, limit); err != nil {
		return nil, err
	}
	rows, err := d.reader.Rows(ctx, `SELECT family, source_id, epoch_id, started_at, ended_at, native_cadence_seconds, reason, owner_generation
FROM unavailable_spans WHERE ended_at > ? AND started_at < ?
ORDER BY started_at ASC, family ASC, source_id ASC, epoch_id ASC LIMIT ?`, start, end, limit)
	if err != nil {
		return nil, fmt.Errorf("read unavailable spans: %w", err)
	}
	result := make([]UnavailableSpan, 0, len(rows))
	for i, row := range rows {
		item, err := decodeUnavailableSpan(row)
		if err != nil {
			return nil, fmt.Errorf("unavailable_spans row %d: %w", i, err)
		}
		result = append(result, item)
	}
	return result, nil
}

// ReadUsageAtoms returns usage atoms with start <= observed_at < end.
func (d *Database) ReadUsageAtoms(ctx context.Context, start, end float64, limit int) ([]UsageAtom, error) {
	if err := validateReadRange(start, end, limit); err != nil {
		return nil, err
	}
	rows, err := d.reader.Rows(ctx, `SELECT event_id, direction, modality, cache_role, unit, observed_at, payload_json
FROM usage_atoms WHERE observed_at >= ? AND observed_at < ?
ORDER BY observed_at ASC, event_id ASC, direction ASC, modality ASC, cache_role ASC, unit ASC LIMIT ?`, start, end, limit)
	if err != nil {
		return nil, fmt.Errorf("read usage atoms: %w", err)
	}
	result := make([]UsageAtom, 0, len(rows))
	for i, row := range rows {
		item, err := decodeUsageAtom(row)
		if err != nil {
			return nil, fmt.Errorf("usage_atoms row %d: %w", i, err)
		}
		result = append(result, item)
	}
	return result, nil
}

func validateReadRange(start, end float64, limit int) error {
	if math.IsNaN(start) || math.IsInf(start, 0) || math.IsNaN(end) || math.IsInf(end, 0) || start >= end {
		return fmt.Errorf("invalid observed-time range")
	}
	if limit <= 0 || limit > MaxFactReadLimit {
		return fmt.Errorf("invalid read limit %d", limit)
	}
	return nil
}

func decodeObservation(row []any) (Observation, error) {
	if len(row) != 7 {
		return Observation{}, fmt.Errorf("expected 7 columns, got %d", len(row))
	}
	observed, generation, err := rowNumbers(row[3], row[5])
	if err != nil {
		return Observation{}, err
	}
	payload, payloadBytes, err := decodePayloadWithSize(row[6])
	if err != nil {
		return Observation{}, err
	}
	item := Observation{EventID: rowString(row[0]), Family: rowString(row[1]), SourceID: rowString(row[2]), ObservedAt: observed, EpochID: rowString(row[4]), OwnerGeneration: generation, Payload: payload, payloadBytes: payloadBytes}
	return item, item.Validate()
}

func decodeCoverageEpoch(row []any) (CoverageEpoch, error) {
	if len(row) != 7 {
		return CoverageEpoch{}, fmt.Errorf("expected 7 columns, got %d", len(row))
	}
	started, cadence, generation, err := rowNumbers3(row[3], row[5], row[6])
	if err != nil {
		return CoverageEpoch{}, err
	}
	item := CoverageEpoch{Family: rowString(row[0]), SourceID: rowString(row[1]), EpochID: rowString(row[2]), StartedAt: started, NativeCadenceSeconds: cadence, OwnerGeneration: generation}
	if row[4] != nil {
		value, err := number(row[4])
		if err != nil {
			return CoverageEpoch{}, err
		}
		item.EndedAt = &value
	}
	return item, item.Validate()
}

func decodeUnavailableSpan(row []any) (UnavailableSpan, error) {
	if len(row) != 8 {
		return UnavailableSpan{}, fmt.Errorf("expected 8 columns, got %d", len(row))
	}
	started, ended, cadence, generation, err := rowNumbers4(row[3], row[4], row[5], row[7])
	if err != nil {
		return UnavailableSpan{}, err
	}
	item := UnavailableSpan{Family: rowString(row[0]), SourceID: rowString(row[1]), EpochID: rowString(row[2]), StartedAt: started, EndedAt: ended, NativeCadenceSeconds: cadence, Reason: rowString(row[6]), OwnerGeneration: generation}
	return item, item.Validate()
}

func decodeUsageAtom(row []any) (UsageAtom, error) {
	if len(row) != 7 {
		return UsageAtom{}, fmt.Errorf("expected 7 columns, got %d", len(row))
	}
	observed, err := number(row[5])
	if err != nil {
		return UsageAtom{}, err
	}
	payload, payloadBytes, err := decodePayloadWithSize(row[6])
	if err != nil {
		return UsageAtom{}, err
	}
	item := UsageAtom{EventID: rowString(row[0]), Direction: rowString(row[1]), Modality: rowString(row[2]), CacheRole: rowString(row[3]), Unit: rowString(row[4]), ObservedAt: observed, Payload: payload, payloadBytes: payloadBytes}
	return item, item.Validate()
}

func decodePayload(value any) (map[string]any, error) {
	payload, _, err := decodePayloadWithSize(value)
	return payload, err
}

func decodePayloadWithSize(value any) (map[string]any, int, error) {
	var raw []byte
	switch value := value.(type) {
	case string:
		raw = []byte(value)
	case []byte:
		raw = value
	default:
		return nil, 0, fmt.Errorf("payload_json is not text")
	}
	if len(raw) == 0 || len(raw) > MaxPayloadBytes {
		return nil, 0, fmt.Errorf("invalid payload_json size")
	}
	var payload map[string]any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if err := decoder.Decode(&payload); err != nil {
		return nil, 0, fmt.Errorf("malformed payload_json: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, 0, fmt.Errorf("malformed payload_json: trailing data")
	}
	if payload == nil {
		return nil, 0, fmt.Errorf("payload_json must be a JSON object")
	}
	return payload, len(raw), nil
}

func rowString(value any) string {
	switch value := value.(type) {
	case string:
		return value
	case []byte:
		return string(value)
	default:
		return ""
	}
}
func rowNumbers(a, b any) (float64, uint64, error) {
	x, err := number(a)
	if err != nil {
		return 0, 0, err
	}
	y, err := uint64Value(b)
	return x, y, err
}
func rowNumbers3(a, b, c any) (float64, float64, uint64, error) {
	x, err := number(a)
	if err != nil {
		return 0, 0, 0, err
	}
	y, err := number(b)
	if err != nil {
		return 0, 0, 0, err
	}
	z, err := uint64Value(c)
	return x, y, z, err
}
func rowNumbers4(a, b, c, d any) (float64, float64, float64, uint64, error) {
	x, err := number(a)
	if err != nil {
		return 0, 0, 0, 0, err
	}
	y, err := number(b)
	if err != nil {
		return 0, 0, 0, 0, err
	}
	z, err := number(c)
	if err != nil {
		return 0, 0, 0, 0, err
	}
	w, err := uint64Value(d)
	return x, y, z, w, err
}
func number(value any) (float64, error) {
	switch value := value.(type) {
	case float64:
		return value, nil
	case float32:
		return float64(value), nil
	case int64:
		return float64(value), nil
	case int:
		return float64(value), nil
	case uint:
		return float64(value), nil
	case uint32:
		return float64(value), nil
	case uint64:
		return float64(value), nil
	case []byte:
		var v float64
		_, err := fmt.Sscan(string(value), &v)
		return v, err
	case string:
		var v float64
		_, err := fmt.Sscan(value, &v)
		return v, err
	default:
		return 0, fmt.Errorf("expected number, got %T", value)
	}
}
func uint64Value(value any) (uint64, error) {
	n, err := number(value)
	if err != nil || n < 0 || math.Trunc(n) != n || n > math.MaxUint64 {
		return 0, fmt.Errorf("invalid unsigned integer")
	}
	return uint64(n), nil
}
