// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/testsupport"
)

func newShareFixture(t *testing.T) (*server, string) {
	t.Helper()
	root := t.TempDir()
	path := filepath.Join(root, "shared.md")
	if err := os.WriteFile(path, []byte("hello\n"), 0600); err != nil {
		t.Fatal(err)
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(service.Close)
	return &server{finder: service, config: t.TempDir(), port: 9443}, path
}
func createShareFixture(t *testing.T, s *server, path string, writable bool, limit int) (string, string, *shareGrant) {
	t.Helper()
	return createNamedShareFixture(t, s, path, "Guest4821", writable, limit)
}
func createNamedShareFixture(t *testing.T, s *server, path, name string, writable bool, limit int) (string, string, *shareGrant) {
	t.Helper()
	saved, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	body := map[string]any{"guest": name, "minutes": 30, "limit": limit, "resources": []map[string]any{{"kind": "file", "path": path, "writable": writable, "saved_content": string(saved)}}}
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest("POST", "https://example.test/api/share", bytes.NewReader(data))
	r = r.WithContext(context.WithValue(r.Context(), authContextKey{}, authUser{username: "ShareAdmin", role: "admin"}))
	w := httptest.NewRecorder()
	s.shareAdmin(w, r)
	if w.Code != 201 {
		t.Fatalf("create %d %s", w.Code, w.Body.String())
	}
	var result struct {
		URL   string     `json:"url"`
		Grant shareGrant `json:"share"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	_, secret, _ := strings.Cut(result.URL, "#")
	if len(secret) != 20 || len(result.Grant.ID) != 20 {
		t.Fatalf("invitation code lengths: path=%d, fragment=%d", len(result.Grant.ID), len(secret))
	}
	return result.Grant.ID, secret, s.shares.grants[result.Grant.ID]
}

func TestShareRejectsDuplicateActiveGuestName(t *testing.T) {
	s, path := newShareFixture(t)
	id, _, _ := createShareFixture(t, s, path, false, 1)
	body, err := json.Marshal(map[string]any{
		"guest": " guest4821 ", "minutes": 5, "limit": 1,
		"resources": []shareSelection{{Kind: "file", Path: path}},
	})
	if err != nil {
		t.Fatal(err)
	}
	duplicate := httptest.NewRecorder()
	s.shareAdmin(duplicate, httptest.NewRequest(http.MethodPost, "https://example.test/api/share", bytes.NewReader(body)))
	if duplicate.Code != http.StatusConflict || !strings.Contains(duplicate.Body.String(), "guest_name_in_use") {
		t.Fatalf("duplicate name returned %d: %s", duplicate.Code, duplicate.Body.String())
	}
	if len(s.shares.grants) != 1 {
		t.Fatal("duplicate request published a share")
	}
	stopped := httptest.NewRecorder()
	s.shareAdmin(stopped, httptest.NewRequest(http.MethodDelete, "https://example.test/api/share?id="+id, nil))
	if stopped.Code != http.StatusOK {
		t.Fatalf("stop returned %d: %s", stopped.Code, stopped.Body.String())
	}
	createShareFixture(t, s, path, false, 1)
}

func TestShareInviteURLsLimitedToRequestedHostView(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, _ := createShareFixture(t, s, path, false, 1)
	for _, query := range []string{"", "?invites=1"} {
		w := httptest.NewRecorder()
		s.shareAdmin(w, httptest.NewRequest(http.MethodGet, "https://example.test/api/share"+query, nil))
		if w.Code != http.StatusOK || strings.Contains(w.Body.String(), secret) != (query != "") {
			t.Fatalf("host invite query %q returned unexpected access (%d)", query, w.Code)
		}
	}
	cookie := joinShareFixture(t, s, id, secret)
	guest := shareRequest(s, http.MethodGet, "/share/"+id+"/state?invites=1", nil, cookie)
	if guest.Code != http.StatusOK || strings.Contains(guest.Body.String(), secret) {
		t.Fatalf("guest state returned unexpected invite access (%d)", guest.Code)
	}
}
func shareRequest(s *server, method, url string, body any, cookie *http.Cookie) *httptest.ResponseRecorder {
	data, _ := json.Marshal(body)
	r := httptest.NewRequest(method, "https://example.test"+url, bytes.NewReader(data))
	if cookie != nil {
		r.AddCookie(cookie)
	}
	w := httptest.NewRecorder()
	s.shareGuest(w, r)
	return w
}
func joinShareFixture(t *testing.T, s *server, id, secret string) *http.Cookie {
	t.Helper()
	w := shareRequest(s, "POST", "/share/"+id+"/join", map[string]any{"secret": secret}, nil)
	if w.Code != 200 {
		t.Fatalf("join %d %s", w.Code, w.Body.String())
	}
	cookies := w.Result().Cookies()
	if len(cookies) != 1 || !cookies[0].Secure || !cookies[0].HttpOnly {
		t.Fatalf("invalid cookie %v", cookies)
	}
	return cookies[0]
}
func TestShareBrowserDetails(t *testing.T) {
	for _, fixture := range []struct{ agent, want, version string }{
		{"Mozilla/5.0 Chrome/131.0 Safari/537.36 Edg/131.0", "Edge", "131.0"},
		{"Mozilla/5.0 Chrome/131.0 Safari/537.36 OPR/116.0", "Opera", "116.0"},
		{"Mozilla/5.0 AppleWebKit/605.1.15 FxiOS/131.0 Mobile/15E148 Safari/605.1.15", "Firefox", "131.0"},
		{"Mozilla/5.0 Version/18.1 Safari/605.1.15", "Safari", "18.1"},
		{"Mozilla/5.0 HeadlessChrome/131.0 Safari/537.36", "Chrome", "131.0"},
		{strings.Repeat("\u023a", 16) + " Chrome/131.0", "Chrome", "131.0"},
		{"", "Unknown", ""},
	} {
		got, version := shareBrowserDetails(fixture.agent)
		if got != fixture.want || version != fixture.version {
			t.Errorf("browser for %q = %q %q, want %q %q", fixture.agent, got, version, fixture.want, fixture.version)
		}
	}
}
func TestShareParticipantHuesAcrossInvites(t *testing.T) {
	s, path := newShareFixture(t)
	first, firstSecret, _ := createNamedShareFixture(t, s, path, "First", false, 3)
	second, secondSecret, _ := createNamedShareFixture(t, s, path, "Second", false, 3)
	s.shares.mu.Lock()
	hostHue := s.shareHostHueLocked("owner")
	s.shares.mu.Unlock()
	if hostHue != 215 {
		t.Fatalf("owner hue = %d, want blue", hostHue)
	}
	for i, want := range []int{35, 155, 275, 95, 335, 35} {
		id, secret := first, firstSecret
		if i%2 != 0 {
			id, secret = second, secondSecret
		}
		cookie := joinShareFixture(t, s, id, secret)
		for range 2 {
			w := shareRequest(s, http.MethodGet, "/share/"+id+"/state", nil, cookie)
			var state struct {
				Hue int `json:"hue"`
			}
			if err := json.Unmarshal(w.Body.Bytes(), &state); w.Code != http.StatusOK || err != nil || state.Hue != want {
				t.Fatalf("guest %d state = %d, hue %d, error %v; want hue %d", i+1, w.Code, state.Hue, err, want)
			}
		}
	}
}
func TestShareBrowserSlotsScopeReadonlyAndRevocation(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, g := createShareFixture(t, s, path, false, 2)
	var wg sync.WaitGroup
	codes := make(chan int, 3)
	for range 3 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			codes <- shareRequest(s, "POST", "/share/"+id+"/join", map[string]any{"secret": secret}, nil).Code
		}()
	}
	wg.Wait()
	close(codes)
	accepted, rejected := 0, 0
	for code := range codes {
		switch code {
		case 200:
			accepted++
		case 409:
			rejected++
		default:
			t.Fatalf("join status %d", code)
		}
	}
	if accepted != 2 || rejected != 1 || g.Slots != 2 {
		t.Fatalf("slots %d %d %d", accepted, rejected, g.Slots)
	}
	s2, path2 := newShareFixture(t)
	id, secret, g = createShareFixture(t, s2, path2, false, 1)
	g.Appearance = shareAppearance{Theme: "light", Accent: "purple"}
	stateURL := "/share/" + id + "/state"
	if w := shareRequest(s2, http.MethodGet, stateURL, nil, nil); w.Code != 401 || !strings.Contains(w.Body.String(), "join_required") || strings.Contains(w.Body.String(), "ShareAdmin") {
		t.Fatalf("unclaimed share returned %d: %s", w.Code, w.Body.String())
	}
	cookie := joinShareFixture(t, s2, id, secret)
	joinURL := "/share/" + id + "/join"
	for _, w := range []*httptest.ResponseRecorder{
		shareRequest(s2, http.MethodGet, stateURL, nil, nil),
		shareRequest(s2, http.MethodPost, joinURL, map[string]any{"secret": secret}, nil),
	} {
		var payload map[string]json.RawMessage
		if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		var appearance shareAppearance
		err := json.Unmarshal(payload["appearance"], &appearance)
		if w.Code != 409 || len(payload) != 2 || string(payload["error"]) != `"guest_limit_reached"` || err != nil || appearance != g.Appearance {
			t.Fatalf("full share returned %d: %s", w.Code, w.Body.String())
		}
	}
	if w := shareRequest(s2, http.MethodPost, joinURL, map[string]any{"secret": "wrong"}, nil); w.Code != 403 || strings.Contains(w.Body.String(), "appearance") {
		t.Fatalf("invalid invitation returned %d: %s", w.Code, w.Body.String())
	}
	if w := shareRequest(s2, http.MethodGet, stateURL, nil, cookie); w.Code != 200 || !strings.Contains(w.Body.String(), `"owner":"ShareAdmin"`) {
		t.Fatalf("claimed browser lost access to full share: %d %s", w.Code, w.Body.String())
	}
	if w := shareRequest(s2, "POST", "/share/"+id+"/join", map[string]any{}, cookie); w.Code != 200 || g.Slots != 1 || !strings.Contains(w.Body.String(), `"owner":"ShareAdmin"`) {
		t.Fatalf("cookie did not reuse slot %d %d", w.Code, g.Slots)
	}
	resource := g.Resources[0].ID
	if w := shareRequest(s2, "GET", "/share/"+id+"/"+resource, nil, cookie); w.Code != 200 || !strings.Contains(w.Body.String(), "hello") {
		t.Fatalf("read %d %s", w.Code, w.Body.String())
	}
	if w := shareRequest(s2, "POST", "/share/"+id+"/"+resource, map[string]any{"text": "overwrite"}, cookie); w.Code != 403 {
		t.Fatalf("RO bypass %d", w.Code)
	}
	if w := shareRequest(s2, "GET", "/share/"+id+"/ungranted", nil, cookie); w.Code != 404 {
		t.Fatalf("neighbor %d", w.Code)
	}
	r := httptest.NewRequest("POST", "https://example.test/share/"+id+"/chat", strings.NewReader(`{"text":"cross origin"}`))
	r.AddCookie(cookie)
	r.Header.Set("Origin", "https://attacker.test")
	w := httptest.NewRecorder()
	s2.shareGuest(w, r)
	if w.Code != 403 {
		t.Fatalf("cross origin %d", w.Code)
	}
	if w := shareRequest(s2, "POST", "/share/"+id+"/chat", map[string]any{"text": "message"}, cookie); w.Code != 200 || !strings.Contains(w.Body.String(), "Guest4821") {
		t.Fatalf("chat %d %s", w.Code, w.Body.String())
	}
	g.stopped = true
	if w := shareRequest(s2, "GET", "/share/"+id+"/"+resource, nil, cookie); w.Code != 410 {
		t.Fatalf("revocation %d", w.Code)
	}
	g.stopped = false
	g.Expires = time.Now().Add(-time.Second)
	if w := shareRequest(s2, "GET", "/share/"+id+"/chat", nil, cookie); w.Code != 410 {
		t.Fatalf("expiry %d", w.Code)
	}
}
func TestShareChangeSetValidationAndUTF16(t *testing.T) {
	for _, tc := range []struct {
		text, raw, want string
		valid           bool
	}{
		{"a", `[1]`, "a", true}, {"a", `[[0,"z"],1]`, "za", true}, {"a", `[[1,"b","c"]]`, "b\nc", true}, {"a😀b", `[1,[2,"x"],1]`, "axb", true},
		{"a", `[null,1]`, "", false}, {"a", `[[1,null]]`, "", false}, {"a", `[[1,"\ud800"]]`, "", false}, {"a", `[[1,"\udc00"]]`, "", false}, {"a", `[[1,"\ud83d\ude00"]]`, "😀", true},
		{"a", `[[1,"x\ny"]]`, "", false}, {"a", `[-1,2]`, "", false}, {"a", `[2]`, "", false}, {"a", `[]`, "", false}, {"😀", `[1,[1]]`, "", false},
	} {
		t.Run(tc.raw, func(t *testing.T) {
			got, _, err := applyShareChanges(tc.text, json.RawMessage(tc.raw))
			if (err == nil) != tc.valid || tc.valid && got != tc.want {
				t.Fatalf("got %q %v want %q valid %v", got, err, tc.want, tc.valid)
			}
		})
	}
}
func TestShareCollaborationRevisionDedupConflictAndRecovery(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, g := createShareFixture(t, s, path, true, 2)
	cookie := joinShareFixture(t, s, id, secret)
	resource := g.Resources[0]
	url := "/share/" + id + "/" + resource.ID + "?client=client-1234567890"
	update := map[string]any{"version": 0, "updates": []map[string]any{{"id": "operation-1234567890", "changes": json.RawMessage(`[[0,"A"],6]`)}}, "anchor": 1, "head": 1}
	for range 2 {
		w := shareRequest(s, "POST", url, update, cookie)
		if w.Code != 200 {
			t.Fatalf("update %d %s", w.Code, w.Body.String())
		}
	}
	if resource.document.Text != "Ahello\n" || len(resource.document.operations) != 1 {
		t.Fatalf("duplicate edits: %+v", resource.document)
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "Ahello\n" {
		t.Fatalf("saved %q %v", data, err)
	}
	if err := os.WriteFile(path, []byte("external\n"), 0600); err != nil {
		t.Fatal(err)
	}
	update = map[string]any{"version": 1, "updates": []map[string]any{{"id": "operation-2234567890", "changes": json.RawMessage(`[7,[0,"B"]]`)}}, "anchor": 8, "head": 8}
	w := shareRequest(s, "POST", url, update, cookie)
	if w.Code != 200 || resource.document.Paused != "external_change" {
		t.Fatalf("external edit %d %s %+v", w.Code, w.Body.String(), resource.document)
	}
	data, err = os.ReadFile(path)
	if err != nil || string(data) != "external\n" {
		t.Fatalf("lost external %q %v", data, err)
	}
	journal, err := os.ReadFile(s.shareJournalPath(path))
	if err != nil || !bytes.Contains(journal, []byte(`Ahello\nB`)) {
		t.Fatalf("recovery %s %v", journal, err)
	}
	before := resource.document.Text
	duplicate := map[string]any{"version": 2, "updates": []map[string]any{{"id": "duplicate-1234567890", "changes": json.RawMessage(`[8,[0,"C"]]`)}, {"id": "duplicate-1234567890", "changes": json.RawMessage(`[9,[0,"D"]]`)}}}
	if w := shareRequest(s, "POST", url, duplicate, cookie); w.Code != 400 || resource.document.Text != before {
		t.Fatalf("duplicate batch %d %s", w.Code, w.Body.String())
	}
}

func TestSharePresenceChoosesOneCursorPerParticipant(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, grant := createShareFixture(t, s, path, true, 2)
	guest := joinShareFixture(t, s, id, secret)
	observer := joinShareFixture(t, s, id, secret)
	resource := grant.Resources[0]
	endpoint := "/share/" + id + "/" + resource.ID + "?client="
	post := func(client string, cookie *http.Cookie, offset int, focused bool) []sharePresence {
		t.Helper()
		w := shareRequest(s, http.MethodPost, endpoint+client, map[string]any{
			"version": 0, "anchor": offset, "head": offset, "focused": focused,
		}, cookie)
		var result struct{ Presence []sharePresence }
		if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &result) != nil {
			t.Fatalf("presence %d %s", w.Code, w.Body.String())
		}
		return result.Presence
	}
	const first = "focused-editor-0001"
	const second = "background-editor-0002"
	const viewer = "observing-editor-0003"
	post(first, guest, 1, true)
	if own := post(second, guest, 3, false); len(own) != 0 {
		t.Fatalf("browser sees its other editor as a peer: %+v", own)
	}
	if peers := post(viewer, observer, 0, true); len(peers) != 1 || peers[0].Head != 1 {
		t.Fatalf("observer sees wrong focused cursor: %+v", peers)
	}
	post(first, guest, 1, false)
	post(second, guest, 3, true)
	post(first, guest, 2, false)
	if peers := post(viewer, observer, 0, true); len(peers) != 1 || peers[0].Head != 3 {
		t.Fatalf("background edit displaced the focused cursor: %+v", peers)
	}
}
func TestShareTerminalGuestNeverResizesAndBlocksBroadcast(t *testing.T) {
	if _, err := exec.LookPath("tmux"); err != nil {
		t.Skip("tmux unavailable")
	}
	t.Setenv("YOMUX_TMUX_SOCKET", filepath.Join(testsupport.ShortTempDir(t), "tmux.sock"))
	s := &server{}
	run := func(args ...string) string {
		t.Helper()
		out, err := s.tmuxCommandContext(context.Background(), args...).CombinedOutput()
		if err != nil {
			t.Fatalf("tmux %v: %v %s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	run("-f", os.DevNull, "new-session", "-d", "-s", "fixture", "-x", "103", "-y", "31")
	t.Cleanup(func() {
		out, err := s.tmuxCommandContext(context.Background(), "kill-server").CombinedOutput()
		if err != nil {
			t.Logf("private tmux cleanup %v %s", err, out)
		}
	})
	pane := run("display-message", "-p", "-t", "fixture:0.0", "#{pane_id}")
	identity, label, err := s.sharePaneIdentity(context.Background(), pane)
	if err != nil {
		t.Fatal(err)
	}
	resource := &shareResource{Pane: pane, Identity: identity, Label: label, Writable: true}
	run("new-session", "-d", "-t", "fixture", "-s", "host-view")
	run("resize-window", "-t", "host-view:0", "-x", "65", "-y", "20")
	before := run("display-message", "-p", "-t", pane, "#{pane_width}x#{pane_height}:#{window_id}:#{pane_active}")
	if _, err := s.sharePaneCommand(context.Background(), resource, "printf guest-input", ""); err != nil {
		t.Fatal(err)
	}
	snapshot, err := s.sharePaneCommand(context.Background(), resource, "", "")
	if err != nil || snapshot.Cursor == nil || len(strings.Split(strings.TrimSuffix(snapshot.Text, "\n"), "\n")) != 20 {
		t.Fatalf("capture did not preserve cursor and resized screen: %+v, %v", snapshot.Cursor, err)
	}
	after := run("display-message", "-p", "-t", pane, "#{pane_width}x#{pane_height}:#{window_id}:#{pane_active}")
	if before != after {
		t.Fatalf("guest altered host geometry/focus %q -> %q", before, after)
	}
	run("set-window-option", "-t", "fixture:0", "synchronize-panes", "on")
	if _, err := s.sharePaneCommand(context.Background(), resource, "must-not-send", ""); err == nil {
		t.Fatal("broadcast input accepted")
	}
	resource.Identity = "0:0:" + pane + ":0:@0"
	if _, err := s.sharePaneCommand(context.Background(), resource, "", ""); err == nil {
		t.Fatal("replacement pane accepted")
	}
}

func TestShareReadOnlyFollowsAuthorizedHostSave(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, g := createShareFixture(t, s, path, false, 1)
	cookie := joinShareFixture(t, s, id, secret)
	if _, failure := s.finder.Write(context.Background(), path, "host saved\n", nil); failure != nil {
		t.Fatal(failure)
	}
	w := shareRequest(s, "GET", "/share/"+id+"/"+g.Resources[0].ID, nil, cookie)
	if w.Code != 200 || !strings.Contains(w.Body.String(), "host saved") {
		t.Fatalf("host save did not reach RO resource: %d %s", w.Code, w.Body.String())
	}
}
func TestShareRecoveryAfterRevocationReleasesSaveOwnership(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, g := createShareFixture(t, s, path, true, 1)
	cookie := joinShareFixture(t, s, id, secret)
	if err := os.WriteFile(path, []byte("external\n"), 0600); err != nil {
		t.Fatal(err)
	}
	body := map[string]any{"version": 0, "updates": []map[string]any{{"id": "retained-operation-1", "changes": json.RawMessage(`[[0,"draft "],6]`)}}}
	w := shareRequest(s, "POST", "/share/"+id+"/"+g.Resources[0].ID+"?client=recovery-client-1", body, cookie)
	if w.Code != 200 {
		t.Fatalf("accept draft %d %s", w.Code, w.Body.String())
	}
	stop := httptest.NewRecorder()
	s.shareAdmin(stop, httptest.NewRequest("DELETE", "https://example.test/api/share?id="+id, nil))
	if stop.Code != 200 {
		t.Fatal(stop.Body.String())
	}
	encoded, err := json.Marshal(map[string]any{"path": path, "action": "restore"})
	if err != nil {
		t.Fatal(err)
	}
	recovered := httptest.NewRecorder()
	s.shareRecovery(recovered, httptest.NewRequest("POST", "https://example.test/api/share/recovery", bytes.NewReader(encoded)))
	if recovered.Code != 200 {
		t.Fatalf("recovery %d %s", recovered.Code, recovered.Body.String())
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "draft hello\n" {
		t.Fatalf("recovered %q %v", data, err)
	}
	if _, failure := s.finder.Write(context.Background(), path, "ordinary save\n", nil); failure != nil {
		t.Fatalf("ownership not released: %v", failure)
	}
}
func TestShareDocumentGenerationChangesOnImmediateRecreation(t *testing.T) {
	s, path := newShareFixture(t)
	id, _, g := createShareFixture(t, s, path, true, 1)
	generation := g.Resources[0].document.ID
	if generation == "" {
		t.Fatal("document has no generation")
	}
	stop := httptest.NewRecorder()
	s.shareAdmin(stop, httptest.NewRequest("DELETE", "https://example.test/api/share?id="+id, nil))
	if stop.Code != 200 {
		t.Fatal(stop.Body.String())
	}
	_, _, next := createShareFixture(t, s, path, true, 1)
	if next.Resources[0].document.ID == generation {
		t.Fatal("reused document generation")
	}
	record := httptest.NewRecorder()
	s.shareHostDocument(record, httptest.NewRequest("GET", "https://example.test/api/share/document?path="+path+"&client=host-client-123456&session="+generation, nil))
	if record.Code != 409 || !strings.Contains(record.Body.String(), "document_session_changed") {
		t.Fatalf("old session admitted: %d %s", record.Code, record.Body.String())
	}
}
func TestShareTexPublicationUsesActualCompiledRevision(t *testing.T) {
	s, path := newShareFixture(t)
	tex := strings.TrimSuffix(path, ".md") + ".tex"
	if err := os.Rename(path, tex); err != nil {
		t.Fatal(err)
	}
	id, secret, g := createShareFixture(t, s, tex, true, 1)
	cookie := joinShareFixture(t, s, id, secret)
	resource := g.Resources[0]
	if err := s.sharePublishPDF(tex, "hello\n", []byte("%PDF-1.4\nfixture")); err != nil {
		t.Fatal(err)
	}
	firstSerial := resource.pdfSerial
	url := "/share/" + id + "/" + resource.ID + "?client=tex-client-123456"
	update := map[string]any{"version": 0, "updates": []map[string]any{{"id": "tex-operation-123456", "changes": json.RawMessage(`[[0,"changed "],6]`)}}}
	w := shareRequest(s, "POST", url, update, cookie)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"pdf_stale":true`) || !strings.Contains(w.Body.String(), `"pdf_available":true`) {
		t.Fatalf("stale publication %d %s", w.Code, w.Body.String())
	}
	if len(s.texProgress) != 0 {
		t.Fatal("guest source edit invoked compiler")
	}
	if err := s.sharePublishPDF(tex, "hello\n", []byte("%PDF-1.4\nolder-source")); err != nil {
		t.Fatal(err)
	}
	if resource.pdfRevision != -1 || resource.pdfSerial <= firstSerial {
		t.Fatal("late compilation mislabeled as current")
	}
	if err := s.sharePublishPDF(tex, "changed hello\n", []byte("%PDF-1.4\ncurrent")); err != nil {
		t.Fatal(err)
	}
	w = shareRequest(s, "GET", url+"&initial=1", nil, cookie)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"pdf_stale":false`) {
		t.Fatalf("current publication %d %s", w.Code, w.Body.String())
	}
	w = shareRequest(s, "GET", "/share/"+id+"/"+resource.ID+"?pdf=1", nil, cookie)
	if w.Code != 200 || w.Body.String() != "%PDF-1.4\ncurrent" {
		t.Fatalf("PDF %d %s", w.Code, w.Body.String())
	}
	stop := httptest.NewRecorder()
	s.shareAdmin(stop, httptest.NewRequest("DELETE", "https://example.test/api/share?id="+id, nil))
	if stop.Code != 200 {
		t.Fatalf("stop share %d %s", stop.Code, stop.Body.String())
	}
	id, secret, g = createShareFixture(t, s, tex, false, 1)
	cookie = joinShareFixture(t, s, id, secret)
	resource = g.Resources[0]
	if err := s.sharePublishPDF(tex, "hello\n", []byte("%PDF-1.4\nolder-source")); err != nil {
		t.Fatal(err)
	}
	change, err := json.Marshal(map[string]any{
		"resource": resource.ID, "writable": true, "permission_revision": resource.PermissionRevision,
		"saved_content": "changed hello\n",
	})
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	s.shareAdmin(response, httptest.NewRequest("PATCH", "https://example.test/api/share?id="+id, bytes.NewReader(change)))
	if response.Code != 200 {
		t.Fatalf("permission change %d %s", response.Code, response.Body.String())
	}
	url = "/share/" + id + "/" + resource.ID + "?client=tex-client-123456"
	w = shareRequest(s, "GET", url+"&initial=1", nil, cookie)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"pdf_stale":true`) {
		t.Fatalf("permission upgrade marked an old PDF current: %d %s", w.Code, w.Body.String())
	}
}

type shareHeldBody struct {
	entered, release chan struct{}
	once             sync.Once
	data             *strings.Reader
}

func (body *shareHeldBody) Read(data []byte) (int, error) {
	body.once.Do(func() { close(body.entered); <-body.release })
	return body.data.Read(data)
}
func (body *shareHeldBody) Close() error { return nil }

type shareHeldWriter struct {
	entered, release chan struct{}
	once             sync.Once
	header           http.Header
}

func (writer *shareHeldWriter) Header() http.Header    { return writer.header }
func (writer *shareHeldWriter) WriteHeader(status int) {}
func (writer *shareHeldWriter) Write(data []byte) (int, error) {
	writer.once.Do(func() { close(writer.entered); <-writer.release })
	return len(data), nil
}
func TestShareNetworkBackpressureDoesNotBlockRevocation(t *testing.T) {
	for _, phase := range []string{"request_body", "patch_body", "response_body"} {
		t.Run(phase, func(t *testing.T) {
			s, path := newShareFixture(t)
			id, secret, _ := createShareFixture(t, s, path, false, 1)
			cookie := joinShareFixture(t, s, id, secret)
			otherID, otherSecret, _ := createNamedShareFixture(t, s, path, "Other guest", false, 1)
			otherCookie := joinShareFixture(t, s, otherID, otherSecret)
			entered, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
			defer func() {
				close(release)
				select {
				case <-finished:
				case <-time.After(2 * time.Second):
					t.Error("held request did not retire")
				}
			}()
			if phase == "request_body" {
				body := &shareHeldBody{entered: entered, release: release, data: strings.NewReader(`{"secret":"wrong"}`)}
				request := httptest.NewRequest("POST", "https://example.test/share/"+id+"/join", nil)
				request.Body = body
				go func() { defer close(finished); s.shareGuest(httptest.NewRecorder(), request) }()
			} else if phase == "patch_body" {
				body := &shareHeldBody{entered: entered, release: release, data: strings.NewReader(`{"writable":true}`)}
				request := httptest.NewRequest("PATCH", "https://example.test/api/share?id="+id, nil)
				request.Body = body
				go func() { defer close(finished); s.shareAdmin(httptest.NewRecorder(), request) }()
			} else {
				writer := &shareHeldWriter{entered: entered, release: release, header: make(http.Header)}
				request := httptest.NewRequest("GET", "https://example.test/share/"+id+"/chat", nil)
				request.AddCookie(cookie)
				go func() { defer close(finished); s.shareGuest(writer, request) }()
			}
			select {
			case <-entered:
			case <-time.After(2 * time.Second):
				t.Fatal("network barrier not entered")
			}
			stopped := make(chan int, 1)
			go func() {
				response := httptest.NewRecorder()
				s.shareAdmin(response, httptest.NewRequest("DELETE", "https://example.test/api/share?id="+id, nil))
				stopped <- response.Code
			}()
			select {
			case status := <-stopped:
				if status != 200 {
					t.Fatalf("stop returned %d", status)
				}
			case <-time.After(2 * time.Second):
				t.Fatal("network IO retained registry ownership")
			}
			if response := shareRequest(s, "GET", "/share/"+otherID+"/state", nil, otherCookie); response.Code != 200 {
				t.Fatalf("unrelated grant blocked: %d", response.Code)
			}
		})
	}
}

func TestShareRevocationInterruptsBlockedResponse(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, _ := createShareFixture(t, s, path, false, 1)
	cookie := joinShareFixture(t, s, id, secret)
	writer := &interruptibleSSEWriter{phase: "write", entered: make(chan struct{}), interrupted: make(chan struct{})}
	request := httptest.NewRequest("GET", "https://example.test/share/"+id+"/chat", nil)
	request.AddCookie(cookie)
	finished := make(chan struct{})
	go func() { defer close(finished); s.shareGuest(writer, request) }()
	select {
	case <-writer.entered:
	case <-time.After(2 * time.Second):
		t.Fatal("response did not enter transport")
	}
	stopped := httptest.NewRecorder()
	s.shareAdmin(stopped, httptest.NewRequest("DELETE", "https://example.test/api/share?id="+id, nil))
	if stopped.Code != 200 {
		t.Fatal(stopped.Body.String())
	}
	select {
	case <-finished:
	case <-time.After(2 * time.Second):
		t.Fatal("revocation did not cancel blocked transport")
	}
}

func TestShareMembershipAtomicRevisionAndRetainedIdentity(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, grant := createShareFixture(t, s, path, false, 1)
	cookie := joinShareFixture(t, s, id, secret)
	original := grant.Resources[0]
	second := filepath.Join(filepath.Dir(path), "second.md")
	if err := os.WriteFile(second, []byte("second\n"), 0600); err != nil {
		t.Fatal(err)
	}
	patch := func(body any) *httptest.ResponseRecorder {
		data, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		w := httptest.NewRecorder()
		s.shareAdmin(w, httptest.NewRequest("PATCH", "https://example.test/api/share?id="+id, bytes.NewReader(data)))
		return w
	}
	payload := map[string]any{"revision": 1, "resources": []map[string]any{{"id": original.ID, "writable": true, "saved_content": "hello\n"}, {"kind": "file", "path": second}}}
	w := patch(payload)
	if w.Code != 200 {
		t.Fatalf("membership update %d %s", w.Code, w.Body.String())
	}
	if len(grant.Resources) != 2 || grant.Resources[0].ID != original.ID || !grant.Resources[0].Writable {
		t.Fatalf("retained resource %+v", grant.Resources)
	}
	if original.permission.ctx.Err() == nil {
		t.Fatal("retained old permission remained admitted")
	}
	if w := patch(payload); w.Code != 409 {
		t.Fatalf("stale update %d %s", w.Code, w.Body.String())
	}
	before := grant.Resources[0]
	invalid := map[string]any{"revision": 2, "resources": []map[string]any{{"id": before.ID, "writable": false}, {"kind": "file", "path": second + "missing"}}}
	if w := patch(invalid); w.Code != 400 {
		t.Fatalf("invalid update %d %s", w.Code, w.Body.String())
	}
	if grant.Resources[0] != before || !before.Writable || before.permission.ctx.Err() != nil {
		t.Fatal("failed update mutated live permission")
	}
	added := grant.Resources[1]
	if w := patch(map[string]any{"revision": 2, "resources": []map[string]any{{"id": added.ID}}}); w.Code != 200 {
		t.Fatalf("remove %d %s", w.Code, w.Body.String())
	}
	if before.permission.ctx.Err() == nil {
		t.Fatal("removed permission remained admitted")
	}
	if w := shareRequest(s, "GET", "/share/"+id+"/"+original.ID, nil, cookie); w.Code != 404 {
		t.Fatalf("removed read %d", w.Code)
	}
	if w := shareRequest(s, "GET", "/share/"+id+"/"+added.ID, nil, cookie); w.Code != 200 {
		t.Fatalf("retained read %d", w.Code)
	}
	if w := shareRequest(s, "POST", "/share/"+id+"/join", map[string]any{}, cookie); w.Code != 200 || grant.Slots != 1 {
		t.Fatal("membership update spent browser slot")
	}
}

func TestShareExpiryCanChangeWithinDayLimit(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, grant := createShareFixture(t, s, path, false, 1)
	tooLong, err := json.Marshal(map[string]any{"guest": "TooLong", "minutes": 1446, "limit": 1,
		"resources": []map[string]any{{"kind": "file", "path": path, "writable": false}}})
	if err != nil {
		t.Fatal(err)
	}
	invalidCreate := httptest.NewRecorder()
	s.shareAdmin(invalidCreate, httptest.NewRequest("POST", "https://example.test/api/share", bytes.NewReader(tooLong)))
	if invalidCreate.Code != http.StatusBadRequest {
		t.Fatalf("creation above expiry limit returned %d: %s", invalidCreate.Code, invalidCreate.Body.String())
	}
	resource := grant.Resources[0]
	priorPermission := resource.permission
	patch := func(revision, minutes int) *httptest.ResponseRecorder {
		data, err := json.Marshal(map[string]int{"revision": revision, "minutes": minutes})
		if err != nil {
			t.Fatal(err)
		}
		response := httptest.NewRecorder()
		s.shareAdmin(response, httptest.NewRequest("PATCH", "https://example.test/api/share?id="+id, bytes.NewReader(data)))
		return response
	}
	if response := patch(1, 1446); response.Code != http.StatusBadRequest {
		t.Fatalf("expiry above limit returned %d: %s", response.Code, response.Body.String())
	}
	response := patch(1, 7)
	if response.Code != http.StatusOK {
		t.Fatalf("expiry update returned %d: %s", response.Code, response.Body.String())
	}
	if grant.Revision != 2 || grant.Expires.Before(time.Now().Add(6*time.Minute)) || grant.Expires.After(time.Now().Add(8*time.Minute)) {
		t.Fatalf("expiry update did not take effect: revision=%d expires=%s", grant.Revision, grant.Expires)
	}
	if priorPermission.ctx.Err() == nil || resource.PermissionRevision != 2 {
		t.Fatal("expiry update did not advance resource permission epochs")
	}
	cookie := joinShareFixture(t, s, id, secret)
	if stale := shareRequest(s, "GET", "/share/"+id+"/"+resource.ID+"?permission=1", nil, cookie); stale.Code != http.StatusConflict {
		t.Fatalf("old permission epoch returned %d: %s", stale.Code, stale.Body.String())
	}
	if response := patch(1, 5); response.Code != http.StatusConflict {
		t.Fatalf("stale expiry update returned %d: %s", response.Code, response.Body.String())
	}
	extendedAt := time.Now()
	if response := patch(2, 1445); response.Code != http.StatusOK || grant.Expires.Sub(extendedAt) < 86700*time.Second ||
		grant.Expires.Sub(extendedAt) > 86701*time.Second {
		t.Fatalf("expiry extension returned %d: %s", response.Code, response.Body.String())
	}
	state := shareRequest(s, http.MethodGet, "/share/"+id+"/state", nil, cookie)
	refreshed := state.Result().Cookies()
	if state.Code != http.StatusOK || len(refreshed) != 1 || refreshed[0].Value != cookie.Value ||
		!refreshed[0].Expires.Equal(grant.Expires.Truncate(time.Second)) || !refreshed[0].Expires.After(cookie.Expires) || grant.Slots != 1 {
		t.Fatalf("expiry extension did not renew the existing guest slot (%d)", state.Code)
	}
}

func TestShareChangesGuestLimitWithoutReplacingClaims(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, grant := createShareFixture(t, s, path, false, 1)
	first := joinShareFixture(t, s, id, secret)
	patch := func(revision, limit int) *httptest.ResponseRecorder {
		body := fmt.Sprintf(`{"revision":%d,"limit":%d}`, revision, limit)
		w := httptest.NewRecorder()
		s.shareAdmin(w, httptest.NewRequest(http.MethodPatch, "https://example.test/api/share?id="+id, strings.NewReader(body)))
		return w
	}
	permission := grant.Resources[0].permission
	if w := patch(1, 2); w.Code != http.StatusOK || grant.Revision != 2 || grant.Limit != 2 || grant.Resources[0].permission != permission {
		t.Fatalf("limit increase failed (%d)", w.Code)
	}
	second := joinShareFixture(t, s, id, secret)
	if first.Value == second.Value || grant.Slots != 2 {
		t.Fatal("raised limit did not admit a distinct second browser")
	}
	if w := patch(2, 1); w.Code != http.StatusConflict || grant.Revision != 2 || grant.Limit != 2 {
		t.Fatalf("claimed browser limit changed (%d)", w.Code)
	}
}

func TestShareMembershipRemovalInterruptsResourceResponse(t *testing.T) {
	s, path := newShareFixture(t)
	id, secret, grant := createShareFixture(t, s, path, false, 1)
	cookie := joinShareFixture(t, s, id, secret)
	writer := &interruptibleSSEWriter{phase: "write", entered: make(chan struct{}), interrupted: make(chan struct{})}
	request := httptest.NewRequest("GET", "https://example.test/share/"+id+"/"+grant.Resources[0].ID, nil)
	request.AddCookie(cookie)
	finished := make(chan struct{})
	go func() { defer close(finished); s.shareGuest(writer, request) }()
	select {
	case <-writer.entered:
	case <-time.After(2 * time.Second):
		t.Fatal("resource response did not enter transport")
	}
	response := httptest.NewRecorder()
	s.shareAdmin(response, httptest.NewRequest("PATCH", "https://example.test/api/share?id="+id, strings.NewReader(`{"revision":1,"resources":[]}`)))
	if response.Code != 200 {
		t.Fatalf("remove all %d %s", response.Code, response.Body.String())
	}
	select {
	case <-finished:
	case <-time.After(2 * time.Second):
		t.Fatal("membership removal did not cancel resource transport")
	}
	if response := shareRequest(s, "GET", "/share/"+id+"/chat", nil, cookie); response.Code != 200 {
		t.Fatalf("resource removal ended chat: %d", response.Code)
	}
}

func TestShareMembershipValidationAndLegacyRevision(t *testing.T) {
	s, path := newShareFixture(t)
	id, _, grant := createShareFixture(t, s, path, false, 1)
	original := grant.Resources[0]
	patch := func(body any) *httptest.ResponseRecorder {
		data, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		response := httptest.NewRecorder()
		s.shareAdmin(response, httptest.NewRequest("PATCH", "https://example.test/api/share?id="+id, bytes.NewReader(data)))
		return response
	}
	for _, selection := range [][]map[string]any{
		{{"id": original.ID}, {"id": original.ID}},
		{{"id": original.ID, "path": path + "other"}},
		{{"id": original.ID, "kind": "terminal"}},
		{{"id": "not-in-this-grant"}},
	} {
		if response := patch(map[string]any{"revision": 1, "resources": selection}); response.Code == 200 {
			t.Fatal("invalid membership admitted")
		}
		if grant.Revision != 1 || grant.Resources[0] != original || original.permission.ctx.Err() != nil {
			t.Fatal("invalid membership changed grant")
		}
	}
	response := patch(map[string]any{"resource": original.ID, "permission_revision": 1, "writable": true, "saved_content": "hello\n"})
	if response.Code != 200 || grant.Revision != 2 {
		t.Fatalf("legacy permission revision %d %s", response.Code, response.Body.String())
	}
	var resource shareResource
	if err := json.Unmarshal(response.Body.Bytes(), &resource); err != nil || resource.ID != original.ID || !resource.Writable {
		t.Fatalf("legacy response shape: %v %s", err, response.Body.String())
	}
	if response := patch(map[string]any{"revision": 1, "resources": []map[string]any{{"id": original.ID}}}); response.Code != 409 {
		t.Fatal("legacy change did not invalidate stale membership")
	}
}

func TestShareGuestWorkspaceUsesHomeAndScopedBootstrap(t *testing.T) {
	s := nativePageFixtureServer(t)
	if _, err := s.nativeSettingsPayload(context.Background(), map[string]any{"appearance": map[string]any{"theme": "light", "active_color": "blue"}}, true); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest("GET", "https://example.test/share/"+strings.Repeat("a", 64), nil)
	response := httptest.NewRecorder()
	s.shareGuest(response, request)
	if response.Code != 200 {
		t.Fatalf("guest home %d %s", response.Code, response.Body.String())
	}
	body := response.Body.Bytes()
	payload := nativePageBootstrap(t, body)
	if payload["guestWorkspace"] != true || payload["sessionMutationsAllowed"] != false || payload["homePath"] != "/shared" || payload["repoRoot"] != "" {
		t.Fatal("guest scope bootstrap changed")
	}
	for _, key := range []string{"sessions", "recentSessions"} {
		if values, ok := payload[key].([]any); !ok || len(values) != 0 {
			t.Fatalf("guest roster %s = %#v", key, payload[key])
		}
	}
	for _, key := range []string{"authUsername", "hostname", "availableAgents", "agentCommands", "statsWriterFence"} {
		if _, found := payload[key]; found {
			t.Fatalf("host field leaked: %s", key)
		}
	}
	settings := payload["settingsPayload"].(map[string]any)["settings"].(map[string]any)
	if _, found := settings["appearance"]; found {
		t.Fatal("host saved appearance leaked into invitation page")
	}
	text := string(body)
	for _, fragment := range []string{`class="topbar"`, `id="grid"`, `vendor/dockview-core.noStyle.js`, `yomux.js`} {
		if !strings.Contains(text, fragment) {
			t.Fatalf("guest omitted normal home owner %s", fragment)
		}
	}
	if strings.Contains(text, "/share-guest.js") || strings.Contains(text, `class="share-workspace"`) {
		t.Fatal("separate guest workspace remains")
	}
}
