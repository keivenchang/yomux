# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Throwaway real-browser verification for the persisted-ring read landing."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.request import urlopen

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from tests.browser_helpers import browser_console
from tests.e2e_browser_harness import E2EBrowserHarness
from tests.e2e_browser_harness import browser  # noqa: F401
from tests.gate_harness import GateAuthCredentials
from tests.gate_harness import GateRuntimePaths
from tests.gate_harness import gate_auth_credentials  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import assert_no_surviving_local_service_daemons
from tests.gate_harness import retire_local_service_daemons_beneath
from tests.gate_harness import run_fixture_cleanup_phases
from yomux_lib import auth as auth_module
from yomux_lib import common
from yomux_lib import server_auth
from yomux_lib.infra.worktree_writer import child_process_artifact_environment
from yomux_lib.local_services.registry import bounded_process_table
from yomux_lib.local_services.registry import inherited_python_path
from yomux_lib.stats_current import client as stats_client
from yomux_lib.stats_current import service as stats_service
from yomux_lib.stats_current import storage
from tools.instance_isolation import synthetic_test_environment


REPO_ROOT = Path(__file__).resolve().parents[1]
PAIRS = ((300, 10), (900, 10), (3_600, 60), (86_400, 300))


def _repository_head_for_evidence() -> str:
    """Read this checkout's head without inheriting a child Git namespace."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    return subprocess.run(
        (
            "git",
            "-c",
            f"safe.directory={REPO_ROOT}",
            "-C",
            str(REPO_ROOT),
            "rev-parse",
            "--short=9",
            "HEAD",
        ),
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@pytest.mark.e2e
def test_ring_evidence_head_uses_checkout_when_cwd_and_git_namespace_differ(
    monkeypatch, tmp_path: Path,
) -> None:
    """Evidence must survive a fixture changing cwd or leaving Git child variables behind."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-repository"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))

    head = _repository_head_for_evidence()

    assert len(head) == 9
    assert all(character in "0123456789abcdef" for character in head)


@dataclass(frozen=True)
class ExternalRuntime:
    port: int
    tmux: object

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _wait_http(browser_driver, port: int) -> None:
    def serving(_driver) -> bool:
        try:
            with urlopen(f"http://127.0.0.1:{port}/login", timeout=0.5) as response:
                return response.status == 200
        except OSError:
            return False

    WebDriverWait(browser_driver, 20, poll_frequency=0.1).until(serving)


def _launch_server(
    port: int,
    session: str,
    log_path: Path,
    runtime_dir: Path,
    tmux_socket: Path,
) -> subprocess.Popen[bytes]:
    output = log_path.open("ab")
    fixture_root = runtime_dir.parent
    # Every child path is fixture-owned. Do not copy ambient values here: a host
    # Codex root, temporary directory, or lock path could reintroduce real client
    # state after synthetic_test_environment() scrubs the parent environment.
    fixture_overrides = {
        "HOME": str(fixture_root / "home"),
        "TMPDIR": str(fixture_root / "tmp"),
        "XDG_CONFIG_HOME": str(fixture_root / "xdg-config"),
        "XDG_DATA_HOME": str(fixture_root / "xdg-data"),
        "XDG_STATE_HOME": str(fixture_root / "xdg-state"),
        "XDG_CACHE_HOME": str(fixture_root / "xdg-cache"),
        "XDG_RUNTIME_DIR": str(fixture_root / "xdg-runtime"),
        "CLAUDE_CONFIG_DIR": str(fixture_root / "claude-config"),
        "YOLOMUX_CONFIG_DIR": str(fixture_root / "config"),
        "YOLOMUX_STATE_DIR": str(fixture_root / "state"),
        "YOLOMUX_CACHE_DIR": str(fixture_root / "cache"),
        "YOLOMUX_CODEX_HOME": str(fixture_root / "codex-home"),
        "CODEX_HOME": str(fixture_root / "codex-home"),
        "YOLOMUX_START_LOCK_DIR": str(fixture_root / "start-lock"),
        "YOLOMUX_TOOL_LOCK_PATH": str(fixture_root / "tool-lock"),
        "YOLOMUX_CA_DIR": str(fixture_root / "ca"),
        "YOLOMUX_LOG_DIR": str(fixture_root / "logs"),
        "YOLOMUX_WORKSPACE_BASE": str(fixture_root / "workspace"),
        "YOLOMUX_TMUX_SOCKET": str(tmux_socket),
        "YOLOMUX_CLAUDE_TRANSCRIPT_ROOT": str(fixture_root / "claude-transcripts"),
        "YOLOMUX_CODEX_TRANSCRIPT_ROOT": str(fixture_root / "codex-transcripts"),
        "YOLOMUX_OPENCODE_DATABASE": str(fixture_root / "opencode.db"),
    }
    environment = synthetic_test_environment(fixture_overrides)
    for path in (
        environment["HOME"],
        environment["TMPDIR"],
        environment["XDG_CONFIG_HOME"],
        environment["XDG_DATA_HOME"],
        environment["XDG_STATE_HOME"],
        environment["XDG_CACHE_HOME"],
        environment["XDG_RUNTIME_DIR"],
        environment["CLAUDE_CONFIG_DIR"],
        environment["YOLOMUX_CONFIG_DIR"],
        environment["YOLOMUX_STATE_DIR"],
        environment["YOLOMUX_CACHE_DIR"],
        environment["CODEX_HOME"],
        environment["YOLOMUX_START_LOCK_DIR"],
        environment["YOLOMUX_CA_DIR"],
        environment["YOLOMUX_LOG_DIR"],
        environment["YOLOMUX_WORKSPACE_BASE"],
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    for path in (
        environment["YOLOMUX_CLAUDE_TRANSCRIPT_ROOT"],
        environment["YOLOMUX_CODEX_TRANSCRIPT_ROOT"],
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    environment["YOLOMUX_START_LOAD_WAIT_SECONDS"] = "30"
    environment.pop("YOLOMUX_ROOT", None)
    environment["YOLOMUX_RUNTIME_DIR"] = str(runtime_dir)
    environment["PYTHONUNBUFFERED"] = "1"
    environment = child_process_artifact_environment(REPO_ROOT, environ=environment)
    environment["PYTHONPATH"] = inherited_python_path(environment)
    process = subprocess.Popen(
        (
            sys.executable,
            str(REPO_ROOT / "yomux.py"),
            "--backend",
            "python",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--sessions",
            session,
            "--http",
        ),
        cwd=REPO_ROOT,
        env=environment,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    output.close()
    return process


def test_ring_server_launch_replaces_ambient_client_roots(monkeypatch, tmp_path: Path) -> None:
    """A hostile operator environment must not reach the ring fixture child."""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    captured: dict[str, object] = {}

    class FakeProcess:
        def poll(self):
            return None

    def capture(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setenv("HOME", "/host/home")
    monkeypatch.setenv("TMPDIR", "/host/tmp")
    monkeypatch.setenv("YOLOMUX_CODEX_HOME", "/host/codex")
    monkeypatch.setenv("YOLOMUX_OPENCODE_DATABASE", "/host/opencode.db")
    monkeypatch.setattr(subprocess, "Popen", capture)

    process = _launch_server(
        7222,
        "fixture-session",
        tmp_path / "server.log",
        runtime_dir,
        tmp_path / "tmux.sock",
    )

    assert isinstance(process, FakeProcess)
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["TMPDIR"] == str(tmp_path / "tmp")
    assert environment["XDG_RUNTIME_DIR"] == str(tmp_path / "xdg-runtime")
    assert environment["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude-config")
    assert environment["YOLOMUX_CODEX_HOME"] == str(tmp_path / "codex-home")
    assert environment["YOLOMUX_OPENCODE_DATABASE"] == str(tmp_path / "opencode.db")
    assert environment["YOLOMUX_TMUX_SOCKET"] == str(tmp_path / "tmux.sock")
    assert all("/host/" not in value for value in environment.values())


def _stop_server(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _retire_browser_and_stop_server(browser_driver, runtime: ExternalRuntime, process: subprocess.Popen[bytes]) -> None:
    phases = []
    current_url = str(browser_driver.current_url)
    if current_url == runtime.base_url or current_url.startswith(f"{runtime.base_url}/"):
        phases.append((
            "browser diagnostic retirement",
            lambda: browser_console.retire_browser_after_strict_diagnostic_gate(browser_driver),
        ))
    phases.append(("server stop", lambda: _stop_server(process)))
    run_fixture_cleanup_phases("ring browser server stop", phases)


@pytest.mark.e2e
def test_ring_server_stop_retires_live_browser_transport_first(monkeypatch) -> None:
    events = []
    runtime = ExternalRuntime(port=43210, tmux=None)
    browser_driver = SimpleNamespace(current_url=f"{runtime.base_url}/?tabs=__debug__")
    process = object()
    monkeypatch.setattr(
        browser_console,
        "retire_browser_after_strict_diagnostic_gate",
        lambda current: events.append(("retire", current)),
    )
    monkeypatch.setattr(sys.modules[__name__], "_stop_server", lambda current: events.append(("stop", current)))

    _retire_browser_and_stop_server(browser_driver, runtime, process)

    assert events == [("retire", browser_driver), ("stop", process)]


@pytest.mark.e2e
def test_ring_server_stop_preserves_retirement_failure_after_stopping_process(monkeypatch) -> None:
    events = []
    runtime = ExternalRuntime(port=43210, tmux=None)
    browser_driver = SimpleNamespace(current_url=f"{runtime.base_url}/?tabs=__debug__")
    process = object()

    def fail_retirement(current) -> None:
        events.append(("retire", current))
        raise AssertionError("injected browser retirement failure")

    monkeypatch.setattr(browser_console, "retire_browser_after_strict_diagnostic_gate", fail_retirement)
    monkeypatch.setattr(sys.modules[__name__], "_stop_server", lambda current: events.append(("stop", current)))

    with pytest.raises(AssertionError, match="injected browser retirement failure"):
        _retire_browser_and_stop_server(browser_driver, runtime, process)

    assert events == [("retire", browser_driver), ("stop", process)]


def _statsd_processes_for_database(database_path: Path) -> list[dict[str, object]]:
    matches = []
    for pid, process in bounded_process_table(require_complete=True).items():
        try:
            arguments = shlex.split(process.command)
        except ValueError:
            continue
        if "yomux_lib.stats_current.service" not in arguments:
            continue
        try:
            database = arguments[arguments.index("--database") + 1]
            socket_value = arguments[arguments.index("--socket") + 1]
        except (IndexError, ValueError):
            continue
        if Path(database).resolve() != database_path.resolve():
            continue
        matches.append({"pid": pid, "socket": socket_value, "arguments": arguments})
    return matches


def _production_stats_client(
    browser_driver,
    database_path: Path,
    requested_socket: Path,
) -> tuple[stats_client.StatsCurrentClient, dict[str, object]]:
    def sole_daemon(_driver):
        processes = _statsd_processes_for_database(database_path)
        return processes[0] if len(processes) == 1 else False

    process = WebDriverWait(browser_driver, 10, poll_frequency=0.05).until(sole_daemon)
    actual_socket = Path(str(process["socket"]))
    expected_socket = stats_service.safe_socket_path(requested_socket, prefix="yolomux-statsd")
    assert actual_socket == expected_socket or actual_socket.name == expected_socket.name
    return stats_client.StatsCurrentClient(actual_socket, database_path), process


def _retire_ring_fixture_daemons(paths: GateRuntimePaths) -> None:
    """Settle every root-owned sidecar through the gate's shared lifecycle owner."""

    label = "ring browser restart"
    retire_local_service_daemons_beneath(paths.root, label=label)
    assert_no_surviving_local_service_daemons(paths.root, label=label)


@pytest.mark.e2e
def test_ring_fixture_daemon_retirement_uses_shared_root_owner(monkeypatch, tmp_path: Path) -> None:
    phases = []

    def retire(root: Path, *, label: str) -> tuple[object, ...]:
        phases.append(("retire", root, label))
        return ()

    def assert_settled(root: Path, *, label: str) -> None:
        phases.append(("settled", root, label))

    monkeypatch.setattr(sys.modules[__name__], "retire_local_service_daemons_beneath", retire)
    monkeypatch.setattr(sys.modules[__name__], "assert_no_surviving_local_service_daemons", assert_settled)

    _retire_ring_fixture_daemons(SimpleNamespace(root=tmp_path))

    assert phases == [
        ("retire", tmp_path, "ring browser restart"),
        ("settled", tmp_path, "ring browser restart"),
    ]


def _wait_ring_published(browser_driver, client: stats_client.StatsCurrentClient, source_generation: int) -> dict[str, object]:
    def published(_driver):
        status = client.status()
        ring = status.get("ring_writer") if isinstance(status, dict) else None
        if not isinstance(ring, dict):
            return False
        if int(ring.get("publications") or 0) < 1:
            return False
        if int(ring.get("last_source_generation") or 0) < source_generation:
            return False
        return status

    try:
        return WebDriverWait(browser_driver, 20, poll_frequency=0.1).until(published)
    except TimeoutException as error:
        raise AssertionError({
            "expected_source_generation": source_generation,
            "status": client.status(),
        }) from error


def _wait_snapshot_ready(
    browser_driver,
    client: stats_client.StatsCurrentClient,
    request: dict[str, object],
) -> tuple[dict[str, object], bytes]:
    """Wait for the cache to converge after a ring publication before reading it."""

    def ready(_driver):
        metadata, binary = client.snapshot(request)
        if metadata.get("ok") is True and binary:
            return metadata, binary
        return False

    try:
        return WebDriverWait(browser_driver, 20, poll_frequency=0.1).until(ready)
    except TimeoutException as error:
        raise AssertionError({
            "request": request,
            "status": client.status(),
            "resource_state": client.resource_state(),
        }) from error


def _seed_zero_and_gap(
    client: stats_client.StatsCurrentClient,
    usage_source: str,
) -> tuple[dict[str, object], dict[str, int]]:
    now = int(time.time())
    aligned = now - now % 10
    start = aligned - 120
    gap_start = aligned - 60
    gap_end = aligned - 40
    source = "gpu:ring-e2e"
    result = client.append(
        observations=(
            storage.Observation(
                "ring-e2e-nonzero-before",
                "gpu",
                source,
                start + 10,
                "ring-e2e-before",
                1,
                {"util_percent": 40, "memory_used_bytes": 400, "memory_capacity_bytes": 1000, "label": "Ring E2E GPU"},
            ),
            storage.Observation(
                "ring-e2e-explicit-zero",
                "gpu",
                source,
                gap_end + 10,
                "ring-e2e-after",
                1,
                {"util_percent": 0, "memory_used_bytes": 0, "memory_capacity_bytes": 1000, "label": "Ring E2E GPU"},
            ),
            storage.Observation(
                "ring-e2e-nonzero-after",
                "gpu",
                source,
                aligned - 5,
                "ring-e2e-after",
                1,
                {"util_percent": 70, "memory_used_bytes": 700, "memory_capacity_bytes": 1000, "label": "Ring E2E GPU"},
            ),
        ),
        usage_atoms=(storage.UsageAtom(
            "ring-e2e-usage",
            "input",
            "text",
            "none",
            "tokens",
            aligned - 5,
            {
                "quantity": 12,
                "provider": "openai",
                "model": "gpt",
                "agent_id": "ring-writer",
                "telemetry_complete": True,
            },
        ),),
        coverage_epochs=(
            storage.CoverageEpoch("gpu", source, "ring-e2e-before", start, gap_start, 10, 1),
            storage.CoverageEpoch("gpu", source, "ring-e2e-after", gap_end, None, 10, 1),
            storage.CoverageEpoch(
                "agent_tokens",
                usage_source,
                "usage-scan-before-direct-atom",
                start,
                gap_start,
                60,
                1,
            ),
        ),
        unavailable_spans=(
            storage.UnavailableSpan("gpu", source, "ring-e2e-gap", gap_start, gap_end, 10, "fixture_downtime", 1),
        ),
    )
    assert result.get("ok") is True, result
    assert int(result.get("accepted") or 0) == 8, result
    return result, {"gap_start": gap_start, "gap_end": gap_end, "zero_start": gap_end + 10}


def test_ring_writer_accepts_explicit_zero_beside_downtime_gap(tmp_path: Path) -> None:
    wall_now = 1_800_000_000
    monotonic_now = [0.0]
    database = tmp_path / storage.DATABASE_FILENAME
    service = stats_service.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        clock=lambda: wall_now,
        monotonic=lambda: monotonic_now[0],
        randomizer=lambda: 0.0,
    )
    request = {
        "action": "append",
        "protocol_version": storage.MIN_WRITER_PROTOCOL,
        "schema_generation": storage.SCHEMA_VERSION,
        "observations": [
            {
                "event_id": "before",
                "family": "gpu",
                "source_id": "gpu:ring-e2e",
                "observed_at": wall_now - 110,
                "epoch_id": "before",
                "owner_generation": 1,
                "payload": {"util_percent": 40, "memory_used_bytes": 400, "memory_capacity_bytes": 1000, "label": "Ring E2E GPU"},
            },
            {
                "event_id": "zero",
                "family": "gpu",
                "source_id": "gpu:ring-e2e",
                "observed_at": wall_now - 30,
                "epoch_id": "after",
                "owner_generation": 1,
                "payload": {"util_percent": 0, "memory_used_bytes": 0, "memory_capacity_bytes": 1000, "label": "Ring E2E GPU"},
            },
        ],
        "usage_atoms": [],
        "usage_tombstones": [],
        "coverage_epochs": [
            {"family": "gpu", "source_id": "gpu:ring-e2e", "epoch_id": "before", "started_at": wall_now - 120, "ended_at": wall_now - 60, "native_cadence_seconds": 10, "owner_generation": 1},
            {"family": "gpu", "source_id": "gpu:ring-e2e", "epoch_id": "after", "started_at": wall_now - 40, "ended_at": None, "native_cadence_seconds": 10, "owner_generation": 1},
        ],
        "unavailable_spans": [
            {"family": "gpu", "source_id": "gpu:ring-e2e", "epoch_id": "gap", "started_at": wall_now - 60, "ended_at": wall_now - 40, "native_cadence_seconds": 10, "reason": "fixture_downtime", "owner_generation": 1},
        ],
    }
    with storage.Store.open(database) as store:
        service.writer = store
        accepted, binary = service.handle_with_binary(request)
        assert accepted["accepted"] == 5
        assert binary == b""
        service._build_once(store, True, frozenset())
        assert service._cache is not None
        candidate = service._cache.generation
        cells = frozenset(service._pending_ring_dirty)
        store.initialize_ring_storage()
        writes = service._ring_writes(
            candidate,
            service._restart_ring_cells(store, candidate, cells),
        )
        assert writes
        store.publish_ring_buckets(
            buckets=writes,
            source_generation=candidate.source_generation,
            published_at=wall_now,
        )


def _graph_state(driver) -> dict[str, object]:
    return driver.execute_script(
        """
        const client = typeof jsDebugCurrentStatsClientState === 'object'
          ? jsDebugCurrentStatsClientState.client
          : null;
        const controller = client?.controller?.();
        const generation = controller?.generation?.();
        const selection = controller?.selection?.();
        const graph = document.querySelector('.js-debug-panel [data-js-debug-graph]');
        const renderNodes = [...document.querySelectorAll([
          '.js-debug-panel polyline[data-js-debug-series]',
          '.js-debug-panel path[data-js-debug-area-series]',
          '.js-debug-panel rect[data-js-debug-bar-series]',
        ].join(','))];
        const zeroBars = [...document.querySelectorAll('.js-debug-panel rect[data-js-debug-bar-series^="gpu:gpuUtil:"]')]
          .filter(node => Number(node.dataset.jsDebugBarTotal) === 0)
          .map(node => ({series: node.dataset.jsDebugBarSeries, height: Number(node.getAttribute('height')), width: Number(node.getAttribute('width'))}));
        const gapRects = [...document.querySelectorAll('.js-debug-panel [data-js-debug-history-coverage-family="gpu"] [data-js-debug-history-no-data-range]')]
          .map(node => ({x: Number(node.getAttribute('x')), width: Number(node.getAttribute('width'))}));
        return {
          selection,
          range: Number(generation?.range_seconds || 0),
          requested: generation?.requested_resolution,
          resolution: Number(generation?.resolution_seconds || 0),
          bucketCount: Array.isArray(generation?.buckets) ? generation.buckets.length : -1,
          noData: Array.isArray(generation?.no_data) ? generation.no_data : [],
          cacheGeneration: Number(generation?.cache_generation || 0),
          sourceGeneration: Number(generation?.source_generation || 0),
           generationCostTokens: Number(generation?.cost_report?.total_tokens || 0),
          controllerGenerationKey: generation && typeof jsDebugCurrentStatsGenerationKey === 'function'
            ? jsDebugCurrentStatsGenerationKey(generation)
            : '',
          paintedGenerationKey: String(jsDebugCurrentStatsClientState.paintedGenerationKey || ''),
          pendingGenerationKey: String(jsDebugCurrentStatsClientState.pendingGenerationKey || ''),
          graphGenerationKey: graph?.dataset?.jsDebugStatsGenerationKey || '',
          serverSequence: Number(jsDebugStatsServerSequence || 0),
          historyState: graph?.dataset?.jsDebugHistoryState || '',
          busy: graph?.getAttribute?.('aria-busy') || '',
          graphRenderedAt: Number(graph?.dataset?.jsDebugGraphRenderedAt || 0),
          graphRenderPending: graph?.dataset?.jsDebugGraphRefreshPending || '',
          renderPaths: renderNodes.length,
          renderedCharts: document.querySelectorAll('.js-debug-panel .js-debug-chart svg').length,
          generationSeries: Array.isArray(generation?.buckets)
            ? generation.buckets.slice(-8).map(bucket => ({
              start: Number(bucket?.start || 0),
              duration: Number(bucket?.duration || 0),
              series: Object.keys(bucket?.series || {}).filter(name => name.startsWith('gpu_')),
            }))
            : [],
          focusedChartToggle: document.activeElement?.dataset?.jsDebugChartToggle || '',
          zeroBars,
          gapRects,
          readiness: typeof jsDebugHistoryReadinessSnapshot === 'function' ? jsDebugHistoryReadinessSnapshot() : null,
          debugEvents: typeof jsDebugEventsForTest === 'function' ? jsDebugEventsForTest().slice(-12) : [],
          bootErrors: jsDebugFailureEvents('error'),
          bootRejections: jsDebugFailureEvents('rejection'),
        };
        """
    )


def _pair_state_ready(
    state: dict[str, object],
    range_seconds: int,
    requested_resolution: int | str,
    resolution_seconds: int,
) -> dict[str, object] | bool:
    if (
        state["range"] != range_seconds
        or state["requested"] != requested_resolution
        or state["resolution"] != resolution_seconds
    ):
        return False
    controller_key = state["controllerGenerationKey"]
    if (
        not controller_key
        or state["pendingGenerationKey"]
        or state["paintedGenerationKey"] != controller_key
        or state["graphGenerationKey"] != controller_key
    ):
        return False
    if state["historyState"] != "ready" or state["busy"] != "false":
        return False
    if state["renderPaths"] < 1 or state["renderedCharts"] < 1:
        return False
    return state


def _wait_pair(
    driver,
    range_seconds: int,
    requested_resolution: int | str,
    resolution_seconds: int,
) -> dict[str, object]:
    def ready(_driver):
        return _pair_state_ready(
            _graph_state(driver),
            range_seconds,
            requested_resolution,
            resolution_seconds,
        )

    try:
        return WebDriverWait(driver, 20, poll_frequency=0.05).until(ready)
    except TimeoutException as error:
        raise AssertionError({
            "expected_range": range_seconds,
            "expected_requested_resolution": requested_resolution,
            "expected_resolution": resolution_seconds,
            "graph": _graph_state(driver),
        }) from error


@pytest.mark.e2e
def test_ring_pair_wait_rejects_stale_auto_paint_during_explicit_selection() -> None:
    stale_auto_paint = {
        "range": 3_600,
        "requested": 60,
        "resolution": 60,
        "controllerGenerationKey": "3600:60:60:12:42",
        "paintedGenerationKey": "3600:AUTO:60:11:41",
        "pendingGenerationKey": "3600:60:60:12:42",
        "graphGenerationKey": "3600:AUTO:60:11:41",
        "historyState": "ready",
        "busy": "false",
        "renderPaths": 1,
        "renderedCharts": 12,
        "generationCostTokens": 12,
    }

    assert _pair_state_ready(stale_auto_paint, 3_600, 60, 60) is False
    converged = {
        **stale_auto_paint,
        "paintedGenerationKey": stale_auto_paint["controllerGenerationKey"],
        "pendingGenerationKey": "",
        "graphGenerationKey": stale_auto_paint["controllerGenerationKey"],
    }
    assert _pair_state_ready(converged, 3_600, 60, 60) is converged


def _set_range_from_slider(driver, target: int) -> None:
    options = driver.execute_script(
        "return [...document.querySelectorAll('.js-debug-panel datalist#js-debug-range-options option')].map(option => Number(option.dataset.jsDebugRange));"
    )
    assert target in options, options
    target_index = options.index(target)
    slider = driver.find_element(By.CSS_SELECTOR, ".js-debug-panel [data-js-debug-range-slider]")
    current_index = int(round(float(slider.get_attribute("value"))))
    if current_index == target_index:
        return
    driver.execute_script(
        """
        const targetIndex = arguments[0];
        let slider = document.querySelector('.js-debug-panel [data-js-debug-range-slider]');
        slider.focus({preventScroll: true});
        slider.value = String(targetIndex);
        slider.dispatchEvent(new Event('input', {bubbles: true}));
        slider = document.querySelector('.js-debug-panel [data-js-debug-range-slider]');
        slider.value = String(targetIndex);
        slider.dispatchEvent(new Event('change', {bubbles: true}));
        """,
        target_index,
    )
    try:
        WebDriverWait(driver, 10, poll_frequency=0.05).until(
            lambda _driver: int(_graph_state(driver)["range"]) == target
        )
    except TimeoutException as error:
        current = driver.find_element(By.CSS_SELECTOR, ".js-debug-panel [data-js-debug-range-slider]")
        driver.set_script_timeout(8)
        controller_probe = driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
            const request = controller?.buildRequest?.();
            if (!request) {
              done({error: 'controller request unavailable'});
              return;
            }
            const url = `/api/stats-snapshot?range_seconds=${encodeURIComponent(request.range_seconds)}&resolution=${encodeURIComponent(request.resolution)}&client_id=${encodeURIComponent(request.client_id)}&since_generation=0`;
            fetch(url, {cache: 'no-store'})
              .then(async response => {
                const text = await response.text();
                let snapshot = null;
                try { snapshot = JSON.parse(text); }
                catch (parseError) {
                  done({status: response.status, bytes: text.length, error: String(parseError)});
                  return;
                }
                let acceptance = 'accepted';
                try { controller.acceptSnapshot(snapshot); }
                catch (acceptError) { acceptance = String(acceptError); }
                done({
                  status: response.status,
                  bytes: text.length,
                  acceptance,
                  range: snapshot?.range_seconds,
                  requested: snapshot?.requested_resolution,
                  resolution: snapshot?.resolution_seconds,
                  sourceGeneration: snapshot?.source_generation,
                  cacheGeneration: snapshot?.cache_generation,
                  bucketCount: Array.isArray(snapshot?.buckets) ? snapshot.buckets.length : -1,
                });
              })
              .catch(fetchError => done({error: String(fetchError)}));
            """
        )
        raise AssertionError({
            "target": target,
            "target_index": target_index,
            "options": options,
                "slider_value": current.get_attribute("value"),
            "slider_rect": current.rect,
            "controller_probe": controller_probe,
            "graph": _graph_state(driver),
        }) from error


def _set_resolution_from_select(driver, target: int) -> None:
    value = str(target)
    element = WebDriverWait(driver, 5, poll_frequency=0.02).until(
        lambda _driver: driver.execute_script(
            """
            const target = arguments[0];
            const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
            return select && [...select.options].some(option => option.value === target) ? select : null;
            """,
            value,
        )
    )
    resolution = Select(element)
    resolution.select_by_value(value)


def _show_gpu_util(driver) -> None:
    menu = driver.find_element(By.CSS_SELECTOR, ".js-debug-panel [data-js-debug-chart-menu]")
    if menu.get_attribute("open") is None:
        menu.find_element(By.CSS_SELECTOR, "summary").click()
    for chart_key in ("gpuUtil",):
        selector = f'.js-debug-panel [data-js-debug-chart-toggle="{chart_key}"]'
        toggle = driver.find_element(By.CSS_SELECTOR, selector)
        if not toggle.is_selected():
            toggle.click()
        WebDriverWait(driver, 5, poll_frequency=0.02).until(
            lambda _driver, target=selector: driver.find_element(
                By.CSS_SELECTOR,
                target,
            ).is_selected()
        )


def _prove_focused_chart_control_converges(driver) -> dict[str, object]:
    baseline = _graph_state(driver)
    accepted = driver.execute_script(
        """
        const toggle = document.querySelector('.js-debug-panel [data-js-debug-chart-toggle="gpuUtil"]');
        toggle.focus({preventScroll: true});
        const controller = jsDebugCurrentStatsClientState.client.controller();
        const current = controller.generation();
        const candidate = JSON.parse(JSON.stringify(current));
        candidate.source_generation += 1;
        candidate.cache_generation += 1;
        return {
          key: toggle.dataset.jsDebugChartToggle,
          focused: document.activeElement === toggle,
          accepted: controller.acceptSnapshot(candidate),
        };
        """
    )
    assert accepted == {"key": "gpuUtil", "focused": True, "accepted": True}, accepted

    def converged(_driver):
        state = _graph_state(driver)
        if state["controllerGenerationKey"] == baseline["controllerGenerationKey"]:
            return False
        if state["focusedChartToggle"] != "gpuUtil":
            return False
        if state["pendingGenerationKey"]:
            return False
        if state["paintedGenerationKey"] != state["controllerGenerationKey"]:
            return False
        if state["graphGenerationKey"] != state["controllerGenerationKey"]:
            return False
        if state["renderPaths"] < 1:
            return False
        return state

    try:
        return WebDriverWait(driver, 10, poll_frequency=0.05).until(converged)
    except TimeoutException as error:
        raise AssertionError({
            "baseline": baseline,
            "accepted": accepted,
            "graph": _graph_state(driver),
        }) from error


def _exercise_pairs(driver) -> tuple[list[dict[str, object]], dict[str, object]]:
    results = []
    _wait_pair(driver, 900, "AUTO", 10)
    for range_seconds, resolution_seconds in PAIRS:
        started = time.monotonic()
        _set_range_from_slider(driver, range_seconds)
        _set_resolution_from_select(driver, resolution_seconds)
        state = _wait_pair(driver, range_seconds, resolution_seconds, resolution_seconds)
        results.append({
            "range": range_seconds,
            "resolution": resolution_seconds,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "render_paths": state["renderPaths"],
            "charts": state["renderedCharts"],
            "buckets": state["bucketCount"],
            "cache_generation": state["cacheGeneration"],
            "source_generation": state["sourceGeneration"],
            "cost_tokens": state["generationCostTokens"],
        })
        assert results[-1]["cost_tokens"] == 12, json.dumps(state, sort_keys=True)
        assert not [
            span
            for span in state["noData"]
            if span.get("family") in {"agent_tokens", "cost"}
            and span.get("reason") == "coverage_gap"
        ], state
        assert state["bootErrors"] == [], state
        assert state["bootRejections"] == [], state
    _set_range_from_slider(driver, 300)
    _set_resolution_from_select(driver, 10)
    return results, _wait_pair(driver, 300, 10, 10)


def _load_real_stats_page(
    request,
    driver,
    runtime: ExternalRuntime,
    credentials: GateAuthCredentials,
) -> E2EBrowserHarness:
    harness = E2EBrowserHarness(
        driver,
        runtime,
        test_id=request.node.nodeid,
        evidence_root=Path("/tmp/yolomux-ring-browser-evidence"),
        bound=25,
    )
    # Retire the previous real app document before removing its cookie. Otherwise its scheduled
    # filesystem batch can observe the cleared session during the navigation window and produce a
    # spurious 401 that the strict browser-retirement gate correctly reports.
    driver.get("about:blank")
    driver.execute_cdp_cmd("Network.clearBrowserCookies", {})
    harness.authenticate(credentials)
    harness.load(tabs=("files", "__debug__", str(runtime.tmux.sessions[0])))
    driver.find_element(By.CSS_SELECTOR, '.dockview-pane-tab[data-pane-tab="__debug__"]').click()
    WebDriverWait(driver, 12, poll_frequency=0.05).until(
        lambda _driver: driver.find_element(By.CSS_SELECTOR, '.js-debug-panel [data-js-debug-subtab="graph"]')
    ).click()
    return harness


@dataclass(frozen=True)
class _RingRuntime:
    """Everything a real-page ring journey needs to launch and re-launch one server."""

    runtime: ExternalRuntime
    port: int
    session: str
    database_path: Path
    requested_socket: Path
    service_runtime_dir: Path
    log_path: Path


def _prepare_ring_runtime(
    monkeypatch,
    gate_runtime_paths: GateRuntimePaths,
    gate_http_port,
    gate_tmux,
    gate_auth_credentials: GateAuthCredentials,
) -> _RingRuntime:
    """Authenticate one user and resolve the runtime addresses both ring journeys launch against.

    ONE owner for this setup. A second journey that re-typed it would drift from the first the
    moment either changed, and the addresses here -- database, socket, runtime dir -- are exactly
    what the restart half of both journeys must reuse unchanged.
    """
    assert 7900 <= gate_http_port.port <= 7999
    monkeypatch.delenv(common.TEST_AUTH_BYPASS_ENV, raising=False)
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    auth_path = gate_runtime_paths.config_dir / "auth.yaml"
    assert common.AUTH_CONFIG_PATH == auth_path
    auth_module.write_auth_config(
        auth_path,
        auth_module.auth_config_text((auth_module.AuthUser(
            username=gate_auth_credentials.username,
            password=gate_auth_credentials.password,
            role=gate_auth_credentials.role,
        ),)),
    )
    initialized = auth_module.initialize_auth_config(auth_path)
    assert len(initialized) == 1
    assert auth_module.auth_password_is_hash(initialized[0].password)

    port = gate_http_port.release()
    service_runtime_dir = Path(tempfile.mkdtemp(prefix="ring-runtime-", dir=gate_runtime_paths.root))
    runtime_environ = synthetic_test_environment({
        "HOME": str(gate_runtime_paths.home_dir),
        "TMPDIR": str(gate_runtime_paths.root / "tmp"),
        "XDG_CONFIG_HOME": str(gate_runtime_paths.config_dir),
        "XDG_DATA_HOME": str(gate_runtime_paths.root / "xdg-data"),
        "XDG_STATE_HOME": str(gate_runtime_paths.state_dir),
        "XDG_CACHE_HOME": str(gate_runtime_paths.cache_dir),
        "XDG_RUNTIME_DIR": str(gate_runtime_paths.root / "xdg-runtime"),
        "CLAUDE_CONFIG_DIR": str(gate_runtime_paths.root / "claude-config"),
        "YOLOMUX_CODEX_HOME": str(gate_runtime_paths.root / "codex-home"),
        "CODEX_HOME": str(gate_runtime_paths.root / "codex-home"),
        "YOLOMUX_CLAUDE_TRANSCRIPT_ROOT": str(gate_runtime_paths.root / "claude-transcripts"),
        "YOLOMUX_CODEX_TRANSCRIPT_ROOT": str(gate_runtime_paths.root / "codex-transcripts"),
        "YOLOMUX_OPENCODE_DATABASE": str(gate_runtime_paths.root / "opencode.db"),
        "YOLOMUX_TMUX_SOCKET": str(gate_tmux.socket_path),
    })
    # This journey passes an explicit runtime directory to an external process. Do not let the
    # suite-wide YOLOMUX_ROOT reinterpret that path and prepend the long gate root a second time;
    # the resulting control socket can exceed Linux's sockaddr_un pathname limit before the server
    # starts.
    runtime_environ.pop("YOLOMUX_ROOT", None)
    runtime_environ["YOLOMUX_RUNTIME_DIR"] = str(service_runtime_dir)
    with monkeypatch.context() as runtime_patch:
        runtime_patch.setattr(
            storage.common,
            "RUNTIME_DIR",
            common.resolve_yolomux_roots(runtime_environ).runtime_dir,
        )
        requested_socket = storage.default_socket_path(gate_runtime_paths.state_dir)
    log_file = tempfile.NamedTemporaryFile(prefix="yolomux-ring-server-", suffix=".txt", dir="/tmp", delete=False)
    log_path = Path(log_file.name)
    log_file.close()
    return _RingRuntime(
        runtime=ExternalRuntime(port=port, tmux=gate_tmux),
        port=port,
        session=str(gate_tmux.sessions[0]),
        database_path=storage.default_database_path(gate_runtime_paths.state_dir),
        requested_socket=requested_socket,
        service_runtime_dir=service_runtime_dir,
        log_path=log_path,
    )


@pytest.mark.e2e
@pytest.mark.socket
def test_ring_landing_real_page_restart_and_zero_gap(
    request,
    browser,
    monkeypatch,
    gate_runtime_paths: GateRuntimePaths,
    gate_http_port,
    gate_tmux,
    gate_auth_credentials: GateAuthCredentials,
) -> None:
    prepared = _prepare_ring_runtime(
        monkeypatch, gate_runtime_paths, gate_http_port, gate_tmux, gate_auth_credentials,
    )
    port = prepared.port
    runtime = prepared.runtime
    service_runtime_dir = prepared.service_runtime_dir
    database_path = prepared.database_path
    requested_socket = prepared.requested_socket
    log_path = prepared.log_path
    process = _launch_server(port, prepared.session, log_path, service_runtime_dir, gate_tmux.socket_path)
    latest_statsd_pid = 0
    try:
        _wait_http(browser, port)
        client, initial_statsd = _production_stats_client(
            browser,
            database_path,
            requested_socket,
        )
        assert client.ensure_started()
        seeded, markers = _seed_zero_and_gap(client, f"port:{port}")
        first_status = _wait_ring_published(browser, client, int(seeded["source_generation"]))
        latest_statsd_pid = int(first_status.get("pid") or 0)
        assert latest_statsd_pid == initial_statsd["pid"]

        _load_real_stats_page(request, browser, runtime, gate_auth_credentials)
        _wait_pair(browser, 900, "AUTO", 10)
        _show_gpu_util(browser)
        focused_control = _prove_focused_chart_control_converges(browser)
        _load_real_stats_page(request, browser, runtime, gate_auth_credentials)
        _show_gpu_util(browser)
        first_pairs, first_zero_gap = _exercise_pairs(browser)
        matching_gap = [
            span for span in first_zero_gap["noData"]
            if span.get("family") == "gpu"
            and span.get("source_id") == "gpu:ring-e2e"
            and int(span.get("start") or 0) == markers["gap_start"]
            and int(span.get("end") or 0) == markers["gap_end"]
        ]
        assert first_zero_gap["zeroBars"], first_zero_gap
        assert all(item["height"] > 0 and item["width"] > 0 for item in first_zero_gap["zeroBars"]), first_zero_gap
        assert matching_gap, first_zero_gap
        assert first_zero_gap["gapRects"], first_zero_gap

        _retire_browser_and_stop_server(browser, runtime, process)
        sidecar_survived_server_stop = latest_statsd_pid > 0 and latest_statsd_pid in bounded_process_table(require_complete=True)
        _retire_ring_fixture_daemons(gate_runtime_paths)

        restart_started = time.monotonic()
        process = _launch_server(port, str(gate_tmux.sessions[0]), log_path, service_runtime_dir, gate_tmux.socket_path)
        _wait_http(browser, port)
        server_ready_ms = round((time.monotonic() - restart_started) * 1000, 1)
        restarted_client, restarted_statsd = _production_stats_client(
            browser,
            database_path,
            requested_socket,
        )
        assert restarted_client.ensure_started()
        restarted_status_before_browser = restarted_client.status()
        latest_statsd_pid = int(restarted_status_before_browser.get("pid") or 0)
        assert latest_statsd_pid == int(restarted_statsd["pid"])
        browser_started = time.monotonic()
        _load_real_stats_page(request, browser, runtime, gate_auth_credentials)
        _show_gpu_util(browser)
        _set_range_from_slider(browser, 300)
        _set_resolution_from_select(browser, 10)
        restarted = _wait_pair(browser, 300, 10, 10)
        browser_ready_ms = round((time.monotonic() - browser_started) * 1000, 1)
        matching_restart_gap = [
            span for span in restarted["noData"]
            if span.get("family") == "gpu"
            and span.get("source_id") == "gpu:ring-e2e"
            and int(span.get("start") or 0) == markers["gap_start"]
            and int(span.get("end") or 0) == markers["gap_end"]
        ]
        assert restarted["zeroBars"], restarted
        assert matching_restart_gap, restarted
        assert restarted["gapRects"], restarted
        assert restarted["renderPaths"] > 0, restarted
        restarted_total = restarted["generationCostTokens"]
        assert restarted_total == 12, restarted
        assert not [
            span
            for span in restarted["noData"]
            if span.get("family") in {"agent_tokens", "cost"}
            and span.get("reason") == "coverage_gap"
        ], restarted

        evidence = {
            "head": _repository_head_for_evidence(),
            "port": port,
            "pairs": first_pairs,
            "zero_bars": first_zero_gap["zeroBars"],
            "gap_rects": first_zero_gap["gapRects"],
            "focused_control": {
                "key": focused_control["focusedChartToggle"],
                "controller_generation_key": focused_control["controllerGenerationKey"],
                "graph_generation_key": focused_control["graphGenerationKey"],
                "render_paths": focused_control["renderPaths"],
            },
            "restart": {
                "server_ready_ms": server_ready_ms,
                "browser_ready_ms": browser_ready_ms,
                "render_paths": restarted["renderPaths"],
                "zero_bars": restarted["zeroBars"],
                "gap_rects": restarted["gapRects"],
                "cost_tokens": restarted_total,
                "ring_publications_before_browser": int((restarted_status_before_browser.get("ring_writer") or {}).get("publications") or 0),
                "sidecar_survived_server_stop": sidecar_survived_server_stop,
            },
        }
        print("RING_BROWSER_EVIDENCE=" + json.dumps(evidence, sort_keys=True), flush=True)
    finally:
        _retire_browser_and_stop_server(browser, runtime, process)
        _retire_ring_fixture_daemons(gate_runtime_paths)


# --- publish -> invalidate -> production restart -> first ready page ----------------------------
# The journey above proves a restart still renders. This one proves the two OWED outcomes do not
# get conflated: an owed cell the materializer can still rebuild must be republished before
# readiness, and an owed cell that has aged out of its window must become an explicit gap -- the
# contradicted payload dropped, never served.


def _durable_ring_state(database_path: Path) -> dict[str, object]:
    """Read the durable ring and ledger directly, with no daemon in the way."""
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        slots = {
            (int(row[0]), int(row[1])): {
                "bucket_start": row[2],
                "complete": int(row[3]),
                "source_generation": int(row[4]),
                "ring_generation": int(row[5]),
                "published_at": float(row[6]),
            }
            for row in connection.execute(
                "SELECT resolution_seconds, slot_index, bucket_start, complete, source_generation, "
                "ring_generation, published_at FROM aggregate_ring_slots"
            )
        }
        pending = {
            (int(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT resolution_seconds, bucket_start FROM ring_invalidations WHERE applied_at IS NULL"
            )
        }
    finally:
        connection.close()
    return {"slots": slots, "pending": pending}


def _slot_address(resolution_seconds: int, bucket_start: int) -> tuple[int, int]:
    return (resolution_seconds, storage.ring_slot_index(resolution_seconds, bucket_start))


def _ring_observation(event_id: str, observed_at: float, util_percent: int) -> storage.Observation:
    return storage.Observation(
        event_id,
        "gpu",
        "gpu:ring-e2e",
        observed_at,
        "ring-e2e-after",
        1,
        {
            "util_percent": util_percent,
            "memory_used_bytes": util_percent * 10,
            "memory_capacity_bytes": 1000,
            "label": "Ring E2E GPU",
        },
    )


@pytest.mark.e2e
@pytest.mark.socket
def test_ring_landing_republishes_rebuildable_and_gaps_unrebuildable_owed_cells(
    request,
    browser,
    monkeypatch,
    gate_runtime_paths: GateRuntimePaths,
    gate_http_port,
    gate_tmux,
    gate_auth_credentials: GateAuthCredentials,
) -> None:
    """One mutation, two owed outcomes, across a real production-style restart.

    The contradicting append touches one instant, so it owes a cell at EVERY resolution. By the
    time the restarted process repairs, the 1-second cell has aged out of the 1-second
    materializer window while its slot still physically holds the contradicted payload, and the
    60-second cell is still inside its window. The first is an honest gap: the payload is dropped
    and the row retired, so nothing stale can be served. The second must be republished before
    readiness and must still render the retained cost on the first authenticated page read.
    """
    prepared = _prepare_ring_runtime(
        monkeypatch, gate_runtime_paths, gate_http_port, gate_tmux, gate_auth_credentials,
    )
    second_capacity = storage.stats_resolution.RING_CAPACITIES[1]
    process = _launch_server(prepared.port, prepared.session, prepared.log_path, prepared.service_runtime_dir, gate_tmux.socket_path)
    latest_statsd_pid = 0
    try:
        _wait_http(browser, prepared.port)
        client, _initial = _production_stats_client(
            browser, prepared.database_path, prepared.requested_socket,
        )
        assert client.ensure_started()
        seeded, _markers = _seed_zero_and_gap(client, f"port:{prepared.port}")
        # A fact far enough back that its 1-second bucket is still inside the 300-slot window now
        # and provably outside it after the restart wait below. Everything else in the journey is
        # derived from what the product then publishes, not from these constants.
        aligned = int(time.time())
        aligned -= aligned % 10
        left_edge = aligned - 240
        aging = client.append(observations=(
            _ring_observation("ring-e2e-left-edge", float(left_edge) + 0.5, 55),
        ))
        assert aging.get("ok") is True, aging
        published_status = _wait_ring_published(browser, client, int(aging["source_generation"]))
        latest_statsd_pid = int(published_status.get("pid") or 0)

        before = _durable_ring_state(prepared.database_path)
        aging_address = _slot_address(1, left_edge)
        assert before["slots"][aging_address]["bucket_start"] == left_edge, (
            "the 1-second slot under test was never published"
        )
        rebuildable_start = left_edge - left_edge % 60
        rebuildable_address = _slot_address(60, rebuildable_start)
        assert before["slots"][rebuildable_address]["bucket_start"] == rebuildable_start

        # Production-style restart: the page retires, the server stops, the sidecar stops.
        _retire_browser_and_stop_server(browser, prepared.runtime, process)
        _retire_ring_fixture_daemons(gate_runtime_paths)
        latest_statsd_pid = 0

        # With no daemon running, wait out the 1-second window and contradict both cells through
        # the same storage owner statsd itself appends with. Contradicting while nothing can
        # republish is what makes the two outcomes deterministic rather than a race with a flush.
        window_clear_at = left_edge + second_capacity + 1
        WebDriverWait(browser, 300, poll_frequency=0.5).until(
            lambda _driver: time.time() > window_clear_at
        )
        with storage.Store.open(prepared.database_path) as offline:
            contradiction = offline.append_batch(observations=(
                _ring_observation("ring-e2e-contradiction", float(left_edge) + 0.75, 61),
            ))
            assert contradiction.observations_accepted == 1, contradiction
        owed = _durable_ring_state(prepared.database_path)["pending"]
        assert (1, left_edge) in owed, owed
        assert (60, rebuildable_start) in owed, owed

        process = _launch_server(prepared.port, prepared.session, prepared.log_path, prepared.service_runtime_dir, gate_tmux.socket_path)
        _wait_http(browser, prepared.port)
        restarted_client, restarted_statsd = _production_stats_client(
            browser, prepared.database_path, prepared.requested_socket,
        )
        assert restarted_client.ensure_started()
        latest_statsd_pid = int(restarted_statsd["pid"])
        restarted_status = _wait_ring_published(browser, restarted_client, int(contradiction.source_generation))

        # Measured BEFORE the first page read: readiness already owns both outcomes.
        after = _durable_ring_state(prepared.database_path)
        aged_out = {
            "left_edge": left_edge,
            "before_slot": before["slots"][aging_address],
            "after_slot": after["slots"][aging_address],
            "still_pending": (1, left_edge) in after["pending"],
            "ring_writer": restarted_status.get("ring_writer"),
        }
        assert (1, left_edge) not in after["pending"], aged_out
        # The modular slot may already hold its next-lap bucket. The logical contract is only that
        # the contradicted old identity is gone; clearing a legitimate new occupant would be loss.
        assert after["slots"][aging_address]["bucket_start"] != left_edge, (
            "the out-of-window contradicted payload remained logically readable after repair: "
            + json.dumps(aged_out, sort_keys=True, default=str)
        )
        assert (60, rebuildable_start) not in after["pending"], after["pending"]
        assert after["slots"][rebuildable_address]["bucket_start"] == rebuildable_start, (
            "a rebuildable owed cell was gapped instead of republished"
        )
        assert (
            after["slots"][rebuildable_address]["published_at"]
            > before["slots"][rebuildable_address]["published_at"]
        ), "the rebuildable owed cell was never republished"

        snapshot_metadata, snapshot_binary = _wait_snapshot_ready(browser, restarted_client, {
            "range_seconds": 3_600,
            "resolution": 60,
            "client_id": "ring-owed-pre-page",
        })
        pre_page_snapshot = json.loads(snapshot_binary)
        assert pre_page_snapshot["cost_report"]["total_tokens"] == 12, pre_page_snapshot

        _load_real_stats_page(request, browser, prepared.runtime, gate_auth_credentials)
        _show_gpu_util(browser)
        _set_range_from_slider(browser, 3_600)
        _set_resolution_from_select(browser, 60)
        rendered = _wait_pair(browser, 3_600, 60, 60)
        rendered_total = rendered["generationCostTokens"]
        assert rendered_total == 12, rendered
        assert not [
            span for span in rendered["noData"]
            if span.get("family") in {"agent_tokens", "cost"}
            and span.get("reason") == "coverage_gap"
        ], rendered
        assert rendered["bootErrors"] == [], rendered
        assert rendered["bootRejections"] == [], rendered

        print("RING_OWED_SPLIT_EVIDENCE=" + json.dumps({
            "left_edge": left_edge,
            "rebuildable_start": rebuildable_start,
            "second_capacity": second_capacity,
            "ring_publications_before_page": int(
                (restarted_status.get("ring_writer") or {}).get("publications") or 0
            ),
            "cost_tokens": rendered_total,
        }, sort_keys=True), flush=True)
    finally:
        _retire_browser_and_stop_server(browser, prepared.runtime, process)
        _retire_ring_fixture_daemons(gate_runtime_paths)
