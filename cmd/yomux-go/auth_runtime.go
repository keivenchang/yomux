// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/hmac"
	"crypto/pbkdf2"
	"crypto/sha256"
	"encoding/hex"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/capabilities"
	"github.com/keivenchang/yomux/internal/productenv"
)

type authUser struct{ username, password, role string }
type authContextKey struct{}

func (s *server) users() []authUser {
	b, err := os.ReadFile(filepath.Join(s.config, "auth.yaml"))
	if err != nil {
		return nil
	}
	return parseAuthYAML(string(b))
}

func (s *server) identityStatus(r *http.Request) (authUser, int) {
	if user, ok := r.Context().Value(authContextKey{}).(authUser); ok {
		return user, 0
	}
	if productenv.Value("TEST_AUTH_BYPASS") == "1" {
		return authUser{username: "isolated-test", role: "admin"}, 0
	}
	users := s.users()
	for _, cookieName := range []string{s.authCookieName(), s.legacyAuthCookieName()} {
		cookie, err := r.Cookie(cookieName)
		if err != nil {
			continue
		}
		for _, user := range users {
			if cookie.Value != "" && hmac.Equal([]byte(cookie.Value), []byte(s.cookieValue(user))) {
				return user, 0
			}
		}
	}
	for _, marker := range []string{"yomux_logged_out", "yolomux_logged_out"} {
		if cookie, err := r.Cookie(marker); err == nil && cookie.Value == "1" {
			return authUser{}, http.StatusUnauthorized
		}
	}
	username, password, ok := r.BasicAuth()
	if !ok {
		return authUser{}, http.StatusUnauthorized
	}
	return s.verifyCredentials(r.Context(), r.RemoteAddr, username, password, users)
}

func rejectCredentialAdmission(w http.ResponseWriter, status int) bool {
	switch status {
	case http.StatusTooManyRequests:
		jsonResponse(w, status, typedError("too many requests; try again later", "auth.error.rateLimitedMinutes", true))
		return true
	case http.StatusServiceUnavailable:
		jsonResponse(w, status, typedError("authentication temporarily unavailable; try again later", "request.error.unavailable", true))
		return true
	}
	return false
}

func (s *server) cookieValue(user authUser) string {
	secret := s.authSecret()
	if len(secret) != 32 {
		return ""
	}
	h := hmac.New(sha256.New, secret)
	_, _ = h.Write([]byte(user.username + ":" + user.password))
	return hex.EncodeToString(h.Sum(nil))
}

func (s *server) authCookieName() string {
	return "yomux_auth_" + strconv.Itoa(s.port)
}

func (s *server) legacyAuthCookieName() string {
	return "yolomux_auth_" + strconv.Itoa(s.port)
}

func (s *server) authSecret() []byte {
	if s.config == "" {
		return nil
	}
	if err := os.MkdirAll(s.config, 0700); err != nil {
		return nil
	}
	secret, err := atomicfile.LoadOrCreateSecret(filepath.Join(s.config, "auth-cookie-secret"))
	if err != nil {
		return nil
	}
	return secret
}

func (s *server) secureCookies() bool { return !s.httpMode }

func passwordMatches(password, stored string) bool {
	parts := strings.Split(stored, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2_sha256" {
		return hmac.Equal([]byte(password), []byte(stored))
	}
	iterations, err := strconv.Atoi(parts[1])
	if err != nil || iterations < 1 {
		return false
	}
	salt := []byte(parts[2])
	digest, keyErr := pbkdf2.Key(sha256.New, password, salt, iterations, 32)
	if keyErr != nil {
		return false
	}
	expected, err := hex.DecodeString(parts[3])
	return err == nil && hmac.Equal(digest, expected)
}

func (s *server) protected(next http.Handler, minimumRoles ...string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !s.admitBrowserRequest(w, r, r.Method != http.MethodGet && r.Method != http.MethodHead && r.Method != http.MethodOptions) {
			return
		}
		policy := capabilities.Policy(r.Method, r.URL.RequestURI())
		role := policy.Role
		for _, minimum := range minimumRoles {
			if minimum == "admin" {
				role = minimum
			}
		}
		user, status := s.identityStatus(r)
		if status != 0 {
			if rejectCredentialAdmission(w, status) {
				return
			}
			s.rejectUnauthorized(w, r)
			return
		}

		if !roleAllows(user, role) {
			s.rejectForbidden(w, user, role)
			return
		}
		if s.capabilities != nil {
			capability := policy.Capability
			if capability != "" && !s.capabilities.Available(capability) {
				entry := s.capabilities.Entry(capability)
				jsonResponse(w, http.StatusNotImplemented, map[string]any{
					"error": "feature_unavailable", "reason": entry.Reason, "capability": capability,
				})
				return
			}
		}
		// Basic auth is a compatibility path. Mint the port-scoped cookie before the
		// downstream handler writes headers so polling can move to cookie auth without
		// exposing credentials on every request. A logout marker deliberately blocks
		// this path until the user submits the login form again.
		canonicalValid := false
		if cookie, err := r.Cookie(s.authCookieName()); err == nil {
			canonicalValid = hmac.Equal([]byte(cookie.Value), []byte(s.cookieValue(user)))
		}
		legacyValid := false
		if cookie, err := r.Cookie(s.legacyAuthCookieName()); err == nil {
			legacyValid = hmac.Equal([]byte(cookie.Value), []byte(s.cookieValue(user)))
		}
		if legacyValid && !canonicalValid {
			http.SetCookie(w, &http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(user), Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
		}
		if !canonicalValid {
			_, newMarkerErr := r.Cookie("yomux_logged_out")
			_, legacyMarkerErr := r.Cookie("yolomux_logged_out")
			if !legacyValid && newMarkerErr != nil && legacyMarkerErr != nil {
				if _, _, basicOK := r.BasicAuth(); basicOK {
					http.SetCookie(w, &http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(user), Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
				}
			}
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), authContextKey{}, user)))
	})
}

// Cookie names and SameSite do not isolate ports on the same host. Authenticate
// machine clients without Origin as before, but never let a different browser
// origin spend its ambient credentials. Forwarded headers are not trusted here.
func (s *server) admitBrowserRequest(w http.ResponseWriter, r *http.Request, mutation bool) bool {
	origins := r.Header.Values("Origin")
	allowed := true
	if len(origins) != 0 {
		allowed = len(origins) == 1 && s.sameBrowserOrigin(r.Host, origins[0])
	}
	if mutation {
		sites := r.Header.Values("Sec-Fetch-Site")
		if len(sites) != 0 {
			allowed = allowed && len(sites) == 1 && (sites[0] == "same-origin" || sites[0] == "none")
		}
	}
	if !allowed {
		http.Error(w, "origin denied", http.StatusForbidden)
	}
	return allowed
}

func (s *server) sameBrowserOrigin(host, origin string) bool {
	scheme, defaultPort := "https", "443"
	if s.httpMode {
		scheme, defaultPort = "http", "80"
	}
	parsed, err := url.Parse(origin)
	if err != nil || parsed.Scheme != scheme || parsed.Hostname() == "" || strings.HasSuffix(parsed.Host, ":") || parsed.User != nil || parsed.Opaque != "" || parsed.Path != "" || parsed.RawQuery != "" || parsed.ForceQuery || strings.Contains(origin, "#") || strings.TrimSpace(origin) != origin {
		return false
	}
	expected, err := url.Parse(scheme + "://" + host)
	if err != nil || expected.Hostname() == "" || expected.User != nil || expected.Path != "" || expected.RawQuery != "" || expected.Fragment != "" {
		return false
	}
	originPort, expectedPort := parsed.Port(), expected.Port()
	if originPort == "" {
		originPort = defaultPort
	}
	if expectedPort == "" {
		expectedPort = defaultPort
	}
	return strings.EqualFold(parsed.Hostname(), expected.Hostname()) && originPort == expectedPort
}

func requestWantsHTML(r *http.Request) bool {
	if r.URL.Path == "/" || r.URL.Path == "/login" {
		return true
	}
	accept := r.Header.Get("Accept")
	return strings.Contains(accept, "text/html") && !strings.Contains(accept, "application/json")
}

func roleAllows(user authUser, requiredRole string) bool {
	switch requiredRole {
	case "readonly":
		return user.role == "admin" || user.role == "readonly"
	case "admin":
		return user.role == "admin"
	default:
		return false
	}
}

func safeLoginNextValue(next string) string {
	if next == "" || !strings.HasPrefix(next, "/") || strings.HasPrefix(next, "//") || strings.ContainsAny(next, "\\\r\n") {
		return "/"
	}
	return next
}

func safeLoginNext(r *http.Request) string {
	return safeLoginNextValue(r.URL.RequestURI())
}

func (s *server) rejectUnauthorized(w http.ResponseWriter, r *http.Request) {
	if requestWantsHTML(r) {
		http.Redirect(w, r, "/login?next="+url.QueryEscape(safeLoginNext(r)), http.StatusSeeOther)
		return
	}
	jsonResponse(w, http.StatusUnauthorized, map[string]any{
		"user_message": map[string]any{
			"key": "auth.error.authenticationRequired", "params": map[string]any{}, "fallback": "authentication required",
		},
		"login_url": "/login?next=" + url.QueryEscape("/"),
	})
}

func (s *server) rejectForbidden(w http.ResponseWriter, user authUser, role string) {
	fallback := role + " access required"
	jsonResponse(w, http.StatusForbidden, map[string]any{
		"user_message": map[string]any{
			"key": "auth.error.accessRequired", "params": map[string]any{"role": role}, "fallback": fallback,
		},
		"role": user.role,
	})
}

func (s *server) authSetupRequired() bool {
	return authUsersRequireSetup(s.users())
}

func authUsersRequireSetup(users []authUser) bool {
	return len(users) == 0 || len(users) == 1 && users[0].username == "user" && users[0].password == "password"
}

func (s *server) authSetup(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"setup_required": s.authSetupRequired()})
}

func (s *server) login(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Method == http.MethodPost && !s.admitBrowserRequest(w, r, true) {
		return
	}
	users := s.users()
	if authUsersRequireSetup(users) {
		s.writeNativeSetupAuth(w, r)
		return
	}
	if r.Method == http.MethodGet {
		next := safeLoginNextValue(r.URL.Query().Get("next"))
		s.writeNativeLogin(w, r, next, "")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 16*1024)
	if err := r.ParseForm(); err != nil {
		jsonResponse(w, http.StatusBadRequest, typedError("invalid login request", "request.error.invalidForm", false))
		return
	}
	username, password := r.FormValue("username"), r.FormValue("password")
	user, status := s.verifyCredentials(r.Context(), r.RemoteAddr, username, password, users)
	if rejectCredentialAdmission(w, status) {
		return
	}
	if status == 0 {
		http.SetCookie(w, &http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(user), Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
		http.SetCookie(w, &http.Cookie{Name: "yomux_logged_out", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
		http.SetCookie(w, &http.Cookie{Name: "yolomux_logged_out", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
		next := safeLoginNextValue(r.FormValue("next"))
		http.Redirect(w, r, next, http.StatusSeeOther)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusUnauthorized)
	s.writeNativeLogin(w, r, r.FormValue("next"), "Invalid username or password.")
}

func (s *server) writeNativeLogin(w http.ResponseWriter, r *http.Request, next, message string) {
	body, err := s.renderPreauthPage(r, false, next, message)
	if err != nil {
		slog.Error("login page unavailable", "error", err)
		http.Error(w, "login page unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func (s *server) writeNativeSetupAuth(w http.ResponseWriter, r *http.Request) {
	body, err := s.renderPreauthPage(r, true, "/", "")
	if err != nil {
		slog.Error("authentication setup page unavailable", "error", err)
		http.Error(w, "authentication setup page unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func (s *server) logout(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !s.admitBrowserRequest(w, r, true) {
		return
	}
	for _, name := range []string{s.authCookieName(), s.legacyAuthCookieName(), "yolomux_auth"} {
		http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: "/", MaxAge: 0, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: false, Expires: time.Unix(0, 0)})
		if s.secureCookies() {
			http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: "/", MaxAge: 0, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: true, Expires: time.Unix(0, 0)})
		}
	}
	http.SetCookie(w, &http.Cookie{Name: "yomux_logged_out", Value: "1", Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
	http.SetCookie(w, &http.Cookie{Name: "yolomux_logged_out", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
	http.Redirect(w, r, "/login", http.StatusSeeOther)
}

type authState struct {
	authMu      sync.Mutex
	authLimiter *authLimiter
}
