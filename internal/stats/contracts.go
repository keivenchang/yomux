package stats

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
)

const (
	MaxEventIDBytes         = 512
	MaxIdentityBytes        = 256
	MaxSeriesComponentBytes = 192
	MaxPayloadBytes         = 256 * 1024
)

type Observation struct {
	EventID         string
	Family          string
	SourceID        string
	ObservedAt      float64
	EpochID         string
	OwnerGeneration uint64
	Payload         map[string]any
}

type CoverageEpoch struct {
	Family               string
	SourceID             string
	EpochID              string
	StartedAt            float64
	EndedAt              *float64
	NativeCadenceSeconds float64
	OwnerGeneration      uint64
}

type UnavailableSpan struct {
	Family               string
	SourceID             string
	EpochID              string
	StartedAt            float64
	EndedAt              float64
	NativeCadenceSeconds float64
	Reason               string
	OwnerGeneration      uint64
}

type UsageAtom struct {
	EventID    string
	Direction  string
	Modality   string
	CacheRole  string
	Unit       string
	ObservedAt float64
	Payload    map[string]any
}

type UsageTombstone struct {
	EventID    string
	Direction  string
	Modality   string
	CacheRole  string
	Unit       string
	ObservedAt float64
	Quantity   float64
	Provider   string
	Model      string
	ThreadID   string
}

type AppendBatch struct {
	Observations     []Observation
	CoverageEpochs   []CoverageEpoch
	UnavailableSpans []UnavailableSpan
	UsageAtoms       []UsageAtom
	UsageTombstones  []UsageTombstone
}

type AppendValidationResult struct {
	ObservationCount     int
	CoverageEpochCount   int
	UnavailableSpanCount int
	UsageAtomCount       int
	TombstoneCount       int
	IdempotencyKeys      []string
}

type ContractError struct {
	Record string
	Field  string
	Reason string
}

func (e *ContractError) Error() string {
	if e.Field == "" {
		return fmt.Sprintf("%s: %s", e.Record, e.Reason)
	}
	return fmt.Sprintf("%s.%s: %s", e.Record, e.Field, e.Reason)
}

func invalid(record, field, reason string) error {
	return &ContractError{Record: record, Field: field, Reason: reason}
}

func validateText(record, field, value string, maximum int) error {
	if strings.TrimSpace(value) == "" {
		return invalid(record, field, "must be a non-empty string")
	}
	if strings.IndexFunc(value, func(r rune) bool { return r < 0x20 || r == 0x7f }) >= 0 {
		return invalid(record, field, "contains control characters")
	}
	if len([]byte(value)) > maximum {
		return invalid(record, field, fmt.Sprintf("exceeds %d bytes", maximum))
	}
	return nil
}

func validateNumber(record, field string, value float64, positive bool) error {
	if math.IsNaN(value) || math.IsInf(value, 0) || value < 0 || (positive && value == 0) {
		reason := "must be finite and non-negative"
		if positive {
			reason = "must be finite and positive"
		}
		return invalid(record, field, reason)
	}
	return nil
}

func validatePayload(record, field string, payload map[string]any) error {
	if payload == nil {
		return invalid(record, field, "must be a JSON object")
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return invalid(record, field, "must contain only JSON values")
	}
	if len(raw) > MaxPayloadBytes {
		return invalid(record, field, fmt.Sprintf("exceeds %d bytes", MaxPayloadBytes))
	}
	return nil
}

func (o Observation) Validate() error {
	if err := validateText("observation", "event_id", o.EventID, MaxEventIDBytes); err != nil {
		return err
	}
	if err := validateText("observation", "family", o.Family, MaxIdentityBytes); err != nil {
		return err
	}
	if err := validateText("observation", "source_id", o.SourceID, MaxSeriesComponentBytes); err != nil {
		return err
	}
	if err := validateNumber("observation", "observed_at", o.ObservedAt, false); err != nil {
		return err
	}
	if err := validateText("observation", "epoch_id", o.EpochID, MaxSeriesComponentBytes); err != nil {
		return err
	}
	return validatePayload("observation", "payload", o.Payload)
}

func (c CoverageEpoch) Validate() error {
	if err := validateText("coverage_epoch", "family", c.Family, MaxIdentityBytes); err != nil {
		return err
	}
	if err := validateText("coverage_epoch", "source_id", c.SourceID, MaxSeriesComponentBytes); err != nil {
		return err
	}
	if err := validateText("coverage_epoch", "epoch_id", c.EpochID, MaxSeriesComponentBytes); err != nil {
		return err
	}
	if err := validateNumber("coverage_epoch", "started_at", c.StartedAt, false); err != nil {
		return err
	}
	if c.EndedAt != nil {
		if err := validateNumber("coverage_epoch", "ended_at", *c.EndedAt, false); err != nil {
			return err
		}
		if *c.EndedAt < c.StartedAt {
			return invalid("coverage_epoch", "ended_at", "must not precede started_at")
		}
	}
	return validateNumber("coverage_epoch", "native_cadence_seconds", c.NativeCadenceSeconds, true)
}

func (s UnavailableSpan) Validate() error {
	if err := validateText("unavailable_span", "family", s.Family, MaxIdentityBytes); err != nil {
		return err
	}
	if err := validateText("unavailable_span", "source_id", s.SourceID, MaxSeriesComponentBytes); err != nil {
		return err
	}
	if err := validateText("unavailable_span", "epoch_id", s.EpochID, MaxSeriesComponentBytes); err != nil {
		return err
	}
	if err := validateNumber("unavailable_span", "started_at", s.StartedAt, false); err != nil {
		return err
	}
	if err := validateNumber("unavailable_span", "ended_at", s.EndedAt, false); err != nil {
		return err
	}
	if s.EndedAt <= s.StartedAt {
		return invalid("unavailable_span", "ended_at", "must follow started_at")
	}
	if err := validateNumber("unavailable_span", "native_cadence_seconds", s.NativeCadenceSeconds, true); err != nil {
		return err
	}
	return validateText("unavailable_span", "reason", s.Reason, MaxIdentityBytes)
}

func (a UsageAtom) Validate() error {
	if err := validateText("usage_atom", "event_id", a.EventID, MaxEventIDBytes); err != nil {
		return err
	}
	for field, value := range map[string]string{"direction": a.Direction, "modality": a.Modality, "cache_role": a.CacheRole, "unit": a.Unit} {
		if err := validateText("usage_atom", field, value, MaxIdentityBytes); err != nil {
			return err
		}
	}
	if err := validateNumber("usage_atom", "observed_at", a.ObservedAt, false); err != nil {
		return err
	}
	return validatePayload("usage_atom", "payload", a.Payload)
}

func (t UsageTombstone) Validate() error {
	if err := validateText("usage_tombstone", "event_id", t.EventID, MaxEventIDBytes); err != nil {
		return err
	}
	for field, value := range map[string]string{"direction": t.Direction, "modality": t.Modality, "cache_role": t.CacheRole, "unit": t.Unit, "provider": t.Provider, "model": t.Model, "thread_id": t.ThreadID} {
		if err := validateText("usage_tombstone", field, value, MaxIdentityBytes); err != nil {
			return err
		}
	}
	if err := validateNumber("usage_tombstone", "observed_at", t.ObservedAt, false); err != nil {
		return err
	}
	if err := validateNumber("usage_tombstone", "quantity", t.Quantity, false); err != nil {
		return err
	}
	if t.Provider != "openai" || !strings.HasPrefix(t.EventID, "codex:"+t.ThreadID+":") {
		return invalid("usage_tombstone", "event_id", "must identify Codex fork history")
	}
	return nil
}

func (o Observation) IdempotencyKey() string {
	return "observation:" + o.Family + "\x00" + o.SourceID + "\x00" + o.EventID
}
func (a UsageAtom) IdempotencyKey() string {
	return "usage:" + strings.Join([]string{a.EventID, a.Direction, a.Modality, a.CacheRole, a.Unit}, "\x00")
}
func (t UsageTombstone) IdempotencyKey() string {
	return "tombstone:" + t.EventID + "\x00" + t.Direction + "\x00" + t.Modality + "\x00" + t.CacheRole + "\x00" + t.Unit
}

func (b AppendBatch) Validate() (AppendValidationResult, error) {
	result := AppendValidationResult{
		ObservationCount: len(b.Observations), CoverageEpochCount: len(b.CoverageEpochs),
		UnavailableSpanCount: len(b.UnavailableSpans), UsageAtomCount: len(b.UsageAtoms), TombstoneCount: len(b.UsageTombstones),
	}
	keys := make(map[string]string)
	addKey := func(key, fingerprint string) error {
		if previous, ok := keys[key]; ok {
			if previous != fingerprint {
				return invalid("append_batch", "identity", "conflicting duplicate idempotency key")
			}
			return invalid("append_batch", "identity", "duplicate idempotency key")
		}
		keys[key] = fingerprint
		result.IdempotencyKeys = append(result.IdempotencyKeys, key)
		return nil
	}
	for i, item := range b.Observations {
		if err := item.Validate(); err != nil {
			return result, fmt.Errorf("observations[%d]: %w", i, err)
		}
		if err := addKey(item.IdempotencyKey(), fmt.Sprintf("%#v", item)); err != nil {
			return result, err
		}
	}
	for i, item := range b.UsageAtoms {
		if err := item.Validate(); err != nil {
			return result, fmt.Errorf("usage_atoms[%d]: %w", i, err)
		}
		if err := addKey(item.IdempotencyKey(), fmt.Sprintf("%#v", item)); err != nil {
			return result, err
		}
	}
	for i, item := range b.UsageTombstones {
		if err := item.Validate(); err != nil {
			return result, fmt.Errorf("usage_tombstones[%d]: %w", i, err)
		}
		if err := addKey(item.IdempotencyKey(), fmt.Sprintf("%#v", item)); err != nil {
			return result, err
		}
	}
	for i, item := range b.CoverageEpochs {
		if err := item.Validate(); err != nil {
			return result, fmt.Errorf("coverage_epochs[%d]: %w", i, err)
		}
	}
	for i, item := range b.UnavailableSpans {
		if err := item.Validate(); err != nil {
			return result, fmt.Errorf("unavailable_spans[%d]: %w", i, err)
		}
	}
	if err := validateIntervals(b.CoverageEpochs, b.UnavailableSpans); err != nil {
		return result, err
	}
	sort.Strings(result.IdempotencyKeys)
	return result, nil
}

func validateIntervals(coverage []CoverageEpoch, unavailable []UnavailableSpan) error {
	type interval struct {
		start, end float64
		open       bool
	}
	bySource := map[string][]interval{}
	for _, c := range coverage {
		end, open := math.Inf(1), c.EndedAt == nil
		if c.EndedAt != nil {
			end = *c.EndedAt
		}
		bySource[c.Family+"\x00"+c.SourceID] = append(bySource[c.Family+"\x00"+c.SourceID], interval{c.StartedAt, end, open})
	}
	for _, s := range unavailable {
		bySource[s.Family+"\x00"+s.SourceID] = append(bySource[s.Family+"\x00"+s.SourceID], interval{s.StartedAt, s.EndedAt, false})
	}
	for source, intervals := range bySource {
		sort.Slice(intervals, func(i, j int) bool { return intervals[i].start < intervals[j].start })
		for i := 1; i < len(intervals); i++ {
			if intervals[i].start < intervals[i-1].end {
				return invalid("append_batch", "interval", "overlapping coverage or unavailable spans for "+source)
			}
		}
	}
	return nil
}
