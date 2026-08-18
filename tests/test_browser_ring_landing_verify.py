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
from tests.gate_harness import run_fixture_cleanup_phases
from tests.serving_process import pid_is_serving
from tests.serving_process import serving_process_table
from yolomux_lib import auth as auth_module
from yolomux_lib import common
from yolomux_lib import server_auth
from yolomux_lib.infra.worktree_writer import child_process_artifact_environment
from yolomux_lib.local_services.registry import inherited_python_path
from yolomux_lib.stats_current import client as stats_client
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.stats_current import storage


REPO_ROOT = Path(__file__).resolve().parents[1]
PAIRS = ((300, 10), (900, 10), (3_600, 60), (86_400, 300))


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


def _launch_server(port: int, session: str, log_path: Path, runtime_dir: Path) -> subprocess.Popen[bytes]:
    output = log_path.open("ab")
    environment = dict(os.environ)
    environment["YOLOMUX_START_LOAD_WAIT_SECONDS"] = "30"
    environment["YOLOMUX_RUNTIME_DIR"] = str(runtime_dir)
    environment["PYTHONUNBUFFERED"] = "1"
    environment = child_process_artifact_environment(REPO_ROOT, environ=environment)
    environment["PYTHONPATH"] = inherited_python_path(environment)
    process = subprocess.Popen(
        (
            sys.executable,
            str(REPO_ROOT / "yolomux.py"),
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


def _pid_alive(pid: int) -> bool:
    return pid > 0 and pid_is_serving(pid)


def _statsd_processes_for_database(database_path: Path) -> list[dict[str, object]]:
    matches = []
    for pid, process in serving_process_table().items():
        try:
            arguments = shlex.split(process.command)
        except ValueError:
            continue
        if "yolomux_lib.stats_current.service" not in arguments:
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
    assert stats_service.safe_socket_path(
        requested_socket,
        prefix="yolomux-statsd",
    ) == Path(str(process["socket"]))
    return stats_client.StatsCurrentClient(requested_socket, database_path), process


def _stop_fixture_statsd(
    browser_driver,
    pid: int,
    paths: GateRuntimePaths,
    requested_socket: Path,
) -> bool:
    if not _pid_alive(pid):
        return False
    cmdline = serving_process_table()[pid].command
    assert "yolomux_lib.stats_current.service" in cmdline, cmdline
    assert str(paths.state_dir) in cmdline, cmdline
    expected_socket = stats_service.safe_socket_path(requested_socket, prefix="yolomux-statsd")
    assert f"--socket {expected_socket}" in cmdline, cmdline
    os.kill(pid, signal.SIGTERM)
    try:
        WebDriverWait(browser_driver, 5, poll_frequency=0.05).until(lambda _driver: not _pid_alive(pid))
    except TimeoutException:
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            WebDriverWait(browser_driver, 5, poll_frequency=0.05).until(lambda _driver: not _pid_alive(pid))
    return True


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
        const costRows = [...document.querySelectorAll('.js-debug-panel [data-js-debug-cost-table="summary"] tr')]
          .map(row => [...row.querySelectorAll('th, td')].map(cell => String(cell.textContent || '').trim()))
          .filter(cells => cells.length >= 3 && cells[0] !== 'Usage')
          .map(cells => ({label: cells[0], tokens: Number(cells[1].replace(/[^0-9.-]/g, '')) || 0}));
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
          paintedGenerationKey: String(jsDebugCurrentStatsClientState.paintedGenerationKey || ''),
          graphGenerationKey: graph?.dataset?.jsDebugStatsGenerationKey || '',
          serverSequence: Number(jsDebugStatsServerSequence || 0),
          historyState: graph?.dataset?.jsDebugHistoryState || '',
          busy: graph?.getAttribute?.('aria-busy') || '',
          graphRenderedAt: Number(graph?.dataset?.jsDebugGraphRenderedAt || 0),
          graphRenderPending: graph?.dataset?.jsDebugGraphRefreshPending || '',
          renderPaths: renderNodes.length,
          renderedCharts: document.querySelectorAll('.js-debug-panel .js-debug-chart svg').length,
          zeroBars,
          gapRects,
          costRows,
          readiness: typeof jsDebugHistoryReadinessSnapshot === 'function' ? jsDebugHistoryReadinessSnapshot() : null,
          debugEvents: typeof jsDebugEventsForTest === 'function' ? jsDebugEventsForTest().slice(-12) : [],
          bootErrors: jsDebugFailureEvents('error'),
          bootRejections: jsDebugFailureEvents('rejection'),
        };
        """
    )


def _wait_pair(driver, range_seconds: int, resolution_seconds: int) -> dict[str, object]:
    def ready(_driver):
        state = _graph_state(driver)
        if state["range"] != range_seconds or state["resolution"] != resolution_seconds:
            return False
        if not state["paintedGenerationKey"] or state["graphGenerationKey"] != state["paintedGenerationKey"]:
            return False
        if state["historyState"] != "ready" or state["busy"] != "false":
            return False
        if state["renderPaths"] < 1 or state["renderedCharts"] < 1:
            return False
        return state

    try:
        return WebDriverWait(driver, 20, poll_frequency=0.05).until(ready)
    except TimeoutException as error:
        raise AssertionError({
            "expected_range": range_seconds,
            "expected_resolution": resolution_seconds,
            "graph": _graph_state(driver),
        }) from error


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


def _show_gpu_util_and_cost_summary(driver) -> None:
    menu = driver.find_element(By.CSS_SELECTOR, ".js-debug-panel [data-js-debug-chart-menu]")
    if menu.get_attribute("open") is None:
        menu.find_element(By.CSS_SELECTOR, "summary").click()
    for chart_key in ("gpuUtil", "costSummary"):
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


def _exercise_pairs(driver) -> tuple[list[dict[str, object]], dict[str, object]]:
    results = []
    _wait_pair(driver, 900, 10)
    for range_seconds, resolution_seconds in PAIRS:
        started = time.monotonic()
        _set_range_from_slider(driver, range_seconds)
        _set_resolution_from_select(driver, resolution_seconds)
        state = _wait_pair(driver, range_seconds, resolution_seconds)
        results.append({
            "range": range_seconds,
            "resolution": resolution_seconds,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "render_paths": state["renderPaths"],
            "charts": state["renderedCharts"],
            "buckets": state["bucketCount"],
            "cache_generation": state["cacheGeneration"],
            "source_generation": state["sourceGeneration"],
            "cost_tokens": next(
                row["tokens"]
                for row in state["costRows"]
                if row["label"] == "Total"
            ),
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
    return results, _wait_pair(driver, 300, 10)


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
    driver.execute_cdp_cmd("Network.clearBrowserCookies", {})
    harness.authenticate(credentials)
    harness.load(tabs=("files", "__debug__", str(runtime.tmux.sessions[0])))
    driver.find_element(By.CSS_SELECTOR, '.dockview-pane-tab[data-pane-tab="__debug__"]').click()
    WebDriverWait(driver, 12, poll_frequency=0.05).until(
        lambda _driver: driver.find_element(By.CSS_SELECTOR, '.js-debug-panel [data-js-debug-subtab="graph"]')
    ).click()
    return harness


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
    runtime = ExternalRuntime(port=port, tmux=gate_tmux)
    service_runtime_dir = Path(tempfile.mkdtemp(prefix="ring-runtime-", dir=gate_runtime_paths.root))
    database_path = storage.default_database_path(gate_runtime_paths.state_dir)
    runtime_environ = dict(os.environ)
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
    process = _launch_server(port, str(gate_tmux.sessions[0]), log_path, service_runtime_dir)
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
        _show_gpu_util_and_cost_summary(browser)
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
        sidecar_survived_server_stop = _pid_alive(latest_statsd_pid)
        if sidecar_survived_server_stop:
            _stop_fixture_statsd(browser, latest_statsd_pid, gate_runtime_paths, requested_socket)

        restart_started = time.monotonic()
        process = _launch_server(port, str(gate_tmux.sessions[0]), log_path, service_runtime_dir)
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
        assert latest_statsd_pid == restarted_statsd["pid"]
        browser_started = time.monotonic()
        _load_real_stats_page(request, browser, runtime, gate_auth_credentials)
        _show_gpu_util_and_cost_summary(browser)
        _set_range_from_slider(browser, 300)
        _set_resolution_from_select(browser, 10)
        restarted = _wait_pair(browser, 300, 10)
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
        restarted_total = next(
            row["tokens"]
            for row in restarted["costRows"]
            if row["label"] == "Total"
        )
        assert restarted_total == 12, restarted
        assert not [
            span
            for span in restarted["noData"]
            if span.get("family") in {"agent_tokens", "cost"}
            and span.get("reason") == "coverage_gap"
        ], restarted

        evidence = {
            "head": subprocess.run(
                ("git", "rev-parse", "--short=9", "HEAD"),
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip(),
            "port": port,
            "pairs": first_pairs,
            "zero_bars": first_zero_gap["zeroBars"],
            "gap_rects": first_zero_gap["gapRects"],
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
        if _pid_alive(latest_statsd_pid):
            _stop_fixture_statsd(browser, latest_statsd_pid, gate_runtime_paths, requested_socket)
