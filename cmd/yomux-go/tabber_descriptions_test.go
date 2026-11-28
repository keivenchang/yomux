// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestAITabberDescriptionMarkerFitsLimit(t *testing.T) {
	description := compactTabberDescription("[AI summary] " + strings.Repeat("x", 120))
	if description[:len("[AI summary] ")] != "[AI summary] " || len([]rune(description)) != 96 {
		t.Fatalf("AI marker or 96-character limit missing: %q (%d runes)", description, len([]rune(description)))
	}
}

func TestTabberDescriptionFieldsMarksExistingAISummaries(t *testing.T) {
	fields := map[string]any{}
	tabberDescriptionFields(fields, `{"text":"A previously generated summary","manual":false,"signature":"signed"}`)
	if fields["description"] != "[AI summary] A previously generated summary" {
		t.Fatalf("existing AI description was not marked: %#v", fields)
	}
	fields = map[string]any{}
	tabberDescriptionFields(fields, `{"text":"A manual description","manual":true,"signature":"signed"}`)
	if fields["description"] != "A manual description" {
		t.Fatalf("manual description was marked as AI: %#v", fields)
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
	// Clearing the manual description restores automatic naming.
	if _, err := restarted.saveTabberDescription(context.Background(), first, "", true); err != nil {
		t.Fatal(err)
	}
	raw, err := restarted.saveTabberDescription(context.Background(), first, "automatic again", false)
	if err != nil {
		t.Fatal(err)
	}
	fields := map[string]any{}
	tabberDescriptionFields(fields, raw)
	if fields["description"] != "automatic again" || fields["description_manual"] != false {
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

func TestTabberAutomaticDescriptionsCoalesceAndManualWins(t *testing.T) {
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
	tmux("new-session", "-d", "-s", "auto")
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	id := tmux("display-message", "-p", "-t", "=auto:0", "#{window_id}")
	s := nativeSettingsFixtureServer(t)
	other := nativeSettingsFixtureServer(t)
	if _, err := s.nativeSettingsPayload(context.Background(), map[string]any{
		"tabber": map[string]any{"codex_model": "claude-sonnet-5-5", "codex_effort": "high"},
	}, true); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	tmux("set-option", "-w", "-t", id, "@yomux_description_source", tabberDescriptionSource("codex", path, ""))
	activeTimestamp := time.Now().UTC().Format(time.RFC3339Nano)
	if err := os.WriteFile(path, []byte(`{"timestamp":"`+activeTimestamp+`","type":"event_msg","payload":{"type":"user_message","message":"Review the current parser"}}`+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	entered, release := make(chan struct{}), make(chan struct{})
	s.tabberDescriptionRun = func(ctx context.Context, root string, config map[string]any, backend, session string, resume bool, prompt string, emit func(string) error) (map[string]any, string, error) {
		if backend != "claude" || config["claude_model"] != "claude-sonnet-5-5" || config["claude_effort"] != "high" ||
			config["_description_data_only"] != true || !strings.Contains(prompt, "untrusted data") {
			return nil, "", errors.New("invalid description request")
		}
		close(entered)
		select {
		case <-release:
			return nil, "Generated title", nil
		case <-ctx.Done():
			return nil, "", ctx.Err()
		}
	}
	other.tabberDescriptionRun = func(context.Context, string, map[string]any, string, string, bool, string, func(string) error) (map[string]any, string, error) {
		return nil, "", errors.New("duplicate inference")
	}
	finished := make(chan error, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go func() { finished <- s.generateTabberDescription(ctx, id, "codex", path, "") }()
	select {
	case <-entered:
	case <-ctx.Done():
		t.Fatal("inference did not start", ctx.Err())
	}
	if err := other.generateTabberDescription(ctx, id, "codex", path, ""); err != nil {
		t.Fatal(err)
	}
	manual := `Manual #{danger} $HOME 'quoted' title`
	if _, err := other.saveTabberDescription(ctx, id, manual, true); err != nil {
		t.Fatal(err)
	}
	close(release)
	if err := <-finished; err != nil {
		t.Fatal(err)
	}
	fields := map[string]any{}
	tabberDescriptionFields(fields, tmux("show-options", "-wqv", "-t", id, "@yomux_description"))
	if fields["description"] != manual || fields["description_manual"] != true {
		t.Fatal(fields)
	}
	if _, err := other.saveTabberDescription(ctx, id, "", true); err != nil {
		t.Fatal(err)
	}
	tmux("set-option", "-w", "-t", id, "@yomux_description_attempt", "9999999999")
	tmux("set-option", "-w", "-t", id, "@yomux_description_attempt_source", tabberDescriptionSource("codex", path, ""))
	if err := other.resetTabberDescriptionAttempt(ctx, id); err != nil {
		t.Fatal(err)
	}
	calls := 0
	s.tabberDescriptionRun = func(context.Context, string, map[string]any, string, string, bool, string, func(string) error) (map[string]any, string, error) {
		calls++
		return nil, "Generated title", nil
	}
	if err := s.generateTabberDescription(ctx, id, "codex", path, ""); err != nil {
		t.Fatal(err)
	}
	tmux("set-option", "-w", "-t", id, "@yomux_description_attempt", "0")
	if err := s.generateTabberDescription(ctx, id, "codex", path, ""); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("unchanged transcript triggered %d calls", calls)
	}
	tabberDescriptionFields(fields, tmux("show-options", "-wqv", "-t", id, "@yomux_description"))
	if fields["description"] != "[AI summary] Generated title" || fields["description_manual"] != false {
		t.Fatal(fields)
	}
	oldTimestamp := time.Now().Add(-tabberDescriptionRecentActivityWindow - time.Minute).UTC().Format(time.RFC3339Nano)
	if err := os.WriteFile(path, []byte(`{"timestamp":"`+oldTimestamp+`","type":"event_msg","payload":{"type":"user_message","message":"An old request that must not replace the description"}}`+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	tmux("set-option", "-w", "-t", id, "@yomux_description_attempt", "0")
	if err := s.generateTabberDescription(ctx, id, "codex", path, ""); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("idle conversation triggered inference %d times", calls-1)
	}
	tabberDescriptionFields(fields, tmux("show-options", "-wqv", "-t", id, "@yomux_description"))
	if fields["description"] != "[AI summary] Generated title" {
		t.Fatalf("idle conversation changed description: %#v", fields)
	}
	activeTimestamp = time.Now().UTC().Format(time.RFC3339Nano)
	if err := os.WriteFile(path, []byte(`{"timestamp":"`+activeTimestamp+`","type":"event_msg","payload":{"type":"user_message","message":"Now debug Reload"}}`+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	s.tabberDescriptionRun = func(context.Context, string, map[string]any, string, string, bool, string, func(string) error) (map[string]any, string, error) {
		tmux("set-option", "-w", "-t", id, "@yomux_description_source", "replacement-conversation")
		return nil, "Stale conversation title", nil
	}
	if err := s.generateTabberDescription(ctx, id, "codex", path, ""); err != nil {
		t.Fatal(err)
	}
	tabberDescriptionFields(fields, tmux("show-options", "-wqv", "-t", id, "@yomux_description"))
	if fields["description"] != "[AI summary] Generated title" {
		t.Fatal("old conversation published", fields)
	}
	tmux("set-option", "-w", "-t", id, "@yomux_description_source", tabberDescriptionSource("codex", path, ""))
	tmux("set-option", "-w", "-t", id, "@yomux_description_attempt", "0")
	s.tabberDescriptionRun = func(ctx context.Context, _ string, _ map[string]any, _, _ string, _ bool, _ string, _ func(string) error) (map[string]any, string, error) {
		_, err := s.nativeSettingsPayload(ctx, map[string]any{"tabber": map[string]any{"automatic_descriptions": false}}, true)
		return nil, "Disabled while running", err
	}
	if err := s.generateTabberDescription(ctx, id, "codex", path, ""); err != nil {
		t.Fatal(err)
	}
	tabberDescriptionFields(fields, tmux("show-options", "-wqv", "-t", id, "@yomux_description"))
	if fields["description"] != "[AI summary] Generated title" {
		t.Fatal("disabled job published", fields)
	}
	if _, err := s.nativeSettingsPayload(ctx, map[string]any{"tabber": map[string]any{"automatic_descriptions": true}}, true); err != nil {
		t.Fatal(err)
	}
	for _, answer := range []string{"", " \t\n "} {
		tmux("set-option", "-w", "-t", id, "@yomux_description_attempt", "0")
		prior := tmux("show-options", "-wqv", "-t", id, "@yomux_description")
		s.tabberDescriptionRun = func(context.Context, string, map[string]any, string, string, bool, string, func(string) error) (map[string]any, string, error) {
			return nil, answer, nil
		}
		if err := s.generateTabberDescription(ctx, id, "codex", path, ""); err == nil {
			t.Fatalf("empty AI answer %q was accepted", answer)
		}
		if got := tmux("show-options", "-wqv", "-t", id, "@yomux_description"); got != prior {
			t.Fatalf("empty AI answer replaced prior description: %q", got)
		}
		if got := tmux("show-options", "-wqv", "-t", id, "@yomux_description_job"); got != "" {
			t.Fatalf("empty AI answer retained job lease: %q", got)
		}
	}
	tmux("set-option", "-w", "-t", id, "@yomux_description_attempt", "0")
	s.tabberDescriptionRun = func(context.Context, string, map[string]any, string, string, bool, string, func(string) error) (map[string]any, string, error) {
		return nil, "", errors.New("provider failure")
	}
	if err := s.generateTabberDescription(ctx, id, "codex", path, ""); err == nil {
		t.Fatal("failure discarded")
	}
	tabberDescriptionFields(fields, tmux("show-options", "-wqv", "-t", id, "@yomux_description"))
	if fields["description"] != "[AI summary] Generated title" {
		t.Fatal(fields)
	}
	tmux("new-session", "-d", "-s", "fresh")
	freshID := tmux("display-message", "-p", "-t", "=fresh:0", "#{window_id}")
	freshPath := filepath.Join(t.TempDir(), "fresh-transcript.jsonl")
	activeTimestamp = time.Now().UTC().Format(time.RFC3339Nano)
	if err := os.WriteFile(freshPath, []byte(`{"timestamp":"`+activeTimestamp+`","type":"event_msg","payload":{"type":"user_message","message":"Start a fresh conversation"}}`+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := s.generateTabberDescription(ctx, freshID, "codex", freshPath, ""); err == nil {
		t.Fatal("unavailable inference was not reported")
	}
	freshDescription := tmux("show-options", "-wqv", "-t", freshID, "@yomux_description")
	if freshDescription != "" {
		t.Fatalf("unavailable AI renamed a fresh window: %q", freshDescription)
	}
	tmux("set-option", "-w", "-t", id, "@yomux_description_attempt", "0")
	s.tabberDescriptionRun = func(ctx context.Context, _ string, _ map[string]any, _, _ string, _ bool, _ string, _ func(string) error) (map[string]any, string, error) {
		cancel()
		return nil, "", ctx.Err()
	}
	if err := s.generateTabberDescription(ctx, id, "codex", path, ""); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation lost: %v", err)
	}
	if got := tmux("show-options", "-wqv", "-t", id, "@yomux_description_job"); got != "" {
		t.Fatal("lease survived cancellation", got)
	}
	oldCtx, oldCancel := context.WithCancel(context.Background())
	defer oldCancel()
	s.producerContext = context.Background()
	s.tabberDescriptionJobs.Store(id, &tabberDescriptionJob{source: "old-conversation", cancel: oldCancel})
	s.queueTabberDescription(id, "", "", "")
	if !errors.Is(oldCtx.Err(), context.Canceled) {
		t.Fatal("missing transcript did not cancel old job")
	}
}

func TestTabberDescriptionTargetAndIndependentCadence(t *testing.T) {
	active := tabberDescriptionTarget{active: true, paneIndex: 3}
	inactive := tabberDescriptionTarget{paneIndex: 0}
	if !active.precedes(inactive) || inactive.precedes(active) || !(tabberDescriptionTarget{paneIndex: 1}).precedes(tabberDescriptionTarget{paneIndex: 2}) {
		t.Fatal("unstable physical-window source")
	}
	ctx, cancel := context.WithCancel(context.Background())
	ticks := make(chan struct{}, 8)
	done := make(chan struct{})
	go func() {
		defer close(done)
		runTabberDescriptionReconciler(ctx, time.Millisecond, func() error {
			select {
			case ticks <- struct{}{}:
			case <-ctx.Done():
			}
			return nil
		})
	}()
	for range 2 {
		select {
		case <-ticks:
		case <-time.After(time.Second):
			t.Fatal("browser-independent reconcile did not recur")
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("reconciler did not cancel")
	}
}

func TestTabberDescriptionReconcilesPhysicalWindowWithoutBrowser(t *testing.T) {
	socket := filepath.Join(testsupport.ShortTempDir(t), "t.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", socket)
	tmux := func(args ...string) string {
		t.Helper()
		out, err := exec.Command("tmux", append([]string{"-S", socket}, args...)...).CombinedOutput()
		if err != nil {
			t.Fatalf("tmux %v: %v %s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	tmux("new-session", "-d", "-s", "physical")
	t.Cleanup(func() { _ = exec.Command("tmux", "-S", socket, "kill-server").Run() })
	id := tmux("display-message", "-p", "-t", "=physical:0", "#{window_id}")
	s := nativeSettingsFixtureServer(t)
	database := filepath.Join(t.TempDir(), "opencode.db")
	createSessionSurfaceOpenCodeDB(t, database)
	db, err := sql.Open("sqlite3", database)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	// Session metadata is fresh in the shared fixture, but inference admission
	// follows recorded message/part activity rather than the session row.
	offset := time.Now().Add(-time.Minute).UnixMilli()
	for _, table := range []string{"message", "part"} {
		if _, err := db.Exec("UPDATE "+table+" SET time_created = time_created + ?, time_updated = time_updated + ? WHERE session_id = ?", offset, offset, "ses-open"); err != nil {
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	activity := (&server{}).agentConversationActivityTS(map[string]any{"kind": "opencode", "transcript": database, "session_id": "ses-open"})
	if activity <= 0 || time.Since(time.Unix(int64(activity), 0)) > tabberDescriptionRecentActivityWindow {
		t.Fatalf("fixture conversation is not recent enough for inference: %v", activity)
	}
	t.Setenv("YOMUX_OPENCODE_DATABASE", database)
	output := "physical\t0\topencode\t0\t%1\t/repo\topencode\told\t100\t0\t1\t" + id + "\t\n" +
		"physical\t0\topencode\t1\t%2\t/repo\topencode\tbuild\t200\t1\t1\t" + id + "\t\n"
	s.tmuxSessionRosterFn = func() ([]string, error) { return []string{"physical"}, nil }
	s.tmuxCommandContextFn = func(ctx context.Context, args ...string) *exec.Cmd {
		if args[0] == "list-panes" {
			return exec.CommandContext(ctx, "printf", "%s", output)
		}
		return exec.CommandContext(ctx, "tmux", append([]string{"-S", socket}, args...)...)
	}
	s.processEntriesFn = func() ([]procEntry, error) {
		return []procEntry{{PID: 100, Command: "opencode --session ses-other", Executable: "/bin/opencode"}, {PID: 200, Command: "opencode --session ses-open", Executable: "/bin/opencode"}}, nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.producerContext = ctx
	entered := make(chan context.Context, 4)
	s.tabberDescriptionRun = func(ctx context.Context, _ string, _ map[string]any, _, _ string, _ bool, prompt string, _ func(string) error) (map[string]any, string, error) {
		entered <- ctx
		<-ctx.Done()
		return nil, "", ctx.Err()
	}
	if err := s.reconcileTabberDescriptionTargets(); err != nil {
		t.Fatal(err)
	}
	var running context.Context
	select {
	case running = <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("no-browser reconciliation never generated a description")
	}
	if source := tmux("show-options", "-wqv", "-t", id, "@yomux_description_source"); source != tabberDescriptionSource("opencode", database, "ses-open") {
		t.Fatal("wrong physical-window source", source)
	}
	if err := s.reconcileTabberDescriptionTargets(); err != nil {
		t.Fatal(err)
	}
	if running.Err() != nil {
		t.Fatal("inactive pane canceled active source", running.Err())
	}
	select {
	case <-entered:
		t.Fatal("duplicate inference for same physical window")
	default:
	}
	s.processEntriesFn = func() ([]procEntry, error) { return nil, nil }
	output = strings.ReplaceAll(output, "\topencode\t", "\tbash\t")
	if err := s.reconcileTabberDescriptionTargets(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-running.Done():
	case <-time.After(time.Second):
		t.Fatal("exited agent did not cancel inference")
	}
	cancel()
	s.stopBackgroundWork()
}
