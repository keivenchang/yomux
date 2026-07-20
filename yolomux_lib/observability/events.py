from __future__ import annotations

import collections
import fcntl
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable
from typing import TypeVar

from ..atomic_file import atomic_write_text
from ..atomic_file import file_lock
from ..common import MAX_EVENT_TAIL_LINES
from ..common import CONFIG_DIR  # noqa: F401 - module-level compatibility seam for state-path test fixtures
from ..common import RUN_HISTORY_PATH
from ..common import STATE_PATH
from ..common import truncate_text
from ..filesystem.io_ops import read_json_file
from ..locales import message_descriptor
from ..locales import message_fields
from ..types import RunHistoryEntry
from ..types import SearchResult


T = TypeVar("T")


@contextmanager
def locked_yolomux_state_file() -> Any:
    # lock + atomic-write machinery shared via atomic_file (with settings.py / yolo_rules).
    with file_lock(STATE_PATH):
        yield


def _read_yolomux_state_unlocked() -> dict[str, Any]:
    state = read_json_file(STATE_PATH, {})
    return state if isinstance(state, dict) else {}


def read_yolomux_state() -> dict[str, Any]:
    with locked_yolomux_state_file():
        return _read_yolomux_state_unlocked()


def _write_yolomux_state_unlocked(state: dict[str, Any]) -> None:
    atomic_write_text(STATE_PATH, json.dumps(state, indent=2, sort_keys=True) + "\n")


def write_yolomux_state(state: dict[str, Any]) -> None:
    with locked_yolomux_state_file():
        _write_yolomux_state_unlocked(state)


def update_yolomux_state(updates: dict[str, Any]) -> None:
    with locked_yolomux_state_file():
        state = _read_yolomux_state_unlocked()
        state.update(updates)
        _write_yolomux_state_unlocked(state)


def mutate_yolomux_state(mutator: Callable[[dict[str, Any]], T]) -> T:
    """Apply a state transition while holding the shared cross-server state lock."""
    with locked_yolomux_state_file():
        state = _read_yolomux_state_unlocked()
        result = mutator(state)
        _write_yolomux_state_unlocked(state)
        return result


def utc_event_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def compact_search_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value or "")


def search_snippet(text: str, query: str, max_chars: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    needle = str(query or "").strip().lower()
    if not compact:
        return ""
    if not needle:
        return truncate_text(compact, max_chars)
    index = compact.lower().find(needle)
    if index < 0:
        return truncate_text(compact, max_chars)
    half = max(20, max_chars // 2)
    start = max(0, index - half)
    end = min(len(compact), index + len(needle) + half)
    if end - start > max_chars:
        end = min(len(compact), start + max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


def event_search_text(event: dict[str, Any]) -> str:
    parts = [
        event.get("type", ""),
        event.get("message", ""),
        compact_search_text(event.get("details", {})),
    ]
    return " ".join(str(part) for part in parts if part)


def event_search_result(event: dict[str, Any], query: str) -> SearchResult:
    session = str(event.get("session") or "")
    timestamp = str(event.get("time") or "")
    kind = str(event.get("type") or "event")
    message = str(event.get("message") or kind)
    message_key = str(event.get("message_key") or "")
    message_params = event.get("message_params") if isinstance(event.get("message_params"), dict) else {}
    snippet = search_snippet(event_search_text(event), query)
    return {
        "session": session,
        "timestamp": timestamp,
        "kind": kind,
        "source": "event",
        **message_fields("title", message_key, message, message_params),
        **message_fields("snippet", message_key, snippet, message_params),
        "target": {
            "type": "events",
            "session": session,
            "timestamp": timestamp,
            "tab": "events",
        },
    }


def safe_event_details(details: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in details.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = truncate_text(value, 2000) if isinstance(value, str) else value
        elif isinstance(value, list):
            safe[key] = [
                truncate_text(item, 1000) if isinstance(item, str) else item
                for item in value
                if isinstance(item, (str, int, float, bool))
            ][:20]
        elif isinstance(value, dict):
            safe[key] = {
                str(item_key): truncate_text(item_value, 1000) if isinstance(item_value, str) else item_value
                for item_key, item_value in value.items()
                if isinstance(item_value, (str, int, float, bool))
            }
    return safe


def safe_message_params(params: dict[str, Any]) -> dict[str, Any]:
    """Bound persisted interpolation data while retaining nested message descriptors."""
    safe: dict[str, Any] = {}
    for name, value in list(params.items())[:20]:
        key = str(name)
        if isinstance(value, str):
            safe[key] = truncate_text(value, 1000)
        elif isinstance(value, (int, float, bool)):
            safe[key] = value
        elif isinstance(value, dict) and ("key" in value or "fallback" in value):
            nested_params = value.get("params") if isinstance(value.get("params"), dict) else {}
            safe[key] = message_descriptor(
                truncate_text(str(value.get("key") or ""), 200),
                truncate_text(str(value.get("fallback") or ""), 1000),
                safe_message_params(nested_params),
            )
    return safe


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_name(f".{path.name}.lock")
        self.lock = threading.Lock()

    def append(
        self,
        session: str | None,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        message_key: str = "",
        message_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "time": utc_event_time(),
            "session": session or "",
            "type": event_type,
            "details": safe_event_details(details or {}),
            **message_fields(
                "message",
                message_key,
                truncate_text(message, 2000),
                safe_message_params(message_params or {}),
            ),
        }
        line = json.dumps(event, sort_keys=True, ensure_ascii=False)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    with self.path.open("a", encoding="utf-8") as handle:
                        handle.write(line + "\n")
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return event

    def _reverse_lines(self) -> Any:
        chunk_size = 65536
        pending = b""
        with self.path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            while position > 0:
                step = min(chunk_size, position)
                position -= step
                handle.seek(position)
                data = handle.read(step) + pending
                lines = data.split(b"\n")
                pending = lines[0]
                for line in reversed(lines[1:]):
                    if line:
                        yield line.decode("utf-8", errors="replace")
            if pending:
                yield pending.decode("utf-8", errors="replace")

    def _reverse_events(self) -> Any:
        for line in self._reverse_lines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event

    @staticmethod
    def _event_matches_session(event: dict[str, Any], session: str | None) -> bool:
        return not session or event.get("session") in {session, ""}

    def tail(self, session: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        keep: list[dict[str, Any]] = []
        try:
            for event in self._reverse_events():
                if not self._event_matches_session(event, session):
                    continue
                keep.append(event)
                if len(keep) >= bounded_limit:
                    break
        except OSError:
            return []
        return list(reversed(keep))

    def tail_many(self, sessions: list[str] | tuple[str, ...] | set[str], limit: int = 100) -> dict[str, list[dict[str, Any]]]:
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        ordered_sessions = [str(session) for session in sessions if str(session)]
        remaining = set(ordered_sessions)
        keep = {session: [] for session in ordered_sessions}
        if not ordered_sessions:
            return keep
        try:
            for event in self._reverse_events():
                event_session = str(event.get("session") or "")
                matched = ordered_sessions if event_session == "" else [event_session]
                for session in matched:
                    if session not in remaining:
                        continue
                    if event_session and event_session != session:
                        continue
                    keep[session].append(event)
                    if len(keep[session]) >= bounded_limit:
                        remaining.discard(session)
                if not remaining:
                    break
        except OSError:
            return {session: [] for session in ordered_sessions}
        return {session: list(reversed(events)) for session, events in keep.items()}


    def search(self, query: str, session: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        needle = query.strip().lower()
        if not needle:
            return []
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        keep: collections.deque[dict[str, Any]] = collections.deque(maxlen=bounded_limit)
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if session and event.get("session") not in {session, ""}:
                        continue
                    text = json.dumps(event, sort_keys=True, ensure_ascii=False).lower()
                    if needle in text:
                        keep.append(event)
        except OSError:
            return []
        return list(keep)

    def search_results(self, query: str, session: str | None = None, limit: int = 100) -> list[SearchResult]:
        needle = query.strip().lower()
        if not needle:
            return []
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        keep: collections.deque[SearchResult] = collections.deque(maxlen=bounded_limit)
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if session and event.get("session") not in {session, ""}:
                        continue
                    if needle in event_search_text(event).lower():
                        keep.append(event_search_result(event, query))
        except OSError:
            return []
        return list(keep)


def run_history_sort_ts(row: dict[str, Any]) -> float:
    for key in ("ended_ts", "started_ts", "transcript_mtime", "updated_ts"):
        try:
            value = float(row.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return 0.0


def clean_run_history_row(row: dict[str, Any]) -> RunHistoryEntry:
    clean: RunHistoryEntry = {
        "id": truncate_text(str(row.get("id") or row.get("session") or ""), 500),
        "session": truncate_text(str(row.get("session") or ""), 200),
        "agent": row.get("agent") if isinstance(row.get("agent"), dict) else None,
        "prompt": truncate_text(str(row.get("prompt") or ""), 1200),
        "cwd": truncate_text(str(row.get("cwd") or ""), 2000),
        "tmux_target": truncate_text(str(row.get("tmux_target") or ""), 200),
        "tmux_command": truncate_text(str(row.get("tmux_command") or ""), 500),
        "started_at": truncate_text(str(row.get("started_at") or ""), 80),
        "ended_at": truncate_text(str(row.get("ended_at") or ""), 80),
        "final_state": truncate_text(str(row.get("final_state") or ""), 80),
        "pr": row.get("pr") if isinstance(row.get("pr"), dict) else None,
        "latest_summary": truncate_text(str(row.get("latest_summary") or ""), 1200),
        "transcript": truncate_text(str(row.get("transcript") or ""), 2000),
        "project": row.get("project") if isinstance(row.get("project"), dict) else {},
        "recent_events": row.get("recent_events") if isinstance(row.get("recent_events"), list) else [],
    }
    for key in ("started_ts", "ended_ts", "latest_summary_updated_ts", "transcript_mtime"):
        try:
            clean[key] = max(0.0, float(row.get(key) or 0))  # type: ignore[literal-required]
        except (TypeError, ValueError):
            clean[key] = 0.0  # type: ignore[literal-required]
    return clean


class RunHistoryStore:
    def __init__(self, path: Path = RUN_HISTORY_PATH, max_rows: int = 500):
        self.path = path
        self.max_rows = max(1, int(max_rows))

    def read_rows_unlocked(self) -> list[RunHistoryEntry]:
        payload = read_json_file(self.path, {})
        rows = payload.get("runs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        return [clean_run_history_row(row) for row in rows if isinstance(row, dict)]

    def load_rows(self, session: str | None = None) -> list[RunHistoryEntry]:
        with file_lock(self.path):
            rows = self.read_rows_unlocked()
        if session:
            rows = [row for row in rows if row.get("session") == session]
        rows.sort(key=lambda item: (-run_history_sort_ts(item), str(item.get("session") or "")))
        return rows[: self.max_rows]

    def upsert_rows(self, rows: list[dict[str, Any]]) -> list[RunHistoryEntry]:
        clean_updates = [clean_run_history_row(row) for row in rows if isinstance(row, dict) and (row.get("id") or row.get("session"))]
        with file_lock(self.path):
            existing = self.read_rows_unlocked()
            by_id = {str(row.get("id") or row.get("session") or ""): row for row in existing}
            for row in clean_updates:
                key = str(row.get("id") or row.get("session") or "")
                if key:
                    by_id[key] = row
            merged = list(by_id.values())
            merged.sort(key=lambda item: (-run_history_sort_ts(item), str(item.get("session") or "")))
            merged = merged[: self.max_rows]
            payload = {
                "version": 1,
                "updated_at": utc_event_time(),
                "runs": merged,
            }
            atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        return merged
