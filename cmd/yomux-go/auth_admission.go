// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/mutablefs"
	_ "github.com/mattn/go-sqlite3"
)

// Policy keys intentionally match the documented operator override interface.
type authBucketPolicy struct {
	Capacity int     `json:"capacity"`
	Refill   float64 `json:"refill_per_minute"`
}
type authRatePolicy struct {
	Exact4    int              `json:"exact_ipv4_prefix"`
	Exact6    int              `json:"exact_ipv6_prefix"`
	Nearby4   int              `json:"nearby_ipv4_prefix"`
	Nearby6   int              `json:"nearby_ipv6_prefix"`
	Broad4    int              `json:"broad_ipv4_prefix"`
	Broad6    int              `json:"broad_ipv6_prefix"`
	Exact     authBucketPolicy `json:"exact_bucket"`
	Nearby    authBucketPolicy `json:"nearby_bucket"`
	Broad     authBucketPolicy `json:"broad_bucket"`
	Global    authBucketPolicy `json:"global_bucket"`
	Allowance int              `json:"username_initial_allowance"`
	Ceiling   int              `json:"username_hard_ceiling"`
	Ladder    []int            `json:"username_cooldown_ladder"`
}

func defaultAuthRatePolicy() authRatePolicy {
	return authRatePolicy{32, 128, 24, 64, 16, 48, authBucketPolicy{10, 5}, authBucketPolicy{50, 25}, authBucketPolicy{200, 100}, authBucketPolicy{1000, 500}, 5, 100, []int{30, 60, 120, 300, 600, 1800, 3600}}
}
func (p authRatePolicy) valid() bool {
	if p.Broad4 < 1 || p.Broad4 > p.Nearby4 || p.Nearby4 > p.Exact4 || p.Exact4 > 32 || p.Broad6 < 1 || p.Broad6 > p.Nearby6 || p.Nearby6 > p.Exact6 || p.Exact6 > 128 || p.Allowance < 1 || p.Ceiling <= p.Allowance || len(p.Ladder) == 0 {
		return false
	}
	previous := 0
	for _, b := range []authBucketPolicy{p.Exact, p.Nearby, p.Broad, p.Global} {
		if b.Capacity < 1 || b.Capacity < previous || b.Refill <= 0 || math.IsNaN(b.Refill) || math.IsInf(b.Refill, 0) {
			return false
		}
		previous = b.Capacity
	}
	previous = 0
	for _, seconds := range p.Ladder {
		if seconds <= 0 || seconds < previous {
			return false
		}
		previous = seconds
	}
	return true
}
func loadAuthRatePolicy(config string) authRatePolicy {
	p := defaultAuthRatePolicy()
	raw, err := os.ReadFile(filepath.Join(config, "login-rate-limit.json"))
	if err != nil {
		return p
	}
	if json.Unmarshal(raw, &p) != nil || !p.valid() {
		return defaultAuthRatePolicy()
	}
	return p
}

type authLimiter struct {
	path      string
	secret    []byte
	policy    authRatePolicy
	now       func() time.Time
	writeGate chan struct{}
}
type authReservation struct {
	id, user     string
	keys         []string
	limiter      *authLimiter
	knownAccount bool
}

const authClaimTTL = 120.0
const authMaxRows = 50000
const authMaxInflight = 32

func peerAddress(remote string) (netip.Addr, error) {
	host, _, err := net.SplitHostPort(remote)
	if err != nil {
		host = remote
	}
	addr, err := netip.ParseAddr(host)
	if err != nil {
		return netip.Addr{}, errors.New("invalid socket peer")
	}
	return addr.Unmap().WithZone(""), nil
}
func (l *authLimiter) key(scope, material string) string {
	h := hmac.New(sha256.New, l.secret)
	h.Write([]byte(scope + "\x00" + material))
	return hex.EncodeToString(h.Sum(nil))
}
func (l *authLimiter) networkKeys(addr netip.Addr) []string {
	p := l.policy
	widths := []int{p.Exact6, p.Nearby6, p.Broad6}
	if addr.Is4() {
		widths = []int{p.Exact4, p.Nearby4, p.Broad4}
	}
	keys := make([]string, 0, 4)
	for i, width := range widths {
		keys = append(keys, l.key(fmt.Sprint(i), netip.PrefixFrom(addr, width).Masked().String()))
	}
	return append(keys, l.key("global", "all"))
}
func (l *authLimiter) buckets() []authBucketPolicy {
	return []authBucketPolicy{l.policy.Exact, l.policy.Nearby, l.policy.Broad, l.policy.Global}
}

func (s *server) loginLimiter() (*authLimiter, error) {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	if s.authLimiter != nil {
		return s.authLimiter, nil
	}
	dir := s.state
	if dir == "" {
		dir = s.config
	}
	if dir == "" {
		return nil, errors.New("auth state directory unavailable")
	}
	parent, err := mutablefs.Canonical(dir)
	path := filepath.Join(parent, "go-login-rate.sqlite3")
	if err != nil {
		return nil, err
	}
	if err = mutablefs.RequireLocal(path); err != nil {
		return nil, err
	}
	// Reject database aliases before SQLite can write through them.
	if info, e := os.Lstat(path); e == nil && info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("auth database symlink")
	}
	if err = preflightAuthDatabase(path); err != nil {
		return nil, err
	}
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	secret, err := atomicfile.LoadOrCreateSecret(filepath.Join(filepath.Dir(path), "go-login-rate-secret"))
	if err != nil {
		return nil, err
	}
	l := &authLimiter{path: path, secret: secret, policy: loadAuthRatePolicy(s.config), now: time.Now, writeGate: make(chan struct{}, 1)}
	db, err := l.open()
	if err != nil {
		return nil, err
	}
	defer db.Close()
	tx, err := db.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	_, err = tx.Exec(`CREATE TABLE IF NOT EXISTS auth_meta (version INTEGER NOT NULL, clock REAL NOT NULL DEFAULT 0);
 INSERT INTO auth_meta SELECT 1,0 WHERE NOT EXISTS(SELECT 1 FROM auth_meta);
 CREATE TABLE IF NOT EXISTS auth_buckets (key TEXT PRIMARY KEY, tokens REAL NOT NULL, updated REAL NOT NULL, seen REAL NOT NULL);
 CREATE TABLE IF NOT EXISTS auth_users (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, cooldown REAL NOT NULL, seen REAL NOT NULL, known INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS auth_claims (id TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);
 CREATE INDEX IF NOT EXISTS auth_claims_username ON auth_claims(username);
 CREATE INDEX IF NOT EXISTS auth_buckets_seen ON auth_buckets(seen);
 CREATE INDEX IF NOT EXISTS auth_users_seen ON auth_users(seen);`)
	if err != nil {
		return nil, err
	}
	var version int
	if err = tx.QueryRow("SELECT version FROM auth_meta").Scan(&version); err != nil || version != 1 {
		return nil, errors.New("auth schema unavailable")
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	s.authLimiter = l
	return l, nil
}

// Inspect compatibility without enabling WAL or repairing schema. An unknown
// nonempty database is not a bootstrap database and must remain untouched.
func preflightAuthDatabase(path string) error {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("auth database must be a regular file")
	}
	if info.Size() == 0 {
		for _, suffix := range []string{"-wal", "-shm"} {
			if _, err := os.Lstat(path + suffix); err == nil || !os.IsNotExist(err) {
				return errors.New("empty auth database has sidecars")
			}
		}
		return nil
	}
	inspectionPath := path
	options := "?mode=ro&immutable=1&_busy_timeout=2000"
	if _, walErr := os.Lstat(path + "-wal"); walErr == nil {
		scratch, err := os.MkdirTemp(filepath.Dir(path), ".auth-preflight-")
		if err != nil {
			return err
		}
		defer os.RemoveAll(scratch)
		inspectionPath = filepath.Join(scratch, "auth.sqlite3")
		for _, suffix := range []string{"", "-wal"} {
			if err := copyAuthPreflightFile(path+suffix, inspectionPath+suffix); err != nil {
				return err
			}
		}
		options = "?mode=ro&_busy_timeout=2000"
	} else if !os.IsNotExist(walErr) {
		return walErr
	}
	db, err := sql.Open("sqlite3", (&url.URL{Scheme: "file", Path: inspectionPath}).String()+options)
	if err != nil {
		return err
	}
	defer db.Close()
	var version, count int
	if err = db.QueryRow("SELECT min(version), count(*) FROM auth_meta").Scan(&version, &count); err != nil {
		return err
	}
	if version != 1 || count != 1 {
		return errors.New("auth schema unavailable")
	}
	return nil
}

func copyAuthPreflightFile(source, destination string) error {
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	size, copyErr := io.Copy(output, io.LimitReader(input, (64<<20)+1))
	closeErr := output.Close()
	if size > 64<<20 {
		return errors.New("auth preflight artifact too large")
	}
	return errors.Join(copyErr, closeErr)
}

func (l *authLimiter) open() (*sql.DB, error) {
	for _, path := range []string{l.path, l.path + "-wal", l.path + "-shm"} {
		info, err := os.Lstat(path)
		if err != nil && !os.IsNotExist(err) {
			return nil, err
		}
		if err == nil && !info.Mode().IsRegular() {
			return nil, errors.New("auth state must be a regular file")
		}
	}
	f, err := os.OpenFile(l.path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err == nil {
		if err = f.Close(); err != nil {
			return nil, err
		}
	} else if !os.IsExist(err) {
		return nil, err
	}
	if err = os.Chmod(l.path, 0600); err != nil {
		return nil, err
	}

	db, err := sql.Open("sqlite3", (&url.URL{Scheme: "file", Path: l.path}).String()+"?mode=rwc&_journal_mode=WAL&_busy_timeout=2000&_txlock=immediate&_synchronous=FULL")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	// SQLite otherwise uses the process umask for credential-state files.
	if err = db.Ping(); err != nil {
		db.Close()
		return nil, err
	}
	if err = os.Chmod(l.path, 0600); err != nil {
		db.Close()
		return nil, err
	}
	return db, nil
}

// A durable high-water clock prevents skewed clients or wall-clock rollback
// from refilling the same elapsed interval twice or shortening cooldowns.
func (l *authLimiter) transactionTime(ctx context.Context, tx *sql.Tx) (float64, error) {
	var previous float64
	if err := tx.QueryRowContext(ctx, "SELECT clock FROM auth_meta").Scan(&previous); err != nil {
		return 0, err
	}
	now := math.Max(previous, float64(l.now().UnixMilli())/1000)
	_, err := tx.ExecContext(ctx, "UPDATE auth_meta SET clock=?", now)
	return now, err
}

func (l *authLimiter) acquireWrite(ctx context.Context) error {
	select {
	case l.writeGate <- struct{}{}:
		if err := ctx.Err(); err != nil {
			<-l.writeGate
			return err
		}
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (l *authLimiter) releaseWrite() {
	<-l.writeGate
}

func authRefill(tokens, updated, now float64, b authBucketPolicy) float64 {
	return math.Min(float64(b.Capacity), tokens+math.Max(0, now-updated)*b.Refill/60)
}

// BEGIN IMMEDIATE linearizes admission across processes. Claims bound hashing
// concurrency; expiry frees abandoned claims without refunding their charge.
func (l *authLimiter) reserve(ctx context.Context, remote, username string, knownAccount ...bool) (*authReservation, int, error) {
	addr, err := peerAddress(remote)
	if err != nil {
		return nil, 503, err
	}
	if len(username) > 1024 {
		return nil, 429, nil
	}
	// One process-local owner avoids SQLITE_BUSY races among connection setup
	// and write transactions; SQLite still arbitrates writers from other processes.
	if err = l.acquireWrite(ctx); err != nil {
		return nil, 503, err
	}
	defer l.releaseWrite()
	db, err := l.open()
	if err != nil {
		return nil, 503, err
	}
	defer db.Close()
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, 503, err
	}
	defer tx.Rollback()
	now, err := l.transactionTime(ctx, tx)
	if err != nil {
		return nil, 503, err
	}
	if _, err = tx.ExecContext(ctx, "DELETE FROM auth_claims WHERE expires <= ?", now); err != nil {
		return nil, 503, err
	}
	// Idle unknown identities must expire so a flood cannot fill the store
	// permanently. Preserve configured-account hard locks, active cooldowns,
	// and identities with in-flight work.
	if _, err = tx.ExecContext(ctx, "DELETE FROM auth_users WHERE (known=0 OR failures < ?) AND cooldown <= ? AND seen < ? AND key NOT IN (SELECT username FROM auth_claims)", l.policy.Ceiling, now, now-21600); err != nil {
		return nil, 503, err
	}
	minRefill := math.Inf(1)
	maxCapacity := 0
	for _, b := range l.buckets() {
		minRefill = math.Min(minRefill, b.Refill/60)
		maxCapacity = max(maxCapacity, b.Capacity)
	}
	if _, err = tx.ExecContext(ctx, "DELETE FROM auth_buckets WHERE seen < ? AND tokens + max(0, ?-updated)*? >= ?", now-21600, now, minRefill, maxCapacity); err != nil {
		return nil, 503, err
	}
	user := l.key("username", username)
	failures := 0
	cooldown := 0.0
	err = tx.QueryRowContext(ctx, "SELECT failures,cooldown FROM auth_users WHERE key=?", user).Scan(&failures, &cooldown)
	if err != nil && err != sql.ErrNoRows {
		return nil, 503, err
	}
	var active, total, rows int
	if err = tx.QueryRowContext(ctx, "SELECT count(*),count(CASE WHEN username=? THEN 1 END) FROM auth_claims", user).Scan(&total, &active); err != nil {
		return nil, 503, err
	}
	if failures >= l.policy.Ceiling || now < cooldown || total >= authMaxInflight || active >= max(1, l.policy.Allowance+1-failures) {
		return nil, 429, nil
	}
	if err = tx.QueryRowContext(ctx, "SELECT (SELECT count(*) FROM auth_users)+(SELECT count(*) FROM auth_buckets)+(SELECT count(*) FROM auth_claims)").Scan(&rows); err != nil {
		return nil, 503, err
	}
	if rows > authMaxRows-5 {
		return nil, 429, nil
	}
	keys := l.networkKeys(addr)
	tokens := make([]float64, len(keys))
	buckets := l.buckets()
	for i, key := range keys {
		value, updated := float64(buckets[i].Capacity), now
		err = tx.QueryRowContext(ctx, "SELECT tokens,updated FROM auth_buckets WHERE key=?", key).Scan(&value, &updated)
		if err != nil && err != sql.ErrNoRows {
			return nil, 503, err
		}
		tokens[i] = authRefill(value, updated, now, buckets[i])
		if tokens[i] < 1 {
			return nil, 429, nil
		}
	}
	for i, key := range keys {
		if _, err = tx.ExecContext(ctx, `INSERT INTO auth_buckets VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET tokens=excluded.tokens,updated=excluded.updated,seen=excluded.seen`, key, tokens[i]-1, now, now); err != nil {
			return nil, 503, err
		}
	}
	idBytes := make([]byte, 16)
	if _, err = rand.Read(idBytes); err != nil {
		return nil, 503, err
	}
	id := hex.EncodeToString(idBytes)
	if _, err = tx.ExecContext(ctx, "INSERT INTO auth_claims VALUES(?,?,?)", id, user, now+authClaimTTL); err != nil {
		return nil, 503, err
	}
	if err = tx.Commit(); err != nil {
		return nil, 503, err
	}
	return &authReservation{id: id, user: user, keys: keys, limiter: l, knownAccount: len(knownAccount) > 0 && knownAccount[0]}, 0, nil
}
func (r *authReservation) finish(ctx context.Context, success bool) error {
	l := r.limiter
	if err := l.acquireWrite(ctx); err != nil {
		return err
	}
	defer l.releaseWrite()
	db, err := l.open()
	if err != nil {
		return err
	}
	defer db.Close()
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, "DELETE FROM auth_claims WHERE id=? AND username=?", r.id, r.user)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected == 0 {
		return errors.New("auth reservation already finalized or expired")
	}
	now, err := l.transactionTime(ctx, tx)
	if err != nil {
		return err
	}
	failures := 0
	cooldown := 0.0
	if !success {
		err = tx.QueryRowContext(ctx, "SELECT failures FROM auth_users WHERE key=?", r.user).Scan(&failures)
		if err != nil && err != sql.ErrNoRows {
			return err
		}
		failures++
		if failures > l.policy.Allowance {
			step := min(failures-l.policy.Allowance-1, len(l.policy.Ladder)-1)
			cooldown = now + float64(l.policy.Ladder[step])
		}
	} else {
		for i, key := range r.keys {
			var value, updated float64
			if err = tx.QueryRowContext(ctx, "SELECT tokens,updated FROM auth_buckets WHERE key=?", key).Scan(&value, &updated); err != nil {
				return err
			}
			tokens := math.Min(float64(l.buckets()[i].Capacity), authRefill(value, updated, now, l.buckets()[i])+1)
			if _, err = tx.ExecContext(ctx, "UPDATE auth_buckets SET tokens=?,updated=?,seen=? WHERE key=?", tokens, now, now, key); err != nil {
				return err
			}
		}
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO auth_users VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,cooldown=excluded.cooldown,seen=excluded.seen,known=max(auth_users.known,excluded.known)`, r.user, failures, cooldown, now, r.knownAccount); err != nil {
		return err
	}
	return tx.Commit()
}
func (s *server) verifyCredentials(rctx context.Context, remote, username, password string, users []authUser) (authUser, int) {
	l, err := s.loginLimiter()
	if err != nil {
		return authUser{}, 503
	}
	ctx, cancel := context.WithTimeout(rctx, 3*time.Second)
	knownAccount := false
	for _, user := range users {
		if user.username == username {
			knownAccount = true
			break
		}
	}
	reservation, status, err := l.reserve(ctx, remote, username, knownAccount)
	cancel()
	if err != nil {
		return authUser{}, 503
	}
	if status != 0 {
		return authUser{}, status
	}
	found := authUser{}
	success := false
	for _, user := range users {
		if user.username == username && passwordMatches(password, user.password) {
			found = user
			success = true
			break
		}
	}
	// Finalization survives client disconnect, but remains bounded on store failure.
	ctx, cancel = context.WithTimeout(context.Background(), 3*time.Second)
	err = reservation.finish(ctx, success)
	cancel()
	if err != nil {
		return authUser{}, 503
	}
	if !success {
		return authUser{}, 401
	}
	return found, 0
}
