# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The durable invalidation ledger: what records work, and what is allowed to retire it.

A ledger that records the wrong bucket, or retires a row for the wrong reason, is worse than no
ledger: it reports the ring reconciled while a contradicted aggregate is still being served. Every
case here is about one of those two failures.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from yolomux_lib.stats_current import materializer
from yolomux_lib.stats_current import service as service_module
from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current.storage import CoverageEpoch
from yolomux_lib.stats_current.storage import Observation
from yolomux_lib.stats_current.storage import UnavailableSpan
from yolomux_lib.stats_current.storage import UsageAtom
from yolomux_lib.stats_current.storage import UsageAtomTombstone

RESOLUTION = 60
RANGE_SECONDS = 3_600


@pytest.fixture
def store(tmp_path):
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as opened:
        yield opened


def _pending(store_obj: storage.Store) -> set[tuple[int, int]]:
    return {
        (int(row[0]), int(row[1]))
        for row in store_obj._connection().execute(
            "SELECT resolution_seconds, bucket_start FROM ring_invalidations WHERE applied_at IS NULL"
        )
    }


def _pending_rows(store_obj: storage.Store) -> set[tuple[int, int, int]]:
    """Including the generation, because the ledger is keyed by it.

    Comparing only (resolution, bucket) hides a NEW invalidation for a bucket that already had one
    from an earlier mutation -- which is exactly the tombstone case, since the atom's own append
    invalidated the same bucket one generation earlier.
    """
    return {
        (int(row[0]), int(row[1]), int(row[2]))
        for row in store_obj._connection().execute(
            "SELECT resolution_seconds, bucket_start, source_generation FROM ring_invalidations "
            "WHERE applied_at IS NULL"
        )
    }


def _pending_generations(store_obj: storage.Store) -> set[int]:
    return {
        int(row[0])
        for row in store_obj._connection().execute(
            "SELECT source_generation FROM ring_invalidations WHERE applied_at IS NULL"
        )
    }


def _cursor(store_obj: storage.Store) -> dict[int, tuple[float, int]]:
    return {
        int(row[0]): (float(row[1]), int(row[2]))
        for row in store_obj._connection().execute(
            "SELECT resolution_seconds, folded_through_observed_at, folded_source_generation "
            "FROM ring_replay_cursor"
        )
    }


def _observation(event_id: str, observed_at: float) -> Observation:
    return Observation(
        event_id=event_id, family="cpu", source_id="host", observed_at=observed_at,
        epoch_id="epoch-a", owner_generation=1,
        payload={"process_percent": 1, "system_percent": 2},
    )


def _usage(event_id: str, observed_at: float, thread_id: str = "t1") -> UsageAtom:
    return UsageAtom(
        event_id=event_id, direction="input", modality="text", cache_role="none", unit="tokens",
        observed_at=observed_at,
        payload={
            "quantity": 5.0, "provider": "openai", "model": "gpt", "thread_id": thread_id,
            "execution_source": "codex",
        },
    )


def _tombstone(event_id: str, observed_at: float, thread_id: str = "t1") -> UsageAtomTombstone:
    return UsageAtomTombstone(
        event_id=event_id, direction="input", modality="text", cache_role="none", unit="tokens",
        observed_at=observed_at, quantity=5.0, provider="openai", model="gpt", thread_id=thread_id,
    )


def _bucket(bucket_start: int, value: int = 1) -> storage.RingBucketWrite:
    return storage.RingBucketWrite(
        resolution_seconds=RESOLUTION, bucket_start=bucket_start,
        bucket_json=json.dumps({
            "series": {"v": {"value": value, "source_count": 1,
                             "first_timestamp": bucket_start, "last_timestamp": bucket_start}},
            "source": {"first_timestamp": bucket_start, "last_timestamp": bucket_start, "count": 1},
        }, sort_keys=True, separators=(",", ":")),
        complete=True,
    )



def _publish_for(store_obj: storage.Store, *instants: float, generation: int = 0) -> None:
    """Publish a real slot for every bucket these instants touch, at the 60s resolution.

    An invalidation is only recorded when a POPULATED slot contradicts the facts, because a bucket
    the ring does not hold already reads as missing and no publication would ever retire a row for
    it. So a test asserting that a mutation records work has to establish the published slot first;
    without it the fixture was asserting behaviour the product deliberately no longer has.
    """
    store_obj.initialize_ring_storage()
    buckets = sorted({int(max(0.0, i) // RESOLUTION) * RESOLUTION for i in instants})
    store_obj.publish_ring_buckets(
        buckets=[_bucket(b) for b in buckets], source_generation=generation, published_at=1.0,
    )


# --- P0-1: tombstone-only append -----------------------------------------------------------

def test_a_tombstone_only_append_invalidates_the_deleted_atoms_bucket(store):
    """The bucket that must be rebuilt is the one the DELETED atom was in.

    The first implementation read the last field of the prepared tombstone tuple, which is
    `thread_id`, not a timestamp. It was filtered out by an isinstance check, so a tombstone-only
    append recorded NOTHING and the contradicted bucket kept serving its pre-deletion total.
    """
    observed_at = 7_000.0
    _publish_for(store, observed_at)
    store.append_batch(usage_atoms=[_usage("codex:t1:e1", observed_at)])
    baseline = _pending_rows(store)

    store.append_batch(usage_tombstones=[_tombstone("codex:t1:e1", observed_at)])

    recorded = {(r, b) for r, b, _g in (_pending_rows(store) - baseline)}
    assert recorded, "a tombstone-only append recorded no invalidation at all"
    expected_bucket = int(observed_at // RESOLUTION) * RESOLUTION
    assert (RESOLUTION, expected_bucket) in recorded, (
        f"the deleted atom's bucket {expected_bucket} was not invalidated; got {sorted(recorded)}"
    )


def test_a_tombstone_never_invalidates_a_bucket_derived_from_a_non_timestamp_field(store):
    """Red-capable control: a thread id must never be interpretable as an observed instant."""
    observed_at = 7_000.0
    _publish_for(store, observed_at)
    store.append_batch(usage_atoms=[_usage("codex:99:e1", observed_at, thread_id="99")])
    baseline = _pending_rows(store)

    store.append_batch(usage_tombstones=[_tombstone("codex:99:e1", observed_at, thread_id="99")])

    recorded = {(r, b) for r, b, _g in (_pending_rows(store) - baseline)}
    # A numeric-looking thread id would land in bucket 0 if it were ever used as a timestamp.
    assert (RESOLUTION, 0) not in recorded, "a thread id was used as an observed instant"
    assert (RESOLUTION, int(observed_at // RESOLUTION) * RESOLUTION) in recorded


# --- P0-2: prune ---------------------------------------------------------------------------

def test_an_explicit_prune_invalidates_the_range_it_actually_deleted(store):
    """`Store.prune` advanced the generation and recorded no invalidation whatsoever."""
    old = 1_000.0
    _publish_for(store, old)
    store.append_batch(observations=[_observation("e-old", old)])
    baseline = _pending_rows(store)

    result = store.prune(now=old + storage.RETENTION_SECONDS + RESOLUTION)

    assert result.observations_deleted == 1, "the fixture did not actually prune anything"
    recorded = {(r, b) for r, b, _g in (_pending_rows(store) - baseline)}
    assert recorded, "an explicit prune deleted facts and invalidated no bucket"
    assert (RESOLUTION, int(old // RESOLUTION) * RESOLUTION) in recorded


def test_append_time_retention_prune_invalidates_the_deleted_range_not_the_offered_rows(store):
    """The pruned range is old; the offered rows are new. Recording the offered range is wrong."""
    old = 1_000.0
    _publish_for(store, old)
    store.append_batch(observations=[_observation("e-old", old)])
    baseline = _pending_rows(store)
    fresh = old + storage.RETENTION_SECONDS + 10 * RESOLUTION

    store.append_batch(
        observations=[_observation("e-new", fresh)],
        retention_now=fresh,
    )

    recorded = {(r, b) for r, b, _g in (_pending_rows(store) - baseline)}
    assert (RESOLUTION, int(old // RESOLUTION) * RESOLUTION) in recorded, (
        "the retention prune deleted an old fact without invalidating its bucket"
    )


# --- coverage and unavailable ---------------------------------------------------------------

def test_a_coverage_or_unavailable_change_invalidates_its_span(store):
    _publish_for(store, *[float(b) for b in range(6_000, 6_960, 60)])
    baseline = _pending(store)

    store.append_batch(
        coverage_epochs=[CoverageEpoch("cpu", "host", "epoch-c", 6_000.0, 6_300.0, 1.0, 1)],
        unavailable_spans=[UnavailableSpan("cpu", "host", "epoch-u", 6_600.0, 6_900.0, 1.0, "down", 1)],
    )

    recorded = {b for r, b in (_pending(store) - baseline) if r == RESOLUTION}
    # EXACT half-open membership. Endpoint flattening recorded only {6000, 6300, 6600, 6900},
    # leaving every interior bucket falsely clean and marking two exclusive ends the change never
    # touched.
    assert recorded == {
        6_000, 6_060, 6_120, 6_180, 6_240,   # coverage  [6000, 6300)
        6_600, 6_660, 6_720, 6_780, 6_840,   # unavailable [6600, 6900)
    }, sorted(recorded)


# --- P0-3: retirement authority -------------------------------------------------------------

def test_a_publication_built_before_newer_facts_cannot_retire_their_invalidation(store):
    """Wall clock is not ordering authority; the source generation is.

    The real race: the materializer builds from a generation-N snapshot, facts then land and raise
    an invalidation at N+1, and only afterwards does the N publication reach the store with a LATER
    wall clock. Retiring by `created_at <= published_at` cleared that N+1 row, so a bucket the
    publication demonstrably cannot account for was marked reconciled and served.

    Publishing a strictly older generation is separately refused by the store
    (`source_generation cannot move backward`), which is why this uses an equal generation -- the
    narrowest form the store actually permits, and therefore the one that had to be caught here.
    """
    store.initialize_ring_storage()
    bucket_start = 7_140
    # Populate the slot FIRST: with nothing published there is no contradiction to record, so the
    # race this row exists to catch could not be set up at all.
    store.publish_ring_buckets(buckets=[_bucket(bucket_start)], source_generation=0, published_at=1.0)
    store.append_batch(observations=[_observation("e1", float(bucket_start) + 1.0)])
    first_generation = max(_pending_generations(store))
    store.publish_ring_buckets(
        buckets=[_bucket(bucket_start)], source_generation=first_generation, published_at=100.0,
    )
    assert (RESOLUTION, bucket_start) not in _pending(store)

    # Newer facts contradict the bucket again, at a newer generation.
    store.append_batch(observations=[_observation("e2", float(bucket_start) + 2.0)])
    newer_generation = max(_pending_generations(store))
    assert newer_generation > first_generation

    # The in-flight publication from the OLDER snapshot finally lands, with a later clock.
    store.publish_ring_buckets(
        buckets=[_bucket(bucket_start)], source_generation=first_generation, published_at=9_999_999.0,
    )

    assert (RESOLUTION, bucket_start) in _pending(store), (
        "a publication built before the contradicting facts retired their invalidation"
    )


# --- P0-4: cursor advancement ---------------------------------------------------------------

def test_same_horizon_publication_at_a_newer_generation_advances_the_cursor(store):
    """The cursor tracks folded GENERATION as well as horizon.

    Advancing only when the horizon moves left `folded_source_generation` stale for a republication
    of the same bucket at a newer generation -- which is exactly what a replay of contradicted work
    looks like.
    """
    store.initialize_ring_storage()
    bucket_start = 7_140
    store.publish_ring_buckets(
        buckets=[_bucket(bucket_start, value=1)], source_generation=3, published_at=100.0,
    )
    first = _cursor(store)[RESOLUTION]

    store.publish_ring_buckets(
        buckets=[_bucket(bucket_start, value=2)], source_generation=9, published_at=101.0,
    )
    second = _cursor(store)[RESOLUTION]

    assert second[0] == first[0], "the horizon did not move, and should not have"
    assert second[1] == 9, (
        f"folded_source_generation stayed {second[1]} after a newer-generation republication"
    )


def test_the_store_refuses_a_backward_publication_generation_outright(store):
    """Why the cursor cannot regress: the store rejects the publication before the cursor is asked.

    Recorded as its own row so the cursor rule above is not mistaken for last-writer-wins. The
    monotonicity is enforced upstream, and this fails loudly if that ever stops being true.
    """
    store.initialize_ring_storage()
    store.publish_ring_buckets(buckets=[_bucket(7_140)], source_generation=9, published_at=100.0)

    with pytest.raises(storage.StorageValidationError):
        store.publish_ring_buckets(buckets=[_bucket(7_140)], source_generation=3, published_at=101.0)

    assert _cursor(store)[RESOLUTION][1] == 9


# --- already-resolved properties that must not regress ---------------------------------------

def test_a_pending_invalidation_keeps_its_bucket_missing_from_a_served_window(store):
    store.initialize_ring_storage()
    bucket_start = 7_140
    store.publish_ring_buckets(
        buckets=[_bucket(bucket_start)], source_generation=0, published_at=100.0,
    )
    store.append_batch(observations=[_observation("e1", float(bucket_start) + 1.0)])

    window = store.read_ring_window(
        range_seconds=RANGE_SECONDS, resolution_seconds=RESOLUTION, window_end=7_200,
    )

    assert bucket_start in window.missing_bucket_starts
    assert all(row.bucket_start != bucket_start for row in window.rows)


def test_a_wrong_payload_version_keeps_its_bucket_missing(store):
    store.initialize_ring_storage()
    bucket_start = 7_140
    store.publish_ring_buckets(
        buckets=[_bucket(bucket_start)], source_generation=0, published_at=100.0,
    )
    connection = store._connection()
    connection.execute(
        "UPDATE aggregate_ring_slots SET payload_version = 99 WHERE bucket_start = ?",
        (bucket_start,),
    )
    connection.commit()

    window = store.read_ring_window(
        range_seconds=RANGE_SECONDS, resolution_seconds=RESOLUTION, window_end=7_200,
    )

    assert bucket_start in window.missing_bucket_starts


# --- P0-5: the ledger is the durable restart work owner ---------------------------------------

def test_a_restart_retains_an_invalidated_cell_even_when_its_slot_looks_persisted(tmp_path):
    """Exercised through `_restart_ring_cells` itself, not through the storage window.

    An earlier version of this row asserted only that `read_ring_window` exposed the pending
    bucket. That is a storage fact: disabling the service-side retention left it green, so it could
    not detect the defect it was written for. The filter is what drops work, so the filter is what
    has to be called.

    `_restart_ring_cells` exists to stop a restart's first build from synthesizing downtime as
    quiet zero, and it does that by DROPPING cells whose slots are already persisted. A bucket with
    an unapplied invalidation is persisted AND wrong, so dropping it left the contradicted payload
    in place with nothing left to rebuild it.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", database,
        monotonic=lambda: monotonic_now[0], clock=lambda: wall_now[0], randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None

        candidate = service._cache.generation
        layer = next(item for item in candidate.layers if item.resolution == RESOLUTION)
        target = next(iter(sorted(bucket.start for bucket in layer.buckets)[-3:]))
        opened.append_batch(observations=[_observation("late", float(target) + 1.0)])
        assert (RESOLUTION, target) in _pending(opened), "the fixture recorded no invalidation"

        # A fresh restart: no publications yet in THIS process.
        service._ring_publications = 0
        cell = next(
            item for item in (
                materializer.DirtyCell(RESOLUTION, target),
            )
        )
        retained = service._restart_ring_cells(opened, candidate, frozenset({cell}))

    assert cell in retained, (
        "the restart filter dropped a cell the ledger had marked contradicted"
    )


def test_a_restart_still_drops_a_historically_open_cell(tmp_path):
    """Negative control: retaining everything would defeat the filter's original purpose.

    The filter's drop case is a cell whose persisted slot is INCOMPLETE and whose window closed
    before this process started -- a bucket that was open when the previous writer died. Rebuilding
    it would synthesize the downtime as quiet zero, which is the regression `_restart_ring_cells`
    exists to prevent. (A clean COMPLETE slot is deliberately retained, so it is not the control.)
    """
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", database,
        monotonic=lambda: monotonic_now[0], clock=lambda: wall_now[0], randomizer=lambda: 0.0,
    )
    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        candidate = service._cache.generation
        layer = next(item for item in candidate.layers if item.resolution == RESOLUTION)
        historical = sorted(bucket.start for bucket in layer.buckets)[0]
        connection = opened._connection()
        connection.execute(
            "UPDATE aggregate_ring_slots SET complete = 0 "
            "WHERE resolution_seconds = ? AND bucket_start = ?",
            (RESOLUTION, historical),
        )
        connection.commit()
        assert not _pending(opened), "this control needs a store with no pending work"

        service._ring_publications = 0
        cell = materializer.DirtyCell(RESOLUTION, historical)
        retained = service._restart_ring_cells(opened, candidate, frozenset({cell}))

    assert cell not in retained, (
        "the restart filter retained a historically open cell; downtime will read as quiet zero"
    )


# --- bounded invalidation cardinality ---------------------------------------------------------

RING_SLOT_TOTAL = sum(storage.stats_resolution.RING_CAPACITIES.values())


@pytest.mark.parametrize("cutoff", (100_000.0, 1_700_000_000.0, 1_787_200_000.0))
def test_a_prune_range_is_bounded_by_the_ring_horizon_not_by_the_unix_epoch(cutoff):
    """The production OOM: `(0, cutoff)` walked from 1970.

    Measured before the clamp, at a current cutoff: 2,001,470,467 pairs across the four
    resolutions, roughly 134 GiB, materialized in ONE list inside the mutating transaction. Every
    pair past the horizon named a ring slot that cannot exist and could never be retired, so the
    cost bought nothing. Parametrized across three decades of cutoff because the defect scaled with
    the clock: a test at a single recent instant would not show that the bound is epoch-independent.
    """
    pairs = storage.invalidated_buckets((0.0, cutoff))

    assert len(pairs) <= RING_SLOT_TOTAL, (
        f"a prune at cutoff {cutoff} produced {len(pairs):,} pairs against a {RING_SLOT_TOTAL} "
        f"slot ceiling; the epoch walk is back"
    )
    assert len(pairs) == len(set(pairs)), "the bounded range emitted duplicates"


def test_the_bound_holds_for_every_configured_resolution_independently():
    """Per resolution, never more pairs than that ring has slots."""
    pairs = storage.invalidated_buckets((0.0, 1_787_200_000.0))
    per_resolution: dict[int, int] = {}
    for resolution_seconds, _bucket_start in pairs:
        per_resolution[resolution_seconds] = per_resolution.get(resolution_seconds, 0) + 1

    assert set(per_resolution) == set(storage.stats_resolution.RING_CAPACITIES)
    for resolution_seconds, count in per_resolution.items():
        assert count <= storage.stats_resolution.RING_CAPACITIES[resolution_seconds]


def test_an_ordinary_short_range_is_unchanged_by_the_clamp():
    """Negative control: clamping must not silently drop buckets a real mutation touches.

    A bound that returned nothing would satisfy every assertion above and destroy the ledger.
    """
    start = 1_787_200_000.0
    pairs = storage.invalidated_buckets((start, start + 120.0))

    by_resolution = {r: [b for r2, b in pairs if r2 == r] for r, _ in pairs}
    assert by_resolution[60], "a two-minute mutation invalidated no 60s bucket"
    assert len(by_resolution[60]) >= 2, "a two-minute span must cover at least two 60s buckets"
    assert all(len(v) >= 1 for v in by_resolution.values())


def test_an_explicit_prune_still_records_the_recent_buckets_it_deleted(store):
    """End-to-end control: the clamp must not stop a prune recording real work."""
    old = 1_787_000_000.0
    _publish_for(store, old)
    store.append_batch(observations=[_observation("e-old", old)])
    baseline = _pending_rows(store)

    result = store.prune(now=old + storage.RETENTION_SECONDS + RESOLUTION)

    assert result.observations_deleted == 1
    recorded = _pending_rows(store) - baseline
    assert recorded, "the clamped prune recorded nothing at all"
    assert len(recorded) <= RING_SLOT_TOTAL


# --- exact boundaries, exact membership, every resolution -------------------------------------
# The earlier cardinality rows asserted only "<= 1248", which a set that is bounded AND WRONG
# satisfies. These assert the exact bucket set instead.

CAPACITIES = storage.stats_resolution.RING_CAPACITIES


@pytest.mark.parametrize("resolution_seconds", sorted(CAPACITIES))
def test_an_aligned_prune_cutoff_yields_exactly_the_affected_window(resolution_seconds):
    """For aligned cutoff C: exactly [C-Nr .. C-r]. Not C, and not missing C-Nr.

    Measured before: an aligned C=60000 at r=60 produced [31260 .. 60000] -- it INCLUDED bucket C,
    which holds only facts at or after C and so lost nothing, and OMITTED C-Nr at the far end.
    Wrong at both ends by exactly one bucket.
    """
    slot_count = CAPACITIES[resolution_seconds]
    cutoff = resolution_seconds * slot_count * 4
    expected = {
        cutoff - index * resolution_seconds for index in range(1, slot_count + 1)
    }

    actual = {
        bucket for res, bucket in storage.invalidated_buckets((0.0, float(cutoff)), end_exclusive=True)
        if res == resolution_seconds
    }

    assert actual == expected
    assert cutoff not in actual, "bucket C lost no facts and must not be invalidated"
    assert cutoff - slot_count * resolution_seconds in actual, "the far horizon bucket was omitted"


@pytest.mark.parametrize("resolution_seconds", sorted(CAPACITIES))
def test_an_inside_bucket_cutoff_includes_the_bucket_that_lost_facts(resolution_seconds):
    """A cutoff partway through a bucket DID delete facts from it, so it must be invalidated."""
    slot_count = CAPACITIES[resolution_seconds]
    base = resolution_seconds * slot_count * 4
    cutoff = base + resolution_seconds // 2 if resolution_seconds > 1 else base + 0.5

    actual = {
        bucket for res, bucket in storage.invalidated_buckets((0.0, float(cutoff)), end_exclusive=True)
        if res == resolution_seconds
    }

    assert base in actual, "the partially pruned bucket was not invalidated"
    assert base + resolution_seconds not in actual


@pytest.mark.parametrize("resolution_seconds", sorted(CAPACITIES))
def test_sparse_old_plus_far_future_instants_keep_the_old_bucket(resolution_seconds):
    """The `max(mutated)` horizon defect, at every resolution.

    One old fact and one far-future fact in the same batch: the span-plus-clamp anchored on the
    newest instant and dropped the old contradicted bucket entirely.
    """
    old_instant = 1_000.0
    future_instant = 1_900_000_000.0

    pairs = storage.invalidated_buckets_for_instants([old_instant, future_instant])

    at_resolution = {bucket for res, bucket in pairs if res == resolution_seconds}
    assert int(old_instant // resolution_seconds) * resolution_seconds in at_resolution
    assert int(future_instant // resolution_seconds) * resolution_seconds in at_resolution
    # Exactly the two touched buckets, not the span between them.
    assert len(at_resolution) == 2


def test_a_two_point_batch_does_not_invalidate_the_span_between_its_ends():
    """Negative control: exactness must not become over-invalidation either."""
    pairs = storage.invalidated_buckets_for_instants([6_000.0, 6_600.0])

    at_60 = sorted(bucket for res, bucket in pairs if res == 60)
    assert at_60 == [6_000, 6_600], f"the batch invalidated the span between its ends: {at_60}"


def test_the_instant_owner_is_bounded_by_its_input_not_by_the_clock():
    """Bounded by `len(instants) * resolutions`, and independent of how old the clock is."""
    for instant in (1_000.0, 1_700_000_000.0, 1_900_000_000.0):
        assert len(storage.invalidated_buckets_for_instants([instant])) == len(CAPACITIES)


# --- existing-v8 CHECK upgrade -----------------------------------------------------------------
# The relaxed constraint was new-database-only: `_validate_ring_schema` compares columns, rows and
# triggers, never table SQL, so a v8 created by the first schema-8 build still rejects correct
# cross-clock retirement today.

RETIRED_CHECK_SQL = (
    "CREATE TABLE ring_invalidations ("
    "resolution_seconds INTEGER NOT NULL, "
    "bucket_start INTEGER NOT NULL CHECK (bucket_start >= 0), "
    "source_generation INTEGER NOT NULL CHECK (source_generation >= 0), "
    "reason TEXT NOT NULL, "
    "created_at REAL NOT NULL CHECK (created_at >= 0), "
    "applied_at REAL, "
    "PRIMARY KEY (resolution_seconds, bucket_start, source_generation), "
    "FOREIGN KEY (resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), "
    "CHECK (bucket_start % resolution_seconds = 0), "
    "CHECK (applied_at IS NULL OR applied_at >= created_at)"
    ") WITHOUT ROWID"
)


def _make_old_v8(path: Path) -> None:
    """A v8 exactly as the first schema-8 build wrote it, carrying the retired CHECK and rows."""
    with storage.Store.open(path) as opened:
        connection = opened._connection()
        connection.execute("DROP INDEX IF EXISTS ring_invalidations_pending")
        connection.execute("DROP TABLE ring_invalidations")
        connection.execute(RETIRED_CHECK_SQL)
        connection.execute(
            "CREATE INDEX ring_invalidations_pending "
            "ON ring_invalidations(resolution_seconds, bucket_start) WHERE applied_at IS NULL"
        )
        # A pending row whose observed `created_at` is far ahead of any wall clock, plus a
        # retired one, so the rebuild is proven to preserve both states.
        connection.execute(
            "INSERT INTO ring_invalidations(resolution_seconds, bucket_start, source_generation, "
            "reason, created_at, applied_at) VALUES(60, 7140, 3, 'fact_mutation', 1787200000.0, NULL)"
        )
        connection.execute(
            "INSERT INTO ring_invalidations(resolution_seconds, bucket_start, source_generation, "
            "reason, created_at, applied_at) VALUES(60, 7080, 2, 'fact_mutation', 5.0, 9.0)"
        )
        connection.commit()


def _table_sql(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ring_invalidations'"
        ).fetchone()[0])
    finally:
        connection.close()


def test_an_existing_v8_with_the_retired_check_is_upgraded_on_open(tmp_path):
    """Reopening a pre-fix v8 must drop the cross-clock CHECK without losing a row."""
    database = tmp_path / storage.DATABASE_FILENAME
    _make_old_v8(database)
    assert "applied_at >= created_at" in _table_sql(database), "fixture did not reproduce the defect"

    with storage.Store.open(database) as opened:
        rows = sorted(opened._connection().execute(
            "SELECT resolution_seconds, bucket_start, source_generation, reason, created_at, applied_at "
            "FROM ring_invalidations"
        ))

    assert "applied_at >= created_at" not in _table_sql(database)
    assert rows == [
        (60, 7080, 2, "fact_mutation", 5.0, 9.0),
        (60, 7140, 3, "fact_mutation", 1787200000.0, None),
    ], "the rebuild lost or altered a ledger row"


def test_a_pre_fix_v8_then_accepts_the_cross_clock_retirement_it_used_to_reject(tmp_path):
    """The behaviour the upgrade exists for, not just the DDL text."""
    database = tmp_path / storage.DATABASE_FILENAME
    _make_old_v8(database)

    with storage.Store.open(database) as opened:
        # `applied_at` (wall clock) far BELOW `created_at` (observed instant) is the case the
        # retired constraint rejected.
        opened._connection().execute(
            "UPDATE ring_invalidations SET applied_at = 100.0 "
            "WHERE bucket_start = 7140 AND applied_at IS NULL"
        )
        opened._connection().commit()
        retired = opened._connection().execute(
            "SELECT applied_at FROM ring_invalidations WHERE bucket_start = 7140"
        ).fetchone()[0]

    assert retired == 100.0


def test_the_upgrade_is_idempotent_across_repeated_opens(tmp_path):
    """Startup runs on every boot; a rebuild on each one would churn the ledger forever."""
    database = tmp_path / storage.DATABASE_FILENAME
    _make_old_v8(database)
    with storage.Store.open(database):
        pass
    first_sql = _table_sql(database)

    with storage.Store.open(database):
        pass
    with storage.Store.open(database):
        pass

    assert _table_sql(database) == first_sql
    connection = sqlite3.connect(database)
    try:
        assert int(connection.execute("SELECT count(*) FROM ring_invalidations").fetchone()[0]) == 2
    finally:
        connection.close()


def test_a_fresh_v8_is_not_rebuilt_because_it_never_had_the_retired_check(tmp_path):
    """Negative control: the upgrade must be gated on the defect, not run unconditionally."""
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database):
        pass

    assert "applied_at >= created_at" not in _table_sql(database)
    assert not storage._ring_invalidations_needs_check_upgrade(sqlite3.connect(database))


def test_a_sparse_batch_through_append_records_the_old_bucket_not_just_the_future_one(store):
    """End-to-end through `append_batch`, because the defect was in the WIRING.

    Asserting `invalidated_buckets_for_instants` directly proves the helper is exact and proves
    nothing about which helper `append_batch` calls. Reverting the caller to the span-plus-clamp
    left those helper rows green -- the same gap as testing a filter's inputs instead of the filter.
    """
    old_instant = 1_000.0
    future_instant = 1_900_000_000.0
    _publish_for(store, old_instant, future_instant)
    baseline = _pending_rows(store)

    store.append_batch(observations=[
        _observation("sparse-old", old_instant),
        _observation("sparse-future", future_instant),
    ])

    recorded = {(r, b) for r, b, _g in (_pending_rows(store) - baseline)}
    old_bucket = int(old_instant // RESOLUTION) * RESOLUTION
    future_bucket = int(future_instant // RESOLUTION) * RESOLUTION
    assert (RESOLUTION, old_bucket) in recorded, (
        "the old contradicted bucket was dropped; the batch was collapsed to a clamped span"
    )
    assert (RESOLUTION, future_bucket) in recorded
    # Exactly the two touched buckets at the one resolution whose slots were published. The other
    # resolutions hold no slot for these buckets, so a row there could never be retired -- recording
    # it is the accumulation this slice removes, not coverage this assertion should demand.
    assert recorded == {(RESOLUTION, old_bucket), (RESOLUTION, future_bucket)}, (
        f"recorded {sorted(recorded)} for a two-fact batch"
    )


# --- actionability: every durable row must name a slot that can still be rebuilt ---------------
# The clamp bounded each prune to at most 1,248 rows but recorded them whether or not a slot
# existed. Measured on a COLD store with zero populated slots and no publication: 1,252 permanently
# pending rows per cycle, 3,756 after three, none retirable by any publication.


def _ledger(store_obj: storage.Store) -> set[tuple[int, int, int, float | None]]:
    return {
        (int(r[0]), int(r[1]), int(r[2]), None if r[3] is None else float(r[3]))
        for r in store_obj._connection().execute(
            "SELECT resolution_seconds, bucket_start, source_generation, applied_at "
            "FROM ring_invalidations"
        )
    }


def _prune_cycle(store_obj: storage.Store, event: str, instant: float) -> None:
    store_obj.append_batch(observations=[_observation(event, instant)])
    store_obj.prune(now=instant + storage.RETENTION_SECONDS + RESOLUTION)


def test_repeated_prune_on_a_cold_store_accumulates_nothing(store):
    """The measured leak: 1,252 rows per cycle on a store with no populated slot at all."""
    for cycle in range(5):
        _prune_cycle(store, f"cold-{cycle}", 1_787_000_000.0 + cycle * 3_600)

    assert _ledger(store) == set(), (
        f"a cold store accumulated {len(_ledger(store))} unactionable rows"
    )


def test_repeated_prune_across_generations_does_not_leak_per_generation(store):
    """Generation advance must not turn one bucket into a new permanent row each cycle."""
    instant = 1_787_000_000.0
    _publish_for(store, instant)
    for cycle in range(5):
        _prune_cycle(store, f"gen-{cycle}", instant + cycle)

    pending = {(r, b) for r, b, _g, applied in _ledger(store) if applied is None}
    assert len(pending) <= 1, f"per-generation leak: {sorted(pending)}"


def test_a_populated_slot_still_records_its_contradiction(store):
    """Negative control: actionability must not become 'record nothing'."""
    instant = 7_000.0
    _publish_for(store, instant)
    baseline = _pending_rows(store)

    store.append_batch(observations=[_observation("real", instant)])

    recorded = {(r, b) for r, b, _g in (_pending_rows(store) - baseline)}
    assert (RESOLUTION, int(instant // RESOLUTION) * RESOLUTION) in recorded


def test_a_wrapped_around_slot_stops_being_actionable_and_is_removed(store):
    """Ring wraparound: the lapped slot holds a DIFFERENT bucket, so the old one is truly gone.

    Matching on `bucket_start` rather than slot index is what makes this correct; a slot-index match
    would have called the lapped slot a live contradiction forever.
    """
    slot_count = CAPACITIES[RESOLUTION]
    original = RESOLUTION * slot_count * 4
    _publish_for(store, float(original))
    store.append_batch(observations=[_observation("contradiction", float(original) + 1.0)])
    assert (RESOLUTION, original) in _pending(store)

    # One full lap later the same slot index holds a different bucket.
    lapped = original + slot_count * RESOLUTION
    store.publish_ring_buckets(
        buckets=[_bucket(lapped)], source_generation=99, published_at=200.0,
    )
    store.prune(now=float(lapped) + storage.RETENTION_SECONDS + RESOLUTION)

    assert (RESOLUTION, original) not in _pending(store), (
        "a pending row survived the wraparound that destroyed its slot"
    )


def test_an_absent_publication_records_nothing_and_an_empty_ring_stays_empty(store):
    """Cold slot and absent publication, asserted as exact emptiness rather than a ceiling."""
    store.initialize_ring_storage()
    store.append_batch(observations=[_observation("no-slot", 7_000.0)])

    assert _ledger(store) == set()


def test_an_already_pending_row_is_not_duplicated_by_a_second_mutation(store):
    """Repeated prune within one generation must not re-add the same row."""
    instant = 7_000.0
    _publish_for(store, instant)
    store.append_batch(observations=[_observation("first", instant)])
    after_first = _ledger(store)

    store.prune(now=instant + storage.RETENTION_SECONDS + RESOLUTION)
    store.prune(now=instant + storage.RETENTION_SECONDS + 2 * RESOLUTION)

    pending_now = {row for row in _ledger(store) if row[3] is None}
    buckets = [(r, b) for r, b, _g, _a in pending_now]
    assert len(buckets) == len(set(buckets)), (
        f"a bucket carries more than one pending row: {sorted(pending_now)}"
    )
    assert len(pending_now) <= len(after_first)


def test_an_already_retired_row_is_never_resurrected_or_deleted(store):
    """A retired row is history; the sweep only removes PENDING rows."""
    instant = 7_000.0
    _publish_for(store, instant)
    store.append_batch(observations=[_observation("e", instant)])
    generation = max(_pending_generations(store))
    store.publish_ring_buckets(
        buckets=[_bucket(int(instant // RESOLUTION) * RESOLUTION)],
        source_generation=generation, published_at=500.0,
    )
    retired = {row for row in _ledger(store) if row[3] is not None}
    assert retired, "fixture retired nothing"

    store.prune(now=instant + storage.RETENTION_SECONDS + RESOLUTION)

    assert retired <= _ledger(store), "the sweep deleted an already-retired row"


# --- end-to-end close/reopen/rebuild/republish -------------------------------------------------

def test_a_contradiction_survives_close_and_reopen_then_retires_only_on_republication(tmp_path):
    """The durable path, across real close/reopen boundaries rather than one live handle.

    Reopen at every boundary must yield either the still-actionable pending row or the rebuilt
    publication, never a falsely clean stale ring.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    bucket_start = 7_140

    with storage.Store.open(database) as opened:
        _publish_for(opened, float(bucket_start))
        opened.append_batch(observations=[_observation("late", float(bucket_start) + 1.0)])
        generation = max(_pending_generations(opened))
        assert (RESOLUTION, bucket_start) in _pending(opened)

    with storage.Store.open(database) as reopened:
        # Survived the close: still pending, and still hidden from a served window.
        assert (RESOLUTION, bucket_start) in _pending(reopened)
        window = reopened.read_ring_window(
            range_seconds=RANGE_SECONDS, resolution_seconds=RESOLUTION, window_end=7_200,
        )
        assert bucket_start in window.missing_bucket_starts
        assert bucket_start in window.pending_invalidations
        # Rebuild and republish at the contradicting generation.
        reopened.publish_ring_buckets(
            buckets=[_bucket(bucket_start, value=7)], source_generation=generation, published_at=900.0,
        )
        assert (RESOLUTION, bucket_start) not in _pending(reopened)

    with storage.Store.open(database) as final:
        # The replacement survives the second close; the row stays retired, not resurrected.
        window = final.read_ring_window(
            range_seconds=RANGE_SECONDS, resolution_seconds=RESOLUTION, window_end=7_200,
        )
        assert bucket_start not in window.missing_bucket_starts
        served = [row for row in window.rows if row.bucket_start == bucket_start]
        assert served and json.loads(served[0].bucket_json)["series"]["v"]["value"] == 7
        assert (RESOLUTION, bucket_start) not in _pending(final)


def test_a_coverage_change_uses_the_same_owner_and_survives_reopen(tmp_path):
    """Coverage/unavailable share the invalidation owner, so they get the same durable path."""
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database) as opened:
        _publish_for(opened, *[float(b) for b in range(6_000, 6_360, 60)])
        opened.append_batch(
            coverage_epochs=[CoverageEpoch("cpu", "host", "epoch-c", 6_000.0, 6_300.0, 1.0, 1)],
        )
        expected = {(RESOLUTION, b) for b in (6_000, 6_060, 6_120, 6_180, 6_240)}
        assert {p for p in _pending(opened) if p[0] == RESOLUTION} == expected
        assert (RESOLUTION, 6_300) not in _pending(opened), "the exclusive end was marked"

    with storage.Store.open(database) as reopened:
        assert expected <= _pending(reopened)


# --- forced concurrency, deterministic ---------------------------------------------------------

@pytest.mark.parametrize("resolution_seconds", sorted(CAPACITIES))
def test_a_mutation_landing_between_build_and_publish_is_not_retired_by_it(tmp_path, resolution_seconds):
    """Deterministic interleaving, not a sleep or a retry.

    The materializer reads a generation-N snapshot, the mutation commits at N+1, and only then does
    the N publication land. The immediate transition is asserted -- the row must still be pending --
    and then convergence after reopen, rather than only a final snapshot.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    slot_count = CAPACITIES[resolution_seconds]
    bucket_start = resolution_seconds * slot_count * 4

    with storage.Store.open(database) as opened:
        opened.initialize_ring_storage()
        opened.publish_ring_buckets(
            buckets=[storage.RingBucketWrite(
                resolution_seconds=resolution_seconds, bucket_start=bucket_start,
                bucket_json=_bucket(0).bucket_json, complete=True,
            )],
            source_generation=0, published_at=1.0,
        )
        # BARRIER: the publication's snapshot generation is captured here, before the mutation.
        snapshot_generation = 0

        # +0.5, not +1.0: at the 1s resolution a whole second lands in the NEXT bucket, so the
        # mutation would contradict a bucket that was never published and record nothing.
        opened.append_batch(observations=[_observation("mid", float(bucket_start) + 0.5)])
        contradicting = max(_pending_generations(opened))
        assert contradicting > snapshot_generation

        # The in-flight publication from the older snapshot now lands.
        opened.publish_ring_buckets(
            buckets=[storage.RingBucketWrite(
                resolution_seconds=resolution_seconds, bucket_start=bucket_start,
                bucket_json=_bucket(0).bucket_json, complete=True,
            )],
            source_generation=snapshot_generation, published_at=9_999.0,
        )

        assert (resolution_seconds, bucket_start) in _pending(opened), (
            "a publication built before the mutation retired its invalidation"
        )

    with storage.Store.open(database) as reopened:
        assert (resolution_seconds, bucket_start) in _pending(reopened), (
            "the pending state did not converge across reopen"
        )


def test_a_mutation_to_an_unheld_bucket_records_nothing_even_when_other_slots_are_populated(store):
    """Actionability matches the exact BUCKET, not merely 'this resolution has some slot'.

    A resolution-only match would call every mutation actionable as soon as any bucket at that
    resolution were published, reintroducing the accumulation for buckets the ring does not hold.
    The unactionable sweep would then hide it, which is why this asserts at the RECORDING step
    rather than at the end state.
    """
    held = 7_140
    unheld = 60_000
    _publish_for(store, float(held))
    baseline = _pending_rows(store)

    store.append_batch(observations=[_observation("elsewhere", float(unheld) + 1.0)])

    recorded = {(r, b) for r, b, _g in (_pending_rows(store) - baseline)}
    assert (RESOLUTION, unheld) not in recorded, (
        "a bucket the ring does not hold was recorded because another slot at its resolution was"
    )
    assert recorded == set()


# --- exact interval membership, every resolution -----------------------------------------------

@pytest.mark.parametrize("resolution_seconds", sorted(CAPACITIES))
def test_a_coverage_interval_marks_every_interior_bucket_and_no_exclusive_end(tmp_path, resolution_seconds):
    """Endpoint flattening left the interior falsely clean and marked an end it never touched."""
    database = tmp_path / storage.DATABASE_FILENAME
    span_buckets = 5
    start = resolution_seconds * CAPACITIES[resolution_seconds] * 4
    end = start + span_buckets * resolution_seconds
    published = [start + index * resolution_seconds for index in range(span_buckets + 2)]

    with storage.Store.open(database) as opened:
        opened.initialize_ring_storage()
        opened.publish_ring_buckets(
            buckets=[
                storage.RingBucketWrite(
                    resolution_seconds=resolution_seconds, bucket_start=bucket,
                    bucket_json=_bucket(0).bucket_json, complete=True,
                )
                for bucket in published
            ],
            source_generation=0, published_at=1.0,
        )
        opened.append_batch(coverage_epochs=[
            CoverageEpoch("cpu", "host", "epoch-x", float(start), float(end), 1.0, 1),
        ])
        recorded = {b for r, b in _pending(opened) if r == resolution_seconds}

    expected = {start + index * resolution_seconds for index in range(span_buckets)}
    assert recorded == expected, f"interval [{start},{end}) -> {sorted(recorded)}"
    assert end not in recorded, "the exclusive end bucket was marked"


def test_an_open_coverage_epoch_marks_from_its_start_without_enumerating(tmp_path):
    """`ended_at is None` runs to the present; it must not collapse to a single start bucket."""
    database = tmp_path / storage.DATABASE_FILENAME
    start = 6_000
    with storage.Store.open(database) as opened:
        opened.initialize_ring_storage()
        opened.publish_ring_buckets(
            buckets=[_bucket(b) for b in range(5_940, 6_300, RESOLUTION)],
            source_generation=0, published_at=1.0,
        )
        opened.append_batch(coverage_epochs=[
            CoverageEpoch("cpu", "host", "epoch-open", float(start), None, 1.0, 1),
        ])
        recorded = {b for r, b in _pending(opened) if r == RESOLUTION}

    assert recorded == {6_000, 6_060, 6_120, 6_180, 6_240}
    assert 5_940 not in recorded, "a bucket entirely before the open epoch was marked"


# --- production-epoch exclusive end -------------------------------------------------------------

@pytest.mark.parametrize("cutoff", (60_000, 1_700_000_000, 1_787_200_000))
@pytest.mark.parametrize("resolution_seconds", sorted(CAPACITIES))
def test_the_exclusive_cutoff_bucket_is_excluded_at_every_scale(cutoff, resolution_seconds):
    """`end - 1e-9` is below the float ULP at production epochs and did not move the value at all.

    Measured before: at cutoff 1_700_000_000 with r=1 the excluded cutoff bucket was included.
    """
    aligned = cutoff - (cutoff % resolution_seconds)
    buckets = {
        b for r, b in storage.invalidated_buckets((0.0, float(aligned)), end_exclusive=True)
        if r == resolution_seconds
    }

    assert aligned not in buckets, f"aligned cutoff {aligned} at r={resolution_seconds} was included"
    assert max(buckets) == aligned - resolution_seconds

    inside = aligned + resolution_seconds // 2 if resolution_seconds > 1 else aligned + 0.5
    inside_buckets = {
        b for r, b in storage.invalidated_buckets((0.0, float(inside)), end_exclusive=True)
        if r == resolution_seconds
    }
    assert aligned in inside_buckets, "a partially pruned bucket was excluded"


# --- overwrite cleanup without a prune ----------------------------------------------------------

def test_a_full_lap_overwrite_retires_the_displaced_row_without_any_prune(store):
    """Cleanup ran only inside prune, so a no-op prune left the row forever."""
    slot_count = CAPACITIES[RESOLUTION]
    original = RESOLUTION * slot_count * 4
    _publish_for(store, float(original))
    store.append_batch(observations=[_observation("c", float(original) + 0.5)])
    assert (RESOLUTION, original) in _pending(store)

    store.publish_ring_buckets(
        buckets=[_bucket(original + slot_count * RESOLUTION)],
        source_generation=99, published_at=200.0,
    )

    assert (RESOLUTION, original) not in _pending(store), (
        "the displaced bucket's row survived the overwrite that destroyed its slot"
    )


def test_an_overwrite_does_not_erase_a_contradiction_for_the_newly_published_bucket(store):
    """Only the DISPLACED bucket is cleaned; the new bucket's own row is generation-gated."""
    slot_count = CAPACITIES[RESOLUTION]
    original = RESOLUTION * slot_count * 4
    lapped = original + slot_count * RESOLUTION
    _publish_for(store, float(original))
    store.publish_ring_buckets(
        buckets=[_bucket(lapped)], source_generation=1, published_at=10.0,
    )
    store.append_batch(observations=[_observation("new", float(lapped) + 0.5)])
    store.append_batch(observations=[_observation("newer", float(lapped) + 1.5)])
    contradiction = max(_pending_generations(store))
    assert (RESOLUTION, lapped) in _pending(store)
    assert contradiction > 1, "the contradiction must be NEWER than the republication generation"

    # Rewriting the SAME bucket from an OLDER snapshot must not silently drop a contradiction it
    # cannot answer. A republication AT the contradicting generation legitimately does retire it --
    # that is the accepted generation-gated behaviour, not this case.
    store.publish_ring_buckets(
        buckets=[_bucket(lapped, value=5)], source_generation=1, published_at=11.0,
    )

    assert (RESOLUTION, lapped) in _pending(store), (
        "rewriting a bucket erased its own unanswered contradiction"
    )


# --- the REAL rebuild/republication caller ------------------------------------------------------
# `publish_ring_buckets` with a synthetic bucket is the storage primitive, not the caller
# production uses. These drive `_build_once` + `_flush_ring_if_due`, the path statsd actually runs.


def _real_service(tmp_path, monotonic_now, wall_now):
    return service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        monotonic=lambda: monotonic_now[0], clock=lambda: wall_now[0], randomizer=lambda: 0.0,
    )


def test_the_real_rebuild_caller_publishes_and_retires_across_close_and_reopen(tmp_path):
    """Build and publish through the production owner, from stored facts, across real reopens."""
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = _real_service(tmp_path, monotonic_now, wall_now)

    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        published = service._flush_ring_if_due()
        assert published is not None, "the real caller published nothing to contradict"
        slot = opened._connection().execute(
            "SELECT resolution_seconds, bucket_start FROM aggregate_ring_slots "
            "WHERE bucket_json IS NOT NULL ORDER BY resolution_seconds, bucket_start LIMIT 1"
        ).fetchone()
        assert slot is not None
        resolution_seconds, bucket_start = int(slot[0]), int(slot[1])
        opened.append_batch(observations=[_observation("late", float(bucket_start) + 0.5)])
        assert (resolution_seconds, bucket_start) in _pending(opened)

    with storage.Store.open(database) as reopened:
        # Survived a real close: still pending, still hidden from the served window.
        assert (resolution_seconds, bucket_start) in _pending(reopened)
        service.writer = reopened
        service._ring_publications = 0
        monotonic_now[0] += service_module.RING_FLUSH_SECONDS
        wall_now[0] += service_module.RING_FLUSH_SECONDS
        service._build_once(reopened, True, frozenset())
        # The build owner repairs owed slots before readiness, so the contradiction is
        # answered by the build itself rather than by a later cadence flush.
        assert (resolution_seconds, bucket_start) not in _pending(reopened), (
            "the real rebuild published but the contradiction was not retired"
        )

    with storage.Store.open(database) as final:
        assert (resolution_seconds, bucket_start) not in _pending(final)


# --- real transaction overlap, two handles, barriers --------------------------------------------

@pytest.mark.parametrize("resolution_seconds", sorted(CAPACITIES))
def test_two_independent_writers_overlap_a_mutation_and_a_stale_publication(tmp_path, resolution_seconds):
    """TWO writable handles whose transactions genuinely overlap, driven by barriers.

    The previous version of this row used ONE writer for both the mutation and the publication and
    only opened readers between steps: sequential, and therefore no evidence about what happens
    when the two transactions actually contend. Here writer A holds an open write transaction
    containing the contradicting fact while writer B attempts its stale publication, so the
    serialization owner is exercised rather than assumed.

    Schedule:
      T0  B publishes the bucket at generation 0 and closes its transaction.
      T1  A BEGINs and appends the contradicting fact.            [barrier: a_appended]
      T2  B, on a SECOND connection, attempts its stale publish.  [barrier: b_attempted]
      T3  A commits.                                              [barrier: a_committed]
      T4  B retries to completion, then both converge.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    slot_count = CAPACITIES[resolution_seconds]
    bucket_start = resolution_seconds * slot_count * 4
    payload = storage.RingBucketWrite(
        resolution_seconds=resolution_seconds, bucket_start=bucket_start,
        bucket_json=_bucket(0).bucket_json, complete=True,
    )
    a_appended = threading.Event()
    b_attempted = threading.Event()
    a_committed = threading.Event()
    b_error: list[BaseException] = []

    with storage.Store.open(database) as writer_b:
        writer_b.initialize_ring_storage()
        writer_b.publish_ring_buckets(buckets=[payload], source_generation=0, published_at=1.0)

        def writer_a() -> None:
            # A genuinely independent writable handle on the same database file.
            with storage.Store.open(database) as handle:
                handle.append_batch(
                    observations=[_observation("mid", float(bucket_start) + 0.5)],
                )
                a_appended.set()
                # Hold here so B's attempt overlaps A's completed-but-observed state.
                b_attempted.wait(10)
                a_committed.set()

        thread = threading.Thread(target=writer_a, name="replay-writer-a")
        thread.start()
        try:
            assert a_appended.wait(10), "writer A never committed its mutation"
            try:
                # B's stale publication, built from generation 0, lands while A is still open.
                writer_b.publish_ring_buckets(
                    buckets=[payload], source_generation=0, published_at=9_999.0,
                )
            except BaseException as error:  # pragma: no cover - recorded, then asserted below
                b_error.append(error)
            b_attempted.set()
            assert a_committed.wait(10)
        finally:
            thread.join(timeout=10)
            assert not thread.is_alive(), "writer A did not finish"

        assert not b_error, f"the stale publication failed unexpectedly: {b_error}"
        assert (resolution_seconds, bucket_start) in _pending(writer_b), (
            "a stale-generation publication cleared a newer contradiction under real contention"
        )

    with storage.Store.open(database) as reopened:
        assert (resolution_seconds, bucket_start) in _pending(reopened), (
            "pending state did not converge across reopen"
        )



# --- the REAL rebuild/republication caller ------------------------------------------------------
# `publish_ring_buckets` with a synthetic bucket is the storage primitive, not the caller
# production uses. These drive `_build_once` + `_flush_ring_if_due`, the path statsd actually runs.


def _real_service(tmp_path, monotonic_now, wall_now):
    return service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME,
        monotonic=lambda: monotonic_now[0], clock=lambda: wall_now[0], randomizer=lambda: 0.0,
    )


def test_the_real_rebuild_caller_publishes_and_retires_across_close_and_reopen(tmp_path):
    """Build and publish through the production owner, from stored facts, across real reopens."""
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = _real_service(tmp_path, monotonic_now, wall_now)

    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        published = service._flush_ring_if_due()
        assert published is not None, "the real caller published nothing to contradict"
        slot = opened._connection().execute(
            "SELECT resolution_seconds, bucket_start FROM aggregate_ring_slots "
            "WHERE bucket_json IS NOT NULL ORDER BY resolution_seconds, bucket_start LIMIT 1"
        ).fetchone()
        assert slot is not None
        resolution_seconds, bucket_start = int(slot[0]), int(slot[1])
        opened.append_batch(observations=[_observation("late", float(bucket_start) + 0.5)])
        assert (resolution_seconds, bucket_start) in _pending(opened)

    with storage.Store.open(database) as reopened:
        # Survived a real close: still pending, still hidden from the served window.
        assert (resolution_seconds, bucket_start) in _pending(reopened)
        service.writer = reopened
        service._ring_publications = 0
        monotonic_now[0] += service_module.RING_FLUSH_SECONDS
        wall_now[0] += service_module.RING_FLUSH_SECONDS
        service._build_once(reopened, True, frozenset())
        assert (resolution_seconds, bucket_start) not in _pending(reopened), (
            "the real rebuild published but the contradiction was not retired"
        )

    with storage.Store.open(database) as final:
        assert (resolution_seconds, bucket_start) not in _pending(final)


def _slot_state(store_obj: storage.Store) -> dict[tuple[int, int], tuple[object, ...]]:
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


def _owed_cell_after_restart(tmp_path, monotonic_now, wall_now, *, oldest: bool):
    """Publish through the real caller, contradict one published bucket, and hand back its address.

    `oldest` selects the left edge of the 1-second ring, which the advancing wall clock carries out
    of the materializer window; the newest bucket stays inside it. Same setup for both, so the two
    outcomes differ only by where the cell sits relative to that window.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    service = _real_service(tmp_path, monotonic_now, wall_now)
    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None, "the real caller published nothing"
        order = "ASC" if oldest else "DESC"
        slot = opened._connection().execute(
            "SELECT resolution_seconds, bucket_start FROM aggregate_ring_slots "
            "WHERE bucket_json IS NOT NULL AND resolution_seconds = 1 "
            f"ORDER BY bucket_start {order} LIMIT 1"
        ).fetchone()
        assert slot is not None
        resolution_seconds, bucket_start = int(slot[0]), int(slot[1])
        opened.append_batch(observations=[_observation("late", float(bucket_start) + 0.5)])
        assert (resolution_seconds, bucket_start) in _pending(opened)
    return service, database, resolution_seconds, bucket_start


def test_out_of_window_owed_cell_becomes_an_honest_gap_and_retires_exactly(tmp_path):
    """The cell no generation can rebuild: cleared and retired together, nothing else touched.

    FORCED RED before the fix: the assertions below measured the contradicted slot still POPULATED
    and its ledger row still PENDING after a restart repair -- a bucket permanently hidden by
    `read_ring_window` and permanently backed by a payload the store's own facts disagree with.
    """
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service, database, resolution_seconds, bucket_start = _owed_cell_after_restart(
        tmp_path, monotonic_now, wall_now, oldest=True,
    )
    slot_index = storage.ring_slot_index(resolution_seconds, bucket_start)

    with storage.Store.open(database) as reopened:
        before_slots = _slot_state(reopened)
        before_pending = _pending_rows(reopened)
        # The exact state the old code preserved forever: populated AND owed.
        assert before_slots[(resolution_seconds, slot_index)][0] == bucket_start
        assert before_slots[(resolution_seconds, slot_index)][1] is not None
        assert (resolution_seconds, bucket_start) in _pending(reopened)

        service.writer = reopened
        service._ring_publications = 0
        monotonic_now[0] += service_module.RING_FLUSH_SECONDS
        wall_now[0] += service_module.RING_FLUSH_SECONDS
        service._build_once(reopened, True, frozenset())

        after_slots = _slot_state(reopened)
        assert after_slots[(resolution_seconds, slot_index)] == (None, None, 0, 0, 0, 0.0, 0), (
            "the unrebuildable slot still serves a contradicted payload"
        )
        assert (resolution_seconds, bucket_start) not in _pending(reopened)
        # One appended fact contradicts one bucket per resolution, so four cells are owed. Exactly
        # ONE of them -- the out-of-window 1-second cell -- becomes a gap; the other three are
        # in-window and must be republished, not cleared. Nothing outside those four moves.
        owed = {(row[0], row[1]) for row in before_pending}
        owed_addresses = {
            (owed_resolution, storage.ring_slot_index(owed_resolution, owed_start))
            for owed_resolution, owed_start in owed
        }
        changed = {
            address for address, value in after_slots.items()
            if before_slots.get(address) != value
        }
        assert changed <= owed_addresses, (
            f"the honest gap disturbed slots no ledger row named: {changed - owed_addresses}"
        )
        emptied = {address for address in changed if after_slots[address][1] is None}
        assert emptied == {(resolution_seconds, slot_index)}, (
            f"the honest gap cleared slots beyond the unrebuildable one: {emptied}"
        )
        for address in changed - emptied:
            assert after_slots[address][0] == before_slots[address][0], (
                f"a republished slot changed identity: {address}"
            )
        removed = before_pending - _pending_rows(reopened)
        assert {(row[0], row[1]) for row in removed} == owed, (
            f"the ledger retired rows nothing answered: {removed}"
        )

    with storage.Store.open(database) as final:
        # Reopen preserves the gap: no resurrection of the payload, no resurrection of the row.
        assert _slot_state(final)[(resolution_seconds, slot_index)] == (None, None, 0, 0, 0, 0.0, 0)
        assert (resolution_seconds, bucket_start) not in _pending(final)
        window_end = int(wall_now[0]) - int(wall_now[0]) % resolution_seconds
        window = final.read_ring_window(
            range_seconds=resolution_seconds * CAPACITIES[resolution_seconds],
            resolution_seconds=resolution_seconds,
            window_end=window_end,
        )
        assert bucket_start not in {row.bucket_start for row in window.rows}


def test_a_contradicted_ring_window_declines_instead_of_serving_a_fabricated_zero(tmp_path):
    """A pending row means the reader routes to the MATERIALIZER, not to a zero placeholder.

    TRACED FIRST INCORRECT TRANSITION for the real-browser restart landing, measured against a
    live page: a browser posting its own telemetry invalidates the right-edge bucket at every
    resolution, and at 60s that is the same bucket the seeded usage atom lives in.
    `read_ring_window` correctly reported it missing, and the ring reader substituted
    `_ring_gap_bucket` -- so the page rendered a cost total of 0 tokens for a store holding 12, and
    blamed `incomplete_persisted_bucket`. An honest gap is for a slot NOTHING can rebuild, and that
    slot is cleared and leaves no pending row; while a row is pending the bucket is rebuildable.
    """
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    database = tmp_path / storage.DATABASE_FILENAME
    service = _real_service(tmp_path, monotonic_now, wall_now)
    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        slot = opened._connection().execute(
            "SELECT bucket_start FROM aggregate_ring_slots "
            "WHERE bucket_json IS NOT NULL AND resolution_seconds = 60 "
            "ORDER BY bucket_start DESC LIMIT 1"
        ).fetchone()
        assert slot is not None
        bucket_start = int(slot[0])
        request = service._ring_snapshot_request(3_600, 60)

        answered = service._read_ring_snapshot(request, reader=opened)
        assert answered.entry is not None, answered
        assert answered.fallback_reason == ""

        opened.append_batch(observations=[_observation("late", float(bucket_start) + 0.5)])
        assert (60, bucket_start) in _pending(opened)

        contradicted = service._read_ring_snapshot(request, reader=opened)

    assert contradicted.entry is None, "a contradicted window was served from the ring"
    assert contradicted.fallback_reason == "ring_contradicted", contradicted.fallback_reason


def test_in_window_owed_cell_still_rebuilds_instead_of_becoming_a_gap(tmp_path):
    """The other half of the same decision: a rebuildable owed cell is republished, never cleared."""
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service, database, resolution_seconds, bucket_start = _owed_cell_after_restart(
        tmp_path, monotonic_now, wall_now, oldest=False,
    )
    slot_index = storage.ring_slot_index(resolution_seconds, bucket_start)

    with storage.Store.open(database) as reopened:
        before = _slot_state(reopened)[(resolution_seconds, slot_index)]
        assert (resolution_seconds, bucket_start) in _pending(reopened)
        service.writer = reopened
        service._ring_publications = 0
        monotonic_now[0] += service_module.RING_FLUSH_SECONDS
        wall_now[0] += service_module.RING_FLUSH_SECONDS
        service._build_once(reopened, True, frozenset())

        after = _slot_state(reopened)[(resolution_seconds, slot_index)]
        assert after[0] == bucket_start and after[1] is not None, (
            "a rebuildable owed cell was turned into a gap instead of republished"
        )
        assert after[5] > before[5], "the rebuildable cell was never republished"
        assert (resolution_seconds, bucket_start) not in _pending(reopened)


# --- real transaction overlap, two handles, barriers --------------------------------------------

@pytest.mark.parametrize("resolution_seconds", sorted(CAPACITIES))
def test_two_handles_overlapping_a_mutation_and_a_publication(tmp_path, resolution_seconds):
    """TWO open handles with an explicit barrier, not one sequential handle.

    Handle A opens a write transaction and appends the contradicting fact. Handle B's publication,
    built from the pre-mutation generation, lands only AFTER A commits. The immediate state at that
    first crossed boundary is asserted, then convergence after both close and a reopen.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    slot_count = CAPACITIES[resolution_seconds]
    bucket_start = resolution_seconds * slot_count * 4
    payload = storage.RingBucketWrite(
        resolution_seconds=resolution_seconds, bucket_start=bucket_start,
        bucket_json=_bucket(0).bucket_json, complete=True,
    )

    with storage.Store.open(database) as writer:
        writer.initialize_ring_storage()
        writer.publish_ring_buckets(buckets=[payload], source_generation=0, published_at=1.0)
        snapshot_generation = 0

        # BARRIER 1: handle B is opened while A still holds the pre-mutation view.
        with storage.Store.open_reader(database) as observer:
            before = {
                (int(r[0]), int(r[1])) for r in observer._connection().execute(
                    "SELECT resolution_seconds, bucket_start FROM ring_invalidations "
                    "WHERE applied_at IS NULL"
                )
            }
            assert (resolution_seconds, bucket_start) not in before

        # A commits the contradicting mutation.
        writer.append_batch(observations=[_observation("mid", float(bucket_start) + 0.5)])
        contradicting = max(_pending_generations(writer))
        assert contradicting > snapshot_generation

        # BARRIER 2: a second handle observes the committed contradiction before B publishes.
        with storage.Store.open_reader(database) as observer:
            crossed = {
                (int(r[0]), int(r[1])) for r in observer._connection().execute(
                    "SELECT resolution_seconds, bucket_start FROM ring_invalidations "
                    "WHERE applied_at IS NULL"
                )
            }
        assert (resolution_seconds, bucket_start) in crossed, (
            "the second handle could not observe the committed contradiction"
        )

        # B's stale publication lands last, with a far later clock.
        writer.publish_ring_buckets(
            buckets=[payload], source_generation=snapshot_generation, published_at=9_999.0,
        )
        assert (resolution_seconds, bucket_start) in _pending(writer)

    with storage.Store.open(database) as reopened:
        assert (resolution_seconds, bucket_start) in _pending(reopened), (
            "pending state did not converge across reopen"
        )


# --- the restart repair transition --------------------------------------------------------------
# `_pending_ring_dirty` lives in memory and does not survive a restart. Recording an invalidation
# hides its bucket, but only a republication answers one, and republication is driven by that
# in-memory set -- so after a restart the durable ledger owed buckets that nothing could hear, and
# the right edge stayed hidden forever. A served page rendered a permanent gap with zero cost.


def _cells_owed(store_obj: storage.Store) -> set[tuple[int, int]]:
    return {
        (r, b) for r, b, _g in storage.pending_invalidation_cells(store_obj._connection())
    }


def test_a_restart_repairs_the_buckets_the_durable_ledger_still_owes(tmp_path):
    """The exact missing transition, at backend level and deterministic."""
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = _real_service(tmp_path, monotonic_now, wall_now)

    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        slot = opened._connection().execute(
            "SELECT resolution_seconds, bucket_start FROM aggregate_ring_slots "
            "WHERE bucket_json IS NOT NULL ORDER BY resolution_seconds, bucket_start LIMIT 1"
        ).fetchone()
        resolution_seconds, bucket_start = int(slot[0]), int(slot[1])
        opened.append_batch(observations=[_observation("late", float(bucket_start) + 0.5)])
        assert (resolution_seconds, bucket_start) in _cells_owed(opened)

    # RESTART: a brand-new service object, so the in-memory dirty set is empty by construction.
    restarted = _real_service(tmp_path, monotonic_now, wall_now)
    with storage.Store.open(database) as reopened:
        restarted.writer = reopened
        assert not restarted._pending_ring_dirty, "the fixture did not actually simulate a restart"
        monotonic_now[0] += 10_000.0
        restarted._next_ring_flush_at = monotonic_now[0] + 10_000.0
        restarted._build_once(reopened, True, frozenset())

        # Repair must not depend on the cadence, so the deadline is pushed far away BEFORE the
        # build that performs it.
        assert (resolution_seconds, bucket_start) not in _cells_owed(reopened), (
            "the build did not answer the owed bucket before readiness"
        )
        assert restarted.repair_pending_ring_slots(reopened) is None, (
            "repair ran twice; the build owner should already have answered everything"
        )
        window = reopened.read_ring_window(
            range_seconds=RANGE_SECONDS, resolution_seconds=RESOLUTION, window_end=7_200,
        )
        assert bucket_start not in window.pending_invalidations


def test_the_repair_is_bounded_to_the_slots_the_ledger_names(tmp_path):
    """Exact-slot repair, not a full-ring rebuild.

    A repair that rewrote every slot would also make the test above pass, and would reintroduce the
    cost this ledger exists to avoid.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = _real_service(tmp_path, monotonic_now, wall_now)

    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        service._flush_ring_if_due()
        slot = opened._connection().execute(
            "SELECT resolution_seconds, bucket_start FROM aggregate_ring_slots "
            "WHERE bucket_json IS NOT NULL ORDER BY resolution_seconds, bucket_start LIMIT 1"
        ).fetchone()
        bucket_start = int(slot[1])
        opened.append_batch(observations=[_observation("one", float(bucket_start) + 0.5)])
        owed = len(_cells_owed(opened))

    restarted = _real_service(tmp_path, monotonic_now, wall_now)
    with storage.Store.open(database) as reopened:
        restarted.writer = reopened
        restarted._build_once(reopened, True, frozenset())
        # Drain the FULL-build dirty set first, so what follows measures the repair's OWN work
        # rather than the cold build's. Without this the assertion reads 1,248 buckets for 4 owed
        # cells and blames the repair for a full-ring publication it did not cause.
        monotonic_now[0] += service_module.RING_FLUSH_SECONDS
        restarted._flush_ring_if_due(reopened)
        still_owed = len(_cells_owed(reopened))
        monotonic_now[0] += 10_000.0
        restarted._next_ring_flush_at = monotonic_now[0] + 10_000.0
        published = restarted.repair_pending_ring_slots(reopened)

    if still_owed == 0:
        # The cold flush already answered everything; the repair correctly has nothing to do.
        assert published is None
    else:
        assert published is not None
        assert published.buckets_updated <= still_owed, (
            f"repair rewrote {published.buckets_updated} buckets for {still_owed} owed cells"
        )


def test_a_restart_with_nothing_owed_repairs_nothing(tmp_path):
    """Negative control: repair must be driven by the ledger, not run unconditionally."""
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = _real_service(tmp_path, monotonic_now, wall_now)

    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        service._flush_ring_if_due()
        assert not _cells_owed(opened)

    restarted = _real_service(tmp_path, monotonic_now, wall_now)
    with storage.Store.open(database) as reopened:
        restarted.writer = reopened
        restarted._build_once(reopened, True, frozenset())
        assert restarted.repair_pending_ring_slots(reopened) is None


def test_the_repair_cannot_let_a_stale_generation_retire_a_newer_contradiction(tmp_path):
    """Safety: repair reuses the generation-gated retirement, it does not bypass it."""
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database) as opened:
        opened.initialize_ring_storage()
        opened.publish_ring_buckets(buckets=[_bucket(7_140)], source_generation=0, published_at=1.0)
        opened.append_batch(observations=[_observation("a", 7_140.5)])
        opened.append_batch(observations=[_observation("b", 7_141.5)])
        newer = max(_pending_generations(opened))

        opened.publish_ring_buckets(
            buckets=[_bucket(7_140, value=3)], source_generation=newer - 1, published_at=9_999.0,
        )

        assert (RESOLUTION, 7_140) in _pending(opened), (
            "a stale-generation publication retired a newer contradiction"
        )


# --- readiness ordering -------------------------------------------------------------------------
# Repair ran in the WORKER after `_build_once` returned, but `_build_once` publishes the cache and
# signals readiness internally. Measured on a real Store: cold snapshot cost 0, readiness set with
# 139 durable invalidations still pending, and only a later explicit repair drove pending 139 -> 0.


def test_readiness_is_not_signalled_while_the_ledger_still_owes_slots(tmp_path):
    """`ready=True` must mean every startup-owed slot has been answered.

    Asserted at the exact instant readiness is signalled, by observing the event from inside the
    build, rather than by checking the ledger afterwards -- which is what let the race hide.
    """
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = _real_service(tmp_path, monotonic_now, wall_now)

    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        assert service._flush_ring_if_due() is not None
        slot = opened._connection().execute(
            "SELECT resolution_seconds, bucket_start FROM aggregate_ring_slots "
            "WHERE bucket_json IS NOT NULL ORDER BY resolution_seconds, bucket_start LIMIT 1"
        ).fetchone()
        bucket_start = int(slot[1])
        opened.append_batch(observations=[_observation("late", float(bucket_start) + 0.5)])
        assert _cells_owed(opened), "the fixture recorded nothing to owe"

    # Restart: fresh service, empty in-memory dirty set.
    restarted = _real_service(tmp_path, monotonic_now, wall_now)
    owed_at_ready: list[int] = []
    with storage.Store.open(database) as reopened:
        restarted.writer = reopened
        real_set = restarted.cache_ready_event.set

        def observe_at_ready() -> None:
            owed_at_ready.append(len(_cells_owed(reopened)))
            real_set()

        restarted.cache_ready_event.set = observe_at_ready  # type: ignore[method-assign]
        monotonic_now[0] += 10_000.0
        restarted._build_once(reopened, True, frozenset())
        restarted.cache_ready_event.set = real_set  # type: ignore[method-assign]

    assert owed_at_ready, "readiness was never signalled, so the ordering was not exercised"
    assert owed_at_ready[0] == 0, (
        f"readiness signalled with {owed_at_ready[0]} durable invalidations still owed"
    )


def test_the_first_cold_request_after_readiness_serves_the_repaired_bucket(tmp_path):
    """The consumer-visible half: the real cold request path, not just the ledger."""
    database = tmp_path / storage.DATABASE_FILENAME
    monotonic_now = [0.0]
    wall_now = [1_800_000_000.0]
    service = _real_service(tmp_path, monotonic_now, wall_now)

    with storage.Store.open(database) as opened:
        service.writer = opened
        service._build_once(opened, True, frozenset())
        monotonic_now[0] = service_module.RING_FLUSH_SECONDS
        service._flush_ring_if_due()
        slot = opened._connection().execute(
            "SELECT resolution_seconds, bucket_start FROM aggregate_ring_slots "
            "WHERE bucket_json IS NOT NULL ORDER BY resolution_seconds, bucket_start LIMIT 1"
        ).fetchone()
        resolution_seconds, bucket_start = int(slot[0]), int(slot[1])
        opened.append_batch(observations=[_observation("late", float(bucket_start) + 0.5)])

    restarted = _real_service(tmp_path, monotonic_now, wall_now)
    with storage.Store.open(database) as reopened:
        restarted.writer = reopened
        monotonic_now[0] += 10_000.0
        restarted._build_once(reopened, True, frozenset())
        assert restarted.cache_ready_event.is_set()

        window = reopened.read_ring_window(
            range_seconds=RANGE_SECONDS, resolution_seconds=RESOLUTION, window_end=7_200,
        )
        assert window.pending_invalidations == (), (
            "a request after readiness still saw owed buckets"
        )
        assert (resolution_seconds, bucket_start) not in _cells_owed(reopened)


# The honest-gap owner drops a payload without a republication, so every row below pins its exact
# slot and pending-ledger authority against current, lapped, retired, and replaced identities.
def test_the_honest_gap_owner_clears_and_retires_only_the_named_cell(tmp_path):
    kept_start = RESOLUTION * 4_000
    gapped_start = kept_start + RESOLUTION
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store_obj:
        store_obj.initialize_ring_storage()
        store_obj.publish_ring_buckets(
            buckets=[_bucket(kept_start), _bucket(gapped_start)],
            source_generation=0, published_at=1.0,
        )
        store_obj.append_batch(observations=[
            _observation("kept", float(kept_start) + 0.5),
            _observation("gapped", float(gapped_start) + 0.5),
        ])
        assert {(RESOLUTION, kept_start), (RESOLUTION, gapped_start)} <= _pending(store_obj)
        pending = [row for row in store_obj.pending_invalidation_cells() if row[1] == gapped_start]
        before = _slot_state(store_obj)
        retired = store_obj.retire_unrebuildable_ring_cells(pending)
        assert retired == ((RESOLUTION, gapped_start),)
        after = _slot_state(store_obj)
        gapped_address = (RESOLUTION, storage.ring_slot_index(RESOLUTION, gapped_start))
        kept_address = (RESOLUTION, storage.ring_slot_index(RESOLUTION, kept_start))
        assert after[gapped_address] == (None, None, 0, 0, 0, 0.0, 0)
        assert after[kept_address] == before[kept_address]
        assert (RESOLUTION, gapped_start) not in _pending(store_obj)
        assert (RESOLUTION, kept_start) in _pending(store_obj)
        assert store_obj.retire_unrebuildable_ring_cells(pending) == ()
        assert _slot_state(store_obj) == after


@pytest.mark.parametrize("transition", ("lapped", "republished", "replaced"))
def test_stale_honest_gap_work_cannot_erase_a_newer_identity(tmp_path, transition):
    slot_count = CAPACITIES[RESOLUTION]
    old_start = RESOLUTION * slot_count * 4
    lapped_start = old_start + RESOLUTION * slot_count
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database) as stale_owner:
        stale_owner.initialize_ring_storage()
        stale_owner.publish_ring_buckets(
            buckets=[_bucket(old_start)], source_generation=0, published_at=1.0,
        )
        stale_owner.append_batch(observations=[
            _observation("contradiction", float(old_start) + 0.5),
        ])
        stale_work = stale_owner.pending_invalidation_cells()
        with storage.Store.open(database) as concurrent_owner:
            if transition == "replaced":
                concurrent_owner.append_batch(observations=[
                    _observation("newer", float(old_start) + 1.5),
                ])
            else:
                current_start = lapped_start if transition == "lapped" else old_start
                concurrent_owner.publish_ring_buckets(
                    buckets=[_bucket(current_start, value=2)],
                    source_generation=1, published_at=2.0,
                )
            current = (_slot_state(concurrent_owner), concurrent_owner.pending_invalidation_cells())
        assert stale_owner.retire_unrebuildable_ring_cells(stale_work) == ()
        assert (_slot_state(stale_owner), stale_owner.pending_invalidation_cells()) == current


def test_the_honest_gap_owner_refuses_a_reader(tmp_path):
    database = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(database) as writer:
        writer.initialize_ring_storage()
        writer.publish_ring_buckets(
            buckets=[_bucket(RESOLUTION * 4_000)], source_generation=0, published_at=1.0,
        )
    with storage.Store.open_reader(database) as reader:
        with pytest.raises(storage.StatsCurrentError):
            reader.retire_unrebuildable_ring_cells([(RESOLUTION, RESOLUTION * 4_000, 0)])
