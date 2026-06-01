import os
from typing import Any

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


def approving_decision(*_args: Any, **_kwargs: Any) -> dict[str, str]:
    return {
        "action": "approve",
        "rule_name": "test approve",
        "risk": "read",
        "source": "test",
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
