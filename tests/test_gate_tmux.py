"""Regression gate D: tmux actions stay scoped and destructive defaults fail closed."""
from __future__ import annotations

import math
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import pytest

from tests import gate_harness
from tests import latency_calibration
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.browser_helpers.browser_layout import WebDriverWait  # noqa: F401
from tests.browser_helpers.browser_layout import TimeoutException
from tests.browser_helpers.browser_layout import assert_live_runtime_boot_healthy
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import click_visible_selector
from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import start_isolated_browser_app
from tests.browser_helpers.browser_layout import stop_browser_fixture
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import load_gate_terminal_only_browser
from tests.tmux_runtime import clear_isolated_product_environment
from tests.tmux_runtime import patch_isolated_product_environment
from tests.tmux_runtime import run_isolated_tmux
from yomux_lib.server import TmuxWebtermHTTPServer
from yomux_lib.tmux import tmux_utils


pytestmark = pytest.mark.socket

KEYSTROKE_LATENCY_SAMPLE_COUNT = 120
# The user-visible keystroke ceiling. Fixed on every host: a host measurement may decide WHICH
# statistic is certifiable, never how slow the product may be. Asserted only in the exclusive
# certification phase (latency_calibration.LATENCY_CERTIFICATION_ENV), never in the parallel
# browser lane.
KEYSTROKE_LATENCY_CEILING_MS = 3.0
KEYSTROKE_LATENCY_CERTIFIED_QUANTILE = 0.99
KEYSTROKE_LATENCY_CERTIFIED_QUANTILE_KEY = f"p{round(KEYSTROKE_LATENCY_CERTIFIED_QUANTILE * 100)}_nearest_rank_ms"
KEYSTROKE_SLOWDOWN_CONTROL_RATE = 4
KEYSTROKE_SLOWDOWN_CONTROL_SAMPLE_COUNT = 12
KEYSTROKE_SLOWDOWN_CONTROL_PRESSURE_SECONDS = 45.0
TERMINAL_PRESSURE_DURATION_SECONDS = 12.0
TERMINAL_PRESSURE_CHUNK_BYTES = 4096
TERMINAL_PRESSURE_INTERVAL_SECONDS = 0.02
TERMINAL_PRESSURE_WARMUP_BYTES = 64 * 1024
TERMINAL_PRESSURE_SAMPLING_MIN_BYTES = 128 * 1024
TERMINAL_PRESSURE_WAIT_TIMEOUT_MS = 8000


@dataclass(frozen=True)
class TerminalOutputPressure:
    session: str
    duration_seconds: float


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_d1_repeated_enter_posts_rename_exactly_once(browser, gate_live_server):
    evidence = gate_harness.held_rename_roster_probe(browser, gate_live_server)
    assert evidence["held"]["calls"] == evidence["final"]["calls"] == 1, evidence


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_d2_successful_repeated_enter_rename_never_surfaces_error(browser, gate_live_server):
    evidence = gate_harness.held_rename_roster_probe(browser, gate_live_server)
    assert evidence["final"]["error"] == "", evidence
    assert evidence["target"] in evidence["final"]["sessions"], evidence


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.browser
def test_d3_submit_control_is_visibly_disabled_then_reenabled_on_both_outcomes(browser, gate_live_server, reject):
    evidence = gate_harness.held_rename_roster_probe(browser, gate_live_server, reject=reject)
    assert evidence["held"]["disabled"] and evidence["held"]["opacity"] < 1, evidence
    assert evidence["final"]["disabled"] is False, evidence
    assert bool(evidence["final"]["error"]) == reject, evidence


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_d4_rename_survives_released_stale_roster_without_reversal(browser, gate_live_server):
    evidence = gate_harness.held_rename_roster_probe(browser, gate_live_server, stale=True)
    assert evidence["target"] in evidence["final"]["sessions"], evidence
    assert evidence["session"] not in evidence["final"]["sessions"], evidence
    assert evidence["target"] in evidence["final"]["layout"], evidence
    assert evidence["target"] in evidence["final"]["tabText"], evidence
    assert evidence["target"] in evidence["final"]["active"], evidence
    assert evidence["session"] not in evidence["final"]["active"], evidence
    assert evidence["final"]["focused"] == evidence["target"], evidence
    assert evidence["session"] not in evidence["final"]["serverSessions"], evidence


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.browser
def test_d10_rename_optimistically_changes_tab_before_reply_and_has_bounded_reconciliation(browser, gate_live_server, reject):
    evidence = gate_harness.held_rename_roster_probe(browser, gate_live_server, reject=reject)
    assert evidence["target"] in evidence["held"]["tabText"], evidence
    expected = evidence["session"] if reject else evidence["target"]
    assert expected in evidence["final"]["tabText"], evidence
    assert expected in evidence["final"]["sessions"], evidence


def _tmux(socket_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", "-S", str(socket_path), *args], capture_output=True, text=True, check=False, timeout=5)


def _request(port: int, path: str) -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("POST", path)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


@pytest.mark.no_browser
# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_gate_d5_kill_session_only_affects_registered_tmux_socket(monkeypatch, tmp_path, make_tmux_webterm_app, no_control_socket, isolated_yoagent_conversation_state):
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket_root = Path(os.environ["YOLOMUX_TEST_ROOT"])
    socket_one = socket_root / "d5-one.sock"
    socket_two = socket_root / "d5-two.sock"
    session = f"yt-{uuid.uuid4().hex[:12]}"
    for socket_path in (socket_one, socket_two):
        created = _tmux(socket_path, "new-session", "-d", "-s", session, "exec /bin/bash --noprofile --norc")
        assert created.returncode == 0, created.stderr or created.stdout
    patch_isolated_product_environment(monkeypatch, {
        "YOMUX_TMUX_SOCKET": str(socket_one),
        "YOMUX_TEST_AUTH_BYPASS": "1",
    })
    app = make_tmux_webterm_app([session])
    server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _ = _request(server.server_address[1], f"/api/kill-session?{urlencode({'session': session})}")
        assert status == HTTPStatus.OK
        assert _tmux(socket_one, "has-session", "-t", f"{session}:").returncode != 0
        assert _tmux(socket_two, "has-session", "-t", f"{session}:").returncode == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        for socket_path in (socket_one, socket_two):
            _tmux(socket_path, "kill-server")


@pytest.mark.no_browser
def test_gate_d6_destructive_default_server_policy_is_explicit(monkeypatch):
    clear_isolated_product_environment(monkeypatch, "TMUX_SOCKET", "TMUX_ALLOW_DEFAULT_SERVER")
    with pytest.raises(tmux_utils.TmuxSocketTargetError):
        tmux_utils.tmux_command(["kill-session", "-t", tmux_utils.tmux_session_target("yt-gate")])
    with pytest.raises(tmux_utils.TmuxSocketTargetError):
        tmux_utils.tmux_command(["kill-server"])
    patch_isolated_product_environment(monkeypatch, {"YOMUX_TMUX_ALLOW_DEFAULT_SERVER": "1"})
    exact = tmux_utils.tmux_session_target("yt-gate")
    assert exact == "=yt-gate:"
    assert tmux_utils.tmux_command(["kill-session", "-t", exact]) == ["tmux", "kill-session", "-t", exact]
    # The opt-in buys a session kill on the shared default server, never a prefix-resolvable one.
    with pytest.raises(tmux_utils.TmuxSocketTargetError):
        tmux_utils.tmux_command(["kill-session", "-t", "yt-gate:"])
    with pytest.raises(tmux_utils.TmuxSocketTargetError):
        tmux_utils.tmux_command(["kill-server"])


@pytest.mark.gate_serial
@pytest.mark.no_browser
# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_gate_d7_kill_session_api_returns_promptly_and_removes_scoped_session(monkeypatch, tmp_path, make_tmux_webterm_app, no_control_socket, isolated_yoagent_conversation_state):
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket_path = Path(os.environ["YOLOMUX_TEST_ROOT"]) / "d7.sock"
    session = f"yt-{uuid.uuid4().hex[:12]}"
    created = _tmux(socket_path, "new-session", "-d", "-s", session, "exec /bin/bash --noprofile --norc")
    assert created.returncode == 0, created.stderr or created.stdout
    patch_isolated_product_environment(monkeypatch, {
        "YOMUX_TMUX_SOCKET": str(socket_path),
        "YOMUX_TEST_AUTH_BYPASS": "1",
    })
    app = make_tmux_webterm_app([session])
    server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started_at = time.monotonic()
        status, _ = _request(server.server_address[1], f"/api/kill-session?{urlencode({'session': session})}")
        assert time.monotonic() - started_at < 0.5
        assert status == HTTPStatus.OK
        assert _tmux(socket_path, "has-session", "-t", f"{session}:").returncode != 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        _tmux(socket_path, "kill-server")


def _start_terminal_output_pressure(runtime, session: str, *, duration_seconds: float = TERMINAL_PRESSURE_DURATION_SECONDS) -> None:
    code = (
        "import sys,time;"
        f"payload=('latency-pressure-'*{TERMINAL_PRESSURE_CHUNK_BYTES})[:{TERMINAL_PRESSURE_CHUNK_BYTES}];"
        f"deadline=time.monotonic()+{float(duration_seconds)!r};"
        f"interval={TERMINAL_PRESSURE_INTERVAL_SECONDS!r};"
        "\nwhile time.monotonic()<deadline:"
        "\n sys.stdout.write(payload+'\\n');sys.stdout.flush();time.sleep(interval)"
    )
    command = shlex.join((sys.executable, "-u", "-c", code))
    # An earlier round leaves its typed characters on the shell's input line. Without this the
    # pressure command is appended to that prefix, never runs, and the warmup wait times out 8 s
    # later with no explanation.
    interrupted = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{session}:", "C-c", timeout=5)
    assert interrupted.returncode == 0, interrupted.stderr or interrupted.stdout
    result = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{session}:", command, "Enter", timeout=5)
    assert result.returncode == 0, result.stderr or result.stdout


def _establish_terminal_output_pressure(
    browser,
    runtime,
    session: str,
    *,
    duration_seconds: float = TERMINAL_PRESSURE_DURATION_SECONDS,
) -> TerminalOutputPressure:
    """Prove the pressure producer reaches xterm before a measured or throttled phase starts."""

    browser.execute_script("clearClientPerfCounters();")
    _start_terminal_output_pressure(runtime, session, duration_seconds=duration_seconds)
    observed_bytes = _wait_for_xterm_write_bytes(
        browser,
        TERMINAL_PRESSURE_WARMUP_BYTES,
        "terminal output pressure reaches xterm",
    )
    assert observed_bytes >= TERMINAL_PRESSURE_WARMUP_BYTES, observed_bytes
    return TerminalOutputPressure(session=session, duration_seconds=duration_seconds)


def _wait_for_xterm_write_bytes(browser, minimum_bytes: int, description: str) -> int:
    """Wait for one durable xterm byte floor instead of sampling an in-flight final counter."""

    observed = browser.execute_async_script(
        """
        const minimumBytes = arguments[0];
        const description = arguments[1];
        const done = arguments[arguments.length - 1];
        window.__yomuxTestWaitFor(() => {
          const counter = Object.fromEntries(clientPerfSummary().map(item => [item.name, item])).xtermWrite;
          return Number(counter?.bytes || 0) >= minimumBytes;
        }, {timeoutMs: arguments[2], description}).then(() => {
          const counter = Object.fromEntries(clientPerfSummary().map(item => [item.name, item])).xtermWrite;
          done(Number(counter?.bytes || 0));
        }, error => done({error: String(error)}));
        """,
        minimum_bytes,
        description,
        TERMINAL_PRESSURE_WAIT_TIMEOUT_MS,
    )
    assert isinstance(observed, (int, float)), observed
    return int(observed)


def _client_perf_counter(driver, name: str) -> dict[str, object]:
    return driver.execute_script(
        "return Object.fromEntries(clientPerfSummary().map(counter => [counter.name, counter]))[arguments[0]] || null;",
        name,
    )


def _client_perf_trace(driver) -> dict[str, object]:
    return driver.execute_script(
        """
        const counters = Object.fromEntries(clientPerfSummary().map(counter => [counter.name, counter]));
        return {
          keydownToTermData: counters.keydownToTermData || null,
          focusSet: counters.focusSet || null,
          termOnData: counters['term.onData'] || null,
          wsSend: counters.wsSend || null,
          echoToTermWrite: counters.echoToTermWrite || null,
          xtermWrite: counters.xtermWrite || null,
          longTasks: clientPerfLongTaskSummary(),
        };
        """
    )


def _send_native_key(driver, character: str) -> None:
    key_code = ord(character.upper())
    common = {
        "key": character,
        "code": f"Key{character.upper()}",
        "windowsVirtualKeyCode": key_code,
        "nativeVirtualKeyCode": key_code,
    }
    driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyDown", "text": character, "unmodifiedText": character, **common})
    driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", **common})


def _start_marker_echo_process(runtime, session: str, *, pressure: bool = False) -> None:
    """Replace an isolated shell with a raw-tty process that visibly acknowledges every `a`."""

    code = (
        "import os,sys,termios,tty,threading,time\n"
        "fd=sys.stdin.fileno()\n"
        "tty.setraw(fd)\n"
        "os.write(sys.stdout.fileno(), b'READY\\r\\n')\n"
        f"if {pressure!r}:\n"
        " def output():\n"
        "  while True:\n"
        "   os.write(sys.stdout.fileno(), b'background-output\\r\\n'); time.sleep(0.02)\n"
        " threading.Thread(target=output,daemon=True).start()\n"
        "count=0\n"
        "while True:\n"
        " data=os.read(fd,1)\n"
        " if not data: break\n"
        " if data==b'a':\n"
        "  count+=1\n"
        "  os.write(sys.stdout.fileno(), f'<E:{count:04d}>\\r\\n'.encode())\n"
    )
    result = run_isolated_tmux(
        runtime.tmux,
        "send-keys",
        "-t",
        f"{session}:",
        shlex.join((sys.executable, "-u", "-c", code)),
        "Enter",
    )
    assert result.returncode == 0, result.stderr or result.stdout


def _install_visible_echo_probe(browser, session: str) -> None:
    browser.execute_script(
        """
        const session = arguments[0];
        const item = terminals.get(session);
        if (!item?.term || !item?.container) throw new Error(`real xterm missing for ${session}`);
        const term = item.term;
        const probe = window.__tabSwitchEchoProbe = {session, samples: []};
        const onKeydown = event => {
          if (event.key !== 'a' || !item.container.contains(event.target)) return;
          const index = probe.samples.length + 1;
          probe.samples.push({index, marker: `<E:${String(index).padStart(4, '0')}>`, eventAt: event.timeStamp,
            trusted: event.isTrusted, treePending: window.__tabActivationTreeDone === false,
            treeRowsAtEvent: document.querySelectorAll('.file-explorer-panel[data-panel-item="__differ__"] .changes-repo-group[data-changes-repo="/repo/app"] .file-tree-row').length});
        };
        document.addEventListener('keydown', onKeydown, true);
        const renderSubscription = term.onRender(() => {
          const buffer = term.buffer.active;
          const firstRow = Math.max(0, buffer.viewportY);
          const lastRow = Math.min(buffer.length, firstRow + term.rows);
          const visibleText = Array.from({length: lastRow - firstRow}, (_, offset) =>
            buffer.getLine(firstRow + offset)?.translateToString(true) || '').join('\\n');
          for (const sample of probe.samples) {
            if (sample.paintedAt || !visibleText.includes(sample.marker)) continue;
            sample.renderAt = performance.now();
            // xterm invokes onRender after it draws. One following frame crosses the browser paint.
            requestAnimationFrame(() => { sample.paintedAt = performance.now(); });
          }
        });
        probe.dispose = () => {
          document.removeEventListener('keydown', onKeydown, true);
          renderSubscription.dispose();
        };
        const originalWrite = term.write.bind(term);
        window.__tabActivationOriginalWrite = originalWrite;
        const barrier = window.__tabActivationWriteBarrier = {session, hold: true, callbacks: []};
        term.write = function(data, callback) {
          if (barrier.hold && data === '' && typeof callback === 'function') {
            barrier.callbacks.push(callback);
            return;
          }
          return originalWrite(data, callback);
        };
        """,
        session,
    )


def _install_tab_transition_probe(browser) -> None:
    browser.execute_script(
        """
        window.__tabTransitionSamples = [];
        window.__tabTransitionLongTasks = [];
        window.__tabActivationStaticReplacements = [];
        window.__tabActivationOriginalReplaceChangesStaticHtml = replaceChangesStaticHtml;
        replaceChangesStaticHtml = (...args) => {
          const root = args[0];
          const groups = root.querySelector('.changes-groups');
          const rows = groups?.querySelectorAll('.file-tree-row').length || 0;
          const startTime = performance.now();
          const result = window.__tabActivationOriginalReplaceChangesStaticHtml(...args);
          window.__tabActivationStaticReplacements.push({startTime, duration: performance.now() - startTime, rows,
            retainedMountedGroups: Boolean(groups && groups === root.querySelector('.changes-groups') && groups.parentElement === root)});
          return result;
        };
        const longTaskObserver = new PerformanceObserver(list => {
          for (const entry of list.getEntries()) {
            window.__tabTransitionLongTasks.push({startTime: entry.startTime, duration: entry.duration});
          }
        });
        longTaskObserver.observe({entryTypes: ['longtask']});
        window.__tabTransitionLongTaskObserver = longTaskObserver;
        window.__tabPointerDownTargets = new Map();
        const activationCounts = new Map();
        const originalActivation = commitUserTerminalTabActivation;
        commitUserTerminalTabActivation = (session, ...args) => {
          activationCounts.set(session, (activationCounts.get(session) || 0) + 1);
          return originalActivation(session, ...args);
        };
        const onPointerdown = event => {
          const tab = event.target.closest?.('.dockview-pane-tab[data-pane-tab]');
          if (!tab) return;
          const session = tab.dataset.paneTab;
          window.__tabPointerDownTargets.set(session, {
            previous: activeItemForSide(slotForItem(session)), activationCount: activationCounts.get(session) || 0,
          });
        };
        const onClick = event => {
          const tab = event.target.closest?.('.dockview-pane-tab[data-pane-tab]');
          if (!tab) return;
          const session = tab.dataset.paneTab;
          const side = slotForItem(session);
          const pointerdown = window.__tabPointerDownTargets.get(session);
          const previous = pointerdown?.previous || activeItemForSide(side);
          window.__tabPointerDownTargets.delete(session);
          const sample = {session, previous, eventAt: event.timeStamp, clickAt: performance.now()};
          window.__tabTransitionSamples.push(sample);
          requestAnimationFrame(() => {
            const item = terminals.get(session);
            const pane = document.getElementById(`terminal-pane-${session}`);
            const previousTab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(previous)}"]`);
            sample.frameAt = performance.now();
            sample.activationCount = (activationCounts.get(session) || 0) - (pointerdown?.activationCount || 0);
            sample.activeAtFrame = activeItemForSide(side);
            sample.targetTabActive = tab.classList.contains(CLS.active);
            sample.previousTabInactive = !previousTab?.classList.contains(CLS.active);
            sample.terminalVisible = Boolean(item?.container && item.container.getBoundingClientRect().width > 0
              && item.container.getBoundingClientRect().height > 0 && pane?.classList.contains(CLS.active));
            sample.focused = document.activeElement === item?.term?.textarea;
            sample.typingReady = item?.container?.classList.contains('typing-ready') === true;
            sample.pending = tab.classList.contains('tab-activation-pending');
            requestAnimationFrame(() => {
              sample.firstPaintAt = performance.now();
              sample.longTasks = window.__tabTransitionLongTasks
                .filter(entry => entry.startTime + entry.duration > sample.eventAt && entry.startTime < sample.firstPaintAt)
                .map(entry => ({startTime: entry.startTime, duration: entry.duration}));
            });
          });
        };
        document.addEventListener('pointerdown', onPointerdown, true);
        document.addEventListener('click', onClick, true);
        longTaskObserver.dispose = () => {
          longTaskObserver.disconnect();
          commitUserTerminalTabActivation = originalActivation;
          document.removeEventListener('pointerdown', onPointerdown, true);
          document.removeEventListener('click', onClick, true);
        };
        """
    )


def _wait_tab_transition_frame(browser, session: str) -> dict[str, object]:
    sample = WebDriverWait(browser, 4, poll_frequency=0.01).until(
        lambda driver: driver.execute_script(
            """
            const latest = [...(window.__tabTransitionSamples || [])].reverse().find(sample =>
              sample.session === arguments[0]);
            return latest?.firstPaintAt > 0 ? latest : false;
            """,
            session,
        )
    )
    assert sample["activeAtFrame"] == session and sample["targetTabActive"] and sample["previousTabInactive"], sample
    assert sample["terminalVisible"] and sample["focused"] and sample["typingReady"], sample
    assert sample["activationCount"] == 1, sample
    sample["clickToFrameMs"] = round(float(sample["frameAt"]) - float(sample["eventAt"]), 2)
    sample["clickToPaintMs"] = round(float(sample["firstPaintAt"]) - float(sample["eventAt"]), 2)
    sample["clickDispatchDelayMs"] = round(float(sample["clickAt"]) - float(sample["eventAt"]), 2)
    return sample


def _send_key_until_visible_echo(browser, *, sample_index: int, timeout: float = 3.0) -> float:
    _send_native_key(browser, "a")
    sample = WebDriverWait(browser, timeout, poll_frequency=0.01).until(
        lambda driver: driver.execute_script(
            """
            const sample = window.__tabSwitchEchoProbe?.samples[arguments[0] - 1];
            return sample?.paintedAt > 0 && sample.trusted ? sample : false;
            """,
            sample_index,
        )
    )
    assert sample["index"] == sample_index and sample["marker"] == f"<E:{sample_index:04d}>", sample
    return round(float(sample["paintedAt"]) - float(sample["eventAt"]), 2)


def _echo_timing_summary(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(_nearest_rank_ms(samples, 0.95), 2),
        "p99_ms": round(_nearest_rank_ms(samples, 0.99), 2),
        "max_ms": round(max(samples), 2),
    }


def _echo_timing_verdicts(samples: list[float], *, label: str = "native key to visible echo") -> list[dict[str, object]]:
    summary = _echo_timing_summary(samples)
    return [
        latency_calibration.fixed_ceiling_verdict(
            label=f"{label} {statistic}",
            raw_measured_ms=summary[f"{statistic}_ms"],
            ceiling_ms=ceiling_ms,
            statistic=statistic,
        )
        for statistic, ceiling_ms in (("p95", 50.0), ("p99", 100.0))
    ]


@pytest.mark.no_browser
def test_s1_native_echo_timing_negative_control_rejects_a_held_paint_delay():
    samples = [20.0] * 19 + [120.0]
    verdicts = _echo_timing_verdicts(samples)
    assert [verdict["passed"] for verdict in verdicts] == [True, False], verdicts
    assert verdicts[0]["ceiling_ms"] == 50.0 and verdicts[1]["ceiling_ms"] == 100.0, verdicts


def _release_held_activation_write(browser, session: str, callback_index: int = -1, *, release_hold: bool = True) -> None:
    browser.execute_script(
        """
        const barrier = window.__tabActivationWriteBarrier;
        if (barrier?.session !== arguments[0]) throw new Error('activation write barrier owner changed');
        if (arguments[2]) barrier.hold = false;
        const callback = barrier.callbacks.splice(arguments[1], 1)[0];
        if (callback) callback();
        """,
        session,
        callback_index,
        release_hold,
    )


def _assert_activation_pending_and_typing_ready(browser, session: str, *, timeout: float = 8.0) -> dict[str, object]:
    state = WebDriverWait(browser, timeout, poll_frequency=0.01).until(
        lambda driver: driver.execute_script(
            """
            const session = arguments[0];
            const item = terminals.get(session);
            const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(session)}"]`);
            const transition = [...(window.__tabTransitionSamples || [])].reverse().find(sample => sample.session === session);
            const inactiveTab = transition?.previous
              ? document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(transition.previous)}"]`) : null;
            const pane = document.getElementById(`terminal-pane-${session}`);
            const terminal = item?.container;
            if (!item || item.socket?.readyState !== WebSocket.OPEN || activeItemForSide(slotForItem(session)) !== session
              || !tab?.classList.contains('tab-activation-pending') || !pane?.classList.contains(CLS.active)
              || !inactiveTab || !terminal?.classList.contains('typing-ready')) return false;
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 1;
            const context = canvas.getContext('2d', {willReadFrequently: true});
            const colorRgb = color => {
              context.clearRect(0, 0, 1, 1);
              context.fillStyle = color;
              context.fillRect(0, 0, 1, 1);
              return [...context.getImageData(0, 0, 1, 1).data].slice(0, 3);
            };
            const originalLight = document.body.classList.contains('theme-light');
            const originalTransition = tab.style.transition;
            const themes = [];
            tab.style.setProperty('--pane-bar-bg', 'rgb(19, 39, 59)');
            tab.style.transition = 'none';
            for (const light of [false, true]) {
              document.body.classList.toggle('theme-light', light);
              const pendingRgb = colorRgb(getComputedStyle(tab).backgroundColor);
              const tabStyle = getComputedStyle(tab);
              const inactiveRgb = colorRgb(tabStyle.getPropertyValue('--pane-inactive-tab-bg'));
              tab.classList.remove('tab-activation-pending');
              const activeStyle = getComputedStyle(tab);
              const activeRgb = colorRgb(activeStyle.getPropertyValue('--pane-tab-active-bg'));
              const activeTextRgb = colorRgb(activeStyle.color);
              tab.classList.add('tab-activation-pending');
              const expectedRgb = inactiveRgb.map((channel, index) => Math.round((channel + activeRgb[index]) / 2));
              const pendingTextRgb = colorRgb(getComputedStyle(tab).color);
              themes.push({light, pendingRgb, inactiveRgb, activeRgb, expectedRgb, pendingTextRgb, activeTextRgb,
                midpoint: pendingRgb.every((channel, index) => Math.abs(channel - expectedRgb[index]) <= 1),
                activeText: pendingTextRgb.every((channel, index) => Math.abs(channel - activeTextRgb[index]) <= 1)});
            }
            tab.style.removeProperty('--pane-bar-bg');
            tab.style.transition = originalTransition;
            document.body.classList.toggle('theme-light', originalLight);
            return {
              active: activeItemForSide(slotForItem(session)),
              focused: document.activeElement === item.term.textarea,
              typingReady: terminal.classList.contains('typing-ready'),
              pendingThemes: themes,
            };
            """,
            session,
        )
    )
    assert state["active"] == session and state["focused"] and state["typingReady"], state
    assert all(theme["midpoint"] and theme["activeText"] for theme in state["pendingThemes"]), state
    return state


def _counter_total(counter: dict[str, object] | None, field: str) -> int:
    return int(counter[field]) if counter else 0


def _nearest_rank_ms(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def _focus_gate_terminal(browser, runtime) -> str:
    """Load the fixture server and leave the keyboard focus on the first session's xterm textarea."""

    return load_gate_terminal_only_browser(browser, runtime)


def _keystroke_round(
    browser,
    runtime,
    session: str,
    *,
    label: str,
    sample_count: int,
    pressure: bool,
    pressure_seconds: float = TERMINAL_PRESSURE_DURATION_SECONDS,
    established_pressure: TerminalOutputPressure | None = None,
    after_key: Callable[[int], None] | None = None,
) -> dict[str, object]:
    """Type ``sample_count`` native keys once and return every delivery and wall-latency observation.

    One owner for every keystroke measurement in this module: the release-blocking delivery test, the
    exclusive-phase wall-latency certification, and the negative controls all read the same fields, so
    a contract cannot hold in one place and silently vanish in another.
    """

    if pressure:
        if established_pressure is None:
            established_pressure = _establish_terminal_output_pressure(
                browser,
                runtime,
                session,
                duration_seconds=pressure_seconds,
            )
        assert established_pressure.session == session, established_pressure
    else:
        assert established_pressure is None, established_pressure
    browser.execute_script("clearClientPerfCounters();")
    xterm_bytes_before = _counter_total(_client_perf_counter(browser, "xtermWrite"), "bytes")
    counts: list[int] = []
    samples: list[float] = []
    started_at = time.monotonic()
    for index in range(1, sample_count + 1):
        _send_native_key(browser, "a")
        counter = _client_perf_counter(browser, "keydownToTermData")
        counts.append(_counter_total(counter, "count"))
        samples.append(float(counter["lastMs"]) if counter else math.inf)
        if after_key is not None:
            after_key(index)
    sampling_seconds = time.monotonic() - started_at
    if pressure:
        # The producer and xterm are asynchronous. Keep the same 128 KiB pressure floor, but let
        # writes already emitted during the round converge instead of judging one in-flight read.
        xterm_bytes_after = _wait_for_xterm_write_bytes(
            browser,
            xterm_bytes_before + TERMINAL_PRESSURE_SAMPLING_MIN_BYTES,
            "terminal output pressure remains observable through the keystroke round",
        )
    else:
        xterm_bytes_after = _counter_total(_client_perf_counter(browser, "xtermWrite"), "bytes")
    trace = _client_perf_trace(browser)
    return {
        "label": label,
        "sample_count": sample_count,
        "pressure": pressure,
        "counts": counts,
        "samples": samples,
        "sampling_seconds": round(sampling_seconds, 3),
        "focus_set": trace["focusSet"],
        "ws_send_count": _counter_total(trace["wsSend"], "count"),
        "ws_send_bytes": _counter_total(trace["wsSend"], "bytes"),
        "term_on_data_count": _counter_total(trace["termOnData"], "count"),
        "term_on_data_bytes": _counter_total(trace["termOnData"], "bytes"),
        "echo_count": _counter_total(trace["echoToTermWrite"], "count"),
        "pressure_bytes_observed": xterm_bytes_after - xterm_bytes_before,
        "long_tasks": trace["longTasks"],
    }


def _keystroke_delivery_failures(observed: dict[str, object]) -> list[str]:
    """Name every violated keystroke-delivery contract, or return an empty list.

    Wall latency is deliberately absent: it is a user-visible interaction budget certified in the
    exclusive phase, not a delivery fact, and fusing the two made an oversubscribed renderer able to
    red a run in which all 120 keys arrived (measured: max 11.1 ms with 120/120 delivered).
    """

    expected = int(observed["sample_count"])
    failures: list[str] = []
    if observed["counts"] != list(range(1, expected + 1)):
        failures.append(f"keystroke-delivery: keydownToTermData must advance exactly once per native key; observed {observed['counts']}")
    if observed["focus_set"] is not None:
        failures.append(f"focus-invariant: nothing may re-set terminal focus while typing; focusSet={observed['focus_set']}")
    if observed["ws_send_count"] != expected or observed["ws_send_bytes"] != expected:
        failures.append(
            f"websocket-send: every key must reach the socket as one 1-byte input frame; expected count={expected} bytes={expected}, "
            f"observed count={observed['ws_send_count']} bytes={observed['ws_send_bytes']}"
        )
    if observed["term_on_data_count"] != expected or observed["term_on_data_bytes"] != expected:
        failures.append(
            f"term-on-data: xterm must emit exactly one 1-byte data event per key; expected count={expected} bytes={expected}, "
            f"observed count={observed['term_on_data_count']} bytes={observed['term_on_data_bytes']}"
        )
    if observed["echo_count"] < 1:
        failures.append(f"socket-echo: the session must answer at least one send while typing; echoToTermWrite count={observed['echo_count']}")
    if observed["pressure"] and int(observed["pressure_bytes_observed"]) < TERMINAL_PRESSURE_SAMPLING_MIN_BYTES:
        failures.append(
            f"output-pressure: xterm must keep consuming the streaming session during the round; "
            f"expected at least {TERMINAL_PRESSURE_SAMPLING_MIN_BYTES} bytes, observed {observed['pressure_bytes_observed']}"
        )
    return failures


def _keystroke_summary(observed: dict[str, object]) -> dict[str, object]:
    """Compact every observation except the raw per-key lists, for prints and assertion messages."""

    samples = list(observed["samples"])
    return {
        "label": observed["label"],
        "pressure": observed["pressure"],
        "sample_count": observed["sample_count"],
        "delivered_count": observed["counts"][-1] if observed["counts"] else 0,
        "sampling_seconds": observed["sampling_seconds"],
        "median_ms": statistics.median(samples),
        "p95_nearest_rank_ms": _nearest_rank_ms(samples, 0.95),
        KEYSTROKE_LATENCY_CERTIFIED_QUANTILE_KEY: _nearest_rank_ms(samples, KEYSTROKE_LATENCY_CERTIFIED_QUANTILE),
        "max_ms": max(samples),
        "focus_set": observed["focus_set"],
        "ws_send_count": observed["ws_send_count"],
        "ws_send_bytes": observed["ws_send_bytes"],
        "term_on_data_count": observed["term_on_data_count"],
        "term_on_data_bytes": observed["term_on_data_bytes"],
        "echo_count": observed["echo_count"],
        "pressure_bytes_observed": observed["pressure_bytes_observed"],
        "long_task_count": observed["long_tasks"]["count"],
        "long_task_max_ms": observed["long_tasks"]["maxMs"],
    }


def _assert_keystroke_delivery_complete(observed: dict[str, object]) -> None:
    failures = _keystroke_delivery_failures(observed)
    assert failures == [], {"failures": failures, "summary": _keystroke_summary(observed), "samples": observed["samples"]}


def _run_tab_activation_native_echo_journey(browser, monkeypatch, tmp_path) -> dict[str, object]:
    """Exercise pending activation and visible echo while the shared Differ renderer is busy."""
    runtime = start_isolated_browser_app(monkeypatch, tmp_path, session_count=2)
    server, thread = start_browser_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    first, second = runtime.sessions
    _start_marker_echo_process(runtime, first)
    _start_marker_echo_process(runtime, second)
    try:
        query = urlencode({
            "sessions": ",".join(runtime.sessions),
            "layout": "left",
            "tabs": f"left:{first},{second}",
        })
        browser.get(f"http://127.0.0.1:{server.server_address[1]}/?{query}")
        WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                "return typeof jsDebugFailureEvents === 'function' && typeof boot === 'function' && Boolean(document.getElementById('grid'));"
            )
        )
        assert_live_runtime_boot_healthy(browser, "tab-activation-native-echo", timeout=12)
        WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                """
                const item = terminals.get(arguments[0]);
                return item?.socket?.readyState === WebSocket.OPEN && item.terminalOutputSeen === true
                  && Boolean(item.term?.onRender) && Boolean(item.container?.querySelector('.xterm-screen'));
                """,
                first,
            )
        )
        browser.execute_script(
            """
            const [first, second] = arguments;
            const slots = emptyLayoutSlots();
            slots[layoutTreeKey] = splitNode('row', leafNode('left'), leafNode('right'), 0.68);
            slots.left = paneStateWithTabs([first, second], first);
            slots.right = paneStateWithTabs([differItemId], differItemId);
            applyLayoutSlots(slots, {prune: false});
            """,
            first,
            second,
        )
        WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                "return Boolean(panelNodes.get(differItemId)?.isConnected) && fileExplorerSessionFilesPaneIsVisible();"
            )
        )
        # Let the real tab path lazily create and warm the second xterm before measuring cached-tab
        # activation. This preserves the production mount behavior and avoids depending on a socket
        # that cannot exist until the tab has been selected once.
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{second}"]')
        WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                """
                const item = terminals.get(arguments[0]);
                return item?.socket?.readyState === WebSocket.OPEN && item.terminalOutputSeen === true
                  && Boolean(item.term?.onRender) && Boolean(item.container?.querySelector('.xterm-screen'));
                """,
                second,
            )
        )
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{first}"]')
        legacy_pending = browser.execute_script(
            """
            const panel = document.createElement('div');
            panel.className = 'panel active-pane';
            const tab = document.createElement('button');
            tab.className = 'pane-tab active tab-activation-pending';
            panel.append(tab);
            document.body.append(panel);
            const pending = getComputedStyle(tab).backgroundColor;
            tab.classList.remove('tab-activation-pending');
            const active = getComputedStyle(tab).backgroundColor;
            panel.remove();
            return {pending, active, differs: pending !== active};
            """
        )
        assert legacy_pending["differs"], legacy_pending
        _install_visible_echo_probe(browser, second)
        _install_tab_transition_probe(browser)
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{second}"]')
        quiet_transition = _wait_tab_transition_frame(browser, second)
        _assert_activation_pending_and_typing_ready(browser, second)
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{first}"]')
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{second}"]')
        quiet_ready = _assert_activation_pending_and_typing_ready(browser, second)
        _release_held_activation_write(browser, second, 0, release_hold=False)
        quiet_ready_after_stale = _assert_activation_pending_and_typing_ready(browser, second)
        quiet_start_time = browser.execute_script("return performance.now();")
        quiet_samples = [_send_key_until_visible_echo(browser, sample_index=index) for index in range(1, 21)]
        quiet_timeline = browser.execute_script(
            """
            const startTime = arguments[0], endTime = performance.now();
            return {startTime, endTime,
              longTasks: window.__tabTransitionLongTasks.filter(task => task.startTime + task.duration > startTime && task.startTime < endTime),
              samples: window.__tabSwitchEchoProbe.samples.map(sample => ({
                index: sample.index, eventAt: sample.eventAt, renderAt: sample.renderAt, paintedAt: sample.paintedAt}))};
            """, quiet_start_time,
        )
        replacement = browser.execute_script(
            """
            const original = terminals.get(arguments[0]);
            const replacement = {...original};
            window.__tabActivationOriginalItem = original;
            window.__tabActivationReplacementItem = replacement;
            terminals.set(arguments[0], replacement);
            dockviewFinishTabActivationPerf(arguments[0]);
            return {replaced: terminals.get(arguments[0]) === replacement,
              callbackCount: window.__tabActivationWriteBarrier.callbacks.length};
            """,
            second,
        )
        assert replacement["replaced"] and replacement["callbackCount"] == 2, replacement
        _release_held_activation_write(browser, second, 0, release_hold=False)
        _assert_activation_pending_and_typing_ready(browser, second)
        _release_held_activation_write(browser, second, 0)
        WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                "return !document.querySelector(`.dockview-pane-tab[data-pane-tab=\"${CSS.escape(arguments[0])}\"]`)?.classList.contains('tab-activation-pending');",
                second,
            )
        )
        browser.execute_script(
            "terminals.set(arguments[0], window.__tabActivationOriginalItem); delete window.__tabActivationOriginalItem; delete window.__tabActivationReplacementItem;",
            second,
        )

        differ_setup = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            (async () => {
              const files = Array.from({length: 5000}, (_, index) => ({
                session: arguments[1], repo: '/repo/app', path: `file-${index}.txt`,
                abs_path: `/repo/app/file-${index}.txt`, status: 'M', mtime: index + 1,
              }));
              const payload = {session: arguments[1], loaded: true, refreshing: false, errors: [], warnings: [],
                repos: [{repo: '/repo/app', count: files.length, added: 0, removed: 0}], files};
              const slots = emptyLayoutSlots();
              slots[layoutTreeKey] = splitNode('row', leafNode('left'), leafNode('right'), 0.68);
              slots.left = paneStateWithTabs([arguments[2], arguments[3]], arguments[3]);
              slots.right = paneStateWithTabs([differItemId], differItemId);
              applyLayoutSlots(slots, {prune: false});
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              window.__tabActivationLargePayload = payload;
              // Keep the synthetic cache authoritative throughout both measured render phases.
              window.__tabActivationOriginalFetch = window.fetch;
              window.fetch = (input, options = {}) => {
                const url = new URL(typeof input === 'string' ? input : input.url, location.href);
                if (!url.pathname.startsWith('/api/session-files')) return window.__tabActivationOriginalFetch(input, options);
                if (options.signal?.aborted) return Promise.reject(options.signal.reason);
                const session = url.searchParams.get('session');
                const payload = sessionFilesCacheForDestination('differ').get(sessionFilesCacheKey(session, 'differ'))?.payload;
                if (!payload) return window.__tabActivationOriginalFetch(input, options);
                if (url.pathname === '/api/session-files-stream') {
                  const {files, ...summary} = payload;
                  const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
                  const body = frame('repo', {...summary, repo: '/repo/app', files}) + frame('done', summary);
                  return Promise.resolve(new Response(body, {status: 200, headers: {'Content-Type': 'text/event-stream'}}));
                }
                return Promise.resolve(new Response(JSON.stringify(payload), {status: 200, headers: {'Content-Type': 'application/json'}}));
              };
              for (const session of [arguments[2], arguments[3]]) {
                for (const destination of ['finder', 'differ']) {
                  const request = sessionFilesRequestForDestination(destination, session);
                  const payload = normalizedSessionFilesPayload({...window.__tabActivationLargePayload, session,
                    files: window.__tabActivationLargePayload.files.map(file => ({...file, session}))}, request);
                  const signature = sessionFilesPayloadSignatureForPayload(payload);
                  sessionFilesCacheForDestination(destination).set(sessionFilesCacheKey(session, destination), {payload, signature});
                }
              }
              setSessionFilesPayloadForDestination('differ', payload);
              fileExplorerSessionFilesState.signature = sessionFilesPayloadSignatureForPayload(payload);
              window.__tabActivationTreePromises = [];
              window.__tabActivationTreeDone = false;
              const original = renderChangesGroups;
              window.__tabActivationOriginalRenderChangesGroups = original;
              renderChangesGroups = (...args) => {
                const result = original(...args);
                if (result?.then) {
                  window.__tabActivationTreePromises.push(result);
                  window.__tabActivationTreeDone = false;
                  result.finally(() => {
                    if (window.__tabActivationTreePromises.at(-1) === result) window.__tabActivationTreeDone = true;
                  });
                }
                return result;
              };
              window.__tabActivationBusyStartedAt = performance.now();
              renderFileExplorerChangesPanel(panelNodes.get(differItemId), {force: true});
              const pending = window.__tabActivationTreePromises.length === 1 && !window.__tabActivationTreeDone;
              done({pending, visible: fileExplorerSessionFilesPaneIsVisible(), rows: files.length});
            })().catch(error => done({error: String(error?.stack || error)}));
            """,
            runtime.sessions[0],
            runtime.sessions[1],
            runtime.sessions[0],
            runtime.sessions[1],
        )
        assert "error" not in differ_setup, differ_setup
        assert differ_setup["pending"] and differ_setup["visible"] and differ_setup["rows"] == 5000, differ_setup
        WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                "return !window.__tabActivationTreeDone && Boolean(document.querySelector('.file-explorer-panel[data-panel-item=\"__differ__\"] .changes-repo-group[data-changes-repo=\"/repo/app\"] .file-tree-row'));"
            )
        )
        browser.execute_script(
            "window.__yomuxTabSwitchPhaseTwoDelayMs = 10000; window.__tabActivationWriteBarrier.hold = true;"
        )
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{first}"]')
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{second}"]')
        busy_transition = _wait_tab_transition_frame(browser, second)
        busy_ready = _assert_activation_pending_and_typing_ready(browser, second)
        busy_samples = []
        for offset, index in enumerate(range(21, 41)):
            WebDriverWait(browser, 12, poll_frequency=0.01).until(
                lambda driver: driver.execute_script(
                    "return document.querySelectorAll('.file-explorer-panel[data-panel-item=\"__differ__\"] .changes-repo-group[data-changes-repo=\"/repo/app\"] .file-tree-row').length >= arguments[0];",
                    max(1, offset * 250),
                )
            )
            busy_samples.append(_send_key_until_visible_echo(browser, sample_index=index))
        delivery = browser.execute_script(
            """
            return {samples: window.__tabSwitchEchoProbe.samples.length,
              markers: window.__tabSwitchEchoProbe.samples.map(sample => sample.marker),
              treeDone: window.__tabActivationTreeDone};
            """
        )
        assert delivery["samples"] == 40 and delivery["markers"] == [f"<E:{index:04d}>" for index in range(1, 41)], delivery
        busy_evidence = browser.execute_script(
            "return window.__tabSwitchEchoProbe.samples.slice(20).map(sample => ({treePending: sample.treePending, treeRowsAtEvent: sample.treeRowsAtEvent}));"
        )
        assert busy_evidence[0]["treePending"] and busy_evidence[0]["treeRowsAtEvent"] > 0, busy_evidence
        tree_state = browser.execute_async_script(
            """
            const done = arguments[0];
            Promise.all(window.__tabActivationTreePromises).then(() => {
              window.__tabActivationTreeDone = true;
              const panel = panelNodes.get(differItemId);
              done({rows: panel?.querySelectorAll('.file-tree-row').length || 0, done: window.__tabActivationTreeDone});
            }, error => done({error: String(error?.stack || error)}));
            """
        )
        assert "error" not in tree_state and tree_state["done"] and tree_state["rows"] >= 5000, tree_state
        browser.execute_async_script("const done = arguments[0]; requestAnimationFrame(() => requestAnimationFrame(done));")
        busy_render_timeline = browser.execute_script(
            """
            const startTime = window.__tabActivationBusyStartedAt;
            const endTime = performance.now();
            return {startTime, endTime,
              longTasks: window.__tabTransitionLongTasks.filter(task => task.startTime + task.duration > startTime && task.startTime < endTime),
              samples: window.__tabSwitchEchoProbe.samples.slice(20).map(sample => ({
                index: sample.index, eventAt: sample.eventAt, renderAt: sample.renderAt, paintedAt: sample.paintedAt,
                treePending: sample.treePending, treeRowsAtEvent: sample.treeRowsAtEvent,
              }))};
            """
        )
        assert busy_transition["eventAt"] > busy_render_timeline["startTime"], busy_transition
        tail_row = browser.execute_async_script(
            """
            const done = arguments[0];
            const panel = panelNodes.get(differItemId);
            const scrollport = panel.querySelector('.file-explorer-changes-panel');
            const row = panel.querySelector('.file-tree-row[data-path="/repo/app/file-4999.txt"]');
            if (!row || !scrollport) { done({error: 'the final Differ row or scrollport is missing'}); return; }
            const scrollTop = scrollport.scrollTop;
            const estimatedHeight = row.getBoundingClientRect().height;
            row.scrollIntoView({block: 'center'});
            requestAnimationFrame(() => requestAnimationFrame(() => {
              const box = row.getBoundingClientRect();
              const viewport = scrollport.getBoundingClientRect();
              const name = row.querySelector('.file-tree-name');
              done({visible: row.checkVisibility({contentVisibilityAuto: true}),
                insideViewport: box.top < viewport.bottom && box.bottom > viewport.top,
                estimatedHeight, renderedHeight: box.height, nameWidth: name?.getBoundingClientRect().width || 0,
                label: name?.textContent || ''});
              scrollport.scrollTop = scrollTop;
            }));
            """
        )
        assert "error" not in tail_row and tail_row["visible"] and tail_row["insideViewport"], tail_row
        assert abs(tail_row["estimatedHeight"] - tail_row["renderedHeight"]) < 1 and tail_row["nameWidth"] > 0, tail_row
        assert tail_row["label"] == "file-4999.txt", tail_row
        _release_held_activation_write(browser, second)
        WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                "return !document.querySelector(`.dockview-pane-tab[data-pane-tab=\"${CSS.escape(arguments[0])}\"]`)?.classList.contains('tab-activation-pending');",
                second,
            )
        )
        # Exercise the real cached activation scheduler after the held-work proof.
        browser.execute_script(
            """
            delete window.__yomuxTabSwitchPhaseTwoDelayMs;
            window.__tabActivationTreePromises = [];
            window.__tabActivationPhaseTwoStartedAt = performance.now();
            """,
            first,
            second,
        )
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{first}"]')
        phase_one_wait = WebDriverWait(browser, 12, poll_frequency=0.01)
        try:
            phase_one_wait.until(
                lambda driver: driver.execute_script(
                    "return fileExplorerSessionFilesState.payload.session === arguments[0] && window.__tabActivationTreePromises.length > 0;", first
                )
            )
        except TimeoutException:
            phase_one_failure = browser.execute_script(
                """
                return {first: arguments[0], second: arguments[1], active: activeItemForSide('left'),
                  focusedTerminal, focusedPanelItem, explicitTarget: fileExplorerExplicitSyncSessionTarget(),
                  selectedSession: fileExplorerChangesSelectedSession,
                  interactionCurrent: fileExplorerChangesSessionInteractionIsCurrent(arguments[0]),
                  differPayload: fileExplorerSessionFilesState.payload?.session || '',
                  differVisible: fileExplorerSessionFilesPaneIsVisible(),
                  renderPromiseCount: window.__tabActivationTreePromises.length,
                  tabActive: document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(arguments[0])}"]`)?.className,
                  panelExists: Boolean(document.querySelector('.file-explorer-changes-panel'))};
                """,
                first,
                second,
            )
            pytest.fail(f"cached tab phase-one sync did not start: {phase_one_failure}")
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{second}"]')
        phase_two_transition = _wait_tab_transition_frame(browser, second)
        WebDriverWait(browser, 12, poll_frequency=0.01).until(
            lambda driver: driver.execute_script(
                "return fileExplorerSessionFilesState.payload.session === arguments[0] && fileExplorerExplicitSyncSessionTarget() === arguments[0] && window.__tabActivationTreePromises.length > 1;", second
            )
        )
        phase_two_samples = [_send_key_until_visible_echo(browser, sample_index=index) for index in range(41, 61)]
        phase_two = _echo_timing_summary(phase_two_samples)
        phase_two_state = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            Promise.all(window.__tabActivationTreePromises).then(() => {
              requestAnimationFrame(() => requestAnimationFrame(() => {
                const startTime = window.__tabActivationPhaseTwoStartedAt;
                const endTime = performance.now();
                done({startTime, endTime, session: fileExplorerSessionFilesState.payload.session,
                  syncTarget: fileExplorerExplicitSyncSessionTarget(),
                  rows: document.querySelectorAll('.changes-repo-group[data-changes-repo="/repo/app"] .file-tree-row').length,
                  samples: window.__tabSwitchEchoProbe.samples.slice(40).map(sample => ({
                    index: sample.index, treePending: sample.treePending, treeRowsAtEvent: sample.treeRowsAtEvent})),
                  longTasks: window.__tabTransitionLongTasks.filter(task => task.startTime + task.duration > startTime && task.startTime < endTime)});
              }));
            }, error => done({error: String(error)}));
            """
        )
        assert phase_two_transition["eventAt"] > phase_two_state["startTime"], phase_two_transition
        assert "error" not in phase_two_state and phase_two_state["session"] == second and phase_two_state["syncTarget"] == second, phase_two_state
        assert phase_two_state["rows"] == 5000 and phase_two_state["samples"][0]["treePending"], phase_two_state
        quiet = _echo_timing_summary(quiet_samples)
        busy = _echo_timing_summary(busy_samples)
        assert len(quiet_samples) == 20 and len(busy_samples) == 20, {"quiet": quiet, "busy": busy}

        browser.execute_script(
            """
            const item = terminals.get(arguments[0]);
            const originalWrite = item.term.write.bind(item.term);
            window.__heldEchoDelayReleased = false;
            item.term.write = function(data, callback) {
              if (!window.__heldEchoDelayReleased && data !== '') {
                window.__heldEchoDelayReleased = true;
                setTimeout(() => originalWrite(data, callback), 150);
                return;
              }
              return originalWrite(data, callback);
            };
            """,
            second,
        )
        held_delay_ms = _send_key_until_visible_echo(browser, sample_index=61)
        browser.execute_script(
            "terminals.get(arguments[0]).term.write = window.__tabActivationOriginalWrite;",
            second,
        )
        negative_control = _echo_timing_verdicts([held_delay_ms], label="held visible-echo negative control")
        assert held_delay_ms >= 100.0 and [verdict["passed"] for verdict in negative_control] == [False, False], {
            "held_delay_ms": held_delay_ms,
            "negative_control": negative_control,
        }
        verdicts = [
            *_echo_timing_verdicts(quiet_samples, label="quiet native key to visible echo"),
            *_echo_timing_verdicts(busy_samples, label="Differ-busy native key to visible echo"),
            *_echo_timing_verdicts(phase_two_samples, label="cached phase-two native key to visible echo"),
        ]
        return {
            "quiet": quiet,
            "busy": busy,
            "phase_two": phase_two,
            "phase_two_samples_ms": phase_two_samples,
            "phase_two_transition": phase_two_transition,
            "phase_two_state": phase_two_state,
            "static_replacements": browser.execute_script("return window.__tabActivationStaticReplacements;"),
            "quiet_timeline": quiet_timeline,
            "quiet_samples_ms": quiet_samples,
            "busy_samples_ms": busy_samples,
            "pending_color": quiet_ready,
            "stale_callback_pending_state": quiet_ready_after_stale,
            "legacy_pending_color": legacy_pending,
            "busy_pending_color": busy_ready,
            "quiet_transition": quiet_transition,
            "busy_transition": busy_transition,
            "busy_render_timeline": busy_render_timeline,
            "differ_rows": tree_state["rows"],
            "differ_tail_row": tail_row,
            "delivery": delivery,
            "differ_pending_at_first_busy_key": busy_evidence[0],
            "held_delay_ms": held_delay_ms,
            "negative_control_verdicts": negative_control,
            "verdicts": verdicts,
        }
    finally:
        browser.execute_script(
            """
            window.__tabTransitionLongTaskObserver?.dispose();
            window.__tabSwitchEchoProbe?.dispose();
            if (window.__tabActivationOriginalRenderChangesGroups) renderChangesGroups = window.__tabActivationOriginalRenderChangesGroups;
            if (window.__tabActivationOriginalReplaceChangesStaticHtml) replaceChangesStaticHtml = window.__tabActivationOriginalReplaceChangesStaticHtml;
            if (window.__tabActivationOriginalFetch) window.fetch = window.__tabActivationOriginalFetch;
            const barrier = window.__tabActivationWriteBarrier;
            if (barrier) {
              barrier.hold = false;
              for (const callback of barrier.callbacks.splice(0)) callback();
            }
            const originalItem = window.__tabActivationOriginalItem;
            if (originalItem) terminals.set(barrier.session, originalItem);
            const item = originalItem || terminals.get(barrier?.session);
            if (item?.term && window.__tabActivationOriginalWrite) item.term.write = window.__tabActivationOriginalWrite;
            delete window.__yomuxTabSwitchPhaseTwoDelayMs;
            """
        )
        stop_browser_fixture(server, thread, runtime, browser=browser)


@pytest.mark.browser
# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_s1_tab_activation_keeps_native_echo_live_while_cached_differ_renders(browser, monkeypatch, tmp_path):
    evidence = _run_tab_activation_native_echo_journey(browser, monkeypatch, tmp_path)
    print({key: evidence[key] for key in ("quiet", "quiet_timeline", "static_replacements", "busy", "phase_two", "phase_two_transition", "phase_two_state", "quiet_transition", "busy_transition", "busy_render_timeline", "differ_rows", "held_delay_ms")})
    assert evidence["delivery"]["samples"] == 40, evidence["delivery"]
    assert evidence["delivery"]["markers"] == [f"<E:{index:04d}>" for index in range(1, 41)], evidence["delivery"]
    assert evidence["differ_pending_at_first_busy_key"]["treePending"], evidence
    assert evidence["differ_rows"] >= 5000, evidence


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_s1_certification_tab_activation_native_echo_holds_fixed_user_ceilings(certification_phase_only, request, browser, monkeypatch, tmp_path):
    """Certify native visible echo in quiet and busy real-xterm tab activations."""

    calibration = latency_calibration.run_browser_latency_calibration(browser)
    calibration_qualification = latency_calibration.browser_calibration_qualification(calibration)
    qualification = latency_calibration.merged_qualification(certification_phase_only, calibration_qualification)
    evidence = _run_tab_activation_native_echo_journey(browser, monkeypatch, tmp_path)
    certified = latency_calibration.certify_verdicts(
        nodeid=request.node.nodeid,
        label="s1-tab-activation-visible-echo",
        verdicts=evidence["verdicts"],
        qualification=qualification,
        extra_evidence={
            "accepted_ceilings_ms": {"p95": 50.0, "p99": 100.0},
            "quiet_samples_ms": evidence["quiet_samples_ms"],
            "differ_busy_samples_ms": evidence["busy_samples_ms"],
            "cached_phase_two_samples_ms": evidence["phase_two_samples_ms"],
            "cached_phase_two_transition": evidence["phase_two_transition"],
            "cached_phase_two_state": evidence["phase_two_state"],
            "static_replacements": evidence["static_replacements"],
            "quiet_timeline": evidence["quiet_timeline"],
            "held_delay_negative_control_ms": evidence["held_delay_ms"],
            "negative_control_verdicts": evidence["negative_control_verdicts"],
            "sample_count_per_condition": 20,
            "native_markers_delivered_in_order": evidence["delivery"]["markers"],
            "differ_rows_rendered": evidence["differ_rows"],
            "differ_pending_at_first_busy_key": evidence["differ_pending_at_first_busy_key"],
            "pending_color": evidence["pending_color"],
            "legacy_pending_color": evidence["legacy_pending_color"],
            "stale_callback_pending_state": evidence["stale_callback_pending_state"],
            "busy_pending_color": evidence["busy_pending_color"],
            "quiet_transition": evidence["quiet_transition"],
            "busy_transition": evidence["busy_transition"],
            "busy_render_timeline": evidence["busy_render_timeline"],
            "browser_calibration": calibration,
        },
    )
    print({"quiet": evidence["quiet"], "busy": evidence["busy"], "heldDelayMs": evidence["held_delay_ms"], "verdicts": evidence["verdicts"], "artifact": certified["artifact"]})


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_s1_keystroke_delivery_is_complete_under_terminal_output_pressure(browser, gate_live_server):
    """Every one of 120 native keys reaches xterm, the websocket and the session while it streams 4 KiB every 20 ms.

    Release-blocking correctness, safe to run in the parallel browser lane: every assertion is a count
    or a byte total, so an oversubscribed renderer cannot change the verdict. The user-visible wall
    ceiling that used to be fused in here is certified separately - see the certification test below.
    """

    session = _focus_gate_terminal(browser, gate_live_server)
    observed = _keystroke_round(
        browser,
        gate_live_server,
        session,
        label="pressured",
        sample_count=KEYSTROKE_LATENCY_SAMPLE_COUNT,
        pressure=True,
    )
    print(f"S1 delivery: {_keystroke_summary(observed)}")
    _assert_keystroke_delivery_complete(observed)


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_s1_keystroke_delivery_survives_a_deterministic_renderer_cpu_slowdown(browser, gate_live_server):
    """A deterministic renderer CPU slowdown must change wall latency only, never delivery.

    The standing proof that the split is real. The same round under a 10x slowdown measured max 11.1 ms
    - a red under the old fused ceiling - with 120/120 keys delivered, 120 one-byte sends and no focus
    repair. This keeps a cheaper 4x version in the release-blocking lane so the delivery contract stays
    insensitive to renderer scheduling; it deliberately asserts no latency.
    """

    session = _focus_gate_terminal(browser, gate_live_server)
    established_pressure = _establish_terminal_output_pressure(
        browser,
        gate_live_server,
        session,
        duration_seconds=KEYSTROKE_SLOWDOWN_CONTROL_PRESSURE_SECONDS,
    )
    browser.execute_cdp_cmd("Emulation.setCPUThrottlingRate", {"rate": KEYSTROKE_SLOWDOWN_CONTROL_RATE})
    try:
        observed = _keystroke_round(
            browser,
            gate_live_server,
            session,
            label=f"cpu-slowdown-{KEYSTROKE_SLOWDOWN_CONTROL_RATE}x",
            sample_count=KEYSTROKE_SLOWDOWN_CONTROL_SAMPLE_COUNT,
            pressure=True,
            pressure_seconds=KEYSTROKE_SLOWDOWN_CONTROL_PRESSURE_SECONDS,
            established_pressure=established_pressure,
        )
    finally:
        browser.execute_cdp_cmd("Emulation.setCPUThrottlingRate", {"rate": 1})
    print(f"S1 delivery under CPU slowdown: {_keystroke_summary(observed)}")
    _assert_keystroke_delivery_complete(observed)


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.no_browser
def test_s1_cpu_slowdown_starts_only_after_pressure_is_established(monkeypatch):
    """The parallel completeness control must not throttle its pressure-readiness prerequisite."""

    calls: list[object] = []
    pressure = TerminalOutputPressure(session="fixture-session", duration_seconds=45.0)

    class Browser:
        def execute_cdp_cmd(self, command, payload):
            calls.append((command, payload))

    def establish(_browser, _runtime, session, *, duration_seconds):
        calls.append(("establish", session, duration_seconds))
        return pressure

    def sample(_browser, _runtime, session, **options):
        calls.append(("sample", session, options["established_pressure"]))
        return _complete_delivery_observation(KEYSTROKE_SLOWDOWN_CONTROL_SAMPLE_COUNT)

    monkeypatch.setattr(sys.modules[__name__], "_focus_gate_terminal", lambda _browser, _runtime: pressure.session)
    monkeypatch.setattr(sys.modules[__name__], "_establish_terminal_output_pressure", establish)
    monkeypatch.setattr(sys.modules[__name__], "_keystroke_round", sample)

    test_s1_keystroke_delivery_survives_a_deterministic_renderer_cpu_slowdown(Browser(), object())

    assert calls == [
        ("establish", pressure.session, KEYSTROKE_SLOWDOWN_CONTROL_PRESSURE_SECONDS),
        ("Emulation.setCPUThrottlingRate", {"rate": KEYSTROKE_SLOWDOWN_CONTROL_RATE}),
        ("sample", pressure.session, pressure),
        ("Emulation.setCPUThrottlingRate", {"rate": 1}),
    ]


@pytest.mark.no_browser
def test_s1_pressure_round_waits_for_the_existing_byte_floor_before_final_observation(monkeypatch):
    baseline_bytes = 41
    sent_keys = 0
    barriers: list[tuple[int, str]] = []
    pressure = TerminalOutputPressure(session="fixture-session", duration_seconds=12.0)

    class Browser:
        def execute_script(self, script):
            assert script == "clearClientPerfCounters();"

    def send_native_key(_browser, character):
        nonlocal sent_keys
        assert character == "a"
        sent_keys += 1

    def client_perf_counter(_browser, name):
        if name == "xtermWrite":
            return {"bytes": baseline_bytes}
        assert name == "keydownToTermData"
        return {"count": sent_keys, "lastMs": 0.1}

    def wait_for_bytes(_browser, minimum_bytes, description):
        barriers.append((minimum_bytes, description))
        return minimum_bytes + 17

    monkeypatch.setattr(sys.modules[__name__], "_send_native_key", send_native_key)
    monkeypatch.setattr(sys.modules[__name__], "_client_perf_counter", client_perf_counter)
    monkeypatch.setattr(sys.modules[__name__], "_wait_for_xterm_write_bytes", wait_for_bytes)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_client_perf_trace",
        lambda _browser: {
            "focusSet": None,
            "wsSend": {"count": sent_keys, "bytes": sent_keys},
            "termOnData": {"count": sent_keys, "bytes": sent_keys},
            "echoToTermWrite": {"count": 1},
            "xtermWrite": {"bytes": baseline_bytes + TERMINAL_PRESSURE_SAMPLING_MIN_BYTES + 17},
            "longTasks": {"count": 0, "maxMs": 0},
        },
    )

    observed = _keystroke_round(
        Browser(),
        object(),
        pressure.session,
        label="pressure-barrier",
        sample_count=2,
        pressure=True,
        established_pressure=pressure,
    )

    assert barriers == [(
        baseline_bytes + TERMINAL_PRESSURE_SAMPLING_MIN_BYTES,
        "terminal output pressure remains observable through the keystroke round",
    )]
    assert observed["pressure_bytes_observed"] == TERMINAL_PRESSURE_SAMPLING_MIN_BYTES + 17


def _certified_latency_statistic(cpu_qualified: bool) -> str:
    """Choose WHICH statistic the host may certify. It never chooses how slow the product may be."""

    return "max_ms" if cpu_qualified else KEYSTROKE_LATENCY_CERTIFIED_QUANTILE_KEY


def _certification_verdict(summary: dict[str, object], certified_statistic: str) -> dict[str, object]:
    """Judge one round against the fixed ceiling, recording the raw statistics either way.

    The comparison itself belongs to latency_calibration.fixed_ceiling_verdict; this only selects
    which statistic of the round is submitted to it and keeps the round's raw fields alongside.
    """

    return {
        **summary,
        **latency_calibration.fixed_ceiling_verdict(
            label="S1 keystroke wall latency",
            raw_measured_ms=float(summary[certified_statistic]),
            ceiling_ms=KEYSTROKE_LATENCY_CEILING_MS,
            statistic=certified_statistic,
        ),
        "certified_statistic": certified_statistic,
        "certified_value_ms": summary[certified_statistic],
    }


# Admission and host fitness both come from the one owner in tests/latency_calibration.py. The
# private `_certification_phase_requested` plus fixture that used to live here shared only the
# env-var name with it, so a threshold or an admission rule could drift between this unit and the
# phase that runs it.
certification_phase_only = latency_calibration.certification_phase_fixture()


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_s1_certification_keystroke_wall_latency_holds_the_fixed_user_ceiling(certification_phase_only, request, browser, gate_live_server):
    """Certify the user-visible keystroke wall latency on a quiet and a pressured round in ONE browser.

    Requirement for the exclusive phase that owns this node:
      ceiling        - a fixed 3.0 ms from explicit key input to xterm term.onData, identical on every
                       host. No calibration, load factor or host measurement may widen it; a slower
                       machine authorising a slower product is exactly the antipattern being removed.
      statistic      - nearest-rank p99 of the 120 per-key samples in each round.
      qualification  - two independent inputs, both from latency_calibration and neither able to move
                       the ceiling. The host qualifier (windowed PSI, procs_running, disk busy and a
                       CPU work unit, measured by `certification_phase_only` before any fixture is
                       built) and the pre-page renderer calibration must BOTH admit; either one
                       refusing produces NOT CERTIFIABLE with its raw evidence. The per-key MAXIMUM is
                       additionally certified only when the renderer calibration is at or below the
                       recorded reference (latency_calibration.CALIBRATION_REFERENCE_MS p75); on a
                       renderer inside admission but past the reference the maximum is recorded in the
                       artifact and the certified statistic falls back to p99, because one sample under
                       a contended scheduler measures the host.
      rounds         - quiet and pressured, same browser, same page load, both must hold the ceiling.
      delivery       - both rounds must also satisfy the full delivery contract; certification may
                       never be reached by typing fewer keys.
    """

    calibration = latency_calibration.run_browser_latency_calibration(browser)
    calibration_qualification = latency_calibration.browser_calibration_qualification(calibration)
    qualification = latency_calibration.merged_qualification(certification_phase_only, calibration_qualification)
    certified_statistic = _certified_latency_statistic(calibration_qualification["at_reference"])

    session = _focus_gate_terminal(browser, gate_live_server)
    rounds = [
        _keystroke_round(browser, gate_live_server, session, label="quiet", sample_count=KEYSTROKE_LATENCY_SAMPLE_COUNT, pressure=False),
        _keystroke_round(browser, gate_live_server, session, label="pressured", sample_count=KEYSTROKE_LATENCY_SAMPLE_COUNT, pressure=True),
    ]
    for observed in rounds:
        _assert_keystroke_delivery_complete(observed)

    verdicts = [_certification_verdict(_keystroke_summary(observed), certified_statistic) for observed in rounds]
    print(f"S1 certification: statistic={certified_statistic} qualified={qualification['qualified']} rounds={verdicts}")
    certified = latency_calibration.certify_verdicts(
        nodeid=request.node.nodeid,
        label="s1-keystroke-wall-latency",
        verdicts=verdicts,
        qualification=qualification,
        extra_evidence={
            "ceiling_ms": KEYSTROKE_LATENCY_CEILING_MS,
            "certified_statistic": certified_statistic,
            "sample_count": KEYSTROKE_LATENCY_SAMPLE_COUNT,
        },
    )
    print(f"S1 certification artifact: {certified['artifact']}")


def _negative_control_reasons(observed: dict[str, object]) -> list[str]:
    return [failure.split(":", 1)[0] for failure in _keystroke_delivery_failures(observed)]


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_s1_negative_control_dropped_keystroke_fails_delivery_completeness(browser, gate_live_server):
    """A single key that never reaches xterm must red the retained delivery contract."""

    session = _focus_gate_terminal(browser, gate_live_server)
    dropped_key = KEYSTROKE_LATENCY_SAMPLE_COUNT // 2
    browser.execute_script(
        """
        const session = arguments[0];
        const dropIndex = Number(arguments[1]);
        const original = window.handleTerminalData;
        if (typeof original !== 'function') throw new Error('handleTerminalData is not reachable for injection');
        let seen = 0;
        window.handleTerminalData = function (targetSession, data, options) {
          if (targetSession === session) {
            seen += 1;
            if (seen === dropIndex) return false;
          }
          return original(targetSession, data, options);
        };
        """,
        session,
        dropped_key,
    )
    observed = _keystroke_round(
        browser,
        gate_live_server,
        session,
        label="negative-control-dropped-key",
        sample_count=KEYSTROKE_LATENCY_SAMPLE_COUNT,
        pressure=True,
    )
    reasons = _negative_control_reasons(observed)
    print(f"S1 negative control (dropped key {dropped_key}): reasons={reasons} summary={_keystroke_summary(observed)}")
    # One lost key is visible independently on the key counter, the socket and xterm's own data event.
    assert reasons == ["keystroke-delivery", "websocket-send", "term-on-data"], {"reasons": reasons, "summary": _keystroke_summary(observed)}
    assert observed["counts"][-1] == KEYSTROKE_LATENCY_SAMPLE_COUNT - 1, observed["counts"]


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_s1_negative_control_stolen_focus_fails_the_focus_invariant(browser, gate_live_server):
    """Focus leaving and returning to the terminal mid-burst must red the retained focus invariant."""

    session = _focus_gate_terminal(browser, gate_live_server)
    steal_after_key = 5

    def steal_focus(index: int) -> None:
        if index != steal_after_key:
            return
        browser.execute_script(
            """
            const session = arguments[0];
            const textarea = document.querySelector(`#term-${CSS.escape(session)} textarea`);
            if (!textarea) throw new Error('terminal textarea is not mounted: ' + session);
            textarea.blur();
            textarea.focus();
            """,
            session,
        )

    observed = _keystroke_round(
        browser,
        gate_live_server,
        session,
        label="negative-control-stolen-focus",
        sample_count=KEYSTROKE_LATENCY_SAMPLE_COUNT,
        pressure=True,
        after_key=steal_focus,
    )
    reasons = _negative_control_reasons(observed)
    print(f"S1 negative control (stolen focus): reasons={reasons} summary={_keystroke_summary(observed)}")
    # Delivery is untouched, so the focus invariant is the only thing that can catch this.
    assert reasons == ["focus-invariant"], {"reasons": reasons, "summary": _keystroke_summary(observed)}


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_s1_negative_control_stalled_websocket_fails_send_completeness(browser, gate_live_server):
    """A transport that stops accepting input mid-burst must red the retained websocket contract.

    xterm still emits every key and keydownToTermData still records it, so this is exactly the state a
    delivery test that only counted keys would call green.
    """

    session = _focus_gate_terminal(browser, gate_live_server)
    stall_after_key = KEYSTROKE_LATENCY_SAMPLE_COUNT // 2

    def stall_socket(index: int) -> None:
        if index != stall_after_key:
            return
        browser.execute_script(
            """
            const session = arguments[0];
            const item = terminals.get(session);
            if (!item?.socket) throw new Error('terminal socket is not mounted: ' + session);
            Object.defineProperty(item.socket, 'readyState', {configurable: true, get: () => WebSocket.CLOSING});
            """,
            session,
        )

    observed = _keystroke_round(
        browser,
        gate_live_server,
        session,
        label="negative-control-stalled-websocket",
        sample_count=KEYSTROKE_LATENCY_SAMPLE_COUNT,
        # Output pressure has its own positive control. This negative control injects one
        # transport defect and must not acquire a second, scheduler-sensitive precondition.
        pressure=False,
        after_key=stall_socket,
    )
    reasons = _negative_control_reasons(observed)
    print(f"S1 negative control (stalled websocket): reasons={reasons} summary={_keystroke_summary(observed)}")
    # Every key still counted; only the transport contract can see the stall.
    assert reasons == ["websocket-send"], {"reasons": reasons, "summary": _keystroke_summary(observed)}
    assert observed["counts"] == list(range(1, KEYSTROKE_LATENCY_SAMPLE_COUNT + 1)), observed["counts"]
    assert observed["ws_send_count"] == stall_after_key, observed["ws_send_count"]


def _complete_delivery_observation(sample_count: int = 4) -> dict[str, object]:
    return {
        "label": "synthetic",
        "sample_count": sample_count,
        "pressure": True,
        "counts": list(range(1, sample_count + 1)),
        "samples": [0.1] * sample_count,
        "sampling_seconds": 1.0,
        "focus_set": None,
        "ws_send_count": sample_count,
        "ws_send_bytes": sample_count,
        "term_on_data_count": sample_count,
        "term_on_data_bytes": sample_count,
        "echo_count": sample_count,
        "pressure_bytes_observed": TERMINAL_PRESSURE_SAMPLING_MIN_BYTES,
        "long_tasks": {"count": 0, "maxMs": 0},
    }


@pytest.mark.parametrize(
    "violation, expected_reason",
    [
        ({"counts": [1, 2, 2, 3]}, "keystroke-delivery"),
        ({"focus_set": {"name": "focusSet", "count": 1}}, "focus-invariant"),
        ({"ws_send_count": 3}, "websocket-send"),
        ({"ws_send_bytes": 3}, "websocket-send"),
        ({"term_on_data_count": 3}, "term-on-data"),
        ({"term_on_data_bytes": 3}, "term-on-data"),
        ({"echo_count": 0}, "socket-echo"),
        ({"pressure_bytes_observed": TERMINAL_PRESSURE_SAMPLING_MIN_BYTES - 1}, "output-pressure"),
    ],
)
@pytest.mark.no_browser
def test_s1_delivery_report_names_exactly_the_violated_contract(violation, expected_reason):
    """Every retained contract must be individually detectable, including the ones a browser control cannot isolate."""

    assert _keystroke_delivery_failures(_complete_delivery_observation()) == []
    observed = {**_complete_delivery_observation(), **violation}
    reasons = [failure.split(":", 1)[0] for failure in _keystroke_delivery_failures(observed)]
    assert reasons == [expected_reason], (reasons, observed)


@pytest.mark.no_browser
def test_s1_delivery_report_ignores_wall_latency_entirely():
    """The parallel-lane contract must stay a pure delivery contract; latency lives in the exclusive phase."""

    slow = {**_complete_delivery_observation(), "samples": [999.0, 999.0, 999.0, 999.0]}
    assert _keystroke_delivery_failures(slow) == []
    assert max(slow["samples"]) > KEYSTROKE_LATENCY_CEILING_MS


def _certification_round_summary(*, max_ms: float, quantile_ms: float) -> dict[str, object]:
    return {"label": "synthetic", "max_ms": max_ms, KEYSTROKE_LATENCY_CERTIFIED_QUANTILE_KEY: quantile_ms}


@pytest.mark.no_browser
def test_s1_certification_qualification_selects_the_statistic_and_never_widens_the_ceiling():
    """CPU qualification may only decide which statistic is certifiable, never the product's budget."""

    qualified = _certified_latency_statistic(True)
    unqualified = _certified_latency_statistic(False)
    assert qualified == "max_ms"
    assert unqualified == KEYSTROKE_LATENCY_CERTIFIED_QUANTILE_KEY

    # The exact observed gate red: one 4 ms sample with a 0.2 ms p99.
    outlier = _certification_round_summary(max_ms=4.0, quantile_ms=0.2)
    assert _certification_verdict(outlier, qualified)["passed"] is False
    assert _certification_verdict(outlier, unqualified)["passed"] is True

    # A genuinely slow product stays red on a slow host: no factor, no widened ceiling.
    slow_product = _certification_round_summary(max_ms=9.0, quantile_ms=5.1)
    for statistic in (qualified, unqualified):
        verdict = _certification_verdict(slow_product, statistic)
        assert verdict["ceiling_ms"] == KEYSTROKE_LATENCY_CEILING_MS
        assert verdict["passed"] is False
    assert {_certification_verdict(outlier, statistic)["ceiling_ms"] for statistic in (qualified, unqualified)} == {KEYSTROKE_LATENCY_CEILING_MS}


def _run_go_tab_and_body_activation(browser, isolated_dev_server_factory, mode="plain", profile=False):
    """Trusted Chromium gestures measure the real Go/PTY/xterm path, with no operator state."""
    runtime = isolated_dev_server_factory("activation", session_count=3 if mode in ("split", "tabber") else 2)
    first, second = runtime.tmux.sessions[:2]
    third = runtime.tmux.sessions[2] if mode == "tabber" else ""
    for session in runtime.tmux.sessions[:3] if mode == "tabber" else (first, second):
        _start_marker_echo_process(runtime, session, pressure=mode == "busy")
    sessions = (first, second, third) if third else (first, second)
    initial_tabs = sessions if mode == "tabber" else (first, second)
    query = urlencode({"sessions": ",".join(sessions), "layout": "left", "tabs": f"left:{','.join(initial_tabs)}"})
    try:
        navigation_started = time.monotonic()
        browser.get(f"{runtime.base_url}/?{query}")
        assert_live_runtime_boot_healthy(browser, "go-activation", timeout=20)
        # These native-tab fixtures start in Advanced; the Tabber case switches through its UI below.
        browser.execute_script("setTabberMode('advanced'); history.replaceState(null, '', arguments[0]); applyLayoutSlots(initialLayoutSlots(), {prune:false});", f"/?{query}")
        for close in browser.find_elements("css selector", "[data-toast-close]"):
            if close.is_displayed():
                close.click()
        cold_started = time.monotonic()
        for session in sessions:
            selector = f'.dockview-pane-tab[data-pane-tab="{session}"]'
            try:
                click_visible_selector(browser, selector)
            except AssertionError as error:
                toast = browser.execute_script("return Array.from(document.querySelectorAll('.toast-title')).map(node => node.closest('.toast')?.innerText || node.textContent);")
                raise AssertionError(f"activation setup tab {session} was obstructed by toast: {toast}") from error
            WebDriverWait(browser, 20).until(lambda driver: driver.execute_script(
                "const item=terminals.get(arguments[0]); return item?.terminalOutputSeen && item.term?.onRender && item.container.querySelector('.xterm-screen');", session))
        cold_connection_seconds = time.monotonic() - navigation_started
        if mode == "split":
            browser.execute_script("const slots=emptyLayoutSlots(); slots[layoutTreeKey]=splitNode('row',leafNode('left'),leafNode('right'),0.5); slots.left=paneStateWithTabs(arguments[0],arguments[0][0]); slots.right=paneStateWithTabs([arguments[1]],arguments[1]); applyLayoutSlots(slots,{prune:false});", [first, second], runtime.tmux.sessions[2])
        elif mode == "tabber":
            browser.execute_async_script("""
              const done = arguments[arguments.length - 1], initial = arguments[0];
              (async () => {
                await openTabberActivityOverview();
                const advanced = document.querySelector('[data-tabber-advanced]');
                if (advanced.checked) advanced.click();
                storeTabberNormalSelections([initial]);
                await applyTabberNormalSelections(initial);
                done(true);
              })().catch(error => done({error:String(error)}));
            """, first)
        elif mode == "busy":
            browser.execute_script("""
              const fetch = window.fetch.bind(window);
              window.__activationHeldMetadata = [];
              window.fetch = async (...args) => {
                const response = await fetch(...args);
                if (String(args[0]).includes('/api/session-metadata')) await new Promise(resolve => window.__activationHeldMetadata.push(resolve));
                return response;
              };
              window.__activationRestoreFetch = () => { window.fetch = fetch; window.__activationHeldMetadata.splice(0).forEach(resolve => resolve()); };
              refreshTranscripts({force:true}); refreshAutoStatuses();
            """)
            WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("return window.__activationHeldMetadata.length > 0;"))
        # Layout changes are cold work; a warm gesture starts only after the real fit/paint owners settle.
        warm_sessions = (second, third, first) if mode == "tabber" else (second, first)
        for session in warm_sessions:
            selector = (f'#panel-__tabber__ .file-tree-row[data-tabber-type="normal"][data-tabber-session="{session}"]'
                        if mode == "tabber" else f'.dockview-pane-tab[data-pane-tab="{session}"]')
            click_visible_selector(browser, selector)
            try:
                WebDriverWait(browser, 10, poll_frequency=0.01).until(lambda driver: driver.execute_script(
                    "const item=terminals.get(arguments[0]); return item && !item.fitFrame && !item.refreshPending && !dockviewLayoutState.tabActivationPerf && terminalFitIsUnchanged(item,estimateTerminalSize(item.container,item.term));", session))
            except TimeoutException as error:
                state = browser.execute_script("""
                  const item = terminals.get(arguments[0]);
                      return {exists: Boolean(item), focusedTerminal, focusedPanelItem, activeItems: activePaneItems(),
                        sessions: [...sessions], visibleSessions: [...visibleSessions], maxSessionTabs, layoutItems: [...layoutItems],
                        tabberNormalSelections: [...(tabberNormalSelections || [])],
                    fitFrame: Boolean(item?.fitFrame), refreshPending: Boolean(item?.refreshPending),
                    tabberRows: Array.from(document.querySelectorAll('#panel-__tabber__ [data-tabber-type="normal"]')).map(row => ({session: row.dataset.tabberSession, active: row.className})),
                    size: item && estimateTerminalSize(item.container, item.term),
                    fitUnchanged: item && terminalFitIsUnchanged(item, estimateTerminalSize(item.container, item.term))};
                """, session)
                raise AssertionError(f"Tabber activation warmup did not settle for {session}: {state}") from error
            if mode == "tabber":
                click_visible_selector(browser, f'#term-{session} .xterm-screen')
        cold_seconds = time.monotonic() - cold_started
        browser.execute_async_script("const done=arguments[arguments.length-1]; requestAnimationFrame(() => requestAnimationFrame(() => done(true)));")
        browser.execute_script(
            r"""
            const probe = window.__activationProbe = {samples: [], disposables: [], originals: [], echoes: new Map(), metadataTabStripRefreshes: [], metadataTabberTreeRefreshes: []};
            const now = () => performance.now();
            for (const name of ['applyLayoutSlots', 'refreshTabberPanels', 'syncTabberTreeActiveSelection', 'syncTabberTreeLayoutState', 'scheduleTabberTreeLayoutStateSync', 'renderPaneTabStrips', 'refreshTerminal', 'dockviewRefreshTabs', 'dockviewBeginTabPointerDrag', 'captureDockviewPaneBeforeDetach', 'capturePaneViewState', 'dockviewCommitPanelActivation', 'activateTab', 'setFocusedTerminal', 'focusTerminalDom', 'closeOtherSessionPopovers', 'dockviewRootBoundaryEdgesAtPoint']) {
              const original = eval(name);
              const wrapper = (...args) => {
                const started = now();
                const metadataTabStripRefresh = name === 'renderPaneTabStrips' && args[0]?.deferDuringTerminalActivation === true;
                const metadataTabberTreeRefresh = name === 'scheduleTabberTreeLayoutStateSync' && args[0]?.refreshTree === true;
                const activationPending = Boolean(dockviewLayoutState.tabActivationPerf);
                try { return original(...args); }
                finally {
                  const elapsed = now()-started;
                  if (metadataTabStripRefresh) probe.metadataTabStripRefreshes.push({at: started, elapsedMs: elapsed, activationPending});
                  if (metadataTabberTreeRefresh) probe.metadataTabberTreeRefreshes.push({at: started, elapsedMs: elapsed, activationPending});
                  const sample=probe.samples.at(-1);
                  if (sample) {
                    (sample.work ||= []).push({name, at:started, ms:elapsed});
                    if (name === 'dockviewRefreshTabs') (sample.refreshStacks ||= []).push(new Error().stack);
                  }
                }
              };
              eval(`${name} = wrapper`);
              probe.originals.push(() => { eval(`${name} = original`); });
            }
            const perfEnd = clientPerfEnd;
            probe.originals.push(() => { clientPerfEnd = perfEnd; });
            clientPerfEnd = (token, ...args) => {
              const sample = probe.samples.at(-1);
              if (token?.name === 'tabActivationPaint' && sample && !sample.body) sample.paintAt = now();
              return perfEnd(token, ...args);
            };
            for (const session of arguments[0]) {
              const item = terminals.get(session), term = item.term;
              const send = item.socket.send.bind(item.socket), refresh = term.refresh.bind(term);
              probe.originals.push(() => { item.socket.send = send; term.refresh = refresh; });
              item.socket.send = data => {
                const message = JSON.parse(data), sample = probe.samples.at(-1);
                if (sample?.session === session) {
                  if (message.type === 'resize') { sample.resizes++; (sample.resizeMessages ||= []).push({...message, at: now(), stack: new Error().stack}); }
                  if (message.type === 'input') sample.sentAt = now();
                }
                return send(data);
              };
              term.refresh = (...args) => {
                const sample = probe.samples.at(-1);
                if (sample?.session === session) { sample.refreshes++; (sample.refreshStacks ||= []).push(new Error().stack); }
                return refresh(...args);
              };
              probe.disposables.push(term.onRender(() => {
                const sample = probe.samples.at(-1);
                if (sample?.session !== session) return;
                sample.renderAt ??= now();
                const buffer = term.buffer.active;
                const text = Array.from({length: term.rows}, (_, i) => buffer.getLine(buffer.viewportY + i)?.translateToString(true) || '').join('\n');
                if (sample.marker && text.includes(sample.marker) && !sample.echoAt) {
                  sample.echoRenderAt = now();
                  requestAnimationFrame(() => { sample.echoAt ??= now(); });
                }
              }));
            }
            const renderedHighlight = session => {
              const dockTab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(session)}"]`);
              if (dockTab) return dockTab.classList.contains('active') && dockTab.closest('.dv-tab')?.classList.contains('dv-active-tab') === true;
              const row = document.querySelector(`.file-tree-row[data-tabber-session="${CSS.escape(session)}"]`);
              return row?.classList.contains('tabber-active-normal') === true;
            };
            const renderedPreviousHighlights = session => {
              const dockTab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(session)}"]`);
              if (dockTab) return [...(dockTab.closest('.dv-groupview')?.querySelectorAll('.dockview-pane-tab.active') || [])]
                .map(tab => tab.dataset.paneTab).filter(item => item && item !== session);
              return [...document.querySelectorAll('.file-tree-row.tabber-active-normal')]
                .map(row => row.dataset.tabberSession).filter(item => item && item !== session);
            };
            const observeHighlight = sample => requestAnimationFrame(() => {
              const ready = renderedHighlight(sample.session) && sample.previousHighlights.every(item => !renderedHighlight(item));
              sample.highlightReadyAt = ready ? now() : null;
              requestAnimationFrame(() => {
                const painted = renderedHighlight(sample.session) && sample.previousHighlights.every(item => !renderedHighlight(item));
                sample.highlightAt = painted ? now() : null;
                sample.focusAt ??= document.activeElement === terminals.get(sample.session)?.term.textarea ? now() : null;
              });
            });
            const pointer = event => {
              const tab = event.target.closest('[data-pane-tab], [data-tabber-session]');
              const entry = [...terminals].find(([, item]) => item.container.contains(event.target));
              const session = tab?.dataset.paneTab || tab?.dataset.tabberSession || entry?.[0];
              if (!session || !arguments[0].includes(session)) return;
              const previousHighlights = renderedPreviousHighlights(session);
              const expectedInactive = arguments[1] === 'tabber'
                ? (tabberNormalSelections?.includes(session) ? [] : [...(tabberNormalSelections || [])])
                : previousHighlights;
              const sample = {session, body: !tab, trusted: event.isTrusted, eventAt: event.timeStamp,
                activationAt: event.timeStamp, releaseActivation: Boolean(event.target.closest('.file-tree-row[data-tabber-type="session"]')),
                handlerAt: now(), previousHighlights: expectedInactive.filter(item => previousHighlights.includes(item)), resizes: 0, refreshes: 0};
              probe.samples.push(sample);
              // Normal rows consume propagation; observe completion after the synchronous event dispatch.
              queueMicrotask(() => { sample.handlerEndAt = now(); });
              if (sample.body && document.activeElement === terminals.get(session)?.term.textarea) sample.focusAt = now();
              if (!sample.releaseActivation) observeHighlight(sample);
            };
            const focused = event => {
              const sample = probe.samples.at(-1);
              if (sample && event.target === terminals.get(sample.session)?.term.textarea) sample.focusAt ??= now();
            };
            const released = event => {
              const sample = probe.samples.at(-1);
              if (!sample) return;
              sample.releasedAt = now();
              if (sample.releaseActivation) { sample.activationAt = event.timeStamp; observeHighlight(sample); }
              Promise.resolve().then(() => {
                sample.focusAt ??= document.activeElement === terminals.get(sample.session)?.term.textarea ? now() : null;
              });
            };
            const key = event => {
              if (event.key !== 'a') return;
              const sample = probe.samples.at(-1), count = (probe.echoes.get(sample.session) || 0) + 1;
              probe.echoes.set(sample.session, count);
              sample.keyAt = event.timeStamp; sample.keyTrusted = event.isTrusted;
              sample.marker = `<E:${String(count).padStart(4, '0')}>`;
            };
            document.addEventListener('pointerdown', pointer, true);
            document.addEventListener('pointerup', released, true);
            document.addEventListener('focusin', focused, true);
            document.addEventListener('keydown', key, true);
            probe.dispose = () => {
              document.removeEventListener('pointerdown', pointer, true);
              document.removeEventListener('pointerup', released, true);
              document.removeEventListener('focusin', focused, true);
              document.removeEventListener('keydown', key, true);
              probe.disposables.forEach(d => d.dispose()); probe.originals.forEach(restore => restore());
            };
        """, sessions, mode)
        if profile:
            browser.execute_cdp_cmd("Profiler.enable", {})
            browser.execute_cdp_cmd("Profiler.start", {})
        expected_normal_selections = [first] if mode == "tabber" else []
        for index in range(40):
            session = (second, third, first)[(index // 2) % 3] if mode == "tabber" else (second if (index // 2) % 2 == 0 else first)
            selector = (f'#term-{session} .xterm-screen' if index % 2 else
                        f'#panel-__tabber__ .file-tree-row[data-tabber-type="normal"][data-tabber-session="{session}"]' if mode == 'tabber' else
                        f'.dockview-pane-tab[data-pane-tab="{session}"]')
            click_visible_selector(browser, selector)
            if mode == "tabber" and index % 2 == 0:
                expected_normal_selections = [] if session in expected_normal_selections else [session]
            _send_native_key(browser, "a")
            try:
                WebDriverWait(browser, 5, poll_frequency=0.01).until(lambda driver: driver.execute_script(
                    "return Boolean(window.__activationProbe.samples.at(-1)?.echoAt);"))
            except TimeoutException as error:
                state = browser.execute_script("""
                  const sample = window.__activationProbe.samples.at(-1), item = sample && terminals.get(sample.session);
                  const buffer = item?.term?.buffer?.active;
                  return {sample, focusedTerminal, focusedPanelItem, activeItems: activePaneItems(),
                    activeElement: document.activeElement?.className || document.activeElement?.id || document.activeElement?.tagName,
                    terminalText: item && Array.from({length:item.term.rows}, (_, index) => buffer.getLine(buffer.viewportY + index)?.translateToString(true) || '').join('\\n')};
                """)
                raise AssertionError(f"Tabber activation did not produce the expected terminal echo: {state}") from error
            if mode == "tabber":
                layout_state = browser.execute_script("""
                  return {selected: [...(tabberNormalSelections || [])], paneSessions: [...new Set(paneItems().filter(isTmuxSession))],
                    activeSessions: activePaneItems().filter(isTmuxSession)};
                """)
                assert layout_state["selected"] == expected_normal_selections, layout_state
                assert len(layout_state["paneSessions"]) <= 1, layout_state
                assert set(layout_state["activeSessions"]) == set(expected_normal_selections), layout_state
            # Cross the ownership scheduler boundary, rather than a fixed sleep.
            browser.execute_async_script("const done=arguments[arguments.length-1]; requestAnimationFrame(() => requestAnimationFrame(() => done(true)));")
        if profile:
            cpu_profile = browser.execute_cdp_cmd("Profiler.stop", {})["profile"]
            profile_path = latency_calibration.write_latency_evidence(
                nodeid=f"go-activation-{mode}", label="activation-cpu", payload={"profile": cpu_profile})
            nodes = {node["id"]: node["callFrame"]["functionName"] for node in cpu_profile["nodes"]}
            durations = {}
            for node_id, duration in zip(cpu_profile["samples"], cpu_profile["timeDeltas"]):
                name = nodes[node_id]
                durations[name] = durations.get(name, 0) + duration / 1000
            print({"activation_cpu_ms": sorted(durations.items(), key=lambda entry: entry[1], reverse=True)[:30], "profile": str(profile_path)})
        samples = browser.execute_script("return window.__activationProbe.samples;")
        highlight_failures = [
            {key: sample.get(key) for key in ("session", "body", "previousHighlights", "highlightReadyAt", "highlightAt", "focusAt")}
            for sample in samples if sample.get("highlightAt") is None
        ]
        assert len(samples) == 40, samples
        assert all(s["trusted"] and s["keyTrusted"] and s["focusAt"] is not None for s in samples), samples
        assert not highlight_failures, highlight_failures
        tab_samples = [s for s in samples if not s["body"]]
        body_samples = [s for s in samples if s["body"]]
        counts = {"tab": len(tab_samples), "body": len(body_samples)}
        assert counts == {"tab": 20, "body": 20}, counts
        assert all(s.get("paintAt") for s in tab_samples), tab_samples
        metadata_tab_strip_refreshes = browser.execute_script("return window.__activationProbe.metadataTabStripRefreshes;")
        metadata_tabber_tree_refreshes = browser.execute_script("return window.__activationProbe.metadataTabberTreeRefreshes;")
        if mode == "tabber":
            assert all(not refresh["activationPending"] or refresh["elapsedMs"] < 2 for refresh in metadata_tab_strip_refreshes), metadata_tab_strip_refreshes
            assert all(not refresh["activationPending"] or refresh["elapsedMs"] < 2 for refresh in metadata_tabber_tree_refreshes), metadata_tabber_tree_refreshes
            for refreshes in (metadata_tab_strip_refreshes, metadata_tabber_tree_refreshes):
                deferred = [refresh for refresh in refreshes if refresh["activationPending"]]
                flushed = [refresh for refresh in refreshes if not refresh["activationPending"]]
                assert not deferred or any(refresh["at"] > deferred[0]["at"] for refresh in flushed), refreshes
        metrics = (("queue", samples, "eventAt", "handlerAt"), ("handler", samples, "handlerAt", "handlerEndAt"),
                   ("highlight", tab_samples, "activationAt", "highlightAt"),
                   ("highlightReady", tab_samples, "activationAt", "highlightReadyAt"), ("focus", tab_samples, "activationAt", "focusAt"),
                   ("input", samples, "keyAt", "sentAt"), ("echo", tab_samples, "keyAt", "echoAt"),
                   ("bodyHighlight", body_samples, "activationAt", "highlightAt"), ("bodyFocus", body_samples, "activationAt", "focusAt"),
                   ("bodyEcho", body_samples, "keyAt", "echoAt"), ("paint", tab_samples, "activationAt", "paintAt"))
        delays = {name: [s[end] - s[start] for s in population] for name, population, start, end in metrics}
        assert all(delay >= 0 for values in delays.values() for delay in values), delays
        summary = {name: _nearest_rank_ms(values, 0.95) for name, values in delays.items()}
        maxima = {name: max(values) for name, values in delays.items()}
        trace_path = latency_calibration.write_latency_evidence(
            nodeid=f"go-activation-{mode}", label=f"activation-{mode}",
            payload={"samples": samples, "summary": summary, "maxima": maxima, "counts": counts, "cold_navigation_and_terminals_ready_seconds": cold_connection_seconds, "cold_warmup_seconds": cold_seconds, "metadata_tab_strip_refreshes": metadata_tab_strip_refreshes, "metadata_tabber_tree_refreshes": metadata_tabber_tree_refreshes})
        print({"go_activation_p95_ms": summary, "maxima": maxima, "counts": counts, "mode": mode, "cold_navigation_and_terminals_ready_seconds": cold_connection_seconds, "cold_warmup_seconds": cold_seconds, "metadata_tab_strip_refreshes": metadata_tab_strip_refreshes, "metadata_tabber_tree_refreshes": metadata_tabber_tree_refreshes, "samples": samples, "trace": str(trace_path)})
        assert all(s["resizes"] == 1 for s in samples), samples
        assert all(s["refreshes"] == 0 for s in samples if s["body"]), samples
        assert all(s["refreshes"] <= 1 for s in samples if not s["body"]), samples
        assert browser.execute_script("return focusedTerminal === arguments[0] && document.activeElement === terminals.get(arguments[0]).term.textarea;", session)
        return summary
    finally:
        if profile:
            browser.execute_cdp_cmd("Profiler.disable", {})
        browser.execute_script("if (typeof window.__activationProbe?.dispose === 'function') window.__activationProbe.dispose(); window.__activationRestoreFetch?.();")
        browser.get("about:blank")


@pytest.mark.parametrize("mode", ["plain", "split", "tabber", "busy"])
@pytest.mark.browser
def test_go_tab_and_body_activation_coalesces_work_and_preserves_native_echo(browser, isolated_dev_server_factory, mode):
    _run_go_tab_and_body_activation(browser, isolated_dev_server_factory, mode, profile=mode in ("plain", "tabber"))


@pytest.mark.browser
def test_go_manual_refresh_notifies_remote_terminal_size(browser, isolated_dev_server_factory):
    runtime = isolated_dev_server_factory("manual-refresh-size", session_count=1)
    session = runtime.tmux.sessions[0]
    try:
        browser.get(f"{runtime.base_url}/?{urlencode({'sessions': session, 'layout': 'left'})}")
        assert_live_runtime_boot_healthy(browser, "go-manual-refresh-size", timeout=20)
        WebDriverWait(browser, 20).until(lambda driver: driver.execute_script(
            "const item=terminals.get(arguments[0]); return item?.terminalOutputSeen && item.socket.readyState === WebSocket.OPEN;", session
        ))
        initial = browser.execute_script(
            "const item=terminals.get(arguments[0]); return {cols:item.term.cols, rows:item.term.rows, height:item.container.clientHeight};", session
        )
        browser.execute_script("""
          const item = terminals.get(arguments[0]);
          const send = item.socket.send.bind(item.socket);
          window.__manualRefreshResizeMessages = [];
          window.__manualRefreshStarted = false;
          let changedHeightAfterImmediateClaim = false;
          item.socket.send = payload => {
            const message = JSON.parse(payload);
            if (message.type === 'resize') window.__manualRefreshResizeMessages.push(message);
            const result = send(payload);
            if (window.__manualRefreshStarted && !changedHeightAfterImmediateClaim
                && message.type === 'resize' && message.activate === true) {
              changedHeightAfterImmediateClaim = true;
              item.container.style.height = `${Math.max(180, Math.floor(item.container.clientHeight / 2))}px`;
            }
            return result;
          };
        """, session)

        browser.execute_script("window.__manualRefreshStarted = true;")
        click_visible_selector(browser, "#refreshMeta")
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script(
            "return window.__manualRefreshResizeMessages.some(message => message.type === 'resize' && message.activate === true && message.rows < arguments[0]);",
            initial["rows"],
        ))
        messages = browser.execute_script("return window.__manualRefreshResizeMessages;")
        assert any(message.get("activate") is True and message["cols"] == initial["cols"] and message["rows"] < initial["rows"] for message in messages), {
            "initial": initial,
            "messages": messages,
        }
    finally:
        browser.get("about:blank")


@pytest.mark.browser
def test_go_activation_certification_meets_highlight_paint_and_echo_budgets(certification_phase_only, request, browser, isolated_dev_server_factory):
    calibration = latency_calibration.run_browser_latency_calibration(browser)
    summaries = {mode: _run_go_tab_and_body_activation(browser, isolated_dev_server_factory, mode)
                 for mode in ("plain", "split", "tabber", "busy")}
    verdicts = [latency_calibration.fixed_ceiling_verdict(
        label=f"{mode} activation {name}", raw_measured_ms=summary[name], ceiling_ms=ceiling, statistic="p95")
        for mode, summary in summaries.items()
        for name, ceiling in (("highlight", 50), ("focus", 50), ("paint", 100), ("echo", 100),
                              ("bodyHighlight", 50), ("bodyFocus", 50), ("bodyEcho", 100))]
    latency_calibration.certify_verdicts(
        nodeid=request.node.nodeid, label="go-activation", verdicts=verdicts,
        qualification=latency_calibration.merged_qualification(
            certification_phase_only, latency_calibration.browser_calibration_qualification(calibration)),
        extra_evidence={"summaries": summaries, "browser_calibration": calibration})


@pytest.mark.browser
def test_go_competing_clients_and_rapid_switch_preserve_draft_viewport_and_panel(browser, isolated_dev_server_factory):
    runtime = isolated_dev_server_factory("competing-activation", session_count=2)
    first, second = runtime.tmux.sessions
    query = urlencode({"sessions": f"{first},{second}", "layout": "left", "tabs": f"left:{first},{second}"})
    def select_tab(session):
        # Async shell notifications can cover the header after the initial boot dismissal.
        for close in browser.find_elements("css selector", "[data-toast-close]"):
            if close.is_displayed():
                close.click()
        click_visible_selector(browser, f'.dockview-pane-tab[data-pane-tab="{session}"]')

    primary = browser.current_window_handle
    browser.get(f"{runtime.base_url}/?{query}")
    try:
        assert_live_runtime_boot_healthy(browser, "go-competing", timeout=20)
        browser.execute_script("""
          const sessions = Array.from(arguments);
          setTabberMode('advanced');
          void applyLayoutSlots(layoutSlotsForItems(sessions, 'split'), {prune:false});
        """, first, second)
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
            "return tabberIsAdvanced() && [arguments[0], arguments[1]].every(session => document.querySelector(`.dockview-pane-tab[data-pane-tab=\"${CSS.escape(session)}\"]`));",
            first, second))
        for session in (second, first):
            select_tab(session)
            WebDriverWait(browser, 20).until(lambda driver: driver.execute_script(
                "return terminals.get(arguments[0])?.terminalOutputSeen && terminals.get(arguments[0]).socket.readyState === WebSocket.OPEN;", session))
        browser.execute_script("window.__activationOriginalItem=terminals.get(arguments[0]); window.__activationOriginalPanel=panelNodes.get(arguments[0]);", first)
        # A real shell draft survives switches; submitting it proves the text reached tmux.
        draft = "printf 'activation-draft-preserved\\n'"
        browser.switch_to.active_element.send_keys(draft)
        for session in (second, first, second, first):
            select_tab(session)
        browser.switch_to.active_element.send_keys("\n")
        WebDriverWait(browser, 10).until(lambda _driver: "activation-draft-preserved" in run_isolated_tmux(
            runtime.tmux, "capture-pane", "-p", "-t", f"{first}:").stdout.splitlines())
        seeded = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{first}:", "seq 1 100", "Enter")
        assert seeded.returncode == 0, seeded.stderr
        WebDriverWait(browser, 10).until(lambda _driver: "100" in run_isolated_tmux(
            runtime.tmux, "capture-pane", "-p", "-t", f"{first}:").stdout.splitlines())
        # tmux owns history in its alternate screen; scroll its real copy-mode viewport.
        before_scroll = run_isolated_tmux(runtime.tmux, "capture-pane", "-p", "-t", f"{first}:").stdout
        for args in (("copy-mode", "-t", f"{first}:"), ("send-keys", "-t", f"{first}:", "-X", "history-top")):
            result = run_isolated_tmux(runtime.tmux, *args)
            assert result.returncode == 0, result.stderr
        assert run_isolated_tmux(runtime.tmux, "capture-pane", "-M", "-p", "-t", f"{first}:").stdout != before_scroll
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
            "const term=terminals.get(arguments[0]).term; return Array.from({length:term.rows},(_,i)=>term.buffer.active.getLine(i)?.translateToString()).join('\\n').includes('activation-draft-preserved');", first))
        browser.execute_script("const term=terminals.get(arguments[0]).term; window.__activationScroll=term.buffer.active.viewportY; window.__activationScreen=Array.from({length:term.rows},(_,i)=>term.buffer.active.getLine(term.buffer.active.viewportY+i)?.translateToString()).join('\\n');", first)
        for session in (second, first):
            select_tab(session)
        assert browser.execute_script("return terminals.get(arguments[0]) === window.__activationOriginalItem && panelNodes.get(arguments[0]) === window.__activationOriginalPanel && terminals.get(arguments[0]).term.buffer.active.viewportY === window.__activationScroll;", first)
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("const term=terminals.get(arguments[0]).term; return Array.from({length:term.rows-1},(_,i)=>term.buffer.active.getLine(term.buffer.active.viewportY+i)?.translateToString()).join('\\n') === window.__activationScreen.split('\\n').slice(0,-1).join('\\n');", first))
        result = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{first}:", "-X", "cancel")
        assert result.returncode == 0, result.stderr
        browser.switch_to.new_window("tab")
        competitor = browser.current_window_handle
        browser.get(f"{runtime.base_url}/?{urlencode({'sessions': first, 'layout': 'left', 'tabs': f'left:{first}'})}")
        assert_live_runtime_boot_healthy(browser, "go-competing-second", timeout=20)
        WebDriverWait(browser, 20).until(lambda driver: driver.execute_script("return terminals.get(arguments[0])?.terminalOutputSeen;", first))
        # Two independent documents own two tmux clients; the second claims a larger viewport.
        browser.execute_script("const item=terminals.get(arguments[0]); item.container.style.width='600px'; fitTerminal(arguments[0],{claim:true});", first)
        def window_dimensions():
            result = run_isolated_tmux(runtime.tmux, "display-message", "-p", "-t", f"{first}:", "#{window_width}")
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()
        competitor_dimensions = browser.execute_script("return String(terminals.get(arguments[0]).term.cols);", first)
        WebDriverWait(browser, 10).until(lambda _driver: window_dimensions() == competitor_dimensions)
        browser.switch_to.window(primary)
        primary_dimensions = browser.execute_script("return String(terminals.get(arguments[0]).term.cols);", first)
        assert primary_dimensions != competitor_dimensions, (primary_dimensions, competitor_dimensions)
        click_visible_selector(browser, f'#term-{first} .xterm-screen')
        WebDriverWait(browser, 10).until(lambda _driver: window_dimensions() == primary_dimensions)
        # Reconnect uses the existing terminal and panel, then recovers remote ownership again.
        browser.execute_script("window.__activationOriginalSocket=terminals.get(arguments[0]).socket; terminals.get(arguments[0]).socket.close();", first)
        WebDriverWait(browser, 15).until(lambda driver: driver.execute_script("const item=terminals.get(arguments[0]); return item.socket !== window.__activationOriginalSocket && item.socket.readyState === WebSocket.OPEN;", first))
        click_visible_selector(browser, f'#term-{first} .xterm-screen')
        WebDriverWait(browser, 10).until(lambda _driver: window_dimensions() == primary_dimensions)
        assert browser.execute_script("return terminals.get(arguments[0]) === window.__activationOriginalItem && panelNodes.get(arguments[0]) === window.__activationOriginalPanel && document.activeElement === terminals.get(arguments[0]).term.textarea;", first)
        # Hold a real xterm parse completion across replacement; it must not repaint the retired item.
        browser.execute_script("""
          const session=arguments[0], old=terminals.get(session), write=old.term.write.bind(old.term);
          window.__retiredActivationCallbacks=[]; window.__retiredActivationCallbackRan=false; window.__retiredActivationFenceCaptured=false;
          old.term.write=(data, callback)=>write(data, () => {
            if (callback) {
              window.__retiredActivationCallbacks.push(callback);
              if (data === '') window.__retiredActivationFenceCaptured=true;
            }
          });
          writeTerminalOutput(session, old, '\u001b[0m');
          refreshTerminal(session, {afterParse:()=>{window.__retiredActivationCallbackRan=true;}});
        """, first)
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return window.__retiredActivationFenceCaptured;"))
        browser.execute_script("closeTerminalItem(arguments[0], terminals.get(arguments[0])); startTerminal(arguments[0]); window.__retiredActivationCallbacks.splice(0).forEach(callback=>callback());", first)
        WebDriverWait(browser, 20).until(lambda driver: driver.execute_script("const item=terminals.get(arguments[0]); return item !== window.__activationOriginalItem && item?.terminalOutputSeen && item.socket.readyState === WebSocket.OPEN;", first))
        click_visible_selector(browser, f'#term-{first} .xterm-screen')
        assert browser.execute_script("return !window.__retiredActivationCallbackRan && panelNodes.get(arguments[0]) === window.__activationOriginalPanel && document.activeElement === terminals.get(arguments[0]).term.textarea;", first)
        browser.switch_to.active_element.send_keys("printf 'replacement-input-preserved\\n'\n")
        WebDriverWait(browser, 10).until(lambda _driver: "replacement-input-preserved" in run_isolated_tmux(
            runtime.tmux, "capture-pane", "-p", "-t", f"{first}:").stdout.splitlines())
        browser.switch_to.window(competitor)
        browser.close()
        browser.switch_to.window(primary)
    finally:
        for handle in list(browser.window_handles):
            browser.switch_to.window(handle)
            browser.get("about:blank")
        browser.switch_to.window(primary)
