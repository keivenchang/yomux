# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded host process-memory census for the elected stats owner."""

from __future__ import annotations

import hashlib
import math
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


MAX_PROCESS_MEMORY_SERIES = 5
# Retained rows from the original implementation carried eight binaries. New censuses and the
# UI are top-five, but the reader must continue accepting those immutable historical facts.
MAX_PROCESS_MEMORY_PAYLOAD_SERIES = 8
MAX_PROCESS_CPU_SERIES = 4
MAX_PROCESS_BINARY_LENGTH = 64
# Every stored process payload re-derives its binary keys on read to prove they are
# already normalized, so one build asks the same short question hundreds of thousands
# of times about a few hundred distinct names. The answer is a pure function of the
# text, so it is cached. The bound is deliberate: see NORMALIZED_BINARY_CACHE_ENTRIES.
NORMALIZED_BINARY_CACHE_ENTRIES = 1024
_DELETED_SUFFIX_RE = re.compile(r"\s+\(deleted\)$", re.IGNORECASE)
_PYTHON_BINARY_RE = re.compile(r"python(?:\d+(?:\.\d+)*)?", re.IGNORECASE)
_VERSION_BINARY_RE = re.compile(r"\d+(?:\.\d+){1,3}")
_SAFE_BINARY_RE = re.compile(r"[^a-z0-9._+-]+")
_PROC_ROOT = Path("/proc")
_LINUX_PROCESS_BINARY_CACHE: dict[tuple[str, str], str] = {}


@dataclass(frozen=True, slots=True)
class ProcessCensusRow:
    """One private native row; only grouped binary totals leave the sampler."""

    pid: int
    identity: str
    binary: str
    cpu_seconds: float
    rss_bytes: int


def normalize_process_binary(value: object) -> str:
    """Return one privacy-safe binary identity without paths or arguments."""

    # Coerce to text BEFORE the cache. Keying the cache on the caller's object would
    # let `1` and `True` share one entry -- they are equal and hash alike, yet they
    # normalize to "1" and "true-3cbc87c7" -- and would raise on an unhashable list.
    return _normalized_process_binary(str(value or "").strip())


@lru_cache(maxsize=NORMALIZED_BINARY_CACHE_ENTRIES)
def _normalized_process_binary(raw: str) -> str:
    """Derive one binary identity from already-stripped text."""

    raw = _DELETED_SUFFIX_RE.sub("", raw)
    name = Path(raw).name
    if _PYTHON_BINARY_RE.fullmatch(name):
        return "python"
    if name.lower() == "nodejs":
        return "node"
    safe = _SAFE_BINARY_RE.sub("-", name.lower()).strip("-._")
    if not safe:
        return ""
    if safe == name and len(safe) <= MAX_PROCESS_BINARY_LENGTH:
        return safe
    digest = hashlib.sha256(name.encode("utf-8", errors="replace")).hexdigest()[:8]
    prefix = safe[:MAX_PROCESS_BINARY_LENGTH - len(digest) - 1].rstrip("-._") or "binary"
    return f"{prefix}-{digest}"


def aggregate_process_memory_by_binary(
    rows: Iterable[tuple[object, object]],
) -> dict[str, int]:
    """Sum RSS by normalized executable and retain deterministic top consumers."""

    totals: dict[str, int] = {}
    for raw_binary, raw_rss in rows:
        binary = normalize_process_binary(raw_binary)
        if not binary or isinstance(raw_rss, bool) or not isinstance(raw_rss, (int, float)):
            continue
        rss = float(raw_rss)
        if not math.isfinite(rss) or rss <= 0:
            continue
        totals[binary] = totals.get(binary, 0) + int(rss)
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return dict(ranked[:MAX_PROCESS_MEMORY_SERIES])


def aggregate_process_cpu_by_binary(
    rows: Iterable[tuple[object, object]],
) -> dict[str, float]:
    """Sum measured host-capacity CPU by binary and retain deterministic top consumers."""

    totals: dict[str, float] = {}
    for raw_binary, raw_percent in rows:
        binary = normalize_process_binary(raw_binary)
        if not binary or isinstance(raw_percent, bool) or not isinstance(raw_percent, (int, float)):
            continue
        percent = float(raw_percent)
        if not math.isfinite(percent) or percent < 0:
            continue
        totals[binary] = totals.get(binary, 0.0) + percent
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return {binary: round(min(100.0, percent), 3) for binary, percent in ranked[:MAX_PROCESS_CPU_SERIES]}


def _linux_process_binary(comm: str, executable: str) -> str:
    raw_executable = re.sub(
        r"\s+\(deleted\)$", "", str(executable or "").strip(), flags=re.IGNORECASE,
    )
    executable_name = Path(raw_executable).name
    if executable_name and _VERSION_BINARY_RE.fullmatch(executable_name):
        parts = [normalize_process_binary(part) for part in Path(raw_executable).parts]
        for index, part in enumerate(parts):
            if part == "versions" and index > 0 and parts[index - 1]:
                return parts[index - 1]
    if executable_name:
        return normalize_process_binary(executable_name)
    return normalize_process_binary(comm)


def _linux_process_census(
    proc_root: Path = _PROC_ROOT,
    *,
    binary_cache: dict[tuple[str, str], str] | None = None,
) -> tuple[ProcessCensusRow, ...] | None:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        clock_ticks = float(os.sysconf("SC_CLK_TCK"))
        with os.scandir(proc_root) as entries:
            pid_entries = tuple(entry.name for entry in entries if entry.name.isdigit())
    except (OSError, ValueError):
        return None
    rows: list[ProcessCensusRow] = []
    retained_binaries: dict[tuple[str, str], str] = {}
    root = os.fspath(proc_root)
    for pid_text in pid_entries:
        try:
            process_root = os.path.join(root, pid_text)
            with open(os.path.join(process_root, "stat"), encoding="utf-8") as stat_file:
                stat_head, stat_tail = stat_file.read().rsplit(") ", 1)
            comm_prefix = f"{pid_text} ("
            if not stat_head.startswith(comm_prefix):
                raise ValueError("invalid proc stat identity")
            comm = stat_head[len(comm_prefix):]
            fields = stat_tail.split()
            pid = int(pid_text)
            cpu_seconds = (float(fields[11]) + float(fields[12])) / clock_ticks
            started_at_ticks = int(fields[19])
            rss = max(0, int(fields[21]) * page_size)
            identity = f"{pid}:{started_at_ticks}"
            cache_key = (identity, comm)
            binary = None if binary_cache is None else binary_cache.get(cache_key)
            if binary is None:
                try:
                    executable = os.readlink(os.path.join(process_root, "exe"))
                except OSError:
                    executable = ""
                binary = _linux_process_binary(comm, executable)
        except (IndexError, OSError, ValueError):
            continue
        if binary:
            retained_binaries[cache_key] = binary
            rows.append(ProcessCensusRow(pid, identity, binary, cpu_seconds, rss))
    if binary_cache is not None:
        binary_cache.clear()
        binary_cache.update(retained_binaries)
    if pid_entries and not rows:
        return None
    return tuple(rows)


def _darwin_cpu_time_seconds(value: str) -> float:
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", value.strip())
    if match is None:
        raise ValueError("invalid process CPU time")
    days, hours, minutes, seconds = match.groups()
    return (
        float(days or 0) * 86400.0
        + float(hours or 0) * 3600.0
        + float(minutes) * 60.0
        + float(seconds)
    )


def _darwin_process_census() -> tuple[ProcessCensusRow, ...] | None:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,rss=,time=,lstart=,comm="],
            capture_output=True,
            text=True,
            timeout=0.75,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
    rows: list[ProcessCensusRow] = []
    for line in lines:
        parts = line.split(maxsplit=8)
        if len(parts) != 9:
            continue
        try:
            pid = int(parts[0])
            rss = max(0, int(parts[1]) * 1024)
            cpu_seconds = _darwin_cpu_time_seconds(parts[2])
        except ValueError:
            continue
        binary = normalize_process_binary(parts[8])
        if binary:
            rows.append(ProcessCensusRow(pid, f"{pid}:{' '.join(parts[3:8])}", binary, cpu_seconds, rss))
    if lines and not rows:
        return None
    return tuple(rows)


def process_census() -> tuple[ProcessCensusRow, ...] | None:
    """Read one all-process native snapshot for both CPU and memory grouping."""

    return (
        _darwin_process_census()
        if sys.platform == "darwin"
        else _linux_process_census(binary_cache=_LINUX_PROCESS_BINARY_CACHE)
    )


def process_memory_by_binary() -> dict[str, int] | None:
    """Compatibility helper for callers that need a one-shot memory census."""

    rows = process_census()
    if rows is None:
        return None
    return aggregate_process_memory_by_binary((row.binary, row.rss_bytes) for row in rows)
