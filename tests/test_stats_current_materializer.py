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


def test_system_memory_projection_keeps_mac_details_as_exact_server_series():
    observation = Observation("memory-1", "system_memory", "host", 10, "epoch", 1, {
        "used_bytes": 900,
        "capacity_bytes": 1000,
        "mac_physical_memory_bytes": 1000,
        "mac_memory_used_bytes": 600,
        "mac_cached_files_bytes": 400,
        "mac_swap_used_bytes": 25,
        "mac_app_memory_bytes": 300,
        "mac_wired_memory_bytes": 100,
        "mac_compressed_memory_bytes": 200,
        "mac_pressure_percent": 20,
        "mac_pressure_level": 2,
    })

    samples = {sample.series: sample.value for sample in materializer._observation_samples(observation)}

    assert samples["mac_pressure_percent"] == 20
    assert samples["mac_pressure_level"] == 2
    assert samples["mac_compressed_memory_bytes"] == 200
    assert samples["system_memory_used_bytes"] == 900


def test_system_memory_projection_emits_one_dynamic_series_per_binary():
    observation = Observation("memory-processes-1", "system_memory", "host", 10, "epoch", 1, {
        "used_bytes": 900,
        "capacity_bytes": 1000,
        "process_memory_bytes": {"python": 300, "node": 200},
    })

    samples = {sample.series: sample.value for sample in materializer._observation_samples(observation)}

    assert samples["process_memory_bytes:python"] == 300
    assert samples["process_memory_bytes:node"] == 200


def test_system_memory_projection_keeps_the_retained_eight_binary_wire_shape_readable():
    processes = {f"process-{index}": 100 - index for index in range(8)}
    observation = Observation("memory-processes-legacy", "system_memory", "host", 10, "epoch", 1, {
        "used_bytes": 900,
        "capacity_bytes": 1000,
        "process_memory_bytes": processes,
    })

    samples = {sample.series: sample.value for sample in materializer._observation_samples(observation)}

    assert {name for name in samples if name.startswith("process_memory_bytes:")} == {
        f"process_memory_bytes:process-{index}" for index in range(8)
    }


def test_cpu_projection_emits_average_min_and_max_series_per_binary():
    observation = Observation("cpu-processes-1", "cpu", "host", 10, "epoch", 1, {
        "process_percent": 5,
        "system_percent": 20,
        "process_cpu_percent": {"python": 4, "node": 3},
    })

    samples = {sample.series: sample.value for sample in materializer._observation_samples(observation)}

    assert samples["process_cpu_percent:python"] == 4
    assert samples["process_cpu_min_percent:python"] == 4
    assert samples["process_cpu_max_percent:python"] == 4
    assert samples["process_cpu_percent:node"] == 3


def test_process_memory_gauge_keeps_the_latest_sample_in_a_coarse_bucket():
    observations = (
        Observation("memory-processes-1", "system_memory", "host", 11, "epoch", 60, {
            "used_bytes": 900, "capacity_bytes": 1000,
            "process_memory_bytes": {"python": 300, "node": 200},
        }),
        Observation("memory-processes-2", "system_memory", "host", 12, "epoch", 60, {
            "used_bytes": 910, "capacity_bytes": 1000,
            "process_memory_bytes": {"python": 400},
        }),
    )
    generation = _build(_snapshot(observations=observations), until=20)
    bucket = next(
        item for item in materializer.slice_generation(
            generation, 300, 10, private_source_id="client",
        ).buckets
        if item.start == 10
    )

    values = _series(bucket)
    assert values["process_memory_bytes:python"].value == 400
    assert values["process_memory_bytes:node"].value == 200


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
            "states": {"a": "ask", "b": "run", "c": "run", "d": "idle"}, "session_states": {"one": "ask", "two": "run"}, "snapshot_revision": 17,
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
    assert values["cpu_min_percent:host"].value == 2
    assert values["cpu_max_percent:host"].value == 4
    assert values["system_cpu_min_percent"].value == 4
    assert values["system_cpu_max_percent"].value == 8
    assert values["agent_window_snapshot_revision"].value == 17
    assert values["gpu_util_percent:host"].value == 40
    assert values["browser_api_per_second"].value == 0.1
    assert values["browser_latency_ms"].value == 15
    assert values["run_agents"].value == 2
    assert values["ask_sessions"].value == 1
    assert values["run_sessions"].value == 1
    assert values["agent_tokens_per_minute:sol"].value == 600
    assert values["model_tokens_per_minute:output:gpt"].value == 600
    assert values["model_tokens_per_minute:all:gpt"].value == 600
    assert values["usage_tokens"].value == 100
    assert values["cost_micro_usd"].value == 25
    assert bucket.source_count == len(observations) + 1
    assert (bucket.first_observed_at, bucket.last_observed_at) == (11, 13)


def test_browser_observations_are_shared_as_fair_all_client_averages():
    observations = (
        Observation("a-api-1", "browser", "browser:a", 11, "epoch:a", 1, {
            "kind": "api", "latency_ms": 10, "bytes": 100,
        }),
        Observation("a-api-2", "browser", "browser:a", 12, "epoch:a", 2, {
            "kind": "api", "latency_ms": 20, "bytes": 300,
        }),
        Observation("a-disconnect", "browser", "browser:a", 13, "epoch:a", 1, {
            "kind": "disconnect", "duration_ms": 40,
        }),
        Observation("b-api", "browser", "browser:b", 12, "epoch:b", 1, {
            "kind": "api", "latency_ms": 30, "bytes": 200,
        }),
        Observation("b-disconnect", "browser", "browser:b", 13, "epoch:b", 2, {
            "kind": "disconnect", "duration_ms": 20,
        }),
    )
    generation = _build(_snapshot(observations=observations))

    shared = next(bucket for bucket in generation.layer(10).buckets if bucket.start == 10)
    values = {item.name: item for item in shared.series}

    assert values["browser_api_per_second"].value == pytest.approx(0.15)
    assert values["browser_api_per_second"].source_count == 2
    assert values["browser_latency_ms"].value == 22.5
    assert values["browser_latency_ms"].source_count == 2
    assert values["browser_bandwidth_bytes_per_second"].value == 30
    assert values["browser_bandwidth_bytes_per_second"].source_count == 2
    assert values["browser_disconnected_ms"].value == 30
    assert values["browser_disconnected_ms"].source_count == 2
    assert generation.private_source_ids == ()
    assert materializer.slice_generation(
        generation, 300, 10, private_source_id="browser:unknown",
    ) == materializer.slice_generation(generation, 300, 10)


def test_browser_failure_without_a_metric_does_not_publish_orphan_source_facts():
    observations = (
        Observation("failure", "browser", "browser:a", 11, "epoch:a", 1, {
            "kind": "error", "signature": "jsf-deadbeef", "message": "boom",
            "source": "/static/yolomux.js", "delivery_outcome": "failed",
        }),
        Observation("api", "browser", "browser:a", 12, "epoch:a", 2, {
            "kind": "api", "latency_ms": 10,
        }),
    )

    generation = _build(_snapshot(observations=observations))
    failure_bucket = next(bucket for bucket in generation.layer(1).buckets if bucket.start == 11)
    api_bucket = next(bucket for bucket in generation.layer(1).buckets if bucket.start == 12)

    assert failure_bucket.series == ()
    assert (failure_bucket.source_count, failure_bucket.first_observed_at, failure_bucket.last_observed_at) == (0, None, None)
    assert api_bucket.series
    assert (api_bucket.source_count, api_bucket.first_observed_at, api_bucket.last_observed_at) == (1, 12, 12)


def test_browser_failure_does_not_widen_same_bucket_metric_source_facts():
    observations = (
        Observation("failure", "browser", "browser:a", 11, "epoch:a", 1, {
            "kind": "error", "signature": "jsf-deadbeef", "message": "boom",
            "source": "/static/yolomux.js", "delivery_outcome": "failed",
        }),
        Observation("api", "browser", "browser:a", 12, "epoch:a", 2, {
            "kind": "api", "latency_ms": 10,
        }),
    )

    generation = _build(_snapshot(observations=observations))
    mixed_bucket = next(bucket for bucket in generation.layer(10).buckets if bucket.start == 10)

    assert mixed_bucket.series
    assert (mixed_bucket.source_count, mixed_bucket.first_observed_at, mixed_bucket.last_observed_at) == (1, 12, 12)


def test_browser_perceptual_queue_and_instrumentation_signals_have_first_class_series():
    observations = (
        Observation("api", "browser", "browser:a", 11, "epoch:a", 1, {
            "kind": "api", "latency_ms": 3000, "queue_ms": 2800,
        }),
        Observation("load", "browser", "browser:a", 12, "epoch:a", 1, {
            "kind": "page_load", "endpoint": "/", "first_paint_ms": 20,
            "first_contentful_paint_ms": 25, "app_ready_ms": 200,
            "max_concurrency": 6,
        }),
        Observation("finder", "browser", "browser:a", 13, "epoch:a", 1, {
            "kind": "finder_usable", "latency_ms": 120, "entry_count": 4,
        }),
        Observation("input", "browser", "browser:a", 14, "epoch:a", 1, {
            "kind": "interaction", "latency_ms": 180, "input_delay_ms": 70,
            "processing_ms": 50, "presentation_delay_ms": 60,
            "interaction_type": "click",
        }),
        Observation("operation", "browser", "browser:a", 15, "epoch:a", 1, {
            "kind": "operation_wait", "latency_ms": 3200,
            "operation_kind": "session_files", "outcome": "ready",
        }),
        Observation("task", "browser", "browser:a", 16, "epoch:a", 1, {
            "kind": "long_task", "latency_ms": 88.5,
        }),
        Observation("health", "browser", "browser:a", 17, "epoch:a", 1, {
            "kind": "heartbeat", "upload_queue_depth": 17, "upload_drops": 2,
            "upload_retries": 3, "instrumentation_cost_ms": 0.42,
        }),
    )
    generation = _build(_snapshot(observations=observations))
    bucket = next(item for item in generation.layer(10).buckets if item.start == 10)
    values = {item.name: item.value for item in bucket.series}

    assert values["browser_queue_ms"] == 2800
    assert values["browser_first_paint_ms"] == 20
    assert values["browser_first_contentful_paint_ms"] == 25
    assert values["browser_app_ready_ms"] == 200
    assert values["browser_page_max_concurrency"] == 6
    assert values["browser_finder_usable_ms"] == 120
    assert values["browser_input_latency_ms"] == 180
    assert values["browser_operation_wait_ms"] == 3200
    assert values["browser_long_task_ms"] == 88.5
    assert values["browser_upload_queue_depth"] == 17
    assert values["browser_upload_drops"] == 2
    assert values["browser_upload_retries"] == 3
    assert values["browser_instrumentation_cost_ms"] == 0.42


def test_all_browser_sources_are_retained_in_shared_series():
    observations = tuple(
        Observation(f"browser-{index}", "browser", f"browser:{index}", 10 + index, f"epoch:{index}", 1, {
            "kind": "api",
        })
        for index in range(materializer.MAX_PRIVATE_BROWSER_CLIENTS + 1)
    )

    generation = _build(_snapshot(observations=observations), until=30)

    bucket = next(bucket for bucket in generation.layer(10).buckets if bucket.start == 10)
    api = next(item for item in bucket.series if item.name == "browser_api_per_second")

    assert api.source_count == materializer.MAX_PRIVATE_BROWSER_CLIENTS + 1
    assert api.value == 0.1
    assert generation.private_source_ids == ()


def test_incremental_browser_update_reuses_unmodified_shared_buckets():
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
    second_a = next(bucket for bucket in second.layer(10).buckets if bucket.start == 20)

    assert second_shared is first_shared
    assert {item.name for item in second_a.series} == {"browser_api_per_second"}


def test_shifted_incremental_layers_match_full_build_with_shared_browser_series_and_cost():
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
    assert new_shared is old_shared
    assert incremental.private_source_ids == ()


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

    expected_fold_count = len(RESOLUTIONS) + 1
    total_bucket_count = sum(len(layer.buckets) for layer in updated.layers)
    assert len(folded) == expected_fold_count == 5
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


def test_legacy_per_sample_coverage_is_compacted_before_gap_scans_without_erasing_gaps():
    legacy = tuple(
        CoverageEpoch("cpu", "port:7443", f"42:cpu:{int(100 + index + index * 0.4)}", 100 + index + index * 0.4, 101 + index + index * 0.4, 1, 42)
        for index in range(5_000)
    )
    boundaries = (
        CoverageEpoch("cpu", "port:7443", "positive-gap", 7_100, 7_101, 1, 42),
        CoverageEpoch("cpu", "port:7443", "new-owner", 7_101, 7_102, 1, 43),
        CoverageEpoch("cpu", "other", "new-source", 7_102, 7_103, 1, 42),
        CoverageEpoch("gpu", "port:7443", "new-family", 7_103, 7_113, 10, 42),
    )

    compacted = materializer._coalesce_coverage_epochs((*legacy, *boundaries), ())

    assert len(compacted) == 5
    first = next(
        item
        for item in compacted
        if (item.family, item.source_id, item.owner_generation) == ("cpu", "port:7443", 42)
        and item.epoch_id != "positive-gap"
    )
    assert (first.family, first.source_id, first.started_at, first.ended_at) == (
        "cpu", "port:7443", 100, legacy[-1].ended_at,
    )
    assert first.epoch_id == legacy[-1].epoch_id
    assert all(item in compacted for item in boundaries)
    assert materializer._coverage_gaps(
        _snapshot(coverage=legacy),
        legacy[0].started_at,
        legacy[-1].ended_at,
    ) == ()


def test_legacy_inline_normalization_keeps_explicit_unavailable_separator():
    before = CoverageEpoch("cpu", "port:7443", "42:cpu:100", 100, 101, 1, 42)
    after = CoverageEpoch("cpu", "port:7443", "42:cpu:101", 101.4, 102.4, 1, 42)
    unavailable = UnavailableSpan(
        "cpu", "port:7443", "explicit", 101, 101.4, 1, "collector_missed", 42,
    )

    compacted = materializer._coalesce_coverage_epochs((before, after), (unavailable,))
    gaps = materializer._coverage_gaps(
        _snapshot(coverage=(before, after), unavailable=(unavailable,)),
        100,
        102.4,
    )

    assert compacted == (before, after)
    assert [(gap.start, gap.end, gap.reason) for gap in gaps] == [
        (101, 101.4, "collector_missed"),
    ]


def test_legacy_inline_normalization_preserves_nonlegacy_epochs_exactly():
    scheduled = CoverageEpoch("gpu", "gpu:0", "scheduled:second", 110, 125, 10, 42)
    canonical = CoverageEpoch("gpu", "gpu:0", "inline:42:gpu:first", 100, 120, 10, 42)
    touching = CoverageEpoch("gpu", "gpu:0", "inline:42:gpu:third", 125, 130, 10, 42)
    unavailable = UnavailableSpan(
        "gpu", "gpu:0", "scheduled:unavailable", 130, 140, 10, "collector_missed", 42,
    )
    epochs = (scheduled, canonical, touching)

    compacted = materializer._coalesce_coverage_epochs(epochs, (unavailable,))
    gaps = materializer._coverage_gaps(
        _snapshot(coverage=epochs, unavailable=(unavailable,)),
        100,
        140,
    )

    assert compacted == epochs
    assert gaps == (
        gaps[0].__class__("gpu", "gpu:0", "scheduled:unavailable", 130, 140, 10, "collector_missed"),
    )


def test_legacy_inline_detection_rejects_numeric_equivalent_noncanonical_ids():
    epochs = (
        CoverageEpoch("cpu", "host", "42:cpu:-0", 0, 100, 1, 42),
        CoverageEpoch("cpu", "host", "42:cpu:+100", 100, 101, 1, 42),
        CoverageEpoch("cpu", "host", "42:cpu:0101", 101, 102, 1, 42),
        CoverageEpoch("cpu", "host", "42:cpu: 102", 102, 103, 1, 42),
        CoverageEpoch("cpu", "host", "42:cpu:1e2", 103, 104, 1, 42),
        CoverageEpoch("cpu", "host", "42:cpu:104", 104, 105, 1, 42),
        CoverageEpoch("cpu", "host", "inline:42:cpu:stable", 105, 106, 1, 42),
        CoverageEpoch("cpu", "host", "42:cpu:106", 106, 107, 1, 42),
        CoverageEpoch("cpu", "host", "scheduled:stable", 107, 108, 1, 42),
    )
    unavailable = UnavailableSpan(
        "cpu", "host", "explicit", 108, 109, 1, "collector_missed", 42,
    )

    assert [materializer._is_legacy_inline_epoch(item) for item in epochs] == [
        False, False, False, False, False, True, False, True, False,
    ]
    assert materializer._coalesce_coverage_epochs(epochs, (unavailable,)) == epochs
    gaps = materializer._coverage_gaps(
        _snapshot(coverage=epochs, unavailable=(unavailable,)),
        0,
        109,
    )
    assert gaps == (
        gaps[0].__class__("cpu", "host", "explicit", 108, 109, 1, "collector_missed"),
    )


def test_legacy_inline_normalization_compacts_interleaved_sources_per_logical_run():
    epochs = tuple(
        CoverageEpoch(
            "gpu", source, f"42:gpu:{started_at}", started_at, started_at + 10, 10, 42,
        )
        for started_at in (100, 110, 120)
        for source in ("gpu:0", "gpu:1")
    )

    compacted = materializer._coalesce_coverage_epochs(epochs, ())

    assert compacted == (
        CoverageEpoch("gpu", "gpu:0", "42:gpu:120", 100, 130, 10, 42),
        CoverageEpoch("gpu", "gpu:1", "42:gpu:120", 100, 130, 10, 42),
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


def test_usage_scan_cadence_does_not_turn_sparse_event_buckets_into_coverage_gaps():
    snapshot = _snapshot(coverage=(
        CoverageEpoch("agent_tokens", "scan", "scan-1", 0, 10, 10, 1),
        CoverageEpoch("agent_tokens", "scan", "scan-2", 20, None, 10, 1),
    ))
    layer = _build(snapshot, until=30).layer(10)
    assert layer.no_data == ()
    assert all(bucket.series == () for bucket in layer.buckets)


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


def test_coarse_service_cpu_buckets_publish_average_minimum_and_maximum():
    observations = tuple(
        Observation(f"service-statsd-{at}", "service_load", "statsd", at, "epoch", index, {
            "running": True, "cpu_percent": value, "rss_bytes": 400,
        })
        for index, (at, value) in enumerate(((11, 2), (71, 54), (131, 7)), start=1)
    )

    bucket = next(
        item for item in _build(_snapshot(observations=observations), until=301).layer(300).buckets
        if item.start == 0
    )
    values = _series(bucket)

    assert values["service_cpu_percent:statsd"].value == 21
    assert values["service_cpu_min_percent:statsd"].value == 2
    assert values["service_cpu_max_percent:statsd"].value == 54
    assert values["service_cpu_percent:statsd"].source_count == 3


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
        atom("write-long", "input", "write_1h", 44, "model-a", "agent-a"),
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
        "input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "reasoning", "other",
    )
    assert values["usage_tokens"].value == 246
    assert values["cost_micro_usd"].value == 2_400
    assert dimensions["input"].tokens == 100
    assert dimensions["cache_read"].tokens == 40
    assert dimensions["cache_write_5m"].tokens == 30
    assert dimensions["cache_write_1h"].tokens == 44
    assert dimensions["output"].tokens == 27
    assert dimensions["other"].tokens == 5
    assert detail.priced == materializer.CostCoverage(atoms=7, tokens=239)
    assert detail.unpriced == materializer.CostCoverage(atoms=1, tokens=7)
    assert all(not name.startswith("cost_detail:") for name in values)

    model_a = materializer.cost_detail_model_key("openai", "model-a")
    model_values = {item.key: item for item in detail.models}
    model_a_dimensions = {
        item.dimension: item for item in model_values[model_a].dimensions
    }
    assert model_a_dimensions["input"].tokens == 100
    assert model_a_dimensions["cache_read"].tokens == 40
    assert model_a_dimensions["cache_write_5m"].tokens == 30
    assert model_a_dimensions["cache_write_1h"].tokens == 44
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
    assert report["total_tokens"] == 246
    assert report["total_micro_usd"] == 2_400
    assert report["total_api_list_micro_usd"] == 2_400
    assert report["dimensions"] == {
        "input": {"tokens": 100, "micro_usd": 1_000, "api_list_micro_usd": 1_000},
        "cache_read": {"tokens": 40, "micro_usd": 400, "api_list_micro_usd": 400},
        "cache_write_5m": {"tokens": 30, "micro_usd": 300, "api_list_micro_usd": 300},
        "cache_write_1h": {"tokens": 44, "micro_usd": 440, "api_list_micro_usd": 440},
        "output": {"tokens": 27, "micro_usd": 200, "api_list_micro_usd": 200},
        "other": {"tokens": 5, "micro_usd": 60, "api_list_micro_usd": 60},
    }
    assert report["priced"] == {"atoms": 7, "tokens": 239}
    assert report["unpriced"] == {"atoms": 1, "tokens": 7}
    assert report["reasoning_available"] is False
    assert "reasoning" not in report["dimensions"]
    assert report["catalog_revision"] == 3
    assert report["omissions"] == {"models": 0, "agents": 0, "evidence": 0}
    model_rows = {row["model"]: row for row in report["models"]}
    assert model_rows["model-a"]["total_tokens"] == 214
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
    assert materializer._privacy_safe_agent_label(
        "122_frontend-crates|0|%17|codex", "codex"
    ) == "122_frontend-crates|0|%17|codex"
    assert first.startswith("claude-bg:")
    assert first != second
    assert len(first.encode()) <= 64
    assert "123456789abc" not in first
    private = materializer._privacy_safe_agent_label("/Users/private/transcript", "codex")
    assert private.startswith("codex:")
    assert "/Users/private" not in private
    private_tmux_like = materializer._privacy_safe_agent_label(
        "/Users/private|0|%17|codex", "codex"
    )
    assert private_tmux_like.startswith("codex:")
    assert "/Users/private" not in private_tmux_like


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


def _reference_uncovered_portions(explicit_gaps, start, end):
    """Brute-force interval subtraction the indexed seek must reproduce exactly."""

    portions = [(start, end)]
    for existing in explicit_gaps:
        next_portions = []
        for piece_start, piece_end in portions:
            if existing.end <= piece_start or existing.start >= piece_end:
                next_portions.append((piece_start, piece_end))
                continue
            if piece_start < existing.start:
                next_portions.append((piece_start, existing.start))
            if existing.end < piece_end:
                next_portions.append((existing.end, piece_end))
        portions = next_portions
    return [(a, b) for a, b in portions if a < b]


@pytest.mark.parametrize("intervals", [1, 2, 5, 17, 64])
def test_uncovered_gap_seek_matches_brute_force_subtraction(intervals):
    """The bisect seek may never change which portions survive subtraction."""

    explicit = [
        materializer.NoData("cpu", "host", f"missed:{index}", index * 2 + 1, index * 2 + 2, 1, "x")
        for index in range(intervals)
    ]
    starts = [item.start for item in explicit]
    for start, end in ((0, intervals * 2 + 2), (1, 2), (0.5, 1.5), (1.5, 4.5), (3, 3), (2, 100)):
        computed = []
        materializer._append_uncovered_gap(
            explicit, starts, computed,
            materializer.NoData("cpu", "host", "candidate", start, end, 1, "y"),
        )
        assert [(item.start, item.end) for item in computed] == _reference_uncovered_portions(
            explicit, start, end,
        ), (start, end, intervals)
        assert all(item.epoch_id == "candidate" and item.reason == "y" for item in computed)


class _SeekOnlyGaps:
    """Explicit-span sequence that forbids whole-list iteration and counts indexing.

    ``_append_uncovered_gap`` may only reach the spans that actually overlap the
    candidate. Iterating the whole sequence is the exact defect this guards, so
    ``__iter__`` fails outright instead of returning data, and every
    ``__getitem__`` is counted. Nothing here depends on wall-clock time, so the
    gate cannot be moved by host load.
    """

    def __init__(self, items):
        self._items = items
        self.gets = 0

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        self.gets += 1
        return self._items[index]

    def __iter__(self):
        raise AssertionError(
            "_append_uncovered_gap iterated the whole explicit-span list; "
            "it must seek to the overlapping spans instead"
        )


def _seek_accesses(span_count, candidate_start, candidate_end):
    """Index accesses used to subtract one candidate from ``span_count`` spans."""

    explicit = _SeekOnlyGaps([
        materializer.NoData("cpu", "host", f"missed:{index}", index * 10, index * 10 + 5, 1, "m")
        for index in range(span_count)
    ])
    starts = [explicit[index].start for index in range(span_count)]
    explicit.gets = 0  # exclude fixture construction from the measurement
    computed = []
    materializer._append_uncovered_gap(
        explicit, starts, computed,
        materializer.NoData("cpu", "host", "candidate", candidate_start, candidate_end, 1, "y"),
    )
    return explicit.gets, computed


def test_uncovered_gap_seeks_instead_of_scanning_every_explicit_span():
    """Cost of one subtraction must not grow with the retained span count.

    ``_append_uncovered_gap`` used to walk the whole explicit-span list for every
    coverage epoch, making each build O(coverage epochs x explicit spans). On a
    live statsd that was 15.6s of CPU in a 40s sampled profile, the largest single
    owner, and it grew quadratically as the retained 24h window filled. A candidate
    overlapping a fixed number of spans must cost a fixed number of accesses no
    matter how much history is retained.
    """

    # Candidate [12, 28) overlaps exactly two spans ([10,15) and [20,25)) in every
    # fixture size, so a seeking implementation does identical work in all of them.
    baseline, expected_portions = _seek_accesses(64, 12, 28)
    for span_count in (256, 1024, 4096, 16384):
        accesses, computed = _seek_accesses(span_count, 12, 28)
        assert accesses == baseline, (
            f"{accesses} span accesses at {span_count} retained spans vs {baseline} "
            f"at 64; subtraction cost must not grow with retained history"
        )
        assert [(item.start, item.end) for item in computed] == [
            (item.start, item.end) for item in expected_portions
        ]
    # Pin the absolute cost too, so a future rewrite cannot become uniformly linear.
    assert baseline <= 8, f"expected an O(1) seek, used {baseline} accesses"


@pytest.mark.parametrize("span_count", [256, 4096])
def test_uncovered_gap_full_span_candidate_touches_only_covered_spans(span_count):
    """A candidate spanning everything must still visit only the spans it overlaps."""

    accesses, computed = _seek_accesses(span_count, 0, 55)
    # Overlaps spans 0..5; a full scan would cost span_count accesses.
    assert accesses <= 12, f"used {accesses} accesses for a 6-span candidate"
    assert computed, "candidate must yield uncovered portions between the spans"


@pytest.mark.parametrize(
    "spans,expected",
    [
        # Raw overlapping markers: normalization keeps the earliest marker as the
        # owner of the overlap and retains only the uncovered portion of the later.
        ((("a", 10, 30), ("b", 20, 40)),
         [(10, 30, "collector_missed"), (30, 40, "collector_missed")]),
        # Exactly touching markers stay separate; the rest is a computed gap.
        ((("a", 10, 20), ("b", 20, 30)),
         [(10, 20, "collector_missed"), (20, 30, "collector_missed"), (30, 40, "coverage_gap")]),
        # Exact duplicates collapse to one marker.
        ((("a", 10, 20), ("b", 10, 20)),
         [(10, 20, "collector_missed"), (20, 40, "coverage_gap")]),
        # A fully contained marker is absorbed by its enclosing owner.
        ((("a", 10, 40), ("b", 20, 30)), [(10, 40, "collector_missed")]),
        # Disjoint markers keep a computed gap between them.
        ((("a", 10, 20), ("b", 30, 40)),
         [(10, 20, "collector_missed"), (20, 30, "coverage_gap"), (30, 40, "collector_missed")]),
        # One marker spanning the whole uncovered stretch leaves nothing computed.
        ((("a", 10, 40),), [(10, 40, "collector_missed")]),
    ],
)
def test_coverage_gap_matrix_survives_raw_overlapping_unavailable_markers(spans, expected):
    """Public no-data contract across overlap, touching, duplicate and nesting.

    These go through ``_coverage_gaps`` rather than the subtraction helper so the
    matrix stays valid whatever the helper's signature is, and so it exercises the
    normalization the helper's ordering invariant depends on.
    """

    coverage = (
        CoverageEpoch("cpu", "host", "before", 0, 10, 1, 42),
        CoverageEpoch("cpu", "host", "after", 40, 50, 1, 42),
    )
    unavailable = tuple(
        UnavailableSpan("cpu", "host", epoch_id, start, end, 1, "collector_missed", 42)
        for epoch_id, start, end in spans
    )
    gaps = materializer._coverage_gaps(
        _snapshot(coverage=coverage, unavailable=unavailable), 0, 50,
    )
    assert [(gap.start, gap.end, gap.reason) for gap in gaps] == expected
    # Whatever the marker shape, the uncovered stretch is fully accounted for.
    assert gaps[0].start == 10 and gaps[-1].end == 40
    assert all(a.end == b.start for a, b in zip(gaps, gaps[1:]))


@pytest.mark.parametrize(
    "candidate_start,candidate_end",
    [(0, 100), (20, 30), (19, 31), (20, 25), (25, 30), (0, 20), (30, 100), (25, 25), (5, 10)],
)
def test_uncovered_gap_candidate_boundaries_match_brute_force(candidate_start, candidate_end):
    """Candidate exactly on, inside, before, after and empty against a span set."""

    explicit = [
        materializer.NoData("cpu", "host", "m1", 20, 30, 1, "collector_missed"),
        materializer.NoData("cpu", "host", "m2", 40, 50, 1, "collector_missed"),
        materializer.NoData("cpu", "host", "m3", 50, 60, 1, "collector_missed"),
    ]
    starts = [item.start for item in explicit]
    computed = []
    materializer._append_uncovered_gap(
        explicit, starts, computed,
        materializer.NoData("cpu", "host", "candidate", candidate_start, candidate_end, 1, "y"),
    )
    assert [(item.start, item.end) for item in computed] == _reference_uncovered_portions(
        explicit, candidate_start, candidate_end,
    )
