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
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.error

WARMUPS = 20
SAMPLES = 200
BUDGET_MS = 20.0
HEADER = "X-YOLOmux-Measurement"
METRIC = "route_to_representation_ready_ms"


def listener_pids(port: int) -> list[int]:
    inodes = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            rows = table.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if len(fields) > 9 and int(fields[1].rsplit(":", 1)[1], 16) == port and fields[3] == "0A":
                inodes.add(fields[9])
    pids = set()
    for process_dir in Path("/proc").iterdir():
        if not process_dir.name.isdigit():
            continue
        try:
            targets = (os.readlink(entry) for entry in (process_dir / "fd").iterdir())
            if any(target.removeprefix("socket:[").removesuffix("]") in inodes for target in targets):
                pids.add(int(process_dir.name))
        except (OSError, PermissionError):
            continue
    return sorted(pids)


def process_environment(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    return {key.decode(): value.decode() for item in raw.split(b"\0") if b"=" in item for key, value in [item.split(b"=", 1)]}


def process_identity(pid: int) -> dict[str, object]:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    fields = stat[stat.rfind(")") + 1 :].split()
    cwd = str(Path(f"/proc/{pid}/cwd").resolve(strict=True))
    sha_result = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
    sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""
    identity = {"pid": pid, "start_ticks": int(fields[19]), "cwd": cwd, "sha": sha}
    if any(value in (None, "") for value in identity.values()):
        raise RuntimeError(f"listener identity is incomplete: {identity}")
    return identity


def config_dir_from_process(environ: dict[str, str]) -> Path:
    root = environ.get("YOLOMUX_ROOT", "").strip()
    configured = environ.get("YOLOMUX_CONFIG_DIR", "").strip()
    if root:
        root_path = Path(root).resolve(strict=True)
        config = Path(configured).resolve(strict=True) if configured else root_path / "config"
        if not config.resolve(strict=True).is_relative_to(root_path):
            raise RuntimeError("listener config root escapes YOLOMUX_ROOT")
        return config
    if not configured:
        raise RuntimeError("listener process exposes no authoritative config root")
    return Path(configured).resolve(strict=True)


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


def run(port: int, scheme: str, output: Path) -> bool:
    pids = listener_pids(port)
    if len(pids) != 1:
        raise RuntimeError(f"port {port} must have exactly one listener; found {pids or 'none'}")
    pid = pids[0]
    before = process_identity(pid)
    cookie = auth_cookie(config_dir_from_process(process_environment(pid)), port)
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
    after_pids = listener_pids(port)
    if after_pids != [pid]:
        raise RuntimeError(f"listener ownership changed during run: {[pid]} != {after_pids or 'none'}")
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
