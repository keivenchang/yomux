# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Pure construction and slicing of the four current YO!stats layers."""

from __future__ import annotations

import bisect
import hashlib
import math
import re
from contextlib import contextmanager
from contextlib import nullcontext
from dataclasses import dataclass
from dataclasses import replace
from typing import Callable
from typing import Iterable
from typing import Iterator
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
from .storage import RebuildBoundExceeded
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
MAX_CACHED_OBSERVATION_PROJECTIONS = 32_768
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


@dataclass(frozen=True, slots=True)
class _ProjectedObservation:
    observed_at: float
    samples: tuple[_Sample, ...]


@dataclass(frozen=True, slots=True)
class _ProjectedUsageAtom:
    observed_at: float
    samples: tuple[_Sample, ...]
    cost_detail: _CostDetailAtom


class ProjectionCache:
    """Bound repeated projection work without retaining the full history."""

    def __init__(self, max_observations: int = MAX_CACHED_OBSERVATION_PROJECTIONS):
        if (
            isinstance(max_observations, bool)
            or not isinstance(max_observations, int)
            or max_observations < 1
        ):
            raise ValueError("max_observations must be a positive integer")
        self.max_observations = max_observations
        self._observations: dict[
            tuple[str, str, str],
            tuple[Observation, _ProjectedObservation],
        ] = {}
        self._generation_keys: set[tuple[str, str, str]] | None = None

    def __len__(self) -> int:
        return len(self._observations)

    @contextmanager
    def generation(self) -> Iterator[None]:
        """Retain only projections that can be reused by the next build."""

        if self._generation_keys is not None:
            raise RuntimeError("projection cache generation is already active")
        self._generation_keys = set()
        try:
            yield
        except BaseException:
            self._generation_keys = None
            raise
        else:
            retained = self._generation_keys
            self._generation_keys = None
            for key in tuple(self._observations):
                if key not in retained:
                    del self._observations[key]

    def observation(self, observation: Observation) -> _ProjectedObservation:
        key = (observation.family, observation.source_id, observation.event_id)
        if self._generation_keys is not None:
            self._generation_keys.add(key)
        cached = self._observations.get(key)
        if cached is not None and cached[0] == observation:
            return cached[1]
        projected = _ProjectedObservation(
            observation.observed_at,
            _observation_samples(observation),
        )
        if cached is None and len(self._observations) >= self.max_observations:
            self._observations.pop(next(iter(self._observations)))
        self._observations[key] = (observation, projected)
        return projected


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
    coverage_gaps: tuple[NoData, ...] | None = None,
    projection_cache: ProjectionCache | None = None,
) -> Generation:
    return _build(
        snapshot, source_generation, cache_generation, generated_at, observed_until,
        price_resolver, None, None, coverage_gaps, projection_cache,
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
    coverage_gaps: tuple[NoData, ...] | None = None,
    projection_cache: ProjectionCache | None = None,
) -> Generation:
    if source_generation < previous.source_generation or cache_generation <= previous.cache_generation:
        raise StaleGenerationError("incremental generation is not newer than its base")
    return _build(
        snapshot, source_generation, cache_generation, generated_at, observed_until,
        price_resolver, previous, frozenset(dirty), coverage_gaps, projection_cache,
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
    coverage_gaps: tuple[NoData, ...] | None,
    projection_cache: ProjectionCache | None,
) -> Generation:
    _validate_generation_inputs(source_generation, cache_generation, generated_at, observed_until)
    bounds = {
        resolution: (
            math.floor(observed_until / resolution) * resolution + resolution,
            LAYER_SECONDS[resolution],
        )
        for resolution in RESOLUTIONS
    }
    all_gaps = (
        _coverage_gaps(
            snapshot,
            min(end - span for end, span in bounds.values()),
            observed_until,
        )
        if coverage_gaps is None
        else coverage_gaps
    )
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
    observation_cells: dict[tuple[int, int], list[_ProjectedObservation]] = {}
    usage_cells: dict[tuple[int, int], list[_ProjectedUsageAtom]] = {}
    projection_cache_generation = (
        projection_cache.generation()
        if projection_cache is not None and previous is not None
        else nullcontext()
    )
    with projection_cache_generation:
        for observation in snapshot.observations:
            cells = []
            for resolution in RESOLUTIONS:
                start = math.floor(observation.observed_at / resolution) * resolution
                if start in shared_fold_starts[resolution]:
                    cells.append((resolution, start))
            if not cells:
                continue
            projected = (
                _ProjectedObservation(
                    observation.observed_at,
                    _observation_samples(observation),
                )
                if projection_cache is None or previous is None
                else projection_cache.observation(observation)
            )
            for cell in cells:
                observation_cells.setdefault(cell, []).append(projected)
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
        cells = []
        for resolution in RESOLUTIONS:
            start = math.floor(atom.observed_at / resolution) * resolution
            if start in shared_fold_starts[resolution]:
                cells.append((resolution, start))
        if not cells:
            continue
        samples, cost_detail = _usage_projection(atom, price_resolver)
        projected = _ProjectedUsageAtom(atom.observed_at, samples, cost_detail)
        for cell in cells:
            usage_cells.setdefault(cell, []).append(projected)
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
    observation_cells: Mapping[object, list[_ProjectedObservation]],
    usage_cells: Mapping[object, list[_ProjectedUsageAtom]],
    observed_until: float,
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
        )
    if any(bucket is None for bucket in buckets):
        raise MaterializationError("incremental layer window is not contiguous")
    return tuple(bucket for bucket in buckets if bucket is not None)


# The open-cell ceiling a streaming full rebuild runs under. One cell per resolution, and no more,
# because the read is ordered by `observed_at` so each resolution's cells arrive contiguously.
#
# It is `len(RESOLUTIONS)` and NOT `len(RESOLUTIONS) * (1 + MAX_PRIVATE_BROWSER_CLIENTS)`: private
# browser overlays would multiply it, but `PrivateOverlay` is constructed nowhere in the product,
# `Generation.private_overlays` is always the empty default, and `_updated_layer_buckets` is never
# called with `private_source_id` -- the parameter exists for a path that only the serving side
# uses, through `slice_generation`. Measured on a real full rebuild: four calls, one per
# resolution, `private_source_id` None in every one. If overlays are ever built, this ceiling and
# the assertion below must both change, which is why it is a formula over the live constants.
MAX_OPEN_FOLD_CELLS = len(RESOLUTIONS)


def _stream_full_layers(
    observations: Iterable[Observation],
    *,
    bounds: Mapping[int, tuple[int, int]],
    usage_cells: Mapping[object, list[_ProjectedUsageAtom]],
    observed_until: float,
    shared_gaps: tuple[NoData, ...],
) -> tuple[Layer, ...]:
    """Build every layer of a FULL rebuild from an ordered observation stream, closing as it goes.

    Only valid when there is no previous generation and no dirty set, which is exactly the full
    rebuild: `build_generation` calls `_build` with `previous=None, dirty=None`, and there
    `_layer_fold_starts` returns every bucket start in the window, both splice guards in
    `_updated_layer_buckets` are skipped, and its loop is already `sorted(touched)` ascending.
    Measured on a real rebuild rather than assumed: four calls, `previous` and `dirty` None in all
    four, `fold_starts` equal to every bucket start in all four. So the incremental path's
    splice-and-patch is not something this has to reproduce -- it is the dirty path's shape.

    **What is bounded.** At most `MAX_OPEN_FOLD_CELLS` folds are open at once, asserted below,
    because the stream is ascending in `observed_at` so a resolution's cell is finished the moment
    the cursor crosses its boundary. Measured by instrumenting this function over a 60,000-real-
    observation fixture: 4 open cells holding **307** accumulators, against the 240,000
    `_ProjectedObservation` objects the current path retains for that same fixture -- 782x fewer.

    Two earlier figures for this exist and both are right about what they counted, so the fixture
    has to travel with the number. **358 / 670x** is a hand-walk model built before this function
    existed; it counted distinct `(series, source_id)` pairs, charging per source only for the
    `*_average_sources` operations, which is not the shape `_SeriesFold` actually holds. Run side by
    side on the identical fixture the two give 358 and 307. **The spec's ~330 accumulators against
    617,243 objects is a different fixture** -- the production store rather than this 60,000-row
    slice -- so its ~1,870x is not comparable to 782x and neither is wrong.

    **What is deliberately NOT bounded here.** `usage_cells` is passed in whole, as the design
    says: 74,011 usage atoms against 1.19 M observations is three orders of magnitude smaller and
    is not the problem. A bucket takes its usage atoms at close, after its observations, which is
    the order `_fold_bucket` uses and therefore the order the compensated sums see.
    """

    open_folds: dict[int, tuple[int, _BucketFold]] = {}
    closed: dict[int, dict[int, Bucket]] = {resolution: {} for resolution in bounds}

    def close(resolution: int) -> None:
        start, fold = open_folds.pop(resolution)
        for atom in usage_cells.get((resolution, start), ()):
            fold.add_usage_atom(atom)
        closed[resolution][start] = fold.close(start, resolution, observed_until)

    for observation in observations:
        projected = _ProjectedObservation(
            observation.observed_at, _observation_samples(observation),
        )
        for resolution, (end, span) in bounds.items():
            start = math.floor(observation.observed_at / resolution) * resolution
            if not end - span <= start < end:
                continue
            current = open_folds.get(resolution)
            if current is not None and current[0] != start:
                if start < current[0]:
                    raise MaterializationError(
                        "streaming rebuild received observations out of order; the open-cell "
                        "bound depends on an ascending observed_at read"
                    )
                close(resolution)
            if resolution not in open_folds:
                open_folds[resolution] = (start, _BucketFold())
            open_folds[resolution][1].add_observation(projected)
            if len(open_folds) > MAX_OPEN_FOLD_CELLS:
                raise MaterializationError(
                    f"streaming rebuild held {len(open_folds)} open cells against a ceiling of "
                    f"{MAX_OPEN_FOLD_CELLS}; the read is no longer ordered"
                )
    for resolution in tuple(open_folds):
        close(resolution)

    layers = []
    for resolution, (end, span) in bounds.items():
        start = end - span
        buckets = []
        for bucket_start in range(start, end, resolution):
            bucket = closed[resolution].get(bucket_start)
            if bucket is None:
                # A bucket with no observations still exists, and may still carry usage atoms.
                empty = _BucketFold()
                for atom in usage_cells.get((resolution, bucket_start), ()):
                    empty.add_usage_atom(atom)
                bucket = empty.close(bucket_start, resolution, observed_until)
            buckets.append(bucket)
        layers.append(Layer(
            resolution, start, end, tuple(buckets), _clip_gaps(shared_gaps, start, end),
        ))
    return tuple(layers)


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
    observations: Iterable[_ProjectedObservation],
    usage_atoms: Iterable[_ProjectedUsageAtom],
    observed_until: float,
) -> Bucket:
    complete = start + duration <= observed_until
    if dirty is not None and identity not in dirty and reusable is not None:
        return reusable if reusable.complete == complete else replace(reusable, complete=complete)
    return _fold_bucket(
        start, duration, observations, usage_atoms, observed_until,
    )


# The three operations whose result depends on which SOURCE a sample came from, and so are the
# only ones whose accumulator needs per-source state. Everything else needs one accumulator per
# series regardless of how many sources contributed.
_SOURCEWISE_OPERATIONS = frozenset({"average_sources", "rate_average_sources", "sum_average_sources"})


class _CompensatedTotal:
    """A running total that matches `sum()` bit for bit, which a plain `+=` does not.

    CPython's `sum()` has a float fast path using Neumaier compensated summation, so
    `sum([...])` and a naive accumulator disagree in the last bits. Measured on real data before
    this existed: one 10-sample `average` series gave 15.103 from `sum()` and 15.102999999999998
    from `+=`, and 6 of the first differing bucket's series values moved. Streaming the fold
    without this would silently change every float series value in the store.

    The int phase is kept exact and only switches to floats when a float arrives, which is what
    `sum()` does -- it starts from int 0 and enters the float path on the first float.
    """

    __slots__ = ("_int_total", "_total", "_compensation")

    def __init__(self) -> None:
        self._int_total = 0
        self._total: float | None = None
        self._compensation = 0.0

    def add(self, value: int | float) -> None:
        if self._total is None:
            if isinstance(value, int) and not isinstance(value, bool):
                self._int_total += value
                return
            self._total, self._compensation = float(self._int_total), 0.0
        total = self._total + value
        if abs(self._total) >= abs(value):
            self._compensation += (self._total - total) + value
        else:
            self._compensation += (value - total) + self._total
        self._total = total

    def value(self) -> int | float:
        if self._total is None:
            return self._int_total
        return self._total + self._compensation


class _SeriesFold:
    """One series' accumulator inside one bucket. Every operation here is order-preserving.

    `_fold_bucket` used to collect every `_Sample` of a bucket into a list and reduce it at the
    end. Each of the eleven operations is computable incrementally -- running total and count,
    running extremum, or a running argmax by `(observed_at, source_id)` -- so the list was never
    needed, only convenient.

    **Order is preserved deliberately, and it is not cosmetic.** `sum()` over a list adds
    left-to-right, and float addition is not associative, so an accumulator that added in a
    different order could differ in the last bits of an `average` or a `rate`. Samples arrive here
    in the same order they were appended before, so the addition sequence is identical.
    """

    __slots__ = ("operation", "total", "count", "minimum", "maximum", "best_key", "best_value",
                 "sources", "source_ids", "first_at", "last_at")


    def __init__(self, sample: _Sample) -> None:
        self.operation = sample.operation
        self.total = _CompensatedTotal()
        self.count = 0
        self.minimum: int | float | None = None
        self.maximum: int | float | None = None
        self.best_key: tuple[float, str] | None = None
        self.best_value: int | float | None = None
        self.sources: dict[str, list] | None = ({} if sample.operation in _SOURCEWISE_OPERATIONS else None)
        self.source_ids: set[str] | None = set() if sample.operation.endswith("_sources") else None
        self.first_at = sample.observed_at
        self.last_at = sample.observed_at

    def add(self, sample: _Sample) -> None:
        if sample.operation != self.operation:
            raise MaterializationError(f"series {sample.series!r} has conflicting fold operations")
        self.total.add(sample.value)
        self.count += 1
        self.minimum = sample.value if self.minimum is None else min(self.minimum, sample.value)
        self.maximum = sample.value if self.maximum is None else max(self.maximum, sample.value)
        key = (sample.observed_at, sample.source_id)
        if self.best_key is None or key > self.best_key:
            self.best_key, self.best_value = key, sample.value
        if self.sources is not None:
            entry = self.sources.get(sample.source_id)
            if entry is None:
                entry = self.sources[sample.source_id] = [_CompensatedTotal(), 0]
            entry[0].add(sample.value)
            entry[1] += 1
        if self.source_ids is not None:
            self.source_ids.add(sample.source_id)
        self.first_at = min(self.first_at, sample.observed_at)
        self.last_at = max(self.last_at, sample.observed_at)

    def value(self, duration: int) -> int | float:
        operation = self.operation
        if operation in ("gauge", "status"):
            return self.best_value
        if operation == "average":
            return self.total.value() / self.count
        if operation == "minimum":
            return self.minimum
        if operation == "maximum":
            return self.maximum
        if operation == "sum":
            return self.total.value()
        if operation == "rate":
            return self.total.value() / duration
        if operation == "rate_per_minute":
            return self.total.value() * 60 / duration
        if operation == "average_sources":
            return sum(total.value() / count for total, count in self.sources.values()) / len(self.sources)
        if operation == "rate_average_sources":
            return sum(total.value() / duration for total, _count in self.sources.values()) / len(self.sources)
        if operation == "sum_average_sources":
            return sum(total.value() for total, _count in self.sources.values()) / len(self.sources)
        raise MaterializationError(f"unknown fold operation {operation!r}")

    def source_count(self) -> int:
        return len(self.source_ids) if self.source_ids is not None else self.count


class _BucketFold:
    """Accumulate one bucket from its observations and usage atoms, and close it once.

    This is the observation-side counterpart of `_CostDetailFold`, and the piece the streaming
    rebuild needs: with the read ordered by `observed_at`, exactly one cell per resolution is open
    at a time, so a caller that closes cells as the cursor passes them holds O(open cells x series)
    instead of O(rows).

    Measured by instrumenting `_stream_full_layers` over a 60,000-real-observation fixture: **4
    cells open at peak holding 307 accumulators, against the 240,000 `_ProjectedObservation`
    objects the current path retains for that fixture -- 782x fewer.** An earlier hand-walk model
    of the same fixture said 358 and 670x; it counted a shape this class does not hold, and
    `_stream_full_layers` carries the reconciliation.
    """

    __slots__ = ("_series", "_timestamps", "_first_at", "_last_at", "_cost")

    def __init__(self) -> None:
        self._series: dict[str, _SeriesFold] = {}
        self._timestamps = 0
        self._first_at: float | None = None
        self._last_at: float | None = None
        self._cost = _CostDetailFold()

    def _add_samples(self, samples: tuple[_Sample, ...], observed_at: float) -> None:
        for sample in samples:
            existing = self._series.get(sample.series)
            if existing is None:
                self._series[sample.series] = _SeriesFold(sample)
                existing = self._series[sample.series]
            existing.add(sample)
        if samples:
            self._timestamps += 1
            self._first_at = observed_at if self._first_at is None else min(self._first_at, observed_at)
            self._last_at = observed_at if self._last_at is None else max(self._last_at, observed_at)

    def add_observation(self, projected: _ProjectedObservation) -> None:
        self._add_samples(projected.samples, projected.observed_at)

    def add_usage_atom(self, projected: _ProjectedUsageAtom) -> None:
        self._add_samples(projected.samples, projected.observed_at)
        self._cost.add(projected.cost_detail)

    def close(self, start: int, duration: int, observed_until: float) -> Bucket:
        series = []
        for name in sorted(self._series):
            try:
                identity.identity_text(name, "series name")
            except identity.IdentityValidationError as error:
                raise MaterializationError(str(error)) from error
            fold = self._series[name]
            result = fold.value(duration)
            if name == "cost_micro_usd" and (
                isinstance(result, bool) or not isinstance(result, int) or result > MAX_SAFE_INTEGER
            ):
                raise MaterializationError("cost projection must remain an exact JSON-safe integer")
            series.append(SeriesValue(
                name, result, fold.source_count(), fold.first_at, fold.last_at,
            ))
        return Bucket(
            start,
            duration,
            tuple(series),
            self._timestamps,
            self._first_at,
            self._last_at,
            start + duration <= observed_until,
            self._cost.close(),
        )


def _fold_bucket(
    start: int,
    duration: int,
    observations: Iterable[_ProjectedObservation],
    usage_atoms: Iterable[_ProjectedUsageAtom],
    observed_until: float,
) -> Bucket:
    """The whole-input form, expressed as the fold so there is only one implementation.

    Same reasoning as `_build_bucket_cost_detail`: a second reducing version beside the fold would
    be two things to drift apart, and the equivalence that matters would be between two copies
    rather than a property of one.
    """

    fold = _BucketFold()
    for observation in observations:
        fold.add_observation(observation)
    for atom in usage_atoms:
        fold.add_usage_atom(atom)
    return fold.close(start, duration, observed_until)


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
        process_percent = _number(payload, "process_percent")
        system_percent = _number(payload, "system_percent")
        samples = [
            _Sample(f"cpu_percent:{source}", "average", process_percent, at, source),
            _Sample(f"cpu_min_percent:{source}", "minimum", process_percent, at, source),
            _Sample(f"cpu_max_percent:{source}", "maximum", process_percent, at, source),
            _Sample("system_cpu_percent", "average", system_percent, at, source),
            _Sample("system_cpu_min_percent", "minimum", system_percent, at, source),
            _Sample("system_cpu_max_percent", "maximum", system_percent, at, source),
        ]
        for binary, percent in payload.get("process_cpu_percent", {}).items():
            samples.extend((
                _Sample(f"process_cpu_percent:{binary}", "average", float(percent), at, source),
                _Sample(f"process_cpu_min_percent:{binary}", "minimum", float(percent), at, source),
                _Sample(f"process_cpu_max_percent:{binary}", "maximum", float(percent), at, source),
            ))
        return tuple(samples)
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
    if observation.family == "service_load":
        samples = [
            _Sample(f"service_cpu_percent:{source}", "average", _number(payload, "cpu_percent"), at, source),
            _Sample(f"service_cpu_min_percent:{source}", "minimum", _number(payload, "cpu_percent"), at, source),
            _Sample(f"service_cpu_max_percent:{source}", "maximum", _number(payload, "cpu_percent"), at, source),
        ]
        if payload.get("rss_bytes") is not None:
            samples.append(_Sample(
                f"service_rss_bytes:{source}", "average",
                _number(payload, "rss_bytes"), at, source,
            ))
        return tuple(samples)
    fields: Mapping[str, str] | None = {
        "gpu": {
            f"gpu_util_percent:{source}": "util_percent",
            f"gpu_memory_bytes:{source}": "memory_used_bytes",
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
        samples = [
            _Sample(series, spec.fold_kind.value, _number(payload, field), at, source)
            for series, field in fields.items()
            if field in payload and payload[field] is not None
        ]
        if observation.family == "system_memory":
            samples.extend(
                _Sample(
                    f"process_memory_bytes:{binary}",
                    spec.fold_kind.value,
                    float(rss_bytes),
                    at,
                    source,
                )
                for binary, rss_bytes in payload.get("process_memory_bytes", {}).items()
            )
        return tuple(samples)
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


class _CostDetailFold:
    """Accumulate one bucket's cost detail atom by atom, and rank once at close.

    `_build_bucket_cost_detail` used to take a whole tuple of atoms and walk it twice: once to
    total the model, agent and evidence scores, then `_ranked_cost_keys` to pick a top-N, then
    again to accumulate attribution rows for the selected keys only. That second pass is why the
    caller had to hold every atom of a bucket until the bucket closed.

    **Ranking is not distributive over concatenation**, so a fold that ranks per chunk and merges
    the rankings is wrong: a key inside chunk A's top-N and outside chunk B's would end up with
    attribution from A and nothing from B. This accumulates attribution for EVERY key it sees and
    applies the ranking once, at close. Selection then only filters, and filtering a complete row
    is exactly what the second pass did.

    **What is retained, and why it is bounded.** One score and one attribution row per DISTINCT
    key, never per atom -- so the state is O(distinct keys in this bucket), not O(atoms). Measured
    on 75,379 real usage atoms: 5 model keys, 62 agent keys, 18 evidence keys. `MAX_FOLD_KEYS`
    caps each map so a pathological bucket cannot make the accumulator the thing that grows; the
    cap is a rebuild bound, so exceeding it abandons the candidate with a reason rather than
    silently dropping keys and reporting a cost report that quietly omits a payer.

    The metadata conflict is raised only for keys that survive ranking, because that is when the
    old code looked: it populated `model_metadata` inside `if atom.model_key in selected_models`.
    A conflict on a key the ranking discards was invisible then and stays invisible now.
    """

    # 64x the largest of the three caps (32). A bucket needs more distinct payers than that to
    # trip it, and if one does, the truncation counts it would report are meaningless anyway.
    MAX_FOLD_KEYS = 2_048

    __slots__ = (
        "_dimensions", "_priced", "_unpriced", "_model_scores", "_agent_scores",
        "_evidence_scores", "_models", "_agents", "_evidence", "_model_metadata",
        "_model_conflicts", "_agent_sources", "_agent_labels", "_saw_any",
    )

    def __init__(self) -> None:
        self._dimensions = _empty_cost_dimensions()
        self._priced = {"atoms": 0, "tokens": 0}
        self._unpriced = {"atoms": 0, "tokens": 0}
        self._model_scores: dict[str, int] = {}
        self._agent_scores: dict[str, int] = {}
        self._evidence_scores: dict[str, int] = {}
        self._models: dict[str, dict[str, object]] = {}
        self._agents: dict[str, dict[str, object]] = {}
        self._evidence: dict[str, CostEvidenceValue] = {}
        self._model_metadata: dict[str, tuple[str, str]] = {}
        self._model_conflicts: set[str] = set()
        self._agent_sources: dict[str, set[str]] = {}
        self._agent_labels: dict[str, set[str]] = {}
        self._saw_any = False

    def _require_key_budget(self) -> None:
        for name, keys in (
            ("cost_fold_model_keys", self._model_scores),
            ("cost_fold_agent_keys", self._agent_scores),
            ("cost_fold_evidence_keys", self._evidence_scores),
        ):
            if len(keys) > self.MAX_FOLD_KEYS:
                raise RebuildBoundExceeded(name, len(keys), self.MAX_FOLD_KEYS)

    def add(self, atom: _CostDetailAtom) -> None:
        self._saw_any = True
        attribution_score = 1 + (2 * atom.quantity if atom.is_tokens else 0)
        attribution_score += atom.micro_usd or 0
        attribution_score += atom.api_list_micro_usd or 0
        self._model_scores[atom.model_key] = (
            self._model_scores.get(atom.model_key, 0) + attribution_score
        )
        self._agent_scores[atom.agent_key] = (
            self._agent_scores.get(atom.agent_key, 0) + attribution_score
        )
        if atom.evidence is not None:
            evidence_score = 1 + (atom.quantity if atom.is_tokens else 0)
            evidence_score += atom.micro_usd or 0
            evidence_score += atom.api_list_micro_usd or 0
            self._evidence_scores[atom.evidence.key] = (
                self._evidence_scores.get(atom.evidence.key, 0) + evidence_score
            )
        self._require_key_budget()

        state = self._priced if atom.priced else self._unpriced
        _add_exact(state, "atoms", 1, "cost bucket coverage")
        if atom.is_tokens:
            _add_exact(self._dimensions[atom.dimension], "tokens", atom.quantity, "cost bucket tokens")
            _add_exact(state, "tokens", atom.quantity, "cost bucket coverage")
        if atom.micro_usd is not None:
            _add_exact(
                self._dimensions[atom.dimension], "micro_usd", atom.micro_usd,
                "cost bucket micro_usd",
            )
        if atom.api_list_micro_usd is not None:
            _add_exact(
                self._dimensions[atom.dimension], "api_list_micro_usd",
                atom.api_list_micro_usd, "cost bucket API-list micro_usd",
            )

        metadata = (atom.provider, atom.model)
        if self._model_metadata.setdefault(atom.model_key, metadata) != metadata:
            self._model_conflicts.add(atom.model_key)
        row = self._models.setdefault(atom.model_key, _empty_attribution(atom.model_key))
        _add_cost_atom_to_attribution(row, atom, "cost model attribution")
        row = self._agents.setdefault(atom.agent_key, _empty_attribution(atom.agent_key))
        self._agent_sources.setdefault(atom.agent_key, set()).add(atom.agent_source)
        self._agent_labels.setdefault(atom.agent_key, set()).add(atom.agent_label)
        _add_cost_atom_to_attribution(row, atom, "cost agent attribution")
        if atom.evidence is not None:
            self._evidence[atom.evidence.key] = _merge_cost_evidence(
                self._evidence.get(atom.evidence.key), atom.evidence,
            )

    def close(self) -> BucketCostDetail:
        if not self._saw_any:
            return BucketCostDetail()
        selected_models = _ranked_cost_keys(self._model_scores, MAX_COST_DETAIL_MODELS)
        selected_agents = _ranked_cost_keys(self._agent_scores, MAX_COST_DETAIL_AGENTS)
        selected_evidence = _ranked_cost_keys(self._evidence_scores, MAX_COST_DETAIL_EVIDENCE)
        if self._model_conflicts & selected_models:
            raise MaterializationError("cost model metadata conflicts within one bucket")
        model_values = tuple(
            _freeze_attribution(
                self._models[key],
                provider=self._model_metadata[key][0],
                model=self._model_metadata[key][1],
            )
            for key in sorted(selected_models)
        )
        agent_values = tuple(
            _freeze_attribution(
                self._agents[key],
                source=(
                    next(iter(self._agent_sources[key]))
                    if len(self._agent_sources[key]) == 1 else "mixed"
                ),
                label=(
                    next(iter(self._agent_labels[key]))
                    if len(self._agent_labels[key]) == 1 else "mixed"
                ),
            )
            for key in sorted(selected_agents)
        )
        return BucketCostDetail(
            _freeze_cost_dimensions(self._dimensions),
            CostCoverage(self._priced["atoms"], self._priced["tokens"]),
            CostCoverage(self._unpriced["atoms"], self._unpriced["tokens"]),
            model_values,
            agent_values,
            tuple(self._evidence[key] for key in sorted(selected_evidence)),
            max(0, len(self._model_scores) - len(selected_models)),
            max(0, len(self._agent_scores) - len(selected_agents)),
            max(0, len(self._evidence_scores) - len(selected_evidence)),
        )


def _build_bucket_cost_detail(atoms: tuple[_CostDetailAtom, ...]) -> BucketCostDetail:
    """The whole-input form, expressed as the fold so there is only one implementation.

    Keeping a separate two-pass version would leave two things to drift apart, and the equivalence
    that matters would be between two copies rather than a property of one. Feeding a whole tuple
    in is now the degenerate case of feeding it in chunks.
    """

    fold = _CostDetailFold()
    for atom in atoms:
        fold.add(atom)
    return fold.close()


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


def merge_normalized_coverage_model(
    retained_coverage: tuple[CoverageEpoch, ...],
    retained_unavailable: tuple[UnavailableSpan, ...],
    coverage: tuple[CoverageEpoch, ...],
    unavailable: tuple[UnavailableSpan, ...],
) -> tuple[tuple[CoverageEpoch, ...], tuple[UnavailableSpan, ...]]:
    """Apply accepted facts without renormalizing unchanged retained history."""

    if not coverage and not unavailable:
        return retained_coverage, retained_unavailable
    replacements = {
        (item.family, item.source_id, item.epoch_id): item
        for item in coverage
    }
    merged = tuple(
        replacements.pop((item.family, item.source_id, item.epoch_id), item)
        for item in retained_coverage
    )
    new_coverage = bool(replacements)
    if replacements:
        merged = tuple(sorted(
            (*merged, *replacements.values()),
            key=lambda item: (
                item.started_at, item.family, item.source_id, item.epoch_id,
            ),
        ))
    if not new_coverage and not unavailable:
        return merged, retained_unavailable
    spans = {
        (item.family, item.source_id, item.epoch_id, item.started_at): item
        for item in retained_unavailable
    }
    spans.update({
        (item.family, item.source_id, item.epoch_id, item.started_at): item
        for item in unavailable
    })
    return normalize_coverage_model(merged, tuple(sorted(
        spans.values(),
        key=lambda item: (
            item.started_at, item.family, item.source_id, item.epoch_id,
        ),
    )))


def _coverage_gaps(snapshot: StoreSnapshot, oldest: float, observed_until: float) -> tuple[NoData, ...]:
    gaps: list[NoData] = []
    if snapshot.coverage_normalized:
        coverage_epochs = snapshot.coverage_epochs
        unavailable_spans = snapshot.unavailable_spans
    else:
        coverage_epochs, unavailable_spans = normalize_coverage_model(
            snapshot.coverage_epochs,
            snapshot.unavailable_spans,
        )
    coverage_by_source: dict[tuple[str, str], list[CoverageEpoch]] = {}
    unavailable_by_source: dict[tuple[str, str], list[UnavailableSpan]] = {}
    sources_by_family: dict[str, set[str]] = {}
    latest_family_end: dict[str, float] = {}
    for item in coverage_epochs:
        key = (item.family, item.source_id)
        coverage_by_source.setdefault(key, []).append(item)
        sources_by_family.setdefault(item.family, set()).add(item.source_id)
        item_end = observed_until if item.ended_at is None else item.ended_at
        latest_family_end[item.family] = max(
            latest_family_end.get(item.family, oldest),
            item_end,
        )
    for item in unavailable_spans:
        key = (item.family, item.source_id)
        unavailable_by_source.setdefault(key, []).append(item)
        sources_by_family.setdefault(item.family, set()).add(item.source_id)
    for spec in CURRENT_FAMILIES:
        if not spec.no_data_eligible:
            continue
        coverage_family = spec.coverage_family
        for source_id in sorted(sources_by_family.get(coverage_family, ())):
            intervals = sorted(
                coverage_by_source.get((coverage_family, source_id), ()),
                key=lambda item: (item.started_at, item.epoch_id),
            )
            explicit_gaps = sorted((
                NoData(
                    spec.name, source_id, item.epoch_id,
                    max(oldest, item.started_at), min(observed_until, item.ended_at),
                    item.native_cadence_seconds, item.reason,
                )
                for item in unavailable_by_source.get((coverage_family, source_id), ())
                if item.ended_at > oldest
                and item.started_at < observed_until
            ), key=lambda item: (item.start, item.end, item.epoch_id))
            if not intervals:
                for gap in explicit_gaps:
                    _append_gap(gaps, gap)
                continue
            # Built once per source: explicit_gaps is already start-ordered and
            # non-overlapping, so this is the seek index for gap subtraction.
            explicit_starts = [item.start for item in explicit_gaps]
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
                    _append_uncovered_gap(explicit_gaps, explicit_starts, computed_gaps, NoData(
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
                and previous.ended_at >= latest_family_end.get(coverage_family, oldest)
            ):
                _append_uncovered_gap(explicit_gaps, explicit_starts, computed_gaps, NoData(
                    spec.name, source_id, previous.epoch_id, cursor, observed_until,
                    previous.native_cadence_seconds,
                ))
            for gap in sorted((*explicit_gaps, *computed_gaps), key=lambda item: (item.start, item.end, item.epoch_id)):
                _append_gap(gaps, gap)
    result = tuple(sorted(
        gaps,
        key=lambda item: (item.family, item.source_id, item.start, item.end, item.epoch_id),
    ))
    if not snapshot.coverage_normalized:
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


def _coverage_latest_metadata(
    coverage: tuple[CoverageEpoch, ...],
) -> tuple[
    dict[tuple[str, str], CoverageEpoch],
    dict[str, float],
]:
    """Index the latest retained epoch per source and family."""

    latest_by_source: dict[tuple[str, str], CoverageEpoch] = {}
    for item in coverage:
        key = (item.family, item.source_id)
        previous = latest_by_source.get(key)
        if previous is None or (item.started_at, item.epoch_id) > (
            previous.started_at, previous.epoch_id
        ):
            latest_by_source[key] = item
    latest_by_family: dict[str, float] = {}
    for item in latest_by_source.values():
        item_end = math.inf if item.ended_at is None else float(item.ended_at)
        latest_by_family[item.family] = max(
            latest_by_family.get(item.family, float("-inf")), item_end,
        )
    return latest_by_source, latest_by_family


def _static_coverage_gaps(
    gaps: tuple[NoData, ...],
    latest_by_source: Mapping[tuple[str, str], CoverageEpoch],
    latest_by_family: Mapping[str, float],
    observed_until: float,
) -> dict[tuple[str, str], tuple[NoData, ...]]:
    """Retain gap geometry that cannot change merely because now advances."""

    coverage_family_by_name = {
        spec.name: spec.coverage_family
        for spec in CURRENT_FAMILIES
        if spec.no_data_eligible
    }
    grouped: dict[tuple[str, str], list[NoData]] = {}
    for gap in gaps:
        coverage_family = coverage_family_by_name.get(gap.family)
        latest = (
            None
            if coverage_family is None
            else latest_by_source.get((coverage_family, gap.source_id))
        )
        latest_end = (
            None if latest is None or latest.ended_at is None
            else float(latest.ended_at)
        )
        dynamic_tail = (
            gap.reason == "coverage_gap"
            and latest is not None
            and latest.epoch_id == gap.epoch_id
            and latest_end is not None
            and gap.start >= latest_end
            and gap.end <= observed_until
            and latest_end >= latest_by_family.get(coverage_family, math.inf)
        )
        if not dynamic_tail:
            grouped.setdefault((gap.family, gap.source_id), []).append(gap)
    return {key: tuple(values) for key, values in grouped.items()}


def _compose_coverage_gaps(
    static_by_source: Mapping[tuple[str, str], tuple[NoData, ...]],
    latest_by_source: Mapping[tuple[str, str], CoverageEpoch],
    latest_by_family: Mapping[str, float],
    static_oldest: float,
    oldest: float,
    observed_until: float,
) -> tuple[NoData, ...]:
    """Combine cached historical gaps with the small moving live tail."""

    name_by_coverage_family = {
        spec.coverage_family: spec.name
        for spec in CURRENT_FAMILIES
        if spec.no_data_eligible
    }
    grouped = {
        key: (
            values
            if oldest == static_oldest
            else _clip_gaps(values, oldest, observed_until)
        )
        for key, values in static_by_source.items()
    }
    for (coverage_family, source_id), latest in latest_by_source.items():
        family = name_by_coverage_family.get(coverage_family)
        latest_end = latest.ended_at
        if (
            family is None
            or latest_end is None
            or latest_end >= observed_until
            or latest_end < latest_by_family.get(coverage_family, math.inf)
        ):
            continue
        key = (family, source_id)
        explicit = list(grouped.get(key, ()))
        computed: list[NoData] = []
        _append_uncovered_gap(
            explicit,
            [item.start for item in explicit],
            computed,
            NoData(
                family,
                source_id,
                latest.epoch_id,
                max(oldest, float(latest_end)),
                observed_until,
                latest.native_cadence_seconds,
            ),
        )
        combined: list[NoData] = []
        for gap in sorted(
            (*explicit, *computed),
            key=lambda item: (item.start, item.end, item.epoch_id),
        ):
            _append_gap(combined, gap)
        grouped[key] = tuple(combined)
    result: list[NoData] = []
    for key in sorted(grouped):
        for gap in grouped[key]:
            _append_gap(result, gap)
    return tuple(result)


def _append_uncovered_gap(
    explicit_gaps: list[NoData],
    explicit_starts: list[float],
    computed_gaps: list[NoData],
    candidate: NoData,
) -> None:
    """Add only candidate portions not already owned by an explicit span.

    ``explicit_gaps`` is sorted by start and non-overlapping within one
    family/source: ``normalize_unavailable_spans`` establishes that invariant
    and the clip to ``[oldest, observed_until]`` preserves it. Only the spans
    that actually overlap the candidate can subtract anything from it, so
    ``explicit_starts`` lets us seek to the first of them instead of walking
    the whole list. The previous full scan made each call O(explicit spans)
    and each build O(coverage epochs x explicit spans). On a live statsd with
    a steady-state 24h window (4023 coverage epochs, 3534 spans) that scan was
    15.6s of CPU in a 40s sampled profile -- about 39 points of one core and
    the largest single owner, though not the only one -- and it grew
    quadratically as the retained window filled.
    """

    cursor = candidate.start
    end = candidate.end
    if cursor >= end:
        return
    index = bisect.bisect_right(explicit_starts, cursor)
    # The span starting at or before the cursor may still cover it.
    if index and explicit_gaps[index - 1].end > cursor:
        index -= 1
    while index < len(explicit_gaps):
        existing = explicit_gaps[index]
        if existing.start >= end:
            break
        if existing.start > cursor:
            computed_gaps.append(NoData(
                candidate.family, candidate.source_id, candidate.epoch_id,
                cursor, existing.start, candidate.native_cadence_seconds, candidate.reason,
            ))
        if existing.end > cursor:
            cursor = existing.end
            if cursor >= end:
                return
        index += 1
    computed_gaps.append(NoData(
        candidate.family, candidate.source_id, candidate.epoch_id,
        cursor, end, candidate.native_cadence_seconds, candidate.reason,
    ))


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
        _append_gap(
            clipped,
            gap
            if gap.start >= start and gap.end <= end
            else NoData(
                gap.family, gap.source_id, gap.epoch_id,
                max(gap.start, start), min(gap.end, end),
                gap.native_cadence_seconds, gap.reason,
            ),
        )
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
