# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The approval-prompt detection pipeline, shared by every caller.

This is the ONE place that turns a captured pane (and, in hybrid mode, the agent transcript) into a
normalized approval-prompt state. It previously lived in the root `auto_approve_tmux` CLI script, which
forced `yolomux_lib` modules (app.py read-path, the auto-approve worker act-path) to import a top-level
script and reach detection through that indirection — an inverted dependency, and two callers reaching
the same logic by different routes. Detection now lives here in the library; the root script re-exports
these names for its own CLI use, and the lib callers import them directly.

Keep the pane-first / transcript-fallback order synced with
docs/specs/AGENT_PROMPTS_AND_COMMUNICATION.md#recommended-channel-order.
"""

from __future__ import annotations

from .prompt_detector import action_for_bash_prompt
from .prompt_detector import action_for_prompt
from .prompt_detector import approval_prompt_state
from .prompt_detector import selected_prompt_option
from .prompt_detector import yes_is_selected
from .sessions import discover_sessions
from .transcripts import transcript_pending_approval


# If Enter is missed, a current prompt should not be stuck forever behind the
# de-dup hash. Retry only after the exact prompt remains visible briefly.
PROMPT_RETRY_SECONDS = 5.0
PROMPT_SOURCE_CHOICES = ("pane", "hybrid")
DEFAULT_PROMPT_SOURCE = "hybrid"


def target_session_name(target: str) -> str:
    if target.startswith("%"):
        return ""
    return target.split(":", 1)[0]


def blank_prompt_state(reason: str = "") -> dict[str, object]:
    state: dict[str, object] = {
        "visible": False,
        "type": "",
        "text": "",
        "yes_selected": False,
        "selected_option": 0,
        "action": "",
        "command": None,
        "dangerous": False,
        "hash": "",
        "source": "pane",
    }
    if reason:
        state["reason"] = reason
    return state


def transcript_approval_prompt_state(target: str, visible_text: str) -> dict[str, object]:
    selected_option = selected_prompt_option(visible_text)
    yes_selected = yes_is_selected(visible_text)
    if not yes_selected and selected_option <= 0:
        return blank_prompt_state("transcript candidate ignored: no visible selectable prompt")
    session_name = target_session_name(target)
    if not session_name:
        return blank_prompt_state("transcript candidate ignored: tmux pane target has no session name")
    infos, errors = discover_sessions([session_name])
    info = infos.get(session_name)
    if not info:
        reason = "; ".join(errors) if errors else "session metadata unavailable"
        return blank_prompt_state(f"transcript candidate ignored: {reason}")
    agent = next((item for item in info.agents if item.transcript), None)
    if agent is None:
        return blank_prompt_state("transcript candidate ignored: no agent transcript found")
    state = transcript_pending_approval(agent.transcript, agent.kind)
    if state.get("visible") is not True:
        reason = str(state.get("reason") or "no recent pending approval in transcript")
        return blank_prompt_state(reason)
    prompt_type = str(state.get("type") or "")
    action = action_for_bash_prompt(visible_text) if prompt_type == "bash" else action_for_prompt(prompt_type)
    state.update({
        "visible": True,
        "yes_selected": yes_selected,
        "selected_option": selected_option,
        "action": action or "",
        "source": "transcript",
    })
    return state


def hybrid_approval_prompt_state(target: str, visible_text: str, pane_text: str | None = None, prompt_source: str = DEFAULT_PROMPT_SOURCE) -> dict[str, object]:
    pane_state = approval_prompt_state(visible_text, pane_text)
    pane_state["source"] = "pane"
    if pane_state.get("visible") is True or prompt_source == "pane":
        return pane_state
    transcript_state = transcript_approval_prompt_state(target, visible_text)
    if transcript_state.get("visible") is True:
        return transcript_state
    if transcript_state.get("reason"):
        pane_state["reason"] = transcript_state["reason"]
    return pane_state
