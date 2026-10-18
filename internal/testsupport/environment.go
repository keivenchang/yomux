package testsupport

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"

	"github.com/keivenchang/yomux/internal/productenv"
)

// ToolPath keeps executable fixtures on the declared runtime PATH in Bazel actions.
func ToolPath(t testing.TB, name string) string {
	t.Helper()
	path, err := exec.LookPath(name)
	if err != nil {
		t.Fatalf("required fixture executable %q: %v", name, err)
	}
	path, err = filepath.Abs(path)
	if err != nil {
		t.Fatalf("absolute fixture executable %q: %v", name, err)
	}
	return path
}

// ShellScript pins generated script interpreters as well as their PATH-resolved commands.
func ShellScript(t testing.TB, body string) string {
	t.Helper()
	shell := ToolPath(t, "sh")
	if strings.ContainsAny(shell, " \t\r\n") {
		t.Fatalf("fixture shell cannot be represented in a shebang: %q", shell)
	}
	return "#!" + shell + "\n" + body
}

// SyntheticEnvironment removes ambient product and client-data inputs before
// a development/test child is started. Callers must add every fixture-owned
// value explicitly; overrides are deduplicated so the child cannot retain an
// earlier inherited value for the same key.
func SyntheticEnvironment(overrides ...string) []string {
	// Process and fixture ownership are inherited capabilities, not caller configuration.
	for _, protected := range []string{"YOMUX_NATIVE_SHORT_TMPDIR", "YOMUX_BAZEL_PROCESS_SCOPE"} {
		value, present := os.LookupEnv(protected)
		if !present {
			continue
		}
		filtered := make([]string, 0, len(overrides)+1)
		for _, entry := range overrides {
			key, _, _ := strings.Cut(entry, "=")
			if key != protected {
				filtered = append(filtered, entry)
			}
		}
		overrides = append(filtered, protected+"="+value)
	}
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
		if !ok || (ambientSyntheticInputKey(key) && key != "YOMUX_NATIVE_SHORT_TMPDIR" && key != "YOMUX_BAZEL_PROCESS_SCOPE") {
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
	root, err := MkdirShortTemp("ym-")
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

// ShortTempBase is the single owner for socket-safe fixture allocation.
// The native wrapper supplies an already private action root; diagnostics retain /tmp.
func ShortTempBase() (string, error) {
	root, present := os.LookupEnv("YOMUX_NATIVE_SHORT_TMPDIR")
	if !present {
		return "/tmp", nil
	}
	if !filepath.IsAbs(root) {
		return "", errors.New("native short fixture root must be absolute")
	}
	metadata, err := os.Lstat(root)
	if err != nil {
		return "", err
	}
	owner, ok := metadata.Sys().(*syscall.Stat_t)
	if !ok || !metadata.IsDir() || metadata.Mode().Perm() != 0o700 || owner.Uid != uint32(os.Getuid()) {
		return "", errors.New("native short fixture root must be user-owned mode 0700 (uid " + strconv.Itoa(os.Getuid()) + ")")
	}
	return root, nil
}

// MkdirShortTemp preserves caller prefixes under the shared socket-safe base.
func MkdirShortTemp(prefix string) (string, error) {
	base, err := ShortTempBase()
	if err != nil {
		return "", err
	}
	if os.Getenv("YOMUX_NATIVE_SHORT_TMPDIR") == "" {
		return os.MkdirTemp(base, prefix)
	}
	for {
		var token [3]byte
		if _, err := rand.Read(token[:]); err != nil {
			return "", err
		}
		directory := filepath.Join(base, "f"+hex.EncodeToString(token[:]))
		if err := os.Mkdir(directory, 0o700); err != nil {
			if errors.Is(err, os.ErrExist) {
				continue
			}
			return "", err
		}
		return directory, nil
	}
}

// ShortTempDir keeps private fixture paths short enough for Unix-domain sockets
// when Bazel supplies a deeply nested per-test temporary directory.
func ShortTempDir(t testing.TB) string {
	t.Helper()
	directory, err := MkdirShortTemp("ym-")
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
