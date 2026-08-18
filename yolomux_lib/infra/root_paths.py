# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One dependency-light resolver for every configured YOLOmux root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tools.instance_isolation import resolved_path
from tools.instance_isolation import resolved_home_path
from tools.instance_isolation import resolved_product_path
from tools.instance_isolation import resolved_state_dir
from tools.instance_isolation import rooted_product_path
from tools.instance_isolation import YolomuxRootError

YOLOMUX_ROOT_ENV = "YOLOMUX_ROOT"


@dataclass(frozen=True)
class YolomuxRoots:
    """Resolved runtime roots; credentials remain user-owned unless explicitly overridden."""

    config_dir: Path
    state_dir: Path
    cache_dir: Path
    codex_home: Path
    runtime_dir: Path
    root: Path | None = None

    def writable_paths(self) -> tuple[Path, ...]:
        """Return paths YOLOmux itself mutates for an isolated instance."""
        return (self.config_dir, self.state_dir, self.cache_dir, self.runtime_dir)


def rooted_override(values: Mapping[str, str], key: str, root: Path, default: Path) -> Path:
    return rooted_product_path(values, key, root, default)


def config_dir_from_environ(values: Mapping[str, str]) -> Path:
    """Resolve auth's early configuration import through the shared parent."""
    configured_root = values.get(YOLOMUX_ROOT_ENV)
    if not configured_root:
        return resolved_product_path(values, "YOLOMUX_CONFIG_DIR", resolved_home_path(values) / ".config" / "yolomux")
    root = resolved_product_path(values, YOLOMUX_ROOT_ENV, configured_root)
    return rooted_override(values, "YOLOMUX_CONFIG_DIR", root, root / "config")


def resolve_yolomux_roots(values: Mapping[str, str], *, default_runtime_dir: Path) -> YolomuxRoots:
    """Resolve all product roots without creating any directory."""
    home = resolved_home_path(values)
    configured_root = values.get(YOLOMUX_ROOT_ENV)
    if not configured_root:
        default_cache_home = resolved_product_path(
            values,
            "XDG_CACHE_HOME",
            home / ".cache",
            reject_home=False,
        )
        codex_home_key = "YOLOMUX_CODEX_HOME" if values.get("YOLOMUX_CODEX_HOME") else "CODEX_HOME"
        return YolomuxRoots(
            config_dir_from_environ(values),
            resolved_state_dir(values),
            resolved_product_path(values, "YOLOMUX_CACHE_DIR", default_cache_home / "yolomux"),
            resolved_product_path(values, codex_home_key, home / ".codex"),
            resolved_path(default_runtime_dir),
        )
    root = resolved_product_path(values, YOLOMUX_ROOT_ENV, configured_root)
    config_dir = config_dir_from_environ(values)
    configured_codex_home = values.get("YOLOMUX_CODEX_HOME")
    codex_home = (
        rooted_override(values, "YOLOMUX_CODEX_HOME", root, root / "codex")
        if configured_codex_home
        else home / ".codex"
    )
    return YolomuxRoots(
        config_dir,
        resolved_state_dir(values),
        rooted_override(values, "YOLOMUX_CACHE_DIR", root, root / "cache"),
        codex_home,
        rooted_override(values, "YOLOMUX_RUNTIME_DIR", root, root / "runtime"),
        root,
    )
