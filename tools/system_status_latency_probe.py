#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Standalone authenticated system-status representation-ready latency probe."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import platform
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.error

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.infra.host_identity import process_identity_snapshot
from yolomux_lib.infra.host_identity import process_start_identity
from yolomux_lib.infra.listener_census import canonical_listener_pids
from yolomux_lib.infra.listener_census import listener_pids
from yolomux_lib.infra.listener_census import require_unique_listener_pid
from yolomux_lib.infra.root_paths import resolved_product_path
from yolomux_lib.local_services.registry import darwin_process_environment
from yolomux_lib.tmux.sessions import process_cwd
from tools.instance_isolation import resolve_instance_environment

WARMUPS = 20
SAMPLES = 200
BUDGET_MS = 20.0
HEADER = "X-YOLOmux-Measurement"
METRIC = "route_to_representation_ready_ms"


def process_parent_pid(pid: int) -> int:
    if platform.system() == "Darwin":
        completed = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        value = completed.stdout.strip()
        return int(value) if completed.returncode == 0 and value.isdigit() else 0
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return 0
    fields = stat.rpartition(")")[2].split()
    return int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 0


def process_command(pid: int) -> str:
    if platform.system() == "Darwin":
        completed = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def canonicalized_listener_pids(port: int) -> list[int]:
    return canonical_listener_pids(
        listener_pids(port),
        parent_reader=process_parent_pid,
        command_reader=process_command,
    )


def process_environment(pid: int) -> dict[str, str]:
    try:
        items = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        items = darwin_process_environment(pid)
    return {key.decode(): value.decode() for item in items if b"=" in item for key, value in [item.split(b"=", 1)]}


def process_identity(pid: int) -> dict[str, object]:
    start_identity = process_start_identity(pid)
    cwd = process_cwd(pid)
    sha_result = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
    sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""
    identity = {"pid": pid, "start_identity": start_identity, "cwd": cwd, "sha": sha}
    if any(value in (None, "") for value in identity.values()):
        raise RuntimeError(f"listener identity is incomplete: {identity}")
    return identity


def config_dir_from_process(environ: dict[str, str], port: int | None = None) -> Path:
    root = environ.get("YOLOMUX_ROOT", "").strip()
    configured = environ.get("YOLOMUX_CONFIG_DIR", "").strip()
    if root:
        root_path = resolved_product_path(environ, "YOLOMUX_ROOT", root).resolve(strict=True)
        config = resolved_product_path(environ, "YOLOMUX_CONFIG_DIR", root_path / "config").resolve(strict=True)
        if not config.resolve(strict=True).is_relative_to(root_path):
            raise RuntimeError("listener config root escapes YOLOMUX_ROOT")
        return config
    if not configured:
        resolution = resolve_instance_environment(port, environ, platform=platform.system())
        root = resolution.environment.get("YOLOMUX_ROOT", "")
        if not root:
            raise RuntimeError("listener process exposes no authoritative config root")
        return Path(root).resolve(strict=True) / "config"
    return resolved_product_path(environ, "YOLOMUX_CONFIG_DIR", configured).resolve(strict=True)


def auth_cookie(config_dir: Path, port: int) -> str:
    text = (config_dir / "auth.yaml").read_text(encoding="utf-8")
    blocks = text.split("  - ")[1:]
    selected: tuple[str, str] | None = None
    for block in blocks:
        values = {}
        for line in block.splitlines():
            key, separator, value = line.strip().partition(":")
            if separator:
                values[key] = value.strip().strip('"').strip("'")
        if values.get("username") and (values.get("password_hash") or values.get("password")):
            candidate = (values["username"], values.get("password_hash") or values["password"])
            if values.get("role") == "admin":
                selected = candidate
                break
            selected = selected or candidate
    if selected is None:
        raise RuntimeError("listener root has no configured auth user")
    secret = bytes.fromhex((config_dir / "auth-cookie-secret").read_text(encoding="utf-8").strip())
    value = hmac.new(secret, f"{selected[0]}:{selected[1]}".encode(), hashlib.sha256).hexdigest()
    return f"yolomux_auth_{port}={value}"


def get_json(url: str, cookie: str, marker: str = "") -> tuple[int, object]:
    headers = {"Cookie": cookie, "Connection": "close"}
    if marker:
        headers[HEADER] = marker
    request = urllib.request.Request(url, headers=headers)
    context = ssl._create_unverified_context() if url.startswith("https:") else None
    try:
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def nearest_rank(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(1, math.ceil(len(ordered) * fraction)) - 1]


def validate_final_listener_owner(port: int, pid: int, before: dict[str, object]) -> None:
    """Classify process exit/reuse separately from listener absence or takeover."""

    expected_start = str(before["start_identity"])

    def require_original_process(phase: str) -> None:
        snapshot = process_identity_snapshot(pid)
        if snapshot is None:
            raise RuntimeError(
                f"listener owner process exited or became unobservable {phase}: pid {pid}"
            )
        if snapshot.state == "Z":
            raise RuntimeError(f"listener owner process exited {phase}: pid {pid}")
        if snapshot.start_identity != expected_start:
            raise RuntimeError(
                f"listener owner process identity changed {phase}: pid {pid} "
                f"{expected_start} != {snapshot.start_identity}"
            )

    require_original_process("before final listener census")
    after_pids = canonicalized_listener_pids(port)
    require_original_process("after final listener census")
    if after_pids == [pid]:
        return
    if not after_pids:
        raise RuntimeError(f"listener absent while original process remains alive: pid {pid}")
    if len(after_pids) == 1:
        raise RuntimeError(f"listener takeover during run: pid {pid} -> {after_pids[0]}")
    raise RuntimeError(f"multiple listener owners after run: expected pid {pid}, found {after_pids}")


def run(port: int, scheme: str, output: Path) -> bool:
    pid = require_unique_listener_pid(port, canonicalized_listener_pids(port))
    before = process_identity(pid)
    cookie = auth_cookie(config_dir_from_process(process_environment(pid), port), port)
    base = f"{scheme}://127.0.0.1:{port}"
    for _ in range(WARMUPS):
        status, body = get_json(base + "/api/system-status", cookie)
        if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("warmup returned unavailable/error response")
    ids = []
    round_trips = []
    for index in range(SAMPLES):
        marker = "capture-" + secrets.token_hex(16)
        ids.append(hashlib.sha256(marker.encode()).hexdigest()[:16])
        started = time.perf_counter()
        status, body = get_json(base + "/api/system-status", cookie, marker)
        round_trips.append((time.perf_counter() - started) * 1000)
        if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError(f"sample {index} returned unavailable/error response")
    status, payload = get_json(base + "/api/diagnostics/performance?measurement_scope=capture", cookie)
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError("capture-scoped performance retrieval failed")
    records = payload.get("perf", {}).get("recent", [])
    by_id = {}
    for record in records:
        request_id = record.get("details", {}).get("measurement_request_id") if isinstance(record, dict) else ""
        if request_id in by_id:
            raise RuntimeError(f"duplicate server record {request_id}")
        if request_id:
            by_id[request_id] = record
    values = []
    for request_id in ids:
        record = by_id.get(request_id)
        value = record.get("details", {}).get(METRIC) if record else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise RuntimeError(f"missing measured record {request_id}")
        values.append(float(value))
    validate_final_listener_owner(port, pid, before)
    after = process_identity(pid)
    if before != after:
        raise RuntimeError(f"listener identity drifted: {before} != {after}")
    p99 = nearest_rank(values, 0.99)
    artifact = {"identity": before, "warmups": WARMUPS, "samples": SAMPLES, "acceptance_outcome": {"ok": p99 < BUDGET_MS, "p99_ms": p99, "budget_ms": BUDGET_MS}, "raw": {METRIC: values, "client_round_trip_ms": round_trips}}
    text = json.dumps(artifact, indent=2, sort_keys=True)
    output.write_text(text, encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(hashlib.sha256(text.encode()).hexdigest() + "\n", encoding="utf-8")
    return bool(artifact["acceptance_outcome"]["ok"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--scheme", choices=("http", "https"), default="https")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return 0 if run(args.port, args.scheme, args.output) else 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
