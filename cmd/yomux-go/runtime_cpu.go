// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/base64"
	"net/http"
	"strconv"
)

func (s *server) runtimeCPUProfile(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	target := r.URL.Query().Get("target")
	if target == "" {
		target = "server"
	}
	if target != "server" && target != "worker" {
		jsonResponse(w, 400, map[string]any{"error": "target must be server or worker"})
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		w.Header().Set("Allow", "GET, POST")
		w.WriteHeader(405)
		return
	}
	s.cpuCaptureMu.Lock()
	defer s.cpuCaptureMu.Unlock()
	seconds := 30
	if raw := r.URL.Query().Get("seconds"); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil || value < 1 || value > 60 {
			jsonResponse(w, 400, map[string]any{"error": "seconds must be 1-60"})
			return
		}
		seconds = value
	}
	mode := "status"
	if r.Method == http.MethodPost {
		mode = "start"
	} else if queryTrue(r.URL.Query().Get("artifact")) {
		mode = "artifact"
	}
	if mode == "start" {
		active := s.cpuCapture.Status()["active"] == true
		if s.searchIndex != nil {
			status, err := s.searchIndex.Profile(r.Context(), "status", 0)
			if err != nil {
				jsonResponse(w, 503, map[string]any{"error": err.Error()})
				return
			}
			active = active || status["active"] == true
		}
		if active {
			jsonResponse(w, 409, map[string]any{"error": "CPU capture already running"})
			return
		}
	}
	var payload map[string]any
	var artifact []byte
	if target == "worker" {
		if s.searchIndex == nil {
			jsonResponse(w, 503, map[string]any{"error": "index worker unavailable"})
			return
		}
		status, err := s.searchIndex.Profile(r.Context(), mode, seconds)
		if err != nil {
			jsonResponse(w, 503, map[string]any{"error": err.Error()})
			return
		}
		payload = status
		if mode == "artifact" {
			encoded, _ := status["artifact"].(string)
			artifact, err = base64.StdEncoding.DecodeString(encoded)
			if err != nil {
				jsonResponse(w, 503, map[string]any{"error": err.Error()})
				return
			}
			delete(payload, "artifact")
		}
	} else {
		if mode == "start" {
			if err := s.cpuCapture.Start(seconds); err != nil {
				jsonResponse(w, 503, map[string]any{"error": err.Error()})
				return
			}
		}
		payload = s.cpuCapture.Status()
		if mode == "artifact" {
			artifact = s.cpuCapture.Artifact()
		}
	}
	if mode == "artifact" {
		if len(artifact) == 0 {
			jsonResponse(w, 404, map[string]any{"error": "No completed CPU capture"})
			return
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Content-Disposition", `attachment; filename="cpu.pprof"`)
		_, _ = w.Write(artifact)
		return
	}
	payload["target"] = target
	status := http.StatusOK
	if mode == "start" {
		status = http.StatusAccepted
	}
	jsonResponse(w, status, payload)
}
