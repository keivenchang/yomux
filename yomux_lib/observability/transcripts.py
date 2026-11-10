"""Transcript parsing helpers for agent activity and approval fallback state.

Transcript recency and pending-tool behavior is exercised by
``tests/test_agent_tui.py`` and ``tests/test_auto_approve_detector.py``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from ..common import SessionInfo
from ..common import tail_file_lines
from ..common import truncate_text
from ..approval.yolo_rules import hard_floor_decision


TRANSCRIPT_ACTIVITY_RECENCY_SECONDS = 10.0
TRANSCRIPT_APPROVAL_RECENCY_SECONDS = 20.0
CLAUDE_TERMINAL_STOP_REASONS = {"end_turn", "stop_sequence", "max_tokens", "stop"}
TRANSCRIPT_BASH_TOOL_NAMES = {"bash", "shell", "sh", "exec", "exec_command", "run_command", "terminal"}
TRANSCRIPT_FILE_TOOL_NAMES = {"write", "edit", "multiedit", "notebookedit", "apply_patch", "patch"}
TRANSCRIPT_METADATA_ONLY_TYPES = {"last-prompt", "ai-title", "mode", "permission-mode", "pr-link"}
TRANSCRIPT_ACTIVITY_EVENT_TYPES = {
    "agent_message",
    "agent_message_delta",
    "custom_tool_call",
    "custom_tool_call_output",
    "function_call",
    "function_call_output",
    "input_message",
    "item.delta",
    "message",
    "message.delta",
    "patch_apply_end",
    "task_complete",
    "task_started",
    "user_message",
}
TRANSCRIPT_ACTIVITY_MESSAGE_ROLES = {"assistant", "tool", "user"}


def transcript_activity_state_from_text(text: str, kind: str = "") -> dict[str, Any]:
    """Classify recent transcript JSON as working or idle.

    Visible TUI state remains primary; this supports the spec's passive transcript
    fallback for pending tool calls and streaming deltas.
    """
    pending_tools: dict[str, str] = {}
    streaming = False
    completed = False
    synthetic_index = 0
    for raw_line in text.splitlines():
        try:
            raw_item = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_item, dict):
            continue
        entry_type = str(raw_item.get("type") or "")
        message = raw_item.get("message")
        payload = raw_item.get("payload")
        if isinstance(message, dict):
            role = str(message.get("role") or "")
            stop_reason = str(message.get("stop_reason") or raw_item.get("stop_reason") or "")
            content = message.get("content")
            if role == "assistant":
                completed = stop_reason in CLAUDE_TERMINAL_STOP_REASONS
                streaming = not completed
            elif role in {"user", "tool", "system"} and not pending_tools:
                streaming = False
                completed = True
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "")
                    if block_type == "tool_use":
                        synthetic_index += 1
                        tool_id = str(block.get("id") or f"tool-{synthetic_index}")
                        pending_tools[tool_id] = str(block.get("name") or "tool")
                        streaming = True
                        completed = False
                    elif block_type == "tool_result":
                        tool_id = str(block.get("tool_use_id") or block.get("id") or "")
                        if tool_id:
                            pending_tools.pop(tool_id, None)
                        streaming = False
            if role == "assistant" and stop_reason in CLAUDE_TERMINAL_STOP_REASONS and not pending_tools:
                streaming = False
                completed = True
        if isinstance(payload, dict):
            payload_type = str(payload.get("type") or entry_type or "")
            if payload_type in {"agent_message_delta", "message.delta", "item.delta", "task_started"}:
                streaming = True
                completed = False
            elif payload_type == "task_complete":
                streaming = False
                completed = True
            elif payload_type in {"function_call", "custom_tool_call"}:
                synthetic_index += 1
                call_id = str(payload.get("call_id") or payload.get("id") or f"call-{synthetic_index}")
                pending_tools[call_id] = str(payload.get("name") or "tool")
                streaming = True
                completed = False
            elif payload_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = str(payload.get("call_id") or payload.get("id") or "")
                if call_id:
                    pending_tools.pop(call_id, None)
                streaming = False
        elif entry_type in {"agent_message_delta", "message.delta", "item.delta"}:
            streaming = True
            completed = False

    if pending_tools:
        names = ", ".join(sorted(set(pending_tools.values())))
        return {"key": "working", "text": f"{kind or 'agent'} tool call pending in transcript: {names}"}
    if streaming and not completed:
        return {"key": "working", "text": f"{kind or 'agent'} turn is active in transcript"}
    return {"key": "idle", "text": ""}


def transcript_activity_is_recent(path: Path, text: str, now: datetime | None = None, recency_seconds: float = TRANSCRIPT_ACTIVITY_RECENCY_SECONDS, kind: str = "") -> bool:
    current = now or datetime.now(timezone.utc)
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        mtime = None
    newest = newest_transcript_activity_timestamp(text, kind)
    timestamps = [newest] if newest is not None else [mtime] if mtime is not None else []
    return any(abs((current - value).total_seconds()) <= recency_seconds for value in timestamps)

def transcript_activity_state(path: Path | str | None, kind: str = "") -> dict[str, Any]:
    if not path:
        return {"key": "idle", "text": ""}
    transcript_path = Path(path)
    try:
        text = tail_file_lines(transcript_path, 400)
    except OSError:
        return {"key": "idle", "text": ""}
    state = transcript_activity_state_from_text(text, kind)
    if state.get("key") != "working":
        return state
    if not transcript_activity_is_recent(transcript_path, text, kind=kind):
        return {"key": "idle", "text": ""}
    return state

def session_transcript_activity_state(info: SessionInfo | None) -> dict[str, Any]:
    if not info:
        return {"key": "idle", "text": ""}
    agent = next((item for item in info.agents if item.transcript), None)
    if agent is None:
        return {"key": "idle", "text": ""}
    return transcript_activity_state(agent.transcript, agent.kind)

def transcript_pending_approval_from_text(text: str, kind: str = "") -> dict[str, Any]:
    pending: dict[str, dict[str, Any]] = {}
    synthetic_index = 0
    for raw_line in text.splitlines():
        try:
            raw_item = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_item, dict):
            continue
        entry_type = str(raw_item.get("type") or "")
        message = raw_item.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "")
                    if block_type == "tool_use":
                        synthetic_index += 1
                        tool_id = str(block.get("id") or f"tool-{synthetic_index}")
                        record = transcript_tool_approval_record(
                            str(block.get("name") or "tool"),
                            block.get("input"),
                            tool_id,
                            kind,
                        )
                        if record:
                            pending[tool_id] = record
                    elif block_type == "tool_result":
                        tool_id = str(block.get("tool_use_id") or block.get("id") or "")
                        if tool_id:
                            pending.pop(tool_id, None)
        payload = raw_item.get("payload")
        if not isinstance(payload, dict) and entry_type in {"function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"}:
            payload = raw_item
        if isinstance(payload, dict):
            payload_type = str(payload.get("type") or entry_type or "")
            if payload_type in {"function_call", "custom_tool_call"}:
                synthetic_index += 1
                call_id = str(payload.get("call_id") or payload.get("id") or f"call-{synthetic_index}")
                arguments = payload.get("arguments") if payload_type == "function_call" else payload.get("input")
                record = transcript_tool_approval_record(
                    str(payload.get("name") or "tool"),
                    arguments,
                    call_id,
                    kind,
                )
                if record:
                    pending[call_id] = record
            elif payload_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = str(payload.get("call_id") or payload.get("id") or "")
                if call_id:
                    pending.pop(call_id, None)
    if not pending:
        return {"visible": False, "type": "", "text": "", "source": "transcript", "hash": ""}
    record = list(pending.values())[-1]
    record["visible"] = True
    record["source"] = "transcript"
    record["hash"] = transcript_approval_hash(record)
    return record

def transcript_pending_approval(path: Path | str | None, kind: str = "", recency_seconds: float = TRANSCRIPT_APPROVAL_RECENCY_SECONDS) -> dict[str, Any]:
    if not path:
        return {"visible": False, "type": "", "text": "", "source": "transcript", "hash": ""}
    transcript_path = Path(path)
    try:
        text = tail_file_lines(transcript_path, 400)
    except OSError:
        return {"visible": False, "type": "", "text": "", "source": "transcript", "hash": ""}
    state = transcript_pending_approval_from_text(text, kind)
    if state.get("visible") is not True:
        return state
    if not transcript_activity_is_recent(transcript_path, text, recency_seconds=recency_seconds):
        return {"visible": False, "type": "", "text": "", "source": "transcript", "hash": "", "reason": "transcript approval candidate is stale"}
    state["transcript"] = str(transcript_path)
    return state

def transcript_tool_approval_record(name: str, tool_input: Any, tool_id: str, kind: str = "") -> dict[str, Any] | None:
    tool_name = name.strip() or "tool"
    normalized = tool_name.lower().replace("-", "_")
    payload = transcript_tool_input_mapping(tool_input)
    command = transcript_tool_command(normalized, payload, tool_input)
    if normalized in TRANSCRIPT_BASH_TOOL_NAMES or command:
        text = command or f"{kind or 'agent'} pending {tool_name}"
        return {
            "type": "bash",
            "text": f"Transcript pending {tool_name}: {truncate_text(text, 220)}",
            "command": command or text,
            "tool": tool_name,
            "tool_id": tool_id,
            "yes_selected": False,
            "selected_option": 0,
            "action": "option1",
            "dangerous": transcript_tool_command_is_dangerous(command),
        }
    if normalized in TRANSCRIPT_FILE_TOOL_NAMES:
        file_path = transcript_tool_file_path(payload, tool_input)
        text = f"{tool_name} {file_path}".strip()
        return {
            "type": "file",
            "text": f"Transcript pending {text}",
            "command": "",
            "tool": tool_name,
            "tool_id": tool_id,
            "yes_selected": False,
            "selected_option": 0,
            "action": "option2",
            "dangerous": False,
        }
    return {
        "type": "tool",
        "text": f"Transcript pending {tool_name}",
        "command": "",
        "tool": tool_name,
        "tool_id": tool_id,
        "yes_selected": False,
        "selected_option": 0,
        "action": "option2",
        "dangerous": False,
    }

def transcript_tool_command_is_dangerous(command: str | None) -> bool:
    if command is None:
        return False
    return hard_floor_decision(command) is not None

def transcript_tool_input_mapping(tool_input: Any) -> dict[str, Any]:
    if isinstance(tool_input, dict):
        return tool_input
    if isinstance(tool_input, str) and tool_input.strip():
        try:
            parsed = json.loads(tool_input)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}

def transcript_tool_command(normalized_name: str, payload: dict[str, Any], raw_input: Any) -> str | None:
    for key in ("command", "cmd", "shell_command"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if normalized_name in TRANSCRIPT_BASH_TOOL_NAMES and isinstance(raw_input, str) and raw_input.strip():
        return raw_input.strip()
    return None

def transcript_tool_file_path(payload: dict[str, Any], raw_input: Any) -> str:
    for key in ("file_path", "path", "target_file", "filename"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return truncate_text(str(raw_input or ""), 120)

def transcript_approval_hash(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("type") or ""),
        str(record.get("tool") or ""),
        str(record.get("tool_id") or ""),
        str(record.get("command") or ""),
        str(record.get("text") or ""),
    ]
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()

def parse_transcript_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def normalized_transcript_type(value: Any) -> str:
    return str(value or "").strip().lower()

def transcript_record_is_metadata_only(raw_item: dict[str, Any]) -> bool:
    entry_type = normalized_transcript_type(raw_item.get("type"))
    if entry_type in TRANSCRIPT_METADATA_ONLY_TYPES:
        return True
    payload = raw_item.get("payload")
    payload_type = normalized_transcript_type(payload.get("type")) if isinstance(payload, dict) else ""
    if payload_type in TRANSCRIPT_METADATA_ONLY_TYPES:
        return True
    metadata_types = {"metadata", "session_metadata", "meta"}
    if entry_type in metadata_types or payload_type in metadata_types:
        for container in (raw_item, payload if isinstance(payload, dict) else None):
            if not isinstance(container, dict):
                continue
            for key in ("key", "name", "field", "subtype"):
                if normalized_transcript_type(container.get(key)) in TRANSCRIPT_METADATA_ONLY_TYPES:
                    return True
    if entry_type not in TRANSCRIPT_ACTIVITY_EVENT_TYPES:
        for key in ("key", "name", "field"):
            if normalized_transcript_type(raw_item.get(key)) in TRANSCRIPT_METADATA_ONLY_TYPES:
                return True
    return False

def transcript_string_field_has_text(payload: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False

def transcript_content_has_activity(content: Any, role: str) -> bool:
    return bool(extract_content_blocks(content, role))

def transcript_record_counts_as_activity(raw_item: dict[str, Any], kind: str = "") -> bool:
    del kind
    if transcript_record_is_metadata_only(raw_item):
        return False
    entry_type = normalized_transcript_type(raw_item.get("type"))
    message = raw_item.get("message")
    if isinstance(message, dict):
        role = normalized_transcript_type(message.get("role") or entry_type)
        if role in TRANSCRIPT_ACTIVITY_MESSAGE_ROLES and transcript_content_has_activity(message.get("content"), role):
            return True
        stop_reason = normalized_transcript_type(message.get("stop_reason") or raw_item.get("stop_reason"))
        if role == "assistant" and stop_reason in CLAUDE_TERMINAL_STOP_REASONS:
            return True
    payload = raw_item.get("payload")
    if not isinstance(payload, dict) and entry_type in TRANSCRIPT_ACTIVITY_EVENT_TYPES:
        payload = raw_item
    if isinstance(payload, dict):
        payload_type = normalized_transcript_type(payload.get("type") or entry_type)
        if payload_type in {"function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output", "patch_apply_end"}:
            return True
        if payload_type in {"task_started", "task_complete"}:
            return True
        if payload_type in {"agent_message_delta", "message.delta", "item.delta"}:
            return True
        if payload_type in {"agent_message", "user_message", "input_message"}:
            return transcript_string_field_has_text(payload, "message", "text", "content", "input", "prompt", "last_agent_message")
        if payload_type == "message":
            role = normalized_transcript_type(payload.get("role") or "message")
            return transcript_content_has_activity(payload.get("content"), role)
    return entry_type in {"agent_message_delta", "message.delta", "item.delta"}

def newest_transcript_activity_timestamp(text: str, kind: str = "") -> datetime | None:
    newest = None
    for raw_line in text.splitlines():
        try:
            raw_item = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_item, dict):
            continue
        timestamp = parse_transcript_timestamp(raw_item.get("timestamp"))
        if not timestamp:
            continue
        if not transcript_record_counts_as_activity(raw_item, kind):
            continue
        if newest is None or timestamp > newest:
            newest = timestamp
    return newest


def extract_content_blocks(content: Any, default_role: str = "message") -> list[dict[str, str]]:
    if isinstance(content, str):
        return [{"role": default_role, "text": truncate_text(content, 5000)}] if content.strip() else []
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, str]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if not isinstance(block_type, str):
            continue
        if block_type in {"text", "input_text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                blocks.append({"role": default_role, "text": text})
        elif block_type == "tool_use":
            name = block.get("name", "tool")
            tool_input = block.get("input")
            blocks.append(
                {
                    "role": "tool_use",
                    "text": f"{name}\n{truncate_text(json.dumps(tool_input, ensure_ascii=False, indent=2), 2200)}",
                }
            )
        elif block_type == "tool_result":
            result = block.get("content", "")
            blocks.append({"role": "tool_result", "text": truncate_text(str(result), 2200)})
    return blocks
