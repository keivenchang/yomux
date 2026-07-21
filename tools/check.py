#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Fast local check runner for YOLOmux.

The default run starts independent lanes in parallel so agents and humans do not
serialize py_compile, static checks, Node checks, full pytest, and whitespace
checks by hand. Use --serial when debugging order or when interleaved process
load makes a failure hard to read. Focused pytest lanes are available with
--lane, but the default gate keeps the old full-pytest behavior.

Usage:
  python3 tools/check.py
  python3 tools/check.py --serial
  python3 tools/check.py --lane pytest-boot
  python3 tools/check.py --no-tool-guard
"""

from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import contextmanager
import fcntl
import json
import os
import platform
import re
import resource
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.background_owner import pid_is_alive
from yolomux_lib.filesystem.io_ops import read_json_file
from tools.test_catalog import PYTEST_PHASE_FILES  # noqa: F401 - check-runner compatibility export
from tools.test_catalog import pytest_files

DEFAULT_TOOL_LOCK_PATH = Path(
    os.environ.get("YOLOMUX_TOOL_LOCK_PATH", str(Path.home() / ".cache" / "yolomux" / "expensive-tools.lock"))
).expanduser()
TOOL_GUARD_STATE_STALE_SECONDS = 30.0
TOOL_GUARD_NICE_DELTA = 5
EXPENSIVE_TOOL_LANES = frozenset({"node-layout", "pytest", "pytest-boot", "pytest-browser", "pytest-e2e"})


class ToolGuardBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class Step:
    label: str
    args: list[str]


@dataclass(frozen=True)
class Lane:
    name: str
    label: str
    steps: tuple[Step, ...]
    default: bool = False


@dataclass(frozen=True)
class LaneResult:
    name: str
    label: str
    ok: bool
    seconds: float
    output: str
    steps: tuple["StepResult", ...] = ()


@dataclass(frozen=True)
class StepResult:
    """One completed command within a check lane."""

    label: str
    command: str
    seconds: float
    returncode: int
    slow_tests: tuple[dict[str, object], ...] = ()


def py_compile_files() -> list[str]:
    return [
        "yolomux.py",
        "tools/tmux_wall.py",
        "tools/auto_approve_tmux.py",
        "tools/yostats_contention_benchmark.py",
        "tools/yostats_active_browser_window.py",
        *sorted(str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "yolomux_lib").rglob("*.py")),
    ]


def check_cpu_percent(cpu_percent: int | None = None) -> int:
    """Fraction of host CPUs the pytest pools may claim, 1-100.

    Precedence: explicit --cpu-percent, then YOLOMUX_CHECK_CPU_PERCENT, then
    the platform default. Linux takes every core; macOS defaults to half
    because Chrome trees plus Defender/Spotlight scanning of temporary
    profiles double the effective per-worker cost there.
    """
    raw = str(cpu_percent) if cpu_percent is not None else os.environ.get("YOLOMUX_CHECK_CPU_PERCENT", "").strip()
    if raw:
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            raise ValueError("CPU percent must be an integer 1-100")
        return int(raw)
    return 50 if platform.system() == "Darwin" else 100


def pytest_worker_counts(*, serial: bool = False, cpu_percent: int | None = None) -> tuple[str, str, str]:
    """Divide a core-derived concurrent worker budget across pytest pools."""
    if serial:
        return "1", "1", "1"
    override = os.environ.get("YOLOMUX_PYTEST_WORKERS", "").strip()
    if override:
        parts = [part.strip() for part in override.split(",")]
        if len(parts) == 1 and parts[0].isdigit() and int(parts[0]) > 0:
            return parts[0], parts[0], parts[0]
        if len(parts) == 3 and all(part.isdigit() and int(part) > 0 for part in parts):
            return parts[0], parts[1], parts[2]
        raise ValueError("YOLOMUX_PYTEST_WORKERS must be N or nonbrowser,browser,e2e")
    cpus = max(1, os.cpu_count() or 1)
    percent = check_cpu_percent(cpu_percent)
    # The three pools run together and split one budget 1/2 non-browser,
    # 1/3 browser, remainder E2E; the floor of 3 keeps every pool alive.
    budget = max(3, (cpus * percent) // 100)
    nonbrowser = max(1, budget // 2)
    browser = max(1, budget // 3)
    e2e = max(1, budget - nonbrowser - browser)
    return str(nonbrowser), str(browser), str(e2e)


def pytest_xdist_args(workers: str, *, serial: bool = False, worksteal: bool = False) -> list[str]:
    if serial:
        return []
    args = ["-n", workers]
    if worksteal:
        args.extend(["--dist", "worksteal"])
    return args


def lanes(*, serial: bool = False, cpu_percent: int | None = None) -> list[Lane]:
    nonbrowser_workers, browser_workers, e2e_workers = pytest_worker_counts(serial=serial, cpu_percent=cpu_percent)
    return [
        Lane(
            "py-compile",
            "py_compile",
            (Step("py_compile", ["python3", "-m", "py_compile", *py_compile_files()]),),
            True,
        ),
        Lane(
            "static",
            "static_build --check",
            (Step("static_build --check", ["python3", "tools/static_build.py", "--check"]),),
            True,
        ),
        Lane(
            "node-syntax",
            "node syntax",
            (
                Step("node --check static/yolomux.js", ["node", "--check", "static/yolomux.js"]),
                Step("node --check static/tmux-wall.js", ["node", "--check", "static/tmux-wall.js"]),
            ),
            True,
        ),
        Lane(
            "node-layout",
            "node layout suite",
            (Step("node tests/layout_url.test.js", ["node", "tests/layout_url.test.js"]),),
            True,
        ),
        Lane(
            "pytest",
            "pytest non-browser",
            # Exclude node_bridge: test_node_suite.py shells out to `node tests/layout_url.test.js`,
            # the exact command the always-on node-layout lane already runs. Without this, the gate
            # runs that ~20s node suite twice concurrently (it was the single slowest pytest item)
            # and the two node processes thrash the cores the browser workers need. The node-layout
            # lane keeps node coverage in the default gate; a bare `python3 -m pytest tests` still
            # runs the bridge for anyone not going through check.py.
            # Exclude e2e too: end-to-end tests launch real tmux + mock agents + a TmuxWebtermApp and are
            # an order of magnitude slower; they run as their own parallel `pytest-e2e` lane (below) so a
            # fast unit failure surfaces immediately and the two pools do not thrash each other's cores.
            # Exclude browser too: Selenium tests are a separate `pytest-browser` lane so browser-only
            # script errors and timing flakes do not hide inside the generic pytest lane.
            (Step("pytest non-browser", ["python3", "-m", "pytest", *pytest_files("nonbrowser"), *pytest_xdist_args(nonbrowser_workers, serial=serial), "-m", "not node_bridge and not e2e and not browser", "-q"]),),
            True,
        ),
        Lane(
            "pytest-boot",
            "pytest boot smoke",
            (Step("pytest boot smoke", ["python3", "-m", "pytest", *pytest_files("boot"), "-m", "boot", "-q"]),),
        ),
        Lane(
            "pytest-browser",
            "pytest browser",
            (
                Step("pytest boot smoke", ["python3", "-m", "pytest", *pytest_files("boot"), "-m", "boot", "-q"]),
                # Browser durations vary enough that xdist's initial load assignment leaves a long
                # tail even with valid slow-first hints. Two same-code A/B pairs made work stealing
                # repeatably faster, while boot smoke stays serial and separate above. Local visual
                # goldens compare rendered pixels against reviewed machine baselines, so they run in
                # a separate serial step: parallel Chrome font/compositor pressure can otherwise add
                # enough non-product raster variation to cross the RMS threshold.
                Step("pytest browser", ["python3", "-m", "pytest", *pytest_files("browser"), *pytest_xdist_args(browser_workers, serial=serial, worksteal=True), "-m", "browser and not e2e and not boot and not visual_golden", "-q"]),
                Step("pytest browser visual goldens", ["python3", "-m", "pytest", *pytest_files("golden"), "-m", "visual_golden", "-q"]),
            ),
            True,
        ),
        Lane(
            "pytest-browser-behavior",
            "pytest browser behavior",
            (
                Step("pytest browser", ["python3", "-m", "pytest", *pytest_files("browser"), *pytest_xdist_args(browser_workers, serial=serial, worksteal=True), "-m", "browser and not e2e and not boot and not visual_golden", "-q"]),
            ),
        ),
        Lane(
            "pytest-e2e",
            "pytest e2e",
            # End-to-end auto-approve etc.: real tmux + claude.py/codex.py --mock + AutoApproveWorker. Keep this pool
            # bounded: `-n auto` can launch dozens of tmux/mock-agent subprocesses while the browser and
            # unit pools are also running, which slows the whole default gate down and makes flakes harder
            # to diagnose.
            (Step("pytest e2e", ["python3", "-m", "pytest", *pytest_files("e2e"), *pytest_xdist_args(e2e_workers, serial=serial), "-m", "e2e", "-q"]),),
            True,
        ),
        Lane(
            "pytest-unit",
            "pytest unit",
            (
                Step(
                    "pytest unit",
                    [
                        "python3",
                        "-m",
                        "pytest",
                        "tests",
                        "--ignore=tests/test_browser_layout.py",
                        "-m",
                        "not socket and not browser and not node_bridge",
                        "-q",
                    ],
                ),
            ),
        ),
        Lane(
            "pytest-socket",
            "pytest socket",
            (
                Step(
                    "pytest socket",
                    [
                        "python3",
                        "-m",
                        "pytest",
                        "tests",
                        "--ignore=tests/test_browser_layout.py",
                        "-m",
                        "socket and not browser",
                        "-q",
                    ],
                ),
            ),
        ),
        Lane(
            "whitespace",
            "git diff --check",
            (Step("git diff --check", ["git", "diff", "--check"]),),
            True,
        ),
    ]


def command_text(args: list[str]) -> str:
    return shlex.join(args)


def state_dir_from_env() -> Path:
    return Path(os.environ.get("YOLOMUX_STATE_DIR", str(Path.home() / ".local" / "state" / "yolomux")))


def active_yolomux_server_records(
    *,
    state_dir: Path | None = None,
    now: float | None = None,
    stale_seconds: float = TOOL_GUARD_STATE_STALE_SECONDS,
) -> list[dict[str, object]]:
    root = Path(state_dir) if state_dir is not None else state_dir_from_env()
    generations_dir = root / "background-owner" / "generations"
    timestamp = time.time() if now is None else float(now)
    try:
        paths = sorted(generations_dir.glob("*.json"))
    except OSError:
        return []
    records: list[dict[str, object]] = []
    for path in paths:
        record = read_json_file(path, None, exceptions=(OSError, json.JSONDecodeError))
        if record is None:
            continue
        if not isinstance(record, dict):
            continue
        try:
            pid = int(record.get("pid") or 0)
            heartbeat = float(record.get("last_heartbeat") or 0.0)
        except (TypeError, ValueError):
            continue
        if not pid_is_alive(pid):
            continue
        if heartbeat <= 0.0 or timestamp - heartbeat > stale_seconds:
            continue
        records.append(record)
    return records


def lower_current_process_priority(active_records: list[dict[str, object]], *, nice_delta: int = TOOL_GUARD_NICE_DELTA) -> bool:
    if not active_records or nice_delta <= 0:
        return False
    try:
        os.nice(nice_delta)
    except OSError:
        return False
    return True


def selected_needs_tool_guard(selected: list[Lane], explicit_lane_names: list[str] | None) -> bool:
    selected_names = {lane.name for lane in selected}
    if explicit_lane_names is None:
        return True
    return bool(selected_names & EXPENSIVE_TOOL_LANES)


@contextmanager
def expensive_tool_lock(enabled: bool = True, lock_path: Path = DEFAULT_TOOL_LOCK_PATH):
    if not enabled:
        yield False
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ToolGuardBusy(f"another expensive YOLOmux check already owns {lock_path}") from exc
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_lane(lane: Lane) -> LaneResult:
    started = time.monotonic()
    chunks: list[str] = []
    step_results: list[StepResult] = []
    ok = True
    for step in lane.steps:
        chunks.append(f"$ {command_text(step.args)}\n")
        step_started = time.monotonic()
        result = subprocess.run(step.args, cwd=REPO_ROOT, capture_output=True, text=True)
        step_results.append(StepResult(step.label, command_text(step.args), time.monotonic() - step_started, result.returncode, pytest_slowest_calls(result.stdout)))
        if result.stdout:
            chunks.append(result.stdout)
            if not result.stdout.endswith("\n"):
                chunks.append("\n")
        if result.stderr:
            chunks.append(result.stderr)
            if not result.stderr.endswith("\n"):
                chunks.append("\n")
        if result.returncode != 0:
            chunks.append(f"exit {result.returncode}: {step.label}\n")
            ok = False
            break
    seconds = time.monotonic() - started
    return LaneResult(lane.name, lane.label, ok, seconds, "".join(chunks), tuple(step_results))


def child_usage_snapshot() -> dict[str, float | int | str]:
    """Return portable aggregate direct-child accounting for an entire gate."""

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "user_seconds": usage.ru_utime,
        "system_seconds": usage.ru_stime,
        "max_rss": usage.ru_maxrss,
        "max_rss_unit": "bytes" if platform.system() == "Darwin" else "KiB",
    }


_PYTEST_SLOW_CALL_RE = re.compile(r"^\s*([0-9.]+)s\s+(?:call|setup|teardown)\s+(.+)$", re.MULTILINE)


def pytest_slowest_calls(output: str, *, limit: int = 10) -> tuple[dict[str, object], ...]:
    """Extract bounded pytest duration rows when a performance run requested them."""

    return tuple(
        {"seconds": float(seconds), "nodeid": nodeid.strip()}
        for seconds, nodeid in _PYTEST_SLOW_CALL_RE.findall(output)[:limit]
    )


def instrument_lane_for_performance(lane: Lane) -> Lane:
    """Ask pytest for a bounded timing table only in opt-in reporting mode."""

    steps = tuple(
        Step(step.label, [*step.args, "--durations=10"] if step.args[:3] == ["python3", "-m", "pytest"] else step.args)
        for step in lane.steps
    )
    return Lane(lane.name, lane.label, steps, lane.default)


def child_usage_delta(before: dict[str, float | int | str], after: dict[str, float | int | str]) -> dict[str, float | int | str]:
    """Calculate gate child CPU totals; RSS remains the high-water mark."""

    return {
        "user_seconds": round(float(after["user_seconds"]) - float(before["user_seconds"]), 6),
        "system_seconds": round(float(after["system_seconds"]) - float(before["system_seconds"]), 6),
        "max_rss": after["max_rss"],
        "max_rss_unit": after["max_rss_unit"],
    }


def performance_report_payload(*, selected: list[Lane], results: list[LaneResult], serial: bool, elapsed: float, child_usage: dict[str, float | int | str], interrupted: bool = False, cpu_percent: int | None = None) -> dict[str, object]:
    """Create stable opt-in machine output without adding noise to normal checks."""

    worker_counts = dict(zip(("nonbrowser", "browser", "e2e"), pytest_worker_counts(serial=serial, cpu_percent=cpu_percent), strict=True))
    return {
        "schema": 1,
        "interrupted": interrupted,
        "mode": "serial" if serial else "parallel",
        "cpu_percent": None if serial else check_cpu_percent(cpu_percent),
        "wall_seconds": round(elapsed, 6),
        "pytest_workers": worker_counts,
        "child_usage": child_usage,
        "lanes": [
            {
                "name": result.name,
                "label": result.label,
                "ok": result.ok,
                "wall_seconds": round(result.seconds, 6),
                "steps": [
                    {
                        "label": step.label,
                        "command": step.command,
                        "wall_seconds": round(step.seconds, 6),
                        "returncode": step.returncode,
                        "slow_tests": step.slow_tests,
                    }
                    for step in result.steps
                ],
            }
            for result in results
        ],
        "selected_lanes": [lane.name for lane in selected],
    }


def performance_report_path(value: str) -> Path:
    """Limit raw machine evidence to /tmp, never the source tree or docs."""

    path = Path(value) if value else Path("/tmp") / f"yolomux-check-{os.getpid()}.json"
    resolved = path.resolve()
    tmp_root = Path("/tmp").resolve()
    if not resolved.is_relative_to(tmp_root):
        raise ValueError("--performance-report must be under /tmp")
    # Keep the caller-visible `/tmp/...` spelling on macOS, where resolving the path turns it
    # into `/private/tmp/...`; the resolved value above remains the security check.
    return path


def write_performance_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_result(result: LaneResult) -> None:
    state = "PASS" if result.ok else "FAIL"
    print(f"{state}: {result.label} ({result.seconds:.2f}s)", flush=True)
    if not result.ok or "WARNING:" in result.output:
        print(result.output, end="" if result.output.endswith("\n") else "\n", flush=True)


# Launch order by expected wall-clock, slowest first: the long-pole lanes
# (Selenium browser, then e2e, then the pytest pools) must start while the
# machine is unloaded so the gate's makespan is the longest lane, not the
# longest lane plus whatever queued ahead of it. Unknown lanes sort last.
LANE_LAUNCH_ORDER = (
    "pytest-browser",
    "pytest-browser-behavior",
    "pytest-e2e",
    "pytest",
    "pytest-unit",
    "pytest-socket",
    "pytest-boot",
    "node-layout",
    "static",
    "node-syntax",
    "py-compile",
    "whitespace",
)


def slowest_first(selected: list[Lane]) -> list[Lane]:
    rank = {name: index for index, name in enumerate(LANE_LAUNCH_ORDER)}
    return sorted(selected, key=lambda lane: rank.get(lane.name, len(rank)))


def run_parallel(selected: list[Lane]) -> list[LaneResult]:
    results: list[LaneResult] = []
    workers = min(len(selected), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_lane = {executor.submit(run_lane, lane): lane for lane in selected}
        for future in concurrent.futures.as_completed(future_to_lane):
            result = future.result()
            results.append(result)
            print_result(result)
    return results


def run_serial(selected: list[Lane]) -> list[LaneResult]:
    results = []
    for lane in selected:
        result = run_lane(lane)
        results.append(result)
        print_result(result)
    return results


def main(argv: list[str] | None = None) -> int:
    available = lanes()
    lane_names = [lane.name for lane in available]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", action="store_true", help="run lanes one at a time instead of in parallel")
    parser.add_argument("--cpu-percent", type=int, default=None, metavar="1-100", help="fraction of host CPUs the pytest pools may claim (default: 100 on Linux, 50 on macOS; env YOLOMUX_CHECK_CPU_PERCENT)")
    parser.add_argument("--lane", action="append", choices=lane_names, help="run only this lane; may be repeated")
    parser.add_argument("--list-lanes", action="store_true", help="print lane names and exit")
    parser.add_argument("--no-tool-guard", action="store_true", help="skip the expensive-tool lock and live-server priority lowering")
    parser.add_argument("--performance-report", nargs="?", const="", metavar="/tmp/REPORT.json", help="write opt-in timing and child-resource JSON under /tmp")
    args = parser.parse_args(argv)

    try:
        check_cpu_percent(args.cpu_percent)
    except ValueError as exc:
        parser.error(str(exc))

    if args.serial or args.cpu_percent is not None:
        available = lanes(serial=args.serial, cpu_percent=args.cpu_percent)

    if args.list_lanes:
        for lane in available:
            print(f"{lane.name}\t{lane.label}")
        return 0

    selected_names = set(args.lane or [lane.name for lane in available if lane.default])
    selected = slowest_first([lane for lane in available if lane.name in selected_names])
    if not selected:
        print("no lanes selected", file=sys.stderr)
        return 2

    try:
        report_path = performance_report_path(args.performance_report) if args.performance_report is not None else None
    except ValueError as exc:
        print(f"CHECK REFUSED: {exc}", file=sys.stderr, flush=True)
        return 2
    if report_path is not None:
        selected = [instrument_lane_for_performance(lane) for lane in selected]

    guard_enabled = selected_needs_tool_guard(selected, args.lane) and not args.no_tool_guard
    if guard_enabled:
        print(f"Acquiring YOLOmux expensive-tool lock: {DEFAULT_TOOL_LOCK_PATH}", flush=True)
    started = time.monotonic()
    usage_before = child_usage_snapshot()
    results: list[LaneResult] = []
    try:
        with expensive_tool_lock(enabled=guard_enabled):
            if guard_enabled:
                active_records = active_yolomux_server_records()
                if lower_current_process_priority(active_records):
                    ports = sorted({str(record.get("port") or "?") for record in active_records})
                    print(f"Detected {len(active_records)} active YOLOmux server(s) on port(s) {', '.join(ports)}; lowered check priority by nice +{TOOL_GUARD_NICE_DELTA}", flush=True)
            mode = "serial" if args.serial else "parallel"
            print(f"Running {len(selected)} check lane(s) in {mode}: {', '.join(lane.name for lane in selected)}", flush=True)
            results = run_serial(selected) if args.serial else run_parallel(selected)
            elapsed = time.monotonic() - started
    except ToolGuardBusy as exc:
        print(f"CHECK REFUSED: {exc}", file=sys.stderr, flush=True)
        return 3
    except KeyboardInterrupt:
        elapsed = time.monotonic() - started
        print("CHECK INTERRUPTED", file=sys.stderr, flush=True)
        if report_path is not None:
            write_performance_report(report_path, performance_report_payload(selected=selected, results=results, serial=args.serial, elapsed=elapsed, child_usage=child_usage_delta(usage_before, child_usage_snapshot()), interrupted=True, cpu_percent=args.cpu_percent))
            print(f"Performance report: {report_path}", file=sys.stderr, flush=True)
        return 130

    if report_path is not None:
        write_performance_report(report_path, performance_report_payload(selected=selected, results=results, serial=args.serial, elapsed=elapsed, child_usage=child_usage_delta(usage_before, child_usage_snapshot()), cpu_percent=args.cpu_percent))
        print(f"Performance report: {report_path}", flush=True)

    failed = [result.label for result in results if not result.ok]
    print("\n" + ("=" * 40))
    if failed:
        print(f"CHECK FAILED in {elapsed:.2f}s: " + ", ".join(failed))
        return 1
    print(f"CHECK PASSED in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
