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
	"sort"
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
	if c.SourceID == "" {
		c.SourceID = "agent-tokens"
	}
	if c.CursorPath == "" {
		c.CursorPath = filepath.Join(os.TempDir(), "yolomux-opencode-token-cursors.json")
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
	cursor           *OpenCodeTokenCursor
	counters         agentTokenCounters
	diagnosticMu     sync.RWMutex
	diagnostics      agentTokenDiagnostics
	mu               sync.Mutex
	run              bool
	stop             context.CancelFunc
	done             chan struct{}
}

func NewAgentTokenCollector(writer BatchWriter, clock HostCollectorClock, config AgentTokenCollectorConfig) *AgentTokenCollector {
	if clock == nil {
		clock = realHostCollectorClock{}
	}
	config = config.withDefaults()
	cursor := NewOpenCodeTokenCursor(config.CursorPath)
	return &AgentTokenCollector{writer: writer, clock: clock, config: config, transcriptStates: make(map[string]*TranscriptScannerState), cursor: cursor}
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
	if len(batch.UnavailableSpans) > 0 {
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
			delay := AgentTokenActiveCadence
			if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
				delay = AgentTokenIdleCadence
			}
			timer := c.clock.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C():
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
	if !c.appendCollected(parent, batch, receipts) {
		c.recordAppendError(errors.New("append failed"), hasMetadataFacts(batch))
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
		if !c.appendCollected(parent, batch, nil) {
			c.recordAppendError(errors.New("append failed"), true)
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
	appendOK, metadataError := c.appendOpenCodeCollected(openAppendCtx, openBatch, receipt)
	openAppendCancel()
	openCollectCancel()
	if !appendOK {
		c.recordAppendError(errors.New("OpenCode append failed"), metadataError)
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
	if !c.appendCollected(parent, batch, receipts) {
		c.recordAppendError(errors.New("append failed"), hasMetadataFacts(batch))
	} else {
		c.setSuccess(attemptedAt)
	}
}

type agentInventoryResult struct {
	records []AgentRecord
	errors  []error
}

// runBoundedAgentInventory isolates an inventory implementation that may call
// an external command or a test double that ignores cancellation. The result
// channel is buffered so a late worker can finish without blocking the token
// collector after its deadline has already expired.
func runBoundedAgentInventory(ctx context.Context, inventory AgentTokenInventory) ([]AgentRecord, []error, bool) {
	if inventory == nil {
		return nil, []error{errors.New("inventory-unavailable")}, false
	}
	result := make(chan agentInventoryResult, 1)
	go func() {
		records, errorsOut := inventory(ctx)
		result <- agentInventoryResult{records: records, errors: errorsOut}
	}()
	select {
	case output := <-result:
		return output.records, output.errors, false
	case <-ctx.Done():
		return nil, []error{ctx.Err()}, true
	}
}

func (c *AgentTokenCollector) appendCollected(ctx context.Context, batch AppendBatch, receipts []agentTokenReceipt) bool {
	if c.writer == nil {
		for _, receipt := range receipts {
			receipt.rollback()
		}
		return false
	}
	result, err := c.writer.AppendBatch(ctx, batch)
	if err != nil {
		for _, receipt := range receipts {
			receipt.rollback()
		}
		return false
	}
	for _, receipt := range receipts {
		receipt.commit()
	}
	c.counters.usage.Add(uint64(result.UsageAccepted))
	c.counters.usageDuplicates.Add(uint64(result.UsageDuplicate))
	c.counters.coverage.Add(uint64(result.CoverageChanged))
	c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
	return true
}

// appendOpenCodeCollected keeps valid usage facts independent from partial
// history metadata. A bounded OpenCode tree can produce useful fresh atoms
// while its coverage/unavailable intervals are incomplete or conflicting.
// Metadata remains strict, but it must not roll back those atoms.
func (c *AgentTokenCollector) appendOpenCodeCollected(ctx context.Context, batch AppendBatch, receipt *agentTokenReceipt) (bool, bool) {
	if c.writer == nil {
		if receipt != nil {
			receipt.rollback()
		}
		return false, false
	}
	if len(batch.UsageAtoms) > 0 {
		result, err := c.writer.AppendBatch(ctx, AppendBatch{UsageAtoms: batch.UsageAtoms})
		if err != nil {
			if receipt != nil {
				receipt.rollback()
			}
			return false, false
		}
		c.counters.usage.Add(uint64(result.UsageAccepted))
		c.counters.usageDuplicates.Add(uint64(result.UsageDuplicate))
	}
	metadata := AppendBatch{CoverageEpochs: batch.CoverageEpochs, UnavailableSpans: batch.UnavailableSpans}
	if len(metadata.CoverageEpochs) > 0 || len(metadata.UnavailableSpans) > 0 {
		result, err := c.writer.AppendBatch(ctx, metadata)
		if err != nil {
			if receipt != nil {
				receipt.rollback()
			}
			return false, true
		}
		c.counters.coverage.Add(uint64(result.CoverageChanged))
		c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
	}
	if receipt != nil {
		receipt.commit()
	}
	return true, false
}

type agentTokenReceipt struct {
	commit   func()
	rollback func()
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
		reason := err.Error()
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
			reason = "transcript-discovery-timeout"
		}
		batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "transcripts", observed, cadence, reason))
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
		state := c.transcriptStates[source.Path]
		if state == nil {
			state = &TranscriptScannerState{}
			c.transcriptStates[source.Path] = state
		}
		result, scanErr := ScanTranscript(source, state, TranscriptScanOptions{MaxRecords: maxAgentTokenRecords})
		if scanErr != nil {
			batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, source.SourceID, observed, cadence, scanErr.Error()))
			continue
		}
		batch.UsageAtoms = append(batch.UsageAtoms, result.Atoms...)
		if result.RecordsParsed > 0 || result.Reset || state.Offset == 0 {
			batch.CoverageEpochs = append(batch.CoverageEpochs, agentTokenCoverage(c.config, source.SourceID, observed, cadence))
		}
		if result.Receipt != nil {
			receipt := result.Receipt
			receipts = append(receipts, agentTokenReceipt{receipt.Commit, receipt.Rollback})
		}
	}
	for _, inventoryErr := range inventoryErrors {
		batch.UnavailableSpans = append(batch.UnavailableSpans, agentTokenUnavailable(c.config, "inventory", observed, cadence, inventoryErr.Error()))
	}
	return batch, receipts, nil
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
// AppendBatch validates the interval set. Choose the lexicographically smallest
// reason so the retained fact does not depend on source traversal order.
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
	batch.UnavailableSpans = make([]UnavailableSpan, 0, len(keys))
	for _, key := range keys {
		batch.UnavailableSpans = append(batch.UnavailableSpans, byKey[key])
	}
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
		key string
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
			c.appendOpenCodeUnavailable(&batch, "opencode:"+sessionID, observed, cadence, rootErr.Error())
			continue
		}
		sessionID = rootSessionID
		if _, ok := claims[sessionID]; ok {
			// Python's inventory exposes one row per resolved root session. Keep
			// the first deterministic claim so duplicate process observations do
			// not double-count the same root/child tree.
			continue
		}
		claims[sessionID] = rootClaim{key: agentKey}
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
	if err := c.cursor.prepare(proposed); err != nil {
		return AppendBatch{UnavailableSpans: []UnavailableSpan{agentTokenUnavailable(c.config, "opencode-cursor", observed, cadence, err.Error())}}, nil, nil
	}
	return batch, &agentTokenReceipt{func() { _ = c.cursor.commit() }, func() { _ = c.cursor.rollback() }}, nil
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
	return s.Device != other.Device || s.Inode != other.Inode || len(s.Events) != len(other.Events) || len(s.Parts) != len(other.Parts) || func() bool {
		for k, v := range s.Events {
			if other.Events[k] != v {
				return true
			}
		}
		for k, v := range s.Parts {
			if other.Parts[k] != v {
				return true
			}
		}
		return false
	}()
}

// OpenCodeTokenCursor is a small fail-closed sidecar transaction. A pending
// journal is never silently replayed after a crash.
type OpenCodeTokenCursor struct {
	path    string
	mu      sync.Mutex
	current openCodeCursorState
	pending *openCodeCursorState
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
	if err := c.load(); err != nil {
		return openCodeCursorState{}, err
	}
	return c.current.clone(), nil
}
func (c *OpenCodeTokenCursor) load() error {
	data, err := os.ReadFile(c.path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	var disk struct {
		State   openCodeCursorState  `json:"state"`
		Pending *openCodeCursorState `json:"pending"`
	}
	if json.Unmarshal(data, &disk) != nil {
		return errors.New("cursor-state-malformed")
	}
	if disk.Pending != nil {
		return errors.New("cursor-state-pending")
	}
	if disk.State.Events == nil {
		disk.State.Events = map[string]string{}
	}
	if disk.State.Parts == nil {
		disk.State.Parts = map[string]OpenCodePartCursor{}
	}
	c.current = disk.State
	return nil
}
func (c *OpenCodeTokenCursor) prepare(next openCodeCursorState) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.pending != nil {
		return errors.New("cursor-state-pending")
	}
	if err := c.load(); err != nil {
		return err
	}
	c.pending = &next
	return c.write()
}
func (c *OpenCodeTokenCursor) commit() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.pending == nil {
		return errors.New("cursor-state-pending-missing")
	}
	c.current = c.pending.clone()
	c.pending = nil
	return c.write()
}
func (c *OpenCodeTokenCursor) rollback() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.pending = nil
	return c.write()
}
func (c *OpenCodeTokenCursor) write() error {
	if err := os.MkdirAll(filepath.Dir(c.path), 0700); err != nil {
		return err
	}
	pending := c.pending
	disk := struct {
		State   openCodeCursorState  `json:"state"`
		Pending *openCodeCursorState `json:"pending,omitempty"`
	}{c.current, pending}
	data, _ := json.Marshal(disk)
	temp := c.path + ".tmp"
	if err := os.WriteFile(temp, data, 0600); err != nil {
		return err
	}
	return os.Rename(temp, c.path)
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
	type pendingDir struct{ path string }
	queue := []pendingDir{{filepath.Clean(root)}}
	candidates := make([]transcriptCandidate, 0, bounds.maxFiles)
	inspectedDirs, inspectedFiles := 0, 0
	var inspectedBytes int64
	for len(queue) > 0 {
		if err := ctx.Err(); err != nil {
			return candidates, err
		}
		if inspectedDirs >= bounds.maxDirs {
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
			return candidates, err
		}
		sort.Slice(entries, func(i, j int) bool { return entries[i].Name() > entries[j].Name() })
		for _, entry := range entries {
			if err := ctx.Err(); err != nil {
				return candidates, err
			}
			path := filepath.Join(dir.path, entry.Name())
			if entry.IsDir() {
				if inspectedDirs+len(queue) >= bounds.maxDirs {
					continue
				}
				queue = append(queue, pendingDir{path})
				continue
			}
			if filepath.Ext(path) != ".jsonl" || inspectedFiles >= bounds.maxFiles {
				if inspectedFiles >= bounds.maxFiles {
					return candidates, errors.New("transcript-discovery-file-budget-exhausted")
				}
				continue
			}
			info, statErr := entry.Info()
			if statErr != nil {
				continue
			}
			if info.Size() > bounds.maxBytes-inspectedBytes {
				return candidates, errors.New("transcript-discovery-byte-budget-exhausted")
			}
			inspectedFiles++
			inspectedBytes += info.Size()
			candidates = append(candidates, transcriptCandidate{path: path, modTime: info.ModTime()})
			if inspectedBytes >= bounds.maxBytes {
				return candidates, errors.New("transcript-discovery-byte-budget-exhausted")
			}
		}
	}
	sort.Slice(candidates, func(i, j int) bool {
		if !candidates[i].modTime.Equal(candidates[j].modTime) {
			return candidates[i].modTime.After(candidates[j].modTime)
		}
		return candidates[i].path < candidates[j].path
	})
	return candidates, nil
}

func defaultTranscriptRoot(kind string, configuredRoots ...string) string {
	if kind == "claude" && len(configuredRoots) > 0 && strings.TrimSpace(configuredRoots[0]) != "" {
		return filepath.Clean(configuredRoots[0])
	}
	if kind == "codex" && len(configuredRoots) > 1 && strings.TrimSpace(configuredRoots[1]) != "" {
		return filepath.Clean(configuredRoots[1])
	}
	home := os.Getenv("HOME")
	if kind == "claude" {
		return filepath.Join(home, ".claude", "projects")
	}
	codexHome := os.Getenv("CODEX_HOME")
	if codexHome == "" {
		codexHome = filepath.Join(home, ".codex")
	}
	return filepath.Join(codexHome, "sessions")
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
