#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Fast local check runner for YOLOmux.

The default run starts independent lanes in parallel so agents and humans do not
serialize py_compile, static checks, Node checks, full pytest, and whitespace
checks by hand. Use --serial when debugging order or when interleaved process
load makes a failure hard to read. Focused pytest lanes are available with
--lane, but the default gate keeps the old full-pytest behavior.

A default run then adds ONE exclusive latency-certification phase. The parallel
lanes retire every process they own, the host is qualified against a declared
resource envelope, the certification units run serially and alone, and the host
is qualified again afterwards. An unqualified host exits 4 with the literal
NOT CERTIFIABLE and its raw evidence; it is never skipped into a green.

Usage:
  python3 tools/check.py
  python3 tools/check.py --serial
  python3 tools/check.py --lane pytest-boot
  python3 tools/check.py --certification-only
  python3 tools/check.py --no-tool-guard

Exit codes:
  0   every lane passed and the certification units certified
  1   a lane failed, or a certification unit breached its fixed ceiling
  2   usage error
  3   another expensive check or worktree writer owns this checkout
  4   NOT CERTIFIABLE - the host was unqualified, owned processes did not
      retire, or a certification unit did not actually run
  130 interrupted
"""

from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import resource
import shlex
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.dont_write_bytecode = True

from tools.instance_isolation import resolved_product_path
from tools.instance_isolation import resolved_home_path
from tools.instance_isolation import resolved_state_dir
from tools.instance_isolation import validate_product_root_environment
from tools.instance_isolation import YolomuxRootError


def default_tool_lock_path() -> Path:
    home = resolved_home_path(os.environ)
    return resolved_product_path(
        os.environ,
        "YOLOMUX_TOOL_LOCK_PATH",
        home / ".cache" / "yolomux" / "expensive-tools.lock",
    )


try:
    validate_product_root_environment(os.environ)
    DEFAULT_TOOL_LOCK_PATH = default_tool_lock_path()
except YolomuxRootError as error:
    if __name__ == "__main__":
        print(f"CHECK REFUSED: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    raise

# tests/latency_calibration.py is the sole owner of host qualification, the declared reference
# envelopes and the fixed-ceiling verdict. The phase runner and the certification units read the
# same module so a threshold cannot drift between the gate and the test that it admits.
from tests import latency_calibration
from tests.browser_helpers.webdriver_lease import process_start_key
from tools import docker_image
from tools import static_build
from yolomux_lib.background_owner import pid_is_alive
from yolomux_lib.filesystem.io_ops import read_json_file
from yolomux_lib.infra import worktree_writer
from tools.test_catalog import MOCK_TRANSCRIPT_FILES  # noqa: F401 - check-runner compatibility export
from tools.test_catalog import NODE_LAYOUT_FILES
from tools.test_catalog import PYTEST_PHASE_FILES  # noqa: F401 - check-runner compatibility export
from tools.test_catalog import focused_phase_target_args
from tools.test_catalog import pytest_files
from tools.test_plan import LANE_SPECS
from tools.test_plan import CHECK_LANE_ENV
from tools.test_plan import StepId
from tools.test_plan import resolved_lane_step_ids
from tools.test_plan import validate_lane_specs
from tools.tool_guard import TOOL_LOCK_OWNER_ENV
from tools.tool_guard import tool_lock_owner_marker
from yolomux_lib.infra.inotify_capacity import InotifyCapacityVerdict
from yolomux_lib.infra.inotify_capacity import inotify_capacity_verdict
TOOL_GUARD_STATE_STALE_SECONDS = 30.0
TOOL_GUARD_NICE_DELTA = 5
EXPENSIVE_TOOL_LANES = frozenset({"node-layout", "pytest", "pytest-boot", "pytest-browser", "pytest-e2e", "pytest-gate-serial"})

EXIT_LANE_FAILED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_NOT_CERTIFIABLE = 4

# The exclusive phase's units. Each is a user-visible wall-latency claim that a parallel lane
# cannot measure: an oversubscribed renderer or a contended disk reports the machine. Every unit
# qualifies its host through the one owner in tests/latency_calibration.py, the same one this
# runner uses for preflight and postflight, so order no longer has to compensate for a private
# decaying estimator inside a unit.
#
# The last entry is the phase's own negative control: it proves the qualifier still separates a
# quiet host from a loaded one on this box. It runs last because it deliberately loads the host.
CERTIFICATION_NODE_IDS = (
    "tests/test_gate_tmux.py::test_s1_certification_keystroke_wall_latency_holds_the_fixed_user_ceiling",
    "tests/test_gate_interaction.py::test_i3a_certification_drag_preview_holds_the_fixed_ceiling",
    "tests/test_gate_interaction.py::test_i3b_certification_dockview_load_layout_holds_the_fixed_ceiling",
    "tests/test_gate_interaction.py::test_i3c_certification_terminal_navigation_ack_holds_the_fixed_ceiling",
    "tests/test_chat_store.py::test_chat_store_operation_wall_latency_certification",
    "tests/test_gate_stats_range.py::test_stats_24h_http_wall_latency_certification",
    "tests/test_check_runner.py::test_certification_host_qualifier_refuses_a_genuinely_loaded_host",
)
CERTIFICATION_JUNIT_NAME = "certification-junit.xml"
# A join, not a settle wait: the phase blocks until every process this gate started has exited,
# then measures once. It never waits for the machine to become quiet.
RETIREMENT_DEADLINE_SECONDS = 30.0
RETIREMENT_POLL_SECONDS = 0.2

# One token minted per gate run and exported so docker/run-tests.sh stamps every container this run
# starts with `--label CONTAINER_OWNER_LABEL=<token>`. The retirement probe then filters on that
# exact label instead of the image ancestor, so a foreign agent's container built from the identical
# test image is never mistaken for one this run owns and never blocks - or falsely clears - our
# certification. Image-wide discovery is the fallback only when no run owns the gate.
CHECK_RUN_TOKEN_ENV = "YOLOMUX_CHECK_RUN_TOKEN"
CONTAINER_OWNER_LABEL = "yolomux.check.run"


class ToolGuardBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class Step:
    label: str
    args: list[str]
    # Extra environment for this step only. The certification units are admitted by env flag and
    # docker/run-tests.sh forwards a fixed allowlist, so the names here must appear in it.
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Lane:
    name: str
    label: str
    steps: tuple[Step, ...]
    default: bool = False
    run_last: bool = False


@dataclass(frozen=True)
class BrowserCapabilityDiagnostic:
    available: bool
    component: str
    detail: str

    def refusal_text(self) -> str:
        return f"BROWSER PREFLIGHT FAILED [{self.component}]: {self.detail}"

    def json_text(self) -> str:
        return json.dumps(
            {
                "available": self.available,
                "component": self.component,
                "detail": self.detail,
            },
            sort_keys=True,
        )


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
    test_durations: tuple[dict[str, object], ...] = ()


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
    the shared-box default. Half the host remains available for live servers,
    browsers, and agent work while the three pytest pools share the rest.
    """
    raw = str(cpu_percent) if cpu_percent is not None else os.environ.get("YOLOMUX_CHECK_CPU_PERCENT", "").strip()
    if raw:
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            raise ValueError("CPU percent must be an integer 1-100")
        return int(raw)
    return 50


def schedulable_cpu_count() -> int:
    """Return CPUs this process may schedule on, falling back to host visibility."""

    logical = max(1, os.cpu_count() or 1)
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return logical


def pytest_worker_counts(*, serial: bool = False, cpu_percent: int | None = None) -> tuple[str, str, str]:
    """Divide the schedulable CPU budget across the concurrent pytest pools."""
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
    cpus = schedulable_cpu_count()
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


def step_catalog(*, serial: bool = False, cpu_percent: int | None = None) -> dict[StepId, Step]:
    nonbrowser_workers, browser_workers, e2e_workers = pytest_worker_counts(serial=serial, cpu_percent=cpu_percent)
    return {
        StepId.PY_COMPILE: Step("py_compile", ["python3", "-m", "py_compile", *py_compile_files()]),
        StepId.STATIC_BUILD: Step("static_build --check", ["python3", "tools/static_build.py", "--check"]),
        StepId.TEXTSHAPE: Step("textshape_assertion_guard", ["python3", "tools/textshape_assertion_guard.py"]),
        StepId.ARCHITECTURE_BUDGETS: Step("architecture budgets", ["python3", "tools/architecture_budgets.py"]),
        StepId.LOCAL_SERVICE_TYPES: Step("local-service type gate", ["python3", "tools/check_local_service_types.py"]),
        StepId.NODE_YOLOMUX_SYNTAX: Step("node --check static/yolomux.js", ["node", "--check", "static/yolomux.js"]),
        StepId.NODE_WALL_SYNTAX: Step("node --check static/tmux-wall.js", ["node", "--check", "static/tmux-wall.js"]),
        StepId.NODE_LAYOUT: Step("node tests/layout_url.test.js", ["node", "tests/layout_url.test.js", *NODE_LAYOUT_FILES]),
        StepId.PYTEST_NONBROWSER: Step("pytest non-browser", ["python3", "-m", "pytest", *pytest_files("nonbrowser"), *pytest_xdist_args(nonbrowser_workers, serial=serial), "-m", "not node_bridge and not gate_serial and not e2e and not browser", "-q"]),
        StepId.PYTEST_GATE_SERIAL: Step("pytest timing-sensitive serial", ["python3", "-m", "pytest", *pytest_files("gate_serial"), "-m", "gate_serial", "-q"]),
        StepId.PYTEST_BOOT: Step("pytest boot smoke", ["python3", "-m", "pytest", *pytest_files("boot"), "-m", "boot", "-q"]),
        StepId.PYTEST_BROWSER: Step("pytest browser", ["python3", "-m", "pytest", *pytest_files("browser"), *pytest_xdist_args(browser_workers, serial=serial, worksteal=True), "-m", "browser and not e2e and not boot and not visual_golden", "-q"]),
        StepId.PYTEST_BROWSER_GOLDEN: Step("pytest browser visual goldens", ["python3", "-m", "pytest", *pytest_files("golden"), "-m", "visual_golden", "-q"]),
        StepId.PYTEST_E2E: Step("pytest e2e", ["python3", "-m", "pytest", *pytest_files("e2e"), *pytest_xdist_args(e2e_workers, serial=serial), "-m", "e2e", "-q"]),
        StepId.PYTEST_UNIT: Step("pytest unit", ["python3", "-m", "pytest", *focused_phase_target_args("nonbrowser"), "-m", "not gate_serial and not socket and not browser and not node_bridge", "-q"]),
        StepId.PYTEST_SOCKET: Step("pytest socket", ["python3", "-m", "pytest", *focused_phase_target_args("nonbrowser"), "-m", "socket and not gate_serial and not browser", "-q"]),
        StepId.WHITESPACE: Step("git diff --check", ["git", "diff", "--check"]),
    }


def lanes(*, serial: bool = False, cpu_percent: int | None = None) -> list[Lane]:
    # Commands are built once and referenced through typed IDs. Focused aliases,
    # prerequisites, and phase ownership therefore cannot drift into copies.
    catalog = step_catalog(serial=serial, cpu_percent=cpu_percent)
    validate_lane_specs(catalog)
    return [
        Lane(
            spec.name,
            spec.label,
            tuple(catalog[step_id] for step_id in resolved_lane_step_ids(spec)),
            spec.default,
            spec.run_last,
        )
        for spec in LANE_SPECS
    ]


def selected_needs_browser(selected: list[Lane]) -> bool:
    browser_lanes = {
        spec.name
        for spec in LANE_SPECS
        if set(spec.phases) & {"browser", "golden"}
    }
    return any(lane.name in browser_lanes for lane in selected)


def ambient_browser_capability_preflight() -> BrowserCapabilityDiagnostic:
    """Resolve browser gate prerequisites in this process environment.

    This intentionally performs no install or download. A requested browser
    lane must either have all three ambient capabilities or fail before pytest,
    where importorskip and fixture skips could otherwise turn absence green.
    """

    if importlib.util.find_spec("selenium") is None:
        return BrowserCapabilityDiagnostic(False, "dependency", "Python package 'selenium' is unavailable")

    browser_candidates = (
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    )
    browser = next(
        (Path(candidate) for candidate in browser_candidates if candidate is not None and Path(candidate).is_file()),
        None,
    )
    if browser is None:
        return BrowserCapabilityDiagnostic(False, "browser", "Chrome or Chromium executable is unavailable")

    driver = shutil.which("chromedriver")
    if driver is None:
        selenium_cache = Path.home() / ".cache" / "selenium" / "chromedriver"
        driver = next(
            (str(path) for path in sorted(selenium_cache.glob("**/chromedriver"), reverse=True) if path.is_file() and os.access(path, os.X_OK)),
            None,
        )
    if driver is None:
        return BrowserCapabilityDiagnostic(
            False,
            "driver",
            "chromedriver is unavailable on PATH and in the Selenium cache; no download was attempted",
        )
    return BrowserCapabilityDiagnostic(True, "ready", f"selenium, {browser}, and {driver}")


def browser_capability_preflight() -> BrowserCapabilityDiagnostic:
    """Resolve prerequisites in the environment which will execute pytest."""

    container_available, _reason = docker_image.container_available(REPO_ROOT)
    if not container_available:
        return ambient_browser_capability_preflight()

    probe = subprocess.run(
        [
            str(REPO_ROOT / "docker" / "run-tests.sh"),
            "--",
            "python3",
            "-c",
            (
                "from tools.check import ambient_browser_capability_preflight; "
                "print(ambient_browser_capability_preflight().json_text())"
            ),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or f"probe exited {probe.returncode}").strip().splitlines()[-1]
        return BrowserCapabilityDiagnostic(False, "environment", f"isolated test environment probe failed: {detail}")
    try:
        payload = json.loads(probe.stdout.strip().splitlines()[-1])
        return BrowserCapabilityDiagnostic(
            bool(payload["available"]),
            str(payload["component"]),
            str(payload["detail"]),
        )
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return BrowserCapabilityDiagnostic(False, "environment", f"isolated test environment returned an invalid diagnostic: {error}")


def command_text(args: list[str]) -> str:
    return shlex.join(args)


def state_dir_from_env() -> Path:
    return resolved_state_dir(os.environ)


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


HEAVY_LANE_NAME_PREFIX = "pytest"


def heavy_lane_names(selected: list[Lane]) -> list[str]:
    """Return the selected lanes that fan out browsers, daemons and xdist workers."""

    return [lane.name for lane in selected if lane.name.startswith(HEAVY_LANE_NAME_PREFIX)]


def admit_inotify_capacity(
    selected: list[Lane], *, profile: dict[str, object] | None = None
) -> InotifyCapacityVerdict | None:
    """Admit this host's inotify capacity before any heavy lane is created.

    The check runs here, not after the lanes retire: every watch daemon and
    browser the gate starts consumes the same uid-wide ceiling, so a refusal is
    only actionable while the capacity still has to be reserved.  Lanes that
    create no watchers are not gated on it.
    """

    resolved_profile = latency_calibration.platform_profile() if profile is None else profile
    if not heavy_lane_names(selected) or not resolved_profile["uses_inotify_capacity"]:
        return None
    return inotify_capacity_verdict()


def selected_needs_tool_guard(selected: list[Lane], explicit_lane_names: list[str] | None) -> bool:
    selected_names = {lane.name for lane in selected}
    if explicit_lane_names is None:
        return True
    return bool(selected_names & EXPENSIVE_TOOL_LANES)


@contextmanager
def expensive_tool_lock(enabled: bool = True, lock_path: Path | None = None):
    if not enabled:
        yield False
        return
    lock_path = default_tool_lock_path() if lock_path is None else resolved_product_path(
        {**os.environ, "YOLOMUX_TOOL_LOCK_PATH": str(lock_path)},
        "YOLOMUX_TOOL_LOCK_PATH",
        lock_path,
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ToolGuardBusy(f"another expensive YOLOmux check already owns {lock_path}") from exc
        previous_owner = os.environ.get(TOOL_LOCK_OWNER_ENV)
        os.environ[TOOL_LOCK_OWNER_ENV] = tool_lock_owner_marker(lock_path)
        try:
            yield True
        finally:
            if previous_owner is None:
                os.environ.pop(TOOL_LOCK_OWNER_ENV, None)
            else:
                os.environ[TOOL_LOCK_OWNER_ENV] = previous_owner
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def check_run_token_environment(token: str | None = None):
    """Export one run token so docker/run-tests.sh can stamp this run's containers, then restore it.

    In a real gate the process exits and the variable dies with it; scoping it keeps an in-process
    caller - the check-runner's own tests, which call main() repeatedly - from leaking a token into a
    later run's container probe and turning its ancestor fallback into a stale label filter.
    """

    previous = os.environ.get(CHECK_RUN_TOKEN_ENV)
    minted = token or uuid.uuid4().hex
    os.environ[CHECK_RUN_TOKEN_ENV] = minted
    try:
        yield minted
    finally:
        if previous is None:
            os.environ.pop(CHECK_RUN_TOKEN_ENV, None)
        else:
            os.environ[CHECK_RUN_TOKEN_ENV] = previous


def run_lane(lane: Lane) -> LaneResult:
    started = time.monotonic()
    chunks: list[str] = []
    step_results: list[StepResult] = []
    ok = True
    for step in lane.steps:
        chunks.append(f"$ {command_text(step.args)}\n")
        step_started = time.monotonic()
        environment = {**os.environ, CHECK_LANE_ENV: lane.name, **dict(step.env)}
        result = subprocess.run(step.args, cwd=REPO_ROOT, capture_output=True, text=True, env=environment)
        step_results.append(StepResult(step.label, command_text(step.args), time.monotonic() - step_started, result.returncode, pytest_duration_phases(result.stdout)))
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


_PYTEST_DURATION_RE = re.compile(r"^\s*([0-9.]+)s\s+(call|setup|teardown)\s+(.+)$", re.MULTILINE)


def pytest_duration_phases(output: str) -> tuple[dict[str, object], ...]:
    """Extract every pytest duration row for the persistent per-run report."""

    return tuple(
        {"seconds": float(seconds), "phase": phase, "nodeid": nodeid.strip()}
        for seconds, phase, nodeid in _PYTEST_DURATION_RE.findall(output)
    )


def instrument_lane_for_performance(lane: Lane) -> Lane:
    """Ask every pytest step for the timing table persisted in the run report."""

    steps = tuple(
        Step(step.label, [*step.args, "--durations=0", "--durations-min=0"] if step.args[:3] == ["python3", "-m", "pytest"] else step.args, step.env)
        for step in lane.steps
    )
    return Lane(lane.name, lane.label, steps, lane.default, lane.run_last)


def child_usage_delta(before: dict[str, float | int | str], after: dict[str, float | int | str]) -> dict[str, float | int | str]:
    """Calculate gate child CPU totals; RSS remains the high-water mark."""

    return {
        "user_seconds": round(float(after["user_seconds"]) - float(before["user_seconds"]), 6),
        "system_seconds": round(float(after["system_seconds"]) - float(before["system_seconds"]), 6),
        "max_rss": after["max_rss"],
        "max_rss_unit": after["max_rss_unit"],
    }


def performance_report_payload(*, selected: list[Lane], results: list[LaneResult], serial: bool, elapsed: float, child_usage: dict[str, float | int | str], interrupted: bool = False, cpu_percent: int | None = None, certification: dict[str, object] | None = None) -> dict[str, object]:
    """Create stable opt-in machine output without adding noise to normal checks."""

    worker_counts = dict(zip(("nonbrowser", "browser", "e2e"), pytest_worker_counts(serial=serial, cpu_percent=cpu_percent), strict=True))
    return {
        "schema": 3,
        "certification": certification,
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
                        "test_durations": step.test_durations,
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

    path = Path(value) if value else Path("/tmp") / "yolomux-check-runs" / f"check-{time.time_ns()}-{os.getpid()}.json"
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
    "pytest-gate-serial",
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


def run_functional_lanes(selected: list[Lane], *, serial: bool) -> list[LaneResult]:
    """Run declared final lanes serially after every parallel functional lane retires."""

    if serial:
        return run_serial(selected)
    parallel_lanes = [lane for lane in selected if not lane.run_last]
    final_lanes = [lane for lane in selected if lane.run_last]
    results = run_parallel(parallel_lanes) if parallel_lanes else []
    if final_lanes:
        print(
            "Running final serial lane(s): " + ", ".join(lane.name for lane in final_lanes),
            flush=True,
        )
        results.extend(run_serial(final_lanes))
    return results


def process_table() -> list[tuple[int, int, str]]:
    """One snapshot of every live process as (pid, ppid, command), portable across Linux and macOS.

    The snapshot is produced by a child process, so an unfiltered table always contains at least one
    live descendant of the caller and retirement could never complete. Excluding that pid is the
    difference between measuring the gate and measuring the measurement.
    """

    with subprocess.Popen(["ps", "-eo", "pid=,ppid=,command="], stdout=subprocess.PIPE, text=True) as probe:
        stdout, _stderr = probe.communicate()
        probe_pid = probe.pid
    rows: list[tuple[int, int, str]] = []
    for line in stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 2 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        if int(fields[0]) == probe_pid:
            continue
        rows.append((int(fields[0]), int(fields[1]), fields[2] if len(fields) > 2 else ""))
    return rows


def descendant_processes(pid: int, rows: list[tuple[int, int, str]] | None = None) -> list[dict[str, object]]:
    """Every live descendant of pid, each stamped with the one immutable proof of its identity.

    A PID is not an identity: the kernel reuses a PID the instant its process exits, so a bare
    descendant walk can re-observe the same number as an unrelated process and either hang retirement
    on it or authorize a signal against it. Each member therefore carries `start_key` - the kernel
    start time from the SAME `process_start_key` owner the WebDriver lease uses to guard its signals -
    captured at the moment of observation. The retirement barrier authorizes a survivor only while
    that captured key still holds, so a reused or reparented PID is proven gone, never signalled.
    """

    table = process_table() if rows is None else rows
    children: dict[int, list[tuple[int, str]]] = {}
    for child_pid, parent_pid, command in table:
        children.setdefault(parent_pid, []).append((child_pid, command))
    found: list[dict[str, object]] = []
    seen = {pid}
    frontier = [pid]
    while frontier:
        current = frontier.pop()
        for child_pid, command in children.get(current, ()):
            if child_pid in seen:
                continue
            seen.add(child_pid)
            frontier.append(child_pid)
            found.append({"pid": child_pid, "ppid": current, "command": command, "start_key": process_start_key(child_pid)})
    return found


def running_test_containers(run_token: str | None = None) -> dict[str, object]:
    """Live containers this run owns, or why they could not be observed.

    pytest re-executes itself inside the test image, so the lanes' real work does NOT run as a
    descendant of this runner: those processes belong to the container runtime. A retirement check
    that only walks the process tree therefore reports "retired" while a full browser suite is
    still winding down. When this run minted a token (default from CHECK_RUN_TOKEN_ENV) the probe
    filters on the exact `CONTAINER_OWNER_LABEL=<token>` stamped by docker/run-tests.sh, so a
    foreign agent's container from the identical image is neither counted nor cleared here; without
    a token it falls back to image-ancestor discovery. An unobservable docker client is not a
    failure of this probe - it carries its reason so an empty result is never mistaken for a
    proven-clean one, and the retirement owner decides whether that reason blocks a run that
    actually started containers.
    """

    image = docker_image.image_name(REPO_ROOT)
    token = run_token if run_token is not None else (os.environ.get(CHECK_RUN_TOKEN_ENV) or None)
    scope_filter = ["--filter", f"label={CONTAINER_OWNER_LABEL}={token}"] if token else ["--filter", f"ancestor={image}"]
    try:
        completed = subprocess.run(
            ["docker", "ps", *scope_filter, "--format", "{{.ID}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        # An expected outcome carrying its reason, not a crash. The gate's own tests execute INSIDE
        # that image, where there is no docker client at all, so this probe must report "cannot
        # observe" rather than raise through the phase or claim an empty, clean result.
        return {"available": False, "reason": f"docker client unavailable: {exc}", "image": image, "containers": []}
    if completed.returncode != 0:
        return {"available": False, "reason": (completed.stderr or "docker ps failed").strip()[:500], "image": image, "containers": []}
    return {
        "available": True,
        "reason": "",
        "image": image,
        "containers": [
            {"container": fields[0], "status": fields[1] if len(fields) > 1 else ""}
            for fields in (line.split("\t") for line in completed.stdout.splitlines())
            if fields and fields[0]
        ],
    }


def retire_owned_processes(
    *,
    pid: int | None = None,
    deadline_seconds: float = RETIREMENT_DEADLINE_SECONDS,
    expected_containers: bool = False,
    identity_fn: Callable[[int | None], str | None] = process_start_key,
) -> dict[str, object]:
    """Join everything the parallel lanes started before anything is measured.

    A join on measured predicates - zero surviving descendants and zero live test containers - not
    a settle wait for a quiet machine. `os.sync()` then completes the writeback those lanes caused
    instead of leaving the qualifier to observe it as ambient disk load; the gate finishes its own
    I/O rather than waiting for the kernel to get around to it. A deadline breach is a
    machine-readable refusal carrying the survivors, never a longer wait and never a silent continue.

    The survivor set is decided by the SAME per-member proof the WebDriver lease uses, not a bare
    PID walk. Each descendant's immutable start key is captured the first time we see that PID and is
    never recaptured; a member is authorized as a live owned survivor only while `identity_fn` still
    reads that exact key. A PID whose key changed was reused by another process - the one we owned
    exited - and a reparented-away or exited PID reads no key at all. Either way it is proven gone
    and is never counted as a survivor, nor, on the shared signal path, ever signalled. The proof is
    what authorizes membership and what proves the exit; a bare number never does either.

    When this gate actually routed its lanes into Docker (`expected_containers`), an unobservable
    docker client is NOT proof of absence: a container this run started could still be draining
    while the client we would ask about it has gone away. That is a refusal, not a clear. When the
    gate never used Docker - it ran on the host, or the units run INSIDE the image where there is no
    client at all - an unobservable client carries no owned container to prove absent, and the
    process walk already covers what ran, so it does not block.
    """

    owner = os.getpid() if pid is None else pid
    started = time.monotonic()
    # pid -> the one immutable proof captured the first time we saw that number, never recomputed.
    proofs: dict[int, dict[str, object]] = {}

    def unproved(probe: dict[str, object]) -> bool:
        return expected_containers and not probe["available"]

    def proven_survivors() -> list[dict[str, object]]:
        for member in descendant_processes(owner):
            proofs.setdefault(member["pid"], member)
        live: list[dict[str, object]] = []
        for member_pid, proof in proofs.items():
            current = identity_fn(member_pid)
            if current is not None and current == proof.get("start_key"):
                live.append(proof)
        return live

    survivors = proven_survivors()
    container_probe = running_test_containers()
    while (survivors or container_probe["containers"] or unproved(container_probe)) and time.monotonic() - started < deadline_seconds:
        time.sleep(RETIREMENT_POLL_SECONDS)
        survivors = proven_survivors()
        container_probe = running_test_containers()
    joined_seconds = time.monotonic() - started
    docker_unobservable = unproved(container_probe)
    sync_started = time.monotonic()
    os.sync()
    return {
        "owner_pid": owner,
        "retired": not survivors and not container_probe["containers"] and not docker_unobservable,
        "seconds": round(joined_seconds, 6),
        "sync_seconds": round(time.monotonic() - sync_started, 6),
        "deadline_seconds": deadline_seconds,
        "expected_containers": expected_containers,
        "docker_unobservable": docker_unobservable,
        "survivors": survivors,
        "container_probe": container_probe,
    }


def certification_evidence_dir(explicit: str | None = None) -> Path:
    """One /tmp directory holding the junit outcomes and every raw latency artifact.

    docker/run-tests.sh bind-mounts YOLOMUX_E2E_EVIDENCE_DIR at the identical absolute path, so
    evidence written by the containerized run is readable here without a second transport.
    """

    path = Path(explicit) if explicit else Path("/tmp") / "yolomux-certification" / f"cert-{time.time_ns()}-{os.getpid()}"
    if not path.resolve().is_relative_to(Path("/tmp").resolve()):
        raise ValueError("certification evidence dir must be under /tmp")
    return path


def certification_step(evidence_dir: Path) -> Step:
    """One serial pytest invocation naming every certification node id explicitly."""

    junit_path = evidence_dir / CERTIFICATION_JUNIT_NAME
    return Step(
        "certification units",
        # junit_family=xunit1 is pinned because the default xunit2 omits the `file` attribute, and
        # without it no reported case can be matched back to a requested node id. That failure is
        # fail-closed - every unit reads as not-collected and the phase refuses - but it refuses a
        # run whose units all actually passed, so the family is pinned rather than inferred.
        ["python3", "-m", "pytest", *CERTIFICATION_NODE_IDS, "-p", "no:xdist", "-o", "junit_family=xunit1", f"--junit-xml={junit_path}", "-rs", "-q"],
        tuple(
            [(name, "1") for name in latency_calibration.CERTIFICATION_ENV_NAMES]
            + [
                ("YOLOMUX_E2E_EVIDENCE_DIR", str(evidence_dir)),
                # Certification runs alone and owns the complete port range; a synthetic lane
                # name would be rejected by gate_http_port_candidates before any unit runs.
                (CHECK_LANE_ENV, ""),
            ]
        ),
    )


# The only children a certification testcase may carry. The three outcome tags are the answer to
# "what did this unit do"; the rest are captured stdio/metadata. A child outside this set is an
# outcome vocabulary this reader does not understand, and a document it cannot fully read is not a
# document it may certify from.
_JUNIT_OUTCOME_TAGS = frozenset({"skipped", "failure", "error"})
_JUNIT_NON_OUTCOME_TAGS = frozenset({"system-out", "system-err", "properties"})


def certification_junit_admission(junit_path: Path, expected: tuple[str, ...] = CERTIFICATION_NODE_IDS) -> dict[str, object]:
    """Validate the certification JUnit structurally before any outcome is trusted. Fail closed.

    A present document is the phase's evidence, and evidence that is malformed, duplicated,
    mis-timed, contradictory, identity-less, or carries a row nobody asked for is a void input, not
    a green. Each such document is refused here - with a machine-readable reason and the offending
    identity - so a number is never read out of a file the phase cannot trust. An ABSENT document is
    NOT a rejection: it is the ordinary "did not run" the not-collected path already refuses through
    `certification_unit_did_not_run`, so admission defers to that owner and reports admitted.
    """

    expected_set = set(expected)
    if not junit_path.exists():
        return {"admitted": True, "reason": "", "detail": f"{junit_path} absent; deferred to did-not-run", "outcomes": certification_outcomes(junit_path, expected)}
    try:
        tree = ElementTree.parse(junit_path)
    except ElementTree.ParseError as exc:
        return {"admitted": False, "reason": "junit_malformed", "detail": f"{junit_path}: {exc}", "outcomes": {}}
    seen: dict[str, dict[str, object]] = {}
    for case in tree.iter("testcase"):
        file_attr = case.get("file")
        name_attr = case.get("name")
        if not file_attr or not name_attr:
            return {"admitted": False, "reason": "junit_missing_identity", "detail": f"file={file_attr!r} name={name_attr!r}", "outcomes": {}}
        nodeid = f"{file_attr}::{name_attr}"
        if nodeid not in expected_set:
            return {"admitted": False, "reason": "junit_unexpected_row", "detail": nodeid, "outcomes": {}}
        if nodeid in seen:
            return {"admitted": False, "reason": "junit_duplicate_row", "detail": nodeid, "outcomes": {}}
        time_raw = case.get("time")
        if time_raw is None:
            return {"admitted": False, "reason": "junit_timing_missing", "detail": nodeid, "outcomes": {}}
        try:
            seconds = float(time_raw)
        except (TypeError, ValueError):
            return {"admitted": False, "reason": "junit_timing_invalid", "detail": f"{nodeid}: time={time_raw!r}", "outcomes": {}}
        if not math.isfinite(seconds) or seconds < 0:
            return {"admitted": False, "reason": "junit_timing_non_finite", "detail": f"{nodeid}: time={time_raw!r}", "outcomes": {}}
        child_tags = [child.tag for child in case]
        unknown = [tag for tag in child_tags if tag not in _JUNIT_OUTCOME_TAGS and tag not in _JUNIT_NON_OUTCOME_TAGS]
        if unknown:
            return {"admitted": False, "reason": "junit_unknown_outcome_child", "detail": f"{nodeid}: {sorted(set(unknown))}", "outcomes": {}}
        outcome_children = [child for child in case if child.tag in _JUNIT_OUTCOME_TAGS]
        if len(outcome_children) > 1:
            return {"admitted": False, "reason": "junit_contradictory_outcome", "detail": f"{nodeid}: {[child.tag for child in outcome_children]}", "outcomes": {}}
        if outcome_children:
            child = outcome_children[0]
            outcome = "skipped" if child.tag == "skipped" else child.tag
            detail = (child.get("message") or child.text or "").strip()
        else:
            outcome, detail = "passed", ""
        seen[nodeid] = {"outcome": outcome, "detail": detail[:2000], "seconds": seconds}
    outcomes = {
        nodeid: seen.get(nodeid, {"outcome": "not-collected", "detail": f"absent from {junit_path}", "seconds": 0.0})
        for nodeid in expected
    }
    return {"admitted": True, "reason": "", "detail": "", "outcomes": outcomes}


def certification_outcomes(junit_path: Path, expected: tuple[str, ...] = CERTIFICATION_NODE_IDS) -> dict[str, dict[str, object]]:
    """Read what each named node actually did. A node that did not run is never counted green."""

    reported: dict[str, dict[str, object]] = {}
    if junit_path.exists():
        for case in ElementTree.parse(junit_path).iter("testcase"):
            nodeid = f"{case.get('file')}::{case.get('name')}"
            outcome, detail = "passed", ""
            for child in case:
                if child.tag in {"skipped", "failure", "error"}:
                    outcome = "skipped" if child.tag == "skipped" else child.tag
                    detail = (child.get("message") or child.text or "").strip()
                    break
            reported[nodeid] = {"outcome": outcome, "detail": detail[:2000], "seconds": float(case.get("time") or 0.0)}
    return {
        nodeid: reported.get(nodeid, {"outcome": "not-collected", "detail": f"absent from {junit_path}", "seconds": 0.0})
        for nodeid in expected
    }


def certification_verdict(
    *,
    retirement: dict[str, object],
    preflight: dict[str, object] | None,
    postflight: dict[str, object] | None,
    outcomes: dict[str, dict[str, object]] | None,
    returncode: int | None,
    junit_admission: dict[str, object] | None = None,
) -> dict[str, object]:
    """Resolve one phase outcome with an explicit precedence, and name the machine-readable reason.

    Precedence, strongest refusal first. A void measurement outranks a unit failure: a ceiling
    breach measured on a host that was not qualified is not evidence about the product, so it is
    reported as NOT CERTIFIABLE with the unit evidence attached rather than blamed on the code. A
    JUnit document the admission owner refused is the same class of void input - a number read out
    of a malformed, duplicated or contradictory file is not evidence about the product either.
    """

    if not retirement["retired"]:
        return {
            "result": "not-certifiable",
            "reason": "owned_processes_not_retired",
            "evidence": {
                "survivors": retirement["survivors"],
                "container_probe": retirement.get("container_probe"),
                "docker_unobservable": retirement.get("docker_unobservable"),
            },
        }
    if preflight is not None and not preflight["qualified"]:
        return {"result": "not-certifiable", "reason": "host_unqualified_preflight", "evidence": preflight["reasons"]}
    if postflight is not None and not postflight["qualified"]:
        return {"result": "not-certifiable", "reason": "host_unqualified_postflight", "evidence": postflight["reasons"]}
    if junit_admission is not None and not junit_admission["admitted"]:
        return {
            "result": "not-certifiable",
            "reason": "certification_junit_rejected",
            "evidence": {"junit_reason": junit_admission["reason"], "detail": junit_admission.get("detail")},
        }
    did_not_run = {nodeid: outcome for nodeid, outcome in (outcomes or {}).items() if outcome["outcome"] in {"skipped", "not-collected"}}
    if did_not_run:
        return {"result": "not-certifiable", "reason": "certification_unit_did_not_run", "evidence": did_not_run}
    # A unit that refused because its own host qualification failed is not evidence about the
    # product either, so it outranks a breach for the same reason the preflight does. The units
    # qualify their host at the moment they run, which the phase's preflight - taken before the
    # whole serial run - cannot; without this branch that refusal would be reported as a product
    # failure. NOT_CERTIFIABLE is the literal every refusal carries, in the runner and in a unit.
    #
    # The refusal changes the verdict but never hides the other reds: a unit that refused and a
    # DIFFERENT unit that failed are two facts, and reporting only the first would discard the
    # second. Both sets are carried, so a reader always sees every non-passing unit.
    not_passed = {nodeid: outcome for nodeid, outcome in (outcomes or {}).items() if outcome["outcome"] != "passed"}
    refused = {nodeid: outcome for nodeid, outcome in not_passed.items() if latency_calibration.NOT_CERTIFIABLE in str(outcome["detail"])}
    if refused:
        return {
            "result": "not-certifiable",
            "reason": "certification_unit_not_certifiable",
            "evidence": {"refused": refused, "also_failed": {nodeid: outcome for nodeid, outcome in not_passed.items() if nodeid not in refused}},
        }
    if not_passed:
        return {"result": "failed", "reason": "certification_unit_failed", "evidence": not_passed}
    if returncode not in (None, 0):
        return {"result": "failed", "reason": "certification_command_failed", "evidence": {"returncode": returncode}}
    return {"result": "certified", "reason": "all_units_certified_on_a_qualified_host", "evidence": outcomes or {}}


def git_head_sha(repo_root: Path = REPO_ROOT) -> str | None:
    """The full 40-character HEAD SHA, or None when git cannot answer. Never the short form."""

    try:
        completed = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    sha = completed.stdout.strip()
    return sha if completed.returncode == 0 and sha else None


def working_tree_clean_state(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    """Every tracked modification and every untracked path, so clean is a proven fact not a hope.

    A certification report that named only the SHA would describe a checkout that may carry
    uncommitted edits the SHA cannot see; the exact-SHA release certification is answerable to this,
    not to the commit id alone.
    """

    try:
        completed = subprocess.run(["git", "-C", str(repo_root), "status", "--porcelain"], capture_output=True, text=True, check=False)
    except OSError as exc:
        return {"observable": False, "clean": False, "reason": f"git status unavailable: {exc}", "tracked": [], "untracked": []}
    if completed.returncode != 0:
        return {"observable": False, "clean": False, "reason": (completed.stderr or "git status failed").strip()[:500], "tracked": [], "untracked": []}
    tracked: list[str] = []
    untracked: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:]
        (untracked if line.startswith("??") else tracked).append(path)
    return {"observable": True, "clean": not tracked and not untracked, "reason": "", "tracked": tracked, "untracked": untracked}


def generated_bundle_hashes(repo_root: Path = REPO_ROOT) -> dict[str, str | None]:
    """A content hash of every generated static bundle static_build.py owns. None when one is absent.

    Read from the one owner of the generated set, so a new bundle is hashed automatically rather
    than by a second hand-maintained list that would drift.
    """

    hashes: dict[str, str | None] = {}
    for asset in sorted(static_build.ASSETS):
        path = repo_root / "static" / asset
        try:
            hashes[asset] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            hashes[asset] = None
    return hashes


def certification_release_context(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    """The candidate identity every certification report carries: SHA, platform, and generated hashes."""

    return {
        "full_sha": git_head_sha(repo_root),
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "generated_bundle_hashes": generated_bundle_hashes(repo_root),
    }


def exact_sha_certification_admission(*, start_state: dict[str, object], end_state: dict[str, object]) -> dict[str, object]:
    """Exact-SHA (release) certification runs only from a fresh checkout with no tracked or untracked files.

    Both ends are checked: a tree that was clean at the start but dirtied by the run itself no longer
    certifies the exact SHA either. Fail closed - an unobservable git state is not a clean one.
    """

    for name, state in (("start", start_state), ("end", end_state)):
        if not state.get("observable", False):
            return {"admitted": False, "reason": f"{name}_state_unobservable", "detail": state.get("reason", "")}
        if not state["clean"]:
            return {"admitted": False, "reason": f"dirty_{name}_checkout", "detail": {"tracked": state["tracked"], "untracked": state["untracked"]}}
    return {"admitted": True, "reason": ""}


def run_certification_phase(*, evidence_dir: Path, expected_containers: bool = False) -> tuple[dict[str, object], LaneResult | None]:
    """Retire, qualify, certify serially, qualify again. Exclusive by construction, never by hope."""

    evidence_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    start_clean_state = working_tree_clean_state()
    retirement = retire_owned_processes(expected_containers=expected_containers)
    preflight = latency_calibration.certification_host_qualification(evidence_root=evidence_dir) if retirement["retired"] else None
    lane_result: LaneResult | None = None
    postflight: dict[str, object] | None = None
    outcomes: dict[str, dict[str, object]] | None = None
    junit_admission: dict[str, object] | None = None
    returncode: int | None = None
    if preflight is not None and preflight["qualified"]:
        lane_result = run_lane(Lane("certification", "latency certification", (certification_step(evidence_dir),)))
        returncode = lane_result.steps[-1].returncode if lane_result.steps else None
        postflight = latency_calibration.certification_host_qualification(evidence_root=evidence_dir)
        junit_admission = certification_junit_admission(evidence_dir / CERTIFICATION_JUNIT_NAME)
        outcomes = junit_admission["outcomes"] if junit_admission["admitted"] else certification_outcomes(evidence_dir / CERTIFICATION_JUNIT_NAME)
    verdict = certification_verdict(retirement=retirement, preflight=preflight, postflight=postflight, outcomes=outcomes, returncode=returncode, junit_admission=junit_admission)
    end_clean_state = working_tree_clean_state()
    exact_sha_admission = exact_sha_certification_admission(start_state=start_clean_state, end_state=end_clean_state)
    release = {
        **certification_release_context(),
        "start_clean_state": start_clean_state,
        "end_clean_state": end_clean_state,
        "exact_sha_certification": exact_sha_admission,
    }
    if verdict["result"] == "certified" and not exact_sha_admission["admitted"]:
        verdict = {
            "result": "not-certifiable",
            "reason": "exact_sha_certification_rejected",
            "evidence": exact_sha_admission,
        }
    payload = {
        **verdict,
        "wall_seconds": round(time.monotonic() - started, 6),
        "evidence_dir": str(evidence_dir),
        "node_ids": list(CERTIFICATION_NODE_IDS),
        "release": release,
        "retirement": retirement,
        "preflight": preflight,
        "postflight": postflight,
        "outcomes": outcomes,
        "junit_admission": junit_admission,
        "returncode": returncode,
    }
    return payload, lane_result


def print_certification(payload: dict[str, object], lane_result: LaneResult | None) -> None:
    result = payload["result"]
    if result == "certified":
        print(f"CERTIFIED: {len(CERTIFICATION_NODE_IDS)} unit(s) on a qualified host ({payload['wall_seconds']:.2f}s)", flush=True)
    elif result == "not-certifiable":
        print(f"{latency_calibration.NOT_CERTIFIABLE}: {payload['reason']}", flush=True)
        print(json.dumps(payload["evidence"], indent=2, sort_keys=True, default=str), flush=True)
    else:
        print(f"CERTIFICATION FAILED: {payload['reason']}", flush=True)
        print(json.dumps(payload["evidence"], indent=2, sort_keys=True, default=str), flush=True)
    if lane_result is not None and result != "certified":
        print(lane_result.output, end="" if lane_result.output.endswith("\n") else "\n", flush=True)
    release = payload.get("release")
    if isinstance(release, dict):
        exact = release.get("exact_sha_certification", {})
        clean = "exact-SHA certifiable (fresh checkout)" if exact.get("admitted") else f"NOT exact-SHA certifiable: {exact.get('reason')}"
        print(f"Candidate: sha={release.get('full_sha')} platform={release.get('platform', {}).get('system')}/{release.get('platform', {}).get('machine')}; {clean}", flush=True)
    print(f"Certification evidence: {payload['evidence_dir']}", flush=True)


def main(argv: list[str] | None = None) -> int:
    available = lanes()
    lane_names = [lane.name for lane in available]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", action="store_true", help="run lanes one at a time instead of in parallel")
    parser.add_argument("--cpu-percent", type=int, default=None, metavar="1-100", help="fraction of host CPUs the pytest pools may claim (default: 50; env YOLOMUX_CHECK_CPU_PERCENT)")
    parser.add_argument("--lane", action="append", choices=lane_names, help="run only this lane; may be repeated")
    parser.add_argument("--list-lanes", action="store_true", help="print lane names and exit")
    parser.add_argument("--no-tool-guard", action="store_true", help="skip the expensive-tool lock and live-server priority lowering")
    parser.add_argument("--performance-report", nargs="?", const="", metavar="/tmp/REPORT.json", help="override the automatic per-run timing report path under /tmp")
    parser.add_argument("--certification-only", action="store_true", help="run only the exclusive latency-certification phase, skipping every functional lane")
    parser.add_argument("--certification-evidence-dir", default=None, metavar="/tmp/DIR", help="override the automatic certification evidence directory under /tmp")
    args = parser.parse_args(argv)

    if args.certification_only and args.lane:
        parser.error("--certification-only runs no lanes; drop --lane")

    try:
        check_cpu_percent(args.cpu_percent)
    except ValueError as exc:
        parser.error(str(exc))

    if args.serial or args.cpu_percent is not None:
        available = lanes(serial=args.serial, cpu_percent=args.cpu_percent)

    if args.list_lanes:
        for lane in available:
            selections = [command_text(step.args) for step in lane.steps]
            if lane.name == "node-layout":
                selections.extend(NODE_LAYOUT_FILES)
            print(f"{lane.name}\t{lane.label}\t{' ; '.join(selections)}")
        return 0

    selected_names = set() if args.certification_only else set(args.lane or [lane.name for lane in available if lane.default])
    selected = slowest_first([lane for lane in available if lane.name in selected_names])
    if not selected and not args.certification_only:
        print("no lanes selected", file=sys.stderr)
        return 2

    if selected_needs_browser(selected):
        browser_capability = browser_capability_preflight()
        if not browser_capability.available:
            print(browser_capability.refusal_text(), file=sys.stderr, flush=True)
            return EXIT_LANE_FAILED

    # The exclusive phase belongs to the canonical command. A focused --lane run is deliberately
    # not a certification, and says so, rather than exiting 0 as if it had certified.
    certify = args.certification_only or args.lane is None
    try:
        report_path = performance_report_path(args.performance_report or "")
        evidence_dir = certification_evidence_dir(args.certification_evidence_dir)
    except ValueError as exc:
        print(f"CHECK REFUSED: {exc}", file=sys.stderr, flush=True)
        return 2
    selected = [instrument_lane_for_performance(lane) for lane in selected]

    inotify_capacity = admit_inotify_capacity(selected)
    if inotify_capacity is not None and not inotify_capacity.admitted:
        print(inotify_capacity.refusal_text(), file=sys.stderr, flush=True)
        return 4

    guard_enabled = (certify or selected_needs_tool_guard(selected, args.lane)) and not args.no_tool_guard
    try:
        tool_lock_path = default_tool_lock_path()
    except YolomuxRootError as exc:
        print(f"CHECK REFUSED: {exc}", file=sys.stderr, flush=True)
        return EXIT_USAGE
    if guard_enabled:
        print(f"Acquiring YOLOmux expensive-tool lock: {tool_lock_path}", flush=True)

    # `expected_containers` records whether the lanes will actually route into Docker: only then is
    # an unobservable client at retirement a refusal rather than a clear, because only then could an
    # owned container still be draining.
    expected_containers = docker_image.container_available(REPO_ROOT)[0]

    started = time.monotonic()
    usage_before = child_usage_snapshot()
    results: list[LaneResult] = []
    certification: dict[str, object] | None = None
    certification_lane: LaneResult | None = None
    try:
        # One run token, exported so docker/run-tests.sh stamps every container this run launches
        # with the owner label, and restored on exit so a later in-process run is never mistaken for
        # this one's owner.
        with check_run_token_environment(), worktree_writer.acquire_worktree_writer(REPO_ROOT, purpose="test-gate"):
            with expensive_tool_lock(enabled=guard_enabled, lock_path=tool_lock_path):
                if guard_enabled:
                    active_records = active_yolomux_server_records()
                    if lower_current_process_priority(active_records):
                        ports = sorted({str(record.get("port") or "?") for record in active_records})
                        print(f"Detected {len(active_records)} active YOLOmux server(s) on port(s) {', '.join(ports)}; lowered check priority by nice +{TOOL_GUARD_NICE_DELTA}", flush=True)
                mode = "serial"
                if not args.serial:
                    mode = "parallel plus final serial" if any(lane.run_last for lane in selected) else "parallel"
                if selected:
                    print(f"Running {len(selected)} check lane(s) in {mode}: {', '.join(lane.name for lane in selected)}", flush=True)
                    results = run_functional_lanes(selected, serial=args.serial)
                # Always, even when a lane already failed. A phase that only runs on an otherwise
                # green gate cannot be trusted on a box where some lane is usually red: its own
                # regressions would never be observed.
                if certify:
                    print(f"Retiring lane processes, then running the exclusive certification phase: {len(CERTIFICATION_NODE_IDS)} unit(s)", flush=True)
                    certification, certification_lane = run_certification_phase(evidence_dir=evidence_dir, expected_containers=expected_containers)
                    print_certification(certification, certification_lane)
                elapsed = time.monotonic() - started
    except worktree_writer.WorktreeWriterBusy as exc:
        print(f"CHECK REFUSED: {exc}", file=sys.stderr, flush=True)
        return 3
    except ToolGuardBusy as exc:
        print(f"CHECK REFUSED: {exc}", file=sys.stderr, flush=True)
        return 3
    except KeyboardInterrupt:
        elapsed = time.monotonic() - started
        print("CHECK INTERRUPTED", file=sys.stderr, flush=True)
        write_performance_report(report_path, performance_report_payload(selected=selected, results=results, serial=args.serial, elapsed=elapsed, child_usage=child_usage_delta(usage_before, child_usage_snapshot()), interrupted=True, cpu_percent=args.cpu_percent))
        print(f"Test runtime report: {report_path}", file=sys.stderr, flush=True)
        return 130

    write_performance_report(report_path, performance_report_payload(selected=selected, results=results, serial=args.serial, elapsed=elapsed, child_usage=child_usage_delta(usage_before, child_usage_snapshot()), cpu_percent=args.cpu_percent, certification=certification))
    print(f"Test runtime report: {report_path}", flush=True)

    failed = [result.label for result in results if not result.ok]
    print("\n" + ("=" * 40))
    if failed:
        # A failed lane is a definite product red and outranks "could not be measured"; the
        # certification verdict was still printed above and is in the run report either way.
        certification_note = f"; certification: {certification['result']}" if certification else ""
        print(f"CHECK FAILED in {elapsed:.2f}s: " + ", ".join(failed) + certification_note)
        return EXIT_LANE_FAILED
    if not certify:
        print(f"CHECK PASSED in {elapsed:.2f}s (focused lane selection: NOT CERTIFIED, the exclusive latency phase did not run)")
        return 0
    exact_sha = certification.get("release", {}).get("exact_sha_certification", {})
    if exact_sha and not exact_sha.get("admitted", False):
        print(f"CHECK {latency_calibration.NOT_CERTIFIABLE} in {elapsed:.2f}s: exact_sha_certification_rejected")
        return EXIT_NOT_CERTIFIABLE
    if certification["result"] == "not-certifiable":
        print(f"CHECK {latency_calibration.NOT_CERTIFIABLE} in {elapsed:.2f}s: {certification['reason']}")
        return EXIT_NOT_CERTIFIABLE
    if certification["result"] != "certified":
        print(f"CHECK FAILED in {elapsed:.2f}s: {certification['reason']}")
        return EXIT_LANE_FAILED
    print(f"CHECK PASSED in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
