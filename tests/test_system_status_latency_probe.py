# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Measured latency probe for `GET /api/system-status`, before and after the snapshot owner.

This module measures; it does not assert a product budget. It exists because the queue item that
moved this route onto a background snapshot required a NUMBER for the first-request (cold) path and
for the steady state, taken to one stated protocol, on both sides of the change.

The protocol, fixed here so a later run cannot quietly redefine it:

* the headline quantity is SERVER HANDLER TIME - route entry to response serialization - read back
  out of the server's own per-request performance ring (`app.performance_records`, role
  `http-endpoint`, surface `GET /api/system-status`), not timed by the client;
* CLIENT ROUND TRIP is recorded beside it as a separate number and never substituted for it;
* the sample is `SAMPLE_REQUESTS` requests after `WARMUP_REQUESTS` discarded warmup requests, at a
  stated fixed concurrency (`CONCURRENCY`, default 1, recorded in the artifact either way);
* the COLD request is the very first `/api/system-status` this server process ever answers, taken
  before any warmup and reported on its own - averaging it into the sample is what hid it before;
* the snapshot publish cadence and the freshness deadline in force are recorded with the sample,
  because a steady-state number means nothing without the cadence that produced it;
* `loadavg` is recorded as an observed side fact only. It is ambient noise, not a workload, and the
  artifact labels it that way so it cannot later be cited as the load condition.

Run it explicitly; it is skipped in the gate because a pre-change run costs about a minute of real
route assembly:

    YOLOMUX_MEASURE_SYSTEM_STATUS=before python3 -m pytest tests/test_system_status_latency_probe.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import time
from typing import Any
from urllib.parse import urlencode

from http.client import HTTPConnection
import pytest

from tests.gate_harness import GateAuthCredentials
from tests.gate_harness import GateLiveServer
from tests.gate_harness import gate_auth_credentials  # noqa: F401
from tests.gate_harness import gate_authenticated_live_server  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_http_request
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
# One authenticated-fixture login helper, not a third copy of the same form post.
from tests.test_gate_route_sweep import _login_cookie


MEASURE_ENV = "YOLOMUX_MEASURE_SYSTEM_STATUS"
# The probe runs inside the isolated test container, whose filesystem is thrown away. The one
# host directory mounted at the same absolute path is the evidence directory `docker/run-tests.sh`
# already binds, so the raw artifact is written there rather than into a container-local HOME that
# would vanish with the run.
ARTIFACT_DIR_ENV = "YOLOMUX_E2E_EVIDENCE_DIR"
DEFAULT_ARTIFACT_DIR = Path.home() / "yolomux-evidence" / "f1"
SYSTEM_STATUS_PATH = "/api/system-status"
SYSTEM_STATUS_SURFACE = f"GET {SYSTEM_STATUS_PATH}"
WARMUP_REQUESTS = 20
SAMPLE_REQUESTS = 200
CONCURRENCY = 1


def _server_handler_samples(runtime: GateLiveServer) -> list[dict[str, Any]]:
    """Read the server's own per-request records for this surface, oldest first."""

    app = runtime.app
    with app.performance_record_lock:
        records = [dict(item) for item in app.performance_records]
    return [item for item in records if item.get("role") == "http-endpoint" and item.get("surface") == SYSTEM_STATUS_SURFACE]


def _await_server_handler_samples(runtime: GateLiveServer, expected: int, *, timeout: float = 5.0) -> list[dict[str, Any]]:
    """Wait for the server's own record of the last response.

    The performance record is written AFTER the response bytes leave, so a client that read its
    body can legitimately be one record ahead of the ring. Waiting for the count is the difference
    between a real measurement and a flaky one; it is not a retry of the measurement itself.
    """

    deadline = time.monotonic() + timeout
    records = _server_handler_samples(runtime)
    while len(records) < expected and time.monotonic() < deadline:
        time.sleep(0.01)
        records = _server_handler_samples(runtime)
    assert len(records) == expected, f"expected {expected} records, got {len(records)}"
    return records


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile: the value an observer can point at, not an interpolation."""

    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": max(values),
    }


@pytest.mark.skipif(
    not os.environ.get(MEASURE_ENV),
    reason=f"latency probe runs only when {MEASURE_ENV} names the run (for example 'before' or 'after')",
)
def test_measure_system_status_latency(
    gate_authenticated_live_server: GateLiveServer,  # noqa: F811
    gate_auth_credentials: GateAuthCredentials,  # noqa: F811
) -> None:
    runtime = gate_authenticated_live_server
    label = str(os.environ.get(MEASURE_ENV) or "unlabelled")
    cookie = _login_cookie(runtime, gate_auth_credentials)
    headers = {"Cookie": cookie, "Connection": "close"}

    # The cold request: the first time this process answers this route at all. Nothing has warmed
    # an import, a cache, or - after the change - published a snapshot.
    cold_started = time.perf_counter()
    cold_response = gate_http_request(runtime, SYSTEM_STATUS_PATH, headers=headers, timeout=30.0)
    cold_round_trip_ms = (time.perf_counter() - cold_started) * 1000.0
    cold_body = json.loads(cold_response.body.decode("utf-8")) if cold_response.body else {}
    cold_records = _await_server_handler_samples(runtime, 1)
    cold_handler_ms = float(cold_records[0].get("compute_ms") or 0.0)

    for _index in range(WARMUP_REQUESTS):
        gate_http_request(runtime, SYSTEM_STATUS_PATH, headers=headers, timeout=30.0)

    round_trips: list[float] = []
    statuses: list[int] = []
    for _index in range(SAMPLE_REQUESTS):
        started = time.perf_counter()
        response = gate_http_request(runtime, SYSTEM_STATUS_PATH, headers=headers, timeout=30.0)
        round_trips.append((time.perf_counter() - started) * 1000.0)
        statuses.append(response.status)

    records = _await_server_handler_samples(runtime, 1 + WARMUP_REQUESTS + SAMPLE_REQUESTS)
    handler_ms = [float(item.get("compute_ms") or 0.0) for item in records[-SAMPLE_REQUESTS:]]
    payload_bytes = [int(item.get("payload_bytes") or 0) for item in records[-SAMPLE_REQUESTS:]]

    # The assembly cost itself, measured in-process. This is the quantity the change MOVES rather
    # than removes: before, every one of those 200 handler times contains it; after, none does.
    # Recording it on both sides is what makes the two runs comparable on a machine whose absolute
    # speed differs from the live host.
    assembly_ms: list[float] = []
    for _index in range(5):
        started = time.perf_counter()
        runtime.app.system_status_payload()
        assembly_ms.append((time.perf_counter() - started) * 1000.0)

    snapshot_owner = getattr(runtime.app, "system_status_snapshot", None)
    snapshot_status = snapshot_owner.status() if snapshot_owner is not None else {
        "present": False,
        "note": "no background snapshot owner in this build; the route assembles on the request thread",
    }

    artifact = {
        "label": label,
        "protocol": {
            "headline_quantity": "server handler time (route entry to response serialization)",
            "server_handler_source": "app.performance_records role=http-endpoint surface=" + SYSTEM_STATUS_SURFACE,
            "client_round_trip": "recorded separately, never substituted for the headline",
            "warmup_requests_discarded": WARMUP_REQUESTS,
            "sample_requests": SAMPLE_REQUESTS,
            "concurrency": CONCURRENCY,
            "cold_request": "the first /api/system-status this server process answered",
        },
        "environment": {
            "host": socket.gethostname(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "measured_at": time.time(),
            # Ambient only. This is NOT the load condition and may not be cited as one.
            "observed_loadavg_side_fact_only": list(os.getloadavg()),
        },
        "snapshot": snapshot_status,
        "cold": {
            "server_handler_ms": cold_handler_ms,
            "client_round_trip_ms": cold_round_trip_ms,
            "http_status": cold_response.status,
            "body_keys": sorted(cold_body.keys()) if isinstance(cold_body, dict) else [],
        },
        # What the change MOVES rather than removes, measured in-process on both sides.
        "assembly_ms": _summary(assembly_ms),
        "steady_state": {
            "server_handler_ms": _summary(handler_ms),
            "client_round_trip_ms": _summary(round_trips),
            "http_status_counts": {str(code): statuses.count(code) for code in sorted(set(statuses))},
            "payload_bytes_median": statistics.median(payload_bytes),
        },
        "raw": {
            "server_handler_ms": handler_ms,
            "client_round_trip_ms": round_trips,
        },
    }
    directory = Path(os.environ.get(ARTIFACT_DIR_ENV) or DEFAULT_ARTIFACT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"system-status-latency-{label}.json"
    text = json.dumps(artifact, indent=2, sort_keys=True)
    path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (directory / f"system-status-latency-{label}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    print(f"\nARTIFACT {path}\nSHA256 {digest}\n{json.dumps({k: v for k, v in artifact.items() if k != 'raw'}, indent=2, sort_keys=True)}")
