package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
)

// goHealthLedger owns measurements made by this Go web process. Request counters are
// intentionally process-local; lifecycle counters are supplied by goHealthLifecycle,
// which is the separate durable owner for facts that must survive a restart.
type goHealthLedger struct {
	mu                sync.Mutex
	epoch             string
	epochStartedAt    time.Time
	processStartCount uint64
	observations      uint64
	requestCount      uint64
	completedCount    uint64
	errorCount        uint64
	errorsByReason    map[string]uint64
	latencyTotal      time.Duration
	latencyMax        time.Duration
}

type goHealthLedgerSnapshot struct {
	epoch             string
	epochStartedAt    time.Time
	processStartCount uint64
	observations      uint64
	requestCount      uint64
	completedCount    uint64
	errorCount        uint64
	errorsByReason    map[string]uint64
	latencyTotal      time.Duration
	latencyMax        time.Duration
}

const goHealthHistorySchemaVersion = 1

type goHealthHistoryDocument struct {
	SchemaVersion          int    `json:"schema_version"`
	ProcessStartCount      uint64 `json:"process_start_count"`
	RestartCount           uint64 `json:"restart_count"`
	DemandStartCount       uint64 `json:"demand_start_count"`
	UnexpectedRestartCount uint64 `json:"unexpected_restart_count"`
	LastEpoch              string `json:"last_epoch"`
	LastPID                int    `json:"last_pid"`
	LastStartedAt          int64  `json:"last_started_at"`
	LastCleanShutdown      bool   `json:"last_clean_shutdown"`
}

type goHealthLifecycleSnapshot struct {
	RestartCount           uint64
	DemandStartCount       uint64
	UnexpectedRestartCount uint64
	Available              bool
	RestartAvailable       bool
	DemandAvailable        bool
	UnexpectedAvailable    bool
	HistoryCoverage        string
	HistoryResetReason     string
	PersistenceState       string
	PersistenceReasonCode  string
}

// goHealthLifecycle is the durable owner for restart classification. The file is
// port-scoped and written atomically; the instance lease guarantees one live Go
// owner for a port, while the mutex keeps test and shutdown calls serialized.
type goHealthLifecycle struct {
	mu       sync.Mutex
	path     string
	current  goHealthHistoryDocument
	snapshot goHealthLifecycleSnapshot
}

func newGoHealthLifecycle(path, epoch string, pid int, startedAt time.Time, startReason string) (*goHealthLifecycle, error) {
	if strings.TrimSpace(path) == "" || strings.TrimSpace(epoch) == "" || pid <= 0 || startedAt.IsZero() {
		return nil, fmt.Errorf("invalid Go health lifecycle identity")
	}
	if startReason = strings.TrimSpace(startReason); startReason == "" {
		startReason = "unknown"
	}
	lifecycle := &goHealthLifecycle{path: path}
	prior, priorOK, resetReason := readGoHealthHistory(path)
	current := goHealthHistoryDocument{
		SchemaVersion:     goHealthHistorySchemaVersion,
		ProcessStartCount: 1,
		LastEpoch:         epoch,
		LastPID:           pid,
		LastStartedAt:     startedAt.Unix(),
	}
	snapshot := goHealthLifecycleSnapshot{
		Available:             true,
		RestartAvailable:      true,
		DemandAvailable:       true,
		UnexpectedAvailable:   true,
		HistoryCoverage:       "process",
		HistoryResetReason:    resetReason,
		PersistenceState:      "ok",
		PersistenceReasonCode: "",
	}
	if priorOK {
		current = prior
		current.SchemaVersion = goHealthHistorySchemaVersion
		current.ProcessStartCount++
		current.RestartCount++
		if current.LastCleanShutdown && startReason == "demand" {
			current.DemandStartCount++
		} else if !current.LastCleanShutdown {
			current.UnexpectedRestartCount++
		} else if startReason != "demand" {
			// The restart itself is known, but this process did not identify its
			// launch cause. Keep the two classifications unavailable rather than
			// assigning a false demand or unexpected bucket.
			snapshot.DemandAvailable = false
			snapshot.UnexpectedAvailable = false
			snapshot.HistoryCoverage = "partial"
			snapshot.HistoryResetReason = "start_reason_unknown"
		}
		snapshot.HistoryCoverage = "full"
		if snapshot.HistoryResetReason == "start_reason_unknown" {
			snapshot.HistoryCoverage = "partial"
		}
	} else if resetReason != "" {
		// A missing file establishes a measured baseline. A malformed file is
		// reset for future continuity, but its pre-reset history is unknowable.
		if resetReason != "no_prior_history" {
			snapshot.HistoryCoverage = "reset"
			snapshot.DemandAvailable = false
			snapshot.UnexpectedAvailable = false
			snapshot.RestartAvailable = false
			snapshot.Available = false
		}
	}
	current.LastEpoch = epoch
	current.LastPID = pid
	current.LastStartedAt = startedAt.Unix()
	current.LastCleanShutdown = false
	lifecycle.current = current
	lifecycle.snapshot = snapshot
	if err := lifecycle.persistLocked(); err != nil {
		lifecycle.snapshot.PersistenceState = "degraded"
		lifecycle.snapshot.PersistenceReasonCode = "history_write_failed"
		lifecycle.snapshot.Available = false
		lifecycle.snapshot.RestartAvailable = false
		lifecycle.snapshot.DemandAvailable = false
		lifecycle.snapshot.UnexpectedAvailable = false
		return lifecycle, nil
	}
	lifecycle.snapshot.RestartCount = current.RestartCount
	lifecycle.snapshot.DemandStartCount = current.DemandStartCount
	lifecycle.snapshot.UnexpectedRestartCount = current.UnexpectedRestartCount
	return lifecycle, nil
}

func readGoHealthHistory(path string) (goHealthHistoryDocument, bool, string) {
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return goHealthHistoryDocument{}, false, "no_prior_history"
	}
	if err != nil {
		return goHealthHistoryDocument{}, false, "history_read_failed"
	}
	var document goHealthHistoryDocument
	if json.Unmarshal(data, &document) != nil || document.SchemaVersion != goHealthHistorySchemaVersion || document.ProcessStartCount == 0 || document.LastEpoch == "" || document.LastPID <= 0 || document.LastStartedAt <= 0 || document.DemandStartCount+document.UnexpectedRestartCount > document.RestartCount {
		return goHealthHistoryDocument{}, false, "history_corrupt"
	}
	return document, true, ""
}

func (l *goHealthLifecycle) persistLocked() error {
	if err := os.MkdirAll(filepath.Dir(l.path), 0o700); err != nil {
		return err
	}
	data, err := json.Marshal(l.current)
	if err != nil {
		return err
	}
	return atomicfile.Write(l.path, data)
}

func (l *goHealthLifecycle) markCleanShutdown() {
	if l == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	l.current.LastCleanShutdown = true
	if err := l.persistLocked(); err != nil {
		l.snapshot.PersistenceState = "degraded"
		l.snapshot.PersistenceReasonCode = "shutdown_history_write_failed"
	}
}

func (l *goHealthLifecycle) snapshotNow() goHealthLifecycleSnapshot {
	if l == nil {
		return goHealthLifecycleSnapshot{}
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	snapshot := l.snapshot
	snapshot.RestartCount = l.current.RestartCount
	snapshot.DemandStartCount = l.current.DemandStartCount
	snapshot.UnexpectedRestartCount = l.current.UnexpectedRestartCount
	return snapshot
}

func newGoHealthLedger(startedAt time.Time) *goHealthLedger {
	if startedAt.IsZero() {
		startedAt = time.Now()
	}
	return &goHealthLedger{
		epoch:             fmt.Sprintf("pid:%d:start:%d", os.Getpid(), startedAt.UnixNano()),
		epochStartedAt:    startedAt,
		processStartCount: 1,
		errorsByReason:    make(map[string]uint64),
	}
}

func (l *goHealthLedger) begin(excluded bool) func(status int, completed bool, elapsed time.Duration) {
	if excluded {
		return func(int, bool, time.Duration) {}
	}
	l.mu.Lock()
	l.requestCount++
	l.mu.Unlock()
	return func(status int, completed bool, elapsed time.Duration) {
		l.finish(status, completed, elapsed)
	}
}

func (l *goHealthLedger) finish(status int, completed bool, elapsed time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if status >= 400 || status == 0 {
		l.errorCount++
		reason := "http_4xx"
		if status >= 500 || status == 0 {
			reason = "http_5xx"
		}
		l.errorsByReason[reason]++
	}
	if !completed || status >= 400 || status == 0 {
		return
	}
	l.completedCount++
	if elapsed < 0 {
		elapsed = 0
	}
	l.latencyTotal += elapsed
	if elapsed > l.latencyMax {
		l.latencyMax = elapsed
	}
}

func (l *goHealthLedger) observe() {
	l.mu.Lock()
	l.observations++
	l.mu.Unlock()
}

func (l *goHealthLedger) snapshot() goHealthLedgerSnapshot {
	l.mu.Lock()
	defer l.mu.Unlock()
	return goHealthLedgerSnapshot{
		epoch:             l.epoch,
		epochStartedAt:    l.epochStartedAt,
		processStartCount: l.processStartCount,
		observations:      l.observations,
		requestCount:      l.requestCount,
		completedCount:    l.completedCount,
		errorCount:        l.errorCount,
		errorsByReason:    cloneGoHealthCounts(l.errorsByReason),
		latencyTotal:      l.latencyTotal,
		latencyMax:        l.latencyMax,
	}
}

func cloneGoHealthCounts(values map[string]uint64) map[string]uint64 {
	clone := make(map[string]uint64, len(values))
	for key, value := range values {
		clone[key] = value
	}
	return clone
}

func goHealthErrorPayload(values map[string]uint64) map[string]any {
	payload := make(map[string]any, len(values))
	for key, value := range values {
		payload[key] = value
	}
	return payload
}

func maxGoHealthFloat(value, minimum float64) float64 {
	if value < minimum {
		return minimum
	}
	return value
}

func (s *server) ensureGoHealthLedger(now time.Time) *goHealthLedger {
	s.healthLedgerMu.Lock()
	defer s.healthLedgerMu.Unlock()
	if s.healthLedger == nil {
		startedAt := s.startedAt
		if startedAt.IsZero() {
			startedAt = now
		}
		s.healthLedger = newGoHealthLedger(startedAt)
	}
	return s.healthLedger
}

func (s *server) goHealthSnapshot(now time.Time) goHealthLedgerSnapshot {
	ledger := s.ensureGoHealthLedger(now)
	return ledger.snapshot()
}

func (s *server) goHealthLifecycleSnapshot() goHealthLifecycleSnapshot {
	return s.healthLifecycle.snapshotNow()
}

func (s *server) markGoHealthCleanShutdown() {
	if s != nil {
		s.healthLifecycle.markCleanShutdown()
	}
}

func lifecycleCoverage(snapshot goHealthLifecycleSnapshot) string {
	if snapshot.Available {
		if snapshot.HistoryCoverage == "" {
			return "full"
		}
		return snapshot.HistoryCoverage
	}
	return "unavailable"
}

func lifecycleCoverageReasons(snapshot goHealthLifecycleSnapshot) []string {
	if snapshot.Available {
		if snapshot.HistoryResetReason == "" {
			return []string{}
		}
		return []string{snapshot.HistoryResetReason}
	}
	if snapshot.PersistenceReasonCode != "" {
		return []string{snapshot.PersistenceReasonCode}
	}
	if snapshot.HistoryResetReason != "" {
		return []string{snapshot.HistoryResetReason}
	}
	return []string{"go_health_history_unavailable"}
}

func goHealthRequestExcluded(path string) bool {
	switch path {
	case "/healthz", "/livez", "/readyz":
		return true
	default:
		return false
	}
}
