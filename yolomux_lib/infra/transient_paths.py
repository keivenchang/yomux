# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Identity-fenced roots for disposable, short-lived YOLOmux artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from contextlib import contextmanager
from typing import Any
import uuid

from .host_identity import HostIdentity
from .host_identity import current_host_identity
from .host_identity import is_current_local_process


MANIFEST_NAME = "manifest.json"
NAMESPACE_TEST = "test"
NAMESPACE_SERVER = "server"
NAMESPACE_ROOT_NAMES = {
    NAMESPACE_TEST: "yolomux-test",
    NAMESPACE_SERVER: "yolomux-server",
}
NAMESPACES = frozenset({NAMESPACE_TEST, NAMESPACE_SERVER})
RETENTION_DISPOSABLE = "disposable"
RETENTION_ON_FAILURE = "retain-on-failure"
RETENTION_CALLER = "caller-retained"
RETENTION_SERVICE = "service-runtime"
RETENTION_DURABLE = "durable"
RETENTION_CLASSES = frozenset(
    {
        RETENTION_DISPOSABLE,
        RETENTION_ON_FAILURE,
        RETENTION_CALLER,
        RETENTION_SERVICE,
        RETENTION_DURABLE,
    }
)


class TransientPathError(RuntimeError):
    """A transient root could not be created, validated, or safely removed."""


@dataclass(frozen=True)
class TransientRunRoot:
    """One manifest-backed root owned by the creating process."""

    path: Path
    manifest: dict[str, Any]
    identity: HostIdentity

    @property
    def retention(self) -> str:
        return str(self.manifest["retention_class"])

    def child(self, name: str) -> Path:
        """Create and return a named child without allowing path traversal."""

        validate_run_root(self.path)
        clean_name = str(name or "").strip()
        if not clean_name or Path(clean_name).name != clean_name or clean_name in {".", ".."}:
            raise TransientPathError(f"invalid transient child name: {name!r}")
        child = self.path / clean_name
        child.mkdir(mode=0o700, exist_ok=False)
        return child

    @contextmanager
    def child_claim(self, *, kind: str, pid: int, start_identity: str):
        """Register a child process for the duration of its use of this root."""

        clean_kind = str(kind or "").strip()
        if not clean_kind or Path(clean_kind).name != clean_kind or clean_kind in {".", ".."} or int(pid) <= 1 or not str(start_identity or "").strip():
            raise TransientPathError("child claim requires kind, PID, and process-start identity")
        validate_run_root(self.path)
        claims = self.path / "claims"
        claims.mkdir(mode=0o700, exist_ok=True)
        claims_info = claims.lstat()
        if not stat.S_ISDIR(claims_info.st_mode) or stat.S_ISLNK(claims_info.st_mode) or claims_info.st_uid != os.geteuid():
            raise TransientPathError(f"transient claims directory is unsafe: {claims}")
        claim = claims / f"{clean_kind}-{int(pid)}-{uuid.uuid4().hex[:8]}.json"
        _write_manifest(
            claim,
            {
                "schema": 1,
                "kind": clean_kind,
                **self.identity.process_record_fields(pid=int(pid), start_identity=str(start_identity)),
            },
        )
        try:
            yield claim
        finally:
            claim.unlink(missing_ok=True)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _remove_tree_at(parent_fd: int, name: str, *, expected: os.stat_result | None = None) -> None:
    """Remove one directory tree using directory descriptors and no symlink following."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    child_fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        child_stat = os.fstat(child_fd)
        if not stat.S_ISDIR(child_stat.st_mode):
            raise TransientPathError(f"transient child is not a directory: {name}")
        if expected is not None and (child_stat.st_dev, child_stat.st_ino) != (expected.st_dev, expected.st_ino):
            raise TransientPathError(f"transient root identity changed: {name}")
        for entry in os.scandir(child_fd):
            if entry.is_symlink():
                os.unlink(entry.name, dir_fd=child_fd)
            elif entry.is_dir(follow_symlinks=False):
                _remove_tree_at(child_fd, entry.name)
            else:
                os.unlink(entry.name, dir_fd=child_fd)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _remove_exact_root(root: Path) -> None:
    """Remove the validated root without following a replacement or descendant symlink."""

    parent = root.parent
    parent_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        expected = root.lstat()
        if not stat.S_ISDIR(expected.st_mode) or stat.S_ISLNK(expected.st_mode):
            raise TransientPathError(f"transient root is unsafe: {root}")
        _remove_tree_at(parent_fd, root.name, expected=expected)
    finally:
        os.close(parent_fd)


def create_run_root(
    *,
    namespace: str = NAMESPACE_TEST,
    owner_role: str,
    retention_class: str = RETENTION_DISPOSABLE,
    temporary_base: Path | None = None,
    identity: HostIdentity | None = None,
    clock: float | None = None,
) -> TransientRunRoot:
    """Allocate a private run root and publish its identity before children exist."""

    clean_namespace = str(namespace or "").strip()
    if clean_namespace not in NAMESPACES:
        raise TransientPathError(f"unknown transient namespace: {namespace!r}")
    role = str(owner_role or "").strip()
    if not role:
        raise TransientPathError("transient owner role is required")
    retention = str(retention_class or "").strip()
    if retention not in RETENTION_CLASSES:
        raise TransientPathError(f"unknown transient retention class: {retention_class!r}")
    owner = identity or current_host_identity()
    base = Path(temporary_base) if temporary_base is not None else Path(tempfile.gettempdir())
    try:
        base_info = base.lstat()
    except OSError as error:
        raise TransientPathError(f"cannot inspect transient base: {base}") from error
    if not stat.S_ISDIR(base_info.st_mode) or stat.S_ISLNK(base_info.st_mode):
        raise TransientPathError(f"transient base is unsafe: {base}")
    nonce = uuid.uuid4().hex[:4]
    root = base / f"{NAMESPACE_ROOT_NAMES[clean_namespace]}-{owner.pid}-{os.geteuid()}-{nonce}"
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        raise TransientPathError(f"transient root name collision: {root}") from None
    try:
        root.chmod(0o700)
        root_info = root.lstat()
        manifest = {
            "schema": 1,
            "namespace": clean_namespace,
            "owner_uid": os.geteuid(),
            "root_device": root_info.st_dev,
            "root_inode": root_info.st_ino,
            "owner_role": role,
            "retention_class": retention,
            "created_at": time.time() if clock is None else float(clock),
            "owner": owner.process_record_fields(instance_nonce=nonce),
        }
        _write_manifest(root / MANIFEST_NAME, manifest)
    except Exception:
        try:
            _remove_exact_root(root)
        except OSError:
            pass
        raise
    return TransientRunRoot(path=root, manifest=manifest, identity=owner)


def _read_manifest(root: Path) -> dict[str, Any]:
    try:
        value = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TransientPathError(f"cannot read transient manifest: {root}") from error
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise TransientPathError(f"malformed transient manifest: {root}")
    return value


def validate_run_root(root: Path) -> dict[str, Any]:
    """Validate one manifest-backed root without changing it."""

    try:
        info = root.lstat()
    except OSError as error:
        raise TransientPathError(f"cannot inspect transient root: {root}") from error
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid():
        raise TransientPathError(f"transient root is unsafe: {root}")
    manifest = _read_manifest(root)
    if (manifest.get("root_device"), manifest.get("root_inode")) != (info.st_dev, info.st_ino):
        raise TransientPathError(f"transient root identity changed: {root}")
    if manifest.get("namespace") not in NAMESPACES:
        raise TransientPathError(f"unknown transient namespace: {manifest.get('namespace')!r}")
    if manifest.get("owner_uid") != os.geteuid():
        raise TransientPathError(f"transient root belongs to another user: {root}")
    retention = manifest.get("retention_class")
    if retention not in RETENTION_CLASSES:
        raise TransientPathError(f"unknown transient retention class: {retention!r}")
    owner = manifest.get("owner")
    required_owner_fields = {
        "stable_host_id", "hostname", "boot_id", "pid", "process_start_identity",
        "process_start_ticks", "instance_nonce",
    }
    if not isinstance(owner, dict) or not required_owner_fields.issubset(owner):
        raise TransientPathError(f"transient owner is missing: {root}")
    if (
        any(not isinstance(owner[field], str) or not owner[field].strip() for field in ("stable_host_id", "hostname", "boot_id", "process_start_identity", "instance_nonce"))
        or isinstance(owner["pid"], bool) or not isinstance(owner["pid"], int) or owner["pid"] <= 1
        or isinstance(owner["process_start_ticks"], bool)
        or not isinstance(owner["process_start_ticks"], int) or owner["process_start_ticks"] <= 0
    ):
        raise TransientPathError(f"malformed transient owner: {root}")
    if not isinstance(manifest.get("owner_role"), str) or not manifest["owner_role"].strip():
        raise TransientPathError(f"malformed transient owner role: {root}")
    if not isinstance(manifest.get("created_at"), (int, float)) or isinstance(manifest["created_at"], bool):
        raise TransientPathError(f"malformed transient creation time: {root}")
    return manifest


def _validate_child_claims(root: Path, *, identity: HostIdentity) -> None:
    claims = root / "claims"
    try:
        claims_info = claims.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise TransientPathError(f"cannot inspect transient child claims: {root}") from error
    if not stat.S_ISDIR(claims_info.st_mode) or stat.S_ISLNK(claims_info.st_mode) or claims_info.st_uid != os.geteuid():
        raise TransientPathError(f"transient claims directory is unsafe: {claims}")
    try:
        entries = tuple(claims.iterdir())
    except OSError as error:
        raise TransientPathError(f"cannot inspect transient child claims: {root}") from error
    for claim in entries:
        try:
            payload = json.loads(claim.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema") != 1:
                raise ValueError("claim is not an object")
            diagnostic = is_current_local_process(payload, host_identity=identity)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise TransientPathError(f"malformed transient child claim: {claim}") from error
        if diagnostic.current or not diagnostic.may_remove_stale_record:
            raise TransientPathError(
                f"transient child claim is active or ambiguous: {claim} ({diagnostic.reason.value})"
            )


def remove_run_root(
    run_root: TransientRunRoot,
    *,
    identity: HostIdentity | None = None,
) -> None:
    """Remove exactly one currently-owned disposable root after identity validation."""

    manifest = validate_run_root(run_root.path)
    if manifest["retention_class"] != RETENTION_DISPOSABLE:
        raise TransientPathError("retained transient roots cannot be removed by disposable cleanup")
    owner = identity or run_root.identity
    record = manifest["owner"]
    if record != owner.process_record_fields(instance_nonce=record["instance_nonce"]):
        raise TransientPathError(f"transient root owner does not match: {run_root.path}")
    _validate_child_claims(run_root.path, identity=owner)
    try:
        _remove_exact_root(run_root.path)
    except OSError as error:
        raise TransientPathError(f"failed to remove transient root: {run_root.path}") from error


def recover_stale_run_root(
    root: Path,
    *,
    identity: HostIdentity | None = None,
) -> None:
    """Remove a disposable root only when its recorded owner is proven gone."""

    manifest = validate_run_root(Path(root))
    if manifest["retention_class"] != RETENTION_DISPOSABLE:
        raise TransientPathError("only disposable transient roots are recoverable")
    current = identity or current_host_identity()
    owner = manifest["owner"]
    if owner["stable_host_id"] != current.stable_host_id or owner["boot_id"] != current.boot_id:
        raise TransientPathError(f"transient root belongs to another host or boot: {root}")
    diagnostic = is_current_local_process(owner, host_identity=current)
    if diagnostic.current or not diagnostic.may_remove_stale_record:
        raise TransientPathError(
            f"transient root owner identity is not proven gone: {root} ({diagnostic.reason.value})"
        )
    _validate_child_claims(Path(root), identity=current)
    try:
        _remove_exact_root(Path(root))
    except OSError as error:
        raise TransientPathError(f"failed to recover transient root: {root}") from error
