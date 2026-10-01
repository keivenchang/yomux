# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded, opt-in login-attack response escalation (defense in depth, NOT the core).

The core defense is the generic 429 in login_rate_limit; this module adds two optional,
off-by-default upper levels for higher-confidence automation and volumetric floods:

  Level 1  Generic coarse 429                     (login_rate_limit — always on)
  Level 2  Randomized harmless decoy challenge    (DecoyChallenge — opt-in)
  Level 3  Expiring firewall / proxy DROP edge    (EdgeBlockController — opt-in, off)

Hard rules baked in here:
  - The decoy asks the client to type a harmless displayed phrase. It NEVER accepts,
    retains, logs, or verifies a username/password, never calls PBKDF2, never mutates
    success state, and never issues an auth cookie. It is a tarpit, not a fake login.
  - Edge DROP is applied ONLY for volumetric exact-IP/prefix sources, NEVER on a
    username bucket alone (a botnet could otherwise weaponize the server to firewall
    arbitrary innocent IPs while targeting one victim account).
  - Edge rules are built as argv lists (no shell string, so no command injection),
    target a validated IP/network only, carry a strict TTL, are capped in count, exclude
    trusted sources, and expose a local list/clear path. The controller is DISABLED by
    default and takes an injected command runner, so tests and normal runs never touch
    the real firewall.
"""

from __future__ import annotations

import hashlib
import ipaddress
import subprocess
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Callable

from .login_rate_limit import SCOPE_BROAD
from .login_rate_limit import SCOPE_EXACT
from .login_rate_limit import SCOPE_GLOBAL
from .login_rate_limit import SCOPE_NEARBY
from .login_rate_limit import SCOPE_USERNAME

# Escalation levels, most severe last.
ESCALATION_NONE = "none"
ESCALATION_DECOY = "decoy"
ESCALATION_EDGE_DROP = "edge_drop"

# Network scopes whose exhaustion signals a volumetric source worth an edge DROP. The
# username scope is deliberately absent: a username lock alone must never firewall an IP.
VOLUMETRIC_SCOPES = frozenset({SCOPE_EXACT, SCOPE_NEARBY, SCOPE_BROAD, SCOPE_GLOBAL})


def escalation_level(blocked_scope: str, *, decoy_enabled: bool, edge_enabled: bool) -> str:
    """Pick the response level for a blocked attempt. A username-only block never rises
    above the decoy (and only when decoys are enabled); an edge DROP requires a
    volumetric network/global scope AND the edge controller being enabled."""
    if edge_enabled and blocked_scope in VOLUMETRIC_SCOPES and blocked_scope != SCOPE_USERNAME:
        return ESCALATION_EDGE_DROP
    if decoy_enabled and blocked_scope:
        return ESCALATION_DECOY
    return ESCALATION_NONE


# --- Level 2: harmless decoy challenge -----------------------------------------

# A small, inoffensive word pool. The challenge is "type these words"; it carries no
# secret and grants nothing, so the pool need not be large or unpredictable.
_DECOY_WORDS = (
    "river", "maple", "pebble", "lantern", "meadow", "harbor", "willow", "cedar",
    "amber", "cobalt", "quartz", "cinder", "marble", "orchard", "thicket", "beacon",
)


def _seed_int(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


@dataclass(frozen=True)
class DecoyChallenge:
    """A harmless type-this-phrase challenge. Deterministic in `seed` so it is testable
    and so the same client sees a stable phrase across a retry, but it protects nothing:
    passing it only lets the client retry the normal (still throttled) login."""

    words: int = 3

    def phrase(self, seed: str) -> str:
        value = _seed_int(seed)
        chosen = []
        for _ in range(self.words):
            chosen.append(_DECOY_WORDS[value % len(_DECOY_WORDS)])
            value //= len(_DECOY_WORDS)
        return " ".join(chosen)

    def verify(self, seed: str, typed: str) -> bool:
        """True if the client echoed the phrase. Whitespace-normalized, case-insensitive.
        This NEVER touches credentials or auth state — a pass just clears the tarpit."""
        return " ".join(str(typed or "").split()).casefold() == self.phrase(seed).casefold()


def should_serve_decoy(seed: str, percent: int) -> bool:
    """Randomized (but seed-deterministic, so testable) decision to serve a decoy for a
    given blocked attempt. `percent` in [0, 100]; 0 disables, 100 always serves."""
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    return (_seed_int(seed) % 100) < percent


# --- Level 3: expiring edge (firewall / reverse-proxy) DROP --------------------


def pf_block_command(ip: str, table: str = "yolomux_login_block") -> list[str]:
    """argv to add a validated address to a macOS pf table (a `block drop from <table>`
    rule must be loaded separately by the operator). Raises on a non-IP input, and the
    argv form means the address can never be interpreted as a shell token."""
    address = str(ipaddress.ip_address(ip))
    return ["pfctl", "-t", table, "-T", "add", address]


def pf_unblock_command(ip: str, table: str = "yolomux_login_block") -> list[str]:
    address = str(ipaddress.ip_address(ip))
    return ["pfctl", "-t", table, "-T", "delete", address]


def nft_block_command(ip: str, ttl_seconds: int, table: str = "yolomux", element_set: str = "login_block") -> list[str]:
    """argv to add a validated address to an nftables timeout set on Linux. The set's own
    `timeout` makes the rule self-expire even if the process dies before removing it."""
    address = str(ipaddress.ip_address(ip))
    family = "ip6" if ipaddress.ip_address(ip).version == 6 else "ip"
    return ["nft", "add", "element", family, table, element_set, "{", address, "timeout", f"{int(ttl_seconds)}s", "}"]


def nft_unblock_command(ip: str, table: str = "yolomux", element_set: str = "login_block") -> list[str]:
    address = str(ipaddress.ip_address(ip))
    family = "ip6" if ipaddress.ip_address(ip).version == 6 else "ip"
    return ["nft", "delete", "element", family, table, element_set, "{", address, "}"]


def default_edge_runner(argv: list[str]) -> bool:
    """Execute one edge command as an argv list (never a shell string) with a short
    timeout. Returns True on exit 0. Only ever called by an ENABLED controller, so a
    default (disabled) server never spawns a firewall process."""
    try:
        result = subprocess.run(argv, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@dataclass
class EdgeBlockRule:
    ip: str
    expires_at: float


@dataclass
class EdgeBlockController:
    """Opt-in, off-by-default controller for expiring edge DROP rules.

    Every guard the request asked for is enforced here: disabled by default; an injected
    `runner` (never a shell) so tests and normal runs cannot touch the real firewall; a
    strict per-rule TTL; a hard cap on simultaneous rules; a trusted-CIDR exclusion so a
    forged volumetric attack cannot get the server to firewall its own operators; and a
    local list/clear path. The runner receives an argv list built by one of the reviewed
    command builders above.
    """

    runner: Callable[[list[str]], bool]
    enabled: bool = False
    ttl_seconds: int = 900
    max_rules: int = 256
    trusted_cidrs: tuple[str, ...] = ()
    block_command: Callable[[str, int], list[str]] = field(default=lambda ip, ttl: nft_block_command(ip, ttl))
    unblock_command: Callable[[str], list[str]] = field(default=lambda ip: nft_unblock_command(ip))
    clock: Callable[[], float] = time.time
    _rules: dict[str, EdgeBlockRule] = field(default_factory=dict)
    _installed: int = 0
    _refused_trusted: int = 0

    def _is_trusted(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        for cidr in self.trusted_cidrs:
            try:
                if address in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def block(self, ip: str) -> bool:
        """Install (or refresh) an expiring DROP for a volumetric source. Returns True
        only if a rule is now active. No-ops when disabled, when the IP is unparseable or
        trusted, or when the active-rule cap is reached (after pruning expired rules)."""
        if not self.enabled:
            return False
        try:
            address = ipaddress.ip_address(str(ip))
        except ValueError:
            return False
        if self._is_trusted(address):
            self._refused_trusted += 1
            return False
        now = self.clock()
        self.expire_due(now)
        key = str(address)
        if key in self._rules:
            self._rules[key].expires_at = now + self.ttl_seconds
            return True
        if len(self._rules) >= self.max_rules:
            return False
        if not self.runner(self.block_command(key, self.ttl_seconds)):
            return False
        self._rules[key] = EdgeBlockRule(ip=key, expires_at=now + self.ttl_seconds)
        self._installed += 1
        return True

    def expire_due(self, now: float | None = None) -> int:
        """Remove rules past their TTL. Returns how many were removed. Safe to call
        often; the nftables set also self-expires, so a missed call cannot leave a
        permanent block."""
        now = self.clock() if now is None else now
        expired = [key for key, rule in self._rules.items() if rule.expires_at <= now]
        for key in expired:
            self.runner(self.unblock_command(key))
            del self._rules[key]
        return len(expired)

    def clear(self) -> int:
        """Operator recovery: drop every active rule immediately."""
        keys = list(self._rules)
        for key in keys:
            self.runner(self.unblock_command(key))
        self._rules.clear()
        return len(keys)

    def active_rules(self) -> list[str]:
        return sorted(self._rules)

    def diagnostics(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "active_rules": len(self._rules),
            "installed_total": self._installed,
            "refused_trusted": self._refused_trusted,
            "max_rules": self.max_rules,
            "ttl_seconds": self.ttl_seconds,
        }
