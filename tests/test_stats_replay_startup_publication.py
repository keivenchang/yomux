# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Startup repair publication boundaries for the durable stats ring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.stats_replay import replay_observation
from tests.helpers.stats_replay import replay_service
from tests.helpers.stats_replay import ring_slot_state
from yolomux_lib.stats_current import materializer
from yolomux_lib.stats_current import service as service_module
from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current.storage import Observation
from yolomux_lib.stats_current.storage import UsageAtom


CAPACITIES = storage.stats_resolution.RING_CAPACITIES


class _StartupRepairHarness:
    """Own the shared service clock, facts, and persisted-slot projections for this boundary."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.database = tmp_path / storage.DATABASE_FILENAME
        self.monotonic_now = [0.0]
        self.wall_now = [1_800_000_010.0]

    def service(self) -> service_module.StatsCurrentService:
        return replay_service(self.tmp_path, self.monotonic_now, self.wall_now)

    @staticmethod
    def observation(event_id: str, observed_at: float) -> Observation:
        return replay_observation(event_id, observed_at)

    @staticmethod
    def slot_state(store_obj: storage.Store) -> dict[tuple[int, int], tuple[object, ...]]:
        return ring_slot_state(store_obj)


@pytest.fixture
def startup_repair(tmp_path: Path) -> _StartupRepairHarness:
    return _StartupRepairHarness(tmp_path)


def test_startup_exact_slot_repair_does_not_promote_a_partial_resolution_view(
    startup_repair: _StartupRepairHarness,
):
    """The first snapshot after bounded repair must keep the coherent warm view."""
    harness = startup_repair
    aligned = int(harness.wall_now[0])
    aligned -= aligned % 10
    left_edge = aligned - 240

    initial = harness.service()
    with storage.Store.open(harness.database) as opened:
        opened.append_batch(
            observations=[harness.observation("left-edge", float(left_edge) + 0.5)],
            usage_atoms=[UsageAtom(
                "retained-cost",
                "input",
                "text",
                "none",
                "tokens",
                float(aligned) - 5.0,
                {
                    "quantity": 12,
                    "provider": "openai",
                    "model": "gpt",
                    "agent_id": "ring-writer",
                    "telemetry_complete": True,
                },
            )],
        )
        initial.writer = opened
        initial._build_once(opened, True, frozenset())
        harness.monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due() is not None

    # Cross the minute boundary and age only the 1-second owed cell out of its ring. The same
    # contradiction leaves its 60-second sibling rebuildable, so startup repairs an exact subset.
    harness.wall_now[0] = float(left_edge) + 301.1
    with storage.Store.open(harness.database) as offline:
        offline.append_batch(
            observations=[harness.observation("contradiction", float(left_edge) + 0.75)],
        )
        pending_cells = storage.pending_invalidation_cells(offline._connection())
        assert (60, left_edge - left_edge % 60) in {
            (int(row[0]), int(row[1])) for row in pending_cells
        }

    restarted = harness.service()
    with storage.Store.open(harness.database) as reopened:
        restarted.writer = reopened
        restarted._build_once(reopened, True, frozenset())
        request = {
            "range_seconds": 3_600,
            "resolution": 60,
            "client_id": "first-page",
        }
        metadata, binary = restarted._snapshot(request)
        assert metadata["ok"] is True, metadata
        snapshot = json.loads(binary)
        assert snapshot["cost_report"]["total_tokens"] == 12.0, snapshot

        public_generation = int(reopened._connection().execute(
            "SELECT ring_generation FROM aggregate_publication WHERE singleton = 1"
        ).fetchone()[0])
        reserved_generations = {
            int(slot[4])
            for slot in harness.slot_state(reopened).values()
            if slot[0] is not None and int(slot[4]) > public_generation
        }
        assert reserved_generations == {public_generation + 1}
        reserved_generation = next(iter(reserved_generations))

        # Crash after the exact repair but before the deferred coherent flush. A fresh process has
        # no warm owner yet, so its cold path consults SQLite directly. Coherence of that cold read
        # is owned by `read_ring_window`'s per-resolution cursor plus the `pair_unavailable` check
        # in `_read_ring_snapshot`, NOT by the publication singleton, so the repaired slots stay
        # readable. What must never happen is a fabricated zero: every range sharing the touched
        # resolution still answers with the proven historical cost.
        cold_after_crash = harness.service()
        with storage.Store.open(harness.database) as cold_reader:
            cold_after_crash.writer = cold_reader
            for range_seconds in (3_600, 2 * 60 * 60):
                cold_metadata, cold_binary = cold_after_crash._snapshot({
                    "range_seconds": range_seconds,
                    "resolution": 60,
                    "client_id": f"cold-after-crash-{range_seconds}",
                })
                assert cold_metadata["ok"] is True, cold_metadata
                cold_snapshot = json.loads(cold_binary)
                assert cold_snapshot["cost_report"]["total_tokens"] == 12.0, cold_snapshot

        # The next real host sample retains the historical cost bucket in memory. The deferred
        # ordinary flush must then replace the warm view with one coherent persisted cursor.
        cost_start = int((aligned - 5) // 60) * 60
        cost_slot = (60, storage.ring_slot_index(60, cost_start))
        cost_before = harness.slot_state(reopened)[cost_slot]
        assert cost_before[2] == 0 and cost_start + 60 <= restarted.started_at
        post_ready = reopened.append_batch(
            observations=[harness.observation("post-readiness-host", harness.wall_now[0] + 0.1)],
        )
        dirty = frozenset(restarted._append_dirty_cells(post_ready))
        restarted._build_once(reopened, False, dirty)

        assert restarted._next_ring_flush_at is not None
        harness.monotonic_now[0] = restarted._next_ring_flush_at
        publication = restarted._flush_ring_if_due(reopened)
        assert publication is not None
        converged_public_generation = int(reopened._connection().execute(
            "SELECT ring_generation FROM aggregate_publication WHERE singleton = 1"
        ).fetchone()[0])
        converged_metadata, converged_binary = restarted._snapshot(request)
        ring_state = restarted._ring_views[(3_600, 60, None)]
        cost_after = harness.slot_state(reopened)[cost_slot]

    assert converged_metadata["ok"] is True, converged_metadata
    converged = json.loads(converged_binary)
    assert converged["cost_report"]["total_tokens"] == 12.0, converged
    assert publication.ring_generation == reserved_generation == converged_public_generation
    assert cost_after[2] == 1 and cost_after[5] > cost_before[5], (
        "ordinary restart flush did not publish the proven historical cost bucket"
    )
    assert ring_state.persisted is True
    assert restarted._entry_cursor(ring_state.snapshot) == restarted._ring_published_cursors[60]


@pytest.mark.parametrize("convergence", ("sample", "restart"))
def test_all_owed_startup_repair_keeps_the_warm_owner_until_ring_convergence(
    startup_repair: _StartupRepairHarness,
    monkeypatch,
    convergence,
):
    """An all-owed repair stays nonpublic until an ordinary sample or restart converges it."""
    harness = startup_repair
    observed_at = harness.wall_now[0] - 5.0
    initial = harness.service()

    with storage.Store.open(harness.database) as opened:
        opened.append_batch(usage_atoms=[UsageAtom(
            "all-owed-cost",
            "input",
            "text",
            "none",
            "tokens",
            observed_at,
            {
                "quantity": 12,
                "provider": "openai",
                "model": "gpt",
                "agent_id": "all-owed",
                "telemetry_complete": True,
            },
        )])
        initial.writer = opened
        initial._build_once(opened, True, frozenset())
        harness.monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert initial._flush_ring_if_due(opened) is not None
        opened.append_batch(observations=[harness.observation("all-owed-late", observed_at)])
        owed_rows = storage.pending_invalidation_cells(opened._connection())
        assert {row[0] for row in owed_rows} == set(CAPACITIES), owed_rows

    restarted = harness.service()
    request = {
        "range_seconds": 3_600,
        "resolution": 60,
        "client_id": "all-owed-warm",
    }
    with storage.Store.open(harness.database) as reopened:
        restarted.writer = reopened
        startup_repair = restarted._repair_startup_owed_slots
        monkeypatch.setattr(restarted, "_repair_startup_owed_slots", lambda _publisher: None)
        restarted._build_once(reopened, True, frozenset())
        monkeypatch.setattr(restarted, "_repair_startup_owed_slots", startup_repair)

        owed_rows = storage.pending_invalidation_cells(reopened._connection())
        owed_cells = frozenset(
            materializer.DirtyCell(resolution_seconds, bucket_start)
            for resolution_seconds, bucket_start, _generation in owed_rows
        )
        assert owed_cells
        with restarted.work_lock:
            restarted._pending_ring_dirty = set(owed_cells)
            restarted._ring_source_generation = max(row[2] for row in owed_rows)
            restarted._next_ring_flush_at = harness.monotonic_now[0]
        public_before = int(reopened._connection().execute(
            "SELECT ring_generation FROM aggregate_publication WHERE singleton = 1"
        ).fetchone()[0])

        repaired = restarted.repair_pending_ring_slots(reopened)

        assert repaired is not None
        assert repaired.buckets_updated == len(owed_cells)
        assert restarted._pending_ring_dirty == set()
        assert restarted._next_ring_flush_at is None
        assert storage.pending_invalidation_cells(reopened._connection()) == ()
        public_after_repair = int(reopened._connection().execute(
            "SELECT ring_generation FROM aggregate_publication WHERE singleton = 1"
        ).fetchone()[0])
        assert public_after_repair == public_before
        reserved_generation = public_before + 1
        assert repaired.ring_generation == reserved_generation
        assert {
            harness.slot_state(reopened)[(
                cell.resolution,
                storage.ring_slot_index(cell.resolution, cell.start),
            )][4]
            for cell in owed_cells
        } == {reserved_generation}

        warm_metadata, warm_binary = restarted._snapshot(request)
        assert warm_metadata["ok"] is True, warm_metadata
        assert json.loads(warm_binary)["cost_report"]["total_tokens"] == 12.0
        warm_entry = restarted._cache.entries[(3_600, 60, None)]
        assert warm_metadata["cache_generation"] == warm_entry.metadata["cache_generation"]

        if convergence == "sample":
            post_ready = reopened.append_batch(
                observations=[harness.observation("all-owed-next-sample", harness.wall_now[0] + 0.1)],
            )
            dirty = frozenset(restarted._append_dirty_cells(post_ready))
            restarted._build_once(reopened, False, dirty)
            assert restarted._next_ring_flush_at is not None
            harness.monotonic_now[0] = restarted._next_ring_flush_at
            publication = restarted._flush_ring_if_due(reopened)
            converged_service = restarted

    if convergence == "restart":
        converged_service = harness.service()
        with storage.Store.open(harness.database) as reopened:
            converged_service.writer = reopened
            converged_service._build_once(reopened, True, frozenset())
            assert converged_service._next_ring_flush_at is not None
            harness.monotonic_now[0] = converged_service._next_ring_flush_at
            publication = converged_service._flush_ring_if_due(reopened)

    assert publication is not None
    assert publication.ring_generation == reserved_generation
    with storage.Store.open(harness.database) as final:
        assert int(final._connection().execute(
            "SELECT ring_generation FROM aggregate_publication WHERE singleton = 1"
        ).fetchone()[0]) == reserved_generation
        assert storage.pending_invalidation_cells(final._connection()) == ()
        # A promoted view is deliberately re-read from SQLite rather than retained in memory, and
        # `_clear_ring_views_locked` keeps the published cursor as a freshness floor when that read
        # fails. The converged owner therefore needs a live handle, not the closed one it published
        # through, or it correctly answers pending instead of serving a pre-floor warm entry.
        converged_service.writer = final
        final_metadata, final_binary = converged_service._snapshot(request)
    assert final_metadata["ok"] is True, final_metadata
    assert json.loads(final_binary)["cost_report"]["total_tokens"] == 12.0
