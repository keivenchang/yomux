from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any


SERVER_LOG_CAPACITY = 500
SERVER_LOG_LEVELS = frozenset({"debug", "info", "warning", "error"})
SERVER_LOG_MESSAGE_MAX_CHARS = 4096


class ServerLogRing:
    """Thread-safe, drop-oldest log history for operator-visible diagnostics."""

    def __init__(self, capacity: int = SERVER_LOG_CAPACITY) -> None:
        self.capacity = max(1, int(capacity))
        self._entries: deque[dict[str, Any]] = deque(maxlen=self.capacity)
        self._dedupe_until: dict[str, float] = {}
        self._sequence = 0
        self._lock = threading.Lock()

    def emit(
        self,
        level: str,
        source: str,
        message: str,
        *,
        category: str = "server",
        dedupe_key: str = "",
        dedupe_seconds: float = 0.0,
    ) -> dict[str, Any] | None:
        normalized_level = str(level or "info").strip().lower()
        if normalized_level not in SERVER_LOG_LEVELS:
            raise ValueError(f"unsupported server log level: {level}")
        now = time.time()
        monotonic_now = time.monotonic()
        with self._lock:
            if dedupe_key:
                expires_at = self._dedupe_until.get(dedupe_key, 0.0)
                if monotonic_now < expires_at:
                    return None
                self._dedupe_until[dedupe_key] = monotonic_now + max(0.0, float(dedupe_seconds))
                if len(self._dedupe_until) > self.capacity * 4:
                    self._dedupe_until = {
                        key: expiry for key, expiry in self._dedupe_until.items() if expiry > monotonic_now
                    }
            self._sequence += 1
            entry = {
                "id": self._sequence,
                "timestamp": now,
                "level": normalized_level,
                "source": str(source or "server"),
                "category": str(category or "server"),
                "message": str(message),
            }
            self._entries.append(entry)
            return dict(entry)

    def payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "logs": [dict(entry) for entry in self._entries],
                "sequence": self._sequence,
                "capacity": self.capacity,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._dedupe_until.clear()
            self._sequence = 0


SERVER_LOGS = ServerLogRing()


class ServerLogHandler(logging.Handler):
    """Mirror process warnings/errors into the operator-visible bounded ring."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = str(record.msg)
        level = "error" if record.levelno >= logging.ERROR else "warning" if record.levelno >= logging.WARNING else "info"
        SERVER_LOGS.emit(
            level,
            record.name or "server",
            message[:SERVER_LOG_MESSAGE_MAX_CHARS],
            category="python",
        )


def install_server_log_handler() -> ServerLogHandler:
    """Install the one root handler that feeds the in-memory operator log."""

    root = logging.getLogger()
    existing = next((handler for handler in root.handlers if isinstance(handler, ServerLogHandler)), None)
    if existing is not None:
        return existing
    handler = ServerLogHandler(level=logging.WARNING)
    root.addHandler(handler)
    return handler


def emit_server_log(
    level: str,
    source: str,
    message: str,
    *,
    category: str = "server",
    dedupe_key: str = "",
    dedupe_seconds: float = 0.0,
) -> dict[str, Any] | None:
    return SERVER_LOGS.emit(
        level,
        source,
        message,
        category=category,
        dedupe_key=dedupe_key,
        dedupe_seconds=dedupe_seconds,
    )


def server_logs_payload() -> dict[str, Any]:
    return SERVER_LOGS.payload()
