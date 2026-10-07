// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestBasicAdmissionRejectsRepeatedInvalidCredentials(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: dir, state: dir}
	handler := s.protected(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(200) }))
	for i := 0; i < 7; i++ {
		r := httptest.NewRequest("GET", "/api/ping", nil)
		r.RemoteAddr = "[2001:db8::1]:1234"
		r.SetBasicAuth("admin", "wrong")
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if i == 6 && w.Code != 429 {
			t.Fatalf("seventh invalid Basic request = %d, want 429", w.Code)
		}
	}
}

func authFixture(t *testing.T) *server {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "auth.yaml"), []byte("users:\n  - username: admin\n    password: secret\n    role: admin\n"), 0600); err != nil {
		t.Fatal(err)
	}
	return &server{config: dir, state: dir, httpMode: true}
}
func TestAuthAdmissionSuccessRefundsAndInvalidStillThrottles(t *testing.T) {
	s := authFixture(t)
	for i := 0; i < 40; i++ {
		user, status := s.verifyCredentials(context.Background(), "192.0.2.1:9999", "admin", "secret", s.users())
		if status != 0 || user.username != "admin" {
			t.Fatalf("valid request %d: %d", i, status)
		}
	}
	for i := 0; i < 6; i++ {
		_, status := s.verifyCredentials(context.Background(), "192.0.2.1:9999", "admin", "bad", s.users())
		if status != 401 {
			t.Fatalf("invalid request %d: %d", i, status)
		}
	}
	_, status := s.verifyCredentials(context.Background(), "192.0.2.1:9999", "admin", "bad", s.users())
	if status != 429 {
		t.Fatalf("invalid request after allowance: %d", status)
	}
	// Valid cookies never touch a broken limiter store.
	cookie := s.cookieValue(s.users()[0])
	s.authLimiter.path = filepath.Join(s.state, "absent", "db")
	r := httptest.NewRequest("GET", "/api/ping", nil)
	r.AddCookie(&http.Cookie{Name: s.authCookieName(), Value: cookie})
	if _, status = s.identityStatus(r); status != 0 {
		t.Fatalf("cookie charged on unavailable store: %d", status)
	}
}
func TestAuthAdmissionRestartUnknownUsernameAndSharedRoutes(t *testing.T) {
	s := authFixture(t)
	for i := 0; i < 6; i++ {
		_, status := s.verifyCredentials(context.Background(), "[2001:db8::1]:42", "nonexistent", "bad", s.users())
		if status != 401 {
			t.Fatalf("unknown failed verification: %d", status)
		}
	}
	restarted := &server{config: s.config, state: s.state}
	r := httptest.NewRequest("GET", "/ws", nil)
	r.RemoteAddr = "[2001:db8::2]:42"
	r.SetBasicAuth("nonexistent", "bad")
	w := httptest.NewRecorder()
	restarted.websocket(w, r)
	if w.Code != 429 {
		t.Fatalf("WebSocket restart throttle: %d", w.Code)
	}
	r = httptest.NewRequest("POST", "/login", strings.NewReader("username=nonexistent&password=bad"))
	r.RemoteAddr = "[2001:db8::2]:42"
	r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	w = httptest.NewRecorder()
	restarted.login(w, r)
	if w.Code != 429 {
		t.Fatalf("form shared username throttle: %d", w.Code)
	}
	data, err := os.ReadFile(filepath.Join(s.state, "go-login-rate.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{"nonexistent", "2001:db8"} {
		if bytes.Contains(data, []byte(raw)) {
			t.Fatalf("persisted raw identifier %s", raw)
		}
	}
}
func TestAuthAdmissionEachNetworkScopeAndIPv6(t *testing.T) {
	for scope := 0; scope < 4; scope++ {
		t.Run(fmt.Sprint(scope), func(t *testing.T) {
			s := authFixture(t)
			l, err := s.loginLimiter()
			if err != nil {
				t.Fatal(err)
			}
			p := defaultAuthRatePolicy()
			p.Exact = authBucketPolicy{50, .001}
			p.Nearby = p.Exact
			p.Broad = p.Exact
			p.Global = p.Exact
			switch scope {
			case 0:
				p.Exact.Capacity = 2
			case 1:
				p.Nearby.Capacity = 2
			case 2:
				p.Broad.Capacity = 2
			case 3:
				p.Global.Capacity = 2
			}
			l.policy = p
			l.now = func() time.Time { return time.Unix(1000, 0) }
			peers := []string{"192.0.2.1:99", "192.0.2.2:99", "192.0.2.3:99"}
			switch scope {
			case 0:
				peers = []string{"[::ffff:192.0.2.1]:99", "192.0.2.1:99", "192.0.2.1:100"}
			case 2:
				peers = []string{"192.0.1.1:99", "192.0.2.1:99", "192.0.3.1:99"}
			case 3:
				peers = []string{"192.0.1.1:99", "198.51.2.1:99", "203.0.3.1:99"}
			}
			for i, peer := range peers {
				claim, status, err := l.reserve(context.Background(), peer, fmt.Sprint(i))
				if err != nil {
					t.Fatal(err)
				}
				if i < 2 {
					if status != 0 {
						t.Fatal(status)
					}
					if err = claim.finish(context.Background(), false); err != nil {
						t.Fatal(err)
					}
				} else if status != 429 {
					t.Fatalf("scope %d did not throttle: %d", scope, status)
				}
			}
		})
	}
	for _, peer := range []string{"[2001:db8:1234:5678::1]:42", "2001:db8:1234:5678::1", "[::ffff:192.0.2.1]:42"} {
		if _, err := peerAddress(peer); err != nil {
			t.Fatal(peer, err)
		}
	}
	s := authFixture(t)
	l, _ := s.loginLimiter()
	a, _ := peerAddress("[2001:db8:1234:5678::1]:42")
	b, _ := peerAddress("[2001:db8:1234:5678::2]:43")
	keysA, keysB := l.networkKeys(a), l.networkKeys(b)
	if keysA[0] == keysB[0] || keysA[1] != keysB[1] || keysA[2] != keysB[2] {
		t.Fatal("IPv6 prefix scopes disagree")
	}
}
func TestAuthAdmissionConcurrentReservationsExactlyOnceAndAbandoned(t *testing.T) {
	s := authFixture(t)
	l, err := s.loginLimiter()
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1000, 0)
	l.now = func() time.Time { return now }
	var wg sync.WaitGroup
	var mu sync.Mutex
	claims := []*authReservation{}
	statuses := []int{}
	for i := 0; i < 30; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			claim, status, e := l.reserve(context.Background(), "192.0.2.1:42", fmt.Sprint(i))
			mu.Lock()
			defer mu.Unlock()
			if e != nil {
				t.Error(e)
			}
			statuses = append(statuses, status)
			if claim != nil {
				claims = append(claims, claim)
			}
		}(i)
	}
	wg.Wait()
	if len(claims) != 10 {
		t.Fatalf("atomic exact bucket admitted %d, want 10; statuses %v", len(claims), statuses)
	}
	for _, claim := range claims[:5] {
		if err = claim.finish(context.Background(), true); err != nil {
			t.Fatal(err)
		}
		if claim.finish(context.Background(), true) == nil {
			t.Fatal("double refund accepted")
		}
	}
	for i := 0; i < 5; i++ {
		claim, status, e := l.reserve(context.Background(), "192.0.2.1:42", fmt.Sprint(100+i))
		if e != nil || status != 0 {
			t.Fatalf("refund %d: %d %v", i, status, e)
		}
		if err = claim.finish(context.Background(), false); err != nil {
			t.Fatal(err)
		}
	}
	if _, status, _ := l.reserve(context.Background(), "192.0.2.1:42", "another"); status != 429 {
		t.Fatal("failed and abandoned charges were refunded")
	}
	now = now.Add(121 * time.Second)
	claim, status, e := l.reserve(context.Background(), "192.0.2.1:42", "later")
	if e != nil || status != 0 {
		t.Fatalf("abandoned claim blocked forever: %d %v", status, e)
	}
	if err = claim.finish(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if claims[5].finish(context.Background(), true) == nil {
		t.Fatal("expired claim refunded after cleanup")
	}
}

func TestAuthAdmissionCanceledWriteWaitersDoNotOpenStore(t *testing.T) {
	s := authFixture(t)
	l, err := s.loginLimiter()
	if err != nil {
		t.Fatal(err)
	}
	l.path = filepath.Join(s.state, "missing-parent", "database.sqlite3")
	l.writeGate <- struct{}{}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if _, status, reserveErr := l.reserve(ctx, "192.0.2.1:42", "admin"); status != 503 || !errors.Is(reserveErr, context.Canceled) {
		t.Fatalf("canceled reservation waiter = status %d, err %v; want context cancellation before store access", status, reserveErr)
	}
	if finishErr := (&authReservation{limiter: l}).finish(ctx, false); !errors.Is(finishErr, context.Canceled) {
		t.Fatalf("canceled settlement waiter = %v; want context cancellation before store access", finishErr)
	}
	deadlineCtx, deadlineCancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	if _, status, reserveErr := l.reserve(deadlineCtx, "192.0.2.1:42", "admin"); status != 503 || !errors.Is(reserveErr, context.DeadlineExceeded) {
		t.Fatalf("expired reservation waiter = status %d, err %v; want deadline expiry before store access", status, reserveErr)
	}
	deadlineCancel()
	deadlineCtx, deadlineCancel = context.WithTimeout(context.Background(), 50*time.Millisecond)
	if finishErr := (&authReservation{limiter: l}).finish(deadlineCtx, false); !errors.Is(finishErr, context.DeadlineExceeded) {
		t.Fatalf("expired settlement waiter = %v; want deadline expiry before store access", finishErr)
	}
	deadlineCancel()
	<-l.writeGate
}

func TestAuthAdmissionStoreFailureAndPolicyValidation(t *testing.T) {
	s := authFixture(t)
	if err := os.WriteFile(filepath.Join(s.state, "blocked"), []byte("x"), 0600); err != nil {
		t.Fatal(err)
	}
	s.state = filepath.Join(s.state, "blocked", "child")
	r := httptest.NewRequest("GET", "/ws", nil)
	r.SetBasicAuth("admin", "secret")
	w := httptest.NewRecorder()
	s.websocket(w, r)
	if w.Code != 503 {
		t.Fatalf("store failure = %d", w.Code)
	}
	s = authFixture(t)
	override := filepath.Join(s.config, "login-rate-limit.json")
	if err := os.WriteFile(override, []byte(`{"exact_bucket":{"capacity":2},"username_initial_allowance":3}`), 0600); err != nil {
		t.Fatal(err)
	}
	p := loadAuthRatePolicy(s.config)
	if p.Exact.Capacity != 2 || p.Exact.Refill != 5 || p.Allowance != 3 {
		t.Fatalf("override not layered: %+v", p)
	}
	if err := os.WriteFile(override, []byte(`{"global_bucket":{"capacity":1}}`), 0600); err != nil {
		t.Fatal(err)
	}
	if loadAuthRatePolicy(s.config).Global.Capacity != 1000 {
		t.Fatal("incoherent policy installed")
	}
}

func TestAuthAdmissionFutureSchemaUntouchedAndIdleFailuresExpire(t *testing.T) {
	s := authFixture(t)
	path := filepath.Join(s.state, "go-login-rate.sqlite3")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec("CREATE TABLE auth_meta(version INTEGER);INSERT INTO auth_meta VALUES(2)"); err != nil {
		t.Fatal(err)
	}
	db.Close()
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.loginLimiter(); err == nil {
		t.Fatal("future schema admitted")
	}
	after, _ := os.ReadFile(path)
	if !bytes.Equal(before, after) {
		t.Fatal("future database mutated")
	}
	for _, suffix := range []string{"-wal", "-shm"} {
		if _, err = os.Stat(path + suffix); !os.IsNotExist(err) {
			t.Fatal("future schema created sidecar", suffix)
		}
	}
	s = authFixture(t)
	l, err := s.loginLimiter()
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(30000, 0)
	l.now = func() time.Time { return now }
	db, err = l.open()
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec("INSERT INTO auth_users VALUES('old-failure',1,0,0,0),('locked',100,0,0,1),('cooling',6,40000,0,0)")
	if err != nil {
		t.Fatal(err)
	}
	db.Close()
	claim, status, err := l.reserve(context.Background(), "192.0.2.1:99", "new")
	if err != nil || status != 0 {
		t.Fatal(status, err)
	}
	if err = claim.finish(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	db, err = l.open()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var count int
	if err = db.QueryRow("SELECT count(*) FROM auth_users WHERE key='old-failure'").Scan(&count); err != nil || count != 0 {
		t.Fatal("idle failures retained", count, err)
	}
	if err = db.QueryRow("SELECT count(*) FROM auth_users WHERE key IN ('locked','cooling')").Scan(&count); err != nil || count != 2 {
		t.Fatal("active protection evicted", count, err)
	}
}

func TestAuthAdmissionSaturationRecoversAfterIdleRetention(t *testing.T) {
	s := authFixture(t)
	l, err := s.loginLimiter()
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(100000, 0)
	l.now = func() time.Time { return now }
	db, err := l.open()
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`WITH RECURSIVE ids(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM ids WHERE n<49996) INSERT INTO auth_users SELECT printf('fixture-%d',n),100,0,100000,0 FROM ids`)
	if err != nil {
		t.Fatal(err)
	}
	db.Close()
	if _, status, err := l.reserve(context.Background(), "192.0.2.1:99", "admin"); err != nil || status != 429 {
		t.Fatal("state cap was not enforced", status, err)
	}
	now = now.Add(21601 * time.Second)
	claim, status, err := l.reserve(context.Background(), "192.0.2.1:99", "admin")
	if err != nil || status != 0 {
		t.Fatal("saturation never recovered", status, err)
	}
	if err = claim.finish(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	db, err = l.open()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var count int
	if err = db.QueryRow("SELECT count(*) FROM auth_users").Scan(&count); err != nil || count != 1 {
		t.Fatal("expired state was not durably reclaimed", count, err)
	}
}
func TestAuthAdmissionCooldownDenialsDoNotExtendAndHardLockPersists(t *testing.T) {
	s := authFixture(t)
	l, err := s.loginLimiter()
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(100000, 0)
	l.now = func() time.Time { return now }
	l.policy.Allowance = 1
	l.policy.Ceiling = 3
	l.policy.Ladder = []int{1, 2}
	fail := func() {
		t.Helper()
		claim, status, e := l.reserve(context.Background(), "192.0.2.1:99", "admin", true)
		if e != nil || status != 0 {
			t.Fatal(status, e)
		}
		if e = claim.finish(context.Background(), false); e != nil {
			t.Fatal(e)
		}
	}
	fail()
	fail()
	for i := 0; i < 3; i++ {
		if _, status, e := l.reserve(context.Background(), "192.0.2.1:99", "admin"); e != nil || status != 429 {
			t.Fatal("cooldown bypassed", status, e)
		}
	}
	now = now.Add(time.Second)
	fail()
	now = now.Add(24 * time.Hour)
	if _, status, e := l.reserve(context.Background(), "198.51.100.1:99", "admin"); e != nil || status != 429 {
		t.Fatal("hard lock expired or became address-local", status, e)
	}
}

func TestAuthAdmissionSkewedClocksCannotRefillTwice(t *testing.T) {
	s := authFixture(t)
	l, err := s.loginLimiter()
	if err != nil {
		t.Fatal(err)
	}
	l.now = func() time.Time { return time.Unix(1000, 0) }
	other := *l
	other.now = func() time.Time { return time.Unix(900, 0) }
	first, status, err := l.reserve(context.Background(), "192.0.2.1:42", "first")
	if status != 0 || err != nil {
		t.Fatal(status, err)
	}
	if err = first.finish(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	second, status, err := other.reserve(context.Background(), "192.0.2.1:42", "second")
	if status != 0 || err != nil {
		t.Fatal(status, err)
	}
	if err = second.finish(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	l.now = func() time.Time { return time.Unix(1006, 0) }
	third, status, err := l.reserve(context.Background(), "192.0.2.1:42", "third")
	if status != 0 || err != nil {
		t.Fatal(status, err)
	}
	if err = third.finish(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	db, err := l.open()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	addr, _ := peerAddress("192.0.2.1:42")
	var tokens, updated float64
	if err = db.QueryRow("SELECT tokens,updated FROM auth_buckets WHERE key=?", l.networkKeys(addr)[0]).Scan(&tokens, &updated); err != nil || tokens != 8.5 || updated != 1006 {
		t.Fatalf("skew minted refill: %v %v %v", tokens, updated, err)
	}
}
func TestAuthAdmissionFutureWALCannotCreateOriginalSHM(t *testing.T) {
	s := authFixture(t)
	path := filepath.Join(s.state, "go-login-rate.sqlite3")
	db, err := sql.Open("sqlite3", path+"?_journal_mode=WAL")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err = db.Exec("PRAGMA wal_autocheckpoint=0;CREATE TABLE auth_meta(version INTEGER);INSERT INTO auth_meta VALUES(2)"); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	walBefore, err := os.ReadFile(path + "-wal")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Remove(path + "-shm"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.loginLimiter(); err == nil {
		t.Fatal("future WAL schema admitted")
	}
	after, _ := os.ReadFile(path)
	walAfter, _ := os.ReadFile(path + "-wal")
	if !bytes.Equal(before, after) || !bytes.Equal(walBefore, walAfter) {
		t.Fatal("incompatible WAL artifacts mutated")
	}
	if _, err = os.Stat(path + "-shm"); !os.IsNotExist(err) {
		t.Fatal("incompatible preflight created original SHM", err)
	}
}
