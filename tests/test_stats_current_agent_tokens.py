# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Regression tests for the statsd-owned agent token lifecycle."""

from types import SimpleNamespace

from yolomux_lib.stats_current import agent_tokens, opencode, storage


class _Scanner:
    def scan(self, _rows):
        return SimpleNamespace(items=(), tombstones=(), receipt_id=1, budget_exhausted=False)


def _attempt(started_at: float, scheduled_at: float) -> SimpleNamespace:
    return SimpleNamespace(
        family="agent_tokens",
        epoch_id="statsd:agent_tokens:pid:1234",
        epoch_started_at=started_at,
        scheduled_at=scheduled_at,
        cadence_seconds=10.0,
        owner_generation=7,
    )


def test_agent_token_epoch_start_is_stable_across_collection_ticks(tmp_path):
    database = tmp_path / storage.DATABASE_FILENAME
    cursors = opencode.OpenCodeCursorStore(tmp_path / "cursors.json")
    collector = agent_tokens.AgentTokenCollector(
        scanner=_Scanner(),
        cursors=cursors,
        database=database,
        inventory_provider=lambda: (opencode.OpenCodeProcessInventory((), ()), []),
        settings_provider=lambda: {},
        source_identity_provider=lambda: "pid:1234",
    )

    first = collector.collect(_attempt(100.0, 110.0))
    second = collector.collect(_attempt(200.0, 210.0))

    assert first.coverage_epochs[0].started_at == 100.0
    assert second.coverage_epochs[0].started_at == 100.0

    with storage.Store.open(database) as store:
        store.append_batch(coverage_epochs=first.coverage_epochs)
        result = store.append_batch(coverage_epochs=second.coverage_epochs)

    assert result.coverage_changed == 1
    assert result.coverage_unchanged == 0


def test_agent_token_unavailable_windows_advance_without_overlap(tmp_path):
    database = tmp_path / storage.DATABASE_FILENAME
    cursors = opencode.OpenCodeCursorStore(tmp_path / "cursors.json")
    collector = agent_tokens.AgentTokenCollector(
        scanner=_Scanner(),
        cursors=cursors,
        database=tmp_path / "opencode.db",
        coverage_database=database,
        inventory_provider=lambda: (opencode.OpenCodeProcessInventory((), ()), []),
        settings_provider=lambda: {},
        source_identity_provider=lambda: "pid:1234",
    )
    first = collector._unavailable(_attempt(100.0, 110.0), "agent-source", "agent", "missing")
    second = collector._unavailable(_attempt(100.0, 110.0), "agent-source", "agent", "missing")

    with storage.Store.open(database) as store:
        store.append_batch(unavailable_spans=first)
        result = store.append_batch(unavailable_spans=second)

    assert result.unavailable_spans_accepted == 1
    assert second[0].started_at == first[0].ended_at
