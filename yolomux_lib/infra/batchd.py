"""Bounded stateless batch broker for YOLOmux background transforms.

The web process submits only registered, immutable JSON payloads.  ``batchd``
owns priority ordering, coalescing, cancellation, and bounded spawn-based
executor capacity so CPU-bound Python work cannot run in HTTP request threads.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import multiprocessing
import os
import stat
import tempfile
import threading
import time
import traceback
import uuid
from collections import deque
from collections.abc import Iterator
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
from ..local_service_projection import registry_runtime_row
from ..workspace import session_files
from ..observability.activity_summary import tabber_activity_view_result
from ..observability.queued_delivery import compact_queued_delivery_journal
from .common import RUNTIME_DIR
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import inline_json_product_metadata
from .common import product_filename
from .common import tail_file_lines
from ..local_services.rpc import LOCAL_RPC_MAX_BINARY_BYTES
from ..local_services.rpc import LOCAL_SERVICE_ERROR_BUSY
from ..local_services.rpc import LOCAL_RPC_VERSION, new_envelope, request as local_service_request, safe_socket_path  # noqa: F401 - public transport-version compatibility export
from ..local_services.runtime import LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT
from ..local_services.runtime import acquire_client_lease
from ..local_services.runtime import request_is_self_connection
from ..local_services.runtime import apply_service_process_priority
from ..local_services.runtime import claim_gated_idle_due
from ..local_services.runtime import local_service_exception_cause
from ..local_services.runtime import redact_local_service_text
from ..local_services.runtime import reap_dead_client_leases
from ..local_services.runtime import release_client_lease
from ..local_services.runtime import run_local_rpc_service
from ..local_services.client import LocalServiceClient
from ..local_services.command_router import CommonDaemonActions
from ..local_services.command_router import LocalServiceCommandRouter
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
# and a v21 web process sending `relay` to a v22 daemon gets `unknown batchd action`, so the fence
# retires the mismatched pair.
# v23: raw and zip products are private, file-backed artifacts consumed through bounded chunk
# leases. A v22 peer can only retain/return whole byte products, so mixed peers must be fenced.
# v24: queued-delivery journal compaction runs as registered maintenance work rather than burning
# request-thread CPU. A v23 daemon rejects that task, so an upgraded web process must retire it.
# v25: shutdown admission refusal identifies itself as pre-handler busy so clients can retry it.
# v26: each scheduler lane owns independently replaceable one-worker slots.  A deadline-backstop
# quarantine fences a kernel-stuck slot without taking its healthy lane siblings down.
# A v24 daemon returns an indistinguishable generic busy response and must not remain attached.
BATCHD_PROTOCOL_VERSION = 26
BATCHD_DEFAULT_IDLE_SECONDS = 60.0
BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS = 0.5
BATCHD_SERVICE_NAME = "batchd"

# batchd is NOT demand-scoped, so it must never declare `demand_started`. The elected background
# owner pins it up with a registry lease (`BatchClient.start_for_scheduler`, called at
# `app.py:2962` when this process acquires background ownership and released at `app.py:3381`
# on demote), and `_idle_should_stop` refuses to retire the broker while any lease is held. A
# process that owns scheduling and cannot see batchd is looking at a real outage.
#
# The one legitimate absence is the other side of that same lease: before this process wins the
# election, or when it never does, nothing here is scheduling and batchd is expected to be absent.
# That is a DYNAMIC fact about this process, not a static property of the service, so it is
# published as a bounded `absence_expected_reason` token and NOT as `demand_started` -- see
# `yolomux_lib/backend_health/observer.py:ABSENCE_EXPECTED_REASON_FIELD`.
BATCHD_ABSENT_WITHOUT_SCHEDULER_LEASE = "scheduler_not_owned"
# No batchd handler waits by contract anymore: every action is zero-wait.  `produce` atomically
# submits and inspects the product store and returns a receipt; the web process polls `product`
# for cold work on its own side (the former blocking `relay` action, which held one handler slot
# for the whole job, has been retired).  So batchd takes the same shared concurrency limit as
# watchd and statusd, and no cheap last-known-good `product` read is charged for another client's
# in-flight job.
BATCHD_CONCURRENT_HANDLER_LIMIT = LOCAL_SERVICE_CONCURRENT_HANDLER_LIMIT
# One scheduler owner, three explicitly bounded lanes.  Every declared priority maps to exactly
# one executor through BATCHD_PRIORITY_LANES, and BATCHD_PRIORITIES is derived from that same table so
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
# `delete` is admitted here ONLY in its bounded form (`recursive` absent/false): one `unlink`, or
# one `rmdir` probe that returns a typed `pending: "subtree"` WITHOUT enumerating anything.  A
# recursive `delete` walks and unlinks a whole subtree -- measured at 20,001 destructive syscalls
# for one 20,000-entry directory -- so it stays on the bulk-shared `interactive` lane.
BATCHD_MAX_WORKERS = 2
BATCHD_INTERACTIVE_WORKERS = 1
BATCHD_POINT_WORKERS = 2
BATCHD_MUTATION_WORKERS = 2
BATCHD_LANE_PRIORITIES: dict[str, tuple[str, ...]] = {
    "point": ("point",),
    "mutation": ("mutation",),
    "interactive": ("interactive",),
    "bulk": ("freshness", "maintenance"),
}
BATCHD_PRIORITY_LANES: dict[str, str] = {
    priority: lane for lane, priorities in BATCHD_LANE_PRIORITIES.items() for priority in priorities
}
# Fixed per-lane worker capacity.  `bulk` is deliberately absent: its capacity is the instance's
# general worker count, derived from the host CPU count when the broker is constructed.
BATCHD_LANE_WORKERS: dict[str, int] = {
    "point": BATCHD_POINT_WORKERS,
    "mutation": BATCHD_MUTATION_WORKERS,
    "interactive": BATCHD_INTERACTIVE_WORKERS,
}
BATCHD_SESSION_FILES_REQUESTERS = frozenset({
    "api-session-files", "api-session-files-batch", "background-refresh",
    "background-info-refresh", "metadata-cache-miss", "metadata-follower-fallback",
})
BATCHD_MAX_QUEUE = 64
BATCHD_MAX_PAYLOAD_BYTES = 256 * 1024
BATCHD_MAX_RESULT_BYTES = 512 * 1024
BATCHD_MAX_FILESYSTEM_BATCH_RESULT_BYTES = LOCAL_RPC_MAX_BINARY_BYTES
BATCHD_MAX_RETAINED_RESULT_BYTES = 32 * 1024 * 1024
BATCHD_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
BATCHD_MAX_ARTIFACTS = 64
BATCHD_MAX_ARTIFACT_LEASES = 128
BATCHD_ARTIFACT_CHUNK_BYTES = 1024 * 1024
BATCHD_ARTIFACT_LEASE_SECONDS = 120.0
BATCHD_MAX_RECORDS = 256
# The last-known-good product store is keyed by coalesce_key (per file/session), so bound it
# independently of the job-record ring and evict the oldest completed bytes past this many keys.
BATCHD_MAX_PRODUCTS = 256
BATCHD_MAX_SOURCE_DIAGNOSTICS = 256
BATCHD_MAX_DEADLINE_MS = 120_000
BATCHD_SCHEDULER_POLL_SECONDS = 0.05
# Shutdown must not enumerate executor children while the scheduler can still submit another
# one. This bound covers a scheduler already across the lock-to-submit boundary.
BATCHD_SCHEDULER_SHUTDOWN_SECONDS = 2.0
# How long past its deadline a RUNNING job may still be, before the broker terminalizes it without
# its worker's answer.  A worker that honors its deadline stops by itself and owns the terminal
# state, so this is a backstop for work that cannot stop -- an uninterruptible syscall on a wedged
# mount -- and never the ordinary path.
#
# Derived from measurement on this host, not chosen.  The longest stretch of a recursive delete with
# no cooperative check in it is one directory's `os.scandir` plus `sorted`, which runs BEFORE the
# first per-entry check: measured 0.71 us/entry (0.143 s at 200,000 entries), so 0.326 s at the
# 457,364-entry directory this codebase already cites.  One entry's own stat+unlink between checks
# measured 63.8 us, the worker-raise-to-broker-visible round trip measured 1.3 ms, and the broker's
# own maintenance poll is BATCHD_SCHEDULER_POLL_SECONDS.  Measured worst sum ~= 0.378 s; 2.0 s is
# roughly 5x that, which is the headroom for a cold page cache that no constant can bound exactly.
# When a directory IS large enough to blow through this, the backstop fires and the worker's partial
# evidence is retained instead of discarded -- that retention is what makes an imperfect bound safe.
BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS = 2.0
# At most this many unkillable predecessors may remain alive across the whole broker.  This is a
# daemon-wide memory bound, not a per-lane convenience limit: allowing two stuck workers in every
# lane would turn one wedged mount into an unbounded replacement storm.
BATCHD_MAX_QUARANTINED_PREDECESSORS = 2
BATCHD_SOCKET_NAME = "batchd.sock"
BATCHD_PRIORITIES = tuple(BATCHD_PRIORITY_LANES)
BATCHD_BROKER_ACTIONS = frozenset({
    "ping", "status", "profile", "submit", "result", "product", "produce", "cancel",
    "lease", "release", "shutdown", "shutdown_if_idle",
})
BATCHD_ARTIFACT_ACTION_METHODS = {
    "artifact_open": "open",
    "artifact_chunk": "chunk",
    "artifact_close": "close",
}
BATCHD_REQUEST_ACTIONS = frozenset((*BATCHD_BROKER_ACTIONS, *BATCHD_ARTIFACT_ACTION_METHODS))
BATCHD_COMMAND_ROUTER = LocalServiceCommandRouter({action: f"_handle_{action}" for action in BATCHD_BROKER_ACTIONS})
BATCHD_ARTIFACT_COMMAND_ROUTER = LocalServiceCommandRouter(BATCHD_ARTIFACT_ACTION_METHODS)
BATCHD_PRODUCT_DELIVERY_MODES = frozenset({"ready_or_receipt", "receipt"})


def default_socket_path() -> Path:
    return safe_socket_path(RUNTIME_DIR / "services" / BATCHD_SOCKET_NAME, prefix="yolomux-batchd")




def batchd_artifact_root() -> Path:
    return RUNTIME_DIR / "batchd-artifacts"


def artifact_root() -> Path:
    return batchd_artifact_root()


def _private_artifact_root() -> Path:
    root = artifact_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise OSError("batchd artifact root must not be a symlink")
    root_stat = root.stat()
    if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != os.getuid():
        raise OSError("batchd artifact root has an invalid owner or type")
    os.chmod(root, 0o700)
    return root


def default_worker_count(cpu_count: int | None = None) -> int:
    return max(1, min(BATCHD_MAX_WORKERS, max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)) - 1)))


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
    while len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > BATCHD_MAX_RESULT_BYTES - 4096 and result["items"]:
        result["items"].pop(0)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _session_files_view(payload: bytes) -> bytes:
    """Compute one bounded session-files product (recursive discovery + git) in a worker.

    Keeps ALL git spawns and transcript discovery out of the web process. The orchestrator lives in
    ``session_files`` (import-safe, no app/web) so it is unit-testable without a broker socket.
    """
    value = json.loads(payload.decode("utf-8"))
    result = session_files.session_files_view_result(value, max_bytes=BATCHD_MAX_RESULT_BYTES - 4096)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _tabber_activity_view(payload: bytes) -> bytes:
    """Assemble bounded Tabber rows for changed sessions from pre-gathered data in a worker.

    Pure assembly only (dict merge/sort) -- the web owner does all impure gathering (tmux capture,
    live attention/cooldown, git) before submitting. The orchestrator lives in ``activity_summary``
    (import-safe, no app/web) so it is unit-testable without a broker socket.
    """
    value = json.loads(payload.decode("utf-8"))
    result = tabber_activity_view_result(value, max_bytes=BATCHD_MAX_RESULT_BYTES - 4096)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _metadata_warm_view(payload: bytes) -> bytes:
    """Warm GitHub/Linear PR status and Linear issue metadata for a batch of sessions in a worker.

    ALL GitHub/Linear network calls and git spawns happen here, never on the web process's
    background thread. The orchestrator lives in ``metadata`` (import-safe, no app/web) so it is
    unit-testable without a broker socket.
    """
    value = json.loads(payload.decode("utf-8"))
    result = metadata_warm_view_result(value, max_bytes=BATCHD_MAX_RESULT_BYTES - 4096)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _filesystem_batch(payload: bytes) -> bytes:
    """Compute one bounded Finder list/info batch outside the web process."""
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("filesystem batch payload must be an object")
    result = filesystem.filesystem_batch_result(value)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _filesystem_operation_untyped(payload: bytes) -> bytes | BatchedTaskResult | BatchedArtifactResult:
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


def _filesystem_operation_authorized(value: dict[str, Any]) -> bytes | BatchedTaskResult | BatchedArtifactResult:
    operation = str(value.get("op") or "")
    path = str(value.get("path") or "")
    args = value.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("filesystem operation args must be an object")
    if operation == "list":
        result = filesystem.list_directory(path, include_repo_info=args.get("include_repo_info") is not False)
    elif operation == "read":
        result = filesystem.read_file(path, include_git=args.get("include_git") is True)
    elif operation == "resolve_file_candidates":
        result = filesystem.resolve_file_candidates(args.get("paths"))
    elif operation == "html_preview":
        result = filesystem.read_file(path)
        body = html_preview_document(
            str(result.get("content") or ""),
            path,
            str(args.get("locale") or "en"),
        ).encode("utf-8")
        return BatchedTaskResult(body, {
            "format": "opaque_bytes",
            "content_type": "text/html; charset=utf-8",
            "length": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "disposition": "inline",
            "filename": "",
        })
    elif operation == "info":
        result = filesystem.path_info(path, operation="filesystem_operation.info", include_git=args.get("include_git") is True)
    elif operation == "search":
        # Step 4: an opaque cursor selects delta mode; ``search_files`` serves committed journal
        # deltas since it (no traversal) instead of a snapshot. An absent/empty cursor is a snapshot.
        cursor = str(args.get("cursor") or "") or None
        search_args = {
            "recursive": args.get("recursive") is True,
            "cursor": cursor,
        }
        if args.get("indexed_only") is True:
            search_args["indexed_only"] = True
        result = filesystem.search_files(path, str(args.get("query") or ""), args.get("limit", 400), **search_args)
    elif operation == "index_status":
        result = filesystem.index_status(path)
    elif operation == "count":
        result = filesystem.count_directory_files(path)
    elif operation == "diff":
        result = filesystem.diff_file(path, from_ref=args.get("from_ref"), to_ref=args.get("to_ref"))
    elif operation == "git_history":
        result = filesystem.git_history(path, limit=args.get("limit"), cursor=str(args.get("cursor") or "") or None)
    elif operation == "git_commit":
        result = filesystem.git_commit(path, commit=str(args.get("commit") or ""), head=str(args.get("head") or ""))
    elif operation == "blame":
        result = filesystem.blame_file(path, ref=args.get("ref"))
    elif operation == "write":
        result = filesystem.write_file(path, str(args.get("content") or ""), expected_mtime=args.get("expected_mtime"))
    elif operation == "delete":
        # Still ONE `delete` arm.  `recursive` picks the cost class the caller already reserved a
        # lane for; a bounded request that turns out to need a subtree walk comes back as a typed
        # pending result and is re-submitted with `recursive=True` on the bulk lane.
        recursive = args.get("recursive") is True
        # A recursive delete is the one filesystem operation whose cost is input-sized, so it is the
        # one that must be able to stop.  The bounded form is a single syscall: a deadline check
        # there could only refuse work that was already about to finish, so it carries none.
        result = filesystem.delete_path(
            path,
            recursive=recursive,
            deadline_monotonic=current_task_control().deadline_monotonic if recursive else None,
        )
    elif operation == "unindex":
        result = filesystem.unindex_root(path)
    elif operation == "rename":
        result = filesystem.rename_path(path, str(args.get("new_name") or ""))
    elif operation == "mkdir":
        result = filesystem.create_directory(path)
    elif operation == "raw":
        return _filesystem_transfer_artifact(operation, path, args)
    elif operation == "zip":
        return _filesystem_transfer_artifact(operation, path, args)
    else:
        raise ValueError("unsupported filesystem operation")
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _filesystem_transfer_artifact(operation: str, path: str, args: dict[str, Any]) -> BatchedArtifactResult:
    root = _private_artifact_root()
    descriptor, raw_name = tempfile.mkstemp(prefix="transfer-", dir=root)
    basename = Path(raw_name).name
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(os.dup(descriptor), "w+b", buffering=0) as target:
            if operation == "raw":
                length, content_type, digest = filesystem.copy_raw_to(path, target, max_bytes=args.get("max_bytes"))
                disposition = "attachment" if args.get("download") is True else "inline"
                filename = product_filename(Path(path).name, fallback="download") if disposition == "attachment" else ""
            else:
                length, digest = filesystem.zip_directory_to(path, target, max_bytes=args.get("max_bytes"))
                content_type = "application/zip"
                disposition = "attachment"
                filename = product_filename(args.get("filename") or f"{Path(path).name or 'archive'}.zip", fallback="archive.zip")
        artifact_stat = os.fstat(descriptor)
        return BatchedArtifactResult(
            basename=basename,
            device=artifact_stat.st_dev,
            inode=artifact_stat.st_ino,
            product={
                "format": "opaque_bytes",
                "content_type": content_type,
                "length": length,
                "sha256": digest,
                "disposition": disposition,
                "filename": filename,
            },
        )
    except Exception:
        directory_fd = -1
        try:
            directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            os.unlink(basename, dir_fd=directory_fd)
        except OSError:
            pass
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)
        raise
    finally:
        os.close(descriptor)


def _filesystem_operation(payload: bytes) -> bytes:
    """Preserve every filesystem facade failure across the batchd process boundary."""
    try:
        return _filesystem_operation_untyped(payload)
    except filesystem.FilesystemError as exc:
        value = json.loads(payload.decode("utf-8"))
        path = str(value.get("path") or "") if isinstance(value, dict) else ""
        raise BatchedFilesystemOperationFailure(exc.status, exc.payload(path=path)) from exc


def _session_files_cache_prune(payload: bytes) -> bytes:
    """Prune the durable session-files cache in a batchd worker process."""
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


def _queued_delivery_compact(payload: bytes) -> bytes:
    """Compact one private operation journal in a batchd worker process."""

    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict) or set(value) != {"state_path"}:
        raise ValueError("queued-delivery compaction payload must contain only state_path")
    state_path = Path(str(value.get("state_path") or "")).expanduser()
    if not state_path.is_absolute() or ".." in state_path.parts:
        raise ValueError("queued-delivery state path must be absolute and normalized")
    result = compact_queued_delivery_journal(state_path)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


REGISTERED_TASKS = {
    "filesystem_batch": _filesystem_batch,
    "filesystem_operation": _filesystem_operation,
    "indexed_repo_roots": _indexed_repo_roots,
    "json_compact": _json_compact,
    "metadata_warm_view": _metadata_warm_view,
    "queued_delivery_compact": _queued_delivery_compact,
    "session_files_cache_prune": _session_files_cache_prune,
    "session_files_view": _session_files_view,
    "tabber_activity_view": _tabber_activity_view,
    "text_facts": _text_facts,
    "transcript_view": _transcript_view,
}


@dataclass(frozen=True)
class BatchedTaskResult:
    """Opaque task bytes plus the uniform product metadata retained with them."""

    body: bytes
    product: dict[str, object]


@dataclass(frozen=True)
class BatchedArtifactResult:
    """Worker-created private artifact adopted by the broker through one pinned descriptor."""

    basename: str
    device: int
    inode: int
    product: dict[str, object]




@dataclass(slots=True)
class StoredInlineProduct:
    generation: int
    body: bytes
    stored_at: float
    product: dict[str, object]


@dataclass(slots=True)
class StoredArtifactProduct:
    generation: int
    descriptor: int
    stored_at: float
    product: dict[str, object]


@dataclass(slots=True)
class StoredJobProduct:
    inline: StoredInlineProduct | None = None
    artifact: StoredArtifactProduct | None = None
    schedule: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class BatchedArtifactLease:
    descriptor: int
    product: dict[str, object]
    expires_at: float


class BatchedFilesystemOperationFailure(RuntimeError):
    """Preserve a worker filesystem error's HTTP payload across the process boundary."""

    def __init__(self, status: int, payload: dict[str, object]):
        super().__init__(str(payload.get("error") or "filesystem operation failed"))
        self.status = int(status)
        self.payload = dict(payload)

    def __reduce__(self) -> tuple[object, tuple[int, dict[str, object]]]:
        return type(self), (self.status, self.payload)




def _validated_product_metadata(body: bytes, product: dict[str, object], *, expected_length: int | None = None) -> dict[str, object]:
    if set(product) != {"format", "content_type", "length", "sha256", "disposition", "filename"}:
        raise ValueError("invalid product metadata fields")
    if product["format"] not in {"json", "opaque_bytes"} or product["disposition"] not in {"inline", "attachment"}:
        raise ValueError("invalid product metadata")
    if not isinstance(product["content_type"], str) or not product["content_type"]:
        raise ValueError("invalid product content type")
    body_length = len(body) if expected_length is None else expected_length
    if product["length"] != body_length or not isinstance(product["sha256"], str):
        raise ValueError("invalid product integrity")
    if expected_length is None and product["sha256"] != hashlib.sha256(body).hexdigest():
        raise ValueError("invalid product integrity")
    if not isinstance(product["filename"], str) or "/" in product["filename"] or "\\" in product["filename"]:
        raise ValueError("invalid product filename")
    return dict(product)


@dataclass(frozen=True, slots=True)
class BatchedTaskControl:
    """The bounds one dispatched job carries into its worker process.

    ``deadline_monotonic`` is an ABSOLUTE ``time.monotonic()`` instant read in the broker process.
    ``CLOCK_MONOTONIC`` is system-wide rather than per-process on every platform this runs on, so a
    spawned worker compares against it directly.  A relative budget would be wrong: the worker would
    start its countdown after cold ``ProcessPoolExecutor`` startup, putting its stop strictly AFTER
    the broker's deadline -- which is the defect this type exists to remove.
    """

    deadline_monotonic: float | None = None


# One worker process runs exactly one task at a time, so the active control is process-local state
# rather than a registry.  ``run_registered_task_result`` is the only writer.
_active_task_control: BatchedTaskControl | None = None
_NO_TASK_CONTROL = BatchedTaskControl()


def current_task_control() -> BatchedTaskControl:
    """The control for the task this process is running, or an empty one outside a task."""
    return _active_task_control if _active_task_control is not None else _NO_TASK_CONTROL


@contextlib.contextmanager
def active_task_control(control: BatchedTaskControl | None) -> Iterator[None]:
    """Install one task's control for the duration of that task and always clear it after.

    Clearing is not optional even when the task raises: a leaked deadline would be inherited by
    whatever this worker runs next, and an already-expired one would refuse it instantly.
    """
    global _active_task_control
    previous = _active_task_control
    _active_task_control = control
    try:
        yield
    finally:
        _active_task_control = previous


def run_registered_task_result(
    task: str,
    payload: bytes,
    control: BatchedTaskControl | None = None,
) -> BatchedTaskResult | BatchedArtifactResult:
    """Executor entry point that preserves opaque task bodies for broker retention.

    This is the ONE entry point every registered task is dispatched through, so it is also where a
    per-job control is installed -- once, for all of them -- rather than threaded through fifteen
    task signatures.  A task that never reads it is unaffected, and ``control=None`` reproduces the
    behaviour of every caller that predates it.
    """
    if task not in REGISTERED_TASKS:
        raise ValueError("unknown task")
    if len(payload) > BATCHD_MAX_PAYLOAD_BYTES:
        raise ValueError("payload too large")
    with active_task_control(control):
        result = REGISTERED_TASKS[task](payload)
    if isinstance(result, BatchedArtifactResult):
        _validated_product_metadata(b"", result.product, expected_length=int(result.product.get("length") or -1))
        return result
    if isinstance(result, BatchedTaskResult):
        body = result.body
        product = _validated_product_metadata(body, result.product)
    else:
        body = result
        product = inline_json_product_metadata(body)
    result_limit = BATCHD_MAX_FILESYSTEM_BATCH_RESULT_BYTES if task == "filesystem_batch" else BATCHD_MAX_RESULT_BYTES
    if len(body) > result_limit:
        raise ValueError("result too large")
    return BatchedTaskResult(body, product)


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
    artifact_finalizing: bool = False
    executor_slot: int = -1
    executor_generation: int = 0


@dataclass
class ExecutorSlot:
    """One independently replaceable worker slot owned by a scheduler lane."""

    executor: ProcessPoolExecutor | None = None
    generation: int = 0
    predecessors: list[tuple[int, ProcessPoolExecutor]] = field(default_factory=list)


class JobProductStore:
    """One bounded owner for retained inline products, artifacts, and artifact leases."""

    def __init__(self) -> None:
        self.entries: dict[str, StoredJobProduct] = {}
        self.leases: dict[str, BatchedArtifactLease] = {}

    @staticmethod
    def _artifact_directory_fd() -> int:
        return os.open(_private_artifact_root(), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))

    def _adopt_artifact(self, result: BatchedArtifactResult) -> StoredArtifactProduct:
        if Path(result.basename).name != result.basename or result.basename in {"", ".", ".."}:
            raise ValueError("invalid artifact basename")
        directory_fd = self._artifact_directory_fd()
        descriptor = -1
        try:
            descriptor = os.open(
                result.basename,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            artifact_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(artifact_stat.st_mode)
                or artifact_stat.st_uid != os.getuid()
                or artifact_stat.st_dev != result.device
                or artifact_stat.st_ino != result.inode
                or artifact_stat.st_size != result.product.get("length")
            ):
                raise ValueError("artifact identity does not match its manifest")
            digest = hashlib.sha256()
            offset = 0
            while offset < artifact_stat.st_size:
                chunk = os.pread(descriptor, min(BATCHD_ARTIFACT_CHUNK_BYTES, artifact_stat.st_size - offset), offset)
                if not chunk:
                    raise ValueError("artifact ended before its manifest length")
                digest.update(chunk)
                offset += len(chunk)
            if digest.hexdigest() != result.product.get("sha256"):
                raise ValueError("artifact digest does not match its manifest")
            product = _validated_product_metadata(b"", result.product, expected_length=artifact_stat.st_size)
            artifact = StoredArtifactProduct(0, descriptor, time.time(), product)
            descriptor = -1
            return artifact
        finally:
            try:
                os.unlink(result.basename, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory_fd)

    @staticmethod
    def _close_artifact(artifact: StoredArtifactProduct | None) -> None:
        if artifact is not None:
            os.close(artifact.descriptor)

    def discard_artifact_result(self, result: BatchedArtifactResult) -> None:
        self._close_artifact(self._adopt_artifact(result))

    def store_inline(
        self,
        *,
        key: str,
        generation: int,
        body: bytes,
        product: dict[str, object],
        schedule: dict[str, object],
        stored_at: float | None = None,
    ) -> None:
        entry = self.entries.setdefault(key, StoredJobProduct())
        if entry.inline is not None and generation < entry.inline.generation:
            return
        entry.inline = StoredInlineProduct(generation, body, time.time() if stored_at is None else stored_at, dict(product))
        entry.schedule = dict(schedule)
        while self.inline_count() > BATCHD_MAX_PRODUCTS or self.inline_bytes() > BATCHD_MAX_RETAINED_RESULT_BYTES:
            oldest_key = min(
                (candidate for candidate, retained in self.entries.items() if retained.inline is not None),
                key=lambda candidate: self.entries[candidate].inline.stored_at,  # type: ignore[union-attr]
            )
            oldest = self.entries[oldest_key]
            oldest.inline = None
            oldest.schedule = {}
            if oldest.artifact is None:
                self.entries.pop(oldest_key, None)

    def store_artifact(
        self,
        *,
        key: str,
        generation: int,
        result: BatchedArtifactResult,
        schedule: dict[str, object],
    ) -> dict[str, object]:
        artifact = self.prepare_artifact(result)
        return self.store_prepared_artifact(
            key=key,
            generation=generation,
            artifact=artifact,
            schedule=schedule,
        )

    def prepare_artifact(self, result: BatchedArtifactResult) -> StoredArtifactProduct:
        """Verify and unlink one worker artifact without touching the shared product index."""
        return self._adopt_artifact(result)

    def store_prepared_artifact(
        self,
        *,
        key: str,
        generation: int,
        artifact: StoredArtifactProduct,
        schedule: dict[str, object],
    ) -> dict[str, object]:
        """Publish one already-verified artifact while the broker state lock is held."""
        current_bytes = sum(
            int(entry.artifact.product["length"])
            for entry in self.entries.values()
            if entry.artifact is not None
        )
        incoming_bytes = int(artifact.product.get("length") or 0)
        if self.artifact_count() >= BATCHD_MAX_ARTIFACTS or current_bytes + incoming_bytes > BATCHD_MAX_ARTIFACT_BYTES:
            self._close_artifact(artifact)
            raise ValueError("artifact capacity full")
        artifact.generation = generation
        entry = self.entries.setdefault(key, StoredJobProduct())
        self._close_artifact(entry.artifact)
        entry.artifact = artifact
        entry.schedule = dict(schedule)
        return dict(artifact.product)

    def prune_leases(self) -> None:
        now = time.monotonic()
        for lease_id, lease in list(self.leases.items()):
            if lease.expires_at <= now:
                os.close(lease.descriptor)
                self.leases.pop(lease_id, None)

    def product(
        self,
        key: str,
        *,
        latest_generation: int,
        inflight: bool,
        source_epoch: str,
    ) -> tuple[dict[str, object], bytes]:
        entry = self.entries.get(key)
        if entry is None or (entry.inline is None and entry.artifact is None):
            return {"ok": True, "state": "pending" if inflight else "none", "generation": 0, "inflight": inflight}, b""
        if entry.artifact is not None:
            retained = entry.artifact
            return {
                "ok": True,
                "state": "stale" if (inflight or latest_generation > retained.generation) else "ready",
                "generation": retained.generation,
                "source_epoch": source_epoch,
                "stored_at": retained.stored_at,
                "inflight": inflight,
                "artifact": True,
                "product": dict(retained.product),
                "schedule": dict(entry.schedule),
            }, b""
        retained = entry.inline
        if retained is None:
            raise RuntimeError("retained product is empty")
        return {
            "ok": True,
            "state": "stale" if (inflight or latest_generation > retained.generation) else "ready",
            "generation": retained.generation,
            "source_epoch": source_epoch,
            "stored_at": retained.stored_at,
            "inflight": inflight,
            "product": dict(retained.product),
            "schedule": dict(entry.schedule),
        }, retained.body

    def submission_identity(self, key: str) -> tuple[int, float] | None:
        entry = self.entries.get(key)
        if entry is None:
            return None
        retained = entry.inline if entry.inline is not None else entry.artifact
        return None if retained is None else (retained.generation, retained.stored_at)

    def handle(self, action: str, request: dict[str, object], body: bytes) -> tuple[dict[str, object], bytes]:
        response = BATCHD_ARTIFACT_COMMAND_ROUTER.dispatch(self, action, request, body)
        return response if response is not None else ({"ok": False, "error": "unknown batchd action"}, b"")

    def open(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        if len(self.leases) >= BATCHD_MAX_ARTIFACT_LEASES:
            return {"ok": False, "error": "artifact lease capacity full"}, b""
        entry = self.entries.get(str(request.get("coalesce_key") or ""))
        if entry is None or entry.artifact is None:
            return {"ok": False, "error": "artifact unavailable"}, b""
        requested_generation = int(request.get("generation") or 0)
        artifact = entry.artifact
        if artifact.generation != requested_generation:
            return {"ok": False, "error": "artifact generation unavailable"}, b""
        lease_id = uuid.uuid4().hex
        self.leases[lease_id] = BatchedArtifactLease(
            descriptor=os.dup(artifact.descriptor),
            product=dict(artifact.product),
            expires_at=time.monotonic() + BATCHD_ARTIFACT_LEASE_SECONDS,
        )
        return {"ok": True, "lease_id": lease_id, "product": dict(artifact.product)}, b""

    def chunk(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        self.prune_leases()
        lease = self.leases.get(str(request.get("lease_id") or ""))
        if lease is None:
            return {"ok": False, "error": "artifact lease unavailable"}, b""
        try:
            offset = int(request.get("offset") or 0)
            requested = int(request.get("max_bytes") or BATCHD_ARTIFACT_CHUNK_BYTES)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid artifact chunk range"}, b""
        length = int(lease.product["length"])
        if offset < 0 or offset > length or requested < 1:
            return {"ok": False, "error": "invalid artifact chunk range"}, b""
        chunk = os.pread(lease.descriptor, min(requested, BATCHD_ARTIFACT_CHUNK_BYTES, length - offset), offset)
        if not chunk and offset < length:
            return {"ok": False, "error": "artifact ended before its declared length"}, b""
        lease.expires_at = time.monotonic() + BATCHD_ARTIFACT_LEASE_SECONDS
        return {
            "ok": True,
            "offset": offset,
            "length": len(chunk),
            "eof": offset + len(chunk) == length,
            "sha256": hashlib.sha256(chunk).hexdigest(),
        }, chunk

    def close(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        lease = self.leases.pop(str(request.get("lease_id") or ""), None)
        if lease is None:
            return {"ok": True, "closed": False}, b""
        os.close(lease.descriptor)
        return {"ok": True, "closed": True}, b""

    def shutdown(self) -> None:
        for lease in self.leases.values():
            os.close(lease.descriptor)
        self.leases.clear()
        for entry in self.entries.values():
            self._close_artifact(entry.artifact)
            entry.artifact = None

    def inline_count(self) -> int:
        return sum(entry.inline is not None for entry in self.entries.values())

    def inline_bytes(self) -> int:
        return sum(len(entry.inline.body) for entry in self.entries.values() if entry.inline is not None)

    def stale_inline_count(self, latest_generations: dict[str, int]) -> int:
        return sum(
            latest_generations.get(key, entry.inline.generation) > entry.inline.generation
            for key, entry in self.entries.items()
            if entry.inline is not None
        )

    def artifact_count(self) -> int:
        return sum(entry.artifact is not None for entry in self.entries.values())

    def lease_count(self) -> int:
        return len(self.leases)

    def open_descriptor_count(self) -> int:
        return self.artifact_count()

    def inline_keys(self) -> set[str]:
        return {key for key, entry in self.entries.items() if entry.inline is not None}

    def inline_generation(self, key: str) -> int:
        entry = self.entries[key]
        if entry.inline is None:
            raise KeyError(key)
        return entry.inline.generation

    def inline_body(self, key: str) -> bytes:
        entry = self.entries[key]
        if entry.inline is None:
            raise KeyError(key)
        return entry.inline.body

    def inline_metadata(self, key: str) -> dict[str, object]:
        entry = self.entries[key]
        if entry.inline is None:
            raise KeyError(key)
        return dict(entry.inline.product)


class PersistentJobBroker:
    """One local broker with bounded spawn-only capacity for typed CPU jobs."""

    def __init__(self, socket_path: Path, idle_seconds: float = BATCHD_DEFAULT_IDLE_SECONDS, workers: int | None = None):
        self.service_name = BATCHD_SERVICE_NAME
        self.socket_path = safe_socket_path(socket_path, prefix=f"yolomux-{self.service_name}")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = multiprocessing.get_context("spawn").Event()
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.general_worker_count = max(1, min(BATCHD_MAX_WORKERS, int(workers or default_worker_count())))
        self.started_at = time.time()
        self.source_epoch = uuid.uuid4().hex
        self.last_client_at = time.monotonic()
        self.leases: dict[str, object] = {}
        self.records: dict[str, JobRecord] = {}
        self.queues = {priority: deque() for priority in BATCHD_PRIORITIES}
        self.coalesced: dict[tuple[str, str], str] = {}
        self.latest_generation: dict[str, int] = {}
        # Materialized-product layer: newest completed bytes per coalesce_key (last-known-good),
        # and bounded per-task counters. These make stale-while-revalidate a broker property so a
        # web route can serve a prior complete product while a newer generation is still building.
        self.product_store = JobProductStore()
        # Scheduling facts retained beside each stored product so a completed operation can say
        # which lane ran it, how long it waited to be dispatched and how long it executed.  Without
        # this the only visible number is total wall time, which cannot distinguish a slow task from
        # a task that sat behind a lane holder -- the exact question a stalled editor open raises.
        self.product_counters: dict[str, dict[str, int]] = {}
        # Per-task pure execution duration (excludes queue wait): count/total/max in milliseconds,
        # bounded per task name (not per job) so this dict cannot grow with job volume.
        self.product_runtime_ms: dict[str, dict[str, float]] = {}
        # Nested only by registered task and a fixed worker-owned phase vocabulary.  The broker
        # deliberately retains aggregates rather than completed product/profile payloads.
        self.product_phase_runtime_ms: dict[str, dict[str, dict[str, float]]] = {}
        self.product_work_totals: dict[str, dict[str, int]] = {}
        self.owner_invocations: dict[str, int] = {
            "batchd_work_graph_rebuild": 0,
            "provider_metadata_rebuild": 0,
        }
        self.source_diagnostics: dict[str, dict[str, str | int]] = {}
        self.source_change_counters: dict[str, int] = {}
        self.session_files_accepted_requester_counters: dict[str, int] = {}
        self.session_files_requester_counters: dict[str, int] = {}
        self.request_counters: dict[str, int] = {}
        self.contention_counters: dict[str, int] = {}
        self.request_counter_lock = threading.Lock()
        self.scheduler_pump_failures = 0
        self.scheduler_pump_last_failure: dict[str, str] = {}
        # A multi-worker ProcessPoolExecutor cannot retire one NFS/D-state worker: replacing it
        # tears down healthy siblings.  Slots keep the broker queues central while each worker has
        # one disposable executor and a monotonically increasing publication generation.
        self.executor_slots: dict[str, list[ExecutorSlot]] = {
            lane: [ExecutorSlot() for _ in range(self._lane_capacity(lane))]
            for lane in BATCHD_LANE_PRIORITIES
        }
        # Compatibility projection for control-plane callers and old status consumers.  It always
        # names slot zero; scheduling exclusively uses executor_slots.
        self.executors: dict[str, ProcessPoolExecutor | None] = {lane: None for lane in BATCHD_LANE_PRIORITIES}
        self.state_lock = threading.RLock()
        # Submission acceptance and shutdown share this short critical section. A shutdown that
        # wins it closes admission before setting the pending-drain flag; a submission that wins it
        # has one accepted record that shutdown must drain before stopping the broker.
        self.lifecycle_admission_lock = threading.Lock()
        self.shutdown_requested = threading.Event()
        self.scheduler_event = threading.Event()
        self.scheduler_thread: threading.Thread | None = None
        self.scheduler_start_lock = threading.Lock()
        self.scheduler_readiness_thread: threading.Thread | None = None
        # Ping/status are lifecycle control-plane actions. A scheduler pump may own `state_lock`,
        # but that must never make the registry lose the daemon's identity or call it unhealthy.
        # Publish full status snapshots atomically after locked reads; a contended status returns
        # the last complete snapshot with an explicit busy marker instead of reading mutable state.
        self._last_status_snapshot: dict[str, Any] = {}
        # Construction is single-threaded, so the canonical status builder can publish the initial
        # zero-work snapshot without taking a second path that will drift from live status fields.
        self.common_status()

    def _bump_counter(self, task: str, name: str) -> None:
        counters = self.product_counters.setdefault(task, {"accepted": 0, "coalesced": 0, "superseded": 0, "completed": 0, "failed": 0, "timed_out": 0})
        counters[name] = counters.get(name, 0) + 1

    def _record_request_action(self, action: str, *, contention: bool = False) -> None:
        action_counter = action if action in BATCHD_REQUEST_ACTIONS else "unknown"
        with self.request_counter_lock:
            counters = self.contention_counters if contention else self.request_counters
            counters[action_counter] = counters.get(action_counter, 0) + 1

    @staticmethod
    def _session_files_requester_key(payload: dict[str, Any]) -> str:
        source = payload.get("source")
        requester = source.get("requester") if isinstance(source, dict) else None
        return requester if requester in BATCHD_SESSION_FILES_REQUESTERS else "unknown"

    def _record_runtime_ms(self, task: str, elapsed_ms: float) -> None:
        stats = self.product_runtime_ms.setdefault(task, {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0})
        stats["count"] += 1
        stats["total_ms"] += elapsed_ms
        stats["max_ms"] = max(stats["max_ms"], elapsed_ms)

    def _record_phase_runtime_ms(self, task: str, decoded: dict[str, Any] | None) -> None:
        if task not in {"session_files_view", "metadata_warm_view"} or decoded is None:
            return
        profile = decoded.get("profile")
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
            if task == "metadata_warm_view":
                for owner in self.owner_invocations:
                    value = work.get(owner)
                    if isinstance(value, int) and value >= 0:
                        self.owner_invocations[owner] += value
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
        while len(self.source_diagnostics) > BATCHD_MAX_SOURCE_DIAGNOSTICS:
            self.source_diagnostics.pop(next(iter(self.source_diagnostics)))

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
        lane = BATCHD_PRIORITY_LANES.get(str(priority))
        if lane is None:
            # `_validated_submission` rejects unknown priorities, so reaching here means a caller
            # built a JobRecord directly with a priority no lane runs.  Name it rather than
            # silently dispatching the work onto whichever pool happens to be first.
            raise ValueError(f"no batchd lane owns priority {priority!r}")
        return lane

    def _lane_capacity(self, lane: str) -> int:
        """Return one lane's bounded worker capacity, refusing a lane no table describes."""
        if lane not in BATCHD_LANE_PRIORITIES:
            raise ValueError(f"unknown batchd lane {lane!r}")
        if lane in BATCHD_LANE_WORKERS:
            return BATCHD_LANE_WORKERS[lane]
        return self.general_worker_count

    @staticmethod
    def _new_executor(worker_count: int) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=worker_count, mp_context=multiprocessing.get_context("spawn"))

    def _executor(self, priority: str = "freshness", slot_index: int = 0) -> ProcessPoolExecutor:
        lane = self._lane_for_priority(priority)
        slot = self.executor_slots[lane][slot_index]
        executor = slot.executor
        if executor is None:
            executor = self._new_executor(1)
            slot.executor = executor
            if slot_index == 0:
                self.executors[lane] = executor
        return executor

    def _quarantined_predecessor_count(self) -> int:
        return sum(len(slot.predecessors) for slots in self.executor_slots.values() for slot in slots)

    @staticmethod
    def _executor_workers(executor: ProcessPoolExecutor) -> list[Any]:
        """Return workers still owned by an executor, including none after shutdown."""
        processes = executor._processes
        return list(processes.values()) if processes is not None else []

    def _quarantine_slot(self, record: JobRecord) -> bool:
        """Fence one unresponsive worker and make only its slot eligible for replacement."""
        lane = self._lane_for_priority(record.priority)
        if record.executor_slot < 0:
            return False
        slot = self.executor_slots[lane][record.executor_slot]
        if slot.generation != record.executor_generation or slot.executor is None:
            return False
        if self._quarantined_predecessor_count() >= BATCHD_MAX_QUARANTINED_PREDECESSORS:
            return False
        predecessor = slot.executor
        predecessor.shutdown(wait=False, cancel_futures=True)
        slot.predecessors.append((slot.generation, predecessor))
        slot.executor = None
        slot.generation += 1
        if record.executor_slot == 0:
            self.executors[lane] = None
        return True

    def _retire_broken_slot(self, record: JobRecord) -> None:
        """Fence only the slot whose executor has already failed.

        ``BrokenProcessPool`` is a per-executor failure.  Retiring a whole lane here would change
        the generation of healthy sibling slots and discard work that the broker already accepted.
        """
        lane = self._lane_for_priority(record.priority)
        if record.executor_slot < 0:
            # Compatibility records created by pre-slot tests and legacy recovery callers have no
            # slot identity. They cannot prove a healthy sibling exists, so preserve the old safe
            # all-lane cleanup only for that un-attributed path.
            self._shutdown_executor(lane=lane)
            return
        slot = self.executor_slots[lane][record.executor_slot]
        if slot.generation != record.executor_generation:
            return
        executor = slot.executor
        slot.executor = None
        slot.generation += 1
        if record.executor_slot == 0:
            self.executors[lane] = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _reap_slot_predecessors(self) -> None:
        """Drop exactly the predecessor tokens whose worker groups have actually exited."""
        for slots in self.executor_slots.values():
            for slot in slots:
                retained: list[tuple[int, ProcessPoolExecutor]] = []
                for generation, executor in slot.predecessors:
                    workers = self._executor_workers(executor)
                    if any(worker.is_alive() for worker in workers):
                        retained.append((generation, executor))
                slot.predecessors = retained

    def _shutdown_executor(self, *, lane: str) -> None:
        """Shut down one lane's process pool and prove every worker process is actually gone.

        `ProcessPoolExecutor.shutdown(wait=False, ...)` only stops the executor from accepting
        new work; it does not wait for spawned worker PROCESSES to exit, and its `_ExecutorManagerThread`
        is daemonized so it cannot itself block process exit. But `multiprocessing` separately
        registers an `atexit` hook (`multiprocessing.util._exit_function`) that unconditionally
        `join()`s every still-`active_children()` process with NO timeout. If a worker was mid-git
        subprocess (or any other blocking call) when the lane was told to stop, that worker never
        exits on its own, and the whole batchd process then hangs forever inside Python's own
        interpreter-shutdown thread-join -- reachable via `sample`/`py-spy` as `wait_for_thread_shutdown`,
        with the listening socket already unlinked, looking alive but serving nothing. Terminating
        (then killing, if needed) every worker here, synchronously and with bounded timeouts, means
        no active child is left for that atexit hook to hang on, regardless of what it was doing.
        """
        slots = self.executor_slots[lane]
        # Include the compatibility projection if a legacy caller supplied a test executor.
        legacy = self.executors.get(lane)
        executors = [executor for slot in slots for executor in ([slot.executor] + [predecessor for _generation, predecessor in slot.predecessors]) if executor is not None]
        if legacy is not None and legacy not in executors:
            executors.append(legacy)
        self.executors[lane] = None
        for slot in slots:
            slot.executor = None
            slot.predecessors = []
            slot.generation += 1
        for executor in executors:
            workers = self._executor_workers(executor)
            executor.shutdown(wait=False, cancel_futures=True)
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
            for worker in workers:
                worker.join(timeout=2.0)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=1.0)

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
        """Expire queued work exactly at its deadline, and running work only past the backstop.

        A running job now carries its own absolute deadline into its worker, so a task that honors
        it stops by itself and its typed result -- including which paths a partial delete actually
        removed -- becomes the terminal state.  Terminalizing here at the same instant would race
        that answer and publish `timed_out` while the filesystem was still changing.  So this waits
        BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS past the deadline, which is derived from measured
        cooperative-stop latency, and only then acts for work that could not stop at all.

        A queued job has no worker to answer for it, so its expiry stays exact.
        """
        for record in self.records.values():
            if record.status not in {"queued", "running"} or record.deadline_at <= 0:
                continue
            if record.status == "queued":
                if now < record.deadline_at:
                    continue
                self._mark_terminal(record, "timed_out", "deadline exceeded before execution")
            else:
                if now < record.deadline_at + BATCHD_RUNNING_DEADLINE_BACKSTOP_SECONDS:
                    continue
                self._mark_terminal(record, "timed_out", "deadline exceeded while executing")
                self._quarantine_slot(record)
            self._bump_counter(record.task, "timed_out")

    def _handle_finished_futures(self, *, finalize_artifacts: bool) -> list[tuple[JobRecord, BatchedArtifactResult]]:
        restart_slots: list[JobRecord] = []
        pending_artifacts: list[tuple[JobRecord, BatchedArtifactResult]] = []
        for record in self.records.values():
            if record.future is None or not record.future.done() or record.artifact_finalizing:
                continue
            future = record.future
            lane = self._lane_for_priority(record.priority)
            if record.executor_slot >= 0 and record.executor_generation != self.executor_slots[lane][record.executor_slot].generation:
                try:
                    abandoned = future.result()
                    if isinstance(abandoned, BatchedArtifactResult):
                        self.product_store.discard_artifact_result(abandoned)
                except Exception:
                    pass
                record.future = None
                continue
            if record.status in {"completed", "failed", "cancelled", "superseded", "timed_out"}:
                try:
                    abandoned = future.result()
                    if isinstance(abandoned, BatchedArtifactResult):
                        self.product_store.discard_artifact_result(abandoned)
                except BatchedFilesystemOperationFailure as exc:
                    # The backstop can still beat a worker that DID stop cooperatively.  Its payload
                    # names the entries a partial delete actually removed, and it is the only record
                    # of them: discarding it leaves the requester unable to invalidate those paths.
                    # The terminal state itself is not revised -- the backstop already owns that.
                    if not record.failure:
                        record.failure = {"filesystem_error": dict(exc.payload), "status": exc.status}
                except Exception:
                    pass
                record.future = None
                continue
            try:
                task_result = future.result()
                if isinstance(task_result, bytes):
                    task_result = BatchedTaskResult(task_result, inline_json_product_metadata(task_result))
                if isinstance(task_result, BatchedArtifactResult):
                    if not finalize_artifacts:
                        continue
                    record.artifact_finalizing = True
                    pending_artifacts.append((record, task_result))
                    continue
                result = task_result.body
                result_limit = BATCHD_MAX_FILESYSTEM_BATCH_RESULT_BYTES if record.task == "filesystem_batch" else BATCHD_MAX_RESULT_BYTES
                if len(result) > result_limit:
                    raise ValueError("result too large")
                decoded_result: dict[str, Any] | None = None
                if task_result.product.get("format") == "json":
                    decoded = json.loads(result.decode("utf-8"))
                    if isinstance(decoded, dict):
                        decoded_result = decoded
                if record.status != "timed_out":
                    record.result = result
                    record.product = dict(task_result.product)
                    self._mark_terminal(record, "completed")
                    self.product_store.store_inline(
                        key=record.coalesce_key,
                        generation=record.generation,
                        body=record.result,
                        product=record.product,
                        schedule=self._record_schedule(record),
                    )
                    self._bump_counter(record.task, "completed")
                    self._record_phase_runtime_ms(record.task, decoded_result)
                    if record.running_started_monotonic > 0:
                        self._record_runtime_ms(record.task, (time.monotonic() - record.running_started_monotonic) * 1000.0)
            except BatchedFilesystemOperationFailure as exc:
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
                restart_slots.append(record)
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
        for record in restart_slots:
            self._retire_broken_slot(record)
        return pending_artifacts

    def _finalize_artifact(self, record: JobRecord, task_result: BatchedArtifactResult) -> None:
        """Verify a large artifact off-lock, then publish its bounded descriptor atomically."""
        artifact: StoredArtifactProduct | None = None
        failure: Exception | None = None
        try:
            artifact = self.product_store.prepare_artifact(task_result)
        except Exception as exc:
            failure = exc
        with self.state_lock:
            record.artifact_finalizing = False
            if failure is not None:
                if record.status != "timed_out":
                    self._mark_terminal(
                        record,
                        "failed",
                        redact_local_service_text(failure),
                        local_service_exception_cause(failure),
                    )
                    self._bump_counter(record.task, "failed")
            elif artifact is not None and record.status != "timed_out":
                try:
                    record.product = self.product_store.store_prepared_artifact(
                        key=record.coalesce_key,
                        generation=record.generation,
                        artifact=artifact,
                        schedule=self._record_schedule(record),
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    self._mark_terminal(
                        record,
                        "failed",
                        redact_local_service_text(exc),
                        local_service_exception_cause(exc),
                    )
                    self._bump_counter(record.task, "failed")
                else:
                    record.result = b""
                    self._mark_terminal(record, "completed")
                    self._bump_counter(record.task, "completed")
                    if record.running_started_monotonic > 0:
                        self._record_runtime_ms(record.task, (time.monotonic() - record.running_started_monotonic) * 1000.0)
            elif artifact is not None:
                self.product_store._close_artifact(artifact)
            record.future = None

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
        remove_count = max(0, len(self.records) - BATCHD_MAX_RECORDS)
        retained_result_bytes = sum(len(record.result) for record in self.records.values())
        while remove_count < len(terminal) and retained_result_bytes > BATCHD_MAX_RETAINED_RESULT_BYTES:
            retained_result_bytes -= len(terminal[remove_count].result)
            remove_count += 1
        for record in terminal[:remove_count]:
            self.records.pop(record.job_id, None)
            if self.coalesced.get((record.task, record.coalesce_key)) == record.job_id:
                self.coalesced.pop((record.task, record.coalesce_key), None)

    def _refresh_records(self, *, finalize_artifacts: bool = False) -> list[tuple[JobRecord, BatchedArtifactResult]]:
        """Collect answers first, then expire what never answered.

        Order is load-bearing, and this is the one place both owners are called from.  A worker that
        already published its result has answered; the broker merely looked late.  Expiring first
        relabelled that delivered answer -- a 409 partial delete carrying the paths it removed, or an
        ordinary completed product -- as `timed_out`, telling the requester nothing came back about
        work it had in fact heard back about.  The backstop is for work still running, so it must be
        evaluated only after every finished future has been claimed.

        Queued expiry is unaffected: a queued record has no future for the first pass to touch, so
        its deadline stays exact.  Claiming finished futures first also releases their slots before
        capacity is recounted, which can only let more queued work start in the same pump.
        """
        pending_artifacts = self._handle_finished_futures(finalize_artifacts=finalize_artifacts)
        self._reap_slot_predecessors()
        self._expire_deadlines(time.monotonic())
        self._prune_records()
        self.product_store.prune_leases()
        return pending_artifacts

    def _pump(self) -> None:
        dispatch: list[JobRecord] = []
        pending_artifacts: list[tuple[JobRecord, BatchedArtifactResult]] = []
        with self.state_lock:
            pending_artifacts = self._refresh_records(finalize_artifacts=True)
            now = time.monotonic()
            for lane, priorities in BATCHD_LANE_PRIORITIES.items():
                slots = self.executor_slots[lane]
                active_slots = {
                    record.executor_slot
                    for record in self.records.values()
                    if record.status == "running" and self._lane_for_priority(record.priority) == lane
                    and record.executor_slot >= 0 and record.executor_generation == slots[record.executor_slot].generation
                }
                legacy_active = sum(
                    1 for record in self.records.values()
                    if record.future is not None and not record.future.done()
                    and self._lane_for_priority(record.priority) == lane and record.executor_slot < 0
                )
                while len(active_slots) + legacy_active < len(slots):
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
                    slot_index = next(index for index in range(len(slots)) if index not in active_slots)
                    slot = slots[slot_index]
                    if slot.executor is None and self._quarantined_predecessor_count() >= BATCHD_MAX_QUARANTINED_PREDECESSORS:
                        error = "filesystem_worker_quarantined" if lane in {"point", "mutation", "interactive"} else "journal_worker_quarantined"
                        self._mark_terminal(record, "failed", error, {"reason": error})
                        self._bump_counter(record.task, "failed")
                        continue
                    # Mark the record in flight before starting cold process capacity. Product reads
                    # can now return `pending` without waiting for ProcessPoolExecutor startup.
                    record.status = "running"
                    record.running_started_at = time.time()
                    record.running_started_monotonic = time.monotonic()
                    record.executor_slot = slot_index
                    record.executor_generation = slot.generation
                    dispatch.append(record)
                    active_slots.add(slot_index)
        for record, task_result in pending_artifacts:
            self._finalize_artifact(record, task_result)
        for record in dispatch:
            try:
                try:
                    executor = self._executor(record.priority, record.executor_slot)
                except TypeError:
                    # Focused test doubles from before slot ownership deliberately expose the old
                    # one-argument seam; retain it while production always chooses a concrete slot.
                    executor = self._executor(record.priority)
                future = executor.submit(
                    run_registered_task_result,
                    record.task,
                    record.payload,
                    BatchedTaskControl(deadline_monotonic=record.deadline_at or None),
                )
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
                        self._retire_broken_slot(record)
                continue
            with self.state_lock:
                record.future = future
            # Queue submission wakes the scheduler, but worker completion otherwise waits for the
            # 50 ms maintenance poll before `_handle_finished_futures` can publish the product.
            future.add_done_callback(lambda _completed: self.scheduler_event.set())
        with self.state_lock:
            self._finish_requested_shutdown_if_drained()

    def _record_scheduler_pump_failure(self, exc: Exception, traceback_text: str) -> None:
        self.scheduler_pump_failures += 1
        self.scheduler_pump_last_failure = {
            "exception_type": type(exc).__name__,
            "reason": redact_local_service_text(exc),
            "traceback": redact_local_service_text(traceback_text),
        }

    def _queue_record(
        self,
        task: str,
        payload: dict[str, Any],
        priority: str,
        generation: int,
        coalesce_key: str,
        deadline_at: float = 0.0,
        *,
        payload_bytes: bytes | None = None,
    ) -> JobRecord:
        encoded = (
            payload_bytes
            if payload_bytes is not None
            else json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
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
        if priority not in BATCHD_PRIORITIES:
            return None, {"ok": False, "error": "invalid priority"}
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return None, {"ok": False, "error": "payload must be an object"}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > BATCHD_MAX_PAYLOAD_BYTES:
            return None, {"ok": False, "error": "payload too large"}
        try:
            generation = max(0, int(request.get("generation") or 0))
            requested_deadline_ms = int(request.get("deadline_ms") or 0)
        except (TypeError, ValueError):
            return None, {"ok": False, "error": "invalid generation or deadline"}
        if requested_deadline_ms < 0:
            return None, {"ok": False, "error": "invalid deadline"}
        if requested_deadline_ms > BATCHD_MAX_DEADLINE_MS:
            return None, {"ok": False, "error": "deadline too large"}
        coalesce_key = str(request.get("coalesce_key") or f"{task}:{encoded.hex()}")[:256]
        return {
            "task": task,
            "priority": priority,
            "payload": payload,
            "payload_bytes": encoded,
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
        payload_bytes = submission["payload_bytes"]
        if not isinstance(payload_bytes, bytes):
            raise ValueError("validated submission payload bytes are invalid")
        generation = int(submission["generation"])
        deadline_at = float(submission["deadline_at"])
        coalesce_key = str(submission["coalesce_key"])
        fresh_only = submission.get("fresh_only") is True
        reusable_states = {"queued", "running"} if fresh_only else {"queued", "running", "completed"}
        with self.lifecycle_admission_lock:
            if self.shutdown_requested.is_set():
                return {
                    "ok": False,
                    "error": LOCAL_SERVICE_ERROR_BUSY,
                    "admission_rejected": True,
                }
            existing_id = self.coalesced.get((task, coalesce_key))
            existing = self.records.get(existing_id or "")
            if existing is not None and existing.generation >= generation and existing.status in reusable_states:
                self._bump_counter(task, "coalesced")
                return {"ok": True, "coalesced": True, "job": self._record_payload(existing)}
            if self._queued_count(lane=self._lane_for_priority(priority)) >= BATCHD_MAX_QUEUE:
                return {"ok": False, "error": "queue full"}
            self.latest_generation[coalesce_key] = max(generation, self.latest_generation.get(coalesce_key, generation))
            self._supersede_stale_queued(coalesce_key, generation)
            record = self._queue_record(
                task,
                payload,
                priority,
                generation,
                coalesce_key,
                deadline_at,
                payload_bytes=payload_bytes,
            )
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
        latest_gen = self.latest_generation.get(coalesce_key, 0)
        inflight = any(record.coalesce_key == coalesce_key and record.status in {"queued", "running"} for record in self.records.values())
        return self.product_store.product(
            coalesce_key,
            latest_generation=latest_gen,
            inflight=inflight,
            source_epoch=self.source_epoch,
        )

    def _produce(self, request: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        """Submit one product job and return materialized bytes or its accepted receipt.

        This is intentionally a zero-wait operation. The broker can atomically submit and inspect
        its product store, but waiting here would hold one of BATCHD_CONCURRENT_HANDLER_LIMIT
        handler slots for the whole job; enough of those and later callers are refused with
        `service busy` instead of being served. No batchd action waits: a caller with no receipt
        protocol (a browser byte download) submits with `delivery="ready_or_receipt"` and polls
        `product` on its own side. Result bytes remain opaque so a bounded batch keeps every item
        id and result exactly as its registered task emitted them.
        """
        delivery = str(request.get("delivery") or "ready_or_receipt")
        if delivery not in BATCHD_PRODUCT_DELIVERY_MODES:
            return {"ok": False, "error": "invalid product delivery mode"}, b""
        self._refresh_records()
        submission, error = self._validated_submission(request)
        if submission is None:
            return error or {"ok": False, "error": "invalid submission"}, b""
        coalesce_key = str(submission["coalesce_key"])
        stored = self.product_store.submission_identity(coalesce_key)
        stored_generation = stored[0] if stored is not None else -1
        if submission.get("fresh_only") is not True and delivery == "ready_or_receipt" and stored_generation >= int(submission["generation"]):
            product_response, body = self._product({"coalesce_key": coalesce_key})
            state = str(product_response.get("state") or "")
            if (body or product_response.get("artifact") is True) and (state == "ready" or request.get("allow_stale") is True):
                existing_id = self.coalesced.get((str(submission["task"]), coalesce_key))
                existing = self.records.get(existing_id or "")
                job = self._record_payload(existing) if existing is not None else {
                    "job_id": "",
                    "task": str(submission["task"]),
                    "priority": str(submission["priority"]),
                    "generation": int(stored_generation),
                    "status": "completed",
                    "submitted_at": 0.0,
                    "running_started_at": 0.0,
                    "completed_at": float(stored[1]),
                    "deadline_at": 0.0,
                    "error": "",
                }
                return {
                    "ok": True,
                    "state": state,
                    "artifact": product_response.get("artifact") is True,
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
        if product_response.get("artifact") is True and (state == "ready" or (allow_stale and state == "stale")):
            response["state"] = state
            response["artifact"] = True
            response["product"] = dict(product_response.get("product") or {})
            response["schedule"] = dict(product_response.get("schedule") or {})
            return response, b""
        return response, b""

    def _control_plane_identity(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": BATCHD_PROTOCOL_VERSION,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "source_epoch": self.source_epoch,
        }

    def common_status(self) -> dict[str, Any]:
        reap_dead_client_leases(self.leases)
        self._refresh_records()
        with self.request_counter_lock:
            request_counters = dict(self.request_counters)
            contention_counters = dict(self.contention_counters)
        active_records = [
            self._record_payload(record)
            for record in self.records.values()
            if record.status == "running"
        ]
        worker_pids = sorted({
            int(process.pid)
            for slots in self.executor_slots.values()
            for slot in slots
            for executor in ([slot.executor] + [predecessor for _generation, predecessor in slot.predecessors])
            if executor is not None
            for process in self._executor_workers(executor)
            if process.pid is not None
        } | {
            int(process.pid)
            for executor in self.executors.values()
            if executor is not None
            for process in self._executor_workers(executor)
            if process.pid is not None
        })
        status = {
            **self._control_plane_identity(),
            "socket": str(self.socket_path),
            "clients": len(self.leases),
            "worker_count": sum(self._lane_capacity(lane) for lane in BATCHD_LANE_PRIORITIES),
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
                for lane, priorities in BATCHD_LANE_PRIORITIES.items()
            },
            "executor_slots": {
                lane: [
                    {
                        "generation": slot.generation,
                        "quarantined_predecessors": len(slot.predecessors),
                        "worker_pids": sorted(int(process.pid) for executor in ([slot.executor] + [predecessor for _generation, predecessor in slot.predecessors]) if executor is not None for process in self._executor_workers(executor) if process.pid is not None),
                    }
                    for slot in self.executor_slots[lane]
                ]
                for lane in BATCHD_LANE_PRIORITIES
            },
            "queues": {priority: sum(1 for job_id in queue if self.records.get(job_id, JobRecord("", "", b"", priority, 0, "", 0)).status == "queued") for priority, queue in self.queues.items()},
            "active_task": next((record.task for record in self.records.values() if record.status == "running"), ""),
            "active_records": active_records,
            "worker_pids": worker_pids,
            "cache": {
                "records": len(self.records), "coalesced": len(self.coalesced), "record_limit": BATCHD_MAX_RECORDS,
                "products": self.product_store.inline_count(),
                # A stored product is "stale" when a newer generation for the same coalesce_key has
                # since been observed (queued, running, or already completed elsewhere) -- an honest
                # age/staleness signal without exposing raw product bytes/keys in diagnostics.
                "products_stale": self.product_store.stale_inline_count(self.latest_generation),
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
            "owner_invocations": dict(self.owner_invocations),
            "source_change_counters": dict(self.source_change_counters),
            "session_files_accepted_requester_counters": dict(self.session_files_accepted_requester_counters),
            "session_files_requester_counters": dict(self.session_files_requester_counters),
            "request_counters": request_counters,
            "contention_counters": contention_counters,
            "last_success": max((record.completed_at for record in self.records.values() if record.status == "completed"), default=0.0),
            # A retained WORK-ITEM failure, not a daemon condition. This scans the bounded record
            # ring, so one failed or timed-out job keeps describing a daemon that has served every
            # request since, and only ring eviction ever drops it -- a later success does not.
            # It must therefore never be published as `last_failure`: `local_service_failure_text`
            # feeds that name to `observed_health`, which reads any `last_failure` on a live pid as
            # CURRENT degradation and pins a healthy batchd to `degraded`/`terminal_failure` forever.
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
        self._last_status_snapshot = copy.deepcopy(status)
        return status

    def handle(self, request: dict[str, object], _request_binary: bytes = b"") -> tuple[dict[str, object], bytes]:
        protocol_version = request.get("protocol_version", BATCHD_PROTOCOL_VERSION)
        if protocol_version != BATCHD_PROTOCOL_VERSION:
            return {
                "ok": False,
                "error": "upgrade_required",
                "required_protocol_version": BATCHD_PROTOCOL_VERSION,
            }, b""
        action = str(request.get("action") or "")
        self._record_request_action(action)
        # Every broker action is documented as zero-wait. Contention with the scheduler pump is
        # therefore overload, not permission for an RPC handler to sit behind this lock until the
        # caller's transport deadline turns a healthy broker into an ERROR.
        if not self.state_lock.acquire(blocking=False):
            self._record_request_action(action, contention=True)
            if action == "ping":
                return self._handle_ping(request, b"")
            if action == "status":
                snapshot = copy.deepcopy(self._last_status_snapshot)
                with self.request_counter_lock:
                    snapshot["request_counters"] = dict(self.request_counters)
                    snapshot["contention_counters"] = dict(self.contention_counters)
                return {**snapshot, "busy": True}, b""
            # Shutdown closes admission through its own short lock and lets already accepted work
            # drain. It does not need the contended state lock merely to record that transition.
            if action == "shutdown":
                refusal = self._retirement_shutdown_epoch_refusal(request)
                if refusal is not None:
                    return refusal, b""
                self._request_shutdown()
                if request.get("retirement_handshake") is True:
                    # The lock owner may have accepted work after the last published status
                    # snapshot. Conservatively report draining so a replacing registry never
                    # treats stale zero-work telemetry as permission to terminate that work.
                    return {
                        **self._control_plane_identity(),
                        "shutdown": True,
                        "draining": True,
                    }, b""
                return {"ok": True, "shutdown": True}, b""
            return {"ok": False, "error": LOCAL_SERVICE_ERROR_BUSY, "state_lock_rejected": True}, b""
        try:
            return self._handle_locked(request)
        finally:
            self.state_lock.release()

    def _handle_locked(self, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        action = str(request.get("action") or "")
        if action in BATCHD_ARTIFACT_ACTION_METHODS:
            self._refresh_records()
            response = self.product_store.handle(action, request, b"")
        else:
            response = BATCHD_COMMAND_ROUTER.dispatch(self, action, request, b"")
        return response if response is not None else ({"ok": False, "error": "unknown batchd action"}, b"")

    def _handle_ping(self, _request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._control_plane_identity(), b""

    def _handle_status(self, _request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self.common_status(), b""

    def _handle_profile(self, _request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return CommonDaemonActions.status(self.common_status, profile=True)

    def _handle_submit(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._submit(request), b""

    def _handle_result(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        self._refresh_records()
        record = self.records.get(str(request.get("job_id") or ""))
        return ({"ok": False, "error": "unknown job"} if record is None else {"ok": True, "job": self._record_payload(record, include_result=True)}), b""

    def _handle_product(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        self._refresh_records()
        return self._product(request)

    def _handle_produce(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._produce(request)

    def _handle_cancel(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
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

    def _handle_lease(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        response = acquire_client_lease(self.leases, request.get("client_pid"), request.get("lease_id"), self_connection=request_is_self_connection(request))
        return {**response, "version": BATCHD_PROTOCOL_VERSION}, b""

    def _handle_release(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return release_client_lease(self.leases, request.get("lease_id")), b""

    def _request_shutdown(self) -> None:
        with self.lifecycle_admission_lock:
            self.shutdown_requested.set()
        self.scheduler_event.set()

    def _retirement_shutdown_epoch_refusal(
        self,
        request: dict[str, object],
    ) -> dict[str, object] | None:
        if request.get("retirement_handshake") is not True:
            return None
        expected_source_epoch = request.get("expected_source_epoch")
        if expected_source_epoch is None or expected_source_epoch == self.source_epoch:
            return None
        return {
            **self._control_plane_identity(),
            "ok": False,
            "error": "source_epoch_mismatch",
            "shutdown": False,
        }

    def _handle_shutdown(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        refusal = self._retirement_shutdown_epoch_refusal(request)
        if refusal is not None:
            return refusal, b""
        self._request_shutdown()
        drained = self._finish_requested_shutdown_if_drained()
        if request.get("retirement_handshake") is True:
            return {
                **self._control_plane_identity(),
                "shutdown": True,
                "draining": not drained,
            }, b""
        return {"ok": True, "shutdown": True}, b""

    def _handle_shutdown_if_idle(self, request: dict[str, object], body: bytes) -> tuple[dict[str, object], bytes]:
        # ONE definition of idle. This used to gate on `self.leases` alone while
        # `_idle_should_stop` also honoured `_has_active_work()`, so a caller
        # could shut batchd down out from under queued or running jobs simply by
        # asking through this path instead of waiting for the idle tick. It also
        # reaps first, so a crashed client's unreleasable lease cannot refuse a
        # legitimate idle shutdown either.
        with self.state_lock:
            reap_dead_client_leases(self.leases)
            busy = bool(self.leases) or self._has_active_work()
            lease_count = len(self.leases)
        if busy:
            return {"ok": True, "shutdown": False, "leases": lease_count}, b""
        return self._handle_shutdown(request, body)

    def _scheduler_loop(self) -> None:
        # Control-plane readiness belongs to the listener. Lower priority only after the first
        # data-plane submission has started this scheduler, so a saturated host can still answer
        # ping/status before background work competes for CPU.
        apply_service_process_priority()
        while not self.stop_event.is_set():
            self.scheduler_event.wait(BATCHD_SCHEDULER_POLL_SECONDS)
            self.scheduler_event.clear()
            try:
                self._pump()
            except Exception as exc:
                self._record_scheduler_pump_failure(exc, traceback.format_exc())

    def _has_active_work(self) -> bool:
        return bool(self._queued_count()) or any(record.status == "running" for record in self.records.values()) or self._quarantined_predecessor_count() > 0

    def _finish_requested_shutdown_if_drained(self) -> bool:
        if self.shutdown_requested.is_set() and not self._has_active_work():
            self.stop_event.set()
            return True
        return False

    def _start_scheduler(self) -> None:
        with self.scheduler_start_lock:
            if self.scheduler_thread is not None and self.scheduler_thread.is_alive():
                return
            self.scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                name="batchd-scheduler",
                daemon=True,
            )
            self.scheduler_thread.start()

    def _start_scheduler_after_listener_accepts(self) -> None:
        """Arm data-plane maintenance only after the listener completes one private accept."""
        def activate() -> None:
            try:
                envelope = new_envelope(self.service_name, "ping", {"action": "ping", "protocol_version": BATCHD_PROTOCOL_VERSION}, timeout_seconds=1.0)
                response, _binary = local_service_request(self.socket_path, envelope, timeout_seconds=1.0)
                if response.get("ok") is not True:
                    raise RuntimeError("batchd listener readiness ping failed")
            except (OSError, RuntimeError) as exc:
                self._record_scheduler_pump_failure(exc, traceback.format_exc())
                return
            self._start_scheduler()

        self.scheduler_readiness_thread = threading.Thread(target=activate, name="batchd-listener-readiness", daemon=True)
        self.scheduler_readiness_thread.start()

    def _idle_should_stop(self) -> bool:
        with self.state_lock:
            reap_dead_client_leases(self.leases)
            if self._finish_requested_shutdown_if_drained():
                return True
            # claim_gated_idle_due is the one shared owner of the
            # transition/deadline algorithm every local service routes
            # through; batchd's claim predicate is a held lease OR real
            # queued/running work (a bare diagnostic status poll from the
            # backend-health observer is neither).
            return claim_gated_idle_due(self, self.leases or self._has_active_work())

    def _on_shutdown(self) -> None:
        """Stop the dispatcher before retiring the pools it is allowed to populate."""

        self.scheduler_event.set()
        if self.scheduler_readiness_thread is not None:
            self.scheduler_readiness_thread.join(timeout=BATCHD_SCHEDULER_SHUTDOWN_SECONDS)
            if self.scheduler_readiness_thread.is_alive():
                raise RuntimeError("batchd scheduler readiness did not stop before executor retirement")
        if self.scheduler_thread is not None:
            self.scheduler_thread.join(timeout=BATCHD_SCHEDULER_SHUTDOWN_SECONDS)
            if self.scheduler_thread.is_alive():
                raise RuntimeError("batchd scheduler did not stop before executor retirement")
        for lane in BATCHD_LANE_PRIORITIES:
            self._shutdown_executor(lane=lane)
        self.product_store.shutdown()

    def run(self) -> int:
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name=self.service_name,
            stop_event=self.stop_event,
            handle=self.handle,
            on_idle=self._idle_should_stop,
            # _idle_should_stop refreshes last_client_at directly whenever a
            # lease or active work exists (see above); a connection-level
            # callback here would count a bare diagnostic RPC (e.g. the
            # backend-health observer's periodic status poll) as demand
            # regardless of whether any real claim exists.
            on_client=lambda: None,
            on_idle_failure=self._record_scheduler_pump_failure,
            on_start=self._start_scheduler_after_listener_accepts,
            on_shutdown=self._on_shutdown,
            concurrent_handlers=BATCHD_CONCURRENT_HANDLER_LIMIT,
        )


class BatchClient(LocalServiceClient):
    """Thin cross-port client for the shared stateless CPU broker."""

    def __init__(
        self,
        socket_path: Path | None = None,
        *,
        service_name: str = BATCHD_SERVICE_NAME,
        module: str = "yolomux_lib.batchd",
        default_socket: Path | None = None,
        protocol_version: int = BATCHD_PROTOCOL_VERSION,
        idle_seconds: float = BATCHD_DEFAULT_IDLE_SECONDS,
    ):
        requested_socket_path = Path(socket_path or default_socket or default_socket_path())
        requested_service_dir = Path(socket_path).parent if socket_path is not None else RUNTIME_DIR / "services"
        super().__init__(
            service_name,
            module,
            requested_socket_path,
            protocol_version,
            idle_seconds=idle_seconds,
            service_dir=requested_service_dir,
        )
        self._scheduler_lease_id = ""
        self._scheduler_lease_lock = threading.Lock()

    def start_for_scheduler(self) -> bool:
        """Keep batchd leased while this process owns background scheduling."""
        with self._scheduler_lease_lock:
            response = self.registry.acquire_lease(self._scheduler_lease_id)
            lease_id = response.get("lease_id")
            if response.get("ok") is not True or not isinstance(lease_id, str) or not lease_id:
                return False
            self._scheduler_lease_id = lease_id
            return True

    @property
    def holds_scheduler_lease(self) -> bool:
        """Whether this process currently pins batchd up for background scheduling.

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

    def submit(self, task: str, payload: dict[str, Any], *, priority: str = "freshness", generation: int = 0, coalesce_key: str = "", deadline_ms: int = 0, fresh_only: bool = False, launch: bool = True) -> dict[str, Any]:
        """Submit a job. `launch=False` asks an already-running batchd and never cold-starts one.

        Maintenance must never be the reason a service starts. `result` and `product` already read
        through the non-launching twin for the same reason - observation is not launch demand - and
        `launch=False` extends that one rule to submissions whose work is housekeeping. A denied
        submission is not lost work: the next submission made for real demand runs it, and batchd is
        up by definition whenever real demand exists.
        """
        request = {"action": "submit", "task": task, "payload": payload, "priority": priority, "generation": generation, "coalesce_key": coalesce_key, "deadline_ms": deadline_ms}
        if fresh_only:
            request["fresh_only"] = True
        return self.request(request) if launch else self.request_if_running(request)

    def result(self, job_id: str, timeout: float = BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS) -> dict[str, Any]:
        return self.request_if_running({"action": "result", "job_id": job_id}, timeout=timeout)

    def product(self, coalesce_key: str, timeout: float = BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS) -> tuple[dict[str, Any], bytes]:
        """Return the newest completed product bytes for an identity (last-known-good).

        The metadata `state` is ready | stale | pending | none; the caller maps a transport
        failure to unavailable. Bytes are empty unless a completed product exists.
        """
        return self.request_with_binary_if_running(
            {"action": "product", "coalesce_key": coalesce_key},
            timeout=timeout,
        )

    def artifact_open(self, coalesce_key: str, generation: int) -> dict[str, Any]:
        return self.request({"action": "artifact_open", "coalesce_key": coalesce_key, "generation": generation})

    def artifact_chunk(self, lease_id: str, offset: int, max_bytes: int = BATCHD_ARTIFACT_CHUNK_BYTES) -> tuple[dict[str, Any], bytes]:
        return self.request_with_binary({
            "action": "artifact_chunk",
            "lease_id": lease_id,
            "offset": offset,
            "max_bytes": min(max_bytes, BATCHD_ARTIFACT_CHUNK_BYTES),
        })

    def artifact_close(self, lease_id: str) -> dict[str, Any]:
        return self.request({"action": "artifact_close", "lease_id": lease_id})

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.request({"action": "cancel", "job_id": job_id})

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
        launch: bool = True,
    ) -> tuple[dict[str, Any], bytes]:
        """Submit once and forward ready product bytes without waiting for cold work.

        `launch=False` selects the non-launching twin, so a maintenance produce cannot cold-start
        batchd. That start is not free: measured inside the gate container it took 1.19-1.41 s, and
        when it happened underneath a forced interactive canonical operation it consumed 61-73% of
        that operation's two-second terminalization budget while the file itself had already been
        read. See `BatchClient.submit` for why declining is safe.
        """
        sender = self.request_with_binary if launch else self.request_with_binary_if_running
        return sender({
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
        return self._runtime_status_for_service(self.service)

    def _runtime_status_for_service(self, service_name: str) -> dict[str, Any]:
        """Build this broker's whole System/health row.

        No ``demand_started`` here on purpose: the scheduler lease pins batchd up, so its absence
        while this process owns scheduling is a verified outage, not idleness. See
        ``BATCHD_ABSENT_WITHOUT_SCHEDULER_LEASE`` for the one absence that is expected instead.
        """
        status = self.registry.status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        worker_pids: list[int] = []
        if isinstance(payload.get("worker_pids"), list):
            for value in payload["worker_pids"]:
                try:
                    worker_pid = int(value)
                except (TypeError, ValueError):
                    continue
                if worker_pid > 0:
                    worker_pids.append(worker_pid)
        return registry_runtime_row(service_name, self.registry, status, payload, resource_pids=worker_pids, include_version=False, fields_before_failure={
            "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {},
            "active_task": str(payload.get("active_task") or ""),
            "active_records": payload.get("active_records") if isinstance(payload.get("active_records"), list) else [],
            "worker_count": int(payload.get("worker_count") or len(worker_pids)),
            "worker_pids": worker_pids,
            "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
            "product_counters": payload.get("product_counters") if isinstance(payload.get("product_counters"), dict) else {},
            "product_runtime_ms": payload.get("product_runtime_ms") if isinstance(payload.get("product_runtime_ms"), dict) else {},
            "product_phase_runtime_ms": payload.get("product_phase_runtime_ms") if isinstance(payload.get("product_phase_runtime_ms"), dict) else {},
            "product_work_totals": payload.get("product_work_totals") if isinstance(payload.get("product_work_totals"), dict) else {},
            "owner_invocations": payload.get("owner_invocations") if isinstance(payload.get("owner_invocations"), dict) else {},
            "source_change_counters": payload.get("source_change_counters") if isinstance(payload.get("source_change_counters"), dict) else {},
            "session_files_accepted_requester_counters": payload.get("session_files_accepted_requester_counters") if isinstance(payload.get("session_files_accepted_requester_counters"), dict) else {},
            "session_files_requester_counters": payload.get("session_files_requester_counters") if isinstance(payload.get("session_files_requester_counters"), dict) else {},
            "request_counters": payload.get("request_counters") if isinstance(payload.get("request_counters"), dict) else {},
            "contention_counters": payload.get("contention_counters") if isinstance(payload.get("contention_counters"), dict) else {},
            "generation": int(payload.get("generation") or 0),
            "last_success": float(payload.get("last_success") or 0.0),
        }, fields_after_failure={
            "last_job_failure": str(payload.get("last_job_failure") or ""),
            "scheduler_pump": payload.get("scheduler_pump") if isinstance(payload.get("scheduler_pump"), dict) else {},
            "absence_expected_reason": "" if self.holds_scheduler_lease else BATCHD_ABSENT_WITHOUT_SCHEDULER_LEASE,
        })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux bounded CPU job broker")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--idle-seconds", type=float, default=BATCHD_DEFAULT_IDLE_SECONDS)
    parser.add_argument("--workers", type=int, default=default_worker_count())
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    return PersistentJobBroker(Path(args.socket), idle_seconds=args.idle_seconds, workers=args.workers).run()


if __name__ == "__main__":
    raise SystemExit(main())
