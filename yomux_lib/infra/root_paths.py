# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One dependency-light resolver for every configured YOmux root."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .host_identity import HostIdentity
from .host_identity import current_host_identity

from tools.instance_isolation import resolved_path
from tools.instance_isolation import resolved_home_path
from tools.instance_isolation import resolved_product_path
from tools.instance_isolation import resolved_state_dir
from tools.instance_isolation import normalize_yomux_environment
from tools.instance_isolation import rooted_product_path
from tools.instance_isolation import YomuxRootError

YOLOMUX_ROOT_ENV = "YOLOMUX_ROOT"


@dataclass(frozen=True)
class YomuxRoots:
    """Resolved runtime roots; credentials remain user-owned unless explicitly overridden."""

    config_dir: Path
    state_dir: Path
    cache_dir: Path
    codex_home: Path
    runtime_dir: Path
    root: Path | None = None

    def writable_paths(self) -> tuple[Path, ...]:
        """Return paths YOmux itself mutates for an isolated instance."""
        return (self.config_dir, self.state_dir, self.cache_dir, self.runtime_dir)


def rooted_override(values: Mapping[str, str], key: str, root: Path, default: Path) -> Path:
    return rooted_product_path(values, key, root, default)


def config_dir_from_environ(values: Mapping[str, str]) -> Path:
    """Resolve auth's early configuration import through the shared parent."""
    values = normalize_yomux_environment(values)
    configured_root = values.get(YOLOMUX_ROOT_ENV)
    if not configured_root:
        return resolved_product_path(values, "YOLOMUX_CONFIG_DIR", resolved_home_path(values) / ".config" / "yomux")
    root = resolved_product_path(values, YOLOMUX_ROOT_ENV, configured_root)
    return rooted_override(values, "YOLOMUX_CONFIG_DIR", root, root / "config")


def resolve_yomux_roots(values: Mapping[str, str], *, default_runtime_dir: Path) -> YomuxRoots:
    """Resolve all product roots without creating any directory."""
    values = normalize_yomux_environment(values)
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
        return YomuxRoots(
            config_dir_from_environ(values),
            resolved_state_dir(values),
            resolved_product_path(values, "YOLOMUX_CACHE_DIR", default_cache_home / "yomux"),
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
    return YomuxRoots(
        config_dir,
        resolved_state_dir(values),
        rooted_override(values, "YOLOMUX_CACHE_DIR", root, root / "cache"),
        codex_home,
        rooted_override(values, "YOLOMUX_RUNTIME_DIR", root, root / "runtime"),
        root,
    )


def runtime_root(
    *,
    environ: Mapping[str, str] | None = None,
    identity: HostIdentity | None = None,
    temporary_dir: Path | None = None,
    uid: int | None = None,
) -> Path:
    """Return the host- and boot-private root for sockets, leases, and locks.

    XDG runtime storage is normally a local tmpfs cleared on reboot. Headless
    shells and containers often lack it, so the fallback remains under the
    machine's temporary directory and is still checked before use.
    """
    values = os.environ if environ is None else environ
    if values.get(YOLOMUX_ROOT_ENV):
        # A rooted run already has a unique, operator-selected parent. Keeping
        # host/boot suffixes here would make the advertised <root>/runtime
        # layout false and wastes the Unix-socket pathname budget.
        return resolved_product_path(values, YOLOMUX_ROOT_ENV, values[YOLOMUX_ROOT_ENV]) / "runtime"
    resolved_identity = identity or current_host_identity()
    if values.get("YOLOMUX_RUNTIME_DIR"):
        runtime_base = resolved_product_path(values, "YOLOMUX_RUNTIME_DIR", values["YOLOMUX_RUNTIME_DIR"]) / "yomux"
    elif values.get("XDG_RUNTIME_DIR"):
        runtime_base = resolved_product_path(
            values,
            "XDG_RUNTIME_DIR",
            values["XDG_RUNTIME_DIR"],
            reject_home=False,
        ) / "yomux"
    else:
        resolved_uid = os.getuid() if uid is None else int(uid)
        runtime_base = resolved_path(temporary_dir or tempfile.gettempdir()) / f"yomux-server-{resolved_uid}" / "shared"
    # Keep the socket-bearing root readable while leaving enough sockaddr_un
    # budget for the longest service filename. These stable prefixes identify
    # the host and boot in diagnostics without an opaque hash.
    host_scope = f"h-{resolved_identity.stable_host_id[:12]}"
    boot_value = resolved_identity.boot_id or f"instance-{resolved_identity.instance_nonce}"
    boot_scope = f"b-{boot_value[:12]}"
    return runtime_base / host_scope / boot_scope
