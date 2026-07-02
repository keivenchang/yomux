# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Durable YO!agent conversation and CLI resume state."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from ..atomic_file import atomic_write_text
from ..atomic_file import file_lock
from ..common import STATE_DIR
from ..common import tail_file_lines
from ..common import truncate_text


YOAGENT_STATE_DIR = STATE_DIR / "yoagent"
YOAGENT_CONVERSATION_PATH = YOAGENT_STATE_DIR / "conversation.jsonl"
YOAGENT_CLI_STATE_PATH = YOAGENT_STATE_DIR / "cli-sessions.json"
YOAGENT_CONVERSATION_MAX_MESSAGES = 500
YOAGENT_MESSAGE_CONTENT_LIMIT = 20_000
YOAGENT_MESSAGE_DETAILS_LIMIT = 4_000
YOAGENT_AUXILIARY_LINE_LIMIT = 4_000
YOAGENT_AUXILIARY_TOTAL_LIMIT = 120_000
YOAGENT_STREAM_ITEM_TEXT_LIMIT = 8_000
YOAGENT_STREAM_ITEMS_LIMIT = 1_000
YOAGENT_STREAM_ITEMS_TOTAL_LIMIT = 120_000
YOAGENT_DETAIL_ROWS_LIMIT = 24
YOAGENT_ACTIONS_LIMIT = 8
YOAGENT_BACKENDS = {"claude", "codex"}
YOAGENT_MESSAGE_KINDS = {"agent_result"}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
        home = Path.home().resolve()
        try:
            return "~/" + str(resolved.relative_to(home))
        except ValueError:
            return str(resolved)
    except OSError:
        return str(path)


def sanitized_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    clean: list[dict[str, Any]] = []
    for item in value[:YOAGENT_ACTIONS_LIMIT]:
        if not isinstance(item, dict):
            continue
        try:
            encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
            decoded = json.loads(encoded)
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict):
            clean.append(decoded)
    return clean


def sanitized_detail_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:YOAGENT_DETAIL_ROWS_LIMIT]:
        if not isinstance(item, dict):
            continue
        key = truncate_text(str(item.get("key") or "").strip(), 160)
        fallback = truncate_text(redacted_auxiliary_text(str(item.get("fallback") or "").strip()), YOAGENT_MESSAGE_DETAILS_LIMIT)
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        try:
            clean_params = json.loads(json.dumps(params, ensure_ascii=False))
        except (TypeError, ValueError):
            clean_params = {}
        if key or fallback:
            rows.append({"key": key, "params": clean_params, "fallback": fallback})
    return rows


def bounded_auxiliary_lines(value: Any) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    clean: list[str] = []
    truncated = False
    for item in value:
        raw = str(item or "").strip()
        if not raw:
            continue
        redacted = redacted_auxiliary_text(raw)
        bounded = truncate_text(redacted, YOAGENT_AUXILIARY_LINE_LIMIT)
        truncated = truncated or bounded != redacted
        if bounded.strip():
            clean.append(bounded)
    return bounded_text_tail(clean, YOAGENT_AUXILIARY_TOTAL_LIMIT, truncated=truncated)


def bounded_text_tail(value: list[str], limit: int, *, truncated: bool = False) -> tuple[list[str], bool]:
    selected: list[str] = []
    total = 0
    for item in reversed(value):
        text = str(item or "")
        separator = 1 if selected else 0
        projected = total + len(text) + separator
        if selected and projected > limit:
            truncated = True
            break
        if not selected and projected > limit:
            selected.append(truncate_text(text, limit))
            truncated = True
            break
        selected.append(text)
        total = projected
    selected.reverse()
    truncated = truncated or len(selected) < len(value)
    return selected, truncated


def sanitized_auxiliary_lines(value: Any) -> list[str]:
    lines, _truncated = bounded_auxiliary_lines(value)
    return lines


def sanitized_auxiliary_preview(value: Any, fallback: str = "") -> tuple[str, bool]:
    raw = str(value or fallback or "").strip()
    if not raw:
        return "", False
    redacted = redacted_auxiliary_text(raw)
    bounded = truncate_text(redacted, YOAGENT_AUXILIARY_LINE_LIMIT)
    return bounded, bounded != redacted


def redacted_auxiliary_text(text: str) -> str:
    return re.sub(r"(?i)\b(token|secret|password|api[_-]?key)\s*=\s*\S+", r"\1=<redacted>", str(text or ""))


def bounded_stream_items(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    result: list[dict[str, Any]] = []
    truncated = False
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in {"assistant", "thinking", "tool", "diagnostic"}:
            continue
        text = redacted_auxiliary_text(str(item.get("text") or ""))
        bounded = truncate_text(text, YOAGENT_STREAM_ITEM_TEXT_LIMIT)
        truncated = truncated or bounded != text
        label_key = truncate_text(str(item.get("labelKey") or item.get("label_key") or "").strip(), 160)
        fallback = truncate_text(redacted_auxiliary_text(str(item.get("fallback") or "").strip()), 240)
        label_params = item.get("labelParams", item.get("label_params"))
        try:
            clean_params = json.loads(json.dumps(label_params, ensure_ascii=False)) if isinstance(label_params, dict) else {}
        except (TypeError, ValueError):
            clean_params = {}
        clean_item: dict[str, Any] = {"kind": kind, "text": bounded}
        for field in ("eventKind", "toolName", "command", "path"):
            field_value = truncate_text(redacted_auxiliary_text(str(item.get(field) or "").strip()), YOAGENT_STREAM_ITEM_TEXT_LIMIT)
            if field_value:
                clean_item[field] = field_value
        token_count = item.get("tokenCount")
        if isinstance(token_count, (int, float)) and token_count > 0:
            clean_item["tokenCount"] = int(token_count)
        if label_key:
            clean_item["labelKey"] = label_key
            clean_item["labelParams"] = clean_params
        if fallback:
            clean_item["fallback"] = fallback
        if bounded.strip() or label_key or fallback:
            result.append(clean_item)
    if len(result) > YOAGENT_STREAM_ITEMS_LIMIT:
        result = result[-YOAGENT_STREAM_ITEMS_LIMIT:]
        truncated = True
    selected: list[dict[str, Any]] = []
    total = 0
    for item in reversed(result):
        text = item["text"]
        separator = 1 if selected else 0
        projected = total + len(text) + separator
        if selected and projected > YOAGENT_STREAM_ITEMS_TOTAL_LIMIT:
            truncated = True
            break
        if not selected and projected > YOAGENT_STREAM_ITEMS_TOTAL_LIMIT:
            selected.append({**item, "text": truncate_text(text, YOAGENT_STREAM_ITEMS_TOTAL_LIMIT)})
            truncated = True
            break
        selected.append(item)
        total = projected
    selected.reverse()
    truncated = truncated or len(selected) < len(result)
    return selected, truncated


def sanitized_stream_items(value: Any) -> list[dict[str, Any]]:
    items, _truncated = bounded_stream_items(value)
    return items


def sanitize_message(value: Any, *, role: str | None = None, content: str | None = None, created_at: str | None = None) -> dict[str, Any] | None:
    raw = value if isinstance(value, dict) else {}
    message_role = str(role or raw.get("role") or "").strip().lower()
    if message_role not in {"user", "assistant"}:
        return None
    message_content = truncate_text(str(content if content is not None else raw.get("content") or "").strip(), YOAGENT_MESSAGE_CONTENT_LIMIT)
    if not message_content:
        return None
    created = str(created_at or raw.get("createdAt") or raw.get("created_at") or utc_iso()).strip()
    message: dict[str, Any] = {"role": message_role, "content": message_content, "createdAt": truncate_text(created, 80)}
    kind = str(raw.get("kind") or "").strip().lower()
    if kind in YOAGENT_MESSAGE_KINDS and message_role == "assistant":
        message["kind"] = kind
    session = str(raw.get("session") or "").strip()
    if session and message_role == "assistant":
        message["session"] = truncate_text(session, 120)
    actions = sanitized_actions(raw.get("actions"))
    if actions and message_role == "assistant":
        message["actions"] = actions
    details = truncate_text(str(raw.get("details") or "").strip(), YOAGENT_MESSAGE_DETAILS_LIMIT)
    if details and message_role == "assistant":
        message["details"] = details
    detail_rows = sanitized_detail_rows(raw.get("detailRows"))
    if detail_rows and message_role == "assistant":
        message["detailRows"] = detail_rows
    raw_response_ms = raw.get("responseMs", raw.get("response_ms"))
    response_ms = float_value(raw_response_ms, 0.0)
    if message_role == "assistant" and response_ms > 0:
        message["responseMs"] = round(response_ms, 3)
    auxiliary_lines, auxiliary_truncated = bounded_auxiliary_lines(raw.get("auxiliaryLines"))
    if auxiliary_lines and message_role == "assistant":
        message["auxiliaryLines"] = auxiliary_lines
        auxiliary_text = "\n".join(auxiliary_lines)
        bounded_auxiliary_text = truncate_text(auxiliary_text, YOAGENT_AUXILIARY_TOTAL_LIMIT)
        auxiliary_truncated = auxiliary_truncated or bounded_auxiliary_text != auxiliary_text
        message["auxiliaryText"] = bounded_auxiliary_text
        preview, preview_truncated = sanitized_auxiliary_preview(raw.get("auxiliaryPreview"), "\n".join(auxiliary_lines[-1:]))
        auxiliary_truncated = auxiliary_truncated or preview_truncated
        if preview:
            message["auxiliaryPreview"] = preview
    stream_items, stream_truncated = bounded_stream_items(raw.get("streamItems"))
    if stream_items and message_role == "assistant":
        message["streamItems"] = stream_items
    if message_role == "assistant" and (auxiliary_lines or stream_items):
        if bool(raw.get("auxiliaryDone")):
            message["auxiliaryDone"] = True
        if bool(raw.get("auxiliaryTruncated")) or auxiliary_truncated or stream_truncated:
            message["auxiliaryTruncated"] = True
    return message


def load_messages(limit: int = YOAGENT_CONVERSATION_MAX_MESSAGES, path: Path | None = None) -> list[dict[str, Any]]:
    target = path or YOAGENT_CONVERSATION_PATH
    bounded = max(1, min(int(limit), YOAGENT_CONVERSATION_MAX_MESSAGES))
    try:
        if not target.exists():
            return []
        with file_lock(target, dir_mode=0o700):
            lines = tail_file_lines(target, bounded)
    except OSError:
        return []
    messages: list[dict[str, Any]] = []
    for line in lines.splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = sanitize_message(raw)
        if message is not None:
            messages.append(message)
    return messages[-bounded:]


def append_message(message: dict[str, Any], path: Path | None = None) -> dict[str, Any] | None:
    clean = sanitize_message(message)
    if clean is None:
        return None
    target = path or YOAGENT_CONVERSATION_PATH
    with file_lock(target, dir_mode=0o700):
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(clean, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return clean


def clear_messages(path: Path | None = None) -> None:
    target = path or YOAGENT_CONVERSATION_PATH
    with file_lock(target, dir_mode=0o700):
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def sanitize_cli_sessions(value: Any, monotonic_now: float | None = None) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    now = time.monotonic() if monotonic_now is None else monotonic_now
    clean: dict[str, dict[str, Any]] = {}
    for backend, state in value.items():
        key = str(backend or "").strip().lower()
        if key not in YOAGENT_BACKENDS or not isinstance(state, dict):
            continue
        session_id = str(state.get("session_id") or "").strip()
        if not session_id:
            continue
        clean[key] = {
            "session_id": truncate_text(session_id, 400),
            "activity_signature": truncate_text(str(state.get("activity_signature") or ""), 2000),
            "updated_ts": max(0.0, float_value(state.get("updated_ts"), 0.0)),
            "updated_monotonic": now,
        }
    return clean


def float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_cli_sessions(path: Path | None = None, monotonic_now: float | None = None) -> dict[str, dict[str, Any]]:
    target = path or YOAGENT_CLI_STATE_PATH
    try:
        with file_lock(target, dir_mode=0o700):
            raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return sanitize_cli_sessions(raw, monotonic_now=monotonic_now)


def save_cli_sessions(sessions: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    target = path or YOAGENT_CLI_STATE_PATH
    clean: dict[str, dict[str, Any]] = {}
    for backend, state in sanitize_cli_sessions(sessions).items():
        clean[backend] = {
            "session_id": state["session_id"],
            "activity_signature": state.get("activity_signature") or "",
            "updated_ts": state.get("updated_ts") or time.time(),
        }
    with file_lock(target, dir_mode=0o700):
        atomic_write_text(target, json.dumps(clean, indent=2, sort_keys=True) + "\n", mode=0o600)


def clear_cli_sessions(path: Path | None = None) -> None:
    target = path or YOAGENT_CLI_STATE_PATH
    with file_lock(target, dir_mode=0o700):
        try:
            target.unlink()
        except FileNotFoundError:
            pass
