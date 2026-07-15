"""Python mirror of the client's ONE stats request-shape owner.

The browser builds every `/api/stats-sample` query through `jsDebugStatsSampleQuery`
(static_src/js/yolomux/83_debug_panel.js). Tests, fixtures, and diagnosis probes on the
python side MUST build request shapes through this mirror instead of hand-rolling them:
the 2026-07-14 host-metrics outage escaped because a probe omitted `token_resolution`
and therefore validated the wrong serve path. Both implementations are contract-tested
byte-equal against the shared goldens in tests/fixtures/stats_request_shapes.json
(python: tests/test_stats_request_shapes.py; node: tests/yostats_performance.test.js) —
if the client builder changes, regenerate the goldens and both sides fail until they
agree again.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

STATS_HISTORY_MAX_POINTS = 6000


def token_resolution_for_range(range_seconds: int) -> int:
    """The client's per-range compact-token-stream resolution (debugGraphAgentTokenResolution)."""
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
    token_resolution: int = 0,
    token_since: int = 0,
    token_history_start: int = 0,
    token_history_end: int = 0,
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
    if int(token_resolution) > 0:
        parts.append(f"token_since={encoded(token_since)}")
        parts.append(f"token_resolution={encoded(token_resolution)}")
        parts.append(f"token_history_start={encoded(token_history_start)}")
        parts.append(f"token_history_end={encoded(token_history_end)}")
    return "/api/stats-sample?" + "&".join(parts)


def reader_history_request(range_seconds: int, now_seconds: int, client_id: str = "range-sweep") -> dict[str, Any]:
    """The statsd `_encoded_history` request dict matching what the browser's wire query
    resolves to for a fresh fetch of `range_seconds` — INCLUDING the per-range
    token_resolution. Serve-layer tests must use this instead of hand-built dicts."""
    request: dict[str, Any] = {
        "history_start": now_seconds - range_seconds,
        "history_end": 0,
        "history_resolution": 1,
        "history_max_points": STATS_HISTORY_MAX_POINTS,
        "include_history": True,
        "client_id": client_id,
    }
    token_resolution = token_resolution_for_range(range_seconds)
    if token_resolution:
        request["token_resolution"] = token_resolution
    return request
