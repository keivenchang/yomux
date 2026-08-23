# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Where a `ClientEventBroker` publish backlog's cost lives, and what may not sit in the lock.

History, because the conclusions changed and the record should say so rather than read as if the
current answer was obvious. "Task 035/036/037" below are coordination IDs from the session that
produced this work; they have no referent anywhere in the repository, so read them as labels that
order these three findings, not as documents to go and look up. Everything they assert that is
checkable -- the ~9.3s drain, the 62ms miss of the 8-second fixture boundary, the withdrawn
barrier measurement -- is restated here with the numbers, so nothing depends on finding them:

* Task 035 observed a real trace: ~55 `operation_terminal` completions draining serially over
  ~9.3s against one browser subscriber, missing an 8-second fixture boundary by 62ms. It named
  `ClientEventBroker.lock` as a CANDIDATE owner but could not distinguish lock WAIT from lock
  HOLD from queue latency from SSE send cost.
* Task 036 built the first version of this file and concluded that `json.dumps` inside `publish()`
  was the hold-time owner. **That attribution is withdrawn.** Its 55-way `threading.Barrier` does
  not produce a 55-way race: `Barrier.wait` wakes threads through `Condition.notify_all`, and each
  woken thread must still reacquire the GIL, so they trickle out one at a time and each publish
  finishes before the next thread runs. Measured on this host, the peak number of threads
  simultaneously waiting for the broker lock was **1**, and total wait time was ~0.14ms across 55
  acquisitions -- the cost of an UNCONTENDED lock. Its `hold >= wait * 3` assertion was therefore
  passing because the lock was uncontended, which is true of every lock ever written, and under
  genuinely forced contention the same inequality inverts. `test_the_broker_lock_genuinely_
  serializes_every_concurrent_publisher` below replaces it with a setup that really does park all
  55 publishers on the lock at once, and asserts that structurally instead of by stopwatch.
* Task 037 asks the structural question instead of the timing one: WHAT WORK is allowed inside the
  ordering critical section? `test_payload_serialization_never_runs_inside_the_ordering_critical_
  section`, `test_independent_publishers_can_serialize_events_concurrently` and
  `test_overflow_drop_charges_exact_bytes_without_encoding_inside_the_lock` are the three
  regressions; all three fail on 2304c569012b28e866f3fba877e26af5adf91aba and pass once payload
  encoding leaves the lock on both the publish and the overflow path. (Two further tests,
  `test_accounting_byte_fallback_agrees_with_the_spliced_measurement` and
  `test_evicting_a_replayed_event_charges_its_real_size_and_leaves_no_pending_bookkeeping`, also
  fail on that commit, but only because each names an attribute the commit does not have
  -- `canonical_json` and `pending_bytes`. Neither is a regression; both exist to stop a later
  maintainer from unpicking the fix, and both were kept honest by mutation rather than by
  inspection.) No verdict among them is a
  speed comparison: nothing is measured and compared against a number. Be precise about the
  timeouts, though, because it would be easy to overclaim here. In the RED direction they cannot affect the answer at all -- the rendezvous they
  bound is structurally impossible while encoding sits in the lock, so any bound gives the same
  verdict. In the GREEN direction they are ordinary liveness guards: a machine starved badly
  enough that two already-runnable threads cannot both reach a barrier within five seconds would
  report a false red. Measured with the host deliberately saturated (load average ~36-40 on 32
  cores) the worst whole-test wall clock was 0.33s against the 5.0s bound, about 15x of headroom --
  one order of magnitude, not two. "Generous guard" is the honest description, not "cannot matter".

**The magnitude gap is still open and this file does not close it.** The lock-hold cost measured
here is milliseconds across 55 publishers, roughly a thousandfold below the ~9.3s that was
actually observed. Moving size-proportional work out of the critical section is correct and is
what the regressions pin, but nothing in this file entitles anyone to say the 9.3s drain is
explained, let alone cured. Two production users of the same lock are still uninstrumented:
`next_event`, which acquires it TWICE per delivered event (`client_events.py`, once to resolve the
subscriber and once for the pending/repair bookkeeping), and `has_demand`/`aggregate_channels`,
which producers call from hot loops. Measure those before anyone claims an owner.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time

import pytest

import yolomux_lib.client_events as client_events
from yolomux_lib.client_events import ClientEventBroker

PUBLISHER_COUNT = 55
# Liveness guards. On a defective implementation the rendezvous they bound is impossible, so they
# only decide how long the failing run takes. On a correct one they are slack for threads that are
# already runnable and merely need to be scheduled; they are not compared against anything and no
# assertion reads an elapsed time, but a sufficiently starved host could still trip them.
RENDEZVOUS_TIMEOUT_SECONDS = 5.0
JOIN_TIMEOUT_SECONDS = 30.0


def _realistic_operation_terminal_payload(index: int) -> dict:
    """Shaped like the REAL event `app.terminalize_operation` publishes -- critically, this must
    nest the operation id at `payload["operation"]["id"]`, exactly what `client_event_resource()`
    reads to key the `operation_terminal:<id>` coalescing resource. A payload missing this exact
    shape silently coalesces every synthetic event into ONE resource bucket instead of 55
    independent ones -- a real bug this file's own first draft hit and is deliberately guarded
    against by the `qsize() == PUBLISHER_COUNT` assertions below. `data` also carries several file
    rows with realistic string lengths so `json.dumps` does comparable real work to a genuine
    session_files/fs_watch_diff completion, not a trivially small dict.

    The row COUNT varies with `index` on purpose. It used to be fixed at 40, which made every
    single-digit index produce a structurally identical envelope -- `op-{index:04d}`, `session-
    {index}`, `r-{index}` and `yt-{index}` are all the same width -- so two different events
    differed in encoded length only by the repr of `time.time()`. Any test that distinguishes
    "charged event A's size" from "charged event B's size" was then decided by a float-repr
    coincidence: measured over 2000 runs, four such events came out all-equal in 699 of them, so a
    mutation charging the wrong event survived 35% of the time. `40 + index` gives each index its
    own length and keeps index 0 at the 40 rows the comments elsewhere quote. Re-measured over
    2000 runs of the four-event fixture: 0 runs with any two sizes equal, smallest pairwise gap
    148 bytes -- two orders above the byte-or-two that `time.time()`'s repr can move."""
    rows = [
        {
            "path": f"/repo/session-{index}/src/module_{row}/handler_{row}.py",
            "status": "modified" if row % 2 else "added",
            "additions": 10 + row,
            "deletions": row,
            "sha": f"{'abcdef0123456789' * 4}"[:40],
        }
        for row in range(40 + index)
    ]
    return {
        "operation": {"id": f"op-{index:04d}"},
        "state": "ready",
        "request": {"id": f"r-{index}"},
        "data": {"session": f"yt-{index}", "files": rows, "hours": 24.0},
    }


class TimingLock:
    """Drop-in replacement for `ClientEventBroker.lock` that records, per `acquire()`, the wait
    time (blocked trying to enter) and the hold time (between acquire returning and the matching
    release), and keeps a live census of how many threads are queued for it. The broker's own code
    is never modified -- only the lock instance it already accepts as a plain attribute is swapped
    after construction.

    Every test that swaps this in also asserts the swap took effect (`samples` is non-empty with
    the expected count). Without that check, a future rename of `ClientEventBroker.lock` would
    leave the swap writing a dead attribute, `held_by_current_thread()` would answer `False`
    forever, and an "is this work inside the lock?" assertion would pass while the defect it
    guards is fully intact."""

    def __init__(self) -> None:
        self._real = threading.RLock()
        self._census = threading.Condition(threading.Lock())
        self.samples: list[dict] = []
        self.waiting = 0
        self.peak_waiting = 0
        self._pending: dict[int, dict] = {}

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        tid = threading.get_ident()
        with self._census:
            self.waiting += 1
            self.peak_waiting = max(self.peak_waiting, self.waiting)
            self._census.notify_all()
        wait_start = time.perf_counter()
        acquired = self._real.acquire(blocking, timeout)
        with self._census:
            self.waiting -= 1
            if acquired:
                self._pending.setdefault(tid, {"depth": 0})
                entry = self._pending[tid]
                entry["depth"] += 1
                if entry["depth"] == 1:
                    entry["wait_seconds"] = time.perf_counter() - wait_start
                    entry["hold_start"] = time.perf_counter()
            self._census.notify_all()
        return acquired

    def release(self) -> None:
        tid = threading.get_ident()
        with self._census:
            entry = self._pending.get(tid)
            if entry is not None:
                entry["depth"] -= 1
                if entry["depth"] == 0:
                    hold_seconds = time.perf_counter() - entry["hold_start"]
                    self.samples.append({"wait_seconds": entry["wait_seconds"], "hold_seconds": hold_seconds})
                    del self._pending[tid]
        self._real.release()

    def held_by_current_thread(self) -> bool:
        """Whether the calling thread is inside this lock's critical section right now.

        Lets a probe installed deep inside `publish()` report WHERE it ran rather than how long it
        took, which is what turns an attribution measurement into a structural assertion."""
        tid = threading.get_ident()
        with self._census:
            entry = self._pending.get(tid)
            return bool(entry and entry["depth"] > 0)

    def wait_for_waiters(self, count: int, timeout: float) -> bool:
        """Block until `count` threads are simultaneously queued for this lock.

        This is the piece task 036 was missing. It is a condition wait, not a sleep and not a poll:
        `acquire()` notifies on every census change, so this returns the instant the parked set is
        full and cannot be tuned by guessing a delay."""
        deadline = time.monotonic() + timeout
        with self._census:
            while self.waiting < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._census.wait(remaining)
            return True

    def gate(self) -> "_LockGate":
        """Hold the underlying lock WITHOUT recording a sample, so a test can pin every publisher
        on it without polluting the measured wait/hold sums with the test's own hold."""
        return _LockGate(self._real)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_exc):
        self.release()


class _LockGate:
    def __init__(self, real_lock) -> None:
        self._real = real_lock

    def __enter__(self):
        self._real.acquire()
        return self

    def __exit__(self, *_exc):
        self._real.release()


class _JsonNamespaceShim:
    """Stand-in for the `json` module inside `client_events` only.

    Tests here patch `client_events.json` -- the module's own name binding -- and NOT
    `client_events.json.dumps`. The latter looks equivalent but is not: `client_events.json` IS the
    stdlib `json` module object, so setting an attribute on it replaces `json.dumps` for every
    thread in the interpreter, for every caller of `json` anywhere, for the duration of the test.

    Be precise about what this buys, because it is easy to overstate. It narrows the blast radius
    from "every `json` user in the process" to "every caller of `ClientEventBroker.publish` in the
    process". It does NOT make the zero-subscriber control below immune: another thread publishing
    a client event during that window would still be observed. What it does remove is the much
    larger and much likelier class of unrelated `json.dumps` callers."""

    def __init__(self, dumps) -> None:
        self.dumps = dumps


def _join_deadline(budget_seconds: float = JOIN_TIMEOUT_SECONDS) -> float:
    return time.monotonic() + budget_seconds


def _join_all(threads: list[threading.Thread], deadline: float) -> list[str]:
    """Join every thread against ONE shared deadline and return the names still alive.

    Per-thread timeouts multiply: joining 55 threads at 30s each is a 27-minute worst case, which
    turns one stuck publisher into an apparently hung test run with no artifact. Callers pass a
    deadline rather than a budget, so a `try` join followed by a `finally` join shares one bound
    instead of granting a fresh one and quietly doubling the worst case."""
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return [thread.name for thread in threads if thread.is_alive()]


def _run_publisher_backlog(broker: ClientEventBroker, count: int = PUBLISHER_COUNT) -> None:
    """Start `count` publisher threads, release them together, and join them all.

    Threads are daemons and their liveness is asserted, so a stuck publisher fails this test
    loudly instead of silently blocking interpreter shutdown for every test that follows."""
    release = threading.Barrier(count)
    failures: list[str] = []

    def publisher(index: int) -> None:
        try:
            release.wait(timeout=RENDEZVOUS_TIMEOUT_SECONDS)
            broker.publish("operation_terminal", _realistic_operation_terminal_payload(index))
        except Exception as exc:  # noqa: BLE001 - surfaced by the caller's assertion below
            failures.append(f"publisher {index}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=publisher, args=(index,), name=f"publisher-{index}", daemon=True) for index in range(count)]
    deadline = _join_deadline()
    try:
        for thread in threads:
            thread.start()
        _join_all(threads, deadline)
    finally:
        # Aborting the barrier releases anything still parked; the join that decides `stragglers`
        # is this one, after the abort, so the join above is only there to let a clean run finish
        # without waiting on the abort path.
        release.abort()
        stragglers = _join_all(threads, deadline)

    assert not failures, f"publisher thread(s) failed: {failures}"
    assert not stragglers, f"publisher thread(s) never finished: {stragglers}"


@pytest.mark.gate_serial
def test_the_broker_lock_genuinely_serializes_every_concurrent_publisher():
    """The premise everything else in this file rests on: one process-wide lock really does put
    every concurrent publisher in a single queue, even when their resources are independent.

    Task 036 asserted this with a stopwatch and got it wrong (see the module docstring). This
    asserts it structurally: the test itself holds the lock until all 55 publishers are provably
    parked on it, then releases. `peak_waiting == 55` is a count, not a duration -- machine load
    can change how long the parking takes but cannot change whether it happened."""

    broker = ClientEventBroker()
    _subscriber_id, subscriber_queue = broker.subscribe()
    timing_lock = TimingLock()
    broker.lock = timing_lock  # swapped AFTER subscribe() so only the publishers are measured

    all_parked = {"reached": False}
    gate_held = threading.Event()
    gate_opened = threading.Event()

    def hold_the_gate() -> None:
        with timing_lock.gate():
            # Ownership handshake, not a timing guess: no publisher may start until the gate is
            # provably held. Without this, whether the publishers pile up behind the gate or sail
            # straight through depends on which thread the scheduler happens to run first, and the
            # test would be measuring the scheduler instead of the broker.
            gate_held.set()
            all_parked["reached"] = timing_lock.wait_for_waiters(PUBLISHER_COUNT, timeout=JOIN_TIMEOUT_SECONDS)
        gate_opened.set()

    gatekeeper = threading.Thread(target=hold_the_gate, name="gatekeeper", daemon=True)
    gatekeeper.start()
    try:
        # A bounded guard against a future hang. It never decides success: reaching the bound is an
        # explicit failure with a named cause, and the assertions below are what pass or fail.
        assert gate_held.wait(timeout=JOIN_TIMEOUT_SECONDS), "the gatekeeper never took the broker lock, so the publishers were never gated"
        _run_publisher_backlog(broker)
    finally:
        gate_opened.wait(timeout=JOIN_TIMEOUT_SECONDS)
        gatekeeper.join(timeout=JOIN_TIMEOUT_SECONDS)

    assert not gatekeeper.is_alive(), "the gatekeeper thread must finish"
    # Positive control: proves the swapped lock is the one the broker actually used. Without it,
    # a rename of `ClientEventBroker.lock` would make every assertion below vacuous.
    assert len(timing_lock.samples) == PUBLISHER_COUNT, (
        f"the swapped lock must be the broker's real ordering lock and be taken exactly once per publish, "
        f"got {len(timing_lock.samples)} acquisitions for {PUBLISHER_COUNT} publishes"
    )
    assert subscriber_queue.qsize() == PUBLISHER_COUNT, "every publisher's event must reach the single subscriber's queue"

    # The OTHER half of the contract this file's failure messages state: allocation must stay
    # inside the lock. Counting arrivals does not check it -- 55 events can arrive carrying 8
    # distinct ids. Draining and checking identities does, and it costs nothing. Without this,
    # hoisting revision/id allocation out of the critical section (a plausible over-eager
    # continuation of the change these tests guard) ships duplicate event ids past a green suite.
    drained = [subscriber_queue.get_nowait() for _ in range(PUBLISHER_COUNT)]
    event_ids = [event["id"] for event in drained]
    assert len(set(event_ids)) == PUBLISHER_COUNT, (
        f"every publish must mint a distinct event id, got {len(set(event_ids))} distinct ids for "
        f"{PUBLISHER_COUNT} publishes. Duplicate ids mean id allocation is no longer serialized."
    )
    assert sorted(event_ids) == list(range(1, PUBLISHER_COUNT + 1)), f"event ids must be the contiguous range 1..{PUBLISHER_COUNT}, got {sorted(event_ids)}"
    resources = [event["resource"] for event in drained]
    assert len(set(resources)) == PUBLISHER_COUNT, "each publisher owns an independent operation_terminal resource"
    for event in drained:
        assert event["resource_revision"] == 1, f"a first publish for {event['resource']} must be revision 1, got {event['resource_revision']}"
        assert event["base_resource_revision"] == event["resource_revision"] - 1, "base_resource_revision must stay one behind its revision"

    assert all_parked["reached"], (
        f"expected all {PUBLISHER_COUNT} publishers to be queued on the broker lock at the same instant; "
        f"peak simultaneous waiters was {timing_lock.peak_waiting}. If this drops, the publishes are no "
        f"longer mutually exclusive and the rest of this file's premise needs re-deriving."
    )
    # Deliberately NOT a second independent check, and left in only as a readout of the number the
    # assertion above turns into a boolean. It cannot fail on its own: `wait_for_waiters` returns
    # True only once the census reaches PUBLISHER_COUNT, and the census cannot exceed it, because
    # exactly PUBLISHER_COUNT threads pass through `acquire()` (the gatekeeper enters via `gate()`,
    # which bypasses the census, and the main thread takes no lock while the backlog is parked).
    assert timing_lock.peak_waiting == PUBLISHER_COUNT, f"peak simultaneous waiters was {timing_lock.peak_waiting}"


def _serializes_the_payload_body(value: object) -> bool:
    """Whether this `json.dumps` call encodes the multi-row payload body built above.

    True whether the payload is handed over on its own or nested inside a full event envelope, so
    the classification does not assume either arrangement. An envelope whose payload has been
    replaced by a placeholder is NOT a payload-body serialization -- that call's cost is fixed and
    tiny no matter how large the real payload is."""
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("data"), dict) and isinstance(value.get("operation"), dict):
        return True
    nested = value.get("payload")
    return isinstance(nested, dict) and isinstance(nested.get("data"), dict) and isinstance(nested.get("operation"), dict)


@pytest.mark.gate_serial
def test_payload_serialization_never_runs_inside_the_ordering_critical_section(monkeypatch):
    """FORCED RED on 2304c569012b28e866f3fba877e26af5adf91aba, and the task-037 replacement for
    task 036's withdrawn attribution test.

    Task 036 asked "how long is the lock held, and by what?" and answered "`json.dumps` for byte
    accounting". Even where that answer described the base commit, it was useless as a regression:
    it asserted the defect. This asks the structural question instead -- WHERE does the
    size-proportional serialization run? -- and answers it with a location, not a duration. The
    probe reports whether the calling thread was inside `ClientEventBroker.lock` at the moment it
    encoded the payload body, so there is no elapsed time, no load sensitivity, and no retry
    anywhere in the verdict.

    The zero-subscriber control is retained: with no SSE consumer there is no wire payload, and
    `publish()` must not serialize anything at all."""

    calls: list[dict] = []
    real_dumps = json.dumps
    active_lock: dict[str, TimingLock | None] = {"lock": None}

    def locating_dumps(value, *args, **kwargs):
        timing_lock = active_lock["lock"]
        calls.append({
            "payload_body": _serializes_the_payload_body(value),
            "under_lock": bool(timing_lock is not None and timing_lock.held_by_current_thread()),
        })
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(client_events, "json", _JsonNamespaceShim(locating_dumps))

    def run_backlog(with_subscriber: bool) -> TimingLock:
        broker = ClientEventBroker()
        if with_subscriber:
            broker.subscribe()
        timing_lock = TimingLock()
        broker.lock = timing_lock
        active_lock["lock"] = timing_lock
        _run_publisher_backlog(broker)
        return timing_lock

    calls.clear()
    empty_lock = run_backlog(with_subscriber=False)
    assert len(empty_lock.samples) == PUBLISHER_COUNT, "positive control: the swapped lock must be the broker's real ordering lock"
    assert calls == [], "publish() must skip serialization entirely with zero subscribers (see test_client_events.py's own control)"

    calls.clear()
    loaded_lock = run_backlog(with_subscriber=True)
    assert len(loaded_lock.samples) == PUBLISHER_COUNT, "positive control: the swapped lock must be the broker's real ordering lock"

    payload_serializations = [call for call in calls if call["payload_body"]]
    payload_under_lock = [call for call in payload_serializations if call["under_lock"]]

    assert len(payload_serializations) == PUBLISHER_COUNT, (
        f"each of the {PUBLISHER_COUNT} publishes must encode its payload body exactly once -- not zero "
        f"times (byte accounting silently lost, and this test blinded) and not twice (the expensive half "
        f"done again), got {len(payload_serializations)}"
    )
    assert payload_under_lock == [], (
        f"{len(payload_under_lock)} of {len(payload_serializations)} payload-body serializations ran while "
        f"the publishing thread held ClientEventBroker.lock. That work is pure CPU over a private "
        f"per-event dict and grows with payload size, so holding the process-wide ordering lock "
        f"across it makes every concurrent publisher pay for every other publisher's payload size. "
        f"Revision allocation, retention, the subscriber snapshot and enqueue must stay inside the "
        f"critical section; encoding the payload body must not."
    )


# --- the overlap regression ------------------------------------------------------------------
#
# Same contract as the test above, approached from the other side. That one asks "was this work
# inside the lock?"; this one asks "could two publishers do this work at the same moment?" and
# never inspects the lock at all. Two probes, two different couplings, one contract:
#
#   Two publishers writing two INDEPENDENT resources must be able to have their event
#   serialization overlap in wall-clock time.
#
# Serialization is pure CPU over a private per-event dict. It reads and writes no broker state, so
# it has no ordering requirement -- unlike revision allocation, retention, the subscriber snapshot,
# and enqueue, which genuinely must stay inside one critical section. When serialization is
# performed inside that same critical section, overlap is not slow, it is IMPOSSIBLE: the second
# publisher cannot reach its serialization until the first has left the lock entirely. So on a
# defective implementation no amount of machine time changes the verdict, and the timeout below
# only decides how long the red run takes.
#
# Two honest limits on what this proves. It shows the two publishers can be INSIDE the encoding
# region at the same instant; on CPython the encoder holds the GIL, so it does not show the
# encoding work itself overlapping. And the probe is resource-agnostic -- it would also pass for a
# per-resource lock, which would fix these two publishers without shortening the global critical
# section. Independence of the two resources is pinned separately, by the queue-size assertion.

OVERLAP_PROBE_MARKER = "p1e5_037_overlap_probe"


def _is_overlap_probe_serialization(value: object) -> bool:
    """Whether this serialization call is producing the wire bytes of a probe event.

    Deliberately recognises the marker at EITHER depth. An implementation that measures the whole
    envelope hands the encoder the event (marker nested one level down under ``payload``); an
    implementation that measures the payload separately hands it the payload (marker at the top
    level). Matching both is what keeps this a contract test rather than a mirror of one particular
    implementation. It must NOT match an envelope whose payload has been replaced by a placeholder,
    which is why the nested branch requires a real dict."""

    if not isinstance(value, dict):
        return False
    if OVERLAP_PROBE_MARKER in value:
        return True
    nested = value.get("payload")
    return isinstance(nested, dict) and OVERLAP_PROBE_MARKER in nested


def _overlap_probe_payload(index: int) -> dict:
    payload = _realistic_operation_terminal_payload(index)
    payload[OVERLAP_PROBE_MARKER] = True
    return payload


@pytest.mark.gate_serial
def test_independent_publishers_can_serialize_events_concurrently(monkeypatch):
    """FORCED RED on 2304c569012b28e866f3fba877e26af5adf91aba. Two publishers, two independent
    `operation_terminal:<id>` resources, one subscriber. Each publisher is stopped at the exact
    instant its event is serialized and made to wait for the other to reach the same point. If
    serialization happens while the process-wide broker lock is held, publisher B is still parked
    at the lock while publisher A waits, the rendezvous can never complete, and both report
    `serialized`."""

    broker = ClientEventBroker()
    _subscriber_id, subscriber_queue = broker.subscribe()

    start = threading.Barrier(2)
    overlap = threading.Barrier(2)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()
    failures: list[str] = []
    real_dumps = json.dumps

    def overlap_probing_dumps(value, *args, **kwargs):
        if _is_overlap_probe_serialization(value):
            try:
                overlap.wait(timeout=RENDEZVOUS_TIMEOUT_SECONDS)
                outcome = "overlapped"
            except threading.BrokenBarrierError:
                # Either this thread timed out waiting alone, or it arrived after the barrier was
                # already broken by the other thread's timeout. Both mean the same thing: the two
                # serializations could not be in flight at the same moment.
                outcome = "serialized"
            with outcomes_lock:
                outcomes.append(outcome)
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(client_events, "json", _JsonNamespaceShim(overlap_probing_dumps))

    def publisher(index: int) -> None:
        try:
            start.wait(timeout=RENDEZVOUS_TIMEOUT_SECONDS)
            broker.publish("operation_terminal", _overlap_probe_payload(index))
        except Exception as exc:  # noqa: BLE001 - surfaced by the main thread's assertion below
            failures.append(f"publisher {index}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=publisher, args=(index,), name=f"overlap-publisher-{index}", daemon=True) for index in range(2)]
    deadline = _join_deadline()
    try:
        for thread in threads:
            thread.start()
        _join_all(threads, deadline)
    finally:
        # Never leave a parked thread behind if an assertion or an unexpected error cut the
        # rendezvous short; aborting is a no-op on an already-satisfied barrier. The join after
        # the abort is the one that decides `stragglers`.
        start.abort()
        overlap.abort()
        stragglers = _join_all(threads, deadline)

    assert not failures, f"publisher thread(s) failed: {failures}"
    assert not stragglers, f"both publisher threads must finish, still alive: {stragglers}"
    assert subscriber_queue.qsize() == 2, "both independent resources must still reach the subscriber"
    assert len(outcomes) == 2, f"each publisher must serialize its event exactly once, got {outcomes}"
    assert outcomes == ["overlapped", "overlapped"], (
        f"two publishers of INDEPENDENT resources must be able to serialize their events at the same "
        f"time, got {outcomes}. 'serialized' means the second publisher could not even begin "
        f"serializing until the first had released ClientEventBroker.lock, so every concurrent "
        f"publisher's per-event CPU cost is added end to end instead of running in parallel. "
        f"Serialization reads no broker state and needs no ordering guarantee; move it out of the "
        f"ordering critical section while leaving revision allocation, retention, the subscriber "
        f"snapshot, and enqueue inside it."
    )


# The drain side of the original task-035 candidate list. This one is a MEASUREMENT, not a
# regression: it passes before and after any fix here, and its job is only to rule two boundaries
# out as sufficient explanations for a multi-second drain. Its thresholds are absolute seconds and
# therefore load-sensitive, so they are set with wide headroom: the claim being tested is "this
# layer cannot account for the ~9.3s drain that was actually observed", and 4.0s aggregate over 55 events
# (~73ms per event on loopback) refutes that while staying far away from ordinary scheduling noise
# on a busy gate host.
DRAIN_BOUNDARY_BUDGET_SECONDS = 4.0


@pytest.mark.gate_serial
def test_enqueue_to_dequeue_and_sse_send_are_not_the_backlog_owner():
    """RETAINED EVIDENCE, not a regression. It passes identically on the base commit and on this
    change, so it pins nothing about either; it exists to record why two of task 035's candidate
    owners were eliminated. It is also the only test in this file whose verdict is decided by
    elapsed time, so the only failure it can produce is a false red or an unrelated one. Kept
    because the elimination is the reason the search moved to the lock at all; a reasonable
    reviewer could argue it belongs in the task record rather than in a standing gate.

    Separately measures the OTHER two candidate boundaries task 035 named: enqueue-to-dequeue
    queue latency, and SSE "send to the wire" cost, using the real `queue.Queue` the broker already
    uses plus a real local loopback `socket.socketpair()` standing in for the SSE connection
    (deterministic, in-process, no real network/browser -- proxies "send" and network-level
    "receipt", not browser JS processing time, which this harness does not and cannot claim to
    measure without a real browser). All 55 events are pre-enqueued (broker work already done,
    matching the real trace where the stuck operation's own work had already finished and it was
    purely waiting its turn) so this isolates drain-side cost only."""

    broker = ClientEventBroker()
    _subscriber_id, subscriber_queue = broker.subscribe()

    for index in range(PUBLISHER_COUNT):
        broker.publish("operation_terminal", _realistic_operation_terminal_payload(index))

    assert subscriber_queue.qsize() == PUBLISHER_COUNT

    dequeue_latencies: list[float] = []
    send_durations: list[float] = []
    receipt_latencies: list[float] = []
    sent_at: dict[str, float] = {}
    received_count = {"n": 0}
    receiver_error: list[str] = []

    server_sock, client_sock = socket.socketpair()
    receiver_thread: threading.Thread | None = None
    try:
        server_sock.settimeout(RENDEZVOUS_TIMEOUT_SECONDS)
        client_sock.settimeout(RENDEZVOUS_TIMEOUT_SECONDS)

        def receiver() -> None:
            # One identified boundary for this worker: any failure is recorded with its reason and
            # surfaced by the main thread, never dropped into a dead thread where pytest cannot
            # see it and the real cause is replaced by a misleading count mismatch.
            try:
                buffer = b""
                while received_count["n"] < PUBLISHER_COUNT:
                    chunk = client_sock.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line:
                            continue
                        receipt_time = time.perf_counter()
                        record = json.loads(line)
                        receipt_latencies.append(receipt_time - sent_at[str(record["id"])])
                        received_count["n"] += 1
            except Exception as exc:  # noqa: BLE001 - reported through receiver_error below
                receiver_error.append(f"{type(exc).__name__}: {exc}")

        receiver_thread = threading.Thread(target=receiver, name="sse-receiver", daemon=True)
        receiver_thread.start()

        for _ in range(PUBLISHER_COUNT):
            dequeue_start = time.perf_counter()
            event = subscriber_queue.get(timeout=RENDEZVOUS_TIMEOUT_SECONDS)
            dequeue_latencies.append(time.perf_counter() - dequeue_start)
            line = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            # Stamped BEFORE the write, so every event has a send timestamp by the time the
            # receiver can possibly observe it. Stamping after `sendall` lets the receiver win the
            # race and silently skip that sample, which would let the receipt assertion below
            # "pass" having measured almost nothing.
            sent_at[str(event["id"])] = time.perf_counter()
            # On a socketpair this is send-buffer backpressure, not "time on the wire": it grows
            # when the receiver thread is descheduled, which is why it is the most load-sensitive
            # of the three aggregates. If the receiver stalls past the socket timeout, `sendall`
            # raises and the test errors with that reason rather than reporting a budget breach --
            # a stalled receiver is a different failure from a slow one and should read as such.
            send_start = time.perf_counter()
            server_sock.sendall(line)
            send_durations.append(time.perf_counter() - send_start)

        receiver_thread.join(timeout=JOIN_TIMEOUT_SECONDS)
    finally:
        server_sock.close()
        client_sock.close()
        if receiver_thread is not None:
            receiver_thread.join(timeout=JOIN_TIMEOUT_SECONDS)

    assert not receiver_error, f"the loopback receiver failed: {receiver_error}"
    assert received_count["n"] == PUBLISHER_COUNT, "every sent event must be received on the loopback socket"
    # (No length assertion for receipt_latencies: it is appended on the statement immediately
    # before `received_count["n"] += 1`, with no branch between them, so its length always equals
    # received_count and the assertion above fires first. An earlier version of this file asserted
    # it anyway and claimed the receiver "can come up short" -- if the receiver stops early, the
    # count is short too, so that assertion could never fail.)
    # (No length assertion for dequeue_latencies/send_durations: the loop above appends to each
    # exactly once per iteration with no branch, so those lengths are 55 by construction and an
    # assertion on them could never fail. The receipt guard above is different -- the receiver
    # thread can stop early, so its list genuinely can come up short.)

    total_dequeue_seconds = sum(dequeue_latencies)
    total_send_seconds = sum(send_durations)
    total_receipt_seconds = sum(receipt_latencies)

    # The three aggregates are this test's whole point, and a passing run does not surface them:
    # they appear only in the failure messages below. (`record_property` was tried and removed --
    # this repo emits JUnit XML only for the certification node list in tools/check.py, so it
    # would have written nothing in any run the gate actually performs.) The budgets stay wide on
    # purpose: they exist to catch a change of ORDER OF MAGNITUDE, not to police scheduling noise.
    assert total_dequeue_seconds < DRAIN_BOUNDARY_BUDGET_SECONDS, f"queue dequeue latency must not itself explain multi-second draining: {total_dequeue_seconds:.4f}s"
    assert total_send_seconds < DRAIN_BOUNDARY_BUDGET_SECONDS, f"local loopback socket send must not itself explain multi-second draining: {total_send_seconds:.4f}s"
    assert total_receipt_seconds < DRAIN_BOUNDARY_BUDGET_SECONDS, f"local loopback socket receipt must not itself explain multi-second draining: {total_receipt_seconds:.4f}s"


def test_publish_byte_accounting_equals_canonical_event_serialization():
    """Exactness pin for the byte counters, across the payload shapes that could expose a
    difference between measuring the whole envelope in one call and measuring it in parts:
    unicode (which `ensure_ascii` escapes), a value only `default=str` can encode, a nested
    literal key called `payload`, a `null`-valued key, and an empty payload.

    `expected` is computed from the event `publish()` returned, never from a second publish: the
    envelope carries `time.time()`, whose float repr varies in length, so two publishes of the
    same payload legitimately differ by a byte or two. Comparing across publishes would produce a
    phantom failure that has nothing to do with the code under test.

    This passes on the base commit and must keep passing -- it is the guard that any change to
    WHERE the bytes are computed does not change WHAT they are, down to the byte. It is supporting
    evidence, never the forced-red regression."""

    payloads = [
        {},
        {"paths": ["/repo/app.py"]},
        {"text": "éè中文 \"quoted\" \\ backslash", "n": 0},
        {"set_needs_default_str": {1, 2, 3}},
        {"payload": {"nested": "literal key named payload"}},
        {"missing": None, "flag": False, "ratio": 1.5},
        {"looks_like_json": "null"},
        _realistic_operation_terminal_payload(7),
    ]
    for index, payload in enumerate(payloads):
        broker = ClientEventBroker()
        broker.subscribe()
        event = broker.publish("operation_terminal", payload)
        expected = len(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        snapshot = broker.snapshot()
        assert snapshot["published_bytes"] == expected, f"payload #{index} published_bytes drifted from the canonical serialization"
        assert snapshot["delivered_bytes"] == expected, f"payload #{index} delivered_bytes drifted from the canonical serialization"
        assert snapshot["published_by_resource"][event["resource"]]["bytes"] == expected, f"payload #{index} per-resource bytes drifted"


def test_overflow_drop_charges_exact_bytes_without_encoding_inside_the_lock(monkeypatch):
    """FORCED RED on the overflow path, which the 55-publisher tests above cannot reach.

    Those tests use the default 256-slot queue, so `enqueue` never overflows and the drop branch
    never runs. That branch is the worst possible place to leave size-proportional work inside the
    critical section: it executes only when a subscriber is already lagging, which is exactly the
    backed-up broker this whole task is about. Re-encoding a full event there charges the lock for
    the slow consumer's problem and makes every other publisher wait behind it.

    The exactness half matters too: the drop counters must charge the DROPPED event's real size,
    not a stand-in and not the incoming event's size."""

    calls: list[dict] = []
    real_dumps = json.dumps
    active_lock: dict[str, TimingLock | None] = {"lock": None}

    def locating_dumps(value, *args, **kwargs):
        timing_lock = active_lock["lock"]
        calls.append({
            "payload_body": _serializes_the_payload_body(value),
            "under_lock": bool(timing_lock is not None and timing_lock.held_by_current_thread()),
        })
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(client_events, "json", _JsonNamespaceShim(locating_dumps))

    # One slot, so every publish after the first evicts its predecessor. Distinct operation ids
    # keep the resources independent, so nothing coalesces and the eviction branch is the only way
    # through.
    broker = ClientEventBroker(max_queue_size=1)
    broker.subscribe()
    timing_lock = TimingLock()
    broker.lock = timing_lock
    active_lock["lock"] = timing_lock

    published = [broker.publish("operation_terminal", _realistic_operation_terminal_payload(index)) for index in range(4)]

    assert len(timing_lock.samples) == 4, "positive control: the swapped lock must be the broker's real ordering lock"
    snapshot = broker.snapshot()
    assert snapshot["dropped_events"] == 3, f"three of four events must have been evicted from the one-slot queue, got {snapshot['dropped_events']}"

    payload_under_lock = [call for call in calls if call["payload_body"] and call["under_lock"]]
    assert payload_under_lock == [], (
        f"{len(payload_under_lock)} payload-body encodings ran inside ClientEventBroker.lock during "
        f"these four publishes; a correct implementation does none. Some of them may be publish()'s "
        f"own accounting (the sibling test above owns that half); the ones this test exists for are "
        f"the drop branch's, which only executes when a subscriber is already lagging, so re-encoding "
        f"a full event there lengthens the critical section precisely when the broker is backed up. "
        f"For a published event the size is already known at enqueue time -- remember it rather than "
        f"recompute it."
    )

    sizes = [len(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")) for event in published]
    # Without this the exactness half below is vacuous. If the four events encode to the same
    # length, "charged the DROPPED event's size" and "charged the INCOMING event's size" are the
    # same number and the assertion cannot tell them apart. `_realistic_operation_terminal_payload`
    # varies its row count with `index` to guarantee the separation; this asserts it held rather
    # than trusting it. Verified by mutation: charging `event_bytes` (the incoming event) instead
    # of `dropped_bytes` fails here, where against the old fixed-length fixture it survived 35% of
    # runs. The separation is deterministic, not lucky -- 2000 fixture runs produced no two equal
    # sizes and a smallest pairwise gap of 148 bytes.
    assert len(set(sizes)) == len(sizes), f"the four fixture events must encode to distinct lengths for the charge assertions below to mean anything, got {sizes}"

    for event in published[:3]:
        expected = len(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        charged = snapshot["dropped_by_resource"][event["resource"]]
        assert charged["events"] == 1, f"{event['resource']} must be charged exactly one drop, got {charged['events']}"
        assert charged["bytes"] == expected, (
            f"the drop counter for {event['resource']} must charge that event's own canonical size "
            f"{expected}, got {charged['bytes']}"
        )


def test_accounting_byte_fallback_agrees_with_the_spliced_measurement():
    """Covers the `payload_json is None` branch of `event_accounting_bytes` by calling it directly,
    because the counters depend on the two branches agreeing and nothing else exercises the
    fallback. Be clear about what this does NOT cover: `publish()` reaches that branch only when a
    subscriber appears between the pre-lock hint and the lock itself, and the enqueue path reaches
    it only when a replayed event is evicted. Neither race is simulated here -- the branch is
    pinned, the routes into it are not."""

    broker = ClientEventBroker()
    broker.subscribe()
    for index, payload in enumerate([{}, {"text": "éè中文"}, {"set_needs_default_str": {1, 2, 3}}, _realistic_operation_terminal_payload(index=3)]):
        event = broker.publish("operation_terminal", payload)
        payload_json = ClientEventBroker.canonical_json(event["payload"])
        spliced = ClientEventBroker.event_accounting_bytes(event, payload_json)
        fallback = ClientEventBroker.event_accounting_bytes(event, None)
        canonical = len(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        # The load-bearing check: the spliced measurement must equal a straight one-pass encode.
        assert spliced == canonical, f"payload #{index}: spliced measurement {spliced} != canonical {canonical}"
        # Not redundant with the assertion above, despite looking it. Checked by mutation: change
        # ONLY the `payload_json is None` branch to use default separators, and `spliced ==
        # canonical` still passes while this assertion fails. Re-measured on the four payloads
        # below, (spliced, mutated fallback) = (199, 214), (232, 248), (233, 249), (6849, 7307);
        # payload #0 is the first to fail, at `fallback 214 != spliced 199`. The two branches are
        # independently reachable -- publish() takes the splice, the drop path takes the fallback --
        # so both need pinning.
        assert fallback == spliced, f"payload #{index}: fallback measurement {fallback} != spliced {spliced}"


def test_subscribing_replays_retained_events_without_encoding_inside_the_lock(monkeypatch):
    """`subscribe()` must not encode anything when the queue can hold the retained set.

    That condition is load-bearing and is asserted below rather than assumed: with a
    `max_queue_size` smaller than `CLIENT_EVENT_RETAINED_LIMIT` a replay CAN overflow, and then
    both the base commit and this one encode once per evicted replay. Production uses the 256
    default against a limit of 8, so the overflow case does not arise there.

    A retained event published while nobody was subscribed was never measured -- `publish()`
    correctly skips accounting with no consumer -- so the replay that hands it to the first
    subscriber has no size to reuse. The tempting move is to compute it there. That would put a
    full payload encode inside `subscribe()`'s critical section, on a path where the base commit
    did none at all: precisely the cost this whole change exists to remove, reintroduced at the
    other end. `subscribe()` also runs before the server's `try/finally`, so making it able to
    raise would leak a registered subscriber that nothing ever drains.

    Retained-event sizes stay unknown instead, and only an actual eviction pays for one encode."""

    calls: list[dict] = []
    real_dumps = json.dumps
    active_lock: dict[str, TimingLock | None] = {"lock": None}

    def locating_dumps(value, *args, **kwargs):
        timing_lock = active_lock["lock"]
        calls.append({"under_lock": bool(timing_lock is not None and timing_lock.held_by_current_thread())})
        return real_dumps(value, *args, **kwargs)

    broker = ClientEventBroker()
    # The condition this test's claim depends on, named rather than assumed: a replay can only
    # overflow (and therefore only encode) when the queue is smaller than the retained set.
    assert broker.max_queue_size > client_events.CLIENT_EVENT_RETAINED_LIMIT, (
        f"this test only pins the no-encode claim while the queue ({broker.max_queue_size}) can hold the "
        f"retained set ({client_events.CLIENT_EVENT_RETAINED_LIMIT})"
    )
    # Published with NO subscriber, so nothing is measured and the retained copies carry no size.
    for scope in range(3):
        broker.publish("search_progress", {"scope_id": f"scope-{scope}", "generation": 1, "revision": scope, "rows": ["x" * 500] * 20})
    assert len(broker.retained_events) == 3, "the three scopes must be retained for replay"

    timing_lock = TimingLock()
    broker.lock = timing_lock
    active_lock["lock"] = timing_lock
    monkeypatch.setattr(client_events, "json", _JsonNamespaceShim(locating_dumps))

    calls.clear()
    _subscriber_id, subscriber_queue = broker.subscribe()

    assert len(timing_lock.samples) == 1, "positive control: subscribe() must take the broker's real ordering lock exactly once"
    assert subscriber_queue.qsize() == 3, "all three retained scopes must be replayed to the new subscriber"
    under_lock = [call for call in calls if call["under_lock"]]
    assert under_lock == [], (
        f"{len(under_lock)} encodes ran inside ClientEventBroker.lock during subscribe(). The base "
        f"commit performs zero here, so this is a new cost on the connect path, and an encode here can "
        f"also raise -- which would leak the subscriber the server has already registered. Leave a "
        f"replayed event's size unmeasured and let the drop path pay for it if it is ever evicted. "
        f"(All {len(calls)} encodes seen during subscribe(): {calls})"
    )
    # Stronger than the assertion above and not implied by it: subscribe() must not encode AT ALL,
    # inside the lock or outside it, because there is no wire payload to measure at connect time.
    assert calls == [], f"subscribe() must not encode at all, got {len(calls)} calls"


def test_evicting_a_replayed_event_charges_its_real_size_and_leaves_no_pending_bookkeeping():
    """Pins the one route into the "size was never measured" fallback that a maintainer will
    actually break, and the pairing of the two pending maps.

    The sibling test above establishes that a replayed event is queued with NO remembered size.
    That leaves `pending_bytes` legitimately missing a key that `pending_by_resource` has, which
    invites an obvious-looking cleanup: "why keep two states -- store 0 when the size is unknown."
    Both maps stay consistent under that change and every other test in this file and in
    tests/test_client_events.py still passes, but the drop counters then charge 0 bytes for an
    evicted replay instead of its real size. Verified by mutation: `pending_bytes[resource] =
    event_bytes if event_bytes is not None else 0` in `remember_pending` passes everything without
    this test and fails here.

    The tail of the test covers the second pairing claim `forget_pending`'s docstring makes. Two
    mutations -- `forget_pending` popping only `pending_by_resource`, and `next_event` reverting to
    a bare `pending_by_resource.pop` -- corrupt no counter, because every writer of
    `pending_by_resource` re-writes or clears the size through `remember_pending`. What they do is
    leak `pending_bytes` entries keyed by resource, unboundedly, for the life of the connection.
    Nothing else in the suite notices; the final two assertions do."""

    broker = ClientEventBroker(max_queue_size=1)
    # Published with nobody subscribed, so publish() correctly skips accounting and the retained
    # copy carries no size. This is the only way to get an unsized event into a queue.
    broker.publish("search_progress", {"scope_id": "scope-0", "generation": 1, "revision": 0, "rows": ["x" * 500] * 20})
    subscriber_id, subscriber_queue = broker.subscribe()
    subscriber = broker.subscribers[subscriber_id]

    replayed = list(subscriber_queue.queue)[0]
    replayed_resource = str(replayed["resource"])
    assert subscriber.pending_by_resource.get(replayed_resource) is replayed, "the replayed event must occupy its resource's pending slot"
    assert replayed_resource not in subscriber.pending_bytes, "a replayed event's size must be absent, not guessed and not zero"
    # Held by reference: nothing coalesces into this object below (the evicting publish uses a
    # different resource), so this stays the exact bytes the drop counter has to charge.
    expected = len(json.dumps(replayed, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
    assert expected > 1000, f"the replayed event must be big enough that charging 0 is unmistakable, got {expected}"

    # A live publish on a DIFFERENT resource: it cannot coalesce, so it overflows the single slot
    # and evicts the replay, which is the only path that reaches the fallback encode.
    broker.publish("operation_terminal", _realistic_operation_terminal_payload(0))

    snapshot = broker.snapshot()
    charged = snapshot["dropped_by_resource"][replayed_resource]
    assert charged["events"] == 1, f"the evicted replay must be charged exactly one drop, got {charged['events']}"
    assert charged["bytes"] == expected, (
        f"the evicted replay must be charged its own real size {expected}, got {charged['bytes']}. "
        f"A 0 here means an unknown size was stored as a number instead of left absent, so the "
        f"fallback encode never ran."
    )
    assert set(subscriber.pending_bytes) <= set(subscriber.pending_by_resource), (
        f"pending_bytes may only ever hold keys pending_by_resource also holds; got "
        f"{sorted(set(subscriber.pending_bytes) - set(subscriber.pending_by_resource))} orphaned"
    )

    # Drain the one queued event. The timeout is a guard against a future hang, not part of the
    # verdict: the queue is already non-empty, so this returns without waiting.
    broker.next_event(subscriber_id, timeout=5.0)
    assert subscriber.pending_by_resource == {}, f"draining the queue must release every pending slot, got {sorted(subscriber.pending_by_resource)}"
    assert subscriber.pending_bytes == {}, (
        f"draining the queue must release every remembered size too, got {sorted(subscriber.pending_bytes)}. "
        f"A leftover key here is an unbounded per-resource leak for the life of the connection."
    )
