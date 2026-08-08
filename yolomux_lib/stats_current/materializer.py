# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Pure construction and slicing of the four current YO!stats layers."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from dataclasses import replace
from typing import Callable
from typing import Iterable
from typing import Mapping

from . import identity
from .families import CURRENT_FAMILIES
from .families import FAMILY_BY_NAME
from .families import validate_payload
from .pricing import PricingEvidence
from .pricing import UsagePriceProjection
from .protocol import MAX_SAFE_INTEGER
from .protocol import COST_REPORT_DIMENSIONS
from .protocol import COST_REPORT_SCHEMA_VERSION
from .protocol import MAX_COST_DETAIL_AGENTS
from .protocol import MAX_COST_DETAIL_EVIDENCE
from .protocol import MAX_COST_DETAIL_MODELS
from .protocol import validate_cost_report
from . import resolution as stats_resolution
from .storage import Observation
from .storage import CoverageEpoch
from .storage import StoreSnapshot
from .storage import UnavailableSpan
from .storage import UsageAtom
from .storage import normalize_unavailable_spans
from .usage import UsageValidationError
from .usage import normalize_usage_atom


RESOLUTIONS = stats_resolution.RESOLUTION_CHOICES
RANGES = stats_resolution.RANGE_SECONDS
LAYER_SECONDS = {
    resolution: max(value for value in RANGES if stats_resolution.is_supported(value, resolution))
    for resolution in RESOLUTIONS
}
PriceResolver = Callable[[UsageAtom], UsagePriceProjection]
MODEL_TOKEN_DIMENSIONS = ("output", "all", "input", "cache_read", "cache_write")
TOKEN_DETAIL_DIMENSIONS = (
    "input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "reasoning", "other",
)
MAX_PRIVATE_BROWSER_CLIENTS = 4
PUBLIC_EXECUTION_SOURCES = frozenset({
    "claude", "codex", "images", "messages", "responses", "unknown",
})


class MaterializationError(RuntimeError):
    """The normalized current facts cannot form a deterministic generation."""


class UnsupportedSliceError(MaterializationError):
    """A Range/Resolution pair is outside the exact current matrix."""


class StaleGenerationError(MaterializationError):
    """A candidate generation cannot replace the currently published one."""


@dataclass(frozen=True, slots=True)
class SeriesValue:
    name: str
    value: int | float
    source_count: int
    first_observed_at: float
    last_observed_at: float


@dataclass(frozen=True, slots=True)
class CostDimensionValue:
    dimension: str
    tokens: int = 0
    micro_usd: int = 0
    api_list_micro_usd: int = 0


@dataclass(frozen=True, slots=True)
class CostCoverage:
    atoms: int = 0
    tokens: int = 0


@dataclass(frozen=True, slots=True)
class CostAttribution:
    key: str
    provider: str = ""
    model: str = ""
    source: str = ""
    label: str = ""
    dimensions: tuple[CostDimensionValue, ...] = ()
    priced: CostCoverage = CostCoverage()
    unpriced: CostCoverage = CostCoverage()


@dataclass(frozen=True, slots=True)
class CostEvidenceValue:
    key: str
    provider: str
    model: str
    dimension: str
    direction: str
    modality: str
    cache_role: str
    unit: str
    pricing_profile: str
    service_tier: str
    catalog_model: str
    rate_usd: str
    rate_scale: int
    effective_from: str
    source_kind: str
    source_url: str
    catalog_revision: int
    tokens: int = 0
    micro_usd: int = 0
    api_list_micro_usd: int = 0
    priced_atoms: int = 0


@dataclass(frozen=True, slots=True)
class BucketCostDetail:
    dimensions: tuple[CostDimensionValue, ...] = ()
    priced: CostCoverage = CostCoverage()
    unpriced: CostCoverage = CostCoverage()
    models: tuple[CostAttribution, ...] = ()
    agents: tuple[CostAttribution, ...] = ()
    evidence: tuple[CostEvidenceValue, ...] = ()
    omitted_models: int = 0
    omitted_agents: int = 0
    omitted_evidence: int = 0


@dataclass(frozen=True, slots=True)
class Bucket:
    start: int
    duration: int
    series: tuple[SeriesValue, ...]
    source_count: int
    first_observed_at: float | None
    last_observed_at: float | None
    complete: bool
    cost_detail: BucketCostDetail = BucketCostDetail()


@dataclass(frozen=True, slots=True)
class NoData:
    family: str
    source_id: str
    epoch_id: str
    start: float
    end: float
    native_cadence_seconds: float
    reason: str = "coverage_gap"


@dataclass(frozen=True, slots=True)
class Layer:
    resolution: int
    start: int
    end: int
    buckets: tuple[Bucket, ...]
    no_data: tuple[NoData, ...]


def _layer_for(layers: tuple[Layer, ...], resolution: int) -> Layer:
    for layer in layers:
        if layer.resolution == resolution:
            return layer
    raise UnsupportedSliceError(f"resolution {resolution}s is not materialized")


@dataclass(frozen=True, slots=True)
class PrivateOverlay:
    source_id: str
    layers: tuple[Layer, ...]

    def layer(self, resolution: int) -> Layer:
        return _layer_for(self.layers, resolution)


@dataclass(frozen=True, slots=True)
class Generation:
    source_generation: int
    cache_generation: int
    generated_at: float
    observed_until: float
    layers: tuple[Layer, ...]
    private_overlays: tuple[PrivateOverlay, ...] = ()

    def layer(self, resolution: int) -> Layer:
        return _layer_for(self.layers, resolution)

    @property
    def private_source_ids(self) -> tuple[str, ...]:
        return tuple(overlay.source_id for overlay in self.private_overlays)

    def private_layer(self, source_id: str, resolution: int) -> Layer | None:
        for overlay in self.private_overlays:
            if overlay.source_id == source_id:
                return overlay.layer(resolution)
        return None


@dataclass(frozen=True, slots=True)
class DirtyCell:
    resolution: int
    start: int


@dataclass(frozen=True, slots=True)
class _Sample:
    series: str
    operation: str
    value: int | float
    observed_at: float
    source_id: str


@dataclass(frozen=True, slots=True)
class _CostDetailAtom:
    dimension: str
    quantity: int
    is_tokens: bool
    priced: bool
    micro_usd: int | None
    api_list_micro_usd: int | None
    model_key: str
    provider: str
    model: str
    agent_key: str
    agent_source: str
    agent_label: str
    evidence: CostEvidenceValue | None


def resolve_resolution(range_seconds: int, requested: int | str) -> int:
    try:
        return stats_resolution.resolve_requested(range_seconds, requested)
    except ValueError as error:
        raise UnsupportedSliceError(str(error)) from error


def build_generation(
    snapshot: StoreSnapshot,
    *,
    source_generation: int,
    cache_generation: int,
    generated_at: float,
    observed_until: float,
    price_resolver: PriceResolver | None = None,
) -> Generation:
    return _build(
        snapshot, source_generation, cache_generation, generated_at, observed_until,
        price_resolver, None, None,
    )


def update_generation(
    previous: Generation,
    snapshot: StoreSnapshot,
    dirty: Iterable[DirtyCell],
    *,
    source_generation: int,
    cache_generation: int,
    generated_at: float,
    observed_until: float,
    price_resolver: PriceResolver | None = None,
) -> Generation:
    if source_generation < previous.source_generation or cache_generation <= previous.cache_generation:
        raise StaleGenerationError("incremental generation is not newer than its base")
    return _build(
        snapshot, source_generation, cache_generation, generated_at, observed_until,
        price_resolver, previous, frozenset(dirty),
    )


def accept_generation(current: Generation | None, candidate: Generation) -> Generation:
    if current is not None and (
        candidate.cache_generation <= current.cache_generation
        or candidate.source_generation < current.source_generation
    ):
        raise StaleGenerationError("candidate generation is stale")
    return candidate


def slice_generation(
    generation: Generation,
    range_seconds: int,
    requested_resolution: int | str,
    *,
    private_source_id: str | None = None,
) -> Layer:
    resolution = resolve_resolution(range_seconds, requested_resolution)
    layer = _slice_layer(generation.layer(resolution), range_seconds)
    if private_source_id is None:
        return layer
    private = generation.private_layer(private_source_id, resolution)
    return layer if private is None else _merge_layers(layer, _slice_layer(private, range_seconds))


def _slice_layer(layer: Layer, range_seconds: int) -> Layer:
    start = layer.end - range_seconds
    buckets = tuple(bucket for bucket in layer.buckets if bucket.start >= start)
    if len(buckets) != stats_resolution.bucket_count(range_seconds, layer.resolution) or len(buckets) > stats_resolution.MAX_BUCKETS:
        raise MaterializationError("materialized layer cannot satisfy the exact matrix slice")
    gaps = _clip_gaps(layer.no_data, start, layer.end)
    return Layer(layer.resolution, start, layer.end, buckets, gaps)


def _merge_layers(shared: Layer, private: Layer) -> Layer:
    if (shared.resolution, shared.start, shared.end) != (
        private.resolution, private.start, private.end,
    ) or len(shared.buckets) != len(private.buckets):
        raise MaterializationError("private browser overlay does not align with the shared layer")
    buckets = tuple(
        _merge_buckets(shared_bucket, private_bucket)
        for shared_bucket, private_bucket in zip(shared.buckets, private.buckets, strict=True)
    )
    gaps = tuple(sorted(
        (*shared.no_data, *private.no_data),
        key=lambda item: (item.family, item.source_id, item.start, item.end, item.epoch_id),
    ))
    return Layer(shared.resolution, shared.start, shared.end, buckets, gaps)


def _merge_buckets(shared: Bucket, private: Bucket) -> Bucket:
    if (shared.start, shared.duration, shared.complete) != (
        private.start, private.duration, private.complete,
    ):
        raise MaterializationError("private browser bucket does not align with the shared bucket")
    shared_names = {item.name for item in shared.series}
    if any(item.name in shared_names for item in private.series):
        raise MaterializationError("private browser series collides with a shared series")
    if private.cost_detail != BucketCostDetail():
        raise MaterializationError("private browser bucket must not carry shared cost detail")
    first_values = tuple(
        value for value in (shared.first_observed_at, private.first_observed_at) if value is not None
    )
    last_values = tuple(
        value for value in (shared.last_observed_at, private.last_observed_at) if value is not None
    )
    return Bucket(
        shared.start,
        shared.duration,
        tuple(sorted((*shared.series, *private.series), key=lambda item: item.name)),
        shared.source_count + private.source_count,
        min(first_values, default=None),
        max(last_values, default=None),
        shared.complete,
        shared.cost_detail,
    )


def _build(
    snapshot: StoreSnapshot,
    source_generation: int,
    cache_generation: int,
    generated_at: float,
    observed_until: float,
    price_resolver: PriceResolver | None,
    previous: Generation | None,
    dirty: frozenset[DirtyCell] | None,
) -> Generation:
    _validate_generation_inputs(source_generation, cache_generation, generated_at, observed_until)
    bounds = {
        resolution: (
            math.floor(observed_until / resolution) * resolution + resolution,
            LAYER_SECONDS[resolution],
        )
        for resolution in RESOLUTIONS
    }
    all_gaps = _coverage_gaps(snapshot, min(end - span for end, span in bounds.values()), observed_until)
    shared_gaps = all_gaps
    previous_layers = {
        layer.resolution: layer
        for layer in (() if previous is None else previous.layers)
    }
    shared_fold_starts = {
        resolution: _layer_fold_starts(
            previous_layers.get(resolution), resolution, end - span, end, dirty,
        )
        for resolution, (end, span) in bounds.items()
    }
    observation_cells: dict[tuple[int, int], list[Observation]] = {}
    usage_cells: dict[tuple[int, int], list[UsageAtom]] = {}
    for observation in snapshot.observations:
        for resolution in RESOLUTIONS:
            start = math.floor(observation.observed_at / resolution) * resolution
            if start in shared_fold_starts[resolution]:
                observation_cells.setdefault((resolution, start), []).append(observation)
    identities: set[tuple[str, str, str, str, str]] = set()
    for raw_atom in snapshot.usage_atoms:
        try:
            atom = normalize_usage_atom(raw_atom)
        except UsageValidationError as error:
            raise MaterializationError("stored usage atom violates the current contract") from error
        identity = (atom.event_id, atom.direction, atom.modality, atom.cache_role, atom.unit)
        if identity in identities:
            continue
        identities.add(identity)
        for resolution in RESOLUTIONS:
            start = math.floor(atom.observed_at / resolution) * resolution
            if start in shared_fold_starts[resolution]:
                usage_cells.setdefault((resolution, start), []).append(atom)
    layers = []
    for resolution in RESOLUTIONS:
        end, span = bounds[resolution]
        start = end - span
        buckets = _updated_layer_buckets(
            previous_layers.get(resolution),
            shared_fold_starts[resolution],
            dirty,
            start,
            end,
            resolution,
            observation_cells,
            usage_cells,
            observed_until,
            price_resolver,
        )
        layers.append(Layer(
            resolution, start, end, buckets, _clip_gaps(shared_gaps, start, end),
        ))
    return Generation(
        source_generation,
        cache_generation,
        generated_at,
        observed_until,
        tuple(layers),
        (),
    )


def _layer_fold_starts(
    previous: Layer | None,
    resolution: int,
    start: int,
    end: int,
    dirty: frozenset[DirtyCell] | None,
) -> frozenset[int]:
    """Select cells that cannot be inherited from the prior fixed-width window."""

    starts = {
        cell.start
        for cell in (() if dirty is None else dirty)
        if (
            cell.resolution == resolution
            and start <= cell.start < end
            and (cell.start - start) % resolution == 0
        )
    }
    if previous is None:
        starts.update(range(start, end, resolution))
        return frozenset(starts)
    overlap_start = max(start, previous.start)
    overlap_end = min(end, previous.end)
    if overlap_start >= overlap_end:
        starts.update(range(start, end, resolution))
        return frozenset(starts)
    starts.update(range(start, overlap_start, resolution))
    starts.update(range(overlap_end, end, resolution))
    return frozenset(starts)


def _updated_layer_buckets(
    previous: Layer | None,
    fold_starts: frozenset[int],
    dirty: frozenset[DirtyCell] | None,
    start: int,
    end: int,
    resolution: int,
    observation_cells: Mapping[object, list[Observation]],
    usage_cells: Mapping[object, list[UsageAtom]],
    observed_until: float,
    price_resolver: PriceResolver | None,
    *,
    private_source_id: str | None = None,
) -> tuple[Bucket, ...]:
    """Splice the overlapping tuple and apply the shared fold decision only where needed."""

    if previous is not None and previous.start == start and previous.end == end and not fold_starts:
        return previous.buckets
    count = (end - start) // resolution
    buckets: list[Bucket | None] = [None] * count
    if previous is not None:
        overlap_start = max(start, previous.start)
        overlap_end = min(end, previous.end)
        if overlap_start < overlap_end:
            source_index = (overlap_start - previous.start) // resolution
            target_index = (overlap_start - start) // resolution
            overlap_count = (overlap_end - overlap_start) // resolution
            buckets[target_index:target_index + overlap_count] = previous.buckets[
                source_index:source_index + overlap_count
            ]
    touched = set(fold_starts)
    completion_candidates = {end - resolution}
    if previous is not None:
        completion_candidates.add(previous.end - resolution)
    for bucket_start in completion_candidates:
        if not start <= bucket_start < end:
            continue
        index = (bucket_start - start) // resolution
        reusable = buckets[index]
        if (
            reusable is not None
            and reusable.complete != (bucket_start + resolution <= observed_until)
        ):
            touched.add(bucket_start)
    for bucket_start in sorted(touched):
        index = (bucket_start - start) // resolution
        reusable = buckets[index]
        observation_key: object = (
            (resolution, bucket_start)
            if private_source_id is None
            else (private_source_id, resolution, bucket_start)
        )
        usage_key: object = (resolution, bucket_start)
        buckets[index] = _fold_or_reuse_bucket(
            DirtyCell(resolution, bucket_start),
            reusable,
            dirty,
            bucket_start,
            resolution,
            observation_cells.get(observation_key, ()),
            usage_cells.get(usage_key, ()),
            observed_until,
            price_resolver,
        )
    if any(bucket is None for bucket in buckets):
        raise MaterializationError("incremental layer window is not contiguous")
    return tuple(bucket for bucket in buckets if bucket is not None)


def _private_browser_sources(snapshot: StoreSnapshot) -> tuple[str, ...]:
    latest: dict[str, float] = {}
    for observation in snapshot.observations:
        if observation.family == "browser":
            latest[observation.source_id] = max(
                latest.get(observation.source_id, float("-inf")), observation.observed_at,
            )
    for epoch in snapshot.coverage_epochs:
        if epoch.family == "browser":
            observed_at = epoch.started_at if epoch.ended_at is None else epoch.ended_at
            latest[epoch.source_id] = max(
                latest.get(epoch.source_id, float("-inf")), observed_at,
            )
    for span in snapshot.unavailable_spans:
        if span.family == "browser":
            latest[span.source_id] = max(
                latest.get(span.source_id, float("-inf")), span.ended_at,
            )
    return tuple(
        source_id
        for source_id, _observed_at in sorted(
            latest.items(), key=lambda item: (-item[1], item[0]),
        )[:MAX_PRIVATE_BROWSER_CLIENTS]
    )


def _fold_or_reuse_bucket(
    identity: DirtyCell,
    reusable: Bucket | None,
    dirty: frozenset[DirtyCell] | None,
    start: int,
    duration: int,
    observations: Iterable[Observation],
    usage_atoms: Iterable[UsageAtom],
    observed_until: float,
    price_resolver: PriceResolver | None,
) -> Bucket:
    complete = start + duration <= observed_until
    if dirty is not None and identity not in dirty and reusable is not None:
        return reusable if reusable.complete == complete else replace(reusable, complete=complete)
    return _fold_bucket(
        start, duration, observations, usage_atoms, observed_until, price_resolver,
    )


def _fold_bucket(
    start: int,
    duration: int,
    observations: Iterable[Observation],
    usage_atoms: Iterable[UsageAtom],
    observed_until: float,
    price_resolver: PriceResolver | None,
) -> Bucket:
    observation_values = tuple(observations)
    usage_values = tuple(usage_atoms)
    samples = []
    projected_timestamps = []
    for observation in observation_values:
        observation_samples = _observation_samples(observation)
        samples.extend(observation_samples)
        if observation_samples:
            projected_timestamps.append(observation.observed_at)
    cost_atoms = []
    for atom in usage_values:
        atom_samples, cost_atom = _usage_projection(atom, price_resolver)
        samples.extend(atom_samples)
        if atom_samples:
            projected_timestamps.append(atom.observed_at)
        cost_atoms.append(cost_atom)
    grouped: dict[str, list[_Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.series, []).append(sample)
    series = []
    for name, values in sorted(grouped.items()):
        try:
            identity.identity_text(name, "series name")
        except identity.IdentityValidationError as error:
            raise MaterializationError(str(error)) from error
        operations = {value.operation for value in values}
        if len(operations) != 1:
            raise MaterializationError(f"series {name!r} has conflicting fold operations")
        operation = operations.pop()
        if operation in ("gauge", "status"):
            result = max(values, key=lambda value: (value.observed_at, value.source_id)).value
        elif operation == "average":
            result = sum(value.value for value in values) / len(values)
        elif operation == "average_sources":
            source_values = _sample_values_by_source(values)
            result = sum(sum(items) / len(items) for items in source_values.values()) / len(source_values)
        elif operation == "rate":
            result = sum(value.value for value in values) / duration
        elif operation == "rate_average_sources":
            source_values = _sample_values_by_source(values)
            result = sum(sum(items) / duration for items in source_values.values()) / len(source_values)
        elif operation == "rate_per_minute":
            result = sum(value.value for value in values) * 60 / duration
        elif operation == "sum":
            result = sum(value.value for value in values)
        elif operation == "sum_average_sources":
            source_values = _sample_values_by_source(values)
            result = sum(sum(items) for items in source_values.values()) / len(source_values)
        else:
            raise MaterializationError(f"unknown fold operation {operation!r}")
        if name == "cost_micro_usd" and (
            isinstance(result, bool) or not isinstance(result, int) or result > MAX_SAFE_INTEGER
        ):
            raise MaterializationError("cost projection must remain an exact JSON-safe integer")
        source_count = len({value.source_id for value in values}) if operation.endswith("_sources") else len(values)
        series.append(SeriesValue(
            name, result, source_count,
            min(value.observed_at for value in values),
            max(value.observed_at for value in values),
        ))
    timestamps = tuple(projected_timestamps)
    return Bucket(
        start,
        duration,
        tuple(series),
        len(timestamps),
        min(timestamps, default=None),
        max(timestamps, default=None),
        start + duration <= observed_until,
        _build_bucket_cost_detail(tuple(cost_atoms)),
    )


def _sample_values_by_source(values: Iterable[_Sample]) -> dict[str, list[int | float]]:
    grouped: dict[str, list[int | float]] = {}
    for value in values:
        grouped.setdefault(value.source_id, []).append(value.value)
    return grouped


def _observation_samples(observation: Observation) -> tuple[_Sample, ...]:
    spec = FAMILY_BY_NAME.get(observation.family)
    if spec is None:
        raise MaterializationError(f"unknown current family {observation.family!r}")
    payload = validate_payload(observation.family, observation.payload)
    at = observation.observed_at
    source = observation.source_id
    if observation.family == "cpu":
        return (
            _Sample(f"cpu_percent:{source}", "average", _number(payload, "process_percent"), at, source),
            _Sample("system_cpu_percent", "average", _number(payload, "system_percent"), at, source),
        )
    if observation.family == "agent_status":
        states = payload["states"]
        if not isinstance(states, Mapping):
            raise MaterializationError("agent_status.states must be an object")
        samples = [
            _Sample(f"{name}_agents", "status", float(sum(value == name for value in states.values())), at, source)
            for name in ("ask", "run", "transition", "idle")
        ]
        session_states = payload.get("session_states", {})
        if not isinstance(session_states, Mapping):
            raise MaterializationError("agent_status.session_states must be an object")
        samples.extend(
            _Sample(f"{name}_sessions", "status", float(sum(value == name for value in session_states.values())), at, source)
            for name in ("ask", "run", "transition", "idle")
        )
        if "snapshot_revision" in payload:
            samples.append(_Sample("agent_window_snapshot_revision", "gauge", _number(payload, "snapshot_revision"), at, source))
        return tuple(samples)
    if observation.family == "browser":
        samples = []
        kind = payload["kind"]
        if kind in ("api", "sse"):
            samples.append(_Sample(f"browser_{kind}_per_second", "rate_average_sources", 1, at, source))
        if "latency_ms" in payload:
            samples.append(_Sample("browser_latency_ms", "average_sources", _number(payload, "latency_ms"), at, source))
        if "bytes" in payload:
            samples.append(_Sample("browser_bandwidth_bytes_per_second", "rate_average_sources", _number(payload, "bytes"), at, source))
        if kind == "disconnect" and "duration_ms" in payload:
            samples.append(_Sample("browser_disconnected_ms", "sum_average_sources", _number(payload, "duration_ms"), at, source))
        if kind == "api" and "queue_ms" in payload:
            samples.append(_Sample("browser_queue_ms", "average_sources", _number(payload, "queue_ms"), at, source))
        if kind == "page_load":
            page_series = {
                "first_paint_ms": "browser_first_paint_ms",
                "first_contentful_paint_ms": "browser_first_contentful_paint_ms",
                "app_ready_ms": "browser_app_ready_ms",
                "max_concurrency": "browser_page_max_concurrency",
            }
            samples.extend(
                _Sample(series, "average_sources", _number(payload, field), at, source)
                for field, series in page_series.items()
                if field in payload
            )
        perceptual_series = {
            "finder_usable": "browser_finder_usable_ms",
            "interaction": "browser_input_latency_ms",
            "operation_wait": "browser_operation_wait_ms",
            "long_task": "browser_long_task_ms",
        }
        if kind in perceptual_series and "latency_ms" in payload:
            samples.append(_Sample(perceptual_series[kind], "average_sources", _number(payload, "latency_ms"), at, source))
        if kind == "heartbeat":
            health_series = {
                "upload_queue_depth": "browser_upload_queue_depth",
                "upload_drops": "browser_upload_drops",
                "upload_retries": "browser_upload_retries",
                "instrumentation_cost_ms": "browser_instrumentation_cost_ms",
            }
            samples.extend(
                _Sample(series, "average_sources", _number(payload, field), at, source)
                for field, series in health_series.items()
                if field in payload
            )
        return tuple(samples)
    fields: Mapping[str, str] | None = {
        "gpu": {
            f"gpu_util_percent:{source}": "util_percent",
            f"gpu_memory_bytes:{source}": "memory_used_bytes",
        },
        "service_load": {
            f"service_cpu_percent:{source}": "cpu_percent",
            f"service_rss_bytes:{source}": "rss_bytes",
        },
        "system_memory": {
            "system_memory_used_bytes": "used_bytes",
            "system_memory_capacity_bytes": "capacity_bytes",
            "mac_physical_memory_bytes": "mac_physical_memory_bytes",
            "mac_memory_used_bytes": "mac_memory_used_bytes",
            "mac_cached_files_bytes": "mac_cached_files_bytes",
            "mac_swap_used_bytes": "mac_swap_used_bytes",
            "mac_app_memory_bytes": "mac_app_memory_bytes",
            "mac_wired_memory_bytes": "mac_wired_memory_bytes",
            "mac_compressed_memory_bytes": "mac_compressed_memory_bytes",
            "mac_pressure_percent": "mac_pressure_percent",
            "mac_pressure_level": "mac_pressure_level",
        },
    }.get(observation.family)
    if fields is not None:
        return tuple(
            _Sample(series, spec.fold_kind.value, _number(payload, field), at, source)
            for series, field in fields.items()
            if field in payload and payload[field] is not None
        )
    # Agent/model token and cost projections have one owner: usage atoms.
    return ()


def _usage_projection(
    atom: UsageAtom,
    price_resolver: PriceResolver | None,
) -> tuple[tuple[_Sample, ...], _CostDetailAtom]:
    at = atom.observed_at
    quantity = _optional_number(atom.payload, "quantity")
    if quantity is not None and quantity < 0:
        raise MaterializationError("usage quantity must be non-negative")
    agent_id = atom.payload.get("agent_id")
    model = atom.payload.get("model")
    provider = atom.payload.get("provider")
    if quantity is None or not quantity.is_integer() or quantity > MAX_SAFE_INTEGER:
        raise MaterializationError("usage detail quantity must be a JSON-safe integer")
    detail_quantity = int(quantity)
    if not isinstance(agent_id, str) or not agent_id or not isinstance(model, str) or not model:
        raise MaterializationError("usage detail requires model and agent attribution")
    if not isinstance(provider, str) or not provider:
        raise MaterializationError("usage detail requires provider attribution")
    samples = []
    if atom.unit == "tokens" and quantity is not None:
        dimension = _model_token_dimension(atom)
        if dimension == "output":
            samples.append(_Sample(
                f"agent_tokens_per_minute:{agent_id}", "rate_per_minute", quantity, at,
                atom.event_id,
            ))
        samples.append(_Sample(
            f"model_tokens_per_minute:{dimension}:{model}", "rate_per_minute", quantity,
            at, atom.event_id,
        ))
        samples.append(_Sample(
            f"model_tokens_per_minute:all:{model}", "rate_per_minute", quantity,
            at, atom.event_id,
        ))
        samples.append(_Sample("usage_tokens", "sum", quantity, at, atom.event_id))
    projection = UsagePriceProjection(None, None, None) if price_resolver is None else price_resolver(atom)
    if not isinstance(projection, UsagePriceProjection):
        raise MaterializationError("price resolver must return exact integer micro-USD projection")
    cost = projection.micro_usd
    api_list_cost = projection.api_list_micro_usd
    if projection.priced != (cost is not None and api_list_cost is not None):
        raise MaterializationError("price projection evidence and both costs must be present together")
    if cost is not None and api_list_cost is not None:
        for value, name in (
            (cost, "marginal"), (api_list_cost, "API-list counterfactual"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > MAX_SAFE_INTEGER:
                raise MaterializationError(
                    f"price resolver must return non-negative JSON-safe integer {name} micro-USD"
                )
        samples.append(_Sample("cost_micro_usd", "sum", cost, at, atom.event_id))
        samples.append(_Sample(
            "api_list_cost_micro_usd", "sum", api_list_cost, at, atom.event_id,
        ))
    dimension = _cost_detail_dimension(atom)
    is_tokens = atom.unit == "tokens"
    execution_source = atom.payload.get("execution_source")
    agent_source = (
        _privacy_safe_execution_source(execution_source)
        if isinstance(execution_source, str) and execution_source else "unknown"
    )
    evidence = None
    if projection.evidence is not None:
        evidence = _cost_evidence_value(
            atom,
            provider,
            model,
            dimension,
            detail_quantity if is_tokens else 0,
            cost if cost is not None else 0,
            api_list_cost if api_list_cost is not None else 0,
            projection.evidence,
        )
    return tuple(samples), _CostDetailAtom(
        dimension,
        detail_quantity,
        is_tokens,
        projection.priced,
        cost,
        api_list_cost,
        cost_detail_model_key(provider, model),
        provider,
        model,
        cost_detail_agent_key(agent_id),
        agent_source,
        _privacy_safe_agent_label(agent_id, agent_source),
        evidence,
    )


def _cost_evidence_value(
    atom: UsageAtom,
    provider: str,
    model: str,
    dimension: str,
    tokens: int,
    micro_usd: int,
    api_list_micro_usd: int,
    evidence: PricingEvidence,
) -> CostEvidenceValue:
    pricing_profile = str(atom.payload.get("pricing_profile") or "default")
    service_tier = str(atom.payload.get("service_tier") or "default")
    key = _stable_detail_key(
        provider,
        model,
        dimension,
        atom.direction,
        atom.modality,
        atom.cache_role,
        atom.unit,
        pricing_profile,
        service_tier,
        evidence.catalog_model,
        evidence.rate_usd,
        str(evidence.rate_scale),
        evidence.effective_from,
        evidence.source_kind,
        evidence.source_url,
        str(evidence.catalog_revision),
    )
    return CostEvidenceValue(
        key,
        provider,
        model,
        dimension,
        atom.direction,
        atom.modality,
        atom.cache_role,
        atom.unit,
        pricing_profile,
        service_tier,
        evidence.catalog_model,
        evidence.rate_usd,
        evidence.rate_scale,
        evidence.effective_from,
        evidence.source_kind,
        evidence.source_url,
        evidence.catalog_revision,
        tokens,
        micro_usd,
        api_list_micro_usd,
        1,
    )


def _ranked_cost_keys(scores: Mapping[str, int], maximum: int) -> frozenset[str]:
    ranked = sorted(scores, key=lambda key: (-scores[key], key))
    return frozenset(ranked[:maximum])


def _add_cost_atom_to_attribution(
    row: dict[str, object], atom: _CostDetailAtom, name: str,
) -> None:
    state = "priced" if atom.priced else "unpriced"
    _accumulate_attribution_metric(row, atom.dimension, f"{state}_atoms", 1, name)
    if atom.is_tokens:
        _accumulate_attribution_metric(row, atom.dimension, "tokens", atom.quantity, name)
        _accumulate_attribution_metric(
            row, atom.dimension, f"{state}_tokens", atom.quantity, name,
        )
    if atom.micro_usd is not None:
        _accumulate_attribution_metric(
            row, atom.dimension, "micro_usd", atom.micro_usd, name,
        )
    if atom.api_list_micro_usd is not None:
        _accumulate_attribution_metric(
            row, atom.dimension, "api_list_micro_usd", atom.api_list_micro_usd,
            name,
        )


def _evidence_identity(value: CostEvidenceValue) -> tuple[object, ...]:
    return (
        value.key, value.provider, value.model, value.dimension, value.direction,
        value.modality, value.cache_role, value.unit, value.pricing_profile,
        value.service_tier, value.catalog_model, value.rate_usd, value.rate_scale,
        value.effective_from, value.source_kind, value.source_url,
        value.catalog_revision,
    )


def _merge_cost_evidence(
    current: CostEvidenceValue | None,
    incoming: CostEvidenceValue,
) -> CostEvidenceValue:
    if current is None:
        return incoming
    if _evidence_identity(current) != _evidence_identity(incoming):
        raise MaterializationError("cost evidence metadata conflicts")
    totals = {
        "tokens": current.tokens,
        "micro_usd": current.micro_usd,
        "api_list_micro_usd": current.api_list_micro_usd,
        "priced_atoms": current.priced_atoms,
    }
    _add_exact(totals, "tokens", incoming.tokens, "cost evidence tokens")
    _add_exact(totals, "micro_usd", incoming.micro_usd, "cost evidence micro_usd")
    _add_exact(
        totals, "api_list_micro_usd", incoming.api_list_micro_usd,
        "cost evidence API-list micro_usd",
    )
    _add_exact(totals, "priced_atoms", incoming.priced_atoms, "cost evidence atoms")
    return replace(current, **totals)


def _freeze_cost_dimensions(
    dimensions: Mapping[str, Mapping[str, int]],
) -> tuple[CostDimensionValue, ...]:
    return tuple(
        CostDimensionValue(
            dimension,
            dimensions[dimension]["tokens"],
            dimensions[dimension]["micro_usd"],
            dimensions[dimension]["api_list_micro_usd"],
        )
        for dimension in COST_REPORT_DIMENSIONS
    )


def _freeze_attribution(
    row: Mapping[str, object],
    *,
    provider: str = "",
    model: str = "",
    source: str = "",
    label: str = "",
) -> CostAttribution:
    dimensions = row["dimensions"]
    priced = row["priced"]
    unpriced = row["unpriced"]
    if (
        not isinstance(dimensions, Mapping)
        or not isinstance(priced, Mapping)
        or not isinstance(unpriced, Mapping)
    ):
        raise MaterializationError("cost attribution accumulator is malformed")
    return CostAttribution(
        key=str(row["key"]), provider=provider, model=model, source=source, label=label,
        dimensions=_freeze_cost_dimensions(dimensions),
        priced=CostCoverage(int(priced["atoms"]), int(priced["tokens"])),
        unpriced=CostCoverage(int(unpriced["atoms"]), int(unpriced["tokens"])),
    )


def _build_bucket_cost_detail(atoms: tuple[_CostDetailAtom, ...]) -> BucketCostDetail:
    if not atoms:
        return BucketCostDetail()
    dimensions = _empty_cost_dimensions()
    priced = {"atoms": 0, "tokens": 0}
    unpriced = {"atoms": 0, "tokens": 0}
    model_scores: dict[str, int] = {}
    agent_scores: dict[str, int] = {}
    evidence_scores: dict[str, int] = {}
    for atom in atoms:
        attribution_score = 1 + (2 * atom.quantity if atom.is_tokens else 0)
        attribution_score += atom.micro_usd or 0
        attribution_score += atom.api_list_micro_usd or 0
        model_scores[atom.model_key] = (
            model_scores.get(atom.model_key, 0) + attribution_score
        )
        agent_scores[atom.agent_key] = (
            agent_scores.get(atom.agent_key, 0) + attribution_score
        )
        if atom.evidence is not None:
            evidence_score = 1 + (atom.quantity if atom.is_tokens else 0)
            evidence_score += atom.micro_usd or 0
            evidence_score += atom.api_list_micro_usd or 0
            evidence_scores[atom.evidence.key] = (
                evidence_scores.get(atom.evidence.key, 0) + evidence_score
            )
    selected_models = _ranked_cost_keys(model_scores, MAX_COST_DETAIL_MODELS)
    selected_agents = _ranked_cost_keys(agent_scores, MAX_COST_DETAIL_AGENTS)
    selected_evidence = _ranked_cost_keys(evidence_scores, MAX_COST_DETAIL_EVIDENCE)
    models: dict[str, dict[str, object]] = {}
    model_metadata: dict[str, tuple[str, str]] = {}
    agents: dict[str, dict[str, object]] = {}
    agent_sources: dict[str, set[str]] = {}
    agent_labels: dict[str, set[str]] = {}
    evidence: dict[str, CostEvidenceValue] = {}
    for atom in atoms:
        state = priced if atom.priced else unpriced
        _add_exact(state, "atoms", 1, "cost bucket coverage")
        if atom.is_tokens:
            _add_exact(dimensions[atom.dimension], "tokens", atom.quantity, "cost bucket tokens")
            _add_exact(state, "tokens", atom.quantity, "cost bucket coverage")
        if atom.micro_usd is not None:
            _add_exact(
                dimensions[atom.dimension], "micro_usd", atom.micro_usd,
                "cost bucket micro_usd",
            )
        if atom.api_list_micro_usd is not None:
            _add_exact(
                dimensions[atom.dimension], "api_list_micro_usd",
                atom.api_list_micro_usd, "cost bucket API-list micro_usd",
            )
        if atom.model_key in selected_models:
            metadata = (atom.provider, atom.model)
            previous_metadata = model_metadata.setdefault(atom.model_key, metadata)
            if previous_metadata != metadata:
                raise MaterializationError("cost model metadata conflicts within one bucket")
            row = models.setdefault(atom.model_key, _empty_attribution(atom.model_key))
            _add_cost_atom_to_attribution(row, atom, "cost model attribution")
        if atom.agent_key in selected_agents:
            row = agents.setdefault(atom.agent_key, _empty_attribution(atom.agent_key))
            agent_sources.setdefault(atom.agent_key, set()).add(atom.agent_source)
            agent_labels.setdefault(atom.agent_key, set()).add(atom.agent_label)
            _add_cost_atom_to_attribution(row, atom, "cost agent attribution")
        if atom.evidence is not None and atom.evidence.key in selected_evidence:
            evidence[atom.evidence.key] = _merge_cost_evidence(
                evidence.get(atom.evidence.key), atom.evidence,
            )
    model_values = tuple(
        _freeze_attribution(
            models[key], provider=model_metadata[key][0], model=model_metadata[key][1],
        )
        for key in sorted(models)
    )
    agent_values = tuple(
        _freeze_attribution(
            agents[key],
            source=next(iter(agent_sources[key])) if len(agent_sources[key]) == 1 else "mixed",
            label=next(iter(agent_labels[key])) if len(agent_labels[key]) == 1 else "mixed",
        )
        for key in sorted(agents)
    )
    return BucketCostDetail(
        _freeze_cost_dimensions(dimensions),
        CostCoverage(priced["atoms"], priced["tokens"]),
        CostCoverage(unpriced["atoms"], unpriced["tokens"]),
        model_values,
        agent_values,
        tuple(evidence[key] for key in sorted(evidence)),
        max(0, len(model_scores) - len(selected_models)),
        max(0, len(agent_scores) - len(selected_agents)),
        max(0, len(evidence_scores) - len(selected_evidence)),
    )


def _stable_detail_key(*values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()[:24]


def _privacy_safe_execution_source(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in PUBLIC_EXECUTION_SOURCES:
        return normalized
    return "sha256-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _privacy_safe_agent_label(agent_id: str, source: str) -> str:
    # The token-rate series and cost report both originate from this bounded roster key.
    # Keep it recognisable so the browser can render one canonical tmux-session label on
    # both surfaces; arbitrary transcript paths and other private identities still hash.
    parts = agent_id.split("|")
    if (
        2 <= len(parts) <= 4
        and parts[-1] in {"claude", "codex", "term"}
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", parts[0])
        and all(0 < len(part) <= 128 and not any(character.isspace() for character in part) for part in parts[1:])
    ):
        return agent_id
    if agent_id.startswith("claude-bg:"):
        parts = agent_id.split(":", 3)
        if len(parts) == 4:
            project = "-".join(part for part in parts[1].split("-") if part)[-32:]
            session = parts[2][:8]
            if project and session:
                return f"claude-bg:{project}:{session}"
    return f"{source}:{cost_detail_agent_key(agent_id)[:8]}"


def cost_detail_model_key(provider: str, model: str) -> str:
    return _stable_detail_key(provider, model)


def cost_detail_agent_key(agent_id: str) -> str:
    return _stable_detail_key(agent_id)


def _exact_cost_value(value: int | float, name: str) -> int:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if (
        isinstance(value, bool) or not isinstance(value, int)
        or value < 0 or value > MAX_SAFE_INTEGER
    ):
        raise MaterializationError(f"{name} must be an exact JSON-safe integer")
    return value


def _add_exact(target: dict[str, int], field: str, value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MaterializationError(f"{name} must be a non-negative exact integer")
    result = target.get(field, 0) + value
    if result > MAX_SAFE_INTEGER:
        raise MaterializationError(f"{name} exceeds the exact JSON integer range")
    target[field] = result


def _empty_cost_dimensions() -> dict[str, dict[str, int]]:
    return {
        dimension: {"tokens": 0, "micro_usd": 0, "api_list_micro_usd": 0}
        for dimension in COST_REPORT_DIMENSIONS
    }


def _empty_attribution(key: str) -> dict[str, object]:
    return {
        "key": key,
        "total_tokens": 0,
        "total_micro_usd": 0,
        "total_api_list_micro_usd": 0,
        "dimensions": _empty_cost_dimensions(),
        "priced": {"atoms": 0, "tokens": 0},
        "unpriced": {"atoms": 0, "tokens": 0},
    }


def _accumulate_attribution_metric(
    row: dict[str, object],
    dimension: str,
    metric: str,
    value: int,
    name: str,
) -> None:
    if dimension not in COST_REPORT_DIMENSIONS:
        raise MaterializationError(
            f"{name} uses an unavailable or fabricated cost dimension"
        )
    dimensions = row["dimensions"]
    if not isinstance(dimensions, dict):
        raise MaterializationError("cost attribution dimensions are malformed")
    dimension_totals = dimensions[dimension]
    if not isinstance(dimension_totals, dict):
        raise MaterializationError("cost attribution dimension is malformed")
    if metric in {"tokens", "micro_usd", "api_list_micro_usd"}:
        _add_exact(dimension_totals, metric, value, name)
        total_field = {
            "tokens": "total_tokens",
            "micro_usd": "total_micro_usd",
            "api_list_micro_usd": "total_api_list_micro_usd",
        }[metric]
        total = row[total_field]
        if isinstance(total, bool) or not isinstance(total, int):
            raise MaterializationError("cost attribution total is malformed")
        if total + value > MAX_SAFE_INTEGER:
            raise MaterializationError(f"{name} exceeds the exact JSON integer range")
        row[total_field] = total + value
        return
    if metric in {"priced_tokens", "unpriced_tokens", "priced_atoms", "unpriced_atoms"}:
        coverage_name, field = metric.split("_", 1)
        coverage = row[coverage_name]
        if not isinstance(coverage, dict):
            raise MaterializationError("cost attribution coverage is malformed")
        _add_exact(coverage, field, value, name)
        return
    raise MaterializationError(f"unknown cost attribution metric {metric!r}")


def _rank_cost_rows(
    rows: Mapping[str, dict[str, object]], maximum: int,
) -> tuple[list[dict[str, object]], int]:
    ranked = sorted(
        rows.values(),
        key=lambda row: (
            -int(row["total_tokens"]), -int(row["total_api_list_micro_usd"]),
            -int(row["total_micro_usd"]), str(row["key"]),
        ),
    )
    return ranked[:maximum], max(0, len(ranked) - maximum)


def build_cost_report(layer: Layer) -> dict[str, object]:
    """Reduce one exact materialized slice into one browser-ready cost report.

    This runs while cache entries are published. HTTP and browser code only forward
    or render the resulting object; they never sum buckets or calculate cost totals.
    """

    dimensions = _empty_cost_dimensions()
    priced = {"atoms": 0, "tokens": 0}
    unpriced = {"atoms": 0, "tokens": 0}
    models: dict[str, dict[str, object]] = {}
    agents: dict[str, dict[str, object]] = {}
    model_metadata: dict[str, set[tuple[str, str]]] = {}
    agent_sources: dict[str, set[str]] = {}
    agent_labels: dict[str, set[str]] = {}
    evidence: dict[str, CostEvidenceValue] = {}
    omissions = {"models": 0, "agents": 0, "evidence": 0}
    total_tokens = 0
    total_micro_usd = 0
    total_api_list_micro_usd = 0

    for bucket in layer.buckets:
        for item in bucket.series:
            if item.name not in {
                "usage_tokens", "cost_micro_usd", "api_list_cost_micro_usd",
            }:
                continue
            value = _exact_cost_value(item.value, item.name)
            if item.name == "usage_tokens":
                total_tokens += value
                if total_tokens > MAX_SAFE_INTEGER:
                    raise MaterializationError("cost report total_tokens exceeds JSON range")
            elif item.name == "cost_micro_usd":
                total_micro_usd += value
                if total_micro_usd > MAX_SAFE_INTEGER:
                    raise MaterializationError("cost report total_micro_usd exceeds JSON range")
            else:
                total_api_list_micro_usd += value
                if total_api_list_micro_usd > MAX_SAFE_INTEGER:
                    raise MaterializationError(
                        "cost report total_api_list_micro_usd exceeds JSON range"
                    )
        detail = bucket.cost_detail
        for item in detail.dimensions:
            if item.dimension not in dimensions:
                raise MaterializationError("typed cost detail uses a non-current dimension")
            _add_exact(
                dimensions[item.dimension], "tokens", item.tokens,
                "typed cost detail tokens",
            )
            _add_exact(
                dimensions[item.dimension], "micro_usd", item.micro_usd,
                "typed cost detail micro_usd",
            )
            _add_exact(
                dimensions[item.dimension], "api_list_micro_usd",
                item.api_list_micro_usd, "typed cost detail API-list micro_usd",
            )
        for target, coverage, name in (
            (priced, detail.priced, "typed priced coverage"),
            (unpriced, detail.unpriced, "typed unpriced coverage"),
        ):
            _add_exact(target, "atoms", coverage.atoms, name)
            _add_exact(target, "tokens", coverage.tokens, name)
        _add_exact(omissions, "models", detail.omitted_models, "typed model omissions")
        _add_exact(omissions, "agents", detail.omitted_agents, "typed agent omissions")
        _add_exact(omissions, "evidence", detail.omitted_evidence, "typed evidence omissions")
        for values, rows, scope in (
            (detail.models, models, "model"),
            (detail.agents, agents, "agent"),
        ):
            for value in values:
                row = rows.setdefault(value.key, _empty_attribution(value.key))
                for dimension in value.dimensions:
                    _accumulate_attribution_metric(
                        row, dimension.dimension, "tokens", dimension.tokens,
                        f"typed {scope} tokens",
                    )
                    _accumulate_attribution_metric(
                        row, dimension.dimension, "micro_usd", dimension.micro_usd,
                        f"typed {scope} micro_usd",
                    )
                    _accumulate_attribution_metric(
                        row, dimension.dimension, "api_list_micro_usd",
                        dimension.api_list_micro_usd,
                        f"typed {scope} API-list micro_usd",
                    )
                row_priced = row["priced"]
                row_unpriced = row["unpriced"]
                if not isinstance(row_priced, dict) or not isinstance(row_unpriced, dict):
                    raise MaterializationError("cost attribution coverage is malformed")
                _add_exact(row_priced, "atoms", value.priced.atoms, f"typed {scope} priced")
                _add_exact(row_priced, "tokens", value.priced.tokens, f"typed {scope} priced")
                _add_exact(row_unpriced, "atoms", value.unpriced.atoms, f"typed {scope} unpriced")
                _add_exact(row_unpriced, "tokens", value.unpriced.tokens, f"typed {scope} unpriced")
                if scope == "model":
                    model_metadata.setdefault(value.key, set()).add((value.provider, value.model))
                else:
                    agent_sources.setdefault(value.key, set()).add(value.source)
                    agent_labels.setdefault(value.key, set()).add(value.label)
        for value in detail.evidence:
            evidence[value.key] = _merge_cost_evidence(evidence.get(value.key), value)

    if sum(item["tokens"] for item in dimensions.values()) != total_tokens:
        raise MaterializationError("cost detail token dimensions disagree with usage_tokens")
    if sum(item["micro_usd"] for item in dimensions.values()) != total_micro_usd:
        raise MaterializationError("cost detail dimensions disagree with cost_micro_usd")
    if (
        sum(item["api_list_micro_usd"] for item in dimensions.values())
        != total_api_list_micro_usd
    ):
        raise MaterializationError(
            "cost detail dimensions disagree with api_list_cost_micro_usd"
        )

    for key, row in models.items():
        values = model_metadata.get(key, set())
        if len(values) != 1:
            raise MaterializationError(f"cost model metadata conflicts for {key}")
        row["provider"], row["model"] = next(iter(values))
    for key, row in agents.items():
        sources = agent_sources.get(key, {"unknown"})
        row["source"] = next(iter(sources)) if len(sources) == 1 else "mixed"
        labels = agent_labels.get(key, {"unknown"})
        row["label"] = next(iter(labels)) if len(labels) == 1 else "mixed"
    model_rows, omitted_models = _rank_cost_rows(models, MAX_COST_DETAIL_MODELS)
    agent_rows, omitted_agents = _rank_cost_rows(agents, MAX_COST_DETAIL_AGENTS)
    _add_exact(omissions, "models", omitted_models, "cost report model rows")
    _add_exact(omissions, "agents", omitted_agents, "cost report agent rows")

    evidence_rows = []
    catalog_revision = 0
    for key, values in evidence.items():
        revision = values.catalog_revision
        catalog_revision = max(catalog_revision, revision)
        evidence_rows.append({
            "key": key,
            "provider": values.provider,
            "model": values.model,
            "dimension": values.dimension,
            "direction": values.direction,
            "modality": values.modality,
            "cache_role": values.cache_role,
            "unit": values.unit,
            "pricing_profile": values.pricing_profile,
            "service_tier": values.service_tier,
            "catalog_model": values.catalog_model,
            "rate_usd": values.rate_usd,
            "rate_scale": values.rate_scale,
            "effective_from": values.effective_from,
            "source_kind": values.source_kind,
            "source_url": values.source_url,
            "catalog_revision": revision,
            "tokens": values.tokens,
            "micro_usd": values.micro_usd,
            "api_list_micro_usd": values.api_list_micro_usd,
            "priced_atoms": values.priced_atoms,
        })
    evidence_rows.sort(
        key=lambda row: (
            -int(row["tokens"]), -int(row["api_list_micro_usd"]),
            -int(row["micro_usd"]), str(row["key"]),
        ),
    )
    omitted_evidence = max(0, len(evidence_rows) - MAX_COST_DETAIL_EVIDENCE)
    _add_exact(omissions, "evidence", omitted_evidence, "cost report evidence rows")
    evidence_rows = evidence_rows[:MAX_COST_DETAIL_EVIDENCE]

    report: dict[str, object] = {
        "schema_version": COST_REPORT_SCHEMA_VERSION,
        "total_micro_usd": total_micro_usd,
        "total_api_list_micro_usd": total_api_list_micro_usd,
        "total_tokens": total_tokens,
        "dimensions": dimensions,
        "priced": priced,
        "unpriced": unpriced,
        "models": model_rows,
        "agents": agent_rows,
        "evidence": evidence_rows,
        "catalog_revision": catalog_revision,
        "omissions": omissions,
        "reasoning_available": False,
    }
    return validate_cost_report(report)


def _cost_detail_dimension(atom: UsageAtom) -> str:
    if atom.unit != "tokens" or atom.modality != "text":
        return "other"
    if atom.direction == "output":
        return "output"
    if atom.cache_role == "read":
        return "cache_read"
    if atom.cache_role == "write_5m":
        return "cache_write_5m"
    if atom.cache_role == "write_1h":
        return "cache_write_1h"
    return "input"


def _model_token_dimension(atom: UsageAtom) -> str:
    if atom.direction == "output":
        return "output"
    if atom.cache_role == "read":
        return "cache_read"
    if atom.cache_role in {"write_5m", "write_1h"}:
        return "cache_write"
    return "input"


def _is_legacy_inline_epoch(item: CoverageEpoch) -> bool:
    if item.family not in {"cpu", "gpu"}:
        return False
    parts = item.epoch_id.split(":")
    if len(parts) != 3 or parts[0] != str(item.owner_generation) or parts[1] != item.family:
        return False
    suffix = parts[2]
    if not suffix.isascii() or not suffix.isdigit():
        return False
    try:
        return suffix == str(int(item.started_at))
    except (OverflowError, ValueError):
        return False


def _coalesce_coverage_epochs(
    coverage_epochs: Iterable[CoverageEpoch],
    unavailable_spans: Iterable[UnavailableSpan],
) -> tuple[CoverageEpoch, ...]:
    """Normalize only the retired inline per-sample lifecycle representation."""

    separators = tuple(unavailable_spans)
    compacted: list[CoverageEpoch] = []
    active_legacy: dict[tuple[int, str, str, float], int] = {}
    for item in coverage_epochs:
        item_is_legacy = _is_legacy_inline_epoch(item)
        key = (
            item.owner_generation,
            item.family,
            item.source_id,
            item.native_cadence_seconds,
        )
        previous_index = active_legacy.get(key) if item_is_legacy else None
        if previous_index is None:
            compacted.append(item)
            if item_is_legacy:
                active_legacy[key] = len(compacted) - 1
            else:
                active_legacy.pop(key, None)
            continue
        previous = compacted[previous_index]
        previous_end = previous.ended_at
        separated = (
            previous_end is not None
            and any(
                span.family == item.family
                and span.source_id == item.source_id
                and span.started_at < item.started_at
                and span.ended_at > previous_end
                for span in separators
            )
        )
        mergeable = (
            previous_end is not None
            and item.started_at - previous_end <= previous.native_cadence_seconds
            and not separated
        )
        if not mergeable:
            compacted.append(item)
            active_legacy[key] = len(compacted) - 1
            continue
        ended_at = (
            None
            if previous_end is None or item.ended_at is None
            else max(previous_end, item.ended_at)
        )
        compacted[previous_index] = replace(
            previous,
            epoch_id=item.epoch_id,
            ended_at=ended_at,
        )
    return tuple(compacted)


def normalize_coverage_model(
    coverage_epochs: Iterable[CoverageEpoch],
    unavailable_spans: Iterable[UnavailableSpan],
) -> tuple[tuple[CoverageEpoch, ...], tuple[UnavailableSpan, ...]]:
    """Normalize durable coverage history once for reuse by incremental builds."""

    normalized_spans = normalize_unavailable_spans(unavailable_spans)
    return (
        _coalesce_coverage_epochs(coverage_epochs, normalized_spans),
        normalized_spans,
    )


def _coverage_gaps(snapshot: StoreSnapshot, oldest: float, observed_until: float) -> tuple[NoData, ...]:
    gaps: list[NoData] = []
    coverage_epochs, unavailable_spans = normalize_coverage_model(
        snapshot.coverage_epochs,
        snapshot.unavailable_spans,
    )
    for spec in CURRENT_FAMILIES:
        if not spec.no_data_eligible:
            continue
        family_coverage = tuple(
            item for item in coverage_epochs
            if item.family == spec.coverage_family
        )
        latest_family_end = max(
            (
                observed_until if item.ended_at is None else item.ended_at
                for item in family_coverage
            ),
            default=oldest,
        )
        sources = {
            item.source_id
            for item in family_coverage
        }
        sources.update(
            item.source_id
            for item in unavailable_spans
            if item.family == spec.coverage_family
        )
        for source_id in sorted(sources):
            intervals = sorted(
                (
                    item for item in family_coverage
                    if item.source_id == source_id
                ),
                key=lambda item: (item.started_at, item.epoch_id),
            )
            explicit_gaps = [
                NoData(
                    spec.name, source_id, item.epoch_id,
                    max(oldest, item.started_at), min(observed_until, item.ended_at),
                    item.native_cadence_seconds, item.reason,
                )
                for item in unavailable_spans
                if item.family == spec.coverage_family
                and item.source_id == source_id
                and item.ended_at > oldest
                and item.started_at < observed_until
            ]
            if not intervals:
                for gap in sorted(explicit_gaps, key=lambda item: (item.start, item.end, item.epoch_id)):
                    _append_gap(gaps, gap)
                continue
            computed_gaps: list[NoData] = []
            cursor = max(oldest, intervals[0].started_at)
            previous = intervals[0]
            for interval in intervals:
                start = max(oldest, interval.started_at)
                end = min(
                    observed_until,
                    interval.ended_at if interval.ended_at is not None else observed_until,
                )
                if end <= oldest or start >= observed_until:
                    previous = interval
                    continue
                if start > cursor:
                    _append_uncovered_gap(explicit_gaps, computed_gaps, NoData(
                        spec.name, source_id, previous.epoch_id, cursor, start,
                        previous.native_cadence_seconds,
                    ))
                cursor = max(cursor, end)
                previous = interval
            # Dynamic process/device identities stop being relevant once a newer
            # source owns this family. Extending every retired source to "now"
            # made a healthy family look completely unavailable after migration.
            if (
                cursor < observed_until
                and previous.ended_at is not None
                and previous.ended_at >= latest_family_end
            ):
                _append_uncovered_gap(explicit_gaps, computed_gaps, NoData(
                    spec.name, source_id, previous.epoch_id, cursor, observed_until,
                    previous.native_cadence_seconds,
                ))
            for gap in sorted((*explicit_gaps, *computed_gaps), key=lambda item: (item.start, item.end, item.epoch_id)):
                _append_gap(gaps, gap)
    result = tuple(sorted(
        gaps,
        key=lambda item: (item.family, item.source_id, item.start, item.end, item.epoch_id),
    ))
    for item in result:
        for name, value in (
            ("no-data family", item.family),
            ("no-data source_id", item.source_id),
            ("no-data epoch_id", item.epoch_id),
            ("no-data reason", item.reason),
        ):
            try:
                identity.identity_text(value, name)
            except identity.IdentityValidationError as error:
                raise MaterializationError(str(error)) from error
    return result


def _append_uncovered_gap(explicit_gaps: list[NoData], computed_gaps: list[NoData], candidate: NoData) -> None:
    """Add only candidate portions not already owned by an explicit span."""

    portions = [(candidate.start, candidate.end)]
    for existing in explicit_gaps:
        next_portions = []
        for start, end in portions:
            if existing.end <= start or existing.start >= end:
                next_portions.append((start, end))
                continue
            if start < existing.start:
                next_portions.append((start, existing.start))
            if existing.end < end:
                next_portions.append((existing.end, end))
        portions = next_portions
    computed_gaps.extend(
        NoData(
            candidate.family, candidate.source_id, candidate.epoch_id,
            start, end, candidate.native_cadence_seconds, candidate.reason,
        )
        for start, end in portions
        if start < end
    )


def _append_gap(gaps: list[NoData], gap: NoData) -> None:
    if gap.start >= gap.end:
        return
    if gaps and (
        gaps[-1].family, gaps[-1].source_id, gaps[-1].epoch_id,
        gaps[-1].native_cadence_seconds, gaps[-1].end,
    ) == (gap.family, gap.source_id, gap.epoch_id, gap.native_cadence_seconds, gap.start):
        previous = gaps.pop()
        gaps.append(NoData(
            previous.family, previous.source_id, previous.epoch_id,
            previous.start, gap.end, previous.native_cadence_seconds,
        ))
    else:
        gaps.append(gap)


def _clip_gaps(gaps: Iterable[NoData], start: float, end: float) -> tuple[NoData, ...]:
    clipped: list[NoData] = []
    for gap in gaps:
        if gap.end <= start or gap.start >= end:
            continue
        _append_gap(clipped, NoData(
            gap.family, gap.source_id, gap.epoch_id,
            max(gap.start, start), min(gap.end, end), gap.native_cadence_seconds, gap.reason,
        ))
    return tuple(clipped)


def _number(values: Mapping[str, object], name: str) -> float:
    value = values[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MaterializationError(f"{name} must be a finite number")
    return float(value)


def _optional_number(values: Mapping[str, object], name: str) -> float | None:
    return None if name not in values else _number(values, name)


def _validate_generation_inputs(source: int, cache: int, generated: float, observed: float) -> None:
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (source, cache)):
        raise MaterializationError("generation numbers must be non-negative integers")
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in (generated, observed)):
        raise MaterializationError("generation timestamps must be finite and non-negative")
