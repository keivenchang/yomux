// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBrowserOriginAdmission(t *testing.T) {
	for _, test := range []struct {
		name     string
		host     string
		origins  []string
		sites    []string
		mutation bool
		allowed  bool
	}{
		{name: "machine", mutation: true, allowed: true},
		{name: "same origin", origins: []string{"https://example.com:7111"}, mutation: true, allowed: true},
		{name: "hostname case", origins: []string{"https://EXAMPLE.COM:7111"}, mutation: true, allowed: true},
		{name: "default origin port", host: "example.com:443", origins: []string{"https://example.com"}, mutation: true, allowed: true},
		{name: "default host port", host: "example.com", origins: []string{"https://example.com:443"}, mutation: true, allowed: true},
		{name: "ipv6", host: "[::1]:7111", origins: []string{"https://[::1]:7111"}, mutation: true, allowed: true},
		{name: "other port", origins: []string{"https://example.com:7112"}, mutation: true},
		{name: "other site", origins: []string{"https://other.example:7111"}, mutation: true},
		{name: "other scheme", origins: []string{"http://example.com:7111"}, mutation: true},
		{name: "opaque", origins: []string{"null"}, mutation: true},
		{name: "empty", origins: []string{""}, mutation: true},
		{name: "credentials", origins: []string{"https://user@example.com:7111"}, mutation: true},
		{name: "path", origins: []string{"https://example.com:7111/"}, mutation: true},
		{name: "query", origins: []string{"https://example.com:7111?"}, mutation: true},
		{name: "fragment", origins: []string{"https://example.com:7111#"}, mutation: true},
		{name: "empty port", host: "example.com", origins: []string{"https://example.com:"}, mutation: true},
		{name: "empty hostname", host: ":7111", origins: []string{"https://:7111"}, mutation: true},
		{name: "malformed", origins: []string{"https://example.com:bad"}, mutation: true},
		{name: "space", origins: []string{" https://example.com:7111"}, mutation: true},
		{name: "origin list", origins: []string{"https://example.com:7111 https://other.example"}, mutation: true},
		{name: "comma list", origins: []string{"https://example.com:7111,https://other.example"}, mutation: true},
		{name: "duplicate header", origins: []string{"https://example.com:7111", "https://example.com:7111"}, mutation: true},
		{name: "no origin same origin metadata", sites: []string{"same-origin"}, mutation: true, allowed: true},
		{name: "typed navigation", sites: []string{"none"}, mutation: true, allowed: true},
		{name: "same site unsafe", sites: []string{"same-site"}, mutation: true},
		{name: "cross site unsafe", sites: []string{"cross-site"}, mutation: true},
		{name: "unknown metadata", sites: []string{"unknown"}, mutation: true},
		{name: "duplicate metadata", sites: []string{"same-origin", "cross-site"}, mutation: true},
		{name: "inconsistent metadata", origins: []string{"https://example.com:7111"}, sites: []string{"cross-site"}, mutation: true},
		{name: "read navigation", sites: []string{"cross-site"}, allowed: true},
		{name: "explicit other origin read", origins: []string{"https://other.example:7111"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			host := test.host
			if host == "" {
				host = "example.com:7111"
			}
			r := httptest.NewRequest(http.MethodPost, "https://"+host+"/api/settings", nil)
			r.Header["Origin"] = test.origins
			r.Header["Sec-Fetch-Site"] = test.sites
			// Neither forwarded host nor scheme may grant another origin admission.
			r.Header.Set("Forwarded", "host=other.example:7111;proto=http")
			r.Header.Set("X-Forwarded-Host", "other.example:7111")
			r.Header.Set("X-Forwarded-Proto", "http")
			w := httptest.NewRecorder()
			if got := (&server{}).admitBrowserRequest(w, r, test.mutation); got != test.allowed {
				t.Fatalf("allowed=%v, want %v; status=%d", got, test.allowed, w.Code)
			}
			if !test.allowed && w.Code != http.StatusForbidden {
				t.Fatalf("rejection status=%d", w.Code)
			}
		})
	}
	for _, httpMode := range []bool{false, true} {
		scheme := "https"
		if httpMode {
			scheme = "http"
		}
		s := &server{httpMode: httpMode}
		if !s.sameBrowserOrigin("example.com:7111", scheme+"://example.com:7111") {
			t.Fatalf("actual listener scheme rejected: %s", scheme)
		}
	}
}

func TestProtectedBrowserOriginStopsEveryMutationBeforeAuthentication(t *testing.T) {
	for _, path := range []string{"/api/settings", "/api/fs/write", "/api/fs/delete", "/api/fs/rename", "/api/fs/mkdir", "/api/kill-session", "/api/kill-window", "/api/rename-session", "/api/auto-approve", "/api/operations/cancel", "/api/yoagent/jobs"} {
		t.Run(path, func(t *testing.T) {
			s := authFixture(t)
			called := false
			handler := s.protected(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				called = true
				w.WriteHeader(http.StatusNoContent)
			}))
			for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodPatch, http.MethodDelete} {
				r := httptest.NewRequest(method, "http://example.com:7111"+path, strings.NewReader("{}"))
				r.Header.Set("Origin", "http://example.com:7112")
				r.SetBasicAuth("admin", "secret")
				w := httptest.NewRecorder()
				handler.ServeHTTP(w, r)
				if w.Code != http.StatusForbidden || called || len(w.Result().Cookies()) != 0 {
					t.Fatalf("cross-origin %s dispatched or minted cookies: %d", method, w.Code)
				}
			}
			if _, err := os.Stat(filepath.Join(s.state, "go-login-rate.sqlite3")); !os.IsNotExist(err) {
				t.Fatalf("origin denial touched credential admission: %v", err)
			}
			for _, origin := range []string{"", "http://example.com:7111"} {
				r := httptest.NewRequest(http.MethodPost, "http://example.com:7111"+path, nil)
				if origin != "" {
					r.Header.Set("Origin", origin)
				}
				r.SetBasicAuth("admin", "secret")
				w := httptest.NewRecorder()
				handler.ServeHTTP(w, r)
				if w.Code != http.StatusNoContent || !called {
					t.Fatalf("machine/same-origin request rejected: %d", w.Code)
				}
			}
		})
	}
}

func TestBrowserAuthEntryPointAdmission(t *testing.T) {
	for _, route := range []string{"login", "logout"} {
		for _, method := range []string{http.MethodGet, http.MethodPost} {
			if route == "login" && method == http.MethodGet {
				continue // Reading the login/setup page must remain navigable from other sites.
			}
			t.Run(method+" "+route, func(t *testing.T) {
				s := authFixture(t)
				r := httptest.NewRequest(method, "http://example.com:7111/"+route, strings.NewReader("username=admin&password=secret"))
				r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
				r.Header.Set("Sec-Fetch-Site", "cross-site")
				w := httptest.NewRecorder()
				if route == "login" {
					s.login(w, r)
				} else {
					s.logout(w, r)
				}
				if w.Code != http.StatusForbidden || len(w.Result().Cookies()) != 0 {
					t.Fatalf("cross-site %s: status=%d, cookies=%d", route, w.Code, len(w.Result().Cookies()))
				}
			})
		}
	}
	for _, method := range []string{http.MethodGet, http.MethodPost} {
		s := authFixture(t)
		r := httptest.NewRequest(method, "/api/auth-setup", nil)
		w := httptest.NewRecorder()
		s.authSetup(w, r)
		if w.Code != http.StatusOK || len(w.Result().Cookies()) != 0 {
			t.Fatalf("setup %s: status=%d", method, w.Code)
		}
	}
}

func TestWebSocketBrowserOriginRejectedBeforeAuthentication(t *testing.T) {
	for _, origin := range []string{"null", "http://example.com:7112", "https://example.com:7111"} {
		s := authFixture(t)
		r := httptest.NewRequest(http.MethodGet, "http://example.com:7111/ws", nil)
		r.Header.Set("Origin", origin)
		r.SetBasicAuth("admin", "wrong")
		w := httptest.NewRecorder()
		s.websocket(w, r)
		if w.Code != http.StatusForbidden {
			t.Fatalf("foreign WebSocket origin charged authentication: %d", w.Code)
		}
		if _, err := os.Stat(filepath.Join(s.state, "go-login-rate.sqlite3")); !os.IsNotExist(err) {
			t.Fatalf("foreign WebSocket origin touched credential admission: %v", err)
		}
	}
}

func TestKillSessionRejectsSafeAndUnsupportedMethods(t *testing.T) {
	for _, method := range []string{http.MethodGet, http.MethodHead, http.MethodOptions, http.MethodPut, http.MethodPatch, http.MethodDelete} {
		w := httptest.NewRecorder()
		(&server{}).killSession(w, httptest.NewRequest(method, "/api/kill-session?session=fixture", nil))
		if w.Code != http.StatusMethodNotAllowed || w.Header().Get("Allow") != http.MethodPost {
			t.Fatalf("%s status=%d, want 405 before touching tmux", method, w.Code)
		}
	}
}
