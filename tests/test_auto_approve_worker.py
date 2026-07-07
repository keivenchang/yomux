import os
from typing import Any

import pytest


from yolomux_lib import auto_approve_worker
from yolomux_lib.auto_approve_policy import auto_approve_poll_is_quiet
from yolomux_lib.auto_approve_policy import auto_approve_quiet_poll_interval


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # process_once's post-action settle is `self.stop_event.wait(3.0)` (~3s of real wall time per test
    # with no effect on the mocked screen). Make Event.wait return its set-state immediately instead of
    # blocking — semantics preserved (False = not stopped), ~3s saved per test. Also no-op time.sleep.
    monkeypatch.setattr(auto_approve_worker.threading.Event, "wait", lambda self, timeout=None: self.is_set())
    monkeypatch.setattr(auto_approve_worker.time, "sleep", lambda *_args, **_kwargs: None)


class DummyApproveModule:
    def __init__(self):
        self.sent: list[tuple[Any, ...]] = []

    def extract_command(self, pane_text: str) -> str:
        return pane_text

    def tmux_send_enter(self, target: str) -> None:
        self.sent.append(("enter", target))

    def tmux_send_option2(self, target: str, selected_option: int | None = None) -> None:
        self.sent.append(("option2", target, selected_option))

    def tmux_send_option(self, target: str, option: int, selected_option: int | None = None) -> None:
        self.sent.append(("option", target, option, selected_option))


class DummyProcessModule(DummyApproveModule):
    PROMPT_RETRY_SECONDS = 5.0

    def __init__(self, prompt_state: dict[str, Any]):
        super().__init__()
        self.prompt_state = prompt_state
        self.capture_calls: list[tuple[str, bool]] = []

    def tmux_capture_pane(self, _target: str, visible_only: bool = False) -> str:
        self.capture_calls.append((_target, visible_only))
        return "visible prompt" if visible_only else "curl -sk -u yolomux:yolomux https://localhost:19077/"

    def approval_prompt_state(self, _visible_text: str) -> dict[str, Any]:
        return self.prompt_state


class DummyHybridProcessModule(DummyProcessModule):
    def __init__(self, visible_text: str, prompt_state: dict[str, Any]):
        super().__init__(prompt_state)
        self.visible_text = visible_text

    def tmux_capture_pane(self, _target: str, visible_only: bool = False) -> str:
        return self.visible_text if visible_only else "pane output without command context"

    def hybrid_approval_prompt_state(self, _target: str, _visible_text: str, pane_text: str | None = None, prompt_source: str = "hybrid") -> dict[str, Any]:
        if prompt_source == "pane":
            return {
                "visible": False,
                "type": "",
                "hash": "",
                "source": "pane",
                "yes_selected": False,
                "selected_option": 0,
            }
        return self.prompt_state


class StalePaneCommandModule(DummyProcessModule):
    PROMPT_RETRY_SECONDS = 5.0

    def __init__(self):
        super().__init__({
            "type": "bash",
            "hash": "hash-cp",
            "action": "option1",
            "selected_option": 1,
            "yes_selected": True,
            "source": "pane",
            "command": "cp -r src/ dist/",
        })

    def tmux_capture_pane(self, _target: str, visible_only: bool = False) -> str:
        if visible_only:
            return "live prompt for cp -r src/ dist/"
        return "● Bash(chmod +x scripts/deploy.sh)\n⎿ ok\nPermission rule Bash requires confirmation"

    def hybrid_approval_prompt_state(self, _target: str, _visible_text: str, pane_text: str | None = None, prompt_source: str = "hybrid") -> dict[str, Any]:
        return self.prompt_state


class MovingOptionModule(DummyProcessModule):
    def selected_prompt_option(self, _visible_text: str) -> int:
        return 2


class StableWalkModule(DummyProcessModule):
    # highlight stays on the target through the walk; records the walk + Enter.
    def __init__(self, prompt_state: dict[str, Any]):
        super().__init__(prompt_state)
        self.moved: list[Any] = []

    def selected_prompt_option(self, _visible_text: str) -> int:
        return 1

    def tmux_move_to_option(self, target: str, option: int, selected_option: int | None = None) -> None:
        self.moved.append((target, option, selected_option))


class PostWalkMoveModule(StableWalkModule):
    # Highlight is correct at the pre-walk check (1) but moves to 2 AFTER the walk (a redraw).
    def __init__(self, prompt_state: dict[str, Any]):
        super().__init__(prompt_state)
        self._calls = 0

    def selected_prompt_option(self, _visible_text: str) -> int:
        self._calls += 1
        return 1 if self._calls == 1 else 2


class MissingPaneModule(DummyApproveModule):
    def tmux_capture_pane(self, _target: str, visible_only: bool = False) -> None:
        return None


def test_quiet_polling_requires_static_non_working_screen_and_caps_at_four_seconds():
    assert auto_approve_poll_is_quiet("working", False) is False
    assert auto_approve_poll_is_quiet("idle", True) is False
    assert auto_approve_poll_is_quiet("idle", False) is True
    assert auto_approve_quiet_poll_interval(0.5, 30, 0) == pytest.approx(2.25)
    assert auto_approve_quiet_poll_interval(0.5, 60, -0.5) == pytest.approx(3.5)
    assert auto_approve_quiet_poll_interval(0.5, 60, 0.5) == pytest.approx(4.5)


def test_send_action_confirms_after_reverifying_the_walked_highlight():
    # walk to the target, re-verify it landed there, THEN press Enter.
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    module = StableWalkModule({"type": "bash", "selected_option": 1})
    assert worker.send_action(module, "option1", selected_option=1) is True
    assert module.moved == [("6", 1, 1)]
    assert ("enter", "6") in module.sent


def test_send_action_aborts_when_highlight_moves_during_the_walk():
    # if the highlight moved during the ~0.6s walk, do NOT press Enter (could confirm "No").
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    module = PostWalkMoveModule({"type": "bash", "selected_option": 1})
    assert worker.send_action(module, "option1", selected_option=1) is False
    assert module.moved == [("6", 1, 1)]
    assert ("enter", "6") not in module.sent


def approving_decision(*_args: Any, **_kwargs: Any) -> dict[str, str]:
    return {
        "action": "approve",
        "rule_name": "test approve",
        "risk": "read",
        "source": "test",
    }


def rule_decision(action: str, **extra: Any) -> dict[str, Any]:
    return {
        "action": action,
        "rule_name": extra.pop("rule_name", f"test {action}"),
        "risk": extra.pop("risk", "test"),
        "source": extra.pop("source", "test"),
        **extra,
    }


def test_bash_prompt_handler_truncates_command_without_crashing(monkeypatch):
    module = DummyApproveModule()
    events: list[tuple[str, str, str, dict[str, Any]]] = []
    worker = auto_approve_worker.AutoApproveWorker(
        "6",
        interval=0.01,
        event_callback=lambda target, event_type, message, details: events.append((target, event_type, message, details)),
    )
    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", approving_decision)

    acted = worker.handle_bash_prompt(module, "curl -sk -u yolomux:yolomux https://localhost:19077/", "hash-1", "option1")

    assert acted is True
    assert module.sent == [("option", "6", 1, 1)]
    assert worker.approved == 1
    assert worker.error is None
    assert worker.last_action.startswith("approved bash: curl -sk -u")
    assert worker.status()["last_action_key"] == "yolo.status.approvedBash"
    assert worker.status()["last_action_params"]["description"].startswith("curl -sk -u")
    assert events[0][1] == "approval_approved"
    assert events[0][3]["command"] == "curl -sk -u yolomux:yolomux https://localhost:19077/"
    assert events[0][3]["message_key"] == "yolo.status.approvedBash"


def test_bash_prompt_decline_sends_no_option(monkeypatch):
    module = DummyApproveModule()
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    monkeypatch.setattr(worker.stop_event, "wait", lambda _delay: False)
    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", lambda *_args, **_kwargs: rule_decision("decline"))

    acted = worker.handle_bash_prompt(module, "rm file.txt", "hash-1", "option1", selected_option=1, command="rm file.txt")

    assert acted is True
    assert module.sent == [("option", "6", 2, 1)]
    assert worker.approved == 1
    assert worker.last_action == "declined bash: rm file.txt"


def test_bash_prompt_passive_actions_do_not_send_keys(monkeypatch):
    for action in ("block", "notify", "ask", "off"):
        module = DummyApproveModule()
        worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
        monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", lambda *_args, action=action, **_kwargs: rule_decision(action))

        acted = worker.handle_bash_prompt(module, "rm file.txt", f"hash-{action}", "option1", selected_option=1, command="rm file.txt")

        assert acted is True
        assert module.sent == []
        assert worker.blocked == 1
        assert worker.last_blocked_hash == f"hash-{action}"


def test_process_once_uses_live_prompt_command_not_stale_pane_command(monkeypatch):
    module = StalePaneCommandModule()
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    evaluated: list[str] = []

    def evaluate(command: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        evaluated.append(command)
        return rule_decision("block", rule_name="block cp")

    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", evaluate)

    acted = worker.process_once(module)

    assert acted is True
    assert evaluated == ["cp -r src/ dist/"]
    assert worker.last_blocked_hash == "hash-cp"
    assert worker.last_action == "blocked bash: cp -r src/ dist/"


def test_process_once_routes_plan_prompt_through_non_bash_rules(monkeypatch):
    module = DummyProcessModule({
        "visible": True,
        "type": "plan",
        "hash": "hash-plan",
        "action": "option1",
        "selected_option": 1,
        "yes_selected": True,
        "question_text": "Claude has written up a plan and is ready to execute. Would you like to proceed?",
        "text": "Claude has written up a plan and is ready to execute. Would you like to proceed?",
    })
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    evaluated: list[tuple[str, str]] = []

    def evaluate(rule_input: str, prompt_type: str = "bash", *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        evaluated.append((rule_input, prompt_type))
        return rule_decision("approve", rule_name="approve plan")

    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", evaluate)

    acted = worker.process_once(module)

    assert acted is True
    assert evaluated == [("Claude has written up a plan and is ready to execute. Would you like to proceed?", "plan")]
    assert module.sent == [("option", "6", 1, 1)]
    assert worker.last_action == "approved plan: approve plan"


def test_process_once_skips_capture_after_initial_tmux_activity_observation():
    module = DummyProcessModule({
        "visible": False,
        "type": "",
        "hash": "",
        "reason": "no approval prompt",
    })
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01, capture_gate=lambda _target: False)

    first = worker.process_once(module)
    module.capture_calls.clear()
    second = worker.process_once(module)

    assert first is False
    assert second is False
    assert module.capture_calls == []
    assert worker.last_action == "idle; tmux activity quiet"


def test_process_once_self_stops_after_repeated_missing_capture():
    events: list[tuple[str, str, str, dict[str, Any]]] = []
    module = MissingPaneModule()
    worker = auto_approve_worker.AutoApproveWorker(
        "6",
        interval=0.01,
        event_callback=lambda target, event_type, message, details: events.append((target, event_type, message, details)),
    )

    for _ in range(auto_approve_worker.AUTO_APPROVE_MISSING_CAPTURE_LIMIT):
        assert worker.process_once(module) is False

    assert worker.stop_event.is_set()
    assert worker.alive() is False
    assert worker.last_action == "session vanished; auto approve stopped"
    assert events == [(
        "6",
        "worker_stopped",
        "auto approve stopped because the tmux session vanished",
        {
            "failures": auto_approve_worker.AUTO_APPROVE_MISSING_CAPTURE_LIMIT,
            "message_key": "events.message.yolo.sessionVanished",
            "message_params": {},
        },
    )]


def test_process_once_captures_idle_target_when_prompt_hash_is_pending():
    module = DummyProcessModule({
        "visible": False,
        "type": "",
        "hash": "",
        "reason": "prompt cleared",
    })
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01, capture_gate=lambda _target: False)
    worker.last_hash = "hash-pending"

    acted = worker.process_once(module)

    assert acted is False
    assert module.capture_calls == [("6", True)]
    assert worker.last_action == "idle; prompt cleared"
    assert worker.last_hash == ""


def test_bash_prompt_dry_run_does_not_send_key(monkeypatch):
    module = DummyApproveModule()
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    monkeypatch.setattr(
        auto_approve_worker.yolo_rules,
        "evaluate",
        lambda *_args, **_kwargs: rule_decision("ask", dry_run=True, would_action="approve"),
    )

    acted = worker.handle_bash_prompt(module, "make test", "hash-dry-run", "option1", selected_option=1, command="make test")

    assert acted is True
    assert module.sent == []
    assert worker.blocked == 1
    assert worker.last_action == "dry-run would approve bash: make test"


def test_non_bash_prompt_passive_action_does_not_send_key(monkeypatch):
    module = DummyApproveModule()
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", lambda *_args, **_kwargs: rule_decision("block", rule_name="block file edit"))

    acted = worker.handle_non_bash_prompt(module, "Edit /repo/app.py", "hash-file", "option1", "file", selected_option=1)

    assert acted is True
    assert module.sent == []
    assert worker.blocked == 1
    assert worker.last_action == "block file: block file edit"


def test_auto_approve_process_lock_blocks_second_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_approve_worker, "AUTO_APPROVE_LOCK_DIR", tmp_path)
    first = auto_approve_worker.AutoApproveProcessLock("6", {"owner": "first"})
    second = auto_approve_worker.AutoApproveProcessLock("6", {"owner": "second"})
    try:
        acquired, owner = first.acquire()
        assert acquired is True
        assert owner is None

        acquired, owner = second.acquire()

        assert acquired is False
        assert owner["target"] == "6"
        assert owner["owner"] == "first"
    finally:
        first.release()
        second.release()


def test_emit_event_logs_expected_io_failure(caplog):
    worker = auto_approve_worker.AutoApproveWorker(
        "6",
        event_callback=lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )

    worker.emit_event("worker_error", "auto approve error")

    assert "disk full" in caplog.text


def test_emit_event_does_not_swallow_programming_error():
    worker = auto_approve_worker.AutoApproveWorker(
        "6",
        event_callback=lambda *_args: (_ for _ in ()).throw(AssertionError("bug")),
    )

    with pytest.raises(AssertionError):
        worker.emit_event("worker_error", "auto approve error")


def test_run_retries_expected_poll_error(monkeypatch):
    events: list[tuple[str, str, str, dict[str, Any]]] = []
    worker = auto_approve_worker.AutoApproveWorker(
        "6",
        interval=0.01,
        event_callback=lambda target, event_type, message, details: events.append((target, event_type, message, details)),
    )

    monkeypatch.setattr(worker, "process_once", lambda _module: (_ for _ in ()).throw(OSError("tmux vanished")))

    def stop_after_wait(_delay: float) -> bool:
        worker.stop_event.set()
        return False

    monkeypatch.setattr(worker.stop_event, "wait", stop_after_wait)

    worker.run()

    assert worker.error == "tmux vanished"
    assert worker.last_action == "auto approve error"
    assert events == [(
        "6",
        "worker_error",
        "auto approve error",
        {"error": "tmux vanished", "message_key": "yolo.status.autoApproveError", "message_params": {}},
    )]


def test_run_does_not_swallow_programming_error(monkeypatch):
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    monkeypatch.setattr(worker, "process_once", lambda _module: (_ for _ in ()).throw(AssertionError("bug")))

    with pytest.raises(AssertionError):
        worker.run()


def test_process_once_can_approve_when_codex_highlights_option2(monkeypatch):
    module = DummyProcessModule({
        "type": "bash",
        "yes_selected": False,
        "selected_option": 2,
        "hash": "hash-2",
        "action": "option1",
        "text": "Would you like to run the following command?",
    })
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", approving_decision)

    acted = worker.process_once(module)

    assert acted is True
    assert module.sent == [("option", "6", 1, 2)]
    assert worker.approved == 1


def test_process_once_aborts_if_highlighted_option_moves_before_enter(monkeypatch):
    module = MovingOptionModule({
        "type": "bash",
        "yes_selected": True,
        "selected_option": 1,
        "hash": "hash-moving",
        "action": "option1",
        "text": "Would you like to run the following command?",
    })
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01)
    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", approving_decision)

    acted = worker.process_once(module)

    assert acted is False
    assert module.sent == []
    assert worker.approved == 0
    assert worker.last_action == "approval option moved from 1 to 2; waiting for next capture"


def test_process_once_can_use_hybrid_transcript_prompt_when_pane_header_is_missing(monkeypatch):
    module = DummyHybridProcessModule("❯ 1. Yes\n  2. No", {
        "visible": True,
        "type": "bash",
        "yes_selected": True,
        "selected_option": 1,
        "hash": "transcript-hash",
        "action": "option1",
        "text": "Transcript pending Bash: make test",
        "command": "make test",
        "source": "transcript",
    })
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01, prompt_source="hybrid")
    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", approving_decision)

    acted = worker.process_once(module)

    assert acted is True
    assert module.sent == [("option", "6", 1, 1)]
    assert worker.approved == 1
    assert worker.last_action == "approved bash: make test"


def test_process_once_can_force_pane_only_prompt_source(monkeypatch):
    module = DummyHybridProcessModule("❯ 1. Yes\n  2. No", {
        "visible": True,
        "type": "bash",
        "yes_selected": True,
        "selected_option": 1,
        "hash": "transcript-hash",
        "action": "option1",
        "text": "Transcript pending Bash: make test",
        "command": "make test",
        "source": "transcript",
    })
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01, prompt_source="pane")
    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", approving_decision)

    acted = worker.process_once(module)

    assert acted is False
    assert module.sent == []
    assert worker.last_action == "question visible; waiting for manual answer"


def test_process_once_does_not_accept_normal_agent_question(monkeypatch):
    module = DummyHybridProcessModule("Which backend should I use?\n❯ 1. vLLM\n  2. SGLang", {
        "visible": False,
        "type": "",
        "hash": "",
        "source": "pane",
        "yes_selected": False,
        "selected_option": 0,
    })
    worker = auto_approve_worker.AutoApproveWorker("6", interval=0.01, prompt_source="hybrid")
    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", approving_decision)

    acted = worker.process_once(module)

    assert acted is False
    assert module.sent == []
    assert worker.approved == 0
    assert worker.blocked == 0
    assert worker.last_action == "question visible; waiting for manual answer"
