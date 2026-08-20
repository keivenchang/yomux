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
import random
import sqlite3
import threading
import time
from bisect import bisect_left
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType

from yolomux_lib import common
from yolomux_lib.control import send_yolomux_control_request
from yolomux_lib.local_services.rpc import safe_socket_path
from yolomux_lib.local_services.command_router import LocalServiceCommandRouter
from yolomux_lib.local_services.runtime import acquire_client_lease, reap_dead_client_leases, release_client_lease
from yolomux_lib.local_services.runtime import run_local_rpc_service
from yolomux_lib.settings import stats_prune_local_time
from yolomux_lib.stats_current import collectors, families, host_collectors, identity, materializer, migration, observations, pricing, protocol, prune_schedule, resolution as stats_resolution, revision, storage, usage

SERVICE_NAME = "statsd"
SOCKET_FILENAME = storage.SOCKET_FILENAME
MAX_ID_BYTES = 512
MAX_SAFE_INTEGER = (1 << 53) - 1
DEFAULT_IDLE_SECONDS = 60.0
# The configured local time owns the daily maintenance occurrence, while this
# interval also bounds physical retention drift between daily runs. The worker
# owns this deadline independently of listener traffic; an accept-timeout hook
# can starve forever on a continuously readable socket.
PRUNE_CHECK_SECONDS = 60.0
# Ten seconds keeps the 10-second views at most one bucket behind durable ingest.
# A 60-second writer cadence would make that view trail by as many as six buckets.
RING_FLUSH_SECONDS = 10.0
BROWSER_FAILURE_LOG_MAX_BYTES = 1 * 1024 * 1024
# One owner, in host_collectors, beside the sampler that sets it. See its comment there.
HOST_CPU_CADENCE_SECONDS = host_collectors.HOST_CPU_CADENCE_SECONDS
HOST_GPU_CADENCE_SECONDS = 10.0
# VACUUM rewrites the SQLite file, so it is intentionally maintenance rather
# than part of startup, ingest, or request handling. A small per-daemon jitter
# stops several local statsd instances from choosing the same hourly moment.
VACUUM_INTERVAL_SECONDS = 60.0 * 60.0
VACUUM_JITTER_SECONDS = 10.0 * 60.0
VACUUM_RETRY_SECONDS = 5.0 * 60.0
# A rewrite holds work_lock through the whole SQLite compaction, so on a busy box
# it is deferred to a quiet window (no RPC within idle_seconds) to keep the serial
# listener from blocking live requests past the append deadline. But a permanently
# busy box would then never reclaim the pruned free-list, so once compaction has
# been owed longer than this cap it runs anyway and accepts the brief stall.
VACUUM_MAX_DEFER_SECONDS = 60.0 * 60.0
PrivateClientKey = str | None
CacheKey = tuple[int, protocol.RequestedResolution, PrivateClientKey]
DeltaKey = tuple[int, int, PrivateClientKey]
RingCursor = tuple[int, int]
DELTA_RPC_BUDGET_SECONDS = 3.0
# Warm materialization and persisted-ring publication are independently phased
# owners of the same public cursor. Retain every transition either owner can
# publish over one client poll plus its RPC budget.
DELTA_RING_ENTRY_BOUNDS = {
    resolution_seconds: sum(
        math.ceil(
            (
                stats_resolution.live_cadence_seconds(resolution_seconds)
                + DELTA_RPC_BUDGET_SECONDS
            ) / publication_interval
        )
        for publication_interval in (
            stats_resolution.live_cadence_seconds(resolution_seconds),
            RING_FLUSH_SECONDS,
        )
    )
    for resolution_seconds in stats_resolution.RESOLUTION_CHOICES
}
MAX_DELTA_RING_ENTRIES = max(DELTA_RING_ENTRY_BOUNDS.values())
# Private browser views are built for clients that actually asked recently. The
# grace covers the coarsest live cadence (60s) twice over, so a hidden-then-
# revisited tab falls back to the public entry for at most one build (~1s) and
# idle clients stop multiplying every per-tick slice/encode/delta.
PRIVATE_DEMAND_GRACE_SECONDS = 120.0
UNDEMANDED_ENCODE_SECONDS = 60.0
MAX_REQUEST_TRACES = 32
MAX_BROWSER_PROFILES = 128
MAX_BROWSER_QUEUE_EXEMPLARS = 8
MAX_BROWSER_QUEUE_DIMENSIONS = 16
BROWSER_QUEUE_HISTOGRAM_BOUNDS_MS = (25, 100, 250, 1_000, 3_000, 10_000)
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
    "browser_profiles": FENCE_FIELDS,
    "browser_upload": FENCE_FIELDS | {"authenticated_username"},
    "lease": FENCE_FIELDS | {"client_pid", "lease_id"},
    "release": FENCE_FIELDS | {"lease_id"},
    # The elected web owner may identify the process it owns, but statsd must
    # resolve roster, paths, pricing, and all measured facts itself.
    "collector_context": FENCE_FIELDS | {"pid", "port", "owner_generation", "control_socket"},
    "usage_atom_backfill": FENCE_FIELDS | {"state", "sources", "missing", "scan"},
    "delta": FENCE_FIELDS | protocol.DELTA_REQUEST_FIELDS,
}
STATS_COMMAND_ACTIONS = frozenset((*CONTROL_FIELDS, "append", "snapshot"))
STATS_COMMAND_ROUTER = LocalServiceCommandRouter({action: f"_handle_{action}" for action in STATS_COMMAND_ACTIONS})
COVERAGE_FAMILIES = frozenset(spec.coverage_family for spec in families.CURRENT_FAMILIES)
BUILD_ERRORS = (OSError, sqlite3.Error, storage.StatsCurrentError, materializer.MaterializationError,
                protocol.ProtocolValidationError, TypeError, ValueError)
REQUEST_ERRORS = (TypeError, ValueError, sqlite3.Error, storage.StatsCurrentError,
                  families.FamilyValidationError, usage.UsageValidationError)
RING_READ_ERRORS = (
    KeyError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
    sqlite3.Error,
    storage.StatsCurrentError,
    materializer.MaterializationError,
    protocol.ProtocolValidationError,
)
RING_BUCKET_PAYLOAD_VERSION = 1
RING_BUCKET_PAYLOAD_FIELDS = frozenset(
    "version generated_at cache_generation bucket no_data cost_detail_json view".split()
)
RING_VIEW_FIELDS = frozenset("range_seconds window_end cost_report".split())
RING_COST_DETAIL_FIELDS = frozenset(
    "dimensions priced unpriced models agents evidence omitted_models omitted_agents omitted_evidence".split()
)
RING_COST_DIMENSION_FIELDS = frozenset(
    "dimension tokens micro_usd api_list_micro_usd".split()
)
RING_COST_COVERAGE_FIELDS = frozenset("atoms tokens".split())
RING_COST_ATTRIBUTION_FIELDS = frozenset(
    "key provider model source label dimensions priced unpriced".split()
)
RING_COST_EVIDENCE_FIELDS = frozenset(
    "key provider model dimension direction modality cache_role unit pricing_profile "
    "service_tier catalog_model rate_usd rate_scale effective_from source_kind source_url "
    "catalog_revision tokens micro_usd api_list_micro_usd priced_atoms".split()
)


def _browser_queue_percentile(values: tuple[float, ...], quantile: float) -> float:
    index = max(0, min(len(values) - 1, math.ceil(quantile * len(values)) - 1))
    return round(values[index], 3)


def _browser_queue_summary(items: tuple[dict[str, object], ...]) -> dict[str, object]:
    rows = tuple(
        (float(item["queue_ms"]), item)
        for item in items
        if isinstance(item.get("queue_ms"), (int, float))
        and not isinstance(item.get("queue_ms"), bool)
        and math.isfinite(float(item["queue_ms"]))
        and float(item["queue_ms"]) >= 0
    )
    values = tuple(sorted(value for value, _item in rows))
    if not values:
        return {
            "count": 0,
            "average_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "maximum_ms": 0.0,
            "histogram": tuple(
                {"upper_bound_ms": bound, "count": 0}
                for bound in (*BROWSER_QUEUE_HISTOGRAM_BOUNDS_MS, None)
            ),
            "dimensions": (),
            "slow_exemplars": (),
        }
    previous_bound: float | None = None
    histogram = []
    for bound in BROWSER_QUEUE_HISTOGRAM_BOUNDS_MS:
        histogram.append({
            "upper_bound_ms": bound,
            "count": sum(
                value <= bound and (previous_bound is None or value > previous_bound)
                for value in values
            ),
        })
        previous_bound = float(bound)
    histogram.append({
        "upper_bound_ms": None,
        "count": sum(value > BROWSER_QUEUE_HISTOGRAM_BOUNDS_MS[-1] for value in values),
    })
    grouped: dict[tuple[str, str], list[float]] = {}
    for value, item in rows:
        dimension = (
            str(item.get("code_revision") or "unknown")[:80],
            str(item.get("browser_family") or "other")[:16],
        )
        grouped.setdefault(dimension, []).append(value)
    dimensions = tuple(
        {
            "code_revision": code_revision,
            "browser_family": browser_family,
            "count": len(group_values),
            "average_ms": round(sum(group_values) / len(group_values), 3),
            "maximum_ms": round(max(group_values), 3),
        }
        for (code_revision, browser_family), group_values in sorted(
            grouped.items(), key=lambda entry: (-max(entry[1]), entry[0]),
        )[:MAX_BROWSER_QUEUE_DIMENSIONS]
    )
    exemplar_fields = (
        "endpoint", "request_id", "journey_id", "code_revision", "browser_family",
        "connection_protocol",
    )
    exemplars = []
    for queue_ms, item in sorted(rows, key=lambda row: (-row[0], -float(row[1]["observed_at"])))[:MAX_BROWSER_QUEUE_EXEMPLARS]:
        exemplar = {
            "observed_at": float(item["observed_at"]),
            "queue_ms": round(queue_ms, 3),
        }
        latency_ms = item.get("latency_ms")
        if isinstance(latency_ms, (int, float)) and not isinstance(latency_ms, bool):
            exemplar["latency_ms"] = round(float(latency_ms), 3)
        exemplar.update({field: item[field] for field in exemplar_fields if item.get(field)})
        exemplars.append(exemplar)
    return {
        "count": len(values),
        "average_ms": round(sum(values) / len(values), 3),
        "p50_ms": _browser_queue_percentile(values, 0.50),
        "p95_ms": _browser_queue_percentile(values, 0.95),
        "p99_ms": _browser_queue_percentile(values, 0.99),
        "maximum_ms": round(values[-1], 3),
        "histogram": tuple(histogram),
        "dimensions": dimensions,
        "slow_exemplars": tuple(exemplars),
    }


def _bounded_migration_issue(issue: migration.MigrationIssue) -> dict[str, str]:
    value = issue.to_json()
    return {
        "kind": str(value.get("kind") or "")[:80],
        "source": str(value.get("source") or "")[:256],
        "detail": str(value.get("detail") or "")[:256],
    }


@dataclass(frozen=True, slots=True)
class CacheEntry:
    metadata: Mapping[str, object]
    binary: bytes


@dataclass(frozen=True, slots=True)
class DecoratedSnapshotBody:
    base: bytes
    base_digest: bytes
    status_signature: tuple[object, ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class RingViewState:
    snapshot: CacheEntry | None = None
    base: CacheEntry | None = None
    deltas: tuple[CacheEntry, ...] = ()
    revision: int = 0
    persisted: bool = False


@dataclass(frozen=True, slots=True)
class PublishedCache:
    generation: materializer.Generation
    entries: Mapping[CacheKey, CacheEntry]
    resolution_generations: Mapping[int, materializer.Generation]
    entry_generations: Mapping[CacheKey, materializer.Generation]


@dataclass(frozen=True, slots=True)
class DecodedRingBucket:
    wire: dict[str, object]
    no_data: tuple[dict[str, object], ...]
    cost_detail_json: str
    view: dict[str, object] | None
    cache_generation: int
    generated_at: float
    ring_generation: int


@dataclass(frozen=True, slots=True)
class RingSnapshotRead:
    entry: CacheEntry | None
    fallback_reason: str


@dataclass(frozen=True, slots=True)
class PublishedSnapshotOwner:
    cache_present: bool
    entry: CacheEntry | None
    ring_current: bool
    public: bool
    ring_cursor: RingCursor | None


def default_socket_path(state_dir: Path | None = None) -> Path:
    return storage.default_socket_path(state_dir)


def default_database_path(state_dir: Path | None = None) -> Path:
    return storage.default_database_path(state_dir)


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


def _ring_bucket_no_data(
    layer: materializer.Layer,
    bucket: materializer.Bucket,
) -> tuple[materializer.NoData, ...]:
    start, end = bucket.start, bucket.start + bucket.duration
    return tuple(
        replace(
            item,
            start=max(item.start, start),
            end=min(item.end, end),
        )
        for item in layer.no_data
        if item.end > start and item.start < end
    )


def _ring_no_data_by_bucket(
    layer: materializer.Layer,
) -> dict[int, tuple[materializer.NoData, ...]]:
    """Clip each no-data span into its overlapping ring buckets in one pass."""

    return _ring_no_data_for_cells(
        layer,
        frozenset(bucket.start for bucket in layer.buckets),
    )


def _ring_no_data_cell_starts(
    layer: materializer.Layer,
    item: materializer.NoData,
) -> range:
    """Return aligned cells where one no-data span can affect ring bytes."""

    first = max(0, math.floor((item.start - layer.start) / layer.resolution))
    last = min(
        len(layer.buckets),
        math.ceil((item.end - layer.start) / layer.resolution),
    )
    return range(
        layer.start + first * layer.resolution,
        layer.start + last * layer.resolution,
        layer.resolution,
    )


def _ring_no_data_for_cells(
    layer: materializer.Layer,
    starts: frozenset[int] | set[int],
) -> dict[int, tuple[materializer.NoData, ...]]:
    """Materialize no-data fragments only for selected cells in this layer."""

    if not layer.buckets or not layer.no_data or not starts:
        return {}
    bucket_count = len(layer.buckets)
    resolution = layer.resolution
    selected_starts = tuple(sorted(
        start for start in starts
        if layer.start <= start < layer.end and (start - layer.start) % resolution == 0
    ))
    if not selected_starts:
        return {}
    indexed: dict[int, list[materializer.NoData]] = {}
    for item in layer.no_data:
        first = max(0, math.floor((item.start - layer.start) / resolution))
        last = min(bucket_count, math.ceil((item.end - layer.start) / resolution))
        first_start = layer.start + first * resolution
        last_start = layer.start + last * resolution
        selected_first = bisect_left(selected_starts, first_start)
        selected_last = bisect_left(selected_starts, last_start)
        for start in selected_starts[selected_first:selected_last]:
            end = start + resolution
            if item.end <= start or item.start >= end:
                continue
            indexed.setdefault(start, []).append(replace(
                item,
                start=max(item.start, start),
                end=min(item.end, end),
            ))
    return {start: tuple(items) for start, items in indexed.items()}


def _ring_no_data_signatures_for_cells(
    layer: materializer.Layer,
    starts: frozenset[int] | set[int],
) -> dict[int, tuple[tuple[object, ...], ...]]:
    """Compare selected no-data cells without constructing ring fragments."""

    selected_starts = tuple(sorted(
        start for start in starts
        if layer.start <= start < layer.end
        and (start - layer.start) % layer.resolution == 0
    ))
    if not selected_starts:
        return {}
    signatures: dict[int, list[tuple[object, ...]]] = {}
    for item in layer.no_data:
        item_starts = _ring_no_data_cell_starts(layer, item)
        selected_first = bisect_left(selected_starts, item_starts.start)
        selected_last = bisect_left(selected_starts, item_starts.stop)
        for start in selected_starts[selected_first:selected_last]:
            end = start + layer.resolution
            if item.end <= start or item.start >= end:
                continue
            signatures.setdefault(start, []).append((
                item.family,
                item.source_id,
                item.epoch_id,
                max(item.start, start),
                min(item.end, end),
                item.native_cadence_seconds,
                item.reason,
            ))
    return {start: tuple(items) for start, items in signatures.items()}


def _changed_ring_no_data_starts(
    previous: materializer.Layer,
    candidate: materializer.Layer,
) -> frozenset[int]:
    """Find exact changed no-data cells without folding unchanged fragments."""

    if previous.no_data == candidate.no_data:
        return frozenset()
    changed_items = set(previous.no_data).symmetric_difference(candidate.no_data)
    potential = {
        start
        for item in changed_items
        for start in _ring_no_data_cell_starts(candidate, item)
    }
    previous_signatures = _ring_no_data_signatures_for_cells(previous, potential)
    candidate_signatures = _ring_no_data_signatures_for_cells(candidate, potential)
    return frozenset(
        start for start in potential
        if previous_signatures.get(start, ()) != candidate_signatures.get(start, ())
    )


def _ring_cost_detail_json(detail: materializer.BucketCostDetail) -> str:
    return json.dumps(
        asdict(detail),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _ring_bucket_payload(
    generation: materializer.Generation,
    layer: materializer.Layer,
    bucket: materializer.Bucket,
    bucket_no_data: tuple[materializer.NoData, ...] | None = None,
) -> dict[str, object]:
    no_data = (
        _ring_bucket_no_data(layer, bucket)
        if bucket_no_data is None else bucket_no_data
    )
    return {
        "version": RING_BUCKET_PAYLOAD_VERSION,
        "generated_at": generation.generated_at,
        "cache_generation": generation.cache_generation,
        "bucket": _wire_bucket(bucket),
        "no_data": [
            _wire_no_data(item)
            for item in no_data
        ],
        # Keep cold/downtime reconstruction self-contained without making every
        # healthy request recursively decode the full attribution tree.
        "cost_detail_json": _ring_cost_detail_json(bucket.cost_detail),
        "view": _ring_bucket_view(generation, layer, bucket),
    }


def _ring_view_carriers(layer: materializer.Layer) -> tuple[tuple[int, int], ...]:
    ranges = tuple(
        range_seconds
        for range_seconds in stats_resolution.RANGE_SECONDS
        if stats_resolution.is_supported(range_seconds, layer.resolution)
    )
    return tuple(
        (range_seconds, layer.end - (index + 1) * layer.resolution)
        for index, range_seconds in enumerate(ranges)
    )


def _ring_bucket_view(
    generation: materializer.Generation,
    layer: materializer.Layer,
    bucket: materializer.Bucket,
) -> dict[str, object] | None:
    for range_seconds, carrier_start in _ring_view_carriers(layer):
        if bucket.start != carrier_start:
            continue
        view = materializer.slice_generation(
            generation,
            range_seconds,
            layer.resolution,
        )
        return {
            "range_seconds": range_seconds,
            "window_end": view.end,
            "cost_report": materializer.build_cost_report(view),
        }
    return None


def _ring_cost_coverage(value: object, name: str) -> materializer.CostCoverage:
    item = _object(value, name, RING_COST_COVERAGE_FIELDS)
    return materializer.CostCoverage(**item)


def _ring_cost_dimensions(value: object, name: str) -> tuple[materializer.CostDimensionValue, ...]:
    return tuple(
        materializer.CostDimensionValue(
            **_object(raw, f"{name}[{index}]", RING_COST_DIMENSION_FIELDS)
        )
        for index, raw in enumerate(_items(value, name))
    )


def _ring_cost_attributions(value: object, name: str) -> tuple[materializer.CostAttribution, ...]:
    result = []
    for index, raw in enumerate(_items(value, name)):
        item = _object(raw, f"{name}[{index}]", RING_COST_ATTRIBUTION_FIELDS)
        result.append(materializer.CostAttribution(
            key=item["key"],
            provider=item["provider"],
            model=item["model"],
            source=item["source"],
            label=item["label"],
            dimensions=_ring_cost_dimensions(
                item["dimensions"], f"{name}[{index}].dimensions",
            ),
            priced=_ring_cost_coverage(
                item["priced"], f"{name}[{index}].priced",
            ),
            unpriced=_ring_cost_coverage(
                item["unpriced"], f"{name}[{index}].unpriced",
            ),
        ))
    return tuple(result)


def _ring_cost_evidence(value: object) -> tuple[materializer.CostEvidenceValue, ...]:
    return tuple(
        materializer.CostEvidenceValue(
            **_object(
                raw,
                f"ring bucket cost evidence[{index}]",
                RING_COST_EVIDENCE_FIELDS,
            )
        )
        for index, raw in enumerate(_items(value, "ring bucket cost evidence"))
    )


def _ring_cost_detail(value: object) -> materializer.BucketCostDetail:
    item = _object(value, "ring bucket cost detail", RING_COST_DETAIL_FIELDS)
    return materializer.BucketCostDetail(
        dimensions=_ring_cost_dimensions(
            item["dimensions"], "ring bucket cost dimensions",
        ),
        priced=_ring_cost_coverage(item["priced"], "ring bucket priced coverage"),
        unpriced=_ring_cost_coverage(item["unpriced"], "ring bucket unpriced coverage"),
        models=_ring_cost_attributions(item["models"], "ring bucket models"),
        agents=_ring_cost_attributions(item["agents"], "ring bucket agents"),
        evidence=_ring_cost_evidence(item["evidence"]),
        omitted_models=item["omitted_models"],
        omitted_agents=item["omitted_agents"],
        omitted_evidence=item["omitted_evidence"],
    )


def _decode_ring_bucket(row: storage.RingBucketRow) -> DecodedRingBucket:
    payload = _object(
        json.loads(row.bucket_json),
        "ring bucket payload",
        RING_BUCKET_PAYLOAD_FIELDS,
    )
    if payload["version"] != RING_BUCKET_PAYLOAD_VERSION:
        raise ValueError("ring bucket payload version is unavailable")
    cache_generation = payload["cache_generation"]
    if (
        isinstance(cache_generation, bool)
        or not isinstance(cache_generation, int)
        or not 0 <= cache_generation <= MAX_SAFE_INTEGER
    ):
        raise ValueError("ring bucket cache_generation is invalid")
    generated_at = payload["generated_at"]
    if (
        isinstance(generated_at, bool)
        or not isinstance(generated_at, (int, float))
        or not math.isfinite(generated_at)
        or generated_at < 0
    ):
        raise ValueError("ring bucket generated_at is invalid")
    wire = dict(_object(
        payload["bucket"],
        "ring bucket wire",
        frozenset(protocol.BUCKET_FIELDS),
    ))
    if (
        wire["start"] != row.bucket_start
        or wire["duration"] != row.resolution_seconds
        or wire["open"] != (not row.complete)
    ):
        raise ValueError("ring bucket columns disagree with the persisted payload")
    no_data = tuple(
        dict(_object(
            raw,
            f"ring bucket no_data[{index}]",
            frozenset(protocol.NO_DATA_FIELDS),
        ))
        for index, raw in enumerate(_items(payload["no_data"], "ring bucket no_data"))
    )
    cost_detail_json = payload["cost_detail_json"]
    if not isinstance(cost_detail_json, str):
        raise ValueError("ring bucket cost_detail_json is invalid")
    raw_view = payload["view"]
    if raw_view is None:
        view = None
    else:
        view = dict(_object(raw_view, "ring bucket view", RING_VIEW_FIELDS))
        range_seconds = view["range_seconds"]
        window_end = view["window_end"]
        if (
            isinstance(range_seconds, bool)
            or not isinstance(range_seconds, int)
            or not stats_resolution.is_supported(range_seconds, row.resolution_seconds)
            or isinstance(window_end, bool)
            or not isinstance(window_end, int)
            or window_end % row.resolution_seconds
        ):
            raise ValueError("ring bucket view identity is invalid")
    return DecodedRingBucket(
        wire,
        no_data,
        cost_detail_json,
        view,
        cache_generation,
        float(generated_at),
        row.ring_generation,
    )


def _materialized_ring_bucket(item: DecodedRingBucket) -> materializer.Bucket:
    series_value = item.wire["series"]
    if not isinstance(series_value, Mapping):
        raise ValueError("ring bucket series is invalid")
    series = tuple(
        materializer.SeriesValue(
            name,
            values["value"],
            values["source_count"],
            values["first_timestamp"],
            values["last_timestamp"],
        )
        for name, raw in sorted(series_value.items())
        for values in (
            _object(
                raw,
                f"ring bucket series {name!r}",
                frozenset(protocol.SERIES_VALUE_FIELDS),
            ),
        )
    )
    source = _object(
        item.wire["source"],
        "ring bucket source",
        frozenset(protocol.SOURCE_FIELDS),
    )
    return materializer.Bucket(
        item.wire["start"],
        item.wire["duration"],
        series,
        source["count"],
        source["first_timestamp"],
        source["last_timestamp"],
        not item.wire["open"],
        _ring_cost_detail(json.loads(item.cost_detail_json)),
    )


def _ring_gap_bucket(
    start: int,
    duration: int,
    *,
    cache_generation: int,
    generated_at: float,
    ring_generation: int,
) -> DecodedRingBucket:
    end = start + duration
    # A dense empty bucket is a genuine zero. Pair the placeholder with explicit
    # family gaps so a crash or cold slot remains unknown without breaking the
    # consumer's exact-window contract.
    no_data = tuple({
        "family": spec.name,
        "source_id": "persisted-ring",
        "start": start,
        "end": end,
        "epoch": f"ring-{ring_generation}",
        "reason": "incomplete_persisted_bucket",
        "source_cadence_seconds": duration,
    } for spec in families.CURRENT_FAMILIES)
    return DecodedRingBucket(
        {
            "start": start,
            "duration": duration,
            "series": {},
            "source": {
                "first_timestamp": None,
                "last_timestamp": None,
                "count": 0,
            },
            "open": False,
        },
        no_data,
        _ring_cost_detail_json(materializer.BucketCostDetail()),
        None,
        cache_generation,
        generated_at,
        ring_generation,
    )


def _merge_ring_no_data(
    buckets: tuple[DecodedRingBucket, ...],
) -> list[dict[str, object]]:
    ordered = sorted(
        (dict(item) for bucket in buckets for item in bucket.no_data),
        key=lambda item: (
            item["family"], item["source_id"], item["start"], item["end"], item["epoch"],
        ),
    )
    merged: list[dict[str, object]] = []
    for item in ordered:
        if merged and (
            merged[-1]["family"],
            merged[-1]["source_id"],
            merged[-1]["epoch"],
            merged[-1]["reason"],
            merged[-1]["source_cadence_seconds"],
            merged[-1]["end"],
        ) == (
            item["family"],
            item["source_id"],
            item["epoch"],
            item["reason"],
            item["source_cadence_seconds"],
            item["start"],
        ):
            merged[-1]["end"] = item["end"]
        else:
            merged.append(item)
    return merged


def _wire_delta_from_components(
    old_buckets: Mapping[tuple[int, int], dict[str, object]],
    new_buckets: Mapping[tuple[int, int], dict[str, object]],
    old_gaps: Mapping[tuple[str, str, str, int | float, int | float], dict[str, object]],
    new_gaps: Mapping[tuple[str, str, str, int | float, int | float], dict[str, object]],
    range_seconds: int,
    resolution_seconds: int,
    source_generation: int,
    base_cache_generation: int,
    cache_generation: int,
    revision_number: int,
    cost_report: dict[str, object],
) -> protocol.DeltaWire:
    buckets = [
        new_buckets[key]
        for key in sorted(new_buckets)
        if old_buckets.get(key) != new_buckets[key]
    ]
    gaps = [
        new_gaps[key]
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
        buckets.append(new_buckets[max(new_buckets)])
    wire: protocol.DeltaWire = {
        "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "range_seconds": range_seconds,
        "resolution_seconds": resolution_seconds,
        "source_generation": source_generation,
        "base_cache_generation": base_cache_generation,
        "cache_generation": cache_generation,
        "revision": revision_number,
        "buckets": buckets,
        "no_data": gaps,
        "tombstones": tombstones,
        "cost_report": cost_report,
    }
    # Client-originated wire still crosses protocol.validate_delta; this trusted
    # server construction path shares the already-validated materialized values.
    return wire


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
    return _wire_delta_from_components(
        {(item.start, item.duration): _wire_bucket(item) for item in old_layer.buckets},
        {(item.start, item.duration): _wire_bucket(item) for item in new_layer.buckets},
        {
            (item.family, item.source_id, item.epoch_id, item.start, item.end): _wire_no_data(item)
            for item in old_layer.no_data
        },
        {
            (item.family, item.source_id, item.epoch_id, item.start, item.end): _wire_no_data(item)
            for item in new_layer.no_data
        },
        range_seconds,
        resolution_seconds,
        candidate.source_generation,
        previous.cache_generation,
        candidate.cache_generation,
        revision_number,
        cost_report,
    )


def _wire_snapshot_delta(
    previous: protocol.SnapshotWire,
    candidate: protocol.SnapshotWire,
    revision_number: int,
) -> protocol.DeltaWire:
    previous = protocol.validate_snapshot(previous)
    candidate = protocol.validate_snapshot(candidate)
    if (
        previous["range_seconds"],
        previous["resolution_seconds"],
    ) != (
        candidate["range_seconds"],
        candidate["resolution_seconds"],
    ):
        raise ValueError("snapshot delta endpoints disagree on their view")

    def bucket_key(item: dict[str, object]) -> tuple[int, int]:
        return int(item["start"]), int(item["duration"])

    def gap_key(item: dict[str, object]) -> tuple[str, str, str, int | float, int | float]:
        return (
            str(item["family"]),
            str(item["source_id"]),
            str(item["epoch"]),
            item["start"],
            item["end"],
        )

    return _wire_delta_from_components(
        {bucket_key(item): item for item in previous["buckets"]},
        {bucket_key(item): item for item in candidate["buckets"]},
        {gap_key(item): item for item in previous["no_data"]},
        {gap_key(item): item for item in candidate["no_data"]},
        candidate["range_seconds"],
        candidate["resolution_seconds"],
        candidate["source_generation"],
        previous["cache_generation"],
        candidate["cache_generation"],
        revision_number,
        candidate["cost_report"],
    )


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
        randomizer: Callable[[], float] = random.random,
        price_resolver: materializer.PriceResolver | None = None,
        migration_runner: Callable[..., migration.MigrationReport] = migration.migrate,
        prune_time_reader: Callable[[], str] = stats_prune_local_time,
    ):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-statsd")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.database_path = Path(database_path)
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.store_opener, self.reader_opener = store_opener, reader_opener
        self.full_builder, self.incremental_builder = full_builder, incremental_builder
        self.encoder, self.clock, self.monotonic = encoder, clock, monotonic
        self.randomizer = randomizer
        self.price_resolver = price_resolver if price_resolver is not None else pricing.UsagePriceProjector()
        self.migration_runner = migration_runner
        self.stop_event, self.work_event, self.cache_ready_event = threading.Event(), threading.Event(), threading.Event()
        self.work_lock, self.cache_lock, self.trace_lock = threading.Lock(), threading.Lock(), threading.Lock()
        self.writer: storage.Store | None = None
        self.collector_context: dict[str, int] | None = None
        # Where to push the web process's CPU sample, supplied by that process itself.
        self.collector_control_socket: str = ""
        self._host_cpu_sampler = host_collectors.CpuSampler()
        self._next_host_cpu_at = self.monotonic()
        self._next_host_gpu_at = self.monotonic()
        self._host_coverage_epochs: dict[tuple[int, str, str, float], tuple[str, float]] = {}
        self._host_gpu_sources: set[str] = set()
        self._host_gpu_seen_sources: set[str] = set()
        self._host_gpu_roster_owner_generation: int | None = None
        self._host_collector_failures = 0
        self._last_host_collector_error = ""
        # The web process's CPU/memory sample is pushed from here on a 1.0s cadence and it is the
        # ONLY writer of that metric. Every skip used to be silent -- no counter, no reason, and
        # `failures` stayed 0 because a skipped push never raised -- so a web row that read
        # "never measured" for the life of the process carried no evidence of why. These shared
        # records make both delivery paths observable: attempted vs delivered is the rate, and
        # the typed reason says which gate stopped it.
        self._host_push_status: dict[str, dict[str, int | float | str]] = {
            kind: {
                "attempted": 0,
                "delivered": 0,
                "last_reason": "",
                "last_reason_at": 0.0,
                "last_delivered_at": 0.0,
            }
            for kind in ("cpu", "memory")
        }
        self.worker: threading.Thread | None = None
        self.leases: dict[str, object] = {}
        self.started_at, self.last_client_at = self.clock(), self.monotonic()
        self._pending_full = True
        self._pending_dirty: set[materializer.DirtyCell] = set()
        self._pending_ring_dirty: set[materializer.DirtyCell] = set()
        self._ring_source_generation = 0
        self._next_ring_flush_at: float | None = None
        self._ring_waiting_for_source = 0
        self._ring_publications = 0
        self._ring_buckets_published = 0
        self._statsd_unchanged_cell_materialization = 0
        self._last_ring_published_at = 0.0
        self._last_ring_publish_seconds = 0.0
        self._last_ring_source_generation = 0
        self._ring_failure = ""
        self._ring_published_cursors: dict[int, RingCursor] = {}
        self._ring_views: dict[DeltaKey, RingViewState] = {}
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
        self._cached_coverage_epochs: tuple[storage.CoverageEpoch, ...] = ()
        self._cached_unavailable_spans: tuple[storage.UnavailableSpan, ...] = ()
        self._coverage_cache_ready = False
        self._coverage_version = 0
        self._latest_source_generation = self._next_cache_generation = 0
        self._cache: PublishedCache | None = None
        self._delta_entries: dict[DeltaKey, list[CacheEntry]] = {}
        self._delta_revisions: dict[DeltaKey, int] = {}
        self._encoded_cost_reports_generation = -1
        self._encoded_cost_reports: Mapping[tuple[int, int], dict[str, object]] = MappingProxyType({})
        self._prune_time_reader = prune_time_reader
        self._prune_time = prune_schedule.resolve_local_time(prune_schedule.DEFAULT_PRUNE_LOCAL_TIME)
        self._prune_preference_error = ""
        self._next_prune_check_at = self.monotonic()
        self._last_pruned_at = 0.0
        self._prunes = 0
        self._last_prune_at = 0.0
        self._last_prune_seconds = 0.0
        self._last_prune_due_at = 0.0
        self._last_vacuumed_at = 0.0
        self._last_vacuum_seconds = 0.0
        self._vacuum_count = 0
        self._vacuum_failure = ""
        self._vacuum_jitter_seconds = self._vacuum_jitter()
        self._next_vacuum_at = self.monotonic() + VACUUM_INTERVAL_SECONDS + self._vacuum_jitter_seconds
        # Monotonic instant at which compaction first became due and started
        # waiting for a quiet window; None whenever it is not currently owed. The
        # max-defer cap is measured from here, not from the last vacuum, so a
        # steady stream of requests cannot keep resetting the deadline.
        self._vacuum_due_since: float | None = None
        self._building = False
        self._rejected_old = self._append_requests = self._snapshot_requests = 0
        self._usage_attribution_conflicts = 0
        self._usage_atoms_accepted = 0
        self._last_usage_atom_accepted_at = 0.0
        self._browser_reports_accepted = 0
        self._browser_observations_accepted = 0
        self._last_browser_report_accepted_at = 0.0
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
        self._usage_atom_backfill: dict[str, object] | None = None
        self._snapshot_body_decoration_lock = threading.Lock()
        self._snapshot_body_decoration_cache: DecoratedSnapshotBody | None = None
        self._snapshot_body_decoration_builds = 0
        self._snapshot_body_decoration_hits = 0
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
        self._migration_issue_kinds: tuple[str, ...] = ()
        self._migration_issue_records: tuple[dict[str, str], ...] = ()

    def _start(self) -> None:
        started = self.monotonic()
        self._migration_state = "running"
        try:
            # run_local_rpc_service invokes _start only after winning the
            # singleton lock. Preflight therefore precedes both migration and
            # the first mutating SQLite open without racing another statsd.
            try:
                storage.require_compatible_writer(
                    self.database_path,
                    writer_protocol=storage.MIN_WRITER_PROTOCOL,
                    writer_build=storage.MIN_WRITER_BUILD,
                )
            except storage.SchemaMismatchError:
                # Migration owns physical-corruption recovery. Passing an
                # unreadable file to that owner is not a successful preflight;
                # it either quarantines with a typed issue or fails closed.
                pass
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
        issue_kinds = tuple(sorted({issue.kind for issue in report.issues}))[:16]
        self._migration_issue_records = tuple(_bounded_migration_issue(issue) for issue in report.issues[:16])
        if report.already_active:
            self._migration_result = "existing"
        elif migration.UNREADABLE_CURRENT_DATABASE in issue_kinds:
            # Distinct from "activated" on purpose: the browser must be able to
            # say history was reset, rather than showing an empty chart as if
            # this were a first run.
            self._migration_result = "recovered"
        else:
            self._migration_result = "activated"
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
        self._migration_issue_kinds = issue_kinds
        self._last_vacuumed_at = self.writer.last_vacuumed_at()
        remaining = (
            max(
                0.0,
                self._last_vacuumed_at + VACUUM_INTERVAL_SECONDS + self._vacuum_jitter_seconds - self.clock(),
            )
            if self._last_vacuumed_at > 0.0
            else VACUUM_INTERVAL_SECONDS + self._vacuum_jitter_seconds
        )
        self._next_vacuum_at = self.monotonic() + remaining
        # Read the persisted prune time before the first check: a restart must not
        # re-run last night's prune, and a daemon that lives less than a day must
        # still catch up a night that was missed while the machine was off.
        self._last_pruned_at = self.writer.last_pruned_at()
        self._next_prune_check_at = self.monotonic()
        self.worker = threading.Thread(target=self._worker_loop, name="yolomux-stats-materializer", daemon=True)
        self.worker.start()
        self.work_event.set()

    def _vacuum_jitter(self) -> float:
        """Return bounded injectable scheduling jitter for periodic maintenance."""
        return VACUUM_JITTER_SECONDS * min(1.0, max(0.0, float(self.randomizer())))

    def _vacuum_if_due_while_idle(self, writer: storage.Store | None = None) -> bool:
        """Run file-rewriting maintenance on the hourly cadence, quiet-gated.

        Compaction becomes DUE on the ``_next_vacuum_at`` cadence, but the rewrite
        holds ``work_lock`` for the whole SQLite pass, so on a busy box it would
        block every arriving RPC past the append deadline. Quiet means no RPC has
        been served within ``idle_seconds`` -- ``_on_client`` stamps
        ``last_client_at`` on every served request -- so while requests keep
        arriving compaction DEFERS and re-checks on the next idle tick instead of
        stalling live traffic. A permanently busy box would then never reclaim the
        pruned free-list, so once compaction has been owed longer than
        ``VACUUM_MAX_DEFER_SECONDS`` (measured from when it first became due, via
        ``_vacuum_due_since``) it runs anyway and accepts the brief stall.
        """
        vacuum_writer = self.writer if writer is None else writer
        if vacuum_writer is None:
            return False
        now = self.monotonic()
        if now < self._next_vacuum_at:
            return False
        # First idle tick at which we are owed: start the max-defer clock here so
        # the cap measures from when compaction was first due, not the last run.
        if self._vacuum_due_since is None:
            self._vacuum_due_since = now
        quiet = now - self.last_client_at >= self.idle_seconds
        capped = now - self._vacuum_due_since >= VACUUM_MAX_DEFER_SECONDS
        if not quiet and not capped:
            # Due, but the box is busy and the cap has not elapsed. The worker
            # must yield instead of spinning on an already-past deadline, while
            # still waking no later than the fixed max-defer boundary.
            self._next_vacuum_at = min(
                now + VACUUM_RETRY_SECONDS,
                self._vacuum_due_since + VACUUM_MAX_DEFER_SECONDS,
            )
            return False
        with self.work_lock:
            pending = (
                self._pending_full
                or bool(self._pending_dirty)
                or self._pending_coverage_refresh
                or bool(self._pending_ring_dirty)
            )
            # A live SQLite read must finish before VACUUM, but queued work is
            # only a scheduling preference. Once the max-defer cap fires, keep
            # the queue intact and compact before taking its next generation;
            # otherwise one dirty cell per tick defeats the cap forever.
            if self._building or (pending and not capped):
                self._next_vacuum_at = self.monotonic() + VACUUM_RETRY_SECONDS
                return False
            started = self.monotonic()
            try:
                completed_at = vacuum_writer.vacuum(completed_at=self.clock())
            except (OSError, sqlite3.Error, storage.StatsCurrentError) as error:
                self._vacuum_failure = type(error).__name__[:64]
                self._next_vacuum_at = self.monotonic() + VACUUM_RETRY_SECONDS
                self._record_failure("vacuum", error)
                return False
            self._last_vacuumed_at = completed_at
            self._last_vacuum_seconds = max(0.0, self.monotonic() - started)
            self._vacuum_count += 1
            self._vacuum_failure = ""
            self._clear_failure("vacuum")
            # Only a completed rewrite clears the due-since clock; a pending/failed
            # attempt above keeps it so the cap still counts from the first due tick.
            self._vacuum_due_since = None
            self._next_vacuum_at = self.monotonic() + VACUUM_INTERVAL_SECONDS + self._vacuum_jitter()
            return True

    def _close(self) -> None:
        self.stop_event.set()
        self.work_event.set()
        if self.worker is not None:
            self.worker.join(timeout=1.0)
        if self.writer is not None:
            self.writer.close()
            self.writer = None

    def stop(self) -> None:
        """Request shutdown of the listener and materialization worker."""

        self.stop_event.set()
        self.work_event.set()

    def _take_work(self) -> tuple[bool, frozenset[materializer.DirtyCell], bool] | None:
        with self.work_lock:
            if (
                not self._pending_full
                and not self._pending_dirty
                and not self._pending_coverage_refresh
            ):
                return None
            work = (
                self._pending_full,
                frozenset(self._pending_dirty),
                self._pending_coverage_refresh,
            )
            self._pending_full = False
            self._pending_dirty.clear()
            self._pending_coverage_refresh = False
            return work

    def _stage_ring_cells_locked(
        self,
        cells: frozenset[materializer.DirtyCell] | set[materializer.DirtyCell],
        source_generation: int,
    ) -> None:
        """Coalesce cells while the sole writer lock already protects ingest state."""

        if not cells:
            return
        self._pending_ring_dirty.update(cells)
        self._ring_source_generation = max(
            self._ring_source_generation,
            source_generation,
        )
        if self._next_ring_flush_at is None:
            self._next_ring_flush_at = self.monotonic() + RING_FLUSH_SECONDS

    @staticmethod
    def _changed_ring_cells(
        previous: materializer.Generation | None,
        candidate: materializer.Generation,
    ) -> frozenset[materializer.DirtyCell]:
        previous_layers = {
            layer.resolution: layer
            for layer in (() if previous is None else previous.layers)
        }
        previous_buckets = {
            (layer.resolution, bucket.start): bucket
            for layer in previous_layers.values()
            for bucket in layer.buckets
        }
        changed = {
            materializer.DirtyCell(layer.resolution, bucket.start)
            for layer in candidate.layers
            for bucket in layer.buckets
            if previous_buckets.get((layer.resolution, bucket.start)) != bucket
        }
        for layer in candidate.layers:
            previous_layer = previous_layers.get(layer.resolution)
            if previous_layer is None:
                changed.update(
                    materializer.DirtyCell(layer.resolution, start)
                    for item in layer.no_data
                    for start in _ring_no_data_cell_starts(layer, item)
                )
                continue
            if previous_layer.no_data == layer.no_data:
                continue
            changed.update(
                materializer.DirtyCell(layer.resolution, start)
                for start in _changed_ring_no_data_starts(previous_layer, layer)
            )
        for layer in candidate.layers:
            changed_starts = {
                cell.start for cell in changed if cell.resolution == layer.resolution
            }
            changed.update(
                materializer.DirtyCell(layer.resolution, carrier_start)
                for range_seconds, carrier_start in _ring_view_carriers(layer)
                if any(
                    layer.end - range_seconds <= start < layer.end
                    for start in changed_starts
                )
            )
        return frozenset(changed)

    def _stage_ring_candidate(
        self,
        previous: materializer.Generation | None,
        candidate: materializer.Generation,
    ) -> None:
        changed = set(self._changed_ring_cells(previous, candidate))
        with self.cache_lock:
            active_public_views = {
                (range_seconds, resolution_seconds)
                for range_seconds, resolution_seconds, private_source_id in self._ring_views
                if private_source_id is None
            }
        for layer in candidate.layers:
            changed.update(
                materializer.DirtyCell(layer.resolution, carrier_start)
                for range_seconds, carrier_start in _ring_view_carriers(layer)
                if (range_seconds, layer.resolution) in active_public_views
            )
        previous_no_data = {
            layer.resolution: layer.no_data
            for layer in (() if previous is None else previous.layers)
        }
        no_data_changed = any(
            previous_no_data.get(layer.resolution) != layer.no_data
            for layer in candidate.layers
        )
        with self.work_lock:
            if previous is not None and no_data_changed:
                self._statsd_unchanged_cell_materialization += 1
            self._stage_ring_cells_locked(frozenset(changed), candidate.source_generation)
            if (
                self._ring_waiting_for_source
                and candidate.source_generation >= self._ring_waiting_for_source
            ):
                self._next_ring_flush_at = self.monotonic()
                self._ring_waiting_for_source = 0

    def _ring_wait_timeout(self) -> float | None:
        with self.work_lock:
            deadlines = [self._next_prune_check_at, self._next_vacuum_at]
            if self.collector_context is not None:
                deadlines.extend((self._next_host_cpu_at, self._next_host_gpu_at))
            if self._pending_ring_dirty and self._next_ring_flush_at is not None:
                deadlines.append(self._next_ring_flush_at)
            if not deadlines:
                return None
            return max(0.0, min(deadlines) - self.monotonic())

    @staticmethod
    def _ring_writes(
        candidate: materializer.Generation,
        cells: frozenset[materializer.DirtyCell],
    ) -> tuple[storage.RingBucketWrite, ...]:
        buckets = {
            (layer.resolution, bucket.start): (layer, bucket)
            for layer in candidate.layers
            for bucket in layer.buckets
        }
        starts_by_resolution: dict[int, set[int]] = {}
        for cell in cells:
            starts_by_resolution.setdefault(cell.resolution, set()).add(cell.start)
        no_data = {
            layer.resolution: _ring_no_data_for_cells(
                layer, starts_by_resolution.get(layer.resolution, set()),
            )
            for layer in candidate.layers
        }
        writes = []
        for cell in sorted(cells, key=lambda item: (item.resolution, item.start)):
            # Synthetic early-epoch clocks can place a full horizon before zero;
            # persisted timestamps start at the Unix epoch by storage contract.
            if cell.start < 0:
                continue
            selected = buckets.get((cell.resolution, cell.start))
            if selected is None:
                continue
            layer, bucket = selected
            writes.append(storage.RingBucketWrite(
                resolution_seconds=cell.resolution,
                bucket_start=cell.start,
                bucket_json=json.dumps(
                    _ring_bucket_payload(
                        candidate,
                        layer,
                        bucket,
                        no_data[layer.resolution].get(bucket.start, ()),
                    ),
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                complete=bucket.complete,
            ))
        return tuple(writes)

    def _restart_ring_cells(
        self,
        ring_writer: storage.Store,
        candidate: materializer.Generation,
        cells: frozenset[materializer.DirtyCell],
    ) -> frozenset[materializer.DirtyCell]:
        """Keep a restart's first full build from synthesizing downtime as quiet zero."""

        if self._ring_publications:
            return cells
        starts_by_resolution: dict[int, set[int]] = {}
        for cell in cells:
            starts_by_resolution.setdefault(cell.resolution, set()).add(cell.start)
        no_data = {
            layer.resolution: _ring_no_data_for_cells(
                layer, starts_by_resolution.get(layer.resolution, set()),
            )
            for layer in candidate.layers
        }
        persisted: dict[tuple[int, int], bool] = {}
        ring_generation = 0
        for layer in candidate.layers:
            window = ring_writer.read_ring_window(
                range_seconds=(
                    layer.resolution
                    * stats_resolution.RING_CAPACITIES[layer.resolution]
                ),
                resolution_seconds=layer.resolution,
                window_end=layer.end,
            )
            ring_generation = max(ring_generation, window.ring_generation)
            persisted.update({
                (row.resolution_seconds, row.bucket_start): row.complete
                for row in window.rows
            })
        if ring_generation <= 0:
            return cells
        buckets = {
            (layer.resolution, bucket.start): (layer, bucket)
            for layer in candidate.layers
            for bucket in layer.buckets
        }
        retained = set()
        for cell in cells:
            selected = buckets.get((cell.resolution, cell.start))
            if selected is None:
                continue
            layer, bucket = selected
            persisted_complete = persisted.get((cell.resolution, cell.start))
            historical_open = (
                persisted_complete is False
                and cell.start + cell.resolution <= self.started_at
            )
            has_persisted_fact = persisted_complete is True
            overlaps_uptime = cell.start + cell.resolution > self.started_at
            has_materialized_fact = bool(
                bucket.series
                or bucket.source_count
                or no_data[layer.resolution].get(bucket.start)
                or bucket.cost_detail != materializer.BucketCostDetail()
            )
            if not historical_open and (
                has_persisted_fact or overlaps_uptime or has_materialized_fact
            ):
                retained.add(cell)
        return frozenset(retained)

    def _flush_ring_if_due(
        self,
        publisher: storage.Store | None = None,
    ) -> storage.RingPublication | None:
        """Publish one coherent all-resolution generation after the staging deadline."""

        with self.work_lock:
            ring_writer = self.writer if publisher is None else publisher
            if (
                ring_writer is None
                or not self._pending_ring_dirty
                or self._next_ring_flush_at is None
                or self.monotonic() < self._next_ring_flush_at
            ):
                return None
            with self.cache_lock:
                candidate = None if self._cache is None else self._cache.generation
            if (
                candidate is None
                or candidate.source_generation < self._ring_source_generation
            ):
                self._ring_waiting_for_source = self._ring_source_generation
                self._next_ring_flush_at = None
                return None
            cells = frozenset(self._pending_ring_dirty)
            started = self.monotonic()
            try:
                # Store.open remains an untouched v7 raw database. Only the
                # singleton statsd writer opts into the explicit ring kernel,
                # immediately before its first publication.
                ring_writer.initialize_ring_storage()
                writes = self._ring_writes(
                    candidate,
                    self._restart_ring_cells(ring_writer, candidate, cells),
                )
                if not writes:
                    self._pending_ring_dirty.difference_update(cells)
                    self._next_ring_flush_at = None
                    return None
                publication = ring_writer.publish_ring_buckets(
                    buckets=writes,
                    source_generation=candidate.source_generation,
                    published_at=self.clock(),
                )
            except BUILD_ERRORS as error:
                self._ring_failure = type(error).__name__[:64]
                self._next_ring_flush_at = self.monotonic() + RING_FLUSH_SECONDS
                self._record_failure("ring_writer", error)
                return None
            self._pending_ring_dirty.difference_update(cells)
            self._next_ring_flush_at = None
            self._ring_waiting_for_source = 0
            self._ring_publications += 1
            self._ring_buckets_published += publication.buckets_updated
            self._last_ring_published_at = publication.published_at
            self._last_ring_publish_seconds = max(0.0, self.monotonic() - started)
            self._last_ring_source_generation = publication.source_generation
            self._ring_failure = ""
            self._publish_ring_views(
                ring_writer,
                candidate,
                frozenset(write.resolution_seconds for write in writes),
            )
            self._clear_failure("ring_writer")
            return publication

    def _worker_loop(self) -> None:
        reader: storage.Store | None = None
        publisher: storage.Store | None = None
        try:
            reader = self.reader_opener(
                self.database_path,
                writer_protocol=storage.MIN_WRITER_PROTOCOL,
                writer_build=storage.MIN_WRITER_BUILD,
            )
            # sqlite3 connections are thread-owned. This connection belongs to
            # the elected statsd worker; work_lock still serializes it with the
            # listener thread's append/prune connection.
            publisher = self.store_opener(
                self.database_path,
                writer_protocol=storage.MIN_WRITER_PROTOCOL,
                writer_build=storage.MIN_WRITER_BUILD,
            )
        except (OSError, sqlite3.Error, storage.StatsCurrentError) as error:
            self._record_build_failure(error)
            if reader is not None:
                reader.close()
            # The listener must not keep accepting facts after its sole
            # retention/materialization owner failed to start.
            self.stop_event.set()
            return
        try:
            while not self.stop_event.is_set():
                self.work_event.wait(self._ring_wait_timeout())
                self.work_event.clear()
                if self.stop_event.is_set():
                    break
                self._prune_if_due(publisher)
                self._vacuum_if_due_while_idle(publisher)
                self._collect_host_facts_if_due(publisher)
                work = self._take_work()
                if work is not None:
                    self._build_once(reader, *work)
                self._flush_ring_if_due(publisher)
        finally:
            try:
                reader.close()
                publisher.close()
            finally:
                # An unexpected worker exit is fatal to the listener. Without
                # this, append RPCs can outlive the only scheduled pruner.
                self.stop_event.set()

    def _append_host_facts(self, publisher: storage.Store, facts: collectors.CollectorFacts) -> None:
        """Append daemon-owned facts through the same dirty/materialization path as RPC ingest."""

        if not facts.observations and not facts.coverage_epochs:
            return
        with self.work_lock:
            append_now = self.clock()
            result = publisher.append_batch(
                observations=facts.observations,
                coverage_epochs=facts.coverage_epochs,
            )
            accepted = result.observations_accepted + result.coverage_changed
            pruned = 0 if result.retention_prune is None else result.retention_prune.changed
            if not accepted and not pruned:
                return
            dirty = self._append_dirty_cells(result)
            self._latest_source_generation = max(self._latest_source_generation, result.source_generation)
            self._last_source_commit_at = append_now
            self._pending_dirty.update(dirty)
            self._stage_ring_cells_locked(dirty, result.source_generation)
            self._update_cached_coverage_locked(
                facts.coverage_epochs,
                (),
                accepted_change=bool(result.coverage_changed),
                retention_prune=result.retention_prune,
            )
        self.work_event.set()

    def _web_push_target(self) -> tuple[dict[str, object] | None, str]:
        """Resolve where to push this process's CPU sample, and say WHY when there is nowhere.

        The address comes from the `collector_context` handshake -- the web process tells this
        statsd, over this statsd's own control channel, both which process it serves and where
        to reach it. It used to be re-discovered from `BACKGROUND_OWNER_DIR/owner.json`, which
        is the distributed-ELECTION record and answers a different question. A managed instance
        runs `DisabledBackgroundOwner` and holds no election, so no record was ever written and
        the push was skipped forever: the whole reason the Daemons web row read "never measured"
        for the life of the process.

        This does not weaken who may receive a sample. The address is no longer read from a
        shared mutable file any co-rooted server can write; it is stated by the target process
        itself. The identity is carried in the sample (`sample["pid"]` is `context["pid"]`) and
        the RECEIVER refuses any sample whose pid is not its own -- see
        `TmuxWebtermApp.handle_control_request`, "stats CPU sample PID mismatch". That check is
        unforgeable and is covered by its own test.
        """

        if not self.collector_control_socket:
            return None, "web_owner_no_control_socket"
        return {"control_socket": self.collector_control_socket}, ""

    def _record_host_push(self, kind: str, reason: str) -> None:
        """One recorder for every CPU or process-memory delivery outcome."""

        status = self._host_push_status[kind]
        status["attempted"] = int(status["attempted"]) + 1
        if not reason:
            status["delivered"] = int(status["delivered"]) + 1
            status["last_delivered_at"] = self.clock()
            status["last_reason"] = ""
            status["last_reason_at"] = 0.0
            return
        status["last_reason"] = reason[:120]
        status["last_reason_at"] = self.clock()

    def _host_push_status_payload(self, kind: str) -> dict[str, int | float | str]:
        status = self._host_push_status[kind]
        return {
            **status,
            "skipped": max(0, int(status["attempted"]) - int(status["delivered"])),
        }

    def _host_coverage_epoch(
        self,
        publisher: storage.Store,
        *,
        context: Mapping[str, int],
        family: str,
        source_id: str,
        cadence_seconds: float,
        observed_at: float,
        reuse_retained: bool = True,
    ) -> tuple[str, float]:
        """Keep one source lifecycle identity across samples and statsd restarts."""

        owner_generation = context["owner_generation"]
        cadence = float(cadence_seconds)
        key = (owner_generation, family, source_id, cadence)
        current = self._host_coverage_epochs.get(key)
        if current is not None:
            return current
        source_digest = hashlib.sha256(
            f"{family}\0{source_id}\0{cadence.hex()}".encode("utf-8")
        ).hexdigest()[:16]
        epoch_prefix = f"inline:{owner_generation}:{family}:{source_digest}:"
        retained = publisher.latest_coverage_epoch(
            family,
            source_id,
            owner_generation,
            cadence,
        ) if reuse_retained else None
        if retained is not None and retained.epoch_id.startswith(epoch_prefix):
            result = (retained.epoch_id, retained.started_at)
        else:
            lifecycle_digest = hashlib.sha256(observed_at.hex().encode("ascii")).hexdigest()[:12]
            result = (f"{epoch_prefix}{lifecycle_digest}", observed_at)
        self._host_coverage_epochs[key] = result
        return result

    def _collect_host_facts_if_due(self, publisher: storage.Store) -> None:
        context = self.collector_context
        if context is None:
            return
        now_monotonic = self.monotonic()
        now = self.clock()
        source_id = f"port:{context['port']}" if context["port"] else f"pid:{context['pid']}"
        try:
            if now_monotonic >= self._next_host_cpu_at:
                self._next_host_cpu_at = now_monotonic + HOST_CPU_CADENCE_SECONDS
                sample = self._host_cpu_sampler.sample(context["pid"])
                # The sampler differences two readings, so its FIRST call after every statsd start
                # has nothing to difference and reports `None` rather than `0.0`. This cycle then
                # publishes nothing at all -- the "nothing to report this cycle" shape the GPU and
                # service-load collectors already use -- because `cpu_success(0.0, 0.0)` would
                # write a fabricated measurement into 48-hour history where it owns the whole 1s
                # bucket at the default five-minute view.
                #
                # The push is SKIPPED, not sent with `None`: the receiver does
                # `float(sample["cpu_percent"])` and would reject it as "invalid stats CPU sample",
                # inflating the rejection counter with a self-inflicted error. One second of
                # structural absence is correct, and `latest_stats_sample` already renders it.
                # The skip is still counted with its reason, because a skipped push that leaves no
                # evidence is the defect this gate was built for.
                if sample["cpu_percent"] is None or sample["system_cpu_percent"] is None:
                    self._record_host_push("cpu", "cpu_sample_no_baseline")
                    # RSS is an absolute census and does not depend on either CPU baseline.
                    # Deliver it independently so a missing /proc/stat or Mach CPU reading
                    # cannot make the System memory process areas disappear.
                    if isinstance(sample.get("process_memory_bytes"), Mapping):
                        owner, push_reason = self._web_push_target()
                        if owner is None:
                            self._record_host_push("memory", push_reason)
                        else:
                            response = send_yolomux_control_request(
                                owner,
                                {
                                    "action": "stats_process_memory_sample",
                                    "sample": {
                                        "time": sample["time"],
                                        "pid": sample["pid"],
                                        "process_memory_bytes": sample["process_memory_bytes"],
                                    },
                                },
                                timeout=0.25,
                            )
                            accepted = isinstance(response, dict) and response.get("ok") is True
                            error = str(response.get("error") or "") if isinstance(response, dict) else "invalid control response"
                            self._record_host_push(
                                "memory",
                                "" if accepted else f"push_rejected: {error or 'unknown error'}",
                            )
                else:
                    epoch_id, epoch_started_at = self._host_coverage_epoch(
                        publisher,
                        context=context,
                        family="cpu",
                        source_id=source_id,
                        cadence_seconds=HOST_CPU_CADENCE_SECONDS,
                        observed_at=now,
                    )
                    facts = collectors.cpu_success(
                        epoch_id=epoch_id,
                        epoch_started_at=epoch_started_at,
                        observed_at=now,
                        cadence_seconds=HOST_CPU_CADENCE_SECONDS,
                        owner_generation=context["owner_generation"],
                        source_id=source_id,
                        process_percent=float(sample["cpu_percent"]),
                        system_percent=float(sample["system_cpu_percent"]),
                        process_cpu_percent=(
                            sample.get("process_cpu_percent")
                            if isinstance(sample.get("process_cpu_percent"), Mapping)
                            else None
                        ),
                    )
                    self._append_host_facts(publisher, facts)
                    # The sole producer of the web process's own CPU/memory metric. It is
                    # fire-and-forget by design (a slow web process must not stall statsd's
                    # cadence), but fire-and-forget must still mean OBSERVED-and-forget: the
                    # outcome of every attempt is counted and the failing gate is named.
                    owner, push_reason = self._web_push_target()
                    if owner is None:
                        self._record_host_push("cpu", push_reason)
                    else:
                        response = send_yolomux_control_request(
                            owner, {"action": "stats_cpu_sample", "sample": sample}, timeout=0.25,
                        )
                        accepted = isinstance(response, dict) and response.get("ok") is True
                        error = str(response.get("error") or "") if isinstance(response, dict) else "invalid control response"
                        self._record_host_push("cpu", "" if accepted else f"push_rejected: {error or 'unknown error'}")
            if now_monotonic >= self._next_host_gpu_at:
                self._next_host_gpu_at = now_monotonic + HOST_GPU_CADENCE_SECONDS
                if self._host_gpu_roster_owner_generation != context["owner_generation"]:
                    retained_sources = set(publisher.inline_coverage_source_ids(
                        "gpu",
                        context["owner_generation"],
                    ))
                    self._host_gpu_sources = retained_sources
                    self._host_gpu_seen_sources = set(retained_sources)
                    self._host_gpu_roster_owner_generation = context["owner_generation"]
                devices = host_collectors.gpu_devices()
                current_sources = set(devices)
                reappeared_sources = (
                    current_sources - self._host_gpu_sources
                ) & self._host_gpu_seen_sources
                for retired_source in self._host_gpu_sources - current_sources:
                    self._host_coverage_epochs.pop((
                        context["owner_generation"],
                        "gpu",
                        retired_source,
                        HOST_GPU_CADENCE_SECONDS,
                    ), None)
                device_facts = tuple(
                    collectors.gpu_devices_success(
                        (collectors.GpuDeviceSample(
                            source_id=device_source_id,
                            util_percent=float(device["util_percent"]),
                            memory_used_bytes=float(device["memory_used_bytes"]),
                            memory_capacity_bytes=float(device["memory_capacity_bytes"]),
                            label=str(device["label"]),
                        ),),
                        epoch_id=epoch[0],
                        epoch_started_at=epoch[1],
                        observed_at=now,
                        cadence_seconds=HOST_GPU_CADENCE_SECONDS,
                        owner_generation=context["owner_generation"],
                    )
                    for device_source_id, device in devices.items()
                    for epoch in (self._host_coverage_epoch(
                        publisher,
                        context=context,
                        family="gpu",
                        source_id=device_source_id,
                        cadence_seconds=HOST_GPU_CADENCE_SECONDS,
                        observed_at=now,
                        reuse_retained=device_source_id not in reappeared_sources,
                    ),)
                )
                facts = collectors.CollectorFacts(
                    observations=tuple(
                        item for device_fact in device_facts for item in device_fact.observations
                    ),
                    coverage_epochs=tuple(
                        item for device_fact in device_facts for item in device_fact.coverage_epochs
                    ),
                )
                self._append_host_facts(publisher, facts)
                self._host_gpu_sources = current_sources
                self._host_gpu_seen_sources.update(current_sources)
            self._last_host_collector_error = ""
        except (OSError, ValueError, storage.StatsCurrentError) as error:
            self._host_collector_failures += 1
            self._last_host_collector_error = type(error).__name__
            self._record_failure("host_collector", error)

    def _build_once(self, reader: storage.Store, full: bool,
                    dirty: frozenset[materializer.DirtyCell], coverage_refresh: bool = False) -> None:
        started = self.monotonic()
        used_full = full
        self._building = True
        try:
            with self.cache_lock:
                previous = None if self._cache is None else self._cache.generation
            used_full = full or previous is None
            observed_until = self.clock()
            dirty_intervals = None if used_full else tuple(
                (cell.start, cell.start + cell.resolution) for cell in dirty
            )
            read_window = (
                max(0.0, observed_until - stats_resolution.MAX_RANGE_SECONDS),
                max(observed_until, math.nextafter(0.0, math.inf)),
            )
            with ExitStack() as snapshot_stack:
                with self.work_lock:
                    coverage_version = self._coverage_version
                    coverage_cache_was_ready = self._coverage_cache_ready
                    cached_coverage_epochs = self._cached_coverage_epochs
                    cached_unavailable_spans = self._cached_unavailable_spans
                    # `coverage_refresh` means an accepted coverage delta still
                    # needs to materialize no-data spans. It does not mean the
                    # complete immutable history must be read again.
                    include_coverage = used_full or not coverage_cache_was_ready
                    # Pin the SQLite WAL generation before a later append can commit;
                    # row scanning then remains independent of the durable writer.
                    read_snapshot = snapshot_stack.enter_context(
                        reader.pinned_snapshot(
                            dirty_intervals=dirty_intervals,
                            include_coverage=include_coverage,
                            read_window=read_window,
                        )
                    )
                snapshot = read_snapshot()
                if include_coverage:
                    coverage_epochs, unavailable_spans = materializer.normalize_coverage_model(
                        snapshot.coverage_epochs,
                        snapshot.unavailable_spans,
                    )
                    snapshot = replace(
                        snapshot,
                        coverage_epochs=coverage_epochs,
                        unavailable_spans=unavailable_spans,
                    )
                    with self.work_lock:
                        if self._coverage_version == coverage_version:
                            self._cached_coverage_epochs = coverage_epochs
                            self._cached_unavailable_spans = unavailable_spans
                            self._coverage_cache_ready = True
                else:
                    snapshot = replace(
                        snapshot,
                        coverage_epochs=cached_coverage_epochs,
                        unavailable_spans=cached_unavailable_spans,
                    )
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
                observed_until=observed_until,
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
            if self._publish(candidate, encoded, resolutions=resolutions):
                self._stage_ring_candidate(previous, candidate)
                # Ready means every consumer-visible owner for this generation has been
                # established. Setting the event inside _publish let a waiter advance an
                # injected monotonic clock before ring staging chose its flush deadline.
                self.cache_ready_event.set()
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
                    entry_generations = {
                        key: generation
                        for key, generation in (
                            () if previous_cache is None
                            else previous_cache.entry_generations.items()
                        )
                        if key in retained_entries
                    }
                    entry_generations.update({key: candidate for key in entries})
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
                        MappingProxyType(entry_generations),
                    )
                    self._publish_warm_views_locked(entries)
                    self._last_build_at = candidate.generated_at
                    self._clear_failure("materializer")
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
            if (
                key[2] not in allowed_clients
                or (key[2] is None and key in self._ring_views)
            ):
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
                    if private_source_id is None and key in self._ring_views:
                        continue
                    previous = self._delta_generation_owner(
                        previous_cache,
                        range_seconds,
                        resolution_seconds,
                        private_source_id,
                    )
                    if previous is None:
                        continue
                    revision_number = self._delta_revisions.get(key, 0) + 1
                    entry = self._delta_entry(
                        previous,
                        candidate,
                        range_seconds,
                        resolution_seconds,
                        revision_number,
                        cost_report,
                        private_source_id=private_source_id,
                    )
                    ring = self._delta_entries.setdefault(key, [])
                    ring.append(entry)
                    del ring[:-DELTA_RING_ENTRY_BOUNDS[resolution_seconds]]
                    self._delta_revisions[key] = revision_number

    @staticmethod
    def _delta_generation_owner(
        cache: PublishedCache,
        range_seconds: int,
        resolution_seconds: int,
        private_source_id: PrivateClientKey,
    ) -> materializer.Generation | None:
        requested_values: tuple[protocol.RequestedResolution, ...] = (
            (resolution_seconds, stats_resolution.AUTO)
            if resolution_seconds == stats_resolution.auto_resolution(range_seconds)
            else (resolution_seconds,)
        )
        generations = tuple(
            cache.entry_generations[key]
            for requested in requested_values
            for key in ((range_seconds, requested, private_source_id),)
            if key in cache.entry_generations
        )
        if not generations:
            return None
        first = generations[0]
        if any(generation is not first for generation in generations[1:]):
            # A cache created by an older process may have allowed AUTO and its
            # explicit twin to drift. Do not invent a cursor bridge across two
            # bases; the next snapshot publication repairs them together.
            return None
        return first

    def _delta_entry(
        self,
        previous: materializer.Generation,
        candidate: materializer.Generation,
        range_seconds: int,
        resolution_seconds: int,
        revision_number: int,
        cost_report: dict[str, object],
        *,
        private_source_id: PrivateClientKey = None,
    ) -> CacheEntry:
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
        return CacheEntry(MappingProxyType({
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

    @staticmethod
    def _entry_cursor(entry: CacheEntry) -> RingCursor:
        return (
            int(entry.metadata["source_generation"]),
            int(entry.metadata["cache_generation"]),
        )

    def _publish_warm_views_locked(
        self,
        entries: Mapping[CacheKey, CacheEntry],
    ) -> None:
        """Advance exact public views from their served cursor at live cadence."""

        for cache_key, entry in entries.items():
            range_seconds, requested_resolution, private_source_id = cache_key
            if private_source_id is not None or requested_resolution == stats_resolution.AUTO:
                continue
            resolution_seconds = int(requested_resolution)
            key = (range_seconds, resolution_seconds, None)
            previous_state = self._ring_views.get(key)
            if previous_state is None:
                continue
            previous = previous_state.snapshot or previous_state.base
            if (
                previous is None
                or int(previous.metadata["cache_generation"])
                >= int(entry.metadata["cache_generation"])
            ):
                continue
            revision_number = previous_state.revision + 1
            try:
                delta = self._ring_delta_entry(previous, entry, revision_number)
            except RING_READ_ERRORS:
                # The warm snapshot is authoritative even when its exact bridge
                # exceeds protocol bounds. One repair replaces the old cursor.
                delta = None
            self._ring_views[key] = RingViewState(
                snapshot=entry,
                base=previous,
                deltas=(
                    ()
                    if delta is None
                    else self._bounded_delta_chain(previous_state.deltas, delta)
                ),
                revision=revision_number,
                persisted=False,
            )

    def _record_served_public_base_locked(
        self,
        key: DeltaKey,
        served: CacheEntry,
    ) -> None:
        """Retain an exact bridge when a cold response races a warm owner."""

        state = self._ring_views.get(key, RingViewState())
        candidate = state.snapshot
        candidate_persisted = state.persisted
        cache = self._cache
        if cache is not None:
            cache_entry = cache.entries.get(key)
            if (
                cache_entry is not None
                and (
                    candidate is None
                    or int(cache_entry.metadata["cache_generation"])
                    > int(candidate.metadata["cache_generation"])
                )
            ):
                candidate = cache_entry
                candidate_persisted = False
        if (
            candidate is None
            or int(candidate.metadata["cache_generation"])
            <= int(served.metadata["cache_generation"])
        ):
            self._ring_views[key] = replace(state, base=served)
            return

        candidate_cursor = self._entry_cursor(candidate)
        state_cursor = (
            None if state.snapshot is None else self._entry_cursor(state.snapshot)
        )
        revision_number = (
            max(1, state.revision)
            if state_cursor == candidate_cursor
            else state.revision + 1
        )
        try:
            delta = self._ring_delta_entry(served, candidate, revision_number)
        except RING_READ_ERRORS:
            delta = None
        self._ring_views[key] = RingViewState(
            snapshot=candidate,
            base=served,
            deltas=() if delta is None else (delta,),
            revision=revision_number,
            persisted=candidate_persisted,
        )

    @staticmethod
    def _ring_snapshot_request(
        range_seconds: int,
        resolution_seconds: int,
    ) -> protocol.SnapshotRequest:
        return protocol.SnapshotRequest(
            range_seconds,
            resolution_seconds,
            resolution_seconds,
            "ring-publication",
            None,
        )

    def _ring_view_keys(self, resolutions: frozenset[int]) -> tuple[DeltaKey, ...]:
        return tuple(
            (range_seconds, resolution_seconds, None)
            for range_seconds in stats_resolution.RANGE_SECONDS
            for resolution_seconds in stats_resolution.explicit_resolutions(range_seconds)
            if resolution_seconds in resolutions
            and (
                self._view_demanded(range_seconds, resolution_seconds)
                or (
                    resolution_seconds == stats_resolution.auto_resolution(range_seconds)
                    and self._view_demanded(range_seconds, stats_resolution.AUTO)
                )
            )
        )

    def _ring_delta_entry(
        self,
        previous: CacheEntry,
        candidate: CacheEntry,
        revision_number: int,
    ) -> CacheEntry:
        wire = _wire_snapshot_delta(
            json.loads(previous.binary),
            json.loads(candidate.binary),
            revision_number,
        )
        return self._encoded_delta_entry(wire)

    def _encoded_delta_entry(
        self,
        wire: protocol.DeltaWire,
    ) -> CacheEntry:
        protocol.validate_delta(wire)
        binary = self.encoder(wire)
        return CacheEntry(MappingProxyType({
            "ok": True,
            "content_type": "application/json",
            "range_seconds": wire["range_seconds"],
            "resolution_seconds": wire["resolution_seconds"],
            "source_generation": wire["source_generation"],
            "base_cache_generation": wire["base_cache_generation"],
            "cache_generation": wire["cache_generation"],
            "revision": wire["revision"],
            "bytes": len(binary),
        }), binary)

    @staticmethod
    def _bounded_delta_chain(
        entries: tuple[CacheEntry, ...] | list[CacheEntry],
        entry: CacheEntry,
    ) -> tuple[CacheEntry, ...]:
        resolution_seconds = int(entry.metadata["resolution_seconds"])
        return (*entries, entry)[-DELTA_RING_ENTRY_BOUNDS[resolution_seconds]:]

    def _compose_delta_chain(
        self,
        entries: tuple[CacheEntry, ...],
        *,
        after_cache_generation: int,
        after_revision: int,
        current_generation: int,
        current_revision: int,
    ) -> CacheEntry | None:
        """Compose one exact delivery from any cursor in the retained graph."""

        if after_revision > current_revision:
            return None
        by_base = {
            int(entry.metadata["base_cache_generation"]): entry
            for entry in entries
        }
        selected = []
        cursor = after_cache_generation
        while cursor != current_generation:
            entry = by_base.get(cursor)
            if entry is None:
                return None
            selected.append(protocol.validate_delta(json.loads(entry.binary)))
            cursor = int(entry.metadata["cache_generation"])
            if len(selected) > len(entries):
                return None
        if not selected:
            return None

        bucket_ops: dict[tuple[int, int], dict[str, object] | None] = {}
        gap_ops: dict[
            tuple[str, str, str, int | float, int | float],
            dict[str, object] | None,
        ] = {}
        for wire in selected:
            for tombstone in wire["tombstones"]:
                if tombstone["kind"] == "bucket":
                    bucket_ops[(int(tombstone["start"]), int(tombstone["duration"]))] = None
                else:
                    gap_ops[(
                        str(tombstone["family"]),
                        str(tombstone["source_id"]),
                        str(tombstone["epoch"]),
                        tombstone["start"],
                        tombstone["end"],
                    )] = None
            for bucket in wire["buckets"]:
                bucket_ops[(int(bucket["start"]), int(bucket["duration"]))] = bucket
            for gap in wire["no_data"]:
                gap_ops[(
                    str(gap["family"]),
                    str(gap["source_id"]),
                    str(gap["epoch"]),
                    gap["start"],
                    gap["end"],
                )] = gap

        latest = selected[-1]
        composed: protocol.DeltaWire = {
            "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
            "range_seconds": latest["range_seconds"],
            "resolution_seconds": latest["resolution_seconds"],
            "source_generation": latest["source_generation"],
            "base_cache_generation": after_cache_generation,
            "cache_generation": current_generation,
            "revision": after_revision + 1,
            "buckets": [
                item for _key, item in sorted(bucket_ops.items())
                if item is not None
            ],
            "no_data": [
                item for _key, item in sorted(gap_ops.items())
                if item is not None
            ],
            "tombstones": [
                {"kind": "bucket", "start": key[0], "duration": key[1]}
                for key, item in sorted(bucket_ops.items())
                if item is None
            ] + [
                {
                    "kind": "no_data",
                    "family": key[0],
                    "source_id": key[1],
                    "epoch": key[2],
                    "start": key[3],
                    "end": key[4],
                }
                for key, item in sorted(gap_ops.items())
                if item is None
            ],
            "cost_report": latest["cost_report"],
        }
        try:
            return self._encoded_delta_entry(composed)
        except protocol.ProtocolValidationError:
            # Every retained edge was validated above. A valid chain can still
            # exceed a wire-wide bound only after composition; that cursor must
            # repair through the authoritative snapshot instead.
            return None

    def _publish_ring_views(
        self,
        ring_reader: storage.Store,
        candidate: materializer.Generation,
        resolutions: frozenset[int],
    ) -> None:
        """Advance ring owners with bridges built from the exact persisted wires."""

        keys = self._ring_view_keys(resolutions)
        with self.cache_lock:
            cache = self._cache
            bases = {}
            previous_states = {
                key: self._ring_views.get(key, RingViewState())
                for key in keys
            }
            for key in keys:
                range_seconds, resolution_seconds, _private_source_id = key
                state = previous_states[key]
                base = state.snapshot or state.base
                if base is None and cache is not None:
                    base = cache.entries.get((range_seconds, resolution_seconds, None))
                    if base is None and resolution_seconds == stats_resolution.auto_resolution(range_seconds):
                        base = cache.entries.get((range_seconds, stats_resolution.AUTO, None))
                if base is not None:
                    bases[key] = base

        entries = {}
        for key in keys:
            range_seconds, resolution_seconds, _private_source_id = key
            ring_read = self._read_ring_snapshot(
                self._ring_snapshot_request(range_seconds, resolution_seconds),
                reader=ring_reader,
            )
            if ring_read.entry is not None:
                entries[key] = ring_read.entry

        expected_cursor = (candidate.source_generation, candidate.cache_generation)
        with self.cache_lock:
            for key in tuple(self._ring_views):
                if key[1] in resolutions:
                    self._ring_views.pop(key, None)
            self._ring_published_cursors.update({
                resolution_seconds: expected_cursor
                for resolution_seconds in resolutions
            })
            for key, entry in entries.items():
                if self._entry_cursor(entry) != expected_cursor:
                    continue
                previous_state = previous_states[key]
                previous = bases.get(key)
                if (
                    previous is not None
                    and self._entry_cursor(previous) == self._entry_cursor(entry)
                ):
                    # A cursor names one exact wire. Reading the persisted ring after
                    # the wall clock crosses a bucket boundary can shift its window
                    # without advancing the materializer cursor. Keep the already
                    # published wire until a later cursor can carry an exact delta;
                    # otherwise two clients at the same cursor can retain different
                    # open buckets and no delta can safely update both.
                    self._ring_views[key] = RingViewState(
                        snapshot=previous,
                        base=previous_state.base or previous,
                        deltas=previous_state.deltas,
                        revision=previous_state.revision,
                        persisted=True,
                    )
                    continue
                delta = None
                deltas = previous_state.deltas
                revision_number = previous_state.revision
                if (
                    previous is not None
                    and int(previous.metadata["cache_generation"])
                    < int(entry.metadata["cache_generation"])
                ):
                    if self._entry_cursor(previous_state.snapshot or previous) != self._entry_cursor(entry):
                        revision_number += 1
                    try:
                        delta = self._ring_delta_entry(previous, entry, revision_number)
                    except RING_READ_ERRORS:
                        # The persisted snapshot remains authoritative. If an
                        # exact bounded bridge cannot be encoded, the consumer
                        # repairs once through that snapshot instead.
                        delta = None
                        deltas = ()
                    else:
                        deltas = self._bounded_delta_chain(deltas, delta)
                self._ring_views[key] = RingViewState(
                    snapshot=entry,
                    base=previous,
                    deltas=deltas,
                    revision=revision_number,
                    persisted=True,
                )

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
        observed_times = (
            *(item.observed_at for item in observations),
            *(item.observed_at for item in atoms),
            *(item.observed_at for item in tombstones),
        )
        return StatsCurrentService._dirty_cells_at(observed_times)

    @staticmethod
    def _dirty_cells_at(observed_times: tuple[float, ...]) -> set[materializer.DirtyCell]:
        dirty = set()
        for observed_at in observed_times:
            for resolution in stats_resolution.RESOLUTION_CHOICES:
                dirty.add(materializer.DirtyCell(resolution, math.floor(observed_at / resolution) * resolution))
        return dirty

    def _accepted_dirty_cells(
        self,
        observed_times: tuple[float, ...],
    ) -> set[materializer.DirtyCell]:
        dirty = self._dirty_cells_at(observed_times)
        with self.cache_lock:
            generation = None if self._cache is None else self._cache.generation
        if generation is None:
            return dirty
        horizon_starts = {
            layer.resolution: layer.start
            for layer in generation.layers
        }
        return {
            cell
            for cell in dirty
            if horizon_starts[cell.resolution] <= cell.start
        }

    def _append_dirty_cells(
        self,
        result: storage.AppendResult,
    ) -> set[materializer.DirtyCell]:
        """Map accepted facts into cells still owned by a rendered layer."""

        return self._accepted_dirty_cells(result.accepted_original_timestamps)

    def _update_cached_coverage_locked(
        self,
        coverage: tuple[storage.CoverageEpoch, ...],
        unavailable: tuple[storage.UnavailableSpan, ...],
        *,
        accepted_change: bool,
        retention_prune: storage.PruneResult | None,
    ) -> bool:
        """Keep the one warm coverage model aligned with accepted and pruned facts."""

        retention_change = bool(
            retention_prune is not None
            and (
                retention_prune.coverage_epochs_deleted
                or retention_prune.coverage_epochs_clipped
                or retention_prune.unavailable_spans_deleted
                or retention_prune.unavailable_spans_clipped
            )
        )
        if not accepted_change and not retention_change:
            return False
        self._coverage_version += 1
        if retention_change:
            # A delete or clip cannot be represented by the append-only merge.
            # Drop both halves together so the next build pins and normalizes
            # one retained SQLite snapshot instead of serving stale no-data rows.
            self._cached_coverage_epochs = ()
            self._cached_unavailable_spans = ()
            self._coverage_cache_ready = False
        else:
            self._merge_cached_coverage(coverage, unavailable)
        self._pending_coverage_refresh = True
        return True

    def _merge_cached_coverage(
        self,
        coverage: tuple[storage.CoverageEpoch, ...],
        unavailable: tuple[storage.UnavailableSpan, ...],
    ) -> bool:
        """Apply accepted append facts without rescanning immutable coverage history."""

        if not self._coverage_cache_ready:
            return False
        if coverage:
            epochs = {
                (item.family, item.source_id, item.epoch_id): item
                for item in self._cached_coverage_epochs
            }
            epochs.update({
                (item.family, item.source_id, item.epoch_id): item
                for item in coverage
            })
            self._cached_coverage_epochs = tuple(sorted(
                epochs.values(),
                key=lambda item: (item.started_at, item.family, item.source_id, item.epoch_id),
            ))
        if unavailable:
            spans = {
                (item.family, item.source_id, item.epoch_id, item.started_at): item
                for item in self._cached_unavailable_spans
            }
            spans.update({
                (item.family, item.source_id, item.epoch_id, item.started_at): item
                for item in unavailable
            })
            unavailable_spans = tuple(sorted(
                spans.values(),
                key=lambda item: (item.started_at, item.family, item.source_id, item.epoch_id),
            ))
        else:
            unavailable_spans = self._cached_unavailable_spans
        self._cached_coverage_epochs, self._cached_unavailable_spans = (
            materializer.normalize_coverage_model(
                self._cached_coverage_epochs,
                unavailable_spans,
            )
        )
        return True

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

    def _set_collector_context(self, request: Mapping[str, object]) -> dict[str, object]:
        data = _object(request, "collector context request", CONTROL_FIELDS["collector_context"])
        values: dict[str, int] = {}
        for name, minimum, maximum in (
            ("pid", 2, 2**31 - 1),
            ("port", 1, 65535),
            ("owner_generation", 0, 2**63 - 1),
        ):
            value = data[name]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"invalid collector context {name}")
            values[name] = value
        # The ADDRESS, deliberately outside `values`.
        #
        # `values` is the source IDENTITY, and the block below invalidates every host coverage
        # epoch whenever it changes -- correct, because a different pid/port/generation is a
        # different source lifecycle. A control socket is only where to reach that same process.
        # Folding it into `values` would make mere re-addressing look like a new source and reset
        # epochs that are still valid, so it is stored separately and compared by nobody.
        socket_path = data["control_socket"]
        if not isinstance(socket_path, str) or not socket_path.strip():
            raise ValueError("invalid collector context control_socket")
        self.collector_control_socket = socket_path.strip()
        previous = self.collector_context
        self.collector_context = values
        if previous != values:
            self._host_coverage_epochs.clear()
            self._host_gpu_sources.clear()
            self._host_gpu_seen_sources.clear()
            self._host_gpu_roster_owner_generation = None
        self._next_host_cpu_at = self.monotonic()
        self._next_host_gpu_at = self.monotonic()
        self.work_event.set()
        return {"ok": True, **values}

    def _append_records(
        self,
        observations: tuple[storage.Observation, ...] = (),
        atoms: tuple[storage.UsageAtom, ...] = (),
        tombstones: tuple[storage.UsageAtomTombstone, ...] = (),
        coverage: tuple[storage.CoverageEpoch, ...] = (),
        unavailable: tuple[storage.UnavailableSpan, ...] = (),
        *,
        observation_receipt_event_ids: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        """Commit one already-validated typed batch through the sole append path."""

        total = sum(map(len, (observations, atoms, tombstones, coverage, unavailable)))
        if total < 1 or total > protocol.MAX_APPEND_RECORDS:
            raise ValueError(f"append requires 1..{protocol.MAX_APPEND_RECORDS} records")
        # A browser posting its telemetry is a live private client: appending
        # counts as demand so its private view is pre-encoded before its first
        # snapshot, while clients that stopped posting AND requesting age out of
        # the private encode/delta multiplier after the grace.
        for observation in observations:
            if observation.family == "browser":
                self._record_private_demand(observation.source_id)
        if self.writer is None:
            raise storage.StatsCurrentError("stats store is not open")
        with self.work_lock:
            append_now = self.clock()
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
            accepted = sum((result.observations_accepted, result.usage_atoms_accepted,
                            result.usage_tombstones_accepted, result.coverage_changed,
                            result.unavailable_spans_accepted))
            pruned = 0 if result.retention_prune is None else result.retention_prune.changed
            self._usage_atoms_accepted += result.usage_atoms_accepted
            if result.usage_atoms_accepted:
                self._last_usage_atom_accepted_at = append_now
            if accepted or pruned:
                dirty = self._append_dirty_cells(result)
                self._latest_source_generation = max(self._latest_source_generation, result.source_generation)
                self._last_source_commit_at = append_now
                self._pending_dirty.update(dirty)
                self._stage_ring_cells_locked(dirty, result.source_generation)
                self._update_cached_coverage_locked(
                    coverage,
                    unavailable,
                    accepted_change=bool(
                        result.coverage_changed or result.unavailable_spans_accepted
                    ),
                    retention_prune=result.retention_prune,
                )
            self._append_browser_failure_log(observations, result.accepted_observation_ids)
        if accepted or pruned:
            self.work_event.set()
        self._append_requests += 1
        self._usage_attribution_conflicts += result.usage_attribution_conflicts
        duplicates = sum((
            result.observations_duplicate, result.usage_atoms_duplicate,
            result.usage_tombstones_duplicate, result.coverage_unchanged,
            result.unavailable_spans_duplicate,
        ))
        counts = asdict(result)
        counts.pop("accepted_observation_ids")
        counts.pop("accepted_original_timestamps")
        counts.pop("retention_cutoff")
        counts.pop("retention_prune")
        response: dict[str, object] = {
            "ok": True,
            "source_generation": result.source_generation,
            "accepted": accepted,
            "duplicates": duplicates,
            "counts": counts,
        }
        if observation_receipt_event_ids is not None:
            if len(observation_receipt_event_ids) != len(observations):
                raise ValueError("observation receipt identities disagree with the batch")
            accepted_observation_ids = set(result.accepted_observation_ids)
            response["observation_receipts"] = [
                {
                    "event_id": receipt_event_id,
                    "disposition": "accepted" if observation.event_id in accepted_observation_ids else "duplicate",
                }
                for observation, receipt_event_id in zip(observations, observation_receipt_event_ids, strict=True)
            ]
        return response

    @property
    def browser_failure_log_path(self) -> Path:
        """Persistent, statsd-owned companion log for browser failures."""

        return self.database_path.with_name(f"{self.database_path.stem}.browser-failures.jsonl")

    def _append_browser_failure_log(
        self,
        observations: tuple[storage.Observation, ...],
        accepted_event_ids: tuple[str, ...],
    ) -> None:
        accepted = set(accepted_event_ids)
        rows = [
            observation for observation in observations
            if observation.event_id in accepted
            and observation.family == "browser"
            and observation.payload.get("kind") in {"error", "unhandledrejection"}
        ]
        if not rows:
            return
        path = self.browser_failure_log_path
        lines = []
        for observation in rows:
            payload = observation.payload
            row = {
                "timestamp": observation.observed_at,
                "signature": payload["signature"],
                "source": payload["source"],
                "line": payload.get("line"),
                "column": payload.get("column"),
                "message": payload["message"],
            }
            if "stack" in payload:
                row["stack"] = payload["stack"]
            if "provenance" in payload:
                row["provenance"] = payload["provenance"]
            lines.append(json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n")
        encoded = "".join(lines).encode("utf-8")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size + len(encoded) > BROWSER_FAILURE_LOG_MAX_BYTES:
                backup = path.with_suffix(path.suffix + ".1")
                backup.unlink(missing_ok=True)
                path.replace(backup)
            with path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            self._record_failure("browser_failure_log", error)

    def _append(self, request: Mapping[str, object]) -> dict[str, object]:
        data = _object(request, "append request", APPEND_FIELDS)
        raw_groups = (
            _items(data["observations"], "observations"),
            _items(data["usage_atoms"], "usage_atoms"),
            _items(data["usage_tombstones"], "usage_tombstones"),
            _items(data["coverage_epochs"], "coverage_epochs"),
            _items(data["unavailable_spans"], "unavailable_spans"),
        )
        return self._append_records(
            tuple(_observation(item) for item in raw_groups[0]),
            tuple(_usage_atom(item) for item in raw_groups[1]),
            tuple(_usage_tombstone(item) for item in raw_groups[2]),
            tuple(_coverage(item) for item in raw_groups[3]),
            tuple(_unavailable(item) for item in raw_groups[4]),
        )

    def _browser_upload(
        self,
        request: Mapping[str, object],
        request_binary: bytes,
    ) -> dict[str, object]:
        if not 1 <= len(request_binary) <= 128 * 1024:
            raise ValueError("browser upload must contain 1..131072 bytes")
        try:
            payload = json.loads(request_binary.decode("utf-8"))
            parsed = observations.parse_browser_observations(
                payload,
                client_binding_secret=common.AUTH_COOKIE_SECRET,
                authenticated_username=str(request["authenticated_username"]),
            )
        except observations.BrowserObservationUpgradeRequired:
            return protocol.upgrade_required_response(
                storage.MIN_WRITER_PROTOCOL,
                storage.SCHEMA_VERSION,
                str(storage.MIN_WRITER_BUILD),
            )
        receipt_event_ids = tuple(str(item["event_id"]).strip() for item in payload["observations"])
        if len(set(receipt_event_ids)) != len(receipt_event_ids):
            raise ValueError("browser observation event IDs must be unique within one upload")
        response = self._append_records(parsed, observation_receipt_event_ids=receipt_event_ids)
        if response.get("ok") is True:
            with self.work_lock:
                self._browser_reports_accepted += 1
                self._browser_observations_accepted += int(response["counts"]["observations_accepted"])
                self._last_browser_report_accepted_at = self.clock()
        return response

    def _browser_observation_status(self) -> dict[str, object]:
        if self.writer is None:
            raise storage.StatsCurrentError("stats store is not open")
        with self.work_lock:
            reports = self._browser_reports_accepted
            observations_accepted = self._browser_observations_accepted
            last_accepted_at = self._last_browser_report_accepted_at
            unchanged_cell_materialization = self._statsd_unchanged_cell_materialization
        return {
            "ok": True,
            "receipt_scope": "statsd_process",
            "receipt_scope_started_at": self.started_at,
            "accepted_reports": reports,
            "accepted_observations": observations_accepted,
            "last_accepted_at": last_accepted_at or None,
            "last_accepted_age_seconds": round(max(0.0, self.clock() - last_accepted_at), 3) if last_accepted_at else None,
            "owner_counters": {
                "statsd_unchanged_cell_materialization": unchanged_cell_materialization,
            },
            **self.writer.browser_observation_status(self.clock()),
        }

    def _read_ring_snapshot(
        self,
        parsed: protocol.SnapshotRequest,
        *,
        reader: storage.Store | None = None,
    ) -> RingSnapshotRead:
        ring_reader = self.writer if reader is None else reader
        if ring_reader is None:
            return RingSnapshotRead(None, "store_unavailable")
        resolution_seconds = parsed.resolution_seconds
        window_end = (
            math.floor(self.clock() / resolution_seconds) * resolution_seconds
            + resolution_seconds
        )
        try:
            window = ring_reader.read_ring_window(
                range_seconds=parsed.range_seconds,
                resolution_seconds=resolution_seconds,
                window_end=window_end,
            )
            if window.ring_generation <= 0:
                return RingSnapshotRead(None, "ring_unfilled")
            decoded = tuple(_decode_ring_bucket(row) for row in window.rows)
            latest = max(
                decoded,
                key=lambda item: (
                    item.ring_generation,
                    item.cache_generation,
                    item.wire["start"],
                ),
                default=None,
            )
            if latest is None:
                generated_at = window.published_at
                cache_generation = min(
                    MAX_SAFE_INTEGER,
                    int(window.published_at * 1_000) + window.ring_generation,
                )
            else:
                if latest.ring_generation != window.ring_generation:
                    return RingSnapshotRead(None, "pair_unavailable")
                generated_at = latest.generated_at
                cache_generation = latest.cache_generation
            right_edge_start = window.window_end - resolution_seconds
            gap_starts = set(window.missing_bucket_starts)
            gap_starts.update(
                int(item.wire["start"])
                for item in decoded
                if item.wire["open"] and item.wire["start"] != right_edge_start
            )
            decoded_by_start = {
                int(item.wire["start"]): item
                for item in decoded
            }
            selected = tuple(
                (
                    _ring_gap_bucket(
                        start,
                        resolution_seconds,
                        cache_generation=cache_generation,
                        generated_at=generated_at,
                        ring_generation=window.ring_generation,
                    )
                    if start in gap_starts
                    else decoded_by_start[start]
                )
                for start in range(
                    window.window_start,
                    window.window_end,
                    resolution_seconds,
                )
            )
            summaries = tuple(
                item.view
                for item in selected
                if not gap_starts
                and item.view is not None
                and item.view["range_seconds"] == parsed.range_seconds
                and item.view["window_end"] == window.window_end
            )
            if len(summaries) > 1:
                raise ValueError("ring window contains duplicate view summaries")
            if summaries:
                cost_report = summaries[0]["cost_report"]
            else:
                layer = materializer.Layer(
                    resolution_seconds,
                    window.window_start,
                    window.window_end,
                    tuple(_materialized_ring_bucket(item) for item in selected),
                    (),
                )
                cost_report = materializer.build_cost_report(layer)
            wire: protocol.SnapshotWire = {
                "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
                "range_seconds": parsed.range_seconds,
                "requested_resolution": parsed.resolution,
                "resolution_seconds": resolution_seconds,
                "window_start": window.window_start,
                "window_end": window.window_end,
                "generated_at": generated_at,
                "source_generation": window.source_generation,
                "cache_generation": cache_generation,
                "rightmost_open": bool(selected and selected[-1].wire["open"]),
                "buckets": [item.wire for item in selected],
                "no_data": _merge_ring_no_data(selected),
                "cost_report": cost_report,
            }
            protocol.validate_snapshot(wire)
        except RING_READ_ERRORS as error:
            return RingSnapshotRead(None, type(error).__name__)
        binary = self.encoder(wire)
        metadata = MappingProxyType({
            "ok": True,
            "content_type": "application/json",
            "range_seconds": parsed.range_seconds,
            "requested_resolution": parsed.resolution,
            "resolution_seconds": resolution_seconds,
            "source_generation": window.source_generation,
            "cache_generation": cache_generation,
            "bytes": len(binary),
        })
        return RingSnapshotRead(CacheEntry(metadata, binary), "")

    def _published_snapshot_owner(
        self,
        parsed: protocol.SnapshotRequest,
        private_source_id: str,
    ) -> PublishedSnapshotOwner:
        """Resolve snapshots and deltas through one warm cursor owner."""

        with self.cache_lock:
            cache = self._cache
            if cache is None:
                return PublishedSnapshotOwner(False, None, False, True, None)
            private_key = (parsed.range_seconds, parsed.resolution, private_source_id)
            public_key = (parsed.range_seconds, parsed.resolution, None)
            selected_key = private_key if private_key in cache.entries else public_key
            public = selected_key[2] is None
            if not public:
                return PublishedSnapshotOwner(
                    True,
                    cache.entries.get(selected_key),
                    False,
                    False,
                    None,
                )
            ring_cursor = self._ring_published_cursors.get(parsed.resolution_seconds)
            ring_key = (parsed.range_seconds, parsed.resolution_seconds, None)
            ring_state = self._ring_views.get(ring_key)
            shared_entry = None if ring_state is None else ring_state.snapshot
            if shared_entry is not None:
                shared_cursor = self._entry_cursor(shared_entry)
                cache_entry = cache.entries.get(selected_key)
                cache_cursor = None if cache_entry is None else self._entry_cursor(cache_entry)
                if ring_state.persisted:
                    if shared_cursor != ring_cursor:
                        shared_entry = None
                elif shared_cursor != cache_cursor:
                    shared_entry = None
                else:
                    return PublishedSnapshotOwner(
                        True,
                        shared_entry,
                        False,
                        True,
                        None,
                    )
            return PublishedSnapshotOwner(
                True,
                shared_entry if ring_cursor is not None else cache.entries.get(selected_key),
                ring_cursor is not None,
                True,
                ring_cursor,
            )

    def _clear_ring_resolution_locked(self, resolution_seconds: int) -> None:
        self._ring_published_cursors.pop(resolution_seconds, None)
        for key in tuple(self._ring_views):
            if key[1] == resolution_seconds:
                self._ring_views.pop(key, None)

    def _public_entry_for_request(
        self,
        entry: CacheEntry,
        requested_resolution: protocol.RequestedResolution,
    ) -> CacheEntry:
        if entry.metadata["requested_resolution"] == requested_resolution:
            return entry
        wire = protocol.validate_snapshot(json.loads(entry.binary))
        aliased: protocol.SnapshotWire = {
            **wire,
            "requested_resolution": requested_resolution,
        }
        protocol.validate_snapshot(aliased)
        binary = self.encoder(aliased)
        return CacheEntry(MappingProxyType({
            **entry.metadata,
            "requested_resolution": requested_resolution,
            "bytes": len(binary),
        }), binary)

    def _snapshot(self, request: Mapping[str, object]) -> tuple[dict[str, object], bytes]:
        return StatsSnapshotProjector._snapshot(self, request)

    @staticmethod
    def _backfill_status_signature(status: Mapping[str, object]) -> tuple[object, ...]:
        scan = status["scan"]
        if not isinstance(scan, Mapping):
            raise ValueError("usage atom backfill scan must be an object")
        reasons = scan["rejection_reasons"]
        if not isinstance(reasons, Mapping):
            raise ValueError("usage atom backfill rejection reasons must be an object")
        return (
            status["state"],
            status["sources"],
            status["missing"],
            scan["files_read"],
            scan["records_parsed"],
            scan["atoms_emitted"],
            scan["atoms_accepted"],
            scan["atoms_rejected"],
            tuple(sorted((str(reason), count) for reason, count in reasons.items())),
        )

    def _snapshot_body_with_backfill_status(self, body: bytes) -> bytes:
        with self._snapshot_body_decoration_lock:
            status = self._usage_atom_backfill
            if status is None:
                return body
            signature = self._backfill_status_signature(status)
            cached = self._snapshot_body_decoration_cache
            if cached is not None and cached.status_signature == signature:
                same_base = cached.base is body
                if not same_base:
                    digest = hashlib.sha256(body).digest()
                    same_base = cached.base_digest == digest and cached.base == body
                if same_base:
                    self._snapshot_body_decoration_hits += 1
                    return cached.body
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("cached snapshot body must be an object")
            payload["usage_atom_backfill"] = status
            decorated = self.encoder(payload)
            self._snapshot_body_decoration_cache = DecoratedSnapshotBody(
                base=body,
                base_digest=hashlib.sha256(body).digest(),
                status_signature=signature,
                body=decorated,
            )
            self._snapshot_body_decoration_builds += 1
            return decorated

    def _set_usage_atom_backfill_status(self, request: Mapping[str, object]) -> dict[str, object]:
        data = _object(request, "usage_atom_backfill request", CONTROL_FIELDS["usage_atom_backfill"])
        state = str(data["state"])
        sources = data["sources"]
        missing = data["missing"]
        scan = data["scan"]
        if state not in {"pending", "complete"} or isinstance(sources, bool) or isinstance(missing, bool) or not isinstance(sources, int) or not isinstance(missing, int) or sources < 0 or missing < 0 or missing > sources:
            raise ValueError("invalid usage_atom_backfill status")
        if not isinstance(scan, dict):
            raise ValueError("invalid usage_atom_backfill scan")
        expected = {"files_read", "records_parsed", "atoms_emitted", "atoms_accepted", "atoms_rejected", "rejection_reasons"}
        if set(scan) != expected or any(isinstance(scan[name], bool) or not isinstance(scan[name], int) or scan[name] < 0 for name in expected - {"rejection_reasons"}):
            raise ValueError("invalid usage_atom_backfill scan")
        reasons = scan["rejection_reasons"]
        if not isinstance(reasons, dict) or any(not isinstance(reason, str) or not reason or isinstance(count, bool) or not isinstance(count, int) or count <= 0 for reason, count in reasons.items()):
            raise ValueError("invalid usage_atom_backfill rejection reasons")
        if scan["atoms_accepted"] + scan["atoms_rejected"] != scan["atoms_emitted"] or sum(reasons.values()) != scan["atoms_rejected"]:
            raise ValueError("invalid usage_atom_backfill atom counts")
        status = {
            "state": state,
            "sources": sources,
            "missing": missing,
            "scan": {**scan, "rejection_reasons": dict(reasons)},
        }
        signature = self._backfill_status_signature(status)
        with self._snapshot_body_decoration_lock:
            previous = self._usage_atom_backfill
            previous_signature = (
                None if previous is None else self._backfill_status_signature(previous)
            )
            self._usage_atom_backfill = status
            if previous_signature != signature:
                self._snapshot_body_decoration_cache = None
        return {"ok": True}

    def _delta(self, request: Mapping[str, object]) -> tuple[dict[str, object], bytes]:
        return StatsDeltaProjector._delta(self, request)

    def _status(self) -> dict[str, object]:
        return StatsStatusProjector._status(self)

    def handle_with_binary(self, request: dict[str, object], _request_binary: bytes = b"") -> tuple[dict[str, object], bytes]:
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
            response = STATS_COMMAND_ROUTER.dispatch(self, str(action), request, _request_binary)
            return response if response is not None else (protocol.unsupported_response(f"unsupported stats action {action!r}"), b"")
        except protocol.UnsupportedRequest as error:
            return error.response, b""
        except REQUEST_ERRORS as error:
            return protocol.unsupported_response(str(error)), b""

    def _handle_ping(self, _request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return {"ok": True, "version": storage.MIN_WRITER_PROTOCOL, "schema_generation": storage.SCHEMA_VERSION, "build": storage.MIN_WRITER_BUILD, "code_revision": revision.CURRENT_CODE_REVISION, "pid": os.getpid(), "started_at": self.started_at}, b""

    def _handle_status(self, _request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._status(), b""

    def _handle_browser_profiles(self, _request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        if self.writer is None:
            raise storage.StatsCurrentError("stats store is not open")
        items = self.writer.recent_browser_profiles(MAX_BROWSER_PROFILES)
        return {"ok": True, "profiles": {"retained": len(items), "maximum": MAX_BROWSER_PROFILES, "items": items, "queue_ms": _browser_queue_summary(items)}, "observation_status": {key: value for key, value in self._browser_observation_status().items() if key != "ok"}}, b""

    def _handle_lease(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return acquire_client_lease(self.leases, request["client_pid"], request["lease_id"]), b""

    def _handle_collector_context(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._set_collector_context(request), b""

    def _handle_release(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return release_client_lease(self.leases, request["lease_id"]), b""

    def _handle_usage_atom_backfill(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._set_usage_atom_backfill_status(request), b""

    def _handle_browser_upload(self, request: dict[str, object], body: bytes) -> tuple[dict[str, object], bytes]:
        return self._browser_upload(request, body), b""

    def _handle_append(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._append(request), b""

    def _handle_snapshot(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._snapshot(request)

    def _handle_delta(self, request: dict[str, object], _body: bytes) -> tuple[dict[str, object], bytes]:
        return self._delta(request)

    def _on_client(self) -> None:
        # Deliberately does NOT prune. Retention cleanup is nightly maintenance,
        # and running it here would charge one unlucky browser request the whole
        # delete while the observer's next sample waits on the same writer lock.
        self.last_client_at = self.monotonic()

    def _resolved_prune_time(self) -> prune_schedule.PruneTime:
        """Re-read the preference so a change takes effect without a restart."""

        try:
            configured = self._prune_time_reader()
        except OSError as error:
            # The preference is unreadable, not absent. Keep cleaning up on the
            # default schedule and name the failure in status; a cleanup that
            # stops because a file could not be read is invisible until the disk
            # is full.
            self._prune_preference_error = type(error).__name__[:64]
            return prune_schedule.resolve_local_time(prune_schedule.DEFAULT_PRUNE_LOCAL_TIME)
        self._prune_preference_error = ""
        return prune_schedule.resolve_local_time(configured)

    def _prune_if_due(self, writer: storage.Store | None = None) -> bool:
        """Run an owed daily prune or the bounded cutoff sweep between runs.

        This is the ONLY pruner. The materializer worker calls it with that
        thread's SQLite writer, never on a request, and asks the schedule at
        most once per PRUNE_CHECK_SECONDS.
        """

        now_monotonic = self.monotonic()
        prune_writer = self.writer if writer is None else writer
        if now_monotonic < self._next_prune_check_at or prune_writer is None:
            return False
        self._next_prune_check_at = now_monotonic + PRUNE_CHECK_SECONDS
        self._prune_time = self._resolved_prune_time()
        now = self.clock()
        # prune_schedule still owns the configured daily occurrence. The second
        # condition owns the retention ceiling between occurrences; without it,
        # a healthy once-nightly delete necessarily permits almost one extra day.
        scheduled_due = prune_schedule.is_due(now, self._last_pruned_at, self._prune_time)
        cutoff_age = now - self._last_pruned_at
        cutoff_sweep_due = cutoff_age >= PRUNE_CHECK_SECONDS
        if not scheduled_due and not cutoff_sweep_due:
            # The monotonic wake can arrive fractionally before the wall-clock
            # cutoff. Preserve the normal cadence, but do not skip that cutoff
            # for another full minute.
            cutoff_remaining = PRUNE_CHECK_SECONDS - cutoff_age
            self._next_prune_check_at = min(
                self._next_prune_check_at,
                now_monotonic + max(0.0, cutoff_remaining),
            )
            return False
        due_at = prune_schedule.most_recent_occurrence(now, self._prune_time)
        started = self.monotonic()
        with self.work_lock:
            previous_source_generation = self._latest_source_generation
            # One timestamp decides due-ness, the cutoff, and what is persisted,
            # so the three can never disagree.
            result = prune_writer.prune(now=now)
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
                cutoff = now - storage.RETENTION_SECONDS
                cutoff_dirty = {
                    materializer.DirtyCell(
                        resolution, math.floor(cutoff / resolution) * resolution
                    )
                    for resolution in stats_resolution.RESOLUTION_CHOICES
                }
                self._pending_dirty.update(cutoff_dirty)
                self._stage_ring_cells_locked(cutoff_dirty, result.source_generation)
                self._update_cached_coverage_locked(
                    (),
                    (),
                    accepted_change=False,
                    retention_prune=result,
                )
        self._prunes += 1
        self._last_prune_at = self.clock()
        self._last_prune_seconds = max(0.0, self.monotonic() - started)
        self._last_prune_due_at = due_at
        # The store persisted this same instant, so a restart reads back one
        # answer rather than two that can disagree.
        self._last_pruned_at = now
        self.work_event.set()
        return True

    def _idle(self) -> bool:
        reap_dead_client_leases(self.leases)
        with self.work_lock:
            pending = (
                self._pending_full
                or bool(self._pending_dirty)
                or self._pending_coverage_refresh
                or bool(self._pending_ring_dirty)
            )
        idle = (
            not self.leases
            and not self._building
            and not pending
            and self.monotonic() - self.last_client_at >= self.idle_seconds
        )
        return idle

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




class StatsSnapshotProjector:
    """Named projection owner retaining the StatsCurrentService context contract."""

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

            owner = self._published_snapshot_owner(parsed, private_source_id)
            cold_ring_entry = None
            if not owner.cache_present:
                cold_ring_entry = self._read_ring_snapshot(parsed).entry
                # A worker may have warmed the cache while SQLite reconstructed
                # the cold ring. Re-resolve so the old persisted cursor cannot
                # mask the warm materializer owner and return a false 304.
                owner = self._published_snapshot_owner(parsed, private_source_id)

            for _attempt in range(2):
                if not (
                    owner.cache_present
                    and owner.ring_current
                    and owner.entry is None
                    and owner.ring_cursor is not None
                ):
                    break
                expected_cursor = owner.ring_cursor
                ring_read = self._read_ring_snapshot(parsed)
                ring_key = (parsed.range_seconds, parsed.resolution_seconds, None)
                with self.cache_lock:
                    if self._ring_published_cursors.get(parsed.resolution_seconds) == expected_cursor:
                        if (
                            ring_read.entry is not None
                            and self._entry_cursor(ring_read.entry) == expected_cursor
                        ):
                            previous = self._ring_views.get(ring_key, RingViewState())
                            self._ring_views[ring_key] = replace(
                                previous,
                                snapshot=ring_read.entry,
                                persisted=True,
                            )
                        else:
                            # SQLite is authoritative for the remembered cursor.
                            # Drop the whole resolution owner atomically and fall
                            # back to the exact per-view materializer entry.
                            self._clear_ring_resolution_locked(parsed.resolution_seconds)
                owner = self._published_snapshot_owner(parsed, private_source_id)

            entry = (
                cold_ring_entry
                if not owner.cache_present
                else owner.entry
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
                return finish(
                    protocol.pending_response(parsed, 1),
                    b"",
                    "pending",
                )
            if owner.public:
                ring_key = (parsed.range_seconds, parsed.resolution_seconds, None)
                with self.cache_lock:
                    self._record_served_public_base_locked(ring_key, entry)
            if owner.public:
                entry = self._public_entry_for_request(entry, parsed.resolution)
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
            body = self._snapshot_body_with_backfill_status(entry.binary)
            self._snapshot_bytes += len(body)
            return finish(entry.metadata, body, "hit")
        finally:
            self._record_request_latency("snapshot", started)


class StatsDeltaProjector:
    """Named projection owner retaining the StatsCurrentService context contract."""

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
                selected_source = (
                    private_source_id
                    if (
                        cache is not None
                        and (
                            parsed.range_seconds,
                            parsed.resolution_seconds,
                            private_source_id,
                        ) in cache.entries
                    )
                    else None
                )
                ring_key = (parsed.range_seconds, parsed.resolution_seconds, None)
                ring_cursor = (
                    self._ring_published_cursors.get(parsed.resolution_seconds)
                    if selected_source is None else None
                )
                resolution_generation = (
                    None
                    if cache is None
                    else self._delta_generation_owner(
                        cache,
                        parsed.range_seconds,
                        parsed.resolution_seconds,
                        selected_source,
                    )
                )
                shared_state = (
                    self._ring_views.get(ring_key)
                    if selected_source is None else None
                )
                shared_entry = (
                    None
                    if shared_state is None
                    else shared_state.snapshot or (
                        shared_state.base if cache is None else None
                    )
                )
                shared_cursor = None if shared_entry is None else self._entry_cursor(shared_entry)
                generation_cursor = (
                    None
                    if resolution_generation is None else (
                        resolution_generation.source_generation,
                        resolution_generation.cache_generation,
                    )
                )
                shared_current = shared_cursor is not None and (
                    cache is None
                    or (shared_state.persisted and shared_cursor == ring_cursor)
                    or (not shared_state.persisted and shared_cursor == generation_cursor)
                )
                if shared_current:
                    current_source_generation, current_generation = shared_cursor
                    entries = shared_state.deltas
                    current_revision = shared_state.revision
                else:
                    delta_key = (
                        parsed.range_seconds,
                        parsed.resolution_seconds,
                        selected_source,
                    )
                    entries = tuple(self._delta_entries.get(delta_key, ()))
                    current_revision = self._delta_revisions.get(delta_key, 0)
                    if resolution_generation is None:
                        current_source_generation = current_generation = None
                    else:
                        current_source_generation = resolution_generation.source_generation
                        current_generation = resolution_generation.cache_generation
            if current_generation is None:
                self._delta_pending += 1
                return finish({
                    "status": "pending",
                    "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
                    "retry_after_seconds": 1,
                    "reason": "materialization is not ready",
                }, b"", "pending")
            if parsed.after_cache_generation == current_generation:
                self._delta_not_modified += 1
                return finish({
                    "ok": True,
                    "not_modified": True,
                    "cache_generation": current_generation,
                    "source_generation": current_source_generation,
                }, b"", "not_modified")
            entry = self._compose_delta_chain(
                entries,
                after_cache_generation=parsed.after_cache_generation,
                after_revision=parsed.after_revision,
                current_generation=current_generation,
                current_revision=current_revision,
            )
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


class StatsStatusProjector:
    """Named projection owner retaining the StatsCurrentService context contract."""

    def _status(self) -> dict[str, object]:
        with self.work_lock:
            materializer_pending = (
                self._pending_full
                or bool(self._pending_dirty)
                or self._pending_coverage_refresh
            )
            pending = materializer_pending or bool(self._pending_ring_dirty)
            pending_full = self._pending_full
            pending_coverage = self._pending_coverage_refresh
            dirty, latest_source = len(self._pending_dirty), self._latest_source_generation
            ring_dirty = len(self._pending_ring_dirty)
            next_ring_flush_at = self._next_ring_flush_at
            ring_waiting_for_source = self._ring_waiting_for_source
            last_source_commit_at = self._last_source_commit_at
            usage_atoms_accepted = self._usage_atoms_accepted
            last_usage_atom_accepted_at = self._last_usage_atom_accepted_at
            usage_identity_conflict_attempts = self._usage_identity_conflict_attempts
            unchanged_cell_materialization = self._statsd_unchanged_cell_materialization
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
            ring_deltas = {
                key: state.deltas
                for key, state in self._ring_views.items()
                if state.deltas
            }
            delta_keys = len(set(self._delta_entries) | set(ring_deltas))
            delta_entries = (
                sum(len(items) for items in self._delta_entries.values())
                + sum(len(items) for items in ring_deltas.values())
            )
            shared_delta_bytes = sum(
                len(item.binary)
                for key, items in self._delta_entries.items()
                if key[2] is None
                for item in items
            ) + sum(
                len(item.binary)
                for items in ring_deltas.values()
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
        # Same rule as by_resolution below: a cache built from an empty source is
        # not a published generation. Reporting one lets a consumer conclude data
        # exists when the chart is blank, which is the defect this box exists for.
        cache_generation = (
            0 if cache is None or cache.generation.source_generation <= 0
            else cache.generation.cache_generation
        )
        # A resolution is only reported as published when it actually carries
        # source data. source_generation == 0 means the materializer produced an
        # empty payload -- succeeded with nothing in it -- and reporting that as a
        # published generation is how `agent_tokens` and `agent_status` claimed
        # success while the chart stayed blank. An empty payload and a real one
        # must not look the same on the wire.
        resolution_generations = {} if cache is None else {
            f"{resolution}s": {
                "source": generation.source_generation,
                "cache": generation.cache_generation,
                "published_at": generation.generated_at,
                "cadence_seconds": stats_resolution.live_cadence_seconds(resolution),
            }
            for resolution, generation in sorted(cache.resolution_generations.items())
            if generation.source_generation > 0
        }
        warm_total = sum(1 + len(stats_resolution.explicit_resolutions(value)) for value in stats_resolution.RANGE_SECONDS)
        next_prune_check_in = max(0.0, self._next_prune_check_at - self.monotonic())
        next_vacuum_in = max(0.0, self._next_vacuum_at - self.monotonic())
        next_ring_in = (
            None
            if next_ring_flush_at is None
            else max(0.0, next_ring_flush_at - self.monotonic())
        )
        materializer_depth = int(pending_full) + dirty + int(pending_coverage)
        materializer_state = (
            "failed" if self._last_failure_component == "materializer"
            else "building" if self._building
            else "dirty" if materializer_pending
            else "ready" if cache is not None
            else "warming"
        )
        with self.trace_lock:
            request_traces = tuple(dict(item) for item in self._request_traces)
        wal_path = self.database_path.with_name(f"{self.database_path.name}-wal")
        try:
            wal_allocated_bytes = wal_path.stat().st_size
        except FileNotFoundError:
            wal_allocated_bytes = 0
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
            "owner_counters": {
                "statsd_unchanged_cell_materialization": unchanged_cell_materialization,
            },
            "ring_writer": {
                "cadence_seconds": RING_FLUSH_SECONDS,
                "sole_writer": self.writer is not None,
                "serving_reads": "published_cache",
                "pending_cells": ring_dirty,
                "waiting_for_source_generation": ring_waiting_for_source,
                "publications": self._ring_publications,
                "buckets_published": self._ring_buckets_published,
                "last_source_generation": self._last_ring_source_generation,
                "last_at": self._last_ring_published_at,
                "last_seconds": round(self._last_ring_publish_seconds, 6),
                "next_in_seconds": (
                    None if next_ring_in is None else round(next_ring_in, 3)
                ),
                "failure": self._ring_failure,
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
                "issue_kinds": self._migration_issue_kinds,
                "issue_records": self._migration_issue_records,
                "skipped_history": "unsupported_legacy_database" in self._migration_issue_kinds,
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
            "retention_prune": {
                "retention_seconds": storage.RETENTION_SECONDS,
                "cutoff_sweep_interval_seconds": PRUNE_CHECK_SECONDS,
                "display_window_seconds": stats_resolution.MAX_RANGE_SECONDS,
                "at_local_time": self._prune_time.text,
                "configured_local_time": self._prune_time.configured,
                # A preference that could not be used says so here. Cleanup keeps
                # running on the default schedule either way.
                "preference_fell_back": self._prune_time.fell_back,
                "preference_error": self._prune_preference_error,
                "check_interval_seconds": PRUNE_CHECK_SECONDS,
                "next_check_at": self.clock() + next_prune_check_in,
                "next_check_in_seconds": round(next_prune_check_in, 3),
                "next_at": prune_schedule.next_occurrence(self.clock(), self._prune_time),
                "due_at": prune_schedule.most_recent_occurrence(self.clock(), self._prune_time),
                "last_pruned_at": self._last_pruned_at,
                "overdue": prune_schedule.is_due(
                    self.clock(), self._last_pruned_at, self._prune_time
                ),
                "count": self._prunes,
                "last_at": self._last_prune_at,
                "last_seconds": round(self._last_prune_seconds, 6),
                "last_due_at": self._last_prune_due_at,
            },
            "wal": {
                "allocated_bytes": wal_allocated_bytes,
                "allocation_ceiling_bytes": storage.WAL_ALLOCATION_CEILING_BYTES,
                "autocheckpoint_pages": storage.WAL_AUTOCHECKPOINT_PAGES,
            },
            "vacuum": {
                "interval_seconds": VACUUM_INTERVAL_SECONDS,
                "jitter_seconds": round(self._vacuum_jitter_seconds, 3),
                "retry_seconds": VACUUM_RETRY_SECONDS,
                "count": self._vacuum_count,
                "last_at": self._last_vacuumed_at,
                "last_seconds": round(self._last_vacuum_seconds, 6),
                "next_at": self.clock() + next_vacuum_in,
                "next_in_seconds": round(next_vacuum_in, 3),
                "failure": self._vacuum_failure,
            },
            "failure": {
                "component": self._last_failure_component,
                "kind": self._last_failure,
                "at": self._last_failure_at,
            },
            "host_collectors": {
                "context": None if self.collector_context is None else dict(self.collector_context),
                "failures": self._host_collector_failures,
                "last_error": self._last_host_collector_error,
                # `failures` counts raised exceptions only, and a skipped push never raises.
                # Delivery of the web process's own CPU/memory sample needs its own evidence:
                # `attempted` without `delivered` is exactly the state that renders the Daemons
                # web row "never measured", and `last_reason` names the gate that stopped it.
                "cpu_push": self._host_push_status_payload("cpu"),
                "memory_push": self._host_push_status_payload("memory"),
            },
        }


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
