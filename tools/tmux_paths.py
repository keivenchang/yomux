# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Private short paths for Unix-socket-backed test fixtures."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import re
import stat
import uuid

from tools.instance_isolation import private_directory
from yomux_lib.infra.transient_paths import remove_exact_tree

SHORT_TMPDIR_ENV = "YOMUX_NATIVE_SHORT_TMPDIR"
NATIVE_ACTION_TMPDIR = "/n"


def short_temp_base() -> Path:
    """Use the admitted action root, retaining diagnostic /tmp defaults."""
    value = os.environ.get(SHORT_TMPDIR_ENV)
    if value is None:
        return Path("/tmp")
    root = Path(value)
    if not root.is_absolute():
        raise ValueError("native short fixture root must be absolute")
    private_directory(root)
    return root


def create_native_fixture_root(base: Path, coalition: int) -> Path:
    """Journal ownership before allocation so SIGKILL cannot orphan fixtures."""
    private_directory(base)
    registry = base / ".owners"
    private_directory(registry)
    while True:
        name = "a" + uuid.uuid4().hex[:4]
        if (base / name).exists() or (base / name).is_symlink():
            continue
        record = registry / ("c" + str(coalition) + "-" + name)
        try:
            descriptor = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        # One global reservation per basename prevents a different coalition's
        # colliding journal from ever claiming an existing fixture directory.
        try:
            os.link(record, registry / name, follow_symlinks=False)
        except FileExistsError:
            record.unlink()
            continue
        root = base / name
        root.mkdir(mode=0o700)
        break
    private_directory(root)
    return root


def cleanup_native_fixture_roots(base: Path, coalition: int) -> None:
    """Remove exact journaled roots only after their kernel owner has retired."""
    if not base.exists():
        return
    private_directory(base)
    registry = base / ".owners"
    if not registry.exists():
        return
    private_directory(registry)
    prefix = "c" + str(coalition) + "-"
    for record in registry.iterdir():
        if not record.name.startswith(prefix):
            continue
        name = record.name[len(prefix):]
        metadata = record.lstat()
        if (re.fullmatch(r"a[0-9a-f]{4}", name) is None
                or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise ValueError("invalid native fixture ownership record: " + str(record))
        reservation = registry / name
        try:
            reserved = reservation.lstat()
        except FileNotFoundError:
            reserved = None
        # A killed allocator may have journaled before reserving the basename.
        # Only the matching inode proves exclusive ownership of that root.
        if reserved is None or (reserved.st_dev, reserved.st_ino) != (metadata.st_dev, metadata.st_ino):
            record.unlink()
            continue
        root = base / name
        if root.exists() or root.is_symlink():
            private_directory(root)
            remove_exact_tree(root, make_owner_writable=True)
        reservation.unlink()
        record.unlink()


def short_temp_prefix(prefix: str) -> str:
    """Reserve socket pathname space for the fixture's runtime descendants."""
    return "f" if SHORT_TMPDIR_ENV in os.environ else prefix


def short_fixture_path(name: str) -> Path:
    """Keep real product-root fixtures below the portable socket budget."""
    if SHORT_TMPDIR_ENV not in os.environ:
        return (short_temp_base() / name).resolve()
    base = short_temp_base()
    reservations = base / ".paths"
    private_directory(reservations)
    while True:
        name = "f" + uuid.uuid4().hex[:6]
        if (base / name).exists() or (base / name).is_symlink():
            continue
        try:
            descriptor = os.open(reservations / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        return base / name


def short_temp_directory(prefix: str) -> str:
    """Allocate a socket-safe fixture, preserving diagnostic directory prefixes."""
    if SHORT_TMPDIR_ENV not in os.environ:
        return tempfile.mkdtemp(prefix=prefix, dir=short_temp_base())
    while True:
        root = short_fixture_path(prefix)
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return str(root)


def short_tmux_socket_path(prefix: str) -> Path:
    """Create a private socket directory below /tmp and return its short socket path."""

    directory = short_temp_directory(prefix=f"{prefix}-{os.getpid()}-")
    return Path(directory) / "s"
