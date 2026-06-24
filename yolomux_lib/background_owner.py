# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Single-owner coordination for expensive background work shared by YOLOmux processes."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable

from .atomic_file import atomic_write_text
from .atomic_file import file_lock
from .common import STATE_DIR
from .control import send_yolomux_control_request


BACKGROUND_OWNER_DIR = STATE_DIR / "background-owner"
BACKGROUND_OWNER_STALE_SECONDS = 10.0
BACKGROUND_OWNER_HEARTBEAT_SECONDS = 1.0
BACKGROUND_RELEASE_TIMEOUT_SECONDS = 2.0
BACKGROUND_REFRESH_TIMEOUT_SECONDS = 0.25
BACKGROUND_OWNER_UNRESPONSIVE_SECONDS = 3.0
BACKGROUND_REFRESH_COALESCE_SECONDS = 5.0
BACKGROUND_ROLE_TABBER_ACTIVITY = "tabber-activity"
BACKGROUND_ROLE_SESSION_FILES = "session-files"
BACKGROUND_ROLE_SEARCH_INDEX = "search-index"
BACKGROUND_ROLE_WATCH_ROOTS = "watch-roots"
BACKGROUND_ROLES = (
    BACKGROUND_ROLE_TABBER_ACTIVITY,
    BACKGROUND_ROLE_SESSION_FILES,
    BACKGROUND_ROLE_SEARCH_INDEX,
    BACKGROUND_ROLE_WATCH_ROOTS,
)


@dataclass(frozen=True)
class BackgroundRoleState:
    role: str
    owner: bool
    status: str
    refresh_requests: int = 0
    fallback_count: int = 0
    last_error: str = ""


def _generation_sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
    try:
        started_at_ns = int(record.get("started_at_ns") or 0)
    except (TypeError, ValueError):
        started_at_ns = 0
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return started_at_ns, pid, str(record.get("nonce") or "")


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class BackgroundOwnerRegistry:
    def __init__(
        self,
        *,
        control_socket: str = "",
        port: int | None = None,
        project_root: str | None = None,
        owner_dir: Path = BACKGROUND_OWNER_DIR,
        roles: tuple[str, ...] = BACKGROUND_ROLES,
        on_demote: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        pid: int | None = None,
        hostname: str | None = None,
    ):
        self.owner_dir = owner_dir
        self.generations_dir = owner_dir / "generations"
        self.owner_path = owner_dir / "owner.json"
        self.owner_lock_path = owner_dir / "owner.lock"
        self.roles = tuple(dict.fromkeys(roles))
        self.control_socket = control_socket
        self.port = port
        self.project_root = str(project_root or Path.cwd())
        self.clock = clock
        self.monotonic = monotonic
        self.pid = os.getpid() if pid is None else int(pid)
        self.hostname = hostname or socket.gethostname()
        self.started_at_ns = time.time_ns()
        self.nonce = uuid.uuid4().hex
        self.generation_id = f"{self.started_at_ns}-{self.pid}-{self.nonce[:12]}"
        self.record_path = self.generations_dir / f"{self.generation_id}.json"
        self.on_demote = on_demote
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.owner_lock_fd: int | None = None
        self.owner = False
        self.status = "new"
        self.last_error = ""
        self.refresh_requests: dict[str, int] = {role: 0 for role in self.roles}
        self.fallback_counts: dict[str, int] = {role: 0 for role in self.roles}
        self.recent_refresh_requests: dict[str, float] = {}
        self.counters: dict[str, int] = {
            "avoided_recomputes": 0,
            "coalesced_refresh_requests": 0,
            "follower_stale_reads": 0,
            "owner_acquired": 0,
            "owner_released": 0,
            "owner_refresh_requests": 0,
            "search_index_bytes_written": 0,
            "takeover_failed": 0,
            "takeover_success": 0,
        }
        self.last_transition = "new"
        self.last_transition_details: dict[str, Any] = {}

    def owner_payload(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "hostname": self.hostname,
            "port": self.port,
            "project_root": self.project_root,
            "control_socket": self.control_socket,
            "generation_id": self.generation_id,
            "started_at_ns": self.started_at_ns,
            "nonce": self.nonce,
        }

    def generation_record(self) -> dict[str, Any]:
        return {
            **self.owner_payload(),
            "roles": list(self.roles),
            "last_heartbeat": self.clock(),
            "owner": self.owner,
            "status": self.status,
            "counters": dict(self.counters),
        }

    def publish_generation(self) -> None:
        self.generations_dir.mkdir(parents=True, exist_ok=True)
        self.generations_dir.chmod(0o700)
        atomic_write_text(self.record_path, json.dumps(self.generation_record(), sort_keys=True) + "\n", mode=0o600)

    def read_generation_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            paths = sorted(self.generations_dir.glob("*.json"))
        except OSError:
            return records
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def live_generation_records(self) -> list[dict[str, Any]]:
        now = self.clock()
        records = []
        for record in self.read_generation_records():
            try:
                pid = int(record.get("pid") or 0)
                heartbeat = float(record.get("last_heartbeat") or 0.0)
            except (TypeError, ValueError):
                continue
            if not pid_is_alive(pid):
                continue
            if heartbeat and now - heartbeat > BACKGROUND_OWNER_STALE_SECONDS:
                continue
            records.append(record)
        return records

    def latest_live_generation(self) -> dict[str, Any] | None:
        records = self.live_generation_records()
        if not records:
            return None
        return max(records, key=_generation_sort_key)

    def is_latest_live_generation(self) -> bool:
        latest = self.latest_live_generation()
        return latest is None or latest.get("generation_id") == self.generation_id

    def read_owner_record(self) -> dict[str, Any] | None:
        try:
            record = json.loads(self.owner_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return record if isinstance(record, dict) else None

    def write_owner_record(self) -> None:
        payload = {
            **self.owner_payload(),
            "roles": list(self.roles),
            "last_heartbeat": self.clock(),
            "status": "owner",
            "role_status": {role: self.role_state(role).__dict__ for role in self.roles},
            "counters": dict(self.counters),
        }
        atomic_write_text(self.owner_path, json.dumps(payload, sort_keys=True) + "\n", mode=0o600)

    def acquire_owner_lock(self) -> bool:
        self.owner_dir.mkdir(parents=True, exist_ok=True)
        self.owner_dir.chmod(0o700)
        fd = os.open(str(self.owner_lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.owner_lock_fd = fd
        return True

    def release_owner_lock(self) -> None:
        fd = self.owner_lock_fd
        self.owner_lock_fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def request_current_owner_release(self) -> dict[str, Any]:
        current = self.read_owner_record()
        if not isinstance(current, dict):
            return {"ok": False, "error": "no owner record"}
        if current.get("generation_id") == self.generation_id:
            return {"ok": True, "message": "already owner"}
        request = {
            "action": "background_release_owner",
            "requester": self.owner_payload(),
        }
        return send_yolomux_control_request(current, request, timeout=BACKGROUND_RELEASE_TIMEOUT_SECONDS)

    def mark_owner_acquired(self, transition: str, owner_record: dict[str, Any] | None = None) -> None:
        self.owner = True
        self.status = "owner"
        self.last_error = ""
        self.last_transition = transition
        self.last_transition_details = {"previous_owner": owner_record or {}}
        self.counters["owner_acquired"] = self.counters.get("owner_acquired", 0) + 1
        if transition == "takeover":
            self.counters["takeover_success"] = self.counters.get("takeover_success", 0) + 1
        self.write_owner_record()
        self.publish_generation()

    def attempt_takeover(self) -> bool:
        with self.lock:
            self.publish_generation()
            if not self.is_latest_live_generation():
                self.status = "follower"
                if self.owner:
                    self.release_owner("newer_generation")
                return False
            if self.owner:
                self.status = "owner"
                self.write_owner_record()
                return True
            owner_record = self.read_owner_record()
            if self.acquire_owner_lock():
                transition = "takeover" if owner_record and owner_record.get("generation_id") != self.generation_id else "acquired"
                self.mark_owner_acquired(transition, owner_record)
                return True
            release = self.request_current_owner_release()
            if release.get("ok") and self.acquire_owner_lock():
                self.mark_owner_acquired("takeover", owner_record)
                return True
            self.status = "blocked_by_unreachable_owner"
            self.last_error = str(release.get("error") or "owner lock is held")
            self.last_transition = "blocked"
            self.last_transition_details = {"owner": owner_record or {}, "release": release}
            self.counters["takeover_failed"] = self.counters.get("takeover_failed", 0) + 1
            self.publish_generation()
            return False

    def release_owner(self, reason: str = "release") -> None:
        demote = False
        with self.lock:
            if self.owner:
                demote = True
            self.owner = False
            self.status = "follower"
            self.last_transition = "released"
            self.last_transition_details = {"reason": reason}
            if demote:
                self.counters["owner_released"] = self.counters.get("owner_released", 0) + 1
            self.release_owner_lock()
            self.publish_generation()
        if demote and self.on_demote is not None:
            self.on_demote()

    def heartbeat_once(self) -> None:
        with self.lock:
            self.publish_generation()
            if self.owner:
                if not self.is_latest_live_generation():
                    self.release_owner("newer_generation")
                    return
                self.write_owner_record()
            elif not self.is_latest_live_generation():
                self.status = "follower"
                self.publish_generation()
            elif self.is_latest_live_generation():
                self.attempt_takeover()

    def start(self) -> bool:
        self.publish_generation()
        acquired = self.attempt_takeover()
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name="background-owner", daemon=True)
            self.thread.start()
        return acquired

    def stop(self) -> None:
        self.stop_event.set()
        self.release_owner("stop")
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        try:
            self.record_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def run(self) -> None:
        while not self.stop_event.wait(BACKGROUND_OWNER_HEARTBEAT_SECONDS):
            self.heartbeat_once()

    def is_owner(self) -> bool:
        with self.lock:
            return bool(self.owner)

    def can_run(self, role: str) -> bool:
        with self.lock:
            return bool(self.owner and role in self.roles)

    def role_state(self, role: str) -> BackgroundRoleState:
        owner = self.can_run(role)
        status = "owner" if owner else self.status
        return BackgroundRoleState(
            role=role,
            owner=owner,
            status=status,
            refresh_requests=self.refresh_requests.get(role, 0),
            fallback_count=self.fallback_counts.get(role, 0),
            last_error=self.last_error,
        )

    def record_refresh_request(self, role: str) -> None:
        with self.lock:
            self.refresh_requests[role] = self.refresh_requests.get(role, 0) + 1
            self.counters["owner_refresh_requests"] = self.counters.get("owner_refresh_requests", 0) + 1

    def refresh_request_key(self, role: str, payload: dict[str, Any] | None = None) -> str:
        try:
            payload_key = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            payload_key = repr(payload or {})
        return f"{role}\0{payload_key}"

    def coalesce_refresh_request(self, role: str, payload: dict[str, Any] | None = None) -> bool:
        now = self.monotonic()
        key = self.refresh_request_key(role, payload)
        with self.lock:
            self.recent_refresh_requests = {
                existing_key: expires_at
                for existing_key, expires_at in self.recent_refresh_requests.items()
                if expires_at > now
            }
            if self.recent_refresh_requests.get(key, 0.0) > now:
                self.counters["coalesced_refresh_requests"] = self.counters.get("coalesced_refresh_requests", 0) + 1
                return True
            self.recent_refresh_requests[key] = now + BACKGROUND_REFRESH_COALESCE_SECONDS
        return False

    def record_fallback(self, role: str) -> None:
        with self.lock:
            self.fallback_counts[role] = self.fallback_counts.get(role, 0) + 1

    def record_avoided_recompute(self, role: str) -> None:
        with self.lock:
            self.counters["avoided_recomputes"] = self.counters.get("avoided_recomputes", 0) + 1

    def record_follower_stale_read(self, role: str) -> None:
        with self.lock:
            self.counters["follower_stale_reads"] = self.counters.get("follower_stale_reads", 0) + 1

    def record_search_index_bytes_written(self, byte_count: int) -> None:
        with self.lock:
            self.counters["search_index_bytes_written"] = self.counters.get("search_index_bytes_written", 0) + max(0, int(byte_count))

    def owner_unresponsive_reason(self, owner_record: dict[str, Any] | None = None) -> str:
        record = owner_record if isinstance(owner_record, dict) else self.read_owner_record()
        if not isinstance(record, dict):
            return "missing_owner_record"
        try:
            heartbeat = float(record.get("last_heartbeat") or 0.0)
        except (TypeError, ValueError):
            heartbeat = 0.0
        if heartbeat and self.clock() - heartbeat > BACKGROUND_OWNER_UNRESPONSIVE_SECONDS:
            return "stale_owner_heartbeat"
        return ""

    def request_owner_refresh(self, role: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.can_run(role):
            if self.coalesce_refresh_request(role, payload):
                return {"ok": True, "accepted": True, "role": role, "local_owner": True, "fallback": False, "already_pending": True, "coalesced": True}
            self.record_refresh_request(role)
            return {"ok": True, "accepted": True, "role": role, "local_owner": True, "fallback": False}
        current = self.read_owner_record()
        reason = self.owner_unresponsive_reason(current)
        if reason:
            self.last_error = reason
            return {"ok": False, "accepted": False, "role": role, "error": reason, "fallback": True}
        if self.coalesce_refresh_request(role, payload):
            return {"ok": True, "accepted": True, "role": role, "fallback": False, "already_pending": True, "coalesced": True}
        self.record_refresh_request(role)
        request = {
            "action": "background_refresh",
            "role": role,
            "payload": payload or {},
            "requester": self.owner_payload(),
        }
        response = send_yolomux_control_request(current, request, timeout=BACKGROUND_REFRESH_TIMEOUT_SECONDS)
        accepted = bool(response.get("ok") and response.get("accepted"))
        if accepted:
            return {"ok": True, "accepted": True, "role": role, "response": response, "fallback": False}
        error = str(response.get("error") or "owner refresh request was not accepted")
        self.last_error = error
        return {"ok": False, "accepted": False, "role": role, "error": error, "response": response, "fallback": True}

    def status_payload(self) -> dict[str, Any]:
        latest = self.latest_live_generation()
        owner_record = self.read_owner_record()
        with self.lock:
            return {
                "owner": self.owner,
                "status": self.status,
                "generation": self.owner_payload(),
                "latest_generation": latest,
                "current_owner": owner_record,
                "roles": {role: self.role_state(role).__dict__ for role in self.roles},
                "counters": dict(self.counters),
                "last_transition": self.last_transition,
                "last_transition_details": dict(self.last_transition_details),
                "last_error": self.last_error,
            }


class DisabledBackgroundOwner:
    def owner_payload(self) -> dict[str, Any]:
        return {}

    def live_generation_records(self) -> list[dict[str, Any]]:
        return []

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        return None

    def is_owner(self) -> bool:
        return True

    def can_run(self, role: str) -> bool:
        return True

    def release_owner(self, reason: str = "release") -> None:
        return None

    def record_refresh_request(self, role: str) -> None:
        return None

    def record_fallback(self, role: str) -> None:
        return None

    def record_avoided_recompute(self, role: str) -> None:
        return None

    def record_follower_stale_read(self, role: str) -> None:
        return None

    def record_search_index_bytes_written(self, byte_count: int) -> None:
        return None

    def request_owner_refresh(self, role: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "accepted": True, "role": role, "local_owner": True, "fallback": False}

    def status_payload(self) -> dict[str, Any]:
        return {"owner": True, "status": "disabled", "roles": {}}


def locked_background_owner_state() -> Any:
    return file_lock(BACKGROUND_OWNER_DIR / "state.json", dir_mode=0o700)


def read_background_owner_debug_status(owner_dir: Path = BACKGROUND_OWNER_DIR) -> dict[str, Any]:
    owner_path = owner_dir / "owner.json"
    generations_dir = owner_dir / "generations"
    try:
        owner_record = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        owner_record = None
    generations: list[dict[str, Any]] = []
    try:
        generation_paths = sorted(generations_dir.glob("*.json"))
    except OSError:
        generation_paths = []
    for path in generation_paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            generations.append(record)
    return {
        "owner_dir": str(owner_dir),
        "current_owner": owner_record if isinstance(owner_record, dict) else None,
        "generations": generations,
    }
