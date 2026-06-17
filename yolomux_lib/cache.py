# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small cache helpers shared by modules that cannot import through common.py."""

from __future__ import annotations

import threading
import time
from typing import Any
from typing import Callable

# the one "not cached" sentinel, so callers can tell a cached None/empty value from a miss.
# common.py re-exports this as _CACHE_MISS (common imports cache, not the reverse), so metadata.py and
# sessions.py — which compared against common._CACHE_MISS — keep working after migrating onto get_or_miss().
MISS: Any = object()


class TtlCache:
    """One thread-safe TTL cache with bounded size. metadata.MetadataCache and the sessions
    transcript-lookup cache each re-implemented this eviction; they now route through it. `clock` is
    injectable so a caller needing wall-clock TTLs (or a test) can pass its own; default is monotonic."""

    def __init__(self, ttl_seconds: float, max_entries: int = 1024, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self.lock = threading.Lock()
        self.values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        result = self.get_or_miss(key)
        return default if result is MISS else result

    def get_or_miss(self, key: str) -> Any:
        """Return the cached value, or MISS if absent/expired — distinguishing a cached None from a miss."""
        now = self.clock()
        with self.lock:
            item = self.values.get(key)
            if item is None:
                return MISS
            expires_at, value = item
            if expires_at <= now:
                self.values.pop(key, None)
                return MISS
            return value

    def clear(self) -> None:
        with self.lock:
            self.values.clear()

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        now = self.clock()
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
