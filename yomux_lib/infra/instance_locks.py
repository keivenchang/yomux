# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Dependency-light root lock primitives shared by startup admission and leases."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .root_paths import YomuxRoots


def _try_lock_instance_path(path: Path, *, create: bool = True) -> int | None:
    fd = os.open(str(path), os.O_RDWR | (os.O_CREAT if create else 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _instance_path_tuple(paths_or_root: YomuxRoots | Path) -> tuple[Path, ...]:
    if isinstance(paths_or_root, YomuxRoots):
        return tuple(Path(path).expanduser().resolve(strict=False) for path in paths_or_root.writable_paths())
    root = Path(paths_or_root).expanduser().resolve(strict=False)
    return (root,)


def _instance_lock_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Return one exclusive lock for every mutable path."""

    return tuple(sorted({path / "instance.lock" for path in paths}, key=str))


def instance_root_is_available(paths_or_root: YomuxRoots | Path) -> bool:
    """Reject an owned root before expensive startup without rewriting lease records.

    This is an early check only. The launch still acquires its complete lease
    tuple before starting services, closing the race after this check releases.
    """
    descriptors: list[int] = []
    try:
        for path in _instance_lock_paths(_instance_path_tuple(paths_or_root)):
            try:
                descriptor = _try_lock_instance_path(path, create=False)
            except FileNotFoundError:
                continue
            if descriptor is None:
                return False
            descriptors.append(descriptor)
        return True
    finally:
        _release_fds(descriptors)


def _release_fds(fds: list[int]) -> None:
    for fd in fds:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
