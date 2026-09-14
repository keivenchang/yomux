package stats

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// AgentStatus is the normalized family value consumed by the current stats
// materializer. unavailable is deliberately not one of the persisted states.
type AgentStatus string

const (
	AgentStatusAsk         AgentStatus = "ask"
	AgentStatusRun         AgentStatus = "run"
	AgentStatusTransition  AgentStatus = "transition"
	AgentStatusIdle        AgentStatus = "idle"
	AgentStatusUnavailable AgentStatus = "unavailable"
	AgentStatusError       AgentStatus = "error"
)

const (
	MaxAgentPaneCaptureBytes     = 128 * 1024
	MaxAgentStatusEvidence       = 8
	MaxAgentStatusEntries        = 256
	MaxAgentStatusRosterBytes    = 64 * 1024
	DefaultAgentTransitionWindow = 60 * time.Second
)

type AgentStatusRosterEntry struct {
	ID      string `json:"id"`
	Kind    string `json:"kind"`
	State   string `json:"state"`
	Session string `json:"session"`
	Pane    string `json:"pane"`
}

type AgentStatusCounts struct {
	Total   int `json:"total"`
	Running int `json:"running"`
	Red     int `json:"red"`
	Yellow  int `json:"yellow"`
}

type AgentStatusPane struct {
	Target         string
	VisibleText    string
	PaneText       string
	CurrentCommand string
	Error          string
	// Status is the structured screen classification produced by the pane
	// reader. VisibleText remains for diagnostics and legacy readers only.
	Status           string
	Acknowledged     bool
	WorkingStoppedAt time.Time
}

type AgentStatusProcess struct {
	PID        int
	Command    string
	Executable string
	Alive      bool
}

type AgentStatusRequest struct {
	Kind             string
	PID              int
	Command          string
	Executable       string
	PaneTarget       string
	Directory        string
	OpenCodeSession  string
	DatabasePath     string
	Acknowledged     bool
	WorkingStoppedAt time.Time
	TransitionWindow time.Duration
	Now              time.Time
}

type AgentStatusResult struct {
	Status           AgentStatus
	Evidence         []string
	Reason           string
	RawStatus        string
	Acknowledged     bool
	WorkingStoppedAt time.Time
}

// AgentStatusSources are the only live-system seams used by ClassifyAgentStatus.
// A nil seam means that the corresponding evidence is unavailable, not true.
type AgentStatusSources struct {
	CapturePane   func(context.Context, string) (AgentStatusPane, error)
	Process       func(context.Context, int) (AgentStatusProcess, error)
	OpenCodeState func(context.Context, AgentStatusRequest) (string, []string, error)
}

// ClassifyAgentStatus classifies one inventory record. Claude and Codex use
// process identity plus bounded visible-pane evidence. OpenCode is classified
// from its persisted state when a database seam is supplied. Ambiguous live
// evidence never gets guessed into a positive state.
func ClassifyAgentStatus(ctx context.Context, request AgentStatusRequest, sources AgentStatusSources) AgentStatusResult {
	kind := strings.ToLower(strings.TrimSpace(request.Kind))
	if kind == "opencode" {
		if sources.OpenCodeState == nil {
			return unavailable("opencode-state-reader-missing")
		}
		state, evidence, err := sources.OpenCodeState(ctx, request)
		if err != nil {
			return unavailable(err.Error())
		}
		switch state {
		case "working":
			return result(AgentStatusRun, evidence)
		case "paused":
			return result(AgentStatusAsk, evidence)
		case "idle":
			return result(AgentStatusIdle, evidence)
		default:
			return unavailable("opencode-state-unknown")
		}
	}
	if kind != "claude" && kind != "codex" {
		return unavailable("unsupported-agent")
	}
	if sources.CapturePane == nil || strings.TrimSpace(request.PaneTarget) == "" {
		return unavailable("pane-capture-unavailable")
	}
	if request.PID > 0 && sources.Process != nil {
		process, err := sources.Process(ctx, request.PID)
		if err != nil || !process.Alive {
			return unavailable("agent-process-unavailable")
		}
		if process.Command != "" && ClassifyAgent(process.Command, request.Executable) != kind {
			return unavailable("agent-process-identity-mismatch")
		}
		if strings.TrimSpace(process.Command) != "" && request.Command == "" {
			request.Command = process.Command
		}
	}
	pane, err := sources.CapturePane(ctx, request.PaneTarget)
	if err != nil || pane.Error != "" {
		if err != nil {
			return unavailable("pane-capture-failed")
		}
		return unavailable("pane-capture-failed")
	}
	text := pane.VisibleText
	if len([]byte(text)) > MaxAgentPaneCaptureBytes {
		return unavailable("pane-capture-bound-exceeded")
	}
	if strings.TrimSpace(text) == "" {
		return unavailable("empty-pane-capture")
	}
	return classifyPaneStatus(pane, request)
}

func classifyPaneStatus(pane AgentStatusPane, request AgentStatusRequest) AgentStatusResult {
	raw := strings.ToLower(strings.TrimSpace(pane.Status))
	acknowledged := pane.Acknowledged || request.Acknowledged
	stoppedAt := pane.WorkingStoppedAt
	if stoppedAt.IsZero() {
		stoppedAt = request.WorkingStoppedAt
	}
	result := AgentStatusResult{RawStatus: raw, Acknowledged: acknowledged, WorkingStoppedAt: stoppedAt}
	switch raw {
	case "approval", "needs-approval", "needs-input", "attention", "interrupted", "blocked":
		if !acknowledged {
			result.Status = AgentStatusAsk
			result.Evidence = []string{"structured-ask-status"}
			return result
		}
	case "working":
		result.Status = AgentStatusRun
		result.Evidence = []string{"structured-working-status"}
		return result
	case "cooldown", "transition":
		if !acknowledged {
			result.Status = AgentStatusTransition
			result.Evidence = []string{"structured-transition-status"}
			return result
		}
	case "error", "failed", "failure":
		result.Status = AgentStatusError
		result.Evidence = []string{"structured-error-status"}
		return result
	}
	now := request.Now
	if now.IsZero() {
		now = time.Now()
	}
	window := request.TransitionWindow
	if window <= 0 {
		window = DefaultAgentTransitionWindow
	}
	if !acknowledged && !stoppedAt.IsZero() && !now.Before(stoppedAt) && now.Sub(stoppedAt) < window {
		result.Status = AgentStatusTransition
		result.Evidence = []string{"recent-working-stopped"}
		return result
	}
	if raw == "" {
		legacy := classifyVisibleAgentText(pane.VisibleText)
		legacy.RawStatus = raw
		legacy.Acknowledged = acknowledged
		legacy.WorkingStoppedAt = stoppedAt
		return legacy
	}
	result.Status = AgentStatusIdle
	result.Evidence = []string{"settled-status"}
	return result
}

func classifyVisibleAgentText(text string) AgentStatusResult {
	lower := strings.ToLower(text)
	ask := containsAny(lower, "enter to select", "esc to cancel", "yes, and don't ask", "yes/no", "what should claude", "what should codex", "approve", "allow this")
	run := containsAny(lower, "esc to interrupt", "thinking", "running ", "pursuing goal", "goal active", "still running", "tool use", "working")
	transition := containsAny(lower, "goal achieved", "all tasks complete", "all tasks completed", "completed", "finished")
	// Generic words such as "errors" can be part of an idle suggestion. Keep
	// error evidence to explicit failure-shaped markers, matching the prompt
	// detector's actionable error screens rather than classifying prose.
	errorState := containsAny(lower, "error:", "fatal", "traceback", "command failed", "exited with code")
	idle := containsAny(lower, "ask anything", "type your message", "use /", "new task", "› ", "❯ ", "claude code")
	if ask && (run || transition || errorState) {
		return unavailable("conflicting-pane-evidence")
	}
	if run && (transition || errorState) {
		return unavailable("conflicting-pane-evidence")
	}
	if transition && errorState {
		return unavailable("conflicting-pane-evidence")
	}
	if ask {
		return result(AgentStatusAsk, []string{"prompt-or-approval"})
	}
	if run {
		return result(AgentStatusRun, []string{"working-screen"})
	}
	if transition {
		return result(AgentStatusTransition, []string{"completion-screen"})
	}
	if errorState {
		return result(AgentStatusError, []string{"error-screen"})
	}
	if idle {
		return result(AgentStatusIdle, []string{"idle-prompt"})
	}
	return unavailable("ambiguous-pane-evidence")
}

func containsAny(text string, needles ...string) bool {
	for _, needle := range needles {
		if strings.Contains(text, needle) {
			return true
		}
	}
	return false
}

func result(status AgentStatus, evidence []string) AgentStatusResult {
	if len(evidence) > MaxAgentStatusEvidence {
		evidence = evidence[:MaxAgentStatusEvidence]
	}
	return AgentStatusResult{Status: status, Evidence: append([]string(nil), evidence...)}
}

func unavailable(reason string) AgentStatusResult {
	return AgentStatusResult{Status: AgentStatusUnavailable, Reason: reason}
}

// OpenCodeSQLiteStateReader is the default read-only seam for OpenCode's
// 1.18-style database. working maps to run and queued input (paused) maps to
// ask. Without the state table, the result is unavailable rather than guessed.
func OpenCodeSQLiteStateReader(database string) func(context.Context, AgentStatusRequest) (string, []string, error) {
	return func(ctx context.Context, request AgentStatusRequest) (string, []string, error) {
		path := database
		if path == "" {
			path = request.DatabasePath
		}
		if path == "" || len([]byte(path)) > MaxIdentityBytes {
			return "", nil, errors.New("opencode-database-unavailable")
		}
		db, err := OpenOpenCodeReadOnly(ctx, path)
		if err != nil {
			return "", nil, err
		}
		defer db.Close()
		if request.OpenCodeSession == "" {
			if strings.TrimSpace(request.Directory) == "" {
				return "", nil, errors.New("opencode-session-unavailable")
			}
			rows, err := db.reader.Rows(ctx, `SELECT id FROM session WHERE directory = ? ORDER BY time_updated DESC, id DESC LIMIT 1`, request.Directory)
			if err != nil || len(rows) != 1 || len(rows[0]) != 1 {
				return "", nil, errors.New("opencode-session-unavailable")
			}
			request.OpenCodeSession, _ = openCodeString(rows[0][0])
			if request.OpenCodeSession == "" {
				return "", nil, errors.New("opencode-session-unavailable")
			}
		}
		rows, err := db.reader.Rows(ctx, `SELECT delivery, admitted_seq, promoted_seq FROM session_input WHERE session_id = ? ORDER BY time_created DESC LIMIT 2`, request.OpenCodeSession)
		if err != nil {
			return "", nil, errors.New("opencode-state-unavailable")
		}
		for _, row := range rows {
			if len(row) != 3 {
				return "", nil, errors.New("opencode-state-malformed")
			}
			delivery, ok := openCodeString(row[0])
			if !ok || (delivery != "queue" && delivery != "steer") {
				return "", nil, errors.New("opencode-state-malformed")
			}
			admitted, ok := agentInteger(row[1])
			if !ok || admitted < 0 {
				return "", nil, errors.New("opencode-state-malformed")
			}
			if row[2] == nil && delivery == "queue" && admitted > 0 {
				return "paused", []string{"queued-input"}, nil
			}
		}
		rows, err = db.reader.Rows(ctx, `SELECT data FROM part WHERE session_id = ? AND json_valid(data) = 1 AND json_extract(data, '$.type') = 'tool' AND json_extract(data, '$.state.status') IN ('pending','running') LIMIT 2`, request.OpenCodeSession)
		if err != nil {
			return "", nil, errors.New("opencode-state-unavailable")
		}
		if len(rows) > 1 {
			return "", nil, errors.New("opencode-state-bound-exceeded")
		}
		if len(rows) == 1 {
			return "working", []string{"running-tool"}, nil
		}
		return "idle", nil, nil
	}
}

func agentInteger(value any) (int64, bool) {
	switch n := value.(type) {
	case int64:
		return n, true
	case int:
		return int64(n), true
	case float64:
		return int64(n), n >= 0 && float64(int64(n)) == n
	}
	return 0, false
}

// AgentStatusInventory is the runtime seam for one coherent process/tmux
// snapshot. BuildAgentInventory is the normal implementation.
type AgentStatusInventory func(context.Context) ([]AgentRecord, []error)

// AgentStatusClassifier is injectable so the runtime can be tested without
// pane capture or OpenCode databases.
type AgentStatusClassifier func(context.Context, AgentStatusRequest) AgentStatusResult

type AgentStatusCollectorConfig struct {
	ActiveCadence    time.Duration
	IdleCadence      time.Duration
	EpochID          string
	EpochStartedAt   float64
	OwnerGeneration  uint64
	SourceID         string
	SnapshotRevision func() uint64
	ActiveFunc       func() bool
}

func (c AgentStatusCollectorConfig) withDefaults() AgentStatusCollectorConfig {
	// The family contract is fixed: active scans are every 10 seconds and idle
	// scans every 60 seconds. Do not let a caller publish another cadence.
	c.ActiveCadence = 10 * time.Second
	c.IdleCadence = time.Minute
	if c.EpochID == "" {
		c.EpochID = "agent-status"
	}
	if c.SourceID == "" {
		c.SourceID = "statusd"
	}
	return c
}

type AgentStatusCollectorCounters struct {
	Cycles               uint64
	AcceptedObservations uint64
	UnavailableSpans     uint64
	Errors               uint64
	LastDuration         time.Duration
}

type agentStatusCounters struct {
	cycles, accepted, unavailable, errors atomic.Uint64
	lastDuration                          atomic.Int64
}

func (c *agentStatusCounters) snapshot() AgentStatusCollectorCounters {
	return AgentStatusCollectorCounters{Cycles: c.cycles.Load(), AcceptedObservations: c.accepted.Load(), UnavailableSpans: c.unavailable.Load(), Errors: c.errors.Load(), LastDuration: time.Duration(c.lastDuration.Load())}
}

// AgentStatusCollector owns one collection goroutine. RunOnce is intended for
// deterministic callers and must not overlap Start.
type AgentStatusCollector struct {
	inventory    AgentStatusInventory
	classifier   AgentStatusClassifier
	writer       BatchWriter
	clock        HostCollectorClock
	config       AgentStatusCollectorConfig
	counters     agentStatusCounters
	mu           sync.Mutex
	run          bool
	stop         context.CancelFunc
	done         chan struct{}
	stateMu      sync.Mutex
	hadSuccess   bool
	epochID      string
	epochStart   float64
	lastRevision uint64
	statusCache  map[string]agentStatusCacheEntry
}

type agentStatusCacheEntry struct {
	acknowledged     bool
	workingStoppedAt time.Time
	updatedAt        time.Time
	lastStatus       AgentStatus
}

func NewAgentStatusCollector(inventory AgentStatusInventory, classifier AgentStatusClassifier, writer BatchWriter, clock HostCollectorClock, config AgentStatusCollectorConfig) *AgentStatusCollector {
	if clock == nil {
		clock = realHostCollectorClock{}
	}
	if classifier == nil {
		classifier = func(ctx context.Context, request AgentStatusRequest) AgentStatusResult {
			return ClassifyAgentStatus(ctx, request, AgentStatusSources{})
		}
	}
	config = config.withDefaults()
	return &AgentStatusCollector{inventory: inventory, classifier: classifier, writer: writer, clock: clock, config: config, epochID: config.EpochID, epochStart: config.EpochStartedAt, statusCache: make(map[string]agentStatusCacheEntry)}
}

func (c *AgentStatusCollector) Counters() AgentStatusCollectorCounters { return c.counters.snapshot() }

func (c *AgentStatusCollector) Start(parent context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.run {
		return errors.New("agent status collector already started")
	}
	ctx, cancel := context.WithCancel(parent)
	c.stop, c.done, c.run = cancel, make(chan struct{}), true
	go c.loop(ctx)
	return nil
}

func (c *AgentStatusCollector) loop(ctx context.Context) {
	defer func() { c.mu.Lock(); c.run = false; close(c.done); c.mu.Unlock() }()
	first := true
	for {
		if !first {
			delay := c.config.ActiveCadence
			if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
				delay = c.config.IdleCadence
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
		c.runOnce(ctx)
	}
}

func (c *AgentStatusCollector) Stop() {
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

func (c *AgentStatusCollector) RunOnce(ctx context.Context) { c.runOnce(ctx) }

func (c *AgentStatusCollector) runOnce(parent context.Context) {
	started := c.clock.Now()
	c.counters.cycles.Add(1)
	cadence := c.config.ActiveCadence
	if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
		cadence = c.config.IdleCadence
	}
	c.stateMu.Lock()
	fact := HostFactContext{EpochID: c.epochID, EpochStartedAt: c.epochStart, ObservedAt: float64(started.UnixNano()) / 1e9, CadenceSeconds: cadence.Seconds(), OwnerGeneration: c.config.OwnerGeneration, SourceID: c.config.SourceID}
	c.stateMu.Unlock()
	batch := c.collect(parent, fact)
	if len(batch.UnavailableSpans) > 0 {
		c.stateMu.Lock()
		if c.hadSuccess {
			ended := fact.ObservedAt
			batch.CoverageEpochs = append(batch.CoverageEpochs, CoverageEpoch{Family: "agent_status", SourceID: fact.SourceID, EpochID: fact.EpochID, StartedAt: fact.EpochStartedAt, EndedAt: &ended, NativeCadenceSeconds: fact.CadenceSeconds, OwnerGeneration: fact.OwnerGeneration})
		}
		c.hadSuccess = false
		c.epochID = fmt.Sprintf("%s-%d", c.config.EpochID, started.UnixNano())
		c.epochStart = fact.ObservedAt + fact.CadenceSeconds
		c.stateMu.Unlock()
	} else if len(batch.Observations) > 0 {
		c.stateMu.Lock()
		c.hadSuccess = true
		c.stateMu.Unlock()
	}
	if c.writer == nil {
		c.counters.errors.Add(1)
	} else if result, err := c.writer.AppendBatch(parent, batch); err != nil {
		c.counters.errors.Add(1)
	} else {
		c.counters.accepted.Add(uint64(result.ObservationsAccepted))
		c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
	}
	c.counters.lastDuration.Store(c.clock.Now().Sub(started).Nanoseconds())
}

func (c *AgentStatusCollector) collect(parent context.Context, fact HostFactContext) AppendBatch {
	if c.inventory == nil {
		c.counters.errors.Add(1)
		return BuildUnavailableFacts("agent_status", fact, "inventory-unavailable")
	}
	records, inventoryErrors := c.inventory(parent)
	if len(inventoryErrors) > 0 {
		c.counters.errors.Add(1)
		return BuildUnavailableFacts("agent_status", fact, "inventory-unavailable")
	}
	if len(records) > MaxAgentStatusEntries {
		return BuildUnavailableFacts("agent_status", fact, "agent-status-bound-exceeded")
	}
	// The fallback identity is intentionally based on the source order, as in
	// Python. Sort only after identities have been assigned so map construction
	// and classification are deterministic without changing agent-N semantics.
	type row struct {
		record AgentRecord
		key    string
		index  int
	}
	rows := make([]row, 0, len(records))
	for index, record := range records {
		if tmuxPaneTarget(record) == "" {
			continue
		}
		key, ok := agentStatusIdentity(record, index)
		if !ok {
			return BuildUnavailableFacts("agent_status", fact, "ambiguous-agent-identity")
		}
		rows = append(rows, row{record: record, key: key, index: index})
	}
	if len(records) > 0 && len(rows) == 0 {
		return BuildUnavailableFacts("agent_status", fact, "pane-capture-unavailable")
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].key < rows[j].key })
	states := make(map[string]any, len(rows))
	sessions := make(map[string]AgentStatus)
	roster := make([]map[string]any, 0, len(rows))
	counts := AgentStatusCounts{}
	seen := make(map[string]bool)
	firstUnavailableReason := ""
	for _, item := range rows {
		record, key := item.record, item.key
		if seen[key] {
			continue
		}
		seen[key] = true
		now := time.Unix(0, int64(fact.ObservedAt*1e9))
		request := AgentStatusRequest{Kind: record.Kind, PID: record.PID, Command: record.Command, Executable: record.Executable, PaneTarget: record.TmuxPaneID, Directory: record.Directory, OpenCodeSession: record.OpenCodeSession, Now: now, TransitionWindow: DefaultAgentTransitionWindow}
		c.stateMu.Lock()
		cached := c.statusCache[key]
		if cached.updatedAt.IsZero() || now.Sub(cached.updatedAt) > 2*DefaultAgentTransitionWindow {
			cached = agentStatusCacheEntry{}
		}
		request.Acknowledged = cached.acknowledged
		request.WorkingStoppedAt = cached.workingStoppedAt
		c.stateMu.Unlock()
		result := c.classifier(parent, request)
		if result.Status == AgentStatusIdle && cached.lastStatus == AgentStatusRun && !request.Acknowledged {
			result.Status = AgentStatusTransition
			result.Evidence = []string{"recent-working-stopped"}
			result.WorkingStoppedAt = now
		}
		c.stateMu.Lock()
		if len(c.statusCache) >= 1024 {
			for oldKey := range c.statusCache {
				delete(c.statusCache, oldKey)
				break
			}
		}
		entry := agentStatusCacheEntry{acknowledged: result.Acknowledged || request.Acknowledged, workingStoppedAt: result.WorkingStoppedAt, updatedAt: now, lastStatus: result.Status}
		if result.Status == AgentStatusRun {
			entry.workingStoppedAt = time.Time{}
		}
		c.statusCache[key] = entry
		c.stateMu.Unlock()
		if result.Status == AgentStatusUnavailable {
			if firstUnavailableReason == "" {
				firstUnavailableReason = result.Reason
			}
			continue
		}
		state := string(result.Status)
		rosterEntry := map[string]any{"id": key, "kind": strings.ToLower(strings.TrimSpace(record.Kind)), "state": state, "session": record.TmuxSession, "pane": tmuxPaneTarget(record)}
		roster = append(roster, rosterEntry)
		counts.Total++
		switch result.Status {
		case AgentStatusRun:
			counts.Running++
		case AgentStatusAsk, AgentStatusError:
			counts.Red++
		case AgentStatusTransition:
			counts.Yellow++
		default:
			states[key] = state
		}
		if result.Status != AgentStatusUnavailable && result.Status != AgentStatusError {
			states[key] = state
		}
		session := record.TmuxSession
		if session == "" {
			session = record.OpenCodeSession
		}
		if session != "" && result.Status != AgentStatusUnavailable && result.Status != AgentStatusError {
			sessions[session] = mergeAgentStatus(sessions[session], result.Status)
		}
	}
	if len(rows) > 0 && len(roster) == 0 {
		if firstUnavailableReason == "" {
			firstUnavailableReason = "pane-capture-unavailable"
		}
		return BuildUnavailableFacts("agent_status", fact, firstUnavailableReason)
	}
	snapshotRevision := agentStatusRevision(c.config, &c.stateMu, &c.lastRevision)
	return BuildAgentStatusFacts(states, sessions, snapshotRevision, counts, roster, fact)
}

func agentStatusIdentity(record AgentRecord, fallbackIndex int) (string, bool) {
	parts := []string{
		strings.TrimSpace(record.TmuxSession),
		tmuxWindowIndexOrFallback(record),
		tmuxPaneTarget(record),
		strings.ToLower(strings.TrimSpace(record.Kind)),
	}
	keyParts := make([]string, 0, len(parts))
	for _, part := range parts {
		if part != "" {
			keyParts = append(keyParts, part)
		}
	}
	if len(keyParts) == 0 {
		return fmt.Sprintf("agent-%d", fallbackIndex), true
	}
	return strings.Join(keyParts, "|"), true
}

func tmuxWindowIndexOrFallback(record AgentRecord) string {
	if value := strings.TrimSpace(record.TmuxWindow); value != "" {
		return value
	}
	return strings.TrimSpace(record.TmuxWindowName)
}

func tmuxPaneTarget(record AgentRecord) string {
	if value := strings.TrimSpace(record.TmuxPaneID); value != "" {
		return value
	}
	return strings.TrimSpace(record.TmuxPane)
}

func mergeAgentStatus(old, next AgentStatus) AgentStatus {
	rank := map[AgentStatus]int{AgentStatusIdle: 1, AgentStatusTransition: 2, AgentStatusRun: 3, AgentStatusAsk: 4}
	if rank[next] > rank[old] {
		return next
	}
	return old
}

func agentStatusRevision(config AgentStatusCollectorConfig, stateMu *sync.Mutex, lastRevision *uint64) uint64 {
	if config.SnapshotRevision == nil {
		// No status snapshot was supplied. Python publishes the neutral revision,
		// not a timestamp from a different source.
		return 0
	}
	candidate := config.SnapshotRevision()
	stateMu.Lock()
	defer stateMu.Unlock()
	if candidate <= *lastRevision {
		candidate = *lastRevision + 1
	}
	*lastRevision = candidate
	return candidate
}

// BuildAgentStatusFacts matches Python's agent_status_success builder.
func BuildAgentStatusFacts(states map[string]any, sessionStates map[string]AgentStatus, snapshotRevision uint64, counts AgentStatusCounts, roster []map[string]any, fact HostFactContext) AppendBatch {
	if !validFactContext(fact) {
		return AppendBatch{}
	}
	sessions := make(map[string]any, len(sessionStates))
	for key, value := range sessionStates {
		sessions[key] = string(value)
	}
	if len(roster) > MaxAgentStatusEntries {
		return BuildUnavailableFacts("agent_status", fact, "agent-status-bound-exceeded")
	}
	payload := map[string]any{"states": states, "session_states": sessions, "snapshot_revision": snapshotRevision, "counts": map[string]any{"total": counts.Total, "running": counts.Running, "red": counts.Red, "yellow": counts.Yellow}, "agents": roster}
	if encoded, err := json.Marshal(payload); err != nil || len(encoded) > MaxAgentStatusRosterBytes {
		return BuildUnavailableFacts("agent_status", fact, "agent-status-payload-bound-exceeded")
	}
	return AppendBatch{Observations: []Observation{{EventID: agentStatusEventID(fact), Family: "agent_status", SourceID: fact.SourceID, ObservedAt: fact.ObservedAt, EpochID: fact.EpochID, OwnerGeneration: fact.OwnerGeneration, Payload: payload}}, CoverageEpochs: []CoverageEpoch{{Family: "agent_status", SourceID: fact.SourceID, EpochID: fact.EpochID, StartedAt: fact.EpochStartedAt, EndedAt: floatPointer(fact.ObservedAt + fact.CadenceSeconds), NativeCadenceSeconds: fact.CadenceSeconds, OwnerGeneration: fact.OwnerGeneration}}}
}

func agentStatusEventID(fact HostFactContext) string {
	return hostEventID("agent_status", fact.SourceID, fact.EpochID, fact.ObservedAt, "sample")
}
