package stats

import (
	"encoding/json"
	"os"
	"path/filepath"
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

func TestPythonAndGoPricingProfilesMatchSyntheticSettings(t *testing.T) {
	settings := map[string]any{
		"cost": map[string]any{
			"openai_pricing_profile":             "default",
			"_openai_pricing_profile_history":    []any{"0|default", "10|subscription", "20|default"},
			"anthropic_pricing_profile":          "subscription",
			"_anthropic_pricing_profile_history": []any{"0|default", "5|subscription"},
		},
		"yoagent": map[string]any{"invocation": "cli"},
	}
	cases := []struct {
		provider, source, endpoint string
		at                         float64
	}{
		{provider: "openai", source: "codex", at: 9},
		{provider: "openai", source: "codex", at: 10},
		{provider: "openai", source: "codex", at: 20},
		{provider: "anthropic", source: "claude", at: 5},
		{provider: "openai", source: "opencode", at: 15},
		{provider: "openai", source: "YO!agent", endpoint: "yoagent", at: 15},
	}
	input := struct {
		Settings map[string]any `json:"settings"`
		Cases    []struct {
			Provider string  `json:"provider"`
			Source   string  `json:"source"`
			Endpoint string  `json:"endpoint"`
			At       float64 `json:"at"`
		} `json:"cases"`
	}{Settings: settings}
	for _, test := range cases {
		input.Cases = append(input.Cases, struct {
			Provider string  `json:"provider"`
			Source   string  `json:"source"`
			Endpoint string  `json:"endpoint"`
			At       float64 `json:"at"`
		}{test.provider, test.source, test.endpoint, test.at})
	}
	encoded, err := json.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	inputPath := filepath.Join(root, "settings.json")
	if err := os.WriteFile(inputPath, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	scriptPath := filepath.Join(root, "pricing_profile.py")
	const script = `import json, sys
from yomux_lib.workspace.settings import usage_pricing_profile
value = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps([usage_pricing_profile(value["settings"], provider=row["provider"], execution_source=row["source"], endpoint=row["endpoint"], observed_at=row["at"]) for row in value["cases"]]))
`
	if err := os.WriteFile(scriptPath, []byte(script), 0o600); err != nil {
		t.Fatal(err)
	}
	output := runPython(t, pythonExecutable(t), scriptPath, inputPath)
	var pythonProfiles []string
	if err := json.Unmarshal([]byte(output), &pythonProfiles); err != nil {
		t.Fatalf("decode Python pricing profiles %q: %v", output, err)
	}
	if len(pythonProfiles) != len(cases) {
		t.Fatalf("Python returned %d profiles for %d cases", len(pythonProfiles), len(cases))
	}
	for index, test := range cases {
		goProfile := ResolveUsagePricingProfile(settings, test.provider, test.source, test.endpoint, test.at)
		if goProfile != pythonProfiles[index] {
			t.Errorf("case %d (%s/%s at %g): Go profile %q, Python profile %q", index, test.provider, test.source, test.at, goProfile, pythonProfiles[index])
		}
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
