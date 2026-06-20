# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Central tmux-facing API for visible Claude/Codex terminal panes."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any
from typing import Callable

from .approvals import blank_prompt_state
from .approvals import hybrid_approval_prompt_state
from .common import SessionInfo
from .prompt_detector import agent_screen_state
from .prompt_detector import approval_prompt_state
from .tmux_utils import cmd_error
from .tmux_utils import tmux_capture_pane
from .tmux_utils import tmux_capture_pane_styled
from .tmux_utils import tmux_clear_input
from .tmux_utils import tmux_paste_text
from .tmux_utils import tmux_run
from .transcripts import session_transcript_activity_state


ANSI_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
DEFAULT_IDLE_SUGGESTION_TEXTS = {
    "commit the DYN_PARSER_DEBUG change",
    "Summarize recent commits",
}
CLIENT_FILENAME_BASES = {
    "claude": "claude-code",
    "codex": "codex-cli",
    "generic": "generic-agent",
    "unknown": "generic-agent",
}

CaptureFunc = Callable[..., str | None]
ClearFunc = Callable[..., subprocess.CompletedProcess[str]]
PasteFunc = Callable[..., subprocess.CompletedProcess[str]]
PromptClassifier = Callable[[str, str, str | None, str], dict[str, Any]]
ScreenClassifier = Callable[[str, str | None], dict[str, Any]]


def strip_ansi_sgr(text: str) -> str:
    return ANSI_SGR_RE.sub("", text)


def agent_client_version_slug(agent: str, version: str = "") -> str:
    agent_key = re.sub(r"[^a-z0-9]+", "-", str(agent or "").strip().lower()).strip("-")
    version_text = str(version or "").strip().lower()
    version_key = re.sub(r"[^a-z0-9.]+", "-", version_text).strip("-")

    if agent_key not in CLIENT_FILENAME_BASES or agent_key in {"generic", "unknown"}:
        if version_key.startswith("claude-code"):
            agent_key = "claude"
        elif version_key.startswith("codex-cli"):
            agent_key = "codex"
        elif version_key.startswith("generic-agent"):
            agent_key = "generic"
    base = CLIENT_FILENAME_BASES.get(agent_key, version_key or "unknown")

    if "synthetic" in version_text:
        return f"{base}-synthetic"

    dotted_version = re.search(r"\d+(?:\.\d+)+", version_text)
    if dotted_version:
        return f"{base}-{dotted_version.group(0)}"
    underscored_version = re.search(r"\d+(?:_\d+)+", version_text)
    if underscored_version:
        return f"{base}-{underscored_version.group(0).replace('_', '.')}"

    if version_key:
        for known_base in CLIENT_FILENAME_BASES.values():
            if version_key == known_base or version_key.startswith(f"{known_base}-"):
                return version_key
    return base


def ansi_sgr_has_param(text: str, param: str) -> bool:
    for match in ANSI_SGR_RE.finditer(text):
        if param in {part for part in match.group(1).split(";") if part}:
            return True
    return False


def default_prompt_classifier(prompt_target: str, visible_text: str, pane_text: str | None = None, prompt_source: str = "hybrid") -> dict[str, Any]:
    return hybrid_approval_prompt_state(prompt_target, visible_text, pane_text, prompt_source=prompt_source)


def visible_prompt_classifier(_prompt_target: str, visible_text: str, pane_text: str | None = None, _prompt_source: str = "pane") -> dict[str, Any]:
    return approval_prompt_state(visible_text, pane_text)


def default_screen_classifier(visible_text: str, pane_target: str | None = None) -> dict[str, Any]:
    return dict(agent_screen_state(visible_text, pane_target=pane_target))


@dataclass(frozen=True)
class AgentTuiTarget:
    target: str
    session: str = ""
    agent_kind: str = ""

    @classmethod
    def from_value(cls, value: str | dict[str, Any]) -> "AgentTuiTarget":
        if isinstance(value, dict):
            pane_target = str(value.get("pane_target") or value.get("target") or value.get("session") or "").strip()
            return cls(
                target=pane_target,
                session=str(value.get("session") or "").strip(),
                agent_kind=str(value.get("agent_kind") or "").strip().lower(),
            )
        return cls(target=str(value or "").strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "session": self.session,
            "agent_kind": self.agent_kind,
        }


@dataclass(frozen=True)
class AgentTuiCursor:
    x: int = 0
    y: int = 0
    character: str = ""
    pane_in_mode: bool = False
    current_command: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "x": self.x,
            "y": self.y,
            "character": self.character,
            "pane_in_mode": self.pane_in_mode,
            "current_command": self.current_command,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class AgentTuiCapture:
    target: str
    visible_text: str = ""
    pane_text: str = ""
    styled_text: str = ""
    current_command: str = ""
    cursor: AgentTuiCursor = AgentTuiCursor()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target": self.target,
            "visible_text": self.visible_text,
            "pane_text": self.pane_text,
            "styled_text": self.styled_text,
            "current_command": self.current_command,
            "cursor": self.cursor.as_dict(),
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class AgentComposerState:
    key: str
    text: str = ""
    detected_text: str = ""
    prompt_suggestion: bool = False
    evidence: str = ""

    @property
    def accepting(self) -> bool:
        return self.key in {"empty", "ghost"}

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "text": self.text,
            "detected_text": self.detected_text,
            "prompt_suggestion": self.prompt_suggestion,
            "accepting": self.accepting,
        }
        if self.evidence:
            payload["evidence"] = self.evidence
        return payload


@dataclass(frozen=True)
class AgentPaneState:
    target: str
    prompt: dict[str, Any]
    screen: dict[str, Any]
    composer: AgentComposerState
    cursor: AgentTuiCursor
    capture: AgentTuiCapture
    agent_kind: str = ""
    attention_kind: str = ""
    attention_label: str = ""
    display: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    reason_code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "prompt": dict(self.prompt),
            "screen": dict(self.screen),
            "composer": self.composer.as_dict(),
            "cursor": self.cursor.as_dict(),
            "agent_kind": self.agent_kind,
            "attention_kind": self.attention_kind,
            "attention_label": self.attention_label,
            "display": dict(self.display or {}),
            "approval": dict(self.approval or {}),
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class AgentTuiClearResult:
    ok: bool
    cleared: bool
    detected_text: str = ""
    remaining_text: str = ""
    remaining_placeholder: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "cleared": self.cleared,
            "detected_text": self.detected_text,
        }
        if self.remaining_text:
            payload["remaining_text"] = self.remaining_text
        if self.remaining_placeholder:
            payload["remaining_placeholder"] = True
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class AgentTuiSendResult:
    ok: bool
    sent: bool
    pasted: bool = False
    cleared: bool = False
    clear_result: AgentTuiClearResult = AgentTuiClearResult(ok=True, cleared=False)
    reason_code: str = ""
    error: str = ""
    returncode: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "sent": self.sent,
            "pasted": self.pasted,
            "cleared": self.cleared,
            "clear": self.clear_result.as_dict(),
            "reason_code": self.reason_code,
        }
        if self.error:
            payload["error"] = self.error
        if self.returncode is not None:
            payload["returncode"] = self.returncode
        return payload


def normalized_prompt_state(prompt: dict[str, Any] | None = None) -> dict[str, Any]:
    state = blank_prompt_state()
    if prompt:
        state.update(prompt)
    return state


def composer_text_is_idle_placeholder(text: str, *, prompt_suggestion: bool = False, idle_suggestions: set[str] | None = None) -> bool:
    candidate = " ".join(str(text or "").split()).strip()
    if not candidate:
        return False
    if re.fullmatch(r'Try\s+(?:"[^"\n]{1,200}"|“[^“”\n]{1,200}”)', candidate):
        return True
    if re.fullmatch(r"Implement\s+\{[^{}\n]{1,80}\}", candidate):
        return True
    suggestions = idle_suggestions if idle_suggestions is not None else DEFAULT_IDLE_SUGGESTION_TEXTS
    return prompt_suggestion and candidate in suggestions


_COMPOSER_FOOTER_PREFIXES = ("▶▶", "⏵⏵", "⏸", "⏺", "gpt-", "claude ")


def _composer_candidate(visible_text: str) -> tuple[str, bool, str, int]:
    raw_lines = str(visible_text or "").splitlines()[-80:]
    plain_raw_lines = [strip_ansi_sgr(line) for line in raw_lines]
    lines = [line.replace("\xa0", " ") for line in plain_raw_lines]
    footer_index = -1
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].strip()
        if stripped.startswith(_COMPOSER_FOOTER_PREFIXES):
            footer_index = index
            break
    if footer_index < 0:
        return "", False, "", 0
    prompt_index = -1
    first_line = ""
    prompt_suggestion = False
    text_start_col = 0
    start_index = max(0, footer_index - 12)
    for index in range(footer_index - 1, start_index - 1, -1):
        raw_line = raw_lines[index]
        plain_raw_line = plain_raw_lines[index]
        line = lines[index]
        match = re.match(r"^\s*[❯›>](?P<gap>[ \xa0]+)(?P<text>\S.*)$", plain_raw_line)
        if not match:
            continue
        if re.match(r"^\s*[❯›>]\s*\d+[.:]\s+\S", line):
            continue
        prompt_index = index
        first_line = match.group("text").replace("\xa0", " ").strip()
        prompt_suggestion = "\xa0" in match.group("gap") or ansi_sgr_has_param(raw_line, "2")
        text_start_col = match.start("text")
        break
    if prompt_index < 0 or not first_line:
        return "", False, "", 0
    block = [first_line]
    for line in lines[prompt_index + 1:footer_index]:
        stripped = line.strip()
        if re.match(r"^[─━╌╍]{3,}$", stripped):
            break
        if stripped.startswith((*_COMPOSER_FOOTER_PREFIXES, "new task?")):
            break
        if stripped.startswith(("●", "✻", "⎿", "⤷")) or re.match(r"^(Ran|Bash|Write|Read|Edit|Update|Search)\b", stripped):
            return "", False, "", 0
        block.append(stripped)
    parts = [part for part in block if part]
    composer_text = " ".join(parts).strip()
    return composer_text, prompt_suggestion, composer_text, text_start_col


def visible_composer_text(visible_text: str) -> str:
    candidate, prompt_suggestion, _raw_candidate, _text_start_col = _composer_candidate(visible_text)
    if composer_text_is_idle_placeholder(candidate, prompt_suggestion=prompt_suggestion):
        return ""
    if prompt_suggestion:
        return ""
    return candidate


def _cursor_is_after_candidate(capture: AgentTuiCapture, candidate: str, text_start_col: int) -> bool:
    if not candidate or capture.cursor.error:
        return False
    # Claude can keep the NBSP/suggestion styling after the user starts typing into the same row.
    # In that case the cursor advances to the end of the real draft; a true ghost suggestion keeps the
    # cursor parked at the candidate's first character.
    return capture.cursor.x >= text_start_col + len(candidate)


def read_composer_state(capture_or_text: AgentTuiCapture | str) -> AgentComposerState:
    visible_text = capture_or_text.visible_text if isinstance(capture_or_text, AgentTuiCapture) else str(capture_or_text or "")
    candidate, prompt_suggestion, raw_candidate, text_start_col = _composer_candidate(visible_text)
    if not candidate:
        return AgentComposerState(key="empty")
    if prompt_suggestion and isinstance(capture_or_text, AgentTuiCapture) and _cursor_is_after_candidate(capture_or_text, candidate, text_start_col):
        return AgentComposerState(key="draft", text=candidate, detected_text=candidate, prompt_suggestion=False, evidence="cursor-after-suggestion-text")
    if composer_text_is_idle_placeholder(candidate, prompt_suggestion=prompt_suggestion):
        return AgentComposerState(key="ghost", text=raw_candidate, detected_text="", prompt_suggestion=prompt_suggestion, evidence="idle-placeholder")
    if prompt_suggestion:
        return AgentComposerState(key="ghost", text=raw_candidate, detected_text="", prompt_suggestion=True, evidence="suggestion-style")
    return AgentComposerState(key="draft", text=candidate, detected_text=candidate, prompt_suggestion=False)


def cursor_state(target: str, *, display_func: Callable[[str], subprocess.CompletedProcess[str]] | None = None) -> AgentTuiCursor:
    target_text = str(target or "").strip()
    if not target_text:
        return AgentTuiCursor(error="target pane is missing")
    if display_func is None:
        display_func = lambda pane_target: tmux_run("display-message", "-p", "-t", pane_target, "#{cursor_x}\t#{cursor_y}\t#{cursor_character}\t#{pane_in_mode}\t#{pane_current_command}", check=False)
    result = display_func(target_text)
    if result.returncode != 0:
        return AgentTuiCursor(error=cmd_error(result, "tmux display-message failed"))
    parts = result.stdout.rstrip("\n").split("\t")
    while len(parts) < 5:
        parts.append("")
    x_text, y_text, character, pane_in_mode, current_command = parts[:5]
    try:
        x = int(x_text)
        y = int(y_text)
    except ValueError:
        return AgentTuiCursor(character=character, pane_in_mode=pane_in_mode == "1", current_command=current_command, error="tmux cursor output was not numeric")
    return AgentTuiCursor(x=x, y=y, character=character, pane_in_mode=pane_in_mode == "1", current_command=current_command)


def capture_agent_pane(
    target: str | dict[str, Any],
    *,
    visible_only: bool = True,
    styled: bool = True,
    include_cursor: bool = True,
    capture_func: CaptureFunc = tmux_capture_pane,
    capture_styled_func: CaptureFunc = tmux_capture_pane_styled,
    cursor_func: Callable[..., AgentTuiCursor] = cursor_state,
) -> AgentTuiCapture:
    tui_target = AgentTuiTarget.from_value(target)
    if not tui_target.target:
        return AgentTuiCapture(target="", error="target pane is missing")
    visible_text = capture_func(tui_target.target, visible_only=visible_only)
    if visible_text is None:
        return AgentTuiCapture(target=tui_target.target, error="failed to capture pane")
    pane_text = "" if visible_only else visible_text
    styled_text = ""
    if styled:
        styled_capture = capture_styled_func(tui_target.target, visible_only=visible_only) or ""
        if styled_capture:
            styled_plain = " ".join(strip_ansi_sgr(styled_capture).split())
            visible_plain = " ".join(str(visible_text or "").split())
            if not visible_plain or styled_plain == visible_plain:
                styled_text = styled_capture
    cursor = cursor_func(tui_target.target) if include_cursor else AgentTuiCursor()
    source_text = styled_text or str(visible_text or "")
    return AgentTuiCapture(
        target=tui_target.target,
        visible_text=source_text,
        pane_text=pane_text,
        styled_text=styled_text,
        current_command=cursor.current_command,
        cursor=cursor,
    )


def visible_composer_source(
    pane_target: str,
    visible_text: str = "",
    *,
    capture_styled_func: CaptureFunc = tmux_capture_pane_styled,
) -> str:
    try:
        styled_text = capture_styled_func(pane_target, visible_only=True) or ""
    except (OSError, subprocess.SubprocessError):
        styled_text = ""
    if styled_text and visible_text:
        styled_plain = " ".join(strip_ansi_sgr(styled_text).split())
        visible_plain = " ".join(str(visible_text or "").split())
        if styled_plain != visible_plain:
            styled_text = ""
    return styled_text or str(visible_text or "")


def _attention_for_state(prompt_state: dict[str, Any], screen_state: dict[str, Any]) -> tuple[str, str]:
    if prompt_state.get("visible") or screen_state.get("key") == "approval":
        return "approval", "YOLO?"
    if screen_state.get("key") == "needs-input":
        return "question", "ASK?"
    if screen_state.get("key") == "working":
        return "working", "RUN"
    return "", ""


def _agent_kind_for_state(tui_target: AgentTuiTarget, prompt_state: dict[str, Any], screen_state: dict[str, Any]) -> str:
    for candidate in [tui_target.agent_kind, prompt_state.get("agent"), screen_state.get("agent")]:
        text = str(candidate or "").strip().lower()
        if text and text != "unknown":
            return text
    return ""


def _display_fields(prompt_state: dict[str, Any], screen_state: dict[str, Any], attention_kind: str, attention_label: str) -> dict[str, Any]:
    return {
        "screen_key": str(screen_state.get("key") or ""),
        "attention_kind": attention_kind,
        "attention_label": attention_label,
        "question_text": str(screen_state.get("question_text") or prompt_state.get("question_text") or screen_state.get("text") or prompt_state.get("text") or ""),
        "prompt_kind": str(screen_state.get("prompt_kind") or prompt_state.get("prompt_kind") or ""),
        "options": screen_state.get("options") or prompt_state.get("options") or [],
        "selected_option": int(screen_state.get("selected_option") or prompt_state.get("selected_option") or 0),
        "prompt_hash": str(screen_state.get("prompt_hash") or prompt_state.get("hash") or prompt_state.get("signature") or ""),
        "evidence_lines": screen_state.get("evidence_lines") or prompt_state.get("evidence_lines") or [],
    }


def _approval_fields(prompt_state: dict[str, Any]) -> dict[str, Any]:
    visible = bool(prompt_state.get("visible"))
    command = str(prompt_state.get("command") or "")
    action = str(prompt_state.get("action") or "")
    prompt_text = str(prompt_state.get("rule_input_text") or prompt_state.get("question_text") or prompt_state.get("text") or "")
    return {
        "approval_visible": visible,
        "approval_type": str(prompt_state.get("type") or ""),
        "approval_action": action,
        "selected_option": int(prompt_state.get("selected_option") or 0),
        "command": command,
        "dangerous": bool(prompt_state.get("dangerous")),
        "risk": "dangerous" if prompt_state.get("dangerous") else "",
        "rule_input_text": command or prompt_text or action,
        "prompt_hash": str(prompt_state.get("hash") or prompt_state.get("signature") or ""),
        "source": "pane" if visible else "",
    }


def _reason_code(prompt_state: dict[str, Any], screen_state: dict[str, Any]) -> str:
    screen_key = str(screen_state.get("key") or "")
    if prompt_state.get("visible") or screen_key == "approval":
        return "approval"
    if screen_key == "needs-input":
        return "needs-input"
    if screen_key == "working":
        return "busy"
    if screen_key == "input-draft":
        return "draft-clearable"
    if screen_key in {"idle", "done"}:
        return screen_key
    if screen_key in {"disconnected", "error"}:
        return screen_key
    return screen_key or "unknown"


def classify_agent_pane(
    target: str | dict[str, Any],
    *,
    session: str = "",
    discovered_sessions: dict[str, SessionInfo] | None = None,
    prompt_source: str = "hybrid",
    include_composer: bool = False,
    include_cursor: bool | None = None,
    include_transcript_activity: bool = True,
    capture_full_for_bash: bool = True,
    capture_func: CaptureFunc = tmux_capture_pane,
    capture_styled_func: CaptureFunc = tmux_capture_pane_styled,
    cursor_func: Callable[..., AgentTuiCursor] = cursor_state,
    prompt_classifier: PromptClassifier = default_prompt_classifier,
    screen_classifier: ScreenClassifier = default_screen_classifier,
    transcript_classifier: Callable[[SessionInfo | None], dict[str, Any]] = session_transcript_activity_state,
    discover_sessions_func: Callable[[list[str]], tuple[dict[str, SessionInfo], list[str]]] | None = None,
) -> AgentPaneState:
    tui_target = AgentTuiTarget.from_value(target)
    prompt_target = session or tui_target.session or tui_target.target
    capture_cursor = include_composer if include_cursor is None else include_cursor
    try:
        capture = capture_agent_pane(
            tui_target.target,
            visible_only=True,
            styled=include_composer,
            include_cursor=capture_cursor,
            capture_func=capture_func,
            capture_styled_func=capture_styled_func,
            cursor_func=cursor_func,
        )
        if not capture.ok:
            prompt = normalized_prompt_state()
            prompt["error"] = capture.error
            screen = {"key": "disconnected", "text": capture.error}
            return AgentPaneState(
                target=tui_target.target,
                prompt=prompt,
                screen=screen,
                composer=AgentComposerState(key="unknown"),
                cursor=capture.cursor,
                capture=capture,
                agent_kind=tui_target.agent_kind,
                display=_display_fields(prompt, screen, "", ""),
                approval=_approval_fields(prompt),
                reason_code="disconnected",
            )
        prompt_state = prompt_classifier(prompt_target, capture.visible_text, None, prompt_source)
        if capture_full_for_bash and prompt_state.get("visible") and prompt_state.get("type") == "bash":
            pane_text = capture_func(tui_target.target, visible_only=False) or capture.visible_text
            prompt_state = prompt_classifier(prompt_target, capture.visible_text, pane_text, prompt_source)
        screen_state = screen_classifier(capture.visible_text, tui_target.target)
        composer = read_composer_state(capture) if include_composer else AgentComposerState(key="unknown")
        if include_composer and screen_state.get("key") == "idle" and composer.key == "draft":
            screen_state = {
                "key": "input-draft",
                "text": "target input box already contains unsent text",
                "detected_text": composer.detected_text,
            }
        if include_transcript_activity and screen_state.get("key") == "idle":
            infos = discovered_sessions
            if infos is None and discover_sessions_func is not None and prompt_target:
                infos, _errors = discover_sessions_func([prompt_target])
            info = infos.get(prompt_target) if infos is not None else None
            transcript_state = transcript_classifier(info)
            if transcript_state.get("key") != "idle":
                screen_state = dict(transcript_state)
        attention_kind, attention_label = _attention_for_state(prompt_state, screen_state)
        agent_kind = _agent_kind_for_state(tui_target, prompt_state, screen_state)
        normalized_prompt = normalized_prompt_state(prompt_state)
        return AgentPaneState(
            target=tui_target.target,
            prompt=normalized_prompt,
            screen=dict(screen_state),
            composer=composer,
            cursor=capture.cursor,
            capture=capture,
            agent_kind=agent_kind,
            attention_kind=attention_kind,
            attention_label=attention_label,
            display=_display_fields(normalized_prompt, screen_state, attention_kind, attention_label),
            approval=_approval_fields(normalized_prompt),
            reason_code=_reason_code(normalized_prompt, screen_state),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        prompt = normalized_prompt_state()
        prompt["error"] = str(exc)
        screen = {"key": "error", "text": str(exc)}
        return AgentPaneState(
            target=tui_target.target,
            prompt=prompt,
            screen=screen,
            composer=AgentComposerState(key="unknown"),
            cursor=AgentTuiCursor(error=str(exc)),
            capture=AgentTuiCapture(target=tui_target.target, error=str(exc)),
            agent_kind=tui_target.agent_kind,
            display=_display_fields(prompt, screen, "", ""),
            approval=_approval_fields(prompt),
            reason_code="error",
        )


def text_still_in_composer(
    target: str | dict[str, Any],
    text: str,
    *,
    wait_seconds: float = 0.8,
    poll_seconds: float = 0.1,
    capture_func: CaptureFunc = tmux_capture_pane,
    capture_styled_func: CaptureFunc = tmux_capture_pane_styled,
) -> bool:
    tui_target = AgentTuiTarget.from_value(target)
    needle = " ".join(str(text or "").split())
    if not tui_target.target or not needle:
        return False
    deadline = time.monotonic() + max(0.0, wait_seconds)
    pause = threading.Event()
    while True:
        try:
            visible_text = visible_composer_source(
                tui_target.target,
                capture_func(tui_target.target, visible_only=True) or "",
                capture_styled_func=capture_styled_func,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        pending = " ".join(visible_composer_text(visible_text).split())
        if not pending:
            return False
        prefix_len = min(120, len(needle))
        if needle in pending or pending in needle or needle[:prefix_len] in pending:
            if time.monotonic() >= deadline:
                return True
            pause.wait(min(max(0.01, poll_seconds), max(0.0, deadline - time.monotonic())))
            continue
        return False


def clear_composer(
    target: str | dict[str, Any],
    *,
    wait_seconds: float = 0.8,
    poll_seconds: float = 0.1,
    capture_func: CaptureFunc = tmux_capture_pane,
    capture_styled_func: CaptureFunc = tmux_capture_pane_styled,
    cursor_func: Callable[..., AgentTuiCursor] = cursor_state,
    clear_func: ClearFunc = tmux_clear_input,
) -> AgentTuiClearResult:
    tui_target = AgentTuiTarget.from_value(target)
    if not tui_target.target:
        return AgentTuiClearResult(ok=False, cleared=False, error="target pane is missing")

    def composer_state_for_target() -> AgentComposerState:
        visible_text = visible_composer_source(
            tui_target.target,
            capture_func(tui_target.target, visible_only=True) or "",
            capture_styled_func=capture_styled_func,
        )
        return read_composer_state(AgentTuiCapture(target=tui_target.target, visible_text=visible_text, cursor=cursor_func(tui_target.target)))

    try:
        composer = composer_state_for_target()
    except (OSError, subprocess.SubprocessError) as exc:
        return AgentTuiClearResult(ok=False, cleared=False, error=str(exc))
    detected = composer.detected_text
    if composer.key == "ghost":
        return AgentTuiClearResult(ok=True, cleared=False)
    if composer.key != "draft" or not detected:
        return AgentTuiClearResult(ok=True, cleared=False)
    result = clear_func(tui_target.target)
    if result.returncode != 0:
        return AgentTuiClearResult(
            ok=False,
            cleared=False,
            detected_text=detected,
            error=cmd_error(result, "tmux send-keys C-u failed"),
        )
    deadline = time.monotonic() + max(0.0, wait_seconds)
    pause = threading.Event()
    while True:
        try:
            remaining_state = composer_state_for_target()
        except (OSError, subprocess.SubprocessError) as exc:
            return AgentTuiClearResult(ok=False, cleared=False, detected_text=detected, error=str(exc))
        if remaining_state.key == "ghost":
            return AgentTuiClearResult(ok=True, cleared=True, detected_text=detected)
        if remaining_state.key != "draft":
            return AgentTuiClearResult(ok=True, cleared=True, detected_text=detected)
        if time.monotonic() >= deadline:
            return AgentTuiClearResult(
                ok=False,
                cleared=False,
                detected_text=detected,
                remaining_text=remaining_state.detected_text,
                error="target input box did not clear",
            )
        pause.wait(min(max(0.01, poll_seconds), max(0.0, deadline - time.monotonic())))


def send_prompt(
    target: str | dict[str, Any],
    text: str,
    *,
    submit: bool = True,
    clear_existing: bool = True,
    verify_submit: bool = True,
    preflight: bool = True,
    preflight_state: AgentPaneState | None = None,
    clear_wait_seconds: float = 0.8,
    clear_poll_seconds: float = 0.1,
    verify_wait_seconds: float = 0.8,
    verify_poll_seconds: float = 0.1,
    paste_func: PasteFunc = tmux_paste_text,
    capture_func: CaptureFunc = tmux_capture_pane,
    capture_styled_func: CaptureFunc = tmux_capture_pane_styled,
    cursor_func: Callable[..., AgentTuiCursor] = cursor_state,
    clear_func: ClearFunc = tmux_clear_input,
) -> AgentTuiSendResult:
    tui_target = AgentTuiTarget.from_value(target)
    if not tui_target.target:
        return AgentTuiSendResult(ok=False, sent=False, reason_code="disconnected", error="target pane is missing")
    if preflight:
        state = preflight_state or classify_agent_pane(
            target,
            include_composer=True,
            include_cursor=True,
            include_transcript_activity=False,
            capture_func=capture_func,
            capture_styled_func=capture_styled_func,
            cursor_func=cursor_func,
        )
        agent_kind = _agent_kind_for_state(tui_target, state.prompt, state.screen)
        if agent_kind not in {"claude", "codex"}:
            return AgentTuiSendResult(ok=False, sent=False, reason_code="not-agent", error="target pane does not have a detected Claude or Codex agent")
        reason = _reason_code(state.prompt, state.screen)
        if reason in {"approval", "needs-input", "busy", "disconnected", "error"}:
            messages = {
                "approval": "target agent is at an approval prompt",
                "needs-input": "target agent is asking a question",
                "busy": "target agent is still working",
                "disconnected": str(state.screen.get("text") or "target pane is not reachable"),
                "error": str(state.screen.get("text") or "target pane is not reachable"),
            }
            return AgentTuiSendResult(ok=False, sent=False, reason_code=reason, error=messages[reason])
        if reason == "draft-clearable" and not clear_existing:
            return AgentTuiSendResult(ok=False, sent=False, reason_code="draft-clearable", error="target input box already contains unsent text")
    clear_result = AgentTuiClearResult(ok=True, cleared=False)
    if clear_existing:
        clear_result = clear_composer(
            tui_target.target,
            wait_seconds=clear_wait_seconds,
            poll_seconds=clear_poll_seconds,
            capture_func=capture_func,
            capture_styled_func=capture_styled_func,
            cursor_func=cursor_func,
            clear_func=clear_func,
        )
        if not clear_result.ok:
            return AgentTuiSendResult(
                ok=False,
                sent=False,
                cleared=clear_result.cleared,
                clear_result=clear_result,
                reason_code="draft-unclearable",
                error=clear_result.error or "target input box did not clear",
            )
    result = paste_func(tui_target.target, text, submit=submit)
    if result.returncode != 0:
        return AgentTuiSendResult(
            ok=False,
            sent=False,
            pasted=False,
            cleared=clear_result.cleared,
            clear_result=clear_result,
            reason_code="tmux-paste-failed",
            error=cmd_error(result, "tmux paste-buffer failed"),
            returncode=result.returncode,
        )
    if submit and verify_submit and text_still_in_composer(
        tui_target.target,
        text,
        wait_seconds=verify_wait_seconds,
        poll_seconds=verify_poll_seconds,
        capture_func=capture_func,
        capture_styled_func=capture_styled_func,
    ):
        return AgentTuiSendResult(
            ok=False,
            sent=False,
            pasted=True,
            cleared=clear_result.cleared,
            clear_result=clear_result,
            reason_code="unsubmitted",
            error="pasted text is still in the target input box after Return; target did not submit it",
            returncode=result.returncode,
        )
    return AgentTuiSendResult(
        ok=True,
        sent=True,
        pasted=True,
        cleared=clear_result.cleared,
        clear_result=clear_result,
        reason_code="submitted" if submit else "pasted",
        returncode=result.returncode,
    )


def wait_until_accepting(
    target: str | dict[str, Any],
    *,
    timeout: float,
    poll: float = 0.25,
    classify_func: Callable[[str | dict[str, Any]], AgentPaneState] | None = None,
) -> AgentPaneState:
    if classify_func is None:
        classify_func = lambda candidate: classify_agent_pane(candidate, include_composer=True)
    deadline = time.monotonic() + max(0.0, timeout)
    pause = threading.Event()
    last_state = classify_func(target)
    while True:
        screen_key = str(last_state.screen.get("key") or "")
        if screen_key in {"idle", "input-draft"} and not last_state.prompt.get("visible"):
            return last_state
        if time.monotonic() >= deadline:
            return last_state
        pause.wait(min(max(0.01, poll), max(0.0, deadline - time.monotonic())))
        last_state = classify_func(target)


__all__ = [
    "AgentComposerState",
    "AgentPaneState",
    "AgentTuiCapture",
    "AgentTuiClearResult",
    "AgentTuiCursor",
    "AgentTuiSendResult",
    "AgentTuiTarget",
    "ansi_sgr_has_param",
    "capture_agent_pane",
    "classify_agent_pane",
    "clear_composer",
    "composer_text_is_idle_placeholder",
    "cursor_state",
    "default_prompt_classifier",
    "default_screen_classifier",
    "normalized_prompt_state",
    "read_composer_state",
    "send_prompt",
    "strip_ansi_sgr",
    "text_still_in_composer",
    "visible_composer_source",
    "visible_composer_text",
    "visible_prompt_classifier",
    "wait_until_accepting",
]
