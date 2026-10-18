// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/testsupport"
)

func cleanNativeLaunchEnvironment(t *testing.T) {
	t.Helper()
	testsupport.PreserveProductEnvironment(t)
	for _, key := range launchInstanceKeys() {
		t.Setenv(key, "")
	}
	for _, key := range productenv.Names("BACKEND") {
		t.Setenv(key, "")
	}
	t.Setenv("HOME", t.TempDir())
}

func TestNativeBackendRejectionPrecedesAnyLaunch(t *testing.T) {
	cleanNativeLaunchEnvironment(t)
	for _, args := range [][]string{{"--deprecated-python"}, {"--backend", "python"}, {"--backend=python"}, {"--backend", "invalid"}, {"--backend"}, {"-backend=python"}, {"-backend", "python"}, {"-backend=unknown"}, {"-backend", "unknown"}, {"-backend"}} {
		if err := validateNativeBackend(args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
	for _, name := range productenv.Names("BACKEND") {
		t.Setenv(name, "python")
		if err := validateNativeBackend([]string{"--backend", "go"}); err == nil {
			t.Fatalf("accepted Python through %s", name)
		}
		t.Setenv(name, "")
	}
	for _, args := range [][]string{nil, {"--backend", "auto"}, {"--backend=go"}, {"--golang"}, {"-backend=go"}, {"-backend", "auto"}, {"-golang"}} {
		if err := validateNativeBackend(args); err != nil {
			t.Fatal(err)
		}
	}
}

func TestNativeRowPlansStripAmbientRootsWithoutCopyingCredentials(t *testing.T) {
	cleanNativeLaunchEnvironment(t)
	checkout := t.TempDir()
	t.Setenv("YOMUX_ROOT", filepath.Join(t.TempDir(), "ambient"))
	t.Setenv("OPENAI_API_KEY", "fixture-secret-must-not-be-in-plan")
	plan, err := nativeLaunchPlan(checkout, 7112, true)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Assign["YOMUX_ROOT"] != filepath.Join("/tmp", "y"+strconv.Itoa(os.Getuid()), "p7112") || plan.Assign["YOMUX_INSTANCE"] != "7112:managed" {
		t.Fatalf("unexpected row plan: %+v", plan)
	}
	data, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "fixture-secret") || strings.Contains(string(data), "OPENAI_API_KEY") {
		t.Fatal("plan copied credentials")
	}
	decoded, err := decodeNativePlan(data)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(decoded.Unset, plan.Unset) {
		t.Fatal("unset contract did not round trip")
	}
	if err := applyNativePlan(decoded); err != nil {
		t.Fatal(err)
	}
	if productenv.Value("ROOT") != plan.Assign["YOMUX_ROOT"] || os.Getenv("OPENAI_API_KEY") != "fixture-secret-must-not-be-in-plan" {
		t.Fatal("row application changed unrelated environment")
	}
}

func TestNativePlanRejectsArbitraryAssignmentsAndTrailingData(t *testing.T) {
	for _, data := range []string{`{"unset":[],"assign":{"OPENAI_API_KEY":"secret"}}`, `{"unset":["HOME"],"assign":{}}`, `{"unset":[],"assign":{}} {}`, `{"assign":{}}`, `{"unset":[],"assign":{},"extra":true}`} {
		if _, err := decodeNativePlan([]byte(data)); err == nil {
			t.Fatalf("accepted %s", data)
		}
	}
}

func TestNativeDirectPlanPreservesExplicitRootsAndRejectsEscape(t *testing.T) {
	cleanNativeLaunchEnvironment(t)
	checkout := t.TempDir()
	base, err := os.MkdirTemp("/tmp", "yn-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(base) })
	root := filepath.Join(base, "product")
	t.Setenv("YOMUX_ROOT", root)
	plan, err := nativeLaunchPlan(checkout, 7111, false)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Assign["YOMUX_ROOT"] != root || plan.Assign["YOMUX_INSTANCE"] != "" {
		t.Fatalf("explicit root acquired managed authority: %+v", plan)
	}
	t.Setenv("YOMUX_STATE_DIR", filepath.Join(t.TempDir(), "outside"))
	if _, err := nativeLaunchPlan(checkout, 7111, false); err == nil {
		t.Fatal("accepted escaped state directory")
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatalf("rejected planning created root: %v", err)
	}
	t.Setenv("YOMUX_STATE_DIR", "")
	t.Setenv("YOMUX_ROOT", filepath.Join(checkout, "state"))
	if _, err := nativeLaunchPlan(checkout, 7111, false); err == nil {
		t.Fatal("accepted worktree state root")
	}
}

func TestNativeCensusFindsForeignListenerWithoutMutatingIt(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	port := listener.Addr().(*net.TCPAddr).Port
	pids, err := nativeListenerPIDs(port)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(pids, []int{os.Getpid()}) {
		t.Fatalf("listener owners %v", pids)
	}
	connection, err := net.Dial("tcp", listener.Addr().String())
	if err != nil {
		t.Fatal("census disturbed listener", err)
	}
	connection.Close()
}

func TestNativeStorageAvailabilityDoesNotRewriteLease(t *testing.T) {
	cleanNativeLaunchEnvironment(t)
	checkout := t.TempDir()
	root := filepath.Join(t.TempDir(), "root")
	t.Setenv("YOMUX_ROOT", root)
	lease, err := acquireDirectoryLease(filepath.Join(root, "state"), "instance.lock")
	if err != nil {
		t.Fatal(err)
	}
	defer releaseInstanceRootLease(lease)
	path := filepath.Join(root, "state", "instance.lock")
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := nativeRootsAvailable(checkout, 0); err == nil {
		t.Fatal("accepted another storage owner")
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatal("preflight rewrote an active lease")
	}
	if err := nativeRootsAvailable(checkout, os.Getpid()); err != nil {
		t.Fatal("verified replacement owner refused", err)
	}
}

func TestNativeReplacementWaitsForDepartedProcessStorage(t *testing.T) {
	for _, relative := range []string{"instance.lock", "config/instance.lock", "config/go-storage.lock", "state/instance.lock", "cache/instance.lock", "runtime/instance.lock"} {
		t.Run(relative, func(t *testing.T) {
			cleanNativeLaunchEnvironment(t)
			checkout := t.TempDir()
			root := t.TempDir()
			t.Setenv("YOMUX_ROOT", root)
			path := filepath.Join(root, relative)
			lease, err := acquireDirectoryLease(filepath.Dir(path), filepath.Base(path))
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				if lease != nil {
					releaseInstanceRootLease(lease)
				}
			})
			gone := func(int) (string, string, error) { return "", "", os.ErrNotExist }
			if err := nativeWaitReplacementRelease(checkout, -1, "departed", 0, gone); err == nil || !strings.Contains(err.Error(), path) {
				t.Fatalf("departed process with retained lock did not name the unavailable resource: %v", err)
			}
			if contender, err := acquireDirectoryLease(filepath.Dir(path), filepath.Base(path)); err == nil {
				releaseInstanceRootLease(contender)
				t.Fatal("accepted a locked directory")
			} else if !errors.Is(err, syscall.EWOULDBLOCK) || !strings.Contains(err.Error(), path) {
				t.Fatalf("directory lease failure lost its path or cause: %v", err)
			}
			checkedAgain := make(chan struct{})
			done := make(chan error, 1)
			go func() {
				checks := 0
				proof := func(pid int) (string, string, error) {
					checks++
					if checks == 2 {
						close(checkedAgain)
					}
					return gone(pid)
				}
				done <- nativeWaitReplacementRelease(checkout, -1, "departed", 5*time.Second, proof)
			}()
			select {
			case <-checkedAgain:
			case err := <-done:
				t.Fatalf("replacement finished before storage release: %v", err)
			case <-time.After(5 * time.Second):
				t.Fatal("replacement did not recheck storage")
			}
			releaseInstanceRootLease(lease)
			lease = nil
			select {
			case err := <-done:
				if err != nil {
					t.Fatal("released replacement storage was refused", err)
				}
			case <-time.After(5 * time.Second):
				t.Fatal("replacement did not observe storage release")
			}
		})
	}
}

func TestNativeManagedDefaultsPreserveOperatorSelection(t *testing.T) {
	for _, prefix := range []string{"YOMUX_", "YOLOMUX_"} {
		t.Run(prefix, func(t *testing.T) {
			for _, key := range []string{"PORT", "DEFAULT_PORT", "HOST"} {
				for _, name := range productenv.Names(key) {
					t.Setenv(name, "")
					os.Unsetenv(name)
				}
			}
			t.Setenv(prefix+"DEFAULT_PORT", "7111")
			port, host, dangerous := 7221, "127.0.0.1", false
			if err := nativeManagedDefaults(map[string]bool{}, &port, &host, &dangerous); err != nil {
				t.Fatal(err)
			}
			if port != 7111 || host != "0.0.0.0" || !dangerous {
				t.Fatalf("defaults: %d %s %t", port, host, dangerous)
			}
			t.Setenv(prefix+"PORT", "7112")
			t.Setenv(prefix+"HOST", "127.0.0.2")
			if err := nativeManagedDefaults(map[string]bool{}, &port, &host, &dangerous); err != nil {
				t.Fatal(err)
			}
			if port != 7112 || host != "127.0.0.2" {
				t.Fatalf("environment: %d %s", port, host)
			}
			port, host, dangerous = 7113, "localhost", false
			if err := nativeManagedDefaults(map[string]bool{"port": true, "host": true, "dang": true}, &port, &host, &dangerous); err != nil {
				t.Fatal(err)
			}
			if port != 7113 || host != "localhost" || dangerous {
				t.Fatal("explicit flags overridden")
			}
			t.Setenv(prefix+"PORT", "")
			if err := nativeManagedDefaults(map[string]bool{}, &port, &host, &dangerous); err == nil {
				t.Fatal("empty port reactivated default")
			}
		})
	}
}

func TestNativeReplacementRejectsMalformedTLSBeforePlanning(t *testing.T) {
	cleanNativeLaunchEnvironment(t)
	root := filepath.Join(t.TempDir(), "uncreated")
	t.Setenv("YOMUX_ROOT", root)
	cert := filepath.Join(t.TempDir(), "certificate.pem")
	key := filepath.Join(t.TempDir(), "key.pem")
	if err := os.WriteFile(cert, []byte("malformed"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(key, []byte("malformed"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := runNativeOperator([]string{"verify-replacement", "--root", t.TempDir(), "--cert", cert, "--key", key}); err == nil || !strings.Contains(err.Error(), "TLS certificate") {
		t.Fatalf("unexpected result: %v", err)
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatalf("TLS rejection created roots: %v", err)
	}
}

func TestNativeExecutableIdentityReadsTheRunningImage(t *testing.T) {
	if err := verifyNativeExecutable(os.Getpid()); err != nil {
		t.Fatal(err)
	}
}

func TestNativeExecutableFileProofChecksDigestAndProcessStart(t *testing.T) {
	started := time.Unix(1700000000, 0)
	for _, scenario := range []struct {
		name, runningData string
		modified          time.Time
		wantError         string
	}{
		{"matching image", "candidate", started.Add(-time.Second), ""},
		{"different image", "previous", started.Add(-time.Second), "does not match"},
		{"replaced path", "candidate", started.Add(time.Second), "replaced or touched"},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			directory := t.TempDir()
			selected, running := filepath.Join(directory, "selected"), filepath.Join(directory, "running")
			if err := os.WriteFile(selected, []byte("candidate"), 0o700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(running, []byte(scenario.runningData), 0o700); err != nil {
				t.Fatal(err)
			}
			if err := os.Chtimes(running, scenario.modified, scenario.modified); err != nil {
				t.Fatal(err)
			}
			err := verifyNativeExecutableFiles(selected, running, started)
			if (scenario.wantError == "" && err != nil) || (scenario.wantError != "" && (err == nil || !strings.Contains(err.Error(), scenario.wantError))) {
				t.Fatalf("image proof = %v, want %q", err, scenario.wantError)
			}
			if err := os.Remove(running); err != nil {
				t.Fatal(err)
			}
			if err := verifyNativeExecutableFiles(selected, running, started); err == nil {
				t.Fatal("missing running image accepted")
			}
		})
	}
}

func TestNativeReexecDescriptorsRestoreOnFailure(t *testing.T) {
	original := acceptedLaunchLeaseFDs
	t.Cleanup(func() { acceptedLaunchLeaseFDs = original })
	lease, err := os.CreateTemp(t.TempDir(), "lease")
	if err != nil {
		t.Fatal(err)
	}
	defer lease.Close()
	fd := int(lease.Fd())
	acceptedLaunchLeaseFDs = []int{fd}
	expected := errors.New("exec refused")
	if err := withInheritedLaunchDescriptors(-1, func() error {
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0)
		if errno != 0 || flags&syscall.FD_CLOEXEC != 0 {
			t.Fatalf("lease not inherited across self exec: %d, %v", flags, errno)
		}
		return expected
	}); !errors.Is(err, expected) {
		t.Fatalf("exec cause lost: %v", err)
	}
	for _, listener := range []int{-1, 999999} {
		if listener >= 0 {
			if err := withInheritedLaunchDescriptors(listener, func() error {
				t.Fatal("invalid listener reached exec")
				return nil
			}); err == nil {
				t.Fatal("invalid listener accepted")
			}
		}
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_GETFD, 0)
		if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
			t.Fatalf("failed exec left lease inheritable: %d, %v", flags, errno)
		}
	}
}

func TestNativeInheritedLeaseIsAdoptedWithoutReleasingOwnership(t *testing.T) {
	t.Cleanup(func() { acceptedLaunchLeaseFDs = nil })
	root := t.TempDir()
	original, err := acquireDirectoryLease(root, "instance.lock")
	if err != nil {
		t.Fatal(err)
	}
	defer releaseInstanceRootLease(original)
	if err := acceptLaunchDescriptors(strconv.Itoa(int(original.Fd())), -1); err != nil {
		t.Fatal(err)
	}
	adopted, err := acquireDirectoryLease(root, "instance.lock")
	if err != nil {
		t.Fatal(err)
	}
	defer releaseInstanceRootLease(adopted)
	competitor, err := os.OpenFile(filepath.Join(root, "instance.lock"), os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer competitor.Close()
	if err := syscall.Flock(int(competitor.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err == nil {
		t.Fatal("adoption released the inherited lease")
	}
}

func TestNativeListenerProbeUsesAuthenticatedBaselinePing(t *testing.T) {
	for _, httpMode := range []bool{true, false} {
		t.Run(strconv.FormatBool(httpMode), func(t *testing.T) {
			cleanNativeLaunchEnvironment(t)
			config := t.TempDir()
			writeConfig := func(directory, secret string) {
				t.Helper()
				if err := os.WriteFile(filepath.Join(directory, "auth.yaml"), []byte("users:\n  - username: fixture\n    password: fixture-password\n    role: admin\n"), 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(directory, "auth-cookie-secret"), []byte(secret), 0600); err != nil {
					t.Fatal(err)
				}
			}
			writeConfig(config, strings.Repeat("ab", 32))
			t.Setenv("YOMUX_CONFIG_DIR", config)
			s := &server{config: config, httpMode: httpMode}
			mux := http.NewServeMux()
			requests := make(chan string, 8)
			mux.Handle("/api/ping", s.protected(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests <- r.URL.Path
				// Baseline servers have no root or pid in their cheap response.
				jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "backend": "go", "version": "older-fixture", "instance_id": "fixture"})
			})))
			mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
				requests <- r.URL.Path
				http.Error(w, "metrics census must not admit a listener", http.StatusServiceUnavailable)
			})
			listener := httptest.NewUnstartedServer(mux)
			defer listener.Close()
			s.port = listener.Listener.Addr().(*net.TCPAddr).Port
			if httpMode {
				listener.Start()
			} else {
				listener.StartTLS()
			}
			payload, err := nativeListenerProbe(t.TempDir(), "127.0.0.1", s.port, httpMode)
			if err != nil || payload["version"] != "older-fixture" {
				t.Fatalf("baseline authenticated ping refused: %v %v", payload, err)
			}
			if path := <-requests; path != "/api/ping" {
				t.Fatalf("expensive or unauthenticated proof endpoint: %s", path)
			}
			wrong := t.TempDir()
			writeConfig(wrong, strings.Repeat("cd", 32))
			t.Setenv("YOMUX_CONFIG_DIR", wrong)
			if _, err := nativeListenerProbe(t.TempDir(), "127.0.0.1", s.port, httpMode); err == nil {
				t.Fatal("listener accepted authentication from another product root")
			}
		})
	}
}

func TestNativeListenerProbeUsesSetupHealthAndRejectsForeignBackend(t *testing.T) {
	cleanNativeLaunchEnvironment(t)
	t.Setenv("YOMUX_CONFIG_DIR", t.TempDir())
	for _, backend := range []string{"go", "foreign"} {
		t.Run(backend, func(t *testing.T) {
			listener := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/healthz" {
					http.Error(w, "unexpected endpoint", http.StatusServiceUnavailable)
					return
				}
				jsonResponse(w, http.StatusOK, map[string]any{"backend": backend})
			}))
			defer listener.Close()
			port := listener.Listener.Addr().(*net.TCPAddr).Port
			_, err := nativeListenerProbe(t.TempDir(), "127.0.0.1", port, true)
			if backend == "go" && err != nil {
				t.Fatal(err)
			}
			if backend != "go" && err == nil {
				t.Fatal("accepted a foreign backend")
			}
		})
	}
}

func TestNativeListenerCommandRootProof(t *testing.T) {
	parent := t.TempDir()
	checkout := filepath.Join(parent, "checkout")
	if err := os.Mkdir(checkout, 0700); err != nil {
		t.Fatal(err)
	}
	for _, arguments := range [][]string{
		{"yomux-go", "--root", checkout},
		{"yomux-go", "-root", checkout},
		{"yomux-go", "--root=" + checkout},
		{"yomux-go", "-root=" + checkout},
		{"yomux-go", "--root", "checkout"},
		{"yomux-go", "-root=checkout"},
		{"yomux-go", "--root", parent, "-root=checkout"},
	} {
		if err := nativeVerifyCommandRoot(checkout, parent, arguments); err != nil {
			t.Fatalf("valid kernel root %v refused: %v", arguments, err)
		}
	}
	if err := nativeVerifyCommandRoot(checkout, checkout, []string{"yomux-go"}); err != nil {
		t.Fatal("default cwd root refused", err)
	}
	for _, arguments := range [][]string{
		{"yomux-go"},
		{"yomux-go", "--root", parent},
		{"yomux-go", "-root=" + parent},
		{"yomux-go", "--root"},
		{"yomux-go", "-root="},
		{"yomux-go", "--", "--root", checkout},
		{"yomux-go", "--root", parent, "ignored", "--root", checkout},
		{"yomux-go", "--http", "false", "--root", checkout},
		{"yomux-go", "--config", "--root", checkout},
	} {
		if err := nativeVerifyCommandRoot(checkout, parent, arguments); err == nil {
			t.Fatalf("accepted wrong or ambiguous root: %v", arguments)
		}
	}
}

func TestNativeSharedServerIdentityDoesNotReadMetricsOrExposeSecrets(t *testing.T) {
	cleanNativeLaunchEnvironment(t)
	root := t.TempDir()
	for _, name := range productenv.Names("ROOT") {
		t.Setenv(name, root)
	}
	for _, name := range productenv.Names("INSTANCE") {
		t.Setenv(name, "fixture")
	}
	t.Setenv("UNRELATED_PRIVATE_VALUE", "must-not-be-published")
	s := &server{root: root, config: t.TempDir(), launchIdentity: nativeLaunchIdentityEnvironment(), processMetricsFn: func(time.Time) goProcessMetrics { panic("identity invoked metrics census") }}
	for _, handler := range []func(http.ResponseWriter, *http.Request){s.ping, s.health} {
		recorder := httptest.NewRecorder()
		handler(recorder, httptest.NewRequest(http.MethodGet, "/", nil))
		var payload map[string]any
		if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		if err := nativeValidateAPIIdentity(payload, root, os.Getpid()); err != nil {
			t.Fatal(err)
		}
		values, err := nativeAPIEnvironment(payload)
		if err != nil || len(values) != 4 || values["YOMUX_ROOT"] != root || values["YOLOMUX_INSTANCE"] != "fixture" {
			t.Fatalf("identity whitelist changed: %v %v", values, err)
		}
		if strings.Contains(recorder.Body.String(), "must-not-be-published") || strings.Contains(recorder.Body.String(), "UNRELATED_PRIVATE_VALUE") {
			t.Fatal("identity leaked an unrelated environment value")
		}
	}
}

func TestNativeAPIIdentityRejectsWrongRootPIDAndEnvironment(t *testing.T) {
	root := t.TempDir()
	other := t.TempDir()
	for _, payload := range []map[string]any{
		{"backend": "go", "root": other, "pid": float64(os.Getpid())},
		{"backend": "go", "root": root, "pid": float64(os.Getpid() + 1)},
		{"backend": "go", "root": root},
		{"backend": "go", "pid": float64(os.Getpid())},
		{"backend": "foreign", "root": root, "pid": float64(os.Getpid())},
	} {
		if err := nativeValidateAPIIdentity(payload, root, os.Getpid()); err == nil {
			t.Fatalf("accepted wrong API identity: %v", payload)
		}
	}
	plan := nativeRowPlan{Assign: map[string]string{"YOMUX_ROOT": root, "YOMUX_INSTANCE": "expected"}}
	for _, mutation := range []string{"wrong-root", "wrong-instance", "missing", "extra", "non-string", "alias-conflict"} {
		values := map[string]any{"YOMUX_ROOT": root, "YOLOMUX_ROOT": root, "YOMUX_INSTANCE": "expected", "YOLOMUX_INSTANCE": "expected"}
		switch mutation {
		case "wrong-root":
			values["YOMUX_ROOT"], values["YOLOMUX_ROOT"] = other, other
		case "wrong-instance":
			values["YOMUX_INSTANCE"], values["YOLOMUX_INSTANCE"] = "wrong", "wrong"
		case "missing":
			delete(values, "YOMUX_ROOT")
		case "extra":
			values["UNVERIFIED"] = "value"
		case "non-string":
			values["YOMUX_ROOT"] = float64(1)
		case "alias-conflict":
			values["YOLOMUX_ROOT"] = other
		}
		actual, err := nativeAPIEnvironment(map[string]any{"launch_environment": values})
		if err == nil {
			err = nativeVerifyEnvironmentValues(actual, plan)
		}
		if err == nil {
			t.Fatalf("accepted %s launch identity", mutation)
		}
	}
}

func TestNativeAPIIdentityUsesCurrentPingAndLegacyAuthenticatedFallback(t *testing.T) {
	for _, mode := range []string{"current", "legacy", "wrong-root", "wrong-pid", "partial", "legacy-refused"} {
		t.Run(mode, func(t *testing.T) {
			cleanNativeLaunchEnvironment(t)
			root := t.TempDir()
			config := t.TempDir()
			if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: fixture\n    password: fixture-password\n    role: admin\n"), 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(config, "auth-cookie-secret"), []byte(strings.Repeat("ab", 32)), 0600); err != nil {
				t.Fatal(err)
			}
			t.Setenv("YOMUX_CONFIG_DIR", config)
			s := &server{root: root, config: config, httpMode: true}
			requests := make(chan string, 4)
			mux := http.NewServeMux()
			mux.Handle("/api/ping", s.protected(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests <- r.URL.Path
				payload := map[string]any{"backend": "go", "version": "older-fixture"}
				if mode != "legacy" && mode != "legacy-refused" {
					payload["root"], payload["pid"] = root, os.Getpid()
				}
				if mode == "wrong-root" {
					payload["root"] = root + "-wrong"
				}
				if mode == "wrong-pid" {
					payload["pid"] = os.Getpid() + 1
				}
				if mode == "partial" {
					delete(payload, "pid")
				}
				jsonResponse(w, http.StatusOK, payload)
			})))
			mux.Handle("/api/system-status", s.protected(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests <- r.URL.Path
				if mode != "legacy" {
					http.Error(w, "status must not authorize this response", http.StatusServiceUnavailable)
					return
				}
				jsonResponse(w, http.StatusOK, map[string]any{"server": map[string]any{"backend": "go", "root": root, "pid": os.Getpid()}})
			})))
			listener := httptest.NewUnstartedServer(mux)
			defer listener.Close()
			s.port = listener.Listener.Addr().(*net.TCPAddr).Port
			listener.Start()
			_, err := nativeListenerAPIIdentity(root, "127.0.0.1", s.port, true, os.Getpid(), true)
			if mode == "current" || mode == "legacy" {
				if err != nil {
					t.Fatal(err)
				}
			} else if err == nil {
				t.Fatal("accepted invalid identity or unavailable legacy proof", mode)
			}
			if path := <-requests; path != "/api/ping" {
				t.Fatal("identity did not start with cheap ping", path)
			}
			if mode == "legacy" || mode == "legacy-refused" {
				if path := <-requests; path != "/api/system-status" {
					t.Fatal("legacy identity did not use authenticated fallback", path)
				}
			} else {
				select {
				case path := <-requests:
					t.Fatal("current or malformed identity reached expensive status", path)
				default:
				}
			}
		})
	}
}
