package stats

import (
	"math"
	"reflect"
	"strings"
	"testing"
)

func usageNumber(value float64) *float64 { return &value }

func TestNormalizeUsageProviders(t *testing.T) {
	tests := []struct {
		name      string
		normalize func() ([]UsageAtom, error)
		want      []struct {
			direction string
			cacheRole string
			quantity  float64
		}
	}{
		{
			name: "claude fields",
			normalize: func() ([]UsageAtom, error) {
				return NormalizeClaudeUsage(ClaudeUsageRecord{ObservedAt: 10, InputTokens: usageNumber(3), CacheReadInputTokens: usageNumber(4), CacheCreation5mTokens: usageNumber(5), CacheCreation1hTokens: usageNumber(6), OutputTokens: usageNumber(7), Metadata: UsageMetadata{Model: "opus"}})
			},
			want: []struct {
				direction, cacheRole string
				quantity             float64
			}{{"input", "none", 3}, {"input", "read", 4}, {"input", "write_5m", 5}, {"input", "write_1h", 6}, {"output", "none", 7}},
		},
		{
			name: "codex cached subset",
			normalize: func() ([]UsageAtom, error) {
				return NormalizeCodexUsage(CodexUsageRecord{ObservedAt: 10, InputTokens: usageNumber(8), CachedInputTokens: usageNumber(2), OutputTokens: usageNumber(1), Metadata: UsageMetadata{Provider: "openai", Model: "gpt-5"}})
			},
			want: []struct {
				direction, cacheRole string
				quantity             float64
			}{{"input", "none", 6}, {"input", "read", 2}, {"output", "none", 1}},
		},
		{
			name: "missing fields",
			normalize: func() ([]UsageAtom, error) {
				return NormalizeClaudeUsage(ClaudeUsageRecord{ObservedAt: 10, InputTokens: usageNumber(0)})
			},
			want: []struct {
				direction, cacheRole string
				quantity             float64
			}{},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			atoms, err := test.normalize()
			if err != nil {
				t.Fatal(err)
			}
			if len(atoms) != len(test.want) {
				t.Fatalf("atom count = %d, want %d", len(atoms), len(test.want))
			}
			for i, want := range test.want {
				if atoms[i].Direction != want.direction || atoms[i].CacheRole != want.cacheRole || atoms[i].Payload["quantity"] != want.quantity {
					t.Fatalf("atom[%d] = %+v", i, atoms[i])
				}
			}
		})
	}
}

func TestNormalizeUsageMapAndMetadataBounds(t *testing.T) {
	atoms, err := NormalizeClaudeUsageMap(map[string]any{"input_tokens": float64(2), "output_tokens": float64(0)}, 4, UsageMetadata{Provider: "anthropic", Model: "sonnet", ThreadID: "thread", ForkID: "fork", Extra: map[string]string{"region": "us"}})
	if err != nil || len(atoms) != 1 {
		t.Fatalf("atoms = %+v, err = %v", atoms, err)
	}
	if atoms[0].Payload["provider"] != "anthropic" || atoms[0].Payload["model"] != "sonnet" || atoms[0].Payload["thread_id"] != "thread" || atoms[0].Payload["fork_id"] != "fork" || atoms[0].Payload["meta_region"] != "us" {
		t.Fatalf("metadata payload = %+v", atoms[0].Payload)
	}
	metadata := UsageMetadata{Extra: map[string]string{}}
	for i := 0; i < MaxUsageMetadataEntries+1; i++ {
		metadata.Extra[strings.Repeat("x", i+1)] = "value"
	}
	if _, err := NormalizeClaudeUsage(ClaudeUsageRecord{ObservedAt: 1, InputTokens: usageNumber(1), Metadata: metadata}); err == nil {
		t.Fatal("oversized metadata entry set accepted")
	}
}

func TestNormalizeClaudeNestedCacheDoesNotDoubleCountAggregate(t *testing.T) {
	atoms, err := NormalizeClaudeUsageMap(map[string]any{
		"input_tokens":                   10.0,
		"cache_creation_input_tokens":    99.0,
		"cache_creation_input_tokens_5m": 3.0,
		"cache_creation_input_tokens_1h": 4.0,
	}, 4, UsageMetadata{})
	if err != nil {
		t.Fatal(err)
	}
	if got := atomQuantitiesByRole(atoms); !reflect.DeepEqual(got, map[string]float64{"none": 10, "write_5m": 3, "write_1h": 4}) {
		t.Fatalf("cache roles = %#v", got)
	}
}

func atomQuantitiesByRole(atoms []UsageAtom) map[string]float64 {
	result := map[string]float64{}
	for _, atom := range atoms {
		result[atom.CacheRole] += atom.Payload["quantity"].(float64)
	}
	return result
}

func TestNormalizeUsageRejectsMalformedValues(t *testing.T) {
	for name, record := range map[string]ClaudeUsageRecord{
		"negative": {ObservedAt: 1, InputTokens: usageNumber(-1)},
		"nan":      {ObservedAt: 1, InputTokens: usageNumber(math.NaN())},
		"infinite": {ObservedAt: 1, InputTokens: usageNumber(math.Inf(1))},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := NormalizeClaudeUsage(record); err == nil {
				t.Fatal("malformed value accepted")
			}
		})
	}
	if _, err := NormalizeClaudeUsageMap(map[string]any{"input_tokens": "bad"}, 1, UsageMetadata{}); err == nil {
		t.Fatal("malformed map value accepted")
	}
	if _, err := NormalizeCodexUsage(CodexUsageRecord{ObservedAt: 1, InputTokens: usageNumber(1), CachedInputTokens: usageNumber(2)}); err == nil {
		t.Fatal("cached subset larger than input accepted")
	}
}

func TestUsageEventIDsAreDeterministic(t *testing.T) {
	record := ClaudeUsageRecord{ObservedAt: 1.5, InputTokens: usageNumber(2), Metadata: UsageMetadata{Model: "model", Extra: map[string]string{"b": "2", "a": "1"}}}
	first, err := NormalizeClaudeUsage(record)
	if err != nil {
		t.Fatal(err)
	}
	second, err := NormalizeClaudeUsage(record)
	if err != nil || first[0].EventID != second[0].EventID || !strings.HasPrefix(first[0].EventID, "claude:") {
		t.Fatalf("IDs = %q/%q, err = %v", first[0].EventID, second[0].EventID, err)
	}
	changed := record
	changed.ObservedAt = 2
	third, _ := NormalizeClaudeUsage(changed)
	if first[0].EventID == third[0].EventID {
		t.Fatal("changed record reused event ID")
	}
}

func TestDeltaUsageCounters(t *testing.T) {
	tests := []struct {
		name      string
		previous  UsageCounters
		current   UsageCounters
		want      UsageCounters
		wantReset bool
	}{
		{"delta", UsageCounters{"input": 10, "output": 4}, UsageCounters{"input": 13, "output": 4}, UsageCounters{"input": 3, "output": 0}, false},
		{"new field", UsageCounters{"input": 10}, UsageCounters{"input": 13, "cached": 2}, UsageCounters{"input": 3, "cached": 2}, false},
		{"whole reset", UsageCounters{"input": 10, "output": 4}, UsageCounters{"input": 2, "output": 1}, UsageCounters{"input": 2, "output": 1}, true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, reset, err := DeltaUsageCounters(test.previous, test.current)
			if err != nil || reset != test.wantReset || !reflect.DeepEqual(got, test.want) {
				t.Fatalf("got = %#v, reset = %t, err = %v", got, reset, err)
			}
		})
	}
	if _, _, err := DeltaUsageCounters(nil, UsageCounters{"bad": -1}); err == nil {
		t.Fatal("invalid counter accepted")
	}
}
