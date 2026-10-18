// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/testsupport"
)

func nativeSettingsFixtureServer(t *testing.T) *server {
	t.Helper()
	testsupport.PreserveProductEnvironment(t)
	for _, prefix := range []string{"YOMUX_", "YOLOMUX_"} {
		for _, suffix := range []string{"SUMMARY_MODEL", "SUMMARY_EFFORT", "SUMMARY_SERVICE_TIER", "FILE_TRANSFER_MAX_BYTES", "UPLOAD_MAX_BYTES"} {
			if err := os.Unsetenv(prefix + suffix); err != nil {
				t.Fatal(err)
			}
		}
	}
	return &server{root: testsupport.RepoRoot(t), config: t.TempDir()}
}

func TestNativeSettingsMatchesRetainedContract(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	schema, err := s.nativeSettingsSchema()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(s.root, "tests", "fixtures", "settings_native_contract.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Defaults map[string]any `json:"defaults"`
		Catalog  map[string]any `json:"catalog"`
		Cases    []struct {
			Input     map[string]any `json:"input"`
			Sanitized map[string]any `json:"sanitized"`
			Coerced   []string       `json:"coerced"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(schema["DEFAULT_SETTINGS"], fixture.Defaults) {
		t.Fatal("defaults differ from retained contract")
	}
	gotCatalog := schema.catalog(fixture.Defaults)
	if !reflect.DeepEqual(gotCatalog, fixture.Catalog) {
		for path, expected := range fixture.Catalog {
			for key, value := range settingsObject(expected) {
				if got := settingsObject(gotCatalog[path])[key]; !reflect.DeepEqual(got, value) {
					t.Errorf("catalog %s.%s = %#v; want %#v", path, key, got, value)
				}
			}
		}
	}
	for index, example := range fixture.Cases {
		coerced := []string{}
		actual := schema.sanitize(example.Input, &coerced)
		sort.Strings(coerced)
		if !reflect.DeepEqual(actual, example.Sanitized) {
			t.Errorf("case %d normalization mismatch: input=%v", index, example.Input)
		}
		if !reflect.DeepEqual(coerced, example.Coerced) {
			t.Errorf("case %d coerced = %v; want %v", index, coerced, example.Coerced)
		}
	}
}

func TestNativeSettingsUploadEnvironmentValidationAndPrecedence(t *testing.T) {
	for _, value := range []string{"", "0", "-1", "invalid", "1.5"} {
		t.Run("invalid_"+value, func(t *testing.T) {
			s := nativeSettingsFixtureServer(t)
			t.Setenv("YOMUX_FILE_TRANSFER_MAX_BYTES", value)
			if _, err := s.nativeSettingsSchema(); err == nil {
				t.Fatal("invalid upload limit was silently replaced")
			}
		})
	}
	t.Run("canonical_precedence", func(t *testing.T) {
		s := nativeSettingsFixtureServer(t)
		t.Setenv("YOMUX_FILE_TRANSFER_MAX_BYTES", "1048576")
		t.Setenv("YOLOMUX_FILE_TRANSFER_MAX_BYTES", "invalid")
		t.Setenv("YOMUX_UPLOAD_MAX_BYTES", "2097152")
		schema, err := s.nativeSettingsSchema()
		if err != nil {
			t.Fatal(err)
		}
		if got := settingsObject(settingsObject(schema["DEFAULT_SETTINGS"])["uploads"])["max_bytes"]; got != float64(1048576) {
			t.Fatalf("upload default = %v", got)
		}
	})
}

func TestNativeSettingsWithoutPython(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	t.Setenv("PATH", t.TempDir())
	request := httptest.NewRequest(http.MethodPost, "/api/settings", strings.NewReader(`{"settings":{"appearance":{"theme":"light","ui_font_size":999},"file_explorer":{"indexed_dirs":["/fixture"]}}}`))
	request = request.WithContext(context.WithValue(request.Context(), authContextKey{}, authUser{role: "admin"}))
	response := httptest.NewRecorder()
	s.settings(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("save without Python = %d %s", response.Code, response.Body.String())
	}
	restarted := &server{root: s.root, config: s.config}
	if err := restarted.loadFinderIndexSettings(); err != nil {
		t.Fatalf("startup without Python: %v", err)
	}
	if got := restarted.indexSettings.roots; !reflect.DeepEqual(got, []string{"/fixture"}) {
		t.Fatalf("startup roots = %v", got)
	}
	response = httptest.NewRecorder()
	restarted.settings(response, httptest.NewRequest(http.MethodGet, "/api/settings", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("read after restart = %d %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	appearance := settingsObject(settingsObject(payload["settings"])["appearance"])
	if appearance["theme"] != "light" || appearance["ui_font_size"] != float64(30) {
		t.Fatalf("persisted appearance = %v", appearance)
	}
	readonly := httptest.NewRecorder()
	s.settings(readonly, httptest.NewRequest(http.MethodPost, "/api/settings", strings.NewReader(`{"appearance":{"theme":"dark"}}`)))
	if readonly.Code != http.StatusForbidden {
		t.Fatalf("unauthorized write = %d", readonly.Code)
	}
}

func TestNativeSettingsSavedTransferCapIsEnforced(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	payload, err := s.nativeSettingsPayload(context.Background(), map[string]any{"uploads": map[string]any{"max_bytes": float64(1048576)}}, true)
	if err != nil {
		t.Fatal(err)
	}
	if payload["error"] != "" {
		t.Fatal(payload["error"])
	}
	if got := s.fileTransferMaxBytes(); got != 1048576 {
		t.Fatalf("saved 1 MiB transfer cap enforced as %d bytes", got)
	}
}

func TestNativeSettingsYAMLNumericAndNonMappingCompatibility(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	for _, example := range []struct {
		source   string
		interval float64
	}{
		{"performance:\n  auto_approve_interval_seconds: .inf\n", 4},
		{"performance:\n  auto_approve_interval_seconds: .nan\n", 4},
		{"performance:\n  auto_approve_interval_seconds: -.inf\n", 0.1},
		{"ignored:\n  arbitrary: .nan\n", 0.5},
		{"[legacy, ignored]\n", 0.5},
	} {
		if err := os.WriteFile(filepath.Join(s.config, "settings.yaml"), []byte(example.source), 0600); err != nil {
			t.Fatal(err)
		}
		payload, err := s.nativeSettingsPayload(context.Background(), nil, false)
		if err != nil {
			t.Fatal(err)
		}
		if payload["error"] != "" {
			t.Errorf("valid YAML rejected: %s: %v", example.source, payload["error"])
			continue
		}
		if got := settingsObject(settingsObject(payload["settings"])["performance"])["auto_approve_interval_seconds"]; got != example.interval {
			t.Errorf("interval = %v; want %v", got, example.interval)
		}
	}
}

func TestNativeSettingsConcurrencyPreservesIndependentPatches(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	var group sync.WaitGroup
	results := make(chan error, 16)
	for index := 0; index < 16; index++ {
		group.Add(1)
		go func(index int) {
			defer group.Done()
			peer := &server{root: s.root, config: s.config}
			patch := map[string]any{"general": map[string]any{"startup_tips": false}}
			if index%2 == 0 {
				patch = map[string]any{"appearance": map[string]any{"theme": "light"}}
			}
			payload, err := peer.nativeSettingsPayload(context.Background(), patch, true)
			if err == nil {
				if message, _ := payload["error"].(string); message != "" {
					err = errors.New(message)
				}
			}
			results <- err
		}(index)
	}
	group.Wait()
	close(results)
	for err := range results {
		if err != nil {
			t.Fatal(err)
		}
	}
	payload, err := s.nativeSettingsPayload(context.Background(), nil, false)
	if err != nil {
		t.Fatal(err)
	}
	settings := settingsObject(payload["settings"])
	if settingsObject(settings["general"])["startup_tips"] != false || settingsObject(settings["appearance"])["theme"] != "light" {
		t.Fatal("concurrent patches lost an independent value")
	}
}

func TestNativeSettingsMalformedFileIsPreserved(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	path := filepath.Join(s.config, "settings.yaml")
	original := []byte("general: [unterminated\n")
	if err := os.WriteFile(path, original, 0600); err != nil {
		t.Fatal(err)
	}
	payload, err := s.nativeSettingsPayload(context.Background(), map[string]any{"general": map[string]any{"startup_tips": false}}, true)
	if err != nil {
		t.Fatal(err)
	}
	if payload["settings"] != nil || payload["error"] == "" {
		t.Fatalf("malformed result = %v", payload)
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != string(original) {
		t.Fatal("malformed settings overwritten")
	}
}

func TestNativeSettingsPricingHistoryAndHandEditRestart(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	payload, err := s.nativeSettingsPayload(context.Background(), map[string]any{"cost": map[string]any{"openai_pricing_profile": "subscription", "_openai_pricing_profile_history": []any{"0|subscription"}}}, true)
	if err != nil {
		t.Fatal(err)
	}
	cost := settingsObject(settingsObject(payload["settings"])["cost"])
	history := settingsProfileHistory(cost["_openai_pricing_profile_history"])
	if len(history) != 2 || history[0].profile != "default" || history[1].profile != "subscription" || history[1].at <= 0 {
		t.Fatalf("profile history = %v", history)
	}
	path := filepath.Join(s.config, "settings.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	data = []byte(strings.Replace(string(data), "openai_pricing_profile: subscription", "openai_pricing_profile: default", 1))
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	boundary := time.Unix(int64(history[1].at)+20, 0)
	if err := os.Chtimes(path, boundary, boundary); err != nil {
		t.Fatal(err)
	}
	restarted := &server{root: s.root, config: s.config}
	payload, err = restarted.nativeSettingsPayload(context.Background(), nil, false)
	if err != nil {
		t.Fatal(err)
	}
	history = settingsProfileHistory(settingsObject(settingsObject(payload["settings"])["cost"])["_openai_pricing_profile_history"])
	if len(history) != 3 || history[2].profile != "default" || history[2].at != float64(boundary.Unix()) {
		t.Fatalf("hand-edit history = %v", history)
	}
	payload, err = restarted.nativeSettingsPayload(context.Background(), nil, false)
	if err != nil {
		t.Fatal(err)
	}
	if got := settingsProfileHistory(settingsObject(settingsObject(payload["settings"])["cost"])["_openai_pricing_profile_history"]); !reflect.DeepEqual(got, history) {
		t.Fatal("restart changed effective history")
	}
}

func TestNativeSettingsPOSIXLockInteroperates(t *testing.T) {
	if os.Getenv("YOMUX_SETTINGS_LOCK_CHILD") == "1" {
		ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
		defer cancel()
		release, err := lockNativeSettings(ctx, os.Getenv("YOMUX_SETTINGS_LOCK_PATH"))
		if release != nil {
			_ = release()
		}
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("peer entered a held POSIX record lock: %v", err)
		}
		return
	}
	path := filepath.Join(t.TempDir(), "settings.yaml")
	release, err := lockNativeSettings(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if release != nil {
			if err := release(); err != nil {
				t.Error(err)
			}
		}
	}()
	command := exec.Command(os.Args[0], "-test.run=^TestNativeSettingsPOSIXLockInteroperates$")
	// The child receives no credentials or unrelated runtime ownership.
	command.Env = []string{"YOMUX_SETTINGS_LOCK_CHILD=1", "YOMUX_SETTINGS_LOCK_PATH=" + path}
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("peer lock probe: %v %s", err, output)
	}
	if err := release(); err != nil {
		t.Fatal(err)
	}
	release = nil
	next, err := lockNativeSettings(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := next(); err != nil {
		t.Fatal(err)
	}
}

func TestNativeSettingsPythonWriterPreservesIndependentPatch(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "python3", "-c", `import sys
from pathlib import Path
from yomux_lib.workspace.settings import locked_settings_file, _read_settings_file_unlocked, _write_settings_file_unlocked, merge_settings
path = Path(sys.argv[1])
with locked_settings_file(path):
    current, error = _read_settings_file_unlocked(path)
    if error:
        raise RuntimeError(error)
    print("locked", flush=True)
    sys.stdin.readline()
    _write_settings_file_unlocked(merge_settings(current, {"general": {"startup_tips": False}}), path)
`, filepath.Join(s.config, "settings.yaml"))
	command.Dir = s.root
	command.Env = []string{"PATH=" + os.Getenv("PATH"), "HOME=" + t.TempDir(), "TMPDIR=" + t.TempDir(), "YOMUX_CONFIG_DIR=" + s.config, "YOMUX_AUTH_CONFIG_READ_ONLY=1", "PYTHONDONTWRITEBYTECODE=1"}
	var stderr bytes.Buffer
	command.Stderr = &stderr
	input, err := command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	output, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	waited := false
	defer func() {
		_ = input.Close()
		if !waited {
			cancel()
			_ = command.Wait()
		}
	}()
	line, err := bufio.NewReader(output).ReadString('\n')
	if err != nil || line != "locked\n" {
		t.Fatalf("Python writer did not acquire lock: %q %v %s", line, err, stderr.String())
	}
	blocked, stop := context.WithTimeout(context.Background(), 100*time.Millisecond)
	_, err = s.nativeSettingsPayload(blocked, map[string]any{"appearance": map[string]any{"theme": "light"}}, true)
	stop()
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Go writer entered Python's POSIX lock: %v", err)
	}
	done := make(chan error, 1)
	go func() {
		payload, err := s.nativeSettingsPayload(ctx, map[string]any{"appearance": map[string]any{"theme": "light"}}, true)
		if err == nil {
			if message, _ := payload["error"].(string); message != "" {
				err = errors.New(message)
			}
		}
		done <- err
	}()
	if err := input.Close(); err != nil {
		t.Fatal(err)
	}
	err = command.Wait()
	waited = true
	if err != nil {
		t.Fatalf("Python writer failed: %v %s", err, stderr.String())
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	payload, err := s.nativeSettingsPayload(ctx, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	current := settingsObject(payload["settings"])
	if settingsObject(current["general"])["startup_tips"] != false || settingsObject(current["appearance"])["theme"] != "light" {
		t.Fatal("Go/Python writers lost an independent patch")
	}
}
