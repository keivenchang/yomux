"""End-to-end auto-approve tests.

Unlike the fast unit/fixture tests in `test_auto_approve_detector.py` (which feed hand-built or captured
prompt text straight into `prompt_detector`), these launch a real `claude.py --mock` / `codex.py --mock` agent in an isolated tmux
session and the REAL `TmuxWebtermApp` + `AutoApproveWorker`, then assert YO auto-approves a `yesno`
sequence HANDS-FREE — the full tmux-capture -> prompt_detector -> yolo_rules -> keystroke-send path that
a unit test cannot exercise. This is the regression that catches "the detector is right but YO still
does not approve in the running server".

Marked `e2e` (own parallel `pytest-e2e` lane in tools/check.py) and `socket` (self-skips when the sandbox
blocks local sockets/tmux).
"""

import os
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import yolomux_lib.app as app_module
import yolomux_lib.common as common
import yolomux_lib.control as control_module
import yolomux_lib.settings as settings_module
import yolomux_lib.approval.yolo_rules as yolo_rules_module
import yolomux_lib.yoagent.conversation as yoagent_conversation_module
import yolomux_lib.yoagent.transports as transport_module
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.tmux_utils import YOLOMUX_TMUX_SOCKET_ENV
from tests.gate_harness import patch_imported_writable_constants
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import wait_for_isolated_tmux_panes

pytestmark = [pytest.mark.e2e, pytest.mark.socket]

REPO_ROOT = Path(__file__).resolve().parent.parent


def _e2e_tmux_runtime(socket_path: Path) -> SimpleNamespace:
    """Build the one private tmux runtime used by every E2E operation."""

    return SimpleNamespace(
        tmux_binary="tmux",
        tmux_args=("-S", str(socket_path)),
        socket_path=socket_path,
    )


def _tmux(socket_path, *args, timeout=8):
    return run_isolated_tmux(_e2e_tmux_runtime(socket_path), *args, timeout=timeout)


def wait_for_e2e_tmux_pane(socket_path, session, predicate, timeout):
    ready, panes = wait_for_isolated_tmux_panes(
        _e2e_tmux_runtime(socket_path),
        [session],
        lambda captures: predicate(captures[session]),
        timeout=timeout,
    )
    return ready, panes.get(session, "")


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
    # approvald is a process, not an in-process test double: all of its inherited roots must be private.
    # A unique STATE_DIR/lock alone is insufficient because a preceding parametrized test can leave the
    # shared approvald alive with its old private tmux socket in its environment. Keep socket-bearing
    # runtime paths under the caller's short /tmp root; pytest's tmp_path is safe for ordinary state.
    state_dir = tmp_path / "state"
    runtime_base_dir = control_dir.parent / "runtime"
    runtime_dir = common.runtime_root(environ={"YOLOMUX_RUNTIME_DIR": str(runtime_base_dir)})
    config_dir = control_dir.parent / "config"
    for d in (state_dir, runtime_dir, config_dir):
        d.mkdir(parents=True, exist_ok=True)
    patch_imported_writable_constants(monkeypatch, {
        common.CONFIG_DIR: config_dir,
        common.STATE_DIR: state_dir,
        common.RUNTIME_DIR: runtime_dir,
    })
    monkeypatch.setattr(common, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(common, "STATE_DIR", state_dir)
    monkeypatch.setattr(common, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setenv("YOLOMUX_STATE_DIR", str(state_dir))
    monkeypatch.setenv("YOLOMUX_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("YOLOMUX_RUNTIME_DIR", str(runtime_base_dir))
    monkeypatch.setattr(common, "CONTROL_SOCKET_DIR", control_dir)
    monkeypatch.setattr(control_module, "CONTROL_SOCKET_DIR", control_dir)
    monkeypatch.setattr(settings_module, "SETTINGS_PATH", config_dir / "settings.yaml")
    monkeypatch.setattr(app_module, "SETTINGS_PATH", config_dir / "settings.yaml")
    monkeypatch.setattr(yolo_rules_module, "YOLO_RULES_PATH", config_dir / "yolo-rules.yaml")
    settings = settings_module.default_settings()
    settings["yolo"]["rule_file_path"] = str(yolo_rules_module.YOLO_RULES_PATH)
    settings_module.write_settings_file(settings, settings_module.SETTINGS_PATH)
    yolo_rules_module.ensure_rule_file(yolo_rules_module.YOLO_RULES_PATH)
    monkeypatch.setattr(yoagent_conversation_module, "YOAGENT_CONVERSATION_PATH", state_dir / "yoagent" / "conversation.jsonl")
    monkeypatch.setattr(yoagent_conversation_module, "YOAGENT_CLI_STATE_PATH", state_dir / "yoagent" / "cli-sessions.json")


@pytest.mark.parametrize("agent,steps", [("claude", 3), ("codex", 2)])
def test_e2e_mock_prompt_reaches_structured_ask_payload(monkeypatch, tmp_path, agent, steps):
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
        f"cd {REPO_ROOT} && exec python3 tools/mockers/{agent}.py --mock",
    )
    assert created.returncode == 0, f"tmux new-session failed: {created.stderr or created.stdout}"

    app = None
    try:
        booted, pane = wait_for_e2e_tmux_pane(socket_path, session, lambda t: "❯" in t or "›" in t, 20)
        assert booted, f"{agent}.py --mock did not boot to an input prompt:\n{pane}"
        _tmux(socket_path, "send-keys", "-t", f"{session}:", f"yesno {steps}", "Enter")
        prompted, pane = wait_for_e2e_tmux_pane(
            socket_path, session,
            lambda t: "do you want to proceed" in t.lower() or "run the following command" in t.lower(),
            20,
        )
        assert prompted, f"{agent}.py --mock did not render a permission prompt after `yesno {steps}`:\n{pane}"

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


def test_e2e_mock_codex_sleep_10_uses_working_turn_without_approval(monkeypatch, tmp_path):
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
        f"cd {REPO_ROOT} && exec python3 tools/mockers/codex.py --mock",
    )
    assert created.returncode == 0, f"tmux new-session failed: {created.stderr or created.stdout}"

    app = None
    try:
        booted, pane = wait_for_e2e_tmux_pane(socket_path, session, lambda t: "›" in t, 20)
        assert booted, f"codex.py --mock did not boot to an input prompt:\n{pane}"
        _tmux(socket_path, "send-keys", "-t", f"{session}:", "sleep 10", "Enter")
        working, pane = wait_for_e2e_tmux_pane(
            socket_path, session,
            lambda t: "• Running sleep 10 now." in t and "• Working" in t,
            20,
        )
        assert working, f"codex.py --mock did not render sleep 10 as a working turn:\n{pane}"

        app = TmuxWebtermApp([session], dangerously_yolo=False)
        payload = app.auto_approve_session_status(session, capture_bare_session_when_roster=True)
        assert payload["prompt"]["visible"] is False
        assert payload["screen"]["key"] == "working"
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
    mock_cwd = tmp_path / "mock-cwd"
    mock_cwd.mkdir()

    for agent, session in sessions.items():
        created = _tmux(
            socket_path, "new-session", "-d", "-s", session, "-x", "120", "-y", "40",
            f"cd {mock_cwd} && exec python3 {REPO_ROOT}/tools/mockers/{agent}.py --mock",
        )
        assert created.returncode == 0, f"tmux new-session failed for {agent}: {created.stderr or created.stdout}"

    app = None
    try:
        for agent, session in sessions.items():
            booted, pane = wait_for_e2e_tmux_pane(socket_path, session, lambda t: "❯" in t or "›" in t, 20)
            assert booted, f"{agent}.py --mock did not boot to an input prompt:\n{pane}"

        app = TmuxWebtermApp(list(sessions.values()), dangerously_yolo=False)
        sent: dict[str, tuple[dict, dict]] = {}
        for agent, session in sessions.items():
            preview, preview_status = app.yoagent_controller.create_yoagent_action_preview({
                "type": "send_prompt",
                "session": session,
                "text": "date",
                "submit": True,
                "return_result": True,
            })
            assert preview_status == 200
            assert preview["status"] == "ready", preview
            assert preview["target"]["agent_kind"] == agent
            result, result_status = app.yoagent_controller.execute_yoagent_send_action(
                {"preview_id": preview["id"]},
                persist_result=True,
                start_result_watch=False,
            )
            assert result_status == 200, result
            assert result["sent"] is True
            app.yoagent_controller.register_yoagent_action_wait(f"wait-{agent}", preview, result["result_marker"])
            sent[agent] = (preview, result["result_marker"])

        waiting = app.yoagent_conversation_payload()["pending_waits"]
        assert {item["id"] for item in waiting} == {"wait-claude", "wait-codex"}
        assert {item["session"] for item in waiting} == set(sessions.values())

        for agent, session in sessions.items():
            prompted, pane = wait_for_e2e_tmux_pane(
                socket_path,
                session,
                lambda t: "Do you want to proceed?" in t or "Would you like to run the following command?" in t,
                20,
            )
            assert prompted, f"{agent}.py --mock did not render a permission prompt for date:\n{pane}"
            _tmux(socket_path, "send-keys", "-t", f"{session}:", "1")
            completed, pane = wait_for_e2e_tmux_pane(socket_path, session, lambda t: "Bash(date)" in t, 20)
            assert completed, f"{agent}.py --mock did not show date output after approval:\n{pane}"

        for agent, (preview, marker) in sent.items():
            result = app.yoagent_controller.run_yoagent_action_result_watcher(
                preview,
                marker,
                watch_id=f"wait-{agent}",
                wait_seconds=3,
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


def test_e2e_yoagent_roster_job_sends_exact_command_once(monkeypatch, tmp_path):
    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")
    sock_base = Path("/tmp") / f"yoroster-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    control_dir = sock_base / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    _isolate_state(monkeypatch, tmp_path, control_dir)
    socket_path = sock_base / "s"
    sessions = ["1", "2", "3", "4"]
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))
    mock_cwd = tmp_path / "mock-cwd"
    mock_cwd.mkdir()
    for session in sessions:
        created = _tmux(
            socket_path, "new-session", "-d", "-s", session, "-x", "120", "-y", "40",
            f"cd {mock_cwd} && exec python3 {REPO_ROOT}/tools/mockers/claude.py --mock",
        )
        assert created.returncode == 0, f"tmux new-session failed for {session}: {created.stderr or created.stdout}"

    app = None
    original_send = transport_module.send_prompt
    sent = []

    def recording_send(target, text, **kwargs):
        sent.append((dict(target), text, dict(kwargs)))
        return original_send(target, text, **kwargs)

    monkeypatch.setattr(transport_module, "send_prompt", recording_send)
    try:
        for session in sessions:
            booted, pane = wait_for_e2e_tmux_pane(socket_path, session, lambda text: "❯" in text, 20)
            assert booted, f"claude.py --mock did not boot session {session}:\n{pane}"
        app = TmuxWebtermApp(sessions, dangerously_yolo=False)
        created, status = app.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": sessions,
            "action": {"session": "1", "text": "/dyn-tps-report 1 2 3 4 EOD", "return_result": False},
            "quiet_seconds": 0,
        })
        fired = app.yoagent_controller.poll_yoagent_jobs_once()
        arrived, pane = wait_for_e2e_tmux_pane(socket_path, "1", lambda text: 'I don\'t know how to handle "/dyn-tps-report 1 2 3 4 EOD"' in text, 20)
        jobs, jobs_status = app.yoagent_controller.yoagent_jobs_payload()
    finally:
        _stop_app(app)
        _tmux(socket_path, "kill-server")
        shutil.rmtree(sock_base, ignore_errors=True)

    assert status == 200
    assert fired == [created["job"]["id"]]
    assert arrived, pane
    assert "❯ /dyn-tps-report 1 2 3 4 EOD" in pane
    assert jobs_status == 200
    assert jobs["jobs"][0]["status"] == "fired"
    assert len(sent) == 1
    target, text, kwargs = sent[0]
    assert target["session"] == "1"
    assert text == "/dyn-tps-report 1 2 3 4 EOD"
    assert kwargs["verify_submit"] is True


@pytest.mark.parametrize("agent,steps", [("claude", 3), ("codex", 2)])
def test_e2e_yo_auto_approves_mock_yesno(monkeypatch, tmp_path, agent, steps):
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
        f"cd {REPO_ROOT} && exec python3 tools/mockers/{agent}.py --mock",
    )
    assert created.returncode == 0, f"tmux new-session failed: {created.stderr or created.stdout}"

    app = None
    worker = None
    try:
        # Wait for the mock to reach its input prompt (real Claude renders ❯, real Codex ›), then drive
        # the queued Yes/No sequence.
        booted, pane = wait_for_e2e_tmux_pane(socket_path, session, lambda t: "❯" in t or "›" in t, 20)
        assert booted, f"{agent}.py --mock did not boot to an input prompt:\n{pane}"
        _tmux(socket_path, "send-keys", "-t", f"{session}:", f"yesno {steps}", "Enter")
        prompted, pane = wait_for_e2e_tmux_pane(
            socket_path, session,
            lambda t: "do you want to proceed" in t.lower() or "run the following command" in t.lower(),
            20,
        )
        assert prompted, f"{agent}.py --mock did not render a permission prompt after `yesno {steps}`:\n{pane}"

        # Start the REAL app + auto-approve worker. dangerously_yolo=True mirrors a `--dang` server.
        app = TmuxWebtermApp([session], dangerously_yolo=True)
        worker, status = app.start_auto_approve_worker(session, takeover=True)
        assert worker is not None, f"auto-approve worker did not start: {status}"

        # Claude can paint its completion line before the worker observes the final queued prompt.
        # Completion is only useful evidence once the requested hands-free approvals arrived too.
        completed, pane = wait_for_e2e_tmux_pane(socket_path, session, lambda t: "complete" in t.lower() and worker.approved >= steps, 60)
        assert completed, (
            f"YO did not auto-approve {agent}.py --mock hands-free; "
            f"approved={worker.approved} blocked={worker.blocked} last_action={worker.last_action!r}\n{pane}"
        )
        assert worker.approved >= steps, (
            f"expected >= {steps} hands-free approvals for {agent}.py --mock, got {worker.approved} "
            f"(blocked={worker.blocked}, last_action={worker.last_action!r})"
        )
    finally:
        if worker is not None:
            worker.stop()
        _stop_app(app)
        _tmux(socket_path, "kill-server")
        shutil.rmtree(sock_base, ignore_errors=True)
