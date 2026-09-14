package stats

import (
	"fmt"
	"math"
	"sort"
	"strings"
)

const materializerMaxSafeInteger = float64((1 << 53) - 1)

// Materializer is the pure, read-only current-facts fold. It intentionally
// takes decoded storage records so callers can use it with SQLite snapshots,
// fixtures, or an in-memory replay without involving statsd.
type Materializer struct{ Price PriceResolver }

// PriceResolver supplies both the marginal and API-list counterfactual price.
// Returning ok=false is the exact unavailable/unpriced state.
type PriceResolver func(UsageAtom) (microUSD, apiListMicroUSD int64, evidence *PriceEvidence, ok bool, err error)

type PriceEvidence struct {
	CatalogModel, RateUSD, EffectiveFrom, SourceKind, SourceURL string
	RateScale, CatalogRevision                                  int64
}

type MaterializeRequest struct {
	Start, End float64
	Resolution float64
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
	Evidence                          []PriceEvidence
}

func (m Materializer) Build(req MaterializeRequest, observations []Observation, epochs []CoverageEpoch, unavailable []UnavailableSpan, atoms []UsageAtom) (MaterializedLayer, error) {
	if math.IsNaN(req.Start) || math.IsInf(req.Start, 0) || math.IsNaN(req.End) || math.IsInf(req.End, 0) || req.Start >= req.End || req.Resolution <= 0 || math.IsNaN(req.Resolution) || math.IsInf(req.Resolution, 0) {
		return MaterializedLayer{}, fmt.Errorf("invalid materialization range")
	}
	count := int(math.Ceil((req.End - req.Start) / req.Resolution))
	if count < 1 || count > MaxProjectionInputs {
		return MaterializedLayer{}, fmt.Errorf("invalid materialization bucket count")
	}
	layer := MaterializedLayer{Resolution: req.Resolution, Start: req.Start, End: req.End, Buckets: make([]MaterializedBucket, count)}
	for i := range layer.Buckets {
		layer.Buckets[i] = MaterializedBucket{Start: req.Start + float64(i)*req.Resolution, Duration: math.Min(req.Resolution, req.End-(req.Start+float64(i)*req.Resolution)), Complete: req.Start+float64(i+1)*req.Resolution <= req.End}
	}
	for i, o := range observations {
		if err := o.Validate(); err != nil {
			return MaterializedLayer{}, fmt.Errorf("observations[%d]: %w", i, err)
		}
		if o.ObservedAt < req.Start || o.ObservedAt >= req.End {
			continue
		}
		addObservation(&layer.Buckets[matBucketIndex(req, o.ObservedAt)], o)
	}
	for i, a := range atoms {
		if err := a.Validate(); err != nil {
			return MaterializedLayer{}, fmt.Errorf("usage[%d]: %w", i, err)
		}
		if a.ObservedAt < req.Start || a.ObservedAt >= req.End {
			continue
		}
		if err := addUsage(&layer.Buckets[matBucketIndex(req, a.ObservedAt)], a, m.Price); err != nil {
			return MaterializedLayer{}, fmt.Errorf("usage[%d]: %w", i, err)
		}
	}
	normalizedUnavailable, err := normalizeUnavailable(req, unavailable)
	if err != nil {
		return MaterializedLayer{}, err
	}
	for _, span := range normalizedUnavailable {
		layer.NoData = append(layer.NoData, span)
		for i := matBucketIndex(req, span.Start); i <= matBucketIndex(req, math.Nextafter(span.End, span.Start)); i++ {
			layer.Buckets[i].Complete = false
		}
	}
	// Event-derived usage is not a coverage outage. Observations are covered by
	// their explicit epoch; empty buckets remain no-data, never zero samples.
	for _, epoch := range epochs {
		if epoch.EndedAt != nil && *epoch.EndedAt <= req.Start || epoch.StartedAt >= req.End {
			continue
		}
		end := req.End
		if epoch.EndedAt != nil {
			end = math.Min(end, *epoch.EndedAt)
		}
		start := math.Max(req.Start, epoch.StartedAt)
		for i := matBucketIndex(req, start); i <= matBucketIndex(req, math.Nextafter(end, start)); i++ {
			if i >= 0 && i < len(layer.Buckets) && layer.Buckets[i].Complete {
				layer.Buckets[i].Complete = true
			}
		}
	}
	for i := range layer.Buckets {
		finalizeBucket(&layer.Buckets[i])
	}
	return layer, nil
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
	total          float64
	count          int
	min, max, best float64
	bestAt         float64
	sources        map[string][]float64
	first, last    float64
}

func (f *fold) add(v, at float64, source string) {
	if f.count == 0 {
		f.min, f.max, f.best, f.bestAt, f.first, f.last = v, v, v, at, at, at
	} else {
		if v < f.min {
			f.min = v
		}
		if v > f.max {
			f.max = v
		}
		if at >= f.bestAt {
			f.best, f.bestAt = v, at
		}
		if at < f.first {
			f.first = at
		}
		if at > f.last {
			f.last = at
		}
	}
	f.total += v
	f.count++
	if f.sources != nil {
		f.sources[source] = append(f.sources[source], v)
	}
}
func (f fold) value(duration float64) float64 {
	switch f.op {
	case "average":
		return f.total / float64(f.count)
	case "minimum":
		return f.min
	case "maximum":
		return f.max
	case "sum":
		return f.total
	case "rate":
		return f.total / duration
	case "rate_per_minute":
		return f.total * 60 / duration
	case "average_sources", "rate_average_sources", "sum_average_sources":
		var total float64
		for _, values := range f.sources {
			var s float64
			for _, v := range values {
				s += v
			}
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
func addObservation(b *MaterializedBucket, o Observation) {
	p := o.Payload
	at, src := o.ObservedAt, o.SourceID
	num := func(k string) (float64, bool) {
		v, ok := p[k]
		if !ok {
			return 0, false
		}
		n, e := number(v)
		return n, e == nil
	}
	switch o.Family {
	case "cpu":
		if v, ok := num("process_percent"); ok {
			foldSeries(b, "process_cpu_percent", "average", v, at, src)
			foldSeries(b, "process_cpu_min_percent", "minimum", v, at, src)
			foldSeries(b, "process_cpu_max_percent", "maximum", v, at, src)
		}
		if v, ok := num("system_percent"); ok {
			foldSeries(b, "system_cpu_percent", "average", v, at, src)
			foldSeries(b, "system_cpu_min_percent", "minimum", v, at, src)
			foldSeries(b, "system_cpu_max_percent", "maximum", v, at, src)
		}
	case "service_load":
		if v, ok := num("cpu_percent"); ok {
			foldSeries(b, "service_cpu_percent", "average", v, at, src)
			foldSeries(b, "service_cpu_min_percent", "minimum", v, at, src)
			foldSeries(b, "service_cpu_max_percent", "maximum", v, at, src)
		}
		if v, ok := num("rss_bytes"); ok {
			foldSeries(b, "service_rss_bytes", "average", v, at, src)
		}
	case "gpu":
		for _, x := range []struct{ n, k string }{{"gpu_util_percent", "util_percent"}, {"gpu_memory_bytes", "memory_used_bytes"}} {
			if v, ok := num(x.k); ok {
				foldSeries(b, x.n+":"+src, "gauge", v, at, src)
			}
		}
	case "system_memory":
		for _, x := range []struct{ n, k string }{{"system_memory_used_bytes", "used_bytes"}, {"system_memory_capacity_bytes", "capacity_bytes"}, {"mac_physical_memory_bytes", "mac_physical_memory_bytes"}, {"mac_memory_used_bytes", "mac_memory_used_bytes"}, {"mac_cached_files_bytes", "mac_cached_files_bytes"}, {"mac_swap_used_bytes", "mac_swap_used_bytes"}, {"mac_app_memory_bytes", "mac_app_memory_bytes"}, {"mac_wired_memory_bytes", "mac_wired_memory_bytes"}, {"mac_compressed_memory_bytes", "mac_compressed_memory_bytes"}, {"mac_pressure_percent", "mac_pressure_percent"}, {"mac_pressure_level", "mac_pressure_level"}} {
			if v, ok := num(x.k); ok {
				foldSeries(b, x.n, "gauge", v, at, src)
			}
		}
		if processMemory, ok := p["process_memory_bytes"].(map[string]any); ok {
			for binary, raw := range processMemory {
				if v, err := number(raw); err == nil {
					foldSeries(b, "process_memory_bytes:"+binary, "gauge", v, at, src)
				}
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
				foldSeries(b, s+"_agents", "status", n, at, src)
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
				foldSeries(b, s+"_sessions", "status", n, at, src)
			}
		}
		if v, ok := num("snapshot_revision"); ok {
			foldSeries(b, "agent_window_snapshot_revision", "gauge", v, at, src)
		}
	case "browser":
		kind, _ := p["kind"].(string)
		if kind == "api" || kind == "sse" {
			foldSeries(b, "browser_"+kind+"_per_second", "rate_average_sources", 1, at, src)
		}
		if v, ok := num("latency_ms"); ok {
			foldSeries(b, "browser_latency_ms", "average_sources", v, at, src)
			m := map[string]string{"finder_usable": "browser_finder_usable_ms", "interaction": "browser_input_latency_ms", "operation_wait": "browser_operation_wait_ms", "long_task": "browser_long_task_ms"}
			if n := m[kind]; n != "" {
				foldSeries(b, n, "average_sources", v, at, src)
			}
		}
		if v, ok := num("bytes"); ok {
			foldSeries(b, "browser_bandwidth_bytes_per_second", "rate_average_sources", v, at, src)
		}
		if kind == "disconnect" {
			if v, ok := num("duration_ms"); ok {
				foldSeries(b, "browser_disconnected_ms", "sum_average_sources", v, at, src)
			}
		}
		if kind == "api" {
			if v, ok := num("queue_ms"); ok {
				foldSeries(b, "browser_queue_ms", "average_sources", v, at, src)
			}
		}
		if kind == "page_load" {
			for _, x := range []struct{ n, k string }{{"browser_first_paint_ms", "first_paint_ms"}, {"browser_first_contentful_paint_ms", "first_contentful_paint_ms"}, {"browser_app_ready_ms", "app_ready_ms"}, {"browser_page_max_concurrency", "max_concurrency"}} {
				if v, ok := num(x.k); ok {
					foldSeries(b, x.n, "average_sources", v, at, src)
				}
			}
		}
		if kind == "heartbeat" {
			for _, x := range []struct{ n, k string }{{"browser_upload_queue_depth", "upload_queue_depth"}, {"browser_upload_drops", "upload_drops"}, {"browser_upload_retries", "upload_retries"}, {"browser_instrumentation_cost_ms", "instrumentation_cost_ms"}} {
				if v, ok := num(x.k); ok {
					foldSeries(b, x.n, "average_sources", v, at, src)
				}
			}
		}
	}
}
func addUsage(b *MaterializedBucket, a UsageAtom, price PriceResolver) error {
	q, e := number(a.Payload["quantity"])
	if e != nil || q < 0 || q > materializerMaxSafeInteger || q != math.Trunc(q) {
		return fmt.Errorf("usage quantity must be a JSON-safe integer")
	}
	if a.Unit == "tokens" {
		b.Cost.Tokens += int64(q)
		agent, _ := a.Payload["agent_id"].(string)
		model, _ := a.Payload["model"].(string)
		if agent != "" && model != "" {
			dim := a.Direction
			if a.CacheRole != "none" {
				dim = a.CacheRole
			}
			foldSeries(b, "model_tokens_per_minute:"+dim+":"+model, "rate_per_minute", q, a.ObservedAt, a.EventID)
			foldSeries(b, "model_tokens_per_minute:all:"+model, "rate_per_minute", q, a.ObservedAt, a.EventID)
			if a.Direction == "output" {
				foldSeries(b, "agent_tokens_per_minute:"+agent, "rate_per_minute", q, a.ObservedAt, a.EventID)
			}
		}
		foldSeries(b, "usage_tokens", "sum", q, a.ObservedAt, a.EventID)
	}
	if price != nil {
		m, l, ev, ok, err := price(a)
		if err != nil {
			return err
		}
		if ok {
			foldSeries(b, "cost_micro_usd", "sum", float64(m), a.ObservedAt, a.EventID)
			foldSeries(b, "api_list_cost_micro_usd", "sum", float64(l), a.ObservedAt, a.EventID)
			b.Cost.MicroUSD += m
			b.Cost.APIListMicroUSD += l
			b.Cost.PricedAtoms++
			if ev != nil {
				b.Cost.Evidence = append(b.Cost.Evidence, *ev)
			}
		} else {
			b.Cost.UnpricedAtoms++
		}
	}
	return nil
}
func finalizeBucket(b *MaterializedBucket) {
	if m := b.folds; m != nil {
		for n, f := range m {
			v := f.value(b.Duration)
			b.Series = append(b.Series, MaterializedSeries{n, v, f.count, f.first, f.last})
			b.SourceCount += f.count
		}
		sort.Slice(b.Series, func(i, j int) bool { return b.Series[i].Name < b.Series[j].Name })
		b.folds = nil
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
