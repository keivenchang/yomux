# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""YO!agent action intent parsing and lightweight policy helpers."""

from __future__ import annotations

import re
from typing import Any


SESSION_RE = re.compile(r"(?:(?:tmux\s+)?session|agent)\s+[`'\"]?([^`'\",\s]+)", re.IGNORECASE)
WAIT_THEN_RUN_RE = re.compile(r"\bwait-then-run\b\s+(.+)$", re.IGNORECASE | re.DOTALL)
THEN_RE = re.compile(r"\bthen\b\s+(.+)$", re.IGNORECASE | re.DOTALL)
CONFIRMATION_REQUEST_RE = re.compile(
    r"\b(?:ask\s+me\s+before|confirm\s+before|confirmation\s+before|preview|show\s+me\s+before|"
    r"do\s+not\s+send\s+yet|don't\s+send\s+yet|wait\s+for\s+(?:my\s+)?confirmation|manual\s+confirmation)\b",
    re.IGNORECASE,
)
RESULT_REQUEST_RE = re.compile(
    r"\b(?:and\s+)?(?:show|print|paste|return|get|fetch|tell|give)\s+(?:me\s+)?(?:the\s+)?"
    r"(?:result|output|answer|response|reply|what\s+it\s+says|what\s+it\s+returns)(?:\s+here)?\b"
    r"|\b(?:show|print|paste)\s+it\s+here\b"
    r"|\btell\s+me\s+what\s+it\s+says\b",
    re.IGNORECASE,
)
RESULT_OPT_OUT_RE = re.compile(
    r"\b(?:do\s+not|don't|dont)\s+(?:wait\s+for|return|show|print|paste|fetch|get|tell\s+me)\s+(?:the\s+)?"
    r"(?:result|output|answer|response|reply)\b"
    r"|\b(?:just|only)\s+send\s+it\b"
    r"|\bsend\s+it\s+(?:only|without\s+waiting)\b"
    r"|\bno\s+(?:result|output|answer|response|reply)\s+(?:needed|required)\b",
    re.IGNORECASE,
)
RESULT_OPT_OUT_TRAILING_RE = re.compile(
    r",?\s+(?:and\s+)?(?:do\s+not|don't|dont)\s+(?:wait\s+for|return|show|print|paste|fetch|get|tell\s+me)\s+(?:the\s+)?"
    r"(?:result|output|answer|response|reply)\.?$"
    r"|,?\s+(?:and\s+)?(?:just|only)\s+send\s+it\.?$"
    r"|,?\s+(?:and\s+)?send\s+it\s+(?:only|without\s+waiting)\.?$"
    r"|,?\s+(?:and\s+)?no\s+(?:result|output|answer|response|reply)\s+(?:needed|required)\.?$",
    re.IGNORECASE,
)
RESULT_TRAILING_RE = re.compile(
    r",?\s+(?:and\s+)?(?:show|print|paste|return|get|fetch|tell|give)\s+(?:me\s+)?(?:the\s+)?"
    r"(?:result|output|answer|response|reply|what\s+it\s+says|what\s+it\s+returns)(?:\s+here)?\.?$"
    r"|,?\s+(?:and\s+)?(?:show|print|paste)\s+it\s+here\.?$"
    r"|,?\s+(?:and\s+)?tell\s+me\s+what\s+it\s+says\.?$",
    re.IGNORECASE,
)
NOTIFY_ALL_IDLE_RE = re.compile(
    r"\b(?:notify|tell)\s+me\s+when\s+(?:all\s+sessions|everything|all\s+agents)\s+(?:are\s+)?(?:idle|idling|done|finished)\b",
    re.IGNORECASE,
)
NOTIFY_SESSION_IDLE_RE = re.compile(
    r"\b(?:notify|tell)\s+me\s+when\s+(?:(?:tmux\s+)?session|agent)\s+[`'\"]?(?P<session>[^`'\",\s]+)[`'\"]?\s+(?:is\s+|has\s+)?(?:idle|idling|done|finished|complete|completed)\b",
    re.IGNORECASE,
)
EXPLICIT_SHELL_COMMAND_RE = re.compile(r"(?:`[^`]+`|\b(?:run|execute|shell\s+command|terminal\s+command)\b)", re.IGNORECASE)
FENCED_TEXT_RE = re.compile(r"```(?:yaml|yml|markdown|md|text)?\s*\n(?P<text>[\s\S]*?)```", re.IGNORECASE)
SKILL_NAME_RE = re.compile(r"\b(?:yo!?skill|skill|context)\s+(?:named\s+|called\s+)?[`'\"]?([a-z][a-z0-9-]{1,63})[`'\"]?", re.IGNORECASE)
DESCRIPTION_RE = re.compile(r"\bdescription\s*:\s*(?P<description>.+)$", re.IGNORECASE | re.DOTALL)


def bare_session_target_pattern(session: str) -> str:
    return rf"(?<![A-Za-z0-9_.-])[`'\"]?{re.escape(str(session))}[`'\"]?(?![A-Za-z0-9_.-])"


def clean_action_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[`'\"]|[`'\"]$", "", text).strip()
    text = re.sub(r"\s*,?\s+and\s*$", "", text, flags=re.IGNORECASE).strip()
    cleaners = [
        r"^and\s+ask\s+(?:it\s+)?for\s+",
        r"^and\s+ask\s+(?:it\s+)?(?:to\s+)?(?:run\s+)?",
        r"^and\s+tell\s+(?:it\s+)?(?:to\s+)?(?:run\s+)?",
        r"^ask\s+(?:it\s+)?for\s+",
        r"^ask\s+(?:it\s+)?(?:to\s+)?(?:run\s+)?",
        r"^tell\s+(?:it\s+)?(?:to\s+)?(?:run\s+)?",
        r"^send\s+",
        r"^run\s+",
        r"^it\s+(?:to\s+)?(?:run\s+)?",
        r"^to\s+(?:run\s+)?",
        r"^for\s+",
        r"^command\s+",
    ]
    for pattern in cleaners:
        next_text = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE).strip()
        if next_text != text:
            text = next_text
            break
    text = RESULT_TRAILING_RE.sub("", text).strip()
    text = RESULT_OPT_OUT_TRAILING_RE.sub("", text).strip()
    return text.strip(" `'\".")


def normalize_agent_prompt_text(action_text: str, source_text: str) -> str:
    text = clean_action_text(action_text)
    if not text or EXPLICIT_SHELL_COMMAND_RE.search(str(source_text or "")):
        return text
    normalized = re.sub(r"^(?:a|an|the)\s+", "", text.lower()).strip()
    normalized = re.sub(r"\s+command$", "", normalized).strip()
    if normalized in {"date", "current date", "today's date", "todays date"}:
        return "tell me the date"
    if normalized in {"time", "current time", "what the time is", "what time it is"}:
        return "what time is it?"
    if normalized in {"status", "current status", "its status"}:
        return "what is your status?"
    patterns = [
        (r"^what\s+(?:has\s+it|it\s+has)\s+done(?P<tail>.*)$", "what have you done{tail}?"),
        (r"^what\s+(?:did\s+it\s+do|it\s+did)(?P<tail>.*)$", "what did you do{tail}?"),
        (r"^what\s+(?:is\s+it|it\s+is|it's)\s+doing(?P<tail>.*)$", "what are you doing{tail}?"),
        (r"^what\s+(?:is\s+it|it\s+is|it's)\s+working\s+on(?P<tail>.*)$", "what are you working on{tail}?"),
        (r"^what\s+(?:has\s+it|it\s+has)\s+been\s+working\s+on(?P<tail>.*)$", "what have you been working on{tail}?"),
        (r"^what\s+(?:has\s+it|it\s+has)\s+changed(?P<tail>.*)$", "what have you changed{tail}?"),
        (r"^what\s+(?:did\s+it\s+change|it\s+changed)(?P<tail>.*)$", "what did you change{tail}?"),
        (r"^what\s+(?:has\s+it|it\s+has)\s+completed(?P<tail>.*)$", "what have you completed{tail}?"),
        (r"^what\s+(?:did\s+it\s+complete|it\s+completed)(?P<tail>.*)$", "what did you complete{tail}?"),
    ]
    for pattern, replacement in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        tail = " ".join(match.group("tail").split()).strip(" ?.")
        return replacement.format(tail=f" {tail}" if tail else "")
    return text


def session_target_pattern(session: str) -> str:
    qualified = rf"(?:the\s+)?(?:(?:tmux\s+)?session|agent)\s+[`'\"]?{re.escape(str(session))}[`'\"]?"
    return rf"(?:{qualified}|{bare_session_target_pattern(session)})"


def extract_action_text_for_session(text: str, session: str) -> str:
    target = session_target_pattern(session)
    patterns = [
        rf"\b(?:ask|tell)\s+{target}\s+(?P<action>.+)$",
        rf"\bsend(?:ing)?\s+(?P<action>.+?)\s+(?:to|into)\s+{target}\b",
        rf"\bsend(?:ing)?\s+(?:to|into)\s+{target}\s*[:,-]?\s*(?P<action>.+)$",
        rf"\bsend(?:ing)?\s+(?P<action>.+?)\s+{target}\b",
        rf"\brun\s+(?P<action>.+?)\s+(?:in|on|for)\s+{target}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, str(text or ""), flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_action_text(match.group("action"))
    return ""


def session_mentions_in_order(text: str, known_sessions: list[str] | tuple[str, ...]) -> list[tuple[str, int]]:
    known = {str(item) for item in known_sessions}
    mentions: list[tuple[str, int]] = []
    for match in SESSION_RE.finditer(str(text or "")):
        candidate = match.group(1).strip()
        if not known or candidate in known:
            mentions.append((candidate, match.start()))
    for session in sorted(known, key=len, reverse=True):
        target = bare_session_target_pattern(session)
        for match in re.finditer(target, str(text or ""), flags=re.IGNORECASE):
            if any(existing == session and abs(start - match.start()) < 12 for existing, start in mentions):
                continue
            prefix = str(text or "")[:match.start()]
            if not re.search(r"\b(?:ask|tell|send|sending|wait\s+for)\s+$", prefix[-32:], flags=re.IGNORECASE):
                continue
            mentions.append((session, match.start()))
    return sorted(mentions, key=lambda item: item[1])


def extract_session_name(text: str, known_sessions: list[str] | tuple[str, ...]) -> str:
    known = {str(item) for item in known_sessions}
    for match in SESSION_RE.finditer(str(text or "")):
        candidate = match.group(1).strip()
        if candidate in known:
            return candidate
    for session in sorted(known, key=len, reverse=True):
        target = bare_session_target_pattern(session)
        patterns = [
            rf"\b(?:ask|tell)\s+{target}(?=$|[\s,;:.])",
            rf"\b(?:send|sending)\s+(?:to|into)\s+{target}(?=$|[\s,;:.])",
            rf"\bwait\s+for\s+{target}(?=$|[\s,;:.])",
        ]
        if any(re.search(pattern, str(text or ""), flags=re.IGNORECASE) for pattern in patterns):
            return session
    if not known:
        match = SESSION_RE.search(str(text or ""))
        return match.group(1).strip() if match else ""
    return ""


def recent_session_from_history(history: list[dict[str, str]], known_sessions: list[str] | tuple[str, ...]) -> str:
    for item in reversed(history or []):
        session = extract_session_name(str(item.get("content") or ""), known_sessions)
        if session:
            return session
    return ""


def action_confirmation_requested(text: str) -> bool:
    return bool(CONFIRMATION_REQUEST_RE.search(str(text or "")))


def action_result_requested(text: str) -> bool:
    return bool(RESULT_REQUEST_RE.search(str(text or "")))


def action_result_opted_out(text: str) -> bool:
    return bool(RESULT_OPT_OUT_RE.search(str(text or "")))


def parse_yoagent_action_intent(question: str, history: list[dict[str, str]], known_sessions: list[str] | tuple[str, ...]) -> dict[str, Any] | None:
    text = " ".join(str(question or "").split())
    if not text:
        return None
    lower = text.lower()
    wait_then = WAIT_THEN_RUN_RE.search(text)
    then = THEN_RE.search(text)
    session = extract_session_name(text, known_sessions) or recent_session_from_history(history, known_sessions)
    mentions = session_mentions_in_order(text, known_sessions)
    distinct_mentions: list[str] = []
    for item, _start in mentions:
        if item not in distinct_mentions:
            distinct_mentions.append(item)
    if then and len(distinct_mentions) >= 2:
        session = distinct_mentions[0]
        first_part = text[: then.start()]
        raw_action = extract_action_text_for_session(first_part, session)
        if raw_action:
            intent = {
                "type": "session_handoff",
                "session": session,
                "text": normalize_agent_prompt_text(raw_action, first_part),
                "submit": True,
                "return_result": True,
                "handoff": {
                    "source_session": session,
                    "session": distinct_mentions[1],
                    "instruction": clean_action_text(then.group(1)),
                },
            }
            if action_confirmation_requested(text):
                intent["requires_confirmation"] = True
            return intent
    if wait_then:
        raw_action = wait_then.group(1)
        action_type = "wait_then_send"
    elif "wait" in lower and then:
        raw_action = then.group(1)
        action_type = "wait_then_send"
    elif re.search(r"\b(?:ask|tell|send|sending|run)\b", lower) and session:
        raw_action = extract_action_text_for_session(text, session)
        if not raw_action:
            raw_action = re.sub(r"^.*?\b(?:ask|tell|send)\b", "", text, count=1, flags=re.IGNORECASE).strip()
        action_type = "send_prompt"
    else:
        return None
    action_text = normalize_agent_prompt_text(raw_action, text)
    if not action_text:
        return None
    if not session:
        return None
    intent = {
        "type": action_type,
        "session": session,
        "text": action_text,
        "submit": True,
    }
    if action_confirmation_requested(text):
        intent["requires_confirmation"] = True
    result_opted_out = action_result_opted_out(text)
    if not result_opted_out:
        intent["return_result"] = True
    if action_result_requested(text) and not result_opted_out:
        intent["return_result"] = True
    return intent


def parse_yoagent_job_intent(question: str, known_sessions: list[str] | tuple[str, ...]) -> dict[str, Any] | None:
    text = " ".join(str(question or "").split())
    if not text:
        return None
    if NOTIFY_ALL_IDLE_RE.search(text):
        return {"type": "notify_all_idle"}
    match = NOTIFY_SESSION_IDLE_RE.search(text)
    if not match:
        return None
    session = match.group("session").strip()
    if known_sessions and session not in {str(item) for item in known_sessions}:
        return None
    return {"type": "notify_session_idle", "session": session}


def redacted_action_text(text: str, limit: int = 4000) -> str:
    value = re.sub(r"(?i)\b(token|secret|password|api[_-]?key)\s*=\s*\S+", r"\1=<redacted>", str(text or ""))
    return value[:limit] + ("..." if len(value) > limit else "")


def redacted_action_preview(text: str, limit: int = 160) -> str:
    return redacted_action_text(text, limit)


def extract_fenced_text(text: str) -> str:
    match = FENCED_TEXT_RE.search(str(text or ""))
    return match.group("text").strip() if match else ""


def extract_skill_file_name(text: str) -> str:
    match = SKILL_NAME_RE.search(str(text or ""))
    return match.group(1).strip() if match else ""


def extract_skill_description(text: str, name: str) -> str:
    match = DESCRIPTION_RE.search(str(text or ""))
    if match:
        return " ".join(match.group("description").split()).strip(" `'\"")
    if name:
        pattern = rf"\b(?:yo!?skill|skill|context)\s+(?:named\s+|called\s+)?[`'\"]?{re.escape(name)}[`'\"]?\s+(?:to|that|which)\s+(?P<description>.+)$"
        match = re.search(pattern, str(text or ""), flags=re.IGNORECASE | re.DOTALL)
        if match:
            return " ".join(match.group("description").split()).strip(" `'\"")
    return ""


def parse_yoagent_skill_file_intent(question: str) -> dict[str, Any] | None:
    text = " ".join(str(question or "").split())
    if not text:
        return None
    lower = text.lower()
    mentions_skill = any(word in lower for word in ["yo!skill", "yoskill", "skill", "skills", "context.d", "skills.d"])
    if not mentions_skill:
        return None
    kind = "context" if "context" in lower and "skill" not in lower else "skill"
    name = extract_skill_file_name(text)
    if re.search(r"\b(?:list|show|where|what)\b", lower) and re.search(r"\b(?:skills|yo!skills|skill files|context files)\b", lower) and not name:
        return {"type": "skill_file", "operation": "list", "kind": kind}
    if re.search(r"\b(?:read|show|inspect|view)\b", lower) and name:
        return {"type": "skill_file", "operation": "read", "kind": kind, "name": name}
    if re.search(r"\b(?:delete|remove)\b", lower) and name:
        return {"type": "skill_file", "operation": "delete", "kind": kind, "name": name}
    if re.search(r"\bdisable\b", lower) and name:
        return {
            "type": "skill_file",
            "operation": "upsert",
            "kind": "skill",
            "name": name,
            "text": f"name: {name}\nenabled: false\n",
        }
    if re.search(r"\b(?:create|add|update|upsert|write|modify|edit)\b", lower) and name:
        body = extract_fenced_text(question)
        if not body:
            description = extract_skill_description(text, name) or "User-defined YO!skill."
            if kind == "context":
                body = description
            else:
                body = "\n".join([
                    f"name: {name}",
                    "kind: workflow",
                    f"description: {description}",
                    "confirmation: none",
                ])
        return {"type": "skill_file", "operation": "upsert", "kind": kind, "name": name, "text": body}
    return None
