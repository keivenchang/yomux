# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""M5 of DOIT.p0.daemon-monitor: the port-scoped retained backend-health history store.

Every test here drives `BackendHealthStore` directly with an injected wall clock and an
injected writer. Nothing sleeps, nothing starts a service, nothing probes: the store's whole
job is to turn an already-built snapshot into bounded durable history, and these tests pin
the parts of that job that silently rot -- delta accounting across a restart, coverage
honesty, the 128-row bound, corrupt-file handling, diagnostic deduplication, redaction, and
the rule that a failed write is never reported as a success.
"""

from __future__ import annotations

import json
import os
from itertools import count
from pathlib import Path

import pytest

from yolomux_lib.backend_health import store as store_module
from yolomux_lib.backend_health.store import AGGREGATE_COVERAGE_FULL
from yolomux_lib.backend_health.store import AGGREGATE_COVERAGE_PARTIAL
from yolomux_lib.backend_health.store import BACKEND_HEALTH_MAX_RESOURCES
from yolomux_lib.backend_health.store import BACKEND_HEALTH_MAX_TRANSITIONS
from yolomux_lib.backend_health.store import BACKEND_HEALTH_REASON_CODES
from yolomux_lib.backend_health.store import BACKEND_HEALTH_RECOVERY_OUTCOMES
from yolomux_lib.backend_health.store import BACKEND_HEALTH_SCHEMA_VERSION
from yolomux_lib.backend_health.store import BACKEND_HEALTH_STATES
from yolomux_lib.backend_health.store import COVERAGE_REASON_CORRUPT_COUNTERS
from yolomux_lib.backend_health.store import COVERAGE_REASON_MISSED_FINAL
from yolomux_lib.backend_health.store import COVERAGE_REASON_ROLLBACK
from yolomux_lib.backend_health.store import DIAGNOSTIC_HISTORY_RESET
from yolomux_lib.backend_health.store import DIAGNOSTIC_PEER_COUNTERS_INVALID
from yolomux_lib.backend_health.store import DIAGNOSTIC_PERSIST_FAILED
from yolomux_lib.backend_health.store import DIAGNOSTIC_REASON_CODE_INVALID
from yolomux_lib.backend_health.store import DIAGNOSTIC_RESOURCE_LIMIT
from yolomux_lib.backend_health.store import DIAGNOSTIC_WRITER_CONFLICT
from yolomux_lib.backend_health.store import HISTORY_COVERAGE_FULL
from yolomux_lib.backend_health.store import HISTORY_COVERAGE_RESET
from yolomux_lib.backend_health.store import PERSISTENCE_BLOCKED
from yolomux_lib.backend_health.store import PERSISTENCE_DEGRADED
from yolomux_lib.backend_health.store import PERSISTENCE_OK
from yolomux_lib.backend_health.store import RESET_HISTORY_CORRUPT
from yolomux_lib.backend_health.store import RESET_HISTORY_PORT_MISMATCH
from yolomux_lib.backend_health.store import RESET_HISTORY_SCHEMA_UNSUPPORTED
from yolomux_lib.backend_health.store import RESET_HISTORY_UNREADABLE
from yolomux_lib.backend_health.store import TRANSITION_ROW_FIELDS
from yolomux_lib.backend_health.store import UNVERIFIED_PROCESS_EPOCH
from yolomux_lib.backend_health.store import BackendHealthContractError
from yolomux_lib.backend_health.store import BackendHealthStore
from yolomux_lib.backend_health.store import HealthSnapshot
from yolomux_lib.backend_health.store import PublishResult
from yolomux_lib.backend_health.store import ResourceObservation
from yolomux_lib.backend_health.store import WriterIdentity
from yolomux_lib.backend_health.store import _PROCESS_EPOCH_RE
from yolomux_lib.backend_health.store import _REASON_CODE_RE
from yolomux_lib.backend_health.store import process_epoch_token
from yolomux_lib.infra.atomic_file import atomic_write_text


PORT = 7771
OTHER_PORT = 7772
EPOCH_A = ("proc:98", 4242)
EPOCH_B = ("proc:200", 5000)


class FakeClock:
    """One injected wall clock. Tests advance it explicitly; nothing here ever sleeps."""

    def __init__(self, value: float = 1_700_000_000.0) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += float(seconds)
        return self.value


class FailingWriter:
    """A writer that fails on demand, so a persistence defect is provable without a full disk."""

    def __init__(self, *, failures: int = 0, error: type[OSError] = OSError) -> None:
        self.remaining_failures = failures
        self.error = error
        self.calls = 0
        self.observed_before_write: list[str | None] = []

    def __call__(self, path: Path, text: str, mode: int | None = None) -> None:
        self.calls += 1
        self.observed_before_write.append(path.read_text(encoding="utf-8") if path.exists() else None)
        if self.remaining_failures:
            self.remaining_failures -= 1
            raise self.error("injected persistence failure")
        atomic_write_text(path, text, mode)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def epoch_ids():
    counter = count(1)
    return lambda: f"{next(counter):016x}"


def writer_identity(pid: int = 11, ticks: int = 5, host: str = "host-a", boot: str = "boot-a") -> WriterIdentity:
    return WriterIdentity(
        stable_host_id=host,
        boot_id=boot,
        pid=pid,
        process_epoch=process_epoch_token(pid, f"proc:{ticks}"),
    )


def build_store(
    tmp_path: Path,
    clock: FakeClock,
    epoch_ids,
    *,
    port: int = PORT,
    identity: WriterIdentity | None = None,
    writer=atomic_write_text,
    peer_is_live=lambda pid, epoch: False,
    diagnostics: list | None = None,
) -> BackendHealthStore:
    return BackendHealthStore(
        port,
        state_dir=tmp_path,
        writer_identity=identity or writer_identity(),
        clock=clock,
        writer=writer,
        peer_is_live=peer_is_live,
        on_diagnostic=None if diagnostics is None else diagnostics.append,
        new_epoch_id=epoch_ids,
    )


def observation(
    *,
    resource: str = "statsd",
    state: str = "ready",
    reason_code: str = "none",
    recovery_outcome: str = "none",
    epoch: tuple[str, int] | None = EPOCH_A,
    counters_available: bool = True,
    requests: float = 0.0,
    errors: float = 0.0,
    completed: float = 0.0,
    latency_total_ms: float = 0.0,
    latency_max_ms: float = 0.0,
) -> ResourceObservation:
    identity, pid = ("", 0) if epoch is None else epoch
    return ResourceObservation(
        resource=resource,
        state=state,
        reason_code=reason_code,
        recovery_outcome=recovery_outcome,
        pid=pid,
        process_start_identity=identity,
        counters_available=counters_available and epoch is not None,
        request_count=requests,
        error_count=errors,
        completed_count=completed,
        latency_total_ms=latency_total_ms,
        latency_max_ms=latency_max_ms,
    )


def publish(store: BackendHealthStore, clock: FakeClock, *observations: ResourceObservation) -> PublishResult:
    clock.advance(2.0)
    return store.record(HealthSnapshot(observed_at=clock.value, resources=tuple(observations)))


def aggregate_of(document: dict, resource: str = "statsd") -> dict:
    return document["resources"][resource]["aggregate"]


def transitions_of(document: dict, resource: str = "statsd") -> list[dict]:
    return document["resources"][resource]["transitions"]


# -- atomic publication -------------------------------------------------------


def test_publication_replaces_the_whole_document_and_leaves_no_partial_file(tmp_path, clock, epoch_ids):
    writer = FailingWriter()
    store = build_store(tmp_path, clock, epoch_ids, writer=writer)

    first = publish(store, clock, observation(state="starting", epoch=None))
    second = publish(store, clock, observation(state="ready", requests=3, completed=3))

    assert (first.published, first.revision) == (True, 1)
    assert (second.published, second.revision) == (True, 2)
    # The previous revision was still complete on disk at the instant the next write began:
    # the document is replaced, never truncated in place.
    assert writer.observed_before_write[0] is None
    assert json.loads(writer.observed_before_write[1])["revision"] == 1
    assert json.loads(store.document_path.read_text(encoding="utf-8")) == second.document
    assert sorted(entry.name for entry in store.directory.iterdir()) == [".7771.json.lock", "7771.json"]
    assert store.status()["persistence"]["state"] == PERSISTENCE_OK


def test_revision_increases_monotonically_and_survives_a_web_restart(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    revisions = [publish(store, clock, observation(state=state)).revision for state in ("starting", "ready", "degraded")]
    assert revisions == [1, 2, 3]
    first_epoch = store.document()["observer_epoch"]

    restarted = build_store(tmp_path, clock, epoch_ids, identity=writer_identity(pid=99, ticks=7))
    assert restarted.document()["revision"] == 3
    assert restarted.document()["observer_epoch"] == first_epoch
    assert restarted.document()["history_coverage"] == HISTORY_COVERAGE_FULL

    resumed = publish(restarted, clock, observation(state="ready"))
    assert resumed.revision == 4
    assert resumed.document["writer"]["pid"] == 99
    assert [row["new_state"] for row in transitions_of(resumed.document)] == [
        "starting",
        "ready",
        "degraded",
        "ready",
    ]


# -- single-writer lease identity ---------------------------------------------


def test_a_live_foreign_writer_blocks_publication_until_that_writer_is_gone(tmp_path, clock, epoch_ids):
    leaseholder = build_store(tmp_path, clock, epoch_ids)
    publish(leaseholder, clock, observation(state="ready"))
    durable = json.loads(leaseholder.document_path.read_text(encoding="utf-8"))

    peer_alive = {"value": True}
    diagnostics: list = []
    intruder = build_store(
        tmp_path,
        clock,
        epoch_ids,
        identity=writer_identity(pid=12, ticks=6),
        peer_is_live=lambda pid, epoch: peer_alive["value"] and pid == 11,
        diagnostics=diagnostics,
    )

    blocked = publish(intruder, clock, observation(state="down", reason_code="service_absent"))
    assert blocked.published is False
    assert (blocked.persistence_state, blocked.reason_code) == (PERSISTENCE_BLOCKED, "writer_conflict")
    assert json.loads(intruder.document_path.read_text(encoding="utf-8")) == durable
    assert intruder.status()["persistence"]["state"] == PERSISTENCE_BLOCKED
    assert [entry.code for entry in diagnostics] == [DIAGNOSTIC_WRITER_CONFLICT]

    # A second blocked cycle must not become a second diagnostic, and must not write.
    publish(intruder, clock, observation(state="down", reason_code="service_absent"))
    assert [entry.code for entry in diagnostics] == [DIAGNOSTIC_WRITER_CONFLICT]
    assert json.loads(intruder.document_path.read_text(encoding="utf-8")) == durable

    peer_alive["value"] = False
    adopted = publish(intruder, clock, observation(state="down", reason_code="service_absent"))
    assert adopted.published is True
    assert adopted.revision == 2
    assert adopted.document["writer"]["pid"] == 12
    assert adopted.document["observer_epoch"] == durable["observer_epoch"]
    assert intruder.status()["persistence"]["state"] == PERSISTENCE_OK


def test_every_published_document_names_its_writer_and_only_its_own_port(tmp_path, clock, epoch_ids):
    mine = build_store(tmp_path, clock, epoch_ids, port=PORT)
    theirs = build_store(tmp_path, clock, epoch_ids, port=OTHER_PORT, identity=writer_identity(pid=12, ticks=6))

    publish(mine, clock, observation(state="ready"))
    for _ in range(3):
        publish(theirs, clock, observation(state="down", reason_code="service_absent"))

    assert mine.document_path.name == "7771.json"
    assert theirs.document_path.name == "7772.json"
    assert mine.document()["port"] == PORT and theirs.document()["port"] == OTHER_PORT
    assert mine.document()["revision"] == 1 and theirs.document()["revision"] == 3
    assert mine.document()["writer"] == writer_identity().as_dict()
    assert mine.document()["resources"]["statsd"]["current"]["state"] == "ready"
    assert mine.load()["resources"]["statsd"]["current"]["state"] == "ready"


def test_another_ports_document_is_rejected_rather_than_adopted(tmp_path, clock, epoch_ids):
    foreign = build_store(tmp_path, clock, epoch_ids, port=OTHER_PORT)
    publish(foreign, clock, observation(state="ready"))
    (tmp_path / "backend-health" / "7771.json").write_text(
        foreign.document_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    diagnostics: list = []
    store = build_store(tmp_path, clock, epoch_ids, diagnostics=diagnostics)
    assert store.document()["history_coverage"] == HISTORY_COVERAGE_RESET
    assert store.document()["history_reset_reason"] == RESET_HISTORY_PORT_MISMATCH
    assert store.document()["resources"] == {}
    assert [(entry.code, entry.detail_code) for entry in diagnostics] == [
        (DIAGNOSTIC_HISTORY_RESET, RESET_HISTORY_PORT_MISMATCH)
    ]


# -- restart delta accounting and process-epoch reset -------------------------


def test_restart_accounting_adds_both_epochs_and_never_emits_a_negative_delta(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)

    publish(store, clock, observation(requests=10, completed=10, errors=1, latency_total_ms=100.0, latency_max_ms=30.0))
    publish(store, clock, observation(requests=25, completed=20, errors=2, latency_total_ms=260.0, latency_max_ms=45.0))
    # The process is replaced between two observations, so the last sample of the old epoch
    # was readable and the store claims nothing lost.
    after_restart = publish(
        store,
        clock,
        observation(epoch=EPOCH_B, requests=3, completed=3, errors=0, latency_total_ms=30.0, latency_max_ms=12.0),
    )

    aggregate = aggregate_of(after_restart.document)
    assert aggregate["restart_count"] == 1
    assert aggregate["verified_epochs"] == 2
    # 25 from the dead process plus 3 from the new one. The lower absolute counter of the
    # new process must never subtract from the total.
    assert aggregate["request_count"] == 28
    assert aggregate["completed_count"] == 23
    assert aggregate["error_count"] == 2
    assert aggregate["latency_total_ms"] == 290.0
    assert aggregate["latency_average_ms"] == round(290.0 / 23, 3)
    # The maximum belongs to the declared epoch, so it resets with the epoch.
    assert aggregate["epoch_latency_max_ms"] == 12.0
    assert aggregate["epoch_latency_max_process_epoch"] == process_epoch_token(EPOCH_B[1], EPOCH_B[0])
    assert all(value >= 0 for value in (aggregate["request_count"], aggregate["completed_count"], aggregate["error_count"]))
    assert aggregate["latency_total_ms"] >= 0.0
    # The final observation of epoch A carried counters, so nothing is claimed lost.
    assert aggregate["coverage"] == AGGREGATE_COVERAGE_FULL


def test_a_restart_seen_through_a_blind_observation_is_reported_as_partial(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(requests=25, completed=20, errors=2, latency_total_ms=260.0))
    # The realistic restart: the observer sees the service down, with no verified process at
    # all, before the replacement appears. Whatever the dead process served after its last
    # readable sample is unmeasurable, and the store says so instead of guessing.
    publish(store, clock, observation(state="down", reason_code="exited", epoch=None))
    after_restart = publish(store, clock, observation(epoch=EPOCH_B, requests=3, completed=3, latency_total_ms=30.0))

    aggregate = aggregate_of(after_restart.document)
    assert aggregate["restart_count"] == 1
    assert aggregate["request_count"] == 28
    assert aggregate["coverage"] == AGGREGATE_COVERAGE_PARTIAL
    assert aggregate["coverage_reasons"] == [COVERAGE_REASON_MISSED_FINAL]


def test_the_first_verified_process_is_a_baseline_and_never_a_restart(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)

    absent = publish(store, clock, observation(state="down", reason_code="service_absent", epoch=None))
    assert aggregate_of(absent.document)["restart_count"] == 0
    assert aggregate_of(absent.document)["verified_epochs"] == 0
    assert absent.document["resources"]["statsd"]["current"]["process_epoch"] == UNVERIFIED_PROCESS_EPOCH

    first = publish(store, clock, observation(state="starting", requests=0))
    assert aggregate_of(first.document)["restart_count"] == 0
    assert aggregate_of(first.document)["verified_epochs"] == 1

    same_epoch_again = publish(store, clock, observation(requests=4, completed=4))
    assert aggregate_of(same_epoch_again.document)["restart_count"] == 0
    assert aggregate_of(same_epoch_again.document)["request_count"] == 4

    replaced = publish(store, clock, observation(epoch=EPOCH_B, requests=1, completed=1))
    assert aggregate_of(replaced.document)["restart_count"] == 1


def test_a_resumed_epoch_after_a_web_restart_counts_no_restart_and_no_double_delta(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(requests=10, completed=10, latency_total_ms=50.0))
    publish(store, clock, observation(requests=40, completed=40, latency_total_ms=200.0))
    assert aggregate_of(store.document())["request_count"] == 40

    restarted = build_store(tmp_path, clock, epoch_ids, identity=writer_identity(pid=99, ticks=7))
    resumed = publish(restarted, clock, observation(requests=45, completed=44, latency_total_ms=230.0))

    aggregate = aggregate_of(resumed.document)
    assert aggregate["restart_count"] == 0
    assert aggregate["verified_epochs"] == 1
    assert aggregate["request_count"] == 45
    assert aggregate["completed_count"] == 44
    assert aggregate["latency_total_ms"] == 230.0
    assert aggregate["coverage"] == AGGREGATE_COVERAGE_FULL


# -- partial coverage ---------------------------------------------------------


def test_a_counter_rollback_marks_partial_coverage_and_contributes_zero(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(requests=30, completed=30, errors=3, latency_total_ms=300.0))
    rolled_back = publish(store, clock, observation(requests=4, completed=4, errors=0, latency_total_ms=40.0))

    aggregate = aggregate_of(rolled_back.document)
    # The delta is 4 - 30. Applying it would move the cumulative total backwards, which is
    # the one arithmetic result this store may never produce, so it contributes zero.
    assert (aggregate["request_count"], aggregate["completed_count"], aggregate["error_count"]) == (30, 30, 3)
    assert aggregate["latency_total_ms"] == 300.0
    assert aggregate["coverage"] == AGGREGATE_COVERAGE_PARTIAL
    assert aggregate["coverage_reasons"] == [COVERAGE_REASON_ROLLBACK]

    # Accounting continues from the rolled-back baseline, so later work is still counted.
    resumed = publish(store, clock, observation(requests=9, completed=9, errors=1, latency_total_ms=90.0))
    assert aggregate_of(resumed.document)["request_count"] == 35
    assert aggregate_of(resumed.document)["coverage"] == AGGREGATE_COVERAGE_PARTIAL


def test_a_missed_final_sample_marks_partial_coverage_at_the_epoch_boundary(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(requests=12, completed=12))
    blind = publish(store, clock, observation(state="degraded", reason_code="probe_timeout", counters_available=False))
    assert aggregate_of(blind.document)["coverage"] == AGGREGATE_COVERAGE_FULL

    # Same epoch, counters readable again: a cumulative counter loses nothing while blind.
    recovered = publish(store, clock, observation(requests=20, completed=20))
    assert aggregate_of(recovered.document)["request_count"] == 20
    assert aggregate_of(recovered.document)["coverage"] == AGGREGATE_COVERAGE_FULL

    lost = publish(store, clock, observation(state="unknown", reason_code="observation_failed", counters_available=False))
    replaced = publish(store, clock, observation(epoch=EPOCH_B, requests=2, completed=2))
    aggregate = aggregate_of(replaced.document)
    assert aggregate["restart_count"] == 1
    assert aggregate["coverage"] == AGGREGATE_COVERAGE_PARTIAL
    assert aggregate["coverage_reasons"] == [COVERAGE_REASON_MISSED_FINAL]
    assert aggregate["request_count"] == 22
    assert aggregate_of(lost.document)["coverage"] == AGGREGATE_COVERAGE_FULL


@pytest.mark.parametrize(
    "corrupt_counters",
    [
        {"requests": -5},
        {"completed": float("inf")},
        {"latency_total_ms": float("nan")},
        {"errors": 2.5},
    ],
)
def test_corrupt_peer_counters_mark_partial_coverage_without_inventing_a_count(
    tmp_path, clock, epoch_ids, corrupt_counters
):
    diagnostics: list = []
    store = build_store(tmp_path, clock, epoch_ids, diagnostics=diagnostics)
    publish(store, clock, observation(requests=7, completed=7, errors=1, latency_total_ms=70.0))
    corrupted_values = {"requests": 7, "completed": 7, "errors": 1, "latency_total_ms": 70.0, **corrupt_counters}
    corrupted = publish(store, clock, observation(**corrupted_values))

    aggregate = aggregate_of(corrupted.document)
    assert aggregate["coverage"] == AGGREGATE_COVERAGE_PARTIAL
    assert aggregate["coverage_reasons"] == [COVERAGE_REASON_CORRUPT_COUNTERS]
    assert (aggregate["request_count"], aggregate["completed_count"], aggregate["error_count"]) == (7, 7, 1)
    assert aggregate["latency_total_ms"] == 70.0
    assert [entry.code for entry in diagnostics] == [DIAGNOSTIC_PEER_COUNTERS_INVALID]


def test_a_history_reset_is_reported_as_reset_coverage_for_the_new_observer_epoch(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(state="ready"))
    store.document_path.write_text("{ this is not json", encoding="utf-8")

    reopened = build_store(tmp_path, clock, epoch_ids)
    assert reopened.document()["history_coverage"] == HISTORY_COVERAGE_RESET
    assert reopened.document()["history_reset_reason"] == RESET_HISTORY_CORRUPT
    assert reopened.document()["observer_epoch"] != store.document()["observer_epoch"]

    after = publish(reopened, clock, observation(state="ready"))
    assert after.revision == 1
    assert after.document["history_coverage"] == HISTORY_COVERAGE_RESET
    assert after.document["history_reset_reason"] == RESET_HISTORY_CORRUPT


# -- 128-row eviction ---------------------------------------------------------


def test_transitions_evict_the_oldest_row_and_never_exceed_the_bound(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    for index in range(BACKEND_HEALTH_MAX_TRANSITIONS):
        published = publish(store, clock, observation(state="ready" if index % 2 else "degraded"))
        assert len(transitions_of(published.document)) == index + 1

    full = transitions_of(store.document())
    assert len(full) == BACKEND_HEALTH_MAX_TRANSITIONS
    oldest, second_oldest = full[0], full[1]
    assert oldest["revision"] == 1
    assert full[-1]["new_state"] == "ready"

    # The 129th transition evicts the first one and the list still holds exactly 128 rows.
    overflowed = publish(store, clock, observation(state="degraded", reason_code="overload"))
    rows = transitions_of(overflowed.document)
    assert len(rows) == BACKEND_HEALTH_MAX_TRANSITIONS
    assert oldest not in rows
    assert rows[0] == second_oldest
    assert rows[-1]["revision"] == BACKEND_HEALTH_MAX_TRANSITIONS + 1
    assert rows[-1]["new_state"] == "degraded"

    for index in range(40):
        published = publish(store, clock, observation(state="ready" if index % 2 else "down", reason_code="exited"))
        assert len(transitions_of(published.document)) <= BACKEND_HEALTH_MAX_TRANSITIONS
    assert len(transitions_of(json.loads(store.document_path.read_text(encoding="utf-8")))) == BACKEND_HEALTH_MAX_TRANSITIONS
    # The bound is per resource, and a second resource keeps its own independent history.
    publish(store, clock, observation(resource="jobd", state="ready"), observation(state="ready"))
    assert len(transitions_of(store.document(), "jobd")) == 1


def test_a_repeated_state_writes_a_revision_but_no_transition_row(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(state="ready"))
    steady = publish(store, clock, observation(state="ready"))
    assert steady.revision == 2
    assert len(transitions_of(steady.document)) == 1
    assert steady.document["resources"]["statsd"]["current"]["since_revision"] == 1


# -- schema, corruption, quarantine -------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ({"schema_version": BACKEND_HEALTH_SCHEMA_VERSION + 1}, RESET_HISTORY_SCHEMA_UNSUPPORTED),
        ({"schema_version": "1"}, RESET_HISTORY_SCHEMA_UNSUPPORTED),
        ({"revision": -3}, RESET_HISTORY_CORRUPT),
        ({"observer_epoch": "not hex"}, RESET_HISTORY_CORRUPT),
        ({"history_coverage": "mostly"}, RESET_HISTORY_CORRUPT),
        ({"writer": {"stable_host_id": "host-a"}}, RESET_HISTORY_CORRUPT),
        ({"resources": []}, RESET_HISTORY_CORRUPT),
    ],
)
def test_an_unsupported_or_malformed_document_is_never_trusted_silently(
    tmp_path, clock, epoch_ids, mutation, expected_reason
):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(state="ready"))
    document = json.loads(store.document_path.read_text(encoding="utf-8"))
    document.update(mutation)
    store.document_path.write_text(json.dumps(document), encoding="utf-8")

    diagnostics: list = []
    reopened = build_store(tmp_path, clock, epoch_ids, diagnostics=diagnostics)
    assert reopened.document()["history_reset_reason"] == expected_reason
    assert reopened.document()["history_coverage"] == HISTORY_COVERAGE_RESET
    assert reopened.document()["revision"] == 0
    assert reopened.document()["resources"] == {}
    assert [(entry.code, entry.detail_code) for entry in diagnostics] == [
        (DIAGNOSTIC_HISTORY_RESET, expected_reason)
    ]


def test_a_transition_row_with_unexpected_fields_rejects_the_document(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(state="ready"))
    document = json.loads(store.document_path.read_text(encoding="utf-8"))
    document["resources"]["statsd"]["transitions"][0]["command_line"] = "python -m yolomux_lib.statsd --serve"
    store.document_path.write_text(json.dumps(document), encoding="utf-8")

    reopened = build_store(tmp_path, clock, epoch_ids)
    assert reopened.document()["history_reset_reason"] == RESET_HISTORY_CORRUPT
    assert reopened.document()["resources"] == {}


def test_corruption_keeps_exactly_one_bounded_quarantine_copy(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(state="ready"))

    first_corrupt_document = "first corruption"
    store.document_path.write_text(first_corrupt_document, encoding="utf-8")
    first = build_store(tmp_path, clock, epoch_ids)
    # One copy from the first corruption too, or "exactly one" below would pass while the
    # store accumulated a copy per reopen and merely happened to reuse one name.
    assert sorted(path.name for path in first.directory.glob("*.quarantine*")) == ["7771.json.quarantine"]
    assert first.quarantine_path.read_text(encoding="utf-8") == first_corrupt_document

    publish(first, clock, observation(state="ready"))
    store.document_path.write_text("second corruption " + "x" * 200_000, encoding="utf-8")
    second = build_store(tmp_path, clock, epoch_ids)

    quarantine_files = sorted(path.name for path in second.directory.glob("*.quarantine*"))
    assert quarantine_files == ["7771.json.quarantine"]
    quarantined = second.quarantine_path.read_text(encoding="utf-8")
    assert quarantined.startswith("second corruption")
    assert len(quarantined) == 64 * 1024
    assert second.document()["history_reset_reason"] == RESET_HISTORY_CORRUPT


# -- deduplicated diagnostics -------------------------------------------------


def test_a_repeated_corrupt_read_emits_one_diagnostic_not_one_per_read(tmp_path, clock, epoch_ids):
    diagnostics: list = []
    store = build_store(tmp_path, clock, epoch_ids, diagnostics=diagnostics)
    for _ in range(3):
        store.document_path.write_text("{{{", encoding="utf-8")
        clock.advance(2.0)
        store.load()

    assert [entry.code for entry in diagnostics] == [DIAGNOSTIC_HISTORY_RESET]
    assert [entry.occurrences for entry in diagnostics] == [1]
    episodes = {entry.code: entry for entry in store.diagnostics()}
    assert episodes[DIAGNOSTIC_HISTORY_RESET].occurrences == 3
    assert episodes[DIAGNOSTIC_HISTORY_RESET].detail_code == RESET_HISTORY_CORRUPT
    assert episodes[DIAGNOSTIC_HISTORY_RESET].first_wall_time < episodes[DIAGNOSTIC_HISTORY_RESET].last_wall_time
    assert episodes[DIAGNOSTIC_HISTORY_RESET].as_dict()["code"] == DIAGNOSTIC_HISTORY_RESET


def test_a_persistence_failure_episode_emits_once_and_a_later_episode_emits_again(tmp_path, clock, epoch_ids):
    diagnostics: list = []
    writer = FailingWriter(failures=5)
    store = build_store(tmp_path, clock, epoch_ids, writer=writer, diagnostics=diagnostics)

    for _ in range(5):
        assert publish(store, clock, observation(state="ready")).published is False
    assert [entry.code for entry in diagnostics] == [DIAGNOSTIC_PERSIST_FAILED]
    assert store.diagnostics()[0].occurrences == 5

    assert publish(store, clock, observation(state="ready")).published is True
    assert store.diagnostics() == []

    writer.remaining_failures = 2
    for _ in range(2):
        assert publish(store, clock, observation(state="degraded")).published is False
    assert [entry.code for entry in diagnostics] == [DIAGNOSTIC_PERSIST_FAILED, DIAGNOSTIC_PERSIST_FAILED]
    assert store.diagnostics()[0].occurrences == 2


def test_an_invalid_reason_code_is_reported_once_per_episode(tmp_path, clock, epoch_ids):
    diagnostics: list = []
    store = build_store(tmp_path, clock, epoch_ids, diagnostics=diagnostics)
    for _ in range(4):
        publish(store, clock, observation(state="degraded", reason_code="probe failed at /home/keivenc/socket"))
    assert [entry.code for entry in diagnostics] == [DIAGNOSTIC_REASON_CODE_INVALID]
    assert {entry.code: entry.occurrences for entry in store.diagnostics()}[DIAGNOSTIC_REASON_CODE_INVALID] == 4


# -- redaction ----------------------------------------------------------------


SECRETS = (
    "/home/keivenc/dev/yolomux.dev7771/state/services/yolomux-statsd.sock",
    "Authorization: Bearer sk-live-9f3a-DO-NOT-LEAK",
    "python -m yolomux_lib.stats_current.service --serve --token=hunter2",
    "Traceback (most recent call last): child log line",
)


def test_a_transition_row_carries_seven_typed_fields_and_no_free_text(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(state="starting"))
    leaky = publish(
        store,
        clock,
        observation(
            state="unknown",
            reason_code=f"probe failed: {SECRETS[1]} while reading {SECRETS[0]}",
            recovery_outcome=f"retry blocked by {SECRETS[2]}",
            epoch=(f"ps:Mon Aug 3 10:00:00 2026 {SECRETS[3]}", 4242),
            counters_available=False,
        ),
    )

    row = transitions_of(leaky.document)[-1]
    assert tuple(sorted(row)) == tuple(sorted(TRANSITION_ROW_FIELDS))
    assert row["previous_state"] == "starting"
    assert row["new_state"] == "unknown"
    assert row["reason_code"] == "reason_invalid"
    assert row["recovery_outcome"] == "recovery_invalid"
    assert row["process_epoch"] == process_epoch_token(4242, f"ps:Mon Aug 3 10:00:00 2026 {SECRETS[3]}")
    assert _PROCESS_EPOCH_RE.fullmatch(row["process_epoch"]) is not None
    assert isinstance(row["revision"], int) and isinstance(row["wall_time"], float)

    serialized = store.document_path.read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in serialized
    for fragment in ("Bearer", "sk-live", "/home/", ".sock", "--serve", "Traceback", "hunter2", "token="):
        assert fragment not in serialized
    for stored_row in transitions_of(json.loads(serialized)):
        assert _REASON_CODE_RE.fullmatch(stored_row["reason_code"]) is not None
        assert _REASON_CODE_RE.fullmatch(stored_row["recovery_outcome"]) is not None
        assert _PROCESS_EPOCH_RE.fullmatch(stored_row["process_epoch"]) is not None
        assert stored_row["new_state"] in BACKEND_HEALTH_STATES


def test_unknown_always_carries_a_bounded_reason(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    published = publish(store, clock, observation(state="unknown", reason_code="none", epoch=None))
    assert transitions_of(published.document)[-1]["reason_code"] == "observation_failed"
    assert published.document["resources"]["statsd"]["current"]["reason_code"] == "observation_failed"


def test_the_five_distinguishable_causes_do_not_collapse_into_one_string(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    causes = ("service_absent", "identity_mismatch", "revision_mismatch", "overload", "probe_failed")
    for index, cause in enumerate(causes):
        publish(store, clock, observation(state="down" if index % 2 else "degraded", reason_code=cause))

    rows = transitions_of(store.document())
    assert [row["reason_code"] for row in rows] == list(causes)
    assert len({row["reason_code"] for row in rows}) == len(causes)


# -- write and fsync failure --------------------------------------------------


def test_a_failed_write_never_reports_success_and_keeps_the_previous_snapshot(tmp_path, clock, epoch_ids):
    writer = FailingWriter()
    store = build_store(tmp_path, clock, epoch_ids, writer=writer)
    good = publish(store, clock, observation(state="ready", requests=10, completed=10, latency_total_ms=100.0))
    durable = store.document_path.read_text(encoding="utf-8")

    writer.remaining_failures = 1
    failed = publish(store, clock, observation(state="down", reason_code="exited", requests=15, completed=15, latency_total_ms=150.0))

    assert failed.published is False
    assert bool(failed) is False
    assert (failed.persistence_state, failed.reason_code) == (PERSISTENCE_DEGRADED, "write_failed")
    assert failed.revision == good.revision
    assert failed.document["resources"]["statsd"]["current"]["state"] == "ready"
    assert store.document_path.read_text(encoding="utf-8") == durable
    persistence = store.status()["persistence"]
    assert persistence["state"] == PERSISTENCE_DEGRADED
    assert persistence["reason_code"] == "write_failed"
    assert persistence["consecutive_failures"] == 1

    # Recovery re-derives every delta from the last durable baseline: nothing is double
    # counted and nothing observed after the failure is lost.
    recovered = publish(store, clock, observation(state="ready", requests=18, completed=18, latency_total_ms=180.0))
    assert recovered.published is True
    assert recovered.revision == good.revision + 1
    assert aggregate_of(recovered.document)["request_count"] == 18
    assert recovered.document["persistence"]["total_failed_publications"] == 1
    assert store.status()["persistence"]["state"] == PERSISTENCE_OK


def test_an_fsync_failure_keeps_the_previous_snapshot_and_leaves_no_temp_file(tmp_path, clock, epoch_ids, monkeypatch):
    store = build_store(tmp_path, clock, epoch_ids)
    published = publish(store, clock, observation(state="ready"))
    durable = store.document_path.read_text(encoding="utf-8")

    real_fsync = os.fsync
    directory = str(store.directory.resolve())

    def failing_fsync(descriptor):
        try:
            target = os.readlink(f"/proc/self/fd/{int(descriptor)}")
        except OSError:
            target = ""
        if target.startswith(directory):
            raise OSError("injected fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    failed = publish(store, clock, observation(state="down", reason_code="exited"))
    monkeypatch.undo()

    assert failed.published is False
    assert (failed.persistence_state, failed.reason_code) == (PERSISTENCE_DEGRADED, "write_failed")
    assert failed.revision == published.revision
    assert store.document_path.read_text(encoding="utf-8") == durable
    assert sorted(entry.name for entry in store.directory.iterdir()) == [".7771.json.lock", "7771.json"]
    assert store.status()["persistence"]["state"] == PERSISTENCE_DEGRADED


# -- structural input contract ------------------------------------------------


@pytest.mark.parametrize(
    "broken",
    [
        observation(state="unavailable"),
        observation(resource="Statsd"),
        observation(resource="../../etc/passwd"),
    ],
)
def test_a_structural_contract_violation_from_our_own_code_fails_fast(tmp_path, clock, epoch_ids, broken):
    store = build_store(tmp_path, clock, epoch_ids)
    with pytest.raises(BackendHealthContractError):
        store.record(HealthSnapshot(observed_at=clock.value, resources=(broken,)))
    assert store.document_path.exists() is False


def test_a_duplicate_resource_in_one_snapshot_fails_fast(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    with pytest.raises(BackendHealthContractError):
        store.record(HealthSnapshot(observed_at=1.0, resources=(observation(), observation(state="down"))))


@pytest.mark.parametrize("port", [0, -1, 70000, "7771", True])
def test_the_store_is_scoped_to_one_real_tcp_port(tmp_path, clock, epoch_ids, port):
    with pytest.raises(BackendHealthContractError):
        BackendHealthStore(port, state_dir=tmp_path, writer_identity=writer_identity(), clock=clock)


def test_the_documented_reason_and_recovery_vocabularies_are_bounded_tokens(tmp_path, clock, epoch_ids):
    for token in BACKEND_HEALTH_REASON_CODES | BACKEND_HEALTH_RECOVERY_OUTCOMES:
        assert _REASON_CODE_RE.fullmatch(token) is not None, token
    # The five causes the DOIT forbids collapsing each have their own documented name.
    assert {"service_absent", "identity_mismatch", "revision_mismatch", "overload", "probe_failed"} <= (
        BACKEND_HEALTH_REASON_CODES
    )
    assert "observation_failed" in BACKEND_HEALTH_REASON_CODES
    assert set(BACKEND_HEALTH_STATES) & BACKEND_HEALTH_REASON_CODES == {"upgrade_required"}


def test_an_unreadable_document_resets_rather_than_reading_as_a_first_run(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(state="ready"))
    store.document_path.unlink()
    store.document_path.mkdir()

    diagnostics: list = []
    reopened = build_store(tmp_path, clock, epoch_ids, diagnostics=diagnostics)
    assert reopened.document()["history_coverage"] == HISTORY_COVERAGE_RESET
    assert reopened.document()["history_reset_reason"] == RESET_HISTORY_UNREADABLE
    assert [(entry.code, entry.detail_code) for entry in diagnostics] == [
        (DIAGNOSTIC_HISTORY_RESET, RESET_HISTORY_UNREADABLE)
    ]
    # An unreadable file must not be quarantined: there was nothing readable to copy.
    assert reopened.quarantine_path.exists() is False


def test_the_resource_count_is_bounded_and_the_overflow_is_reported(tmp_path, clock, epoch_ids):
    diagnostics: list = []
    store = build_store(tmp_path, clock, epoch_ids, diagnostics=diagnostics)
    accepted = tuple(observation(resource=f"svc-{index:02d}") for index in range(BACKEND_HEALTH_MAX_RESOURCES))
    publish(store, clock, *accepted)
    assert len(store.document()["resources"]) == BACKEND_HEALTH_MAX_RESOURCES

    overflowed = publish(store, clock, *accepted, observation(resource="one-too-many"))
    assert len(overflowed.document["resources"]) == BACKEND_HEALTH_MAX_RESOURCES
    assert "one-too-many" not in overflowed.document["resources"]
    assert [entry.code for entry in diagnostics] == [DIAGNOSTIC_RESOURCE_LIMIT]
    assert store.diagnostics()[0].detail_code == "one-too-many"


def test_a_resource_absent_from_a_snapshot_keeps_its_history(tmp_path, clock, epoch_ids):
    store = build_store(tmp_path, clock, epoch_ids)
    publish(store, clock, observation(resource="jobd", state="ready"), observation(state="ready"))
    partial = publish(store, clock, observation(state="degraded", reason_code="overload"))

    assert set(partial.document["resources"]) == {"jobd", "statsd"}
    assert partial.document["resources"]["jobd"]["current"]["state"] == "ready"
    assert len(transitions_of(partial.document, "jobd")) == 1
    assert [row["new_state"] for row in transitions_of(partial.document)] == ["ready", "degraded"]


def test_the_production_store_writes_under_the_RESOLVED_state_root(tmp_path, clock, epoch_ids, monkeypatch):
    """WHERE the history lands, pinned at the one construction `cli` actually uses.

    THE REPRO, and it cost hours. `~/.local/state/yolomux/backend-health/` on this machine held a
    zero-byte `.7771.json.lock` and no document, which reads exactly like "this feature has never
    produced a durable publication". It had. The live 7771 server is a MANAGED INSTANCE PORT, so
    `tools/instance_isolation` gives it its own root and its history was sitting in that root's
    state directory, 34,860 bytes of it, revision 122, written by that server's own pid. The empty
    home directory was the leftover of an older process that ran with an explicit
    `YOLOMUX_CONFIG_DIR` -- which suppresses the managed root -- and only ever reached `load()`,
    which is what creates the lock file and writes no document.

    Every other test in this file passes `state_dir=tmp_path`, so the DEFAULT was the one thing
    nothing exercised: the store could have derived its directory from anywhere and the whole
    suite would still be green while an operator looked in the wrong place. This is the test that
    fails if the resolved state root stops being the answer.
    """

    resolved = tmp_path / "resolved-state"
    monkeypatch.setattr(store_module, "STATE_DIR", resolved)
    built = BackendHealthStore(PORT, clock=clock, new_epoch_id=epoch_ids)

    assert built.directory == resolved / "backend-health", built.directory
    assert built.document_path == resolved / "backend-health" / f"{PORT}.json", built.document_path
    assert built.quarantine_path == built.document_path.with_suffix(".json.quarantine"), built.quarantine_path
    # And construction alone leaves the lock and no document -- the shape the empty directory had.
    assert not built.document_path.exists(), sorted(path.name for path in built.directory.iterdir())
    assert (built.directory / f".{PORT}.json.lock").exists(), sorted(
        path.name for path in built.directory.iterdir()
    )

    # One publication and the document is there, under the resolved root and nowhere else.
    assert built.record(HealthSnapshot(observed_at=1.0, resources=(observation(),))).published is True
    assert built.document_path.exists(), sorted(path.name for path in built.directory.iterdir())
    assert json.loads(built.document_path.read_text())["port"] == PORT
