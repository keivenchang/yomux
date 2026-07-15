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
                # Keep the persistent lock's timestamp meaningful: it records the last process
                # that acquired exclusive ownership without changing the lock's contents.
                os.utime(lock_path, None)
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_or_create_secret_key(path: Path, num_bytes: int = 32) -> bytes:
    """Load a raw secret key from `path`, creating it race-safely on first use.

    The chat cursor codec and the login rate-limiter both need a private HMAC key that
    is generated once and shared across every process pointing at the same state
    directory. This is the one owner of that pattern (raw bytes, mode 0600, exclusive
    create so two concurrent starters agree on one key). The auth-cookie secret is
    deliberately NOT routed here: it stores a hex-text form with truncate-on-rewrite
    semantics, and unifying it would change that on-disk format and invalidate every
    live login cookie.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        secret = path.read_bytes()
    except FileNotFoundError:
        secret = os.urandom(num_bytes)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # A concurrent starter won the create; adopt its key so all agree.
            secret = path.read_bytes()
        else:
            with os.fdopen(descriptor, "wb") as output:
                output.write(secret)
    if len(secret) < num_bytes:
        raise ValueError(f"secret key at {path} is too short ({len(secret)} < {num_bytes} bytes)")
    return secret


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
