import json
import os
import sqlite3
import threading
import time

import pytest

from yolomux_lib import statsd
from yolomux_lib.local_services import stats_store


def _bucket(start=100, duration=1, sequence=1):
    bucket = stats_store.empty_bucket(start, duration)
    bucket.update({"sequence": sequence, "server_sequence": sequence, "cpu_total_percent": 12.5, "cpu_count": 1.0})
    bucket["clients"] = {"browser-a": {**stats_store.empty_client_bucket(), "sequence": sequence, "api_count": 2.0}}
    bucket["servers"] = {"port:17071": {**stats_store.empty_process_bucket(), "sequence": sequence, "label": "yolomux.py :17071", "port": 17071, "cpu_total_percent": 12.5, "cpu_count": 1.0}}
    bucket["agent_token_rates"] = {"17071|0|codex": {"label": "17071:0:codex", "total": 8.0, "samples": 1.0, "tokens": 8.0}}
    bucket["host_metrics"] = {**stats_store.empty_host_metrics(), "cpu_processes": {"python": {"label": "python", "total_percent": 12.5, "samples": 1.0}}}
    return bucket


def test_stats_store_wal_round_trip_preserves_bucket_and_normalized_rows(tmp_path):
    path = tmp_path / "stats.sqlite3"
    store = stats_store.StatsStore(path)
    store.upsert_bucket(_bucket())
    store.close()

    reopened = stats_store.StatsStore(path)
    assert reopened.query_buckets() == [_bucket()]
    diagnostics = reopened.diagnostics()
    assert diagnostics["rows"] == 1
    assert diagnostics["children"] == {"stats_clients": 1, "stats_processes": 1, "stats_agent_rates": 1, "stats_host_metrics": 5}
    assert reopened._connection().execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    reopened.close()


def test_statsd_new_owner_negotiates_old_and_current_protocols(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    old_ping, _binary = service.handle_with_binary({"action": "ping"})
    current_ping, _binary = service.handle_with_binary(
        {"action": "ping", "protocol_version": statsd.STATSD_PROTOCOL_VERSION}
    )
    old_status, _binary = service.handle_with_binary({"action": "status", "protocol_version": 4})

    malformed_ping, _binary = service.handle_with_binary({"action": "ping", "protocol_version": "invalid"})

    assert old_ping["version"] == statsd.STATSD_COMPAT_PROTOCOL_VERSION
    assert old_status["version"] == statsd.STATSD_COMPAT_PROTOCOL_VERSION
    assert malformed_ping["version"] == statsd.STATSD_COMPAT_PROTOCOL_VERSION
    assert current_ping["version"] == statsd.STATSD_PROTOCOL_VERSION
    service.store.close()


def test_statsd_claims_model_deltas_and_preserves_them_through_history(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    key = "1|0|codex"

    baseline = service.claim_agent_token_deltas([{
        "key": key,
        "label": "1:0:codex",
        "tokens": 100,
        "source": "transcript",
        "identity": "stable",
        "models": {"gpt-5.6-sol": 80, "gpt-5.6-terra": 20},
    }], {key}, 1000.0)
    claimed = service.claim_agent_token_deltas([{
        "key": key,
        "label": "1:0:codex",
        "tokens": 160,
        "source": "transcript",
        "identity": "stable",
        "models": {"gpt-5.6-sol": 110, "gpt-5.6-terra": 40},
    }], {key}, 1060.0)
    service.merge_server_records(claimed["records"], now=1060.0)
    history = service.handle({"action": "history", "token_resolution_seconds": 60})

    assert baseline["records"] == []
    rates = [record["agent_token_rates"][0] for record in claimed["records"]]
    assert sum(rate["tokens"] for rate in rates) == pytest.approx(60.0)
    model_tokens = {
        model: sum(rate["model_rates"].get(model, {}).get("tokens", 0.0) for rate in rates)
        for model in {model for rate in rates for model in rate["model_rates"]}
    }
    assert model_tokens == {
        "gpt-5.6-sol": pytest.approx(30.0),
        "gpt-5.6-terra": pytest.approx(20.0),
        "unknown": pytest.approx(10.0),
    }
    stored_rates = [record["agent_token_rates"][0] for record in history["agent_token_history"]["records"]]
    assert sum(rate["model_rates"]["gpt-5.6-sol"]["tokens"] for rate in stored_rates) == pytest.approx(30.0)
    assert claimed["state"][key]["models"] == {"gpt-5.6-sol": 110.0, "gpt-5.6-terra": 40.0}
    service.store.close()


def test_statsd_old_model_less_baseline_does_not_recount_cumulative_models(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    key = "1|0|codex"
    service._set_agent_token_state({key: {"tokens": 100, "time": 1000, "label": "1:0:codex", "source": "transcript", "identity": "stable"}})

    claimed = service.claim_agent_token_deltas([{
        "key": key,
        "label": "1:0:codex",
        "tokens": 110,
        "source": "transcript",
        "identity": "stable",
        "models": {"gpt-5.6-sol": 110},
    }], {key}, 1060.0)

    rates = [record["agent_token_rates"][0] for record in claimed["records"]]
    assert sum(rate["tokens"] for rate in rates) == pytest.approx(10.0)
    assert sum(rate["model_rates"]["unknown"]["tokens"] for rate in rates) == pytest.approx(10.0)
    service.store.close()


@pytest.mark.parametrize(
    ("model_rates", "expected"),
    (
        ({"gpt-sol": {"tokens": 25, "seconds": 30}}, {"gpt-sol": 25, "unknown": 75}),
        ({"gpt-sol": {"tokens": 150, "seconds": 30}, "gpt-terra": {"tokens": 50, "seconds": 30}}, {"gpt-sol": 75, "gpt-terra": 25}),
    ),
)
def test_statsd_model_partition_is_normalized_to_owning_agent_total(model_rates, expected):
    [rate] = statsd.PersistentStatsService._agent_token_rate_records({
        "1|0|codex": {"tokens": 100, "total": 100, "samples": 1, "seconds": 60, "model_rates": model_rates},
    })

    assert {model: item["tokens"] for model, item in rate["model_rates"].items()} == pytest.approx(expected)
    assert sum(item["tokens"] for item in rate["model_rates"].values()) == pytest.approx(rate["tokens"])
    assert {item["seconds"] for item in rate["model_rates"].values()} == {rate["seconds"]}


def test_statsd_recovery_generation_repairs_existing_model_partition_mismatch(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    bucket = stats_store.empty_bucket(960, 60)
    bucket["sequence"] = bucket["server_sequence"] = 1
    bucket["agent_token_rates"] = {
        "1|0|codex": {
            "label": "1:0:codex", "tokens": 100, "total": 100, "samples": 1, "seconds": 60,
            "model_rates": {"gpt-sol": {"tokens": 160, "total": 160, "samples": 1, "seconds": 60}},
        },
    }
    service.store.upsert_bucket(bucket)

    result = service.recover_agent_token_history([], now=1_000)
    repaired = service.store.bucket(960, 60)["agent_token_rates"]["1|0|codex"]

    assert result["changed"] is True
    assert repaired["model_rates"]["gpt-sol"]["tokens"] == pytest.approx(100)
    assert service.store.metadata_value(statsd.STATSD_AGENT_TOKEN_RECOVERY_MARKER) == str(statsd.STATSD_AGENT_TOKEN_RECOVERY_VERSION)
    service.store.close()


def test_statsd_empty_transcript_roster_does_not_consume_recovery_marker(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    result = service.recover_agent_token_history_from_rows([], now=1_000.0)

    assert result == {"ok": True, "changed": False, "reason": "no_transcript_rows"}
    assert service.store.metadata_value(statsd.STATSD_AGENT_TOKEN_RECOVERY_MARKER) is None
    service.store.close()


def test_stats_store_rolls_back_bucket_and_child_rows_when_one_child_is_invalid(tmp_path):
    store = stats_store.StatsStore(tmp_path / "stats.sqlite3")
    too_large = _bucket()
    too_large["clients"]["browser-a"]["overflow"] = "x" * (stats_store.STATS_STORE_MAX_JSON_BYTES + 1)

    with pytest.raises(ValueError, match="too large"):
        store.upsert_bucket(too_large)

    assert store.query_buckets() == []
    assert store.diagnostics()["children"] == {"stats_clients": 0, "stats_processes": 0, "stats_agent_rates": 0, "stats_host_metrics": 0}


def test_stats_store_retain_and_query_after_cursor_bound_growth(tmp_path):
    store = stats_store.StatsStore(tmp_path / "stats.sqlite3")
    for sequence in range(1, 5):
        store.upsert_bucket(_bucket(start=100 + sequence, sequence=sequence))

    assert [bucket["sequence"] for bucket in store.query_buckets(after_sequence=2)] == [3, 4]
    assert store.retain_after(104) == 2
    assert [bucket["sequence"] for bucket in store.query_buckets()] == [3, 4]


def test_statsd_service_has_single_writer_protocol_and_recovers_after_restart(tmp_path):
    socket_path = tmp_path / "statsd.sock"
    database_path = tmp_path / "stats.sqlite3"
    service = statsd.PersistentStatsService(socket_path, database_path, idle_seconds=10.0)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = statsd.StatsClient(socket_path, database_path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert client.healthy() is True
    assert client.request({"action": "upsert_bucket", "bucket": _bucket()})["ok"] is True
    assert client.request({"action": "query_buckets"})["buckets"] == [_bucket()]
    assert client.request({"action": "shutdown"}) == {"ok": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False

    restarted = statsd.PersistentStatsService(socket_path, database_path, idle_seconds=10.0)
    restarted_worker = threading.Thread(target=restarted.run, daemon=True)
    restarted_worker.start()
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.request({"action": "query_buckets"})["buckets"] == [_bucket()]
    assert client.request({"action": "shutdown"}) == {"ok": True}
    restarted_worker.join(timeout=2.0)


def test_statsd_restart_recovers_durable_sampler_owner_and_does_not_idle_exit(tmp_path):
    owner_path = tmp_path / "background-owner" / "owner.json"
    owner_path.parent.mkdir()
    owner = {
        "status": "owner",
        "roles": ["stats-sampler"],
        "control_socket": str(tmp_path / "owner.sock"),
        "generation_id": "generation-1",
        "last_heartbeat": time.time(),
        "pid": os.getpid(),
        "port": 9991,
    }
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    service = statsd.PersistentStatsService(
        tmp_path / "statsd.sock",
        tmp_path / "stats.sqlite3",
        idle_seconds=1.0,
        sampler_owner_path=owner_path,
    )
    service.last_client_at = time.monotonic() - 10

    assert service._sampler_owner_for_cycle()["generation_id"] == "generation-1"
    assert service._idle_shutdown_ready() is False
    service.store.close()


def test_stats_client_registry_spawns_statsd_with_its_requested_database(tmp_path):
    socket_path = tmp_path / "state with spaces" / "statsd.sock"
    database_path = tmp_path / "state with spaces" / "stats.sqlite3"
    client = statsd.StatsClient(socket_path, database_path)

    assert client.ensure_started() is True
    assert client.request({"action": "upsert_bucket", "bucket": _bucket()})["ok"] is True
    assert client.history(client_id="browser-a")["records"]
    assert database_path.exists() is True
    assert client.request({"action": "shutdown"}) == {"ok": True}


def test_stats_store_does_not_leave_a_partial_sqlite_transaction(tmp_path):
    store = stats_store.StatsStore(tmp_path / "stats.sqlite3")
    store.upsert_bucket(_bucket(sequence=1))
    connection = store._connection()
    with pytest.raises(sqlite3.IntegrityError):
        with connection:
            connection.execute("INSERT INTO stats_buckets(start,duration,sequence,server_sequence,bucket_json) VALUES(?,?,?,?,?)", (101, 1, 2, 2, "{}"))
            connection.execute("INSERT INTO stats_clients VALUES(?,?,?,?,?)", (101, 1, "broken", 2, None))
    assert [bucket["sequence"] for bucket in store.query_buckets()] == [1]


def test_stats_store_replace_is_one_rollback_safe_transaction(tmp_path):
    store = stats_store.StatsStore(tmp_path / "stats.sqlite3")
    store.upsert_bucket(_bucket(sequence=1))
    invalid = _bucket(start=200, sequence=2)
    invalid["agent_token_rates"]["overflow"] = {"blob": "x" * (stats_store.STATS_STORE_MAX_JSON_BYTES + 1)}

    with pytest.raises(ValueError, match="too large"):
        store.replace_buckets([_bucket(start=101, sequence=2), invalid])

    assert [bucket["sequence"] for bucket in store.query_buckets()] == [1]


def test_statsd_history_uses_cached_cursor_delta_and_point_budget(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    for sequence in range(1, 7):
        bucket = _bucket(start=100 + sequence, sequence=sequence)
        bucket["clients"]["browser-a"]["api_count"] = float(sequence)
        service.store.upsert_bucket(bucket)

    first = service.handle({"action": "history", "client_id": "browser-a", "max_points": 2})
    cached = service.handle({"action": "history", "client_id": "browser-a", "max_points": 2})
    delta = service.handle({"action": "history", "client_id": "browser-a", "after_sequence": 4})

    assert first == cached
    assert len(first["records"]) <= 2
    assert sum(record["api_count"] for record in first["records"]) == sum(range(1, 7))
    assert [record["sequence"] for record in delta["records"]] == [5, 6]
    assert delta["coverage"]["cursor"] == 6
    service.store.close()


def test_statsd_merge_records_owns_browser_deltas_and_cursor(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    first = service.handle({"action": "merge_records", "client_id": "browser-a", "now": 1000, "records": [{"start": 999, "api_count": 2}, {"start": 999, "latency_count": 1, "latency_total_ms": 5}]})
    history = service.handle({"action": "history", "client_id": "browser-a"})
    delta = service.handle({"action": "history", "client_id": "browser-a", "after_sequence": first["sequence"] - 1})

    assert first == {"ok": True, "changed": 2, "sequence": 2}
    assert history["records"][0]["api_count"] == 2
    assert history["records"][0]["latency_total_ms"] == 5
    assert delta["records"]
    service.store.close()


def test_statsd_merge_and_history_is_one_atomic_post_acknowledgement(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    response = service.handle({
        "action": "merge_and_history",
        "client_id": "browser-a",
        "now": 1000,
        "records": [{"start": 999, "api_count": 2}],
        "query": {"since": 0, "client_id": "browser-a"},
    })

    assert response["ok"] is True
    assert response["merged"] == {"ok": True, "changed": 1, "sequence": 1}
    assert response["history"]["records"][0]["api_count"] == 2
    service.store.close()


def test_statsd_merge_records_compacts_browser_history_into_retention_tiers(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    now = 200000.0
    records = [{"start": now - age + index, "api_count": 1} for age in (90 * 60, 3 * 60 * 60, 6 * 60 * 60, 10 * 60 * 60, 18 * 60 * 60) for index in range(20)]

    merged = service.merge_records(records, client_id="browser-a", now=now)
    history = service.handle({"action": "history", "client_id": "browser-a"})

    assert merged["changed"] == 100
    assert len(history["records"]) <= 10
    assert sum(record["api_count"] for record in history["records"]) == 100
    assert {record["duration"] for record in history["records"]} == {600}
    service.store.close()


def test_statsd_merge_server_records_owns_global_process_and_host_deltas(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    merged = service.handle({"action": "merge_server_records", "now": 1000, "records": [{
        "start": 999,
        "cpu_total_percent": 12,
        "cpu_count": 1,
        "system_cpu_total_percent": 50,
        "system_cpu_count": 1,
        "process": {"id": "port:18771", "label": "yolomux.py :18771", "pid": 42, "port": 18771, "started_at": 900, "cpu_percent": 12, "cpu_count": 1},
        "host_metrics": {"system_memory_used_bytes": 10, "system_memory_capacity_bytes": 100},
    }]})
    history = service.handle({"action": "history"})

    assert merged["changed"] == 1
    record = history["records"][0]
    assert record["cpu_total_percent"] == 12
    assert record["servers"]["port:18771"]["cpu_total_percent"] == 12
    assert record["host_metrics"]["system_memory_used_total_bytes"] == 10
    service.store.close()


def test_statsd_returns_preencoded_bounded_history_bytes(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.upsert_bucket(_bucket())

    response = service.handle({"action": "write_encoded_history", "client_id": "browser-a"})

    assert response["ok"] is True
    assert response["encoding"] == "json"
    assert response["size"] == len(response["bytes"].encode("utf-8"))


def test_statsd_rpc_returns_preencoded_history_as_binary_without_metadata_copy(tmp_path):
    socket_path = tmp_path / "statsd.sock"
    database_path = tmp_path / "stats.sqlite3"
    service = statsd.PersistentStatsService(socket_path, database_path, idle_seconds=10.0)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = statsd.StatsClient(socket_path, database_path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.request({"action": "upsert_bucket", "bucket": _bucket()})["ok"] is True

    response, encoded = client.encoded_history(client_id="browser-a")

    assert response == {"ok": True, "encoding": "json", "size": len(encoded)}
    assert json.loads(encoded)["records"][0]["sequence"] == 1
    assert "bytes" not in response
    assert client.request({"action": "shutdown"}) == {"ok": True}
    worker.join(timeout=2.0)


def test_statsd_history_wire_shape_for_mixed_bucket(tmp_path):
    bucket = stats_store.empty_bucket(990, 1)
    bucket.update({"sequence": 2, "server_sequence": 2, "cpu_total_percent": 12.0, "cpu_count": 1.0})
    bucket["clients"]["browser-a"] = {**stats_store.empty_client_bucket(), "sequence": 1, "api_count": 2.0, "latency_total_ms": 5.0, "latency_count": 1.0}
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.replace_buckets([bucket])

    history = service.handle({"action": "history", "client_id": "browser-a"})

    assert history["ok"] is True
    assert history["sequence"] == 2
    assert history["latest_sequence"] == 2
    assert history["coverage"]["returned_records"] == 1
    assert history["records"] == [{
        "start": 990,
        "duration": 1,
        "sequence": 2,
        "clients": {"browser-a": {"api_count": 2.0, "sse_count": 0.0, "latency_total_ms": 5.0, "latency_count": 1.0, "bandwidth_bytes": 0.0, "heartbeat_count": 0.0, "disconnected_ms": 0.0}},
        "servers": {},
        "api_count": 2.0,
        "sse_count": 0.0,
        "latency_total_ms": 5.0,
        "latency_count": 1.0,
        "bandwidth_bytes": 0.0,
        "heartbeat_count": 0.0,
        "disconnected_ms": 0.0,
        "cpu_total_percent": 12.0,
        "cpu_count": 1.0,
        "system_cpu_total_percent": 0.0,
        "system_cpu_count": 0.0,
        "ask_agent_total": 0.0,
        "run_agent_total": 0.0,
        "transition_agent_total": 0.0,
        "idle_agent_total": 0.0,
        "active_agent_total": 0.0,
        "inactive_agent_total": 0.0,
        "agent_activity_samples": 0.0,
        "tokens_per_agent_total": 0.0,
        "agent_token_samples": 0.0,
        "agent_token_rates": [],
        "host_metrics": stats_store.empty_host_metrics(),
    }]
    service.store.close()


def test_statsd_imports_legacy_v4_history_once_and_preserves_the_marker(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "stats-client-history-v4.json").write_text(json.dumps({
        "version": 4,
        "raw_buckets": [[100, 1, 4, {"browser-a": {"sequence": 4, "api_count": 2}}]],
        "rollup_buckets": [],
    }), encoding="utf-8")
    (state_dir / "tmux-AI-status.json").write_text(json.dumps({
        "stats_history": {
            "raw_buckets": [[100, 1, 7, 7, *([0] * len(stats_store.SERVER_FIELDS)), {}, stats_store.empty_host_metrics()]],
            "rollup_buckets": [],
        },
    }), encoding="utf-8")
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    assert service.import_legacy_history_once(state_dir) == {"ok": True, "imported": True, "rows": 1}
    bucket = service.store.query_buckets()[0]
    assert bucket["sequence"] == 7
    assert bucket["clients"]["browser-a"]["api_count"] == 2
    assert service.import_legacy_history_once(state_dir) == {"ok": True, "imported": False, "reason": "already_imported", "rows": 1}
    service.store.close()


def test_statsd_imports_legacy_v3_history_as_a_compatible_fallback(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "stats-client-history-v3.json").write_text(json.dumps({
        "version": 3,
        "raw_buckets": [[100, 1, 4, {"browser-a": {"sequence": 4, "api_count": 2}}]],
        "rollup_buckets": [],
    }), encoding="utf-8")
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    assert service.import_legacy_history_once(state_dir) == {"ok": True, "imported": True, "rows": 1}
    assert service.store.query_buckets()[0]["clients"]["browser-a"]["api_count"] == 2
    service.store.close()


def test_statsd_legacy_import_prefers_v4_rows_over_overlapping_v3_rows(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    for version, api_count in ((4, 4), (3, 2)):
        (state_dir / f"stats-client-history-v{version}.json").write_text(json.dumps({
            "version": version,
            "raw_buckets": [[100, 1, version, {"browser-a": {"sequence": version, "api_count": api_count}}]],
            "rollup_buckets": [],
        }), encoding="utf-8")
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    assert service.import_legacy_history_once(state_dir) == {"ok": True, "imported": True, "rows": 1}
    bucket = service.store.query_buckets()[0]
    assert bucket["sequence"] == 4
    assert bucket["clients"]["browser-a"]["api_count"] == 4
    service.store.close()


def test_statsd_legacy_import_does_not_replace_existing_durable_history(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "stats-client-history-v4.json").write_text(json.dumps({
        "version": 4,
        "raw_buckets": [[100, 1, 4, {"browser-a": {"sequence": 4, "api_count": 2}}]],
        "rollup_buckets": [],
    }), encoding="utf-8")
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.upsert_bucket(_bucket(start=200, sequence=9))

    assert service.import_legacy_history_once(state_dir) == {"ok": True, "imported": False, "reason": "existing_statsd_history", "rows": 1}
    assert service.store.query_buckets() == [_bucket(start=200, sequence=9)]
    service.store.close()


def test_statsd_legacy_import_rolls_back_without_marking_on_invalid_bucket(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "stats-client-history-v4.json").write_text(json.dumps({
        "version": 4,
        "raw_buckets": [[100, 1, 4, {"browser-a": {"sequence": 4, "overflow": "x" * (stats_store.STATS_STORE_MAX_JSON_BYTES + 1)}}]],
        "rollup_buckets": [],
    }), encoding="utf-8")
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    with pytest.raises(ValueError, match="too large"):
        service.import_legacy_history_once(state_dir)

    assert service.store.query_buckets() == []
    assert service.store.metadata_value(statsd.STATSD_LEGACY_IMPORT_MARKER) is None
    service.store.close()
