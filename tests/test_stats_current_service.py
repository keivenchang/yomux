# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused concurrency and cache contracts for the current stats service."""

import json
import math
import os
import threading
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from types import MappingProxyType

import pytest

from yolomux_lib.host_identity import current_host_identity
from yolomux_lib.stats_current import client as client_module
from yolomux_lib.stats_current import http as http_module
from yolomux_lib.stats_current import materializer, migration, pricing, protocol, revision, storage
from yolomux_lib.stats_current import resolution as stats_resolution
from yolomux_lib.stats_current import service as service_module

FENCE = {
    "protocol_version": storage.MIN_WRITER_PROTOCOL,
    "schema_generation": storage.SCHEMA_VERSION,
}


def current_view_count() -> int:
    return sum(
        1 + len(stats_resolution.explicit_resolutions(range_seconds))
        for range_seconds in stats_resolution.RANGE_SECONDS
    )


def fully_warm_status() -> dict[str, float | int]:
    count = current_view_count()
    return {"ready": count, "total": count, "percent": 100.0}


def dead_client_lease_record(pid: int) -> dict[str, object]:
    return current_host_identity().process_record_fields(pid=pid, start_identity="proc:1")


def cpu_record(event_id: str = "cpu-1", observed_at: float = 10.0) -> dict[str, object]:
    return {
        "event_id": event_id,
        "family": "cpu",
        "source_id": "host",
        "observed_at": observed_at,
        "epoch_id": "cpu:1",
        "owner_generation": 1,
        "payload": {"process_percent": 2, "system_percent": 4},
    }


def browser_record(client_id: str = "private-browser") -> dict[str, object]:
    return {
        "event_id": f"event:{client_id}",
        "family": "browser",
        "source_id": client_id,
        "observed_at": 10.0,
        "epoch_id": "browser:1",
        "owner_generation": 1,
        "payload": {"kind": "api", "latency_ms": 2},
    }


def usage_record(cache_role: str = "none") -> dict[str, object]:
    return {
        "event_id": "usage-1",
        "direction": "input",
        "modality": "text",
        "cache_role": cache_role,
        "unit": "tokens",
        "observed_at": 10.0,
        "payload": {
            "quantity": 12,
            "provider": "openai",
            "model": "gpt",
            "agent_id": "sol",
            "telemetry_complete": True,
        },
    }


def append_request(
    *,
    observations: list[dict[str, object]] | None = None,
    usage_atoms: list[dict[str, object]] | None = None,
    usage_tombstones: list[dict[str, object]] | None = None,
    coverage_epochs: list[dict[str, object]] | None = None,
    unavailable_spans: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        **FENCE,
        "action": "append",
        "observations": observations or [],
        "usage_atoms": usage_atoms or [],
        "usage_tombstones": usage_tombstones or [],
        "coverage_epochs": coverage_epochs or [],
        "unavailable_spans": unavailable_spans or [],
    }


def snapshot_request(since_generation: int | None = None) -> dict[str, object]:
    request = {
        **FENCE,
        "action": "snapshot",
        "range_seconds": 300,
        "resolution": "AUTO",
        "client_id": "browser-a",
    }
    if since_generation is not None:
        request["since_generation"] = since_generation
    return request


def delta_request(
    *, after_cache_generation: int, after_revision: int = 0,
) -> dict[str, object]:
    return {
        **FENCE,
        "action": "delta",
        "range_seconds": 300,
        "resolution_seconds": 1,
        "client_id": "browser-a",
        "after_cache_generation": after_cache_generation,
        "after_revision": after_revision,
    }


class FakeStore:
    def __init__(self):
        self.source_generation = 0
        self.reads = 0
        self.appends = 0
        self.closed = 0
        self.last_append = {}
        self.prunes = 0
        self.dirty_reads = []
        self.coverage_reads = []
        self.vacuums = []

    def append_batch(self, **values):
        self.appends += 1
        self.last_append = values
        count = sum(len(items) for items in values.values())
        self.source_generation += int(count > 0)
        return storage.AppendResult(
            self.source_generation,
            len(values.get("observations", ())),
            0,
            len(values.get("coverage_epochs", ())),
            0,
            len(values.get("usage_atoms", ())),
            0,
            len(values.get("unavailable_spans", ())),
            0,
            usage_tombstones_accepted=len(values.get("usage_tombstones", ())),
            accepted_observation_ids=tuple(
                item.event_id for item in values.get("observations", ())
            ),
            accepted_original_timestamps=tuple(
                item.observed_at
                for key in ("observations", "usage_atoms", "usage_tombstones")
                for item in values.get(key, ())
            ),
        )

    def latest_coverage_epoch(self, family, source_id, owner_generation, native_cadence_seconds):
        return next((
            item
            for item in self.last_append.get("coverage_epochs", ())
            if (item.family, item.source_id, item.owner_generation, item.native_cadence_seconds)
            == (family, source_id, owner_generation, native_cadence_seconds)
        ), None)

    def inline_coverage_source_ids(self, family, owner_generation):
        prefix = f"inline:{owner_generation}:{family}:"
        return tuple(sorted({
            item.source_id
            for item in self.last_append.get("coverage_epochs", ())
            if item.family == family
            and item.owner_generation == owner_generation
            and item.epoch_id.startswith(prefix)
        }))

    def recent_browser_profiles(self, _limit):
        return ()

    def browser_observation_status(self, _now):
        return {
            "retained_observations": 0,
            "retained_failures": 0,
            "confirmed_real_failures": 0,
            "probe_failures": 0,
            "unknown_failures": 0,
            "retained_errors": 0,
            "retained_unhandled_rejections": 0,
            "last_retained_observed_at": None,
            "last_retained_observed_age_seconds": None,
            "fingerprints": (),
            "classification_counts": {"open": 0, "fixed": 0, "live_verified": 0},
            "unprovable_states": ("fixed", "live_verified"),
        }

    def read_snapshot(self, *, dirty_intervals=None):
        self.reads += 1
        self.dirty_reads.append(dirty_intervals)
        return storage.StoreSnapshot(
            storage.SchemaMetadata(5, 23, 1, self.source_generation),
            tuple(self.last_append.get("observations", ())),
            tuple(self.last_append.get("coverage_epochs", ())),
            tuple(self.last_append.get("usage_atoms", ())),
            (),
            tuple(self.last_append.get("unavailable_spans", ())),
        )

    @contextmanager
    def pinned_snapshot(self, *, dirty_intervals=None, include_coverage=True):
        self.coverage_reads.append(include_coverage)
        yield lambda: self.read_snapshot(dirty_intervals=dirty_intervals)

    def read_ring_window(self, **_values):
        raise storage.StatsCurrentError("aggregate ring storage is not initialized")

    def prune(self, *, now):
        self.prunes += 1
        deleted = getattr(self, "prune_observations_deleted", 0)
        return storage.PruneResult(deleted, 0, 0, 0, self.source_generation, 0, 0)

    def last_vacuumed_at(self):
        return self.vacuums[-1] if self.vacuums else 0.0

    def vacuum(self, *, completed_at):
        self.vacuums.append(completed_at)
        return completed_at

    def close(self):
        self.closed += 1


def test_store_open_is_deferred_until_generic_runtime_owns_the_lock(tmp_path, monkeypatch):
    events = []
    store = FakeStore()

    def open_store(*args, **kwargs):
        events.append("open")
        return store

    def migrate_store(inputs, active_database, *, completed_at):
        events.append("migrate")
        return migration.MigrationReport(
            active_database, "", 0, 0, 0, 0, (), 0, False,
        )

    def runtime(**kwargs):
        events.append("lock")
        kwargs["on_start"]()
        events.append("started")
        kwargs["on_shutdown"]()
        return 0

    monkeypatch.setattr(service_module, "run_local_rpc_service", runtime)
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        store_opener=open_store,
        migration_runner=migrate_store,
        clock=lambda: 1_000.0,
        monotonic=lambda: 10.0,
        randomizer=lambda: 0.0,
    )
    assert events == []
    assert service.run() == 0
    assert events[0:3] == ["lock", "migrate", "open"]
    assert service._next_vacuum_at == 3_610.0


def test_startup_migrates_before_writer_open_preserves_legacy_and_reports_bounded_counts(
    tmp_path,
    monkeypatch,
):
    state = tmp_path / "state"
    state.mkdir()
    legacy = state / "tmux-AI-status.json"
    legacy.write_text(json.dumps({
        "stats_history": {
            "raw_buckets": [{
                "start": 100,
                "duration": 1,
                "cpu_total_percent": 5,
                "cpu_count": 1,
                "system_cpu_total_percent": 20,
                "system_cpu_count": 1,
            }],
            "rollup_buckets": [],
        },
    }), encoding="utf-8")
    statuses = []

    def runtime(**kwargs):
        kwargs["on_start"]()
        statuses.append(kwargs["handle"]({**FENCE, "action": "status"})[0])
        kwargs["on_shutdown"]()
        return 0

    monkeypatch.setattr(service_module, "run_local_rpc_service", runtime)
    database = state / storage.DATABASE_FILENAME
    first = service_module.StatsCurrentService(
        state / "services" / "statsd.sock", database, clock=lambda: 200.0,
    )
    assert first.run() == 0
    assert statuses[-1]["migration"] == {
        "state": "ready",
        "result": "activated",
        "failure": "",
        "seconds": statuses[-1]["migration"]["seconds"],
        "observations": 1,
        "coverage_epochs": 1,
        "usage_atoms": 0,
        "unavailable_spans": 0,
        "issues": 0,
        "issue_kinds": (),
        "issue_records": (),
        "skipped_history": False,
    }
    assert json.loads(legacy.read_text(encoding="utf-8")) == {}
    with storage.Store.open_reader(database) as reader:
        snapshot = reader.read_snapshot()
    assert [item.family for item in snapshot.observations] == ["cpu"]
    assert len(snapshot.migration_reconciliation) == 1

    second = service_module.StatsCurrentService(
        state / "services" / "statsd.sock", database, clock=lambda: 201.0,
    )
    assert second.run() == 0
    assert statuses[-1]["migration"]["state"] == "ready"
    assert statuses[-1]["migration"]["result"] == "existing"
    assert statuses[-1]["migration"]["observations"] == 1
    assert json.loads(legacy.read_text(encoding="utf-8")) == {}


def test_future_state_fence_stops_service_after_singleton_before_migration_or_database_open(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    legacy = state / migration.RETIRED_DATABASE_FILENAME
    legacy.write_bytes(b"legacy-must-not-be-read-or-changed")
    fence = state / storage.WRITER_FENCE_FILENAME
    fence.write_text(json.dumps({
        "application_id": storage.APPLICATION_ID,
        "database_filename": "stats-v6.sqlite3",
        "schema_version": storage.SCHEMA_VERSION + 1,
        "minimum_writer_protocol": storage.MIN_WRITER_PROTOCOL + 1,
        "minimum_writer_build": storage.MIN_WRITER_BUILD,
    }), encoding="utf-8")
    legacy_before = legacy.read_bytes()
    fence_before = fence.read_bytes()
    called = []

    def migration_runner(*args, **kwargs):
        called.append("migration")
        raise AssertionError("migration must not run past a future fence")

    def store_opener(*args, **kwargs):
        called.append("open")
        raise AssertionError("database must not open past a future fence")

    service = service_module.StatsCurrentService(
        state / "services" / "statsd.sock",
        state / storage.DATABASE_FILENAME,
        migration_runner=migration_runner,
        store_opener=store_opener,
    )
    with pytest.raises(storage.SchemaTooNewError):
        service.run()

    assert called == []
    assert service._status()["migration"]["state"] == "failed"
    assert service._status()["migration"]["failure"] == "SchemaTooNewError"
    assert legacy.read_bytes() == legacy_before
    assert fence.read_bytes() == fence_before
    assert not (state / storage.DATABASE_FILENAME).exists()


@pytest.mark.parametrize(
    "fence",
    [
        {"protocol_version": 23, "schema_generation": 6},
        {"protocol_version": 24, "schema_generation": 4},
        {"protocol_version": 25, "schema_generation": 6},
        {"protocol_version": "24", "schema_generation": 6},
    ],
)
def test_old_or_mismatched_protocol_is_terminal_before_dispatch_or_mutation(tmp_path, fence):
    store = FakeStore()
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    service.writer = store
    request = append_request(observations=[cpu_record()])
    request.update(fence)

    response, binary = service.handle_with_binary(request)

    assert response["status"] == "upgrade_required"
    assert response["required_protocol_version"] == 24
    assert response["required_schema_generation"] == storage.SCHEMA_VERSION
    assert binary == b""
    assert store.appends == 0
    assert service._status()["requests"]["rejected_old"] == 1


def test_append_normalizes_families_usage_private_ids_and_commits_one_batch(tmp_path):
    store = FakeStore()
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    service.writer = store
    raw_client = "private-browser"

    response, _binary = service.handle_with_binary(append_request(
        observations=[cpu_record(), browser_record(raw_client)],
        usage_atoms=[usage_record("read")],
    ))

    assert response.get("ok") is True, response
    assert response["accepted"] == 3
    assert response["source_generation"] == 1
    assert store.appends == 1
    browser = store.last_append["observations"][1]
    assert browser.source_id.startswith("browser:")
    assert browser.event_id.startswith("browser:")
    assert browser.epoch_id.startswith("browser:")
    assert browser.epoch_id != "browser:1"
    assert raw_client not in browser.source_id + browser.event_id
    atom = store.last_append["usage_atoms"][0]
    assert atom.cache_role == "read"
    assert atom.payload["quantity"] == 12.0

    bad, _binary = service.handle_with_binary(append_request(usage_atoms=[usage_record("cached")]))
    assert bad["status"] == "unsupported"
    assert store.appends == 1


def test_append_reports_agent_attribution_changes_without_double_counting(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        service.writer = store
        first = usage_record("read")
        moved = {**first, "payload": {**first["payload"], "agent_id": "terra"}}

        accepted, _binary = service.handle_with_binary(append_request(usage_atoms=[first]))
        duplicate, _binary = service.handle_with_binary(append_request(usage_atoms=[moved]))

        assert accepted["accepted"] == 1
        assert duplicate["accepted"] == 0
        assert duplicate["duplicates"] == 1
        assert duplicate["counts"]["usage_attribution_conflicts"] == 1
        assert service._status()["requests"]["usage_attribution_conflicts"] == 1
        assert len(store.read_snapshot().usage_atoms) == 1
    service.writer = None


def test_append_reports_and_quarantines_hard_usage_conflict_without_partial_store(tmp_path):
    now = [100.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: now[0],
    )
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        service.writer = store
        first = usage_record()
        conflict = {**first, "observed_at": 11.0}
        clean = {**first, "event_id": "usage-clean", "observed_at": 12.0}

        accepted, _binary = service.handle_with_binary(
            append_request(usage_atoms=[first])
        )
        rejected, _binary = service.handle_with_binary(
            append_request(usage_atoms=[clean, conflict])
        )

        assert accepted["ok"] is True
        assert rejected["ok"] is False
        assert rejected["status"] == storage.USAGE_IDENTITY_CONFLICT_STATUS
        assert set(rejected["conflict"]) == {
            "event_id", "identity_hash", "first_payload_hash",
            "attempted_payload_hash",
        }
        assert rejected["conflict"]["event_id"] == "usage-1"
        assert all(len(rejected["conflict"][key]) == 64 for key in (
            "identity_hash", "first_payload_hash", "attempted_payload_hash",
        ))
        assert [item.event_id for item in store.read_snapshot().usage_atoms] == [
            "usage-1"
        ]

        now[0] = 105.0
        clean_result, _binary = service.handle_with_binary(
            append_request(usage_atoms=[clean])
        )
        status = service._status()["usage"]

        assert clean_result["ok"] is True
        assert status["accepted_atoms"] == 2
        assert status["last_accepted_at"] == 105.0
        assert status["last_accepted_age_seconds"] == 0.0
        assert status["quarantined_conflict_count"] == 1
        assert status["quarantined_conflict_attempts"] == 1
        assert len(status["quarantined"]) == 1
        assert not any(
            key in status["quarantined"][0]
            for key in ("payload", "quantity", "model", "source_file")
        )
    service.writer = None


def test_usage_conflict_diagnostics_are_bounded_and_deduplicated(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    for index in range(service_module.MAX_USAGE_CONFLICTS + 1):
        digest = f"{index:064x}"
        service._usage_identity_conflict_response(
            storage.UsageAtomIdentityConflict(
                event_id=f"event-{index}",
                identity_hash=digest,
                first_payload_hash="a" * 64,
                attempted_payload_hash="b" * 64,
            )
        )
    newest = storage.UsageAtomIdentityConflict(
        event_id=f"event-{service_module.MAX_USAGE_CONFLICTS}",
        identity_hash=f"{service_module.MAX_USAGE_CONFLICTS:064x}",
        first_payload_hash="a" * 64,
        attempted_payload_hash="c" * 64,
    )
    service._usage_identity_conflict_response(newest)

    usage = service._status()["usage"]

    assert usage["quarantined_conflict_count"] == service_module.MAX_USAGE_CONFLICTS
    assert usage["quarantined_conflict_attempts"] == service_module.MAX_USAGE_CONFLICTS + 2
    assert "event-0" not in {item["event_id"] for item in usage["quarantined"]}
    latest = next(
        item for item in usage["quarantined"]
        if item["event_id"] == f"event-{service_module.MAX_USAGE_CONFLICTS}"
    )
    assert latest["attempts"] == 2
    assert latest["attempted_payload_hash"] == "c" * 64


def test_fork_history_tombstone_deletes_exact_atom_and_dirties_its_old_cells(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    legacy = usage_record()
    legacy["event_id"] = "codex:child-thread:3"
    legacy["observed_at"] = 99.5
    legacy["payload"] = {
        **legacy["payload"],
        "model": "unknown",
        "thread_id": "child-thread",
        "execution_source": "codex",
        "pricing_profile": "default",
    }
    tombstone = {
        "event_id": legacy["event_id"],
        "direction": legacy["direction"],
        "modality": legacy["modality"],
        "cache_role": legacy["cache_role"],
        "unit": legacy["unit"],
        "observed_at": legacy["observed_at"],
        "quantity": legacy["payload"]["quantity"],
        "provider": "openai",
        "model": "unknown",
        "thread_id": "child-thread",
    }
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        service.writer = store
        accepted, _binary = service.handle_with_binary(
            append_request(usage_atoms=[legacy]),
        )
        service._pending_dirty.clear()
        removed, _binary = service.handle_with_binary(
            append_request(usage_tombstones=[tombstone]),
        )

        assert accepted["accepted"] == 1
        assert removed["accepted"] == 1
        assert removed["counts"]["usage_tombstones_accepted"] == 1
        assert store.read_snapshot().usage_atoms == ()
        assert service._pending_dirty == service._dirty_cells(
            (), (), (storage.UsageAtomTombstone(
                tombstone["event_id"], tombstone["direction"],
                tombstone["modality"], tombstone["cache_role"],
                tombstone["unit"], tombstone["observed_at"],
                tombstone["quantity"], tombstone["provider"],
                tombstone["model"], tombstone["thread_id"],
            ),),
        )
    service.writer = None


def test_blocked_cold_build_publishes_then_catches_up_without_starvation(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    incremental_entered = threading.Event()
    release_incremental = threading.Event()
    append_done = threading.Event()
    store = FakeStore()

    def blocked_builder(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return materializer.build_generation(*args, **kwargs)

    def blocked_incremental(*args, **kwargs):
        incremental_entered.set()
        assert release_incremental.wait(2)
        return materializer.update_generation(*args, **kwargs)

    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        store_opener=lambda *args, **kwargs: store,
        reader_opener=lambda *args, **kwargs: store,
        full_builder=blocked_builder,
        incremental_builder=blocked_incremental,
        clock=lambda: 100_000.0,
    )
    service._start()
    assert entered.wait(1)
    result = []

    def append():
        result.append(service.handle_with_binary(append_request(observations=[cpu_record()])))
        append_done.set()

    thread = threading.Thread(target=append)
    thread.start()
    assert append_done.wait(1), "durable append waited on the materializer"
    assert result[0][0]["source_generation"] == 1
    release.set()
    thread.join(timeout=1)
    assert incremental_entered.wait(2)
    assert service._cache is not None
    assert service._cache.generation.source_generation == 0
    assert service._stale_builds == 0
    release_incremental.set()
    service._close()
    assert service._cache.generation.source_generation == 1
    assert service._full_builds == service._incremental_builds == 1


def test_producer_faster_than_builder_publishes_progress_then_converges(tmp_path):
    class AccumulatingStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.observations = []

        def append_batch(self, **values):
            result = super().append_batch(**values)
            self.observations.extend(values["observations"])
            return result

        def read_snapshot(self, *, dirty_intervals=None):
            self.reads += 1
            self.dirty_reads.append(dirty_intervals)
            selected = tuple(self.observations)
            if dirty_intervals is not None:
                selected = tuple(
                    item
                    for item in selected
                    if any(start <= item.observed_at < end for start, end in dirty_intervals)
                )
            return storage.StoreSnapshot(
                storage.SchemaMetadata(5, 23, 1, self.source_generation),
                selected, (), (), (), (),
            )

    now = [100_000.0]
    builder_entered = threading.Event()
    release_builder = threading.Event()

    def blocked_incremental(*args, **kwargs):
        builder_entered.set()
        assert release_builder.wait(2)
        return materializer.update_generation(*args, **kwargs)

    store = AccumulatingStore()
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: now[0],
        incremental_builder=blocked_incremental,
    )
    service._view_demanded = lambda *args: True  # this test pins the all-views (fully demanded) contract
    service.writer = store
    service._build_once(store, True, frozenset())
    service._pending_full = False

    first, _binary = service.handle_with_binary(append_request(observations=[
        cpu_record("cpu-1", 99_990.25),
    ]))
    assert first["source_generation"] == 1
    first_work = service._take_work()
    assert first_work is not None
    now[0] = 100_001.0
    build = threading.Thread(target=lambda: service._build_once(store, *first_work))
    build.start()
    assert builder_entered.wait(1)

    for generation, observed_at in ((2, 99_991.25), (3, 99_992.25)):
        response, _binary = service.handle_with_binary(append_request(observations=[
            cpu_record(f"cpu-{generation}", observed_at),
        ]))
        assert response["source_generation"] == generation

    release_builder.set()
    build.join(timeout=2)
    assert build.is_alive() is False
    assert service._cache is not None
    assert service._cache.generation.source_generation == 1
    assert service._status()["generations"]["cache_matches_source"] is False
    metadata, binary = service.handle_with_binary(snapshot_request())
    assert metadata["source_generation"] == 1
    assert protocol.validate_snapshot(json.loads(binary))["source_generation"] == 1

    service.incremental_builder = materializer.update_generation
    catch_up = service._take_work()
    assert catch_up is not None
    now[0] = 100_002.0
    service._build_once(store, *catch_up)

    assert service._cache.generation.source_generation == 3
    assert service._take_work() is None
    assert service._status()["generations"]["cache_matches_source"] is True
    assert service._full_builds == 1
    assert service._incremental_builds == 2
    assert service._stale_builds == 0
    for observed_at in (99_990.25, 99_991.25, 99_992.25):
        bucket = next(
            item
            for item in service._cache.generation.layer(1).buckets
            if item.start <= observed_at < item.start + item.duration
        )
        assert any(item.name == "cpu_percent:host" for item in bucket.series)


def test_cache_hit_does_zero_storage_build_report_or_encoding_work(tmp_path, monkeypatch):
    store = FakeStore()
    builds = 0
    encodes = 0

    def open_store(*args, **kwargs):
        return store

    def build(*args, **kwargs):
        nonlocal builds
        builds += 1
        return materializer.build_generation(*args, **kwargs)

    def encode(wire):
        nonlocal encodes
        encodes += 1
        return json.dumps(wire, sort_keys=True).encode()

    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        store_opener=open_store,
        reader_opener=open_store,
        full_builder=build,
        encoder=encode,
        clock=lambda: 100_000.0,
    )
    service._start()
    assert service.cache_ready_event.wait(2)
    before = (store.reads, builds, encodes)
    monkeypatch.setattr(
        materializer,
        "build_cost_report",
        lambda _layer: (_ for _ in ()).throw(AssertionError("request recalculated report")),
    )

    first = service.handle_with_binary(snapshot_request())
    second = service.handle_with_binary(snapshot_request())

    assert first == second
    assert first[0]["cache_generation"] == 100_000_000
    assert first[1].startswith(b"{")
    assert (store.reads, builds, encodes) == before
    assert before == (1, 1, current_view_count())
    status, _binary = service.handle_with_binary({**FENCE, "action": "status"})
    assert status["warm"] == fully_warm_status()
    assert status["requests"]["hits"] == 2
    service._close()


def test_shared_browser_snapshots_are_preencoded_and_identical_for_all_clients(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    client_a = service_module._private_id("browser-a", "test.client")
    client_b = service_module._private_id("browser-b", "test.client")
    observations = (
        storage.Observation("a", "browser", client_a, 99_999, "epoch:a", 1, {
            "kind": "api", "latency_ms": 15,
        }),
        storage.Observation("b", "browser", client_b, 99_999, "epoch:b", 1, {
            "kind": "sse", "bytes": 200,
        }),
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 1), observations, (), (), (), ()),
        source_generation=1,
        cache_generation=10,
        generated_at=100_000,
        observed_until=100_000,
    )
    encodes = 0

    def encode(wire):
        nonlocal encodes
        encodes += 1
        return json.dumps(wire, sort_keys=True).encode()

    service.encoder = encode
    entries = service._encode_generation(generation)
    assert service._publish(generation, entries) is True
    built_encodes = encodes

    def browser_series(client_id):
        request = snapshot_request()
        request["client_id"] = client_id
        _metadata, binary = service.handle_with_binary(request)
        wire = protocol.validate_snapshot(json.loads(binary))
        return {
            name
            for bucket in wire["buckets"]
            for name in bucket["series"]
            if name.startswith("browser_")
        }

    expected = {
        "browser_api_per_second", "browser_latency_ms",
        "browser_bandwidth_bytes_per_second", "browser_sse_per_second",
    }
    assert browser_series("browser-a") == expected
    assert browser_series("browser-b") == expected
    assert browser_series("browser-unknown") == expected
    assert encodes == built_encodes
    assert built_encodes == current_view_count()


def test_current_browser_batch_ack_materializes_shared_all_client_series(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: 100_000.0,
    )
    raw_client = "current-browser"
    records = [
        {
            "event_id": "api-1", "family": "browser", "source_id": raw_client,
            "observed_at": 99_991.0, "epoch_id": "page-1", "owner_generation": 1,
            "payload": {"kind": "api", "latency_ms": 12, "bytes": 300},
        },
        {
            "event_id": "sse-1", "family": "browser", "source_id": raw_client,
            "observed_at": 99_992.0, "epoch_id": "page-1", "owner_generation": 1,
            "payload": {"kind": "sse", "bytes": 200},
        },
        {
            "event_id": "heartbeat-1", "family": "browser", "source_id": raw_client,
            "observed_at": 99_993.0, "epoch_id": "page-1", "owner_generation": 1,
            "payload": {"kind": "heartbeat", "latency_ms": 9, "bytes": 100},
        },
        {
            "event_id": "disconnect-1", "family": "browser", "source_id": raw_client,
            "observed_at": 99_994.0, "epoch_id": "page-1", "owner_generation": 1,
            "payload": {"kind": "disconnect", "duration_ms": 40},
        },
    ]
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        service.writer = store
        accepted, _binary = service.handle_with_binary(append_request(observations=records))
        duplicate, _binary = service.handle_with_binary(append_request(observations=records))
        assert accepted["ok"] is True and accepted["accepted"] == 4
        assert duplicate["accepted"] == 0 and duplicate["duplicates"] == 4
        snapshot = store.read_snapshot()
        assert len(snapshot.observations) == 4
        generation = materializer.build_generation(
            snapshot, source_generation=accepted["source_generation"], cache_generation=1,
            generated_at=100_000, observed_until=100_000,
        )
    service.writer = None
    assert service._publish(generation, service._encode_generation(generation)) is True

    def browser_series(client_id):
        request = snapshot_request()
        request.update({"client_id": client_id, "range_seconds": "300", "resolution": "10"})
        _metadata, binary = service.handle_with_binary(request)
        wire = protocol.validate_snapshot(json.loads(binary))
        return {
            name
            for bucket in wire["buckets"]
            for name in bucket["series"]
            if name.startswith("browser_")
        }

    assert browser_series(raw_client) == {
        "browser_api_per_second", "browser_sse_per_second", "browser_latency_ms",
        "browser_bandwidth_bytes_per_second", "browser_disconnected_ms",
    }
    assert browser_series("different-browser") == browser_series(raw_client)


def test_shared_browser_delta_and_cache_have_no_per_client_keys(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    service._view_demanded = lambda *args: True  # pins the fully demanded (all-views) contract
    def generation(cache_generation, extra=()):
        observations = tuple(
            storage.Observation(
                f"event-{index}-{cache_generation}", "browser",
                service_module._private_id(f"client-{index}", "test.client"),
                99_998 + cache_generation / 10,
                f"epoch-{index}", 1,
                {"kind": "api" if index % 2 == 0 else "sse"},
            )
            for index in range(materializer.MAX_PRIVATE_BROWSER_CLIENTS + 1)
        ) + tuple(extra)
        return materializer.build_generation(
            storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 1), observations, (), (), (), ()),
            source_generation=1,
            cache_generation=cache_generation,
            generated_at=100_000 + cache_generation,
            observed_until=100_000 + cache_generation,
        )

    first = generation(10)
    retained = service_module._private_id("client-0", "test.client")
    second = generation(20, (
        storage.Observation(
            "retained-latency", "browser", retained, 100_001, "epoch-retained", 1,
            {"kind": "api", "latency_ms": 31},
        ),
    ))
    assert service._publish(first, service._encode_generation(first)) is True
    assert service._publish(second, service._encode_generation(second)) is True

    request = delta_request(after_cache_generation=10)
    request["client_id"] = "client-0"
    _metadata, binary = service.handle_with_binary(request)
    wire = protocol.validate_delta(json.loads(binary))
    assert any("browser_latency_ms" in bucket["series"] for bucket in wire["buckets"])

    other = delta_request(after_cache_generation=10)
    other["client_id"] = "unknown-client"
    _metadata, binary = service.handle_with_binary(other)
    wire = protocol.validate_delta(json.loads(binary))
    assert any("browser_latency_ms" in bucket["series"] for bucket in wire["buckets"])

    status = service._status()
    assert status["cache"]["private_clients"] == 0
    assert status["cache"]["max_private_clients"] == materializer.MAX_PRIVATE_BROWSER_CLIENTS
    assert status["cache"]["private_entries"] == 0
    assert status["cache"]["private_bytes"] == 0


def test_shared_browser_updates_keep_only_public_cache_keys(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    private_ids = tuple(
        service_module._private_id(f"client-{index}", "test.client")
        for index in range(materializer.MAX_PRIVATE_BROWSER_CLIENTS + 1)
    )
    def generation(source_generation, cache_generation, selected):
        observations = tuple(
            storage.Observation(
                f"event-{source_generation}-{index}", "browser", private_ids[index],
                99_990 + source_generation, f"epoch-{index}", 1,
                {"kind": "api", "latency_ms": index + 1},
            )
            for index in selected
        )
        return materializer.build_generation(
            storage.StoreSnapshot(
                storage.SchemaMetadata(5, 23, 1, source_generation),
                observations, (), (), (), (),
            ),
            source_generation=source_generation,
            cache_generation=cache_generation,
            generated_at=100_000 + source_generation,
            observed_until=100_000 + source_generation,
        )

    first_indexes = tuple(range(materializer.MAX_PRIVATE_BROWSER_CLIENTS))
    second_indexes = tuple(range(1, materializer.MAX_PRIVATE_BROWSER_CLIENTS + 1))
    first = generation(1, 10, first_indexes)
    second = generation(2, 20, second_indexes)
    assert service._publish(first, service._encode_generation(first)) is True
    assert service._publish(second, service._encode_generation(second)) is True
    assert second.private_source_ids == ()
    assert {key[2] for key in service._cache.entries} == {None}
    assert {key[2] for key in service._delta_entries} <= {None}
    assert {key[2] for key in service._delta_revisions} <= {None}

    request = snapshot_request()
    request["client_id"] = "client-0"
    _metadata, binary = service.handle_with_binary(request)
    wire = protocol.validate_snapshot(json.loads(binary))
    assert any(
        name.startswith("browser_")
        for bucket in wire["buckets"]
        for name in bucket["series"]
    )
    request["client_id"] = f"client-{second_indexes[-1]}"
    _metadata, binary = service.handle_with_binary(request)
    wire = protocol.validate_snapshot(json.loads(binary))
    assert any(
        name.startswith("browser_")
        for bucket in wire["buckets"]
        for name in bucket["series"]
    )


def test_snapshot_returns_pending_or_cached_preencoded_protocol_wire(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    pending, binary = service.handle_with_binary(snapshot_request())
    assert pending["status"] == "pending"
    assert binary == b""

    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), ()),
        source_generation=0,
        cache_generation=1,
        generated_at=100_000,
        observed_until=100_000,
    )
    entries = service._encode_generation(generation)
    assert service._publish(generation, entries) is True
    metadata, binary = service.handle_with_binary(snapshot_request())
    wire = protocol.validate_snapshot(json.loads(binary))
    assert metadata["bytes"] == len(binary)
    assert wire["range_seconds"] == 300
    assert wire["resolution_seconds"] == 1
    assert len(wire["buckets"]) == 300
    assert wire["buckets"][0]["series"] == {}
    assert wire["buckets"][0]["source"] == {
        "first_timestamp": None,
        "last_timestamp": None,
        "count": 0,
    }
    assert wire["cost_report"]["total_tokens"] == 0
    assert wire["cost_report"]["total_micro_usd"] == 0
    older, older_binary = service.handle_with_binary(snapshot_request(metadata["cache_generation"] - 1))
    assert older == metadata
    assert older_binary == binary

    same, same_binary = service.handle_with_binary(snapshot_request(metadata["cache_generation"]))
    newer, newer_binary = service.handle_with_binary(snapshot_request(metadata["cache_generation"] + 1))
    assert same == newer == {
        "ok": True,
        "not_modified": True,
        "range_seconds": 300,
        "requested_resolution": "AUTO",
        "resolution_seconds": 1,
        "source_generation": 0,
        "cache_generation": metadata["cache_generation"],
    }
    assert same_binary == newer_binary == b""


def test_incremental_encode_slices_only_cells_published_at_this_cadence(tmp_path, monkeypatch):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), ()),
        source_generation=0,
        cache_generation=1,
        generated_at=100_000,
        observed_until=100_000,
    )
    original = materializer.slice_generation
    calls = []

    def counted(*args, **kwargs):
        calls.append((args[1], args[2], kwargs.get("private_source_id")))
        return original(*args, **kwargs)

    monkeypatch.setattr(materializer, "slice_generation", counted)
    entries = service._encode_generation(generation, resolutions=frozenset({1}))

    # AUTO is an alias of its resolved explicit twin: ONE slice + wire construction
    # per concrete resolution serves both cache entries (they differ only by the
    # echoed requested_resolution field), instead of re-slicing per requested value.
    assert calls == [(300, 1, None)]
    assert set(entries) == {(300, "AUTO", None), (300, 1, None)}
    auto_entry, explicit_entry = entries[(300, "AUTO", None)], entries[(300, 1, None)]
    assert auto_entry.metadata["requested_resolution"] == "AUTO"
    assert explicit_entry.metadata["requested_resolution"] == 1
    auto_body = json.loads(auto_entry.binary)
    explicit_body = json.loads(explicit_entry.binary)
    assert auto_body.pop("requested_resolution") == "AUTO"
    assert explicit_body.pop("requested_resolution") == 1
    assert auto_body == explicit_body  # identical apart from the echoed selector


def test_wire_bucket_uses_fact_provenance_instead_of_summing_projected_series():
    bucket = materializer.Bucket(
        100,
        1,
        (
            materializer.SeriesValue("system_cpu_percent", 20, 1, 100.25, 100.25),
            materializer.SeriesValue("process_cpu_percent:web", 5, 1, 100.25, 100.25),
        ),
        1,
        100.25,
        100.25,
        True,
    )

    wire = service_module._wire_bucket(bucket)

    assert wire["source"] == {
        "first_timestamp": 100.25,
        "last_timestamp": 100.25,
        "count": 1,
    }
    assert sum(item["source_count"] for item in wire["series"].values()) == 2


def test_server_wire_builders_do_not_revalidate_each_preencoded_private_variant():
    source = Path(service_module.__file__).read_text(encoding="utf-8")
    assert "return protocol.validate_snapshot(wire)" not in source
    assert "return protocol.validate_delta(wire)" not in source


def test_every_trusted_preencoded_snapshot_and_delta_passes_the_canonical_validator(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    empty = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), (),
    )
    populated = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 1),
        (
            storage.Observation(
                "cpu", "cpu", "web", 100_000.25, "epoch", 1,
                {"process_percent": 4, "system_percent": 20},
            ),
        ),
        (),
        (
            storage.UsageAtom("usage", "output", "text", "none", "tokens", 100_000.25, {
                "quantity": 12,
                "provider": "openai",
                "model": "gpt",
                "agent_id": "sol",
                "telemetry_complete": True,
            }),
        ),
        (),
        (),
    )
    first = materializer.build_generation(
        empty,
        source_generation=0,
        cache_generation=10,
        generated_at=100_000,
        observed_until=100_000,
    )
    second = materializer.build_generation(
        populated,
        source_generation=1,
        cache_generation=20,
        generated_at=100_001,
        observed_until=100_001,
    )
    first_entries = service._encode_generation(first)
    second_entries = service._encode_generation(second)

    for entry in (*first_entries.values(), *second_entries.values()):
        protocol.validate_snapshot(json.loads(entry.binary))
    assert service._publish(first, first_entries) is True
    assert service._publish(second, second_entries) is True
    for entries in service._delta_entries.values():
        for entry in entries:
            protocol.validate_delta(json.loads(entry.binary))


def test_service_composes_the_cadence_delta_bound_and_repairs_only_overflow(
    tmp_path,
    monkeypatch,
):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    service._view_demanded = lambda *args: True  # pins the fully demanded (all-views) contract
    empty = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), (),
    )
    assert service_module.DELTA_RING_ENTRY_BOUNDS == {1: 5, 10: 4, 60: 9, 300: 9}
    expected_bound = service_module.DELTA_RING_ENTRY_BOUNDS[1]

    def generation(index):
        return materializer.build_generation(
            empty,
            source_generation=0,
            cache_generation=index * 10,
            generated_at=100_000 + index,
            observed_until=100_000 + index,
        )

    first = generation(1)
    second = generation(2)
    assert service._publish(first, service._encode_generation(first)) is True
    second_entries = service._encode_generation(second)
    monkeypatch.setattr(materializer, "build_cost_report", lambda _layer: (_ for _ in ()).throw(
        AssertionError("delta publication rebuilt an already encoded cost report")
    ))
    assert service._publish(second, second_entries) is True
    monkeypatch.undo()
    for index in range(3, expected_bound + 2):
        item = generation(index)
        assert service._publish(item, service._encode_generation(item)) is True

    current_generation = (expected_bound + 1) * 10

    metadata, binary = service.handle_with_binary(delta_request(after_cache_generation=10))
    composed = protocol.validate_delta(json.loads(binary))
    assert metadata["revision"] == composed["revision"] == 1
    assert composed["base_cache_generation"] == 10
    assert composed["cache_generation"] == current_generation
    assert service._delta_repairs == 0

    assert service._status()["delta"]["max_entries_per_key"] == 9
    assert len(service._delta_entries[(300, 1, None)]) == expected_bound

    overflow = generation(expected_bound + 2)
    assert service._publish(overflow, service._encode_generation(overflow)) is True
    overflow_generation = (expected_bound + 2) * 10

    repair, repair_binary = service.handle_with_binary(delta_request(after_cache_generation=10))
    assert repair["status"] == "repair_required"
    assert repair_binary == b""

    metadata, binary = service.handle_with_binary(delta_request(
        after_cache_generation=20,
        after_revision=1,
    ))
    retained = protocol.validate_delta(json.loads(binary))
    assert metadata["revision"] == retained["revision"] == 2
    assert retained["base_cache_generation"] == 20
    assert retained["cache_generation"] == overflow_generation
    assert len(service._delta_entries[(300, 1, None)]) == expected_bound

    repair, repair_binary = service.handle_with_binary(delta_request(
        after_cache_generation=20,
        after_revision=99,
    ))
    assert repair["status"] == "repair_required"
    assert repair_binary == b""

    current, current_binary = service.handle_with_binary(delta_request(
        after_cache_generation=overflow_generation,
        after_revision=2,
    ))
    assert current["not_modified"] is True
    assert current_binary == b""


def test_composed_identity_overflow_is_typed_repair_through_http(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    empty = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), (),
    )
    current = materializer.build_generation(
        empty,
        source_generation=0,
        cache_generation=30,
        generated_at=100_000,
        observed_until=100_000,
    )
    assert service._publish(current, service._encode_generation(current)) is True
    snapshot = protocol.validate_snapshot(json.loads(
        service._cache.entries[(300, 1, None)].binary
    ))

    def edge(prefix, base, target, revision_number):
        return service._encoded_delta_entry({
            "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
            "range_seconds": 300,
            "resolution_seconds": 1,
            "source_generation": 0,
            "base_cache_generation": base,
            "cache_generation": target,
            "revision": revision_number,
            "buckets": [],
            "no_data": [],
            "tombstones": [
                {
                    "kind": "no_data",
                    "family": "cpu",
                    "source_id": f"{prefix}{index:04d}",
                    "epoch": "epoch",
                    "start": 0,
                    "end": 1,
                }
                for index in range(601)
            ],
            "cost_report": snapshot["cost_report"],
        })

    key = (300, 1, None)
    service._delta_entries[key] = [
        edge("a", 10, 20, 1),
        edge("b", 20, 30, 2),
    ]
    service._delta_revisions[key] = 2

    metadata, binary = service.handle_with_binary(
        delta_request(after_cache_generation=10)
    )

    class DirectClient:
        def ensure_started(self):
            return True

        def status(self):
            return {"ok": True}

        def retry(self):
            return True

        def snapshot(self, _request):
            raise AssertionError("delta repair must not fetch a snapshot in the forwarder")

        def delta(self, request):
            return service.handle_with_binary({
                **FENCE,
                "action": "delta",
                "range_seconds": request.range_seconds,
                "resolution_seconds": request.resolution_seconds,
                "client_id": request.client_id,
                "after_cache_generation": request.after_cache_generation,
                "after_revision": request.after_revision,
            })

    forwarded = http_module.StatsHttpForwarder(
        DirectClient(),
        client_binding_secret=b"s" * 32,
    ).delta_stream(
        "range_seconds=300&resolution_seconds=1&client_id=browser-a&"
        "after_cache_generation=10&after_revision=0",
        authenticated_username="alice",
    )

    assert metadata["status"] == "repair_required"
    assert binary == b""
    assert service._delta_repairs == 2
    assert forwarded.status == HTTPStatus.CONFLICT
    assert forwarded.metadata["status"] == "repair_required"


def test_malformed_retained_edge_is_not_composition_repair(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    empty = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), (),
    )
    current = materializer.build_generation(
        empty,
        source_generation=0,
        cache_generation=30,
        generated_at=100_000,
        observed_until=100_000,
    )
    assert service._publish(current, service._encode_generation(current)) is True
    key = (300, 1, None)
    service._delta_entries[key] = [service_module.CacheEntry(
        MappingProxyType({
            "base_cache_generation": 10,
            "cache_generation": 30,
        }),
        b"{}",
    )]
    service._delta_revisions[key] = 1

    metadata, binary = service.handle_with_binary(
        delta_request(after_cache_generation=10)
    )

    assert metadata["status"] == "unsupported"
    assert binary == b""
    assert service._delta_repairs == 0


@pytest.mark.parametrize(
    ("resolution_seconds", "expected_bound"),
    sorted(service_module.DELTA_RING_ENTRY_BOUNDS.items()),
)
def test_fallback_delta_history_enforces_each_resolution_bound(
    tmp_path,
    resolution_seconds,
    expected_bound,
):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    service._view_demanded = lambda *args: True
    empty = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), (),
    )
    for index in range(expected_bound + 3):
        generation = materializer.build_generation(
            empty,
            source_generation=0,
            cache_generation=(index + 1) * 10,
            generated_at=100_000 + index,
            observed_until=100_000 + index,
        )
        encoded = service._encode_generation(
            generation,
            resolutions=frozenset({resolution_seconds}),
        )
        assert service._publish(
            generation,
            encoded,
            resolutions=frozenset({resolution_seconds}),
        ) is True

    retained = [
        len(entries)
        for key, entries in service._delta_entries.items()
        if key[1] == resolution_seconds
    ]
    assert retained
    assert set(retained) == {expected_bound}


def test_delta_carries_the_full_precomputed_candidate_cost_report(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    service._view_demanded = lambda *args: True  # pins the fully demanded (all-views) contract
    empty = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), (),
    )
    atom = storage.UsageAtom("usage", "input", "text", "none", "tokens", 99_999, {
        "quantity": 12,
        "provider": "openai",
        "model": "gpt",
        "agent_id": "sol",
        "execution_source": "codex",
        "telemetry_complete": True,
    })
    populated = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 1), (), (), (atom,), (), (),
    )
    evidence = pricing.PricingEvidence(
        "gpt", "2.00", 1_000_000, "2026-07-09T00:00:00Z", "seed",
        "https://example.com/pricing", 3,
    )
    resolver = lambda _atom: pricing.UsagePriceProjection(25, 25, evidence)
    first = materializer.build_generation(
        empty,
        source_generation=0,
        cache_generation=10,
        generated_at=100_000,
        observed_until=100_000,
        price_resolver=resolver,
    )
    second = materializer.build_generation(
        populated,
        source_generation=1,
        cache_generation=20,
        generated_at=100_000,
        observed_until=100_000,
        price_resolver=resolver,
    )
    assert service._publish(first, service._encode_generation(first)) is True
    assert service._publish(second, service._encode_generation(second)) is True

    _metadata, snapshot_binary = service.handle_with_binary(snapshot_request())
    snapshot_wire = protocol.validate_snapshot(json.loads(snapshot_binary))
    _metadata, delta_binary = service.handle_with_binary(
        delta_request(after_cache_generation=10),
    )
    delta_wire = protocol.validate_delta(json.loads(delta_binary))

    assert delta_wire["cost_report"] == snapshot_wire["cost_report"]
    assert delta_wire["cost_report"]["total_tokens"] == 12
    assert delta_wire["cost_report"]["total_micro_usd"] == 25
    assert delta_wire["cost_report"]["total_api_list_micro_usd"] == 25
    assert delta_wire["cost_report"]["dimensions"]["input"] == {
        "tokens": 12,
        "micro_usd": 25,
        "api_list_micro_usd": 25,
    }


def test_no_change_prune_schedules_no_build_and_deletions_dirty_only_cutoff_cells(tmp_path):
    monotonic_now = [10.0]
    store = FakeStore()
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: 1_000.0,
    )
    service.writer = store
    service._pending_full = False
    service._next_reconcile_at = 20.0

    assert service._reconcile_if_due() is False
    monotonic_now[0] = 20.0
    # A prune that removed nothing schedules NO build at all: rebuilding an
    # unchanged generation was an 18.6s near-100% CPU spike every five minutes.
    assert service._reconcile_if_due() is True
    assert store.prunes == 1
    assert service._take_work() is None
    assert service._reconcile_if_due() is False
    assert service._reconciliations == 1
    assert service._next_reconcile_at == 320.0

    # A prune that DID delete originals marks exactly the cutoff-straddling cell
    # per resolution dirty (the incremental builder skips out-of-window ones);
    # it never requests a full rebuild.
    store.prune_observations_deleted = 3
    monotonic_now[0] = 320.0
    assert service._reconcile_if_due() is True
    cutoff = 1_000.0 - storage.RETENTION_SECONDS
    expected = frozenset(
        materializer.DirtyCell(resolution, math.floor(cutoff / resolution) * resolution)
        for resolution in stats_resolution.RESOLUTION_CHOICES
    )
    assert service._take_work() == (False, expected, False)


def test_vacuum_runs_only_after_idle_and_persists_its_schedule(tmp_path):
    monotonic_now = [0.0]
    wall_now = [1_000.0]
    store = FakeStore()
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        idle_seconds=1.0,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )
    service.writer = store
    service._pending_full = False
    service._next_reconcile_at = 9_999.0
    service._next_vacuum_at = 0.0

    # Requests never perform file-rewriting maintenance; the generic runtime
    # calls _idle only after client handling has stopped.
    service._on_client()
    assert store.vacuums == []
    monotonic_now[0] = 2.0
    assert service._idle() is True
    assert store.vacuums == [1_000.0]
    assert service._status()["vacuum"] == {
        "interval_seconds": service_module.VACUUM_INTERVAL_SECONDS,
        "jitter_seconds": 0.0,
        "retry_seconds": service_module.VACUUM_RETRY_SECONDS,
        "count": 1,
        "last_at": 1_000.0,
        "last_seconds": 0.0,
        "next_at": 4_600.0,
        "next_in_seconds": 3_600.0,
        "failure": "",
    }


@pytest.mark.parametrize(
    ("collector", "dirty", "ring_deadline", "host_deadlines", "now", "expected"),
    (
        pytest.param(False, False, None, (100.0, 100.0), 100.0, None, id="no-collector-idle"),
        pytest.param(False, False, 90.0, (100.0, 100.0), 100.0, None, id="no-collector-stale-ring"),
        pytest.param(False, True, 105.0, (100.0, 100.0), 100.0, 5.0, id="no-collector-future-ring"),
        pytest.param(False, True, 100.0, (110.0, 110.0), 100.0, 0.0, id="no-collector-due-ring"),
        pytest.param(False, True, None, (100.0, 100.0), 100.0, None, id="no-collector-waiting-source"),
        pytest.param(True, False, None, (105.0, 110.0), 100.0, 5.0, id="collector-future"),
        pytest.param(True, False, None, (90.0, 110.0), 100.0, 0.0, id="collector-due"),
        pytest.param(True, True, 103.0, (105.0, 110.0), 100.0, 3.0, id="collector-with-earlier-ring"),
        pytest.param(True, False, None, (100.0, 110.0), 90.0, 10.0, id="monotonic-rollback"),
    ),
)
def test_ring_wait_timeout_uses_only_owned_deadlines(
    tmp_path,
    collector,
    dirty,
    ring_deadline,
    host_deadlines,
    now,
    expected,
):
    monotonic_now = [100.0]
    service = service_module.StatsCurrentService(
        tmp_path / "stats.sock",
        tmp_path / "stats.sqlite3",
        monotonic=lambda: monotonic_now[0],
    )
    if collector:
        service.collector_context = {"pid": 1234, "port": 7443, "owner_generation": 42}
    service._next_host_cpu_at, service._next_host_gpu_at = host_deadlines
    if dirty:
        service._pending_ring_dirty.add(materializer.DirtyCell(1, 0))
    service._next_ring_flush_at = ring_deadline
    if dirty and ring_deadline is None:
        service._ring_waiting_for_source = 1
    monotonic_now[0] = now

    assert service._ring_wait_timeout() == expected


def test_collector_context_accepts_only_bounded_owner_identity(tmp_path):
    service = service_module.StatsCurrentService(tmp_path / "stats.sock", tmp_path / "stats.sqlite3")

    rejected, binary = service.handle_with_binary({
        **FENCE,
        "action": "collector_context",
        "pid": os.getpid(),
        "port": 7443,
        "owner_generation": 42,
        "sessions": ["must-not-cross-the-boundary"],
    })

    assert binary == b""
    assert rejected["status"] == "unsupported"

    accepted, binary = service.handle_with_binary({
        **FENCE,
        "action": "collector_context",
        "pid": os.getpid(),
        "port": 7443,
        "owner_generation": 42,
    })

    assert binary == b""
    assert accepted == {
        "ok": True,
        "pid": os.getpid(),
        "port": 7443,
        "owner_generation": 42,
    }
    assert service.collector_context == {
        "pid": os.getpid(),
        "port": 7443,
        "owner_generation": 42,
    }


def test_statsd_collects_registered_web_cpu_and_pushes_it_to_the_matching_owner(tmp_path, monkeypatch):
    service = service_module.StatsCurrentService(tmp_path / "stats.sock", tmp_path / "stats.sqlite3")
    publisher = FakeStore()
    service.collector_context = {"pid": 1234, "port": 7443, "owner_generation": 42}
    service._next_host_cpu_at = 0.0
    service._next_host_gpu_at = float("inf")

    class CpuSampler:
        def sample(self, pid):
            assert pid == 1234
            return {"time": 100.0, "pid": 1234, "cpu_percent": 12.0, "system_cpu_percent": 20.0, "rss_bytes": 99}

    pushed = []
    service._host_cpu_sampler = CpuSampler()
    monkeypatch.setattr(service, "_matching_web_owner", lambda context: {"control_socket": "owned.sock"})
    monkeypatch.setattr(service_module, "send_yolomux_control_request", lambda owner, request, timeout: pushed.append((owner, request, timeout)) or {"ok": True})

    service._collect_host_facts_if_due(publisher)

    observation = publisher.last_append["observations"][0]
    assert observation.family == "cpu"
    assert observation.source_id == "port:7443"
    assert observation.payload == {"process_percent": 12.0, "system_percent": 20.0}
    assert pushed == [({"control_socket": "owned.sock"}, {"action": "stats_cpu_sample", "sample": {"time": 100.0, "pid": 1234, "cpu_percent": 12.0, "system_cpu_percent": 20.0, "rss_bytes": 99}}, 0.25)]


def test_inline_host_collectors_keep_source_scoped_epochs_until_context_replacement(tmp_path, monkeypatch):
    monotonic_now = [0.0]
    wall_now = [100.0]

    class RecordingStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.batches = []

        def append_batch(self, **values):
            self.batches.append(values)
            return super().append_batch(**values)

        def latest_coverage_epoch(self, family, source_id, owner_generation, native_cadence_seconds):
            return next((
                item
                for batch in reversed(self.batches)
                for item in batch.get("coverage_epochs", ())
                if (item.family, item.source_id, item.owner_generation, item.native_cadence_seconds)
                == (family, source_id, owner_generation, native_cadence_seconds)
            ), None)

        def inline_coverage_source_ids(self, family, owner_generation):
            prefix = f"inline:{owner_generation}:{family}:"
            return tuple(sorted({
                item.source_id
                for batch in self.batches
                for item in batch.get("coverage_epochs", ())
                if item.family == family
                and item.owner_generation == owner_generation
                and item.epoch_id.startswith(prefix)
            }))

    class CpuSampler:
        def sample(self, _pid):
            return {"cpu_percent": 12.0, "system_cpu_percent": 20.0}

    gpu_pass = [0]

    def gpu_devices():
        gpu_pass[0] += 1
        sources = {
            1: ("gpu:0", "gpu:1"),
            2: ("gpu:1", "gpu:2"),
        }.get(gpu_pass[0], ("gpu:0", "gpu:1", "gpu:2"))
        return {
            source: {
                "util_percent": 10.0,
                "memory_used_bytes": 20.0,
                "memory_capacity_bytes": 100.0,
                "label": source,
            }
            for source in sources
        }

    service = service_module.StatsCurrentService(
        tmp_path / "stats.sock",
        tmp_path / "stats.sqlite3",
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
    )
    publisher = RecordingStore()
    service.collector_context = {"pid": 1234, "port": 7443, "owner_generation": 42}
    service._host_cpu_sampler = CpuSampler()
    monkeypatch.setattr(service, "_matching_web_owner", lambda _context: None)
    monkeypatch.setattr(service_module.host_collectors, "gpu_devices", gpu_devices)

    service._collect_host_facts_if_due(publisher)
    monotonic_now[0] = wall_now[0] = 110.0
    service._collect_host_facts_if_due(publisher)

    observations = [item for batch in publisher.batches for item in batch["observations"]]
    coverage = [item for batch in publisher.batches for item in batch["coverage_epochs"]]
    for family, source_id in (("cpu", "port:7443"), ("gpu", "gpu:1")):
        source_observations = [item for item in observations if (item.family, item.source_id) == (family, source_id)]
        source_coverage = [item for item in coverage if (item.family, item.source_id) == (family, source_id)]
        assert len(source_observations) == len(source_coverage) == 2
        assert len({item.epoch_id for item in source_observations}) == 1
        assert len({item.started_at for item in source_coverage}) == 1
    gpu_two = next(item for item in coverage if item.source_id == "gpu:2")
    assert gpu_two.started_at == 110.0

    first_cpu_epoch = next(item.epoch_id for item in observations if item.family == "cpu")
    accepted, _binary = service.handle_with_binary({
        **FENCE,
        "action": "collector_context",
        "pid": 1234,
        "port": 7443,
        "owner_generation": 42,
    })
    assert accepted["ok"] is True
    wall_now[0] = monotonic_now[0] = 120.0
    service._collect_host_facts_if_due(publisher)
    later_coverage = [item for batch in publisher.batches for item in batch["coverage_epochs"]]
    gpu_zero = [item for item in later_coverage if item.source_id == "gpu:0"]
    assert len(gpu_zero) == 2
    assert gpu_zero[1].epoch_id != gpu_zero[0].epoch_id
    assert (gpu_zero[0].started_at, gpu_zero[1].started_at) == (100.0, 120.0)

    accepted, _binary = service.handle_with_binary({
        **FENCE,
        "action": "collector_context",
        "pid": 1234,
        "port": 7443,
        "owner_generation": 43,
    })
    assert accepted["ok"] is True
    wall_now[0] = monotonic_now[0] = 130.0
    service._next_host_gpu_at = float("inf")
    service._collect_host_facts_if_due(publisher)
    cpu_epochs = [
        item.epoch_id
        for batch in publisher.batches
        for item in batch["observations"]
        if item.family == "cpu"
    ]
    assert cpu_epochs[2] == first_cpu_epoch, "idempotent registration preserves the source epoch"
    assert cpu_epochs[3] != cpu_epochs[2], "a new owner generation rotates the source epoch"

    restarted = service_module.StatsCurrentService(
        tmp_path / "restarted.sock",
        tmp_path / "stats.sqlite3",
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
    )
    restarted.collector_context = {"pid": 1234, "port": 7443, "owner_generation": 42}
    restarted._host_cpu_sampler = CpuSampler()
    restarted._next_host_gpu_at = float("inf")
    monkeypatch.setattr(restarted, "_matching_web_owner", lambda _context: None)
    wall_now[0] = monotonic_now[0] = 140.0
    restarted._collect_host_facts_if_due(publisher)
    restarted_cpu = publisher.batches[-1]["coverage_epochs"][0]
    original_cpu = next(
        item
        for batch in publisher.batches
        for item in batch["coverage_epochs"]
        if item.family == "cpu" and item.owner_generation == 42
    )
    assert (restarted_cpu.epoch_id, restarted_cpu.started_at) == (
        original_cpu.epoch_id,
        original_cpu.started_at,
    )

    restarted_gpu = service_module.StatsCurrentService(
        tmp_path / "restarted-gpu.sock",
        tmp_path / "stats.sqlite3",
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
    )
    restarted_gpu.collector_context = {"pid": 1234, "port": 7443, "owner_generation": 42}
    restarted_gpu._next_host_cpu_at = float("inf")
    wall_now[0] = monotonic_now[0] = 150.0
    restarted_gpu._collect_host_facts_if_due(publisher)
    restarted_gpu_one = next(
        item for item in publisher.batches[-1]["coverage_epochs"] if item.source_id == "gpu:1"
    )
    original_gpu_one = next(
        item
        for batch in publisher.batches
        for item in reversed(batch["coverage_epochs"])
        if item.source_id == "gpu:1" and item.owner_generation == 42
    )
    assert (restarted_gpu_one.epoch_id, restarted_gpu_one.started_at) == (
        original_gpu_one.epoch_id,
        original_gpu_one.started_at,
    )


def test_statsd_restart_rotates_only_gpu_missing_from_initial_roster(tmp_path, monkeypatch):
    monotonic_now = [0.0]
    wall_now = [100.0]

    class RecordingStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.batches = []

        def append_batch(self, **values):
            self.batches.append(values)
            return super().append_batch(**values)

        def latest_coverage_epoch(self, family, source_id, owner_generation, native_cadence_seconds):
            return next((
                item
                for batch in reversed(self.batches)
                for item in batch.get("coverage_epochs", ())
                if (item.family, item.source_id, item.owner_generation, item.native_cadence_seconds)
                == (family, source_id, owner_generation, native_cadence_seconds)
            ), None)

        def inline_coverage_source_ids(self, family, owner_generation):
            prefix = f"inline:{owner_generation}:{family}:"
            return tuple(sorted({
                item.source_id
                for batch in self.batches
                for item in batch.get("coverage_epochs", ())
                if item.family == family
                and item.owner_generation == owner_generation
                and item.epoch_id.startswith(prefix)
            }))

    class CpuSampler:
        def sample(self, _pid):
            return {"cpu_percent": 12.0, "system_cpu_percent": 20.0}

    gpu_pass = [0]

    def gpu_devices():
        gpu_pass[0] += 1
        sources = ("gpu:0", "gpu:1") if gpu_pass[0] != 2 else ("gpu:1",)
        return {
            source: {
                "util_percent": 10.0,
                "memory_used_bytes": 20.0,
                "memory_capacity_bytes": 100.0,
                "label": source,
            }
            for source in sources
        }

    publisher = RecordingStore()
    monkeypatch.setattr(service_module.host_collectors, "gpu_devices", gpu_devices)

    initial = service_module.StatsCurrentService(
        tmp_path / "initial.sock", tmp_path / "stats.sqlite3",
        monotonic=lambda: monotonic_now[0], clock=lambda: wall_now[0],
    )
    initial.collector_context = {"pid": 1234, "port": 7443, "owner_generation": 42}
    initial._host_cpu_sampler = CpuSampler()
    monkeypatch.setattr(initial, "_matching_web_owner", lambda _context: None)
    initial._collect_host_facts_if_due(publisher)

    restarted = service_module.StatsCurrentService(
        tmp_path / "restarted.sock", tmp_path / "stats.sqlite3",
        monotonic=lambda: monotonic_now[0], clock=lambda: wall_now[0],
    )
    restarted.collector_context = {"pid": 1234, "port": 7443, "owner_generation": 42}
    restarted._host_cpu_sampler = CpuSampler()
    monkeypatch.setattr(restarted, "_matching_web_owner", lambda _context: None)
    wall_now[0] = monotonic_now[0] = 110.0
    restarted._collect_host_facts_if_due(publisher)
    wall_now[0] = monotonic_now[0] = 120.0
    restarted._collect_host_facts_if_due(publisher)

    coverage = [item for batch in publisher.batches for item in batch["coverage_epochs"]]
    cpu = [item for item in coverage if item.family == "cpu"]
    gpu_zero = [item for item in coverage if item.source_id == "gpu:0"]
    gpu_one = [item for item in coverage if item.source_id == "gpu:1"]
    assert len({(item.epoch_id, item.started_at) for item in cpu}) == 1
    assert len({(item.epoch_id, item.started_at) for item in gpu_one}) == 1
    assert len(gpu_zero) == 2
    assert gpu_zero[1].epoch_id != gpu_zero[0].epoch_id
    assert (gpu_zero[0].started_at, gpu_zero[1].started_at) == (100.0, 120.0)


def test_browser_diagnostics_and_delta_remain_schedulable_during_active_materialization(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    now = [100_000.0]
    store = FakeStore()
    service = service_module.StatsCurrentService(
        tmp_path / "stats.sock",
        tmp_path / "stats.sqlite3",
        clock=lambda: now[0],
    )
    service.writer = store
    service._build_once(store, True, frozenset())
    assert service._cache is not None
    current_generation = service._cache.generation.cache_generation
    original_builder = service.incremental_builder

    def delayed_builder(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original_builder(*args, **kwargs)

    service.incremental_builder = delayed_builder
    store.source_generation = 1
    observation = storage.Observation(
        "cpu-later", "cpu", "host", now[0] - 0.25, "cpu:1", 1,
        {"process_percent": 2, "system_percent": 4},
    )
    store.last_append = {"observations": (observation,)}
    dirty = service._dirty_cells((observation,), (), ())
    build = threading.Thread(
        target=lambda: service._build_once(store, False, frozenset(dirty)),
    )
    build.start()
    assert entered.wait(1)

    profiles, profiles_binary = service.handle_with_binary({
        **FENCE,
        "action": "browser_profiles",
    })
    delta_metadata, delta_binary = service.handle_with_binary(
        delta_request(after_cache_generation=current_generation),
    )
    release.set()
    build.join(timeout=2)

    assert build.is_alive() is False
    assert profiles["ok"] is True
    assert profiles_binary == b""
    assert delta_metadata.get("ok") is True
    assert delta_metadata.get("not_modified") is True
    assert delta_binary == b""


def test_active_client_lease_prevents_idle_exit_until_released(tmp_path):
    monotonic_now = [0.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        idle_seconds=1.0,
        monotonic=lambda: monotonic_now[0],
    )
    service._pending_full = False
    monotonic_now[0] = 10.0

    lease, binary = service.handle_with_binary({
        **FENCE,
        "action": "lease",
        "client_pid": os.getpid(),
        "lease_id": "",
    })
    assert lease.get("ok") is True, lease
    assert binary == b""
    assert service._idle() is False

    renewed, binary = service.handle_with_binary({
        **FENCE,
        "action": "lease",
        "client_pid": os.getpid(),
        "lease_id": lease["lease_id"],
    })
    assert renewed == lease
    assert binary == b""
    assert len(service.leases) == 1

    released, binary = service.handle_with_binary({
        **FENCE,
        "action": "release",
        "lease_id": lease["lease_id"],
    })
    assert released == {"ok": True, "leases": 0}
    assert binary == b""
    assert service._idle() is True


def test_genuine_idle_exit_restarts_and_cold_warms_the_same_database(tmp_path):
    socket_path = tmp_path / "statsd.sock"
    database = tmp_path / storage.DATABASE_FILENAME
    first = service_module.StatsCurrentService(
        socket_path,
        database,
        idle_seconds=1.0,
    )
    first_thread = threading.Thread(target=first.run, daemon=True)
    first_thread.start()
    assert first.cache_ready_event.wait(5), first._status()
    first_status = first._status()
    # Persisted rings flush on a ten-second cadence before idle exit is safe.
    first_thread.join(timeout=15)
    assert first_thread.is_alive() is False
    assert first_status["warm"] == fully_warm_status()

    second = service_module.StatsCurrentService(
        socket_path,
        database,
        idle_seconds=1.0,
    )
    second_thread = threading.Thread(target=second.run, daemon=True)
    second_thread.start()
    try:
        assert second.cache_ready_event.wait(5), second._status()
        second_status = second._status()
        assert second_status["warm"] == fully_warm_status()
        assert second_status["generations"]["source"] == first_status["generations"]["source"]
        # This database is empty, so under G3 neither run publishes a generation:
        # succeeded-with-nothing-in-it must not report as published. The property
        # this test owns is that a cold restart re-warms every resolution over the
        # same database, which the warm assertion above proves.
        assert second_status["generations"]["cache"] == first_status["generations"]["cache"] == 0
        assert second_status["warm"] == first_status["warm"]
    finally:
        second.stop_event.set()
        second.work_event.set()
        second_thread.join(timeout=3)
        assert second_thread.is_alive() is False


def test_new_lease_reaps_dead_process_owners_instead_of_leaking_capacity(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    service.leases["dead"] = dead_client_lease_record(2_147_483_647)

    lease, _binary = service.handle_with_binary({
        **FENCE,
        "action": "lease",
        "client_pid": os.getpid(),
        "lease_id": "",
    })

    assert lease["ok"] is True
    assert lease["leases"] == 1
    assert "dead" not in service.leases


def test_idle_check_reaps_dead_leases_when_no_new_client_arrives(tmp_path):
    monotonic_now = [0.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        idle_seconds=1.0,
        monotonic=lambda: monotonic_now[0],
    )
    service._pending_full = False
    service.leases["dead"] = dead_client_lease_record(2_147_483_647)
    monotonic_now[0] = 10.0

    assert service._idle() is True
    assert service.leases == {}


def test_system_status_exposes_current_pipeline_health_without_private_values(tmp_path):
    monotonic_now = [10.0]

    def monotonic():
        monotonic_now[0] += 0.025
        return monotonic_now[0]

    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: 1_000.0,
        monotonic=monotonic,
    )
    service._view_demanded = lambda *args: True  # pins the fully demanded (all-views) contract
    service.writer = FakeStore()
    empty = storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), (),
    )
    first = materializer.build_generation(
        empty,
        source_generation=0,
        cache_generation=10,
        generated_at=100_000,
        observed_until=100_000,
    )
    second = materializer.build_generation(
        empty,
        source_generation=0,
        cache_generation=20,
        generated_at=100_001,
        observed_until=100_001,
    )
    assert service._publish(first, service._encode_generation(first)) is True
    assert service._publish(second, service._encode_generation(second)) is True
    service.handle_with_binary(snapshot_request())
    service.handle_with_binary(delta_request(after_cache_generation=10))
    private_value = "status-must-not-expose-this-browser"
    private_record = browser_record(private_value)
    private_record["observed_at"] = 99_999.0
    service.handle_with_binary(append_request(observations=[private_record]))
    service.handle_with_binary({"protocol_version": 22, "schema_generation": 5, "action": "status"})
    service._record_build_failure(ValueError(f"must not expose {private_value} or /private/path"))

    status = service._status()
    rendered = json.dumps(status, sort_keys=True)

    assert status["service"] == {
        "protocol_version": storage.MIN_WRITER_PROTOCOL,
        "wire_protocol_version": protocol.WIRE_PROTOCOL_VERSION,
        "build": storage.MIN_WRITER_BUILD,
        "code_revision": revision.CURRENT_CODE_REVISION,
    }
    assert status["schema"] == {
        "application_id": storage.APPLICATION_ID,
        "generation": storage.SCHEMA_VERSION,
        "minimum_writer_protocol": storage.MIN_WRITER_PROTOCOL,
        "minimum_writer_build": storage.MIN_WRITER_BUILD,
    }
    assert status["writer"]["pid"] > 0
    assert status["writer"]["sole_writer"] is True
    assert status["writer"]["last_source_commit_at"] == 1_000.0
    assert {
        key: status["generations"][key]
        for key in ("source", "cache", "cache_matches_source")
    } == {
        "source": 1,
        "cache": 0,
        "cache_matches_source": False,
    }
    # source_generation is 0 -- an empty payload -- so no resolution is
    # reported as published. G3: succeeded-with-nothing-in-it must not look
    # identical to a real publication on the wire.
    assert status["generations"]["by_resolution"] == {}
    assert status["warm"] == fully_warm_status()
    assert status["queue"]["writer_depth"] == 0
    assert status["queue"]["materializer_depth"] == 5
    assert status["materializer"] == {
        "state": "failed",
        "dirty_cells": 4,
        "building": False,
        "failed_builds": 1,
    }
    assert status["cache"]["snapshot_entries"] == current_view_count()
    assert status["cache"]["delta_entries"] > 0
    assert status["cache"]["shared_bytes"] > 0
    assert status["cache"]["private_bytes"] == 0
    assert status["traffic"]["snapshot"]["count"] == 1
    assert status["traffic"]["snapshot"]["hits"] == 1
    assert status["traffic"]["snapshot"]["last_seconds"] > 0
    assert status["traffic"]["delta"]["count"] == 1
    assert status["traffic"]["delta"]["hits"] == 1
    assert status["traffic"]["delta"]["last_seconds"] > 0
    assert status["request_traces"]["retained"] == 2
    assert status["request_traces"]["maximum"] == service_module.MAX_REQUEST_TRACES
    assert [item["kind"] for item in status["request_traces"]["items"]] == ["snapshot", "delta"]
    assert [item["result"] for item in status["request_traces"]["items"]] == ["hit", "hit"]
    for item in status["request_traces"]["items"]:
        assert item["request_id"].startswith("stats-")
        assert item["range_seconds"] == 300
        assert item["resolution_seconds"] == 1
        assert item["client_hash"].startswith("browser:")
        assert item["source_generation"] >= 0
        assert item["cache_generation"] >= 0
    assert status["requests"]["rejected_old"] == 1
    assert status["reconciliation"]["interval_seconds"] == service_module.FULL_RECONCILE_SECONDS
    assert status["reconciliation"]["next_at"] > 1_000.0
    assert status["failure"] == {
        "component": "materializer",
        "kind": "ValueError",
        "at": 1_000.0,
    }
    assert private_value not in rendered
    assert "browser-a" not in rendered
    assert "/private/path" not in rendered


def test_request_traces_are_bounded_and_do_not_expose_raw_client_ids(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), ()),
        source_generation=0,
        cache_generation=10,
        generated_at=100_000,
        observed_until=100_000,
    )
    assert service._publish(generation, service._encode_generation(generation)) is True
    raw_client = "raw-private-browser-identity"
    for _index in range(service_module.MAX_REQUEST_TRACES + 5):
        request = snapshot_request()
        request["client_id"] = raw_client
        service.handle_with_binary(request)
    traces = service._status()["request_traces"]
    assert traces["retained"] == traces["maximum"] == service_module.MAX_REQUEST_TRACES
    assert traces["items"][0]["request_id"] == "stats-6"
    assert raw_client not in json.dumps(traces)


def test_cache_generation_advances_across_service_restart(tmp_path):
    generations = []
    for now in (100_000.0, 100_001.0):
        store = FakeStore()
        service = service_module.StatsCurrentService(
            tmp_path / "statsd.sock",
            tmp_path / storage.DATABASE_FILENAME,
            clock=lambda now=now: now,
        )
        service._build_once(store, True, frozenset())
        assert service._cache is not None
        generations.append(service._cache.generation.cache_generation)
    assert generations == [100_000_000, 100_001_000]


def test_concurrent_reader_writer_restart_keeps_generations_monotonic(tmp_path):
    path = tmp_path / storage.DATABASE_FILENAME
    first_observation = storage.Observation(
        "restart-1", "cpu", "web", 99_990.25, "epoch", 1,
        {"process_percent": 4, "system_percent": 20},
    )
    second_observation = storage.Observation(
        "restart-2", "cpu", "web", 99_991.25, "epoch", 1,
        {"process_percent": 5, "system_percent": 21},
    )
    writer = storage.Store.open(path)
    first_reader = None
    second_reader = None
    try:
        assert writer.append_observation(first_observation) is True
        first_reader = storage.Store.open_reader(path)
        first_service = service_module.StatsCurrentService(
            tmp_path / "first.sock", path, clock=lambda: 100_000.0,
        )
        first_service._build_once(first_reader, True, frozenset())
        assert first_service._cache is not None

        with first_reader.pinned_snapshot(
            dirty_intervals=((99_990, 99_991),),
        ) as read_pinned:
            assert writer.append_observation(second_observation) is True
            pinned = read_pinned()
        assert pinned.schema.source_generation == 1
        assert pinned.observations == (first_observation,)
        first_reader.close()
        first_reader = None

        second_reader = storage.Store.open_reader(path)
        second_service = service_module.StatsCurrentService(
            tmp_path / "second.sock", path, clock=lambda: 100_001.0,
        )
        second_service._build_once(second_reader, True, frozenset())
        assert second_service._cache is not None

        assert (
            first_service._cache.generation.source_generation,
            second_service._cache.generation.source_generation,
        ) == (1, 2)
        assert (
            first_service._cache.generation.cache_generation
            < second_service._cache.generation.cache_generation
        )
        assert second_service._cache.generation.cache_generation == 100_001_000
        assert second_reader.read_snapshot().schema.source_generation == 2
    finally:
        if first_reader is not None:
            first_reader.close()
        if second_reader is not None:
            second_reader.close()
        writer.close()


def test_incremental_build_reads_only_the_union_of_dirty_bucket_intervals(tmp_path):
    now = [100_000.0]
    store = FakeStore()
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: now[0],
    )
    service._build_once(store, True, frozenset())
    observation = storage.Observation(
        "cpu", "cpu", "web", 100_000.25, "epoch", 1,
        {"process_percent": 4, "system_percent": 20},
    )
    store.source_generation = 1
    store.last_append = {"observations": (observation,)}
    dirty = frozenset(service._dirty_cells((observation,), ()))
    now[0] = 100_001.0

    service._build_once(store, False, dirty)

    assert store.dirty_reads[0] is None
    assert set(store.dirty_reads[1]) == {
        (cell.start, cell.start + cell.resolution) for cell in dirty
    }
    assert store.coverage_reads == [True, False], 'observation-only incremental builds reuse the last complete coverage model'
    assert service._cache is not None
    assert service._cache.generation.source_generation == 1


def test_incremental_build_does_not_rescan_retired_private_browser_history(tmp_path):
    path = tmp_path / storage.DATABASE_FILENAME
    now = [100_000.0]
    browser_history = tuple(
        storage.Observation(
            f"browser-{source}-{index}",
            "browser",
            f"browser:{source}",
            10_000.0 + index,
            f"browser:{source}",
            1,
            {"kind": "api"},
        )
        for source in range(materializer.MAX_PRIVATE_BROWSER_CLIENTS)
        for index in range(16)
    )
    current = storage.Observation(
        "cpu-current",
        "cpu",
        "web",
        now[0] - 0.25,
        "cpu:current",
        1,
        {"process_percent": 4, "system_percent": 20},
    )
    incremental_rows = []

    with storage.Store.open(path) as writer:
        writer.append_batch(observations=browser_history)
        with storage.Store.open_reader(path) as reader:
            service = service_module.StatsCurrentService(
                tmp_path / "statsd.sock",
                path,
                clock=lambda: now[0],
            )
            service._build_once(reader, True, frozenset())
            assert writer.append_observation(current) is True
            original_builder = service.incremental_builder

            def recording_builder(previous, snapshot, dirty, **kwargs):
                incremental_rows.append(snapshot.observations)
                return original_builder(previous, snapshot, dirty, **kwargs)

            service.incremental_builder = recording_builder
            service._build_once(
                reader,
                False,
                frozenset(service._dirty_cells((current,), ())),
            )

    assert incremental_rows == [(current,)]


def test_incremental_build_reuses_compacted_legacy_coverage_model(tmp_path, monkeypatch):
    now = [100_000.0]
    store = FakeStore()
    legacy_rows = tuple(
        storage.CoverageEpoch(
            "cpu",
            "retired:cpu",
            f"42:cpu:{started_at}",
            started_at,
            started_at + 1,
            1,
            42,
        )
        for started_at in range(60_000, 60_000 + 16_178)
    )
    cpu_rows = tuple(
        storage.CoverageEpoch(
            "cpu",
            "port:7443",
            f"stable:cpu:{index}",
            90_000 + (index * 2),
            None if index == 4_909 else 90_001 + (index * 2),
            1,
            42,
        )
        for index in range(4_910)
    )
    gpu_rows = tuple(
        storage.CoverageEpoch(
            "gpu",
            "gpu:0",
            f"stable:gpu:{index}",
            98_000 + index,
            None if index == 1_154 else 98_001 + index,
            10,
            42,
        )
        for index in range(1_155)
    )
    raw_coverage = tuple(sorted(
        (*legacy_rows, *cpu_rows, *gpu_rows),
        key=lambda item: (item.started_at, item.family, item.source_id, item.epoch_id),
    ))
    store.last_append = {"coverage_epochs": raw_coverage}
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: now[0],
    )
    coalesce_sizes = []
    original_coalesce = materializer._coalesce_coverage_epochs

    def recording_coalesce(coverage_epochs, unavailable_spans):
        coverage_epochs = tuple(coverage_epochs)
        coalesce_sizes.append(len(coverage_epochs))
        return original_coalesce(coverage_epochs, unavailable_spans)

    monkeypatch.setattr(materializer, "_coalesce_coverage_epochs", recording_coalesce)
    service._build_once(store, True, frozenset())

    current = storage.Observation(
        "cpu-current",
        "cpu",
        "port:7443",
        now[0] - 0.25,
        "inline:42:cpu:stable",
        42,
        {"process_percent": 4, "system_percent": 20},
    )
    store.source_generation += 1
    store.last_append = {"observations": (current,)}
    now[0] += 1
    service._build_once(
        store,
        False,
        frozenset(service._dirty_cells((current,), ())),
    )

    assert len(raw_coverage) == coalesce_sizes[0] == 22_243
    assert max(coalesce_sizes[1:]) == 6_066
    assert len(service._cached_coverage_epochs) == 6_066


def test_ring_change_detection_indexes_each_gap_once_at_live_scale():
    gap_count = 4_909

    class CountingGaps(tuple):
        def __new__(cls, values):
            return super().__new__(cls, values)

        def __init__(self, _values):
            self.visits = 0

        def __iter__(self):
            for item in super().__iter__():
                self.visits += 1
                yield item

    buckets = tuple(
        materializer.Bucket(start, 1, (), 0, None, None, True)
        for start in range(stats_resolution.RING_CAPACITIES[1])
    )
    gap_values = tuple(
        materializer.NoData(
            "cpu",
            f"source:{index}",
            f"epoch:{index}",
            (index % len(buckets)) + 0.1,
            (index % len(buckets)) + 0.9,
            1,
        )
        for index in range(gap_count)
    )
    previous_gaps = CountingGaps(gap_values)
    candidate_gaps = CountingGaps(gap_values)
    previous_layer = materializer.Layer(1, 0, len(buckets), buckets, previous_gaps)
    candidate_layer = materializer.Layer(1, 0, len(buckets), buckets, candidate_gaps)
    previous = materializer.Generation(1, 10, 100.0, 100.0, (previous_layer,))
    candidate = materializer.Generation(1, 20, 101.0, 101.0, (candidate_layer,))

    changed = service_module.StatsCurrentService._changed_ring_cells(previous, candidate)
    carrier_starts = {
        start for _range_seconds, start in service_module._ring_view_carriers(candidate_layer)
    }

    assert changed == frozenset(materializer.DirtyCell(1, start) for start in carrier_starts)
    assert previous_gaps.visits == gap_count
    assert candidate_gaps.visits == gap_count


@pytest.mark.parametrize("range_seconds", stats_resolution.RANGE_SECONDS)
def test_ring_no_data_index_matches_exact_bucket_clipping_for_every_range(range_seconds):
    resolution_seconds = stats_resolution.auto_resolution(range_seconds)
    layer_start = 100 * resolution_seconds
    buckets = tuple(
        materializer.Bucket(
            layer_start + (index * resolution_seconds),
            resolution_seconds,
            (),
            0,
            None,
            None,
            True,
        )
        for index in range(6)
    )
    layer_end = buckets[-1].start + resolution_seconds
    no_data = tuple(
        materializer.NoData(
            "cpu",
            f"source:{index}",
            f"epoch:{index}",
            start,
            end,
            resolution_seconds,
        )
        for index, (start, end) in enumerate((
            (layer_start - resolution_seconds, layer_start),
            (layer_start - (resolution_seconds / 2), layer_start + (resolution_seconds / 4)),
            (layer_start, layer_start + resolution_seconds),
            (layer_start + (resolution_seconds * 1.25), layer_start + (resolution_seconds * 1.75)),
            (layer_start + (resolution_seconds * 1.5), layer_start + (resolution_seconds * 3.5)),
            (layer_end - (resolution_seconds / 4), layer_end),
            (layer_end, layer_end + resolution_seconds),
        ))
    )
    layer = materializer.Layer(
        resolution_seconds,
        layer_start,
        layer_end,
        buckets,
        no_data,
    )

    indexed = service_module._ring_no_data_by_bucket(layer)

    assert {
        bucket.start: indexed.get(bucket.start, ())
        for bucket in buckets
    } == {
        bucket.start: service_module._ring_bucket_no_data(layer, bucket)
        for bucket in buckets
    }


def test_partial_reader_generation_is_pinned_before_later_append_commits(tmp_path):
    class RacingStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.observations = []
            self.block_reads = False
            self.read_entered = threading.Event()
            self.release_read = threading.Event()

        def append_batch(self, **values):
            result = super().append_batch(**values)
            self.observations.extend(values["observations"])
            return result

        @contextmanager
        def pinned_snapshot(self, *, dirty_intervals=None, include_coverage=True):
            self.reads += 1
            self.dirty_reads.append(dirty_intervals)
            pinned_generation = self.source_generation
            pinned_observations = tuple(self.observations)

            def read():
                if self.block_reads:
                    self.read_entered.set()
                    assert self.release_read.wait(2)
                selected = pinned_observations
                if dirty_intervals is not None:
                    selected = tuple(
                        item
                        for item in selected
                        if any(
                            start <= item.observed_at < end
                            for start, end in dirty_intervals
                        )
                    )
                return storage.StoreSnapshot(
                    storage.SchemaMetadata(5, 23, 1, pinned_generation),
                    selected, (), (), (), (),
                )

            yield read

    now = [100_000.0]
    store = RacingStore()
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: now[0],
    )
    service.writer = store
    service._build_once(store, True, frozenset())
    service._pending_full = False
    first, _binary = service.handle_with_binary(append_request(observations=[
        cpu_record("cpu-first", 99_990.25),
    ]))
    assert first["source_generation"] == 1
    first_work = service._take_work()
    assert first_work is not None
    store.block_reads = True
    build = threading.Thread(target=lambda: service._build_once(store, *first_work))
    build.start()
    assert store.read_entered.wait(1)
    append_started = threading.Event()
    append_done = threading.Event()

    def append_later():
        append_started.set()
        response, _binary = service.handle_with_binary(append_request(observations=[
            cpu_record("cpu-later", 99_995.25),
        ]))
        assert response["source_generation"] == 2
        append_done.set()

    later = threading.Thread(target=append_later)
    later.start()
    assert append_started.wait(1)
    assert append_done.wait(1) is True
    store.release_read.set()
    build.join(timeout=2)
    later.join(timeout=2)

    assert build.is_alive() is False
    assert later.is_alive() is False
    assert service._cache is not None
    assert service._cache.generation.source_generation == 1
    later_work = service._take_work()
    assert later_work is not None
    assert any(cell.start <= 99_995.25 < cell.start + cell.resolution for cell in later_work[1])


@pytest.mark.parametrize("warm_cache", [False, True], ids=["cold-cache", "warm-cache"])
def test_pinned_build_keeps_coverage_generation_atomic_across_append(tmp_path, warm_cache):
    class RacingCoverageStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.observations = []
            self.coverage = []
            self.block_reads = False
            self.read_entered = threading.Event()
            self.release_read = threading.Event()

        def append_batch(self, **values):
            result = super().append_batch(**values)
            self.observations.extend(values.get("observations", ()))
            self.coverage.extend(values.get("coverage_epochs", ()))
            return result

        @contextmanager
        def pinned_snapshot(self, *, dirty_intervals=None, include_coverage=True):
            self.coverage_reads.append(include_coverage)
            pinned_generation = self.source_generation
            pinned_observations = tuple(self.observations)
            pinned_coverage = tuple(self.coverage) if include_coverage else ()

            def read():
                if self.block_reads:
                    self.read_entered.set()
                    assert self.release_read.wait(2)
                return storage.StoreSnapshot(
                    storage.SchemaMetadata(5, 23, 1, pinned_generation),
                    pinned_observations, pinned_coverage, (), (), (),
                )

            yield read

    built = []

    def record_full(snapshot, **values):
        built.append((snapshot.schema.source_generation, snapshot.coverage_epochs))
        return materializer.build_generation(snapshot, **values)

    def record_incremental(previous, snapshot, dirty, **values):
        built.append((snapshot.schema.source_generation, snapshot.coverage_epochs))
        return materializer.update_generation(previous, snapshot, dirty, **values)

    base = storage.CoverageEpoch("cpu", "host", "inline:1:cpu:base", 99_980, 99_990, 1, 1)
    appended = storage.CoverageEpoch("cpu", "host", "inline:1:cpu:next", 99_990, 100_000, 1, 1)
    store = RacingCoverageStore()
    if warm_cache:
        store.append_batch(coverage_epochs=(base,))
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: 100_000.0,
        full_builder=record_full,
        incremental_builder=record_incremental,
    )
    service.writer = store
    first_work = service._take_work()
    assert first_work is not None
    if warm_cache:
        service._build_once(store, *first_work)
        service.handle_with_binary(append_request(observations=[cpu_record("trigger", 99_995.25)]))
        first_work = service._take_work()
        assert first_work is not None
    built.clear()
    store.block_reads = True
    build = threading.Thread(target=lambda: service._build_once(store, *first_work))
    build.start()
    assert store.read_entered.wait(1)
    response, _binary = service.handle_with_binary(append_request(coverage_epochs=[{
        "family": appended.family,
        "source_id": appended.source_id,
        "epoch_id": appended.epoch_id,
        "started_at": appended.started_at,
        "ended_at": appended.ended_at,
        "native_cadence_seconds": appended.native_cadence_seconds,
        "owner_generation": appended.owner_generation,
    }]))
    assert response["accepted"] == 1
    store.release_read.set()
    build.join(timeout=2)
    assert build.is_alive() is False

    assert built[-1] == ((2 if warm_cache else 0), (base,) if warm_cache else ())
    later_work = service._take_work()
    assert later_work is not None
    store.block_reads = False
    service._build_once(store, *later_work)
    assert built[-1][0] == (3 if warm_cache else 1)
    assert appended in built[-1][1]
    assert appended in service._cached_coverage_epochs


def test_coverage_only_append_schedules_empty_dirty_incremental_refresh(tmp_path):
    now = [100_000.0]
    store = FakeStore()
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: now[0],
    )
    service.writer = store
    service._build_once(store, True, frozenset())
    service._pending_full = False
    coverage = {
        "family": "cpu",
        "source_id": "host",
        "epoch_id": "cpu:1",
        "started_at": 99_990.0,
        "ended_at": 100_001.0,
        "native_cadence_seconds": 1.0,
        "owner_generation": 1,
    }

    response, _binary = service.handle_with_binary(
        append_request(coverage_epochs=[coverage]),
    )
    work = service._take_work()

    assert response.get("ok") is True, response
    assert service._pending_full is False
    assert work == (False, frozenset(), True)
    now[0] = 100_001.0
    service._build_once(store, *work)
    assert store.dirty_reads[-1] == ()
    assert store.coverage_reads[-1] is False, 'an accepted coverage append updates the retained model without a history rescan'
    assert service._cached_coverage_epochs[0].ended_at == 100_001.0
    assert service._full_builds == service._incremental_builds == 1


def test_duplicate_historical_usage_does_not_amplify_accepted_dirty_work(tmp_path):
    path = tmp_path / storage.DATABASE_FILENAME
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        path,
        clock=lambda: 100_000.0,
    )
    usage_payload = {
        "quantity": 1,
        "provider": "test",
        "model": "test",
        "agent_id": "test-agent",
        "telemetry_complete": True,
    }
    duplicates = tuple(
        storage.UsageAtom(
            f"duplicate:{index}",
            "output",
            "text",
            "none",
            "tokens",
            10_000 + ((index // 3) * 54),
            usage_payload,
        )
        for index in range(997)
    )
    expired = storage.UsageAtom(
        "expired",
        "output",
        "text",
        "none",
        "tokens",
        9_999.25,
        usage_payload,
    )
    accepted = storage.UsageAtom(
        "accepted",
        "output",
        "text",
        "none",
        "tokens",
        100_000.25,
        usage_payload,
    )
    coverage = storage.CoverageEpoch(
        "cpu", "web", "coverage:1", 99_999, None, 1, 1,
    )

    with storage.Store.open(path) as writer:
        writer.append_batch(usage_atoms=duplicates)
        with storage.Store.open_reader(path) as reader:
            service._build_once(reader, True, frozenset())
        service.writer = writer
        response = service._append_records(
            atoms=(*duplicates, expired, accepted),
            coverage=(coverage,),
        )
        service.writer = None

    assert response["counts"]["usage_atoms_accepted"] == 2
    assert response["counts"]["usage_atoms_duplicate"] == len(duplicates)
    assert response["counts"]["coverage_changed"] == 1
    assert len(service._pending_dirty) == len(stats_resolution.RESOLUTION_CHOICES)
    assert service._pending_dirty == service._dirty_cells((), (accepted,))
    assert service._pending_coverage_refresh is True


def test_resolution_publication_follows_one_ten_and_sixty_second_boundaries(tmp_path):
    now = [120_000.0]
    store = FakeStore()
    encoded = []

    def encode(wire):
        encoded.append((
            "delta" if "base_cache_generation" in wire else "snapshot",
            wire["resolution_seconds"],
        ))
        return json.dumps(wire, sort_keys=True).encode()

    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: now[0],
        encoder=encode,
    )
    service._view_demanded = lambda *args: True  # this test pins the all-views (fully demanded) contract
    service._build_once(store, True, frozenset())
    assert service._cache is not None
    assert {
        resolution: generation.generated_at
        for resolution, generation in service._cache.resolution_generations.items()
    } == {1: 120_000.0, 10: 120_000.0, 60: 120_000.0, 300: 120_000.0}
    encoded.clear()

    published_at = {}
    for offset in range(1, 61):
        now[0] = 120_000.0 + offset
        observation = storage.Observation(
            f"cpu-{offset}", "cpu", "web", now[0] - 0.1, "epoch", offset,
            {"process_percent": 4, "system_percent": 20},
        )
        store.source_generation = offset
        store.last_append = {"observations": (observation,)}
        service._build_once(
            store,
            False,
            frozenset(service._dirty_cells((observation,), ())),
        )
        assert service._cache is not None
        published_at[offset] = {
            resolution: generation.generated_at
            for resolution, generation in service._cache.resolution_generations.items()
        }

    assert published_at[1] == {1: 120_001.0, 10: 120_000.0, 60: 120_000.0, 300: 120_000.0}
    assert published_at[9][10] == 120_000.0
    assert published_at[10][10] == 120_010.0
    assert published_at[59][60] == published_at[59][300] == 120_000.0
    assert published_at[60][60] == published_at[60][300] == 120_060.0
    def expected_counts(resolution):
        publications = 60 // stats_resolution.live_cadence_seconds(resolution)
        snapshot_keys = sum(
            (resolution in stats_resolution.explicit_resolutions(range_seconds))
            + (stats_resolution.auto_resolution(range_seconds) == resolution)
            for range_seconds in stats_resolution.RANGE_SECONDS
        )
        delta_keys = sum(
            resolution in stats_resolution.explicit_resolutions(range_seconds)
            for range_seconds in stats_resolution.RANGE_SECONDS
        )
        return snapshot_keys * publications, delta_keys * publications

    assert {
        resolution: encoded.count(("snapshot", resolution))
        for resolution in stats_resolution.RESOLUTION_CHOICES
    } == {
        resolution: expected_counts(resolution)[0]
        for resolution in stats_resolution.RESOLUTION_CHOICES
    }
    assert {
        resolution: encoded.count(("delta", resolution))
        for resolution in stats_resolution.RESOLUTION_CHOICES
    } == {
        resolution: expected_counts(resolution)[1]
        for resolution in stats_resolution.RESOLUTION_CHOICES
    }


def test_stale_publish_is_rejected_without_replacing_current_cache(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    current = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 2), (), (), (), (), ()),
        source_generation=2,
        cache_generation=2,
        generated_at=100_001,
        observed_until=100_001,
    )
    stale = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 1), (), (), (), (), ()),
        source_generation=1,
        cache_generation=3,
        generated_at=100_002,
        observed_until=100_002,
    )
    assert service._publish(current, MappingProxyType({})) is True
    assert service._publish(stale, MappingProxyType({})) is False
    assert service._cache is not None
    assert service._cache.generation is current
    assert service._stale_builds == 1


def test_only_current_actions_exist_and_snapshot_rejects_retired_parameters(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
    )
    ping, _binary = service.handle_with_binary({**FENCE, "action": "ping"})
    status, _binary = service.handle_with_binary({**FENCE, "action": "status"})
    assert ping["code_revision"] == status["code_revision"] == revision.CURRENT_CODE_REVISION
    for action in ("history", "materialized_snapshot", "query_buckets", "merge_records", "diagnostics", "shutdown"):
        response, binary = service.handle_with_binary({**FENCE, "action": action})
        assert response["status"] == "unsupported"
        assert binary == b""
        assert service.stop_event.is_set() is False
    request = snapshot_request()
    request["history"] = "1"
    response, _binary = service.handle_with_binary(request)
    assert response["status"] == "unsupported"


def test_cli_rejects_noncanonical_database_filename(tmp_path):
    with pytest.raises(SystemExit) as raised:
        service_module.main(["--serve", "--database", str(tmp_path / "stats.sqlite3")])
    assert raised.value.code == 2


def test_browser_views_remain_shared_regardless_of_private_demand(tmp_path):
    monotonic_now = [1_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        monotonic=lambda: monotonic_now[0],
    )
    active = service_module._private_id("browser-active", "test.client")
    stale = service_module._private_id("browser-stale", "test.client")
    observations = tuple(
        storage.Observation(f"event:{client}", "browser", client, 99_999, f"epoch:{client}", 1,
                            {"kind": "api", "latency_ms": 2})
        for client in (active, stale)
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 1), observations, (), (), (), ()),
        source_generation=1, cache_generation=10,
        generated_at=100_000, observed_until=100_000,
    )
    assert generation.private_source_ids == ()

    service._record_private_demand(active)
    monotonic_now[0] += service_module.PRIVATE_DEMAND_GRACE_SECONDS + 1
    clients = {key[2] for key in service._encode_generation(generation)}
    assert clients == {None}

    monotonic_now[0] += service_module.PRIVATE_DEMAND_GRACE_SECONDS + 1
    assert {key[2] for key in service._encode_generation(generation)} == {None}


def test_encode_accounting_and_full_build_reason_are_reported(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), ()),
        source_generation=0, cache_generation=1,
        generated_at=100_000, observed_until=100_000,
    )
    service._encode_generation(generation, resolutions=frozenset({1}))
    accounting = service._last_encode_accounting
    # 5m is the only range offering 1s: ONE slice serves the explicit entry and its
    # AUTO alias (two entries, one alias reuse, 300 bucket visits for the 5m/1s layer).
    assert accounting["slices"] == 1
    assert accounting["alias_reuses"] == 1
    assert accounting["entries"] == 2
    assert accounting["bucket_visits"] == len(
        materializer.slice_generation(generation, 300, 1).buckets
    )
    assert accounting["bytes"] > 0
    assert service._encode_totals["entries"] == 2
    # Every full build carries an explicit reason; the initial pending build is startup.
    assert service._pending_full_reason == "startup"
    assert service._last_full_build_reason == ""  # no build ran through _build_once yet


def test_one_cpu_append_dirties_exactly_one_cell_per_resolution(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    observation = storage.Observation(
        "cpu-1", "cpu", "host", 100_123.4, "epoch", 1,
        {"process_percent": 2, "system_percent": 4},
    )
    dirty = service._dirty_cells((observation,), ())
    # One committed original touches exactly one bucket per concrete resolution
    # (the cell containing its timestamp), never a range of cells or a full layer.
    assert dirty == {
        materializer.DirtyCell(resolution, math.floor(100_123.4 / resolution) * resolution)
        for resolution in stats_resolution.RESOLUTION_CHOICES
    }
    assert len(dirty) == len(stats_resolution.RESOLUTION_CHOICES)


def test_public_encode_is_demand_gated_and_recovers_on_next_request(tmp_path):
    """Idle builds retain stale entries while returning demand refreshes them."""
    monotonic_now = [1_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        monotonic=lambda: monotonic_now[0],
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), ()),
        source_generation=0, cache_generation=1,
        generated_at=100_000, observed_until=100_000,
    )

    # Startup counts as demand: within the grace the encode runs.
    assert service._has_public_demand() is True
    resolutions = service._publication_resolutions(generation)
    service._publish(generation, service._encode_generation(generation, resolutions=resolutions), resolutions=resolutions)
    assert len(service._cache.entries) > 0

    # Grace expires with no request: the next published generation encodes no
    # public entries, but the generation itself advances (incremental base)
    # and the previous entries are RETAINED as stale bodies, so a returning
    # client is served immediately instead of getting pending.
    monotonic_now[0] += service_module.PRIVATE_DEMAND_GRACE_SECONDS + 1
    assert service._has_public_demand() is False
    stale_generation = service._cache.generation.cache_generation
    newer = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 2, 0), (), (), (), (), ()),
        source_generation=2, cache_generation=2_000,
        generated_at=100_001, observed_until=100_001,
    )
    resolutions = service._publication_resolutions(newer)
    service._publish(newer, {}, resolutions=resolutions)
    assert service._cache.generation is newer
    assert all(
        int(entry.metadata["cache_generation"]) == stale_generation
        for entry in service._cache.entries.values()
    )

    # A snapshot request while idle: the retained stale body serves instantly,
    # and demand is recorded so the next build encodes fresh.
    metadata, _binary = service._snapshot({"range_seconds": "300", "resolution": "1", "client_id": "b" * 64})
    assert metadata["ok"] is True
    assert int(metadata["cache_generation"]) == stale_generation
    assert service._has_public_demand() is True
    assert service._view_demanded(300, 1) is True

    current, current_binary = service._delta({
        **delta_request(after_cache_generation=stale_generation),
        "client_id": "b" * 64,
    })
    assert current["not_modified"] is True
    assert current["cache_generation"] == stale_generation
    assert current_binary == b""

    refreshed = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 3, 0), (), (), (), (), ()),
        source_generation=3, cache_generation=3_000,
        generated_at=100_002, observed_until=100_002,
    )
    encoded = service._encode_generation(
        refreshed,
        resolutions=frozenset({1}),
        previous_generated_at=newer.generated_at,
    )
    service._publish(refreshed, encoded, resolutions=frozenset({1}))
    advanced, advanced_binary = service._delta({
        **delta_request(after_cache_generation=stale_generation),
        "client_id": "b" * 64,
    })
    advanced_wire = protocol.validate_delta(json.loads(advanced_binary))
    assert advanced["base_cache_generation"] == stale_generation
    assert advanced_wire["base_cache_generation"] == stale_generation
    assert advanced_wire["cache_generation"] == refreshed.cache_generation


def test_first_build_warms_every_view_even_when_startup_exceeds_demand_grace(tmp_path):
    monotonic_now = [10_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: 100_000.0,
        monotonic=lambda: monotonic_now[0],
    )
    service._last_public_demand = monotonic_now[0] - service_module.PRIVATE_DEMAND_GRACE_SECONDS - 1
    service._build_once(FakeStore(), True, frozenset())
    assert service._has_public_demand() is False
    assert service._cache is not None
    expected_views = sum(
        1 + len(stats_resolution.explicit_resolutions(range_seconds))
        for range_seconds in stats_resolution.RANGE_SECONDS
    )
    assert len(service._cache.entries) == expected_views


def test_missing_exact_view_wakes_and_forces_next_publication(tmp_path):
    now = [120_001.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: now[0],
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), ()),
        source_generation=0, cache_generation=1,
        generated_at=120_000.0, observed_until=120_000.0,
    )
    service._publish(
        generation,
        service._encode_generation(generation, resolutions=frozenset({60})),
        resolutions=frozenset(stats_resolution.RESOLUTION_CHOICES),
    )
    assert (7200, 300, None) not in service._cache.entries

    metadata, binary = service._snapshot({
        "range_seconds": "7200", "resolution": "300", "client_id": "a" * 64,
    })
    assert metadata["status"] == "pending"
    assert binary == b""
    assert service.work_event.is_set()
    assert materializer.DirtyCell(300, 120_000) in service._pending_dirty

    candidate = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), ()),
        source_generation=0, cache_generation=2,
        generated_at=now[0], observed_until=now[0],
    )
    resolutions = service._publication_resolutions(candidate)
    assert 300 in resolutions
    entries = service._encode_generation(
        candidate,
        resolutions=resolutions,
        previous_generated_at=generation.generated_at,
    )
    service._publish(candidate, entries, resolutions=resolutions)
    metadata, binary = service._snapshot({
        "range_seconds": "7200", "resolution": "300", "client_id": "a" * 64,
    })
    assert metadata["ok"] is True
    assert binary


def test_appends_are_accepted_while_startup_build_is_still_pending(tmp_path):
    """Writers stay responsive before the first generation exists: an append is
    accepted and durably stored while snapshots still answer pending."""
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: 100_000.0,
    )
    writer = storage.Store.open(tmp_path / storage.DATABASE_FILENAME)
    service.writer = writer
    try:
        observation = storage.Observation(
            "startup-1", "cpu", "web", 99_999.25, "epoch", 1,
            {"process_percent": 4, "system_percent": 20},
        )
        payload = client_module._append_payload((observation,), (), (), ())
        response = service._append({
            "action": "append",
            "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
            "schema_generation": storage.SCHEMA_VERSION,
            **payload,
        })
        assert response["ok"] is True
        assert response["accepted"] == 1
        assert response["counts"]["observations_accepted"] == 1
        # No generation has been published yet: readers get pending, not errors.
        metadata, binary = service._snapshot({"range_seconds": "300", "resolution": "1", "client_id": "a" * 64})
        assert metadata["status"] == "pending"
        assert binary == b""
        # The append marked work: the startup build will fold it when it runs.
        assert service._pending_dirty or service._pending_full
    finally:
        writer.close()


def test_browser_profiles_are_queried_from_durable_observations_in_statsd(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    writer = storage.Store.open(tmp_path / storage.DATABASE_FILENAME)
    service.writer = writer
    try:
        records = (
            storage.Observation(
                "api-1", "browser", "browser-private", 100.0, "page-1", 0,
                {
                    "kind": "api", "endpoint": "/api/session-metadata", "method": "GET",
                    "request_id": "r-web-1", "status": 200, "ttfb_ms": 8400,
                    "latency_ms": 8553.6, "queue_ms": 3200,
                    "journey_id": "j-1", "code_revision": "rev-1",
                    "browser_family": "chromium", "connection_protocol": "h2",
                },
            ),
            storage.Observation(
                "sse-1", "browser", "browser-private", 101.0, "page-1", 0,
                {"kind": "sse", "latency_ms": 2},
            ),
            storage.Observation(
                "load-1", "browser", "browser-private", 102.0, "page-1", 0,
                {"kind": "page_load", "endpoint": "/", "interactive_ms": 240, "fanout_count": 9},
            ),
            storage.Observation(
                "api-2", "browser", "browser-private", 103.0, "page-1", 0,
                {
                    "kind": "api", "endpoint": "/api/ping", "method": "GET",
                    "latency_ms": 4000, "queue_ms": 3600, "journey_id": "j-2",
                    "code_revision": "rev-1", "browser_family": "chromium",
                    "connection_protocol": "http/1.1",
                },
            ),
        )
        response = service._append({
            "action": "append",
            "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
            "schema_generation": storage.SCHEMA_VERSION,
            **client_module._append_payload(records, (), (), ()),
        })
        profiles, binary = service.handle_with_binary({
            **FENCE,
            "action": "browser_profiles",
        })
    finally:
        writer.close()

    assert response["accepted"] == 4
    assert binary == b""
    assert profiles["ok"] is True
    assert profiles["profiles"] == {
        "retained": 3,
        "maximum": service_module.MAX_BROWSER_PROFILES,
        "items": (
            {"observed_at": 103.0, "kind": "api", "endpoint": "/api/ping", "method": "GET", "latency_ms": 4000, "queue_ms": 3600, "journey_id": "j-2", "code_revision": "rev-1", "browser_family": "chromium", "connection_protocol": "http/1.1"},
            {"observed_at": 102.0, "kind": "page_load", "endpoint": "/", "interactive_ms": 240, "fanout_count": 9},
            {"observed_at": 100.0, "kind": "api", "endpoint": "/api/session-metadata", "method": "GET", "request_id": "r-web-1", "status": 200, "ttfb_ms": 8400, "latency_ms": 8553.6, "queue_ms": 3200, "journey_id": "j-1", "code_revision": "rev-1", "browser_family": "chromium", "connection_protocol": "h2"},
        ),
        "queue_ms": {
            "count": 2, "average_ms": 3400.0, "p50_ms": 3200.0,
            "p95_ms": 3600.0, "p99_ms": 3600.0, "maximum_ms": 3600.0,
            "histogram": (
                {"upper_bound_ms": 25, "count": 0},
                {"upper_bound_ms": 100, "count": 0},
                {"upper_bound_ms": 250, "count": 0},
                {"upper_bound_ms": 1000, "count": 0},
                {"upper_bound_ms": 3000, "count": 0},
                {"upper_bound_ms": 10000, "count": 2},
                {"upper_bound_ms": None, "count": 0},
            ),
            "dimensions": (
                {"code_revision": "rev-1", "browser_family": "chromium", "count": 2, "average_ms": 3400.0, "maximum_ms": 3600.0},
            ),
            "slow_exemplars": (
                {"observed_at": 103.0, "queue_ms": 3600.0, "latency_ms": 4000.0, "endpoint": "/api/ping", "journey_id": "j-2", "code_revision": "rev-1", "browser_family": "chromium", "connection_protocol": "http/1.1"},
                {"observed_at": 100.0, "queue_ms": 3200.0, "latency_ms": 8553.6, "endpoint": "/api/session-metadata", "request_id": "r-web-1", "journey_id": "j-1", "code_revision": "rev-1", "browser_family": "chromium", "connection_protocol": "h2"},
            ),
        },
    }
    assert profiles["observation_status"]["retained_observations"] == 4


def test_browser_observation_status_distinguishes_current_receipts_from_retained_failures(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME, clock=lambda: 150.0,
    )
    writer = storage.Store.open(tmp_path / storage.DATABASE_FILENAME)
    service.writer = writer
    try:
        records = (
            storage.Observation(
                "heartbeat-1", "browser", "browser-private", 100.0, "page-1", 0,
                {"kind": "heartbeat", "upload_queue_depth": 0, "upload_drops": 0, "upload_retries": 0},
            ),
            storage.Observation(
                "error-1", "browser", "browser-private", 110.0, "page-1", 0,
                {"kind": "error", "signature": "jsf-unknown", "message": "render failed", "source": "/static/yolomux.js", "code_revision": "old-revision"},
            ),
            storage.Observation(
                "error-2", "browser", "browser-private", 120.0, "page-1", 0,
                {"kind": "error", "signature": "jsf-probe", "message": "controlled throw", "source": "/static/yolomux.js", "code_revision": "old-revision", "provenance": "controlled_probe"},
            ),
            storage.Observation(
                "rejection-1", "browser", "browser-private", 130.0, "page-1", 0,
                {"kind": "unhandledrejection", "signature": "jsf-real", "message": "promise failed", "source": "/static/yolomux.js", "code_revision": "old-revision", "provenance": "confirmed_real"},
            ),
        )
        service._append({
            "action": "append",
            "protocol_version": protocol.WIRE_PROTOCOL_VERSION,
            "schema_generation": storage.SCHEMA_VERSION,
            **client_module._append_payload(records, (), (), ()),
        })
        diagnostics, binary = service.handle_with_binary({
            **FENCE,
            "action": "browser_profiles",
        })
    finally:
        writer.close()

    assert binary == b""
    assert diagnostics["observation_status"] == {
        "receipt_scope": "statsd_process",
        "receipt_scope_started_at": service.started_at,
        "accepted_reports": 0,
        "accepted_observations": 0,
        "last_accepted_at": None,
        "last_accepted_age_seconds": None,
        "retained_observations": 4,
        "retained_failures": 3,
        "confirmed_real_failures": 1,
        "probe_failures": 1,
        "unknown_failures": 1,
        "retained_errors": 2,
        "retained_unhandled_rejections": 1,
        "last_retained_observed_at": 130.0,
        "last_retained_observed_age_seconds": 20.0,
        "fingerprints": (
            {"signature": "jsf-real", "kind": "unhandledrejection", "provenance": "confirmed_real", "count": 1, "first_observed_at": 130.0, "last_observed_at": 130.0, "code_revisions": ("old-revision",), "state": "open", "state_reason": "no durable closure or path-execution evidence"},
            {"signature": "jsf-probe", "kind": "error", "provenance": "controlled_probe", "count": 1, "first_observed_at": 120.0, "last_observed_at": 120.0, "code_revisions": ("old-revision",), "state": "open", "state_reason": "no durable closure or path-execution evidence"},
            {"signature": "jsf-unknown", "kind": "error", "provenance": "unknown", "count": 1, "first_observed_at": 110.0, "last_observed_at": 110.0, "code_revisions": ("old-revision",), "state": "open", "state_reason": "no durable closure or path-execution evidence"},
        ),
        "classification_counts": {"open": 3, "fixed": 0, "live_verified": 0},
        "unprovable_states": ("fixed", "live_verified"),
    }


def test_encoding_targets_only_demanded_views_between_slow_refreshes(tmp_path):
    """A demanded concrete view and its AUTO alias encode at live cadence; other views
    refresh together only when the 60s undemanded boundary advances, and their
    retained bodies keep serving in between (instant range switches)."""
    monotonic_now = [1_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        monotonic=lambda: monotonic_now[0],
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 0), (), (), (), (), ()),
        source_generation=1, cache_generation=1,
        generated_at=100_000, observed_until=100_000,
    )

    # Demand exactly the 5m/1s concrete view; its indistinguishable AUTO cursor
    # must publish in the same generation for the shared delta key.
    service._record_view_demand(300, 1)
    entries = service._encode_generation(
        generation, resolutions=frozenset(stats_resolution.RESOLUTION_CHOICES),
        previous_generated_at=100_000 - 1,  # same 60s window: no slow refresh
    )
    assert set(entries) == {(300, 1, None), (300, stats_resolution.AUTO, None)}
    assert (
        entries[(300, 1, None)].metadata["cache_generation"]
        == entries[(300, stats_resolution.AUTO, None)].metadata["cache_generation"]
    )

    # The slow boundary advance refreshes every view in one build.
    full = service._encode_generation(
        generation, resolutions=frozenset(stats_resolution.RESOLUTION_CHOICES),
        previous_generated_at=100_000 - service_module.UNDEMANDED_ENCODE_SECONDS,
    )
    assert len(full) > len(entries)
    assert (86400, 300, None) in full
    assert (300, stats_resolution.AUTO, None) in full

    # A full/first build (no previous generation) always encodes everything.
    cold = service._encode_generation(generation, previous_generated_at=None)
    assert set(cold) == set(full)


def test_wire_bucket_fragments_are_reused_for_unchanged_bucket_objects(tmp_path, monkeypatch):
    """An advancing tail must not rebuild hundreds of unchanged bucket wire
    dicts: unchanged (identical frozen) bucket objects hit the identity memo,
    and the memoized body is byte-for-byte identical to a fresh build."""
    generation = materializer.build_generation(
        storage.StoreSnapshot(
            storage.SchemaMetadata(5, 23, 1, 1),
            tuple(
                storage.Observation(f"event-{index}", "cpu", "web", 99_800 + index, "epoch", 1,
                                    {"process_percent": index % 7, "system_percent": 20})
                for index in range(50)
            ),
            (), (), (), (),
        ),
        source_generation=1, cache_generation=1,
        generated_at=100_000, observed_until=100_000,
    )
    layer = materializer.slice_generation(generation, 300, 1)
    populated = [bucket for bucket in layer.buckets if bucket.source_count]
    assert populated

    service_module._WIRE_BUCKET_CACHE.clear()
    first = [service_module._wire_bucket(bucket) for bucket in populated]

    builds = []
    real_build = service_module._build_wire_bucket
    monkeypatch.setattr(service_module, "_build_wire_bucket", lambda bucket: builds.append(bucket) or real_build(bucket))
    second = [service_module._wire_bucket(bucket) for bucket in populated]
    assert builds == []  # every unchanged bucket came from the memo
    assert json.dumps(second, sort_keys=True) == json.dumps(first, sort_keys=True)
    assert json.dumps(second, sort_keys=True) == json.dumps([real_build(bucket) for bucket in populated], sort_keys=True)


def test_four_browser_sources_still_encode_one_shared_view_set(tmp_path):
    monotonic_now = [1_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        monotonic=lambda: monotonic_now[0],
    )
    clients = [chr(ord("a") + index) * 64 for index in range(4)]
    private_ids = [service_module._private_id(client, "test.client") for client in clients]
    observations = tuple(
        storage.Observation(f"event:{private_id}", "browser", private_id, 99_999, f"epoch:{private_id}", 1,
                            {"kind": "api", "latency_ms": 2})
        for private_id in private_ids
    )
    generation = materializer.build_generation(
        storage.StoreSnapshot(storage.SchemaMetadata(5, 23, 1, 1), observations, (), (), (), ()),
        source_generation=1, cache_generation=10,
        generated_at=100_000, observed_until=100_000,
    )
    for private_id in private_ids:
        service._record_private_demand(private_id)

    entries = service._encode_generation(generation)  # full/first build: all views
    accounting = service._last_encode_accounting

    views_per_client = sum(
        len(stats_resolution.explicit_resolutions(range_seconds))
        for range_seconds in stats_resolution.RANGE_SECONDS
    )
    auto_aliases_per_client = len(stats_resolution.RANGE_SECONDS)
    expected_entries = views_per_client + auto_aliases_per_client
    assert accounting["slices"] == views_per_client
    assert accounting["alias_reuses"] == auto_aliases_per_client
    assert accounting["entries"] == expected_entries
    assert len(entries) == expected_entries
    assert {key[2] for key in entries} == {None}


def test_snapshot_wire_includes_only_the_latest_scanner_backfill_status(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    body = b'{"protocol_version":2}'

    assert service._snapshot_body_with_backfill_status(body) == body
    scan = {
        "files_read": 1, "records_parsed": 2, "atoms_emitted": 3,
        "atoms_accepted": 2, "atoms_rejected": 1,
        "rejection_reasons": {"invalid model": 1},
    }
    service._usage_atom_backfill = {"state": "pending", "sources": 2, "missing": 1, "scan": scan}
    assert json.loads(service._snapshot_body_with_backfill_status(body))["usage_atom_backfill"] == {
        "state": "pending", "sources": 2, "missing": 1, "scan": scan,
    }
    service._usage_atom_backfill = {"state": "complete", "sources": 2, "missing": 0, "scan": scan}
    assert json.loads(service._snapshot_body_with_backfill_status(body))["usage_atom_backfill"]["state"] == "complete"


def test_usage_atom_backfill_control_publishes_scan_counters(tmp_path):
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    scan = {
        "files_read": 3, "records_parsed": 12, "atoms_emitted": 4,
        "atoms_accepted": 3, "atoms_rejected": 1,
        "rejection_reasons": {"direction must be one of: input, output": 1},
    }

    response, binary = service.handle_with_binary({
        **FENCE, "action": "usage_atom_backfill", "state": "pending",
        "sources": 7, "missing": 4, "scan": scan,
    })

    assert response == {"ok": True}
    assert binary == b""
    assert service._usage_atom_backfill == {
        "state": "pending", "sources": 7, "missing": 4, "scan": scan,
    }
