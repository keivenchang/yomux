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
	if strings.HasPrefix(key, "YOLOMUX_") || strings.HasPrefix(key, "XDG_") ||
		strings.HasPrefix(key, "OPENCODE_") || strings.HasPrefix(key, "CODEX_") || strings.HasPrefix(key, "CLAUDE_") {
		return true
	}
	switch key {
	case "HOME", "TMPDIR", "PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX":
		return true
	default:
		return false
	}
}
