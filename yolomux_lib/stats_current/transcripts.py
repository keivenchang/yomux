# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded incremental transcript usage collection for current YO!stats."""

from __future__ import annotations

import copy
import hashlib
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import session_files
from ..atomic_file import atomic_write_text


_PREFIX_BYTES = 64 * 1024
_TAIL_BYTES = 512
_MAX_FILE_RECORDS = 512
_MAX_FAMILY_RECORDS = 256
DEFAULT_MAX_BYTES_PER_SCAN = 4 * 1024 * 1024
DEFAULT_MAX_RECORDS_PER_SCAN = 4096
LEGACY_REPAIR_MAX_BYTES_PER_SCAN = 8 * 1024 * 1024
LEGACY_REPAIR_MAX_RECORDS_PER_SCAN = 8192


@dataclass(frozen=True, slots=True)
class ScannedUsageAtom:
    """One newly parsed atom with its deterministic roster attribution."""

    atom: session_files.TranscriptUsageAtom
    tmux_key: str
    agent_kind: str


@dataclass(frozen=True, slots=True)
class TranscriptUsageScanResult:
    """New atoms plus bounded-work evidence for one collection pass."""

    items: tuple[ScannedUsageAtom, ...]
    tombstones: tuple[ScannedUsageAtom, ...]
    files_considered: int
    files_read: int
    bytes_read: int
    records_parsed: int
    resets: int
    backlog_files: int
    budget_exhausted: bool
    receipt_id: int


@dataclass(frozen=True, slots=True)
class _FamilyRecord:
    signature: tuple[object, ...]
    paths: tuple[Path, ...]
    context: dict[str, tuple[str, str, str, int]]


@dataclass(frozen=True, slots=True)
class _FamilyView:
    kind: str
    key: str
    root_source: str
    paths: tuple[Path, ...]
    context: dict[str, tuple[str, str, str, int]]
    cold_priority: int = 0


@dataclass(frozen=True, slots=True)
class _FamilySeed:
    kind: str
    key: str
    root: Path
    root_source: str
    candidates: tuple[Path, ...]
    cold_priority: int = 0


@dataclass(frozen=True, slots=True)
class _FileChoice:
    path: Path
    kind: str
    tmux_key: str
    context: tuple[str, str, str, int]
    repair_only: bool = False
    cold_priority: int = 0


@dataclass(frozen=True, slots=True)
class _Inspection:
    stat: Any
    reset: bool
    unchanged: bool


@dataclass
class _FileRecord:
    kind: str
    device: int
    inode: int
    durable_record: session_files.TranscriptScanRecord
    mtime_ns: int = 0
    last_seen_scan: int = 0

    @property
    def parser_state(self) -> session_files.ClaudeUsageAtomState | session_files.CodexUsageAtomState:
        return self.durable_record.state["usage_parser_state"]

    @property
    def offset(self) -> int:
        return max(0, int(self.durable_record.state.get("offset") or 0))

    @offset.setter
    def offset(self, value: int) -> None:
        self.durable_record.state["offset"] = max(0, int(value))

    @property
    def observed_size(self) -> int:
        return max(0, int(self.durable_record.state.get("size") or 0))

    @observed_size.setter
    def observed_size(self, value: int) -> None:
        self.durable_record.state["size"] = max(0, int(value))

    @property
    def prefix_digest(self) -> str:
        return str(self.durable_record.state.get("prefix_digest") or "")

    @prefix_digest.setter
    def prefix_digest(self, value: str) -> None:
        self.durable_record.state["prefix_digest"] = str(value or "")

    @property
    def parsed_tail(self) -> bytes:
        value = self.durable_record.state.get("parsed_tail")
        return value if isinstance(value, bytes) else b""

    @parsed_tail.setter
    def parsed_tail(self, value: bytes) -> None:
        self.durable_record.state["parsed_tail"] = bytes(value)


@dataclass
class _ScanCounters:
    files_read: int = 0
    bytes_read: int = 0
    appended_bytes: int = 0
    records_parsed: int = 0
    resets: int = 0
    budget_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class _FileCheckpoint:
    state: dict[str, Any]
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class _ScanReceipt:
    receipt_id: int
    files: dict[str, _FileCheckpoint]
    next_source: str | None
    appended_bytes: int
    legacy_repair_complete: bool
    legacy_repair_root_ids: tuple[str, ...]
    legacy_repair_status: dict[str, object]


@dataclass(frozen=True, slots=True)
class _CommittedUsage:
    """Immutable transcript status published only after a successful durable commit."""

    appended_bytes: int
    last_visible_append_at: float
    legacy_fork_repair: dict[str, object]


class StatsCurrentTranscriptUsageScanner:
    """Parse each physical transcript once, then only parse complete appended records."""

    def __init__(
        self,
        *,
        max_bytes_per_scan: int = DEFAULT_MAX_BYTES_PER_SCAN,
        max_records_per_scan: int = DEFAULT_MAX_RECORDS_PER_SCAN,
        clock: Any = time.time,
    ) -> None:
        if max_bytes_per_scan <= 0 or max_records_per_scan <= 0:
            raise ValueError("transcript scan budgets must be positive")
        self._lock = threading.RLock()
        self._files: dict[str, _FileRecord] = {}
        self._families: dict[tuple[str, str], _FamilyRecord] = {}
        self._scan_number = 0
        self._max_bytes_per_scan = int(max_bytes_per_scan)
        self._max_records_per_scan = int(max_records_per_scan)
        self._active_max_bytes_per_scan = self._max_bytes_per_scan
        self._active_max_records_per_scan = self._max_records_per_scan
        self._receipt_sequence = 0
        self._inflight: _ScanReceipt | None = None
        self._active_checkpoints: dict[str, _FileCheckpoint] | None = None
        self._next_source: str | None = None
        self._clock = clock
        self._legacy_repair_choices_cache: dict[str, _FileChoice] | None = None
        self._legacy_repair_roots_cache: tuple[str, ...] | None = None
        self._committed = _CommittedUsage(
            appended_bytes=0,
            last_visible_append_at=0.0,
            legacy_fork_repair={
                "active": False,
                "complete": False,
                "discovered_files": 0,
                "orphan_files": 0,
                "complete_files": 0,
                "remaining_files": 0,
                "remaining_bytes": 0,
                "advanced_files": 0,
                "scan_bytes": 0,
                "scan_records": 0,
                "budget_bytes": self._max_bytes_per_scan,
                "budget_records": self._max_records_per_scan,
            },
        )

    def scan(self, rows: list[Mapping[str, object]]) -> TranscriptUsageScanResult:
        """Return usage increments discovered since the previous successful pass."""

        with self._lock:
            self._rollback_inflight()
            self._scan_number += 1
            self._receipt_sequence += 1
            receipt_id = self._receipt_sequence
            checkpoints: dict[str, _FileCheckpoint] = {}
            self._active_checkpoints = checkpoints
            normalized = self._normalized_rows(rows)
            direct_sources = {
                source for _key, _kind, _path, source in normalized
            }
            views = self._family_views(normalized)
            choices = self._file_choices(views)
            repair_active, repair_choices, repair_root_ids = self._legacy_fork_repair_choices(normalized)
            normal_sources = set(choices)
            repair_sources = set(repair_choices) - normal_sources
            for source, choice in repair_choices.items():
                choices.setdefault(source, choice)
            inspections = self._inspect_choices(choices)
            if any(item.reset for item in inspections.values()):
                # A same-path truncate can rewrite session metadata without changing
                # the family candidate set. Rebuild once before attributing its atoms.
                self._families.clear()
                views = self._family_views(normalized)
                choices = self._file_choices(views)
                for source, choice in repair_choices.items():
                    choices.setdefault(source, choice)
                inspections = self._inspect_choices(choices)

            counters = _ScanCounters()
            self._active_max_bytes_per_scan = self._max_bytes_per_scan
            self._active_max_records_per_scan = self._max_records_per_scan
            if (
                repair_active
                and self._max_bytes_per_scan == DEFAULT_MAX_BYTES_PER_SCAN
                and self._max_records_per_scan == DEFAULT_MAX_RECORDS_PER_SCAN
            ):
                self._active_max_bytes_per_scan = LEGACY_REPAIR_MAX_BYTES_PER_SCAN
                self._active_max_records_per_scan = LEGACY_REPAIR_MAX_RECORDS_PER_SCAN
            scanned: list[ScannedUsageAtom] = []
            suppressed: list[ScannedUsageAtom] = []
            seen_atoms: set[tuple[str, str, str, str, str]] = set()
            seen_tombstones: set[tuple[str, str, str, str, str]] = set()
            repair_scan_bytes = 0
            repair_scan_records = 0
            ordered_sources = self._ordered_sources(
                choices,
                inspections,
                direct_sources,
            )
            proposed_next_source = self._next_source
            last_source_index: int | None = None
            try:
                for source_index, source in enumerate(ordered_sources):
                    if self._budget_spent(counters):
                        counters.budget_exhausted = self._sources_have_backlog(
                            ordered_sources[source_index:], inspections,
                        )
                        break
                    last_source_index = source_index
                    choice = choices[source]
                    inspection = inspections.get(source)
                    if inspection is None:
                        continue
                    bytes_before = counters.bytes_read
                    records_before = counters.records_parsed
                    atoms, tombstones = self._scan_choice(choice, inspection, counters)
                    if choice.repair_only:
                        repair_scan_bytes += counters.bytes_read - bytes_before
                        repair_scan_records += counters.records_parsed - records_before
                        atoms = []
                    for atom in atoms:
                        identity = (
                            atom.event_id,
                            atom.direction,
                            atom.modality,
                            atom.cache_role,
                            atom.unit,
                        )
                        if identity in seen_atoms:
                            continue
                        seen_atoms.add(identity)
                        scanned.append(ScannedUsageAtom(atom, choice.tmux_key, choice.kind))
                    for atom in tombstones:
                        identity = (
                            atom.event_id,
                            atom.direction,
                            atom.modality,
                            atom.cache_role,
                            atom.unit,
                        )
                        if identity in seen_tombstones:
                            continue
                        seen_tombstones.add(identity)
                        suppressed.append(
                            ScannedUsageAtom(atom, choice.tmux_key, choice.kind)
                        )
                    if self._budget_spent(counters):
                        counters.budget_exhausted = (
                            counters.budget_exhausted
                            or self._sources_have_backlog(
                                ordered_sources[source_index + 1:], inspections,
                            )
                        )
                        break
            except Exception:
                self._inflight = _ScanReceipt(
                    receipt_id, checkpoints, self._next_source, counters.appended_bytes,
                    False, (), dict(self._committed.legacy_fork_repair),
                )
                self._rollback_inflight()
                raise
            finally:
                self._active_checkpoints = None
                self._active_max_bytes_per_scan = self._max_bytes_per_scan
                self._active_max_records_per_scan = self._max_records_per_scan
            if last_source_index is not None and ordered_sources:
                proposed_next_source = ordered_sources[
                    (last_source_index + 1) % len(ordered_sources)
                ]
            backlog_files = self._backlog_files(choices, inspections)
            legacy_repair_complete = repair_active and all(
                source in self._files
                and source in inspections
                and self._files[source].offset >= int(inspections[source].stat.st_size)
                for source in repair_sources
            )
            current_root_ids = {
                self._legacy_fork_repair_root_id(root)
                for _key, kind, path, _source in normalized
                if kind == "codex"
                for root in [self._codex_sessions_root(path)]
                if root is not None
            }
            marker_complete = bool(current_root_ids) and current_root_ids.issubset(
                self._completed_legacy_fork_repair_roots()
            )
            complete_files = sum(
                source in self._files
                and source in inspections
                and self._files[source].offset >= int(inspections[source].stat.st_size)
                for source in repair_sources
            )
            remaining_bytes = sum(
                max(
                    0,
                    int(inspections[source].stat.st_size)
                    - (self._files[source].offset if source in self._files else 0),
                )
                for source in repair_sources
                if source in inspections
            )
            advanced_files = sum(
                source in checkpoints
                and source in self._files
                and self._files[source].offset
                > max(0, int(checkpoints[source].state.get("offset") or 0))
                for source in repair_sources
            )
            repair_status: dict[str, object] = {
                "active": repair_active,
                "complete": marker_complete or legacy_repair_complete,
                "discovered_files": len(repair_choices),
                "orphan_files": len(repair_sources),
                "complete_files": complete_files,
                "remaining_files": max(0, len(repair_sources) - complete_files),
                "remaining_bytes": remaining_bytes,
                "advanced_files": advanced_files,
                "scan_bytes": repair_scan_bytes,
                "scan_records": repair_scan_records,
                "budget_bytes": (
                    LEGACY_REPAIR_MAX_BYTES_PER_SCAN
                    if repair_active
                    and self._max_bytes_per_scan == DEFAULT_MAX_BYTES_PER_SCAN
                    and self._max_records_per_scan == DEFAULT_MAX_RECORDS_PER_SCAN
                    else self._max_bytes_per_scan
                ),
                "budget_records": (
                    LEGACY_REPAIR_MAX_RECORDS_PER_SCAN
                    if repair_active
                    and self._max_bytes_per_scan == DEFAULT_MAX_BYTES_PER_SCAN
                    and self._max_records_per_scan == DEFAULT_MAX_RECORDS_PER_SCAN
                    else self._max_records_per_scan
                ),
            }
            self._inflight = _ScanReceipt(
                receipt_id, checkpoints, proposed_next_source, counters.appended_bytes,
                legacy_repair_complete, repair_root_ids, repair_status,
            )
            return TranscriptUsageScanResult(
                tuple(scanned),
                tuple(suppressed),
                len(choices),
                counters.files_read,
                counters.bytes_read,
                counters.records_parsed,
                counters.resets,
                backlog_files,
                counters.budget_exhausted,
                receipt_id,
            )

    def commit(self, receipt_id: int) -> None:
        """Persist scanner progress only after every derived append was acknowledged."""

        with self._lock:
            receipt = self._require_receipt(receipt_id)
            for source in sorted(receipt.files):
                record = self._files.get(source)
                if record is None:
                    raise RuntimeError("transcript receipt lost its file record")
                durable = record.durable_record
                with durable.lock:
                    if not session_files.persist_transcript_scan_state(
                        durable.identity,
                        durable.state,
                        force=True,
                    ):
                        raise OSError("failed to persist transcript scan receipt")
                    durable.persisted_offset = record.offset
                    durable.persisted_at = time.perf_counter()
                    durable.state.pop("_allow_offset_rewind", None)
            self._next_source = receipt.next_source
            committed = self._committed
            appended_bytes = committed.appended_bytes
            last_visible_append_at = committed.last_visible_append_at
            if receipt.appended_bytes > 0:
                appended_bytes += receipt.appended_bytes
                last_visible_append_at = float(self._clock())
            if receipt.legacy_repair_complete:
                marker = self._legacy_fork_repair_marker()
                marker.parent.mkdir(parents=True, exist_ok=True)
                completed_roots = self._completed_legacy_fork_repair_roots()
                completed_roots.update(receipt.legacy_repair_root_ids)
                atomic_write_text(
                    marker,
                    "".join(f"{root_id}\n" for root_id in sorted(completed_roots)),
                    mode=0o600,
                )
                self._legacy_repair_choices_cache = None
                self._legacy_repair_roots_cache = None
            # One reference assignment publishes one complete generation only after every
            # durable write above succeeds. Readers never need the scanner's long-held lock.
            self._committed = _CommittedUsage(
                appended_bytes=appended_bytes,
                last_visible_append_at=last_visible_append_at,
                legacy_fork_repair=dict(receipt.legacy_repair_status),
            )
            self._inflight = None
            self._prune_records()

    def status(self) -> dict[str, object]:
        """Return bounded evidence of transcript growth that was durably committed."""

        committed = self._committed
        last_append = committed.last_visible_append_at
        now = float(self._clock())
        return {
            "committed_appended_bytes": committed.appended_bytes,
            "last_visible_append_at": last_append,
            "visible_append_age_seconds": (
                max(0.0, now - last_append) if last_append > 0 else None
            ),
            "legacy_fork_repair": dict(committed.legacy_fork_repair),
        }

    def usage_atom_backfill_status(self) -> dict[str, object] | None:
        """Return only cursor progress that has already been durably committed."""

        with self._lock:
            if self._scan_number == 0 or self._inflight is not None:
                return None
            records = tuple(self._files.values())
            missing = sum(record.offset < record.observed_size for record in records)
            return {"state": "complete" if missing == 0 else "pending", "sources": len(records), "missing": missing}

    @staticmethod
    def usage_atom_backfill_status_for_scan(
        scan: TranscriptUsageScanResult,
        *,
        atoms_accepted: int,
        rejection_reasons: Mapping[str, int],
    ) -> dict[str, object]:
        """Project one scanner result without creating a second completion state."""

        reasons = {
            str(reason): int(count)
            for reason, count in sorted(rejection_reasons.items())
            if isinstance(reason, str) and reason and isinstance(count, int) and count > 0
        }
        emitted = len(scan.items)
        rejected = sum(reasons.values())
        if atoms_accepted < 0 or atoms_accepted + rejected != emitted:
            raise ValueError("usage atom adapter counts must partition emitted atoms")
        return {
            "state": "complete" if scan.backlog_files == 0 else "pending",
            "sources": scan.files_considered,
            "missing": scan.backlog_files,
            "scan": {
                "files_read": scan.files_read,
                "records_parsed": scan.records_parsed,
                "atoms_emitted": emitted,
                "atoms_accepted": atoms_accepted,
                "atoms_rejected": rejected,
                "rejection_reasons": reasons,
            },
        }

    def rollback(self, receipt_id: int) -> None:
        """Discard unacknowledged progress so the whole receipt is replayed."""

        with self._lock:
            self._require_receipt(receipt_id)
            self._rollback_inflight()

    def _require_receipt(self, receipt_id: int) -> _ScanReceipt:
        receipt = self._inflight
        if receipt is None or receipt.receipt_id != receipt_id:
            raise RuntimeError("transcript scan receipt is not active")
        return receipt

    def _rollback_inflight(self) -> None:
        receipt = self._inflight
        if receipt is None:
            return
        for source, checkpoint in receipt.files.items():
            record = self._files.get(source)
            if record is None:
                continue
            with record.durable_record.lock:
                record.durable_record.state = copy.deepcopy(checkpoint.state)
                record.mtime_ns = checkpoint.mtime_ns
        self._inflight = None
        self._prune_records()

    def _ordered_sources(
        self,
        choices: Mapping[str, _FileChoice],
        inspections: Mapping[str, _Inspection],
        direct_sources: set[str],
    ) -> list[str]:
        """Keep live tails current, then rotate all historical work fairly.

        A brand-new direct Codex roster transcript has no durable EOF checkpoint yet, so
        its unread bytes need the same priority as a previously completed file that
        started growing. Partially consumed files keep their historical cursor position,
        and Claude's newest-project ordering remains the owner for its cold candidates.
        Backlog and repair share one cursor tier so either can resume after a bounded scan.
        """

        live: list[str] = []
        historical: list[str] = []
        for source in sorted(
            choices,
            key=lambda item: (choices[item].cold_priority, item),
        ):
            record = self._files.get(source)
            inspection = inspections.get(source)
            durable = (
                record.durable_record
                if record is not None
                else session_files.stats_current_transcript_scan_record(
                    choices[source].path,
                    choices[source].kind,
                )
            )
            with durable.lock:
                prior_eof = durable.state.get("usage_committed_eof_size")
                offset = max(0, int(durable.state.get("offset") or 0))
                observed_size = max(0, int(durable.state.get("size") or 0))
                if not isinstance(prior_eof, int) and offset > 0 and offset == observed_size:
                    prior_eof = offset
            repair_only = choices[source].repair_only
            if (
                inspection is not None
                and isinstance(prior_eof, int)
                and int(inspection.stat.st_size) > prior_eof
            ):
                live.append(source)
            elif (
                inspection is not None
                and source in direct_sources
                and not repair_only
                and choices[source].kind == "codex"
                and offset == 0
                and observed_size == 0
                and int(inspection.stat.st_size) > offset
            ):
                live.append(source)
            else:
                historical.append(source)
        return [
            *self._rotate_sources(live),
            *self._rotate_sources(historical),
        ]

    def _rotate_sources(self, sources: list[str]) -> list[str]:
        if not sources or not self._next_source:
            return sources
        try:
            start = sources.index(self._next_source)
        except ValueError:
            start = 0
        return [*sources[start:], *sources[:start]]

    def _sources_have_backlog(
        self,
        sources: list[str],
        inspections: Mapping[str, _Inspection],
    ) -> bool:
        for source in sources:
            inspection = inspections.get(source)
            if inspection is None:
                continue
            record = self._files.get(source)
            if record is None or record.offset < int(inspection.stat.st_size):
                return True
        return False

    @staticmethod
    def _normalized_rows(rows: list[Mapping[str, object]]) -> list[tuple[str, str, Path, str]]:
        normalized: list[tuple[str, str, Path, str]] = []
        for row in rows:
            kind = str(row.get("kind") or "").strip().lower()
            transcript = str(row.get("transcript") or "").strip()
            if kind not in {"claude", "codex"} or not transcript:
                continue
            path = Path(transcript).expanduser().resolve(strict=False)
            key = str(row.get("key") or "unknown").strip() or "unknown"
            normalized.append((key, kind, path, str(path)))
        return sorted(set(normalized), key=lambda item: (item[0], item[1], item[3]))

    @staticmethod
    def _path_identity(path: Path) -> tuple[str, int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return str(path), int(stat.st_dev), int(stat.st_ino)

    @staticmethod
    def _codex_sessions_root(path: Path) -> Path | None:
        return next((ancestor for ancestor in path.parents if ancestor.name == "sessions"), None)

    @staticmethod
    def _legacy_fork_repair_marker() -> Path:
        return session_files.common.STATE_DIR / (
            "stats-current-codex-fork-repair-"
            f"v{session_files._STATS_CURRENT_TRANSCRIPT_SCAN_VERSION}.done"
        )

    @staticmethod
    def _legacy_fork_repair_root_id(root: Path) -> str:
        return hashlib.sha256(str(root.resolve(strict=False)).encode("utf-8")).hexdigest()

    def _completed_legacy_fork_repair_roots(
        self,
        legacy_roots: set[Path] | None = None,
    ) -> set[str]:
        try:
            lines = self._legacy_fork_repair_marker().read_text(encoding="utf-8").splitlines()
        except OSError:
            return set()
        if lines == ["complete"] and legacy_roots is not None and len(legacy_roots) == 1:
            # Protocol 24 initially wrote one global completion word. It is an
            # unambiguous proof only when the current installation has one Codex
            # sessions root; migrate that proof now so a later second CODEX_HOME
            # is independently discovered and repaired.
            completed = {self._legacy_fork_repair_root_id(next(iter(legacy_roots)))}
            atomic_write_text(
                self._legacy_fork_repair_marker(),
                "".join(f"{root_id}\n" for root_id in sorted(completed)),
                mode=0o600,
            )
            return completed
        return {
            line
            for line in lines
            if len(line) == 64 and all(char in "0123456789abcdef" for char in line)
        }

    def _legacy_fork_repair_choices(
        self,
        rows: list[tuple[str, str, Path, str]],
    ) -> tuple[bool, dict[str, _FileChoice], tuple[str, ...]]:
        """Return the one-time orphan-fork repair backlog, never the steady hot set."""

        roots = {
            root
            for _key, kind, path, _source in rows
            if kind == "codex"
            for root in [self._codex_sessions_root(path)]
            if root is not None
        }
        completed_roots = self._completed_legacy_fork_repair_roots(roots)
        pending_roots = {
            root
            for root in roots
            if self._legacy_fork_repair_root_id(root) not in completed_roots
        }
        root_ids = tuple(sorted(
            self._legacy_fork_repair_root_id(root) for root in pending_roots
        ))
        if not pending_roots:
            return False, {}, ()
        if (
            self._legacy_repair_choices_cache is not None
            and self._legacy_repair_roots_cache == root_ids
        ):
            return True, self._legacy_repair_choices_cache, root_ids
        choices: dict[str, _FileChoice] = {}
        for root in sorted(pending_roots, key=str):
            for path in session_files.recent_codex_transcript_candidates(
                root=root,
                limit=1 << 30,
            ):
                try:
                    with path.open("rb") as handle:
                        record = session_files.transcript_json_record(
                            handle.readline().rstrip(b"\r\n")
                        )
                except OSError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "session_meta":
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                parent = str(payload.get("forked_from_id") or "").strip()[:256]
                thread_source = str(payload.get("thread_source") or "").strip().lower()
                child = str(payload.get("id") or payload.get("thread_id") or "").strip()[:256]
                if not parent or thread_source != "subagent" or not child:
                    continue
                source = str(path.expanduser().resolve(strict=False))
                choices[source] = _FileChoice(
                    Path(source),
                    "codex",
                    "legacy-codex-fork-repair",
                    (parent, child, parent, 1),
                    True,
                )
        self._legacy_repair_choices_cache = choices
        self._legacy_repair_roots_cache = root_ids
        return True, choices, root_ids

    def _family_views(self, rows: list[tuple[str, str, Path, str]]) -> list[_FamilyView]:
        codex_roots = [path for _key, kind, path, _source in rows if kind == "codex"]
        codex_candidate_groups: dict[str, tuple[Path, ...]] = {}
        for sessions_root in {self._codex_sessions_root(path) for path in codex_roots}:
            group_key = str(sessions_root) if sessions_root is not None else ""
            group_roots = [
                path for path in codex_roots
                if self._codex_sessions_root(path) == sessions_root
            ]
            discovered = (
                session_files.recent_codex_transcript_candidates(root=sessions_root)
                if sessions_root is not None
                else group_roots
            )
            unique = {path.expanduser().resolve(strict=False) for path in [*group_roots, *discovered]}
            codex_candidate_groups[group_key] = tuple(sorted(unique, key=str))

        claude_roots = [path for _key, kind, path, _source in rows if kind == "claude"]
        direct_claude_sources = {str(path) for path in claude_roots}
        claude_project_roots: dict[str, tuple[Path, ...]] = {}
        claude_priorities: dict[str, int] = {}
        for project_root in {path.parent for path in claude_roots}:
            group_roots = [path for path in claude_roots if path.parent == project_root]
            discovered = session_files.recent_claude_transcript_candidates(project_root)
            unique = {
                path.expanduser().resolve(strict=False)
                for path in [*group_roots, *discovered]
            }
            ordered = tuple(sorted(
                unique,
                key=lambda path: (session_files.path_mtime_or_zero(path), str(path)),
                reverse=True,
            ))
            claude_project_roots[str(project_root)] = ordered
            claude_priorities.update({str(path): index for index, path in enumerate(ordered)})

        seeds: list[_FamilySeed] = []
        for key, kind, root, root_source in rows:
            if kind == "claude":
                candidates = tuple(session_files.claude_transcript_family_paths(root))
                priority = claude_priorities.get(root_source, 0)
            else:
                group_key = str(self._codex_sessions_root(root) or "")
                candidates = codex_candidate_groups.get(group_key, (root,))
                priority = 0
            seeds.append(_FamilySeed(kind, key, root, root_source, candidates, priority))
        for project_roots in claude_project_roots.values():
            for root in project_roots:
                root_source = str(root)
                if root_source in direct_claude_sources:
                    continue
                seeds.append(_FamilySeed(
                    "claude",
                    self._claude_background_agent_key(root),
                    root,
                    root_source,
                    tuple(session_files.claude_transcript_family_paths(root)),
                    claude_priorities[root_source],
                ))

        views: list[_FamilyView] = []
        for seed in seeds:
            cache_key = (seed.kind, seed.root_source)
            identities = tuple(
                identity
                for identity in (self._path_identity(path) for path in seed.candidates)
                if identity is not None
            )
            signature: tuple[object, ...] = (seed.kind, seed.root_source, *identities)
            cached = self._families.get(cache_key)
            if cached is None or cached.signature != signature:
                if seed.kind == "claude":
                    family = [path.expanduser().resolve(strict=False) for path in seed.candidates]
                else:
                    family = session_files.codex_transcript_family_paths(
                        seed.root, list(seed.candidates),
                    )
                paths = tuple(dict.fromkeys(
                    path.expanduser().resolve(strict=False) for path in family
                ))
                cached = _FamilyRecord(
                    signature,
                    paths,
                    session_files.transcript_family_context(list(paths), seed.kind),
                )
                self._families[cache_key] = cached
            views.append(_FamilyView(
                seed.kind,
                seed.key,
                seed.root_source,
                cached.paths,
                cached.context,
                seed.cold_priority,
            ))
        self._prune_families()
        return views

    @staticmethod
    def _claude_background_agent_key(path: Path) -> str:
        def bounded(value: str, maximum: int) -> str:
            return value.encode("utf-8")[:maximum].decode("utf-8", errors="ignore")

        resolved = path.expanduser().resolve(strict=False)
        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
        project = bounded(resolved.parent.name, 72)
        session = bounded(resolved.stem, 72)
        return f"claude-bg:{project}:{session}:{digest}"

    @staticmethod
    def _file_choices(views: list[_FamilyView]) -> dict[str, _FileChoice]:
        candidates: dict[str, list[tuple[_FamilyView, tuple[str, str, str, int]]]] = {}
        for view in views:
            for path in view.paths:
                source = str(path)
                context = view.context.get(source, (source, source, "", 0))
                candidates.setdefault(source, []).append((view, context))

        choices: dict[str, _FileChoice] = {}
        for source, source_candidates in candidates.items():
            # Direct roster ownership determines the displayed agent. The broadest
            # discovered family independently owns root/parent/depth attribution.
            owner, _owner_context = min(
                source_candidates,
                key=lambda item: (
                    item[0].root_source != source,
                    item[0].key,
                    item[0].kind,
                    item[0].root_source,
                ),
            )
            context_owner, context = min(
                (item for item in source_candidates if item[0].kind == owner.kind),
                key=lambda item: (-len(item[0].paths), item[0].root_source, item[0].key),
            )
            choices[source] = _FileChoice(
                path=Path(source),
                kind=owner.kind,
                tmux_key=owner.key,
                context=context_owner.context.get(source, context),
                cold_priority=owner.cold_priority,
            )
        return choices

    def _inspect_choices(self, choices: dict[str, _FileChoice]) -> dict[str, _Inspection]:
        inspections: dict[str, _Inspection] = {}
        for source, choice in choices.items():
            try:
                stat = choice.path.stat()
            except OSError:
                continue
            record = self._files.get(source)
            if record is None:
                inspections[source] = _Inspection(stat, False, False)
                continue
            record.last_seen_scan = self._scan_number
            identity_changed = (
                record.kind != choice.kind
                or record.device != int(stat.st_dev)
                or record.inode != int(stat.st_ino)
            )
            truncated = int(stat.st_size) < record.offset
            unchanged = (
                not identity_changed
                and not truncated
                and record.offset == int(stat.st_size)
                and int(stat.st_size) == record.observed_size
                and int(stat.st_mtime_ns) == record.mtime_ns
            )
            reset = identity_changed or truncated
            if not unchanged and not reset:
                reset = self._content_before_offset_changed(choice.path, record)
            inspections[source] = _Inspection(stat, reset, unchanged)
        return inspections

    @staticmethod
    def _prefix_digest(path: Path) -> str:
        try:
            with path.open("rb") as handle:
                return hashlib.sha256(handle.readline(_PREFIX_BYTES)).hexdigest()
        except OSError:
            return ""

    @classmethod
    def _content_before_offset_changed(cls, path: Path, record: _FileRecord) -> bool:
        if record.prefix_digest and cls._prefix_digest(path) != record.prefix_digest:
            return True
        if record.offset <= 0 or not record.parsed_tail:
            return False
        start = max(0, record.offset - len(record.parsed_tail))
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                current_tail = handle.read(record.offset - start)
        except OSError:
            return True
        return current_tail != record.parsed_tail[-len(current_tail):]

    def _new_file_record(self, choice: _FileChoice, inspection: _Inspection) -> _FileRecord:
        durable_record = session_files.stats_current_transcript_scan_record(choice.path, choice.kind)
        return _FileRecord(
            choice.kind,
            int(inspection.stat.st_dev),
            int(inspection.stat.st_ino),
            durable_record,
            last_seen_scan=self._scan_number,
        )

    def _scan_choice(
        self,
        choice: _FileChoice,
        inspection: _Inspection,
        counters: _ScanCounters,
    ) -> tuple[
        list[session_files.TranscriptUsageAtom],
        list[session_files.TranscriptUsageAtom],
    ]:
        source = str(choice.path)
        record = self._files.get(source)
        if record is None or inspection.reset:
            if record is not None:
                counters.resets += 1
            record = self._new_file_record(choice, inspection)
            self._files[source] = record
        else:
            record.last_seen_scan = self._scan_number
        with record.durable_record.lock:
            return self._scan_file(choice, inspection, counters, record)

    def _scan_file(
        self,
        choice: _FileChoice,
        inspection: _Inspection,
        counters: _ScanCounters,
        record: _FileRecord,
    ) -> tuple[
        list[session_files.TranscriptUsageAtom],
        list[session_files.TranscriptUsageAtom],
    ]:
        if inspection.unchanged:
            return [], []

        checkpoints = self._active_checkpoints
        if checkpoints is None:
            raise RuntimeError("transcript scan has no active receipt")
        source = str(choice.path)
        checkpoints.setdefault(
            source,
            _FileCheckpoint(
                copy.deepcopy(record.durable_record.state),
                record.mtime_ns,
            ),
        )

        current_size = int(inspection.stat.st_size)
        previous_offset = record.offset
        previous_observed_size = record.observed_size
        prior_eof_size = record.durable_record.state.get("usage_committed_eof_size")
        if not isinstance(prior_eof_size, int) and previous_offset > 0 and previous_offset == previous_observed_size:
            prior_eof_size = previous_offset
            record.durable_record.state["usage_committed_eof_size"] = prior_eof_size
        if self._budget_spent(counters):
            counters.budget_exhausted = record.offset < current_size
            return [], []
        if (
            choice.repair_only
            and record.offset > 0
            and isinstance(record.parser_state, session_files.CodexUsageAtomState)
            and not record.parser_state.suppress_fork_history
            and self._finish_repair_suffix(choice, inspection, counters, record)
        ):
            return [], []
        if record.offset < current_size and record.offset > 0 and isinstance(record.parser_state, session_files.CodexUsageAtomState):
            session_files.resume_codex_usage_atom_state(record.parser_state)
        atoms: list[session_files.TranscriptUsageAtom] = []
        suppressed: list[session_files.TranscriptUsageAtom] = []
        consumed = 0
        read_bytes = 0
        try:
            with choice.path.open("rb") as handle:
                handle.seek(record.offset)
                while record.offset + read_bytes < current_size:
                    if self._budget_spent(counters, pending_bytes=read_bytes):
                        counters.budget_exhausted = True
                        break
                    remaining = current_size - record.offset - read_bytes
                    raw_line = handle.readline(remaining)
                    if not raw_line:
                        break
                    read_bytes += len(raw_line)
                    if not (raw_line.endswith(b"\n") or raw_line.endswith(b"\r")):
                        break
                    consumed += len(raw_line)
                    record.parsed_tail = (record.parsed_tail + raw_line)[-_TAIL_BYTES:]
                    parsed = session_files.transcript_json_record(raw_line.rstrip(b"\r\n"))
                    if parsed is None:
                        continue
                    counters.records_parsed += 1
                    root, agent, parent, depth = choice.context
                    if choice.kind == "claude":
                        if not isinstance(record.parser_state, session_files.ClaudeUsageAtomState):
                            raise RuntimeError("Claude transcript has Codex parser state")
                        atoms.extend(session_files.claude_usage_atoms_from_record(
                            parsed,
                            record.parser_state,
                            source=source,
                            root_thread_id=root,
                            agent_thread_id=agent,
                            parent_thread_id=parent,
                            depth=depth,
                        ))
                    else:
                        if not isinstance(record.parser_state, session_files.CodexUsageAtomState):
                            raise RuntimeError("Codex transcript has Claude parser state")
                        atoms.extend(session_files.codex_usage_atoms_from_record(
                            parsed,
                            record.parser_state,
                            source=source,
                            root_thread_id=root,
                            agent_thread_id=agent,
                            parent_thread_id=parent,
                            depth=depth,
                            suppressed_atoms=suppressed,
                        ))
                        if (
                            choice.repair_only
                            and parsed.get("type")
                            == "inter_agent_communication_metadata"
                        ):
                            # This one-time path emits only cloned-prefix deletion
                            # proofs. The explicit handoff ends that prefix, so a
                            # large genuine child suffix must stay untouched and
                            # need not be reread as migration input.
                            consumed = current_size - record.offset
                            handle.seek(max(0, current_size - _TAIL_BYTES))
                            record.parsed_tail = handle.read(_TAIL_BYTES)
                            break
        except OSError:
            return [], []
        counters.files_read += 1
        counters.bytes_read += read_bytes
        if (
            not inspection.reset
            and isinstance(prior_eof_size, int)
            and current_size > prior_eof_size
            and previous_offset >= prior_eof_size
        ):
            counters.appended_bytes += max(
                0,
                min(previous_offset + consumed, current_size)
                - max(previous_offset, prior_eof_size),
            )
        record.offset += consumed
        record.observed_size = current_size
        if record.offset >= current_size:
            record.durable_record.state["usage_committed_eof_size"] = current_size
        record.mtime_ns = int(inspection.stat.st_mtime_ns)
        if not record.prefix_digest:
            record.prefix_digest = self._prefix_digest(choice.path)
        return atoms, suppressed

    def _finish_repair_suffix(
        self,
        choice: _FileChoice,
        inspection: _Inspection,
        counters: _ScanCounters,
        record: _FileRecord,
    ) -> bool:
        """Advance a previously handed-off repair-only fork without parsing its child work."""

        current_size = int(inspection.stat.st_size)
        try:
            with choice.path.open("rb") as handle:
                handle.seek(max(0, current_size - _TAIL_BYTES))
                tail = handle.read(_TAIL_BYTES)
        except OSError:
            return False
        counters.files_read += 1
        counters.bytes_read += len(tail)
        record.offset = current_size
        record.observed_size = current_size
        record.durable_record.state["usage_committed_eof_size"] = current_size
        record.parsed_tail = tail
        record.mtime_ns = int(inspection.stat.st_mtime_ns)
        if not record.prefix_digest:
            record.prefix_digest = self._prefix_digest(choice.path)
        return True

    def _budget_spent(self, counters: _ScanCounters, *, pending_bytes: int = 0) -> bool:
        return (
            counters.records_parsed >= self._active_max_records_per_scan
            or counters.bytes_read + pending_bytes >= self._active_max_bytes_per_scan
        )

    def _backlog_files(
        self,
        choices: dict[str, _FileChoice],
        inspections: dict[str, _Inspection],
    ) -> int:
        backlog = 0
        for source in choices:
            inspection = inspections.get(source)
            record = self._files.get(source)
            if inspection is not None and (record is None or record.offset < int(inspection.stat.st_size)):
                backlog += 1
        return backlog

    def _prune_records(self) -> None:
        if len(self._files) <= _MAX_FILE_RECORDS:
            return
        oldest = sorted(
            self._files,
            key=lambda source: (self._files[source].last_seen_scan, source),
        )
        for source in oldest[:len(self._files) - _MAX_FILE_RECORDS]:
            self._files.pop(source, None)

    def _prune_families(self) -> None:
        while len(self._families) > _MAX_FAMILY_RECORDS:
            oldest = next(iter(self._families))
            self._families.pop(oldest, None)
