# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One dependency-light resolver for every configured YOLOmux root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


YOLOMUX_ROOT_ENV = "YOLOMUX_ROOT"


class YolomuxRootError(ValueError):
    """A root configuration cannot safely contain every product path."""


@dataclass(frozen=True)
class YolomuxRoots:
    """Resolved mutable roots; a configured parent owns every member."""

    config_dir: Path
    state_dir: Path
    cache_dir: Path
    codex_home: Path
    runtime_dir: Path
    root: Path | None = None

    def writable_paths(self) -> tuple[Path, ...]:
        return (self.config_dir, self.state_dir, self.cache_dir, self.codex_home, self.runtime_dir)


def resolved_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def rooted_override(values: Mapping[str, str], key: str, root: Path, default: Path) -> Path:
    configured = values.get(key)
    candidate = resolved_path(configured) if configured else default
    if not candidate.is_relative_to(root):
        raise YolomuxRootError(f"{key} resolves outside YOLOMUX_ROOT: {candidate}; unset {key} or choose a path inside {root}")
    return candidate


def config_dir_from_environ(values: Mapping[str, str]) -> Path:
    """Resolve auth's early configuration import through the shared parent."""
    configured_root = values.get(YOLOMUX_ROOT_ENV)
    if not configured_root:
        return Path(values.get("YOLOMUX_CONFIG_DIR", str(Path.home() / ".config" / "yolomux"))).expanduser()
    root = resolved_path(configured_root)
    return rooted_override(values, "YOLOMUX_CONFIG_DIR", root, root / "config")


def resolve_yolomux_roots(values: Mapping[str, str], *, default_runtime_dir: Path) -> YolomuxRoots:
    """Resolve all product roots without creating any directory."""
    configured_root = values.get(YOLOMUX_ROOT_ENV)
    if not configured_root:
        default_cache_home = Path(values.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        return YolomuxRoots(
            config_dir_from_environ(values),
            Path(values.get("YOLOMUX_STATE_DIR", str(Path.home() / ".local" / "state" / "yolomux"))).expanduser(),
            Path(values.get("YOLOMUX_CACHE_DIR", str(default_cache_home / "yolomux"))).expanduser(),
            Path(values.get("YOLOMUX_CODEX_HOME") or values.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser(),
            default_runtime_dir,
        )
    root = resolved_path(configured_root)
    return YolomuxRoots(
        config_dir_from_environ(values),
        rooted_override(values, "YOLOMUX_STATE_DIR", root, root / "state"),
        rooted_override(values, "YOLOMUX_CACHE_DIR", root, root / "cache"),
        rooted_override(values, "YOLOMUX_CODEX_HOME", root, root / "codex"),
        rooted_override(values, "YOLOMUX_RUNTIME_DIR", root, root / "runtime"),
        root,
    )
