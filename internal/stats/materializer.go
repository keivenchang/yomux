package stats

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"math"
	"sort"
	"strings"
)

const materializerMaxSafeInteger = float64(maxSafeJSONInteger)

// Materializer is the pure, read-only current-facts fold. It intentionally
// takes decoded storage records so callers can use it with SQLite snapshots,
// fixtures, or an in-memory replay without involving statsd.
type Materializer struct{ Price PriceResolver }

// PriceResolver supplies both the marginal and API-list counterfactual price.
// Returning ok=false is the exact unavailable/unpriced state.
type PriceResolver func(UsageAtom) (microUSD, apiListMicroUSD int64, evidence *PriceEvidence, ok bool, err error)

type PriceEvidence struct {
	Key, Provider, Model, Dimension, Direction, Modality        string
	CacheRole, Unit, PricingProfile, ServiceTier                string
	CatalogModel, RateUSD, EffectiveFrom, SourceKind, SourceURL string
	RateScale, CatalogRevision                                  int64
	Tokens, MicroUSD, APIListMicroUSD, PricedAtoms              int64
}

type MaterializeRequest struct {
	Start, End float64
	Resolution float64
	// ObservedUntil distinguishes an aligned output window from the last
	// instant observed by its producer. Nil means the entire window is known.
	ObservedUntil *float64
}

type MaterializedLayer struct {
	Resolution float64
	Start, End float64
	Buckets    []MaterializedBucket
	NoData     []MaterializedNoData
}

type MaterializedBucket struct {
	Start, Duration                 float64
	Series                          []MaterializedSeries
	SourceCount                     int
	FirstObservedAt, LastObservedAt *float64
	Complete                        bool
	Cost                            MaterializedCostDetail
	folds                           map[string]*fold
	costDimensions                  map[string]*MaterializedCostDimension
	costModels                      map[string]*MaterializedCostAttribution
	costAgents                      map[string]*MaterializedCostAttribution
}

type MaterializedSeries struct {
	Name                            string
	Value                           float64
	SourceCount                     int
	FirstObservedAt, LastObservedAt float64
}
type MaterializedNoData struct {
	Family, SourceID, EpochID, Reason string
	Start, End, NativeCadenceSeconds  float64
}

type MaterializedCostDetail struct {
	Tokens, MicroUSD, APIListMicroUSD int64
	PricedAtoms, UnpricedAtoms        int
	PricedTokens, UnpricedTokens      int64
	Evidence                          []PriceEvidence
	Dimensions                        []MaterializedCostDimension
	Models                            []MaterializedCostAttribution
	Agents                            []MaterializedCostAttribution
	OmittedModels, OmittedAgents      int
	OmittedEvidence                   int
}

type MaterializedCostDimension struct {
	Dimension                         string
	Tokens, MicroUSD, APIListMicroUSD int64
}

type MaterializedCostAttribution struct {
	Key, Provider, Model, Source, Label    string
	TotalTokens, MicroUSD, APIListMicroUSD int64
	Dimensions                             []MaterializedCostDimension
	PricedAtoms, UnpricedAtoms             int
	PricedTokens, UnpricedTokens           int64
	Sources                                []MaterializedCostSource
}

type MaterializedCostSource struct {
	Source                                 string
	TotalTokens, MicroUSD, APIListMicroUSD int64
	Dimensions                             []MaterializedCostDimension
	PricedAtoms, UnpricedAtoms             int
	PricedTokens, UnpricedTokens           int64
}

// costReportDimensions is the fixed Python wire order. Every non-empty cost
// detail carries all dimensions, including zero-valued ones, so consumers can
// reconcile totals without guessing whether an omitted dimension means zero
// or an older payload shape.
var costReportDimensions = [...]string{
	"input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "other",
}

type materializerAccumulator struct {
	req         MaterializeRequest
	price       PriceResolver
	layer       MaterializedLayer
	coverage    []CoverageEpoch
	unavailable []MaterializedNoData
}

func newMaterializerAccumulator(req MaterializeRequest, price PriceResolver) (*materializerAccumulator, error) {
	if math.IsNaN(req.Start) || math.IsInf(req.Start, 0) || math.IsNaN(req.End) || math.IsInf(req.End, 0) || req.Start >= req.End || req.Resolution <= 0 || math.IsNaN(req.Resolution) || math.IsInf(req.Resolution, 0) {
		return nil, fmt.Errorf("invalid materialization range")
	}
	count := int(math.Ceil((req.End - req.Start) / req.Resolution))
	if count < 1 || count > MaxProjectionInputs {
		return nil, fmt.Errorf("invalid materialization bucket count")
	}
	observedUntil := req.End
	if req.ObservedUntil != nil {
		observedUntil = *req.ObservedUntil
		if math.IsNaN(observedUntil) || math.IsInf(observedUntil, 0) {
			return nil, fmt.Errorf("invalid materialization observation cutoff")
		}
	}
	layer := MaterializedLayer{Resolution: req.Resolution, Start: req.Start, End: req.End, Buckets: make([]MaterializedBucket, count)}
	for i := range layer.Buckets {
		layer.Buckets[i] = MaterializedBucket{Start: req.Start + float64(i)*req.Resolution, Duration: math.Min(req.Resolution, req.End-(req.Start+float64(i)*req.Resolution)), Complete: req.Start+float64(i+1)*req.Resolution <= math.Min(req.End, observedUntil)}
	}
	return &materializerAccumulator{req: req, price: price, layer: layer}, nil
}

func (a *materializerAccumulator) addObservations(observations []Observation, offset int) error {
	for i, o := range observations {
		if err := o.Validate(); err != nil {
			return fmt.Errorf("observations[%d]: %w", offset+i, err)
		}
		if o.ObservedAt < a.req.Start || o.ObservedAt >= a.req.End {
			continue
		}
		bucket := &a.layer.Buckets[matBucketIndex(a.req, o.ObservedAt)]
		if addObservation(bucket, o) {
			recordBucketFact(bucket, o.ObservedAt)
			bucket.SourceCount++
		}
	}
	return nil
}

func (a *materializerAccumulator) addUsage(atoms []UsageAtom, offset int) error {
	seen := make(map[string]struct{}, len(atoms))
	for i, atom := range atoms {
		if err := atom.Validate(); err != nil {
			return fmt.Errorf("usage[%d]: %w", offset+i, err)
		}
		// The Python materializer folds one canonical usage identity even if a
		// replay or legacy snapshot presents the same row more than once. Keep
		// this boundary idempotent by using the same key as the writer.
		identity := atom.IdempotencyKey()
		if _, exists := seen[identity]; exists {
			continue
		}
		seen[identity] = struct{}{}
		if atom.ObservedAt < a.req.Start || atom.ObservedAt >= a.req.End {
			continue
		}
		bucket := &a.layer.Buckets[matBucketIndex(a.req, atom.ObservedAt)]
		added, err := addUsage(bucket, atom, a.price)
		if err != nil {
			return fmt.Errorf("usage[%d]: %w", offset+i, err)
		}
		if added {
			recordBucketFact(bucket, atom.ObservedAt)
			bucket.SourceCount++
		}
	}
	return nil
}

func (a *materializerAccumulator) addUnavailable(unavailable []UnavailableSpan) error {
	normalizedUnavailable, err := normalizeUnavailable(a.req, unavailable)
	if err != nil {
		return err
	}
	a.unavailable = append(a.unavailable, normalizedUnavailable...)
	for _, span := range normalizedUnavailable {
		a.layer.NoData = append(a.layer.NoData, span)
		if span.Family != "agent_tokens" ||
			(!strings.Contains(span.Reason, "truncated") && !strings.Contains(span.Reason, "bound-exceeded")) {
			continue
		}
		// A bounded token history can carry valid fresh atoms while explicitly
		// admitting that omitted history is not covered. Keep those buckets
		// incomplete so the next bounded pass can repair them. Ordinary source
		// outages are complete materializations with no-data spans; they must not
		// become a permanent lifecycle repair queue.
		for i := matBucketIndex(a.req, span.Start); i <= matBucketIndex(a.req, math.Nextafter(span.End, span.Start)); i++ {
			a.layer.Buckets[i].Complete = false
		}
	}
	return nil
}

func (a *materializerAccumulator) addCoverage(epochs []CoverageEpoch) {
	// Event-derived usage is not a coverage outage. Keep the complete coverage
	// model until finish so gaps can be computed across all paged rows and
	// subtracted against explicit unavailable spans.
	a.coverage = append(a.coverage, epochs...)
}

func (a *materializerAccumulator) finish() MaterializedLayer {
	a.addCoverageGaps()
	for i := range a.layer.Buckets {
		finalizeBucket(&a.layer.Buckets[i])
	}
	return a.layer
}

func (m Materializer) Build(req MaterializeRequest, observations []Observation, epochs []CoverageEpoch, unavailable []UnavailableSpan, atoms []UsageAtom) (MaterializedLayer, error) {
	accumulator, err := newMaterializerAccumulator(req, m.Price)
	if err != nil {
		return MaterializedLayer{}, err
	}
	if err := accumulator.addObservations(observations, 0); err != nil {
		return MaterializedLayer{}, err
	}
	if err := accumulator.addUsage(atoms, 0); err != nil {
		return MaterializedLayer{}, err
	}
	if err := accumulator.addUnavailable(unavailable); err != nil {
		return MaterializedLayer{}, err
	}
	accumulator.addCoverage(epochs)
	layer := accumulator.finish()
	if err := validateMaterializedCostRange(layer); err != nil {
		return MaterializedLayer{}, err
	}
	return layer, nil
}

type materializedCostSums struct {
	tokens, microUSD, apiListMicroUSD int64
	pricedAtoms, unpricedAtoms        int64
	pricedTokens, unpricedTokens      int64
}

func (s *materializedCostSums) add(tokens, microUSD, apiListMicroUSD, pricedAtoms, unpricedAtoms, pricedTokens, unpricedTokens int64, name string) error {
	var err error
	if s.tokens, err = addSafeJSONInteger(s.tokens, tokens, name+" tokens"); err != nil {
		return err
	}
	if s.microUSD, err = addSafeJSONInteger(s.microUSD, microUSD, name+" micro_usd"); err != nil {
		return err
	}
	if s.apiListMicroUSD, err = addSafeJSONInteger(s.apiListMicroUSD, apiListMicroUSD, name+" api_list_micro_usd"); err != nil {
		return err
	}
	if s.pricedAtoms, err = addSafeJSONInteger(s.pricedAtoms, pricedAtoms, name+" priced atoms"); err != nil {
		return err
	}
	if s.unpricedAtoms, err = addSafeJSONInteger(s.unpricedAtoms, unpricedAtoms, name+" unpriced atoms"); err != nil {
		return err
	}
	if s.pricedTokens, err = addSafeJSONInteger(s.pricedTokens, pricedTokens, name+" priced tokens"); err != nil {
		return err
	}
	if s.unpricedTokens, err = addSafeJSONInteger(s.unpricedTokens, unpricedTokens, name+" unpriced tokens"); err != nil {
		return err
	}
	return nil
}

func materializedCostDimensionSums(dimensions []MaterializedCostDimension, name string) (materializedCostSums, error) {
	var sums materializedCostSums
	for _, dimension := range dimensions {
		if err := sums.add(dimension.Tokens, dimension.MicroUSD, dimension.APIListMicroUSD, 0, 0, 0, 0, name); err != nil {
			return materializedCostSums{}, err
		}
	}
	return sums, nil
}

func validateCostTotalsWithin(actual, bound materializedCostSums, name string) error {
	if actual.tokens > bound.tokens || actual.microUSD > bound.microUSD || actual.apiListMicroUSD > bound.apiListMicroUSD || actual.pricedAtoms > bound.pricedAtoms || actual.unpricedAtoms > bound.unpricedAtoms || actual.pricedTokens > bound.pricedTokens || actual.unpricedTokens > bound.unpricedTokens {
		return fmt.Errorf("%s exceed their enclosing cost totals", name)
	}
	return nil
}

func validateMaterializedCostSource(source MaterializedCostSource, name string) (materializedCostSums, error) {
	var sums materializedCostSums
	if err := sums.add(source.TotalTokens, source.MicroUSD, source.APIListMicroUSD, int64(source.PricedAtoms), int64(source.UnpricedAtoms), source.PricedTokens, source.UnpricedTokens, name); err != nil {
		return materializedCostSums{}, err
	}
	dimensions, err := materializedCostDimensionSums(source.Dimensions, name+" dimensions")
	if err != nil {
		return materializedCostSums{}, err
	}
	if len(source.Dimensions) > 0 && (dimensions.tokens != source.TotalTokens || dimensions.microUSD != source.MicroUSD || dimensions.apiListMicroUSD != source.APIListMicroUSD) {
		return materializedCostSums{}, fmt.Errorf("%s dimensions disagree with totals", name)
	}
	return sums, nil
}

func validateMaterializedCostAttribution(attribution MaterializedCostAttribution, name string) (materializedCostSums, error) {
	var sums materializedCostSums
	if err := sums.add(attribution.TotalTokens, attribution.MicroUSD, attribution.APIListMicroUSD, int64(attribution.PricedAtoms), int64(attribution.UnpricedAtoms), attribution.PricedTokens, attribution.UnpricedTokens, name); err != nil {
		return materializedCostSums{}, err
	}
	dimensions, err := materializedCostDimensionSums(attribution.Dimensions, name+" dimensions")
	if err != nil {
		return materializedCostSums{}, err
	}
	if len(attribution.Dimensions) > 0 && (dimensions.tokens != attribution.TotalTokens || dimensions.microUSD != attribution.MicroUSD || dimensions.apiListMicroUSD != attribution.APIListMicroUSD) {
		return materializedCostSums{}, fmt.Errorf("%s dimensions disagree with totals", name)
	}
	var sources materializedCostSums
	for _, source := range attribution.Sources {
		sourceSums, err := validateMaterializedCostSource(source, name+" source")
		if err != nil {
			return materializedCostSums{}, err
		}
		if err := sources.add(sourceSums.tokens, sourceSums.microUSD, sourceSums.apiListMicroUSD, sourceSums.pricedAtoms, sourceSums.unpricedAtoms, sourceSums.pricedTokens, sourceSums.unpricedTokens, name+" sources"); err != nil {
			return materializedCostSums{}, err
		}
	}
	if err := validateCostTotalsWithin(sources, sums, name+" sources"); err != nil {
		return materializedCostSums{}, err
	}
	return sums, nil
}

func validateMaterializedCostRange(layer MaterializedLayer) error {
	var rangeTotals materializedCostSums
	var seriesTotals = map[string]int64{}
	seriesSeen := map[string]bool{}
	for bucketIndex, bucket := range layer.Buckets {
		var bucketTotals materializedCostSums
		bucketSeriesSeen := map[string]bool{}
		cost := bucket.Cost
		if err := bucketTotals.add(cost.Tokens, cost.MicroUSD, cost.APIListMicroUSD, int64(cost.PricedAtoms), int64(cost.UnpricedAtoms), cost.PricedTokens, cost.UnpricedTokens, "cost bucket"); err != nil {
			return err
		}
		if err := rangeTotals.add(bucketTotals.tokens, bucketTotals.microUSD, bucketTotals.apiListMicroUSD, bucketTotals.pricedAtoms, bucketTotals.unpricedAtoms, bucketTotals.pricedTokens, bucketTotals.unpricedTokens, "cost report"); err != nil {
			return err
		}
		for _, item := range bucket.Series {
			if item.Name != "usage_tokens" && item.Name != "cost_micro_usd" && item.Name != "api_list_cost_micro_usd" {
				continue
			}
			if math.IsNaN(item.Value) || math.IsInf(item.Value, 0) || item.Value < 0 || item.Value > materializerMaxSafeInteger || item.Value != math.Trunc(item.Value) {
				return fmt.Errorf("%s must be an exact JSON-safe integer", item.Name)
			}
			if bucketSeriesSeen[item.Name] {
				return fmt.Errorf("duplicate cost series %q", item.Name)
			}
			bucketSeriesSeen[item.Name] = true
			seriesSeen[item.Name] = true
			value := int64(item.Value)
			var total int64
			var err error
			if total, err = addSafeJSONInteger(seriesTotals[item.Name], value, "cost report "+item.Name); err != nil {
				return err
			}
			seriesTotals[item.Name] = total
			var typed int64
			switch item.Name {
			case "usage_tokens":
				typed = cost.Tokens
			case "cost_micro_usd":
				typed = cost.MicroUSD
			case "api_list_cost_micro_usd":
				typed = cost.APIListMicroUSD
			}
			if value != typed {
				return fmt.Errorf("bucket %d %s disagrees with typed cost detail", bucketIndex, item.Name)
			}
		}
		dimensionSums, err := materializedCostDimensionSums(cost.Dimensions, "cost bucket dimensions")
		if err != nil {
			return err
		}
		if len(cost.Dimensions) > 0 && (dimensionSums.tokens != cost.Tokens || dimensionSums.microUSD != cost.MicroUSD || dimensionSums.apiListMicroUSD != cost.APIListMicroUSD) {
			return fmt.Errorf("bucket %d cost dimensions disagree with typed cost totals", bucketIndex)
		}
		for _, family := range []struct {
			name string
			rows []MaterializedCostAttribution
		}{{"models", cost.Models}, {"agents", cost.Agents}} {
			var familySums materializedCostSums
			for _, row := range family.rows {
				rowSums, err := validateMaterializedCostAttribution(row, "cost "+family.name)
				if err != nil {
					return err
				}
				if err := familySums.add(rowSums.tokens, rowSums.microUSD, rowSums.apiListMicroUSD, rowSums.pricedAtoms, rowSums.unpricedAtoms, rowSums.pricedTokens, rowSums.unpricedTokens, "cost "+family.name); err != nil {
					return err
				}
			}
			if err := validateCostTotalsWithin(familySums, bucketTotals, "cost "+family.name); err != nil {
				return err
			}
		}
		var evidenceSums materializedCostSums
		for _, evidence := range cost.Evidence {
			if err := evidenceSums.add(evidence.Tokens, evidence.MicroUSD, evidence.APIListMicroUSD, evidence.PricedAtoms, 0, evidence.Tokens, 0, "cost evidence"); err != nil {
				return err
			}
		}
		if err := validateCostTotalsWithin(evidenceSums, bucketTotals, "cost evidence"); err != nil {
			return err
		}
	}
	for seriesName, typed := range map[string]int64{
		"usage_tokens": rangeTotals.tokens, "cost_micro_usd": rangeTotals.microUSD,
		"api_list_cost_micro_usd": rangeTotals.apiListMicroUSD,
	} {
		if seriesSeen[seriesName] && seriesTotals[seriesName] != typed {
			return fmt.Errorf("cost report %s disagrees with typed cost totals", seriesName)
		}
	}
	return nil
}

// coverageGapFamilies are the Python families whose series represent sampled
// telemetry. Usage and cost are event-derived and therefore must not turn an
// atom-free interval into a fabricated outage.
var coverageGapFamilies = map[string]string{
	"cpu":           "cpu",
	"agent_status":  "agent_status",
	"gpu":           "gpu",
	"service_load":  "service_load",
	"system_memory": "system_memory",
	"system_disk":   "system_disk",
	"browser":       "browser",
}

type materializerCoverageInterval struct {
	epoch CoverageEpoch
	start float64
	end   float64
}

func (a *materializerAccumulator) addCoverageGaps() {
	coverageBySource := make(map[string][]materializerCoverageInterval)
	latestFamilyEnd := make(map[string]float64)
	sourcesByFamily := make(map[string]map[string]struct{})
	for _, epoch := range a.coverage {
		end := a.req.End
		if epoch.EndedAt != nil {
			end = *epoch.EndedAt
		}
		key := epoch.Family + "\x00" + epoch.SourceID
		coverageBySource[key] = append(coverageBySource[key], materializerCoverageInterval{epoch: epoch, start: epoch.StartedAt, end: end})
		if _, ok := sourcesByFamily[epoch.Family]; !ok {
			sourcesByFamily[epoch.Family] = make(map[string]struct{})
		}
		sourcesByFamily[epoch.Family][epoch.SourceID] = struct{}{}
		if _, ok := latestFamilyEnd[epoch.Family]; !ok {
			latestFamilyEnd[epoch.Family] = a.req.Start
		}
		if end > latestFamilyEnd[epoch.Family] {
			latestFamilyEnd[epoch.Family] = end
		}
	}

	explicitBySource := make(map[string][]MaterializedNoData)
	for _, gap := range a.unavailable {
		coverageFamily, eligible := coverageGapFamilies[gap.Family]
		if !eligible {
			continue
		}
		gap.Family = coverageFamily
		key := coverageFamily + "\x00" + gap.SourceID
		explicitBySource[key] = append(explicitBySource[key], gap)
		if _, ok := sourcesByFamily[coverageFamily]; !ok {
			sourcesByFamily[coverageFamily] = make(map[string]struct{})
		}
		sourcesByFamily[coverageFamily][gap.SourceID] = struct{}{}
	}

	// Explicit unavailable spans are authoritative for every family, including
	// event-derived agent_tokens/cost. Computed gaps are added only for the
	// sampled families above.
	computed := append([]MaterializedNoData(nil), a.unavailable...)
	for family, displayFamily := range coverageGapFamilies {
		for sourceID := range sourcesByFamily[family] {
			key := family + "\x00" + sourceID
			explicit := append([]MaterializedNoData(nil), explicitBySource[key]...)
			sort.SliceStable(explicit, func(i, j int) bool {
				if explicit[i].Start != explicit[j].Start {
					return explicit[i].Start < explicit[j].Start
				}
				if explicit[i].End != explicit[j].End {
					return explicit[i].End < explicit[j].End
				}
				return explicit[i].EpochID < explicit[j].EpochID
			})
			intervals := append([]materializerCoverageInterval(nil), coverageBySource[key]...)
			sort.SliceStable(intervals, func(i, j int) bool {
				if intervals[i].epoch.StartedAt != intervals[j].epoch.StartedAt {
					return intervals[i].epoch.StartedAt < intervals[j].epoch.StartedAt
				}
				return intervals[i].epoch.EpochID < intervals[j].epoch.EpochID
			})
			if len(intervals) == 0 {
				continue
			}
			cursor := math.Max(a.req.Start, intervals[0].epoch.StartedAt)
			previous := intervals[0].epoch
			for _, interval := range intervals {
				if interval.end <= a.req.Start || interval.start >= a.req.End {
					previous = interval.epoch
					continue
				}
				if interval.start > cursor {
					appendUncoveredMaterializerGap(&computed, explicit, displayFamily, sourceID, previous.EpochID, cursor, interval.start, previous.NativeCadenceSeconds)
				}
				if interval.end > cursor {
					cursor = interval.end
				}
				previous = interval.epoch
			}
			if cursor < a.req.End && previous.EndedAt != nil && *previous.EndedAt >= latestFamilyEnd[family] {
				appendUncoveredMaterializerGap(&computed, explicit, displayFamily, sourceID, previous.EpochID, cursor, a.req.End, previous.NativeCadenceSeconds)
			}
		}
	}
	// Existing explicit spans were already appended by addUnavailable. Replace
	// the list with the canonical explicit-plus-computed ordering so callers see
	// one Python-compatible no-data stream rather than duplicates.
	sort.SliceStable(computed, func(i, j int) bool {
		if computed[i].Family != computed[j].Family {
			return computed[i].Family < computed[j].Family
		}
		if computed[i].SourceID != computed[j].SourceID {
			return computed[i].SourceID < computed[j].SourceID
		}
		if computed[i].Start != computed[j].Start {
			return computed[i].Start < computed[j].Start
		}
		if computed[i].End != computed[j].End {
			return computed[i].End < computed[j].End
		}
		return computed[i].EpochID < computed[j].EpochID
	})
	a.layer.NoData = computed
}

func appendUncoveredMaterializerGap(out *[]MaterializedNoData, explicit []MaterializedNoData, family, sourceID, epochID string, start, end, cadence float64) {
	if start >= end {
		return
	}
	cursor := start
	for _, existing := range explicit {
		if existing.End <= cursor {
			continue
		}
		if existing.Start >= end {
			break
		}
		if existing.Start > cursor {
			*out = append(*out, MaterializedNoData{Family: family, SourceID: sourceID, EpochID: epochID, Reason: "coverage_gap", Start: cursor, End: math.Min(existing.Start, end), NativeCadenceSeconds: cadence})
		}
		if existing.End > cursor {
			cursor = existing.End
		}
		if cursor >= end {
			return
		}
	}
	if cursor < end {
		*out = append(*out, MaterializedNoData{Family: family, SourceID: sourceID, EpochID: epochID, Reason: "coverage_gap", Start: cursor, End: end, NativeCadenceSeconds: cadence})
	}
}

// normalizeUnavailable mirrors the Python store's historical-span repair. Old
// databases can contain overlapping markers even though current writes reject
// them, so the materializer must make the wire representation safe on read.
func normalizeUnavailable(req MaterializeRequest, unavailable []UnavailableSpan) ([]MaterializedNoData, error) {
	type candidate struct {
		span       UnavailableSpan
		start, end float64
	}
	candidates := make([]candidate, 0, len(unavailable))
	for i, span := range unavailable {
		if err := span.Validate(); err != nil {
			return nil, fmt.Errorf("unavailable[%d]: %w", i, err)
		}
		start := math.Max(req.Start, span.StartedAt)
		end := math.Min(req.End, span.EndedAt)
		if start < end {
			candidates = append(candidates, candidate{span: span, start: start, end: end})
		}
	}
	sort.SliceStable(candidates, func(i, j int) bool {
		left, right := candidates[i], candidates[j]
		if left.span.Family != right.span.Family {
			return left.span.Family < right.span.Family
		}
		if left.span.SourceID != right.span.SourceID {
			return left.span.SourceID < right.span.SourceID
		}
		if left.start != right.start {
			return left.start < right.start
		}
		if left.end != right.end {
			return left.end < right.end
		}
		if left.span.EpochID != right.span.EpochID {
			return left.span.EpochID < right.span.EpochID
		}
		if left.span.Reason != right.span.Reason {
			return left.span.Reason < right.span.Reason
		}
		if left.span.NativeCadenceSeconds != right.span.NativeCadenceSeconds {
			return left.span.NativeCadenceSeconds < right.span.NativeCadenceSeconds
		}
		return left.span.OwnerGeneration < right.span.OwnerGeneration
	})

	accepted := make([]MaterializedNoData, 0, len(candidates))
	sourceEnd := make(map[string]float64)
	for _, item := range candidates {
		key := item.span.Family + "\x00" + item.span.SourceID
		start := item.start
		previousEnd, hasPrevious := sourceEnd[key]
		if hasPrevious && start < previousEnd {
			start = previousEnd
		}
		if start >= item.end {
			continue
		}
		current := MaterializedNoData{
			Family: item.span.Family, SourceID: item.span.SourceID, EpochID: item.span.EpochID,
			Reason: item.span.Reason, Start: start, End: item.end,
			NativeCadenceSeconds: item.span.NativeCadenceSeconds,
		}
		if len(accepted) > 0 {
			previous := accepted[len(accepted)-1]
			if previous.Family == current.Family && previous.SourceID == current.SourceID &&
				strings.HasPrefix(current.SourceID, "retired-unavailable:") &&
				previous.End == current.Start && previous.NativeCadenceSeconds == current.NativeCadenceSeconds &&
				previous.Reason == current.Reason {
				accepted[len(accepted)-1].End = current.End
			} else {
				accepted = append(accepted, current)
			}
		} else {
			accepted = append(accepted, current)
		}
		if current.End > sourceEnd[key] {
			sourceEnd[key] = current.End
		}
	}
	return accepted, nil
}

type fold struct {
	op             string
	total          compensatedTotal
	count          int
	min, max, best float64
	bestAt         float64
	bestSource     string
	sources        map[string][]float64
	first, last    float64
}

// compensatedTotal mirrors CPython's compensated float sum. Materialized
// values are folded in storage order, and a plain float64 += loses low-order
// terms when a large sample precedes smaller samples. Keeping the correction
// term makes Go's average/rate/sum values bit-for-bit compatible with Python.
type compensatedTotal struct {
	total, compensation float64
}

func (s *compensatedTotal) add(value float64) {
	next := s.total + value
	if math.Abs(s.total) >= math.Abs(value) {
		s.compensation += (s.total - next) + value
	} else {
		s.compensation += (value - next) + s.total
	}
	s.total = next
}

func (s compensatedTotal) value() float64 { return s.total + s.compensation }

func compensatedSum(values []float64) float64 {
	var total compensatedTotal
	for _, value := range values {
		total.add(value)
	}
	return total.value()
}

func (f *fold) add(v, at float64, source string) {
	if f.count == 0 {
		f.min, f.max, f.best, f.bestAt, f.bestSource, f.first, f.last = v, v, v, at, source, at, at
	} else {
		if v < f.min {
			f.min = v
		}
		if v > f.max {
			f.max = v
		}
		if at > f.bestAt || (at == f.bestAt && source > f.bestSource) {
			f.best, f.bestAt, f.bestSource = v, at, source
		}
		if at < f.first {
			f.first = at
		}
		if at > f.last {
			f.last = at
		}
	}
	f.total.add(v)
	f.count++
	if f.sources != nil {
		f.sources[source] = append(f.sources[source], v)
	}
}
func (f fold) value(duration float64) float64 {
	switch f.op {
	case "average":
		return f.total.value() / float64(f.count)
	case "minimum":
		return f.min
	case "maximum":
		return f.max
	case "sum":
		return f.total.value()
	case "rate":
		return f.total.value() / duration
	case "rate_per_minute":
		return f.total.value() * 60 / duration
	case "average_sources", "rate_average_sources", "sum_average_sources":
		var total float64
		for _, values := range f.sources {
			s := compensatedSum(values)
			if f.op == "rate_average_sources" {
				total += s / duration
			} else if f.op == "sum_average_sources" {
				total += s
			} else {
				total += s / float64(len(values))
			}
		}
		if len(f.sources) == 0 {
			return 0
		}
		return total / float64(len(f.sources))
	case "gauge", "status":
		return f.best
	}
	return 0
}

func (f fold) sourceCount() int {
	switch f.op {
	case "average_sources", "rate_average_sources", "sum_average_sources":
		// Source-wise folds first reduce each client/source, then combine those
		// reductions. The wire count is therefore distinct sources, not samples.
		return len(f.sources)
	default:
		return f.count
	}
}

func foldSeries(b *MaterializedBucket, name, op string, v, at float64, source string) {
	m := b.folds
	if m == nil {
		m = map[string]*fold{}
		b.folds = m
	}
	f := m[name]
	if f == nil {
		f = &fold{op: op, sources: map[string][]float64{}}
		if op != "average_sources" && op != "rate_average_sources" && op != "sum_average_sources" {
			f.sources = nil
		}
		m[name] = f
	}
	f.add(v, at, source)
}

func recordBucketFact(b *MaterializedBucket, at float64) {
	if b.FirstObservedAt == nil || at < *b.FirstObservedAt {
		value := at
		b.FirstObservedAt = &value
	}
	if b.LastObservedAt == nil || at > *b.LastObservedAt {
		value := at
		b.LastObservedAt = &value
	}
}

func addObservation(b *MaterializedBucket, o Observation) bool {
	p := o.Payload
	at, src := o.ObservedAt, o.SourceID
	added := false
	add := func(name, op string, value float64) {
		foldSeries(b, name, op, value, at, src)
		added = true
	}
	num := func(k string) (float64, bool) {
		v, ok := p[k]
		if !ok {
			return 0, false
		}
		n, e := number(v)
		return n, e == nil
	}
	if incomplete, ok := p["process_census_incomplete"].(bool); ok && incomplete {
		add("process_census_incomplete:"+o.Family, "maximum", 1)
	}
	switch o.Family {
	case "cpu":
		if v, ok := num("process_percent"); ok {
			add("cpu_percent:"+src, "average", v)
			add("cpu_min_percent:"+src, "minimum", v)
			add("cpu_max_percent:"+src, "maximum", v)
		}
		if processCPU, ok := p["process_cpu_percent"].(map[string]any); ok {
			for binary, raw := range processCPU {
				v, err := number(raw)
				if err != nil {
					continue
				}
				add("process_cpu_percent:"+binary, "average", v)
				add("process_cpu_min_percent:"+binary, "minimum", v)
				add("process_cpu_max_percent:"+binary, "maximum", v)
			}
		}
		if ownedProcessCPU, ok := p["owned_process_cpu_percent"].(map[string]any); ok {
			for binary, raw := range ownedProcessCPU {
				v, err := number(raw)
				if err != nil {
					continue
				}
				add("owned_process_cpu_percent:"+binary, "average", v)
				add("owned_process_cpu_min_percent:"+binary, "minimum", v)
				add("owned_process_cpu_max_percent:"+binary, "maximum", v)
			}
		}
		if v, ok := num("system_percent"); ok {
			add("system_cpu_percent", "average", v)
			add("system_cpu_min_percent", "minimum", v)
			add("system_cpu_max_percent", "maximum", v)
		}
	case "service_load":
		if v, ok := num("cpu_percent"); ok {
			add("service_cpu_percent:"+src, "average", v)
			add("service_cpu_min_percent:"+src, "minimum", v)
			add("service_cpu_max_percent:"+src, "maximum", v)
		}
		if v, ok := num("rss_bytes"); ok {
			add("service_rss_bytes:"+src, "average", v)
		}
	case "gpu":
		for _, x := range []struct{ n, k string }{{"gpu_util_percent", "util_percent"}, {"gpu_memory_bytes", "memory_used_bytes"}} {
			if v, ok := num(x.k); ok {
				add(x.n+":"+src, "gauge", v)
			}
		}
	case "system_memory":
		for _, x := range []struct{ n, k string }{{"system_memory_used_bytes", "used_bytes"}, {"system_memory_capacity_bytes", "capacity_bytes"}, {"mac_physical_memory_bytes", "mac_physical_memory_bytes"}, {"mac_memory_used_bytes", "mac_memory_used_bytes"}, {"mac_cached_files_bytes", "mac_cached_files_bytes"}, {"mac_swap_used_bytes", "mac_swap_used_bytes"}, {"mac_app_memory_bytes", "mac_app_memory_bytes"}, {"mac_wired_memory_bytes", "mac_wired_memory_bytes"}, {"mac_compressed_memory_bytes", "mac_compressed_memory_bytes"}, {"mac_pressure_percent", "mac_pressure_percent"}, {"mac_pressure_level", "mac_pressure_level"}} {
			if v, ok := num(x.k); ok {
				add(x.n, "gauge", v)
			}
		}
		if processMemory, ok := p["process_memory_bytes"].(map[string]any); ok {
			for binary, raw := range processMemory {
				if v, err := number(raw); err == nil {
					add("process_memory_bytes:"+binary, "gauge", v)
				}
			}
		}
	case "system_disk":
		if mountpoint, valid := p["mountpoint"].(string); valid && mountpoint != "" {
			if _, hasUsage := p["used_percent"]; !hasUsage {
				add("system_disk_inventory:"+mountpoint, "gauge", 1)
			}
			if v, ok := num("used_percent"); ok {
				add("system_disk_used_percent:"+mountpoint, "gauge", v)
			}
		}
	case "agent_status":
		if states, ok := p["states"].(map[string]any); ok {
			for _, s := range []string{"ask", "run", "transition", "idle"} {
				var n float64
				for _, v := range states {
					if v == s {
						n++
					}
				}
				add(s+"_agents", "status", n)
			}
		}
		if states, ok := p["session_states"].(map[string]any); ok {
			for _, s := range []string{"ask", "run", "transition", "idle"} {
				var n float64
				for _, v := range states {
					if v == s {
						n++
					}
				}
				add(s+"_sessions", "status", n)
			}
		}
		if v, ok := num("snapshot_revision"); ok {
			add("agent_window_snapshot_revision", "gauge", v)
		}
	case "browser":
		kind, _ := p["kind"].(string)
		if kind == "api" || kind == "sse" {
			add("browser_"+kind+"_per_second", "rate_average_sources", 1)
		}
		if v, ok := num("latency_ms"); ok {
			add("browser_latency_ms", "average_sources", v)
			// Retain the legacy mixed series for historical readers. New charts use typed measurements.
			if name := map[string]string{"heartbeat": "browser_ping_rtt_ms", "api": "browser_api_duration_ms", "sse": "browser_sse_publication_age_ms"}[kind]; name != "" && (kind != "heartbeat" || v > 0) {
				add(name, "average_sources", v)
			}
			m := map[string]string{"finder_usable": "browser_finder_usable_ms", "interaction": "browser_input_latency_ms", "operation_wait": "browser_operation_wait_ms", "long_task": "browser_long_task_ms"}
			if n := m[kind]; n != "" {
				add(n, "average_sources", v)
			}
		}
		if v, ok := num("bytes"); ok {
			add("browser_bandwidth_bytes_per_second", "rate_average_sources", v)
		}
		if kind == "disconnect" {
			if v, ok := num("duration_ms"); ok {
				add("browser_disconnected_ms", "sum_average_sources", v)
			}
		}
		if kind == "api" {
			if v, ok := num("queue_ms"); ok {
				add("browser_queue_ms", "average_sources", v)
			}
		}
		if kind == "page_load" {
			for _, x := range []struct{ n, k string }{{"browser_first_paint_ms", "first_paint_ms"}, {"browser_first_contentful_paint_ms", "first_contentful_paint_ms"}, {"browser_app_ready_ms", "app_ready_ms"}, {"browser_page_max_concurrency", "max_concurrency"}} {
				if v, ok := num(x.k); ok {
					add(x.n, "average_sources", v)
				}
			}
		}
		if kind == "heartbeat" {
			for _, x := range []struct{ n, k string }{{"browser_upload_queue_depth", "upload_queue_depth"}, {"browser_upload_drops", "upload_drops"}, {"browser_upload_retries", "upload_retries"}, {"browser_instrumentation_cost_ms", "instrumentation_cost_ms"}} {
				if v, ok := num(x.k); ok {
					add(x.n, "average_sources", v)
				}
			}
		}
	}
	return added
}

func addUsage(b *MaterializedBucket, a UsageAtom, price PriceResolver) (bool, error) {
	q, e := number(a.Payload["quantity"])
	if e != nil || q < 0 || q > materializerMaxSafeInteger || q != math.Trunc(q) {
		return false, fmt.Errorf("usage quantity must be a JSON-safe integer")
	}
	dimension := usageCostDimension(a)
	agent, _ := a.Payload["agent_id"].(string)
	model, _ := a.Payload["model"].(string)
	provider, _ := a.Payload["provider"].(string)
	source, _ := a.Payload["execution_source"].(string)
	source = privacySafeExecutionSource(source)
	added := false
	add := func(name, op string, value float64) {
		foldSeries(b, name, op, value, a.ObservedAt, a.EventID)
		added = true
	}
	if b.costDimensions == nil {
		b.costDimensions = make(map[string]*MaterializedCostDimension)
		for _, name := range costReportDimensions {
			b.costDimensions[name] = &MaterializedCostDimension{Dimension: name}
		}
	}
	costDimension := b.costDimensions[dimension]
	if costDimension == nil {
		costDimension = &MaterializedCostDimension{Dimension: dimension}
		b.costDimensions[dimension] = costDimension
	}
	if a.Unit == "tokens" {
		tokens, err := addSafeJSONInteger(b.Cost.Tokens, int64(q), "cost bucket tokens")
		if err != nil {
			return false, err
		}
		b.Cost.Tokens = tokens
		costDimension.Tokens += int64(q)
		if model != "" {
			if b.costModels == nil {
				b.costModels = make(map[string]*MaterializedCostAttribution)
			}
			key := stableCostKey(provider, model)
			modelDetail := b.costModels[key]
			if modelDetail == nil {
				modelDetail = &MaterializedCostAttribution{Key: key, Provider: provider, Model: model}
				b.costModels[key] = modelDetail
			}
			modelDetail.TotalTokens += int64(q)
			addAttributionDimension(modelDetail, usageCostDimension(a), int64(q), 0, 0)
		}
		if agent != "" {
			if b.costAgents == nil {
				b.costAgents = make(map[string]*MaterializedCostAttribution)
			}
			key := stableCostKey(agent)
			agentDetail := b.costAgents[key]
			if agentDetail == nil {
				agentDetail = &MaterializedCostAttribution{Key: key, Source: source, Label: privacySafeAgentLabel(agent, source)}
				b.costAgents[key] = agentDetail
			}
			agentDetail.TotalTokens += int64(q)
			addAttributionDimension(agentDetail, usageCostDimension(a), int64(q), 0, 0)
		}
		if agent != "" && model != "" {
			// Keep model-token series on the same canonical dimensions used by
			// cost attribution and Python's materializer (cache_read,
			// cache_write_5m, cache_write_1h), rather than the storage role
			// spelling (read/write_5m/write_1h).
			dim := usageCostDimension(a)
			add("model_tokens_per_minute:"+dim+":"+model, "rate_per_minute", q)
			add("model_tokens_per_minute:all:"+model, "rate_per_minute", q)
			if a.Direction == "output" {
				add("agent_tokens_per_minute:"+agent, "rate_per_minute", q)
			}
		}
		add("usage_tokens", "sum", q)
	}
	var (
		m, l int64
		ev   *PriceEvidence
		ok   bool
		err  error
	)
	if price != nil {
		m, l, ev, ok, err = price(a)
		if err != nil {
			return false, err
		}
	}
	if ok {
		if m < 0 || m > maxSafeJSONInteger || l < 0 || l > maxSafeJSONInteger {
			return false, fmt.Errorf("price resolver must return non-negative JSON-safe integer micro-USD")
		}
		bucketMicroUSD, err := addSafeJSONInteger(b.Cost.MicroUSD, m, "cost bucket micro_usd")
		if err != nil {
			return false, err
		}
		bucketAPIListMicroUSD, err := addSafeJSONInteger(b.Cost.APIListMicroUSD, l, "cost bucket API-list micro_usd")
		if err != nil {
			return false, err
		}
		add("cost_micro_usd", "sum", float64(m))
		add("api_list_cost_micro_usd", "sum", float64(l))
		b.Cost.MicroUSD = bucketMicroUSD
		b.Cost.APIListMicroUSD = bucketAPIListMicroUSD
		costDimension.MicroUSD += m
		costDimension.APIListMicroUSD += l
		b.Cost.PricedAtoms++
		b.Cost.PricedTokens += int64(q)
		for _, detail := range []*MaterializedCostAttribution{b.costModels[stableCostKey(provider, model)], b.costAgents[stableCostKey(agent)]} {
			if detail != nil {
				detail.MicroUSD += m
				detail.APIListMicroUSD += l
				detail.PricedAtoms++
				detail.PricedTokens += int64(q)
				addAttributionDimension(detail, usageCostDimension(a), 0, m, l)
				if agent != "" && detail == b.costAgents[stableCostKey(agent)] {
					addAttributionSource(detail, source, usageCostDimension(a), int64(q), m, l, true)
				}
			}
		}
		if ev != nil {
			b.Cost.Evidence = append(b.Cost.Evidence, *ev)
		}
	} else {
		b.Cost.UnpricedAtoms++
		b.Cost.UnpricedTokens += int64(q)
		provider, _ := a.Payload["provider"].(string)
		model, _ := a.Payload["model"].(string)
		agent, _ := a.Payload["agent_id"].(string)
		for _, detail := range []*MaterializedCostAttribution{b.costModels[stableCostKey(provider, model)], b.costAgents[stableCostKey(agent)]} {
			if detail != nil {
				detail.UnpricedAtoms++
				detail.UnpricedTokens += int64(q)
				if agent != "" && detail == b.costAgents[stableCostKey(agent)] {
					addAttributionSource(detail, source, usageCostDimension(a), int64(q), 0, 0, false)
				}
			}
		}
	}
	return added, nil
}

func addSafeJSONInteger(total, value int64, name string) (int64, error) {
	if total < 0 || total > maxSafeJSONInteger || value < 0 || value > maxSafeJSONInteger-total {
		return 0, fmt.Errorf("%s exceeds the exact JSON integer range", name)
	}
	return total + value, nil
}
func finalizeBucket(b *MaterializedBucket) {
	if m := b.folds; m != nil {
		for n, f := range m {
			v := f.value(b.Duration)
			b.Series = append(b.Series, MaterializedSeries{n, v, f.sourceCount(), f.first, f.last})
		}
		sort.Slice(b.Series, func(i, j int) bool { return b.Series[i].Name < b.Series[j].Name })
		b.folds = nil
	}
	if len(b.costDimensions) > 0 {
		b.Cost.Dimensions = make([]MaterializedCostDimension, 0, len(b.costDimensions))
		for _, value := range b.costDimensions {
			b.Cost.Dimensions = append(b.Cost.Dimensions, *value)
		}
		sortCostDimensions(b.Cost.Dimensions)
		b.costDimensions = nil
	}
	if len(b.costModels) > 0 {
		b.Cost.Models = sortedAttributions(b.costModels)
		b.costModels = nil
	}
	if len(b.costAgents) > 0 {
		b.Cost.Agents = sortedAttributions(b.costAgents)
		b.costAgents = nil
	}
}

func addAttributionSource(detail *MaterializedCostAttribution, source, dimension string, tokens, microUSD, apiListMicroUSD int64, priced bool) {
	for index := range detail.Sources {
		item := &detail.Sources[index]
		if item.Source != source {
			continue
		}
		item.TotalTokens += tokens
		item.MicroUSD += microUSD
		item.APIListMicroUSD += apiListMicroUSD
		if priced {
			item.PricedAtoms++
			item.PricedTokens += tokens
		} else {
			item.UnpricedAtoms++
			item.UnpricedTokens += tokens
		}
		addAttributionDimensionSource(item, dimension, tokens, microUSD, apiListMicroUSD)
		return
	}
	item := MaterializedCostSource{Source: source, TotalTokens: tokens, MicroUSD: microUSD, APIListMicroUSD: apiListMicroUSD}
	if priced {
		item.PricedAtoms = 1
		item.PricedTokens = tokens
	} else {
		item.UnpricedAtoms = 1
		item.UnpricedTokens = tokens
	}
	addAttributionDimensionSource(&item, dimension, tokens, microUSD, apiListMicroUSD)
	detail.Sources = append(detail.Sources, item)
}

func addAttributionDimensionSource(detail *MaterializedCostSource, dimension string, tokens, microUSD, apiListMicroUSD int64) {
	ensureCostDimensionRows(&detail.Dimensions)
	for index := range detail.Dimensions {
		if detail.Dimensions[index].Dimension == dimension {
			detail.Dimensions[index].Tokens += tokens
			detail.Dimensions[index].MicroUSD += microUSD
			detail.Dimensions[index].APIListMicroUSD += apiListMicroUSD
			return
		}
	}
	detail.Dimensions = append(detail.Dimensions, MaterializedCostDimension{Dimension: dimension, Tokens: tokens, MicroUSD: microUSD, APIListMicroUSD: apiListMicroUSD})
}

func addAttributionDimension(detail *MaterializedCostAttribution, dimension string, tokens, microUSD, apiListMicroUSD int64) {
	ensureCostDimensionRows(&detail.Dimensions)
	for index := range detail.Dimensions {
		if detail.Dimensions[index].Dimension == dimension {
			detail.Dimensions[index].Tokens += tokens
			detail.Dimensions[index].MicroUSD += microUSD
			detail.Dimensions[index].APIListMicroUSD += apiListMicroUSD
			return
		}
	}
	detail.Dimensions = append(detail.Dimensions, MaterializedCostDimension{Dimension: dimension, Tokens: tokens, MicroUSD: microUSD, APIListMicroUSD: apiListMicroUSD})
}

func ensureCostDimensionRows(dimensions *[]MaterializedCostDimension) {
	if *dimensions != nil {
		return
	}
	rows := make([]MaterializedCostDimension, 0, len(costReportDimensions))
	for _, name := range costReportDimensions {
		rows = append(rows, MaterializedCostDimension{Dimension: name})
	}
	*dimensions = rows
}

func sortedAttributions(values map[string]*MaterializedCostAttribution) []MaterializedCostAttribution {
	result := make([]MaterializedCostAttribution, 0, len(values))
	for _, value := range values {
		sortCostDimensions(value.Dimensions)
		sort.Slice(value.Sources, func(i, j int) bool { return value.Sources[i].Source < value.Sources[j].Source })
		for index := range value.Sources {
			sortCostDimensions(value.Sources[index].Dimensions)
		}
		result = append(result, *value)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Key < result[j].Key })
	return result
}

func sortCostDimensions(values []MaterializedCostDimension) {
	rank := func(name string) int {
		for index, canonical := range costReportDimensions {
			if canonical == name {
				return index
			}
		}
		return len(costReportDimensions)
	}
	sort.SliceStable(values, func(i, j int) bool {
		left, right := rank(values[i].Dimension), rank(values[j].Dimension)
		if left != right {
			return left < right
		}
		return values[i].Dimension < values[j].Dimension
	})
}

func stableCostKey(values ...string) string {
	hash := sha256.New()
	var length [4]byte
	for _, value := range values {
		binary.BigEndian.PutUint32(length[:], uint32(len(value)))
		_, _ = hash.Write(length[:])
		_, _ = hash.Write([]byte(value))
	}
	return fmt.Sprintf("%x", hash.Sum(nil))[:24]
}

func privacySafeExecutionSource(value string) string {
	normalized := strings.ToLower(strings.TrimSpace(value))
	switch normalized {
	case "messages":
		return "claude"
	case "response":
		return "responses"
	case "ai summary", "codex-exec":
		return "summary"
	case "image tool", "image stream":
		return "images"
	case "yo!agent":
		return "yoagent"
	case "claude", "codex", "opencode", "yoagent", "summary", "images", "responses", "legacy", "unknown":
		return normalized
	default:
		return "unknown"
	}
}

func publicTmuxAgentID(value string) bool {
	parts := strings.Split(value, "|")
	if len(parts) < 2 || len(parts) > 4 {
		return false
	}
	if parts[len(parts)-1] != "claude" && parts[len(parts)-1] != "codex" && parts[len(parts)-1] != "opencode" && parts[len(parts)-1] != "term" {
		return false
	}
	if len(parts[0]) == 0 || len(parts[0]) > 96 || !asciiAgentStart(parts[0][0]) {
		return false
	}
	for index := 1; index < len(parts[0]); index++ {
		value := parts[0][index]
		if !asciiAgentStart(value) && value != '.' && value != '_' && value != '-' {
			return false
		}
	}
	for _, part := range parts[1:] {
		if len(part) == 0 || len(part) > 128 || strings.IndexAny(part, " \t\r\n\v\f") >= 0 {
			return false
		}
	}
	return true
}

func asciiAgentStart(value byte) bool {
	return value >= 'A' && value <= 'Z' || value >= 'a' && value <= 'z' || value >= '0' && value <= '9'
}

func privacySafeAgentLabel(agentID, source string) string {
	if publicTmuxAgentID(agentID) {
		return agentID
	}
	if strings.HasPrefix(agentID, "claude-bg:") {
		parts := strings.SplitN(agentID, ":", 4)
		if len(parts) == 4 {
			projectParts := strings.Split(parts[1], "-")
			project := make([]string, 0, len(projectParts))
			for _, part := range projectParts {
				if part != "" {
					project = append(project, part)
				}
			}
			projectName := strings.Join(project, "-")
			if len(projectName) > 32 {
				projectName = projectName[len(projectName)-32:]
			}
			session := parts[2]
			if len(session) > 8 {
				session = session[:8]
			}
			if projectName != "" && session != "" {
				return "claude-bg:" + projectName + ":" + session
			}
		}
	}
	return privacySafeExecutionSource(source) + ":" + stableCostKey(agentID)[:8]
}

func usageCostDimension(a UsageAtom) string {
	if a.Unit != "tokens" || a.Modality != "text" {
		return "other"
	}
	if a.Direction == "output" {
		return "output"
	}
	switch a.CacheRole {
	case "read":
		return "cache_read"
	case "write_5m":
		return "cache_write_5m"
	case "write_1h":
		return "cache_write_1h"
	default:
		return "input"
	}
}
func matBucketIndex(r MaterializeRequest, t float64) int {
	i := int(math.Floor((t - r.Start) / r.Resolution))
	if i < 0 {
		i = 0
	}
	if i >= int(math.Ceil((r.End-r.Start)/r.Resolution)) {
		i = int(math.Ceil((r.End-r.Start)/r.Resolution)) - 1
	}
	return i
}
