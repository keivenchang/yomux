import subprocess

import auto_approve_tmux
from yolomux_lib import tmux_utils


def test_tmux_run_converts_timeout_to_completed_process(monkeypatch):
    def fake_run(args, capture_output, text, timeout, check):
        raise subprocess.TimeoutExpired(args, timeout, output="partial", stderr="")

    monkeypatch.setattr(tmux_utils.subprocess, "run", fake_run)

    result = tmux_utils.tmux_run("capture-pane", check=False, timeout=0.01)

    assert result.returncode == 124
    assert result.stdout == "partial"
    assert "timed out after 0.01s" in result.stderr


def test_tmux_run_check_raises_after_timeout(monkeypatch):
    def fake_run(args, capture_output, text, timeout, check):
        raise subprocess.TimeoutExpired(args, timeout, output="", stderr="hung")

    monkeypatch.setattr(tmux_utils.subprocess, "run", fake_run)

    try:
        tmux_utils.tmux_run("capture-pane", timeout=0.01)
    except subprocess.CalledProcessError as exc:
        assert exc.returncode == 124
        assert exc.stderr == "hung"
    else:
        raise AssertionError("tmux_run(check=True) should raise on timeout")


def test_cached_session_names_memoizes_within_ttl(monkeypatch):
    # DOIT.6 #80: tmux_exact_target no longer runs `tmux list-sessions` on every capture — session-name
    # resolution is cached for a short window so a poll's captures reuse one resolution.
    calls = {"n": 0}

    def fake_names():
        calls["n"] += 1
        return ["1", "2"]

    monkeypatch.setattr(tmux_utils, "tmux_session_names", fake_names)
    tmux_utils._SESSION_NAMES_CACHE["at"] = 0.0  # force one fresh resolution
    for _ in range(5):
        assert tmux_utils.cached_session_names() == ["1", "2"]
    assert calls["n"] == 1


def test_tmux_exact_target_skips_resolution_for_unambiguous_targets(monkeypatch):
    def fail_names():
        raise AssertionError("must not resolve sessions for an unambiguous target")

    monkeypatch.setattr(tmux_utils, "cached_session_names", fail_names)
    assert tmux_utils.tmux_exact_target("%3") == "%3"
    assert tmux_utils.tmux_exact_target("1:") == "1:"


def test_target_resolution_self_test_cases_live_in_pytest():
    sessions = ["project1", "project2", "project3", "misc"]

    assert auto_approve_tmux._resolve_targets_from_sessions(["project1"], sessions) == ["project1"]
    assert auto_approve_tmux._resolve_targets_from_sessions(["project1", "project2,project3"], sessions) == [
        "project1",
        "project2",
        "project3",
    ]
    assert auto_approve_tmux._resolve_targets_from_sessions(["project*"], sessions) == [
        "project1",
        "project2",
        "project3",
    ]
    assert auto_approve_tmux._resolve_targets_from_sessions(["project*:0.1"], sessions) == [
        "project1:0.1",
        "project2:0.1",
        "project3:0.1",
    ]
    assert auto_approve_tmux.specs_have_wildcards(["project1", "project2:0.1"]) is False
    assert auto_approve_tmux.specs_have_wildcards(["project1", "dyn*"]) is True
    assert auto_approve_tmux.tmux_exact_target_from_sessions("1", ["1", "6", "ant"]) == "1:"
    assert auto_approve_tmux.tmux_exact_target_from_sessions("%79", ["1", "6", "ant"]) == "%79"
