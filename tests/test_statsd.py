import gzip
import json
import os
import sqlite3
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from yolomux_lib import statsd
from yolomux_lib.local_services import stats_store


class _FakePricingCatalog:
    def __init__(self):
        self.calls = []

    def resolve_rate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"] == "unknown":
            return None
        return SimpleNamespace(
            usd=Decimal("2.5"),
            scale=1_000_000,
            catalog_revision=7,
            source_url="https://prices.example/model",
            effective_from="2026-01-01T00:00:00Z",
        )

    def estimate_rate_band(self, **_kwargs):
        return SimpleNamespace(
            minimum=SimpleNamespace(usd=Decimal("0.5"), scale=1_000_000, catalog_revision=7, source_url="https://prices.example/min"),
            maximum=SimpleNamespace(usd=Decimal("5.0"), scale=1_000_000, catalog_revision=7, source_url="https://prices.example/max"),
        )


class _MutablePricingCatalog(_FakePricingCatalog):
    def __init__(self):
        super().__init__()
        self.usd = Decimal("2.5")
        self.revision = 7

    def status(self):
        return {"state": "fresh", "catalog_revision": self.revision}

    def resolve_rate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            usd=self.usd,
            scale=1_000_000,
            catalog_revision=self.revision,
            source_url="https://prices.example/model",
            effective_from="2026-01-01T00:00:00Z",
        )


class _ContextPricingCatalog:
    def __init__(self):
        self.calls = []

    def resolve_rate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("profile") != "batch" or kwargs.get("service_tier") != "flex":
            return None
        return SimpleNamespace(
            usd=Decimal("1.25"),
            scale=1_000_000,
            catalog_revision=9,
            source_url="https://prices.example/batch",
            effective_from="2026-07-01T00:00:00Z",
        )


class _EstimatedPricingCatalog:
    def resolve_rate(self, **kwargs):
        if kwargs.get("model") == "known":
            return SimpleNamespace(
                usd=Decimal("2.0"),
                scale=1_000_000,
                catalog_revision=3,
                source_url="https://prices.example/known",
                effective_from="2026-01-01T00:00:00Z",
            )
        return None

    def estimate_rate_band(self, **_kwargs):
        return SimpleNamespace(
            minimum=SimpleNamespace(usd=Decimal("1.0"), scale=1_000_000, catalog_revision=3, source_url="https://prices.example/min"),
            maximum=SimpleNamespace(usd=Decimal("7.5"), scale=1_000_000, catalog_revision=3, source_url="https://prices.example/max"),
        )


def _bucket(start=100, duration=1, sequence=1):
    bucket = stats_store.empty_bucket(start, duration)
    bucket.update({"sequence": sequence, "server_sequence": sequence, "cpu_total_percent": 12.5, "cpu_count": 1.0})
    bucket["clients"] = {"browser-a": {**stats_store.empty_client_bucket(), "sequence": sequence, "api_count": 2.0}}
    bucket["servers"] = {"port:17071": {**stats_store.empty_process_bucket(), "sequence": sequence, "label": "yolomux.py :17071", "port": 17071, "cpu_total_percent": 12.5, "cpu_count": 1.0}}
    bucket["agent_token_rates"] = {"17071|0|codex": {"label": "17071:0:codex", "total": 8.0, "samples": 1.0, "tokens": 8.0}}
    bucket["host_metrics"] = {**stats_store.empty_host_metrics(), "cpu_processes": {"python": {"label": "python", "total_percent": 12.5, "samples": 1.0}}}
    return bucket


def _real_schema_history_bucket(start, duration, sequence):
    """Dense production-shaped stats row for deterministic history budgets."""
    bucket = stats_store.empty_bucket(start, duration)
    bucket.update({
        "sequence": sequence,
        "server_sequence": sequence,
        "cpu_total_percent": 40.0 * duration,
        "cpu_count": float(duration),
        "tokens_per_agent_total": 80.0 * duration,
        "agent_token_samples": float(duration),
    })
    bucket["clients"] = {
        "browser-a": {
            **stats_store.empty_client_bucket(),
            "sequence": sequence,
            "api_count": 2.0 * duration,
            "latency_total_ms": 20.0 * duration,
            "latency_count": 2.0 * duration,
            "bandwidth_bytes": 2048.0 * duration,
        }
    }
    bucket["servers"] = {
        f"port:{9900 + index}": {
            **stats_store.empty_process_bucket(),
            "sequence": sequence,
            "label": f"yolomux.py :{9900 + index}",
            "pid": 10_000 + index,
            "port": 9900 + index,
            "started_at": start - 3600,
            "cpu_total_percent": float((index + 1) * duration),
            "cpu_count": float(duration),
        }
        for index in range(4)
    }
    bucket["agent_token_rates"] = {
        f"session|{index}|codex": {
            "label": f"session:{index}:codex",
            "total": 10.0 * duration,
            "samples": float(duration),
            "tokens": 10.0 * duration,
            "seconds": float(duration),
            "source": "transcript",
            "model_rates": {
                f"gpt-{index % 3}": {
                    "total": 10.0 * duration,
                    "samples": float(duration),
                    "tokens": 10.0 * duration,
                    "seconds": float(duration),
                }
            },
        }
        for index in range(8)
    }
    for index in range(12):
        bucket["host_metrics"]["cpu_processes"][f"process-{index}"] = {
            "label": f"process-{index}",
            "total_percent": float((index + 1) * duration),
            "samples": float(duration),
        }
        bucket["host_metrics"]["memory_processes"][f"process-{index}"] = {
            "label": f"process-{index}",
            "used_total_bytes": float((index + 1) * 1024 * duration),
            "samples": float(duration),
        }
    bucket["cost_summary"]["components"] = [
        {
            "event_id": f"event-{start}-{index}",
            "provider": "openai",
            "model": f"gpt-{index % 3}",
            "model_evidence": "transcript",
            "effort": "high" if index % 2 else "",
            "direction": "output" if index % 3 == 0 else "input",
            "modality": "text",
            "cache_role": "read" if index % 3 == 1 else "none",
            "unit": "tokens",
            "root_thread_id": f"root-{index % 2}",
            "agent_thread_id": f"agent-{index}",
            "parent_thread_id": "",
            "depth": 0,
            "endpoint": "responses",
            "tool_name": "",
            "tmux_key": f"session|{index}|codex",
            "tmux_label": f"session:{index}:codex",
            "tmux_session": "session",
            "tmux_window": str(index),
            "tmux_window_label": f"agent-{index}",
            "agent_kind": "codex",
            "pricing_profile": "standard",
            "service_tier": "default",
            "backfill_source": "codex",
            "telemetry_complete": True,
            "source": "transcript",
            "priced": True,
            "catalog_revision": 1,
            "source_url": "https://example.invalid/pricing",
            "effective_from": "2026-01-01T00:00:00Z",
            "rate_usd": "1.0",
            "rate_scale": 1_000_000,
            "estimated": False,
            "quantity": float((index + 1) * duration),
            "micro_usd": (index + 1) * duration,
            "lower_micro_usd": (index + 1) * duration,
            "upper_micro_usd": (index + 1) * duration,
        }
        for index in range(8)
    ]
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


def test_stats_store_keeps_rollups_outside_raw_history_rows(tmp_path):
    store = stats_store.StatsStore(tmp_path / "stats.sqlite3")
    raw = _bucket(start=100, duration=1, sequence=1)
    rollup = _bucket(start=60, duration=60, sequence=2)
    store.upsert_bucket(raw)
    store.upsert_rollup(rollup)

    assert store.query_buckets() == [raw]
    assert store.rollup_bucket(60, 60) == rollup
    assert store.query_rollups(duration=60, start=60, end=120) == [rollup]
    store.close()


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


def test_statsd_tracks_independent_sampler_family_diagnostics_deterministically(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    cpu = {
        "cadence_seconds": 1.0, "attempts": 8, "successes": 7, "failures": 1,
        "late_cycles": 1, "missed_cycles": 1, "last_runtime_seconds": 0.03,
        "last_attempt_at": 100.0, "last_success_at": 99.0, "last_failure": "cpu delayed", "alive": True,
    }
    gpu = {
        "cadence_seconds": 10.0, "attempts": 2, "successes": 2, "failures": 0,
        "late_cycles": 0, "missed_cycles": 0, "last_runtime_seconds": 0.4,
        "last_attempt_at": 101.0, "last_success_at": 101.0, "last_failure": "", "alive": True,
    }
    assert service.handle({"action": "update_sampler_family", "family": "cpu", "status": cpu})["ok"] is True
    assert service.handle({"action": "update_sampler_family", "family": "gpu", "status": gpu})["ok"] is True
    status = service.common_status()
    assert status["sampler_families"] == {"cpu": cpu, "gpu": gpu}
    assert status["last_failure"] == "cpu: cpu delayed"
    assert status["sampler_last_cycle_seconds"] == 0.4
    assert status["sampler_late_cycles"] == 1
    assert status["sampler_missed_cycles"] == 1
    service.store.close()


def test_statsd_daemon_constructs_the_shared_pricing_catalog(monkeypatch, tmp_path):
    captured = {}

    class Catalog:
        pass

    class Service:
        def __init__(self, socket_path, database_path, *, idle_seconds, sampler_owner_path, pricing_catalog):
            captured.update(socket_path=socket_path, database_path=database_path, idle_seconds=idle_seconds, sampler_owner_path=sampler_owner_path, pricing_catalog=pricing_catalog)

        def run(self):
            return 17

    monkeypatch.setattr(statsd, "PricingCatalog", Catalog)
    monkeypatch.setattr(statsd, "PersistentStatsService", Service)

    assert statsd.main(["--serve", "--socket", str(tmp_path / "statsd.sock"), "--database", str(tmp_path / "stats.sqlite3")]) == 17
    assert isinstance(captured["pricing_catalog"], Catalog)


def test_statsd_defers_database_migration_until_listener_owns_singleton(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    events = []
    monkeypatch.setattr(service, "import_legacy_history_once", lambda: events.append("import") or {"ok": True})
    monkeypatch.setattr(service, "_schedule_cost_reprojection", lambda: events.append("reproject-scheduled") or {"ok": True})

    class Thread:
        def __init__(self, *, target, name, daemon):
            events.append(("thread", target.__name__, name, daemon))

        def start(self):
            events.append("sampler-start")

        def is_alive(self):
            return False

    def fake_runtime(**kwargs):
        assert events == []
        events.append("listener-owned")
        kwargs["on_start"]()
        return 0

    monkeypatch.setattr(statsd.threading, "Thread", Thread)
    monkeypatch.setattr(statsd, "run_local_rpc_service", fake_runtime)

    assert service.run() == 0
    assert events == ["listener-owned", "import", "reproject-scheduled"]
    service.store.close()


def test_statsd_reprices_startup_history_in_bounded_keyset_turns(monkeypatch, tmp_path):
    """Startup schedules repricing; every listener turn rewrites only one page."""
    catalog = _MutablePricingCatalog()
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=catalog)
    for index in range(3):
        atom = {
            "event_id": f"event-{index}", "timestamp": 1000 + index, "source": "rollout", "provider": "openai", "model": "gpt-5.6",
            "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 100,
            "telemetry_complete": True,
        }
        service.merge_server_records([{"time": 1000 + index, "usage_atoms": [atom]}], now=1000 + index)
    catalog.usd = Decimal("3")
    catalog.revision = 8
    monkeypatch.setattr(statsd, "STATSD_PRICING_REPROJECTION_BATCH_BUCKETS", 1)

    assert service._schedule_cost_reprojection()["pending"] is True
    first = service._maybe_reproject_cost_summaries()
    assert first["pending"] is True
    assert first["processed"] == 1
    assert service.store.metadata_value(statsd.STATSD_PRICING_REPROJECTION_MARKER) is None
    # A ping/status can be served between these calls because no maintenance
    # loop occupies the listener or moves SQLite to a background thread.
    assert service.handle({"action": "ping"})["ok"] is True
    while service.pricing_reprojection is not None:
        service._maybe_reproject_cost_summaries()

    assert service.store.metadata_value(statsd.STATSD_PRICING_REPROJECTION_MARKER) == "8"
    assert all(record["cost_summary"]["total_micro_usd"] == 300 for record in service.handle({"action": "history"})["records"])
    service.store.close()


def test_stats_store_history_range_uses_primary_time_index_and_fast_sequence(tmp_path):
    store = stats_store.StatsStore(tmp_path / "stats.sqlite3")
    store.upsert_bucket(_bucket(sequence=17))

    plan = store._connection().execute(
        "EXPLAIN QUERY PLAN SELECT bucket_json FROM stats_buckets WHERE sequence > ? AND start + duration > ? AND start < ? ORDER BY start,duration LIMIT ?",
        (0, 1, 10_000, 100),
    ).fetchall()

    assert any("sqlite_autoindex_stats_buckets_1" in str(row[3]) for row in plan)
    assert store.latest_sequence() == 17
    store.close()


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


def test_statsd_persists_projected_usage_atoms_in_normal_and_compact_token_history(tmp_path):
    catalog = _FakePricingCatalog()
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=catalog)
    atom = {
        "event_id": "event-1", "timestamp": 1000, "source": "rollout", "transcript": "/tmp/rollout.jsonl", "provider": "openai", "model": "gpt-5.6",
        "model_evidence": "turn_context.payload.model", "effort": "high", "direction": "input", "modality": "text", "cache_role": "none", "unit": "tokens",
        "quantity": 100, "root_thread_id": "root", "agent_thread_id": "child", "parent_thread_id": "root", "depth": 1, "telemetry_complete": True,
        "tmux_key": "s|0|codex", "tmux_label": "s:0:codex", "tmux_session": "s", "tmux_window": "0", "tmux_window_label": "0:codex", "agent_kind": "codex",
    }
    result = service.merge_server_records([
        {"time": 1000, "usage_atoms": [atom]},
        {"time": 1000, "usage_atoms": [atom]},  # stable event id makes replay idempotent
        {"time": 1000, "usage_atoms": [{**atom, "event_id": "event-2", "model": "unknown", "quantity": 8, "telemetry_complete": False}]},
    ], now=1000)
    normal_history = service.handle({"action": "history"})
    history = service.handle({"action": "history", "token_resolution_seconds": 60})
    summary = normal_history["records"][0]["cost_summary"]
    compact = history["agent_token_history"]["records"][0]["cost_summary"]

    assert result["changed"] == 2
    assert summary["total_micro_usd"] == 250
    assert summary["known_micro_usd"] == 250
    assert summary["lower_micro_usd"] == 250
    assert summary["upper_micro_usd"] == 290
    assert summary["complete"] is False
    assert summary["unpriced_count"] == 1
    assert summary["unpriced_token_quantity"] == 8
    assert summary["catalog_revision"] == 7
    assert summary["active_catalog_revision"] == 0
    assert summary["freshness"] == "unknown"
    assert summary["components"][0]["effort"] == "high"
    assert summary["components"][0]["source_url"] == "https://prices.example/model"
    assert summary["components"][0]["effective_from"] == "2026-01-01T00:00:00Z"
    assert summary["components"][0]["rate_usd"] == "2.5"
    assert summary["models"][0]["model"] == "gpt-5.6"
    assert summary["models"][0]["input_micro_usd"] == 250
    assert summary["models"][0]["token_quantity"] == 100
    assert summary["models"][0]["input_tokens"] == 100
    assert summary["models"][0]["cache_tokens"] == 0
    assert summary["models"][0]["output_tokens"] == 0
    assert summary["models"][0]["cache_micro_usd"] == 0
    assert summary["models"][0]["output_micro_usd"] == 0
    assert summary["models"][0]["other_micro_usd"] == 0
    assert summary["sources"][0]["agent_thread_id"] == "child"
    assert summary["sources"][0]["transcript"] == "/tmp/rollout.jsonl"
    assert summary["sources"][0]["input_micro_usd"] == 250
    assert summary["sources"][0]["lower_micro_usd"] == 250
    assert summary["sources"][0]["upper_micro_usd"] == 290
    assert summary["sources"][0]["token_quantity"] == 108
    assert summary["tmux_windows"] == [{
        "tmux_key": "s|0|codex",
        "tmux_label": "s:0:codex",
        "tmux_session": "s",
        "tmux_window": "0",
        "tmux_window_label": "0:codex",
        "agent_kind": "codex",
        "quantity": 108.0,
        "micro_usd": 250,
            "count": 2,
            "unpriced_count": 1,
            "unpriced_token_quantity": 8.0,
        "lower_micro_usd": 250,
        "upper_micro_usd": 290,
        "input_micro_usd": 250,
        "cache_micro_usd": 0,
        "output_micro_usd": 0,
        "other_micro_usd": 0,
        "input_lower_micro_usd": 250,
        "cache_lower_micro_usd": 0,
        "output_lower_micro_usd": 0,
        "other_lower_micro_usd": 0,
        "input_upper_micro_usd": 290,
        "cache_upper_micro_usd": 0,
        "output_upper_micro_usd": 0,
        "other_upper_micro_usd": 0,
        "token_quantity": 108.0,
        "input_tokens": 108.0,
        "cache_tokens": 0.0,
        "output_tokens": 0.0,
        "other_tokens": 0.0,
    }]
    assert compact == summary
    assert catalog.calls[0]["timestamp"] == "1970-01-01T00:16:40Z"
    service.store.close()


def test_statsd_cost_summary_exposes_lower_upper_bounds_for_unpriced_usage():
    catalog = _EstimatedPricingCatalog()
    known = statsd.projected_usage_component({
        "event_id": "known", "timestamp": 1, "provider": "openai", "model": "known", "direction": "input",
        "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 100, "telemetry_complete": True,
    }, catalog)
    unknown = statsd.projected_usage_component({
        "event_id": "unknown", "timestamp": 1, "provider": "openai", "model": "unknown-new-model", "direction": "output",
        "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 100, "telemetry_complete": True,
    }, catalog)
    summary = statsd.cost_summary_response({"components": [known, unknown]})

    assert known is not None and known["priced"] is True and known["estimated_lower_micro_usd"] == known["micro_usd"] == 200
    assert unknown is not None and unknown["priced"] is False and unknown["micro_usd"] == 0
    assert unknown["estimated_lower_micro_usd"] == 0
    assert unknown["estimated_upper_micro_usd"] == 750
    assert summary["lower_micro_usd"] == 200
    assert summary["known_micro_usd"] == 200
    assert summary["total_micro_usd"] == 200
    assert summary["upper_micro_usd"] == 950
    assert summary["lower_micro_usd"] <= summary["known_micro_usd"] <= summary["upper_micro_usd"]
    assert summary["complete"] is False
    assert summary["models"][0]["lower_micro_usd"] == 200
    assert sum(row["upper_micro_usd"] for row in summary["models"]) == summary["upper_micro_usd"]


def test_statsd_projects_observed_profile_and_service_tier_without_default_fallback():
    catalog = _ContextPricingCatalog()
    component = statsd.projected_usage_component({
        "event_id": "profiled", "timestamp": 1_784_332_800, "source": "direct", "provider": "openai", "model": "gpt-5.6",
        "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 1_000_000,
        "pricing_profile": "batch", "service_tier": "flex", "telemetry_complete": True,
    }, catalog)

    assert component is not None and component["priced"] is True
    assert component["micro_usd"] == 1_250_000
    assert component["pricing_profile"] == "batch"
    assert component["service_tier"] == "flex"
    assert catalog.calls[0]["profile"] == "batch"
    assert catalog.calls[0]["service_tier"] == "flex"


def test_statsd_projects_billable_text_image_and_claude_cache_components_exactly():
    class Catalog:
        prices = {
            ("openai", "input", "text", "none"): "2",
            ("openai", "input", "image", "none"): "3",
            ("openai", "output", "image", "none"): "4",
            ("anthropic", "input", "text", "none"): "1",
            ("anthropic", "input", "text", "read"): "0.1",
            ("anthropic", "input", "text", "write_5m"): "1.25",
            ("anthropic", "input", "text", "write_1h"): "2",
            ("anthropic", "output", "text", "none"): "5",
        }

        def resolve_rate(self, **kwargs):
            usd = self.prices.get((kwargs["provider"], kwargs["direction"], kwargs["modality"], kwargs["cache_role"]))
            return None if usd is None else SimpleNamespace(usd=Decimal(usd), scale=1_000_000, catalog_revision=1, source_url="https://prices.example/exact", effective_from="2026-01-01T00:00:00Z")

    catalog = Catalog()
    atoms = [
        {"event_id": "text", "timestamp": 1, "provider": "openai", "model": "gpt-image-2", "direction": "input", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 1_000_000, "telemetry_complete": True},
        {"event_id": "image-in", "timestamp": 1, "provider": "openai", "model": "gpt-image-2", "direction": "input", "modality": "image", "cache_role": "none", "unit": "tokens", "quantity": 1_000_000, "telemetry_complete": True},
        {"event_id": "image-out", "timestamp": 1, "provider": "openai", "model": "gpt-image-2", "direction": "output", "modality": "image", "cache_role": "none", "unit": "tokens", "quantity": 1_000_000, "telemetry_complete": True},
        *[{"event_id": role, "timestamp": 1, "provider": "anthropic", "model": "claude-test", "direction": direction, "modality": "text", "cache_role": role, "unit": "tokens", "quantity": 1_000_000, "telemetry_complete": True} for role, direction in (("none", "input"), ("read", "input"), ("write_5m", "input"), ("write_1h", "input"), ("none-output", "output"))],
    ]
    # The output component uses normal cache role ``none``; retain unique event
    # ids without distorting the catalog lookup dimensions.
    atoms[-1]["cache_role"] = "none"
    components = [statsd.projected_usage_component(atom, catalog) for atom in atoms]
    summary = statsd.cost_summary_response({"components": components})

    assert all(component and component["priced"] for component in components)
    assert summary["total_micro_usd"] == 18_350_000
    assert summary["lower_micro_usd"] == summary["upper_micro_usd"] == summary["total_micro_usd"]
    assert summary["complete"] is True
    assert sum(row["micro_usd"] for row in summary["models"]) == summary["total_micro_usd"]


def test_statsd_projection_keeps_unknown_unpriced_and_rounds_tiny_nonzero_micro_usd():
    class Catalog:
        def resolve_rate(self, **_kwargs):
            return SimpleNamespace(usd=Decimal("0.000001"), scale=1, catalog_revision=1, source_url="https://prices.example/tiny", effective_from="2026-01-01T00:00:00Z")

    unknown = statsd.projected_usage_component({"event_id": "unknown", "timestamp": 1, "provider": "openai", "model": "unknown", "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 1}, Catalog())
    tiny = statsd.projected_usage_component({"event_id": "tiny", "timestamp": 1, "provider": "openai", "model": "gpt-test", "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 1}, Catalog())
    zero = statsd.projected_usage_component({"event_id": "zero", "timestamp": 1, "provider": "openai", "model": "gpt-test", "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 0}, Catalog())

    assert unknown is not None and unknown["priced"] is False and unknown["micro_usd"] == 0
    assert tiny is not None and tiny["priced"] is True and tiny["micro_usd"] == 1
    assert zero is None


def test_statsd_cost_summary_exposes_local_catalog_freshness_without_history_fetch(tmp_path):
    catalog = _MutablePricingCatalog()
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=catalog)
    atom = {
        "event_id": "event-1", "timestamp": 1000, "source": "rollout", "provider": "openai", "model": "gpt-5.6",
        "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 100,
        "telemetry_complete": True,
    }
    service.merge_server_records([{"time": 1000, "usage_atoms": [atom]}], now=1000)
    summary = service.handle({"action": "history"})["records"][0]["cost_summary"]

    assert summary["catalog_revision"] == 7
    assert summary["active_catalog_revision"] == 7
    assert summary["freshness"] == "fresh"
    service.store.close()


def test_statsd_usage_atom_backfill_marks_only_complete_rosters_and_is_idempotent(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("\n".join(json.dumps(item) for item in [
        {"type": "session_meta", "payload": {"id": "root"}},
        {"timestamp": 990, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "med"}},
        {"timestamp": 1000, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}},
    ]) + "\n", encoding="utf-8")
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    rows = [{"transcript": str(transcript), "kind": "codex"}]

    first = service.migrate_usage_atom_history_from_rows(rows, now=1010)
    second = service.migrate_usage_atom_history_from_rows(rows, now=1010)
    partial = statsd.PersistentStatsService(tmp_path / "partial.sock", tmp_path / "partial.sqlite3")
    incomplete = partial.migrate_usage_atom_history_from_rows([{"transcript": str(tmp_path / "gone.jsonl"), "kind": "codex"}], now=1010)

    assert first == {"ok": True, "changed": True, "sources": 1, "missing": 0, "complete": True}
    assert second["reason"] == "already_migrated"
    assert service.handle({"action": "history"})["records"][0]["cost_summary"]["total_micro_usd"] == 38
    assert service.handle({"action": "history"})["usage_atom_backfill"] == {"state": "complete", "sources": 1, "missing": 0}
    assert incomplete["complete"] is False
    assert partial.store.metadata_value(statsd.STATSD_USAGE_ATOM_MIGRATION_MARKER) is None
    assert partial.handle({"action": "history"})["usage_atom_backfill"] == {"state": "partial", "sources": 0, "missing": 1}
    service.store.close()
    partial.store.close()


def test_statsd_usage_atom_backfill_uses_captured_family_byte_high_water(monkeypatch, tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    initial = [
        {"type": "session_meta", "payload": {"id": "root"}},
        {"timestamp": 1000, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high"}},
        {"timestamp": 1001, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}},
    ]
    appended = {"timestamp": 1002, "payload": {"info": {"total_token_usage": {"input_tokens": 20, "output_tokens": 9}}}}
    transcript.write_text("\n".join(json.dumps(item) for item in initial) + "\n", encoding="utf-8")
    original = statsd.session_files.transcript_usage_atoms

    def append_after_snapshot(path, kind, **kwargs):
        transcript.write_text(transcript.read_text(encoding="utf-8") + json.dumps(appended) + "\n", encoding="utf-8")
        return original(path, kind, **kwargs)

    monkeypatch.setattr(statsd.session_files, "transcript_usage_atoms", append_after_snapshot)
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    result = service.migrate_usage_atom_history_from_rows([{"transcript": str(transcript), "kind": "codex"}], now=1010)
    components = service.handle({"action": "history"})["records"][0]["cost_summary"]["components"]

    assert result["complete"] is True
    assert {(item["direction"], item["quantity"]) for item in components} == {("input", 10.0), ("output", 5.0)}
    service.store.close()


def test_statsd_usage_atom_backfill_replaces_only_prior_staged_root_family_atoms(tmp_path):
    transcript = tmp_path / "rollout.jsonl"

    def write(output):
        transcript.write_text("\n".join(json.dumps(item) for item in [
            {"type": "session_meta", "payload": {"id": "root"}},
            {"timestamp": 1000, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high"}},
            {"timestamp": 1001, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": output}}}},
        ]) + "\n", encoding="utf-8")

    write(5)
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    # The missing sibling keeps the marker open, simulating an interrupted/
    # partial roster that must safely resume with replacement.
    assert service.migrate_usage_atom_history_from_rows([{"transcript": str(transcript), "kind": "codex"}, {"transcript": str(tmp_path / "missing.jsonl"), "kind": "codex"}], now=1010)["complete"] is False
    write(7)
    assert service.migrate_usage_atom_history_from_rows([{"transcript": str(transcript), "kind": "codex"}], now=1010)["complete"] is True
    components = service.handle({"action": "history"})["records"][0]["cost_summary"]["components"]

    assert {(item["direction"], item["quantity"]) for item in components} == {("input", 10.0), ("output", 7.0)}
    service.store.close()


def test_statsd_usage_atom_backfill_coalesces_busy_bucket_before_bounded_persistence(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    atoms = [{
        "event_id": f"event-{index}", "timestamp": 1000, "source": "rollout", "provider": "openai", "model": "gpt-5.6",
        "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 1,
        "telemetry_complete": True, "backfill_source": "/tmp/rollout.jsonl",
    } for index in range(1000)]

    assert service._replace_backfill_source_atoms("/tmp/rollout.jsonl", atoms, 1010) == 1
    record = service.handle({"action": "history"})["records"][0]

    assert len(record["cost_summary"]["components"]) == 1
    assert record["cost_summary"]["components"][0]["quantity"] == 1000
    assert service.store.diagnostics()["rows"] == 1
    service.store.close()


def test_statsd_usage_atom_backfill_repairs_interrupted_uncoalesced_sources(tmp_path):
    catalog = _FakePricingCatalog()
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=catalog)
    legacy = [statsd.projected_usage_component({
        "event_id": f"legacy-{index}", "timestamp": 1000, "source": "rollout", "provider": "openai", "model": "gpt-5.6",
        "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 1,
        "telemetry_complete": True, "backfill_source": "/tmp/legacy.jsonl",
    }, catalog) for index in range(100)]
    bucket = stats_store.empty_bucket(1000, 1)
    bucket["sequence"] = 1
    bucket["server_sequence"] = 1
    service._recalculate_usage_summary(bucket["cost_summary"], legacy)
    service.store.upsert_bucket(bucket)

    assert service._replace_backfill_source_atoms("/tmp/new.jsonl", [{
        "event_id": "new", "timestamp": 1000, "source": "rollout", "provider": "openai", "model": "gpt-5.6",
        "direction": "input", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 5,
        "telemetry_complete": True, "backfill_source": "/tmp/new.jsonl",
    }], 1010) == 1
    components = service.store.bucket(1000, 1)["cost_summary"]["components"]

    assert {(item["backfill_source"], item["quantity"]) for item in components} == {
        ("/tmp/legacy.jsonl", 100), ("/tmp/new.jsonl", 5),
    }
    service.store.close()


def test_statsd_reprojects_retained_usage_when_catalog_revision_changes_without_rewriting_tokens(tmp_path):
    catalog = _MutablePricingCatalog()
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=catalog)
    atom = {
        "event_id": "event-1", "timestamp": 1000, "source": "rollout", "provider": "openai", "model": "gpt-5.6",
        "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 100,
        "telemetry_complete": True,
    }
    service.merge_server_records([{"time": 1000, "usage_atoms": [atom], "tokens_per_agent_total": 11}], now=1000)
    before = service.handle({"action": "history"})["records"][0]
    catalog.usd = Decimal("3")
    catalog.revision = 8

    result = service._maybe_reproject_cost_summaries()
    after = service.handle({"action": "history"})["records"][0]

    assert result["changed"] == 1
    assert before["cost_summary"]["total_micro_usd"] == 250
    assert after["cost_summary"]["total_micro_usd"] == 300
    assert int(after["cost_summary"]["components"][0]["catalog_revision"]) == 8
    assert after["tokens_per_agent_total"] == 11
    assert service._maybe_reproject_cost_summaries()["reason"] in {"complete", "current_catalog"}
    service.store.close()


def test_statsd_backfill_marks_counter_only_legacy_bucket_lower_bound_without_deleting_output_history(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.merge_server_records([{
        "time": 1000,
        "tokens_per_agent_total": 12,
        "agent_token_samples": 1,
        "agent_token_rates": [{"key": "s|0|codex", "label": "s:0", "total": 12, "samples": 1, "tokens": 12, "seconds": 1}],
    }], now=1010)

    result = service.migrate_usage_atom_history_from_rows([], now=1010)
    bucket = service.handle({"action": "history"})["records"][0]

    assert result["complete"] is True
    assert bucket["tokens_per_agent_total"] == 12
    assert bucket["cost_summary"]["complete"] is False
    service.store.close()


def test_statsd_live_transcript_baseline_does_not_replay_historical_atoms(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("\n".join(json.dumps(item) for item in [
        {"type": "session_meta", "payload": {"id": "root"}},
        {"timestamp": 1000, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high"}},
        {"timestamp": 1001, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}},
    ]) + "\n", encoding="utf-8")
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    rows = [{"key": "s|0|codex", "label": "s:0", "transcript": str(transcript), "kind": "codex", "session": "s", "window": "0", "window_label": "0"}]

    baseline = service.claim_agent_token_deltas_from_rows(rows, {"s|0|codex"}, 1002)
    history = service.handle({"action": "history"})

    assert baseline["records"] == []
    assert baseline["persisted_records"] == 0
    assert history["records"] == []
    service.store.close()


def test_statsd_live_claim_persists_delta_before_returning_compact_response(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("\n".join(json.dumps(item) for item in [
        {"type": "session_meta", "payload": {"id": "root"}},
        {"timestamp": 1000, "type": "turn_context", "payload": {"model": "gpt-5.6", "effort": "high"}},
        {"timestamp": 1001, "payload": {"info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}},
    ]) + "\n", encoding="utf-8")
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    rows = [{"key": "s|0|codex", "label": "s:0", "transcript": str(transcript), "kind": "codex", "session": "s", "window": "0", "window_label": "0"}]
    service.claim_agent_token_deltas_from_rows(rows, {"s|0|codex"}, 1002)
    transcript.write_text(transcript.read_text(encoding="utf-8") + json.dumps(
        {"timestamp": 1061, "payload": {"info": {"total_token_usage": {"input_tokens": 12, "output_tokens": 25}}}}
    ) + "\n", encoding="utf-8")

    claimed = service.claim_agent_token_deltas_from_rows(rows, {"s|0|codex"}, 1062)
    history = service.handle({"action": "history"})

    assert claimed["records"] == []
    assert claimed["persisted_records"] > 0
    assert len(json.dumps(claimed)) < 256 * 1024
    assert sum(record["tokens_per_agent_total"] for record in history["records"]) == pytest.approx(20)
    assert sum(sum(rate["model_rates"]["gpt-5.6"]["tokens"] for rate in record["agent_token_rates"]) for record in history["records"]) == pytest.approx(20)
    cost_source = next(record["cost_summary"]["sources"][0] for record in history["records"] if record.get("cost_summary", {}).get("sources"))
    assert cost_source["tmux_key"] == "s|0|codex"
    assert cost_source["tmux_label"] == "s:0"
    assert cost_source["tmux_session"] == "s"
    assert cost_source["tmux_window"] == "0"
    assert cost_source["agent_kind"] == "codex"
    assert cost_source["transcript"] == str(transcript.resolve())
    cost_window = next(record["cost_summary"]["tmux_windows"][0] for record in history["records"] if record.get("cost_summary", {}).get("tmux_windows"))
    assert cost_window["tmux_key"] == "s|0|codex"
    assert cost_window["tmux_session"] == "s"
    assert cost_window["tmux_window"] == "0"
    assert cost_window["token_quantity"] > 0
    service.store.close()


def test_statsd_cost_components_are_idempotent_for_subagent_family_event_ids(monkeypatch, tmp_path):
    claude = tmp_path / "session.jsonl"
    claude_child = tmp_path / "session" / "subagents" / "agent.jsonl"
    claude_child.parent.mkdir(parents=True)
    codex = tmp_path / "rollout-parent.jsonl"
    codex_child = tmp_path / "rollout-child.jsonl"

    def claude_line(output_tokens):
        return json.dumps({
            "timestamp": 100,
            "type": "assistant",
            "message": {"id": "provider-message-1", "model": "claude-opus-4-8", "usage": {"output_tokens": output_tokens}, "content": []},
        }) + "\n"

    def codex_lines(thread_id, parent_thread_id, output_tokens):
        meta = {"id": thread_id}
        if parent_thread_id:
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
        return "\n".join(json.dumps(row) for row in [
            {"type": "session_meta", "payload": meta},
            {"timestamp": 100, "type": "turn_context", "payload": {"model": "gpt-5.6"}},
            {"timestamp": 101, "payload": {"info": {"total_token_usage": {"output_tokens": output_tokens}}}},
        ]) + "\n"

    claude.write_text(claude_line(11), encoding="utf-8")
    claude_child.write_text(claude_line(13), encoding="utf-8")
    codex.write_text(codex_lines("root", "", 17), encoding="utf-8")
    codex_child.write_text(codex_lines("child", "root", 19), encoding="utf-8")
    monkeypatch.setattr(statsd.session_files, "codex_transcript_family_paths", lambda _path: [codex, codex_child])
    atoms = statsd.session_files.transcript_usage_atoms(claude, "claude") + statsd.session_files.transcript_usage_atoms(codex, "codex")
    records = [{"time": atom.timestamp, "usage_atoms": [statsd.normalized_usage_atom(atom)]} for atom in atoms]
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())

    first = service.merge_server_records(records, now=200)
    before = [component for bucket in service.store.query_buckets() for component in bucket["cost_summary"]["components"]]
    second = service.merge_server_records(records, now=200)
    after = [component for bucket in service.store.query_buckets() for component in bucket["cost_summary"]["components"]]

    assert first["changed"] > 0
    assert second["changed"] == 0
    assert len(before) == len(atoms)
    assert len({(item["provider"], item["model"], item["source"], item["agent_thread_id"]) for item in before}) == len(atoms)
    assert sum(item["micro_usd"] for item in after) == sum(item["micro_usd"] for item in before)
    assert after == before
    service.store.close()


def test_statsd_cost_summary_truncation_marks_incomplete_lower_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(statsd, "STATS_COST_SUMMARY_MAX_COMPONENTS", 1)
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    atoms = [
        {"event_id": "kept", "timestamp": 1000, "provider": "openai", "model": "gpt-5.6", "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 10, "telemetry_complete": True},
        {"event_id": "evicted", "timestamp": 1000, "provider": "openai", "model": "gpt-5.6", "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens", "quantity": 99, "telemetry_complete": True},
    ]

    service.merge_server_records([{"time": 1000, "usage_atoms": atoms}], now=1000)
    raw_summary = service.store.bucket(1000, 1)["cost_summary"]
    public_summary = service.handle({"action": "history"})["records"][0]["cost_summary"]

    assert raw_summary["truncated"] is True
    assert raw_summary["lower_bound"] is True
    assert len(raw_summary["components"]) == 1
    assert public_summary["complete"] is False
    assert public_summary["known_micro_usd"] == public_summary["lower_micro_usd"] == public_summary["upper_micro_usd"]
    service.store.close()


def test_statsd_cost_component_bytes_leave_room_for_live_bucket_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(statsd, "STATS_COST_SUMMARY_MAX_BYTES", 1800)
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    atoms = [{
        "event_id": f"event-{index}",
        "timestamp": 1000,
        "provider": "openai",
        "model": "gpt-5.6",
        "direction": "output",
        "modality": "text",
        "cache_role": "none",
        "unit": "tokens",
        "quantity": 10,
        "telemetry_complete": True,
        "source": "/tmp/large.jsonl",
        "transcript": "/tmp/large.jsonl",
    } for index in range(20)]

    service.merge_server_records([{"time": 1000, "usage_atoms": atoms}], now=1000)
    service.merge_server_records([{
        "time": 1000,
        "tokens_per_agent_total": 5,
        "agent_token_samples": 1,
        "agent_token_rates": [{"key": "s|0|codex", "tokens": 5, "total": 5, "samples": 1, "seconds": 1}],
    }], now=1000)
    bucket = service.store.bucket(1000, 1)
    summary = bucket["cost_summary"]

    assert summary["truncated"] is True
    assert summary["lower_bound"] is True
    assert len(json.dumps(summary["components"], sort_keys=True, separators=(",", ":")).encode("utf-8")) <= 1800
    assert sum(1 for item in summary["components"] if item.get("transcript")) == 1
    assert bucket["tokens_per_agent_total"] == 5
    service.store.close()


def test_statsd_compaction_repairs_legacy_oversized_cost_projection_before_live_merge(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3", pricing_catalog=_FakePricingCatalog())
    atoms = [{
        "event_id": f"legacy-{index}", "timestamp": 1000, "provider": "openai", "model": "gpt-5.6",
        "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens",
        "quantity": 1, "telemetry_complete": True, "source": f"source-{index}-" + ("x" * 80),
    } for index in range(30)]
    service.merge_server_records([{"time": 1000, "usage_atoms": atoms}], now=1000)
    monkeypatch.setattr(statsd, "STATS_COST_SUMMARY_MAX_BYTES", 2400)

    merged = service.merge_records([{"start": 1060, "api_count": 1}], client_id="browser-a", now=1060)
    buckets = service.store.query_buckets()

    assert merged["ok"] is True
    assert any(bucket.get("clients", {}).get("browser-a", {}).get("api_count") == 1 for bucket in buckets)
    for bucket in buckets:
        summary = bucket.get("cost_summary", {})
        encoded = json.dumps(summary.get("components", []), sort_keys=True, separators=(",", ":")).encode("utf-8")
        assert len(encoded) <= 2400
    assert any(bucket.get("cost_summary", {}).get("truncated") is True for bucket in buckets)
    service.store.close()


def test_statsd_large_live_atom_claim_stays_below_rpc_metadata_limit(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    rows = [{"key": "s|0|codex", "label": "s:0", "transcript": "/large.jsonl", "kind": "codex"}]
    token_counts = iter((100, 200))
    model_counts = iter(({"gpt-5.6": 100}, {"gpt-5.6": 200}))
    atoms = [SimpleNamespace(
        event_id=f"event-{index}-" + ("e" * 480),
        timestamp=1003 + (index / 10),
        provider="openai",
        model="gpt-5.6",
        model_evidence="m" * 256,
        effort="high",
        direction="output",
        modality="text",
        cache_role="none",
        unit="tokens",
        quantity=1,
        root_thread_id="r" * 256,
        agent_thread_id="a" * 256,
        parent_thread_id="p" * 256,
        depth=0,
        endpoint="responses",
        tool_name="",
        call_id="c" * 256,
        pricing_profile="default",
        service_tier="default",
        telemetry_complete=True,
        source="s" * 512,
    ) for index in range(300)]
    monkeypatch.setattr(statsd.session_files, "transcript_generated_tokens", lambda *_args: next(token_counts))
    monkeypatch.setattr(statsd.session_files, "transcript_generated_tokens_by_model", lambda *_args: next(model_counts))
    monkeypatch.setattr(statsd.session_files, "transcript_usage_identity", lambda *_args: "stable")
    monkeypatch.setattr(statsd.session_files, "transcript_usage_atoms", lambda *_args: atoms)

    service.claim_agent_token_deltas_from_rows(rows, {"s|0|codex"}, 1002)
    claimed = service.claim_agent_token_deltas_from_rows(rows, {"s|0|codex"}, 1062)
    old_wire_records = [
        {"time": atom.timestamp, "usage_atoms": [statsd.normalized_usage_atom(atom)]}
        for atom in atoms
    ]
    history = service.handle({"action": "history"})

    assert len(json.dumps({"records": old_wire_records}).encode()) > 256 * 1024
    assert claimed["records"] == []
    assert len(json.dumps(claimed).encode()) < 256 * 1024
    assert claimed["persisted_records"] >= len(atoms)
    assert sum(record["tokens_per_agent_total"] for record in history["records"]) == pytest.approx(100)
    assert sum(len(record["cost_summary"]["components"]) for record in history["records"]) > 0
    service.store.close()


def test_statsd_completed_recovery_skips_transcript_parsing(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.set_metadata_value(statsd.STATSD_AGENT_TOKEN_RECOVERY_MARKER, str(statsd.STATSD_AGENT_TOKEN_RECOVERY_VERSION))
    monkeypatch.setattr(statsd.session_files, "transcript_generated_token_events", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("recovery reparsed transcript")))

    result = service.recover_agent_token_history_from_rows([
        {"key": "s|0|codex", "label": "s:0", "transcript": "/large.jsonl", "kind": "codex"},
    ], now=1062)

    assert result == {"ok": True, "changed": False, "reason": "already_recovered"}
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


def test_stats_client_registry_spawns_statsd_with_its_requested_database(tmp_path):
    socket_path = tmp_path / "state with spaces" / "statsd.sock"
    database_path = tmp_path / "state with spaces" / "stats.sqlite3"
    client = statsd.StatsClient(socket_path, database_path)

    assert client.ensure_started() is True
    assert client.request({"action": "upsert_bucket", "bucket": _bucket()})["ok"] is True
    assert client.history(client_id="browser-a")["records"]
    assert database_path.exists() is True
    assert client.request({"action": "shutdown"}) == {"ok": True}


def test_statsd_encoded_sample_can_omit_history_payload(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.upsert_bucket(_bucket(sequence=1))

    encoded = service.encoded_sample(
        {"time": 123.0, "pid": 456, "cpu_percent": 1.25},
        {"enabled": True},
        {"include_history": False, "since": 0, "client_id": "browser-a", "start": 0, "end": 0, "resolution_seconds": 0, "max_points": 0},
    )
    payload = json.loads(encoded.decode("utf-8"))

    assert payload["ok"] is True
    assert payload["pid"] == 456
    assert payload["shared_stats"] == {"enabled": True}
    assert "history" not in payload
    assert service.last_history_profile["cache_hit"] is False
    assert service.last_history_profile["coverage_ms"] == 0.0
    assert service.last_history_profile["query_ms"] == 0.0
    assert service.last_history_profile["assemble_ms"] >= 0.0
    assert service.last_history_profile["source_records"] == 0
    assert service.last_history_profile["returned_records"] == 0
    service.store.close()


def test_stats_client_runtime_status_exposes_sampler_and_history_diagnostics(monkeypatch, tmp_path):
    client = statsd.StatsClient(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    monkeypatch.setattr(client.registry, "status", lambda: {"healthy": True, "status": {
        "pid": 123, "sampler_alive": True, "sampler_last_cycle_seconds": 1.25,
        "sampler_late_cycles": 2, "sampler_missed_cycles": 3,
        "sampler_families": {"cpu": {"cadence_seconds": 1.0, "attempts": 4, "successes": 4}},
        "history_requests": 8, "history_cache_hits": 6,
        "history_profile": {"assemble_ms": 12.5, "source_records": 4, "returned_records": 2},
    }})
    monkeypatch.setattr(client.registry, "resources", lambda _pid: {"cpu_percent": 1.0})

    status = client.runtime_status()

    assert status["sampler_alive"] is True
    assert status["sampler_families"]["cpu"]["cadence_seconds"] == 1.0
    assert status["sampler_last_cycle_seconds"] == 1.25
    assert (status["sampler_late_cycles"], status["sampler_missed_cycles"]) == (2, 3)
    assert (status["history_requests"], status["history_cache_hits"]) == (8, 6)
    assert status["history_profile"] == {"assemble_ms": 12.5, "source_records": 4, "returned_records": 2}


def test_statsd_history_listener_stays_responsive_while_agent_token_scan_is_slow(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.upsert_bucket(_bucket(sequence=1))
    # A FIFO holds the real transcript reader in the child process. This is a
    # deterministic recovery stall without a test-only production hook.
    transcript = tmp_path / "slow.jsonl"
    os.mkfifo(transcript)

    started = time.perf_counter()
    accepted, _binary = service.handle_with_binary({
        "action": "claim_agent_token_deltas_from_rows",
        "rows": [{"key": "s|0|codex", "transcript": str(transcript), "kind": "codex"}],
        "seen_keys": ["s|0|codex"],
        "sample_time": 1002,
    })
    accept_elapsed = time.perf_counter() - started
    assert accepted["accepted"] is True
    assert accept_elapsed < 0.1
    assert service.agent_token_scan_worker is not None
    deadline = time.monotonic() + 2.0
    while not service.agent_token_scan_worker.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service.agent_token_scan_worker.is_alive() is True
    assert service.common_status()["active_task"] == "agent-token-scan"

    busy, _binary = service.handle_with_binary({
        "action": "claim_agent_token_deltas_from_rows",
        "rows": [],
        "seen_keys": [],
        "sample_time": 1003,
    })
    assert busy["busy"] is True

    started = time.perf_counter()
    history, _binary = service.handle_with_binary({"action": "history", "start": 0, "end": 0})
    history_elapsed = time.perf_counter() - started
    assert history["ok"] is True
    assert history["records"]
    assert history_elapsed < 0.1

    # Shutdown cancels a process-bound parse without waiting for it to unblock
    # and leaves the single-flight slot/temporary result cleaned up.
    service._shutdown()
    assert service.agent_token_scan_worker is None or not service.agent_token_scan_worker.is_alive()


def test_statsd_agent_token_scan_exception_releases_single_flight_slot(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    # A child parser failure reaches the SQLite owner as a terminal result;
    # draining it must release the single-flight slot for the next scan.
    first = {"scan_id": "scan-1"}
    service.agent_token_scan_id = first["scan_id"]
    service.agent_token_scan_sequence = 1
    service.agent_token_scan_result = {"error": "broken parser", "scan_id": first["scan_id"]}
    assert service._drain_agent_token_scan_result() is True
    completed = service.finish_agent_token_scan(first["scan_id"])
    assert completed["done"] is True
    assert completed["ok"] is False
    assert "broken parser" in completed["error"]

    second = service.start_agent_token_scan_from_rows([], set(), 1001)
    assert second["accepted"] is True
    assert second["scan_id"] != first["scan_id"]
    service._shutdown()


def test_statsd_agent_token_scan_does_not_advance_state_when_record_merge_fails(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    previous_state = {
        "s|0|codex": {
            "tokens": 10,
            "time": 1000,
            "label": "s:0:codex",
            "source": "transcript",
            "identity": "same-file",
            "models": {},
        }
    }
    service._set_agent_token_state(previous_state)
    monkeypatch.setattr(service, "merge_server_records", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("merge failed")))

    with pytest.raises(ValueError, match="merge failed"):
        service._persist_agent_token_scan(
            [{"key": "s|0|codex", "label": "s:0:codex", "tokens": 20, "source": "transcript", "identity": "same-file"}],
            [],
            {"s|0|codex"},
            1060,
            previous_state,
        )

    assert service._agent_token_state() == previous_state
    service.store.close()


def test_statsd_completed_agent_scan_persists_in_bounded_pages_without_blocking_history(monkeypatch, tmp_path):
    """A large completed child result is installed, then drained between RPC turns."""
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.upsert_bucket(_bucket(sequence=1))
    records = [{"time": 1000 + index, "usage_atoms": []} for index in range(130)]
    service.agent_token_scan_id = "scan-1"
    service.agent_token_scan_result = {
        "scan_id": "scan-1", "measurements": [], "atom_records": records,
        "seen_keys": [], "sample_time": 1200, "previous_state": {},
    }
    pages = []
    compact_flags = []
    monkeypatch.setattr(
        service,
        "merge_server_records",
        lambda page, **kwargs: (pages.append(len(page)), compact_flags.append(kwargs.get("compact")), {"ok": True, "changed": len(page)})[-1],
    )

    # The completion handoff does no SQLite merge, so a history request is not
    # queued behind all 130 records (the former synchronous behavior).
    assert service._drain_agent_token_scan_result() is True
    assert pages == []
    started = time.perf_counter()
    history, _binary = service.handle_with_binary({"action": "history", "start": 0, "end": 0})
    assert history["ok"] is True
    assert time.perf_counter() - started < 0.1
    assert service.common_status()["active_task"] == "agent-token-persist"

    while service.agent_token_scan_persistence is not None:
        service._drain_agent_token_scan_result()
    assert len(pages) == 130
    assert set(pages) == {1}
    assert set(compact_flags) == {False}
    assert service._agent_token_state() == {}
    done = service.finish_agent_token_scan("scan-1")
    assert done["done"] is True
    assert done["persisted_records"] == 130
    service.store.close()


def _spool_test_atom(index, timestamp):
    return SimpleNamespace(
        event_id=f"spool-event-{index}", timestamp=timestamp, provider="openai", model="gpt-5.6",
        model_evidence="turn_context.payload.model", effort="high", direction="output", modality="text",
        cache_role="none", unit="tokens", quantity=1, root_thread_id="root", agent_thread_id="agent",
        parent_thread_id="", depth=0, endpoint="responses", tool_name="", call_id="",
        pricing_profile="default", service_tier="default", telemetry_complete=True, source="transcript.jsonl",
    )


def test_statsd_large_agent_scan_streams_atoms_to_bounded_spool(monkeypatch, tmp_path):
    """The worker result stays O(agents), not O(all historical usage atoms)."""
    monkeypatch.setattr(statsd.session_files, "transcript_generated_tokens", lambda *_args: 50_010)
    monkeypatch.setattr(statsd.session_files, "transcript_generated_tokens_by_model", lambda *_args: {"gpt-5.6": 50_010})
    monkeypatch.setattr(statsd.session_files, "transcript_usage_identity", lambda *_args: "stable")

    def atoms(*_args):
        for index in range(50_000):
            yield _spool_test_atom(index, 1001 + (index % 50))

    monkeypatch.setattr(statsd.session_files, "iter_transcript_usage_atoms", atoms)
    spool = tmp_path / "atoms.sqlite3"
    measurements, target_state, count = statsd._scan_agent_token_rows_to_spool(
        [{"key": "s|0|codex", "transcript": str(tmp_path / "transcript.jsonl"), "kind": "codex"}],
        1060, {}, {"s|0|codex": 1000}, spool, include_atoms=True,
    )

    assert len(measurements) == 1
    assert target_state == {"s|0|codex": 1060}
    assert count == 50_000
    with sqlite3.connect(spool) as connection:
        assert connection.execute("SELECT COUNT(DISTINCT bucket_start || ':' || duration) FROM atoms").fetchone()[0] == 50
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.agent_token_atom_persistence = {
        "path": str(spool), "cursor_start": -1, "cursor_duration": -1, "cursor_event": "",
        "target_state": target_state, "sample_time": 1060, "count": count, "changed": 0,
    }
    pages = []
    monkeypatch.setattr(
        service, "merge_server_records",
        lambda records, **_kwargs: pages.append(sum(len(record["usage_atoms"]) for record in records)) or {"ok": True, "changed": len(records)},
    )
    assert service._drain_agent_token_atom_spool() is True
    assert pages == [statsd.STATSD_AGENT_TOKEN_ATOM_PAGE_RECORDS]
    assert service.agent_token_atom_persistence is not None
    service.agent_token_atom_persistence = None
    service.store.close()


def test_statsd_cpu_write_stays_responsive_during_atom_spool_maintenance(monkeypatch, tmp_path):
    """One bounded token-maintenance turn cannot starve the one-second writer."""

    spool = tmp_path / "latency-atoms.sqlite3"
    with sqlite3.connect(spool) as connection:
        connection.execute("CREATE TABLE atoms (bucket_start INTEGER NOT NULL, duration INTEGER NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(bucket_start, duration, event_key))")
        for index in range(16):
            normalized = statsd.normalized_usage_atom(_spool_test_atom(index, 1050.0))
            connection.execute(
                "INSERT INTO atoms VALUES(?, ?, ?, ?)",
                (1050, 1, statsd._agent_token_spool_identity(normalized), json.dumps(normalized)),
            )
    persistence = {
        "path": str(spool), "cursor_start": -1, "cursor_duration": -1, "cursor_event": "",
        "target_state": {"s|0|codex": 1060}, "sample_time": 1060, "count": 16, "changed": 0,
    }
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    monkeypatch.setattr(service, "_load_agent_token_atom_spool", lambda: dict(persistence))
    original_merge = service.merge_server_records
    maintenance_started = threading.Event()
    atom_page_sizes = []

    def bounded_slow_merge(records, **kwargs):
        atom_count = sum(len(record.get("usage_atoms") or []) for record in records)
        if atom_count:
            atom_page_sizes.append(atom_count)
            maintenance_started.set()
            time.sleep(0.2 * atom_count)
            return {"ok": True, "changed": len(records), "sequence": 0}
        return original_merge(records, **kwargs)

    monkeypatch.setattr(service, "merge_server_records", bounded_slow_merge)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = statsd.StatsClient(service.socket_path, service.store.path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.healthy() is True
    assert maintenance_started.wait(2.0) is True

    started = time.monotonic()
    response = client.merge_server_records(
        [{"time": time.time(), "cpu_total_percent": 1, "cpu_count": 1}],
        compact=False, refresh_rollups=False, timeout=0.9,
    )
    elapsed = time.monotonic() - started

    client.request({"action": "shutdown"})
    worker.join(timeout=2.0)
    assert response["ok"] is True
    assert atom_page_sizes[0] == 1
    assert elapsed < 0.9


def test_statsd_hot_write_does_not_probe_or_restart_on_contention(monkeypatch, tmp_path):
    client = statsd.StatsClient(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    requests = []
    monkeypatch.setattr(client, "request", lambda payload, timeout: requests.append((payload, timeout)) or {"ok": False, "error": "timed out"})
    monkeypatch.setattr(client, "ensure_started", lambda: pytest.fail("contention must not trigger health probes or launches"))

    response = client.merge_server_records(
        [{"time": 1000, "cpu_count": 1}], compact=False, refresh_rollups=False, timeout=0.9,
    )

    assert response == {"ok": False, "error": "timed out"}
    assert len(requests) == 1
    assert requests[0][1] == 0.9


def test_statsd_hot_write_starts_only_when_socket_transport_is_absent(monkeypatch, tmp_path):
    client = statsd.StatsClient(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    responses = iter([
        {"ok": False, "error": "No such file or directory", "_transport_error": "absent"},
        {"ok": True, "changed": 1, "version": statsd.STATSD_PROTOCOL_VERSION},
    ])
    monkeypatch.setattr(client, "request", lambda _payload, timeout: next(responses))
    starts = []
    monkeypatch.setattr(client, "ensure_started", lambda: starts.append(True) or True)

    response = client.merge_server_records([{"time": 1000, "cpu_count": 1}])

    assert response["ok"] is True
    assert starts == [True]


def test_statsd_hot_write_does_not_start_for_application_not_found(monkeypatch, tmp_path):
    client = statsd.StatsClient(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    monkeypatch.setattr(client, "request", lambda _payload, timeout: {"ok": False, "error": "model not found"})
    monkeypatch.setattr(client, "ensure_started", lambda: pytest.fail("application errors must not launch statsd"))

    assert client.update_sampler_family("cpu", {}) == {"ok": False, "error": "model not found"}


def test_statsd_browser_writes_use_live_socket_without_probe_or_inline_compaction(monkeypatch, tmp_path):
    client = statsd.StatsClient(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    requests = []
    monkeypatch.setattr(
        client, "request",
        lambda payload, timeout: requests.append((payload, timeout))
        or {"ok": True, "version": statsd.STATSD_PROTOCOL_VERSION},
    )
    monkeypatch.setattr(client, "ensure_started", lambda: pytest.fail("live browser writes must not health-probe"))

    assert client.merge_records([{"time": 1000, "api_count": 1}], client_id="browser-a")["ok"] is True
    assert client.merge_and_history(
        [{"time": 1001, "api_count": 1}], client_id="browser-a", query={"since": 0},
    )["ok"] is True

    assert [request[0]["action"] for request in requests] == ["merge_records", "merge_and_history"]
    assert all(request[0]["compact"] is False for request in requests)


def test_statsd_hot_write_replaces_stale_protocol_once(monkeypatch, tmp_path):
    client = statsd.StatsClient(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    responses = iter([
        {"ok": True, "changed": 1},
        {"ok": True, "changed": 1, "version": statsd.STATSD_PROTOCOL_VERSION},
    ])
    monkeypatch.setattr(client, "request", lambda payload, timeout: next(responses))
    starts = []
    monkeypatch.setattr(client, "ensure_started", lambda: starts.append(True) or True)

    response = client.merge_records([{"time": 1000, "api_count": 1}], client_id="browser-a")

    assert response["version"] == statsd.STATSD_PROTOCOL_VERSION
    assert starts == [True]


def test_statsd_live_token_rates_advance_while_historical_atom_spool_is_pending(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    previous_state = {
        "s|0|codex": {"tokens": 10, "time": 1000, "label": "s:0", "source": "transcript", "identity": "stable", "models": {"gpt-5.6": 10}},
    }
    service._set_agent_token_state(previous_state)
    spool = service.socket_path.parent / f"{tmp_path.name}-scan.atoms.sqlite3"
    spool.unlink(missing_ok=True)
    with sqlite3.connect(spool) as connection:
        connection.execute("CREATE TABLE atoms (bucket_start INTEGER NOT NULL, duration INTEGER NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(bucket_start, duration, event_key))")
        for index in range(3):
            normalized = statsd.normalized_usage_atom(_spool_test_atom(index, 1050.0))
            connection.execute(
                "INSERT INTO atoms VALUES(?, ?, ?, ?)",
                (1050, 1, statsd._agent_token_spool_identity(normalized), json.dumps(normalized)),
            )
    service.agent_token_scan_id = "scan-live"
    service.agent_token_scan_result = {
        "scan_id": "scan-live",
        "measurements": [{"key": "s|0|codex", "label": "s:0", "tokens": 20, "source": "transcript", "identity": "stable", "models": {"gpt-5.6": 20}}],
        "atom_spool": str(spool), "atom_count": 3, "atom_state": {"s|0|codex": 1060},
        "seen_keys": ["s|0|codex"], "sample_time": 1060, "previous_state": previous_state,
    }

    assert service._drain_agent_token_scan_result() is True
    assert service.agent_token_atom_persistence is not None
    while service.agent_token_scan_persistence is not None:
        service._drain_agent_token_scan_result()

    # The chart-producing rate and model split are durable before backfill is
    # complete; this is the regression that left both live GUI series blank.
    history = service.handle({"action": "history"})
    assert sum(record["tokens_per_agent_total"] for record in history["records"]) == pytest.approx(10)
    live_rates = [rate for record in history["records"] for rate in record["agent_token_rates"]]
    assert sum(rate["tokens"] for rate in live_rates) == pytest.approx(10)
    assert sum(rate["model_rates"]["gpt-5.6"]["tokens"] for rate in live_rates) == pytest.approx(10)
    assert service._agent_token_state()["s|0|codex"]["time"] == 1060
    assert service._agent_token_atom_state()["s|0|codex"] == 1000

    service.store.close()
    restarted = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    restarted.agent_token_atom_persistence = restarted._load_agent_token_atom_spool()
    assert restarted.agent_token_atom_persistence is not None
    while restarted.agent_token_atom_persistence is not None:
        restarted._drain_agent_token_scan_result()
    assert restarted._agent_token_atom_state()["s|0|codex"] == 1060
    assert len(restarted.store.bucket(1050, 1)["cost_summary"]["components"]) == 3
    restarted.store.close()


def test_statsd_early_counter_result_detaches_historical_worker_slot(tmp_path):
    class AliveWorker:
        def is_alive(self):
            return True

    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    worker = AliveWorker()
    result_path = service.socket_path.parent / f"{tmp_path.name}-worker.json"
    service.agent_token_scan_id = "scan-early"
    service.agent_token_scan_worker = worker
    service.agent_token_scan_result_path = result_path
    service.agent_token_scan_includes_atoms = True
    service.agent_token_scan_result = {
        "partial": True, "scan_id": "scan-early", "measurements": [], "seen_keys": [],
        "sample_time": 1060, "previous_state": {},
    }

    assert service._drain_agent_token_scan_result() is True
    assert service.agent_token_scan_worker is None
    assert service.agent_token_atom_worker is worker
    assert service.agent_token_atom_result_path == result_path
    assert service.agent_token_scan_persistence is not None
    service.agent_token_atom_worker = None
    service.agent_token_atom_result_path = None
    while service.agent_token_scan_persistence is not None:
        service._drain_agent_token_scan_result()
    service.store.close()


def test_statsd_agent_token_recovery_coalesces_rollups_without_inline_refresh(monkeypatch, tmp_path):
    """Historical token pages queue four tiers, then converge between requests."""
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    records = [{"time": 1000 + index, "cpu_total_percent": 1, "cpu_count": 1} for index in range(64)]
    inline_refreshes = []
    monkeypatch.setattr(
        service,
        "_refresh_persisted_rollups",
        lambda sample_time: inline_refreshes.append(sample_time) or pytest.fail("recovery must not rebuild rollups per record"),
    )
    service.agent_token_scan_id = "scan-rollup"
    service.agent_token_scan_result = {
        "scan_id": "scan-rollup", "measurements": [], "atom_records": records,
        "seen_keys": [], "sample_time": 1100, "previous_state": {},
    }

    # Install, then persist exactly one record (the intentionally tight
    # recovery page). The history handler is an ordinary request between turns.
    assert service._drain_agent_token_scan_result() is True
    assert service._drain_agent_token_scan_result() is True
    started = time.perf_counter()
    history, _binary = service.handle_with_binary({"action": "history", "start": 0, "end": 0})
    assert history["ok"] is True
    assert time.perf_counter() - started < 0.1
    assert inline_refreshes == []
    assert len(service.rollup_pending) == len(statsd.STATS_HISTORY_PERSISTED_ROLLUP_SECONDS)

    while service.agent_token_scan_persistence is not None:
        service._drain_agent_token_scan_result()
    while service.rollup_pending or service.rollup_jobs or service.rollup_backfill is not None:
        service._rollup_maintenance_step()

    rollups = service.store.query_rollups(duration=60, start=960, end=1080)
    assert sum(float(bucket["cpu_count"]) for bucket in rollups) == 64
    assert sum(float(bucket["cpu_total_percent"]) for bucket in rollups) == 64
    service.store.close()


def test_statsd_server_merge_can_defer_full_history_compaction(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    monkeypatch.setattr(service, "_compact_history", lambda *_args: pytest.fail("recovery page must not compact the full store"))

    result = service.merge_server_records([{"time": 1000, "cpu_total_percent": 1, "cpu_count": 1}], now=1000, compact=False)

    assert result["changed"] == 1
    service.store.close()


def test_statsd_history_profile_separates_sqlite_query_and_assembly(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.upsert_bucket(_bucket(sequence=1))

    response = service._encoded_history({"start": 1, "end": 10_000, "max_points": 100})
    profile = service.last_history_profile

    assert response["records"]
    assert profile["cache_hit"] is False
    assert profile["source_records"] == 1
    assert profile["returned_records"] == 1
    assert profile["coverage_ms"] >= 0
    assert profile["query_ms"] >= 0
    assert profile["assemble_ms"] >= profile["coverage_ms"] + profile["query_ms"]
    assert service.common_status()["history_profile"] == profile
    assert service.common_status()["history_requests"] == 1
    assert service.common_status()["history_cache_hits"] == 0
    service._encoded_history({"start": 1, "end": 10_000, "max_points": 100})
    assert service.common_status()["history_requests"] == 2
    assert service.common_status()["history_cache_hits"] == 1
    service.store.close()


def test_statsd_history_contract_distinguishes_empty_complete_and_partial_coverage(tmp_path):
    """Consumers must use coverage, not an empty record list, to judge history."""
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    try:
        empty = service.handle({"action": "history", "start": 100, "end": 200})
        assert empty["ok"] is True
        assert empty["records"] == []
        assert empty["coverage"]["complete"] is False
        assert empty["coverage"]["covered_start"] == 0
        assert empty["coverage"]["covered_end"] == 0

        service.store.upsert_bucket(_bucket(start=100, duration=1, sequence=1))
        complete_but_unchanged = service.handle({"action": "history", "since": 1, "start": 100, "end": 101})
        assert complete_but_unchanged["records"] == []
        assert complete_but_unchanged["coverage"]["complete"] is True
        assert complete_but_unchanged["coverage"]["returned_records"] == 0

        partial = service.handle({"action": "history", "start": 50, "end": 200})
        assert partial["coverage"]["complete"] is False
        assert (partial["coverage"]["covered_start"], partial["coverage"]["covered_end"]) == (100, 101)

        status, encoded = service.handle_with_binary({
            "action": "write_encoded_sample",
            "sample": {"time": 101.0, "pid": 8881},
            "shared_stats": {"enabled": True},
            "query": {"start": 100, "end": 101},
        })
        assert status == {"ok": True, "encoding": "json", "size": len(encoded)}
        assert json.loads(encoded)["history"]["coverage"]["complete"] is True
    finally:
        service.store.close()


def test_statsd_history_contract_enforces_real_schema_1h_and_24h_budgets(tmp_path):
    """Dense retained history stays exact, bounded, and responsive."""
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    start = 1_000_200  # aligned to every retained and persisted rollup tier
    end = start + (24 * 60 * 60)
    try:
        # The exact production tiers total 2,700 retained slots: 12h/10m,
        # 4h/5m, 4h/2m, 2h/1m, 90m/10s, and the newest 30m/1s.
        tiers = ((12 * 3600, 600), (4 * 3600, 300), (4 * 3600, 120),
                 (2 * 3600, 60), (90 * 60, 10), (30 * 60, 1))
        raw = []
        cursor = start
        sequence = 1
        for span, duration in tiers:
            for bucket_start in range(cursor, cursor + span, duration):
                raw.append(_real_schema_history_bucket(bucket_start, duration, sequence))
                sequence += 1
            cursor += span
        assert len(raw) == 2700 and cursor == end
        service.store.replace_buckets(raw)
        for duration in (60, 300):
            for bucket_start in range(start, end, duration):
                service.store.upsert_rollup(_real_schema_history_bucket(bucket_start, duration, sequence))
                sequence += 1

        for span, latency_budget in (
            (3600, 1.0),
            (24 * 3600, 2.0),
        ):
            service._encoded_query_cache.clear()
            requested_start = end - span
            started = time.perf_counter()
            status, encoded = service.handle_with_binary({
                "action": "write_encoded_history",
                "start": requested_start,
                "end": end,
                "max_points": 360,
                "client_id": "browser-a",
                "token_resolution_seconds": 60,
            })
            elapsed = time.perf_counter() - started
            payload = json.loads(encoded)
            token_records = payload["agent_token_history"]["records"]
            token_total = sum(float(record["tokens_per_agent_total"]) for record in token_records)
            agent_total = sum(
                float(rate["total"])
                for record in token_records
                for rate in record["agent_token_rates"]
            )
            component_quantity = sum(
                float(component["quantity"])
                for record in token_records
                for component in record["cost_summary"]["components"]
            )

            assert status == {"ok": True, "encoding": "json", "size": len(encoded)}
            assert len(payload["records"]) <= 360
            assert payload["coverage"]["complete"] is True
            assert payload["agent_token_history"]["coverage"]["complete"] is True
            assert token_total == agent_total == 80.0 * span
            assert component_quantity == 36.0 * span
            assert len(encoded) <= 4 * 1024 * 1024
            assert len(gzip.compress(encoded)) <= 768 * 1024
            assert elapsed <= latency_budget
    finally:
        service.store.close()


def test_statsd_compaction_preserves_the_full_large_legacy_history_not_just_query_cap(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    now = 1_100_000
    # Deliberately cross STATS_STORE_MAX_ROWS_PER_QUERY.  Compaction replaces
    # the store, so using the public query cap here used to erase every row
    # after the first 20k before a 24h JSONL/legacy rebuild could serve it.
    count = stats_store.STATS_STORE_MAX_ROWS_PER_QUERY + 5
    buckets = []
    for start in range(now - count, now):
        bucket = stats_store.empty_bucket(start, 1)
        bucket.update({"sequence": start, "server_sequence": start, "cpu_total_percent": 1.0, "cpu_count": 1.0})
        buckets.append(bucket)
    service.store.replace_buckets(buckets)

    service._compact_history(now)
    history = service.handle({"action": "history", "start": now - count, "end": now, "max_points": 1000})

    assert sum(record["cpu_count"] for record in history["records"]) == count
    assert history["coverage"]["covered_start"] == now - count
    assert history["coverage"]["covered_end"] == now
    assert history["coverage"]["source_records"] < count
    service.store.close()


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


def test_statsd_persists_and_serves_coarse_rollups_for_bounded_range(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    now = 10_000
    service.merge_server_records(
        [{"time": now + second, "cpu_total_percent": 1, "cpu_count": 1} for second in range(10)],
        now=now + 10,
        compact=False,
    )

    rollup = service.store.rollup_bucket(now, 10)
    history = service.handle({"action": "history", "start": now, "end": now + 10, "resolution_seconds": 10})

    assert rollup is not None
    assert rollup["cpu_total_percent"] == 10
    assert history["coverage"]["resolution_seconds"] == 10
    assert len(history["records"]) == 1
    assert history["records"][0]["cpu_total_percent"] == 10
    service.store.close()


def test_statsd_twenty_four_hour_range_uses_bounded_persisted_rollups(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    start = 86_400
    total_cpu = 0.0
    for index in range(144):
        bucket = stats_store.empty_bucket(start + index * 600, 600)
        bucket.update({"sequence": index + 1, "server_sequence": index + 1, "cpu_total_percent": 10.0, "cpu_count": 1.0})
        service.store.upsert_rollup(bucket)
        total_cpu += 10.0
    # Raw coverage remains authoritative, while the range itself reads the
    # persisted 600-second tier instead of materializing raw samples.
    service.store.upsert_bucket(stats_store.empty_bucket(start, 1))
    service.store.upsert_bucket(stats_store.empty_bucket(start + 24 * 60 * 60 - 1, 1))

    history = service.handle({"action": "history", "start": start, "end": start + 24 * 60 * 60, "max_points": 200})

    assert history["coverage"]["resolution_seconds"] == 600
    assert len(history["records"]) == 144
    assert sum(record["cpu_total_percent"] for record in history["records"]) == total_cpu
    assert history["coverage"]["source_records"] == 2
    service.store.close()


def test_statsd_rollup_backfill_uses_bounded_coalesced_work(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.upsert_bucket(_bucket(start=100, duration=1, sequence=1))
    service.store.upsert_bucket(_bucket(start=101, duration=1, sequence=2))
    service.rollup_backfill = {"after_start": -1, "after_duration": -1, "processed": 0}

    steps = []
    while service.rollup_backfill is not None or service.rollup_pending or service.rollup_jobs:
        steps.append(service._rollup_backfill_step())

    assert steps
    assert all(step["processed"] <= statsd.STATSD_ROLLUP_MAINTENANCE_PAGE_ROWS for step in steps)
    assert service.store.rollup_bucket(100, 10)["cpu_total_percent"] == 25.0
    service.store.close()


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


def test_statsd_history_cursor_survives_replacement_without_replaying_acknowledged_rows(tmp_path):
    socket_path = tmp_path / "statsd.sock"
    database_path = tmp_path / "stats.sqlite3"
    first_owner = statsd.PersistentStatsService(socket_path, database_path)
    first_owner.handle({
        "action": "merge_records",
        "client_id": "browser-a",
        "now": 1000,
        "records": [{"start": 999, "api_count": 2}],
    })
    acknowledged = first_owner.handle({"action": "history", "client_id": "browser-a", "after_sequence": 0})
    cursor = acknowledged["coverage"]["cursor"]
    first_owner.store.close()

    replacement_owner = statsd.PersistentStatsService(socket_path, database_path)
    replay = replacement_owner.handle({"action": "history", "client_id": "browser-a", "after_sequence": cursor})
    full = replacement_owner.handle({"action": "history", "client_id": "browser-a", "after_sequence": 0})

    assert acknowledged["records"] and cursor == 1
    assert replay["records"] == []
    assert replay["coverage"]["cursor"] == cursor
    assert sum(record["api_count"] for record in full["records"]) == 2
    assert full["coverage"]["cursor"] == cursor
    replacement_owner.store.close()


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


def test_statsd_browser_retention_compacts_cooperatively_without_losing_totals(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    now = 200000.0
    records = [
        {"start": now - age + index, "api_count": 1}
        for age in (90 * 60, 3 * 60 * 60, 6 * 60 * 60, 10 * 60 * 60, 18 * 60 * 60)
        for index in range(20)
    ]

    merged = service.merge_records(records, client_id="browser-a", now=now, compact=False)
    steps = 0
    while service.retention_compaction is not None:
        result = service._retention_maintenance_step()
        assert result["processed"] <= 1
        steps += 1
        assert steps < 500
    history = service.handle({"action": "history", "client_id": "browser-a"})

    assert merged["changed"] == 100
    assert len(history["records"]) <= 10
    assert sum(record["api_count"] for record in history["records"]) == 100
    assert {record["duration"] for record in history["records"]} == {600}
    service.store.close()


def test_statsd_cooperative_retention_expires_raw_and_rollup_rows(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    now = 200000.0
    expired = statsd.stats_store.empty_bucket(int(now - statsd.STATS_HISTORY_RETENTION_SECONDS - 100), 1)
    expired["sequence"] = 1
    retained = statsd.stats_store.empty_bucket(int(now - 100), 1)
    retained["sequence"] = 2
    service.store.upsert_bucket(expired)
    service.store.upsert_bucket(retained)
    service.store.upsert_rollup(expired)
    service._enqueue_retention_compaction(now)

    while service.retention_compaction is not None:
        service._retention_maintenance_step()

    assert service.store.bucket(expired["start"], 1) is None
    assert service.store.rollup_bucket(expired["start"], 1) is None
    assert service.store.bucket(retained["start"], 1) is not None
    service.store.close()


def test_statsd_retention_converges_while_live_writes_continue(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    now = 200000.0
    historical = [
        {"start": now - 6 * 60 * 60 + index, "_statsd_duration": 1, "api_count": 1}
        for index in range(40)
    ]
    service.merge_records(historical, client_id="browser-a", now=now, compact=False)

    # A one-second writer keeps advancing the requested maintenance epoch
    # while the active pass consumes old fine-grained rows.
    for index in range(60):
        sample_now = now + index
        service.merge_server_records(
            [{"time": sample_now, "cpu_total_percent": 1, "cpu_count": 1}],
            now=sample_now, compact=False, refresh_rollups=False,
        )
        result = service._retention_maintenance_step()
        assert result["processed"] <= 1

    steps = 0
    while service.retention_compaction is not None:
        service._retention_maintenance_step()
        steps += 1
        assert steps < 500

    buckets = service.store.all_buckets()
    assert sum(
        float(client.get("api_count") or 0)
        for bucket in buckets
        for client in (bucket.get("clients") or {}).values()
    ) == 40
    assert sum(float(bucket.get("cpu_count") or 0) for bucket in buckets) == 60
    assert not any(
        int(bucket["duration"]) < service._bucket_seconds(float(bucket["start"]), now + 59)
        for bucket in buckets
    )
    service.store.close()


def test_statsd_cooperative_retention_preserves_legacy_component_normalization(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    now = 200000.0
    bucket = statsd.stats_store.empty_bucket(int(now - 10), 1)
    bucket["sequence"] = 1
    bucket["cost_summary"] = {"components": [
        {"event_id": "one", "source": "rollout", "transcript": "/tmp/same.jsonl", "unit": "tokens"},
        {"event_id": "two", "source": "rollout", "transcript": "/tmp/same.jsonl", "unit": "tokens"},
    ]}
    service.store.upsert_bucket(bucket)
    service._enqueue_retention_compaction(now)

    while service.retention_compaction is not None:
        service._retention_maintenance_step()

    normalized = service.store.bucket(bucket["start"], bucket["duration"])
    assert normalized["cost_summary"]["components"][0]["transcript"] == "/tmp/same.jsonl"
    assert normalized["cost_summary"]["components"][1]["transcript"] == ""
    service.store.close()


def test_statsd_cpu_write_stays_responsive_during_browser_retention_maintenance(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    original_replace = service.store.replace_compacted_bucket
    maintenance_started = threading.Event()

    def slow_replace(*args, **kwargs):
        maintenance_started.set()
        time.sleep(0.2)
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(service.store, "replace_compacted_bucket", slow_replace)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = statsd.StatsClient(service.socket_path, service.store.path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.healthy() is True
    now = time.time()
    browser = client.merge_records(
        [{"start": now - 3 * 60 * 60 + index, "_statsd_duration": 1, "api_count": 1} for index in range(16)],
        client_id="browser-a", now=now,
    )
    assert browser["ok"] is True
    assert maintenance_started.wait(2.0) is True

    started = time.monotonic()
    response = client.merge_server_records(
        [{"time": time.time(), "cpu_total_percent": 1, "cpu_count": 1}],
        compact=False, refresh_rollups=False, timeout=0.9,
    )
    elapsed = time.monotonic() - started

    client.request({"action": "shutdown"})
    worker.join(timeout=2.0)
    assert response["ok"] is True
    assert elapsed < 0.9


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
            "cost_summary": statsd.cost_summary_response(stats_store.empty_bucket(0, 1)["cost_summary"]),
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
