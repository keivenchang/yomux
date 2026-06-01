import os
from typing import Any

import pytest

os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")
os.environ.setdefault("YOLOMUX_STATE_DIR", "/tmp/yolomux-test-state")

from yolomux_lib import auto_approve_worker


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

    def tmux_capture_pane(self, _target: str, visible_only: bool = False) -> str:
        return "visible prompt" if visible_only else "curl -sk -u yolomux:yolomux https://localhost:7777/"

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


class MovingOptionModule(DummyProcessModule):
    def selected_prompt_option(self, _visible_text: str) -> int:
        return 2


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

    acted = worker.handle_bash_prompt(module, "curl -sk -u yolomux:yolomux https://localhost:7777/", "hash-1", "option1")

    assert acted is True
    assert module.sent == [("option", "6", 1, 1)]
    assert worker.approved == 1
    assert worker.error is None
    assert worker.last_action.startswith("approved bash: curl -sk -u")
    assert events[0][1] == "approval_approved"
    assert events[0][3]["command"] == "curl -sk -u yolomux:yolomux https://localhost:7777/"


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
    assert events == [("6", "worker_error", "auto approve error", {"error": "tmux vanished"})]


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
    assert worker.last_action == "idle"
