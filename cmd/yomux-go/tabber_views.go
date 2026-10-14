// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

func isOwnedTabberViewSession(name, sourceID, sourceName string) bool {
	return isTabberViewSessionName(name) && strings.TrimSpace(sourceID) != "" && strings.TrimSpace(sourceName) != ""
}

func (s *server) tabberViewSessionOwned(ctx context.Context, session string) (bool, error) {
	if !isTabberViewSessionName(session) {
		return false, nil
	}
	output, err := s.tmuxCommandContext(ctx, "display-message", "-p", "-t", s.exactTarget(session), "#{@yomux_view_source_id}\t#{@yomux_view_source}").Output()
	if err != nil {
		return false, err
	}
	fields := strings.Split(strings.TrimRight(string(output), "\r\n"), "\t")
	return len(fields) == 2 && isOwnedTabberViewSession(session, fields[0], fields[1]), nil
}

type tabberViewSessionRequest struct {
	Session  string `json:"session"`
	Window   string `json:"window"`
	WindowID string `json:"window_id"`
}

func tabberViewSessionName(port int, sourceID, windowID string) string {
	identity := fmt.Sprintf("%d\x1f%s\x1f%s", port, sourceID, windowID)
	digest := sha256.Sum256([]byte(identity))
	return fmt.Sprintf("%s%d_%s", stats.TabberViewSessionPrefix, port, hex.EncodeToString(digest[:12]))
}

func (s *server) tabberViewSession(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var request tabberViewSessionRequest
	if err := decodeJSONBody(r.Body, 16*1024, &request); err != nil {
		jsonResponse(w, http.StatusBadRequest, typedError("invalid request body", "common.invalidRequest", false))
		return
	}
	request.Session = strings.TrimSpace(request.Session)
	request.Window = strings.TrimSpace(request.Window)
	request.WindowID = strings.TrimSpace(request.WindowID)
	window, err := strconv.Atoi(request.Window)
	if request.Session == "" || request.WindowID == "" || err != nil || window < 0 {
		jsonResponse(w, http.StatusBadRequest, typedError("invalid Tabber window target", "common.invalidRequest", false))
		return
	}
	s.tmuxRosterFetchMu.Lock()
	defer s.tmuxRosterFetchMu.Unlock()
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	if err := s.tmuxCommandContext(ctx, "has-session", "-t", s.exactTarget(request.Session)).Run(); err != nil {
		jsonResponse(w, http.StatusNotFound, typedError("tmux session not found", "common.sessionNotFound", false))
		return
	}
	owned, err := s.tabberViewSessionOwned(ctx, request.Session)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, typedError("could not verify tmux view ownership", "common.requestFailed", true))
		return
	}
	if owned {
		jsonResponse(w, http.StatusBadRequest, typedError("invalid Tabber window target", "common.invalidRequest", false))
		return
	}
	sourceIDOutput, err := s.tmuxCommandContext(ctx, "display-message", "-p", "-t", s.exactTarget(request.Session), "#{session_id}").Output()
	if err != nil || strings.TrimSpace(string(sourceIDOutput)) == "" {
		jsonResponse(w, http.StatusServiceUnavailable, typedError("could not identify tmux session", "common.requestFailed", true))
		return
	}
	sourceID := strings.TrimSpace(string(sourceIDOutput))
	windows, err := s.tmuxCommandContext(ctx, "list-windows", "-t", s.exactTarget(request.Session), "-F", "#{window_index}\t#{window_id}").Output()
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, typedError("could not read tmux windows", "common.requestFailed", true))
		return
	}
	windowExists := false
	for _, line := range strings.Split(strings.TrimSpace(string(windows)), "\n") {
		fields := strings.SplitN(strings.TrimSpace(line), "\t", 2)
		if len(fields) == 2 && fields[0] == strconv.Itoa(window) && fields[1] == request.WindowID {
			windowExists = true
			break
		}
	}
	if !windowExists {
		jsonResponse(w, http.StatusNotFound, typedError("tmux window not found", "common.requestFailed", false))
		return
	}
	viewSession := tabberViewSessionName(s.port, sourceID, request.WindowID)
	createdViewSession := false
	if err := s.tmuxCommandContext(ctx, "has-session", "-t", s.exactTarget(viewSession)).Run(); err != nil {
		if err := s.tmuxCommandContext(ctx, "new-session", "-d", "-s", viewSession, "-t", s.exactTarget(request.Session)).Run(); err != nil {
			jsonResponse(w, http.StatusServiceUnavailable, typedError("could not create an independent tmux window view", "common.requestFailed", true))
			return
		}
		createdViewSession = true
		// Source identity is the readiness marker consumed by every server's
		// roster. Publish it last so an observer cannot retire a partial view.
		args := []string{}
		for _, option := range [][2]string{
			{"@yomux_view_source", request.Session},
			{"@yomux_view_window", strconv.Itoa(window)},
			{"@yomux_view_window_id", request.WindowID},
			{"@yomux_view_source_id", sourceID},
		} {
			if len(args) > 0 {
				args = append(args, ";")
			}
			args = append(args, "set-option", "-t", s.exactTarget(viewSession), option[0], option[1])
		}
		if err := s.tmuxCommandContext(ctx, args...).Run(); err != nil {
			_ = s.tmuxCommandContext(ctx, "kill-session", "-t", s.exactTarget(viewSession)).Run()
			jsonResponse(w, http.StatusServiceUnavailable, typedError("could not record Tabber view ownership", "common.requestFailed", true))
			return
		}
	} else {
		source, sourceErr := s.tmuxCommandContext(ctx, "show-options", "-qv", "-t", s.exactTarget(viewSession), "@yomux_view_source_id").Output()
		view, windowErr := s.tmuxCommandContext(ctx, "show-options", "-qv", "-t", s.exactTarget(viewSession), "@yomux_view_window_id").Output()
		if sourceErr != nil || windowErr != nil || strings.TrimSpace(string(source)) != sourceID || strings.TrimSpace(string(view)) != request.WindowID {
			jsonResponse(w, http.StatusConflict, typedError("Tabber view session identity changed", "common.requestFailed", false))
			return
		}
		if err := s.tmuxCommandContext(ctx, "set-option", "-t", s.exactTarget(viewSession), "@yomux_view_source", request.Session).Run(); err != nil {
			jsonResponse(w, http.StatusServiceUnavailable, typedError("could not update Tabber source name", "common.requestFailed", true))
			return
		}
	}
	// Window indexes can be reused while this request is creating or reopening the
	// grouped view. Keep the stable window ID, but scope it to this view session:
	// selecting a bare window ID can change another linked session's current window.
	windowTarget := s.exactTarget(viewSession) + request.WindowID
	if err := s.tmuxCommandContext(ctx, "select-window", "-t", windowTarget).Run(); err != nil {
		if createdViewSession {
			_ = s.tmuxCommandContext(ctx, "kill-session", "-t", s.exactTarget(viewSession)).Run()
		}
		jsonResponse(w, http.StatusNotFound, typedError("could not select tmux window", "common.requestFailed", false))
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "session": viewSession, "source_session": request.Session, "window": window, "window_id": request.WindowID})
}
