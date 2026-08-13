#!/usr/bin/env python3
"""Resolve one complete per-port YOLOmux instance environment before package import."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


ROOT_KEYS = ("YOLOMUX_RUNTIME_DIR", "YOLOMUX_CONFIG_DIR", "YOLOMUX_STATE_DIR", "YOLOMUX_CACHE_DIR")
YOLOMUX_ROOT_ENV = "YOLOMUX_ROOT"
# W1: one authoritative identity carrier. Replaces the three same-valued vars
# (YOLOMUX_EARLY_INSTANCE_PORT, YOLOMUX_MANAGED_INSTANCE_PORT, and the managed
# use of the primary-port var) that previously had to be kept in sync by hand.
INSTANCE_ENV = "YOLOMUX_INSTANCE"
# Legacy env-var names retained ONLY so callers/tests can assert their absence in
# a negative search. Nothing in this module emits or reads them any more.
EARLY_PORT_ENV = "YOLOMUX_EARLY_INSTANCE_PORT"
MANAGED_INSTANCE_PORT_ENV = "YOLOMUX_MANAGED_INSTANCE_PORT"


@dataclass(frozen=True)
class InstanceIdentity:
    """The one per-row identity: which port this row is, and whether the
    auto-managed launcher granted it the private-root local-owner capability.
    `managed` is granted only by `resolve_instance_environment`; a caller-set
    YOLOMUX_ROOT never produces this descriptor, so a bare path cannot grant it."""

    port: int
    managed: bool


def format_instance(identity: InstanceIdentity) -> str:
    return f"{identity.port}:{'managed' if identity.managed else 'shared'}"


def parse_instance(environ: Mapping[str, str] | None = None) -> "InstanceIdentity | None":
    """Read the one authoritative identity, or None when no row descriptor is set."""
    raw = (os.environ if environ is None else environ).get(INSTANCE_ENV, "")
    port_text, separator, mode = raw.partition(":")
    if not separator or mode not in ("managed", "shared"):
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    return InstanceIdentity(port=port, managed=(mode == "managed"))


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
        # One authoritative identity carrier - no separate same-valued vars. It is
        # an assertion made only by the managed launcher: a caller-set YOLOMUX_ROOT
        # reaches the early return above and never gets this descriptor, so a bare
        # path cannot grant the local-owner capability. A managed row does NOT
        # carry YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT: it uses DisabledBackgroundOwner
        # (which never reads that election var); shared/default servers still get
        # their primary from startup_common.sh.
        INSTANCE_ENV: format_instance(InstanceIdentity(port=port, managed=True)),
    })


# Every inherited YOLOmux root/instance variable a parent shell (which may belong
# to another instance) could carry. The launcher strips all of these before it
# resolves a row, so an ambient root can never contaminate a fresh launch.
_INHERITED_INSTANCE_KEYS = (
    YOLOMUX_ROOT_ENV,
    INSTANCE_ENV,
    EARLY_PORT_ENV,
    MANAGED_INSTANCE_PORT_ENV,
    "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT",
    *ROOT_KEYS,
)


@dataclass(frozen=True)
class RowPlan:
    """A declarative, secret-free per-row environment plan: the inherited
    instance/root keys to strip (`unset`), and the resolved roots/ports/identity
    to overlay (`assign`). It carries NO inherited environment values or
    credentials, so it is safe to serialize as bounded JSON and reuse for the
    server launch and every authenticated probe of the same row."""

    unset: tuple[str, ...]
    assign: dict[str, str]

    def to_json(self) -> str:
        return json.dumps({"unset": list(self.unset), "assign": dict(self.assign)}, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "RowPlan":
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != {"unset", "assign"}:
            raise ValueError("row plan must be an object with exactly unset and assign")
        unset = data["unset"]
        assign = data["assign"]
        if not isinstance(unset, list) or not all(isinstance(key, str) for key in unset):
            raise ValueError("row plan unset must be a list of strings")
        if not isinstance(assign, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in assign.items()):
            raise ValueError("row plan assign must be a map of strings")
        stray = set(assign) - set(_INHERITED_INSTANCE_KEYS)
        if stray:
            # never let a plan carry arbitrary/inherited env or credentials.
            raise ValueError(f"row plan assign contains non-instance keys: {sorted(stray)}")
        return cls(unset=tuple(unset), assign=dict(assign))


def resolve_row_plan(port: int | None, base_environ: Mapping[str, str], *, platform: str | None = None, tempdir: Path | None = None) -> RowPlan:
    """Resolve ONE row plan: strip every inherited instance/root var, then resolve
    this row against that stripped view (so an ambient root can never suppress the
    managed resolution). Call this once per configured row and reuse the plan."""
    stripped = {key: value for key, value in base_environ.items() if key not in _INHERITED_INSTANCE_KEYS}
    resolution = resolve_instance_environment(port, stripped, platform=platform or os.uname().sysname, tempdir=tempdir)
    if resolution.error:
        raise RuntimeError(resolution.error)
    return RowPlan(unset=_INHERITED_INSTANCE_KEYS, assign=dict(resolution.environment))


def apply_row_plan(plan: RowPlan, base_environ: Mapping[str, str]) -> dict[str, str]:
    """Apply a resolved plan to a COPY of an environment: strip the plan's unset
    keys, overlay its assign keys. The parent `base_environ` is never mutated."""
    child = {key: value for key, value in base_environ.items() if key not in plan.unset}
    child.update(plan.assign)
    return child


def clean_row_environment(port: int | None, base_environ: Mapping[str, str], *, platform: str | None = None, tempdir: Path | None = None) -> dict[str, str]:
    """Convenience: resolve a row plan and apply it in one call. This is the one
    environment the launcher uses for server launch, authentication, ownership
    verification, and transcript discovery, so every probe sees the same root."""
    return apply_row_plan(resolve_row_plan(port, base_environ, platform=platform, tempdir=tempdir), base_environ)


def apply_early_instance_environment(argv: list[str], environ: dict[str, str] | None = None) -> int | None:
    target = os.environ if environ is None else environ
    resolution = resolve_instance_environment(scan_port(argv), target, platform=os.uname().sysname)
    if resolution.error:
        raise RuntimeError(resolution.error)
    target.update(resolution.environment)
    return resolution.port


def assert_early_port(parsed_port: int, environ: Mapping[str, str] | None = None) -> None:
    """Refuse to run when the early-resolved row port disagrees with the argparse
    port. Reads the one descriptor's port, not a separate early-port variable."""
    identity = parse_instance(environ)
    if identity is not None and identity.port != int(parsed_port):
        raise RuntimeError(f"YOLOmux early instance port {identity.port} disagrees with parsed --port {parsed_port}; refusing split ownership")


def is_managed_instance_port(port: int, environ: Mapping[str, str] | None = None) -> bool:
    """Whether this exact port received the auto-derived private-root contract.
    Reads the one authoritative descriptor; managed capability is granted there
    only by the managed resolver, never inferred from a bare YOLOMUX_ROOT path."""
    identity = parse_instance(environ)
    return bool(identity and identity.managed and identity.port == port)


def _exec_row(argv: list[str]) -> int:
    """`exec --plan-file <path> -- <command...>`: apply an already-resolved row
    plan to a copy of the current environment and exec the command. The env is
    applied before the target interpreter imports yolomux_lib; no eval, no
    inherited environment or secrets in argv, and the parent shell is unchanged."""
    if len(argv) < 2 or argv[0] != "--plan-file":
        print("usage: exec --plan-file <path> -- <command...>", file=sys.stderr)
        return 2
    rest = argv[2:]
    if not rest or rest[0] != "--" or len(rest) < 2:
        print("exec requires: -- <command...>", file=sys.stderr)
        return 2
    command = rest[1:]
    try:
        plan = RowPlan.from_json(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"ERROR: invalid row plan: {error}", file=sys.stderr)
        return 2
    os.execvpe(command[0], command, apply_row_plan(plan, os.environ))


def _plan_row(argv: list[str]) -> int:
    """`plan --port P`: print the one bounded, secret-free RowPlan JSON for a row,
    resolved against the current (launcher) environment. The launcher captures this
    ONCE per row and reuses the exact same plan for the server launch and every
    authenticated probe of that row, so the server and both probes see one root."""
    plan = resolve_row_plan(scan_port(argv), os.environ)
    print(plan.to_json())
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "exec":
        return _exec_row(argv[1:])
    if argv and argv[0] == "plan":
        return _plan_row(argv[1:])
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
