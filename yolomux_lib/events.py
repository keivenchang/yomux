from __future__ import annotations

import collections
import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from .common import CONFIG_DIR
from .common import MAX_EVENT_TAIL_LINES
from .common import STATE_PATH
from .common import truncate_text


_STATE_LOCK = threading.RLock()


@contextmanager
def locked_yolomux_state_file() -> Any:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_PATH.with_name(f".{STATE_PATH.name}.lock")
    with _STATE_LOCK:
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_yolomux_state_unlocked() -> dict[str, Any]:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def read_yolomux_state() -> dict[str, Any]:
    with locked_yolomux_state_file():
        return _read_yolomux_state_unlocked()


def _write_yolomux_state_unlocked(state: dict[str, Any]) -> None:
    tmp_path = STATE_PATH.with_name(f".{STATE_PATH.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(STATE_PATH)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def write_yolomux_state(state: dict[str, Any]) -> None:
    with locked_yolomux_state_file():
        _write_yolomux_state_unlocked(state)


def update_yolomux_state(updates: dict[str, Any]) -> None:
    with locked_yolomux_state_file():
        state = _read_yolomux_state_unlocked()
        state.update(updates)
        _write_yolomux_state_unlocked(state)

def utc_event_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

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

class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_name(f".{path.name}.lock")
        self.lock = threading.Lock()

    def append(self, session: str | None, event_type: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "time": utc_event_time(),
            "session": session or "",
            "type": event_type,
            "message": truncate_text(message, 2000),
            "details": safe_event_details(details or {}),
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

    def tail(self, session: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
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
                    keep.append(event)
        except OSError:
            return []
        return list(keep)

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
