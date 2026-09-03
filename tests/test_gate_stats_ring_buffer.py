# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Red contracts for persisted fixed-size YO!stats resolution rings."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from yolomux_lib.stats_current import families
from yolomux_lib.stats_current import materializer
from yolomux_lib.stats_current import protocol
from yolomux_lib.stats_current import resolution
from yolomux_lib.stats_current import service as service_module
from yolomux_lib.stats_current import storage


RING_CAPACITIES = {1: 300, 10: 180, 60: 480, 300: 288}
TOTAL_RING_SLOTS = 1_248
# Derived from the production owner rather than restated. This was a hand-maintained copy of the
# same list, so schema 8's two new tables made it silently wrong in five places at once.
RING_TABLES = tuple(sorted(storage._RING_TABLES))


def _ring_table_names(store: storage.Store) -> set[str]:
    connection = store._connection()
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    } & set(storage._RING_TABLES)


def _ring_counts(store: storage.Store) -> dict[int, int]:
    connection = store._connection()
    tables = _ring_table_names(store)
    assert tables == set(RING_TABLES), tables
    rows = connection.execute(
        "SELECT resolution_seconds, count(*) FROM aggregate_ring_slots "
        "GROUP BY resolution_seconds ORDER BY resolution_seconds"
    ).fetchall()
    return {int(row[0]): int(row[1]) for row in rows}


def _bucket(
    resolution_seconds: int,
    bucket_start: int,
    *,
    value: int | None = None,
    complete: bool = True,
) -> object:
    series = {}
    source = {"first_timestamp": None, "last_timestamp": None, "count": 0}
    if value is not None:
        series = {
            "fixture_value": {
                "value": value,
                "source_count": 1,
                "first_timestamp": bucket_start,
                "last_timestamp": bucket_start,
            }
        }
        source = {
            "first_timestamp": bucket_start,
            "last_timestamp": bucket_start,
            "count": 1,
        }
    return storage.RingBucketWrite(
        resolution_seconds=resolution_seconds,
        bucket_start=bucket_start,
        bucket_json=json.dumps(
            {"series": series, "source": source},
            sort_keys=True,
            separators=(",", ":"),
        ),
        complete=complete,
    )


def _publish(
    store: storage.Store,
    *buckets: object,
    source_generation: int,
    published_at: float,
) -> object:
    return store.publish_ring_buckets(
        buckets=buckets,
        source_generation=source_generation,
        published_at=published_at,
    )


def _cpu_append_request(observed_at: float) -> dict[str, object]:
    return {
        "protocol_version": storage.MIN_WRITER_PROTOCOL,
        "schema_generation": storage.SCHEMA_VERSION,
        "action": "append",
        "observations": [{
            "event_id": "cpu-ring-writer",
            "family": "cpu",
            "source_id": "host",
            "observed_at": observed_at,
            "epoch_id": "cpu:ring-writer",
            "owner_generation": 1,
            "payload": {"process_percent": 2, "system_percent": 4},
        }],
        "usage_atoms": [],
        "usage_tombstones": [],
        "coverage_epochs": [],
        "unavailable_spans": [],
    }


def _real_ingest_request(
    observed_at: float,
    *,
    usage_coverage_ended_at: float | None = None,
) -> dict[str, object]:
    request = _cpu_append_request(observed_at)
    request["usage_atoms"] = [{
        "event_id": "usage-ring-writer",
        "direction": "input",
        "modality": "text",
        "cache_role": "none",
        "unit": "tokens",
        "observed_at": observed_at,
        "payload": {
            "quantity": 12,
            "provider": "openai",
            "model": "gpt",
            "agent_id": "ring-writer",
            "telemetry_complete": True,
        },
    }]
    if usage_coverage_ended_at is not None:
        request["coverage_epochs"] = [{
            "family": "agent_tokens",
            "source_id": "port:48123",
            "epoch_id": "usage-scan-before-direct-atom",
            "started_at": usage_coverage_ended_at - 60,
            "ended_at": usage_coverage_ended_at,
            "native_cadence_seconds": 60,
            "owner_generation": 1,
        }]
    return request


def _snapshot_request(
    *,
    range_seconds: int = 300,
    requested_resolution: int | str = resolution.AUTO,
    client_id: str = "ring-reader",
) -> dict[str, object]:
    return {
        "protocol_version": storage.MIN_WRITER_PROTOCOL,
        "schema_generation": storage.SCHEMA_VERSION,
        "action": "snapshot",
        "range_seconds": range_seconds,
        "resolution": requested_resolution,
        "client_id": client_id,
    }


def _delta_request(
    *,
    range_seconds: int = 300,
    resolution_seconds: int = 1,
    client_id: str = "ring-reader",
    after_cache_generation: int,
    after_revision: int = 0,
) -> dict[str, object]:
    return {
        "protocol_version": storage.MIN_WRITER_PROTOCOL,
        "schema_generation": storage.SCHEMA_VERSION,
        "action": "delta",
        "range_seconds": range_seconds,
        "resolution_seconds": resolution_seconds,
        "client_id": client_id,
        "after_cache_generation": after_cache_generation,
        "after_revision": after_revision,
    }


def _apply_delta(
    snapshot: dict[str, object],
    delta: dict[str, object],
) -> dict[str, object]:
    protocol.validate_delta_for_snapshot(snapshot, delta)
    buckets = {
        (item["start"], item["duration"]): item
        for item in snapshot["buckets"]
    }
    no_data = {
        (item["family"], item["source_id"], item["epoch"], item["start"], item["end"]): item
        for item in snapshot["no_data"]
    }
    for tombstone in delta["tombstones"]:
        if tombstone["kind"] == "bucket":
            buckets.pop((tombstone["start"], tombstone["duration"]), None)
        else:
            no_data.pop((
                tombstone["family"],
                tombstone["source_id"],
                tombstone["epoch"],
                tombstone["start"],
                tombstone["end"],
            ), None)
    buckets.update({
        (item["start"], item["duration"]): item
        for item in delta["buckets"]
    })
    no_data.update({
        (item["family"], item["source_id"], item["epoch"], item["start"], item["end"]): item
        for item in delta["no_data"]
    })
    return {
        "source_generation": delta["source_generation"],
        "cache_generation": delta["cache_generation"],
        "buckets": [buckets[key] for key in sorted(buckets)],
        "no_data": [no_data[key] for key in sorted(no_data)],
        "cost_report": delta["cost_report"],
    }


def _assert_rings_match_generation(
    store: storage.Store,
    generation: materializer.Generation,
) -> None:
    for layer in generation.layers:
        window = store.read_ring_window(
            range_seconds=materializer.LAYER_SECONDS[layer.resolution],
            resolution_seconds=layer.resolution,
            window_end=layer.end,
        )
        assert window.missing_bucket_starts == ()
        assert {
            row.bucket_start: json.loads(row.bucket_json)["bucket"]
            for row in window.rows
        } == {
            bucket.start: service_module._wire_bucket(bucket)
            for bucket in layer.buckets
        }


@pytest.mark.parametrize(
    ("resolution_seconds", "bucket_start", "observations_at"),
    (
        (10, 86_390, (86_391, 86_394, 86_398)),
        (60, 86_340, (86_341, 86_351, 86_361)),
        (300, 86_100, (86_111, 86_171, 86_231)),
    ),
)
def test_persisted_coarse_service_cpu_bucket_keeps_minimum_average_and_maximum(
    tmp_path: Path,
    resolution_seconds: int,
    bucket_start: int,
    observations_at: tuple[int, int, int],
) -> None:
    observations = tuple(
        storage.Observation(
            f"service-statsd-{observed_at}",
            "service_load",
            "statsd",
            observed_at,
            "service:epoch",
            owner_generation,
            {"running": True, "cpu_percent": value, "rss_bytes": 400},
        )
        for owner_generation, (observed_at, value) in enumerate(
            zip(observations_at, (2, 54, 7), strict=True),
            start=1,
        )
    ) + tuple(
        storage.Observation(
            f"cpu-host-{observed_at}",
            "cpu",
            "host",
            observed_at,
            "cpu:epoch",
            owner_generation,
            {"process_percent": value, "system_percent": value * 2},
        )
        for owner_generation, (observed_at, value) in enumerate(
            zip(observations_at, (2, 54, 7), strict=True),
            start=1,
        )
    )
    snapshot = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 1, 1), observations, (), (), (), (),
    )
    generation = materializer.build_generation(
        snapshot,
        source_generation=1,
        cache_generation=1,
        generated_at=86_400,
        observed_until=86_400,
    )
    writes = service_module.StatsCurrentService._ring_writes(
        generation,
        frozenset({materializer.DirtyCell(resolution_seconds, bucket_start)}),
    )

    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        publication = store.publish_ring_buckets(
            buckets=writes,
            source_generation=1,
            published_at=86_400,
        )
        window = store.read_ring_window(
            range_seconds=materializer.LAYER_SECONDS[resolution_seconds],
            resolution_seconds=resolution_seconds,
            window_end=generation.layer(resolution_seconds).end,
        )

    assert publication.buckets_updated == 1
    row = next(item for item in window.rows if item.bucket_start == bucket_start)
    persisted = json.loads(row.bucket_json)["bucket"]["series"]
    assert persisted["service_cpu_min_percent:statsd"]["value"] == 2
    assert persisted["service_cpu_percent:statsd"]["value"] == 21
    assert persisted["service_cpu_max_percent:statsd"]["value"] == 54
    assert persisted["cpu_min_percent:host"]["value"] == 2
    assert persisted["cpu_percent:host"]["value"] == 21
    assert persisted["cpu_max_percent:host"]["value"] == 54
    assert persisted["system_cpu_min_percent"]["value"] == 4
    assert persisted["system_cpu_percent"]["value"] == 42
    assert persisted["system_cpu_max_percent"]["value"] == 108

    restored = service_module._materialized_ring_bucket(
        service_module._decode_ring_bucket(row)
    )
    restored_series = {item.name: item.value for item in restored.series}
    assert restored_series["service_cpu_min_percent:statsd"] == 2
    assert restored_series["service_cpu_percent:statsd"] == 21
    assert restored_series["service_cpu_max_percent:statsd"] == 54
    assert restored_series["cpu_min_percent:host"] == 2
    assert restored_series["cpu_percent:host"] == 21
    assert restored_series["cpu_max_percent:host"] == 54
    assert restored_series["system_cpu_min_percent"] == 4
    assert restored_series["system_cpu_percent"] == 42
    assert restored_series["system_cpu_max_percent"] == 108


def test_real_ingest_ring_and_published_cache_series_agree_for_every_valid_pair(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = 1_800_000_000.0
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now,
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        accepted, binary = service.handle_with_binary(
            _real_ingest_request(
                wall_now - 0.25,
                usage_coverage_ended_at=wall_now - 60,
            )
        )
        assert accepted["accepted"] == 3
        assert binary == b""
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        assert service._cache is not None

        for range_seconds in resolution.RANGE_SECONDS:
            for resolution_seconds in resolution.explicit_resolutions(range_seconds):
                cache_entry = service._cache.entries[(range_seconds, resolution_seconds, None)]
                cached = json.loads(cache_entry.binary)
                ring = store.read_ring_window(
                    range_seconds=range_seconds,
                    resolution_seconds=resolution_seconds,
                    window_end=cached["window_end"],
                )
                assert ring.missing_bucket_starts == ()
                payloads = [json.loads(row.bucket_json) for row in ring.rows]
                assert [payload["bucket"] for payload in payloads] == cached["buckets"], (
                    range_seconds,
                    resolution_seconds,
                )
                assert cached["cost_report"]["total_tokens"] == 12, (
                    range_seconds,
                    resolution_seconds,
                    cached["cost_report"],
                )
                assert not [
                    span
                    for span in cached["no_data"]
                    if span["family"] in {"agent_tokens", "cost"}
                    and span["reason"] == "coverage_gap"
                ], (range_seconds, resolution_seconds, cached["no_data"])
                assert service_module._merge_ring_no_data(tuple(
                    service_module._decode_ring_bucket(row) for row in ring.rows
                )) == cached["no_data"]
                assert all("view" not in payload for payload in payloads)
                ring_layer = materializer.Layer(
                    resolution_seconds,
                    cached["window_start"],
                    cached["window_end"],
                    tuple(
                        service_module._materialized_ring_bucket(
                            service_module._decode_ring_bucket(row)
                        )
                        for row in ring.rows
                    ),
                    (),
                )
                assert materializer.build_cost_report(ring_layer) == cached["cost_report"]


def test_ring_capacities_and_minimum_density_derive_the_current_view_matrix() -> None:
    assert dict(resolution.RING_CAPACITIES) == RING_CAPACITIES
    for range_seconds in resolution.RANGE_SECONDS:
        derived = tuple(
            resolution_seconds
            for resolution_seconds, slot_count in RING_CAPACITIES.items()
            if resolution.MIN_BUCKETS
            <= range_seconds / resolution_seconds
            <= slot_count
        )
        assert resolution.explicit_resolutions(range_seconds) == derived



def _ring_publication_generation(store: storage.Store) -> int:
    return int(
        store._connection()
        .execute("SELECT ring_generation FROM aggregate_publication WHERE singleton = 1")
        .fetchone()[0]
    )

def test_fixed_slot_row_count_survives_one_hour_of_ingest(tmp_path: Path) -> None:
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        # Schema 8 creates the ring extension with the database. It stopped being an opt-in a
        # caller had to remember, because the durability kernel is absent exactly when a crash
        # needs it if its creation is optional.
        assert _ring_table_names(store) == set(storage._RING_TABLES)
        store.initialize_ring_storage()
        assert _ring_counts(store) == RING_CAPACITIES
        store.initialize_ring_storage()
        assert _ring_counts(store) == RING_CAPACITIES
        with pytest.raises(sqlite3.IntegrityError, match="aggregate ring rows are fixed"):
            store._connection().execute(
                "DELETE FROM aggregate_ring_slots WHERE resolution_seconds = 1 AND slot_index = 0"
            )
        for flush_end in range(10, 3_601, 10):
            writes = [
                _bucket(1, bucket_start, value=bucket_start)
                for bucket_start in range(flush_end - 10, flush_end)
            ]
            writes.extend(
                _bucket(
                    resolution_seconds,
                    ((flush_end - 1) // resolution_seconds) * resolution_seconds,
                    value=flush_end,
                    complete=False,
                )
                for resolution_seconds in (10, 60, 300)
            )
            _publish(
                store,
                *writes,
                source_generation=flush_end,
                published_at=float(flush_end),
            )

        assert _ring_counts(store) == RING_CAPACITIES
        assert sum(_ring_counts(store).values()) == TOTAL_RING_SLOTS


def test_steady_publication_is_one_transaction_and_uses_no_aggregate_insert_or_delete(
    tmp_path: Path,
) -> None:
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        assert _ring_counts(store) == RING_CAPACITIES
        with pytest.raises(storage.StorageValidationError, match="bucket_json exceeds"):
            _publish(
                store,
                storage.RingBucketWrite(
                    resolution_seconds=10,
                    bucket_start=0,
                    bucket_json=json.dumps({"value": "x" * storage.MAX_RING_BUCKET_BYTES}),
                    complete=False,
                ),
                source_generation=1,
                published_at=1.0,
            )
        statements: list[str] = []
        store._connection().set_trace_callback(statements.append)
        _publish(
            store,
            *(
                _bucket(resolution_seconds, 0, value=1, complete=False)
                for resolution_seconds in RING_CAPACITIES
            ),
            source_generation=1,
            published_at=1.0,
        )

    normalized = [statement.strip().upper() for statement in statements]
    assert sum(statement.startswith("BEGIN") for statement in normalized) == 1, normalized
    assert sum(statement == "COMMIT" for statement in normalized) == 1, normalized
    assert any(
        statement.startswith("UPDATE") and "AGGREGATE_RING_SLOTS" in statement
        for statement in normalized
    )
    assert not any(
        (statement.startswith("INSERT") or statement.startswith("DELETE"))
        and "AGGREGATE_" in statement
        for statement in normalized
    ), normalized


def test_large_ring_payload_is_compressed_without_changing_the_read_contract(
    tmp_path: Path,
) -> None:
    bucket_json = json.dumps(
        {"series": {"fixture": {"payload": "repeat-me" * 8_192}}},
        separators=(",", ":"),
        sort_keys=True,
    )
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        _publish(
            store,
            storage.RingBucketWrite(1, 299, bucket_json, False),
            source_generation=1,
            published_at=300.0,
        )
        stored_type, stored_bytes = store._connection().execute(
            "SELECT typeof(bucket_json), length(bucket_json) FROM aggregate_ring_slots "
            "WHERE resolution_seconds = 1 AND slot_index = 299"
        ).fetchone()
        compressed = store.read_ring_window(
            range_seconds=300,
            resolution_seconds=1,
            window_end=300,
        )
        store._connection().execute(
            "UPDATE aggregate_ring_slots SET bucket_json = ? "
            "WHERE resolution_seconds = 1 AND slot_index = 299",
            (bucket_json,),
        )
        legacy = store.read_ring_window(
            range_seconds=300,
            resolution_seconds=1,
            window_end=300,
        )

    assert stored_type == "blob"
    assert stored_bytes < len(bucket_json.encode("utf-8")) // 4
    assert compressed.rows[0].bucket_json == bucket_json
    assert legacy.rows[0].bucket_json == bucket_json


def test_lap_stale_slot_is_absent_from_the_current_window(tmp_path: Path) -> None:
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        assert _ring_counts(store) == RING_CAPACITIES
        _publish(store, _bucket(1, 0, value=7), source_generation=1, published_at=1.0)
        window = store.read_ring_window(
            range_seconds=300,
            resolution_seconds=1,
            window_end=600,
        )

    assert 300 in window.missing_bucket_starts
    assert 0 not in {row.bucket_start for row in window.rows}
    assert 300 not in {row.bucket_start for row in window.rows}


def test_idle_bucket_is_zero_and_not_a_gap(tmp_path: Path) -> None:
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        assert _ring_counts(store) == RING_CAPACITIES
        _publish(store, _bucket(10, 100), source_generation=1, published_at=110.0)
        window = store.read_ring_window(
            range_seconds=300,
            resolution_seconds=10,
            window_end=400,
        )

    idle = next(row for row in window.rows if row.bucket_start == 100)
    payload = json.loads(idle.bucket_json)
    assert payload["series"] == {}
    assert payload["source"] == {
        "count": 0,
        "first_timestamp": None,
        "last_timestamp": None,
    }
    assert 100 not in window.missing_bucket_starts
    assert 110 in window.missing_bucket_starts


def test_window_straddling_write_head_filters_each_expected_timestamp(tmp_path: Path) -> None:
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        assert _ring_counts(store) == RING_CAPACITIES
        _publish(
            store,
            _bucket(10, 0, value=1),
            _bucket(10, 1_790, value=2),
            _bucket(10, 1_800, value=3),
            _bucket(10, 1_810, value=4),
            source_generation=4,
            published_at=1_811.0,
        )
        window = store.read_ring_window(
            range_seconds=300,
            resolution_seconds=10,
            window_end=1_900,
        )

    starts = [row.bucket_start for row in window.rows]
    assert starts == [1_790, 1_800, 1_810]
    assert 0 not in starts
    assert set(window.missing_bucket_starts) == set(range(1_600, 1_900, 10)) - set(starts)


def test_restart_reads_persisted_ring_without_materializer_warm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database) as writer:
        writer.initialize_ring_storage()
        assert _ring_counts(writer) == RING_CAPACITIES
        _publish(writer, _bucket(10, 390, value=9), source_generation=1, published_at=391.0)

    def forbidden_builder(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("persisted ring read invoked the materializer")

    monkeypatch.setattr(materializer, "build_generation", forbidden_builder)
    monkeypatch.setattr(materializer, "update_generation", forbidden_builder)
    with storage.Store.open_reader(database) as reader:
        window = reader.read_ring_window(
            range_seconds=300,
            resolution_seconds=10,
            window_end=400,
        )

    assert [row.bucket_start for row in window.rows] == [390]
    assert json.loads(window.rows[0].bucket_json)["series"]["fixture_value"]["value"] == 9


def test_read_only_follower_cannot_publish_ring_rows(tmp_path: Path) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database) as writer:
        writer.initialize_ring_storage()
        assert _ring_counts(writer) == RING_CAPACITIES

    with storage.Store.open_reader(database) as follower:
        with pytest.raises(storage.StatsCurrentError, match="reader cannot"):
            _publish(
                follower,
                _bucket(10, 0, value=1),
                source_generation=1,
                published_at=1.0,
            )


def test_leader_writer_coalesces_ingest_for_ten_seconds_and_matches_materializer(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())

        # The TABLES exist from open in schema 8; what must still be absent before the flush
        # deadline is any PUBLICATION. That is the behaviour this row actually guards.
        assert _ring_table_names(store) == set(storage._RING_TABLES)
        assert _ring_publication_generation(store) == 0
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS - 0.001
        assert service._flush_ring_if_due() is None
        assert _ring_publication_generation(store) == 0

        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        seeded = service._flush_ring_if_due()
        assert seeded is not None
        assert seeded.buckets_updated == TOTAL_RING_SLOTS
        assert service._cache is not None
        _assert_rings_match_generation(store, service._cache.generation)

        accepted, binary = service.handle_with_binary(
            _cpu_append_request(wall_now[0] - 0.25)
        )
        assert accepted["accepted"] == 1
        assert binary == b""
        work = service._take_work()
        assert work is not None
        service._build_once(store, *work)

        monotonic_now[0] += service_module.RING_FLUSH_SECONDS - 0.001
        assert service._flush_ring_if_due() is None
        assert service._cache is not None
        assert service._cache.generation.source_generation == 1
        before = store.read_ring_window(
            range_seconds=300,
            resolution_seconds=1,
            window_end=service._cache.generation.layer(1).end,
        )
        assert before.source_generation == 0

        monotonic_now[0] += 0.001
        publication = service._flush_ring_if_due()
        assert publication is not None
        assert publication.source_generation == 1
        # A source-generation change writes the exact dirty cell for each supported
        # resolution. Range-level carrier rows are no longer persisted.
        assert publication.buckets_updated == len(resolution.RING_CAPACITIES)
        _assert_rings_match_generation(store, service._cache.generation)

        changed_rows = store._connection().execute(
            "SELECT resolution_seconds, ring_generation FROM aggregate_ring_slots "
            "WHERE source_generation = 1 ORDER BY resolution_seconds"
        ).fetchall()
        assert changed_rows == [
            (resolution_seconds, publication.ring_generation)
            for resolution_seconds in resolution.RING_CAPACITIES
            for _cell in range(1)
        ]
        ring_status = service._status()["ring_writer"]
        assert ring_status == {
            "cadence_seconds": 10.0,
            "sole_writer": True,
            "serving_reads": "published_cache",
            "pending_cells": 0,
            "waiting_for_source_generation": 0,
            "publications": 2,
            "buckets_published": TOTAL_RING_SLOTS + len(resolution.RING_CAPACITIES),
            "last_source_generation": 1,
            "last_at": wall_now[0],
            "last_seconds": 0.0,
            "next_in_seconds": None,
            "failure": "",
        }


def test_snapshot_reads_from_persisted_ring_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: 1_800_000_000.0,
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        assert service._cache is not None
        key = (300, resolution.AUTO, None)
        cached = service._cache.entries[key]
        entries = dict(service._cache.entries)
        entries[key] = service_module.CacheEntry(cached.metadata, b"published-cache-path")
        service._cache = service_module.PublishedCache(
            service._cache.generation,
            entries,
            service._cache.resolution_generations,
            service._cache.entry_generations,
        )
        reads = 0
        original_read = store.read_ring_window

        def counted_read(**values: object) -> storage.RingWindow:
            nonlocal reads
            reads += 1
            return original_read(**values)

        monkeypatch.setattr(store, "read_ring_window", counted_read)
        metadata, binary = service.handle_with_binary(
            _snapshot_request(client_id="ring-reader-guard")
        )

    assert metadata["ok"] is True
    assert reads == 1
    assert binary.startswith(b"{")
    assert binary != b"published-cache-path"


def test_persisted_ring_snapshot_uses_the_delta_cursor_owner_after_cache_advances(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        assert service._cache is not None
        ring_generation = service._cache.generation.cache_generation

        # Let the in-memory owner advance far enough that the persisted ring
        # cursor is outside the single retained delta link.
        for offset in (1.0, 2.0):
            wall_now[0] += offset
            dirty = frozenset({materializer.DirtyCell(1, math.floor(wall_now[0]))})
            service._build_once(store, False, dirty)
        assert service._cache is not None
        materialized_generation = service._cache.resolution_generations[1].cache_generation
        assert materialized_generation > ring_generation

        metadata, binary = service.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-delta-owner")
        )
        snapshot = json.loads(binary)
        delta_metadata, delta_binary = service.handle_with_binary(_delta_request(
            client_id="ring-delta-owner",
            after_cache_generation=snapshot["cache_generation"],
        ))

        # The snapshot request records demand. Its next publication must bridge
        # directly from the exact cursor that was served, even though several
        # undemanded materializer generations advanced in between.
        wall_now[0] += 1.0
        dirty = frozenset({materializer.DirtyCell(1, math.floor(wall_now[0]))})
        service._build_once(store, False, dirty)
        monotonic_now[0] += service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        advanced_metadata, advanced_binary = service.handle_with_binary(_delta_request(
            client_id="ring-delta-owner",
            after_cache_generation=snapshot["cache_generation"],
        ))
        advanced = json.loads(advanced_binary)

    assert metadata["cache_generation"] == snapshot["cache_generation"] == ring_generation
    assert delta_metadata["not_modified"] is True
    assert delta_metadata["cache_generation"] == ring_generation
    assert delta_binary == b""
    assert advanced_metadata["base_cache_generation"] == ring_generation
    assert advanced["base_cache_generation"] == ring_generation
    assert advanced["cache_generation"] > materialized_generation


@pytest.mark.parametrize("requested_resolution", [resolution.AUTO, 1])
def test_persisted_ring_snapshot_advances_at_warm_materializer_cadence_before_ring_flush(
    tmp_path: Path,
    requested_resolution: int | str,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        assert service._ring_publications == 1

        metadata, binary = service.handle_with_binary(_snapshot_request(
            requested_resolution=requested_resolution,
            client_id=f"warm-cadence-{requested_resolution}",
        ))
        snapshot = protocol.validate_snapshot(json.loads(binary))
        cursor = snapshot["cache_generation"]
        revision = 0
        delivered = []

        for _offset in range(3):
            wall_now[0] += 1.0
            dirty = frozenset({materializer.DirtyCell(1, math.floor(wall_now[0]))})
            service._build_once(store, False, dirty)
            assert service._flush_ring_if_due() is None
            delta_metadata, delta_binary = service.handle_with_binary(_delta_request(
                client_id=f"warm-cadence-{requested_resolution}",
                after_cache_generation=cursor,
                after_revision=revision,
            ))
            delta = protocol.validate_delta(json.loads(delta_binary))
            delivered.append(delta)
            assert delta_metadata["base_cache_generation"] == cursor
            assert delta["base_cache_generation"] == cursor
            cursor = delta["cache_generation"]
            revision = delta["revision"]

    assert metadata["cache_generation"] == snapshot["cache_generation"]
    assert [item["base_cache_generation"] for item in delivered] == [
        snapshot["cache_generation"],
        delivered[0]["cache_generation"],
        delivered[1]["cache_generation"],
    ]
    assert [item["revision"] for item in delivered] == [1, 2, 3]
    assert len({item["cache_generation"] for item in delivered}) == 3
    assert service._delta_repairs == 0
    assert service._ring_publications == 1


def test_live_cursor_survives_contradicted_ring_handoff_beyond_delta_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None

        _metadata, binary = service.handle_with_binary(_snapshot_request(
            requested_resolution=resolution.AUTO,
            client_id="continuous-sse-cursor",
        ))
        snapshot = protocol.validate_snapshot(json.loads(binary))
        cursor = snapshot["cache_generation"]
        revision_number = 0

        for delivery in range(1, 12):
            wall_now[0] += 1.0
            dirty = frozenset({materializer.DirtyCell(1, math.floor(wall_now[0]))})
            service._build_once(store, False, dirty)
            assert service._cache is not None

            if delivery == 6:
                monkeypatch.setattr(
                    service,
                    "_read_ring_snapshot",
                    lambda *_args, **_kwargs: service_module.RingSnapshotRead(
                        None, "ring_contradicted",
                    ),
                )
                service._publish_ring_views(
                    store,
                    service._cache.generation,
                    frozenset({1}),
                )
                monkeypatch.undo()

            delta_metadata, delta_binary = service.handle_with_binary(_delta_request(
                client_id="continuous-sse-cursor",
                after_cache_generation=cursor,
                after_revision=revision_number,
            ))
            delta = protocol.validate_delta(json.loads(delta_binary))
            assert delta_metadata["base_cache_generation"] == cursor
            cursor = delta["cache_generation"]
            revision_number = delta["revision"]

    assert revision_number == 11
    assert service._delta_repairs == 0


def test_public_delta_keeps_the_exact_served_base_across_same_cursor_ring_republication(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_009.9]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        _metadata, warm_binary = service.handle_with_binary(_snapshot_request(
            range_seconds=900,
            requested_resolution=10,
            client_id="same-cursor-served-base",
        ))
        warm = protocol.validate_snapshot(json.loads(warm_binary))

        wall_now[0] += 0.2
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        state = service._ring_views[(900, 10, None)]
        assert state.snapshot is not None
        retained = protocol.validate_snapshot(json.loads(state.snapshot.binary))
        assert retained["cache_generation"] == warm["cache_generation"]
        assert retained["buckets"] == warm["buckets"]
        assert state.persisted is True

        accepted, _binary = service.handle_with_binary(_cpu_append_request(wall_now[0]))
        assert accepted["accepted"] == 1
        work = service._take_work()
        assert work is not None
        service._build_once(store, *work)
        delta_metadata, delta_binary = service.handle_with_binary(_delta_request(
            range_seconds=900,
            resolution_seconds=10,
            client_id="same-cursor-served-base",
            after_cache_generation=warm["cache_generation"],
        ))
        delta = protocol.validate_delta(json.loads(delta_binary))
        current_state = service._ring_views[(900, 10, None)]
        assert current_state.snapshot is not None
        current = protocol.validate_snapshot(json.loads(current_state.snapshot.binary))

    assert delta_metadata["base_cache_generation"] == warm["cache_generation"]
    applied = _apply_delta(warm, delta)
    assert [bucket["start"] for bucket in applied["buckets"] if bucket["open"]] == [
        current["window_end"] - current["resolution_seconds"]
    ]
    assert applied == {
        "source_generation": current["source_generation"],
        "cache_generation": current["cache_generation"],
        "buckets": current["buckets"],
        "no_data": current["no_data"],
        "cost_report": current["cost_report"],
    }


@pytest.mark.parametrize(
    ("range_seconds", "requested_resolution", "resolution_seconds"),
    (
        (300, resolution.AUTO, 1),
        (300, 1, 1),
        (900, 10, 10),
        (3_600, 60, 60),
        (86_400, 300, 300),
    ),
    ids=("AUTO", "1", "10", "60", "300"),
)
def test_public_owner_composes_retained_cadence_edges_and_repairs_only_overflow(
    tmp_path: Path,
    range_seconds: int,
    requested_resolution: int | str,
    resolution_seconds: int,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        initial._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None

        service_monotonic = [0.0]
        service = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            monotonic=lambda: service_monotonic[0],
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        service.writer = store
        client_id = f"ring-retained-chain-{requested_resolution}"
        _cold_metadata, cold_binary = service.handle_with_binary(_snapshot_request(
            range_seconds=range_seconds,
            requested_resolution=requested_resolution,
            client_id=client_id,
        ))
        cold = protocol.validate_snapshot(json.loads(cold_binary))
        cadence = resolution.live_cadence_seconds(resolution_seconds)
        expected_bound = service_module.DELTA_RING_ENTRY_BOUNDS[resolution_seconds]
        for _index in range(expected_bound):
            wall_now[0] += cadence
            service._build_once(store, True, frozenset())

        service_monotonic[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None

        key = (range_seconds, resolution_seconds, None)
        state = service._ring_views[key]
        assert state.persisted is True
        assert len(state.deltas) == expected_bound
        assert key not in service._delta_entries
        retained_cursor = int(state.deltas[0].metadata["cache_generation"])
        current_generation = int(state.snapshot.metadata["cache_generation"])
        current_snapshot = protocol.validate_snapshot(json.loads(state.snapshot.binary))
        composed_metadata, composed_binary = service.handle_with_binary(_delta_request(
            range_seconds=range_seconds,
            resolution_seconds=resolution_seconds,
            client_id=client_id,
            after_cache_generation=cold["cache_generation"],
        ))
        composed = protocol.validate_delta(json.loads(composed_binary))

        wall_now[0] += cadence
        service._build_once(store, True, frozenset())
        overflow_state = service._ring_views[key]
        repair_metadata, repair_binary = service.handle_with_binary(_delta_request(
            range_seconds=range_seconds,
            resolution_seconds=resolution_seconds,
            client_id=client_id,
            after_cache_generation=cold["cache_generation"],
        ))
        retained_metadata, retained_binary = service.handle_with_binary(_delta_request(
            range_seconds=range_seconds,
            resolution_seconds=resolution_seconds,
            client_id=client_id,
            after_cache_generation=retained_cursor,
            after_revision=1,
        ))
        retained = protocol.validate_delta(json.loads(retained_binary))

    assert composed_metadata["base_cache_generation"] == cold["cache_generation"]
    assert composed["cache_generation"] == current_generation
    assert _apply_delta(cold, composed) == {
        "source_generation": current_snapshot["source_generation"],
        "cache_generation": current_snapshot["cache_generation"],
        "buckets": current_snapshot["buckets"],
        "no_data": current_snapshot["no_data"],
        "cost_report": current_snapshot["cost_report"],
    }
    assert repair_metadata["status"] == "repair_required"
    assert repair_binary == b""
    assert retained_metadata["base_cache_generation"] == retained_cursor
    assert retained["cache_generation"] == int(
        overflow_state.snapshot.metadata["cache_generation"]
    )
    assert len(overflow_state.deltas) == expected_bound
    assert service._delta_repairs == 1


def test_public_owner_retains_delayed_poll_across_phase_offset_warm_and_ring_edges(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    initial_monotonic = [0.0]
    service_monotonic = [0.0]
    wall_now = [1_800_000_000.0]
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: initial_monotonic[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        initial._build_once(store, True, frozenset())
        initial_monotonic[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None

        service = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            monotonic=lambda: service_monotonic[0],
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        service.writer = store
        _metadata, cold_binary = service.handle_with_binary(_snapshot_request(
            range_seconds=900,
            requested_resolution=10,
            client_id="phase-offset-delayed-poll",
        ))
        cold = protocol.validate_snapshot(json.loads(cold_binary))

        wall_now[0] += 10.1
        service._build_once(store, True, frozenset())
        wall_now[0] += 0.4
        service._build_once(store, True, frozenset())
        service_monotonic[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        wall_now[0] += 9.6
        service._build_once(store, True, frozenset())

        key = (900, 10, None)
        state = service._ring_views[key]
        current = protocol.validate_snapshot(json.loads(state.snapshot.binary))
        delta_metadata, delta_binary = service.handle_with_binary(_delta_request(
            range_seconds=900,
            resolution_seconds=10,
            client_id="phase-offset-delayed-poll",
            after_cache_generation=cold["cache_generation"],
        ))
        delta = protocol.validate_delta(json.loads(delta_binary))

    assert delta_metadata["base_cache_generation"] == cold["cache_generation"]
    assert delta["cache_generation"] == current["cache_generation"]
    assert _apply_delta(cold, delta) == {
        "source_generation": current["source_generation"],
        "cache_generation": current["cache_generation"],
        "buckets": current["buckets"],
        "no_data": current["no_data"],
        "cost_report": current["cost_report"],
    }
    assert service._delta_repairs == 0


@pytest.mark.parametrize(
    ("range_seconds", "resolution_seconds"),
    ((3_600, 60), (86_400, 300)),
    ids=("60", "300"),
)
def test_public_owner_delivers_two_full_poll_intervals_across_ten_second_flushes(
    tmp_path: Path,
    range_seconds: int,
    resolution_seconds: int,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    client_id = f"two-poll-intervals-{resolution_seconds}"
    key = (range_seconds, resolution_seconds, None)
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None

        _metadata, binary = service.handle_with_binary(_snapshot_request(
            range_seconds=range_seconds,
            requested_resolution=resolution_seconds,
            client_id=client_id,
        ))
        client_snapshot = protocol.validate_snapshot(json.loads(binary))
        cursor = int(client_snapshot["cache_generation"])
        revision_number = 0
        expected_bound = service_module.DELTA_RING_ENTRY_BOUNDS[resolution_seconds]
        flushes_per_poll = (
            resolution.live_cadence_seconds(resolution_seconds)
            // int(service_module.RING_FLUSH_SECONDS)
        )
        assert expected_bound == 9
        assert flushes_per_poll == 6

        for cycle in (1, 2):
            for _flush in range(flushes_per_poll):
                wall_now[0] += service_module.RING_FLUSH_SECONDS
                service._build_once(store, True, frozenset())
                monotonic_now[0] += service_module.RING_FLUSH_SECONDS
                assert service._flush_ring_if_due() is not None
                assert key not in service._delta_entries

            state = service._ring_views[key]
            assert state.persisted is (resolution_seconds == 60)
            current_snapshot = protocol.validate_snapshot(json.loads(state.snapshot.binary))
            delta_metadata, delta_binary = service.handle_with_binary(_delta_request(
                range_seconds=range_seconds,
                resolution_seconds=resolution_seconds,
                client_id=client_id,
                after_cache_generation=cursor,
                after_revision=revision_number,
            ))
            delta = protocol.validate_delta(json.loads(delta_binary))
            applied = _apply_delta(client_snapshot, delta)

            assert delta_metadata["base_cache_generation"] == cursor
            assert delta["base_cache_generation"] == cursor
            assert delta_metadata["cache_generation"] == current_snapshot["cache_generation"]
            assert delta["cache_generation"] == current_snapshot["cache_generation"]
            assert applied == {
                "source_generation": current_snapshot["source_generation"],
                "cache_generation": current_snapshot["cache_generation"],
                "buckets": current_snapshot["buckets"],
                "no_data": current_snapshot["no_data"],
                "cost_report": current_snapshot["cost_report"],
            }
            assert 0 < len(state.deltas) <= expected_bound
            assert key not in service._delta_entries
            assert service._delta_repairs == 0
            cursor = int(delta["cache_generation"])
            revision_number = int(delta["revision"])
            client_snapshot = {**current_snapshot, **applied}


def test_restart_ring_cursor_bridges_once_to_the_warm_delta_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        initial._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None

        restarted = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        cold_metadata, cold_binary = restarted.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-restart-owner")
        )
        cold_snapshot = json.loads(cold_binary)
        assert cold_metadata["cache_generation"] == cold_snapshot["cache_generation"]

        wall_now[0] += 20.0
        restarted._build_once(store, True, frozenset())
        assert restarted._cache is not None
        current_generation = restarted._cache.resolution_generations[1].cache_generation
        assert current_generation > cold_snapshot["cache_generation"]

        bridge_metadata, bridge_binary = restarted.handle_with_binary(_delta_request(
            client_id="ring-restart-owner",
            after_cache_generation=cold_snapshot["cache_generation"],
        ))
        bridge = protocol.validate_delta(json.loads(bridge_binary))
        repaired_metadata, repaired_binary = restarted.handle_with_binary({
            **_snapshot_request(
                requested_resolution=1,
                client_id="ring-restart-owner",
            ),
            "since_generation": cold_snapshot["cache_generation"],
        })
        repaired_snapshot = json.loads(repaired_binary)
        current, current_binary = restarted.handle_with_binary(_delta_request(
            client_id="ring-restart-owner",
            after_cache_generation=repaired_snapshot["cache_generation"],
        ))

    assert bridge_metadata["base_cache_generation"] == cold_snapshot["cache_generation"]
    assert bridge["base_cache_generation"] == cold_snapshot["cache_generation"]
    assert bridge["cache_generation"] == current_generation
    assert restarted._delta_repairs == 0
    assert repaired_metadata["cache_generation"] == current_generation
    assert repaired_snapshot["cache_generation"] == current_generation
    assert current["not_modified"] is True
    assert current["cache_generation"] == current_generation
    assert current_binary == b""


@pytest.mark.parametrize(
    ("range_seconds", "requested_resolution", "resolution_seconds"),
    (
        (300, resolution.AUTO, 1),
        (300, 1, 1),
        (900, 10, 10),
        (3_600, 60, 60),
        (86_400, 300, 300),
    ),
    ids=("AUTO", "1", "10", "60", "300"),
)
def test_restart_cold_snapshot_racing_first_warm_publish_bridges_served_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    range_seconds: int,
    requested_resolution: int | str,
    resolution_seconds: int,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        initial._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None

        restarted = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        owner_resolved = threading.Barrier(2)
        warm_published = threading.Barrier(2)
        owner_calls = 0
        original_owner = restarted._published_snapshot_owner

        def gated_owner(*args, **kwargs):
            nonlocal owner_calls
            owner = original_owner(*args, **kwargs)
            owner_calls += 1
            if owner_calls == 2:
                assert owner.cache_present is False
                owner_resolved.wait(timeout=5)
                warm_published.wait(timeout=5)
            return owner

        monkeypatch.setattr(restarted, "_published_snapshot_owner", gated_owner)
        client_id = f"ring-restart-race-{requested_resolution}"

        def publish_first_warm_cache() -> int:
            owner_resolved.wait(timeout=5)
            try:
                wall_now[0] += 20.0
                with storage.Store.open(database) as warm_reader:
                    restarted._build_once(warm_reader, True, frozenset())
                assert restarted._cache is not None
                return restarted._cache.resolution_generations[
                    resolution_seconds
                ].cache_generation
            finally:
                warm_published.wait(timeout=5)

        with ThreadPoolExecutor(max_workers=1) as executor:
            warm_future = executor.submit(publish_first_warm_cache)
            cold_metadata, cold_binary = restarted.handle_with_binary(
                _snapshot_request(
                    range_seconds=range_seconds,
                    requested_resolution=requested_resolution,
                    client_id=client_id,
                )
            )
            current_generation = warm_future.result(timeout=5)

        assert cold_binary, cold_metadata
        cold_snapshot = protocol.validate_snapshot(json.loads(cold_binary))
        assert cold_metadata["cache_generation"] == cold_snapshot["cache_generation"]
        assert cold_snapshot["cache_generation"] < current_generation
        bridge_metadata, bridge_binary = restarted.handle_with_binary(_delta_request(
            range_seconds=range_seconds,
            resolution_seconds=resolution_seconds,
            client_id=client_id,
            after_cache_generation=cold_snapshot["cache_generation"],
        ))
        assert bridge_metadata.get("status") != "repair_required", bridge_metadata
        bridge = protocol.validate_delta(json.loads(bridge_binary))

    assert owner_calls == 2
    assert bridge_metadata["base_cache_generation"] == cold_snapshot["cache_generation"]
    assert bridge["base_cache_generation"] == cold_snapshot["cache_generation"]
    assert bridge["cache_generation"] == current_generation
    assert restarted._delta_repairs == 0


def test_restart_serves_persisted_snapshot_before_materializer_warm(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = 1_800_000_000.0
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now,
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        accepted, binary = initial.handle_with_binary(
            _real_ingest_request(wall_now - 0.25)
        )
        assert accepted["accepted"] == 2
        assert binary == b""
        initial._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None
        assert initial._cache is not None
        expected = json.loads(initial._cache.entries[(300, 1, None)].binary)

        restarted = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            clock=lambda: wall_now,
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        assert restarted._cache is None
        metadata, binary = restarted.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-cold-restart")
        )

    assert metadata["ok"] is True
    assert json.loads(binary) == expected


@pytest.mark.parametrize(
    ("range_seconds", "requested_resolution", "resolution_seconds"),
    (
        (300, resolution.AUTO, 1),
        (300, 1, 1),
        (900, 10, 10),
        (3_600, 60, 60),
        (86_400, 300, 300),
    ),
    ids=("AUTO", "1", "10", "60", "300"),
)
def test_restart_persisted_snapshot_is_current_until_first_warm_publication(
    tmp_path: Path,
    range_seconds: int,
    requested_resolution: int | str,
    resolution_seconds: int,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        initial._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None

        restarted = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        assert restarted._cache is None
        cold_metadata, cold_binary = restarted.handle_with_binary(_snapshot_request(
            range_seconds=range_seconds,
            requested_resolution=requested_resolution,
            client_id=f"ring-cold-current-{requested_resolution}",
        ))
        cold_snapshot = protocol.validate_snapshot(json.loads(cold_binary))

        current_metadata, current_binary = restarted.handle_with_binary(_delta_request(
            range_seconds=range_seconds,
            resolution_seconds=resolution_seconds,
            client_id=f"ring-cold-current-{requested_resolution}",
            after_cache_generation=cold_snapshot["cache_generation"],
        ))

        wall_now[0] += 20.0
        restarted._build_once(store, True, frozenset())
        warm_metadata, warm_binary = restarted.handle_with_binary(_delta_request(
            range_seconds=range_seconds,
            resolution_seconds=resolution_seconds,
            client_id=f"ring-cold-current-{requested_resolution}",
            after_cache_generation=cold_snapshot["cache_generation"],
        ))
        warm_delta = protocol.validate_delta(json.loads(warm_binary))

    assert cold_metadata["cache_generation"] == cold_snapshot["cache_generation"]
    assert current_metadata["not_modified"] is True
    assert current_metadata["cache_generation"] == cold_snapshot["cache_generation"]
    assert current_binary == b""
    assert warm_metadata["base_cache_generation"] == cold_snapshot["cache_generation"]
    assert warm_delta["base_cache_generation"] == cold_snapshot["cache_generation"]
    assert warm_delta["cache_generation"] > cold_snapshot["cache_generation"]
    assert restarted._delta_pending == 0
    assert restarted._delta_repairs == 0


def test_restart_read_closes_an_uncontradicted_precrash_open_bucket_without_losing_cost(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.5]
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        accepted, binary = initial.handle_with_binary(
            _real_ingest_request(wall_now[0] - 0.25)
        )
        assert accepted["accepted"] == 2
        assert binary == b""
        work = initial._take_work()
        assert work is not None
        initial._build_once(store, *work)
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None
        historical_open_start = math.floor(wall_now[0])
        before = store.read_ring_window(
            range_seconds=300,
            resolution_seconds=1,
            window_end=historical_open_start + 1,
        )
        persisted = service_module._decode_ring_bucket(next(
            row for row in before.rows if row.bucket_start == historical_open_start
        ))
        assert persisted.wire["open"] is True
        assert persisted.wire["series"]["usage_tokens"]["value"] == 12

        wall_now[0] += 120
        restarted = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        assert restarted._cache is None
        metadata, binary = restarted.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-preflush-gap")
        )

    assert metadata["ok"] is True
    snapshot = json.loads(binary)
    buckets = {bucket["start"]: bucket for bucket in snapshot["buckets"]}
    assert list(buckets) == list(range(snapshot["window_start"], snapshot["window_end"]))
    assert buckets[historical_open_start - 1]["series"] == {}
    assert buckets[historical_open_start]["open"] is False
    assert buckets[historical_open_start]["series"]["usage_tokens"]["value"] == 12
    assert snapshot["cost_report"]["total_tokens"] == 12
    assert buckets[snapshot["window_end"] - 1]["series"] == {}
    assert all(bucket["open"] is False for bucket in snapshot["buckets"])
    ring_gaps = [
        span
        for span in snapshot["no_data"]
        if span["reason"] == "incomplete_persisted_bucket"
    ]
    assert {
        (span["family"], span["start"], span["end"])
        for span in ring_gaps
    } == {
        (spec.name, historical_open_start, snapshot["window_end"])
        for spec in families.CURRENT_FAMILIES
    }
    assert snapshot["rightmost_open"] is False


def test_restart_ring_delta_bridges_the_exact_persisted_gap_snapshot(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        initial._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None

        wall_now[0] += 120.0
        restarted = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            monotonic=lambda: monotonic_now[0],
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        cold_metadata, cold_binary = restarted.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-restart-gap-delta")
        )
        cold_snapshot = protocol.validate_snapshot(json.loads(cold_binary))
        assert cold_metadata["cache_generation"] == cold_snapshot["cache_generation"]
        assert any(
            item["reason"] == "incomplete_persisted_bucket"
            for item in cold_snapshot["no_data"]
        )

        restarted._build_once(store, True, frozenset())
        monotonic_now[0] += service_module.RING_FLUSH_SECONDS
        assert restarted._flush_ring_if_due() is not None
        delta_metadata, delta_binary = restarted.handle_with_binary(_delta_request(
            client_id="ring-restart-gap-delta",
            after_cache_generation=cold_snapshot["cache_generation"],
        ))
        delta = protocol.validate_delta(json.loads(delta_binary))
        current_metadata, current_binary = restarted.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-restart-gap-delta")
        )
        current_snapshot = protocol.validate_snapshot(json.loads(current_binary))

    assert delta_metadata["base_cache_generation"] == cold_snapshot["cache_generation"]
    assert current_metadata["cache_generation"] == delta["cache_generation"]
    applied = _apply_delta(cold_snapshot, delta)
    assert applied == {
        "source_generation": current_snapshot["source_generation"],
        "cache_generation": current_snapshot["cache_generation"],
        "buckets": current_snapshot["buckets"],
        "no_data": current_snapshot["no_data"],
        "cost_report": current_snapshot["cost_report"],
    }


@pytest.mark.parametrize("initialized", [False, True])
def test_warm_snapshot_uses_published_cache_without_a_current_ring_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initialized: bool,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        clock=lambda: 1_800_000_000.0,
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        if initialized:
            store.initialize_ring_storage()
        assert service._cache is not None
        expected = service._cache.entries[(300, resolution.AUTO, None)]
        reads = 0
        original_read = store.read_ring_window

        def counted_read(**values: object) -> storage.RingWindow:
            nonlocal reads
            reads += 1
            return original_read(**values)

        monkeypatch.setattr(store, "read_ring_window", counted_read)
        metadata, binary = service.handle_with_binary(
            _snapshot_request(client_id=f"unfilled-{initialized}")
        )

    assert reads == 0
    assert metadata == expected.metadata
    assert binary == expected.binary


@pytest.mark.parametrize(
    ("range_seconds", "requested_resolution", "retained_resolution", "advanced_resolution"),
    (
        (300, 1, 1, 10),
        (300, 10, 10, 1),
        (900, 60, 60, 10),
        (1_800, resolution.AUTO, 60, 10),
        (3_600, 300, 300, 60),
        (3_600, resolution.AUTO, 300, 60),
    ),
)
def test_snapshot_keeps_the_requested_resolution_cursor_when_another_resolution_advances(
    tmp_path: Path,
    range_seconds: int,
    requested_resolution: int | str,
    retained_resolution: int,
    advanced_resolution: int,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = 1_800_000_000.0
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now,
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        assert service._cache is not None
        retained_layer = service._cache.generation.layer(retained_resolution)
        retained_before = store.read_ring_window(
            range_seconds=range_seconds,
            resolution_seconds=retained_resolution,
            window_end=retained_layer.end,
        )
        expected_cursor = service._ring_published_cursors[retained_resolution]
        advanced_layer = service._cache.generation.layer(advanced_resolution)
        advanced_window = store.read_ring_window(
            range_seconds=next(
                range_value
                for range_value in resolution.RANGE_SECONDS
                if resolution.is_supported(range_value, advanced_resolution)
            ),
            resolution_seconds=advanced_resolution,
            window_end=advanced_layer.end,
        )
        carrier = advanced_window.rows[-1]
        publication = store.publish_ring_buckets(
            buckets=(storage.RingBucketWrite(
                resolution_seconds=carrier.resolution_seconds,
                bucket_start=carrier.bucket_start,
                bucket_json=carrier.bucket_json,
                complete=carrier.complete,
            ),),
            source_generation=advanced_window.source_generation,
            published_at=wall_now,
        )
        assert publication.ring_generation > carrier.ring_generation
        retained_after = store.read_ring_window(
            range_seconds=range_seconds,
            resolution_seconds=retained_resolution,
            window_end=retained_layer.end,
        )
        metadata, binary = service.handle_with_binary(
            _snapshot_request(
                range_seconds=range_seconds,
                requested_resolution=requested_resolution,
                client_id="ring-resolution-local-cursor",
            )
        )
        snapshot = protocol.validate_snapshot(json.loads(binary))
        delta_metadata, delta_binary = service.handle_with_binary(
            _delta_request(
                range_seconds=range_seconds,
                resolution_seconds=retained_resolution,
                client_id="ring-resolution-local-cursor",
                after_cache_generation=snapshot["cache_generation"],
            )
        )

    assert (
        retained_after.ring_generation,
        retained_after.source_generation,
        retained_after.published_at,
    ) == (
        retained_before.ring_generation,
        retained_before.source_generation,
        retained_before.published_at,
    )
    assert metadata["source_generation"] == retained_before.source_generation
    assert service._ring_published_cursors[retained_resolution] == expected_cursor
    state = service._ring_views[(range_seconds, retained_resolution, None)]
    assert state.persisted is True
    assert state.snapshot is not None
    assert delta_metadata["not_modified"] is True
    assert delta_binary == b""


def test_unpublished_resolution_keeps_a_zero_cursor_when_another_resolution_publishes(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database) as store:
        store.initialize_ring_storage()
        _publish(
            store,
            _bucket(1, 1_800_000_000, value=1),
            source_generation=1,
            published_at=1_800_000_001.0,
        )
        untouched = store.read_ring_window(
            range_seconds=3_600,
            resolution_seconds=60,
            window_end=1_800_000_060,
        )

    assert untouched.rows == ()
    assert untouched.source_generation == 0
    assert untouched.ring_generation == 0
    assert untouched.published_at == 0.0


def test_seeded_slow_ring_view_cannot_fall_back_to_the_startup_zero_cache(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        assert service._cache is not None
        startup = protocol.validate_snapshot(json.loads(
            service._cache.entries[(3_600, 60, None)].binary
        ))
        assert startup["source_generation"] == 0
        assert startup["cost_report"]["total_tokens"] == 0

        accepted, binary = service.handle_with_binary(
            _real_ingest_request(wall_now[0] - 0.25)
        )
        assert accepted["accepted"] == 2
        assert binary == b""
        work = service._take_work()
        assert work is not None
        service._build_once(store, *work)
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        first_publication = service._flush_ring_if_due()
        assert first_publication is not None
        seeded_cursor = service._ring_published_cursors[60]

        fast_request = _cpu_append_request(wall_now[0] + 9.75)
        fast_request["observations"][0]["event_id"] = "cpu-fast-generation"
        wall_now[0] += 10.0
        accepted, binary = service.handle_with_binary(fast_request)
        assert accepted["accepted"] == 1
        assert binary == b""
        work = service._take_work()
        assert work is not None
        service._build_once(store, *work)
        assert service._cache is not None
        fast_cell = materializer.DirtyCell(
            10,
            math.floor(wall_now[0] / 10) * 10,
        )
        fast_writes = service._ring_writes(
            service._cache.generation,
            frozenset({fast_cell}),
        )
        assert len(fast_writes) == 1
        second_publication = store.publish_ring_buckets(
            buckets=fast_writes,
            source_generation=service._cache.generation.source_generation,
            published_at=wall_now[0],
        )
        assert second_publication.ring_generation > first_publication.ring_generation
        assert service._ring_published_cursors[60] == seeded_cursor

        explicit_pending, explicit_pending_binary = service.handle_with_binary(
            _snapshot_request(
                range_seconds=3_600,
                requested_resolution=60,
                client_id="seeded-explicit-slow-view",
            )
        )
        auto_pending, auto_pending_binary = service.handle_with_binary(
            _snapshot_request(
                range_seconds=1_800,
                requested_resolution=resolution.AUTO,
                client_id="seeded-auto-slow-view",
            )
        )
        assert explicit_pending["status"] == "pending"
        assert explicit_pending_binary == b""
        assert auto_pending["status"] == "pending"
        assert auto_pending_binary == b""
        work = service._take_work()
        assert work is not None
        service._build_once(store, *work)

        explicit_metadata, explicit_binary = service.handle_with_binary(
            _snapshot_request(
                range_seconds=3_600,
                requested_resolution=60,
                client_id="seeded-explicit-slow-view",
            )
        )
        explicit = protocol.validate_snapshot(json.loads(explicit_binary))
        auto_metadata, auto_binary = service.handle_with_binary(
            _snapshot_request(
                range_seconds=1_800,
                requested_resolution=resolution.AUTO,
                client_id="seeded-auto-slow-view",
            )
        )
        auto = protocol.validate_snapshot(json.loads(auto_binary))

    assert explicit_metadata["source_generation"] > 0
    assert explicit["cost_report"]["total_tokens"] == 12
    assert auto_metadata["source_generation"] > 0
    assert auto["resolution_seconds"] == 60
    assert auto["cost_report"]["total_tokens"] == 12
    assert service._ring_published_cursors[60] == seeded_cursor


def test_populated_ring_serves_a_dense_explicit_gap_snapshot_after_its_horizon(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None

        wall_now[0] += 2 * resolution.RING_CAPACITIES[1]
        restarted = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        assert restarted._cache is None
        metadata, binary = restarted.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-empty-window")
        )

    assert metadata["ok"] is True
    assert binary
    snapshot = json.loads(binary)
    assert [bucket["start"] for bucket in snapshot["buckets"]] == list(
        range(snapshot["window_start"], snapshot["window_end"])
    )
    assert all(
        bucket["series"] == {}
        and bucket["source"] == {
            "count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
        }
        and bucket["open"] is False
        for bucket in snapshot["buckets"]
    )
    assert {
        (span["family"], span["start"], span["end"])
        for span in snapshot["no_data"]
        if span["reason"] == "incomplete_persisted_bucket"
    } == {
        (spec.name, snapshot["window_start"], snapshot["window_end"])
        for spec in families.CURRENT_FAMILIES
    }
    assert snapshot["rightmost_open"] is False


def test_snapshot_reconstructs_cost_report_from_persisted_bucket_details(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = 1_800_000_000.0
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now,
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        accepted, binary = service.handle_with_binary(
            _real_ingest_request(wall_now - 0.25)
        )
        assert accepted["accepted"] == 2
        assert binary == b""
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        assert service._cache is not None
        cache_key = (300, 1, None)
        cached = json.loads(service._cache.entries[cache_key].binary)
        ring = store.read_ring_window(
            range_seconds=300,
            resolution_seconds=1,
            window_end=cached["window_end"],
        )
        assert all("view" not in json.loads(row.bucket_json) for row in ring.rows)
        entries = dict(service._cache.entries)
        entries[cache_key] = service_module.CacheEntry(
            service._cache.entries[cache_key].metadata,
            b"published-cache-path",
        )
        service._cache = service_module.PublishedCache(
            service._cache.generation,
            entries,
            service._cache.resolution_generations,
            service._cache.entry_generations,
        )
        metadata, binary = service.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-cost-rebuild")
        )

    assert metadata["ok"] is True
    assert binary != b"published-cache-path"
    assert json.loads(binary)["cost_report"] == cached["cost_report"]


def test_snapshot_distinguishes_zero_cold_and_one_lap_stale_slots(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = 1_800_000_000.0
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        database,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now,
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        service.writer = store
        service._build_once(store, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        assert service._cache is not None
        cached = json.loads(service._cache.entries[(300, 1, None)].binary)
        cold_start = cached["window_end"] - 3
        stale_start = cached["window_end"] - 2
        connection = store._connection()
        connection.execute(
            # `payload_version` is part of the empty-slot shape in schema 8, and the CHECK enforces
            # it: a cold slot is fully cold or it is not cold at all.
            "UPDATE aggregate_ring_slots SET bucket_start = NULL, bucket_json = NULL, "
            "complete = 0, source_generation = 0, ring_generation = 0, published_at = 0, "
            "payload_version = 0 "
            "WHERE resolution_seconds = 1 AND slot_index = ?",
            (cold_start % resolution.RING_CAPACITIES[1],),
        )
        connection.execute(
            "UPDATE aggregate_ring_slots SET bucket_start = ? "
            "WHERE resolution_seconds = 1 AND slot_index = ?",
            (
                stale_start - resolution.RING_CAPACITIES[1],
                stale_start % resolution.RING_CAPACITIES[1],
            ),
        )
        metadata, binary = service.handle_with_binary(
            _snapshot_request(requested_resolution=1, client_id="ring-slot-semantics")
        )

    assert metadata["ok"] is True
    snapshot = json.loads(binary)
    buckets = {bucket["start"]: bucket for bucket in snapshot["buckets"]}
    assert snapshot["window_end"] - 1 in buckets
    assert buckets[snapshot["window_end"] - 1]["series"] == {}
    assert buckets[cold_start]["series"] == {}
    assert buckets[stale_start]["series"] == {}
    ring_gaps = [
        span
        for span in snapshot["no_data"]
        if span["reason"] == "incomplete_persisted_bucket"
    ]
    assert {
        (span["family"], span["start"], span["end"])
        for span in ring_gaps
    } == {
        (spec.name, cold_start, stale_start + 1)
        for spec in families.CURRENT_FAMILIES
    }
    assert all(
        not (span["start"] <= snapshot["window_end"] - 1 < span["end"])
        for span in ring_gaps
    )


def test_restart_first_flush_preserves_downtime_as_explicit_gaps(
    tmp_path: Path,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    wall_now = [1_800_000_000.0]
    initial_monotonic = [0.0]
    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock",
        database,
        monotonic=lambda: initial_monotonic[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as store:
        initial.writer = store
        initial._build_once(store, True, frozenset())
        initial_monotonic[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None
        historical_open_start = math.floor(wall_now[0] / 300) * 300

        wall_now[0] += 2 * 60 * 60
        restarted_monotonic = [0.0]
        restarted = service_module.StatsCurrentService(
            tmp_path / "restarted.sock",
            database,
            monotonic=lambda: restarted_monotonic[0],
            clock=lambda: wall_now[0],
            randomizer=lambda: 0.0,
        )
        restarted.writer = store
        restarted._build_once(store, True, frozenset())
        restarted_monotonic[0] = service_module.RING_FLUSH_SECONDS
        publication = restarted._flush_ring_if_due()
        assert publication is not None

        window_end = math.floor(wall_now[0] / 60) * 60 + 60
        window = store.read_ring_window(
            range_seconds=900,
            resolution_seconds=60,
            window_end=window_end,
        )
        metadata, binary = restarted.handle_with_binary(_snapshot_request(
            range_seconds=86_400,
            requested_resolution=300,
            client_id="ring-restart-gap",
        ))

    assert [row.bucket_start for row in window.rows] == [window_end - 60]
    assert set(window.missing_bucket_starts) == set(range(window_end - 900, window_end - 60, 60))
    assert metadata["ok"] is True
    snapshot = json.loads(binary)
    buckets = {bucket["start"]: bucket for bucket in snapshot["buckets"]}
    assert len(buckets) == 86_400 // 300
    assert historical_open_start - 300 in buckets
    assert buckets[historical_open_start]["series"] == {}
    assert snapshot["window_end"] - 300 in buckets
    ring_gaps = [
        span
        for span in snapshot["no_data"]
        if span["reason"] == "incomplete_persisted_bucket"
    ]
    assert {
        span["family"]
        for span in ring_gaps
        if span["start"] <= historical_open_start < span["end"]
    } == {spec.name for spec in families.CURRENT_FAMILIES}


def test_materializer_worker_wakes_itself_at_the_ring_flush_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / storage.DATABASE_FILENAME
    published = threading.Event()
    original_publish = storage.Store.publish_ring_buckets

    def publish_and_signal(
        store: storage.Store,
        **values: object,
    ) -> storage.RingPublication:
        publication = original_publish(store, **values)
        published.set()
        return publication

    monkeypatch.setattr(service_module, "RING_FLUSH_SECONDS", 0.01)
    monkeypatch.setattr(storage.Store, "publish_ring_buckets", publish_and_signal)
    with storage.Store.open(database) as store:
        service = service_module.StatsCurrentService(
            tmp_path / "statsd.sock",
            database,
            clock=lambda: 1_800_000_000.0,
            randomizer=lambda: 0.0,
        )
        service.writer = store
        service.worker = threading.Thread(target=service._worker_loop, daemon=True)
        service.worker.start()
        service.work_event.set()
        try:
            assert service.cache_ready_event.wait(2)
            did_publish = published.wait(2)
            ring_status = service._status()["ring_writer"]
            assert did_publish, (
                ring_status["failure"],
                ring_status["next_in_seconds"],
                ring_status["waiting_for_source_generation"],
                ring_status["publications"],
            )
        finally:
            service.stop_event.set()
            service.work_event.set()
            service.worker.join(timeout=2)

        assert service.worker.is_alive() is False
        assert _ring_counts(store) == RING_CAPACITIES
        assert service._status()["ring_writer"]["publications"] == 1


def test_schema_v8_creation_leaves_v7_database_untouched(tmp_path: Path) -> None:
    """v8 is a SIDE-BY-SIDE format, so creating it must not touch the v7 file at all.

    `DATABASE_FILENAME` embeds the schema version, so a v8 build addresses `stats-v8.sqlite3` and an
    existing `stats-v7.sqlite3` is not opened, not written, and not even WAL-touched -- an opened
    SQLite file grows `-wal`/`-shm` sidecars, so their absence is the evidence that nothing looked
    at it. That is the whole rollback boundary: the v7 build keeps running against its own file.
    """
    assert storage.SCHEMA_VERSION == 9
    assert storage.DATABASE_FILENAME == "stats-v9.sqlite3"
    previous = tmp_path / "stats-v7.sqlite3"
    original = b"existing v7 database belongs to its running build"
    previous.write_bytes(original)

    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        assert _ring_counts(store) == RING_CAPACITIES

    assert previous.read_bytes() == original
    assert not Path(f"{previous}-wal").exists()
    assert not Path(f"{previous}-shm").exists()


def test_downtime_is_a_gap_and_is_not_synthesized_as_quiet_zero(tmp_path: Path) -> None:
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        assert _ring_counts(store) == RING_CAPACITIES
        _publish(store, _bucket(60, 0), source_generation=1, published_at=60.0)
        window = store.read_ring_window(
            range_seconds=900,
            resolution_seconds=60,
            window_end=7_260,
        )

    assert window.rows == ()
    assert set(window.missing_bucket_starts) == set(range(6_360, 7_260, 60))


# --- publication generation versus store generation ------------------------------------------
# `publish_ring_buckets` and `append_batch` commit in SEPARATE transactions: facts land with a
# bumped `schema_meta.source_generation`, and the ring follows up to RING_FLUSH_SECONDS later.
# Until now `read_ring_window` returned only the generation the ring was published FROM, so no
# consumer could tell a ring that is one flush behind from one that is answering for a store that
# no longer exists. Both readings were invisible in exactly the same way.


def _store_generation(store: storage.Store) -> int:
    return int(
        store._connection()
        .execute("SELECT source_generation FROM schema_meta WHERE singleton = 1")
        .fetchone()[0]
    )


def _set_store_generation(store: storage.Store, value: int) -> None:
    connection = store._connection()
    connection.execute(
        "UPDATE schema_meta SET source_generation = ? WHERE singleton = 1", (value,)
    )
    connection.commit()


def test_a_caught_up_ring_reports_no_publication_lag(tmp_path: Path) -> None:
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        _publish(store, _bucket(60, 7_140), source_generation=_store_generation(store), published_at=1_000.0)
        window = store.read_ring_window(range_seconds=3_600, resolution_seconds=60, window_end=7_200)

    assert window.source_generation == window.store_source_generation
    assert window.publication_lag == 0


def test_a_ring_behind_the_store_is_measured_and_still_served(tmp_path: Path) -> None:
    """The ORDINARY steady state, and it must not be refused.

    Publication coalesces on RING_FLUSH_SECONDS, so the store is routinely ahead. Refusing on any
    difference would disable the cold-cache ring path permanently and force every restart back
    through a whole-window materializer build -- the exact cost the ring exists to avoid.
    """
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        _publish(store, _bucket(60, 7_140), source_generation=_store_generation(store), published_at=1_000.0)
        _set_store_generation(store, _store_generation(store) + 7)
        window = store.read_ring_window(range_seconds=3_600, resolution_seconds=60, window_end=7_200)

    assert window.publication_lag == 7
    assert window.rows, "a lagging ring is still authoritative for the buckets it did publish"


def test_a_publication_ahead_of_the_store_is_measured_rather_than_assumed_impossible(tmp_path: Path) -> None:
    """A publication generation LEADING the store is not rejected here, and that is deliberate.

    In production `service._flush_ring_if_due` publishes `candidate.source_generation`, which comes
    from `snapshot.schema.source_generation` -- the store's own value -- so a leading publication
    would indeed mean the raw store moved backward. But `publish_ring_buckets` takes the generation
    as a CALLER-SUPPLIED argument and does not derive it, so the storage layer is not the owner of
    that invariant, and this suite's own fixtures publish decoupled literals against a fresh store.
    Enforcing it here would have forced eight pinned ring invariants to change to satisfy a rule
    this layer does not own. The pair is measured instead, so the service layer that DOES own the
    provenance can act on it once a durable cursor exists to say what the correct response is.
    """
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        _publish(store, _bucket(60, 7_140), source_generation=9, published_at=1_000.0)
        window = store.read_ring_window(range_seconds=3_600, resolution_seconds=60, window_end=7_200)

    assert window.source_generation == 9
    assert window.store_source_generation == 0
    # `publication_lag` clamps at zero rather than reporting a negative distance, so a caller that
    # only reads the lag cannot mistake this state for "behind".
    assert window.publication_lag == 0


def test_the_generation_pair_survives_a_crash_boundary_reopen(tmp_path: Path) -> None:
    """Facts durable, publication stale: the exact separate-transaction crash boundary.

    A crash between `append_batch`'s commit and the next `_flush_ring_if_due` leaves the store
    ahead of the ring. After reopen the pair must still be readable and still describe that
    distance, because a cold cache serves this ring before the materializer warms.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database) as store:
        store.initialize_ring_storage()
        _publish(store, _bucket(60, 7_140), source_generation=_store_generation(store), published_at=1_000.0)
        _set_store_generation(store, _store_generation(store) + 3)

    with storage.Store.open(database) as reopened:
        window = reopened.read_ring_window(range_seconds=3_600, resolution_seconds=60, window_end=7_200)

    assert window.publication_lag == 3
    assert window.store_source_generation == 3
    assert window.rows, "the published bucket survives the reopen"


def test_a_missing_schema_metadata_row_refuses_the_ring_read(tmp_path: Path) -> None:
    """Negative control for the pair: with no store generation there is nothing to compare against.

    Serving a ring whose counterpart cannot be read would reintroduce exactly the blind state this
    pair removes, so the read fails closed rather than defaulting the missing side to zero.
    """
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        store.initialize_ring_storage()
        _publish(store, _bucket(60, 7_140), source_generation=_store_generation(store), published_at=1_000.0)
        connection = store._connection()
        connection.execute("DELETE FROM schema_meta WHERE singleton = 1")
        connection.commit()

        with pytest.raises(storage.SchemaMismatchError) as raised:
            store.read_ring_window(range_seconds=3_600, resolution_seconds=60, window_end=7_200)

    assert "schema metadata row is missing" in str(raised.value)
