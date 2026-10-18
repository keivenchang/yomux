// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"slices"
	"strings"
)

type ModelIntent struct {
	Name, Handler, Tag, SessionScope string
	Schema                           map[string]any
	AnyOf, RequireAny, Collective    []string
	MinimumSessions, Priority        int
	ConfirmationRequired             bool
	Defaults                         map[string]any
}

var modelIntentNamePattern = regexp.MustCompile(`^[a-z][a-z0-9-]{1,63}$`)

var modelIntentSchemas = map[string]map[string]any{
	"delegated-prompt": {
		"type": "object", "additionalProperties": false, "required": []any{"session", "prompt", "return_result"},
		"properties": map[string]any{
			"session":       map[string]any{"type": "string", "format": "known-session"},
			"prompt":        map[string]any{"type": "string", "maxLength": 4000},
			"return_result": map[string]any{"type": "boolean"},
		},
	},
	"loop-send": {
		"type": "object", "additionalProperties": false, "required": []any{"session", "prompt", "interval_seconds", "max_runs"},
		"properties": map[string]any{
			"session":          map[string]any{"type": "string", "format": "known-session"},
			"prompt":           map[string]any{"type": "string", "maxLength": 4000},
			"interval_seconds": map[string]any{"type": "number", "minimum": 5.0, "maximum": 3600.0},
			"max_runs":         map[string]any{"type": "integer", "minimum": 1, "maximum": 100},
		},
	},
}

func SelectModelIntent(skills []Skill, question string, sessions []string) *ModelIntent {
	matches := make([]ModelIntent, 0)
	for _, skill := range skills {
		intent, ok := modelIntentFromSkill(skill)
		if ok && modelIntentMatches(intent, question, sessions) {
			matches = append(matches, intent)
		}
	}
	if len(matches) == 0 {
		return nil
	}
	highest := matches[0].Priority
	for _, candidate := range matches[1:] {
		if candidate.Priority > highest {
			highest = candidate.Priority
		}
	}
	var selected *ModelIntent
	for index := range matches {
		if matches[index].Priority != highest {
			continue
		}
		if selected != nil {
			return nil
		}
		selected = &matches[index]
	}
	return selected
}

func modelIntentFromSkill(skill Skill) (ModelIntent, bool) {
	raw := skill.ModelIntent
	if !skill.Builtin || !skill.Enabled || raw == nil || !modelIntentNamePattern.MatchString(skill.Name) {
		return ModelIntent{}, false
	}
	handler, _ := raw["handler"].(string)
	confirmation, _ := raw["confirmation"].(string)
	candidate, output, defaults := object(raw["candidate"]), object(raw["output"]), object(raw["defaults"])
	if candidate == nil || output == nil || defaults == nil || !slices.Contains([]string{"wait-roster-then-send", "wait-then-send", "loop-send"}, handler) || !modelIntentNamePattern.MatchString(handler) || confirmation != "required" && confirmation != "none" {
		return ModelIntent{}, false
	}
	anyOfValue := candidate["any_of"]
	requireAnyValue := candidateValue(candidate, "require_any", []any{})
	collectiveValue := candidateValue(candidate, "collective_terms", []any{})
	if !modelIntentStringList(anyOfValue, true) || !modelIntentStringList(requireAnyValue, false) || !modelIntentStringList(collectiveValue, false) {
		return ModelIntent{}, false
	}
	anyOf := modelIntentStrings(anyOfValue)
	requireAny := modelIntentStrings(requireAnyValue)
	collective := modelIntentStrings(collectiveValue)
	minimum, okMinimum := modelIntentInteger(candidateValue(candidate, "min_known_sessions", 0))
	priority, okPriority := modelIntentInteger(candidateValue(candidate, "priority", 0))
	scope, _ := candidateValue(candidate, "session_scope", "condition").(string)
	tag, _ := output["tag"].(string)
	if !okMinimum || minimum < 0 || !okPriority || priority < 0 || priority > 100 || scope != "condition" && scope != "request" || !modelIntentNamePattern.MatchString(tag) {
		return ModelIntent{}, false
	}
	schema := object(output["schema"])
	if schemaName, ok := output["schema"].(string); ok {
		schema = modelIntentSchemas[schemaName]
	}
	if schema == nil {
		return ModelIntent{}, false
	}
	return ModelIntent{
		Name: skill.Name, Handler: handler, Tag: tag, SessionScope: scope, Schema: schema,
		AnyOf: anyOf, RequireAny: requireAny, Collective: collective, MinimumSessions: minimum, Priority: priority,
		ConfirmationRequired: confirmation == "required", Defaults: defaults,
	}, true
}

func modelIntentStrings(value any) []string {
	values := array(value)
	result := make([]string, 0, len(values))
	seen := make(map[string]bool, len(values))
	for _, item := range values {
		textValue, ok := item.(string)
		if !ok || strings.TrimSpace(textValue) == "" {
			continue
		}
		textValue = strings.ToLower(strings.TrimSpace(textValue))
		if !seen[textValue] {
			seen[textValue] = true
			result = append(result, textValue)
		}
	}
	return result
}

func modelIntentMatches(intent ModelIntent, question string, sessions []string) bool {
	lower := strings.ToLower(question)
	if !containsAny(lower, intent.AnyOf) || len(intent.RequireAny) > 0 && !containsAny(lower, intent.RequireAny) {
		return false
	}
	commandStart := len(question)
	for _, term := range intent.RequireAny {
		if index := strings.Index(lower, term); index >= 0 && index < commandStart {
			commandStart = index
		}
	}
	condition := strings.ToLower(question[:commandStart])
	sessionText := condition
	if intent.SessionScope == "request" {
		sessionText = question
	}
	mentioned := 0
	for _, session := range uniqueKnownSessions(sessions) {
		if knownSessionMentioned(sessionText, session) {
			mentioned++
		}
	}
	if len(intent.Collective) > 0 {
		return mentioned >= intent.MinimumSessions || containsAny(condition, intent.Collective)
	}
	return mentioned >= intent.MinimumSessions
}

func containsAny(text string, terms []string) bool {
	for _, term := range terms {
		if strings.Contains(text, term) {
			return true
		}
	}
	return false
}

func knownSessionMentioned(text, session string) bool {
	pattern := regexp.MustCompile(`(?i)(^|[^A-Za-z0-9_.-])[` + "`'\"" + `]?` + regexp.QuoteMeta(session) + `[` + "`'\"" + `]?($|[^A-Za-z0-9_.-])`)
	return pattern.MatchString(text)
}

func uniqueKnownSessions(sessions []string) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(sessions))
	for _, session := range sessions {
		session = strings.TrimSpace(session)
		if session != "" && !seen[session] {
			seen[session] = true
			result = append(result, session)
		}
	}
	return result
}

func ModelIntentProviderSchema(intent ModelIntent, sessions []string) map[string]any {
	known := uniqueKnownSessions(sessions)
	var lower func(any) any
	lower = func(value any) any {
		switch typed := value.(type) {
		case []any:
			result := make([]any, len(typed))
			for index, item := range typed {
				result[index] = lower(item)
			}
			return result
		case map[string]any:
			result := make(map[string]any, len(typed))
			for key, item := range typed {
				result[key] = lower(item)
			}
			if result["format"] == "known-session" {
				delete(result, "format")
				result["enum"] = stringValues(known)
			}
			return result
		default:
			return value
		}
	}
	return object(lower(intent.Schema))
}

func stringValues(values []string) []any {
	result := make([]any, len(values))
	for index, value := range values {
		result[index] = value
	}
	return result
}

func ModelIntentPrompt(intent ModelIntent, question string, sessions []string, schema map[string]any) string {
	encodedSessions, _ := json.Marshal(uniqueKnownSessions(sessions))
	encodedSchema, _ := json.Marshal(schema)
	return fmt.Sprintf("You are a read-only YO!agent intent planner. Do not use tools, do not run commands, and do not contact or send anything to tmux. Invoke preset %q by interpreting the user request against only this contract. Known tmux sessions: %s. If and only if the request unambiguously matches the preset, return exactly the JSON object matching this schema with no surrounding prose: %s. Use only known sessions. If any required field is ambiguous, return exactly <no-plan/>.\n\nUser request: %s", intent.Name, encodedSessions, encodedSchema, question)
}

func ParseModelIntentPlan(answer string, intent ModelIntent, sessions []string) map[string]any {
	answer = strings.TrimSpace(answer)
	if answer == "<no-plan/>" {
		return nil
	}
	if prefix, suffix := "<"+intent.Tag+">", "</"+intent.Tag+">"; len(answer) >= len(prefix)+len(suffix) && strings.EqualFold(answer[:len(prefix)], prefix) && strings.EqualFold(answer[len(answer)-len(suffix):], suffix) {
		answer = strings.TrimSpace(answer[len(prefix) : len(answer)-len(suffix)])
	}
	decoder := json.NewDecoder(strings.NewReader(answer))
	decoder.UseNumber()
	var plan map[string]any
	if err := decoder.Decode(&plan); err != nil || plan == nil {
		return nil
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return nil
	}
	known := uniqueKnownSessions(sessions)
	if !validateIntentSchema(plan, intent.Schema, known) {
		return nil
	}
	return plan
}

func validateIntentSchema(value any, schema map[string]any, sessions []string) bool {
	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		objectValue, ok := value.(map[string]any)
		properties := object(schema["properties"])
		required := array(schema["required"])
		if !ok || properties == nil {
			return false
		}
		for _, item := range required {
			key, ok := item.(string)
			if !ok {
				return false
			}
			if _, exists := objectValue[key]; !exists {
				return false
			}
		}
		if schema["additionalProperties"] == false {
			for key := range objectValue {
				if _, exists := properties[key]; !exists {
					return false
				}
			}
		}
		for key, item := range objectValue {
			property := object(properties[key])
			if property == nil || !validateIntentSchema(item, property, sessions) {
				return false
			}
		}
		return true
	case "array":
		values, ok := value.([]any)
		itemSchema := object(schema["items"])
		if !ok || itemSchema == nil || number(schema["minItems"]) > 0 && float64(len(values)) < number(schema["minItems"]) || number(schema["maxItems"]) > 0 && float64(len(values)) > number(schema["maxItems"]) {
			return false
		}
		for _, item := range values {
			if !validateIntentSchema(item, itemSchema, sessions) {
				return false
			}
		}
		return true
	case "string":
		textValue, ok := value.(string)
		if !ok || strings.TrimSpace(textValue) == "" || number(schema["maxLength"]) > 0 && float64(len([]rune(textValue))) > number(schema["maxLength"]) {
			return false
		}
		if choices, ok := schema["enum"].([]any); ok {
			matched := false
			for _, choice := range choices {
				matched = matched || choice == textValue
			}
			if !matched {
				return false
			}
		}
		if schema["format"] == "known-session" && !slices.Contains(sessions, textValue) {
			return false
		}
		return true
	case "number", "integer":
		numberValue, ok := value.(json.Number)
		if !ok {
			return false
		}
		parsed, err := numberValue.Float64()
		if err != nil || typeName == "integer" && parsed != float64(int64(parsed)) {
			return false
		}
		if minimum, ok := schema["minimum"]; ok && parsed < number(minimum) {
			return false
		}
		if maximum, ok := schema["maximum"]; ok && parsed > number(maximum) {
			return false
		}
		return true
	case "boolean":
		_, ok := value.(bool)
		return ok
	default:
		return false
	}
}

func ModelIntentJobPayload(intent ModelIntent, plan map[string]any) map[string]any {
	confirmation := intent.ConfirmationRequired
	booleanResult := plan["return_result"] == true
	switch intent.Handler {
	case "wait-roster-then-send":
		return map[string]any{
			"type": "wait_roster_then_send", "roster": plan["roster"], "quiet_seconds": number(intent.Defaults["quiet_seconds"]),
			"return_result": booleanResult, "requires_confirmation": confirmation,
			"action": map[string]any{"session": plan["destination"], "text": plan["command"], "submit": true, "return_result": booleanResult, "requires_confirmation": confirmation},
		}
	case "wait-then-send":
		return map[string]any{
			"type": "wait_then_send", "session": plan["session"], "quiet_seconds": numberOr(intent.Defaults["quiet_seconds"], 3),
			"return_result": booleanResult || plan["return_result"] == nil, "requires_confirmation": confirmation,
			"action": map[string]any{"session": plan["session"], "text": plan["prompt"], "submit": true, "return_result": booleanResult || plan["return_result"] == nil, "requires_confirmation": confirmation},
		}
	case "loop-send":
		return map[string]any{
			"type": "loop_send", "session": plan["session"], "interval_seconds": numberOr(plan["interval_seconds"], numberOr(intent.Defaults["interval_seconds"], 30)),
			"max_runs": integer(plan["max_runs"]), "return_result": false, "requires_confirmation": confirmation,
			"action": map[string]any{"session": plan["session"], "text": plan["prompt"], "submit": true, "return_result": false, "requires_confirmation": confirmation},
		}
	default:
		return nil
	}
}
