# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Red contracts for immutable cross-host SQLite views."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from yolomux_lib.infra.host_identity import HostIdentity
from tests.helpers.local_service_records import FixtureHostIdentityBuilder


CROSS_HOST_VIEWS_MODULE = "yolomux_lib.cross_host_views"
DATASET = "stats"
SCHEMA_VERSION = 3
COVERAGE_START = 100
COVERAGE_END = 200


def _module() -> Any:
    return importlib.import_module(CROSS_HOST_VIEWS_MODULE)


def _host_identity() -> HostIdentity:
    return FixtureHostIdentityBuilder(
        stable_host_id="fixture-host-a",
        display_hostname="lin1",
        boot_id="fixture-boot-a",
        pid=4242,
        process_start_ticks=5252,
        instance_nonce="fixture-instance-a",
        stable_host_id_source="gate fixture",
    ).build()


def _live_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.execute("CREATE TABLE observations(value INTEGER NOT NULL)")
    connection.execute("INSERT INTO observations(value) VALUES (42)")
    connection.commit()
    return connection


def _publish(module: Any, source: Path, publish_root: Path) -> Any:
    return module.publish_sqlite_snapshot(
        source,
        publish_root,
        dataset=DATASET,
        host_identity=_host_identity(),
        source_generation=7,
        schema_version=SCHEMA_VERSION,
        coverage_start=COVERAGE_START,
        coverage_end=COVERAGE_END,
    )


@pytest.mark.xfail(
    strict=True,
    reason="Deferred, not in current release scope: cross-host SQLite snapshot publication and offline reading are intentionally unbuilt; foreign live databases remain forbidden",
)
def test_published_snapshot_is_sidecar_free_and_readable_after_source_goes_offline(tmp_path: Path) -> None:
    module = _module()
    source_dir = tmp_path / "source-host"
    source = source_dir / "live.sqlite3"
    connection = _live_database(source)
    try:
        published = _publish(module, source, tmp_path / "published")
    finally:
        connection.close()

    assert published.payload_path.exists()
    assert not Path(f"{published.payload_path}-wal").exists()
    assert not Path(f"{published.payload_path}-shm").exists()
    source_dir.rename(tmp_path / "offline-source-host")

    reader = module.SnapshotReader(
        tmp_path / "published",
        supported_schemas={DATASET: {SCHEMA_VERSION}},
    )
    with reader.open(published.manifest_path) as snapshot:
        assert snapshot.execute("SELECT value FROM observations").fetchone() == (42,)


@pytest.mark.xfail(
    strict=True,
    reason="Deferred, not in current release scope: cross-host snapshot manifests with stable source, generation, schema, and coverage identity are intentionally unbuilt",
)
def test_snapshot_manifest_carries_stable_source_generation_schema_and_coverage(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "source-host" / "live.sqlite3"
    connection = _live_database(source)
    try:
        published = _publish(module, source, tmp_path / "published")
    finally:
        connection.close()

    manifest = json.loads(published.manifest_path.read_text(encoding="utf-8"))
    assert manifest["format_version"] == 1
    assert manifest["dataset"] == DATASET
    assert manifest["source"] == {
        "stable_host_id": "fixture-host-a",
        "hostname": "lin1",
        "boot_id": "fixture-boot-a",
    }
    assert manifest["source_generation"] == 7
    assert manifest["schema"] == {"version": SCHEMA_VERSION}
    assert manifest["coverage"] == {"start": COVERAGE_START, "end": COVERAGE_END}
    assert manifest["payload"]["path"] == "snapshot.sqlite3"
    assert manifest["payload"]["bytes"] == published.payload_path.stat().st_size
    assert manifest["payload"]["sha256"] == hashlib.sha256(published.payload_path.read_bytes()).hexdigest()
    assert "live_database_path" not in manifest


@pytest.mark.xfail(
    strict=True,
    reason="Deferred, not in current release scope: the contained immutable cross-host snapshot reader is intentionally unbuilt; foreign live database paths remain forbidden",
)
def test_reader_opens_only_the_published_payload_never_the_live_foreign_database(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "source-host" / "live.sqlite3"
    connection = _live_database(source)
    try:
        published = _publish(module, source, tmp_path / "published")
    finally:
        connection.close()
    opened: list[tuple[str, dict[str, Any]]] = []

    def tracking_opener(database: object, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        opened.append((str(database), dict(kwargs)))
        return sqlite3.connect(database, *args, **kwargs)

    reader = module.SnapshotReader(
        tmp_path / "published",
        supported_schemas={DATASET: {SCHEMA_VERSION}},
        sqlite_opener=tracking_opener,
    )
    with reader.open(published.manifest_path) as snapshot:
        assert snapshot.execute("SELECT count(*) FROM observations").fetchone() == (1,)

    assert len(opened) == 1
    opened_database, opened_options = opened[0]
    assert str(published.payload_path.resolve()) in opened_database
    assert str(source.resolve()) not in opened_database
    assert "mode=ro" in opened_database
    assert "immutable=1" in opened_database
    assert opened_options.get("uri") is True


@pytest.mark.xfail(
    strict=True,
    reason="Deferred, not in current release scope: typed cross-host snapshot schema rejection before SQLite open is intentionally unbuilt",
)
def test_schema_mismatch_is_rejected_before_payload_open_with_typed_reason(tmp_path: Path) -> None:
    module = _module()
    publish_root = tmp_path / "published"
    generation_dir = publish_root / "v1" / DATASET / "fixture-host-a" / "fixture-boot-a" / "9"
    generation_dir.mkdir(parents=True)
    payload = generation_dir / "snapshot.sqlite3"
    payload.write_bytes(b"not a sqlite database and must never be partially opened")
    manifest_path = generation_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "dataset": DATASET,
                "source": {
                    "stable_host_id": "fixture-host-a",
                    "hostname": "lin1",
                    "boot_id": "fixture-boot-a",
                },
                "source_generation": 9,
                "schema": {"version": 999},
                "coverage": {"start": COVERAGE_START, "end": COVERAGE_END},
                "created_at": 201,
                "payload": {
                    "path": payload.name,
                    "bytes": payload.stat().st_size,
                    "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    opened: list[str] = []

    def forbidden_opener(database: object, *_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        opened.append(str(database))
        raise AssertionError("an unsupported snapshot payload was opened")

    reader = module.SnapshotReader(
        publish_root,
        supported_schemas={DATASET: {SCHEMA_VERSION}},
        sqlite_opener=forbidden_opener,
    )
    with pytest.raises(module.SnapshotRejected) as rejected:
        reader.open(manifest_path)

    assert rejected.value.reason_code == "schema_mismatch"
    assert rejected.value.found_schema == 999
    assert rejected.value.supported_schemas == (SCHEMA_VERSION,)
    assert opened == []
