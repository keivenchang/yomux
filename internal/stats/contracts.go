package stats

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
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
	// payloadBytes is the validated JSON size read from SQLite. Keeping this
	// decode-side fact avoids marshaling the payload again when lifecycle
	// accounting enforces its decoded-byte budget.
	payloadBytes int
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
	// payloadBytes has the same purpose as Observation.payloadBytes.
	payloadBytes int
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

// validateCurrentFamilyBatch is deliberately separate from AppendBatch.Validate.
// The database reader must continue to accept immutable legacy/sparse rows, while
// a current Go producer must not publish a payload that the Python family owner
// would reject. Writer.EnableCurrentFamilyValidation selects this write-only path.
func validateCurrentFamilyBatch(batch AppendBatch) error {
	for index, item := range batch.Observations {
		if err := validateCurrentFamilyObservation(item); err != nil {
			return fmt.Errorf("observations[%d]: %w", index, err)
		}
	}
	return nil
}

func validateCurrentFamilyObservation(item Observation) error {
	allowed, required := currentFamilyFields(item.Family)
	if allowed == nil {
		return invalid("observation", "family", "is not a current stats family")
	}
	for name := range item.Payload {
		if !allowed[name] {
			return invalid("observation", "payload", fmt.Sprintf("%s has unknown field %q", item.Family, name))
		}
	}
	for name := range required {
		if _, ok := item.Payload[name]; !ok {
			return invalid("observation", "payload", fmt.Sprintf("%s is missing field %q", item.Family, name))
		}
	}
	validateNumberField := func(name string, allowNil bool) error {
		value, present := item.Payload[name]
		if !present {
			return nil
		}
		if value == nil && allowNil {
			return nil
		}
		if !currentNonNegativeNumber(value) {
			return invalid("observation", "payload", fmt.Sprintf("%s.%s must be a non-negative finite number", item.Family, name))
		}
		return nil
	}
	for _, name := range []string{"process_percent", "system_percent", "util_percent", "memory_used_bytes", "memory_capacity_bytes", "cpu_percent", "used_bytes", "capacity_bytes", "snapshot_revision", "pressure_percent", "mac_physical_memory_bytes", "mac_memory_used_bytes", "mac_cached_files_bytes", "mac_swap_used_bytes", "mac_app_memory_bytes", "mac_wired_memory_bytes", "mac_compressed_memory_bytes", "mac_pressure_percent", "mac_pressure_level"} {
		if err := validateNumberField(name, false); err != nil {
			return err
		}
	}
	if err := validateNumberField("rss_bytes", true); err != nil {
		return err
	}
	if value, ok := item.Payload["running"]; ok {
		if _, valid := value.(bool); !valid {
			return invalid("observation", "payload", "service_load.running must be a boolean")
		}
	}
	for _, name := range []string{"label", "kind"} {
		if value, ok := item.Payload[name]; ok {
			if text, valid := value.(string); !valid || strings.TrimSpace(text) == "" {
				return invalid("observation", "payload", fmt.Sprintf("%s.%s must be a non-empty string", item.Family, name))
			}
		}
	}
	for _, name := range []string{"process_cpu_percent", "owned_process_cpu_percent", "process_memory_bytes"} {
		if value, ok := item.Payload[name]; ok {
			if err := validateCurrentProcessSeries(item.Family, name, value); err != nil {
				return err
			}
		}
	}
	if item.Family == "agent_status" {
		for _, name := range []string{"states", "session_states"} {
			if value, ok := item.Payload[name]; ok {
				if err := validateCurrentAgentStates(name, value); err != nil {
					return err
				}
			}
		}
		if value, ok := item.Payload["counts"]; ok {
			if err := validateCurrentAgentCounts(value); err != nil {
				return err
			}
		}
		if value, ok := item.Payload["agents"]; ok {
			if err := validateCurrentAgentRoster(value); err != nil {
				return err
			}
		}
	}
	if item.Family == "browser" {
		if value, ok := item.Payload["kind"]; !ok || !browserKindCurrent(value) {
			return invalid("observation", "payload", "browser.kind is unsupported")
		}
	}
	return nil
}

func currentFamilyFields(family string) (map[string]bool, map[string]bool) {
	fields := map[string][]string{
		"cpu":           {"process_percent", "system_percent", "process_cpu_percent", "owned_process_cpu_percent"},
		"agent_status":  {"states", "session_states", "snapshot_revision", "counts", "agents"},
		"gpu":           {"util_percent", "memory_used_bytes", "memory_capacity_bytes", "label"},
		"service_load":  {"running", "cpu_percent", "rss_bytes"},
		"system_memory": {"used_bytes", "capacity_bytes", "mac_physical_memory_bytes", "mac_memory_used_bytes", "mac_cached_files_bytes", "mac_swap_used_bytes", "mac_app_memory_bytes", "mac_wired_memory_bytes", "mac_compressed_memory_bytes", "mac_pressure_percent", "mac_pressure_level", "process_memory_bytes", "pressure_percent"},
		"browser":       {"kind", "latency_ms", "bytes", "duration_ms", "endpoint", "method", "request_id", "status", "journey_id", "code_revision", "browser_family", "connection_protocol", "max_concurrency", "entry_count", "input_delay_ms", "processing_ms", "presentation_delay_ms", "interaction_type", "operation_kind", "outcome", "signature", "message", "stack", "source", "provenance", "route", "event_type", "wall_time", "delivery_outcome", "line", "column", "fanout_count", "queue_ms", "connect_ms", "tls_ms", "ttfb_ms", "download_ms", "apply_render_ms", "navigation_ms", "bundle_parse_eval_ms", "first_paint_ms", "first_contentful_paint_ms", "first_api_ms", "fanout_ms", "interactive_ms", "app_ready_ms", "upload_queue_depth", "upload_drops", "upload_retries", "instrumentation_cost_ms"},
	}
	requiredFields := map[string][]string{
		"cpu": {"process_percent", "system_percent"}, "agent_status": {"states"},
		"gpu":          {"util_percent", "memory_used_bytes", "memory_capacity_bytes", "label"},
		"service_load": {"running", "cpu_percent", "rss_bytes"}, "system_memory": {"used_bytes", "capacity_bytes"}, "browser": {"kind"},
	}
	names, ok := fields[family]
	if !ok {
		return nil, nil
	}
	allowed, required := make(map[string]bool, len(names)), make(map[string]bool, len(requiredFields[family]))
	for _, name := range names {
		allowed[name] = true
	}
	for _, name := range requiredFields[family] {
		required[name] = true
	}
	return allowed, required
}

func currentNonNegativeNumber(value any) bool {
	if value == nil || reflect.ValueOf(value).Kind() == reflect.Bool {
		return false
	}
	switch number := value.(type) {
	case json.Number:
		parsed, err := number.Float64()
		return err == nil && currentFiniteNonNegative(parsed)
	case float32:
		return currentFiniteNonNegative(float64(number))
	case float64:
		return currentFiniteNonNegative(number)
	case int:
		return number >= 0
	case int8:
		return number >= 0
	case int16:
		return number >= 0
	case int32:
		return number >= 0
	case int64:
		return number >= 0
	case uint, uint8, uint16, uint32, uint64:
		return true
	default:
		return false
	}
}

func currentFiniteNonNegative(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0
}

func validateCurrentProcessSeries(family, name string, value any) error {
	rows, ok := value.(map[string]any)
	if !ok {
		return invalid("observation", "payload", fmt.Sprintf("%s.%s must be an object", family, name))
	}
	maximum := 8
	if name == "process_cpu_percent" || name == "owned_process_cpu_percent" {
		maximum = 4
	}
	if len(rows) > maximum {
		return invalid("observation", "payload", fmt.Sprintf("%s.%s has too many binaries", family, name))
	}
	for binary, metric := range rows {
		if binary == "" || len([]byte(binary)) > 64 || normalizeHostBinary(binary) != binary || !currentNonNegativeNumber(metric) {
			return invalid("observation", "payload", fmt.Sprintf("%s.%s has an invalid binary series", family, name))
		}
		if name == "process_cpu_percent" {
			value, _ := currentNumber(metric)
			if value > 100 {
				return invalid("observation", "payload", fmt.Sprintf("%s.%s must be at most 100", family, name))
			}
		}
	}
	return nil
}

func currentNumber(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := number.Float64()
		return parsed, err == nil
	case float32:
		return float64(number), true
	case float64:
		return number, true
	case int:
		return float64(number), true
	case int8:
		return float64(number), true
	case int16:
		return float64(number), true
	case int32:
		return float64(number), true
	case int64:
		return float64(number), true
	case uint:
		return float64(number), true
	case uint8:
		return float64(number), true
	case uint16:
		return float64(number), true
	case uint32:
		return float64(number), true
	case uint64:
		return float64(number), true
	default:
		return 0, false
	}
}

func validateCurrentAgentStates(field string, value any) error {
	rows, ok := value.(map[string]any)
	if !ok {
		return invalid("observation", "payload", fmt.Sprintf("agent_status.%s must be an object", field))
	}
	valid := map[string]bool{"ask": true, "run": true, "transition": true, "idle": true}
	for identity, state := range rows {
		text, textOK := state.(string)
		if strings.TrimSpace(identity) == "" || !textOK || !valid[text] {
			return invalid("observation", "payload", fmt.Sprintf("agent_status.%s contains an invalid state", field))
		}
	}
	return nil
}

func validateCurrentAgentCounts(value any) error {
	rows, ok := value.(map[string]any)
	if !ok {
		return invalid("observation", "payload", "agent_status.counts must be an object")
	}
	if len(rows) != 4 {
		return invalid("observation", "payload", "agent_status.counts must contain exactly four fields")
	}
	for _, name := range []string{"total", "running", "red", "yellow"} {
		metric, present := rows[name]
		number, valid := currentNumber(metric)
		if !present || !valid || math.Trunc(number) != number || number < 0 {
			return invalid("observation", "payload", "agent_status.counts contains an invalid count")
		}
	}
	return nil
}

func validateCurrentAgentRoster(value any) error {
	rows, ok := value.([]map[string]any)
	if !ok {
		generic, genericOK := value.([]any)
		if !genericOK {
			return invalid("observation", "payload", "agent_status.agents must be a bounded list")
		}
		rows = make([]map[string]any, 0, len(generic))
		for _, item := range generic {
			row, rowOK := item.(map[string]any)
			if !rowOK {
				return invalid("observation", "payload", "agent_status.agents contains an invalid row")
			}
			rows = append(rows, row)
		}
	}
	if len(rows) > 256 {
		return invalid("observation", "payload", "agent_status.agents exceeds its bound")
	}
	validStates := map[string]bool{"ask": true, "run": true, "transition": true, "idle": true, "error": true, "unavailable": true}
	for _, row := range rows {
		if len(row) != 5 {
			return invalid("observation", "payload", "agent_status.agents contains an invalid row")
		}
		for _, name := range []string{"id", "kind", "state", "session", "pane"} {
			if _, ok := row[name].(string); !ok || strings.TrimSpace(row[name].(string)) == "" {
				return invalid("observation", "payload", "agent_status.agents contains an invalid identity")
			}
		}
		if !validStates[row["state"].(string)] {
			return invalid("observation", "payload", "agent_status.agents contains an invalid state")
		}
	}
	return nil
}

func browserKindCurrent(value any) bool {
	kind, ok := value.(string)
	if !ok {
		return false
	}
	return map[string]bool{"api": true, "sse": true, "heartbeat": true, "disconnect": true, "page_load": true, "finder_usable": true, "interaction": true, "operation_wait": true, "long_task": true, "warning": true, "error": true, "unhandledrejection": true}[kind]
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
