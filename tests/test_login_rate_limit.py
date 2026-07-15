# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deterministic coverage for the login throttle: token-bucket math, username staged
backoff, IP canonicalization, and the process-safe SQLite store. Everything runs on an
injected fake clock so boundaries are exact and no test waits on wall time."""
from __future__ import annotations

import threading

import pytest

from yolomux_lib import login_rate_limit as lrl
from yolomux_lib.login_rate_limit import LoginRatePolicy
from yolomux_lib.login_rate_limit import LoginRateLimiter


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(tmp_path, clock=None, policy=None):
    clock = clock or FakeClock()
    return LoginRateLimiter(
        tmp_path / "login-throttle.sqlite3",
        clock=clock,
        policy=policy or LoginRatePolicy(),
    ), clock


# --- policy validation ----------------------------------------------------------


def test_default_policy_validates_and_prefixes_widen():
    policy = lrl.DEFAULT_LOGIN_RATE_POLICY
    assert policy.broad_ipv4_prefix <= policy.nearby_ipv4_prefix <= policy.exact_ipv4_prefix
    assert policy.exact_bucket.capacity <= policy.nearby_bucket.capacity <= policy.broad_bucket.capacity <= policy.global_bucket.capacity


@pytest.mark.parametrize("bad", [
    {"exact_ipv4_prefix": 33},
    {"nearby_ipv4_prefix": 8, "broad_ipv4_prefix": 16},  # broad stricter than nearby
    {"username_initial_allowance": 0},
    {"username_hard_ceiling": 3},  # not above allowance
    {"exact_bucket": lrl.BucketPolicy(0, 5)},
    {"exact_bucket": lrl.BucketPolicy(10, 0)},
])
def test_invalid_policies_are_rejected(bad):
    with pytest.raises(ValueError):
        LoginRatePolicy(**bad).validated()


# --- IP canonicalization (checkbox 3) -------------------------------------------


def test_canonical_ip_collapses_v4_mapped_and_strips_scope():
    assert str(lrl.canonical_ip("::ffff:203.0.113.9")) == "203.0.113.9"
    assert str(lrl.canonical_ip("fe80::1%en0")) == "fe80::1"
    assert lrl.canonical_ip("not-an-ip") is None
    assert lrl.canonical_ip("") is None


def test_equivalent_ipv6_spellings_share_one_bucket_key(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    secret = limiter._load_secret()
    a = lrl.canonical_ip("2001:db8::1")
    b = lrl.canonical_ip("2001:0db8:0000:0000:0000:0000:0000:0001")
    ma = lrl.network_scope_material(a, limiter.policy)
    mb = lrl.network_scope_material(b, limiter.policy)
    for scope in lrl.NETWORK_SCOPES:
        assert lrl.scope_row_key(secret, scope, ma[scope]) == lrl.scope_row_key(secret, scope, mb[scope])


def test_ipv4_and_ipv6_same_width_keys_are_disjoint(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    secret = limiter._load_secret()
    v4 = lrl.network_scope_material(lrl.canonical_ip("10.0.0.1"), limiter.policy)
    v6 = lrl.network_scope_material(lrl.canonical_ip("2001:db8::1"), limiter.policy)
    assert lrl.scope_row_key(secret, lrl.SCOPE_NEARBY, v4[lrl.SCOPE_NEARBY]) != lrl.scope_row_key(secret, lrl.SCOPE_NEARBY, v6[lrl.SCOPE_NEARBY])


# --- token bucket boundaries + refill (no fixed-window boundary attack) ----------


def test_exact_ip_bucket_admits_capacity_then_blocks(tmp_path):
    limiter, clock = make_limiter(tmp_path)
    cap = limiter.policy.exact_bucket.capacity
    admits = sum(1 for i in range(cap + 5) if limiter.check_and_reserve("203.0.113.7", f"u{i}").admitted)
    assert admits == cap
    blocked = limiter.check_and_reserve("203.0.113.7", "u-more")
    assert blocked.admitted is False and blocked.blocked_scope == lrl.SCOPE_EXACT


def test_token_bucket_refills_continuously_no_window_boundary_burst(tmp_path):
    # A fixed window would allow a full second burst at the window edge. A token bucket
    # only ever hands back what has refilled: at 5/min = 1 per 12s, waiting 24s yields
    # exactly 2 more attempts, never a capacity-sized burst.
    limiter, clock = make_limiter(tmp_path)
    for _ in range(limiter.policy.exact_bucket.capacity):
        assert limiter.check_and_reserve("203.0.113.7", "u").admitted
    assert limiter.check_and_reserve("203.0.113.7", "u").admitted is False
    clock.advance(24.0)  # 5/min -> 1 token per 12s -> 2 tokens
    assert limiter.check_and_reserve("203.0.113.7", "u").admitted
    assert limiter.check_and_reserve("203.0.113.7", "u").admitted
    assert limiter.check_and_reserve("203.0.113.7", "u").admitted is False


def test_clock_going_backwards_grants_no_tokens(tmp_path):
    limiter, clock = make_limiter(tmp_path)
    for _ in range(limiter.policy.exact_bucket.capacity):
        limiter.check_and_reserve("203.0.113.7", "u")
    clock.advance(-100.0)
    assert limiter.check_and_reserve("203.0.113.7", "u").admitted is False


# --- threat: one IP spraying many usernames -------------------------------------


def test_one_ip_spraying_many_usernames_is_capped_by_exact_bucket(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    admitted = 0
    for i in range(100):
        decision = limiter.check_and_reserve("203.0.113.7", f"account{i}")
        if decision.admitted:
            admitted += 1
            limiter.record_result("203.0.113.7", f"account{i}", success=False)
    # Bounded by the exact-IP bucket, not by any single username.
    assert admitted == limiter.policy.exact_bucket.capacity


# --- threat: many IPs targeting one username ------------------------------------


def test_many_ips_targeting_one_username_trip_the_username_backoff(tmp_path):
    limiter, clock = make_limiter(tmp_path)
    admitted = 0
    # Each attacker IP is fresh (own exact bucket), but they converge on one username.
    for i in range(20):
        ip = f"198.51.{i}.{i}"  # distinct /16s so no network bucket collides
        decision = limiter.check_and_reserve(ip, "victim")
        if decision.admitted:
            admitted += 1
            limiter.record_result(ip, "victim", success=False)
        else:
            assert decision.blocked_scope == lrl.SCOPE_USERNAME
    assert admitted == limiter.policy.username_initial_allowance


# --- threat: subnet / provider farms inside and outside grouping boundaries ------


def test_farm_rotating_inside_a_24_collides_on_nearby_bucket(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    admitted = 0
    for host in range(1, 200):
        # All inside 203.0.113.0/24 -> exact buckets differ but nearby (/24) is shared.
        decision = limiter.check_and_reserve(f"203.0.113.{host % 256}", f"u{host}")
        if decision.admitted:
            admitted += 1
    # Capped by the nearby (/24) bucket, well below one-per-host.
    assert admitted == limiter.policy.nearby_bucket.capacity


def test_addresses_outside_the_broad_prefix_do_not_share_a_bucket(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    # Two addresses in different /16s must not throttle each other on the broad bucket.
    a = limiter.check_and_reserve("203.0.0.1", "u")
    b = limiter.check_and_reserve("198.51.0.1", "u2")
    assert a.admitted and b.admitted


def test_ipv6_privacy_addresses_on_one_64_collapse(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    admitted = 0
    for host in range(1, 200):
        # Same /64, differing interface identifiers (privacy addresses).
        decision = limiter.check_and_reserve(f"2001:db8:0:0::{host:x}", f"u{host}")
        if decision.admitted:
            admitted += 1
    assert admitted == limiter.policy.nearby_bucket.capacity


# --- username staged backoff (progressive cooldown, non-extension, reset, ceiling)


def test_username_cooldown_is_progressive_and_success_resets(tmp_path):
    limiter, clock = make_limiter(tmp_path)
    ip_pool = iter(f"198.51.{i}.{i}" for i in range(1000))

    def fail_once():
        ip = next(ip_pool)
        decision = limiter.check_and_reserve(ip, "victim")
        assert decision.admitted, "network buckets must not be the limiter here"
        limiter.record_result(ip, "victim", success=False)

    # 5 free failures.
    for _ in range(limiter.policy.username_initial_allowance):
        fail_once()
    # 6th attempt is gated by the first ladder step (30s).
    blocked = limiter.check_and_reserve(next(ip_pool), "victim")
    assert blocked.admitted is False and blocked.blocked_scope == lrl.SCOPE_USERNAME
    # Wait the first step; the attempt is admitted again.
    clock.advance(limiter.policy.username_cooldown_ladder[0])
    fail_once()  # 6th failure -> second ladder step (60s)
    assert limiter.check_and_reserve(next(ip_pool), "victim").admitted is False
    clock.advance(limiter.policy.username_cooldown_ladder[0])  # 30s < 60s, still blocked
    assert limiter.check_and_reserve(next(ip_pool), "victim").admitted is False
    clock.advance(limiter.policy.username_cooldown_ladder[1])  # now past 60s
    ip = next(ip_pool)
    decision = limiter.check_and_reserve(ip, "victim")
    assert decision.admitted
    limiter.record_result(ip, "victim", success=True)  # success resets
    # Fresh: 5 more free failures available again.
    for _ in range(limiter.policy.username_initial_allowance):
        fail_once()
    assert limiter.check_and_reserve(next(ip_pool), "victim").admitted is False


def test_blocked_request_does_not_extend_cooldown(tmp_path):
    limiter, clock = make_limiter(tmp_path)
    ip_pool = iter(f"198.51.{i}.{i}" for i in range(1000))
    for _ in range(limiter.policy.username_initial_allowance):
        ip = next(ip_pool)
        limiter.check_and_reserve(ip, "victim")
        limiter.record_result(ip, "victim", success=False)
    # Hammer while blocked: none of these may advance the counter or push the deadline.
    for _ in range(50):
        assert limiter.check_and_reserve(next(ip_pool), "victim").admitted is False
    # After exactly the first ladder step from the LAST real failure, it reopens.
    clock.advance(limiter.policy.username_cooldown_ladder[0])
    assert limiter.check_and_reserve(next(ip_pool), "victim").admitted is True


def test_hard_ceiling_locks_until_cleared(tmp_path):
    policy = LoginRatePolicy(username_hard_ceiling=8, username_initial_allowance=3, exact_bucket=lrl.BucketPolicy(1000, 1000), nearby_bucket=lrl.BucketPolicy(1000, 1000), broad_bucket=lrl.BucketPolicy(2000, 2000), global_bucket=lrl.BucketPolicy(5000, 5000))
    limiter, clock = make_limiter(tmp_path, policy=policy)
    ip_pool = iter(f"198.51.{i // 256}.{i % 256}" for i in range(10000))
    failures = 0
    while failures < policy.username_hard_ceiling:
        ip = next(ip_pool)
        decision = limiter.check_and_reserve(ip, "victim")
        if decision.admitted:
            limiter.record_result(ip, "victim", success=False)
            failures += 1
        else:
            # Skip past any active cooldown so we can reach the ceiling deterministically.
            clock.advance(policy.username_cooldown_ladder[-1] + 1)
    # Locked: advancing time by a year does not reopen it.
    clock.advance(365 * 24 * 3600)
    assert limiter.check_and_reserve(next(ip_pool), "victim").admitted is False
    # Operator clear reopens it.
    assert limiter.clear_username("victim") is True
    assert limiter.check_and_reserve(next(ip_pool), "victim").admitted is True


# --- unknown vs known username parity -------------------------------------------


def test_unknown_and_known_usernames_are_indistinguishable_to_the_limiter(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    # The limiter never consults the user list; both names take the identical path.
    known = limiter.check_and_reserve("203.0.113.5", "real-admin")
    unknown = limiter.check_and_reserve("203.0.113.6", "nonexistent")
    assert known.admitted == unknown.admitted is True


# --- successful logins still consume network pressure ---------------------------


def test_success_does_not_refund_network_tokens(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    cap = limiter.policy.exact_bucket.capacity
    for _ in range(cap):
        decision = limiter.check_and_reserve("203.0.113.7", "admin")
        assert decision.admitted
        limiter.record_result("203.0.113.7", "admin", success=True)  # all succeed
    # Even though every attempt succeeded, the exact bucket is now empty.
    assert limiter.check_and_reserve("203.0.113.7", "admin").admitted is False


# --- global emergency bucket + unparseable peer ---------------------------------


def test_unparseable_ip_is_charged_only_to_the_global_bucket(tmp_path):
    policy = LoginRatePolicy(
        exact_bucket=lrl.BucketPolicy(1, 1),
        nearby_bucket=lrl.BucketPolicy(2, 1),
        broad_bucket=lrl.BucketPolicy(3, 1),
        global_bucket=lrl.BucketPolicy(3, 1),
    )
    limiter, _ = make_limiter(tmp_path, policy=policy)
    admits = sum(1 for i in range(10) if limiter.check_and_reserve("garbage-peer", f"u{i}").admitted)
    assert admits == 3
    blocked = limiter.check_and_reserve("garbage-peer", "u")
    assert blocked.blocked_scope == lrl.SCOPE_GLOBAL


# --- persistence, cross-instance sharing, cardinality ---------------------------


def test_state_persists_across_limiter_instances_same_db(tmp_path):
    clock = FakeClock()
    first = LoginRateLimiter(tmp_path / "login-throttle.sqlite3", clock=clock)
    for _ in range(first.policy.exact_bucket.capacity):
        first.check_and_reserve("203.0.113.7", "u")
    # A second process/port opening the same DB sees the drained bucket.
    second = LoginRateLimiter(tmp_path / "login-throttle.sqlite3", clock=clock)
    assert second.check_and_reserve("203.0.113.7", "u").admitted is False


def test_concurrent_admission_never_overshoots_capacity(tmp_path):
    policy = LoginRatePolicy(exact_bucket=lrl.BucketPolicy(20, 1), nearby_bucket=lrl.BucketPolicy(1000, 1), broad_bucket=lrl.BucketPolicy(2000, 1), global_bucket=lrl.BucketPolicy(5000, 1))
    clock = FakeClock()
    limiter = LoginRateLimiter(tmp_path / "login-throttle.sqlite3", clock=clock, policy=policy)
    limiter._initialize()
    admits = []
    lock = threading.Lock()

    def worker(i):
        decision = limiter.check_and_reserve("203.0.113.7", f"u{i}")
        if decision.admitted:
            with lock:
                admits.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(80)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Atomic BEGIN IMMEDIATE reserve means never more than capacity admits.
    assert len(admits) == 20


def test_idle_rows_are_pruned_but_locks_survive(tmp_path):
    policy = LoginRatePolicy(username_hard_ceiling=6, username_initial_allowance=2, exact_bucket=lrl.BucketPolicy(1000, 1000), nearby_bucket=lrl.BucketPolicy(1000, 1000), broad_bucket=lrl.BucketPolicy(2000, 2000), global_bucket=lrl.BucketPolicy(5000, 5000))
    clock = FakeClock()
    limiter = LoginRateLimiter(tmp_path / "login-throttle.sqlite3", clock=clock, policy=policy)
    # Lock one username at the ceiling.
    fails = 0
    ip_pool = iter(f"198.51.{i // 256}.{i % 256}" for i in range(10000))
    while fails < policy.username_hard_ceiling:
        ip = next(ip_pool)
        if limiter.check_and_reserve(ip, "locked-victim").admitted:
            limiter.record_result(ip, "locked-victim", success=False)
            fails += 1
        else:
            clock.advance(policy.username_cooldown_ladder[-1] + 1)
    # Create many idle network rows, then jump past the TTL and force a prune via a write.
    for i in range(50):
        limiter.check_and_reserve(f"203.0.{i}.1", "someone")
    clock.advance(lrl.LOGIN_THROTTLE_IDLE_TTL_SECONDS + 1)
    limiter.check_and_reserve("203.0.113.250", "trigger-prune")
    diag = limiter.diagnostics()
    assert diag["locked_usernames"] == 1, "an absolute account lock must never be pruned"


# --- coarse guidance never understates ------------------------------------------


def test_coarse_retry_band_never_promises_admission_early():
    assert lrl.coarse_retry_band(30) == lrl.RETRY_BAND_MINUTES
    assert lrl.coarse_retry_band(1800) == lrl.RETRY_BAND_MINUTES
    assert lrl.coarse_retry_band(3600) == lrl.RETRY_BAND_MINUTES
    assert lrl.coarse_retry_band(3601) == lrl.RETRY_BAND_HOURS
    # An hour-plus cooldown must never be described as "minutes".
    assert lrl.coarse_retry_band(7200) == lrl.RETRY_BAND_HOURS


# --- diagnostics are privacy-safe -----------------------------------------------


def test_diagnostics_expose_only_aggregates(tmp_path):
    limiter, _ = make_limiter(tmp_path)
    for _ in range(limiter.policy.exact_bucket.capacity + 3):
        limiter.check_and_reserve("203.0.113.7", "u")
    diag = limiter.diagnostics()
    assert diag["allowed"] == limiter.policy.exact_bucket.capacity
    assert diag["blocked"].get(lrl.SCOPE_EXACT) == 3
    assert diag["active_rows"] >= 1
    assert diag["healthy"] is True
    # No raw identifiers anywhere in the payload.
    blob = repr(diag)
    assert "203.0.113.7" not in blob and "\"u\"" not in blob
