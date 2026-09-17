package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/keivenchang/yolomux/internal/stats"
)

const statsWireVersion = 2

const (
	statsWriterProtocol   = 25
	statsSchemaGeneration = 9
	statsClientIDDomain   = "yolomux-stats-client-v1\x00"
	// Keep snapshot chunks below the Python local-RPC target. The public
	// protocol permits a normal snapshot to be returned as its first chunk;
	// callers request the remaining indexes with chunk_generation.
	statsSnapshotChunkTargetBytes = 1 << 20
	statsMaxSnapshotChunks        = 64
	statsMaxSafeJSONInteger       = int64(9007199254740991)
)

var statsRanges = []int{300, 900, 1800, 3600, 7200, 14400, 28800, 57600, 86400}

var errSnapshotChunkGenerationAdvanced = errors.New("snapshot generation advanced while chunks were loading")

var statsRingCapacities = map[int]int{1: 300, 10: 180, 60: 480, 300: 288}

var statsRetiredQueryFields = map[string]bool{
	"history": true, "history_start": true, "history_end": true,
	"history_resolution": true, "history_max_points": true, "max_points": true,
	"token_resolution": true, "token_start": true, "token_end": true,
	"cursor": true, "page": true,
}

type statsQuery struct {
	rangeSeconds, resolution            int
	resolutionAuto                      bool
	clientID                            string
	afterCacheGeneration, afterRevision int64
	sinceGeneration                     int64
	chunkIndex                          int
	chunkGeneration                     int64
	hasChunkIndex                       bool
}

type statsDeltaKey struct {
	rangeSeconds, resolution int
}

const statsDeltaHistoryLimit = 8

func parseStatsQuery(r *http.Request, delta bool) (statsQuery, map[string]any, bool) {
	unsupported := func(reason string, rangeSeconds ...int) map[string]any {
		return statsUnsupportedForQuery(delta, reason, rangeSeconds...)
	}
	if len([]byte(r.URL.RawQuery)) > 2048 {
		return statsQuery{}, unsupported("query is too large"), false
	}
	values, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return statsQuery{}, unsupported("query contains a malformed escape"), false
	}
	allowed := map[string]bool{"range_seconds": true, "client_id": true}
	if delta {
		allowed["resolution_seconds"], allowed["after_cache_generation"], allowed["after_revision"] = true, true, true
	} else {
		allowed["resolution"], allowed["since_generation"] = true, true
		allowed["chunk_index"], allowed["chunk_generation"] = true, true
	}
	duplicates := make([]string, 0)
	for key, items := range values {
		if len(items) > 1 {
			duplicates = append(duplicates, key)
		}
	}
	if len(duplicates) > 0 {
		sort.Strings(duplicates)
		reason := "duplicate query parameters: " + pythonStringList(duplicates)
		// Python's query parser reports duplicates before it knows whether the
		// request is a snapshot or delta. Preserve that generic envelope for
		// delta duplicates too (including its AUTO resolution choice).
		if delta {
			return statsQuery{}, statsUnsupported(reason), false
		}
		return statsQuery{}, unsupported(reason), false
	}
	unknown := make([]string, 0)
	retired := false
	for key := range values {
		if allowed[key] {
			continue
		}
		unknown = append(unknown, key)
		retired = retired || statsRetiredQueryFields[key]
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		kind := "unknown"
		if retired {
			kind = "retired"
		}
		suffix := " query parameters"
		if delta {
			kind += " delta"
			suffix = " parameters"
		}
		return statsQuery{}, unsupported(kind + suffix + ": " + pythonStringList(unknown)), false
	}
	required := []string{"range_seconds", "resolution", "client_id"}
	if delta {
		required = []string{"range_seconds", "resolution_seconds", "client_id", "after_cache_generation", "after_revision"}
	}
	missing := make([]string, 0)
	for _, key := range required {
		if _, present := values[key]; !present {
			missing = append(missing, key)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		name := "missing query parameters: "
		if delta {
			name = "missing delta parameters: "
		}
		return statsQuery{}, unsupported(name + pythonStringList(missing)), false
	}
	parse := func(name string, minimum int) (int, bool, string) {
		value := values.Get(name)
		if value == "" || strings.IndexFunc(value, func(r rune) bool { return r < '0' || r > '9' }) >= 0 {
			return 0, false, name + " must be a canonical decimal integer"
		}
		n, parseErr := strconv.Atoi(value)
		if parseErr != nil {
			return 0, false, name + " must be a canonical decimal integer"
		}
		if n < minimum || strconv.Itoa(n) != value {
			return 0, false, fmt.Sprintf("%s must be a canonical decimal integer >= %d", name, minimum)
		}
		return n, true, ""
	}
	rangeSeconds, ok, parseReason := parse("range_seconds", 1)
	if !ok {
		return statsQuery{}, unsupported(parseReason), false
	}
	if !containsInt(statsRanges, rangeSeconds) {
		return statsQuery{}, unsupported(fmt.Sprintf("unsupported range_seconds %d", rangeSeconds), rangeSeconds), false
	}
	result := statsQuery{rangeSeconds: rangeSeconds, clientID: values.Get("client_id")}
	if result.clientID == "" || strings.TrimSpace(result.clientID) == "" {
		return statsQuery{}, unsupported("client_id must be a non-empty string", rangeSeconds), false
	}
	if strings.IndexFunc(result.clientID, func(r rune) bool { return r < 0x20 || r == 0x7f }) >= 0 {
		return statsQuery{}, unsupported("client_id contains control characters", rangeSeconds), false
	}
	if len([]byte(result.clientID)) > 128 {
		return statsQuery{}, unsupported("client_id exceeds 128 bytes", rangeSeconds), false
	}
	if delta {
		var resolutionReason string
		result.resolution, ok, resolutionReason = parse("resolution_seconds", 1)
		if !ok {
			return statsQuery{}, unsupported(resolutionReason, rangeSeconds), false
		}
		if !supportedResolution(rangeSeconds, result.resolution) {
			return statsQuery{}, unsupported("delta has an unsupported Range/Resolution key", rangeSeconds), false
		}
		parseGeneration := func(name string) (int64, bool, string) {
			value := values.Get(name)
			if value == "" || strings.IndexFunc(value, func(r rune) bool { return r < '0' || r > '9' }) >= 0 {
				return 0, false, name + " must be a canonical decimal integer"
			}
			generation, valid := parseGenerationValue(value)
			if !valid {
				if parsed, parseErr := strconv.ParseInt(value, 10, 64); parseErr == nil && parsed > statsMaxSafeJSONInteger {
					return 0, false, name + " exceeds the exact JSON integer range"
				}
				return 0, false, name + " must be a canonical decimal integer"
			}
			return generation, true, ""
		}
		var generationReason string
		result.afterCacheGeneration, ok, generationReason = parseGeneration("after_cache_generation")
		if !ok {
			return statsQuery{}, unsupported(generationReason, rangeSeconds), false
		}
		result.afterRevision, ok, generationReason = parseGeneration("after_revision")
		if !ok {
			return statsQuery{}, unsupported(generationReason, rangeSeconds), false
		}
	} else {
		if value, present := values["since_generation"]; present {
			generation, valid := parseGenerationValue(value[0])
			if !valid {
				if parsed, parseErr := strconv.ParseInt(value[0], 10, 64); parseErr == nil && parsed > statsMaxSafeJSONInteger {
					return statsQuery{}, unsupported("since_generation exceeds the exact JSON integer range", rangeSeconds), false
				}
				return statsQuery{}, unsupported("since_generation must be a canonical decimal integer", rangeSeconds), false
			}
			result.sinceGeneration = generation
		}
		resolution := values.Get("resolution")
		if resolution == "AUTO" {
			result.resolutionAuto = true
			result.resolution = autoResolution(rangeSeconds)
		} else {
			var resolutionReason string
			result.resolution, ok, resolutionReason = parse("resolution", 1)
			if !ok {
				return statsQuery{}, unsupported(resolutionReason, rangeSeconds), false
			}
			if !supportedResolution(rangeSeconds, result.resolution) {
				return statsQuery{}, unsupported(fmt.Sprintf("unsupported resolution %d for range %ds; offered %s", result.resolution, rangeSeconds, formatIntTuple(supportedResolutionValues(rangeSeconds))), rangeSeconds), false
			}
		}
		_, hasChunkIndex := values["chunk_index"]
		_, hasChunkGeneration := values["chunk_generation"]
		if hasChunkIndex != hasChunkGeneration {
			return statsQuery{}, unsupported("chunk_index and chunk_generation must be supplied together", rangeSeconds), false
		}
		if hasChunkIndex {
			chunkIndex, chunkOK, chunkReason := parse("chunk_index", 0)
			if !chunkOK || chunkIndex >= statsMaxSnapshotChunks {
				if chunkOK {
					chunkReason = "chunk_index exceeds the snapshot chunk limit"
				}
				return statsQuery{}, unsupported(chunkReason, rangeSeconds), false
			}
			chunkGeneration, generationOK := parseGenerationValue(values["chunk_generation"][0])
			if !generationOK || chunkGeneration < 1 {
				return statsQuery{}, unsupported("chunk_generation must be positive", rangeSeconds), false
			}
			result.chunkIndex, result.chunkGeneration, result.hasChunkIndex = chunkIndex, chunkGeneration, true
		}
	}
	return result, nil, true
}

func pythonStringList(values []string) string {
	quoted := make([]string, len(values))
	for index, value := range values {
		quoted[index] = "'" + strings.ReplaceAll(value, "'", "\\'") + "'"
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}

func supportedResolutionValues(rangeSeconds int) []any {
	values := make([]any, 0, 4)
	for _, resolution := range []int{1, 10, 60, 300} {
		if supportedResolution(rangeSeconds, resolution) {
			values = append(values, resolution)
		}
	}
	return values
}

func formatIntTuple(values []any) string {
	parts := make([]string, len(values))
	for index, value := range values {
		parts[index] = fmt.Sprint(value)
	}
	if len(parts) == 1 {
		return "(" + parts[0] + ",)"
	}
	return "(" + strings.Join(parts, ", ") + ")"
}

func parseGenerationValue(value string) (int64, bool) {
	generation, err := strconv.ParseInt(value, 10, 64)
	return generation, err == nil && generation >= 0 && generation <= statsMaxSafeJSONInteger && strconv.FormatInt(generation, 10) == value
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
	end := stats.NextWindowEnd(now, resolution)
	return float64(end - int64(rangeSeconds)), float64(end)
}

func statsUnsupported(reason string, rangeSeconds ...int) map[string]any {
	return statsUnsupportedForQuery(false, reason, rangeSeconds...)
}

func statsUnsupportedForQuery(delta bool, reason string, rangeSeconds ...int) map[string]any {
	var selectedRange any
	validResolutions := []any{"AUTO", 1, 10, 60, 300}
	if len(rangeSeconds) > 0 {
		selectedRange = rangeSeconds[0]
		if containsInt(statsRanges, rangeSeconds[0]) {
			validResolutions = []any{}
			if !delta {
				validResolutions = append(validResolutions, "AUTO")
			}
			for _, resolution := range []int{1, 10, 60, 300} {
				if supportedResolution(rangeSeconds[0], resolution) {
					validResolutions = append(validResolutions, resolution)
				}
			}
		} else if delta {
			validResolutions = []any{1, 10, 60, 300}
		}
	}
	if delta && len(rangeSeconds) == 0 {
		validResolutions = []any{1, 10, 60, 300}
	}
	return map[string]any{"status": "unsupported", "protocol_version": statsWireVersion, "reason": reason, "range_seconds": selectedRange, "valid_ranges": statsRanges, "valid_resolutions": validResolutions}
}
func statsError(reason string) map[string]any {
	return map[string]any{"status": "unavailable", "protocol_version": statsWireVersion, "reason": reason}
}

func (s *server) statsAgentStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if s.statsService == nil {
		response := map[string]any{"available": false, "source_generation": 0, "unavailable": map[string]any{"reason": "agent-status-owner-unavailable"}}
		if s.tokenCollector != nil {
			response["token_collection"] = s.tokenCollector.Snapshot()
		}
		jsonResponse(w, http.StatusServiceUnavailable, response)
		return
	}
	snapshot, err := s.statsService.AgentStatus(r.Context())
	if err != nil {
		response := map[string]any{"available": false, "source_generation": snapshot.SourceGeneration, "unavailable": map[string]any{"reason": "agent-status-query-failed"}}
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
	payload := map[string]any{"resolution_choices": []int{1, 10, 60, 300}, "max_buckets": 600, "min_buckets": 12, "max_live_cadence_seconds": 60, "ranges": ranges}
	if s.statsMigration != nil {
		payload["migration"] = cloneMap(s.statsMigration)
	}
	jsonResponse(w, http.StatusOK, statsReadyEnvelope(statsRequestID(r), payload))
}

func statsRequestID(r *http.Request) string {
	if r != nil {
		candidate := strings.TrimSpace(r.Header.Get("X-YOLOmux-Request-ID"))
		if len(candidate) >= 3 && len(candidate) <= 123 && strings.HasPrefix(candidate, "r-") &&
			!strings.ContainsAny(candidate, " \t\r\n") {
			return candidate
		}
	}
	return fmt.Sprintf("r-%d", time.Now().UnixNano())
}

func statsReadyEnvelope(requestID string, payload map[string]any) map[string]any {
	// Keep the transport envelope compatible with Python's
	// ApiResponseWriter, which preserves established payload aliases at the
	// top level for JSON products. The browser unwraps data, while raw parity
	// consumers compare these aliases too.
	envelope := map[string]any{
		"state":    "ready",
		"request":  map[string]any{"id": requestID},
		"data":     payload,
		"ok":       true,
		"terminal": true,
	}
	for key, value := range payload {
		if _, exists := envelope[key]; !exists {
			envelope[key] = value
		}
	}
	return envelope
}

// statsHTTPErrorEnvelope mirrors the Python ApiResponseWriter's canonical
// failure framing for the current stats routes.  The legacy stats payload is
// retained in details and flattened at the top level for older browser
// consumers; only request/error/state are reserved envelope keys.
func statsHTTPErrorEnvelope(r *http.Request, status int, legacy map[string]any) map[string]any {
	if legacy == nil {
		legacy = map[string]any{}
	}
	reason := "request failed"
	if value, ok := legacy["error"].(string); ok && value != "" {
		reason = value
	} else if value, ok := legacy["reason"].(string); ok && value != "" {
		reason = value
	} else if value, ok := legacy["message"].(string); ok && value != "" {
		reason = value
	}
	code := "request_failed"
	if value, ok := legacy["code"].(string); ok && value != "" {
		code = value
	} else {
		switch status {
		case http.StatusBadRequest:
			code = "invalid_request"
		case http.StatusUnauthorized:
			code = "authentication_required"
		case http.StatusForbidden:
			code = "forbidden"
		case http.StatusNotFound:
			code = "not_found"
		case http.StatusConflict:
			code = "conflict"
		case http.StatusRequestEntityTooLarge:
			code = "request_too_large"
		case http.StatusTooManyRequests:
			code = "rate_limited"
		case http.StatusFailedDependency:
			code = "dependency_failed"
		case http.StatusServiceUnavailable:
			code = "service_unavailable"
		case http.StatusGatewayTimeout:
			code = "deadline_expired"
		case http.StatusUpgradeRequired:
			code = "upgrade_required"
		}
	}
	retryable := false
	if value, ok := legacy["retryable"].(bool); ok {
		retryable = value
	} else {
		retryable = status == http.StatusRequestTimeout || status == http.StatusTooManyRequests || status == http.StatusBadGateway || status == http.StatusServiceUnavailable || status == http.StatusGatewayTimeout
	}
	details := make(map[string]any, len(legacy))
	for key, value := range legacy {
		switch key {
		case "cause", "code", "diagnostic", "error", "error_code", "origin", "reason_code", "retryable", "status", "user_message":
			continue
		default:
			details[key] = value
		}
	}
	message := map[string]any{"key": "common.requestFailed", "params": map[string]any{}, "fallback": reason}
	if descriptor, ok := legacy["user_message"].(map[string]any); ok {
		message = descriptor
	}
	operation := ""
	if r != nil {
		operation = r.Method + " " + r.URL.Path
	}
	if operation == "" {
		operation = "POST /api/stats-observations"
	}
	errorRecord := map[string]any{
		"code": code, "message": message, "origin": "server.http", "retryable": retryable,
		"details": details, "stack": []any{map[string]any{"component": "server.http", "operation": operation, "code": code}},
	}
	if value, ok := legacy["reason"].(string); ok && value != "" {
		errorRecord["reason"] = value
	}
	envelope := map[string]any{
		"state": "failed", "request": map[string]any{"id": statsRequestID(r)}, "error": errorRecord,
		"user_message": message, "legacy_error": reason, "status": status, "ok": false, "terminal": true,
	}
	for key, value := range details {
		if key != "data" && key != "error" && key != "operation" && key != "request" && key != "state" {
			envelope[key] = value
		}
	}
	return envelope
}

func statsObservationFailure(w http.ResponseWriter, r *http.Request, status int, legacy map[string]any) {
	jsonResponse(w, status, statsHTTPErrorEnvelope(r, status, legacy))
}

func statsObservationUnsupported(reason string) map[string]any {
	return statsUnsupported(reason)
}

// currentStatsSnapshot is the single request-facing read boundary. HTTP never
// opens SQLite or folds facts; the lifecycle must publish an immutable ring
// product before a request can be served.
func (s *server) currentStatsSnapshot(ctx context.Context, query statsQuery) (stats.StatsSnapshot, error) {
	if s.statsService == nil {
		return stats.StatsSnapshot{}, fmt.Errorf("%w: stats service is not initialized", stats.ErrProductPending)
	}
	if s.statsLifecycle != nil {
		// A pending HTTP read is still a demand signal. The lifecycle uses it to
		// prioritize this exact product over unrelated cold-ring repair work;
		// materialization remains fully off the request path.
		s.statsLifecycle.DemandProduct(query.rangeSeconds, query.resolution)
	}
	// The service owns the latest retained product window. Do not derive strict
	// bounds from this request's wall clock: a lifecycle publication can land
	// on either side of the bucket boundary while the request is in flight.
	snapshot, err := s.statsService.CurrentSnapshot(ctx, query.rangeSeconds, query.resolution)
	if err != nil {
		return stats.StatsSnapshot{}, err
	}
	// Snapshot returns the last complete product while the lifecycle rebuilds
	// dirty cells. A cold product still returns ErrProductPending from the
	// service, while a cached product carries its older source/cache generation
	// until the next immutable publication replaces it.
	return snapshot, nil
}

func statsPending(query statsQuery, reason string) map[string]any {
	if reason == "" {
		reason = "stats materialization is pending"
	}
	requestedResolution := any(query.resolution)
	if query.resolutionAuto {
		requestedResolution = "AUTO"
	}
	return map[string]any{
		"status": "pending", "protocol_version": statsWireVersion,
		"range_seconds": query.rangeSeconds, "requested_resolution": requestedResolution,
		"resolution_seconds": query.resolution, "retry_after_seconds": 1, "reason": reason,
	}
}

func (s *server) statsSnapshot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	query, failure, ok := parseStatsQuery(r, false)
	if !ok {
		jsonResponse(w, http.StatusBadRequest, statsHTTPErrorEnvelope(r, http.StatusBadRequest, failure))
		return
	}
	snapshot, err := s.currentStatsSnapshot(r.Context(), query)
	if err != nil {
		if errors.Is(err, stats.ErrProductPending) {
			jsonResponse(w, http.StatusAccepted, statsPending(query, err.Error()))
			return
		}
		jsonResponse(w, http.StatusFailedDependency, statsError(err.Error()))
		return
	}
	// Even an unchanged response establishes the client's current immutable
	// predecessor. A caller can follow a 304 with an after-generation delta;
	// that cursor must not be treated as outside the retained chain.
	seedKey := statsDeltaKey{rangeSeconds: query.rangeSeconds, resolution: query.resolution}
	seed := func() {
		s.seedStatsDeltaGeneration(seedKey, snapshot.CacheGeneration, snapshotWire(snapshot, query), 0)
	}
	if query.sinceGeneration > 0 && snapshot.CacheGeneration <= query.sinceGeneration {
		seed()
		jsonResponse(w, http.StatusNotModified, map[string]any{"protocol_version": statsWireVersion, "not_modified": true, "range_seconds": query.rangeSeconds, "resolution_seconds": query.resolution, "source_generation": snapshot.SourceGeneration, "cache_generation": snapshot.CacheGeneration})
		return
	}
	payload, _, err := snapshotPayloadForQuery(snapshot, query)
	if err != nil {
		if errors.Is(err, errSnapshotChunkGenerationAdvanced) {
			jsonResponse(w, http.StatusAccepted, statsPending(query, err.Error()))
			return
		}
		jsonResponse(w, http.StatusBadRequest, statsHTTPErrorEnvelope(r, http.StatusBadRequest, statsUnsupportedForQuery(false, err.Error(), query.rangeSeconds)))
		return
	}
	// A direct snapshot establishes the same predecessor as the SSE path.  The
	// next delta request may use this generation as its cursor, so retain the
	// complete immutable wire body (not a transport chunk) before responding.
	seed()
	jsonResponse(w, http.StatusOK, payload)
}

func (s *server) statsDelta(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	query, failure, ok := parseStatsQuery(r, true)
	if !ok {
		jsonResponse(w, http.StatusBadRequest, statsHTTPErrorEnvelope(r, http.StatusBadRequest, failure))
		return
	}
	snapshot, err := s.currentStatsSnapshot(r.Context(), query)
	if err != nil {
		if errors.Is(err, stats.ErrProductPending) {
			jsonResponse(w, http.StatusAccepted, statsPending(query, err.Error()))
			return
		}
		jsonResponse(w, http.StatusFailedDependency, statsError(err.Error()))
		return
	}
	generation := snapshot.CacheGeneration
	current := snapshotWire(snapshot, statsQuery{rangeSeconds: query.rangeSeconds, resolution: query.resolution})
	key := statsDeltaKey{rangeSeconds: query.rangeSeconds, resolution: query.resolution}
	s.statsDeltaMu.Lock()
	if s.statsDeltaHistory == nil {
		s.statsDeltaHistory = make(map[statsDeltaKey]map[int64]map[string]any)
	}
	if s.statsDeltaNextRevision == nil {
		s.statsDeltaNextRevision = make(map[statsDeltaKey]int64)
	}
	history := s.statsDeltaHistory[key]
	if history == nil {
		history = make(map[int64]map[string]any)
		s.statsDeltaHistory[key] = history
	}
	previous := history[query.afterCacheGeneration]
	currentRevision, hasCurrentRevision := history[generation]["revision"]
	if !hasCurrentRevision {
		currentRevision = float64(s.statsDeltaNextRevision[key])
		s.statsDeltaNextRevision[key]++
	}
	current["revision"] = currentRevision
	history[generation] = current
	if len(history) > statsDeltaHistoryLimit {
		oldest := generation
		for candidate := range history {
			if candidate < oldest {
				oldest = candidate
			}
		}
		delete(history, oldest)
	}
	s.statsDeltaMu.Unlock()
	revision := int64(currentRevision.(float64))
	// Python treats a cursor on the current immutable cache generation as
	// unchanged regardless of the delivery revision supplied by the caller.
	// Revisions describe the exact delta chain between generations; they must
	// not turn a same-generation probe into a repair response.
	if query.afterCacheGeneration == generation {
		jsonResponse(w, http.StatusNotModified, map[string]any{"protocol_version": statsWireVersion, "not_modified": true, "cache_generation": generation, "revision": revision})
		return
	}
	if previous == nil {
		jsonResponse(w, http.StatusConflict, statsHTTPErrorEnvelope(r, http.StatusConflict, map[string]any{
			"status": "repair_required", "protocol_version": statsWireVersion,
			"cache_generation": generation,
			"reason":           "delta cursor is outside the retained exact chain",
		}))
		return
	}
	if previous != nil {
		jsonResponse(w, http.StatusOK, deltaFromWireMaps(previous, current, query))
		return
	}
	jsonResponse(w, http.StatusOK, deltaWire(snapshot, query))
}

// seedStatsDeltaGeneration records the generation the stream has already
// delivered. Without this seed, the first subsequent generation has no
// predecessor in the bounded history and is incorrectly classified as an
// evicted cursor that requires repair.
func (s *server) seedStatsDeltaGeneration(key statsDeltaKey, generation int64, snapshot map[string]any, revision int64) {
	s.statsDeltaMu.Lock()
	defer s.statsDeltaMu.Unlock()
	if s.statsDeltaHistory == nil {
		s.statsDeltaHistory = make(map[statsDeltaKey]map[int64]map[string]any)
	}
	if s.statsDeltaNextRevision == nil {
		s.statsDeltaNextRevision = make(map[statsDeltaKey]int64)
	}
	history := s.statsDeltaHistory[key]
	if history == nil {
		history = make(map[int64]map[string]any)
		s.statsDeltaHistory[key] = history
	}
	if _, exists := history[generation]; exists {
		return
	}
	historySnapshot := make(map[string]any, len(snapshot)+1)
	for name, value := range snapshot {
		historySnapshot[name] = value
	}
	historySnapshot["revision"] = float64(revision)
	history[generation] = historySnapshot
	if s.statsDeltaNextRevision[key] <= revision {
		s.statsDeltaNextRevision[key] = revision + 1
	}
	if len(history) <= statsDeltaHistoryLimit {
		return
	}
	oldest := generation
	for candidate := range history {
		if candidate < oldest {
			oldest = candidate
		}
	}
	delete(history, oldest)
}

func snapshotWire(snapshot stats.StatsSnapshot, query statsQuery) map[string]any {
	// Ring-backed products intentionally omit the raw observation projection;
	// the public wire is defined by the immutable materialized bucket set.
	buckets := make([]map[string]any, len(snapshot.Materialized.Buckets))
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
		buckets[i] = map[string]any{"start": bucket.Start, "duration": bucket.Duration, "series": series, "source": map[string]any{"count": bucket.SourceCount, "first_timestamp": firstTimestamp, "last_timestamp": lastTimestamp}, "open": i == len(snapshot.Materialized.Buckets)-1 || !bucket.Complete}
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
	dimensionTotals := map[string]map[string]int64{}
	modelTotals := map[string]stats.MaterializedCostAttribution{}
	agentTotals := map[string]stats.MaterializedCostAttribution{}
	evidenceTotals := map[string]stats.PriceEvidence{}
	omittedModels, omittedAgents, omittedEvidence := int64(0), int64(0), int64(0)
	for _, bucket := range snapshot.Materialized.Buckets {
		if os.Getenv("YOLOMUX_DEBUG_COST") == "1" {
			for _, agent := range bucket.Cost.Agents {
				fmt.Fprintf(os.Stderr, "cost-debug bucket=%v agent=%s total=%d dims=%+v sources=%+v\\n", bucket.Start, agent.Key, agent.TotalTokens, agent.Dimensions, agent.Sources)
			}
		}
		totalTokens += bucket.Cost.Tokens
		totalMicroUSD += bucket.Cost.MicroUSD
		totalAPIListMicroUSD += bucket.Cost.APIListMicroUSD
		priced += int64(bucket.Cost.PricedAtoms)
		unpriced += int64(bucket.Cost.UnpricedAtoms)
		for _, dimension := range bucket.Cost.Dimensions {
			totals := dimensionTotals[dimension.Dimension]
			if totals == nil {
				totals = map[string]int64{"tokens": 0, "micro_usd": 0, "api_list_micro_usd": 0}
				dimensionTotals[dimension.Dimension] = totals
			}
			totals["tokens"] += dimension.Tokens
			totals["micro_usd"] += dimension.MicroUSD
			totals["api_list_micro_usd"] += dimension.APIListMicroUSD
		}
		for _, model := range bucket.Cost.Models {
			mergeCostAttribution(modelTotals, model)
		}
		for _, agent := range bucket.Cost.Agents {
			mergeCostAttribution(agentTotals, agent)
		}
		for _, evidence := range bucket.Cost.Evidence {
			mergePriceEvidence(evidenceTotals, evidence)
		}
		omittedModels += int64(bucket.Cost.OmittedModels)
		omittedAgents += int64(bucket.Cost.OmittedAgents)
		omittedEvidence += int64(bucket.Cost.OmittedEvidence)
	}
	return map[string]any{"protocol_version": statsWireVersion, "range_seconds": query.rangeSeconds, "requested_resolution": func() any {
		if query.resolutionAuto {
			return "AUTO"
		}
		return query.resolution
	}(), "resolution_seconds": query.resolution, "window_start": snapshot.Request.Start, "window_end": snapshot.Request.End, "generated_at": snapshot.GeneratedAt, "source_generation": snapshot.SourceGeneration, "cache_generation": snapshot.CacheGeneration, "rightmost_open": len(buckets) > 0 && buckets[len(buckets)-1]["open"] == true, "buckets": buckets, "no_data": noData, "cost_report": costReportWithEvidence(totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced, dimensionTotals, modelTotals, agentTotals, evidenceTotals, omittedModels, omittedAgents, omittedEvidence)}
}

// snapshotPayloadForQuery is the Go equivalent of Python's size-derived
// snapshot chunk projector. A normal request receives the complete snapshot
// when it fits the transport target; otherwise it receives chunk zero and the
// caller can request the remaining indexes with chunk_generation. Keeping the
// split deterministic from one immutable product means no separate mutable
// transport cache is needed on the HTTP path.
func snapshotPayloadForQuery(snapshot stats.StatsSnapshot, query statsQuery) (map[string]any, int, error) {
	full := snapshotWire(snapshot, query)
	encoded, err := json.Marshal(full)
	if err != nil {
		return nil, 0, fmt.Errorf("encode stats snapshot: %w", err)
	}
	buckets, ok := full["buckets"].([]map[string]any)
	if !ok {
		return nil, 0, errors.New("stats snapshot buckets are malformed")
	}
	chunked := query.hasChunkIndex || len(encoded) > statsSnapshotChunkTargetBytes
	if !chunked {
		return full, 1, nil
	}
	if len(buckets) < 2 {
		return nil, 0, errors.New("snapshot cannot be split into chunks")
	}
	chunkLimit := len(buckets)
	if chunkLimit > statsMaxSnapshotChunks {
		chunkLimit = statsMaxSnapshotChunks
	}
	chunkCount := 2
	if estimate := (len(encoded) + statsSnapshotChunkTargetBytes - 1) / statsSnapshotChunkTargetBytes; estimate > chunkCount {
		chunkCount = estimate
	}
	if chunkCount > chunkLimit {
		chunkCount = chunkLimit
	}
	for {
		chunks := make([]map[string]any, 0, chunkCount)
		largest := 0
		for index := 0; index < chunkCount; index++ {
			chunk, chunkErr := snapshotChunkWire(full, index, chunkCount)
			if chunkErr != nil {
				return nil, 0, chunkErr
			}
			chunkBytes, marshalErr := json.Marshal(chunk)
			if marshalErr != nil {
				return nil, 0, fmt.Errorf("encode stats snapshot chunk: %w", marshalErr)
			}
			if len(chunkBytes) > largest {
				largest = len(chunkBytes)
			}
			chunks = append(chunks, chunk)
		}
		if largest <= statsSnapshotChunkTargetBytes || chunkCount == chunkLimit {
			if largest > statsSnapshotChunkTargetBytes {
				return nil, 0, errors.New("one stats snapshot chunk exceeds the transport limit")
			}
			if query.hasChunkIndex {
				if query.chunkGeneration != snapshot.CacheGeneration {
					return nil, 0, errSnapshotChunkGenerationAdvanced
				}
				if query.chunkIndex < 0 || query.chunkIndex >= len(chunks) {
					return nil, 0, errors.New("chunk_index lies outside the retained snapshot batch")
				}
				return chunks[query.chunkIndex], len(chunks), nil
			}
			return chunks[0], len(chunks), nil
		}
		next := (chunkCount*largest + statsSnapshotChunkTargetBytes - 1) / statsSnapshotChunkTargetBytes
		if next <= chunkCount {
			next = chunkCount + 1
		}
		if next > chunkLimit {
			next = chunkLimit
		}
		chunkCount = next
	}
}

// streamSnapshotPayloads returns the first transport payload and the complete
// immutable snapshot used as the delta predecessor. A chunked stream must
// seed its cursor with every bucket, even though it sends only chunk zero in
// the first write; otherwise the first post-ready delta would resend every
// bucket omitted from that first chunk.
func streamSnapshotPayloads(snapshot stats.StatsSnapshot, query statsQuery) (first, seed map[string]any, chunkCount int, err error) {
	seed = snapshotWire(snapshot, query)
	first, chunkCount, err = snapshotPayloadForQuery(snapshot, query)
	if err != nil {
		return nil, nil, 0, err
	}
	return first, seed, chunkCount, nil
}

func snapshotChunkWire(full map[string]any, index, count int) (map[string]any, error) {
	buckets, ok := full["buckets"].([]map[string]any)
	if !ok || count < 2 || count > len(buckets) || index < 0 || index >= count {
		return nil, errors.New("snapshot chunk bounds are invalid")
	}
	resolution, ok := full["resolution_seconds"].(int)
	if !ok || resolution <= 0 {
		// JSON-facing maps normally carry an int here; accept float64 as well so
		// this helper remains usable by decoded fixtures in tests.
		if value, floatOK := full["resolution_seconds"].(float64); floatOK && float64(int(value)) == value && value > 0 {
			resolution = int(value)
		} else {
			return nil, errors.New("snapshot chunk resolution is invalid")
		}
	}
	first := index * len(buckets) / count
	last := (index + 1) * len(buckets) / count
	if first >= last || last > len(buckets) {
		return nil, errors.New("snapshot chunk has no buckets")
	}
	start, startOK := buckets[first]["start"].(float64)
	if !startOK {
		if value, intOK := buckets[first]["start"].(int); intOK {
			start = float64(value)
			startOK = true
		}
	}
	lastStart, lastOK := buckets[last-1]["start"].(float64)
	if !lastOK {
		if value, intOK := buckets[last-1]["start"].(int); intOK {
			lastStart = float64(value)
			lastOK = true
		}
	}
	if !startOK || !lastOK {
		return nil, errors.New("snapshot chunk bucket bounds are invalid")
	}
	chunkEnd := lastStart + float64(resolution)
	chunk := make(map[string]any, len(full)+4)
	for key, value := range full {
		if key != "buckets" && key != "no_data" && key != "chunk_index" && key != "chunk_count" && key != "chunk_start" && key != "chunk_end" {
			chunk[key] = value
		}
	}
	chunk["chunk_index"], chunk["chunk_count"] = index, count
	chunk["chunk_start"], chunk["chunk_end"] = start, chunkEnd
	chunk["buckets"] = buckets[first:last]
	noData, _ := full["no_data"].([]map[string]any)
	clipped := make([]map[string]any, 0, len(noData))
	for _, span := range noData {
		spanStart, startOK := span["start"].(float64)
		spanEnd, endOK := span["end"].(float64)
		if !startOK || !endOK || spanEnd <= start || spanStart >= chunkEnd {
			continue
		}
		copySpan := make(map[string]any, len(span))
		for key, value := range span {
			copySpan[key] = value
		}
		if spanStart < start {
			copySpan["start"] = start
		}
		if spanEnd > chunkEnd {
			copySpan["end"] = chunkEnd
		}
		clipped = append(clipped, copySpan)
	}
	chunk["no_data"] = clipped
	return chunk, nil
}

func costReport(totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced int64, dimensionTotals map[string]map[string]int64, modelTotals, agentTotals map[string]stats.MaterializedCostAttribution) map[string]any {
	return costReportWithEvidence(totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced, dimensionTotals, modelTotals, agentTotals, nil, 0, 0, 0)
}

func costReportWithEvidence(totalTokens, totalMicroUSD, totalAPIListMicroUSD, priced, unpriced int64, dimensionTotals map[string]map[string]int64, modelTotals, agentTotals map[string]stats.MaterializedCostAttribution, evidenceTotals map[string]stats.PriceEvidence, omittedModels, omittedAgents, omittedEvidence int64) map[string]any {
	dimensions := map[string]any{}
	for _, name := range []string{"input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "other"} {
		dimensions[name] = map[string]any{"tokens": int64(0), "micro_usd": int64(0), "api_list_micro_usd": int64(0)}
	}
	for name, totals := range dimensionTotals {
		if _, known := dimensions[name]; known {
			dimensions[name] = map[string]any{"tokens": totals["tokens"], "micro_usd": totals["micro_usd"], "api_list_micro_usd": totals["api_list_micro_usd"]}
		}
	}
	// Non-token or legacy atoms have no typed dimension and remain explicit in
	// the neutral bucket. Preserve aggregate totals if a caller supplies an old
	// materialized layer without dimension detail.
	knownTokens := int64(0)
	knownMicroUSD := int64(0)
	knownAPIListMicroUSD := int64(0)
	for _, value := range dimensions {
		row := value.(map[string]any)
		knownTokens += row["tokens"].(int64)
		knownMicroUSD += row["micro_usd"].(int64)
		knownAPIListMicroUSD += row["api_list_micro_usd"].(int64)
	}
	if knownTokens < totalTokens || knownMicroUSD < totalMicroUSD || knownAPIListMicroUSD < totalAPIListMicroUSD {
		dimensions["other"] = map[string]any{"tokens": totalTokens - knownTokens, "micro_usd": totalMicroUSD - knownMicroUSD, "api_list_micro_usd": totalAPIListMicroUSD - knownAPIListMicroUSD}
	}
	pricedTokens := int64(0)
	unpricedTokens := int64(0)
	for _, value := range modelTotals {
		pricedTokens += value.PricedTokens
		unpricedTokens += value.UnpricedTokens
	}
	// Atoms without a model cannot appear in an attribution row. Preserve
	// their explicit priced/unpriced status in the aggregate rather than
	// declaring every token unpriced when one unknown model is present.
	if pricedTokens+unpricedTokens < totalTokens {
		if unpriced == 0 {
			pricedTokens += totalTokens - pricedTokens - unpricedTokens
		} else {
			unpricedTokens += totalTokens - pricedTokens - unpricedTokens
		}
	}
	evidence := make([]stats.PriceEvidence, 0, len(evidenceTotals))
	for _, value := range evidenceTotals {
		evidence = append(evidence, value)
	}
	sort.Slice(evidence, func(i, j int) bool {
		if evidence[i].Tokens != evidence[j].Tokens {
			return evidence[i].Tokens > evidence[j].Tokens
		}
		if evidence[i].APIListMicroUSD != evidence[j].APIListMicroUSD {
			return evidence[i].APIListMicroUSD > evidence[j].APIListMicroUSD
		}
		if evidence[i].MicroUSD != evidence[j].MicroUSD {
			return evidence[i].MicroUSD > evidence[j].MicroUSD
		}
		return evidence[i].Key < evidence[j].Key
	})
	if len(evidence) > 32 {
		omittedEvidence += int64(len(evidence) - 32)
		evidence = evidence[:32]
	}
	evidenceRows := make([]any, 0, len(evidence))
	catalogRevision := int64(0)
	for _, value := range evidence {
		if value.CatalogRevision > catalogRevision {
			catalogRevision = value.CatalogRevision
		}
		evidenceRows = append(evidenceRows, map[string]any{
			"key": value.Key, "provider": value.Provider, "model": value.Model, "dimension": value.Dimension,
			"direction": value.Direction, "modality": value.Modality, "cache_role": value.CacheRole, "unit": value.Unit,
			"pricing_profile": value.PricingProfile, "service_tier": value.ServiceTier, "catalog_model": value.CatalogModel,
			"rate_usd": value.RateUSD, "rate_scale": value.RateScale, "effective_from": value.EffectiveFrom,
			"source_kind": value.SourceKind, "source_url": value.SourceURL, "catalog_revision": value.CatalogRevision,
			"tokens": value.Tokens, "micro_usd": value.MicroUSD, "api_list_micro_usd": value.APIListMicroUSD, "priced_atoms": value.PricedAtoms,
		})
	}
	return map[string]any{
		"schema_version":           4,
		"total_micro_usd":          totalMicroUSD,
		"total_api_list_micro_usd": totalAPIListMicroUSD,
		"total_tokens":             totalTokens,
		"dimensions":               dimensions,
		"priced":                   map[string]any{"atoms": priced, "tokens": pricedTokens},
		"unpriced":                 map[string]any{"atoms": unpriced, "tokens": unpricedTokens},
		"models":                   costAttributionWire(modelTotals, false), "agents": costAttributionWire(agentTotals, true), "evidence": evidenceRows,
		"catalog_revision":    catalogRevision,
		"omissions":           map[string]any{"models": omittedModels, "agents": omittedAgents, "evidence": omittedEvidence},
		"reasoning_available": false,
	}
}

func mergeCostAttribution(target map[string]stats.MaterializedCostAttribution, value stats.MaterializedCostAttribution) {
	current := target[value.Key]
	current.Key, current.Provider, current.Model, current.Source, current.Label = value.Key, value.Provider, value.Model, value.Source, value.Label
	current.TotalTokens += value.TotalTokens
	current.MicroUSD += value.MicroUSD
	current.APIListMicroUSD += value.APIListMicroUSD
	current.PricedAtoms += value.PricedAtoms
	current.UnpricedAtoms += value.UnpricedAtoms
	current.PricedTokens += value.PricedTokens
	current.UnpricedTokens += value.UnpricedTokens
	for _, dimension := range value.Dimensions {
		found := false
		for index := range current.Dimensions {
			if current.Dimensions[index].Dimension == dimension.Dimension {
				current.Dimensions[index].Tokens += dimension.Tokens
				current.Dimensions[index].MicroUSD += dimension.MicroUSD
				current.Dimensions[index].APIListMicroUSD += dimension.APIListMicroUSD
				found = true
				break
			}
		}
		if !found {
			current.Dimensions = append(current.Dimensions, dimension)
		}
	}
	for _, source := range value.Sources {
		mergeCostSource(&current, source)
	}
	target[value.Key] = current
}

func mergeCostSource(target *stats.MaterializedCostAttribution, value stats.MaterializedCostSource) {
	for index := range target.Sources {
		current := &target.Sources[index]
		if current.Source != value.Source {
			continue
		}
		current.TotalTokens += value.TotalTokens
		current.MicroUSD += value.MicroUSD
		current.APIListMicroUSD += value.APIListMicroUSD
		current.PricedAtoms += value.PricedAtoms
		current.UnpricedAtoms += value.UnpricedAtoms
		current.PricedTokens += value.PricedTokens
		current.UnpricedTokens += value.UnpricedTokens
		mergeCostDimensions(&current.Dimensions, value.Dimensions)
		return
	}
	// The source dimensions belong to the immutable materialized bucket. Keep
	// the aggregate's working copy independent so folding a later bucket cannot
	// mutate an already-published bucket (or a prior wire response).
	value.Dimensions = append([]stats.MaterializedCostDimension(nil), value.Dimensions...)
	target.Sources = append(target.Sources, value)
}

func mergeCostDimensions(target *[]stats.MaterializedCostDimension, values []stats.MaterializedCostDimension) {
	for _, value := range values {
		found := false
		for index := range *target {
			if (*target)[index].Dimension == value.Dimension {
				(*target)[index].Tokens += value.Tokens
				(*target)[index].MicroUSD += value.MicroUSD
				(*target)[index].APIListMicroUSD += value.APIListMicroUSD
				found = true
				break
			}
		}
		if !found {
			*target = append(*target, value)
		}
	}
}

func mergePriceEvidence(target map[string]stats.PriceEvidence, value stats.PriceEvidence) {
	if current, ok := target[value.Key]; ok {
		current.Tokens += value.Tokens
		current.MicroUSD += value.MicroUSD
		current.APIListMicroUSD += value.APIListMicroUSD
		current.PricedAtoms += value.PricedAtoms
		target[value.Key] = current
		return
	}
	target[value.Key] = value
}

func costAttributionWire(values map[string]stats.MaterializedCostAttribution, agent bool) []any {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		left, right := values[keys[i]], values[keys[j]]
		if left.TotalTokens != right.TotalTokens {
			return left.TotalTokens > right.TotalTokens
		}
		if left.APIListMicroUSD != right.APIListMicroUSD {
			return left.APIListMicroUSD > right.APIListMicroUSD
		}
		if left.MicroUSD != right.MicroUSD {
			return left.MicroUSD > right.MicroUSD
		}
		return keys[i] < keys[j]
	})
	result := make([]any, 0, len(keys))
	for _, key := range keys {
		value := values[key]
		dimensions := make(map[string]any, 6)
		for _, name := range []string{"input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "other"} {
			dimensions[name] = map[string]any{"tokens": int64(0), "micro_usd": int64(0), "api_list_micro_usd": int64(0)}
		}
		for _, dimension := range value.Dimensions {
			dimensions[dimension.Dimension] = map[string]any{"tokens": dimension.Tokens, "micro_usd": dimension.MicroUSD, "api_list_micro_usd": dimension.APIListMicroUSD}
		}
		row := map[string]any{"key": value.Key, "total_tokens": value.TotalTokens, "total_micro_usd": value.MicroUSD, "total_api_list_micro_usd": value.APIListMicroUSD, "dimensions": dimensions, "priced": map[string]any{"atoms": value.PricedAtoms, "tokens": value.PricedTokens}, "unpriced": map[string]any{"atoms": value.UnpricedAtoms, "tokens": value.UnpricedTokens}}
		if agent {
			source := costSourceKind(value.Source)
			label := value.Label
			if label == "" {
				label = value.Key
			}
			row["source"], row["label"] = source, label
			row["sources"] = costSourceRows(value, source, dimensions)
		} else {
			row["provider"], row["model"] = value.Provider, value.Model
		}
		result = append(result, row)
	}
	return result
}

func costSourceRows(value stats.MaterializedCostAttribution, fallbackSource string, fallbackDimensions map[string]any) []any {
	sources := make([]stats.MaterializedCostSource, len(value.Sources))
	copy(sources, value.Sources)
	if len(sources) == 0 {
		sources = append(sources, stats.MaterializedCostSource{Source: fallbackSource, TotalTokens: value.TotalTokens, MicroUSD: value.MicroUSD, APIListMicroUSD: value.APIListMicroUSD, Dimensions: value.Dimensions, PricedAtoms: value.PricedAtoms, UnpricedAtoms: value.UnpricedAtoms, PricedTokens: value.PricedTokens, UnpricedTokens: value.UnpricedTokens})
	}
	sort.Slice(sources, func(i, j int) bool {
		if sources[i].TotalTokens != sources[j].TotalTokens {
			return sources[i].TotalTokens > sources[j].TotalTokens
		}
		if sources[i].APIListMicroUSD != sources[j].APIListMicroUSD {
			return sources[i].APIListMicroUSD > sources[j].APIListMicroUSD
		}
		if sources[i].MicroUSD != sources[j].MicroUSD {
			return sources[i].MicroUSD > sources[j].MicroUSD
		}
		return sources[i].Source < sources[j].Source
	})
	rows := make([]any, 0, len(sources))
	for _, item := range sources {
		dimensions := fallbackDimensions
		if len(item.Dimensions) > 0 {
			dimensions = make(map[string]any, 6)
			for _, name := range []string{"input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "other"} {
				dimensions[name] = map[string]any{"tokens": int64(0), "micro_usd": int64(0), "api_list_micro_usd": int64(0)}
			}
			for _, dimension := range item.Dimensions {
				dimensions[dimension.Dimension] = map[string]any{"tokens": dimension.Tokens, "micro_usd": dimension.MicroUSD, "api_list_micro_usd": dimension.APIListMicroUSD}
			}
		}
		rows = append(rows, map[string]any{"source": costSourceKind(item.Source), "total_tokens": item.TotalTokens, "total_micro_usd": item.MicroUSD, "total_api_list_micro_usd": item.APIListMicroUSD, "dimensions": dimensions, "priced": map[string]any{"atoms": item.PricedAtoms, "tokens": item.PricedTokens}, "unpriced": map[string]any{"atoms": item.UnpricedAtoms, "tokens": item.UnpricedTokens}})
	}
	return rows
}

func costSourceKind(value string) string {
	switch value {
	case "claude", "codex", "opencode", "yoagent", "summary", "images", "responses", "legacy", "unknown":
		return value
	default:
		return "unknown"
	}
}

func deltaFromWireMaps(previous, current map[string]any, query statsQuery) map[string]any {
	previousBuckets := wireBucketMap(previous["buckets"])
	currentBuckets := wireBucketMap(current["buckets"])
	buckets := make([]map[string]any, 0)
	for key, bucket := range currentBuckets {
		if old, ok := previousBuckets[key]; !ok || !reflect.DeepEqual(old, bucket) {
			buckets = append(buckets, bucket)
		}
	}
	tombstones := make([]any, 0)
	removedBucketKeys := make([]string, 0)
	for key := range previousBuckets {
		if _, ok := currentBuckets[key]; !ok {
			removedBucketKeys = append(removedBucketKeys, key)
		}
	}
	sort.Slice(removedBucketKeys, func(i, j int) bool {
		left, right := previousBuckets[removedBucketKeys[i]], previousBuckets[removedBucketKeys[j]]
		leftStart, rightStart := left["start"].(float64), right["start"].(float64)
		if leftStart != rightStart {
			return leftStart < rightStart
		}
		return left["duration"].(float64) < right["duration"].(float64)
	})
	for _, key := range removedBucketKeys {
		old := previousBuckets[key]
		tombstones = append(tombstones, map[string]any{"kind": "bucket", "start": old["start"], "duration": old["duration"]})
	}
	previousNoData := wireNoDataMap(previous["no_data"])
	currentNoData := wireNoDataMap(current["no_data"])
	noData := make([]map[string]any, 0)
	for key, item := range currentNoData {
		if old, ok := previousNoData[key]; !ok || !reflect.DeepEqual(old, item) {
			noData = append(noData, item)
		}
	}
	removedNoDataKeys := make([]string, 0)
	for key := range previousNoData {
		if _, ok := currentNoData[key]; !ok {
			removedNoDataKeys = append(removedNoDataKeys, key)
		}
	}
	sort.Slice(removedNoDataKeys, func(i, j int) bool {
		left, right := previousNoData[removedNoDataKeys[i]], previousNoData[removedNoDataKeys[j]]
		for _, field := range []string{"family", "source_id", "epoch"} {
			leftValue, rightValue := left[field].(string), right[field].(string)
			if leftValue != rightValue {
				return leftValue < rightValue
			}
		}
		leftStart, rightStart := left["start"].(float64), right["start"].(float64)
		if leftStart != rightStart {
			return leftStart < rightStart
		}
		return left["end"].(float64) < right["end"].(float64)
	})
	for _, key := range removedNoDataKeys {
		old := previousNoData[key]
		tombstones = append(tombstones, map[string]any{"kind": "no_data", "family": old["family"], "source_id": old["source_id"], "epoch": old["epoch"], "start": old["start"], "end": old["end"]})
	}
	sort.Slice(buckets, func(i, j int) bool { return buckets[i]["start"].(float64) < buckets[j]["start"].(float64) })
	sort.Slice(noData, func(i, j int) bool {
		left, right := noData[i], noData[j]
		for _, field := range []string{"family", "source_id", "epoch"} {
			leftValue, rightValue := left[field].(string), right[field].(string)
			if leftValue != rightValue {
				return leftValue < rightValue
			}
		}
		leftStart, rightStart := left["start"].(float64), right["start"].(float64)
		if leftStart != rightStart {
			return leftStart < rightStart
		}
		return left["end"].(float64) < right["end"].(float64)
	})
	if len(buckets) == 0 && len(noData) == 0 && len(tombstones) == 0 && len(currentBuckets) > 0 {
		latestKey := ""
		for key := range currentBuckets {
			if latestKey == "" || currentBuckets[key]["start"].(float64) > currentBuckets[latestKey]["start"].(float64) {
				latestKey = key
			}
		}
		buckets = append(buckets, currentBuckets[latestKey])
	}
	return map[string]any{
		"protocol_version":      statsWireVersion,
		"range_seconds":         query.rangeSeconds,
		"resolution_seconds":    query.resolution,
		"source_generation":     current["source_generation"],
		"base_cache_generation": query.afterCacheGeneration,
		"cache_generation":      current["cache_generation"],
		"revision":              wireRevision(current),
		"buckets":               buckets,
		"no_data":               noData,
		"tombstones":            tombstones,
		"cost_report":           current["cost_report"],
	}
}

func wireBucketMap(value any) map[string]map[string]any {
	result := map[string]map[string]any{}
	for _, raw := range value.([]map[string]any) {
		key := fmt.Sprintf("%v/%v", raw["start"], raw["duration"])
		result[key] = raw
	}
	return result
}

func wireNoDataMap(value any) map[string]map[string]any {
	result := map[string]map[string]any{}
	for _, raw := range value.([]map[string]any) {
		// The no-data identity matches Python's gap_key: a reason change is an
		// updated span, not a removal followed by a new span.
		key := fmt.Sprintf("%v/%v/%v/%v/%v", raw["family"], raw["source_id"], raw["epoch"], raw["start"], raw["end"])
		result[key] = raw
	}
	return result
}

func deltaWire(snapshot stats.StatsSnapshot, query statsQuery) map[string]any {
	wire := snapshotWire(snapshot, query)
	return map[string]any{
		"protocol_version":      statsWireVersion,
		"range_seconds":         query.rangeSeconds,
		"resolution_seconds":    query.resolution,
		"source_generation":     snapshot.SourceGeneration,
		"base_cache_generation": query.afterCacheGeneration,
		"cache_generation":      snapshot.CacheGeneration,
		"revision":              wireRevision(wire),
		"buckets":               wire["buckets"],
		"no_data":               wire["no_data"],
		"tombstones":            []any{},
		"cost_report":           wire["cost_report"],
	}
}

func wireRevision(wire map[string]any) any {
	if revision, ok := wire["revision"]; ok {
		return revision
	}
	return wire["cache_generation"]
}

func (s *server) statsObservations(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.ContentLength > 128*1024 {
		statsObservationFailure(w, r, http.StatusRequestEntityTooLarge, map[string]any{
			"error":        "content too large",
			"user_message": map[string]any{"key": "request.error.contentTooLarge", "params": map[string]any{"max": 128 * 1024}, "fallback": "content too large"},
		})
		return
	}
	defer r.Body.Close()
	body, readErr := io.ReadAll(io.LimitReader(r.Body, 128*1024+1))
	if readErr != nil {
		statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported("invalid observations JSON"))
		return
	}
	if len(body) > 128*1024 {
		statsObservationFailure(w, r, http.StatusRequestEntityTooLarge, map[string]any{
			"error":        "content too large",
			"user_message": map[string]any{"key": "request.error.contentTooLarge", "params": map[string]any{"max": 128 * 1024}, "fallback": "content too large"},
		})
		return
	}
	var raw map[string]json.RawMessage
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err := decoder.Decode(&raw); err != nil {
		reason := err.Error()
		if strings.Contains(reason, "unexpected end of JSON input") || strings.Contains(reason, "unexpected EOF") {
			reason = fmt.Sprintf("Expecting ',' delimiter: line 1 column %d (char %d)", len(body)+1, len(body))
		}
		statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported(reason))
		return
	}
	firstValueOffset := decoder.InputOffset()
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); err != io.EOF {
		statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported(trailingJSONReason(body, firstValueOffset)))
		return
	}
	if !hasExactKeys(raw, "protocol_version", "schema_generation", "client_id", "observations") {
		statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported("browser observation upload fields must be exactly ['client_id', 'observations', 'protocol_version', 'schema_generation']"))
		return
	}
	var envelope browserObservationEnvelope
	if err := unmarshalRaw(raw, &envelope); err != nil {
		statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported(err.Error()))
		return
	}
	if envelope.ProtocolVersion != statsWriterProtocol || envelope.SchemaGeneration != statsSchemaGeneration {
		statsObservationFailure(w, r, http.StatusUpgradeRequired, map[string]any{
			"status": "upgrade_required", "protocol_version": statsWireVersion,
			"required_protocol_version": statsWriterProtocol, "required_schema_generation": statsSchemaGeneration,
			"required_build": "8", "reason": "client or writer is too old", "caller_outcome_owner": "statsd.browser_upload",
		})
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
		statsObservationFailure(w, r, http.StatusFailedDependency, statsObservationUnsupported("client binding unavailable"))
		return
	}
	observations, err := parseBrowserObservationEnvelope(raw, user.username, secret)
	if err != nil {
		statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported(err.Error()))
		return
	}
	requestEventIDs := make(map[string]struct{}, len(envelope.Observations))
	for index, rawItem := range envelope.Observations {
		var item map[string]json.RawMessage
		var eventID string
		if json.Unmarshal(rawItem, &item) != nil || decodeText(item["event_id"], &eventID, 512) != nil {
			statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported(fmt.Sprintf("observations[%d] has invalid identity", index)))
			return
		}
		if _, exists := requestEventIDs[eventID]; exists {
			statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported("browser observation event IDs must be unique within one upload"))
			return
		}
		requestEventIDs[eventID] = struct{}{}
	}
	if s.statsWriter == nil {
		statsObservationFailure(w, r, http.StatusFailedDependency, statsObservationUnsupported("stats writer unavailable"))
		return
	}
	result, err := s.statsWriter.AppendBatch(r.Context(), stats.AppendBatch{Observations: observations})
	if err != nil {
		statsObservationFailure(w, r, http.StatusBadRequest, statsObservationUnsupported(err.Error()))
		return
	}
	accepted := make(map[string]struct{}, len(result.AcceptedObservationIDs))
	for _, eventID := range result.AcceptedObservationIDs {
		accepted[eventID] = struct{}{}
	}
	receipts := make([]map[string]any, 0, len(envelope.Observations))
	for index, rawItem := range envelope.Observations {
		var item map[string]json.RawMessage
		var eventID string
		if json.Unmarshal(rawItem, &item) == nil && decodeText(item["event_id"], &eventID, 512) == nil {
			boundEventID := boundStatsClientID(secret, user.username, strings.TrimSpace(envelope.ClientID)+":event:"+eventID)
			disposition := "duplicate"
			if _, ok := accepted[boundEventID]; ok {
				disposition = "accepted"
			}
			receipts = append(receipts, map[string]any{"event_id": eventID, "disposition": disposition})
			continue
		}
		// parseBrowserObservationEnvelope already validated every item, so this
		// branch is defensive and keeps the response shape deterministic if that
		// invariant changes later.
		receipts = append(receipts, map[string]any{"event_id": fmt.Sprintf("observation-%d", index), "disposition": "duplicate"})
	}
	statsPayload := map[string]any{"ok": true, "source_generation": result.Generation, "accepted": result.ObservationsAccepted, "duplicates": result.ObservationsDuplicate, "observation_receipts": receipts}
	jsonResponse(w, http.StatusOK, statsReadyEnvelope(statsRequestID(r), statsPayload))
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

func trailingJSONReason(body []byte, offset int64) string {
	index := int(offset)
	if index < 0 {
		index = 0
	}
	if index > len(body) {
		index = len(body)
	}
	for index < len(body) {
		switch body[index] {
		case ' ', '\t', '\r', '\n':
			index++
		default:
			goto positioned
		}
	}
positioned:
	line, column := 1, 1
	for _, character := range body[:index] {
		if character == '\n' {
			line, column = line+1, 1
		} else {
			column++
		}
	}
	return fmt.Sprintf("Extra data: line %d column %d (char %d)", line, column, index)
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
	clientID := strings.TrimSpace(envelope.ClientID)
	if clientID == "" || len([]byte(clientID)) > 128 || hasControlCharacter(clientID) {
		return nil, fmt.Errorf("client_id is too long or contains controls")
	}
	if len(envelope.Observations) < 1 || len(envelope.Observations) > 1000 {
		return nil, fmt.Errorf("observations must contain 1..1000 items")
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
		if family != "browser" {
			return nil, fmt.Errorf("browser observation family must be browser")
		}
		if sourceID != clientID {
			return nil, fmt.Errorf("browser observation source_id must match client_id")
		}
		var observedAt float64
		if err := json.Unmarshal(item["observed_at"], &observedAt); err != nil || math.IsNaN(observedAt) || math.IsInf(observedAt, 0) || observedAt < 0 {
			return nil, fmt.Errorf("observations[%d].observed_at must be a non-negative finite number", index)
		}
		var payload map[string]any
		if err := json.Unmarshal(item["payload"], &payload); err != nil || payload == nil {
			return nil, fmt.Errorf("observations[%d].payload must be an object", index)
		}
		if err := validateBrowserPayload(payload); err != nil {
			return nil, err
		}
		payload = sanitizeBrowserPayload(payload)
		if err := validateBrowserPayload(payload); err != nil {
			return nil, err
		}
		result = append(result, stats.Observation{EventID: boundStatsClientID(secret, username, clientID+":event:"+eventID), Family: family, SourceID: boundStatsClientID(secret, username, clientID), ObservedAt: observedAt, EpochID: boundStatsClientID(secret, username, clientID+":epoch:"+epochID), Payload: payload})
	}
	return result, nil
}

func boundStatsClientID(secret []byte, username, value string) string {
	h := hmac.New(sha256.New, secret)
	_, _ = h.Write([]byte(statsClientIDDomain + username + "\x00" + strings.TrimSpace(value)))
	return "stats-" + hex.EncodeToString(h.Sum(nil))[:32]
}

var browserSecretKey = regexp.MustCompile(`(?i)^(?:token|secret|password|passwd|authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|bearer|api[-_]?key|client[-_]?secret|(?:access|refresh)[-_]?token|[A-Za-z][A-Za-z0-9_-]{0,62}[-_]token)$`)
var browserTokenQuery = regexp.MustCompile(`(?i)([?#&][A-Za-z][A-Za-z0-9_-]{0,63}token=)[^&#\s"']+`)
var browserBearerValue = regexp.MustCompile(`(?i)\b(Bearer)[ \t]+[^\s,;:="'<>]+`)
var browserSecretAssignment = regexp.MustCompile(`(?i)\b(token|secret|password|passwd|authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|bearer|api[-_]?key|client[-_]?secret|access[-_]?token|refresh[-_]?token|[A-Za-z][A-Za-z0-9_-]{0,62}[-_]token)["']?[ \t]*([=:][ \t]*)("[^"]*"|'[^']*'|[^&#\s,;"'<>}]+)`)

// sanitizeBrowserPayload mirrors the Python observation boundary: browser diagnostics are
// retained facts, so redaction must happen before they enter the durable stats store.
func sanitizeBrowserPayload(payload map[string]any) map[string]any {
	result := make(map[string]any, len(payload))
	for key, value := range payload {
		if browserSecretKey.MatchString(key) {
			result[key] = "[redacted-secret]"
			continue
		}
		if text, ok := value.(string); ok {
			result[key] = sanitizeBrowserText(text)
			continue
		}
		result[key] = value
	}
	for _, key := range []string{"message", "stack"} {
		if text, ok := result[key].(string); ok {
			limit := 4000
			if key == "message" {
				limit = 500
			}
			result[key] = boundBrowserText(text, limit)
		}
	}
	return result
}

func sanitizeBrowserText(value string) string {
	value = browserTokenQuery.ReplaceAllString(value, `$1[redacted-secret]`)
	value = browserBearerValue.ReplaceAllString(value, `${1} [redacted-secret]`)
	return browserSecretAssignment.ReplaceAllString(value, `${1}${2}[redacted-secret]`)
}

func boundBrowserText(value string, maximum int) string {
	if len([]byte(value)) <= maximum {
		return value
	}
	marker := "[truncated]"
	bytes := []byte(value)
	budget := maximum - len([]byte(marker))
	if budget < 0 {
		budget = 0
	}
	value = string(bytes[:budget])
	for !utf8.ValidString(value) && len(value) > 0 {
		value = value[:len(value)-1]
	}
	return value + marker
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

func hasControlCharacter(value string) bool {
	for _, character := range value {
		if character < 0x20 || character == 0x7f {
			return true
		}
	}
	return false
}

func validateBrowserPayload(payload map[string]any) error {
	kind, ok := payload["kind"].(string)
	if !ok || kind == "" {
		return fmt.Errorf("browser payload is missing fields: kind")
	}
	if !browserEventKinds[kind] {
		return fmt.Errorf("browser.kind must be one of: api, disconnect, error, finder_usable, heartbeat, interaction, long_task, operation_wait, page_load, sse, unhandledrejection, warning")
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
				return fmt.Errorf("browser %s requires signature, message, source, and optional stack position", kind)
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
	query, failure, ok := parseStatsQuery(r, false)
	if !ok {
		// Python validates the request before switching the response to SSE. A
		// malformed stream query is therefore an ordinary 400 JSON response,
		// not a successful SSE connection carrying an unsupported event.
		jsonResponse(w, http.StatusBadRequest, statsHTTPErrorEnvelope(r, http.StatusBadRequest, failure))
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	streamWriter := &statsSSEWriter{writer: w, startedAt: time.Now()}
	s.statsDemand.Add(1)
	defer s.statsDemand.Add(-1)
	flusher, ok := w.(http.Flusher)
	if !ok {
		return
	}
	flusher.Flush()
	heartbeat := s.statsHeartbeat
	if heartbeat <= 0 {
		// Python schedules one unchanged ready frame per min(resolution, 60)
		// seconds. Keep test-only overrides while making the production stream
		// use the same cadence instead of a fixed one-second poll.
		heartbeat = time.Duration(minInt(query.resolution, 60)) * time.Second
	}
	var snapshot stats.StatsSnapshot
	for {
		var err error
		snapshot, err = s.currentStatsSnapshot(r.Context(), query)
		if err == nil {
			break
		}
		if !errors.Is(err, stats.ErrProductPending) {
			streamWriter.write("unavailable", statsError(err.Error()))
			return
		}
		// A source generation can advance after the last immutable product was
		// published. Keep the stream alive while the lifecycle repairs it; a
		// terminal pending response would make callers race a new EventSource
		// against the same stale product.
		streamWriter.write("pending", statsPending(query, err.Error()))
		if s.statsService != nil {
			key := stats.ProductKey{RangeSeconds: query.rangeSeconds, ResolutionSeconds: query.resolution}
			if _, waitErr := s.statsService.WaitForProductPublication(r.Context(), key, 0, heartbeat); waitErr != nil {
				return
			}
			continue
		}
		timer := time.NewTimer(100 * time.Millisecond)
		select {
		case <-r.Context().Done():
			if !timer.Stop() {
				select {
				case <-timer.C:
				default:
				}
			}
			return
		case <-timer.C:
			// A partially constructed server (and a cold startup before the
			// lifecycle is attached) still has to keep an accepted SSE cursor
			// alive. Emit the same liveness signal as a settled stream instead
			// of silently polling until the client declares it stalled.
			streamWriter.write("heartbeat", map[string]any{"status": "pending", "retry_after_seconds": 1})
		}
	}
	requestedResolution := any(query.resolution)
	if query.resolutionAuto {
		requestedResolution = "AUTO"
	}
	notModified := query.sinceGeneration > 0 && snapshot.CacheGeneration <= query.sinceGeneration
	var snapshotPayload, seedPayload map[string]any
	chunkCount := 1
	if notModified {
		// The browser already owns this immutable generation. Still seed the
		// server-side predecessor with the complete body so the next delta is
		// minimal after a reconnect that skipped the snapshot.
		seedPayload = snapshotWire(snapshot, query)
	} else {
		var payloadErr error
		snapshotPayload, seedPayload, chunkCount, payloadErr = streamSnapshotPayloads(snapshot, query)
		if payloadErr != nil {
			streamWriter.write("unavailable", statsError(payloadErr.Error()))
			return
		}
	}
	streamWriter.write("ack", map[string]any{"cache_generation": snapshot.CacheGeneration, "chunk_count": chunkCount, "not_modified": notModified, "range_seconds": query.rangeSeconds, "requested_resolution": requestedResolution, "resolution_seconds": query.resolution})
	s.seedStatsDeltaGeneration(statsDeltaKey{rangeSeconds: query.rangeSeconds, resolution: query.resolution}, snapshot.CacheGeneration, seedPayload, 0)
	if !notModified {
		streamWriter.write("snapshot", snapshotPayload)
		for index := 1; index < chunkCount; index++ {
			chunkQuery := query
			chunkQuery.chunkIndex = index
			chunkQuery.chunkGeneration = snapshot.CacheGeneration
			chunkQuery.hasChunkIndex = true
			chunk, _, chunkErr := snapshotPayloadForQuery(snapshot, chunkQuery)
			if chunkErr != nil {
				streamWriter.write("unavailable", statsError(chunkErr.Error()))
				return
			}
			streamWriter.write("snapshot", chunk)
		}
	}
	streamWriter.write("ready", map[string]any{"cache_generation": snapshot.CacheGeneration, "revision": 0})
	flusher.Flush()
	cacheGeneration := snapshot.CacheGeneration
	// The initial ready frame establishes revision zero for this cache cursor.
	// Cache generations and delivery revisions are independent: a generation can
	// be assigned a later revision, and using the generation here causes the first
	// post-ready delta to look evicted and terminate the stream with repair.
	revision := int64(0)
	var ticker *time.Ticker
	if s.statsService == nil {
		ticker = time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()
	}
	for {
		if s.statsService != nil {
			key := stats.ProductKey{RangeSeconds: query.rangeSeconds, ResolutionSeconds: query.resolution}
			changed, waitErr := s.statsService.WaitForProductPublication(r.Context(), key, cacheGeneration, heartbeat)
			if waitErr != nil {
				return
			}
			if !changed {
				// `ready` is the Python wire event for an unchanged generation and
				// the browser's liveness owner consumes only this event. A separate
				// heartbeat event makes the stream appear stalled after three ticks.
				streamWriter.write("ready", map[string]any{"cache_generation": cacheGeneration, "revision": revision})
				continue
			}
		} else {
			select {
			case <-r.Context().Done():
				return
			case <-ticker.C:
			}
		}
		deltaRequest := r.Clone(r.Context())
		values := deltaRequest.URL.Query()
		values.Del("resolution")
		values.Del("since_generation")
		values.Set("resolution_seconds", strconv.Itoa(query.resolution))
		values.Set("after_cache_generation", strconv.FormatInt(cacheGeneration, 10))
		values.Set("after_revision", strconv.FormatInt(revision, 10))
		deltaRequest.URL.RawQuery = values.Encode()
		var deltaResponse bytes.Buffer
		deltaRecorder := &bufferResponseWriter{header: make(http.Header), body: &deltaResponse}
		s.statsDelta(deltaRecorder, deltaRequest)
		if deltaRecorder.status == http.StatusNotModified {
			streamWriter.write("ready", map[string]any{"cache_generation": cacheGeneration, "revision": revision})
			continue
		}
		if deltaRecorder.status == http.StatusAccepted {
			var pending map[string]any
			if err := json.Unmarshal(deltaResponse.Bytes(), &pending); err == nil {
				streamWriter.write("pending", pending)
				continue
			}
		}
		if deltaRecorder.status == http.StatusConflict {
			var repair map[string]any
			if err := json.Unmarshal(deltaResponse.Bytes(), &repair); err == nil {
				// The internal delta call uses the same HTTP handler as external
				// clients, so its conflict is canonically wrapped. SSE keeps
				// Python's legacy repair event shape for the browser instead of
				// leaking an HTTP failure envelope inside the stream.
				if repair["state"] == "failed" {
					legacy := map[string]any{
						"status": "repair_required", "protocol_version": statsWireVersion,
						"reason": "delta cursor is outside the retained exact chain",
					}
					if value, ok := repair["cache_generation"]; ok {
						legacy["cache_generation"] = value
					}
					repair = legacy
				}
				streamWriter.write("repair", repair)
				return
			}
		}
		if deltaRecorder.status != http.StatusOK {
			streamWriter.write("unavailable", statsError("stats delta unavailable"))
			return
		}
		var payload map[string]any
		if err := json.Unmarshal(deltaResponse.Bytes(), &payload); err != nil {
			streamWriter.write("unavailable", statsError("stats delta malformed"))
			return
		}
		streamWriter.write("delta", payload)
		cacheGeneration = int64(payload["cache_generation"].(float64))
		revision = int64(payload["revision"].(float64))
	}
}

type bufferResponseWriter struct {
	header http.Header
	body   *bytes.Buffer
	status int
}

// statsSSEWriter assigns a process-relative monotonic millisecond id to every
// frame, including acknowledgement, heartbeat, and terminal error frames.
type statsSSEWriter struct {
	writer    http.ResponseWriter
	startedAt time.Time
	lastID    int64
}

func (s *statsSSEWriter) write(event string, payload any) {
	data, _ := json.Marshal(payload)
	id := time.Since(s.startedAt).Milliseconds()
	if id <= s.lastID {
		id = s.lastID + 1
	}
	s.lastID = id
	fmt.Fprintf(s.writer, "id: %d\nevent: %s\ndata: %s\n\n", id, event, data)
	if flusher, ok := s.writer.(http.Flusher); ok {
		flusher.Flush()
	}
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
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if s.pricing == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "pricing catalog unavailable"})
		return
	}
	s.pricingRefreshMu.Lock()
	refreshState := map[string]any{
		"status": "idle", "failure_count": 0,
		"next_refresh_at": float64(time.Now().Add(24 * time.Hour).Unix()),
	}
	for key, value := range s.pricingRefreshState {
		refreshState[key] = value
	}
	if s.pricingRefreshing {
		refreshState["status"] = "running"
	}
	s.pricingRefreshMu.Unlock()
	jsonResponse(w, http.StatusOK, statsReadyEnvelope(statsRequestID(r), map[string]any{"catalog": s.pricing.PublicPayload(), "refresh": refreshState}))
}

func (s *server) pricingCatalogRefresh(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if s.pricing == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "pricing catalog unavailable"})
		return
	}
	s.pricingRefreshMu.Lock()
	if s.pricingRefreshing {
		state := map[string]any{"ok": true, "coalesced": true}
		for key, value := range s.pricingRefreshState {
			state[key] = value
		}
		s.pricingRefreshMu.Unlock()
		jsonResponse(w, http.StatusAccepted, state)
		return
	}
	s.pricingRefreshEpoch++
	state := map[string]any{
		"status":     "running",
		"started_at": time.Now().UnixNano() / 1e9,
		"reason":     "manual",
		"key":        "pricing-refresh",
		"epoch":      s.pricingRefreshEpoch,
		"ticket":     fmt.Sprintf("pricing-refresh-%d", s.pricingRefreshEpoch),
	}
	s.pricingRefreshState = state
	s.pricingRefreshing = true
	response := map[string]any{"ok": true, "coalesced": false}
	for key, value := range state {
		response[key] = value
	}
	s.pricingRefreshMu.Unlock()
	go func() {
		err := s.pricing.Refresh()
		s.pricingRefreshMu.Lock()
		s.pricingRefreshing = false
		if err != nil {
			s.pricingRefreshState["status"] = "failed"
			s.pricingRefreshState["error"] = err.Error()
		} else {
			s.pricingRefreshState["status"] = "done"
		}
		s.pricingRefreshMu.Unlock()
	}()
	jsonResponse(w, http.StatusAccepted, response)
}

func (s *server) statsRetry(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if s.statsLifecycle == nil {
		jsonResponse(w, http.StatusFailedDependency, statsError("stats lifecycle unavailable"))
		return
	}
	s.statsLifecycle.NotifyAppend()
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "status": "ready"})
}

func (s *server) ready(w http.ResponseWriter, r *http.Request) {
	if s.statsService == nil || s.statsWriter == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ready": false, "failures": []string{"stats owner unavailable"}})
		return
	}
	if s.statsLifecycle == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ready": false, "failures": []string{"stats materializer unavailable"}})
		return
	}
	counters := s.statsLifecycle.Counters()
	serviceState := s.statsService.State()
	pending := s.statsWriter.PendingCount()
	pendingCells := []map[string]any{}
	if pending > 0 {
		if cells, err := s.statsWriter.PendingInvalidationCells(r.Context()); err == nil {
			if len(cells) > 64 {
				cells = cells[:64]
			}
			pendingCells = make([]map[string]any, 0, len(cells))
			for _, cell := range cells {
				pendingCells = append(pendingCells, map[string]any{
					"resolution_seconds": cell.ResolutionSeconds,
					"bucket_start":       cell.BucketStart,
					"source_generation":  cell.SourceGeneration,
				})
			}
		}
	}
	ready := counters.Runs > 0 && (counters.LastRunSucceeded || counters.Errors == 0) && pending == 0 && serviceState.Ready
	status := http.StatusOK
	if !ready {
		status = http.StatusServiceUnavailable
	}
	failures := []string{}
	if counters.Runs == 0 {
		failures = append(failures, "stats materializer has not completed a run")
	}
	if counters.Runs > 0 && counters.Errors > 0 && !counters.LastRunSucceeded {
		if counters.LastRunError != "" {
			failures = append(failures, "stats lifecycle last run failed: "+counters.LastRunError)
		} else {
			failures = append(failures, "stats lifecycle last run failed")
		}
	}
	if pending > 0 {
		failures = append(failures, "stats ring has pending invalidations")
	} else if pending < 0 {
		failures = append(failures, "stats ring pending state unavailable")
		ready = false
	}
	if serviceState.PendingProducts > 0 {
		failures = append(failures, "stats service has pending products")
	}
	if serviceState.LastError != "" {
		failures = append(failures, "stats service refresh failed: "+serviceState.LastError)
	}
	jsonResponse(w, status, map[string]any{"ready": ready, "source_generation": serviceState.SourceGeneration, "cache_generation": serviceState.CacheGeneration, "pending_cells": maxInt(pending, 0), "pending_cell_details": pendingCells, "lifecycle_runs": counters.Runs, "lifecycle_publications": counters.Publications, "lifecycle_repairs": counters.Repairs, "lifecycle_errors": counters.Errors, "lifecycle_last_run_succeeded": counters.LastRunSucceeded, "lifecycle_last_run_error": counters.LastRunError, "migration_state": "ready", "materializer_state": func() string {
		if counters.Runs > 0 && !counters.LastRunSucceeded {
			return "error"
		}
		if counters.Runs == 0 {
			return "pending"
		}
		return "ready"
	}(), "ring_failure": serviceState.LastError, "failures": failures})
}
