import io
import json
import os


import pytest

from yolomux_lib import claude_permission_hook as hook
from yolomux_lib import yolo_rules


def bash_request(command: str, **extra) -> dict:
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
    payload.update(extra)
    return payload


@pytest.mark.parametrize(
    "action,expected",
    [
        ("approve", "allow"),
        ("decline", "deny"),
        ("block", "deny"),
        ("ask", "ask"),
        ("notify", "ask"),
        ("off", "ask"),
        ("totally-unknown", "ask"),
    ],
)
def test_rule_action_maps_to_permission_decision(monkeypatch, action, expected):
    # Only a clean "approve" auto-allows; block/decline deny; everything else (and any unknown
    # action the engine could grow) defers to the human prompt. This is the fail-safe contract.
    monkeypatch.setattr(
        hook.yolo_rules, "evaluate", lambda *a, **k: {"action": action, "rule_name": "r", "source": "test"}
    )
    permission, _reason = hook.decide(bash_request("echo hi"))
    assert permission == expected


def test_deny_and_allow_carry_a_reason_naming_the_rule(monkeypatch):
    monkeypatch.setattr(
        hook.yolo_rules,
        "evaluate",
        lambda *a, **k: {"action": "approve", "rule_name": "git read-only", "source": "user"},
    )
    permission, reason = hook.decide(bash_request("git status"))
    assert permission == "allow"
    assert "git read-only" in reason and "user" in reason

    monkeypatch.setattr(
        hook.yolo_rules,
        "evaluate",
        lambda *a, **k: {"action": "ask", "rule_name": "default", "source": "user"},
    )
    _permission, ask_reason = hook.decide(bash_request("python3 deploy.py"))
    assert ask_reason == ""


def test_hard_floor_command_denies_through_the_real_engine():
    # No monkeypatch: the built-in catastrophic floor (rm root) always blocks regardless of the
    # user's ruleset, so a real evaluate() call must come back as deny end-to-end.
    permission, reason = hook.decide(bash_request("rm -rf /"))
    assert permission == "deny"
    assert reason


def test_command_is_passed_through_to_engine_with_claude_agent(monkeypatch):
    seen = {}

    def fake_evaluate(cmd, prompt_type="bash", agent="", session="", **k):
        seen.update(cmd=cmd, prompt_type=prompt_type, agent=agent, session=session)
        return {"action": "approve"}

    monkeypatch.setattr(hook.yolo_rules, "evaluate", fake_evaluate)
    hook.decide(bash_request("ls -la /tmp", session_id="abc123"))
    assert seen == {"cmd": "ls -la /tmp", "prompt_type": "bash", "agent": "claude", "session": "abc123"}


def test_non_bash_tool_defers_to_human(monkeypatch):
    # Edit/Write carry no shell command; the hook must not guess — it asks. It must also never
    # reach the engine for these (no command to evaluate).
    monkeypatch.setattr(hook.yolo_rules, "evaluate", lambda *a, **k: pytest.fail("engine called for non-Bash"))
    for payload in (
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "/x"}},
        {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": "/x", "content": "y"}},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "   "}},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash"},
        {},
        "not-a-dict",
    ):
        assert hook.decide(payload) == ("ask", "")


def test_read_payload_tolerates_empty_and_malformed_input():
    assert hook._read_payload("") == {}
    assert hook._read_payload("   ") == {}
    assert hook._read_payload("{not json") == {}
    assert hook._read_payload("[1, 2, 3]") == {}  # valid JSON but not an object
    assert hook._read_payload('{"tool_name": "Bash"}') == {"tool_name": "Bash"}


def test_hook_response_shape():
    resp = hook.hook_response("allow", "because")
    assert resp == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "because",
        }
    }
    # No reason -> the key is omitted entirely (ask path).
    assert "permissionDecisionReason" not in hook.hook_response("ask")["hookSpecificOutput"]


def test_main_reads_stdin_and_emits_valid_decision_json(monkeypatch, capsys):
    monkeypatch.setattr(hook.yolo_rules, "evaluate", lambda *a, **k: {"action": "approve", "rule_name": "r"})
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bash_request("echo hi"))))
    assert hook.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_on_wrong_event_or_empty_stdin_asks(monkeypatch, capsys):
    monkeypatch.setattr(hook.yolo_rules, "evaluate", lambda *a, **k: pytest.fail("engine called"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}})))
    assert hook.main([]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert hook.main([]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_print_settings_emits_the_pretooluse_hook_snippet(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("should-not-be-read"))
    assert hook.main(["--print-settings"]) == 0
    settings = json.loads(capsys.readouterr().out)
    pre = settings["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Bash"
    assert pre["hooks"][0]["command"] == "python3 -m yolomux_lib.claude_permission_hook"


def test_decision_table_only_allows_approve():
    # Guard the safety contract directly: "allow" is reachable from exactly one action.
    assert [a for a, d in hook._DECISION_BY_ACTION.items() if d == "allow"] == ["approve"]
    # Every active/passive rule action the engine can emit has an explicit mapping.
    assert set(hook._DECISION_BY_ACTION) >= yolo_rules.RULE_ACTIONS
