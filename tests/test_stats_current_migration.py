# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for the atomic current-only stats migration boundary."""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from yolomux_lib.stats_current import migration
from yolomux_lib.stats_current import identity
from yolomux_lib.stats_current.storage import DATABASE_FILENAME
from yolomux_lib.stats_current.storage import RETENTION_SECONDS
from yolomux_lib.stats_current.storage import WRITER_FENCE_FILENAME
from yolomux_lib.stats_current.storage import Observation
from yolomux_lib.stats_current.storage import Store
from yolomux_lib.stats_current.storage import SchemaTooNewError
from yolomux_lib.stats_current.storage import UsageAtom


def _component(event_id: str, quantity: int, *, timestamp: float = 100) -> dict[str, object]:
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "direction": "input",
        "modality": "text",
        "cache_role": "read",
        "unit": "tokens",
        "quantity": quantity,
        "provider": "openai",
        "model": "gpt-5",
        "tmux_key": "agent-a",
        "telemetry_complete": True,
        "micro_usd": 999,
    }


def _create_spool(path: Path, *, event_id: str = "spool", quantity: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE atoms (bucket_start INTEGER, duration INTEGER, event_key TEXT, payload TEXT)"
    )
    connection.execute(
        "INSERT INTO atoms VALUES(?,?,?,?)",
        (100, 10, event_id, json.dumps(_component(event_id, quantity))),
    )
    connection.commit()
    connection.close()


def _create_legacy_database(path: Path, *, unsupported_table: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    spool = path.parent / "services" / "statsd-agent-token-scan-test.atoms.sqlite3"
    _create_spool(spool)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE stats_buckets (
          start INTEGER NOT NULL, duration INTEGER NOT NULL, sequence INTEGER NOT NULL,
          server_sequence INTEGER NOT NULL, bucket_json TEXT NOT NULL,
          PRIMARY KEY(start,duration));
        CREATE TABLE stats_coverage_intervals (
          family TEXT NOT NULL, epoch_id TEXT NOT NULL, start INTEGER NOT NULL,
          end INTEGER NOT NULL, cadence INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
          source TEXT NOT NULL, PRIMARY KEY(family,epoch_id,start));
        CREATE TABLE stats_raw_samples (
          family TEXT NOT NULL, source_id TEXT NOT NULL, sample_time REAL NOT NULL,
          epoch_id TEXT NOT NULL, owner_generation INTEGER NOT NULL, payload_json TEXT NOT NULL,
          PRIMARY KEY(family,source_id,sample_time));
        CREATE TABLE stats_usage_atoms (
          event_id TEXT NOT NULL, direction TEXT NOT NULL, modality TEXT NOT NULL,
          cache_role TEXT NOT NULL, unit TEXT NOT NULL, sample_time REAL NOT NULL,
          atom_json TEXT NOT NULL,
          PRIMARY KEY(event_id,direction,modality,cache_role,unit));
        CREATE TABLE stats_rollups (start INTEGER, duration INTEGER, bucket_json TEXT);
        """
    )
    connection.execute(
        "INSERT INTO schema_meta VALUES(?,?)",
        ("agent_token_atom_spool", json.dumps({"path": str(spool)})),
    )
    connection.execute("INSERT INTO schema_meta VALUES('schema_version','4')")
    connection.execute("INSERT INTO schema_meta VALUES('raw_schema_version','1')")
    connection.execute(
        "INSERT INTO stats_coverage_intervals VALUES(?,?,?,?,?,?,?)",
        ("cpu", "cpu-live", 90, 110, 1, 2, "sampler"),
    )
    connection.execute(
        "INSERT INTO stats_raw_samples VALUES(?,?,?,?,?,?)",
        (
            "cpu", "web", 105, "raw-epoch", 2,
            json.dumps({"process_percent": 4, "system_percent": 20}),
        ),
    )
    connection.execute(
        "INSERT INTO stats_usage_atoms VALUES(?,?,?,?,?,?,?)",
        ("table", "input", "text", "read", "tokens", 100, json.dumps(_component("table", 5))),
    )
    host = {
        "system_memory_used_total_bytes": 100,
        "system_memory_capacity_total_bytes": 200,
        "system_memory_count": 1,
        "gpu_devices": {"gpu:0": {
            "label": "GPU 0", "util_total_percent": 25, "memory_used_total_bytes": 50,
            "memory_capacity_total_bytes": 100, "samples": 1,
        }},
        "service_load": {"statsd": {
            "cpu_total_percent": 3, "cpu_samples": 1,
            "rss_total_bytes": 400, "rss_samples": 1,
        }},
    }
    bucket = {
        "start": 100, "duration": 10,
        "cpu_total_percent": 50, "cpu_count": 10,
        "system_cpu_total_percent": 200, "system_cpu_count": 10,
        "agent_activity_samples": 1,
        "agent_token_samples": 1,
        "agent_token_rates": {"agent-a": {"tokens": 5}},
        "host_metrics": host,
        "clients": {"browser-a": {"api_count": 2, "latency_count": 1}},
        "cost_summary": {"components": [_component("bucket", 7)]},
    }
    connection.execute(
        "INSERT INTO stats_buckets VALUES(?,?,?,?,?)",
        (100, 10, 1, 1, json.dumps(bucket)),
    )
    if unsupported_table:
        connection.execute("CREATE TABLE mystery_facts(value TEXT)")
    connection.commit()
    connection.close()
    return path


def _create_live_schema2_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE stats_buckets (
          start INTEGER NOT NULL, duration INTEGER NOT NULL, sequence INTEGER NOT NULL,
          server_sequence INTEGER NOT NULL, bucket_json TEXT NOT NULL,
          PRIMARY KEY(start,duration));
        CREATE TABLE stats_clients (
          start INTEGER, duration INTEGER, client_id TEXT, sequence INTEGER, values_json TEXT);
        CREATE TABLE stats_processes (
          start INTEGER, duration INTEGER, process_id TEXT, sequence INTEGER, values_json TEXT);
        CREATE TABLE stats_agent_rates (
          start INTEGER, duration INTEGER, rate_key TEXT, values_json TEXT);
        CREATE TABLE stats_host_metrics (
          start INTEGER, duration INTEGER, metric_key TEXT, values_json TEXT);
        """
    )
    connection.executemany(
        "INSERT INTO schema_meta VALUES(?,?)",
        (
            ("schema_version", "2"),
            ("legacy_import_version", "1"),
            ("agent_token_history_recovery_version", "1"),
            ("agent_token_state", json.dumps({
                "agent-a": {
                    "identity": "codex:1:2:fixture",
                    "label": "agent-a",
                    "source": "transcript",
                    "time": 100,
                    "tokens": 25,
                },
            })),
        ),
    )
    bucket = {
        "start": 100,
        "duration": 10,
        "cpu_total_percent": 50,
        "cpu_count": 10,
        "system_cpu_total_percent": 200,
        "system_cpu_count": 10,
        "agent_token_samples": 1,
        "agent_token_rates": {"agent-a": {"tokens": 5}},
    }
    connection.execute(
        "INSERT INTO stats_buckets VALUES(?,?,?,?,?)",
        (100, 10, 1, 1, json.dumps(bucket)),
    )
    connection.commit()
    connection.close()
    return path


def _file_state(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def test_schema4_database_migrates_exact_facts_and_marks_lost_aggregates(tmp_path):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    before = _file_state(legacy)
    supplied = UsageAtom("image", "output", "image", "none", "requests", 101, {
        "quantity": 1, "provider": "openai", "model": "gpt-image",
        "agent_id": "agent-a", "telemetry_complete": False,
    })

    report = migration.migrate(
        migration.MigrationInputs(state, usage_atoms=(supplied,)), completed_at=200,
    )

    assert report.active_database == state / DATABASE_FILENAME
    assert report.already_active is False
    assert before[0].startswith(b"SQLite format 3")
    assert not legacy.exists()
    assert not any((state / "services").glob("statsd-agent-token-scan-*"))
    assert not (state / migration.RETIREMENT_ARCHIVE_FILENAME).exists()
    assert not (state / migration.RETIREMENT_JOURNAL_FILENAME).exists()
    with Store.open(report.active_database) as store:
        snapshot = store.read_snapshot()
    assert {item.family for item in snapshot.observations} == {
        "cpu", "gpu", "service_load", "system_memory",
    }
    assert {item.event_id for item in snapshot.usage_atoms} == {"table", "bucket", "spool", "image"}
    assert all("micro_usd" not in item.payload for item in snapshot.usage_atoms)
    unavailable = {item.family for item in snapshot.unavailable_spans}
    assert {"cpu", "agent_status", "browser", "agent_tokens"} <= unavailable
    assert "cost" not in unavailable
    assert len(snapshot.migration_reconciliation) == 1
    assert snapshot.migration_reconciliation[0].source_digest == report.source_digest
    assert any(issue.kind == "derived_table" for issue in report.issues)
    connection = sqlite3.connect(report.active_database)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
    connection.close()


def test_live_schema2_database_with_zero_sqlite_headers_migrates(tmp_path):
    state = tmp_path / "state"
    legacy = _create_live_schema2_database(
        state / migration.RETIRED_DATABASE_FILENAME,
    )
    connection = sqlite3.connect(legacy)
    assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    connection.close()

    report = migration.migrate(
        migration.MigrationInputs(state), completed_at=200,
    )

    assert report.active_database.is_file()
    assert not legacy.exists()
    assert any(
        issue.kind == "retired_schema" and issue.detail == "2"
        for issue in report.issues
    )
    assert any(
        issue.kind == "retired_marker" and issue.detail == "agent_token_state"
        for issue in report.issues
    )
    with Store.open_reader(report.active_database) as reader:
        snapshot = reader.read_snapshot()
    assert any(
        item.family == "agent_tokens"
        and item.reason == "token aggregates are not source atoms"
        for item in snapshot.unavailable_spans
    )


def test_fresh_install_activates_the_same_reconciled_current_schema(tmp_path):
    state = tmp_path / "state"

    first = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert first.active_database == state / DATABASE_FILENAME
    assert first.already_active is False
    assert (
        first.observations,
        first.coverage_epochs,
        first.usage_atoms,
        first.unavailable_spans,
        first.issue_count,
    ) == (0, 0, 0, 0, 0)
    assert (state / WRITER_FENCE_FILENAME).is_file()
    with Store.open_reader(first.active_database) as reader:
        snapshot = reader.read_snapshot()
    assert snapshot.schema.schema_version == migration.SCHEMA_VERSION
    assert len(snapshot.migration_reconciliation) == 1

    second = migration.migrate(migration.MigrationInputs(state), completed_at=300)
    assert second.already_active is True
    assert second.source_digest == first.source_digest


@pytest.mark.parametrize(("filename", "version"), (
    ("stats-client-history.json", 2),
    ("stats-client-history-v3.json", 3),
    ("stats-client-history-v4.json", 4),
))
def test_json_only_inputs_recover_exact_cpu_and_mark_browser_events_unavailable(
    tmp_path, filename, version,
):
    state = tmp_path / "state"
    state.mkdir()
    journal = state / filename
    journal.write_text(json.dumps({
        "version": version,
        "raw_buckets": [[100, 1, 1, {"browser-a": {"api_count": 2}}]],
        "rollup_buckets": [],
    }), encoding="utf-8")
    shared = {
        "start": 100, "duration": 1,
        "cpu_total_percent": 5, "cpu_count": 1,
        "system_cpu_total_percent": 20, "system_cpu_count": 1,
    }
    (state / "tmux-AI-status.json").write_text(json.dumps({
        "attention_acks": {"unrelated": True},
        "stats_history": {"raw_buckets": [shared], "rollup_buckets": []},
    }), encoding="utf-8")

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    with Store.open(report.active_database) as store:
        snapshot = store.read_snapshot()
    assert [(item.family, dict(item.payload)) for item in snapshot.observations] == [
        ("cpu", {"process_percent": 5.0, "system_percent": 20.0}),
    ]
    assert [item.family for item in snapshot.unavailable_spans] == ["browser"]
    assert not journal.exists()
    retained = json.loads((state / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert retained == {"attention_acks": {"unrelated": True}}


def test_coarse_bucket_never_expands_into_fake_observations(tmp_path):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    connection = sqlite3.connect(legacy)
    connection.execute("DELETE FROM stats_raw_samples")
    connection.commit()
    connection.close()

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    with Store.open(report.active_database) as store:
        snapshot = store.read_snapshot()
    assert all(item.family != "cpu" for item in snapshot.observations)
    assert any(item.family == "cpu" and item.reason == "CPU samples were aggregated" for item in snapshot.unavailable_spans)


def test_database_buckets_supersede_duplicate_json_journals(tmp_path):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    connection = sqlite3.connect(legacy)
    connection.execute("DELETE FROM stats_raw_samples")
    connection.commit()
    connection.close()
    (state / "stats-client-history-v4.json").write_text(json.dumps({
        "version": 4,
        "raw_buckets": [[100, 1, 9, {"browser-a": {"api_count": 99}}]],
        "rollup_buckets": [],
    }), encoding="utf-8")

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    with Store.open(report.active_database) as store:
        snapshot = store.read_snapshot()
    assert all(item.family != "cpu" for item in snapshot.observations)
    assert sum(item.family == "browser" for item in snapshot.unavailable_spans) == 1
    assert any(issue.kind == "superseded_json" for issue in report.issues)


def test_every_valid_compact_token_spool_is_recovered_once_then_retired(tmp_path):
    state = tmp_path / "state"
    _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    orphan = state / "services" / "statsd-agent-token-scan-orphan.atoms.sqlite3"
    _create_spool(orphan, event_id="orphan", quantity=9)
    crash_input = state / "services" / "statsd-agent-token-scan-crash.json"
    crash_input.write_text("{}", encoding="utf-8")

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    with Store.open_reader(report.active_database) as reader:
        snapshot = reader.read_snapshot()
    assert {item.event_id for item in snapshot.usage_atoms} == {
        "table", "bucket", "spool", "orphan",
    }
    assert not any((state / "services").glob("statsd-agent-token-scan-*"))


def test_retry_is_idempotent_and_does_not_rewrite_active_database(tmp_path):
    state = tmp_path / "state"
    _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    first = migration.migrate(migration.MigrationInputs(state), completed_at=200)
    before = _file_state(first.active_database)
    fence = state / WRITER_FENCE_FILENAME
    fence_before = fence.read_bytes()

    second = migration.migrate(migration.MigrationInputs(state), completed_at=300)

    assert second.already_active is True
    assert second.source_digest == first.source_digest
    assert _file_state(first.active_database) == before
    assert fence.read_bytes() == fence_before


def test_current_database_reopens_after_live_facts_change_activation_counts(tmp_path):
    state = tmp_path / "state"
    first = migration.migrate(migration.MigrationInputs(state), completed_at=200)
    with Store.open(first.active_database) as store:
        result = store.append_batch(observations=(Observation(
            "cpu-live", "cpu", "web", 201, "cpu:live", 1,
            {"process_percent": 1, "system_percent": 2},
        ),))
        assert result.source_generation > 0
    before = _file_state(first.active_database)

    second = migration.migrate(migration.MigrationInputs(state), completed_at=300)

    assert second.already_active is True
    assert second.source_digest == first.source_digest
    assert second.observations == first.observations == 0
    assert _file_state(first.active_database) == before
    with Store.open_reader(first.active_database) as reader:
        assert {item.event_id for item in reader.read_snapshot().observations} == {"cpu-live"}


def test_current_restart_removes_stale_retired_files_recreated_by_an_old_runner(tmp_path):
    state = tmp_path / "state"
    first = migration.migrate(migration.MigrationInputs(state), completed_at=200)
    with Store.open_reader(first.active_database) as reader:
        before = reader.read_snapshot()
    stale_database = state / migration.RETIRED_DATABASE_FILENAME
    stale_database.write_bytes(b"old runner recreated an unusable retired database")
    stale_json = state / "stats-client-history-v4.json"
    stale_json.write_bytes(b"old runner recreated an unusable retired journal")

    second = migration.migrate(migration.MigrationInputs(state), completed_at=201)

    assert second.already_active is True
    assert not stale_database.exists()
    assert not stale_json.exists()
    with Store.open_reader(second.active_database) as reader:
        after = reader.read_snapshot()
    assert after == before
    assert not (state / migration.RETIREMENT_ARCHIVE_FILENAME).exists()
    assert not (state / migration.RETIREMENT_JOURNAL_FILENAME).exists()


def test_existing_current_database_reconciliation_is_validated_without_mutation(tmp_path):
    state = tmp_path / "state"
    _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)
    connection = sqlite3.connect(report.active_database)
    encoded = connection.execute(
        "SELECT details_json FROM migration_reconciliation WHERE migration_id=?",
        (migration.MIGRATION_ID,),
    ).fetchone()[0]
    details = json.loads(encoded)
    details["counts"]["observations"] += 1
    connection.execute(
        "UPDATE migration_reconciliation SET details_json=? WHERE migration_id=?",
        (json.dumps(details, sort_keys=True, separators=(",", ":")), migration.MIGRATION_ID),
    )
    connection.commit()
    connection.close()
    before = _file_state(report.active_database)

    with pytest.raises(migration.MigrationError, match="reconciliation counts"):
        migration.migrate(migration.MigrationInputs(state), completed_at=300)

    assert _file_state(report.active_database) == before


def test_future_state_fence_aborts_before_legacy_inspection_or_shadow_activation(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    legacy = state / migration.RETIRED_DATABASE_FILENAME
    legacy.write_bytes(b"deliberately-invalid-legacy-input")
    fence = state / WRITER_FENCE_FILENAME
    fence.write_text(json.dumps({
        "application_id": migration.APPLICATION_ID,
        "database_filename": "stats-v6.sqlite3",
        "schema_version": migration.SCHEMA_VERSION + 1,
        "minimum_writer_protocol": 24,
        "minimum_writer_build": 1,
    }), encoding="utf-8")
    legacy_before = _file_state(legacy)
    fence_before = _file_state(fence)

    with pytest.raises(SchemaTooNewError):
        migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert _file_state(legacy) == legacy_before
    assert _file_state(fence) == fence_before
    assert not (state / DATABASE_FILENAME).exists()
    assert not (state / migration.RETIREMENT_ARCHIVE_FILENAME).exists()
    assert not (state / migration.RETIREMENT_JOURNAL_FILENAME).exists()


@pytest.mark.parametrize(
    "source_name",
    [
        migration.RETIRED_DATABASE_FILENAME,
        "stats-client-history-v4.json",
        "services/statsd-agent-token-scan-unsafe.atoms.sqlite3",
        "services/statsd-agent-token-scan-unsafe.json",
    ],
)
def test_migration_does_not_follow_retired_source_symlinks(tmp_path, source_name):
    state = tmp_path / "state"
    state.mkdir()
    outside = tmp_path / "outside-input"
    outside.write_text("private", encoding="utf-8")
    source = state / source_name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.symlink_to(outside)

    with pytest.raises(
        migration.MigrationError,
        match="regular state-directory|symbolic link|not a regular file",
    ):
        migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert outside.read_text(encoding="utf-8") == "private"
    assert not (state / DATABASE_FILENAME).exists()


def test_corrupt_retired_database_is_quarantined_and_empty_current_activates(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    source = state / migration.RETIRED_DATABASE_FILENAME
    source.write_bytes(b"not valid")
    before = _file_state(source)

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert not source.exists()
    quarantined = list(state.glob(f"{migration.RETIRED_DATABASE_FILENAME}.unsupported-*"))
    assert len(quarantined) == 1
    assert _file_state(quarantined[0]) == before
    assert report.active_database.is_file()
    assert any(issue.kind == "unsupported_legacy_database" for issue in report.issues)


def test_corrupt_retired_json_still_aborts_without_touching_source(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    source = state / "stats-client-history-v4.json"
    source.write_bytes(b"not valid")
    before = _file_state(source)

    with pytest.raises(migration.MigrationError):
        migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert _file_state(source) == before
    assert not (state / DATABASE_FILENAME).exists()


def test_unknown_durable_table_quarantines_database_instead_of_losing_facts(tmp_path):
    state = tmp_path / "state"
    legacy = _create_legacy_database(
        state / migration.RETIRED_DATABASE_FILENAME, unsupported_table=True,
    )
    before = _file_state(legacy)

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert not legacy.exists()
    quarantined = list(state.glob(f"{migration.RETIRED_DATABASE_FILENAME}.unsupported-*"))
    assert len(quarantined) == 1
    assert _file_state(quarantined[0]) == before
    assert report.active_database.is_file()
    assert any(
        issue.kind == "unsupported_legacy_database"
        and "unsupported retired database tables" in issue.detail
        for issue in report.issues
    )


def test_invalid_supplied_atom_aborts_before_activation(tmp_path):
    state = tmp_path / "state"
    bad = UsageAtom("bad", "input", "text", "none", "tokens", 1, {
        "quantity": 1, "provider": "openai", "model": "gpt",
        "agent_id": "agent", "telemetry_complete": True, "micro_usd": 1,
    })

    with pytest.raises(migration.MigrationError, match="supplied usage atom"):
        migration.migrate(migration.MigrationInputs(state, usage_atoms=(bad,)), completed_at=200)

    assert not (state / DATABASE_FILENAME).exists()


def test_repeated_raw_samples_extend_one_coverage_epoch(tmp_path):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    connection = sqlite3.connect(legacy)
    connection.execute(
        "INSERT INTO stats_raw_samples VALUES(?,?,?,?,?,?)",
        (
            "cpu", "web", 106, "raw-epoch", 3,
            json.dumps({"process_percent": 5, "system_percent": 21}),
        ),
    )
    connection.commit()
    connection.close()

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    with Store.open(report.active_database) as store:
        snapshot = store.read_snapshot()
    coverage = next(
        item for item in snapshot.coverage_epochs
        if item.family == "cpu" and item.source_id == "web" and item.epoch_id == "raw-epoch"
    )
    assert (coverage.started_at, coverage.ended_at, coverage.owner_generation) == (105, 107, 3)


def test_migration_canonicalizes_invalid_legacy_identities_without_losing_facts(tmp_path):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    bad_source = "web\ninvalid"
    bad_epoch = "epoch" + "x" * 220
    component = _component("usage\ninvalid", 5)
    component.update({"model": "model\ninvalid", "tmux_key": "agent\x7finvalid"})
    connection = sqlite3.connect(legacy)
    connection.execute(
        "UPDATE stats_raw_samples SET source_id=?, epoch_id=?",
        (bad_source, bad_epoch),
    )
    connection.execute(
        "UPDATE stats_usage_atoms SET event_id=?, atom_json=?",
        ("usage\ninvalid", json.dumps(component)),
    )
    connection.commit()
    connection.close()

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    with Store.open(report.active_database) as store:
        snapshot = store.read_snapshot()
    expected_source, _changed = identity.legacy_identity(bad_source, "source")
    expected_epoch, _changed = identity.legacy_identity(bad_epoch, "epoch")
    cpu = next(item for item in snapshot.observations if item.observed_at == 105)
    usage_atom = next(item for item in snapshot.usage_atoms if item.payload["quantity"] == 5)
    assert (cpu.source_id, cpu.epoch_id) == (expected_source, expected_epoch)
    assert usage_atom.event_id.startswith("retired-event:")
    assert usage_atom.payload["model"].startswith("retired-model:")
    assert usage_atom.payload["agent_id"].startswith("retired-agent:")
    assert usage_atom.payload["quantity"] == 5
    assert all(issue.kind == "identity_canonicalized" for issue in report.issues if issue.kind.startswith("identity_"))
    assert all("\n" not in issue.source + issue.detail and "\x7f" not in issue.source + issue.detail for issue in report.issues)


def test_migration_retains_and_clips_exactly_at_24_hour_boundary(tmp_path):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    completed_at = 100_000
    cutoff = completed_at - RETENTION_SECONDS
    connection = sqlite3.connect(legacy)
    for observed_at, suffix in ((cutoff - 1, "old"), (cutoff, "boundary")):
        connection.execute(
            "INSERT INTO stats_raw_samples VALUES(?,?,?,?,?,?)",
            (
                "cpu", "retained", observed_at, "retained-epoch", 4,
                json.dumps({"process_percent": 7, "system_percent": 22}),
            ),
        )
        connection.execute(
            "INSERT INTO stats_usage_atoms VALUES(?,?,?,?,?,?,?)",
            (
                suffix, "input", "text", "read", "tokens", observed_at,
                json.dumps(_component(suffix, 2, timestamp=observed_at)),
            ),
        )
    bucket = {
        "start": cutoff - 5,
        "duration": 10,
        "cpu_total_percent": 20,
        "cpu_count": 10,
        "system_cpu_total_percent": 50,
        "system_cpu_count": 10,
    }
    connection.execute(
        "INSERT INTO stats_buckets VALUES(?,?,?,?,?)",
        (cutoff - 5, 10, 2, 2, json.dumps(bucket)),
    )
    connection.commit()
    connection.close()

    report = migration.migrate(
        migration.MigrationInputs(state), completed_at=completed_at,
    )

    with Store.open(report.active_database) as store:
        snapshot = store.read_snapshot()
    assert {item.observed_at for item in snapshot.observations} == {cutoff}
    assert {item.event_id for item in snapshot.usage_atoms} == {"boundary"}
    assert snapshot.coverage_epochs
    assert min(item.started_at for item in snapshot.coverage_epochs) == cutoff
    assert snapshot.unavailable_spans
    assert min(item.started_at for item in snapshot.unavailable_spans) == cutoff


def test_unsafe_spool_pointer_quarantines_database_without_following_path(tmp_path):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    outside = tmp_path / "statsd-agent-token-scan-outside.atoms.sqlite3"
    _create_spool(outside)
    outside_before = _file_state(outside)
    connection = sqlite3.connect(legacy)
    connection.execute(
        "UPDATE schema_meta SET value=? WHERE key='agent_token_atom_spool'",
        (json.dumps({"path": str(outside)}),),
    )
    connection.commit()
    connection.close()

    report = migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert report.active_database.is_file()
    assert _file_state(outside) == outside_before
    quarantined = list(state.glob(f"{migration.RETIRED_DATABASE_FILENAME}.unsupported-*"))
    assert len(quarantined) == 1
    assert any(
        issue.kind == "unsupported_legacy_database"
        and "expected state services directory" in issue.detail
        for issue in report.issues
    )


@pytest.mark.parametrize("failure_point", ["materialization", "activation"])
def test_pre_activation_failure_leaves_no_partial_current_database(
    tmp_path,
    monkeypatch,
    failure_point,
):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    before = _file_state(legacy)
    if failure_point == "materialization":
        def fail_materialization(*args, **kwargs):
            raise migration.materializer.MaterializationError("injected")

        monkeypatch.setattr(migration.materializer, "build_generation", fail_materialization)
    else:
        def fail_activation(*args, **kwargs):
            raise migration.MigrationError("injected activation interruption")

        monkeypatch.setattr(migration, "_activate_database", fail_activation)

    with pytest.raises(migration.MigrationError):
        migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert _file_state(legacy) == before
    assert not (state / DATABASE_FILENAME).exists()


def test_final_retirement_failure_restores_every_source_and_deactivates_current_for_retry(
    tmp_path,
    monkeypatch,
):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    shared = state / "tmux-AI-status.json"
    shared.write_text(json.dumps({
        "keep": {"value": 1},
        "stats_history": {"raw_buckets": [], "rollup_buckets": []},
    }), encoding="utf-8")
    old_fence = state / WRITER_FENCE_FILENAME
    old_fence.write_text(json.dumps({
        "schema_version": max(migration.RETIRED_SCHEMA_VERSIONS),
        "minimum_writer_protocol": 22,
        "minimum_writer_build": "legacy-build",
    }), encoding="utf-8")
    legacy_before = _file_state(legacy)
    shared_before = _file_state(shared)
    fence_before = _file_state(old_fence)
    discard = migration._discard_retirement_archive

    def fail_final_discard(plan):
        raise OSError("injected final archive unlink failure")

    monkeypatch.setattr(migration, "_discard_retirement_archive", fail_final_discard)
    with pytest.raises(migration.MigrationError, match="activation/retirement"):
        migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert _file_state(legacy) == legacy_before
    assert _file_state(shared) == shared_before
    assert _file_state(old_fence) == fence_before
    assert not (state / DATABASE_FILENAME).exists()
    assert list(state.glob("stats-v5.failed-*.sqlite3"))
    assert (state / migration.RETIREMENT_ARCHIVE_FILENAME).is_file()
    assert (state / migration.RETIREMENT_JOURNAL_FILENAME).is_file()

    monkeypatch.setattr(migration, "_discard_retirement_archive", discard)
    report = migration.migrate(migration.MigrationInputs(state), completed_at=201)
    assert report.active_database.is_file()
    assert not legacy.exists()
    assert json.loads(shared.read_text(encoding="utf-8")) == {"keep": {"value": 1}}
    assert not (state / migration.RETIREMENT_ARCHIVE_FILENAME).exists()
    assert not (state / migration.RETIREMENT_JOURNAL_FILENAME).exists()


def test_retirement_rollback_merges_stats_history_without_losing_new_shared_status(tmp_path, monkeypatch):
    state = tmp_path / "state"
    _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    shared = state / "tmux-AI-status.json"
    history = {"raw_buckets": [], "rollup_buckets": []}
    shared.write_text(json.dumps({"keep": 1, "stats_history": history}), encoding="utf-8")

    def update_shared_then_fail(plan):
        current = json.loads(shared.read_text(encoding="utf-8"))
        assert "stats_history" not in current
        current["keep"] = 2
        migration.atomic_write_text(
            shared,
            json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
        )
        raise OSError("injected archive cleanup failure")

    monkeypatch.setattr(migration, "_discard_retirement_archive", update_shared_then_fail)
    with pytest.raises(migration.MigrationError):
        migration.migrate(migration.MigrationInputs(state), completed_at=200)

    restored = json.loads(shared.read_text(encoding="utf-8"))
    assert restored == {"keep": 2, "stats_history": history}
    assert not (state / DATABASE_FILENAME).exists()


def test_restart_finishes_activation_interrupted_after_atomic_database_swap(tmp_path, monkeypatch):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    activate = migration._activate_database

    def swap_then_crash(shadow, target):
        activate(shadow, target)
        raise KeyboardInterrupt("injected process interruption")

    monkeypatch.setattr(migration, "_activate_database", swap_then_crash)
    with pytest.raises(KeyboardInterrupt):
        migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert not legacy.exists()
    assert (state / DATABASE_FILENAME).is_file()
    assert (state / migration.RETIREMENT_ARCHIVE_FILENAME).is_file()
    assert (state / migration.RETIREMENT_JOURNAL_FILENAME).is_file()

    monkeypatch.setattr(migration, "_activate_database", activate)
    report = migration.migrate(migration.MigrationInputs(state), completed_at=201)
    assert report.already_active is True
    assert not legacy.exists()
    assert not (state / migration.RETIREMENT_ARCHIVE_FILENAME).exists()
    assert not (state / migration.RETIREMENT_JOURNAL_FILENAME).exists()
    fence = json.loads((state / WRITER_FENCE_FILENAME).read_text(encoding="utf-8"))
    assert fence["schema_version"] == migration.SCHEMA_VERSION


def test_restart_restores_a_source_removed_before_retirement_was_interrupted(tmp_path, monkeypatch):
    state = tmp_path / "state"
    legacy = _create_legacy_database(state / migration.RETIRED_DATABASE_FILENAME)
    before = _file_state(legacy)
    retire = migration._retire_legacy_sources

    def remove_one_then_crash(plan):
        migration._write_retirement_journal(plan, "retiring")
        artifact = next(
            item for item in plan.artifacts
            if item.relative_path == migration.RETIRED_DATABASE_FILENAME
        )
        (plan.state_dir / artifact.relative_path).unlink()
        raise KeyboardInterrupt("injected retirement interruption")

    monkeypatch.setattr(migration, "_retire_legacy_sources", remove_one_then_crash)
    with pytest.raises(KeyboardInterrupt):
        migration.migrate(migration.MigrationInputs(state), completed_at=200)

    assert not legacy.exists()
    assert not (state / DATABASE_FILENAME).exists()
    monkeypatch.setattr(migration, "_retire_legacy_sources", retire)
    assert migration._recover_interrupted_retirement(state, state / DATABASE_FILENAME) is None
    assert _file_state(legacy) == before
    assert not (state / migration.RETIREMENT_ARCHIVE_FILENAME).exists()
    assert not (state / migration.RETIREMENT_JOURNAL_FILENAME).exists()

    report = migration.migrate(migration.MigrationInputs(state), completed_at=201)
    assert report.active_database.is_file()
    assert not legacy.exists()


def test_current_migration_has_no_retired_runtime_imports():
    source = Path(migration.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not imported & {
        "yolomux_lib.statsd",
        "yolomux_lib.stats_families",
        "yolomux_lib.local_services.stats_store",
        "yolomux_lib.session_files",
    }
