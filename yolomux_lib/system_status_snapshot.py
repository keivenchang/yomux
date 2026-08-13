# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The one owner of the `/api/system-status` body, and the one place it is retained.

The Daemons panel polls `/api/system-status` every five seconds while it is visible. Every one of
those requests used to assemble the whole diagnostic report on the request thread: local-service
projection, cache-directory walks, transcript scans, a full `json.dumps`, and the response
envelope's `copy.deepcopy` of the encoded body. Measured on the live host that was 0.18 s
typically, 1.002 s at load, and one construction was observed at 5 s - a diagnostic view that got
slower exactly when the server was in trouble and the reader needed it.

This module holds the two things that fixes:

* `SnapshotSlot` - one publish slot. A producer builds a body, the slot rebinds one immutable
  `Snapshot` reference, and readers take that reference. There is no lock on the read path and no
  partially-updated state, because a published snapshot is never mutated.
* `SystemStatusSnapshotOwner` - one background thread that drives one or more slots on a cadence,
  and the typed read the route returns.

Two rules this module exists to enforce, both of them defects this codebase has had before:

1. **A reader never builds.** `read()` cannot call a producer. The slowest thing it can do is
   record demand and wake the owner thread. A route that rebuilds synchronously when the cache is
   cold has simply moved the 5 s construction to the unluckiest reader.
2. **A stale snapshot is never presented as current.** Past the freshness deadline the read is
   `stale`, carries the age that made it stale and a machine-readable reason, and does NOT carry
   the aged body. The whole point of this panel is to tell a reader what is true right now; a body
   labelled `generated_at` five minutes ago and rendered as the current state is the exact failure
   the panel is supposed to detect elsewhere.

Everything time-related is injected, as in `backend_health.observer`: `monotonic` drives the
cadence and the age, `wall_clock` stamps the snapshot, and `wait` is the only blocking call in the
loop, so a test never sleeps.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
import json
import threading
import time
from typing import Any

from .infra.common import inline_json_product_metadata
from .infra.common import thread_is_running


# One rebuild per panel poll while a reader is demanding the payload, and none at all while nobody
# is looking (see `DEMAND_WINDOW_SECONDS`). Deliberately equal to the panel's 5 s poll rather than
# faster: a shorter cadence would move the work off the request thread and then do MORE of it, and
# a body newer than the poll that reads it is newness nobody can see.
SNAPSHOT_CADENCE_SECONDS = 5.0

# How long a read keeps the producer running after the reader goes away. It must exceed the panel's
# 5 s poll interval or the producer would idle out between two polls of an open panel; 30 s also
# covers a reader who switches tabs briefly and comes back.
DEMAND_WINDOW_SECONDS = 30.0

# The age past which a published body stops being an answer to "what is true now".
#
# Derived from measurement, not typed. Three measured terms:
#   cadence                     5.0 s  (above)
#   worst construction observed 5.0 s  (live host; typical 0.18 s, loaded maximum 1.002 s)
#   scheduling slack            2.0 s
# 5.0 + 5.0 + 2.0 = 12.0. In words: a body is called current only if the producer finished a cycle
# within one cadence plus one worst-case build. Anything older means the producer genuinely stopped
# keeping up, which is exactly what the reader of this panel needs to be told rather than shown an
# aged body with a fresh-looking timestamp.
#
# The cold path was measured before choosing this. First-request handler time on the isolated
# fixture host was 51.6 ms with the old inline assembly; the first BUILD after this change is the
# same work on the producer thread, two orders of magnitude inside this deadline.
FRESHNESS_DEADLINE_SECONDS = 12.0

# Advanced diagnostics are consulted deliberately, not scanned, so they are produced only when
# somebody asks and retained for their own window rather than rebuilt on the 5 s poll.
ADVANCED_CADENCE_SECONDS = 10.0

# The payload keys whose only reader is the Advanced disclosure, or nothing at all. Declared once,
# here, so the producer split and the test that polices it cannot drift apart.
SYSTEM_STATUS_ADVANCED_KEYS = (
    "refresh",
    "top_endpoints",
    "top_background_work",
    "top_event_types",
    "login_throttle",
    "largest_active_transcripts",
    "transcripts_cache",
)

SNAPSHOT_UNAVAILABLE_REASON_CODE = "system_status_snapshot_unavailable"
SNAPSHOT_UNAVAILABLE_REASON = "No system-status snapshot has been published yet; a build was requested."
SNAPSHOT_STALE_REASON_CODE = "system_status_snapshot_stale"
SNAPSHOT_STALE_REASON = "The newest system-status snapshot is {seconds:.1f}s old, past the {deadline:.1f}s freshness deadline."
SNAPSHOT_CURRENT_REASON_CODE = "system_status_snapshot_current"

SNAPSHOT_STATE_CURRENT = "current"
SNAPSHOT_STATE_STALE = "stale"
SNAPSHOT_STATE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Snapshot:
    """One published body and the provenance a reader needs to judge it.

    `body` is the encoded JSON object, not a dict, because the route writes bytes: encoding on the
    request thread is the same class of per-request work as assembling on it.
    """

    body: bytes
    product: Mapping[str, Any]
    generated_at: float
    published_monotonic: float
    build_ms: float
    sequence: int


@dataclass(frozen=True)
class SnapshotRead:
    """The typed result of one read. `snapshot` is present ONLY when the state is current."""

    state: str
    reason_code: str
    reason: str
    age_seconds: float | None
    snapshot: Snapshot | None
    last_generated_at: float | None
    last_sequence: int

    @property
    def current(self) -> bool:
        return self.state == SNAPSHOT_STATE_CURRENT

    def refusal_payload(self, *, cadence_seconds: float, deadline_seconds: float) -> dict[str, Any]:
        """The body served when there is nothing current to serve.

        It is a description of the snapshot's state, never an aged body wearing a current
        timestamp. `last_generated_at` is published so a reader can say how far behind the producer
        is, which is a different statement from publishing the aged numbers themselves.
        """

        return {
            "ok": False,
            "schema": "system-status-snapshot",
            "snapshot": {
                "state": self.state,
                "reason_code": self.reason_code,
                "reason": self.reason,
                "age_seconds": self.age_seconds,
                "last_generated_at": self.last_generated_at,
                "last_sequence": self.last_sequence,
                "cadence_seconds": cadence_seconds,
                "freshness_deadline_seconds": deadline_seconds,
            },
        }


OWNER_UNATTACHED_REASON_CODE = "system_status_snapshot_owner_unattached"
OWNER_UNATTACHED_REASON = "This process has no system-status snapshot owner."


def owner_unattached_read() -> SnapshotRead:
    """The typed read for a process that never armed a producer.

    It is a distinct reason code but the same shape as every other refusal, so a client parses one
    contract rather than discovering a second one the first time it meets this state.
    """

    return SnapshotRead(
        state=SNAPSHOT_STATE_UNAVAILABLE,
        reason_code=OWNER_UNATTACHED_REASON_CODE,
        reason=OWNER_UNATTACHED_REASON,
        age_seconds=None,
        snapshot=None,
        last_generated_at=None,
        last_sequence=0,
    )


def encode_snapshot_body(payload: Mapping[str, Any]) -> bytes:
    """Encode one payload exactly as the response writer would have, once, off the request thread."""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass
class SnapshotSlot:
    """One producer, one retained snapshot, one demand clock.

    This is the shared parent for both the core payload and the advanced diagnostics. They differ
    only in their producer and their cadence, so they must not be two classes: a second retained
    system-status body with its own freshness rules is precisely the divergent-copy defect that
    keeps producing two answers to one question here.
    """

    name: str
    build: Callable[[], Mapping[str, Any]]
    cadence_seconds: float
    deadline_seconds: float
    monotonic: Callable[[], float]
    wall_clock: Callable[[], float]
    demand_window_seconds: float = DEMAND_WINDOW_SECONDS
    _snapshot: Snapshot | None = field(default=None, init=False)
    _sequence: int = field(default=0, init=False)
    _demanded_monotonic: float | None = field(default=None, init=False)
    _builds: int = field(default=0, init=False)
    _build_failures: int = field(default=0, init=False)
    _last_failure: str = field(default="", init=False)
    _reads: int = field(default=0, init=False)
    _reads_by_state: dict[str, int] = field(default_factory=dict, init=False)
    # Guards the read-path COUNTERS only, never the snapshot read itself. Concurrent request
    # threads incrementing a plain int and a plain dict would silently undercount, and a published
    # counter that can lose events is worse than no counter. The snapshot reference stays lock-free
    # because it is only ever rebound, never mutated.
    _counter_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def publish_once(self) -> Snapshot:
        """Build one body and rebind the published reference.

        The rebinding is the atomic step: a reader either sees the previous snapshot or the new
        one, never a half-built dict, because the new object is fully constructed before the name
        `_snapshot` points at it and no published snapshot is ever mutated afterwards.
        """

        started = self.monotonic()
        payload = self.build()
        body = encode_snapshot_body(payload)
        self._sequence += 1
        self._builds += 1
        snapshot = Snapshot(
            body=body,
            product=inline_json_product_metadata(body),
            generated_at=self.wall_clock(),
            published_monotonic=self.monotonic(),
            build_ms=(self.monotonic() - started) * 1000.0,
            sequence=self._sequence,
        )
        self._snapshot = snapshot
        return snapshot

    def record_build_failure(self, error: BaseException) -> None:
        """Count a failed cycle against this slot; the loop above decides what to do about it."""

        self._build_failures += 1
        self._last_failure = f"{type(error).__name__}: {error}"

    def read(self) -> SnapshotRead:
        """Return the typed state of this slot and record demand. Never builds."""

        now = self.monotonic()
        self._demanded_monotonic = now
        with self._counter_lock:
            self._reads += 1
        snapshot = self._snapshot
        if snapshot is None:
            result = SnapshotRead(
                state=SNAPSHOT_STATE_UNAVAILABLE,
                reason_code=SNAPSHOT_UNAVAILABLE_REASON_CODE,
                reason=SNAPSHOT_UNAVAILABLE_REASON,
                age_seconds=None,
                snapshot=None,
                last_generated_at=None,
                last_sequence=0,
            )
        else:
            age = max(0.0, now - snapshot.published_monotonic)
            if age > self.deadline_seconds:
                result = SnapshotRead(
                    state=SNAPSHOT_STATE_STALE,
                    reason_code=SNAPSHOT_STALE_REASON_CODE,
                    reason=SNAPSHOT_STALE_REASON.format(seconds=age, deadline=self.deadline_seconds),
                    age_seconds=age,
                    snapshot=None,
                    last_generated_at=snapshot.generated_at,
                    last_sequence=snapshot.sequence,
                )
            else:
                result = SnapshotRead(
                    state=SNAPSHOT_STATE_CURRENT,
                    reason_code=SNAPSHOT_CURRENT_REASON_CODE,
                    reason="",
                    age_seconds=age,
                    snapshot=snapshot,
                    last_generated_at=snapshot.generated_at,
                    last_sequence=snapshot.sequence,
                )
        with self._counter_lock:
            self._reads_by_state[result.state] = self._reads_by_state.get(result.state, 0) + 1
        return result

    def demanded(self) -> bool:
        """Is anybody currently reading this slot? Nothing rebuilds for a panel nobody has open."""

        demanded_at = self._demanded_monotonic
        if demanded_at is None:
            return False
        return (self.monotonic() - demanded_at) <= self.demand_window_seconds

    def due(self) -> bool:
        """Is a rebuild owed right now?"""

        if not self.demanded():
            return False
        snapshot = self._snapshot
        if snapshot is None:
            return True
        return (self.monotonic() - snapshot.published_monotonic) >= self.cadence_seconds

    def seconds_until_due(self) -> float:
        """How long the owner thread may wait before this slot needs attention."""

        if not self.demanded():
            return self.demand_window_seconds
        snapshot = self._snapshot
        if snapshot is None:
            return 0.0
        return max(0.0, self.cadence_seconds - (self.monotonic() - snapshot.published_monotonic))

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot
        return {
            "name": self.name,
            "cadence_seconds": self.cadence_seconds,
            "freshness_deadline_seconds": self.deadline_seconds,
            "demand_window_seconds": self.demand_window_seconds,
            "demanded": self.demanded(),
            "sequence": self._sequence,
            "builds": self._builds,
            "build_failures": self._build_failures,
            "last_failure": self._last_failure,
            "reads": self._reads,
            "reads_by_state": dict(self._reads_by_state),
            "published": snapshot is not None,
            "generated_at": snapshot.generated_at if snapshot is not None else None,
            "build_ms": snapshot.build_ms if snapshot is not None else None,
            "body_bytes": len(snapshot.body) if snapshot is not None else 0,
        }


class SystemStatusSnapshotOwner:
    """One background thread that keeps the system-status slots published.

    Start it once, stop it once. `start()` latches, exactly like `BackendHealthObserver.start`, so a
    second start cannot produce a second producer for the same slots - two producers publishing one
    payload is the same defect as two caches answering one question.
    """

    THREAD_NAME = "system-status-snapshot"

    def __init__(
        self,
        *,
        build_core: Callable[[], Mapping[str, Any]],
        build_advanced: Callable[[], Mapping[str, Any]],
        cadence_seconds: float = SNAPSHOT_CADENCE_SECONDS,
        advanced_cadence_seconds: float = ADVANCED_CADENCE_SECONDS,
        deadline_seconds: float = FRESHNESS_DEADLINE_SECONDS,
        demand_window_seconds: float = DEMAND_WINDOW_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        wait: Callable[[threading.Event, float], bool] | None = None,
        on_diagnostic: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self._wait = wait if wait is not None else (lambda event, timeout: event.wait(timeout))
        self._on_diagnostic = on_diagnostic
        self.core = SnapshotSlot(
            name="core",
            build=build_core,
            cadence_seconds=float(cadence_seconds),
            deadline_seconds=float(deadline_seconds),
            demand_window_seconds=float(demand_window_seconds),
            monotonic=monotonic,
            wall_clock=wall_clock,
        )
        self.advanced = SnapshotSlot(
            name="advanced",
            build=build_advanced,
            cadence_seconds=float(advanced_cadence_seconds),
            deadline_seconds=float(deadline_seconds),
            demand_window_seconds=float(demand_window_seconds),
            monotonic=monotonic,
            wall_clock=wall_clock,
        )
        self._slots = (self.core, self.advanced)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._cycles = 0

    @property
    def running(self) -> bool:
        return thread_is_running(self._thread)

    def start(self) -> bool:
        """Start the producer exactly once. Returns False if it was already started."""

        with self._lock:
            if self._started:
                return False
            self._started = True
            thread = threading.Thread(target=self._run, name=self.THREAD_NAME, daemon=True)
            self._thread = thread
        thread.start()
        return True

    def wake(self) -> None:
        """Cut the current wait short. This is what a cold read does instead of building."""

        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def read_core(self) -> SnapshotRead:
        """The route's whole request-thread cost: one read, and a wake if it was not current."""

        result = self.core.read()
        if not result.current:
            self.wake()
        return result

    def read_advanced(self) -> SnapshotRead:
        result = self.advanced.read()
        if not result.current:
            self.wake()
        return result

    def publish_once(self) -> None:
        """Build every slot that is currently owed one. The loop body, callable from a test."""

        for slot in self._slots:
            if not slot.due():
                continue
            try:
                slot.publish_once()
            except Exception as error:  # noqa: BLE001 - documented supervisor boundary, see below
                # One slot's producer failing must not end the thread that publishes the other, and
                # must not be silent: the failure is counted and named against the slot that
                # produced it, and reported through the same diagnostic channel the health observer
                # uses. This is the unit boundary of a loop over independent units.
                slot.record_build_failure(error)
                if self._on_diagnostic is not None:
                    self._on_diagnostic(slot.name, error)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._cycles += 1
            # Clear BEFORE building, never after: a read that arrives while a build is in flight
            # must survive it. Clearing afterwards would drop that reader's demand and make it wait
            # a whole cadence for a payload it had already asked for.
            self._wake.clear()
            self.publish_once()
            self._wait(self._wake, self._next_wait_seconds())

    def _next_wait_seconds(self) -> float:
        return max(0.05, min(slot.seconds_until_due() for slot in self._slots))

    def status(self) -> dict[str, Any]:
        return {
            "present": True,
            "running": self.running,
            "started": self._started,
            "cycles": self._cycles,
            "slots": {slot.name: slot.status() for slot in self._slots},
        }
