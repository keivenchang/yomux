package testsupport

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

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
	if !strings.HasPrefix(tempDir, "/tmp/ym-") {
		t.Fatalf("private TMPDIR = %q, want a short private path under /tmp", tempDir)
	}
	if _, present := os.LookupEnv("YOMUX_TMUX_SOCKET"); present {
		t.Fatal("private tmux default retained an ambient canonical override")
	}
	if got, want := os.Getenv("YOLOMUX_TMUX_SOCKET"), filepath.Join(filepath.Dir(tempDir), "tmux.sock"); got != want {
		t.Fatalf("private tmux socket = %q, want %q", got, want)
	}
	socket := filepath.Join(tempDir, "TestActivityRouteIncludesEveryGlobalTmuxSession2333904820", "001", "tmux.sock")
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
