# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused regressions for the statsd ring-invalidation storm fix."""

from __future__ import annotations

import json

import pytest

from yolomux_lib import session_files
from yolomux_lib.stats_current import CoverageEpoch
from yolomux_lib.stats_current import DATABASE_FILENAME
from yolomux_lib.stats_current import Observation
from yolomux_lib.stats_current import Store
from yolomux_lib.stats_current import UnavailableSpan
from yolomux_lib.stats_current import identity
from yolomux_lib.stats_current import scheduler
from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from tests.helpers.gate_stats import commit_scan
from tools.mockers.transcript import append_record
from tools.mockers.transcript import codex_meta
from tools.mockers.transcript import codex_usage
from tools.mockers.transcript import write_records


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


def _published_bucket(resolution_seconds: int, bucket_start: int) -> storage.RingBucketWrite:
    return storage.RingBucketWrite(
        resolution_seconds=resolution_seconds,
        bucket_start=bucket_start,
        bucket_json=json.dumps(
            {
                "series": {},
                "source": {
                    "first_timestamp": bucket_start,
                    "last_timestamp": bucket_start,
                    "count": 1,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        complete=True,
    )


def _clear_invalidations(store: Store) -> None:
    store._connection().execute("DELETE FROM ring_invalidations")
    store._connection().commit()


def _pending_invalidations(store: Store) -> set[tuple[int, int]]:
    return set(store._connection().execute(
        "SELECT resolution_seconds, bucket_start FROM ring_invalidations "
        "WHERE applied_at IS NULL"
    ).fetchall())


def _observation(event_id: str, observed_at: float) -> Observation:
    return Observation(
        event_id,
        "cpu",
        "host",
        observed_at,
        "ep-long",
        1,
        {"process_percent": 1.0, "system_percent": 2.0},
    )


def test_an_early_wake_inside_a_skipped_window_never_precedes_its_epoch_start():
    class Clock:
        monotonic_now = 0.0
        wall_now = 100.0

        def monotonic(self):
            return self.monotonic_now

        def wall(self):
            return self.wall_now + self.monotonic_now

    class Wake:
        def __init__(self, stop, clock):
            self.stop = stop
            self.clock = clock
            self.waits = 0

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 2:
                return True
            if self.waits == 3:
                self.clock.monotonic_now += timeout
                return False
            if self.waits > 3:
                self.stop.set()
            return False

        def clear(self):
            pass

    clock = Clock()
    attempts = []

    def collect(attempt):
        attempts.append((attempt, clock.wall()))
        if len(attempts) == 1:
            clock.monotonic_now = 10.0

    family_scheduler = scheduler.FamilyScheduler(
        (scheduler.CollectorJob("cpu", collect, lambda: 1.0, lambda _miss: None),),
        owner_generation=lambda: 7,
        wall_clock=clock.wall,
        monotonic=clock.monotonic,
    )
    family_scheduler._epochs["cpu"] = 1
    worker = scheduler._Worker(
        family_scheduler._workers["cpu"].job,
        Wake(family_scheduler._stop, clock),
    )

    family_scheduler._run_family(worker, 7, 1)

    assert len(attempts) >= 2
    assert all(
        attempt.epoch_started_at <= attempt.scheduled_at <= collected_at
        for attempt, collected_at in attempts
    )


def test_closing_an_open_epoch_invalidates_its_claim_and_retracted_tail(tmp_path):
    open_epoch = CoverageEpoch("cpu", "host", "epoch-1", 10.0, None, 1.0, 1)
    closed_epoch = CoverageEpoch("cpu", "host", "epoch-1", 10.0, 20.0, 1.0, 2)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_coverage_epoch(open_epoch)
        store.publish_ring_buckets(
            buckets=(_published_bucket(10, 10), _published_bucket(10, 20)),
            source_generation=1,
            published_at=20.0,
        )
        _clear_invalidations(store)

        store.append_coverage_epoch(closed_epoch)

        assert _pending_invalidations(store) == {(10, 10), (10, 20)}


def test_extending_a_long_epoch_invalidates_only_newly_claimed_buckets(tmp_path):
    epoch_start = 1_800_000_000.0
    epoch_end = epoch_start + 100_000.0
    first = CoverageEpoch("cpu", "host", "ep-long", epoch_start, epoch_end, 1.0, 1)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        seeded = store.append_batch(
            observations=(_observation("seed", epoch_end),),
            coverage_epochs=(first,),
        )
        store.publish_ring_buckets(
            buckets=[
                _published_bucket(resolution, int(instant // resolution) * resolution)
                for resolution in (1, 10, 60, 300)
                for instant in (epoch_start + 10.0, epoch_end - 10.0)
            ],
            source_generation=seeded.source_generation,
            published_at=epoch_end,
        )
        _clear_invalidations(store)

        store.append_batch(
            observations=(_observation("tick", epoch_end + 1.0),),
            coverage_epochs=(CoverageEpoch(
                "cpu", "host", "ep-long", epoch_start, epoch_end + 1.0, 1.0, 1,
            ),),
        )

        invalidated = _pending_invalidations(store)
    stale_edge = {
        (resolution, int((epoch_end - 10.0) // resolution) * resolution)
        for resolution in (1, 10, 60, 300)
        if int((epoch_end - 10.0) // resolution) * resolution + resolution > epoch_end
    }
    assert invalidated == stale_edge


def test_reoffering_an_unchanged_epoch_invalidates_no_old_bucket(tmp_path):
    epoch_start = 1_800_000_000.0
    epoch_end = epoch_start + 100_000.0
    epoch = CoverageEpoch("cpu", "host", "ep-long", epoch_start, epoch_end, 1.0, 1)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        seeded = store.append_batch(
            observations=(_observation("seed", epoch_end),),
            coverage_epochs=(epoch,),
        )
        store.publish_ring_buckets(
            buckets=[
                _published_bucket(resolution, int((epoch_start + 10.0) // resolution) * resolution)
                for resolution in (1, 10, 60, 300)
            ],
            source_generation=seeded.source_generation,
            published_at=epoch_end,
        )
        _clear_invalidations(store)

        store.append_batch(
            observations=(_observation("tick", epoch_end),),
            coverage_epochs=(epoch,),
        )

        pending = _pending_invalidations(store)
    assert all(
        bucket_start >= int((epoch_end - resolution) // resolution) * resolution
        for resolution, bucket_start in pending
    )


def test_owner_generation_change_invalidates_the_whole_epoch_extent(tmp_path):
    first = CoverageEpoch("cpu", "host", "ep-owner", 100.0, 120.0, 1.0, 1)
    advanced = CoverageEpoch("cpu", "host", "ep-owner", 100.0, 121.0, 1.0, 2)
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        seeded = store.append_batch(coverage_epochs=(first,))
        store.publish_ring_buckets(
            buckets=(_published_bucket(10, 100), _published_bucket(10, 120)),
            source_generation=seeded.source_generation,
            published_at=120.0,
        )
        _clear_invalidations(store)

        store.append_batch(coverage_epochs=(advanced,))

        assert _pending_invalidations(store) == {(10, 100), (10, 120)}


def test_reoffering_an_unchanged_unavailable_span_invalidates_nothing(tmp_path):
    span = UnavailableSpan(
        "cpu", "host", "gap-1", 100.0, 120.0, 1.0, "collector unavailable", 1,
    )
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        seeded = store.append_batch(unavailable_spans=(span,))
        store.publish_ring_buckets(
            buckets=(_published_bucket(10, 100), _published_bucket(10, 110)),
            source_generation=seeded.source_generation,
            published_at=120.0,
        )
        _clear_invalidations(store)

        duplicate = store.append_batch(unavailable_spans=(span,))

        assert duplicate.unavailable_spans_accepted == 0
        assert _pending_invalidations(store) == set()


def test_identity_control_scan_matches_original_predicate_for_50012_codepoints():
    for codepoint in range(50_012):
        value = f"prefix-{chr(codepoint)}-suffix"
        if codepoint < 32 or codepoint == 127:
            with pytest.raises(identity.IdentityValidationError, match="control characters"):
                identity.identity_text(value, "identity")
        else:
            assert identity.identity_text(value, "identity") == value


def test_identity_scan_preserves_utf8_limits_and_strip_behavior():
    assert identity.identity_text("  \u00e9  ", "identity", maximum_bytes=2, strip=True) == "\u00e9"
    with pytest.raises(identity.IdentityValidationError, match="exceeds 1 bytes"):
        identity.identity_text("\u00e9", "identity", maximum_bytes=1)
    assert identity.identity_text("prefix-~-\u0080-\U0010ffff", "identity") == "prefix-~-\u0080-\U0010ffff"


def test_new_codex_roster_transcript_precedes_legacy_repair_backlog(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions"
    live = sessions / "2026" / "07" / "16" / "rollout-live.jsonl"
    historical = sessions / "2026" / "01" / "01" / "rollout-historical.jsonl"
    write_records(live, [
        codex_meta("live-thread", model="gpt-live"),
        {"type": "turn_context", "timestamp": 1, "payload": {"model": "gpt-live"}},
        codex_usage(2, 10, 4, 2),
    ])
    write_records(historical, [
        codex_meta(
            "historical-child",
            "live-thread",
            forked_from_id="live-thread",
            thread_source="user",
        ),
        {"type": "turn_context", "timestamp": 1, "payload": {"model": "gpt-old"}},
        codex_usage(2, 20, 8, 4),
    ])
    orphans = []
    for index in range(6):
        orphan = sessions / "2026" / "01" / "01" / f"rollout-orphan-{index}.jsonl"
        write_records(orphan, [
            codex_meta(
                f"orphan-{index}",
                "live-thread",
                forked_from_id="live-thread",
                thread_source="subagent",
            ),
            codex_meta("live-thread", model="gpt-live"),
            codex_usage(2, 10, 4, 2),
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


def test_partially_consumed_codex_roster_tail_precedes_legacy_repair_backlog(
    tmp_path,
    monkeypatch,
):
    sessions = tmp_path / ".codex" / "sessions"
    live = sessions / "2026" / "07" / "16" / "rollout-live.jsonl"
    repair = sessions / "2026" / "01" / "01" / "rollout-repair.jsonl"
    write_records(live, [
        codex_meta("live-thread", model="gpt-live"),
        {"type": "turn_context", "timestamp": 1, "payload": {"model": "gpt-live"}},
        codex_usage(2, 10, 4, 2),
    ])
    write_records(repair, [
        codex_meta(
            "repair-child",
            "live-thread",
            forked_from_id="live-thread",
            thread_source="subagent",
        ),
        codex_meta("live-thread", model="gpt-live"),
        codex_usage(2, 20, 8, 4),
        {"type": "inter_agent_communication_metadata", "timestamp": 3, "payload": {}},
    ])

    def candidates(*, root: object = None, limit: int = 256):
        return [live, repair] if limit >= 1 << 30 else [live]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=2)
    rows = [{"key": "live", "kind": "codex", "transcript": str(live)}]

    first = scanner.scan(rows)
    commit_scan(scanner, first)
    live_record = scanner._files[str(live)]
    assert 0 < live_record.offset < live.stat().st_size
    assert "usage_committed_eof_size" not in live_record.durable_record.state
    assert scanner._next_source == str(repair)
    append_record(live, codex_usage(3, 30, 12, 6))

    second = scanner.scan(rows)

    assert any(item.atom.event_id == "codex:live-thread:3" for item in second.items)
    assert str(repair) not in scanner._files


def test_continuously_growing_codex_tail_cannot_starve_legacy_repair(
    tmp_path,
    monkeypatch,
):
    sessions = tmp_path / ".codex" / "sessions"
    live = sessions / "2026" / "07" / "16" / "rollout-live.jsonl"
    repair = sessions / "2026" / "01" / "01" / "rollout-repair.jsonl"
    write_records(live, [])
    write_records(repair, [
        codex_meta(
            "repair-child",
            "live-thread",
            forked_from_id="live-thread",
            thread_source="subagent",
        ),
        codex_meta("live-thread", model="gpt-live"),
        codex_usage(2, 20, 8, 4),
        {"type": "inter_agent_communication_metadata", "timestamp": 3, "payload": {}},
    ])

    def candidates(*, root: object = None, limit: int = 256):
        return [live, repair] if limit >= 1 << 30 else [live]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=1)
    rows = [{"key": "live", "kind": "codex", "transcript": str(live)}]
    live_records = [
        codex_meta("live-thread", model="gpt-live"),
        {"type": "turn_context", "timestamp": 1, "payload": {"model": "gpt-live"}},
        codex_usage(2, 10, 4, 2),
        codex_usage(3, 20, 8, 4),
        codex_usage(4, 30, 12, 6),
        codex_usage(5, 40, 16, 8),
    ]
    live_advances = []
    repair_advances = []
    previous_live_offset = 0
    previous_repair_offset = 0

    for record in live_records:
        append_record(live, record)
        result = scanner.scan(rows)
        commit_scan(scanner, result)
        live_offset = scanner._files[str(live)].offset
        repair_offset = (
            scanner._files[str(repair)].offset
            if str(repair) in scanner._files
            else 0
        )
        live_advances.append(live_offset > previous_live_offset)
        repair_advances.append(repair_offset > previous_repair_offset)
        previous_live_offset = live_offset
        previous_repair_offset = repair_offset

    assert live_advances[0] is True
    assert all(any(live_advances[index:index + 2]) for index in range(5))
    assert any(repair_advances[:3])
    assert str(repair) in scanner._files
    assert scanner._files[str(repair)].offset > 0


@pytest.mark.parametrize("repair_count", [1, 2, 4])
def test_continuous_live_growth_rotates_every_repair_source_within_tier_bound(
    tmp_path,
    monkeypatch,
    repair_count,
):
    sessions = tmp_path / ".codex" / "sessions"
    live = sessions / "2026" / "07" / "16" / "rollout-live.jsonl"
    write_records(live, [])
    repairs = []
    for index in range(repair_count):
        repair = sessions / "2026" / "01" / "01" / f"rollout-repair-{index}.jsonl"
        write_records(repair, [
            codex_meta(
                f"repair-child-{index}",
                "live-thread",
                forked_from_id="live-thread",
                thread_source="subagent",
            ),
            codex_meta("live-thread", model="gpt-live"),
        ])
        repairs.append(repair)

    def candidates(*, root: object = None, limit: int = 256):
        return [live, *repairs] if limit >= 1 << 30 else [live]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=1)
    rows = [{"key": "live", "kind": "codex", "transcript": str(live)}]

    for scan_index in range(3 * repair_count):
        record = (
            codex_meta("live-thread", model="gpt-live")
            if scan_index == 0
            else {
                "type": "response_item",
                "timestamp": scan_index + 1,
                "payload": {"text": "live"},
            }
        )
        append_record(live, record)
        result = scanner.scan(rows)
        commit_scan(scanner, result)

    assert all(
        str(repair) in scanner._files
        and scanner._files[str(repair)].offset > 0
        for repair in repairs
    )


def test_repair_cursor_precedes_partially_consumed_historical_backlog(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions"
    live = sessions / "2025" / "12" / "31" / "rollout-live.jsonl"
    historical = sessions / "2026" / "01" / "01" / "rollout-historical.jsonl"
    repair = sessions / "2026" / "02" / "01" / "rollout-repair.jsonl"
    write_records(live, [codex_meta("live", model="gpt-live")])
    write_records(historical, [
        codex_meta("historical", "live", forked_from_id="live", thread_source="user"),
        {"type": "turn_context", "timestamp": 1, "payload": {"model": "gpt-old"}},
        codex_usage(2, 20, 8, 4),
    ])
    write_records(repair, [
        codex_meta(
            "repair-child",
            "live",
            forked_from_id="live",
            thread_source="subagent",
        ),
        codex_meta("live", model="gpt-live"),
        codex_usage(2, 20, 8, 4),
        {"type": "inter_agent_communication_metadata", "timestamp": 3, "payload": {}},
    ])

    def candidates(*, root: object = None, limit: int = 256):
        return [live, historical, repair] if limit >= 1 << 30 else [live, historical]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    durable = session_files.stats_current_transcript_scan_record(live, "codex")
    with durable.lock:
        durable.state["offset"] = live.stat().st_size
        durable.state["size"] = live.stat().st_size
        durable.state["usage_committed_eof_size"] = live.stat().st_size
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=1)
    rows = [{"key": "live", "kind": "codex", "transcript": str(live)}]

    first = scanner.scan(rows)
    commit_scan(scanner, first)
    assert first.budget_exhausted is True
    assert scanner._next_source == str(repair)
    assert str(repair) not in scanner._files

    second = scanner.scan(rows)

    assert second.records_parsed == 1
    assert str(repair) in scanner._files
    assert scanner._files[str(repair)].offset > 0


def test_incomplete_new_codex_record_yields_to_committed_repair_cursor(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions"
    live = sessions / "2025" / "12" / "31" / "rollout-live.jsonl"
    repair = sessions / "2026" / "02" / "01" / "rollout-repair.jsonl"
    live.parent.mkdir(parents=True, exist_ok=True)
    incomplete = b'{"type":"session_meta"'
    live.write_bytes(incomplete)
    write_records(repair, [
        codex_meta(
            "repair-child",
            "live",
            forked_from_id="live",
            thread_source="subagent",
        ),
        codex_meta("live", model="gpt-live"),
        codex_usage(2, 20, 8, 4),
        {"type": "inter_agent_communication_metadata", "timestamp": 3, "payload": {}},
    ])

    def candidates(*, root: object = None, limit: int = 256):
        return [live, repair] if limit >= 1 << 30 else [live]

    monkeypatch.setattr(session_files, "recent_codex_transcript_candidates", candidates)
    scanner = StatsCurrentTranscriptUsageScanner(max_bytes_per_scan=len(incomplete))
    rows = [{"key": "live", "kind": "codex", "transcript": str(live)}]

    first = scanner.scan(rows)
    commit_scan(scanner, first)
    assert first.records_parsed == 0
    assert scanner._files[str(live)].offset == 0
    assert scanner._files[str(live)].observed_size == len(incomplete)
    assert scanner._next_source == str(repair)

    second = scanner.scan(rows)

    assert second.records_parsed > 0
    assert str(repair) in scanner._files
    assert scanner._files[str(repair)].offset > 0
