# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Normalized YO!agent stream events for Claude/Codex backends."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any

ASSISTANT_DELTA = "assistant_delta"
HIDDEN_WORK_DELTA = "hidden_work_delta"
HIDDEN_WORK_DONE = "hidden_work_done"
TOOL_CALL_STARTED = "tool_call_started"
TOOL_CALL_DELTA = "tool_call_delta"
TOOL_CALL_FINISHED = "tool_call_finished"
APPROVAL_REQUESTED = "approval_requested"
USAGE = "usage"
ERROR = "error"
TURN_DONE = "turn_done"

CODEX_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
}

CODEX_TOOL_TYPES = {
    "commandExecution": "command",
    "fileChange": "file",
    "mcpToolCall": "mcp",
    "dynamicToolCall": "tool",
    "webSearch": "web",
    "tool_use": "tool",
}


@dataclass(frozen=True)
class YoagentStreamEvent:
    kind: str
    text: str = ""
    backend: str = ""
    native_type: str = ""
    item_id: str = ""
    turn_id: str = ""
    thread_id: str = ""
    tool_name: str = ""
    cwd: str = ""
    command: str = ""
    path: str = ""
    timestamp: str = ""
    summary: bool = False
    raw_thinking: bool = False
    redacted: bool = False
    snapshot: bool = False
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "event": self.kind,
            "text": self.text,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }
        for key, value in (
            ("backend", self.backend),
            ("native_type", self.native_type),
            ("item_id", self.item_id),
            ("turn_id", self.turn_id),
            ("thread_id", self.thread_id),
            ("tool_name", self.tool_name),
            ("cwd", self.cwd),
            ("command", self.command),
            ("path", self.path),
        ):
            if value:
                payload[key] = value
        if self.summary:
            payload["summary"] = True
        if self.raw_thinking:
            payload["raw_thinking"] = True
        if self.redacted:
            payload["redacted"] = True
        if self.snapshot:
            payload["snapshot"] = True
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


def stream_event(kind: str, **kwargs: Any) -> dict[str, Any]:
    return YoagentStreamEvent(kind=kind, **kwargs).as_dict()


def normalize_yoagent_stream_event(event: dict[str, Any], *, backend: str = "") -> dict[str, Any]:
    kind = str(event.get("kind") or event.get("event") or "").strip()
    if kind == "delta":
        text = str(event.get("text") if event.get("text") is not None else event.get("delta") or "")
        return stream_event(
            ASSISTANT_DELTA,
            text=text,
            backend=str(event.get("backend") or backend or ""),
            native_type=str(event.get("native_type") or kind),
            item_id=str(event.get("item_id") or ""),
            turn_id=str(event.get("turn_id") or ""),
            thread_id=str(event.get("thread_id") or ""),
            snapshot=event.get("text") is not None,
        )
    if kind == "thinking":
        return stream_event(
            HIDDEN_WORK_DELTA,
            text=str(event.get("text") or event.get("delta") or "thinking"),
            backend=str(event.get("backend") or backend or ""),
            native_type=str(event.get("native_type") or kind),
            item_id=str(event.get("item_id") or ""),
            turn_id=str(event.get("turn_id") or ""),
            thread_id=str(event.get("thread_id") or ""),
        )
    allowed = {
        ASSISTANT_DELTA,
        HIDDEN_WORK_DELTA,
        HIDDEN_WORK_DONE,
        TOOL_CALL_STARTED,
        TOOL_CALL_DELTA,
        TOOL_CALL_FINISHED,
        APPROVAL_REQUESTED,
        USAGE,
        ERROR,
        TURN_DONE,
    }
    if kind not in allowed:
        kind = ERROR
    payload = dict(event)
    payload["kind"] = kind
    payload["event"] = kind
    if backend and not payload.get("backend"):
        payload["backend"] = backend
    if not payload.get("timestamp"):
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    if "text" in payload:
        payload["text"] = str(payload.get("text") or "")
    return payload


def _stream_auxiliary_compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _stream_auxiliary_multiline_text(value: Any) -> str:
    return str(value or "").replace("\\n", "\n").strip()


def yoagent_stream_event_auxiliary_line(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or event.get("event") or "")
    text = _stream_auxiliary_compact_text(event.get("text"))
    multiline_text = _stream_auxiliary_multiline_text(event.get("text"))
    tool_name = str(event.get("tool_name") or "").strip()
    native_type = str(event.get("native_type") or "").strip()
    command = str(event.get("command") or "").strip()
    path = str(event.get("path") or "").strip()
    if kind == HIDDEN_WORK_DELTA:
        prefix = "thinking"
        if event.get("summary"):
            prefix = "thinking summary"
        if event.get("redacted"):
            prefix = "redacted thinking"
        if text.lower() == prefix or text.lower().startswith(f"{prefix}..."):
            return text
        return f"{prefix}: {text}" if text else prefix
    if kind == HIDDEN_WORK_DONE:
        return "thinking done"
    if kind == TOOL_CALL_STARTED:
        detail = command or path or multiline_text
        label = f"tool start: {tool_name or native_type or 'tool'}"
        return f"{label}: {detail}" if detail else label
    if kind == TOOL_CALL_DELTA:
        label = f"tool output: {tool_name or native_type or 'tool'}"
        return f"{label}: {multiline_text}" if multiline_text else label
    if kind == TOOL_CALL_FINISHED:
        detail = command or path or multiline_text
        label = f"tool done: {tool_name or native_type or 'tool'}"
        return f"{label}: {detail}" if detail else label
    if kind == APPROVAL_REQUESTED:
        detail = command or path or text
        return f"approval requested: {detail}" if detail else "approval requested"
    if kind == USAGE:
        return f"usage: {text}" if text else "usage"
    if kind == ERROR:
        return f"error: {text}" if text else "error"
    return ""


def _json_compact(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _tool_input_summary(name: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        if name == "Bash":
            command = str(tool_input.get("command") or "").strip()
            description = str(tool_input.get("description") or "").strip()
            return command or description
        for key in ("file_path", "path", "pattern", "url", "prompt"):
            value = str(tool_input.get(key) or "").strip()
            if value:
                return value
        return _json_compact(tool_input)
    if isinstance(tool_input, str):
        return tool_input
    return ""


def _codex_tool_name(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "").strip()
    if item_type == "commandExecution":
        return "command"
    if item_type == "fileChange":
        return "file change"
    if item_type == "mcpToolCall":
        return str(item.get("toolName") or item.get("name") or "MCP tool")
    if item_type == "dynamicToolCall":
        return str(item.get("name") or "tool")
    if item_type == "webSearch":
        return "web search"
    return item_type or "tool"


def _codex_command_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return ""


def normalize_codex_app_server_message(message: dict[str, Any], *, backend: str = "codex") -> list[dict[str, Any]]:
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    common = {
        "backend": backend,
        "native_type": method,
        "thread_id": str(params.get("threadId") or ""),
        "turn_id": str(params.get("turnId") or ""),
        "item_id": str(params.get("itemId") or ""),
    }
    if message.get("id") is not None and method in CODEX_APPROVAL_METHODS:
        command = _codex_command_text(params.get("command"))
        file_changes = params.get("fileChanges") if isinstance(params.get("fileChanges"), dict) else {}
        path = ", ".join(str(path) for path in file_changes.keys()) if file_changes else ""
        reason = str(params.get("reason") or "").strip()
        return [stream_event(APPROVAL_REQUESTED, text=reason, command=command, path=path, cwd=str(params.get("cwd") or ""), **common)]
    if method == "item/agentMessage/delta":
        delta = params.get("delta")
        if isinstance(delta, str) and delta:
            return [stream_event(ASSISTANT_DELTA, text=delta, **common)]
        return []
    if method == "item/reasoning/summaryTextDelta":
        delta = str(params.get("delta") or "")
        return [stream_event(HIDDEN_WORK_DELTA, text=delta or "reasoning summary...", summary=True, **common)]
    if method == "item/reasoning/textDelta":
        delta = str(params.get("delta") or "")
        return [stream_event(HIDDEN_WORK_DELTA, text=delta or "reasoning...", raw_thinking=True, **common)]
    if method in {"item/reasoning/delta", "item/thinking/delta", "item/thought/delta"}:
        delta = str(params.get("delta") or params.get("text") or "")
        return [stream_event(HIDDEN_WORK_DELTA, text=delta or "reasoning...", **common)]
    if method in {"item/commandExecution/outputDelta", "item/fileChange/outputDelta"}:
        delta = str(params.get("delta") or "")
        tool_name = "command" if "commandExecution" in method else "file change"
        return [stream_event(TOOL_CALL_DELTA, text=delta, tool_name=tool_name, **common)]
    if method == "item/commandExecution/terminalInteraction":
        return [stream_event(TOOL_CALL_DELTA, text="terminal interaction", tool_name="command", **common)]
    if method == "item/mcpToolCall/progress":
        return [stream_event(TOOL_CALL_DELTA, text=str(params.get("message") or "progress"), tool_name="MCP tool", **common)]
    if method == "item/started":
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        if item_type not in CODEX_TOOL_TYPES:
            return []
        return [stream_event(TOOL_CALL_STARTED, text=_codex_command_text(item.get("command")) or str(item.get("name") or ""), tool_name=_codex_tool_name(item), item_id=str(item.get("id") or common["item_id"]), **{k: v for k, v in common.items() if k != "item_id"})]
    if method == "item/completed":
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        if item_type == "agentMessage":
            return []
        if item_type not in CODEX_TOOL_TYPES:
            return []
        text = str(item.get("aggregatedOutput") or item.get("text") or "")
        return [stream_event(TOOL_CALL_FINISHED, text=text, tool_name=_codex_tool_name(item), item_id=str(item.get("id") or common["item_id"]), **{k: v for k, v in common.items() if k != "item_id"})]
    if method == "turn/completed":
        return [stream_event(TURN_DONE, **common)]
    if method == "thread/status/changed":
        status = params.get("status") if isinstance(params.get("status"), dict) else {}
        if status.get("type") == "idle":
            return [stream_event(TURN_DONE, **common)]
        return []
    return []


class ClaudeStreamJsonNormalizer:
    def __init__(self, *, backend: str = "claude"):
        self.backend = backend
        self.blocks: dict[int, dict[str, Any]] = {}
        self.tool_inputs_started: set[str] = set()
        self.tool_inputs_reported: set[str] = set()
        self.thinking_token_estimate: int | None = None

    def normalize_line(self, line: str) -> list[dict[str, Any]]:
        try:
            item = json.loads(line)
        except ValueError:
            return []
        if not isinstance(item, dict):
            return []
        return self.normalize_item(item)

    def normalize_item(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        item_type = str(item.get("type") or "")
        if item_type == "stream_event":
            nested = item.get("event") if isinstance(item.get("event"), dict) else {}
            if not nested:
                return []
            nested_item = dict(nested)
            for key in ("session_id", "uuid", "parent_tool_use_id"):
                if key not in nested_item and item.get(key) is not None:
                    nested_item[key] = item.get(key)
            return self.normalize_item(nested_item)
        if item_type == "content_block_start":
            return self._content_block_start(item)
        if item_type == "content_block_delta":
            return self._content_block_delta(item)
        if item_type == "content_block_stop":
            return self._content_block_stop(item)
        if item_type == "assistant":
            return self._assistant_message(item)
        if item_type == "user":
            return self._user_message(item)
        if item_type == "system":
            return self._system_message(item)
        if item_type == "result":
            return self._result_message(item)
        return []

    def _common(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "native_type": str(item.get("type") or ""),
            "thread_id": str(item.get("session_id") or ""),
        }

    def _content_block_start(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        index = int(item.get("index") or 0)
        block = item.get("content_block") if isinstance(item.get("content_block"), dict) else {}
        self.blocks[index] = dict(block)
        if block.get("type") != "tool_use":
            return []
        tool_id = str(block.get("id") or "")
        name = str(block.get("name") or "tool")
        if tool_id:
            if tool_id in self.tool_inputs_started:
                return []
            self.tool_inputs_started.add(tool_id)
        text = _tool_input_summary(name, block.get("input"))
        return [stream_event(TOOL_CALL_STARTED, text=text, tool_name=name, item_id=tool_id, **self._common(item))]

    def _content_block_delta(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        index = int(item.get("index") or 0)
        delta = item.get("delta") if isinstance(item.get("delta"), dict) else {}
        delta_type = str(delta.get("type") or "")
        if delta_type == "text_delta":
            text = str(delta.get("text") or "")
            return [stream_event(ASSISTANT_DELTA, text=text, **self._common(item))] if text else []
        if delta_type == "input_json_delta":
            partial = str(delta.get("partial_json") or "")
            block = self.blocks.setdefault(index, {"type": "tool_use", "json_parts": [], "name": "tool"})
            block.setdefault("json_parts", []).append(partial)
            return [stream_event(TOOL_CALL_DELTA, text=partial, tool_name=str(block.get("name") or "tool"), item_id=str(block.get("id") or ""), **self._common(item))] if partial else []
        if delta_type in {"thinking_delta", "redacted_thinking_delta"}:
            text = str(delta.get("thinking") or delta.get("text") or "")
            estimated_tokens = delta.get("estimated_tokens")
            token_count = estimated_tokens if isinstance(estimated_tokens, (int, float)) else None
            if text:
                return [stream_event(HIDDEN_WORK_DELTA, text=text, raw_thinking=True, redacted=delta_type == "redacted_thinking_delta", **self._common(item))]
            if token_count is not None and self.thinking_token_estimate is not None and int(token_count) <= self.thinking_token_estimate:
                return []
            if token_count is None:
                return []
            self.thinking_token_estimate = int(token_count)
            heartbeat = f"thinking... (~{int(token_count)} tokens)" if token_count is not None else "thinking..."
            metadata = {"estimated_tokens": token_count} if token_count is not None else None
            return [stream_event(HIDDEN_WORK_DELTA, text=heartbeat, raw_thinking=True, redacted=delta_type == "redacted_thinking_delta", metadata=metadata, **self._common(item))]
        return []

    def _content_block_stop(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        index = int(item.get("index") or 0)
        block = self.blocks.get(index)
        if not block or block.get("type") != "tool_use":
            return []
        tool = self._tool_payload(block)
        tool_id = str(tool.get("id") or "")
        if tool_id and tool_id in self.tool_inputs_reported:
            return []
        if tool_id:
            self.tool_inputs_reported.add(tool_id)
        name = str(tool.get("name") or "tool")
        return [stream_event(TOOL_CALL_FINISHED, text=_tool_input_summary(name, tool.get("input")), tool_name=name, item_id=tool_id, **self._common(item))]

    def _assistant_message(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        payload = item.get("message") if isinstance(item.get("message"), dict) else {}
        content = payload.get("content") if isinstance(payload.get("content"), list) else []
        events: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type in {"thinking", "redacted_thinking"}:
                text = str(block.get("thinking") or block.get("text") or "").strip()
                if not text:
                    continue
                metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else None
                events.append(stream_event(HIDDEN_WORK_DELTA, text=text, raw_thinking=True, redacted=block_type == "redacted_thinking", snapshot=True, metadata=metadata, **self._common(item)))
                continue
            if block_type != "tool_use":
                continue
            tool_id = str(block.get("id") or "")
            if tool_id and (tool_id in self.tool_inputs_started or tool_id in self.tool_inputs_reported):
                continue
            if tool_id:
                self.tool_inputs_started.add(tool_id)
            name = str(block.get("name") or "tool")
            events.append(stream_event(TOOL_CALL_STARTED, text=_tool_input_summary(name, block.get("input")), tool_name=name, item_id=tool_id, **self._common(item)))
        return events

    def _user_message(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        result = item.get("tool_use_result") if isinstance(item.get("tool_use_result"), dict) else {}
        if not result:
            return []
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        text = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
        if not text:
            text = "interrupted" if result.get("interrupted") else "result"
        return [stream_event(TOOL_CALL_FINISHED, text=text, tool_name="tool", **self._common(item))]

    def _system_message(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        if str(item.get("subtype") or "") != "thinking_tokens":
            return []
        estimated_tokens = item.get("estimated_tokens")
        if not isinstance(estimated_tokens, (int, float)):
            return []
        token_count = int(estimated_tokens)
        if self.thinking_token_estimate is not None and token_count <= self.thinking_token_estimate:
            return []
        self.thinking_token_estimate = token_count
        metadata: dict[str, Any] = {"estimated_tokens": token_count}
        estimated_tokens_delta = item.get("estimated_tokens_delta")
        if isinstance(estimated_tokens_delta, (int, float)):
            metadata["estimated_tokens_delta"] = int(estimated_tokens_delta)
        return [
            stream_event(
                HIDDEN_WORK_DELTA,
                text=f"thinking... (~{token_count} tokens)",
                raw_thinking=True,
                metadata=metadata,
                **self._common(item),
            )
        ]

    def _result_message(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        if item.get("is_error"):
            text = str(item.get("result") or item.get("api_error_status") or item.get("subtype") or "Claude stream-json error")
            return [stream_event(ERROR, text=text, **self._common(item))]
        events = [stream_event(TURN_DONE, **self._common(item))]
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        model_usage = item.get("modelUsage") if isinstance(item.get("modelUsage"), dict) else {}
        usage_text = _json_compact(usage or model_usage) if usage or model_usage else ""
        if usage_text:
            events.insert(0, stream_event(USAGE, text=usage_text, metadata={"usage": usage, "model_usage": model_usage}, **self._common(item)))
        return events

    def _tool_payload(self, block: dict[str, Any]) -> dict[str, Any]:
        tool = dict(block)
        json_parts = tool.pop("json_parts", [])
        if json_parts:
            raw_json = "".join(str(part) for part in json_parts)
            try:
                tool["input"] = json.loads(raw_json) if raw_json else {}
            except json.JSONDecodeError:
                tool["input"] = raw_json
        return tool
