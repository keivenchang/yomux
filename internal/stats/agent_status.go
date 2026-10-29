package stats

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode"
	"unicode/utf8"
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
	// ObservedAt is stamped by the pane reader so callers can order this evidence against other samples.
	ObservedAt time.Time
	// Status is the structured screen classification produced by the pane
	// reader. VisibleText remains for diagnostics and legacy readers only.
	Status           string
	Acknowledged     bool
	WorkingStoppedAt time.Time
}

type AgentStatusProcess struct {
	PID             int
	Command         string
	Executable      string
	StartedIdentity string
	Alive           bool
	StartedAt       float64
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
	Status                 AgentStatus
	Evidence               []string
	QueuedFollowupQuestion bool
	AttentionKind          string
	AttentionSignature     string
	Reason                 string
	RawStatus              string
	Acknowledged           bool
	WorkingStoppedAt       time.Time
	// ObservedAt carries the pane evidence timestamp through the classifier result.
	ObservedAt time.Time
}

// AgentStatusSources are the only live-system seams used by ClassifyAgentStatus.
// A nil seam means that the corresponding evidence is unavailable, not true.
type AgentStatusSources struct {
	CapturePane   func(context.Context, string) (AgentStatusPane, error)
	Process       func(context.Context, int) (AgentStatusProcess, error)
	OpenCodeState func(context.Context, AgentStatusRequest) (string, []string, error)
	CodexTurn     func(context.Context, int, float64) AgentStatus
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
		// The reader returns only after its database evidence has been read.
		// Stamp at that boundary so metadata and the collector compare the same
		// kind of observation rather than their later publication times.
		observedAt := time.Now()
		if err != nil {
			return unavailableAt(err.Error(), observedAt)
		}
		switch state {
		case "working":
			return resultAt(AgentStatusRun, evidence, observedAt)
		case "paused":
			return resultAt(AgentStatusAsk, evidence, observedAt)
		case "idle":
			return resultAt(AgentStatusIdle, evidence, observedAt)
		default:
			return unavailableAt("opencode-state-unknown", observedAt)
		}
	}
	if kind != "claude" && kind != "codex" {
		return unavailable("unsupported-agent")
	}
	if sources.CapturePane == nil || strings.TrimSpace(request.PaneTarget) == "" {
		return unavailable("pane-capture-unavailable")
	}
	processStartedAt := float64(0)
	if request.PID > 0 && sources.Process != nil {
		process, err := sources.Process(ctx, request.PID)
		processObservedAt := time.Now()
		if err != nil || !process.Alive {
			return unavailableAt("agent-process-unavailable", processObservedAt)
		}
		if process.Command != "" && !agentProcessMatchesKind(kind, process.Command, request.Executable) {
			return unavailableAt("agent-process-identity-mismatch", processObservedAt)
		}
		if strings.TrimSpace(process.Command) != "" && request.Command == "" {
			request.Command = process.Command
		}
		processStartedAt = process.StartedAt
	}
	pane, err := sources.CapturePane(ctx, request.PaneTarget)
	if pane.ObservedAt.IsZero() {
		pane.ObservedAt = time.Now()
	}
	if err != nil || pane.Error != "" {
		return unavailableAt("pane-capture-failed", pane.ObservedAt)
	}
	text := pane.VisibleText
	if len([]byte(text)) > MaxAgentPaneCaptureBytes {
		return unavailableAt("pane-capture-bound-exceeded", pane.ObservedAt)
	}
	if strings.TrimSpace(text) == "" {
		return unavailableAt("empty-pane-capture", pane.ObservedAt)
	}
	classified := classifyPaneStatus(pane, request)
	if kind == "codex" && sources.CodexTurn != nil && (classified.Status == AgentStatusIdle || classified.Status == AgentStatusTransition || (classified.Status == AgentStatusUnavailable && classified.Reason == "ambiguous-pane-evidence")) {
		// Codex removes its busy row while streaming commentary. A composer is
		// not proof of completion while this process is still emitting turn events.
		switch sources.CodexTurn(ctx, request.PID, processStartedAt) {
		case AgentStatusRun:
			classified = result(AgentStatusRun, []string{"codex-active-turn"})
		case AgentStatusUnavailable:
			// Unknown history cannot erase explicit completion or its acknowledgement.
			if !agentStatusHasEvidence(classified, "completion-screen", "settled-completion", "structured-transition-status") && classified.RawStatus != "cooldown" && classified.RawStatus != "transition" {
				classified = unavailable("codex-turn-evidence-unavailable")
			}
		case AgentStatusError:
			classified = result(AgentStatusError, []string{"codex-failed-turn"})
		}
	}
	if classified.Status == AgentStatusAsk || classified.Status == AgentStatusError || classified.QueuedFollowupQuestion {
		text := currentAgentPaneText(pane.VisibleText)
		lines := strings.Split(text, "\n")
		if classified.QueuedFollowupQuestion {
			for index := currentWorkingFooterIndex(lines) + 1; index < len(lines); index++ {
				line := lines[index]
				if strings.Contains(strings.ToLower(line), "queued follow-up inputs") {
					lines = lines[index:min(index+6, len(lines))]
					break
				}
			}
		} else if len(lines) > 24 {
			lines = lines[len(lines)-24:]
		}
		for index := range lines {
			lines[index] = strings.TrimRight(lines[index], " \t")
		}
		digest := sha256.Sum256([]byte(strings.Join(lines, "\n")))
		classified.AttentionSignature = string(classified.Status) + ":" + hex.EncodeToString(digest[:])
	}
	classified.ObservedAt = pane.ObservedAt
	return classified
}

func classifyPaneStatus(pane AgentStatusPane, request AgentStatusRequest) AgentStatusResult {
	raw := strings.ToLower(strings.TrimSpace(pane.Status))
	acknowledged := pane.Acknowledged || request.Acknowledged
	stoppedAt := pane.WorkingStoppedAt
	if stoppedAt.IsZero() {
		stoppedAt = request.WorkingStoppedAt
	}
	result := AgentStatusResult{RawStatus: raw, Acknowledged: acknowledged, WorkingStoppedAt: stoppedAt}
	if CapacityNoticeCurrent(pane.VisibleText) {
		result.Status = AgentStatusError
		result.Evidence = []string{"model-capacity-notice"}
		result.AttentionKind = "unknown"
		return result
	}
	switch raw {
	case "approval", "needs-approval", "needs-input", "attention", "interrupted":
		result.Status = AgentStatusAsk
		result.Evidence = []string{"structured-ask-status"}
		result.AttentionKind = agentAttentionKind(pane.VisibleText)
		if raw == "approval" || raw == "needs-approval" {
			result.AttentionKind = "approval"
		}
		return result
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
	case "blocked", "error", "failed", "failure":
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
	if raw == "" {
		legacy := classifyVisibleAgentText(pane.VisibleText)
		legacy.RawStatus = raw
		legacy.Acknowledged = acknowledged
		legacy.WorkingStoppedAt = stoppedAt
		if legacy.Status == AgentStatusIdle && !acknowledged && !stoppedAt.IsZero() && !now.Before(stoppedAt) && now.Sub(stoppedAt) < window {
			legacy.Status = AgentStatusTransition
			legacy.Evidence = []string{"recent-working-stopped"}
		}
		if legacy.Status == AgentStatusTransition && (acknowledged || (!stoppedAt.IsZero() && !now.Before(stoppedAt) && now.Sub(stoppedAt) >= window)) {
			legacy.Status = AgentStatusIdle
			legacy.Evidence = []string{"settled-completion"}
		}
		return legacy
	}
	if !acknowledged && !stoppedAt.IsZero() && !now.Before(stoppedAt) && now.Sub(stoppedAt) < window {
		result.Status = AgentStatusTransition
		result.Evidence = []string{"recent-working-stopped"}
		return result
	}
	result.Status = AgentStatusIdle
	result.Evidence = []string{"settled-status"}
	return result
}

// CapacityNoticeCurrent recognizes the visible Codex capacity failure only
// while it remains the newest turn output above the live composer. Sharing this
// predicate keeps status display and YO's optional retry worker in agreement.
func CapacityNoticeCurrent(visible string) bool {
	lines := strings.Split(strings.ReplaceAll(visible, "\r\n", "\n"), "\n")
	composer := -1
	for index := len(lines) - 1; index >= max(0, len(lines)-12); index-- {
		if capacityComposerLine(strings.TrimSpace(lines[index])) {
			composer = index
			break
		}
	}
	if composer < 0 {
		return false
	}
	for index := composer - 1; index >= 0; index-- {
		line := strings.TrimSpace(lines[index])
		if agentComposerChromeLine(line, true) {
			continue
		}
		return capacityNoticeLine(line)
	}
	return false
}

func capacityNoticeLine(line string) bool {
	line = strings.TrimSpace(line)
	if !strings.HasPrefix(line, "■") {
		return false
	}
	line = strings.TrimLeft(strings.TrimPrefix(line, "■"), " \t\r\n\v\f")
	return line == "Selected model is at capacity. Please try a different model."
}

func capacityComposerLine(line string) bool {
	return strings.HasPrefix(line, "›") || strings.HasPrefix(line, "❯") || strings.HasPrefix(line, ">")
}

var agentBackgroundTerminalChrome = regexp.MustCompile(`^\d+\s+background terminals? running\s+·\s+/ps to view\s+·\s+/stop to close$`)
var agentComposerShortcutChrome = regexp.MustCompile(`^\? for shortcuts(?:\s+⚠\x{FE0F}?\s+\d+\s+warnings?\s+·\s+f2 to view)?$`)
var agentComposerModelChrome = regexp.MustCompile(`(?i)^(?:gpt-[^ ]+\b.*|claude\s+code\b.*)$`)
var agentComposerOtherFooterChrome = regexp.MustCompile(`(?i)^(?:bypass permissions\b.*|tip:.*)$`)

func agentComposerChromeLine(line string, beforeComposer bool) bool {
	line = strings.TrimSpace(line)
	if line == "" || capacityRuleLine(line) {
		return true
	}
	lower := strings.ToLower(line)
	if agentComposerModelChrome.MatchString(line) || agentComposerOtherFooterChrome.MatchString(line) {
		return true
	}
	if beforeComposer {
		return agentBackgroundTerminalChrome.MatchString(lower)
	}
	return agentComposerShortcutChrome.MatchString(lower)
}

// AgentComposerFooter recognizes the ordered chrome below a composer. A help-looking
// continuation before the model footer is still a draft, not permission to submit it.
func AgentComposerFooter(lines []string) bool {
	modelFooter, shortcuts := false, false
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || capacityRuleLine(line) {
			continue
		}
		if shortcuts || !agentComposerChromeLine(line, false) {
			return false
		}
		if agentComposerShortcutChrome.MatchString(strings.ToLower(line)) {
			if !modelFooter {
				return false
			}
			shortcuts = true
		}
		if agentComposerModelChrome.MatchString(line) {
			if modelFooter {
				return false
			}
			modelFooter = true
		}
	}
	return true
}

func capacityRuleLine(line string) bool {
	if line == "" {
		return false
	}
	for _, character := range line {
		if !strings.ContainsRune("─━╭╮╰╯│┌┐└┘", character) {
			return false
		}
	}
	return true
}

// Empty terminal rows below the composer must not displace current evidence or its identity.
func currentAgentPaneText(text string) string {
	return strings.TrimRight(strings.ReplaceAll(text, "\r", ""), " \t\n")
}

func classifyVisibleAgentText(text string) AgentStatusResult {
	// A visible Codex pane commonly contains old completion text above the live
	// `Ask Codex to do anything` composer. Limit evidence to the current tail and
	// let a current idle composer win over stale completion words.
	lower := strings.ToLower(currentAgentPaneText(text))
	lines := strings.Split(lower, "\n")
	tailStart := 0
	if len(lines) > 24 {
		tailStart = len(lines) - 24
	}
	tail := strings.Join(lines[tailStart:], "\n")
	approvalText := currentUnquotedPromptText(lines[tailStart:])
	ask := containsAny(approvalText, "enter to select", "esc to cancel", "yes, and don't ask", "yes/no", "what should claude", "what should codex", "approve this", "approve command", "approval required", "approval needed", "allow this", "would you like to run the following")
	run := containsWorkingScreenSignal(tail)
	transition := containsStatusLinePrefix(tail, "goal achieved", "all tasks complete", "all tasks completed", "task completed", "finished")
	// Generic words such as "errors" can be part of an idle suggestion. Keep
	// error evidence to explicit failure-shaped markers, matching the prompt
	// detector's actionable error screens rather than classifying prose.
	errorState := containsStatusLinePrefix(tail, "error:", "fatal", "traceback", "command failed", "exited with code")
	// A Codex/Claude working footer is the current screen boundary. Tool output
	// above it can contain a traceback or command failure from an earlier turn;
	// treating that scrollback as simultaneous current evidence paints a false
	// unavailable state over a live working pane. Only error evidence at or below
	// the current working footer can conflict with that footer.
	visibleLines := lines[tailStart:]
	workingFooter := currentWorkingFooterIndex(visibleLines)
	workedForFooter := currentWorkedForFooterIndex(visibleLines)
	interruptedIndex := currentConversationInterruptedIndex(visibleLines)
	interruptionWins := interruptedIndex > workingFooter && interruptedIndex > workedForFooter && !containsWorkingScreenSignal(strings.Join(visibleLines[interruptedIndex+1:], "\n"))
	workedForCompletion := workedForFooter > workingFooter && workedForFooter > interruptedIndex
	if workedForCompletion {
		// Codex leaves this duration footer after finishing a turn. It is a newer
		// screen boundary than stale activity above it. Re-evaluate everything
		// below the footer so fresh work, prompts, and errors take precedence.
		completionLines := visibleLines[workedForFooter+1:]
		completionText := strings.Join(completionLines, "\n")
		approvalText = currentUnquotedPromptText(completionLines)
		ask = containsAny(approvalText, "enter to select", "esc to cancel", "yes, and don't ask", "yes/no", "what should claude", "what should codex", "approve this", "approve command", "approval required", "approval needed", "allow this", "would you like to run the following")
		workingFooter = -1
		run = containsWorkingScreenSignal(completionText)
		transition = containsStatusLinePrefix(completionText, "goal achieved", "all tasks complete", "all tasks completed", "task completed", "finished")
		errorState = containsStatusLinePrefix(completionText, "error:", "fatal", "traceback", "command failed", "exited with code")
	}
	if interruptionWins {
		workingFooter = -1
	}
	if ask {
		// Approval controls and conflicts must describe the same current segment.
		statusText := currentPromptStatusText(approvalText)
		transition = containsStatusLinePrefix(statusText, "goal achieved", "all tasks complete", "all tasks completed", "task completed", "finished")
		errorState = containsStatusLinePrefix(statusText, "error:", "fatal", "traceback", "command failed", "exited with code")
		if workingFooter < 0 {
			run = containsWorkingScreenSignal(statusText)
		}
	}
	queuedFollowupQuestion := workingFooter >= 0 && codexQueuedFollowupQuestionAfter(lines[tailStart:], workingFooter)
	if workingFooter >= 0 {
		// Completion and failure text from an earlier turn can remain above the
		// current working footer. Treat the footer as the live screen boundary
		// for every terminal state, not only explicit error markers; otherwise a
		// still-running Codex pane is downgraded to unavailable by stale "goal
		// achieved" prose in its visible scrollback.
		transition = false
		errorState = false
		ask = containsAny(currentUnquotedPromptText(lines[tailStart+workingFooter+1:]), "enter to select", "esc to cancel", "approval required", "would you like to run the following")
	}
	if workingFooter < 0 && !ask && !run && !errorState && currentComposerQuestion(lines[tailStart:]) {
		ask, transition = true, false
	}
	if interruptionWins {
		// An explicit Codex interruption after the last work marker ends that
		// run; otherwise its stale footer can keep an idle composer green.
		ask, run, transition, errorState = false, false, false, false
		queuedFollowupQuestion = false
	}
	if workedForCompletion && !interruptionWins && !ask && !run && !errorState {
		return result(AgentStatusTransition, []string{"completion-screen"})
	}
	idle := containsAny(tail, "ask anything", "ask codex to do anything", "type your message", "use /", "new task", "claude code") || hasIdleComposerLine(lines[tailStart:], !transition)
	// A live status counter is stronger than generic footer words such as
	// "background terminals running". Compaction and background-terminal wait
	// lines are also working states without the interrupt affordance.
	if !containsWorkingScreenSignal(tail) {
		run = false
	}
	if idle && !ask && !run && !errorState {
		return result(AgentStatusIdle, []string{"idle-composer"})
	}
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
		classified := result(AgentStatusAsk, []string{"prompt-or-approval"})
		classified.AttentionKind = agentAttentionKindFromEvidence(approvalText, currentComposerQuestion(lines[tailStart:]))
		return classified
	}
	if run {
		classified := result(AgentStatusRun, []string{"working-screen"})
		classified.QueuedFollowupQuestion = queuedFollowupQuestion
		return classified
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

func agentAttentionKind(text string) string {
	kind := classifyVisibleAgentText(text).AttentionKind
	if kind == "" {
		return "unknown"
	}
	return kind
}

func agentAttentionKindFromEvidence(promptText string, composerQuestion bool) string {
	if composerQuestion {
		return "question"
	}
	if containsAny(promptText, "enter to select", "esc to cancel", "yes, and don't ask", "yes/no", "approve this", "approve command", "approval required", "approval needed", "allow this", "would you like to run the following") {
		return "approval"
	}
	return "unknown"
}

func codexQueuedFollowupQuestionAfter(lines []string, workingFooter int) bool {
	for index := workingFooter + 1; index < len(lines); index++ {
		if !strings.Contains(strings.ToLower(strings.TrimSpace(lines[index])), "queued follow-up inputs") {
			continue
		}
		questionCount := false
		answerHint := false
		end := min(index+5, len(lines))
		for _, line := range lines[index+1 : end] {
			fields := strings.Fields(strings.ToLower(strings.TrimSpace(line)))
			if len(fields) == 3 && fields[0] == "?" && (fields[2] == "question" || fields[2] == "questions") {
				count, err := strconv.Atoi(fields[1])
				questionCount = err == nil && count > 0
			}
			if strings.Join(fields, " ") == "shift+← to answer" {
				answerHint = true
			}
		}
		if questionCount && answerHint {
			return true
		}
	}
	return false
}

func currentWorkingFooterIndex(lines []string) int {
	latest := -1
	for index := 0; index < len(lines); index++ {
		text := strings.TrimSpace(lines[index])
		if strings.HasPrefix(text, "```") {
			// A literal unmatched fence in tool output cannot hide the live TUI
			// footer. Exclude complete quoted examples, not the rest of the pane.
			for end := index + 1; end < len(lines); end++ {
				if strings.HasPrefix(strings.TrimSpace(lines[end]), "```") {
					index = end
					break
				}
			}
			continue
		}
		if strings.HasPrefix(text, ">") || strings.HasPrefix(text, "`") || strings.HasPrefix(text, "\"") || strings.HasPrefix(text, "'") || strings.HasPrefix(text, "$") {
			continue
		}
		if isWorkingStatusLine(lines[index]) || currentClaudeInterruptFooter(lines, index) {
			latest = index
		}
	}
	return latest
}

var claudeInterruptFooter = regexp.MustCompile(`^[\p{S}\s]*(?:[a-z]+ mode|bypass permissions|accept edits) on(?: \(shift\+tab to cycle\))?(?:\s*·\s*[^·]*)*$`)

// Claude's activity verb and spinner are animated. Its interrupt control below
// the ruled composer is stable; prose or an older footer above that composer is
// not evidence that the current turn is still running.
func currentClaudeInterruptFooter(lines []string, index int) bool {
	line := strings.ToLower(strings.TrimSpace(lines[index]))
	if !claudeInterruptFooter.MatchString(line) {
		return false
	}
	interrupt := false
	for _, control := range strings.Split(line, "·") {
		interrupt = interrupt || strings.TrimSpace(control) == "esc to interrupt"
	}
	if !interrupt {
		return false
	}
	for _, later := range lines[index+1:] {
		text := strings.TrimSpace(later)
		if strings.HasPrefix(text, "❯") || strings.HasPrefix(text, "›") {
			return false
		}
	}
	ruleSeen := false
	for previous := index - 1; previous >= 0; previous-- {
		text := strings.TrimSpace(lines[previous])
		if text == "" {
			continue
		}
		if capacityRuleLine(text) {
			ruleSeen = true
			continue
		}
		if strings.HasPrefix(text, "❯") {
			return ruleSeen
		}
		if !ruleSeen {
			return false
		}
	}
	return false
}

func currentWorkedForFooterIndex(lines []string) int {
	for index := len(lines) - 1; index >= 0; index-- {
		line := normalizedStatusLine(lines[index])
		if !strings.HasPrefix(line, "worked for ") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		duration := strings.TrimRight(fields[2], ",.")
		if len(duration) < 2 {
			continue
		}
		unit := duration[len(duration)-1]
		if unit != 'h' && unit != 'm' && unit != 's' {
			continue
		}
		value := strings.TrimSuffix(strings.TrimSuffix(strings.TrimSuffix(duration, "h"), "m"), "s")
		if parsed, err := strconv.ParseFloat(value, 64); err == nil && parsed >= 0 && parsed < 1e9 {
			return index
		}
	}
	return -1
}

func currentConversationInterruptedIndex(lines []string) int {
	for index := len(lines) - 1; index >= 0; index-- {
		if hasStatusLinePrefix(normalizedStatusLine(lines[index]), "conversation interrupted") {
			return index
		}
	}
	return -1
}

func containsWorkingScreenSignal(text string) bool {
	return currentWorkingFooterIndex(strings.Split(text, "\n")) >= 0
}

func containsStatusLinePrefix(text string, prefixes ...string) bool {
	for _, line := range strings.Split(text, "\n") {
		line = normalizedStatusLine(line)
		for _, prefix := range prefixes {
			if hasStatusLinePrefix(line, prefix) {
				return true
			}
		}
	}
	return false
}

func isWorkingStatusLine(line string) bool {
	line = normalizedStatusLine(line)
	for _, marker := range []string{"working", "thinking", "pursuing goal", "goal active", "still running", "tool use", "compacting context", "waiting for background terminal"} {
		if !strings.HasPrefix(line, marker) {
			continue
		}
		suffix := strings.TrimSpace(strings.TrimPrefix(line, marker))
		if suffix == "" {
			return true
		}
		first, _ := utf8.DecodeRuneInString(suffix)
		if first == '(' || first == ':' || first == '·' || first == '…' || first == '-' {
			return true
		}
		if strings.HasPrefix(suffix, "...") {
			return true
		}
	}
	return false
}

func normalizedStatusLine(line string) string {
	line = strings.ToLower(strings.TrimSpace(line))
	line = strings.TrimLeft(line, "◦•*· ")
	// A spinner's phase changes the glyph, not the status. Strip one standalone
	// Unicode symbol rather than pinning the currently observed animation frames.
	first, size := utf8.DecodeRuneInString(line)
	if first > unicode.MaxASCII && unicode.IsSymbol(first) && len(line) > size {
		next, _ := utf8.DecodeRuneInString(line[size:])
		if unicode.IsSpace(next) {
			return strings.TrimSpace(line[size:])
		}
	}
	return line
}

func hasStatusLinePrefix(line, prefix string) bool {
	if !strings.HasPrefix(line, prefix) {
		return false
	}
	suffix := strings.TrimPrefix(line, prefix)
	if suffix == "" {
		return true
	}
	first, _ := utf8.DecodeRuneInString(suffix)
	return unicode.IsSpace(first) || unicode.IsPunct(first) || unicode.IsSymbol(first)
}

// Command bodies in approval dialogs can mention status words without being status rows.
func currentPromptStatusText(text string) string {
	current := []string{}
	for _, line := range strings.Split(text, "\n") {
		line = normalizedStatusLine(line)
		for _, prefix := range []string{"working", "thinking", "pursuing goal", "goal active", "still running", "tool use", "compacting context", "waiting for background terminal", "esc to interrupt", "goal achieved", "all tasks complete", "task completed", "finished", "error:", "fatal", "traceback", "command failed", "exited with code"} {
			if hasStatusLinePrefix(line, prefix) {
				current = append(current, line)
				break
			}
		}
	}
	return strings.Join(current, "\n")
}

// Quoted examples and tool/code blocks are terminal content, not prompt controls.
func currentUnquotedPromptText(lines []string) string {
	current := []string{}
	fenced := false
	for _, line := range lines {
		text := strings.TrimSpace(line)
		if strings.HasPrefix(text, "```") {
			fenced = !fenced
			continue
		}
		if fenced || strings.HasPrefix(text, ">") || strings.HasPrefix(text, "\"") || strings.HasPrefix(text, "'") {
			continue
		}
		lower := strings.ToLower(text)
		completed := strings.HasPrefix(lower, "• ") && containsAny(lower, "all done", "goal achieved", "all tasks complete", "task completed", "finished")
		if strings.HasPrefix(text, "$") || strings.HasPrefix(lower, "• ran ") || completed {
			current = nil
			continue
		}
		current = append(current, text)
	}
	return strings.Join(current, "\n")
}

var agentQuestionURL = regexp.MustCompile(`(?i)(?:https?://|file://|www\.)\S+`)

func agentQuestionLineText(lines []string, index int) string {
	text := strings.TrimSpace(lines[index])
	withoutURLs := strings.TrimSpace(agentQuestionURL.ReplaceAllString(text, ""))
	if withoutURLs != text || len(strings.Fields(text)) != 1 {
		return withoutURLs
	}
	queryContinues := index+1 < len(lines) && len(strings.Fields(lines[index+1])) == 1 && strings.Contains(lines[index+1], "=")
	if !strings.ContainsAny(text, "/=&%#") && !queryContinues {
		return text
	}
	// Hard wrapping can put the query delimiter on a later row than its scheme.
	// Keep a standalone prose question after a URL, such as "Continue?".
	for previous := index - 1; previous >= 0; previous-- {
		fragment := strings.TrimSpace(lines[previous])
		if agentQuestionURL.MatchString(fragment) {
			return ""
		}
		if len(strings.Fields(fragment)) != 1 {
			break
		}
	}
	return text
}

// A current unquoted question near the composer requests input. Codex can place
// explanatory text and its duration footer between the question and composer.
func currentComposerQuestion(lines []string) bool {
	for index := len(lines) - 1; index >= 0; index-- {
		line := strings.TrimSpace(lines[index])
		if !strings.HasPrefix(line, "›") && !strings.HasPrefix(line, "❯") {
			continue
		}
		promptLines := strings.Split(currentUnquotedPromptText(lines[max(0, index-16):index]), "\n")
		for previous := len(promptLines) - 1; previous >= 0; previous-- {
			text := strings.TrimSpace(promptLines[previous])
			if text == "" || strings.Trim(text, "─━-") == "" {
				continue
			}
			lower := strings.ToLower(text)
			if strings.HasPrefix(text, ">") || strings.HasPrefix(text, "`") || strings.HasPrefix(text, "$") || strings.HasPrefix(text, "›") || strings.HasPrefix(text, "❯") {
				return false
			}
			if strings.HasPrefix(lower, "worked for ") {
				continue
			}
			if containsStatusLinePrefix(normalizedStatusLine(lower), "all done", "goal achieved", "all tasks complete", "all tasks completed", "task completed", "finished") {
				return false
			}
			// A query delimiter can end a physical URL row in a hard-wrapped TUI.
			// Only punctuation outside the URL can establish a user question.
			if strings.HasSuffix(agentQuestionLineText(promptLines, previous), "?") {
				return true
			}
		}
		return false
	}
	return false
}

func containsAny(text string, needles ...string) bool {
	for _, needle := range needles {
		if strings.Contains(text, needle) {
			return true
		}
	}
	return false
}

func hasIdleComposerLine(lines []string, allowEmpty bool) bool {
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		prompt, size := utf8.DecodeRuneInString(trimmed)
		if prompt != '›' && prompt != '❯' {
			continue
		}
		// The empty Unicode composer remains an idle prompt after a long reply
		// scrolls the banner away. Slice by the rune width, not ASCII byte offsets.
		composer := strings.TrimSpace(trimmed[size:])
		// A blank composer accompanies explicit completion screens too; retain
		// their transition while allowing named idle suggestions to supersede prose.
		if composer == "" && !allowEmpty {
			continue
		}
		if len(composer) >= 2 && composer[0] >= '0' && composer[0] <= '9' && composer[1] == '.' {
			continue
		}
		return true
	}
	return false
}

func result(status AgentStatus, evidence []string) AgentStatusResult {
	if len(evidence) > MaxAgentStatusEvidence {
		evidence = evidence[:MaxAgentStatusEvidence]
	}
	classified := AgentStatusResult{Status: status, Evidence: append([]string(nil), evidence...)}
	if status == AgentStatusAsk {
		// Persisted pause evidence need not distinguish a question from approval.
		classified.AttentionKind = "unknown"
	}
	return classified
}

func agentStatusHasEvidence(classified AgentStatusResult, matches ...string) bool {
	for _, evidence := range classified.Evidence {
		for _, match := range matches {
			if evidence == match {
				return true
			}
		}
	}
	return false
}

func resultAt(status AgentStatus, evidence []string, observedAt time.Time) AgentStatusResult {
	classified := result(status, evidence)
	classified.ObservedAt = observedAt
	return classified
}

func unavailable(reason string) AgentStatusResult {
	return AgentStatusResult{Status: AgentStatusUnavailable, Reason: reason}
}

func unavailableAt(reason string, observedAt time.Time) AgentStatusResult {
	classified := unavailable(reason)
	classified.ObservedAt = observedAt
	return classified
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
		// An explicit process session must exist in the selected database. An
		// empty or mismatched database otherwise falls through to zero
		// session_input/part rows and is incorrectly reported as idle.
		rows, err := db.reader.Rows(ctx, `SELECT id FROM session WHERE id = ? LIMIT 2`, request.OpenCodeSession)
		if err != nil {
			return "", nil, errors.New("opencode-state-unavailable")
		}
		if len(rows) == 0 {
			return "", nil, errors.New("opencode-session-unavailable")
		}
		if len(rows) > 1 || len(rows[0]) != 1 {
			return "", nil, errors.New("opencode-state-malformed")
		}
		storedSession, ok := openCodeString(rows[0][0])
		if !ok || storedSession != request.OpenCodeSession {
			return "", nil, errors.New("opencode-state-malformed")
		}
		rows, err = db.reader.Rows(ctx, `SELECT delivery, admitted_seq, promoted_seq FROM session_input WHERE session_id = ? ORDER BY time_created DESC LIMIT 2`, request.OpenCodeSession)
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
	ActiveCadence         time.Duration
	IdleCadence           time.Duration
	EpochID               string
	EpochStartedAt        float64
	OwnerGeneration       uint64
	SourceID              string
	SnapshotRevision      func() uint64
	ActiveFunc            func() bool
	AttentionAcknowledged func(string) (bool, error)
	AttentionCommitted    func([]string) error
	Committed             func()
}

func (c AgentStatusCollectorConfig) withDefaults() AgentStatusCollectorConfig {
	// The family contract is fixed: active scans are every 10 seconds and idle
	// scans every 60 seconds. Do not let a caller publish another cadence.
	c.ActiveCadence = AgentStatusActiveCadence
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
	DurationTotal        time.Duration
	LastCadence          time.Duration
}

type agentStatusCounters struct {
	cycles, accepted, unavailable, errors atomic.Uint64
	lastDuration                          atomic.Int64
	durationTotal                         atomic.Uint64
	lastCadence                           atomic.Int64
}

func (c *agentStatusCounters) snapshot() AgentStatusCollectorCounters {
	return AgentStatusCollectorCounters{Cycles: c.cycles.Load(), AcceptedObservations: c.accepted.Load(), UnavailableSpans: c.unavailable.Load(), Errors: c.errors.Load(), LastDuration: time.Duration(c.lastDuration.Load()), DurationTotal: time.Duration(c.durationTotal.Load()), LastCadence: time.Duration(c.lastCadence.Load())}
}

// AgentStatusCollector owns one collection goroutine. RunOnce is intended for
// deterministic callers and must not overlap Start.
type AgentStatusCollector struct {
	inventory     AgentStatusInventory
	classifier    AgentStatusClassifier
	writer        BatchWriter
	clock         HostCollectorClock
	config        AgentStatusCollectorConfig
	counters      agentStatusCounters
	mu            sync.Mutex
	run           bool
	stop          context.CancelFunc
	done          chan struct{}
	stateMu       sync.Mutex
	hadSuccess    bool
	epochID       string
	epochStart    float64
	lastRevision  uint64
	lastCadence   float64
	statusCache   map[string]agentStatusCacheEntry
	lastSignature string
	revisionSet   bool
}

type agentStatusCacheEntry struct {
	acknowledged         bool
	processInstanceID    string
	workingStoppedAt     time.Time
	transitionID         string
	cooldownAttentionKey string
	updatedAt            time.Time
	lastStatus           AgentStatus
	completionSettled    bool
	attentionSignature   string
	attentionKey         string
	attentionSettled     bool
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

func (c *AgentStatusCollector) RestoreRecentLifecycles(lifecycles []AgentStatusLifecycleState) {
	c.stateMu.Lock()
	defer c.stateMu.Unlock()
	for _, lifecycle := range lifecycles {
		if lifecycle.ID == "" || lifecycle.ObservedAt.IsZero() {
			continue
		}
		if lifecycle.AttentionKey == "" && !lifecycle.CompletionSettled && (lifecycle.WorkingStoppedAt.IsZero() || lifecycle.TransitionID == "" || lifecycle.CooldownAttentionKey == "") {
			continue
		}
		if len(c.statusCache) >= MaxAgentStatusEntries {
			break
		}
		state := AgentStatusTransition
		if lifecycle.CompletionSettled {
			state = AgentStatusIdle
		}
		c.statusCache[lifecycle.ID] = agentStatusCacheEntry{
			lastStatus: state, processInstanceID: lifecycle.ProcessInstanceID, updatedAt: lifecycle.ObservedAt,
			workingStoppedAt: lifecycle.WorkingStoppedAt,
			transitionID:     lifecycle.TransitionID, cooldownAttentionKey: lifecycle.CooldownAttentionKey,
			completionSettled:  lifecycle.CompletionSettled,
			attentionSignature: lifecycle.AttentionSignature, attentionKey: lifecycle.AttentionKey, attentionSettled: lifecycle.AttentionSettled,
		}
	}
}

// CurrentAttentionKeys bounds durable pending settlements to the live inventory.
func (c *AgentStatusCollector) CurrentAttentionKeys() map[string]bool {
	c.stateMu.Lock()
	defer c.stateMu.Unlock()
	keys := map[string]bool{}
	for _, entry := range c.statusCache {
		if entry.attentionKey != "" {
			keys[entry.attentionKey] = true
		}
		if entry.cooldownAttentionKey != "" {
			keys[entry.cooldownAttentionKey] = true
		}
	}
	return keys
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
	runWithStatsProfileLabels(ctx, "agent_status_collector", "", func(ctx context.Context) {
		defer func() { c.mu.Lock(); c.run = false; close(c.done); c.mu.Unlock() }()
		for {
			if ctx.Err() != nil {
				return
			}
			started := c.clock.Now()
			c.runOnce(ctx)
			if !c.waitForNextCycle(ctx, started) {
				return
			}
		}
	})
}

func (c *AgentStatusCollector) waitForNextCycle(ctx context.Context, started time.Time) bool {
	// Anchor the next scan to this scan's start. Overruns begin one successor
	// immediately, without overlapping scans or replaying missed deadlines.
	elapsed := max(0, c.clock.Now().Sub(started))
	active, idle := c.config.ActiveCadence-elapsed, c.config.IdleCadence-elapsed
	if c.config.ActiveFunc == nil || c.config.ActiveFunc() {
		if active <= 0 {
			return ctx.Err() == nil
		}
	} else if idle <= 0 {
		return ctx.Err() == nil
	}
	return waitCollectorCadence(ctx, c.clock, max(time.Nanosecond, active), idle, c.config.ActiveFunc)
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
	ctx, cancel := context.WithTimeout(parent, AgentStatusCollectionBudget)
	defer cancel()
	started := c.clock.Now()
	c.counters.cycles.Add(1)
	cadence := c.config.ActiveCadence
	if c.config.ActiveFunc != nil && !c.config.ActiveFunc() {
		cadence = c.config.IdleCadence
	}
	c.stateMu.Lock()
	epochID, epochStart, hadSuccess, lastCadence := c.epochID, c.epochStart, c.hadSuccess, c.lastCadence
	if lastCadence != 0 && lastCadence != cadence.Seconds() {
		// Native cadence is immutable within a coverage epoch. Demand can
		// switch a collector between idle and active cadence, so begin a new
		// epoch instead of attempting to rewrite the old metadata row.
		epochID = fmt.Sprintf("%s-%d", c.config.EpochID, started.UnixNano())
		epochStart = float64(started.UnixNano()) / 1e9
		hadSuccess = false
	}
	c.counters.lastCadence.Store(cadence.Nanoseconds())
	fact := HostFactContext{EpochID: epochID, EpochStartedAt: epochStart, ObservedAt: float64(started.UnixNano()) / 1e9, CadenceSeconds: cadence.Seconds(), OwnerGeneration: c.config.OwnerGeneration, SourceID: c.config.SourceID}
	c.stateMu.Unlock()
	// collect updates the status cache and revision only as it builds the next
	// batch. Keep a rollback copy until the writer acknowledges the batch; a
	// failed append must not make the next successful sample inherit state that
	// was never persisted.
	statusCache, lastSignature, lastRevision, revisionSet := c.statusStateSnapshot()
	var batch AppendBatch
	runWithStatsProfilePhase(ctx, "agent_status_collector", "agent_status", "collection", func(phaseCtx context.Context) {
		batch = c.collect(phaseCtx, fact)
	})
	nextEpochID, nextEpochStart, nextHadSuccess := epochID, epochStart, hadSuccess
	nextCadence := cadence.Seconds()
	if len(batch.UnavailableSpans) > 0 {
		if hadSuccess {
			ended := fact.ObservedAt
			batch.CoverageEpochs = append(batch.CoverageEpochs, CoverageEpoch{Family: "agent_status", SourceID: fact.SourceID, EpochID: fact.EpochID, StartedAt: fact.EpochStartedAt, EndedAt: &ended, NativeCadenceSeconds: fact.CadenceSeconds, OwnerGeneration: fact.OwnerGeneration})
		}
		nextHadSuccess = false
		nextEpochID = fmt.Sprintf("%s-%d", c.config.EpochID, started.UnixNano())
		nextEpochStart = fact.ObservedAt + fact.CadenceSeconds
	} else if len(batch.Observations) > 0 {
		nextHadSuccess = true
	}
	appendSucceeded := false
	if c.writer == nil {
		c.counters.errors.Add(1)
	} else {
		var result AppendResult
		var appendErr error
		runWithStatsProfilePhase(ctx, "agent_status_collector", "agent_status", "append", func(phaseCtx context.Context) {
			result, appendErr = c.writer.AppendBatch(phaseCtx, batch)
		})
		if appendErr != nil {
			c.counters.errors.Add(1)
		} else {
			appendSucceeded = true
			c.counters.accepted.Add(uint64(result.ObservationsAccepted))
			c.counters.unavailable.Add(uint64(result.UnavailableAccepted))
		}
	}
	if len(batch.UnavailableSpans) > 0 {
		c.restoreStatusState(statusCache, lastSignature, lastRevision, revisionSet)
	}
	if appendSucceeded {
		c.stateMu.Lock()
		c.epochID, c.epochStart, c.hadSuccess, c.lastCadence = nextEpochID, nextEpochStart, nextHadSuccess, nextCadence
		c.stateMu.Unlock()
		if c.config.Committed != nil {
			c.config.Committed()
		}
		if len(batch.Observations) > 0 && c.config.AttentionCommitted != nil {
			pending := []string{}
			c.stateMu.Lock()
			for _, entry := range c.statusCache {
				if !entry.attentionSettled && entry.attentionKey != "" {
					pending = append(pending, entry.attentionKey)
				}
				if !entry.completionSettled && entry.cooldownAttentionKey != "" {
					pending = append(pending, entry.cooldownAttentionKey)
				}
			}
			c.stateMu.Unlock()
			if err := c.config.AttentionCommitted(pending); err != nil {
				c.counters.errors.Add(1)
			}
		}
	} else {
		c.restoreStatusState(statusCache, lastSignature, lastRevision, revisionSet)
	}
	elapsed := c.clock.Now().Sub(started)
	if elapsed < 0 {
		elapsed = 0
	}
	c.counters.lastDuration.Store(elapsed.Nanoseconds())
	c.counters.durationTotal.Add(uint64(elapsed.Nanoseconds()))
}

func (c *AgentStatusCollector) statusStateSnapshot() (map[string]agentStatusCacheEntry, string, uint64, bool) {
	c.stateMu.Lock()
	defer c.stateMu.Unlock()
	cache := make(map[string]agentStatusCacheEntry, len(c.statusCache))
	for key, value := range c.statusCache {
		cache[key] = value
	}
	return cache, c.lastSignature, c.lastRevision, c.revisionSet
}

func (c *AgentStatusCollector) restoreStatusState(cache map[string]agentStatusCacheEntry, signature string, revision uint64, revisionSet bool) {
	c.stateMu.Lock()
	c.statusCache = cache
	c.lastSignature = signature
	c.lastRevision = revision
	c.revisionSet = revisionSet
	c.stateMu.Unlock()
}

func (c *AgentStatusCollector) collect(parent context.Context, fact HostFactContext) AppendBatch {
	if c.inventory == nil {
		c.counters.errors.Add(1)
		return BuildUnavailableFacts("agent_status", fact, "inventory-unavailable")
	}
	records, inventoryErrors := c.inventory(parent)
	if len(inventoryErrors) > 0 {
		// Inventory adapters may return a usable process/pane snapshot together
		// with a best-effort enrichment error. Preserve the admitted records so
		// one ambiguous pane or transient annotation failure does not erase the
		// whole roster; the per-record classifier still publishes unavailable
		// when that record's evidence cannot be established.
		c.counters.errors.Add(1)
		if len(records) == 0 {
			return BuildUnavailableFacts("agent_status", fact, "inventory-unavailable")
		}
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
	for index, record := range tmuxScopedAgentRecords(records) {
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
	for _, item := range rows {
		record, key := item.record, item.key
		if seen[key] {
			continue
		}
		seen[key] = true
		now := time.Unix(0, int64(fact.ObservedAt*1e9))
		processInstanceID := AgentStatusProcessInstanceID(record)
		request := AgentStatusRequest{Kind: record.Kind, PID: record.PID, Command: record.Command, Executable: record.Executable, PaneTarget: record.TmuxPaneID, Directory: record.Directory, OpenCodeSession: record.OpenCodeSession, Now: now, TransitionWindow: DefaultAgentTransitionWindow}
		c.stateMu.Lock()
		cached := c.statusCache[key]
		if cached.updatedAt.IsZero() || (cached.processInstanceID != "" && processInstanceID != "" && cached.processInstanceID != processInstanceID) {
			cached = agentStatusCacheEntry{}
		}
		request.Acknowledged = cached.acknowledged
		request.WorkingStoppedAt = cached.workingStoppedAt
		c.stateMu.Unlock()
		result := c.classifier(parent, request)
		if cached.completionSettled && result.Status == AgentStatusTransition && agentStatusHasEvidence(result, "completion-screen") {
			result.Status = AgentStatusIdle
			result.Evidence = []string{"settled-completion"}
		}
		if result.Status == AgentStatusIdle && cached.lastStatus == AgentStatusRun && !request.Acknowledged {
			result.Status = AgentStatusTransition
			result.Evidence = []string{"recent-working-stopped"}
			result.WorkingStoppedAt = now
		}
		if result.Status == AgentStatusTransition && result.WorkingStoppedAt.IsZero() && cached.workingStoppedAt.IsZero() {
			// A Codex completion screen can be the first sample after the pane
			// inventory appears. Give that observed transition a stable lifecycle
			// identity so the browser can acknowledge it just like a sampled
			// run-to-idle transition.
			result.WorkingStoppedAt = now
		}
		transitionID, cooldownAttentionKey := cached.transitionID, cached.cooldownAttentionKey
		if result.Status == AgentStatusTransition && !result.WorkingStoppedAt.IsZero() && (transitionID == "" || cooldownAttentionKey == "") {
			transitionIdentity := fmt.Sprintf("%s:%s:%d", key, processInstanceID, result.WorkingStoppedAt.UnixNano())
			transitionDigest := sha256.Sum256([]byte(transitionIdentity))
			digest := hex.EncodeToString(transitionDigest[:])
			transitionID = "go:transition:" + digest
			cooldownAttentionKey = "go:cooldown:" + digest
		}
		entry := agentStatusCacheEntry{
			acknowledged: result.Acknowledged || request.Acknowledged, processInstanceID: processInstanceID,
			workingStoppedAt: result.WorkingStoppedAt, transitionID: transitionID, cooldownAttentionKey: cooldownAttentionKey,
			updatedAt: now, lastStatus: result.Status, completionSettled: cached.completionSettled,
		}
		if entry.workingStoppedAt.IsZero() && result.Status == AgentStatusIdle {
			entry.workingStoppedAt = cached.workingStoppedAt
		}
		if result.Status == AgentStatusRun {
			entry.workingStoppedAt = time.Time{}
			entry.transitionID, entry.cooldownAttentionKey = "", ""
			entry.acknowledged, entry.completionSettled = false, false
		}
		signature := result.AttentionSignature
		if signature == "" && (result.Status == AgentStatusAsk || result.Status == AgentStatusError || result.QueuedFollowupQuestion) {
			signature = fmt.Sprintf("%s:%t:%s", result.Status, result.QueuedFollowupQuestion, strings.Join(result.Evidence, "|"))
		}
		if signature != "" {
			entry.attentionSignature = signature
			if signature == cached.attentionSignature {
				entry.attentionKey, entry.attentionSettled = cached.attentionKey, cached.attentionSettled
			} else {
				digest := sha256.Sum256([]byte(fmt.Sprintf("%s:%s:%s:%d", key, processInstanceID, signature, now.UnixNano())))
				entry.attentionKey = "go:attention:" + hex.EncodeToString(digest[:])
			}
		} else if result.Status == AgentStatusUnavailable {
			// A capture failure is not evidence that the prompt disappeared.
			entry.attentionSignature, entry.attentionKey, entry.attentionSettled = cached.attentionSignature, cached.attentionKey, cached.attentionSettled
		}
		if c.config.AttentionAcknowledged != nil {
			for _, attentionKey := range []string{entry.attentionKey, entry.cooldownAttentionKey} {
				if attentionKey == "" {
					continue
				}
				acknowledged, err := c.config.AttentionAcknowledged(attentionKey)
				if err != nil {
					return BuildUnavailableFacts("agent_status", fact, "attention-state-unavailable")
				}
				if acknowledged {
					if attentionKey == entry.attentionKey {
						entry.attentionSettled = true
					}
					if attentionKey == entry.cooldownAttentionKey {
						entry.completionSettled, entry.acknowledged = true, true
					}
				}
			}
		}
		if entry.completionSettled {
			entry.workingStoppedAt = time.Time{}
			entry.transitionID, entry.cooldownAttentionKey = "", ""
			if result.Status == AgentStatusTransition {
				result.Status = AgentStatusIdle
				entry.lastStatus = result.Status
			}
		}
		c.stateMu.Lock()
		c.statusCache[key] = entry
		c.stateMu.Unlock()
		state := string(result.Status)
		observedAt := result.ObservedAt
		if observedAt.IsZero() {
			observedAt = c.clock.Now()
		}
		observedAtSeconds := float64(observedAt.UnixNano()) / 1e9
		rosterEntry := map[string]any{"id": key, "kind": strings.ToLower(strings.TrimSpace(record.Kind)), "state": state, "session": record.TmuxSession, "pane": tmuxPaneTarget(record), "observed_at": observedAtSeconds}
		if entry.processInstanceID != "" {
			rosterEntry["process_instance_id"] = entry.processInstanceID
		}
		if !entry.workingStoppedAt.IsZero() {
			stoppedAt := float64(entry.workingStoppedAt.UnixNano()) / 1e9
			rosterEntry["working_stopped_ts"] = stoppedAt
			rosterEntry["transition_id"] = entry.transitionID
			rosterEntry["cooldown_attention_key"] = entry.cooldownAttentionKey
		}
		if entry.attentionKey != "" {
			rosterEntry["attention_signature"] = entry.attentionSignature
			rosterEntry["attention_key"] = entry.attentionKey
			rosterEntry["attention_acknowledged"] = entry.attentionSettled
		}
		rosterEntry["queued_followup_question"] = result.QueuedFollowupQuestion
		if result.Status == AgentStatusAsk {
			rosterEntry["attention_kind"] = result.AttentionKind
		}
		if entry.completionSettled {
			rosterEntry["completion_settled"] = true
		}
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
			// `unavailable` is retained in the roster so a discovered pane is
			// never silently omitted. The compact states/session_states maps
			// intentionally contain only materialized activity states.
			if result.Status != AgentStatusUnavailable {
				states[key] = state
			}
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
	c.stateMu.Lock()
	for key := range c.statusCache {
		if !seen[key] {
			delete(c.statusCache, key)
		}
	}
	c.stateMu.Unlock()
	signature := agentStatusPayloadSignature(states, sessions, counts, roster)
	snapshotRevision := c.agentStatusRevision(signature)
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

func AgentStatusProcessInstanceID(record AgentRecord) string {
	startedIdentity := strings.TrimSpace(record.StartedIdentity)
	if record.PID <= 0 && startedIdentity == "" {
		return ""
	}
	identity := fmt.Sprintf("%d\x1f%s", record.PID, startedIdentity)
	digest := sha256.Sum256([]byte(identity))
	return "process:" + hex.EncodeToString(digest[:])
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

func agentStatusPayloadSignature(states map[string]any, sessions map[string]AgentStatus, counts AgentStatusCounts, roster []map[string]any) string {
	signatureRoster := make([]map[string]any, 0, len(roster))
	for _, entry := range roster {
		signatureEntry := make(map[string]any, len(entry))
		for key, value := range entry {
			if key != "observed_at" {
				signatureEntry[key] = value
			}
		}
		signatureRoster = append(signatureRoster, signatureEntry)
	}
	payload := struct {
		States   map[string]any         `json:"states"`
		Sessions map[string]AgentStatus `json:"session_states"`
		Counts   AgentStatusCounts      `json:"counts"`
		Roster   []map[string]any       `json:"agents"`
	}{States: states, Sessions: sessions, Counts: counts, Roster: signatureRoster}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return ""
	}
	// encoding/json sorts string map keys. Ignore per-agent timestamps so a new
	// sample with unchanged states does not create a status revision.
	return string(encoded)
}

func (c *AgentStatusCollector) agentStatusRevision(signature string) uint64 {
	if c.config.SnapshotRevision == nil {
		// No status snapshot was supplied. Python publishes the neutral revision,
		// not a timestamp from a different source.
		return 0
	}
	c.stateMu.Lock()
	defer c.stateMu.Unlock()
	if c.revisionSet && c.lastSignature == signature {
		return c.lastRevision
	}
	candidate := c.config.SnapshotRevision()
	if c.revisionSet && candidate <= c.lastRevision {
		candidate = c.lastRevision + 1
	}
	if !c.revisionSet && candidate == 0 {
		candidate = 1
	}
	c.lastRevision = candidate
	c.lastSignature = signature
	c.revisionSet = true
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
