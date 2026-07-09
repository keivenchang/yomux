# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from http import HTTPStatus
import threading
from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.yoagent import controller as controller_module
from yolomux_lib.yoagent import transports as transport_module


pytestmark = pytest.mark.usefixtures("no_control_socket", "isolated_yoagent_conversation_state")


def install_fake_yolomux_state(monkeypatch):
    state = {}
    lock = threading.Lock()
    monkeypatch.setattr(app_module, "read_yolomux_state", lambda: dict(state))
    monkeypatch.setattr(app_module, "update_yolomux_state", lambda updates: state.update(updates))
    monkeypatch.setattr(app_module, "mutate_yolomux_state", lambda mutator: _lock_and_mutate(lock, state, mutator))
    return state


def _lock_and_mutate(lock, state, mutator):
    with lock:
        return mutator(state)


def fake_agent_tui_send_result():
    return SimpleNamespace(
        ok=True,
        sent=True,
        pasted=True,
        cleared=False,
        reason_code="submitted",
        returncode=0,
        error="",
        clear_result=SimpleNamespace(as_dict=lambda: {}),
    )


def idle_target(session: str = "1") -> dict:
    return {
        "session": session,
        "pane_target": f"%{session}",
        "agent_kind": "claude",
        "agent_session_id": f"claude-session-{session}",
        "agent_transcript": f"/tmp/claude-session-{session}.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }


def install_chat_defaults(monkeypatch, webapp):
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {"type": args[0] if args else ""})


def test_yoagent_chat_send_by_bare_number_executes_verified_send_without_refusal(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target_calls = []
    send_calls = []
    watchers = []
    install_chat_defaults(monkeypatch, webapp)
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: target_calls.append(session) or (idle_target(session), HTTPStatus.OK))
    monkeypatch.setattr(
        transport_module,
        "send_prompt",
        lambda send_target, text, **kwargs: send_calls.append((send_target, text, kwargs)) or fake_agent_tui_send_result(),
    )
    monkeypatch.setattr(webapp.yoagent_controller, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"id": "watch-1", "started": True})

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "ask 1 what time it is"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert target_calls == ["1", "1"]
    assert len(send_calls) == 1
    send_target, text, kwargs = send_calls[0]
    assert send_target["pane_target"] == "%1"
    assert text == "what time is it?"
    assert kwargs["verify_submit"] is True
    assert watchers and watchers[0][0]["session"] == "1"
    assert "I verified tmux session `1`" in payload["answer"]
    assert "did not send anything" not in payload["answer"]
    assert "no transport" not in payload["answer"].lower()


def test_yoagent_chat_wait_then_send_queues_while_busy_then_fires_when_idle(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1"])
    state = {"screen": "working"}
    send_calls = []
    install_chat_defaults(monkeypatch, webapp)

    def target(session):
        current = idle_target(session)
        current["screen"] = {"key": state["screen"], "text": state["screen"]}
        return current, HTTPStatus.OK

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    monkeypatch.setattr(
        transport_module,
        "send_prompt",
        lambda send_target, text, **kwargs: send_calls.append((send_target, text, kwargs)) or fake_agent_tui_send_result(),
    )

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "wait for session 1 to finish, then tell it to run date"})
        queued_jobs, _queued_status = webapp.yoagent_controller.yoagent_jobs_payload()
        send_calls_before_idle = list(send_calls)
        with webapp.yoagent_job_lock:
            webapp.yoagent_jobs[queued_jobs["jobs"][0]["id"]]["predicate"]["quiet_seconds"] = 0
        state["screen"] = "idle"
        fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        fired_jobs, _fired_status = webapp.yoagent_controller.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert send_calls_before_idle == []
    assert queued_jobs["jobs"][0]["type"] == "wait_then_send"
    assert queued_jobs["jobs"][0]["status"] == "queued"
    assert fired == [queued_jobs["jobs"][0]["id"]]
    assert fired_jobs["jobs"][0]["status"] == "fired"
    assert len(send_calls) == 1
    assert send_calls[0][1] == "date"
    assert send_calls[0][2]["verify_submit"] is True


def test_yoagent_chat_roster_calm_then_send_queues_without_model_backend(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1", "2", "3", "4"])
    install_chat_defaults(monkeypatch, webapp)
    monkeypatch.setattr(
        webapp.yoagent_controller,
        "yoagent_chat_cli_or_fallback_response",
        lambda _ctx: (_ for _ in ()).throw(AssertionError("recognized roster job must not invoke a model backend")),
    )

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({
            "message": "periodically monitor 1 2 3 4 and if they are all done, then send a /dyn-tps-report 1 2 3 4 EOD to session 1.",
        })
        jobs, jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert jobs_status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert len(jobs["jobs"]) == 1
    job = jobs["jobs"][0]
    assert job["type"] == "wait_roster_then_send"
    assert job["target"] == {"roster": ["1", "2", "3", "4"]}
    assert job["predicate"] == {"type": "all_calm", "quiet_seconds": 10.0}
    assert job["action"]["session"] == "1"
    assert job["action"]["text"] == "/dyn-tps-report 1 2 3 4 EOD"
    assert job["action"]["return_result"] is False


def test_yoagent_chat_accepts_validated_model_roster_plan_as_confirmation_only_job(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1", "2", "3", "4"])
    install_chat_defaults(monkeypatch, webapp)
    model_calls = []
    monkeypatch.setattr(webapp.yoagent_controller.deps, "resolve_yoagent_backend", lambda _backend: "codex")

    def model_plan(*args, **kwargs):
        model_calls.append((args, kwargs))
        return (
            '{"type":"wait_roster_then_send","roster":["1","2","3","4"],"destination":"1","command":"/dyn-tps-report 1 2 3 4 EOD","return_result":false}',
            "",
            {"backend": "codex"},
        )

    monkeypatch.setattr(
        webapp.yoagent_controller,
        "run_yoagent_model_intent_backend",
        model_plan,
    )

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({
            "message": "Could you keep an eye on 1, 2, 3 and 4, and after they all settle have session 1 run /dyn-tps-report 1 2 3 4 EOD?",
        })
        jobs, jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert jobs_status == HTTPStatus.OK
    assert payload["backend_used"] == "codex"
    assert payload["cli"]["structured_job"] is True
    assert len(jobs["jobs"]) == 1
    assert jobs["jobs"][0]["status"] == "pending_confirmation"
    assert jobs["jobs"][0]["action"]["text"] == "/dyn-tps-report 1 2 3 4 EOD"
    assert "Invoke preset `wait-roster-then-send`" in model_calls[0][0][1]
    assert "Do not use tools" in model_calls[0][0][1]
    assert model_calls[0][0][2]["type"] == "object"


def test_yoagent_model_intent_requires_enabled_builtin_skill(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    monkeypatch.setattr(
        webapp.yoagent_controller,
        "yoagent_skills_payload",
        lambda: {"skills": [{"name": "wait-roster-then-send", "enabled": True, "builtin": False, "model_intent": {}}]},
    )
    try:
        assert webapp.yoagent_controller.yoagent_model_intent("Monitor 1 and 2 then send a report to session 1") is None
    finally:
        webapp.control_server.stop()


def test_yoagent_model_intent_backend_isolated_and_disables_claude_tools(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    claude_calls = []
    codex_calls = []
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_claude_cli", lambda *args, **kwargs: claude_calls.append((args, kwargs)) or ("<no-plan/>", ""))
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_codex_cli", lambda *args, **kwargs: codex_calls.append((args, kwargs)) or ("<no-plan/>", "", ""))

    try:
        schema = {"type": "object", "properties": {}, "required": []}
        claude_answer, claude_reason, claude_status = webapp.yoagent_controller.run_yoagent_model_intent_backend("claude", "plan", schema, {"claude_model": "test"})
        codex_answer, codex_reason, codex_status = webapp.yoagent_controller.run_yoagent_model_intent_backend("codex", "plan", schema, {"codex_model": "test"})
    finally:
        webapp.control_server.stop()

    assert (claude_answer, claude_reason) == ("<no-plan/>", "")
    assert claude_calls[0][1]["session_id"] == ""
    assert claude_calls[0][1]["resume"] is False
    assert claude_calls[0][1]["tools"] == ""
    assert claude_calls[0][1]["permission_mode"] is None
    assert claude_calls[0][1]["json_schema"] == schema
    assert claude_status["external_tools_enabled"] is False
    assert (codex_answer, codex_reason) == ("<no-plan/>", "")
    assert codex_calls[0][1]["session_id"] == ""
    assert codex_calls[0][1]["resume"] is False
    assert codex_calls[0][1]["output_schema"] == schema
    assert codex_status["external_tools_enabled"] is False


def test_yoagent_sequential_dependent_ask_waits_then_sends_computed_followup(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    send_calls = []
    watchers = []
    install_chat_defaults(monkeypatch, webapp)
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: (idle_target(session), HTTPStatus.OK))
    monkeypatch.setattr(
        transport_module,
        "send_prompt",
        lambda send_target, text, **kwargs: send_calls.append((send_target, text, kwargs)) or fake_agent_tui_send_result(),
    )
    monkeypatch.setattr(webapp.yoagent_controller, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"id": f"watch-{len(watchers)}", "started": True})

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "ask 1 what time it is, then add 5 minutes, then ask if that is correct."})
        assert status == HTTPStatus.OK
        assert "I verified tmux session `1`" in payload["answer"]
        assert len(send_calls) == 1
        assert send_calls[0][1] == "what time is it?"
        assert len(watchers) == 1
        webapp.yoagent_controller.finish_yoagent_action_result(watchers[0][0], "The time is 7:20 PM PDT.")
    finally:
        webapp.control_server.stop()

    assert len(send_calls) == 2
    assert send_calls[1][1] == "Is 7:25 PM the correct time now?"
    assert "session" not in send_calls[1][1].lower()
    assert len(watchers) == 2
    assert watchers[1][0]["session"] == "1"


def test_yoagent_prompt_answer_uses_selector_path_without_free_text_paste(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target = idle_target("1")
    target.update({
        "prompt": {"visible": True, "selected_option": 1, "options": [{"text": "Approve"}, {"text": "Reject"}]},
        "screen": {"key": "approval", "text": "Approve this?", "selected_option": 1, "options": [{"text": "Approve"}, {"text": "Reject"}]},
    })
    moved = []
    entered = []
    install_chat_defaults(monkeypatch, webapp)
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(controller_module, "tmux_move_to_option", lambda pane, option, selected_option=None: moved.append((pane, option, selected_option)))
    monkeypatch.setattr(controller_module, "tmux_send_enter", lambda pane: entered.append(pane))
    monkeypatch.setattr(webapp.yoagent_controller.deps, "tmux_capture_pane", lambda _target, visible_only=False: "  1. Approve\n❯ 2. Reject\nEnter to select")
    monkeypatch.setattr(
        transport_module,
        "send_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prompt answers must not paste free text")),
    )

    try:
        preview, preview_status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "2"})
        result, status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=False)
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert preview["status"] == "ready"
    assert preview["prompt_answer"]["option"] == 2
    assert status == HTTPStatus.OK
    assert result["prompt_answer"] is True
    assert result["option"] == 2
    assert moved == [("%1", 2, 1)]
    assert entered == ["%1"]
