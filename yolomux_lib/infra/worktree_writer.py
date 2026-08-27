# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared-worktree writer declaration and host-local tool artifact routing."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from collections.abc import MutableMapping
from dataclasses import dataclass
import hashlib
import json
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import uuid

from .atomic_file import atomic_write_text
from .atomic_file import is_atomic_write_temp
from .host_identity import current_host_identity
from .host_identity import HostIdentity
from .host_identity import is_current_local_process
from .host_identity import LocalProcessReason
from .root_paths import YOLOMUX_ROOT_ENV
from .root_paths import resolved_product_path
from tools.instance_isolation import GENERATED_PYTHON_CACHE_PREFIX_ENV
from tools.instance_isolation import resolved_path
from tools.instance_isolation import rooted_product_path
from tools.instance_isolation import YolomuxRootError


WRITER_TOKEN_ENV = "YOLOMUX_WORKTREE_WRITER_TOKEN"
WRITER_RECORD_NAME = "owner.json"
WRITER_SCHEMA = 1
DEFAULT_STALE_SECONDS = 30.0
DEFAULT_HEARTBEAT_SECONDS = 5.0

CONTAINER_REFUSAL_NO_TOKEN = "no_inherited_token"
CONTAINER_REFUSAL_STALE_TOKEN = "stale_inherited_token"

_CONTAINER_REFUSAL_MESSAGES = {
    CONTAINER_REFUSAL_NO_TOKEN: (
        f"in-container caller inherited no {WRITER_TOKEN_ENV} and the writer slot is read-only; "
        "the host process must acquire the worktree writer lease and forward its "
        f"{WRITER_TOKEN_ENV} before starting a container"
    ),
    CONTAINER_REFUSAL_STALE_TOKEN: (
        f"in-container caller inherited a {WRITER_TOKEN_ENV} that matches no live writer record, "
        "so its borrowed authority is stale or invalid, and the writer slot is read-only; the "
        "host process must re-acquire the worktree writer lease and forward its current "
        f"{WRITER_TOKEN_ENV} before starting a container"
    ),
}

@dataclass(frozen=True)
class WorktreeWriterStatus:
    state: str
    active: bool
    stale: bool
    foreign_host: bool
    stable_host_id: str = ""
    hostname: str = ""
    token: str = ""
    reason: str = ""
    record: dict[str, Any] | None = None


@dataclass(frozen=True)
class HostArtifactPaths:
    root: Path
    python_cache: Path
    pytest_cache: Path
    package_cache: Path
    logs: Path


class WorktreeWriterError(RuntimeError):
    """Base class for declaration failures."""


class WorktreeWriterBusy(WorktreeWriterError):
    def __init__(self, status: WorktreeWriterStatus) -> None:
        self.status = status
        owner = status.hostname or status.stable_host_id or "unknown owner"
        super().__init__(f"worktree writer is busy: {owner} ({status.reason or status.state})")


class WorktreeWriterReleaseError(WorktreeWriterError):
    """An owned declaration could not be refreshed or released safely."""


class WorktreeWriterContainerRefusal(WorktreeWriterError):
    """An in-container caller cannot mint a declaration on the read-only slot mount.

    The writer slot lives under the git-common directory, which containers only
    ever get bind-mounted read-only. A host process is the sole declaration
    minter (`tools/check.py`); an in-container caller is only ever supposed to
    borrow the host's already-exported token. Attempting the real acquire path
    there always fails on the read-only mount, previously with an
    unrelated-looking `PermissionError` deep in a `mkdir` chain instead of a
    clear refusal.

    `reason` separates the two failing authorities, because they need different
    operator actions: nothing was inherited at all, or a non-empty inherited
    token matched no live writer record.
    """

    def __init__(self, reason: str, *, inherited_token: str = "") -> None:
        self.reason = reason
        self.inherited_token = inherited_token
        super().__init__(_CONTAINER_REFUSAL_MESSAGES[reason])


class WorktreeArtifactError(WorktreeWriterError):
    """A generated artifact path resolves inside the shared worktree."""


def _resolved(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _path_is_inside(path: Path, parent: Path) -> bool:
    return _resolved(path).is_relative_to(_resolved(parent))


def worktree_declaration_slot(worktree_root: Path) -> Path:
    """Return this physical worktree's shared marker slot without invoking Git."""

    root = _resolved(worktree_root)
    git_entry = root / ".git"
    if git_entry.is_dir():
        git_dir = git_entry
    elif git_entry.is_file():
        text = git_entry.read_text(encoding="utf-8").strip()
        prefix = "gitdir:"
        if not text.lower().startswith(prefix):
            raise WorktreeWriterError(f"cannot resolve worktree declaration from {git_entry}")
        raw = text[len(prefix) :].strip()
        if not raw:
            raise WorktreeWriterError(f"empty Git directory declaration in {git_entry}")
        candidate = Path(raw).expanduser()
        git_dir = candidate if candidate.is_absolute() else root / candidate
    else:
        raise WorktreeWriterError(f"{root} is not a Git worktree")
    return _resolved(git_dir) / "yolomux" / "worktree-writer"


def _claim_slot_leaf(slot: Path) -> bool:
    """Create the exclusive slot leaf, reporting whether this caller won the race."""

    try:
        slot.mkdir(mode=0o700)
    except FileExistsError:
        return False
    return True


def _slot_record_path(slot_dir: Path) -> Path:
    return Path(slot_dir) / WRITER_RECORD_NAME


def _read_record(record_path: Path) -> dict[str, Any]:
    value = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("writer declaration must contain one JSON object")
    return value


def _record_heartbeat(record: dict[str, Any], record_path: Path) -> float:
    try:
        heartbeat = float(record.get("heartbeat_at") or 0.0)
    except (TypeError, ValueError):
        heartbeat = 0.0
    if heartbeat > 0.0:
        return heartbeat
    try:
        return float(record_path.stat().st_mtime)
    except OSError:
        return 0.0


def _status_from_record(
    record: dict[str, Any],
    record_path: Path,
    *,
    host_identity: HostIdentity,
    now: float,
    stale_after_seconds: float,
    start_identity_reader: Callable[[int], str | None] | None,
) -> WorktreeWriterStatus:
    stable_host_id = str(record.get("stable_host_id") or "").strip().lower()
    hostname = str(record.get("hostname") or "").strip()
    token = str(record.get("token") or "").strip()
    heartbeat = _record_heartbeat(record, record_path)
    expired = heartbeat <= 0.0 or now - heartbeat > stale_after_seconds
    try:
        schema = int(record.get("schema") or 0)
    except (TypeError, ValueError):
        schema = 0
    if not stable_host_id or not token or schema != WRITER_SCHEMA:
        return WorktreeWriterStatus(
            state="invalid_stale" if expired else "invalid_active",
            active=not expired,
            stale=expired,
            foreign_host=False,
            stable_host_id=stable_host_id,
            hostname=hostname,
            token=token,
            reason="writer declaration is incomplete or has an unsupported schema",
            record=record,
        )
    if stable_host_id != host_identity.stable_host_id:
        return WorktreeWriterStatus(
            state="foreign_stale" if expired else "foreign_active",
            active=not expired,
            stale=expired,
            foreign_host=True,
            stable_host_id=stable_host_id,
            hostname=hostname,
            token=token,
            reason="foreign writer heartbeat expired" if expired else "fresh foreign writer declaration",
            record=record,
        )
    diagnostic = is_current_local_process(
        record,
        host_identity=host_identity,
        start_identity_reader=start_identity_reader,
    )
    immediately_stale = diagnostic.reason in {
        LocalProcessReason.PREVIOUS_BOOT,
        LocalProcessReason.PROCESS_NOT_FOUND,
        LocalProcessReason.PROCESS_IDENTITY_REUSED,
    }
    stale = immediately_stale or expired
    return WorktreeWriterStatus(
        state="local_stale" if stale else "local_active",
        active=not stale,
        stale=stale,
        foreign_host=False,
        stable_host_id=stable_host_id,
        hostname=hostname,
        token=token,
        reason=diagnostic.reason.value if not expired else "local writer heartbeat expired",
        record=record,
    )


def inspect_worktree_writer(
    worktree_root: Path,
    *,
    host_identity: HostIdentity,
    slot_dir: Path | None = None,
    now: float | None = None,
    stale_after_seconds: float = DEFAULT_STALE_SECONDS,
    start_identity_reader: Callable[[int], str | None] | None = None,
) -> WorktreeWriterStatus:
    """Inspect without creating, rewriting, reclaiming, or invoking a Git command."""

    slot = Path(slot_dir) if slot_dir is not None else worktree_declaration_slot(worktree_root)
    record_path = _slot_record_path(slot)
    if not slot.exists():
        return WorktreeWriterStatus("clear", False, False, False, reason="no writer declaration")
    active_now = time.time() if now is None else float(now)
    try:
        record = _read_record(record_path)
    except FileNotFoundError:
        try:
            modified = float(slot.stat().st_mtime)
        except OSError:
            modified = active_now
        stale = active_now - modified > stale_after_seconds
        return WorktreeWriterStatus(
            "initializing_stale" if stale else "initializing_active",
            not stale,
            stale,
            False,
            reason="writer slot has no complete owner record",
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        try:
            modified = float(record_path.stat().st_mtime)
        except OSError:
            modified = active_now
        stale = active_now - modified > stale_after_seconds
        return WorktreeWriterStatus(
            "invalid_stale" if stale else "invalid_active",
            not stale,
            stale,
            False,
            reason=f"writer declaration cannot be read: {type(error).__name__}",
        )
    return _status_from_record(
        record,
        record_path,
        host_identity=host_identity,
        now=active_now,
        stale_after_seconds=max(0.1, float(stale_after_seconds)),
        start_identity_reader=start_identity_reader,
    )


def _remove_exact_slot(slot: Path) -> None:
    record_path = _slot_record_path(slot)
    children = list(slot.iterdir())
    # A writer killed between creating its heartbeat temp and renaming it leaves that temp behind.
    # Treating it as a foreign entry made the slot unreclaimable, so every later writer in this
    # worktree refused to start until someone deleted the file by hand.
    abandoned = [
        path
        for path in children
        if path.name != WRITER_RECORD_NAME and is_atomic_write_temp(path.name, target_name=WRITER_RECORD_NAME)
    ]
    unexpected = [
        path for path in children if path.name != WRITER_RECORD_NAME and path not in abandoned
    ]
    if unexpected:
        raise WorktreeWriterReleaseError(
            f"refusing to remove writer slot with unexpected entries: {', '.join(path.name for path in unexpected)}"
        )
    for path in abandoned:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    try:
        record_path.unlink()
    except FileNotFoundError:
        pass
    slot.rmdir()


def _reclaim_stale_slot(slot: Path, token: str) -> None:
    tombstone = slot.with_name(f".{slot.name}.stale-{token}")
    os.replace(slot, tombstone)
    _remove_exact_slot(tombstone)


def _writer_record(identity: HostIdentity, *, token: str, purpose: str, now: float) -> dict[str, Any]:
    return {
        **identity.process_record_fields(),
        "schema": WRITER_SCHEMA,
        "token": token,
        "purpose": str(purpose),
        "hostname": identity.display_hostname,
        "declared_at": now,
        "heartbeat_at": now,
    }


class WorktreeWriterLease:
    def __init__(
        self,
        *,
        token: str,
        slot_dir: Path,
        record: dict[str, Any],
        borrowed: bool,
        clock: Callable[[], float],
        heartbeat_interval_seconds: float,
        environ: MutableMapping[str, str],
        previous_token: str | None,
    ) -> None:
        self.token = token
        self.slot_dir = Path(slot_dir)
        self.record_path = _slot_record_path(self.slot_dir)
        self.record = dict(record)
        self.borrowed = bool(borrowed)
        self.clock = clock
        self.heartbeat_interval_seconds = max(0.0, float(heartbeat_interval_seconds))
        self.environ = environ
        self.previous_token = previous_token
        self._stop = threading.Event()
        self._release_lock = threading.Lock()
        self._released = False
        self._heartbeat_error: BaseException | None = None
        self._heartbeat_thread: threading.Thread | None = None
        if not self.borrowed and self.heartbeat_interval_seconds > 0.0:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="worktree-writer-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_seconds):
            try:
                current = _read_record(self.record_path)
                if str(current.get("token") or "") != self.token:
                    return
                current["heartbeat_at"] = float(self.clock())
                atomic_write_text(self.record_path, json.dumps(current, sort_keys=True) + "\n", mode=0o600)
                self.record = current
            except Exception as error:
                self._heartbeat_error = error
                return

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
            self._stop.set()
            if self._heartbeat_thread is not None:
                self._heartbeat_thread.join(timeout=max(1.0, self.heartbeat_interval_seconds + 1.0))
                if self._heartbeat_thread.is_alive():
                    raise WorktreeWriterReleaseError("writer heartbeat did not stop during release")
            if not self.borrowed:
                try:
                    current = _read_record(self.record_path)
                except FileNotFoundError:
                    current = {}
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    raise WorktreeWriterReleaseError(f"cannot verify writer token during release: {error}") from error
                if str(current.get("token") or "") == self.token:
                    _remove_exact_slot(self.slot_dir)
            if self.environ.get(WRITER_TOKEN_ENV) == self.token:
                if self.previous_token is None:
                    self.environ.pop(WRITER_TOKEN_ENV, None)
                else:
                    self.environ[WRITER_TOKEN_ENV] = self.previous_token
            if self._heartbeat_error is not None:
                raise WorktreeWriterReleaseError(
                    f"writer heartbeat failed: {type(self._heartbeat_error).__name__}: {self._heartbeat_error}"
                ) from self._heartbeat_error

    def __enter__(self) -> "WorktreeWriterLease":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def acquire_worktree_writer(
    worktree_root: Path,
    *,
    purpose: str,
    host_identity: HostIdentity | None = None,
    slot_dir: Path | None = None,
    clock: Callable[[], float] | None = None,
    stale_after_seconds: float = DEFAULT_STALE_SECONDS,
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    start_identity_reader: Callable[[int], str | None] | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> WorktreeWriterLease:
    """Claim the one writer slot, reclaiming only typed-dead or expired owners."""

    identity = host_identity or current_host_identity()
    slot = Path(slot_dir) if slot_dir is not None else worktree_declaration_slot(worktree_root)
    values = os.environ if environ is None else environ
    active_clock = time.time if clock is None else clock
    inherited_token = str(values.get(WRITER_TOKEN_ENV) or "").strip()
    if inherited_token:
        status = inspect_worktree_writer(
            worktree_root,
            host_identity=identity,
            slot_dir=slot,
            now=active_clock(),
            stale_after_seconds=stale_after_seconds,
            start_identity_reader=start_identity_reader,
        )
        if status.record is not None and str(status.record.get("token") or "") == inherited_token:
            return WorktreeWriterLease(
                token=inherited_token,
                slot_dir=slot,
                record=status.record,
                borrowed=True,
                clock=active_clock,
                heartbeat_interval_seconds=0.0,
                environ=values,
                previous_token=inherited_token,
            )
    token = uuid.uuid4().hex
    for _attempt in range(4):
        now = float(active_clock())
        status = inspect_worktree_writer(
            worktree_root,
            host_identity=identity,
            slot_dir=slot,
            now=now,
            stale_after_seconds=stale_after_seconds,
            start_identity_reader=start_identity_reader,
        )
        if status.active:
            raise WorktreeWriterBusy(status)
        if status.stale:
            try:
                _reclaim_stale_slot(slot, token)
            except FileNotFoundError:
                continue
        try:
            slot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            claimed = _claim_slot_leaf(slot)
        except PermissionError as error:
            # React to the real failure rather than guessing from the environment alone. A busy
            # or stale existing slot never reaches here (it was raised or reclaimed above without
            # creating anything new), and a caller-owned writable `slot_dir` such as a test's own
            # `tmp_path` is never the shared read-only mount, so its mkdir simply succeeds. Only a
            # genuinely fresh declaration against a read-only directory lands here, which is the
            # in-container case this refusal exists for. `environ` is injectable, so read the
            # marker off `values` rather than `tools.docker_image.running_inside_container`.
            if values.get("YOLOMUX_CHECK_IN_CONTAINER") == "1":
                raise WorktreeWriterContainerRefusal(
                    CONTAINER_REFUSAL_STALE_TOKEN if inherited_token else CONTAINER_REFUSAL_NO_TOKEN,
                    inherited_token=inherited_token,
                ) from error
            raise
        if not claimed:
            continue
        record = _writer_record(identity, token=token, purpose=purpose, now=now)
        try:
            atomic_write_text(_slot_record_path(slot), json.dumps(record, sort_keys=True) + "\n", mode=0o600)
        except Exception as error:
            try:
                _remove_exact_slot(slot)
            except (OSError, WorktreeWriterReleaseError) as cleanup_error:
                raise WorktreeWriterReleaseError(
                    f"writer claim failed and its incomplete slot could not be removed: {cleanup_error}"
                ) from error
            raise
        previous_token = values.get(WRITER_TOKEN_ENV)
        values[WRITER_TOKEN_ENV] = token
        return WorktreeWriterLease(
            token=token,
            slot_dir=slot,
            record=record,
            borrowed=False,
            clock=active_clock,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            environ=values,
            previous_token=previous_token,
        )
    raise WorktreeWriterBusy(
        WorktreeWriterStatus(
            "claim_race",
            True,
            False,
            False,
            reason="writer slot changed during bounded claim attempts",
        )
    )


def server_start_writer_warning(
    worktree_root: Path,
    *,
    host_identity: HostIdentity | None = None,
    slot_dir: Path | None = None,
    now: float | None = None,
) -> str:
    """Return a startup warning without acquiring, reclaiming, or refusing."""

    identity = host_identity or current_host_identity()
    if slot_dir is None and not (_resolved(worktree_root) / ".git").exists():
        return ""
    try:
        status = inspect_worktree_writer(
            worktree_root,
            host_identity=identity,
            slot_dir=slot_dir,
            now=now,
        )
    except (OSError, WorktreeWriterError) as error:
        return f"Worktree writer declaration unavailable ({error}); continuing as a read-only source server."
    if status.state == "foreign_active":
        owner = status.hostname or status.stable_host_id
        return (
            f"Worktree is declared writable by {owner} ({status.stable_host_id}); continuing as a read-only source "
            "server. Do not run builds, tests, package installs, uploads into the checkout, or Git mutations here."
        )
    if status.state in {"invalid_active", "initializing_active"}:
        return (
            f"Worktree has an active but unverifiable writer declaration ({status.reason}); continuing as a read-only "
            "source server and refusing to infer writer safety."
        )
    return ""


def host_artifact_paths(
    worktree_root: Path,
    *,
    environ: MutableMapping[str, str] | None = None,
    temporary_dir: Path | None = None,
    uid: int | None = None,
) -> HostArtifactPaths:
    """Return tool artifact paths under one explicitly local runtime base."""

    values = os.environ if environ is None else environ
    explicit = str(values.get("YOLOMUX_HOST_ARTIFACT_DIR") or "").strip()
    if explicit:
        configured_root = str(values.get(YOLOMUX_ROOT_ENV) or "").strip()
        base = (
            rooted_product_path(
                values,
                "YOLOMUX_HOST_ARTIFACT_DIR",
                resolved_product_path(values, YOLOMUX_ROOT_ENV, configured_root),
                explicit,
            )
            if configured_root
            else resolved_product_path(values, "YOLOMUX_HOST_ARTIFACT_DIR", explicit)
        )
    elif values.get(YOLOMUX_ROOT_ENV):
        root = resolved_product_path(values, YOLOMUX_ROOT_ENV, values[YOLOMUX_ROOT_ENV])
        runtime = rooted_product_path(values, "YOLOMUX_RUNTIME_DIR", root, root / "runtime")
        base = runtime / "yolomux-worktree-artifacts"
    elif values.get("YOLOMUX_RUNTIME_DIR"):
        base = resolved_product_path(values, "YOLOMUX_RUNTIME_DIR", values["YOLOMUX_RUNTIME_DIR"]) / "yolomux-worktree-artifacts"
    elif values.get("XDG_RUNTIME_DIR"):
        base = resolved_product_path(
            values,
            "XDG_RUNTIME_DIR",
            values["XDG_RUNTIME_DIR"],
            reject_home=False,
        ) / "yolomux-worktree-artifacts"
    else:
        active_uid = os.getuid() if uid is None else int(uid)
        configured_tmp = str(values.get("TMPDIR") or "").strip()
        default_tmp = temporary_dir or tempfile.gettempdir()
        base = resolved_product_path(values, "TMPDIR", configured_tmp or default_tmp) / f"yolomux-{active_uid}" / "worktree-artifacts"
    worktree = _resolved(worktree_root)
    digest = hashlib.sha256(str(worktree).encode("utf-8")).hexdigest()[:16]
    root = base / f"w-{digest}"
    if _path_is_inside(root, worktree):
        raise WorktreeArtifactError(f"host artifact root resolves inside shared worktree: {root}")
    return HostArtifactPaths(
        root=root,
        python_cache=root / "python-cache",
        pytest_cache=root / "pytest-cache",
        package_cache=root / "package-cache",
        logs=root / "logs",
    )


def configure_host_local_artifacts(
    worktree_root: Path,
    *,
    environ: MutableMapping[str, str] | None = None,
    temporary_dir: Path | None = None,
    uid: int | None = None,
    apply_process: bool = True,
) -> HostArtifactPaths:
    """Install bytecode/cache environment without writing inside the worktree."""

    values = os.environ if environ is None else environ
    paths = host_artifact_paths(
        worktree_root,
        environ=values,
        temporary_dir=temporary_dir,
        uid=uid,
    )
    configured_prefix = str(values.get("PYTHONPYCACHEPREFIX") or "").strip()
    generated_marker = str(values.get(GENERATED_PYTHON_CACHE_PREFIX_ENV) or "").strip()
    generated_prefix = not configured_prefix or generated_marker == configured_prefix
    if configured_prefix and values.get(YOLOMUX_ROOT_ENV):
        root = resolved_product_path(values, YOLOMUX_ROOT_ENV, values[YOLOMUX_ROOT_ENV])
        python_cache = rooted_product_path(values, "PYTHONPYCACHEPREFIX", root, configured_prefix)
    elif configured_prefix:
        python_cache = resolved_product_path(values, "PYTHONPYCACHEPREFIX", configured_prefix)
    else:
        python_cache = paths.python_cache
    if _path_is_inside(python_cache, worktree_root):
        raise WorktreeArtifactError(f"PYTHONPYCACHEPREFIX resolves inside shared worktree: {python_cache}")
    values["PYTHONPYCACHEPREFIX"] = str(python_cache)
    if generated_prefix:
        values[GENERATED_PYTHON_CACHE_PREFIX_ENV] = str(python_cache)
    else:
        values.pop(GENERATED_PYTHON_CACHE_PREFIX_ENV, None)
    values["GIT_OPTIONAL_LOCKS"] = "0"
    values["PIP_CACHE_DIR"] = str(paths.package_cache / "pip")
    values["NPM_CONFIG_CACHE"] = str(paths.package_cache / "npm")
    values["COVERAGE_FILE"] = str(paths.pytest_cache / ".coverage")
    if apply_process:
        python_cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        sys.pycache_prefix = str(python_cache)
        if str(values.get("PYTHONDONTWRITEBYTECODE") or "").strip().lower() not in {"1", "true", "yes"}:
            sys.dont_write_bytecode = False
    return HostArtifactPaths(
        root=paths.root,
        python_cache=python_cache,
        pytest_cache=paths.pytest_cache,
        package_cache=paths.package_cache,
        logs=paths.logs,
    )


def child_process_artifact_environment(
    worktree_root: Path,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Rebase only product-generated artifacts when a child uses a newer root."""

    child = dict(os.environ if environ is None else environ)
    configured_root = str(child.get(YOLOMUX_ROOT_ENV) or "").strip()
    configured_prefix = str(child.get("PYTHONPYCACHEPREFIX") or "").strip()
    generated_marker = str(child.get(GENERATED_PYTHON_CACHE_PREFIX_ENV) or "").strip()
    active_python_cache = str(sys.pycache_prefix or "").strip()
    if configured_root and configured_prefix and generated_marker == configured_prefix == active_python_cache:
        root = resolved_product_path(child, YOLOMUX_ROOT_ENV, configured_root)
        try:
            prefix = resolved_product_path(child, "PYTHONPYCACHEPREFIX", configured_prefix)
        except YolomuxRootError:
            prefix = resolved_path(configured_prefix)
        if not prefix.is_relative_to(root):
            child.pop("PYTHONPYCACHEPREFIX", None)
            child.pop(GENERATED_PYTHON_CACHE_PREFIX_ENV, None)
    configure_host_local_artifacts(worktree_root, environ=child, apply_process=False)
    return child


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one command while declaring this Git worktree writable.")
    parser.add_argument("--purpose", default="explicit-command")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    worktree = Path.cwd()
    try:
        with acquire_worktree_writer(worktree, purpose=args.purpose):
            return subprocess.run(command, cwd=worktree, check=False).returncode
    except WorktreeWriterBusy as error:
        print(f"WORKTREE WRITE REFUSED: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
