import os
import shutil
import subprocess
import uuid
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
        (["kill-session", "-t", "=alpha:"], "kill-session"),
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


def test_tmux_exact_target_reads_no_session_list(monkeypatch):
    # Pinning is pure string work now: the exact `=name:` form is correct whether or not the
    # session is alive, so no capture pays a `tmux list-sessions` subprocess to build a target.
    def fail_names():
        raise AssertionError("must not resolve sessions to build a target")

    monkeypatch.setattr(tmux_utils, "tmux_session_names", fail_names)
    assert tmux_utils.tmux_exact_target("%3") == "%3"
    assert tmux_utils.tmux_exact_target("1:") == "=1:"
    assert tmux_utils.tmux_exact_target("1") == "=1:"


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
    assert auto_approve_tmux.tmux_exact_target_from_sessions("1", ["1", "6", "ant"]) == "=1:"
    assert auto_approve_tmux.tmux_exact_target_from_sessions("%79", ["1", "6", "ant"]) == "%79"


# --- exact-target enforcement -------------------------------------------------
# tmux resolves a bare `name:` target by prefix, so `-t 1:` lands on session `12` the moment
# session `1` is gone or renamed. Sessions here are literally named `1`, `2`, `12`, so a session
# kill aimed at `1:` can destroy `12`. Every destructive target must be the exact `=name:` form.


def _private_socket(monkeypatch, path: str = "/tmp/declared-private.sock") -> None:
    monkeypatch.setenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, path)


def test_session_target_is_an_exact_match_target():
    assert tmux_utils.tmux_session_target("1") == "=1:"
    assert tmux_utils.tmux_session_target("yolomux7771") == "=yolomux7771:"


@pytest.mark.parametrize(
    "target",
    ["alpha:", "alpha", "alpha:0", "=", "=:", "= :", "alpha*", "*", ""],
)
def test_kill_session_refuses_every_target_tmux_could_resolve_loosely(monkeypatch, target):
    _private_socket(monkeypatch)

    with pytest.raises(tmux_utils.TmuxSocketTargetError) as raised:
        tmux_utils.tmux_command(["kill-session", "-t", target])

    assert raised.value.verb == "kill-session"


@pytest.mark.parametrize(
    "argv",
    [
        ["kill-session"],
        ["kill-session", "-a", "-t", "=alpha:"],
        ["kill-session", "-t", "=alpha:", "-t", "=beta:"],
    ],
)
def test_kill_session_refuses_missing_multiple_and_all_but_targets(monkeypatch, argv):
    _private_socket(monkeypatch)

    with pytest.raises(tmux_utils.TmuxSocketTargetError) as raised:
        tmux_utils.tmux_command(argv)

    assert raised.value.verb == "kill-session"


def test_kill_session_accepts_the_exact_target_on_a_private_socket(monkeypatch):
    _private_socket(monkeypatch)

    assert tmux_utils.tmux_command(["kill-session", "-t", tmux_utils.tmux_session_target("alpha")]) == [
        "tmux",
        "-S",
        "/tmp/declared-private.sock",
        "kill-session",
        "-t",
        "=alpha:",
    ]


def test_default_server_kill_session_needs_both_the_optin_and_an_exact_target(monkeypatch):
    monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, raising=False)
    monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV, raising=False)
    exact = ["kill-session", "-t", tmux_utils.tmux_session_target("alpha")]

    with pytest.raises(tmux_utils.TmuxSocketTargetError):
        tmux_utils.tmux_command(exact)

    monkeypatch.setenv(tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV, "1")
    assert tmux_utils.tmux_command(exact) == ["tmux", "kill-session", "-t", "=alpha:"]

    with pytest.raises(tmux_utils.TmuxSocketTargetError) as raised:
        tmux_utils.tmux_command(["kill-session", "-t", "alpha:"])
    assert raised.value.verb == "kill-session"


@pytest.mark.parametrize("optin", [None, "", "0", "true", "yes", "1"])
def test_default_server_kill_server_is_refused_in_every_optin_mode(monkeypatch, optin):
    monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, raising=False)
    if optin is None:
        monkeypatch.delenv(tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV, raising=False)
    else:
        monkeypatch.setenv(tmux_utils.YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER_ENV, optin)

    with pytest.raises(tmux_utils.TmuxSocketTargetError) as raised:
        tmux_utils.tmux_command(["kill-server"])

    assert raised.value.verb == "kill-server"


def test_exact_target_cannot_kill_a_prefix_named_sibling_session(monkeypatch, tmp_path):
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket_path = str(tmp_path / "exact-target.sock")
    sibling = f"yt-{uuid.uuid4().hex[:8]}-sibling"
    prefix = sibling[: sibling.index("-sibling")]
    created = subprocess.run(
        ["tmux", "-S", socket_path, "new-session", "-d", "-s", sibling, "sleep", "300"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    monkeypatch.setenv(tmux_utils.YOLOMUX_TMUX_SOCKET_ENV, socket_path)
    try:
        missed = tmux_utils.tmux(["kill-session", "-t", tmux_utils.tmux_session_target(prefix)])
        assert missed.returncode != 0
        assert tmux_utils.tmux_has_exact_session(sibling)

        killed = tmux_utils.tmux(["kill-session", "-t", tmux_utils.tmux_session_target(sibling)])
        assert killed.returncode == 0, killed.stderr
        assert not tmux_utils.tmux_has_exact_session(sibling)
    finally:
        subprocess.run(["tmux", "-S", socket_path, "kill-server"], capture_output=True, text=True, check=False)


# --- one exact-target owner ---------------------------------------------------
# tmux_exact_target claimed the name but emitted the bare `1:` form, which tmux still resolves
# by prefix onto `12`. Every session target it builds must satisfy tmux_exact_session_target().


def test_tmux_exact_target_emits_the_exact_form_for_every_session_target(monkeypatch):
    monkeypatch.setattr(tmux_utils, "tmux_session_names", lambda: ["1", "12"])

    assert tmux_utils.tmux_exact_target("1") == "=1:"
    assert tmux_utils.tmux_exact_target("1:") == "=1:"
    assert tmux_utils.tmux_exact_target("1:2.0") == "=1:2.0"
    assert tmux_utils.tmux_exact_target("=1:") == "=1:"
    # pane ids, the current-session target and the empty target carry no session name to pin.
    assert tmux_utils.tmux_exact_target("%3") == "%3"
    assert tmux_utils.tmux_exact_target(":") == ":"
    assert tmux_utils.tmux_exact_target("") == ""


def test_tmux_exact_target_output_passes_the_destructive_precision_check(monkeypatch):
    monkeypatch.setattr(tmux_utils, "tmux_session_names", lambda: ["1", "12"])

    for target in ("1", "1:", "12", "12:", "project1:0.1"):
        assert tmux_utils.tmux_exact_session_target(tmux_utils.tmux_exact_target(target)), target


def test_tmux_exact_target_from_sessions_never_emits_a_bare_prefix_target():
    # A session missing from the list used to fall through as a bare name, so tmux prefix-resolved
    # a send-keys aimed at a dead `1` onto the live `12`. An unknown session must fail closed.
    assert tmux_utils.tmux_exact_target_from_sessions("1", ["1", "6", "ant"]) == "=1:"
    assert tmux_utils.tmux_exact_target_from_sessions("1", ["12", "ant"]) == "=1:"
    assert tmux_utils.tmux_exact_target_from_sessions("1:2.0", ["12"]) == "=1:2.0"
    assert tmux_utils.tmux_exact_target_from_sessions("%79", ["1", "6", "ant"]) == "%79"
