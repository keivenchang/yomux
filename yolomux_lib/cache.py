# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small cache helpers shared by modules that cannot import through common.py."""

from __future__ import annotations

import threading
import time
from typing import Any


class TtlCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 1024):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.lock = threading.Lock()
        self.values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        now = time.monotonic()
        with self.lock:
            item = self.values.get(key)
            if item is None:
                return default
            expires_at, value = item
            if expires_at <= now:
                self.values.pop(key, None)
                return default
            return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        now = time.monotonic()
        expires_at = now + (self.ttl_seconds if ttl is None else ttl)
        with self.lock:
            self.values[key] = (expires_at, value)
            if len(self.values) <= self.max_entries:
                return
            for dead in [name for name, (expiry, _) in self.values.items() if expiry <= now]:
                self.values.pop(dead, None)
            if len(self.values) <= self.max_entries:
                return
            overflow = len(self.values) - self.max_entries
            for stale in sorted(self.values, key=lambda name: self.values[name][0])[:overflow]:
                self.values.pop(stale, None)
