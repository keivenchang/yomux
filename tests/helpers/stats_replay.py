# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared builders and projections for durable stats replay tests."""

from __future__ import annotations

from pathlib import Path

from yolomux_lib.stats_current import service as service_module
from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current.storage import Observation


def replay_service(
    tmp_path: Path,
    monotonic_now: list[float],
    wall_now: list[float],
) -> service_module.StatsCurrentService:
    return service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        monotonic=lambda: monotonic_now[0],
        clock=lambda: wall_now[0],
        randomizer=lambda: 0.0,
    )


def replay_observation(event_id: str, observed_at: float) -> Observation:
    return Observation(
        event_id=event_id,
        family="cpu",
        source_id="host",
        observed_at=observed_at,
        epoch_id="epoch-a",
        owner_generation=1,
        payload={"process_percent": 1, "system_percent": 2},
    )


def ring_slot_state(store_obj: storage.Store) -> dict[tuple[int, int], tuple[object, ...]]:
    """Every ring slot by ADDRESS, including the empty ones.

    Keyed by (resolution, slot_index) rather than by bucket, so a slot that was cleared is visible
    as a changed value instead of silently vanishing from the comparison.
    """
    return {
        (int(row[0]), int(row[1])): (
            row[2], row[3], int(row[4]), int(row[5]), int(row[6]), float(row[7]), int(row[8]),
        )
        for row in store_obj._connection().execute(
            "SELECT resolution_seconds, slot_index, bucket_start, bucket_json, complete, "
            "source_generation, ring_generation, published_at, payload_version "
            "FROM aggregate_ring_slots"
        )
    }
