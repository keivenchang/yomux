# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Red control and prototype for the statsd pending-fact serving overlay.

The queue item asks for batched persistence "without reducing one-second acquisition or
in-memory UI freshness".  Today those two are the same thing: a fact reaches a reader only
by being committed, because `_build_once` obtains facts exclusively through
`reader.pinned_snapshot(...)` at `service.py:2366`.  Deferring the commit to a shared
5-10 s interval therefore deletes one-second freshness unless something else serves the
not-yet-durable fact.

`test_deferring_the_commit_hides_the_fact_at_one_second` is the RED CONTROL: it proves the
coupling exists, and every claim below it is meaningless without it.  The overlay prototype
then serves the same deferred fact through the SAME builder by merging the buffered records
into the `StoreSnapshot` the builder already reads, which is why this is one builder and not
two.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import replace

from yolomux_lib.stats_current import service as service_module
from yolomux_lib.stats_current import storage

CLOCK = 100_000.0
OBSERVED_AT = CLOCK - 0.25


def _cpu(event_id: str, observed_at: float, process_percent: float) -> storage.Observation:
    return storage.Observation(
        event_id, "cpu", "host", observed_at, "epoch:1", 1,
        {"process_percent": process_percent, "system_percent": 10.0},
    )


def _coverage() -> storage.CoverageEpoch:
    return storage.CoverageEpoch("cpu", "host", "epoch:1", CLOCK - 60.0, None, 1.0, 1)


def _service(tmp_path, path):
    return service_module.StatsCurrentService(
        tmp_path / "statsd.sock", path, clock=lambda: CLOCK,
    )


def _one_second_series(generation, observed_at: float):
    """Return the 1 s bucket covering `observed_at`, or None when the reader cannot see it."""

    bucket_start = math.floor(observed_at)
    for bucket in generation.layer(1).buckets:
        if bucket.start == bucket_start:
            return bucket
    return None


CPU_SERIES = "cpu_max_percent:host"


def _cpu_percent(bucket) -> float | None:
    """Read the process-CPU series the materializer derives from `process_percent`."""

    if bucket is None:
        return None
    for value in bucket.series:
        if value.name == CPU_SERIES:
            return float(value.value)
    return None


class PendingFactBuffer:
    """The statsd-owned buffer of accepted-but-not-yet-committed facts.

    Deliberately holds the SAME typed records the append path already carries
    (`storage.Observation`, `storage.UsageAtom`, ...), so the overlay introduces no new
    fact type and no second representation of a fact.
    """

    def __init__(self) -> None:
        self.observations: dict[tuple[str, str, str], storage.Observation] = {}
        self.usage_atoms: dict[tuple[str, str, str, str, str], storage.UsageAtom] = {}

    def stage(self, *, observations=(), usage_atoms=()) -> None:
        for item in observations:
            self.observations[(item.family, item.source_id, item.event_id)] = item
        for item in usage_atoms:
            self.usage_atoms[
                (item.event_id, item.direction, item.modality, item.cache_role, item.unit)
            ] = item

    def drain(self) -> tuple[tuple[storage.Observation, ...], tuple[storage.UsageAtom, ...]]:
        observations = tuple(self.observations.values())
        usage_atoms = tuple(self.usage_atoms.values())
        self.observations.clear()
        self.usage_atoms.clear()
        return observations, usage_atoms

    def merge(self, snapshot: storage.StoreSnapshot, window) -> storage.StoreSnapshot:
        """Overlay buffered facts onto one committed snapshot.

        Committed rows WIN on identity collision: once a fact is durable the buffered copy is
        the stale duplicate, never the other way round.  This is the property that makes a
        double-count impossible across the flush.
        """

        low, high = window
        committed_observations = {
            (item.family, item.source_id, item.event_id): item
            for item in snapshot.observations
        }
        pending_observations = {
            key: item
            for key, item in self.observations.items()
            if key not in committed_observations and low <= item.observed_at <= high
        }
        committed_usage = {
            (item.event_id, item.direction, item.modality, item.cache_role, item.unit): item
            for item in snapshot.usage_atoms
        }
        pending_usage = {
            key: item
            for key, item in self.usage_atoms.items()
            if key not in committed_usage and low <= item.observed_at <= high
        }
        if not pending_observations and not pending_usage:
            return snapshot
        return replace(
            snapshot,
            # Same ORDER BY the SQL uses, so the materializer cannot tell the rows apart.
            observations=tuple(sorted(
                (*snapshot.observations, *pending_observations.values()),
                key=lambda item: (item.observed_at, item.family, item.source_id),
            )),
            usage_atoms=tuple(sorted(
                (*snapshot.usage_atoms, *pending_usage.values()),
                key=lambda item: (item.observed_at, item.event_id, item.direction,
                                  item.modality, item.cache_role, item.unit),
            )),
        )


class PendingOverlayReader:
    """Prototype of the overlay seam: `_build_once` reads facts through exactly one call."""

    def __init__(self, reader: storage.Store, buffer: PendingFactBuffer) -> None:
        self._reader = reader
        self._buffer = buffer
        self.snapshot_calls = 0

    def __getattr__(self, name):
        return getattr(self._reader, name)

    @contextmanager
    def pinned_snapshot(self, **kwargs):
        self.snapshot_calls += 1
        window = kwargs.get("read_window") or (float("-inf"), float("inf"))
        with self._reader.pinned_snapshot(**kwargs) as read_committed:
            yield lambda: self._buffer.merge(read_committed(), window)


def _open_pair(tmp_path, name):
    path = tmp_path / name / storage.DATABASE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = storage.Store.open(path)
    return path, writer


def test_a_committed_fact_is_visible_in_the_one_second_layer(tmp_path):
    """Baseline: today's behaviour, with the commit in place."""

    path, writer = _open_pair(tmp_path, "committed")
    writer.append_batch(observations=(_cpu("cpu-1", OBSERVED_AT, 42.0),),
                        coverage_epochs=(_coverage(),))
    reader = storage.Store.open_reader(path)
    service = _service(tmp_path, path)
    service._build_once(reader, True, frozenset())
    assert service._cache is not None
    assert _cpu_percent(_one_second_series(service._cache.generation, OBSERVED_AT)) == 42.0
    reader.close()
    writer.close()


def test_deferring_the_commit_hides_the_fact_at_one_second(tmp_path):
    """RED CONTROL. The fact is acquired but not committed; the reader cannot see it.

    This is exactly the state a 5-10 s batching interval creates for up to 10 seconds, and it
    is why the queue item's "without reducing in-memory UI freshness" clause needs an overlay
    rather than only a larger transaction.
    """

    path, writer = _open_pair(tmp_path, "deferred")
    writer.append_batch(coverage_epochs=(_coverage(),))
    buffer = PendingFactBuffer()
    # Acquisition still happens at one second. Only the COMMIT is deferred.
    buffer.stage(observations=(_cpu("cpu-1", OBSERVED_AT, 42.0),))
    reader = storage.Store.open_reader(path)
    service = _service(tmp_path, path)
    service._build_once(reader, True, frozenset())
    assert service._cache is not None
    assert _cpu_percent(_one_second_series(service._cache.generation, OBSERVED_AT)) is None
    assert reader.read_snapshot().observations == ()
    reader.close()
    writer.close()


def test_the_overlay_restores_one_second_visibility_without_committing(tmp_path):
    """The same deferred fact, served through the same builder, with nothing written."""

    path, writer = _open_pair(tmp_path, "overlay")
    writer.append_batch(coverage_epochs=(_coverage(),))
    buffer = PendingFactBuffer()
    buffer.stage(observations=(_cpu("cpu-1", OBSERVED_AT, 42.0),))
    reader = storage.Store.open_reader(path)
    overlay = PendingOverlayReader(reader, buffer)
    service = _service(tmp_path, path)
    size_before = path.stat().st_size
    generation_before = reader.read_snapshot().schema.source_generation

    service._build_once(overlay, True, frozenset())

    assert service._cache is not None
    assert _cpu_percent(_one_second_series(service._cache.generation, OBSERVED_AT)) == 42.0
    # One builder: the overlay is consulted through the single existing fact read.
    assert overlay.snapshot_calls == 1
    # Nothing became durable, and no generation was manufactured for the pending fact.
    assert reader.read_snapshot().observations == ()
    assert reader.read_snapshot().schema.source_generation == generation_before
    assert path.stat().st_size == size_before
    reader.close()
    writer.close()


def test_committing_the_buffer_does_not_double_count_the_fact(tmp_path):
    """Across the flush the fact appears exactly once, from either side of the boundary."""

    path, writer = _open_pair(tmp_path, "flush")
    writer.append_batch(coverage_epochs=(_coverage(),))
    buffer = PendingFactBuffer()
    observation = _cpu("cpu-1", OBSERVED_AT, 42.0)
    buffer.stage(observations=(observation,))
    reader = storage.Store.open_reader(path)
    overlay = PendingOverlayReader(reader, buffer)
    service = _service(tmp_path, path)

    # The flush commits the buffer but deliberately does NOT drain it yet, which is the
    # widest possible double-count window: the fact is durable AND still buffered.
    observations, usage_atoms = buffer.observations.values(), ()
    writer.append_batch(observations=tuple(observations), usage_atoms=usage_atoms)

    service._build_once(overlay, True, frozenset())
    assert service._cache is not None
    bucket = _one_second_series(service._cache.generation, OBSERVED_AT)
    assert _cpu_percent(bucket) == 42.0
    assert bucket.source_count == 1
    reader.close()
    writer.close()


def flush(buffer: PendingFactBuffer, writer: storage.Store) -> tuple[int, int]:
    """Commit one buffered batch. Returns (committed, quarantined).

    Two rules carry the safety properties:
      * the buffer is drained ONLY after the transaction commits, so a crash mid-flush
        replays the batch instead of losing it;
      * a batch the store REJECTS is quarantined rather than retained, so a fact the
        transaction discarded stops being served instead of being offered forever.
    """

    observations, usage_atoms = buffer.observations, buffer.usage_atoms
    if not observations and not usage_atoms:
        return 0, 0
    batch = tuple(observations.values()), tuple(usage_atoms.values())
    try:
        writer.append_batch(observations=batch[0], usage_atoms=batch[1])
    except storage.StorageValidationError:
        buffer.drain()
        return 0, len(batch[0]) + len(batch[1])
    buffer.drain()
    return len(batch[0]) + len(batch[1]), 0


def test_a_fact_the_transaction_rejects_stops_being_served(tmp_path):
    """A failed transaction must not leave the overlay serving the discarded fact."""

    path, writer = _open_pair(tmp_path, "rejected")
    writer.append_batch(observations=(_cpu("cpu-1", OBSERVED_AT, 42.0),),
                        coverage_epochs=(_coverage(),))
    buffer = PendingFactBuffer()
    # Same identity, different data: storage.py rejects this batch outright.
    buffer.stage(observations=(_cpu("cpu-1", OBSERVED_AT, 99.0),))
    reader = storage.Store.open_reader(path)
    overlay = PendingOverlayReader(reader, buffer)

    # Before the flush the DURABLE value already wins on identity collision.
    service = _service(tmp_path, path)
    service._build_once(overlay, True, frozenset())
    assert _cpu_percent(_one_second_series(service._cache.generation, OBSERVED_AT)) == 42.0

    committed, quarantined = flush(buffer, writer)
    assert (committed, quarantined) == (0, 1)
    assert buffer.observations == {}

    after = _service(tmp_path, path)
    after._build_once(overlay, True, frozenset())
    assert _cpu_percent(_one_second_series(after._cache.generation, OBSERVED_AT)) == 42.0
    reader.close()
    writer.close()


def test_the_overlay_never_advances_the_source_generation_ahead_of_durability(tmp_path):
    """Serving a pending fact must not manufacture or advance a generation."""

    path, writer = _open_pair(tmp_path, "generation")
    writer.append_batch(observations=(_cpu("cpu-0", OBSERVED_AT - 1.0, 7.0),),
                        coverage_epochs=(_coverage(),))
    durable_generation = storage.Store.open_reader(path).read_snapshot().schema.source_generation
    buffer = PendingFactBuffer()
    buffer.stage(observations=(_cpu("cpu-1", OBSERVED_AT, 42.0),))
    reader = storage.Store.open_reader(path)
    overlay = PendingOverlayReader(reader, buffer)
    service = _service(tmp_path, path)

    service._build_once(overlay, True, frozenset())

    # The pending fact is served ...
    assert _cpu_percent(_one_second_series(service._cache.generation, OBSERVED_AT)) == 42.0
    # ... but the generation the reader publishes is still the DURABLE one.
    assert service._cache.generation.source_generation == durable_generation
    assert reader.read_snapshot().schema.source_generation == durable_generation

    committed, quarantined = flush(buffer, writer)
    assert (committed, quarantined) == (1, 0)
    after = storage.Store.open_reader(path)
    assert after.read_snapshot().schema.source_generation > durable_generation
    after.close()
    reader.close()
    writer.close()


def test_an_unflushed_buffer_is_lost_at_close_which_is_why_shutdown_must_flush(tmp_path):
    """Terminal boundary RED CONTROL: closing without flushing drops buffered facts."""

    path, writer = _open_pair(tmp_path, "shutdown-red")
    writer.append_batch(coverage_epochs=(_coverage(),))
    buffer = PendingFactBuffer()
    buffer.stage(observations=(_cpu("cpu-1", OBSERVED_AT, 42.0),))
    writer.close()

    reopened = storage.Store.open(path)
    assert reopened.read_snapshot().observations == ()
    assert buffer.observations != {}  # still in memory, never durable
    reopened.close()


def test_flushing_at_the_terminal_boundary_keeps_the_fact_exactly_once(tmp_path):
    """Orderly shutdown, then prune, then VACUUM: neither lost nor duplicated."""

    path, writer = _open_pair(tmp_path, "shutdown-green")
    writer.append_batch(coverage_epochs=(_coverage(),))
    buffer = PendingFactBuffer()
    buffer.stage(observations=(_cpu("cpu-1", OBSERVED_AT, 42.0),))

    committed, quarantined = flush(buffer, writer)
    assert (committed, quarantined) == (1, 0) and buffer.observations == {}

    # A VACUUM cannot run inside a transaction; the flush must therefore be complete
    # before the worker's compaction, which it is because both hold work_lock in turn.
    writer.prune(now=OBSERVED_AT + 1.0)
    writer.vacuum(completed_at=OBSERVED_AT + 2.0)
    writer.close()

    reopened = storage.Store.open_reader(path)
    surviving = [item for item in reopened.read_snapshot().observations if item.event_id == "cpu-1"]
    assert len(surviving) == 1
    assert surviving[0].payload["process_percent"] == 42.0
    reopened.close()


def test_a_duplicated_usage_atom_does_not_double_the_token_total(tmp_path):
    """Bounds what the overlay must dedup: cost is already idempotent, aggregation is not.

    A duplicated OBSERVATION doubles `Bucket.source_count` (that is why `merge` drops
    identity collisions).  A duplicated USAGE ATOM does not change `usage_tokens`, because
    the materializer already keys atoms by identity.  Recording the asymmetry here stops a
    later reader from moving the dedup into the wrong layer.
    """

    path, writer = _open_pair(tmp_path, "usage")
    atom = storage.UsageAtom(
        "usage-1", "input", "text", "none", "tokens", OBSERVED_AT,
        {"quantity": 1000, "provider": "openai", "model": "gpt",
         "agent_id": "sol", "telemetry_complete": True},
    )
    writer.append_batch(usage_atoms=(atom,), coverage_epochs=(_coverage(),))
    buffer = PendingFactBuffer()
    buffer.stage(usage_atoms=(atom,))  # durable AND buffered at once
    reader = storage.Store.open_reader(path)
    service = _service(tmp_path, path)
    service._build_once(PendingOverlayReader(reader, buffer), True, frozenset())

    bucket = _one_second_series(service._cache.generation, OBSERVED_AT)
    tokens = [value.value for value in bucket.series if value.name == "usage_tokens"]
    assert tokens == [1000.0]
    reader.close()
    writer.close()


# --------------------------------------------------------------------------------------
# The RPC acknowledgement contract.
#
# A buffering listener cannot use the commit to decide `accepted` / `duplicate`, because the
# commit has not happened.  It does not have to: the decision at `storage.py:2734-2746` is a
# SELECT followed by an INSERT, and only the INSERT needs the transaction.  The probe below
# runs the SELECT half against committed rows AND the pending buffer, and must reach the same
# verdict the commit later reaches.  `read_bytes` was 0 in every arm of the REMEASURE-08
# grid, so this extra read is effectively free on a page-cached store.
# --------------------------------------------------------------------------------------

ACCEPTED, DUPLICATE, CONFLICT = "accepted", "duplicate", "conflict"


def probe_disposition(reader, buffer: PendingFactBuffer, observation) -> str:
    """Decide an append's disposition without committing it."""

    values = storage._observation_values(observation)
    identity, rest = values[:3], values[3:]
    pending = buffer.observations.get((observation.family, observation.source_id, observation.event_id))
    if pending is not None:
        return DUPLICATE if storage._observation_values(pending)[3:] == rest else CONFLICT
    previous = reader._connection().execute(
        "SELECT observed_at, epoch_id, owner_generation, payload_json FROM observations "
        "WHERE event_id = ? AND family = ? AND source_id = ?", identity,
    ).fetchone()
    if previous is None:
        return ACCEPTED
    return DUPLICATE if tuple(previous) == rest else CONFLICT


def commit_disposition(writer: storage.Store, observation) -> str:
    """What the real commit decides, for the same record."""

    try:
        return ACCEPTED if writer.append_observation(observation) else DUPLICATE
    except storage.StorageValidationError:
        return CONFLICT


def test_the_ack_time_probe_matches_what_the_commit_decides(tmp_path):
    """Every disposition the browser receipt contract needs, decided without a commit."""

    path, writer = _open_pair(tmp_path, "probe")
    committed = _cpu("cpu-1", OBSERVED_AT, 42.0)
    writer.append_batch(observations=(committed,), coverage_epochs=(_coverage(),))
    reader = storage.Store.open_reader(path)
    buffer = PendingFactBuffer()
    buffered = _cpu("cpu-2", OBSERVED_AT, 7.0)
    buffer.stage(observations=(buffered,))

    cases = [
        ("new identity", _cpu("cpu-3", OBSERVED_AT, 1.0), ACCEPTED),
        ("committed, identical", committed, DUPLICATE),
        ("committed, different payload", _cpu("cpu-1", OBSERVED_AT, 99.0), CONFLICT),
        ("buffered, identical", buffered, DUPLICATE),
        ("buffered, different payload", _cpu("cpu-2", OBSERVED_AT, 99.0), CONFLICT),
    ]
    probed = {name: probe_disposition(reader, buffer, item) for name, item, _ in cases}
    assert probed == {name: expected for name, _, expected in cases}, probed

    # Now let the buffer flush and replay every case through the REAL commit path. The probe
    # must have predicted each one, which is what makes a non-committing ack honest.
    assert flush(buffer, writer) == (1, 0)
    replayed = {}
    for name, item, _ in cases:
        replay_path, replay_writer = _open_pair(tmp_path, f"probe-replay-{name.replace(' ', '-').replace(',', '')}")
        replay_writer.append_batch(observations=(committed, buffered), coverage_epochs=(_coverage(),))
        replayed[name] = commit_disposition(replay_writer, item)
        replay_writer.close()
    assert replayed == probed, (replayed, probed)
    reader.close()
    writer.close()
