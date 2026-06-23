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
  python3 tools/check.py --lane pytest-browser
"""

from __future__ import annotations

import argparse
import concurrent.futures
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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


def py_compile_files() -> list[str]:
    return [
        "yolomux.py",
        "tmux_wall.py",
        "auto_approve_tmux.py",
        *sorted(str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "yolomux_lib").rglob("*.py")),
    ]


def lanes() -> list[Lane]:
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
            (Step("pytest non-browser", ["python3", "-m", "pytest", "tests", "-n", "auto", "-m", "not node_bridge and not e2e and not browser", "-q"]),),
            True,
        ),
        Lane(
            "pytest-browser",
            "pytest browser",
            (Step("pytest browser", ["python3", "-m", "pytest", "tests", "-n", "auto", "-m", "browser and not e2e", "-q"]),),
            True,
        ),
        Lane(
            "pytest-e2e",
            "pytest e2e",
            # End-to-end auto-approve etc.: real tmux + claude.py/codex.py --mock + AutoApproveWorker. Keep this pool
            # bounded: `-n auto` can launch dozens of tmux/mock-agent subprocesses while the browser and
            # unit pools are also running, which slows the whole default gate down and makes flakes harder
            # to diagnose.
            (Step("pytest e2e", ["python3", "-m", "pytest", "tests", "-n", "4", "-m", "e2e", "-q"]),),
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


def run_lane(lane: Lane) -> LaneResult:
    started = time.monotonic()
    chunks: list[str] = []
    ok = True
    for step in lane.steps:
        chunks.append(f"$ {command_text(step.args)}\n")
        result = subprocess.run(step.args, cwd=REPO_ROOT, capture_output=True, text=True)
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
    return LaneResult(lane.name, lane.label, ok, seconds, "".join(chunks))


def print_result(result: LaneResult) -> None:
    state = "PASS" if result.ok else "FAIL"
    print(f"{state}: {result.label} ({result.seconds:.2f}s)", flush=True)
    if not result.ok or "WARNING:" in result.output:
        print(result.output, end="" if result.output.endswith("\n") else "\n", flush=True)


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
    parser.add_argument("--lane", action="append", choices=lane_names, help="run only this lane; may be repeated")
    parser.add_argument("--list-lanes", action="store_true", help="print lane names and exit")
    args = parser.parse_args(argv)

    if args.list_lanes:
        for lane in available:
            print(f"{lane.name}\t{lane.label}")
        return 0

    selected_names = set(args.lane or [lane.name for lane in available if lane.default])
    selected = [lane for lane in available if lane.name in selected_names]
    if not selected:
        print("no lanes selected", file=sys.stderr)
        return 2

    started = time.monotonic()
    mode = "serial" if args.serial else "parallel"
    print(f"Running {len(selected)} check lane(s) in {mode}: {', '.join(lane.name for lane in selected)}", flush=True)
    results = run_serial(selected) if args.serial else run_parallel(selected)
    elapsed = time.monotonic() - started

    failed = [result.label for result in results if not result.ok]
    print("\n" + ("=" * 40))
    if failed:
        print(f"CHECK FAILED in {elapsed:.2f}s: " + ", ".join(failed))
        return 1
    print(f"CHECK PASSED in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
