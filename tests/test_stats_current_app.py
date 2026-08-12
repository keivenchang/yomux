# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""App adapters from native YOLOmux state to current stats facts."""

import ast
import inspect
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module, http_routes, local_service_projection, session_files, settings as settings_module
from yolomux_lib.stats_current import materializer as materializer_module
from yolomux_lib.stats_current import collectors as collectors_module
from yolomux_lib.stats_current import pricing as pricing_module
from yolomux_lib.stats_current import runtime as runtime_module
from yolomux_lib.stats_current import storage as storage_module
from yolomux_lib.stats_current import usage as usage_module
from yolomux_lib.stats_current.transcripts import ScannedUsageAtom, StatsCurrentTranscriptUsageScanner, TranscriptUsageScanResult


def attempt(family, cadence):
    return SimpleNamespace(
        family=family,
        epoch_id=f"1:{family}:1",
        epoch_started_at=100,
        scheduled_at=110,
        cadence_seconds=cadence,
        owner_generation=1,
    )


def current_runtime(client, collector_overrides=None):
    collectors = {
        family: (lambda _attempt: collectors_module.CollectorFacts())
        for family in runtime_module.WEB_COLLECTED_FAMILIES
    }
    collectors.update(collector_overrides or {})
    return runtime_module.StatsCurrentRuntime(
        client,
        collectors,
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )


# `test_cpu_adapter_forces_a_native_sample_at_the_scheduler_deadline` used to sit here. It was one
# of only two references anywhere to `TmuxWebtermApp.collect_current_stats_cpu`, which the collector
# registry in `TmuxWebtermApp.__init__` never registered -- the adapter it asserted had no
# production call site. The CPU family's real producer is statsd's
# `StatsCurrentService._collect_host_facts_if_due`, and its contract is owned by
# `tests/test_stats_current_service.py`.


def test_agent_status_adapter_carries_the_authoritative_statusd_snapshot_revision():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_collection_state = SimpleNamespace(agent_activity_lock=threading.RLock(), agent_activity_state={})
    webapp.status_snapshot_payload = lambda: {
        "agent_window_snapshot_revision": 17,
        "sessions": {"1": {"agent_windows": [{"window_index": 0, "pane_target": "%1", "kind": "codex", "state": "working"}]}},
    }
    webapp.notification_transition_seconds = lambda: 30.0
    webapp.stats_agent_activity_kind_locked = lambda _row, _key, _at, _seconds: "run"
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)

    facts = webapp.collect_current_stats_agent_status(attempt("agent_status", 10))

    assert facts.observations[0].payload == {
        "states": {"1|0|%1|codex": "run"},
        "session_states": {"1": "run"},
        "snapshot_revision": 17,
    }


def test_agent_status_adapter_rolls_deduplicated_windows_into_one_session_state():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_collection_state = SimpleNamespace(agent_activity_lock=threading.RLock(), agent_activity_state={})
    webapp.status_snapshot_payload = lambda: {
        "sessions": {
            "1": {"agent_windows": [
                {"window_index": 0, "pane_target": "%1", "kind": "codex", "state": "idle"},
                {"window_index": 1, "pane_target": "%2", "kind": "claude", "state": "working"},
                {"window_index": 1, "pane_target": "%2", "kind": "claude", "state": "working"},
            ]},
            "2": {"agent_windows": [{"window_index": 0, "pane_target": "%3", "kind": "codex", "state": "approval"}]},
        },
    }
    webapp.notification_transition_seconds = lambda: 30.0
    webapp.stats_agent_activity_kind_locked = lambda row, _key, _at, _seconds: {"approval": "ask", "working": "run", "idle": "idle"}[row["state"]]
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)

    facts = webapp.collect_current_stats_agent_status(attempt("agent_status", 10))

    assert facts.observations[0].payload["states"] == {"1|0|%1|codex": "idle", "1|1|%2|claude": "run", "2|0|%3|codex": "ask"}
    assert facts.observations[0].payload["session_states"] == {"1": "run", "2": "ask"}


def test_agent_status_adapter_counts_six_physical_windows_and_drops_a_removed_pane():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_collection_state = SimpleNamespace(agent_activity_lock=threading.RLock(), agent_activity_state={})
    rows = [
        {"window_index": index, "pane_target": f"%{index}", "kind": "codex", "state": "working"}
        for index in range(6)
    ]
    payload = {"sessions": {"1": {"agent_windows": rows + [dict(rows[0])]}}}
    webapp.status_snapshot_payload = lambda: payload
    webapp.notification_transition_seconds = lambda: 30.0
    webapp.stats_agent_activity_kind_locked = lambda _row, _key, _at, _seconds: "run"
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)

    first = webapp.collect_current_stats_agent_status(attempt("agent_status", 10)).observations[0].payload
    payload["sessions"]["1"]["agent_windows"] = rows[:-1]
    second = webapp.collect_current_stats_agent_status(attempt("agent_status", 10)).observations[0].payload

    assert len(first["states"]) == 6
    assert first["session_states"] == {"1": "run"}
    assert len(second["states"]) == 5
    assert "1|5|%5|codex" not in second["states"]
    assert "1|5|%5|codex" not in webapp.stats_collection_state.agent_activity_state


def test_agent_status_adapter_expires_a_finished_window_transition_at_the_configured_glow_deadline():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_collection_state = SimpleNamespace(agent_activity_lock=threading.RLock(), agent_activity_state={})
    payload = {"sessions": {"1": {"agent_windows": [
        {"window_index": 0, "pane_target": "%0", "kind": "codex", "state": "working"},
    ]}}}
    webapp.status_snapshot_payload = lambda: payload
    webapp.notification_transition_seconds = lambda: 30.0
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)

    def status_attempt(scheduled_at):
        value = attempt("agent_status", 10)
        value.scheduled_at = scheduled_at
        return value

    running = webapp.collect_current_stats_agent_status(status_attempt(100)).observations[0].payload
    payload["sessions"]["1"]["agent_windows"][0]["state"] = "idle"
    during_glow = webapp.collect_current_stats_agent_status(status_attempt(101)).observations[0].payload
    after_glow = webapp.collect_current_stats_agent_status(status_attempt(131)).observations[0].payload

    assert running["states"] == {"1|0|%0|codex": "run"}
    assert during_glow["states"] == {"1|0|%0|codex": "transition"}
    assert after_glow["states"] == {"1|0|%0|codex": "idle"}


def test_service_load_adapter_excludes_the_web_process_owned_by_cpu():
    """The CPU family owns the web PID, so this family may never carry a "web" row.

    M3 of DOIT.p0.daemon-monitor made that structural instead of a filter. This sampler and
    /api/system-status now share one owner, `local_services_snapshot()`, and that collector
    rejects any producer outside the six-service inventory -- so a "web" row cannot reach the
    sampler rather than being dropped by it once it has. The stub therefore moved from the
    rendered HTTP payload to the collector, and the second assertion proves the rejection.
    """
    webapp = object.__new__(app_module.TmuxWebtermApp)
    collector = local_service_projection.LocalServicesCollector(
        lambda: {
            "indexd": lambda: {"service": "indexd", "pid": 0, "resources": {}},
            "statsd": lambda: {"service": "statsd", "pid": 21, "resources": {"cpu_percent": 4, "rss_bytes": 400}},
            "jobd": lambda: {"service": "jobd", "pid": 0, "resources": {}},
            "statusd": lambda: {"service": "statusd", "pid": 0, "resources": {}},
            "watchd": lambda: {"service": "watchd", "pid": 0, "resources": {}},
            "approvald": lambda: {"service": "approvald", "pid": 0, "resources": {}},
        }
    )
    webapp.local_services_snapshot = collector.collect

    facts = webapp.collect_current_stats_service_load(attempt("service_load", 10))
    statsd = next(observation for observation in facts.observations if observation.source_id == "statsd")

    assert [observation.source_id for observation in facts.observations] == list(
        local_service_projection.LOCAL_SERVICE_INVENTORY
    )
    assert "web" not in [observation.source_id for observation in facts.observations]
    assert statsd.payload == {"running": True, "cpu_percent": 4.0, "rss_bytes": 400.0}
    with pytest.raises(ValueError, match=r"unexpected=\['web'\]"):
        local_service_projection.LocalServicesCollector(
            lambda: {
                **{name: (lambda: {"pid": 0, "resources": {}}) for name in local_service_projection.LOCAL_SERVICE_INVENTORY},
                "web": lambda: {"service": "web", "pid": 22, "resources": {"cpu_percent": 9, "rss_bytes": 900}},
            }
        ).collect()


def test_token_adapter_uses_incremental_structured_atoms_and_keeps_dimensions(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        '{"type":"session_meta","timestamp":101,"payload":{"id":"thread-1"}}\n'
        '{"type":"turn_context","timestamp":102,"payload":{"model":"gpt-5.6","effort":"high"}}\n'
        '{"type":"event_msg","timestamp":105,"payload":{"info":{"total_token_usage":{"output_tokens":42}}}}\n',
        encoding="utf-8",
    )
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["yo8881"]
    webapp.stats_agent_window_rows = lambda: [{"session": "yo8881"}]
    webapp.stats_agent_token_rows = lambda _rows: [{
        "key": "yo8881|0|codex",
        "transcript": str(transcript),
        "kind": "codex",
    }]
    webapp.settings_payload = lambda: {"settings": {"cost": {"openai_pricing_profile": "default"}}}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()

    facts = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))

    assert len(facts.usage_atoms) == 1
    assert facts.usage_atoms[0].payload["quantity"] == 42
    assert facts.usage_atoms[0].payload["model"] == "gpt-5.6"
    assert facts.usage_atoms[0].payload["agent_id"] == "yo8881|0|codex"
    assert facts.usage_atoms[0].payload["pricing_profile"] == "default"
    assert facts.coverage_epochs[0].native_cadence_seconds == 10
    assert facts.receipt is not None
    facts.receipt.commit()

    unchanged = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
    assert unchanged.usage_atoms == ()

    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"type":"event_msg","timestamp":106,"payload":{"info":{"total_token_usage":{"output_tokens":50}}}}\n'
        )
    appended = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
    assert len(appended.usage_atoms) == 1
    assert appended.usage_atoms[0].payload["quantity"] == 8


def test_token_adapter_publishes_emitted_rejected_reason_counters(monkeypatch):
    invalid = session_files.TranscriptUsageAtom(
        source="fixture", timestamp=105, event_id="invalid-direction", provider="openai",
        model="gpt-5.6", model_evidence="fixture", effort="", direction="invalid",
        modality="text", cache_role="none", unit="tokens", quantity=42,
        root_thread_id="fixture", agent_thread_id="fixture", parent_thread_id="", depth=0,
        endpoint="responses", tool_name="", call_id="", pricing_profile="default",
        service_tier="default", telemetry_complete=True,
    )
    result = TranscriptUsageScanResult(
        (ScannedUsageAtom(invalid, "yo8881|0|codex", "codex"),), (),
        1, 1, 128, 1, 0, 1, False, 1,
    )

    class Scanner:
        def scan(self, _rows):
            return result

        def commit(self, _receipt_id):
            return None

        def rollback(self, _receipt_id):
            return None

        usage_atom_backfill_status_for_scan = staticmethod(
            StatsCurrentTranscriptUsageScanner.usage_atom_backfill_status_for_scan
        )

    captured = []
    client = app_module.StatsCurrentClient()
    monkeypatch.setattr(client, "set_usage_atom_backfill_status", lambda status: captured.append(status))
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = []
    webapp.stats_agent_window_rows = lambda: []
    webapp.stats_agent_token_rows = lambda _rows: []
    webapp.settings_payload = lambda: {"settings": {}}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = Scanner()
    webapp.stats_current_client = client

    facts = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))

    assert facts.usage_atoms == ()
    assert captured[0]["scan"] == {
        "files_read": 1,
        "records_parsed": 1,
        "atoms_emitted": 1,
        "atoms_accepted": 0,
        "atoms_rejected": 1,
        "rejection_reasons": {"direction must be one of: input, output": 1},
    }


def test_token_adapter_requests_follow_up_only_for_exhausted_scan_budget():
    results = [
        SimpleNamespace(
            items=(), tombstones=(), receipt_id=1, budget_exhausted=True,
        ),
        SimpleNamespace(
            items=(), tombstones=(), receipt_id=2, budget_exhausted=False,
        ),
    ]

    class Scanner:
        def scan(self, _rows):
            return results.pop(0)

        def commit(self, _receipt_id):
            return None

        def rollback(self, _receipt_id):
            return None

    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = []
    webapp.stats_agent_window_rows = lambda: []
    webapp.stats_agent_token_rows = lambda _rows: []
    webapp.settings_payload = lambda: {"settings": {}}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = Scanner()

    exhausted = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
    normal = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))

    assert exhausted.budget_exhausted_follow_up is True
    assert normal.budget_exhausted_follow_up is False


def test_token_adapter_stamps_subscription_profile_on_new_cli_atoms(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        '{"type":"turn_context","timestamp":102,"payload":{"model":"gpt-5.6","effort":"high"}}\n'
        '{"type":"event_msg","timestamp":105,"payload":{"info":{"total_token_usage":{"output_tokens":42}}}}\n',
        encoding="utf-8",
    )
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["yo8881"]
    webapp.stats_agent_window_rows = lambda: [{"session": "yo8881"}]
    webapp.stats_agent_token_rows = lambda _rows: [{
        "key": "yo8881|0|codex",
        "transcript": str(transcript),
        "kind": "codex",
    }]
    webapp.settings_payload = lambda: {"settings": {"cost": {
        "openai_pricing_profile": "subscription",
        "_openai_pricing_profile_history": ["0|default", "100|subscription"],
    }}}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()

    facts = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))

    assert {atom.payload["pricing_profile"] for atom in facts.usage_atoms} == {"subscription"}
    assert sum(atom.payload["quantity"] for atom in facts.usage_atoms) == 42


def test_token_adapter_tombstones_replayed_fork_history_before_first_turn(tmp_path):
    transcript = tmp_path / "rollout-fork.jsonl"
    transcript.write_text(
        '{"type":"session_meta","timestamp":101,"payload":{"id":"child-thread","forked_from_id":"parent-thread","thread_source":"subagent"}}\n'
        '{"type":"event_msg","timestamp":102,"payload":{"type":"thread_settings_applied","thread_settings":{"model":"gpt-5.6","reasoning_effort":"high"}}}\n'
        '{"type":"event_msg","timestamp":103,"payload":{"type":"token_count","info":{"total_token_usage":{"output_tokens":42}}}}\n'
        '{"type":"turn_context","timestamp":104,"payload":{"model":"gpt-5.6","effort":"high"}}\n'
        '{"type":"inter_agent_communication_metadata","timestamp":104.5,"payload":{}}\n'
        '{"type":"event_msg","timestamp":105,"payload":{"type":"token_count","info":{"total_token_usage":{"output_tokens":50}}}}\n',
        encoding="utf-8",
    )
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["yo8881"]
    webapp.stats_agent_window_rows = lambda: [{"session": "yo8881"}]
    webapp.stats_agent_token_rows = lambda _rows: [{
        "key": "yo8881|0|codex",
        "transcript": str(transcript),
        "kind": "codex",
    }]
    webapp.settings_payload = lambda: {"settings": {"cost": {
        "openai_pricing_profile": "default",
    }}}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()

    facts = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))

    assert [(atom.observed_at, atom.payload["quantity"]) for atom in facts.usage_atoms] == [
        (105.0, 8.0),
    ]
    assert [(item.event_id, item.observed_at, item.quantity, item.thread_id) for item in facts.usage_tombstones] == [
        ("codex:child-thread:2", 103.0, 42.0, "child-thread"),
    ]
    assert facts.receipt is not None
    facts.receipt.commit()
    unchanged = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
    assert unchanged.usage_atoms == ()
    assert unchanged.usage_tombstones == ()


def test_partial_tombstone_append_failure_replays_whole_receipt_then_commits(tmp_path):
    transcript = tmp_path / "rollout-large-fork.jsonl"
    records = [
        {"type": "session_meta", "timestamp": 1, "payload": {
            "id": "child-thread", "forked_from_id": "parent-thread",
            "thread_source": "subagent",
        }},
        {"type": "event_msg", "timestamp": 2, "payload": {
            "type": "thread_settings_applied",
            "thread_settings": {"model": "gpt-5.6", "reasoning_effort": "high"},
        }},
        *(
            {"type": "event_msg", "timestamp": index + 3, "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {
                    "input_tokens": index * 10,
                    "cached_input_tokens": index * 5,
                    "output_tokens": index,
                }},
            }}
            for index in range(1, 401)
        ),
        {"type": "turn_context", "timestamp": 500, "payload": {
            "model": "gpt-5.6", "effort": "high",
        }},
    ]
    transcript.write_text(
        "".join(f"{json.dumps(record, separators=(',', ':'))}\n" for record in records),
        encoding="utf-8",
    )
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["yo8881"]
    webapp.stats_agent_window_rows = lambda: [{"session": "yo8881"}]
    webapp.stats_agent_token_rows = lambda _rows: [{
        "key": "yo8881|0|codex", "transcript": str(transcript), "kind": "codex",
    }]
    webapp.settings_payload = lambda: {"settings": {"cost": {
        "openai_pricing_profile": "default",
    }}}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()

    first = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
    first_ids = tuple(item.event_id for item in first.usage_tombstones)
    assert len(first_ids) > 1_000
    durable = session_files.stats_current_transcript_scan_record(transcript, "codex")
    cache_path = session_files.transcript_scan_store_path(durable.identity)

    class ChunkClient:
        def __init__(self, fail_at=0):
            self.fail_at = fail_at
            self.calls = 0

        def append(self, **_groups):
            self.calls += 1
            return {"ok": self.calls != self.fail_at, "reason": "injected"}

    failing_runtime = current_runtime(ChunkClient(fail_at=2))
    with pytest.raises(runtime_module.CurrentRuntimeError, match="injected"):
        failing_runtime._append_facts("agent_tokens", first)
    assert failing_runtime.client.calls == 2
    assert not cache_path.exists()

    replayed = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
    assert tuple(item.event_id for item in replayed.usage_tombstones) == first_ids
    successful_runtime = current_runtime(ChunkClient())
    successful_runtime._append_facts("agent_tokens", replayed)
    assert successful_runtime.client.calls > 1
    assert cache_path.exists()

    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()
    third = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
    assert third.usage_atoms == ()
    assert third.usage_tombstones == ()


def test_accepted_tombstone_with_lost_ack_replays_as_duplicate_then_commits(tmp_path):
    transcript = tmp_path / "rollout-lost-ack.jsonl"
    transcript.write_text(
        '{"type":"session_meta","timestamp":1,"payload":{"id":"child-thread","forked_from_id":"parent-thread","thread_source":"subagent"}}\n'
        '{"type":"event_msg","timestamp":2,"payload":{"type":"thread_settings_applied","thread_settings":{"model":"gpt-5.6"}}}\n'
        '{"type":"event_msg","timestamp":3,"payload":{"type":"token_count","info":{"total_token_usage":{"output_tokens":42}}}}\n'
        '{"type":"turn_context","timestamp":4,"payload":{"model":"gpt-5.6"}}\n',
        encoding="utf-8",
    )
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["yo8881"]
    webapp.stats_agent_window_rows = lambda: [{"session": "yo8881"}]
    webapp.stats_agent_token_rows = lambda _rows: [{
        "key": "yo8881|0|codex", "transcript": str(transcript), "kind": "codex",
    }]
    webapp.settings_payload = lambda: {"settings": {"cost": {
        "openai_pricing_profile": "default",
    }}}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()

    first = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
    assert len(first.usage_tombstones) == 1
    tombstone = first.usage_tombstones[0]
    legacy = storage_module.UsageAtom(
        tombstone.event_id, tombstone.direction, tombstone.modality,
        tombstone.cache_role, tombstone.unit, tombstone.observed_at, {
            "quantity": tombstone.quantity,
            "provider": "openai",
                "model": tombstone.model,
            "agent_id": "yo8881|0|codex",
            "thread_id": tombstone.thread_id,
            "execution_source": "codex",
            "pricing_profile": "default",
            "telemetry_complete": True,
        },
    )
    store = storage_module.Store.open(tmp_path / storage_module.DATABASE_FILENAME)
    store.append_usage_atom(legacy)
    durable = session_files.stats_current_transcript_scan_record(transcript, "codex")
    cache_path = session_files.transcript_scan_store_path(durable.identity)

    class LostAckClient:
        def __init__(self):
            self.lose_ack = True

        def append(self, **groups):
            result = store.append_batch(**groups)
            if self.lose_ack:
                self.lose_ack = False
                raise OSError("response lost after durable commit")
            return {"ok": True, "counts": vars(result)}

    client = LostAckClient()
    current = current_runtime(client)
    try:
        with pytest.raises(OSError, match="response lost"):
            current._append_facts("agent_tokens", first)
        assert store.read_snapshot().usage_atoms == ()
        assert not cache_path.exists()

        replayed = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
        assert replayed.usage_tombstones == first.usage_tombstones
        current._append_facts("agent_tokens", replayed)
        assert cache_path.exists()

        with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
            session_files._TRANSCRIPT_SCAN_CACHE.clear()
        webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()
        third = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))
        assert third.usage_atoms == ()
        assert third.usage_tombstones == ()
    finally:
        store.close()


def test_old_eof_fork_repair_removes_huge_materialized_usage_and_cold_resumes(
    tmp_path,
    monkeypatch,
):
    transcript = tmp_path / "rollout-old-eof-fork.jsonl"
    records = [
        {"type": "session_meta", "timestamp": 101, "payload": {
            "id": "child-thread", "forked_from_id": "parent-thread",
            "thread_source": "subagent",
        }},
        {"type": "session_meta", "timestamp": 101.25, "payload": {
            "id": "parent-thread", "model": "gpt-5.6",
        }},
        {"type": "turn_context", "timestamp": 101.5, "payload": {
            "model": "gpt-5.6", "effort": "high",
        }},
        {"type": "event_msg", "timestamp": 102, "payload": {
            "type": "thread_settings_applied",
            "thread_settings": {"model": "gpt-5.6", "reasoning_effort": "high"},
        }},
        {"type": "event_msg", "timestamp": 103, "payload": {
            "type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 1_400_000_000,
                "cached_input_tokens": 1_000_000_000,
                "output_tokens": 100_000_000,
            }},
        }},
        {"type": "turn_context", "timestamp": 104, "payload": {
            "model": "gpt-5.6", "effort": "high",
        }},
        {"type": "inter_agent_communication_metadata", "timestamp": 104.5, "payload": {}},
        {"type": "event_msg", "timestamp": 105, "payload": {
            "type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 1_400_000_060,
                "cached_input_tokens": 1_000_000_040,
                "output_tokens": 100_000_020,
            }},
        }},
    ]
    transcript.write_text(
        "".join(f"{json.dumps(record, separators=(',', ':'))}\n" for record in records),
        encoding="utf-8",
    )
    rows = [{
        "key": "yo8881|0|codex", "transcript": str(transcript), "kind": "codex",
    }]
    current_scan_version = session_files._STATS_CURRENT_TRANSCRIPT_SCAN_VERSION
    monkeypatch.setattr(
        session_files,
        "_STATS_CURRENT_TRANSCRIPT_SCAN_VERSION",
        current_scan_version - 1,
    )
    old_scanner = StatsCurrentTranscriptUsageScanner()
    old_scan = old_scanner.scan(rows)
    old_record = session_files.stats_current_transcript_scan_record(transcript, "codex")
    old_cache_path = session_files.transcript_scan_store_path(old_record.identity)

    assert old_record.state["offset"] == transcript.stat().st_size
    assert old_record.identity[1] == current_scan_version - 1
    assert sum(item.atom.quantity for item in old_scan.tombstones) == 1_500_000_000
    assert sum(item.atom.quantity for item in old_scan.items) == 80
    old_scanner.commit(old_scan.receipt_id)
    assert old_cache_path.exists()

    def stored_atom(item):
        fields = {
            **vars(item.atom),
            "tmux_key": item.tmux_key,
            "agent_kind": item.agent_kind,
        }
        return usage_module.usage_atom_from_source(fields)

    legacy_atoms = tuple(stored_atom(item) for item in old_scan.tombstones)
    retained_atoms = tuple(stored_atom(item) for item in old_scan.items)
    assert {atom.payload["model"] for atom in legacy_atoms} == {"gpt-5.6"}
    database = tmp_path / storage_module.DATABASE_FILENAME
    store = storage_module.Store.open(database)
    store.append_batch(usage_atoms=(*legacy_atoms, *retained_atoms))

    evidence = pricing_module.PricingEvidence(
        "test-rate", "1.0", 1, "2026-07-16T00:00:00Z", "seed",
        "https://developers.openai.com/api/docs/pricing", 1,
    )

    # Give every fixture token an exact test price so a stale materialized cost
    # contribution cannot survive even though production leaves unknown models unpriced.
    def price(atom):
        quantity = int(atom.payload["quantity"])
        return pricing_module.UsagePriceProjection(quantity, quantity * 2, evidence)

    def materialize(snapshot, cache_generation):
        generation = materializer_module.build_generation(
            snapshot,
            source_generation=snapshot.schema.source_generation,
            cache_generation=cache_generation,
            generated_at=200,
            observed_until=200,
            price_resolver=price,
        )
        layer = materializer_module.slice_generation(generation, 300, 10)
        return generation, materializer_module.build_cost_report(layer)

    before_snapshot = store.read_snapshot()
    _before_generation, before_report = materialize(before_snapshot, 1)
    assert before_report["total_tokens"] == 1_500_000_080
    assert before_report["total_micro_usd"] == 1_500_000_080
    assert before_report["total_api_list_micro_usd"] == 3_000_000_160

    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    monkeypatch.setattr(
        session_files,
        "_STATS_CURRENT_TRANSCRIPT_SCAN_VERSION",
        current_scan_version,
    )
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["yo8881"]
    webapp.stats_agent_window_rows = lambda: [{"session": "yo8881"}]
    webapp.stats_agent_token_rows = lambda _rows: rows
    webapp.settings_payload = lambda: {"settings": {"cost": {
        "openai_pricing_profile": "default",
    }}}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()
    scheduled_attempt = attempt("agent_tokens", 10)
    scheduled_attempt.assert_current = lambda: None
    captured = []
    append_results = []

    class StoreClient:
        def append(self, **groups):
            result = store.append_batch(**groups)
            append_results.append(result)
            return {"ok": True, "counts": vars(result)}

    def collect(current_attempt):
        facts = webapp.collect_current_stats_agent_tokens(current_attempt)
        captured.append(facts)
        return facts

    current = current_runtime(StoreClient(), {"agent_tokens": collect})
    current._collector_callback("agent_tokens")(scheduled_attempt)

    assert len(captured) == 1
    assert sum(item.quantity for item in captured[0].usage_tombstones) == 1_500_000_000
    assert sum(item.payload["quantity"] for item in captured[0].usage_atoms) == 80
    assert sum(result.usage_tombstones_accepted for result in append_results) == 3
    assert sum(result.usage_atoms_duplicate for result in append_results) == 3
    current_record = session_files.stats_current_transcript_scan_record(transcript, "codex")
    current_cache_path = session_files.transcript_scan_store_path(current_record.identity)
    assert current_record.identity[1] == current_scan_version
    assert current_cache_path != old_cache_path
    assert current_cache_path.exists()

    after_snapshot = store.read_snapshot()
    assert len(after_snapshot.usage_atoms) == len(retained_atoms) == 3
    after_generation, after_report = materialize(after_snapshot, 2)
    assert before_report["total_tokens"] - after_report["total_tokens"] == 1_500_000_000
    assert before_report["total_micro_usd"] - after_report["total_micro_usd"] == 1_500_000_000
    assert (
        before_report["total_api_list_micro_usd"]
        - after_report["total_api_list_micro_usd"]
        == 3_000_000_000
    )
    assert after_report["total_tokens"] == 80
    assert after_report["total_micro_usd"] == 80
    assert after_report["total_api_list_micro_usd"] == 160
    after_bucket = next(
        bucket for bucket in after_generation.layer(10).buckets if bucket.start == 100
    )
    after_series = {item.name: item.value for item in after_bucket.series}
    assert after_series["agent_tokens_per_minute:yo8881|0|codex"] == 120
    assert after_series["model_tokens_per_minute:output:gpt-5.6"] == 120
    assert after_series["model_tokens_per_minute:all:gpt-5.6"] == 480
    assert not any("unknown" in name for name in after_series)
    assert sum(
        value for name, value in after_series.items()
        if name.startswith("agent_tokens_per_minute:")
    ) == sum(
        value for name, value in after_series.items()
        if name.startswith("model_tokens_per_minute:output:")
    )

    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    third_scanner = StatsCurrentTranscriptUsageScanner()
    third = third_scanner.scan(rows)
    assert third.items == ()
    assert third.tombstones == ()
    assert third.records_parsed == third.bytes_read == 0
    assert third.backlog_files == 0
    third_scanner.commit(third.receipt_id)
    store.close()


def test_delayed_token_scan_uses_profile_effective_when_each_atom_was_observed(tmp_path):
    transcript = tmp_path / "rollout-delayed.jsonl"
    transcript.write_text(
        '{"type":"turn_context","timestamp":100,"payload":{"model":"gpt-5.6"}}\n'
        '{"type":"event_msg","timestamp":150,"payload":{"info":{"total_token_usage":{"output_tokens":10}}}}\n'
        '{"type":"event_msg","timestamp":250,"payload":{"info":{"total_token_usage":{"output_tokens":25}}}}\n',
        encoding="utf-8",
    )
    configured = settings_module.default_settings()
    configured["cost"]["openai_pricing_profile"] = "subscription"
    configured["cost"]["_openai_pricing_profile_history"] = [
        "0|default",
        "200|subscription",
    ]
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["yo8881"]
    webapp.stats_agent_window_rows = lambda: [{"session": "yo8881"}]
    webapp.stats_agent_token_rows = lambda _rows: [{
        "key": "yo8881|0|codex",
        "transcript": str(transcript),
        "kind": "codex",
    }]
    webapp.settings_payload = lambda: {"settings": configured}
    webapp.stats_current_process_identity = lambda: ("web-8881", "web", 8881)
    webapp.stats_current_transcript_usage = StatsCurrentTranscriptUsageScanner()

    facts = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))

    assert {
        atom.observed_at: atom.payload["pricing_profile"]
        for atom in facts.usage_atoms
        if atom.direction == "output"
    } == {
        150.0: "default",
        250.0: "subscription",
    }


def test_token_adapter_does_not_claim_zero_coverage_when_roster_is_cold():
    """A statusd refresh gap is blank, never a collector failure or measured zero."""
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["yo8881"]
    webapp.stats_agent_window_rows = lambda: []

    facts = webapp.collect_current_stats_agent_tokens(attempt("agent_tokens", 10))

    assert facts.observations == ()
    assert facts.coverage_epochs == ()
    assert facts.usage_atoms == ()
    assert facts.usage_tombstones == ()
    assert facts.receipt is None


def test_background_owner_starts_only_the_current_stats_runtime():
    calls = []
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.background_owner = SimpleNamespace(status_payload=lambda: {"owner": True}, can_run=lambda _role: True, is_owner=lambda: True)
    # Slice B: owner acquisition also leases indexd via refresh_search_indexer_schedule; with no
    # configured roots the lease is a bounded no-op and must not perturb the private-worker call order.
    webapp.search_indexer = SimpleNamespace(lease_configured_roots=lambda roots: {"ok": True, "leased": False})
    webapp.settings_payload = lambda: {"settings": {"file_explorer": {"indexed_dirs": []}}}
    webapp.indexed_repo_discovery_dirs = lambda _fe: []
    webapp.log_event = lambda *args, **kwargs: calls.append("event")
    webapp.job_client = SimpleNamespace(start_for_scheduler=lambda: calls.append("job"))
    webapp.pricing_refresh_coordinator = SimpleNamespace(
        start_periodic=lambda: calls.append("pricing"),
    )
    webapp.stats_current_runtime = SimpleNamespace(start=lambda: calls.append("current") or True)
    webapp.stats_client = SimpleNamespace(
        ensure_started=lambda: pytest.fail("legacy stats client must not start"),
    )
    webapp.start_stats_metric_scheduler = lambda: pytest.fail("legacy scheduler must not start")
    webapp.warm_start_session_files_payload_cache = lambda: calls.append("session-files")
    webapp.warm_start_tabber_activity_cache = lambda: calls.append("tabber")
    webapp.start_tabber_activity_cache_warmer = lambda: calls.append("tabber-worker")
    webapp.publish_background_client_event = lambda *args, **kwargs: calls.append("publish")

    webapp.handle_background_owner_acquired({"last_transition": "acquired", "generation": {}})

    assert calls == ["event", "job", "pricing", "current", "session-files", "tabber", "tabber-worker", "publish"]


def test_background_owner_advertises_current_stats_writer_build(monkeypatch, tmp_path):
    captured = {}

    class Owner:
        status = "follower"

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            return True

    monkeypatch.setattr(app_module, "BackgroundOwnerRegistry", Owner)
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.control_server = SimpleNamespace(path=tmp_path / "control.sock")

    assert webapp.start_background_owner(port=7771, priority=0) is True
    assert captured["capabilities"] == {
        "stats_writer_build": app_module.stats_current_storage.MIN_WRITER_BUILD,
    }


def test_background_owner_demotion_stops_current_runtime_not_legacy_scheduler(monkeypatch):
    calls = []
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.pricing_refresh_coordinator = SimpleNamespace(
        stop_periodic=lambda: calls.append("pricing"),
    )
    webapp.stats_current_runtime = SimpleNamespace(stop=lambda: calls.append("current"))
    webapp.job_client = SimpleNamespace(stop_for_scheduler=lambda: calls.append("jobd"))
    webapp.stop_stats_metric_scheduler = lambda: pytest.fail("legacy scheduler must not stop")
    webapp.metadata_warm_lock = threading.Lock()
    webapp.metadata_warm_record = SimpleNamespace(stop_event=threading.Event())
    webapp.activity_transcript_service = SimpleNamespace(
        tabber_cache_lock=threading.Lock(),
        # The real record type: demotion sets its wake event so a parked warmer
        # exits promptly instead of waiting forever.
        tabber_warmer_record=app_module.TabberActivityWarmerRecord(),
        tabber_cache_record=SimpleNamespace(refresh_worker=object()),
    )
    webapp.session_files_service = app_module.SessionFilesService()
    reserved = webapp.session_files_service.reserve_work(("active",), "stable")
    assert reserved is not None
    webapp.background_owner = SimpleNamespace(status_payload=lambda: {"owner": False})
    # Slice B: demotion releases the indexd scheduler lease; a bounded no-op here that must not
    # perturb the asserted stop order.
    webapp.search_indexer = SimpleNamespace(release_scheduler_lease=lambda: None)
    webapp.publish_background_client_event = lambda *args, **kwargs: calls.append("publish")
    monkeypatch.setattr(app_module.file_index, "clear_memory_indexes", lambda: calls.append("indexes"))

    webapp.demote_background_owner()

    assert calls == ["pricing", "current", "jobd", "indexes", "publish"]
    assert webapp.activity_transcript_service.tabber_warmer_record.wake.is_set() is False  # fresh replacement record
    assert webapp.metadata_warm_record.stop_event.is_set()
    assert webapp.session_files_service.work_records == {}
    assert webapp.session_files_service.latest_stable_generations == {}


def test_app_shutdown_stops_current_runtime_not_legacy_scheduler():
    calls = []
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.pricing_refresh_coordinator = SimpleNamespace(
        stop_periodic=lambda: calls.append("pricing"),
    )
    webapp.stats_current_runtime = SimpleNamespace(stop=lambda: calls.append("current"))
    webapp.jobd_operation_service = SimpleNamespace(stop=lambda: calls.append("operations"))
    webapp.job_client = SimpleNamespace(stop_for_scheduler=lambda: calls.append("jobd"))
    webapp.stop_stats_metric_scheduler = lambda: pytest.fail("legacy scheduler must not stop")
    webapp.approval_client = SimpleNamespace(
        request=lambda *args, **kwargs: calls.append("approval"),
    )
    webapp.background_owner = app_module.DisabledBackgroundOwner()
    webapp.background_owner.stop = lambda: calls.append("owner")
    webapp.yoagent_controller = SimpleNamespace(
        close_yoagent_codex_app_server=lambda: calls.append("yoagent"),
    )
    webapp.control_server = SimpleNamespace(stop=lambda: calls.append("control"))

    webapp.stop_auto_approve_all()

    assert calls == ["pricing", "current", "operations", "jobd", "approval", "owner", "yoagent", "control"]


def test_legacy_stats_handlers_and_scheduler_bodies_are_deleted():
    app_source = inspect.getsource(app_module.TmuxWebtermApp)
    module_source = inspect.getsource(app_module)

    for retired in (
        "self.stats_client",
        "stats_metric_thread_context",
        "def record_stats_history_payload",
        "def start_stats_metric_scheduler",
        "def stop_stats_metric_scheduler",
        "def stats_metric_family_loop",
        "def record_stats_global_sample",
        "def stats_sample_context",
        "def stats_sample_history_query",
        "def stats_sample_payload",
        "def stats_sample_encoded_payload",
    ):
        assert retired not in app_source
    assert "from .statsd import StatsClient" not in module_source
    assert "STATS_HISTORY_TIERS" not in module_source
    assert not hasattr(http_routes, "get_stats_sample")
    assert not hasattr(http_routes, "post_stats_history")


def test_retired_stats_runtime_files_and_production_imports_are_deleted():
    root = Path(app_module.__file__).resolve().parent
    retired_files = (
        root / "statsd.py",
        root / "stats_families.py",
        root / "stats_rebuild.py",
        root / "local_services" / "stats_store.py",
    )
    assert not any(path.exists() for path in retired_files)

    forbidden_modules = {
        "yolomux_lib.statsd",
        "yolomux_lib.stats_families",
        "yolomux_lib.stats_rebuild",
        "yolomux_lib.local_services.stats_store",
    }
    imports = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    assert not imports & forbidden_modules

    app_source = inspect.getsource(app_module)
    state_source = (root / "state_services.py").read_text(encoding="utf-8")
    for retired in (
        "StatsHistoryService",
        "stats_history_service",
        "agent_token_state",
        "agent_token_next_sample_at",
        "agent_token_consumer_until",
        "agent_token_bootstrap_pending",
        "agent_token_worker",
        "scheduler_diagnostics",
    ):
        assert retired not in app_source
        assert retired not in state_source
