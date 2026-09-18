package stats

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

const (
	AgentTokenActiveCadence           = 10 * time.Second
	AgentTokenIdleCadence             = time.Minute
	maxAgentTokenSources              = 256
	maxAgentTokenRecords              = 4096
	defaultTranscriptDiscoveryFiles   = 256
	defaultTranscriptDiscoveryDirs    = 512
	defaultTranscriptDiscoveryBytes   = 64 * 1024 * 1024
	defaultTranscriptDiscoveryTimeout = 500 * time.Millisecond
	defaultOpenCodeInventoryTimeout   = 750 * time.Millisecond
	defaultOpenCodeCollectionTimeout  = 4 * time.Second
	defaultOpenCodeAppendTimeout      = 2 * time.Second
	defaultAuxiliaryInventoryTimeout  = 250 * time.Millisecond
	maxDiagnosticPathBytes            = 256
	maxDiagnosticTextBytes            = 512
	maxAgentTokenEpochIDBytes         = 128
)

// AgentTokenInventory is deliberately the same coherent inventory boundary as
// the status collector. Source resolution is separate so tests can copy real
// transcript/database fixtures without creating processes or tmux panes.
type AgentTokenInventory func(context.Context) ([]AgentRecord, []error)
type AgentTokenSources func(context.Context, []AgentRecord) ([]TranscriptSource, error)

type AgentTokenCollectorConfig struct {
	Inventory                   AgentTokenInventory
	OpenCodeInventory           AgentTokenInventory
	TranscriptSources           AgentTokenSources
	ClaudeTranscriptRoot        string
	CodexTranscriptRoot         string
	OpenCodeDatabase            string
	CursorPath                  string
	TranscriptCursorPath        string
	SkipTranscriptScan          bool
	EpochID                     string
	EpochStartedAt              float64
	OwnerGeneration             uint64
	SourceID                    string
	ActiveFunc                  func() bool
	TranscriptDiscoveryMaxFiles int
	TranscriptDiscoveryMaxDirs  int
	TranscriptDiscoveryMaxBytes int64
	TranscriptDiscoveryTimeout  time.Duration
	OpenCodeInventoryTimeout    time.Duration
	OpenCodeCollectionTimeout   time.Duration
	OpenCodeAppendTimeout       time.Duration
	AuxiliaryInventoryTimeout   time.Duration
}

func (c AgentTokenCollectorConfig) withDefaults() AgentTokenCollectorConfig {
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
		c.TranscriptSources = func(ctx context.Context, records []AgentRecord) ([]TranscriptSource, error) {
			return defaultAgentTokenSourcesBounded(ctx, records, transcriptDiscoveryBounds{
				maxFiles: c.TranscriptDiscoveryMaxFiles,
				maxDirs:  c.TranscriptDiscoveryMaxDirs,
				maxBytes: c.TranscriptDiscoveryMaxBytes,
			}, c.ClaudeTranscriptRoot, c.CodexTranscriptRoot)
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
	LastDuration               time.Duration
}

type agentTokenCounters struct {
	cycles, usage, usageDuplicates, coverage, unavailable, errors atomic.Uint64
	appendErrors, metadataErrors                                  atomic.Uint64
	opencodeInventoryErrors, opencodeInventoryTimeouts            atomic.Uint64
	auxiliaryInventoryErrors, auxiliaryInventoryTimeouts          atomic.Uint64
	duration                                                      atomic.Int64
}

func (c *agentTokenCounters) snapshot() AgentTokenCollectorCounters {
	return AgentTokenCollectorCounters{
		Cycles: c.cycles.Load(), UsageAccepted: c.usage.Load(), UsageDuplicates: c.usageDuplicates.Load(), CoverageAccepted: c.coverage.Load(),
		UnavailableAccepted: c.unavailable.Load(), Errors: c.errors.Load(), AppendErrors: c.appendErrors.Load(), MetadataErrors: c.metadataErrors.Load(),
		OpenCodeInventoryErrors: c.opencodeInventoryErrors.Load(), OpenCodeInventoryTimeouts: c.opencodeInventoryTimeouts.Load(),
		AuxiliaryInventoryErrors: c.auxiliaryInventoryErrors.Load(), AuxiliaryInventoryTimeouts: c.auxiliaryInventoryTimeouts.Load(),
		LastDuration: time.Duration(c.duration.Load()),
	}
}

// AgentTokenCollectionSnapshot is a bounded, runtime-only diagnostic view of
// the token collector. It is deliberately separate from the persisted stats
// payload so existing browser consumers do not acquire new stream semantics.
type AgentTokenCollectionSnapshot struct {
	Cycles                     uint64 `json:"cycles"`
	LastAttemptAt              string `json:"last_attempt_at,omitempty"`
	LastSuccessAt              string `json:"last_success_at,omitempty"`
	UsageAccepted              uint64 `json:"usage_accepted"`
	UsageDuplicates            uint64 `json:"usage_duplicates"`
	OpenCodeInventoryRecords   int    `json:"opencode_inventory_records"`
	OpenCodeInventoryErrors    uint64 `json:"opencode_inventory_errors"`
	OpenCodeInventoryTimeouts  uint64 `json:"opencode_inventory_timeouts"`
	OpenCodeInventoryLastError string `json:"opencode_inventory_last_error,omitempty"`
	BatchAtoms                 int    `json:"batch_atoms"`
	BatchCoverageEpochs        int    `json:"batch_coverage_epochs"`
	BatchUnavailableSpans      int    `json:"batch_unavailable_spans"`
	AppendErrors               uint64 `json:"append_errors"`
	MetadataErrors             uint64 `json:"metadata_errors"`
	LastAppendError            string `json:"last_append_error,omitempty"`
	LastMetadataError          string `json:"last_metadata_error,omitempty"`
	SourceDBPath               string `json:"source_db_path,omitempty"`
	SourceCursorPath           string `json:"source_cursor_path,omitempty"`
	LastReason                 string `json:"last_reason,omitempty"`
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

// AgentTokenCollector owns one bounded collection goroutine. RunOnce is safe
// for deterministic callers and is the transaction boundary used by tests.
type AgentTokenCollector struct {
	writer           BatchWriter
	clock            HostCollectorClock
	config           AgentTokenCollectorConfig
	transcriptStates map[string]*TranscriptScannerState
	transcriptCursor *TranscriptCursorStore
	cursor           *OpenCodeTokenCursor
	counters         agentTokenCounters
	diagnosticMu     sync.RWMutex
	diagnostics      agentTokenDiagnostics
	mu               sync.Mutex
	run              bool
	stop             context.CancelFunc
	done             chan struct{}
	lastCadence      float64
	epochBaseID      string
	epochSequence    uint64
}

func NewAgentTokenCollector(writer BatchWriter, clock HostCollectorClock, config AgentTokenCollectorConfig) *AgentTokenCollector {
	if clock == nil {
		clock = realHostCollectorClock{}
	}
	config = config.withDefaults()
	cursor := NewOpenCodeTokenCursor(config.CursorPath)
	return &AgentTokenCollector{writer: writer, clock: clock, config: config, transcriptStates: make(map[string]*TranscriptScannerState), transcriptCursor: NewTranscriptCursorStore(config.TranscriptCursorPath), cursor: cursor, epochBaseID: config.EpochID}
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
		Cycles:                     counters.Cycles,
		UsageAccepted:              counters.UsageAccepted,
		UsageDuplicates:            counters.UsageDuplicates,
		OpenCodeInventoryRecords:   diagnostics.opencodeInventoryRecords,
		OpenCodeInventoryErrors:    counters.OpenCodeInventoryErrors,
		OpenCodeInventoryTimeouts:  counters.OpenCodeInventoryTimeouts,
		OpenCodeInventoryLastError: diagnostics.opencodeInventoryLastError,
		BatchAtoms:                 diagnostics.batch.BatchAtoms,
		BatchCoverageEpochs:        diagnostics.batch.BatchCoverageEpochs,
		BatchUnavailableSpans:      diagnostics.batch.BatchUnavailableSpans,
		AppendErrors:               counters.AppendErrors,
		MetadataErrors:             counters.MetadataErrors,
		LastAppendError:            diagnostics.lastAppendError,
		LastMetadataError:          diagnostics.lastMetadataError,
		SourceDBPath:               diagnosticPath(c.config.OpenCodeDatabase),
		SourceCursorPath:           diagnosticPath(c.cursor.path),
		LastReason:                 diagnostics.lastReason,
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
		return
	}
	stop, done := c.stop, c.done
	c.mu.Unlock()
	stop()
	<-done
}
func (c *AgentTokenCollector) loop(ctx context.Context) {
	defer func() { c.mu.Lock(); c.run = false; close(c.done); c.mu.Unlock() }()
	first := true
	for {
		if !first {
			if !waitCollectorCadence(ctx, c.clock, AgentTokenActiveCadence, AgentTokenIdleCadence, c.config.ActiveFunc) {
				return
			}
		}
		first = false
		if ctx.Err() != nil {
			return
		}
		c.RunOnce(ctx)
	}
}

func (c *AgentTokenCollector) RunOnce(parent context.Context) {
	started := c.clock.Now()
	c.setAttempt(started)
	defer func() { c.counters.duration.Store(c.clock.Now().Sub(started).Nanoseconds()) }()
	c.counters.cycles.Add(1)
	cadence := AgentTokenActiveCadence
	if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
		cadence = AgentTokenIdleCadence
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
	now := float64(started.UnixNano()) / 1e9
	if c.config.OpenCodeDatabase != "" && (c.config.Inventory != nil || c.config.OpenCodeInventory != nil) {
		c.runOnceSplit(parent, now, cadence.Seconds(), started)
		return
	}
	batch, receipts, err := c.collect(parent, now, cadence.Seconds())
	if err != nil {
		c.recordCollectionError(err)
		return
	}
	c.recordBatch(batch)
	if ok, err := c.appendCollected(parent, batch, receipts); !ok {
		c.recordAppendError(err, hasMetadataFacts(batch))
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
	records, inventoryErrors, timedOut := runBoundedAgentInventory(openCtx, opencodeInventory)
	openCancel()
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
	openBatch, receipt, err := c.collectOpenCode(openCollectCtx, observed, cadence, records)
	if err != nil {
		openCollectCancel()
		c.recordCollectionError(err)
		return
	}
	openAppendCtx, openAppendCancel := context.WithTimeout(parent, c.config.OpenCodeAppendTimeout)
	c.recordBatch(openBatch)
	appendOK, metadataError, appendErr := c.appendOpenCodeCollected(openAppendCtx, openBatch, receipt)
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
		auxiliaryRecords, auxiliaryErrors, auxiliaryTimedOut = runBoundedAgentInventory(auxCtx, c.config.Inventory)
		auxCancel()
		if auxiliaryTimedOut {
			c.counters.auxiliaryInventoryTimeouts.Add(1)
		}
		if len(auxiliaryErrors) > 0 {
			c.counters.auxiliaryInventoryErrors.Add(uint64(len(auxiliaryErrors)))
		}
	}
	transcriptCtx, cancel := context.WithTimeout(parent, c.config.TranscriptDiscoveryTimeout)
	defer cancel()
	batch, receipts, err := c.collectTranscripts(transcriptCtx, observed, cadence, auxiliaryRecords, auxiliaryErrors, AppendBatch{}, nil)
	if err != nil {
		if len(batch.UnavailableSpans) == 0 {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, err.Error()))
		}
	}
	if len(batch.UsageAtoms) == 0 && len(batch.CoverageEpochs) == 0 && len(batch.UnavailableSpans) == 0 {
		c.setSuccess(attemptedAt)
		return
	}
	// The parent may have expired while optional transcript work was running.
	// OpenCode is already durable; do not turn a transcript timeout into a
	// second failed transaction.
	c.recordBatch(batch)
	if ok, err := c.appendCollected(parent, batch, receipts); !ok {
		c.recordAppendError(err, hasMetadataFacts(batch))
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
			// An outage interval may already have been persisted by the prior
			// cadence or by an earlier owner. The writer deliberately rejects
			// overlapping coverage/outage rows, so treat that specific conflict
			// as an idempotent metadata result while preserving the fresh atoms.
			// Other metadata failures remain fatal and roll the source cursor back.
			if !isUnavailableIntervalConflict(err) {
				return false, true, errors.Join(err, rollbackReceipt(receipt))
			}
			if receipt != nil {
				if commitErr := receipt.commit(); commitErr != nil {
					return false, false, commitErr
				}
			}
			return true, false, nil
		}
		c.counters.coverage.Add(uint64(result.CoverageChanged))
		c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
	}
	if receipt != nil {
		if err := receipt.commit(); err != nil {
			return false, false, err
		}
	}
	return true, false, nil
}

func isUnavailableIntervalConflict(err error) bool {
	if err == nil {
		return false
	}
	message := err.Error()
	return strings.Contains(message, "unavailable spans overlap") ||
		strings.Contains(message, "coverage epoch overlaps an unavailable span")
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
		return batch, receipts, nil
	}
	transcriptCtx, cancel := context.WithTimeout(parent, c.config.TranscriptDiscoveryTimeout)
	defer cancel()
	return c.collectTranscripts(transcriptCtx, observed, cadence, records, inventoryErrors, batch, receipts)
}

func (c *AgentTokenCollector) collectTranscripts(parent context.Context, observed, cadence float64, records []AgentRecord, inventoryErrors []error, batch AppendBatch, receipts []agentTokenReceipt) (AppendBatch, []agentTokenReceipt, error) {
	sources, err := c.config.TranscriptSources(parent, records)
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
	if len(sources) > maxAgentTokenSources {
		batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, "agent-token-source-bound-exceeded"))
		sources = sources[:maxAgentTokenSources]
	}
	for _, source := range sources {
		if err := parent.Err(); err != nil {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, "transcript-discovery-timeout"))
			break
		}
		stateKey := transcriptCursorKey(source)
		state := c.transcriptStates[stateKey]
		if state == nil {
			persisted, stateErr := c.transcriptCursor.State(source)
			if stateErr != nil {
				batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, source.SourceID, observed, cadence, "transcript-cursor-"+stateErr.Error()))
				continue
			}
			state = &persisted
			c.transcriptStates[stateKey] = state
		}
		result, scanErr := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: maxAgentTokenRecords})
		if scanErr != nil {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, source.SourceID, observed, cadence, scanErr.Error()))
			continue
		}
		if result.Receipt != nil {
			changed := !reflect.DeepEqual(result.Receipt.before, result.Receipt.after)
			if changed {
				if err := c.transcriptCursor.Prepare(source, result.Receipt.before, result.Receipt.after); err != nil {
					result.Receipt.Rollback()
					batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, source.SourceID, observed, cadence, "transcript-cursor-"+err.Error()))
					continue
				}
			}
			if !changed {
				result.Receipt = nil
			}
		}
		batch.UsageAtoms = append(batch.UsageAtoms, result.Atoms...)
		if result.RecordsParsed > 0 || result.Reset || state.Offset == 0 {
			batch.CoverageEpochs = append(batch.CoverageEpochs, agentTokenCoverage(c.config, source.SourceID, observed, cadence))
		}
		if result.Receipt != nil {
			receipt := result.Receipt
			receipts = append(receipts, agentTokenReceipt{
				commit: func() error {
					if err := c.transcriptCursor.Commit(source, receipt.after); err != nil {
						return err
					}
					receipt.Commit()
					return nil
				},
				rollback: func() error {
					receipt.Rollback()
					return c.transcriptCursor.Rollback(source)
				},
			})
		}
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
		strings.Contains(message, "transcript-discovery-byte-budget-exhausted")
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

func (c *AgentTokenCollector) collectOpenCode(ctx context.Context, observed, cadence float64, records []AgentRecord) (batch AppendBatch, receipt *agentTokenReceipt, err error) {
	defer func() { deduplicateUnavailableSpans(&batch) }()
	identity, err := os.Stat(c.config.OpenCodeDatabase)
	if err != nil {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode", observed, cadence, "database-unavailable")}}, nil, nil
	}
	device, inode := fileIdentity(identity)
	cursorState, err := c.cursor.state()
	if err != nil {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode-cursor", observed, cadence, err.Error())}}, nil, nil
	}
	cursorBefore := cursorState.clone()
	if cursorState.Device != 0 && (cursorState.Device != device || cursorState.Inode != inode) {
		cursorState = openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}, Device: device, Inode: inode}
	}
	cursorState.Device, cursorState.Inode = device, inode
	db, err := OpenOpenCodeReadOnly(ctx, c.config.OpenCodeDatabase)
	if err != nil {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode", observed, cadence, err.Error())}}, nil, nil
	}
	defer db.Close()
	proposed := cursorState.clone()
	batch = AppendBatch{}
	type rootClaim struct {
		key    string
		record AgentRecord
	}
	claims := map[string]rootClaim{}
	for _, record := range records {
		if record.Kind != "opencode" {
			continue
		}
		agentKey := agentKeyForRecord(record, record.OpenCodeSession)
		sessionID := record.OpenCodeSession
		if sessionID == "" {
			processSource := openCodeProcessSource(record)
			if record.Directory == "" {
				c.appendOpenCodeUnavailable(&batch, processSource, observed, cadence, "session-identity-unavailable")
				continue
			}
			resolved, resolveErr := db.ResolveSessionID(ctx, record.Directory, record.Title, record.StartedAt, observed)
			if resolveErr != nil {
				c.appendOpenCodeUnavailable(&batch, processSource, observed, cadence, resolveErr.Error())
				continue
			}
			sessionID = resolved
		}
		rootSessionID, rootErr := db.RootSessionID(ctx, sessionID, MaxOpenCodeSessionTree)
		if rootErr != nil {
			// Deterministic mock clients are terminal-test fixtures. Their pane
			// processes can outlive the fixture database and therefore carry a
			// session id that is intentionally absent from the operator's native
			// OpenCode database. Do not let those stale fixtures manufacture an
			// unavailable interval or mask a valid native root.
			if isStaleOpenCodeMockRecord(record, rootErr) {
				continue
			}
			c.appendOpenCodeUnavailable(&batch, "opencode:"+sessionID, observed, cadence, rootErr.Error())
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
	initialCursor := len(proposed.Parts) == 0
	for _, sessionID := range rootIDs {
		claim := claims[sessionID]
		tree, treeErr := db.ReadCurrentSessionDescendantFrontier(ctx, sessionID, MaxOpenCodeSessionTree)
		if treeErr != nil {
			c.appendOpenCodeUnavailable(&batch, "opencode:"+sessionID, observed, cadence, treeErr.Error())
			continue
		}
		activeAt := tree.NewestUpdated
		if observedMillis := int64(observed * 1000); activeAt > observedMillis {
			activeAt = observedMillis
		}
		activeSince := activeAt - int64(openCodeActiveWindow/time.Millisecond)
		if activeSince < 0 {
			activeSince = 0
		}
		if initialCursor {
			// The first read has no cursor. It still stays bounded by the SQL
			// LIMIT, while allowing old fixture/live sessions to establish an
			// initial anchor from their newest rows.
			activeSince = tree.OldestCreated
		}
		initiallyUnseen := true
		for _, childID := range tree.Sessions {
			cursor := proposed.Parts[childID]
			if cursor.RowID > 0 {
				initiallyUnseen = false
			}
			selections = append(selections, OpenCodeStepFinishSelection{
				SessionID:     childID,
				RootSessionID: sessionID,
				ActiveSince:   activeSince,
				CursorRowID:   cursor.RowID,
			})
		}
		rootWindows = append(rootWindows, rootWindow{sessionID, claim, tree, initiallyUnseen})
	}
	var window OpenCodeStepFinishWindow
	if len(selections) > 0 {
		window, err = db.ReadStepFinishPartsForSelections(ctx, selections, int64(observed*1000)+1, proposed.Parts, MaxOpenCodePartReadLimit)
	}
	if err != nil {
		for _, root := range rootWindows {
			c.appendOpenCodeUnavailable(&batch, "opencode:"+root.id, observed, cadence, err.Error())
		}
	} else {
		for _, part := range window.Parts {
			claim := claims[part.RootSessionID]
			c.addOpenCodePart(&batch, proposed, part, claim.key, part.RootSessionID, observed, cadence)
		}
		proposed.Parts = window.Cursors
	}
	for _, root := range rootWindows {
		if err != nil {
			continue
		}
		partsTruncated := window.TruncatedRoots[root.id]
		partialHistory := root.tree.Complete && root.initiallyUnseen &&
			root.tree.NewestUpdated-root.tree.OldestCreated > int64(openCodeActiveWindow/time.Millisecond)
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
	if !proposed.changed(cursorState) {
		return batch, nil, nil
	}
	if err := c.cursor.prepare(cursorBefore, proposed); err != nil {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode-cursor", observed, cadence, err.Error())}}, nil, nil
	}
	committed := proposed.clone()
	return batch, &agentTokenReceipt{
		commit:   func() error { return c.cursor.commit(committed) },
		rollback: c.cursor.rollback,
	}, nil
}

func isStaleOpenCodeMockRecord(record AgentRecord, err error) bool {
	if err == nil || !strings.Contains(err.Error(), "OpenCode root session not found") {
		return false
	}
	return strings.Contains(record.Command, "--mock") && record.OpenCodeSession != ""
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
		key := part.SessionID + ":" + part.PartID + ":" + dimension
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
		batch.UsageAtoms = append(batch.UsageAtoms, UsageAtom{EventID: "opencode:" + key, Direction: direction, Modality: "text", CacheRole: cache, Unit: "tokens", ObservedAt: float64(part.CompletedAt) / 1000, Payload: map[string]any{"quantity": float64(quantity), "provider": part.Provider, "model": part.Model, "thread_id": part.SessionID, "agent_id": agentID}})
	}
}

func agentKeyForRecord(record AgentRecord, sessionID string) string {
	if record.TmuxSession != "" {
		return record.TmuxSession
	}
	return openCodeProcessSource(record)
}

type openCodeCursorState struct {
	Events map[string]string             `json:"events"`
	Parts  map[string]OpenCodePartCursor `json:"parts,omitempty"`
	Device uint64                        `json:"device,omitempty"`
	Inode  uint64                        `json:"inode,omitempty"`
}

func (s openCodeCursorState) clone() openCodeCursorState {
	out := openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}, Device: s.Device, Inode: s.Inode}
	for k, v := range s.Events {
		out.Events[k] = v
	}
	for k, v := range s.Parts {
		out.Parts[k] = v
	}
	return out
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
		if disk.Pending != nil {
			cleanGeneration := disk.Generation + 1
			clean := openCodeCursorDisk{Version: 1, Generation: cleanGeneration, State: disk.State}
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
	Version      int                  `json:"version"`
	Generation   uint64               `json:"generation,omitempty"`
	State        openCodeCursorState  `json:"state"`
	Pending      *openCodeCursorState `json:"pending,omitempty"`
	PendingToken string               `json:"pending_token,omitempty"`
}

func (c *OpenCodeTokenCursor) readDisk() (openCodeCursorDisk, error) {
	data, err := os.ReadFile(c.path)
	if os.IsNotExist(err) {
		return openCodeCursorDisk{Version: 1, State: openCodeCursorState{Events: map[string]string{}, Parts: map[string]OpenCodePartCursor{}}}, nil
	}
	if err != nil {
		return openCodeCursorDisk{}, err
	}
	var disk openCodeCursorDisk
	if json.Unmarshal(data, &disk) != nil || (disk.Version != 0 && disk.Version != 1) {
		return openCodeCursorDisk{}, errors.New("cursor-state-malformed")
	}
	if disk.State.Events == nil {
		disk.State.Events = map[string]string{}
	}
	if disk.State.Parts == nil {
		disk.State.Parts = map[string]OpenCodePartCursor{}
	}
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
		prepared := next.clone()
		staged := openCodeCursorDisk{Version: 1, Generation: disk.Generation + 1, State: disk.State, Pending: &prepared, PendingToken: token}
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
		staged := openCodeCursorDisk{Version: 1, Generation: disk.Generation + 1, State: committed}
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
	return withSidecarLock(c.path, func() error {
		disk, err := c.readDisk()
		if err != nil {
			return err
		}
		if disk.PendingToken != c.pendingToken {
			return errors.New("cursor-state-stale")
		}
		staged := openCodeCursorDisk{Version: 1, Generation: disk.Generation + 1, State: disk.State}
		if err := atomicSidecarWrite(c.path, staged); err != nil {
			return err
		}
		c.current, c.generation, c.pending, c.pendingToken = disk.State, staged.Generation, nil, ""
		return nil
	})
}
func (c *OpenCodeTokenCursor) write() error {
	disk := openCodeCursorDisk{Version: 1, Generation: c.generation, State: c.current, Pending: c.pending, PendingToken: c.pendingToken}
	return atomicSidecarWrite(c.path, disk)
}

type transcriptDiscoveryBounds struct {
	maxFiles int
	maxDirs  int
	maxBytes int64
}

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
	seen := map[string]bool{}
	out := []TranscriptSource{}
	for _, record := range records {
		if err := ctx.Err(); err != nil {
			return out, err
		}
		if record.Kind != "claude" && record.Kind != "codex" {
			continue
		}
		if record.TranscriptPath != "" {
			addTranscriptSource(&out, seen, record.TranscriptPath, record)
			continue
		}
		root := defaultTranscriptRoot(record.Kind, configuredRoots...)
		if root == "" {
			return out, errors.New("transcript-root-unconfigured")
		}
		candidates, err := recentTranscriptCandidates(ctx, root, bounds)
		for _, candidate := range candidates {
			if len(out) >= maxAgentTokenSources {
				break
			}
			addTranscriptSource(&out, seen, candidate.path, record)
		}
		if err != nil {
			return out, err
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Path < out[j].Path })
	return out, nil
}

func addTranscriptSource(out *[]TranscriptSource, seen map[string]bool, path string, record AgentRecord) {
	path = filepath.Clean(path)
	if filepath.Ext(path) != ".jsonl" || seen[path] || len(*out) >= maxAgentTokenSources {
		return
	}
	seen[path] = true
	*out = append(*out, TranscriptSource{Path: path, Provider: record.Kind, SourceID: record.Kind + ":" + path, ThreadID: record.TmuxSession, AgentID: transcriptAgentID(record)})
}

// recentTranscriptCandidates never reads transcript contents. It visits a
// bounded recent-first directory frontier, so an old Codex tree cannot turn
// token collection into an 11GB recursive walk.
func recentTranscriptCandidates(ctx context.Context, root string, bounds transcriptDiscoveryBounds) ([]transcriptCandidate, error) {
	if bounds.maxFiles <= 0 || bounds.maxDirs <= 0 || bounds.maxBytes <= 0 {
		return nil, errors.New("transcript-discovery-bound-invalid")
	}
	type pendingDir struct {
		path    string
		modTime time.Time
	}
	queue := []pendingDir{{path: filepath.Clean(root)}}
	candidates := make([]transcriptCandidate, 0, bounds.maxFiles)
	inspectedDirs, inspectedFiles := 0, 0
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
	for len(queue) > 0 {
		if err := ctx.Err(); err != nil {
			sortCandidates()
			return candidates, err
		}
		if inspectedDirs >= bounds.maxDirs {
			sortCandidates()
			return candidates, errors.New("transcript-discovery-directory-budget-exhausted")
		}
		dir := queue[0]
		queue = queue[1:]
		inspectedDirs++
		entries, err := os.ReadDir(dir.path)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			sortCandidates()
			return candidates, err
		}
		sort.Slice(entries, func(i, j int) bool { return entries[i].Name() > entries[j].Name() })
		childDirs := make([]pendingDir, 0)
		for _, entry := range entries {
			if err := ctx.Err(); err != nil {
				sortCandidates()
				return candidates, err
			}
			path := filepath.Join(dir.path, entry.Name())
			if entry.IsDir() {
				info, statErr := entry.Info()
				if statErr == nil {
					childDirs = append(childDirs, pendingDir{path: path, modTime: info.ModTime()})
				} else if discoveryErr == nil {
					discoveryErr = statErr
				}
				continue
			}
			if filepath.Ext(path) != ".jsonl" || inspectedFiles >= bounds.maxFiles {
				if inspectedFiles >= bounds.maxFiles {
					sortCandidates()
					return candidates, errors.New("transcript-discovery-file-budget-exhausted")
				}
				continue
			}
			info, statErr := entry.Info()
			if statErr != nil {
				if discoveryErr == nil {
					discoveryErr = statErr
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
		sort.Slice(childDirs, func(i, j int) bool {
			if !childDirs[i].modTime.Equal(childDirs[j].modTime) {
				return childDirs[i].modTime.After(childDirs[j].modTime)
			}
			return childDirs[i].path < childDirs[j].path
		})
		for _, child := range childDirs {
			if inspectedDirs+len(queue) >= bounds.maxDirs {
				break
			}
			queue = append(queue, child)
		}
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
	if record.TmuxSession != "" {
		return record.TmuxSession
	}
	if record.TmuxPaneID != "" {
		return record.TmuxPaneID
	}
	return record.Kind
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
