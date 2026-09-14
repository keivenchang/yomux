package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/keivenchang/yolomux/internal/stats"
)

const statsWireVersion = 2

const (
	statsWriterProtocol   = 25
	statsSchemaGeneration = 9
	statsClientIDDomain   = "yolomux-stats-client-v1\x00"
)

var statsRanges = []int{300, 900, 1800, 3600, 7200, 14400, 28800, 57600, 86400}

var statsRingCapacities = map[int]int{1: 300, 10: 180, 60: 480, 300: 288}

type statsQuery struct {
	rangeSeconds, resolution, limit     int
	resolutionAuto                      bool
	clientID                            string
	afterCacheGeneration, afterRevision int64
	sinceGeneration                     int64
}

func parseStatsQuery(r *http.Request, delta bool) (statsQuery, map[string]any, bool) {
	if len([]byte(r.URL.RawQuery)) > 2048 {
		return statsQuery{}, statsUnsupported("query is too large"), false
	}
	values, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return statsQuery{}, statsUnsupported("query contains a malformed escape"), false
	}
	allowed := map[string]bool{"range_seconds": true, "client_id": true, "limit": true}
	if delta {
		allowed["resolution_seconds"], allowed["after_cache_generation"], allowed["after_revision"] = true, true, true
	} else {
		allowed["resolution"], allowed["since_generation"] = true, true
	}
	for key, items := range values {
		if !allowed[key] || len(items) != 1 || items[0] == "" {
			return statsQuery{}, statsUnsupported("unknown, duplicate, or empty query parameter: " + key), false
		}
	}
	parse := func(name string, minimum int) (int, bool) {
		value, ok := values[name]
		if !ok || len(value) != 1 {
			return 0, false
		}
		n, err := strconv.Atoi(value[0])
		return n, err == nil && n >= minimum && strconv.Itoa(n) == value[0]
	}
	rangeSeconds, ok := parse("range_seconds", 1)
	if !ok || !containsInt(statsRanges, rangeSeconds) {
		return statsQuery{}, statsUnsupported("unsupported range_seconds"), false
	}
	limit := stats.MaxFactReadLimit
	if _, present := values["limit"]; present {
		limit, ok = parse("limit", 1)
		if !ok || limit > stats.MaxFactReadLimit {
			return statsQuery{}, statsUnsupported("invalid limit"), false
		}
	}
	result := statsQuery{rangeSeconds: rangeSeconds, limit: limit, clientID: values.Get("client_id")}
	if result.clientID == "" || len([]byte(result.clientID)) > 128 {
		return statsQuery{}, statsUnsupported("invalid client_id"), false
	}
	if delta {
		result.resolution, ok = parse("resolution_seconds", 1)
		if !ok || !supportedResolution(rangeSeconds, result.resolution) {
			return statsQuery{}, statsUnsupported("unsupported resolution"), false
		}
		parseGeneration := func(name string) (int64, bool) {
			value, present := values[name]
			if !present || len(value) != 1 {
				return 0, false
			}
			n, err := strconv.ParseInt(value[0], 10, 64)
			return n, err == nil && n >= 0 && strconv.FormatInt(n, 10) == value[0]
		}
		result.afterCacheGeneration, ok = parseGeneration("after_cache_generation")
		if !ok {
			return statsQuery{}, statsUnsupported("invalid after_cache_generation"), false
		}
		result.afterRevision, ok = parseGeneration("after_revision")
		if !ok {
			return statsQuery{}, statsUnsupported("invalid after_revision"), false
		}
	} else {
		if value, present := values["since_generation"]; present {
			generation, err := strconv.ParseInt(value[0], 10, 64)
			if err != nil || generation < 0 || strconv.FormatInt(generation, 10) != value[0] {
				return statsQuery{}, statsUnsupported("invalid since_generation"), false
			}
			result.sinceGeneration = generation
		}
		if _, present := values["resolution"]; !present {
			return statsQuery{}, statsUnsupported("missing query parameter: resolution"), false
		}
		resolution := values.Get("resolution")
		if resolution == "AUTO" {
			result.resolutionAuto = true
			result.resolution = autoResolution(rangeSeconds)
		} else {
			result.resolution, ok = parse("resolution", 1)
			if !ok || !supportedResolution(rangeSeconds, result.resolution) {
				return statsQuery{}, statsUnsupported("unsupported resolution"), false
			}
		}
	}
	return result, nil, true
}

func containsInt(values []int, wanted int) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
func supportedResolution(window, resolution int) bool {
	capacity, ok := statsRingCapacities[resolution]
	if !ok || window%resolution != 0 {
		return false
	}
	buckets := window / resolution
	return buckets >= 12 && buckets <= capacity
}
func autoResolution(window int) int {
	choices := []int{1, 10, 60, 300}
	explicit := make([]int, 0, len(choices))
	for _, resolution := range choices {
		if supportedResolution(window, resolution) {
			explicit = append(explicit, resolution)
		}
	}
	if len(explicit) == 0 {
		for _, resolution := range choices {
			capacity := statsRingCapacities[resolution]
			if window/resolution <= capacity {
				return resolution
			}
		}
		return 300
	}
	if window <= 900 {
		return explicit[0]
	}
	return explicit[len(explicit)-1]
}

func statsWindow(rangeSeconds, resolution int, now time.Time) (float64, float64) {
	seconds := now.Unix()
	end := seconds + int64(resolution) - seconds%int64(resolution)
	return float64(end - int64(rangeSeconds)), float64(end)
}

func statsUnsupported(reason string) map[string]any {
	return map[string]any{"status": "unsupported", "protocol_version": statsWireVersion, "reason": reason, "valid_ranges": statsRanges, "valid_resolutions": []any{"AUTO", 1, 10, 60, 300}}
}
func statsError(reason string) map[string]any {
	return map[string]any{"status": "unavailable", "protocol_version": statsWireVersion, "reason": reason}
}

func (s *server) statsAgentStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	database, err := stats.OpenReadOnly(r.Context(), s.statsPath)
	if err != nil {
		response := map[string]any{"available": false, "source_generation": 0, "unavailable": map[string]any{"reason": "stats-database-unavailable"}}
		if s.tokenCollector != nil {
			response["token_collection"] = s.tokenCollector.Snapshot()
		}
		jsonResponse(w, http.StatusServiceUnavailable, response)
		return
	}
	defer database.Close()
	snapshot, err := database.QueryAgentStatus(r.Context(), stats.AgentStatusQuery{})
	if err != nil {
		response := map[string]any{"available": false, "source_generation": database.Header.SourceGeneration, "unavailable": map[string]any{"reason": "agent-status-query-failed"}}
		if s.tokenCollector != nil {
			response["token_collection"] = s.tokenCollector.Snapshot()
		}
		jsonResponse(w, http.StatusServiceUnavailable, response)
		return
	}
	if s.tokenCollector != nil {
		diagnostics := s.tokenCollector.Snapshot()
		snapshot.TokenCollection = &diagnostics
	}
	jsonResponse(w, http.StatusOK, snapshot)
}

func (s *server) statsCapabilities(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	ranges := make([]map[string]any, 0, len(statsRanges))
	for _, window := range statsRanges {
		resolutions := []int{}
		buckets := map[int]int{}
		for _, resolution := range []int{1, 10, 60, 300} {
			if supportedResolution(window, resolution) {
				resolutions = append(resolutions, resolution)
				buckets[resolution] = window / resolution
			}
		}
		ranges = append(ranges, map[string]any{"range_seconds": window, "auto_resolution_seconds": autoResolution(window), "explicit_resolution_seconds": resolutions, "buckets": buckets})
	}
	jsonResponse(w, http.StatusOK, map[string]any{"resolution_choices": []int{1, 10, 60, 300}, "max_buckets": 600, "min_buckets": 12, "max_live_cadence_seconds": 60, "ranges": ranges})
}

func (s *server) openStats(r *http.Request) (*stats.Database, error) {
	return stats.OpenReadOnly(r.Context(), s.statsPath)
}

func (s *server) statsSnapshot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	query, failure, ok := parseStatsQuery(r, false)
	if !ok {
		jsonResponse(w, http.StatusBadRequest, failure)
		return
	}
	database, err := s.openStats(r)
	if err != nil {
		jsonResponse(w, http.StatusFailedDependency, statsError("stats database unavailable"))
		return
	}
	defer database.Close()
	start, end := statsWindow(query.rangeSeconds, query.resolution, time.Now())
	snapshot, err := stats.BuildSnapshotWithPricing(r.Context(), database, stats.SnapshotRequest{Start: start, End: end, Resolution: float64(query.resolution), FactLimit: query.limit}, s.pricing)
	if err != nil {
		jsonResponse(w, http.StatusFailedDependency, statsError(err.Error()))
		return
	}
	jsonResponse(w, http.StatusOK, snapshotWire(snapshot, query))
}

func (s *server) statsDelta(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	query, failure, ok := parseStatsQuery(r, true)
	if !ok {
		jsonResponse(w, http.StatusBadRequest, failure)
		return
	}
	database, err := s.openStats(r)
	if err != nil {
		jsonResponse(w, http.StatusFailedDependency, statsError("stats database unavailable"))
		return
	}
	defer database.Close()
	generation := database.Header.SourceGeneration
	if query.afterCacheGeneration >= generation && query.afterRevision >= generation {
		jsonResponse(w, http.StatusNotModified, map[string]any{"protocol_version": statsWireVersion, "not_modified": true, "cache_generation": generation, "revision": generation})
		return
	}
	start, end := statsWindow(query.rangeSeconds, query.resolution, time.Now())
	snapshot, err := stats.BuildSnapshotWithPricing(r.Context(), database, stats.SnapshotRequest{Start: start, End: end, Resolution: float64(query.resolution), FactLimit: query.limit}, s.pricing)
	if err != nil {
		jsonResponse(w, http.StatusFailedDependency, statsError(err.Error()))
		return
	}
	jsonResponse(w, http.StatusOK, deltaWire(snapshot, query))
}

func snapshotWire(snapshot stats.StatsSnapshot, query statsQuery) map[string]any {
	buckets := make([]map[string]any, len(snapshot.Observations.Buckets))
	for i, bucket := range snapshot.Materialized.Buckets {
		series := make(map[string]map[string]any, len(bucket.Series))
		var firstTimestamp, lastTimestamp *float64
		for _, item := range bucket.Series {
			series[item.Name] = map[string]any{"value": item.Value, "source_count": item.SourceCount, "first_timestamp": item.FirstObservedAt, "last_timestamp": item.LastObservedAt}
			if firstTimestamp == nil || item.FirstObservedAt < *firstTimestamp {
				first := item.FirstObservedAt
				firstTimestamp = &first
			}
			if lastTimestamp == nil || item.LastObservedAt > *lastTimestamp {
				last := item.LastObservedAt
				lastTimestamp = &last
			}
		}
		buckets[i] = map[string]any{"start": bucket.Start, "duration": bucket.Duration, "series": series, "source": map[string]any{"count": bucket.SourceCount, "first_timestamp": firstTimestamp, "last_timestamp": lastTimestamp}, "open": i == len(snapshot.Materialized.Buckets)-1}
	}
	noData := make([]map[string]any, 0)
	for _, item := range snapshot.Materialized.NoData {
		noData = append(noData, map[string]any{
			"family": item.Family, "source_id": item.SourceID, "start": item.Start, "end": item.End,
			"epoch": item.EpochID, "reason": item.Reason, "source_cadence_seconds": item.NativeCadenceSeconds,
		})
	}
	sort.Slice(noData, func(i, j int) bool {
		left, right := noData[i], noData[j]
		for _, key := range []string{"family", "source_id"} {
			leftValue, rightValue := left[key].(string), right[key].(string)
			if leftValue != rightValue {
				return leftValue < rightValue
			}
		}
		for _, key := range []string{"start", "end"} {
			leftValue, rightValue := left[key].(float64), right[key].(float64)
			if leftValue != rightValue {
				return leftValue < rightValue
			}
		}
		for _, key := range []string{"epoch", "reason"} {
			leftValue, rightValue := left[key].(string), right[key].(string)
			if leftValue != rightValue {
				return leftValue < rightValue
			}
		}
		return false
	})
	var totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced int64
	for _, bucket := range snapshot.Materialized.Buckets {
		totalTokens += bucket.Cost.Tokens
		totalMicroUSD += bucket.Cost.MicroUSD
		totalAPIListMicroUSD += bucket.Cost.APIListMicroUSD
		priced += int64(bucket.Cost.PricedAtoms)
		unpriced += int64(bucket.Cost.UnpricedAtoms)
	}
	return map[string]any{"protocol_version": statsWireVersion, "range_seconds": query.rangeSeconds, "requested_resolution": func() any {
		if query.resolutionAuto {
			return "AUTO"
		}
		return query.resolution
	}(), "resolution_seconds": query.resolution, "window_start": snapshot.Request.Start, "window_end": snapshot.Request.End, "generated_at": snapshot.Request.End, "source_generation": snapshot.SourceGeneration, "cache_generation": snapshot.SourceGeneration, "rightmost_open": len(buckets) > 0, "buckets": buckets, "no_data": noData, "cost_report": costReport(totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced)}
}

func costReport(totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced int64) map[string]any {
	dimensions := map[string]any{}
	for _, name := range []string{"input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "other"} {
		dimensions[name] = map[string]any{"tokens": int64(0), "micro_usd": int64(0), "api_list_micro_usd": int64(0)}
	}
	// The Go materializer currently exposes aggregate cost counters, not the
	// Python attribution rows. Keep the wire schema exact and place aggregates
	// in the neutral dimension until typed attribution is available.
	dimensions["other"] = map[string]any{"tokens": totalTokens, "micro_usd": totalMicroUSD, "api_list_micro_usd": totalAPIListMicroUSD}
	pricedTokens := int64(0)
	unpricedTokens := totalTokens
	if unpriced == 0 {
		pricedTokens = totalTokens
		unpricedTokens = 0
	}
	return map[string]any{
		"schema_version":           4,
		"total_micro_usd":          totalMicroUSD,
		"total_api_list_micro_usd": totalAPIListMicroUSD,
		"total_tokens":             totalTokens,
		"dimensions":               dimensions,
		"priced":                   map[string]any{"atoms": priced, "tokens": pricedTokens},
		"unpriced":                 map[string]any{"atoms": unpriced, "tokens": unpricedTokens},
		"models":                   []any{}, "agents": []any{}, "evidence": []any{},
		"catalog_revision":    int64(0),
		"omissions":           map[string]any{"models": int64(0), "agents": int64(0), "evidence": int64(0)},
		"reasoning_available": false,
	}
}

func deltaWire(snapshot stats.StatsSnapshot, query statsQuery) map[string]any {
	wire := snapshotWire(snapshot, query)
	return map[string]any{
		"protocol_version":      statsWireVersion,
		"range_seconds":         query.rangeSeconds,
		"resolution_seconds":    query.resolution,
		"source_generation":     snapshot.SourceGeneration,
		"base_cache_generation": query.afterCacheGeneration,
		"cache_generation":      snapshot.SourceGeneration,
		"revision":              snapshot.SourceGeneration,
		"buckets":               wire["buckets"],
		"no_data":               wire["no_data"],
		"tombstones":            []any{},
		"cost_report":           wire["cost_report"],
	}
}

func (s *server) statsObservations(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.ContentLength > 128*1024 {
		jsonResponse(w, http.StatusRequestEntityTooLarge, statsError("request body too large"))
		return
	}
	defer r.Body.Close()
	var raw map[string]json.RawMessage
	decoder := json.NewDecoder(io.LimitReader(r.Body, 128*1024+1))
	decoder.UseNumber()
	if err := decoder.Decode(&raw); err != nil || !jsonEOF(decoder) {
		jsonResponse(w, http.StatusBadRequest, statsError("invalid observations JSON"))
		return
	}
	if !hasExactKeys(raw, "protocol_version", "schema_generation", "client_id", "observations") {
		jsonResponse(w, http.StatusBadRequest, statsError("invalid observations fields"))
		return
	}
	var envelope browserObservationEnvelope
	if err := unmarshalRaw(raw, &envelope); err != nil || envelope.ProtocolVersion != statsWriterProtocol || envelope.SchemaGeneration != statsSchemaGeneration {
		jsonResponse(w, http.StatusBadRequest, statsError("browser stats writer is not current"))
		return
	}
	user, ok := r.Context().Value(authContextKey{}).(authUser)
	if !ok {
		user, ok = s.identity(r)
	}
	if !ok || user.username == "" {
		jsonResponse(w, http.StatusUnauthorized, map[string]any{"error": "authentication_required"})
		return
	}
	secret := s.authSecret()
	if len(secret) == 0 {
		jsonResponse(w, http.StatusFailedDependency, statsError("client binding unavailable"))
		return
	}
	observations, err := parseBrowserObservationEnvelope(raw, user.username, secret)
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, statsError(err.Error()))
		return
	}
	if s.statsWriter == nil {
		jsonResponse(w, http.StatusFailedDependency, statsError("stats writer unavailable"))
		return
	}
	result, err := s.statsWriter.AppendBatch(r.Context(), stats.AppendBatch{Observations: observations})
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"status": "unsupported", "reason": err.Error()})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "source_generation": result.Generation, "accepted": result.ObservationsAccepted, "duplicates": result.ObservationsDuplicate})
}

type browserObservationEnvelope struct {
	ProtocolVersion  int               `json:"protocol_version"`
	SchemaGeneration int               `json:"schema_generation"`
	ClientID         string            `json:"client_id"`
	Observations     []json.RawMessage `json:"observations"`
}

func jsonEOF(decoder *json.Decoder) bool {
	var extra any
	return decoder.Decode(&extra) == io.EOF
}

func hasExactKeys(value map[string]json.RawMessage, keys ...string) bool {
	if len(value) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, ok := value[key]; !ok {
			return false
		}
	}
	return true
}

func unmarshalRaw(value map[string]json.RawMessage, target any) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, target)
}

func parseBrowserObservationEnvelope(raw map[string]json.RawMessage, username string, secret []byte) ([]stats.Observation, error) {
	var envelope browserObservationEnvelope
	if err := unmarshalRaw(raw, &envelope); err != nil {
		return nil, err
	}
	if len([]byte(strings.TrimSpace(envelope.ClientID))) == 0 || len([]byte(envelope.ClientID)) > 128 {
		return nil, fmt.Errorf("client_id must be bounded text")
	}
	if len(envelope.Observations) < 1 || len(envelope.Observations) > 1000 {
		return nil, fmt.Errorf("observations must contain 1..1000 items")
	}
	bound := func(value string) string {
		h := hmac.New(sha256.New, secret)
		_, _ = h.Write([]byte(statsClientIDDomain + username + "\x00" + strings.TrimSpace(value)))
		return "stats-" + hex.EncodeToString(h.Sum(nil))[:32]
	}
	result := make([]stats.Observation, 0, len(envelope.Observations))
	for index, rawItem := range envelope.Observations {
		var item map[string]json.RawMessage
		if err := json.Unmarshal(rawItem, &item); err != nil || !hasExactKeys(item, "event_id", "family", "source_id", "observed_at", "epoch_id", "payload") {
			return nil, fmt.Errorf("observations[%d] has invalid fields", index)
		}
		var eventID, family, sourceID, epochID string
		if err := decodeText(item["event_id"], &eventID, 512); err != nil {
			return nil, fmt.Errorf("observations[%d] has invalid identity", index)
		}
		if err := decodeText(item["family"], &family, 256); err != nil {
			return nil, fmt.Errorf("observations[%d] has invalid identity", index)
		}
		if err := decodeText(item["source_id"], &sourceID, 128); err != nil {
			return nil, fmt.Errorf("observations[%d] has invalid identity", index)
		}
		if err := decodeText(item["epoch_id"], &epochID, 512); err != nil {
			return nil, fmt.Errorf("observations[%d] has invalid identity", index)
		}
		if family != "browser" || sourceID != strings.TrimSpace(envelope.ClientID) {
			return nil, fmt.Errorf("browser observation identity is invalid")
		}
		var observedAt float64
		if err := json.Unmarshal(item["observed_at"], &observedAt); err != nil || math.IsNaN(observedAt) || math.IsInf(observedAt, 0) || observedAt < 0 {
			return nil, fmt.Errorf("observations[%d].observed_at must be finite and non-negative", index)
		}
		var payload map[string]any
		if err := json.Unmarshal(item["payload"], &payload); err != nil || payload == nil {
			return nil, fmt.Errorf("observations[%d].payload must be an object", index)
		}
		if err := validateBrowserPayload(payload); err != nil {
			return nil, fmt.Errorf("observations[%d].payload: %w", index, err)
		}
		result = append(result, stats.Observation{EventID: bound(strings.TrimSpace(envelope.ClientID) + ":event:" + eventID), Family: family, SourceID: bound(envelope.ClientID), ObservedAt: observedAt, EpochID: bound(strings.TrimSpace(envelope.ClientID) + ":epoch:" + epochID), Payload: payload})
	}
	return result, nil
}

func decodeText(raw json.RawMessage, target *string, maximum int) error {
	if json.Unmarshal(raw, target) != nil || strings.TrimSpace(*target) == "" || len([]byte(strings.TrimSpace(*target))) > maximum {
		return fmt.Errorf("invalid text")
	}
	for _, r := range strings.TrimSpace(*target) {
		if r < 0x20 || r == 0x7f {
			return fmt.Errorf("control character")
		}
	}
	*target = strings.TrimSpace(*target)
	return nil
}

func validateBrowserPayload(payload map[string]any) error {
	kind, ok := payload["kind"].(string)
	if !ok || kind == "" {
		return fmt.Errorf("kind is required")
	}
	if !browserEventKinds[kind] {
		return fmt.Errorf("unsupported kind")
	}
	for key := range payload {
		if key == "kind" {
			continue
		}
		if browserCommonFields[key] {
			if err := validateBrowserCommonField(key, payload[key]); err != nil {
				return err
			}
			continue
		}
		if !browserObservationFields[kind][key] {
			return fmt.Errorf("field %s is not supported for %s", key, kind)
		}
		if browserNumericFields[key] {
			if !browserNonNegativeNumber(payload[key]) {
				return fmt.Errorf("field %s must be a non-negative finite number", key)
			}
		} else if browserStringFields[key] {
			if value, ok := payload[key].(string); !ok || strings.TrimSpace(value) == "" {
				return fmt.Errorf("field %s must be non-empty text", key)
			}
		} else {
			return fmt.Errorf("field %s has no validation contract", key)
		}
	}
	if kind == "warning" || kind == "error" || kind == "unhandledrejection" {
		for _, required := range []string{"signature", "message", "source"} {
			if _, ok := payload[required]; !ok {
				return fmt.Errorf("%s is required", required)
			}
		}
		if !browserToken(payload["signature"], 32) {
			return fmt.Errorf("signature must be bounded ASCII token text")
		}
		if !browserText(payload["message"], 500) {
			return fmt.Errorf("message must be bounded text")
		}
		if stack, ok := payload["stack"]; ok && !browserText(stack, 4000) {
			return fmt.Errorf("stack must be bounded text")
		}
		if !browserEndpoint(payload["source"]) {
			return fmt.Errorf("source must be a bounded absolute path")
		}
		if line, ok := payload["line"]; ok && !browserInteger(line, 0, 10_000_000) {
			return fmt.Errorf("line must be an integer from 0 through 10000000")
		}
		if column, ok := payload["column"]; ok && !browserInteger(column, 0, 10_000_000) {
			return fmt.Errorf("column must be an integer from 0 through 10000000")
		}
		if requestID, ok := payload["request_id"]; ok && !browserRequestID(requestID) {
			return fmt.Errorf("request_id must be a bounded request id")
		}
		if route, ok := payload["route"]; ok && !browserEndpoint(route) {
			return fmt.Errorf("route must be a bounded absolute path")
		}
		if eventType, ok := payload["event_type"]; ok && !browserToken(eventType, 64) {
			return fmt.Errorf("event_type must be bounded ASCII token text")
		}
		if wallTime, ok := payload["wall_time"]; ok {
			value, valid := wallTime.(string)
			if !valid || !browserPacificWallTime.MatchString(value) {
				return fmt.Errorf("wall_time must be an exact Pacific wall time")
			}
		}
		if delivery, ok := payload["delivery_outcome"]; ok {
			value, valid := delivery.(string)
			if !valid || !browserFailureDeliveryOutcomes[value] {
				return fmt.Errorf("delivery_outcome must be a bounded failure outcome")
			}
		}
		if status, ok := payload["status"]; ok && !browserInteger(status, 100, 599) {
			return fmt.Errorf("status must be an integer from 100 through 599")
		}
		if provenance, ok := payload["provenance"]; ok {
			value, valid := provenance.(string)
			if !valid || !browserFailureProvenance[value] {
				return fmt.Errorf("provenance must be controlled_probe or confirmed_real")
			}
		}
		return nil
	}
	if kind == "disconnect" {
		if _, ok := payload["duration_ms"]; !ok || !browserHasOnlyFields(payload, "duration_ms") {
			return fmt.Errorf("disconnect requires only duration_ms")
		}
	}
	if kind == "page_load" {
		if _, ok := payload["endpoint"]; !ok || !browserEndpoint(payload["endpoint"]) {
			return fmt.Errorf("endpoint must be a bounded absolute path")
		}
		if value, ok := payload["fanout_count"]; ok && !browserInteger(value, 0, 10_000) {
			return fmt.Errorf("fanout_count must be an integer from 0 through 10000")
		}
		if value, ok := payload["max_concurrency"]; ok && !browserInteger(value, 0, 1_000) {
			return fmt.Errorf("max_concurrency must be an integer from 0 through 1000")
		}
	}
	if kind == "api" {
		if endpoint, ok := payload["endpoint"]; ok && !browserEndpoint(endpoint) {
			return fmt.Errorf("endpoint must be a bounded absolute path")
		}
		if method, ok := payload["method"]; ok && !browserMethod(method) {
			return fmt.Errorf("method must be a bounded uppercase HTTP method")
		}
		if requestID, ok := payload["request_id"]; ok && !browserRequestID(requestID) {
			return fmt.Errorf("request_id must be a bounded request id")
		}
		if protocol, ok := payload["connection_protocol"]; ok && !browserToken(protocol, 24) {
			return fmt.Errorf("connection_protocol must be bounded ASCII token text")
		}
		if status, ok := payload["status"]; ok && !browserInteger(status, 100, 599) {
			return fmt.Errorf("status must be an integer from 100 through 599")
		}
	}
	if kind == "finder_usable" {
		if value, ok := payload["entry_count"]; ok && !browserInteger(value, 0, 1_000_000) {
			return fmt.Errorf("entry_count must be an integer from 0 through 1000000")
		}
	}
	if kind == "interaction" {
		if value, ok := payload["interaction_type"]; ok && !browserToken(value, 32) {
			return fmt.Errorf("interaction_type must be bounded ASCII token text")
		}
	}
	if kind == "operation_wait" {
		if value, ok := payload["operation_kind"]; ok && !browserToken(value, 64) {
			return fmt.Errorf("operation_kind must be bounded ASCII token text")
		}
		outcome, ok := payload["outcome"]
		if !ok || (outcome != "ready" && outcome != "failed") {
			return fmt.Errorf("outcome must be ready or failed")
		}
		if requestID, ok := payload["request_id"]; ok && !browserRequestID(requestID) {
			return fmt.Errorf("request_id must be a bounded request id")
		}
	}
	return nil
}

var browserEventKinds = map[string]bool{
	"api": true, "sse": true, "heartbeat": true, "disconnect": true, "page_load": true,
	"finder_usable": true, "interaction": true, "operation_wait": true, "long_task": true,
	"warning": true, "error": true, "unhandledrejection": true,
}

var browserCommonFields = map[string]bool{"journey_id": true, "code_revision": true, "browser_family": true}

var browserObservationFields = map[string]map[string]bool{
	"api":                {"latency_ms": true, "bytes": true, "endpoint": true, "method": true, "request_id": true, "status": true, "connection_protocol": true, "queue_ms": true, "connect_ms": true, "tls_ms": true, "ttfb_ms": true, "download_ms": true, "apply_render_ms": true},
	"sse":                {"latency_ms": true, "bytes": true},
	"heartbeat":          {"latency_ms": true, "bytes": true, "upload_queue_depth": true, "upload_drops": true, "upload_retries": true, "instrumentation_cost_ms": true},
	"disconnect":         {"duration_ms": true},
	"page_load":          {"endpoint": true, "fanout_count": true, "max_concurrency": true, "navigation_ms": true, "bundle_parse_eval_ms": true, "first_paint_ms": true, "first_contentful_paint_ms": true, "first_api_ms": true, "fanout_ms": true, "interactive_ms": true, "app_ready_ms": true},
	"finder_usable":      {"latency_ms": true, "entry_count": true},
	"interaction":        {"latency_ms": true, "input_delay_ms": true, "processing_ms": true, "presentation_delay_ms": true, "interaction_type": true},
	"operation_wait":     {"latency_ms": true, "operation_kind": true, "outcome": true, "request_id": true},
	"long_task":          {"latency_ms": true},
	"warning":            {"signature": true, "message": true, "stack": true, "source": true, "line": true, "column": true, "provenance": true, "request_id": true, "route": true, "event_type": true, "wall_time": true, "delivery_outcome": true, "status": true},
	"error":              {"signature": true, "message": true, "stack": true, "source": true, "line": true, "column": true, "provenance": true, "request_id": true, "route": true, "event_type": true, "wall_time": true, "delivery_outcome": true, "status": true},
	"unhandledrejection": {"signature": true, "message": true, "stack": true, "source": true, "line": true, "column": true, "provenance": true, "request_id": true, "route": true, "event_type": true, "wall_time": true, "delivery_outcome": true, "status": true},
}

var browserNumericFields = map[string]bool{
	"latency_ms": true, "bytes": true, "duration_ms": true, "status": true, "queue_ms": true, "connect_ms": true, "tls_ms": true, "ttfb_ms": true, "download_ms": true, "apply_render_ms": true,
	"fanout_count": true, "max_concurrency": true, "navigation_ms": true, "bundle_parse_eval_ms": true, "first_paint_ms": true, "first_contentful_paint_ms": true, "first_api_ms": true, "fanout_ms": true, "interactive_ms": true, "app_ready_ms": true,
	"entry_count": true, "input_delay_ms": true, "processing_ms": true, "presentation_delay_ms": true, "line": true, "column": true,
	"upload_queue_depth": true, "upload_drops": true, "upload_retries": true, "instrumentation_cost_ms": true,
}

var browserStringFields = map[string]bool{
	"endpoint": true, "method": true, "request_id": true, "connection_protocol": true, "interaction_type": true, "operation_kind": true, "outcome": true,
	"signature": true, "message": true, "stack": true, "source": true, "provenance": true, "route": true, "event_type": true, "wall_time": true, "delivery_outcome": true,
}

var browserFailureDeliveryOutcomes = map[string]bool{
	"failed": true, "stalled": true, "timeout": true, "rejected": true, "dropped": true, "retrying": true,
}

var browserFailureProvenance = map[string]bool{"controlled_probe": true, "confirmed_real": true}

var browserPacificWallTime = regexp.MustCompile(`^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} P(?:S|D)T$`)

func validateBrowserCommonField(key string, value any) error {
	switch key {
	case "journey_id":
		if !browserToken(value, 96) {
			return fmt.Errorf("journey_id must be bounded ASCII token text")
		}
	case "code_revision":
		if !browserToken(value, 80) {
			return fmt.Errorf("code_revision must be bounded ASCII token text")
		}
	case "browser_family":
		family, ok := value.(string)
		if !ok || (family != "chromium" && family != "firefox" && family != "safari" && family != "other") {
			return fmt.Errorf("browser_family is invalid")
		}
	}
	return nil
}

func browserHasOnlyFields(payload map[string]any, fields ...string) bool {
	wanted := make(map[string]bool, len(fields))
	for _, field := range fields {
		wanted[field] = true
	}
	for field := range payload {
		if field == "kind" || browserCommonFields[field] {
			continue
		}
		if !wanted[field] {
			return false
		}
	}
	return true
}

func browserNumber(value any) (float64, bool) {
	switch value := value.(type) {
	case json.Number:
		number, err := value.Float64()
		return number, err == nil
	case string:
		return 0, false
	case float64:
		return value, true
	case float32:
		return float64(value), true
	case int:
		return float64(value), true
	case int8:
		return float64(value), true
	case int16:
		return float64(value), true
	case int32:
		return float64(value), true
	case int64:
		return float64(value), true
	case uint:
		return float64(value), true
	case uint8:
		return float64(value), true
	case uint16:
		return float64(value), true
	case uint32:
		return float64(value), true
	case uint64:
		return float64(value), true
	default:
		return 0, false
	}
}

func browserNonNegativeNumber(value any) bool {
	number, ok := browserNumber(value)
	return ok && !math.IsNaN(number) && !math.IsInf(number, 0) && number >= 0
}

func browserInteger(value any, minimum, maximum int) bool {
	number, ok := browserNumber(value)
	return ok && !math.IsNaN(number) && !math.IsInf(number, 0) && math.Trunc(number) == number && number >= float64(minimum) && number <= float64(maximum)
}

func browserToken(value any, maximum int) bool {
	text, ok := value.(string)
	if !ok || len(text) == 0 || len([]byte(text)) > maximum {
		return false
	}
	for _, character := range text {
		if !(character < 128 && (character >= 'a' && character <= 'z' || character >= 'A' && character <= 'Z' || character >= '0' && character <= '9' || strings.ContainsRune("._-/", character))) {
			return false
		}
	}
	return true
}

func browserText(value any, maximum int) bool {
	text, ok := value.(string)
	if !ok || len(text) == 0 || len([]byte(text)) > maximum {
		return false
	}
	for _, character := range text {
		if (character < 0x20 && character != '\n' && character != '\t') || character == 0x7f {
			return false
		}
	}
	return true
}

func browserEndpoint(value any) bool {
	text, ok := value.(string)
	if !ok || !strings.HasPrefix(text, "/") || len([]byte(text)) > 240 || strings.ContainsAny(text, "?#") {
		return false
	}
	for _, character := range text {
		if character < 0x20 || character == 0x7f {
			return false
		}
	}
	return true
}

func browserRequestID(value any) bool {
	text, ok := value.(string)
	if !ok || !strings.HasPrefix(text, "r-") || len([]byte(text)) > 128 {
		return false
	}
	for _, character := range text {
		if character < 0x21 || character == 0x7f {
			return false
		}
	}
	return true
}

func browserMethod(value any) bool {
	method, ok := value.(string)
	if !ok || len(method) < 1 || len(method) > 16 || method != strings.ToUpper(method) {
		return false
	}
	for _, character := range method {
		if !(character >= 'A' && character <= 'Z' || character == '-') {
			return false
		}
	}
	return true
}

func (s *server) statsStream(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	query, failure, ok := parseStatsQuery(r, false)
	if !ok {
		writeSSE(w, "unsupported", failure)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		return
	}
	flusher.Flush()
	database, err := s.openStats(r)
	if err != nil {
		writeSSE(w, "unavailable", statsError("stats database unavailable"))
		return
	}
	defer database.Close()
	start, end := statsWindow(query.rangeSeconds, query.resolution, time.Now())
	snapshot, err := stats.BuildSnapshot(r.Context(), database, stats.SnapshotRequest{Start: start, End: end, Resolution: float64(query.resolution), FactLimit: query.limit})
	if err != nil {
		writeSSE(w, "unavailable", statsError(err.Error()))
		return
	}
	requestedResolution := any(query.resolution)
	if query.resolutionAuto {
		requestedResolution = "AUTO"
	}
	writeSSE(w, "ack", map[string]any{"cache_generation": snapshot.SourceGeneration, "chunk_count": 1, "not_modified": false, "range_seconds": query.rangeSeconds, "requested_resolution": requestedResolution, "resolution_seconds": query.resolution})
	writeSSE(w, "snapshot", snapshotWire(snapshot, query))
	writeSSE(w, "ready", map[string]any{"cache_generation": snapshot.SourceGeneration, "revision": 0})
	flusher.Flush()
	cacheGeneration := snapshot.SourceGeneration
	revision := snapshot.SourceGeneration
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
		}
		deltaRequest := r.Clone(r.Context())
		values := deltaRequest.URL.Query()
		values.Del("resolution")
		values.Set("resolution_seconds", strconv.Itoa(query.resolution))
		values.Set("after_cache_generation", strconv.FormatInt(cacheGeneration, 10))
		values.Set("after_revision", strconv.FormatInt(revision, 10))
		deltaRequest.URL.RawQuery = values.Encode()
		var deltaResponse bytes.Buffer
		deltaRecorder := &bufferResponseWriter{header: make(http.Header), body: &deltaResponse}
		s.statsDelta(deltaRecorder, deltaRequest)
		if deltaRecorder.status == http.StatusNotModified {
			writeSSE(w, "heartbeat", map[string]any{"cache_generation": cacheGeneration, "revision": revision})
			continue
		}
		if deltaRecorder.status != http.StatusOK {
			writeSSE(w, "unavailable", statsError("stats delta unavailable"))
			return
		}
		var payload map[string]any
		if err := json.Unmarshal(deltaResponse.Bytes(), &payload); err != nil {
			writeSSE(w, "unavailable", statsError("stats delta malformed"))
			return
		}
		writeSSE(w, "delta", payload)
		cacheGeneration = int64(payload["cache_generation"].(float64))
		revision = int64(payload["revision"].(float64))
	}
}

type bufferResponseWriter struct {
	header http.Header
	body   *bytes.Buffer
	status int
}

func (w *bufferResponseWriter) Header() http.Header    { return w.header }
func (w *bufferResponseWriter) WriteHeader(status int) { w.status = status }
func (w *bufferResponseWriter) Write(data []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	return w.body.Write(data)
}

func writeSSE(w http.ResponseWriter, event string, payload any) {
	data, _ := json.Marshal(payload)
	fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, data)
	if flusher, ok := w.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (s *server) pricingCatalog(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Method == http.MethodPost {
		if s.pricing == nil {
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "pricing catalog unavailable"})
			return
		}
		if err := s.pricing.Refresh(); err != nil {
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": err.Error()})
			return
		}
	}
	if s.pricing == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "pricing catalog unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"catalog": s.pricing.PublicPayload(), "refresh": map[string]any{"status": "idle"}})
}

func (s *server) ready(w http.ResponseWriter, r *http.Request) {
	database, err := s.openStats(r)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ready": false, "failures": []string{"stats database unavailable"}})
		return
	}
	defer database.Close()
	jsonResponse(w, http.StatusOK, map[string]any{"ready": true, "source_generation": database.Header.SourceGeneration, "cache_generation": database.Header.SourceGeneration, "pending_cells": 0, "migration_state": "ready", "materializer_state": "ready", "ring_failure": ""})
}
