# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Sole current YO!stats writer and pre-encoded snapshot owner."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from yolomux_lib import common
from yolomux_lib.local_services.rpc import safe_socket_path
from yolomux_lib.local_services.runtime import acquire_client_lease, reap_dead_client_leases, release_client_lease
from yolomux_lib.local_services.runtime import run_local_rpc_service
from yolomux_lib.stats_current import families, identity, materializer, migration, pricing, protocol, resolution as stats_resolution, revision, storage, usage

SERVICE_NAME = "statsd"
SOCKET_FILENAME = "statsd.sock"
MAX_ID_BYTES = 512
MAX_SAFE_INTEGER = (1 << 53) - 1
DEFAULT_IDLE_SECONDS = 60.0
FULL_RECONCILE_SECONDS = 300.0
PrivateClientKey = str | None
CacheKey = tuple[int, protocol.RequestedResolution, PrivateClientKey]
DeltaKey = tuple[int, int, PrivateClientKey]
MAX_DELTA_RING_ENTRIES = 1
# Private browser views are built for clients that actually asked recently. The
# grace covers the coarsest live cadence (60s) twice over, so a hidden-then-
# revisited tab falls back to the public entry for at most one build (~1s) and
# idle clients stop multiplying every per-tick slice/encode/delta.
PRIVATE_DEMAND_GRACE_SECONDS = 120.0
UNDEMANDED_ENCODE_SECONDS = 60.0
MAX_REQUEST_TRACES = 32
MAX_USAGE_CONFLICTS = 32

FENCE_FIELDS = frozenset("action protocol_version schema_generation".split())
OBSERVATION_FIELDS = frozenset("event_id family source_id observed_at epoch_id owner_generation payload".split())
COVERAGE_FIELDS = frozenset("family source_id epoch_id started_at ended_at native_cadence_seconds owner_generation".split())
USAGE_FIELDS = frozenset("event_id direction modality cache_role unit observed_at payload".split())
USAGE_TOMBSTONE_FIELDS = frozenset(
    "event_id direction modality cache_role unit observed_at quantity provider model thread_id".split()
)
UNAVAILABLE_FIELDS = frozenset("family source_id epoch_id started_at ended_at native_cadence_seconds reason owner_generation".split())
APPEND_FIELDS = FENCE_FIELDS | frozenset(
    "observations usage_atoms usage_tombstones coverage_epochs unavailable_spans".split()
)
CONTROL_FIELDS = {
    "ping": FENCE_FIELDS,
    "status": FENCE_FIELDS,
    "lease": FENCE_FIELDS | {"client_pid", "lease_id"},
    "release": FENCE_FIELDS | {"lease_id"},
    "delta": FENCE_FIELDS | protocol.DELTA_REQUEST_FIELDS,
}
COVERAGE_FAMILIES = frozenset(spec.coverage_family for spec in families.CURRENT_FAMILIES)
BUILD_ERRORS = (OSError, sqlite3.Error, storage.StatsCurrentError, materializer.MaterializationError,
                protocol.ProtocolValidationError, TypeError, ValueError)
REQUEST_ERRORS = (TypeError, ValueError, sqlite3.Error, storage.StatsCurrentError,
                  families.FamilyValidationError, usage.UsageValidationError)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    metadata: Mapping[str, object]
    binary: bytes


@dataclass(frozen=True, slots=True)
class PublishedCache:
    generation: materializer.Generation
    entries: Mapping[CacheKey, CacheEntry]
    resolution_generations: Mapping[int, materializer.Generation]


def default_socket_path(state_dir: Path | None = None) -> Path:
    return Path(state_dir or common.STATE_DIR) / "services" / SOCKET_FILENAME


def default_database_path(state_dir: Path | None = None) -> Path:
    return Path(state_dir or common.STATE_DIR) / storage.DATABASE_FILENAME


def _json_bytes(value: protocol.SnapshotWire | protocol.DeltaWire) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _latency_status(count: int, total: float, last: float, maximum: float) -> dict[str, float]:
    return {
        "last_seconds": round(last, 6),
        "average_seconds": round(total / count, 6) if count else 0.0,
        "max_seconds": round(maximum, 6),
    }


def _object(value: object, name: str, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    if set(value) != fields:
        raise ValueError(f"{name} fields must be exactly {sorted(fields)}")
    return value


def _items(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _text(value: object, name: str) -> str:
    try:
        return identity.identity_text(value, name, maximum_bytes=MAX_ID_BYTES, strip=True)
    except identity.IdentityValidationError as error:
        raise ValueError(str(error)) from error


def _private_id(value: object, name: str) -> str:
    normalized = _text(value, name)
    return f"browser:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]}"


def _coverage_identity(item: Mapping[str, object], label: str) -> tuple[str, object, object]:
    family = _text(item["family"], f"{label}.family")
    if family not in COVERAGE_FAMILIES:
        raise ValueError(f"unknown {label} family {family!r}")
    if family != "browser":
        return family, item["source_id"], item["epoch_id"]
    return (family, _private_id(item["source_id"], f"{label}.source_id"),
            _private_id(item["epoch_id"], f"{label}.epoch_id"))


def _observation(value: object) -> storage.Observation:
    item = _object(value, "observation", OBSERVATION_FIELDS)
    family = _text(item["family"], "observation.family")
    private = family == "browser"
    return storage.Observation(
        _private_id(item["event_id"], "observation.event_id") if private else item["event_id"],
        family,
        _private_id(item["source_id"], "observation.source_id") if private else item["source_id"],
        item["observed_at"],
        _private_id(item["epoch_id"], "observation.epoch_id") if private else item["epoch_id"],
        item["owner_generation"],
        families.validate_payload(family, item["payload"]),
    )


def _coverage(value: object) -> storage.CoverageEpoch:
    item = _object(value, "coverage epoch", COVERAGE_FIELDS)
    family, source_id, epoch_id = _coverage_identity(item, "coverage")
    return storage.CoverageEpoch(
        family, source_id, epoch_id, item["started_at"], item["ended_at"],
        item["native_cadence_seconds"], item["owner_generation"],
    )


def _usage_atom(value: object) -> storage.UsageAtom:
    item = _object(value, "usage atom", USAGE_FIELDS)
    return usage.normalize_usage_atom(storage.UsageAtom(
        item["event_id"], item["direction"], item["modality"], item["cache_role"],
        item["unit"], item["observed_at"], item["payload"],
    ))


def _usage_tombstone(value: object) -> storage.UsageAtomTombstone:
    item = _object(value, "usage tombstone", USAGE_TOMBSTONE_FIELDS)
    return storage.UsageAtomTombstone(
        item["event_id"], item["direction"], item["modality"],
        item["cache_role"], item["unit"], item["observed_at"],
        item["quantity"], item["provider"], item["model"], item["thread_id"],
    )


def _unavailable(value: object) -> storage.UnavailableSpan:
    item = _object(value, "unavailable span", UNAVAILABLE_FIELDS)
    family, source_id, epoch_id = _coverage_identity(item, "unavailable")
    return storage.UnavailableSpan(
        family, source_id, epoch_id, item["started_at"], item["ended_at"],
        item["native_cadence_seconds"], item["reason"], item["owner_generation"],
    )


# Unchanged buckets are the SAME frozen objects across incremental generations
# (`_fold_or_reuse_bucket`), so their wire dicts are memoized by object identity:
# a per-second encode of a 300-bucket demanded view rebuilds only the changed
# bucket dicts instead of all of them. The strong bucket reference in the value
# makes id() reuse impossible while the entry lives; bounded oldest-half
# eviction like the other identity caches. Private merged overlays produce new
# bucket objects each build and simply miss.
_WIRE_BUCKET_CACHE: dict[int, tuple[materializer.Bucket, dict[str, object]]] = {}
_WIRE_BUCKET_CACHE_MAX = 8192


def _wire_bucket(bucket: materializer.Bucket) -> dict[str, object]:
    cached = _WIRE_BUCKET_CACHE.get(id(bucket))
    if cached is not None and cached[0] is bucket:
        return cached[1]
    value = _build_wire_bucket(bucket)
    if len(_WIRE_BUCKET_CACHE) >= _WIRE_BUCKET_CACHE_MAX:
        for stale_key in list(_WIRE_BUCKET_CACHE)[: _WIRE_BUCKET_CACHE_MAX // 2]:
            del _WIRE_BUCKET_CACHE[stale_key]
    _WIRE_BUCKET_CACHE[id(bucket)] = (bucket, value)
    return value


def _build_wire_bucket(bucket: materializer.Bucket) -> dict[str, object]:
    series = {
        item.name: {
            "value": item.value,
            "source_count": item.source_count,
            "first_timestamp": item.first_observed_at,
            "last_timestamp": item.last_observed_at,
        }
        for item in bucket.series
    }
    return {
        "start": bucket.start,
        "duration": bucket.duration,
        "series": series,
        "source": {
            "first_timestamp": bucket.first_observed_at,
            "last_timestamp": bucket.last_observed_at,
            "count": bucket.source_count,
        },
        "open": not bucket.complete,
    }


def _wire_snapshot(
    generation: materializer.Generation,
    layer: materializer.Layer,
    range_seconds: int,
    requested: protocol.RequestedResolution,
    cost_report: dict[str, object],
) -> protocol.SnapshotWire:
    spans = sorted(layer.no_data, key=lambda item: (item.family, item.source_id, item.start, item.end))
    wire: protocol.SnapshotWire = {
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "range_seconds": range_seconds,
        "requested_resolution": requested,
        "resolution_seconds": layer.resolution,
        "window_start": layer.start,
        "window_end": layer.end,
        "generated_at": generation.generated_at,
        "source_generation": generation.source_generation,
        "cache_generation": generation.cache_generation,
        "rightmost_open": bool(layer.buckets and not layer.buckets[-1].complete),
        "buckets": [_wire_bucket(bucket) for bucket in layer.buckets],
        "no_data": [{
            "family": span.family, "source_id": span.source_id, "start": span.start, "end": span.end,
            "epoch": span.epoch_id, "reason": span.reason, "source_cadence_seconds": span.native_cadence_seconds,
        } for span in spans],
        "cost_report": cost_report,
    }
    # Storage/materializer dataclasses and the builders above already enforce this
    # shape. Re-validating every server-built private variant made one CPU update
    # walk the same bounded strings millions of times before serialization.
    return wire


def _wire_no_data(item: materializer.NoData) -> dict[str, object]:
    return {
        "family": item.family,
        "source_id": item.source_id,
        "start": item.start,
        "end": item.end,
        "epoch": item.epoch_id,
        "reason": item.reason,
        "source_cadence_seconds": item.native_cadence_seconds,
    }


def _wire_delta(
    previous: materializer.Generation,
    candidate: materializer.Generation,
    range_seconds: int,
    resolution_seconds: int,
    revision_number: int,
    cost_report: dict[str, object],
    *,
    private_source_id: str | None = None,
) -> protocol.DeltaWire:
    old_layer = materializer.slice_generation(
        previous, range_seconds, resolution_seconds, private_source_id=private_source_id,
    )
    new_layer = materializer.slice_generation(
        candidate, range_seconds, resolution_seconds, private_source_id=private_source_id,
    )
    old_buckets = {(item.start, item.duration): item for item in old_layer.buckets}
    new_buckets = {(item.start, item.duration): item for item in new_layer.buckets}
    old_gaps = {
        (item.family, item.source_id, item.epoch_id, item.start, item.end): item
        for item in old_layer.no_data
    }
    new_gaps = {
        (item.family, item.source_id, item.epoch_id, item.start, item.end): item
        for item in new_layer.no_data
    }
    buckets = [
        _wire_bucket(new_buckets[key])
        for key in sorted(new_buckets)
        if old_buckets.get(key) != new_buckets[key]
    ]
    gaps = [
        _wire_no_data(new_gaps[key])
        for key in sorted(new_gaps)
        if old_gaps.get(key) != new_gaps[key]
    ]
    tombstones = [
        {"kind": "bucket", "start": key[0], "duration": key[1]}
        for key in sorted(set(old_buckets) - set(new_buckets))
    ]
    tombstones.extend({
        "kind": "no_data",
        "family": key[0],
        "source_id": key[1],
        "epoch": key[2],
        "start": key[3],
        "end": key[4],
    } for key in sorted(set(old_gaps) - set(new_gaps)))
    if not buckets and not gaps and not tombstones:
        buckets.append(_wire_bucket(new_buckets[max(new_buckets)]))
    wire: protocol.DeltaWire = {
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "range_seconds": range_seconds,
        "resolution_seconds": resolution_seconds,
        "source_generation": candidate.source_generation,
        "base_cache_generation": previous.cache_generation,
        "cache_generation": candidate.cache_generation,
        "revision": revision_number,
        "buckets": buckets,
        "no_data": gaps,
        "tombstones": tombstones,
        "cost_report": cost_report,
    }
    # Client-originated wire still crosses protocol.validate_delta; this trusted
    # server construction path shares the already-validated materialized values.
    return wire


class StatsCurrentService:
    """One listener writer plus one independent materialization worker."""

    def __init__(
        self,
        socket_path: Path,
        database_path: Path,
        *,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        store_opener: Callable[..., storage.Store] = storage.Store.open,
        reader_opener: Callable[..., storage.Store] = storage.Store.open_reader,
        full_builder: Callable[..., materializer.Generation] = materializer.build_generation,
        incremental_builder: Callable[..., materializer.Generation] = materializer.update_generation,
        encoder: Callable[[protocol.SnapshotWire | protocol.DeltaWire], bytes] = _json_bytes,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        price_resolver: materializer.PriceResolver | None = None,
        migration_runner: Callable[..., migration.MigrationReport] = migration.migrate,
    ):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-statsd")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.database_path = Path(database_path)
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.store_opener, self.reader_opener = store_opener, reader_opener
        self.full_builder, self.incremental_builder = full_builder, incremental_builder
        self.encoder, self.clock, self.monotonic = encoder, clock, monotonic
        self.price_resolver = price_resolver if price_resolver is not None else pricing.UsagePriceProjector()
        self.migration_runner = migration_runner
        self.stop_event, self.work_event, self.cache_ready_event = threading.Event(), threading.Event(), threading.Event()
        self.work_lock, self.cache_lock, self.trace_lock = threading.Lock(), threading.Lock(), threading.Lock()
        self.writer: storage.Store | None = None
        self.worker: threading.Thread | None = None
        self.leases: dict[str, int] = {}
        self.started_at, self.last_client_at = self.clock(), self.monotonic()
        self._pending_full = True
        self._pending_dirty: set[materializer.DirtyCell] = set()
        self._demand_lock = threading.Lock()
        self._private_demand: dict[str, float] = {}
        # Startup counts as public demand so warm behavior is unchanged; on a
        # box with no snapshot/delta requests, PUBLIC encoding also stops after
        # the grace (folding continues so data stays durable), and the first
        # request after idle gets the existing pending+retry, like a cold start.
        self._last_public_demand = self.monotonic()
        self._encodes_skipped_idle = 0
        # Per-view demand keyed by (range_seconds, requested_resolution): a
        # demanded view encodes at its live cadence; undemanded views refresh
        # together once per UNDEMANDED_ENCODE_SECONDS so a range/resolution
        # switch renders instantly from a slightly stale entry and catches up
        # on the next one-second build.
        self._view_demand: dict[tuple[int, object], float] = {}
        self._forced_publication_resolutions: set[int] = set()
        self._pending_coverage_refresh = False
        self._latest_source_generation = self._next_cache_generation = 0
        self._cache: PublishedCache | None = None
        self._delta_entries: dict[DeltaKey, list[CacheEntry]] = {}
        self._delta_revisions: dict[DeltaKey, int] = {}
        self._encoded_cost_reports_generation = -1
        self._encoded_cost_reports: Mapping[tuple[int, int], dict[str, object]] = MappingProxyType({})
        self._next_reconcile_at = self.monotonic() + FULL_RECONCILE_SECONDS
        self._reconciliations = 0
        self._last_reconcile_at = 0.0
        self._last_reconcile_seconds = 0.0
        self._building = False
        self._rejected_old = self._append_requests = self._snapshot_requests = 0
        self._usage_attribution_conflicts = 0
        self._usage_atoms_accepted = 0
        self._last_usage_atom_accepted_at = 0.0
        self._usage_identity_conflict_attempts = 0
        self._usage_identity_conflicts: dict[str, dict[str, object]] = {}
        self._snapshot_hits = self._snapshot_pending = self._snapshot_not_modified = 0
        self._snapshot_bytes = 0
        self._snapshot_latency_total = self._snapshot_latency_last = self._snapshot_latency_max = 0.0
        self._delta_requests = self._delta_hits = self._delta_pending = 0
        self._delta_not_modified = self._delta_repairs = self._delta_bytes = 0
        self._delta_latency_total = self._delta_latency_last = self._delta_latency_max = 0.0
        self._request_trace_sequence = 0
        self._request_traces: deque[dict[str, object]] = deque(maxlen=MAX_REQUEST_TRACES)
        self._full_builds = self._incremental_builds = self._stale_builds = self._failed_builds = 0
        # Every full build carries an explicit reason; an unlabelled periodic full
        # build is a bug (the five-minute reconcile must not schedule one).
        self._pending_full_reason = "startup"
        self._last_full_build_reason = ""
        self._last_encode_accounting: dict[str, int] = {}
        self._encode_totals = {"slices": 0, "alias_reuses": 0, "entries": 0, "bytes": 0, "bucket_visits": 0}
        self._last_build_seconds = self._last_build_at = 0.0
        self._last_full_build_seconds = self._last_incremental_build_seconds = 0.0
        self._last_source_commit_at = 0.0
        self._last_failure = ""
        self._last_failure_component = ""
        self._last_failure_at = 0.0
        self._migration_state = "pending"
        self._migration_result = ""
        self._migration_failure = ""
        self._migration_seconds = 0.0
        self._migration_counts = {
            "observations": 0,
            "coverage_epochs": 0,
            "usage_atoms": 0,
            "unavailable_spans": 0,
            "issues": 0,
        }

    def _start(self) -> None:
        started = self.monotonic()
        self._migration_state = "running"
        try:
            # run_local_rpc_service invokes _start only after winning the
            # singleton lock. Preflight therefore precedes both migration and
            # the first mutating SQLite open without racing another statsd.
            storage.require_compatible_writer(
                self.database_path,
                writer_protocol=storage.MIN_WRITER_PROTOCOL,
                writer_build=storage.MIN_WRITER_BUILD,
            )
            report = self.migration_runner(
                migration.MigrationInputs(self.database_path.parent),
                active_database=self.database_path,
                completed_at=self.clock(),
            )
            self.writer = self.store_opener(
                self.database_path,
                writer_protocol=storage.MIN_WRITER_PROTOCOL,
                writer_build=storage.MIN_WRITER_BUILD,
            )
        except (OSError, sqlite3.Error, storage.StatsCurrentError, migration.MigrationError) as error:
            self._migration_state = "failed"
            self._migration_failure = type(error).__name__[:64]
            self._migration_seconds = max(0.0, self.monotonic() - started)
            self._record_failure("migration", error)
            raise
        self._migration_state = "ready"
        self._migration_result = "existing" if report.already_active else "activated"
        self._migration_failure = ""
        self._clear_failure("migration")
        self._migration_seconds = max(0.0, self.monotonic() - started)
        self._migration_counts = {
            "observations": report.observations,
            "coverage_epochs": report.coverage_epochs,
            "usage_atoms": report.usage_atoms,
            "unavailable_spans": report.unavailable_spans,
            "issues": report.issue_count,
        }
        self.worker = threading.Thread(target=self._worker_loop, name="yolomux-stats-materializer", daemon=True)
        self.worker.start()
        self._next_reconcile_at = self.monotonic() + FULL_RECONCILE_SECONDS
        self.work_event.set()

    def _close(self) -> None:
        self.stop_event.set()
        self.work_event.set()
        if self.worker is not None:
            self.worker.join(timeout=1.0)
        if self.writer is not None:
            self.writer.close()
            self.writer = None

    def _take_work(self) -> tuple[bool, frozenset[materializer.DirtyCell]] | None:
        with self.work_lock:
            if (
                not self._pending_full
                and not self._pending_dirty
                and not self._pending_coverage_refresh
            ):
                return None
            work = (self._pending_full, frozenset(self._pending_dirty))
            self._pending_full = False
            self._pending_dirty.clear()
            self._pending_coverage_refresh = False
            return work

    def _worker_loop(self) -> None:
        try:
            reader = self.reader_opener(
                self.database_path,
                writer_protocol=storage.MIN_WRITER_PROTOCOL,
                writer_build=storage.MIN_WRITER_BUILD,
            )
        except (OSError, sqlite3.Error, storage.StatsCurrentError) as error:
            self._record_build_failure(error)
            return
        try:
            while not self.stop_event.is_set():
                self.work_event.wait()
                self.work_event.clear()
                work = self._take_work()
                if work is not None:
                    self._build_once(reader, *work)
        finally:
            reader.close()

    def _build_once(self, reader: storage.Store, full: bool,
                    dirty: frozenset[materializer.DirtyCell]) -> None:
        started = self.monotonic()
        used_full = full
        self._building = True
        try:
            with self.cache_lock:
                previous = None if self._cache is None else self._cache.generation
            used_full = full or previous is None
            dirty_intervals = None if used_full else tuple(
                (cell.start, cell.start + cell.resolution) for cell in dirty
            )
            with ExitStack() as snapshot_stack:
                with self.work_lock:
                    # Pin the SQLite WAL generation before a later append can commit;
                    # row scanning then remains independent of the durable writer.
                    read_snapshot = snapshot_stack.enter_context(
                        reader.pinned_snapshot(
                            dirty_intervals=dirty_intervals,
                            private_observation_sources=(
                                0
                                if dirty_intervals is None
                                else materializer.MAX_PRIVATE_BROWSER_CLIENTS
                            ),
                        )
                    )
                snapshot = read_snapshot()
                source_generation = snapshot.schema.source_generation
            with self.work_lock:
                self._latest_source_generation = max(self._latest_source_generation, source_generation)
            now = self.clock()
            with self.cache_lock:
                cache_generation = max(self._next_cache_generation + 1, int(now * 1_000))
                if cache_generation > MAX_SAFE_INTEGER:
                    raise ValueError("cache generation exceeds the JSON safe integer range")
                self._next_cache_generation = cache_generation
            build = self.full_builder if used_full else self.incremental_builder
            positional = (snapshot,) if build is self.full_builder else (previous, snapshot, dirty)
            candidate = build(
                *positional,
                source_generation=source_generation,
                cache_generation=cache_generation,
                generated_at=now,
                observed_until=now,
                price_resolver=self.price_resolver,
            )
            if build is self.full_builder:
                self._full_builds += 1
                self._last_full_build_reason = (
                    self._pending_full_reason if full else "cold_cache"
                )
            else:
                self._incremental_builds += 1
            resolutions = self._publication_resolutions(candidate)
            if previous is None or self._has_public_demand():
                encoded = self._encode_generation(
                    candidate,
                    resolutions=resolutions,
                    previous_generated_at=None if previous is None else previous.generated_at,
                )
            else:
                # No snapshot/delta request within the grace: publish the
                # generation (it stays the incremental base) but encode no wire
                # entries; the next request gets pending+retry and, having
                # recorded demand, the following build encodes again.
                encoded = {}
                self._encodes_skipped_idle += 1
            self._publish(candidate, encoded, resolutions=resolutions)
        except BUILD_ERRORS as error:
            self._record_build_failure(error)
        finally:
            self._building = False
            self._last_build_seconds = max(0.0, self.monotonic() - started)
            if used_full:
                self._last_full_build_seconds = self._last_build_seconds
            else:
                self._last_incremental_build_seconds = self._last_build_seconds

    def _publication_resolutions(
        self,
        candidate: materializer.Generation,
    ) -> frozenset[int]:
        with self.work_lock:
            forced = frozenset(self._forced_publication_resolutions)
            self._forced_publication_resolutions.clear()
        with self.cache_lock:
            cache = self._cache
            if cache is None:
                return frozenset(stats_resolution.RESOLUTION_CHOICES)
            published = dict(cache.resolution_generations)
        return forced | frozenset(
            resolution
            for resolution in stats_resolution.RESOLUTION_CHOICES
            if (
                resolution not in published
                or math.floor(
                    candidate.generated_at / stats_resolution.live_cadence_seconds(resolution)
                )
                > math.floor(
                    published[resolution].generated_at
                    / stats_resolution.live_cadence_seconds(resolution)
                )
            )
        )

    def _record_private_demand(self, private_source_id: str | None) -> None:
        with self._demand_lock:
            self._last_public_demand = self.monotonic()
            if private_source_id is not None:
                self._private_demand[private_source_id] = self.monotonic()

    def _has_public_demand(self) -> bool:
        with self._demand_lock:
            return self.monotonic() - self._last_public_demand <= PRIVATE_DEMAND_GRACE_SECONDS

    def _record_view_demand(self, range_seconds: int, requested: object) -> None:
        with self._demand_lock:
            if len(self._view_demand) > 4 * len(stats_resolution.RANGE_SECONDS) * len(stats_resolution.RESOLUTION_CHOICES):
                horizon = self.monotonic() - PRIVATE_DEMAND_GRACE_SECONDS
                self._view_demand = {
                    key: value for key, value in self._view_demand.items() if value >= horizon
                }
            self._view_demand[(range_seconds, requested)] = self.monotonic()

    def _view_demanded(self, range_seconds: int, requested: object) -> bool:
        horizon = self.monotonic() - PRIVATE_DEMAND_GRACE_SECONDS
        with self._demand_lock:
            return self._view_demand.get((range_seconds, requested), float("-inf")) >= horizon

    def _demanded_private_sources(self, source_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Only clients that requested within the grace get private views.

        This is a leaf lock (never held while taking another), safe from both
        the request threads and the publish path under cache_lock.
        """
        horizon = self.monotonic() - PRIVATE_DEMAND_GRACE_SECONDS
        with self._demand_lock:
            if len(self._private_demand) > materializer.MAX_PRIVATE_BROWSER_CLIENTS * 4:
                self._private_demand = {
                    key: value for key, value in self._private_demand.items() if value >= horizon
                }
            demand = self._private_demand
            return tuple(
                source_id for source_id in source_ids
                if demand.get(source_id, float("-inf")) >= horizon
            )

    def _encode_generation(
        self,
        generation: materializer.Generation,
        *,
        resolutions: frozenset[int] | None = None,
        previous_generated_at: float | None = None,
    ) -> Mapping[CacheKey, CacheEntry]:
        if len(generation.private_source_ids) > materializer.MAX_PRIVATE_BROWSER_CLIENTS:
            raise materializer.MaterializationError("private browser overlay bound exceeded")
        selected_resolutions = (
            frozenset(stats_resolution.RESOLUTION_CHOICES)
            if resolutions is None else resolutions
        )
        # Per-view demand: a demanded (range, resolution) view encodes at its
        # live cadence; undemanded views refresh together when the slow
        # boundary advances (or always on a full/first build), so a switch to
        # another view renders instantly from a <=60s-stale retained entry and
        # catches up on the next one-second build. This is what keeps a single
        # 5m/1s viewer from paying for all seventeen views every second.
        refresh_undemanded = previous_generated_at is None or (
            math.floor(generation.generated_at / UNDEMANDED_ENCODE_SECONDS)
            > math.floor(previous_generated_at / UNDEMANDED_ENCODE_SECONDS)
        )
        entries: dict[CacheKey, CacheEntry] = {}
        reports: dict[tuple[int, int], dict[str, object]] = {}
        accounting = {"slices": 0, "alias_reuses": 0, "entries": 0, "bytes": 0, "bucket_visits": 0}
        for private_source_id in (None, *self._demanded_private_sources(generation.private_source_ids)):
            for range_seconds in stats_resolution.RANGE_SECONDS:
                auto_resolution = stats_resolution.auto_resolution(range_seconds)
                for concrete_resolution in stats_resolution.explicit_resolutions(range_seconds):
                    if concrete_resolution not in selected_resolutions:
                        continue
                    if not refresh_undemanded and not (
                        self._view_demanded(range_seconds, concrete_resolution)
                        or (concrete_resolution == auto_resolution and self._view_demanded(range_seconds, stats_resolution.AUTO))
                    ):
                        continue
                    # Slice and construct ONCE per concrete resolution; AUTO is an
                    # alias of its resolved explicit twin and differs only by the
                    # echoed requested_resolution field, so the second entry reuses
                    # the same layer, cost report, and wire-dict body instead of
                    # re-slicing and rebuilding hundreds of bucket dicts (this was
                    # doubling the every-second 5m/1s encode and the minute-boundary
                    # sweep across all nine ranges).
                    layer = materializer.slice_generation(
                        generation,
                        range_seconds,
                        concrete_resolution,
                        private_source_id=private_source_id,
                    )
                    if layer.resolution != concrete_resolution:
                        raise RuntimeError("materialized resolution disagrees with the range matrix")
                    accounting["slices"] += 1
                    accounting["bucket_visits"] += len(layer.buckets)
                    report_key = (range_seconds, layer.resolution)
                    if report_key not in reports:
                        reports[report_key] = materializer.build_cost_report(layer)
                    cost_report = reports[report_key]
                    wire = _wire_snapshot(
                        generation, layer, range_seconds, concrete_resolution, cost_report,
                    )
                    requested_values: tuple[protocol.RequestedResolution, ...] = (
                        (concrete_resolution, stats_resolution.AUTO)
                        if concrete_resolution == auto_resolution
                        else (concrete_resolution,)
                    )
                    for requested in requested_values:
                        if not refresh_undemanded and not self._view_demanded(range_seconds, requested):
                            continue
                        body = wire if requested == concrete_resolution else {
                            **wire, "requested_resolution": requested,
                        }
                        if requested != concrete_resolution:
                            accounting["alias_reuses"] += 1
                        binary = self.encoder(body)
                        metadata = MappingProxyType({
                            "ok": True,
                            "content_type": "application/json",
                            "range_seconds": range_seconds,
                            "requested_resolution": requested,
                            "resolution_seconds": layer.resolution,
                            "source_generation": generation.source_generation,
                            "cache_generation": generation.cache_generation,
                            "bytes": len(binary),
                        })
                        entries[(range_seconds, requested, private_source_id)] = CacheEntry(metadata, binary)
        accounting["entries"] = len(entries)
        accounting["bytes"] = sum(len(entry.binary) for entry in entries.values())
        self._last_encode_accounting = accounting
        for key, value in accounting.items():
            self._encode_totals[key] += value
        self._encoded_cost_reports_generation = generation.cache_generation
        self._encoded_cost_reports = MappingProxyType(reports)
        return MappingProxyType(entries)

    def _publish(
        self,
        candidate: materializer.Generation,
        entries: Mapping[CacheKey, CacheEntry],
        *,
        resolutions: frozenset[int] | None = None,
    ) -> bool:
        published_resolutions = (
            frozenset(stats_resolution.RESOLUTION_CHOICES)
            if resolutions is None else resolutions
        )
        with self.work_lock:
            with self.cache_lock:
                previous_cache = self._cache
                current = None if previous_cache is None else previous_cache.generation
                try:
                    materializer.accept_generation(current, candidate)
                except materializer.StaleGenerationError:
                    stale = True
                else:
                    stale = False
                    self._append_delta_entries(
                        previous_cache, candidate, published_resolutions,
                    )
                    # Retain every previous entry the new encode did not
                    # replace: undemanded views keep serving their <=60s-stale
                    # body, and resolutions outside this publication cadence
                    # keep theirs. The key set is bounded (17 views x clients),
                    # and expired private clients still drop out below.
                    retained_entries = {
                        key: entry
                        for key, entry in (
                            () if previous_cache is None else previous_cache.entries.items()
                        )
                        if (
                            key[2] is None
                            or key[2] in self._demanded_private_sources(candidate.private_source_ids)
                        )
                    }
                    retained_entries.update(entries)
                    resolution_generations = dict(
                        {} if previous_cache is None
                        else previous_cache.resolution_generations
                    )
                    resolution_generations.update({
                        resolution: candidate for resolution in published_resolutions
                    })
                    self._cache = PublishedCache(
                        candidate,
                        MappingProxyType(retained_entries),
                        MappingProxyType(resolution_generations),
                    )
                    self._last_build_at = candidate.generated_at
                    self._clear_failure("materializer")
                    self.cache_ready_event.set()
            if stale:
                self._stale_builds += 1
                self._pending_full = True
                self._pending_full_reason = "stale_generation_repair"
        if stale:
            self.work_event.set()
        return not stale

    def _append_delta_entries(
        self,
        previous_cache: PublishedCache | None,
        candidate: materializer.Generation,
        resolutions: frozenset[int],
    ) -> None:
        if previous_cache is None:
            return
        allowed_clients: tuple[PrivateClientKey, ...] = (
            None, *self._demanded_private_sources(candidate.private_source_ids),
        )
        for key in tuple(self._delta_entries):
            if key[2] not in allowed_clients:
                del self._delta_entries[key]
                self._delta_revisions.pop(key, None)
        for range_seconds in stats_resolution.RANGE_SECONDS:
            auto_resolution = stats_resolution.auto_resolution(range_seconds)
            for resolution_seconds in stats_resolution.explicit_resolutions(range_seconds):
                if resolution_seconds not in resolutions:
                    continue
                # Per-view demand: delta entries exist for views someone is
                # actually streaming (or just snapshotted, including AUTO); an
                # undemanded view's cursor repairs through the retained
                # snapshot when it returns. This keeps a single 5m/1s viewer
                # from paying delta slicing for every view at each cadence.
                if not (
                    self._view_demanded(range_seconds, resolution_seconds)
                    or (
                        resolution_seconds == auto_resolution
                        and self._view_demanded(range_seconds, stats_resolution.AUTO)
                    )
                ):
                    continue
                previous = previous_cache.resolution_generations.get(resolution_seconds)
                if previous is None:
                    continue
                candidate_layer = materializer.slice_generation(
                    candidate, range_seconds, resolution_seconds,
                )
                cost_report = (
                    self._encoded_cost_reports.get((range_seconds, resolution_seconds))
                    if self._encoded_cost_reports_generation == candidate.cache_generation
                    else None
                )
                if cost_report is None:
                    cost_report = materializer.build_cost_report(candidate_layer)
                for private_source_id in allowed_clients:
                    key = (range_seconds, resolution_seconds, private_source_id)
                    revision_number = self._delta_revisions.get(key, 0) + 1
                    wire = _wire_delta(
                        previous,
                        candidate,
                        range_seconds,
                        resolution_seconds,
                        revision_number,
                        cost_report,
                        private_source_id=private_source_id,
                    )
                    binary = self.encoder(wire)
                    entry = CacheEntry(MappingProxyType({
                        "ok": True,
                        "content_type": "application/json",
                        "range_seconds": range_seconds,
                        "resolution_seconds": resolution_seconds,
                        "source_generation": candidate.source_generation,
                        "base_cache_generation": previous.cache_generation,
                        "cache_generation": candidate.cache_generation,
                        "revision": revision_number,
                        "bytes": len(binary),
                    }), binary)
                    ring = self._delta_entries.setdefault(key, [])
                    ring.append(entry)
                    del ring[:-MAX_DELTA_RING_ENTRIES]
                    self._delta_revisions[key] = revision_number

    def _record_build_failure(self, error: object) -> None:
        self._failed_builds += 1
        self._record_failure("materializer", error)

    def _record_failure(self, component: str, error: object) -> None:
        self._last_failure = type(error).__name__[:64]
        self._last_failure_component = component
        self._last_failure_at = self.clock()

    def _clear_failure(self, component: str) -> None:
        if self._last_failure_component == component:
            self._last_failure = ""
            self._last_failure_component = ""
            self._last_failure_at = 0.0

    def _record_request_latency(self, kind: str, started: float) -> None:
        elapsed = max(0.0, self.monotonic() - started)
        if kind == "snapshot":
            self._snapshot_latency_total += elapsed
            self._snapshot_latency_last = elapsed
            self._snapshot_latency_max = max(self._snapshot_latency_max, elapsed)
            return
        self._delta_latency_total += elapsed
        self._delta_latency_last = elapsed
        self._delta_latency_max = max(self._delta_latency_max, elapsed)

    def _record_request_trace(
        self,
        kind: str,
        *,
        range_seconds: int,
        requested_resolution: protocol.RequestedResolution,
        resolution_seconds: int,
        client_hash: str,
        result: str,
        metadata: Mapping[str, object],
    ) -> None:
        with self.work_lock:
            source_generation = self._latest_source_generation
        with self.cache_lock:
            cache_generation = 0 if self._cache is None else self._cache.generation.cache_generation
        source_generation = int(metadata.get("source_generation", source_generation))
        cache_generation = int(metadata.get("cache_generation", cache_generation))
        with self.trace_lock:
            self._request_trace_sequence += 1
            self._request_traces.append({
                "request_id": f"stats-{self._request_trace_sequence}",
                "kind": kind,
                "range_seconds": range_seconds,
                "requested_resolution": requested_resolution,
                "resolution_seconds": resolution_seconds,
                "client_hash": client_hash,
                "source_generation": source_generation,
                "cache_generation": cache_generation,
                "result": result,
                "at": self.clock(),
            })

    @staticmethod
    def _dirty_cells(observations: tuple[storage.Observation, ...],
                     atoms: tuple[storage.UsageAtom, ...],
                     tombstones: tuple[storage.UsageAtomTombstone, ...] = ()) -> set[materializer.DirtyCell]:
        dirty = set()
        observed_times = (
            *(item.observed_at for item in observations),
            *(item.observed_at for item in atoms),
            *(item.observed_at for item in tombstones),
        )
        for observed_at in observed_times:
            for resolution in stats_resolution.RESOLUTION_CHOICES:
                dirty.add(materializer.DirtyCell(resolution, math.floor(observed_at / resolution) * resolution))
        return dirty

    def _usage_identity_conflict_response(
        self,
        error: storage.UsageAtomIdentityConflict,
    ) -> dict[str, object]:
        """Record one bounded poison identity without retaining its payload."""

        now = self.clock()
        self._usage_identity_conflict_attempts += 1
        record = self._usage_identity_conflicts.get(error.identity_hash)
        if record is None:
            if len(self._usage_identity_conflicts) >= MAX_USAGE_CONFLICTS:
                oldest = min(
                    self._usage_identity_conflicts,
                    key=lambda key: float(
                        self._usage_identity_conflicts[key]["last_seen_at"]
                    ),
                )
                del self._usage_identity_conflicts[oldest]
            record = {
                "event_id": error.event_id,
                "identity_hash": error.identity_hash,
                "first_payload_hash": error.first_payload_hash,
                "attempted_payload_hash": error.attempted_payload_hash,
                "first_seen_at": now,
                "last_seen_at": now,
                "attempts": 0,
            }
            self._usage_identity_conflicts[error.identity_hash] = record
        record["attempted_payload_hash"] = error.attempted_payload_hash
        record["last_seen_at"] = now
        record["attempts"] = int(record["attempts"]) + 1
        return {
            "ok": False,
            "status": storage.USAGE_IDENTITY_CONFLICT_STATUS,
            "reason": str(error),
            "conflict": {
                key: record[key]
                for key in (
                    "event_id",
                    "identity_hash",
                    "first_payload_hash",
                    "attempted_payload_hash",
                )
            },
        }

    def _append(self, request: Mapping[str, object]) -> dict[str, object]:
        data = _object(request, "append request", APPEND_FIELDS)
        raw_groups = (
            _items(data["observations"], "observations"),
            _items(data["usage_atoms"], "usage_atoms"),
            _items(data["usage_tombstones"], "usage_tombstones"),
            _items(data["coverage_epochs"], "coverage_epochs"),
            _items(data["unavailable_spans"], "unavailable_spans"),
        )
        total = sum(len(items) for items in raw_groups)
        if total < 1 or total > protocol.MAX_APPEND_RECORDS:
            raise ValueError(f"append requires 1..{protocol.MAX_APPEND_RECORDS} records")
        observations = tuple(_observation(item) for item in raw_groups[0])
        atoms = tuple(_usage_atom(item) for item in raw_groups[1])
        tombstones = tuple(_usage_tombstone(item) for item in raw_groups[2])
        coverage = tuple(_coverage(item) for item in raw_groups[3])
        unavailable = tuple(_unavailable(item) for item in raw_groups[4])
        # A browser posting its telemetry is a live private client: appending
        # counts as demand so its private view is pre-encoded before its first
        # snapshot, while clients that stopped posting AND requesting age out of
        # the private encode/delta multiplier after the grace.
        for observation in observations:
            if observation.family == "browser":
                self._record_private_demand(observation.source_id)
        if self.writer is None:
            raise storage.StatsCurrentError("stats store is not open")
        dirty = self._dirty_cells(observations, atoms, tombstones)
        with self.work_lock:
            try:
                result = self.writer.append_batch(
                    observations=observations,
                    usage_atoms=atoms,
                    usage_tombstones=tombstones,
                    coverage_epochs=coverage,
                    unavailable_spans=unavailable,
                )
            except storage.UsageAtomIdentityConflict as error:
                self._append_requests += 1
                return self._usage_identity_conflict_response(error)
            changed = sum((result.observations_accepted, result.usage_atoms_accepted,
                           result.usage_tombstones_accepted, result.coverage_changed,
                           result.unavailable_spans_accepted))
            self._usage_atoms_accepted += result.usage_atoms_accepted
            if result.usage_atoms_accepted:
                self._last_usage_atom_accepted_at = self.clock()
            if changed:
                self._latest_source_generation = max(self._latest_source_generation, result.source_generation)
                self._last_source_commit_at = self.clock()
                self._pending_dirty.update(dirty)
                self._pending_coverage_refresh |= bool(
                    result.coverage_changed or result.unavailable_spans_accepted
                )
        if changed:
            self.work_event.set()
        self._append_requests += 1
        self._usage_attribution_conflicts += result.usage_attribution_conflicts
        duplicates = sum((
            result.observations_duplicate, result.usage_atoms_duplicate,
            result.usage_tombstones_duplicate, result.coverage_unchanged,
            result.unavailable_spans_duplicate,
        ))
        return {
            "ok": True,
            "source_generation": result.source_generation,
            "accepted": changed,
            "duplicates": duplicates,
            "counts": asdict(result),
        }

    def _snapshot(self, request: Mapping[str, object]) -> tuple[dict[str, object], bytes]:
        started = self.monotonic()
        self._snapshot_requests += 1
        try:
            allowed = FENCE_FIELDS | protocol.SNAPSHOT_REQUEST_FIELDS
            unknown = set(request) - allowed
            if unknown:
                raise ValueError(f"snapshot request has unknown fields: {sorted(unknown)}")
            params = {name: request[name] for name in protocol.SNAPSHOT_REQUEST_FIELDS if name in request}
            parsed = protocol.parse_snapshot_request(params)
            private_source_id = _private_id(parsed.client_id, "snapshot.client_id")
            self._record_private_demand(private_source_id)
            self._record_view_demand(parsed.range_seconds, parsed.resolution)

            def finish(metadata: Mapping[str, object], binary: bytes, result: str) -> tuple[dict[str, object], bytes]:
                self._record_request_trace(
                    "snapshot",
                    range_seconds=parsed.range_seconds,
                    requested_resolution=parsed.resolution,
                    resolution_seconds=int(metadata.get("resolution_seconds", parsed.resolution_seconds)),
                    client_hash=private_source_id,
                    result=result,
                    metadata=metadata,
                )
                return dict(metadata), binary

            with self.cache_lock:
                if self._cache is None:
                    entry = None
                else:
                    entry = self._cache.entries.get(
                        (parsed.range_seconds, parsed.resolution, private_source_id),
                    ) or self._cache.entries.get(
                        (parsed.range_seconds, parsed.resolution, None),
                    )
            if entry is None:
                # A cache-key miss is demand for an already-materialized layer,
                # not permission to wait for that resolution's next live cadence.
                # Re-fold only its current cell and force the next publication to
                # encode this exact view; the request still receives the bounded
                # pending response while that one worker cycle runs.
                resolution_seconds = parsed.resolution_seconds
                now = self.clock()
                with self.work_lock:
                    self._forced_publication_resolutions.add(resolution_seconds)
                    self._pending_dirty.add(materializer.DirtyCell(
                        resolution_seconds,
                        math.floor(now / resolution_seconds) * resolution_seconds,
                    ))
                self.work_event.set()
                self._snapshot_pending += 1
                return finish(protocol.pending_response(parsed, 1), b"", "pending")
            self._snapshot_hits += 1
            cache_generation = int(entry.metadata["cache_generation"])
            if parsed.since_generation is not None and cache_generation <= parsed.since_generation:
                self._snapshot_not_modified += 1
                return finish({
                    "ok": True,
                    "not_modified": True,
                    "range_seconds": parsed.range_seconds,
                    "requested_resolution": parsed.resolution,
                    "resolution_seconds": parsed.resolution_seconds,
                    "source_generation": entry.metadata["source_generation"],
                    "cache_generation": cache_generation,
                }, b"", "not_modified")
            self._snapshot_bytes += len(entry.binary)
            return finish(entry.metadata, entry.binary, "hit")
        finally:
            self._record_request_latency("snapshot", started)

    def _delta(self, request: Mapping[str, object]) -> tuple[dict[str, object], bytes]:
        started = self.monotonic()
        self._delta_requests += 1
        try:
            params = {name: request[name] for name in protocol.DELTA_REQUEST_FIELDS}
            parsed = protocol.parse_delta_request(params)
            private_source_id = _private_id(parsed.client_id, "delta.client_id")
            self._record_private_demand(private_source_id)
            self._record_view_demand(parsed.range_seconds, parsed.resolution_seconds)

            def finish(metadata: Mapping[str, object], binary: bytes, result: str) -> tuple[dict[str, object], bytes]:
                self._record_request_trace(
                    "delta",
                    range_seconds=parsed.range_seconds,
                    requested_resolution=parsed.resolution_seconds,
                    resolution_seconds=parsed.resolution_seconds,
                    client_hash=private_source_id,
                    result=result,
                    metadata=metadata,
                )
                return dict(metadata), binary

            with self.cache_lock:
                cache = self._cache
                resolution_generation = (
                    None if cache is None
                    else cache.resolution_generations.get(parsed.resolution_seconds)
                )
                selected_source = (
                    private_source_id
                    if (
                        resolution_generation is not None
                        and private_source_id in resolution_generation.private_source_ids
                    )
                    else None
                )
                entries = tuple(self._delta_entries.get(
                    (parsed.range_seconds, parsed.resolution_seconds, selected_source),
                    (),
                ))
            if cache is None or resolution_generation is None:
                self._delta_pending += 1
                return finish({
                    "status": "pending",
                    "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
                    "retry_after_seconds": 1,
                    "reason": "materialization is not ready",
                }, b"", "pending")
            current_generation = resolution_generation.cache_generation
            if parsed.after_cache_generation == current_generation:
                self._delta_not_modified += 1
                return finish({
                    "ok": True,
                    "not_modified": True,
                    "cache_generation": current_generation,
                    "source_generation": resolution_generation.source_generation,
                }, b"", "not_modified")
            entry = next((
                item
                for item in entries
                if item.metadata["base_cache_generation"] == parsed.after_cache_generation
                and (
                    parsed.after_revision == 0
                    or item.metadata["revision"] == parsed.after_revision + 1
                )
            ), None)
            if entry is None:
                self._delta_repairs += 1
                return finish({
                    "status": "repair_required",
                    "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
                    "reason": "delta cursor is outside the retained exact chain",
                    "cache_generation": current_generation,
                }, b"", "repair_required")
            self._delta_hits += 1
            self._delta_bytes += len(entry.binary)
            return finish(entry.metadata, entry.binary, "hit")
        finally:
            self._record_request_latency("delta", started)

    def _status(self) -> dict[str, object]:
        with self.work_lock:
            pending = (
                self._pending_full
                or bool(self._pending_dirty)
                or self._pending_coverage_refresh
            )
            pending_full = self._pending_full
            pending_coverage = self._pending_coverage_refresh
            dirty, latest_source = len(self._pending_dirty), self._latest_source_generation
            last_source_commit_at = self._last_source_commit_at
            usage_atoms_accepted = self._usage_atoms_accepted
            last_usage_atom_accepted_at = self._last_usage_atom_accepted_at
            usage_identity_conflict_attempts = self._usage_identity_conflict_attempts
            usage_identity_conflicts = tuple(
                dict(item)
                for item in sorted(
                    self._usage_identity_conflicts.values(),
                    key=lambda item: float(item["last_seen_at"]),
                    reverse=True,
                )
            )
        with self.cache_lock:
            cache = self._cache
            delta_keys = len(self._delta_entries)
            delta_entries = sum(len(items) for items in self._delta_entries.values())
            shared_delta_bytes = sum(
                len(item.binary)
                for key, items in self._delta_entries.items()
                if key[2] is None
                for item in items
            )
            private_delta_bytes = sum(
                len(item.binary)
                for key, items in self._delta_entries.items()
                if key[2] is not None
                for item in items
            )
            private_delta_entries = sum(
                len(items) for key, items in self._delta_entries.items() if key[2] is not None
            )
        warm_ready = 0 if cache is None else sum(key[2] is None for key in cache.entries)
        shared_snapshot_bytes = 0 if cache is None else sum(
            len(item.binary) for key, item in cache.entries.items() if key[2] is None
        )
        private_snapshot_bytes = 0 if cache is None else sum(
            len(item.binary) for key, item in cache.entries.items() if key[2] is not None
        )
        private_entries = (
            sum(key[2] is not None for key in (() if cache is None else cache.entries))
            + private_delta_entries
        )
        private_clients = 0 if cache is None else len(cache.generation.private_source_ids)
        cache_generation = 0 if cache is None else cache.generation.cache_generation
        resolution_generations = {} if cache is None else {
            f"{resolution}s": {
                "source": generation.source_generation,
                "cache": generation.cache_generation,
                "published_at": generation.generated_at,
                "cadence_seconds": stats_resolution.live_cadence_seconds(resolution),
            }
            for resolution, generation in sorted(cache.resolution_generations.items())
        }
        warm_total = sum(1 + len(stats_resolution.explicit_resolutions(value)) for value in stats_resolution.RANGE_SECONDS)
        next_reconcile_in = max(0.0, self._next_reconcile_at - self.monotonic())
        materializer_depth = int(pending_full) + dirty + int(pending_coverage)
        materializer_state = (
            "failed" if self._last_failure_component == "materializer"
            else "building" if self._building
            else "dirty" if pending
            else "ready" if cache is not None
            else "warming"
        )
        with self.trace_lock:
            request_traces = tuple(dict(item) for item in self._request_traces)
        return {
            "ok": True,
            "version": storage.MIN_WRITER_PROTOCOL,
            "schema_generation": storage.SCHEMA_VERSION,
            "code_revision": revision.CURRENT_CODE_REVISION,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "uptime_seconds": round(max(0.0, self.clock() - self.started_at), 3),
            "clients": len(self.leases),
            "source_generation": latest_source,
            "cache_generation": cache_generation,
            "service": {
                "protocol_version": storage.MIN_WRITER_PROTOCOL,
                "wire_protocol_version": protocol.WIRE_PROTOCOL_VERSION,
                "build": storage.MIN_WRITER_BUILD,
                "code_revision": revision.CURRENT_CODE_REVISION,
            },
            "schema": {
                "application_id": storage.APPLICATION_ID,
                "generation": storage.SCHEMA_VERSION,
                "minimum_writer_protocol": storage.MIN_WRITER_PROTOCOL,
                "minimum_writer_build": storage.MIN_WRITER_BUILD,
            },
            "writer": {
                "pid": os.getpid() if self.writer is not None else 0,
                "sole_writer": self.writer is not None,
                "mode": "inline",
                "last_source_commit_at": last_source_commit_at,
            },
            "generations": {
                "source": latest_source,
                "cache": cache_generation,
                "cache_matches_source": cache is not None and cache.generation.source_generation == latest_source,
                "by_resolution": resolution_generations,
            },
            "warm": {"ready": warm_ready, "total": warm_total, "percent": round(warm_ready * 100 / warm_total, 1)},
            "queue": {
                "pending": int(pending),
                "writer_depth": 0,
                "materializer_depth": materializer_depth,
                "dirty_cells": dirty,
                "coverage_refresh": pending_coverage,
                "building": self._building,
            },
            "materializer": {
                "state": materializer_state,
                "dirty_cells": dirty,
                "building": self._building,
                "failed_builds": self._failed_builds,
            },
            "cache": {
                "snapshot_entries": 0 if cache is None else len(cache.entries),
                "delta_entries": delta_entries,
                "shared_bytes": shared_snapshot_bytes + shared_delta_bytes,
                "private_clients": private_clients,
                "max_private_clients": materializer.MAX_PRIVATE_BROWSER_CLIENTS,
                "private_entries": private_entries,
                "private_bytes": private_snapshot_bytes + private_delta_bytes,
            },
            "migration": {
                "state": self._migration_state,
                "result": self._migration_result,
                "failure": self._migration_failure,
                "seconds": round(self._migration_seconds, 6),
                **self._migration_counts,
            },
            "build": {
                "full": self._full_builds,
                "incremental": self._incremental_builds,
                "stale": self._stale_builds,
                "failed": self._failed_builds,
                "last_seconds": round(self._last_build_seconds, 6),
                "last_full_seconds": round(self._last_full_build_seconds, 6),
                "last_incremental_seconds": round(self._last_incremental_build_seconds, 6),
                "last_at": self._last_build_at,
                "last_failure": self._last_failure,
                "last_full_reason": self._last_full_build_reason,
                "last_encode": dict(self._last_encode_accounting),
                "encode_totals": dict(self._encode_totals),
                "encodes_skipped_idle": self._encodes_skipped_idle,
            },
            "requests": {
                "append": self._append_requests,
                "snapshot": self._snapshot_requests,
                "hits": self._snapshot_hits,
                "pending": self._snapshot_pending,
                "rejected_old": self._rejected_old,
                "usage_attribution_conflicts": self._usage_attribution_conflicts,
            },
            "usage": {
                "accepted_atoms": usage_atoms_accepted,
                "last_accepted_at": last_usage_atom_accepted_at,
                "last_accepted_age_seconds": (
                    round(max(0.0, self.clock() - last_usage_atom_accepted_at), 3)
                    if last_usage_atom_accepted_at > 0
                    else None
                ),
                "quarantined_conflict_count": len(usage_identity_conflicts),
                "quarantined_conflict_attempts": usage_identity_conflict_attempts,
                "quarantined": usage_identity_conflicts,
            },
            "traffic": {
                "snapshot": {
                    "count": self._snapshot_requests,
                    "hits": self._snapshot_hits,
                    "pending": self._snapshot_pending,
                    "not_modified": self._snapshot_not_modified,
                    "bytes": self._snapshot_bytes,
                    **_latency_status(
                        self._snapshot_requests,
                        self._snapshot_latency_total,
                        self._snapshot_latency_last,
                        self._snapshot_latency_max,
                    ),
                },
                "delta": {
                    "count": self._delta_requests,
                    "hits": self._delta_hits,
                    "pending": self._delta_pending,
                    "not_modified": self._delta_not_modified,
                    "repair_required": self._delta_repairs,
                    "bytes": self._delta_bytes,
                    **_latency_status(
                        self._delta_requests,
                        self._delta_latency_total,
                        self._delta_latency_last,
                        self._delta_latency_max,
                    ),
                },
            },
            "request_traces": {
                "retained": len(request_traces),
                "maximum": MAX_REQUEST_TRACES,
                "items": request_traces,
            },
            "delta": {
                "keys": delta_keys,
                "entries": delta_entries,
                "max_entries_per_key": MAX_DELTA_RING_ENTRIES,
            },
            "reconciliation": {
                "interval_seconds": FULL_RECONCILE_SECONDS,
                "count": self._reconciliations,
                "last_at": self._last_reconcile_at,
                "last_seconds": round(self._last_reconcile_seconds, 6),
                "next_at": self.clock() + next_reconcile_in,
                "next_in_seconds": round(next_reconcile_in, 3),
            },
            "failure": {
                "component": self._last_failure_component,
                "kind": self._last_failure,
                "at": self._last_failure_at,
            },
        }

    def handle_with_binary(self, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        if (request.get("protocol_version"), request.get("schema_generation")) != (
            storage.MIN_WRITER_PROTOCOL, storage.SCHEMA_VERSION,
        ):
            self._rejected_old += 1
            return protocol.upgrade_required_response(
                storage.MIN_WRITER_PROTOCOL, storage.SCHEMA_VERSION, str(storage.MIN_WRITER_BUILD),
            ), b""
        action = request.get("action")
        try:
            if action in CONTROL_FIELDS:
                _object(request, f"{action} request", CONTROL_FIELDS[action])
            if action == "ping":
                return {
                    "ok": True,
                    "version": storage.MIN_WRITER_PROTOCOL,
                    "schema_generation": storage.SCHEMA_VERSION,
                    "code_revision": revision.CURRENT_CODE_REVISION,
                    "pid": os.getpid(),
                    "started_at": self.started_at,
                }, b""
            if action == "status":
                return self._status(), b""
            if action == "lease":
                return acquire_client_lease(
                    self.leases,
                    request["client_pid"],
                    request["lease_id"],
                ), b""
            if action == "release":
                return release_client_lease(self.leases, request["lease_id"]), b""
            if action == "append":
                return self._append(request), b""
            if action == "snapshot":
                return self._snapshot(request)
            if action == "delta":
                return self._delta(request)
            return protocol.unsupported_response(f"unsupported stats action {action!r}"), b""
        except protocol.UnsupportedRequest as error:
            return error.response, b""
        except REQUEST_ERRORS as error:
            return protocol.unsupported_response(str(error)), b""

    def _on_client(self) -> None:
        self.last_client_at = self.monotonic()
        self._reconcile_if_due()

    def _reconcile_if_due(self) -> bool:
        now_monotonic = self.monotonic()
        if now_monotonic < self._next_reconcile_at or self.writer is None:
            return False
        started = now_monotonic
        with self.work_lock:
            previous_source_generation = self._latest_source_generation
            prune_now = self.clock()
            result = self.writer.prune(now=prune_now)
            self._latest_source_generation = max(
                self._latest_source_generation,
                result.source_generation,
            )
            if result.source_generation > previous_source_generation:
                self._last_source_commit_at = self.clock()
            # A no-change prune schedules NO build at all: rebuilding an unchanged
            # generation burned ~18.6s of near-100% CPU every five minutes for zero
            # new information. When pruning DID remove/clip rows, every removed fact
            # is older than the retention cutoff, so the only serving cells that can
            # still contain it are the ones straddling the cutoff — mark exactly
            # those dirty (the incremental builder safely skips any that fall
            # outside a layer's window) instead of requesting a full rebuild.
            if (
                result.observations_deleted
                or result.coverage_epochs_deleted
                or result.coverage_epochs_clipped
                or result.usage_atoms_deleted
                or result.unavailable_spans_deleted
                or result.unavailable_spans_clipped
            ):
                cutoff = prune_now - storage.RETENTION_SECONDS
                self._pending_dirty.update(
                    materializer.DirtyCell(
                        resolution, math.floor(cutoff / resolution) * resolution
                    )
                    for resolution in stats_resolution.RESOLUTION_CHOICES
                )
        self._reconciliations += 1
        self._last_reconcile_at = self.clock()
        self._last_reconcile_seconds = max(0.0, self.monotonic() - started)
        self._next_reconcile_at = self.monotonic() + FULL_RECONCILE_SECONDS
        self.work_event.set()
        return True

    def _idle(self) -> bool:
        self._reconcile_if_due()
        reap_dead_client_leases(self.leases)
        with self.work_lock:
            pending = (
                self._pending_full
                or bool(self._pending_dirty)
                or self._pending_coverage_refresh
            )
        return (
            not self.leases
            and not self._building
            and not pending
            and self.monotonic() - self.last_client_at >= self.idle_seconds
        )

    def run(self) -> int:
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name=SERVICE_NAME,
            stop_event=self.stop_event,
            handle=self.handle_with_binary,
            on_idle=self._idle,
            on_client=self._on_client,
            on_start=self._start,
            on_shutdown=self._close,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux current stats service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", type=Path, default=default_socket_path())
    parser.add_argument("--database", type=Path, default=default_database_path())
    parser.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    if args.database.name != storage.DATABASE_FILENAME:
        parser.error(f"--database basename must be {storage.DATABASE_FILENAME}")
    # Every one-second build churns thousands of short-lived bucket/series
    # objects; with default thresholds the cyclic collector was ~a fifth of the
    # daemon's ACTIVE CPU (macOS sample, 2026-07-16). The steady state has no
    # reference cycles worth chasing at that rate — raise gen0 so collections
    # amortize across many builds, and move import-time objects out of every
    # scan. Full collections still run, just far less often.
    gc.freeze()
    gc.set_threshold(50_000, 20, 20)
    return StatsCurrentService(args.socket, args.database, idle_seconds=args.idle_seconds).run()


if __name__ == "__main__":
    raise SystemExit(main())
