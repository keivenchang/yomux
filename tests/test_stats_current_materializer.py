# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused contracts for the pure current YO!stats materializer."""

import inspect
import json
import math
import random
from dataclasses import FrozenInstanceError

import pytest

from yolomux_lib.stats_current import materializer
from yolomux_lib.stats_current import pricing as current_pricing
from yolomux_lib.stats_current import service as service_module
from yolomux_lib.stats_current.materializer import LAYER_SECONDS
from yolomux_lib.stats_current.materializer import RANGES
from yolomux_lib.stats_current.materializer import RESOLUTIONS
from yolomux_lib.stats_current.materializer import DirtyCell
from yolomux_lib.stats_current.materializer import MaterializationError
from yolomux_lib.stats_current.materializer import StaleGenerationError
from yolomux_lib.stats_current.materializer import UnsupportedSliceError
from yolomux_lib.stats_current.materializer import accept_generation
from yolomux_lib.stats_current.materializer import build_generation
from yolomux_lib.stats_current.materializer import resolve_resolution
from yolomux_lib.stats_current.materializer import slice_generation
from yolomux_lib.stats_current.materializer import update_generation
from yolomux_lib.stats_current.storage import CoverageEpoch
from yolomux_lib.stats_current.storage import Observation
from yolomux_lib.stats_current.storage import SchemaMetadata
from yolomux_lib.stats_current.storage import StoreSnapshot
from yolomux_lib.stats_current.storage import UsageAtom
from yolomux_lib.stats_current.storage import UnavailableSpan
from yolomux_lib.stats_current import resolution as stats_resolution


def _snapshot(*, observations=(), coverage=(), usage=(), unavailable=()):
    return StoreSnapshot(
        SchemaMetadata(5, 1, 1), tuple(observations), tuple(coverage), tuple(usage), (),
        tuple(unavailable),
    )


def _cpu(at, total, *, source="host", epoch="epoch"):
    return Observation(f"cpu-{source}-{at}", "cpu", source, at, epoch, 1, {
        "process_percent": total,
        "system_percent": total * 2,
    })


def _series(bucket):
    return {item.name: item for item in bucket.series}


def _projection(micro_usd):
    if micro_usd is None:
        return current_pricing.UsagePriceProjection(None, None, None)
    return current_pricing.UsagePriceProjection(
        micro_usd,
        micro_usd,
        current_pricing.PricingEvidence(
            "catalog-model", "2.50", 1_000_000, "2026-07-09T00:00:00Z",
            "seed", "https://developers.openai.com/api/docs/pricing", 3,
        ),
    )


def _build(snapshot, *, source=1, cache=1, until=20.0, price_resolver=None):
    return build_generation(
        snapshot, source_generation=source, cache_generation=cache,
        generated_at=until, observed_until=until, price_resolver=price_resolver,
    )


def test_builds_only_four_epoch_aligned_immutable_layers():
    generation = _build(_snapshot(), until=615.25)
    assert tuple(layer.resolution for layer in generation.layers) == RESOLUTIONS
    assert {layer.resolution: layer.end - layer.start for layer in generation.layers} == LAYER_SECONDS
    for layer in generation.layers:
        assert len(layer.buckets) == LAYER_SECONDS[layer.resolution] // layer.resolution
        assert all(bucket.start % layer.resolution == 0 for bucket in layer.buckets)
        assert all(bucket.duration == layer.resolution for bucket in layer.buckets)
        assert len({(bucket.start, bucket.duration) for bucket in layer.buckets}) == len(layer.buckets)
    with pytest.raises(FrozenInstanceError):
        generation.cache_generation = 2


def test_one_fold_handles_average_gauge_rate_status_tokens_and_cost():
    observations = (
        _cpu(11, 2), _cpu(12, 4),
        Observation("gpu-11", "gpu", "host", 11, "epoch", 1, {
            "util_percent": 20, "memory_used_bytes": 100,
            "memory_capacity_bytes": 200, "label": "GPU 0",
        }),
        Observation("gpu-13", "gpu", "host", 13, "epoch", 1, {
            "util_percent": 40, "memory_used_bytes": 200,
            "memory_capacity_bytes": 200, "label": "GPU 0",
        }),
        Observation("browser-12", "browser", "client", 12, "epoch", 1, {
            "kind": "api", "latency_ms": 15, "bytes": 200,
        }),
        Observation("status-12", "agent_status", "host", 12, "epoch", 1, {
            "states": {"a": "ask", "b": "run", "c": "run", "d": "idle"},
        }),
    )
    atom = UsageAtom("event", "output", "text", "none", "tokens", 12, {
        "quantity": 100, "provider": "openai", "agent_id": "sol", "model": "gpt",
        "telemetry_complete": True,
    })
    generation = _build(
            _snapshot(observations=observations, usage=(atom,)), price_resolver=lambda _atom: _projection(25),
    )
    bucket = next(
        item for item in materializer.slice_generation(
            generation, 300, 10, private_source_id="client",
        ).buckets
        if item.start == 10
    )
    values = _series(bucket)
    assert values["cpu_percent:host"].value == 3
    assert values["gpu_util_percent:host"].value == 40
    assert values["browser_api_per_second"].value == 0.1
    assert values["browser_latency_ms"].value == 15
    assert values["run_agents"].value == 2
    assert values["agent_tokens_per_minute:sol"].value == 600
    assert values["model_tokens_per_minute:output:gpt"].value == 600
    assert values["model_tokens_per_minute:all:gpt"].value == 600
    assert values["usage_tokens"].value == 100
    assert values["cost_micro_usd"].value == 25
    assert bucket.source_count == len(observations) + 1
    assert (bucket.first_observed_at, bucket.last_observed_at) == (11, 13)


def test_browser_observations_are_private_overlays_not_shared_series():
    observations = (
        Observation("a-api", "browser", "browser:a", 12, "epoch:a", 1, {
            "kind": "api", "latency_ms": 15, "bytes": 200,
        }),
        Observation("a-disconnect", "browser", "browser:a", 13, "epoch:a", 1, {
            "kind": "disconnect", "duration_ms": 40,
        }),
        Observation("b-sse", "browser", "browser:b", 12, "epoch:b", 1, {
            "kind": "sse", "latency_ms": 25,
        }),
    )
    generation = _build(_snapshot(observations=observations))

    shared = next(bucket for bucket in generation.layer(10).buckets if bucket.start == 10)
    client_a = next(
        bucket
        for bucket in materializer.slice_generation(
            generation, 300, 10, private_source_id="browser:a",
        ).buckets
        if bucket.start == 10
    )
    client_b = next(
        bucket
        for bucket in materializer.slice_generation(
            generation, 300, 10, private_source_id="browser:b",
        ).buckets
        if bucket.start == 10
    )
    unknown = next(
        bucket
        for bucket in materializer.slice_generation(
            generation, 300, 10, private_source_id="browser:unknown",
        ).buckets
        if bucket.start == 10
    )

    assert not any(item.name.startswith("browser_") for item in shared.series)
    assert {item.name for item in client_a.series} == {
        "browser_api_per_second", "browser_bandwidth_bytes_per_second",
        "browser_disconnected_ms", "browser_latency_ms",
    }
    assert {item.name for item in client_b.series} == {
        "browser_latency_ms", "browser_sse_per_second",
    }
    assert unknown == shared


def test_private_browser_overlay_count_is_bounded_by_most_recent_source():
    observations = tuple(
        Observation(f"browser-{index}", "browser", f"browser:{index}", 10 + index, f"epoch:{index}", 1, {
            "kind": "api",
        })
        for index in range(materializer.MAX_PRIVATE_BROWSER_CLIENTS + 1)
    )

    generation = _build(_snapshot(observations=observations), until=30)

    assert len(generation.private_source_ids) == materializer.MAX_PRIVATE_BROWSER_CLIENTS
    assert "browser:0" not in generation.private_source_ids
    assert generation.private_source_ids == tuple(
        f"browser:{index}"
        for index in range(materializer.MAX_PRIVATE_BROWSER_CLIENTS, 0, -1)
    )


def test_incremental_browser_overlay_reuses_shared_and_unmodified_private_buckets():
    first_snapshot = _snapshot(observations=(
        Observation("a-1", "browser", "browser:a", 12, "epoch:a", 1, {"kind": "api"}),
        Observation("b-1", "browser", "browser:b", 12, "epoch:b", 1, {"kind": "sse"}),
        _cpu(12, 5),
    ))
    first = _build(first_snapshot, source=1, cache=1, until=30)
    second_snapshot = _snapshot(observations=(*first_snapshot.observations,
        Observation("a-2", "browser", "browser:a", 22, "epoch:a", 2, {"kind": "api"}),
    ))

    second = update_generation(
        first,
        second_snapshot,
        (DirtyCell(10, 20),),
        source_generation=2,
        cache_generation=2,
        generated_at=30,
        observed_until=30,
    )

    first_shared = next(bucket for bucket in first.layer(10).buckets if bucket.start == 10)
    second_shared = next(bucket for bucket in second.layer(10).buckets if bucket.start == 10)
    first_b = next(
        bucket for bucket in first.private_layer("browser:b", 10).buckets if bucket.start == 10
    )
    second_b = next(
        bucket for bucket in second.private_layer("browser:b", 10).buckets if bucket.start == 10
    )
    second_a = next(
        bucket for bucket in second.private_layer("browser:a", 10).buckets if bucket.start == 20
    )

    assert second_shared is first_shared
    assert second_b is first_b
    assert {item.name for item in second_a.series} == {"browser_api_per_second"}


def test_shifted_incremental_layers_match_full_build_with_private_overlays_and_cost():
    old_cpu = _cpu(598.2, 5)
    new_cpu = _cpu(600.2, 7)
    old_browser_a = Observation(
        "browser-a-old", "browser", "browser:a", 598.3, "epoch:a", 1,
        {"kind": "api", "latency_ms": 10},
    )
    new_browser_a = Observation(
        "browser-a-new", "browser", "browser:a", 600.3, "epoch:a", 1,
        {"kind": "sse", "latency_ms": 20},
    )
    browser_b = Observation(
        "browser-b-old", "browser", "browser:b", 598.4, "epoch:b", 1,
        {"kind": "api", "bytes": 100},
    )
    old_atom = UsageAtom("usage-old", "input", "text", "none", "tokens", 598.5, {
        "quantity": 10, "provider": "openai", "agent_id": "sol", "model": "gpt",
        "telemetry_complete": True,
    })
    new_atom = UsageAtom("usage-new", "output", "text", "none", "tokens", 600.4, {
        "quantity": 20, "provider": "openai", "agent_id": "sol", "model": "gpt",
        "telemetry_complete": True,
    })
    first_snapshot = _snapshot(
        observations=(old_cpu, old_browser_a, browser_b),
        usage=(old_atom,),
    )
    second_snapshot = _snapshot(
        observations=(old_cpu, new_cpu, old_browser_a, new_browser_a, browser_b),
        usage=(old_atom, new_atom),
    )
    resolver = lambda _atom: _projection(25)
    first = _build(
        first_snapshot,
        source=1,
        cache=1,
        until=599.5,
        price_resolver=resolver,
    )
    changed_times = (new_cpu.observed_at, new_browser_a.observed_at, new_atom.observed_at)
    dirty = frozenset(
        DirtyCell(resolution, math.floor(observed_at / resolution) * resolution)
        for observed_at in changed_times
        for resolution in RESOLUTIONS
    )

    incremental = update_generation(
        first,
        second_snapshot,
        dirty,
        source_generation=2,
        cache_generation=2,
        generated_at=601.2,
        observed_until=601.2,
        price_resolver=resolver,
    )
    full = build_generation(
        second_snapshot,
        source_generation=2,
        cache_generation=2,
        generated_at=601.2,
        observed_until=601.2,
        price_resolver=resolver,
    )

    assert incremental == full
    old_shared = next(bucket for bucket in first.layer(1).buckets if bucket.start == 598)
    new_shared = next(bucket for bucket in incremental.layer(1).buckets if bucket.start == 598)
    old_private = next(
        bucket for bucket in first.private_layer("browser:b", 1).buckets
        if bucket.start == 598
    )
    new_private = next(
        bucket for bucket in incremental.private_layer("browser:b", 1).buckets
        if bucket.start == 598
    )
    assert new_shared is old_shared
    assert new_private is old_private


def test_usage_deletion_removes_agent_model_and_cost_from_materialized_layers():
    legacy = UsageAtom(
        "codex:child-thread:3", "output", "text", "none", "tokens", 12.5,
        {
            "quantity": 7,
            "provider": "openai",
            "model": "unknown",
            "agent_id": "yo8881|0|codex",
            "thread_id": "child-thread",
            "execution_source": "codex",
            "telemetry_complete": True,
        },
    )
    first = _build(_snapshot(usage=(legacy,)), source=1, cache=1, until=20)
    dirty = frozenset(
        DirtyCell(resolution, math.floor(legacy.observed_at / resolution) * resolution)
        for resolution in RESOLUTIONS
    )

    deleted = update_generation(
        first,
        _snapshot(),
        dirty,
        source_generation=2,
        cache_generation=2,
        generated_at=21,
        observed_until=21,
    )

    assert deleted == build_generation(
        _snapshot(),
        source_generation=2,
        cache_generation=2,
        generated_at=21,
        observed_until=21,
    )
    assert all(
        not name.startswith(("agent_tokens_per_minute:", "model_tokens_per_minute:"))
        for bucket in deleted.layer(1).buckets
        for name in _series(bucket)
    )
    report = materializer.build_cost_report(
        materializer.slice_generation(deleted, 300, 1),
    )
    assert report["total_tokens"] == 0
    assert report["total_micro_usd"] == 0
    assert report["models"] == []
    assert report["agents"] == []


def test_incremental_fold_work_is_bounded_by_dirty_and_new_edge_cells(monkeypatch):
    private = tuple(
        Observation(
            f"browser-{index}-old", "browser", f"browser:{index}",
            99_990.25 + index / 100, f"epoch:{index}", 1, {"kind": "api"},
        )
        for index in range(materializer.MAX_PRIVATE_BROWSER_CLIENTS)
    )
    first = _build(
        _snapshot(observations=private),
        source=1,
        cache=1,
        until=100_000.0,
    )
    new_cpu = _cpu(100_000.25, 8)
    new_browser = Observation(
        "browser-0-new", "browser", "browser:0", 100_000.25,
        "epoch:0", 1, {"kind": "sse"},
    )
    snapshot = _snapshot(observations=(*private, new_cpu, new_browser))
    dirty = frozenset(
        DirtyCell(
            resolution,
            math.floor(new_cpu.observed_at / resolution) * resolution,
        )
        for resolution in RESOLUTIONS
    )
    folded = []
    real_fold = materializer._fold_bucket

    def counted_fold(*args, **kwargs):
        folded.append((args[0], args[1]))
        return real_fold(*args, **kwargs)

    monkeypatch.setattr(materializer, "_fold_bucket", counted_fold)
    updated = update_generation(
        first,
        snapshot,
        dirty,
        source_generation=2,
        cache_generation=2,
        generated_at=100_001.0,
        observed_until=100_001.0,
    )

    layer_sets = 1 + materializer.MAX_PRIVATE_BROWSER_CLIENTS
    expected_fold_count = layer_sets * (len(RESOLUTIONS) + 1)
    total_bucket_count = sum(len(layer.buckets) for layer in updated.layers)
    total_bucket_count += sum(
        len(layer.buckets)
        for overlay in updated.private_overlays
        for layer in overlay.layers
    )
    assert len(folded) == expected_fold_count == 25
    assert len(folded) * 10 < total_bucket_count


def test_bucket_provenance_counts_original_facts_not_projected_series():
    atom = UsageAtom("output", "output", "text", "none", "tokens", 12, {
        "quantity": 10, "provider": "openai", "agent_id": "sol", "model": "gpt",
        "telemetry_complete": True,
    })
    bucket = next(
        item for item in _build(_snapshot(observations=(_cpu(11, 2),), usage=(atom,))).layer(10).buckets
        if item.start == 10
    )

    assert bucket.source_count == 2
    assert len(bucket.series) > bucket.source_count
    assert (bucket.first_observed_at, bucket.last_observed_at) == (11, 12)


def test_measured_zero_is_a_value_while_missing_covered_slot_is_no_data():
    snapshot = _snapshot(
        observations=(_cpu(0, 0), _cpu(20, 5)),
        coverage=(
            CoverageEpoch("cpu", "host", "epoch-1", 0, 10, 10, 1),
            CoverageEpoch("cpu", "host", "epoch-2", 20, 30, 10, 1),
        ),
    )
    layer = _build(snapshot, until=30).layer(10)
    buckets = {bucket.start: bucket for bucket in layer.buckets}
    assert _series(buckets[0])["cpu_percent:host"].value == 0
    assert buckets[10].series == ()
    assert layer.no_data == (
        layer.no_data[0].__class__("cpu", "host", "epoch-1", 10, 20, 10),
    )


def test_superseded_dynamic_source_does_not_poison_current_family_coverage():
    snapshot = _snapshot(
        observations=(_cpu(0, 1, source="retired:web"), _cpu(20, 2, source="port:8881")),
        coverage=(
            CoverageEpoch("cpu", "retired:web", "retired", 0, 10, 1, 1),
            CoverageEpoch("cpu", "port:8881", "current", 20, None, 1, 2),
        ),
    )

    gaps = _build(snapshot, until=30).layer(10).no_data

    assert gaps == ()


def test_coverage_only_incremental_refreshes_no_data_without_rebuilding_buckets():
    first = _build(_snapshot(coverage=(
        CoverageEpoch("cpu", "host", "epoch", 0, 10, 10, 1),
    )), until=30)
    snapshot = _snapshot(coverage=(
        CoverageEpoch("cpu", "host", "epoch", 0, 20, 10, 2),
    ))

    incremental = update_generation(
        first,
        snapshot,
        (),
        source_generation=2,
        cache_generation=2,
        generated_at=30,
        observed_until=30,
    )
    full = build_generation(
        snapshot,
        source_generation=2,
        cache_generation=2,
        generated_at=30,
        observed_until=30,
    )

    assert incremental == full
    assert all(
        updated is original
        for before, after in zip(first.layers, incremental.layers, strict=True)
        for original, updated in zip(before.buckets, after.buckets, strict=True)
    )


def test_successful_empty_usage_scan_is_covered_and_real_scan_gap_serves_tokens_and_cost():
    snapshot = _snapshot(coverage=(
        CoverageEpoch("agent_tokens", "scan", "scan-1", 0, 10, 10, 1),
        CoverageEpoch("agent_tokens", "scan", "scan-2", 20, None, 10, 1),
    ))
    gaps = _build(snapshot, until=30).layer(10).no_data
    assert {(gap.family, gap.start, gap.end) for gap in gaps} == {
        ("agent_tokens", 10, 20),
        ("cost", 10, 20),
    }


def test_explicit_unrecoverable_span_is_served_without_fabricating_coverage():
    snapshot = _snapshot(
        coverage=(
            CoverageEpoch("agent_status", "legacy", "before", 0, 10, 10, 1),
            CoverageEpoch("agent_status", "legacy", "after", 20, 30, 10, 1),
        ),
        unavailable=(UnavailableSpan(
            "agent_status", "legacy", "migration-1", 10, 20, 10,
            "legacy_aggregate_not_reconstructable", 1,
        ),),
    )

    gaps = _build(snapshot, until=30).layer(10).no_data

    assert gaps == (
        gaps[0].__class__(
            "agent_status", "legacy", "migration-1", 10, 20, 10,
            "legacy_aggregate_not_reconstructable",
        ),
    )


def test_early_schema5_overlapping_unavailable_spans_are_sliced_once():
    snapshot = _snapshot(unavailable=(
        UnavailableSpan("agent_status", "legacy", "first", 10, 20, 10, "first loss", 1),
        UnavailableSpan("agent_status", "legacy", "second", 15, 25, 10, "second loss", 1),
    ))

    gaps = _build(snapshot, until=30).layer(10).no_data

    assert [(gap.start, gap.end, gap.reason) for gap in gaps] == [
        (10, 20, "first loss"),
        (20, 25, "second loss"),
    ]


def test_dynamic_process_device_and_service_series_keep_source_identity():
    observations = (
        _cpu(11, 10, source="web"),
        _cpu(11, 20, source="statsd"),
        Observation("gpu-0", "gpu", "gpu:0", 11, "epoch", 1, {
            "util_percent": 10, "memory_used_bytes": 100,
            "memory_capacity_bytes": 1000, "label": "GPU 0",
        }),
        Observation("gpu-1", "gpu", "gpu:1", 11, "epoch", 1, {
            "util_percent": 20, "memory_used_bytes": 200,
            "memory_capacity_bytes": 1000, "label": "GPU 1",
        }),
        Observation("service-web", "service_load", "web", 11, "epoch", 1, {
            "running": True, "cpu_percent": 4, "rss_bytes": 400,
        }),
    )
    bucket = next(
        item for item in _build(_snapshot(observations=observations)).layer(10).buckets
        if item.start == 10
    )
    assert {
        "cpu_percent:web", "cpu_percent:statsd",
        "gpu_util_percent:gpu:0", "gpu_util_percent:gpu:1",
        "service_cpu_percent:web",
    } <= set(_series(bucket))


def test_usage_identity_is_deduplicated_before_token_and_cost_projection():
    atom = UsageAtom("same", "input", "text", "none", "tokens", 12, {
        "quantity": 10, "provider": "openai", "agent_id": "sol", "model": "gpt",
        "telemetry_complete": True,
    })
    bucket = next(
        item for item in _build(
            _snapshot(usage=(atom, atom)), price_resolver=lambda _atom: _projection(2),
        ).layer(10).buckets
        if item.start == 10
    )
    values = _series(bucket)
    assert values["usage_tokens"].value == 10
    assert values["cost_micro_usd"].value == 2


def test_cost_projection_rejects_inexact_float_micro_usd():
    atom = UsageAtom("cost", "output", "text", "none", "tokens", 12, {
        "quantity": 10, "provider": "openai", "agent_id": "sol", "model": "gpt",
        "telemetry_complete": True,
    })

    with pytest.raises(MaterializationError, match="integer micro-USD"):
        _build(_snapshot(usage=(atom,)), price_resolver=lambda _atom: 1.5)


def test_cost_detail_series_are_exact_attributed_bounded_and_privacy_safe():
    def atom(
        event_id, direction, cache_role, quantity, model, agent_id,
        *, modality="text", unit="tokens", source="codex",
    ):
        return UsageAtom(event_id, direction, modality, cache_role, unit, 12, {
            "quantity": quantity,
            "provider": "openai",
            "model": model,
            "agent_id": agent_id,
            "execution_source": source,
            "telemetry_complete": True,
        })

    atoms = (
        atom("input", "input", "none", 100, "model-a", "agent-a"),
        atom("read", "input", "read", 40, "model-a", "agent-a"),
        atom("write", "input", "write_5m", 30, "model-a", "agent-a"),
        atom("output", "output", "none", 20, "model-b", "agent-b", source="claude"),
        atom("image", "output", "none", 5, "image-model", "agent-b", modality="image"),
        atom("request", "output", "none", 1, "image-model", "agent-b", modality="image", unit="requests"),
        atom(
            "unknown", "output", "none", 7, "unknown-model", "agent-c",
            source="private/user/path",
        ),
    )

    def resolve(item):
        quantity = int(item.payload["quantity"])
        return _projection(None if item.payload["model"] == "unknown-model" else quantity * 10)

    generation = _build(_snapshot(usage=atoms), price_resolver=resolve)
    bucket = next(item for item in generation.layer(10).buckets if item.start == 10)
    values = _series(bucket)
    detail = bucket.cost_detail
    dimensions = {item.dimension: item for item in detail.dimensions}

    assert materializer.TOKEN_DETAIL_DIMENSIONS == (
        "input", "cache_read", "cache_write", "output", "reasoning", "other",
    )
    assert values["usage_tokens"].value == 202
    assert values["cost_micro_usd"].value == 1_960
    assert dimensions["input"].tokens == 100
    assert dimensions["cache_read"].tokens == 40
    assert dimensions["cache_write"].tokens == 30
    assert dimensions["output"].tokens == 27
    assert dimensions["other"].tokens == 5
    assert detail.priced == materializer.CostCoverage(atoms=6, tokens=195)
    assert detail.unpriced == materializer.CostCoverage(atoms=1, tokens=7)
    assert all(not name.startswith("cost_detail:") for name in values)

    model_a = materializer.cost_detail_model_key("openai", "model-a")
    model_values = {item.key: item for item in detail.models}
    model_a_dimensions = {
        item.dimension: item for item in model_values[model_a].dimensions
    }
    assert model_a_dimensions["input"].tokens == 100
    assert model_a_dimensions["cache_read"].tokens == 40
    assert model_a_dimensions["cache_write"].tokens == 30
    assert model_a_dimensions["input"].micro_usd == 1_000
    unknown_model = materializer.cost_detail_model_key("openai", "unknown-model")
    assert model_values[unknown_model].unpriced == materializer.CostCoverage(
        atoms=1, tokens=7,
    )
    unknown_dimensions = {
        item.dimension: item for item in model_values[unknown_model].dimensions
    }
    assert unknown_dimensions["output"].micro_usd == 0

    agent_a = materializer.cost_detail_agent_key("agent-a")
    agent_values = {item.key: item for item in detail.agents}
    agent_a_dimensions = {
        item.dimension: item for item in agent_values[agent_a].dimensions
    }
    assert agent_a_dimensions["input"].tokens == 100
    assert "agent-a" not in repr(detail)
    assert "agent-b" not in repr(detail)
    assert all(len(name.encode()) <= 256 for name in values)
    agent_c = materializer.cost_detail_agent_key("agent-c")
    assert model_values[model_a].provider == "openai"
    assert model_values[model_a].model == "model-a"
    assert agent_values[agent_a].source == "codex"
    assert agent_values[agent_a].label == f"codex:{agent_a[:8]}"
    assert agent_values[agent_c].source.startswith("sha256-")
    assert detail.evidence
    assert all(
        item.source_url == "https://developers.openai.com/api/docs/pricing"
        for item in detail.evidence
    )

    report = materializer.build_cost_report(
        materializer.slice_generation(generation, 300, 10),
    )
    assert report["total_tokens"] == 202
    assert report["total_micro_usd"] == 1_960
    assert report["total_api_list_micro_usd"] == 1_960
    assert report["dimensions"] == {
        "input": {"tokens": 100, "micro_usd": 1_000, "api_list_micro_usd": 1_000},
        "cache_read": {"tokens": 40, "micro_usd": 400, "api_list_micro_usd": 400},
        "cache_write": {"tokens": 30, "micro_usd": 300, "api_list_micro_usd": 300},
        "output": {"tokens": 27, "micro_usd": 200, "api_list_micro_usd": 200},
        "other": {"tokens": 5, "micro_usd": 60, "api_list_micro_usd": 60},
    }
    assert report["priced"] == {"atoms": 6, "tokens": 195}
    assert report["unpriced"] == {"atoms": 1, "tokens": 7}
    assert report["reasoning_available"] is False
    assert "reasoning" not in report["dimensions"]
    assert report["catalog_revision"] == 3
    assert report["omissions"] == {"models": 0, "agents": 0, "evidence": 0}
    model_rows = {row["model"]: row for row in report["models"]}
    assert model_rows["model-a"]["total_tokens"] == 170
    assert model_rows["unknown-model"]["unpriced"] == {"atoms": 1, "tokens": 7}
    assert model_rows["unknown-model"]["total_micro_usd"] == 0
    assert model_rows["unknown-model"]["total_api_list_micro_usd"] == 0
    assert {row["source"] for row in report["agents"]} == {
        "codex", "mixed", "sha256-7555f019daf1e0ad1350e992",
    }
    assert all("agent-" not in row["label"] for row in report["agents"])
    assert report["evidence"]
    assert all(
        row["source_url"] == "https://developers.openai.com/api/docs/pricing"
        for row in report["evidence"]
    )


def test_cost_detail_model_agent_and_evidence_cardinality_has_named_bounds():
    atoms = tuple(
        UsageAtom(f"event-{index}", "output", "text", "none", "tokens", 12, {
            "quantity": index + 1,
            "provider": "provider",
            "model": f"model-{index}",
            "agent_id": f"private-agent-{index}",
            "execution_source": "codex",
            "telemetry_complete": True,
        })
        for index in range(materializer.MAX_COST_DETAIL_MODELS + 1)
    )
    generation = _build(
        _snapshot(usage=atoms), price_resolver=lambda _atom: _projection(None),
    )
    bucket = next(item for item in generation.layer(10).buckets if item.start == 10)
    values = _series(bucket)
    detail = bucket.cost_detail
    model_keys = {item.key for item in detail.models}
    agent_keys = {item.key for item in detail.agents}

    assert len(model_keys) == materializer.MAX_COST_DETAIL_MODELS
    assert len(agent_keys) == materializer.MAX_COST_DETAIL_AGENTS
    assert detail.omitted_models == 1
    assert detail.omitted_agents == 1
    assert "private-agent" not in repr(detail)
    assert all(not name.startswith("cost_detail:") for name in values)
    report = materializer.build_cost_report(
        materializer.slice_generation(generation, 300, 10),
    )
    assert len(report["models"]) == materializer.MAX_COST_DETAIL_MODELS
    assert len(report["agents"]) == materializer.MAX_COST_DETAIL_AGENTS
    assert report["omissions"] == {"models": 1, "agents": 1, "evidence": 0}


def test_cost_detail_evidence_cardinality_is_bounded_independently():
    atoms = tuple(
        UsageAtom(f"event-{index}", "output", "text", "none", "tokens", 12, {
            "quantity": 1,
            "provider": "provider",
            "model": "one-model",
            "agent_id": "one-agent",
            "telemetry_complete": True,
        })
        for index in range(materializer.MAX_COST_DETAIL_EVIDENCE + 1)
    )

    def resolve(atom):
        index = atom.event_id.rsplit("-", 1)[1]
        return current_pricing.UsagePriceProjection(
            1,
            1,
            current_pricing.PricingEvidence(
                "catalog-model", f"{index}.00", 1_000_000,
                "2026-07-09T00:00:00Z", "seed",
                "https://developers.openai.com/api/docs/pricing", 3,
            ),
        )

    generation = _build(_snapshot(usage=atoms), price_resolver=resolve)
    bucket = next(item for item in generation.layer(10).buckets if item.start == 10)
    evidence_keys = {item.key for item in bucket.cost_detail.evidence}

    assert len(evidence_keys) == materializer.MAX_COST_DETAIL_EVIDENCE
    assert bucket.cost_detail.omitted_evidence == 1
    report = materializer.build_cost_report(
        materializer.slice_generation(generation, 300, 10),
    )
    assert len(report["evidence"]) == materializer.MAX_COST_DETAIL_EVIDENCE
    assert report["omissions"]["evidence"] == 1


def test_typed_cost_detail_preserves_unicode_colons_without_encoded_series_metadata():
    atom = UsageAtom("unicode", "output", "text", "none", "tokens", 12, {
        "quantity": 9,
        "provider": "提供者:alpha",
        "model": "模型:beta",
        "agent_id": "agent:用户",
        "execution_source": "codex",
        "telemetry_complete": True,
    })
    generation = _build(
        _snapshot(usage=(atom,)), price_resolver=lambda _atom: _projection(7),
    )
    bucket = next(item for item in generation.layer(10).buckets if item.start == 10)
    report = materializer.build_cost_report(
        materializer.slice_generation(generation, 300, 10),
    )

    assert bucket.cost_detail.models[0].provider == "提供者:alpha"
    assert bucket.cost_detail.models[0].model == "模型:beta"
    assert report["models"][0]["provider"] == "提供者:alpha"
    assert report["models"][0]["model"] == "模型:beta"
    with pytest.raises(FrozenInstanceError):
        bucket.cost_detail.priced.tokens = 0


def test_cost_agent_labels_preserve_public_tmux_identity_and_bound_background_agents():
    first = materializer._privacy_safe_agent_label(
        "claude-bg:-Users-keivenc-projects-yolomux.dev8881:123456789abc:deadbeef",
        "claude",
    )
    second = materializer._privacy_safe_agent_label(
        "claude-bg:-Users-keivenc-projects-yolomux.dev8881:abcdef012345:feedface",
        "claude",
    )

    assert materializer._privacy_safe_agent_label("yo8881|2|codex", "codex") == (
        "yo8881|2|codex"
    )
    assert first.startswith("claude-bg:")
    assert first != second
    assert len(first.encode()) <= 64
    assert "123456789abc" not in first
    private = materializer._privacy_safe_agent_label("/Users/private/transcript", "codex")
    assert private.startswith("codex:")
    assert "/Users/private" not in private


def test_materializer_source_has_no_synthetic_cost_series_or_metadata_codec():
    source = inspect.getsource(materializer)

    assert "COST_DETAIL_PREFIX" not in source
    assert "cost_detail:v1" not in source
    assert "base64" not in source
    assert "_decode_metadata_value" not in source
    assert "_metadata_name" not in source


def test_model_dimensions_are_mutually_exclusive_and_output_exactly_partitions_agents():
    atoms = (
        UsageAtom("output-sol", "output", "text", "none", "tokens", 12, {
            "quantity": 10, "provider": "openai", "agent_id": "sol", "model": "gpt",
            "telemetry_complete": True,
        }),
        UsageAtom("output-terra", "output", "text", "none", "tokens", 12, {
            "quantity": 20, "provider": "openai", "agent_id": "terra", "model": "gpt",
            "telemetry_complete": True,
        }),
        UsageAtom("input", "input", "text", "none", "tokens", 12, {
            "quantity": 30, "provider": "openai", "agent_id": "sol", "model": "gpt",
            "telemetry_complete": True,
        }),
        UsageAtom("read", "input", "text", "read", "tokens", 12, {
            "quantity": 40, "provider": "openai", "agent_id": "sol", "model": "gpt",
            "telemetry_complete": True,
        }),
        UsageAtom("write", "input", "text", "write_5m", "tokens", 12, {
            "quantity": 50, "provider": "openai", "agent_id": "sol", "model": "gpt",
            "telemetry_complete": True,
        }),
    )
    bucket = next(
        item for item in _build(_snapshot(usage=atoms)).layer(10).buckets
        if item.start == 10
    )
    values = _series(bucket)

    agent_output = sum(
        item.value for name, item in values.items()
        if name.startswith("agent_tokens_per_minute:")
    )
    model_output = sum(
        item.value for name, item in values.items()
        if name.startswith("model_tokens_per_minute:output:")
    )
    assert agent_output == model_output == 180
    assert values["model_tokens_per_minute:input:gpt"].value == 180
    assert values["model_tokens_per_minute:cache_read:gpt"].value == 240
    assert values["model_tokens_per_minute:cache_write:gpt"].value == 300
    assert values["model_tokens_per_minute:all:gpt"].value == 900
    assert values["usage_tokens"].value == 150


def test_noncanonical_stored_usage_fails_instead_of_creating_a_parallel_projection():
    atom = UsageAtom("old", "input", "text", "cached", "tokens", 12, {
        "tokens": 10,
    })

    with pytest.raises(MaterializationError, match="stored usage atom violates"):
        _build(_snapshot(usage=(atom,)))


def test_incremental_and_full_build_use_the_same_bucket_result():
    first = _build(_snapshot(observations=(_cpu(11, 2),)), source=1, cache=1)
    snapshot = _snapshot(observations=(_cpu(11, 2), _cpu(12, 4)))
    dirty = tuple(
        DirtyCell(resolution, int(12 // resolution * resolution))
        for resolution in RESOLUTIONS
    )
    incremental = update_generation(
        first, snapshot, dirty, source_generation=2, cache_generation=2,
        generated_at=20, observed_until=20,
    )
    full = build_generation(
        snapshot, source_generation=2, cache_generation=2,
        generated_at=20, observed_until=20,
    )
    assert incremental == full
    assert incremental.layer(1).buckets[-3] is first.layer(1).buckets[-3]


def test_incremental_update_preserves_clean_open_bucket_when_it_becomes_complete():
    atom = UsageAtom("cost", "output", "text", "none", "tokens", 11, {
        "quantity": 10, "provider": "openai", "agent_id": "sol", "model": "gpt",
        "telemetry_complete": True,
    })
    first = _build(
        _snapshot(observations=(_cpu(11, 2),), usage=(atom,)),
        source=1,
        cache=1,
        until=12,
        price_resolver=lambda _atom: _projection(3),
    )
    before = next(bucket for bucket in first.layer(10).buckets if bucket.start == 10)
    assert before.complete is False
    updated = update_generation(
        first, _snapshot(), (), source_generation=1, cache_generation=2,
        generated_at=20, observed_until=20,
    )
    after = next(bucket for bucket in updated.layer(10).buckets if bucket.start == 10)
    assert after.complete is True
    assert after is not before
    assert after.series == before.series
    assert after.source_count == before.source_count
    assert after.cost_detail == before.cost_detail


def test_every_exact_matrix_slice_has_the_requested_data_resolution_and_bound():
    generation = _build(_snapshot(), until=90_000)
    for range_seconds in RANGES:
        allowed = stats_resolution.explicit_resolutions(range_seconds)
        assert allowed
        assert resolve_resolution(range_seconds, "AUTO") == allowed[0]
        for requested in ("AUTO", *allowed):
            result = slice_generation(generation, range_seconds, requested)
            assert result.resolution == resolve_resolution(range_seconds, requested)
            assert len(result.buckets) == range_seconds // result.resolution <= 600
            assert {bucket.duration for bucket in result.buckets} == {result.resolution}
    for range_seconds, resolution in ((900, 1), (7200, 10), (14400, 120), (57600, 1), (86400, 600)):
        with pytest.raises(UnsupportedSliceError):
            slice_generation(generation, range_seconds, resolution)


def test_stale_incremental_build_and_publish_are_rejected():
    current = _build(_snapshot(), source=2, cache=2)
    stale = _build(_snapshot(), source=1, cache=1)
    with pytest.raises(StaleGenerationError):
        accept_generation(current, stale)
    with pytest.raises(StaleGenerationError):
        update_generation(
            current, _snapshot(), (), source_generation=2, cache_generation=2,
            generated_at=21, observed_until=21,
        )
    fresh = _build(_snapshot(), source=2, cache=3)
    assert accept_generation(current, fresh) is fresh


def test_randomized_incremental_schedule_matches_full_build_and_deltas_apply_exactly():
    """Property/differential battery: a seeded random append schedule (every
    family kind, browser private clients, late/out-of-order events, epoch
    bumps, an unavailable span, exact zeroes, boundary advance) is applied as
    incremental updates and compared against the deterministic full builder
    after EVERY batch — generations must be equal, every allowed
    Range/Resolution slice (including AUTO) must match, and the wire delta
    applied to the previous snapshot must reproduce the new snapshot exactly."""
    rng = random.Random(20260716)
    base = 120_000
    families = ["cpu", "system_memory", "agent_status", "browser"]
    private_clients = ["a" * 64, "b" * 64]
    observations = []
    unavailable = [UnavailableSpan("cpu", "host", "epoch", base - 50, base - 40, 1.0, "collector_gap", 1)]

    def random_observation(index, at):
        family = rng.choice(families)
        if family == "browser":
            client = rng.choice(private_clients)
            return Observation(f"event-{index}", "browser", client, at, f"epoch:{client}", 1,
                               {"kind": "api", "latency_ms": rng.randrange(0, 30)})
        if family == "agent_status":
            return Observation(f"event-{index}", "agent_status", "host", at, "epoch", 1,
                               {"states": {"a": rng.choice(["ask", "run", "idle"])}})
        if family == "system_memory":
            return Observation(f"event-{index}", "system_memory", "host", at, "epoch", 1,
                               {"used_bytes": rng.choice([0, rng.randrange(0, 1 << 32)]), "capacity_bytes": 1 << 33})  # exact zero is a value
        return Observation(f"event-{index}", "cpu", "host", at, "epoch", 1,
                           {"process_percent": rng.choice([0, rng.randrange(0, 100)]),
                            "system_percent": rng.randrange(0, 200)})

    current = None
    index = 0
    now = float(base)
    for batch_number in range(12):
        now += rng.choice([0.5, 1.0, 1.0, 7.0, 61.0])  # includes boundary advances and gaps
        batch = []
        for _ in range(rng.randrange(1, 9)):
            late = rng.random() < 0.25
            at = now - rng.uniform(0.0, 240.0 if late else 0.9)  # late/out-of-order events
            batch.append(random_observation(index, round(at, 3)))
            index += 1
        observations.extend(batch)
        snapshot = _snapshot(observations=tuple(observations), unavailable=tuple(unavailable))
        dirty = frozenset(
            DirtyCell(resolution, math.floor(item.observed_at / resolution) * resolution)
            for item in batch
            for resolution in RESOLUTIONS
        )
        full = build_generation(
            snapshot, source_generation=batch_number + 1, cache_generation=batch_number + 1,
            generated_at=now, observed_until=now,
        )
        if current is None:
            current = full
            continue
        previous = current
        current = update_generation(
            previous, snapshot, dirty,
            source_generation=batch_number + 1, cache_generation=batch_number + 1,
            generated_at=now, observed_until=now,
        )
        assert current == full  # exact generation equality, every family and layer

        for range_seconds in stats_resolution.RANGE_SECONDS:
            for requested in (*stats_resolution.explicit_resolutions(range_seconds), stats_resolution.AUTO):
                concrete = resolve_resolution(range_seconds, requested)
                for private_source_id in (None, *current.private_source_ids):
                    incremental_slice = slice_generation(current, range_seconds, concrete, private_source_id=private_source_id)
                    full_slice = slice_generation(full, range_seconds, concrete, private_source_id=private_source_id)
                    assert incremental_slice == full_slice

        # Delta exactness: previous snapshot + delta == new snapshot, per view.
        for range_seconds, concrete in ((300, 1), (86400, 300)):
            report = materializer.build_cost_report(slice_generation(current, range_seconds, concrete))
            delta = service_module._wire_delta(previous, current, range_seconds, concrete, 1, report)
            old_wire = service_module._wire_snapshot(
                previous, slice_generation(previous, range_seconds, concrete), range_seconds, concrete,
                materializer.build_cost_report(slice_generation(previous, range_seconds, concrete)),
            )
            new_wire = service_module._wire_snapshot(
                current, slice_generation(current, range_seconds, concrete), range_seconds, concrete, report,
            )
            merged = {(item["start"], item["duration"]): item for item in old_wire["buckets"]}
            for tombstone in delta.get("tombstones", ()):
                if tombstone["kind"] == "bucket":
                    merged.pop((tombstone["start"], tombstone["duration"]), None)
            for item in delta["buckets"]:
                merged[(item["start"], item["duration"])] = item
            expected = {(item["start"], item["duration"]): item for item in new_wire["buckets"]}
            assert json.dumps(sorted(merged.items()), sort_keys=True) == json.dumps(sorted(expected.items()), sort_keys=True)
