"""Regression gate D: tmux actions stay scoped and destructive defaults fail closed."""
from __future__ import annotations

import math
import shlex
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

import pytest

from tests.helpers.browser_contracts import send_native_key as _send_native_key
from tests import latency_calibration
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.browser_helpers.browser_layout import WebDriverWait  # noqa: F401
from tests.browser_helpers.browser_layout import TimeoutException
from tests.browser_helpers.browser_layout import assert_live_runtime_boot_healthy
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import click_visible_selector
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.native_gate_fixtures import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.tmux_runtime import clear_isolated_product_environment
from tests.tmux_runtime import patch_isolated_product_environment
from tests.tmux_runtime import run_isolated_tmux
from yomux_lib.tmux import tmux_utils


pytestmark = pytest.mark.socket

# The user-visible keystroke ceiling. Fixed on every host: a host measurement may decide WHICH
# statistic is certifiable, never how slow the product may be. Asserted only in the exclusive
# certification phase (latency_calibration.LATENCY_CERTIFICATION_ENV), never in the parallel
# browser lane.
KEYSTROKE_LATENCY_CEILING_MS = 3.0
KEYSTROKE_LATENCY_CERTIFIED_QUANTILE = 0.99
KEYSTROKE_LATENCY_CERTIFIED_QUANTILE_KEY = f"p{round(KEYSTROKE_LATENCY_CERTIFIED_QUANTILE * 100)}_nearest_rank_ms"
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
          inputToFirstOutput: counters.inputToFirstOutput || null,
          xtermWrite: counters.xtermWrite || null,
          longTasks: clientPerfLongTaskSummary(),
        };
        """
    )


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


def _counter_total(counter: dict[str, object] | None, field: str) -> int:
    return int(counter[field]) if counter else 0


def _nearest_rank_ms(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


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
        "echo_count": _counter_total(trace["inputToFirstOutput"], "count"),
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
        failures.append(f"socket-echo: the session must answer at least one send while typing; inputToFirstOutput count={observed['echo_count']}")
    if observed["pressure"] and int(observed["pressure_bytes_observed"]) < TERMINAL_PRESSURE_SAMPLING_MIN_BYTES:
        failures.append(
            f"output-pressure: xterm must keep consuming the streaming session during the round; "
            f"expected at least {TERMINAL_PRESSURE_SAMPLING_MIN_BYTES} bytes, observed {observed['pressure_bytes_observed']}"
        )
    return failures


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
            "inputToFirstOutput": {"count": 1},
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


@pytest.mark.parametrize("pressure", [False, True])
@pytest.mark.browser
def test_native_input_diagnostics_and_visible_echo_remain_correlated(browser, isolated_dev_server_factory, pressure):
    runtime = isolated_dev_server_factory("input-diagnostics", session_count=1)
    session = runtime.tmux.sessions[0]
    _start_marker_echo_process(runtime, session, pressure=pressure)
    query = urlencode({"sessions": session, "layout": "left", "tabs": f"left:{session}", "debug": "1"})
    try:
        browser.get(f"{runtime.base_url}/?{query}")
        assert_live_runtime_boot_healthy(browser, "input-diagnostics", timeout=20)
        WebDriverWait(browser, 20).until(lambda driver: driver.execute_script(
            "const item=terminals.get(arguments[0]); return item?.terminalOutputSeen && item.term?.onRender && item.container.querySelector('.xterm-screen');", session))
        browser.execute_script("const item=terminals.get(arguments[0]); setFocusedTerminal(arguments[0]); item.term.focus(); clearClientPerfCounters();", session)
        _install_visible_echo_probe(browser, session)
        # A native modifier must not arm a timer that a focus report can consume.
        browser.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Shift", "code": "ShiftLeft", "windowsVirtualKeyCode": 16})
        browser.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Shift", "code": "ShiftLeft", "windowsVirtualKeyCode": 16})
        browser.execute_script("handleTerminalData(arguments[0], '\\x1b[I');", session)
        assert _client_perf_counter(browser, "keydownToTermData") is None
        for index in range(1, 11):
            _send_native_key(browser, "a")
            counter = _client_perf_counter(browser, "keydownToTermData")
            assert _counter_total(counter, "count") == index, counter
            WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
                "return window.__tabSwitchEchoProbe.samples[arguments[0] - 1]?.paintedAt > 0;", index))
        samples = browser.execute_script("return window.__tabSwitchEchoProbe.samples;")
        assert len(samples) == 10 and all(sample["trusted"] and sample["paintedAt"] >= sample["eventAt"] for sample in samples), samples
    finally:
        browser.execute_script("window.__tabSwitchEchoProbe?.dispose?.();")
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
