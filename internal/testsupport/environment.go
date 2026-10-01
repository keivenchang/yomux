package testsupport

import (
	"os"
	"strings"
)

// SyntheticEnvironment removes ambient product and client-data inputs before
// a development/test child is started. Callers must add every fixture-owned
// value explicitly; overrides are deduplicated so the child cannot retain an
// earlier inherited value for the same key.
func SyntheticEnvironment(overrides ...string) []string {
	overrideKeys := make(map[string]struct{}, len(overrides))
	for _, entry := range overrides {
		key, _, ok := strings.Cut(entry, "=")
		if ok && key != "" {
			overrideKeys[key] = struct{}{}
		}
	}
	environment := make([]string, 0, len(os.Environ())+len(overrides))
	for _, entry := range os.Environ() {
		key, _, ok := strings.Cut(entry, "=")
		if !ok || ambientSyntheticInputKey(key) {
			continue
		}
		if _, overridden := overrideKeys[key]; overridden {
			continue
		}
		environment = append(environment, entry)
	}
	return append(environment, overrides...)
}

func ambientSyntheticInputKey(key string) bool {
	if strings.HasPrefix(key, "YOMUX_") || strings.HasPrefix(key, "YOLOMUX_") || strings.HasPrefix(key, "XDG_") ||
		strings.HasPrefix(key, "OPENCODE_") || strings.HasPrefix(key, "CODEX_") || strings.HasPrefix(key, "CLAUDE_") {
		return true
	}
	switch key {
	case "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
		"GH_TOKEN", "GITHUB_TOKEN", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
		"HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "LINEAR_KEY", "NGC_API_KEY", "OPENAI_API_KEY",
		"HOME", "TMPDIR", "TMUX", "TMUX_PANE", "TMUX_TMPDIR", "PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX":
		return true
	default:
		return false
	}
}
