"""Regression gate D: tmux actions stay scoped and destructive defaults fail closed."""
from __future__ import annotations

import importlib.util
import math
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
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import load_gate_browser
from tests.gate_harness import run_when_browser_ready
from tests.tmux_runtime import run_isolated_tmux
from yolomux_lib.server import TmuxWebtermHTTPServer
from yolomux_lib.tmux import tmux_utils


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


@dataclass(frozen=True)
class TerminalOutputPressure:
    session: str
    duration_seconds: float


def _rename_harness_available() -> bool:
    if importlib.util.find_spec("tests.gate_harness") is None:
        return False
    return hasattr(gate_harness, "held_rename_roster_probe")


@pytest.mark.xfail(strict=True, reason="F3/D1: gate_harness rename transition probe is not present in v0.6.10.")
def test_d1_repeated_enter_posts_rename_exactly_once():
    assert _rename_harness_available(), "F3 must hold the reply and prove N Enter presses issue one POST /api/rename-session"


@pytest.mark.xfail(strict=True, reason="F3/D2: gate_harness rename transition probe is not present in v0.6.10.")
def test_d2_successful_repeated_enter_rename_never_surfaces_error():
    assert _rename_harness_available(), "F3 must prove a successful repeated-Enter rename leaves no visible error"


@pytest.mark.xfail(strict=True, reason="F3/D3: gate_harness rename transition probe is not present in v0.6.10.")
def test_d3_submit_control_is_visibly_disabled_then_reenabled_on_both_outcomes():
    assert _rename_harness_available(), "F3 must measure computed disabled style before resolution and finally restoration on success and failure"


@pytest.mark.xfail(strict=True, reason="F3/D4: gate_harness lacks the held-old-roster probe on v0.6.10.")
def test_d4_rename_survives_released_stale_roster_without_reversal():
    assert _rename_harness_available(), "F3 must hold old roster, apply rename/new roster, release stale roster, and retain only the new tab/layout/active/roster name"


@pytest.mark.xfail(strict=True, reason="F3/D10: gate_harness held-response rename transition probe is not present in v0.6.10.")
def test_d10_rename_optimistically_changes_tab_before_reply_and_has_bounded_reconciliation():
    assert _rename_harness_available(), "F3 must prove optimistic rename before held reply, rejection rollback, and bounded final reconciliation"


def _tmux(socket_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", "-S", str(socket_path), *args], capture_output=True, text=True, check=False, timeout=5)


def _request(port: int, path: str) -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("POST", path)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_gate_d5_kill_session_only_affects_registered_tmux_socket(monkeypatch, tmp_path, make_tmux_webterm_app, no_control_socket, isolated_yoagent_conversation_state):
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket_one = tmp_path / "socket-one"
    socket_two = tmp_path / "socket-two"
    session = f"yt-{uuid.uuid4().hex[:12]}"
    for socket_path in (socket_one, socket_two):
        created = _tmux(socket_path, "new-session", "-d", "-s", session)
        assert created.returncode == 0, created.stderr or created.stdout
    monkeypatch.setenv("YOLOMUX_TMUX_SOCKET", str(socket_one))
    monkeypatch.setenv("YOLOMUX_TEST_AUTH_BYPASS", "1")
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


def test_gate_d6_destructive_default_server_policy_is_explicit(monkeypatch):
    monkeypatch.delenv("YOLOMUX_TMUX_SOCKET", raising=False)
    monkeypatch.delenv("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER", raising=False)
    with pytest.raises(tmux_utils.TmuxSocketTargetError):
        tmux_utils.tmux_command(["kill-session", "-t", "yt-gate:"])
    with pytest.raises(tmux_utils.TmuxSocketTargetError):
        tmux_utils.tmux_command(["kill-server"])
    monkeypatch.setenv("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER", "1")
    assert tmux_utils.tmux_command(["kill-session", "-t", "yt-gate:"]) == ["tmux", "kill-session", "-t", "yt-gate:"]
    with pytest.raises(tmux_utils.TmuxSocketTargetError):
        tmux_utils.tmux_command(["kill-server"])


def test_gate_d7_kill_session_api_returns_promptly_and_removes_scoped_session(monkeypatch, tmp_path, make_tmux_webterm_app, no_control_socket, isolated_yoagent_conversation_state):
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket_path = tmp_path / "socket"
    session = f"yt-{uuid.uuid4().hex[:12]}"
    created = _tmux(socket_path, "new-session", "-d", "-s", session)
    assert created.returncode == 0, created.stderr or created.stdout
    monkeypatch.setenv("YOLOMUX_TMUX_SOCKET", str(socket_path))
    monkeypatch.setenv("YOLOMUX_TEST_AUTH_BYPASS", "1")
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
    pressure_ready = browser.execute_async_script(
        """
        const minimumBytes = arguments[0];
        const done = arguments[arguments.length - 1];
        window.__yolomuxTestWaitFor(() => {
          const counter = Object.fromEntries(clientPerfSummary().map(item => [item.name, item])).xtermWrite;
          return Number(counter?.bytes || 0) >= minimumBytes;
        }, {timeoutMs: 8000, description: 'terminal output pressure reaches xterm'}).then(() => done(true), error => done({error: String(error)}));
        """,
        TERMINAL_PRESSURE_WARMUP_BYTES,
    )
    assert pressure_ready is True, pressure_ready
    return TerminalOutputPressure(session=session, duration_seconds=duration_seconds)


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


def _counter_total(counter: dict[str, object] | None, field: str) -> int:
    return int(counter[field]) if counter else 0


def _nearest_rank_ms(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def _focus_gate_terminal(browser, runtime) -> str:
    """Load the fixture server and leave the keyboard focus on the first session's xterm textarea."""

    session = runtime.tmux.sessions[0]
    load_gate_browser(browser, runtime)
    run_when_browser_ready(
        browser,
        "return terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN"
        " && Boolean(document.querySelector(`#term-${CSS.escape(arguments[0])} .xterm-screen`));",
        session,
        globals_required={"clientPerfSummary": "function", "clearClientPerfCounters": "function"},
        dom_anchors=("#grid",),
        timeout=8,
    )
    browser.find_element("css selector", f"#term-{session} .xterm-screen").click()
    run_when_browser_ready(
        browser,
        "return document.activeElement === document.querySelector(`#term-${CSS.escape(arguments[0])} textarea`);",
        session,
        globals_required={"clientPerfSummary": "function"},
        dom_anchors=(f"#term-{session} .xterm-screen",),
        timeout=8,
    )
    return session


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
        "pressure_bytes_during_sampling": xterm_bytes_after - xterm_bytes_before,
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
    if observed["pressure"] and int(observed["pressure_bytes_during_sampling"]) < TERMINAL_PRESSURE_SAMPLING_MIN_BYTES:
        failures.append(
            f"output-pressure: xterm must keep consuming the streaming session during sampling; "
            f"expected at least {TERMINAL_PRESSURE_SAMPLING_MIN_BYTES} bytes, observed {observed['pressure_bytes_during_sampling']}"
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
        "pressure_bytes_during_sampling": observed["pressure_bytes_during_sampling"],
        "long_task_count": observed["long_tasks"]["count"],
        "long_task_max_ms": observed["long_tasks"]["maxMs"],
    }


def _assert_keystroke_delivery_complete(observed: dict[str, object]) -> None:
    failures = _keystroke_delivery_failures(observed)
    assert failures == [], {"failures": failures, "summary": _keystroke_summary(observed), "samples": observed["samples"]}


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
        pressure=True,
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
        "pressure_bytes_during_sampling": TERMINAL_PRESSURE_SAMPLING_MIN_BYTES,
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
        ({"pressure_bytes_during_sampling": TERMINAL_PRESSURE_SAMPLING_MIN_BYTES - 1}, "output-pressure"),
    ],
)
def test_s1_delivery_report_names_exactly_the_violated_contract(violation, expected_reason):
    """Every retained contract must be individually detectable, including the ones a browser control cannot isolate."""

    assert _keystroke_delivery_failures(_complete_delivery_observation()) == []
    observed = {**_complete_delivery_observation(), **violation}
    reasons = [failure.split(":", 1)[0] for failure in _keystroke_delivery_failures(observed)]
    assert reasons == [expected_reason], (reasons, observed)


def test_s1_delivery_report_ignores_wall_latency_entirely():
    """The parallel-lane contract must stay a pure delivery contract; latency lives in the exclusive phase."""

    slow = {**_complete_delivery_observation(), "samples": [999.0, 999.0, 999.0, 999.0]}
    assert _keystroke_delivery_failures(slow) == []
    assert max(slow["samples"]) > KEYSTROKE_LATENCY_CEILING_MS


def _certification_round_summary(*, max_ms: float, quantile_ms: float) -> dict[str, object]:
    return {"label": "synthetic", "max_ms": max_ms, KEYSTROKE_LATENCY_CERTIFIED_QUANTILE_KEY: quantile_ms}


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
