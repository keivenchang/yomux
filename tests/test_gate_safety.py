# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Gate P9/P10: destructive defaults and approval failures fail closed."""

from pathlib import Path
from typing import Any

import pytest

from tests.gate_harness import gate_http_port  # noqa: F401
from tests.native_gate_fixtures import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from yomux_lib.approval import auto_approve_worker
from yomux_lib.approval import yolo_rules


class _ApprovalEffectProbe:
    """Record whether a safety decision injected any approval keystroke."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, ...]] = []

    def extract_command(self, pane_text: str) -> str:
        return pane_text

    def tmux_send_enter(self, target: str) -> None:
        self.sent.append(("enter", target))

    def tmux_send_option(self, target: str, option: int, selected_option: int | None = None) -> None:
        self.sent.append(("option", target, option, selected_option))

    def tmux_send_option2(self, target: str, selected_option: int | None = None) -> None:
        self.sent.append(("option2", target, selected_option))


def _exercise_broken_ruleset(
    monkeypatch: pytest.MonkeyPatch,
    rule_path: Path,
) -> tuple[dict[str, Any], auto_approve_worker.AutoApproveWorker, _ApprovalEffectProbe, list[dict[str, Any]]]:
    monkeypatch.setattr(
        yolo_rules,
        "yolo_settings",
        lambda: {"rule_file_path": str(rule_path), "dry_run": False},
    )
    real_evaluate = yolo_rules.evaluate
    decisions: list[dict[str, Any]] = []

    def capture_decision(*args: Any, **kwargs: Any) -> dict[str, Any]:
        decision = real_evaluate(*args, **kwargs)
        decisions.append(decision)
        return decision

    monkeypatch.setattr(auto_approve_worker.yolo_rules, "evaluate", capture_decision)
    events: list[dict[str, Any]] = []
    worker = auto_approve_worker.AutoApproveWorker(
        "fixture-session",
        event_callback=lambda _target, event_type, message, details: events.append(
            {"type": event_type, "message": message, "details": details}
        ),
    )
    probe = _ApprovalEffectProbe()
    handled = worker.handle_bash_prompt(
        probe,
        "printf fixture-safe-command",
        "fixture-prompt-hash",
        "option1",
        command="printf fixture-safe-command",
    )
    assert handled is True
    assert len(decisions) == 1, decisions
    return decisions[0], worker, probe, events


def _assert_typed_fail_closed(
    result: tuple[dict[str, Any], auto_approve_worker.AutoApproveWorker, _ApprovalEffectProbe, list[dict[str, Any]]],
    expected_reason_code: str,
) -> None:
    decision, worker, probe, events = result
    assert decision["action"] == "ask", decision
    assert decision["reason_code"] == expected_reason_code, decision
    assert isinstance(decision.get("error"), str) and decision["error"].strip(), decision
    assert probe.sent == [], probe.sent
    assert worker.approved == 0
    assert worker.blocked == 1
    assert len(events) == 1, events
    assert events[0]["type"] == "approval_blocked", events
    assert events[0]["details"]["reason_code"] == expected_reason_code, events


def test_p10_absent_yolo_rules_fail_closed_with_a_typed_reason(monkeypatch, gate_runtime_paths):
    rule_path = gate_runtime_paths.config_dir / "absent-yolo-rules.yaml"
    assert not rule_path.exists()
    _assert_typed_fail_closed(
        _exercise_broken_ruleset(monkeypatch, rule_path),
        "rules_file_missing",
    )


def test_p10_unreadable_yolo_rules_fail_closed_with_a_typed_reason(monkeypatch, gate_runtime_paths):
    rule_path = gate_runtime_paths.config_dir / "unreadable-yolo-rules.yaml"
    rule_path.mkdir()
    _assert_typed_fail_closed(
        _exercise_broken_ruleset(monkeypatch, rule_path),
        "rules_file_unreadable",
    )


def test_p10_malformed_yolo_rules_fail_closed_with_a_typed_reason(monkeypatch, gate_runtime_paths):
    rule_path = gate_runtime_paths.config_dir / "malformed-yolo-rules.yaml"
    rule_path.write_text("default: approve\nrules:\n  - type: regex\n    match: '('\n    action: approve\n", encoding="utf-8")
    _assert_typed_fail_closed(
        _exercise_broken_ruleset(monkeypatch, rule_path),
        "rules_file_malformed",
    )


def test_p10_evaluator_failure_fails_closed_with_a_typed_reason(monkeypatch, gate_runtime_paths):
    rule_path = gate_runtime_paths.config_dir / "evaluator-yolo-rules.yaml"
    rule_path.write_text("default: approve\nrules: []\n", encoding="utf-8")

    def fail_evaluation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("fixture evaluator failed")

    monkeypatch.setattr(yolo_rules, "evaluate_ruleset", fail_evaluation)
    _assert_typed_fail_closed(
        _exercise_broken_ruleset(monkeypatch, rule_path),
        "rules_evaluator_failed",
    )
