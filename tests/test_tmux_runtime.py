from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import tmux_runtime


def test_adaptive_tmux_poll_interval_keeps_a_fast_observation_window_then_caps():
    intervals = [tmux_runtime.adaptive_tmux_poll_interval(index) for index in range(10)]

    assert intervals[:5] == [0.05] * 5
    assert intervals[5:] == [0.1, 0.2, 0.4, 0.4, 0.4]


def test_wait_for_isolated_tmux_panes_returns_immediately_without_sleep(monkeypatch):
    captures = []
    sleeps = []
    runtime = SimpleNamespace()
    monkeypatch.setattr(tmux_runtime, "capture_isolated_tmux_pane", lambda _runtime, session: captures.append(session) or "ready")

    ready, panes = tmux_runtime.wait_for_isolated_tmux_panes(
        runtime,
        ["one"],
        lambda values: values["one"] == "ready",
        clock=lambda: 0.0,
        sleeper=sleeps.append,
    )

    assert ready is True
    assert panes == {"one": "ready"}
    assert captures == ["one"]
    assert sleeps == []


def test_wait_for_isolated_tmux_panes_adapts_then_caps_and_captures_all_sessions_once_per_pass(monkeypatch):
    now = [0.0]
    sleeps = []
    capture_count = [0]
    passes = [0]
    runtime = SimpleNamespace()

    def capture(_runtime, session):
        capture_count[0] += 1
        if session == "one":
            passes[0] += 1
        return "ready" if passes[0] >= 9 else f"waiting-{session}"

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(tmux_runtime, "capture_isolated_tmux_pane", capture)
    ready, panes = tmux_runtime.wait_for_isolated_tmux_panes(
        runtime,
        ["one", "two"],
        lambda values: values["one"] == values["two"] == "ready",
        timeout=10,
        clock=lambda: now[0],
        sleeper=sleep,
    )

    assert ready is True
    assert panes == {"one": "ready", "two": "ready"}
    assert sleeps == [0.05, 0.05, 0.05, 0.05, 0.05, 0.1, 0.2, 0.4]
    assert capture_count[0] == 18


def test_wait_for_isolated_tmux_panes_honors_a_fixed_interval_and_returns_last_capture_on_timeout(monkeypatch):
    now = [0.0]
    sleeps = []
    captures = []
    runtime = SimpleNamespace()

    def capture(_runtime, session):
        captures.append(session)
        return "still-waiting"

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(tmux_runtime, "capture_isolated_tmux_pane", capture)
    ready, panes = tmux_runtime.wait_for_isolated_tmux_panes(
        runtime,
        ["one"],
        lambda _values: False,
        timeout=0.3,
        poll_interval=0.2,
        clock=lambda: now[0],
        sleeper=sleep,
    )

    assert ready is False
    assert panes == {"one": "still-waiting"}
    assert captures == ["one", "one", "one"]
    assert sleeps == pytest.approx([0.2, 0.1])


def test_e2e_auto_approve_routes_tmux_waits_through_the_selenium_free_shared_owner():
    source = Path(__file__).with_name("test_e2e_auto_approve.py").read_text(encoding="utf-8")

    assert "from tests.tmux_runtime import wait_for_isolated_tmux_panes" in source
    assert "def _wait_until(" not in source
    assert "time.sleep(0.4)" not in source
