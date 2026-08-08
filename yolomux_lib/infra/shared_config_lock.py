# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared-configuration locking and revisioned mapping updates.

This uses POSIX record locks (``fcntl.lockf``), never the native ``flock``
used for host-local runtime state.  A record lock is released by the kernel
when its owning process exits, including a crash.  Same-host tests establish
that behaviour, but they do not establish NFS interoperability.

Cross-host acceptance remains required: run one updater on the exporter-local
host and one on an NFSv4 client against the same configuration file, start both
at once, and prove that only one holds the POSIX lock while the other waits;
then repeat with different keys, same keys, and a killed holder.  Observe valid
complete YAML/JSON after every run and both independent keys in the final file.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterator
from typing import Mapping

import yaml

from .atomic_file import atomic_write_text
from .host_identity import current_host_identity


CROSS_HOST_ACCEPTANCE_CONTRACT = (
    "On lin1 (exporter-local) and lin2 (NFS client), run concurrent POSIX-record-lock updaters "
    "against the same ~/.config/yolomux/settings.yaml path. Observe one holder while the peer waits, "
    "then repeat after killing the holder; run different-key and same-key updates and verify valid "
    "complete YAML/JSON after every operation plus both independent keys in the final file."
)


class SharedConfigError(RuntimeError):
    """A shared configuration file cannot be read or updated safely."""


class SharedConfigRevisionConflict(SharedConfigError):
    """A whole-document shared-config write was stale and was not applied."""


_THREAD_LOCKS: dict[Path, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class SharedConfigUpdate:
    base_revision: str
    revision: str
    revision_conflict: bool
    owner_record: dict[str, Any]


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.shared-config.lock")


def _thread_lock(path: Path) -> threading.RLock:
    key = path.expanduser().resolve(strict=False)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def shared_config_lock(path: Path) -> Iterator[None]:
    """Hold the one POSIX record lock used by shared configuration writers."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(target):
        fd = os.open(_lock_path(target), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.lockf(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode_mapping(raw: bytes, *, file_format: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = yaml.safe_load(raw) if file_format == "yaml" else json.loads(raw)
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeDecodeError) as error:
        raise SharedConfigError(f"cannot parse shared {file_format} configuration") from error
    if decoded is None:
        return {}
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise SharedConfigError(f"shared {file_format} configuration must contain a mapping")
    return dict(decoded)


def _encode_mapping(mapping: Mapping[str, Any], *, file_format: str) -> str:
    if file_format == "yaml":
        return yaml.safe_dump(dict(mapping), allow_unicode=True, default_flow_style=False, sort_keys=True)
    return json.dumps(dict(mapping), ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def _validate_format(file_format: str) -> str:
    if file_format not in {"json", "yaml"}:
        raise ValueError(f"unsupported shared configuration format: {file_format}")
    return file_format


def read_shared_mapping(path: Path, *, file_format: str = "yaml") -> tuple[dict[str, Any], str]:
    """Read a complete structured configuration and its raw-byte revision under lock."""
    clean_format = _validate_format(file_format)
    with shared_config_lock(path):
        try:
            raw = Path(path).read_bytes()
        except FileNotFoundError:
            raw = b""
        return _decode_mapping(raw, file_format=clean_format), _revision(raw)


def read_shared_document(path: Path) -> tuple[str, str]:
    """Read a comment-preserving document and its revision under the shared lock."""
    with shared_config_lock(path):
        try:
            raw = Path(path).read_bytes()
        except FileNotFoundError:
            raw = b""
        return raw.decode("utf-8"), _revision(raw)


def write_shared_document(path: Path, text: str, *, expected_revision: str | None = None) -> str:
    """Atomically replace a hand-edited document only when its revision still matches."""
    target = Path(path)
    with shared_config_lock(target):
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            raw = b""
        current_revision = _revision(raw)
        if expected_revision is not None and expected_revision != current_revision:
            raise SharedConfigRevisionConflict("shared configuration document changed; re-read before retrying")
        atomic_write_text(target, text, mode=0o600)
        return _revision(text.encode("utf-8"))


def update_shared_mapping(
    path: Path,
    changes: Mapping[str, Any],
    *,
    expected_revision: str | None = None,
    file_format: str = "yaml",
) -> SharedConfigUpdate:
    """Merge top-level changes into the latest mapping and atomically publish it.

    A stale ``expected_revision`` is recorded rather than ignored.  The update is
    merged into the newest locked mapping, so stale independent-key updates survive
    instead of replacing a whole earlier document.
    """
    clean_format = _validate_format(file_format)
    if any(not isinstance(key, str) for key in changes):
        raise ValueError("shared configuration keys must be strings")
    target = Path(path)
    with shared_config_lock(target):
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            raw = b""
        current, base_revision = _decode_mapping(raw, file_format=clean_format), _revision(raw)
        merged = {**current, **dict(changes)}
        text = _encode_mapping(merged, file_format=clean_format)
        atomic_write_text(target, text, mode=0o600)
        identity = current_host_identity()
        return SharedConfigUpdate(
            base_revision=base_revision,
            revision=_revision(text.encode("utf-8")),
            revision_conflict=expected_revision is not None and expected_revision != base_revision,
            owner_record=identity.diagnostics(),
        )


def update_shared_yaml(
    path: Path,
    changes: Mapping[str, Any],
    *,
    expected_revision: str | None = None,
) -> SharedConfigUpdate:
    """Revisioned, key-level merged update for auth/settings/rules YAML files."""
    return update_shared_mapping(path, changes, expected_revision=expected_revision, file_format="yaml")


def update_shared_json(
    path: Path,
    changes: Mapping[str, Any],
    *,
    expected_revision: str | None = None,
) -> SharedConfigUpdate:
    """Revisioned, key-level merged update for shared JSON preference state."""
    return update_shared_mapping(path, changes, expected_revision=expected_revision, file_format="json")
