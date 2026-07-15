# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Process-safe login throttling for every password path.

WHY THIS EXISTS
Both password entry points — the browser `POST /login` form and HTTP Basic auth —
funnel through one verifier (`auth_identity_for_credentials`) that runs PBKDF2 for
every configured user on every attempt. Without a limiter, an attacker can brute
force, credential-stuff, password-spray, or simply burn the server's password-hash
CPU. This module is the admission gate that runs BEFORE that verifier and the record
step that runs after, shared by both paths and by every YOLOmux process/port that
points at the same state directory.

THE TWO PRIMITIVES (see docs/DEVELOPMENT.md for the full incident/threat writeup)
Different threats need different math, so there are exactly two, both O(1) per key:

1. Token bucket, for the four NETWORK scopes (exact IP, nearby prefix, broad prefix,
   and a single global emergency key). State per key is just (tokens, updated_at):
   tokens refill continuously at `refill_per_minute` up to `capacity`. Every
   credential-bearing attempt — including successful logins — consumes one token from
   each applicable network bucket; a success never refunds network pressure. If ANY
   applicable bucket is empty the attempt is blocked (and consumes nothing, so the
   buckets keep refilling on their own). Hierarchical prefixes (/32+/24+/16 for IPv4,
   /128+/64+/48 for IPv6) mean rotating addresses inside a subnet or provider block
   still collides on the coarser bucket, while the coarser buckets are deliberately
   looser so corporate-NAT / CGNAT / VPN users are not locked out with one bad
   neighbor.

2. Staged backoff, for the USERNAME scope, keyed by HMAC of the exact submitted
   identifier so known and unknown usernames behave identically. State is
   (consecutive_failures, cooldown_until, locked). The counter advances ONLY after a
   completed failed verification and resets on success (NIST SP 800-63B). After
   `username_initial_allowance` failures each further failure imposes an escalating
   cooldown (30s, 60s, 2m, 5m, 10m, 30m, 1h). A request blocked while a cooldown is
   already active does NOT advance the counter or extend the cooldown, so an attacker
   cannot pin a victim's account locked forever by hammering it. At the NIST upper
   bound of `username_hard_ceiling` consecutive failures the identifier is locked
   until its credential changes or an operator clears it.

WHY TOKEN BUCKET (algorithm selection, DOIT checkbox 1)
Compared against the bounded alternatives the request named:
  - Fixed window: rejected. Allows a 2x burst straddling the window boundary for the
    same storage cost; a token bucket refills continuously and has no boundary.
  - Sliding-window log: rejected. Storing per-attempt timestamps is O(N) rows per
    active key — unbounded growth exactly under the distributed attack we defend
    against. A token bucket is two numbers per key.
  - GCRA / leaky bucket: equivalent (single theoretical-arrival-time value, also
    O(1)). Token bucket chosen for legibility and a 1:1 mapping to the documented
    capacity/refill policy table.
  - ASN / IP-reputation database: rejected. Needs an external, updating dataset with
    privacy, offline, and failure concerns; fixed prefixes need no external data, give
    bounded O(1) key derivation, and directly cover subnet/provider address rotation.

SECURITY INVARIANTS
  - The limiter check is cheap (a couple of indexed SQLite point reads) and always
    runs before PBKDF2, so a blocked attempt costs no password hash.
  - Rows are keyed by HMAC(scope || key) under a mode-0600 secret; raw usernames and
    IPs are never stored, so the database cannot be used for offline enumeration.
  - A limiter-store failure fails CLOSED for remote password attempts (generic bounded
    response), never open to unlimited hashing — wired at the call sites.
  - Responses never reveal which bucket fired, whether the username exists, attempts
    used/remaining, or a precise reset time (see `coarse_retry_band`).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterator

from .atomic_file import load_or_create_secret_key


# --- Centrally owned, validated policy defaults ---------------------------------
# Every number the limiter uses lives here (overridable via auth.yaml, validated on
# load); nothing is a scattered literal at a call site.


@dataclass(frozen=True)
class BucketPolicy:
    """A single token bucket: an initial burst `capacity` that refills at
    `refill_per_minute` tokens/minute. One token is spent per credential-bearing
    attempt on this scope."""

    capacity: int
    refill_per_minute: float

    def validated(self, label: str) -> "BucketPolicy":
        if self.capacity < 1:
            raise ValueError(f"{label}: capacity must be >= 1, got {self.capacity}")
        if self.refill_per_minute <= 0:
            raise ValueError(f"{label}: refill_per_minute must be > 0, got {self.refill_per_minute}")
        return self

    @property
    def refill_per_second(self) -> float:
        return self.refill_per_minute / 60.0


# The staged username cooldown ladder, in seconds: the wait imposed on the 1st, 2nd,
# ... failure PAST the initial allowance. The last value is the sustained maximum.
USERNAME_COOLDOWN_LADDER_SECONDS: tuple[int, ...] = (30, 60, 120, 300, 600, 1800, 3600)


@dataclass(frozen=True)
class LoginRatePolicy:
    """The complete login-throttling policy. Prefix widths are progressively broader
    and their buckets progressively looser: an exact-IP-strict threshold on a /16 or
    /48 would lock out entire corporate-NAT/CGNAT/VPN/campus populations."""

    # IPv4 / IPv6 prefix widths for the three network scopes.
    exact_ipv4_prefix: int = 32
    exact_ipv6_prefix: int = 128
    nearby_ipv4_prefix: int = 24
    nearby_ipv6_prefix: int = 64
    broad_ipv4_prefix: int = 16
    broad_ipv6_prefix: int = 48

    # Token buckets per network scope.
    exact_bucket: BucketPolicy = BucketPolicy(10, 5)
    nearby_bucket: BucketPolicy = BucketPolicy(50, 25)
    broad_bucket: BucketPolicy = BucketPolicy(200, 100)
    # High enough that it only bites during a genuinely distributed flood; it exists to
    # bound total password-hash CPU, not to be the normal limiter.
    global_bucket: BucketPolicy = BucketPolicy(1000, 500)

    # Username staged backoff.
    username_initial_allowance: int = 5
    username_cooldown_ladder: tuple[int, ...] = USERNAME_COOLDOWN_LADDER_SECONDS
    username_hard_ceiling: int = 100

    def validated(self) -> "LoginRatePolicy":
        for width, lo, hi, label in (
            (self.exact_ipv4_prefix, 1, 32, "exact_ipv4_prefix"),
            (self.nearby_ipv4_prefix, 1, 32, "nearby_ipv4_prefix"),
            (self.broad_ipv4_prefix, 1, 32, "broad_ipv4_prefix"),
            (self.exact_ipv6_prefix, 1, 128, "exact_ipv6_prefix"),
            (self.nearby_ipv6_prefix, 1, 128, "nearby_ipv6_prefix"),
            (self.broad_ipv6_prefix, 1, 128, "broad_ipv6_prefix"),
        ):
            if not lo <= width <= hi:
                raise ValueError(f"{label}: must be in [{lo}, {hi}], got {width}")
        # Broader scope must not be numerically stricter than a narrower one, or the
        # progressively-looser guarantee (and collateral-lockout protection) breaks.
        if not self.broad_ipv4_prefix <= self.nearby_ipv4_prefix <= self.exact_ipv4_prefix:
            raise ValueError("IPv4 prefixes must widen exact >= nearby >= broad")
        if not self.broad_ipv6_prefix <= self.nearby_ipv6_prefix <= self.exact_ipv6_prefix:
            raise ValueError("IPv6 prefixes must widen exact >= nearby >= broad")
        self.exact_bucket.validated("exact_bucket")
        self.nearby_bucket.validated("nearby_bucket")
        self.broad_bucket.validated("broad_bucket")
        self.global_bucket.validated("global_bucket")
        if not self.exact_bucket.capacity <= self.nearby_bucket.capacity <= self.broad_bucket.capacity <= self.global_bucket.capacity:
            raise ValueError("network bucket capacities must not shrink at broader scope")
        if self.username_initial_allowance < 1:
            raise ValueError(f"username_initial_allowance must be >= 1, got {self.username_initial_allowance}")
        if not self.username_cooldown_ladder or any(step <= 0 for step in self.username_cooldown_ladder):
            raise ValueError("username_cooldown_ladder must be non-empty positive seconds")
        if list(self.username_cooldown_ladder) != sorted(self.username_cooldown_ladder):
            raise ValueError("username_cooldown_ladder must be non-decreasing")
        if self.username_hard_ceiling <= self.username_initial_allowance:
            raise ValueError("username_hard_ceiling must exceed username_initial_allowance")
        return self


DEFAULT_LOGIN_RATE_POLICY = LoginRatePolicy().validated()


# --- Network scope key derivation (IP canonicalization) -------------------------

# Scope names are stable identifiers used as the SQLite row key prefix and as the
# diagnostics aggregation label. Do not rename without a migration.
SCOPE_EXACT = "ip_exact"
SCOPE_NEARBY = "ip_nearby"
SCOPE_BROAD = "ip_broad"
SCOPE_GLOBAL = "global"
SCOPE_USERNAME = "username"
NETWORK_SCOPES = (SCOPE_EXACT, SCOPE_NEARBY, SCOPE_BROAD, SCOPE_GLOBAL)

# One fixed key for the global emergency bucket (there is exactly one per state dir).
GLOBAL_KEY_MATERIAL = b"login-rate-limit:global"


def canonical_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Canonicalize a socket-peer address string to an `ipaddress` object, or None if
    it is not a usable IP. Strips an IPv6 scope id (`fe80::1%en0`), and collapses an
    IPv4-mapped IPv6 address (`::ffff:1.2.3.4`) to plain IPv4 so equivalent spellings
    of the same host share one bucket. Does NOT try to unwrap NAT64 or other embedded
    forms — those are treated as the IPv6 addresses they are."""
    text = str(raw or "").strip()
    if not text:
        return None
    # A socket peer may arrive as "addr%scope"; the scope id is link-local routing
    # metadata, not part of host identity.
    text = text.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _prefix_material(addr: ipaddress.IPv4Address | ipaddress.IPv6Address, v4_width: int, v6_width: int) -> bytes:
    """The packed network address for `addr` masked to the scope width. Using the
    packed network bytes (not text) means every equivalent spelling of the same IPv6
    network collides on one key, and the version byte prefix keeps IPv4 /24 and IPv6
    /24 keys disjoint."""
    width = v4_width if addr.version == 4 else v6_width
    network = ipaddress.ip_network(f"{addr}/{width}", strict=False)
    return bytes([addr.version]) + int(width).to_bytes(1, "big") + network.network_address.packed


def network_scope_material(addr: ipaddress.IPv4Address | ipaddress.IPv6Address, policy: LoginRatePolicy) -> dict[str, bytes]:
    """Pre-HMAC key material for each network scope for one client address. The caller
    HMACs these under the secret key before use as a row key, so raw addresses never
    reach storage."""
    return {
        SCOPE_EXACT: _prefix_material(addr, policy.exact_ipv4_prefix, policy.exact_ipv6_prefix),
        SCOPE_NEARBY: _prefix_material(addr, policy.nearby_ipv4_prefix, policy.nearby_ipv6_prefix),
        SCOPE_BROAD: _prefix_material(addr, policy.broad_ipv4_prefix, policy.broad_ipv6_prefix),
        SCOPE_GLOBAL: GLOBAL_KEY_MATERIAL,
    }


def scope_row_key(secret: bytes, scope: str, material: bytes) -> str:
    """Deterministic, non-reversible row key: HMAC-SHA256(secret, scope || material).
    The scope is mixed in so the same IP cannot collide across scopes."""
    return hmac.new(secret, scope.encode("ascii") + b"\x00" + material, hashlib.sha256).hexdigest()


def username_row_key(secret: bytes, username: str) -> str:
    """Row key for the username staged-backoff bucket. HMAC of the EXACT submitted
    identifier (not normalized) so the key is stable per submission and reveals
    nothing about whether the account exists."""
    return hmac.new(secret, SCOPE_USERNAME.encode("ascii") + b"\x00" + username.encode("utf-8"), hashlib.sha256).hexdigest()


# --- Pure token-bucket math -----------------------------------------------------


def refilled_tokens(tokens: float, updated_at: float, now: float, bucket: BucketPolicy) -> float:
    """Tokens available at `now` given the stored level and timestamp. Clock going
    backwards (suspend/NTP correction) never grants tokens and never removes them."""
    elapsed = max(0.0, now - updated_at)
    return min(float(bucket.capacity), tokens + elapsed * bucket.refill_per_second)


def bucket_allows(tokens: float, updated_at: float, now: float, bucket: BucketPolicy) -> bool:
    """Whether one token is available now (does not mutate)."""
    return refilled_tokens(tokens, updated_at, now, bucket) >= 1.0


def bucket_after_consume(tokens: float, updated_at: float, now: float, bucket: BucketPolicy) -> tuple[float, float]:
    """New (tokens, updated_at) after spending one token. Callers only invoke this
    once admission across ALL applicable buckets has been decided, so a blocked
    attempt consumes nothing and every bucket keeps refilling."""
    available = refilled_tokens(tokens, updated_at, now, bucket)
    return (available - 1.0, now)


# --- Pure username staged-backoff logic -----------------------------------------


@dataclass(frozen=True)
class UsernameState:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    locked: bool = False


def username_blocked(state: UsernameState, now: float) -> bool:
    """True if this identifier may not attempt right now: either permanently locked at
    the hard ceiling, or inside an active escalating cooldown."""
    return state.locked or now < state.cooldown_until


def username_after_failure(state: UsernameState, now: float, policy: LoginRatePolicy) -> UsernameState:
    """Advance the staged backoff after a COMPLETED failed verification. Only call this
    for an attempt that was admitted and actually hashed to a wrong credential — never
    for a request rejected at admission (that must not extend the cooldown)."""
    failures = state.consecutive_failures + 1
    if failures >= policy.username_hard_ceiling:
        # NIST upper bound: stop verifying this identifier until its credential changes
        # or an operator clears it.
        return UsernameState(consecutive_failures=failures, cooldown_until=now, locked=True)
    if failures < policy.username_initial_allowance:
        # Still within the free-allowance failures; no cooldown yet.
        return UsernameState(consecutive_failures=failures, cooldown_until=0.0, locked=False)
    # The failure that reaches the allowance imposes the first ladder step, so the NEXT
    # attempt must wait; each further failure escalates. "5 failed verifications then 30s"
    # means the 6th attempt is gated, not that a 6th verification runs first.
    ladder = policy.username_cooldown_ladder
    step = ladder[min(failures - policy.username_initial_allowance, len(ladder) - 1)]
    return UsernameState(consecutive_failures=failures, cooldown_until=now + step, locked=False)


def username_after_success() -> UsernameState:
    """Reset on successful authentication (NIST): clears failures and any cooldown. A
    hard lock is never reached on success because success cannot follow a lock."""
    return UsernameState()


# --- Fact-free client guidance --------------------------------------------------

RETRY_BAND_MINUTES = "minutes"
RETRY_BAND_HOURS = "hours"


def coarse_retry_band(cooldown_seconds: float) -> str:
    """Map a real cooldown to a coarse, non-scheduler-friendly band for the user-facing
    message. Never returns a band that would promise admission before the real cooldown
    elapses: anything at or over ~a few minutes rounds UP to the hours band only when it
    truly exceeds an hour, otherwise minutes. Callers must not surface exact seconds,
    the firing bucket, or attempts remaining."""
    if cooldown_seconds > 3600:
        return RETRY_BAND_HOURS
    return RETRY_BAND_MINUTES


# --- Admission decision ---------------------------------------------------------


@dataclass(frozen=True)
class AdmissionDecision:
    """Outcome of a pre-verification admission check. `retry_band` is a coarse
    "minutes"/"hours" hint for the generic message ONLY; the firing scope is kept
    server-side for diagnostics and never leaves the process."""

    admitted: bool
    retry_band: str = ""
    blocked_scope: str = ""
    degraded: bool = False


ADMIT = AdmissionDecision(admitted=True)


# --- Process-safe SQLite-backed limiter -----------------------------------------

LOGIN_THROTTLE_DATABASE_NAME = "login-throttle.sqlite3"
LOGIN_THROTTLE_SECRET_NAME = "login-throttle.key"
LOGIN_THROTTLE_SCHEMA_VERSION = 1
LOGIN_THROTTLE_BUSY_TIMEOUT_MS = 5000
# A network row is idle-evictable once its bucket has fully refilled; a username row
# once it carries no failures and no lock. Idle rows older than this are pruned lazily
# so the table cannot grow without bound under a distributed attack.
LOGIN_THROTTLE_IDLE_TTL_SECONDS = 6 * 3600
# Absolute backstop on stored rows. When exceeded, the oldest idle rows are evicted
# first; a locked-username row (an absolute account lock) is NEVER evicted this way.
LOGIN_THROTTLE_ROW_CEILING = 50_000


class LoginRateLimiterError(Exception):
    """Raised only for unrecoverable schema problems; transient store failures fail
    closed via AdmissionDecision(degraded=True) instead of raising into the request."""


class LoginRateLimiter:
    """One shared, process-safe login throttle backed by a private WAL SQLite file.

    Every YOLOmux port that points at the same state directory opens the same database,
    so the policy holds across ports. Admission is an atomic BEGIN IMMEDIATE
    check-and-reserve so two concurrent processes can never both spend the last token.

    Wall-clock time is used (injectable for tests) because token refill and username
    cooldowns must agree across processes and survive restart, where a per-process
    monotonic clock cannot.
    """

    def __init__(
        self,
        database_path: Path | str,
        *,
        secret_path: Path | str | None = None,
        policy: LoginRatePolicy = DEFAULT_LOGIN_RATE_POLICY,
        clock: Callable[[], float] = time.time,
        busy_timeout_ms: int = LOGIN_THROTTLE_BUSY_TIMEOUT_MS,
    ) -> None:
        self.path = Path(database_path)
        self.secret_path = Path(secret_path) if secret_path is not None else self.path.with_name(LOGIN_THROTTLE_SECRET_NAME)
        self.policy = policy.validated()
        self.clock = clock
        self.busy_timeout_ms = busy_timeout_ms
        self._secret: bytes | None = None
        self._initialized = False
        self._initialize_lock = threading.Lock()
        self._diag_lock = threading.Lock()
        # Per-process counters; the durable active-row count is queried from the DB.
        self._allowed = 0
        self._blocked_by_scope: dict[str, int] = {}
        self._degraded_events = 0
        self._decision_seconds_total = 0.0
        self._decision_count = 0
        self._decision_seconds_max = 0.0

    # --- secret / connection ---

    def _load_secret(self) -> bytes:
        if self._secret is None:
            self._secret = load_or_create_secret_key(self.secret_path)
        return self._secret

    def _raw_connection(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        for attempt in range(6):
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                    raise
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def _initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            connection: sqlite3.Connection | None = None
            try:
                connection = self._raw_connection()
                self._enable_wal(connection)
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.execute("BEGIN IMMEDIATE")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > LOGIN_THROTTLE_SCHEMA_VERSION:
                    raise LoginRateLimiterError(
                        f"login throttle schema {version} is newer than supported {LOGIN_THROTTLE_SCHEMA_VERSION}"
                    )
                if version == 0:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS login_buckets (
                            scope TEXT NOT NULL,
                            row_key TEXT NOT NULL,
                            tokens REAL NOT NULL DEFAULT 0,
                            updated_at REAL NOT NULL DEFAULT 0,
                            failures INTEGER NOT NULL DEFAULT 0,
                            cooldown_until REAL NOT NULL DEFAULT 0,
                            locked INTEGER NOT NULL DEFAULT 0,
                            seen_at REAL NOT NULL DEFAULT 0,
                            PRIMARY KEY (scope, row_key)
                        )
                        """
                    )
                    # Idle-eviction scans by seen_at; keep it indexed so pruning stays cheap.
                    connection.execute("CREATE INDEX IF NOT EXISTS login_buckets_seen_at ON login_buckets (seen_at)")
                    connection.execute(f"PRAGMA user_version = {LOGIN_THROTTLE_SCHEMA_VERSION}")
                connection.execute("COMMIT")
                self._initialized = True
            except sqlite3.DatabaseError as error:
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise LoginRateLimiterError(f"cannot initialize login throttle database: {error}") from error
            finally:
                if connection is not None:
                    connection.close()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._initialize()
        connection = self._raw_connection()
        try:
            yield connection
        finally:
            connection.close()

    # --- diagnostics bookkeeping ---

    def _note_decision(self, seconds: float, admitted: bool, blocked_scope: str, degraded: bool) -> None:
        with self._diag_lock:
            self._decision_seconds_total += seconds
            self._decision_count += 1
            self._decision_seconds_max = max(self._decision_seconds_max, seconds)
            if degraded:
                self._degraded_events += 1
            if admitted:
                self._allowed += 1
            elif blocked_scope:
                self._blocked_by_scope[blocked_scope] = self._blocked_by_scope.get(blocked_scope, 0) + 1

    # --- row helpers ---

    @staticmethod
    def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        return {key: row[key] for key in row.keys()}

    def _prune(self, connection: sqlite3.Connection, now: float) -> None:
        """Lazy bounded cleanup, run inside the caller's write transaction. Removes rows
        that carry no live state and have been idle past the TTL, then enforces the
        absolute ceiling by evicting the oldest idle rows. A locked account row is never
        pruned (its lock must persist until credential change or operator clear)."""
        idle_predicate = (
            "(locked = 0 AND failures = 0 AND cooldown_until <= ? "
            "AND (tokens >= ? OR updated_at <= ?))"
        )
        # A network bucket is "idle" once refilled to its largest capacity; use the
        # global capacity as a safe upper bound so we never prune a still-throttled row.
        full = float(self.policy.global_bucket.capacity)
        stale_before = now - LOGIN_THROTTLE_IDLE_TTL_SECONDS
        connection.execute(
            f"DELETE FROM login_buckets WHERE seen_at <= ? AND {idle_predicate}",
            (stale_before, now, full, stale_before),
        )
        count = int(connection.execute("SELECT COUNT(*) FROM login_buckets").fetchone()[0])
        if count <= LOGIN_THROTTLE_ROW_CEILING:
            return
        overflow = count - LOGIN_THROTTLE_ROW_CEILING
        connection.execute(
            """
            DELETE FROM login_buckets WHERE rowid IN (
                SELECT rowid FROM login_buckets
                WHERE locked = 0 AND failures = 0 AND cooldown_until <= ?
                ORDER BY seen_at ASC LIMIT ?
            )
            """,
            (now, overflow),
        )

    # --- public admission / record API ---

    def check_and_reserve(self, client_ip: str, username: str) -> AdmissionDecision:
        """Decide whether this password attempt may proceed to verification, and if so
        atomically consume one token from every applicable network bucket. Returns
        AdmissionDecision(admitted=False, ...) when any network bucket is empty or the
        username is locked/in-cooldown — in which case NOTHING is consumed and the
        username counter is untouched. On a store failure, fails CLOSED (admitted=False,
        degraded=True). Never raises into the request path."""
        started = time.perf_counter()
        now = float(self.clock())
        addr = canonical_ip(client_ip)
        secret = self._load_secret()

        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    decision = self._decide_locked(connection, addr, username, secret, now)
                    if decision.admitted:
                        connection.execute("COMMIT")
                    else:
                        connection.execute("ROLLBACK")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
        except sqlite3.DatabaseError:
            decision = AdmissionDecision(admitted=False, retry_band=RETRY_BAND_MINUTES, blocked_scope="", degraded=True)

        self._note_decision(time.perf_counter() - started, decision.admitted, decision.blocked_scope, decision.degraded)
        return decision

    def _decide_locked(
        self,
        connection: sqlite3.Connection,
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address | None,
        username: str,
        secret: bytes,
        now: float,
    ) -> AdmissionDecision:
        # 1. Username lock / cooldown (advances only in record_result, never here).
        username_key = username_row_key(secret, username)
        username_row = self._row_dict(
            connection.execute(
                "SELECT * FROM login_buckets WHERE scope = ? AND row_key = ?",
                (SCOPE_USERNAME, username_key),
            ).fetchone()
        )
        if username_row:
            state = UsernameState(
                consecutive_failures=int(username_row["failures"]),
                cooldown_until=float(username_row["cooldown_until"]),
                locked=bool(username_row["locked"]),
            )
            if username_blocked(state, now):
                band = RETRY_BAND_HOURS if state.locked else coarse_retry_band(state.cooldown_until - now)
                return AdmissionDecision(admitted=False, retry_band=band, blocked_scope=SCOPE_USERNAME)

        # 2. Network buckets. An unparseable peer address cannot be keyed to a prefix, so
        #    it is charged only against the global emergency bucket (never unlimited).
        if addr is None:
            materials = {SCOPE_GLOBAL: GLOBAL_KEY_MATERIAL}
        else:
            materials = network_scope_material(addr, self.policy)
        bucket_for = {
            SCOPE_EXACT: self.policy.exact_bucket,
            SCOPE_NEARBY: self.policy.nearby_bucket,
            SCOPE_BROAD: self.policy.broad_bucket,
            SCOPE_GLOBAL: self.policy.global_bucket,
        }
        loaded: dict[str, tuple[str, float, float]] = {}
        for scope, material in materials.items():
            bucket = bucket_for[scope]
            row_key = scope_row_key(secret, scope, material)
            row = self._row_dict(
                connection.execute(
                    "SELECT tokens, updated_at FROM login_buckets WHERE scope = ? AND row_key = ?",
                    (scope, row_key),
                ).fetchone()
            )
            tokens = float(row["tokens"]) if row else float(bucket.capacity)
            updated_at = float(row["updated_at"]) if row else now
            if not bucket_allows(tokens, updated_at, now, bucket):
                return AdmissionDecision(admitted=False, retry_band=RETRY_BAND_MINUTES, blocked_scope=scope)
            loaded[scope] = (row_key, tokens, updated_at)

        # 3. All buckets pass: consume one token from each network bucket atomically.
        for scope, (row_key, tokens, updated_at) in loaded.items():
            new_tokens, new_updated = bucket_after_consume(tokens, updated_at, now, bucket_for[scope])
            connection.execute(
                """
                INSERT INTO login_buckets (scope, row_key, tokens, updated_at, seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, row_key) DO UPDATE SET tokens = excluded.tokens, updated_at = excluded.updated_at, seen_at = excluded.seen_at
                """,
                (scope, row_key, new_tokens, new_updated, now),
            )
        self._prune(connection, now)
        return ADMIT

    def record_result(self, client_ip: str, username: str, success: bool) -> None:
        """Record the outcome of an ADMITTED attempt (one that reached verification).
        Success resets the username's staged backoff; failure advances it. Network
        buckets were already charged at admission and are untouched here. Never called
        for a request rejected at admission, so a blocked attempt cannot advance the
        username counter. Swallows store failures (best-effort accounting; admission
        already fails closed on a broken store)."""
        now = float(self.clock())
        secret = self._load_secret()
        username_key = username_row_key(secret, username)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = self._row_dict(
                        connection.execute(
                            "SELECT * FROM login_buckets WHERE scope = ? AND row_key = ?",
                            (SCOPE_USERNAME, username_key),
                        ).fetchone()
                    )
                    state = UsernameState(
                        consecutive_failures=int(row["failures"]) if row else 0,
                        cooldown_until=float(row["cooldown_until"]) if row else 0.0,
                        locked=bool(row["locked"]) if row else False,
                    )
                    if success:
                        new_state = username_after_success()
                    else:
                        new_state = username_after_failure(state, now, self.policy)
                    connection.execute(
                        """
                        INSERT INTO login_buckets (scope, row_key, failures, cooldown_until, locked, seen_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(scope, row_key) DO UPDATE SET failures = excluded.failures, cooldown_until = excluded.cooldown_until, locked = excluded.locked, seen_at = excluded.seen_at
                        """,
                        (SCOPE_USERNAME, username_key, new_state.consecutive_failures, new_state.cooldown_until, 1 if new_state.locked else 0, now),
                    )
                    connection.execute("COMMIT")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
        except sqlite3.DatabaseError:
            with self._diag_lock:
                self._degraded_events += 1

    def clear_username(self, username: str) -> bool:
        """Operator recovery: drop a username's staged-backoff row (including an absolute
        lock). Returns True if a row was removed."""
        secret = self._load_secret()
        username_key = username_row_key(secret, username)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "DELETE FROM login_buckets WHERE scope = ? AND row_key = ?",
                    (SCOPE_USERNAME, username_key),
                )
                connection.execute("COMMIT")
                return cursor.rowcount > 0
        except sqlite3.DatabaseError:
            return False

    def diagnostics(self) -> dict[str, Any]:
        """Privacy-safe aggregates for the admin System panel: no raw usernames, IPs, or
        HMAC keys — only counts, active-row totals, and decision latency."""
        active_rows = 0
        locked_usernames = 0
        try:
            with self._connection() as connection:
                active_rows = int(connection.execute("SELECT COUNT(*) FROM login_buckets").fetchone()[0])
                locked_usernames = int(
                    connection.execute("SELECT COUNT(*) FROM login_buckets WHERE scope = ? AND locked = 1", (SCOPE_USERNAME,)).fetchone()[0]
                )
                healthy = True
        except sqlite3.DatabaseError:
            healthy = False
        with self._diag_lock:
            avg_ms = (self._decision_seconds_total / self._decision_count * 1000.0) if self._decision_count else 0.0
            return {
                "schema_version": LOGIN_THROTTLE_SCHEMA_VERSION,
                "healthy": healthy,
                "allowed": self._allowed,
                "blocked": dict(self._blocked_by_scope),
                "blocked_total": sum(self._blocked_by_scope.values()),
                "degraded_events": self._degraded_events,
                "active_rows": active_rows,
                "locked_usernames": locked_usernames,
                "decision_ms_avg": round(avg_ms, 3),
                "decision_ms_max": round(self._decision_seconds_max * 1000.0, 3),
                "decisions": self._decision_count,
            }
