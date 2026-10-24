// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/chat"
	"github.com/keivenchang/yomux/internal/stats"
)

const nativeYOAgentActionPreviewTTL = 2 * time.Minute

var nativeYOAgentRiskPatterns = map[string]*regexp.Regexp{
	"secret-like-text": regexp.MustCompile(`(?i)\b(?:token|secret|password|api[_-]?key)\s*=\s*\S+`),
	"credential-path":  regexp.MustCompile(`(?i)(~?/\.config/gitlab-token|~?/\.cache/huggingface/token|~?/\.docker/config\.json|~?/\.ngc/config)`),
	"recursive-delete": regexp.MustCompile(`(?i)\brm\s+-[^\n]*r[^\n]*f\b`),
	"hard-reset":       regexp.MustCompile(`(?i)\bgit\s+reset\s+--hard\b`),
	"broad-pkill":      regexp.MustCompile(`(?i)\bpkill\s+-f\b`),
	"recursive-chmod":  regexp.MustCompile(`(?i)\b(?:chmod|chown)\s+-R\b`),
	"ssh-command":      regexp.MustCompile(`(?i)\bssh\s+\S+`),
}

type nativeYOAgentActionTarget struct {
	Session           string
	SessionIdentity   string
	PaneTarget        string
	PaneWindow        string
	PaneIndex         string
	CWD               string
	AgentKind         string
	AgentPID          int
	AgentCommand      string
	AgentExecutable   string
	AgentSessionID    string
	AgentModel        string
	AgentTranscript   string
	ProcessInstanceID string
	ProcessStarted    string
	Status            stats.AgentStatus
	StatusReason      string
	ObservedAt        time.Time
	VisibleText       string
}

type nativeYOAgentActionPreview struct {
	ID              string
	Session         string
	Locale          string
	Target          nativeYOAgentActionTarget
	Text            string
	Submit          bool
	ReturnResult    bool
	RequiresConfirm bool
	RiskLabels      []string
	CreatedAt       time.Time
}

type nativeYOAgentActions struct {
	server   *server
	mu       sync.Mutex
	sendMu   sync.Mutex
	watchWG  sync.WaitGroup
	closed   bool
	previews map[string]nativeYOAgentActionPreview
	waits    map[string]nativeYOAgentActionWait
	ctx      context.Context
	cancel   context.CancelFunc
	now      func() time.Time
	targetFn func(context.Context, string) (nativeYOAgentActionTarget, error)
	pasteFn  func(context.Context, string, string) error
	submitFn func(context.Context, string) error
}

type nativeYOAgentActionWait struct {
	ID         string
	Session    string
	Locale     string
	Transcript string
	StartedAt  time.Time
	Target     nativeYOAgentActionTarget
	Offset     int64
	Cancel     context.CancelFunc
}

func newNativeYOAgentActions(server *server) *nativeYOAgentActions {
	ctx, cancel := context.WithCancel(context.Background())
	return &nativeYOAgentActions{server: server, previews: map[string]nativeYOAgentActionPreview{}, waits: map[string]nativeYOAgentActionWait{}, ctx: ctx, cancel: cancel, now: time.Now}
}

func (actions *nativeYOAgentActions) close() {
	if actions == nil {
		return
	}
	actions.cancel()
	actions.mu.Lock()
	actions.closed = true
	for id, wait := range actions.waits {
		wait.Cancel()
		delete(actions.waits, id)
	}
	actions.mu.Unlock()
	actions.watchWG.Wait()
}

func (actions *nativeYOAgentActions) pendingWaits() []map[string]any {
	actions.mu.Lock()
	defer actions.mu.Unlock()
	waits := make([]nativeYOAgentActionWait, 0, len(actions.waits))
	for _, wait := range actions.waits {
		waits = append(waits, wait)
	}
	sort.Slice(waits, func(i, j int) bool { return waits[i].StartedAt.Before(waits[j].StartedAt) })
	result := make([]map[string]any, 0, len(waits))
	for _, wait := range waits {
		result = append(result, map[string]any{
			"id": wait.ID, "session": wait.Session, "transcript": wait.Transcript,
			"started_ts": float64(wait.StartedAt.UnixNano()) / 1e9,
		})
	}
	return result
}

func (actions *nativeYOAgentActions) clearWait(id string) bool {
	actions.mu.Lock()
	wait, exists := actions.waits[id]
	if exists {
		delete(actions.waits, id)
	}
	actions.mu.Unlock()
	if exists {
		wait.Cancel()
	}
	return exists
}

func (actions *nativeYOAgentActions) completeWait(ctx context.Context, wait nativeYOAgentActionWait, text string, timedOut bool) {
	actions.mu.Lock()
	current, exists := actions.waits[wait.ID]
	if exists && current.StartedAt.Equal(wait.StartedAt) {
		delete(actions.waits, wait.ID)
	} else {
		exists = false
	}
	actions.mu.Unlock()
	if exists && ctx.Err() == nil {
		actions.recordResult(ctx, wait, text, timedOut)
	}
}

func (actions *nativeYOAgentActions) target(ctx context.Context, session string) (nativeYOAgentActionTarget, error) {
	return actions.targetWithComposer(ctx, session, "")
}

func (actions *nativeYOAgentActions) targetWithComposer(ctx context.Context, session, allowedComposer string) (nativeYOAgentActionTarget, error) {
	if actions.targetFn != nil {
		return actions.targetFn(ctx, session)
	}
	server := actions.server
	if server == nil || strings.TrimSpace(session) == "" {
		return nativeYOAgentActionTarget{}, errors.New("missing target session")
	}
	roster, err := server.tmuxSessionRosterSnapshot(true)
	if err != nil {
		return nativeYOAgentActionTarget{}, fmt.Errorf("refresh tmux session roster: %w", err)
	}
	known := false
	for _, name := range roster.sessions {
		if name == session {
			known = true
			break
		}
	}
	if !known {
		return nativeYOAgentActionTarget{}, fmt.Errorf("session %q is not present", session)
	}
	metadata, err := server.liveSessionMetadataForSessionsWithError([]string{session})
	if err != nil {
		return nativeYOAgentActionTarget{}, fmt.Errorf("refresh target pane inventory: %w", err)
	}
	info := metadata[session]
	if info == nil {
		return nativeYOAgentActionTarget{}, errors.New("session metadata is unavailable")
	}
	panes, _ := info["panes"].([]map[string]any)
	agents, _ := info["agents"].([]map[string]any)
	selected, _ := info["selected_pane"].(map[string]any)
	selectedTarget := nativeAgentStringSetting(selected, "pane_id", "")
	var selectedAgent map[string]any
	for _, candidate := range agents {
		if nativeAgentStringSetting(candidate, "pane_target", "") == selectedTarget {
			selectedAgent = candidate
			break
		}
	}
	if selectedAgent == nil && len(agents) > 0 {
		selectedAgent = agents[0]
	}
	if selectedAgent == nil {
		return nativeYOAgentActionTarget{}, errors.New("target session has no detected Claude or Codex process")
	}
	kind := strings.ToLower(nativeAgentStringSetting(selectedAgent, "kind", ""))
	if kind != "claude" && kind != "codex" {
		return nativeYOAgentActionTarget{}, fmt.Errorf("agent kind %q has no supported native prompt transport", kind)
	}
	paneTarget := nativeAgentStringSetting(selectedAgent, "pane_target", "")
	pidValue, _ := settingsNumber(selectedAgent["pid"])
	pid := int(pidValue)
	if paneTarget == "" || pid <= 0 || server.agentProcessSnapshot == nil || server.agentStatusClassifier == nil {
		return nativeYOAgentActionTarget{}, errors.New("target agent identity is unavailable")
	}
	process, err := server.agentProcessSnapshot(ctx, int(pid))
	if err != nil || !process.Alive || process.StartedIdentity == "" {
		if err == nil {
			err = errors.New("target agent process is not alive")
		}
		return nativeYOAgentActionTarget{}, fmt.Errorf("verify target process identity: %w", err)
	}
	command := nativeAgentStringSetting(selectedAgent, "command", "")
	executable := nativeAgentStringSetting(selectedAgent, "executable", "")
	if process.Command != command || process.Executable != executable {
		return nativeYOAgentActionTarget{}, errors.New("target agent process changed; refresh the preview")
	}
	classified := server.agentStatusClassifier(ctx, stats.AgentStatusRequest{
		Kind: kind, PID: int(pid), Command: command, Executable: executable,
		PaneTarget: paneTarget, Directory: nativeAgentStringSetting(selectedAgent, "cwd", ""),
	})
	if classified.Status != stats.AgentStatusIdle {
		return nativeYOAgentActionTarget{}, fmt.Errorf("target is not ready to accept a prompt: %s", classified.Status)
	}
	captureContext, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	capture, err := server.tmuxCommandContext(captureContext, "capture-pane", "-p", "-J", "-t", paneTarget).Output()
	if err != nil || len(capture) > stats.MaxAgentPaneCaptureBytes {
		if err == nil {
			err = errors.New("target pane capture exceeds the limit")
		}
		return nativeYOAgentActionTarget{}, fmt.Errorf("capture target pane: %w", err)
	}
	composer, composerErr := nativeYOAgentComposerText(string(capture))
	if composerErr != nil && allowedComposer == "" {
		return nativeYOAgentActionTarget{}, composerErr
	}
	if allowedComposer != "" && composer != strings.Join(strings.Fields(allowedComposer), " ") {
		return nativeYOAgentActionTarget{}, errors.New("target composer changed after paste; prompt was not submitted")
	}
	var selectedPane map[string]any
	for _, pane := range panes {
		if nativeAgentStringSetting(pane, "pane_id", "") == paneTarget {
			selectedPane = pane
			break
		}
	}
	return nativeYOAgentActionTarget{
		Session: session, SessionIdentity: goTmuxSessionIdentity(session, roster.identities[session]),
		PaneTarget: paneTarget, PaneWindow: nativeAgentStringSetting(selectedAgent, "window", ""),
		PaneIndex: nativeAgentStringSetting(selectedAgent, "pane", ""),
		AgentKind: kind,
		AgentPID:  int(pid), AgentCommand: command, AgentExecutable: executable,
		AgentSessionID:    nativeAgentStringSetting(selectedAgent, "session_id", ""),
		AgentTranscript:   nativeAgentStringSetting(selectedAgent, "transcript", ""),
		ProcessInstanceID: stats.AgentStatusProcessInstanceID(stats.AgentRecord{PID: int(pid), StartedIdentity: process.StartedIdentity}),
		ProcessStarted:    process.StartedIdentity, Status: classified.Status,
		StatusReason: classified.Reason, ObservedAt: classified.ObservedAt,
		VisibleText: string(capture),
		CWD:         nativeAgentStringSetting(selectedAgent, "cwd", nativeAgentStringSetting(selectedPane, "current_path", "")),
	}, nil
}

func nativeYOAgentComposerText(capture string) (string, error) {
	lines := strings.Split(strings.ReplaceAll(capture, "\r", ""), "\n")
	start := -1
	for index := len(lines) - 1; index >= 0 && index >= len(lines)-8; index-- {
		line := strings.TrimSpace(lines[index])
		for _, marker := range []string{"›", "❯", "➜"} {
			if strings.HasPrefix(line, marker) {
				start = index
				break
			}
		}
		if start >= 0 {
			break
		}
	}
	if start < 0 {
		return "", errors.New("target composer could not be identified; nothing was sent")
	}
	first := strings.TrimSpace(lines[start])
	for _, marker := range []string{"›", "❯", "➜"} {
		if strings.HasPrefix(first, marker) {
			first = strings.TrimSpace(strings.TrimPrefix(first, marker))
			break
		}
	}
	parts := []string{first}
	for index := start + 1; index < len(lines) && index < start+5; index++ {
		line := strings.TrimSpace(lines[index])
		if line == "" || strings.HasPrefix(line, "Claude Code") || strings.HasPrefix(line, "OpenAI Codex") || strings.HasPrefix(line, "gpt-") {
			break
		}
		parts = append(parts, line)
	}
	text := strings.TrimSpace(strings.Join(parts, " "))
	for _, placeholder := range []string{"Ask Claude to do anything", "Ask Codex to do anything", "Ask OpenCode to do anything"} {
		if text == placeholder || text == "Ask Codex anything" || text == "Ask Claude anything" {
			return "", nil
		}
	}
	if text == "" {
		return "", nil
	}
	return text, fmt.Errorf("target composer contains an unsent draft (%d characters); it was left unchanged", len([]rune(text)))
}

func (actions *nativeYOAgentActions) preview(ctx context.Context, session, text string, submit, returnResult, requireConfirmation bool) (map[string]any, int) {
	return actions.previewWithLocale(ctx, session, text, submit, returnResult, requireConfirmation, "")
}

func (actions *nativeYOAgentActions) previewWithLocale(ctx context.Context, session, text string, submit, returnResult, requireConfirmation bool, locale string) (map[string]any, int) {
	session, text = strings.TrimSpace(session), strings.TrimSpace(text)
	if session == "" || text == "" {
		return map[string]any{"error": "target session and prompt text are required"}, http.StatusBadRequest
	}
	if len([]rune(text)) > 12000 {
		return map[string]any{"error": "prompt text is too long"}, http.StatusBadRequest
	}
	target, err := actions.target(ctx, session)
	if err != nil {
		status := http.StatusConflict
		if strings.Contains(err.Error(), "not present") || strings.Contains(err.Error(), "no detected") {
			status = http.StatusNotFound
		}
		return map[string]any{"ok": false, "session": session, "reason_code": "target-unavailable", "message": err.Error()}, status
	}
	riskLabels := make([]string, 0)
	labels := make([]string, 0, len(nativeYOAgentRiskPatterns))
	for label := range nativeYOAgentRiskPatterns {
		labels = append(labels, label)
	}
	sort.Strings(labels)
	for _, label := range labels {
		if nativeYOAgentRiskPatterns[label].MatchString(text) {
			riskLabels = append(riskLabels, label)
		}
	}
	if len(riskLabels) > 0 {
		requireConfirmation = true
	}
	previewID, err := nativeYOAgentRandomID()
	if err != nil {
		return map[string]any{"error": "could not create an action preview"}, http.StatusInternalServerError
	}
	preview := nativeYOAgentActionPreview{
		ID: previewID, Session: session, Target: target, Text: text, Submit: submit,
		Locale: locale, ReturnResult: returnResult, RequiresConfirm: requireConfirmation,
		RiskLabels: riskLabels, CreatedAt: actions.now(),
	}
	actions.mu.Lock()
	actions.pruneLocked(actions.now())
	actions.previews[previewID] = preview
	actions.mu.Unlock()
	status := "ready"
	statusText := "Ready to send."
	if !submit {
		statusText = "Ready to place in the empty composer without submitting."
	}
	return map[string]any{
		"ok": true, "id": previewID, "type": "send_prompt", "session": session,
		"text": text, "submit": submit, "return_result": returnResult,
		"requires_confirmation": requireConfirmation, "risk_labels": riskLabels,
		"status": status, "status_text": statusText, "accepting_prompt": true,
		"target": map[string]any{
			"session": target.Session, "pane_target": target.PaneTarget, "pane_window": target.PaneWindow,
			"pane_index": target.PaneIndex, "cwd": target.CWD, "agent_kind": target.AgentKind,
			"agent_session_id": target.AgentSessionID, "agent_transcript": target.AgentTranscript,
			"transport": "tmux", "transport_kind": "tmux", "transport_label": "tmux prompt",
		},
		"screen":     map[string]any{"key": "idle", "text": ""},
		"created_ts": preview.CreatedAt.Unix(), "expires_in_seconds": int(nativeYOAgentActionPreviewTTL.Seconds()),
	}, http.StatusOK
}

func nativeYOAgentRandomID() (string, error) {
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		return "", err
	}
	return "ya_" + hex.EncodeToString(bytes[:]), nil
}

func (actions *nativeYOAgentActions) pruneLocked(now time.Time) {
	for id, preview := range actions.previews {
		if now.Sub(preview.CreatedAt) >= nativeYOAgentActionPreviewTTL {
			delete(actions.previews, id)
		}
	}
}

func (actions *nativeYOAgentActions) execute(ctx context.Context, previewID string) (map[string]any, int) {
	previewID = strings.TrimSpace(previewID)
	if previewID == "" {
		return map[string]any{"ok": false, "error": "preview_id is required"}, http.StatusBadRequest
	}
	actions.mu.Lock()
	actions.pruneLocked(actions.now())
	preview, found := actions.previews[previewID]
	actions.mu.Unlock()
	if !found {
		return map[string]any{"ok": false, "preview_id": previewID, "reason_code": "preview-expired", "error": "action preview expired or unknown"}, http.StatusNotFound
	}
	actions.sendMu.Lock()
	defer actions.sendMu.Unlock()
	current, err := actions.target(ctx, preview.Session)
	if err != nil || current.SessionIdentity != preview.Target.SessionIdentity || current.PaneTarget != preview.Target.PaneTarget || current.ProcessInstanceID != preview.Target.ProcessInstanceID || current.AgentKind != preview.Target.AgentKind {
		return map[string]any{"ok": false, "preview_id": previewID, "session": preview.Session, "reason_code": "stale-target", "error": "action target changed; create a fresh preview"}, http.StatusConflict
	}
	transcriptOffset := int64(0)
	if preview.ReturnResult && preview.Submit {
		if current.AgentTranscript == "" {
			return map[string]any{"ok": false, "preview_id": previewID, "session": preview.Session, "reason_code": "result-source-unavailable", "error": "target agent transcript is unavailable; prompt was not sent"}, http.StatusConflict
		}
		info, statErr := os.Stat(current.AgentTranscript)
		if statErr != nil || !info.Mode().IsRegular() {
			return map[string]any{"ok": false, "preview_id": previewID, "session": preview.Session, "reason_code": "result-source-unavailable", "error": "target agent transcript could not be read; prompt was not sent"}, http.StatusConflict
		}
		transcriptOffset = info.Size()
	}
	if err := actions.pastePrompt(ctx, current.PaneTarget, preview.Text); err != nil {
		return map[string]any{"ok": false, "preview_id": previewID, "session": preview.Session, "reason_code": "paste-failed", "error": err.Error()}, http.StatusConflict
	}
	if preview.Submit {
		verified, err := actions.targetWithComposer(ctx, preview.Session, preview.Text)
		if err != nil || verified.SessionIdentity != preview.Target.SessionIdentity || verified.PaneTarget != preview.Target.PaneTarget || verified.ProcessInstanceID != preview.Target.ProcessInstanceID {
			return map[string]any{"ok": false, "preview_id": previewID, "session": preview.Session, "reason_code": "stale-target", "error": "target changed after paste; prompt remains unsubmitted"}, http.StatusConflict
		}
		composer, _ := nativeYOAgentComposerText(verified.VisibleText)
		if composer != strings.Join(strings.Fields(preview.Text), " ") {
			return map[string]any{"ok": false, "preview_id": previewID, "session": preview.Session, "reason_code": "unsubmitted", "error": "prompt was pasted but could not be verified in the composer; it was not submitted"}, http.StatusConflict
		}
		if err := actions.submitPrompt(ctx, verified.PaneTarget); err != nil {
			return map[string]any{"ok": false, "preview_id": previewID, "session": preview.Session, "reason_code": "unsubmitted", "error": "prompt was pasted but Enter could not be sent"}, http.StatusConflict
		}
	}
	actions.mu.Lock()
	delete(actions.previews, previewID)
	actions.mu.Unlock()
	locale, localeErr := actions.server.pageLocales(preview.Locale, "")
	if localeErr != nil {
		slog.Warn("YO!agent send acknowledgement locale unavailable; using English", "locale", preview.Locale, "error", localeErr)
		locale, localeErr = actions.server.pageLocales("en", "")
	}
	answer := fmt.Sprintf("Sent to tmux session `%s` through `tmux prompt`.", preview.Session)
	if localeErr != nil {
		slog.Error("YO!agent English acknowledgement locale unavailable", "error", localeErr)
	} else {
		answer = locale.text("yoagent.action.sent", "session", preview.Session, "transport", "tmux prompt")
	}
	if _, err := actions.server.yoagentState.AppendMessage(ctx, map[string]any{
		"role": "assistant", "content": answer,
	}); err != nil {
		return map[string]any{"ok": false, "session": preview.Session, "sent": preview.Submit, "error": "prompt was sent but the YO!agent conversation could not be saved"}, http.StatusServiceUnavailable
	}
	if err := actions.server.publishNativeYOAgentConversationChanged("action_sent"); err != nil {
		return map[string]any{"ok": false, "session": preview.Session, "sent": preview.Submit, "error": "prompt was sent but the YO!agent conversation update could not be published"}, http.StatusServiceUnavailable
	}
	resultWatch := map[string]any{"started": false}
	if preview.ReturnResult && preview.Submit {
		var watchErr error
		resultWatch, watchErr = actions.startResultWatch(preview, current, transcriptOffset)
		if watchErr != nil {
			slog.Error("YO!agent result watch could not start", "session", preview.Session, "error", watchErr)
			resultWatch = map[string]any{"started": false, "error": watchErr.Error()}
		}
	}
	conversation, conversationErr := actions.server.yoagentConversationPayloadContext(ctx)
	if conversationErr != nil {
		return map[string]any{"ok": false, "session": preview.Session, "sent": preview.Submit, "error": "conversation state could not be loaded after send"}, http.StatusServiceUnavailable
	}
	return map[string]any{"ok": true, "preview_id": previewID, "session": preview.Session, "sent": preview.Submit, "answer": answer, "return_result": preview.ReturnResult, "result_watch": resultWatch, "transport": "tmux", "conversation": conversation}, http.StatusOK
}

func (actions *nativeYOAgentActions) startResultWatch(preview nativeYOAgentActionPreview, target nativeYOAgentActionTarget, offset int64) (map[string]any, error) {
	id, err := nativeYOAgentRandomID()
	if err != nil {
		return nil, fmt.Errorf("create result wait identity: %w", err)
	}
	ctx, cancel := context.WithCancel(actions.ctx)
	wait := nativeYOAgentActionWait{
		ID: "yar_" + strings.TrimPrefix(id, "ya_"), Session: preview.Session, Locale: preview.Locale,
		Transcript: target.AgentTranscript, StartedAt: actions.now(), Target: target, Offset: offset, Cancel: cancel,
	}
	actions.mu.Lock()
	if actions.closed {
		actions.mu.Unlock()
		cancel()
		return nil, errors.New("YO!agent result watcher is shutting down")
	}
	actions.waits[wait.ID] = wait
	actions.watchWG.Add(1)
	actions.mu.Unlock()
	go func() {
		defer actions.watchWG.Done()
		actions.watchResult(ctx, wait)
	}()
	return map[string]any{"id": wait.ID, "started": true, "wait_seconds": int(nativeYOAgentResultWaitTimeout.Seconds())}, nil
}

const nativeYOAgentResultWaitTimeout = 180 * time.Second

func (actions *nativeYOAgentActions) watchResult(ctx context.Context, wait nativeYOAgentActionWait) {
	defer func() {
		actions.mu.Lock()
		if current, exists := actions.waits[wait.ID]; exists && current.StartedAt.Equal(wait.StartedAt) {
			delete(actions.waits, wait.ID)
		}
		actions.mu.Unlock()
		if err := actions.server.publishNativeYOAgentConversationChanged("wait_finished"); err != nil && !errors.Is(err, context.Canceled) {
			slog.Error("YO!agent result wait completion publication failed", "wait_id", wait.ID, "error", err)
		}
	}()
	deadline := time.NewTimer(nativeYOAgentResultWaitTimeout)
	defer deadline.Stop()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	var pending []byte
	offset := wait.Offset
	lastText := ""
	lastChange := actions.now()
	sawWork := false
	for {
		items, nextOffset, nextPending, err := readTranscriptItemsSinceWithPending(wait.Transcript, offset, pending)
		if err != nil {
			if !errors.Is(err, os.ErrNotExist) {
				slog.Warn("YO!agent result transcript read failed", "wait_id", wait.ID, "error", err)
			}
		} else {
			offset, pending = nextOffset, nextPending
			var pieces []string
			for _, item := range items {
				if nativeAgentStringSetting(item, "role", "") != "assistant" {
					continue
				}
				if text := strings.TrimSpace(nativeAgentStringSetting(item, "text", "")); text != "" {
					pieces = append(pieces, text)
				}
			}
			if len(pieces) > 0 {
				lastText = strings.TrimSpace(strings.Join(pieces, "\n\n"))
				if len([]rune(lastText)) > 6000 {
					lastText = string([]rune(lastText)[len([]rune(lastText))-6000:])
				}
				lastChange = actions.now()
			}
		}
		state, identityOK, statusErr := actions.resultTargetState(ctx, wait.Target)
		if statusErr != nil {
			slog.Warn("YO!agent result target observation failed", "wait_id", wait.ID, "error", statusErr)
		}
		if !identityOK {
			actions.completeWait(ctx, wait, lastText, true)
			return
		}
		if state == stats.AgentStatusRun {
			sawWork = true
		}
		if lastText != "" && state == stats.AgentStatusIdle && (sawWork || actions.now().Sub(wait.StartedAt) >= 5*time.Second) && actions.now().Sub(lastChange) >= 2*time.Second {
			actions.completeWait(ctx, wait, lastText, false)
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-deadline.C:
			actions.completeWait(ctx, wait, lastText, true)
			return
		case <-ticker.C:
		}
	}
}

func (actions *nativeYOAgentActions) resultTargetState(ctx context.Context, target nativeYOAgentActionTarget) (stats.AgentStatus, bool, error) {
	server := actions.server
	if server == nil || server.agentProcessSnapshot == nil || server.agentStatusClassifier == nil {
		return stats.AgentStatusUnavailable, false, errors.New("agent identity and status owners are unavailable")
	}
	checkCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	process, err := server.agentProcessSnapshot(checkCtx, target.AgentPID)
	if err != nil {
		return stats.AgentStatusUnavailable, true, fmt.Errorf("read agent process identity: %w", err)
	}
	if !process.Alive || process.StartedIdentity != target.ProcessStarted || process.Command != target.AgentCommand || process.Executable != target.AgentExecutable {
		return stats.AgentStatusUnavailable, false, nil
	}
	classified := server.agentStatusClassifier(checkCtx, stats.AgentStatusRequest{
		Kind: target.AgentKind, PID: target.AgentPID, Command: target.AgentCommand, Executable: target.AgentExecutable,
		PaneTarget: target.PaneTarget, Directory: target.CWD,
	})
	return classified.Status, true, nil
}

func (actions *nativeYOAgentActions) recordResult(ctx context.Context, wait nativeYOAgentActionWait, text string, timedOut bool) {
	locale, err := actions.server.pageLocales(wait.Locale, "")
	if err != nil {
		slog.Warn("YO!agent result locale unavailable; using English", "locale", wait.Locale, "error", err)
		locale, err = actions.server.pageLocales("en", "")
	}
	if err != nil {
		slog.Error("YO!agent result English locale unavailable", "error", err)
	}
	source, result, partial, timeout := "tmux session `"+wait.Session+"`", "Result", "Partial result", "I sent the request to {source}, but I did not see a result before the wait timed out."
	if err == nil {
		source = locale.text("common.tmuxSession", "label", "`"+wait.Session+"`")
		result = locale.text("common.result")
		partial = locale.text("yoagent.result.partialHeading")
		timeout = locale.text("yoagent.result.timedOut", "source", source)
	}
	content := fmt.Sprintf("%s from %s:\n\n%s", result, source, strings.TrimSpace(text))
	if timedOut && strings.TrimSpace(text) == "" {
		content = strings.ReplaceAll(timeout, "{source}", source)
	} else if timedOut {
		content = fmt.Sprintf("%s from %s:\n\n%s", partial, source, strings.TrimSpace(text))
	}
	if _, err := actions.server.yoagentState.AppendMessage(ctx, map[string]any{
		"role": "assistant", "content": content, "kind": "agent_result", "session": wait.Session,
	}); err != nil {
		slog.Error("YO!agent result could not be persisted", "wait_id", wait.ID, "error", err)
		return
	}
	if err := actions.server.publishNativeYOAgentConversationChanged("yoagent_result"); err != nil {
		slog.Error("YO!agent result publication failed", "wait_id", wait.ID, "error", err)
	}
}

func (actions *nativeYOAgentActions) paste(ctx context.Context, pane, text string) error {
	_, err := actions.server.pasteTerminalText(ctx, pane, text, nil)
	return err
}

// Bracketed paste keeps a client's burst detector from treating the following
// Return as pasted text. The admission check runs after preparing the buffer.
func (s *server) pasteTerminalText(ctx context.Context, pane, text string, beforePaste func() (bool, error)) (sent bool, err error) {
	buffer, err := nativeYOAgentRandomID()
	if err != nil {
		return false, fmt.Errorf("create private tmux buffer identity: %w", err)
	}
	load := s.tmuxCommandContext(ctx, "load-buffer", "-b", buffer, "-")
	load.Stdin = strings.NewReader(text)
	if output, err := load.CombinedOutput(); err != nil {
		return false, fmt.Errorf("load prompt into private tmux buffer: %w: %s", err, strings.TrimSpace(string(output)))
	}
	defer func() {
		cleanupContext, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
		defer cancel()
		cleanupErr := s.tmuxCommandContext(cleanupContext, "delete-buffer", "-b", buffer).Run()
		err = errors.Join(err, wrapNativeYOAgentTmuxError("delete private tmux prompt buffer", cleanupErr, nil))
	}()
	if beforePaste != nil {
		if allowed, guardErr := beforePaste(); guardErr != nil || !allowed {
			return false, guardErr
		}
	}
	output, pasteErr := s.tmuxCommandContext(ctx, "paste-buffer", "-p", "-r", "-b", buffer, "-t", pane).CombinedOutput()
	return pasteErr == nil, wrapNativeYOAgentTmuxError("paste prompt into target composer", pasteErr, output)
}

func (actions *nativeYOAgentActions) pastePrompt(ctx context.Context, pane, text string) error {
	if actions.pasteFn != nil {
		return actions.pasteFn(ctx, pane, text)
	}
	return actions.paste(ctx, pane, text)
}

func (actions *nativeYOAgentActions) submitPrompt(ctx context.Context, pane string) error {
	if actions.submitFn != nil {
		return actions.submitFn(ctx, pane)
	}
	return actions.server.tmuxCommandContext(ctx, "send-keys", "-t", pane, "Enter").Run()
}

func wrapNativeYOAgentTmuxError(operation string, err error, output []byte) error {
	if err == nil {
		return nil
	}
	detail := strings.TrimSpace(string(output))
	if detail == "" {
		return fmt.Errorf("%s: %w", operation, err)
	}
	return fmt.Errorf("%s: %w: %s", operation, err, detail)
}

func (s *server) yoagentActionPreview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if s.agentActions == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "agent_actions_unavailable"})
		return
	}
	var body struct {
		Session              string `json:"session"`
		Target               string `json:"target"`
		Text                 string `json:"text"`
		Prompt               string `json:"prompt"`
		Submit               *bool  `json:"submit"`
		ReturnResult         bool   `json:"return_result"`
		RequiresConfirmation bool   `json:"requires_confirmation"`
	}
	if err := decodeJSONBody(r.Body, 32*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	if body.Session == "" {
		body.Session = body.Target
	}
	if body.Text == "" {
		body.Text = body.Prompt
	}
	preference, err := s.requestLocalePreference(r)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "locale_unavailable"})
		return
	}
	localeState, err := s.pageLocales(preference, r.Header.Get("Accept-Language"))
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "locale_unavailable"})
		return
	}
	submit := body.Submit == nil || *body.Submit
	payload, status := s.agentActions.previewWithLocale(r.Context(), body.Session, body.Text, submit, body.ReturnResult, body.RequiresConfirmation, localeState.locale)
	jsonResponse(w, status, payload)
}

func (s *server) yoagentActionExecute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if s.agentActions == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "agent_actions_unavailable"})
		return
	}
	var body struct {
		PreviewID    string `json:"preview_id"`
		ReturnResult bool   `json:"return_result"`
	}
	if err := decodeJSONBody(r.Body, 16*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	payload, status := s.agentActions.execute(r.Context(), body.PreviewID)
	jsonResponse(w, status, payload)
}

func (s *server) yoagentWaitClear(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	id := strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/yoagent/waits/"), "/clear"))
	if id == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "wait_id_required"})
		return
	}
	if s.agentActions == nil || !s.agentActions.clearWait(id) {
		conversation, err := s.yoagentConversationPayload(r)
		if err != nil {
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "conversation_unavailable"})
			return
		}
		jsonResponse(w, http.StatusNotFound, map[string]any{"ok": false, "id": id, "error": "wait_not_found", "conversation": conversation})
		return
	}
	if err := s.publishNativeYOAgentConversationChanged("wait_cleared"); err != nil {
		slog.Error("YO!agent wait clear event publication failed", "wait_id", id, "error", err)
	}
	conversation, err := s.yoagentConversationPayload(r)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "conversation_unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "id": id, "conversation": conversation})
}

func (s *server) yoagentPrewarm(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if s.yoagent == nil || s.yoagentState == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "agent_unavailable"})
		return
	}
	var body struct {
		Locale  string `json:"locale"`
		Visible bool   `json:"visible"`
	}
	if err := decodeJSONBody(r.Body, 8*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	conversation, err := s.yoagentConversationPayload(r)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "conversation_unavailable"})
		return
	}
	messages, _, err := s.yoagentState.LoadMessages(r.Context(), 1)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "conversation_unavailable"})
		return
	}
	if len(messages) > 0 || !body.Visible {
		jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "started": false, "visible": body.Visible, "reason": "native CLI providers start on demand", "conversation": conversation})
		return
	}
	localeState, err := s.pageLocales(body.Locale, r.Header.Get("Accept-Language"))
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "locale_unavailable", "conversation": conversation})
		return
	}
	requestID, streamID := nativeAgentRequestID("startup-"+fmt.Sprint(time.Now().UnixNano())), nativeAgentRequestID("startup-stream-"+fmt.Sprint(time.Now().UnixNano()))
	answer, status, err := s.yoagent.AnswerWithSessions(r.Context(), "The user opened YO!agent. Give a concise first response with active work, anything needing attention, and one concrete next step.", localeState.resolve(body.Locale, r.Header.Get("Accept-Language")), requestID, streamID, "admin", nil)
	if err != nil {
		var protocol *chat.Error
		if errors.As(err, &protocol) {
			writeChatError(w, err)
		} else {
			slog.Error("YO!agent startup response failed", "error", err)
			jsonResponse(w, http.StatusBadGateway, map[string]any{"ok": false, "error": "startup_response_failed", "conversation": conversation})
		}
		return
	}
	conversation, err = s.yoagentConversationPayload(r)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "conversation_unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "started": true, "visible": true, "answer": answer, "backend": status["backend"], "backend_used": status["backend"], "conversation": conversation, "stream_id": streamID})
}
