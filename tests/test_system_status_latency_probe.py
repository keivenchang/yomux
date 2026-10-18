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

import dataclasses
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import secrets
import socket
import statistics
import subprocess
import time
from typing import Any
from typing import Sequence
from urllib.parse import urlencode

from http.client import HTTPConnection
import pytest

from tools import bazel_execution_contract
from tools import system_status_latency_probe
from yomux_lib import common as common_module
from yomux_lib.infra import listener_census

from tests.gate_harness import GateAuthCredentials
from tests.gate_harness import GateLiveServer
from tests.gate_harness import gate_auth_credentials  # noqa: F401
from tests.native_gate_fixtures import gate_authenticated_live_server  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_http_request
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
# One authenticated-fixture login helper, not a third copy of the same form post.
from tests.helpers.http_routes import login_cookie as _login_cookie


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
# W9: the browser's capture-marker header. Each sampled request carries a UNIQUE marker so the
# server tags each per-request record with a unique measurement_request_id we can join one-to-one.
MEASUREMENT_HEADER = "X-YOmux-Measurement"
# W9: the stated headline for this queue item -- server assembly time from route entry to the wire
# representation being ready, stamped by the shared response writer BEFORE any header/body byte
# leaves. compute_ms/write_ms are retained beside it as diagnostics, not as the headline.
HEADLINE_METRIC = "route_to_representation_ready_ms"
# W9 acceptance: nearest-rank p99 of the representation-ready time must be under this. Client RTT is
# recorded separately and is never substituted for it.
REPRESENTATION_READY_BUDGET_MS = 20.0


def unique_capture_marker() -> str:
    """Mint one marker the server's own `measurement_marker()` validator will accept.

    The validator requires `capture-` then 32 lowercase hex characters (40 total). `token_hex(16)`
    is exactly 32 hex chars, and being random it makes every sampled request distinct so no two
    requests can be conflated onto one server record.
    """

    return "capture-" + secrets.token_hex(16)


def measurement_request_id(marker: str) -> str:
    """Mirror the server's opaque per-request join key: sha256(marker)[:16]. One owner of the rule
    would be better, but the server derives it inside the request handler with no importable seam,
    so this reproduces exactly that derivation and the join test pins the two together."""

    return hashlib.sha256(marker.encode("ascii")).hexdigest()[:16] if marker else ""


@dataclasses.dataclass(frozen=True)
class CaptureSample:
    """One issued sampled request as the client observed it."""

    request_id: str
    http_status: int
    stale: bool


@dataclasses.dataclass(frozen=True)
class JoinedRun:
    """Typed outcome of joining issued requests to server records. `ok=False` invalidates the run
    and `reason` says why; a probe never reports a p99 built on an invalidated join."""

    ok: bool
    reason: str
    values: tuple[float, ...]


def join_capture_samples_to_records(
    samples: Sequence[CaptureSample],
    records: Sequence[dict[str, Any]],
) -> JoinedRun:
    """Join every issued request to EXACTLY ONE server record and return the headline series.

    Any of these invalidates the whole run (W9): a duplicated request digest, a request that did
    not return 200, a request served a stale/unavailable snapshot, a request with no joined server
    record (missing), two server records claiming one request (duplicate), a server record whose
    own recorded status was not 200, or a joined record that never measured the headline metric
    (unavailable). The series is returned in issued order so the percentile is over the real sample.
    """

    if len({sample.request_id for sample in samples}) != len(samples):
        return JoinedRun(False, "duplicate request digest among issued samples", ())
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        details = record.get("details") if isinstance(record.get("details"), dict) else {}
        request_id = str(details.get("measurement_request_id") or "")
        if not request_id:
            continue
        if request_id in by_id:
            return JoinedRun(False, f"duplicate server record for request {request_id}", ())
        by_id[request_id] = record
    values: list[float] = []
    for sample in samples:
        if sample.http_status != 200:
            return JoinedRun(False, f"request {sample.request_id} returned HTTP {sample.http_status}", ())
        if sample.stale:
            return JoinedRun(False, f"request {sample.request_id} served a stale/unavailable snapshot", ())
        record = by_id.get(sample.request_id)
        if record is None:
            return JoinedRun(False, f"no server record joined request {sample.request_id}", ())
        recorded_status = str(record.get("cache_status") or "")
        if recorded_status not in ("", "200"):
            return JoinedRun(False, f"server record for {sample.request_id} recorded status {recorded_status}", ())
        details = record.get("details") if isinstance(record.get("details"), dict) else {}
        headline = details.get(HEADLINE_METRIC)
        if isinstance(headline, bool) or not isinstance(headline, (int, float)) or not math.isfinite(float(headline)):
            return JoinedRun(False, f"request {sample.request_id} has no measured {HEADLINE_METRIC}", ())
        values.append(float(headline))
    return JoinedRun(True, "ok", tuple(values))


def server_identity_snapshot() -> dict[str, Any]:
    """The process-identity facts a run requires to stay unchanged across its whole span: a changed
    PID/start time means a restart happened mid-run, a changed cwd/version/SHA means a different
    artifact answered part of it. Read from the product's own known constants, never sampled."""

    return {
        "pid": os.getpid(),
        "started_at": float(common_module.SERVER_STARTED_AT),
        "cwd": str(common_module.PROJECT_ROOT),
        "version": str(common_module.YOMUX_VERSION),
        "sha": resolved_source_sha(str(common_module.PROJECT_ROOT)),
    }


def resolved_source_sha(project_root: str) -> str:
    """Resolve the deployed source SHA from the git checkout at `project_root`.

    Returns "" when the tree is not a resolvable git checkout. That absence is itself part of the
    identity: an empty SHA compared against an empty SHA is unchanged, but an empty SHA against a
    resolved one (or two different resolved ones) invalidates -- which is the point.
    """

    try:
        completed = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def identity_unchanged(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str]:
    """Require the two identity snapshots to be identical key-for-key. A differing or missing key
    invalidates the run; there is no field a restart is allowed to change mid-measurement."""

    if set(before) != set(after):
        return False, f"identity key set changed: {sorted(before)} != {sorted(after)}"
    required = {"pid", "started_at", "cwd", "version", "sha"}
    missing = sorted(key for key in required if key not in before or before[key] in (None, ""))
    if missing:
        return False, f"identity fields unavailable: {missing}"
    for key in sorted(before):
        if before[key] != after[key]:
            return False, f"identity field {key} changed from {before[key]!r} to {after[key]!r}"
    return True, "ok"


def acceptance_outcome(values: Sequence[float], budget_ms: float = REPRESENTATION_READY_BUDGET_MS) -> dict[str, Any]:
    """Nearest-rank p99 against the budget. `ok` is the acceptance bit; `p99_ms` is the number an
    observer can point at (an actual sample, not an interpolation)."""

    series = [float(value) for value in values]
    p99 = _percentile(series, 0.99)
    return {"ok": p99 < float(budget_ms), "p99_ms": p99, "budget_ms": float(budget_ms), "count": len(series)}




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




# ---- gate-safe synthetic proofs of the probe's pure logic --------------------------------------
#
# These run in the gate: they prove the join/validation/percentile/identity rules that decide
# whether a run is valid, without paying for a live 200-sample assembly (that acceptance run is
# W14, against a settled server). The live probe above imports the very same functions.


def _record(request_id: str, ready_ms: float, *, status: str = "200", compute_ms: float = 1.0, write_ms: float = 0.5) -> dict[str, Any]:
    return {
        "role": "http-endpoint",
        "surface": SYSTEM_STATUS_SURFACE,
        "cache_status": status,
        "compute_ms": compute_ms,
        "payload_bytes": 128,
        "details": {
            "measurement_scope": "capture",
            "measurement_request_id": request_id,
            HEADLINE_METRIC: ready_ms,
            "write_ms": write_ms,
        },
    }




def test_join_returns_the_headline_series_in_issued_order_for_a_clean_run() -> None:
    ids = [measurement_request_id(unique_capture_marker()) for _ in range(3)]
    samples = [CaptureSample(request_id=rid, http_status=200, stale=False) for rid in ids]
    # Records arrive out of order; the join must still return values in ISSUED order.
    records = [_record(ids[2], 9.0), _record(ids[0], 3.0), _record(ids[1], 6.0)]
    result = join_capture_samples_to_records(samples, records)
    assert result.ok, result.reason
    assert result.values == (3.0, 6.0, 9.0)


def test_join_invalidates_on_a_missing_server_record() -> None:
    ids = [measurement_request_id(unique_capture_marker()) for _ in range(2)]
    samples = [CaptureSample(request_id=rid, http_status=200, stale=False) for rid in ids]
    result = join_capture_samples_to_records(samples, [_record(ids[0], 3.0)])
    assert not result.ok
    assert "no server record" in result.reason


def test_join_invalidates_on_a_duplicate_server_record() -> None:
    rid = measurement_request_id(unique_capture_marker())
    samples = [CaptureSample(request_id=rid, http_status=200, stale=False)]
    result = join_capture_samples_to_records(samples, [_record(rid, 3.0), _record(rid, 4.0)])
    assert not result.ok
    assert "duplicate server record" in result.reason


def test_join_invalidates_on_a_duplicate_request_digest() -> None:
    rid = measurement_request_id(unique_capture_marker())
    samples = [CaptureSample(request_id=rid, http_status=200, stale=False), CaptureSample(request_id=rid, http_status=200, stale=False)]
    result = join_capture_samples_to_records(samples, [_record(rid, 3.0)])
    assert not result.ok
    assert "duplicate request digest" in result.reason


def test_join_invalidates_on_a_non_200_request() -> None:
    rid = measurement_request_id(unique_capture_marker())
    samples = [CaptureSample(request_id=rid, http_status=503, stale=False)]
    result = join_capture_samples_to_records(samples, [_record(rid, 3.0)])
    assert not result.ok
    assert "HTTP 503" in result.reason


def test_join_invalidates_on_a_stale_or_unavailable_snapshot() -> None:
    rid = measurement_request_id(unique_capture_marker())
    samples = [CaptureSample(request_id=rid, http_status=200, stale=True)]
    result = join_capture_samples_to_records(samples, [_record(rid, 3.0)])
    assert not result.ok
    assert "stale/unavailable" in result.reason


def test_join_invalidates_when_a_record_never_measured_the_headline() -> None:
    rid = measurement_request_id(unique_capture_marker())
    samples = [CaptureSample(request_id=rid, http_status=200, stale=False)]
    record = _record(rid, 3.0)
    del record["details"][HEADLINE_METRIC]
    result = join_capture_samples_to_records(samples, [record])
    assert not result.ok
    assert HEADLINE_METRIC in result.reason
    # A non-finite value is the same unmeasured state, not a real 0.
    nan_record = _record(rid, float("nan"))
    result_nan = join_capture_samples_to_records(samples, [nan_record])
    assert not result_nan.ok


def test_join_invalidates_when_the_record_recorded_a_non_200_status() -> None:
    rid = measurement_request_id(unique_capture_marker())
    samples = [CaptureSample(request_id=rid, http_status=200, stale=False)]
    result = join_capture_samples_to_records(samples, [_record(rid, 3.0, status="500")])
    assert not result.ok
    assert "recorded status 500" in result.reason


def test_identity_unchanged_flags_every_kind_of_drift() -> None:
    base = {"pid": 10, "started_at": 1.0, "cwd": "/srv", "version": "0.7.2", "sha": "abc"}
    ok, _reason = identity_unchanged(base, dict(base))
    assert ok
    for key, changed in (("pid", 11), ("started_at", 2.0), ("cwd", "/other"), ("version", "0.7.3"), ("sha", "def")):
        drifted = {**base, key: changed}
        ok, reason = identity_unchanged(base, drifted)
        assert not ok and key in reason
    # A missing key is drift, not a match.
    ok, reason = identity_unchanged(base, {k: v for k, v in base.items() if k != "sha"})
    assert not ok and "key set changed" in reason
    for key in base:
        unavailable = {**base, key: ""}
        ok, reason = identity_unchanged(unavailable, dict(unavailable))
        assert not ok and key in reason


def test_server_identity_snapshot_reads_stable_process_facts() -> None:
    snapshot = server_identity_snapshot()
    assert snapshot["pid"] == os.getpid()
    assert snapshot["version"] == common_module.YOMUX_VERSION
    assert snapshot["cwd"] == str(common_module.PROJECT_ROOT)
    # Two reads in one unchanged process must be identical.
    if not snapshot["sha"]:
        pytest.skip("repository identity is unavailable in the isolated test checkout")
    ok, reason = identity_unchanged(snapshot, server_identity_snapshot())
    assert ok, reason


def test_acceptance_uses_nearest_rank_p99_against_the_budget() -> None:
    # Nearest-rank p99 of 200 sorted values is the 198th (ceil(0.99*200)); the top two sit ABOVE it.
    good = [5.0] * 197 + [19.0, 19.2, 19.5]
    outcome = acceptance_outcome(good)
    assert outcome["ok"] and outcome["p99_ms"] == 19.0 and outcome["count"] == 200
    # Three values at/above the p99 rank fail acceptance; the mean would have hidden them.
    bad = [5.0] * 197 + [25.0, 25.5, 26.0]
    assert acceptance_outcome(bad)["ok"] is False and acceptance_outcome(bad)["p99_ms"] == 25.0


@pytest.mark.parametrize("foreign", [False, True])
def test_listener_identity_uses_declared_revision_only_for_declared_checkout(monkeypatch, tmp_path, foreign):
    declared_root = (tmp_path / "declared").resolve()
    declared_root.mkdir()
    monkeypatch.setattr(bazel_execution_contract, "ROOT", declared_root)
    monkeypatch.setenv("YOMUX_BAZEL_EXECUTION", "qualified-native")
    monkeypatch.setenv("YOMUX_BAZEL_CONTRACT", "fixture-policy")
    monkeypatch.setenv("YOMUX_TEST_REVISION", "a" * 40)
    listener_root = declared_root
    if foreign:
        listener_root = (tmp_path / "foreign").resolve()
        listener_root.mkdir()
        subprocess.run(["git", "init", "--quiet", str(listener_root)],
                       env=bazel_execution_contract.repository_git_environment(), check=True)
        git_directory = listener_root / ".git"
        (git_directory / "HEAD").write_text("ref: refs/heads/fixture\n")
        (git_directory / "refs/heads/fixture").write_text("b" * 40 + "\n")
    monkeypatch.setattr(system_status_latency_probe, "process_start_identity", lambda _pid: "proc:123")
    monkeypatch.setattr(system_status_latency_probe, "process_cwd", lambda _pid: str(listener_root))

    identity = system_status_latency_probe.process_identity(123)

    assert not (declared_root / ".git").exists()
    assert identity == {"pid": 123, "start_identity": "proc:123", "cwd": str(listener_root),
                        "sha": ("b" if foreign else "a") * 40}


def test_probe_error_report_preserves_the_cause_chain_and_errno():
    """Exit 2 must name the underlying refusal, not just the census summary line.

    Three retained gate artifacts recorded only `cannot enumerate file descriptors for Linux
    process ...`. That text reads identically whether the kernel refused another user's process
    or /proc itself was unreadable, and the operator cannot act without the errno.
    """

    denied = PermissionError(errno.EACCES, os.strerror(errno.EACCES), "/proc/456/fd")
    try:
        raise listener_census.ListenerCensusError(
            "cannot enumerate file descriptors for Linux process 456"
        ) from denied
    except listener_census.ListenerCensusError as error:
        rendered = system_status_latency_probe.error_with_cause(error)

    assert "cannot enumerate file descriptors for Linux process 456" in rendered
    assert "PermissionError" in rendered
    assert f"errno {errno.EACCES} EACCES" in rendered
    assert "<- caused by" in rendered
    # The pre-change report was the summary alone; it must no longer be the whole message.
    assert rendered != "cannot enumerate file descriptors for Linux process 456"


def test_probe_error_report_terminates_on_a_self_referential_cause():
    """A cause cycle must not spin the renderer on the failure path."""

    first = RuntimeError("outer")
    second = RuntimeError("inner")
    first.__cause__ = second
    second.__cause__ = first

    rendered = system_status_latency_probe.error_with_cause(first)
    assert rendered.count("<- caused by") == 1


def test_probe_error_report_honours_a_suppressed_context():
    """`raise ... from None` is an instruction to hide the context; the report must obey it."""

    try:
        try:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), "/proc/456/fd")
        except PermissionError:
            raise RuntimeError("listener census failed") from None
    except RuntimeError as error:
        rendered = system_status_latency_probe.error_with_cause(error)

    assert rendered == "RuntimeError: listener census failed"
    assert "PermissionError" not in rendered
    assert "EACCES" not in rendered


def test_probe_error_report_still_follows_an_unsuppressed_context():
    """An implicit context is not suppressed, so it is still reported with its errno."""

    try:
        try:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), "/proc/456/fd")
        except PermissionError:
            raise RuntimeError("listener census failed")
    except RuntimeError as error:
        rendered = system_status_latency_probe.error_with_cause(error)

    assert "RuntimeError: listener census failed" in rendered
    assert "PermissionError" in rendered
    assert f"errno {errno.EACCES} EACCES" in rendered
