// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/keivenchang/yomux/internal/capabilities"
	"github.com/keivenchang/yomux/internal/chat"
)

func nativeChatFixture(t *testing.T, publish func(context.Context, string, map[string]any) error) (*server, *http.ServeMux) {
	t.Helper()
	s := nativeSettingsFixtureServer(t)
	s.events = newEventBroker()
	s.capabilities = capabilities.New("chat-http-test")
	s.capabilities.Set("pane.chat.v1", capabilities.Entry{Available: true})
	if publish == nil {
		publish = func(_ context.Context, kind string, payload map[string]any) error {
			_, err := s.events.publish(kind, payload)
			return err
		}
	}
	runtime, err := newNativeChat(context.Background(), s.root, filepath.Join(t.TempDir(), "yochat.sqlite3"), s.chatRetentionDays, publish)
	if err != nil {
		t.Fatal(err)
	}
	s.chat = runtime
	t.Cleanup(func() {
		if err := runtime.store.Close(); err != nil {
			t.Error(err)
		}
	})
	mux := http.NewServeMux()
	s.registerChatRoutes(mux)
	// Product dispatch must work with no Python or other executables on PATH.
	t.Setenv("PATH", t.TempDir())
	return s, mux
}

func chatHTTPCall(t *testing.T, mux http.Handler, method, path, body string, status int, cookies ...*http.Cookie) map[string]any {
	t.Helper()
	request := httptest.NewRequest(method, "/api/chat/"+path, strings.NewReader(body))
	for _, cookie := range cookies {
		request.AddCookie(cookie)
	}
	request.RemoteAddr = "[2001:db8::5]:41234"
	request.Header.Set("X-Forwarded-For", "192.0.2.2")
	request = request.WithContext(context.WithValue(request.Context(), authContextKey{}, authUser{username: "alice", role: "readonly"}))
	response := httptest.NewRecorder()
	mux.ServeHTTP(response, request)
	if response.Code != status {
		t.Fatalf("%s %s status=%d want=%d body=%s", method, path, response.Code, status, response.Body)
	}
	if response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("chat response can be cached")
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	return payload
}

func TestNativeChatHTTPHistoryTypingSearchAndReadWithoutPython(t *testing.T) {
	_, mux := nativeChatFixture(t, nil)
	first := chatHTTPCall(t, mux, "GET", "bootstrap?browser_instance_id=browser", "", 200)
	if first["client_ip"] != "2001:db8::5" || first["first_registration"] != true {
		t.Fatal("bootstrap identity changed")
	}
	body := `{"browser_instance_id":"browser","client_message_uuid":"one","body":"Where is café?"}`
	sent := chatHTTPCall(t, mux, "POST", "send", body, 201)
	message := sent["message"].(map[string]any)
	if message["sender_ip"] != "2001:db8::5" || message["username"] != "alice" || message["is_question"] != true {
		t.Fatal("send identity or question changed")
	}
	duplicate := chatHTTPCall(t, mux, "POST", "send", body, 200)
	if duplicate["created"] != false {
		t.Fatal("send retry inserted twice")
	}
	delta := chatHTTPCall(t, mux, "GET", "delta?after="+first["newer_cursor"].(string), "", 200)
	if len(delta["messages"].([]any)) != 1 {
		t.Fatal("delta lost message")
	}
	page := chatHTTPCall(t, mux, "GET", "page?limit=0", "", 200)
	if len(page["messages"].([]any)) != 1 {
		t.Fatal("page limit zero changed")
	}
	context := chatHTTPCall(t, mux, "GET", "context?message_id=1&before=0&after=0", "", 200)
	if context["target"].(map[string]any)["id"] != float64(1) {
		t.Fatal("context target changed")
	}
	search := chatHTTPCall(t, mux, "GET", "search?query=cafe", "", 200)
	if len(search["hits"].([]any)) != 1 {
		t.Fatal("accented FTS search lost message")
	}
	chatHTTPCall(t, mux, "POST", "typing", `{"browser_instance_id":"other","typing":true}`, 200)
	active := chatHTTPCall(t, mux, "GET", "bootstrap?browser_instance_id=browser", "", 200)
	if len(active["typing"].([]any)) != 1 {
		t.Fatal("typing did not reach other browser")
	}
	cleared := chatHTTPCall(t, mux, "POST", "typing", `{"browser_instance_id":"other","typing":false}`, 200)
	if len(cleared["typing"].([]any)) != 0 {
		t.Fatal("typing did not clear")
	}
	read := chatHTTPCall(t, mux, "POST", "read", `{"message_id":1}`, 200)
	if read["read_up_to_id"] != float64(1) {
		t.Fatal("read cursor did not advance")
	}
}

func TestNativeChatHTTPRepairsPublicationOnIdempotentRetry(t *testing.T) {
	var fail atomic.Bool
	fail.Store(true)
	var messages atomic.Int64
	_, mux := nativeChatFixture(t, func(_ context.Context, kind string, _ map[string]any) error {
		if kind == "chat_messages_changed" {
			if fail.CompareAndSwap(true, false) {
				return errors.New("injected event failure")
			}
			messages.Add(1)
		}
		return nil
	})
	body := `{"browser_instance_id":"browser","client_message_uuid":"one","body":"hello"}`
	chatHTTPCall(t, mux, "POST", "send", body, 500)
	retried := chatHTTPCall(t, mux, "POST", "send", body, 200)
	if retried["created"] != false || messages.Load() != 1 {
		t.Fatal("retry duplicated send or failed to repair event")
	}
	page := chatHTTPCall(t, mux, "GET", "page", "", 200)
	if len(page["messages"].([]any)) != 1 {
		t.Fatal("failed publication repeated insertion")
	}
}

func TestNativeChatHTTPRejectsInvalidRequestsAndMalformedSettings(t *testing.T) {
	s, mux := nativeChatFixture(t, nil)
	for _, example := range []struct {
		method, path, body string
		status             int
	}{
		{"GET", "send", "", 405},
		{"POST", "send", "null", 400},
		{"POST", "send", "[]", 400},
		{"POST", "send", `{} {}`, 400},
		{"POST", "typing", strings.Repeat("x", 4097), 400},
		{"POST", "read", `{"message_id":"bad"}`, 400},
		{"GET", "context?message_id=1&before=bad", "", 400},
		{"GET", "search?query=hello&limit=bad", "", 400},
		{"GET", "page?before=bad", "", 400},
		{"GET", "context?message_id=999", "", 404},
	} {
		payload := chatHTTPCall(t, mux, example.method, example.path, example.body, example.status)
		if example.status != 405 && (payload["code"] == nil || payload["status"] != float64(example.status) || payload["user_message"] == nil) {
			t.Fatal("chat error envelope changed")
		}
	}
	if err := os.WriteFile(filepath.Join(s.config, "settings.yaml"), []byte("chat: ["), 0600); err != nil {
		t.Fatal(err)
	}
	payload := chatHTTPCall(t, mux, "POST", "send", `{"browser_instance_id":"browser","client_message_uuid":"one","body":"hello"}`, 503)
	if payload["code"] != "settings_file_malformed" {
		t.Fatal("malformed settings were hidden")
	}
	snapshot, err := s.chat.store.EventSnapshot(context.Background())
	if err != nil || snapshot.LatestID != 0 {
		t.Fatalf("malformed settings allowed write: tail=%d err=%v", snapshot.LatestID, err)
	}
}

func TestNativeChatRoutesEnforceAuthAndCapabilityWithoutOpeningStore(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	t.Setenv("YOMUX_TEST_AUTH_BYPASS", "")
	t.Setenv("YOLOMUX_TEST_AUTH_BYPASS", "")
	s.capabilities = capabilities.New("unavailable-chat")
	mux := http.NewServeMux()
	s.registerChatRoutes(mux)
	response := httptest.NewRecorder()
	mux.ServeHTTP(response, httptest.NewRequest("GET", "/api/chat/page", nil))
	if response.Code != 401 {
		t.Fatalf("unauthenticated chat status=%d", response.Code)
	}
	chatHTTPCall(t, mux, "POST", "send", `{}`, 501)
	entries, err := os.ReadDir(s.config)
	if err != nil || len(entries) != 0 {
		t.Fatalf("unavailable chat mutated config: entries=%v err=%v", entries, err)
	}
}

func TestNativeChatHTTPSavedLanguageAndValidatedCookie(t *testing.T) {
	s, mux := nativeChatFixture(t, nil)
	if _, err := s.nativeSettingsPayload(context.Background(), map[string]any{"general": map[string]any{"language": "fr"}}, true); err != nil {
		t.Fatal(err)
	}
	for index, example := range []struct {
		cookie   string
		question bool
	}{
		{"", true},
		{"yomux_locale=invalid", true},
		{"yomux_locale=en-XA", true},
		{"yomux_locale=%66r", true},
		{"yolomux_locale=fr", true},
		{"yomux_locale=en", false},
	} {
		var cookies []*http.Cookie
		if name, value, ok := strings.Cut(example.cookie, "="); ok {
			cookies = append(cookies, &http.Cookie{Name: name, Value: value})
		}
		body := `{"browser_instance_id":"browser","client_message_uuid":"locale-` + strconv.Itoa(index) + `","body":"Pourquoi cela arrive"}`
		payload := chatHTTPCall(t, mux, "POST", "send", body, 201, cookies...)
		if payload["message"].(map[string]any)["is_question"] != example.question {
			t.Fatalf("cookie %q: saved French question changed", example.cookie)
		}
	}
}

func TestNativeChatHTTPAgentForwardsRoleLanguageAndSource(t *testing.T) {
	s, mux := nativeChatFixture(t, nil)
	if _, err := s.nativeSettingsPayload(context.Background(), map[string]any{"general": map[string]any{"language": "fr"}}, true); err != nil {
		t.Fatal(err)
	}
	replies, err := chat.NewAgentReplies(s.chat.service, s.chat.events, func(_ context.Context, request chat.AgentRequest) (string, error) {
		if request.AccessRole != "readonly" || request.Locale != "fr" || request.RequestID != "yochat-1" || request.Message != "Pourquoi cela arrive" {
			t.Errorf("agent context changed: %+v", request)
		}
		return "Une réponse", nil
	})
	if err != nil {
		t.Fatal(err)
	}
	s.chat.replies = replies
	chatHTTPCall(t, mux, "POST", "send", `{"browser_instance_id":"browser","client_message_uuid":"source","body":"Pourquoi cela arrive"}`, 201)
	chatHTTPCall(t, mux, "POST", "yoagent", `{"browser_instance_id":"another-browser","message_id":1}`, 403)
	body := `{"browser_instance_id":"browser","message_id":1}`
	reply := chatHTTPCall(t, mux, "POST", "yoagent", body, 201)
	if reply["source_message_id"] != float64(1) || reply["message"].(map[string]any)["body"] != "Une réponse" {
		t.Fatal("agent reply envelope changed")
	}
	retry := chatHTTPCall(t, mux, "POST", "yoagent", body, 200)
	if retry["created"] != false {
		t.Fatal("agent retry inserted twice")
	}
	bootstrap := chatHTTPCall(t, mux, "GET", "bootstrap?browser_instance_id=browser", "", 200)
	if len(bootstrap["typing"].([]any)) != 0 {
		t.Fatal("agent request left typing lease")
	}
}

func TestNativeChatHTTPMessageIDPreservesIntegerPrecision(t *testing.T) {
	for _, raw := range []json.RawMessage{json.RawMessage(`9007199254740993`), json.RawMessage(`"9007199254740993"`)} {
		id, err := chatMessageID(raw)
		if err != nil || id != 9007199254740993 {
			t.Fatalf("message ID rounded: id=%d err=%v", id, err)
		}
	}
	for _, raw := range []json.RawMessage{json.RawMessage(`9223372036854775808`), json.RawMessage(`1.5`), json.RawMessage(`true`), json.RawMessage(`null`)} {
		if _, err := chatMessageID(raw); err == nil {
			t.Fatalf("invalid message ID accepted: %s", raw)
		}
	}
}
