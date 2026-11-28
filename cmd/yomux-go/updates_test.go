package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"syscall"
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
	retry := httptest.NewRequest(http.MethodPost, "/api/update-status", nil)
	retry.SetBasicAuth("reader", "reader-pass")
	retryDenied := httptest.NewRecorder()
	s.protected(http.HandlerFunc(s.updateStatus)).ServeHTTP(retryDenied, retry)
	if retryDenied.Code != http.StatusForbidden {
		t.Fatalf("readonly retry = %d", retryDenied.Code)
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
	if started.Code != http.StatusConflict {
		t.Fatalf("unverified build accepted: %d %s", started.Code, started.Body.String())
	}
	if _, err := os.Stat(s.updateJobPath); !os.IsNotExist(err) {
		t.Fatalf("unverified upgrade created job: %v", err)
	}

}

func TestSelfUpdateDryRunDoesNotClaimAnUnverifiedBuild(t *testing.T) {
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
	if response.Code != http.StatusConflict {
		t.Fatalf("dry-run status = %d, want 409: %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["ok"] != false || payload["dry_run"] != true || payload["restarting"] != false {
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
	for _, scenario := range []string{"prepared", "installed", "build-failure", "native-incompatible", "changed-during-build", "artifact-tampered", "dependency-tampered", "extra-dependency", "checkout-edited"} {
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
				buildScript += "cat > bazel-bin/cmd/yomux-go/yomux-go_/yomux-go <<'EOF'\n#!/bin/sh\nif [ \"$1\" = --operator ] && [ \"$2\" = setup-assets ]; then exit 82; fi\nif [ \"$1\" = \"--version\" ]; then printf 'yomux-go 0.10.7\\n'; exit 0; fi\nif [ \"$1\" = \"--operator\" ]; then printf '0.10.7\\n'; exit 0; fi\nif [ \"$1\" = \"--internal-self-update-supervisor\" ]; then YOMUX_SELF_UPDATE_TEST_WORKER=1 YOMUX_SELF_UPDATE_TEST_SPEC=\"$2\" exec \"$YOMUX_SELF_UPDATE_TEST_EXECUTABLE\" -test.run=^TestSelfUpdateSupervisorSubprocessHelper$; fi\nexit 64\nEOF\n"
				buildScript += "chmod 700 bazel-bin/cmd/yomux-go/yomux-go_/yomux-go\n"
			}
			if scenario == "native-incompatible" {
				buildScript = strings.ReplaceAll(buildScript, "if [ \"$1\" = \"--operator\" ]; then printf '0.10.7\\n'; exit 0; fi", "if [ \"$1\" = \"--operator\" ]; then printf 'native verification trace payload\\nnative launch probe rejected\\n' >&2; exit 64; fi")
			}
			if scenario == "changed-during-build" {
				buildScript += "printf 'local edit' > \"$YOMUX_UPDATE_TEST_SOURCE/local.txt\"\n"
			}
			write("tools/build_go.sh", buildScript, 0o700)
			write("bazel-bin/tools/staticgen/staticgen_/staticgen", "#!/bin/sh\nset -eu\n[ \"$1\" = --source-root ]\n[ \"$3\" = --output-directory ]\nmkdir -p \"$4/vendor\" \"$4/fonts\" \"$4/locales\"\nprintf 'new-js\\n' > \"$4/yomux.js\"\nprintf 'new-css\\n' > \"$4/yomux.css\"\nprintf 'dependency\\n' > \"$4/vendor/fixture.js\"\nprintf 'font\\n' > \"$4/fonts/fixture.woff2\"\nprintf '{}\\n' > \"$4/locales/en.json\"\n", 0o700)
			write("boot.sh", "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$YOMUX_UPDATE_TEST_ARGS\"\nprintf '%s' \"$YOMUX_GO_BINARY\" > \"$YOMUX_UPDATE_TEST_BINARY\"\n", 0o700)
			write("tools/install.sh", `#!/bin/sh
set -eu
if [ "$1" = --help ]; then printf 'Usage: --prefix PREFIX --source-root CHECKOUT\n'; exit 0; fi
[ "$1" = --prefix ]
[ "$3" = --source-root ]
[ "$4" = "$YOMUX_UPDATE_TEST_SOURCE" ]
[ "$YOMUX_INSTALL_PREPARED" = 1 ]
app="$2/lib/yomux"
mkdir -p "$app/static"
cp "$YOMUX_GO_BINARY" "$app/yomux-go"
cp boot.sh "$app/boot.sh"
cp -R static/. "$app/static/"
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
			t.Setenv("GORACE", testsupport.GoHelperProcessRaceOptions(os.Getenv("GORACE")))
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
					// macOS platform executables must run from their signed location.
					wrapper := "#!/bin/sh\nexec " + shellQuote(command) + " \"$@\"\n"
					if err := testsupport.WriteExecutable(filepath.Join(path, name), []byte(wrapper), 0o700); err != nil {
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
					if current.State == "idle" {
						return
					}
					if current.ID == job.ID && (current.State == "complete" || current.State == "failed") {
						if (scenario == "prepared" || scenario == "installed") && current.State != "complete" {
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
			if scenario == "prepared" {
				cwd, err := os.Getwd()
				if err != nil {
					t.Fatal(err)
				}
				s.config, err = filepath.Rel(cwd, config)
				if err != nil {
					t.Fatal(err)
				}
			}
			prepared, prepareErr := s.prepareSelfUpdate(context.Background(), job)
			s.config = config
			if got := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD")); got != baseSHA {
				t.Fatalf("preflight changed HEAD: %s", got)
			}
			if data, err := os.ReadFile(filepath.Join(runtimeRoot, "static", "yomux.js")); scenario != "installed" && (err != nil || string(data) != "old-js\n") {
				t.Fatalf("preflight changed live assets: %q %v", data, err)
			}
			if _, err := os.Stat(argsPath); !os.IsNotExist(err) {
				t.Fatal("preflight launched server")
			}
			if _, err := os.Stat(s.updateJobPath); !os.IsNotExist(err) {
				t.Fatal("preflight created installation job")
			}
			if scenario == "changed-during-build" {
				if prepareErr == nil || !strings.Contains(prepareErr.Error(), "checkout changed") {
					t.Fatalf("stale preflight accepted: %v", prepareErr)
				}
				return
			}
			if scenario == "build-failure" || scenario == "native-incompatible" {
				if prepareErr == nil {
					t.Fatal("broken candidate passed preflight")
				}
				if scenario == "native-incompatible" {
					data, err := os.ReadFile(filepath.Join(config, "self-update", targetSHA, job.ID, "build.log"))
					if err != nil || !strings.Contains(string(data), "native verification trace payload") {
						t.Fatalf("verification output missing from private log: %v %s", err, data)
					}
					if strings.Contains(prepareErr.Error(), "trace payload") || !strings.Contains(prepareErr.Error(), "native launch probe rejected") {
						t.Fatalf("verification notice is not a short reason: %v", prepareErr)
					}
				}
			} else {
				if prepareErr != nil {
					t.Fatal(prepareErr)
				}
				job.prepared = prepared
				if scenario == "artifact-tampered" || scenario == "dependency-tampered" || scenario == "extra-dependency" || scenario == "checkout-edited" {
					path := filepath.Join(prepared.stage, "static", "yomux.js")
					want := "artifacts changed"
					if scenario == "dependency-tampered" {
						path = filepath.Join(prepared.stage, "static", "vendor", "fixture.js")
					}
					if scenario == "extra-dependency" {
						path = filepath.Join(prepared.stage, "static", "vendor", "unexpected.js")
					}
					if scenario == "checkout-edited" {
						path, want = filepath.Join(root, "local.txt"), "checkout changed"
					}
					if err := os.WriteFile(path, []byte("changed"), 0o600); err != nil {
						t.Fatal(err)
					}
					s.runSelfUpdate(job)
					if failed := s.loadUpdateJob(); failed.State != "failed" || !strings.Contains(failed.Error, want) {
						t.Fatalf("stale candidate activated: %+v", failed)
					}
					if got := strings.TrimSpace(gitRun(t, root, "rev-parse", "HEAD")); got != baseSHA {
						t.Fatal("stale candidate moved HEAD")
					}
					if _, err := os.Stat(argsPath); !os.IsNotExist(err) {
						t.Fatal("stale candidate launched server")
					}
					return
				}

				// A second build would now fail; activation must reuse the verified output.
				if err := testsupport.WriteExecutable(filepath.Join(prepared.stage, "tools", "build_go.sh"), []byte("#!/bin/sh\nexit 81\n"), 0o700); err != nil {
					t.Fatal(err)
				}
			}
			if scenario == "build-failure" {
				retryConfig := t.TempDir()
				if err := os.Chmod(retryConfig, 0o700); err != nil {
					t.Fatal(err)
				}
				retryServer := &server{root: runtimeRoot, config: retryConfig, updateJobPath: filepath.Join(retryConfig, "job.json")}
				for attempt := range 3 {
					_, err := retryServer.prepareSelfUpdate(context.Background(), selfUpdateJob{ID: fmt.Sprintf("preflight-%d", attempt), Target: job.Target, TargetSHA: targetSHA})
					if err == nil || !strings.Contains(err.Error(), "fixture build failure") {
						t.Fatalf("retry build error: %v", err)
					}
				}
				entries, err := os.ReadDir(filepath.Join(retryConfig, "self-update"))
				if err != nil || len(entries) != 2 || entries[0].Name() != "preflight" || entries[1].Name() != "preflight.owner" {
					t.Fatalf("unbounded retry stages: %v %v", entries, err)
				}
			}
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
			for path, want := range map[string]string{"static/yomux.js": "new-js\n", "static/yomux.css": "new-css\n", "static/vendor/fixture.js": "dependency\n", "static/fonts/fixture.woff2": "font\n", "static/locales/en.json": "{}\n"} {
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

func TestSelfUpdateRecoveryRetiresInterruptedPreparation(t *testing.T) {
	for _, state := range []string{"preparing", "activating"} {
		t.Run(state, func(t *testing.T) {
			s := &server{updateJobPath: filepath.Join(t.TempDir(), "job.json")}
			if err := s.saveUpdateJob(selfUpdateJob{ID: "interrupted", State: state}); err != nil {
				t.Fatal(err)
			}
			s.recoverSelfUpdateJob()
			if job := s.loadUpdateJob(); job.State != "failed" || !strings.Contains(job.Error, "interrupted") {
				t.Fatalf("stale job still blocks updates: %+v", job)
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
	var states []string
	checker.publish = func(result updateSnapshot) {
		states = append(states, result.BuildState)
		if checker.Snapshot().BuildState != result.BuildState {
			t.Error("publication did not expose the current readiness snapshot")
		}
	}
	checker.prepare = func(context.Context, selfUpdateJob) (*preparedSelfUpdate, error) {
		return nil, errors.New("fixture compiler unavailable")
	}
	failed := checker.Check(context.Background())
	if !failed.Available || failed.BuildState != "failed" || failed.BuildError != "fixture compiler unavailable" || failed.prepared != nil {
		t.Fatalf("build failure hid availability: %+v", failed)
	}
	payload := updateStatusPayload(failed)
	if payload["available"] != true || payload["build_state"] != "failed" {
		t.Fatalf("readiness absent from status: %+v", payload)
	}
	if !reflect.DeepEqual(states, []string{"checking", "failed"}) {
		t.Fatalf("readiness events: %v", states)
	}
	builds := 0
	checker.prepare = func(ctx context.Context, job selfUpdateJob) (*preparedSelfUpdate, error) {
		builds++
		source, installation, err := nativeUpdateSource(installedRoot)
		if err != nil {
			return nil, err
		}
		stage := t.TempDir()
		binary := filepath.Join(stage, "yomux-go")
		if err := testsupport.WriteExecutable(binary, []byte("prepared native executable"), 0o700); err != nil {
			return nil, err
		}
		for name, body := range map[string]string{"yomux.js": "prepared script", "yomux.css": "prepared style", "vendor/dependency.js": "prepared dependency"} {
			path := filepath.Join(stage, "static", name)
			if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
				return nil, err
			}
			if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
				return nil, err
			}
		}
		hashes, err := preparedUpdateHashes(stage, binary)
		if err != nil {
			return nil, err
		}
		prepared := &preparedSelfUpdate{runtimeRoot: installedRoot, root: source, head: strings.TrimSpace(gitRun(t, repo, "rev-parse", "HEAD")), installation: installation, target: job.TargetSHA, stage: stage, binary: binary, hashes: hashes}
		return prepared, validatePreparedUpdate(ctx, prepared)
	}
	ready := checker.Check(context.Background())
	if ready.BuildState != "ready" || ready.BuildError != "" || ready.prepared == nil {
		t.Fatalf("retry did not recover: %+v", ready)
	}
	cached := checker.Check(context.Background())
	if cached.prepared != ready.prepared || builds != 1 {
		t.Fatalf("verified candidate was rebuilt: builds=%d", builds)
	}
	t.Run("status refresh build retires before shutdown returns", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		checker := newUpdateChecker(installedRoot, "0.10.6", func() string { return "minor" })
		started := make(chan *os.Process, 1)
		finished := make(chan error, 1)
		buildDone := make(chan struct{})
		checker.prepare = func(ctx context.Context, _ selfUpdateJob) (*preparedSelfUpdate, error) {
			cmd := exec.CommandContext(ctx, "sleep", "60")
			cmd.Env = testsupport.SyntheticEnvironment()
			cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM) }
			if err := cmd.Start(); err != nil {
				return nil, err
			}
			started <- cmd.Process
			err := cmd.Wait()
			finished <- err
			close(buildDone)
			return nil, ctx.Err()
		}
		s := &server{updates: checker, producerContext: ctx, serverCancel: cancel}
		s.updateStatus(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/api/update-status", nil))
		var child *os.Process
		select {
		case child = <-started:
		case <-time.After(5 * time.Second):
			t.Fatal("status refresh did not launch the fixture build")
		}
		t.Cleanup(func() {
			select {
			case <-buildDone:
				return
			default:
				_ = syscall.Kill(-child.Pid, syscall.SIGKILL)
			}
			select {
			case <-buildDone:
			case <-time.After(5 * time.Second):
				t.Error("fixture child did not retire")
			}
		})
		s.shutdown()
		select {
		case err := <-finished:
			if err == nil {
				t.Fatal("blocked build completed without cancellation")
			}
		default:
			t.Fatal("shutdown returned while its build child was still running")
		}
		if err := syscall.Kill(-child.Pid, 0); !errors.Is(err, syscall.ESRCH) {
			t.Fatalf("build process group survived shutdown: %v", err)
		}
	})
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
	if err := startDetachedCommand(os.Args[0], []string{"-test.run=^TestSelfUpdateSupervisorSubprocessHelper$"}, testsupport.GoHelperProcessEnvironment(environment), os.Getenv("YOMUX_SELF_UPDATE_TEST_LOG")); err != nil {
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
	if err := testsupport.WriteExecutable(bootPath, []byte(boot), 0o700); err != nil {
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
	parent.Env = testsupport.GoHelperProcessEnvironment(testsupport.SyntheticEnvironment(
		"YOMUX_SELF_UPDATE_TEST_PARENT=1",
		"YOMUX_SELF_UPDATE_TEST_SPEC="+specPath,
		"YOMUX_SELF_UPDATE_TEST_LOG="+logPath,
	))
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
	if err := testsupport.WriteExecutable(bootPath, []byte(boot), 0o700); err != nil {
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
	parent.Env = testsupport.GoHelperProcessEnvironment(testsupport.SyntheticEnvironment(
		"YOMUX_SELF_UPDATE_TEST_PARENT=1",
		"YOMUX_SELF_UPDATE_TEST_SPEC="+specPath,
		"YOMUX_SELF_UPDATE_TEST_LOG="+logPath,
	))
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
	want := []string{"--force", "--backend", "go", "--port", "48123", "--host", "127.0.0.1", "--config", "/private/config", "--stats", "/private/stats.sqlite", "--cert", "/private/cert.pem", "--key", "/private/key.pem"}
	if !reflect.DeepEqual(args, want) {
		t.Fatalf("restart %v, want %v", args, want)
	}
	s.httpMode = true
	args = s.selfUpdateBootArgs()
	if slices.Contains(args, "--no-dang") || slices.Contains(args, "--cert") || !slices.Contains(args, "--http") {
		t.Fatalf("HTTP restart changed mode: %v", args)
	}
}

func TestUpdateStageWriterAdmission(t *testing.T) {
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	stage := filepath.Join(base, "self-update", "preflight")
	if err := os.MkdirAll(stage, 0700); err != nil {
		t.Fatal(err)
	}
	marker := stage + ".owner"
	if err := os.WriteFile(marker, []byte("yomux-update-preflight-v1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := validateUpdateStage(stage); err != nil {
		t.Fatal(err)
	}
	build := func() (map[string][]byte, error) { return map[string][]byte{"fixture.js": []byte("ready")}, nil }
	if err := nativeWriteAssets(stage, filepath.Join(stage, "static"), build); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(filepath.Join(stage, ".git")); !os.IsNotExist(err) {
		t.Fatalf("stage acquired .git: %v", err)
	}
	if err := os.Mkdir(filepath.Join(stage, ".asset-writer"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := nativeWriteAssets(stage, filepath.Join(stage, "static"), build); err == nil {
		t.Fatal("competing writer admitted")
	}
	if err := os.Remove(filepath.Join(stage, ".asset-writer")); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{stage, marker} {
		if err := os.Chmod(path, 0755); err != nil {
			t.Fatal(err)
		}
		if err := validateUpdateStage(stage); err == nil {
			t.Fatalf("public path admitted: %s", path)
		}
		mode := os.FileMode(0700)
		if path == marker {
			mode = 0600
		}
		if err := os.Chmod(path, mode); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Rename(marker, marker+".saved"); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(marker+".saved", marker); err != nil {
		t.Fatal(err)
	}
	if err := validateUpdateStage(stage); err == nil {
		t.Fatal("symlink marker admitted")
	}
	if err := os.Remove(marker); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(marker+".saved", marker); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stage, ".git"), []byte("gitdir: missing\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := nativeAssetWriterSlot(stage); err == nil {
		t.Fatal("broken Git checkout fell back to archive")
	}
	if _, err := nativeAssetWriterSlot(base); err == nil {
		t.Fatal("unmarked archive admitted")
	}
}

func TestManualUpdateRetryCoalescesAndThrottles(t *testing.T) {
	checker := newUpdateChecker(t.TempDir(), "0.11.0", func() string { return "none" })
	var queued func()
	count := 0
	start := func(fn func()) bool { count++; queued = fn; return true }
	now := time.Now()
	if accepted, limited := checker.QueueManual(context.Background(), start, now); !accepted || limited {
		t.Fatal("first retry rejected")
	}
	for i := 0; i < 10; i++ {
		if accepted, limited := checker.QueueManual(context.Background(), start, now); accepted || limited {
			t.Fatal("burst did not coalesce")
		}
	}
	if count != 1 {
		t.Fatalf("scheduled %d checks", count)
	}
	queued()
	if accepted, limited := checker.QueueManual(context.Background(), start, now.Add(29*time.Second)); accepted || !limited {
		t.Fatal("retry was not throttled")
	}
	if accepted, limited := checker.QueueManual(context.Background(), start, now.Add(30*time.Second)); !accepted || limited {
		t.Fatal("retry did not recover")
	}
	queued()
}

func TestUpdateWriterUsesLinkedCheckoutGitSlot(t *testing.T) {
	root := t.TempDir()
	gitRun(t, root, "init", "--initial-branch=main")
	gitRun(t, root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "fixture")
	linked := filepath.Join(t.TempDir(), "linked")
	gitRun(t, root, "worktree", "add", "--detach", linked)
	output, err := nativeRepositoryPath(linked, "--absolute-git-dir")
	if err != nil {
		t.Fatal(err)
	}
	slot, err := nativeAssetWriterSlot(linked)
	if err != nil {
		t.Fatal(err)
	}
	if slot != filepath.Join(strings.TrimSpace(string(output)), "yolomux", "worktree-writer") {
		t.Fatalf("wrong linked writer slot: %s", slot)
	}
}

func TestUpdateRetryRouteCoalescesDuringPreparation(t *testing.T) {
	checker := newUpdateChecker(t.TempDir(), "0.11.0", nil)
	checker.running = true
	s := &server{updates: checker, producerContext: context.Background()}
	// Preparation holds this mutex throughout the build. Retry must not acquire it.
	s.updateJobMu.Lock()
	defer s.updateJobMu.Unlock()
	for i := 0; i < 8; i++ {
		response := httptest.NewRecorder()
		s.updateStatus(response, httptest.NewRequest(http.MethodPost, "/api/update-status", nil))
		if response.Code != http.StatusAccepted {
			t.Fatalf("coalesced retry = %d %s", response.Code, response.Body.String())
		}
	}
}

func TestUpdateStageRetiresOnlyStaleAssetWriters(t *testing.T) {
	config, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(config, 0o700); err != nil {
		t.Fatal(err)
	}
	stage := filepath.Join(config, "self-update", "preflight")
	if err := prepareUpdateStage(config, stage); err != nil {
		t.Fatal(err)
	}
	hostID, err := productHostPartitionID()
	if err != nil {
		t.Fatal(err)
	}
	finished := exec.Command("true")
	if err := finished.Run(); err != nil {
		t.Fatal(err)
	}
	live := map[string]any{
		"stable_host_id": hostID, "boot_id": readIdentityValue("", "/proc/sys/kernel/random/boot_id", "unknown-boot"),
		"pid": os.Getpid(), "process_start_identity": processStartIdentity(os.Getpid()),
	}
	with := func(changes map[string]any) map[string]any {
		record := map[string]any{}
		for key, value := range live {
			record[key] = value
		}
		for key, value := range changes {
			record[key] = value
		}
		return record
	}
	slot := filepath.Join(stage, ".asset-writer")
	for _, test := range []struct {
		name    string
		record  map[string]any
		age     time.Duration
		retired bool
	}{
		{name: "live writer", record: live},
		{name: "writer on another host", record: with(map[string]any{"stable_host_id": "another-host", "pid": finished.Process.Pid})},
		{name: "writer before a reboot", record: with(map[string]any{"boot_id": "an-earlier-boot"}), retired: true},
		{name: "exited writer", record: with(map[string]any{"pid": finished.Process.Pid, "process_start_identity": "proc:0"}), retired: true},
		{name: "recycled writer pid", record: with(map[string]any{"process_start_identity": "proc:0"}), retired: true},
		{name: "unrecorded writer still starting"},
		{name: "unrecorded writer that died", age: 2 * assetWriterUnrecordedGrace, retired: true},
	} {
		if err := os.Mkdir(slot, 0o700); err != nil {
			t.Fatal(err)
		}
		if test.record != nil {
			data, err := json.Marshal(test.record)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(slot, "owner.json"), data, 0o600); err != nil {
				t.Fatal(err)
			}
		}
		declared := time.Now().Add(-test.age)
		if err := os.Chtimes(slot, declared, declared); err != nil {
			t.Fatal(err)
		}
		err := prepareUpdateStage(config, stage)
		_, slotErr := os.Lstat(slot)
		if test.retired {
			if err != nil || !os.IsNotExist(slotErr) {
				t.Fatalf("%s: prepare=%v slot=%v; want the slot retired", test.name, err, slotErr)
			}
			if err := validateUpdateStage(stage); err != nil {
				t.Fatalf("%s: rebuilt stage invalid: %v", test.name, err)
			}
			continue
		}
		if err == nil || slotErr != nil {
			t.Fatalf("%s: prepare=%v slot=%v; want the writer kept and the stage refused", test.name, err, slotErr)
		}
		if err := os.RemoveAll(slot); err != nil {
			t.Fatal(err)
		}
	}
}

func TestUpdateStageRejectsRedirectBeforeMutation(t *testing.T) {
	config, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(config, 0o700); err != nil {
		t.Fatal(err)
	}
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(config, "self-update")); err != nil {
		t.Fatal(err)
	}
	if err := ensurePrivateUpdateParents(config, filepath.Join(config, "self-update", "sha")); err == nil {
		t.Fatal("redirect admitted")
	}
	entries, err := os.ReadDir(outside)
	if err != nil || len(entries) != 0 {
		t.Fatalf("redirect target changed: %v %v", entries, err)
	}
}

func TestSelfUpdateRecoveryPreservesBinaryAndArguments(t *testing.T) {
	args := []string{"--config", "/fixture/config with spaces", "--stats", "/fixture/quote'and$space"}
	command := selfUpdateRecoveryCommand("/fixture/root with spaces", "/fixture/prepared binary", args)
	// Substitute only the executable to inspect the shell's exact argument roundtrip.
	command = strings.Replace(command, "bash ", "printf '%s\\n' ", 1)
	output, err := exec.Command("sh", "-c", command).CombinedOutput()
	if err != nil {
		t.Fatalf("recovery command: %v %s", err, output)
	}
	want := append([]string{"/fixture/root with spaces/boot.sh"}, args...)
	if !reflect.DeepEqual(strings.Split(strings.TrimSuffix(string(output), "\n"), "\n"), want) {
		t.Fatalf("recovery split arguments: %s", output)
	}
	if !strings.Contains(command, shellQuote("YOMUX_GO_BINARY=/fixture/prepared binary")) {
		t.Fatal("recovery lost candidate binary")
	}
}

func TestInstalledRecoveryRepairsMixedPrefixBeforeRestart(t *testing.T) {
	root := t.TempDir()
	stage := filepath.Join(root, "stage with spaces")
	prefix := filepath.Join(root, "prefix with spaces")
	app := filepath.Join(prefix, "lib", "yomux")
	source := filepath.Join(root, "source with spaces")
	for _, path := range []string{filepath.Join(stage, "tools"), app, source} {
		if err := os.MkdirAll(path, 0700); err != nil {
			t.Fatal(err)
		}
	}
	binary := filepath.Join(stage, "yomux-go")
	if err := testsupport.WriteExecutable(binary, []byte("candidate executable"), 0700); err != nil {
		t.Fatal(err)
	}
	manifest := filepath.Join(app, "version.yaml")
	if err := os.WriteFile(manifest, []byte("old manifest"), 0600); err != nil {
		t.Fatal(err)
	}
	flag := filepath.Join(stage, "fail-once")
	if err := os.WriteFile(flag, nil, 0600); err != nil {
		t.Fatal(err)
	}
	installer := `#!/bin/sh
set -eu
[ "$YOMUX_INSTALL_PREPARED" = 1 ]
[ "$1" = --prefix ]
[ "$3" = --source-root ]
app="$2/lib/yomux"
cp "$YOMUX_GO_BINARY" "$app/yomux-go"
if [ -f fail-once ]; then rm fail-once; exit 83; fi
printf 'new manifest' > "$app/version.yaml"
printf '#!/bin/sh\n[ "$(cat "$(dirname "$0")/version.yaml")" = "new manifest" ] || exit 84\nprintf restarted > "$(dirname "$0")/restarted"\n' > "$app/boot.sh"
`
	path := filepath.Join(stage, "tools", "install.sh")
	if err := testsupport.WriteExecutable(path, []byte(installer), 0700); err != nil {
		t.Fatal(err)
	}
	prepared := &preparedSelfUpdate{root: source, stage: stage, binary: binary, runtimeRoot: app, installation: &nativeInstallSource{Prefix: prefix}}
	command := prepared.recoveryCommand([]string{"--config", filepath.Join(root, "config with spaces")})
	first := exec.Command("sh", "-c", command)
	if output, err := first.CombinedOutput(); err == nil {
		t.Fatalf("partial installer did not fail: %s", output)
	}
	if data, err := os.ReadFile(filepath.Join(app, "yomux-go")); err != nil || string(data) != "candidate executable" {
		t.Fatalf("partial binary = %q %v", data, err)
	}
	if data, err := os.ReadFile(manifest); err != nil || string(data) != "old manifest" {
		t.Fatalf("manifest changed before failure: %q %v", data, err)
	}
	if output, err := exec.Command("sh", "-c", command).CombinedOutput(); err != nil {
		t.Fatalf("printed recovery did not repair prefix: %v %s", err, output)
	}
	if data, err := os.ReadFile(filepath.Join(app, "restarted")); err != nil || string(data) != "restarted" {
		t.Fatalf("managed restart did not follow repair: %q %v", data, err)
	}
}

func TestUpdatePermissionDiagnosticPreservesExistingMode(t *testing.T) {
	config := t.TempDir()
	if err := os.Chmod(config, 0755); err != nil {
		t.Fatal(err)
	}
	err := ensurePrivateUpdateParents(config, filepath.Join(config, "self-update"))
	if err == nil || !strings.Contains(err.Error(), "0755") || !strings.Contains(err.Error(), "0700") {
		t.Fatalf("permission diagnostic: %v", err)
	}
	info, statErr := os.Stat(config)
	if statErr != nil || info.Mode().Perm() != 0755 {
		t.Fatalf("existing mode changed: %v %v", info, statErr)
	}
}

func TestUpdatePreparationFailureKeepsConcreteReason(t *testing.T) {
	stage := t.TempDir()
	if err := os.WriteFile(filepath.Join(stage, "build.log"), []byte("fatal: executable cannot load library libfixture\nVerification: native launch support\n"), 0600); err != nil {
		t.Fatal(err)
	}
	cause := errors.New("exit status 1")
	err := updatePreparationFailure(stage, "verification", cause)
	if !errors.Is(err, cause) || !strings.Contains(err.Error(), "cannot load library") || strings.Contains(err.Error(), ": Verification:") {
		t.Fatalf("lost concrete update failure: %v", err)
	}
}
