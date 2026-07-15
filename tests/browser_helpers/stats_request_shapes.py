"""Python mirror of the client's ONE stats request-shape owner.

The browser builds every `/api/stats-sample` query through `jsDebugStatsSampleQuery`
(static_src/js/yolomux/83_debug_panel.js). Tests, fixtures, and diagnosis probes on the
python side MUST build request shapes through this mirror instead of hand-rolling them:
the 2026-07-14 host-metrics outage escaped because a probe hand-rolled a request and
validated the wrong serve path. Both implementations are contract-tested byte-equal
against the shared goldens in tests/fixtures/stats_request_shapes.json
(python: tests/test_stats_request_shapes.py; node: tests/yostats_performance.test.js) —
if the client builder changes, regenerate the goldens and both sides fail until they
agree again.

There are NO token_* params in the client request anymore: token rates and cost ride
every history record of the ONE history stream. `token_resolution_for_range` survives
as the client's token-chart DISPLAY FLOOR mirror (debugGraphAgentTokenResolution) and
as the server-side legacy-compat concept old clients still send.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from yolomux_lib.app import TmuxWebtermApp

STATS_HISTORY_MAX_POINTS = 6000


def token_resolution_for_range(range_seconds: int) -> int:
    """Mirror of the client's per-range token-chart display floor
    (debugGraphAgentTokenResolution). No longer sent on the wire by current
    clients; the same tiers double as the legacy-compat `token_resolution`
    an OLD client would have requested, which legacy server tests still use."""
    if range_seconds < 4 * 3600:
        return 0
    return 300 if range_seconds >= 16 * 3600 else 120


def stats_sample_query(
    *,
    since: int = 0,
    client_id: str = "",
    token_consumer: str = "0",
    history_start: int = 0,
    history_end: int = 0,
    history_resolution: int = 1,
    history_max_points: int = STATS_HISTORY_MAX_POINTS,
    history: bool = True,
) -> str:
    """Byte-identical mirror of jsDebugStatsSampleQuery."""

    def encoded(value: Any) -> str:
        # encodeURIComponent leaves A-Za-z0-9 -_.!~*'() unescaped.
        return quote(str(value), safe="-_.!~*'()")

    parts = [
        f"since={encoded(since)}",
        f"client_id={encoded(client_id)}",
        f"token_consumer={encoded(token_consumer)}",
        f"history_start={encoded(history_start)}",
        f"history_end={encoded(history_end)}",
        f"history_resolution={encoded(history_resolution)}",
        f"history_max_points={encoded(history_max_points)}",
    ]
    if not history:
        parts.append("history=0")
    return "/api/stats-sample?" + "&".join(parts)


def reader_history_request(range_seconds: int, now_seconds: int, client_id: str = "range-sweep") -> dict[str, Any]:
    """The statsd `_encoded_history` request dict matching what the browser's wire query
    resolves to for a fresh fetch of `range_seconds` — translated through the REAL
    web-layer translator (`TmuxWebtermApp.stats_sample_history_query`), never a
    hand-mapped dict. The reader dialect uses `start`/`end`/`resolution_seconds`/
    `max_points`, NOT the wire's `history_*` names: hand-mapped dicts with the wire
    names silently queried the whole unwindowed store (that mistake shipped in this
    very helper first). Current clients send NO token params — token detail rides
    every record; use `legacy_reader_history_request` to model an OLD client."""
    return TmuxWebtermApp.stats_sample_history_query(
        since=0,
        client_id=client_id,
        history_start=now_seconds - range_seconds,
        history_end=0,
        history_resolution_seconds=1,
        history_max_points=STATS_HISTORY_MAX_POINTS,
        include_history=True,
    )


def legacy_reader_history_request(range_seconds: int, now_seconds: int, client_id: str = "legacy-client") -> dict[str, Any]:
    """LEGACY COMPAT (retire with the server path): the request an OLD client —
    one built before the single history stream — still sends at `range_seconds`,
    including its per-range compact-token-stream params. Built through the REAL
    translator so legacy server tests exercise the exact accepted shape."""
    token_resolution = token_resolution_for_range(range_seconds)
    return TmuxWebtermApp.stats_sample_history_query(
        since=0,
        client_id=client_id,
        token_since=0,
        token_resolution_seconds=token_resolution,
        token_history_start=(now_seconds - range_seconds) if token_resolution else None,
        token_history_end=0 if token_resolution else None,
        history_start=now_seconds - range_seconds,
        history_end=0,
        history_resolution_seconds=1,
        history_max_points=STATS_HISTORY_MAX_POINTS,
        include_history=True,
    )
