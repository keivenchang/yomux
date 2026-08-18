"""Shared owner for public session and agent-status snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .app import TmuxWebtermApp
from .polling_policy import quiet_poll_interval
from .tmux.sessions import discover_status_sessions
from .tmux.tmux_utils import cmd_error
from .tmux.tmux_utils import list_tmux_session_activity
from .tmux.tmux_utils import list_tmux_session_names
from .tmux.tmux_utils import tmux
from .local_services.runtime import LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT
from .local_services.runtime import acquire_client_lease
from .local_services.runtime import apply_service_process_priority
from .local_services.runtime import LocalRpcServiceState
from .local_services.runtime import reap_dead_client_leases
from .local_services.runtime import release_client_lease
from .local_services.runtime import run_local_rpc_service
from .local_services.command_router import CommonDaemonActions
from .local_services.command_router import LocalServiceCommandRouter
from .local_services.rpc import LOCAL_RPC_MAX_BINARY_BYTES
from .statusd_protocol import STATUSD_PROTOCOL_VERSION
from .statusd_protocol import STATUSD_CODE_REVISION
from .statusd_protocol import STATUSD_SERVICE_NAME
from .statusd_protocol import StatusProtocolError
from .statusd_protocol import StatusSnapshotMetadata
from .statusd_protocol import activity_summary_disabled_response
from .statusd_protocol import activity_summary_enabled
from .statusd_protocol import decode_activity_work_body
from .statusd_protocol import validate_request
from .statusd_client import STATUSD_DEFAULT_IDLE_SECONDS
from .statusd_client import default_socket_path


STATUSD_MAX_SESSIONS = 256
STATUSD_CONCURRENT_HANDLER_LIMIT = LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT

# Working/idle classification changes on every agent turn transition, and nothing about that
# transition (no approval prompt, no attention-ack) triggers an explicit invalidate() call. Without
# a bounded max age, a snapshot built while an agent was busy would be served forever, leaving tab
# status dots stuck on "running" long after the agent actually stopped. Mirrors the pre-statusd
# AUTO_APPROVE_CACHE_MAX_AGE_SECONDS safety net removed when this daemon replaced that cache.
STATUSD_SNAPSHOT_MAX_AGE_SECONDS = 5.003
STATUSD_SESSION_RECENT_INTERVAL_SECONDS = 10.0
STATUSD_SESSION_QUIET_INTERVAL_SECONDS = 30.0
STATUSD_SESSION_COLD_INTERVAL_SECONDS = 120.0
STATUSD_SESSION_ACTIVE_AGE_SECONDS = 5 * 60.0
STATUSD_SESSION_RECENT_AGE_SECONDS = 60 * 60.0
STATUSD_SESSION_COLD_AGE_SECONDS = 24 * 60 * 60.0
STATUSD_SESSION_MAX_JITTER_SECONDS = 5.0

STATUSD_COMMAND_ROUTER = LocalServiceCommandRouter({
    "ping": "_handle_ping", "status": "_handle_status", "profile": "_handle_status",
    "snapshot": "_handle_snapshot", "inventory": "_handle_inventory",
    "activity_summary": "_handle_activity_summary", "wait_generation": "_handle_wait_generation",
    "invalidate": "_handle_invalidate", "lease": "_handle_lease", "release": "_handle_release",
    "shutdown": "_handle_shutdown", "shutdown_if_idle": "_handle_shutdown_if_idle",
})


def list_tmux_pane_source_signatures() -> tuple[dict[str, tuple[str, str]], str | None]:
    fields = (
        "session_name",
        "window_index",
        "pane_index",
        "pane_id",
        "pane_active",
        "window_active",
        "window_activity",
        "history_size",
        "history_bytes",
        "cursor_x",
        "cursor_y",
        "cursor_character",
        "pane_pid",
        "pane_current_command",
        "pane_dead",
        "pane_in_mode",
        "alternate_on",
        "pane_width",
        "pane_height",
    )
    result = tmux(
        ["list-panes", "-a", "-F", "\t".join(f"#{{{field}}}" for field in fields)],
        timeout=3.0,
    )
    if result.returncode != 0:
        return {}, cmd_error(result, "tmux pane source scan failed")
    signatures: dict[str, tuple[str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != len(fields):
            continue
        session = parts[0]
        pane_target = parts[3] or f"{session}:{parts[1]}.{parts[2]}"
        signature = hashlib.sha1("\0".join(parts[1:]).encode("utf-8")).hexdigest()[:16]
        signatures[pane_target] = (session, signature)
        if parts[4] == "1" and parts[5] == "1":
            signatures[session] = (session, signature)
    return signatures, None


class PersistentStatusService(LocalRpcServiceState):
    """One per-state-directory status owner with retained immutable bytes."""

    def __init__(
        self,
        socket_path: Path,
        idle_seconds: float = STATUSD_DEFAULT_IDLE_SECONDS,
        *,
        wall_clock: Callable[[], float] | None = None,
        monotonic: Callable[[], float] | None = None,
        session_activity_reader: Callable[[], tuple[dict[str, int], str | None]] | None = None,
        pane_source_reader: Callable[[], tuple[dict[str, tuple[str, str]], str | None]] | None = None,
        session_jitter: Callable[[float, float], float] | None = None,
    ):
        super().__init__(socket_path, prefix="yolomux-statusd", idle_seconds=idle_seconds)
        self.wall_clock = wall_clock or time.time
        self.monotonic = monotonic or time.monotonic
        self.session_activity_reader = session_activity_reader or list_tmux_session_activity
        self.pane_source_reader = pane_source_reader or list_tmux_pane_source_signatures
        self.session_jitter = session_jitter or random.uniform
        self.lock = threading.Condition(threading.RLock())
        self.build_lock = threading.Lock()
        self.activity_lock = threading.Lock()
        self.app: TmuxWebtermApp | None = None
        self.activity_app: TmuxWebtermApp | None = None
        self.activity_profile: dict[str, Any] = {}
        self.session_names: tuple[str, ...] = ()
        self.snapshot: tuple[StatusSnapshotMetadata, bytes] | None = None
        # The roster the retained snapshot was actually BUILT for. `session_names` is bound to the
        # in-flight build's roster by `_ensure_app` before that build commits, so it cannot answer
        # "does this snapshot cover this session". Reading a session through the wrong roster is how
        # a session created one second earlier was reported as a definitive `unknown session` 404.
        self.snapshot_session_names: tuple[str, ...] = ()
        self.snapshot_payload: dict[str, Any] | None = None
        self.snapshot_signature: str | None = None
        self.generation = 0
        self.build_count = 0
        self.encode_count = 0
        self.snapshot_build_conflicts = 0
        # Retention is bounded to ONE latest roster, so a newer divergent demand replaces an
        # older pending one. Count those replacements: the superseded roster is still owed a
        # build, and a starved roster must be measurable here rather than disappearing silently.
        self.snapshot_refresh_supersessions = 0
        self.invalidation_reason = "startup"
        self.invalidation_generation = 0
        self.last_error = ""
        self.inventory: tuple[dict[str, object], bytes] | None = None
        self.inventory_generation = 0
        self.inventory_signature: str | None = None
        self.refresh_worker: threading.Thread | None = None
        self.refresh_requested_sessions: tuple[str, ...] | None = None
        self.refresh_build_sessions: tuple[str, ...] | None = None
        self.refresh_retry_at = 0.0
        self.session_payload_cache: dict[str, dict[str, Any]] = {}
        self.session_capture_due_at: dict[str, float] = {}
        self.session_activity: dict[str, int] = {}
        self.session_capture_attempts = 0
        self.session_capture_promotions = 0
        self.pane_source_signatures: dict[str, tuple[str, str]] = {}
        # The external schema fixed this key before the implementation. Its value counts actual
        # roster classify_agent_pane calls, including mandatory due-at safety recaptures.
        self.owner_invocations = {"statusd_unchanged_pane_capture": 0}

    def _session_capture_interval(self, activity_timestamp: int | None) -> float:
        age = max(0.0, self.wall_clock() - float(activity_timestamp or 0))
        if activity_timestamp is None or age < STATUSD_SESSION_ACTIVE_AGE_SECONDS:
            return STATUSD_SNAPSHOT_MAX_AGE_SECONDS
        if age < STATUSD_SESSION_RECENT_AGE_SECONDS:
            target = STATUSD_SESSION_RECENT_INTERVAL_SECONDS
        elif age < STATUSD_SESSION_COLD_AGE_SECONDS:
            target = STATUSD_SESSION_QUIET_INTERVAL_SECONDS
        else:
            target = STATUSD_SESSION_COLD_INTERVAL_SECONDS
        jitter_bound = min(STATUSD_SESSION_MAX_JITTER_SECONDS, target * 0.1)
        jitter = self.session_jitter(-jitter_bound, jitter_bound)
        return quiet_poll_interval(STATUSD_SNAPSHOT_MAX_AGE_SECONDS, target, 1.0, jitter)

    def _capture_sessions(
        self,
        sessions: tuple[str, ...],
        *,
        force: bool,
    ) -> tuple[set[str], set[str] | None, dict[str, tuple[str, str]] | None, str | None]:
        now = self.monotonic()
        roster = set(sessions)
        self.session_payload_cache = {
            name: payload for name, payload in self.session_payload_cache.items() if name in roster
        }
        self.session_capture_due_at = {
            name: due_at for name, due_at in self.session_capture_due_at.items() if name in roster
        }
        self.session_activity = {
            name: timestamp for name, timestamp in self.session_activity.items() if name in roster
        }
        activity, activity_error = self.session_activity_reader()
        pane_sources, pane_source_error = self.pane_source_reader()
        if activity_error or pane_source_error:
            selected = roster
            deadline_reset_sessions = roster
            capture_targets = None
            retained_pane_sources = None
        else:
            selected = set()
            recapture_sessions = set()
            for name in sessions:
                timestamp = activity.get(name)
                previous = self.session_activity.get(name)
                promoted = previous is not None and timestamp != previous
                if promoted and now < self.session_capture_due_at.get(name, 0.0):
                    self.session_capture_promotions += 1
                due = now >= self.session_capture_due_at.get(name, 0.0)
                missing = name not in self.session_payload_cache
                if force or missing or promoted or due:
                    selected.add(name)
                if missing or promoted or due:
                    recapture_sessions.add(name)
                if timestamp is not None:
                    self.session_activity[name] = timestamp
            retained_pane_sources = {
                target: (session, signature)
                for target, (session, signature) in pane_sources.items()
                if session in roster
            }
            # Pane metadata is an early invalidation signal, not a correctness TTL. A session whose
            # established due-at has arrived remains in recapture_sessions even when every cheap
            # field collides, so same-size/same-cursor screen rewrites cannot freeze status.
            capture_targets = {
                target
                for target, (session, signature) in retained_pane_sources.items()
                if session in recapture_sessions
                or self.pane_source_signatures.get(target) != (session, signature)
            }
            selected.update(
                session
                for target, (session, signature) in retained_pane_sources.items()
                if self.pane_source_signatures.get(target) != (session, signature)
            )
            deadline_reset_sessions = recapture_sessions | {
                session
                for target, (session, signature) in retained_pane_sources.items()
                if self.pane_source_signatures.get(target) != (session, signature)
            }
        for name in deadline_reset_sessions:
            self.session_capture_due_at[name] = now + self._session_capture_interval(activity.get(name))
        self.session_capture_attempts += len(selected)
        errors = [error for error in (activity_error, pane_source_error) if error]
        return selected, capture_targets, retained_pane_sources, "; ".join(errors) if errors else None

    def _sessions(self, request: dict[str, Any]) -> tuple[str, ...]:
        raw = request.get("sessions", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list) or len(raw) > STATUSD_MAX_SESSIONS:
            raise StatusProtocolError("invalid sessions")
        names = tuple(dict.fromkeys(str(item).strip() for item in raw if isinstance(item, str) and item.strip()))
        if len(names) != len(raw):
            raise StatusProtocolError("invalid sessions")
        return names

    def _ensure_app(self, sessions: tuple[str, ...]) -> TmuxWebtermApp:
        if self.app is None:
            self.app = TmuxWebtermApp(list(sessions), status_service_mode=True)
        self.app.sessions = list(sessions)
        self.session_names = sessions
        return self.app

    def _ensure_activity_app(self, sessions: tuple[str, ...]) -> TmuxWebtermApp:
        if self.activity_app is None:
            self.activity_app = TmuxWebtermApp(list(sessions), status_service_mode=True)
        self.activity_app.sessions = list(sessions)
        return self.activity_app

    def _build(self, sessions: tuple[str, ...]) -> tuple[StatusSnapshotMetadata, bytes]:
        with self.lock:
            build_invalidation_generation = self.invalidation_generation
            force_capture = bool(self.invalidation_reason) or sessions != self.snapshot_session_names
        app = self._ensure_app(sessions)
        timings: dict[str, float] = {}
        capture_sessions, capture_targets, pane_sources, activity_error = self._capture_sessions(
            sessions,
            force=force_capture,
        )
        payload, status = app.build_auto_approve_status(
            timings=timings,
            sync_workers=False,
            session_payload_cache=self.session_payload_cache,
            capture_sessions=capture_sessions,
            pane_source_signatures=(
                {target: signature for target, (_session, signature) in pane_sources.items()}
                if pane_sources is not None
                else None
            ),
            capture_targets=capture_targets,
        )
        if not isinstance(payload, dict):
            raise StatusProtocolError("invalid status payload")
        sessions_payload = payload.get("sessions")
        if isinstance(sessions_payload, dict):
            self.session_payload_cache = {
                name: dict(value)
                for name, value in sessions_payload.items()
                if name in sessions and isinstance(value, dict)
            }
        if pane_sources is not None:
            self.pane_source_signatures = pane_sources
        pane_capture_count = timings.get("pane_capture_count", 0.0)
        if isinstance(pane_capture_count, (int, float)):
            self.owner_invocations["statusd_unchanged_pane_capture"] += max(0, int(pane_capture_count))
        if activity_error:
            errors = payload.get("errors")
            if isinstance(errors, list):
                errors.append(activity_error)
        payload["timings"] = timings
        source_payload = {key: value for key, value in payload.items() if key != "timings"}
        source_signature = hashlib.sha1(
            json.dumps(source_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        # Reserve the generation before encoding so the immutable body forwarded to every
        # consumer identifies the exact statusd snapshot it represents. Do not advance the
        # public counter until this body is committed: waiters must never observe a generation
        # for a snapshot that cannot yet be read.
        with self.lock:
            if self.snapshot is not None and not self.invalidation_reason and source_signature == self.snapshot_signature:
                metadata, body = self.snapshot
                refreshed = StatusSnapshotMetadata(
                    metadata.generation,
                    metadata.status,
                    False,
                    self.wall_clock(),
                    metadata.content_type,
                )
                self.snapshot = (refreshed, body)
                self.snapshot_session_names = sessions
                self.last_error = ""
                self.lock.notify_all()
                return refreshed, body
            generation = self.generation + 1
        payload["agent_window_snapshot_revision"] = generation
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.lock:
            self.generation = generation
            self.build_count += 1
            self.encode_count += 1
            metadata = StatusSnapshotMetadata(
                generation=self.generation,
                status=int(status),
                stale=False,
                built_at=self.wall_clock(),
            )
            self.snapshot = (metadata, body)
            self.snapshot_session_names = sessions
            self.snapshot_payload = payload
            self.snapshot_signature = source_signature
            # An invalidate accepted while the expensive build was outside the lock belongs
            # to a later generation. Preserve it so the refresh loop immediately rebuilds
            # instead of letting this older result erase newer producer state.
            if self.invalidation_generation == build_invalidation_generation:
                self.invalidation_reason = ""
            self.last_error = ""
            self.lock.notify_all()
        return metadata, body

    def _discover_roster(self, hint: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
        # The daemon owns the canonical roster; a web-supplied hint is only a
        # fallback when tmux enumeration fails, never authority.
        names, error = list_tmux_session_names()
        if error or not names:
            return hint, "hint"
        roster = tuple(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
        return (roster, "daemon") if roster else (hint, "hint")

    def _session_inventory_entry(self, info: Any) -> dict[str, Any]:
        # Bounded identifiers only: no git/transcript/repo enrichment. discover_status_sessions
        # runs with enrich_paths=False, so current_path here is the raw tmux pane cwd.
        panes = [
            {"target": str(pane.target or ""), "window": str(pane.window or ""), "cwd": str(pane.current_path or ""), "active": bool(getattr(pane, "active", False))}
            for pane in getattr(info, "panes", [])
        ]
        agents = [
            {"kind": str(agent.kind or ""), "pane": str(getattr(agent, "pane_target", "") or "")}
            for agent in getattr(info, "agents", [])
        ]
        material = json.dumps({"panes": panes, "agents": agents}, sort_keys=True, separators=(",", ":"))
        return {
            "windows": len({pane["window"] for pane in panes}),
            "panes": panes,
            "agents": agents,
            "source_signature": hashlib.sha1(material.encode("utf-8")).hexdigest()[:16],
        }

    def _inventory(self, request: dict[str, Any]) -> tuple[dict[str, object], bytes]:
        hint = self._sessions(request)
        roster, roster_source = self._discover_roster(hint)
        infos, errors = discover_status_sessions(list(roster))
        sessions_payload = {name: self._session_inventory_entry(info) for name, info in infos.items()}
        overall = hashlib.sha1(
            json.dumps({name: entry["source_signature"] for name, entry in sorted(sessions_payload.items())}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.lock:
            if overall != self.inventory_signature:
                self.inventory_generation += 1
                self.inventory_signature = overall
                self.lock.notify_all()
            generation = self.inventory_generation
            payload = {
                "inventory_generation": generation,
                "roster": list(roster),
                "roster_source": roster_source,
                "sessions": sessions_payload,
                "errors": list(errors),
            }
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            metadata = {"ok": True, "protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": generation, "built_at": time.time()}
            self.inventory = (metadata, body)
        return metadata, body

    def _activity_summary(self, request: dict[str, Any], request_binary: bytes) -> tuple[dict[str, object], bytes]:
        if not activity_summary_enabled():
            return activity_summary_disabled_response()
        activity_started = time.perf_counter()
        sessions = self._sessions(request)
        decode_started = time.perf_counter()
        work_by_session = decode_activity_work_body(request_binary, sessions)
        timings = {"decode_ms": round((time.perf_counter() - decode_started) * 1000, 1)}
        with self.lock:
            self.activity_profile = {
                "in_progress": True,
                "phase": "waiting",
                "sessions": len(sessions),
                "work_sessions": len(work_by_session),
                "request_bytes": len(request_binary),
                "timings": dict(timings),
                "error": "",
            }
        with self.activity_lock:
            try:
                app = self._ensure_activity_app(sessions)
                # The web process owns the summary worker, but every completed update is durable.
                # Reload immediately before assembly so the daemon never attaches its startup copy.
                with self.lock:
                    self.activity_profile["phase"] = "load_summaries"
                started = time.perf_counter()
                app.yoagent_controller.load_yoagent_session_summaries()
                timings["load_summaries_ms"] = round((time.perf_counter() - started) * 1000, 1)
                with self.lock:
                    self.activity_profile["phase"] = "assemble"
                    self.activity_profile["timings"] = dict(timings)
                payload = app.assemble_activity_summary_payload(
                    force=request["force"],
                    locale=request["locale"],
                    session_scope=request["session_scope"],
                    hours=request["hours"],
                    work_by_session=work_by_session,
                    timings=timings,
                )
                with self.lock:
                    self.activity_profile["phase"] = "encode"
                    self.activity_profile["timings"] = dict(timings)
                started = time.perf_counter()
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                timings["encode_ms"] = round((time.perf_counter() - started) * 1000, 1)
                if len(body) > LOCAL_RPC_MAX_BINARY_BYTES:
                    raise StatusProtocolError("activity body too large")
            except Exception as error:
                with self.lock:
                    self.activity_profile.update({
                        "in_progress": False,
                        "phase": "failed",
                        "total_ms": round((time.perf_counter() - activity_started) * 1000, 1),
                        "error": str(error)[:256],
                    })
                raise
            with self.lock:
                self.activity_profile.update({
                    "in_progress": False,
                    "phase": "complete",
                    "total_ms": round((time.perf_counter() - activity_started) * 1000, 1),
                    "response_bytes": len(body),
                    "timings": dict(timings),
                })
        return {
            "ok": True,
            "protocol_version": STATUSD_PROTOCOL_VERSION,
            "status": int(HTTPStatus.OK),
            "built_at": time.time(),
            "content_type": "application/json; charset=utf-8",
        }, body

    def _retain_refresh_request(self, sessions: tuple[str, ...]) -> None:
        """Retain the one roster the refresh loop must build next. Caller holds ``self.lock``.

        Single owner for "statusd owes this roster a build". A divergent roster demanded while
        another roster is mid-build used to be dropped here, which made the bounded `refreshing`
        response unresolvable: the age-based reconciler in `refresh_loop` rebuilds
        `self.session_names` -- the roster that just built -- so nothing else remembered the
        divergent one, and only a later independent demand for it could ever make progress.

        Retaining it does not race the in-flight build. `refresh_loop` consumes this slot and
        assigns `refresh_build_sessions` in one locked step, so a roster retained during an
        active build cannot be picked up until that build has committed its generation and
        notified its waiters inside `_build`.

        Bounded to one latest request, never a queue: three divergent rosters arriving during a
        single build leave only the third retained, and the two superseded ones are counted in
        `snapshot_refresh_supersessions` and re-register on their next demand.
        """
        if self.refresh_build_sessions == sessions or self.refresh_requested_sessions == sessions:
            return
        if self.refresh_requested_sessions is not None:
            self.snapshot_refresh_supersessions += 1
        self.refresh_requested_sessions = sessions
        self.lock.notify_all()

    def _snapshot(self, request: dict[str, Any]) -> tuple[dict[str, object], bytes]:
        sessions = self._sessions(request)
        session = request.get("session")
        if session is not None and (not isinstance(session, str) or not session):
            raise StatusProtocolError("invalid session")
        with self.lock:
            # Only the roster this snapshot was built for may be answered from it. `session_names`
            # adopts the roster of a build the moment that build STARTS, so matching on it served the
            # previous roster's snapshot as an authoritative answer for a session it never covered:
            # the unscoped read reported `stale: False`, and the session-scoped read reported a
            # definitive `unknown session` 404 for a session that existed.
            retained = self.snapshot if sessions == self.snapshot_session_names else None
            payload = self.snapshot_payload if retained is not None else None
            expired = bool(retained and time.time() - retained[0].built_at > STATUSD_SNAPSHOT_MAX_AGE_SECONDS)
            needs_refresh = retained is None or bool(self.invalidation_reason) or expired
            if needs_refresh:
                if retained is None and self.refresh_build_sessions not in (None, sessions):
                    # A divergent roster demanded while another roster builds. It still answers
                    # with the bounded transient `refreshing` outcome below -- this counter only
                    # records that the demand arrived mid-build, it never suppresses scheduling.
                    self.snapshot_build_conflicts += 1
                self._retain_refresh_request(sessions)
            if retained is None:
                return {"ok": False, "status": int(HTTPStatus.SERVICE_UNAVAILABLE), "error": "refreshing"}, b""
            metadata, body = retained
            served_stale = needs_refresh
        if session is not None:
            if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), dict) or session not in payload["sessions"]:
                return {"ok": False, "status": int(HTTPStatus.NOT_FOUND), "error": "unknown session"}, b""
            session_payload = payload["sessions"][session]
            if not isinstance(session_payload, dict):
                return {"ok": False, "status": int(HTTPStatus.NOT_FOUND), "error": "unknown session"}, b""
            # Session-scoped reads are still statusd snapshots; retain the source revision so
            # a client cannot merge this state with Tabber data from a different generation.
            body = json.dumps({**session_payload, "agent_window_snapshot_revision": metadata.generation}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        response_metadata = metadata if not served_stale else StatusSnapshotMetadata(
            metadata.generation,
            metadata.status,
            True,
            metadata.built_at,
            metadata.content_type,
        )
        return {"ok": True, **response_metadata.to_dict()}, body

    def _wait_generation(self, request: dict[str, Any]) -> tuple[dict[str, object], bytes]:
        after = int(request.get("after_generation") or 0)
        timeout = float(request.get("timeout_seconds") or 0.0)
        deadline = time.monotonic() + timeout
        with self.lock:
            while self.generation <= after and timeout > 0 and not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.lock.wait(remaining)
            metadata = self.snapshot[0] if self.snapshot else None
        return {"ok": True, "protocol_version": STATUSD_PROTOCOL_VERSION, "generation": metadata.generation if metadata else 0, "changed": bool(metadata and metadata.generation > after)}, b""

    def refresh_loop(self) -> None:
        """Rebuild only while a web process holds a status generation lease.

        A worker-to-idle transition has no reliable immediate producer today.
        This daemon-owned cadence is therefore a correctness reconciliation, not
        a per-web polling substitute; each web process long-waits its generation.
        """
        while not self.stop_event.is_set():
            with self.lock:
                while True:
                    if self.stop_event.is_set():
                        return
                    requested = self.refresh_requested_sessions
                    if requested is not None:
                        sessions = requested
                        self.refresh_requested_sessions = None
                        break
                    if self.leases and self.snapshot is not None:
                        metadata, _body = self.snapshot
                        invalidated = bool(self.invalidation_reason)
                        remaining = 0.0 if invalidated else max(0.0, STATUSD_SNAPSHOT_MAX_AGE_SECONDS - (time.time() - metadata.built_at))
                        remaining = max(remaining, self.refresh_retry_at - time.monotonic())
                        if remaining <= 0:
                            sessions = self.session_names
                            break
                        self.lock.wait(remaining)
                        continue
                    self.lock.wait(0.1)
                self.refresh_build_sessions = sessions
            try:
                with self.build_lock:
                    self._build(sessions)
                with self.lock:
                    self.refresh_retry_at = 0.0
            except (OSError, RuntimeError, StatusProtocolError) as error:
                with self.lock:
                    self.last_error = str(error)[:256]
                    self.refresh_retry_at = time.monotonic() + 1.0
            finally:
                with self.lock:
                    if self.refresh_build_sessions == sessions:
                        self.refresh_build_sessions = None
                    self.lock.notify_all()

    def start_refresh_worker(self) -> None:
        with self.lock:
            worker = self.refresh_worker
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(target=self.refresh_loop, name="statusd-refresh", daemon=True)
            self.refresh_worker = worker
        worker.start()

    def idle_due(self) -> bool:
        with self.lock:
            # A test worker or a crashed web process cannot release its lease. Reap before
            # deciding idleness so its abandoned lease cannot pin a private daemon forever.
            reap_dead_client_leases(self.leases)
            return not self.leases and time.monotonic() - self.last_client_at >= self.idle_seconds

    def status(self) -> dict[str, object]:
        with self.lock:
            snapshot = self.snapshot[0] if self.snapshot else None
            return {
                "ok": True, "service": STATUSD_SERVICE_NAME, "pid": os.getpid(), "version": STATUSD_PROTOCOL_VERSION, "code_revision": STATUSD_CODE_REVISION, "build_revision": 1,
                "socket": str(self.socket_path), "started_at": self.started_at, "clients": len(self.leases),
                "generation": self.generation, "build_count": self.build_count, "encode_count": self.encode_count,
                "snapshot_build_conflicts": self.snapshot_build_conflicts,
                "snapshot_refresh_supersessions": self.snapshot_refresh_supersessions,
                "session_capture_attempts": self.session_capture_attempts,
                "session_capture_promotions": self.session_capture_promotions,
                "owner_invocations": dict(self.owner_invocations),
                "invalidation_generation": self.invalidation_generation,
                "inventory_generation": self.inventory_generation,
                "cache": {"ready": snapshot is not None, "stale": False}, "invalidation_reason": self.invalidation_reason,
                "last_error": self.last_error, "sessions": len(self.session_names), "queue_depth": int(self.refresh_requested_sessions is not None),
                "activity_summary": {
                    **self.activity_profile,
                    "timings": dict(self.activity_profile.get("timings") or {}),
                },
            }

    def _handle_ping(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return CommonDaemonActions.ping(
            STATUSD_SERVICE_NAME,
            STATUSD_PROTOCOL_VERSION,
            pid=os.getpid(),
            code_revision=STATUSD_CODE_REVISION,
            build_revision=1,
        )

    def _handle_status(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return CommonDaemonActions.status(self.status)

    @staticmethod
    def _bad_request(error: StatusProtocolError) -> tuple[dict[str, Any], bytes]:
        return {"ok": False, "status": int(HTTPStatus.BAD_REQUEST), "error": str(error)}, b""

    def _handle_snapshot(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        try:
            return self._snapshot(request)
        except StatusProtocolError as error:
            return self._bad_request(error)

    def _handle_inventory(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        try:
            return self._inventory(request)
        except StatusProtocolError as error:
            return self._bad_request(error)

    def _handle_activity_summary(self, request: dict[str, Any], body: bytes) -> tuple[dict[str, Any], bytes]:
        try:
            return self._activity_summary(request, body)
        except StatusProtocolError as error:
            return self._bad_request(error)

    def _handle_wait_generation(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        return self._wait_generation(request)

    def _handle_invalidate(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        with self.lock:
            self.invalidation_generation += 1
            self.invalidation_reason = str(request.get("reason") or "external")[:80]
            self.lock.notify_all()
        return {"ok": True, "generation": self.generation}, b""

    def _handle_lease(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        with self.lock:
            response = acquire_client_lease(self.leases, request.get("client_pid"), request.get("lease_id"))
            self.lock.notify_all()
        return {**response, "version": STATUSD_PROTOCOL_VERSION}, b""

    def _handle_release(self, request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        with self.lock:
            response = release_client_lease(self.leases, request.get("lease_id"))
            self.lock.notify_all()
        return response, b""

    def _handle_shutdown(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        self.stop_event.set()
        with self.lock:
            self.lock.notify_all()
        return {"ok": True, "shutdown": True}, b""

    def _handle_shutdown_if_idle(self, _request: dict[str, Any], _body: bytes) -> tuple[dict[str, Any], bytes]:
        with self.lock:
            leased = bool(self.leases)
        if leased:
            return {"ok": True, "shutdown": False, "leases": len(self.leases)}, b""
        return self._handle_shutdown({}, b"")

    def handle(self, request: dict[str, Any], request_binary: bytes = b"") -> tuple[dict[str, object], bytes]:
        self.last_client_at = time.monotonic()
        try:
            request = validate_request(request)
        except StatusProtocolError as error:
            return {"ok": False, "error": str(error), "required_protocol_version": STATUSD_PROTOCOL_VERSION}, b""
        response = STATUSD_COMMAND_ROUTER.dispatch(self, str(request["action"]), request, request_binary)
        return response if response is not None else ({"ok": False, "error": "unknown status action"}, b"")

    def run(self) -> int:
        self.start_refresh_worker()
        return run_local_rpc_service(
            socket_path=self.socket_path, lock_path=self.lock_path, service_name=STATUSD_SERVICE_NAME,
            stop_event=self.stop_event, handle=self.handle,
            on_idle=self.idle_due,
            on_client=lambda: setattr(self, "last_client_at", time.monotonic()),
            concurrent_handlers=STATUSD_CONCURRENT_HANDLER_LIMIT,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux shared status service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--idle-seconds", type=float, default=STATUSD_DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    apply_service_process_priority()
    return PersistentStatusService(Path(args.socket), idle_seconds=args.idle_seconds).run()


if __name__ == "__main__":
    raise SystemExit(main())
