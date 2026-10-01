package stats

import (
	"encoding/json"
	"reflect"
	"strings"
)

// usageReplayPayloadChange mirrors the Python writer's narrow metadata
// exceptions while preserving immutable token quantity and source identity.
func usageReplayPayloadChange(eventID string, previousAt float64, previousJSON string, observedAt float64, currentJSON string) (string, bool, bool) {
	if previousAt != observedAt {
		return "", false, false
	}
	var previous, current map[string]any
	if json.Unmarshal([]byte(previousJSON), &previous) != nil || json.Unmarshal([]byte(currentJSON), &current) != nil {
		return "", false, false
	}
	if reflect.DeepEqual(previous, current) {
		return "", false, true
	}
	if legacyOpenCodePayloadCompatible(eventID, previous, current) {
		return "", false, true
	}
	if repairedJSON, ok := repairUnknownModelPayload(previous, current); ok {
		return repairedJSON, true, true
	}

	changed := false
	for _, field := range []string{"agent_id", "model_evidence", "pricing_profile"} {
		if !reflect.DeepEqual(previous[field], current[field]) {
			changed = true
		}
		delete(previous, field)
		delete(current, field)
	}
	return "", false, changed && reflect.DeepEqual(previous, current)
}

func repairUnknownModelPayload(previous, current map[string]any) (string, bool) {
	previousModel, ok := previous["model"].(string)
	if !ok || !strings.EqualFold(strings.TrimSpace(previousModel), "unknown") {
		return "", false
	}
	currentModel, ok := current["model"].(string)
	if !ok {
		return "", false
	}
	currentModel = strings.TrimSpace(currentModel)
	if currentModel == "" || strings.EqualFold(currentModel, "unknown") {
		return "", false
	}
	previousComparable := cloneUsagePayload(previous)
	currentComparable := cloneUsagePayload(current)
	for _, field := range []string{"agent_id", "model", "model_evidence", "pricing_profile"} {
		delete(previousComparable, field)
		delete(currentComparable, field)
	}
	if !reflect.DeepEqual(previousComparable, currentComparable) {
		return "", false
	}
	repaired := cloneUsagePayload(previous)
	repaired["model"] = currentModel
	if evidence, ok := current["model_evidence"].(string); ok && strings.TrimSpace(evidence) != "" {
		repaired["model_evidence"] = strings.TrimSpace(evidence)
	}
	encoded, err := json.Marshal(repaired)
	if err != nil {
		return "", false
	}
	return string(encoded), true
}

func cloneUsagePayload(payload map[string]any) map[string]any {
	cloned := make(map[string]any, len(payload))
	for key, value := range payload {
		cloned[key] = value
	}
	return cloned
}

// Legacy Go OpenCode rows predate execution_source and pricing metadata. Accept
// only their exact five-field shape and the current collector's known fields.
func legacyOpenCodePayloadCompatible(eventID string, previous, current map[string]any) bool {
	if !strings.HasPrefix(eventID, "opencode:") || len(previous) != 5 {
		return false
	}
	// A live pane rename changes agent attribution after an older OpenCode
	// cursor is replayed. Keep that first attribution durable while still
	// requiring every source-derived quantity and identity field to match.
	immutableFields := [...]string{"quantity", "provider", "model", "thread_id"}
	legacyFields := [...]string{"quantity", "provider", "model", "thread_id", "agent_id"}
	for _, field := range immutableFields {
		if _, exists := previous[field]; !exists || !reflect.DeepEqual(previous[field], current[field]) {
			return false
		}
	}
	previousAgent, previousHasAgent := previous["agent_id"].(string)
	currentAgent, currentHasAgent := current["agent_id"].(string)
	if !previousHasAgent || strings.TrimSpace(previousAgent) == "" || !currentHasAgent || strings.TrimSpace(currentAgent) == "" {
		return false
	}
	for field := range previous {
		if !containsUsageReplayField(legacyFields[:], field) {
			return false
		}
	}
	if current["execution_source"] != "opencode" {
		return false
	}
	for _, field := range []string{"effort", "pricing_profile", "service_tier"} {
		if text, ok := current[field].(string); !ok || text == "" {
			return false
		}
	}
	if _, ok := current["telemetry_complete"].(bool); !ok {
		return false
	}
	for field, value := range current {
		if containsUsageReplayField(legacyFields[:], field) || field == "execution_source" || field == "telemetry_complete" {
			continue
		}
		if !containsUsageReplayMetadataField(field) {
			return false
		}
		if text, ok := value.(string); !ok || text == "" {
			return false
		}
	}
	return true
}

func containsUsageReplayField(fields []string, candidate string) bool {
	for _, field := range fields {
		if candidate == field {
			return true
		}
	}
	return false
}

func containsUsageReplayMetadataField(field string) bool {
	switch field {
	case "model_evidence", "effort", "pricing_profile", "service_tier":
		return true
	default:
		return false
	}
}
