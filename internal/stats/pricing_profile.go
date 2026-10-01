package stats

import (
	"math"
	"sort"
	"strconv"
	"strings"
)

// ResolveUsagePricingProfile mirrors the Python usage_pricing_profile policy
// for collector atoms. Settings are the sanitized settings mapping returned by
// yomux_lib.workspace.settings.settings_payload.
func ResolveUsagePricingProfile(settings map[string]any, provider, executionSource, endpoint string, observedAt float64) string {
	provider = strings.ToLower(strings.TrimSpace(provider))
	source := strings.ReplaceAll(strings.ToLower(strings.TrimSpace(executionSource)), "!", "")
	endpoint = strings.ToLower(strings.TrimSpace(endpoint))
	yoagent, _ := settings["yoagent"].(map[string]any)
	yoagentCLI := source == "yoagent" && endpoint == "yoagent" && stringSetting(yoagent, "invocation", "cli") == "cli"
	switch provider {
	case "openai":
		if source != "codex" && !yoagentCLI && !((source == "summary" || source == "ai summary") && (endpoint == "summary" || endpoint == "codex-exec")) {
			return "default"
		}
	case "anthropic":
		if source != "claude" && !yoagentCLI {
			return "default"
		}
	default:
		return "default"
	}

	cost, _ := settings["cost"].(map[string]any)
	profileKey := provider + "_pricing_profile"
	historyKey := "_" + provider + "_pricing_profile_history"
	if cost == nil {
		return "default"
	}
	current := validPricingProfile(stringSetting(cost, profileKey, "default"))
	historyValue, hasHistory := cost[historyKey]
	if !hasHistory {
		return current
	}
	if math.IsNaN(observedAt) || math.IsInf(observedAt, 0) || observedAt < 0 {
		return "default"
	}

	byTimestamp := map[float64]string{0: "default"}
	for _, item := range stringSlice(historyValue) {
		parts := strings.SplitN(item, "|", 2)
		if len(parts) != 2 {
			continue
		}
		timestamp, err := strconv.ParseFloat(parts[0], 64)
		profile := validPricingProfile(strings.ToLower(strings.TrimSpace(parts[1])))
		if err == nil && !math.IsNaN(timestamp) && !math.IsInf(timestamp, 0) && timestamp >= 0 && profile == strings.ToLower(strings.TrimSpace(parts[1])) {
			byTimestamp[timestamp] = profile
		}
	}
	timestamps := make([]float64, 0, len(byTimestamp))
	for timestamp := range byTimestamp {
		timestamps = append(timestamps, timestamp)
	}
	sort.Float64s(timestamps)
	selected := "default"
	previous := ""
	for _, timestamp := range timestamps {
		profile := byTimestamp[timestamp]
		if profile == previous {
			continue
		}
		previous = profile
		if timestamp > observedAt {
			break
		}
		selected = profile
	}
	return selected
}

func validPricingProfile(value string) string {
	if value == "subscription" {
		return value
	}
	return "default"
}

func stringSetting(values map[string]any, key, fallback string) string {
	if value, ok := values[key].(string); ok && value != "" {
		return value
	}
	return fallback
}

func stringSlice(value any) []string {
	switch values := value.(type) {
	case []string:
		return values
	case []any:
		result := make([]string, 0, len(values))
		for _, item := range values {
			if text, ok := item.(string); ok {
				result = append(result, text)
			}
		}
		return result
	default:
		return nil
	}
}
