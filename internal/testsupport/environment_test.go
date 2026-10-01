package testsupport

import (
	"strings"
	"testing"
)

func TestSyntheticEnvironmentDropsInheritedTmuxClient(t *testing.T) {
	t.Setenv("TMUX", "/host/tmux/default,123,0")
	t.Setenv("TMUX_PANE", "%7")
	t.Setenv("TMUX_TMPDIR", "/host/tmux")

	for _, entry := range SyntheticEnvironment("YOLOMUX_TMUX_SOCKET=/fixture/tmux.sock") {
		key, _, ok := strings.Cut(entry, "=")
		if !ok {
			continue
		}
		if key == "TMUX" || key == "TMUX_PANE" || key == "TMUX_TMPDIR" {
			t.Fatalf("synthetic environment retained inherited %s", key)
		}
	}
}

func TestSyntheticEnvironmentDropsCanonicalAndLegacyProductRoots(t *testing.T) {
	t.Setenv("YOMUX_ROOT", "/host/yomux")
	t.Setenv("YOLOMUX_ROOT", "/host/yolomux")
	t.Setenv("YOMUX_STATS_DISABLE_COLLECTORS", "0")

	for _, entry := range SyntheticEnvironment("YOLOMUX_STATS_DISABLE_COLLECTORS=1") {
		key, value, ok := strings.Cut(entry, "=")
		if !ok {
			continue
		}
		if key == "YOMUX_ROOT" || key == "YOLOMUX_ROOT" {
			t.Fatalf("synthetic environment retained inherited product root %s", key)
		}
		if key == "YOMUX_STATS_DISABLE_COLLECTORS" {
			t.Fatalf("synthetic environment retained inherited canonical override %s", key)
		}
		if key == "YOLOMUX_STATS_DISABLE_COLLECTORS" && value != "1" {
			t.Fatalf("synthetic environment canonical override = %q, want fixture value", value)
		}
	}
}

func TestSyntheticEnvironmentDropsInheritedProviderCredentials(t *testing.T) {
	credentialNames := []string{
		"ANTHROPIC_API_KEY",
		"ANTHROPIC_AUTH_TOKEN",
		"AWS_ACCESS_KEY_ID",
		"AWS_SECRET_ACCESS_KEY",
		"AWS_SESSION_TOKEN",
		"GH_TOKEN",
		"GITHUB_TOKEN",
		"GOOGLE_API_KEY",
		"GOOGLE_APPLICATION_CREDENTIALS",
		"HF_TOKEN",
		"HUGGINGFACE_HUB_TOKEN",
		"LINEAR_KEY",
		"NGC_API_KEY",
		"OPENAI_API_KEY",
	}
	for _, name := range credentialNames {
		t.Setenv(name, "synthetic-test-secret")
	}

	overrideSeen := false
	for _, entry := range SyntheticEnvironment("OPENAI_API_KEY=fixture-owned-key") {
		key, value, ok := strings.Cut(entry, "=")
		if !ok {
			continue
		}
		for _, name := range credentialNames {
			if key == name && key != "OPENAI_API_KEY" {
				t.Fatalf("synthetic environment retained inherited credential %s", key)
			}
		}
		if key == "OPENAI_API_KEY" {
			if value != "fixture-owned-key" {
				t.Fatalf("synthetic environment credential override was not preserved")
			}
			overrideSeen = true
		}
	}
	if !overrideSeen {
		t.Fatal("synthetic environment omitted an explicit fixture-owned credential override")
	}
}
