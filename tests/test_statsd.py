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


def test_statsd_sampler_survives_store_maintenance_owned_by_rpc_thread(monkeypatch, tmp_path):
    """The daemon sampler must keep ticking after pricing maintenance is requested.

    StatsStore opens its SQLite connection on the RPC thread.  This regression
    deliberately opens it before starting the sampler thread, matching the
    daemon startup order, so direct sampler-thread store access fails loudly.
    """
    service = statsd.PersistentStatsService(
        tmp_path / "statsd.sock",
        tmp_path / "stats.sqlite3",
        pricing_catalog=_MutablePricingCatalog(),
    )
    service.store.open()
    calls = []
    monkeypatch.setattr(service, "_sampler_owner_for_cycle", lambda: {"control_socket": "owner.sock"})
    monkeypatch.setattr(
        statsd,
        "send_yolomux_control_request",
        lambda *_args, **_kwargs: calls.append(time.time()) or {"ok": True},
    )

    class BoundedWake:
        def wait(self, _seconds):
            if len(calls) >= 2:
                service.stop_event.set()

        def clear(self):
            pass

        def set(self):
            pass

    service.sampler_wake_event = BoundedWake()
    thread = threading.Thread(target=service._sampler_loop)
    thread.start()
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert len(calls) == 2
    assert service.last_sampler_success_at > 0
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
    assert events == [
        "listener-owned",
        "import",
        "reproject-scheduled",
        ("thread", "_sampler_loop", "statsd-sampler", True),
        "sampler-start",
    ]
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
    assert service._maybe_reproject_cost_summaries()["reason"] == "current_catalog"
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


def test_statsd_clears_cached_sampler_when_durable_owner_is_released(tmp_path):
    owner_path = tmp_path / "background-owner" / "owner.json"
    owner_path.parent.mkdir()
    owner_path.write_text(json.dumps({"status": "follower", "pid": os.getpid()}), encoding="utf-8")
    service = statsd.PersistentStatsService(
        tmp_path / "statsd.sock",
        tmp_path / "stats.sqlite3",
        idle_seconds=1.0,
        sampler_owner_path=owner_path,
    )
    service.sampler_owner = {"generation_id": "stale-owner", "pid": os.getpid()}
    service.last_client_at = time.monotonic() - 10

    assert service._sampler_owner_for_cycle() == {}
    assert service._idle_shutdown_ready() is True
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


def test_statsd_history_listener_stays_responsive_while_sampler_owner_is_slow(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    service.store.upsert_bucket(_bucket(sequence=1))
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(service, "_sampler_owner_for_cycle", lambda: {"control_socket": "slow-owner.sock"})

    def slow_owner(*_args, **_kwargs):
        entered.set()
        release.wait(1.0)
        return {"ok": False, "error": "injected slow sampler"}

    monkeypatch.setattr(statsd, "send_yolomux_control_request", slow_owner)
    sampler = threading.Thread(target=service._sampler_loop)
    sampler.start()
    assert entered.wait(1.0) is True

    started = time.perf_counter()
    response, _binary = service.handle_with_binary({"action": "history", "start": 0, "end": 0})
    elapsed = time.perf_counter() - started

    release.set()
    service.stop_event.set()
    service.sampler_wake_event.set()
    sampler.join(timeout=1.0)
    assert response["ok"] is True
    assert response["records"]
    assert elapsed < 0.1
    assert sampler.is_alive() is False
    service.store.close()


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


def test_statsd_agent_token_scan_batches_recovery_atoms_without_advancing_state_early(monkeypatch, tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    batches = []
    compactions = []

    def merge(records, *, now, compact):
        batches.append((len(records), now, compact))
        return {"ok": True, "changed": len(records), "sequence": len(batches)}

    monkeypatch.setattr(service, "merge_server_records", merge)
    monkeypatch.setattr(service, "_compact_history", lambda now: compactions.append(now))
    atoms = [{"time": 1060, "usage_atoms": []} for _ in range(statsd.STATS_HISTORY_POST_MAX_RECORDS + 1)]

    result = service._persist_agent_token_scan([], atoms, set(), 1060, {})

    assert batches == [(statsd.STATS_HISTORY_POST_MAX_RECORDS, 1060, False), (1, 1060, False)]
    assert compactions == [1060]
    assert result["persisted_records"] == statsd.STATS_HISTORY_POST_MAX_RECORDS + 1
    assert result["merge"] == {"ok": True, "changed": statsd.STATS_HISTORY_POST_MAX_RECORDS + 1, "sequence": 2, "batches": 2}
    assert service._agent_token_state() == {}
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


def test_statsd_sampler_failure_delay_backs_off(tmp_path):
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    assert service._sampler_delay_seconds() == statsd.STATSD_SAMPLE_INTERVAL_SECONDS
    service.sampler_failure_count = 1
    assert service._sampler_delay_seconds() == statsd.STATSD_SAMPLE_FAILURE_INITIAL_BACKOFF_SECONDS
    service.sampler_failure_count = 2
    assert service._sampler_delay_seconds() == statsd.STATSD_SAMPLE_FAILURE_INITIAL_BACKOFF_SECONDS * 2
    service.sampler_failure_count = 99
    assert service._sampler_delay_seconds() == statsd.STATSD_SAMPLE_FAILURE_MAX_BACKOFF_SECONDS
    service.store.close()


def test_statsd_sampler_deadline_math_does_not_add_successful_rpc_duration(tmp_path):
    """A 200ms owner RPC still leaves the following fixed 1s deadline at 1.0."""

    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")

    assert service._next_sampler_deadline(0.0, 0.2, cycle_failed=False) == 1.0
    assert service._next_sampler_deadline(1.0, 1.2, cycle_failed=False) == 2.0
    assert service.sampler_missed_cycles == 0
    assert service.sampler_late_cycles == 0
    service.store.close()


def test_statsd_sampler_deadline_math_skips_overdue_cycles_and_retains_failure_backoff(tmp_path):
    """A slow cycle is observable degradation, never a catch-up burst."""

    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    assert service._next_sampler_deadline(0.0, 2.2, cycle_failed=False) == 3.0
    assert service.sampler_late_cycles == 1
    assert service.sampler_missed_cycles == 2
    service.sampler_failure_count = 2
    assert service._next_sampler_deadline(3.0, 3.2, cycle_failed=True) == 13.2
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
