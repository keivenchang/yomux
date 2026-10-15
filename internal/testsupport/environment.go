package testsupport

import (
	"errors"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/keivenchang/yomux/internal/productenv"
)

// Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
func RequireDeprecatedPythonBackend(t *testing.T) {
	t.Helper()
	if os.Getenv("YOMUX_RUN_DEPRECATED_PYTHON") != "1" {
		t.Skip("deprecated Python backend comparison requires --run-deprecated-python (Bazel: --test_env=YOMUX_RUN_DEPRECATED_PYTHON=1)")
	}
}

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
	privateRoot := productenv.Value("PRIVATE_TEST_ENV_ROOT")
	privateDefaults := make([]string, 0, 5)
	if _, overridden := overrideKeys["YOMUX_TEST_DISABLE_PRICING_REFRESH"]; !overridden {
		privateDefaults = append(privateDefaults, "YOMUX_TEST_DISABLE_PRICING_REFRESH=1")
	}
	if privateRoot != "" {
		if _, overridden := overrideKeys["HOME"]; !overridden {
			privateDefaults = append(privateDefaults, "HOME="+filepath.Join(privateRoot, privateTestDirectories["HOME"]))
		}
		if _, overridden := overrideKeys["TMPDIR"]; !overridden {
			privateDefaults = append(privateDefaults, "TMPDIR="+filepath.Join(privateRoot, privateTestDirectories["TMPDIR"]))
		}
		for _, entry := range []string{"PYTHONUSERBASE=" + os.Getenv("PYTHONUSERBASE"), "PYTHONDONTWRITEBYTECODE=1"} {
			key, _, _ := strings.Cut(entry, "=")
			if _, overridden := overrideKeys[key]; !overridden {
				privateDefaults = append(privateDefaults, entry)
			}
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
	environment = append(environment, privateDefaults...)
	return append(environment, overrides...)
}

// NewPrivateTestEnvironment gives cross-language test children temporary HOME and XDG roots.
// Python module imports may create or chmod application config directories, so tests must not
// inherit writable paths from the developer account or share those paths across parallel actions.
func NewPrivateTestEnvironment() (func(), error) {
	pythonUserBase := os.Getenv("PYTHONUSERBASE")
	if pythonUserBase == "" {
		originalHome := os.Getenv("HOME")
		if originalHome == "" {
			currentUser, err := user.Current()
			if err != nil {
				return nil, err
			}
			originalHome = currentUser.HomeDir
		}
		if originalHome == "" {
			return nil, errors.New("resolve original home directory")
		}
		pythonUserBase = filepath.Join(originalHome, ".local")
	}
	tempRoot := os.TempDir()
	if runtime.GOOS == "darwin" || runtime.GOOS == "linux" {
		// Bazel and macOS can provide deeply nested temp roots. Test tmux sockets
		// are often created below testing.T.TempDir, so keep this private prefix
		// short enough for the Unix-domain socket path limit on both platforms.
		tempRoot = "/tmp"
	}
	root, err := os.MkdirTemp(tempRoot, "ym-")
	if err != nil {
		return nil, err
	}
	for _, directory := range privateTestDirectories {
		path := filepath.Join(root, directory)
		if err := os.Mkdir(path, 0o700); err != nil {
			_ = os.RemoveAll(root)
			return nil, err
		}
	}
	environment := make(map[string]string, len(privateTestDirectories)+4)
	for key, directory := range privateTestDirectories {
		environment[key] = filepath.Join(root, directory)
	}
	for _, key := range productenv.Names("PRIVATE_TEST_ENV_ROOT") {
		environment[key] = root
	}
	environment["PYTHONUSERBASE"] = pythonUserBase
	environment["PYTHONDONTWRITEBYTECODE"] = "1"
	// Direct adapter tests must stay off the operator's machine-wide tmux. Keep
	// the legacy alias as the fixture default so the adapter exercises fallback.
	tmuxSocketNames := productenv.Names("TMUX_SOCKET")
	for _, name := range tmuxSocketNames[:len(tmuxSocketNames)-1] {
		if err := os.Unsetenv(name); err != nil {
			_ = os.RemoveAll(root)
			return nil, err
		}
	}
	environment[tmuxSocketNames[len(tmuxSocketNames)-1]] = filepath.Join(root, "tmux.sock")
	for key, value := range environment {
		if err := os.Setenv(key, value); err != nil {
			_ = os.RemoveAll(root)
			return nil, err
		}
	}
	return func() { _ = os.RemoveAll(root) }, nil
}

var privateTestDirectories = map[string]string{
	"HOME":              "home",
	"TMPDIR":            "tmp",
	"XDG_CONFIG_HOME":   "xdg-config",
	"XDG_DATA_HOME":     "xdg-data",
	"XDG_STATE_HOME":    "xdg-state",
	"XDG_CACHE_HOME":    "xdg-cache",
	"CODEX_HOME":        "codex-home",
	"CLAUDE_CONFIG_DIR": "claude-config",
	"XDG_RUNTIME_DIR":   "xdg-runtime",
}

// ShortTempDir keeps private fixture paths short enough for Unix-domain sockets
// when Bazel supplies a deeply nested per-test temporary directory.
func ShortTempDir(t testing.TB) string {
	t.Helper()
	directory, err := os.MkdirTemp("/tmp", "ym-")
	if err != nil {
		t.Fatalf("create short private temporary directory: %v", err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(directory); err != nil {
			t.Errorf("remove short private temporary directory %q: %v", directory, err)
		}
	})
	return directory
}

func ambientSyntheticInputKey(key string) bool {
	if _, product := productenv.Suffix(key); product || strings.HasPrefix(key, "XDG_") ||
		strings.HasPrefix(key, "OPENCODE_") || strings.HasPrefix(key, "CODEX_") || strings.HasPrefix(key, "CLAUDE_") {
		return true
	}
	switch key {
	case "TEST_SHARD_INDEX", "TEST_TOTAL_SHARDS", "TEST_SHARD_STATUS_FILE",
		"TESTBRIDGE_TEST_ONLY", "TESTBRIDGE_TEST_RUNNER_FAIL_FAST",
		"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
		"GH_TOKEN", "GITHUB_TOKEN", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
		"HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "LINEAR_KEY", "NGC_API_KEY", "OPENAI_API_KEY",
		"HOME", "TMPDIR", "TMUX", "TMUX_PANE", "TMUX_TMPDIR", "PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX":
		return true
	default:
		return false
	}
}

// PreserveProductEnvironment restores synthesized aliases and metadata as well as raw fixture inputs.
func PreserveProductEnvironment(t testing.TB) {
	t.Helper()
	original := make(map[string]string)
	for _, entry := range os.Environ() {
		key, value, _ := strings.Cut(entry, "=")
		if _, product := productenv.Suffix(key); product {
			original[key] = value
		}
	}
	t.Cleanup(func() {
		for _, entry := range os.Environ() {
			key, _, _ := strings.Cut(entry, "=")
			if _, product := productenv.Suffix(key); product {
				if _, existed := original[key]; !existed {
					_ = os.Unsetenv(key)
				}
			}
		}
		for key, value := range original {
			_ = os.Setenv(key, value)
		}
	})
}
