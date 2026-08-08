# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Lease-driven filesystem watcher and reconciliation daemon."""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from watchfiles import DefaultFilter as WatchfilesDefaultFilter
    from watchfiles import watch as watchfiles_watch
except ImportError:
    WatchfilesDefaultFilter = None
    watchfiles_watch = None

from . import filesystem
from .filesystem.exclusions import ExclusionVerdict
from .filesystem.exclusions import path_exclusion_verdict
from .background_owner import pid_is_alive
from .host_identity import process_start_identity
from .infra import common
from .local_services.rpc import safe_socket_path
from .local_services.runtime import LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT
from .local_services.runtime import LocalRpcServiceState
from .local_services.runtime import acquire_client_lease
from .local_services.runtime import apply_service_process_priority
from .local_services.runtime import reap_dead_client_leases
from .local_services.runtime import release_client_lease
from .local_services.runtime import run_local_rpc_service
from .watchd_protocol import EffectiveWatchConfiguration
from .watchd_protocol import WATCHD_CODE_REVISION
from .watchd_protocol import WATCHD_DESCRIPTOR_TTL_SECONDS
from .watchd_protocol import WATCHD_MAX_CHANGED_PATHS
from .watchd_protocol import WATCHD_PROTOCOL_VERSION
from .watchd_protocol import WATCHD_SERVICE_NAME
from .watchd_protocol import WATCHD_SNAPSHOT_DEADLINE_SECONDS
from .watchd_protocol import WatchDescriptor
from .watchd_protocol import WatchProtocolError
from .watchd_protocol import validate_descriptor
from .watchd_protocol import validate_request
from .watch_diff import payload_from_products


WATCHD_DEFAULT_IDLE_SECONDS = 60.0
WATCHD_CONCURRENT_HANDLER_LIMIT = LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT
WATCHD_HISTORY_LIMIT = 64
WATCHD_RECONCILE_SECONDS = 300.0
WATCHD_RETRY_SECONDS = 10.0
WATCHD_POLL_SECONDS = 1.0
WATCHD_EVENT_BATCH_LIMIT = 64
WATCHD_SIGNATURE_CHILD_LIMIT = 512
WATCHD_DEBOUNCE_MS = 250
WATCHD_STEP_MS = 50
WATCHD_RUST_TIMEOUT_MS = 1_000
WATCHD_SNAPSHOT_QUEUE_LIMIT = 64
WATCHD_SNAPSHOT_RETENTION_SECONDS = 120.0
# Registering a recursive native watch is one uninterruptible call that holds
# the interpreter lock for its whole duration, so no handler thread in this
# process can answer while it runs. Measured on a 63-root ~/dev configuration it
# blocks every other thread for 3.4 s, which is longer than a long poll's whole
# transport deadline. These bound the declared window around it.
WATCHD_NATIVE_BUILD_QUIESCE_SECONDS = 1.0
WATCHD_NATIVE_BUILD_HANDOFF_SECONDS = 0.05
WATCHD_NATIVE_BUILD_RETRY_SECONDS = 0.25
WATCHD_OPERATION_ERRORS = (OSError, RuntimeError, ValueError, filesystem.FilesystemError)


def default_socket_path() -> Path:
    return safe_socket_path(common.RUNTIME_DIR / "services" / "watchd.sock", prefix="yolomux-watchd")


def compact_watch_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    candidates = sorted(set(paths), key=lambda item: (len(Path(item).parts), item))
    compacted: list[str] = []
    for candidate in candidates:
        path = Path(candidate)
        if any(path == Path(parent) or filesystem._path_is_within(path, Path(parent)) for parent in compacted):
            continue
        compacted.append(candidate)
    return tuple(compacted)


def effective_configuration(descriptors: list[WatchDescriptor]) -> EffectiveWatchConfiguration:
    def union(field: str) -> tuple[str, ...]:
        return tuple(sorted({item for descriptor in descriptors for item in getattr(descriptor, field)}))

    settings_paths = tuple(sorted({descriptor.settings_path for descriptor in descriptors}))
    attention_paths = tuple(sorted({descriptor.attention_path for descriptor in descriptors}))
    roots = union("roots")
    files = union("files")
    background_files = union("background_files")
    transcripts = union("transcripts")
    indexed_dirs = union("indexed_dirs")
    watch_paths = compact_watch_paths((
        *roots,
        *indexed_dirs,
        *(str(Path(path).parent) for path in (*files, *background_files, *transcripts, *settings_paths, *attention_paths)),
    ))
    return EffectiveWatchConfiguration(
        roots=roots,
        files=files,
        background_files=background_files,
        transcripts=transcripts,
        repo_roots=union("repo_roots"),
        indexed_dirs=indexed_dirs,
        skip_dirs=union("skip_dirs"),
        settings_paths=settings_paths,
        attention_paths=attention_paths,
        configured_roots=union("configured_roots"),
        watch_paths=watch_paths,
    )


class PersistentWatchService:
    """Own one runtime namespace's descriptors, native watcher, and revisions."""

    def __init__(self, socket_path: Path, idle_seconds: float = WATCHD_DEFAULT_IDLE_SECONDS):
        state = LocalRpcServiceState(socket_path, prefix="yolomux-watchd", idle_seconds=idle_seconds)
        self.socket_path = state.socket_path
        self.lock_path = state.lock_path
        self.stop_event = state.stop_event
        self.idle_seconds = state.idle_seconds
        self.started_at = state.started_at
        self.last_client_at = state.last_client_at
        self.lock = threading.Condition(threading.RLock())
        self.leases: dict[str, dict[str, object]] = {}
        self.descriptors: dict[tuple[str, str], WatchDescriptor] = {}
        self.epoch = uuid.uuid4().hex
        self.source_epoch = self.epoch
        self.revision = 0
        self.watch_generation = 0
        self.scanned_watch_generation = 0
        self.active_watch_generation = 0
        self.configuration = EffectiveWatchConfiguration()
        self.configuration_hash = self._configuration_hash(self.configuration)
        self.revisions: list[dict[str, Any]] = []
        self.root_signatures: dict[str, tuple[Any, ...]] = {}
        self.root_generations: dict[str, int] = {}
        self.repo_generations: dict[str, int] = {}
        self.native_worker: threading.Thread | Any | None = None
        self.native_stop_event = threading.Event()
        self.reconfigure_event = threading.Event()
        self.native_healthy = False
        self.polling_fallback = False
        self.native_build_active = False
        self.long_poll_waiters = 0
        self.last_error = ""
        self.next_reconcile_at = 0.0
        self.snapshot_requests: dict[str, dict[str, Any]] = {}
        self.snapshot_keys: dict[str, str] = {}
        self.snapshot_queue: deque[str] = deque()
        self.snapshot_worker: threading.Thread | Any | None = None
        self.snapshot_stop_event = threading.Event()

    @staticmethod
    def _configuration_hash(configuration: EffectiveWatchConfiguration) -> str:
        encoded = json.dumps(configuration.stable_payload(), separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _compact_signature(signature: tuple[Any, ...]) -> tuple[Any, ...]:
        encoded = json.dumps(signature, default=str, separators=(",", ":")).encode("utf-8")
        return (str(signature[0]) if signature else "", "digest", hashlib.sha256(encoded).hexdigest()[:24], len(encoded), ())

    def _dead_lease_ids(self) -> tuple[str, ...] | None:
        """Probe recorded client liveness off the lock, or refuse when contended.

        Liveness is process-identity I/O.  The listener thread runs this between
        accepts, so it must neither read under the condition nor wait for it.
        """
        if not self.lock.acquire(blocking=False):
            return None
        try:
            candidates = dict(self.leases)
        finally:
            self.lock.release()
        survivors = dict(candidates)
        reap_dead_client_leases(
            survivors,
            start_identity_reader=process_start_identity,
            pid_probe=pid_is_alive,
        )
        return tuple(lease_id for lease_id in candidates if lease_id not in survivors)

    def _reap_locked(self, dead_lease_ids: tuple[str, ...] = ()) -> bool:
        # A lease id is minted once per acquisition, so dropping an id measured
        # dead can never drop a lease acquired since that measurement.
        dead = {lease_id for lease_id in dead_lease_ids if self.leases.pop(lease_id, None) is not None}
        now = time.monotonic()
        expired = {
            key
            for key, descriptor in self.descriptors.items()
            if key[0] in dead or key[0] not in self.leases or descriptor.expires_at <= now
        }
        for key in expired:
            self.descriptors.pop(key, None)
        return bool(dead or expired)

    def effective_configuration(self) -> EffectiveWatchConfiguration:
        with self.lock:
            return self.configuration

    def _refresh_configuration_locked(self) -> bool:
        configuration = effective_configuration(list(self.descriptors.values()))
        signature = self._configuration_hash(configuration)
        if signature == self.configuration_hash:
            return False
        self.configuration = configuration
        self.configuration_hash = signature
        self.watch_generation += 1
        self.native_healthy = False
        self.reconfigure_event.set()
        self.native_stop_event.set()
        self.lock.notify_all()
        return True

    def _native_build_payload_locked(self) -> dict[str, object]:
        """Answer one long poll with the declared blocking window, not a stall.

        The caller asked for a revision this daemon cannot produce until the
        native registration finishes.  Returning a typed outcome names the
        reason and the remaining window, so the client can re-arm against a
        deadline that covers it instead of reading an unexplained timeout.
        """
        return {
            "ok": True,
            "state": "reconfiguring",
            "error_code": "native_watch_rebuild",
            "retry_after_seconds": WATCHD_NATIVE_BUILD_RETRY_SECONDS,
            "epoch": self.epoch,
            "current_revision": self.revision,
            "watch_generation": self.watch_generation,
            "active_watch_generation": self.active_watch_generation,
            "changed": False,
            "reset": False,
            "revision": {},
        }

    def _begin_native_build(self) -> None:
        """Declare and drain before a registration that blocks every handler."""
        with self.lock:
            self.native_build_active = True
            self.lock.notify_all()
            deadline = time.monotonic() + WATCHD_NATIVE_BUILD_QUIESCE_SECONDS
            while self.long_poll_waiters and not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.lock.wait(remaining)
        # Waking a waiter only schedules it.  Its response still has to reach the
        # socket before this thread takes the interpreter lock for the build, and
        # the drained counter cannot observe that write, which the listener owns.
        time.sleep(WATCHD_NATIVE_BUILD_HANDOFF_SECONDS)

    def _end_native_build(self) -> None:
        with self.lock:
            self.native_build_active = False
            self.lock.notify_all()

    def _declared_native_changes(self, watch_iterator: Any) -> Any:
        """Yield native batches, declaring the blocking first registration.

        ``watchfiles.watch`` is a generator: nothing is registered until it is
        advanced, so the blocking window is the first advance, not construction.
        """
        self._begin_native_build()
        try:
            first = next(watch_iterator, None)
        finally:
            self._end_native_build()
        if first is None:
            return
        yield first
        yield from watch_iterator

    def _release_locked(self, lease_id: str) -> dict[str, object]:
        response = release_client_lease(self.leases, lease_id)
        for key in [key for key in self.descriptors if key[0] == lease_id]:
            self.descriptors.pop(key, None)
        self._refresh_configuration_locked()
        return response

    def _snapshot_payload_locked(self) -> dict[str, Any]:
        latest = dict(self.revisions[-1]) if self.revisions else {
            "epoch": self.epoch,
            "revision": 0,
            "watch_generation": self.watch_generation,
            "active_watch_generation": self.active_watch_generation,
            "kind": "snapshot",
            "token": f"{self.epoch}:0",
            "roots": list(self.configuration.roots),
            "changed_paths": [],
            "repo_generations": dict(self.repo_generations),
            "root_generations": dict(self.root_generations),
            "healthy": self.native_healthy,
            "fallback": self.polling_fallback,
        }
        return latest

    @staticmethod
    def _directory_signature_paths(configuration: EffectiveWatchConfiguration) -> set[str]:
        return set((*configuration.roots, *configuration.indexed_dirs))

    @staticmethod
    def _scan_signature(path: str, directory_paths: set[str]) -> tuple[Any, ...]:
        """Measure one path the single way every watchd scan measures it."""
        return filesystem.watch_signature(
            path,
            child_limit=WATCHD_SIGNATURE_CHILD_LIMIT if path in directory_paths else 0,
        )

    def _reset_watched_files_locked(self) -> tuple[str, ...]:
        configuration = self.configuration
        watched_files = tuple(dict.fromkeys((*configuration.files, *configuration.background_files)))
        return watched_files[:WATCHD_MAX_CHANGED_PATHS]

    def _scan_file_signatures(self, paths: tuple[str, ...], directory_paths: set[str]) -> dict[str, tuple[Any, ...]]:
        """Read exact on-disk signatures without holding the service condition."""
        signatures: dict[str, tuple[Any, ...]] = {}
        for path in paths:
            try:
                signatures[path] = self._scan_signature(path, directory_paths)
            except WATCHD_OPERATION_ERRORS:
                # The only signature kinds the browser accepts are file, dir, and missing.
                signatures[path] = (path, "missing", 0, 0)
        return signatures

    def _reset_payload_locked(self, file_signatures: dict[str, tuple[Any, ...]]) -> dict[str, Any]:
        """Compose recovery from current service state, never from the last event."""
        configuration = self.configuration
        changed_paths = list(dict.fromkeys((
            *configuration.roots,
            *configuration.repo_roots,
            *configuration.indexed_dirs,
            *configuration.configured_roots,
            *configuration.files,
            *configuration.background_files,
            *configuration.transcripts,
            *configuration.settings_paths,
            *configuration.attention_paths,
        )))
        watched_files = self._reset_watched_files_locked()
        for path in watched_files:
            signature = file_signatures.get(path)
            if signature is not None:
                self.root_signatures[path] = signature
        return {
            "epoch": self.epoch,
            "revision": self.revision,
            "watch_generation": self.watch_generation,
            "active_watch_generation": self.active_watch_generation,
            "kind": "full",
            "token": f"{self.epoch}:{self.revision}",
            "roots": list(configuration.roots),
            "changed_paths": changed_paths[:WATCHD_MAX_CHANGED_PATHS],
            "files_changed": [
                {"path": path, "signature": file_signatures[path]}
                for path in watched_files
                if path in file_signatures
            ],
            "settings_changed": bool(configuration.settings_paths),
            "attention_changed": bool(configuration.attention_paths),
            "transcripts_changed": bool(configuration.transcripts),
            "coarse": True,
            "repo_generations": dict(self.repo_generations),
            "root_generations": dict(self.root_generations),
            "healthy": self.native_healthy,
            "fallback": self.polling_fallback,
            "created_at": time.time(),
        }

    def _reset_payload(self) -> dict[str, Any]:
        """Scan every watched file outside the lock, then compose under it."""
        signatures: dict[str, tuple[Any, ...]] = {}
        while True:
            with self.lock:
                pending = tuple(
                    path for path in self._reset_watched_files_locked() if path not in signatures
                )
                if not pending:
                    return self._reset_payload_locked(signatures)
                directory_paths = self._directory_signature_paths(self.configuration)
            # Reconfiguration during the scan only ever adds paths, so accumulating
            # signatures terminates and never rescans a path already measured.
            signatures.update(self._scan_file_signatures(pending, directory_paths))

    def _retained_revision_after_locked(self, after_revision: int) -> dict[str, Any] | None:
        expected_revision = after_revision + 1
        for revision in self.revisions:
            if int(revision.get("revision") or 0) == expected_revision:
                return dict(revision)
        return None

    def publish_revision(
        self,
        *,
        kind: str,
        changed_paths: list[str],
        coarse: bool = False,
        settings_changed: bool = False,
        attention_changed: bool = False,
        transcripts_changed: bool = False,
        files_changed: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            bounded_files_changed = (files_changed or [])[:WATCHD_MAX_CHANGED_PATHS]
            for item in bounded_files_changed:
                path = item.get("path") if isinstance(item, dict) else None
                signature = item.get("signature") if isinstance(item, dict) else None
                if isinstance(path, str) and isinstance(signature, (list, tuple)):
                    self.root_signatures[path] = tuple(signature)
            self.revision += 1
            revision = {
                "epoch": self.epoch,
                "revision": self.revision,
                "watch_generation": self.watch_generation,
                "active_watch_generation": self.active_watch_generation,
                "kind": kind,
                "token": f"{self.epoch}:{self.revision}",
                "roots": list(self.configuration.roots),
                "changed_paths": changed_paths[:WATCHD_MAX_CHANGED_PATHS],
                "files_changed": bounded_files_changed,
                "settings_changed": settings_changed,
                "attention_changed": attention_changed,
                "transcripts_changed": transcripts_changed,
                "coarse": bool(coarse),
                "repo_generations": dict(self.repo_generations),
                "root_generations": dict(self.root_generations),
                "healthy": self.native_healthy,
                "fallback": self.polling_fallback,
                "created_at": time.time(),
            }
            self.revisions.append(revision)
            self.revisions = self.revisions[-WATCHD_HISTORY_LIMIT:]
            self.lock.notify_all()
            return dict(revision)

    def _path_verdict(
        self,
        path: Path,
        configuration: EffectiveWatchConfiguration,
        *,
        resolved: Path | None = None,
    ) -> ExclusionVerdict:
        """Decide one path through the single shared exclusion owner.

        ``resolved`` is threaded through by callers that already resolved the
        path.  This runs once per native change, and resolving twice would
        double the syscalls on the daemon's hottest path.
        """

        return path_exclusion_verdict(
            path,
            skip_dirs=configuration.skip_dirs,
            configured_roots=configuration.configured_roots,
            resolved=resolved,
        )

    def _path_allowed(self, path: Path, configuration: EffectiveWatchConfiguration) -> bool:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except OSError:
            return False
        if self._path_verdict(path, configuration, resolved=resolved).excluded:
            return False
        return any(
            resolved == Path(root) or filesystem._path_is_within(resolved, Path(root))
            for root in configuration.watch_paths
        )

    def native_watch_filter(self, configuration: EffectiveWatchConfiguration):
        default_filter = WatchfilesDefaultFilter()

        def watch_filter(change: Any, raw_path: str) -> bool:
            # Every ignored directory is decided by the one exclusion owner, not
            # only Git internals: watchfiles' own default filter does not know
            # this deployment's configured skip_dirs or excluded paths, so a
            # cache, virtualenv, dependency tree or build output would otherwise
            # be admitted here and only rejected later, if at all.
            if self._path_verdict(Path(raw_path), configuration).excluded:
                return False
            return bool(default_filter(change, raw_path))

        return watch_filter

    @staticmethod
    def _generation_bump_targets(
        candidates: tuple[str, ...],
        changed_paths: list[Path],
        *,
        project_descendants: bool,
    ) -> list[str]:
        """Match changed paths against one generation set the single way watchd matches them."""
        return [
            candidate
            for candidate in candidates
            if any(
                path == Path(candidate)
                or filesystem._path_is_within(path, Path(candidate))
                or (project_descendants and filesystem._path_is_within(Path(candidate), path))
                for path in changed_paths
            )
        ]

    def _generation_bumps(
        self,
        configuration: EffectiveWatchConfiguration,
        changed_paths: list[Path],
        *,
        project_descendants: bool = False,
    ) -> tuple[list[str], list[str]]:
        """Resolve which generations a change set bumps without holding the lock.

        Path matching is unbounded work over roots x changed paths.  A watch
        generation owns one immutable configuration, so the caller's captured
        configuration is exactly the one the apply step re-verifies under the
        lock, and no waiter or listener queues behind this scan.
        """
        return (
            self._generation_bump_targets(configuration.roots, changed_paths, project_descendants=project_descendants),
            self._generation_bump_targets(configuration.repo_roots, changed_paths, project_descendants=project_descendants),
        )

    def _apply_generation_bumps_locked(self, bumps: tuple[list[str], list[str]]) -> None:
        """Advance matched generations atomically with the revision that reports them."""
        roots, repos = bumps
        for root in roots:
            self.root_generations[root] = self.root_generations.get(root, 0) + 1
        for repo in repos:
            self.repo_generations[repo] = self.repo_generations.get(repo, 0) + 1

    def admit_native_changes(self, changes: set[tuple[Any, str]], *, watch_generation: int) -> dict[str, Any] | None:
        with self.lock:
            if watch_generation != self.watch_generation:
                return None
            configuration = self.configuration
        if len(changes) > WATCHD_EVENT_BATCH_LIMIT:
            return self.reconcile(reason="overflow", watch_generation=watch_generation, coarse=True)
        admitted: list[Path] = []
        for _change, raw_path in changes:
            if not isinstance(raw_path, str) or not raw_path.startswith("/"):
                continue
            path = Path(raw_path)
            if self._path_allowed(path, configuration):
                admitted.append(path.resolve(strict=False))
        changed_paths = sorted(set(admitted), key=str)
        if not changed_paths:
            return None
        settings_changed = any(str(path) in configuration.settings_paths for path in changed_paths)
        attention_changed = any(str(path) in configuration.attention_paths for path in changed_paths)
        transcripts_changed = any(str(path) in configuration.transcripts for path in changed_paths)
        watched_files = set((*configuration.files, *configuration.background_files))
        files_changed: list[dict[str, Any]] = []
        for watched_file in sorted(watched_files):
            watched_path = Path(watched_file)
            if not any(
                path == watched_path
                or filesystem._path_is_within(path, watched_path)
                or filesystem._path_is_within(watched_path, path)
                for path in changed_paths
            ):
                continue
            try:
                signature = filesystem.watch_signature(watched_file)
            except (OSError, ValueError, filesystem.FilesystemError):
                signature = (watched_file, "missing", 0, 0)
            files_changed.append({"path": watched_file, "signature": signature})
        bumps = self._generation_bumps(configuration, changed_paths)
        with self.lock:
            if watch_generation != self.watch_generation:
                return None
            self._apply_generation_bumps_locked(bumps)
            return self.publish_revision(
                kind="delta",
                changed_paths=[str(path) for path in changed_paths],
                settings_changed=settings_changed,
                attention_changed=attention_changed,
                transcripts_changed=transcripts_changed,
                files_changed=files_changed,
            )

    def reconcile(self, *, reason: str, watch_generation: int, coarse: bool = False) -> dict[str, Any] | None:
        with self.lock:
            if watch_generation != self.watch_generation:
                return None
            configuration = self.configuration
            previous = dict(self.root_signatures)
        directory_paths = self._directory_signature_paths(configuration)
        explicit_paths = set((
            *configuration.files,
            *configuration.background_files,
            *configuration.settings_paths,
            *configuration.attention_paths,
            *configuration.transcripts,
        ))
        signatures: dict[str, tuple[Any, ...]] = {}
        for path in sorted(directory_paths | explicit_paths):
            signatures[path] = self._scan_signature(path, directory_paths)
        changed = sorted(path for path in signatures if previous.get(path) != signatures.get(path))
        removed = sorted(set(previous) - set(signatures))
        changed_paths = changed + removed
        bumps = self._generation_bumps(
            configuration,
            [Path(path) for path in changed_paths],
            project_descendants=True,
        )
        with self.lock:
            if watch_generation != self.watch_generation:
                return None
            self.scanned_watch_generation = watch_generation
            self.root_signatures = signatures
            self._apply_generation_bumps_locked(bumps)
            self.next_reconcile_at = time.monotonic() + WATCHD_RECONCILE_SECONDS
            if not changed_paths and self.revision:
                return None
            watched_files = set((*configuration.files, *configuration.background_files))
            files_changed = [
                {
                    "path": path,
                    "signature": signatures.get(path, (path, "missing")),
                }
                for path in sorted(watched_files.intersection(changed_paths))
            ]
            return self.publish_revision(
                kind="full",
                changed_paths=changed_paths,
                coarse=coarse,
                settings_changed=bool(set(configuration.settings_paths).intersection(changed_paths)),
                attention_changed=bool(set(configuration.attention_paths).intersection(changed_paths)),
                transcripts_changed=bool(set(configuration.transcripts).intersection(changed_paths)),
                files_changed=files_changed,
            )

    def _snapshot_plan_locked(self, since: str, force_full: bool) -> tuple[dict[str, Any], list[str]]:
        current = self._snapshot_payload_locked()
        current_token = str(current.get("token") or f"{self.epoch}:{self.revision}")
        current_roots = list(self.configuration.roots)
        if force_full:
            return {"mode": "full", "reason": "forced", "token": current_token, "since": since, "removed_roots": []}, current_roots
        if since == current_token:
            return {
                "mode": "none",
                "token": current_token,
                "since": since,
                "directories": [],
                "removed_roots": [],
                "change_summary": {"roots_changed": 0, "roots_added": 0, "roots_removed": 0},
            }, []
        prior = next((item for item in reversed(self.revisions) if str(item.get("token") or "") == since), None)
        if prior is None:
            return {"mode": "full", "reason": "stale-since", "token": current_token, "since": since, "removed_roots": []}, current_roots
        prior_roots = {str(root) for root in prior.get("roots", []) if isinstance(root, str)}
        roots = set(current_roots)
        prior_revision = int(prior.get("revision") or 0)
        changed_paths = {
            str(path)
            for revision in self.revisions
            if int(revision.get("revision") or 0) > prior_revision
            for path in revision.get("changed_paths", [])
            if isinstance(path, str)
        }
        changed_roots = sorted(
            root
            for root in roots
            if root not in prior_roots or any(
                Path(path) == Path(root) or filesystem._path_is_within(Path(path), Path(root))
                for path in changed_paths
            )
        )
        removed_roots = sorted(prior_roots - roots)
        return {
            "mode": "diff",
            "token": current_token,
            "since": since,
            "removed_roots": removed_roots,
            "change_summary": {
                "roots_changed": len(changed_roots) + len(removed_roots),
                "roots_added": len(roots - prior_roots),
                "roots_removed": len(removed_roots),
            },
        }, changed_roots

    def _produce_snapshot(
        self,
        since: str,
        force_full: bool,
        *,
        expected_generation: int,
        expected_revision: int,
        deadline_at: float,
    ) -> tuple[dict[str, object], bytes]:
        """Build one retained watch-diff product outside every RPC handler."""
        with self.lock:
            base_payload, roots = self._snapshot_plan_locked(since, force_full)
            if expected_generation != self.watch_generation:
                return {"ok": False, "state": "failed", "status": 502, "error": "watch state changed before snapshot production", "error_code": "producer_failed"}, b""
        if roots:
            batch_payload = {
                "requests": [
                    {
                        "id": index,
                        "type": "list",
                        "path": root,
                        "include_watch_signature": True,
                        "trigger": "watch-diff",
                    }
                    for index, root in enumerate(roots)
                ],
                "trigger": "watch-diff",
                "client_scope": "browser",
                "client_revision": str(base_payload.get("token") or "watchd")[:80],
            }
            product = filesystem.filesystem_batch_result(batch_payload)
            if time.monotonic() >= deadline_at:
                return {"ok": False, "state": "failed", "status": 504, "error": "watchd snapshot deadline expired", "error_code": "deadline_expired"}, b""
            payload = payload_from_products(base_payload, roots, [product])
        else:
            payload = base_payload
        with self.lock:
            if expected_generation != self.watch_generation:
                return {"ok": False, "state": "failed", "status": 502, "error": "watch state changed during snapshot production", "error_code": "producer_failed"}, b""
            # Native revisions may advance while a bounded listing is in flight.
            # Keep the captured token/revision as the product fence; the later
            # revision remains queued for the client's next diff.
            revision = expected_revision
            token = str(payload.get("token") or f"{self.epoch}:{revision}")
            payload["token"] = token
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        metadata: dict[str, object] = {
            "ok": True,
            "state": "ready",
            "status": 200,
            "source_epoch": self.source_epoch,
            "daemon_epoch": self.epoch,
            "revision": revision,
            "token": token,
            "product": common.inline_json_product_metadata(body),
        }
        return metadata, body

    def _snapshot_key_locked(self, since: str, force_full: bool) -> str:
        encoded = json.dumps(
            [self.source_epoch, self.watch_generation, self.revision, since, force_full],
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _prune_snapshot_requests_locked(self) -> None:
        cutoff = time.monotonic() - WATCHD_SNAPSHOT_RETENTION_SECONDS
        stale = [
            producer_id
            for producer_id, record in self.snapshot_requests.items()
            if str(record.get("state") or "") in {"ready", "failed"}
            and float(record.get("completed_at") or 0.0) < cutoff
        ]
        for producer_id in stale:
            record = self.snapshot_requests.pop(producer_id, None)
            key = str(record.get("key") or "") if isinstance(record, dict) else ""
            if self.snapshot_keys.get(key) == producer_id:
                self.snapshot_keys.pop(key, None)

    @staticmethod
    def _snapshot_accepted_metadata(record: dict[str, Any]) -> dict[str, object]:
        return {
            "ok": True,
            "state": "accepted",
            "status": 202,
            "producer_id": str(record["producer_id"]),
            "source_epoch": str(record["source_epoch"]),
            "daemon_epoch": str(record["daemon_epoch"]),
            "watch_generation": int(record["watch_generation"]),
            "revision": int(record["revision"]),
            "deadline_at": float(record["deadline_wall"]),
        }

    def snapshot(self, since: str, force_full: bool) -> tuple[dict[str, object], bytes]:
        """Return one retained product or confirm bounded cold-work acceptance."""
        with self.lock:
            self._prune_snapshot_requests_locked()
            key = self._snapshot_key_locked(since, force_full)
            existing_id = self.snapshot_keys.get(key)
            existing = self.snapshot_requests.get(existing_id or "")
            if existing is not None:
                state = str(existing.get("state") or "")
                if state == "ready":
                    return dict(existing["metadata"]), bytes(existing["body"])
                if state in {"accepted", "running"}:
                    return self._snapshot_accepted_metadata(existing), b""
                self.snapshot_keys.pop(key, None)
            if len(self.snapshot_queue) >= WATCHD_SNAPSHOT_QUEUE_LIMIT:
                return {"ok": False, "state": "failed", "status": 503, "error": "watchd snapshot queue is full", "error_code": "service_unavailable"}, b""
            producer_id = f"watch-{uuid.uuid4().hex}"
            accepted_at = time.monotonic()
            record = {
                "producer_id": producer_id,
                "key": key,
                "state": "accepted",
                "since": since,
                "force_full": force_full,
                "watch_generation": self.watch_generation,
                "revision": self.revision,
                "source_epoch": self.source_epoch,
                "daemon_epoch": self.epoch,
                "deadline_at": accepted_at + WATCHD_SNAPSHOT_DEADLINE_SECONDS,
                "deadline_wall": time.time() + WATCHD_SNAPSHOT_DEADLINE_SECONDS,
            }
            self.snapshot_requests[producer_id] = record
            self.snapshot_keys[key] = producer_id
            self.snapshot_queue.append(producer_id)
            self.lock.notify_all()
        self.start_snapshot_worker()
        return self._snapshot_accepted_metadata(record), b""

    def snapshot_product(self, producer_id: str, timeout: float) -> tuple[dict[str, object], bytes]:
        deadline = time.monotonic() + timeout
        with self.lock:
            record = self.snapshot_requests.get(producer_id)
            if record is None:
                return {"ok": False, "state": "failed", "status": 503, "error": "watchd snapshot product is unavailable", "error_code": "service_unavailable"}, b""
            while str(record.get("state") or "") in {"accepted", "running"} and not self.stop_event.is_set():
                remaining = min(deadline, float(record["deadline_at"])) - time.monotonic()
                if remaining <= 0:
                    break
                self.lock.wait(remaining)
            state = str(record.get("state") or "")
            if state == "ready":
                return dict(record["metadata"]), bytes(record["body"])
            if state == "failed":
                return dict(record["metadata"]), b""
            if time.monotonic() >= float(record["deadline_at"]):
                failure = {"ok": False, "state": "failed", "status": 504, "error": "watchd snapshot deadline expired", "error_code": "deadline_expired"}
                record["state"] = "failed"
                record["metadata"] = failure
                record["completed_at"] = time.monotonic()
                self.lock.notify_all()
                return failure, b""
            return {**self._snapshot_accepted_metadata(record), "state": "pending"}, b""

    def snapshot_producer_loop(self) -> None:
        worker = threading.current_thread()
        try:
            while not self.stop_event.is_set() and not self.snapshot_stop_event.is_set():
                with self.lock:
                    while not self.snapshot_queue and not self.stop_event.is_set() and not self.snapshot_stop_event.is_set():
                        self.lock.wait(1.0)
                    if self.stop_event.is_set() or self.snapshot_stop_event.is_set():
                        return
                    producer_id = self.snapshot_queue.popleft()
                    record = self.snapshot_requests.get(producer_id)
                    if record is None or str(record.get("state") or "") != "accepted":
                        continue
                    record["state"] = "running"
                    request = dict(record)
                try:
                    metadata, body = self._produce_snapshot(
                        str(request["since"]),
                        bool(request["force_full"]),
                        expected_generation=int(request["watch_generation"]),
                        expected_revision=int(request["revision"]),
                        deadline_at=float(request["deadline_at"]),
                    )
                except WATCHD_OPERATION_ERRORS as error:
                    metadata = {"ok": False, "state": "failed", "status": 502, "error": str(error)[:256] or "watchd snapshot failed", "error_code": "producer_failed"}
                    body = b""
                with self.lock:
                    current = self.snapshot_requests.get(producer_id)
                    if current is None or str(current.get("state") or "") == "failed":
                        continue
                    current["state"] = "ready" if metadata.get("ok") is True else "failed"
                    current["metadata"] = dict(metadata)
                    current["body"] = bytes(body)
                    current["completed_at"] = time.monotonic()
                    self.lock.notify_all()
        finally:
            with self.lock:
                if self.snapshot_worker is worker:
                    self.snapshot_worker = None

    def start_snapshot_worker(self) -> None:
        with self.lock:
            if self.snapshot_worker is not None and self.snapshot_worker.is_alive():
                return
            worker = threading.Thread(target=self.snapshot_producer_loop, name="watchd-snapshot-producer", daemon=True)
            self.snapshot_worker = worker
        worker.start()

    def shutdown_snapshot_worker(self) -> None:
        self.snapshot_stop_event.set()
        with self.lock:
            self.lock.notify_all()
            worker = self.snapshot_worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=None)

    def shutdown_workers(self) -> None:
        self.shutdown_watcher()
        self.shutdown_snapshot_worker()

    def _activate_watch_generation(
        self,
        generation: int,
        *,
        native_healthy: bool,
        polling_fallback: bool,
        error: BaseException | None = None,
    ) -> bool:
        """Publish one atomic completion barrier for every watch backend."""
        with self.lock:
            if generation != self.watch_generation or self.scanned_watch_generation != generation:
                return False
            publish_activation = (
                self.active_watch_generation != generation
                or self.native_healthy != native_healthy
                or self.polling_fallback != polling_fallback
            )
            self.native_healthy = native_healthy
            self.polling_fallback = polling_fallback
            self.active_watch_generation = generation
            if error is not None:
                self.last_error = str(error)[:256]
            if publish_activation:
                # A later reconcile may find identical signatures and publish
                # nothing, so publish the exact generation barrier atomically.
                self.publish_revision(kind="state", changed_paths=[])
        return True

    def _mark_watch_generation_unhealthy(self, generation: int, error: BaseException) -> bool:
        """Withdraw readiness after one exact generation's scan fails."""
        with self.lock:
            if generation != self.watch_generation:
                return False
            publish_failure = self.native_healthy or self.polling_fallback
            self.native_healthy = False
            self.polling_fallback = False
            if self.scanned_watch_generation == generation:
                self.scanned_watch_generation = 0
            self.last_error = str(error)[:256]
            if publish_failure:
                self.publish_revision(kind="state", changed_paths=[])
        return True

    def _poll_fallback_until_native_retry(
        self,
        generation: int,
        generation_stop_event: threading.Event,
        *,
        error: BaseException | None = None,
    ) -> bool:
        """Reconcile an advertised fallback until the native retry deadline."""
        retry_at = time.monotonic() + WATCHD_RETRY_SECONDS
        while True:
            with self.lock:
                if generation != self.watch_generation:
                    return False
            remaining = retry_at - time.monotonic()
            if remaining <= 0:
                return False
            wait_outcome = self._wait_watch_generation(
                generation,
                generation_stop_event,
                min(WATCHD_POLL_SECONDS, remaining),
            )
            if wait_outcome != "elapsed":
                return wait_outcome == "stopped"
            with self.lock:
                if generation != self.watch_generation:
                    return False
            try:
                self.reconcile(reason="fallback", watch_generation=generation)
            except WATCHD_OPERATION_ERRORS as scan_error:
                self._mark_watch_generation_unhealthy(generation, scan_error)
                return False
            if not self._activate_watch_generation(
                generation,
                native_healthy=False,
                polling_fallback=True,
                error=error,
            ):
                return False

    def _wait_watch_generation(
        self,
        generation: int,
        generation_stop_event: threading.Event,
        timeout: float,
    ) -> str:
        if not generation_stop_event.wait(timeout):
            return "elapsed"
        with self.lock:
            return "stopped" if generation == self.watch_generation else "reconfigured"

    def native_watch_loop(self) -> None:
        worker = threading.current_thread()
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    generation = self.watch_generation
                    configuration = self.configuration
                    self.native_stop_event = threading.Event()
                    stop_event = self.native_stop_event
                    self.reconfigure_event.clear()
                if not configuration.watch_paths:
                    with self.lock:
                        self.native_healthy = False
                        self.polling_fallback = False
                        self.lock.wait(1.0)
                    continue
                configuration_scan_error: BaseException | None = None
                try:
                    self.reconcile(reason="configuration", watch_generation=generation)
                except WATCHD_OPERATION_ERRORS as error:
                    configuration_scan_error = error
                    self._mark_watch_generation_unhealthy(generation, error)
                with self.lock:
                    if generation != self.watch_generation:
                        continue
                if watchfiles_watch is None:
                    self._activate_watch_generation(
                        generation,
                        native_healthy=False,
                        polling_fallback=True,
                    )
                    if self._poll_fallback_until_native_retry(generation, stop_event):
                        return
                    continue
                backend_error: BaseException | None = None
                scan_error = configuration_scan_error
                watch_iterator = None
                try:
                    with self.lock:
                        self.native_healthy = False
                        self.polling_fallback = False
                    watch_iterator = watchfiles_watch(
                        *configuration.watch_paths,
                        watch_filter=self.native_watch_filter(configuration),
                        debounce=WATCHD_DEBOUNCE_MS,
                        step=WATCHD_STEP_MS,
                        rust_timeout=WATCHD_RUST_TIMEOUT_MS,
                        yield_on_timeout=True,
                        stop_event=stop_event,
                        raise_interrupt=False,
                        ignore_permission_denied=True,
                    )
                    for changes in self._declared_native_changes(watch_iterator):
                        if self.stop_event.is_set() or stop_event.is_set():
                            break
                        if not self._activate_watch_generation(
                            generation,
                            native_healthy=True,
                            polling_fallback=False,
                        ):
                            break
                        if changes:
                            self.admit_native_changes(changes, watch_generation=generation)
                        if time.monotonic() >= self.next_reconcile_at:
                            try:
                                self.reconcile(reason="periodic", watch_generation=generation)
                            except WATCHD_OPERATION_ERRORS as error:
                                scan_error = error
                                break
                except WATCHD_OPERATION_ERRORS as error:
                    backend_error = error
                finally:
                    if watch_iterator is not None:
                        watch_iterator.close()
                with self.lock:
                    if generation != self.watch_generation:
                        continue
                if scan_error is not None:
                    self._mark_watch_generation_unhealthy(generation, scan_error)
                elif backend_error is not None:
                    self._activate_watch_generation(
                        generation,
                        native_healthy=False,
                        polling_fallback=True,
                        error=backend_error,
                    )
                    if self._poll_fallback_until_native_retry(generation, stop_event, error=backend_error):
                        return
                    continue
                else:
                    continue
                wait_outcome = self._wait_watch_generation(
                    generation,
                    stop_event,
                    WATCHD_RETRY_SECONDS,
                )
                if wait_outcome == "stopped":
                    return
                if wait_outcome == "reconfigured":
                    continue
                try:
                    self.reconcile(reason="fallback", watch_generation=generation)
                except WATCHD_OPERATION_ERRORS as error:
                    self._mark_watch_generation_unhealthy(generation, error)
                    continue
                self._activate_watch_generation(
                    generation,
                    native_healthy=False,
                    polling_fallback=True,
                    error=scan_error,
                )
        finally:
            with self.lock:
                if self.native_worker is worker:
                    self.native_worker = None
                self.native_healthy = False

    def start_watcher(self) -> None:
        with self.lock:
            if self.native_worker is not None and self.native_worker.is_alive():
                return
            worker = threading.Thread(target=self.native_watch_loop, name="watchd-native-filesystem", daemon=True)
            self.native_worker = worker
        worker.start()

    def shutdown_watcher(self) -> None:
        self.native_stop_event.set()
        self.reconfigure_event.set()
        with self.lock:
            self.lock.notify_all()
            worker = self.native_worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=None)

    def idle_due(self) -> bool:
        """Answer the listener's maintenance probe without ever waiting on the condition.

        The listener calls this between accepts.  Blocking here stops the daemon
        from accepting connections at all, so a contended probe defers to the
        next accept timeout instead of queueing behind a data-plane critical
        section.  Deferring only delays an idle shutdown; it never reports idle.
        """
        dead_lease_ids = self._dead_lease_ids()
        if dead_lease_ids is None or not self.lock.acquire(blocking=False):
            return False
        try:
            if self._reap_locked(dead_lease_ids):
                self._refresh_configuration_locked()
            return not self.leases and time.monotonic() - self.last_client_at >= self.idle_seconds
        finally:
            self.lock.release()

    def status(self) -> dict[str, object]:
        with self.lock:
            return {
                "ok": True,
                "service": WATCHD_SERVICE_NAME,
                "pid": os.getpid(),
                "version": WATCHD_PROTOCOL_VERSION,
                "code_revision": WATCHD_CODE_REVISION,
                "build_revision": 1,
                "socket": str(self.socket_path),
                "started_at": self.started_at,
                "epoch": self.epoch,
                "source_epoch": self.source_epoch,
                "revision": self.revision,
                "watch_generation": self.watch_generation,
                "active_watch_generation": self.active_watch_generation,
                "clients": len(self.leases),
                "descriptors": len(self.descriptors),
                "roots": len(self.configuration.roots),
                "healthy": self.native_healthy,
                "fallback": self.polling_fallback,
                "reconfiguring": self.native_build_active,
                "last_error": self.last_error,
                "snapshot_queue": len(self.snapshot_queue),
                "snapshot_products": len(self.snapshot_requests),
            }

    def handle(self, request: dict[str, Any], request_binary: bytes = b"") -> tuple[dict[str, object], bytes]:
        del request_binary
        self.last_client_at = time.monotonic()
        try:
            request = validate_request(request)
        except WatchProtocolError as error:
            return {"ok": False, "error": str(error), "required_protocol_version": WATCHD_PROTOCOL_VERSION}, b""
        action = str(request["action"])
        if action == "ping":
            return {"ok": True, "service": WATCHD_SERVICE_NAME, "pid": os.getpid(), "version": WATCHD_PROTOCOL_VERSION, "code_revision": WATCHD_CODE_REVISION, "build_revision": 1}, b""
        if action == "status":
            return self.status(), b""
        if action == "snapshot":
            return self.snapshot(str(request.get("since") or ""), bool(request.get("force_full")))
        if action == "snapshot_product":
            return self.snapshot_product(str(request["producer_id"]), float(request.get("timeout_seconds") or 0.0))
        if action == "lease":
            with self.lock:
                response = acquire_client_lease(
                    self.leases,
                    request.get("client_pid"),
                    request.get("lease_id"),
                    start_identity_reader=process_start_identity,
                    pid_probe=pid_is_alive,
                )
                self.lock.notify_all()
            return {**response, "version": WATCHD_PROTOCOL_VERSION, "epoch": self.epoch, "watch_generation": self.watch_generation, "active_watch_generation": self.active_watch_generation}, b""
        if action == "release":
            with self.lock:
                return self._release_locked(str(request.get("lease_id") or "")), b""
        if action == "upsert":
            descriptor = validate_descriptor(request.get("descriptor"))
            key = (str(request["lease_id"]), str(request["descriptor_id"]))
            with self.lock:
                if key[0] not in self.leases:
                    return {"ok": False, "error": "unknown lease", "error_code": "unknown_lease"}, b""
                previous = self.descriptors.get(key)
                if previous is not None and descriptor.descriptor_generation < previous.descriptor_generation:
                    return {"ok": False, "error": "stale descriptor generation", "error_code": "stale_generation", "watch_generation": self.watch_generation}, b""
                stable_unchanged = previous is not None and descriptor.stable_payload() == previous.stable_payload()
                self.descriptors[key] = descriptor
                changed = self._refresh_configuration_locked()
                return {"ok": True, "changed": changed, "descriptor_unchanged": stable_unchanged, "watch_generation": self.watch_generation, "active_watch_generation": self.active_watch_generation, "epoch": self.epoch}, b""
        if action == "remove":
            key = (str(request["lease_id"]), str(request["descriptor_id"]))
            with self.lock:
                removed = self.descriptors.pop(key, None) is not None
                changed = self._refresh_configuration_locked() if removed else False
                return {"ok": True, "removed": removed, "changed": changed, "watch_generation": self.watch_generation, "active_watch_generation": self.active_watch_generation}, b""
        if action == "wait_revision":
            after = int(request.get("after_revision") or 0)
            epoch = str(request.get("epoch") or "")
            timeout = float(request.get("timeout_seconds") or 0.0)
            deadline = time.monotonic() + timeout
            with self.lock:
                if self.native_build_active:
                    return self._native_build_payload_locked(), b""
                reset_reason = "epoch_changed" if epoch and epoch != self.epoch else ""
                self.long_poll_waiters += 1
                try:
                    while not reset_reason and not self.native_build_active and epoch == self.epoch and self.revision == after and timeout > 0 and not self.stop_event.is_set():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self.lock.wait(remaining)
                finally:
                    self.long_poll_waiters -= 1
                    self.lock.notify_all()
                if self.native_build_active:
                    return self._native_build_payload_locked(), b""
                payload: dict[str, Any] = {}
                current_revision = self.revision
                if not epoch:
                    payload = self._snapshot_payload_locked()
                elif not reset_reason and after > self.revision:
                    reset_reason = "cursor_ahead"
                elif not reset_reason and self.revision > after:
                    retained = self._retained_revision_after_locked(after)
                    if retained is None:
                        reset_reason = "history_expired"
                    else:
                        payload = retained
            if reset_reason:
                # Gap recovery owes the client exact signatures, and that scan must
                # never run under the condition unrelated handlers wait on.
                payload = self._reset_payload()
                current_revision = int(payload["revision"])
            return {
                "ok": True,
                "epoch": self.epoch,
                "current_revision": current_revision,
                "changed": bool(payload),
                "reset": bool(reset_reason),
                "reset_reason": reset_reason,
                "revision": payload,
            }, b""
        if action == "shutdown":
            self.stop_event.set()
            self.native_stop_event.set()
            self.snapshot_stop_event.set()
            with self.lock:
                self.lock.notify_all()
            return {"ok": True, "shutdown": True}, b""
        if action == "shutdown_if_idle":
            with self.lock:
                if self.leases:
                    return {"ok": True, "shutdown": False, "leases": len(self.leases)}, b""
            self.stop_event.set()
            self.native_stop_event.set()
            self.snapshot_stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        return {"ok": False, "error": "unknown watch action"}, b""

    def run(self) -> int:
        self.start_watcher()
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name=WATCHD_SERVICE_NAME,
            stop_event=self.stop_event,
            handle=self.handle,
            on_idle=self.idle_due,
            on_client=lambda: setattr(self, "last_client_at", time.monotonic()),
            on_shutdown=self.shutdown_workers,
            concurrent_handlers=WATCHD_CONCURRENT_HANDLER_LIMIT,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux shared filesystem watch service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--idle-seconds", type=float, default=WATCHD_DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    apply_service_process_priority()
    return PersistentWatchService(Path(args.socket), idle_seconds=args.idle_seconds).run()


if __name__ == "__main__":
    raise SystemExit(main())
