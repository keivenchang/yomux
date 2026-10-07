#!/usr/bin/env python3
"""Resolve one complete per-port YOmux instance environment before package import."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Older entrypoints import these names here; implementation has one owner.
from tools.product_environment import apply_yomux_environment_assignments
from tools.product_environment import clear_yomux_environment_assignments
from tools.product_environment import expose_yomux_legacy_aliases
from tools.product_environment import normalize_yomux_environment
from tools.product_environment import environment_value
from tools.product_environment import product_env
from tools.product_environment import product_env_name
from tools.product_environment import product_env_names
from tools.product_environment import PRODUCT_PREFIXES

ROOT_KEYS = (product_env_name('RUNTIME_DIR'), product_env_name('CONFIG_DIR'), product_env_name('STATE_DIR'), product_env_name('CACHE_DIR'))
PRODUCT_ROOT_ENV = product_env_name('ROOT')
GENERATED_PYTHON_CACHE_PREFIX_ENV = product_env_name('GENERATED_PYTHONPYCACHEPREFIX')

_legacy_alias_warning_emitted = False


PRODUCT_ROOT_SETTINGS = (
    "ROOT",
    "CONFIG_DIR",
    "STATE_DIR",
    "CACHE_DIR",
    "RUNTIME_DIR",
    "CODEX_HOME",
    "HOST_ARTIFACT_DIR",
    "START_LOCK_DIR",
    "LOG_DIR",
    "CA_DIR",
    "TOOL_LOCK_PATH",
)
PRODUCT_ROOT_KEYS = tuple(
    name for setting in PRODUCT_ROOT_SETTINGS for name in product_env_names(setting)
) + (
    "CODEX_HOME",
    "PYTHONPYCACHEPREFIX",
    "TMPDIR",
)
INHERITED_CREDENTIAL_ENV_KEYS = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HF_TOKEN",
    "HUGGINGFACE_HUB_TOKEN",
    "LINEAR_KEY",
    "NGC_API_KEY",
    "OPENAI_API_KEY",
})


def synthetic_test_environment(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child environment with no ambient product/client-data roots."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith((*PRODUCT_PREFIXES, "XDG_", "OPENCODE_", "CODEX_", "CLAUDE_"))
        and key not in INHERITED_CREDENTIAL_ENV_KEYS
        and key not in {"HOME", "TMPDIR", "TMUX", "TMUX_PANE", "TMUX_TMPDIR", "PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX"}
    }
    environment.update(overrides or {})
    return environment
ROOTED_OVERRIDE_KEYS = (
    *ROOT_KEYS,
    product_env_name('CODEX_HOME'),
    product_env_name('HOST_ARTIFACT_DIR'),
    "PYTHONPYCACHEPREFIX",
)
ROOTED_IGNORED_AMBIENT_KEYS = (
    "CODEX_HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
)
AUTO_ROOT_IGNORED_AMBIENT_KEYS = (
    *ROOTED_IGNORED_AMBIENT_KEYS,
    *product_env_names("HOST_ARTIFACT_DIR"),
    "PYTHONPYCACHEPREFIX",
    *product_env_names("GENERATED_PYTHONPYCACHEPREFIX"),
)
ROOTED_SOCKET_RELATIVE_PATHS = (
    Path("control/yolomux-4194304-ffffffffffffffff.sock"),
    Path("services/statusd.sock"),
    Path("services/batchd.sock"),
    Path("services/watchd.sock"),
    Path("services/approvald.sock"),
    Path("services/statsd.p25s9.ffffffffffffffff.sock"),
    Path("services/indexer.sock"),
)
# W1: one authoritative identity carrier. Replaces the three same-valued vars
# (YOMUX_EARLY_INSTANCE_PORT, YOMUX_MANAGED_INSTANCE_PORT, and the managed
# use of the primary-port var) that previously had to be kept in sync by hand.
INSTANCE_ENV = product_env_name('INSTANCE')
# Legacy env-var names retained ONLY so callers/tests can assert their absence in
# a negative search. Nothing in this module emits or reads them any more.
EARLY_PORT_ENV = product_env_name('EARLY_INSTANCE_PORT')
MANAGED_INSTANCE_PORT_ENV = product_env_name('MANAGED_INSTANCE_PORT')


class YomuxRootError(ValueError):
    """A root configuration cannot safely contain every product path."""


def resolved_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def resolved_home_path(values: Mapping[str, str]) -> Path:
    """Resolve HOME without allowing a relative environment value to inherit cwd."""
    configured = values.get("HOME")
    configured_path = Path(configured) if configured else None
    if configured_path is not None and not configured_path.is_absolute():
        raise YomuxRootError(f"HOME must be an absolute path, got {configured!r}")
    if configured_path is not None and configured_path == Path(configured_path.anchor):
        raise YomuxRootError(f"HOME cannot use the filesystem root: {configured_path}")
    expanded = configured_path if configured_path is not None else Path.home().expanduser()
    if not expanded.is_absolute():
        raise YomuxRootError(f"HOME must resolve to an absolute path, got {expanded!s}")
    candidate = resolved_path(expanded)
    if candidate == Path(candidate.anchor):
        raise YomuxRootError(f"HOME cannot use the filesystem root: {candidate}")
    return candidate


def resolved_product_path(
    values: Mapping[str, str],
    key: str,
    default: str | Path,
    *,
    reject_home: bool = True,
) -> Path:
    """Resolve one configured path without letting process cwd choose its location."""
    configured = environment_value(values, key)
    supplied = Path(configured) if configured else Path(default).expanduser()
    if not supplied.is_absolute():
        supplied = configured if configured else str(default)
        raise YomuxRootError(f"{key} must resolve from an absolute path, got {supplied!r}")
    candidate = resolved_path(supplied)
    if configured and candidate == Path(candidate.anchor):
        raise YomuxRootError(f"{key} cannot use the filesystem root as a product root: {candidate}")
    if configured and reject_home and candidate == resolved_home_path(values):
        raise YomuxRootError(f"{key} cannot use the home directory as a product root: {candidate}")
    reject_worktree_product_path(key, candidate)
    return candidate


def reject_worktree_product_path(key: str, candidate: Path) -> None:
    if candidate.is_relative_to(REPO_ROOT):
        raise YomuxRootError(
            f"{key} cannot resolve inside the shared worktree: {candidate}; choose a host-local path"
        )


def rooted_product_path(
    values: Mapping[str, str],
    key: str,
    root: Path,
    default: str | Path,
) -> Path:
    """Resolve one rooted override and require it to remain under its owner."""
    candidate = resolved_product_path(values, key, default)
    if not candidate.is_relative_to(root):
        raise YomuxRootError(
            f"{key} resolves outside {product_env_name('ROOT')}: {candidate}; unset {key} or choose a path inside {root}"
        )
    return candidate


def local_rpc_socket_path_bytes(platform: str = sys.platform) -> int:
    """Return the real portable bind budget for this platform."""
    return 96 if platform.casefold() == "darwin" else 107


def rooted_socket_candidates(runtime_dir: Path, *, stats_digest: str = "f" * 16) -> tuple[Path, ...]:
    """Return every rooted product socket shape without importing product code."""
    return tuple(
        runtime_dir / str(relative).replace("ffffffffffffffff", stats_digest)
        for relative in ROOTED_SOCKET_RELATIVE_PATHS
    )


def validate_rooted_socket_budget(runtime_dir: Path, *, platform: str = sys.platform) -> None:
    """Refuse a rooted layout whose longest real socket cannot bind in place."""
    longest = max(rooted_socket_candidates(runtime_dir), key=lambda path: len(os.fsencode(str(path))))
    length = len(os.fsencode(str(longest)))
    limit = local_rpc_socket_path_bytes(platform)
    if length > limit:
        raise YomuxRootError(
            f"{product_env_name('ROOT')} is too deep for product socket {longest} ({length} bytes; limit {limit}); choose a shorter {product_env_name('ROOT')}"
        )


def resolved_state_dir(values: Mapping[str, str]) -> Path:
    """Resolve the effective state directory without importing product modules."""
    values = normalize_yomux_environment(values)
    home = resolved_home_path(values)
    configured_root = product_env('ROOT', environ=values)
    if not configured_root:
        return resolved_product_path(values, product_env_name('STATE_DIR'), home / ".local" / "state" / "yomux")
    root = resolved_product_path(values, PRODUCT_ROOT_ENV, configured_root)
    return rooted_product_path(values, product_env_name('STATE_DIR'), root, root / "state")


def validate_product_root_environment(
    environ: Mapping[str, str],
    *,
    platform: str = sys.platform,
) -> None:
    """Validate every explicit root before startup writes, locks, or listener mutation."""
    environ = normalize_yomux_environment(environ)
    home = resolved_home_path(environ)
    reject_worktree_product_path("HOME", home)
    configured_root = product_env('ROOT', environ=environ)
    for key in PRODUCT_ROOT_KEYS:
        if key == "CODEX_HOME" and (configured_root or product_env('CODEX_HOME', environ=environ)):
            continue
        if environment_value(environ, key):
            resolved_product_path(environ, key, environ[key])
    if configured_root:
        root = resolved_product_path(environ, PRODUCT_ROOT_ENV, configured_root)
        for key in ROOTED_OVERRIDE_KEYS:
            if environment_value(environ, key):
                rooted_product_path(environ, key, root, environ[key])
        runtime_dir = rooted_product_path(environ, product_env_name('RUNTIME_DIR'), root, root / "runtime")
        validate_rooted_socket_budget(runtime_dir, platform=platform)
    else:
        for key in ("XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            if environment_value(environ, key):
                resolved_product_path(environ, key, environ[key], reject_home=False)


@dataclass(frozen=True)
class InstanceIdentity:
    """The one per-row identity: which port this row is, and whether the
    auto-managed launcher granted it the private-root local-owner capability.
    `managed` is granted only by `resolve_instance_environment`; a caller-set
    YOMUX_ROOT never produces this descriptor, so a bare path cannot grant it."""

    port: int
    managed: bool


def format_instance(identity: InstanceIdentity) -> str:
    return f"{identity.port}:{'managed' if identity.managed else 'shared'}"


def parse_instance(environ: Mapping[str, str] | None = None) -> "InstanceIdentity | None":
    """Read the one authoritative identity, or None when no row descriptor is set."""
    raw = product_env('INSTANCE', "", environ=os.environ if environ is None else environ)
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
    return 8880 if platform.casefold() == "darwin" else 7110


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
    values = normalize_yomux_environment(environ)
    has_explicit_root = bool(product_env('ROOT', environ=values) or any(environment_value(values, key) for key in ROOT_KEYS))
    auto_root = not has_explicit_root and port is not None and port != default_port(platform)
    validation_values = (
        {key: value for key, value in values.items() if key not in AUTO_ROOT_IGNORED_AMBIENT_KEYS}
        if auto_root
        else values
    )
    try:
        validate_product_root_environment(validation_values, platform=platform)
    except YomuxRootError as error:
        return InstanceResolution(port, {}, str(error))
    if has_explicit_root:
        return InstanceResolution(port, {})
    if port is None or port == default_port(platform):
        return InstanceResolution(port, {})
    # Runtime sockets must stay on a local, deliberately short path. F0 turns
    # this managed launch into one root instead of four independent families.
    # Keep row planning independent of the parent process's cached tempfile state.
    # A deliberate TMPDIR mapping is honored by direct resolution; row plans strip
    # inherited TMPDIR and therefore use this canonical short base.
    default_tempdir = Path("/tmp")
    configured_tmp = str(values.get("TMPDIR") or "").strip()
    runtime_base = Path(tempdir) if tempdir is not None else resolved_product_path(
        values,
        "TMPDIR",
        configured_tmp or default_tempdir,
    )
    runtime_base = runtime_base / f"y{os.getuid()}"
    instance = f"p{port}"
    environment = {
        PRODUCT_ROOT_ENV: str(runtime_base / instance),
        # One authoritative identity carrier - no separate same-valued vars. It is
        # an assertion made only by the managed launcher: a caller-set YOMUX_ROOT
        # reaches the early return above and never gets this descriptor, so a bare
        # path cannot grant the local-owner capability.
        INSTANCE_ENV: format_instance(InstanceIdentity(port=port, managed=True)),
    }
    try:
        validate_product_root_environment({**validation_values, **environment}, platform=platform)
    except YomuxRootError as error:
        return InstanceResolution(port, {}, str(error))
    return InstanceResolution(port, environment)


# Every inherited YOmux root/instance variable a parent shell (which may belong
# to another instance) could carry. The launcher strips all of these before it
# resolves a row, so an ambient root can never contaminate a fresh launch.
_INHERITED_INSTANCE_KEYS = (
    *product_env_names("INSTANCE"),
    *product_env_names("EARLY_INSTANCE_PORT"),
    *product_env_names("MANAGED_INSTANCE_PORT"),
    *PRODUCT_ROOT_KEYS,
    *product_env_names("GENERATED_PYTHONPYCACHEPREFIX"),
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    *product_env_names("ROW_PLAN_FILE"),
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
    normalized = normalize_yomux_environment(base_environ)
    stripped = {key: value for key, value in normalized.items() if key not in _INHERITED_INSTANCE_KEYS}
    resolution = resolve_instance_environment(port, stripped, platform=platform or os.uname().sysname, tempdir=tempdir)
    if resolution.error:
        raise RuntimeError(resolution.error)
    return RowPlan(unset=_INHERITED_INSTANCE_KEYS, assign=dict(resolution.environment))


def _apply_instance_resolution(port: int | None, target: dict[str, str], *, platform: str, tempdir: Path | None = None) -> InstanceResolution:
    """Apply the early root/identity resolution to one mutable environment."""
    target.update(normalize_yomux_environment(target))
    resolution = resolve_instance_environment(port, target, platform=platform, tempdir=tempdir)
    if resolution.error:
        raise RuntimeError(resolution.error)
    if product_env('ROOT', environ=resolution.environment):
        for key in AUTO_ROOT_IGNORED_AMBIENT_KEYS:
            target.pop(key, None)
    elif product_env('ROOT', environ=target):
        for key in ROOTED_IGNORED_AMBIENT_KEYS:
            target.pop(key, None)
    apply_yomux_environment_assignments(target, resolution.environment)
    return resolution


def resolve_direct_launch_plan(port: int | None, base_environ: Mapping[str, str], *, platform: str | None = None, tempdir: Path | None = None) -> RowPlan:
    """Capture direct-launch root semantics without trusting a service daemon's
    retained environment. Explicit caller roots survive; stale instance identity
    and local-scheduler authority do not."""
    platform_name = platform or os.uname().sysname
    values = normalize_yomux_environment(base_environ)
    has_explicit_root = bool(product_env('ROOT', environ=values) or any(environment_value(values, key) for key in ROOT_KEYS))
    if port is not None and port != default_port(platform_name) and not has_explicit_root:
        return resolve_row_plan(port, values, platform=platform_name, tempdir=tempdir)
    for key in (
        INSTANCE_ENV,
        EARLY_PORT_ENV,
        MANAGED_INSTANCE_PORT_ENV,
        *product_env_names("INSTANCE"),
        *product_env_names("EARLY_INSTANCE_PORT"),
        *product_env_names("MANAGED_INSTANCE_PORT"),
        *product_env_names("ROW_PLAN_FILE"),
    ):
        values.pop(key, None)
    _apply_instance_resolution(port, values, platform=platform_name, tempdir=tempdir)
    assign = {key: values[key] for key in _INHERITED_INSTANCE_KEYS if environment_value(values, key)}
    return RowPlan(unset=_INHERITED_INSTANCE_KEYS, assign=assign)


def apply_row_plan(plan: RowPlan, base_environ: Mapping[str, str]) -> dict[str, str]:
    """Apply a resolved plan to a COPY of an environment: strip the plan's unset
    keys, overlay its assign keys. The parent `base_environ` is never mutated."""
    normalized = normalize_yomux_environment(base_environ)
    child = {key: value for key, value in normalized.items() if key not in plan.unset}
    apply_yomux_environment_assignments(child, plan.assign)
    return child


def clean_row_environment(port: int | None, base_environ: Mapping[str, str], *, platform: str | None = None, tempdir: Path | None = None) -> dict[str, str]:
    """Convenience: resolve a row plan and apply it in one call. This is the one
    environment the launcher uses for server launch, authentication, ownership
    verification, and transcript discovery, so every probe sees the same root."""
    return apply_row_plan(resolve_row_plan(port, base_environ, platform=platform, tempdir=tempdir), base_environ)


def apply_early_instance_environment(argv: list[str], environ: dict[str, str] | None = None) -> int | None:
    target = os.environ if environ is None else environ
    target.update(normalize_yomux_environment(target, warn=True))
    target[product_env_name('ENVIRONMENT_NORMALIZED')] = "1"
    resolution = _apply_instance_resolution(scan_port(argv), target, platform=os.uname().sysname)
    return resolution.port


def assert_early_port(parsed_port: int, environ: Mapping[str, str] | None = None) -> None:
    """Refuse to run when the early-resolved row port disagrees with the argparse
    port. Reads the one descriptor's port, not a separate early-port variable."""
    identity = parse_instance(environ)
    if identity is not None and identity.port != int(parsed_port):
        raise RuntimeError(f"YOmux early instance port {identity.port} disagrees with parsed --port {parsed_port}; refusing split ownership")


def is_managed_instance_port(port: int, environ: Mapping[str, str] | None = None) -> bool:
    """Whether this exact port received the auto-derived private-root contract.
    Reads the one authoritative descriptor; managed capability is granted there
    only by the managed resolver, never inferred from a bare YOMUX_ROOT path."""
    identity = parse_instance(environ)
    return bool(identity and identity.managed and identity.port == port)


def _exec_row(argv: list[str]) -> int:
    """`exec --plan-file <path> -- <command...>` or `exec --plan-json <json> --
    <command...>`: apply an already-resolved row
    plan to a copy of the current environment and exec the command. The env is
    applied before the target interpreter imports yomux_lib; no eval, no
    inherited environment or secrets in argv, and the parent shell is unchanged."""
    if len(argv) < 2 or argv[0] not in {"--plan-file", "--plan-json"}:
        print("usage: exec (--plan-file <path> | --plan-json <json>) -- <command...>", file=sys.stderr)
        return 2
    rest = argv[2:]
    if not rest or rest[0] != "--" or len(rest) < 2:
        print("exec requires: -- <command...>", file=sys.stderr)
        return 2
    command = rest[1:]
    try:
        plan_text = Path(argv[1]).read_text(encoding="utf-8") if argv[0] == "--plan-file" else argv[1]
        plan = RowPlan.from_json(plan_text)
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


def _plan_direct(argv: list[str]) -> int:
    """`plan-direct --port P`: capture the validated direct-launch environment
    as a bounded plan that can cross a retained service-daemon boundary."""
    try:
        plan = resolve_direct_launch_plan(scan_port(argv), os.environ)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(plan.to_json())
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "validate-roots":
        try:
            validate_product_root_environment(os.environ)
        except YomuxRootError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        return 0
    if argv and argv[0] == "resolve-product-path":
        if len(argv) != 3:
            print("usage: resolve-product-path <environment-key> <path>", file=sys.stderr)
            return 2
        key, value = argv[1:]
        values = dict(os.environ)
        values[key] = value
        try:
            print(resolved_product_path(values, key, value))
        except YomuxRootError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        return 0
    if argv and argv[0] == "resolve-state-dir":
        if len(argv) != 1:
            print("usage: resolve-state-dir", file=sys.stderr)
            return 2
        try:
            validate_product_root_environment(os.environ, platform=os.uname().sysname)
            print(resolved_state_dir(os.environ))
        except YomuxRootError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        return 0
    if argv and argv[0] == "exec":
        return _exec_row(argv[1:])
    if argv and argv[0] == "plan":
        return _plan_row(argv[1:])
    if argv and argv[0] == "plan-direct":
        return _plan_direct(argv[1:])
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
