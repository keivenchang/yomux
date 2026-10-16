// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestFirstTabberContextDoesNotUseLaterUserTurn(t *testing.T) {
	path := filepath.Join(t.TempDir(), "conversation.jsonl")
	for _, test := range []struct{ data, want string }{
		{`{"type":"user","message":{"role":"user","content":"Fix tabber selection"}}` + "\n" + `{"type":"user","message":{"role":"user","content":"Later request"}}`, "Fix tabber selection"},
		{`{"type":"event_msg","payload":{"type":"user_message","message":"Build a report"}}`, "Build a report"},
		{`{"type":"user","message":{"role":"user","content":"First request"}}` + "\n" + `{"type":"custom-title","customTitle":"Conversation title"}`, "Conversation title"},
		{`{"type":"assistant","message":{"role":"assistant","content":"No user yet"}}`, ""},
	} {
		if err := os.WriteFile(path, []byte(test.data+"\n"), 0600); err != nil {
			t.Fatal(err)
		}
		if got := firstTabberConversationDescription(path); got != test.want {
			t.Fatalf("got %q want %q", got, test.want)
		}
	}
}

func TestTabberWindowDescriptionsPersistAndManualWinsOverlap(t *testing.T) {
	socket := filepath.Join(testsupport.ShortTempDir(t), "t.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	tmux := func(args ...string) string {
		t.Helper()
		output, err := exec.Command("tmux", append([]string{"-S", socket}, args...)...).CombinedOutput()
		if err != nil {
			t.Fatalf("tmux %v: %v (%s)", args, err, output)
		}
		return strings.TrimSpace(string(output))
	}
	tmux("new-session", "-d", "-s", "source")
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	tmux("new-window", "-d", "-t", "=source:")
	first := tmux("display-message", "-p", "-t", "=source:0", "#{window_id}")
	second := tmux("display-message", "-p", "-t", "=source:1", "#{window_id}")
	s := &server{}
	var wait sync.WaitGroup
	errors := make(chan error, 2)
	for _, manual := range []bool{false, true} {
		wait.Add(1)
		go func(manual bool) {
			defer wait.Done()
			text := "automatic"
			if manual {
				text = "manual"
			}
			writer := &server{}
			_, err := writer.saveTabberDescription(context.Background(), first, text, manual)
			errors <- err
		}(manual)
	}
	wait.Wait()
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatal(err)
		}
	}
	literal := `second window $HOME 'quotes' "double" \slash`
	if _, err := s.saveTabberDescription(context.Background(), second, literal, false); err != nil {
		t.Fatal(err)
	}
	tmux("rename-session", "-t", "=source:", "renamed")
	// A new app server must read the same window-owned values, independent of its cache.
	restarted := &server{}
	for _, test := range []struct{ id, want string }{{first, "manual"}, {second, literal}} {
		raw, err := restarted.saveTabberDescription(context.Background(), test.id, "later context", false)
		if err != nil {
			t.Fatal(err)
		}
		fields := map[string]any{}
		tabberDescriptionFields(fields, raw)
		if fields["description"] != test.want {
			t.Fatalf("window %s: %#v", test.id, fields)
		}
	}
	// An intentionally empty manual description also permanently suppresses automatic naming.
	if _, err := restarted.saveTabberDescription(context.Background(), first, "", true); err != nil {
		t.Fatal(err)
	}
	raw, err := restarted.saveTabberDescription(context.Background(), first, "automatic again", false)
	if err != nil {
		t.Fatal(err)
	}
	fields := map[string]any{}
	tabberDescriptionFields(fields, raw)
	if fields["description"] != "" || fields["description_manual"] != true {
		t.Fatal(fields)
	}
	for _, window := range []string{"999", "-1", "x"} {
		body, _ := json.Marshal(map[string]string{"session": "renamed", "window": window, "description": "bad target"})
		recorder := httptest.NewRecorder()
		restarted.tabberDescription(recorder, httptest.NewRequest(http.MethodPost, "/api/tabber-description", strings.NewReader(string(body))))
		if recorder.Code == http.StatusOK {
			t.Fatalf("invalid target %s accepted", window)
		}
	}
}

func TestEmptyDescriptionKeepsLastWindowIdentity(t *testing.T) {
	s := &server{}
	output := []byte("1\t0\tbash\t0\t%1\t/tmp\tbash\tplain\t0\t1\t1\t@1\t\n")
	info := s.liveSessionMetadataFromOutput("1", output, nil)
	windows := info["window_metadata"].([]map[string]any)
	if len(windows) != 1 || windows[0]["window_id"] != "@1" {
		t.Fatalf("lost empty trailing field: %#v", windows)
	}
}

func TestTabberDescriptionRejectsReusedWindowIndex(t *testing.T) {
	socket := filepath.Join(testsupport.ShortTempDir(t), "t.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	tmux := func(args ...string) string {
		t.Helper()
		output, err := exec.Command("tmux", append([]string{"-S", socket}, args...)...).CombinedOutput()
		if err != nil {
			t.Fatalf("tmux %v: %v (%s)", args, err, output)
		}
		return strings.TrimSpace(string(output))
	}
	tmux("new-session", "-d", "-s", "reuse")
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	tmux("new-window", "-d", "-t", "=reuse:")
	tmux("set-option", "-t", "=reuse:", "renumber-windows", "on")
	oldID := tmux("display-message", "-p", "-t", "=reuse:0", "#{window_id}")
	tmux("kill-window", "-t", "=reuse:0")
	newID := tmux("display-message", "-p", "-t", "=reuse:0", "#{window_id}")
	if oldID == newID {
		t.Fatalf("test requires a different stable window id after index reuse: %s", oldID)
	}
	s := &server{root: t.TempDir()}
	if _, err := s.saveTabberDescription(context.Background(), newID, "replacement", true); err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(map[string]string{"session": "reuse", "window": "0", "window_id": oldID, "description": "stale rename"})
	recorder := httptest.NewRecorder()
	s.tabberDescription(recorder, httptest.NewRequest(http.MethodPost, "/api/tabber-description", strings.NewReader(string(body))))
	if recorder.Code != http.StatusConflict {
		t.Fatalf("stale window id accepted: %d %s", recorder.Code, recorder.Body.String())
	}
	raw, err := s.saveTabberDescription(context.Background(), newID, "automatic", false)
	if err != nil {
		t.Fatal(err)
	}
	fields := map[string]any{}
	tabberDescriptionFields(fields, raw)
	if fields["description"] != "replacement" || fields["description_manual"] != true {
		t.Fatalf("replacement window was changed by stale rename: %#v", fields)
	}
}

func TestReservedLookingUnownedSessionIsNeverRetired(t *testing.T) {
	socket := filepath.Join(testsupport.ShortTempDir(t), "t.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	name := "__yomux_view_notes"
	if output, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", name).CombinedOutput(); err != nil {
		t.Fatalf("%v: %s", err, output)
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	s := &server{}
	roster, err := s.tmuxSessionRosterSnapshot(true)
	if err != nil || len(roster.sessions) != 1 || roster.sessions[0] != name {
		t.Fatalf("ordinary session hidden: %#v, %v", roster.sessions, err)
	}
	if err := exec.Command("tmux", "-S", socket, "has-session", "-t", "="+name+":").Run(); err != nil {
		t.Fatalf("ordinary session was deleted: %v", err)
	}
}

func TestTabberSimultaneousNumericCreationPreservesExistingNames(t *testing.T) {
	socket := filepath.Join(testsupport.ShortTempDir(t), "t.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	for _, name := range []string{"1", "3", "existing-name"} {
		if out, err := exec.Command("tmux", "-S", socket, "new-session", "-d", "-s", name).CombinedOutput(); err != nil {
			t.Fatalf("%v: %s", err, out)
		}
	}
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	s := &server{root: t.TempDir()}
	results := make(chan *httptest.ResponseRecorder, 2)
	var wait sync.WaitGroup
	for i := 0; i < 2; i++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			r := httptest.NewRecorder()
			s.createSession(r, httptest.NewRequest(http.MethodPost, "/api/create-session?agent=term&terminal=bash", nil))
			results <- r
		}()
	}
	wait.Wait()
	close(results)
	names := map[string]bool{}
	for r := range results {
		if r.Code != http.StatusOK {
			t.Fatalf("create: %d %s", r.Code, r.Body.String())
		}
		var result struct {
			Session string `json:"session"`
		}
		if err := json.Unmarshal(r.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		names[result.Session] = true
	}
	if len(names) != 2 || !names["2"] || !names["4"] {
		t.Fatal(names)
	}
	for _, name := range []string{"1", "3", "existing-name"} {
		if err := exec.Command("tmux", "-S", socket, "has-session", "-t", "="+name+":").Run(); err != nil {
			t.Fatalf("lost %s: %v", name, err)
		}
	}
}
