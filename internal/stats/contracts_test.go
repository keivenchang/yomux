package stats

import (
	"context"
	"errors"
	"math"
	"path/filepath"
	"strings"
	"testing"
)

func testObservation() Observation {
	return Observation{EventID: "event-1", Family: "cpu", SourceID: "host", ObservedAt: 10, EpochID: "epoch-1", Payload: map[string]any{"value": 1}}
}

func TestCurrentAgentRosterAllowsBoundedObservationTime(t *testing.T) {
	cases := []struct {
		name  string
		extra map[string]any
		valid bool
	}{
		{"historical", nil, true},
		{"current", map[string]any{"observed_at": 123.5}, true},
		{"zero", map[string]any{"observed_at": 0.0}, true},
		{"negative", map[string]any{"observed_at": -1.0}, false},
		{"nan", map[string]any{"observed_at": math.NaN()}, false},
		{"infinity", map[string]any{"observed_at": math.Inf(1)}, false},
		{"boolean", map[string]any{"observed_at": true}, false},
		{"string", map[string]any{"observed_at": "123"}, false},
		{"null", map[string]any{"observed_at": nil}, false},
		{"unknown field", map[string]any{"timestamp": 123.5}, false},
		{"extra field", map[string]any{"observed_at": 123.5, "timestamp": 123.5}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			row := map[string]any{"id": "agent", "kind": "codex", "state": "run", "session": "work", "pane": "%1"}
			for key, value := range tc.extra {
				row[key] = value
			}
			if err := validateCurrentAgentRoster([]map[string]any{row}); (err == nil) != tc.valid {
				t.Fatalf("roster validation = %v, want valid=%v", err, tc.valid)
			}
		})
	}
}

func testCoverage() CoverageEpoch {
	end := 20.0
	return CoverageEpoch{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 10, EndedAt: &end, NativeCadenceSeconds: 1, OwnerGeneration: 1}
}

func testUnavailable() UnavailableSpan {
	return UnavailableSpan{Family: "gpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 10, EndedAt: 20, NativeCadenceSeconds: 5, Reason: "query-failed"}
}

func testUsage() UsageAtom {
	return UsageAtom{EventID: "event-1", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 10, Payload: map[string]any{"quantity": 4, "provider": "openai"}}
}

func testTombstone() UsageTombstone {
	return UsageTombstone{EventID: "codex:thread-1:event-1", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 10, Quantity: 4, Provider: "openai", Model: "gpt", ThreadID: "thread-1"}
}

func TestContractsAcceptValidRecords(t *testing.T) {
	batch := AppendBatch{Observations: []Observation{testObservation()}, CoverageEpochs: []CoverageEpoch{testCoverage()}, UnavailableSpans: []UnavailableSpan{testUnavailable()}, UsageAtoms: []UsageAtom{testUsage()}, UsageTombstones: []UsageTombstone{testTombstone()}}
	result, err := batch.Validate()
	if err != nil {
		t.Fatal(err)
	}
	if result.ObservationCount != 1 || result.CoverageEpochCount != 1 || result.UsageAtomCount != 1 || len(result.IdempotencyKeys) != 3 {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestContractsRejectMalformedValues(t *testing.T) {
	cases := []struct {
		name     string
		validate func() error
	}{
		{"nan timestamp", func() error { item := testObservation(); item.ObservedAt = math.NaN(); return item.Validate() }},
		{"negative quantity", func() error { item := testTombstone(); item.Quantity = -1; return item.Validate() }},
		{"zero cadence", func() error { item := testCoverage(); item.NativeCadenceSeconds = 0; return item.Validate() }},
		{"empty identity", func() error { item := testObservation(); item.SourceID = " "; return item.Validate() }},
		{"control character", func() error { item := testUnavailable(); item.Reason = "bad\nreason"; return item.Validate() }},
		{"invalid tombstone identity", func() error { item := testTombstone(); item.Provider = "anthropic"; return item.Validate() }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var contractErr *ContractError
			if !errors.As(tc.validate(), &contractErr) {
				t.Fatal("expected ContractError")
			}
		})
	}
}

func TestContractsRejectBoundedFields(t *testing.T) {
	item := testObservation()
	item.EventID = strings.Repeat("x", MaxEventIDBytes+1)
	if err := item.Validate(); err == nil {
		t.Fatal("oversized event ID accepted")
	}
	item = testObservation()
	item.Payload = map[string]any{"value": strings.Repeat("x", MaxPayloadBytes)}
	if err := item.Validate(); err == nil {
		t.Fatal("oversized payload accepted")
	}
}

func TestAppendBatchRejectsDuplicateAndConflictingIdentity(t *testing.T) {
	first := testObservation()
	if _, err := (AppendBatch{Observations: []Observation{first, first}}).Validate(); err == nil || !strings.Contains(err.Error(), "duplicate") {
		t.Fatalf("duplicate error = %v", err)
	}
	conflict := first
	conflict.Payload = map[string]any{"value": 2}
	if _, err := (AppendBatch{Observations: []Observation{first, conflict}}).Validate(); err == nil || !strings.Contains(err.Error(), "conflicting") {
		t.Fatalf("conflict error = %v", err)
	}
}

func TestAppendBatchRejectsIntervalErrors(t *testing.T) {
	coverage := testCoverage()
	end := 30.0
	coverage.EndedAt = &end
	if _, err := (AppendBatch{CoverageEpochs: []CoverageEpoch{testCoverage(), coverage}}).Validate(); err == nil || !strings.Contains(err.Error(), "overlapping") {
		t.Fatalf("overlap error = %v", err)
	}
	span := testUnavailable()
	span.Family, span.SourceID = coverage.Family, coverage.SourceID
	if _, err := (AppendBatch{CoverageEpochs: []CoverageEpoch{testCoverage()}, UnavailableSpans: []UnavailableSpan{span}}).Validate(); err == nil || !strings.Contains(err.Error(), "overlapping") {
		t.Fatalf("coverage/unavailable error = %v", err)
	}
	bad := testCoverage()
	bad.StartedAt = 21
	bad.EndedAt = func() *float64 { value := 20.0; return &value }()
	if err := bad.Validate(); err == nil || !strings.Contains(err.Error(), "precede") {
		t.Fatalf("interval error = %v", err)
	}
}

func TestIdempotencyKeysUseDeclaredIdentities(t *testing.T) {
	if testObservation().IdempotencyKey() != "observation:cpu\x00host\x00event-1" {
		t.Fatal("observation key changed")
	}
	if testUsage().IdempotencyKey() != "usage:event-1\x00input\x00text\x00none\x00tokens" {
		t.Fatal("usage key changed")
	}
	if !strings.HasPrefix(testTombstone().IdempotencyKey(), "tombstone:codex:thread-1:event-1") {
		t.Fatal("tombstone key changed")
	}
}

func TestCurrentFamilyValidationIsWriteOnlyAndCompatibilityAware(t *testing.T) {
	valid := BuildCPUFacts(CPUSample{
		Reason:  CPUValid,
		Process: CPUMeasurement{Value: 3.25, Valid: true},
		Host:    CPUMeasurement{Value: 12.5, Valid: true},
	}, map[string]float64{"python": 3.25}, HostFactContext{
		EpochID: "cpu", EpochStartedAt: 0, ObservedAt: 1, CadenceSeconds: 1, SourceID: "host",
	})
	malformed := valid
	malformed.Observations = append([]Observation(nil), valid.Observations...)
	malformed.Observations[0].Payload = map[string]any{"process_percent": 3.25}

	legacyWriter, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "legacy.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := legacyWriter.AppendBatch(context.Background(), malformed); err != nil {
		_ = legacyWriter.Close()
		t.Fatalf("compatibility writer rejected sparse fixture: %v", err)
	}
	if err := legacyWriter.Close(); err != nil {
		t.Fatal(err)
	}

	currentWriter, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "current.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	currentWriter.EnableCurrentFamilyValidation()
	validBatches := []AppendBatch{
		valid,
		BuildGPUFacts([]GPUDevice{{SourceID: "gpu:0", Label: "GPU 0", Utilization: 50, MemoryUsedBytes: 100, MemoryTotalBytes: 200}}, HostFactContext{EpochID: "gpu", EpochStartedAt: 0, ObservedAt: 2, CadenceSeconds: 10, SourceID: "gpu:0"}),
		BuildSystemMemoryFacts(MemoryCounters{UsedBytes: 100, CapacityBytes: 200}, nil, nil, HostFactContext{EpochID: "memory", EpochStartedAt: 0, ObservedAt: 3, CadenceSeconds: 60, SourceID: "host"}),
		BuildAgentStatusFacts(map[string]any{"agent": "run"}, map[string]AgentStatus{"session": AgentStatusIdle}, 4, AgentStatusCounts{Total: 1, Running: 0}, []map[string]any{{"id": "agent", "kind": "codex", "state": "idle", "session": "work", "pane": "%1"}}, HostFactContext{EpochID: "agents", EpochStartedAt: 0, ObservedAt: 4, CadenceSeconds: 10, SourceID: "host"}),
		{Observations: []Observation{{EventID: "service-load", Family: "service_load", SourceID: "statsd", ObservedAt: 5, EpochID: "service", Payload: map[string]any{"running": true, "cpu_percent": 2.0, "rss_bytes": nil}}}},
		{Observations: []Observation{{EventID: "browser-api", Family: "browser", SourceID: "browser:a", ObservedAt: 6, EpochID: "browser", Payload: map[string]any{"kind": "api", "latency_ms": 2.0, "status": 200}}}},
	}
	for _, batch := range validBatches {
		if _, err := currentWriter.AppendBatch(context.Background(), batch); err != nil {
			t.Fatalf("current writer rejected Python-shaped payload: %v", err)
		}
	}
	if _, err := currentWriter.AppendBatch(context.Background(), AppendBatch{Observations: []Observation{{
		EventID: "service-load-empty", Family: "service_load", SourceID: "yolomux-go", ObservedAt: 5.75,
		EpochID: "service", Payload: map[string]any{"running": true},
	}}}); err == nil || !strings.Contains(err.Error(), "missing field") {
		t.Fatalf("current writer accepted service load without CPU: %v", err)
	}
	if _, err := currentWriter.AppendBatch(context.Background(), AppendBatch{Observations: []Observation{{
		EventID: "service-load-null-rss", Family: "service_load", SourceID: "yolomux-go", ObservedAt: 5.8,
		EpochID: "service", Payload: map[string]any{"running": true, "rss_bytes": nil},
	}}}); err == nil || !strings.Contains(err.Error(), "missing field") {
		t.Fatalf("current writer accepted service load without CPU: %v", err)
	}
	if _, err := currentWriter.AppendBatch(context.Background(), malformed); err == nil || !strings.Contains(err.Error(), "missing field") {
		t.Fatalf("current writer accepted malformed current payload: %v", err)
	}
	if err := currentWriter.Close(); err != nil {
		t.Fatal(err)
	}
}
