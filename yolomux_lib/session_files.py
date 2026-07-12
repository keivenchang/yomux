# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Repo-aware AI file-change attribution for live sessions."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import os
import re
import shlex
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any
from typing import Callable

from . import common
from .atomic_file import atomic_write_text
from .atomic_file import file_lock
from .common import AgentInfo
from .common import SessionInfo
from .common import git
from .common import git_ahead_behind_counts
from .common import is_generated_upload_name
from .common import positive_finite_number
from .common import path_mtime_or_zero
from .filesystem import git_root_for_path
from .filesystem.git_ops import diff_refs
from .filesystem.git_ops import git_ref_exists
from .filesystem.git_ops import normal_ref
from .filesystem.git_ops import refs_requested
from .locales import message_descriptor
from .locales import user_message_payload
from .sessions import claude_transcript_family_paths
from .sessions import codex_transcript_family_paths
from .sessions import CODEX_TRANSCRIPT_SCAN_LIMIT
from .sessions import find_recent_codex_transcript
from .sessions import recent_codex_transcript_candidates
from .types import RepoPayload
from .types import SessionFileEntry
from .types import SessionFilesPayload
from .workdir import session_workdir


CLAUDE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CODEX_PATCH_RE = re.compile(r"\*\*\* (Add|Update|Delete) File: ([^\"\\\n]+)")
CODEX_USAGE_VALUE_RE = re.compile(r'"(?:output_tokens|outputTokens|completion_tokens|completionTokens|generated_tokens|generatedTokens|reasoning_output_tokens|reasoningOutputTokens)"\s*:\s*([0-9]+(?:\.[0-9]+)?)')
CODEX_PATCH_STATUS = {"Add": "A", "Update": "M", "Delete": "D"}
CODEX_SHELL_TOOL_NAMES = {"exec_command", "shell_command", "shell"}
SHELL_COMMAND_BREAK_TOKENS = {"&&", "||", ";", "|"}
SHELL_RUNNERS = {"bash", "sh", "zsh"}
SESSION_FILES_MAX_HOURS = 24 * 14
SESSION_FILES_CUTOFF_GRACE_SECONDS = 60.0
# Keep both newest filename and newest-mtime candidate windows resident. Raw Codex state no longer
# multiplies by caller cwd, so this covers the full shared historical window without cache churn.
_TRANSCRIPT_SCAN_CACHE_MAX = CODEX_TRANSCRIPT_SCAN_LIMIT * 2
_TRANSCRIPT_SCAN_PREFIX_BYTES = 64 * 1024
_TRANSCRIPT_SCAN_TAIL_BYTES = 512
_TRANSCRIPT_REVERSE_SCAN_BYTES = 64 * 1024
_CODEX_TRANSCRIPT_SCAN_VERSION = 7
_CLAUDE_TRANSCRIPT_SCAN_VERSION = 5
_TRANSCRIPT_SCAN_STORE_VERSION = 2
_TRANSCRIPT_SCAN_STORE_MAX_BYTES = 64 * 1024 * 1024
_TRANSCRIPT_SCAN_STORE_PRUNE_SECONDS = 60.0
_TRANSCRIPT_SCAN_MESSAGE_ID_MAX = 4096
_TRANSCRIPT_SCAN_PERSIST_MIN_BYTES = 64 * 1024
_TRANSCRIPT_SCAN_PERSIST_APPEND_BYTES = 256 * 1024
_TRANSCRIPT_SCAN_PERSIST_INTERVAL_SECONDS = 30.0


@dataclass
class TranscriptScanRecord:
    identity: tuple[Any, ...]
    state: dict[str, Any]
    lock: threading.RLock = field(default_factory=threading.RLock)
    persisted_offset: int = 0
    persisted_at: float = 0.0


@dataclass(frozen=True)
class TranscriptUsageEvent:
    """One timestamped increment in an agent transcript's generated-token counter."""

    source: str
    timestamp: float
    tokens: float
    model: str = ""


@dataclass(frozen=True)
class TranscriptUsageAtom:
    """One provider-reported billable usage component.

    ``TranscriptUsageEvent`` is deliberately retained as the compatibility
    projection consumed by the existing output-token chart.  New consumers use
    this lossless-enough, component-level record instead: provider fields are
    normalized before they reach any cost calculation, and unknown fields stay
    unknown rather than being converted to a zero-valued component.
    """

    source: str
    timestamp: float
    event_id: str
    provider: str
    model: str
    model_evidence: str
    effort: str
    direction: str
    modality: str
    cache_role: str
    unit: str
    quantity: float
    root_thread_id: str = ""
    agent_thread_id: str = ""
    parent_thread_id: str = ""
    depth: int = 0
    endpoint: str = ""
    tool_name: str = ""
    call_id: str = ""
    pricing_profile: str = "default"
    service_tier: str = "default"
    telemetry_complete: bool = False


@dataclass(frozen=True)
class HistoricalCodexTranscriptRecord:
    """One bounded, raw-parsed candidate used by historical cwd lookups."""

    path: Path
    mtime: float
    raw_shell_changes: dict[str, set[str]]


_TRANSCRIPT_SCAN_CACHE: dict[tuple[Any, ...], TranscriptScanRecord] = {}
_TRANSCRIPT_SCAN_CACHE_GUARD = threading.RLock()
_TRANSCRIPT_SCAN_CACHE_STATE_DIR: Path | None = None
_TRANSCRIPT_SCAN_STORE_NEXT_PRUNE = 0.0
_HISTORICAL_CODEX_TRANSCRIPT_INDEX: dict[tuple[tuple[str, int, int, int, int], ...], tuple[HistoricalCodexTranscriptRecord, ...]] = {}
_HISTORICAL_CODEX_TRANSCRIPT_INDEX_GUARD = threading.RLock()
_HISTORICAL_CODEX_TRANSCRIPT_INDEX_MAX = 4
_CODEX_RELATIVE_CWD_SENTINEL = "/__yolomux_codex_relative_cwd__"

logger = logging.getLogger(__name__)

SessionFilesPhaseRecorder = Callable[[str, float, dict[str, Any]], None]
GitSnapshotProvider = Callable[[Path, str | None, str | None], dict[str, Any]]


def classify_change(markers: set[str]) -> str:
    if "A" in markers:
        return "A"
    if "D" in markers:
        return "D"
    return "M"


def resolved_change_path(raw_path: str, cwd: str | None) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        if not cwd:
            return None
        path = Path(cwd).expanduser() / path
    return Path(os.path.abspath(os.fspath(path)))


file_mtime = path_mtime_or_zero


def file_size(path: Path) -> int | None:
    # C5: Modified-files rows need the same size Finder gets from /api/fs/list so the image hover preview
    # can enforce the same "only preview images under the cap" rule. None when the file is gone (deleted).
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


def file_mtime_or_fallback(path: Path, fallback: Any = 0.0) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        pass
    try:
        return float(fallback) if fallback not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def scan_claude_transcript(path: Path, cwd: str | None = None) -> dict[str, set[str]]:
    return copy_change_set(scan_claude_transcript_details(path, cwd).get("changes", {}))


def scan_claude_transcript_details(path: Path, cwd: str | None = None) -> dict[str, Any]:
    # Cache the raw file once. candidate_session_cwds can ask about the same transcript from more
    # than one cwd; resolving paths only after parsing avoids repeating a growing JSONL scan per cwd.
    cache_key = claude_transcript_scan_cache_key(path)
    return incremental_transcript_scan_details(
        path,
        cache_key,
        new_claude_transcript_scan_state,
        update=lambda state, line: update_claude_transcript_scan_state(state, line),
        details=lambda state: claude_transcript_scan_state_details(state, cwd),
    )


def claude_transcript_scan_cache_key(path: Path) -> tuple[str, int, int, int, str] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        "claude",
        _CLAUDE_TRANSCRIPT_SCAN_VERSION,
        int(stat.st_dev),
        int(stat.st_ino),
        str(path.expanduser().resolve(strict=False)),
    )


def scan_codex_transcript(path: Path, cwd: str | None = None, include_patch_text: bool = True) -> dict[str, set[str]]:
    return copy_change_set(scan_codex_transcript_details(path, cwd, include_patch_text, include_usage=False).get("changes", {}))


def scan_codex_transcript_details(path: Path, cwd: str | None = None, include_patch_text: bool = True, include_usage: bool = True) -> dict[str, Any]:
    # Codex records relative paths. Parse those bytes once with a synthetic cwd, then resolve the
    # raw paths for the caller. This mirrors Claude's raw-state owner and prevents one growing
    # rollout from being reparsed for every pane/repo cwd.
    cache_key = codex_transcript_scan_cache_key(path, cwd, include_patch_text, include_usage)
    return incremental_transcript_scan_details(
        path,
        cache_key,
        new_codex_transcript_scan_state,
        update=lambda state, line: update_codex_transcript_scan_state(state, line),
        details=lambda state: codex_transcript_scan_state_details(state, cwd, include_patch_text, include_usage),
    )


def codex_transcript_scan_cache_key(path: Path, cwd: str | None = None, include_patch_text: bool = True, include_usage: bool = True) -> tuple[str, int, int, int, str] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        "codex",
        _CODEX_TRANSCRIPT_SCAN_VERSION,
        int(stat.st_dev),
        int(stat.st_ino),
        str(path.expanduser().resolve(strict=False)),
    )


def new_codex_transcript_scan_state() -> dict[str, Any]:
    return {
        "offset": 0,
        "size": 0,
        "patch_changes": {},
        "shell_changes": {},
        "last_token_total": None,
        "summed_last_generated_tokens": 0.0,
        "generated_tokens_by_model": {},
        "model": "",
        "parsed_tail": b"",
        "prefix_digest": "",
    }


def new_claude_transcript_scan_state() -> dict[str, Any]:
    return {
        "offset": 0,
        "size": 0,
        "raw_changes": {},
        "generated_tokens": 0.0,
        "usage_tokens_by_message_id": {},
        "generated_tokens_by_model": {},
        "model": "",
        "parsed_tail": b"",
        "prefix_digest": "",
    }


def transcript_scan_store_dir() -> Path:
    return common.STATE_DIR / f"transcript-scan-cache-v{_TRANSCRIPT_SCAN_STORE_VERSION}"


def transcript_scan_store_path(cache_key: tuple[Any, ...]) -> Path:
    identity_text = json.dumps(list(cache_key), ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(identity_text.encode("utf-8")).hexdigest()
    return transcript_scan_store_dir() / f"{digest}.json"


def transcript_scan_prefix_digest(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return hashlib.sha256(handle.readline(_TRANSCRIPT_SCAN_PREFIX_BYTES)).hexdigest()
    except OSError:
        return ""


def serialized_transcript_marker_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(path): sorted({str(marker) for marker in markers if str(marker) in {"A", "M", "D"}})
        for path, markers in value.items()
        if isinstance(markers, (set, list, tuple))
    }


def transcript_scan_state_payload(cache_key: tuple[Any, ...], state: dict[str, Any]) -> dict[str, Any]:
    provider = str(cache_key[0])
    payload: dict[str, Any] = {
        "offset": max(0, int(state.get("offset") or 0)),
        "size": max(0, int(state.get("size") or 0)),
        "prefix_digest": str(state.get("prefix_digest") or ""),
        "parsed_tail_b64": base64.b64encode(state.get("parsed_tail") if isinstance(state.get("parsed_tail"), bytes) else b"").decode("ascii"),
    }
    if provider == "claude":
        tokens_by_id = state.get("usage_tokens_by_message_id")
        bounded_tokens = list(tokens_by_id.items())[-_TRANSCRIPT_SCAN_MESSAGE_ID_MAX:] if isinstance(tokens_by_id, dict) else []
        payload.update({
            "raw_changes": serialized_transcript_marker_map(state.get("raw_changes")),
            "generated_tokens": positive_finite_number(state.get("generated_tokens")),
            "usage_tokens_by_message_id": {str(key): positive_finite_number(value) for key, value in bounded_tokens},
            "generated_tokens_by_model": transcript_usage_models(state.get("generated_tokens_by_model")),
            "model": transcript_model_name(state.get("model")),
        })
    elif provider == "codex":
        payload.update({
            "patch_changes": serialized_transcript_marker_map(state.get("patch_changes")),
            "shell_changes": serialized_transcript_marker_map(state.get("shell_changes")),
            "last_token_total": positive_finite_number(state.get("last_token_total")) if state.get("last_token_total") is not None else None,
            "summed_last_generated_tokens": positive_finite_number(state.get("summed_last_generated_tokens")),
            "generated_tokens_by_model": transcript_usage_models(state.get("generated_tokens_by_model")),
            "model": transcript_model_name(state.get("model")),
        })
    return payload


def transcript_scan_state_from_payload(cache_key: tuple[Any, ...], payload: Any, new_state: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    state = new_state()
    try:
        state["offset"] = max(0, int(payload.get("offset") or 0))
        state["size"] = max(0, int(payload.get("size") or 0))
        state["prefix_digest"] = str(payload.get("prefix_digest") or "")
        state["parsed_tail"] = base64.b64decode(str(payload.get("parsed_tail_b64") or ""), validate=True)
    except (TypeError, ValueError):
        return None
    provider = str(cache_key[0])
    if provider == "claude":
        state["raw_changes"] = {path: set(markers) for path, markers in serialized_transcript_marker_map(payload.get("raw_changes")).items()}
        state["generated_tokens"] = positive_finite_number(payload.get("generated_tokens"))
        tokens_by_id = payload.get("usage_tokens_by_message_id")
        if isinstance(tokens_by_id, dict):
            state["usage_tokens_by_message_id"] = {
                str(key): positive_finite_number(value)
                for key, value in list(tokens_by_id.items())[-_TRANSCRIPT_SCAN_MESSAGE_ID_MAX:]
            }
        state["generated_tokens_by_model"] = transcript_usage_models(payload.get("generated_tokens_by_model"))
        state["model"] = transcript_model_name(payload.get("model"))
    elif provider == "codex":
        state["patch_changes"] = {path: set(markers) for path, markers in serialized_transcript_marker_map(payload.get("patch_changes")).items()}
        state["shell_changes"] = {path: set(markers) for path, markers in serialized_transcript_marker_map(payload.get("shell_changes")).items()}
        state["last_token_total"] = positive_finite_number(payload.get("last_token_total")) if payload.get("last_token_total") is not None else None
        state["summed_last_generated_tokens"] = positive_finite_number(payload.get("summed_last_generated_tokens"))
        state["generated_tokens_by_model"] = transcript_usage_models(payload.get("generated_tokens_by_model"))
        state["model"] = transcript_model_name(payload.get("model"))
    return state


def load_transcript_scan_state(cache_key: tuple[Any, ...], path: Path, new_state: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    cache_path = transcript_scan_store_path(cache_key)
    if not cache_path.exists():
        return None
    try:
        with file_lock(cache_path, dir_mode=0o700):
            record = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            return None
        if record.get("schema_version") != _TRANSCRIPT_SCAN_STORE_VERSION or record.get("identity") != list(cache_key):
            return None
        state = transcript_scan_state_from_payload(cache_key, record.get("state"), new_state)
        if state is None or transcript_scan_state_needs_reset(path, path.stat().st_size, state):
            return None
        os.utime(cache_path, None)
        return state
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("failed to load transcript scan cache %s: %s", cache_path.name, exc)
        return None


def prune_transcript_scan_store(max_entries: int = _TRANSCRIPT_SCAN_CACHE_MAX, max_bytes: int = _TRANSCRIPT_SCAN_STORE_MAX_BYTES) -> None:
    store_dir = transcript_scan_store_dir()
    try:
        paths = sorted(store_dir.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
    except OSError as exc:
        logger.warning("failed to inspect transcript scan cache: %s", exc)
        return
    retained_bytes = 0
    for index, path in enumerate(paths):
        try:
            size = path.stat().st_size
            if index < max_entries and retained_bytes + size <= max_bytes:
                retained_bytes += size
                continue
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("failed to prune transcript scan cache %s: %s", path.name, exc)


def persist_transcript_scan_state(cache_key: tuple[Any, ...], state: dict[str, Any]) -> bool:
    global _TRANSCRIPT_SCAN_STORE_NEXT_PRUNE
    cache_path = transcript_scan_store_path(cache_key)
    if int(state.get("size") or 0) < _TRANSCRIPT_SCAN_PERSIST_MIN_BYTES and not cache_path.exists():
        return False
    record = {
        "schema_version": _TRANSCRIPT_SCAN_STORE_VERSION,
        "identity": list(cache_key),
        "state": transcript_scan_state_payload(cache_key, state),
    }
    try:
        with file_lock(cache_path, dir_mode=0o700):
            if cache_path.exists():
                existing = json.loads(cache_path.read_text(encoding="utf-8"))
                existing_state = existing.get("state") if isinstance(existing, dict) else None
                if isinstance(existing, dict) and existing.get("identity") == list(cache_key) and isinstance(existing_state, dict) and int(existing_state.get("offset") or 0) > int(state.get("offset") or 0):
                    return False
            atomic_write_text(cache_path, json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True), mode=0o600)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("failed to persist transcript scan cache %s: %s", cache_path.name, exc)
        return False
    now = time.perf_counter()
    if now >= _TRANSCRIPT_SCAN_STORE_NEXT_PRUNE:
        _TRANSCRIPT_SCAN_STORE_NEXT_PRUNE = now + _TRANSCRIPT_SCAN_STORE_PRUNE_SECONDS
        prune_transcript_scan_store()
    return True


def maybe_persist_transcript_scan_record(record: TranscriptScanRecord, force: bool = False) -> None:
    now = time.perf_counter()
    offset = int(record.state.get("offset") or 0)
    cache_exists = transcript_scan_store_path(record.identity).exists()
    if not force:
        if not cache_exists and int(record.state.get("size") or 0) < _TRANSCRIPT_SCAN_PERSIST_MIN_BYTES:
            return
        if cache_exists and offset - record.persisted_offset < _TRANSCRIPT_SCAN_PERSIST_APPEND_BYTES and now - record.persisted_at < _TRANSCRIPT_SCAN_PERSIST_INTERVAL_SECONDS:
            return
    if persist_transcript_scan_state(record.identity, record.state):
        record.persisted_offset = offset
        record.persisted_at = now


def transcript_scan_memory_record(cache_key: tuple[Any, ...], path: Path, new_state: Callable[[], dict[str, Any]]) -> TranscriptScanRecord:
    global _TRANSCRIPT_SCAN_CACHE_STATE_DIR
    store_dir = transcript_scan_store_dir()
    with _TRANSCRIPT_SCAN_CACHE_GUARD:
        if _TRANSCRIPT_SCAN_CACHE_STATE_DIR != store_dir:
            _TRANSCRIPT_SCAN_CACHE.clear()
            _TRANSCRIPT_SCAN_CACHE_STATE_DIR = store_dir
        record = _TRANSCRIPT_SCAN_CACHE.get(cache_key)
        if record is not None:
            return record
        loaded_state = load_transcript_scan_state(cache_key, path, new_state)
        state = loaded_state or new_state()
        record = TranscriptScanRecord(
            cache_key,
            state,
            persisted_offset=int(state.get("offset") or 0) if loaded_state is not None else 0,
            persisted_at=time.perf_counter() if loaded_state is not None else 0.0,
        )
        if len(_TRANSCRIPT_SCAN_CACHE) >= _TRANSCRIPT_SCAN_CACHE_MAX:
            oldest_key = next(iter(_TRANSCRIPT_SCAN_CACHE), None)
            if oldest_key is not None:
                _TRANSCRIPT_SCAN_CACHE.pop(oldest_key, None)
        _TRANSCRIPT_SCAN_CACHE[cache_key] = record
        return record


def incremental_transcript_scan_details(
    path: Path,
    cache_key: tuple[Any, ...] | None,
    new_state: Callable[[], dict[str, Any]],
    *,
    update: Callable[[dict[str, Any], str], None],
    details: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    try:
        current_size = path.stat().st_size
    except OSError:
        return details(new_state())
    if cache_key is None:
        state = new_state()
        try:
            scan_transcript_append(path, 0, lambda line: update(state, line), state)
        except OSError:
            pass
        return details(state)
    record = transcript_scan_memory_record(cache_key, path, new_state)
    with record.lock:
        try:
            current_size = path.stat().st_size
        except OSError:
            return details(record.state)
        reset = transcript_scan_state_needs_reset(path, current_size, record.state)
        if reset:
            record.state = new_state()
        state = record.state
        prefix_digest = transcript_scan_prefix_digest(path)
        if not state.get("prefix_digest"):
            state["prefix_digest"] = prefix_digest
        offset = int(state.get("offset") or 0)
        if offset >= current_size:
            state["size"] = current_size
            if reset:
                maybe_persist_transcript_scan_record(record, force=True)
            return details(state)
        try:
            consumed = scan_transcript_append(path, offset, lambda line: update(state, line), state)
            state["offset"] = offset + consumed
            state["size"] = current_size
            maybe_persist_transcript_scan_record(record, force=reset)
        except OSError:
            return details(state)
        return details(state)


def transcript_scan_state_needs_reset(path: Path, current_size: int, state: dict[str, Any]) -> bool:
    offset = int(state.get("offset") or 0)
    if current_size < offset:
        return True
    prefix_digest = str(state.get("prefix_digest") or "")
    if prefix_digest and transcript_scan_prefix_digest(path) != prefix_digest:
        return True
    parsed_tail = state.get("parsed_tail")
    if offset <= 0 or not isinstance(parsed_tail, bytes) or not parsed_tail:
        return False
    start = max(0, offset - len(parsed_tail))
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            current_tail = handle.read(offset - start)
    except OSError:
        return True
    return current_tail != parsed_tail[-len(current_tail):]


def scan_transcript_append(path: Path, offset: int, update: Callable[[str], None], state: dict[str, Any]) -> int:
    consumed = 0
    with path.open("rb") as handle:
        handle.seek(offset)
        while raw_line := handle.readline():
            if not (raw_line.endswith(b"\n") or raw_line.endswith(b"\r")):
                break
            update(raw_line.decode("utf-8", errors="replace"))
            update_transcript_scan_tail(state, raw_line)
            consumed += len(raw_line)
    return consumed


def update_transcript_scan_tail(state: dict[str, Any], parsed_chunk: bytes) -> None:
    if not parsed_chunk:
        return
    if len(parsed_chunk) >= _TRANSCRIPT_SCAN_TAIL_BYTES:
        state["parsed_tail"] = parsed_chunk[-_TRANSCRIPT_SCAN_TAIL_BYTES:]
        return
    previous = state.get("parsed_tail")
    previous_tail = previous if isinstance(previous, bytes) else b""
    state["parsed_tail"] = (previous_tail + parsed_chunk)[-_TRANSCRIPT_SCAN_TAIL_BYTES:]


def update_claude_transcript_scan_state(state: dict[str, Any], line: str) -> None:
    is_assistant = '"type":"assistant"' in line or '"type": "assistant"' in line
    if not is_assistant or ('"usage"' not in line and not any(tool in line for tool in CLAUDE_EDIT_TOOLS)):
        return
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return
    if record.get("type") != "assistant":
        return
    message = record.get("message")
    if not isinstance(message, dict):
        return
    model = transcript_model_key(message.get("model"))
    if model:
        state["model"] = model
    generated_tokens = claude_usage_generated_tokens(message.get("usage"))
    message_id = str(message.get("id") or "").strip()
    if message_id:
        tokens_by_message_id = state.get("usage_tokens_by_message_id")
        if not isinstance(tokens_by_message_id, dict):
            tokens_by_message_id = {}
            state["usage_tokens_by_message_id"] = tokens_by_message_id
        previous_tokens = positive_finite_number(tokens_by_message_id.get(message_id))
        if generated_tokens > previous_tokens:
            token_delta = generated_tokens - previous_tokens
            state["generated_tokens"] = float(state.get("generated_tokens") or 0.0) + token_delta
            transcript_add_model_tokens(state, model, token_delta)
            tokens_by_message_id[message_id] = generated_tokens
            while len(tokens_by_message_id) > _TRANSCRIPT_SCAN_MESSAGE_ID_MAX:
                tokens_by_message_id.pop(next(iter(tokens_by_message_id)))
    elif generated_tokens:
        state["generated_tokens"] = float(state.get("generated_tokens") or 0.0) + generated_tokens
        transcript_add_model_tokens(state, model, generated_tokens)
    raw_changes = state.get("raw_changes")
    if not isinstance(raw_changes, dict):
        raw_changes = {}
        state["raw_changes"] = raw_changes
    for item in message.get("content", []) or []:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        tool = item.get("name")
        if tool not in CLAUDE_EDIT_TOOLS:
            continue
        payload = item.get("input")
        file_path = payload.get("file_path") if isinstance(payload, dict) else None
        raw_path = str(file_path or "").strip()
        if raw_path:
            raw_changes.setdefault(raw_path, set()).add("A" if tool == "Write" else "M")


def claude_transcript_scan_state_details(state: dict[str, Any], cwd: str | None) -> dict[str, Any]:
    changes: dict[str, set[str]] = {}
    raw_changes = state.get("raw_changes") if isinstance(state, dict) else {}
    if isinstance(raw_changes, dict):
        for raw_path, markers in raw_changes.items():
            resolved = resolved_change_path(str(raw_path), cwd)
            if resolved is not None and isinstance(markers, set):
                changes.setdefault(str(resolved), set()).update(markers)
    return transcript_scan_details(
        changes,
        positive_finite_number(state.get("generated_tokens") if isinstance(state, dict) else 0.0),
        transcript_usage_models(state.get("generated_tokens_by_model") if isinstance(state, dict) else {}),
    )


def update_codex_transcript_scan_state(state: dict[str, Any], line: str, _cwd: str | None = None, _include_patch_text: bool = True, _include_usage: bool = True) -> None:
    """Accumulate provider-neutral raw Codex state; caller options are derived at read time."""
    patch_changes = state.get("patch_changes")
    if not isinstance(patch_changes, dict):
        patch_changes = {}
        state["patch_changes"] = patch_changes
    for verb, raw_path in CODEX_PATCH_RE.findall(line):
        resolved = resolved_change_path(raw_path, _CODEX_RELATIVE_CWD_SENTINEL)
        if resolved is not None:
            patch_changes.setdefault(str(resolved), set()).add(CODEX_PATCH_STATUS[verb])
    has_model_context = '"turn_context"' in line and '"model"' in line
    if has_model_context:
        try:
            context_record = json.loads(line)
        except json.JSONDecodeError:
            context_record = None
        payload = context_record.get("payload") if isinstance(context_record, dict) else None
        model = transcript_model_key(payload.get("model") if isinstance(payload, dict) else "")
        if model:
            state["model"] = model
    has_usage = '"total_token_usage"' in line or '"last_token_usage"' in line
    has_shell_call = (
        ('"function_call"' in line or '"custom_tool_call"' in line)
        and any(f'"{tool}"' in line for tool in CODEX_SHELL_TOOL_NAMES)
    )
    has_git_change = has_shell_call and codex_line_may_contain_git_change(line)
    if not has_usage and not has_git_change:
        return
    record: dict[str, Any] | None = None
    if has_git_change:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return
        record = parsed if isinstance(parsed, dict) else None
    if has_usage and not has_git_change:
        total_generated, last_generated = codex_line_usage_generated_tokens(line)
    else:
        total_generated, last_generated = codex_record_usage_generated_tokens(record)
    if total_generated is not None:
        previous_total = state.get("last_token_total")
        previous_number = positive_finite_number(previous_total) if previous_total is not None else 0.0
        token_delta = total_generated if previous_total is None or total_generated < previous_number else total_generated - previous_number
        state["last_token_total"] = total_generated
        transcript_add_model_tokens(state, transcript_model_key(state.get("model")), token_delta)
    elif last_generated is not None:
        state["summed_last_generated_tokens"] = float(state.get("summed_last_generated_tokens") or 0.0) + last_generated
        transcript_add_model_tokens(state, transcript_model_key(state.get("model")), last_generated)
    if not has_git_change:
        return
    shell_changes = state.get("shell_changes")
    if not isinstance(shell_changes, dict):
        shell_changes = {}
        state["shell_changes"] = shell_changes
    for path_text, markers in scan_codex_tool_call_changes_from_record(record, _CODEX_RELATIVE_CWD_SENTINEL).items():
        shell_changes.setdefault(path_text, set()).update(markers)


def codex_line_usage_generated_tokens(line: str) -> tuple[float | None, float | None]:
    values = [positive_finite_number(value) for value in CODEX_USAGE_VALUE_RE.findall(line)]
    generated = max(values, default=0.0)
    if generated <= 0:
        return None, None
    return (generated, None) if '"total_token_usage"' in line else (None, generated)


def resolve_codex_raw_change_path(raw_path: str, cwd: str | None) -> Path | None:
    path = Path(raw_path)
    sentinel = Path(_CODEX_RELATIVE_CWD_SENTINEL)
    try:
        relative = path.relative_to(sentinel)
    except ValueError:
        return path
    return resolved_change_path(str(relative), cwd)


def resolved_codex_raw_changes(raw_changes: dict[str, set[str]], cwd: str | None) -> dict[str, set[str]]:
    changes: dict[str, set[str]] = {}
    for raw_path, markers in raw_changes.items():
        resolved = resolve_codex_raw_change_path(str(raw_path), cwd)
        if resolved is not None:
            changes.setdefault(str(resolved), set()).update(markers)
    return changes


def codex_transcript_scan_state_details(state: dict[str, Any], cwd: str | None = None, include_patch_text: bool = True, include_usage: bool = True) -> dict[str, Any]:
    raw_changes: dict[str, set[str]] = {}
    if isinstance(state, dict):
        for key in ("shell_changes", "patch_changes") if include_patch_text else ("shell_changes",):
            source = state.get(key)
            if isinstance(source, dict):
                for path_text, markers in source.items():
                    if isinstance(markers, set):
                        raw_changes.setdefault(str(path_text), set()).update(markers)
    changes = resolved_codex_raw_changes(raw_changes, cwd)
    last_token_total = state.get("last_token_total") if isinstance(state, dict) else None
    summed_last_generated_tokens = float(state.get("summed_last_generated_tokens") or 0.0) if isinstance(state, dict) else 0.0
    generated_tokens = (positive_finite_number(last_token_total) if last_token_total is not None else summed_last_generated_tokens) if include_usage else 0.0
    return transcript_scan_details(
        changes if isinstance(changes, dict) else {},
        generated_tokens,
        transcript_usage_models(state.get("generated_tokens_by_model") if isinstance(state, dict) else {}),
    )


def copy_change_set(changes: dict[str, set[str]]) -> dict[str, set[str]]:
    return {path_text: set(markers) for path_text, markers in changes.items()}


def transcript_scan_details(
    changes: dict[str, set[str]],
    generated_tokens: float = 0.0,
    generated_tokens_by_model: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "changes": copy_change_set(changes),
        "usage": {
            "generated_tokens": max(0.0, float(generated_tokens or 0.0)),
            "generated_tokens_by_model": transcript_usage_models(generated_tokens_by_model),
        },
    }


def transcript_model_name(value: Any) -> str:
    """Keep only a bounded, machine-provided model identifier; never infer one from prose."""

    return str(value or "").strip()[:256]


def transcript_model_key(value: Any) -> str:
    return transcript_model_name(value) or "unknown"


def transcript_usage_models(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        transcript_model_key(model): positive_finite_number(tokens)
        for model, tokens in value.items()
        if positive_finite_number(tokens) > 0
    }


def transcript_add_model_tokens(state: dict[str, Any], model: str, tokens: float) -> None:
    if tokens <= 0:
        return
    models = state.get("generated_tokens_by_model")
    if not isinstance(models, dict):
        models = {}
        state["generated_tokens_by_model"] = models
    key = transcript_model_key(model)
    models[key] = positive_finite_number(models.get(key)) + tokens


def copy_transcript_scan_details(details: dict[str, Any]) -> dict[str, Any]:
    changes = details.get("changes") if isinstance(details, dict) else {}
    usage = details.get("usage") if isinstance(details, dict) else {}
    generated_tokens = usage.get("generated_tokens") if isinstance(usage, dict) else 0.0
    models = usage.get("generated_tokens_by_model") if isinstance(usage, dict) else {}
    return transcript_scan_details(changes if isinstance(changes, dict) else {}, positive_finite_number(generated_tokens), transcript_usage_models(models))


def generated_usage_tokens(usage: Any) -> float:
    if not isinstance(usage, dict):
        return 0.0
    # Providers expose these names as aliases or nested subsets, not additive counters. In
    # particular, Codex reasoning_output_tokens is already included in output_tokens.
    return max((positive_finite_number(usage.get(key)) for key in (
        "output_tokens",
        "outputTokens",
        "completion_tokens",
        "completionTokens",
        "generated_tokens",
        "generatedTokens",
        "reasoning_output_tokens",
        "reasoningOutputTokens",
    )), default=0.0)


def transcript_usage_identity(path: Path, kind: str) -> str:
    try:
        stat = path.stat()
        with path.open("rb") as handle:
            prefix_digest = hashlib.sha256(handle.readline(4096)).hexdigest()[:16]
    except OSError:
        return ""
    resolved = str(path.expanduser().resolve(strict=False))
    return f"{str(kind or '').strip().lower()}:{int(stat.st_dev)}:{int(stat.st_ino)}:{prefix_digest}:{resolved}"


def claude_usage_generated_tokens(usage: Any) -> float:
    direct = generated_usage_tokens(usage)
    if direct:
        return direct
    if not isinstance(usage, dict):
        return 0.0
    iterations = usage.get("iterations")
    if not isinstance(iterations, list):
        return 0.0
    return sum(generated_usage_tokens(item) for item in iterations if isinstance(item, dict))


def codex_record_usage_generated_tokens(record: Any) -> tuple[float | None, float | None]:
    if not isinstance(record, dict):
        return None, None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None, None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None, None
    total_usage = info.get("total_token_usage")
    total_generated = generated_usage_tokens(total_usage)
    if total_generated:
        return total_generated, None
    last_usage = info.get("last_token_usage")
    last_generated = generated_usage_tokens(last_usage)
    return None, last_generated if last_generated else None


def usage_number(usage: Any, *names: str) -> float | None:
    """Return a reported non-negative usage field without manufacturing zeroes.

    API payloads use both snake_case and camelCase.  A present zero is useful
    telemetry, whereas an absent field means the collector cannot claim that
    component was free, so the two must remain distinguishable.
    """

    if not isinstance(usage, dict):
        return None
    for name in names:
        if name not in usage:
            continue
        try:
            value = float(usage[name])
        except (TypeError, ValueError):
            continue
        if value >= 0 and value == value and value != float("inf"):
            return value
    return None


def transcript_effort(value: Any) -> str:
    """Keep an explicit provider effort label; never infer it from token use."""

    text = str(value or "").strip().lower()
    return text[:64] if text else "unknown"


def transcript_pricing_context(value: Any) -> str:
    """Retain an explicit bounded billing selector; unknown defaults safely."""

    text = str(value or "").strip().lower()
    return text[:64] if text else "default"


def usage_component_atoms(
    *,
    source: str,
    timestamp: float,
    event_id: str,
    provider: str,
    model: str,
    model_evidence: str,
    effort: str,
    components: dict[tuple[str, str, str, str], float | None],
    root_thread_id: str = "",
    agent_thread_id: str = "",
    parent_thread_id: str = "",
    depth: int = 0,
    endpoint: str = "",
    tool_name: str = "",
    call_id: str = "",
    pricing_profile: str = "default",
    service_tier: str = "default",
    telemetry_complete: bool = False,
) -> list[TranscriptUsageAtom]:
    """Materialize only quantities explicitly reported by a provider."""

    atoms: list[TranscriptUsageAtom] = []
    for (direction, modality, cache_role, unit), raw_quantity in components.items():
        if raw_quantity is None:
            continue
        quantity = positive_finite_number(raw_quantity)
        # Keep reported zeroes out of the event log: their absence is equivalent
        # for aggregation, while missing values are represented by no component
        # and ``telemetry_complete`` remains false.
        if quantity <= 0:
            continue
        atoms.append(TranscriptUsageAtom(
            source=source,
            timestamp=timestamp,
            event_id=event_id,
            provider=provider,
            model=transcript_model_name(model),
            model_evidence=model_evidence,
            effort=transcript_effort(effort),
            direction=direction,
            modality=modality,
            cache_role=cache_role,
            unit=unit,
            quantity=quantity,
            root_thread_id=root_thread_id,
            agent_thread_id=agent_thread_id,
            parent_thread_id=parent_thread_id,
            depth=max(0, int(depth)),
            endpoint=endpoint,
            tool_name=tool_name,
            call_id=call_id,
            pricing_profile=transcript_pricing_context(pricing_profile),
            service_tier=transcript_pricing_context(service_tier),
            telemetry_complete=telemetry_complete,
        ))
    return atoms


def codex_usage_components(usage: Any) -> dict[tuple[str, str, str, str], float | None]:
    """Normalize Codex/OpenAI token counters with cached input as a subset."""

    input_tokens = usage_number(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
    cached_tokens = usage_number(usage, "cached_input_tokens", "cachedInputTokens")
    details = usage.get("input_tokens_details") if isinstance(usage, dict) else None
    if cached_tokens is None and isinstance(details, dict):
        cached_tokens = usage_number(details, "cached_tokens", "cachedTokens")
    # OpenAI reports cached input inside input_tokens.  Do not add both.
    uncached_input = max(0.0, input_tokens - min(input_tokens, cached_tokens or 0.0)) if input_tokens is not None else None
    return {
        ("input", "text", "none", "tokens"): uncached_input,
        ("input", "text", "read", "tokens"): cached_tokens,
        ("output", "text", "none", "tokens"): usage_number(usage, "output_tokens", "outputTokens", "completion_tokens", "completionTokens", "generated_tokens", "generatedTokens"),
    }


def claude_usage_components(usage: Any) -> dict[tuple[str, str, str, str], float | None]:
    """Normalize Anthropic's separate ordinary/read/write token counters."""

    creation = usage.get("cache_creation") if isinstance(usage, dict) else None
    nested_5m = usage_number(creation, "ephemeral_5m_input_tokens", "ephemeral5mInputTokens")
    nested_1h = usage_number(creation, "ephemeral_1h_input_tokens", "ephemeral1hInputTokens")
    return {
        ("input", "text", "none", "tokens"): usage_number(usage, "input_tokens", "inputTokens"),
        ("input", "text", "read", "tokens"): usage_number(usage, "cache_read_input_tokens", "cacheReadInputTokens"),
        # When Claude provides the nested duration split, it is the exact
        # component view.  The top-level creation total is an aggregate and
        # must not be added alongside it.
        ("input", "text", "write_5m", "tokens"): nested_5m if nested_5m is not None else usage_number(usage, "cache_creation_input_tokens", "cacheCreationInputTokens"),
        ("input", "text", "write_1h", "tokens"): nested_1h if nested_1h is not None else usage_number(usage, "cache_creation_input_tokens_1h", "cacheCreationInputTokens1h"),
        ("output", "text", "none", "tokens"): usage_number(usage, "output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
    }


def usage_component_delta(current: dict[tuple[str, str, str, str], float | None], previous: dict[tuple[str, str, str, str], float]) -> dict[tuple[str, str, str, str], float | None]:
    """Delta a cumulative component snapshot atomically, including resets."""

    reported = {key: value for key, value in current.items() if value is not None}
    if not reported:
        return {}
    # A provider rollover makes all counters a new snapshot.  Applying this
    # decision once for the whole usage object avoids mixing pre/post-reset
    # input and output components.
    reset = any(float(value) < previous.get(key, 0.0) for key, value in reported.items())
    return {
        key: float(value) if reset else max(0.0, float(value) - previous.get(key, 0.0))
        for key, value in reported.items()
    }


def usage_telemetry_complete(components: dict[tuple[str, str, str, str], float | None]) -> bool:
    """An exact token price needs both ordinary input and output telemetry."""

    return (
        components.get(("input", "text", "none", "tokens")) is not None
        and components.get(("output", "text", "none", "tokens")) is not None
    )


def reverse_transcript_lines(path: Path) -> Iterator[bytes]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            position = size
            suffix = b""
            while position > 0:
                chunk_size = min(position, _TRANSCRIPT_REVERSE_SCAN_BYTES)
                position -= chunk_size
                handle.seek(position)
                parts = (handle.read(chunk_size) + suffix).split(b"\n")
                suffix = parts[0]
                for raw_line in reversed(parts[1:]):
                    if raw_line:
                        yield raw_line
            if suffix:
                yield suffix
    except OSError:
        return


def latest_codex_total_generated_tokens(path: Path) -> float | None:
    for raw_line in reverse_transcript_lines(path):
        if b'"total_token_usage"' not in raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        total_generated, _last_generated = codex_record_usage_generated_tokens(record)
        if total_generated is not None:
            return total_generated
    return None


def transcript_generated_tokens(path: Path, kind: str, cwd: str | None = None) -> float | None:
    agent_kind = str(kind or "").strip().lower()
    if agent_kind == "claude":
        generated_tokens = sum(
            positive_finite_number(
                scan_claude_transcript_details(transcript_path, cwd).get("usage", {}).get("generated_tokens")
            )
            for transcript_path in claude_transcript_family_paths(path)
        )
    elif agent_kind == "codex":
        generated_tokens = 0.0
        for transcript_path in codex_transcript_family_paths(path):
            total = latest_codex_total_generated_tokens(transcript_path)
            if total is None:
                details = scan_codex_transcript_details(transcript_path, cwd)
                usage = details.get("usage") if isinstance(details, dict) else {}
                total = positive_finite_number(usage.get("generated_tokens") if isinstance(usage, dict) else 0.0)
            generated_tokens += total or 0.0
    else:
        return None
    return generated_tokens if generated_tokens > 0 else None


def transcript_record_timestamp(record: dict[str, Any]) -> float | None:
    value = record.get("timestamp")
    if isinstance(value, (int, float)):
        timestamp = float(value)
        return timestamp if timestamp > 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def transcript_json_records(path: Path, *, max_bytes: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield complete JSONL records, optionally bounded by a captured offset.

    Backfill captures byte high-water marks before it starts parsing.  Reading
    only that prefix means a concurrent append belongs to the normal live
    collector, not to both the historical and live paths.
    """
    try:
        with path.open("rb") as handle:
            payload = handle.read(max(0, int(max_bytes))) if max_bytes is not None else handle.read()
            for raw_line in payload.splitlines():
                try:
                    record = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def claude_transcript_generated_token_events(path: Path) -> list[TranscriptUsageEvent]:
    tokens_by_message_id: dict[str, float] = {}
    source = str(path.expanduser().resolve(strict=False))
    events: list[TranscriptUsageEvent] = []
    for record in transcript_json_records(path):
        if record.get("type") != "assistant":
            continue
        timestamp = transcript_record_timestamp(record)
        message = record.get("message")
        if timestamp is None or not isinstance(message, dict):
            continue
        tokens = claude_usage_generated_tokens(message.get("usage"))
        if tokens <= 0:
            continue
        message_id = str(message.get("id") or "").strip()
        if message_id:
            previous = tokens_by_message_id.get(message_id, 0.0)
            tokens_by_message_id[message_id] = max(previous, tokens)
            tokens -= previous
        if tokens > 0:
            events.append(TranscriptUsageEvent(source=source, timestamp=timestamp, tokens=tokens, model=transcript_model_name(message.get("model"))))
    return events


def codex_transcript_generated_token_events(path: Path) -> list[TranscriptUsageEvent]:
    source = str(path.expanduser().resolve(strict=False))
    usage_records: list[tuple[float, float | None, float | None, str]] = []
    current_model = ""
    for record in transcript_json_records(path):
        if record.get("type") == "turn_context":
            payload = record.get("payload")
            if isinstance(payload, dict):
                current_model = transcript_model_name(payload.get("model")) or current_model
        timestamp = transcript_record_timestamp(record)
        if timestamp is None:
            continue
        total_generated, last_generated = codex_record_usage_generated_tokens(record)
        if total_generated is not None or last_generated is not None:
            usage_records.append((timestamp, total_generated, last_generated, current_model))
    if any(total is not None for _timestamp, total, _last, _model in usage_records):
        previous_total: float | None = None
        events: list[TranscriptUsageEvent] = []
        for timestamp, total, _last, model in usage_records:
            if total is None:
                continue
            tokens = total if previous_total is None else total - previous_total
            previous_total = total
            if tokens > 0:
                events.append(TranscriptUsageEvent(source=source, timestamp=timestamp, tokens=tokens, model=model))
        return events
    return [
        TranscriptUsageEvent(source=source, timestamp=timestamp, tokens=last, model=model)
        for timestamp, _total, last, model in usage_records
        if last is not None and last > 0
    ]


def transcript_generated_token_events(path: Path, kind: str) -> list[TranscriptUsageEvent]:
    agent_kind = str(kind or "").strip().lower()
    if agent_kind == "claude":
        events = [
            event
            for transcript_path in claude_transcript_family_paths(path)
            for event in claude_transcript_generated_token_events(transcript_path)
        ]
    elif agent_kind == "codex":
        events = [
            event
            for transcript_path in codex_transcript_family_paths(path)
            for event in codex_transcript_generated_token_events(transcript_path)
        ]
    else:
        return []
    return sorted(events, key=lambda event: (event.source, event.timestamp))


def codex_transcript_identity_context(path: Path) -> tuple[str, str]:
    """Return the rollout's thread and explicit parent link, if present."""

    thread_id = ""
    parent_thread_id = ""
    for record in transcript_json_records(path):
        if record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        thread_id = str(payload.get("id") or payload.get("thread_id") or "").strip()[:256] or thread_id
        source = payload.get("source")
        subagent = source.get("subagent") if isinstance(source, dict) else None
        spawned = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        parent_thread_id = str(spawned.get("parent_thread_id") or "").strip()[:256] if isinstance(spawned, dict) else parent_thread_id
        if thread_id or parent_thread_id:
            break
    return thread_id, parent_thread_id


def transcript_family_context(paths: list[Path], kind: str) -> dict[str, tuple[str, str, str, int]]:
    """Build bounded root/parent/depth context without attributing child use to a parent model."""

    if str(kind or "").strip().lower() != "codex":
        root = str(paths[0].expanduser().resolve(strict=False)) if paths else ""
        return {
            str(path.expanduser().resolve(strict=False)): (root, str(path.expanduser().resolve(strict=False)), "", 0 if index == 0 else 1)
            for index, path in enumerate(paths)
        }
    raw = {
        str(path.expanduser().resolve(strict=False)): codex_transcript_identity_context(path)
        for path in paths
    }
    thread_to_source = {thread: source for source, (thread, _parent) in raw.items() if thread}
    roots: dict[str, str] = {}
    depths: dict[str, int] = {}
    for source, (thread, parent) in raw.items():
        cursor = thread
        visited: set[str] = set()
        depth = 0
        while cursor and cursor not in visited:
            visited.add(cursor)
            candidate_source = thread_to_source.get(cursor)
            candidate_parent = raw.get(candidate_source, ("", ""))[1] if candidate_source else ""
            if not candidate_parent or candidate_parent not in thread_to_source:
                break
            cursor = candidate_parent
            depth += 1
        roots[source] = cursor or thread or source
        depths[source] = depth
    return {
        source: (roots.get(source, thread or source), thread or source, parent, depths.get(source, 0))
        for source, (thread, parent) in raw.items()
    }


def claude_record_usage(usage: Any) -> dict[tuple[str, str, str, str], float | None]:
    """Use top-level Claude usage, or iteration usage only when top-level is absent."""

    direct = claude_usage_components(usage)
    if any(value is not None for value in direct.values()):
        return direct
    iterations = usage.get("iterations") if isinstance(usage, dict) else None
    if not isinstance(iterations, list):
        return direct
    totals: dict[tuple[str, str, str, str], float] = {}
    for iteration in iterations:
        for key, value in claude_usage_components(iteration).items():
            if value is not None:
                totals[key] = totals.get(key, 0.0) + value
    return {key: value for key, value in totals.items()}


def claude_transcript_usage_atoms(path: Path, *, root_thread_id: str = "", agent_thread_id: str = "", parent_thread_id: str = "", depth: int = 0, max_bytes: int | None = None) -> list[TranscriptUsageAtom]:
    source = str(path.expanduser().resolve(strict=False))
    previous_by_message: dict[str, dict[tuple[str, str, str, str], float]] = {}
    atoms: list[TranscriptUsageAtom] = []
    for sequence, record in enumerate(transcript_json_records(path, max_bytes=max_bytes)):
        if record.get("type") != "assistant":
            continue
        timestamp = transcript_record_timestamp(record)
        message = record.get("message")
        if timestamp is None or not isinstance(message, dict):
            continue
        components = claude_record_usage(message.get("usage"))
        if not components:
            continue
        message_id = str(message.get("id") or f"line-{sequence}").strip()[:256]
        previous = previous_by_message.get(message_id, {})
        delta = usage_component_delta(components, previous)
        previous_by_message[message_id] = {key: float(value) for key, value in components.items() if value is not None}
        model = transcript_model_name(message.get("model"))
        effort = message.get("effort") or record.get("effort")
        atoms.extend(usage_component_atoms(
            source=source,
            timestamp=timestamp,
            event_id=f"claude:{agent_thread_id or source}:{message_id}",
            provider="anthropic",
            model=model,
            model_evidence="assistant.message.model" if model else "unknown",
            effort=effort,
            components=delta,
            root_thread_id=root_thread_id or source,
            agent_thread_id=agent_thread_id or source,
            parent_thread_id=parent_thread_id,
            depth=depth,
            endpoint="messages",
            pricing_profile=message.get("pricing_profile") or message.get("profile") or record.get("pricing_profile") or record.get("profile"),
            service_tier=message.get("service_tier") or message.get("serviceTier") or record.get("service_tier") or record.get("serviceTier"),
            telemetry_complete=usage_telemetry_complete(components),
        ))
    return atoms


def codex_transcript_usage_atoms(path: Path, *, root_thread_id: str = "", agent_thread_id: str = "", parent_thread_id: str = "", depth: int = 0, max_bytes: int | None = None) -> list[TranscriptUsageAtom]:
    source = str(path.expanduser().resolve(strict=False))
    totals: dict[tuple[str, str, str, str], float] = {}
    current_model = ""
    current_effort = "unknown"
    current_pricing_profile = "default"
    current_service_tier = "default"
    atoms: list[TranscriptUsageAtom] = []
    for sequence, record in enumerate(transcript_json_records(path, max_bytes=max_bytes)):
        if record.get("type") == "turn_context":
            payload = record.get("payload")
            if isinstance(payload, dict):
                current_model = transcript_model_name(payload.get("model")) or current_model
                current_effort = transcript_effort(payload.get("effort"))
                current_pricing_profile = transcript_pricing_context(payload.get("pricing_profile") or payload.get("profile"))
                current_service_tier = transcript_pricing_context(payload.get("service_tier") or payload.get("serviceTier"))
            continue
        timestamp = transcript_record_timestamp(record)
        payload = record.get("payload")
        info = payload.get("info") if isinstance(payload, dict) else None
        if timestamp is None or not isinstance(info, dict):
            continue
        cumulative = info.get("total_token_usage")
        last = info.get("last_token_usage")
        usage = cumulative if isinstance(cumulative, dict) else last if isinstance(last, dict) else None
        if usage is None:
            continue
        components = codex_usage_components(usage)
        is_cumulative = isinstance(cumulative, dict)
        delta = usage_component_delta(components, totals) if is_cumulative else components
        if is_cumulative:
            totals = {key: float(value) for key, value in components.items() if value is not None}
        atoms.extend(usage_component_atoms(
            source=source,
            timestamp=timestamp,
            event_id=f"codex:{agent_thread_id or source}:{sequence}",
            provider="openai",
            model=current_model,
            model_evidence="turn_context.payload.model" if current_model else "unknown",
            effort=current_effort,
            components=delta,
            root_thread_id=root_thread_id or agent_thread_id or source,
            agent_thread_id=agent_thread_id or source,
            parent_thread_id=parent_thread_id,
            depth=depth,
            endpoint="responses",
            pricing_profile=current_pricing_profile,
            service_tier=current_service_tier,
            telemetry_complete=usage_telemetry_complete(components),
        ))
    return atoms


def transcript_usage_atoms(path: Path, kind: str, *, family_paths: list[Path] | None = None, max_bytes_by_path: dict[str, int] | None = None) -> list[TranscriptUsageAtom]:
    """Return normalized atoms for a root transcript and its discovered family."""

    agent_kind = str(kind or "").strip().lower()
    paths = family_paths if family_paths is not None else (claude_transcript_family_paths(path) if agent_kind == "claude" else codex_transcript_family_paths(path) if agent_kind == "codex" else [])
    context = transcript_family_context(paths, agent_kind)
    atoms: list[TranscriptUsageAtom] = []
    for transcript_path in paths:
        source = str(transcript_path.expanduser().resolve(strict=False))
        root, agent, parent, depth = context.get(source, (source, source, "", 0))
        max_bytes = (max_bytes_by_path or {}).get(source)
        if agent_kind == "claude":
            atoms.extend(claude_transcript_usage_atoms(transcript_path, root_thread_id=root, agent_thread_id=agent, parent_thread_id=parent, depth=depth, max_bytes=max_bytes))
        elif agent_kind == "codex":
            atoms.extend(codex_transcript_usage_atoms(transcript_path, root_thread_id=root, agent_thread_id=agent, parent_thread_id=parent, depth=depth, max_bytes=max_bytes))
    return sorted(atoms, key=lambda atom: (atom.timestamp, atom.source, atom.event_id, atom.direction, atom.cache_role))


def direct_image_usage_atoms(*, request: dict[str, Any], response: dict[str, Any], timestamp: float, source: str, request_id: str = "", root_thread_id: str = "", agent_thread_id: str = "", parent_thread_id: str = "", depth: int = 0) -> list[TranscriptUsageAtom]:
    """Normalize a correlated direct Images API completion without guessing its model.

    This accepts only the structured request/response pair.  Responses API
    image-tool children intentionally do not flow through here because their
    documented result lacks the child model/usage identity needed to estimate a
    cost honestly.
    """

    model = transcript_model_name(request.get("model"))
    usage = response.get("usage") if isinstance(response, dict) else None
    details = usage.get("input_tokens_details") if isinstance(usage, dict) else None
    if not model or not isinstance(usage, dict) or not isinstance(details, dict):
        return []
    return usage_component_atoms(
        source=str(source),
        timestamp=timestamp,
        event_id=f"image:{request_id or response.get('id') or hashlib.sha256(json.dumps(response, sort_keys=True).encode('utf-8')).hexdigest()[:16]}",
        provider="openai",
        model=model,
        model_evidence="images.request.model",
        effort="unknown",
        components={
            ("input", "text", "none", "tokens"): usage_number(details, "text_tokens"),
            ("input", "image", "none", "tokens"): usage_number(details, "image_tokens"),
            ("output", "image", "none", "tokens"): usage_number(usage, "output_tokens"),
        },
        endpoint="images",
        call_id=str(request_id or response.get("id") or "")[:256],
        root_thread_id=root_thread_id or source,
        agent_thread_id=agent_thread_id or source,
        parent_thread_id=parent_thread_id,
        depth=depth,
        telemetry_complete=True,
    )


def opaque_responses_image_tool_atoms(*, timestamp: float, source: str, call_id: str = "", root_thread_id: str = "", agent_thread_id: str = "") -> list[TranscriptUsageAtom]:
    """Represent a Responses image tool call without inventing its model/cost.

    The tool call itself proves one image-generation request happened, but the
    public tool result can omit its internal image model and usage envelope.
    A bounded unpriced request atom keeps that child visible in accounting
    without charging the parent response model twice.
    """

    # A visible tool name alone is not a correlation key.  In particular,
    # rendered/prose lookalikes and incomplete stream fragments must never
    # create a synthetic child that could be confused with a real invocation.
    identity = str(call_id or "").strip()[:256]
    if not identity:
        return []
    return usage_component_atoms(
        source=str(source),
        timestamp=timestamp,
        event_id=f"responses-image-tool:{identity}",
        provider="openai",
        model="unknown",
        model_evidence="responses.image_generation_call.model_not_exposed",
        effort="unknown",
        components={("output", "image", "none", "requests"): 1},
        root_thread_id=root_thread_id or source,
        agent_thread_id=agent_thread_id or source,
        endpoint="responses",
        tool_name="image_generation_call",
        call_id=identity,
        telemetry_complete=False,
    )


def transcript_generated_tokens_by_model(path: Path, kind: str, cwd: str | None = None) -> dict[str, float]:
    """Return generated transcript tokens by exact provider-supplied model identifier."""

    agent_kind = str(kind or "").strip().lower()
    if agent_kind == "claude":
        paths = claude_transcript_family_paths(path)
    elif agent_kind == "codex":
        paths = codex_transcript_family_paths(path)
    else:
        return {}
    totals: dict[str, float] = {}
    for transcript_path in paths:
        details = (
            scan_claude_transcript_details(transcript_path, cwd)
            if agent_kind == "claude"
            else scan_codex_transcript_details(transcript_path, cwd, include_patch_text=False, include_usage=True)
        )
        usage = details.get("usage") if isinstance(details, dict) else {}
        models = usage.get("generated_tokens_by_model") if isinstance(usage, dict) else {}
        for model, tokens in transcript_usage_models(models).items():
            totals[model] = totals.get(model, 0.0) + tokens
    return totals


def codex_line_may_contain_git_change(line: str) -> bool:
    if "git" not in line:
        return False
    return any(token in line for token in (" add ", " rm ", " mv ", " add\\", " rm\\", " mv\\"))


def codex_tool_call_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    arguments = payload.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str) or not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def scan_codex_tool_call_changes(line: str, cwd: str | None = None) -> dict[str, set[str]]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return scan_codex_tool_call_changes_from_record(record, cwd)


def scan_codex_tool_call_changes_from_record(record: Any, cwd: str | None = None) -> dict[str, set[str]]:
    if not isinstance(record, dict):
        return {}
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("type") or "") not in {"function_call", "custom_tool_call"}:
        return {}
    if str(payload.get("name") or "") not in CODEX_SHELL_TOOL_NAMES:
        return {}
    arguments = codex_tool_call_arguments(payload)
    command = arguments.get("cmd") or arguments.get("command")
    if not isinstance(command, str):
        return {}
    workdir = arguments.get("workdir")
    effective_cwd = workdir if isinstance(workdir, str) and workdir else cwd
    return scan_shell_command_changes(command, effective_cwd)


def shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return []


def scan_shell_command_changes(command: str, cwd: str | None = None) -> dict[str, set[str]]:
    tokens = shell_tokens(command)
    if not tokens:
        return {}
    changes: dict[str, set[str]] = {}
    effective_cwd = cwd
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token in SHELL_COMMAND_BREAK_TOKENS:
            segment_changes, effective_cwd = scan_shell_command_segment_changes(segment, effective_cwd)
            for path_text, markers in segment_changes.items():
                changes.setdefault(path_text, set()).update(markers)
            segment = []
            continue
        segment.append(token)
    return changes


def scan_shell_command_segment_changes(tokens: list[str], cwd: str | None = None) -> tuple[dict[str, set[str]], str | None]:
    if not tokens:
        return {}, cwd
    if tokens[0] == "cd" and len(tokens) >= 2:
        resolved = resolved_change_path(tokens[1], cwd)
        return {}, str(resolved) if resolved is not None else cwd
    inline_command = shell_runner_inline_command(tokens)
    if inline_command is not None:
        return scan_shell_command_changes(inline_command, cwd), cwd
    return scan_git_command_changes(tokens, cwd), cwd


def shell_runner_inline_command(tokens: list[str]) -> str | None:
    if not tokens or Path(tokens[0]).name not in SHELL_RUNNERS:
        return None
    for index, token in enumerate(tokens[1:], start=1):
        if token == "-c" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("-") and "c" in token and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def scan_git_command_changes(tokens: list[str], cwd: str | None = None) -> dict[str, set[str]]:
    if not tokens or tokens[0] != "git":
        return {}
    index = 1
    effective_cwd = cwd
    while index < len(tokens):
        token = tokens[index]
        if token == "-C" and index + 1 < len(tokens):
            resolved = resolved_change_path(tokens[index + 1], effective_cwd)
            effective_cwd = str(resolved) if resolved is not None else tokens[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(tokens):
        return {}
    subcommand = tokens[index]
    if subcommand == "add":
        return scan_git_path_args(tokens[index + 1 :], effective_cwd, "M")
    if subcommand == "rm":
        return scan_git_path_args(tokens[index + 1 :], effective_cwd, "D")
    if subcommand == "mv":
        path_args = git_path_args(tokens[index + 1 :])
        changes: dict[str, set[str]] = {}
        if len(path_args) >= 1:
            resolved = resolved_change_path(path_args[0], effective_cwd)
            if resolved is not None:
                changes.setdefault(str(resolved), set()).add("D")
        if len(path_args) >= 2:
            resolved = resolved_change_path(path_args[-1], effective_cwd)
            if resolved is not None:
                changes.setdefault(str(resolved), set()).add("A")
        return changes
    return {}


def scan_git_path_args(tokens: list[str], cwd: str | None, marker: str) -> dict[str, set[str]]:
    changes: dict[str, set[str]] = {}
    for raw_path in git_path_args(tokens):
        resolved = resolved_change_path(raw_path, cwd)
        if resolved is None:
            continue
        changes.setdefault(str(resolved), set()).add(marker)
    return changes


def git_path_args(tokens: list[str]) -> list[str]:
    paths: list[str] = []
    index = 0
    positional = False
    options_with_value = {"--pathspec-from-file", "--chmod"}
    while index < len(tokens):
        token = tokens[index]
        if positional:
            paths.append(token)
            index += 1
            continue
        if token == "--":
            positional = True
            index += 1
            continue
        if token in SHELL_COMMAND_BREAK_TOKENS:
            break
        if token in options_with_value:
            index += 2
            continue
        if token.startswith("--pathspec-from-file=") or token.startswith("--chmod="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        paths.append(token)
        index += 1
    return paths


def scan_agent_changes(agent: AgentInfo) -> dict[str, set[str]]:
    if not agent.transcript:
        return {}
    path = Path(agent.transcript).expanduser()
    if agent.kind == "claude":
        return scan_claude_transcript(path, agent.cwd)
    if agent.kind == "codex":
        return scan_codex_transcript(path, agent.cwd)
    return {}


def session_touched_dirs(info: SessionInfo) -> list[str]:
    """Directories the session's agents have actually EDITED files in, derived from each agent's
    transcript (the same edit-tool scan the Modified-files / Tabber panes use, so it counts edits, not
    reads). This is the signal that lets repo detection find the real project repo even when the live
    pane cwd is $HOME or another non-repo: a claude launched from ~ but editing files in
    ~/yolomux.dev7773 still surfaces that repo. Returns unique containing directories in first-seen
    order; git-root resolution and dedupe across repos happen in the caller's repo_summary pass."""
    dirs: list[str] = []
    seen: set[str] = set()
    for agent in info.agents:
        for path_text in scan_agent_changes(agent):
            parent = str(Path(path_text).parent)
            if parent and parent != "." and parent not in seen:
                seen.add(parent)
                dirs.append(parent)
    return dirs


def bounded_session_files_hours(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 24.0
    return max(0.25, min(parsed, float(SESSION_FILES_MAX_HOURS)))


def session_files_cutoff(hours: float, now: float | None = None) -> float:
    # Poll ticks near the lookback boundary should not make a repo appear on one refresh and vanish on the
    # next just because transcript mtime crossed the exact second cutoff.
    current = now if now is not None else time.time()
    return current - bounded_session_files_hours(hours) * 3600 - SESSION_FILES_CUTOFF_GRACE_SECONDS


def git_default_branch_ref(repo: Path) -> str | None:
    result = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], cwd=str(repo), timeout=5.0)
    if result.returncode == 0:
        ref = result.stdout.strip()
        if ref:
            return ref
    for ref in ("origin/main", "origin/master", "main", "master"):
        verify_ref = f"refs/remotes/{ref}" if ref.startswith("origin/") else f"refs/heads/{ref}"
        verify = git(["show-ref", "--verify", "--quiet", verify_ref], cwd=str(repo), timeout=5.0)
        if verify.returncode == 0:
            return ref
    return None


def git_diff_base(repo: Path) -> str:
    default_ref = git_default_branch_ref(repo)
    if default_ref:
        result = git(["merge-base", default_ref, "HEAD"], cwd=str(repo), timeout=5.0)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return "HEAD"


def validate_diff_refs(repo: Path, from_ref: str, to_ref: str) -> str:
    if to_ref == "current":
        if from_ref == "current":
            return "FROM ref must be older than TO ref (current is the working tree)"
        return "" if git_ref_exists(repo, from_ref) else f"unknown FROM ref: {from_ref}"
    if from_ref == "current":
        return "FROM ref must be older than TO ref (current is the working tree)"
    if not git_ref_exists(repo, from_ref):
        return f"unknown FROM ref: {from_ref}"
    if not git_ref_exists(repo, to_ref):
        return f"unknown TO ref: {to_ref}"
    order = git(["merge-base", "--is-ancestor", from_ref, to_ref], cwd=str(repo), timeout=5.0)
    if order.returncode != 0:
        return f"FROM ref must be older than TO ref ({from_ref} is not an ancestor of {to_ref})"
    return ""


def diff_ref_issue(message: str, from_ref: str, to_ref: str, repo: str = "") -> dict[str, Any]:
    """Classify one git-ref diagnostic into the shared localizable descriptor shape."""
    fallback = str(message or "")
    params = {"from": from_ref, "to": to_ref, "repo": repo, "error": fallback}
    if fallback.startswith("unknown FROM ref:"):
        return message_descriptor("common.unknownFromRef", fallback, {"ref": from_ref})
    if fallback.startswith("unknown TO ref:"):
        return message_descriptor("common.unknownToRef", fallback, {"ref": to_ref})
    if fallback.startswith("FROM ref must be older than TO ref"):
        return message_descriptor("diff.error.fromNotOlder", fallback, params)
    return message_descriptor("diff.error.git", fallback, params)


def git_diff_args(repo: Path, base: str | None = None, from_ref: str | None = None, to_ref: str | None = None) -> tuple[list[str], bool, str]:
    if refs_requested(from_ref, to_ref):
        older, newer = diff_refs(from_ref, to_ref)
        error = validate_diff_refs(repo, older, newer)
        if error:
            return [], False, error
        if newer == "current":
            return [older], True, ""
        return [older, newer], False, ""
    return [base or git_diff_base(repo)], True, ""


def git_decoration_aliases(decorations: str, *, include_head: bool = False) -> list[str]:
    aliases: list[str] = ["HEAD"] if include_head else []
    for raw in decorations.split(","):
        for part in raw.strip().split(" -> "):
            alias = part.strip()
            if alias.startswith("tag: "):
                alias = alias.removeprefix("tag: ").strip()
            if alias.startswith("refs/heads/"):
                alias = alias.removeprefix("refs/heads/")
            elif alias.startswith("refs/remotes/"):
                alias = alias.removeprefix("refs/remotes/")
            if alias == "HEAD" and not include_head:
                continue
            if not alias or alias == "origin/HEAD" or alias in aliases:
                continue
            aliases.append(alias)

    def sort_key(alias: str) -> tuple[int, str]:
        if alias == "HEAD":
            return (0, alias)
        if alias in {"origin/main", "origin/master"}:
            return (1, alias)
        if alias in {"main", "master"}:
            return (2, alias)
        if alias.startswith("origin/"):
            return (3, alias)
        return (4, alias)

    return sorted(aliases, key=sort_key)


def git_ref_label(short_sha: str, aliases: list[str]) -> str:
    if not aliases:
        return short_sha
    return f"{short_sha}/{aliases[0]}{' ' + ' '.join(aliases[1:]) if len(aliases) > 1 else ''}"


def git_recent_refs(repo: Path, limit: int = 100) -> list[dict[str, Any]]:
    result = git([
        "log",
        "--decorate=short",
        f"--max-count={max(1, min(limit, 200))}",
        "--pretty=format:%H%x1f%h%x1f%s%x1f%at%x1f%an%x1f%D",
    ], cwd=str(repo), timeout=5.0)
    refs: list[dict[str, Any]] = [{"ref": "HEAD", "short": "HEAD", "subject": "base commit"}, {"ref": "current", "short": "current", "subject": "working tree"}]
    if result.returncode != 0:
        return refs
    seen = {"current", "HEAD"}
    head_commit_seen = False
    for line in result.stdout.splitlines():
        parts = line.split("\x1f", 5)
        if len(parts) < 3 or not parts[0] or parts[0] in seen:
            continue
        aliases = git_decoration_aliases(parts[5] if len(parts) >= 6 else "")
        entry: dict[str, Any] = {"ref": parts[0], "short": git_ref_label(parts[1], aliases), "subject": parts[2]}
        if aliases:
            entry["aliases"] = aliases
        if len(parts) >= 5:
            entry["date"] = parts[3]
            entry["author"] = parts[4]
        if not head_commit_seen:
            head_aliases = git_decoration_aliases(parts[5] if len(parts) >= 6 else "", include_head=True)
            refs[0] = {
                **refs[0],
                "short": git_ref_label(parts[1], head_aliases),
                "subject": parts[2],
                "commit": parts[0],
                "aliases": head_aliases,
            }
            if len(parts) >= 5:
                refs[0]["date"] = parts[3]
                refs[0]["author"] = parts[4]
            head_commit_seen = True
        refs.append(entry)
        seen.add(parts[0])
    return refs


def diff_ref_resolution_error(message: str) -> bool:
    return (
        message.startswith("unknown FROM ref:")
        or message.startswith("unknown TO ref:")
        or message.startswith("FROM ref must be older than TO ref")
    )


def git_name_status(repo: Path, base: str | None = None, from_ref: str | None = None, to_ref: str | None = None) -> tuple[dict[str, str], str]:
    statuses: dict[str, str] = {}
    diff_args, include_untracked, error = git_diff_args(repo, base, from_ref, to_ref)
    if error:
        return statuses, error
    diff = git(["diff", "--name-status", "-z", "--find-renames", *diff_args], cwd=str(repo), timeout=5.0)
    if diff.returncode == 0:
        parts = diff.stdout.split("\0")
        index = 0
        while index < len(parts):
            status_text = parts[index]
            index += 1
            if not status_text:
                continue
            status = status_text[0]
            if status in {"R", "C"}:
                new_path = parts[index + 1] if index + 1 < len(parts) else ""
                index += 2
                if new_path:
                    statuses[new_path] = status
                continue
            rel_path = parts[index] if index < len(parts) else ""
            index += 1
            if rel_path:
                statuses[rel_path] = "A" if status == "A" else "D" if status == "D" else "M"
    if include_untracked:
        untracked = git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=str(repo), timeout=5.0)
    else:
        untracked = None
    if untracked and untracked.returncode == 0:
        for rel_path in untracked.stdout.split("\0"):
            if rel_path:
                # Untracked working-tree files get "?" (git's own untracked marker — `git status` shows
                # "??"), distinct from a genuine staged/committed add "A" (from `git diff --name-status`
                # above). Both are "new", but "A" means git is tracking the add; "?" means the file is
                # not in the index at all.
                statuses[rel_path] = "?"
    return statuses, ""


def parse_numstat_value(value: str) -> int | None:
    if value == "-":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def git_numstat(repo: Path, base: str | None = None, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, dict[str, int | None]]:
    counts: dict[str, dict[str, int | None]] = {}
    diff_args, _, error = git_diff_args(repo, base, from_ref, to_ref)
    if error:
        return counts
    diff = git(["diff", "--numstat", "-z", "--find-renames", *diff_args], cwd=str(repo), timeout=5.0)
    if diff.returncode != 0:
        return counts
    parts = diff.stdout.split("\0")
    index = 0
    while index < len(parts):
        head = parts[index]
        index += 1
        if not head:
            continue
        parts_head = head.split("\t", 2)
        if len(parts_head) < 3:
            continue
        if parts_head[2]:
            rel_path = parts_head[2]
        else:
            old_path = parts[index] if index < len(parts) else ""
            new_path = parts[index + 1] if index + 1 < len(parts) else ""
            index += 2
            rel_path = new_path or old_path
        if not rel_path:
            continue
        counts[rel_path] = {
            "added": parse_numstat_value(parts_head[0]),
            "removed": parse_numstat_value(parts_head[1]),
        }
    return counts


def git_ahead_behind(repo: Path, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, int]:
    if refs_requested(from_ref, to_ref):
        older, newer = diff_refs(from_ref, to_ref)
        left_ref = older
        right_ref = "HEAD" if newer == "current" else newer
    else:
        upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=str(repo), timeout=3.0)
        if upstream.returncode != 0 or not upstream.stdout.strip():
            return {}
        left_ref = upstream.stdout.strip()
        right_ref = "HEAD"
    if not git_ref_exists(repo, left_ref) or not git_ref_exists(repo, right_ref):
        return {}
    # shared ahead/behind parse. left...right -> ahead = right-only commits, behind = left-only.
    counts = git_ahead_behind_counts(str(repo), left_ref, right_ref)
    if counts is None:
        return {}
    ahead, behind = counts
    return {"behind": behind, "ahead": ahead}


def git_snapshot_identity(repo: Path, from_ref: str | None = None, to_ref: str | None = None) -> tuple[Any, ...]:
    """Return every worktree/ref input that can change a shared Git snapshot."""
    canonical_repo = str(repo.expanduser().resolve(strict=False))
    git_dir_result = git(["rev-parse", "--absolute-git-dir"], cwd=canonical_repo, timeout=5.0)
    git_dir = git_dir_result.stdout.strip() if git_dir_result.returncode == 0 else ""
    head_result = git(["rev-parse", "--verify", "HEAD"], cwd=canonical_repo, timeout=5.0)
    head = head_result.stdout.strip() if head_result.returncode == 0 else ""
    status_result = git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=canonical_repo, timeout=10.0)
    status_text = status_result.stdout if status_result.returncode == 0 else f"error:{status_result.returncode}:{status_result.stderr}"
    worktree_signature = hashlib.sha256(status_text.encode("utf-8", errors="replace")).hexdigest()
    index_signature = ""
    index_result = git(["rev-parse", "--git-path", "index"], cwd=canonical_repo, timeout=5.0)
    if index_result.returncode == 0 and index_result.stdout.strip():
        index_path = Path(index_result.stdout.strip())
        if not index_path.is_absolute():
            index_path = repo / index_path
        try:
            index_signature = hashlib.sha256(index_path.read_bytes()).hexdigest()
        except OSError:
            pass
    refs_active = refs_requested(from_ref, to_ref)
    selected_from, selected_to = diff_refs(from_ref, to_ref) if refs_active else ("", "")
    ref_commits: list[tuple[str, str]] = []
    for ref in (selected_from, selected_to):
        if not ref or ref == "current":
            ref_commits.append((ref, ref))
            continue
        result = git(["rev-parse", "--verify", ref], cwd=canonical_repo, timeout=5.0)
        ref_commits.append((ref, result.stdout.strip() if result.returncode == 0 else ""))
    default_ref = "" if refs_active else str(git_default_branch_ref(repo) or "")
    default_commit = ""
    if default_ref:
        default_result = git(["rev-parse", "--verify", default_ref], cwd=canonical_repo, timeout=5.0)
        default_commit = default_result.stdout.strip() if default_result.returncode == 0 else ""
    refs_result = git(["for-each-ref", "--format=%(refname)%00%(objectname)"], cwd=canonical_repo, timeout=5.0)
    refs_text = refs_result.stdout if refs_result.returncode == 0 else f"error:{refs_result.returncode}:{refs_result.stderr}"
    refs_signature = hashlib.sha256(refs_text.encode("utf-8", errors="replace")).hexdigest()
    return (
        canonical_repo,
        git_dir,
        head,
        index_signature,
        worktree_signature,
        selected_from,
        selected_to,
        tuple(ref_commits),
        default_ref,
        default_commit,
        refs_signature,
    )


def build_git_snapshot(repo: Path, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, Any]:
    """Build repository-wide Git facts once; session attribution is merged later."""
    refs_active = refs_requested(from_ref, to_ref)
    selected_from, selected_to = diff_refs(from_ref, to_ref) if refs_active else ("", "")
    repo_error = ""
    repo_error_message = message_descriptor("", "")
    diff_base = "" if refs_active else git_diff_base(repo)
    statuses, status_error = git_name_status(repo, diff_base or None, selected_from or None, selected_to or None)
    if status_error and refs_active and diff_ref_resolution_error(status_error):
        fallback_base = git_diff_base(repo)
        statuses, status_error = git_name_status(repo, fallback_base)
        numstat = git_numstat(repo, fallback_base) if not status_error else {}
        selected_from, selected_to = "", ""
        repo_error = "requested refs not found in this repo; showing default"
        repo_error_message = message_descriptor("diff.warning.refsFallback", repo_error, {"repo": repo.name})
    elif status_error:
        issue = diff_ref_issue(status_error, selected_from, selected_to, repo.name)
        repo_error = status_error
        repo_error_message = issue
        statuses = {}
        numstat = {}
    else:
        numstat = git_numstat(repo, diff_base or None, selected_from or None, selected_to or None)
    return {
        "statuses": statuses,
        "numstat": numstat,
        "selected_from": selected_from,
        "selected_to": selected_to,
        "status_error": status_error,
        "repo_error": repo_error,
        "repo_error_message": repo_error_message,
        "recent_refs": git_recent_refs(repo),
        "ahead_behind": git_ahead_behind(repo, selected_from or None, selected_to or None),
    }


def record_session_files_phase(
    recorder: SessionFilesPhaseRecorder | None,
    phase: str,
    started: float,
    details: dict[str, Any] | None = None,
) -> None:
    if recorder is None:
        return
    recorder(phase, max(0.0, (time.perf_counter() - started) * 1000), dict(details or {}))


def untracked_added_line_count(path: Path) -> int | None:
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)


def repo_relative_path(path: Path, repo: Path) -> str | None:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return None


def configured_session_repo_candidate(info: SessionInfo) -> str | None:
    configured = session_workdir(info.session)
    if not configured.is_dir():
        return None
    try:
        if configured.resolve() == Path.home().resolve():
            return None
    except OSError:
        pass
    return str(configured)


def session_candidate_repo_roots(info: SessionInfo) -> list[str]:
    roots: list[str] = []
    candidates: list[str] = []
    candidates.extend(str(agent.cwd) for agent in info.agents if agent.cwd)
    if info.selected_pane is not None and info.selected_pane.current_path:
        candidates.append(info.selected_pane.current_path)
    candidates.extend(pane.current_path for pane in info.panes if pane.current_path)
    configured = configured_session_repo_candidate(info)
    if configured:
        candidates.append(configured)
    for value in candidates:
        repo = git_root_for_path(Path(value).expanduser())
        if repo and repo not in roots:
            roots.append(repo)
    return roots


def session_live_pane_repo_roots(info: SessionInfo) -> list[str]:
    roots: list[str] = []
    candidates: list[str] = []
    if info.selected_pane is not None and info.selected_pane.current_path:
        candidates.append(info.selected_pane.current_path)
    candidates.extend(pane.current_path for pane in info.panes if pane.current_path)
    configured = configured_session_repo_candidate(info)
    if configured:
        candidates.append(configured)
    for value in candidates:
        repo = git_root_for_path(Path(value).expanduser())
        if repo and repo not in roots:
            roots.append(repo)
    return roots


def refreshing_session_files_payload_for_info(
    info: SessionInfo,
    hours: float = 24.0,
    from_ref: str | None = None,
    to_ref: str | None = None,
    repo_refs: dict[str, dict[str, str]] | None = None,
) -> SessionFilesPayload:
    refs_active = refs_requested(from_ref, to_ref)
    selected_from, selected_to = diff_refs(from_ref, to_ref) if refs_active else ("", "")
    repo_payloads: list[RepoPayload] = []
    for repo_text in session_live_pane_repo_roots(info):
        repo = Path(repo_text)
        repo_override = (repo_refs or {}).get(repo_text) or (repo_refs or {}).get(str(repo)) or {}
        repo_from = str(repo_override.get("from") or "").strip() or from_ref
        repo_to = str(repo_override.get("to") or "").strip() or to_ref
        repo_refs_active = refs_requested(repo_from, repo_to)
        sel_from, sel_to = diff_refs(repo_from, repo_to) if repo_refs_active else ("", "")
        repo_payload: RepoPayload = {
            "repo": str(repo),
            "count": 0,
            "touched_count": 0,
            "added": 0,
            "removed": 0,
            "from_ref": sel_from or "default",
            "to_ref": sel_to or "base",
            "error": "",
        }
        repo_payload.update(git_ahead_behind(repo, sel_from or None, sel_to or None))
        repo_payloads.append(repo_payload)
    return {
        "session": info.session,
        "hours": bounded_session_files_hours(hours),
        "files": [],
        "repos": repo_payloads,
        "refs_by_repo": {},
        "from_ref": selected_from or "default",
        "to_ref": selected_to or "base",
        "errors": [],
        "warnings": [],
        "refreshing_elsewhere": True,
    }


def session_file_entry(
    session: str,
    agents: list[str],
    status: str,
    path: Path,
    repo: Path | None,
    source: str,
    added: int | None = None,
    removed: int | None = None,
    mtime: float | None = None,
    diff_tracked: bool | None = None,
    agent_windows: list[dict[str, Any]] | None = None,
) -> SessionFileEntry:
    rel_path = repo_relative_path(path, repo) if repo else None
    agent_list = [a for a in agents if a]
    tracked_diff = bool(repo) and source == "git" and status != "?"
    missing = not path.exists()
    if diff_tracked is not None:
        tracked_diff = diff_tracked
    return {
        "session": session,
        # C5: a changed file can be touched by 0, 1, or several agents, so carry the full list (the UI
        # renders 0-to-N icons from it). `agent` stays as a scalar first-agent alias for legacy consumers.
        "agents": agent_list,
        "agent": agent_list[0] if agent_list else "",
        "agent_windows": agent_windows or [],
        "status": status,
        "repo": str(repo) if repo else "",
        "path": rel_path or str(path),
        "abs_path": str(path),
        "mtime": file_mtime(path) if mtime is None else mtime,
        "size": file_size(path),
        "missing": missing,
        "source": source,
        "added": added,
        "removed": removed,
        "diff_tracked": tracked_diff,
        "uploaded": is_generated_upload_name(path),
    }


def line_total(entries: list[dict[str, Any]], key: str) -> int:
    total = 0
    for entry in entries:
        if entry.get("diff_tracked") is not True:
            continue
        value = entry.get(key)
        if isinstance(value, int):
            total += value
    return total


def differ_visible_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if str(entry.get("status") or "M").upper() != "T"]


def merge_agent_lists(*agent_lists: list[str]) -> list[str]:
    merged: list[str] = []
    for agent_list in agent_lists:
        for agent_name in agent_list:
            if agent_name and agent_name not in merged:
                merged.append(agent_name)
    return merged


def agent_window_for_info(info: SessionInfo, agent: AgentInfo) -> tuple[str, str]:
    for pane in info.panes:
        if pane.target == agent.pane_target or pane.pane_id == agent.pane_target:
            return str(pane.window or ""), str(pane.pane or "")
    match = re.match(r"^[^:]+:(?P<window>[^.]+)(?:\.(?P<pane>.*))?$", str(agent.pane_target or ""))
    if not match:
        return "", ""
    return match.group("window") or "", match.group("pane") or ""


def agent_window_attribution(info: SessionInfo, agent: AgentInfo) -> dict[str, Any]:
    window, pane = agent_window_for_info(info, agent)
    try:
        window_index: int | None = int(window)
    except ValueError:
        window_index = None
    return {
        "kind": str(agent.kind or ""),
        "window": window,
        "window_index": window_index,
        "pane": pane,
        "pane_target": str(agent.pane_target or ""),
    }


def merge_agent_window_lists(*window_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for window_list in window_lists:
        for raw in window_list:
            if not isinstance(raw, dict):
                continue
            item = {
                "kind": str(raw.get("kind") or ""),
                "window": str(raw.get("window") or ""),
                "window_index": raw.get("window_index") if isinstance(raw.get("window_index"), int) else None,
                "pane": str(raw.get("pane") or ""),
                "pane_target": str(raw.get("pane_target") or ""),
            }
            key = (item["kind"], item["window"], item["pane"], item["pane_target"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def touched_files_for_info(info: SessionInfo, cutoff: float, warnings: list[str | dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    touched: dict[str, dict[str, Any]] = {}
    for agent in info.agents:
        if not agent.transcript:
            # D2: a missing/undiscoverable transcript is inherently a PER-AGENT condition (e.g. an inactive
            # background Codex pane that never wrote a discoverable rollout). It is NOT a session-level
            # failure: the other agents and git-derived repo data in the same session are still valid. Surface
            # it as a non-blocking warning so the Differ keeps rendering the changed files/repos instead of
            # treating the whole session as failed (the frontend renders payload["errors"] as red blocking
            # rows; warnings are a separate, non-blocking channel).
            if agent.error and warnings is not None:
                warnings.append(message_descriptor("diff.warning.agentDiscovery", agent.error, {"error": agent.error}))
            continue
        transcript = Path(agent.transcript).expanduser()
        transcript_mtime = file_mtime(transcript)
        if transcript_mtime < cutoff:
            continue
        for path_text, markers in scan_agent_changes(agent).items():
            # C5: accumulate every agent that touched this path instead of overwriting, so a file edited
            # by both Claude and Codex keeps both attributions (rendered as two icons).
            entry = touched.setdefault(path_text, {"agents": [], "agent_windows": [], "status": "", "mtime": 0.0})
            if agent.kind and agent.kind not in entry["agents"]:
                entry["agents"].append(agent.kind)
            entry["agent_windows"] = merge_agent_window_lists(entry.get("agent_windows", []), [agent_window_attribution(info, agent)])
            entry["status"] = classify_change(markers)
            entry["mtime"] = max(float(entry.get("mtime") or 0.0), transcript_mtime)
    for path_text, metadata in historical_codex_changes_for_info(info, cutoff).items():
        entry = touched.setdefault(path_text, {"agents": [], "agent_windows": [], "status": "", "mtime": 0.0})
        if "codex" not in entry["agents"]:
            entry["agents"].append("codex")
        entry["status"] = str(metadata.get("status") or "M")
        entry["mtime"] = max(float(entry.get("mtime") or 0.0), float(metadata.get("mtime") or 0.0))
    return touched


def historical_codex_changes_for_info(info: SessionInfo, cutoff: float) -> dict[str, dict[str, Any]]:
    seen = {str(agent.transcript) for agent in info.agents if agent.transcript}
    changes: dict[str, dict[str, Any]] = {}
    for cwd in historical_codex_candidate_cwds(info):
        transcript = historical_codex_transcript_for_cwd(cwd, cutoff)
        if transcript is None:
            continue
        transcript_text = str(transcript)
        if transcript_text in seen or file_mtime(transcript) < cutoff:
            continue
        seen.add(transcript_text)
        transcript_mtime = file_mtime(transcript)
        for path_text, markers in scan_codex_transcript(transcript, cwd, include_patch_text=False).items():
            if not path_is_under_text(path_text, cwd):
                continue
            entry = changes.setdefault(path_text, {"status": "", "mtime": 0.0})
            entry["status"] = classify_change(markers)
            entry["mtime"] = max(float(entry.get("mtime") or 0.0), transcript_mtime)
    return changes


def historical_codex_transcript_for_cwd(cwd: str, cutoff: float) -> Path | None:
    candidates: list[Path] = []
    direct = find_recent_codex_transcript(cwd)
    if direct is not None:
        candidates.append(direct)
    candidates.extend(recent_codex_transcript_candidates())
    for record in historical_codex_transcript_index(candidates):
        if record.mtime < cutoff:
            continue
        changes = resolved_codex_raw_changes(record.raw_shell_changes, cwd)
        if any(path_is_under_text(path_text, cwd) for path_text in changes):
            return record.path
    return None


def historical_codex_transcript_index(candidates: list[Path]) -> tuple[HistoricalCodexTranscriptRecord, ...]:
    """Return one bounded, raw-parsed candidate window shared by all cwd lookups."""
    unique: list[tuple[Path, tuple[str, int, int, int, int]]] = []
    seen: set[str] = set()
    for transcript in candidates:
        try:
            stat = transcript.stat()
        except OSError:
            continue
        resolved = str(transcript.expanduser().resolve(strict=False))
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append((transcript, (resolved, int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))))
    identity = tuple(item[1] for item in unique)
    with _HISTORICAL_CODEX_TRANSCRIPT_INDEX_GUARD:
        cached = _HISTORICAL_CODEX_TRANSCRIPT_INDEX.get(identity)
        if cached is not None:
            return cached
    records: list[HistoricalCodexTranscriptRecord] = []
    for transcript, _signature in unique:
        raw = codex_transcript_raw_shell_changes(transcript)
        records.append(HistoricalCodexTranscriptRecord(transcript, file_mtime(transcript), raw))
    result = tuple(records)
    with _HISTORICAL_CODEX_TRANSCRIPT_INDEX_GUARD:
        _HISTORICAL_CODEX_TRANSCRIPT_INDEX[identity] = result
        while len(_HISTORICAL_CODEX_TRANSCRIPT_INDEX) > _HISTORICAL_CODEX_TRANSCRIPT_INDEX_MAX:
            _HISTORICAL_CODEX_TRANSCRIPT_INDEX.pop(next(iter(_HISTORICAL_CODEX_TRANSCRIPT_INDEX)))
    return result


def codex_transcript_raw_shell_changes(path: Path) -> dict[str, set[str]]:
    cache_key = codex_transcript_scan_cache_key(path)
    return incremental_transcript_scan_details(
        path,
        cache_key,
        new_codex_transcript_scan_state,
        update=lambda state, line: update_codex_transcript_scan_state(state, line),
        details=lambda state: copy_change_set(state.get("shell_changes") if isinstance(state.get("shell_changes"), dict) else {}),
    )


def path_is_under_text(path_text: str, root_text: str) -> bool:
    path = Path(path_text).expanduser().resolve(strict=False)
    root = Path(root_text).expanduser().resolve(strict=False)
    return path == root or path.is_relative_to(root)


def historical_codex_candidate_cwds(info: SessionInfo) -> list[str]:
    candidates: list[str] = []
    for agent in info.agents:
        if agent.cwd:
            candidates.append(agent.cwd)
    if info.selected_pane is not None and info.selected_pane.current_path:
        candidates.append(info.selected_pane.current_path)
    candidates.extend(pane.current_path for pane in info.panes if pane.current_path)
    candidates.extend(session_candidate_repo_roots(info))
    unique: list[str] = []
    for value in candidates:
        text = str(value or "").strip()
        if not text:
            continue
        repo = git_root_for_path(Path(text).expanduser())
        if repo and repo not in unique:
            unique.append(repo)
    return unique


def agent_attribution_by_path(infos: dict[str, SessionInfo], cutoff: float) -> dict[str, list[str]]:
    attribution: dict[str, list[str]] = {}
    for info in infos.values():
        for path_text, metadata in touched_files_for_info(info, cutoff).items():
            attribution[path_text] = merge_agent_lists(attribution.get(path_text, []), metadata.get("agents", []))
    return attribution


def session_files_payload_for_info(
    info: SessionInfo,
    hours: float = 24.0,
    now: float | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    repo_refs: dict[str, dict[str, str]] | None = None,
    agent_attribution: dict[str, list[str]] | None = None,
    git_snapshot_provider: GitSnapshotProvider | None = None,
    phase_recorder: SessionFilesPhaseRecorder | None = None,
) -> SessionFilesPayload:
    # C6: `repo_refs` carries per-repo FROM/TO overrides ({repo_path: {"from","to"}}); a SHA chosen for
    # one repo no longer leaks into another. The scalar from_ref/to_ref stay as the global default applied
    # to any repo without an override (and drive the top-level payload refs for legacy single-repo callers).
    cutoff = session_files_cutoff(hours, now)
    refs_active = refs_requested(from_ref, to_ref)
    selected_from, selected_to = diff_refs(from_ref, to_ref) if refs_active else ("", "")
    errors: list[str | dict[str, Any]] = []
    # D2: per-agent transcript-discovery problems land here, separate from the blocking `errors` list, so a
    # single inactive agent's missing transcript does not read as a session-level Differ failure.
    warnings: list[str | dict[str, Any]] = []
    phase_started = time.perf_counter()
    touched = touched_files_for_info(info, cutoff, warnings)
    record_session_files_phase(phase_recorder, "transcript-attribution", phase_started, {"session": info.session, "paths": len(touched)})

    phase_started = time.perf_counter()
    repos: dict[str, set[str]] = {}
    outside_repo_paths: set[str] = set()
    for path_text, metadata in touched.items():
        path = Path(path_text)
        repo_text = git_root_for_path(path)
        if repo_text:
            repos.setdefault(repo_text, set()).add(path_text)
        else:
            outside_repo_paths.add(path_text)
    candidate_repo_roots = set(session_candidate_repo_roots(info))
    for repo_text in candidate_repo_roots:
        repos.setdefault(repo_text, set())
    live_pane_repo_roots = set(session_live_pane_repo_roots(info))
    record_session_files_phase(phase_recorder, "repository-discovery", phase_started, {"session": info.session, "repos": len(repos)})

    phase_started = time.perf_counter()
    files: list[SessionFileEntry] = []
    repo_payloads: list[RepoPayload] = []
    refs_by_repo: dict[str, list[dict[str, Any]]] = {}
    for repo_text in sorted(repos):
        repo = Path(repo_text)
        # C6: resolve this repo's effective FROM/TO — its own override if present, else the global scalar.
        repo_override = (repo_refs or {}).get(repo_text) or (repo_refs or {}).get(str(repo)) or {}
        repo_from = str(repo_override.get("from") or "").strip() or from_ref
        repo_to = str(repo_override.get("to") or "").strip() or to_ref
        repo_refs_active = refs_requested(repo_from, repo_to)
        snapshot = (
            git_snapshot_provider(repo, repo_from, repo_to)
            if git_snapshot_provider is not None
            else build_git_snapshot(repo, repo_from, repo_to)
        )
        statuses = {
            str(path): str(status)
            for path, status in snapshot.get("statuses", {}).items()
        }
        numstat = {
            str(path): dict(counts)
            for path, counts in snapshot.get("numstat", {}).items()
            if isinstance(counts, dict)
        }
        sel_from = str(snapshot.get("selected_from") or "")
        sel_to = str(snapshot.get("selected_to") or "")
        status_error = str(snapshot.get("status_error") or "")
        repo_error = str(snapshot.get("repo_error") or "")
        raw_error_message = snapshot.get("repo_error_message")
        repo_error_message = dict(raw_error_message) if isinstance(raw_error_message, dict) else message_descriptor("", "")
        if status_error and not repo_error.startswith("requested refs not found"):
            issue = repo_error_message if repo_error_message.get("fallback") else diff_ref_issue(status_error, sel_from, sel_to, repo.name)
            errors.append(message_descriptor("diff.error.repo", f"{repo.name}: {status_error}", {"repo": repo.name, "error": issue["fallback"]}))
        touched_by_rel: dict[str, dict[str, Any]] = {}
        for touched_path, metadata in touched.items():
            rel_path = repo_relative_path(Path(touched_path), repo)
            if rel_path:
                touched_by_rel[rel_path] = metadata
        repo_entries: list[SessionFileEntry] = []
        for rel_path, status in statuses.items():
            path = repo / rel_path
            counts = numstat.get(rel_path, {})
            added = counts.get("added")
            removed = counts.get("removed")
            diff_tracked = status != "?" and rel_path in numstat
            if status in {"A", "?"} and rel_path not in numstat:
                added = untracked_added_line_count(path)
                removed = 0
            # C5: attribute the file to exactly the agents the transcripts say touched it — no fallback.
            # A repo-only change with no transcript attribution gets an empty list (zero agent icons).
            agents = merge_agent_lists(touched_by_rel.get(rel_path, {}).get("agents", []), (agent_attribution or {}).get(str(path), []))
            repo_entries.append(session_file_entry(
                info.session,
                agents,
                status,
                path,
                repo,
                "git",
                added=added,
                removed=removed,
                diff_tracked=diff_tracked,
                agent_windows=merge_agent_window_lists(touched_by_rel.get(rel_path, {}).get("agent_windows", [])),
            ))
        for rel_path, metadata in touched_by_rel.items():
            if rel_path in statuses:
                continue
            if repo_refs_active:
                continue
            path = repo / rel_path
            repo_entries.append(session_file_entry(
                info.session,
                merge_agent_lists(metadata.get("agents", []), (agent_attribution or {}).get(str(path), [])),
                "T",
                path,
                repo,
                "transcript",
                mtime=file_mtime_or_fallback(path, metadata.get("mtime")),
                agent_windows=merge_agent_window_lists(metadata.get("agent_windows", [])),
            ))
        repo_entries.sort(key=lambda item: (-float(item.get("mtime") or 0), item["path"]))
        files.extend(repo_entries)
        rendered_entries = differ_visible_entries(repo_entries)
        if not rendered_entries and repo_text not in live_pane_repo_roots and not repo_refs_active:
            continue
        refs_by_repo[str(repo)] = copy.deepcopy(snapshot.get("recent_refs", []))
        repo_payload: RepoPayload = {
            "repo": str(repo),
            "count": len(rendered_entries),
            "touched_count": len(repos[repo_text]),
            "added": line_total(rendered_entries, "added"),
            "removed": line_total(rendered_entries, "removed"),
            # C6: report the refs THIS repo actually compared, plus any per-repo fallback, so each repo
            # header can render its own comparison title independently of the others.
            "from_ref": sel_from or "default",
            "to_ref": sel_to or "base",
            "error": repo_error,
        }
        if repo_error_message.get("key") or repo_error_message.get("fallback"):
            repo_payload["error_message"] = repo_error_message
        ahead_behind = snapshot.get("ahead_behind")
        if isinstance(ahead_behind, dict):
            repo_payload.update({str(key): int(value) for key, value in ahead_behind.items() if isinstance(value, int)})
        repo_payloads.append(repo_payload)

    outside_entries: list[SessionFileEntry] = []
    if outside_repo_paths and not refs_active:
        for path_text in sorted(outside_repo_paths):
            path = Path(path_text)
            metadata = touched.get(path_text, {})
            status = str(metadata.get("status") or "?")
            outside_entries.append(session_file_entry(
                info.session,
                merge_agent_lists(metadata.get("agents", []), (agent_attribution or {}).get(str(path), [])),
                status if status in {"A", "D", "M", "?"} else "?",
                path,
                None,
                "transcript",
                untracked_added_line_count(path) if path.exists() and path.is_file() else None,
                0 if path.exists() and path.is_file() else None,
                mtime=file_mtime_or_fallback(path, metadata.get("mtime")),
                diff_tracked=False,
                agent_windows=merge_agent_window_lists(metadata.get("agent_windows", [])),
            ))
        outside_entries.sort(key=lambda item: (-float(item.get("mtime") or 0), item["path"]))
        files.extend(outside_entries)
        repo_payloads.append({
            "repo": "",
            "count": len(outside_entries),
            "touched_count": len(outside_entries),
            "added": 0,
            "removed": 0,
            "from_ref": "default",
            "to_ref": "base",
            "error": "",
        })

    files.sort(key=lambda item: (-float(item.get("mtime") or 0), item["repo"], item["path"]))
    record_session_files_phase(
        phase_recorder,
        "session-merge-render",
        phase_started,
        {"session": info.session, "repos": len(repo_payloads), "files": len(files)},
    )
    payload_from_ref = selected_from or "default"
    payload_to_ref = selected_to or "base"
    return {
        "session": info.session,
        "hours": bounded_session_files_hours(hours),
        "files": files,
        "repos": repo_payloads,
        "refs_by_repo": refs_by_repo,
        "from_ref": payload_from_ref,
        "to_ref": payload_to_ref,
        "errors": errors,
        "warnings": warnings,
    }


def session_files_payload(
    session: str | None,
    infos: dict[str, SessionInfo],
    hours: float = 24.0,
    from_ref: str | None = None,
    to_ref: str | None = None,
    repo_refs: dict[str, dict[str, str]] | None = None,
    include_cross_session_attribution: bool = True,
    git_snapshot_provider: GitSnapshotProvider | None = None,
    phase_recorder: SessionFilesPhaseRecorder | None = None,
) -> tuple[SessionFilesPayload, HTTPStatus]:
    now = time.time()
    cutoff = session_files_cutoff(hours, now)
    attribution = agent_attribution_by_path(infos, cutoff) if include_cross_session_attribution else {}
    if session:
        info = infos.get(session)
        if info is None:
            diagnostic = f"unknown session: {session}"
            return {"session": session, **user_message_payload("status.sessionEnded", diagnostic, session=session)}, HTTPStatus.NOT_FOUND
        payload = session_files_payload_for_info(
            info,
            hours,
            now=now,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_refs=repo_refs,
            agent_attribution=attribution,
            git_snapshot_provider=git_snapshot_provider,
            phase_recorder=phase_recorder,
        )
        return payload, HTTPStatus.OK

    files: list[SessionFileEntry] = []
    repos: dict[str, RepoPayload] = {}
    refs_by_repo: dict[str, list[dict[str, Any]]] = {}
    errors: list[str | dict[str, Any]] = []
    warnings: list[str | dict[str, Any]] = []
    for info in infos.values():
        payload = session_files_payload_for_info(
            info,
            hours,
            now=now,
            from_ref=from_ref,
            to_ref=to_ref,
            repo_refs=repo_refs,
            agent_attribution=attribution,
            git_snapshot_provider=git_snapshot_provider,
            phase_recorder=phase_recorder,
        )
        files.extend(payload["files"])
        errors.extend(payload["errors"])
        warnings.extend(payload.get("warnings", []))
        refs_by_repo.update(payload.get("refs_by_repo", {}))
        for repo in payload["repos"]:
            key = repo["repo"]
            existing = repos.setdefault(key, {"repo": key, "count": 0, "touched_count": 0, "added": 0, "removed": 0})
            existing["count"] += repo["count"]
            existing["touched_count"] += repo["touched_count"]
            existing["added"] += repo.get("added", 0)
            existing["removed"] += repo.get("removed", 0)
            # C6: carry the per-repo effective comparison refs/error from the first session that touched it.
            existing.setdefault("from_ref", repo.get("from_ref", "default"))
            existing.setdefault("to_ref", repo.get("to_ref", "base"))
            existing.setdefault("error", repo.get("error", ""))
            if "error_message" in repo:
                existing.setdefault("error_message", repo["error_message"])
    files.sort(key=lambda item: (-float(item.get("mtime") or 0), item["session"], item["path"]))
    return {
        "session": "",
        "hours": bounded_session_files_hours(hours),
        "files": files,
        "repos": sorted(repos.values(), key=lambda item: item["repo"]),
        "refs_by_repo": refs_by_repo,
        "from_ref": diff_refs(from_ref, to_ref)[0] if refs_requested(from_ref, to_ref) else "default",
        "to_ref": diff_refs(from_ref, to_ref)[1] if refs_requested(from_ref, to_ref) else "base",
        "errors": errors,
        "warnings": warnings,
    }, HTTPStatus.OK
