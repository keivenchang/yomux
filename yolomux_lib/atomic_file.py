# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Durable single-file persistence: a cross-process file lock and an atomic write.

settings.py, events.py, and yolo_rules.py each carried their own copy of an RLock+flock contextmanager and
the same `.{name}.{pid}.{tid}.{ns}.tmp` + fsync + os.replace dance, with the durability/permission
guarantees drifting between them. This is the one owner; callers pass the permission bits as data.

Import-light (stdlib only), like cache.py, so any module can use it without import-cycle worries.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# One in-process RLock per file path, so threads serialize before contending on the OS flock. A registry
# (rather than a per-caller module global) means two modules locking the same path share one lock.
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


@contextmanager
def file_lock(path: Path, dir_mode: int | None = None) -> Any:
    """Hold an exclusive in-process + cross-process lock for `path` (via a sibling `.<name>.lock` file).

    Creates the parent directory; `dir_mode` (e.g. 0o700) tightens its permissions when given.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if dir_mode is not None:
        path.parent.chmod(dir_mode)
    lock_path = path.with_name(f".{path.name}.lock")
    with _lock_for(path):
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Write `text` to `path` atomically: unique temp sibling, fsync, then os.replace.

    The temp name carries pid + thread id + ns so concurrent writers never collide. `mode` (e.g. 0o600)
    sets permissions on both the temp (at create) and the final file (after replace).
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if mode is None else mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        if mode is not None:
            path.chmod(mode)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
