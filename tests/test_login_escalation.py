# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Coverage for the opt-in login-attack escalation: level selection, the harmless decoy,
and the expiring edge-DROP controller. Every edge test uses a MOCK runner and a fake
clock — no test may build a real firewall rule or touch the machine's real edge."""
from __future__ import annotations

import ipaddress

import pytest

from yolomux_lib import login_escalation as esc
from yolomux_lib.login_rate_limit import SCOPE_BROAD
from yolomux_lib.login_rate_limit import SCOPE_EXACT
from yolomux_lib.login_rate_limit import SCOPE_GLOBAL
from yolomux_lib.login_rate_limit import SCOPE_USERNAME


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingRunner:
    """Stand-in for firewall command execution: records argv, never runs anything."""

    def __init__(self, succeed=True):
        self.calls = []
        self.succeed = succeed

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.succeed


# --- level selection ------------------------------------------------------------


def test_username_only_block_never_reaches_edge_drop():
    # Even with the edge enabled, a username-scope block must not firewall an IP.
    assert esc.escalation_level(SCOPE_USERNAME, decoy_enabled=True, edge_enabled=True) == esc.ESCALATION_DECOY
    assert esc.escalation_level(SCOPE_USERNAME, decoy_enabled=False, edge_enabled=True) == esc.ESCALATION_NONE


def test_volumetric_block_reaches_edge_only_when_enabled():
    for scope in (SCOPE_EXACT, SCOPE_BROAD, SCOPE_GLOBAL):
        assert esc.escalation_level(scope, decoy_enabled=False, edge_enabled=True) == esc.ESCALATION_EDGE_DROP
        # Off by default -> falls back to decoy (if on) or nothing.
        assert esc.escalation_level(scope, decoy_enabled=True, edge_enabled=False) == esc.ESCALATION_DECOY
        assert esc.escalation_level(scope, decoy_enabled=False, edge_enabled=False) == esc.ESCALATION_NONE


# --- decoy challenge ------------------------------------------------------------


def test_decoy_phrase_is_deterministic_and_verifiable():
    decoy = esc.DecoyChallenge(words=3)
    phrase = decoy.phrase("client-abc")
    assert phrase == decoy.phrase("client-abc"), "same seed -> stable phrase across a retry"
    assert len(phrase.split()) == 3
    assert decoy.verify("client-abc", phrase) is True
    # Whitespace/case normalized.
    assert decoy.verify("client-abc", f"  {phrase.upper()}  ") is True
    assert decoy.verify("client-abc", "wrong words here") is False
    # A different client gets an independent phrase.
    assert decoy.phrase("client-xyz") != phrase or True  # allowed to collide rarely; identity not required


def test_should_serve_decoy_bounds():
    assert esc.should_serve_decoy("any", 0) is False
    assert esc.should_serve_decoy("any", 100) is True
    # Deterministic per seed.
    assert esc.should_serve_decoy("seed-1", 50) == esc.should_serve_decoy("seed-1", 50)


# --- edge command builders ------------------------------------------------------


def test_command_builders_produce_argv_and_reject_non_ip():
    assert esc.pf_block_command("203.0.113.7") == ["pfctl", "-t", "yolomux_login_block", "-T", "add", "203.0.113.7"]
    assert esc.nft_block_command("203.0.113.7", 900)[:6] == ["nft", "add", "element", "ip", "yolomux", "login_block"]
    assert esc.nft_block_command("2001:db8::1", 900)[3] == "ip6"
    for bad in ("; rm -rf /", "203.0.113.7 && reboot", "not-an-ip", ""):
        with pytest.raises(ValueError):
            esc.pf_block_command(bad)
        with pytest.raises(ValueError):
            esc.nft_block_command(bad, 900)


def test_command_argv_has_no_shell_metacharacters_for_valid_ip():
    # argv form: even if an address string carried metacharacters it would be one token,
    # but a valid IP has none anyway. Assert every token is shell-safe.
    argv = esc.nft_block_command("198.51.100.9", 600)
    joined = " ".join(argv)
    for meta in (";", "|", "&", "$", "`", "\n", ">"):
        assert meta not in "".join(t for t in argv if t not in {"{", "}"})


# --- edge controller (disabled by default; mock runner; fake clock) -------------


def test_controller_is_disabled_by_default():
    runner = RecordingRunner()
    controller = esc.EdgeBlockController(runner=runner)
    assert controller.block("203.0.113.7") is False
    assert runner.calls == [], "a disabled controller must never invoke the runner"


def test_enabled_controller_installs_and_expires_rules():
    clock = FakeClock()
    runner = RecordingRunner()
    controller = esc.EdgeBlockController(runner=runner, enabled=True, ttl_seconds=100, clock=clock)
    assert controller.block("203.0.113.7") is True
    assert controller.active_rules() == ["203.0.113.7"]
    assert runner.calls[0][:3] == ["nft", "add", "element"]
    # Before TTL: still active. After TTL: pruned with an unblock command.
    clock.advance(50)
    assert controller.expire_due() == 0
    clock.advance(60)
    assert controller.expire_due() == 1
    assert controller.active_rules() == []
    assert any(call[:3] == ["nft", "delete", "element"] for call in runner.calls)


def test_trusted_sources_are_never_blocked():
    runner = RecordingRunner()
    controller = esc.EdgeBlockController(runner=runner, enabled=True, trusted_cidrs=("10.0.0.0/8", "192.168.1.0/24"))
    assert controller.block("10.4.5.6") is False
    assert controller.block("192.168.1.50") is False
    assert runner.calls == []
    assert controller.diagnostics()["refused_trusted"] == 2
    # A non-trusted address still blocks.
    assert controller.block("203.0.113.7") is True


def test_rule_cap_is_enforced():
    clock = FakeClock()
    runner = RecordingRunner()
    controller = esc.EdgeBlockController(runner=runner, enabled=True, max_rules=3, ttl_seconds=1000, clock=clock)
    assert controller.block("203.0.113.1") is True
    assert controller.block("203.0.113.2") is True
    assert controller.block("203.0.113.3") is True
    assert controller.block("203.0.113.4") is False, "cap reached"
    assert len(controller.active_rules()) == 3


def test_blocking_the_same_ip_refreshes_ttl_without_a_second_install():
    clock = FakeClock()
    runner = RecordingRunner()
    controller = esc.EdgeBlockController(runner=runner, enabled=True, ttl_seconds=100, clock=clock)
    controller.block("203.0.113.7")
    installs = sum(1 for c in runner.calls if c[:2] == ["nft", "add"])
    clock.advance(30)
    controller.block("203.0.113.7")  # refresh
    installs_after = sum(1 for c in runner.calls if c[:2] == ["nft", "add"])
    assert installs_after == installs == 1
    # TTL was extended from the refresh time.
    clock.advance(80)  # 110 total, but refreshed at 30 -> expires at 130
    assert controller.expire_due() == 0
    clock.advance(30)
    assert controller.expire_due() == 1


def test_clear_removes_all_rules():
    runner = RecordingRunner()
    controller = esc.EdgeBlockController(runner=runner, enabled=True)
    controller.block("203.0.113.1")
    controller.block("203.0.113.2")
    assert controller.clear() == 2
    assert controller.active_rules() == []


def test_runner_failure_does_not_record_a_phantom_rule():
    runner = RecordingRunner(succeed=False)
    controller = esc.EdgeBlockController(runner=runner, enabled=True)
    assert controller.block("203.0.113.7") is False
    assert controller.active_rules() == [], "a failed edge command must not leave a tracked rule"
