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
	"path/filepath"
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
	// Match Python's 1 MiB chunk target while allowing one indivisible bucket
	// up to the local-RPC binary response bound. A bucket's no-data ledger can
	// exceed the target even after every other bucket has been split away.
	statsSnapshotChunkTargetBytes = stats.MaxStatsSnapshotChunkTargetBytes
	statsSnapshotChunkMaxBytes    = stats.MaxStatsSnapshotChunkBytes
	statsMaxSnapshotChunks        = stats.MaxStatsSnapshotChunks
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

// statsRuntimeDiagnostics is a bounded owner-level view of the work that can
// account for Go CPU. It intentionally reports counters and elapsed time, not
// raw rows or payloads, so it is safe to expose through system-status.
func (s *server) statsRuntimeDiagnostics() map[string]any {
	payload := map[string]any{"schema_version": 1, "memory": s.runtimeMemory.diagnostics()}
	s.processEntriesCacheMu.Lock()
	processEntriesRows := len(s.processEntriesCache)
	processEntriesHits := s.processEntriesCacheHits
	processEntriesMisses := s.processEntriesCacheMisses
	processEntriesAt := s.processEntriesCacheAt
	processEntriesLastDuration := s.processEntriesCacheLastDuration
	processEntriesDurationTotal := s.processEntriesCacheDurationTotal
	s.processEntriesCacheMu.Unlock()
	s.processMetricsCacheMu.Lock()
	processMetricsHits := s.processMetricsCacheHits
	processMetricsMisses := s.processMetricsCacheMisses
	processMetricsLastDuration := s.processMetricsLastDuration
	processMetricsDurationTotal := s.processMetricsDurationTotal
	processMetricsThreadRows := s.processMetricsLastThreadRows
	processMetricsChildRows := s.processMetricsLastChildRows
	s.processMetricsCacheMu.Unlock()
	s.agentInventoryMu.Lock()
	agentInventoryCacheHits := s.agentInventoryCacheHits
	agentInventoryCacheMisses := s.agentInventoryCacheMisses
	agentInventoryLastDuration := s.agentInventoryLastDuration
	agentInventoryDurationTotal := s.agentInventoryDurationTotal
	agentInventoryRows := len(s.agentInventoryRecords)
	agentInventoryAt := s.agentInventoryAt
	s.agentInventoryMu.Unlock()
	var processEntriesAtValue any
	if !processEntriesAt.IsZero() {
		processEntriesAtValue = unixSeconds(processEntriesAt)
	}
	rosterScope, rosterReader := "go_descendants", "proc_children"
	if s.childProcessEntriesFn == nil {
		rosterScope, rosterReader = "fallback_process_roster", "process_entries"
	}
	payload["process_diagnostics"] = map[string]any{
		"roster_scope":                      rosterScope,
		"roster_reader":                     rosterReader,
		"roster_cache_ttl_ms":               goProcessEntriesCacheTTL.Seconds() * 1000,
		"roster_cache_hits":                 processEntriesHits,
		"roster_cache_misses":               processEntriesMisses,
		"last_roster_rows":                  processEntriesRows,
		"last_roster_at":                    processEntriesAtValue,
		"last_roster_duration_ms":           processEntriesLastDuration.Seconds() * 1000,
		"roster_duration_total_ms":          processEntriesDurationTotal.Seconds() * 1000,
		"metrics_cache_ttl_ms":              goProcessMetricsCacheTTL.Seconds() * 1000,
		"metrics_cache_hits":                processMetricsHits,
		"metrics_cache_misses":              processMetricsMisses,
		"last_metrics_duration_ms":          processMetricsLastDuration.Seconds() * 1000,
		"metrics_duration_total_ms":         processMetricsDurationTotal.Seconds() * 1000,
		"last_thread_rows":                  processMetricsThreadRows,
		"last_child_rows":                   processMetricsChildRows,
		"agent_inventory_cache_ttl_ms":      goAgentInventoryCacheTTL.Seconds() * 1000,
		"agent_inventory_cache_hits":        agentInventoryCacheHits,
		"agent_inventory_cache_misses":      agentInventoryCacheMisses,
		"agent_inventory_last_duration_ms":  agentInventoryLastDuration.Seconds() * 1000,
		"agent_inventory_duration_total_ms": agentInventoryDurationTotal.Seconds() * 1000,
		"last_agent_inventory_rows":         agentInventoryRows,
		"last_agent_inventory_at": func() any {
			if agentInventoryAt.IsZero() {
				return nil
			}
			return unixSeconds(agentInventoryAt)
		}(),
	}
	if s.statsLifecycle != nil {
		counters := s.statsLifecycle.Counters()
		byResolution := make([]map[string]any, 0, len(counters.ByResolution))
		resolutions := make([]int, 0, len(counters.ByResolution))
		for resolution := range counters.ByResolution {
			resolutions = append(resolutions, resolution)
		}
		sort.Ints(resolutions)
		for _, resolution := range resolutions {
			value := counters.ByResolution[resolution]
			byResolution = append(byResolution, map[string]any{
				"resolution_seconds": resolution,
				"selection_runs":     value.SelectionRuns, "materialization_runs": value.MaterializationRuns,
				"selected_cells": value.SelectedCells, "materialized_buckets": value.MaterializedBuckets,
				"fact_rows_read": value.FactRowsRead, "decoded_bytes": value.DecodedBytes,
				"selection_duration_ms":       value.SelectionDuration.Seconds() * 1000,
				"materialization_duration_ms": value.MaterializationDuration.Seconds() * 1000,
			})
		}
		payload["lifecycle"] = map[string]any{
			"runs": counters.Runs, "publications": counters.Publications, "repairs": counters.Repairs, "errors": counters.Errors,
			"background_backfill_deferrals": counters.BackgroundBackfillDeferrals,
			"startup_preparation":           statsStartupPreparationWire(counters.StartupPreparation),
			"selected_cells":                counters.SelectedCells, "materialized_buckets": counters.MaterializedBuckets,
			"fact_rows_read": counters.FactRowsRead, "decoded_bytes": counters.DecodedBytes,
			"selection_duration_ms":       counters.SelectionDuration.Seconds() * 1000,
			"materialization_duration_ms": counters.MaterializationDuration.Seconds() * 1000,
			"by_resolution":               byResolution,
		}
		refresh := s.statsService.RefreshCounters()
		payload["service_refresh"] = map[string]any{
			"runs": refresh.Runs, "errors": refresh.Errors, "ring_reads": refresh.RingReads,
			"ring_rows": refresh.RingRows, "bucket_decodes": refresh.BucketDecodes,
			"decoded_bytes": refresh.DecodedBytes, "products_built": refresh.ProductsBuilt,
			"duration_total_ms": refresh.DurationTotal.Seconds() * 1000,
			"last_duration_ms":  refresh.LastDuration.Seconds() * 1000,
		}
	}
	collectors := map[string]any{}
	if s.hostCollector != nil {
		counters := s.hostCollector.Counters()
		families := map[string]any{}
		for name, timing := range counters.Family {
			families[name] = map[string]any{
				"runs": timing.Runs, "duration_total_ms": timing.DurationTotal.Seconds() * 1000, "last_duration_ms": timing.LastDuration.Seconds() * 1000,
				"source_duration_total_ms": timing.SourceDurationTotal.Seconds() * 1000, "last_source_duration_ms": timing.LastSourceDuration.Seconds() * 1000,
			}
		}
		collectors["host"] = map[string]any{
			"cycles": counters.Cycles, "duration_total_ms": counters.DurationTotal.Seconds() * 1000, "last_duration_ms": counters.LastDuration.Seconds() * 1000,
			// These row counts are deliberately reported beside family timing. A
			// slow CPU sample is otherwise indistinguishable from a slow SQLite
			// append, while a large process census explains the /proc work that can
			// consume more than one logical CPU on a busy host.
			"last_cpu_process_rows": counters.LastCPUProcessRows, "last_memory_process_rows": counters.LastMemoryProcessRows,
			"families": families,
		}
	}
	if s.agentStatusCollector != nil {
		counters := s.agentStatusCollector.Counters()
		collectors["agent_status"] = map[string]any{"cycles": counters.Cycles, "duration_total_ms": counters.DurationTotal.Seconds() * 1000, "last_duration_ms": counters.LastDuration.Seconds() * 1000, "accepted_observations": counters.AcceptedObservations, "unavailable_spans": counters.UnavailableSpans, "errors": counters.Errors}
	}
	if s.tokenCollector != nil {
		counters := s.tokenCollector.Counters()
		openCodePhases := make(map[string]any, len(counters.OpenCodePhases))
		for name, timing := range counters.OpenCodePhases {
			openCodePhases[name] = map[string]any{
				"runs": timing.Runs, "last_duration_ms": timing.Last.Seconds() * 1000, "duration_total_ms": timing.Total.Seconds() * 1000,
			}
		}
		collectors["agent_tokens"] = map[string]any{
			"cycles": counters.Cycles, "duration_total_ms": counters.DurationTotal.Seconds() * 1000, "last_duration_ms": counters.LastDuration.Seconds() * 1000,
			"usage_accepted": counters.UsageAccepted, "coverage_accepted": counters.CoverageAccepted, "unavailable_spans": counters.UnavailableAccepted, "errors": counters.Errors,
			"opencode_database_opens": counters.OpenCodeDatabaseOpens, "opencode_database_reuses": counters.OpenCodeDatabaseReuses,
			"opencode_database_last_open_duration_ms":  counters.OpenCodeDatabaseLastOpen.Seconds() * 1000,
			"opencode_database_open_duration_total_ms": counters.OpenCodeDatabaseOpenTotal.Seconds() * 1000,
			"opencode_collection_last_duration_ms":     counters.OpenCodeCollectionLast.Seconds() * 1000,
			"opencode_collection_duration_total_ms":    counters.OpenCodeCollectionTotal.Seconds() * 1000,
			"opencode_phases":                          openCodePhases,
		}
	}
	payload["collectors"] = collectors
	if s.statsRuntimeRecorder != nil {
		payload["runtime_recorder"] = s.statsRuntimeRecorder.diagnostics()
	}
	return payload
}

func statsStartupPreparationWire(state stats.StartupPreparationState) map[string]any {
	return map[string]any{"started_at": state.StartedAt, "horizon": state.Horizon, "source_generation": state.SourceGeneration,
		"total_cells": state.TotalCells, "pending_cells": state.PendingCells, "complete": state.Complete,
		"timed_out": state.TimedOut, "duration_ms": state.Duration.Seconds() * 1000}
}

// statsRuntimeLoad reports collector occupancy against each owner's native
// cadence. The values describe elapsed collector work, including waits and
// I/O; they are not process CPU percentages.
func (s *server) statsRuntimeLoad() map[string]any {
	load := make(map[string]any)
	add := func(key, label string, duration, cadence time.Duration) {
		if duration <= 0 || cadence <= 0 {
			return
		}
		percent := duration.Seconds() / cadence.Seconds() * 100
		if math.IsNaN(percent) || math.IsInf(percent, 0) || percent < 0 {
			return
		}
		load[key] = map[string]any{
			"label": label, "load_percent": percent,
			"duration_ms": duration.Seconds() * 1000, "cadence_ms": cadence.Seconds() * 1000,
			"unit": "runtime_occupancy",
		}
	}
	if s.statsLifecycle != nil {
		counters := s.statsLifecycle.Counters()
		cadence := s.statsLifecycle.Cadence()
		add("stats_lifecycle", "stats lifecycle", counters.LastDuration, cadence)
	}
	if s.hostCollector != nil {
		counters := s.hostCollector.Counters()
		labels := map[string]string{
			"cpu":           "host CPU collector (runtime occupancy)",
			"system_memory": "host memory collector (runtime occupancy)",
			"gpu":           "host GPU collector (runtime occupancy)",
		}
		for family, timing := range counters.Family {
			label, ok := labels[family]
			if !ok || !s.hostCollector.FamilyDemanded(family) {
				continue
			}
			add("host_collector:"+family, label, timing.LastDuration, s.hostCollector.FamilyCadence(family))
		}
	}
	if s.tokenCollector != nil {
		counters := s.tokenCollector.Counters()
		cadence := counters.LastCadence
		if cadence <= 0 {
			cadence = stats.AgentTokenActiveCadence
		}
		add("agent_tokens", "agent token collector", counters.LastDuration, cadence)
	}
	if s.agentStatusCollector != nil {
		counters := s.agentStatusCollector.Counters()
		cadence := counters.LastCadence
		if cadence <= 0 {
			cadence = 10 * time.Second
		}
		add("agent_status", "agent status collector", counters.LastDuration, cadence)
	}
	return load
}

func (s *server) statsRuntimeServiceLoadSamples(at time.Time) []stats.ServiceLoadSample {
	s.runtimeMemory.sample(at)
	load := s.statsRuntimeLoad()
	samples := make([]stats.ServiceLoadSample, 0, len(load)+4)
	for key, raw := range load {
		record, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		percent, ok := record["load_percent"].(float64)
		if !ok || math.IsNaN(percent) || math.IsInf(percent, 0) || percent < 0 {
			continue
		}
		cpu := percent
		samples = append(samples, stats.ServiceLoadSample{SourceID: "runtime:" + key, Running: true, CPUPercent: &cpu})
	}
	var process goProcessMetrics
	if s.processMetricsFn != nil {
		process = s.processMetricsFn(at)
	} else {
		process = s.readGoProcessMetrics(at)
	}
	processSample := stats.ServiceLoadSample{SourceID: s.statsMainProcessSourceID(), Running: true}
	if validServiceLoadPercent(process.CPUPercent) {
		processSample.CPUPercent = process.CPUPercent
	}
	if process.RSSBytes != nil {
		rss := float64(*process.RSSBytes)
		processSample.RSSBytes = &rss
	}
	if processSample.CPUPercent != nil || processSample.RSSBytes != nil {
		samples = append(samples, processSample)
	}
	seenThreads := make(map[string]bool)
	for _, thread := range process.Threads {
		if thread.TID <= 0 || !validServiceLoadPercent(thread.CPUPercent) || thread.State == "dead" || thread.State == "zombie" {
			continue
		}
		key := goProcessInstanceKey(thread.TID, thread.Identity)
		if seenThreads[key] {
			continue
		}
		seenThreads[key] = true
		samples = append(samples, stats.ServiceLoadSample{
			SourceID: goMetricSourceID("thread", thread.TID, thread.Identity), Running: true, CPUPercent: thread.CPUPercent,
		})
	}
	for _, child := range normalizeGoChildProcessMetrics(process.ChildProcesses) {
		if child.State == "dead" || child.State == "zombie" {
			continue
		}
		childSample := stats.ServiceLoadSample{SourceID: goMetricSourceID("child", child.PID, child.Identity), Running: true}
		if validServiceLoadPercent(child.CPUPercent) {
			childSample.CPUPercent = child.CPUPercent
		}
		if child.RSSBytes != nil {
			rss := float64(*child.RSSBytes)
			childSample.RSSBytes = &rss
		}
		if childSample.CPUPercent != nil || childSample.RSSBytes != nil {
			samples = append(samples, childSample)
		}
	}
	return samples
}

// Keep identity and display metadata in the persisted source key: historical
// samples must remain attributable after the process or its owner disappears.
func (s *server) statsMainProcessSourceID() string {
	s.statsProcessSourceOnce.Do(func() {
		hostname, _ := os.Hostname()
		host := readIdentityValue("YOLOMUX_HOST_ID", "/etc/machine-id", hostname)
		boot := readIdentityValue("", "/proc/sys/kernel/random/boot_id", "unknown-boot")
		start := processStartIdentity(os.Getpid())
		owner := s.config + "\x00" + s.root + "\x00" + strconv.Itoa(s.port)
		if s.background != nil {
			host, boot, start, owner = s.background.stableHostID, s.background.bootID, s.background.processStartIdentity, s.background.serverID
		}
		// The shared owner also lacks /proc start metadata on platforms such
		// as macOS. Apply the fallback after reading it to separate PID reuse.
		if start == "" {
			start = strconv.FormatInt(s.startedAt.UnixNano(), 10)
		}
		executable, _ := os.Executable()
		s.statsProcessSource = statsProcessSourceID(os.Getpid(), s.port, os.Getppid(), executable, host, boot, start, owner)
	})
	return s.statsProcessSource
}

func statsProcessSourceID(pid, port, parentPID int, executable, host, boot, start, owner string) string {
	// Source IDs have a 192-byte storage limit. Hash complete identity fields,
	// while retaining bounded human-readable process details in the prefix.
	identity, _ := json.Marshal([]string{host, boot, strconv.Itoa(pid), start, owner, executable})
	digest := sha256.Sum256(identity)
	name := filepath.Base(executable)
	if name == "." || name == "" {
		name = "yolomux-go"
	}
	for len(url.QueryEscape(name)) > 64 {
		_, width := utf8.DecodeLastRuneInString(name)
		name = name[:len(name)-width]
	}
	return fmt.Sprintf("yolomux-go:process:%d:%d:%d:%s:h%s", pid, port, parentPID, url.QueryEscape(name), hex.EncodeToString(digest[:16]))
}

func validServiceLoadPercent(value *float64) bool {
	return value != nil && !math.IsNaN(*value) && !math.IsInf(*value, 0) && *value >= 0
}

func goMetricSourceID(kind string, id int, identity string) string {
	base := fmt.Sprintf("yolomux-go:%s:%d", kind, id)
	identity = strings.TrimSpace(identity)
	if identity == "" {
		return base
	}
	prefix := strconv.Itoa(id) + ":"
	if strings.HasPrefix(identity, prefix) {
		identity = strings.TrimPrefix(identity, prefix)
	}
	for _, r := range identity {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_') {
			digest := sha256.Sum256([]byte(identity))
			return base + ":h" + hex.EncodeToString(digest[:6])
		}
	}
	return base + ":" + identity
}

func goProcessInstanceKey(pid int, identity string) string {
	identity = strings.TrimSpace(identity)
	if identity == "" {
		return strconv.Itoa(pid)
	}
	return strconv.Itoa(pid) + "\x1f" + identity
}

func normalizeGoChildProcessMetrics(children []goChildProcessMetric) []goChildProcessMetric {
	result := make([]goChildProcessMetric, 0, len(children))
	indexes := make(map[string]int, len(children))
	for _, child := range children {
		if child.PID <= 0 {
			continue
		}
		child.Identity = strings.TrimSpace(child.Identity)
		key := goProcessInstanceKey(child.PID, child.Identity)
		index, exists := indexes[key]
		if !exists {
			indexes[key] = len(result)
			result = append(result, child)
			continue
		}
		current := &result[index]
		if !validServiceLoadPercent(current.CPUPercent) && validServiceLoadPercent(child.CPUPercent) {
			current.CPUPercent = child.CPUPercent
		}
		if current.RSSBytes == nil && child.RSSBytes != nil {
			current.RSSBytes = child.RSSBytes
		}
		if current.State == "" || current.State == "dead" || current.State == "zombie" {
			current.State = child.State
		}
	}
	return result
}

func runtimeWireTimestamp(value any) (float64, bool) {
	switch timestamp := value.(type) {
	case float64:
		return timestamp, !math.IsNaN(timestamp) && !math.IsInf(timestamp, 0)
	case int:
		return float64(timestamp), true
	case int64:
		return float64(timestamp), true
	default:
		return 0, false
	}
}

func augmentSnapshotWireRuntimeLoad(wire map[string]any, load map[string]any) map[string]any {
	buckets, ok := wire["buckets"].([]map[string]any)
	if !ok || len(buckets) == 0 {
		return wire
	}
	liveBucket := buckets[len(buckets)-1]
	if liveBucket["open"] != true {
		return wire
	}
	series, ok := liveBucket["series"].(map[string]map[string]any)
	if !ok {
		return wire
	}
	sampledAt, ok := runtimeWireTimestamp(wire["generated_at"])
	if !ok || sampledAt < 0 {
		sampledAt = float64(time.Now().Unix())
	}
	source, _ := liveBucket["source"].(map[string]any)
	if source == nil {
		source = map[string]any{"count": 0, "first_timestamp": nil, "last_timestamp": nil}
		liveBucket["source"] = source
	}
	originalFirst, originalLast, originalCount := source["first_timestamp"], source["last_timestamp"], source["count"]
	first, firstOK := runtimeWireTimestamp(originalFirst)
	last, lastOK := runtimeWireTimestamp(originalLast)
	if !firstOK || first < 0 || sampledAt < first {
		first = sampledAt
	}
	if !lastOK || last < first || sampledAt > last {
		last = sampledAt
	}
	added := false
	for key, raw := range load {
		record, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		value, ok := record["load_percent"].(float64)
		if !ok || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
			continue
		}
		for _, prefix := range []string{"service_cpu_percent:runtime:", "service_cpu_min_percent:runtime:", "service_cpu_max_percent:runtime:"} {
			series[prefix+key] = map[string]any{
				"value": value, "source_count": 1,
				"first_timestamp": sampledAt, "last_timestamp": sampledAt,
			}
		}
		added = true
	}
	if !added {
		return wire
	}
	source["first_timestamp"], source["last_timestamp"] = first, last
	switch count := originalCount.(type) {
	case int:
		source["count"] = count + 1
	case int64:
		source["count"] = count + 1
	case float64:
		source["count"] = int(count) + 1
	default:
		source["count"] = 1
	}
	return wire
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
	if err := s.demandStatsProduct(query); err != nil {
		return stats.StatsSnapshot{}, err
	}
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

func (s *server) currentStatsSnapshotIfChanged(ctx context.Context, query statsQuery) (stats.StatsSnapshot, bool, error) {
	if err := s.demandStatsProduct(query); err != nil {
		return stats.StatsSnapshot{}, false, err
	}
	return s.statsService.CurrentSnapshotIfChanged(ctx, query.rangeSeconds, query.resolution, query.afterCacheGeneration)
}

func (s *server) demandStatsProduct(query statsQuery) error {
	if s.statsService == nil {
		return fmt.Errorf("%w: stats service is not initialized", stats.ErrProductPending)
	}
	if s.statsLifecycle != nil {
		// A pending HTTP read is still a demand signal. The lifecycle uses it to
		// prioritize this exact product over unrelated cold-ring repair work;
		// materialization remains fully off the request path.
		s.statsLifecycle.DemandProduct(query.rangeSeconds, query.resolution)
	}
	return nil
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
	// A generation descriptor is already owned by the service; seeding delivery
	// metadata never reconstructs its wire body, including unchanged requests.
	seedKey := statsDeltaKey{rangeSeconds: query.rangeSeconds, resolution: query.resolution}
	seed := func() { s.statsDeliveryRevision(seedKey, snapshot.CacheGeneration, true) }
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
	result := s.statsDeltaOperation(r.Context(), query)
	payload := result.payload
	if result.status == http.StatusConflict {
		payload = statsHTTPErrorEnvelope(r, result.status, payload)
	}
	jsonResponse(w, result.status, payload)
}

// statsOperationResult carries transport state separately from the JSON payload.
// HTTP and SSE apply their own framing without parsing an internal HTTP response.
type statsOperationResult struct {
	status               int
	generation, revision int64
	payload              map[string]any
}

func (s *server) statsDeltaOperation(ctx context.Context, query statsQuery) statsOperationResult {
	snapshot, changed, err := s.currentStatsSnapshotIfChanged(ctx, query)
	if err != nil {
		if errors.Is(err, stats.ErrProductPending) {
			return statsOperationResult{status: http.StatusAccepted, payload: statsPending(query, err.Error())}
		}
		return statsOperationResult{status: http.StatusFailedDependency, payload: statsError(err.Error())}
	}
	if !changed {
		generation := query.afterCacheGeneration
		key := statsDeltaKey{rangeSeconds: query.rangeSeconds, resolution: query.resolution}
		revision := s.statsDeliveryRevision(key, generation, false)
		return statsOperationResult{status: http.StatusNotModified, generation: generation, revision: revision, payload: map[string]any{"protocol_version": statsWireVersion, "not_modified": true, "cache_generation": generation, "revision": revision}}
	}
	generation := snapshot.CacheGeneration
	key := statsDeltaKey{rangeSeconds: query.rangeSeconds, resolution: query.resolution}
	revision := s.statsDeliveryRevision(key, generation, false)
	s.statsDeltaMu.Lock()
	_, delivered := s.statsDeltaRevisions[key][query.afterCacheGeneration]
	s.statsDeltaMu.Unlock()
	previous, retained := s.statsService.GenerationSnapshot(ctx, query.rangeSeconds, query.resolution, query.afterCacheGeneration)
	if !delivered || !retained {
		return statsOperationResult{status: http.StatusConflict, generation: generation, revision: revision, payload: map[string]any{
			"status": "repair_required", "protocol_version": statsWireVersion,
			"cache_generation": generation, "reason": "delta cursor is outside the retained exact chain",
		}}
	}
	previousWire, currentWire := statsDeltaWirePair(previous, snapshot, query)
	currentWire["revision"] = revision
	return statsOperationResult{status: http.StatusOK, generation: generation, revision: revision,
		payload: deltaFromWireMaps(previousWire, currentWire, query)}
}

// Only delivery revisions live in HTTP. Bucket and coverage history belongs to
// the service, where generations share immutable products instead of wire maps.
func (s *server) statsDeliveryRevision(key statsDeltaKey, generation int64, seed bool) int64 {
	s.statsDeltaMu.Lock()
	defer s.statsDeltaMu.Unlock()
	if s.statsDeltaRevisions == nil {
		s.statsDeltaRevisions = make(map[statsDeltaKey]map[int64]int64)
	}
	if s.statsDeltaNextRevision == nil {
		s.statsDeltaNextRevision = make(map[statsDeltaKey]int64)
	}
	history := s.statsDeltaRevisions[key]
	if history == nil {
		history = make(map[int64]int64)
		s.statsDeltaRevisions[key] = history
	}
	if revision, exists := history[generation]; exists {
		return revision
	}
	revision := s.statsDeltaNextRevision[key]
	if seed {
		revision = 0
	}
	history[generation] = revision
	if s.statsDeltaNextRevision[key] <= revision {
		s.statsDeltaNextRevision[key] = revision + 1
	}
	if len(history) > statsDeltaHistoryLimit {
		oldest := generation
		for candidate := range history {
			if candidate < oldest {
				oldest = candidate
			}
		}
		delete(history, oldest)
	}
	return revision
}

// statsDeltaWirePair compares immutable typed buckets before expanding only the
// changed or removed buckets into wire maps. Cost totals still cover the complete
// current product. Both sides include the same selected identities, so unchanged
// buckets cannot be mistaken for tombstones by the shared delta projector.
func statsDeltaWirePair(previous, current stats.StatsSnapshot, query statsQuery) (map[string]any, map[string]any) {
	type bucketKey struct{ start, duration float64 }
	old := make(map[bucketKey]int, len(previous.Materialized.Buckets))
	selected := make(map[bucketKey]bool)
	for index, bucket := range previous.Materialized.Buckets {
		old[bucketKey{bucket.Start, bucket.Duration}] = index
	}
	for index, bucket := range current.Materialized.Buckets {
		key := bucketKey{bucket.Start, bucket.Duration}
		oldIndex, exists := old[key]
		if !exists {
			selected[key] = true
			continue
		}
		before := previous.Materialized.Buckets[oldIndex]
		oldOpen := oldIndex == len(previous.Materialized.Buckets)-1 || !before.Complete
		newOpen := index == len(current.Materialized.Buckets)-1 || !bucket.Complete
		if before.SourceCount != bucket.SourceCount || oldOpen != newOpen || !reflect.DeepEqual(before.Series, bucket.Series) {
			selected[key] = true
		}
		delete(old, key)
	}
	for key := range old {
		selected[key] = true
	}
	// The public delta protocol repeats the rightmost bucket when a publication
	// changes neither bucket nor coverage data. Include it in both comparisons.
	if len(selected) == 0 && len(current.Materialized.Buckets) > 0 {
		last := current.Materialized.Buckets[len(current.Materialized.Buckets)-1]
		selected[bucketKey{last.Start, last.Duration}] = true
	}
	include := func(bucket stats.MaterializedBucket) bool { return selected[bucketKey{bucket.Start, bucket.Duration}] }
	return snapshotWireSelected(previous, query, include), snapshotWireSelected(current, query, include)
}

func snapshotWire(snapshot stats.StatsSnapshot, query statsQuery) map[string]any {
	return snapshotWireSelected(snapshot, query, nil)
}

func snapshotWireSelected(snapshot stats.StatsSnapshot, query statsQuery, include func(stats.MaterializedBucket) bool) map[string]any {
	// Ring-backed products intentionally omit the raw observation projection;
	// the public wire is defined by the immutable materialized bucket set.
	buckets := make([]map[string]any, 0, len(snapshot.Materialized.Buckets))
	for i, bucket := range snapshot.Materialized.Buckets {
		if include != nil && !include(bucket) {
			continue
		}
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
		buckets = append(buckets, map[string]any{"start": bucket.Start, "duration": bucket.Duration, "series": series, "source": map[string]any{"count": bucket.SourceCount, "first_timestamp": firstTimestamp, "last_timestamp": lastTimestamp}, "open": i == len(snapshot.Materialized.Buckets)-1 || !bucket.Complete})
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
	if query.hasChunkIndex && query.chunkGeneration != snapshot.CacheGeneration {
		return nil, 0, errSnapshotChunkGenerationAdvanced
	}
	payloads, err := snapshotPayloadsForQuery(snapshot, query)
	if err != nil {
		return nil, 0, err
	}
	if query.hasChunkIndex {
		if query.chunkIndex < 0 || query.chunkIndex >= len(payloads) {
			return nil, 0, errors.New("chunk_index lies outside the retained snapshot batch")
		}
		return payloads[query.chunkIndex], len(payloads), nil
	}
	return payloads[0], len(payloads), nil
}

func snapshotPayloadsForQuery(snapshot stats.StatsSnapshot, query statsQuery) ([]map[string]any, error) {
	return snapshotPayloadsForWire(snapshotWire(snapshot, query), query)
}

func snapshotPayloadsForWire(full map[string]any, query statsQuery) ([]map[string]any, error) {
	encodedSize, err := statsWireSize(full)
	if err != nil {
		return nil, fmt.Errorf("encode stats snapshot: %w", err)
	}
	buckets, ok := full["buckets"].([]map[string]any)
	if !ok {
		return nil, errors.New("stats snapshot buckets are malformed")
	}
	chunked := query.hasChunkIndex || encodedSize > statsSnapshotChunkTargetBytes
	if !chunked {
		return []map[string]any{full}, nil
	}
	if len(buckets) == 0 {
		return nil, errors.New("snapshot cannot be split into chunks")
	}
	// A single bucket cannot be divided by time. Keep an oversized-but-valid
	// one-bucket snapshot as one transport payload; the wire limit, rather than
	// the smaller chunk target, is the applicable bound when no bucket split is
	// possible.
	if len(buckets) == 1 && encodedSize <= statsSnapshotChunkMaxBytes && !query.hasChunkIndex {
		return []map[string]any{full}, nil
	}
	chunkLimit := len(buckets)
	if chunkLimit > statsMaxSnapshotChunks {
		chunkLimit = statsMaxSnapshotChunks
	}
	chunkCount := 2
	if estimate := (encodedSize + statsSnapshotChunkTargetBytes - 1) / statsSnapshotChunkTargetBytes; estimate > chunkCount {
		chunkCount = estimate
	}
	if chunkCount > chunkLimit {
		chunkCount = chunkLimit
	}
	if len(buckets) == 1 {
		chunk, chunkErr := singleBucketSnapshotChunkWire(full)
		if chunkErr != nil {
			return nil, chunkErr
		}
		fragments, fragmentErr := splitSnapshotChunkNoData(chunk)
		if fragmentErr != nil {
			return nil, fragmentErr
		}
		if len(fragments) < 2 || len(fragments) > statsMaxSnapshotChunks {
			return nil, errors.New("single-bucket snapshot cannot be split within the transport chunk count limit")
		}
		for index, fragment := range fragments {
			fragment["chunk_index"], fragment["chunk_count"] = index, len(fragments)
		}
		return fragments, nil
	}
	for {
		chunks := make([]map[string]any, 0, chunkCount)
		largest := 0
		for index := 0; index < chunkCount; index++ {
			chunk, chunkErr := snapshotChunkWire(full, index, chunkCount)
			if chunkErr != nil {
				return nil, chunkErr
			}
			chunkSize, marshalErr := statsWireSize(chunk)
			if marshalErr != nil {
				return nil, fmt.Errorf("encode stats snapshot chunk: %w", marshalErr)
			}
			if chunkSize > largest {
				largest = chunkSize
			}
			chunks = append(chunks, chunk)
		}
		if largest <= statsSnapshotChunkMaxBytes {
			for index, chunk := range chunks {
				chunk["chunk_index"], chunk["chunk_count"] = index, len(chunks)
			}
			return chunks, nil
		}
		if chunkCount == chunkLimit {
			fragmented := make([]map[string]any, 0, chunkCount+1)
			for _, chunk := range chunks {
				encodedChunkSize, marshalErr := statsWireSize(chunk)
				if marshalErr != nil {
					return nil, fmt.Errorf("encode stats snapshot chunk: %w", marshalErr)
				}
				if encodedChunkSize <= statsSnapshotChunkMaxBytes {
					fragmented = append(fragmented, chunk)
					continue
				}
				chunkBuckets, bucketOK := chunk["buckets"].([]map[string]any)
				if !bucketOK || len(chunkBuckets) != 1 {
					return nil, errors.New("stats snapshot chunk exceeds the transport limit after bucket partitioning")
				}
				fragments, fragmentErr := splitSnapshotChunkNoData(chunk)
				if fragmentErr != nil {
					return nil, fragmentErr
				}
				fragmented = append(fragmented, fragments...)
			}
			if len(fragmented) > statsMaxSnapshotChunks {
				return nil, errors.New("stats snapshot exceeds the transport chunk count limit")
			}
			for index, chunk := range fragmented {
				chunk["chunk_index"], chunk["chunk_count"] = index, len(fragmented)
			}
			return fragmented, nil
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

func singleBucketSnapshotChunkWire(full map[string]any) (map[string]any, error) {
	buckets, ok := full["buckets"].([]map[string]any)
	if !ok || len(buckets) != 1 {
		return nil, errors.New("single-bucket snapshot bounds are invalid")
	}
	resolution, ok := full["resolution_seconds"].(int)
	if !ok || resolution <= 0 {
		return nil, errors.New("single-bucket snapshot resolution is invalid")
	}
	start, ok := buckets[0]["start"].(float64)
	if !ok {
		return nil, errors.New("single-bucket snapshot start is invalid")
	}
	chunk := make(map[string]any, len(full)+2)
	for key, value := range full {
		if key != "chunk_index" && key != "chunk_count" && key != "chunk_start" && key != "chunk_end" {
			chunk[key] = value
		}
	}
	chunk["chunk_start"], chunk["chunk_end"] = start, start+float64(resolution)
	return chunk, nil
}

func splitSnapshotChunkNoData(chunk map[string]any) ([]map[string]any, error) {
	spans, ok := chunk["no_data"].([]map[string]any)
	if !ok || len(spans) < 2 {
		return nil, errors.New("one stats snapshot bucket exceeds the transport limit")
	}
	base := make(map[string]any, len(chunk))
	for key, value := range chunk {
		if key != "no_data" {
			base[key] = value
		}
	}
	base["no_data"] = []map[string]any{}
	baseSize, err := statsWireSize(base)
	if err != nil {
		return nil, fmt.Errorf("encode stats snapshot fragment: %w", err)
	}
	const fragmentMetadataReserve = 32
	available := statsSnapshotChunkMaxBytes - baseSize - fragmentMetadataReserve
	if available <= 2 {
		return nil, errors.New("stats snapshot bucket leaves no room for no-data fragments")
	}
	fragments := make([]map[string]any, 0, 2)
	current := make([]map[string]any, 0)
	currentBytes := 2
	flush := func() {
		fragment := make(map[string]any, len(base)+1)
		for key, value := range base {
			fragment[key] = value
		}
		fragment["no_data"] = current
		fragments = append(fragments, fragment)
		current = make([]map[string]any, 0)
		currentBytes = 2
	}
	for _, span := range spans {
		spanSize, marshalErr := statsWireSize(span)
		if marshalErr != nil {
			return nil, fmt.Errorf("encode stats no-data span: %w", marshalErr)
		}
		itemBytes := spanSize
		if len(current) > 0 {
			itemBytes++
		}
		if currentBytes+itemBytes > available {
			flush()
			itemBytes = spanSize
		}
		if currentBytes+itemBytes > available {
			return nil, errors.New("one stats no-data span exceeds the transport limit")
		}
		current = append(current, span)
		currentBytes += itemBytes
	}
	if len(current) > 0 || len(fragments) == 0 {
		flush()
	}
	return fragments, nil
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
	frameWriter, ok := newJSONSSEWriter(w)
	if !ok {
		return
	}
	streamWriter := &statsSSEWriter{stream: frameWriter, startedAt: time.Now()}
	s.statsDemand.Add(1)
	defer s.statsDemand.Add(-1)
	frameWriter.flush()
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
	var snapshotPayloads []map[string]any
	chunkCount := 1
	if !notModified {
		var payloadErr error
		snapshotPayloads, payloadErr = snapshotPayloadsForQuery(snapshot, query)
		if payloadErr != nil {
			streamWriter.write("unavailable", statsError(payloadErr.Error()))
			return
		}
		chunkCount = len(snapshotPayloads)
	}
	streamWriter.write("ack", map[string]any{"cache_generation": snapshot.CacheGeneration, "chunk_count": chunkCount, "not_modified": notModified, "range_seconds": query.rangeSeconds, "requested_resolution": requestedResolution, "resolution_seconds": query.resolution})
	s.statsDeliveryRevision(statsDeltaKey{rangeSeconds: query.rangeSeconds, resolution: query.resolution}, snapshot.CacheGeneration, true)
	if !notModified {
		for index, chunk := range snapshotPayloads {
			streamWriter.write("snapshot", chunk)
			snapshotPayloads[index] = nil
		}
	}
	streamWriter.write("ready", map[string]any{"cache_generation": snapshot.CacheGeneration, "revision": 0})
	cacheGeneration := snapshot.CacheGeneration
	s.writeStatsRuntimeEvent(streamWriter, cacheGeneration)
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
				s.writeStatsRuntimeEvent(streamWriter, cacheGeneration)
				continue
			}
		} else {
			select {
			case <-r.Context().Done():
				return
			case <-ticker.C:
			}
		}
		deltaQuery := query
		deltaQuery.afterCacheGeneration, deltaQuery.afterRevision = cacheGeneration, revision
		result := s.statsDeltaOperation(r.Context(), deltaQuery)
		switch result.status {
		case http.StatusNotModified:
			streamWriter.write("ready", map[string]any{"cache_generation": cacheGeneration, "revision": revision})
			continue
		case http.StatusAccepted:
			streamWriter.write("pending", result.payload)
			continue
		case http.StatusConflict:
			streamWriter.write("repair", result.payload)
			return
		case http.StatusOK:
			streamWriter.write("delta", result.payload)
			cacheGeneration, revision = result.generation, result.revision
		default:
			streamWriter.write("unavailable", statsError("stats delta unavailable"))
			return
		}
		s.writeStatsRuntimeEvent(streamWriter, cacheGeneration)
	}
}

func (s *server) writeStatsRuntimeEvent(stream *statsSSEWriter, cacheGeneration int64) {
	load := s.statsRuntimeLoad()
	if len(load) == 0 {
		return
	}
	stream.write("runtime", map[string]any{"cache_generation": cacheGeneration, "load": load})
}

// statsSSEWriter assigns a process-relative monotonic millisecond id to every
// frame, including acknowledgement, heartbeat, and terminal error frames.
type statsSSEWriter struct {
	stream    *jsonSSEWriter
	startedAt time.Time
	lastID    int64
}

func (s *statsSSEWriter) write(event string, payload any) {
	id := time.Since(s.startedAt).Milliseconds()
	if id <= s.lastID {
		id = s.lastID + 1
	}
	s.lastID = id
	_ = s.stream.writeWithID(strconv.FormatInt(id, 10), event, payload)
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
		if err == nil && s.statsWriter != nil {
			_, err = s.statsWriter.InvalidatePricingCatalog(context.Background(), float64(time.Now().UnixNano())/1e9)
		}
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
	response := map[string]any{"ready": ready, "source_generation": serviceState.SourceGeneration, "cache_generation": serviceState.CacheGeneration, "pending_cells": maxInt(pending, 0), "pending_cell_details": pendingCells, "startup_preparation": statsStartupPreparationWire(counters.StartupPreparation), "lifecycle_runs": counters.Runs, "lifecycle_publications": counters.Publications, "lifecycle_repairs": counters.Repairs, "lifecycle_errors": counters.Errors, "lifecycle_background_backfill_deferrals": counters.BackgroundBackfillDeferrals, "lifecycle_selected_cells": counters.SelectedCells, "lifecycle_materialized_buckets": counters.MaterializedBuckets, "lifecycle_fact_rows_read": counters.FactRowsRead, "lifecycle_decoded_bytes": counters.DecodedBytes, "lifecycle_selection_duration_ms": counters.SelectionDuration.Seconds() * 1000, "lifecycle_materialization_duration_ms": counters.MaterializationDuration.Seconds() * 1000, "lifecycle_by_resolution": counters.ByResolution, "lifecycle_last_run_succeeded": counters.LastRunSucceeded, "lifecycle_last_run_error": counters.LastRunError, "migration_state": "ready", "materializer_state": func() string {
		if counters.Runs > 0 && !counters.LastRunSucceeded {
			return "error"
		}
		if counters.Runs == 0 {
			return "pending"
		}
		return "ready"
	}(), "ring_failure": serviceState.LastError, "failures": failures}
	jsonResponse(w, status, response)
}
