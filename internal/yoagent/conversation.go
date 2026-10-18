// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package yoagent owns the native assistant's durable conversation protocol.
package yoagent

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/decimal"
	"github.com/keivenchang/yomux/internal/locales"
)

const (
	ConversationLimit   = 500
	ContentLimit        = 20000
	DetailsLimit        = 4000
	AuxiliaryLineLimit  = 4000
	AuxiliaryTotalLimit = 120000
	StreamTextLimit     = 8000
	StreamItemsLimit    = 1000
	StreamTotalLimit    = 120000
	DetailRowsLimit     = 24
	ActionsLimit        = 8
)

// ManagedBackends is the single Go owner of YO!agent's resumable CLI backends.
func ManagedBackends() []string { return []string{"codex", "claude"} }

type Message = map[string]any

type MessageOptions struct{ Role, Content, CreatedAt *string }

type CLIState struct {
	SessionID         string  `json:"session_id"`
	ActivitySignature string  `json:"activity_signature"`
	UpdatedTS         float64 `json:"updated_ts"`
	UpdatedMonotonic  float64 `json:"updated_monotonic"`
}

func object(value any) map[string]any { result, _ := value.(map[string]any); return result }
func array(value any) []any {
	switch value := value.(type) {
	case []any:
		return value
	case []string:
		result := make([]any, len(value))
		for index, item := range value {
			result[index] = item
		}
		return result
	case []map[string]any:
		result := make([]any, len(value))
		for index, item := range value {
			result[index] = item
		}
		return result
	default:
		return nil
	}
}
func truthy(value any) bool {
	switch value := value.(type) {
	case nil:
		return false
	case string:
		return value != ""
	case bool:
		return value
	case float64:
		return value != 0
	case float32:
		return value != 0
	case int:
		return value != 0
	case int64:
		return value != 0
	case uint64:
		return value != 0
	case json.Number:
		// JSON exponent forms are binary floats in existing records. Underflow
		// is zero; overflow and arbitrarily large integer values remain true.
		number, err := value.Float64()
		return (err == nil || errors.Is(err, strconv.ErrRange)) && number != 0
	case []string:
		return len(value) > 0
	case []map[string]any:
		return len(value) > 0
	case []any:
		return len(value) > 0
	case map[string]any:
		return len(value) > 0
	default:
		return true
	}
}

// Text fields in old records may contain JSON scalars. Keep their established
// display conversion while accepting ordinary string fields without a copy.
func text(value any) string {
	if !truthy(value) {
		return ""
	}
	switch value := value.(type) {
	case string:
		return value
	case bool:
		if value {
			return "True"
		}
		return "False"
	case json.Number:
		return value.String()
	default:
		return fmt.Sprint(value)
	}
}
func trim(value any) string { return locales.TrimSpace(text(value)) }
func truncate(value string, limit int) string {
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	return string(runes[:limit]) + "\n[truncated]"
}
func number(value any) float64 {
	switch value := value.(type) {
	case float64:
		return value
	case int:
		return float64(value)
	case int8:
		return float64(value)
	case int16:
		return float64(value)
	case int32:
		return float64(value)
	case int64:
		return float64(value)
	case uint:
		return float64(value)
	case uint8:
		return float64(value)
	case uint16:
		return float64(value)
	case uint32:
		return float64(value)
	case uint64:
		return float64(value)
	case json.Number:
		n, err := value.Float64()
		if err == nil || errors.Is(err, strconv.ErrRange) {
			return n
		}
	case string:
		n, err := strconv.ParseFloat(locales.TrimSpace(value), 64)
		if err == nil || errors.Is(err, strconv.ErrRange) {
			return n
		}
	case bool:
		if value {
			return 1
		}
	}
	return 0
}
func clone(value any) (any, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	var copy any
	if err := decoder.Decode(&copy); err != nil {
		return nil, err
	}
	return copy, nil
}

var credentialName = regexp.MustCompile(`(?i)token|secret|password|ap[i\x{0130}\x{0131}][_-]?key`)
var actionCredentialAssignment = regexp.MustCompile(`(?i)\b(token|secret|password|api[_-]?key)\s*=\s*\S+`)

func redactedActionText(value string, limit int) string {
	value = actionCredentialAssignment.ReplaceAllString(value, "$1=<redacted>")
	if limit < 0 {
		return value
	}
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	return string(runes[:limit]) + "..."
}

func PublicJob(value map[string]any) (map[string]any, error) {
	copyValue, err := clone(value)
	if err != nil {
		return nil, fmt.Errorf("copy YO!agent job for response: %w", err)
	}
	job := object(copyValue)
	if prompt := text(job["prompt"]); prompt != "" {
		job["prompt_preview"] = redactedActionText(prompt, 160)
		job["prompt"] = redactedActionText(prompt, 240)
		job["public_text"] = redactedActionText(prompt, 240)
	}
	if action := object(job["action"]); action != nil {
		if raw, ok := action["text"].(string); ok {
			action["text_preview"] = redactedActionText(raw, 160)
			action["text"] = redactedActionText(raw, 240)
		}
		job["action"] = action
	}
	return job, nil
}

func whitespace(r rune) bool { return locales.TrimSpace(string(r)) == "" }

// Redact only assignment values and preserve the original key spelling. The
// Unicode boundary and whitespace rules match existing saved conversations;
// Go regexp's ASCII-only word boundary would redact inside unrelated words.
func RedactAuxiliary(value string) string {
	var out strings.Builder
	end := 0
	for _, match := range credentialName.FindAllStringIndex(value, -1) {
		if match[0] < end {
			continue
		}
		if match[0] > 0 {
			r, _ := utf8.DecodeLastRuneInString(value[:match[0]])
			if unicode.IsLetter(r) || unicode.IsNumber(r) || r == '_' {
				continue
			}
		}
		cursor := match[1]
		for cursor < len(value) {
			r, size := utf8.DecodeRuneInString(value[cursor:])
			if !whitespace(r) {
				break
			}
			cursor += size
		}
		if cursor >= len(value) || value[cursor] != '=' {
			continue
		}
		cursor++
		for cursor < len(value) {
			r, size := utf8.DecodeRuneInString(value[cursor:])
			if !whitespace(r) {
				break
			}
			cursor += size
		}
		start := cursor
		for cursor < len(value) {
			r, size := utf8.DecodeRuneInString(value[cursor:])
			if whitespace(r) {
				break
			}
			cursor += size
		}
		if cursor == start {
			continue
		}
		out.WriteString(value[end:match[0]])
		out.WriteString(value[match[0]:match[1]])
		out.WriteString("=<redacted>")
		end = cursor
	}
	out.WriteString(value[end:])
	return out.String()
}

func boundedTextTail(lines []string, limit int, truncated bool) ([]string, bool) {
	selected := []string{}
	total := 0
	for index := len(lines) - 1; index >= 0; index-- {
		line := lines[index]
		separator := 0
		if len(selected) > 0 {
			separator = 1
		}
		projected := total + utf8.RuneCountInString(line) + separator
		if len(selected) > 0 && projected > limit {
			truncated = true
			break
		}
		if len(selected) == 0 && projected > limit {
			selected = append(selected, truncate(line, limit))
			truncated = true
			break
		}
		selected = append(selected, line)
		total = projected
	}
	slices.Reverse(selected)
	return selected, truncated || len(selected) < len(lines)
}

func BoundedAuxiliaryLines(value any) ([]string, bool) {
	lines := []string{}
	truncated := false
	for _, item := range array(value) {
		raw := trim(item)
		if raw == "" {
			continue
		}
		redacted := RedactAuxiliary(raw)
		bounded := truncate(redacted, AuxiliaryLineLimit)
		truncated = truncated || bounded != redacted
		if locales.TrimSpace(bounded) != "" {
			lines = append(lines, bounded)
		}
	}
	return boundedTextTail(lines, AuxiliaryTotalLimit, truncated)
}

func descriptor(key, fallback string, params any) (map[string]any, error) {
	clean := map[string]any{}
	if object(params) != nil {
		copy, err := clone(params)
		if err != nil {
			return nil, err
		}
		clean = object(copy)
	}
	return map[string]any{"key": key, "fallback": fallback, "params": clean}, nil
}

func detailRows(value any) ([]any, error) {
	result := []any{}
	for _, item := range array(value)[:min(len(array(value)), DetailRowsLimit)] {
		row := object(item)
		if row == nil {
			continue
		}
		key, fallback := truncate(trim(row["key"]), 160), truncate(RedactAuxiliary(trim(row["fallback"])), DetailsLimit)
		if key == "" && fallback == "" {
			continue
		}
		clean, err := descriptor(key, fallback, row["params"])
		if err != nil {
			return nil, err
		}
		result = append(result, clean)
	}
	return result, nil
}

func BoundedStreamItems(value any) ([]map[string]any, bool, error) {
	result := []map[string]any{}
	truncated := false
	for _, item := range array(value) {
		raw := object(item)
		if raw == nil {
			continue
		}
		kind := strings.ToLower(trim(raw["kind"]))
		if !slices.Contains([]string{"assistant", "thinking", "tool", "diagnostic"}, kind) {
			continue
		}
		original := RedactAuxiliary(text(raw["text"]))
		bounded := truncate(original, StreamTextLimit)
		truncated = truncated || bounded != original
		label := object(raw["label"])
		if label == nil {
			key := raw["labelKey"]
			if !truthy(key) {
				key = raw["label_key"]
			}
			params, exists := raw["labelParams"]
			if !exists {
				params = raw["label_params"]
			}
			label = map[string]any{"key": key, "params": params, "fallback": raw["fallback"]}
		}
		key, fallback := truncate(trim(label["key"]), 160), truncate(RedactAuxiliary(trim(label["fallback"])), 240)
		clean := map[string]any{"kind": kind, "text": bounded}
		for _, field := range []string{"eventKind", "toolName", "command", "path"} {
			value := truncate(RedactAuxiliary(trim(raw[field])), StreamTextLimit)
			if value != "" {
				clean[field] = value
			}
		}
		switch token := raw["tokenCount"].(type) {
		case float64, json.Number, bool:
			if n := number(token); n > 0 {
				if math.IsInf(n, 0) {
					return nil, false, errors.New("non-finite stream token count")
				}
				clean["tokenCount"] = math.Trunc(n)
			}
		}
		if key != "" || fallback != "" {
			canonical, err := descriptor(key, fallback, label["params"])
			if err != nil {
				return nil, false, err
			}
			clean["label"], clean["labelKey"], clean["labelParams"], clean["fallback"] = canonical, canonical["key"], canonical["params"], canonical["fallback"]
		}
		if locales.TrimSpace(bounded) != "" || key != "" || fallback != "" {
			result = append(result, clean)
		}
	}
	if len(result) > StreamItemsLimit {
		result = result[len(result)-StreamItemsLimit:]
		truncated = true
	}
	selected := []map[string]any{}
	total := 0
	for index := len(result) - 1; index >= 0; index-- {
		item := result[index]
		value := item["text"].(string)
		separator := 0
		if len(selected) > 0 {
			separator = 1
		}
		projected := total + utf8.RuneCountInString(value) + separator
		if len(selected) > 0 && projected > StreamTotalLimit {
			truncated = true
			break
		}
		if len(selected) == 0 && projected > StreamTotalLimit {
			item["text"] = truncate(value, StreamTotalLimit)
			selected = append(selected, item)
			truncated = true
			break
		}
		selected = append(selected, item)
		total = projected
	}
	slices.Reverse(selected)
	return selected, truncated || len(selected) < len(result), nil
}

func SanitizeMessage(value any, options MessageOptions, now time.Time) (Message, error) {
	raw := object(value)
	role := strings.ToLower(trim(raw["role"]))
	if options.Role != nil && *options.Role != "" {
		role = strings.ToLower(locales.TrimSpace(*options.Role))
	}
	if role != "user" && role != "assistant" {
		return nil, nil
	}
	content := text(raw["content"])
	if options.Content != nil {
		content = *options.Content
	}
	content = truncate(locales.TrimSpace(content), ContentLimit)
	if content == "" {
		return nil, nil
	}
	created := raw["createdAt"]
	if !truthy(created) {
		created = raw["created_at"]
	}
	if options.CreatedAt != nil && *options.CreatedAt != "" {
		created = *options.CreatedAt
	}
	if !truthy(created) {
		format := "2006-01-02T15:04:05"
		if now.Nanosecond()/1000 != 0 {
			format += ".000000"
		}
		created = now.UTC().Format(format) + "+00:00"
	}
	message := Message{"role": role, "content": content, "createdAt": truncate(trim(created), 80)}
	if role != "assistant" {
		return message, nil
	}
	if strings.ToLower(trim(raw["kind"])) == "agent_result" {
		message["kind"] = "agent_result"
	}
	if session := trim(raw["session"]); session != "" {
		message["session"] = truncate(session, 120)
	}
	actions := []any{}
	for _, item := range array(raw["actions"])[:min(len(array(raw["actions"])), ActionsLimit)] {
		if object(item) == nil {
			continue
		}
		copy, err := clone(item)
		if err != nil {
			return nil, err
		}
		actions = append(actions, copy)
	}
	if len(actions) > 0 {
		message["actions"] = actions
	}
	if details := truncate(trim(raw["details"]), DetailsLimit); details != "" {
		message["details"] = details
	}
	rows, err := detailRows(raw["detailRows"])
	if err != nil {
		return nil, err
	}
	if len(rows) > 0 {
		message["detailRows"] = rows
	}
	response, exists := raw["responseMs"]
	if !exists {
		response = raw["response_ms"]
	}
	if n := number(response); n > 0 {
		if math.IsNaN(n) || math.IsInf(n, 0) {
			return nil, fmt.Errorf("invalid response duration")
		}
		rounded, err := decimal.Round(n, 3)
		if err != nil {
			return nil, fmt.Errorf("round response duration: %w", err)
		}
		message["responseMs"] = rounded
	}
	lines, truncated := BoundedAuxiliaryLines(raw["auxiliaryLines"])
	if len(lines) > 0 {
		message["auxiliaryLines"] = lines
		auxiliary := strings.Join(lines, "\n")
		bounded := truncate(auxiliary, AuxiliaryTotalLimit)
		message["auxiliaryText"] = bounded
		truncated = truncated || bounded != auxiliary
		preview := raw["auxiliaryPreview"]
		if !truthy(preview) {
			preview = lines[len(lines)-1]
		}
		original := RedactAuxiliary(trim(preview))
		bounded = truncate(original, AuxiliaryLineLimit)
		truncated = truncated || bounded != original
		if bounded != "" {
			message["auxiliaryPreview"] = bounded
		}
	}
	stream, streamTruncated, err := BoundedStreamItems(raw["streamItems"])
	if err != nil {
		return nil, err
	}
	if len(stream) > 0 {
		message["streamItems"] = stream
	}
	if len(lines) > 0 || len(stream) > 0 {
		if truthy(raw["auxiliaryDone"]) {
			message["auxiliaryDone"] = true
		}
		if truthy(raw["auxiliaryTruncated"]) || truncated || streamTruncated {
			message["auxiliaryTruncated"] = true
		}
	}
	return message, nil
}

// The admitted backends come from the caller's shared client registry; this
// codec must not introduce a second list of installed or supported clients.
var ErrAmbiguousBackend = errors.New("ambiguous normalized CLI backend")
var ErrInvalidTimestamp = errors.New("non-finite CLI timestamp")

func SanitizeCLISessions(value any, monotonicNow float64, backends []string) (map[string]CLIState, error) {
	result := map[string]CLIState{}
	if math.IsNaN(monotonicNow) || math.IsInf(monotonicNow, 0) {
		return nil, ErrInvalidTimestamp
	}
	seen := map[string]bool{}
	for backend, item := range object(value) {
		key := strings.ToLower(locales.TrimSpace(backend))
		state := object(item)
		if !slices.Contains(backends, key) || state == nil {
			continue
		}
		if seen[key] {
			return nil, fmt.Errorf("%w: %s", ErrAmbiguousBackend, key)
		}
		seen[key] = true
		session := trim(state["session_id"])
		if session == "" {
			continue
		}
		updated := number(state["updated_ts"])
		if math.IsInf(updated, 0) {
			return nil, fmt.Errorf("%w: %s", ErrInvalidTimestamp, key)
		}
		if math.IsNaN(updated) || updated < 0 {
			updated = 0
		}
		result[key] = CLIState{SessionID: truncate(session, 400), ActivitySignature: truncate(text(state["activity_signature"]), 2000), UpdatedTS: updated, UpdatedMonotonic: monotonicNow}
	}
	return result, nil
}
