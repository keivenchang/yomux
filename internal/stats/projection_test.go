package stats

import (
	"strings"
	"testing"
)

func projectionRequest() ProjectionRequest {
	return ProjectionRequest{Start: 0, End: 10, Resolution: 3}
}

func projectionObservation(id string, at float64) Observation {
	return Observation{EventID: id, Family: "cpu", SourceID: "host", ObservedAt: at, EpochID: "epoch", Payload: map[string]any{"value": 1}}
}

func projectionUsage(id, direction, cache string, at, quantity float64) UsageAtom {
	return UsageAtom{EventID: id, Direction: direction, Modality: "text", CacheRole: cache, Unit: "tokens", ObservedAt: at, Payload: map[string]any{"quantity": quantity}}
}

func TestProjectObservationsUsesDeterministicBoundariesAndOrdering(t *testing.T) {
	result, err := ProjectObservations(projectionRequest(), []Observation{
		projectionObservation("b", 3), projectionObservation("a", 0), projectionObservation("outside", 10),
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Buckets) != 4 || result.Buckets[0].Start != 0 || result.Buckets[0].End != 3 || result.Buckets[1].Start != 3 || result.Buckets[3].End != 10 {
		t.Fatalf("boundaries = %+v", result.Buckets)
	}
	if len(result.Buckets[0].Observations) != 1 || result.Buckets[0].Observations[0].EventID != "a" || len(result.Buckets[1].Observations) != 1 {
		t.Fatalf("observations = %+v", result.Buckets)
	}
}

func TestProjectObservationsClipsSparseUnavailableCoverage(t *testing.T) {
	result, err := ProjectObservations(ProjectionRequest{Start: 2, End: 8, Resolution: 2}, nil, []UnavailableSpan{{
		Family: "cpu", SourceID: "host", EpochID: "epoch", StartedAt: 1, EndedAt: 5, NativeCadenceSeconds: 1, Reason: "offline",
	}})
	if err != nil {
		t.Fatal(err)
	}
	if !result.Buckets[0].NoData || result.Buckets[0].Unavailable[0].Start != 2 || result.Buckets[1].Unavailable[0].End != 5 || !result.Buckets[2].NoData {
		t.Fatalf("unavailable coverage = %+v", result.Buckets)
	}
}

func TestProjectTokensAggregatesDimensionsAndClipsRange(t *testing.T) {
	result, err := ProjectTokens(ProjectionRequest{Start: 0, End: 10, Resolution: 5}, []UsageAtom{
		projectionUsage("out", "output", "none", 1, 2), projectionUsage("in", "input", "none", 1, 3),
		projectionUsage("cache", "input", "read", 6, 4), projectionUsage("other", "input", "none", 11, 99),
	})
	if err != nil {
		t.Fatal(err)
	}
	want := []TokenBucket{{Start: 0, End: 5, Input: 3, Output: 2}, {Start: 5, End: 10, Cache: 4}}
	for i, bucket := range result.Buckets {
		if bucket != want[i] {
			t.Fatalf("bucket %d = %+v, want %+v", i, bucket, want[i])
		}
	}
}

func TestProjectionRejectsInvalidInputAndLimits(t *testing.T) {
	for name, request := range map[string]ProjectionRequest{
		"reversed":         {Start: 2, End: 1, Resolution: 1},
		"zero resolution":  {Start: 0, End: 1, Resolution: 0},
		"too many buckets": {Start: 0, End: float64(MaxProjectionInputs + 1), Resolution: 1},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := ProjectObservations(request, nil, nil); err == nil {
				t.Fatal("invalid request accepted")
			}
		})
	}
	tooMany := make([]Observation, MaxProjectionInputs+1)
	for i := range tooMany {
		tooMany[i] = projectionObservation(strings.Repeat("x", i+1), 0)
	}
	if _, err := ProjectObservations(projectionRequest(), tooMany, nil); err == nil {
		t.Fatal("oversized input accepted")
	}
	missingQuantity := projectionUsage("missing", "input", "none", 1, 0)
	delete(missingQuantity.Payload, "quantity")
	if _, err := ProjectTokens(projectionRequest(), []UsageAtom{missingQuantity}); err == nil {
		t.Fatal("missing quantity accepted")
	}
}
