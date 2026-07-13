"""Bounded stateless CPU broker for YOLOmux background transforms.

The web process submits only registered, immutable JSON payloads.  ``jobd``
owns priority ordering, coalescing, cancellation, and a small spawn-based
executor pool so CPU-bound Python work cannot run in HTTP request threads.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import time
import uuid
from collections import deque
from concurrent.futures import Future
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from .common import STATE_DIR
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import tail_file_lines
from .local_services.rpc import LOCAL_RPC_VERSION
from .local_services.rpc import safe_socket_path
from .local_services.runtime import acquire_client_lease
from .local_services.runtime import apply_service_process_priority
from .local_services.runtime import redact_local_service_text
from .local_services.runtime import release_client_lease
from .local_services.runtime import run_local_rpc_service
from .local_services.client import LocalServiceClient
from .transcripts import compact_transcript_items
from .transcripts import compact_transcript_items_since
from .transcripts import compact_transcript_lines
from .transcripts import newest_transcript_activity_timestamp
from .transcripts import newest_transcript_timestamp
from .transcripts import transcript_activity_state_from_text


JOBD_PROTOCOL_VERSION = LOCAL_RPC_VERSION
JOBD_DEFAULT_IDLE_SECONDS = 60.0
JOBD_MAX_WORKERS = 2
JOBD_MAX_QUEUE = 64
JOBD_MAX_PAYLOAD_BYTES = 256 * 1024
JOBD_MAX_RESULT_BYTES = 512 * 1024
JOBD_MAX_RECORDS = 256
JOBD_MAX_DEADLINE_MS = 120_000
JOBD_MAX_CONSECUTIVE_HIGH_PRIORITY = 4
JOBD_SOCKET_NAME = "jobd.sock"
JOBD_PRIORITIES = ("interactive", "freshness", "maintenance")


def default_socket_path() -> Path:
    return safe_socket_path(STATE_DIR / "services" / JOBD_SOCKET_NAME, prefix="yolomux-jobd")


def default_worker_count(cpu_count: int | None = None) -> int:
    return max(1, min(JOBD_MAX_WORKERS, max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)) - 1)))


def _json_compact(payload: bytes) -> bytes:
    value = json.loads(payload.decode("utf-8"))
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _text_facts(payload: bytes) -> bytes:
    value = json.loads(payload.decode("utf-8"))
    text = str(value.get("text") or "") if isinstance(value, dict) else ""
    lines = text.splitlines()
    result = {"bytes": len(text.encode("utf-8")), "lines": len(lines), "nonempty_lines": sum(1 for line in lines if line.strip())}
    return json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _indexed_repo_roots(payload: bytes) -> bytes:
    """Discover configured repositories in a worker process, never in HTTP."""
    value = json.loads(payload.decode("utf-8"))
    raw_dirs = value.get("indexed_dirs") if isinstance(value, dict) else None
    if not isinstance(raw_dirs, list) or len(raw_dirs) > 64:
        raise ValueError("indexed_dirs must be a bounded list")
    indexed_dirs: list[str] = []
    for item in raw_dirs:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("indexed directory must be absolute and normalized")
        indexed_dirs.append(str(path))
    # Import locally so ordinary jobd startup does not initialize metadata/git
    # helpers until this maintenance task actually runs.
    from .metadata import _discover_indexed_repo_roots

    result = {"roots": _discover_indexed_repo_roots(indexed_dirs)}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _normalized_transcript_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError("transcript path must be absolute")
    if ".." in path.parts:
        raise ValueError("transcript path must be normalized")
    if path.is_symlink():
        raise ValueError("transcript path must not be a symlink")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("transcript path must be a file")
    return resolved


def _transcript_view(payload: bytes) -> bytes:
    """Read one bounded transcript tail and return compact facts only.

    This task intentionally has no session or HTTP knowledge.  The caller keys it
    by stable file identity and generation; the worker restats before and after
    the bounded read so callers can reject append/truncate/replace races.
    """
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("transcript view payload must be an object")
    path_text = str(value.get("path") or "")
    path = _normalized_transcript_path(path_text)
    line_limit = max(1, min(int(value.get("line_limit") or MAX_TRANSCRIPT_TAIL_LINES), MAX_TRANSCRIPT_TAIL_LINES))
    item_limit = max(1, min(int(value.get("item_limit") or MAX_COMPACT_TRANSCRIPT_ITEMS), MAX_COMPACT_TRANSCRIPT_ITEMS))
    compact_line_limit = max(0, min(int(value.get("compact_line_limit") or 0), MAX_COMPACT_TRANSCRIPT_ITEMS))
    kind = str(value.get("kind") or "")[:32]
    since_text = str(value.get("since") or "")
    before = path.stat()
    text = tail_file_lines(path, line_limit)
    after = path.stat()
    items = compact_transcript_items(text, item_limit)
    since_items: list[dict[str, str]] = []
    since_stats: dict[str, int] = {}
    if since_text:
        try:
            since = datetime.fromisoformat(since_text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid transcript since timestamp") from exc
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_items, since_stats = compact_transcript_items_since(text, since)
        since_items = since_items[-item_limit:]
    compact_lines = compact_transcript_lines(text, compact_line_limit) if compact_line_limit else []
    newest = newest_transcript_timestamp(text)
    activity = newest_transcript_activity_timestamp(text, kind)
    result: dict[str, Any] = {
        "generation": [int(after.st_mtime_ns), int(after.st_size)],
        "read_generation": [int(before.st_mtime_ns), int(before.st_size)],
        "items": items,
        "since_items": since_items,
        "since_stats": since_stats,
        "compact_lines": compact_lines,
        "newest_timestamp": newest.isoformat() if newest is not None else "",
        "activity_timestamp": activity.isoformat() if activity is not None else "",
        "activity_state": transcript_activity_state_from_text(text, kind),
    }
    # A transcript item is already bounded by the parser, but preserve the
    # broker's contract even for a pathological number of tool blocks.
    while len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > JOBD_MAX_RESULT_BYTES - 4096 and result["items"]:
        result["items"].pop(0)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


REGISTERED_TASKS = {
    "indexed_repo_roots": _indexed_repo_roots,
    "json_compact": _json_compact,
    "text_facts": _text_facts,
    "transcript_view": _transcript_view,
}


def run_registered_task(task: str, payload: bytes) -> bytes:
    """Executor entry point; accepts only known names and bounded bytes."""
    if task not in REGISTERED_TASKS:
        raise ValueError("unknown task")
    if len(payload) > JOBD_MAX_PAYLOAD_BYTES:
        raise ValueError("payload too large")
    result = REGISTERED_TASKS[task](payload)
    if len(result) > JOBD_MAX_RESULT_BYTES:
        raise ValueError("result too large")
    return result


@dataclass
class JobRecord:
    job_id: str
    task: str
    payload: bytes
    priority: str
    generation: int
    coalesce_key: str
    submitted_at: float
    status: str = "queued"
    future: Future[bytes] | None = None
    result: bytes = b""
    error: str = ""
    completed_at: float = 0.0
    deadline_at: float = 0.0


class PersistentJobBroker:
    """One local broker with a small spawn-only pool for typed CPU jobs."""

    def __init__(self, socket_path: Path, idle_seconds: float = JOBD_DEFAULT_IDLE_SECONDS, workers: int | None = None):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-jobd")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = multiprocessing.get_context("spawn").Event()
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.worker_count = max(1, min(JOBD_MAX_WORKERS, int(workers or default_worker_count())))
        self.started_at = time.time()
        self.last_client_at = time.monotonic()
        self.leases: dict[str, int] = {}
        self.records: dict[str, JobRecord] = {}
        self.queues = {priority: deque() for priority in JOBD_PRIORITIES}
        self.coalesced: dict[tuple[str, str], str] = {}
        self.latest_generation: dict[str, int] = {}
        self.executor: ProcessPoolExecutor | None = None
        self.high_priority_streak = 0

    def _executor(self) -> ProcessPoolExecutor:
        if self.executor is None:
            self.executor = ProcessPoolExecutor(max_workers=self.worker_count, mp_context=multiprocessing.get_context("spawn"))
        return self.executor

    def _record_payload(self, record: JobRecord, *, include_result: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": record.job_id,
            "task": record.task,
            "priority": record.priority,
            "generation": record.generation,
            "status": record.status,
            "submitted_at": record.submitted_at,
            "completed_at": record.completed_at,
            "deadline_at": record.deadline_at,
            "error": record.error,
        }
        if include_result and record.status == "completed":
            payload["result"] = json.loads(record.result.decode("utf-8"))
        return payload

    def _mark_terminal(self, record: JobRecord, status: str, error: str = "") -> None:
        record.status = status
        record.error = error
        record.completed_at = time.time()

    def _future_slots(self) -> int:
        """Count executor work, including timed-out work that cannot be killed safely."""
        return sum(1 for record in self.records.values() if record.future is not None and not record.future.done())

    def _expire_deadlines(self, now: float) -> None:
        for record in self.records.values():
            if record.status not in {"queued", "running"} or record.deadline_at <= 0 or now < record.deadline_at:
                continue
            if record.status == "queued":
                self._mark_terminal(record, "timed_out", "deadline exceeded before execution")
            else:
                # ProcessPoolExecutor cannot safely cancel an already-running task.  Keep
                # its future occupying a slot until it exits so a deadline cannot create
                # unbounded hidden CPU work behind the broker's capacity accounting.
                self._mark_terminal(record, "timed_out", "deadline exceeded while executing")

    def _handle_finished_futures(self) -> None:
        restart_executor = False
        for record in self.records.values():
            if record.future is None or not record.future.done():
                continue
            future = record.future
            if record.status in {"completed", "failed", "cancelled", "superseded"}:
                continue
            try:
                result = future.result()
                if len(result) > JOBD_MAX_RESULT_BYTES:
                    raise ValueError("result too large")
                json.loads(result.decode("utf-8"))
                if record.status != "timed_out":
                    record.result = result
                    self._mark_terminal(record, "completed")
            except BrokenProcessPool as exc:
                if record.status != "timed_out":
                    self._mark_terminal(record, "failed", "worker crashed")
                restart_executor = True
            except (OSError, RuntimeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                if record.status != "timed_out":
                    self._mark_terminal(record, "failed", redact_local_service_text(exc))
        if restart_executor and self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None

    def _next_queued_record(self) -> JobRecord | None:
        candidates: dict[str, JobRecord | None] = {}
        for priority in JOBD_PRIORITIES:
            candidates[priority] = None
            queue = self.queues[priority]
            while queue:
                record = self.records.get(queue[0])
                if record is None or record.status != "queued":
                    queue.popleft()
                    continue
                candidates[priority] = record
                break
        lower = next((candidates[priority] for priority in JOBD_PRIORITIES[1:] if candidates[priority] is not None), None)
        selected = lower if self.high_priority_streak >= JOBD_MAX_CONSECUTIVE_HIGH_PRIORITY and lower is not None else next((candidates[priority] for priority in JOBD_PRIORITIES if candidates[priority] is not None), None)
        if selected is None:
            return None
        self.queues[selected.priority].popleft()
        self.high_priority_streak = self.high_priority_streak + 1 if selected.priority == "interactive" else 0
        return selected

    def _prune_records(self) -> None:
        terminal = sorted((record for record in self.records.values() if record.status in {"completed", "failed", "cancelled", "superseded", "timed_out"}), key=lambda record: record.completed_at)
        for record in terminal[:max(0, len(self.records) - JOBD_MAX_RECORDS)]:
            self.records.pop(record.job_id, None)
            if self.coalesced.get((record.task, record.coalesce_key)) == record.job_id:
                self.coalesced.pop((record.task, record.coalesce_key), None)

    def _pump(self) -> None:
        now = time.monotonic()
        self._expire_deadlines(now)
        self._handle_finished_futures()
        active = self._future_slots()
        while active < self.worker_count:
            record = self._next_queued_record()
            if record is None:
                break
            if record.generation < self.latest_generation.get(record.coalesce_key, record.generation):
                self._mark_terminal(record, "superseded")
                continue
            if record.deadline_at > 0 and now >= record.deadline_at:
                self._mark_terminal(record, "timed_out", "deadline exceeded before execution")
                continue
            record.future = self._executor().submit(run_registered_task, record.task, record.payload)
            record.status = "running"
            active += 1
        self._prune_records()

    def _queue_record(self, task: str, payload: dict[str, Any], priority: str, generation: int, coalesce_key: str, deadline_at: float = 0.0) -> JobRecord:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        record = JobRecord(uuid.uuid4().hex, task, encoded, priority, generation, coalesce_key, time.time(), deadline_at=deadline_at)
        self.records[record.job_id] = record
        self.coalesced[(task, coalesce_key)] = record.job_id
        self.queues[priority].append(record.job_id)
        return record

    def _supersede_stale_queued(self, coalesce_key: str, generation: int) -> None:
        for record in self.records.values():
            if record.coalesce_key == coalesce_key and record.status == "queued" and record.generation < generation:
                record.status = "superseded"
                record.completed_at = time.time()

    def _queued_count(self) -> int:
        return sum(1 for record in self.records.values() if record.status == "queued")

    def _submit(self, request: dict[str, Any]) -> dict[str, Any]:
        self._pump()
        task = str(request.get("task") or "")
        priority = str(request.get("priority") or "normal")
        if priority == "normal":
            priority = "freshness"
        if task not in REGISTERED_TASKS:
            return {"ok": False, "error": "unknown task"}
        if priority not in JOBD_PRIORITIES:
            return {"ok": False, "error": "invalid priority"}
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return {"ok": False, "error": "payload must be an object"}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > JOBD_MAX_PAYLOAD_BYTES:
            return {"ok": False, "error": "payload too large"}
        try:
            generation = max(0, int(request.get("generation") or 0))
            requested_deadline_ms = int(request.get("deadline_ms") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid generation or deadline"}
        if requested_deadline_ms < 0:
            return {"ok": False, "error": "invalid deadline"}
        if requested_deadline_ms > JOBD_MAX_DEADLINE_MS:
            return {"ok": False, "error": "deadline too large"}
        deadline_ms = requested_deadline_ms
        deadline_at = time.monotonic() + (deadline_ms / 1000.0) if deadline_ms else 0.0
        coalesce_key = str(request.get("coalesce_key") or f"{task}:{encoded.hex()}")[:256]
        existing_id = self.coalesced.get((task, coalesce_key))
        existing = self.records.get(existing_id or "")
        if existing is not None and existing.generation >= generation and existing.status in {"queued", "running", "completed"}:
            return {"ok": True, "coalesced": True, "job": self._record_payload(existing)}
        if self._queued_count() >= JOBD_MAX_QUEUE:
            return {"ok": False, "error": "queue full"}
        self.latest_generation[coalesce_key] = max(generation, self.latest_generation.get(coalesce_key, generation))
        self._supersede_stale_queued(coalesce_key, generation)
        record = self._queue_record(task, payload, priority, generation, coalesce_key, deadline_at)
        self._pump()
        return {"ok": True, "coalesced": False, "job": self._record_payload(record)}

    def common_status(self) -> dict[str, Any]:
        self._pump()
        return {
            "ok": True,
            "version": JOBD_PROTOCOL_VERSION,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "socket": str(self.socket_path),
            "clients": len(self.leases),
            "worker_count": self.worker_count,
            "queues": {priority: sum(1 for job_id in queue if self.records.get(job_id, JobRecord("", "", b"", priority, 0, "", 0)).status == "queued") for priority, queue in self.queues.items()},
            "active_task": next((record.task for record in self.records.values() if record.status == "running"), ""),
            "cache": {"records": len(self.records), "coalesced": len(self.coalesced), "record_limit": JOBD_MAX_RECORDS},
            "last_success": max((record.completed_at for record in self.records.values() if record.status == "completed"), default=0.0),
            "last_failure": next((record.error for record in reversed(list(self.records.values())) if record.status == "failed"), ""),
            "restart_backoff_seconds": 0.0,
            "generation": max(self.latest_generation.values(), default=0),
            "idle_seconds": self.idle_seconds,
        }

    def handle(self, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        action = str(request.get("action") or "")
        if action == "ping":
            return {"ok": True, "version": JOBD_PROTOCOL_VERSION, "pid": os.getpid(), "started_at": self.started_at}, b""
        if action == "status":
            return self.common_status(), b""
        if action == "profile":
            return {"ok": True, "profile": self.common_status()}, b""
        if action == "submit":
            return self._submit(request), b""
        if action == "result":
            self._pump()
            record = self.records.get(str(request.get("job_id") or ""))
            return ({"ok": False, "error": "unknown job"} if record is None else {"ok": True, "job": self._record_payload(record, include_result=True)}), b""
        if action == "cancel":
            record = self.records.get(str(request.get("job_id") or ""))
            if record is None:
                return {"ok": False, "error": "unknown job"}, b""
            if record.status == "queued":
                record.status = "cancelled"
                record.completed_at = time.time()
            elif record.status == "running" and record.future is not None:
                if record.future.cancel():
                    self._mark_terminal(record, "cancelled")
                else:
                    return {"ok": False, "error": "job already executing", "job": self._record_payload(record)}, b""
            return {"ok": True, "job": self._record_payload(record)}, b""
        if action == "lease":
            response = acquire_client_lease(self.leases, request.get("client_pid"))
            return {**response, "version": JOBD_PROTOCOL_VERSION}, b""
        if action == "release":
            return release_client_lease(self.leases, request.get("lease_id")), b""
        if action in {"shutdown", "shutdown_if_idle"}:
            if action == "shutdown_if_idle" and self.leases:
                return {"ok": True, "shutdown": False, "leases": len(self.leases)}, b""
            self.stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        return {"ok": False, "error": "unknown jobd action"}, b""

    def _on_shutdown(self) -> None:
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)

    def run(self) -> int:
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="jobd",
            stop_event=self.stop_event,
            handle=self.handle,
            on_idle=lambda: (self._pump() is None) and not self.leases and not self._queued_count() and not any(record.status == "running" for record in self.records.values()) and time.monotonic() - self.last_client_at >= self.idle_seconds,
            on_client=lambda: setattr(self, "last_client_at", time.monotonic()),
            on_shutdown=self._on_shutdown,
        )


class JobClient(LocalServiceClient):
    """Thin cross-port client for the shared stateless CPU broker."""

    def __init__(self, socket_path: Path | None = None):
        super().__init__("jobd", "yolomux_lib.jobd", socket_path or default_socket_path(), JOBD_PROTOCOL_VERSION, idle_seconds=JOBD_DEFAULT_IDLE_SECONDS)

    def start_for_scheduler(self) -> bool:
        """Start jobd from a scheduler/owner path, never an HTTP submission path."""
        return self.ensure_started()

    def submit(self, task: str, payload: dict[str, Any], *, priority: str = "freshness", generation: int = 0, coalesce_key: str = "", deadline_ms: int = 0) -> dict[str, Any]:
        return self.request({"action": "submit", "task": task, "payload": payload, "priority": priority, "generation": generation, "coalesce_key": coalesce_key, "deadline_ms": deadline_ms})

    def result(self, job_id: str) -> dict[str, Any]:
        return self.request({"action": "result", "job_id": job_id})

    def runtime_status(self) -> dict[str, Any]:
        status = self.registry.status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        return {"service": "jobd", "pid": int(payload.get("pid") or 0), "healthy": bool(status.get("healthy")), "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {}, "active_task": str(payload.get("active_task") or ""), "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {}, "generation": int(payload.get("generation") or 0), "resources": self.registry.resources(int(payload.get("pid") or 0))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux bounded CPU job broker")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--idle-seconds", type=float, default=JOBD_DEFAULT_IDLE_SECONDS)
    parser.add_argument("--workers", type=int, default=default_worker_count())
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    apply_service_process_priority()
    return PersistentJobBroker(Path(args.socket), idle_seconds=args.idle_seconds, workers=args.workers).run()


if __name__ == "__main__":
    raise SystemExit(main())
