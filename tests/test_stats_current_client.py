# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Mock-RPC tests for the current-only YO!stats client boundary."""

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from yolomux_lib import common
from yolomux_lib.stats_current import client as client_module
from yolomux_lib.stats_current import protocol, revision, storage
from yolomux_lib.local_services.rpc import safe_socket_path


def observation(index: int = 1) -> storage.Observation:
    return storage.Observation(f"cpu-{index}", "cpu", "host", float(index), "cpu:1", 1, {"value": index})


def usage(index: int = 1) -> storage.UsageAtom:
    return storage.UsageAtom(f"event-{index}", "input", "text", "none", "tokens", float(index), {
        "quantity": index,
        "provider": "test-provider",
        "model": "test-model",
        "agent_id": "test-agent",
        "telemetry_complete": True,
    })


def usage_tombstone(index: int = 1) -> storage.UsageAtomTombstone:
    return storage.UsageAtomTombstone(
        f"codex:child-thread:{index}", "input", "text", "none", "tokens",
        float(index), float(index), "openai", "gpt-test", "child-thread",
    )


def coverage() -> storage.CoverageEpoch:
    return storage.CoverageEpoch("cpu", "host", "cpu:1", 0.0, None, 1.0, 1)


def unavailable() -> storage.UnavailableSpan:
    return storage.UnavailableSpan(
        "gpu", "legacy", "migration-1", 0.0, 10.0, 10.0,
        "legacy_aggregate_not_reconstructable", 1,
    )


def test_default_paths_are_version_scoped_and_never_use_the_legacy_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "STATE_DIR", tmp_path)
    client = client_module.StatsCurrentClient()

    assert client.database_path == tmp_path / storage.DATABASE_FILENAME
    assert client.database_path == tmp_path / "stats-v6.sqlite3"
    assert client_module.default_socket_path() == tmp_path / "services" / "statsd.p24s6.sock"
    assert client_module.default_socket_path().name == storage.SOCKET_FILENAME
    assert client._transport.socket_path == safe_socket_path(
        client_module.default_socket_path(), prefix="yolomux-statsd",
    )
    assert client._transport.registry.spec.socket_name == client._transport.socket_path.name
    assert client._transport.registry.spec.protocol_version == storage.MIN_WRITER_PROTOCOL == 24
    assert client._transport.registry.spec.code_revision == revision.CURRENT_CODE_REVISION
    assert client._transport.registry.spec.extra_args == ("--database", str(client.database_path))
    assert client._transport.registry.service_dir == tmp_path / "services"
    assert client._transport.registry.lock_path.parent == tmp_path / "services"
    assert "services/services" not in str(client._transport.registry.lock_path)
    source = Path(client_module.__file__).read_text(encoding="utf-8")
    assert "stats-history.sqlite3" not in source


def test_mixed_protocol_schema_service_identities_cannot_share_a_socket_or_database(tmp_path):
    current_socket = tmp_path / "services" / storage.socket_filename(24, 6)
    older_socket = tmp_path / "services" / storage.socket_filename(23, 5)

    assert storage.DATABASE_FILENAME == "stats-v6.sqlite3"
    assert storage.socket_filename(23, 5) == "statsd.p23s5.sock"
    assert current_socket != older_socket
    assert safe_socket_path(current_socket, prefix="yolomux-statsd") != safe_socket_path(
        older_socket, prefix="yolomux-statsd",
    )


def test_all_lifecycle_and_data_rpcs_carry_the_current_service_and_schema_fence(tmp_path, monkeypatch):
    calls = []

    def rpc(_socket_path, envelope, *, timeout_seconds):
        calls.append((envelope.method, envelope.payload, timeout_seconds))
        binary = b'{"snapshot":true}' if envelope.method == "snapshot" else b'{"delta":true}' if envelope.method == "delta" else b""
        return {"ok": True, "version": storage.MIN_WRITER_PROTOCOL, "pid": 123}, binary

    monkeypatch.setattr(client_module, "local_service_request", rpc)
    client = client_module.StatsCurrentClient(tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME)
    monkeypatch.setattr(client, "ensure_started", lambda: True)

    assert client._transport.registry._request("ping")["ok"] is True
    assert client.status()["ok"] is True
    assert client.acquire_lease()["ok"] is True
    assert client.renew_lease("lease-a")["ok"] is True
    assert client.release_lease("lease-a")["ok"] is True
    assert client.append(
        observations=[observation()], usage_atoms=[usage()], coverage_epochs=[coverage()],
        unavailable_spans=[unavailable()],
    )["ok"] is True
    metadata, binary = client.snapshot({"range_seconds": "300", "resolution": "AUTO", "client_id": "browser-a", "since_generation": "7"})
    assert metadata["ok"] is True
    assert binary == b'{"snapshot":true}'
    delta_metadata, delta_binary = client.delta({
        "range_seconds": "300", "resolution_seconds": "1", "client_id": "browser-a",
        "after_cache_generation": "7", "after_revision": "41",
    })
    assert delta_metadata["ok"] is True
    assert delta_binary == b'{"delta":true}'

    assert [method for method, _payload, _timeout in calls] == ["ping", "status", "lease", "lease", "release", "append", "snapshot", "delta"]
    for method, payload, _timeout in calls:
        assert payload["action"] == method
        assert payload["protocol_version"] == storage.MIN_WRITER_PROTOCOL
        assert payload["schema_generation"] == storage.SCHEMA_VERSION
    assert calls[2][1]["lease_id"] == ""
    assert calls[3][1]["lease_id"] == "lease-a"
    assert calls[1][2] == client_module.STATUS_TIMEOUT_SECONDS == 3.0
    assert [calls[index][2] for index in (2, 3, 4)] == [
        client_module.LEASE_TIMEOUT_SECONDS,
        client_module.LEASE_TIMEOUT_SECONDS,
        client_module.LEASE_TIMEOUT_SECONDS,
    ]
    append_payload = calls[-3][1]
    assert append_payload["observations"] == [{"event_id": "cpu-1", "family": "cpu", "source_id": "host", "observed_at": 1.0, "epoch_id": "cpu:1", "owner_generation": 1, "payload": {"value": 1}}]
    assert append_payload["usage_atoms"][0]["event_id"] == "event-1"
    assert append_payload["usage_tombstones"] == []
    assert append_payload["coverage_epochs"][0]["epoch_id"] == "cpu:1"
    assert append_payload["unavailable_spans"][0]["reason"] == "legacy_aggregate_not_reconstructable"
    snapshot_payload = calls[-2][1]
    assert snapshot_payload == {"range_seconds": 300, "resolution": "AUTO", "client_id": "browser-a", "since_generation": 7, "action": "snapshot", "protocol_version": 24, "schema_generation": 6}
    delta_payload = calls[-1][1]
    assert delta_payload == {"range_seconds": 300, "resolution_seconds": 1, "client_id": "browser-a", "after_cache_generation": 7, "after_revision": 41, "action": "delta", "protocol_version": 24, "schema_generation": 6}


def test_snapshot_revalidates_typed_or_query_requests_before_rpc(tmp_path, monkeypatch):
    calls = []

    def rpc(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}, b"snapshot"

    monkeypatch.setattr(client_module, "local_service_request", rpc)
    client = client_module.StatsCurrentClient(tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME)
    valid = protocol.parse_snapshot_request({"range_seconds": "900", "resolution": "10", "client_id": "browser-a"})
    assert client.snapshot(valid)[1] == b"snapshot"
    with pytest.raises(protocol.UnsupportedRequest):
        client.snapshot({"range_seconds": "900", "resolution": "1", "client_id": "browser-a"})
    with pytest.raises(protocol.UnsupportedRequest):
        client.snapshot({"range_seconds": "300", "resolution": "1", "client_id": "browser-a", "history": "1"})
    invalid_typed = protocol.SnapshotRequest(900, 1, 1, "browser-a", None)
    with pytest.raises(protocol.UnsupportedRequest):
        client.snapshot(invalid_typed)
    assert len(calls) == 1


def test_delta_revalidates_exact_key_and_preserves_binary_response_bytes(tmp_path, monkeypatch):
    calls = []
    encoded = b"\x00already-encoded-delta\xff"
    metadata = {"ok": True, "content_type": "application/json", "cache_generation": 8}

    def rpc(_socket_path, envelope, *, timeout_seconds):
        calls.append((envelope.method, envelope.payload, timeout_seconds))
        return metadata, encoded

    monkeypatch.setattr(client_module, "local_service_request", rpc)
    client = client_module.StatsCurrentClient(tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME)
    request = protocol.DeltaRequest(300, 1, "browser-a", 7, 41)

    returned_metadata, returned_binary = client.delta(request)

    assert returned_metadata is metadata
    assert returned_binary is encoded
    assert calls[0][0] == "delta"
    for changes in (
        {"resolution_seconds": "600"},
        {"range_seconds": "900", "resolution_seconds": "1"},
        {"history": "1"},
    ):
        with pytest.raises(protocol.UnsupportedRequest):
            client.delta({
                "range_seconds": "300", "resolution_seconds": "1", "client_id": "browser-a",
                "after_cache_generation": "7", "after_revision": "41", **changes,
            })
    with pytest.raises(protocol.UnsupportedRequest):
        client.delta(protocol.DeltaRequest(300, 600, "browser-a", 7, 41))
    assert len(calls) == 1


def test_append_is_one_bounded_atomic_batch(tmp_path, monkeypatch):
    calls = []

    def rpc(_socket_path, envelope, *, timeout_seconds):
        calls.append(envelope.payload)
        return {"ok": True, "source_generation": 9, "accepted": 3, "duplicates": 0}, b""

    monkeypatch.setattr(client_module, "local_service_request", rpc)
    client = client_module.StatsCurrentClient(tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME)
    response = client.append(observations=[observation()], usage_atoms=[usage()], coverage_epochs=[coverage()])
    assert response == {"ok": True, "source_generation": 9, "accepted": 3, "duplicates": 0}
    assert [call["action"] for call in calls] == ["append"]

    with pytest.raises(ValueError, match="1..1000"):
        client.append()
    with pytest.raises(ValueError, match="1..1000"):
        client.append(observations=[observation(index) for index in range(1001)])
    with pytest.raises(TypeError, match="Observation"):
        client.append(observations=[coverage()])
    assert len(calls) == 1


def test_tombstone_replay_batches_all_identities_within_rpc_limits():
    tombstones = tuple(usage_tombstone(index) for index in range(1, 3_016))

    batches = tuple(client_module.iter_append_batches(
        usage_tombstones=tombstones,
    ))

    assert tuple(
        item.event_id for batch in batches for item in batch[2]
    ) == tuple(item.event_id for item in tombstones)
    assert all(sum(len(group) for group in batch) <= protocol.MAX_APPEND_RECORDS for batch in batches)
    assert all(client_module.append_metadata_size(
        observations=batch[0],
        usage_atoms=batch[1],
        usage_tombstones=batch[2],
        coverage_epochs=batch[3],
        unavailable_spans=batch[4],
    ) <= client_module.LOCAL_RPC_MAX_METADATA_BYTES for batch in batches)


def test_append_copies_nested_immutable_fact_payloads_without_deepcopy(tmp_path, monkeypatch):
    calls = []

    def rpc(_socket_path, envelope, *, timeout_seconds):
        json.dumps(envelope.to_dict())
        calls.append(envelope.payload)
        return {"ok": True}, b""

    monkeypatch.setattr(client_module, "local_service_request", rpc)
    client = client_module.StatsCurrentClient(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    immutable_payload = MappingProxyType({
        "states": MappingProxyType({"agent-a": "run"}),
    })
    record = storage.Observation(
        "agent-status-1", "agent_status", "web", 1.0, "agent_status:1", 1,
        immutable_payload,
    )

    assert client.append(observations=[record])["ok"] is True
    assert calls[0]["observations"][0]["payload"] == {
        "states": {"agent-a": "run"},
    }
    assert isinstance(calls[0]["observations"][0]["payload"], dict)
    assert isinstance(calls[0]["observations"][0]["payload"]["states"], dict)


@pytest.mark.parametrize("status_field", ["error_code", "status"])
def test_upgrade_required_is_terminal_and_never_retried(tmp_path, monkeypatch, status_field):
    calls = []
    upgrade = {"ok": False, status_field: "upgrade_required", "required_protocol_version": 25, "required_schema_generation": 6}

    def rpc(_socket_path, envelope, *, timeout_seconds):
        calls.append(envelope.payload)
        return upgrade, b"must-not-pass"

    monkeypatch.setattr(client_module, "local_service_request", rpc)
    client = client_module.StatsCurrentClient(tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME)
    assert client.status() == upgrade
    assert client.status() == upgrade
    assert client.append(observations=[observation()]) == upgrade
    assert client.snapshot({"range_seconds": "300", "resolution": "1", "client_id": "browser-a"}) == (upgrade, b"")
    assert client.delta({"range_seconds": "300", "resolution_seconds": "1", "client_id": "browser-a", "after_cache_generation": "7", "after_revision": "41"}) == (upgrade, b"")
    assert client.ensure_started() is False
    assert len(calls) == 1


def test_registry_preserves_schema_upgrade_as_terminal(tmp_path, monkeypatch):
    calls = []
    upgrade = {"ok": False, "status": "upgrade_required", "required_protocol_version": 25, "required_schema_generation": 6}

    def rpc(_socket_path, envelope, *, timeout_seconds):
        calls.append(envelope.payload)
        return upgrade, b""

    monkeypatch.setattr(client_module, "local_service_request", rpc)
    client = client_module.StatsCurrentClient(tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME)
    assert client.ensure_started() is False
    assert client.status()["required_schema_generation"] == 6
    assert len(calls) == 1


def test_registry_does_not_misclassify_an_older_daemon_as_a_terminal_upgrade(tmp_path, monkeypatch):
    calls = []
    older = {
        "ok": False,
        "error_code": "upgrade_required",
        "version": storage.MIN_WRITER_PROTOCOL - 1,
        "required_protocol_version": storage.MIN_WRITER_PROTOCOL - 1,
        "pid": 4242,
    }

    def rpc(_socket_path, envelope, *, timeout_seconds):
        calls.append(envelope.payload)
        return older, b""

    monkeypatch.setattr(client_module, "local_service_request", rpc)
    client = client_module.StatsCurrentClient(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )

    response = client._transport.registry._request("ping")

    assert response == older
    assert client._transport.registry._upgrade_required is None
    assert len(calls) == 1


def test_retry_clears_a_cached_read_fence_and_restarts_only_its_version_scoped_service(tmp_path, monkeypatch):
    socket_path = tmp_path / "services" / storage.SOCKET_FILENAME
    client = client_module.StatsCurrentClient(socket_path, tmp_path / storage.DATABASE_FILENAME)
    client._upgrade_required = {
        "ok": False,
        "status": "upgrade_required",
        "required_protocol_version": storage.MIN_WRITER_PROTOCOL,
        "required_schema_generation": storage.SCHEMA_VERSION,
    }
    calls = []
    monkeypatch.setattr(client._transport.registry, "retry", lambda: calls.append("retry"))
    monkeypatch.setattr(client._transport.registry, "recently_healthy", lambda: True)

    assert client.retry() is True
    assert client._upgrade_required is None
    assert calls == ["retry"]
    assert client._transport.socket_path == safe_socket_path(
        socket_path, prefix="yolomux-statsd",
    )


def test_current_registry_rejects_same_protocol_stale_daemon(tmp_path, monkeypatch):
    client = client_module.StatsCurrentClient(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    registry = client._transport.registry
    response = {
        "ok": True,
        "version": storage.MIN_WRITER_PROTOCOL,
        "pid": 4242,
        "code_revision": "stale-revision",
    }
    monkeypatch.setattr(registry, "_request", lambda *args, **kwargs: response)
    assert registry.healthy() is False
    response["code_revision"] = revision.CURRENT_CODE_REVISION
    assert registry.healthy() is True


def test_current_registry_accepts_newer_same_protocol_daemon(tmp_path, monkeypatch):
    client = client_module.StatsCurrentClient(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
    )
    registry = client._transport.registry
    response = {
        "ok": True,
        "version": storage.MIN_WRITER_PROTOCOL,
        "build": storage.MIN_WRITER_BUILD + 1,
        "pid": 4242,
        "code_revision": "newer-revision",
    }
    monkeypatch.setattr(registry, "_request", lambda *args, **kwargs: response)

    assert registry.healthy() is True


def test_running_newer_daemon_bypasses_local_future_writer_preflight(tmp_path, monkeypatch):
    database = tmp_path / storage.DATABASE_FILENAME
    client = client_module.StatsCurrentClient(tmp_path / "statsd.sock", database)
    monkeypatch.setattr(client._transport.registry, "recently_healthy", lambda: False)
    monkeypatch.setattr(client._transport.registry, "healthy", lambda: True)
    monkeypatch.setattr(
        storage,
        "require_compatible_writer",
        lambda _path: (_ for _ in ()).throw(AssertionError("RPC reader ran writer preflight")),
    )

    assert client.ensure_started() is True


def test_offline_future_writer_fence_stops_spawn_and_retry_before_transport(tmp_path, monkeypatch):
    database = tmp_path / storage.DATABASE_FILENAME
    (tmp_path / storage.WRITER_FENCE_FILENAME).write_text(json.dumps({
        "application_id": storage.APPLICATION_ID,
        "database_filename": "stats-v6.sqlite3",
        "schema_version": storage.SCHEMA_VERSION + 1,
        "minimum_writer_protocol": storage.MIN_WRITER_PROTOCOL + 1,
        "minimum_writer_build": storage.MIN_WRITER_BUILD + 1,
    }), encoding="utf-8")
    client = client_module.StatsCurrentClient(tmp_path / "statsd.sock", database)
    starts = []
    monkeypatch.setattr(client._transport.registry, "ensure_started", lambda: starts.append(True))

    assert client.ensure_started() is False
    assert client.ensure_started() is False
    assert client.status()["required_schema_generation"] == storage.SCHEMA_VERSION + 1
    assert starts == []
    assert not database.exists()


def test_public_surface_has_only_shared_lifecycle_one_write_snapshot_and_delta():
    public = {name for name, value in client_module.StatsCurrentClient.__dict__.items() if not name.startswith("_") and callable(value)}
    assert public == {
        "ensure_started", "acquire_lease", "renew_lease", "release_lease",
        "status", "retry", "append", "snapshot", "delta",
    }
    source = Path(client_module.__file__).read_text(encoding="utf-8")
    for retired in ("fallback_legacy", "materialized_snapshot", "query_buckets", "claim_agent_token", "merge_records"):
        assert retired not in source
