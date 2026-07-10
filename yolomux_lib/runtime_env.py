# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Dependency-light environment normalization shared by server and agent clients."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def _path_entries(value: object) -> list[str]:
    return [entry for entry in str(value or "").split(os.pathsep) if entry]


def _expand_path_entry(value: str, home: Path) -> str:
    if value == "~":
        return str(home)
    if value.startswith(f"~{os.sep}"):
        return str(home / value[2:])
    return str(Path(value).expanduser())


def healed_runtime_path(env: Mapping[str, object], *, home: Path | None = None) -> str:
    """Return PATH with configured agent locations prepended once, in stable order."""
    path_entries = _path_entries(env.get("PATH", ""))
    active_home = home or Path.home()
    candidates = [_expand_path_entry(entry, active_home) for entry in _path_entries(env.get("YOLOMUX_EXTRA_PATH", ""))]
    local_bin = active_home / ".local" / "bin"
    if local_bin.is_dir():
        candidates.append(str(local_bin))
    additions: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in path_entries and candidate not in additions:
            additions.append(candidate)
    return os.pathsep.join([*additions, *path_entries])
