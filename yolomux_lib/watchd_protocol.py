# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded descriptor and revision contract shared by watchd and web."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


WATCHD_PROTOCOL_VERSION = 1
WATCHD_CODE_REVISION = "watchd-v2"
WATCHD_SERVICE_NAME = "watchd"
WATCHD_MAX_PATHS = 256
WATCHD_MAX_CHANGED_PATHS = 256
WATCHD_MAX_WAIT_SECONDS = 30.0
WATCHD_DESCRIPTOR_TTL_SECONDS = 90.0
# How long the web bridge may reuse the transcript set it last derived for its descriptors.
# The revision loop runs back-to-back — every transcript byte it watches produces a revision —
# so rebuilding that set per revision cost ~26ms of CPU per pass and held the loop near a full
# core. A tmux topology change bypasses this and resyncs immediately; the interval is only the
# backstop for changes the signature cannot see. 6x under WATCHD_DESCRIPTOR_TTL_SECONDS, so a
# descriptor cannot expire between resyncs even if every one of them is served from the memo.
WATCHD_DESCRIPTOR_RESYNC_SECONDS = 15.0
# Floor on one revision-loop iteration. The loop's CPU converges to body_cpu / loop_period, and
# because a cheaper body also re-arms sooner, that ratio is scale-invariant: three rounds of
# making the body cheaper moved it 89% -> 47% -> 43% of a core and could not reach the 30%
# budget. Only a period floor can. At the measured 3.07ms of body CPU this holds 6.1% of a core,
# a 5x margin rather than the 10.2ms that would just scrape past. The cost is up to 50ms of
# added latency before the browser sees a filesystem change, which is below both perception and
# the UI's frame budget. No revision is lost: watchd's revision counter is monotonic, so a
# change arriving during the floor is returned by the next wait_revision immediately.
WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS = 0.05
WATCHD_SNAPSHOT_DEADLINE_SECONDS = 10.0
WATCHD_PRIVATE_FIELDS = frozenset({"cookie", "authorization", "browser_metrics", "private_client_state"})


class WatchProtocolError(ValueError):
    """One watchd request violates its bounded public contract."""


def _bounded_paths(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > WATCHD_MAX_PATHS:
        raise WatchProtocolError(f"invalid {field}")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.startswith("/") or len(item) > 4096 or "\x00" in item:
            raise WatchProtocolError(f"invalid {field}")
        paths.append(str(Path(item).expanduser().resolve(strict=False)))
    return tuple(sorted(set(paths)))


@dataclass(frozen=True)
class WatchDescriptor:
    descriptor_generation: int
    expires_at: float
    roots: tuple[str, ...]
    files: tuple[str, ...]
    background_files: tuple[str, ...]
    transcripts: tuple[str, ...]
    repo_roots: tuple[str, ...]
    indexed_dirs: tuple[str, ...]
    skip_dirs: tuple[str, ...]
    settings_path: str
    attention_path: str
    configured_roots: tuple[str, ...]

    def stable_payload(self) -> tuple[object, ...]:
        return (
            self.roots,
            self.files,
            self.background_files,
            self.transcripts,
            self.repo_roots,
            self.indexed_dirs,
            self.skip_dirs,
            self.settings_path,
            self.attention_path,
            self.configured_roots,
        )


@dataclass(frozen=True)
class EffectiveWatchConfiguration:
    roots: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    background_files: tuple[str, ...] = ()
    transcripts: tuple[str, ...] = ()
    repo_roots: tuple[str, ...] = ()
    indexed_dirs: tuple[str, ...] = ()
    skip_dirs: tuple[str, ...] = ()
    settings_paths: tuple[str, ...] = ()
    attention_paths: tuple[str, ...] = ()
    configured_roots: tuple[str, ...] = ()
    watch_paths: tuple[str, ...] = ()

    def stable_payload(self) -> tuple[object, ...]:
        return (
            self.roots,
            self.files,
            self.background_files,
            self.transcripts,
            self.repo_roots,
            self.indexed_dirs,
            self.skip_dirs,
            self.settings_paths,
            self.attention_paths,
            self.configured_roots,
            self.watch_paths,
        )


def validate_descriptor(value: object) -> WatchDescriptor:
    if not isinstance(value, dict) or any(field in value for field in WATCHD_PRIVATE_FIELDS):
        raise WatchProtocolError("invalid descriptor")
    generation = value.get("descriptor_generation")
    expires_at = value.get("expires_at")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise WatchProtocolError("invalid descriptor_generation")
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or expires_at <= 0:
        raise WatchProtocolError("invalid expires_at")
    settings_path = _bounded_paths([value.get("settings_path")], "settings_path")[0]
    attention_path = _bounded_paths([value.get("attention_path")], "attention_path")[0]
    skip_dirs = value.get("skip_dirs")
    if not isinstance(skip_dirs, list) or len(skip_dirs) > WATCHD_MAX_PATHS or any(not isinstance(item, str) or not item or "/" in item for item in skip_dirs):
        raise WatchProtocolError("invalid skip_dirs")
    return WatchDescriptor(
        descriptor_generation=generation,
        expires_at=float(expires_at),
        roots=_bounded_paths(value.get("roots"), "roots"),
        files=_bounded_paths(value.get("files"), "files"),
        background_files=_bounded_paths(value.get("background_files"), "background_files"),
        transcripts=_bounded_paths(value.get("transcripts"), "transcripts"),
        repo_roots=_bounded_paths(value.get("repo_roots"), "repo_roots"),
        indexed_dirs=_bounded_paths(value.get("indexed_dirs"), "indexed_dirs"),
        skip_dirs=tuple(sorted(set(skip_dirs))),
        settings_path=settings_path,
        attention_path=attention_path,
        configured_roots=_bounded_paths(value.get("configured_roots"), "configured_roots"),
    )


def validate_request(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or any(field in value for field in WATCHD_PRIVATE_FIELDS):
        raise WatchProtocolError("request must be an object")
    if value.get("protocol_version", WATCHD_PROTOCOL_VERSION) != WATCHD_PROTOCOL_VERSION:
        raise WatchProtocolError("upgrade_required")
    action = value.get("action")
    if action not in {"ping", "status", "lease", "release", "upsert", "remove", "wait_revision", "snapshot", "snapshot_product", "shutdown", "shutdown_if_idle"}:
        raise WatchProtocolError("unknown watch action")
    if action in {"upsert", "remove"}:
        if not isinstance(value.get("lease_id"), str) or not value["lease_id"]:
            raise WatchProtocolError("invalid lease_id")
        descriptor_id = value.get("descriptor_id")
        if not isinstance(descriptor_id, str) or not descriptor_id or len(descriptor_id) > 160:
            raise WatchProtocolError("invalid descriptor_id")
    if action == "upsert":
        validate_descriptor(value.get("descriptor"))
    if action == "wait_revision":
        revision = value.get("after_revision", 0)
        timeout = value.get("timeout_seconds", 0.0)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise WatchProtocolError("invalid after_revision")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout <= WATCHD_MAX_WAIT_SECONDS:
            raise WatchProtocolError("invalid timeout_seconds")
        epoch = value.get("epoch", "")
        if not isinstance(epoch, str) or len(epoch) > 80:
            raise WatchProtocolError("invalid epoch")
    if action == "snapshot":
        token = value.get("since", "")
        if not isinstance(token, str) or len(token) > 160:
            raise WatchProtocolError("invalid since")
        if not isinstance(value.get("force_full", False), bool):
            raise WatchProtocolError("invalid force_full")
    if action == "snapshot_product":
        producer_id = value.get("producer_id")
        timeout = value.get("timeout_seconds", 0.0)
        if not isinstance(producer_id, str) or not producer_id or len(producer_id) > 80:
            raise WatchProtocolError("invalid producer_id")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout <= WATCHD_MAX_WAIT_SECONDS:
            raise WatchProtocolError("invalid timeout_seconds")
    return dict(value)
