package stats

import (
	"testing"
)

func TestResolveUsagePricingProfileMatchesSettingsTimeline(t *testing.T) {
	settings := map[string]any{
		"cost": map[string]any{
			"openai_pricing_profile":             "default",
			"_openai_pricing_profile_history":    []any{"0|default", "10|subscription", "20|default"},
			"anthropic_pricing_profile":          "subscription",
			"_anthropic_pricing_profile_history": []any{"0|default", "5|subscription"},
		},
		"yoagent": map[string]any{"invocation": "cli"},
	}
	for _, test := range []struct {
		name     string
		provider string
		source   string
		endpoint string
		at       float64
		want     string
		settings map[string]any
	}{
		{name: "before change", provider: "openai", source: "codex", at: 9, want: "default"},
		{name: "effective at change", provider: "openai", source: "codex", at: 10, want: "subscription"},
		{name: "between changes", provider: "openai", source: "codex", at: 19, want: "subscription"},
		{name: "reverted", provider: "openai", source: "codex", at: 20, want: "default"},
		{name: "anthropic timeline", provider: "anthropic", source: "claude", at: 5, want: "subscription"},
		{name: "OpenCode is ineligible", provider: "openai", source: "opencode", at: 15, want: "default"},
		{name: "YOagent CLI", provider: "openai", source: "YO!agent", endpoint: "yoagent", at: 15, want: "subscription"},
		{name: "YOagent non-CLI", provider: "openai", source: "YO!agent", endpoint: "yoagent", at: 15, want: "default", settings: map[string]any{
			"cost":    map[string]any{"_openai_pricing_profile_history": []any{"0|default", "10|subscription"}},
			"yoagent": map[string]any{"invocation": "api"},
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			currentSettings := settings
			if test.settings != nil {
				currentSettings = test.settings
			}
			if got := ResolveUsagePricingProfile(currentSettings, test.provider, test.source, test.endpoint, test.at); got != test.want {
				t.Fatalf("pricing profile = %q, want %q", got, test.want)
			}
		})
	}
}

func TestAgentTokenCollectorAppliesOnlyEligibleUsagePricingProfile(t *testing.T) {
	collector := NewAgentTokenCollector(nil, nil, AgentTokenCollectorConfig{
		UsagePricingProfile: func(atom UsageAtom) string {
			return ResolveUsagePricingProfile(map[string]any{
				"cost": map[string]any{"_openai_pricing_profile_history": []string{"0|default", "10|subscription"}},
			}, stringPayload(atom.Payload, "provider"), stringPayload(atom.Payload, "execution_source"), stringPayload(atom.Payload, "endpoint"), atom.ObservedAt)
		},
	})
	batch := AppendBatch{UsageAtoms: []UsageAtom{
		{ObservedAt: 12, Payload: map[string]any{"provider": "openai", "execution_source": "codex", "pricing_profile": "default"}},
		{ObservedAt: 12, Payload: map[string]any{"provider": "openai", "execution_source": "opencode", "pricing_profile": "default"}},
		{ObservedAt: 12, Payload: map[string]any{"provider": "openai", "execution_source": "codex", "pricing_profile": "subscription"}},
	}}
	collector.applyUsagePricingProfiles(&batch)
	if got := stringPayload(batch.UsageAtoms[0].Payload, "pricing_profile"); got != "subscription" {
		t.Fatalf("eligible Codex profile = %q, want subscription", got)
	}
	if got := stringPayload(batch.UsageAtoms[1].Payload, "pricing_profile"); got != "default" {
		t.Fatalf("ineligible OpenCode profile = %q, want default", got)
	}
	if got := stringPayload(batch.UsageAtoms[2].Payload, "pricing_profile"); got != "subscription" {
		t.Fatalf("explicit transcript profile = %q, want subscription", got)
	}
}
