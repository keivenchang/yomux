// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
)

func TestInotifyWarningSurvivesUnavailableCompatibilityTools(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux kernel limit warning")
	}
	for _, availableLibc := range []bool{false, true} {
		tools := t.TempDir()
		t.Setenv("PATH", tools)
		if availableLibc {
			if err := os.WriteFile(filepath.Join(tools, "getconf"), []byte("#!/bin/sh\nprintf 'glibc 2.40\\n'\n"), 0o700); err != nil {
				t.Fatal(err)
			}
		}
		measured := map[string]string{"max_user_instances": "128", "max_user_watches": "2097152", "max_queued_events": "16384"}
		response := httptest.NewRecorder()
		(&server{}).runtimeCompatibilityWithReadFile(response, httptest.NewRequest("GET", "/api/runtime/compatibility", nil), func(path string) ([]byte, error) {
			return []byte(measured[filepath.Base(path)]), nil
		})
		var payload struct {
			ProbeStatus string              `json:"probe_status"`
			Inotify     inotifyLimitsReport `json:"inotify_limits"`
			Warnings    []struct {
				Code      string            `json:"code"`
				Key       string            `json:"message_key"`
				Params    map[string]string `json:"message_params"`
				TimeoutMS int               `json:"timeout_ms"`
			} `json:"warnings"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		if len(payload.Warnings) != 1 || payload.Warnings[0].Code != "inotify_limits_low" {
			t.Fatalf("low inotify warning lost when compatibility tools unavailable: %s", response.Body.String())
		}
		warning := payload.Warnings[0]
		if warning.Key != "runtime.warning.inotifyLimits" || warning.TimeoutMS != 2*60*60*1000 || warning.Params["command"] != "sudo sysctl -w fs.inotify.max_user_instances=1024 fs.inotify.max_queued_events=65536" || strings.Contains(warning.Params["limits"], "max_user_watches") {
			t.Fatalf("inotify warning descriptor: %+v", warning)
		}
		wantStatus := "libc_unavailable"
		if availableLibc {
			wantStatus = "tmux_unavailable"
		}
		if payload.ProbeStatus != wantStatus || payload.Inotify.Status != "measured" || len(payload.Inotify.Measured) != 3 {
			t.Fatalf("independent probe status: %s", response.Body.String())
		}
	}
}

func TestInotifyLimitsProbeBoundariesAndUnavailableMeasurements(t *testing.T) {
	baseline := probeInotifyLimits(func(string) ([]byte, error) { return []byte("999999999"), nil })
	if len(baseline.Recommended) != 3 {
		t.Fatalf("invalid shared runtime recommendations: %+v", baseline)
	}
	for _, name := range inotifyLimitNames {
		for _, delta := range []int64{-1, 0, 1} {
			report := probeInotifyLimits(func(path string) ([]byte, error) {
				key := filepath.Base(path)
				value := baseline.Recommended[key]
				if key == name {
					return []byte(strconv.FormatInt(int64(value)+delta, 10)), nil
				}
				return []byte(strconv.FormatUint(value, 10)), nil
			})
			wantLow := delta < 0
			if (len(report.warnings()) == 1) != wantLow || report.Status != "measured" || len(report.Errors) != 0 {
				t.Fatalf("name=%s delta=%d: %+v", name, delta, report)
			}
		}
	}
	for _, invalid := range []string{"", "-1", "1.5", "not a limit", "18446744073709551616"} {
		report := probeInotifyLimits(func(path string) ([]byte, error) {
			if filepath.Base(path) == "max_user_instances" {
				return nil, errors.New("permission denied")
			}
			return []byte(invalid), nil
		})
		if report.Status != "unavailable" || len(report.Errors) != 3 || len(report.Measured) != 0 || len(report.warnings()) != 0 || report.Command != "" {
			t.Fatalf("unavailable measurements admitted: %+v", report)
		}
		if !strings.HasPrefix(report.Errors["max_user_instances"], "read:") || !strings.HasPrefix(report.Errors["max_user_watches"], "parse:") {
			t.Fatalf("read and parse failures lost: %+v", report)
		}
	}
	partial := probeInotifyLimits(func(path string) ([]byte, error) {
		if filepath.Base(path) == "max_user_watches" {
			return nil, errors.New("unavailable")
		}
		return []byte("0"), nil
	})
	if partial.Status != "partially_measured" || len(partial.Measured) != 2 || len(partial.warnings()) != 1 || strings.Contains(partial.Command, "max_user_watches") {
		t.Fatalf("partial measurement: %+v", partial)
	}
}

func TestStartupInotifyWarningsAreReadOnlyAndPlatformScoped(t *testing.T) {
	var stderr bytes.Buffer
	reads := 0
	read := func(path string) ([]byte, error) {
		reads++
		if !strings.HasPrefix(path, "/proc/sys/fs/inotify/") {
			t.Fatalf("unexpected probe path %q", path)
		}
		if filepath.Base(path) == "max_user_watches" {
			return nil, errors.New("permission denied")
		}
		return []byte("128"), nil
	}
	warnRuntimeInotifyLimits(&stderr, "darwin", read)
	if reads != 0 || stderr.Len() != 0 {
		t.Fatal("non-Linux startup probed Linux kernel limits")
	}
	warnRuntimeInotifyLimits(&stderr, "linux", read)
	if reads != 3 || !strings.Contains(stderr.String(), "max_user_instances=128") || !strings.Contains(stderr.String(), "sudo sysctl -w fs.inotify.max_user_instances=1024 fs.inotify.max_queued_events=65536") || !strings.Contains(stderr.String(), "max_user_watches could not be measured: read: permission denied") {
		t.Fatalf("startup warning: reads=%d stderr=%s", reads, stderr.String())
	}
	stderr.Reset()
	warnRuntimeInotifyLimits(&stderr, "linux", func(string) ([]byte, error) { return []byte("999999999"), nil })
	if stderr.Len() != 0 {
		t.Fatalf("limits above recommendations produced warning: %s", stderr.String())
	}
}

func TestTmuxGlibcCrashWarning(t *testing.T) {
	for _, test := range []struct {
		tmux, libc string
		want       bool
	}{
		{"3.4", "glibc 2.39", true}, {"tmux 3.7a", "glibc 2.38", true},
		{"3.8", "glibc 2.39", false}, {"3.8a", "glibc 2.39", false},
		{"4.0", "glibc 2.39", false}, {"3.4", "glibc 2.40", false},
		{"3.4", "musl 1.2.5", false}, {"unknown", "glibc 2.39", false},
		{"3.4", "unknown", false},
	} {
		if got := tmuxGlibcWarning(test.tmux, test.libc); got != test.want {
			t.Errorf("tmux=%q libc=%q: got %v want %v", test.tmux, test.libc, got, test.want)
		}
	}
}
