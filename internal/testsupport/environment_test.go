package testsupport

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestGoHelperProcessCommandSurvivesEnvironmentReplacement(t *testing.T) {
	if len(os.Args) >= 3 && os.Args[len(os.Args)-2] == "--go-helper-arguments" {
		fmt.Fprintf(os.Stdout, "%s\n%s\n", os.Getenv("GORACE"), os.Args[len(os.Args)-1])
		os.Exit(0)
	}
	logDirectory := filepath.Join(t.TempDir(), "fixture  logs")
	if err := os.MkdirAll(logDirectory, 0o700); err != nil {
		t.Fatal(err)
	}
	options := "exitcode=77 log_path='" + filepath.Join(logDirectory, "race") + "' atexit_sleep_ms=1000"
	t.Setenv("GORACE", options)
	argument := "fixture argument with spaces"
	command := GoHelperProcessCommand(t, "-test.run=^TestGoHelperProcessCommandSurvivesEnvironmentReplacement$", "--", "--go-helper-arguments", argument)
	// Model the production worker whitelist replacing the hook's environment.
	command.Env = []string{"PATH=/fixture-without-tools"}
	output, err := command.CombinedOutput()
	want := options + " atexit_sleep_ms=0\n" + argument + "\n"
	if err != nil || string(output) != want {
		t.Fatalf("helper after environment replacement = %q, %v; want %q", output, err, want)
	}
}

func TestGoHelperProcessEnvironmentPreservesRaceChecksAndPrivateInputs(t *testing.T) {
	t.Setenv("AMBIENT_HELPER_SECRET", "must-not-inherit")
	tests := []struct {
		name  string
		input []string
		want  string
	}{
		{"default", []string{"PATH=/fixture"}, "atexit_sleep_ms=0"},
		{"preserve-reporting", []string{"PATH=/fixture", "GORACE=halt_on_error=1 exitcode=77 log_path=/fixture/race atexit_sleep_ms=1000 strip_path_prefix=/fixture"}, "halt_on_error=1 exitcode=77 log_path=/fixture/race atexit_sleep_ms=1000 strip_path_prefix=/fixture atexit_sleep_ms=0"},
		{"quoted-whitespace", []string{"PATH=/fixture", "GORACE=log_path='/private/fixture  logs/race'\t exitcode=77 atexit_sleep_ms=1000"}, "log_path='/private/fixture  logs/race'\t exitcode=77 atexit_sleep_ms=1000 atexit_sleep_ms=0"},
		{"duplicate-exit-option", []string{"PATH=/fixture", "GORACE=atexit_sleep_ms=1000 exitcode=77 atexit_sleep_ms=20"}, "atexit_sleep_ms=1000 exitcode=77 atexit_sleep_ms=20 atexit_sleep_ms=0"},
		{"duplicate-environment-key", []string{"PATH=/fixture", "GORACE=exitcode=12", "GORACE=exitcode=77"}, "exitcode=77 atexit_sleep_ms=0"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := GoHelperProcessEnvironment(test.input)
			if len(got) != 2 || got[0] != "PATH=/fixture" || got[1] != "GORACE="+test.want {
				t.Fatalf("helper environment = %#v, want private PATH and GORACE=%s", got, test.want)
			}
			if second := GoHelperProcessEnvironment(got); strings.Join(second, "\n") != strings.Join(got, "\n") {
				t.Fatalf("helper environment is not idempotent: %#v", second)
			}
		})
	}
}

func TestExecutableFixturesUseTheSelectedRuntimeShell(t *testing.T) {
	shell := ToolPath(t, "sh")
	bin := t.TempDir()
	selected := filepath.Join(bin, "sh")
	if err := os.Symlink(shell, selected); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin)
	if got := ToolPath(t, "sh"); got != selected {
		t.Fatalf("fixture executable = %q, want PATH-selected %q", got, selected)
	}
	script := filepath.Join(t.TempDir(), "fixture")
	source := ShellScript(t, "printf 'selected-runtime-shell\\n'\n")
	if !strings.HasPrefix(source, "#!"+selected+"\n") {
		t.Fatalf("fixture interpreter does not use the selected runtime: %q", source)
	}
	if err := os.WriteFile(script, []byte(source), 0o700); err != nil {
		t.Fatal(err)
	}
	output, err := exec.Command(script).CombinedOutput()
	if err != nil || string(output) != "selected-runtime-shell\n" {
		t.Fatalf("fixture through selected shell = %q, %v", output, err)
	}
}

func TestPreserveProductEnvironmentOwnsGeneratedPeers(t *testing.T) {
	t.Setenv("YOMUX_FIXTURE_OWNER", "original")
	t.Setenv("YOLOMUX_FIXTURE_OWNER", "original")
	before, present := os.LookupEnv("YOMUX_ENVIRONMENT_ALIASES")
	t.Run("normalize-and-assign", func(t *testing.T) {
		PreserveProductEnvironment(t)
		t.Setenv("YOLOMUX_FIXTURE_OWNER", "raw")
		_ = os.Setenv("YOMUX_FIXTURE_OWNER", "generated")
		_ = os.Setenv("YOMUX_ENVIRONMENT_ALIASES", "generated-metadata")
	})
	if os.Getenv("YOMUX_FIXTURE_OWNER") != "original" || os.Getenv("YOLOMUX_FIXTURE_OWNER") != "original" {
		t.Fatal("fixture cleanup failed to restore the complete alias pair")
	}
	if after, exists := os.LookupEnv("YOMUX_ENVIRONMENT_ALIASES"); after != before || exists != present {
		t.Fatal("fixture cleanup failed to restore synthesized metadata")
	}
}

func TestPrivateTestEnvironmentKeepsNestedTmuxSocketPathsShort(t *testing.T) {
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		t.Skip("short Unix temp roots are selected on Linux and macOS")
	}
	t.Setenv("TMPDIR", "/tmp/yolomux-bazel-tmp.5XyIp2/with/a/deep/inherited/root")
	type savedValue struct {
		value   string
		present bool
	}
	original := make(map[string]savedValue)
	for key := range privateTestDirectories {
		value, present := os.LookupEnv(key)
		original[key] = savedValue{value: value, present: present}
	}
	for _, key := range []string{"YOMUX_PRIVATE_TEST_ENV_ROOT", "YOLOMUX_PRIVATE_TEST_ENV_ROOT", "YOMUX_TMUX_SOCKET", "YOLOMUX_TMUX_SOCKET", "PYTHONUSERBASE", "PYTHONDONTWRITEBYTECODE"} {
		value, present := os.LookupEnv(key)
		original[key] = savedValue{value: value, present: present}
	}
	cleanup, err := NewPrivateTestEnvironment()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(cleanup)
	t.Cleanup(func() {
		for key, saved := range original {
			var err error
			if saved.present {
				err = os.Setenv(key, saved.value)
			} else {
				err = os.Unsetenv(key)
			}
			if err != nil {
				t.Errorf("restore %s: %v", key, err)
			}
		}
	})

	tempDir := os.Getenv("TMPDIR")
	base, err := ShortTempBase()
	if err != nil {
		t.Fatal(err)
	}
	expectedPrefix := filepath.Join(base, "ym-")
	if os.Getenv("YOMUX_NATIVE_SHORT_TMPDIR") != "" {
		expectedPrefix = filepath.Join(base, "f")
	}
	if !strings.HasPrefix(tempDir, expectedPrefix) {
		t.Fatalf("private TMPDIR = %q, want a short private path under %q", tempDir, base)
	}
	if _, present := os.LookupEnv("YOMUX_TMUX_SOCKET"); present {
		t.Fatal("private tmux default retained an ambient canonical override")
	}
	if got, want := os.Getenv("YOLOMUX_TMUX_SOCKET"), filepath.Join(filepath.Dir(tempDir), "tmux.sock"); got != want {
		t.Fatalf("private tmux socket = %q, want %q", got, want)
	}
	socket := filepath.Join(tempDir, "TestActivityRouteIncludesEveryGlobalTmuxSession2333904820", "001", "tmux.sock")
	if os.Getenv("YOMUX_NATIVE_SHORT_TMPDIR") != "" {
		// Native tests allocate socket roots through the explicit short-path owner.
		// testing.T.TempDir adds arbitrary test names and is not a socket path contract.
		socket = filepath.Join(ShortTempDir(t), "tmux.sock")
	}
	if len(socket) > 104 {
		t.Fatalf("representative nested tmux socket path is %d bytes (%q), want at most 104", len(socket), socket)
	}
}

func TestSyntheticEnvironmentPrivateRootPrefersCanonicalIncludingEmpty(t *testing.T) {
	for _, canonical := range []string{"/fixture/canonical", ""} {
		t.Run(canonical, func(t *testing.T) {
			t.Setenv("YOMUX_PRIVATE_TEST_ENV_ROOT", canonical)
			t.Setenv("YOLOMUX_PRIVATE_TEST_ENV_ROOT", "/fixture/legacy")
			environment := make(map[string]string)
			for _, entry := range SyntheticEnvironment() {
				key, value, _ := strings.Cut(entry, "=")
				environment[key] = value
			}
			for key, directory := range map[string]string{"HOME": "home", "TMPDIR": "tmp"} {
				want := ""
				if canonical != "" {
					want = filepath.Join(canonical, directory)
				}
				if environment[key] != want {
					t.Fatalf("private %s = %q, want %q", key, environment[key], want)
				}
			}
		})
	}
}

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

func TestSyntheticEnvironmentDropsInheritedBazelSharding(t *testing.T) {
	for _, name := range []string{
		"TEST_SHARD_INDEX", "TEST_TOTAL_SHARDS", "TEST_SHARD_STATUS_FILE",
		"TESTBRIDGE_TEST_ONLY", "TESTBRIDGE_TEST_RUNNER_FAIL_FAST",
	} {
		t.Setenv(name, "inherited-shard-state")
	}
	for _, entry := range SyntheticEnvironment() {
		key, _, ok := strings.Cut(entry, "=")
		if ok && (key == "TEST_SHARD_INDEX" || key == "TEST_TOTAL_SHARDS" || key == "TEST_SHARD_STATUS_FILE" ||
			key == "TESTBRIDGE_TEST_ONLY" || key == "TESTBRIDGE_TEST_RUNNER_FAIL_FAST") {
			t.Fatalf("synthetic subprocess inherited Bazel sharding variable %s", key)
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

func TestShortTempBaseRejectsNonPrivateOrRelativeRoots(t *testing.T) {
	// Keep all fixture writes inside the admitted root even when testing bad inputs.
	directory, err := os.MkdirTemp(os.TempDir(), "short-base-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(directory) })
	for _, value := range []string{"relative", directory} {
		if value == directory {
			if err := os.Chmod(directory, 0o755); err != nil {
				t.Fatal(err)
			}
		}
		t.Setenv("YOMUX_NATIVE_SHORT_TMPDIR", value)
		if _, err := ShortTempBase(); err == nil {
			t.Fatalf("accepted invalid root %q", value)
		}
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOMUX_NATIVE_SHORT_TMPDIR", directory)
	physical, err := filepath.EvalSymlinks(directory)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := ShortTempBase(); err != nil || got != physical {
		t.Fatalf("private root = %q, %v", got, err)
	}
}

func TestShortTempBaseResolvesAncestorAliasesButRejectsSymlinkLeaf(t *testing.T) {
	parent := t.TempDir()
	owned := filepath.Join(parent, "real", "owned")
	if err := os.MkdirAll(owned, 0o700); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(parent, "alias")
	if err := os.Symlink(filepath.Join(parent, "real"), alias); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOMUX_NATIVE_SHORT_TMPDIR", filepath.Join(alias, "owned"))
	physical, err := filepath.EvalSymlinks(owned)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := ShortTempBase(); err != nil || got != physical {
		t.Fatalf("aliased root = %q, %v; want %q", got, err, physical)
	}
	t.Setenv("YOMUX_NATIVE_SHORT_TMPDIR", alias)
	if _, err := ShortTempBase(); err == nil {
		t.Fatal("accepted a symlink as the owned root")
	}
}

func TestSyntheticEnvironmentPreservesProtectedOwnershipAgainstOverrides(t *testing.T) {
	for _, key := range []string{"YOMUX_NATIVE_SHORT_TMPDIR", "YOMUX_BAZEL_PROCESS_SCOPE"} {
		t.Setenv(key, "owned")
		count := 0
		for _, entry := range SyntheticEnvironment(key+"=foreign", key+"=second") {
			name, value, _ := strings.Cut(entry, "=")
			if name == key {
				count++
				if value != "owned" {
					t.Fatalf("ownership %s replaced with %q", key, value)
				}
			}
		}
		if count != 1 {
			t.Fatalf("ownership %s appears %d times", key, count)
		}
	}
}
