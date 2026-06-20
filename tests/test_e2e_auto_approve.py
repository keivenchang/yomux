"""End-to-end auto-approve tests.

Unlike the fast unit/fixture tests in `test_auto_approve_detector.py` (which feed hand-built or captured
prompt text straight into `prompt_detector`), these launch a REAL `mock_*.py` agent in an isolated tmux
session and the REAL `TmuxWebtermApp` + `AutoApproveWorker`, then assert YO auto-approves a `yesno`
sequence HANDS-FREE — the full tmux-capture -> prompt_detector -> yolo_rules -> keystroke-send path that
a unit test cannot exercise. This is the regression that catches "the detector is right but YO still
does not approve in the running server".

Marked `e2e` (own parallel `pytest-e2e` lane in tools/check.py) and `socket` (self-skips when the sandbox
blocks local sockets/tmux).
"""

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

import yolomux_lib.auto_approve_worker as auto_approve_worker_module
import yolomux_lib.app as app_module
import yolomux_lib.common as common
import yolomux_lib.control as control_module
import yolomux_lib.yoagent.conversation as yoagent_conversation_module
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.tmux_utils import YOLOMUX_TMUX_SOCKET_ENV

pytestmark = [pytest.mark.e2e, pytest.mark.socket]

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tmux(socket_path, *args, timeout=8):
    return subprocess.run(
        ["tmux", "-S", str(socket_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _capture(socket_path, session):
    return _tmux(socket_path, "capture-pane", "-p", "-t", f"{session}:").stdout or ""


def _wait_until(socket_path, session, predicate, timeout):
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _capture(socket_path, session)
        if predicate(last):
            return True, last
        time.sleep(0.4)
    return False, last


def _stop_app(app):
    if app is None:
        return
    stop = getattr(getattr(app, "control_server", None), "stop", None)
    if callable(stop):
        try:
            stop()
        except Exception:
            pass


def _isolate_state(monkeypatch, tmp_path, control_dir):
    # Per-test STATE_DIR-derived dirs so the App's control server + the worker's process lock never
    # collide under `-n auto`. Mirrors the browser harness's isolate_browser_runtime_paths (subset).
    # control_dir MUST be a SHORT path (it holds an AF_UNIX socket, ~108 char limit) — pytest tmp_path
    # is too long — so the caller passes a short /tmp dir; lock files tolerate the long tmp_path.
    state_dir = tmp_path / "state"
    lock_dir = state_dir / "locks"
    for d in (state_dir, lock_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(common, "STATE_DIR", state_dir)
    monkeypatch.setattr(common, "CONTROL_SOCKET_DIR", control_dir)
    monkeypatch.setattr(common, "AUTO_APPROVE_LOCK_DIR", lock_dir)
    monkeypatch.setattr(control_module, "CONTROL_SOCKET_DIR", control_dir)
    monkeypatch.setattr(auto_approve_worker_module, "AUTO_APPROVE_LOCK_DIR", lock_dir)
    monkeypatch.setattr(yoagent_conversation_module, "YOAGENT_CONVERSATION_PATH", state_dir / "yoagent" / "conversation.jsonl")
    monkeypatch.setattr(yoagent_conversation_module, "YOAGENT_CLI_STATE_PATH", state_dir / "yoagent" / "cli-sessions.json")


@pytest.mark.parametrize("mock_name,steps", [("mock_claude.py", 3), ("mock_codex.py", 2)])
def test_e2e_mock_prompt_reaches_structured_ask_payload(monkeypatch, tmp_path, mock_name, steps):
    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")
    sock_base = Path("/tmp") / f"yoask-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    control_dir = sock_base / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    _isolate_state(monkeypatch, tmp_path, control_dir)

    socket_path = sock_base / "s"
    session = f"ya-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))

    created = _tmux(
        socket_path, "new-session", "-d", "-s", session, "-x", "120", "-y", "40",
        f"cd {REPO_ROOT} && exec python3 tools/{mock_name}",
    )
    assert created.returncode == 0, f"tmux new-session failed: {created.stderr or created.stdout}"

    app = None
    try:
        booted, pane = _wait_until(socket_path, session, lambda t: "❯" in t or "›" in t, 20)
        assert booted, f"{mock_name} did not boot to an input prompt:\n{pane}"
        _tmux(socket_path, "send-keys", "-t", f"{session}:", f"yesno {steps}", "Enter")
        prompted, pane = _wait_until(
            socket_path, session,
            lambda t: "do you want to proceed" in t.lower() or "run the following command" in t.lower(),
            20,
        )
        assert prompted, f"{mock_name} did not render a permission prompt after `yesno {steps}`:\n{pane}"

        app = TmuxWebtermApp([session], dangerously_yolo=False)
        payload = app.auto_approve_session_status(session, capture_bare_session_when_roster=True)
        assert payload["prompt"]["visible"] is True
        assert payload["screen"]["key"] == "approval"
        assert payload["prompt"]["signature"]
        assert payload["prompt"]["prompt_kind"] in {"shell-command", "question"}
        assert payload["prompt"]["question_text"]
        assert payload["prompt"]["selected_option"] == 1
    finally:
        _stop_app(app)
        _tmux(socket_path, "kill-server")
        shutil.rmtree(sock_base, ignore_errors=True)


def test_e2e_mock_codex_sleep_10_prompt_reaches_structured_ask_payload(monkeypatch, tmp_path):
    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")
    sock_base = Path("/tmp") / f"yoask-sleep-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    control_dir = sock_base / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    _isolate_state(monkeypatch, tmp_path, control_dir)

    socket_path = sock_base / "s"
    session = f"yc-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))

    created = _tmux(
        socket_path, "new-session", "-d", "-s", session, "-x", "120", "-y", "40",
        f"cd {REPO_ROOT} && exec python3 tools/mock_codex.py",
    )
    assert created.returncode == 0, f"tmux new-session failed: {created.stderr or created.stdout}"

    app = None
    try:
        booted, pane = _wait_until(socket_path, session, lambda t: "›" in t, 20)
        assert booted, f"mock_codex.py did not boot to an input prompt:\n{pane}"
        _tmux(socket_path, "send-keys", "-t", f"{session}:", "sleep 10", "Enter")
        prompted, pane = _wait_until(
            socket_path, session,
            lambda t: "Would you like to run the following command?" in t and "$ sleep 10" in t,
            20,
        )
        assert prompted, f"mock_codex.py did not render the exact sleep 10 approval prompt:\n{pane}"

        app = TmuxWebtermApp([session], dangerously_yolo=False)
        payload = app.auto_approve_session_status(session, capture_bare_session_when_roster=True)
        assert payload["prompt"]["visible"] is True
        assert payload["screen"]["key"] == "approval"
        assert payload["prompt"]["agent"] == "codex"
        assert payload["prompt"]["prompt_kind"] == "shell-command"
        assert payload["prompt"]["question_text"] == "Would you like to run the following command?"
        assert payload["prompt"]["command"] == "sleep 10"
        assert payload["prompt"]["selected_option"] == 1
        assert payload["prompt"]["yes_selected"] is True
        assert payload["prompt"]["signature"]
    finally:
        _stop_app(app)
        _tmux(socket_path, "kill-server")
        shutil.rmtree(sock_base, ignore_errors=True)


def test_e2e_yoagent_mock_sends_capture_multiple_results(monkeypatch, tmp_path):
    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")
    sock_base = Path("/tmp") / f"yoyae2e-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    control_dir = sock_base / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    _isolate_state(monkeypatch, tmp_path, control_dir)

    socket_path = sock_base / "s"
    sessions = {
        "claude": f"ymc-{os.getpid()}-{uuid.uuid4().hex[:6]}",
        "codex": f"ymx-{os.getpid()}-{uuid.uuid4().hex[:6]}",
    }
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))

    for agent, session in sessions.items():
        created = _tmux(
            socket_path, "new-session", "-d", "-s", session, "-x", "120", "-y", "40",
            f"cd {REPO_ROOT} && exec python3 tools/mock_{agent}.py",
        )
        assert created.returncode == 0, f"tmux new-session failed for {agent}: {created.stderr or created.stdout}"

    app = None
    try:
        for agent, session in sessions.items():
            booted, pane = _wait_until(socket_path, session, lambda t: "❯" in t or "›" in t, 20)
            assert booted, f"mock_{agent}.py did not boot to an input prompt:\n{pane}"

        app = TmuxWebtermApp(list(sessions.values()), dangerously_yolo=False)
        sent: dict[str, tuple[dict, dict]] = {}
        for agent, session in sessions.items():
            preview, preview_status = app.create_yoagent_action_preview({
                "type": "send_prompt",
                "session": session,
                "text": "date",
                "submit": True,
                "return_result": True,
            })
            assert preview_status == 200
            assert preview["status"] == "ready", preview
            assert preview["target"]["agent_kind"] == agent
            result, result_status = app.execute_yoagent_send_action(
                {"preview_id": preview["id"]},
                persist_result=True,
                start_result_watch=False,
            )
            assert result_status == 200, result
            assert result["sent"] is True
            app.register_yoagent_action_wait(f"wait-{agent}", preview, result["result_marker"])
            sent[agent] = (preview, result["result_marker"])

        waiting = app.yoagent_conversation_payload()["pending_waits"]
        assert {item["id"] for item in waiting} == {"wait-claude", "wait-codex"}
        assert {item["session"] for item in waiting} == set(sessions.values())

        for agent, session in sessions.items():
            prompted, pane = _wait_until(
                socket_path,
                session,
                lambda t: "Do you want to proceed?" in t or "Would you like to run the following command?" in t,
                20,
            )
            assert prompted, f"mock_{agent}.py did not render a permission prompt for date:\n{pane}"
            _tmux(socket_path, "send-keys", "-t", f"{session}:", "1")
            completed, pane = _wait_until(socket_path, session, lambda t: "Bash(date)" in t, 20)
            assert completed, f"mock_{agent}.py did not show date output after approval:\n{pane}"

        for agent, (preview, marker) in sent.items():
            result = app.run_yoagent_action_result_watcher(
                preview,
                marker,
                watch_id=f"wait-{agent}",
                wait_seconds=1,
                poll_seconds=0.1,
            )
            assert result["ok"] is True, result

        conversation = app.yoagent_conversation_payload()
        assert conversation["pending_waits"] == []
        result_messages = [message for message in conversation["messages"] if message.get("kind") == "agent_result"]
        assert len(result_messages) >= 2
        for session in sessions.values():
            matching = [message["content"] for message in result_messages if message.get("session") == session]
            assert matching, f"missing YO!agent result for {session}: {result_messages}"
            assert "Bash(date)" in matching[-1]
    finally:
        _stop_app(app)
        _tmux(socket_path, "kill-server")
        shutil.rmtree(sock_base, ignore_errors=True)


@pytest.mark.parametrize("mock_name,steps", [("mock_claude.py", 3), ("mock_codex.py", 2)])
def test_e2e_yo_auto_approves_mock_yesno(monkeypatch, tmp_path, mock_name, steps):
    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")
    # AF_UNIX sockets (tmux + the App control server) cap the path at ~108 chars, so keep them under a
    # SHORT /tmp dir rather than the long pytest tmp_path.
    sock_base = Path("/tmp") / f"yoe2e-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    control_dir = sock_base / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    _isolate_state(monkeypatch, tmp_path, control_dir)

    socket_path = sock_base / "s"
    session = f"yt-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))

    created = _tmux(
        socket_path, "new-session", "-d", "-s", session, "-x", "120", "-y", "40",
        f"cd {REPO_ROOT} && exec python3 tools/{mock_name}",
    )
    assert created.returncode == 0, f"tmux new-session failed: {created.stderr or created.stdout}"

    app = None
    worker = None
    try:
        # Wait for the mock to reach its input prompt (real Claude renders ❯, real Codex ›), then drive
        # the queued Yes/No sequence.
        booted, pane = _wait_until(socket_path, session, lambda t: "❯" in t or "›" in t, 20)
        assert booted, f"{mock_name} did not boot to an input prompt:\n{pane}"
        _tmux(socket_path, "send-keys", "-t", f"{session}:", f"yesno {steps}", "Enter")
        prompted, pane = _wait_until(
            socket_path, session,
            lambda t: "do you want to proceed" in t.lower() or "run the following command" in t.lower(),
            20,
        )
        assert prompted, f"{mock_name} did not render a permission prompt after `yesno {steps}`:\n{pane}"

        # Start the REAL app + auto-approve worker. dangerously_yolo=True mirrors a `--dang` server.
        app = TmuxWebtermApp([session], dangerously_yolo=True)
        worker, status = app.start_auto_approve_worker(session, takeover=True)
        assert worker is not None, f"auto-approve worker did not start: {status}"

        # The worker now runs hands-free in its own thread — NO manual keystrokes. Poll for completion.
        completed, pane = _wait_until(socket_path, session, lambda t: "complete" in t.lower(), 60)
        assert completed, (
            f"YO did not auto-approve {mock_name} hands-free; "
            f"approved={worker.approved} blocked={worker.blocked} last_action={worker.last_action!r}\n{pane}"
        )
        assert worker.approved >= steps, (
            f"expected >= {steps} hands-free approvals for {mock_name}, got {worker.approved} "
            f"(blocked={worker.blocked}, last_action={worker.last_action!r})"
        )
    finally:
        if worker is not None:
            worker.stop()
        _stop_app(app)
        _tmux(socket_path, "kill-server")
        shutil.rmtree(sock_base, ignore_errors=True)
