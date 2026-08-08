#!/usr/bin/env python3
"""Resolve one complete per-port YOLOmux instance environment before package import."""

from __future__ import annotations

import os
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


ROOT_KEYS = ("YOLOMUX_RUNTIME_DIR", "YOLOMUX_CONFIG_DIR", "YOLOMUX_STATE_DIR", "YOLOMUX_CACHE_DIR")
YOLOMUX_ROOT_ENV = "YOLOMUX_ROOT"
EARLY_PORT_ENV = "YOLOMUX_EARLY_INSTANCE_PORT"
MANAGED_INSTANCE_PORT_ENV = "YOLOMUX_MANAGED_INSTANCE_PORT"


@dataclass(frozen=True)
class InstanceResolution:
    port: int | None
    environment: dict[str, str]
    error: str = ""


def default_port(platform: str) -> int:
    return 8880 if platform == "Darwin" else 7770


def scan_port(argv: list[str]) -> int | None:
    """Read only a valid --port value, leaving normal CLI errors to argparse."""
    for index, argument in enumerate(argv):
        if argument == "--":
            return None
        value = ""
        if argument == "--port" and index + 1 < len(argv):
            value = argv[index + 1]
        elif argument.startswith("--port="):
            value = argument.partition("=")[2]
        else:
            continue
        try:
            port = int(value)
        except ValueError:
            return None
        return port if 1 <= port <= 65535 else None
    return None


def resolve_instance_environment(port: int | None, environ: Mapping[str, str], *, platform: str = sys.platform, home: Path | None = None, tempdir: Path | None = None) -> InstanceResolution:
    """Return the one root for a managed non-default instance."""
    values = dict(environ)
    if values.get(YOLOMUX_ROOT_ENV) or any(values.get(key) for key in ROOT_KEYS):
        return InstanceResolution(port, {})
    if port is None or port == default_port(platform):
        return InstanceResolution(port, {})
    # Runtime sockets must stay on a local, deliberately short path. F0 turns
    # this managed launch into one root instead of four independent families.
    runtime_base = Path(tempdir or tempfile.gettempdir()) / f"y{os.getuid()}"
    instance = f"p{port}"
    return InstanceResolution(port, {
        YOLOMUX_ROOT_ENV: str(runtime_base / instance),
        "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT": str(port),
        EARLY_PORT_ENV: str(port),
        # This is an assertion made only by the managed launcher.  A caller-set
        # YOLOMUX_ROOT is a path, not proof that another process will not share
        # it, so it deliberately does not receive this local-owner capability.
        # TODO: A separately approved strict-instance contract could extend this
        # to caller-set roots; never infer that guarantee from the path alone.
        MANAGED_INSTANCE_PORT_ENV: str(port),
    })


def apply_early_instance_environment(argv: list[str], environ: dict[str, str] | None = None) -> int | None:
    target = os.environ if environ is None else environ
    resolution = resolve_instance_environment(scan_port(argv), target, platform=os.uname().sysname)
    if resolution.error:
        raise RuntimeError(resolution.error)
    target.update(resolution.environment)
    return resolution.port


def assert_early_port(parsed_port: int) -> None:
    early = os.environ.get(EARLY_PORT_ENV)
    if early and int(early) != int(parsed_port):
        raise RuntimeError(f"YOLOmux early instance port {early} disagrees with parsed --port {parsed_port}; refusing split ownership")


def is_managed_instance_port(port: int, environ: Mapping[str, str] | None = None) -> bool:
    """Whether this exact port received the auto-derived private-root contract."""
    values = os.environ if environ is None else environ
    return (
        values.get(EARLY_PORT_ENV) == str(port)
        and values.get(MANAGED_INSTANCE_PORT_ENV) == str(port)
        and bool(values.get(YOLOMUX_ROOT_ENV))
    )


def main(argv: list[str]) -> int:
    port = scan_port(argv)
    resolution = resolve_instance_environment(port, os.environ, platform=os.uname().sysname)
    if resolution.error:
        print(f"ERROR: {resolution.error}", file=sys.stderr)
        return 2
    for key, value in resolution.environment.items():
        print(f"export {key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
