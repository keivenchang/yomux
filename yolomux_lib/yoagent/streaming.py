# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""YO!agent streaming state and client-event publishing."""

from __future__ import annotations

import copy
import threading
import time
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Callable

from ..common import truncate_text
from .actions import redacted_action_text
from .backends import strip_yoagent_stream_hidden_thinking
from .stream_events import ASSISTANT_DELTA
from .stream_events import ERROR
from .stream_events import HIDDEN_WORK_DELTA
from .stream_events import HIDDEN_WORK_DONE
from .stream_events import TOOL_CALL_DELTA
from .stream_events import TOOL_CALL_FINISHED
from .stream_events import TOOL_CALL_STARTED
from .stream_events import TURN_DONE
from .stream_events import USAGE
from .stream_events import normalize_yoagent_stream_event
from .stream_events import yoagent_stream_event_auxiliary_line


YOAGENT_STREAM_STATE_LIMIT = 50
YOAGENT_STREAM_STATE_PRUNE_COUNT = 10


def sanitized_stream_items(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in {"assistant", "thinking", "tool"}:
            continue
        text = redacted_action_text(str(item.get("text") or ""), None)
        if text.strip():
            result.append({"kind": kind, "text": text})
    return result


class YoagentStreamStateStore:
    def __init__(
        self,
        *,
        state_limit: int = YOAGENT_STREAM_STATE_LIMIT,
        prune_count: int = YOAGENT_STREAM_STATE_PRUNE_COUNT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.lock = threading.RLock()
        self.states: dict[str, dict[str, Any]] = {}
        self.state_limit = max(1, int(state_limit))
        self.prune_count = max(1, int(prune_count))
        self.clock = clock

    def store_callback_state(
        self,
        stream_id: str,
        *,
        auxiliary_lines: list[str],
        auxiliary_preview: str,
        stream_items: list[dict[str, str]],
        auxiliary_done: bool,
        auxiliary_truncated: bool,
    ) -> None:
        safe_stream_id = str(stream_id or "").strip()
        if not safe_stream_id:
            return
        with self.lock:
            self.states[safe_stream_id] = {
                "auxiliaryLines": list(auxiliary_lines),
                "auxiliaryText": "\n".join(auxiliary_lines),
                "auxiliaryPreview": auxiliary_preview,
                "streamItems": sanitized_stream_items(stream_items),
                "auxiliaryDone": bool(auxiliary_done),
                "auxiliaryTruncated": bool(auxiliary_truncated),
                "updatedTs": self.clock(),
            }
            self._prune_locked()

    def auxiliary_message_fields(self, stream_id: str) -> dict[str, Any]:
        safe_stream_id = str(stream_id or "").strip()
        if not safe_stream_id:
            return {}
        with self.lock:
            state = copy.deepcopy(self.states.get(safe_stream_id) or {})
        lines = [str(line or "") for line in state.get("auxiliaryLines", []) if str(line or "").strip()]
        stream_items = sanitized_stream_items(state.get("streamItems"))
        if not lines and not stream_items:
            return {}
        fields: dict[str, Any] = {"stream_items": stream_items}
        if lines:
            fields["auxiliary_lines"] = lines
            fields["auxiliary_preview"] = str(state.get("auxiliaryPreview") or "\n".join(lines[-1:]))
        if bool(state.get("auxiliaryDone")):
            fields["auxiliary_done"] = True
        if bool(state.get("auxiliaryTruncated")):
            fields["auxiliary_truncated"] = True
        return fields

    def _prune_locked(self) -> None:
        if len(self.states) <= self.state_limit:
            return
        oldest = sorted(self.states.items(), key=lambda item: float(item[1].get("updatedTs") or 0))[: self.prune_count]
        for key, _value in oldest:
            self.states.pop(key, None)


class YoagentStreamPublisher:
    def __init__(
        self,
        *,
        publish_client_event: Callable[..., Any],
        publish_stream_delta: Callable[..., Any] | None = None,
        store: YoagentStreamStateStore | None = None,
        now_iso: Callable[[], str] | None = None,
    ) -> None:
        self.publish_client_event = publish_client_event
        self.publish_stream_delta = publish_stream_delta or self.publish_delta
        self.store = store or YoagentStreamStateStore()
        self.now_iso = now_iso or (lambda: datetime.now(timezone.utc).isoformat())

    def publish_delta(
        self,
        stream_id: str,
        content: str,
        *,
        backend: str = "",
        phase: str = "",
        done: bool = False,
        hidden_thinking_removed: bool = False,
        events: list[dict[str, Any]] | None = None,
        auxiliary_lines: list[str] | None = None,
        auxiliary_preview: str = "",
        stream_items: list[dict[str, str]] | None = None,
        hidden_work_active: bool = False,
        tool_active: bool = False,
        auxiliary_done: bool = False,
        auxiliary_truncated: bool = False,
        turn_done: bool = False,
        error: bool = False,
        aborted: bool = False,
        created_at: str = "",
    ) -> None:
        safe_stream_id = str(stream_id or "").strip()
        if not safe_stream_id:
            return
        payload: dict[str, Any] = {
            "stream_id": safe_stream_id,
            "content": truncate_text(str(content or ""), 20_000),
            "done": bool(done),
            "running": not bool(done),
            "turn_done": bool(turn_done or done),
            "error": bool(error),
            "aborted": bool(aborted),
            "created_at": created_at or self.now_iso(),
        }
        if backend:
            payload["backend"] = backend
        if phase:
            payload["phase"] = phase
        if hidden_thinking_removed:
            payload["hidden_thinking_removed"] = True
        if events:
            payload["events"] = events
        if auxiliary_lines:
            payload["auxiliary_lines"] = [str(line or "") for line in auxiliary_lines if str(line or "").strip()]
        if auxiliary_preview:
            payload["auxiliary_preview"] = str(auxiliary_preview)
        clean_stream_items = sanitized_stream_items(stream_items)
        if clean_stream_items:
            payload["stream_items"] = clean_stream_items
        if hidden_work_active:
            payload["hidden_work_active"] = True
        if tool_active:
            payload["tool_active"] = True
        if auxiliary_done:
            payload["auxiliary_done"] = True
        if auxiliary_truncated:
            payload["auxiliary_truncated"] = True
        self.publish_client_event("yoagent_stream_delta", payload, trigger="yoagent_stream", cache="delta")

    def auxiliary_message_fields(self, stream_id: str) -> dict[str, Any]:
        return self.store.auxiliary_message_fields(stream_id)

    def callback_for(self, stream_id: str, backend: str) -> Callable[[dict[str, Any]], None]:
        state: dict[str, Any] = {
            "raw_content": "",
            "last_content": None,
            "hidden_thinking_removed": False,
            "auxiliary_lines": [],
            "hidden_work_prefix": "",
            "hidden_work_text": "",
            "hidden_work_active": False,
            "tool_active": False,
            "auxiliary_done": False,
            "auxiliary_truncated": False,
            "stream_items": [],
        }

        def publish(events: list[dict[str, Any]], phase: str = "") -> None:
            lines = list(state.get("auxiliary_lines") or [])
            stream_items = sanitized_stream_items(state.get("stream_items"))
            active = bool(state.get("hidden_work_active") or state.get("tool_active"))
            preview_count = 2 if active else 1
            preview = "\n".join(lines[-preview_count:]) if lines else ""
            self.store.store_callback_state(
                stream_id,
                auxiliary_lines=lines,
                auxiliary_preview=preview,
                stream_items=stream_items,
                auxiliary_done=bool(state.get("auxiliary_done")),
                auxiliary_truncated=bool(state.get("auxiliary_truncated")),
            )
            self.publish_stream_delta(
                stream_id,
                str(state.get("last_content") or ""),
                backend=backend,
                phase=phase,
                hidden_thinking_removed=bool(state.get("hidden_thinking_removed")),
                events=events,
                auxiliary_lines=lines,
                auxiliary_preview=preview,
                stream_items=stream_items,
                hidden_work_active=bool(state.get("hidden_work_active")),
                tool_active=bool(state.get("tool_active")),
                auxiliary_done=bool(state.get("auxiliary_done")),
                auxiliary_truncated=bool(state.get("auxiliary_truncated")),
                turn_done=phase == "done",
                error=phase == "error",
            )

        def hidden_work_prefix(event: dict[str, Any]) -> str:
            if event.get("summary"):
                return "thinking summary"
            if event.get("redacted"):
                return "redacted thinking"
            return "thinking"

        def compact_auxiliary_text(value: str) -> str:
            return " ".join(str(value or "").split())

        def hidden_work_text_is_heartbeat(prefix: str, value: str) -> bool:
            compact = compact_auxiliary_text(value).lower()
            if not compact:
                return False
            safe_prefix = compact_auxiliary_text(prefix).lower() or "thinking"
            roots = {safe_prefix, "thinking", "reasoning"}
            if safe_prefix.endswith("summary"):
                roots.update({"thinking summary", "reasoning summary"})
            for root in roots:
                if compact == root or compact == f"{root}..." or compact.startswith(f"{root}... "):
                    return True
            return False

        def hidden_work_auxiliary_line(prefix: str, text: str) -> str:
            compact = compact_auxiliary_text(text)
            if not compact:
                return prefix
            if compact.lower() == (compact_auxiliary_text(prefix).lower() or "thinking"):
                return prefix
            if hidden_work_text_is_heartbeat(prefix, compact):
                return compact
            return f"{prefix}: {compact}"

        def append_stream_item(kind: str, text: str, *, merge: bool = True) -> None:
            safe_kind = str(kind or "").strip().lower()
            value = redacted_action_text(str(text or ""), None)
            if safe_kind not in {"assistant", "thinking", "tool"} or not value:
                return
            items = sanitized_stream_items(state.get("stream_items"))
            if merge and items and items[-1].get("kind") == safe_kind:
                joiner = "" if safe_kind == "assistant" else "\n"
                items[-1]["text"] = str(items[-1].get("text") or "") + joiner + value
            else:
                items.append({"kind": safe_kind, "text": value})
            state["stream_items"] = items

        def replace_or_append_stream_item(kind: str, text: str) -> None:
            safe_kind = str(kind or "").strip().lower()
            value = redacted_action_text(str(text or ""), None)
            if safe_kind not in {"assistant", "thinking", "tool"} or not value:
                return
            items = sanitized_stream_items(state.get("stream_items"))
            if items and items[-1].get("kind") == safe_kind:
                items[-1]["text"] = value
            else:
                items.append({"kind": safe_kind, "text": value})
            state["stream_items"] = items

        def hidden_work_stream_item_text(event: dict[str, Any]) -> str:
            prefix = str(state.get("hidden_work_prefix") or hidden_work_prefix(event) or "thinking")
            raw_text = str(state.get("hidden_work_text") or event.get("text") or "")
            compact_text = compact_auxiliary_text(raw_text)
            if raw_text:
                if hidden_work_text_is_heartbeat(prefix, raw_text):
                    return compact_text or prefix
                return f"{prefix}: {raw_text}"
            if compact_text:
                return f"{prefix}: {compact_text}"
            return yoagent_stream_event_auxiliary_line(event)

        def append_auxiliary_line(event: dict[str, Any]) -> None:
            line = yoagent_stream_event_auxiliary_line(event)
            if not line:
                return
            event_type = str(event.get("kind") or event.get("event") or "")
            lines = list(state.get("auxiliary_lines") or [])
            if event_type == HIDDEN_WORK_DELTA:
                prefix = hidden_work_prefix(event)
                raw_text = str(event.get("text") or "")
                compact_text = compact_auxiliary_text(raw_text)
                if not compact_text:
                    compact_text = compact_auxiliary_text(line.removeprefix(f"{prefix}:").strip()) or prefix
                previous_prefix = str(state.get("hidden_work_prefix") or "")
                if lines and previous_prefix == prefix:
                    previous_text = str(state.get("hidden_work_text") or "")
                    incoming_is_heartbeat = hidden_work_text_is_heartbeat(prefix, compact_text)
                    previous_is_heartbeat = hidden_work_text_is_heartbeat(prefix, previous_text)
                    if incoming_is_heartbeat and previous_text and not previous_is_heartbeat:
                        next_text = previous_text
                    elif incoming_is_heartbeat:
                        next_text = compact_text
                    elif event.get("snapshot"):
                        next_text = raw_text
                    elif previous_is_heartbeat:
                        next_text = raw_text
                    else:
                        next_text = previous_text + raw_text
                    state["hidden_work_text"] = next_text
                    lines[-1] = hidden_work_auxiliary_line(prefix, next_text)
                else:
                    state["hidden_work_prefix"] = prefix
                    state["hidden_work_text"] = compact_text if hidden_work_text_is_heartbeat(prefix, compact_text) else raw_text
                    lines.append(hidden_work_auxiliary_line(prefix, compact_text))
            elif event_type in {TOOL_CALL_STARTED, TOOL_CALL_DELTA, TOOL_CALL_FINISHED}:
                state["hidden_work_prefix"] = ""
                state["hidden_work_text"] = ""
                value = str(line).strip()
                if value:
                    lines.append(value)
            else:
                state["hidden_work_prefix"] = ""
                state["hidden_work_text"] = ""
                for part in str(line).splitlines() or [line]:
                    value = part.strip()
                    if value:
                        lines.append(value)
            state["auxiliary_lines"] = lines

        def callback(event: dict[str, Any]) -> None:
            normalized = normalize_yoagent_stream_event(event, backend=backend)
            event_type = str(normalized.get("kind") or normalized.get("event") or "")
            if event_type == ASSISTANT_DELTA:
                incoming_text = str(normalized.get("text") or "")
                if normalized.get("snapshot"):
                    state["raw_content"] = incoming_text
                else:
                    state["raw_content"] = str(state.get("raw_content") or "") + incoming_text
                visible_text, hidden_thinking_removed = strip_yoagent_stream_hidden_thinking(str(state.get("raw_content") or ""))
                hidden_changed = bool(hidden_thinking_removed and not state.get("hidden_thinking_removed"))
                state["hidden_thinking_removed"] = bool(state.get("hidden_thinking_removed") or hidden_thinking_removed)
                if visible_text == state.get("last_content") and not hidden_changed:
                    return
                previous_visible = str(state.get("last_content") or "")
                state["last_content"] = visible_text
                visible_delta = visible_text
                if normalized.get("snapshot"):
                    visible_delta = visible_text
                elif previous_visible and visible_text.startswith(previous_visible):
                    visible_delta = visible_text[len(previous_visible) :]
                append_stream_item("assistant", visible_delta)
                publish([normalized], phase="answer" if visible_text else "thinking")
                return
            if event_type == HIDDEN_WORK_DELTA:
                state["hidden_thinking_removed"] = True
                state["hidden_work_active"] = True
                state["auxiliary_done"] = False
                append_auxiliary_line(normalized)
                replace_or_append_stream_item("thinking", hidden_work_stream_item_text(normalized))
                publish([normalized], phase="thinking")
                return
            if event_type in {TOOL_CALL_STARTED, TOOL_CALL_DELTA}:
                state["tool_active"] = True
                state["auxiliary_done"] = False
                append_auxiliary_line(normalized)
                append_stream_item("tool", yoagent_stream_event_auxiliary_line(normalized), merge=False)
                publish([normalized], phase="tool")
                return
            if event_type in {TOOL_CALL_FINISHED, HIDDEN_WORK_DONE}:
                if event_type == TOOL_CALL_FINISHED:
                    state["tool_active"] = False
                if event_type == HIDDEN_WORK_DONE:
                    state["hidden_work_active"] = False
                append_auxiliary_line(normalized)
                if event_type == TOOL_CALL_FINISHED:
                    append_stream_item("tool", yoagent_stream_event_auxiliary_line(normalized), merge=False)
                publish([normalized], phase="tool" if event_type == TOOL_CALL_FINISHED else "thinking")
                return
            if event_type == USAGE:
                publish([normalized], phase="usage")
                return
            if event_type in {TURN_DONE, ERROR}:
                state["hidden_work_active"] = False
                state["tool_active"] = False
                state["auxiliary_done"] = True
                append_auxiliary_line(normalized)
                publish([normalized], phase="done" if event_type == TURN_DONE else "error")
                return
            append_auxiliary_line(normalized)
            publish([normalized], phase=str(normalized.get("native_type") or "event"))

        return callback
