# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Process-local scheduling for one already-leased YOLOmux instance."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .host_identity import current_host_identity

LOGGER = logging.getLogger(__name__)


@contextmanager
def background_scheduler_admission(app: Any, role: str):
    """Fence a worker's scheduler check, publication, and start as one transition."""

    scheduler = app.__dict__.get("background_scheduler")
    lock = getattr(scheduler, "lock", None)
    can_run = getattr(app, "scheduler_can_run", None)
    if not callable(can_run):
        can_run = getattr(scheduler, "can_run", None)
    if lock is None or not callable(can_run):
        yield True
        return
    with lock:
        yield bool(can_run(role))


BACKGROUND_REFRESH_COALESCE_SECONDS = 5.0
BACKGROUND_ROLE_TABBER_ACTIVITY = "tabber-activity"
BACKGROUND_ROLE_SESSION_FILES = "session-files"
BACKGROUND_ROLE_SEARCH_INDEX = "search-index"
BACKGROUND_ROLE_STATS_SAMPLER = "stats-sampler"
BACKGROUND_ROLE_WATCH_ROOTS = "watch-roots"
BACKGROUND_ROLES = (
    BACKGROUND_ROLE_TABBER_ACTIVITY,
    BACKGROUND_ROLE_SESSION_FILES,
    BACKGROUND_ROLE_SEARCH_INDEX,
    BACKGROUND_ROLE_STATS_SAMPLER,
    BACKGROUND_ROLE_WATCH_ROOTS,
)


@dataclass(frozen=True)
class BackgroundRoleState:
    role: str
    available: bool
    status: str
    refresh_requests: int = 0
    fallback_count: int = 0
    last_error: str = ""


def pid_is_alive(pid: int) -> bool:
    """Return basic liveness for local-service record readers."""

    if int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class BackgroundScheduler:
    """Schedule background work locally for one already-leased server."""

    def __init__(
        self,
        *,
        port: int | None = None,
        project_root: str | None = None,
        roles: tuple[str, ...] = BACKGROUND_ROLES,
        monotonic: Any = time.monotonic,
    ) -> None:
        identity = current_host_identity()
        self.roles = tuple(dict.fromkeys(roles))
        self.port = port
        self.project_root = str(project_root or Path.cwd())
        self.host_identity = identity
        self.pid = identity.pid
        self.started_at_ns = time.time_ns()
        self.instance_nonce = identity.instance_nonce
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self._work_queue: queue.Queue[tuple[str, Callable[[], None]]] = queue.Queue()
        self._work_keys: set[str] = set()
        self._work_thread: threading.Thread | None = None
        self._external_work_count = 0
        self._external_work_changed = threading.Condition(self.lock)
        self.lifecycle = "not_started"
        self.last_error = ""
        self.refresh_requests = {role: 0 for role in self.roles}
        self.fallback_counts = {role: 0 for role in self.roles}
        self.recent_refresh_requests: dict[str, float] = {}
        self.counters: dict[str, int] = {
            "avoided_recomputes": 0,
            "coalesced_refresh_requests": 0,
            "refresh_requests": 0,
            "search_index_bytes_written": 0,
        }

    def process_payload(self) -> dict[str, Any]:
        return {
            **self.host_identity.process_record_fields(
                pid=self.pid,
                start_identity=self.host_identity.process_start_identity,
                instance_nonce=self.instance_nonce,
            ),
            "port": self.port,
            "project_root": self.project_root,
            "started_at_ns": self.started_at_ns,
            "scope": "local",
        }

    def start(self, *, port: int | None = None, project_root: str | None = None) -> bool:
        """Arm this instance's scheduler once, after its product-root lease is held."""

        with self.lock:
            if self.lifecycle != "not_started":
                return False
            if port is not None:
                self.port = port
            if project_root is not None:
                self.project_root = str(project_root)
            self.lifecycle = "running"
            self._work_thread = threading.Thread(
                target=self._run_work,
                name="yolomux-background-scheduler",
                daemon=True,
            )
            try:
                self._work_thread.start()
            except BaseException:
                # Thread.start() can fail before the new thread exists (for example, when the
                # runtime refuses another native thread). Roll back the publication so callers
                # can report startup failure without leaving a scheduler that claims to run.
                self._work_thread = None
                self.stop_event.clear()
                self.lifecycle = "not_started"
                raise
            return True

    def begin_stop(self) -> None:
        """Close admission before the application starts stopping its specialized workers."""

        with self.lock:
            if self.lifecycle in {"stopping", "stopped"}:
                return
            # Keep already-admitted external work alive until it returns, while making the
            # stopping transition visible to every new admission.
            self.lifecycle = "stopping"
            self.stop_event.set()

    def finish_stop(self) -> None:
        """Join all admitted scheduler work before publishing the stopped state.

        The caller must not hold ``self.lock`` while calling this method: a task may need the
        scheduler lock to publish its completion.  This deliberately waits for admitted work
        instead of returning a timed-out ``stopped`` state; callers release product leases only
        after this method returns.
        """

        with self.lock:
            if self.lifecycle == "stopped":
                return
            if self.lifecycle == "running":
                self.lifecycle = "stopping"
                self.stop_event.set()
            worker = self._work_thread
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        with self._external_work_changed:
            while self._external_work_count:
                self._external_work_changed.wait()
            while True:
                try:
                    queued_key, _queued_work = self._work_queue.get_nowait()
                except queue.Empty:
                    break
                self._work_keys.discard(queued_key)
                self._work_queue.task_done()
            if self._work_thread is worker and worker is not None and not worker.is_alive():
                self._work_thread = None
        with self.lock:
            self.lifecycle = "stopped"

    def stop(self) -> None:
        """Fence admission and finish scheduler-owned teardown."""

        self.begin_stop()
        self.finish_stop()

    @contextmanager
    def external_work_admission(self, role: str, *, allow_not_started: bool = False):
        """Fence specialized workers that must run outside the scheduler queue."""

        with self._external_work_changed:
            lifecycle_allowed = self.lifecycle == "running" or (
                allow_not_started and self.lifecycle == "not_started"
            )
            admitted = lifecycle_allowed and role in self.roles
            if admitted:
                self._external_work_count += 1
        try:
            yield admitted
        finally:
            if admitted:
                with self._external_work_changed:
                    self._external_work_count -= 1
                    self._external_work_changed.notify_all()

    def submit_work(self, key: str, work: Callable[[], None]) -> dict[str, bool]:
        """Queue one coalesced advisory task without running it on the request thread."""

        normalized_key = str(key)
        with self.lock:
            if self.lifecycle != "running":
                return {"queued": False, "coalesced": False}
            if normalized_key in self._work_keys:
                return {"queued": True, "coalesced": True}
            self._work_keys.add(normalized_key)
            self._work_queue.put((normalized_key, work))
            return {"queued": True, "coalesced": False}

    def _run_work(self) -> None:
        while not self.stop_event.is_set():
            try:
                key, work = self._work_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if not self.stop_event.is_set():
                    work()
            except Exception as error:
                with self.lock:
                    self.last_error = f"{type(error).__name__}: {error}"
                LOGGER.exception("background scheduler task failed")
            finally:
                with self.lock:
                    self._work_keys.discard(key)
                self._work_queue.task_done()

    def can_run(self, role: str) -> bool:
        with self.lock:
            return role in self.roles and self.lifecycle == "running"

    def role_state(self, role: str) -> BackgroundRoleState:
        available = self.can_run(role)
        with self.lock:
            status = "local" if self.lifecycle == "running" else self.lifecycle
            return BackgroundRoleState(
                role=role,
                available=available,
                status=status,
                refresh_requests=self.refresh_requests.get(role, 0),
                fallback_count=self.fallback_counts.get(role, 0),
                last_error=self.last_error,
            )

    def refresh_request_key(self, role: str, payload: dict[str, Any] | None = None) -> str:
        source = payload if isinstance(payload, dict) else {}
        cache_key = source.get("cache_key")
        if cache_key in (None, "") and isinstance(source.get("payload"), dict):
            cache_key = source["payload"].get("cache_key")
        if cache_key not in (None, ""):
            source = {"cache_key": str(cache_key)}
        else:
            source = {
                str(key): value
                for key, value in source.items()
                if str(key) not in {"action", "reason", "requester", "role", "trigger"}
            }
        try:
            encoded = json.dumps(source, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            encoded = repr(source)
        return f"{role}\0{encoded}"

    def _prune_refresh_requests_locked(self, now: float) -> None:
        self.recent_refresh_requests = {
            existing: expiry
            for existing, expiry in self.recent_refresh_requests.items()
            if expiry > now
        }

    def refresh_is_pending(self, role: str, payload: dict[str, Any] | None = None) -> bool:
        now = self.monotonic()
        key = self.refresh_request_key(role, payload)
        with self.lock:
            self._prune_refresh_requests_locked(now)
            return self.recent_refresh_requests.get(key, 0.0) > now

    def _accept_refresh_locked(
        self,
        role: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_stopping: bool = False,
    ) -> dict[str, Any]:
        """Record a refresh while the scheduler lifecycle lock is held."""

        now = self.monotonic()
        key = self.refresh_request_key(role, payload)
        if self.lifecycle != "running" and not (allow_stopping and self.lifecycle == "stopping"):
            return {
                "accepted": False,
                "coalesced": False,
                "local": False,
                "fallback": True,
                "error": f"background scheduler {self.lifecycle.replace('_', ' ')}",
            }
        if role not in self.roles:
            return {
                "accepted": False,
                "coalesced": False,
                "local": False,
                "fallback": True,
                "error": "unknown background role",
            }
        self._prune_refresh_requests_locked(now)
        if self.recent_refresh_requests.get(key, 0.0) > now:
            self.counters["coalesced_refresh_requests"] += 1
            return {"accepted": True, "coalesced": True}
        self.recent_refresh_requests[key] = now + BACKGROUND_REFRESH_COALESCE_SECONDS
        self.refresh_requests[role] = self.refresh_requests.get(role, 0) + 1
        self.counters["refresh_requests"] += 1
        return {"accepted": True, "coalesced": False}

    def accept_refresh(
        self,
        role: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_stopping: bool = False,
    ) -> dict[str, Any]:
        """Record a refresh only after its downstream worker accepted the work."""

        with self.lock:
            return self._accept_refresh_locked(role, payload, allow_stopping=allow_stopping)

    def refresh_queue_payload(self) -> dict[str, Any]:
        now = self.monotonic()
        with self.lock:
            self._prune_refresh_requests_locked(now)
            by_role: dict[str, int] = {}
            remaining = 0.0
            for key, expiry in self.recent_refresh_requests.items():
                role, _, _ = key.partition("\0")
                by_role[role] = by_role.get(role, 0) + 1
                value = max(0.0, expiry - now)
                remaining = value if not remaining else min(remaining, value)
        return {
            "coalesce_window_seconds": BACKGROUND_REFRESH_COALESCE_SECONDS,
            "recent_pending_count": sum(by_role.values()),
            "recent_pending_by_role": by_role,
            "next_expires_seconds": round(remaining, 3),
        }

    def request_refresh(
        self,
        role: str,
        payload: dict[str, Any] | None = None,
        *,
        defer_acceptance: bool = False,
    ) -> dict[str, Any]:
        with self.lock:
            if self.lifecycle != "running":
                return {
                    "ok": False,
                    "accepted": False,
                    "role": role,
                    "local": False,
                    "error": f"background scheduler {self.lifecycle.replace('_', ' ')}",
                    "fallback": True,
                }
            if role not in self.roles:
                return {"ok": False, "accepted": False, "role": role, "error": "unknown background role", "fallback": True}
            if self.refresh_is_pending(role, payload):
                self.counters["coalesced_refresh_requests"] += 1
                return {
                    "ok": True,
                    "accepted": True,
                    "role": role,
                    "local": True,
                    "fallback": False,
                    "already_pending": True,
                    "coalesced": True,
                }
            if defer_acceptance:
                return {
                    "ok": True,
                    "accepted": False,
                    "role": role,
                    "local": True,
                    "fallback": False,
                    "deferred_acceptance": True,
                }
            acceptance = self._accept_refresh_locked(role, payload)
        if not acceptance["accepted"]:
            return {
                "ok": False,
                "accepted": False,
                "role": role,
                "local": False,
                "fallback": True,
                "error": acceptance.get("error", "background scheduler unavailable"),
            }
        if acceptance["coalesced"]:
            return {
                "ok": True,
                "accepted": True,
                "role": role,
                "local": True,
                "fallback": False,
                "already_pending": True,
                "coalesced": True,
            }
        return {"ok": True, "accepted": True, "role": role, "local": True, "fallback": False}

    def record_fallback(self, role: str) -> None:
        with self.lock:
            self.fallback_counts[role] = self.fallback_counts.get(role, 0) + 1

    def record_search_index_bytes_written(self, byte_count: int) -> None:
        with self.lock:
            self.counters["search_index_bytes_written"] += max(0, int(byte_count))

    def status_payload(self) -> dict[str, Any]:
        process = self.process_payload()
        with self.lock:
            roles = {role: self.role_state(role).__dict__ for role in self.roles}
            status = "local" if self.lifecycle == "running" else self.lifecycle
            return {
                "status": status,
                "process": process,
                "roles": roles,
                "counters": dict(self.counters),
                "refresh_queue": self.refresh_queue_payload(),
                "work_queue": {"pending": len(self._work_keys)},
                "last_transition": status,
                "last_transition_details": {},
                "last_error": self.last_error,
                "search_index": {
                    "role": BACKGROUND_ROLE_SEARCH_INDEX,
                    "mode": "indexing-server",
                    "process": process,
                    "status": status,
                },
            }
