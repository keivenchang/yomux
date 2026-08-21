# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded descriptor and revision contract shared by watchd and web."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


WATCHD_PROTOCOL_VERSION = 1
# Bumped v2 -> v3 for the bounded-scope rework: the wire stays backward-compatible
# (no protocol bump), but a daemon still running the v2 code registers the whole
# recursive workspace and silently ignores the new exclude_rules. The registry
# retires a same-protocol daemon only when this code revision differs, so this
# bump is what forces a stale v2 daemon to be shut down and respawned from the
# v3 code BEFORE any descriptor is sent, instead of relying on a manual restart.
WATCHD_CODE_REVISION = "watchd-v3"
WATCHD_SERVICE_NAME = "watchd"
WATCHD_MAX_PATHS = 256
# Daemon-wide ceiling on the native (recursive=False) registration UNION -- the exact
# set handed to ``watchfiles_watch``. WATCHD_MAX_PATHS bounds each descriptor FIELD
# independently, which does NOT bound the effective union: one valid descriptor with 256
# visible roots and 256 distinct exact-file parents already registers >512 native watches,
# and every additional descriptor/lease adds more. This is the one explicit union limit;
# an upsert that would cross it is rejected atomically (the last-good configuration,
# generation and worker are preserved) rather than silently truncated, so the count passed
# to ``watchfiles_watch`` can never exceed it.
WATCHD_MAX_NATIVE_REGISTRATIONS = 512


def watchd_failure_detail(error_code: str, response: dict) -> str:
    """Render the operator-actionable measurements a typed watchd refusal carries, if any.

    A capacity refusal is the one typed watchd failure that is about the REQUEST rather than about
    the daemon: the daemon is healthy and the union is simply too large. Its response is also the
    only one carrying numbers the operator can act on, and "a limit was exceeded" without the
    over-subscription is the same as not reporting it -- narrowing a watched root needs the size.

    This lives beside ``WATCHD_MAX_NATIVE_REGISTRATIONS`` because the daemon that raises the refusal
    owns how it reads, and every client boundary renders it the same way from one place.
    """
    if error_code != "native_capacity_exceeded":
        return ""
    requested = response.get("native_registration_paths")
    limit = response.get("native_registration_limit")
    if not isinstance(requested, int) or not isinstance(limit, int):
        return ""
    return f": {requested} native registrations exceeds limit {limit}"


WATCHD_MAX_CHANGED_PATHS = 256
WATCHD_MAX_WAIT_SECONDS = 30.0
WATCHD_DESCRIPTOR_TTL_SECONDS = 90.0
# One bounded reconciliation owner covers native event loss and descriptor transcript discovery.
# Descriptor leases are renewed independently on every bridge long-poll, so their 90-second TTL
# does not require expensive session discovery to run at the lease cadence.
WATCHD_RECONCILE_SECONDS = 300.0
WATCHD_DESCRIPTOR_RESYNC_SECONDS = WATCHD_RECONCILE_SECONDS
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
    """Validate and expand a descriptor path field, PRESERVING the lexical path.

    The path is expanded (``~``) and required to be absolute, but it is NOT resolved
    here.  Resolving at this boundary destroyed the requested lexical path before the
    shared exclusion owner could see it, so an exact file named inside an ignored
    directory (``root/.git/link -> root/src/file``) arrived already collapsed to its
    clean target and was admitted -- the fail-closed exact-file invariant was false for
    lexical ignored aliases.  Canonicalisation happens only AFTER exclusion has judged
    both forms: the effective-configuration union and the per-descriptor admission owner
    resolve admitted paths for signatures, generation matching, and native registration.
    """

    if not isinstance(value, list) or len(value) > WATCHD_MAX_PATHS:
        raise WatchProtocolError(f"invalid {field}")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.startswith("/") or len(item) > 4096 or "\x00" in item:
            raise WatchProtocolError(f"invalid {field}")
        paths.append(str(Path(item).expanduser()))
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
    # The configured-exclusion half of the one shared ExclusionPolicy (its
    # index_exclude_paths rules). ``skip_dirs`` already carries the directory-name
    # half; carrying the rules too lets the daemon compile the FULL policy through
    # the shared owner and apply it at native REGISTRATION, not just at event
    # admission, so the descriptor protocol can finally express "all configured
    # ignore paths" instead of only skip_dirs.
    exclude_rules: tuple[str, ...] = ()

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
            self.exclude_rules,
        )


@dataclass(frozen=True)
class DescriptorAdmission:
    """One descriptor's admission ownership, as root-independent DATA.

    Event admission is allow-if-any-owner: a path is admitted when at least ONE
    owning descriptor admits it under THAT descriptor's own exclusion policy, so a
    rule one tenant configured against its own root can never suppress another
    tenant's legitimate watch. Only raw strings live here; the daemon compiles the
    exclusion verdict through the one shared ``ExclusionPolicy`` owner.
    """

    visible_roots: tuple[str, ...] = ()
    exact_paths: tuple[str, ...] = ()
    skip_dirs: tuple[str, ...] = ()
    configured_roots: tuple[str, ...] = ()
    exclude_rules: tuple[str, ...] = ()


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
    exclude_rules: tuple[str, ...] = ()
    # ``watch_paths`` is the legacy within-admission set and the shallow
    # registration fallback for direct-construction callers. The typed scope
    # classes below replace the one collapsed recursive root. There is NO
    # recursive native class: every native registration is recursive=False, so no
    # single root can descend a whole subtree of inotify descriptors. Index
    # coverage of ``indexed_dirs`` comes from BFS/frontier + periodic
    # reconciliation, not from a recursive native watch.
    #   * shallow_watch_paths  -- visible Finder dirs + exact-file parents,
    #                             registered recursive=False (no descent into
    #                             ignored/internal subtrees; read-through covers
    #                             collapsed descendants);
    #   * exact_watch_paths    -- the exact settings/attention/transcript/file
    #                             paths, admitted by exact match (narrow explicit
    #                             ownership, not a whole-home recursive exception).
    watch_paths: tuple[str, ...] = ()
    shallow_watch_paths: tuple[str, ...] = ()
    exact_watch_paths: tuple[str, ...] = ()
    # Per-descriptor admission owners; event admission is allow-if-any-owner.
    descriptor_admissions: tuple[DescriptorAdmission, ...] = ()

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
            self.exclude_rules,
            self.watch_paths,
            self.shallow_watch_paths,
            self.exact_watch_paths,
            tuple(
                (
                    admission.visible_roots,
                    admission.exact_paths,
                    admission.skip_dirs,
                    admission.configured_roots,
                    admission.exclude_rules,
                )
                for admission in self.descriptor_admissions
            ),
        )

    def shallow_registration_paths(self) -> tuple[str, ...]:
        """The recursive=False registration set, falling back to the legacy field.

        Direct-construction callers (and older descriptors) that only populate
        ``watch_paths`` still register exactly those paths non-recursively; the
        scope-split producers populate ``shallow_watch_paths`` explicitly.
        """

        return self.shallow_watch_paths or self.watch_paths


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
    exclude_rules = value.get("exclude_rules", [])
    if not isinstance(exclude_rules, list) or len(exclude_rules) > WATCHD_MAX_PATHS or any(
        not isinstance(item, str) or not item.strip() or len(item) > 4096 or "\x00" in item for item in exclude_rules
    ):
        raise WatchProtocolError("invalid exclude_rules")
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
        exclude_rules=tuple(sorted({item.strip() for item in exclude_rules})),
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
