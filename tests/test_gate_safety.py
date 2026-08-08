# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Gate P9/P10: destructive defaults and approval failures fail closed."""

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from tests.browser_helpers.browser_console import validate_server_log_ring_payload
from tests.browser_helpers.browser_console import validate_server_log_ring_transition
from tests.gate_harness import gate_http_request
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from yolomux_lib.approval import auto_approve_worker
from yolomux_lib.approval import yolo_rules
from yolomux_lib.server_logs import SERVER_LOGS
from yolomux_lib.workspace import settings as settings_module


def _retire_expected_settings_failure(runtime, response, route: str) -> None:
    """Advance the fixture boundary only for this exact malformed-settings failure."""

    start = validate_server_log_ring_payload(runtime.server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    failures = [
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    ]
    payload = response.json()
    request_id = payload["request"]["id"]
    assert transition["droppedCount"] == 0, transition
    assert len(failures) == 1, transition
    entry = failures[0]
    message = json.loads(str(entry.get("message") or ""))
    assert (entry.get("level"), entry.get("source"), entry.get("category")) == (
        "error",
        "api-response",
        "api",
    )
    assert message["code"] == "settings_file_malformed"
    assert message["request"]["id"] == request_id
    assert message["stack"] == [{
        "code": "settings_file_malformed",
        "component": "server.http",
        "operation": route,
    }]
    runtime.server_log_boundary = current


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


def _write_malformed_settings(path: Path) -> bytes:
    malformed = b"chat:\n  retention_days: [unterminated\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(malformed)
    return malformed


def test_p9_malformed_settings_are_preserved_and_surfaced_without_default_substitution(
    gate_live_server,
    monkeypatch,
):
    settings_path = settings_module.SETTINGS_PATH
    assert settings_path.is_relative_to(gate_live_server.paths.config_dir)
    original = _write_malformed_settings(settings_path)
    monkeypatch.setattr(
        gate_live_server.app,
        "settings_payload",
        lambda: settings_module.settings_payload(settings_path),
    )

    response = gate_http_request(gate_live_server, "/api/settings")
    payload = response.json()

    assert settings_path.read_bytes() == original
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE, payload
    assert payload["settings"] is None, payload
    assert payload["error"]["code"] == "settings_file_malformed", payload
    assert isinstance(payload["error"].get("reason"), str) and payload["error"]["reason"].strip(), payload
    _retire_expected_settings_failure(gate_live_server, response, "GET /api/settings")


def test_p9_malformed_settings_never_trigger_chat_retention_from_defaults(
    gate_live_server,
    monkeypatch,
):
    settings_path = settings_module.SETTINGS_PATH
    original = _write_malformed_settings(settings_path)
    prune_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        gate_live_server.app,
        "settings_payload",
        lambda: settings_module.settings_payload(settings_path),
    )
    monkeypatch.setattr(
        gate_live_server.app.chat_store,
        "prune_if_due",
        lambda **kwargs: prune_calls.append(kwargs),
    )

    response = gate_http_request(
        gate_live_server,
        "/api/chat/bootstrap?browser_instance_id=gate-p9",
    )

    assert settings_path.read_bytes() == original
    assert prune_calls == [], prune_calls
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE, response.body
    assert response.json()["error"]["code"] == "settings_file_malformed", response.json()
    _retire_expected_settings_failure(gate_live_server, response, "GET /api/chat/bootstrap")
