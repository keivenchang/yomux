// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var jobRiskChecks = []struct {
	label   string
	pattern *regexp.Regexp
}{
	{"secret-like-text", regexp.MustCompile(`(?i)\b(?:token|secret|password|api[_-]?key)\s*=\s*\S+`)},
	{"credential-path", regexp.MustCompile(`(?i)(~?/\.config/gitlab-token|~?/\.cache/huggingface/token|~?/\.docker/config\.json|~?/\.ngc/config)`)},
	{"recursive-delete", regexp.MustCompile(`(?i)\brm\s+-[^\n]*r[^\n]*f\b`)},
	{"hard-reset", regexp.MustCompile(`(?i)\bgit\s+reset\s+--hard\b`)},
	{"broad-pkill", regexp.MustCompile(`(?i)\bpkill\s+-f\b`)},
	{"recursive-permission-change", regexp.MustCompile(`(?i)\b(?:chmod|chown)\s+-R\b`)},
	{"mfa-ssh", regexp.MustCompile(`(?i)\bssh\s+\S+`)},
}

const (
	jobActionTextLimit = 4000
	jobQuietDefault    = 3.0
	jobRosterQuiet     = 10.0
	jobTimeoutDefault  = 120.0
)

// BuildJob validates the browser job contract and returns its durable representation.
// Explicit confirmations and risk-classified prompts remain pending until an operator confirms them.
func BuildJob(payload map[string]any, sessions []string, actor string, now time.Time) (map[string]any, error) {
	if payload == nil {
		return nil, errors.New("YO!agent job payload is required")
	}
	targetPayload := object(payload["target"])
	actionPayload := object(payload["action"])
	jobType := strings.TrimSpace(text(payload["type"]))
	if jobType == "" {
		jobType = strings.TrimSpace(text(payload["kind"]))
	}
	if jobType == "" {
		jobType = "notify_session_idle"
	}
	session := firstNonEmpty(text(payload["session"]), text(targetPayload["session"]), text(actionPayload["session"]))
	known := make(map[string]bool, len(sessions))
	for _, item := range sessions {
		known[item] = true
	}
	target := map[string]any{}
	predicate := map[string]any{}
	action := map[string]any{}
	maxRuns := 0
	switch jobType {
	case "notify_when_idle", "notify_session", "notify_session_idle", "notify_session_needs_input", "notify_needs_input", "notify_session_blocked", "notify_blocked", "notify_session_done_after_working", "notify_done_after_working":
		messageKey := "yoagent.job.notification.idle"
		predicateType := "session_idle"
		switch jobType {
		case "notify_session_needs_input", "notify_needs_input":
			jobType, predicateType, messageKey = "notify_session_needs_input", "session_needs_input", "yoagent.job.notification.needsInput"
		case "notify_session_blocked", "notify_blocked":
			jobType, predicateType, messageKey = "notify_session_blocked", "session_blocked", "yoagent.job.notification.blocked"
		case "notify_session_done_after_working", "notify_done_after_working":
			jobType, predicateType, messageKey = "notify_session_done_after_working", "session_done_after_working", "yoagent.job.notification.doneAfterWorking"
		default:
			jobType = "notify_session_idle"
		}
		if err := requireKnownSession(session, known); err != nil {
			return nil, err
		}
		target["session"] = session
		predicate = map[string]any{"type": predicateType, "quiet_seconds": numberOr(payload["quiet_seconds"], jobQuietDefault)}
		action = notificationAction(messageKey, map[string]any{"session": session}, payload["message"])
	case "notify_all_idle", "all_idle_summary":
		roster := uniqueStrings(array(payload["roster"]))
		if len(roster) == 0 {
			roster = append([]string(nil), sessions...)
		}
		if err := requireKnownRoster(roster, known); err != nil {
			return nil, err
		}
		jobType = "notify_all_idle"
		target["roster"] = roster
		predicate = map[string]any{"type": "all_idle", "quiet_seconds": numberOr(payload["quiet_seconds"], jobQuietDefault)}
		action = notificationAction("yoagent.job.notification.allIdle", nil, payload["message"])
	case "wait_then_send", "wait_then_run", "loop_send", "repeat_send":
		if err := requireKnownSession(session, known); err != nil {
			return nil, err
		}
		prompt := truncateJobText(firstNonEmpty(text(payload["text"]), text(actionPayload["text"])))
		if prompt == "" {
			return nil, errors.New("YO!agent job prompt is required")
		}
		riskLabels := jobActionRiskLabels(prompt)
		if len(riskLabels) > 0 {
			prompt = RedactAuxiliary(prompt)
		}
		if jobType == "wait_then_run" {
			jobType = "wait_then_send"
		}
		if jobType == "repeat_send" {
			jobType = "loop_send"
		}
		target["session"] = session
		submit := actionPayload["submit"] != false
		returnResult := payload["return_result"] != false
		if value, exists := actionPayload["return_result"]; exists {
			returnResult = value == true
		}
		action = map[string]any{"type": "send_prompt", "session": session, "text": prompt, "submit": submit, "return_result": returnResult}
		if len(riskLabels) > 0 {
			action["risk_labels"] = riskLabels
		}
		if payload["requires_confirmation"] == true || payload["confirm_required"] == true || actionPayload["requires_confirmation"] == true || len(riskLabels) > 0 {
			action["requires_confirmation"] = true
		}
		if jobType == "loop_send" {
			interval := numberOr(payload["interval_seconds"], 30)
			maxRuns = 10
			if value, exists := payload["max_runs"]; exists {
				maxRuns = integer(value)
			}
			if interval < 5 || interval > 3600 || maxRuns < 1 || maxRuns > 100 {
				return nil, errors.New("YO!agent loop interval must be 5-3600 seconds and max runs must be 1-100")
			}
			predicate = map[string]any{"type": "interval", "interval_seconds": interval, "quiet_seconds": 0.0}
			returnResult = false
			action["return_result"] = false
		} else {
			predicate = map[string]any{"type": "session_idle", "quiet_seconds": numberOr(payload["quiet_seconds"], jobQuietDefault)}
		}
	case "wait_roster_then_send":
		roster := uniqueStrings(array(payload["roster"]))
		if len(roster) == 0 {
			roster = uniqueStrings(array(targetPayload["roster"]))
		}
		if err := requireKnownRoster(roster, known); err != nil {
			return nil, err
		}
		destination := firstNonEmpty(text(actionPayload["session"]), text(payload["destination"]))
		if err := requireKnownSession(destination, known); err != nil {
			return nil, err
		}
		prompt := truncateJobText(firstNonEmpty(text(actionPayload["text"]), text(payload["text"])))
		if prompt == "" {
			return nil, errors.New("YO!agent job prompt is required")
		}
		riskLabels := jobActionRiskLabels(prompt)
		if len(riskLabels) > 0 {
			prompt = RedactAuxiliary(prompt)
		}
		target["roster"] = roster
		predicate = map[string]any{"type": "all_calm", "quiet_seconds": numberOr(payload["quiet_seconds"], jobRosterQuiet)}
		returnResult := payload["return_result"] == true
		if value, exists := actionPayload["return_result"]; exists {
			returnResult = value == true
		}
		action = map[string]any{"type": "send_prompt", "session": destination, "text": prompt, "submit": actionPayload["submit"] != false, "return_result": returnResult}
		if len(riskLabels) > 0 {
			action["risk_labels"] = riskLabels
		}
		if payload["requires_confirmation"] == true || payload["confirm_required"] == true || actionPayload["requires_confirmation"] == true || len(riskLabels) > 0 {
			action["requires_confirmation"] = true
		}
	default:
		return nil, fmt.Errorf("unsupported YO!agent job type %q", jobType)
	}
	if quiet, ok := predicate["quiet_seconds"]; ok {
		seconds := number(quiet)
		if math.IsNaN(seconds) || math.IsInf(seconds, 0) {
			return nil, errors.New("YO!agent job quiet period must be finite")
		}
	}
	now = now.UTC()
	created := float64(now.UnixNano()) / 1e9
	timeoutMinutes := numberOr(payload["timeout_minutes"], jobTimeoutDefault)
	if timeoutMinutes < 1 {
		timeoutMinutes = 1
	}
	if timeoutMinutes > 1440 {
		timeoutMinutes = 1440
	}
	id := "yj_" + rand.Text()[:16]
	encodedSignature, err := json.Marshal(map[string]any{"type": jobType, "target": target, "predicate": predicate, "action": action})
	if err != nil {
		return nil, fmt.Errorf("encode YO!agent job signature: %w", err)
	}
	signature := sha256.Sum256(encodedSignature)
	idempotencyKey := firstNonEmpty(text(payload["idempotency_key"]), hex.EncodeToString(signature[:]))
	confirm := payload["confirm_required"] == true || payload["requires_confirmation"] == true || action["requires_confirmation"] == true
	job := map[string]any{
		"id": id, "job_id": id, "type": jobType, "target": target, "predicate": predicate, "action": action,
		"prompt": text(action["text"]), "created_at": now.Format(time.RFC3339Nano), "created_ts": created,
		"created_by": firstNonEmpty(actor, "yoagent"), "locale": firstNonEmpty(text(payload["locale"]), "en"),
		"status": "queued", "last_observed_state": map[string]any{}, "timeout_at": now.Add(time.Duration(timeoutMinutes * float64(time.Minute))).Format(time.RFC3339Nano),
		"timeout_ts": created + timeoutMinutes*60, "confirm_required": confirm, "confirmed_at": now.Format(time.RFC3339Nano),
		"idempotency_key": idempotencyKey, "audit_event_ids": []any{},
	}
	if confirm {
		job["status"] = "pending_confirmation"
		job["confirmed_at"] = ""
	}
	if maxRuns > 0 {
		job["run_count"], job["max_runs"], job["next_run_ts"] = 0, maxRuns, created
	}
	return job, nil
}

func requireKnownSession(session string, known map[string]bool) error {
	if strings.TrimSpace(session) == "" {
		return errors.New("YO!agent target session is required")
	}
	if !known[session] {
		return fmt.Errorf("unknown YO!agent target session %q", session)
	}
	return nil
}

func requireKnownRoster(roster []string, known map[string]bool) error {
	if len(roster) == 0 {
		return errors.New("YO!agent target roster is required")
	}
	for _, session := range roster {
		if !known[session] {
			return fmt.Errorf("unknown YO!agent target session %q", session)
		}
	}
	return nil
}

func notificationAction(key string, params map[string]any, custom any) map[string]any {
	message := strings.TrimSpace(text(custom))
	if message != "" {
		return map[string]any{"type": "notify_user", "message": message}
	}
	fallback := map[string]string{
		"yoagent.job.notification.allIdle":          "all watched tmux sessions are idle",
		"yoagent.job.notification.blocked":          fmt.Sprintf("tmux session `%s` is blocked", text(params["session"])),
		"yoagent.job.notification.doneAfterWorking": fmt.Sprintf("tmux session `%s` finished after working", text(params["session"])),
		"yoagent.job.notification.idle":             fmt.Sprintf("tmux session `%s` is idle", text(params["session"])),
		"yoagent.job.notification.needsInput":       fmt.Sprintf("tmux session `%s` needs input", text(params["session"])),
	}
	return map[string]any{"type": "notify_user", "message": fallback[key], "message_key": key, "message_params": params}
}

func jobActionRiskLabels(value string) []string {
	labels := make([]string, 0, len(jobRiskChecks))
	for _, check := range jobRiskChecks {
		if check.pattern.MatchString(value) {
			labels = append(labels, check.label)
		}
	}
	return labels
}

func truncateJobText(value string) string {
	runes := []rune(value)
	if len(runes) <= jobActionTextLimit {
		return value
	}
	return string(runes[:jobActionTextLimit]) + "\n[truncated]"
}

func uniqueStrings(values []any) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(values))
	for _, item := range values {
		value := strings.TrimSpace(text(item))
		if value != "" && !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	return result
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value = strings.TrimSpace(value); value != "" {
			return value
		}
	}
	return ""
}

func numberOr(value any, fallback float64) float64 {
	if value == nil {
		return fallback
	}
	switch number := value.(type) {
	case string:
		parsed, err := strconv.ParseFloat(strings.TrimSpace(number), 64)
		if err != nil {
			return fallback
		}
		return parsed
	case json.Number:
		parsed, err := number.Float64()
		if err != nil {
			return fallback
		}
		return parsed
	}
	return number(value)
}

func integer(value any) int {
	switch number := value.(type) {
	case int:
		return number
	case int64:
		return int(number)
	case json.Number:
		parsed, err := number.Int64()
		if err == nil && int64(int(parsed)) == parsed {
			return int(parsed)
		}
		parsedFloat, floatErr := number.Float64()
		if floatErr == nil && math.Trunc(parsedFloat) == parsedFloat && parsedFloat >= math.MinInt && parsedFloat <= math.MaxInt {
			return int(parsedFloat)
		}
	case float64:
		if math.Trunc(number) == number {
			return int(number)
		}
	}
	return 0
}
