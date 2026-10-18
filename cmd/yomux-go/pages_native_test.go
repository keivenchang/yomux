// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/capabilities"
	"github.com/keivenchang/yomux/internal/testsupport"
)

func nativePageFixtureServer(t *testing.T) *server {
	t.Helper()
	s := nativeSettingsFixtureServer(t)
	t.Setenv("PATH", t.TempDir())
	t.Setenv("HOME", t.TempDir())
	t.Setenv("YOMUX_EXTRA_PATH", "")
	t.Setenv("YOMUX_ROOT", "")
	t.Setenv("YOMUX_CODEX_HOME", "")
	t.Setenv("CODEX_HOME", "")
	s.capabilities = capabilities.New("native-page-test")
	s.startedAt = time.Now()
	t.Cleanup(s.closeNativePages)
	return s
}

func nativePageBootstrap(t *testing.T, body []byte) map[string]any {
	t.Helper()
	const prefix = `<script id="yomux-bootstrap" type="application/json">`
	_, rest, found := strings.Cut(string(body), prefix)
	if !found {
		t.Fatal("missing bootstrap")
	}
	encoded, _, found := strings.Cut(rest, "</script>")
	if !found {
		t.Fatal("unterminated bootstrap")
	}
	var value map[string]any
	if err := json.Unmarshal([]byte(encoded), &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func TestNativePagesRenderWithoutPython(t *testing.T) {
	s := nativePageFixtureServer(t)
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	topology := goTmuxTopology{sessions: []string{`work</script><script>alert(1)</script>&x`}, generation: 17}
	body, err := s.renderNativeHome(request, authUser{username: `admin<&"`, role: "readonly"}, topology)
	if err != nil {
		t.Fatal(err)
	}
	payload := nativePageBootstrap(t, body)
	if payload["authUsername"] != `admin<&"` || payload["sessionMutationsAllowed"] != false {
		t.Fatal("identity contract changed")
	}
	if payload["sessions"].([]any)[0] != topology.sessions[0] {
		t.Fatal("session did not round trip")
	}
	if bytes.Contains(body, []byte(`</script><script>alert(1)</script>`)) {
		t.Fatal("session escaped bootstrap script")
	}
	if strings.Contains(string(body), "ZgotmplZ") {
		t.Fatal("template rejected a native URL or fragment")
	}
	if payload["backend"] != goRuntimeLabel() {
		t.Fatal("home advertises a different backend")
	}
	settings := payload["settingsPayload"].(map[string]any)
	if settings["deferred_metadata"] != true || settings["catalog"] != nil || settings["settings"] == nil {
		t.Fatal("first-paint settings contract changed")
	}
	fence := payload["statsWriterFence"].(map[string]any)
	if fence["protocol_version"] != float64(statsWriterProtocol) || fence["schema_generation"] != float64(statsSchemaGeneration) {
		t.Fatal("browser fence differs from request admission")
	}
	limits := payload["filesystemBatchLimits"].(map[string]any)
	if limits["maxRequests"] != limits["triggerCountLimit"] {
		t.Fatal("batch bounds diverged")
	}
	for _, setup := range []bool{false, true} {
		body, err := s.renderPreauthPage(request, setup, `//foreign.example`, `<script>alert(2)</script>`)
		if err != nil {
			t.Fatal(err)
		}
		text := string(body)
		if strings.Contains(text, "python3") || strings.Contains(text, "yomux.py") || strings.Contains(text, "ZgotmplZ") {
			t.Fatal("preauth page contains a retired launch command or unsafe fragment")
		}
		if strings.Contains(text, `<script>alert(2)</script>`) {
			t.Fatal("login message is unescaped")
		}
		if !setup && !strings.Contains(text, `name="next" value="/"`) {
			t.Fatal("unsafe redirect was retained")
		}
	}
}

func TestNativePreauthLocaleAndAppearance(t *testing.T) {
	s := nativePageFixtureServer(t)
	if _, err := s.nativeSettingsPayload(context.Background(), map[string]any{"appearance": map[string]any{"theme": "light", "active_color": "blue"}}, true); err != nil {
		t.Fatal(err)
	}
	locales, err := s.pageLocales("system", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(locales.specs) != 19 {
		t.Fatalf("shipped locale count = %d", len(locales.specs))
	}
	for _, spec := range locales.specs {
		t.Run(spec.Code, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, "/login", nil)
			request.AddCookie(&http.Cookie{Name: "yomux_locale", Value: spec.Code})
			for _, setup := range []bool{false, true} {
				body, err := s.renderPreauthPage(request, setup, "/finder", "")
				if err != nil {
					t.Fatal(err)
				}
				if !strings.Contains(string(body), `lang="`+spec.Code+`" dir="`+localeDirection(spec.Code)+`"`) {
					t.Fatal("wrong first-paint language or direction")
				}
				if !strings.Contains(string(body), `data-locale-value="`+spec.Code+`" aria-selected="true"`) {
					t.Fatal("picker lost current locale")
				}
				if !setup && !strings.Contains(string(body), `class="login-theme-light login-accent-blue"`) {
					t.Fatal("saved appearance lost")
				}
			}
		})
	}
	for _, sample := range []struct{ preference, header, want string }{
		{"system", "ja;q=0.2, zh-TW;q=0.9", "zh-Hant"},
		{"", "xx,fr-CA;q=0.4,de;q=0.4", "fr"},
		{"he", "en-US", "he"},
		{"../../auth", "ar-SA;q=1", "ar"},
		{"system", "de;q=0,en;q=bad,ko;q=0.1", "ko"},
		{"system", "zh-CN", "zh-Hans"},
		{"en-XA", "ar", "en-XA"},
	} {
		if got := locales.resolve(sample.preference, sample.header); got != sample.want {
			t.Errorf("resolve(%q,%q)=%q, want %q", sample.preference, sample.header, got, sample.want)
		}
	}
}

func TestNativePageAdmissionCancellationAndShutdown(t *testing.T) {
	s := nativePageFixtureServer(t)
	ctx, release, err := s.beginNativePage(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.beginNativePage(context.Background()); err == nil {
		t.Fatal("parallel page admitted")
	}
	done := make(chan struct{})
	go func() { s.closeNativePages(); close(done) }()
	select {
	case <-ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("shutdown did not cancel page")
	}
	select {
	case <-done:
		t.Fatal("shutdown did not join page")
	default:
	}
	release()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("shutdown did not complete")
	}
	if _, _, err := s.beginNativePage(context.Background()); err == nil {
		t.Fatal("page admitted after shutdown")
	}
}

func TestNativeRuntimePathFindsLocalAndConfiguredAgents(t *testing.T) {
	s := nativePageFixtureServer(t)
	home := os.Getenv("HOME")
	local := filepath.Join(home, ".local", "bin")
	extra := filepath.Join(home, "extra")
	for _, directory := range []string{local, extra} {
		if err := os.MkdirAll(directory, 0700); err != nil {
			t.Fatal(err)
		}
	}
	for _, sample := range []struct{ directory, name, source string }{
		{local, "claude", "#!/bin/sh\nprintf '%s' '{\"loggedIn\":true}'\n"},
		{extra, "codex", "#!/bin/sh\nprintf '%s' 'Not logged in' >&2\nexit 1\n"},
	} {
		if err := os.WriteFile(filepath.Join(sample.directory, sample.name), []byte(sample.source), 0700); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("YOMUX_EXTRA_PATH", "~/extra")
	rows, err := s.pageAgentStatus(context.Background(), true)
	if err != nil {
		t.Fatal(err)
	}
	if !rows["claude"].Installed || rows["claude"].LoggedIn == nil || !rows["claude"].Available {
		t.Fatalf("local Claude not probed: %+v", rows["claude"])
	}
	if !rows["codex"].Installed || rows["codex"].LoggedIn == nil || rows["codex"].Available {
		t.Fatalf("configured Codex not probed: %+v", rows["codex"])
	}
	path, err := nativeRuntimePath()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(path, extra+string(os.PathListSeparator)+local+string(os.PathListSeparator)) {
		t.Fatalf("PATH precedence changed: %s", path)
	}
}

func TestNativeCodexHomeRejectsSymlinkEscape(t *testing.T) {
	nativePageFixtureServer(t)
	root := t.TempDir()
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOMUX_ROOT", root)
	t.Setenv("YOMUX_CODEX_HOME", filepath.Join(root, "escape", "missing"))
	if _, err := nativeCodexEnvironment(); err == nil {
		t.Fatal("symlink escape admitted")
	}
}

func TestNativeAgentProbePreservesUnknownAndCancellation(t *testing.T) {
	s := nativePageFixtureServer(t)
	directory := os.Getenv("PATH")
	source := "#!/bin/sh\nexec /bin/sleep 60\n"
	if err := os.WriteFile(filepath.Join(directory, "claude"), []byte(source), 0700); err != nil {
		t.Fatal(err)
	}
	parent, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := s.pageAgentStatus(parent, true); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled probe returned %v", err)
	}
	s.closeNativePages()
}

func TestNativeSessionLaunchCarriesDiscoveredAgentPathIntoTmux(t *testing.T) {
	for _, sample := range []struct {
		agent, terminal string
		extra, full     bool
	}{
		{"claude", "", false, false}, {"claude", "", true, true},
		{"codex", "", true, false}, {"codex", "", false, true},
		{"opencode", "", true, false}, {"term", "fixture-shell", true, false},
	} {
		name := fmt.Sprintf("%s_extra=%t_full=%t", sample.agent, sample.extra, sample.full)
		t.Run(name, func(t *testing.T) {
			checkNativeSessionLaunchPath(t, sample.agent, sample.terminal, sample.extra, sample.full)
		})
	}
}

func checkNativeSessionLaunchPath(t *testing.T, agent, terminal string, extra, full bool) {
	tmuxPath, err := exec.LookPath("tmux")
	if err != nil {
		t.Fatal(err)
	}
	s := nativePageFixtureServer(t)
	t.Setenv("SHELL", "/bin/sh")
	socket := filepath.Join(testsupport.ShortTempDir(t), "t.sock")
	marker := filepath.Join(t.TempDir(), "entered")
	local := filepath.Join(os.Getenv("HOME"), ".local", "bin")
	if extra {
		local = filepath.Join(os.Getenv("HOME"), "extra tools")
		t.Setenv("YOMUX_EXTRA_PATH", local)
	}
	s.dangerouslyYolo = full
	executable := agent
	if agent == "term" {
		executable = terminal
	}
	if err := os.MkdirAll(local, 0700); err != nil {
		t.Fatal(err)
	}
	source := "#!/bin/sh\nexec native-session-child \"$@\"\n"
	helper := "#!/bin/sh\n"
	if extra {
		source = "#!/usr/bin/env native-session-child\n"
		helper += "shift\n"
	}
	helper += "printf '%s|%s\\n' ready \"$*\" > " + shellQuote(marker) + "\nexec /bin/sleep 60\n"
	if err := os.WriteFile(filepath.Join(local, "native-session-child"), []byte(helper), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(local, executable), []byte(source), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		return exec.CommandContext(ctx, tmuxPath, append([]string{"-S", socket, "-f", "/dev/null"}, args...)...)
	}
	if output, err := s.tmuxCommand("new-session", "-d", "-s", "hold", "/bin/sleep 60").CombinedOutput(); err != nil {
		t.Fatalf("fixture tmux: %v %s", err, output)
	}
	if output, err := s.tmuxCommand("set-option", "-g", "remain-on-exit", "on").CombinedOutput(); err != nil {
		t.Fatalf("fixture pane retention: %v %s", err, output)
	}
	t.Cleanup(func() {
		if err := exec.Command(tmuxPath, "-S", socket, "kill-server").Run(); err != nil {
			t.Errorf("fixture tmux cleanup: %v", err)
		}
	})
	t.Cleanup(s.shutdown)
	response := httptest.NewRecorder()
	s.createSession(response, httptest.NewRequest(http.MethodPost, "/api/create-session?agent="+agent+"&terminal="+terminal, nil))
	if response.Code != http.StatusOK {
		t.Fatalf("create: %d %s", response.Code, response.Body.String())
	}
	timer := time.NewTimer(2 * time.Second)
	defer timer.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		if data, err := os.ReadFile(marker); err == nil && strings.HasPrefix(string(data), "ready|") && strings.HasSuffix(string(data), "\n") {
			expected := "ready|"
			if full {
				switch agent {
				case "claude":
					expected += "--dangerously-skip-permissions"
				case "codex":
					expected += "--dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"
				}
			}
			expected += "\n"
			if string(data) != expected {
				t.Fatalf("launch arguments = %q, want %q", data, expected)
			}
			return
		}
		select {
		case <-ticker.C:
		case <-timer.C:
			pane, paneErr := s.tmuxCommand("capture-pane", "-p", "-t", "=1:").CombinedOutput()
			shell, shellErr := s.tmuxCommand("show-options", "-g", "default-shell").CombinedOutput()
			environment, environmentErr := s.tmuxCommand("show-environment", "-t", "=1:", "PATH").CombinedOutput()
			t.Fatalf("discovered command never executed inside tmux: %s; pane=%s (%v), shell=%s (%v), environment=%s (%v)", response.Body.String(), pane, paneErr, shell, shellErr, environment, environmentErr)
		}
	}
}

func TestNativeAuthProbeRejectsOversizedDiagnosticOutput(t *testing.T) {
	s := nativePageFixtureServer(t)
	source := "#!/bin/sh\nprintf '%s' '{\"loggedIn\":true}'\ni=0\nwhile [ \"$i\" -lt 10000 ]; do printf 12345678 >&2; i=$((i+1)); done\n"
	if err := os.WriteFile(filepath.Join(os.Getenv("PATH"), "claude"), []byte(source), 0700); err != nil {
		t.Fatal(err)
	}
	rows, err := s.pageAgentStatus(context.Background(), true)
	if err != nil {
		t.Fatal(err)
	}
	if rows["claude"].LoggedIn != nil || rows["claude"].Reason != "auth-unknown" || !strings.Contains(rows["claude"].ProbeError, errNativeCommandOutputLimit.Error()) {
		t.Fatalf("oversized probe accepted: %+v", rows["claude"])
	}
}

func TestNativeAuthJSONAuthorityMatchesBooleanIdentity(t *testing.T) {
	nativePageFixtureServer(t)
	directory := os.Getenv("PATH")
	for _, sample := range []struct {
		key, value string
		want       bool
	}{
		{"loggedIn", `true`, true}, {"loggedIn", `false`, false}, {"loggedIn", `null`, false}, {"loggedIn", `0`, false}, {"loggedIn", `1`, false},
		{"loggedIn", `"true"`, false}, {"loggedIn", `"false"`, false}, {"loggedIn", `{}`, false}, {"loggedIn", `[]`, false},
		{"LoggedIn", `false`, true}, {"loggedin", `false`, true},
	} {
		t.Run(sample.key+"_"+sample.value, func(t *testing.T) {
			source := "#!/bin/sh\nprintf '%s' " + shellQuote(`{"`+sample.key+`":`+sample.value+`}`) + "\n"
			if err := os.WriteFile(filepath.Join(directory, "claude"), []byte(source), 0700); err != nil {
				t.Fatal(err)
			}
			row := probePageAgentAuth(context.Background(), "claude", pageAgentAuth{Installed: true, Available: true, Reason: "auth-unknown"})
			if row.LoggedIn == nil || *row.LoggedIn != sample.want || row.Available != sample.want {
				t.Fatalf("loggedIn=%s: %+v, want %t", sample.value, row, sample.want)
			}
		})
	}
}

func TestNativeRequestLocalePreferenceUsesSavedLanguageAndValidatesCookies(t *testing.T) {
	s := nativePageFixtureServer(t)
	if _, err := s.nativeSettingsPayload(context.Background(), map[string]any{"general": map[string]any{"language": "fr"}}, true); err != nil {
		t.Fatal(err)
	}
	for _, example := range []struct{ cookies, want string }{
		{"", "fr"},
		{"yomux_locale=bad", "fr"},
		{"yomux_locale=en-XA", "fr"},
		{"yomux_locale=%ZZ; yolomux_locale=de", "de"},
		{"yomux_locale=bad; yolomux_locale=%65n", "en"},
		{"yomux_locale=de; yolomux_locale=en", "de"},
		{"yomux_locale=system", "en"},
	} {
		request := httptest.NewRequest(http.MethodGet, "/login", nil)
		request.Header.Set("Cookie", example.cookies)
		body, err := s.renderPreauthPage(request, false, "/", "")
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(body), `lang="`+example.want+`"`) {
			t.Fatalf("cookie %q lost first-paint language %s", example.cookies, example.want)
		}
	}
	if err := os.WriteFile(filepath.Join(s.config, "settings.yaml"), []byte("general: ["), 0600); err != nil {
		t.Fatal(err)
	}
	body, err := s.renderPreauthPage(httptest.NewRequest(http.MethodGet, "/login", nil), false, "/", "")
	if err != nil || !strings.Contains(string(body), `lang="en"`) {
		t.Fatalf("malformed settings prevented default login shell: %v", err)
	}
}
