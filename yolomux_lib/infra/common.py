# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Interactive browser terminals for local tmux sessions.

This starts a local HTTP/WebSocket server and attaches one PTY-backed tmux
client per browser panel. The server is intentionally dependency-free on the
Python side so it can run from a normal host checkout.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import math
import os
import re
import signal
import shutil
import socket
import subprocess
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Mapping
from typing import TypedDict
from zoneinfo import ZoneInfo

from .cache import MISS as cache_MISS
from .host_identity import current_host_identity
from .host_identity import HostIdentity
from .host_partition import HOST_PARTITION_DIRNAME
from .host_partition import host_partitioned_state_dir
from ..local_services.rpc import LOCAL_RPC_SOCKET_PATH_BYTES
from .root_paths import YOLOMUX_ROOT_ENV
from .root_paths import YolomuxRootError
from .root_paths import YolomuxRoots
from .root_paths import resolve_yolomux_roots as _resolve_root_paths
from .root_paths import resolved_path
from .root_paths import resolved_product_path
from tools.instance_isolation import rooted_socket_candidates
from .filesystem_preflight import FilesystemClassification
from .filesystem_preflight import preflight_mutable_roots
from ..workspace.locales import user_message_payload
from .runtime_env import healed_runtime_path
from ..tmux.tmux_utils import list_tmux_session_names
from ..tmux.tmux_utils import run_cmd
from ..tmux.tmux_utils import unique_session_names
from ..tmux.process_group_ownership import signal_recorded_process_group


DEFAULT_SESSIONS: tuple[str, ...] = ()
DEFAULT_COLS = 120
DEFAULT_ROWS = 36
MAX_TRANSCRIPT_TAIL_LINES = 5000
MAX_COMPACT_TRANSCRIPT_ITEMS = 200
MAX_YOLOMUX_SESSION_TABS = 99
ACTIVITY_MAX_HOURS = 24.0 * 365.0
YOLOMUX_VERSION = "0.7.30"
# Persistent state is versioned independently from the release string.  A
# rebuilt checkout must be able to run beside v0.6.10 without reopening its
# append-only event log or its current-schema database.
PERSISTENT_STATE_GENERATION = 7
UPDATE_NOTIFY_LEVELS: tuple[str, ...] = ("major", "minor", "patch", "none")
SUMMARY_LOOKBACK_SECONDS = 3600
SUMMARY_MAX_PROMPT_CHARS = 100_000
SUMMARY_CODEX_TIMEOUT_SECONDS = 600
SUMMARY_CODEX_MODEL = os.environ.get("YOLOMUX_SUMMARY_MODEL", "gpt-5.5")
SUMMARY_CODEX_EFFORT = os.environ.get("YOLOMUX_SUMMARY_EFFORT", "low")
SUMMARY_CODEX_SERVICE_TIER = os.environ.get("YOLOMUX_SUMMARY_SERVICE_TIER", "fast")
YOAGENT_CLAUDE_SUMMARY_MODEL = os.environ.get("YOLOMUX_YOAGENT_CLAUDE_SUMMARY_MODEL", "claude-haiku-4-5")
def resolve_yolomux_roots(
    environ: Mapping[str, str] | None = None,
    *,
    identity: HostIdentity | None = None,
    temporary_dir: Path | None = None,
    uid: int | None = None,
) -> YolomuxRoots:
    """Resolve all writable product roots from one parent without creating paths.

    A rooted run isolates YOLOmux-owned XDG state. Codex credentials and
    configuration remain user-owned by default; only YOLOMUX_CODEX_HOME opts
    into a Codex home inside the root.
    """
    values = os.environ if environ is None else environ
    paths = _resolve_root_paths(
        values,
        default_runtime_dir=runtime_root(environ=values, identity=identity, temporary_dir=temporary_dir, uid=uid),
    )
    # CODEX_HOME is ambient process configuration, not a YOLOmux override.
    # Ignore it under a root so a developer's normal Codex install cannot
    # redirect an isolated server.
    if paths.root is not None:
        validate_rooted_socket_paths(paths, identity=identity)
    return paths


# The ONE owner of the stats schema version and its derived filenames.
#
# It lives here rather than in `stats_current.storage` because `storage` imports this module, so
# the dependency can only run one way. It was previously a hardcoded "stats-v7.sqlite3" literal in
# `runtime_socket_candidates`, which is a divergent copy of the same fact: bumping the schema left
# the socket digest computed from a filename the product no longer uses, so the socket-length
# preflight validated a path that would never exist.
STATS_SCHEMA_VERSION = 8
STATS_DATABASE_FILENAME = f"stats-v{STATS_SCHEMA_VERSION}.sqlite3"


def runtime_socket_candidates(paths: YolomuxRoots, *, identity: HostIdentity | None = None) -> tuple[Path, ...]:
    """Enumerate every product-owned Unix socket before rooted directories exist."""
    resolved_identity = identity or current_host_identity()
    database = paths.state_dir / HOST_PARTITION_DIRNAME / resolved_identity.stable_host_id / STATS_DATABASE_FILENAME
    digest = hashlib.sha256(str(database).encode("utf-8")).hexdigest()[:16]
    return rooted_socket_candidates(paths.runtime_dir, stats_digest=digest)


def validate_rooted_socket_paths(paths: YolomuxRoots, *, identity: HostIdentity | None = None) -> None:
    """Refuse a root whose real socket names would trigger a /tmp fallback."""
    longest = max(runtime_socket_candidates(paths, identity=identity), key=lambda path: len(os.fsencode(str(path))))
    length = len(os.fsencode(str(longest)))
    if length > LOCAL_RPC_SOCKET_PATH_BYTES:
        raise YolomuxRootError(
            f"YOLOMUX_ROOT is too deep for product socket {longest} ({length} bytes; limit {LOCAL_RPC_SOCKET_PATH_BYTES}); choose a shorter YOLOMUX_ROOT"
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
        runtime_base = resolved_product_path(values, "YOLOMUX_RUNTIME_DIR", values["YOLOMUX_RUNTIME_DIR"]) / "yolomux"
    elif values.get("XDG_RUNTIME_DIR"):
        runtime_base = resolved_product_path(
            values,
            "XDG_RUNTIME_DIR",
            values["XDG_RUNTIME_DIR"],
            reject_home=False,
        ) / "yolomux"
    else:
        resolved_uid = os.getuid() if uid is None else int(uid)
        runtime_base = resolved_path(temporary_dir or tempfile.gettempdir()) / f"yolomux-server-{resolved_uid}" / "shared"
    # Keep the socket-bearing root readable while leaving enough sockaddr_un
    # budget for the longest service filename. These stable prefixes identify
    # the host and boot in diagnostics without an opaque hash.
    host_scope = f"h-{resolved_identity.stable_host_id[:12]}"
    boot_value = resolved_identity.boot_id or f"instance-{resolved_identity.instance_nonce}"
    boot_scope = f"b-{boot_value[:12]}"
    return runtime_base / host_scope / boot_scope


def ensure_runtime_root(
    root: Path,
    *,
    classifier: Callable[[Path], FilesystemClassification] | None = None,
) -> Path:
    """Validate and create a private runtime root without accepting network mounts."""
    path = Path(root).expanduser()
    preflight_mutable_roots(
        unix_sockets=(path / ".yolomux-runtime-probe.sock",),
        classifier=classifier,
    )
    rooted_runtime = _YOLOMUX_ROOTS.root is not None and path == RUNTIME_DIR
    candidates = (path.parent, path) if rooted_runtime else (path.parent.parent, path.parent, path)
    for candidate in candidates:
        _ensure_private_runtime_directory(candidate)
    if not rooted_runtime:
        cleanup_previous_boot_runtime_dirs(path)
    return path


def _runtime_directory_error(candidate: Path, metadata: os.stat_result, reason: str, action: str) -> PermissionError:
    """Describe an unsafe runtime path without hiding the remediation."""

    found_mode = stat.S_IMODE(metadata.st_mode)
    return PermissionError(
        f"unsafe runtime directory {candidate}: {reason}; found mode {found_mode:04o}, "
        f"owner uid {metadata.st_uid}; required a non-symlink directory owned by uid {os.getuid()} "
        f"with mode 0700; {action}"
    )


def _ensure_private_runtime_directory(candidate: Path) -> None:
    """Create or upgrade one application-owned runtime path component safely."""

    if not candidate.exists() and not candidate.is_symlink():
        candidate.mkdir(mode=0o700)
    metadata = candidate.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise _runtime_directory_error(
            candidate, metadata, "path is a symlink",
            "replace it with a private directory after verifying the target is not needed",
        )
    if not stat.S_ISDIR(metadata.st_mode):
        raise _runtime_directory_error(
            candidate, metadata, "path is not a directory",
            "replace it with a private directory",
        )
    if metadata.st_uid != os.getuid():
        raise _runtime_directory_error(
            candidate, metadata, "path is owned by another uid",
            "do not reuse it; choose a different YOLOMUX_RUNTIME_DIR or have its owner remove it",
        )
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        try:
            candidate.chmod(0o700)
        except OSError as error:
            raise _runtime_directory_error(
                candidate, metadata, f"could not tighten its mode ({type(error).__name__})",
                f"run chmod 700 {candidate} after verifying it is private",
            ) from error
        metadata = candidate.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise _runtime_directory_error(
                candidate, metadata, "path changed while its mode was tightened",
                "inspect the path and recreate a private runtime directory",
            )


def cleanup_previous_boot_runtime_dirs(root: Path) -> tuple[Path, ...]:
    """Remove only private previous-boot siblings of the current runtime root."""
    path = Path(root)
    host_dir = path.parent
    removed: list[Path] = []
    for candidate in host_dir.glob("b-*"):
        if candidate == path:
            continue
        _ensure_private_runtime_directory(candidate)
        shutil.rmtree(candidate)
        removed.append(candidate)
    return tuple(removed)


_YOLOMUX_ROOTS = resolve_yolomux_roots()
# Whether the caller pinned every product path under one YOLOMUX_ROOT. That is
# the difference between "several YOLOmux servers share this runtime directory,
# so a survivor there may legitimately belong to one of them" and "this root has
# exactly one launcher, so a survivor has no successor to be handed to". Every
# adoption decision reads this, so it is resolved once beside the roots rather
# than re-derived from a path shape at each call site.
MANAGED_PRIVATE_ROOT = _YOLOMUX_ROOTS.root is not None
RUNTIME_DIR = _YOLOMUX_ROOTS.runtime_dir
CONFIG_DIR = _YOLOMUX_ROOTS.config_dir
STATE_DIR = _YOLOMUX_ROOTS.state_dir
YOLOMUX_CACHE_DIR = _YOLOMUX_ROOTS.cache_dir
MODEL_PRICING_CACHE_DIR = YOLOMUX_CACHE_DIR / "model-pricing"
MODEL_PRICING_DATABASE_PATH = MODEL_PRICING_CACHE_DIR / "pricing.sqlite3"
YOAGENT_CODEX_HOME = _YOLOMUX_ROOTS.codex_home
STATE_PATH = CONFIG_DIR / "state.json"

# Auth creates its configuration at import time. Resolve and validate every
# rooted product path first so an invalid root leaves no partial directory.
from .. import auth as _auth


def event_log_path(state_dir: Path | None = None) -> Path:
    """Return this host's event journal without adopting legacy shared history."""

    root = STATE_DIR if state_dir is None else Path(state_dir)
    return host_partitioned_state_dir(root) / f"events-v{PERSISTENT_STATE_GENERATION}.jsonl"


def run_history_path(state_dir: Path | None = None) -> Path:
    """Return this host's run history without adopting legacy shared history."""

    root = STATE_DIR if state_dir is None else Path(state_dir)
    return host_partitioned_state_dir(root) / "run-history.json"


EVENT_LOG_PATH = event_log_path()
RUN_HISTORY_PATH = run_history_path()
ACTIVITY_PATH = STATE_DIR / "activity.json"
TMUX_AI_STATUS_PATH = STATE_DIR / "tmux-AI-status.json"
LEGACY_ATTENTION_ACKS_PATH = STATE_DIR / "attention-acks.json"
ACTIVITY_HEARTBEATS_PATH = STATE_DIR / "activity-heartbeats.jsonl"
WATCH_INDEX_PATH = STATE_DIR / "watch-index.json"
AUTO_APPROVE_LOCK_DIR = RUNTIME_DIR / "locks"
CONTROL_SOCKET_DIR = RUNTIME_DIR / "control"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
@dataclass(frozen=True, slots=True)
class AgentClientSpec:
    """Capabilities shared by every supported interactive client."""

    label: str
    visible: bool = False
    native_context_menu: bool = False
    restart: bool = False
    managed_chat: bool = False
    auto_approve: bool = False
    prompt_transport: bool = False
    jsonl_transcript: bool = False


AGENT_CLIENTS = {
    "claude": AgentClientSpec(
        label="Claude",
        visible=True,
        restart=True,
        managed_chat=True,
        auto_approve=True,
        prompt_transport=True,
        jsonl_transcript=True,
    ),
    "codex": AgentClientSpec(
        label="Codex",
        visible=True,
        restart=True,
        managed_chat=True,
        auto_approve=True,
        prompt_transport=True,
        jsonl_transcript=True,
    ),
    # TODO(OpenCode): enable managed_chat, auto_approve, and prompt_transport after their contracts exist.
    "opencode": AgentClientSpec(label="OpenCode", visible=True, native_context_menu=True),
}


def agent_client_kinds(capability: str | None = None) -> frozenset[str]:
    if capability is None:
        return frozenset(AGENT_CLIENTS)
    return frozenset(
        kind for kind, spec in AGENT_CLIENTS.items() if getattr(spec, capability) is True
    )


AGENT_COMMANDS = agent_client_kinds() | {"term"}
VISIBLE_AGENT_KINDS = agent_client_kinds("visible")
MANAGED_CHAT_AGENT_KINDS = agent_client_kinds("managed_chat")
AUTO_APPROVE_AGENT_KINDS = agent_client_kinds("auto_approve")
PROMPT_TRANSPORT_AGENT_KINDS = agent_client_kinds("prompt_transport")
JSONL_TRANSCRIPT_AGENT_KINDS = agent_client_kinds("jsonl_transcript")
RESTART_AGENT_KINDS = agent_client_kinds("restart")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "static"
TERMINAL_QUERY_RESPONSE_RE = re.compile(r"(?:\x1b\[[?>]?[0-9;]*c|\x1bP[>|!][^\x1b]*(?:\x1b\\|\x9c))")
LINEAR_ID_RE = re.compile(r"(?<![A-Za-z0-9])(?:DIS|DGH|DYN|OPS|INFRA)-\d{1,6}(?![A-Za-z0-9])")
YOLOMUX_VERSION_ASSIGNMENT_RE = re.compile(r"^\s*YOLOMUX_VERSION\s*=\s*['\"]([^'\"]+)['\"]\s*$", re.MULTILINE)
SEMVER_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:\D.*)?$")


def thread_is_running(thread: threading.Thread | None) -> bool:
    """Answer whether an owner's single background thread was started and is still alive.

    Every owner that starts one named worker thread asks exactly this question, so it has
    one implementation here rather than a copy per owner; two byte-identical copies of it
    were what the duplicate-body guard caught.
    """
    return thread is not None and thread.is_alive()


def start_thread_with_rollback(worker: threading.Thread, rollback: Callable[[], None]) -> None:
    """Start an installed worker, restoring its owning record if Thread.start fails."""
    try:
        worker.start()
    except Exception:
        rollback()
        raise


def file_revision(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "0-0"
    return f"{stat.st_mtime_ns}-{stat.st_size}"


def yolomux_client_revision() -> str:
    return file_revision(STATIC_DIR / "yolomux.js")


def yolomux_dev_bundle_revision() -> str:
    """Identify both browser assets so a restarted dev server can refresh stale clients."""
    return ".".join(file_revision(STATIC_DIR / asset) for asset in ("yolomux.js", "yolomux.css"))


class ErrorPayload(TypedDict, total=False):
    state: str
    request: dict[str, str]
    error: str | dict[str, Any]
    user_message: dict[str, Any]
    diagnostic: str
    path: str
    session: str
    status: int


class ProductMetadata(TypedDict):
    format: str
    content_type: str
    length: int
    sha256: str
    disposition: str
    filename: str


PRODUCT_METADATA_FIELDS = frozenset(ProductMetadata.__required_keys__)
PRODUCT_CONTENT_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}(?:; charset=[a-z0-9._-]{1,32})?$",
)
PRODUCT_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$")


def product_filename(raw_name: object, *, fallback: str) -> str:
    """Return one metadata-safe attachment basename for an untrusted path name."""
    name = Path(str(raw_name or "")).name
    safe = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\", ";", "/"} else "_" for char in name).strip()
    if PRODUCT_FILENAME_RE.fullmatch(safe) is None or safe in {".", ".."} or safe[-1:] in {" ", "."}:
        return fallback
    return safe


def inline_json_product_metadata(data: bytes) -> ProductMetadata:
    """Describe one pre-encoded JSON object for the shared opaque-product writer."""

    if not isinstance(data, bytes):
        raise TypeError("JSON product data must be bytes")
    return {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }


def validated_product_metadata(value: object, *, body_length: int) -> ProductMetadata:
    """Validate the uniform bounded control block without inspecting product bytes."""

    if not isinstance(value, dict) or set(value) != PRODUCT_METADATA_FIELDS:
        raise ValueError("product metadata fields do not match the shared contract")
    product_format = value["format"]
    content_type = value["content_type"]
    length = value["length"]
    sha256 = value["sha256"]
    disposition = value["disposition"]
    filename = value["filename"]
    if product_format not in {"json", "opaque_bytes"}:
        raise ValueError("product format must be json or opaque_bytes")
    if not isinstance(content_type, str) or not PRODUCT_CONTENT_TYPE_RE.fullmatch(content_type):
        raise ValueError("product content_type is invalid")
    if product_format == "json" and content_type != "application/json; charset=utf-8":
        raise ValueError("JSON products require the canonical content type")
    if isinstance(length, bool) or not isinstance(length, int) or length < 0 or length != body_length:
        raise ValueError("product length does not match its body")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise ValueError("product sha256 is invalid")
    if disposition not in {"inline", "attachment"}:
        raise ValueError("product disposition must be inline or attachment")
    if not isinstance(filename, str) or (
        filename
        and (
            PRODUCT_FILENAME_RE.fullmatch(filename) is None
            or filename in {".", ".."}
            or filename[-1] in {" ", "."}
        )
    ):
        raise ValueError("product filename must be an empty string or validated basename")
    if disposition == "attachment" and not filename:
        raise ValueError("attachment product requires a filename")
    return {
        "format": product_format,
        "content_type": content_type,
        "length": length,
        "sha256": sha256,
        "disposition": disposition,
        "filename": filename,
    }


def ready_response_envelope_bytes(data: bytes, request_id: str) -> bytes:
    """Frame one opaque JSON object while preserving its established top-level aliases."""

    normalized_request_id = str(request_id or "").strip()
    if not re.fullmatch(r"r-[A-Za-z0-9._-]{1,120}", normalized_request_id):
        raise ValueError("ready API response requires a validated request.id")
    if not isinstance(data, bytes) or len(data) < 2 or data[:1] != b"{" or data[-1:] != b"}":
        raise ValueError("ready API product must be an encoded JSON object")
    aliases = data[1:-1]
    framed = (
        b'{"state":"ready","request":{"id":"'
        + normalized_request_id.encode("ascii")
        + b'"},"data":'
        + data
        + b',"ok":true,"terminal":true'
    )
    if aliases.strip():
        framed += b"," + aliases
    return framed + b"}"


def validated_causal_stack(stack: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return the one causal stack a canonical failure envelope is allowed to carry.

    The API response parent rejects a ``state: failed`` envelope whose stack is empty or whose
    frames lack ``component``/``operation``/``code``, and route dispatch then turns that rejection
    into an internal-error 500 with the real cause lost.  Validating here makes a caller mistake
    fail in the producing call, where the test that covers that caller can see it, instead of in a
    browser.  The caller supplies its own exact frame; this helper never guesses one from the call
    stack or the route registry.
    """
    frames = [dict(frame) for frame in (stack or []) if isinstance(frame, dict)]
    complete = bool(frames) and all(
        bool(str(frame.get("component") or ""))
        and bool(str(frame.get("operation") or ""))
        and bool(str(frame.get("code") or ""))
        for frame in frames
    )
    if not complete:
        raise ValueError(
            "canonical failure payload requires a causal stack of frames carrying "
            f"component, operation, and code; got {stack!r}"
        )
    return frames


def error_payload(
    error: object,
    *,
    message_key: str = "",
    message_params: dict[str, Any] | None = None,
    diagnostic: object = "",
    canonical: bool = False,
    code: str = "",
    origin: str = "",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    stack: list[dict[str, Any]] | None = None,
    request_id: str = "",
    **fields: Any,
) -> ErrorPayload:
    """Return one structured user-message shape while preserving raw diagnostic context.

    Typed request/filesystem errors pass their known fields explicitly through their own ``payload()``
    methods. Plain-string callers retain their raw fallback until they are assigned a catalog key.
    """
    fallback = str(error)
    key = str(message_key or "")
    params = message_params or {}
    raw_diagnostic = diagnostic
    payload: ErrorPayload = user_message_payload(key, fallback, **dict(params or {}))
    if canonical:
        descriptor = payload.get("user_message")
        error_record: dict[str, Any] = {
            "code": str(code or "request_failed"),
            "message": dict(descriptor) if isinstance(descriptor, dict) else {
                "key": key,
                "params": dict(params),
                "fallback": fallback,
            },
            "origin": str(origin or "server.http"),
            "retryable": bool(retryable),
            "details": dict(details or {}),
            "stack": validated_causal_stack(stack),
        }
        return {
            "state": "failed",
            "request": {"id": str(request_id or "")},
            "error": error_record,
        }
    if raw_diagnostic:
        payload["diagnostic"] = str(raw_diagnostic)
    for field_name, value in fields.items():
        if value is not None:
            payload[field_name] = int(value) if field_name == "status" else value
    return payload
MAIN_BRANCHES = {"main", "master"}
METADATA_CACHE_TTL_SECONDS = 300
HTTP_METADATA_TIMEOUT_SECONDS = 2.0
MAX_EVENT_TAIL_LINES = 500
GITHUB_API_ROOT = "https://api.github.com"
LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_LINEAR_ISSUE_BASE_URL = "https://linear.app/issue"
OTHER_BRANCH_LIMIT = 8
# the cache-miss sentinel is owned by cache.py (where the single TtlCache lives) and re-exported
# here for the modules that import it from common. Same object identity, so `is _CACHE_MISS` holds.
_CACHE_MISS = cache_MISS
SERVER_HOSTNAME = current_host_identity().display_hostname
SERVER_STARTED_AT = time.time()
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
_YOLOMUX_COMMIT_TIME_PT: str | None = None
_YOLOMUX_COMMIT_SHA: str | None = None
_YOLOMUX_COMMIT_COUNT: int | None = None
_AGENT_PATH_WARNING_KEYS: set[str] = set()


def heal_server_path() -> str:
    """Make agent CLIs installed under ~/.local/bin visible under stripped service environments."""
    os.environ["PATH"] = healed_runtime_path(os.environ, home=Path.home())
    return os.environ["PATH"]


def codex_home_from_env(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    return resolve_yolomux_roots(values).codex_home


def codex_runtime_env(base_env: dict[str, str] | None = None, *, create_home: bool = True) -> dict[str, str]:
    """Build the Codex subprocess environment used by YO!agent."""
    env = dict(os.environ)
    if base_env is not None:
        env.update(base_env)
    codex_home = codex_home_from_env(env)
    if create_home:
        codex_home.mkdir(parents=True, exist_ok=True)
    env["PATH"] = healed_runtime_path(env, home=Path.home())
    env["CODEX_HOME"] = str(codex_home)
    env["TERM"] = "xterm-256color"
    env["NO_COLOR"] = "1"
    return env


def warn_unavailable_agent_commands_once(agents: tuple[str, ...] = ("claude", "codex")) -> None:
    path = heal_server_path()
    logger = logging.getLogger(__name__)
    for agent in agents:
        if shutil.which(agent):
            continue
        if agent in _AGENT_PATH_WARNING_KEYS:
            continue
        _AGENT_PATH_WARNING_KEYS.add(agent)
        logger.warning("%s not found on server PATH=%s; agent will be greyed in the UI", agent, path)


heal_server_path()


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def positive_finite_number(value: Any) -> float:
    """Normalize counters and rates that cannot be negative, infinite, or NaN."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def path_mtime_or_zero(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def codex_exec_argv(
    *,
    resume_session_id: str | None = None,
    ephemeral: bool = False,
    model: str | None = None,
    effort: str | None = None,
    service_tier: str | None = None,
    search: bool = False,
) -> list[str]:
    selected_model = str(model or SUMMARY_CODEX_MODEL).strip() or SUMMARY_CODEX_MODEL
    selected_effort = str(effort or SUMMARY_CODEX_EFFORT).strip() or SUMMARY_CODEX_EFFORT
    selected_service_tier = str(service_tier or SUMMARY_CODEX_SERVICE_TIER).strip() or SUMMARY_CODEX_SERVICE_TIER
    common = [
        "--json",
        "-m",
        selected_model,
        "-c",
        f'model_reasoning_effort="{selected_effort}"',
        "-c",
        f'service_tier="{selected_service_tier}"',
        "--ignore-rules",
    ]
    if resume_session_id:
        # `codex exec resume` restores the original cwd/sandbox and rejects --sandbox/--cd.
        return ["codex", "exec", "resume", *common, resume_session_id, "-"]
    args = ["codex"]
    if search:
        # `--search` is a top-level Codex flag in 0.141.0; `codex exec --search` is rejected.
        args.append("--search")
    args.extend(["exec", *common, "--sandbox", "read-only"])
    if ephemeral:
        args.append("--ephemeral")
    return [*args, "--cd", str(PROJECT_ROOT), "-"]


def codex_event_kind(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "")
    if event_type in {"thread.started", "turn.started"}:
        return "log"
    if event_type == "turn.completed":
        return "completed"
    if event_type in {"error", "turn.failed"}:
        return "error"
    return "content"


AuthUser = _auth.AuthUser
AuthIdentity = _auth.AuthIdentity
# Compatibility exception: auth.AUTH_CONFIG_PATH, infra.common.AUTH_CONFIG_PATH, filesystem.AUTH_CONFIG_PATH, and filesystem.paths.AUTH_CONFIG_PATH are intentionally independent bindings that may differ simultaneously; _sync_auth_overrides() and filesystem._sync_package_overrides() reconcile their respective compatibility pairs.
# Assigning this binding leaves auth and both filesystem bindings stale; _sync_auth_overrides() updates auth, while assigning filesystem.AUTH_CONFIG_PATH leaves filesystem.paths stale until _sync_package_overrides().
# Collapsing these bindings changes synchronization and filesystem secret filtering, so fixtures must patch all four; this is a documented exception to the divergent-copy rule.
AUTH_CONFIG_PATH = _auth.AUTH_CONFIG_PATH
PLACEHOLDER_AUTH_USERNAME = _auth.PLACEHOLDER_AUTH_USERNAME
PLACEHOLDER_AUTH_PASSWORD = _auth.PLACEHOLDER_AUTH_PASSWORD
GUEST_AUTH_USERNAME = _auth.GUEST_AUTH_USERNAME
GUEST_AUTH_PASSWORD = _auth.GUEST_AUTH_PASSWORD
TEST_AUTH_BYPASS_ENV = _auth.TEST_AUTH_BYPASS_ENV
AUTH_COOKIE_NAME = _auth.AUTH_COOKIE_NAME
AUTH_LOGOUT_COOKIE_NAME = _auth.AUTH_LOGOUT_COOKIE_NAME
AUTH_COOKIE_MAX_AGE_SECONDS = _auth.AUTH_COOKIE_MAX_AGE_SECONDS
AUTH_COOKIE_SECRET_PATH = _auth.AUTH_COOKIE_SECRET_PATH
AUTH_COOKIE_SECRET = _auth.AUTH_COOKIE_SECRET
AUTH_CONFIG = _auth.AUTH_CONFIG
yaml_quote = _auth.yaml_quote
yaml_scalar = _auth.yaml_scalar
strip_yaml_comment = _auth.strip_yaml_comment
parse_yaml_key_value = _auth.parse_yaml_key_value
normalize_auth_role = _auth.normalize_auth_role
auth_user_from_mapping = _auth.auth_user_from_mapping
parse_auth_yaml = _auth.parse_auth_yaml
auth_config_text = _auth.auth_config_text
auth_password_is_hash = _auth.auth_password_is_hash
auth_password_matches = _auth.auth_password_matches
read_auth_users = _auth.read_auth_users
login_username = _auth.login_username
random_auth_password = _auth.random_auth_password
commented_auth_config_text = _auth.commented_auth_config_text
legacy_placeholder_auth_active = _auth.legacy_placeholder_auth_active
write_auth_config = _auth.write_auth_config
secure_auth_config_permissions = _auth.secure_auth_config_permissions


def _sync_auth_overrides() -> None:
    _auth.AUTH_CONFIG_PATH = AUTH_CONFIG_PATH
    _auth.AUTH_COOKIE_SECRET = AUTH_COOKIE_SECRET
    _auth.login_username = login_username
    _auth.random_auth_password = random_auth_password


def starter_auth_users() -> tuple[AuthUser, ...]:
    _sync_auth_overrides()
    return _auth.starter_auth_users()


def initialize_auth_config(path: Path) -> tuple[AuthUser, ...]:
    _sync_auth_overrides()
    return _auth.initialize_auth_config(path)


def current_auth_users() -> tuple[AuthUser, ...]:
    _sync_auth_overrides()
    return _auth.current_auth_users()


def auth_setup_required() -> bool:
    _sync_auth_overrides()
    return _auth.auth_setup_required()


def test_auth_bypass_enabled() -> bool:
    return _auth.test_auth_bypass_enabled()


def load_auth_cookie_secret(path: Path | None = None) -> bytes:
    return _auth.load_auth_cookie_secret(AUTH_COOKIE_SECRET_PATH if path is None else path)


def auth_cookie_value(username: str, password: str) -> str:
    _sync_auth_overrides()
    return _auth.auth_cookie_value(username, password)


def auth_identity_for_credentials(username: str, password: str) -> AuthIdentity | None:
    _sync_auth_overrides()
    return _auth.auth_identity_for_credentials(username, password)


def yolomux_commit_time_pt() -> str:
    global _YOLOMUX_COMMIT_TIME_PT
    if _YOLOMUX_COMMIT_TIME_PT is not None:
        return _YOLOMUX_COMMIT_TIME_PT
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "show", "-s", "--format=%cI", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            timeout=1.0,
        )
        timestamp = result.stdout.strip()
        commit_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        _YOLOMUX_COMMIT_TIME_PT = commit_time.astimezone(PACIFIC_TIME).strftime("%Y-%m-%d %H:%M:%S PT")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
        _YOLOMUX_COMMIT_TIME_PT = "commit time unavailable"
    return _YOLOMUX_COMMIT_TIME_PT


def yolomux_commit_sha() -> str:
    global _YOLOMUX_COMMIT_SHA
    if _YOLOMUX_COMMIT_SHA is not None:
        return _YOLOMUX_COMMIT_SHA
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            timeout=1.0,
        )
        _YOLOMUX_COMMIT_SHA = result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        _YOLOMUX_COMMIT_SHA = ""
    return _YOLOMUX_COMMIT_SHA


def yolomux_commit_count() -> int:
    global _YOLOMUX_COMMIT_COUNT
    if _YOLOMUX_COMMIT_COUNT is not None:
        return _YOLOMUX_COMMIT_COUNT
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-list", "--count", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            timeout=1.0,
        )
        _YOLOMUX_COMMIT_COUNT = max(0, int(result.stdout.strip()))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
        _YOLOMUX_COMMIT_COUNT = 0
    return _YOLOMUX_COMMIT_COUNT


def positive_env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


_FILE_TRANSFER_MAX_BYTES_DEFAULT = 300 * 1024 * 1024
if "YOLOMUX_FILE_TRANSFER_MAX_BYTES" in os.environ:
    FILE_TRANSFER_MAX_BYTES = positive_env_int("YOLOMUX_FILE_TRANSFER_MAX_BYTES", _FILE_TRANSFER_MAX_BYTES_DEFAULT)
else:
    FILE_TRANSFER_MAX_BYTES = positive_env_int("YOLOMUX_UPLOAD_MAX_BYTES", _FILE_TRANSFER_MAX_BYTES_DEFAULT)
UPLOAD_MAX_BYTES = FILE_TRANSFER_MAX_BYTES
UPLOAD_MAX_FILES = positive_env_int("YOLOMUX_UPLOAD_MAX_FILES", 16)
DEFAULT_UPLOAD_FILENAME_TEMPLATE = "{date:%Y%m%d}-{seq:03d}-{name}{ext}"
UPLOAD_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
PASTE_UPLOAD_NAME_RE = re.compile(r"^(?P<date>\d{8})-(?P<index>\d{3})(?P<suffix>\.[A-Za-z0-9]{1,8})$")
UPLOAD_GENERATED_NAME_RE = re.compile(r"^\d{8}-\d{3}(?:-[^/]+)?\.[A-Za-z0-9]{1,12}$")


def is_generated_upload_name(path: str | Path) -> bool:
    return bool(UPLOAD_GENERATED_NAME_RE.fullmatch(Path(path).name))


@dataclass(frozen=True)
class TmuxPaneInfo:
    session: str
    window: str
    pane: str
    pane_id: str
    target: str
    current_path: str
    command: str
    active: bool
    window_active: bool
    title: str
    pid: int
    process_label: str | None = None
    process_label_pid: int | None = None
    window_name: str = ""


# Compatibility import for third-party callers during the terminology migration. New backend code
# must use TmuxPaneInfo so it cannot be confused with a physical YOLOmux YOPane.
PaneInfo = TmuxPaneInfo


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    command: str
    executable: str | None = None


@dataclass(frozen=True)
class AgentInfo:
    session: str
    kind: str
    pid: int
    pane_target: str
    command: str
    cwd: str | None
    status: str | None
    session_id: str | None
    transcript: str | None
    error: str | None
    model: str | None = None
    started_at: float | None = None


@dataclass(frozen=True)
class SessionInfo:
    session: str
    panes: list[TmuxPaneInfo]
    selected_pane: TmuxPaneInfo | None
    agents: list[AgentInfo]


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes


def default_session_names() -> list[str]:
    tmux_sessions, _ = list_tmux_session_names()
    return unique_session_names(tmux_sessions)


def next_numbered_session_name(existing_sessions: list[str]) -> str | None:
    if len(existing_sessions) >= MAX_YOLOMUX_SESSION_TABS:
        return None
    for index in range(1, MAX_YOLOMUX_SESSION_TABS + 1):
        session = str(index)
        if session not in existing_sessions:
            return session
    return None


# Cumulative per-verb git spawn counts (bounded by the small git verb set).
# Monotonic so readers can diff without a cross-thread reset race; sampled into
# session-files performance accounting (DOIT.optimize-backends).
GIT_COMMAND_COUNTS: dict[str, int] = {}
_METADATA_WARM_WORK_METRICS: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "metadata_warm_work_metrics", default=None
)


@contextmanager
def metadata_warm_work_metrics() -> Any:
    """Count only the Git/network work performed by one metadata-warm batchd task."""
    metrics = {"git_spawns": 0, "github_http_calls": 0, "linear_http_calls": 0}
    token = _METADATA_WARM_WORK_METRICS.set(metrics)
    try:
        yield metrics
    finally:
        _METADATA_WARM_WORK_METRICS.reset(token)


def record_metadata_warm_http(url: str) -> None:
    metrics = _METADATA_WARM_WORK_METRICS.get()
    if metrics is None:
        return
    if url.startswith(GITHUB_API_ROOT):
        metrics["github_http_calls"] += 1
    elif url.startswith(LINEAR_API_URL):
        metrics["linear_http_calls"] += 1


def record_git_spawn(args: list[str]) -> None:
    verb = args[0] if args else ""
    GIT_COMMAND_COUNTS[verb] = GIT_COMMAND_COUNTS.get(verb, 0) + 1
    metrics = _METADATA_WARM_WORK_METRICS.get()
    if metrics is not None:
        metrics["git_spawns"] += 1


def git(args: list[str], cwd: str, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    record_git_spawn(args)
    return run_cmd(["git", "-C", cwd, *args], timeout=timeout)


def git_ahead_behind_counts(cwd: str, left: str, right: str = "HEAD") -> tuple[int, int] | None:
    """(ahead, behind) of `right` relative to `left`, or None on git failure / unparseable output.

    ahead = commits in `right` not in `left`; behind = the reverse. Uses
    `git rev-list --left-right --count left...right`, where parts[0] is the left-only count (behind) and
    parts[1] the right-only count (ahead). metadata.py and session_files.py each parsed this
    with their own ref order + return shape; the left/right sign is the classic trap, so it lives once here.
    """
    result = git(["rev-list", "--left-right", "--count", f"{left}...{right}"], cwd)
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1]), int(parts[0])  # (ahead = right-only, behind = left-only)
    except ValueError:
        return None


def parse_yolomux_version_source(source: str) -> str | None:
    match = YOLOMUX_VERSION_ASSIGNMENT_RE.search(source)
    return match.group(1).strip() if match else None


def semver_parts(version: Any) -> tuple[int, int, int] | None:
    match = SEMVER_RE.match(str(version or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def version_change_level(current: Any, target: Any) -> str:
    current_parts = semver_parts(current)
    target_parts = semver_parts(target)
    if current_parts is None or target_parts is None or target_parts <= current_parts:
        return "none"
    if target_parts[0] != current_parts[0]:
        return "major"
    if target_parts[1] != current_parts[1]:
        return "minor"
    if target_parts[2] != current_parts[2]:
        return "patch"
    return "none"


def update_notify_level_allows(change_level: Any, notify_level: Any) -> bool:
    ranks = {"none": 0, "patch": 1, "minor": 2, "major": 3}
    threshold = str(notify_level or "patch")
    if threshold == "none":
        return False
    return ranks.get(str(change_level or "none"), 0) >= ranks.get(threshold, ranks["patch"])


def git_yolomux_version_at_ref(cwd: str, ref: str) -> tuple[str | None, str | None]:
    result = git(["show", f"{ref}:yolomux_lib/common.py"], cwd)
    if result.returncode != 0:
        return None, (result.stderr or f"git show {ref}:yolomux_lib/common.py failed").strip()[:300]
    version = parse_yolomux_version_source(result.stdout or "")
    if not version:
        return None, f"YOLOMUX_VERSION not found in {ref}:yolomux_lib/common.py"
    return version, None


def yolomux_version_parts(version: str) -> tuple[int, ...] | None:
    clean = version.strip()
    if not clean or not re.fullmatch(r"\d+(?:\.\d+)*", clean):
        return None
    return tuple(int(part) for part in clean.split("."))


def yolomux_version_is_newer(target: str, current: str) -> bool:
    target_parts = yolomux_version_parts(target)
    current_parts = yolomux_version_parts(current)
    if target_parts is None or current_parts is None:
        return target.strip() != current.strip()
    length = max(len(target_parts), len(current_parts))
    padded_target = target_parts + (0,) * (length - len(target_parts))
    padded_current = current_parts + (0,) * (length - len(current_parts))
    return padded_target > padded_current


def update_check_status(cwd: str, branch: str = "main", dryrun: bool = False, fetch: bool = True) -> dict[str, Any]:
    """Whether `origin/<branch>` has a newer YOLOMUX_VERSION than the running checkout.

    Reads `yolomux_lib/common.py` from the remote ref via git on the local checkout (reusing its
    existing credentials, so this works for private repos with no GitHub token). SHA and ahead/behind
    counts stay in the payload for diagnostics only; they do not decide whether to notify.
    """
    current_sha = yolomux_commit_sha()
    base = {"available": False, "ahead": 0, "behind": 0, "current": YOLOMUX_VERSION,
            "current_version": YOLOMUX_VERSION, "current_sha": current_sha, "target": None,
            "target_version": None, "target_sha": None, "branch": branch, "dryrun": dryrun, "error": None,
            "version_change_level": "none"}
    if dryrun:
        return {**base, "available": True, "behind": 1, "target": "dryrun",
                "target_version": "dryrun", "version_change_level": "patch"}
    if fetch:
        fetched = git(["fetch", "--quiet", "origin", branch], cwd)
        if fetched.returncode != 0:
            return {**base, "error": (fetched.stderr or "git fetch failed").strip()[:300]}
    counts = git_ahead_behind_counts(cwd, f"origin/{branch}")
    if counts is None:
        return {**base, "error": "git rev-list failed"}
    ahead, behind = counts
    target = git(["rev-parse", "--short=12", f"origin/{branch}"], cwd)
    target_sha = target.stdout.strip() if target.returncode == 0 else None
    target_version, version_error = git_yolomux_version_at_ref(cwd, f"origin/{branch}")
    if version_error:
        return {**base, "ahead": ahead, "behind": behind, "target_sha": target_sha, "error": version_error}
    change_level = version_change_level(YOLOMUX_VERSION, target_version)
    return {**base, "available": bool(target_version and yolomux_version_is_newer(target_version, YOLOMUX_VERSION)),
            "ahead": ahead, "behind": behind, "target": target_version, "target_version": target_version,
            "target_sha": target_sha, "version_change_level": change_level}


def git_bytes(args: list[str], cwd: str, timeout: float = 3.0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=timeout, check=False)


def xterm_asset_path(asset: str) -> Path | None:
    if asset not in {"xterm.js", "xterm.css", "xterm-addon-unicode11.js"}:
        return None
    vendor_path = STATIC_DIR / "vendor" / asset
    return vendor_path if vendor_path.is_file() else None


def split_csv(values: list[str]) -> list[str]:
    parts: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                parts.append(item)
    return parts



def tail_file_lines(path: Path, lines: int) -> str:
    # read a bounded window backward from EOF instead of scanning the whole file front-to-
    # back. Transcripts are multi-hundred-MB JSONL and this is called on every metadata poll,
    # /api/context, /api/session-metadata, and the summary — a full re-scan each time was the hot path.
    want = min(max(1, lines), MAX_TRANSCRIPT_TAIL_LINES)
    chunk = 65536
    max_bytes = want * chunk  # generous per-line ceiling; never walk the entire huge file
    # Accumulate the blocks and carry running totals. Prepending each block to one
    # buffer and re-running `data.count(b"\n")` over the whole buffer made the walk
    # quadratic in the window: transcripts with long JSONL lines need hundreds of
    # 64 KiB steps to reach `want` newlines, so the counting alone re-scanned
    # gigabytes per call. The totals are identical to the whole-buffer counts.
    blocks: list[bytes] = []
    newlines = 0
    scanned = 0
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        pos = handle.tell()
        while pos > 0 and newlines <= want and scanned < max_bytes:
            step = min(chunk, pos)
            pos -= step
            handle.seek(pos)
            block = handle.read(step)
            blocks.append(block)
            newlines += block.count(b"\n")
            scanned += len(block)
    blocks.reverse()
    text = b"".join(blocks).decode("utf-8", errors="replace")
    return "".join(text.splitlines(keepends=True)[-want:])

def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}

def terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    term_outcome = signal_recorded_process_group(process, signal.SIGTERM)
    if not term_outcome["signalled"]:
        logging.getLogger(__name__).warning(
            "refused SIGTERM for process group %s: %s",
            process.pid,
            term_outcome["reason"],
        )
        return
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        kill_outcome = signal_recorded_process_group(process, signal.SIGKILL)
        if not kill_outcome["signalled"]:
            logging.getLogger(__name__).warning(
                "refused SIGKILL for process group %s: %s",
                process.pid,
                kill_outcome["reason"],
            )
            return
        try:
            process.wait(timeout=2.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            return

def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"
