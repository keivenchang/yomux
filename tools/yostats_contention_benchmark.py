#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Capture a bounded, read-only YOLOmux CPU/batchd contention window.

The harness deliberately does not drive a browser or refresh any API. Run it while
the operator keeps the target browser state open, then compare its start/end batchd
counters and per-PID CPU samples with the browser request/long-task capture.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.batchd import BatchClient
from tools.yostats_capture_common import positive_float, positive_int, process_cpu_seconds


def process_cpu_percent(pid: int) -> float | None:
    if pid <= 0:
        return None
    # A slow ps under the very load being measured is a missed sample, not a
    # benchmark crash (a mid-capture crash orphaned this child on 2026-07-19).
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu="],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        return max(0.0, float(result.stdout.strip()))
    except ValueError:
        return None


def process_snapshot(pids: dict[str, int]) -> dict[str, dict[str, float | None]]:
    return {
        name: {
            "ps_cpu_percent": process_cpu_percent(pid),
            "cpu_time_seconds": process_cpu_seconds(pid),
        }
        for name, pid in sorted(pids.items())
    }


def cpu_time_delta_percent(
    first: dict[str, dict[str, float | None]],
    last: dict[str, dict[str, float | None]],
    elapsed_seconds: float,
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for name, first_row in first.items():
        start = first_row.get("cpu_time_seconds")
        end = last.get(name, {}).get("cpu_time_seconds")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or elapsed_seconds <= 0:
            result[name] = None
            continue
        result[name] = round(max(0.0, float(end) - float(start)) * 100.0 / elapsed_seconds, 3)
    return result


def bounded_batchd_status() -> dict[str, Any]:
    status = BatchClient().runtime_status()
    return {
        "healthy": bool(status.get("healthy")),
        "pid": int(status.get("pid") or 0),
        "worker_pids": [int(value) for value in status.get("worker_pids", []) if isinstance(value, int)],
        "product_counters": status.get("product_counters") if isinstance(status.get("product_counters"), dict) else {},
        "product_runtime_ms": status.get("product_runtime_ms") if isinstance(status.get("product_runtime_ms"), dict) else {},
        "product_phase_runtime_ms": status.get("product_phase_runtime_ms") if isinstance(status.get("product_phase_runtime_ms"), dict) else {},
        "product_work_totals": status.get("product_work_totals") if isinstance(status.get("product_work_totals"), dict) else {},
        "session_files_accepted_requester_counters": status.get("session_files_accepted_requester_counters") if isinstance(status.get("session_files_accepted_requester_counters"), dict) else {},
        "session_files_requester_counters": status.get("session_files_requester_counters") if isinstance(status.get("session_files_requester_counters"), dict) else {},
        "request_counters": status.get("request_counters") if isinstance(status.get("request_counters"), dict) else {},
        "source_change_counters": status.get("source_change_counters") if isinstance(status.get("source_change_counters"), dict) else {},
        "last_failure": str(status.get("last_failure") or ""),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture a read-only YOLOmux CPU/batchd contention window")
    parser.add_argument("--duration", type=positive_float, default=60.0, help="capture duration in seconds (default: 60)")
    parser.add_argument("--interval", type=positive_float, default=1.0, help="CPU sample interval in seconds (default: 1)")
    parser.add_argument("--web-pid", type=positive_int, required=True, help="active yolomux.py web PID")
    parser.add_argument("--indexer-pid", type=positive_int, default=0, help="optional indexd PID")
    parser.add_argument("--statsd-pid", type=positive_int, default=0, help="optional statsd PID")
    parser.add_argument("--output", type=Path, required=True, help="JSON report path; use /tmp for transient captures")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = time.time()
    batchd_start = bounded_batchd_status()
    pids = {
        "web": args.web_pid,
        "indexd": args.indexer_pid,
        "statsd": args.statsd_pid,
        "batchd": int(batchd_start["pid"]),
    }
    for index, pid in enumerate(batchd_start["worker_pids"]):
        pids[f"batchd-worker-{index}"] = pid
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + args.duration
    while True:
        samples.append({"time": time.time(), "cpu_percent": process_snapshot(pids)})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.interval, remaining))
    batchd_end = bounded_batchd_status()
    ended_at = time.time()
    final_cpu = process_snapshot(pids)
    payload = {
        "version": 1,
        "started_at": started_at,
        "ended_at": ended_at,
        "requested_duration_seconds": args.duration,
        "interval_seconds": args.interval,
        "pids": pids,
        "batchd_start": batchd_start,
        "batchd_end": batchd_end,
        "samples": samples,
        "final_cpu": final_cpu,
        "cpu_time_delta_percent": cpu_time_delta_percent(samples[0]["cpu_percent"], final_cpu, ended_at - started_at),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {len(samples)} samples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
