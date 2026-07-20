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
import sqlite3
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

    Concurrency: an in-process lock serializes threads, and the exclusive create plus a
    bounded read-retry covers cross-process races — a peer that lost the create must not
    read the file in the window after O_EXCL creation but before the bytes are flushed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        for _ in range(200):
            try:
                secret = path.read_bytes()
            except FileNotFoundError:
                secret = os.urandom(num_bytes)
                try:
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    # A concurrent starter won the create; loop to read its key.
                    continue
                with os.fdopen(descriptor, "wb") as output:
                    output.write(secret)
                    output.flush()
                    os.fsync(output.fileno())
                return secret
            if len(secret) >= num_bytes:
                return secret
            # The creator has claimed the file but not finished writing; brief retry.
            time.sleep(0.005)
        raise ValueError(f"secret key at {path} did not become readable ({num_bytes} bytes)")


def open_wal_database(path: Path, busy_timeout_ms: int, *, row_factory: Any = None) -> sqlite3.Connection:
    """Open a WAL-friendly SQLite connection under a private 0700 parent dir.

    chat_store and the login rate-limiter both open a multi-writer WAL database with the
    same connect timeout + busy_timeout so concurrent writers wait rather than error;
    this is the one owner of that open dance (callers add their own schema-specific
    PRAGMAs like foreign_keys). `isolation_level=None` keeps autocommit so callers drive
    explicit BEGIN IMMEDIATE transactions.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    connection = sqlite3.connect(path, timeout=busy_timeout_ms / 1000, isolation_level=None)
    if row_factory is not None:
        connection.row_factory = row_factory
    connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    return connection


def enable_wal_with_retry(connection: sqlite3.Connection, attempts: int = 6) -> None:
    """Turn on WAL journaling, retrying briefly while another connection holds the lock
    during the mode switch. Shared by every store that opens the file from more than one
    process."""
    for attempt in range(attempts):
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                raise
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


@contextmanager
def open_wal_session(initialize: Any, connect: Any) -> Any:
    """Run `initialize()` (lazy schema migration), open a fresh connection via `connect()`,
    yield it, and always close it. The shared per-operation connection lifecycle for the
    WAL stores so each caller's `_connection` is a one-line delegator rather than a copy."""
    initialize()
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def begin_wal_migration(connection: sqlite3.Connection) -> int:
    """Enable WAL, enter an exclusive (BEGIN IMMEDIATE) migration transaction, and return
    the current PRAGMA user_version. The shared prologue for every user_version-migrated
    store; each caller then branches on the returned version with its own schema steps and
    commits. Callers hold their own init lock and roll back / close on error."""
    enable_wal_with_retry(connection)
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("BEGIN IMMEDIATE")
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


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
