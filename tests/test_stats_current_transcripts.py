# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Regression tests for bounded current transcript usage scans."""

import json
import os
import threading
import time

import pytest

from yolomux_lib import session_files
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS
from yolomux_lib.pricing_catalog import PricingCatalog
from yolomux_lib.stats_current import materializer
from yolomux_lib.stats_current import pricing
from yolomux_lib.stats_current.storage import DATABASE_FILENAME
from yolomux_lib.stats_current.storage import Store
from yolomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yolomux_lib.stats_current.usage import usage_atom_from_source
from tools.mockers.transcript import append_record as _append_record
from tools.mockers.transcript import claude_usage as _claude_usage
from tools.mockers.transcript import codex_meta as _codex_meta
from tools.mockers.transcript import codex_usage as _codex_usage
from tools.mockers.transcript import write_records as _write_records
from tests.cross_layer_matrix import render_cost_model_table
from tests.helpers.gate_stats import commit_scan as _commit
from tests.terminal_state_guard import assert_terminal_transition


@pytest.fixture(autouse=True)
def _isolated_transcript_scan_store(tmp_path, monkeypatch):
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
        session_files._TRANSCRIPT_SCAN_CACHE_STATE_DIR = None
    yield
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
        session_files._TRANSCRIPT_SCAN_CACHE_STATE_DIR = None


def _output_items(result):
    return [item for item in result.items if item.atom.direction == "output"]


def test_usage_atom_backfill_status_varies_with_committed_cursor_progress(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    _write_records(transcript, [
        _codex_meta("backfill-status", model="gpt-5.6-sol"),
        _codex_usage(1_000, 10, 5, 1),
    ])
    scanner = StatsCurrentTranscriptUsageScanner(max_bytes_per_scan=16, max_records_per_scan=16)
    rows = [{"key": "yo7772|0|codex", "kind": "codex", "transcript": str(transcript)}]

    unknown = scanner.usage_atom_backfill_status()
    assert unknown is None
    _commit(scanner, scanner.scan(rows))
    pending = scanner.usage_atom_backfill_status()
    assert pending == {"state": "pending", "sources": 1, "missing": 1}

    scanner._max_bytes_per_scan = 1024 * 1024
    _commit(scanner, scanner.scan(rows))
    complete = scanner.usage_atom_backfill_status()
    assert complete == {"state": "complete", "sources": 1, "missing": 0}
    assert_terminal_transition(
        contract_id="usage-backfill-status",
        pending_observed=(unknown is None and pending["state"] == "pending"),
        terminal_observed=(complete["state"] == "complete" and complete != pending),
        evidence={"unknown": unknown, "pending": pending, "complete": complete},
    )


def test_backfill_scan_status_exposes_adapter_rejection_reasons(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    _write_records(transcript, [
        _codex_meta("scan-counters", model="gpt-5.6-sol"),
        _codex_usage(1_000, 10, 5, 1),
    ])
    result = StatsCurrentTranscriptUsageScanner().scan([
        {"key": "yo7772|0|codex", "kind": "codex", "transcript": str(transcript)},
    ])
    status = StatsCurrentTranscriptUsageScanner.usage_atom_backfill_status_for_scan(
        result,
        atoms_accepted=0,
        rejection_reasons={"payload.model exceeds 512 bytes": len(result.items)},
    )
    assert status["scan"] == {
        "files_read": 1,
        "records_parsed": 2,
        "atoms_emitted": len(result.items),
        "atoms_accepted": 0,
        "atoms_rejected": len(result.items),
        "rejection_reasons": {"payload.model exceeds 512 bytes": len(result.items)},
    }


def _model_totals(results):
    totals = {}
    for result in results:
        for item in result.items:
            key = (item.atom.model, item.atom.direction, item.atom.cache_role)
            totals[key] = totals.get(key, 0) + item.atom.quantity
    return totals


def test_opus_five_transcript_reaches_priced_api_report_and_costs_renderer(tmp_path):
    transcript = tmp_path / ".claude" / "projects" / "fixture" / "session.jsonl"
    _write_records(transcript, [
        _claude_usage("2026-09-02T00:00:01Z", "opus", "claude-opus-5", 10, 5),
        _claude_usage("2026-09-02T00:00:02Z", "unknown", "future-model", 10, 5),
    ])
    atoms = [
        usage_atom_from_source(atom)
        for atom in session_files.iter_claude_transcript_usage_atoms(transcript)
    ]
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(usage_atoms=atoms)
        snapshot = store.read_snapshot()
    observed_until = max(atom.observed_at for atom in atoms) + 1
    generation = materializer.build_generation(
        snapshot,
        source_generation=snapshot.schema.source_generation,
        cache_generation=1,
        generated_at=observed_until,
        observed_until=observed_until,
        price_resolver=pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing")),
    )
    report = materializer.build_cost_report(
        materializer.slice_generation(generation, 86_400, 300),
    )

    models = {row["model"]: row for row in report["models"]}
    assert models["claude-opus-5"]["total_micro_usd"] == 175
    assert models["claude-opus-5"]["unpriced"] == {"atoms": 0, "tokens": 0}
    assert models["future-model"]["total_micro_usd"] == 0
    assert models["future-model"]["unpriced"] == {"atoms": 2, "tokens": 15}
    assert report["total_micro_usd"] == 175
    assert report["unpriced"] == {"atoms": 2, "tokens": 15}

    rendered = render_cost_model_table(report)
    assert "claude-opus-5" in rendered
    assert "$0.000175" in rendered
    unknown_row = rendered.split("future-model", 1)[1].split("</tr>", 1)[0]
    assert "Unpriced" in unknown_row
    assert "$0" not in unknown_row

def test_two_recent_forks_do_not_add_copied_parent_tokens_or_cost(tmp_path):
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "16"
    root = sessions / "rollout-root.jsonl"
    first_fork = sessions / "rollout-first-fork.jsonl"
    second_fork = sessions / "rollout-second-fork.jsonl"
    root_context = {"type": "turn_context", "timestamp": 1_996_400, "payload": {"model": "gpt-5.6-sol"}}
    first_parent_usage = _codex_usage(1_996_410, 40, 20, 4)
    second_parent_usage = _codex_usage(1_996_420, 100, 60, 10)
    _write_records(root, [
        _codex_meta("root-thread", model="gpt-5.6-sol"),
        root_context,
        first_parent_usage,
        second_parent_usage,
    ])
    _write_records(first_fork, [
        _codex_meta(
            "first-child", "root-thread", forked_from_id="root-thread",
            thread_source="subagent",
        ),
        _codex_meta("root-thread", model="gpt-5.6-sol"),
        {**root_context, "timestamp": 1_999_900},
        {**first_parent_usage, "timestamp": 1_999_910},
        {**second_parent_usage, "timestamp": 1_999_920},
        {"type": "turn_context", "timestamp": 1_999_930, "payload": {"model": "gpt-5.6-sol"}},
        {"type": "inter_agent_communication_metadata", "timestamp": 1_999_940, "payload": {}},
        _codex_usage(1_999_950, 115, 65, 14),
    ])
    _write_records(second_fork, [
        _codex_meta(
            "second-child", "root-thread", forked_from_id="root-thread",
            thread_source="subagent",
        ),
        _codex_meta("root-thread", model="gpt-5.6-sol"),
        {**root_context, "timestamp": 1_999_900},
        {**first_parent_usage, "timestamp": 1_999_910},
        {"type": "turn_context", "timestamp": 1_999_930, "payload": {"model": "gpt-5.6-sol"}},
        {"type": "inter_agent_communication_metadata", "timestamp": 1_999_940, "payload": {}},
        _codex_usage(1_999_960, 47, 22, 7),
    ])
    scanner = StatsCurrentTranscriptUsageScanner()
    result = scanner.scan([{
        "key": "yo8881|0|codex", "kind": "codex", "transcript": str(root),
    }])

    totals = {}
    atoms = []
    for item in result.items:
        key = (item.atom.direction, item.atom.cache_role)
        totals[key] = totals.get(key, 0) + item.atom.quantity
        atoms.append(usage_atom_from_source({
            **vars(item.atom), "tmux_key": item.tmux_key,
            "agent_kind": item.agent_kind,
        }))
    assert totals == {
        ("input", "none"): 55,
        ("input", "read"): 67,
        ("output", "none"): 17,
    }
    assert sum(item.atom.quantity for item in result.tombstones) == 154

    evidence = pricing.PricingEvidence(
        "fixed-test-rate", "1", 1, "2026-07-16T00:00:00Z", "fixture",
        "https://developers.openai.com/api/docs/pricing", 1,
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(usage_atoms=atoms)
        snapshot = store.read_snapshot()
    generation = materializer.build_generation(
        snapshot,
        source_generation=snapshot.schema.source_generation,
        cache_generation=1,
        generated_at=2_000_000,
        observed_until=2_000_000,
        price_resolver=lambda atom: pricing.UsagePriceProjection(
            int(atom.payload["quantity"]), int(atom.payload["quantity"]) * 2,
            evidence,
        ),
    )
    report = materializer.build_cost_report(
        materializer.slice_generation(generation, 86_400, 300)
    )
    assert report["total_tokens"] == 139
    assert report["total_micro_usd"] == 139
    assert report["total_api_list_micro_usd"] == 278


def test_orphan_fork_repair_runs_once_without_reinserting_child_suffix(
    tmp_path,
    monkeypatch,
):
    sessions = tmp_path / ".codex" / "sessions"
    root = sessions / "2026" / "07" / "16" / "rollout-root.jsonl"
    orphan = sessions / "2026" / "01" / "01" / "rollout-orphan-fork.jsonl"
    _write_records(root, [
        _codex_meta("root-thread", model="gpt-root"),
        {"type": "turn_context", "timestamp": 1, "payload": {"model": "gpt-root"}},
    ])
    _write_records(orphan, [
        _codex_meta(
            "orphan-child", "root-thread", forked_from_id="root-thread",
            thread_source="subagent",
        ),
        _codex_meta("root-thread", model="gpt-root"),
        {"type": "turn_context", "timestamp": 2, "payload": {"model": "gpt-root"}},
        _codex_usage(3, 80, 30, 12),
        {"type": "turn_context", "timestamp": 4, "payload": {"model": "gpt-child"}},
        {"type": "inter_agent_communication_metadata", "timestamp": 4.5, "payload": {}},
        {"type": "response_item", "timestamp": 4.75, "payload": {"text": "x" * (256 * 1024)}},
        _codex_usage(5, 90, 35, 15),
    ])
    calls = []
    active_root = root

    def candidates(*, root: object = None, limit: int = 256):
        calls.append(limit)
        return [active_root, orphan] if limit >= 1 << 30 else [active_root]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    rows = [{"key": "root", "kind": "codex", "transcript": str(root)}]
    scanner = StatsCurrentTranscriptUsageScanner()

    repaired = scanner.scan(rows)

    assert repaired.items == ()
    assert sum(item.atom.quantity for item in repaired.tombstones) == 92
    assert {item.atom.agent_thread_id for item in repaired.tombstones} == {"orphan-child"}
    assert repaired.bytes_read < orphan.stat().st_size // 2
    _commit(scanner, repaired)
    repair_status = scanner.status()["legacy_fork_repair"]
    assert 0 < repair_status.pop("scan_bytes") <= repaired.bytes_read
    assert 0 < repair_status.pop("scan_records") <= repaired.records_parsed
    assert repair_status == {
        "active": True,
        "complete": True,
        "discovered_files": 1,
        "orphan_files": 1,
        "complete_files": 1,
        "remaining_files": 0,
        "remaining_bytes": 0,
        "advanced_files": 1,
        "budget_bytes": 8 * 1024 * 1024,
        "budget_records": 8192,
    }
    marker = scanner._legacy_fork_repair_marker()
    assert marker.read_text(encoding="utf-8") == (
        scanner._legacy_fork_repair_root_id(sessions) + "\n"
    )

    calls.clear()
    replayed = StatsCurrentTranscriptUsageScanner().scan(rows)
    assert replayed.tombstones == ()
    assert all(limit < 1 << 30 for limit in calls)


def test_orphan_fork_repair_rolls_back_and_resumes_after_cold_restart(
    tmp_path,
    monkeypatch,
):
    sessions = tmp_path / ".codex" / "sessions"
    root = sessions / "2026" / "07" / "16" / "rollout-root.jsonl"
    orphan = sessions / "2026" / "01" / "01" / "rollout-orphan-fork.jsonl"
    _write_records(root, [_codex_meta("root-thread", model="gpt-root")])
    _write_records(orphan, [
        _codex_meta(
            "orphan-child", "root-thread", forked_from_id="root-thread",
            thread_source="subagent",
        ),
        _codex_meta("root-thread", model="gpt-root"),
        {"type": "turn_context", "timestamp": 2, "payload": {"model": "gpt-root"}},
        _codex_usage(3, 10, 4, 2),
        _codex_usage(4, 20, 8, 4),
        {"type": "turn_context", "timestamp": 5, "payload": {"model": "gpt-child"}},
        {"type": "inter_agent_communication_metadata", "timestamp": 6, "payload": {}},
        _codex_usage(7, 30, 12, 6),
    ])

    def candidates(*, root: object = None, limit: int = 256):
        return [root_path, orphan] if limit >= 1 << 30 else [root_path]

    root_path = root
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    rows = [{"key": "root", "kind": "codex", "transcript": str(root)}]
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=5)
    marker = scanner._legacy_fork_repair_marker()

    unacknowledged = scanner.scan(rows)
    first_ids = {item.atom.event_id for item in unacknowledged.tombstones}
    assert unacknowledged.budget_exhausted is True
    assert first_ids
    assert marker.exists() is False
    scanner.rollback(unacknowledged.receipt_id)
    assert marker.exists() is False

    replayed = scanner.scan(rows)
    assert {item.atom.event_id for item in replayed.tombstones} == first_ids
    _commit(scanner, replayed)
    assert marker.exists() is False
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()

    resumed_scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=5)
    resumed = resumed_scanner.scan(rows)
    assert resumed.items == ()
    assert resumed_scanner.status()["legacy_fork_repair"]["complete"] is False
    assert marker.exists() is False
    _commit(resumed_scanner, resumed)
    assert marker.read_text(encoding="utf-8") == (
        resumed_scanner._legacy_fork_repair_root_id(sessions) + "\n"
    )
    assert sum(item.atom.quantity for item in replayed.tombstones) == 12
    assert sum(item.atom.quantity for item in resumed.tombstones) == 12


def test_legacy_repair_marker_does_not_skip_a_later_codex_sessions_root(
    tmp_path,
    monkeypatch,
):
    roots = [tmp_path / name / ".codex" / "sessions" for name in ("first", "second")]
    root_files = []
    orphan_files = []
    for index, sessions in enumerate(roots, 1):
        root = sessions / "2026" / "07" / "16" / "rollout-root.jsonl"
        orphan = sessions / "2026" / "01" / "01" / "rollout-orphan.jsonl"
        _write_records(root, [_codex_meta(f"root-{index}")])
        _write_records(orphan, [
            _codex_meta(
                f"child-{index}", f"root-{index}",
                forked_from_id=f"root-{index}", thread_source="subagent",
            ),
            _codex_meta(f"root-{index}", model="gpt-root"),
            _codex_usage(2, 10 * index, 4 * index, 2 * index),
            {"type": "inter_agent_communication_metadata", "timestamp": 3, "payload": {}},
        ])
        root_files.append(root)
        orphan_files.append(orphan)

    def candidates(*, root: object = None, limit: int = 256):
        index = roots.index(root)
        return [root_files[index], orphan_files[index]] if limit >= 1 << 30 else [root_files[index]]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    first_scanner = StatsCurrentTranscriptUsageScanner()
    first = first_scanner.scan([{
        "key": "first", "kind": "codex", "transcript": str(root_files[0]),
    }])
    assert first.tombstones
    _commit(first_scanner, first)

    second_scanner = StatsCurrentTranscriptUsageScanner()
    second = second_scanner.scan([{
        "key": "second", "kind": "codex", "transcript": str(root_files[1]),
    }])
    assert second.tombstones
    _commit(second_scanner, second)
    assert set(first_scanner._legacy_fork_repair_marker().read_text(encoding="utf-8").splitlines()) == {
        first_scanner._legacy_fork_repair_root_id(root) for root in roots
    }


def test_single_root_legacy_completion_marker_migrates_without_replay(
    tmp_path,
    monkeypatch,
):
    sessions = tmp_path / ".codex" / "sessions"
    root = sessions / "2026" / "07" / "16" / "rollout-root.jsonl"
    _write_records(root, [_codex_meta("root-thread")])
    scanner = StatsCurrentTranscriptUsageScanner()
    marker = scanner._legacy_fork_repair_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("complete\n", encoding="utf-8")
    calls = []

    def candidates(*, root: object = None, limit: int = 256):
        calls.append(limit)
        return [root_path]

    root_path = root
    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    result = scanner.scan([{
        "key": "root", "kind": "codex", "transcript": str(root),
    }])

    assert result.tombstones == ()
    assert all(limit < 1 << 30 for limit in calls)
    assert marker.read_text(encoding="utf-8") == (
        scanner._legacy_fork_repair_root_id(sessions) + "\n"
    )


def test_codex_scan_is_incremental_and_resets_safely_after_rewrite(tmp_path):
    transcript = tmp_path / "rollout-current.jsonl"
    _write_records(transcript, [
        _codex_meta("thread-one"),
        {
            "type": "turn_context",
            "timestamp": 2,
            "payload": {"model": "gpt-current", "effort": "high"},
        },
        _codex_usage(3, 100, 40, 10),
    ])
    rows = [{"key": "yo8881|0|codex", "kind": "codex", "transcript": str(transcript)}]
    scanner = StatsCurrentTranscriptUsageScanner()

    first = scanner.scan(rows)
    assert first.files_considered == first.files_read == 1
    assert first.records_parsed == 3
    assert [(item.atom.quantity, item.atom.model, item.atom.event_id) for item in _output_items(first)] == [
        (10, "gpt-current", "codex:thread-one:2"),
    ]
    _commit(scanner, first)

    unchanged = scanner.scan(rows)
    assert unchanged.items == ()
    assert unchanged.files_read == unchanged.bytes_read == unchanged.records_parsed == 0
    _commit(scanner, unchanged)

    appended_line = _append_record(transcript, _codex_usage(4, 150, 70, 25))
    appended = scanner.scan(rows)
    assert appended.files_read == 1
    assert appended.bytes_read == len(appended_line.encode("utf-8"))
    assert appended.records_parsed == 1
    assert [(item.atom.quantity, item.atom.event_id) for item in _output_items(appended)] == [
        (15, "codex:thread-one:3"),
    ]
    _commit(scanner, appended)

    _write_records(transcript, [
        _codex_meta("thread-two"),
        _codex_usage(5, 20, 5, 7),
    ])
    rewritten = scanner.scan(rows)
    assert rewritten.resets == 1
    assert rewritten.records_parsed == 2
    assert [(item.atom.quantity, item.atom.event_id) for item in _output_items(rewritten)] == [
        (7, "codex:thread-two:1"),
    ]
    _commit(scanner, rewritten)
    unchanged_after_rewrite = scanner.scan(rows)
    assert unchanged_after_rewrite.items == ()
    _commit(scanner, unchanged_after_rewrite)


def test_visible_append_status_changes_only_after_growth_receipt_commits(tmp_path):
    transcript = tmp_path / "rollout-status.jsonl"
    _write_records(transcript, [_codex_meta("status-thread"), _codex_usage(2, 10, 0, 3)])
    rows = [{"key": "yo8881|0|codex", "kind": "codex", "transcript": str(transcript)}]
    now = [100.0]
    scanner = StatsCurrentTranscriptUsageScanner(clock=lambda: now[0])

    cold = scanner.scan(rows)
    scanner.commit(cold.receipt_id)
    assert scanner.status() == {
        "committed_appended_bytes": 0,
        "last_visible_append_at": 0.0,
        "visible_append_age_seconds": None,
        "legacy_fork_repair": {
            "active": False,
            "complete": False,
            "discovered_files": 0,
            "orphan_files": 0,
            "complete_files": 0,
            "remaining_files": 0,
            "remaining_bytes": 0,
            "advanced_files": 0,
            "scan_bytes": 0,
            "scan_records": 0,
            "budget_bytes": 4 * 1024 * 1024,
            "budget_records": 4096,
        },
    }

    appended_line = _append_record(transcript, _codex_usage(3, 20, 0, 5))
    uncommitted = scanner.scan(rows)
    assert scanner.status()["last_visible_append_at"] == 0.0
    scanner.rollback(uncommitted.receipt_id)
    assert scanner.status()["last_visible_append_at"] == 0.0

    replayed = scanner.scan(rows)
    now[0] = 110.0
    scanner.commit(replayed.receipt_id)
    status = scanner.status()
    assert status["committed_appended_bytes"] == len(appended_line.encode("utf-8"))
    assert status["last_visible_append_at"] == 110.0
    now[0] = 117.5
    assert scanner.status()["visible_append_age_seconds"] == 7.5


def test_claude_scan_preserves_repeated_message_cumulative_state(tmp_path):
    transcript = tmp_path / "claude.jsonl"
    base = {
        "type": "assistant",
        "timestamp": 1,
        "message": {
            "id": "message-one",
            "model": "claude-current",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        },
    }
    _write_records(transcript, [base])
    rows = [{"key": "yo8881|1|claude", "kind": "claude", "transcript": str(transcript)}]
    scanner = StatsCurrentTranscriptUsageScanner()

    first = scanner.scan(rows)
    assert [(item.atom.quantity, item.atom.event_id) for item in _output_items(first)] == [
        (10, f"claude:{transcript}:message-one"),
    ]
    _commit(scanner, first)
    unchanged = scanner.scan(rows)
    assert unchanged.records_parsed == 0
    _commit(scanner, unchanged)

    repeated = dict(base)
    repeated["timestamp"] = 2
    repeated["message"] = dict(base["message"])
    repeated["message"]["usage"] = {"input_tokens": 25, "output_tokens": 15}
    _append_record(transcript, repeated)
    delta = scanner.scan(rows)
    assert [(item.atom.quantity, item.atom.event_id) for item in _output_items(delta)] == [
        (5, f"claude:{transcript}:message-one:revision-1"),
    ]
    _commit(scanner, delta)
    first_identity = _output_items(first)[0].atom.event_id
    delta_identity = _output_items(delta)[0].atom.event_id
    assert first_identity != delta_identity
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        for item in (*first.items, *delta.items):
            fields = dict(vars(item.atom))
            fields["tmux_key"] = item.tmux_key
            fields["agent_kind"] = item.agent_kind
            store.append_usage_atom(usage_atom_from_source(fields))

    repeated_again = dict(repeated)
    repeated_again["timestamp"] = 3
    _append_record(transcript, repeated_again)
    no_delta = scanner.scan(rows)
    assert no_delta.records_parsed == 1
    assert no_delta.items == ()
    _commit(scanner, no_delta)


def test_claude_project_sibling_is_independent_and_incremental(tmp_path):
    project = tmp_path / "-Users-keivenc-project"
    pane = project / "pane-session.jsonl"
    background = project / "background-session.jsonl"
    background_subagent = project / "background-session" / "subagents" / "agent-one.jsonl"
    unrelated = tmp_path / "-Users-keivenc-other" / "unrelated.jsonl"
    _write_records(pane, [_claude_usage(1, "pane-message", "claude-pane", 20, 5)])
    _write_records(background, [_claude_usage(2, "background-message", "claude-background", 40, 9)])
    _write_records(background_subagent, [_claude_usage(3, "subagent-message", "claude-background", 10, 3)])
    _write_records(unrelated, [_claude_usage(4, "unrelated-message", "claude-other", 100, 50)])
    os.utime(pane, (1, 1))
    os.utime(background, (10, 10))
    rows = [{"key": "yo8881|1|claude", "kind": "claude", "transcript": str(pane)}]
    scanner = StatsCurrentTranscriptUsageScanner()

    first = scanner.scan(rows)
    outputs = {item.atom.event_id: item for item in _output_items(first)}
    pane_id = f"claude:{pane}:pane-message"
    background_id = f"claude:{background}:background-message"
    subagent_id = f"claude:{background_subagent}:subagent-message"
    assert set(outputs) == {pane_id, background_id, subagent_id}
    assert outputs[pane_id].tmux_key == "yo8881|1|claude"
    background_key = scanner._claude_background_agent_key(background)
    assert len(background_key.encode("utf-8")) <= 192
    assert outputs[background_id].tmux_key == background_key
    assert outputs[subagent_id].tmux_key == background_key
    assert outputs[background_id].atom.root_thread_id == str(background.resolve())
    assert outputs[subagent_id].atom.root_thread_id == str(background.resolve())
    assert all("unrelated-message" not in item.atom.event_id for item in first.items)
    _commit(scanner, first)

    unchanged = scanner.scan(rows)
    assert unchanged.items == ()
    assert unchanged.records_parsed == 0
    _commit(scanner, unchanged)

    _append_record(background, _claude_usage(5, "background-next", "claude-background", 7, 2))
    appended = scanner.scan(rows)
    assert [(item.atom.event_id, item.atom.quantity, item.tmux_key) for item in _output_items(appended)] == [
        (f"claude:{background}:background-next", 2, background_key),
    ]
    _commit(scanner, appended)

    _append_record(background, _claude_usage(6, "background-direct", "claude-background", 8, 3))
    direct = scanner.scan([*rows, {
        "key": "yo8881|2|claude", "kind": "claude", "transcript": str(background),
    }])
    assert [(item.atom.event_id, item.atom.quantity, item.tmux_key) for item in _output_items(direct)] == [
        (f"claude:{background}:background-direct", 3, "yo8881|2|claude"),
    ]
    _commit(scanner, direct)


def test_newest_claude_background_session_precedes_large_cold_siblings(tmp_path):
    project = tmp_path / "-Users-keivenc-project"
    pane = project / "z-pane.jsonl"
    active = project / "b-active.jsonl"
    _write_records(pane, [_claude_usage(1, "pane", "claude-pane", 1, 1)])
    for index in range(3):
        stale = project / f"a-stale-{index}.jsonl"
        _write_records(stale, [
            _claude_usage(2, f"stale-{index}", "claude-stale", 1, 1),
            *(
                {"type": "progress", "timestamp": line, "payload": {"text": "x"}}
                for line in range(100)
            ),
        ])
        os.utime(stale, (2 + index, 2 + index))
    _write_records(active, [_claude_usage(10, "active", "claude-active", 10, 4)])
    os.utime(pane, (1, 1))
    os.utime(active, (100, 100))
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=2)

    first = scanner.scan([{
        "key": "yo8881|1|claude", "kind": "claude", "transcript": str(pane),
    }])

    assert first.budget_exhausted is True
    assert [(item.atom.event_id, item.tmux_key) for item in _output_items(first)][:1] == [
        (f"claude:{active}:active", scanner._claude_background_agent_key(active)),
    ]


def test_claude_sibling_keeps_cursor_when_pane_attribution_changes(tmp_path):
    project = tmp_path / "-Users-keivenc-project"
    pane = project / "pane.jsonl"
    sibling = project / "sibling.jsonl"
    _write_records(pane, [_claude_usage(1, "pane", "claude-pane", 1, 1)])
    _write_records(sibling, [_claude_usage(2, "before", "claude-sibling", 10, 3)])
    first_scanner = StatsCurrentTranscriptUsageScanner()
    direct = first_scanner.scan([{
        "key": "yo8881|2|claude", "kind": "claude", "transcript": str(sibling),
    }])
    _commit(first_scanner, direct)
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    _append_record(sibling, _claude_usage(3, "after", "claude-sibling", 4, 2))

    resumed_scanner = StatsCurrentTranscriptUsageScanner()
    resumed = resumed_scanner.scan([{
        "key": "yo8881|1|claude", "kind": "claude", "transcript": str(pane),
    }])

    outputs = [item for item in _output_items(resumed) if str(sibling) in item.atom.event_id]
    assert [(item.atom.event_id, item.atom.quantity, item.tmux_key) for item in outputs] == [
        (
            f"claude:{sibling}:after",
            2,
            resumed_scanner._claude_background_agent_key(sibling),
        ),
    ]


def test_overlapping_codex_roster_families_scan_each_file_once(tmp_path):
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "15"
    parent = sessions / "rollout-parent.jsonl"
    child = sessions / "rollout-child.jsonl"
    _write_records(parent, [_codex_meta("parent-thread"), _codex_usage(2, 10, 0, 7)])
    _write_records(child, [_codex_meta("child-thread", "parent-thread"), _codex_usage(3, 5, 0, 3)])
    rows = [
        {"key": "yo8881|1|codex", "kind": "codex", "transcript": str(child)},
        {"key": "yo8881|0|codex", "kind": "codex", "transcript": str(parent)},
    ]

    result = StatsCurrentTranscriptUsageScanner().scan(rows)

    assert result.files_considered == result.files_read == 2
    assert result.records_parsed == 4
    outputs = {item.atom.agent_thread_id: item for item in _output_items(result)}
    assert set(outputs) == {"parent-thread", "child-thread"}
    assert outputs["parent-thread"].tmux_key == "yo8881|0|codex"
    assert outputs["child-thread"].tmux_key == "yo8881|1|codex"
    assert outputs["child-thread"].atom.root_thread_id == "parent-thread"
    assert outputs["child-thread"].atom.parent_thread_id == "parent-thread"
    assert outputs["child-thread"].atom.depth == 1


def test_cold_large_transcript_is_budgeted_then_converges_to_zero_read(tmp_path):
    transcript = tmp_path / "rollout-large.jsonl"
    records = [
        _codex_meta("large-thread"),
        {"type": "turn_context", "timestamp": 2, "payload": {"model": "gpt-large"}},
        *({"type": "response_item", "timestamp": index + 3, "payload": {"text": "x" * 2048}} for index in range(200)),
        _codex_usage(6000, 100, 20, 50),
    ]
    _write_records(transcript, records)
    rows = [{"key": "large", "kind": "codex", "transcript": str(transcript)}]
    scanner = StatsCurrentTranscriptUsageScanner(
        max_bytes_per_scan=4096,
        max_records_per_scan=10,
    )

    cold = scanner.scan(rows)
    largest_line = max(
        len((json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8"))
        for record in records
    )
    assert cold.budget_exhausted is True
    assert cold.backlog_files == 1
    assert cold.records_parsed <= 10
    assert cold.bytes_read <= 4096 + largest_line
    _commit(scanner, cold)

    parsed_records = cold.records_parsed
    output_quantities = [item.atom.quantity for item in _output_items(cold)]
    for _attempt in range(200):
        resumed = scanner.scan(rows)
        parsed_records += resumed.records_parsed
        output_quantities.extend(item.atom.quantity for item in _output_items(resumed))
        assert resumed.records_parsed <= 10
        assert resumed.bytes_read <= 4096 + largest_line
        _commit(scanner, resumed)
        if resumed.backlog_files == 0:
            break
    assert resumed.backlog_files == 0
    assert parsed_records == len(records)
    assert output_quantities == [50]

    unchanged = scanner.scan(rows)
    assert unchanged.items == ()
    assert unchanged.files_read == unchanged.bytes_read == unchanged.records_parsed == 0
    _commit(scanner, unchanged)

    appended_line = _append_record(transcript, _codex_usage(6001, 101, 20, 51))
    appended = scanner.scan(rows)
    assert appended.records_parsed == 1
    assert appended.bytes_read == len(appended_line.encode("utf-8"))
    assert appended.backlog_files == 0
    assert [item.atom.quantity for item in _output_items(appended)] == [1]
    _commit(scanner, appended)


def test_budgeted_round_robin_commits_cursor_before_advancing_to_late_fork(tmp_path):
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "16"
    root = sessions / "rollout-a-long-root.jsonl"
    fork = sessions / "rollout-z-late-fork.jsonl"
    _write_records(root, [
        _codex_meta("root-thread"),
        {"type": "turn_context", "timestamp": 2, "payload": {"model": "gpt-root"}},
        *(
            {"type": "response_item", "timestamp": index + 3, "payload": {"text": "root"}}
            for index in range(12)
        ),
    ])
    _write_records(fork, [
        _codex_meta(
            "fork-thread",
            "root-thread",
            forked_from_id="root-thread",
            thread_source="subagent",
        ),
        {
            "type": "event_msg",
            "timestamp": 2,
            "payload": {
                "type": "thread_settings_applied",
                "thread_settings": {"model": "gpt-fork", "reasoning_effort": "high"},
            },
        },
        _codex_usage(3, 80, 30, 12),
        {"type": "turn_context", "timestamp": 4, "payload": {"model": "gpt-live"}},
        {"type": "inter_agent_communication_metadata", "timestamp": 4.5, "payload": {}},
        _codex_usage(5, 90, 35, 15),
    ])
    rows = [{"key": "root", "kind": "codex", "transcript": str(root)}]
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=2)
    root_source = str(root.resolve())
    fork_source = str(fork.resolve())

    unacknowledged = scanner.scan(rows)
    assert unacknowledged.records_parsed == 2
    assert set(scanner._inflight.files) == {root_source}
    assert scanner._inflight.next_source == fork_source
    assert scanner._next_source is None
    assert fork_source not in scanner._files
    scanner.rollback(unacknowledged.receipt_id)
    assert scanner._next_source is None

    replayed = scanner.scan(rows)
    assert replayed.records_parsed == 2
    assert set(scanner._inflight.files) == {root_source}
    assert fork_source not in scanner._files
    _commit(scanner, replayed)
    assert scanner._next_source == fork_source

    fork_context = scanner.scan(rows)
    assert fork_context.records_parsed == 2
    assert set(scanner._inflight.files) == {fork_source}
    assert scanner._inflight.next_source == root_source
    assert fork_context.items == fork_context.tombstones == ()
    _commit(scanner, fork_context)

    committed = [replayed, fork_context]
    fork_advanced_before_root_drained = False
    for _attempt in range(20):
        result = scanner.scan(rows)
        committed.append(result)
        if any(item.atom.agent_thread_id == "fork-thread" for item in result.tombstones):
            fork_advanced_before_root_drained = (
                scanner._files[root_source].offset < root.stat().st_size
            )
        _commit(scanner, result)
        if result.backlog_files == 0:
            break

    assert result.backlog_files == 0
    assert fork_advanced_before_root_drained is True
    fork_tombstones = [
        item.atom
        for result in committed
        for item in result.tombstones
        if item.atom.agent_thread_id == "fork-thread" and item.atom.direction == "output"
    ]
    fork_outputs = [
        item.atom
        for result in committed
        for item in result.items
        if item.atom.agent_thread_id == "fork-thread" and item.atom.direction == "output"
    ]
    assert [
        (atom.quantity, atom.model, atom.model_evidence, atom.timestamp)
        for atom in fork_tombstones
    ] == [(12, "gpt-fork", "scan_state.resumed_model", 3)]
    assert [
        (atom.quantity, atom.model, atom.model_evidence, atom.timestamp)
        for atom in fork_outputs
    ] == [(3, "gpt-live", "scan_state.resumed_model", 5)]

    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    cold_scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=2)
    cold = cold_scanner.scan(rows)
    assert cold.items == cold.tombstones == ()
    assert cold.records_parsed == cold.bytes_read == 0
    assert cold.backlog_files == 0
    _commit(cold_scanner, cold)


def test_cross_tier_fairness_cursors_advance_only_after_commit(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions"
    live = sessions / "2026" / "07" / "16" / "rollout-live.jsonl"
    repair = sessions / "2026" / "01" / "01" / "rollout-repair.jsonl"
    _write_records(live, [])
    _write_records(repair, [
        _codex_meta(
            "repair-child",
            "live-thread",
            forked_from_id="live-thread",
            thread_source="subagent",
        ),
        _codex_meta("live-thread", model="gpt-live"),
    ])

    def candidates(*, root: object = None, limit: int = 256):
        return [live, repair] if limit >= 1 << 30 else [live]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=1)
    rows = [{"key": "live", "kind": "codex", "transcript": str(live)}]
    _append_record(live, _codex_meta("live-thread", model="gpt-live"))

    uncommitted = scanner.scan(rows)
    assert scanner._live_priority_scans == 0
    assert scanner._next_historical_source is None
    scanner.rollback(uncommitted.receipt_id)
    assert scanner._live_priority_scans == 0
    assert scanner._next_historical_source is None

    replayed = scanner.scan(rows)
    _commit(scanner, replayed)
    assert scanner._live_priority_scans == 1
    _append_record(live, {
        "type": "response_item",
        "timestamp": 2,
        "payload": {"text": "live"},
    })
    _commit(scanner, scanner.scan(rows))
    assert scanner._live_priority_scans == 2
    _append_record(live, {
        "type": "response_item",
        "timestamp": 3,
        "payload": {"text": "live"},
    })

    forced = scanner.scan(rows)
    assert set(scanner._inflight.files) == {str(repair)}
    assert scanner._live_priority_scans == 2
    assert scanner._next_historical_source is None
    scanner.rollback(forced.receipt_id)
    assert scanner._live_priority_scans == 2
    assert scanner._next_historical_source is None

    replayed_forced = scanner.scan(rows)
    assert set(scanner._inflight.files) == {str(repair)}
    _commit(scanner, replayed_forced)
    assert scanner._live_priority_scans == 0
    assert scanner._next_historical_source == str(repair)


def test_live_tails_advance_before_new_historical_backlog(tmp_path):
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "16"
    active_a = sessions / "rollout-a-active.jsonl"
    active_b = sessions / "rollout-b-active.jsonl"
    cold = sessions / "rollout-0-cold.jsonl"
    _write_records(active_a, [_codex_meta("active-a"), _codex_usage(2, 10, 0, 1)])
    _write_records(active_b, [_codex_meta("active-b"), _codex_usage(2, 20, 0, 2)])
    rows = [
        {"key": "active-a", "kind": "codex", "transcript": str(active_a)},
        {"key": "active-b", "kind": "codex", "transcript": str(active_b)},
    ]
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=10)

    initial = scanner.scan(rows)
    _commit(scanner, initial)
    assert initial.backlog_files == 0

    _write_records(cold, [
        _codex_meta("cold", "active-a", forked_from_id="active-a", thread_source="subagent"),
        *({"type": "response_item", "timestamp": index + 2, "payload": {"text": "old"}} for index in range(10)),
    ])
    _append_record(active_a, _codex_usage(3, 20, 0, 3))
    _append_record(active_b, _codex_usage(3, 30, 0, 4))
    scanner._max_records_per_scan = 1

    first_live = scanner.scan(rows)
    assert set(scanner._inflight.files) == {str(active_a.resolve())}
    assert str(cold.resolve()) not in scanner._files
    _commit(scanner, first_live)

    second_live = scanner.scan(rows)
    assert set(scanner._inflight.files) == {str(active_b.resolve())}
    assert str(cold.resolve()) not in scanner._files
    _commit(scanner, second_live)
    assert sum(item.atom.quantity for item in (*first_live.items, *second_live.items) if item.atom.direction == "output") == 4


def test_codex_two_pass_resume_keeps_model_effort_and_matches_full_rescan(tmp_path):
    records = [
        _codex_meta("resume-thread"),
        {"type": "turn_context", "timestamp": 2, "payload": {"model": "gpt-resumed", "effort": "high"}},
        _codex_usage(3, 100, 40, 10),
        _codex_usage(4, 150, 70, 25),
    ]
    transcript = tmp_path / "rollout-resume.jsonl"
    _write_records(transcript, records)
    rows = [{"key": "resume", "kind": "codex", "transcript": str(transcript)}]
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=2)

    context_pass = scanner.scan(rows)
    _commit(scanner, context_pass)
    usage_pass = scanner.scan(rows)

    assert context_pass.items == ()
    resumed = _output_items(usage_pass)
    assert [(item.atom.quantity, item.atom.model, item.atom.effort, item.atom.model_evidence) for item in resumed] == [
        (10, "gpt-resumed", "high", "scan_state.resumed_model"),
        (15, "gpt-resumed", "high", "scan_state.resumed_model"),
    ]
    canonical = usage_atom_from_source({**vars(resumed[0].atom), "tmux_key": resumed[0].tmux_key})
    assert canonical.payload["model_evidence"] == "scan_state.resumed_model"

    full_transcript = tmp_path / "rollout-full.jsonl"
    _write_records(full_transcript, records)
    full = StatsCurrentTranscriptUsageScanner().scan([
        {"key": "resume", "kind": "codex", "transcript": str(full_transcript)},
    ])
    assert _model_totals((context_pass, usage_pass)) == _model_totals((full,))
    _commit(scanner, usage_pass)


def test_codex_cold_restart_restores_durable_model_effort_and_counters(tmp_path):
    transcript = tmp_path / "rollout-cold.jsonl"
    _write_records(transcript, [
        _codex_meta("cold-thread"),
        {"type": "turn_context", "timestamp": 2, "payload": {"model": "gpt-cold", "effort": "medium"}},
        {"type": "response_item", "timestamp": 3, "payload": {"text": "x" * (70 * 1024)}},
        _codex_usage(4, 80, 30, 12),
    ])
    rows = [{"key": "cold", "kind": "codex", "transcript": str(transcript)}]
    first_scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=2)

    first = first_scanner.scan(rows)
    durable = session_files.stats_current_transcript_scan_record(transcript, "codex")
    cache_path = session_files.transcript_scan_store_path(durable.identity)

    assert first.items == ()
    assert first.backlog_files == 1
    _commit(first_scanner, first)
    assert cache_path.exists()
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()

    resumed = StatsCurrentTranscriptUsageScanner().scan(rows)
    output = _output_items(resumed)
    assert [(item.atom.quantity, item.atom.model, item.atom.effort, item.atom.model_evidence) for item in output] == [
        (12, "gpt-cold", "medium", "scan_state.resumed_model"),
    ]
    assert resumed.records_parsed == 2
    assert resumed.bytes_read < transcript.stat().st_size


def test_fresh_host_partition_replays_transcript_despite_legacy_eof_cursor(tmp_path, monkeypatch):
    transcript = tmp_path / "rollout-partitioned.jsonl"
    _write_records(transcript, [
        _codex_meta("partition-thread", model="gpt-partition"),
        _codex_usage(2, 20, 10, 5),
    ])
    rows = [{"key": "partition", "kind": "codex", "transcript": str(transcript)}]
    state_dir = tmp_path / "state"
    legacy_store = state_dir / f"transcript-scan-cache-v{session_files._TRANSCRIPT_SCAN_STORE_VERSION}"

    monkeypatch.setattr(session_files.common, "STATE_DIR", state_dir)
    monkeypatch.setattr(
        session_files,
        "host_partitioned_state_dir",
        lambda root: state_dir / "hosts" / "host-a",
        raising=False,
    )
    with monkeypatch.context() as legacy_cursor:
        legacy_cursor.setattr(session_files, "transcript_scan_store_dir", lambda: legacy_store)
        old_scanner = StatsCurrentTranscriptUsageScanner()
        old_scan = old_scanner.scan(rows)
        assert _output_items(old_scan)
        _commit(old_scanner, old_scan)

    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
        session_files._TRANSCRIPT_SCAN_CACHE_STATE_DIR = None

    fresh_scanner = StatsCurrentTranscriptUsageScanner()
    fresh_scan = fresh_scanner.scan(rows)
    fresh_atoms = tuple(
        usage_atom_from_source({**vars(item.atom), "tmux_key": item.tmux_key, "agent_kind": item.agent_kind})
        for item in fresh_scan.items
    )
    database = state_dir / "hosts" / "host-a" / DATABASE_FILENAME
    with Store.open(database) as store:
        appended = store.append_batch(usage_atoms=fresh_atoms)
        assert appended.usage_atoms_accepted > 0
        assert store.read_snapshot().usage_atoms
    _commit(fresh_scanner, fresh_scan)

    fresh_record = session_files.stats_current_transcript_scan_record(transcript, "codex")
    assert session_files.transcript_scan_store_path(fresh_record.identity).parent == database.parent / f"transcript-scan-cache-v{session_files._TRANSCRIPT_SCAN_STORE_VERSION}"
    assert legacy_store.exists()


def test_incomplete_backfill_cursors_survive_pruning_until_every_file_advances(tmp_path, monkeypatch):
    paths = []
    for index in range(3):
        path = tmp_path / f"rollout-{index}.jsonl"
        _write_records(path, [
            _codex_meta(f"prune-thread-{index}", model="gpt-prune"),
            _codex_usage(index + 1, 10, 5, index + 1),
        ])
        paths.append(path)
    rows = [
        {"key": f"prune|{index}|codex", "kind": "codex", "transcript": str(path)}
        for index, path in enumerate(paths)
    ]
    output_ids = set()

    for index in (*range(3), *range(3)):
        scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=1)
        result = scanner.scan([rows[index]])
        output_ids.update(item.atom.event_id for item in result.items)
        _commit(scanner, result)
        session_files.prune_transcript_scan_store(max_entries=1, max_bytes=64 * 1024 * 1024)

    assert len(output_ids) == 3


def test_partial_receipt_persist_failure_replays_all_files_in_process(tmp_path, monkeypatch):
    paths = [tmp_path / "rollout-a.jsonl", tmp_path / "rollout-b.jsonl"]
    for index, path in enumerate(paths, 1):
        _write_records(path, [
            _codex_meta(f"thread-{index}"),
            {"type": "turn_context", "timestamp": 2, "payload": {"model": "gpt-test"}},
            _codex_usage(3, 10, 0, index),
        ])
    rows = [
        {"key": f"agent-{index}", "kind": "codex", "transcript": str(path)}
        for index, path in enumerate(paths, 1)
    ]
    scanner = StatsCurrentTranscriptUsageScanner()
    first = scanner.scan(rows)
    first_ids = {item.atom.event_id for item in first.items}
    real_persist = session_files.persist_transcript_scan_state
    calls = []

    def fail_second(cache_key, state, *, force=False):
        calls.append(cache_key)
        if len(calls) == 2:
            return False
        return real_persist(cache_key, state, force=force)

    monkeypatch.setattr(session_files, "persist_transcript_scan_state", fail_second)
    with pytest.raises(OSError, match="persist transcript scan receipt"):
        scanner.commit(first.receipt_id)
    scanner.rollback(first.receipt_id)

    monkeypatch.setattr(session_files, "persist_transcript_scan_state", real_persist)
    replayed = scanner.scan(rows)
    assert {item.atom.event_id for item in replayed.items} == first_ids
    _commit(scanner, replayed)


def test_receipt_larger_than_memory_retention_commits_before_pruning(tmp_path):
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "08" / "02"
    active = sessions / "rollout-active.jsonl"
    _write_records(active, [
        _codex_meta("active-thread", model="gpt-5.6-sol"),
        _codex_usage(2, 20, 10, 5),
    ])
    for index in range(512):
        _write_records(sessions / f"rollout-fork-{index:04d}.jsonl", [
            _codex_meta(
                f"fork-{index}",
                "active-thread",
                forked_from_id="active-thread",
                thread_source="subagent",
            ),
        ])
    scanner = StatsCurrentTranscriptUsageScanner()

    result = scanner.scan([{
        "key": "active", "kind": "codex", "transcript": str(active),
    }])

    assert result.files_read == 513
    assert len(scanner._inflight.files) == 513
    assert [item.atom.quantity for item in _output_items(result)] == [5]
    pending_observed = (
        scanner._inflight is not None
        and scanner._inflight.receipt_id == result.receipt_id
        and len(scanner._inflight.files) == 513
    )
    scanner.commit(result.receipt_id)
    assert len(scanner._files) == 512
    assert_terminal_transition(
        contract_id="transcript-scan-receipt",
        pending_observed=pending_observed,
        terminal_observed=(scanner._inflight is None and len(scanner._files) == 512),
        evidence={"receipt_id": result.receipt_id, "retained_files": len(scanner._files)},
    )


def test_real_codex_fork_suppresses_copied_metadata_context_and_usage_until_handoff(tmp_path):
    transcript = tmp_path / "rollout-real-fork.jsonl"
    records = [
        _codex_meta(
            "child-thread",
            "parent-thread",
            forked_from_id="parent-thread",
            thread_source="subagent",
        ),
        _codex_meta("parent-thread"),
        {"type": "turn_context", "timestamp": 2, "payload": {"model": "gpt-parent", "effort": "high"}},
        _codex_usage(3, 100, 40, 10),
        _codex_usage(4, 150, 70, 25),
        {"type": "turn_context", "timestamp": 5, "payload": {"model": "gpt-child", "effort": "medium"}},
        {"type": "inter_agent_communication_metadata", "timestamp": 6, "payload": {}},
        _codex_usage(7, 160, 75, 30),
    ]
    _write_records(transcript, records)
    rows = [{"key": "child", "kind": "codex", "transcript": str(transcript)}]

    result = StatsCurrentTranscriptUsageScanner().scan(rows)

    assert [(item.atom.direction, item.atom.cache_role, item.atom.quantity, item.atom.model) for item in result.items] == [
        ("input", "none", 5, "gpt-child"),
        ("input", "read", 5, "gpt-child"),
        ("output", "none", 5, "gpt-child"),
    ]
    assert sum(item.atom.quantity for item in result.tombstones) == 175
    assert {item.atom.model for item in result.tombstones} == {"gpt-parent"}


def test_stats_scanner_version_migration_replays_an_old_eof_cache(tmp_path, monkeypatch):
    transcript = tmp_path / "rollout-old-cache.jsonl"
    records = [
        _codex_meta(
            "migration-thread",
            forked_from_id="parent-thread",
            thread_source="subagent",
        ),
        {
            "type": "event_msg",
            "timestamp": 2,
            "payload": {
                "type": "thread_settings_applied",
                "thread_settings": {
                    "model": "gpt-migrated",
                    "reasoning_effort": "high",
                    "service_tier": "default",
                },
            },
        },
        {"type": "response_item", "timestamp": 3, "payload": {"text": "x" * (70 * 1024)}},
        _codex_usage(4, 80, 30, 12),
        {"type": "turn_context", "timestamp": 5, "payload": {"model": "gpt-later", "effort": "low"}},
        {"type": "inter_agent_communication_metadata", "timestamp": 5.5, "payload": {}},
        _codex_usage(6, 90, 35, 15),
    ]
    _write_records(transcript, records)
    rows = [{"key": "migration", "kind": "codex", "transcript": str(transcript)}]
    current_version = session_files._STATS_CURRENT_TRANSCRIPT_SCAN_VERSION
    assert current_version >= 2

    monkeypatch.setattr(
        session_files,
        "_STATS_CURRENT_TRANSCRIPT_SCAN_VERSION",
        current_version - 1,
    )
    old_scanner = StatsCurrentTranscriptUsageScanner()
    old_result = old_scanner.scan(rows)
    old_record = session_files.stats_current_transcript_scan_record(transcript, "codex")
    old_cache_path = session_files.transcript_scan_store_path(old_record.identity)
    assert old_result.records_parsed == len(records)
    assert [(item.atom.quantity, item.atom.timestamp) for item in _output_items(old_result)] == [
        (3, 6),
    ]
    assert [(item.atom.quantity, item.atom.timestamp) for item in old_result.tombstones if item.atom.direction == "output"] == [
        (12, 4),
    ]
    assert old_record.state["offset"] == transcript.stat().st_size
    _commit(old_scanner, old_result)
    assert old_cache_path.exists()

    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    monkeypatch.setattr(
        session_files,
        "_STATS_CURRENT_TRANSCRIPT_SCAN_VERSION",
        current_version,
    )
    replay_scanner = StatsCurrentTranscriptUsageScanner()
    replayed = replay_scanner.scan(rows)
    new_record = session_files.stats_current_transcript_scan_record(transcript, "codex")
    new_cache_path = session_files.transcript_scan_store_path(new_record.identity)

    assert replayed.records_parsed == len(records)
    assert replayed.bytes_read == transcript.stat().st_size
    assert [(item.atom.quantity, item.atom.model, item.atom.model_evidence, item.atom.timestamp) for item in _output_items(replayed)] == [
        (3, "gpt-later", "turn_context.payload.model", 6),
    ]
    assert [(item.atom.quantity, item.atom.model, item.atom.model_evidence, item.atom.timestamp) for item in replayed.tombstones if item.atom.direction == "output"] == [
        (12, "gpt-migrated", "thread_settings_applied.thread_settings.model", 4),
    ]
    assert new_cache_path != old_cache_path
    _commit(replay_scanner, replayed)
    assert new_cache_path.exists()

    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
    third = StatsCurrentTranscriptUsageScanner().scan(rows)
    assert third.items == ()
    assert third.tombstones == ()
    assert third.records_parsed == 0


def test_codex_session_meta_seeds_model_without_guessing_from_prose(tmp_path):
    transcript = tmp_path / "rollout-meta.jsonl"
    _write_records(transcript, [
        _codex_meta("meta-thread", model="gpt-meta", effort="low"),
        {"type": "response_item", "timestamp": 2, "payload": {"text": "pretend model gpt-wrong"}},
        _codex_usage(3, 20, 5, 7),
    ])

    result = StatsCurrentTranscriptUsageScanner().scan([
        {"key": "meta", "kind": "codex", "transcript": str(transcript)},
    ])

    output = _output_items(result)
    assert [(item.atom.model, item.atom.effort, item.atom.model_evidence) for item in output] == [
        ("gpt-meta", "low", "session_meta.payload.model"),
    ]


def test_a_new_roster_transcript_is_scanned_before_legacy_repair_backlog(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions"
    live = sessions / "2026" / "07" / "16" / "rollout-live.jsonl"
    _write_records(live, [
        _codex_meta("live-thread", model="gpt-live"),
        {"type": "turn_context", "timestamp": 1, "payload": {"model": "gpt-live"}},
        _codex_usage(2, 10, 4, 2),
    ])
    historical = sessions / "2026" / "01" / "01" / "rollout-historical.jsonl"
    _write_records(historical, [
        _codex_meta("historical-child", "live-thread", forked_from_id="live-thread", thread_source="user"),
        {"type": "turn_context", "timestamp": 1, "payload": {"model": "gpt-old"}},
        _codex_usage(2, 20, 8, 4),
    ])
    orphans = []
    for index in range(6):
        orphan = sessions / "2026" / "01" / "01" / f"rollout-orphan-{index}.jsonl"
        _write_records(orphan, [
            _codex_meta(f"orphan-{index}", "live-thread", forked_from_id="live-thread", thread_source="subagent"),
            _codex_meta("live-thread", model="gpt-live"),
            _codex_usage(2, 10, 4, 2),
            {"type": "inter_agent_communication_metadata", "timestamp": 3, "payload": {}},
        ])
        orphans.append(orphan)

    def candidates(*, root: object = None, limit: int = 256):
        return [historical, live, *orphans] if limit >= 1 << 30 else [historical, live]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    scan = StatsCurrentTranscriptUsageScanner(max_records_per_scan=3).scan([
        {"key": "live", "kind": "codex", "transcript": str(live)},
    ])

    assert scan.budget_exhausted is True
    assert any(item.atom.model == "gpt-live" for item in scan.items)


def test_committed_status_is_readable_while_a_scan_holds_the_scanner_lock(tmp_path):
    transcript = tmp_path / "rollout-nonblocking.jsonl"
    _write_records(transcript, [_codex_meta("nonblocking-thread"), _codex_usage(2, 10, 0, 3)])
    rows = [{"key": "yo7220|0|codex", "kind": "codex", "transcript": str(transcript)}]
    now = [100.0]
    scanner = StatsCurrentTranscriptUsageScanner(clock=lambda: now[0])
    _commit(scanner, scanner.scan(rows))
    appended = _append_record(transcript, _codex_usage(3, 20, 0, 5))
    now[0] = 110.0
    _commit(scanner, scanner.scan(rows))
    committed = scanner.status()
    assert committed["committed_appended_bytes"] == len(appended.encode("utf-8"))

    _append_record(transcript, _codex_usage(4, 30, 0, 7))
    holding = threading.Event()
    release = threading.Event()
    ordered_sources = scanner._ordered_sources

    def parked(*args, **kwargs):
        holding.set()
        release.wait(timeout=5.0)
        return ordered_sources(*args, **kwargs)

    scanner._ordered_sources = parked
    scan_thread = threading.Thread(target=lambda: scanner.scan(rows), name="parked-scan")
    scan_thread.start()
    try:
        assert holding.wait(timeout=5.0)
        started = time.perf_counter()
        observed = scanner.status()
        elapsed = time.perf_counter() - started
    finally:
        release.set()
        scan_thread.join(timeout=10.0)

    assert not scan_thread.is_alive()
    assert elapsed < BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS / 5
    assert observed == committed
