"""Bounded stateless CPU broker for YOLOmux background transforms.

The web process submits only registered, immutable JSON payloads.  ``jobd``
owns priority ordering, coalescing, cancellation, and bounded spawn-based
executor capacity so CPU-bound Python work cannot run in HTTP request threads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import threading
import time
import traceback
import uuid
from collections import deque
from concurrent.futures import Future
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from .. import filesystem
from ..workspace import session_files
from ..observability.activity_summary import tabber_activity_view_result
from .common import RUNTIME_DIR
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import inline_json_product_metadata
from .common import product_filename
from .common import tail_file_lines
from ..local_services.rpc import LOCAL_RPC_MAX_BINARY_BYTES
from ..local_services.rpc import LOCAL_RPC_VERSION, safe_socket_path  # noqa: F401 - public transport-version compatibility export
from ..local_services.runtime import LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT
from ..local_services.runtime import acquire_client_lease
from ..local_services.runtime import apply_service_process_priority
from ..local_services.runtime import local_service_exception_cause
from ..local_services.runtime import local_service_failure_text
from ..local_services.runtime import redact_local_service_text
from ..local_services.runtime import release_client_lease
from ..local_services.runtime import run_local_rpc_service
from ..local_services.client import LocalServiceClient
from ..workspace.metadata import _discover_indexed_repo_roots
from ..workspace.metadata import metadata_warm_view_result
from ..observability.transcripts import compact_transcript_items
from ..observability.transcripts import compact_transcript_items_since
from ..observability.transcripts import compact_transcript_lines
from ..observability.transcripts import newest_transcript_activity_timestamp
from ..observability.transcripts import newest_transcript_timestamp
from ..observability.transcripts import transcript_activity_state_from_text
from ..web import html_preview_document


# The envelope transport remains LOCAL_RPC_VERSION. Bump this service generation whenever the
# registered task/result contract changes so a newly restarted web process retires an older daemon.
# v3: added the materialized-product layer (last-known-good store + `product` RPC + per-product counters).
# v4: registered the `session_files_view` task; a v3 daemon lacks it, so the fence retires the old one.
# v5: registered the `tabber_activity_view` task; a v4 daemon lacks it, so the fence retires the old one.
# v6: registered the `metadata_warm_view` task; a v5 daemon lacks it, so the fence retires the old one.
# v7: session_files_view returns bounded worker phase timings, surfaced in System diagnostics.
# v8: session_files_view's repository snapshot cache has bounded expiry pruning; restart older
# workers so the active broker does not retain an unbounded on-disk cache behavior.
# v9: bounded requester counters identify the requester for each completed session-files job.
# v10: metadata warm products include bounded Git/GitHub/Linear work totals.
# v11: timed-out products are counted and reported as the latest failure.
# v12: session-files requester attribution is recorded when a product is accepted, not only after
# a worker completes, so timeouts and still-running expensive jobs remain attributable.
# v13: tabber_activity_view accepts bounded precomputed recent-path summaries instead of full
# session-files payloads, so an older worker must not silently discard the projected field.
# v14: `produce` atomically submits one typed product request and either forwards already
# materialized bytes or returns its accepted job receipt without waiting in the RPC handler.
# v15: registered one max-64 filesystem list/info batch product for Finder.
# v16: dispatches cold process starts from a scheduler thread so RPC handlers stay available.
# v17: moves durable session-files cache pruning out of the web process.
# v18: adds byte-product relay requests for browser-owned filesystem consumers.
# v19: adds the bounded `point` scheduler lane, plus `fresh_only` submissions that join in-flight
# work but never accept an already-stored product. A v18 daemon rejects `priority="point"` as an
# invalid priority and would silently ignore `fresh_only`, serving a retained product for content
# that may have changed, so the fence must retire it.
# v20: filesystem descriptors carry the accepting server's access policy, and the worker authorizes
# with that policy instead of its own environment. A v19 daemon ignores the new field and keeps
# authorizing every port's filesystem work with its launcher's roots -- the cross-port confused
# deputy this fence exists to retire -- so an upgraded web process must not reuse one.
# v21: adds the bounded `mutation` scheduler lane so a point write/rename/mkdir no longer queues
# behind unbounded recursive work on the shared `interactive` slot. A v20 daemon rejects
# `priority="mutation"` as an invalid priority, so the fence must retire it.
# v22: retires the blocking `relay` action.  A browser byte download now submits with a zero-wait
# `produce` and polls `product` on the web side, so no handler blocks a serial listener slot for a
# whole job.  A v21 daemon still accepts `relay` and would block; a v22 web process never sends it,
# and a v21 web process sending `relay` to a v22 daemon gets `unknown jobd action`, so the fence
# retires the mismatched pair.
JOBD_PROTOCOL_VERSION = 22
JOBD_DEFAULT_IDLE_SECONDS = 60.0

# jobd is NOT demand-scoped, so it must never declare `demand_started`. The elected background
# owner pins it up with a registry lease (`JobClient.start_for_scheduler`, called at
# `app.py:2962` when this process acquires background ownership and released at `app.py:3381`
# on demote), and `_idle_should_stop` refuses to retire the broker while any lease is held. A
# process that owns scheduling and cannot see jobd is looking at a real outage.
#
# The one legitimate absence is the other side of that same lease: before this process wins the
# election, or when it never does, nothing here is scheduling and jobd is expected to be absent.
# That is a DYNAMIC fact about this process, not a static property of the service, so it is
# published as a bounded `absence_expected_reason` token and NOT as `demand_started` -- see
# `yolomux_lib/backend_health/observer.py:ABSENCE_EXPECTED_REASON_FIELD`.
JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE = "scheduler_not_owned"
# No jobd handler waits by contract anymore: every action is zero-wait.  `produce` atomically
# submits and inspects the product store and returns a receipt; the web process polls `product`
# for cold work on its own side (the former blocking `relay` action, which held one handler slot
# for the whole job, has been retired).  So jobd takes the same shared concurrency limit as
# watchd and statusd, and no cheap last-known-good `product` read is charged for another client's
# in-flight job.
JOBD_CONCURRENT_HANDLER_LIMIT = LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT
# One scheduler owner, three explicitly bounded lanes.  Every declared priority maps to exactly
# one executor through JOBD_PRIORITY_LANES, and JOBD_PRIORITIES is derived from that same table so
# a priority can never exist without a lane that runs it.
#
# `point` exists because bounded single-target filesystem work (an editor open's `read`, an `info`
# probe, an `index_status` check) previously shared the single `interactive` slot with Finder
# batches, watch-diff batches and forced session-files transforms.  One slow bulk holder then put
# every editor open behind it head-of-line: measured backend terminal latencies for one 12,353-byte
# file were 29.9s, 11.3s, 51.3s and 16.0s during a batch/watch fanout, and 0.02s once the lane
# drained.  Point capacity is deliberately bounded at two -- enough that one slow NFS stat cannot
# strand every other editor open, small enough that point work cannot become unbounded CPU itself.
#
# `mutation` is the write-side sibling of `point`, added for the same reason and measured the same
# way: a bounded single-target `write`/`rename`/`mkdir` used to share the single `interactive` slot
# with recursive `count`, `search`, `diff` and Finder batches, so clicking "new folder" while
# something walked a 457,364-file tree queued the one `mkdir` syscall for 6737 ms and 8167 ms
# across two runs while the `point` lane answered in 0.07 ms.  It is a lane of its own rather than
# more `point` capacity because `point` means a coalescable retained READ -- `app.py` gates the
# stat-derived content key and `fresh_only` on `priority == "point"` -- and a mutation is a
# non-coalescable side effect that would pay for that machinery without ever using it.  Capacity
# matches `point` for the same reason `point` is two: one slow mutation must not strand the next.
# Recursive `delete` is deliberately NOT here: `delete_path` walks and unlinks a whole subtree, so
# its cost is unbounded in the input and it belongs on the bulk-shared `interactive` lane.
JOBD_MAX_WORKERS = 2
JOBD_INTERACTIVE_WORKERS = 1
JOBD_POINT_WORKERS = 2
JOBD_MUTATION_WORKERS = 2
JOBD_LANE_PRIORITIES: dict[str, tuple[str, ...]] = {
    "point": ("point",),
    "mutation": ("mutation",),
    "interactive": ("interactive",),
    "bulk": ("freshness", "maintenance"),
}
JOBD_PRIORITY_LANES: dict[str, str] = {
    priority: lane for lane, priorities in JOBD_LANE_PRIORITIES.items() for priority in priorities
}
# Fixed per-lane worker capacity.  `bulk` is deliberately absent: its capacity is the instance's
# general worker count, derived from the host CPU count when the broker is constructed.
JOBD_LANE_WORKERS: dict[str, int] = {
    "point": JOBD_POINT_WORKERS,
    "mutation": JOBD_MUTATION_WORKERS,
    "interactive": JOBD_INTERACTIVE_WORKERS,
}
JOBD_SESSION_FILES_REQUESTERS = frozenset({
    "api-session-files", "api-session-files-batch", "background-refresh",
    "background-info-refresh", "metadata-cache-miss", "metadata-follower-fallback",
})
JOBD_MAX_QUEUE = 64
JOBD_MAX_PAYLOAD_BYTES = 256 * 1024
JOBD_MAX_RESULT_BYTES = 512 * 1024
JOBD_MAX_FILESYSTEM_BATCH_RESULT_BYTES = LOCAL_RPC_MAX_BINARY_BYTES
JOBD_MAX_RETAINED_RESULT_BYTES = 32 * 1024 * 1024
JOBD_MAX_RECORDS = 256
# The last-known-good product store is keyed by coalesce_key (per file/session), so bound it
# independently of the job-record ring and evict the oldest completed bytes past this many keys.
JOBD_MAX_PRODUCTS = 256
JOBD_MAX_SOURCE_DIAGNOSTICS = 256
JOBD_MAX_DEADLINE_MS = 120_000
JOBD_SCHEDULER_POLL_SECONDS = 0.05
JOBD_SOCKET_NAME = "jobd.sock"
JOBD_PRIORITIES = tuple(JOBD_PRIORITY_LANES)
JOBD_REQUEST_ACTIONS = frozenset({
    "ping", "status", "profile", "submit", "result", "product", "produce", "cancel",
    "lease", "release", "shutdown", "shutdown_if_idle",
})
JOBD_PRODUCT_DELIVERY_MODES = frozenset({"ready_or_receipt", "receipt"})


def default_socket_path() -> Path:
    return safe_socket_path(RUNTIME_DIR / "services" / JOBD_SOCKET_NAME, prefix="yolomux-jobd")


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
        # File identity (device, inode) is separate from the [mtime, size] generation so a
        # replaced inode that coincidentally reproduces the same mtime/size cannot satisfy an
        # old key. The consumer rejects a result whose identity differs from the file it expects.
        "identity": [int(after.st_dev), int(after.st_ino)],
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


def _session_files_view(payload: bytes) -> bytes:
    """Compute one bounded session-files product (recursive discovery + git) in a worker.

    Keeps ALL git spawns and transcript discovery out of the web process. The orchestrator lives in
    ``session_files`` (import-safe, no app/web) so it is unit-testable without a broker socket.
    """
    value = json.loads(payload.decode("utf-8"))
    result = session_files.session_files_view_result(value, max_bytes=JOBD_MAX_RESULT_BYTES - 4096)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _tabber_activity_view(payload: bytes) -> bytes:
    """Assemble bounded Tabber rows for changed sessions from pre-gathered data in a worker.

    Pure assembly only (dict merge/sort) -- the web owner does all impure gathering (tmux capture,
    live attention/cooldown, git) before submitting. The orchestrator lives in ``activity_summary``
    (import-safe, no app/web) so it is unit-testable without a broker socket.
    """
    value = json.loads(payload.decode("utf-8"))
    result = tabber_activity_view_result(value, max_bytes=JOBD_MAX_RESULT_BYTES - 4096)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _metadata_warm_view(payload: bytes) -> bytes:
    """Warm GitHub/Linear PR status and Linear issue metadata for a batch of sessions in a worker.

    ALL GitHub/Linear network calls and git spawns happen here, never on the web process's
    background thread. The orchestrator lives in ``metadata`` (import-safe, no app/web) so it is
    unit-testable without a broker socket.
    """
    value = json.loads(payload.decode("utf-8"))
    result = metadata_warm_view_result(value, max_bytes=JOBD_MAX_RESULT_BYTES - 4096)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _filesystem_batch(payload: bytes) -> bytes:
    """Compute one bounded Finder list/info batch outside the web process."""
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("filesystem batch payload must be an object")
    result = filesystem.filesystem_batch_result(value)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _filesystem_operation_untyped(payload: bytes) -> bytes:
    """Execute one typed filesystem snapshot descriptor outside the web process.

    This daemon is shared by every server on every port, so the descriptor's own access policy --
    captured by the server that accepted the request -- is what authorizes the path.  A descriptor
    without a parsable policy is refused; falling back to this process's environment would hand the
    caller whichever server happened to launch the daemon first.
    """
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("filesystem operation payload must be an object")
    policy = filesystem.access_policy_from_descriptor(value.get(filesystem.FS_ACCESS_POLICY_FIELD))
    with filesystem.enforce_access_policy(policy):
        return _filesystem_operation_authorized(value)


def _filesystem_operation_authorized(value: dict[str, Any]) -> bytes:
    operation = str(value.get("op") or "")
    path = str(value.get("path") or "")
    args = value.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("filesystem operation args must be an object")
    if operation == "list":
        result = filesystem.list_directory(path)
    elif operation == "read":
        result = filesystem.read_file(path)
    elif operation == "html_preview":
        result = filesystem.read_file(path)
        body = html_preview_document(
            str(result.get("content") or ""),
            path,
            str(args.get("locale") or "en"),
        ).encode("utf-8")
        return JobdTaskResult(body, {
            "format": "opaque_bytes",
            "content_type": "text/html; charset=utf-8",
            "length": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "disposition": "inline",
            "filename": "",
        })
    elif operation == "info":
        result = filesystem.path_info(path, operation="filesystem_operation.info")
    elif operation == "search":
        # Step 4: an opaque cursor selects delta mode; ``search_files`` serves committed journal
        # deltas since it (no traversal) instead of a snapshot. An absent/empty cursor is a snapshot.
        cursor = str(args.get("cursor") or "") or None
        result = filesystem.search_files(path, str(args.get("query") or ""), args.get("limit", 400), recursive=args.get("recursive") is True, cursor=cursor)
    elif operation == "index_status":
        result = filesystem.index_status(path)
    elif operation == "count":
        result = filesystem.count_directory_files(path)
    elif operation == "diff":
        result = filesystem.diff_file(path, from_ref=args.get("from_ref"), to_ref=args.get("to_ref"))
    elif operation == "blame":
        result = filesystem.blame_file(path, ref=args.get("ref"))
    elif operation == "write":
        result = filesystem.write_file(path, str(args.get("content") or ""), expected_mtime=args.get("expected_mtime"))
    elif operation == "delete":
        result = filesystem.delete_path(path)
    elif operation == "unindex":
        result = filesystem.unindex_root(path)
    elif operation == "rename":
        result = filesystem.rename_path(path, str(args.get("new_name") or ""))
    elif operation == "mkdir":
        result = filesystem.create_directory(path)
    elif operation == "raw":
        body, content_type = filesystem.read_raw(path, max_bytes=args.get("max_bytes"))
        return JobdTaskResult(body, {
            "format": "opaque_bytes",
            "content_type": content_type,
            "length": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "disposition": "attachment" if args.get("download") is True else "inline",
            "filename": product_filename(Path(path).name, fallback="download") if args.get("download") is True else "",
        })
    elif operation == "zip":
        archive, _size = filesystem.zip_directory(path, max_bytes=args.get("max_bytes"))
        try:
            body = archive.read()
        finally:
            archive.close()
        return JobdTaskResult(body, {
            "format": "opaque_bytes",
            "content_type": "application/zip",
            "length": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "disposition": "attachment",
            "filename": product_filename(args.get("filename") or f"{Path(path).name or 'archive'}.zip", fallback="archive.zip"),
        })
    else:
        raise ValueError("unsupported filesystem operation")
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _filesystem_operation(payload: bytes) -> bytes:
    """Preserve every filesystem facade failure across the jobd process boundary."""
    try:
        return _filesystem_operation_untyped(payload)
    except filesystem.FilesystemError as exc:
        value = json.loads(payload.decode("utf-8"))
        path = str(value.get("path") or "") if isinstance(value, dict) else ""
        raise JobdFilesystemOperationFailure(exc.status, exc.payload(path=path)) from exc


def _session_files_cache_prune(payload: bytes) -> bytes:
    """Prune the durable session-files cache in a jobd worker process."""
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("session-files cache prune payload must be an object")
    cache_dir = Path(str(value.get("cache_dir") or "")).expanduser()
    if not cache_dir.is_absolute() or ".." in cache_dir.parts:
        raise ValueError("session-files cache directory must be absolute and normalized")
    result = session_files.prune_disk_cache(
        cache_dir,
        max_age_seconds=float(value.get("max_age_seconds") or 0.0),
        max_bytes=int(value.get("max_bytes") or 0),
        batch_size=int(value.get("batch_size") or 1),
    )
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


REGISTERED_TASKS = {
    "filesystem_batch": _filesystem_batch,
    "filesystem_operation": _filesystem_operation,
    "indexed_repo_roots": _indexed_repo_roots,
    "json_compact": _json_compact,
    "metadata_warm_view": _metadata_warm_view,
    "session_files_cache_prune": _session_files_cache_prune,
    "session_files_view": _session_files_view,
    "tabber_activity_view": _tabber_activity_view,
    "text_facts": _text_facts,
    "transcript_view": _transcript_view,
}


@dataclass(frozen=True)
class JobdTaskResult:
    """Opaque task bytes plus the uniform product metadata retained with them."""

    body: bytes
    product: dict[str, object]


class JobdFilesystemOperationFailure(RuntimeError):
    """Preserve a worker filesystem error's HTTP payload across the process boundary."""

    def __init__(self, status: int, payload: dict[str, object]):
        super().__init__(str(payload.get("error") or "filesystem operation failed"))
        self.status = int(status)
        self.payload = dict(payload)

    def __reduce__(self) -> tuple[object, tuple[int, dict[str, object]]]:
        return type(self), (self.status, self.payload)


def _validated_product_metadata(body: bytes, product: dict[str, object]) -> dict[str, object]:
    if set(product) != {"format", "content_type", "length", "sha256", "disposition", "filename"}:
        raise ValueError("invalid product metadata fields")
    if product["format"] not in {"json", "opaque_bytes"} or product["disposition"] not in {"inline", "attachment"}:
        raise ValueError("invalid product metadata")
    if not isinstance(product["content_type"], str) or not product["content_type"]:
        raise ValueError("invalid product content type")
    if product["length"] != len(body) or not isinstance(product["sha256"], str) or product["sha256"] != hashlib.sha256(body).hexdigest():
        raise ValueError("invalid product integrity")
    if not isinstance(product["filename"], str) or "/" in product["filename"] or "\\" in product["filename"]:
        raise ValueError("invalid product filename")
    return dict(product)


def run_registered_task_result(task: str, payload: bytes) -> JobdTaskResult:
    """Executor entry point that preserves opaque task bodies for broker retention."""
    if task not in REGISTERED_TASKS:
        raise ValueError("unknown task")
    if len(payload) > JOBD_MAX_PAYLOAD_BYTES:
        raise ValueError("payload too large")
    result = REGISTERED_TASKS[task](payload)
    if isinstance(result, JobdTaskResult):
        body = result.body
        product = _validated_product_metadata(body, result.product)
    else:
        body = result
        product = inline_json_product_metadata(body)
    result_limit = JOBD_MAX_FILESYSTEM_BATCH_RESULT_BYTES if task == "filesystem_batch" else JOBD_MAX_RESULT_BYTES
    if len(body) > result_limit:
        raise ValueError("result too large")
    return JobdTaskResult(body, product)


def run_registered_task(task: str, payload: bytes) -> bytes:
    """Compatibility entry point for existing JSON-task callers."""
    return run_registered_task_result(task, payload).body


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
    product: dict[str, object] = field(default_factory=dict)
    error: str = ""
    failure: dict[str, object] = field(default_factory=dict)
    completed_at: float = 0.0
    deadline_at: float = 0.0
    running_started_at: float = 0.0
    running_started_monotonic: float = 0.0


class PersistentJobBroker:
    """One local broker with bounded spawn-only capacity for typed CPU jobs."""

    def __init__(self, socket_path: Path, idle_seconds: float = JOBD_DEFAULT_IDLE_SECONDS, workers: int | None = None):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-jobd")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = multiprocessing.get_context("spawn").Event()
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.general_worker_count = max(1, min(JOBD_MAX_WORKERS, int(workers or default_worker_count())))
        self.started_at = time.time()
        self.source_epoch = uuid.uuid4().hex
        self.last_client_at = time.monotonic()
        self.leases: dict[str, object] = {}
        self.records: dict[str, JobRecord] = {}
        self.queues = {priority: deque() for priority in JOBD_PRIORITIES}
        self.coalesced: dict[tuple[str, str], str] = {}
        self.latest_generation: dict[str, int] = {}
        # Materialized-product layer: newest completed bytes per coalesce_key (last-known-good),
        # and bounded per-task counters. These make stale-while-revalidate a broker property so a
        # web route can serve a prior complete product while a newer generation is still building.
        self.latest_product: dict[str, tuple[int, bytes, float]] = {}
        self.latest_product_metadata: dict[str, dict[str, object]] = {}
        # Scheduling facts retained beside each stored product so a completed operation can say
        # which lane ran it, how long it waited to be dispatched and how long it executed.  Without
        # this the only visible number is total wall time, which cannot distinguish a slow task from
        # a task that sat behind a lane holder -- the exact question a stalled editor open raises.
        self.latest_product_schedule: dict[str, dict[str, object]] = {}
        self.product_counters: dict[str, dict[str, int]] = {}
        # Per-task pure execution duration (excludes queue wait): count/total/max in milliseconds,
        # bounded per task name (not per job) so this dict cannot grow with job volume.
        self.product_runtime_ms: dict[str, dict[str, float]] = {}
        # Nested only by registered task and a fixed worker-owned phase vocabulary.  The broker
        # deliberately retains aggregates rather than completed product/profile payloads.
        self.product_phase_runtime_ms: dict[str, dict[str, dict[str, float]]] = {}
        self.product_work_totals: dict[str, dict[str, int]] = {}
        self.source_diagnostics: dict[str, dict[str, str | int]] = {}
        self.source_change_counters: dict[str, int] = {}
        self.session_files_accepted_requester_counters: dict[str, int] = {}
        self.session_files_requester_counters: dict[str, int] = {}
        self.request_counters: dict[str, int] = {}
        self.scheduler_pump_failures = 0
        self.scheduler_pump_last_failure: dict[str, str] = {}
        self.executors: dict[str, ProcessPoolExecutor | None] = {lane: None for lane in JOBD_LANE_PRIORITIES}
        self.state_lock = threading.RLock()
        self.scheduler_event = threading.Event()
        self.scheduler_thread: threading.Thread | None = None

    def _bump_counter(self, task: str, name: str) -> None:
        counters = self.product_counters.setdefault(task, {"accepted": 0, "coalesced": 0, "superseded": 0, "completed": 0, "failed": 0, "timed_out": 0})
        counters[name] = counters.get(name, 0) + 1

    @staticmethod
    def _session_files_requester_key(payload: dict[str, Any]) -> str:
        source = payload.get("source")
        requester = source.get("requester") if isinstance(source, dict) else None
        return requester if requester in JOBD_SESSION_FILES_REQUESTERS else "unknown"

    def _record_runtime_ms(self, task: str, elapsed_ms: float) -> None:
        stats = self.product_runtime_ms.setdefault(task, {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0})
        stats["count"] += 1
        stats["total_ms"] += elapsed_ms
        stats["max_ms"] = max(stats["max_ms"], elapsed_ms)

    def _record_phase_runtime_ms(self, task: str, result: bytes) -> None:
        if task not in {"session_files_view", "metadata_warm_view"}:
            return
        decoded = json.loads(result.decode("utf-8"))
        profile = decoded.get("profile") if isinstance(decoded, dict) else None
        phases = profile.get("phases") if isinstance(profile, dict) else None
        if task == "session_files_view" and isinstance(phases, dict):
            task_stats = self.product_phase_runtime_ms.setdefault(task, {})
            for phase, raw_stats in phases.items():
                if phase not in session_files.SESSION_FILES_VIEW_PHASES or not isinstance(raw_stats, dict):
                    continue
                count = raw_stats.get("count")
                total_ms = raw_stats.get("total_ms")
                max_ms = raw_stats.get("max_ms")
                if not isinstance(count, int) or count < 1 or not isinstance(total_ms, (int, float)) or not isinstance(max_ms, (int, float)):
                    continue
                stats = task_stats.setdefault(phase, {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0})
                stats["count"] += count
                stats["total_ms"] += max(0.0, float(total_ms))
                stats["max_ms"] = max(stats["max_ms"], max(0.0, float(max_ms)))
        work = profile.get("work") if isinstance(profile, dict) else None
        if isinstance(work, dict):
            totals = self.product_work_totals.setdefault(task, {})
            allowed_work = {
                "session_files_view": ("sessions", "repositories", "files", "git_snapshots", "git_snapshot_cache_hits", "result_bytes"),
                "metadata_warm_view": ("sessions", "entries", "git_spawns", "github_http_calls", "linear_http_calls", "result_bytes"),
            }[task]
            for name in allowed_work:
                value = work.get(name)
                if isinstance(value, int) and value >= 0:
                    totals[name] = totals.get(name, 0) + value
        if task != "session_files_view":
            return
        source = profile.get("source") if isinstance(profile, dict) else None
        if not isinstance(source, dict):
            return
        requester_key = self._session_files_requester_key({"source": source})
        self.session_files_requester_counters[requester_key] = self.session_files_requester_counters.get(requester_key, 0) + 1
        stable_view = source.get("stable_view")
        info_signature = source.get("info_signature")
        repo_signature = source.get("repo_signature")
        if not all(isinstance(value, str) and value for value in (stable_view, info_signature, repo_signature)):
            return
        prior = self.source_diagnostics.get(stable_view)
        if prior is None:
            reason = "initial"
        elif prior["repo_signature"] != repo_signature and prior["info_signature"] != info_signature:
            reason = "repository-and-metadata"
        elif prior["repo_signature"] != repo_signature:
            reason = "repository-state"
        elif prior["info_signature"] != info_signature:
            reason = "agent-or-transcript-metadata"
        else:
            reason = "same-source"
        self.source_change_counters[reason] = self.source_change_counters.get(reason, 0) + 1
        dirty_count = source.get("repo_dirty_generation_count")
        dirty_max = source.get("repo_dirty_generation_max")
        if not isinstance(dirty_count, int) or dirty_count < 0:
            dirty_count = 0
        if not isinstance(dirty_max, int) or dirty_max < 0:
            dirty_max = 0
        if prior is not None:
            dirty_changed = prior.get("repo_dirty_generation_count") != dirty_count or prior.get("repo_dirty_generation_max") != dirty_max
            dirty_key = "dirty-generation-changed" if dirty_changed else "dirty-generation-unchanged"
            self.source_change_counters[dirty_key] = self.source_change_counters.get(dirty_key, 0) + 1
        self.source_diagnostics[stable_view] = {
            "info_signature": info_signature,
            "repo_signature": repo_signature,
            "repo_dirty_generation_count": dirty_count,
            "repo_dirty_generation_max": dirty_max,
        }
        while len(self.source_diagnostics) > JOBD_MAX_SOURCE_DIAGNOSTICS:
            self.source_diagnostics.pop(next(iter(self.source_diagnostics)))

    def _store_product(self, record: JobRecord) -> None:
        # Generation guard: a slow older-generation completion must never overwrite a newer
        # complete product. A failed/superseded record never reaches here (it is terminal already).
        stored = self.latest_product.get(record.coalesce_key)
        if stored is not None and record.generation < stored[0]:
            return
        stored_at = time.time()
        self.latest_product[record.coalesce_key] = (record.generation, record.result, stored_at)
        self.latest_product_metadata[record.coalesce_key] = dict(record.product)
        self.latest_product_schedule[record.coalesce_key] = self._record_schedule(record)
        while (
            len(self.latest_product) > JOBD_MAX_PRODUCTS
            or sum(len(body) for _generation, body, _stored_at in self.latest_product.values()) > JOBD_MAX_RETAINED_RESULT_BYTES
        ):
            oldest_key = min(self.latest_product, key=lambda key: self.latest_product[key][2])
            self.latest_product.pop(oldest_key, None)
            self.latest_product_metadata.pop(oldest_key, None)
            self.latest_product_schedule.pop(oldest_key, None)

    @staticmethod
    def _record_schedule(record: JobRecord) -> dict[str, object]:
        """Return one record's bounded lane/wait/execution facts for retained diagnostics."""
        queue_wait_ms = max(0.0, (record.running_started_at - record.submitted_at) * 1000.0) if record.running_started_at > 0 else 0.0
        execution_ms = max(0.0, (record.completed_at - record.running_started_at) * 1000.0) if record.running_started_at > 0 and record.completed_at > 0 else 0.0
        return {
            "task": record.task,
            "priority": record.priority,
            "lane": PersistentJobBroker._lane_for_priority(record.priority),
            "submitted_at": round(record.submitted_at, 6),
            "running_started_at": round(record.running_started_at, 6),
            "completed_at": round(record.completed_at, 6),
            "queue_wait_ms": round(queue_wait_ms, 3),
            "execution_ms": round(execution_ms, 3),
        }

    @staticmethod
    def _lane_for_priority(priority: str) -> str:
        """Map one declared priority onto its single owning executor lane."""
        lane = JOBD_PRIORITY_LANES.get(str(priority))
        if lane is None:
            # `_validated_submission` rejects unknown priorities, so reaching here means a caller
            # built a JobRecord directly with a priority no lane runs.  Name it rather than
            # silently dispatching the work onto whichever pool happens to be first.
            raise ValueError(f"no jobd lane owns priority {priority!r}")
        return lane

    def _lane_capacity(self, lane: str) -> int:
        """Return one lane's bounded worker capacity, refusing a lane no table describes."""
        if lane not in JOBD_LANE_PRIORITIES:
            raise ValueError(f"unknown jobd lane {lane!r}")
        if lane in JOBD_LANE_WORKERS:
            return JOBD_LANE_WORKERS[lane]
        return self.general_worker_count

    @staticmethod
    def _new_executor(worker_count: int) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=worker_count, mp_context=multiprocessing.get_context("spawn"))

    def _executor(self, priority: str = "freshness") -> ProcessPoolExecutor:
        lane = self._lane_for_priority(priority)
        executor = self.executors[lane]
        if executor is None:
            executor = self._new_executor(self._lane_capacity(lane))
            self.executors[lane] = executor
        return executor

    def _shutdown_executor(self, *, lane: str) -> None:
        executor = self.executors.get(lane)
        self.executors[lane] = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _record_payload(self, record: JobRecord, *, include_result: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": record.job_id,
            "task": record.task,
            "priority": record.priority,
            "generation": record.generation,
            "status": record.status,
            "submitted_at": record.submitted_at,
            "running_started_at": record.running_started_at,
            "completed_at": record.completed_at,
            "deadline_at": record.deadline_at,
            "error": record.error,
        }
        if record.failure:
            payload["failure"] = dict(record.failure)
        if include_result and record.status == "completed" and record.product.get("format") == "json":
            payload["result"] = json.loads(record.result.decode("utf-8"))
        return payload

    def _mark_terminal(
        self,
        record: JobRecord,
        status: str,
        error: str = "",
        failure: dict[str, object] | None = None,
    ) -> None:
        record.status = status
        record.error = error
        record.failure = dict(failure or {})
        record.completed_at = time.time()

    def _future_slots(self, *, lane: str) -> int:
        """Count executor work in one lane, including timed-out work that cannot be killed safely."""
        return sum(
            1
            for record in self.records.values()
            if record.future is not None
            and not record.future.done()
            and self._lane_for_priority(record.priority) == lane
        )

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
            self._bump_counter(record.task, "timed_out")

    def _handle_finished_futures(self) -> None:
        restart_executors: set[str] = set()
        for record in self.records.values():
            if record.future is None or not record.future.done():
                continue
            future = record.future
            if record.status in {"completed", "failed", "cancelled", "superseded", "timed_out"}:
                record.future = None
                continue
            try:
                task_result = future.result()
                if isinstance(task_result, bytes):
                    task_result = JobdTaskResult(task_result, inline_json_product_metadata(task_result))
                result = task_result.body
                result_limit = JOBD_MAX_FILESYSTEM_BATCH_RESULT_BYTES if record.task == "filesystem_batch" else JOBD_MAX_RESULT_BYTES
                if len(result) > result_limit:
                    raise ValueError("result too large")
                if task_result.product.get("format") == "json":
                    json.loads(result.decode("utf-8"))
                if record.status != "timed_out":
                    record.result = result
                    record.product = dict(task_result.product)
                    self._mark_terminal(record, "completed")
                    self._store_product(record)
                    self._bump_counter(record.task, "completed")
                    self._record_phase_runtime_ms(record.task, result)
                    if record.running_started_monotonic > 0:
                        self._record_runtime_ms(record.task, (time.monotonic() - record.running_started_monotonic) * 1000.0)
            except JobdFilesystemOperationFailure as exc:
                if record.status != "timed_out":
                    self._mark_terminal(record, "failed", str(exc), {
                        "filesystem_error": dict(exc.payload),
                        "status": exc.status,
                    })
                    self._bump_counter(record.task, "failed")
            except BrokenProcessPool:
                if record.status != "timed_out":
                    error = BrokenProcessPool("worker crashed")
                    self._mark_terminal(
                        record,
                        "failed",
                        "worker crashed",
                        local_service_exception_cause(error),
                    )
                    self._bump_counter(record.task, "failed")
                    if record.running_started_monotonic > 0:
                        self._record_runtime_ms(record.task, (time.monotonic() - record.running_started_monotonic) * 1000.0)
                restart_executors.add(self._lane_for_priority(record.priority))
            except (OSError, RuntimeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                if record.status != "timed_out":
                    self._mark_terminal(
                        record,
                        "failed",
                        redact_local_service_text(exc),
                        local_service_exception_cause(exc),
                    )
                    self._bump_counter(record.task, "failed")
                    if record.running_started_monotonic > 0:
                        self._record_runtime_ms(record.task, (time.monotonic() - record.running_started_monotonic) * 1000.0)
            except Exception as exc:
                # A worker's task failure is data-plane state, not a scheduler failure. Keep the
                # broker serving unrelated work and preserve a typed, redacted reason for the
                # requester instead of letting ``future.result()`` escape through ``on_idle``.
                if record.status != "timed_out":
                    self._mark_terminal(
                        record,
                        "failed",
                        f"{type(exc).__name__}: {redact_local_service_text(exc)}",
                        local_service_exception_cause(exc),
                    )
                    self._bump_counter(record.task, "failed")
                    if record.running_started_monotonic > 0:
                        self._record_runtime_ms(record.task, (time.monotonic() - record.running_started_monotonic) * 1000.0)
        for lane in restart_executors:
            self._shutdown_executor(lane=lane)

    def _next_queued_record(self, priorities: tuple[str, ...]) -> JobRecord | None:
        for priority in priorities:
            queue = self.queues[priority]
            while queue:
                record = self.records.get(queue[0])
                if record is None or record.status != "queued":
                    queue.popleft()
                    continue
                queue.popleft()
                return record
        return None

    def _prune_records(self) -> None:
        terminal = sorted((record for record in self.records.values() if record.status in {"completed", "failed", "cancelled", "superseded", "timed_out"}), key=lambda record: record.completed_at)
        remove_count = max(0, len(self.records) - JOBD_MAX_RECORDS)
        retained_result_bytes = sum(len(record.result) for record in self.records.values())
        while remove_count < len(terminal) and retained_result_bytes > JOBD_MAX_RETAINED_RESULT_BYTES:
            retained_result_bytes -= len(terminal[remove_count].result)
            remove_count += 1
        for record in terminal[:remove_count]:
            self.records.pop(record.job_id, None)
            if self.coalesced.get((record.task, record.coalesce_key)) == record.job_id:
                self.coalesced.pop((record.task, record.coalesce_key), None)

    def _refresh_records(self) -> None:
        now = time.monotonic()
        self._expire_deadlines(now)
        self._handle_finished_futures()
        self._prune_records()

    def _pump(self) -> None:
        dispatch: list[JobRecord] = []
        with self.state_lock:
            self._refresh_records()
            now = time.monotonic()
            for lane, priorities in JOBD_LANE_PRIORITIES.items():
                capacity = self._lane_capacity(lane)
                active = self._future_slots(lane=lane)
                while active < capacity:
                    record = self._next_queued_record(priorities)
                    if record is None:
                        break
                    if record.generation < self.latest_generation.get(record.coalesce_key, record.generation):
                        self._mark_terminal(record, "superseded")
                        self._bump_counter(record.task, "superseded")
                        continue
                    if record.deadline_at > 0 and now >= record.deadline_at:
                        self._mark_terminal(record, "timed_out", "deadline exceeded before execution")
                        continue
                    # Mark the record in flight before starting cold process capacity. Product reads
                    # can now return `pending` without waiting for ProcessPoolExecutor startup.
                    record.status = "running"
                    record.running_started_at = time.time()
                    record.running_started_monotonic = time.monotonic()
                    dispatch.append(record)
                    active += 1
        for record in dispatch:
            try:
                future = self._executor(record.priority).submit(run_registered_task_result, record.task, record.payload)
            except Exception as exc:
                with self.state_lock:
                    self._mark_terminal(
                        record,
                        "failed",
                        redact_local_service_text(exc),
                        local_service_exception_cause(exc),
                    )
                    self._bump_counter(record.task, "failed")
                    if isinstance(exc, BrokenProcessPool):
                        self._shutdown_executor(lane=self._lane_for_priority(record.priority))
                continue
            with self.state_lock:
                record.future = future
            # Queue submission wakes the scheduler, but worker completion otherwise waits for the
            # 50 ms maintenance poll before `_handle_finished_futures` can publish the product.
            future.add_done_callback(lambda _completed: self.scheduler_event.set())

    def _record_scheduler_pump_failure(self, exc: Exception, traceback_text: str) -> None:
        self.scheduler_pump_failures += 1
        self.scheduler_pump_last_failure = {
            "exception_type": type(exc).__name__,
            "reason": redact_local_service_text(exc),
            "traceback": redact_local_service_text(traceback_text),
        }

    def _queue_record(self, task: str, payload: dict[str, Any], priority: str, generation: int, coalesce_key: str, deadline_at: float = 0.0) -> JobRecord:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        record = JobRecord(uuid.uuid4().hex, task, encoded, priority, generation, coalesce_key, time.time(), deadline_at=deadline_at)
        self.records[record.job_id] = record
        self.coalesced[(task, coalesce_key)] = record.job_id
        self.queues[priority].append(record.job_id)
        self.scheduler_event.set()
        return record

    def _supersede_stale_queued(self, coalesce_key: str, generation: int) -> None:
        for record in self.records.values():
            if record.coalesce_key == coalesce_key and record.status == "queued" and record.generation < generation:
                record.status = "superseded"
                record.completed_at = time.time()
                self._bump_counter(record.task, "superseded")

    def _queued_count(self, *, lane: str | None = None) -> int:
        """Count queued records, globally or within one lane.

        The lane-scoped count is what admission backpressure uses: a global cap ahead of the
        per-lane executors let a full bulk/freshness queue refuse an idle `point` read as
        `queue full` while the point lane read capacity 2, active 0.  A per-lane cap keeps each
        lane bounded (the overall backpressure intent) without one lane's queue starving another's
        admission.  The global count remains the right question for idle retirement.
        """
        if lane is None:
            return sum(1 for record in self.records.values() if record.status == "queued")
        return sum(
            1
            for record in self.records.values()
            if record.status == "queued" and self._lane_for_priority(record.priority) == lane
        )

    @staticmethod
    def _validated_submission(request: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        task = str(request.get("task") or "")
        priority = str(request.get("priority") or "normal")
        if priority == "normal":
            priority = "freshness"
        if task not in REGISTERED_TASKS:
            return None, {"ok": False, "error": "unknown task"}
        if priority not in JOBD_PRIORITIES:
            return None, {"ok": False, "error": "invalid priority"}
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return None, {"ok": False, "error": "payload must be an object"}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > JOBD_MAX_PAYLOAD_BYTES:
            return None, {"ok": False, "error": "payload too large"}
        try:
            generation = max(0, int(request.get("generation") or 0))
            requested_deadline_ms = int(request.get("deadline_ms") or 0)
        except (TypeError, ValueError):
            return None, {"ok": False, "error": "invalid generation or deadline"}
        if requested_deadline_ms < 0:
            return None, {"ok": False, "error": "invalid deadline"}
        if requested_deadline_ms > JOBD_MAX_DEADLINE_MS:
            return None, {"ok": False, "error": "deadline too large"}
        coalesce_key = str(request.get("coalesce_key") or f"{task}:{encoded.hex()}")[:256]
        return {
            "task": task,
            "priority": priority,
            "payload": payload,
            "generation": generation,
            "deadline_ms": requested_deadline_ms,
            "deadline_at": time.monotonic() + (requested_deadline_ms / 1000.0) if requested_deadline_ms else 0.0,
            "coalesce_key": coalesce_key,
            # `fresh_only` means the caller's coalesce key is only as trustworthy as its own
            # granularity, so a completed product carrying that key may describe content that has
            # since changed underneath it. Such a submission still joins in-flight work for the same
            # key -- that work has not produced anything yet, so it cannot be stale -- but it never
            # accepts an already-stored product.
            "fresh_only": request.get("fresh_only") is True,
        }, None

    def _submit_validated(self, submission: dict[str, Any]) -> dict[str, Any]:
        task = str(submission["task"])
        priority = str(submission["priority"])
        payload = submission["payload"]
        generation = int(submission["generation"])
        deadline_at = float(submission["deadline_at"])
        coalesce_key = str(submission["coalesce_key"])
        fresh_only = submission.get("fresh_only") is True
        reusable_states = {"queued", "running"} if fresh_only else {"queued", "running", "completed"}
        existing_id = self.coalesced.get((task, coalesce_key))
        existing = self.records.get(existing_id or "")
        if existing is not None and existing.generation >= generation and existing.status in reusable_states:
            self._bump_counter(task, "coalesced")
            return {"ok": True, "coalesced": True, "job": self._record_payload(existing)}
        if self._queued_count(lane=self._lane_for_priority(priority)) >= JOBD_MAX_QUEUE:
            return {"ok": False, "error": "queue full"}
        self.latest_generation[coalesce_key] = max(generation, self.latest_generation.get(coalesce_key, generation))
        self._supersede_stale_queued(coalesce_key, generation)
        record = self._queue_record(task, payload, priority, generation, coalesce_key, deadline_at)
        self._bump_counter(task, "accepted")
        if task == "session_files_view":
            requester_key = self._session_files_requester_key(payload)
            counters = self.session_files_accepted_requester_counters
            counters[requester_key] = counters.get(requester_key, 0) + 1
        return {"ok": True, "coalesced": False, "job": self._record_payload(record)}

    def _submit(self, request: dict[str, Any]) -> dict[str, Any]:
        self._refresh_records()
        submission, error = self._validated_submission(request)
        if submission is None:
            return error or {"ok": False, "error": "invalid submission"}
        return self._submit_validated(submission)

    def _product(self, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        coalesce_key = str(request.get("coalesce_key") or "")
        if not coalesce_key:
            return {"ok": False, "error": "missing coalesce_key"}, b""
        stored = self.latest_product.get(coalesce_key)
        latest_gen = self.latest_generation.get(coalesce_key, 0)
        inflight = any(record.coalesce_key == coalesce_key and record.status in {"queued", "running"} for record in self.records.values())
        if stored is None:
            # `pending`: a job is building the first product. `none`: a successful lookup with
            # nothing in flight and nothing ever produced (distinct from an RPC failure = unavailable).
            return {"ok": True, "state": "pending" if inflight else "none", "generation": 0, "inflight": inflight}, b""
        generation, body, stored_at = stored
        product = self.latest_product_metadata.get(coalesce_key)
        if product is None:
            return {"ok": False, "error": "retained product metadata missing"}, b""
        state = "stale" if (inflight or latest_gen > generation) else "ready"
        return {
            "ok": True,
            "state": state,
            "generation": generation,
            "source_epoch": self.source_epoch,
            "stored_at": stored_at,
            "inflight": inflight,
            "product": dict(product),
            "schedule": dict(self.latest_product_schedule.get(coalesce_key) or {}),
        }, body

    def _produce(self, request: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        """Submit one product job and return materialized bytes or its accepted receipt.

        This is intentionally a zero-wait operation. The broker can atomically submit and inspect
        its product store, but waiting here would hold one of JOBD_CONCURRENT_HANDLER_LIMIT
        handler slots for the whole job; enough of those and later callers are refused with
        `service busy` instead of being served. No jobd action waits: a caller with no receipt
        protocol (a browser byte download) submits with `delivery="ready_or_receipt"` and polls
        `product` on its own side. Result bytes remain opaque so a bounded batch keeps every item
        id and result exactly as its registered task emitted them.
        """
        delivery = str(request.get("delivery") or "ready_or_receipt")
        if delivery not in JOBD_PRODUCT_DELIVERY_MODES:
            return {"ok": False, "error": "invalid product delivery mode"}, b""
        self._refresh_records()
        submission, error = self._validated_submission(request)
        if submission is None:
            return error or {"ok": False, "error": "invalid submission"}, b""
        coalesce_key = str(submission["coalesce_key"])
        stored = self.latest_product.get(coalesce_key)
        if submission.get("fresh_only") is not True and delivery == "ready_or_receipt" and stored is not None and stored[0] >= int(submission["generation"]):
            product_response, body = self._product({"coalesce_key": coalesce_key})
            state = str(product_response.get("state") or "")
            if body and (state == "ready" or request.get("allow_stale") is True):
                existing_id = self.coalesced.get((str(submission["task"]), coalesce_key))
                existing = self.records.get(existing_id or "")
                job = self._record_payload(existing) if existing is not None else {
                    "job_id": "",
                    "task": str(submission["task"]),
                    "priority": str(submission["priority"]),
                    "generation": int(stored[0]),
                    "status": "completed",
                    "submitted_at": 0.0,
                    "running_started_at": 0.0,
                    "completed_at": float(stored[2]),
                    "deadline_at": 0.0,
                    "error": "",
                }
                return {
                    "ok": True,
                    "state": state,
                    "coalesced": True,
                    "job": job,
                    "product": dict(product_response.get("product") or {}),
                    "schedule": dict(product_response.get("schedule") or {}),
                }, body
        submitted = self._submit_validated(submission)
        if submitted.get("ok") is not True:
            return submitted, b""
        job = submitted.get("job") if isinstance(submitted.get("job"), dict) else {}
        product_meta: dict[str, Any] = {
            "coalesce_key": coalesce_key,
            "generation": 0,
            "inflight": str(job.get("status") or "") in {"queued", "running"},
        }
        response = {
            "ok": True,
            "state": "queued",
            "coalesced": bool(submitted.get("coalesced")),
            "job": job,
            "product": product_meta,
        }
        if delivery == "receipt":
            return response, b""
        product_response, body = self._product(request)
        if product_response.get("ok") is not True:
            return product_response, b""
        product_meta.update({
            "generation": int(product_response.get("generation") or 0),
            "stored_at": float(product_response.get("stored_at") or 0.0),
            "inflight": bool(product_response.get("inflight")),
        })
        state = str(product_response.get("state") or "")
        allow_stale = request.get("allow_stale") is True
        if body and (state == "ready" or (allow_stale and state == "stale")):
            response["state"] = state
            response["product"] = dict(product_response.get("product") or {})
            response["schedule"] = dict(product_response.get("schedule") or {})
            return response, body
        return response, b""

    def common_status(self) -> dict[str, Any]:
        self._refresh_records()
        active_records = [
            self._record_payload(record)
            for record in self.records.values()
            if record.status == "running"
        ]
        worker_pids = sorted({
            int(process.pid)
            for executor in self.executors.values()
            if executor is not None
            for process in executor._processes.values()
            if process.pid is not None
        })
        return {
            "ok": True,
            "version": JOBD_PROTOCOL_VERSION,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "source_epoch": self.source_epoch,
            "socket": str(self.socket_path),
            "clients": len(self.leases),
            "worker_count": sum(self._lane_capacity(lane) for lane in JOBD_LANE_PRIORITIES),
            # One row per scheduler lane: its bounded capacity and the work actually occupying it.
            # A lane at capacity with a nonzero queue is head-of-line blocking, which total wall
            # time alone cannot show.
            "lanes": {
                lane: {
                    "capacity": self._lane_capacity(lane),
                    "active": self._future_slots(lane=lane),
                    "queued": sum(
                        1
                        for priority in priorities
                        for job_id in self.queues[priority]
                        if (self.records.get(job_id) is not None and self.records[job_id].status == "queued")
                    ),
                }
                for lane, priorities in JOBD_LANE_PRIORITIES.items()
            },
            "queues": {priority: sum(1 for job_id in queue if self.records.get(job_id, JobRecord("", "", b"", priority, 0, "", 0)).status == "queued") for priority, queue in self.queues.items()},
            "active_task": next((record.task for record in self.records.values() if record.status == "running"), ""),
            "active_records": active_records,
            "worker_pids": worker_pids,
            "cache": {
                "records": len(self.records), "coalesced": len(self.coalesced), "record_limit": JOBD_MAX_RECORDS,
                "products": len(self.latest_product),
                # A stored product is "stale" when a newer generation for the same coalesce_key has
                # since been observed (queued, running, or already completed elsewhere) -- an honest
                # age/staleness signal without exposing raw product bytes/keys in diagnostics.
                "products_stale": sum(1 for key, (generation, _body, _stored_at) in self.latest_product.items() if self.latest_generation.get(key, generation) > generation),
            },
            "product_counters": {task: dict(counters) for task, counters in self.product_counters.items()},
            "product_runtime_ms": {
                task: {"count": int(stats["count"]), "total_ms": round(stats["total_ms"], 3), "max_ms": round(stats["max_ms"], 3), "avg_ms": round(stats["total_ms"] / stats["count"], 3) if stats["count"] else 0.0}
                for task, stats in self.product_runtime_ms.items()
            },
            "product_phase_runtime_ms": {
                task: {
                    phase: {"count": int(stats["count"]), "total_ms": round(stats["total_ms"], 3), "max_ms": round(stats["max_ms"], 3), "avg_ms": round(stats["total_ms"] / stats["count"], 3) if stats["count"] else 0.0}
                    for phase, stats in phases.items()
                }
                for task, phases in self.product_phase_runtime_ms.items()
            },
            "product_work_totals": {task: dict(totals) for task, totals in self.product_work_totals.items()},
            "source_change_counters": dict(self.source_change_counters),
            "session_files_accepted_requester_counters": dict(self.session_files_accepted_requester_counters),
            "session_files_requester_counters": dict(self.session_files_requester_counters),
            "request_counters": dict(self.request_counters),
            "last_success": max((record.completed_at for record in self.records.values() if record.status == "completed"), default=0.0),
            # A retained WORK-ITEM failure, not a daemon condition. This scans the bounded record
            # ring, so one failed or timed-out job keeps describing a daemon that has served every
            # request since, and only ring eviction ever drops it -- a later success does not.
            # It must therefore never be published as `last_failure`: `local_service_failure_text`
            # feeds that name to `observed_health`, which reads any `last_failure` on a live pid as
            # CURRENT degradation and pins a healthy jobd to `degraded`/`terminal_failure` forever.
            # The daemon's own current trouble travels as the registry's `failure_reason` instead.
            "last_job_failure": next((record.error for record in reversed(list(self.records.values())) if record.status in {"failed", "timed_out"}), ""),
            "scheduler_pump": {
                "failures": self.scheduler_pump_failures,
                "last_failure": dict(self.scheduler_pump_last_failure),
            },
            "restart_backoff_seconds": 0.0,
            "generation": max(self.latest_generation.values(), default=0),
            "idle_seconds": self.idle_seconds,
        }

    def handle(self, request: dict[str, object], _request_binary: bytes = b"") -> tuple[dict[str, object], bytes]:
        with self.state_lock:
            return self._handle_locked(request)

    def _handle_locked(self, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        protocol_version = request.get("protocol_version", JOBD_PROTOCOL_VERSION)
        if protocol_version != JOBD_PROTOCOL_VERSION:
            return {
                "ok": False,
                "error": "upgrade_required",
                "required_protocol_version": JOBD_PROTOCOL_VERSION,
            }, b""
        action = str(request.get("action") or "")
        action_counter = action if action in JOBD_REQUEST_ACTIONS else "unknown"
        self.request_counters[action_counter] = self.request_counters.get(action_counter, 0) + 1
        if action == "ping":
            return {"ok": True, "version": JOBD_PROTOCOL_VERSION, "pid": os.getpid(), "started_at": self.started_at, "source_epoch": self.source_epoch}, b""
        if action == "status":
            return self.common_status(), b""
        if action == "profile":
            return {"ok": True, "profile": self.common_status()}, b""
        if action == "submit":
            return self._submit(request), b""
        if action == "result":
            self._refresh_records()
            record = self.records.get(str(request.get("job_id") or ""))
            return ({"ok": False, "error": "unknown job"} if record is None else {"ok": True, "job": self._record_payload(record, include_result=True)}), b""
        if action == "product":
            self._refresh_records()
            return self._product(request)
        if action == "produce":
            return self._produce(request)
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
            response = acquire_client_lease(
                self.leases,
                request.get("client_pid"),
                request.get("lease_id"),
            )
            return {**response, "version": JOBD_PROTOCOL_VERSION}, b""
        if action == "release":
            return release_client_lease(self.leases, request.get("lease_id")), b""
        if action in {"shutdown", "shutdown_if_idle"}:
            if action == "shutdown_if_idle" and self.leases:
                return {"ok": True, "shutdown": False, "leases": len(self.leases)}, b""
            self.stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        return {"ok": False, "error": "unknown jobd action"}, b""

    def _scheduler_loop(self) -> None:
        while not self.stop_event.is_set():
            self.scheduler_event.wait(JOBD_SCHEDULER_POLL_SECONDS)
            self.scheduler_event.clear()
            try:
                self._pump()
            except Exception as exc:
                self._record_scheduler_pump_failure(exc, traceback.format_exc())

    def _start_scheduler(self) -> None:
        if self.scheduler_thread is not None and self.scheduler_thread.is_alive():
            return
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="jobd-scheduler",
            daemon=True,
        )
        self.scheduler_thread.start()

    def _idle_should_stop(self) -> bool:
        with self.state_lock:
            return (
                not self.leases
                and not self._queued_count()
                and not any(record.status == "running" for record in self.records.values())
                and time.monotonic() - self.last_client_at >= self.idle_seconds
            )

    def _on_shutdown(self) -> None:
        self.scheduler_event.set()
        if self.scheduler_thread is not None:
            self.scheduler_thread.join(timeout=0.5)
        for lane in JOBD_LANE_PRIORITIES:
            self._shutdown_executor(lane=lane)

    def run(self) -> int:
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="jobd",
            stop_event=self.stop_event,
            handle=self.handle,
            on_idle=self._idle_should_stop,
            on_client=lambda: setattr(self, "last_client_at", time.monotonic()),
            on_idle_failure=self._record_scheduler_pump_failure,
            on_start=self._start_scheduler,
            on_shutdown=self._on_shutdown,
            concurrent_handlers=JOBD_CONCURRENT_HANDLER_LIMIT,
        )


class JobClient(LocalServiceClient):
    """Thin cross-port client for the shared stateless CPU broker."""

    def __init__(self, socket_path: Path | None = None):
        super().__init__(
            "jobd",
            "yolomux_lib.jobd",
            socket_path or default_socket_path(),
            JOBD_PROTOCOL_VERSION,
            idle_seconds=JOBD_DEFAULT_IDLE_SECONDS,
            service_dir=Path(socket_path).parent if socket_path is not None else RUNTIME_DIR / "services",
        )
        self._scheduler_lease_id = ""
        self._scheduler_lease_lock = threading.Lock()

    def start_for_scheduler(self) -> bool:
        """Keep jobd leased while this process owns background scheduling."""
        with self._scheduler_lease_lock:
            response = self.registry.acquire_lease(self._scheduler_lease_id)
            lease_id = response.get("lease_id")
            if response.get("ok") is not True or not isinstance(lease_id, str) or not lease_id:
                return False
            self._scheduler_lease_id = lease_id
            return True

    @property
    def holds_scheduler_lease(self) -> bool:
        """Whether this process currently pins jobd up for background scheduling.

        Deliberately NOT taken under ``_scheduler_lease_lock``. ``start_for_scheduler`` holds
        that lock across ``registry.acquire_lease()``, which can spawn the broker and wait for
        its socket; blocking a bounded health probe behind a service spawn would turn a healthy
        start into a probe timeout. Rebinding a str attribute is atomic, so the worst a lock-free
        read can see is the value from just before or just after the acquire -- and both are
        true statements about a lease that is in the act of being taken.
        """
        return bool(self._scheduler_lease_id)

    def stop_for_scheduler(self) -> bool:
        """Release the scheduler lease when this process is demoted."""
        with self._scheduler_lease_lock:
            if not self._scheduler_lease_id:
                return True
            response = self.registry.release_lease(self._scheduler_lease_id)
            if response.get("ok") is not True:
                return False
            self._scheduler_lease_id = ""
            return True

    def submit(self, task: str, payload: dict[str, Any], *, priority: str = "freshness", generation: int = 0, coalesce_key: str = "", deadline_ms: int = 0) -> dict[str, Any]:
        return self.request({"action": "submit", "task": task, "payload": payload, "priority": priority, "generation": generation, "coalesce_key": coalesce_key, "deadline_ms": deadline_ms})

    def result(self, job_id: str) -> dict[str, Any]:
        return self.request({"action": "result", "job_id": job_id})

    def product(self, coalesce_key: str, timeout: float = 0.5) -> tuple[dict[str, Any], bytes]:
        """Return the newest completed product bytes for an identity (last-known-good).

        The metadata `state` is ready | stale | pending | none; the caller maps a transport
        failure to unavailable. Bytes are empty unless a completed product exists.
        """
        return self.request_with_binary({"action": "product", "coalesce_key": coalesce_key}, timeout=timeout)

    def produce(
        self,
        task: str,
        payload: dict[str, Any],
        *,
        priority: str = "freshness",
        generation: int = 0,
        coalesce_key: str = "",
        deadline_ms: int = 0,
        delivery: str = "ready_or_receipt",
        allow_stale: bool = False,
        fresh_only: bool = False,
        timeout: float = 0.5,
    ) -> tuple[dict[str, Any], bytes]:
        """Submit once and forward ready product bytes without waiting for cold work."""
        return self.request_with_binary({
            "action": "produce",
            "task": task,
            "payload": payload,
            "priority": priority,
            "generation": generation,
            "coalesce_key": coalesce_key,
            "deadline_ms": deadline_ms,
            "delivery": delivery,
            "allow_stale": bool(allow_stale),
            "fresh_only": bool(fresh_only),
        }, timeout=timeout)

    def runtime_status(self) -> dict[str, Any]:
        """Build jobd's whole System/health row.

        No ``demand_started`` here on purpose: the scheduler lease pins jobd up, so its absence
        while this process owns scheduling is a verified outage, not idleness. See
        ``JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE`` for the one absence that is expected instead.
        """
        status = self.registry.status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        pid = int(payload.get("pid") or 0)
        worker_pids: list[int] = []
        if isinstance(payload.get("worker_pids"), list):
            for value in payload["worker_pids"]:
                try:
                    worker_pid = int(value)
                except (TypeError, ValueError):
                    continue
                if worker_pid > 0:
                    worker_pids.append(worker_pid)
        return {"service": "jobd", "pid": pid, "started_at": float(payload.get("started_at") or 0.0), "healthy": bool(status.get("healthy")), "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {}, "active_task": str(payload.get("active_task") or ""), "active_records": payload.get("active_records") if isinstance(payload.get("active_records"), list) else [], "worker_count": int(payload.get("worker_count") or len(worker_pids)), "worker_pids": worker_pids, "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {}, "product_counters": payload.get("product_counters") if isinstance(payload.get("product_counters"), dict) else {}, "product_runtime_ms": payload.get("product_runtime_ms") if isinstance(payload.get("product_runtime_ms"), dict) else {}, "product_phase_runtime_ms": payload.get("product_phase_runtime_ms") if isinstance(payload.get("product_phase_runtime_ms"), dict) else {}, "product_work_totals": payload.get("product_work_totals") if isinstance(payload.get("product_work_totals"), dict) else {}, "source_change_counters": payload.get("source_change_counters") if isinstance(payload.get("source_change_counters"), dict) else {}, "session_files_accepted_requester_counters": payload.get("session_files_accepted_requester_counters") if isinstance(payload.get("session_files_accepted_requester_counters"), dict) else {}, "session_files_requester_counters": payload.get("session_files_requester_counters") if isinstance(payload.get("session_files_requester_counters"), dict) else {}, "request_counters": payload.get("request_counters") if isinstance(payload.get("request_counters"), dict) else {}, "generation": int(payload.get("generation") or 0), "last_success": float(payload.get("last_success") or 0.0), "last_failure": local_service_failure_text(status, payload), "last_job_failure": str(payload.get("last_job_failure") or ""), "scheduler_pump": payload.get("scheduler_pump") if isinstance(payload.get("scheduler_pump"), dict) else {}, "absence_expected_reason": "" if self.holds_scheduler_lease else JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE, "resources": self.registry.resources_for_pids(pid, worker_pids)}


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
