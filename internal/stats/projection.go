package stats

import (
	"fmt"
	"math"
	"sort"
)

// MaxProjectionInputs bounds the already-read facts accepted by this
// foundation. Callers must page or otherwise bound history before projecting.
const MaxProjectionInputs = MaxFactReadLimit

type ProjectionRequest struct {
	Start      float64
	End        float64
	Resolution float64
}

type UnavailableCoverage struct {
	Start  float64
	End    float64
	Reason string
}

type ObservationBucket struct {
	Start        float64
	End          float64
	Observations []Observation
	Unavailable  []UnavailableCoverage
	NoData       bool
}

type ObservationProjection struct {
	Buckets []ObservationBucket
}

type TokenBucket struct {
	Start  float64
	End    float64
	Input  float64
	Output float64
	Cache  float64
}

type TokenProjection struct {
	Buckets []TokenBucket
}

// ProjectObservations groups bounded observations into half-open, range-clipped
// time buckets. Unavailable spans are represented explicitly rather than as
// zero-valued observations.
func ProjectObservations(request ProjectionRequest, observations []Observation, unavailable []UnavailableSpan) (ObservationProjection, error) {
	buckets, err := projectionBuckets(request)
	if err != nil {
		return ObservationProjection{}, err
	}
	if len(observations)+len(unavailable) > MaxProjectionInputs {
		return ObservationProjection{}, fmt.Errorf("projection input exceeds %d facts", MaxProjectionInputs)
	}
	for i, observation := range observations {
		if err := observation.Validate(); err != nil {
			return ObservationProjection{}, fmt.Errorf("observations[%d]: %w", i, err)
		}
		if observation.ObservedAt < request.Start || observation.ObservedAt >= request.End {
			continue
		}
		buckets[bucketIndex(request, observation.ObservedAt)].Observations = append(buckets[bucketIndex(request, observation.ObservedAt)].Observations, observation)
	}
	for i, span := range unavailable {
		if err := span.Validate(); err != nil {
			return ObservationProjection{}, fmt.Errorf("unavailable[%d]: %w", i, err)
		}
		start := math.Max(request.Start, span.StartedAt)
		end := math.Min(request.End, span.EndedAt)
		if start >= end {
			continue
		}
		first := bucketIndex(request, start)
		last := bucketIndex(request, math.Nextafter(end, request.Start))
		for index := first; index <= last; index++ {
			clipStart := math.Max(start, buckets[index].Start)
			clipEnd := math.Min(end, buckets[index].End)
			if clipStart < clipEnd {
				buckets[index].Unavailable = append(buckets[index].Unavailable, UnavailableCoverage{Start: clipStart, End: clipEnd, Reason: span.Reason})
				buckets[index].NoData = true
			}
		}
	}
	for index := range buckets {
		buckets[index].NoData = len(buckets[index].Observations) == 0 || buckets[index].NoData
		sort.Slice(buckets[index].Observations, func(i, j int) bool {
			a, b := buckets[index].Observations[i], buckets[index].Observations[j]
			if a.ObservedAt != b.ObservedAt {
				return a.ObservedAt < b.ObservedAt
			}
			if a.Family != b.Family {
				return a.Family < b.Family
			}
			if a.SourceID != b.SourceID {
				return a.SourceID < b.SourceID
			}
			return a.EventID < b.EventID
		})
		sort.Slice(buckets[index].Unavailable, func(i, j int) bool {
			a, b := buckets[index].Unavailable[i], buckets[index].Unavailable[j]
			if a.Start != b.Start {
				return a.Start < b.Start
			}
			if a.End != b.End {
				return a.End < b.End
			}
			return a.Reason < b.Reason
		})
	}
	return ObservationProjection{Buckets: buckets}, nil
}

// ProjectTokens sums token quantities from bounded usage atoms. Only the
// declared input, output, and cache dimensions are projected; other units are
// ignored so this foundation does not imply cost/materializer parity.
func ProjectTokens(request ProjectionRequest, atoms []UsageAtom) (TokenProjection, error) {
	bounds, err := projectionBounds(request)
	if err != nil {
		return TokenProjection{}, err
	}
	buckets := make([]TokenBucket, len(bounds))
	for i, bounds := range bounds {
		buckets[i] = TokenBucket{Start: bounds[0], End: bounds[1]}
	}
	if len(atoms) > MaxProjectionInputs {
		return TokenProjection{}, fmt.Errorf("token input exceeds %d facts", MaxProjectionInputs)
	}
	for i, atom := range atoms {
		if err := atom.Validate(); err != nil {
			return TokenProjection{}, fmt.Errorf("usage[%d]: %w", i, err)
		}
		if atom.Unit != "tokens" || atom.ObservedAt < request.Start || atom.ObservedAt >= request.End {
			continue
		}
		quantity, err := tokenQuantity(atom)
		if err != nil {
			return TokenProjection{}, fmt.Errorf("usage[%d]: %w", i, err)
		}
		bucket := &buckets[bucketIndex(request, atom.ObservedAt)]
		switch {
		case atom.CacheRole != "none":
			bucket.Cache += quantity
		case atom.Direction == "input":
			bucket.Input += quantity
		case atom.Direction == "output":
			bucket.Output += quantity
		}
	}
	return TokenProjection{Buckets: buckets}, nil
}

func projectionBuckets(request ProjectionRequest) ([]ObservationBucket, error) {
	bounds, err := projectionBounds(request)
	if err != nil {
		return nil, err
	}
	buckets := make([]ObservationBucket, len(bounds))
	for i, bounds := range bounds {
		buckets[i] = ObservationBucket{Start: bounds[0], End: bounds[1]}
	}
	return buckets, nil
}

func projectionBounds(request ProjectionRequest) ([][2]float64, error) {
	if math.IsNaN(request.Start) || math.IsInf(request.Start, 0) || math.IsNaN(request.End) || math.IsInf(request.End, 0) || request.Start >= request.End {
		return nil, fmt.Errorf("invalid projection range")
	}
	if math.IsNaN(request.Resolution) || math.IsInf(request.Resolution, 0) || request.Resolution <= 0 {
		return nil, fmt.Errorf("invalid projection resolution")
	}
	count := math.Ceil((request.End - request.Start) / request.Resolution)
	if count > MaxProjectionInputs {
		return nil, fmt.Errorf("projection range exceeds %d buckets", MaxProjectionInputs)
	}
	bounds := make([][2]float64, int(count))
	for i := range bounds {
		start := request.Start + float64(i)*request.Resolution
		bounds[i] = [2]float64{start, math.Min(request.End, start+request.Resolution)}
	}
	return bounds, nil
}

func bucketIndex(request ProjectionRequest, timestamp float64) int {
	index := int(math.Floor((timestamp - request.Start) / request.Resolution))
	return index
}

func tokenQuantity(atom UsageAtom) (float64, error) {
	value, ok := atom.Payload["quantity"]
	if !ok {
		return 0, fmt.Errorf("payload.quantity is required for token atoms")
	}
	quantity, err := number(value)
	if err != nil || math.IsNaN(quantity) || math.IsInf(quantity, 0) || quantity < 0 {
		return 0, fmt.Errorf("payload.quantity must be finite and non-negative")
	}
	return quantity, nil
}
