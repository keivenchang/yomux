package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/capabilities"
	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestUpdateVersionThresholds(t *testing.T) {
	cases := []struct{ current, target, want string }{
		{"0.10.6", "0.11.0", "minor"}, {"0.10.6", "0.10.7", "patch"}, {"0.10.6", "1.0.0", "major"},
		{"0.10.6", "0.10.6", "none"}, {"0.10.6", "0.10.5", "none"}, {"bad", "1.0.0", "none"},
	}
	for _, tc := range cases {
		if got := versionChangeLevel(tc.current, tc.target); got != tc.want {
			t.Errorf("versionChangeLevel(%q, %q) = %q, want %q", tc.current, tc.target, got, tc.want)
		}
	}
	for _, tc := range []struct {
		level, threshold string
		want             bool
	}{{"patch", "patch", true}, {"minor", "patch", true}, {"patch", "minor", false}, {"major", "major", true}, {"major", "none", false}} {
		if got := updateNotifyLevelAllows(tc.level, tc.threshold); got != tc.want {
			t.Errorf("updateNotifyLevelAllows(%q, %q) = %t", tc.level, tc.threshold, got)
		}
	}
}

func TestUpdateCheckerDisabledPreferenceSkipsFetch(t *testing.T) {
	checker := newUpdateChecker(t.TempDir(), "0.10.6", func() string { return "none" })
	result := checker.Check(context.Background())
	if result.Available || result.Target != "" || result.TargetSHA != "" || result.Error != "" || result.Level != "none" || result.CheckedAt.IsZero() {
		t.Fatalf("disabled check should record a quiet result without fetching: %#v", result)
	}
}

func TestUpdateRoutesKeepStatusReadableAndInstallationAdminOnly(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: admin\n    password: admin-pass\n    role: admin\n  - username: reader\n    password: reader-pass\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: config, capabilities: capabilities.New("test"), updates: &updateChecker{snapshot: updateSnapshot{Available: true, Current: "0.10.6", Target: "0.10.7", TargetSHA: "0123456789012345678901234567890123456789", Branch: "main", Level: "patch", CheckedAt: time.Now()}}, updateJobPath: filepath.Join(config, "self-update-job.json")}
	s.capabilities.Set("core.updates.v1", capabilities.Entry{Available: true})
	status := httptest.NewRequest(http.MethodGet, "/api/update-status", nil)
	status.SetBasicAuth("reader", "reader-pass")
	statusResponse := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.updateStatus)).ServeHTTP(statusResponse, status)
	if statusResponse.Code != http.StatusOK {
		t.Fatalf("readonly update status = %d, want 200", statusResponse.Code)
	}
	var payload map[string]any
	if err := json.Unmarshal(statusResponse.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["available"] != true || payload["target_version"] != "0.10.7" {
		t.Fatalf("unexpected cached update payload: %#v", payload)
	}
	if err := s.saveUpdateJob(selfUpdateJob{ID: "failed-job", State: "failed", Error: "checkout has tracked or untracked changes"}); err != nil {
		t.Fatal(err)
	}
	jobStatusRequest := httptest.NewRequest(http.MethodGet, "/api/self-update/status", nil)
	jobStatusRequest.SetBasicAuth("admin", "admin-pass")
	jobStatusResponse := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.selfUpdateStatus)).ServeHTTP(jobStatusResponse, jobStatusRequest)
	if jobStatusResponse.Code != http.StatusOK {
		t.Fatalf("admin update job status = %d, want 200", jobStatusResponse.Code)
	}
	if err := json.Unmarshal(jobStatusResponse.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	failedJob, _ := payload["job"].(map[string]any)
	if failedJob["state"] != "failed" || failedJob["error"] != "checkout has tracked or untracked changes" {
		t.Fatalf("unexpected update job status envelope: %#v", payload)
	}
	post := httptest.NewRequest(http.MethodPost, "/api/self-update", nil)
	post.SetBasicAuth("reader", "reader-pass")
	denied := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.selfUpdate)).ServeHTTP(denied, post)
	if denied.Code != http.StatusForbidden {
		t.Fatalf("readonly self-update = %d, want 403", denied.Code)
	}
	job := selfUpdateJob{ID: "active-job", State: "preparing", Target: "0.10.7", TargetSHA: "0123456789012345678901234567890123456789", StartedAt: time.Now()}
	if err := s.saveUpdateJob(job); err != nil {
		t.Fatal(err)
	}
	post = httptest.NewRequest(http.MethodPost, "/api/self-update", nil)
	post.SetBasicAuth("admin", "admin-pass")
	accepted := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.selfUpdate)).ServeHTTP(accepted, post)
	if accepted.Code != http.StatusOK {
		t.Fatalf("duplicate admin update status = %d, want 200: %s", accepted.Code, accepted.Body.String())
	}
	if err := json.Unmarshal(accepted.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	active, _ := payload["job"].(map[string]any)
	if active["id"] != "active-job" {
		t.Fatalf("duplicate click did not return active job: %#v", payload)
	}
	if err := os.Remove(s.updateJobPath); err != nil {
		t.Fatal(err)
	}
	post = httptest.NewRequest(http.MethodPost, "/api/self-update", nil)
	post.SetBasicAuth("admin", "admin-pass")
	started := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.selfUpdate)).ServeHTTP(started, post)
	if started.Code != http.StatusOK {
		t.Fatalf("new admin update status = %d, want 200: %s", started.Code, started.Body.String())
	}
	if err := json.Unmarshal(started.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["ok"] != true || payload["restarting"] != true || payload["target"] != "0.10.7" {
		t.Fatalf("unexpected new update acknowledgement: %#v", payload)
	}
	deadline := time.After(time.Second)
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		if completed := s.loadUpdateJob(); completed.State == "failed" {
			break
		}
		select {
		case <-ticker.C:
		case <-deadline:
			t.Fatal("test update worker did not finish")
		}
	}
}

func TestSelfUpdateDryRunDoesNotCreateOrStartJob(t *testing.T) {
	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: admin\n    password: admin-pass\n    role: admin\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: config, capabilities: capabilities.New("test"), updates: &updateChecker{snapshot: updateSnapshot{Available: true, Current: "0.10.6", Target: "0.10.7", TargetSHA: "0123456789012345678901234567890123456789", Branch: "main", Level: "patch", CheckedAt: time.Now()}}, updateJobPath: filepath.Join(config, "self-update-job.json")}
	s.capabilities.Set("core.updates.v1", capabilities.Entry{Available: true})
	request := httptest.NewRequest(http.MethodPost, "/api/self-update?dryrun=1", nil)
	request.SetBasicAuth("admin", "admin-pass")
	response := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.selfUpdate)).ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("dry-run status = %d, want 200: %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["dry_run"] != true || payload["restarting"] != false || payload["target"] != "0.10.7" {
		t.Fatalf("unexpected dry-run response: %#v", payload)
	}
	if _, err := os.Stat(s.updateJobPath); !os.IsNotExist(err) {
		t.Fatalf("dry-run created a job file: stat error = %v", err)
	}
}

func TestSelfUpdateRefusesDirtyAndDivergedCheckouts(t *testing.T) {
	for _, scenario := range []string{"dirty", "diverged", "dirty-diverged"} {
		t.Run(scenario, func(t *testing.T) {
			root := t.TempDir()
			gitRun(t, root, "init", "--initial-branch=main")
			gitRun(t, root, "config", "user.email", "test@example.invalid")
			gitRun(t, root, "config", "user.name", "Fixture")
			if err := os.WriteFile(filepath.Join(root, "base.txt"), []byte("base\n"), 0o600); err != nil {
				t.Fatal(err)
			}
			gitRun(t, root, "add", "base.txt")
			gitRun(t, root, "commit", "-m", "base")
			gitRun(t, root, "branch", "update-target")
			gitRun(t, root, "checkout", "update-target")
			if err := os.WriteFile(filepath.Join(root, "target.txt"), []byte("target\n"), 0o600); err != nil {
				t.Fatal(err)
			}
			gitRun(t, root, "add", "target.txt")
			gitRun(t, root, "commit", "-m", "upstream target")
			target := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD"))
			gitRun(t, root, "checkout", "main")
			if scenario == "diverged" || scenario == "dirty-diverged" {
				if err := os.WriteFile(filepath.Join(root, "local.txt"), []byte("local\n"), 0o600); err != nil {
					t.Fatal(err)
				}
				gitRun(t, root, "add", "local.txt")
				gitRun(t, root, "commit", "-m", "local divergent commit")
			}
			headBefore := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD"))
			canonicalRoot, err := filepath.EvalSymlinks(root)
			if err != nil {
				t.Fatal(err)
			}
			if scenario == "dirty" || scenario == "dirty-diverged" {
				if err := os.WriteFile(filepath.Join(root, "untracked.txt"), []byte("keep\n"), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			config := filepath.Join(t.TempDir(), "config")
			jobPath := filepath.Join(config, "self-update-job.json")
			job := selfUpdateJob{ID: scenario, State: "preparing", Target: "0.10.7", TargetSHA: target, StartedAt: time.Now()}
			s := &server{root: canonicalRoot, config: config, updateJobPath: jobPath}
			s.runSelfUpdate(job)
			result := s.loadUpdateJob()
			if result.State != "failed" {
				t.Fatalf("job state = %q, want failed: %#v", result.State, result)
			}
			for _, wantError := range map[string][]string{
				"dirty":          {"Auto-update needs a clean checkout", "Commit or stash the local edits"},
				"diverged":       {"branch because it has a local commit", "clean 0.10.7 checkout"},
				"dirty-diverged": {"checkout because it has local work", "Save that work", "clean 0.10.7 checkout"},
			}[scenario] {
				if !strings.Contains(result.Error, wantError) {
					t.Fatalf("job error = %q, want it to contain %q", result.Error, wantError)
				}
			}
			if headAfter := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD")); headAfter != headBefore {
				t.Fatalf("refusal changed HEAD from %s to %s", headBefore, headAfter)
			}
			if scenario == "dirty" || scenario == "dirty-diverged" {
				if content, err := os.ReadFile(filepath.Join(root, "untracked.txt")); err != nil || string(content) != "keep\n" {
					t.Fatalf("dirty untracked file changed or disappeared: %q, %v", content, err)
				}
			}
		})
	}
}

func TestSelfUpdatePreparesBeforeFastForwardAndLaunchesManagedInstance(t *testing.T) {
	for _, scenario := range []string{"prepared", "installed", "build-failure", "native-incompatible"} {
		t.Run(scenario, func(t *testing.T) {
			root := t.TempDir()
			gitRun(t, root, "init", "--initial-branch=main")
			gitRun(t, root, "config", "user.email", "test@example.invalid")
			gitRun(t, root, "config", "user.name", "Fixture")
			write := func(relative, content string, mode os.FileMode) {
				t.Helper()
				path := filepath.Join(root, relative)
				if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte(content), mode); err != nil {
					t.Fatal(err)
				}
			}
			write("version.yaml", "version: \"0.10.6\"\n", 0o600)
			write("static/yomux.js", "old-js\n", 0o600)
			write("static/yomux.css", "old-css\n", 0o600)
			gitRun(t, root, "add", ".")
			gitRun(t, root, "commit", "-m", "running version")
			baseSHA := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD"))

			write("version.yaml", "version: \"0.10.7\"\n", 0o600)
			buildScript := "#!/bin/sh\nset -eu\n"
			if scenario == "build-failure" {
				buildScript += "echo fixture build failure >&2\nexit 1\n"
			} else {
				buildScript += "mkdir -p bazel-bin/cmd/yomux-go/yomux-go_\n"
				buildScript += "cat > bazel-bin/cmd/yomux-go/yomux-go_/yomux-go <<'EOF'\n#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then printf 'yomux-go 0.10.7\\n'; exit 0; fi\nif [ \"$1\" = \"--operator\" ]; then printf '0.10.7\\n'; exit 0; fi\nif [ \"$1\" = \"--internal-self-update-supervisor\" ]; then YOMUX_SELF_UPDATE_TEST_WORKER=1 YOMUX_SELF_UPDATE_TEST_SPEC=\"$2\" exec \"$YOMUX_SELF_UPDATE_TEST_EXECUTABLE\" -test.run=^TestSelfUpdateSupervisorSubprocessHelper$; fi\nexit 64\nEOF\n"
				buildScript += "chmod 700 bazel-bin/cmd/yomux-go/yomux-go_/yomux-go\n"
			}
			if scenario == "native-incompatible" {
				buildScript = strings.ReplaceAll(buildScript, "if [ \"$1\" = \"--operator\" ]; then printf '0.10.7\\n'; exit 0; fi", "if [ \"$1\" = \"--operator\" ]; then exit 64; fi")
			}
			write("tools/build_go.sh", buildScript, 0o700)
			write("bazel-bin/tools/staticgen/staticgen_/staticgen", "#!/bin/sh\nset -eu\n[ \"$1\" = --source-root ]\n[ \"$3\" = --output-directory ]\nmkdir -p \"$4\"\nprintf 'new-js\\n' > \"$4/yomux.js\"\nprintf 'new-css\\n' > \"$4/yomux.css\"\n", 0o700)
			write("boot.sh", "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$YOMUX_UPDATE_TEST_ARGS\"\nprintf '%s' \"$YOMUX_GO_BINARY\" > \"$YOMUX_UPDATE_TEST_BINARY\"\n", 0o700)
			write("tools/install.sh", `#!/bin/sh
set -eu
if [ "$1" = --help ]; then printf 'Usage: --prefix PREFIX --source-root CHECKOUT\n'; exit 0; fi
[ "$1" = --prefix ]
[ "$3" = --source-root ]
[ "$4" = "$YOMUX_UPDATE_TEST_SOURCE" ]
app="$2/lib/yomux"
mkdir -p "$app/static"
cp "$YOMUX_GO_BINARY" "$app/yomux-go"
cp boot.sh "$app/boot.sh"
cp static/yomux.js static/yomux.css "$app/static/"
mkdir -p "$2/bin"
printf '#!/bin/sh\nexec "%s/yomux-go" "$@"\n' "$app" > "$2/bin/yomux"
chmod 700 "$2/bin/yomux"
`, 0o700)
			gitRun(t, root, "add", ".")
			gitRun(t, root, "commit", "-m", "upstream version")
			targetSHA := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD"))
			gitRun(t, root, "checkout", baseSHA)

			config := filepath.Join(t.TempDir(), "config")
			records := t.TempDir()
			argsPath := filepath.Join(records, "args")
			binaryPath := filepath.Join(records, "binary")
			t.Setenv("YOMUX_UPDATE_TEST_ARGS", argsPath)
			t.Setenv("YOMUX_UPDATE_TEST_BINARY", binaryPath)
			t.Setenv("YOMUX_SELF_UPDATE_TEST_EXECUTABLE", os.Args[0])
			t.Setenv("YOMUX_UPDATE_TEST_SOURCE", root)
			runtimeRoot := root
			if scenario == "installed" {
				prefix := t.TempDir()
				runtimeRoot = filepath.Join(prefix, "lib", "yomux")
				if err := os.MkdirAll(runtimeRoot, 0o700); err != nil {
					t.Fatal(err)
				}
				if err := recordNativeInstallSource(runtimeRoot, root, prefix); err != nil {
					t.Fatal(err)
				}
				// The Go controller may use Python elsewhere; this update's entire
				// subprocess graph receives only the native production tools.
				path := t.TempDir()
				for _, name := range []string{"bash", "sh", "git", "tar", "mkdir", "chmod", "cat", "cp"} {
					command, err := exec.LookPath(name)
					if err != nil {
						t.Fatal(err)
					}
					if err := copyAtomic(command, filepath.Join(path, name), 0o700); err != nil {
						t.Fatal(err)
					}
				}
				t.Setenv("PATH", path)
				if _, err := exec.LookPath("python3"); err == nil {
					t.Fatal("installed update PATH contains Python")
				}
			}
			job := selfUpdateJob{ID: scenario, State: "preparing", Target: "0.10.7", TargetSHA: targetSHA, StartedAt: time.Now()}
			s := &server{root: runtimeRoot, config: config, port: 7110, httpMode: true, updateJobPath: filepath.Join(config, "self-update-job.json")}
			// The detached supervisor still writes the job after boot records its
			// invocation. Retire that writer before TempDir cleanup, even on failure.
			t.Cleanup(func() {
				deadline := time.NewTimer(5 * time.Second)
				defer deadline.Stop()
				ticker := time.NewTicker(5 * time.Millisecond)
				defer ticker.Stop()
				for {
					current := s.loadUpdateJob()
					if current.ID == job.ID && (current.State == "complete" || current.State == "failed") {
						if scenario != "build-failure" && scenario != "native-incompatible" && current.State != "complete" {
							t.Errorf("managed launcher did not complete: %#v", current)
						}
						return
					}
					select {
					case <-ticker.C:
					case <-deadline.C:
						t.Error("self-update writer did not finish before fixture cleanup")
						return
					}
				}
			})
			s.runSelfUpdate(job)
			result := s.loadUpdateJob()
			if scenario == "build-failure" || scenario == "native-incompatible" {
				expectedError := "fixture build failure"
				if scenario == "native-incompatible" {
					expectedError = "lacks compatible native launch support"
				}
				if result.State != "failed" || !strings.Contains(result.Error, expectedError) {
					t.Fatalf("build failure was not reported: %#v", result)
				}
				if actual := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD")); actual != baseSHA {
					t.Fatalf("failed preparation activated %s, want original %s", actual, baseSHA)
				}
				for path, want := range map[string]string{"static/yomux.js": "old-js\n", "static/yomux.css": "old-css\n"} {
					if data, err := os.ReadFile(filepath.Join(root, path)); err != nil || string(data) != want {
						t.Fatalf("failed preparation changed %s to %q, %v; want %q", path, data, err, want)
					}
				}
				if _, err := os.Stat(argsPath); !os.IsNotExist(err) {
					t.Fatalf("failed preparation started managed launcher: %v", err)
				}
				return
			}
			if (result.State != "restarting" && result.State != "complete") || result.TargetSHA != targetSHA {
				t.Fatalf("prepared update did not persist restart state: %#v", result)
			}
			if result.LauncherLog != filepath.Join(config, "self-update", targetSHA, scenario, "managed-launcher.log") {
				t.Fatalf("prepared update did not persist launcher log path: %#v", result)
			}
			if actual := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD")); actual != targetSHA {
				t.Fatalf("activation HEAD = %s, want pinned target %s", actual, targetSHA)
			}
			for path, want := range map[string]string{"static/yomux.js": "new-js\n", "static/yomux.css": "new-css\n"} {
				if data, err := os.ReadFile(filepath.Join(runtimeRoot, path)); err != nil || string(data) != want {
					t.Fatalf("activated %s = %q, %v; want %q", path, data, err, want)
				}
			}
			ticker := time.NewTicker(5 * time.Millisecond)
			defer ticker.Stop()
			timeout := time.NewTimer(5 * time.Second)
			defer timeout.Stop()
			for {
				_, argsErr := os.Stat(argsPath)
				_, binaryErr := os.Stat(binaryPath)
				if argsErr == nil && binaryErr == nil {
					break
				}
				select {
				case <-ticker.C:
				case <-timeout.C:
					t.Fatal("managed launcher did not record its invocation")
				}
			}
			args, err := os.ReadFile(argsPath)
			if err != nil {
				t.Fatal(err)
			}
			for _, want := range []string{"--force", "--backend", "go", "--port", "7110", "--http"} {
				if !strings.Contains("\n"+string(args)+"\n", "\n"+want+"\n") {
					t.Fatalf("managed launcher args %q do not contain %q", args, want)
				}
			}
			preparedBinary, err := os.ReadFile(binaryPath)
			if err != nil {
				t.Fatal(err)
			}
			if scenario == "installed" && string(preparedBinary) != filepath.Join(runtimeRoot, "yomux-go") {
				t.Fatalf("installed restart did not use its refreshed native binary: %q", preparedBinary)
			}
			if scenario == "installed" {
				prefix := filepath.Dir(filepath.Dir(runtimeRoot))
				output, err := exec.Command(filepath.Join(prefix, "bin", "yomux"), "--version").CombinedOutput()
				if err != nil || strings.TrimSpace(string(output)) != "yomux-go 0.10.7" {
					t.Fatalf("future installed invocation did not use the new payload: %q, %v", output, err)
				}
				source, installation, err := nativeUpdateSource(runtimeRoot)
				if err != nil || source != root || installation == nil || installation.Prefix != prefix {
					t.Fatalf("update lost installation provenance: %q, %#v, %v", source, installation, err)
				}
			}
			if scenario != "installed" && !strings.HasSuffix(strings.TrimSpace(string(preparedBinary)), filepath.Join("self-update", targetSHA, scenario, "yomux-go")) {
				t.Fatalf("managed launcher binary = %q", preparedBinary)
			}
			if _, err := os.Stat(filepath.Join(config, "self-update", targetSHA, scenario, "yomux-go")); err != nil {
				t.Fatalf("prepared target binary missing: %v", err)
			}
			for _, path := range []string{
				s.updateJobPath,
				filepath.Join(config, "self-update", targetSHA, scenario, "self-update-supervisor.json"),
				filepath.Join(config, "self-update", targetSHA, scenario, "managed-launcher.log"),
			} {
				info, err := os.Stat(path)
				if err != nil {
					t.Fatalf("stat persistent update file %s: %v", path, err)
				}
				if mode := info.Mode().Perm(); mode != 0o600 {
					t.Fatalf("persistent update file %s mode = %04o; want 0600", path, mode)
				}
			}
			launcherLog, err := os.ReadFile(filepath.Join(config, "self-update", targetSHA, scenario, "managed-launcher.log"))
			if err != nil || !strings.Contains(string(launcherLog), "self-update subprocess helper entered") {
				t.Fatalf("prepared supervisor child did not enter its test helper: %q, %v", launcherLog, err)
			}
		})
	}
}

func TestSelfUpdateRecoveryVerifiesPinnedTargetAndAwaitsManagedReadiness(t *testing.T) {
	root := t.TempDir()
	gitRun(t, root, "init", "--initial-branch=main")
	gitRun(t, root, "config", "user.email", "test@example.invalid")
	gitRun(t, root, "config", "user.name", "Fixture")
	if err := os.WriteFile(filepath.Join(root, "version.yaml"), []byte("version: \"0.10.6\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	gitRun(t, root, "add", "version.yaml")
	gitRun(t, root, "commit", "-m", "running version")
	targetSHA := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD"))
	s := &server{root: root, config: t.TempDir(), updateJobPath: filepath.Join(t.TempDir(), "self-update-job.json")}
	if err := s.saveUpdateJob(selfUpdateJob{ID: "recovery", State: "restarting", Target: version, TargetSHA: targetSHA, StartedAt: time.Now()}); err != nil {
		t.Fatal(err)
	}
	s.recoverSelfUpdateJob()
	job := s.loadUpdateJob()
	if job.State != "restarting" || job.Error != "" || job.TargetSHA != targetSHA || job.Progress != "New instance started; awaiting managed readiness" {
		t.Fatalf("pinned running target was not left awaiting managed readiness: %#v", job)
	}
	if err := s.saveUpdateJob(selfUpdateJob{ID: "wrong-target", State: "restarting", Target: "9.9.9", TargetSHA: targetSHA, StartedAt: time.Now()}); err != nil {
		t.Fatal(err)
	}
	s.recoverSelfUpdateJob()
	if job = s.loadUpdateJob(); job.State != "failed" || !strings.Contains(job.Error, "expected version 9.9.9") {
		t.Fatalf("recovery did not report a different version target: %#v", job)
	}
}

func TestUpdateCheckerPinsManifestAndRecoversOffline(t *testing.T) {
	root := t.TempDir()
	remote := filepath.Join(root, "origin.git")
	repo := filepath.Join(root, "repo")
	gitRun(t, root, "init", "--bare", "--initial-branch=main", remote)
	if err := os.Mkdir(repo, 0o700); err != nil {
		t.Fatal(err)
	}
	gitRun(t, repo, "init", "--initial-branch=main")
	gitRun(t, repo, "config", "user.email", "test@example.invalid")
	gitRun(t, repo, "config", "user.name", "Fixture")
	if err := os.WriteFile(filepath.Join(repo, "version.yaml"), []byte("version: \"0.10.6\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	gitRun(t, repo, "add", "version.yaml")
	gitRun(t, repo, "commit", "-m", "base")
	gitRun(t, repo, "remote", "add", "origin", remote)
	gitRun(t, repo, "push", "origin", "main")
	if err := os.WriteFile(filepath.Join(repo, "version.yaml"), []byte("version: \"0.11.0\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	gitRun(t, repo, "add", "version.yaml")
	gitRun(t, repo, "commit", "-m", "manifest bump")
	gitRun(t, repo, "push", "origin", "main")

	prefix := t.TempDir()
	installedRoot := filepath.Join(prefix, "lib", "yomux")
	if err := os.MkdirAll(installedRoot, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := recordNativeInstallSource(installedRoot, repo, prefix); err != nil {
		t.Fatal(err)
	}
	checker := newUpdateChecker(installedRoot, "0.10.6", func() string { return "minor" })
	var wait sync.WaitGroup
	results := make(chan updateSnapshot, 2)
	for range 2 {
		wait.Add(1)
		go func() { defer wait.Done(); results <- checker.Check(context.Background()) }()
	}
	wait.Wait()
	close(results)
	for result := range results {
		if !result.Available || result.Target != "0.11.0" || result.Level != "minor" || len(result.TargetSHA) != 40 || result.Error != "" {
			t.Fatalf("unexpected update result: %#v", result)
		}
	}
	if err := os.Rename(remote, remote+".offline"); err != nil {
		t.Fatal(err)
	}
	offline := checker.Check(context.Background())
	if offline.Error == "" || offline.Available {
		t.Fatalf("offline result was not surfaced: %#v", offline)
	}
	if err := os.Rename(remote+".offline", remote); err != nil {
		t.Fatal(err)
	}
	recovered := checker.Check(context.Background())
	if recovered.Error != "" || !recovered.Available || recovered.Target != "0.11.0" {
		t.Fatalf("checker did not recover: %#v", recovered)
	}
}

func TestSelfUpdateSupervisorSubprocessHelper(t *testing.T) {
	if os.Getenv("YOMUX_SELF_UPDATE_TEST_PARENT") != "1" {
		return
	}
	environment := environmentWithValue(os.Environ(), "YOMUX_SELF_UPDATE_TEST_WORKER", "1")
	environment = environmentWithValue(environment, "YOMUX_SELF_UPDATE_TEST_SPEC", os.Getenv("YOMUX_SELF_UPDATE_TEST_SPEC"))
	if err := startDetachedCommand(os.Args[0], []string{"-test.run=^TestSelfUpdateSupervisorSubprocessHelper$"}, environment, os.Getenv("YOMUX_SELF_UPDATE_TEST_LOG")); err != nil {
		t.Fatal(err)
	}
}

func TestSelfUpdateSupervisorPersistsFailureAfterParentExit(t *testing.T) {
	directory := t.TempDir()
	jobPath := filepath.Join(directory, "self-update-job.json")
	logPath := filepath.Join(directory, "managed-launcher.log")
	gatePath := filepath.Join(directory, "release-launcher-gate")
	bootPath := filepath.Join(directory, "boot.sh")
	boot := "#!/bin/sh\nwhile [ ! -f \"$1\" ]; do :; done\nprintf 'fixture launch failed\\n' >&2\nexit 23\n"
	if err := os.WriteFile(bootPath, []byte(boot), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := (&server{updateJobPath: jobPath}).saveUpdateJob(selfUpdateJob{ID: "detached-failure", State: "restarting", Target: "0.10.7", TargetSHA: strings.Repeat("a", 40)}); err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(directory, "supervisor.json")
	spec := selfUpdateSupervisorSpec{JobPath: jobPath, JobID: "detached-failure", Root: directory, PreparedBinary: filepath.Join(directory, "prepared-yomux-go"), BootArgs: []string{gatePath}, LogPath: logPath}
	if err := writeSelfUpdateSupervisorSpec(specPath, spec); err != nil {
		t.Fatal(err)
	}
	parent := exec.Command(os.Args[0], "-test.run=^TestSelfUpdateSupervisorSubprocessHelper$")
	parent.Env = testsupport.SyntheticEnvironment(
		"YOMUX_SELF_UPDATE_TEST_PARENT=1",
		"YOMUX_SELF_UPDATE_TEST_SPEC="+specPath,
		"YOMUX_SELF_UPDATE_TEST_LOG="+logPath,
	)
	if output, err := parent.CombinedOutput(); err != nil {
		t.Fatalf("detached supervisor parent failed: %v: %s", err, output)
	}
	if job := loadSelfUpdateJob(jobPath); job.State != "restarting" {
		t.Fatalf("supervisor should remain pending before gate release, got %#v", job)
	}
	if err := os.WriteFile(gatePath, []byte("continue\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		job := loadSelfUpdateJob(jobPath)
		if job.State == "failed" {
			if job.ID != "detached-failure" || job.LauncherLog != logPath || !strings.Contains(job.Error, "exit status 23") || !strings.Contains(job.Error, logPath) {
				t.Fatalf("detached launcher failure lacks identity or diagnostics: %#v", job)
			}
			log, err := os.ReadFile(logPath)
			if err != nil || !strings.Contains(string(log), "fixture launch failed") {
				t.Fatalf("launcher diagnostic was not retained: %q, %v", log, err)
			}
			if !strings.Contains(string(log), "self-update subprocess helper entered") {
				t.Fatalf("detached worker did not enter its test helper: %q", log)
			}
			return
		}
		select {
		case <-ticker.C:
		case <-deadline.C:
			t.Fatalf("detached supervisor did not persist failure after parent exit: %#v", job)
		}
	}
}

func TestSelfUpdateSupervisorCompletesAfterLauncherReadiness(t *testing.T) {
	directory := t.TempDir()
	jobPath := filepath.Join(directory, "self-update-job.json")
	logPath := filepath.Join(directory, "managed-launcher.log")
	gatePath := filepath.Join(directory, "release-launcher-gate")
	bootPath := filepath.Join(directory, "boot.sh")
	boot := "#!/bin/sh\nwhile [ ! -f \"$1\" ]; do :; done\nexit 0\n"
	if err := os.WriteFile(bootPath, []byte(boot), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := (&server{updateJobPath: jobPath}).saveUpdateJob(selfUpdateJob{ID: "detached-success", State: "restarting", Target: "0.10.7", TargetSHA: strings.Repeat("c", 40)}); err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(directory, "supervisor.json")
	spec := selfUpdateSupervisorSpec{JobPath: jobPath, JobID: "detached-success", Root: directory, PreparedBinary: filepath.Join(directory, "prepared-yomux-go"), BootArgs: []string{gatePath}, LogPath: logPath}
	if err := writeSelfUpdateSupervisorSpec(specPath, spec); err != nil {
		t.Fatal(err)
	}
	parent := exec.Command(os.Args[0], "-test.run=^TestSelfUpdateSupervisorSubprocessHelper$")
	parent.Env = testsupport.SyntheticEnvironment(
		"YOMUX_SELF_UPDATE_TEST_PARENT=1",
		"YOMUX_SELF_UPDATE_TEST_SPEC="+specPath,
		"YOMUX_SELF_UPDATE_TEST_LOG="+logPath,
	)
	if output, err := parent.CombinedOutput(); err != nil {
		t.Fatalf("detached supervisor parent failed: %v: %s", err, output)
	}
	if job := loadSelfUpdateJob(jobPath); job.State != "restarting" {
		t.Fatalf("job completed before managed launcher readiness: %#v", job)
	}
	if err := os.WriteFile(gatePath, []byte("continue\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		job := loadSelfUpdateJob(jobPath)
		if job.State == "complete" {
			if job.ID != "detached-success" || !strings.Contains(job.Progress, "passed managed readiness") || job.Error != "" {
				t.Fatalf("managed launcher success lacks completion details: %#v", job)
			}
			log, err := os.ReadFile(logPath)
			if err != nil || !strings.Contains(string(log), "self-update subprocess helper entered") {
				t.Fatalf("detached worker did not enter its test helper: %q, %v", log, err)
			}
			return
		}
		select {
		case <-ticker.C:
		case <-deadline.C:
			t.Fatalf("detached supervisor did not complete after readiness: %#v", job)
		}
	}
}

func TestSelfUpdateLauncherFailureIsGuardedAndSerialized(t *testing.T) {
	path := filepath.Join(t.TempDir(), "self-update-job.json")
	spec := selfUpdateSupervisorSpec{JobPath: path, JobID: "same-job", LogPath: "/logs/same-job.log"}
	if err := saveSelfUpdateJob(path, selfUpdateJob{ID: spec.JobID, State: "complete", Progress: "verified"}); err != nil {
		t.Fatal(err)
	}
	if err := recordSelfUpdateLauncherFailure(spec, errors.New("late launcher failure")); err != nil {
		t.Fatal(err)
	}
	if err := recordSelfUpdateLauncherSuccess(spec); err != nil {
		t.Fatal(err)
	}
	if job := loadSelfUpdateJob(path); job.State != "complete" || job.Progress != "verified" {
		t.Fatalf("late supervisor transition overwrote completed job: %#v", job)
	}
	if err := saveSelfUpdateJob(path, selfUpdateJob{ID: "newer-job", State: "preparing", Progress: "new target"}); err != nil {
		t.Fatal(err)
	}
	if err := recordSelfUpdateLauncherFailure(spec, errors.New("stale launcher failure")); err != nil {
		t.Fatal(err)
	}
	if err := recordSelfUpdateLauncherSuccess(spec); err != nil {
		t.Fatal(err)
	}
	if job := loadSelfUpdateJob(path); job.ID != "newer-job" || job.State != "preparing" {
		t.Fatalf("stale supervisor transition overwrote newer job: %#v", job)
	}
	if err := saveSelfUpdateJob(path, selfUpdateJob{ID: spec.JobID, State: "restarting"}); err != nil {
		t.Fatal(err)
	}
	var wait sync.WaitGroup
	wait.Add(2)
	go func() {
		defer wait.Done()
		if err := recordSelfUpdateLauncherFailure(spec, errors.New("launcher exit status 23")); err != nil {
			t.Errorf("record failure: %v", err)
		}
	}()
	go func() {
		defer wait.Done()
		if err := recordSelfUpdateLauncherSuccess(spec); err != nil {
			t.Errorf("record launcher success: %v", err)
		}
	}()
	wait.Wait()
	job := loadSelfUpdateJob(path)
	if job.State != "complete" && job.State != "failed" {
		t.Fatalf("concurrent terminal transition left a nonterminal job: %#v", job)
	}
	if job.State == "complete" && job.Error != "" {
		t.Fatalf("completed job retained failure diagnostics: %#v", job)
	}
	if job.State == "failed" && !strings.Contains(job.Error, "launcher exit status 23") {
		t.Fatalf("failed job lost launcher diagnostic: %#v", job)
	}
}

func TestSelfUpdateSupervisorStartFailureIsPersisted(t *testing.T) {
	directory := t.TempDir()
	jobPath := filepath.Join(directory, "self-update-job.json")
	logPath := filepath.Join(directory, "managed-launcher.log")
	s := &server{updateJobPath: jobPath}
	job := selfUpdateJob{ID: "start-failure", State: "preparing", Target: "0.10.7", TargetSHA: strings.Repeat("b", 40)}
	err := launchManagedSelfUpdateSupervisor(s, job, filepath.Join(directory, "missing-binary"), filepath.Join(directory, "supervisor.json"), logPath)
	if err == nil || !strings.Contains(err.Error(), "could not start managed-launch supervisor") {
		t.Fatalf("expected explicit supervisor start error, got %v", err)
	}
	result := s.loadUpdateJob()
	if result.ID != job.ID || result.State != "failed" || result.LauncherLog != logPath || !strings.Contains(result.Error, "no such file") {
		t.Fatalf("supervisor start failure was not persisted with diagnostics: %#v", result)
	}
	if info, statErr := os.Stat(jobPath); statErr != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("persisted self-update job mode = %v, %v; want 0600", info, statErr)
	}
}

func gitRun(t *testing.T, dir string, args ...string) string {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", dir}, args...)...)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v: %s", args, err, output)
	}
	return string(output)
}

func TestSelfUpdatePreservesEffectiveLaunchSettings(t *testing.T) {
	s := &server{port: 48123, host: "127.0.0.1", config: "/private/config", statsPath: "/private/stats.sqlite", tlsCertificate: "/private/cert.pem", tlsKey: "/private/key.pem"}
	args := s.selfUpdateBootArgs()
	want := []string{"--force", "--backend", "go", "--port", "48123", "--host", "127.0.0.1", "--config", "/private/config", "--stats", "/private/stats.sqlite", "--no-dang", "--cert", "/private/cert.pem", "--key", "/private/key.pem"}
	if !reflect.DeepEqual(args, want) {
		t.Fatalf("restart %v, want %v", args, want)
	}
	s.httpMode, s.dangerouslyYolo = true, true
	args = s.selfUpdateBootArgs()
	if slices.Contains(args, "--no-dang") || slices.Contains(args, "--cert") || !slices.Contains(args, "--http") {
		t.Fatalf("HTTP restart changed mode: %v", args)
	}
}
