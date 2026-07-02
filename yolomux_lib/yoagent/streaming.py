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
from .backends import strip_yoagent_stream_hidden_thinking
from .conversation import bounded_auxiliary_lines
from .conversation import bounded_stream_items
from .conversation import sanitized_auxiliary_preview
from .conversation import YOAGENT_STREAM_ITEMS_LIMIT
from .conversation import YOAGENT_STREAM_ITEMS_TOTAL_LIMIT
from .stream_events import ASSISTANT_DELTA
from .stream_events import APPROVAL_REQUESTED
from .stream_events import ERROR
from .stream_events import HIDDEN_WORK_DELTA
from .stream_events import HIDDEN_WORK_DONE
from .stream_events import TOOL_CALL_DELTA
from .stream_events import TOOL_CALL_FINISHED
from .stream_events import TOOL_CALL_STARTED
from .stream_events import TURN_DONE
from .stream_events import USAGE
from .stream_events import normalize_yoagent_stream_event
from .stream_events import yoagent_stream_event_auxiliary_item


YOAGENT_STREAM_STATE_LIMIT = 50
YOAGENT_STREAM_STATE_PRUNE_COUNT = 10


def sanitized_stream_items(value: Any) -> list[dict[str, Any]]:
    items, _truncated = bounded_stream_items(value)
    return items


def copy_stream_items(value: Any) -> list[dict[str, Any]]:
    items, _truncated = bounded_stream_items(value)
    return items


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
        stream_items: list[dict[str, Any]],
        auxiliary_done: bool,
        auxiliary_truncated: bool,
        stream_items_sanitized: bool = False,
    ) -> None:
        safe_stream_id = str(stream_id or "").strip()
        if not safe_stream_id:
            return
        if stream_items_sanitized:
            clean_lines = auxiliary_lines
            lines_truncated = False
        else:
            clean_lines, lines_truncated = bounded_auxiliary_lines(auxiliary_lines)
        clean_preview, preview_truncated = sanitized_auxiliary_preview(auxiliary_preview, "\n".join(clean_lines[-1:]))
        if stream_items_sanitized:
            clean_stream_items = stream_items
            stream_items_truncated = False
        else:
            clean_stream_items, stream_items_truncated = bounded_stream_items(stream_items)
        with self.lock:
            self.states[safe_stream_id] = {
                "auxiliaryLines": clean_lines,
                "auxiliaryPreview": clean_preview,
                "streamItems": clean_stream_items,
                "auxiliaryDone": bool(auxiliary_done),
                "auxiliaryTruncated": bool(auxiliary_truncated or lines_truncated or preview_truncated or stream_items_truncated),
                "updatedTs": self.clock(),
            }
            self._prune_locked()

    def auxiliary_message_fields(self, stream_id: str) -> dict[str, Any]:
        safe_stream_id = str(stream_id or "").strip()
        if not safe_stream_id:
            return {}
        with self.lock:
            state = copy.deepcopy(self.states.get(safe_stream_id) or {})
        lines, lines_truncated = bounded_auxiliary_lines(state.get("auxiliaryLines"))
        stream_items, stream_items_truncated = bounded_stream_items(state.get("streamItems"))
        if not lines and not stream_items:
            return {}
        fields: dict[str, Any] = {"stream_items": stream_items}
        if lines:
            fields["auxiliary_lines"] = lines
            preview, preview_truncated = sanitized_auxiliary_preview(state.get("auxiliaryPreview"), "\n".join(lines[-1:]))
            fields["auxiliary_preview"] = preview
            lines_truncated = lines_truncated or preview_truncated
        if bool(state.get("auxiliaryDone")):
            fields["auxiliary_done"] = True
        if bool(state.get("auxiliaryTruncated")) or lines_truncated or stream_items_truncated:
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
        stream_items: list[dict[str, Any]] | None = None,
        hidden_work_active: bool = False,
        tool_active: bool = False,
        auxiliary_done: bool = False,
        auxiliary_truncated: bool = False,
        turn_done: bool = False,
        error: bool = False,
        aborted: bool = False,
        created_at: str = "",
        stream_items_sanitized: bool = False,
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
        if stream_items_sanitized:
            clean_auxiliary_lines = list(auxiliary_lines)
            auxiliary_lines_truncated = False
        else:
            clean_auxiliary_lines, auxiliary_lines_truncated = bounded_auxiliary_lines(auxiliary_lines)
        if clean_auxiliary_lines:
            payload["auxiliary_lines"] = clean_auxiliary_lines
        clean_preview, auxiliary_preview_truncated = sanitized_auxiliary_preview(auxiliary_preview)
        if clean_preview:
            payload["auxiliary_preview"] = clean_preview
        if stream_items_sanitized:
            clean_stream_items = list(stream_items)
            stream_items_truncated = False
        else:
            clean_stream_items, stream_items_truncated = bounded_stream_items(stream_items)
        if clean_stream_items:
            payload["stream_items"] = clean_stream_items
        if hidden_work_active:
            payload["hidden_work_active"] = True
        if tool_active:
            payload["tool_active"] = True
        if auxiliary_done:
            payload["auxiliary_done"] = True
        if auxiliary_truncated or auxiliary_lines_truncated or auxiliary_preview_truncated or stream_items_truncated:
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
            "hidden_work_descriptor": "",
            "hidden_work_text": "",
            "hidden_work_active": False,
            "tool_active": False,
            "auxiliary_done": False,
            "auxiliary_truncated": False,
            "stream_items": [],
            "stream_items_length": 0,
        }

        def publish(events: list[dict[str, Any]], phase: str = "") -> None:
            stream_items = state["stream_items"]
            stream_items_truncated = False
            if len(stream_items) > YOAGENT_STREAM_ITEMS_LIMIT or state["stream_items_length"] > YOAGENT_STREAM_ITEMS_TOTAL_LIMIT:
                while len(stream_items) > 1 and (
                    len(stream_items) > YOAGENT_STREAM_ITEMS_LIMIT
                    or state["stream_items_length"] > YOAGENT_STREAM_ITEMS_TOTAL_LIMIT
                ):
                    state["stream_items_length"] -= len(str(stream_items.pop(0).get("text") or "")) + 1
                    stream_items_truncated = True
            if stream_items_truncated:
                state["auxiliary_truncated"] = True
                state["stream_items"] = stream_items
            active = bool(state.get("hidden_work_active") or state.get("tool_active"))
            preview_count = 2 if active else 1
            thinking_text = [
                str(item.get("text") or "")
                for item in stream_items
                if item.get("kind") == "thinking" and str(item.get("text") or "").strip()
            ]
            preview = "\n".join(thinking_text[-preview_count:]) if thinking_text else ""
            self.store.store_callback_state(
                stream_id,
                auxiliary_lines=[],
                auxiliary_preview=preview,
                stream_items=stream_items,
                auxiliary_done=bool(state.get("auxiliary_done")),
                auxiliary_truncated=bool(state.get("auxiliary_truncated")),
                stream_items_sanitized=True,
            )
            self.publish_stream_delta(
                stream_id,
                str(state.get("last_content") or ""),
                backend=backend,
                phase=phase,
                hidden_thinking_removed=bool(state.get("hidden_thinking_removed")),
                events=events,
                auxiliary_lines=[],
                auxiliary_preview=preview,
                stream_items=stream_items,
                hidden_work_active=bool(state.get("hidden_work_active")),
                tool_active=bool(state.get("tool_active")),
                auxiliary_done=bool(state.get("auxiliary_done")),
                auxiliary_truncated=bool(state.get("auxiliary_truncated")),
                turn_done=phase == "done",
                error=phase == "error",
                stream_items_sanitized=True,
            )

        def append_stream_item(item: dict[str, Any], *, merge: bool = True) -> None:
            clean, _truncated = bounded_stream_items([item])
            if not clean:
                return
            next_item = clean[0]
            safe_kind = str(next_item.get("kind") or "")
            value = str(next_item.get("text") or "")
            items = state.get("stream_items")
            if not isinstance(items, list):
                items = []
                state["stream_items"] = items
            same_descriptor = items and all(
                items[-1].get(field) == next_item.get(field)
                for field in ("kind", "eventKind", "label", "toolName")
            )
            if merge and same_descriptor:
                joiner = "" if safe_kind == "assistant" else "\n"
                previous = str(items[-1].get("text") or "")
                merged, _truncated = bounded_stream_items([{**next_item, "text": previous + joiner + value}])
                if not merged:
                    return
                items[-1] = merged[0]
                state["stream_items_length"] += len(str(merged[0].get("text") or "")) - len(previous)
            else:
                items.append(next_item)
                state["stream_items_length"] += len(value) + (1 if len(items) > 1 else 0)

        def replace_or_append_stream_item(item: dict[str, Any]) -> None:
            clean, _truncated = bounded_stream_items([item])
            if not clean:
                return
            next_item = clean[0]
            safe_kind = str(next_item.get("kind") or "")
            value = str(next_item.get("text") or "")
            items = state.get("stream_items")
            if not isinstance(items, list):
                items = []
                state["stream_items"] = items
            if items and items[-1].get("kind") == safe_kind:
                previous = str(items[-1].get("text") or "")
                items[-1] = next_item
                state["stream_items_length"] += len(value) - len(previous)
            else:
                items.append(next_item)
                state["stream_items_length"] += len(value) + (1 if len(items) > 1 else 0)

        def hidden_work_stream_item(event: dict[str, Any]) -> dict[str, Any]:
            item = yoagent_stream_event_auxiliary_item(event)
            descriptor = {
                "eventKind": str(item.get("eventKind") or ""),
                "label": item.get("label") if isinstance(item.get("label"), dict) else {},
            }
            previous_text = str(state.get("hidden_work_text") or "") if descriptor == state.get("hidden_work_descriptor") else ""
            incoming_text = str(event.get("text") or "")
            if event.get("heartbeat"):
                next_text = previous_text
            elif event.get("snapshot"):
                next_text = incoming_text
            else:
                next_text = previous_text + incoming_text
            state["hidden_work_descriptor"] = descriptor
            state["hidden_work_text"] = next_text
            item["text"] = next_text
            return item

        def callback(event: dict[str, Any]) -> None:
            normalized = normalize_yoagent_stream_event(event, backend=backend)
            event_type = str(normalized.get("kind") or normalized.get("event") or "")
            if event_type == ASSISTANT_DELTA:
                state["hidden_work_active"] = False
                state["hidden_work_descriptor"] = ""
                state["hidden_work_text"] = ""
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
                append_stream_item({"kind": "assistant", "text": visible_delta})
                publish([normalized], phase="answer" if visible_text else "thinking")
                return
            if event_type == HIDDEN_WORK_DELTA:
                state["hidden_thinking_removed"] = True
                state["hidden_work_active"] = True
                state["auxiliary_done"] = False
                replace_or_append_stream_item(hidden_work_stream_item(normalized))
                publish([normalized], phase="thinking")
                return
            if event_type in {TOOL_CALL_STARTED, TOOL_CALL_DELTA}:
                if event_type == TOOL_CALL_STARTED:
                    state["hidden_work_active"] = False
                    state["hidden_work_descriptor"] = ""
                    state["hidden_work_text"] = ""
                state["tool_active"] = True
                state["auxiliary_done"] = False
                append_stream_item(yoagent_stream_event_auxiliary_item(normalized), merge=False)
                publish([normalized], phase="tool")
                return
            if event_type == APPROVAL_REQUESTED:
                state["hidden_work_active"] = False
                state["hidden_work_descriptor"] = ""
                state["hidden_work_text"] = ""
                state["tool_active"] = False
                state["auxiliary_done"] = False
                append_stream_item(yoagent_stream_event_auxiliary_item(normalized), merge=False)
                publish([normalized], phase="approval")
                return
            if event_type in {TOOL_CALL_FINISHED, HIDDEN_WORK_DONE}:
                if event_type == TOOL_CALL_FINISHED:
                    state["tool_active"] = False
                if event_type == HIDDEN_WORK_DONE:
                    state["hidden_work_active"] = False
                    state["hidden_work_descriptor"] = ""
                    state["hidden_work_text"] = ""
                if event_type == TOOL_CALL_FINISHED:
                    append_stream_item(yoagent_stream_event_auxiliary_item(normalized), merge=False)
                elif event_type == HIDDEN_WORK_DONE:
                    append_stream_item(yoagent_stream_event_auxiliary_item(normalized), merge=False)
                publish([normalized], phase="tool" if event_type == TOOL_CALL_FINISHED else "thinking")
                return
            if event_type == USAGE:
                append_stream_item(yoagent_stream_event_auxiliary_item(normalized), merge=False)
                publish([normalized], phase="usage")
                return
            if event_type in {TURN_DONE, ERROR}:
                state["hidden_work_active"] = False
                state["hidden_work_descriptor"] = ""
                state["hidden_work_text"] = ""
                state["tool_active"] = False
                state["auxiliary_done"] = True
                if event_type == ERROR:
                    append_stream_item(yoagent_stream_event_auxiliary_item(normalized), merge=False)
                publish([normalized], phase="done" if event_type == TURN_DONE else "error")
                return
            publish([normalized], phase=str(normalized.get("native_type") or "event"))

        return callback
