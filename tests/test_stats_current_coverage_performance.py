# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Coverage-history performance contracts for current YO!stats."""

from dataclasses import replace

from yomux_lib.stats_current import materializer, storage
from yomux_lib.stats_current import pricing
from yomux_lib.stats_current import service as service_module


class CountingTuple(tuple):
    def __new__(cls, values):
        instance = super().__new__(cls, values)
        instance.yielded = 0
        return instance

    def __iter__(self):
        for item in super().__iter__():
            self.yielded += 1
            yield item


class RejectIterationTuple(tuple):
    def __iter__(self):
        raise AssertionError("an empty update must not scan retained history")


def test_projection_work_is_once_per_fact_across_resolution_cells(monkeypatch):
    observation = storage.Observation(
        "service-load:statsd:1",
        "service_load",
        "statsd",
        100_000.25,
        "epoch:statsd",
        1,
        {"running": True, "cpu_percent": 12.5, "rss_bytes": 1024},
    )
    atom = storage.UsageAtom(
        "usage:1",
        "output",
        "text",
        "none",
        "tokens",
        100_000.25,
        {
            "quantity": 10,
            "provider": "openai",
            "agent_id": "codex",
            "model": "gpt",
            "telemetry_complete": True,
        },
    )
    snapshot = storage.StoreSnapshot(
        storage.SchemaMetadata(7, 24, 5),
        (observation,),
        (),
        (atom,),
        (),
    )
    validation_calls = 0
    price_calls = 0
    original_validate = materializer.validate_payload

    def validate_once(family, payload):
        nonlocal validation_calls
        validation_calls += 1
        return original_validate(family, payload)

    def price_once(_atom):
        nonlocal price_calls
        price_calls += 1
        return pricing.UsagePriceProjection(None, None, None)

    monkeypatch.setattr(materializer, "validate_payload", validate_once)

    materializer.build_generation(
        snapshot,
        source_generation=5,
        cache_generation=6,
        generated_at=100_001,
        observed_until=100_001,
        price_resolver=price_once,
    )

    assert validation_calls == 1
    assert price_calls == 1


def test_incremental_observation_projection_reuses_unchanged_fact(monkeypatch):
    observed_at = 100_000.25
    observation = storage.Observation(
        "service-load:statsd:1",
        "service_load",
        "statsd",
        observed_at,
        "epoch:statsd",
        1,
        {"running": True, "cpu_percent": 12.5, "rss_bytes": 1024},
    )
    snapshot = storage.StoreSnapshot(
        storage.SchemaMetadata(7, 24, 5),
        (observation,),
        (),
        (),
        (),
    )
    cache = materializer.ProjectionCache()
    dirty = tuple(
        materializer.DirtyCell(
            resolution,
            int(observed_at // resolution) * resolution,
        )
        for resolution in materializer.RESOLUTIONS
    )
    validation_calls = 0
    original_validate = materializer.validate_payload

    def count_validation(family, payload):
        nonlocal validation_calls
        validation_calls += 1
        return original_validate(family, payload)

    monkeypatch.setattr(materializer, "validate_payload", count_validation)
    previous = materializer.build_generation(
        snapshot,
        source_generation=5,
        cache_generation=6,
        generated_at=100_001,
        observed_until=100_001,
    )
    validation_calls = 0
    first = materializer.update_generation(
        previous,
        snapshot,
        dirty,
        source_generation=5,
        cache_generation=7,
        generated_at=100_002,
        observed_until=100_002,
        projection_cache=cache,
    )
    second = materializer.update_generation(
        first,
        snapshot,
        dirty,
        source_generation=5,
        cache_generation=8,
        generated_at=100_003,
        observed_until=100_003,
        projection_cache=cache,
    )

    assert second.source_generation == 5
    assert validation_calls == 1


def test_incremental_projection_cache_retires_closed_cells():
    first_observation = storage.Observation(
        "service-load:statsd:1",
        "service_load",
        "statsd",
        100_000.25,
        "epoch:statsd",
        1,
        {"running": True, "cpu_percent": 12.5, "rss_bytes": 1024},
    )
    second_observation = replace(
        first_observation,
        event_id="service-load:statsd:2",
        observed_at=100_001.25,
    )
    snapshot = storage.StoreSnapshot(
        storage.SchemaMetadata(7, 24, 5),
        (first_observation, second_observation),
        (),
        (),
        (),
    )
    cache = materializer.ProjectionCache()
    previous = materializer.build_generation(
        snapshot,
        source_generation=5,
        cache_generation=6,
        generated_at=100_002,
        observed_until=100_002,
    )
    first = materializer.update_generation(
        previous,
        snapshot,
        (
            materializer.DirtyCell(1, int(first_observation.observed_at)),
            materializer.DirtyCell(1, int(second_observation.observed_at)),
        ),
        source_generation=5,
        cache_generation=7,
        generated_at=100_003,
        observed_until=100_003,
        projection_cache=cache,
    )

    assert len(cache) == 2

    materializer.update_generation(
        first,
        snapshot,
        (materializer.DirtyCell(1, int(second_observation.observed_at)),),
        source_generation=5,
        cache_generation=8,
        generated_at=100_004,
        observed_until=100_004,
        projection_cache=cache,
    )

    assert len(cache) == 1
    assert next(iter(cache._observations))[2] == second_observation.event_id


def test_incremental_observation_projection_revalidates_changed_identity(monkeypatch):
    observed_at = 100_000.25
    original = storage.Observation(
        "service-load:statsd:1",
        "service_load",
        "statsd",
        observed_at,
        "epoch:statsd",
        1,
        {"running": True, "cpu_percent": 12.5, "rss_bytes": 1024},
    )
    changed = replace(
        original,
        payload={"running": True, "cpu_percent": 25.0, "rss_bytes": 1024},
    )
    cache = materializer.ProjectionCache()
    first = cache.observation(original)
    validation_calls = 0
    original_validate = materializer.validate_payload

    def count_validation(family, payload):
        nonlocal validation_calls
        validation_calls += 1
        return original_validate(family, payload)

    monkeypatch.setattr(materializer, "validate_payload", count_validation)
    second = cache.observation(changed)

    assert second is not first
    assert validation_calls == 1
    assert any(sample.value == 25.0 for sample in second.samples)


def test_empty_merge_reuses_normalized_history_without_scanning():
    coverage = RejectIterationTuple((storage.CoverageEpoch(
        "service_load", "statsd", "epoch:retained", 10, 11, 1, 42,
    ),))
    unavailable = RejectIterationTuple(())

    merged = materializer.merge_normalized_coverage_model(
        coverage, unavailable, (), (),
    )

    assert merged == (coverage, unavailable)


def test_normalized_gap_build_indexes_each_retained_fact_once(monkeypatch):
    coverage = CountingTuple(
        storage.CoverageEpoch(
            "service_load", source, f"epoch:{index}", index * 2, index * 2 + 1, 1, 42,
        )
        for source in ("approvald", "indexd", "batchd", "statsd", "statusd", "watchd")
        for index in range(64)
    )
    unavailable = CountingTuple(
        storage.UnavailableSpan(
            "service_load", source, f"miss:{index}", index * 2 + 1, index * 2 + 2,
            1, "scheduler_deadline_missed", 42,
        )
        for source in ("approvald", "indexd", "batchd", "statsd", "statusd", "watchd")
        for index in range(64)
    )
    snapshot = storage.StoreSnapshot(
        storage.SchemaMetadata(7, 24, 5), (), coverage, (), (), unavailable,
        coverage_normalized=True,
    )
    identity_checks = 0

    def record_identity_check(value, name):
        nonlocal identity_checks
        identity_checks += 1
        return value

    monkeypatch.setattr(materializer.identity, "identity_text", record_identity_check)

    gaps = materializer._coverage_gaps(snapshot, 0, 128)

    assert coverage.yielded == len(coverage)
    assert unavailable.yielded == len(unavailable)
    assert identity_checks == 0
    assert len(gaps) == 6 * 64


def test_existing_epoch_update_does_not_renormalize_warm_history(monkeypatch):
    coverage = CountingTuple(
        storage.CoverageEpoch(
            "service_load", "statsd", f"epoch:{index}", index, index + 1, 1, 42,
        )
        for index in range(4_096)
    )
    updated = replace(coverage[-1], ended_at=coverage[-1].ended_at + 1)

    def reject_normalization(*_args):
        raise AssertionError("an existing epoch update must not renormalize retained history")

    monkeypatch.setattr(materializer, "normalize_coverage_model", reject_normalization)

    merged, unavailable = materializer.merge_normalized_coverage_model(
        coverage, (), (updated,), (),
    )

    assert coverage.yielded == len(coverage)
    assert merged[:-1] == coverage[:-1]
    assert merged[-1] == updated
    assert unavailable == ()


def test_new_epoch_falls_back_to_sorted_normalized_history():
    retained = storage.CoverageEpoch(
        "service_load", "statsd", "epoch:retained", 10, 11, 1, 42,
    )
    inserted = storage.CoverageEpoch(
        "service_load", "statsd", "epoch:inserted", 5, 6, 1, 42,
    )

    merged, unavailable = materializer.merge_normalized_coverage_model(
        (retained,), (), (inserted,), (),
    )

    assert merged == (inserted, retained)
    assert unavailable == ()


def test_healthy_epoch_extensions_reuse_exact_gap_geometry(tmp_path, monkeypatch):
    observed_until = 100_000.0
    retained = storage.CoverageEpoch(
        "service_load", "statsd", "epoch:current",
        observed_until - 60, observed_until + 1, 1, 42,
    )
    snapshot = storage.StoreSnapshot(
        storage.SchemaMetadata(7, 24, 5), (), (retained,), (), (), (),
        coverage_normalized=True,
    )
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    with service.work_lock:
        service._cached_coverage_epochs = snapshot.coverage_epochs
        service._cached_unavailable_spans = snapshot.unavailable_spans
        service._coverage_cache_ready = True
        service._coverage_gap_cache = service_module._CoverageGapCache.from_coverage(
            snapshot.coverage_epochs
        )

    scans = 0
    original = materializer._coverage_gaps

    def record_scan(*args):
        nonlocal scans
        scans += 1
        return original(*args)

    monkeypatch.setattr(materializer, "_coverage_gaps", record_scan)
    oldest = observed_until - 300
    first = service._coverage_gaps_for_build(
        snapshot, service._coverage_version, oldest, observed_until,
    )

    extended = replace(retained, ended_at=observed_until + 2)
    with service.work_lock:
        service._update_cached_coverage_locked(
            (extended,), (), accepted_change=True, retention_prune=None,
        )
        version = service._coverage_version
        extended_snapshot = replace(
            snapshot,
            coverage_epochs=service._cached_coverage_epochs,
            unavailable_spans=service._cached_unavailable_spans,
        )
    second = service._coverage_gaps_for_build(
        extended_snapshot, version, oldest, observed_until + 0.5,
    )

    assert first == second == original(
        extended_snapshot, oldest, observed_until + 0.5,
    )
    assert scans == 1

    expired = service._coverage_gaps_for_build(
        extended_snapshot, version, oldest, observed_until + 3,
    )
    assert expired == original(extended_snapshot, oldest, observed_until + 3)
    assert scans == 1


def test_cached_multi_source_gaps_match_full_build_after_live_tail_extensions(
    tmp_path,
    monkeypatch,
):
    observed_until = 100.0
    oldest = 0.0
    coverage = (
        storage.CoverageEpoch("service_load", "statsd", "statsd:old", 0, 20, 1, 42),
        storage.CoverageEpoch("service_load", "statsd", "statsd:live", 40, 80, 1, 42),
        storage.CoverageEpoch("service_load", "batchd", "batchd:live", 0, 70, 1, 42),
        storage.CoverageEpoch("cpu", "host", "cpu:live", 0, 90, 1, 42),
    )
    unavailable = (
        storage.UnavailableSpan(
            "service_load", "statsd", "statsd:miss", 25, 30, 1,
            "scheduler_deadline_missed", 42,
        ),
        storage.UnavailableSpan(
            "service_load", "batchd", "batchd:miss", 72, 74, 1,
            "scheduler_deadline_missed", 42,
        ),
        storage.UnavailableSpan(
            "cpu", "host", "cpu:miss", 92, 94, 1,
            "scheduler_deadline_missed", 42,
        ),
    )
    snapshot = storage.StoreSnapshot(
        storage.SchemaMetadata(7, 24, 5), (), coverage, (), (), unavailable,
        coverage_normalized=True,
    )
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    with service.work_lock:
        service._cached_coverage_epochs = snapshot.coverage_epochs
        service._cached_unavailable_spans = snapshot.unavailable_spans
        service._coverage_cache_ready = True
        service._coverage_gap_cache = service_module._CoverageGapCache.from_coverage(
            snapshot.coverage_epochs
        )

    scans = 0
    original = materializer._coverage_gaps

    def record_scan(*args):
        nonlocal scans
        scans += 1
        return original(*args)

    monkeypatch.setattr(materializer, "_coverage_gaps", record_scan)
    first = service._coverage_gaps_for_build(
        snapshot, service._coverage_version, oldest, observed_until,
    )
    assert first == original(snapshot, oldest, observed_until)

    extensions = (
        replace(coverage[1], ended_at=85),
        replace(coverage[3], ended_at=95),
    )
    with service.work_lock:
        service._update_cached_coverage_locked(
            extensions, (), accepted_change=True, retention_prune=None,
        )
        version = service._coverage_version
        extended_snapshot = replace(
            snapshot,
            coverage_epochs=service._cached_coverage_epochs,
            unavailable_spans=service._cached_unavailable_spans,
        )
    second = service._coverage_gaps_for_build(
        extended_snapshot, version, oldest, observed_until + 1,
    )

    assert second == original(extended_snapshot, oldest, observed_until + 1)
    assert scans == 1
    assert any(
        gap.source_id == "statsd"
        and gap.reason == "coverage_gap"
        and (gap.start, gap.end) == (85, 101)
        for gap in second
    )
    assert not any(
        gap.source_id == "batchd"
        and gap.reason == "coverage_gap"
        and gap.end == 101
        for gap in second
    )


def test_clip_gaps_reuses_fully_contained_objects():
    partial = materializer.NoData("cpu", "partial", "epoch:partial", 8, 11, 1)
    contained = materializer.NoData("cpu", "contained", "epoch:contained", 12, 18, 1)

    clipped = materializer._clip_gaps((partial, contained), 10, 20)

    assert clipped[0] == replace(partial, start=10)
    assert clipped[0] is not partial
    assert clipped[1] is contained
