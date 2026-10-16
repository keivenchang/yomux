// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestTabberViewSessionsKeepTmuxWindowsIndependentAndHidden(t *testing.T) {
	socket := filepath.Join(testsupport.ShortTempDir(t), "tmux.sock")
	t.Setenv("YOLOMUX_TMUX_SOCKET", socket)
	root := t.TempDir()
	if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", "source", "-c", root).CombinedOutput(); err != nil {
		t.Fatalf("create source session: %v (%s)", err, output)
	}
	if output, err := exec.Command("tmux", "-S", socket, "new-window", "-t", "=source:", "-n", "second").CombinedOutput(); err != nil {
		t.Fatalf("create second source window: %v (%s)", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })

	config := t.TempDir()
	if err := os.WriteFile(filepath.Join(config, "auth.yaml"), []byte("users:\n  - username: reader\n    password: reader\n    role: readonly\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "auth-cookie-secret"), []byte(strings.Repeat("11", 32)), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{port: 7110, config: config, events: newEventBroker()}
	viewHandler := s.protected(http.HandlerFunc(s.tabberViewSession))
	create := func(session, window string) (string, string) {
		t.Helper()
		windowIDOutput, err := exec.Command("tmux", "-S", socket, "display-message", "-p", "-t", "="+session+":"+window, "#{window_id}").Output()
		if err != nil {
			t.Fatalf("read window identity for %s:%s: %v", session, window, err)
		}
		windowID := strings.TrimSpace(string(windowIDOutput))
		body, err := json.Marshal(tabberViewSessionRequest{Session: session, Window: window, WindowID: windowID})
		if err != nil {
			t.Fatal(err)
		}
		response := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, "/api/tabber-view-session", bytes.NewReader(body))
		request.AddCookie(&http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(authUser{username: "reader", password: "reader", role: "readonly"})})
		viewHandler.ServeHTTP(response, request)
		if response.Code != http.StatusOK {
			t.Fatalf("create view for window %s: status=%d body=%s", window, response.Code, response.Body.String())
		}
		var payload struct {
			Session string `json:"session"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		if payload.Session == "" {
			t.Fatalf("window %s returned an empty view identity: %s", window, response.Body.String())
		}
		selectedWindowID, err := exec.Command("tmux", "-S", socket, "display-message", "-p", "-t", "="+payload.Session+":", "#{window_id}").Output()
		if err != nil || strings.TrimSpace(string(selectedWindowID)) != windowID {
			t.Fatalf("view %q selected window ID %q, %v; want stable source window ID %q", payload.Session, strings.TrimSpace(string(selectedWindowID)), err, windowID)
		}
		return payload.Session, windowID
	}
	first, firstWindowID := create("source", "0")
	if output, err := exec.Command("tmux", "-S", socket, "select-window", "-t", "="+first+":1").CombinedOutput(); err != nil {
		t.Fatalf("move the cached view to another window: %v (%s)", err, output)
	}
	if again, _ := create("source", "0"); again != first {
		t.Fatalf("same client/window view identity changed after reselection: %q != %q", again, first)
	}
	if output, err := exec.Command("tmux", "-S", socket, "display-message", "-p", "-t", "="+first+":", "#{window_index}").CombinedOutput(); err != nil || strings.TrimSpace(string(output)) != "0" {
		t.Fatalf("reopening the cached view did not restore window 0: got %q, %v", strings.TrimSpace(string(output)), err)
	}
	second, secondWindowID := create("source", "1")
	if first == second {
		t.Fatalf("different windows share a view identity: %q", first)
	}
	if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", "__yomux_view_crash_partial", "-t", "=source", "-c", root).CombinedOutput(); err != nil {
		t.Fatalf("create partially initialized internal view: %v (%s)", err, output)
	}
	if firstWindowID == secondWindowID {
		t.Fatalf("different windows share a tmux window ID: %q", firstWindowID)
	}
	if again, _ := create("source", "0"); again != first {
		t.Fatalf("reselecting window 0 changed its view identity: %q != %q", again, first)
	}
	for _, test := range []struct {
		session  string
		window   string
		windowID string
	}{{first, "0", firstWindowID}, {second, "1", secondWindowID}} {
		output, err := exec.Command("tmux", "-S", socket, "display-message", "-p", "-t", "="+test.session+":", "#{window_index}|#{window_id}").CombinedOutput()
		fields := strings.Split(strings.TrimSpace(string(output)), "|")
		if err != nil || len(fields) != 2 || fields[0] != test.window || fields[1] != test.windowID {
			t.Fatalf("view %q active window = %q, %v; want index %s and ID %s", test.session, strings.TrimSpace(string(output)), err, test.window, test.windowID)
		}
	}
	if roster, err := s.tmuxSessionRosterSnapshot(true); err != nil || len(roster.sessions) != 1 || roster.sessions[0] != "source" {
		t.Fatalf("Tabber internal sessions leaked into the selectable roster: %#v, %v", roster.sessions, err)
	}
	if output, err := exec.Command("tmux", "-S", socket, "kill-window", "-t", "=source:1").CombinedOutput(); err != nil {
		t.Fatalf("close source window 1: %v (%s)", err, output)
	}
	if _, err := s.tmuxSessionRosterSnapshot(true); err != nil {
		t.Fatalf("reconcile aliases after closing source window: %v", err)
	}
	if output, err := exec.Command("tmux", "-S", socket, "has-session", "-t", "="+second+":").CombinedOutput(); err == nil {
		t.Fatalf("closed source window left old view alias %q alive: %s", second, output)
	}
	replacementOutput, err := exec.Command("tmux", "-S", socket, "new-window", "-d", "-P", "-F", "#{window_index}|#{window_id}", "-t", "=source:", "-n", "replacement").CombinedOutput()
	if err != nil {
		t.Fatalf("create replacement source window: %v (%s)", err, replacementOutput)
	}
	replacementFields := strings.Split(strings.TrimSpace(string(replacementOutput)), "|")
	if len(replacementFields) != 2 || replacementFields[0] != "1" {
		t.Fatalf("replacement did not reuse window index 1: fields=%q output=%q", replacementFields, replacementOutput)
	}
	replacement, replacementWindowID := create("source", replacementFields[0])
	if replacementWindowID != replacementFields[1] {
		t.Fatalf("replacement window changed identity between tmux creation and alias request: %q != %q", replacementWindowID, replacementFields[1])
	}
	if replacement == second || replacementWindowID == secondWindowID {
		t.Fatalf("reused window number revived its old identity: alias %q -> %q, id %q -> %q", second, replacement, secondWindowID, replacementWindowID)
	}
	if output, err := exec.Command("tmux", "-S", socket, "rename-session", "-t", "=source:", "__yomux_view_notes").CombinedOutput(); err != nil {
		t.Fatalf("rename source session: %v (%s)", err, output)
	}
	if _, err := s.tmuxSessionRosterSnapshot(true); err != nil {
		t.Fatalf("reconcile aliases after source rename without reopening them: %v", err)
	}
	if renamed, _ := create("__yomux_view_notes", "0"); renamed != first {
		t.Fatalf("source rename changed its stable window view identity: %q != %q", renamed, first)
	}
	response := httptest.NewRecorder()
	s.tabberDescription(response, httptest.NewRequest(http.MethodPost, "/api/tabber-description", strings.NewReader(fmt.Sprintf(`{"session":"__yomux_view_notes","window":"1","window_id":%q,"description":"Distinct user window"}`, replacementWindowID))))
	if response.Code != http.StatusOK {
		t.Fatalf("unowned reserved-looking name cannot rename description: %d %s", response.Code, response.Body.String())
	}
	response = httptest.NewRecorder()
	s.tabberViewSession(response, httptest.NewRequest(http.MethodPost, "/api/tabber-view-session", strings.NewReader(fmt.Sprintf(`{"session":%q,"window":"0"}`, first))))
	if response.Code != http.StatusBadRequest {
		t.Fatalf("owned alias accepted as source: %d %s", response.Code, response.Body.String())
	}
	response = httptest.NewRecorder()
	s.killSession(response, httptest.NewRequest(http.MethodPost, "/api/kill-session?session=__yomux_view_notes", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("kill source session: status=%d body=%s", response.Code, response.Body.String())
	}
	for _, view := range []string{first, second, replacement} {
		if output, err := exec.Command("tmux", "-S", socket, "has-session", "-t", "="+view+":").CombinedOutput(); err == nil {
			t.Fatalf("source kill left grouped view %q alive: %s", view, output)
		}
	}
}
