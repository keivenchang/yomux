#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Runnable experiment harness for Defect 2, the YO!stats stream generation stall.

A rate design is inert here and is deliberately not offered. At the measured 2/163 base rate
(1.227%) twenty attempts per arm expect 0.2454 events; Fisher's exact on 20-vs-20 needs a 20.4x
rate ratio before it can separate anything, and detecting a plausible 2x needs 1,883 attempts per
arm, about 26.9 days at 617.9 s per full gate. So this harness does not count failures. It makes
ONE occurrence classifiable: every attempt is recorded with the identity that makes two arms
comparable, and a failed attempt is reduced to an ordered boundary timeline naming which side of
the pipeline went quiet first.

The three evidence sources it reduces are the ones the telemetry commits on this branch produce:
the `streamEvidence()` snapshot attached to the failure, the anomaly-only server boundary records
in the operator log ring, and the SSE emit timestamp compared against arrival.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
PACIFIC = ZoneInfo("America/Los_Angeles")

ATTEMPT_SCHEMA = 1
TIMELINE_SCHEMA = 1

# The one failure this experiment classifies. Byte-identical to the client predicate at
# static_src/js/yolomux/84_stats_current.js; several retained records key off it, and the
# retired `cpuAxisMax == 100` mismatch is deliberately NOT in scope.
STALL_PREDICATE = "YO!stats stream generation stalled for more than 3s"
SUBJECT_NODE_ID = (
    "tests/test_browser_stats_widen.py::"
    "test_real_stats_cpu_value_round_trips_through_rpc_and_rendered_svg"
)
# The failing node runs in the e2e lane. An arm that varies browser-lane workers varies the wrong
# knob: one retained run had pytest-browser pass outright while pytest-e2e still failed.
SUBJECT_LANE = "pytest-e2e"
ENVELOPE_WORKERS = {"browser": "5", "e2e": "3", "nonbrowser": "8"}
ENVELOPE_MODE = "parallel"

# The arm variable has exactly one owner, `service.APPEND_FLUSH_ENV_NAME`, and its name is read
# from there rather than restated. A literal copied here would keep passing after a rename, which
# is precisely how an arm variable stops reaching the subject without anything going red.
ARM_ENV_OWNER = Path("yolomux_lib/stats_current/service.py")
ARM_ENV_OWNER_CONSTANT = "APPEND_FLUSH_ENV_NAME"

# `0` selects the pre-batching synchronous owner; the batched owner's default is 10.0. BOTH arms
# state an explicit value on purpose. Unset resolves to that same 10.0 default, so an unset
# control is indistinguishable from a control whose variable never reached the container: both
# run the treatment owner while the record says control, and the experiment reports a clean null.
ARMS = {"control_synchronous": "0", "treatment_batched": "10.0"}

# statsd respawns from disk and no served response distinguishes a patched interpreter from an
# unpatched one, so the arm is proven by source identity on disk, not by behaviour.
STATSD_SOURCE_FILES = (
    Path("yolomux_lib/stats_current/storage.py"),
    Path("yolomux_lib/stats_current/service.py"),
)

# Per-test attribution crosses the container boundary through the ONE directory
# docker/run-tests.sh bind-mounts at an identical absolute path. Nothing else is forwarded, so
# nothing else can carry it back out.
ATTRIBUTION_DIR_NAME = "defect2-attempt"


# ---------------------------------------------------------------------------
# Identity: the things two arms must share before any comparison is meaningful
# ---------------------------------------------------------------------------


def pacific_now() -> str:
    return datetime.now(PACIFIC).strftime("%Y-%m-%d %H:%M:%S %Z")


def forwarded_test_env(repo_root: Path = REPO_ROOT, *, source_text: str | None = None) -> list[str]:
    """The exact allowlist docker/run-tests.sh applies, read from that one owner.

    pytest re-executes itself inside the test container and `docker run` passes through only
    these names. A variable that selects an arm and is missing here does not fail loudly: the
    node is silently skipped, both arms run the same code, and the experiment reports green
    while having measured nothing. That is the single most expensive way this experiment can
    lie, so it is checked before an attempt runs rather than discovered afterwards.
    """

    text = source_text if source_text is not None else (repo_root / "docker" / "run-tests.sh").read_text(encoding="utf-8")
    block = re.search(r"FORWARDED_TEST_ENV=\(\n(.*?)\n\)", text, re.DOTALL)
    if block is None:
        raise ValueError("docker/run-tests.sh no longer declares FORWARDED_TEST_ENV")
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def arm_env_admission(
    arm_env_name: str,
    repo_root: Path = REPO_ROOT,
    *,
    allowlist_source: str | None = None,
) -> dict[str, Any]:
    """Fail closed when the arm variable cannot reach the subject inside the container."""

    name = str(arm_env_name or "").strip()
    if not name:
        return {"admitted": False, "reason": "no_arm_env_name", "forwarded": []}
    allowlist = forwarded_test_env(repo_root, source_text=allowlist_source)
    if name not in allowlist:
        return {
            "admitted": False,
            "reason": "arm_env_not_forwarded_into_container",
            "detail": name,
            "forwarded": allowlist,
        }
    return {"admitted": True, "reason": "", "forwarded": allowlist}


def arm_env_name(service_source: str) -> str:
    """Extract the arm variable's name from its owning module's source.

    Source rather than import, so the name can be read out of a composed tree or a git ref
    without checking that tree out -- a full materialization of this repo is 68 MB.
    """

    for node in ast.walk(ast.parse(service_source)):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == ARM_ENV_OWNER_CONSTANT:
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    return node.value.value
    raise ValueError(f"{ARM_ENV_OWNER.as_posix()} does not define {ARM_ENV_OWNER_CONSTANT}")


def read_from_ref(ref: str, relative: Path, repo_root: Path = REPO_ROOT) -> str:
    """One file out of a git ref. Kilobytes, so it is usable while large I/O is frozen."""

    completed = subprocess.run(
        ["git", "show", f"{ref}:{relative.as_posix()}"],
        capture_output=True, text=True, check=False, cwd=repo_root,
    )
    if completed.returncode != 0:
        raise ValueError(f"cannot read {relative.as_posix()} from {ref}: {completed.stderr.strip()[:200]}")
    return completed.stdout


def arm_plan_violations(arms: Mapping[str, str]) -> list[str]:
    """Refuse an arm plan that cannot be told apart from a broken one."""

    violations: list[str] = []
    if len(arms) < 2:
        violations.append(f"only one arm declared: {sorted(arms)}")
    for name, value in arms.items():
        if not str(value).strip():
            violations.append(
                f"arm {name!r} leaves the variable unset, which resolves to the batched default "
                "and is indistinguishable from the variable never reaching the container"
            )
    values = [str(value) for value in arms.values()]
    if len(set(values)) < len(values):
        violations.append(f"two arms request the same value: {values}")
    return violations


def container_identity(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Tag AND content id. The tag is derived from build inputs, so two arms can share a tag
    while holding different image content if the image was rebuilt between them. Only the
    daemon's image id pins the bits that actually ran."""

    tag = subprocess.run(
        [sys.executable, str(repo_root / "tools" / "docker_image.py"), "--name"],
        capture_output=True, text=True, check=False, cwd=repo_root,
    ).stdout.strip()
    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True, text=True, check=False,
    )
    image_id = inspect.stdout.strip() if inspect.returncode == 0 else ""
    return {"tag": tag, "image_id": image_id, "image_present": bool(image_id)}


def statsd_source_identity(repo_root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    """Source and compiled identity for every file statsd respawns from.

    Size and mtime of the `.pyc` are recorded against the `.py` it was compiled from, because a
    stale cached bytecode is exactly how an arm can run the other arm's code while every source
    check passes.
    """

    records: list[dict[str, Any]] = []
    for relative in STATSD_SOURCE_FILES:
        source = repo_root / relative
        record: dict[str, Any] = {"source": relative.as_posix(), "present": source.exists()}
        if source.exists():
            stat = source.stat()
            record.update(
                source_bytes=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            )
        cache = sorted((source.parent / "__pycache__").glob(f"{source.stem}.*.pyc"))
        record["pyc"] = []
        for compiled in cache:
            stat = compiled.stat()
            record["pyc"].append({
                "path": compiled.relative_to(repo_root).as_posix(),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "stale_against_source": bool(
                    source.exists() and stat.st_mtime_ns < source.stat().st_mtime_ns
                ),
            })
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Arm equality, as executable checks that fail an attempt
# ---------------------------------------------------------------------------


def arm_equality_violations(
    records: Sequence[Mapping[str, Any]],
    *,
    arm_env_name: str,
) -> list[str]:
    """Every way two arms can stop being the same subject, stated as failures not prose.

    Arms must be artifact-equal, not source-equal. The historical pair that looked like one
    defect were the same commit SHA with the same 21 dirty paths and DIFFERENT `yolomux.js`
    (`4dda432e...` vs `3247b973...`), with `84_stats_current.js` -- the file that owns the
    watchdog -- dirty in both. The bundle triple is already recorded by the gate, so asserting
    it costs nothing and would have caught that.
    """

    violations: list[str] = []
    if not records:
        return ["no_attempts_recorded"]

    def distinct(key_path: Sequence[str]) -> list[str]:
        seen = []
        for record in records:
            value: Any = record
            for key in key_path:
                value = (value or {}).get(key) if isinstance(value, Mapping) else None
            token = json.dumps(value, sort_keys=True)
            if token not in seen:
                seen.append(token)
        return seen

    bundles = distinct(("artifacts", "generated_bundle_hashes"))
    if len(bundles) != 1:
        violations.append(f"generated_bundle_hashes differ across attempts: {bundles}")
    if bundles == ["null"]:
        violations.append("generated_bundle_hashes were never recorded")

    for label, path in (
        ("container tag", ("container", "tag")),
        ("container image id", ("container", "image_id")),
        ("head sha", ("tree", "head_sha")),
        ("worker counts", ("workers", "counts")),
        ("mode", ("workers", "mode")),
        ("subject node id", ("subject", "node_id")),
    ):
        values = distinct(path)
        if len(values) != 1:
            violations.append(f"{label} differs across attempts: {values}")

    for record in records:
        attempt = record.get("attempt_id", "?")
        for end in ("start_clean_state", "end_clean_state"):
            state = (record.get("tree") or {}).get(end) or {}
            if not state.get("observable", False):
                violations.append(f"{attempt}: {end} unobservable")
            elif not state.get("clean", False):
                violations.append(f"{attempt}: {end} is a dirty checkout, not a clean one")
        admission = (record.get("arm_env") or {})
        if admission.get("name") != arm_env_name:
            violations.append(f"{attempt}: arm variable is {admission.get('name')!r}, expected {arm_env_name!r}")
        if not admission.get("forwarded", False):
            violations.append(f"{attempt}: {arm_env_name} is not forwarded into the test container")
        if admission.get("observed_by_subject") is not True:
            violations.append(f"{attempt}: {arm_env_name} was never observed by the subject process")

    arms = {str(record.get("arm", "")) for record in records}
    if len(arms) < 2:
        violations.append(f"only one arm present: {sorted(arms)}")

    # The persistence owner is the ONLY thing allowed to differ. Anything else that varies with
    # the arm is a confound, so source identity is compared arm-by-arm rather than globally.
    by_arm: dict[str, list[str]] = {}
    for record in records:
        token = json.dumps(record.get("statsd", {}).get("source_shape"), sort_keys=True)
        by_arm.setdefault(str(record.get("arm", "")), [])
        if token not in by_arm[str(record.get("arm", ""))]:
            by_arm[str(record.get("arm", ""))].append(token)
    for arm, tokens in by_arm.items():
        if len(tokens) != 1:
            violations.append(f"arm {arm!r} did not hold one statsd source identity: {tokens}")
    return violations


def envelope_violations(record: Mapping[str, Any]) -> list[str]:
    """The envelope is the full parallel gate. A narrower one has already failed to reproduce."""

    violations: list[str] = []
    workers = (record.get("workers") or {})
    counts = {str(key): str(value) for key, value in (workers.get("counts") or {}).items()}
    if counts != ENVELOPE_WORKERS:
        violations.append(f"worker counts {counts} are not the envelope {ENVELOPE_WORKERS}")
    if str(workers.get("mode", "")) != ENVELOPE_MODE:
        violations.append(f"mode {workers.get('mode')!r} is not {ENVELOPE_MODE!r}")
    if str((record.get("subject") or {}).get("lane", "")) != SUBJECT_LANE:
        violations.append(f"subject lane is not {SUBJECT_LANE}")
    return violations


# ---------------------------------------------------------------------------
# First-transition extraction: one occurrence -> one classification
# ---------------------------------------------------------------------------


def _stall_failures(browser_failures: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        event for event in browser_failures
        if STALL_PREDICATE in str(event.get("message", ""))
    ]


def _record_status(entry: Mapping[str, Any]) -> int | None:
    """The HTTP status a server boundary record carries, or None when it names none."""

    try:
        detail = json.loads(str(entry.get("message", "")))
    except (TypeError, ValueError):
        return None
    status = detail.get("status") if isinstance(detail, Mapping) else None
    return int(status) if isinstance(status, (int, float)) else None


def _epoch_seconds(iso_text: str) -> float | None:
    try:
        return datetime.fromisoformat(str(iso_text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def first_transition_timeline(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce one failed attempt to an ordered boundary timeline and a first-bad-boundary verdict.

    `bundle` carries `browser_failures` (retained `jsDebugFailureEvents()` records, each stall one
    now carrying `streamEvidence`) and `server_logs` (the operator ring payload, whose
    `stats_stream` category holds the anomaly-only boundary records).

    The verdict is deliberately conservative: when the evidence that would separate two causes is
    absent, it says so and names what is missing rather than guessing. An extractor that guesses
    is worse than no extractor, because the guess is what gets quoted.
    """

    browser_failures = list(bundle.get("browser_failures") or [])
    server_logs = list(((bundle.get("server_logs") or {}).get("logs")) or [])
    missing: list[str] = []
    reasons: list[str] = []

    stalls = _stall_failures(browser_failures)
    if not stalls:
        return {
            "schema": TIMELINE_SCHEMA,
            "predicate": STALL_PREDICATE,
            "predicate_fired": False,
            "is_defect_2": False,
            "classifiable": False,
            "first_bad_boundary": "not_applicable",
            "timeline": [],
            "reasons": ["the stall predicate did not fire in this attempt"],
            "missing_evidence": [],
        }

    stall = stalls[0]
    evidence = stall.get("streamEvidence")
    stall_at = _epoch_seconds(stall.get("ts", ""))
    if not isinstance(evidence, Mapping):
        missing.append("streamEvidence was not attached to the stall failure")
    if stall_at is None:
        missing.append("the stall record carries no readable timestamp")

    timeline: list[dict[str, Any]] = []
    window_start: float | None = None
    if isinstance(evidence, Mapping):
        last_arrival_ms = float(evidence.get("lastDeliveryAtMs") or 0)
        window_start = last_arrival_ms / 1000.0 if last_arrival_ms > 0 else None
        timeline.append({
            "at_epoch": window_start,
            "boundary": "browser_delivery",
            "event": "last-accepted-frame",
            "detail": {
                "kind": evidence.get("lastDeliveryKind"),
                "deliverySequence": evidence.get("deliverySequence"),
                "acceptedDeltaSequence": evidence.get("acceptedDeltaSequence"),
                "streamEpoch": evidence.get("streamEpoch"),
                "streamOpen": evidence.get("streamOpen"),
                "emitMs": evidence.get("lastDeliveryEmitMs"),
                "arrivalMs": evidence.get("lastDeliveryAtMs"),
            },
        })
        if not evidence.get("lastDeliveryEmitMs"):
            missing.append("no server emit timestamp on the last delivery; transport cannot be isolated")

    # A window whose bounds do not agree is not a window. The watchdog cannot fire sooner than
    # its own three-second minimum budget, and an hour of silence is a clock mismatch between the
    # two records, not an observation. Either way the in-window filter below would quietly select
    # nothing and the verdict would read as a confident "transport" -- so refuse instead. This
    # exact mistake was made while demonstrating the extractor, which is why it is checked.
    window_seconds: float | None = None
    if window_start is not None and stall_at is not None:
        window_seconds = stall_at - window_start
        if window_seconds < 3.0:
            missing.append(
                f"implausible silence window of {window_seconds:.3f}s: the watchdog cannot fire "
                "before its three-second minimum budget, so these two records are not one episode"
            )
        elif window_seconds > 3600.0:
            missing.append(
                f"implausible silence window of {window_seconds:.0f}s: the browser arrival clock "
                "and the server log clock do not agree"
            )

    # Server boundary records that fall inside the silence window are the server's own account of
    # what it was doing while the browser saw nothing.
    in_window: list[Mapping[str, Any]] = []
    for entry in server_logs:
        if str(entry.get("category", "")) != "stats_stream":
            continue
        at = entry.get("timestamp")
        if not isinstance(at, (int, float)):
            continue
        if window_start is not None and at < window_start:
            continue
        if stall_at is not None and at > stall_at:
            continue
        in_window.append(entry)
        try:
            detail = json.loads(str(entry.get("message", "")))
        except (TypeError, ValueError):
            detail = {"raw": str(entry.get("message", ""))[:200]}
        timeline.append({
            "at_epoch": float(at),
            "boundary": str(detail.get("boundary", "unknown")),
            "event": str(entry.get("event", "")),
            "detail": detail,
        })
    if not server_logs:
        missing.append("no server log ring payload was retained; the server side is unreadable")

    timeline.append({
        "at_epoch": stall_at,
        "boundary": "browser_watchdog",
        "event": "stall-reported",
        "detail": {"message": stall.get("message"), "signature": stall.get("signature")},
    })
    timeline.sort(key=lambda item: (item["at_epoch"] is None, item["at_epoch"] or 0.0))
    for order, item in enumerate(timeline):
        item["order"] = order

    # A sibling defect makes `cache_ready_event` fire before the served window's ring is flushed,
    # so on 6 of 6 cold starts a snapshot requested at the readiness instant is legitimately
    # refused with `pending` and `retry_after_seconds: 1` for about 0.9 s. That is a real defect
    # with its own queue item and it is NOT this one. It matters here because a post-settle
    # `pending` frame routes through `routeStreamFailure`, which closes the stream -- so a statsd
    # restart inside an attempt leaves `streamOpen` false and would otherwise read as a
    # browser-side rejection. The server's own ACCEPTED is the discriminator, and because it is
    # server-side evidence rather than a client-side inference it outranks that reading.
    pending_records = [
        entry for entry in in_window
        if _record_status(entry) == 202 or str(entry.get("event", "")) == "pending"
    ]

    verdict = "unknown"
    if pending_records:
        verdict = "server_pending_restart_window"
        reasons.append(
            "the server answered ACCEPTED inside the window: statsd was refreshing, so this "
            "silence is the cold-start pending window and not the stall predicate's defect"
        )
    elif isinstance(evidence, Mapping) and evidence.get("streamOpen") is False:
        # The transport was already closed when the watchdog fired, so the browser tore this
        # stream down. That is a client-side rejection or transport error, never upstream silence.
        verdict = "client_rejection_or_transport_error"
        reasons.append("streamOpen was false at report time: this browser had already closed the stream")
    else:
        kinds = {str(entry.get("event", "")) for entry in in_window}
        if "rpc-slow" in kinds:
            verdict = "statsd_delta_rpc"
            reasons.append("a statsd delta RPC outran the cadence inside the silence window")
        elif kinds & {"tick-late", "repair", "unavailable"}:
            verdict = "frame_production"
            reasons.append(f"the server emit loop recorded {sorted(kinds)} inside the silence window")
        elif server_logs and window_start is not None and stall_at is not None:
            verdict = "transport_or_connection_closed"
            reasons.append(
                "the server recorded no boundary anomaly in the window, so it believed it was "
                "producing frames while the browser received none"
            )
        else:
            reasons.append("not enough retained evidence to separate the server from the transport")

    classifiable = verdict != "unknown" and not missing
    # A verdict must never be quoted without this flag. The predicate alone defines the scope,
    # and a legitimate restart window satisfies the predicate while being a different defect.
    is_defect_2 = verdict not in {"server_pending_restart_window", "unknown"}
    return {
        "schema": TIMELINE_SCHEMA,
        "predicate": STALL_PREDICATE,
        "predicate_fired": True,
        "is_defect_2": is_defect_2,
        "classifiable": classifiable,
        "first_bad_boundary": verdict,
        "silence_window": {"start_epoch": window_start, "end_epoch": stall_at, "seconds": window_seconds},
        "timeline": timeline,
        "reasons": reasons,
        "missing_evidence": missing,
    }


# ---------------------------------------------------------------------------
# Retention: every attempt self-describing, nothing re-derived from memory
# ---------------------------------------------------------------------------


def retention_root(explicit: str | None = None) -> Path:
    path = Path(explicit) if explicit else Path("/tmp") / "yolomux-defect2" / f"run-{time.time_ns()}"
    if not path.resolve().is_relative_to(Path("/tmp").resolve()):
        raise ValueError("defect2 retention root must be under /tmp")
    return path


def write_attempt(root: Path, record: Mapping[str, Any]) -> Path:
    """One attempt, one file, named so ordering survives without reading any of them."""

    attempts = root / "attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    name = f"{record.get('arm', 'unknown')}-{record.get('attempt_id', 'unknown')}.json"
    path = attempts / name
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = root / "MANIFEST.json"
    existing = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {
        "schema": ATTEMPT_SCHEMA,
        "task": "YOLO-V0717-E3-DEFECT2HARNESS-17",
        "predicate": STALL_PREDICATE,
        "subject": SUBJECT_NODE_ID,
        "lane": SUBJECT_LANE,
        "envelope": {"workers": ENVELOPE_WORKERS, "mode": ENVELOPE_MODE},
        "attempts": [],
    }
    existing["attempts"] = sorted(set(existing["attempts"] + [path.name]))
    existing["updated_at_pt"] = pacific_now()
    manifest.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_attempts(root: Path) -> list[dict[str, Any]]:
    attempts = root / "attempts"
    if not attempts.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(attempts.glob("*.json"))
    ]


def worker_assignment(evidence_dir: Path) -> list[dict[str, Any]]:
    """Worker-to-test assignment and in-worker order, as the attribution hook recorded it.

    Browser reuse is one Chrome per xdist worker for the whole session, so predecessor identity
    is a property of xdist sharding, not of file order. Only a per-worker record can pin it.
    """

    directory = evidence_dir / ATTRIBUTION_DIR_NAME
    if not directory.is_dir():
        return []
    # A serial run writes `worker-master.jsonl`; an xdist run writes one file per `gw*` worker and
    # the controller writes nothing. If both shapes are present the master file is a leftover from
    # an earlier serial run in the same directory, and counting it would double every row.
    files = sorted(directory.glob("worker-*.jsonl"))
    sharded = [path for path in files if path.name != "worker-master.jsonl"]
    rows: list[dict[str, Any]] = []
    for path in (sharded or files):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    rows.sort(key=lambda row: (str(row.get("worker", "")), float(row.get("start", 0.0))))
    for order, row in enumerate(rows):
        row["global_order"] = order
    by_worker: dict[str, int] = {}
    for row in rows:
        worker = str(row.get("worker", ""))
        row["worker_order"] = by_worker.get(worker, 0)
        by_worker[worker] = row["worker_order"] + 1
    return rows


def predecessors_of(rows: Sequence[Mapping[str, Any]], node_id: str) -> list[str]:
    """Everything that ran before the subject in the SAME worker, which is the same Chrome."""

    target = next((row for row in rows if str(row.get("nodeid", "")) == node_id), None)
    if target is None:
        return []
    worker = str(target.get("worker", ""))
    order = int(target.get("worker_order", 0))
    return [
        str(row.get("nodeid", ""))
        for row in rows
        if str(row.get("worker", "")) == worker and int(row.get("worker_order", 0)) < order
    ]


def enable_attribution(evidence_dir: Path) -> Path:
    """Create the directory whose existence turns the conftest attribution hook on.

    A directory rather than an environment variable on purpose: docker/run-tests.sh forwards a
    fixed allowlist, and YOLOMUX_E2E_EVIDENCE_DIR is the only channel already bind-mounted at an
    identical absolute path on both sides.
    """

    directory = evidence_dir / ATTRIBUTION_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retention-root", default="", help="retention directory under /tmp (default: a fresh one)")
    parser.add_argument("--arm-env", default="", help="override the arm variable name; default reads it from its owning constant")
    parser.add_argument("--from-ref", default="", help="resolve the allowlist and the arm variable name from this git ref instead of the worktree")
    parser.add_argument("--preflight", action="store_true", help="check arm admission and container identity, then exit")
    parser.add_argument("--extract", metavar="ATTEMPT.json", default="", help="print the boundary timeline for one retained attempt")
    parser.add_argument("--verify-arms", action="store_true", help="run the arm-equality checks over a retention root")
    args = parser.parse_args(argv)

    if args.extract:
        record = json.loads(Path(args.extract).read_text(encoding="utf-8"))
        print(json.dumps(first_transition_timeline(record.get("evidence") or {}), indent=2, sort_keys=True))
        return 0

    if args.verify_arms:
        root = retention_root(args.retention_root or None)
        records = read_attempts(root)
        violations = arm_equality_violations(records, arm_env_name=args.arm_env)
        for record in records:
            violations.extend(f"{record.get('attempt_id')}: {item}" for item in envelope_violations(record))
        for violation in violations:
            print(f"arm equality violation: {violation}")
        return 1 if violations else 0

    if args.preflight:
        # The composed tree an attempt will run from is not necessarily this worktree, and
        # materializing one is 68 MB. Reading two files out of a ref is kilobytes.
        allowlist_source = read_from_ref(args.from_ref, Path("docker/run-tests.sh")) if args.from_ref else None
        name, name_source = args.arm_env, "--arm-env"
        if not name:
            service_source = (
                read_from_ref(args.from_ref, ARM_ENV_OWNER) if args.from_ref
                else (REPO_ROOT / ARM_ENV_OWNER).read_text(encoding="utf-8")
            )
            try:
                name, name_source = arm_env_name(service_source), f"{ARM_ENV_OWNER.as_posix()}:{ARM_ENV_OWNER_CONSTANT}"
            except ValueError as error:
                name, name_source = "", str(error)
        admission = arm_env_admission(name, allowlist_source=allowlist_source)
        payload = {
            "recorded_at_pt": pacific_now(),
            "resolved_from": args.from_ref or "worktree",
            "arm_env": {"name": name, "name_source": name_source, **admission},
            "arms": dict(ARMS),
            "arm_plan_violations": arm_plan_violations(ARMS),
            "container": container_identity(),
            "statsd": {"source_shape": statsd_source_identity()},
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if admission["admitted"] and not payload["arm_plan_violations"] else 1

    parser.error("choose one of --preflight, --extract, or --verify-arms")
    return 2


if __name__ == "__main__":
    sys.exit(main())
