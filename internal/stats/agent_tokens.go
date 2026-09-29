package stats

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

const (
	AgentTokenActiveCadence = 5 * time.Second
	AgentTokenIdleCadence   = 5 * time.Second
	// OpenCode is read-only and exposes no row-level change log, so historical
	// in-place edits need an occasional bounded full reconciliation.
	openCodeRawReconcileInterval                   = 15 * time.Minute
	maxAgentTokenSources                           = 256
	maxAgentTokenRecords                           = 4096
	maxOpenCodeEventRevisionEntries                = 100_000
	maxOpenCodeRevisionBytes                       = 256
	maxCodexForkRepairCatalogRoots                 = maxAgentTokenSources
	maxCodexForkRepairSourcesPerRoot               = maxAgentTokenSources / 4
	maxCodexForkRepairDirectoryFrontier            = defaultTranscriptDiscoveryDirs
	maxCodexForkRepairFileFrontier                 = defaultTranscriptDiscoveryFiles
	maxConsecutiveLiveTranscriptPriorityRuns       = 2
	defaultTranscriptCycleMaxBytes                 = DefaultTranscriptMaxBytes
	defaultTranscriptCycleMaxRecords               = maxAgentTokenRecords
	legacyCodexRepairCycleMaxBytes           int64 = 8 << 20
	legacyCodexRepairCycleMaxRecords               = 8192
	defaultTranscriptDiscoveryFiles                = 256
	defaultTranscriptDiscoveryDirs                 = 512
	defaultTranscriptDiscoveryBytes                = 64 * 1024 * 1024
	maxCodexForkRepairHeaderBytes            int64 = 64 * 1024 * 1024
	defaultTranscriptDiscoveryTimeout              = 500 * time.Millisecond
	defaultCodexForkRepairDiscoveryTimeout         = 100 * time.Millisecond
	defaultOpenCodeInventoryTimeout                = 750 * time.Millisecond
	defaultOpenCodeCollectionTimeout               = 4 * time.Second
	defaultOpenCodeAppendTimeout                   = 2 * time.Second
	defaultAuxiliaryInventoryTimeout               = 250 * time.Millisecond
	maxDiagnosticPathBytes                         = 256
	maxDiagnosticTextBytes                         = 512
	maxAgentTokenEpochIDBytes                      = 128
)

// AgentTokenInventory is deliberately the same coherent inventory boundary as
// the status collector. Source resolution is separate so tests can copy real
// transcript/database fixtures without creating processes or tmux panes.
type AgentTokenInventory func(context.Context) ([]AgentRecord, []error)
type AgentTokenSources func(context.Context, []AgentRecord) ([]TranscriptSource, error)

type AgentTokenCollectorConfig struct {
	ActiveCadence        time.Duration
	IdleCadence          time.Duration
	Inventory            AgentTokenInventory
	OpenCodeInventory    AgentTokenInventory
	TranscriptSources    AgentTokenSources
	UsagePricingProfile  func(UsageAtom) string
	ClaudeTranscriptRoot string
	CodexTranscriptRoot  string
	OpenCodeDatabase     string
	CursorPath           string
	TranscriptCursorPath string
	SkipTranscriptScan   bool
	// RequireTmuxPane limits production attribution to agents visible in the
	// machine-wide tmux roster. Fixture collectors leave this false so they can
	// scan synthetic transcript records without creating a tmux server.
	RequireTmuxPane                 bool
	EpochID                         string
	EpochStartedAt                  float64
	OwnerGeneration                 uint64
	SourceID                        string
	ActiveFunc                      func() bool
	TranscriptDiscoveryMaxFiles     int
	TranscriptDiscoveryMaxDirs      int
	TranscriptDiscoveryMaxBytes     int64
	TranscriptDiscoveryTimeout      time.Duration
	CodexForkRepairDiscoveryTimeout time.Duration
	TranscriptMaxBytesPerCycle      int64
	TranscriptMaxRecordsPerCycle    int
	OpenCodeInventoryTimeout        time.Duration
	OpenCodeCollectionTimeout       time.Duration
	OpenCodeAppendTimeout           time.Duration
	AuxiliaryInventoryTimeout       time.Duration
}

func (c AgentTokenCollectorConfig) withDefaults() AgentTokenCollectorConfig {
	if c.ActiveCadence <= 0 {
		c.ActiveCadence = AgentTokenActiveCadence
	}
	if c.IdleCadence <= 0 {
		c.IdleCadence = AgentTokenIdleCadence
	}
	if c.EpochID == "" {
		c.EpochID = "agent-tokens"
	}
	c.EpochID = boundedAgentTokenEpochID(c.EpochID)
	if c.SourceID == "" {
		c.SourceID = "agent-tokens"
	}
	if c.CursorPath == "" {
		c.CursorPath = filepath.Join(os.TempDir(), "yolomux-opencode-token-cursors.json")
	}
	if c.TranscriptCursorPath == "" {
		c.TranscriptCursorPath = c.CursorPath + ".transcripts"
	}
	if c.TranscriptDiscoveryMaxFiles <= 0 {
		c.TranscriptDiscoveryMaxFiles = defaultTranscriptDiscoveryFiles
	}
	if c.TranscriptDiscoveryMaxDirs <= 0 {
		c.TranscriptDiscoveryMaxDirs = defaultTranscriptDiscoveryDirs
	}
	if c.TranscriptDiscoveryMaxBytes <= 0 {
		c.TranscriptDiscoveryMaxBytes = defaultTranscriptDiscoveryBytes
	}
	if c.TranscriptDiscoveryTimeout <= 0 {
		c.TranscriptDiscoveryTimeout = defaultTranscriptDiscoveryTimeout
	}
	if c.CodexForkRepairDiscoveryTimeout <= 0 {
		c.CodexForkRepairDiscoveryTimeout = defaultCodexForkRepairDiscoveryTimeout
	}
	if c.TranscriptMaxBytesPerCycle <= 0 {
		c.TranscriptMaxBytesPerCycle = defaultTranscriptCycleMaxBytes
	}
	if c.TranscriptMaxRecordsPerCycle <= 0 {
		c.TranscriptMaxRecordsPerCycle = defaultTranscriptCycleMaxRecords
	}
	if c.OpenCodeInventoryTimeout <= 0 {
		c.OpenCodeInventoryTimeout = defaultOpenCodeInventoryTimeout
	}
	if c.OpenCodeCollectionTimeout <= 0 {
		c.OpenCodeCollectionTimeout = defaultOpenCodeCollectionTimeout
	}
	if c.OpenCodeAppendTimeout <= 0 {
		c.OpenCodeAppendTimeout = defaultOpenCodeAppendTimeout
	}
	if c.AuxiliaryInventoryTimeout <= 0 {
		c.AuxiliaryInventoryTimeout = defaultAuxiliaryInventoryTimeout
	}
	if c.TranscriptSources == nil {
		discoveryCursors := newTranscriptDiscoveryCursorStore()
		c.TranscriptSources = func(ctx context.Context, records []AgentRecord) ([]TranscriptSource, error) {
			return defaultAgentTokenSourcesBoundedWithCursor(ctx, records, transcriptDiscoveryBounds{
				maxFiles: c.TranscriptDiscoveryMaxFiles,
				maxDirs:  c.TranscriptDiscoveryMaxDirs,
				maxBytes: c.TranscriptDiscoveryMaxBytes,
			}, discoveryCursors, c.ClaudeTranscriptRoot, c.CodexTranscriptRoot)
		}
	}
	return c
}

func boundedAgentTokenEpochID(value string) string {
	return truncateAgentTokenEpochID(value, maxAgentTokenEpochIDBytes)
}

func truncateAgentTokenEpochID(value string, maxBytes int) string {
	if maxBytes <= 0 {
		return ""
	}
	if len([]byte(value)) <= maxBytes {
		return value
	}
	end := maxBytes
	for end > 0 && !utf8.RuneStart(value[end]) {
		end--
	}
	if end == 0 {
		return "agent-tokens"[:min(len("agent-tokens"), maxBytes)]
	}
	return value[:end]
}

type AgentTokenCollectorCounters struct {
	Cycles                     uint64
	UsageAccepted              uint64
	UsageDuplicates            uint64
	CoverageAccepted           uint64
	UnavailableAccepted        uint64
	Errors                     uint64
	AppendErrors               uint64
	MetadataErrors             uint64
	OpenCodeInventoryErrors    uint64
	OpenCodeInventoryTimeouts  uint64
	AuxiliaryInventoryErrors   uint64
	AuxiliaryInventoryTimeouts uint64
	OpenCodeDatabaseOpens      uint64
	OpenCodeDatabaseReuses     uint64
	OpenCodeDatabaseLastOpen   time.Duration
	OpenCodeDatabaseOpenTotal  time.Duration
	OpenCodeCollectionLast     time.Duration
	OpenCodeCollectionTotal    time.Duration
	OpenCodeSQLiteQueries      uint64
	OpenCodeSQLiteLast         time.Duration
	OpenCodeSQLiteTotal        time.Duration
	OpenCodePhases             map[string]OpenCodePhaseTiming
	LastDuration               time.Duration
	DurationTotal              time.Duration
	LastCadence                time.Duration
}

// OpenCodePhaseTiming attributes the bounded database-backed portion of one
// token cycle. Durations are diagnostic evidence only; they do not change the
// collection or coverage contract.
type OpenCodePhaseTiming struct {
	Runs  uint64
	Last  time.Duration
	Total time.Duration
}

type agentTokenCounters struct {
	cycles, usage, usageDuplicates, coverage, unavailable, errors atomic.Uint64
	appendErrors, metadataErrors                                  atomic.Uint64
	opencodeInventoryErrors, opencodeInventoryTimeouts            atomic.Uint64
	auxiliaryInventoryErrors, auxiliaryInventoryTimeouts          atomic.Uint64
	opencodeDatabaseOpens, opencodeDatabaseReuses                 atomic.Uint64
	opencodeDatabaseLastOpen, opencodeDatabaseOpenTotal           atomic.Int64
	opencodeCollectionLast, opencodeCollectionTotal               atomic.Int64
	opencodeSQLiteQueries                                         atomic.Uint64
	opencodeSQLiteLast, opencodeSQLiteTotal                       atomic.Int64
	duration, durationTotal                                       atomic.Int64
	lastCadence                                                   atomic.Int64
	opencodePhaseMu                                               sync.Mutex
	opencodePhases                                                map[string]OpenCodePhaseTiming
}

func (c *agentTokenCounters) snapshot() AgentTokenCollectorCounters {
	c.opencodePhaseMu.Lock()
	phases := make(map[string]OpenCodePhaseTiming, len(c.opencodePhases))
	for name, timing := range c.opencodePhases {
		phases[name] = timing
	}
	c.opencodePhaseMu.Unlock()
	return AgentTokenCollectorCounters{
		Cycles: c.cycles.Load(), UsageAccepted: c.usage.Load(), UsageDuplicates: c.usageDuplicates.Load(), CoverageAccepted: c.coverage.Load(),
		UnavailableAccepted: c.unavailable.Load(), Errors: c.errors.Load(), AppendErrors: c.appendErrors.Load(), MetadataErrors: c.metadataErrors.Load(),
		OpenCodeInventoryErrors: c.opencodeInventoryErrors.Load(), OpenCodeInventoryTimeouts: c.opencodeInventoryTimeouts.Load(),
		AuxiliaryInventoryErrors: c.auxiliaryInventoryErrors.Load(), AuxiliaryInventoryTimeouts: c.auxiliaryInventoryTimeouts.Load(),
		OpenCodeDatabaseOpens: c.opencodeDatabaseOpens.Load(), OpenCodeDatabaseReuses: c.opencodeDatabaseReuses.Load(),
		OpenCodeDatabaseLastOpen: time.Duration(c.opencodeDatabaseLastOpen.Load()), OpenCodeDatabaseOpenTotal: time.Duration(c.opencodeDatabaseOpenTotal.Load()),
		OpenCodeCollectionLast: time.Duration(c.opencodeCollectionLast.Load()), OpenCodeCollectionTotal: time.Duration(c.opencodeCollectionTotal.Load()),
		OpenCodeSQLiteQueries: c.opencodeSQLiteQueries.Load(), OpenCodeSQLiteLast: time.Duration(c.opencodeSQLiteLast.Load()), OpenCodeSQLiteTotal: time.Duration(c.opencodeSQLiteTotal.Load()),
		OpenCodePhases: phases,
		LastDuration:   time.Duration(c.duration.Load()), DurationTotal: time.Duration(c.durationTotal.Load()), LastCadence: time.Duration(c.lastCadence.Load()),
	}
}

func (c *agentTokenCounters) recordOpenCodePhase(name string, duration time.Duration) {
	if duration < 0 {
		duration = 0
	}
	c.opencodePhaseMu.Lock()
	if c.opencodePhases == nil {
		c.opencodePhases = make(map[string]OpenCodePhaseTiming)
	}
	timing := c.opencodePhases[name]
	timing.Runs++
	timing.Last = duration
	timing.Total += duration
	c.opencodePhases[name] = timing
	c.opencodePhaseMu.Unlock()
}

// AgentTokenCollectionSnapshot is a bounded, runtime-only diagnostic view of
// the token collector. It is deliberately separate from the persisted stats
// payload so existing browser consumers do not acquire new stream semantics.
type AgentTokenCollectionSnapshot struct {
	Cycles                      uint64                           `json:"cycles"`
	LastDurationMS              float64                          `json:"last_duration_ms"`
	DurationTotalMS             float64                          `json:"duration_total_ms"`
	LastAttemptAt               string                           `json:"last_attempt_at,omitempty"`
	LastSuccessAt               string                           `json:"last_success_at,omitempty"`
	UsageAccepted               uint64                           `json:"usage_accepted"`
	UsageDuplicates             uint64                           `json:"usage_duplicates"`
	OpenCodeInventoryRecords    int                              `json:"opencode_inventory_records"`
	OpenCodeInventoryErrors     uint64                           `json:"opencode_inventory_errors"`
	OpenCodeInventoryTimeouts   uint64                           `json:"opencode_inventory_timeouts"`
	OpenCodeInventoryLastError  string                           `json:"opencode_inventory_last_error,omitempty"`
	OpenCodeDatabaseOpens       uint64                           `json:"opencode_database_opens"`
	OpenCodeDatabaseReuses      uint64                           `json:"opencode_database_reuses"`
	OpenCodeDatabaseLastOpenMS  float64                          `json:"opencode_database_last_open_duration_ms"`
	OpenCodeDatabaseOpenTotalMS float64                          `json:"opencode_database_open_duration_total_ms"`
	OpenCodeCollectionLastMS    float64                          `json:"opencode_collection_last_duration_ms"`
	OpenCodeCollectionTotalMS   float64                          `json:"opencode_collection_duration_total_ms"`
	OpenCodeSQLiteQueries       uint64                           `json:"opencode_sqlite_queries"`
	OpenCodeSQLiteLastMS        float64                          `json:"opencode_sqlite_last_duration_ms"`
	OpenCodeSQLiteTotalMS       float64                          `json:"opencode_sqlite_duration_total_ms"`
	OpenCodePhases              map[string]OpenCodePhaseSnapshot `json:"opencode_phases,omitempty"`
	BatchAtoms                  int                              `json:"batch_atoms"`
	BatchCoverageEpochs         int                              `json:"batch_coverage_epochs"`
	BatchUnavailableSpans       int                              `json:"batch_unavailable_spans"`
	AppendErrors                uint64                           `json:"append_errors"`
	MetadataErrors              uint64                           `json:"metadata_errors"`
	LastAppendError             string                           `json:"last_append_error,omitempty"`
	LastMetadataError           string                           `json:"last_metadata_error,omitempty"`
	SourceDBPath                string                           `json:"source_db_path,omitempty"`
	SourceCursorPath            string                           `json:"source_cursor_path,omitempty"`
	LastReason                  string                           `json:"last_reason,omitempty"`
}

type OpenCodePhaseSnapshot struct {
	Runs            uint64  `json:"runs"`
	LastDurationMS  float64 `json:"last_duration_ms"`
	TotalDurationMS float64 `json:"total_duration_ms"`
}

type agentTokenDiagnostics struct {
	lastAttemptAt              time.Time
	lastSuccessAt              time.Time
	opencodeInventoryRecords   int
	opencodeInventoryLastError string
	batch                      AgentTokenCollectionSnapshot
	lastAppendError            string
	lastMetadataError          string
	lastReason                 string
}

type transcriptCursorRepository interface {
	State(TranscriptSource) (TranscriptScannerState, error)
	Schedule() (transcriptCursorSchedule, error)
	NextSource() (string, error)
	Prepare(TranscriptSource, TranscriptScannerState, TranscriptScannerState) error
	PrepareBatch([]transcriptCursorChange) error
	PrepareBatchWithNextSource([]transcriptCursorChange, string) error
	PrepareBatchWithSchedules([]transcriptCursorChange, string, string, int) error
	PrepareBatchWithHistoricalSchedule([]transcriptCursorChange, string, int) error
	CommitBatch([]transcriptCursorChange) error
	RollbackBatch([]transcriptCursorChange) error
	CodexForkRepairFrontier() (codexForkRepairFrontierState, error)
	SaveCodexForkRepairFrontier(codexForkRepairFrontierState) error
	CompletedCodexForkRepairRoots() (map[string]struct{}, error)
	CommitBatchWithCodexForkRepairRoots([]transcriptCursorChange, []string) error
}

// AgentTokenCollector owns one bounded collection goroutine. RunOnce is safe
// for deterministic callers and is the transaction boundary used by tests.
type AgentTokenCollector struct {
	writer                     BatchWriter
	clock                      HostCollectorClock
	config                     AgentTokenCollectorConfig
	transcriptStates           map[string]*TranscriptScannerState
	transcriptCursor           transcriptCursorRepository
	codexForkRepairMu          sync.Mutex
	codexForkRepairCatalog     map[string]*codexForkRepairCatalog
	nextCodexForkRepairRoot    string
	nextCodexForkRepairSource  string
	codexForkRepairLoaded      bool
	nextHistoricalTranscript   string
	liveTranscriptPriorityRuns int
	cursor                     *OpenCodeTokenCursor
	openCodeDatabaseMu         sync.Mutex
	openCodeDatabase           *OpenCodeDatabase
	openCodeDatabasePath       string
	openCodeDatabaseDevice     uint64
	openCodeDatabaseInode      uint64
	openCodeDatabaseWALDevice  uint64
	openCodeDatabaseWALInode   uint64
	// Test seam for process-crash coverage between source reads and cursor preparation.
	openCodeAfterSourceRead func(OpenCodeStepFinishWindow, error)
	counters                agentTokenCounters
	diagnosticMu            sync.RWMutex
	diagnostics             agentTokenDiagnostics
	mu                      sync.Mutex
	run                     bool
	stop                    context.CancelFunc
	done                    chan struct{}
	lastCadence             float64
	epochBaseID             string
	epochSequence           uint64
}

type codexForkRepairCatalog struct {
	root              string
	pendingDirs       []string
	pendingFiles      []string
	blockedFiles      []string
	sources           map[string]TranscriptSource
	directory         *codexForkRepairDirectoryState
	pausedDirectories []codexForkRepairDirectoryState
	blocked           bool
	initialized       bool
	complete          bool
}

func NewAgentTokenCollector(writer BatchWriter, clock HostCollectorClock, config AgentTokenCollectorConfig) *AgentTokenCollector {
	if clock == nil {
		clock = realHostCollectorClock{}
	}
	config = config.withDefaults()
	cursor := NewOpenCodeTokenCursor(config.CursorPath)
	return &AgentTokenCollector{writer: writer, clock: clock, config: config, transcriptStates: make(map[string]*TranscriptScannerState), transcriptCursor: NewTranscriptCursorStore(config.TranscriptCursorPath), codexForkRepairCatalog: make(map[string]*codexForkRepairCatalog), cursor: cursor, epochBaseID: config.EpochID}
}

func (c *AgentTokenCollector) Counters() AgentTokenCollectorCounters { return c.counters.snapshot() }

// Snapshot returns the latest bounded runtime diagnostics. Paths are reduced
// to home-relative values and all diagnostic strings have a fixed byte bound.
func (c *AgentTokenCollector) Snapshot() AgentTokenCollectionSnapshot {
	counters := c.counters.snapshot()
	c.diagnosticMu.RLock()
	diagnostics := c.diagnostics
	c.diagnosticMu.RUnlock()
	snapshot := AgentTokenCollectionSnapshot{
		Cycles:                      counters.Cycles,
		LastDurationMS:              counters.LastDuration.Seconds() * 1000,
		DurationTotalMS:             counters.DurationTotal.Seconds() * 1000,
		UsageAccepted:               counters.UsageAccepted,
		UsageDuplicates:             counters.UsageDuplicates,
		OpenCodeInventoryRecords:    diagnostics.opencodeInventoryRecords,
		OpenCodeInventoryErrors:     counters.OpenCodeInventoryErrors,
		OpenCodeInventoryTimeouts:   counters.OpenCodeInventoryTimeouts,
		OpenCodeInventoryLastError:  diagnostics.opencodeInventoryLastError,
		OpenCodeDatabaseOpens:       counters.OpenCodeDatabaseOpens,
		OpenCodeDatabaseReuses:      counters.OpenCodeDatabaseReuses,
		OpenCodeDatabaseLastOpenMS:  counters.OpenCodeDatabaseLastOpen.Seconds() * 1000,
		OpenCodeDatabaseOpenTotalMS: counters.OpenCodeDatabaseOpenTotal.Seconds() * 1000,
		OpenCodeCollectionLastMS:    counters.OpenCodeCollectionLast.Seconds() * 1000,
		OpenCodeCollectionTotalMS:   counters.OpenCodeCollectionTotal.Seconds() * 1000,
		OpenCodeSQLiteQueries:       counters.OpenCodeSQLiteQueries,
		OpenCodeSQLiteLastMS:        counters.OpenCodeSQLiteLast.Seconds() * 1000,
		OpenCodeSQLiteTotalMS:       counters.OpenCodeSQLiteTotal.Seconds() * 1000,
		BatchAtoms:                  diagnostics.batch.BatchAtoms,
		BatchCoverageEpochs:         diagnostics.batch.BatchCoverageEpochs,
		BatchUnavailableSpans:       diagnostics.batch.BatchUnavailableSpans,
		AppendErrors:                counters.AppendErrors,
		MetadataErrors:              counters.MetadataErrors,
		LastAppendError:             diagnostics.lastAppendError,
		LastMetadataError:           diagnostics.lastMetadataError,
		SourceDBPath:                diagnosticPath(c.config.OpenCodeDatabase),
		SourceCursorPath:            diagnosticPath(c.cursor.path),
		LastReason:                  diagnostics.lastReason,
	}
	if len(counters.OpenCodePhases) > 0 {
		snapshot.OpenCodePhases = make(map[string]OpenCodePhaseSnapshot, len(counters.OpenCodePhases))
		for name, timing := range counters.OpenCodePhases {
			snapshot.OpenCodePhases[name] = OpenCodePhaseSnapshot{Runs: timing.Runs, LastDurationMS: timing.Last.Seconds() * 1000, TotalDurationMS: timing.Total.Seconds() * 1000}
		}
	}
	if !diagnostics.lastAttemptAt.IsZero() {
		snapshot.LastAttemptAt = diagnostics.lastAttemptAt.UTC().Format(time.RFC3339Nano)
	}
	if !diagnostics.lastSuccessAt.IsZero() {
		snapshot.LastSuccessAt = diagnostics.lastSuccessAt.UTC().Format(time.RFC3339Nano)
	}
	return snapshot
}

func (c *AgentTokenCollector) setAttempt(at time.Time) {
	c.diagnosticMu.Lock()
	c.diagnostics.lastAttemptAt = at
	c.diagnostics.lastReason = ""
	c.diagnosticMu.Unlock()
}

func (c *AgentTokenCollector) setSuccess(at time.Time) {
	c.diagnosticMu.Lock()
	c.diagnostics.lastSuccessAt = at
	if c.diagnostics.lastReason == "" {
		c.diagnostics.lastReason = "ok"
	}
	c.diagnosticMu.Unlock()
}

func (c *AgentTokenCollector) setLastReason(reason string) {
	if reason == "" {
		return
	}
	c.diagnosticMu.Lock()
	c.diagnostics.lastReason = diagnosticText(reason)
	c.diagnosticMu.Unlock()
}

func diagnosticText(value string) string {
	value = strings.TrimSpace(value)
	if len([]byte(value)) <= maxDiagnosticTextBytes {
		return value
	}
	value = string([]byte(value)[:maxDiagnosticTextBytes])
	for len(value) > 0 && !utf8.ValidString(value) {
		value = value[:len(value)-1]
	}
	return value
}

func diagnosticPath(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	home, err := os.UserHomeDir()
	if err == nil {
		if relative, relativeErr := filepath.Rel(home, value); relativeErr == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			value = "~/" + filepath.ToSlash(relative)
		} else {
			value = "<redacted>/" + filepath.Base(value)
		}
	} else {
		value = "<redacted>/" + filepath.Base(value)
	}
	if len([]byte(value)) > maxDiagnosticPathBytes {
		value = value[:maxDiagnosticPathBytes]
	}
	return diagnosticText(value)
}

func (c *AgentTokenCollector) setReason(reason string) {
	if reason == "" {
		return
	}
	c.diagnosticMu.Lock()
	c.diagnostics.lastReason = diagnosticText(reason)
	c.diagnosticMu.Unlock()
}

func (c *AgentTokenCollector) recordInventory(records []AgentRecord, inventoryErrors []error, timedOut bool) {
	c.diagnosticMu.Lock()
	c.diagnostics.opencodeInventoryRecords = len(records)
	if len(inventoryErrors) > 0 {
		c.diagnostics.opencodeInventoryLastError = diagnosticText(inventoryErrors[len(inventoryErrors)-1].Error())
	}
	c.diagnosticMu.Unlock()
	if timedOut {
		c.setReason("opencode-inventory-timeout")
	}
	for _, inventoryErr := range inventoryErrors {
		c.setReason(inventoryErr.Error())
	}
}

func (c *AgentTokenCollector) recordInventoryFailure(err error) {
	if err == nil {
		return
	}
	c.diagnosticMu.Lock()
	c.diagnostics.opencodeInventoryLastError = diagnosticText(err.Error())
	c.diagnosticMu.Unlock()
	c.setLastReason(err.Error())
}

func (c *AgentTokenCollector) recordBatch(batch AppendBatch) {
	c.diagnosticMu.Lock()
	c.diagnostics.batch.BatchAtoms = len(batch.UsageAtoms)
	c.diagnostics.batch.BatchCoverageEpochs = len(batch.CoverageEpochs)
	c.diagnostics.batch.BatchUnavailableSpans = len(batch.UnavailableSpans)
	// A cycle can legitimately publish usable atoms while recording an
	// unavailable span for an unrelated stale process. Keep that span in the
	// durable product, but do not let it masquerade as the cycle's overall
	// failure in the latest-success diagnostic. A batch with no usable facts
	// still reports its concrete unavailable reason.
	if len(batch.UsageAtoms) > 0 {
		c.diagnostics.lastReason = ""
	} else if len(batch.UnavailableSpans) > 0 {
		c.diagnostics.lastReason = diagnosticText(batch.UnavailableSpans[len(batch.UnavailableSpans)-1].Reason)
	}
	c.diagnosticMu.Unlock()
}

func (c *AgentTokenCollector) recordCollectionError(err error) {
	c.counters.errors.Add(1)
	c.setReason(err.Error())
}

func (c *AgentTokenCollector) recordAppendError(err error, metadata bool) {
	message := "append failed"
	if err != nil {
		message = err.Error()
	}
	c.diagnosticMu.Lock()
	c.diagnostics.lastAppendError = diagnosticText(message)
	c.diagnosticMu.Unlock()
	c.counters.errors.Add(1)
	c.counters.appendErrors.Add(1)
	if metadata {
		c.diagnosticMu.Lock()
		c.diagnostics.lastMetadataError = diagnosticText(message)
		c.diagnosticMu.Unlock()
		c.counters.metadataErrors.Add(1)
	}
	c.setReason(message)
}

func hasMetadataFacts(batch AppendBatch) bool {
	return len(batch.CoverageEpochs) > 0 || len(batch.UnavailableSpans) > 0
}

func (c *AgentTokenCollector) Start(parent context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.run {
		return errors.New("agent token collector already started")
	}
	ctx, cancel := context.WithCancel(parent)
	c.stop, c.done, c.run = cancel, make(chan struct{}), true
	go c.loop(ctx)
	return nil
}
func (c *AgentTokenCollector) Stop() {
	c.mu.Lock()
	if !c.run {
		c.mu.Unlock()
		c.closeOpenCodeDatabase()
		c.closeCodexForkRepairDirectories()
		return
	}
	stop, done := c.stop, c.done
	c.mu.Unlock()
	stop()
	<-done
	c.closeOpenCodeDatabase()
	c.closeCodexForkRepairDirectories()
}

func (c *AgentTokenCollector) closeCodexForkRepairDirectories() {
	if err := c.loadCodexForkRepairFrontier(); err != nil {
		c.recordCollectionError(fmt.Errorf("load Codex fork repair frontier during stop: %w", err))
		return
	}
	c.codexForkRepairMu.Lock()
	for _, catalog := range c.codexForkRepairCatalog {
		retryCodexForkRepairBlockedFiles(catalog)
	}
	err := c.saveCodexForkRepairFrontierLocked()
	c.codexForkRepairMu.Unlock()
	if err != nil {
		c.recordCollectionError(fmt.Errorf("persist Codex fork repair frontier during stop: %w", err))
	}
}

// closeOpenCodeDatabase releases the long-lived read-only handle owned by the
// collector. Keeping this handle across ten-second cycles avoids repeating
// SQLite connection setup and schema validation against a potentially large
// external database, while Stop still gives tests and shutdown a deterministic
// close boundary.
func (c *AgentTokenCollector) closeOpenCodeDatabase() {
	c.openCodeDatabaseMu.Lock()
	database := c.openCodeDatabase
	c.openCodeDatabase = nil
	c.openCodeDatabasePath = ""
	c.openCodeDatabaseDevice = 0
	c.openCodeDatabaseInode = 0
	c.openCodeDatabaseWALDevice = 0
	c.openCodeDatabaseWALInode = 0
	c.openCodeDatabaseMu.Unlock()
	if database != nil {
		_ = database.Close()
	}
}

// discardOpenCodeDatabase closes the handle only when it is still the cached
// instance that observed the source failure. This keeps a later replacement
// from being closed by an older collection attempt if the collector is ever
// driven concurrently by a caller and its background loop.
func (c *AgentTokenCollector) discardOpenCodeDatabase(database *OpenCodeDatabase) {
	if database == nil {
		return
	}
	c.openCodeDatabaseMu.Lock()
	if c.openCodeDatabase != database {
		c.openCodeDatabaseMu.Unlock()
		return
	}
	c.openCodeDatabase = nil
	c.openCodeDatabasePath = ""
	c.openCodeDatabaseDevice = 0
	c.openCodeDatabaseInode = 0
	c.openCodeDatabaseWALDevice = 0
	c.openCodeDatabaseWALInode = 0
	c.openCodeDatabaseMu.Unlock()
	_ = database.Close()
}

func (c *AgentTokenCollector) openCachedOpenCodeDatabase(ctx context.Context, path string, device, inode, walDevice, walInode uint64) (*OpenCodeDatabase, bool, time.Duration, error) {
	c.openCodeDatabaseMu.Lock()
	defer c.openCodeDatabaseMu.Unlock()
	if c.openCodeDatabase != nil && c.openCodeDatabasePath == path && c.openCodeDatabaseDevice == device && c.openCodeDatabaseInode == inode && c.openCodeDatabaseWALDevice == walDevice && c.openCodeDatabaseWALInode == walInode {
		return c.openCodeDatabase, true, 0, nil
	}
	if c.openCodeDatabase != nil {
		_ = c.openCodeDatabase.Close()
		c.openCodeDatabase = nil
	}
	started := time.Now()
	database, err := OpenOpenCodeReadOnly(ctx, path)
	duration := time.Since(started)
	if err != nil {
		return nil, false, duration, err
	}
	c.openCodeDatabase = database
	c.openCodeDatabasePath = path
	c.openCodeDatabaseDevice = device
	c.openCodeDatabaseInode = inode
	c.openCodeDatabaseWALDevice = walDevice
	c.openCodeDatabaseWALInode = walInode
	return database, false, duration, nil
}

// openCodeWALIdentity is part of the external-source identity. OpenCode may
// replace only the WAL sidecar while retaining the main database inode; a
// long-lived read handle must not be reused across that boundary. A missing
// WAL is a valid rollback-journal or checkpointed state and is represented by
// the zero identity.
func openCodeWALIdentity(path string) (uint64, uint64, error) {
	info, err := os.Stat(path + "-wal")
	if errors.Is(err, os.ErrNotExist) {
		return 0, 0, nil
	}
	if err != nil {
		return 0, 0, err
	}
	device, inode := fileIdentity(info)
	if device == 0 || inode == 0 {
		return 0, 0, errors.New("OpenCode WAL identity unavailable")
	}
	return device, inode, nil
}

func (c *AgentTokenCollector) loop(ctx context.Context) {
	runWithStatsProfileLabels(ctx, "agent_token_collector", "", func(ctx context.Context) {
		defer func() { c.mu.Lock(); c.run = false; close(c.done); c.mu.Unlock() }()
		first := true
		for {
			if !first {
				if !waitCollectorCadence(ctx, c.clock, c.config.ActiveCadence, c.config.IdleCadence, c.config.ActiveFunc) {
					return
				}
			}
			first = false
			if ctx.Err() != nil {
				return
			}
			c.RunOnce(ctx)
		}
	})
}

func (c *AgentTokenCollector) RunOnce(parent context.Context) {
	started := c.clock.Now()
	c.setAttempt(started)
	defer func() {
		elapsed := c.clock.Now().Sub(started)
		if elapsed < 0 {
			elapsed = 0
		}
		c.counters.duration.Store(elapsed.Nanoseconds())
		c.counters.durationTotal.Add(elapsed.Nanoseconds())
	}()
	c.counters.cycles.Add(1)
	cadence := c.config.ActiveCadence
	if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
		cadence = c.config.IdleCadence
	}
	if c.lastCadence != 0 && c.lastCadence != cadence.Seconds() {
		// Coverage rows pin their native cadence. A demand transition therefore
		// starts a new token epoch rather than mutating an existing row.
		c.epochSequence++
		suffix := strconv.FormatUint(c.epochSequence, 10)
		base := c.epochBaseID
		limit := maxAgentTokenEpochIDBytes - len(suffix) - 1
		base = truncateAgentTokenEpochID(base, limit)
		c.config.EpochID = base + "-" + suffix
		c.config.EpochStartedAt = float64(started.UnixNano()) / 1e9
	}
	c.lastCadence = cadence.Seconds()
	c.counters.lastCadence.Store(cadence.Nanoseconds())
	now := float64(started.UnixNano()) / 1e9
	if c.config.OpenCodeDatabase != "" && (c.config.Inventory != nil || c.config.OpenCodeInventory != nil) {
		c.runOnceSplit(parent, now, cadence.Seconds(), started)
		return
	}
	var batch AppendBatch
	var receipts []agentTokenReceipt
	var err error
	runWithStatsProfilePhase(parent, "agent_token_collector", "agent_tokens", "collection", func(phaseCtx context.Context) {
		batch, receipts, err = c.collect(phaseCtx, now, cadence.Seconds())
	})
	if err != nil {
		c.recordCollectionError(err)
		return
	}
	c.recordBatch(batch)
	var appendOK bool
	var appendErr error
	runWithStatsProfilePhase(parent, "agent_token_collector", "agent_tokens", "append", func(phaseCtx context.Context) {
		appendOK, appendErr = c.appendCollected(phaseCtx, batch, receipts)
	})
	if !appendOK {
		c.recordAppendError(appendErr, hasMetadataFacts(batch))
		return
	}
	c.setSuccess(started)
}

// runOnceSplit makes the OpenCode database append its own transaction. A
// transcript root is an optional enrichment source; it must never hold an
// OpenCode cursor or batch hostage while a historical tree is discovered.
func (c *AgentTokenCollector) runOnceSplit(parent context.Context, observed, cadence float64, attemptedAt time.Time) {
	opencodeInventory := c.config.OpenCodeInventory
	if opencodeInventory == nil {
		// Preserve the fixture-friendly legacy seam. Production supplies the
		// narrow inventory explicitly so this fallback is never on the live path.
		opencodeInventory = c.config.Inventory
	}
	openCtx, openCancel := context.WithTimeout(parent, c.config.OpenCodeInventoryTimeout)
	var records []AgentRecord
	var inventoryErrors []error
	var timedOut bool
	runWithStatsProfilePhase(openCtx, "agent_token_collector", "agent_tokens", "opencode_inventory", func(ctx context.Context) {
		records, inventoryErrors, timedOut = runBoundedAgentInventory(ctx, opencodeInventory)
	})
	openCancel()
	if c.config.RequireTmuxPane {
		records = tmuxScopedAgentRecords(records)
	}
	c.recordInventory(records, inventoryErrors, timedOut)
	if timedOut {
		c.counters.opencodeInventoryTimeouts.Add(1)
	}
	if len(inventoryErrors) > 0 {
		c.counters.opencodeInventoryErrors.Add(uint64(len(inventoryErrors)))
		c.recordInventoryFailure(inventoryErrors[len(inventoryErrors)-1])
	}
	if len(records) > maxAgentTokenSources {
		batch := AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, c.config.SourceID, observed, cadence, "inventory-bound-exceeded")}}
		c.recordBatch(batch)
		if ok, err := c.appendCollected(parent, batch, nil); !ok {
			c.recordAppendError(err, true)
		} else {
			c.setSuccess(attemptedAt)
		}
		return
	}
	openCollectCtx, openCollectCancel := context.WithTimeout(parent, c.config.OpenCodeCollectionTimeout)
	var openBatch AppendBatch
	var receipt *agentTokenReceipt
	var err error
	runWithStatsProfilePhase(openCollectCtx, "agent_token_collector", "agent_tokens", "opencode_collection", func(ctx context.Context) {
		openBatch, receipt, err = c.collectOpenCode(ctx, observed, cadence, records)
	})
	if err != nil {
		openCollectCancel()
		c.recordCollectionError(err)
		return
	}
	openAppendCtx, openAppendCancel := context.WithTimeout(parent, c.config.OpenCodeAppendTimeout)
	c.recordBatch(openBatch)
	openAppendStarted := time.Now()
	var appendOK, metadataError bool
	var appendErr error
	runWithStatsProfilePhase(openAppendCtx, "agent_token_collector", "agent_tokens", "opencode_append", func(ctx context.Context) {
		appendOK, metadataError, appendErr = c.appendOpenCodeCollected(ctx, openBatch, receipt)
	})
	c.counters.recordOpenCodePhase("append", time.Since(openAppendStarted))
	openAppendCancel()
	openCollectCancel()
	if !appendOK {
		c.recordAppendError(appendErr, metadataError)
		return
	}

	if c.config.SkipTranscriptScan {
		c.setSuccess(attemptedAt)
		return
	}
	auxiliaryRecords, auxiliaryErrors := records, inventoryErrors
	if c.config.OpenCodeInventory != nil && c.config.Inventory != nil {
		auxCtx, auxCancel := context.WithTimeout(parent, c.config.AuxiliaryInventoryTimeout)
		var auxiliaryTimedOut bool
		runWithStatsProfilePhase(auxCtx, "agent_token_collector", "agent_tokens", "transcript_inventory", func(ctx context.Context) {
			auxiliaryRecords, auxiliaryErrors, auxiliaryTimedOut = runBoundedAgentInventory(ctx, c.config.Inventory)
		})
		auxCancel()
		if auxiliaryTimedOut {
			c.counters.auxiliaryInventoryTimeouts.Add(1)
		}
		if len(auxiliaryErrors) > 0 {
			c.counters.auxiliaryInventoryErrors.Add(uint64(len(auxiliaryErrors)))
		}
	}
	if c.config.RequireTmuxPane {
		auxiliaryRecords = tmuxScopedAgentRecords(auxiliaryRecords)
	}
	var batch AppendBatch
	var receipts []agentTokenReceipt
	runWithStatsProfilePhase(parent, "agent_token_collector", "agent_tokens", "transcript_discovery", func(ctx context.Context) {
		batch, receipts, err = c.collectTranscripts(ctx, observed, cadence, auxiliaryRecords, auxiliaryErrors, AppendBatch{}, nil)
	})
	if err != nil {
		if len(batch.UnavailableSpans) == 0 {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, err.Error()))
		}
	}
	if len(batch.UsageAtoms) == 0 && len(batch.UsageTombstones) == 0 && len(batch.CoverageEpochs) == 0 && len(batch.UnavailableSpans) == 0 && len(receipts) == 0 {
		c.setSuccess(attemptedAt)
		return
	}
	// The parent may have expired while optional transcript work was running.
	// OpenCode is already durable; do not turn a transcript timeout into a
	// second failed transaction.
	c.recordBatch(batch)
	var appended bool
	var transcriptAppendErr error
	runWithStatsProfilePhase(parent, "agent_token_collector", "agent_tokens", "transcript_append", func(ctx context.Context) {
		appended, transcriptAppendErr = c.appendCollected(ctx, batch, receipts)
	})
	if !appended {
		c.recordAppendError(transcriptAppendErr, hasMetadataFacts(batch))
	} else {
		c.setSuccess(attemptedAt)
	}
}

// runBoundedAgentInventory calls the inventory cooperatively. Inventory owners
// must honor ctx; spawning a detached timeout goroutine would let a stuck
// provider accumulate one live goroutine per cadence and survive collector
// shutdown.
func runBoundedAgentInventory(ctx context.Context, inventory AgentTokenInventory) ([]AgentRecord, []error, bool) {
	if inventory == nil {
		return nil, []error{errors.New("inventory-unavailable")}, false
	}
	records, errorsOut := inventory(ctx)
	if err := ctx.Err(); err != nil {
		return records, append(errorsOut, err), true
	}
	return records, errorsOut, false
}

func (c *AgentTokenCollector) appendCollected(ctx context.Context, batch AppendBatch, receipts []agentTokenReceipt) (bool, error) {
	if c.writer == nil {
		return false, errors.Join(errors.New("stats writer is unavailable"), rollbackReceipts(receipts))
	}
	result, err := c.writer.AppendBatch(ctx, batch)
	if err != nil {
		if isUnavailableIntervalConflict(err) {
			// A cadence can rediscover an outage interval that an earlier owner
			// already recorded. Preserve usage facts and advance their cursor;
			// the interval itself is idempotent metadata, while other append
			// failures remain strict below.
			usageBatch := AppendBatch{UsageAtoms: batch.UsageAtoms, UsageTombstones: batch.UsageTombstones}
			usageResult, usageErr := c.writer.AppendBatch(ctx, usageBatch)
			if usageErr != nil {
				return false, errors.Join(usageErr, rollbackReceipts(receipts))
			}
			c.counters.usage.Add(uint64(usageResult.UsageAccepted))
			c.counters.usageDuplicates.Add(uint64(usageResult.UsageDuplicate))
			if err := commitReceipts(receipts); err != nil {
				return false, err
			}
			return true, nil
		}
		return false, errors.Join(err, rollbackReceipts(receipts))
	}
	if err := commitReceipts(receipts); err != nil {
		return false, err
	}
	c.counters.usage.Add(uint64(result.UsageAccepted))
	c.counters.usageDuplicates.Add(uint64(result.UsageDuplicate))
	c.counters.coverage.Add(uint64(result.CoverageChanged))
	c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
	return true, nil
}

// appendOpenCodeCollected keeps valid usage facts independent from partial
// history metadata. A bounded OpenCode tree can produce useful fresh atoms
// while its coverage/unavailable intervals are incomplete or conflicting.
// Metadata remains strict, but it must not roll back those atoms.
func (c *AgentTokenCollector) appendOpenCodeCollected(ctx context.Context, batch AppendBatch, receipt *agentTokenReceipt) (bool, bool, error) {
	if c.writer == nil {
		return false, false, errors.Join(errors.New("stats writer is unavailable"), rollbackReceipt(receipt))
	}
	if len(batch.UsageAtoms) > 0 {
		result, err := c.writer.AppendBatch(ctx, AppendBatch{UsageAtoms: batch.UsageAtoms})
		if err != nil {
			return false, false, errors.Join(err, rollbackReceipt(receipt))
		}
		c.counters.usage.Add(uint64(result.UsageAccepted))
		c.counters.usageDuplicates.Add(uint64(result.UsageDuplicate))
	}
	metadata := AppendBatch{CoverageEpochs: batch.CoverageEpochs, UnavailableSpans: batch.UnavailableSpans}
	if len(metadata.CoverageEpochs) > 0 || len(metadata.UnavailableSpans) > 0 {
		result, err := c.writer.AppendBatch(ctx, metadata)
		if err != nil {
			// A recovered source can still carry its original epoch start, which
			// makes the proposed coverage overlap the outage just recorded. Start
			// a new epoch at this observation and retry the metadata transaction;
			// never commit the source cursor while recovery coverage is missing.
			if !isUnavailableIntervalConflict(err) {
				return false, true, errors.Join(err, rollbackReceipt(receipt))
			}
			if len(metadata.CoverageEpochs) == 0 {
				// Usage was committed independently above. An overlapping outage
				// marker is metadata-only and cannot invalidate those usage facts;
				// preserve the existing idempotent behavior by advancing the source
				// cursor while leaving the conflicting marker unchanged.
				if receipt != nil {
					if commitErr := commitOpenCodeReceipt(receipt); commitErr != nil {
						return false, false, commitErr
					}
				}
				return true, false, nil
			}
			startedAt, ok := coverageObservationStart(metadata.CoverageEpochs)
			if !ok {
				return false, true, errors.Join(err, rollbackReceipt(receipt))
			}
			c.rotateCoverageEpoch(startedAt)
			for index := range metadata.CoverageEpochs {
				metadata.CoverageEpochs[index].EpochID = c.config.EpochID
				metadata.CoverageEpochs[index].StartedAt = startedAt
			}
			result, err = c.writer.AppendBatch(ctx, metadata)
			if err != nil {
				return false, true, errors.Join(err, rollbackReceipt(receipt))
			}
		}
		c.counters.coverage.Add(uint64(result.CoverageChanged))
		c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
	}
	if receipt != nil {
		if err := commitOpenCodeReceipt(receipt); err != nil {
			return false, false, err
		}
	}
	return true, false, nil
}

func coverageObservationStart(epochs []CoverageEpoch) (float64, bool) {
	if len(epochs) == 0 {
		return 0, false
	}
	startedAt := math.Inf(1)
	for _, epoch := range epochs {
		if epoch.EndedAt == nil || epoch.NativeCadenceSeconds <= 0 {
			return 0, false
		}
		observedAt := *epoch.EndedAt - epoch.NativeCadenceSeconds
		if observedAt < startedAt {
			startedAt = observedAt
		}
	}
	return startedAt, !math.IsInf(startedAt, 1) && !math.IsNaN(startedAt)
}

func (c *AgentTokenCollector) rotateCoverageEpoch(startedAt float64) {
	nanoseconds := int64(startedAt * float64(time.Second))
	suffix := "-recovery-" + strconv.FormatInt(nanoseconds, 10)
	base := truncateAgentTokenEpochID(c.epochBaseID, maxAgentTokenEpochIDBytes-len(suffix))
	c.config.EpochID = base + suffix
	c.config.EpochStartedAt = startedAt
}

func commitOpenCodeReceipt(receipt *agentTokenReceipt) error {
	if err := receipt.commit(); err != nil {
		return errors.Join(err, rollbackReceipt(receipt))
	}
	return nil
}

func retryCodexForkRepairBlockedFiles(catalog *codexForkRepairCatalog) {
	if catalog == nil || len(catalog.blockedFiles) == 0 {
		return
	}
	catalog.pendingFiles = append(catalog.pendingFiles, catalog.blockedFiles...)
	catalog.blockedFiles = nil
	// Byte-budget-blocked headers are recoverable on the next collector run,
	// which may use a larger budget. Reprocessing the file will re-establish a
	// block if the condition still exists.
	catalog.blocked = false
}

func isUnavailableIntervalConflict(err error) bool {
	if err == nil {
		return false
	}
	message := err.Error()
	return strings.Contains(message, "unavailable spans overlap") ||
		strings.Contains(message, "coverage epoch overlaps an unavailable span") ||
		strings.Contains(message, "unavailable span overlaps a coverage epoch")
}

type agentTokenReceipt struct {
	commit   func() error
	rollback func() error
}

func rollbackReceipt(receipt *agentTokenReceipt) error {
	if receipt == nil || receipt.rollback == nil {
		return nil
	}
	return receipt.rollback()
}

func rollbackReceipts(receipts []agentTokenReceipt) error {
	var errs []error
	for index := range receipts {
		if receipts[index].rollback == nil {
			continue
		}
		if err := receipts[index].rollback(); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

func commitReceipts(receipts []agentTokenReceipt) error {
	var errs []error
	for index := range receipts {
		if receipts[index].commit == nil {
			continue
		}
		if err := receipts[index].commit(); err != nil {
			errs = append(errs, err)
			if receipts[index].rollback != nil {
				if rollbackErr := receipts[index].rollback(); rollbackErr != nil {
					errs = append(errs, rollbackErr)
				}
			}
		}
	}
	return errors.Join(errs...)
}

func (c *AgentTokenCollector) collect(parent context.Context, observed, cadence float64) (batch AppendBatch, receipts []agentTokenReceipt, err error) {
	defer func() { deduplicateUnavailableSpans(&batch) }()
	if c.config.Inventory == nil {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "agent_tokens", SourceID: c.config.SourceID, EpochID: c.config.EpochID, StartedAt: observed, EndedAt: observed + cadence, NativeCadenceSeconds: cadence, Reason: "inventory-unavailable", OwnerGeneration: c.config.OwnerGeneration}}}, nil, nil
	}
	records, inventoryErrors := c.config.Inventory(parent)
	if c.config.RequireTmuxPane {
		records = tmuxScopedAgentRecords(records)
	}
	if len(records) > maxAgentTokenSources {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "agent_tokens", SourceID: c.config.SourceID, EpochID: c.config.EpochID, StartedAt: observed, EndedAt: observed + cadence, NativeCadenceSeconds: cadence, Reason: "inventory-bound-exceeded", OwnerGeneration: c.config.OwnerGeneration}}}, nil, nil
	}
	batch = AppendBatch{}
	receipts = []agentTokenReceipt{}
	// OpenCode is a database-backed source and must not wait behind a recursive
	// scan of potentially multi-gigabyte Claude/Codex transcript roots.
	if c.config.OpenCodeDatabase != "" {
		openBatch, receipt, openErr := c.collectOpenCode(parent, observed, cadence, records)
		if openErr != nil {
			return AppendBatch{}, nil, openErr
		}
		batch.UsageAtoms = append(batch.UsageAtoms, openBatch.UsageAtoms...)
		batch.CoverageEpochs = append(batch.CoverageEpochs, openBatch.CoverageEpochs...)
		batch.UnavailableSpans = append(batch.UnavailableSpans, openBatch.UnavailableSpans...)
		if receipt != nil {
			receipts = append(receipts, *receipt)
		}
	}
	if c.config.SkipTranscriptScan {
		c.applyUsagePricingProfiles(&batch)
		return batch, receipts, nil
	}
	batch, receipts, err = c.collectTranscripts(parent, observed, cadence, records, inventoryErrors, batch, receipts)
	if err == nil {
		c.applyUsagePricingProfiles(&batch)
	}
	return batch, receipts, err
}

func (c *AgentTokenCollector) applyUsagePricingProfiles(batch *AppendBatch) {
	if c.config.UsagePricingProfile == nil {
		return
	}
	for index := range batch.UsageAtoms {
		atom := &batch.UsageAtoms[index]
		current, _ := atom.Payload["pricing_profile"].(string)
		if current != "" && current != "default" {
			continue
		}
		profile := c.config.UsagePricingProfile(*atom)
		if profile == "default" || profile == "subscription" {
			atom.Payload["pricing_profile"] = profile
		}
	}
}

func transcriptSourceIsLive(source TranscriptSource, state TranscriptScannerState, currentSize int64) bool {
	if state.Path != "" && state.UsageCommittedEOFKnown && currentSize > state.UsageCommittedEOFSize && state.Offset >= state.UsageCommittedEOFSize {
		return true
	}
	if currentSize > state.FileSize && state.Offset == state.FileSize && len(state.PendingLine) == 0 && state.Path != "" {
		return true
	}
	if source.LiveCandidate && !source.CodexForkRepairOnly &&
		source.Provider == "codex" && currentSize > state.Offset &&
		((state.Offset == 0 && state.FileSize == 0) || (state.Offset > 0 && currentSize > state.FileSize)) {
		return true
	}
	return false
}

func rotateTranscriptSources(sources []TranscriptSource, startKey string) []TranscriptSource {
	if len(sources) < 2 || startKey == "" {
		return sources
	}
	for index, source := range sources {
		if transcriptCursorKey(source) == startKey {
			rotated := make([]TranscriptSource, 0, len(sources))
			rotated = append(rotated, sources[index:]...)
			rotated = append(rotated, sources[:index]...)
			return rotated
		}
	}
	return sources
}

func boundedTranscriptSourcePage(sources []TranscriptSource, startKey string, limit int) (ordered, page []TranscriptSource) {
	ordered = rotateTranscriptSources(sources, startKey)
	if limit <= 0 || len(ordered) <= limit {
		return ordered, ordered
	}
	return ordered, ordered[:limit]
}

func nextTranscriptSourceKey(sources []TranscriptSource, currentKey string) string {
	for index, source := range sources {
		if transcriptCursorKey(source) == currentKey {
			return transcriptCursorKey(sources[(index+1)%len(sources)])
		}
	}
	return ""
}

func boundedCodexForkRepairSources(sources []TranscriptSource, startKey string, limit int) ([]TranscriptSource, string) {
	rotated := rotateTranscriptSources(sources, startKey)
	if len(rotated) <= limit {
		return rotated, ""
	}
	next := transcriptCursorKey(rotated[limit])
	return rotated[:limit], next
}

func transcriptSourceInOrder(source TranscriptSource, ordered []TranscriptSource) bool {
	key := transcriptCursorKey(source)
	for _, candidate := range ordered {
		if transcriptCursorKey(candidate) == key {
			return true
		}
	}
	return false
}

func (c *AgentTokenCollector) collectTranscripts(parent context.Context, observed, cadence float64, records []AgentRecord, inventoryErrors []error, batch AppendBatch, receipts []agentTokenReceipt) (AppendBatch, []agentTokenReceipt, error) {
	type transcriptCandidate struct {
		source   TranscriptSource
		result   TranscriptScanResult
		change   transcriptCursorChange
		changed  bool
		coverage bool
	}
	discoveryCtx, discoveryCancel := context.WithTimeout(parent, c.config.TranscriptDiscoveryTimeout)
	sources, err := c.config.TranscriptSources(discoveryCtx, records)
	discoveryCancel()
	if err != nil {
		// Discovery budgets describe deferred backlog, not an outage. Keep the
		// candidates returned before the bound and let their bounded scanners
		// contribute current usage without recording a false unavailable span.
		if !isTranscriptDiscoveryBudget(err) {
			reason := err.Error()
			if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
				reason = "transcript-discovery-timeout"
			}
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, reason))
		}
	}
	completedRepairRoots, markerErr := c.transcriptCursor.CompletedCodexForkRepairRoots()
	repairSourcesByRoot := make(map[string][]TranscriptSource)
	repairDiscoveryComplete := make(map[string]bool)
	var allRepairSources []TranscriptSource
	if markerErr != nil {
		batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "codex-fork-repair", observed, cadence, "transcript-cursor-"+markerErr.Error()))
	} else {
		rootPaths := make(map[string]string)
		for _, source := range sources {
			if source.Provider != "codex" {
				continue
			}
			root := codexSessionsRoot(source.Path)
			if root == "" && c.config.CodexTranscriptRoot != "" {
				root = filepath.Clean(c.config.CodexTranscriptRoot)
			}
			if root != "" {
				rootPaths[filepath.Clean(root)] = root
			}
		}
		orderedRoots := make([]string, 0, len(rootPaths))
		for root := range rootPaths {
			orderedRoots = append(orderedRoots, root)
		}
		activeRepairRootCount := 0
		for _, root := range orderedRoots {
			if _, complete := completedRepairRoots[codexForkRepairRootID(root)]; !complete {
				activeRepairRootCount++
			}
		}
		var boundErr error
		orderedRoots, boundErr = c.boundCodexForkRepairRoots(orderedRoots, completedRepairRoots)
		if boundErr != nil {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "codex-fork-repair", observed, cadence, "transcript-cursor-frontier-"+boundErr.Error()))
		}
		nextRootAfterBound := c.nextCodexForkRepairRoot
		repairCtx, repairCancel := context.WithTimeout(parent, c.config.CodexForkRepairDiscoveryTimeout)
		for rootIndex, root := range orderedRoots {
			rootID := codexForkRepairRootID(root)
			if _, complete := completedRepairRoots[rootID]; complete {
				continue
			}
			repairSourcesByRoot[rootID] = nil
			candidates, discoveryComplete, _, discoveryErr := c.codexForkRepairSources(repairCtx, root, rootID)
			repairDiscoveryComplete[rootID] = discoveryComplete
			if discoveryErr != nil {
				reason := "codex-fork-repair-discovery-unavailable"
				if errors.Is(discoveryErr, context.DeadlineExceeded) || errors.Is(discoveryErr, context.Canceled) || repairCtx.Err() != nil {
					reason = "codex-fork-repair-discovery-timeout"
				}
				batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "codex-fork-repair", observed, cadence, reason))
			}
			repairSourcesByRoot[rootID] = candidates
			allRepairSources = append(allRepairSources, candidates...)
			if repairCtx.Err() != nil {
				nextRoot := nextRootAfterBound
				if activeRepairRootCount <= maxCodexForkRepairCatalogRoots {
					if rootIndex+1 < len(orderedRoots) {
						nextRoot = orderedRoots[rootIndex+1]
					} else if len(orderedRoots) > 0 {
						nextRoot = orderedRoots[0]
					}
				}
				c.codexForkRepairMu.Lock()
				previousNextRoot := c.nextCodexForkRepairRoot
				c.nextCodexForkRepairRoot = nextRoot
				if persistErr := c.saveCodexForkRepairFrontierLocked(); persistErr != nil {
					c.nextCodexForkRepairRoot = previousNextRoot
					batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "codex-fork-repair", observed, cadence, "transcript-cursor-frontier-"+persistErr.Error()))
				}
				c.codexForkRepairMu.Unlock()
				break
			}
		}
		repairCancel()
	}
	schedule, scheduleErr := c.transcriptCursor.Schedule()
	if scheduleErr != nil {
		batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, "transcript-cursor-schedule-"+scheduleErr.Error()))
		schedule = transcriptCursorSchedule{}
	}
	nextSource := schedule.NextSource
	normalSourceOrder, boundedSources := boundedTranscriptSourcePage(sources, nextSource, len(sources))
	c.liveTranscriptPriorityRuns = schedule.LiveTranscriptPriorityRuns
	if len(allRepairSources) > 0 {
		// Reserve up to one quarter of the bounded source slots for the
		// one-time repair, even when ordinary sources fill their cap.
		repairSlots := maxAgentTokenSources / 4
		if repairSlots < 1 {
			repairSlots = 1
		}
		if repairSlots > len(allRepairSources) {
			repairSlots = len(allRepairSources)
		}
		if freeSlots := maxAgentTokenSources - len(sources); freeSlots >= 0 && freeSlots < repairSlots && freeSlots > 0 {
			repairSlots = freeSlots
		}
		normalLimit := maxAgentTokenSources - repairSlots
		if len(normalSourceOrder) > normalLimit {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, "agent-token-source-bound-exceeded"))
			normalSourceOrder, boundedSources = boundedTranscriptSourcePage(normalSourceOrder, "", normalLimit)
		}
		c.codexForkRepairMu.Lock()
		repairStart := c.nextCodexForkRepairSource
		c.codexForkRepairMu.Unlock()
		allRepairSources, nextRepairSource := boundedCodexForkRepairSources(allRepairSources, repairStart, repairSlots)
		c.codexForkRepairMu.Lock()
		c.nextCodexForkRepairSource = nextRepairSource
		persistErr := c.saveCodexForkRepairFrontierLocked()
		if persistErr != nil {
			c.nextCodexForkRepairSource = repairStart
		}
		c.codexForkRepairMu.Unlock()
		if persistErr != nil {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "codex-fork-repair", observed, cadence, "transcript-cursor-frontier-"+persistErr.Error()))
		}
		boundedSources = append(boundedSources, allRepairSources...)
	} else if len(normalSourceOrder) > maxAgentTokenSources {
		batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, "agent-token-source-bound-exceeded"))
		normalSourceOrder, boundedSources = boundedTranscriptSourcePage(normalSourceOrder, "", maxAgentTokenSources)
	}
	sources = boundedSources
	states := make(map[string]*TranscriptScannerState, len(sources))
	liveSources := make([]TranscriptSource, 0, len(sources))
	historicalSources := make([]TranscriptSource, 0, len(sources))
	for _, source := range sources {
		key := transcriptCursorKey(source)
		state := c.transcriptStates[key]
		if state == nil {
			persisted, stateErr := c.transcriptCursor.State(source)
			if stateErr != nil {
				// Keep the source in the bounded scan order so the ordinary source
				// path records the typed cursor-unavailable result below.
				historicalSources = append(historicalSources, source)
				continue
			}
			state = &persisted
			c.transcriptStates[key] = state
		}
		states[key] = state
		info, statErr := os.Stat(source.Path)
		if statErr == nil && transcriptSourceIsLive(source, *state, info.Size()) {
			liveSources = append(liveSources, source)
		} else {
			historicalSources = append(historicalSources, source)
		}
	}
	liveSources = rotateTranscriptSources(liveSources, nextSource)
	historicalStart := schedule.NextHistoricalSource
	if historicalStart == "" {
		historicalStart = c.nextHistoricalTranscript
	}
	if historicalStart == "" {
		historicalStart = nextSource
	}
	historicalSources = rotateTranscriptSources(historicalSources, historicalStart)
	sources = append(liveSources, historicalSources...)
	if len(liveSources) > 0 && len(historicalSources) > 0 && c.liveTranscriptPriorityRuns >= maxConsecutiveLiveTranscriptPriorityRuns {
		fairOrder := make([]TranscriptSource, 0, len(sources))
		fairOrder = append(fairOrder, historicalSources[0])
		fairOrder = append(fairOrder, liveSources...)
		fairOrder = append(fairOrder, historicalSources[1:]...)
		sources = fairOrder
	}
	candidates := make([]transcriptCandidate, 0, len(sources))
	cycleMaxBytes, cycleMaxRecords := transcriptCycleBudgets(
		c.config.TranscriptMaxBytesPerCycle,
		c.config.TranscriptMaxRecordsPerCycle,
		len(repairDiscoveryComplete) > 0,
	)
	var bytesScanned int64
	var recordsScanned int
	lastScanned := -1
	lastNormalSourceKey := ""
	liveWork, historicalWork := false, false
	historicalAttempted := false
	proposedNextHistoricalTranscript := historicalStart
	for sourceIndex, source := range sources {
		remainingBytes := cycleMaxBytes - bytesScanned
		remainingRecords := cycleMaxRecords - recordsScanned
		if remainingBytes <= 0 || remainingRecords <= 0 {
			break
		}
		if err := parent.Err(); err != nil {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, "transcript-discovery-timeout"))
			break
		}
		stateKey := transcriptCursorKey(source)
		if !source.CodexForkRepairOnly {
			lastNormalSourceKey = stateKey
		}
		isHistorical := transcriptSourceInOrder(source, historicalSources)
		if isHistorical {
			historicalAttempted = true
			for index, historical := range historicalSources {
				if transcriptCursorKey(historical) == stateKey {
					proposedNextHistoricalTranscript = transcriptCursorKey(historicalSources[(index+1)%len(historicalSources)])
					break
				}
			}
		}
		state := states[stateKey]
		if state == nil {
			state = c.transcriptStates[stateKey]
		}
		if state == nil {
			persisted, stateErr := c.transcriptCursor.State(source)
			if stateErr != nil {
				batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, source.SourceID, observed, cadence, "transcript-cursor-"+stateErr.Error()))
				continue
			}
			state = &persisted
			c.transcriptStates[stateKey] = state
		}
		result, scanErr := ScanTranscript(source, state, TranscriptScanOptions{Context: parent, MaxBytes: remainingBytes, MaxRecords: remainingRecords})
		if scanErr != nil {
			reason := scanErr.Error()
			if errors.Is(scanErr, context.Canceled) || errors.Is(scanErr, context.DeadlineExceeded) {
				reason = "transcript-discovery-timeout"
			}
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, source.SourceID, observed, cadence, reason))
			if parent.Err() != nil {
				break
			}
			continue
		}
		bytesScanned += result.BytesRead
		recordsScanned += result.RecordsParsed
		lastScanned = sourceIndex
		if result.BytesRead > 0 || result.RecordsParsed > 0 {
			if isHistorical {
				historicalWork = true
			} else {
				liveWork = true
			}
		}
		if result.Receipt != nil {
			changed := !reflect.DeepEqual(result.Receipt.before, result.Receipt.after)
			if !changed {
				result.Receipt = nil
			}
		}
		candidate := transcriptCandidate{source: source, result: result, changed: result.Receipt != nil, coverage: result.RecordsParsed > 0 || result.Reset || state.Offset == 0}
		if candidate.changed {
			candidate.change = transcriptCursorChange{source: source, before: result.Receipt.before, after: result.Receipt.after}
		}
		candidates = append(candidates, candidate)
	}
	tombstonesByKey := make(map[string]UsageTombstone)
	orderedTombstoneKeys := make([]string, 0)
	tombstoneRootsByKey := make(map[string]map[string]struct{})
	conflictingTombstoneKeys := make(map[string]struct{})
	conflictingRepairRoots := make(map[string]struct{})
	for index := range candidates {
		candidate := &candidates[index]
		for _, tombstone := range candidate.result.Tombstones {
			key := tombstone.IdempotencyKey()
			root := codexSessionsRoot(candidate.source.Path)
			if root == "" && c.config.CodexTranscriptRoot != "" {
				root = filepath.Clean(c.config.CodexTranscriptRoot)
			}
			rootID := ""
			if root != "" {
				rootID = codexForkRepairRootID(root)
			}
			if previous, exists := tombstonesByKey[key]; exists {
				if previous != tombstone {
					conflictingTombstoneKeys[key] = struct{}{}
					for previousRoot := range tombstoneRootsByKey[key] {
						conflictingRepairRoots[previousRoot] = struct{}{}
					}
					if rootID != "" {
						conflictingRepairRoots[rootID] = struct{}{}
					}
				}
			} else {
				tombstonesByKey[key] = tombstone
				orderedTombstoneKeys = append(orderedTombstoneKeys, key)
			}
			if rootID != "" {
				if tombstoneRootsByKey[key] == nil {
					tombstoneRootsByKey[key] = make(map[string]struct{})
				}
				tombstoneRootsByKey[key][rootID] = struct{}{}
			}
		}
	}
	if len(conflictingTombstoneKeys) > 0 {
		batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "codex-fork-repair", observed, cadence, "transcript-tombstone-conflict"))
	}
	changedCandidates := make([]*transcriptCandidate, 0, len(candidates))
	changes := make([]transcriptCursorChange, 0, len(candidates))
	for index := range candidates {
		candidate := &candidates[index]
		if !candidate.changed {
			continue
		}
		if candidate.source.CodexForkRepairOnly {
			root := codexSessionsRoot(candidate.source.Path)
			if root == "" && c.config.CodexTranscriptRoot != "" {
				root = filepath.Clean(c.config.CodexTranscriptRoot)
			}
			if root != "" {
				if _, conflicted := conflictingRepairRoots[codexForkRepairRootID(root)]; conflicted {
					candidate.result.Receipt.Rollback()
					continue
				}
			}
		}
		changedCandidates = append(changedCandidates, candidate)
		changes = append(changes, candidate.change)
	}
	prepared := make(map[string]struct{}, len(changedCandidates))
	schedulePrepared := false
	nextSourceAfter := ""
	if lastScanned >= 0 && lastNormalSourceKey != "" {
		nextSourceAfter = nextTranscriptSourceKey(normalSourceOrder, lastNormalSourceKey)
	}
	proposedLiveTranscriptPriorityRuns := c.liveTranscriptPriorityRuns
	if historicalWork {
		proposedLiveTranscriptPriorityRuns = 0
	} else if liveWork && len(historicalSources) > 0 {
		if proposedLiveTranscriptPriorityRuns < maxConsecutiveLiveTranscriptPriorityRuns {
			proposedLiveTranscriptPriorityRuns++
		}
	} else if len(historicalSources) == 0 {
		proposedLiveTranscriptPriorityRuns = 0
	}
	if len(changes) > 0 || lastScanned >= 0 || historicalAttempted {
		var prepareErr error
		if lastScanned >= 0 && len(historicalSources) > 0 {
			prepareErr = c.transcriptCursor.PrepareBatchWithSchedules(changes, nextSourceAfter, proposedNextHistoricalTranscript, proposedLiveTranscriptPriorityRuns)
		} else if historicalAttempted {
			prepareErr = c.transcriptCursor.PrepareBatchWithHistoricalSchedule(changes, proposedNextHistoricalTranscript, proposedLiveTranscriptPriorityRuns)
		} else if lastScanned >= 0 {
			prepareErr = c.transcriptCursor.PrepareBatchWithNextSource(changes, nextSourceAfter)
		} else {
			prepareErr = c.transcriptCursor.PrepareBatch(changes)
		}
		if prepareErr == nil {
			schedulePrepared = lastScanned >= 0 || historicalAttempted
			for _, candidate := range changedCandidates {
				prepared[transcriptCursorKey(candidate.source)] = struct{}{}
			}
		} else {
			// A concurrent owner or stale sidecar should only defer the affected
			// source. The normal path is one batch write; this per-source fallback
			// keeps the old conflict behavior without turning a rare race into a
			// loss of all transcript observations.
			fallbackChanges := make([]transcriptCursorChange, 0, len(changedCandidates))
			for _, candidate := range changedCandidates {
				if prepareErr := c.transcriptCursor.Prepare(candidate.source, candidate.change.before, candidate.change.after); prepareErr != nil {
					candidate.result.Receipt.Rollback()
					batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, candidate.source.SourceID, observed, cadence, "transcript-cursor-"+prepareErr.Error()))
					continue
				}
				prepared[transcriptCursorKey(candidate.source)] = struct{}{}
				fallbackChanges = append(fallbackChanges, candidate.change)
			}
			if len(fallbackChanges) > 0 {
				var scheduleErr error
				if lastScanned >= 0 && len(historicalSources) > 0 {
					scheduleErr = c.transcriptCursor.PrepareBatchWithSchedules(nil, nextSourceAfter, proposedNextHistoricalTranscript, proposedLiveTranscriptPriorityRuns)
				} else if historicalAttempted {
					scheduleErr = c.transcriptCursor.PrepareBatchWithHistoricalSchedule(nil, proposedNextHistoricalTranscript, proposedLiveTranscriptPriorityRuns)
				} else {
					scheduleErr = c.transcriptCursor.PrepareBatchWithNextSource(nil, nextSourceAfter)
				}
				if scheduleErr != nil {
					rollbackErr := c.transcriptCursor.RollbackBatch(fallbackChanges)
					for _, candidate := range changedCandidates {
						if _, ok := prepared[transcriptCursorKey(candidate.source)]; ok {
							candidate.result.Receipt.Rollback()
							delete(prepared, transcriptCursorKey(candidate.source))
							batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, candidate.source.SourceID, observed, cadence, "transcript-cursor-schedule-"+scheduleErr.Error()))
							if rollbackErr != nil {
								batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, candidate.source.SourceID, observed, cadence, "transcript-cursor-rollback-"+rollbackErr.Error()))
							}
						}
					}
				} else {
					schedulePrepared = true
				}
			}
			if len(changes) == 0 && !schedulePrepared {
				batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, "transcript-cursor-schedule-"+prepareErr.Error()))
			}
		}
	}
	preparedChanges := make([]transcriptCursorChange, 0, len(prepared))
	preparedReceipts := make([]*TranscriptScanReceipt, 0, len(prepared))
	repairAfter := make(map[string]TranscriptScannerState, len(candidates))
	orderedTombstones := make([]UsageTombstone, 0, len(orderedTombstoneKeys))
	for _, key := range orderedTombstoneKeys {
		if _, conflicted := conflictingTombstoneKeys[key]; conflicted {
			continue
		}
		rootConflicted := false
		for rootID := range tombstoneRootsByKey[key] {
			if _, conflicted := conflictingRepairRoots[rootID]; conflicted {
				rootConflicted = true
				break
			}
		}
		if !rootConflicted {
			orderedTombstones = append(orderedTombstones, tombstonesByKey[key])
		}
	}
	for index := range candidates {
		candidate := &candidates[index]
		if candidate.source.CodexForkRepairOnly && candidate.result.Receipt != nil {
			repairAfter[transcriptCursorKey(candidate.source)] = candidate.result.Receipt.after
		}
		if candidate.changed {
			if _, ok := prepared[transcriptCursorKey(candidate.source)]; !ok {
				continue
			}
			preparedChanges = append(preparedChanges, candidate.change)
			preparedReceipts = append(preparedReceipts, candidate.result.Receipt)
		}
		batch.UsageAtoms = append(batch.UsageAtoms, candidate.result.Atoms...)
		if candidate.coverage {
			batch.CoverageEpochs = append(batch.CoverageEpochs, agentTokenCoverage(c.config, candidate.source.SourceID, observed, cadence))
		}
	}
	batch.UsageTombstones = append(batch.UsageTombstones, orderedTombstones...)
	completedRootsToMark := make([]string, 0, len(repairSourcesByRoot))
	for rootID, rootSources := range repairSourcesByRoot {
		if !repairDiscoveryComplete[rootID] {
			continue
		}
		if _, conflicted := conflictingRepairRoots[rootID]; conflicted {
			continue
		}
		complete := true
		for _, source := range rootSources {
			key := transcriptCursorKey(source)
			state := states[key]
			if state == nil {
				persisted, stateErr := c.transcriptCursor.State(source)
				if stateErr != nil {
					complete = false
					break
				}
				state = &persisted
			}
			proposed, scanned := repairAfter[key]
			if scanned {
				if _, isPrepared := prepared[key]; isPrepared {
					state = &proposed
				} else if !reflect.DeepEqual(state, &proposed) {
					complete = false
					break
				}
			}
			if !state.CodexForkRepairComplete {
				complete = false
				break
			}
		}
		if complete {
			completedRootsToMark = append(completedRootsToMark, rootID)
		}
	}
	sort.Strings(completedRootsToMark)
	if len(preparedChanges) > 0 || schedulePrepared || len(completedRootsToMark) > 0 {
		receipts = append(receipts, agentTokenReceipt{
			commit: func() error {
				// appendCollected has already committed usage facts. Promote the
				// cursors and completion roots in one atomic sidecar write so a failed
				// cursor commit cannot leave a durable marker that suppresses retry.
				if err := c.transcriptCursor.CommitBatchWithCodexForkRepairRoots(preparedChanges, completedRootsToMark); err != nil {
					return err
				}
				for _, receipt := range preparedReceipts {
					receipt.Commit()
				}
				c.nextHistoricalTranscript = proposedNextHistoricalTranscript
				c.liveTranscriptPriorityRuns = proposedLiveTranscriptPriorityRuns
				c.codexForkRepairMu.Lock()
				for _, rootID := range completedRootsToMark {
					delete(c.codexForkRepairCatalog, rootID)
				}
				c.codexForkRepairMu.Unlock()
				return nil
			},
			rollback: func() error {
				for _, receipt := range preparedReceipts {
					receipt.Rollback()
				}
				err := c.transcriptCursor.RollbackBatch(preparedChanges)
				// A failed commit can be ambiguous after the sidecar rename: the
				// disk may already contain the promoted cursor even though the
				// directory sync failed. Even a no-op rollback cannot distinguish
				// that case, so reload these cursors from the sidecar next cycle.
				for _, change := range preparedChanges {
					delete(c.transcriptStates, transcriptCursorKey(change.source))
				}
				return err
			},
		})
	}
	for _, inventoryErr := range inventoryErrors {
		batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "inventory", observed, cadence, inventoryErr.Error()))
	}
	deduplicateUnavailableSpans(&batch)
	return batch, receipts, nil
}

func isTranscriptDiscoveryBudget(err error) bool {
	if err == nil {
		return false
	}
	message := err.Error()
	return strings.Contains(message, "transcript-discovery-file-budget-exhausted") ||
		strings.Contains(message, "transcript-discovery-directory-budget-exhausted") ||
		strings.Contains(message, "transcript-discovery-entry-budget-exhausted") ||
		strings.Contains(message, "transcript-discovery-byte-budget-exhausted") ||
		strings.Contains(message, "transcript-discovery-frontier-budget-exceeded") ||
		errors.Is(err, errTranscriptDiscoveryDirectoryChanged)
}

func codexSessionsRoot(path string) string {
	for current := filepath.Clean(path); current != "." && current != string(filepath.Separator); current = filepath.Dir(current) {
		if filepath.Base(current) == "sessions" {
			return current
		}
		parent := filepath.Dir(current)
		if parent == current {
			break
		}
	}
	return ""
}

func codexForkRepairRootID(root string) string {
	absolute, err := filepath.Abs(filepath.Clean(root))
	if err != nil {
		absolute = filepath.Clean(root)
	}
	digest := sha256.Sum256([]byte(absolute))
	return hex.EncodeToString(digest[:])
}

func newCodexForkRepairDirectoryCursor(root, path string) (*codexForkRepairDirectoryState, error) {
	file, info, err := openCodexForkRepairPathNoSymlinks(root, path, true)
	if err != nil {
		return nil, fmt.Errorf("open Codex repair directory %q: %w", path, err)
	}
	defer file.Close()
	device, inode := fileIdentity(info)
	return &codexForkRepairDirectoryState{
		Path: filepath.Clean(path), CursorVersion: 2, CursorPlatform: runtime.GOOS, Device: device, Inode: inode,
		ModTimeNS: info.ModTime().UnixNano(), ChangeTimeNS: codexDirectoryChangeTimeNS(info),
	}, nil
}

func readCodexForkRepairDirectoryPage(root string, state *codexForkRepairDirectoryState) ([]string, bool, error) {
	if state == nil {
		return nil, false, fmt.Errorf("Codex repair directory cursor is nil")
	}
	file, info, err := openCodexForkRepairPathNoSymlinks(root, state.Path, true)
	if err != nil {
		return nil, false, fmt.Errorf("reopen Codex repair directory %q: %w", state.Path, err)
	}
	defer file.Close()
	device, inode := fileIdentity(info)
	changeTime := codexDirectoryChangeTimeNS(info)
	if state.CursorVersion != 2 || state.CursorPlatform != runtime.GOOS || (state.Device != 0 && (state.Device != device || state.Inode != inode || state.ModTimeNS != info.ModTime().UnixNano() || state.ChangeTimeNS != changeTime)) {
		state.Cookie = 0
		state.PendingNames = nil
	}
	state.CursorVersion = 2
	state.CursorPlatform = runtime.GOOS
	state.Device = device
	state.Inode = inode
	state.ModTimeNS = info.ModTime().UnixNano()
	state.ChangeTimeNS = changeTime
	names, cookie, eof, err := readCodexDirectoryPage(file, state.Cookie)
	if err != nil {
		return nil, false, err
	}
	updated, err := file.Stat()
	if err != nil {
		return nil, false, fmt.Errorf("restat Codex repair directory after page read %q: %w", state.Path, err)
	}
	updatedDevice, updatedInode := fileIdentity(updated)
	updatedChangeTime := codexDirectoryChangeTimeNS(updated)
	if device != updatedDevice || inode != updatedInode || info.ModTime() != updated.ModTime() || changeTime != updatedChangeTime {
		// A page from a changing directory is ambiguous. Discard it and replay
		// from the beginning so additions or renames cannot be hidden by a stale
		// continuation count.
		state.Cookie = 0
		state.PendingNames = nil
		state.ModTimeNS = updated.ModTime().UnixNano()
		state.ChangeTimeNS = updatedChangeTime
		state.Device = updatedDevice
		state.Inode = updatedInode
		return nil, false, nil
	}
	state.Cookie = cookie
	filtered := names[:0]
	for _, name := range names {
		if name == "" || name == "." || name == ".." || strings.ContainsAny(name, `/\\`) {
			continue
		}
		filtered = append(filtered, name)
	}
	return filtered, eof, nil
}

func (c *AgentTokenCollector) loadCodexForkRepairFrontier() error {
	c.codexForkRepairMu.Lock()
	defer c.codexForkRepairMu.Unlock()
	if c.codexForkRepairLoaded {
		return nil
	}
	frontier, err := c.transcriptCursor.CodexForkRepairFrontier()
	if err != nil {
		return err
	}
	c.codexForkRepairCatalog = make(map[string]*codexForkRepairCatalog, len(frontier.Roots))
	c.nextCodexForkRepairRoot = frontier.NextRoot
	c.nextCodexForkRepairSource = frontier.NextSource
	for rootID, state := range frontier.Roots {
		catalog := &codexForkRepairCatalog{
			root: state.Root, pendingDirs: append([]string(nil), state.PendingDirs...),
			pendingFiles: append([]string(nil), state.PendingFiles...), blockedFiles: append([]string(nil), state.BlockedFiles...), sources: make(map[string]TranscriptSource, len(state.Sources)),
			directory: state.Directory, pausedDirectories: append([]codexForkRepairDirectoryState(nil), state.PausedDirectories...),
			blocked: state.Blocked, initialized: state.Initialized, complete: state.Complete,
		}
		for _, source := range state.Sources {
			catalog.sources[source.Path] = source
		}
		// A process can exit before Stop promotes over-budget files back into
		// the retry queue. Recover those entries at load so a fresh collector can
		// retry them under its current discovery budget.
		retryCodexForkRepairBlockedFiles(catalog)
		c.codexForkRepairCatalog[rootID] = catalog
	}
	c.codexForkRepairLoaded = true
	return nil
}

func (c *AgentTokenCollector) saveCodexForkRepairFrontierLocked() error {
	frontier := codexForkRepairFrontierState{
		NextRoot: c.nextCodexForkRepairRoot, NextSource: c.nextCodexForkRepairSource,
		Roots: make(map[string]codexForkRepairCatalogState, len(c.codexForkRepairCatalog)),
	}
	for rootID, catalog := range c.codexForkRepairCatalog {
		state := codexForkRepairCatalogState{
			Root: catalog.root, PendingDirs: append([]string(nil), catalog.pendingDirs...),
			PendingFiles: append([]string(nil), catalog.pendingFiles...), BlockedFiles: append([]string(nil), catalog.blockedFiles...), Directory: catalog.directory,
			PausedDirectories: append([]codexForkRepairDirectoryState(nil), catalog.pausedDirectories...),
			Blocked:           catalog.blocked, Initialized: catalog.initialized, Complete: catalog.complete,
		}
		for _, source := range catalog.sources {
			state.Sources = append(state.Sources, source)
		}
		sort.Slice(state.Sources, func(i, j int) bool { return state.Sources[i].Path < state.Sources[j].Path })
		frontier.Roots[rootID] = state
	}
	return c.transcriptCursor.SaveCodexForkRepairFrontier(frontier)
}

func (c *AgentTokenCollector) persistCodexForkRepairFrontier() error {
	if err := c.loadCodexForkRepairFrontier(); err != nil {
		return err
	}
	c.codexForkRepairMu.Lock()
	defer c.codexForkRepairMu.Unlock()
	return c.saveCodexForkRepairFrontierLocked()
}

func (c *AgentTokenCollector) boundCodexForkRepairRoots(roots []string, completed map[string]struct{}) ([]string, error) {
	if err := c.loadCodexForkRepairFrontier(); err != nil {
		return nil, err
	}
	active := make([]string, 0, len(roots))
	for _, root := range roots {
		if _, done := completed[codexForkRepairRootID(root)]; !done {
			active = append(active, filepath.Clean(root))
		}
	}
	sort.Strings(active)
	c.codexForkRepairMu.Lock()
	defer c.codexForkRepairMu.Unlock()
	previousCatalog := cloneCodexForkRepairCatalogs(c.codexForkRepairCatalog)
	previousNextRoot := c.nextCodexForkRepairRoot
	previousNextSource := c.nextCodexForkRepairSource
	start := sort.Search(len(active), func(index int) bool { return active[index] >= c.nextCodexForkRepairRoot })
	if start == len(active) {
		start = 0
	}
	rotated := make([]string, 0, len(active))
	rotated = append(rotated, active[start:]...)
	rotated = append(rotated, active[:start]...)
	selected := rotated
	if len(active) > maxCodexForkRepairCatalogRoots {
		selected = make([]string, 0, maxCodexForkRepairCatalogRoots)
		for index := 0; index < maxCodexForkRepairCatalogRoots; index++ {
			selected = append(selected, rotated[index])
		}
	}
	keepIDs := make(map[string]struct{}, len(selected))
	for _, root := range selected {
		keepIDs[codexForkRepairRootID(root)] = struct{}{}
	}
	for rootID := range c.codexForkRepairCatalog {
		if _, active := keepIDs[rootID]; active {
			continue
		}
		delete(c.codexForkRepairCatalog, rootID)
	}
	if err := c.saveCodexForkRepairFrontierLocked(); err != nil {
		c.codexForkRepairCatalog = previousCatalog
		c.nextCodexForkRepairRoot = previousNextRoot
		c.nextCodexForkRepairSource = previousNextSource
		return nil, err
	}
	return selected, nil
}

func (c *AgentTokenCollector) codexForkRepairSources(ctx context.Context, root, rootID string) ([]TranscriptSource, bool, int64, error) {
	if err := c.loadCodexForkRepairFrontier(); err != nil {
		return nil, false, 0, err
	}
	c.codexForkRepairMu.Lock()
	previousCatalog := cloneCodexForkRepairCatalogs(c.codexForkRepairCatalog)
	previousNextRoot := c.nextCodexForkRepairRoot
	previousNextSource := c.nextCodexForkRepairSource
	catalog := c.codexForkRepairCatalog[rootID]
	if catalog == nil {
		catalog = &codexForkRepairCatalog{root: filepath.Clean(root), sources: make(map[string]TranscriptSource)}
		c.codexForkRepairCatalog[rootID] = catalog
	}
	if !catalog.initialized {
		catalog.pendingDirs = []string{filepath.Clean(root)}
		catalog.initialized = true
	}
	var discoveryErr error
	for path, source := range catalog.sources {
		state, err := c.transcriptCursor.State(source)
		if err != nil {
			discoveryErr = errors.Join(discoveryErr, err)
			continue
		}
		if state.CodexForkRepairComplete {
			delete(catalog.sources, path)
		}
	}
	maxFiles := c.config.TranscriptDiscoveryMaxFiles
	maxDirs := c.config.TranscriptDiscoveryMaxDirs
	maxBytes := c.config.TranscriptDiscoveryMaxBytes
	bytesRead := int64(0)
	processedFiles, processedDirPages := 0, 0
	deferFilesAfterError := false
	var sourceErr error
	for processedFiles < maxFiles && bytesRead < maxBytes && ((len(catalog.pendingFiles) > 0 && !deferFilesAfterError) || processedDirPages < maxDirs && (catalog.directory != nil || len(catalog.pendingDirs) > 0 || len(catalog.pausedDirectories) > 0)) {
		if err := ctx.Err(); err != nil {
			discoveryErr = errors.Join(discoveryErr, err)
			break
		}
		if len(catalog.pendingFiles) > 0 && !deferFilesAfterError {
			path := catalog.pendingFiles[0]
			catalog.pendingFiles = catalog.pendingFiles[1:]
			headerBudget := maxBytes - bytesRead
			if headerBudget > maxCodexForkRepairHeaderBytes {
				headerBudget = maxCodexForkRepairHeaderBytes
			}
			source, readBytes, budgetCut, err := codexForkRepairSource(catalog.root, path, headerBudget)
			bytesRead += readBytes
			if err != nil {
				if !os.IsNotExist(err) {
					sourceErr = errors.Join(sourceErr, err)
					catalog.pendingFiles = append(catalog.pendingFiles, path)
					deferFilesAfterError = true
				}
				processedFiles++
				continue
			}
			if budgetCut {
				if bytesRead == readBytes && maxBytes <= readBytes {
					catalog.blocked = true
					if len(catalog.pendingFiles)+len(catalog.blockedFiles) < maxCodexForkRepairFileFrontier {
						catalog.blockedFiles = append(catalog.blockedFiles, path)
					} else {
						catalog.pendingFiles = append([]string{path}, catalog.pendingFiles...)
					}
					sourceErr = errors.Join(sourceErr, errors.New("Codex transcript header exceeds discovery byte budget"))
					processedFiles++
					continue
				}
				catalog.pendingFiles = append([]string{path}, catalog.pendingFiles...)
				break
			}
			if source != nil {
				if _, alreadyRetained := catalog.sources[source.Path]; !alreadyRetained && len(catalog.sources) >= maxCodexForkRepairSourcesPerRoot {
					catalog.pendingFiles = append([]string{path}, catalog.pendingFiles...)
					break
				}
				catalog.sources[source.Path] = *source
			}
			processedFiles++
			continue
		}
		if catalog.directory == nil && len(catalog.pendingDirs) > 0 {
			dir := catalog.pendingDirs[0]
			catalog.pendingDirs = catalog.pendingDirs[1:]
			cursor, err := newCodexForkRepairDirectoryCursor(catalog.root, dir)
			if err != nil {
				if !os.IsNotExist(err) {
					sourceErr = errors.Join(sourceErr, err)
					catalog.pendingDirs = append(catalog.pendingDirs, dir)
				}
				processedDirPages++
				continue
			}
			catalog.directory = cursor
		} else if catalog.directory == nil && len(catalog.pausedDirectories) > 0 {
			last := len(catalog.pausedDirectories) - 1
			cursor := catalog.pausedDirectories[last]
			catalog.directory = &cursor
			catalog.pausedDirectories = catalog.pausedDirectories[:last]
		}
		if catalog.directory == nil {
			continue
		}
		if len(catalog.directory.PendingNames) == 0 {
			names, eof, err := readCodexForkRepairDirectoryPage(catalog.root, catalog.directory)
			processedDirPages++
			if err != nil {
				sourceErr = errors.Join(sourceErr, err)
				catalog.blocked = true
				break
			}
			catalog.directory.PendingNames = names
			if eof {
				catalog.directory = nil
				continue
			}
			if len(names) == 0 {
				continue
			}
		}
		if len(catalog.directory.PendingNames) == 0 {
			continue
		}
		name := catalog.directory.PendingNames[0]
		path := filepath.Join(catalog.directory.Path, name)
		entry, info, statErr := openCodexForkRepairPathNoSymlinks(catalog.root, path, true)
		if statErr != nil && (errors.Is(statErr, errCodexForkRepairNotDirectory) || errors.Is(statErr, errCodexForkRepairSymlink)) {
			statErr = nil
		}
		if statErr != nil && !os.IsNotExist(statErr) {
			sourceErr = errors.Join(sourceErr, statErr)
			catalog.blocked = true
			break
		}
		if entry != nil {
			_ = entry.Close()
		}
		if statErr == nil && info != nil && info.IsDir() {
			if len(catalog.pausedDirectories) >= maxCodexForkRepairDirectoryFrontier {
				catalog.blocked = true
				sourceErr = errors.Join(sourceErr, errors.New("Codex fork repair directory stack reached its bound"))
				break
			}
			child, openErr := newCodexForkRepairDirectoryCursor(catalog.root, path)
			if openErr != nil {
				if !os.IsNotExist(openErr) {
					sourceErr = errors.Join(sourceErr, openErr)
					catalog.blocked = true
					break
				}
				catalog.directory.PendingNames = catalog.directory.PendingNames[1:]
				processedDirPages++
				continue
			}
			catalog.directory.PendingNames = catalog.directory.PendingNames[1:]
			processedDirPages++
			catalog.pausedDirectories = append(catalog.pausedDirectories, cloneCodexForkRepairDirectory(*catalog.directory))
			catalog.directory = child
			continue
		}
		catalog.directory.PendingNames = catalog.directory.PendingNames[1:]
		processedDirPages++
		if statErr != nil {
			continue
		}
		if strings.HasPrefix(filepath.Base(path), "rollout-") && filepath.Ext(path) == ".jsonl" {
			if len(catalog.pendingFiles)+len(catalog.blockedFiles) >= maxCodexForkRepairFileFrontier {
				catalog.directory.PendingNames = append([]string{name}, catalog.directory.PendingNames...)
				catalog.blocked = true
				sourceErr = errors.Join(sourceErr, errors.New("Codex fork repair file queue reached its bound"))
				break
			}
			catalog.pendingFiles = append(catalog.pendingFiles, path)
		}
	}
	if catalog.blocked && sourceErr == nil && len(catalog.blockedFiles) == 0 {
		catalog.blocked = false
	}
	if catalog.blocked && sourceErr == nil {
		sourceErr = errors.New("Codex fork repair discovery has unresolved entries")
	}
	discoveryErr = errors.Join(discoveryErr, sourceErr)
	catalog.complete = len(catalog.pendingDirs) == 0 && len(catalog.pendingFiles) == 0 && catalog.directory == nil && len(catalog.pausedDirectories) == 0 && !catalog.blocked && discoveryErr == nil
	complete := catalog.complete
	if persistErr := c.saveCodexForkRepairFrontierLocked(); persistErr != nil {
		discoveryErr = errors.Join(discoveryErr, persistErr)
		complete = false
		c.codexForkRepairCatalog = previousCatalog
		c.nextCodexForkRepairRoot = previousNextRoot
		c.nextCodexForkRepairSource = previousNextSource
		c.codexForkRepairMu.Unlock()
		return nil, false, bytesRead, discoveryErr
	}
	result := codexForkRepairCatalogSources(catalog)
	c.codexForkRepairMu.Unlock()
	return result, complete, bytesRead, discoveryErr
}

func cloneCodexForkRepairCatalogs(catalogs map[string]*codexForkRepairCatalog) map[string]*codexForkRepairCatalog {
	cloned := make(map[string]*codexForkRepairCatalog, len(catalogs))
	for rootID, catalog := range catalogs {
		if catalog == nil {
			cloned[rootID] = nil
			continue
		}
		copyOf := *catalog
		copyOf.pendingDirs = append([]string(nil), catalog.pendingDirs...)
		copyOf.pendingFiles = append([]string(nil), catalog.pendingFiles...)
		copyOf.blockedFiles = append([]string(nil), catalog.blockedFiles...)
		copyOf.pausedDirectories = make([]codexForkRepairDirectoryState, len(catalog.pausedDirectories))
		for index, directory := range catalog.pausedDirectories {
			copyOf.pausedDirectories[index] = cloneCodexForkRepairDirectory(directory)
		}
		if catalog.directory != nil {
			directory := cloneCodexForkRepairDirectory(*catalog.directory)
			copyOf.directory = &directory
		}
		copyOf.sources = make(map[string]TranscriptSource, len(catalog.sources))
		for path, source := range catalog.sources {
			copyOf.sources[path] = source
		}
		cloned[rootID] = &copyOf
	}
	return cloned
}

func codexForkRepairCatalogSources(catalog *codexForkRepairCatalog) []TranscriptSource {
	result := make([]TranscriptSource, 0, len(catalog.sources))
	for _, source := range catalog.sources {
		result = append(result, source)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Path < result[j].Path })
	return result
}

func codexForkRepairSource(root, path string, maxHeaderBytes int64) (*TranscriptSource, int64, bool, error) {
	file, info, err := openCodexForkRepairPathNoSymlinks(root, path, false)
	if err != nil {
		if errors.Is(err, errCodexForkRepairSymlink) {
			return nil, 0, false, nil
		}
		return nil, 0, false, err
	}
	defer file.Close()
	if !info.Mode().IsRegular() {
		return nil, 0, false, nil
	}
	if maxHeaderBytes <= 0 {
		return nil, 0, true, nil
	}
	counting := &transcriptCountingReader{reader: io.LimitReader(file, maxHeaderBytes)}
	header, readErr := bufio.NewReader(counting).ReadBytes('\n')
	headerBytesRead := counting.read
	if readErr != nil && !errors.Is(readErr, io.EOF) {
		return nil, headerBytesRead, false, readErr
	}
	if int64(len(header)) == maxHeaderBytes && info.Size() > int64(len(header)) && bytes.IndexByte(header, '\n') < 0 {
		if maxHeaderBytes < maxCodexForkRepairHeaderBytes {
			return nil, headerBytesRead, true, nil
		}
		return nil, headerBytesRead, false, errors.New("Codex transcript header exceeds repair limit")
	}
	if newline := bytes.IndexByte(header, '\n'); newline >= 0 {
		header = header[:newline]
	}
	var record struct {
		Type    string `json:"type"`
		Payload struct {
			ID           string `json:"id"`
			ThreadID     string `json:"thread_id"`
			ForkedFromID string `json:"forked_from_id"`
			ThreadSource string `json:"thread_source"`
		} `json:"payload"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(header), &record); err != nil || record.Type != "session_meta" {
		return nil, headerBytesRead, false, nil
	}
	payload := map[string]any{"id": record.Payload.ID, "thread_id": record.Payload.ThreadID}
	parent := record.Payload.ForkedFromID
	threadSource := record.Payload.ThreadSource
	child := codexSessionMetaThreadID(payload)
	parent = strings.TrimSpace(parent)
	if len(parent) > 256 {
		parent = parent[:256]
	}
	if len(child) > 256 {
		child = child[:256]
	}
	if parent == "" || child == "" || !strings.EqualFold(strings.TrimSpace(threadSource), "subagent") {
		return nil, headerBytesRead, false, nil
	}
	path = filepath.Clean(path)
	sourceID := "codex:" + path
	if len(sourceID) > maxCodexForkRepairPathBytes {
		digest := sha256.Sum256([]byte(path))
		sourceID = "codex:" + hex.EncodeToString(digest[:])
	}
	return &TranscriptSource{
		Path: path, Provider: "codex", SourceID: sourceID,
		ThreadID: child, AgentID: child,
		CodexForkRepairOnly: true,
	}, headerBytesRead, false, nil
}

func agentTokenCoverage(c AgentTokenCollectorConfig, source string, observed, cadence float64) CoverageEpoch {
	return CoverageEpoch{"agent_tokens", source, c.EpochID, c.EpochStartedAt, floatPointer(observed + cadence), cadence, c.OwnerGeneration}
}
func agentTokenUnavailable(c AgentTokenCollectorConfig, source string, observed, cadence float64, reason string) UnavailableSpan {
	return UnavailableSpan{"agent_tokens", source, c.EpochID, observed, observed + cadence, cadence, reason, c.OwnerGeneration}
}

func (c *AgentTokenCollector) appendOpenCodeUnavailable(batch *AppendBatch, source string, observed, cadence float64, reason string) {
	batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, source, observed, cadence, reason))
}

type unavailableSpanKey struct {
	family, source, epoch string
	start, end            float64
}

// deduplicateUnavailableSpans is the final collector boundary for unavailable
// facts. OpenCode can report one bad session through multiple inventory rows,
// root walks, and transcript-source paths; all of those paths must agree before
// AppendBatch validates the interval set. Exact duplicates are collapsed, and
// overlapping intervals with one semantic source/epoch are coalesced. Choose
// the lexicographically smallest reason so the retained fact does not depend on
// source traversal order.
func deduplicateUnavailableSpans(batch *AppendBatch) {
	if len(batch.UnavailableSpans) < 2 {
		return
	}
	byKey := make(map[unavailableSpanKey]UnavailableSpan, len(batch.UnavailableSpans))
	for _, span := range batch.UnavailableSpans {
		key := unavailableSpanKey{span.Family, span.SourceID, span.EpochID, span.StartedAt, span.EndedAt}
		current, ok := byKey[key]
		if !ok || unavailableSpanLess(span, current) {
			byKey[key] = span
		}
	}
	keys := make([]unavailableSpanKey, 0, len(byKey))
	for key := range byKey {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		left, right := keys[i], keys[j]
		if left.family != right.family {
			return left.family < right.family
		}
		if left.source != right.source {
			return left.source < right.source
		}
		if left.epoch != right.epoch {
			return left.epoch < right.epoch
		}
		if left.start != right.start {
			return left.start < right.start
		}
		return left.end < right.end
	})
	spans := make([]UnavailableSpan, 0, len(keys))
	for _, key := range keys {
		spans = append(spans, byKey[key])
	}
	sort.SliceStable(spans, func(i, j int) bool {
		left, right := spans[i], spans[j]
		if left.Family != right.Family {
			return left.Family < right.Family
		}
		if left.SourceID != right.SourceID {
			return left.SourceID < right.SourceID
		}
		if left.EpochID != right.EpochID {
			return left.EpochID < right.EpochID
		}
		if left.NativeCadenceSeconds != right.NativeCadenceSeconds {
			return left.NativeCadenceSeconds < right.NativeCadenceSeconds
		}
		if left.OwnerGeneration != right.OwnerGeneration {
			return left.OwnerGeneration < right.OwnerGeneration
		}
		if left.StartedAt != right.StartedAt {
			return left.StartedAt < right.StartedAt
		}
		return left.EndedAt < right.EndedAt
	})
	coalesced := make([]UnavailableSpan, 0, len(spans))
	for _, span := range spans {
		if len(coalesced) > 0 {
			previous := &coalesced[len(coalesced)-1]
			if previous.Family == span.Family && previous.SourceID == span.SourceID &&
				previous.EpochID == span.EpochID && previous.NativeCadenceSeconds == span.NativeCadenceSeconds &&
				previous.OwnerGeneration == span.OwnerGeneration && span.StartedAt < previous.EndedAt {
				if span.EndedAt > previous.EndedAt {
					previous.EndedAt = span.EndedAt
				}
				if unavailableSpanLess(span, *previous) {
					previous.Reason = span.Reason
				}
				continue
			}
		}
		coalesced = append(coalesced, span)
	}
	batch.UnavailableSpans = coalesced
}

func unavailableSpanLess(left, right UnavailableSpan) bool {
	if left.Reason != right.Reason {
		return left.Reason < right.Reason
	}
	if left.NativeCadenceSeconds != right.NativeCadenceSeconds {
		return left.NativeCadenceSeconds < right.NativeCadenceSeconds
	}
	return left.OwnerGeneration < right.OwnerGeneration
}

func openCodeStepFinishSelections(tree OpenCodeSessionTreeWindow, rootSessionID string, observedMillis int64, cursors map[string]OpenCodePartCursor) []OpenCodeStepFinishSelection {
	activeAt := tree.NewestUpdated
	if activeAt > observedMillis {
		activeAt = observedMillis
	}
	activeSince := activeAt - int64(openCodeActiveWindow/time.Millisecond)
	if activeSince < 0 {
		activeSince = 0
	}
	selections := make([]OpenCodeStepFinishSelection, 0, len(tree.Sessions))
	for _, sessionID := range tree.Sessions {
		selections = append(selections, OpenCodeStepFinishSelection{
			SessionID: sessionID, RootSessionID: rootSessionID, ActiveSince: activeSince,
			CursorRowID: cursors[sessionID].RowID,
		})
	}
	return selections
}

func (c *AgentTokenCollector) collectOpenCode(ctx context.Context, observed, cadence float64, records []AgentRecord) (batch AppendBatch, receipt *agentTokenReceipt, err error) {
	collectionStarted := time.Now()
	defer func() {
		duration := time.Since(collectionStarted)
		c.counters.opencodeCollectionLast.Store(duration.Nanoseconds())
		c.counters.opencodeCollectionTotal.Add(duration.Nanoseconds())
	}()
	defer func() { deduplicateUnavailableSpans(&batch) }()
	identityCursorStarted := time.Now()
	identity, err := os.Stat(c.config.OpenCodeDatabase)
	if err != nil {
		c.counters.recordOpenCodePhase("identity_cursor", time.Since(identityCursorStarted))
		c.closeOpenCodeDatabase()
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode", observed, cadence, "database-unavailable")}}, nil, nil
	}
	device, inode := fileIdentity(identity)
	walDevice, walInode, walErr := openCodeWALIdentity(c.config.OpenCodeDatabase)
	if walErr != nil {
		c.counters.recordOpenCodePhase("identity_cursor", time.Since(identityCursorStarted))
		c.closeOpenCodeDatabase()
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode", observed, cadence, "database-wal-unavailable")}}, nil, nil
	}
	statsDatabaseIdentity := ""
	if identityProvider, ok := c.writer.(interface{ statsDatabaseIdentity() (string, error) }); ok {
		statsDatabaseIdentity, err = identityProvider.statsDatabaseIdentity()
		if err != nil {
			c.counters.recordOpenCodePhase("identity_cursor", time.Since(identityCursorStarted))
			return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode-cursor", observed, cadence, "stats-database-identity-unavailable")}}, nil, nil
		}
	}
	cursorState, err := c.cursor.state()
	if err != nil {
		c.counters.recordOpenCodePhase("identity_cursor", time.Since(identityCursorStarted))
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode-cursor", observed, cadence, err.Error())}}, nil, nil
	}
	cursorBefore := cursorState.clone()
	statsDatabaseChanged := statsDatabaseIdentity != "" && cursorState.StatsDatabaseIdentity != statsDatabaseIdentity
	if statsDatabaseChanged {
		cursorState = openCodeCursorState{
			Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{},
			StatsDatabaseIdentity: statsDatabaseIdentity,
		}
	}
	mainDatabaseChanged := cursorState.Device != 0 && (cursorState.Device != device || cursorState.Inode != inode)
	walIdentityChanged := cursorState.Device != 0 && !mainDatabaseChanged && (cursorState.WALDevice != walDevice || cursorState.WALInode != walInode)
	if mainDatabaseChanged {
		cursorState = openCodeCursorState{
			Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{},
			Device: device, Inode: inode, StatsDatabaseIdentity: cursorState.StatsDatabaseIdentity,
		}
	}
	cursorReset := statsDatabaseChanged || mainDatabaseChanged
	cursorState.Device, cursorState.Inode, cursorState.WALDevice, cursorState.WALInode = device, inode, walDevice, walInode
	if statsDatabaseIdentity != "" {
		cursorState.StatsDatabaseIdentity = statsDatabaseIdentity
	}
	c.counters.recordOpenCodePhase("identity_cursor", time.Since(identityCursorStarted))
	databaseStarted := time.Now()
	db, reused, openDuration, err := c.openCachedOpenCodeDatabase(ctx, c.config.OpenCodeDatabase, device, inode, walDevice, walInode)
	c.counters.recordOpenCodePhase("database_open", time.Since(databaseStarted))
	if reused {
		c.counters.opencodeDatabaseReuses.Add(1)
	} else {
		c.counters.opencodeDatabaseOpens.Add(1)
		c.counters.opencodeDatabaseLastOpen.Store(openDuration.Nanoseconds())
		c.counters.opencodeDatabaseOpenTotal.Add(openDuration.Nanoseconds())
	}
	if err != nil {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode", observed, cadence, openCodeUnavailableReason(err))}}, nil, nil
	}
	sourceDataVersion, err := db.ReadSourceDataVersion(ctx)
	if err != nil {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode", observed, cadence, "database-version-unavailable")}}, nil, nil
	}
	// Capture the source-database work even when a later bounded query fails.
	// This is cumulative per long-lived read handle; the collector counter
	// stores the latest snapshot for the Daemons disclosure.
	defer c.recordOpenCodeSQLiteMetrics(db)
	proposed := cursorState.clone()
	batch = AppendBatch{}
	type rootClaim struct {
		key    string
		record AgentRecord
	}
	claimsStarted := time.Now()
	claims := map[string]rootClaim{}
	var sourceFailure error
	for _, record := range records {
		if record.Kind != "opencode" {
			continue
		}
		agentKey := agentKeyForRecord(record, record.OpenCodeSession)
		sessionID := record.OpenCodeSession
		if sourceFailure != nil {
			source := "opencode:" + sessionID
			if sessionID == "" {
				source = openCodeProcessSource(record)
			}
			c.appendOpenCodeUnavailable(&batch, source, observed, cadence, openCodeUnavailableReason(sourceFailure))
			continue
		}
		if sessionID == "" {
			processSource := openCodeProcessSource(record)
			if record.Directory == "" {
				c.appendOpenCodeUnavailable(&batch, processSource, observed, cadence, "session-identity-unavailable")
				continue
			}
			resolved, resolveErr := db.ResolveSessionID(ctx, record.Directory, record.Title, record.StartedAt, observed)
			if resolveErr != nil {
				if isOpenCodeSourceReadFailure(resolveErr) {
					c.discardOpenCodeDatabase(db)
					sourceFailure = resolveErr
				}
				c.appendOpenCodeUnavailable(&batch, processSource, observed, cadence, openCodeUnavailableReason(resolveErr))
				continue
			}
			sessionID = resolved
		}
		rootSessionID, rootErr := db.RootSessionID(ctx, sessionID, MaxOpenCodeSessionTree)
		if rootErr != nil {
			if isOpenCodeSourceReadFailure(rootErr) {
				c.discardOpenCodeDatabase(db)
				sourceFailure = rootErr
			}
			// Deterministic mock clients are terminal-test fixtures. Their pane
			// processes can outlive the fixture database and therefore carry a
			// session id that is intentionally absent from the operator's native
			// OpenCode database. Do not let those stale fixtures manufacture an
			// unavailable interval or mask a valid native root.
			if isStaleOpenCodeMockRecord(record, rootErr) {
				continue
			}
			c.appendOpenCodeUnavailable(&batch, "opencode:"+sessionID, observed, cadence, openCodeUnavailableReason(rootErr))
			continue
		}
		sessionID = rootSessionID
		candidate := rootClaim{key: agentKey, record: record}
		if current, ok := claims[sessionID]; ok {
			// A machine-wide /proc scan can see stale wrappers for the same
			// persisted root. Prefer the process anchored to the current tmux
			// pane before falling back to a deterministic start/PID order; using
			// the first PID let an unrelated old process own a live root.
			if !openCodeRootClaimBetter(candidate.record, current.record) {
				continue
			}
		}
		claims[sessionID] = candidate
	}
	c.counters.recordOpenCodePhase("claims", time.Since(claimsStarted))
	rootIDs := make([]string, 0, len(claims))
	for sessionID := range claims {
		rootIDs = append(rootIDs, sessionID)
	}
	sort.Strings(rootIDs)
	type rootWindow struct {
		id              string
		claim           rootClaim
		tree            OpenCodeSessionTreeWindow
		initiallyUnseen bool
	}
	rootWindows := make([]rootWindow, 0, len(rootIDs))
	selections := make([]OpenCodeStepFinishSelection, 0)
	treesStarted := time.Now()
	for _, sessionID := range rootIDs {
		if sourceFailure != nil {
			c.appendOpenCodeUnavailable(&batch, "opencode:"+sessionID, observed, cadence, openCodeUnavailableReason(sourceFailure))
			continue
		}
		claim := claims[sessionID]
		tree, treeErr := db.ReadCurrentSessionDescendantFrontier(ctx, sessionID, MaxOpenCodeSessionTree)
		if treeErr != nil {
			if isOpenCodeSourceReadFailure(treeErr) {
				c.discardOpenCodeDatabase(db)
				sourceFailure = treeErr
			}
			c.appendOpenCodeUnavailable(&batch, "opencode:"+sessionID, observed, cadence, openCodeUnavailableReason(treeErr))
			continue
		}
		initiallyUnseen := true
		for _, childID := range tree.Sessions {
			if proposed.Parts[childID].RowID > 0 {
				initiallyUnseen = false
				break
			}
		}
		// Keep the fair-result query on the current window. The durable row-ID
		// reconciliation below backfills older history across cycles; widening
		// this query to the root's oldest row makes fresh roots wait for a large
		// history ranking before they can emit current usage.
		rootSelections := openCodeStepFinishSelections(tree, sessionID, int64(observed*1000), proposed.Parts)
		selections = append(selections, rootSelections...)
		rootWindows = append(rootWindows, rootWindow{sessionID, claim, tree, initiallyUnseen})
	}
	c.counters.recordOpenCodePhase("session_tree", time.Since(treesStarted))
	selectedSessionIDs := make([]string, 0, len(selections))
	for _, selection := range selections {
		selectedSessionIDs = append(selectedSessionIDs, selection.SessionID)
	}
	selectionKey := openCodeSelectionKey(selections)
	rawScan := proposed.RawScan
	rawScanInProgress := rawScan.HasScan && rawScan.SelectionKey == selectionKey && rawScan.ScannedThroughRowID < rawScan.HighWaterRowID
	// SQLite data_version is database-wide. New rows and active edits are
	// handled incrementally; periodically rescan old rowids to catch revisions
	// whose time_updated value was not advanced by the source.
	fullRawReconcileDue := rawScan.HasScan && !rawScanInProgress && !rawScan.ScanDirty &&
		(observed >= float64(rawScan.LastFullReconcileAt)+openCodeRawReconcileInterval.Seconds())
	sourceScanRequired := sourceFailure == nil && (db.SourceScanRequired(sourceDataVersion, selectedSessionIDs, cursorReset || walIdentityChanged || rawScan.ForceRescan) || rawScanInProgress || rawScan.ForceRescan || fullRawReconcileDue)
	var window OpenCodeStepFinishWindow
	stepFinishStarted := time.Now()
	if sourceFailure != nil {
		err = sourceFailure
	} else if sourceScanRequired && len(selections) > 0 {
		window, err = db.ReadStepFinishPartsForSelections(ctx, selections, int64(observed*1000)+1, proposed.Parts, MaxOpenCodePartReadLimit)
	} else {
		window.Cursors = proposed.Parts
	}
	if window.RootErrors == nil {
		window.RootErrors = map[string]string{}
	}
	if window.TruncatedRoots == nil {
		window.TruncatedRoots = map[string]bool{}
	}
	var queryRootErrors *OpenCodeStepFinishRootErrors
	queryUsable := err == nil || errors.As(err, &queryRootErrors)
	queryErrors := cloneOpenCodeRootErrors(window.RootErrors)
	sourceChangedDuringRawPage := false
	if queryUsable && sourceScanRequired && len(selections) > 0 {
		longInitialHistory := false
		for _, root := range rootWindows {
			if root.tree.Complete && root.initiallyUnseen && root.tree.NewestUpdated-root.tree.OldestCreated > int64(openCodeActiveWindow/time.Millisecond) {
				longInitialHistory = true
				break
			}
		}
		selectionChanged := rawScan.HasScan && rawScan.SelectionKey != selectionKey
		pagePending := rawScanInProgress
		sourceVersionChanged := rawScan.HasScan && rawScan.SourceDataVersion != sourceDataVersion
		walResume := walIdentityChanged && rawScan.HasScan && rawScan.SelectionKey == selectionKey &&
			rawScan.ScannedThroughRowID >= rawScan.HighWaterRowID && !rawScan.ScanDirty && !rawScan.ForceRescan
		needRawScan := !reused || pagePending || longInitialHistory || window.Truncated || selectionChanged || sourceVersionChanged || rawScan.ForceRescan || fullRawReconcileDue
		if needRawScan {
			restart := !rawScan.HasScan || rawScan.SelectionKey != selectionKey || (!reused && !walResume) || rawScan.ForceRescan || fullRawReconcileDue
			frontierReady := true
			if restart {
				highWaterRowID, highWaterErr := db.ReadPartHighWaterRowID(ctx)
				if highWaterErr != nil {
					err = highWaterErr
					frontierReady = false
				} else {
					publishedErrors := cloneOpenCodeRootErrors(rawScan.RootErrors)
					if selectionChanged {
						publishedErrors = openCodeSelectedRootErrors(selections, publishedErrors)
					}
					rawScan = openCodeRawScanState{
						HasScan: true, SelectionKey: selectionKey, HighWaterRowID: highWaterRowID,
						SourceDataVersion: sourceDataVersion, LastFullReconcileAt: int64(observed), FullReconcile: true,
						RootErrors:     publishedErrors,
						ScanRootErrors: map[string]string{},
					}
				}
			} else if walResume || (!pagePending && sourceVersionChanged) {
				// Keep the completed row-ID frontier when another process commits.
				// The bounded current-window query detects recent revisions, while
				// this page catches newly appended rows without replaying all history.
				highWaterRowID, highWaterErr := db.ReadPartHighWaterRowID(ctx)
				if highWaterErr != nil {
					err = highWaterErr
					frontierReady = false
				} else {
					rawScan.HighWaterRowID = highWaterRowID
					rawScan.SourceDataVersion = sourceDataVersion
				}
			} else if pagePending && sourceVersionChanged {
				// Keep the pass frontier fixed. Reconcile writes in a fresh pass
				// after this generation reaches its original high-water row.
				rawScan.ScanDirty = true
			}
			if queryUsable && frontierReady && rawScan.ScannedThroughRowID < rawScan.HighWaterRowID {
				page, pageErr := db.ReadStepFinishPartsPage(ctx, selections, rawScan.ScannedThroughRowID, rawScan.HighWaterRowID, MaxOpenCodePartReadLimit)
				if pageErr != nil {
					err = pageErr
				} else {
					versionAfterPage, versionErr := db.ReadSourceDataVersion(ctx)
					if versionErr != nil {
						err = versionErr
					} else {
						rawScan.ScannedThroughRowID = page.ScannedThroughRowID
						for rootID, reason := range page.RootErrors {
							rawScan.ScanRootErrors[rootID] = reason
						}
						if versionAfterPage != sourceDataVersion {
							rawScan.ScanDirty = true
							sourceChangedDuringRawPage = true
						}
						for rootID, reason := range rawScan.RootErrors {
							window.RootErrors[rootID] = reason
						}
						for rootID, reason := range rawScan.ScanRootErrors {
							window.RootErrors[rootID] = reason
						}
						window.Parts = mergeOpenCodeStepFinishParts(window.Parts, page.Candidates)
						for sessionID, cursor := range page.Cursors {
							if current, ok := window.Cursors[sessionID]; !ok || cursor.RowID > current.RowID {
								window.Cursors[sessionID] = cursor
							}
						}
						if !queryUsable || rawScan.ScanDirty || page.HasMore || rawScan.ScannedThroughRowID < rawScan.HighWaterRowID {
							for _, selection := range selections {
								window.TruncatedRoots[selection.RootSessionID] = true
							}
							window.Truncated = true
						} else {
							rawScan.ScannedThroughRowID = rawScan.HighWaterRowID
							rawScan.SourceDataVersion = versionAfterPage
							window.Truncated = false
							window.TruncatedRoots = map[string]bool{}
						}
						if rawScan.ScannedThroughRowID >= rawScan.HighWaterRowID && rawScan.ScanDirty && queryUsable {
							nextHighWaterRowID, highWaterErr := db.ReadPartHighWaterRowID(ctx)
							if highWaterErr != nil {
								err = highWaterErr
							} else {
								rawScan.ScannedThroughRowID = 0
								rawScan.HighWaterRowID = nextHighWaterRowID
								rawScan.SourceDataVersion = versionAfterPage
								rawScan.ScanRootErrors = map[string]string{}
								rawScan.ScanDirty = false
								rawScan.ForceRescan = true
							}
						}
					}
				}
			}
		}
		if queryUsable && rawScan.HasScan && rawScan.SelectionKey == selectionKey &&
			rawScan.ScannedThroughRowID >= rawScan.HighWaterRowID && !rawScan.ScanDirty && !sourceChangedDuringRawPage {
			if rawScan.FullReconcile {
				rawScan.LastFullReconcileAt = int64(observed)
				rawScan.FullReconcile = false
			}
			rawScan.RootErrors = cloneOpenCodeRootErrors(rawScan.ScanRootErrors)
			rawScan.ScanRootErrors = map[string]string{}
			window.RootErrors = cloneOpenCodeRootErrors(queryErrors)
			for rootID, reason := range rawScan.RootErrors {
				window.RootErrors[rootID] = reason
			}
			window.Truncated = false
			window.TruncatedRoots = map[string]bool{}
		}
		if rawScan.HasScan && rawScan.SelectionKey == selectionKey {
			for rootID, reason := range rawScan.RootErrors {
				window.RootErrors[rootID] = reason
			}
			for rootID, reason := range rawScan.ScanRootErrors {
				window.RootErrors[rootID] = reason
			}
			if rawScan.ScannedThroughRowID < rawScan.HighWaterRowID {
				for _, selection := range selections {
					window.TruncatedRoots[selection.RootSessionID] = true
				}
				window.Truncated = true
			} else if rawScan.SourceDataVersion == sourceDataVersion && !sourceChangedDuringRawPage {
				window.Truncated = false
				window.TruncatedRoots = map[string]bool{}
			}
		}
		proposed.RawScan = rawScan
	}
	if proposed.RawScan.HasScan && proposed.RawScan.SelectionKey == selectionKey {
		for rootID, reason := range proposed.RawScan.RootErrors {
			window.RootErrors[rootID] = reason
		}
		for rootID, reason := range proposed.RawScan.ScanRootErrors {
			window.RootErrors[rootID] = reason
		}
	}
	c.counters.recordOpenCodePhase("step_finish", time.Since(stepFinishStarted))
	if c.openCodeAfterSourceRead != nil {
		c.openCodeAfterSourceRead(window, err)
	}
	var rootErrors *OpenCodeStepFinishRootErrors
	rootValidationFailed := errors.As(err, &rootErrors)
	if err != nil && !rootValidationFailed {
		// A source-level read failure can leave a long-lived sqlite handle
		// attached to a truncated or otherwise replaced database. Do not reuse
		// that handle on the next cadence; the path identity may be unchanged
		// when OpenCode rewrites the file in place.
		c.discardOpenCodeDatabase(db)
		for _, root := range rootWindows {
			c.appendOpenCodeUnavailable(&batch, "opencode:"+root.id, observed, cadence, openCodeUnavailableReason(err))
		}
	} else {
		for root, reason := range window.RootErrors {
			c.appendOpenCodeUnavailable(&batch, "opencode:"+root, observed, cadence, reason)
		}
		for _, part := range window.Parts {
			claim := claims[part.RootSessionID]
			c.addOpenCodePart(&batch, proposed, part, claim.key, part.RootSessionID, observed, cadence)
		}
		proposed.Parts = window.Cursors
	}
	for _, root := range rootWindows {
		if err != nil && !rootValidationFailed {
			continue
		}
		if _, failed := window.RootErrors[root.id]; failed {
			continue
		}
		partsTruncated := window.TruncatedRoots[root.id]
		partialHistory := root.tree.Complete && root.initiallyUnseen &&
			root.tree.NewestUpdated-root.tree.OldestCreated > int64(openCodeActiveWindow/time.Millisecond)
		if proposed.RawScan.HasScan && proposed.RawScan.SelectionKey == selectionKey && proposed.RawScan.ScannedThroughRowID >= proposed.RawScan.HighWaterRowID {
			partialHistory = false
		}
		treeRevisionChanged := false
		for _, span := range batch.UnavailableSpans {
			if span.SourceID == "opencode:"+root.id && span.Reason == "opencode-source-revision-changed" {
				treeRevisionChanged = true
				break
			}
		}
		if !root.tree.Complete {
			// The bounded newest-child window is safe for usage atoms, but it
			// cannot establish coverage for omitted historical descendants.
			c.appendOpenCodeUnavailable(&batch, "opencode:"+root.id, observed, cadence, "opencode-session-tree-truncated")
		} else if partsTruncated || partialHistory {
			// Fresh rows are still useful, but the omitted historical suffix means
			// this cycle cannot establish complete coverage. Keep one interval per
			// root: tree truncation takes precedence when both bounds are hit.
			c.appendOpenCodeUnavailable(&batch, "opencode:"+root.id, observed, cadence, "opencode-session-parts-truncated")
		} else if !partsTruncated && !partialHistory && !treeRevisionChanged {
			batch.CoverageEpochs = append(batch.CoverageEpochs, agentTokenCoverage(c.config, "opencode:"+root.id, observed, cadence))
		}
	}
	cursorPrepareStarted := time.Now()
	cursorChanged := proposed.changed(cursorState)
	sourceScanSucceeded := sourceScanRequired && sourceFailure == nil && (err == nil || rootValidationFailed)
	if !cursorChanged && !sourceScanSucceeded {
		c.counters.recordOpenCodePhase("cursor_prepare", time.Since(cursorPrepareStarted))
		return batch, nil, nil
	}
	if cursorChanged {
		if err := c.cursor.prepare(cursorBefore, proposed); err != nil {
			c.counters.recordOpenCodePhase("cursor_prepare", time.Since(cursorPrepareStarted))
			return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode-cursor", observed, cadence, err.Error())}}, nil, nil
		}
	}
	c.counters.recordOpenCodePhase("cursor_prepare", time.Since(cursorPrepareStarted))
	committed := proposed.clone()
	return batch, &agentTokenReceipt{
		commit: func() error {
			if cursorChanged {
				if err := c.cursor.commit(committed); err != nil {
					return err
				}
			}
			if sourceScanSucceeded {
				db.CommitSourceScan(sourceDataVersion, selectedSessionIDs)
			}
			return nil
		},
		rollback: func() error {
			if !cursorChanged {
				return nil
			}
			return c.cursor.rollback()
		},
	}, nil
}

func (c *AgentTokenCollector) recordOpenCodeSQLiteMetrics(database *OpenCodeDatabase) {
	if database == nil {
		return
	}
	metrics := database.ReadMetrics()
	c.counters.opencodeSQLiteQueries.Store(metrics.Queries)
	c.counters.opencodeSQLiteLast.Store(metrics.LastDuration.Nanoseconds())
	c.counters.opencodeSQLiteTotal.Store(metrics.Duration.Nanoseconds())
}

func isStaleOpenCodeMockRecord(record AgentRecord, err error) bool {
	if err == nil || !strings.Contains(err.Error(), "OpenCode root session not found") {
		return false
	}
	return strings.Contains(record.Command, "--mock") && record.OpenCodeSession != ""
}

func isOpenCodeSourceReadFailure(err error) bool {
	if err == nil {
		return false
	}
	message := err.Error()
	for _, marker := range []string{
		"resolve OpenCode session:",
		"read OpenCode ",
		"check OpenCode ",
	} {
		if strings.Contains(message, marker) {
			return true
		}
	}
	return false
}

// openCodeUnavailableReason keeps source-level SQLite failures on the same
// typed boundary as Python. Raw driver text is useful in logs, but it is not a
// stable browser/no-data reason and can vary between SQLite builds.
func openCodeUnavailableReason(err error) string {
	if err == nil {
		return ""
	}
	message := strings.ToLower(err.Error())
	switch {
	case strings.Contains(message, "file is not a database"),
		strings.Contains(message, "database disk image is malformed"),
		strings.Contains(message, "malformed database"):
		return "database-malformed"
	case strings.Contains(message, "database is locked"),
		strings.Contains(message, "database table is locked"),
		strings.Contains(message, "database schema is locked"),
		strings.Contains(message, "database is busy"),
		strings.Contains(message, "sqlite_busy"):
		return "database-locked"
	default:
		return err.Error()
	}
}

func openCodeRootClaimBetter(candidate, current AgentRecord) bool {
	candidatePane := candidate.TmuxPaneID != "" || candidate.TmuxSession != ""
	currentPane := current.TmuxPaneID != "" || current.TmuxSession != ""
	if candidatePane != currentPane {
		return candidatePane
	}
	if candidate.StartedAt != current.StartedAt {
		return candidate.StartedAt > current.StartedAt
	}
	return candidate.PID > current.PID
}

func openCodeProcessSource(record AgentRecord) string {
	if record.PID > 0 {
		return fmt.Sprintf("opencode-process:%d:%s", record.PID, record.StartedIdentity)
	}
	value := record.Directory + "\x1f" + record.Title
	hash := sha256.Sum256([]byte(value))
	return "opencode-process:" + hex.EncodeToString(hash[:8])
}

func (c *AgentTokenCollector) addOpenCodePart(batch *AppendBatch, cursor openCodeCursorState, part OpenCodeStepFinishPart, agentID, rootSessionID string, observed, cadence float64) {
	for dimension, quantity := range map[string]int64{"input": part.Tokens.Input, "cache_read": part.Tokens.CacheRead, "output": part.Tokens.Output} {
		if !part.Tokens.Present[dimension] || quantity <= 0 {
			continue
		}
		key := openCodeRevisionKey(part.SessionID, part.PartID, dimension)
		revision := openCodeRevision(part)
		if previous, ok := cursor.Events[key]; ok {
			if previous == revision {
				continue
			}
			c.appendOpenCodeUnavailable(batch, "opencode:"+rootSessionID, observed, cadence, "opencode-source-revision-changed")
			continue
		}
		cursor.Events[key] = revision
		direction, cache := "input", "none"
		if dimension == "output" {
			direction = "output"
		}
		if dimension == "cache_read" {
			cache = "read"
		}
		metadata := UsageMetadata{
			Provider: part.Provider, Model: part.Model, ModelEvidence: part.ModelEvidence,
			Effort: "unknown", PricingProfile: "default", ServiceTier: "default", ThreadID: part.SessionID,
			ExecutionSource: "opencode", TelemetryComplete: openCodeTokenTelemetryComplete(part.Tokens),
		}
		payload := usageMetadataPayloadFields(metadata, part.Provider)
		payload["quantity"] = float64(quantity)
		payload["agent_id"] = agentID
		batch.UsageAtoms = append(batch.UsageAtoms, UsageAtom{EventID: openCodeUsageEventID(part.SessionID, part.PartID, dimension), Direction: direction, Modality: "text", CacheRole: cache, Unit: "tokens", ObservedAt: float64(part.CompletedAt) / 1000, Payload: payload})
	}
}

func openCodeRevisionKey(sessionID, partID, dimension string) string {
	key := sessionID + ":" + partID + ":" + dimension
	if len([]byte("opencode:"+key)) <= MaxEventIDBytes {
		return key
	}
	return openCodeUsageEventID(sessionID, partID, dimension)
}

func openCodeTokenTelemetryComplete(tokens OpenCodeTokenSnapshot) bool {
	for _, dimension := range []string{"input", "cache_read", "cache_write", "output", "reasoning"} {
		if !tokens.Present[dimension] {
			return false
		}
	}
	return true
}

// openCodeUsageEventID mirrors Python's bounded identity contract. OpenCode
// normally uses short UUID-like session and part IDs, but a malformed or
// imported database can contain arbitrarily long text identifiers. Hash the
// complete readable identity before appending in that case so one long source
// row cannot reject the whole current token batch at MaxEventIDBytes.
func openCodeUsageEventID(sessionID, partID, dimension string) string {
	readable := "opencode:" + sessionID + ":" + partID + ":" + dimension
	if len([]byte(readable)) <= MaxEventIDBytes {
		return readable
	}
	digest := sha256.Sum256([]byte(readable))
	return "opencode:" + hex.EncodeToString(digest[:]) + ":" + dimension
}

func agentKeyForRecord(record AgentRecord, sessionID string) string {
	if record.TmuxSession != "" {
		return record.TmuxSession
	}
	return openCodeProcessSource(record)
}

type openCodeCursorState struct {
	Events                map[string]string             `json:"events"`
	Parts                 map[string]OpenCodePartCursor `json:"parts,omitempty"`
	RawScan               openCodeRawScanState          `json:"raw_scan,omitempty"`
	Device                uint64                        `json:"device,omitempty"`
	Inode                 uint64                        `json:"inode,omitempty"`
	WALDevice             uint64                        `json:"wal_device,omitempty"`
	WALInode              uint64                        `json:"wal_inode,omitempty"`
	StatsDatabaseIdentity string                        `json:"stats_database_identity,omitempty"`
}

func (s openCodeCursorState) clone() openCodeCursorState {
	out := openCodeCursorState{
		Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{},
		Device: s.Device, Inode: s.Inode, WALDevice: s.WALDevice, WALInode: s.WALInode,
		StatsDatabaseIdentity: s.StatsDatabaseIdentity,
		RawScan:               s.RawScan.clone(),
	}
	for k, v := range s.Events {
		out.Events[k] = v
	}
	for k, v := range s.Parts {
		out.Parts[k] = v
	}
	return out
}

type openCodeRawScanState struct {
	HasScan             bool              `json:"has_scan,omitempty"`
	SelectionKey        string            `json:"selection_key,omitempty"`
	ScannedThroughRowID int64             `json:"scanned_through_rowid,omitempty"`
	HighWaterRowID      int64             `json:"high_water_rowid,omitempty"`
	SourceDataVersion   int64             `json:"source_data_version,omitempty"`
	LastFullReconcileAt int64             `json:"last_full_reconcile_at,omitempty"`
	FullReconcile       bool              `json:"full_reconcile,omitempty"`
	RootErrors          map[string]string `json:"root_errors,omitempty"`
	ScanRootErrors      map[string]string `json:"scan_root_errors,omitempty"`
	ScanDirty           bool              `json:"scan_dirty,omitempty"`
	ForceRescan         bool              `json:"force_rescan,omitempty"`
	// ErrorRefs and ErrorCheckAfterRowID are read only to migrate v1 sidecars.
	ErrorRefs            []OpenCodeStepFinishErrorRef `json:"error_refs,omitempty"`
	ErrorCheckAfterRowID int64                        `json:"error_check_after_rowid,omitempty"`
}

func (s openCodeRawScanState) clone() openCodeRawScanState {
	out := s
	if s.HasScan && out.RootErrors == nil {
		out.RootErrors = map[string]string{}
	}
	if s.RootErrors != nil {
		out.RootErrors = make(map[string]string, len(s.RootErrors))
		for rootID, reason := range s.RootErrors {
			out.RootErrors[rootID] = reason
		}
	}
	out.ErrorRefs = append([]OpenCodeStepFinishErrorRef(nil), s.ErrorRefs...)
	if s.ScanRootErrors != nil {
		out.ScanRootErrors = make(map[string]string, len(s.ScanRootErrors))
		for rootID, reason := range s.ScanRootErrors {
			out.ScanRootErrors[rootID] = reason
		}
	} else if s.HasScan {
		out.ScanRootErrors = map[string]string{}
	}
	return out
}

func openCodeRootErrors(refs []OpenCodeStepFinishErrorRef) map[string]string {
	result := make(map[string]string)
	for _, ref := range refs {
		result[ref.RootSessionID] = "malformed-transcript-json"
	}
	return result
}

func cloneOpenCodeRootErrors(source map[string]string) map[string]string {
	clone := make(map[string]string, len(source))
	for rootID, reason := range source {
		clone[rootID] = reason
	}
	return clone
}

func openCodeSelectedRootErrors(selections []OpenCodeStepFinishSelection, errorsByRoot map[string]string) map[string]string {
	selected := make(map[string]bool, len(selections))
	for _, selection := range selections {
		selected[selection.RootSessionID] = true
	}
	filtered := make(map[string]string)
	for rootID, reason := range errorsByRoot {
		if selected[rootID] {
			filtered[rootID] = reason
		}
	}
	return filtered
}

func openCodeSelectionKey(selections []OpenCodeStepFinishSelection) string {
	identities := make([]string, 0, len(selections))
	for _, selection := range selections {
		identities = append(identities, selection.SessionID+"\x00"+selection.RootSessionID)
	}
	sort.Strings(identities)
	digest := sha256.Sum256([]byte(strings.Join(identities, "\n")))
	return hex.EncodeToString(digest[:])
}

func mergeOpenCodeStepFinishParts(existing, page []OpenCodeStepFinishPart) []OpenCodeStepFinishPart {
	merged := make([]OpenCodeStepFinishPart, 0, len(existing)+len(page))
	indices := make(map[string]int, len(existing)+len(page))
	for _, part := range existing {
		key := part.SessionID + "\x00" + part.PartID
		indices[key] = len(merged)
		merged = append(merged, part)
	}
	for _, part := range page {
		key := part.SessionID + "\x00" + part.PartID
		if index, ok := indices[key]; ok {
			merged[index] = part
			continue
		}
		indices[key] = len(merged)
		merged = append(merged, part)
	}
	return merged
}
func (s openCodeCursorState) changed(other openCodeCursorState) bool {
	return !reflect.DeepEqual(s.clone(), other.clone())
}

// OpenCodeTokenCursor is a small sidecar transaction. A pending journal is
// discarded on reload and the committed state is replayed through statsd's
// stable event-id dedupe; promoting the pending state would lose the ability
// to distinguish a crash before versus after the fact append.
type OpenCodeTokenCursor struct {
	path         string
	mu           sync.Mutex
	current      openCodeCursorState
	pending      *openCodeCursorState
	pendingToken string
	generation   uint64
}

func NewOpenCodeTokenCursor(path string) *OpenCodeTokenCursor {
	return &OpenCodeTokenCursor{path: path, current: openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}}}
}
func (c *OpenCodeTokenCursor) state() (openCodeCursorState, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.pending != nil {
		return openCodeCursorState{}, errors.New("cursor-state-pending")
	}
	var result openCodeCursorState
	err := withSidecarLock(c.path, func() error {
		disk, err := c.readDisk()
		if err != nil {
			return err
		}
		if disk.Pending != nil || disk.EventsCompacted {
			cleanGeneration := disk.Generation + 1
			clean := openCodeCursorDisk{Version: 2, Generation: cleanGeneration, State: disk.State}
			if err := atomicSidecarWrite(c.path, clean); err != nil {
				return err
			}
			disk.Generation = cleanGeneration
		}
		c.current, c.generation = disk.State, disk.Generation
		c.pending, c.pendingToken = nil, ""
		result = c.current.clone()
		return nil
	})
	return result, err
}

type openCodeCursorDisk struct {
	Version         int                  `json:"version"`
	Generation      uint64               `json:"generation,omitempty"`
	State           openCodeCursorState  `json:"state"`
	Pending         *openCodeCursorState `json:"pending,omitempty"`
	PendingToken    string               `json:"pending_token,omitempty"`
	EventsCompacted bool                 `json:"-"`
}

// boundOpenCodeEventRevisions matches Python's deterministic revision-map
// bound. Durable stats event IDs still deduplicate re-offered facts after an
// old local revision entry is evicted.
func boundOpenCodeEventRevisions(events map[string]string) {
	for key, revision := range events {
		if len([]byte(key)) > MaxEventIDBytes || len([]byte(revision)) > maxOpenCodeRevisionBytes {
			delete(events, key)
		}
	}
	if len(events) <= maxOpenCodeEventRevisionEntries {
		return
	}
	keys := make([]string, 0, len(events))
	for key := range events {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys[:len(keys)-maxOpenCodeEventRevisionEntries] {
		delete(events, key)
	}
}

func (c *OpenCodeTokenCursor) readDisk() (openCodeCursorDisk, error) {
	data, err := os.ReadFile(c.path)
	if os.IsNotExist(err) {
		return openCodeCursorDisk{Version: 2, State: openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}}}, nil
	}
	if err != nil {
		return openCodeCursorDisk{}, err
	}
	var disk openCodeCursorDisk
	if json.Unmarshal(data, &disk) != nil || (disk.Version != 0 && disk.Version != 1 && disk.Version != 2) {
		return openCodeCursorDisk{}, errors.New("cursor-state-malformed")
	}
	if disk.State.Events == nil {
		disk.State.Events = map[string]string{}
	}
	stateEventCount := len(disk.State.Events)
	boundOpenCodeEventRevisions(disk.State.Events)
	if len(disk.State.Events) != stateEventCount {
		disk.EventsCompacted = true
	}
	if disk.State.Parts == nil {
		disk.State.Parts = map[string]OpenCodePartCursor{}
	}
	if disk.Pending != nil {
		if disk.Pending.Events == nil {
			disk.Pending.Events = map[string]string{}
		}
		pendingEventCount := len(disk.Pending.Events)
		boundOpenCodeEventRevisions(disk.Pending.Events)
		if len(disk.Pending.Events) != pendingEventCount {
			disk.EventsCompacted = true
		}
	}
	if disk.State.RawScan.HasScan {
		if disk.State.RawScan.SelectionKey == "" || disk.State.RawScan.ScannedThroughRowID < 0 || disk.State.RawScan.HighWaterRowID < disk.State.RawScan.ScannedThroughRowID || disk.State.RawScan.SourceDataVersion < 0 || disk.State.RawScan.LastFullReconcileAt < 0 {
			return openCodeCursorDisk{}, errors.New("cursor-state-malformed")
		}
		if disk.State.RawScan.RootErrors == nil {
			disk.State.RawScan.RootErrors = map[string]string{}
		}
		if disk.State.RawScan.ScanRootErrors == nil {
			disk.State.RawScan.ScanRootErrors = map[string]string{}
		}
		if disk.Version < 2 {
			seenErrorRows := make(map[int64]bool, len(disk.State.RawScan.ErrorRefs))
			for _, ref := range disk.State.RawScan.ErrorRefs {
				if ref.RowID <= 0 || strings.TrimSpace(ref.RootSessionID) == "" || strings.TrimSpace(ref.SessionID) == "" || seenErrorRows[ref.RowID] {
					return openCodeCursorDisk{}, errors.New("cursor-state-malformed")
				}
				seenErrorRows[ref.RowID] = true
			}
			if !reflect.DeepEqual(disk.State.RawScan.RootErrors, openCodeRootErrors(disk.State.RawScan.ErrorRefs)) {
				return openCodeCursorDisk{}, errors.New("cursor-state-malformed")
			}
			// v1 retained one reference per malformed row. Keep its published
			// root summary until a full bounded scan can verify it, then stop
			// carrying row-level references in the sidecar.
			disk.State.RawScan.ErrorRefs = nil
			disk.State.RawScan.ErrorCheckAfterRowID = 0
			disk.State.RawScan.ScanRootErrors = map[string]string{}
			disk.State.RawScan.ForceRescan = true
		} else if len(disk.State.RawScan.ErrorRefs) != 0 || disk.State.RawScan.ErrorCheckAfterRowID != 0 {
			return openCodeCursorDisk{}, errors.New("cursor-state-malformed")
		}
		if len(disk.State.RawScan.RootErrors) > MaxOpenCodeSessionTree || len(disk.State.RawScan.ScanRootErrors) > MaxOpenCodeSessionTree {
			return openCodeCursorDisk{}, errors.New("cursor-state-malformed")
		}
	}
	disk.Version = 2
	return disk, nil
}
func (c *OpenCodeTokenCursor) prepare(before, next openCodeCursorState) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.pending != nil {
		return errors.New("cursor-state-pending")
	}
	return withSidecarLock(c.path, func() error {
		disk, err := c.readDisk()
		if err != nil {
			return err
		}
		if disk.Pending != nil {
			return errors.New("cursor-state-pending")
		}
		if disk.State.changed(before) {
			return errors.New("cursor-state-stale")
		}
		token := strconv.FormatUint(disk.Generation+1, 10) + "-" + strconv.FormatInt(time.Now().UnixNano(), 10)
		boundOpenCodeEventRevisions(next.Events)
		prepared := next.clone()
		staged := openCodeCursorDisk{Version: 2, Generation: disk.Generation + 1, State: disk.State, Pending: &prepared, PendingToken: token}
		if err := atomicSidecarWrite(c.path, staged); err != nil {
			return err
		}
		c.current, c.generation = disk.State, staged.Generation
		c.pending, c.pendingToken = &prepared, token
		return nil
	})
}
func (c *OpenCodeTokenCursor) commit(expected openCodeCursorState) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.pending == nil {
		return errors.New("cursor-state-pending-missing")
	}
	return withSidecarLock(c.path, func() error {
		disk, err := c.readDisk()
		if err != nil {
			return err
		}
		if disk.Pending == nil || disk.PendingToken == "" || disk.PendingToken != c.pendingToken || disk.Pending.changed(expected) {
			return errors.New("cursor-state-stale")
		}
		committed := disk.Pending.clone()
		staged := openCodeCursorDisk{Version: 2, Generation: disk.Generation + 1, State: committed}
		if err := atomicSidecarWrite(c.path, staged); err != nil {
			return err
		}
		c.current, c.generation, c.pending, c.pendingToken = committed, staged.Generation, nil, ""
		return nil
	})
}
func (c *OpenCodeTokenCursor) rollback() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.pending == nil {
		return nil
	}
	err := withSidecarLock(c.path, func() error {
		disk, err := c.readDisk()
		if err != nil {
			return err
		}
		if disk.PendingToken != c.pendingToken {
			return errors.New("cursor-state-stale")
		}
		staged := openCodeCursorDisk{Version: 2, Generation: disk.Generation + 1, State: disk.State}
		if err := atomicSidecarWrite(c.path, staged); err != nil {
			return err
		}
		c.current, c.generation, c.pending, c.pendingToken = disk.State, staged.Generation, nil, ""
		return nil
	})
	if err != nil {
		// The durable pending journal remains authoritative and will be discarded
		// or replayed by state() once the sidecar is readable again. Do not keep a
		// failed in-memory receipt wedged across every subsequent collection.
		c.pending, c.pendingToken = nil, ""
	}
	return err
}
func (c *OpenCodeTokenCursor) write() error {
	disk := openCodeCursorDisk{Version: 2, Generation: c.generation, State: c.current, Pending: c.pending, PendingToken: c.pendingToken}
	return atomicSidecarWrite(c.path, disk)
}

type transcriptDiscoveryBounds struct {
	maxFiles   int
	maxDirs    int
	maxBytes   int64
	maxEntries int
}

func transcriptCycleBudgets(maxBytes int64, maxRecords int, codexRepairActive bool) (int64, int) {
	if codexRepairActive && maxBytes == defaultTranscriptCycleMaxBytes && maxRecords == defaultTranscriptCycleMaxRecords {
		// Python raises only its default scan budgets during the one-time fork
		// repair. Keep that throughput parity without overriding explicit bounds.
		return legacyCodexRepairCycleMaxBytes, legacyCodexRepairCycleMaxRecords
	}
	return maxBytes, maxRecords
}

type transcriptDiscoveryDirectoryCursor struct {
	cookie       int64
	device       uint64
	inode        uint64
	modTimeNS    int64
	changeTimeNS int64
	pending      []string
	complete     bool
}

type transcriptDiscoveryCursorStore struct {
	mu                 sync.Mutex
	directories        map[string]transcriptDiscoveryDirectoryCursor
	frontiers          map[string][]string
	frontierOrder      []string
	deferNamesOverride func(string, []string, bool, transcriptDiscoveryDirectoryCursor) error
}

type transcriptPendingDirectory struct {
	path    string
	modTime time.Time
}

func newTranscriptDiscoveryCursorStore() *transcriptDiscoveryCursorStore {
	return &transcriptDiscoveryCursorStore{directories: make(map[string]transcriptDiscoveryDirectoryCursor), frontiers: make(map[string][]string)}
}

func (s *transcriptDiscoveryCursorStore) load(path string, info os.FileInfo) transcriptDiscoveryDirectoryCursor {
	device, inode := fileIdentity(info)
	changeTimeNS := codexDirectoryChangeTimeNS(info)
	s.mu.Lock()
	defer s.mu.Unlock()
	cursor, ok := s.directories[path]
	if !ok || cursor.device != device || cursor.inode != inode || cursor.modTimeNS != info.ModTime().UnixNano() || cursor.changeTimeNS != changeTimeNS {
		s.deleteDirectorySubtreeLocked(path)
		return transcriptDiscoveryDirectoryCursor{device: device, inode: inode, modTimeNS: info.ModTime().UnixNano(), changeTimeNS: changeTimeNS}
	}
	cursor.pending = append([]string(nil), cursor.pending...)
	return cursor
}

func (s *transcriptDiscoveryCursorStore) save(path string, cursor transcriptDiscoveryDirectoryCursor) {
	s.mu.Lock()
	defer s.mu.Unlock()
	cursor.pending = append([]string(nil), cursor.pending...)
	if cursor.cookie == 0 && len(cursor.pending) == 0 {
		delete(s.directories, path)
		return
	}
	s.directories[path] = cursor
}

func (s *transcriptDiscoveryCursorStore) deferNames(path string, names []string, complete bool, observed transcriptDiscoveryDirectoryCursor) error {
	if s.deferNamesOverride != nil {
		return s.deferNamesOverride(path, names, complete, observed)
	}
	return s.deferNamesDefault(path, names, complete, observed)
}

func (s *transcriptDiscoveryCursorStore) deferNamesDefault(path string, names []string, complete bool, observed transcriptDiscoveryDirectoryCursor) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	info, err := directory.Stat()
	if err != nil {
		return err
	}
	device, inode := fileIdentity(info)
	if device != observed.device || inode != observed.inode || info.ModTime().UnixNano() != observed.modTimeNS || codexDirectoryChangeTimeNS(info) != observed.changeTimeNS {
		// The names were read from a different directory version. Do not attach
		// that stale page to the replacement/current cursor or mark it complete;
		// the caller will resume the directory and enumerate the current version.
		s.save(path, transcriptDiscoveryDirectoryCursor{})
		return errTranscriptDiscoveryDirectoryChanged
	}
	cursor := s.load(path, info)
	cursor.pending = append(append([]string(nil), names...), cursor.pending...)
	cursor.complete = cursor.complete || complete
	s.save(path, cursor)
	return nil
}

func (s *transcriptDiscoveryCursorStore) loadFrontier(root string) []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	paths, ok := s.frontiers[root]
	if !ok {
		return nil
	}
	s.touchFrontierRootLocked(root)
	return append([]string(nil), paths...)
}

func (s *transcriptDiscoveryCursorStore) saveFrontier(root string, paths []string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(paths) == 0 {
		s.deleteFrontierRootLocked(root)
		return nil
	}
	if len(paths) > maxTranscriptDiscoveryFrontierPaths {
		return errTranscriptDiscoveryFrontierBudgetExceeded
	}
	if _, exists := s.frontiers[root]; !exists && len(s.frontierOrder) >= maxTranscriptDiscoveryFrontierRoots {
		s.deleteFrontierRootLocked(s.frontierOrder[0])
	}
	s.frontiers[root] = append([]string(nil), paths...)
	s.touchFrontierRootLocked(root)
	s.pruneDirectoryCursorsLocked()
	return nil
}

func (s *transcriptDiscoveryCursorStore) touchFrontierRootLocked(root string) {
	for index, current := range s.frontierOrder {
		if current == root {
			s.frontierOrder = append(s.frontierOrder[:index], s.frontierOrder[index+1:]...)
			break
		}
	}
	s.frontierOrder = append(s.frontierOrder, root)
}

func (s *transcriptDiscoveryCursorStore) deleteFrontierRootLocked(root string) {
	delete(s.frontiers, root)
	for index, current := range s.frontierOrder {
		if current == root {
			s.frontierOrder = append(s.frontierOrder[:index], s.frontierOrder[index+1:]...)
			break
		}
	}
	s.deleteDirectorySubtreeLocked(root)
}

func (s *transcriptDiscoveryCursorStore) deleteDirectorySubtreeLocked(root string) {
	for path := range s.directories {
		relative, err := filepath.Rel(root, path)
		if err == nil && (relative == "." || (relative != ".." && !strings.HasPrefix(relative, ".."+string(os.PathSeparator)))) {
			delete(s.directories, path)
		}
	}
}

func (s *transcriptDiscoveryCursorStore) discardDirectorySubtree(path string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.deleteDirectorySubtreeLocked(path)
}

func (s *transcriptDiscoveryCursorStore) pruneDirectoryCursorsLocked() {
	retained := make(map[string]struct{})
	for _, paths := range s.frontiers {
		for _, path := range paths {
			retained[path] = struct{}{}
		}
	}
	for path := range s.directories {
		if _, keep := retained[path]; !keep {
			delete(s.directories, path)
		}
	}
}

func prependTranscriptDiscoveryChild(queue []transcriptPendingDirectory, child transcriptPendingDirectory, maxFrontier int) ([]transcriptPendingDirectory, bool) {
	// Leave one slot for the parent directory that must be resumed after its
	// prioritized child; otherwise a deep tree could grow the saved frontier on
	// every descent despite a per-cycle directory limit.
	if maxFrontier < 2 || len(queue) >= maxFrontier-1 {
		return queue, false
	}
	return append([]transcriptPendingDirectory{child}, queue...), true
}

const defaultTranscriptDiscoveryEntries = 8192
const maxTranscriptDiscoveryFrontierPaths = 4096 // above the maximum depth of a valid POSIX path
const maxTranscriptDiscoveryFrontierRoots = maxAgentTokenSources

type transcriptCandidate struct {
	path    string
	modTime time.Time
}

// defaultAgentTokenSources remains the fixture-friendly entry point. Production
// configuration supplies the same bounds through AgentTokenCollectorConfig.
func defaultAgentTokenSources(ctx context.Context, records []AgentRecord, configuredRoots ...string) ([]TranscriptSource, error) {
	return defaultAgentTokenSourcesBounded(ctx, records, transcriptDiscoveryBounds{
		maxFiles: defaultTranscriptDiscoveryFiles,
		maxDirs:  defaultTranscriptDiscoveryDirs,
		maxBytes: defaultTranscriptDiscoveryBytes,
	}, configuredRoots...)
}

func defaultAgentTokenSourcesBounded(ctx context.Context, records []AgentRecord, bounds transcriptDiscoveryBounds, configuredRoots ...string) ([]TranscriptSource, error) {
	return defaultAgentTokenSourcesBoundedWithCursor(ctx, records, bounds, nil, configuredRoots...)
}

func defaultAgentTokenSourcesBoundedWithCursor(ctx context.Context, records []AgentRecord, bounds transcriptDiscoveryBounds, cursors *transcriptDiscoveryCursorStore, configuredRoots ...string) ([]TranscriptSource, error) {
	if cursors == nil {
		cursors = newTranscriptDiscoveryCursorStore()
	}
	seen := map[string]bool{}
	out := []TranscriptSource{}
	coldPriority := map[string]int{}
	claudeCandidateCache := map[string][]transcriptCandidate{}
	claudeCandidateErrors := map[string]error{}
	claudeCandidateCached := map[string]bool{}
	claudeCandidates := func(root string) ([]transcriptCandidate, error) {
		root = filepath.Clean(root)
		if claudeCandidateCached[root] {
			return claudeCandidateCache[root], claudeCandidateErrors[root]
		}
		candidates, err := recentTranscriptCandidatesWithCursor(ctx, root, bounds, cursors)
		claudeCandidateCache[root] = candidates
		claudeCandidateErrors[root] = err
		claudeCandidateCached[root] = true
		return candidates, err
	}
	fallbackRecords := make([]AgentRecord, 0, len(records))
	exactCodexScopes := make(map[string]bool)
	// Resolve exact Codex rollouts from the live process file descriptors before
	// walking either transcript root. A large historical root can consume the
	// bounded discovery interval; letting an unrelated Claude walk run first
	// would otherwise prevent the active Codex pane from ever contributing.
	for _, record := range records {
		if err := ctx.Err(); err != nil {
			return out, err
		}
		if record.Kind != "claude" && record.Kind != "codex" {
			continue
		}
		if record.TranscriptPath != "" {
			addTranscriptSource(&out, seen, record.TranscriptPath, record, true)
			if record.Kind == "claude" {
				candidates, err := claudeCandidates(filepath.Dir(filepath.Clean(record.TranscriptPath)))
				for priority, candidate := range candidates {
					path := filepath.Clean(candidate.path)
					if current, exists := coldPriority[path]; !exists || priority < current {
						coldPriority[path] = priority
					}
				}
				if err != nil {
					return out, err
				}
			}
			if record.Kind == "codex" {
				exactCodexScopes[agentTokenScope(record)] = true
			}
			continue
		}
		if record.Kind == "codex" {
			root := defaultTranscriptRoot(record.Kind, configuredRoots...)
			if exact := codexTranscriptFromProcessFD(record.PID, root); exact != "" {
				addTranscriptSource(&out, seen, exact, record, true)
				exactCodexScopes[agentTokenScope(record)] = true
				continue
			}
		}
		fallbackRecords = append(fallbackRecords, record)
	}
	for _, record := range fallbackRecords {
		if err := ctx.Err(); err != nil {
			return out, err
		}
		// The native Codex process and its launcher wrapper can both appear in
		// one pane. Once the native process supplied an exact open rollout, do
		// not make the wrapper trigger a second historical-root walk.
		if record.Kind == "codex" && exactCodexScopes[agentTokenScope(record)] {
			continue
		}
		root := defaultTranscriptRoot(record.Kind, configuredRoots...)
		if root == "" {
			return out, errors.New("transcript-root-unconfigured")
		}
		var candidates []transcriptCandidate
		var err error
		if record.Kind == "claude" {
			candidates, err = claudeCandidates(root)
		} else {
			candidates, err = recentTranscriptCandidatesWithCursor(ctx, root, bounds, cursors)
		}
		for priority, candidate := range candidates {
			if len(out) >= maxAgentTokenSources {
				break
			}
			addTranscriptSource(&out, seen, candidate.path, record, false)
			// Keep Python's project-local newest-first rank through the final
			// global tie-break; a path-only sort can put older history first.
			if record.Kind == "claude" {
				path := filepath.Clean(candidate.path)
				if current, exists := coldPriority[path]; !exists || priority < current {
					coldPriority[path] = priority
				}
			}
		}
		if err != nil {
			return out, err
		}
	}
	sort.Slice(out, func(i, j int) bool {
		leftPriority, rightPriority := coldPriority[out[i].Path], coldPriority[out[j].Path]
		if leftPriority != rightPriority {
			return leftPriority < rightPriority
		}
		return out[i].Path < out[j].Path
	})
	return out, nil
}

func agentTokenScope(record AgentRecord) string {
	if record.TmuxPaneID != "" {
		return "pane:" + record.TmuxPaneID
	}
	if record.TmuxSession != "" {
		return "session:" + record.TmuxSession
	}
	return fmt.Sprintf("pid:%d", record.PID)
}

// codexTranscriptFromProcessFD mirrors the Python session resolver's
// process-owned lookup: the active Codex process has the rollout JSONL open,
// so resolving that descriptor is both exact and independent of a potentially
// multi-gigabyte historical CODEX_HOME tree.
func codexTranscriptFromProcessFD(pid int, root string) string {
	if pid <= 0 || root == "" {
		return ""
	}
	entries, err := os.ReadDir(filepath.Join("/proc", strconv.Itoa(pid), "fd"))
	if err != nil {
		return ""
	}
	targets := make([]string, 0, len(entries))
	for _, entry := range entries {
		target, readErr := os.Readlink(filepath.Join("/proc", strconv.Itoa(pid), "fd", entry.Name()))
		if readErr == nil {
			targets = append(targets, target)
		}
	}
	return newestCodexTranscriptTarget(root, targets)
}

func newestCodexTranscriptTarget(root string, targets []string) string {
	root = filepath.Clean(root)
	candidates := make([]string, 0, len(targets))
	for _, target := range targets {
		target = strings.TrimSuffix(target, " (deleted)")
		if filepath.Base(target) == "" || !strings.HasPrefix(filepath.Base(target), "rollout-") || filepath.Ext(target) != ".jsonl" {
			continue
		}
		relative, err := filepath.Rel(root, target)
		if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) {
			continue
		}
		if _, err := os.Stat(target); err != nil {
			continue
		}
		candidates = append(candidates, filepath.Clean(target))
	}
	sort.Slice(candidates, func(i, j int) bool {
		left, leftErr := os.Stat(candidates[i])
		right, rightErr := os.Stat(candidates[j])
		if leftErr == nil && rightErr == nil && !left.ModTime().Equal(right.ModTime()) {
			return left.ModTime().After(right.ModTime())
		}
		return candidates[i] < candidates[j]
	})
	if len(candidates) == 0 {
		return ""
	}
	return candidates[0]
}

func addTranscriptSource(out *[]TranscriptSource, seen map[string]bool, path string, record AgentRecord, liveCandidate bool) {
	path = filepath.Clean(path)
	if filepath.Ext(path) != ".jsonl" || len(*out) >= maxAgentTokenSources {
		return
	}
	if seen[path] {
		if liveCandidate {
			for index := range *out {
				if (*out)[index].Path == path {
					(*out)[index].LiveCandidate = true
					break
				}
			}
		}
		return
	}
	seen[path] = true
	*out = append(*out, TranscriptSource{Path: path, Provider: record.Kind, SourceID: record.Kind + ":" + path, ThreadID: record.TmuxSession, AgentID: transcriptAgentID(record), LiveCandidate: liveCandidate})
}

// recentTranscriptCandidates never reads transcript contents. It visits a
// bounded recent-first directory frontier, so an old Codex tree cannot turn
// token collection into an 11GB recursive walk.
func recentTranscriptCandidates(ctx context.Context, root string, bounds transcriptDiscoveryBounds) ([]transcriptCandidate, error) {
	return recentTranscriptCandidatesWithCursor(ctx, root, bounds, newTranscriptDiscoveryCursorStore())
}

func recentTranscriptCandidatesWithCursor(ctx context.Context, root string, bounds transcriptDiscoveryBounds, cursors *transcriptDiscoveryCursorStore) ([]transcriptCandidate, error) {
	if bounds.maxFiles <= 0 || bounds.maxDirs <= 0 || bounds.maxBytes <= 0 {
		return nil, errors.New("transcript-discovery-bound-invalid")
	}
	if cursors == nil {
		cursors = newTranscriptDiscoveryCursorStore()
	}
	if bounds.maxEntries <= 0 {
		bounds.maxEntries = defaultTranscriptDiscoveryEntries
	}
	root = filepath.Clean(root)
	queue := make([]transcriptPendingDirectory, 0, min(bounds.maxDirs, maxTranscriptDiscoveryFrontierPaths))
	for _, path := range cursors.loadFrontier(root) {
		info, err := os.Stat(path)
		if err == nil && info.IsDir() {
			queue = append(queue, transcriptPendingDirectory{path: path, modTime: info.ModTime()})
		} else {
			cursors.discardDirectorySubtree(path)
		}
	}
	if len(queue) == 0 {
		queue = append(queue, transcriptPendingDirectory{path: root})
	}
	saveFrontier := func(directories []transcriptPendingDirectory) error {
		paths := make([]string, 0, len(directories))
		for _, directory := range directories {
			paths = append(paths, directory.path)
		}
		return cursors.saveFrontier(root, paths)
	}
	candidates := make([]transcriptCandidate, 0, bounds.maxFiles)
	inspectedDirs, inspectedFiles, inspectedEntries := 0, 0, 0
	var inspectedBytes int64
	byteBudgetExceeded := false
	var discoveryErr error
	sortCandidates := func() {
		sort.Slice(candidates, func(i, j int) bool {
			if !candidates[i].modTime.Equal(candidates[j].modTime) {
				return candidates[i].modTime.After(candidates[j].modTime)
			}
			return candidates[i].path < candidates[j].path
		})
	}
	savePartial := func(discoveryErr error) ([]transcriptCandidate, error) {
		if saveErr := saveFrontier(queue); saveErr != nil {
			discoveryErr = errors.Join(discoveryErr, saveErr)
		}
		sortCandidates()
		return candidates, discoveryErr
	}
	for len(queue) > 0 {
		if err := ctx.Err(); err != nil {
			return savePartial(err)
		}
		if inspectedDirs >= bounds.maxDirs {
			return savePartial(errTranscriptDiscoveryDirectoryBudgetExhausted)
		}
		dir := queue[0]
		queue = queue[1:]
		inspectedDirs++
		if bounds.maxEntries-inspectedEntries <= 0 {
			queue = append([]transcriptPendingDirectory{dir}, queue...)
			return savePartial(errTranscriptDiscoveryEntryBudgetExhausted)
		}
		var directoryVersion transcriptDiscoveryDirectoryCursor
		entries, err := readTranscriptDirectoryBounded(ctx, dir.path, bounds.maxEntries-inspectedEntries, cursors, &directoryVersion)
		if err != nil && !errors.Is(err, errTranscriptDiscoveryEntryBudgetExhausted) && !isTranscriptDiscoveryInterrupted(err) {
			if os.IsNotExist(err) {
				continue
			}
			queue = append([]transcriptPendingDirectory{dir}, queue...)
			return savePartial(err)
		}
		inspectedEntries += len(entries)
		readComplete := err == nil
		sort.Slice(entries, func(i, j int) bool { return entries[i].name > entries[j].name })
		childDirs := make([]transcriptPendingDirectory, 0)
		preserveChildren := func(names []string) (bool, bool, error) {
			sort.Slice(childDirs, func(i, j int) bool {
				if !childDirs[i].modTime.Equal(childDirs[j].modTime) {
					return childDirs[i].modTime.After(childDirs[j].modTime)
				}
				return childDirs[i].path < childDirs[j].path
			})
			queued := make(map[string]struct{}, len(queue))
			for _, pending := range queue {
				queued[pending.path] = struct{}{}
			}
			deferredChildren := false
			scheduledChild := false
			for _, child := range childDirs {
				if _, exists := queued[child.path]; exists {
					continue
				}
				if !scheduledChild {
					var scheduled bool
					queue, scheduled = prependTranscriptDiscoveryChild(queue, child, maxTranscriptDiscoveryFrontierPaths)
					if !scheduled {
						names = append(names, filepath.Base(child.path))
						deferredChildren = true
						continue
					}
					queued[child.path] = struct{}{}
					scheduledChild = true
					continue
				}
				names = append(names, filepath.Base(child.path))
				deferredChildren = true
			}
			if len(names) > 0 {
				if err := cursors.deferNames(dir.path, names, readComplete, directoryVersion); err != nil {
					// The directory reader may already have advanced its page cursor.
					// Restart this directory so an unpersisted continuation cannot skip
					// names when the parent is retried.
					cursors.save(dir.path, transcriptDiscoveryDirectoryCursor{})
					return deferredChildren, scheduledChild, err
				}
			}
			childDirs = nil
			return deferredChildren, scheduledChild, nil
		}
		resumeDirectory := func(scheduledChild bool) {
			if scheduledChild && len(queue) > 0 {
				queue = append(queue[:1], append([]transcriptPendingDirectory{dir}, queue[1:]...)...)
				return
			}
			queue = append([]transcriptPendingDirectory{dir}, queue...)
		}
		fileBudgetExceeded := false
		currentChildScheduled := false
		for entryIndex, entry := range entries {
			if err := ctx.Err(); err != nil {
				remaining := make([]string, 0, len(entries)-entryIndex)
				for _, pending := range entries[entryIndex:] {
					remaining = append(remaining, pending.name)
				}
				_, currentChildScheduled, deferErr := preserveChildren(remaining)
				if deferErr != nil {
					resumeDirectory(currentChildScheduled)
					return savePartial(deferErr)
				}
				resumeDirectory(currentChildScheduled)
				return savePartial(err)
			}
			if entry.err != nil {
				if discoveryErr == nil {
					discoveryErr = entry.err
				}
				continue
			}
			path := filepath.Join(dir.path, entry.name)
			info := entry.info
			if info.IsDir() {
				childDirs = append(childDirs, transcriptPendingDirectory{path: path, modTime: info.ModTime()})
				continue
			}
			if filepath.Ext(path) != ".jsonl" || inspectedFiles >= bounds.maxFiles {
				if inspectedFiles >= bounds.maxFiles {
					remaining := make([]string, 0, len(entries)-entryIndex)
					for _, pending := range entries[entryIndex:] {
						remaining = append(remaining, pending.name)
					}
					var deferErr error
					_, currentChildScheduled, deferErr = preserveChildren(remaining)
					if deferErr != nil {
						resumeDirectory(currentChildScheduled)
						return savePartial(deferErr)
					}
					fileBudgetExceeded = true
					break
				}
				continue
			}
			if info.Size() > bounds.maxBytes-inspectedBytes {
				// Keep the newest candidate even when its historical size exceeds
				// the discovery budget. ScanTranscript applies the per-cadence
				// budget to a bounded tail, so rejecting this path would hide a
				// source carrying current usage. Continue stat'ing other bounded
				// candidates so filename order cannot hide a newer transcript.
				inspectedFiles++
				candidates = append(candidates, transcriptCandidate{path: path, modTime: info.ModTime()})
				byteBudgetExceeded = true
				continue
			}
			inspectedFiles++
			inspectedBytes += info.Size()
			candidates = append(candidates, transcriptCandidate{path: path, modTime: info.ModTime()})
			if inspectedBytes >= bounds.maxBytes {
				byteBudgetExceeded = true
			}
		}
		deferredChildren, childScheduled, deferErr := preserveChildren(nil)
		if deferErr != nil {
			resumeDirectory(currentChildScheduled || childScheduled)
			return savePartial(deferErr)
		}
		resumeDir := deferredChildren
		resumeDirBeforeQueue := false
		if errors.Is(err, errTranscriptDiscoveryEntryBudgetExhausted) || fileBudgetExceeded || isTranscriptDiscoveryInterrupted(err) {
			resumeDir = true
			resumeDirBeforeQueue = true
		}
		if resumeDir {
			if resumeDirBeforeQueue {
				resumeDirectory(currentChildScheduled || childScheduled)
			} else {
				resumeDirectory(childScheduled)
			}
		}
		if errors.Is(err, errTranscriptDiscoveryEntryBudgetExhausted) || fileBudgetExceeded || isTranscriptDiscoveryInterrupted(err) {
			if fileBudgetExceeded {
				return savePartial(errTranscriptDiscoveryFileBudgetExhausted)
			}
			return savePartial(err)
		}
	}
	if err := saveFrontier(nil); err != nil {
		return candidates, err
	}
	sortCandidates()
	if byteBudgetExceeded {
		return candidates, errors.New("transcript-discovery-byte-budget-exhausted")
	}
	if discoveryErr != nil {
		return candidates, discoveryErr
	}
	return candidates, nil
}

var errTranscriptDiscoveryEntryBudgetExhausted = errors.New("transcript-discovery-entry-budget-exhausted")
var errTranscriptDiscoveryFileBudgetExhausted = errors.New("transcript-discovery-file-budget-exhausted")
var errTranscriptDiscoveryDirectoryBudgetExhausted = errors.New("transcript-discovery-directory-budget-exhausted")
var errTranscriptDiscoveryFrontierBudgetExceeded = errors.New("transcript-discovery-frontier-budget-exceeded")
var errTranscriptDiscoveryDirectoryChanged = errors.New("transcript-discovery-directory-changed-during-deferral")

func isTranscriptDiscoveryInterrupted(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
}

type transcriptDirectoryEntry struct {
	name string
	info os.FileInfo
	err  error
}

func readTranscriptDirectoryBounded(ctx context.Context, path string, maxEntries int, cursors *transcriptDiscoveryCursorStore, observed *transcriptDiscoveryDirectoryCursor) ([]transcriptDirectoryEntry, error) {
	if maxEntries <= 0 {
		return nil, errTranscriptDiscoveryEntryBudgetExhausted
	}
	directory, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer directory.Close()
	info, err := directory.Stat()
	if err != nil {
		return nil, err
	}
	cursor := cursors.load(path, info)
	if observed != nil {
		*observed = transcriptDiscoveryDirectoryCursor{
			device: cursor.device, inode: cursor.inode, modTimeNS: cursor.modTimeNS, changeTimeNS: cursor.changeTimeNS,
		}
	}

	entries := make([]transcriptDirectoryEntry, 0, min(maxEntries, 256))
	appendName := func(name string) {
		entryInfo, statErr := os.Lstat(filepath.Join(path, name))
		entries = append(entries, transcriptDirectoryEntry{name: name, info: entryInfo, err: statErr})
	}
	if cursor.complete && len(cursor.pending) == 0 {
		cursors.save(path, transcriptDiscoveryDirectoryCursor{})
		return entries, nil
	}
	for len(cursor.pending) > 0 {
		if err := ctx.Err(); err != nil {
			cursors.save(path, cursor)
			return entries, err
		}
		if len(entries) >= maxEntries {
			cursors.save(path, cursor)
			return entries, errTranscriptDiscoveryEntryBudgetExhausted
		}
		name := cursor.pending[0]
		cursor.pending = cursor.pending[1:]
		if name != "." && name != ".." {
			appendName(name)
		}
	}
	if cursor.complete {
		cursors.save(path, transcriptDiscoveryDirectoryCursor{})
		return entries, nil
	}
	for {
		if err := ctx.Err(); err != nil {
			cursors.save(path, cursor)
			return entries, err
		}
		previousCookie := cursor.cookie
		names, nextCookie, done, err := readCodexDirectoryPage(directory, previousCookie)
		if err != nil {
			cursors.save(path, cursor)
			return entries, err
		}
		sort.Strings(names)
		cursor.cookie = nextCookie
		cursor.complete = done
		for index, name := range names {
			if name == "." || name == ".." {
				continue
			}
			if err := ctx.Err(); err != nil {
				cursor.pending = append(cursor.pending, names[index:]...)
				cursors.save(path, cursor)
				return entries, err
			}
			if len(entries) >= maxEntries {
				cursor.pending = append(cursor.pending, names[index:]...)
				cursors.save(path, cursor)
				return entries, errTranscriptDiscoveryEntryBudgetExhausted
			}
			appendName(name)
		}
		if done {
			cursor.complete = true
			cursors.save(path, cursor)
			return entries, nil
		}
		if nextCookie == previousCookie && len(names) == 0 {
			cursors.save(path, cursor)
			return entries, errors.New("transcript-directory-page-did-not-advance")
		}
	}
}

func defaultTranscriptRoot(kind string, configuredRoots ...string) string {
	if kind == "claude" && len(configuredRoots) > 0 && strings.TrimSpace(configuredRoots[0]) != "" {
		return filepath.Clean(configuredRoots[0])
	}
	if kind == "codex" && len(configuredRoots) > 1 && strings.TrimSpace(configuredRoots[1]) != "" {
		return filepath.Clean(configuredRoots[1])
	}
	// Production resolves and passes explicit roots from cmd/yolomux-go.  Do not
	// fall back to HOME/CODEX_HOME here: a missing configuration must be a
	// bounded unavailable source, never an accidental read of developer state.
	return ""
}

func transcriptAgentID(record AgentRecord) string {
	key, _ := agentStatusIdentity(record, 0)
	return key
}

func openCodeRevision(part OpenCodeStepFinishPart) string {
	present := make([]string, 0, len(part.Tokens.Present))
	for dimension, value := range part.Tokens.Present {
		if value {
			present = append(present, dimension)
		}
	}
	sort.Strings(present)
	h := sha256.Sum256([]byte(fmt.Sprintf("%s:%s:%d:%d:%d:%d:%d:%d:%d:%s:%s:%s", part.PartID, part.MessageID, part.CreatedAt, part.CompletedAt, part.Tokens.Input, part.Tokens.Output, part.Tokens.Reasoning, part.Tokens.CacheRead, part.Tokens.CacheWrite, strings.Join(present, ","), part.Provider, part.Model)))
	return hex.EncodeToString(h[:])
}

var _ = openCodeRevision
var _ = strings.TrimSpace
