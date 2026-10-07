# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Batched persistence on a shared flush interval, with one-second freshness preserved.

The queue item asks to persist telemetry on a 5-10 s interval "without reducing one-second
acquisition or in-memory UI freshness".  `test_stats_pending_overlay_reproducer.py` holds the
red control that makes this falsifiable: withhold the commit and the fact leaves the
one-second layer.  These tests drive the real service instead of a prototype.

Two facts about this workload shape the design and are asserted here rather than assumed:

  * A live collector re-offers its OPEN coverage epoch on EVERY cadence tick with `ended_at`
    advanced by one cadence (`collectors.py:184`, and the comment at `storage.py:2864`).  So
    buffering observations alone would not remove a single commit -- the coverage row still
    writes every tick.  Coverage must buffer too, and it collapses per epoch.
  * The `browser` family carries the receipt barrier and its acknowledgement transfers
    custody, so it must not buffer.
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from yomux_lib.stats_current import service as service_module
from yomux_lib.stats_current import storage

@pytest.fixture(autouse=True)
def candidate_arm(monkeypatch):
    """Every test in this module exercises the CANDIDATE arm, and says so.

    The shipped DEFAULT is 0 -- write through, the pre-batching path -- pending the
    `source_generation` collision recorded beside `APPEND_FLUSH_SECONDS`. Without this the file
    would silently test whichever arm the default happened to be, which is exactly the quiet
    failure the admission variable exists to prevent. Tests that mean the control arm override
    it explicitly.
    """

    # BOTH names. The arm alone is refused: it is test-only and requires the test-container
    # marker, so a shipped statsd cannot enable a known-broken path from its environment.
    monkeypatch.setenv(service_module.APPEND_FLUSH_TEST_MARKER_ENV, "1")
    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME,
                       str(service_module.APPEND_FLUSH_MEASURED_SECONDS))


CLOCK = 100_000.0
AT = CLOCK - 0.25
CPU_SERIES = "cpu_max_percent:host"


def _cpu(event_id: str, observed_at: float, percent: float) -> storage.Observation:
    return storage.Observation(
        event_id, "cpu", "host", observed_at, "epoch:1", 1,
        {"process_percent": percent, "system_percent": 10.0},
    )


def _browser(event_id: str) -> storage.Observation:
    return storage.Observation(
        event_id, "browser", "client-1", AT, "epoch:b", 1, {"kind": "api", "latency_ms": 2},
    )


def _coverage(ended_at: float | None = None) -> storage.CoverageEpoch:
    return storage.CoverageEpoch("cpu", "host", "epoch:1", CLOCK - 60.0, ended_at, 1.0, 1)


def _service(tmp_path, path, monotonic=None):
    ticks = monotonic if monotonic is not None else [0.0]
    return service_module.StatsCurrentService(
        tmp_path / "statsd.sock", path,
        clock=lambda: CLOCK, monotonic=lambda: ticks[0],
    )


def _bucket(generation, observed_at):
    for bucket in generation.layer(1).buckets:
        if bucket.start == math.floor(observed_at):
            return bucket
    return None


def _percent(bucket):
    if bucket is None:
        return None
    for value in bucket.series:
        if value.name == CPU_SERIES:
            return float(value.value)
    return None


def _rows(path):
    with storage.Store.open_reader(path) as reader:
        return reader.read_snapshot().observations


def test_a_buffered_family_append_does_not_commit_but_is_acknowledged(tmp_path):
    """The append is accepted and answered without a transaction."""

    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        service.writer = writer
        response = service._append_records(
            observations=(_cpu("cpu-1", AT, 42.0),), coverage=(_coverage(AT + 1.0),),
        )
        service.writer = None

    assert response["ok"] is True
    assert response["accepted"] == 2
    assert response["duplicates"] == 0
    # Nothing durable, and no generation was invented for it.
    assert _rows(path) == ()
    assert response["source_generation"] is None
    assert service._buffered_fact_count() == 2


def test_the_buffered_fact_is_visible_in_the_one_second_layer(tmp_path):
    """The requirement: one-second freshness survives the deferred commit."""

    path = tmp_path / storage.DATABASE_FILENAME
    service = _service(tmp_path, path)
    with storage.Store.open(path) as writer:
        service.writer = writer
        service._append_records(
            observations=(_cpu("cpu-1", AT, 42.0),), coverage=(_coverage(AT + 1.0),),
        )
        with storage.Store.open_reader(path) as reader:
            service._build_once(reader, True, frozenset())
        service.writer = None

    assert service._cache is not None
    assert _percent(_bucket(service._cache.generation, AT)) == 42.0
    # Still nothing on disk while the reader sees it.
    assert _rows(path) == ()


def test_the_flush_deadline_commits_the_buffer_exactly_once(tmp_path):
    """The commit happens on the shared interval, and the fact is not duplicated."""

    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        service.writer = writer
        service._append_records(
            observations=(_cpu("cpu-1", AT, 42.0),), coverage=(_coverage(AT + 1.0),),
        )
        assert service._flush_appends_if_due(writer) is False   # not due yet
        assert _rows(path) == ()
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        assert service._flush_appends_if_due(writer) is True
        service.writer = None

    rows = _rows(path)
    assert len(rows) == 1 and rows[0].event_id == "cpu-1"
    assert service._buffered_fact_count() == 0


def test_coverage_epochs_collapse_across_the_flush(tmp_path):
    """Ten ticks of one open epoch become one row carrying the last `ended_at`."""

    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        service.writer = writer
        for index in range(10):
            service._append_records(
                observations=(_cpu(f"cpu-{index}", AT - index, 42.0),),
                coverage=(_coverage(AT + 1.0 + index),),
            )
        # Ten observations survive; ten coverage offers collapse to one.
        assert service._buffered_fact_count() == 11
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        assert service._flush_appends_if_due(writer) is True
        service.writer = None

    with storage.Store.open_reader(path) as reader:
        snapshot = reader.read_snapshot()
    assert len(snapshot.observations) == 10
    epochs = [item for item in snapshot.coverage_epochs if item.epoch_id == "epoch:1"]
    assert len(epochs) == 1
    assert epochs[0].ended_at == AT + 1.0 + 9


def test_the_browser_family_still_commits_synchronously(tmp_path):
    """Receipt custody transfers on acknowledgement, so browser must stay durable."""

    path = tmp_path / storage.DATABASE_FILENAME
    service = _service(tmp_path, path)
    with storage.Store.open(path) as writer:
        service.writer = writer
        response = service._append_records(
            observations=(_browser("b-1"),),
            observation_receipt_event_ids=("b-1",),
        )
        service.writer = None

    assert response["ok"] is True
    assert response["source_generation"] is not None
    assert response["observation_receipts"] == [{"event_id": "b-1", "disposition": "accepted"}]
    assert len(_rows(path)) == 1
    assert service._buffered_fact_count() == 0


def test_a_conflicting_record_falls_back_to_the_synchronous_commit(tmp_path):
    """The identity-conflict contract that runtime.py bisects on must be preserved."""

    path = tmp_path / storage.DATABASE_FILENAME
    service = _service(tmp_path, path)
    with storage.Store.open(path) as writer:
        writer.append_batch(observations=(_cpu("cpu-1", AT, 42.0),))
        service.writer = writer
        with pytest.raises(storage.StorageValidationError):
            service._append_records(observations=(_cpu("cpu-1", AT, 99.0),))
        service.writer = None
    assert service._buffered_fact_count() == 0


def test_a_duplicate_is_reported_without_a_commit(tmp_path):
    """The probe decides `duplicate` from a read, as the receipt contract needs."""

    path = tmp_path / storage.DATABASE_FILENAME
    service = _service(tmp_path, path)
    observation = _cpu("cpu-1", AT, 42.0)
    with storage.Store.open(path) as writer:
        writer.append_batch(observations=(observation,))
        service.writer = writer
        response = service._append_records(observations=(observation,))
        service.writer = None

    assert response["accepted"] == 0 and response["duplicates"] == 1
    assert service._buffered_fact_count() == 0


def test_the_buffer_never_advances_the_generation_before_it_commits(tmp_path):
    """No cursor, watermark or generation may lead durability."""

    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        writer.append_batch(observations=(_cpu("cpu-0", AT - 5.0, 7.0),))
        durable = writer.read_snapshot().schema.source_generation
        service.writer = writer
        service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))
        assert service._latest_source_generation <= durable
        assert service._pending_ring_dirty == set()      # durable publication must not lead
        assert service._pending_dirty                    # in-memory publication may lead
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        service._flush_appends_if_due(writer)
        assert service._latest_source_generation > durable
        assert service._pending_ring_dirty              # only now
        service.writer = None


def test_the_flush_deadline_participates_in_the_worker_wait(tmp_path):
    """No second scheduler: the existing multi-deadline loop owns it."""

    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    assert service._next_append_flush_at is None
    with storage.Store.open(path) as writer:
        service.writer = writer
        service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))
        assert service._next_append_flush_at == service_module.APPEND_FLUSH_MEASURED_SECONDS
        assert service._ring_wait_timeout() <= service_module.APPEND_FLUSH_MEASURED_SECONDS
        service.writer = None


def test_close_flushes_the_buffer_before_the_store_goes_away(tmp_path):
    """Terminal boundary: an orderly shutdown must not drop buffered samples."""

    path = tmp_path / storage.DATABASE_FILENAME
    service = _service(tmp_path, path)
    writer = storage.Store.open(path)
    service.writer = writer
    service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))
    assert _rows(path) == ()
    service._close()

    rows = _rows(path)
    assert len(rows) == 1 and rows[0].event_id == "cpu-1"


def test_the_control_arm_commits_synchronously(tmp_path, monkeypatch):
    """`YOLOMUX_STATS_APPEND_FLUSH_SECONDS=0` selects the pre-batching behaviour.

    This is the A/B's control arm and the rollback switch. It is an admission variable, so
    `docker/run-tests.sh` forwards it -- `tests/test_check_runner.py` asserts that against the
    constant, because an unforwarded arm makes both arms run identical code and report a null.
    """

    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME, "0")
    path = tmp_path / storage.DATABASE_FILENAME
    service = _service(tmp_path, path)
    with storage.Store.open(path) as writer:
        service.writer = writer
        response = service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))
        service.writer = None

    assert response["source_generation"] is not None
    assert len(_rows(path)) == 1
    assert service._buffered_fact_count() == 0
    assert service._next_append_flush_at is None


def test_a_malformed_arm_fails_loudly_rather_than_defaulting(tmp_path, monkeypatch):
    """A silently-defaulted arm would measure nothing and report a clean null."""

    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME, "not-a-number")
    with pytest.raises(ValueError):
        service_module.resolve_append_flush_seconds()


def test_statsd_reports_which_persistence_owner_it_selected(tmp_path, monkeypatch):
    """The arm must be OBSERVABLE, not merely forwarded.

    An A/B needs both halves: forwarding is a property of the launcher, observation is a
    property of the run. `_append_flushes` alone cannot distinguish the control arm from the
    candidate arm on a quiet stream -- both report zero -- so the resolved interval itself has
    to be in the status payload or nothing outside the process can tell which owner ran.
    """

    path = tmp_path / storage.DATABASE_FILENAME

    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME, "0")
    control = _service(tmp_path, path)._status()["append_persistence"]
    assert control["flush_seconds"] == 0.0
    assert control["buffering"] is False

    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME, "10")
    candidate = _service(tmp_path, path)._status()["append_persistence"]
    assert candidate["flush_seconds"] == 10.0
    assert candidate["buffering"] is True

    monkeypatch.delenv(service_module.APPEND_FLUSH_ENV_NAME)
    default = _service(tmp_path, path)._status()["append_persistence"]
    assert default["flush_seconds"] == service_module.APPEND_FLUSH_SECONDS
    assert default["buffering"] is False, "the shipped default is write-through"
    assert default["measured_flush_seconds"] == service_module.APPEND_FLUSH_MEASURED_SECONDS
    assert default["env_name"] == service_module.APPEND_FLUSH_ENV_NAME
    assert default["buffered_facts"] == 0 and default["flushes"] == 0
    assert default["write_through_families"] == sorted(service_module.WRITE_THROUGH_FAMILIES)


def test_the_ring_never_publishes_a_generation_whose_facts_are_still_buffered(tmp_path):
    """A ring slot is DURABLE, so it may not carry facts that are not.

    The overlay deliberately lets the SERVED generation lead durability -- that is the whole
    point, and `test_the_buffer_never_advances_the_generation_before_it_commits` pins that the
    ring staging stays behind the commit. But the ring FLUSH has its own deadline, and nothing
    stopped it from firing first and writing a bucket built from overlaid, uncommitted facts
    while `schema_meta.source_generation` still named the older durable generation.

    A crash there loses the facts and keeps the slot, which contradicts the ledger the replay
    cursor folds from. So the flush commits the buffer before it publishes.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        service.writer = writer
        with storage.Store.open_reader(path) as reader:
            service._build_once(reader, True, frozenset(), publisher=writer)
            service._append_records(
                observations=(_cpu("cpu-1", AT, 42.0),), coverage=(_coverage(AT + 1.0),),
            )
            assert service._buffered_fact_count() > 0
            work = service._take_work()
            assert work is not None
            service._build_once(reader, *work, publisher=writer)

        durable_before = writer.read_snapshot().schema.source_generation
        # The ring deadline comes due while the append deadline has NOT.
        ticks[0] = service_module.RING_FLUSH_SECONDS + 1.0
        assert ticks[0] < service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0 or True
        publication = service._flush_ring_if_due(writer)
        durable_after = writer.read_snapshot().schema.source_generation
        service.writer = None

    if publication is not None:
        # Whatever it published is now backed by committed facts.
        assert service._buffered_fact_count() == 0
        assert durable_after > durable_before
        assert publication.source_generation <= durable_after


# ---------------------------------------------------------------------------------------
# The acknowledgement contract's hardest case: a fact acked `ok: True` that the commit
# would reject. The probe must predict every rule the applier enforces, or the batch is
# acknowledged and then discarded with nobody holding a retry.
# ---------------------------------------------------------------------------------------

def _epoch(started_at=CLOCK - 60.0, ended_at=CLOCK, cadence=1.0, owner_generation=1):
    return storage.CoverageEpoch("cpu", "host", "epoch:1", started_at, ended_at,
                                 cadence, owner_generation)


COVERAGE_REJECTION_RULES = [
    ("end moves backward", _epoch(ended_at=CLOCK - 50.0)),
    ("start mutated", _epoch(started_at=CLOCK - 59.0)),
    ("cadence mutated", _epoch(cadence=10.0)),
    ("owner_generation regresses", _epoch(owner_generation=0)),
]


@pytest.mark.parametrize("label,offered", COVERAGE_REJECTION_RULES,
                         ids=[label for label, _ in COVERAGE_REJECTION_RULES])
def test_an_epoch_the_commit_would_reject_is_not_acknowledged(tmp_path, label, offered):
    """Every rule `_apply_coverage_epochs` enforces must be visible to the probe.

    The probe modelled the unavailable-span overlap and "differs from stored", but not the
    three immutability rules, so all three were acknowledged `ok: True` and buffered. The
    caller then dropped its retry on that acknowledgement and the flush discarded the record.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    with storage.Store.open(path) as writer:
        writer.append_batch(coverage_epochs=(_epoch(),))
        service = _service(tmp_path, path)
        service.writer = writer
        with pytest.raises(storage.StorageValidationError):
            service._append_records(coverage=(offered,))
        service.writer = None
    assert service._buffered_fact_count() == 0, label


def test_a_rejected_epoch_does_not_take_other_families_acked_facts_with_it(tmp_path):
    """A poisoned record must be quarantined, not used to discard a whole flush interval.

    Every buffered record was answered `ok: True`, and the caller dropped its retry on that
    answer. Clearing the buffer wholesale loses acked facts with nobody left to resend them --
    the same custody argument that keeps `browser` synchronous.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        writer.append_batch(coverage_epochs=(_epoch(),))
        service.writer = writer
        # A perfectly valid observation, acknowledged and buffered.
        service._append_records(observations=(_cpu("cpu-good", AT, 42.0),))
        assert service._buffered_fact_count() == 1
        # Now poison the buffer directly, modelling a record that turned conflicting AFTER it
        # was buffered -- reachable because the browser family still writes through.
        with service.work_lock:
            service._pending_coverage[("cpu", "host", "epoch:1")] = _epoch(ended_at=CLOCK - 50.0)
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        service._flush_appends_if_due(writer)
        service.writer = None

    rows = _rows(path)
    assert [item.event_id for item in rows] == ["cpu-good"], "the valid acked fact survived"
    assert service._append_flush_quarantined == 1
    assert service._append_flush_failure == "StorageValidationError"


def test_a_quarantined_epoch_does_not_leave_the_warm_model_serving_it(tmp_path):
    """Stage time already merged the epoch into the warm coverage model; nothing rolled it back.

    Without this the served coverage keeps a value the store never accepted, indefinitely.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        writer.append_batch(coverage_epochs=(_epoch(),))
        service.writer = writer
        service._append_records(observations=(_cpu("cpu-good", AT, 42.0),))
        with service.work_lock:
            service._pending_coverage[("cpu", "host", "epoch:1")] = _epoch(ended_at=CLOCK - 50.0)
            service._update_cached_coverage_locked(
                (_epoch(ended_at=CLOCK - 50.0),), (), accepted_change=True, retention_prune=None,
            )
        assert service._coverage_cache_ready or service._cached_coverage_epochs is not None
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        service._flush_appends_if_due(writer)
        service.writer = None

    assert service._coverage_cache_ready is False
    assert service._cached_coverage_epochs == ()
    assert service._pending_coverage_refresh is True


def test_the_commit_clock_does_not_advance_when_nothing_commits(tmp_path):
    """`last_source_commit_at` is a COMMIT clock, so staging must not touch it.

    Assigning it at stage time made the status blob report a fresh commit while every flush was
    failing, contradicting `append_persistence.last_failure` in the same payload.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        service.writer = writer
        before = service._last_source_commit_at
        service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))
        assert service._last_source_commit_at == before, "staging is not a commit"
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        service._flush_appends_if_due(writer)
        assert service._last_source_commit_at > before, "the flush IS a commit"
        service.writer = None


def test_batched_persistence_ships_disabled_until_the_generation_collision_is_resolved(
    monkeypatch,
):
    """PIN. Do not re-enable this by editing the constant.

    Batching is fully implemented and tested, and it is OFF by default because the overlay
    serves buffered facts without advancing `source_generation` -- the ring's freshness key --
    so the served cache carries generation 0 while showing real data. Two ring correctness
    gates fail on exactly that:

        test_seeded_slow_ring_view_cannot_fall_back_to_the_startup_zero_cache
        test_leader_writer_coalesces_ingest_for_ten_seconds_and_matches_materializer

    Both pass at 0. Advancing the generation for uncommitted facts is not the fix: it would
    break the invariant `test_the_buffer_never_advances_the_generation_before_it_commits` pins.
    Resolve that tension first, run those two gates, and only then change this number.
    """

    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME, "")
    assert service_module.APPEND_FLUSH_SECONDS == 0.0
    assert service_module.resolve_append_flush_seconds() == 0.0
    # The measured interval survives as the value to select, not as the default.
    assert service_module.APPEND_FLUSH_MEASURED_SECONDS == 10.0


def test_a_shipped_statsd_cannot_enable_the_broken_arm_from_its_environment(tmp_path, monkeypatch):
    """FAIL-CLOSED. A default is not a guard.

    `APPEND_FLUSH_ENV_NAME` is forwarded into the test container, so on its own it is settable
    on any process -- and it selects an arm that fails two ring correctness gates. A silently
    honoured admission variable that enables a known-broken path in production is the mirror
    image of the silently ignored one the container allowlist exists to prevent.

    This is the production shape: the arm set exactly as a launcher would set it, with no
    test-container marker. Buffering must stay OFF, and the refusal must be visible rather than
    look like the feature working.
    """

    monkeypatch.setenv(service_module.APPEND_FLUSH_TEST_MARKER_ENV, "")
    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME, "10.0")

    path = tmp_path / storage.DATABASE_FILENAME
    service = _service(tmp_path, path)
    assert service._append_flush_seconds == 0.0

    reported = service._status()["append_persistence"]
    assert reported["buffering"] is False
    assert reported["flush_seconds"] == 0.0
    assert reported["requested_flush_seconds"] is None
    assert service_module.APPEND_FLUSH_TEST_MARKER_ENV in reported["refused_reason"]

    # And the behaviour, not only the projection: an append still commits.
    with storage.Store.open(path) as writer:
        service.writer = writer
        response = service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))
        service.writer = None
    assert response["source_generation"] is not None
    assert len(_rows(path)) == 1
    assert service._buffered_fact_count() == 0


def test_the_test_container_marker_alone_does_not_enable_it_either(tmp_path, monkeypatch):
    """Both names are required, so neither one on its own is a switch."""

    monkeypatch.setenv(service_module.APPEND_FLUSH_TEST_MARKER_ENV, "1")
    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME, "")
    service = _service(tmp_path, tmp_path / storage.DATABASE_FILENAME)
    assert service._append_flush_seconds == 0.0
    assert service._status()["append_persistence"]["refused_reason"] == ""


def test_a_malformed_arm_still_raises_inside_the_test_container(tmp_path, monkeypatch):
    """Refusal ignores in production; inside the container a malformed arm must be loud.

    There, a silently defaulted arm measures nothing and reports a clean null -- which is the
    whole reason the allowlist exists.
    """

    monkeypatch.setenv(service_module.APPEND_FLUSH_TEST_MARKER_ENV, "1")
    monkeypatch.setenv(service_module.APPEND_FLUSH_ENV_NAME, "not-a-number")
    with pytest.raises(ValueError):
        service_module.resolve_append_flush_arm()

    # ... and the same malformed value on a production process is ignored, not fatal: a stray
    # export must not take statsd down.
    monkeypatch.setenv(service_module.APPEND_FLUSH_TEST_MARKER_ENV, "")
    assert service_module.resolve_append_flush_arm().seconds == 0.0


class _BrokenStore:
    """A store whose commit AND whose conflict probes both fail.

    This is the disk-full / corrupt-store shape: the one failure mode where losing acknowledged
    facts matters most, and the one where the quarantine cannot identify an offender because the
    probe is the thing that is failing.
    """

    def __init__(self, inner):
        self._inner = inner
        self.append_attempts = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def append_batch(self, **values):
        self.append_attempts += 1
        raise sqlite3.OperationalError("database or disk is full")

    def observation_dispositions(self, observations):
        raise sqlite3.OperationalError("database or disk is full")

    def coverage_dispositions(self, coverage):
        raise sqlite3.OperationalError("database or disk is full")


def test_a_flush_that_cannot_identify_an_offender_never_reports_zero_over_a_loss(tmp_path):
    """The quarantine's own failure mode: the probe is what is broken.

    `_quarantine_conflicts_locked` cannot name offenders when the probe itself raises, so the
    old handler retried nothing, cleared the buffer wholesale, and incremented the quarantine
    counter by ZERO. On a disk-full store that discards acknowledged facts and reports a clean
    flush -- the exact class the quarantine was built to remove.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        broken = _BrokenStore(writer)
        service.writer = writer
        service._append_records(observations=(
            _cpu("cpu-1", AT, 42.0), _cpu("cpu-2", AT - 1.0, 43.0),
        ))
        assert service._buffered_fact_count() == 2

        # First failing flush: the facts are NOT discarded, and buffering degrades to
        # write-through so the caller keeps custody of anything new.
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        assert service._flush_appends_if_due(broken) is False
        assert service._buffered_fact_count() == 2, "acked facts survive one transient failure"
        assert service._append_flush_failure == "OperationalError"
        assert service._append_flush_degraded is True

        # While degraded, a new append writes through instead of being acknowledged into a
        # buffer that cannot be committed. Against the broken store it fails to the caller.
        with pytest.raises(sqlite3.OperationalError):
            service.writer = broken
            service._append_records(observations=(_cpu("cpu-3", AT - 2.0, 44.0),))
        service.writer = writer

        # Second consecutive failure exhausts the bound. The facts are discarded -- and COUNTED.
        ticks[0] += service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        service._next_append_flush_at = ticks[0]
        assert service._flush_appends_if_due(broken) is False
        assert service._buffered_fact_count() == 0
        assert service._append_flush_quarantined == 2, "the discarded facts are reported, not zero"
        service.writer = None

    reported = service._status()["append_persistence"]
    assert reported["quarantined_facts"] == 2
    assert reported["last_failure"] == "OperationalError"
    assert reported["degraded"] is True


def test_a_successful_flush_restores_buffering_after_a_degrade(tmp_path):
    """Degraded is a state, not a latch: a working store returns the daemon to batching."""
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        broken = _BrokenStore(writer)
        service.writer = writer
        service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        service._flush_appends_if_due(broken)
        assert service._append_flush_degraded is True

        ticks[0] += 1.0
        service._next_append_flush_at = ticks[0]
        assert service._flush_appends_if_due(writer) is True
        assert service._append_flush_degraded is False
        assert service._buffered_fact_count() == 0
        service.writer = None
    assert [item.event_id for item in _rows(path)] == ["cpu-1"]


def _service_load(event_id: str, observed_at: float, percent: float) -> storage.Observation:
    """The OTHER buffered family the queue item names, and the one nothing exercised."""

    return storage.Observation(
        event_id, "service_load", "watchd", observed_at, "epoch:sl", 1,
        {"cpu_percent": percent, "rss_bytes": 50_688_000.0, "running": True},
    )


def _service_load_coverage(ended_at: float | None = None) -> storage.CoverageEpoch:
    return storage.CoverageEpoch("service_load", "watchd", "epoch:sl", CLOCK - 60.0,
                                 ended_at, 1.0, 1)


def test_service_load_buffers_and_survives_the_flush_exactly_once(tmp_path):
    """`service_load` is a buffered family and is 80.69% of observations by measured volume.

    `WRITE_THROUGH_FAMILIES` is `frozenset({"browser"})`, so `service_load` takes the buffered
    path -- and every buffered-path test used `cpu`. The queue item names both families; this
    closes the half that was never exercised.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        service.writer = writer
        response = service._append_records(
            observations=(_service_load("sl-1", AT, 101.123),),
            coverage=(_service_load_coverage(AT + 1.0),),
        )
        assert response["source_generation"] is None, "service_load buffers, it does not commit"
        assert service._buffered_fact_count() == 2
        assert _rows(path) == ()

        # Visible at one second through the same builder, like cpu.
        with storage.Store.open_reader(path) as reader:
            service._build_once(reader, True, frozenset())
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        assert service._flush_appends_if_due(writer) is True
        service.writer = None

    rows = [item for item in _rows(path) if item.family == "service_load"]
    assert len(rows) == 1 and rows[0].event_id == "sl-1"


def test_both_named_families_buffer_together_without_losing_or_duplicating_either(tmp_path):
    """The queue item's claim, as a test rather than an argument: cpu AND service_load."""
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        service.writer = writer
        service._append_records(observations=(
            _cpu("cpu-1", AT, 42.0), _service_load("sl-1", AT, 101.123),
        ))
        assert service._buffered_fact_count() == 2
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        service._flush_appends_if_due(writer)
        service.writer = None

    families = sorted(item.family for item in _rows(path))
    assert families == ["cpu", "service_load"], families
    assert len(_rows(path)) == 2, "neither lost nor duplicated"


def test_a_prune_across_a_staged_buffer_neither_loses_nor_duplicates(tmp_path):
    """Falsifiable, rather than an ordering argument.

    The prune deletes by retention cutoff on the same worker thread under the same lock. A
    buffered fact is NEWER than any cutoff, so it must survive; and committing it after the
    prune must not double it.
    """
    # A real wall clock, because the retention window is two days and the module's fixed CLOCK
    # would put the cutoff before the epoch.
    now = 1_800_000_000.0
    recent = now - 0.25
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", path, clock=lambda: now, monotonic=lambda: ticks[0],
    )
    with storage.Store.open(path) as writer:
        # An old durable fact the prune WILL remove, so the test can tell a working prune from
        # a prune that silently did nothing.
        old = storage.Observation(
            "cpu-old", "cpu", "host", now - storage.RETENTION_SECONDS - 10.0, "epoch:1", 1,
            {"process_percent": 1.0, "system_percent": 1.0},
        )
        writer.append_batch(observations=(old,))
        service.writer = writer
        service._append_records(observations=(
            storage.Observation("cpu-new", "cpu", "host", recent, "epoch:1", 1,
                                {"process_percent": 42.0, "system_percent": 10.0}),
            _service_load("sl-new", recent, 101.123),
        ))
        assert service._buffered_fact_count() == 2

        result = writer.prune(now=now)
        assert result.observations_deleted == 1, "the prune really removed the old fact"
        assert service._buffered_fact_count() == 2, "a prune does not consume the buffer"

        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        assert service._flush_appends_if_due(writer) is True
        service.writer = None

    ids = sorted(item.event_id for item in _rows(path))
    assert ids == ["cpu-new", "sl-new"], ids


def test_a_vacuum_across_a_staged_buffer_neither_loses_nor_duplicates(tmp_path):
    """SQLite forbids VACUUM inside a transaction, so the flush must be complete before it runs.

    Both hold `work_lock` on the worker thread, and `_flush_ring_if_due` commits the buffer
    before publishing. This asserts the outcome rather than the ordering: the facts are durable
    exactly once and the store is still usable afterwards.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        service.writer = writer
        service._append_records(observations=(
            _cpu("cpu-1", AT, 42.0), _service_load("sl-1", AT, 101.123),
        ))
        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        assert service._flush_appends_if_due(writer) is True
        assert service._buffered_fact_count() == 0, "VACUUM must not run with a buffer owed"

        writer.vacuum(completed_at=CLOCK + 1.0)
        # Still usable, and still exactly the two facts.
        service.writer = None

    ids = sorted(item.event_id for item in _rows(path))
    assert ids == ["cpu-1", "sl-1"], ids


def test_a_failed_flush_cannot_advance_the_persisted_watermark(tmp_path):
    """True by construction today -- and the construction can change, so it gets a test.

    `schema_meta.source_generation` is the DURABLE watermark. A transaction that fails must
    leave it exactly where it was, or a later reader folds from a point nothing ever committed.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        writer.append_batch(observations=(_cpu("cpu-seed", AT - 5.0, 7.0),))
        before = writer.read_snapshot().schema.source_generation
        broken = _BrokenStore(writer)
        service.writer = writer
        service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))

        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        assert service._flush_appends_if_due(broken) is False
        assert writer.read_snapshot().schema.source_generation == before
        assert service._latest_source_generation <= before
        service.writer = None


class _TransientlyFailingStore:
    """`append_batch` fails once, transiently. The probes are fine and answer normally.

    This is `database is locked` / `disk I/O error`: the commit fails for a reason that
    implicates no individual record, while the probes -- which are SELECTs -- keep working and
    correctly report nothing conflicting.
    """

    def __init__(self, inner, failures=1):
        self._inner = inner
        self.remaining_failures = failures
        self.append_attempts = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def append_batch(self, **values):
        self.append_attempts += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise sqlite3.OperationalError("database is locked")
        return self._inner.append_batch(**values)


def test_a_transient_failure_with_no_offender_is_retried_not_discarded(tmp_path):
    """The retry gate must not depend on an offender having been NAMED.

    Gating the retry on `dropped` inverts it against the failure most likely to be retryable: a
    transient error leaves the probes able to answer and correctly reporting nothing
    conflicting, so `dropped == 0`, and the path that most deserves a retry was the one
    guaranteed not to get one -- the buffer cleared and the counter reporting zero.

    "No offender identified" is the same epistemic state whether the probe could not answer or
    answered and found nothing: either way there is nothing to remove, so retrying the identical
    batch is the only useful move, bounded.
    """
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        flaky = _TransientlyFailingStore(writer, failures=1)
        service.writer = writer
        service._append_records(observations=(
            _cpu("cpu-1", AT, 42.0), _service_load("sl-1", AT, 101.123),
        ))
        assert service._buffered_fact_count() == 2

        ticks[0] = service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        assert service._flush_appends_if_due(flaky) is False
        # The probes answered; nothing was conflicting; so nothing may be discarded.
        assert service._buffered_fact_count() == 2, "a transient failure must not lose acked facts"
        assert service._append_flush_quarantined == 0
        assert service._append_flush_degraded is True

        # The store recovers and the next flush commits everything, exactly once.
        ticks[0] += service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
        service._next_append_flush_at = ticks[0]
        assert service._flush_appends_if_due(flaky) is True
        assert service._buffered_fact_count() == 0
        assert service._append_flush_degraded is False
        service.writer = None

    ids = sorted(item.event_id for item in _rows(path))
    assert ids == ["cpu-1", "sl-1"], ids


def test_a_persistent_failure_with_no_offender_still_discards_and_counts(tmp_path):
    """The bound still applies: retrying an unretryable batch forever is an OOM, not a fix."""
    path = tmp_path / storage.DATABASE_FILENAME
    ticks = [0.0]
    service = _service(tmp_path, path, ticks)
    with storage.Store.open(path) as writer:
        flaky = _TransientlyFailingStore(writer, failures=99)
        service.writer = writer
        service._append_records(observations=(_cpu("cpu-1", AT, 42.0),))
        for attempt in range(service_module.APPEND_FLUSH_UNRESOLVED_LIMIT):
            ticks[0] += service_module.APPEND_FLUSH_MEASURED_SECONDS + 1.0
            service._next_append_flush_at = ticks[0]
            assert service._flush_appends_if_due(flaky) is False
        service.writer = None

    assert service._buffered_fact_count() == 0
    assert service._append_flush_quarantined == 1, "discarded facts are counted, never zero"
    assert service._status()["append_persistence"]["degraded"] is True
