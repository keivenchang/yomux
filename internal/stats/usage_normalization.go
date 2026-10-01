package stats

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
)

const (
	MaxUsageMetadataEntries = 16
	MaxUsageMetadataBytes   = 2048
	MaxUsageInputEntries    = 32
)

// UsageMetadata identifies the provider context without coupling normalization
// to a transcript or collector implementation.
type UsageMetadata struct {
	Provider          string
	Model             string
	ModelEvidence     string
	Effort            string
	PricingProfile    string
	ServiceTier       string
	ThreadID          string
	ForkID            string
	ExecutionSource   string
	Endpoint          string
	TelemetryComplete bool
	Extra             map[string]string
}

func sourceUsageMetadata(provider, model, modelEvidence, threadID, executionSource string) UsageMetadata {
	return UsageMetadata{
		Provider: provider, Model: model, ModelEvidence: modelEvidence,
		Effort: "unknown", PricingProfile: "default", ServiceTier: "default",
		ThreadID: threadID, ExecutionSource: executionSource,
	}
}

type ClaudeUsageRecord struct {
	ObservedAt               float64
	InputTokens              *float64
	CacheReadInputTokens     *float64
	CacheCreationInputTokens *float64
	CacheCreation5mTokens    *float64
	CacheCreation1hTokens    *float64
	OutputTokens             *float64
	Metadata                 UsageMetadata
}

type CodexUsageRecord struct {
	ObservedAt        float64
	InputTokens       *float64
	CachedInputTokens *float64
	OutputTokens      *float64
	Metadata          UsageMetadata
}

// UsageCounters contains only counters present in a provider snapshot. A
// missing key is different from a present key whose value is zero.
type UsageCounters map[string]float64

// DeltaUsageCounters returns counter increments. If any counter decreases, the
// snapshot is treated as a whole reset and all current counters are returned.
func DeltaUsageCounters(previous, current UsageCounters) (UsageCounters, bool, error) {
	if err := validateCounters(previous); err != nil {
		return nil, false, err
	}
	if err := validateCounters(current); err != nil {
		return nil, false, err
	}
	reset := false
	for name, old := range previous {
		if value, ok := current[name]; ok && value < old {
			reset = true
			break
		}
	}
	delta := make(UsageCounters, len(current))
	for name, value := range current {
		if reset {
			delta[name] = value
			continue
		}
		if old, ok := previous[name]; ok {
			delta[name] = value - old
		} else {
			delta[name] = value
		}
	}
	return delta, reset, nil
}

func NormalizeClaudeUsage(record ClaudeUsageRecord) ([]UsageAtom, error) {
	record.Metadata.TelemetryComplete = record.Metadata.TelemetryComplete || record.InputTokens != nil && record.OutputTokens != nil
	cache5m := record.CacheCreation5mTokens
	if cache5m == nil {
		cache5m = record.CacheCreationInputTokens
	}
	return normalizeUsage(record.ObservedAt, record.Metadata, "claude", []usageValue{
		{"input", "none", record.InputTokens},
		{"input", "read", record.CacheReadInputTokens},
		{"input", "write_5m", cache5m},
		{"input", "write_1h", record.CacheCreation1hTokens},
		{"output", "none", record.OutputTokens},
	})
}

func NormalizeCodexUsage(record CodexUsageRecord) ([]UsageAtom, error) {
	record.Metadata.TelemetryComplete = record.Metadata.TelemetryComplete || record.InputTokens != nil && record.OutputTokens != nil
	var uncached *float64
	if record.InputTokens != nil {
		value := *record.InputTokens
		if record.CachedInputTokens != nil {
			value -= *record.CachedInputTokens
			if value < 0 {
				return nil, invalid("usage_record", "cached_input_tokens", "must not exceed input_tokens")
			}
		}
		uncached = &value
	}
	return normalizeUsage(record.ObservedAt, record.Metadata, "codex", []usageValue{
		{"input", "none", uncached},
		{"input", "read", record.CachedInputTokens},
		{"output", "none", record.OutputTokens},
	})
}

// NormalizeClaudeUsageMap and NormalizeCodexUsageMap accept decoded JSON-like
// maps while retaining absent fields as absent atoms.
func NormalizeClaudeUsageMap(values map[string]any, observedAt float64, metadata UsageMetadata) ([]UsageAtom, error) {
	if err := validateUsageInputMap(values); err != nil {
		return nil, err
	}
	return NormalizeClaudeUsage(ClaudeUsageRecord{
		ObservedAt:               observedAt,
		InputTokens:              mapNumber(values, "input_tokens"),
		CacheReadInputTokens:     mapNumber(values, "cache_read_input_tokens"),
		CacheCreationInputTokens: mapNumber(values, "cache_creation_input_tokens"),
		CacheCreation5mTokens:    mapNumber(values, "cache_creation_input_tokens_5m"),
		CacheCreation1hTokens:    mapNumber(values, "cache_creation_input_tokens_1h"),
		OutputTokens:             mapNumber(values, "output_tokens"),
		Metadata:                 metadata,
	})
}

func NormalizeCodexUsageMap(values map[string]any, observedAt float64, metadata UsageMetadata) ([]UsageAtom, error) {
	if err := validateUsageInputMap(values); err != nil {
		return nil, err
	}
	return NormalizeCodexUsage(CodexUsageRecord{
		ObservedAt:        observedAt,
		InputTokens:       mapNumber(values, "input_tokens"),
		CachedInputTokens: mapNumber(values, "cached_input_tokens"),
		OutputTokens:      mapNumber(values, "output_tokens"),
		Metadata:          metadata,
	})
}

type usageValue struct {
	direction string
	cacheRole string
	quantity  *float64
}

func normalizeUsage(observedAt float64, metadata UsageMetadata, provider string, values []usageValue) ([]UsageAtom, error) {
	if err := validateNumber("usage_record", "observed_at", observedAt, false); err != nil {
		return nil, err
	}
	if metadata.Provider == "" {
		metadata.Provider = provider
	}
	payloadMetadata, err := usageMetadataPayload(metadata, provider)
	if err != nil {
		return nil, err
	}
	result := make([]UsageAtom, 0, len(values))
	for _, value := range values {
		if value.quantity == nil {
			continue
		}
		if err := validateNumber("usage_record", value.direction, *value.quantity, false); err != nil {
			return nil, err
		}
		if *value.quantity == 0 {
			continue
		}
		payload := cloneStringMap(payloadMetadata)
		payload["quantity"] = *value.quantity
		atom := UsageAtom{
			EventID:    usageEventID(provider, metadata, observedAt, value.direction, value.cacheRole, *value.quantity),
			Direction:  value.direction,
			Modality:   "text",
			CacheRole:  value.cacheRole,
			Unit:       "tokens",
			ObservedAt: observedAt,
			Payload:    payload,
		}
		result = append(result, atom)
	}
	return result, nil
}

func usageMetadataPayload(metadata UsageMetadata, provider string) (map[string]any, error) {
	payload := usageMetadataPayloadFields(metadata, provider)
	if err := validateText("usage_metadata", "provider", payload["provider"].(string), MaxIdentityBytes); err != nil {
		return nil, err
	}
	for name, value := range payload {
		if name == "provider" || name == "telemetry_complete" {
			continue
		}
		if err := validateText("usage_metadata", name, value.(string), MaxSeriesComponentBytes); err != nil {
			return nil, err
		}
	}
	if len(metadata.Extra) > MaxUsageMetadataEntries {
		return nil, invalid("usage_metadata", "extra", "too many entries")
	}
	for name, value := range metadata.Extra {
		if err := validateText("usage_metadata", "extra_key", name, MaxSeriesComponentBytes); err != nil {
			return nil, err
		}
		if err := validateText("usage_metadata", "extra_value", value, MaxSeriesComponentBytes); err != nil {
			return nil, err
		}
		payload["meta_"+name] = value
	}
	raw, _ := json.Marshal(payload)
	if len(raw) > MaxUsageMetadataBytes {
		return nil, invalid("usage_metadata", "payload", "exceeds metadata bound")
	}
	return payload, nil
}

func usageMetadataPayloadFields(metadata UsageMetadata, provider string) map[string]any {
	if metadata.Provider == "" {
		metadata.Provider = provider
	}
	payload := map[string]any{"provider": metadata.Provider}
	for name, value := range map[string]string{
		"model": metadata.Model, "model_evidence": metadata.ModelEvidence, "effort": metadata.Effort, "pricing_profile": metadata.PricingProfile, "service_tier": metadata.ServiceTier, "thread_id": metadata.ThreadID, "fork_id": metadata.ForkID,
		"execution_source": metadata.ExecutionSource, "endpoint": metadata.Endpoint,
	} {
		if value == "" {
			continue
		}
		payload[name] = value
	}
	payload["telemetry_complete"] = metadata.TelemetryComplete
	return payload
}

func usageEventID(provider string, metadata UsageMetadata, observedAt float64, direction, cacheRole string, quantity float64) string {
	parts := []string{provider, metadata.Provider, metadata.Model, metadata.ThreadID, metadata.ForkID, formatFloat(observedAt), direction, cacheRole, formatFloat(quantity)}
	keys := make([]string, 0, len(metadata.Extra))
	for key := range metadata.Extra {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		parts = append(parts, key, metadata.Extra[key])
	}
	digest := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	return provider + ":" + hex.EncodeToString(digest[:])
}

func formatFloat(value float64) string { return fmt.Sprintf("%.17g", value) }

func mapNumber(values map[string]any, key string) *float64 {
	value, ok := values[key]
	if !ok {
		return nil
	}
	var number float64
	switch typed := value.(type) {
	case float64:
		number = typed
	case float32:
		number = float64(typed)
	case int:
		number = float64(typed)
	case int64:
		number = float64(typed)
	case json.Number:
		parsed, err := typed.Float64()
		if err != nil {
			return invalidNumberPointer()
		}
		number = parsed
	default:
		return invalidNumberPointer()
	}
	return &number
}

func invalidNumberPointer() *float64 {
	value := math.NaN()
	return &value
}

func validateCounters(counters UsageCounters) error {
	for name, value := range counters {
		if err := validateText("usage_counters", "name", name, MaxSeriesComponentBytes); err != nil {
			return err
		}
		if err := validateNumber("usage_counters", name, value, false); err != nil {
			return err
		}
	}
	return nil
}

func validateUsageInputMap(values map[string]any) error {
	if len(values) > MaxUsageInputEntries {
		return invalid("usage_record", "fields", "too many entries")
	}
	return nil
}

func cloneStringMap(source map[string]any) map[string]any {
	result := make(map[string]any, len(source)+1)
	for key, value := range source {
		result[key] = value
	}
	return result
}
