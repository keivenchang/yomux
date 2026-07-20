# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
from __future__ import annotations

"""Claude Code ``PreToolUse`` permission hook for YOLOmux.

Screen-scraping a drifting TUI to decide WHEN to type ``1``/Enter is fragile by
construction — every new CLI footer variant breaks the liveness guard, and a false
positive types into whatever pane is focused. The reliable fix is to change the
CHANNEL: Claude Code fires this hook BEFORE a tool runs, hands us the request as
structured JSON on stdin, and honors the permission decision we print on stdout — so
the decision is made on the EXACT command and returned programmatically, with zero
keystroke injection and zero dependence on footer/spinner/wrapping text.

This reuses the SAME rule engine the screen-scraper worker uses
(``yolo_rules.evaluate`` + the catastrophic ``hard_floor_decision``), so the hook's
auto-approve decision and the UI danger badge stay consistent — no new decision logic.

Fail-safe by construction (the conservative default the keystroke guard always had):
- Only a clean rule ``approve`` returns ``allow`` (the tool runs with no prompt).
- A rule ``block``/``decline`` and EVERY built-in hard-floor block returns ``deny``.
- Anything else — ``ask``/``notify``/``off``, an unknown action, an unparseable
  request, a non-``Bash`` tool, an empty command, a ruleset error, or dry-run — returns
  ``ask`` (the normal human prompt). NEVER ``allow``.
Claude also re-evaluates its own ``deny``/``ask`` permission rules regardless of this
hook, so the hook can only auto-approve what is otherwise allowed; it can never widen
past a deny. And if the hook errors or times out, Claude falls back to the human prompt.

Wire it up (a deliberate, user-level opt-in — it applies to every Claude session the
user starts under this ``$HOME``). Run ``python3 -m yolomux_lib.claude_permission_hook
--print-settings`` for the exact snippet, then merge it into ``~/.claude/settings.json``.
"""

import json
import sys

from . import yolo_rules

# yolo_rules action -> Claude permissionDecision. Only a clean "approve" auto-allows; "decline"
# and "block" deny; everything else (and any unknown action) defers to the human prompt via "ask".
_DECISION_BY_ACTION = {
    "approve": "allow",
    "decline": "deny",
    "block": "deny",
    "ask": "ask",
    "notify": "ask",
    "off": "ask",
}

_SETTINGS_SNIPPET = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 -m yolomux_lib.claude_permission_hook",
                        "timeout": 5,
                    }
                ],
            }
        ]
    }
}


def decide(payload: dict) -> tuple[str, str]:
    """Map a Claude ``PreToolUse`` request to a ``(permissionDecision, reason)`` pair.

    Only ``Bash`` requests (which carry a concrete ``command`` the shell ruleset can
    evaluate) are auto-decided; every other tool falls back to ``ask`` so a human
    approves it. The ``reason`` is empty for ``ask`` and names the matched rule otherwise.
    """
    if not isinstance(payload, dict):
        return "ask", ""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    # File-edit tools (Edit/Write) have no command for the shell ruleset; defer them to the
    # human prompt rather than guess. Keeps the hook scoped to the surface the engine can judge.
    if payload.get("tool_name") != "Bash" or not isinstance(command, str) or not command.strip():
        return "ask", ""

    session = payload.get("session_id")
    decision = yolo_rules.evaluate(
        command, "bash", agent="claude", session=session if isinstance(session, str) else ""
    )
    permission = _DECISION_BY_ACTION.get(decision.get("action"), "ask")
    if permission == "ask":
        return "ask", ""
    rule = decision.get("rule_name") or "default"
    source = decision.get("source") or ""
    verb = "auto-approved" if permission == "allow" else "blocked"
    reason = f"YOLOmux {verb} via rule '{rule}'" + (f" ({source})" if source else "")
    return permission, reason


def hook_response(permission: str, reason: str = "") -> dict:
    output: dict[str, object] = {"hookEventName": "PreToolUse", "permissionDecision": permission}
    if reason:
        output["permissionDecisionReason"] = reason
    return {"hookSpecificOutput": output}


def _read_payload(raw: str) -> dict:
    if not raw.strip():
        return {}
    # A malformed request is an expected abnormal input (not a bug to surface): treat it as
    # "can't tell" and let `decide` fall back to ask. Narrow catch — only the parse, not the engine.
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--print-settings" in argv:
        json.dump(_SETTINGS_SNIPPET, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    payload = _read_payload(sys.stdin.read())
    # If a non-PreToolUse event is wired to this hook, don't decide it — fall back to ask.
    if payload.get("hook_event_name") not in (None, "PreToolUse"):
        permission, reason = "ask", ""
    else:
        permission, reason = decide(payload)
    json.dump(hook_response(permission, reason), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
