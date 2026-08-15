import os
import subprocess
from pathlib import Path

import pytest

from tools import auto_approve_tmux
from yolomux_lib import tmux_utils


def test_tmux_run_converts_timeout_to_completed_process(monkeypatch):
    def fake_run(args, capture_output, text, encoding, errors, timeout, check):
        assert encoding == "utf-8"
        assert errors == "replace"
        raise subprocess.TimeoutExpired(args, timeout, output="partial", stderr="")

    monkeypatch.setattr(tmux_utils.subprocess, "run", fake_run)

    result = tmux_utils.tmux_run("capture-pane", check=False, timeout=0.01)

    assert result.returncode == 124
    assert result.stdout == "partial"
    assert "timed out after 0.01s" in result.stderr


def test_tmux_run_check_raises_after_timeout(monkeypatch):
    def fake_run(args, capture_output, text, encoding, errors, timeout, check):
        assert encoding == "utf-8"
        assert errors == "replace"
        raise subprocess.TimeoutExpired(args, timeout, output="", stderr="hung")

    monkeypatch.setattr(tmux_utils.subprocess, "run", fake_run)

    try:
        tmux_utils.tmux_run("capture-pane", timeout=0.01)
    except subprocess.CalledProcessError as exc:
        assert exc.returncode == 124
        assert exc.stderr == "hung"
    else:
        raise AssertionError("tmux_run(check=True) should raise on timeout")


def test_run_cmd_decodes_with_replacement_errors(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(tmux_utils.subprocess, "run", fake_run)

    result = tmux_utils.run_cmd(["tmux", "capture-pane"])

    assert result.stdout == "ok"
    assert calls[0]["encoding"] == "utf-8"
    assert calls[0]["errors"] == "replace"


def test_tmux_command_uses_configured_socket(monkeypatch):
    monkeypatch.setenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, "/tmp/yolomux-test-tmux.sock")

    assert tmux_utils.tmux_command(["list-sessions"]) == ["tmux", "-S", "/tmp/yolomux-test-tmux.sock", "list-sessions"]


def test_list_tmux_session_activity_reads_the_roster_in_one_bulk_call(monkeypatch):
    calls = []

    def fake_tmux(args, timeout):
        calls.append((args, timeout))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="active\t123\ncold\t45\ninvalid\tnot-a-timestamp\n",
            stderr="",
        )

    monkeypatch.setattr(tmux_utils, "tmux", fake_tmux)

    activity, error = tmux_utils.list_tmux_session_activity()

    assert activity == {"active": 123, "cold": 45}
    assert error is None
    assert calls == [
        (["list-sessions", "-F", "#{session_name}\t#{session_activity}"], 3.0),
    ]


def test_readonly_control_mode_attach_allows_default_server(monkeypatch):
    monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, raising=False)
    monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV, raising=False)
    argv = ["-C", "attach-session", "-f", "read-only,ignore-size", "-t", "alpha:"]

    assert tmux_utils.tmux_command(argv) == ["tmux", *argv]

    monkeypatch.setenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, "/tmp/declared.sock")
    assert tmux_utils.tmux_command(argv) == ["tmux", "-S", "/tmp/declared.sock", *argv]


@pytest.mark.parametrize(
    ("argv", "verb"),
    [
        (["kill-server"], "kill-server"),
        (["kill-session", "-t", "alpha:"], "kill-session"),
        (["-C", "attach-session", "-t", "alpha:"], "-C attach-session"),
    ],
)
def test_default_server_refuses_destructive_and_writable_control_commands(monkeypatch, argv, verb):
    monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, raising=False)
    monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV, raising=False)

    with pytest.raises(tmux_utils.TmuxSocketTargetError) as raised:
        tmux_utils.tmux_command(argv)

    assert raised.value.verb == verb

    monkeypatch.setenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, "/tmp/declared-private.sock")
    assert tmux_utils.tmux_command(argv) == ["tmux", "-S", "/tmp/declared-private.sock", *argv]


def test_launchers_declare_the_default_server_intent_for_control_mode(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV, raising=False)
    optin = subprocess.run(
        ["bash", "-c", "source tools/startup_common.sh; yolomux_default_server_optin"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert optin == f"{tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV}=1"
    assert f'export PATH="$launch_path"' in subprocess.run(
        ["bash", "-c", "source tools/startup_common.sh; yolomux_macos_server_launcher"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV in subprocess.run(
        ["bash", "-c", "source tools/startup_common.sh; yolomux_macos_server_launcher"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert 'extra_env+=("$(yolomux_default_server_optin)")' in (root / "boot.sh").read_text(encoding="utf-8")


def test_tmux_move_to_option_walks_highlight_without_crashing(monkeypatch):
    # exercise the REAL tmux_move_to_option body (only tmux_run is stubbed). The auto-approve
    # tests fake the whole tmux module, so they never run this loop — which masked a missing `import time`
    # that made every highlight-moving approval (option 2, declines) raise NameError and kill the worker.
    sent = []

    def fake_run(*args, **kwargs):
        sent.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(tmux_utils, "tmux_run", fake_run)
    monkeypatch.setattr(tmux_utils.time, "sleep", lambda *_: None)

    # "%3" is an unambiguous target (no session resolution). option 2 from selected 1 -> one Down press.
    tmux_utils.tmux_move_to_option("%3", 2, 1)

    down_presses = [call for call in sent if call[:2] == ("send-keys", "-t") and call[-1] == "Down"]
    assert len(down_presses) == 1


def test_cached_session_names_memoizes_within_ttl(monkeypatch):
    # tmux_exact_target no longer runs `tmux list-sessions` on every capture — session-name
    # resolution is cached for a short window so a poll's captures reuse one resolution.
    calls = {"n": 0}

    def fake_names():
        calls["n"] += 1
        return ["1", "2"]

    monkeypatch.setattr(tmux_utils, "tmux_session_names", fake_names)
    tmux_utils._session_names_cache.values.clear()  # force one fresh resolution
    for _ in range(5):
        assert tmux_utils.cached_session_names() == ["1", "2"]
    assert calls["n"] == 1


def test_tmux_exact_target_skips_resolution_for_unambiguous_targets(monkeypatch):
    def fail_names():
        raise AssertionError("must not resolve sessions for an unambiguous target")

    monkeypatch.setattr(tmux_utils, "cached_session_names", fail_names)
    assert tmux_utils.tmux_exact_target("%3") == "%3"
    assert tmux_utils.tmux_exact_target("1:") == "1:"


def test_tmux_clear_input_moves_to_end_before_clearing(monkeypatch):
    calls = []

    def fake_tmux_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(tmux_utils, "tmux_run", fake_tmux_run)

    result = tmux_utils.tmux_clear_input("%6")

    assert result.returncode == 0
    assert calls == [
        (("send-keys", "-t", "%6", "C-e", "C-u"), {"check": False, "timeout": 5.0}),
    ]


def test_tmux_paste_text_submits_with_enter_key_not_pasted_newline(monkeypatch):
    calls = []

    def fake_subprocess_run(args, **kwargs):
        calls.append(("subprocess", tuple(args), kwargs.get("input")))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def fake_tmux_run(*args, **_kwargs):
        calls.append(("tmux_run", args, None))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(tmux_utils.secrets, "token_hex", lambda _size: "abc123")
    monkeypatch.setattr(tmux_utils.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(tmux_utils, "tmux_run", fake_tmux_run)

    result = tmux_utils.tmux_paste_text("%6", "date", submit=True)
    private_socket = os.environ[tmux_utils.YOLOMUX_TMUX_SOCKET_ENV]

    assert result.returncode == 0
    assert calls == [
        ("subprocess", ("tmux", "-S", private_socket, "load-buffer", "-b", "yolomux-abc123", "-"), "date"),
        ("tmux_run", ("paste-buffer", "-p", "-t", "%6", "-b", "yolomux-abc123"), None),
        ("tmux_run", ("send-keys", "-t", "%6", "Enter"), None),
        ("tmux_run", ("delete-buffer", "-b", "yolomux-abc123"), None),
    ]


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
