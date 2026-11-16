// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"
)

//go:embed runtime_limits.json
var runtimeLimitsJSON []byte

var inotifyLimitNames = []string{"max_user_instances", "max_user_watches", "max_queued_events"}

type inotifyLimitsReport struct {
	Status      string            `json:"status"`
	Measured    map[string]uint64 `json:"measured"`
	Recommended map[string]uint64 `json:"recommended"`
	Errors      map[string]string `json:"errors,omitempty"`
	Below       []string          `json:"below_recommended,omitempty"`
	Command     string            `json:"command,omitempty"`
}

func probeInotifyLimits(readFile func(string) ([]byte, error)) inotifyLimitsReport {
	report := inotifyLimitsReport{Status: "measured", Measured: map[string]uint64{}, Errors: map[string]string{}}
	var limits struct {
		Inotify map[string]uint64 `json:"inotify"`
	}
	if err := json.Unmarshal(runtimeLimitsJSON, &limits); err != nil {
		report.Status = "recommendations_unavailable"
		report.Errors["recommendations"] = err.Error()
		return report
	}
	if len(limits.Inotify) != len(inotifyLimitNames) {
		report.Status = "recommendations_unavailable"
		report.Errors["recommendations"] = "incomplete inotify recommendations"
		return report
	}
	var assignments []string
	for _, name := range inotifyLimitNames {
		if limits.Inotify[name] == 0 {
			report.Status = "recommendations_unavailable"
			report.Errors["recommendations"] = "invalid inotify recommendation: " + name
			return report
		}
	}
	report.Recommended = limits.Inotify
	for _, name := range inotifyLimitNames {
		body, err := readFile("/proc/sys/fs/inotify/" + name)
		if err != nil {
			report.Errors[name] = "read: " + err.Error()
			continue
		}
		value, err := strconv.ParseUint(strings.TrimSpace(string(body)), 10, 64)
		if err != nil {
			report.Errors[name] = "parse: " + err.Error()
			continue
		}
		report.Measured[name] = value
		if value < limits.Inotify[name] {
			report.Below = append(report.Below, fmt.Sprintf("fs.inotify.%s=%d (recommended >=%d)", name, value, limits.Inotify[name]))
			assignments = append(assignments, fmt.Sprintf("fs.inotify.%s=%d", name, limits.Inotify[name]))
		}
	}
	if len(assignments) > 0 {
		report.Command = "sudo sysctl -w " + strings.Join(assignments, " ")
	}
	if len(report.Errors) > 0 {
		report.Status = "partially_measured"
		if len(report.Measured) == 0 {
			report.Status = "unavailable"
		}
	}
	return report
}

func (report inotifyLimitsReport) warnings() []map[string]any {
	if len(report.Below) == 0 {
		return []map[string]any{}
	}
	return []map[string]any{{
		"code": "inotify_limits_low", "message_key": "runtime.warning.inotifyLimits",
		"message_params": map[string]string{"limits": strings.Join(report.Below, "; "), "command": report.Command},
		"timeout_ms":     2 * 60 * 60 * 1000,
	}}
}

func warnRuntimeInotifyLimits(stderr io.Writer, operatingSystem string, readFile func(string) ([]byte, error)) {
	if operatingSystem != "linux" {
		return
	}
	report := probeInotifyLimits(readFile)
	if len(report.Below) > 0 {
		fmt.Fprintf(stderr, "WARNING: Linux inotify limits are below recommended values: %s. To increase them, run: %s\n", strings.Join(report.Below, "; "), report.Command)
	}
	if len(report.Errors) > 0 {
		for _, name := range append([]string{"recommendations"}, inotifyLimitNames...) {
			if failure, ok := report.Errors[name]; ok {
				fmt.Fprintf(stderr, "WARNING: Linux inotify limit %s could not be measured: %s\n", name, failure)
			}
		}
	}
}

func tmuxGlibcWarning(tmuxVersion, libcVersion string) bool {
	var tmuxMajor, tmuxMinor, libcMajor, libcMinor int
	if _, err := fmt.Sscanf(strings.TrimPrefix(strings.TrimSpace(tmuxVersion), "tmux "), "%d.%d", &tmuxMajor, &tmuxMinor); err != nil {
		return false
	}
	if _, err := fmt.Sscanf(strings.TrimSpace(libcVersion), "glibc %d.%d", &libcMajor, &libcMinor); err != nil {
		return false
	}
	return (tmuxMajor < 3 || tmuxMajor == 3 && tmuxMinor < 8) && (libcMajor < 2 || libcMajor == 2 && libcMinor <= 39)
}

func (s *server) runtimeCompatibility(w http.ResponseWriter, r *http.Request) {
	s.runtimeCompatibilityWithReadFile(w, r, os.ReadFile)
}

func (s *server) runtimeCompatibilityWithReadFile(w http.ResponseWriter, r *http.Request, readFile func(string) ([]byte, error)) {
	warnings := []map[string]any{}
	payload := map[string]any{"warnings": warnings, "probe_status": "not_applicable"}
	if runtime.GOOS != "linux" {
		jsonResponse(w, http.StatusOK, payload)
		return
	}
	// Kernel limit evidence is independent of libc and a running tmux server.
	// Preserve it in every response, including those tools' early failures.
	inotify := probeInotifyLimits(readFile)
	warnings = append(warnings, inotify.warnings()...)
	payload["warnings"] = warnings
	payload["inotify_limits"] = inotify
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	libc, err := exec.CommandContext(ctx, "getconf", "GNU_LIBC_VERSION").Output()
	if err != nil {
		payload["probe_status"] = "libc_unavailable"
		jsonResponse(w, http.StatusOK, payload)
		return
	}
	// The installed client can be 3.8 while an old server remains alive after
	// package upgrade. Query that server through the normal socket owner.
	tmux, err := s.tmuxPointCommand(ctx, "display-message", "-p", "#{version}").Output()
	if err != nil {
		payload["probe_status"] = "tmux_unavailable"
		jsonResponse(w, http.StatusOK, payload)
		return
	}
	payload["probe_status"] = "measured"
	if tmuxGlibcWarning(string(tmux), string(libc)) {
		warnings = append(warnings, map[string]any{
			"code": "tmux_glibc_crash_risk", "message_key": "runtime.warning.tmuxGlibc",
			"message_params": map[string]string{"tmux": strings.TrimSpace(string(tmux)), "glibc": strings.TrimSpace(strings.TrimPrefix(string(libc), "glibc "))},
			"timeout_ms":     2 * 60 * 60 * 1000,
		})
		payload["warnings"] = warnings
	}
	jsonResponse(w, http.StatusOK, payload)
}
