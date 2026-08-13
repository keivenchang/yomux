# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Port-scoped retained backend-health history (M5 of DOIT.p0.daemon-monitor.md).

This module is the ONE owner of ``STATE_DIR/backend-health/<port>.json``. It takes an
already-built observation snapshot and turns it into bounded durable history. It does not
observe, probe, spawn, signal, or start anything: the collector (M3) and the observer (M4)
are separate owners and call :meth:`BackendHealthStore.record`.

INPUT CONTRACT
--------------
The caller builds one :class:`HealthSnapshot` per completed observation cycle::

    HealthSnapshot(
        observed_at=<wall clock seconds of the cycle>,
        resources=(ResourceObservation(...), ...),   # one row per observed resource
    )

    ResourceObservation(
        resource="statsd",                # ^[a-z][a-z0-9_-]{0,63}$, one per snapshot
        state="ready",                    # exactly one of BACKEND_HEALTH_STATES
        reason_code="none",               # bounded token, ^[a-z][a-z0-9_]{0,47}$
        recovery_outcome="none",          # bounded token, ^[a-z][a-z0-9_]{0,47}$
        pid=4242,                         # 0 when no process was verified
        process_start_identity="proc:98", # host_identity.process_start_identity(pid), "" when unverified
        counters_available=True,          # False => this cycle could not read the counters
        request_count=..., error_count=..., completed_count=...,   # cumulative SINCE PROCESS START
        latency_total_ms=..., latency_max_ms=...,                  # cumulative / max SINCE PROCESS START
    )

Structural violations from OUR OWN code (unknown state name, malformed resource id, a
duplicate resource in one snapshot, an out-of-range port) raise
:class:`BackendHealthContractError` -- fail fast, per the workspace error policy.

Values that arrive from a PEER PROCESS never raise and never invent data. A negative or
non-finite counter, a counter that rolled backwards, or a lost final sample degrades that
resource's ``aggregate.coverage`` to ``partial`` with a typed reason and contributes a
delta of zero. There is no code path that can emit a negative delta.

A reason code or recovery outcome that does not match the bounded token pattern is
replaced by ``reason_invalid`` / ``recovery_invalid`` and reported once as a deduplicated
diagnostic. It is NOT raised and NOT truncated: truncating ``"failed for token=abc123"``
would publish half a secret, and raising inside an observer loop would stop the loop. The
transition row is therefore structurally incapable of carrying free text -- see REDACTION.

ON-DISK SCHEMA (schema_version 1)
---------------------------------
::

    {
      "schema_version": 1,
      "port": 7771,
      "observer_epoch": "<32 hex>",          # history-continuity epoch; survives a web restart,
      "observer_epoch_started_at": 1.0,      # re-minted ONLY together with history_reset_reason
      "revision": 7,                         # monotonically increasing, continues across restarts
      "written_at": 12.5,                    # last successful write, wall clock seconds
      "history_coverage": "full" | "reset",
      "history_reset_reason": "" | "history_corrupt" | "history_schema_unsupported"
                                 | "history_port_mismatch" | "history_unreadable",
      "writer": {"stable_host_id": "...", "boot_id": "...", "pid": 11,
                 "process_epoch": "pid:11:start:5"},
      "persistence": {"total_failed_publications": 0,
                      "last_failure_reason_code": "",
                      "last_failure_wall_time": 0.0},
      "resources": {
        "statsd": {
          "current": {"state": "ready", "reason_code": "none", "recovery_outcome": "none",
                      "process_epoch": "pid:4242:start:98", "pid": 4242,
                      "observed_at": 12.5, "since_revision": 3, "since_wall_time": 6.0},
          "aggregate": {"coverage": "full" | "partial", "coverage_reasons": [...],
                        "restart_count": 0, "verified_epochs": 1, "observations": 4,
                        "request_count": 0, "error_count": 0, "completed_count": 0,
                        "latency_total_ms": 0.0, "latency_average_ms": null | float,
                        "epoch_latency_max_ms": null | float,
                        "epoch_latency_max_process_epoch": "...",
                        "last_verified_epoch": "...",
                        "last_sample": {"process_epoch": "...", "counters_available": true,
                                        "request_count": 0, "error_count": 0,
                                        "completed_count": 0, "latency_total_ms": 0.0}},
          "transitions": [ {row}, ... ],      # at most 128, oldest evicted first
          "transitions_total": 231,           # OPTIONAL, additive within schema_version 1
          "transitions_total_exact": true     # OPTIONAL, additive within schema_version 1
        }
      }
    }

A transition row carries EXACTLY these seven fields and nothing else::

    {"revision": 3, "wall_time": 6.0, "previous_state": "starting", "new_state": "ready",
     "reason_code": "none", "process_epoch": "pid:4242:start:98", "recovery_outcome": "none"}

TRANSITION TOTALS -- additive within schema_version 1
-----------------------------------------------------
``transitions_total`` is the LIFETIME number of accepted state changes for one resource, and
``transitions_total_exact`` says whether that number is the exact count or only a floor. The
``transitions`` list is a bounded 128-row window, so its length stops being the total the moment
the 129th change is recorded, and a reader told "128 recorded, all shown" is holding a number
that will never move again.

Both fields were added WITHOUT a version bump, deliberately. They are additive: an older reader
that does not know them ignores them, and every document remains a valid schema-1 document.
Raising ``BACKEND_HEALTH_SCHEMA_VERSION`` would instead have made every existing document
``history_schema_unsupported`` and thrown away real retained history to gain a counter.

The cost of that choice is stated rather than hidden:

* PRE-COUNTER DOCUMENT. A document written before the fields existed carries neither. Its total
  can only be inferred from an already-truncated list, so on load it becomes
  ``max(stored_total, len(transitions))`` with ``transitions_total_exact`` false whenever the
  retained list has reached the cap -- a LOWER BOUND, never a claim. Presenting that as exact
  would be the same defect the counter was added to fix, one layer in.
* DOWNGRADE. A document written by this build and then written again by a pre-counter build
  loses both fields; the older build keeps producing valid schema-1 documents, and the next
  counter-aware load treats the result as the pre-counter case above. A downgrade therefore
  costs EXACTNESS and never history, and the total never decreases -- both the record path and
  the load path floor it at ``len(transitions)``.
* EXACTNESS IS ONLY EVER ASSERTED WHEN EARNED. A resource whose retained list has not reached
  ``BACKEND_HEALTH_MAX_TRANSITIONS`` is exact because nothing has been evicted; a document that
  already carried the flag keeps its own answer.
* PARTIAL DOWNGRADE. The two fields did not land together: an intermediate build wrote
  ``transitions_total`` with no ``transitions_total_exact`` at all, and those documents are still
  on disk. Their counter is carried across eviction by its writer, so it is the exact count, and
  a MISSING flag beside a usable total keeps that inference. A flag that is PRESENT but not a
  boolean does not: the document asserted something unreadable, which is not the same as having
  asserted nothing. Both fields are asked PRESENCE first and VALIDITY second, independently, in
  the one owner ``_transition_totals`` -- collapsing those two questions is a mistake this module
  has now made three times, once for the total and twice around the flag.
* The projection publishes both, and ``transitions_truncated`` is what tells the panel the list
  it is rendering is shorter than the total it is quoting.

REDACTION
---------
Redaction here is structural, not a filter. Every value a transition row can hold is either
a number, one of the seven typed states, ``""``, a token matching ``^[a-z][a-z0-9_]{0,47}$``,
or a process-epoch token matching ``^(none|pid:\\d+:(start:\\d+|startid:[0-9a-f]{16}))$``.
A path, command line, socket name, URL, request payload, bearer token, or log line cannot
survive that alphabet, and a non-Linux ``ps:<lstart>`` identity is hashed rather than
copied. No sample, payload, or child log text is stored anywhere in the document.

COUNTERS
--------
Counters accumulate as deltas inside one verified process epoch, where the epoch is
``(pid, process start time)`` -- ``registry.process_start_time`` / ``process_start_identity``
folded into one token by :func:`process_epoch_token`. The first verified epoch for a
resource is a BASELINE and never a restart; ``restart_count`` increments only when a
verified epoch replaces a different, previously verified epoch. ``latency_average_ms`` is
``latency_total_ms / completed_count``; ``epoch_latency_max_ms`` is the maximum declared for
the CURRENT epoch and resets with it. Individual samples are never persisted.

Known limitation, stated rather than hidden: ``missed_final_sample`` is raised when the last
observation before an epoch change could not read counters. If the last observation DID read
them, work served between that observation and process exit is still unmeasurable; the store
records what it can prove was lost, not what it can only suspect.

PERSISTENCE FAILURE
-------------------
The reducer is pure. ``record()`` builds the candidate document, writes it, and adopts it
only after the write returns. A write or fsync failure therefore keeps the previous good
snapshot on disk AND in memory, returns ``published=False`` with a typed reason, and shows
``persistence.state == "degraded"`` in :meth:`BackendHealthStore.status`. It never reports
success -- this is the same defect class as the 0.7.0 registry bug where a failed write was
reported as a successful start.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from time import time as wall_clock_now
from typing import Any
from typing import Callable
from typing import Mapping

from ..infra.atomic_file import atomic_write_text
from ..infra.atomic_file import file_lock
from ..infra.common import STATE_DIR
from ..infra.host_identity import HostIdentity
from ..infra.host_identity import current_host_identity
from ..infra.host_identity import process_start_identity
from ..infra.host_identity import process_start_ticks


BACKEND_HEALTH_SCHEMA_VERSION = 1
BACKEND_HEALTH_DIRECTORY_NAME = "backend-health"
BACKEND_HEALTH_MAX_TRANSITIONS = 128
BACKEND_HEALTH_MAX_RESOURCES = 32
BACKEND_HEALTH_QUARANTINE_MAX_BYTES = 64 * 1024
BACKEND_HEALTH_MAX_COVERAGE_REASONS = 8

BACKEND_HEALTH_STATES = (
    "starting",
    "ready",
    "degraded",
    "down",
    "backoff",
    "upgrade_required",
    "unknown",
)

# Documented codes, not a closed set: the collector owns its own vocabulary and any token
# matching `_REASON_CODE_RE` is accepted. Listing them keeps the five causes the DOIT
# forbids collapsing -- absence, identity mismatch, revision mismatch, overload, probe
# failure -- visible as five distinct names in one place.
BACKEND_HEALTH_REASON_CODES = frozenset(
    {
        "none",
        "observation_failed",
        "service_absent",
        "identity_mismatch",
        "revision_mismatch",
        "overload",
        "probe_failed",
        "probe_timeout",
        "start_blocked",
        # A RUNNING process that reported a fault this cycle: reconnecting, `healthy=False`, a
        # recorded `last_failure`. Kept DISTINCT from `terminal_failure` because a live pid is
        # not terminally failed -- `terminal_failure` is the registry's latched permanent
        # start-failure fence that gates recovery, and collapsing a transient running-degraded
        # window into it was the misclassification the daemon-monitor DOIT names.
        "service_unhealthy",
        "terminal_failure",
        "upgrade_required",
        "exited",
        "reason_invalid",
    }
)

BACKEND_HEALTH_RECOVERY_OUTCOMES = frozenset(
    {
        "none",
        "not_attempted",
        "retry_scheduled",
        "retry_blocked",
        "retry_exhausted",
        "recovered",
        "recovery_invalid",
    }
)

TRANSITION_ROW_FIELDS = (
    "revision",
    "wall_time",
    "previous_state",
    "new_state",
    "reason_code",
    "process_epoch",
    "recovery_outcome",
)

UNVERIFIED_PROCESS_EPOCH = "none"
NO_PRIOR_STATE = ""

HISTORY_COVERAGE_FULL = "full"
HISTORY_COVERAGE_RESET = "reset"
AGGREGATE_COVERAGE_FULL = "full"
AGGREGATE_COVERAGE_PARTIAL = "partial"

PERSISTENCE_OK = "ok"
PERSISTENCE_DEGRADED = "degraded"
PERSISTENCE_BLOCKED = "blocked"

RESET_HISTORY_CORRUPT = "history_corrupt"
RESET_HISTORY_SCHEMA_UNSUPPORTED = "history_schema_unsupported"
RESET_HISTORY_PORT_MISMATCH = "history_port_mismatch"
RESET_HISTORY_UNREADABLE = "history_unreadable"

DIAGNOSTIC_HISTORY_RESET = "backend_health_history_reset"
DIAGNOSTIC_PERSIST_FAILED = "backend_health_persist_failed"
DIAGNOSTIC_WRITER_CONFLICT = "backend_health_writer_conflict"
DIAGNOSTIC_REASON_CODE_INVALID = "backend_health_reason_code_invalid"
DIAGNOSTIC_RECOVERY_OUTCOME_INVALID = "backend_health_recovery_outcome_invalid"
DIAGNOSTIC_RESOURCE_LIMIT = "backend_health_resource_limit_exceeded"
DIAGNOSTIC_PEER_COUNTERS_INVALID = "backend_health_peer_counters_invalid"
# Raised by the observer's supervisor boundary, not by the store: a cycle that throws never
# reaches `record`, so no persistence diagnostic can describe it. Lives here beside its siblings
# because this is the one vocabulary of backend-health diagnostic codes.
DIAGNOSTIC_CYCLE_FAILED = "backend_health_cycle_failed"

COVERAGE_REASON_ROLLBACK = "counters_rollback"
COVERAGE_REASON_MISSED_FINAL = "missed_final_sample"
COVERAGE_REASON_CORRUPT_COUNTERS = "corrupt_counters"
COVERAGE_REASON_HISTORY_RESET = "history_reset"

_RESOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_PROCESS_EPOCH_RE = re.compile(r"^(?:none|pid:[0-9]{1,10}:(?:start:[0-9]{1,20}|startid:[0-9a-f]{16}))$")
_HOST_TOKEN_RE = re.compile(r"^[a-z0-9._-]{1,128}$")

_COUNTER_FIELDS = ("request_count", "error_count", "completed_count")


class BackendHealthContractError(ValueError):
    """A caller in this codebase violated the store's structural input contract."""


class _DocumentRejected(Exception):
    """One persisted document cannot be continued; carries the typed reset reason."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def process_epoch_token(pid: object, start_identity: object) -> str:
    """Return the one bounded token that names a verified ``(pid, process start time)``.

    ``UNVERIFIED_PROCESS_EPOCH`` when no process was verified. A Linux ``proc:<ticks>``
    identity is kept readable; any other identity (the portable ``ps:<lstart>`` fallback)
    is hashed so a transition row can never carry free text out of the process table.
    """

    try:
        clean_pid = int(pid)
    except (TypeError, ValueError):
        return UNVERIFIED_PROCESS_EPOCH
    identity = str(start_identity or "").strip()
    if clean_pid <= 1 or not identity:
        return UNVERIFIED_PROCESS_EPOCH
    ticks = process_start_ticks(identity)
    if ticks is not None and ticks > 0:
        return f"pid:{clean_pid}:start:{ticks}"
    digest = hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:16]
    return f"pid:{clean_pid}:startid:{digest}"


def _normalized_token(value: object, *, default: str = "none", invalid: str = "reason_invalid") -> tuple[str, bool]:
    """Return ``(token, accepted)``: an absent token becomes *default*, a malformed one *invalid*.

    A malformed token is REPLACED, never truncated. Truncating ``"failed for token=abc123"``
    to a bounded length would publish half a secret; replacing it publishes none of it.
    """

    text = str(value or "").strip()
    if not text:
        return default, True
    if _REASON_CODE_RE.fullmatch(text) is None:
        return invalid, False
    return text, True


def _finite_number(value: object) -> float | None:
    """Return a finite, non-negative number, or ``None`` when the value is unusable.

    Deliberately NOT `infra.common.positive_finite_number`: that owner maps both "invalid"
    and "zero" to `0.0`, which is the silent default this store must never take. A peer
    reporting zero requests is a fact; a peer reporting `-5` is corrupt data that has to
    reach `coverage: partial` instead of being quietly rounded up to a plausible count.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def _validated_wall_time(clock: Callable[[], float]) -> float:
    """THE ONE reader of an injected wall clock in this module.

    Both `DiagnosticEpisodes` and `BackendHealthStore` stamp episodes and documents from the SAME
    injected wall clock -- the store hands its own `clock` straight to the episodes it owns, and
    the observer hands its `wall_clock` (never its monotonic clock) to the episodes it owns. Two
    copies of this validator is how one of them ends up accepting a clock the other rejects, so a
    corrupt clock reaches persisted `first_wall_time`/`written_at` through whichever copy drifted.
    """

    value = _finite_number(clock())
    if value is None:
        raise BackendHealthContractError("injected clock must return a finite, non-negative wall time")
    return value


def _transition_totals(source: Mapping[str, Any] | None, retained: int) -> tuple[int, bool]:
    """Resolve ``(transitions_total, transitions_total_exact)`` from one resource record.

    THE ONE OWNER of the additive-schema-1 counter rules, because this module has now had the
    same mistake three times in a row: PRESENCE and VALIDITY are DIFFERENT QUESTIONS, and a
    branch that asks only "is this the right type?" answers "absent" and "present but broken"
    identically -- losing, in one direction, a claim an older writer earned, and inventing, in
    the other, a claim no document ever made. Both fields are OPTIONAL, so both have to be asked
    the two questions SEPARATELY, and neither one's answer may be read off the other's.

    ``retained`` is the number of rows the document actually carries.

    THE TOTAL, presence first:

    * ABSENT -- a document written before the counter existed. The rows are all there is, so the
      total is their length, and that is the EXACT answer only while nothing has been evicted.
    * PRESENT BUT UNUSABLE, or PRESENT AND SMALLER THAN ITS OWN ROWS -- a partial write, a
      hand-edit or an older writer. Both are a disagreement about the counter, and a disagreement
      costs EXACTNESS, never history: floor to what the rows prove and claim nothing.
    * PRESENT AND CONSISTENT -- taken as written; the flag then decides exactness.

    THE FLAG, presence first, and only ever consulted for a usable total:

    * A real boolean is honoured in both directions -- the document's own answer wins.
    * ABSENT -- `8ee3374ba` wrote `transitions_total` and had no such flag at ALL, and those
      documents are on disk now. Their counter is carried across eviction by its writer, so it
      IS the exact count; that legacy inference is earned by the total's own provenance and must
      survive, or an additive field would have retroactively demoted valid schema-1 documents.
    * PRESENT BUT NOT A BOOLEAN -- `'true'`, `1`, `None`, `{}`. Exactness is a CLAIM, and a
      corrupt flag does not make it. This document asserted something and the assertion is
      unreadable, which is the opposite of having asserted nothing.
    """

    record = source or {}
    total = record.get("transitions_total")
    if "transitions_total" not in record:
        return retained, retained < BACKEND_HEALTH_MAX_TRANSITIONS
    usable = isinstance(total, int) and not isinstance(total, bool) and total >= 0
    if not usable or total < retained:
        return retained, False
    exact = record.get("transitions_total_exact")
    if isinstance(exact, bool):
        return int(total), exact
    return int(total), "transitions_total_exact" not in record


@dataclass(frozen=True)
class ResourceObservation:
    """One resource's already-built observation. See the module INPUT CONTRACT."""

    resource: str
    state: str
    reason_code: str = "none"
    recovery_outcome: str = "none"
    pid: int = 0
    process_start_identity: str = ""
    counters_available: bool = False
    request_count: float = 0.0
    error_count: float = 0.0
    completed_count: float = 0.0
    latency_total_ms: float = 0.0
    latency_max_ms: float = 0.0


@dataclass(frozen=True)
class HealthSnapshot:
    """One completed observation cycle. Resources absent from it retain prior history."""

    observed_at: float
    resources: tuple[ResourceObservation, ...] = ()


@dataclass(frozen=True)
class WriterIdentity:
    """The identity of the process holding the port lease for this file."""

    stable_host_id: str
    boot_id: str
    pid: int
    process_epoch: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "stable_host_id": self.stable_host_id,
            "boot_id": self.boot_id,
            "pid": self.pid,
            "process_epoch": self.process_epoch,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WriterIdentity":
        host = str(raw.get("stable_host_id") or "").strip().lower()
        boot = str(raw.get("boot_id") or "").strip().lower()
        if _HOST_TOKEN_RE.fullmatch(host) is None or _HOST_TOKEN_RE.fullmatch(boot) is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        try:
            pid = int(raw.get("pid") or 0)
        except (TypeError, ValueError) as exc:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT) from exc
        epoch = str(raw.get("process_epoch") or UNVERIFIED_PROCESS_EPOCH)
        if pid < 0 or _PROCESS_EPOCH_RE.fullmatch(epoch) is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        return cls(stable_host_id=host, boot_id=boot, pid=pid, process_epoch=epoch)

    @classmethod
    def for_current_process(cls, *, host_identity: HostIdentity | None = None) -> "WriterIdentity":
        identity = host_identity or current_host_identity()
        pid = os.getpid()
        return cls(
            stable_host_id=identity.stable_host_id,
            boot_id=identity.boot_id,
            pid=pid,
            process_epoch=process_epoch_token(pid, process_start_identity(pid) or ""),
        )


@dataclass(frozen=True)
class BackendHealthDiagnostic:
    """One deduplicated typed diagnostic episode, emitted once per episode."""

    code: str
    port: int
    observer_epoch: str
    detail_code: str
    occurrences: int
    first_wall_time: float
    last_wall_time: float
    # The CAUSE, when the producer has one: a bounded traceback for an episode that started
    # with a caught exception. Deliberately absent from `as_dict()` -- this is for the
    # operator log, not for the status payload, whose diagnostic rows stay typed tokens.
    detail_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "port": self.port,
            "observer_epoch": self.observer_epoch,
            "detail_code": self.detail_code,
            "occurrences": self.occurrences,
            "first_wall_time": self.first_wall_time,
            "last_wall_time": self.last_wall_time,
        }


@dataclass(frozen=True)
class PublishResult:
    """The outcome of one :meth:`BackendHealthStore.record` call."""

    published: bool
    revision: int
    persistence_state: str
    reason_code: str
    document: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.published


@dataclass
class _DiagnosticEpisode:
    detail_code: str
    occurrences: int
    first_wall_time: float
    last_wall_time: float


class DiagnosticEpisodes:
    """Report one typed backend-health failure ONCE PER EPISODE, not once per occurrence.

    The ONE owner of that rule, shared by the store's persistence/corruption diagnostics and by
    the observer's cycle failures. Both produce a fault that repeats on a fixed cadence -- a
    write that fails every publication, a cycle that throws every ``interval_seconds`` -- so both
    need exactly the same "first occurrence and each change of cause is reported, the repeats are
    counted" behaviour. A second copy of it is how one of the two ends up emitting one operator
    log line every two seconds while the other emits one.

    ``epoch_source`` is a callable, not a value: the store's observer epoch changes under it when
    history resets, and an episode opened before the reset must still name the epoch it is
    reported in.
    """

    def __init__(
        self,
        port: int,
        *,
        clock: Callable[[], float],
        on_diagnostic: Callable[[BackendHealthDiagnostic], None] | None = None,
        epoch_source: Callable[[], str] = lambda: "",
    ) -> None:
        self.port = int(port)
        self._clock = clock
        self._on_diagnostic = on_diagnostic
        self._epoch_source = epoch_source
        self._episodes: dict[str, _DiagnosticEpisode] = {}

    def emit(self, code: str, *, detail_code: str = "", detail_text: str = "") -> None:
        """Open or extend one episode; report only when it opens or its cause changes."""

        now = round(self._now(), 3)
        episode = self._episodes.get(code)
        if episode is not None and episode.detail_code == detail_code:
            episode.occurrences += 1
            episode.last_wall_time = now
            return
        self._episodes[code] = _DiagnosticEpisode(
            detail_code=detail_code,
            occurrences=1,
            first_wall_time=now,
            last_wall_time=now,
        )
        if self._on_diagnostic is None:
            return
        self._on_diagnostic(
            BackendHealthDiagnostic(
                code=code,
                port=self.port,
                observer_epoch=self._epoch_source(),
                detail_code=detail_code,
                occurrences=1,
                first_wall_time=now,
                last_wall_time=now,
                detail_text=detail_text,
            )
        )

    def clear(self, code: str) -> None:
        """End one episode so a later recurrence is reported again, once."""

        self._episodes.pop(code, None)

    def rows(self) -> list[BackendHealthDiagnostic]:
        """One typed row per OPEN episode, with its occurrence count."""

        return [
            BackendHealthDiagnostic(
                code=code,
                port=self.port,
                observer_epoch=self._epoch_source(),
                detail_code=episode.detail_code,
                occurrences=episode.occurrences,
                first_wall_time=episode.first_wall_time,
                last_wall_time=episode.last_wall_time,
            )
            for code, episode in sorted(self._episodes.items())
        ]

    def _now(self) -> float:
        return _validated_wall_time(self._clock)


def _default_peer_is_live(pid: int, process_epoch: str) -> bool:
    """Whether the recorded peer writer is still the same live process."""

    if pid <= 1 or process_epoch == UNVERIFIED_PROCESS_EPOCH:
        return False
    return process_epoch_token(pid, process_start_identity(pid) or "") == process_epoch


class BackendHealthStore:
    """Retain bounded backend-health history for exactly one leased web port."""

    def __init__(
        self,
        port: int,
        *,
        state_dir: Path | str | None = None,
        writer_identity: WriterIdentity | None = None,
        host_identity: HostIdentity | None = None,
        clock: Callable[[], float] = wall_clock_now,
        writer: Callable[..., None] = atomic_write_text,
        peer_is_live: Callable[[int, str], bool] = _default_peer_is_live,
        on_diagnostic: Callable[[BackendHealthDiagnostic], None] | None = None,
        new_epoch_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            raise BackendHealthContractError(f"backend health store needs one TCP port, got {port!r}")
        self.port = int(port)
        self.directory = Path(state_dir or STATE_DIR) / BACKEND_HEALTH_DIRECTORY_NAME
        self._clock = clock
        self._writer = writer
        self._peer_is_live = peer_is_live
        self._on_diagnostic = on_diagnostic
        self._new_epoch_id = new_epoch_id
        self._writer_identity = writer_identity or WriterIdentity.for_current_process(
            host_identity=host_identity
        )
        self._episodes = DiagnosticEpisodes(
            self.port,
            clock=self._clock,
            on_diagnostic=on_diagnostic,
            epoch_source=lambda: str(self._document.get("observer_epoch") or ""),
        )
        self._consecutive_write_failures = 0
        self._last_write_failure_reason = ""
        self._last_write_failure_wall_time = 0.0
        self._last_write_success_wall_time = 0.0
        self._blocked_by_writer: WriterIdentity | None = None
        self._document: dict[str, Any] = {}
        self.load()

    # -- paths ---------------------------------------------------------------

    @property
    def document_path(self) -> Path:
        return self.directory / f"{self.port}.json"

    @property
    def quarantine_path(self) -> Path:
        return self.directory / f"{self.port}.json.quarantine"

    # -- public surface ------------------------------------------------------

    def document(self) -> dict[str, Any]:
        """Return the last durably published document (a copy)."""

        return json.loads(json.dumps(self._document))

    def status(self) -> dict[str, Any]:
        """Return the durable document plus the live, unpersistable persistence state.

        Observer LIVENESS is deliberately NOT here. This store owns history and persistence; the
        observer owns the cadence and the thread whose survival is the question, so it owns that
        answer. Keeping a copy here made this module a second owner of a fact it could not see,
        and it aged that fact on the wall clock while the observer schedules on monotonic.
        """

        payload = self.document()
        payload["persistence"] = self.persistence_status()
        return payload

    def persistence_status(self) -> dict[str, Any]:
        """Return the live persistence state, including degradation the file cannot carry.

        A store that cannot write cannot publish the fact that it cannot write, so this
        block is computed in memory and is what makes a failing writer visible to callers.
        """

        state = PERSISTENCE_OK
        reason_code = ""
        if self._blocked_by_writer is not None:
            state = PERSISTENCE_BLOCKED
            reason_code = "writer_conflict"
        elif self._consecutive_write_failures:
            state = PERSISTENCE_DEGRADED
            reason_code = self._last_write_failure_reason or "persist_failed"
        persisted = self._document.get("persistence", {})
        return {
            "state": state,
            "reason_code": reason_code,
            "consecutive_failures": self._consecutive_write_failures,
            "total_failed_publications": int(persisted.get("total_failed_publications") or 0)
            + self._consecutive_write_failures,
            "last_failure_wall_time": self._last_write_failure_wall_time,
            "last_success_wall_time": self._last_write_success_wall_time,
        }

    def diagnostics(self) -> list[BackendHealthDiagnostic]:
        """Return one typed row per OPEN diagnostic episode, with its occurrence count.

        One row per episode, not per occurrence: a write that keeps failing every observation
        interval must not become one diagnostic per interval. ``as_dict()`` is the projection
        for status payloads.
        """

        return self._episodes.rows()

    def load(self) -> dict[str, Any]:
        """Read the durable document, quarantining and resetting anything untrustworthy."""

        with file_lock(self.document_path, dir_mode=0o700):
            raw_text, read_reason = self._read_text()
            if raw_text is None and read_reason == "":
                self._document = self._empty_document(reset_reason="")
                return self.document()
            if raw_text is None:
                self._document = self._reset_document(reason_code=read_reason, raw_text=None)
                return self.document()
            try:
                self._document = self._validated_document(raw_text)
            except _DocumentRejected as rejection:
                self._document = self._reset_document(reason_code=rejection.reason_code, raw_text=raw_text)
        return self.document()

    def record(self, snapshot: HealthSnapshot) -> PublishResult:
        """Fold one observation snapshot into history and publish it atomically."""

        observations = self._validated_snapshot(snapshot)
        if self._writer_conflict() is not None:
            return PublishResult(
                published=False,
                revision=int(self._document.get("revision") or 0),
                persistence_state=PERSISTENCE_BLOCKED,
                reason_code="writer_conflict",
                document=self.document(),
            )
        candidate, deferred = self._reduced_document(self._document, snapshot, observations)
        text = json.dumps(candidate, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            with file_lock(self.document_path, dir_mode=0o700):
                self._writer(self.document_path, text, mode=0o600)
        except OSError as exc:
            self._consecutive_write_failures += 1
            self._last_write_failure_reason = "write_failed"
            self._last_write_failure_wall_time = self._now()
            self._emit(DIAGNOSTIC_PERSIST_FAILED, detail_code=type(exc).__name__[:48].lower())
            # The previous good document stays both on disk (atomic_write_text replaces only
            # after a successful fsync) and in memory, so the next successful publication
            # re-derives every delta from the last durable baseline instead of losing it.
            return PublishResult(
                published=False,
                revision=int(self._document.get("revision") or 0),
                persistence_state=PERSISTENCE_DEGRADED,
                reason_code="write_failed",
                document=self.document(),
            )
        self._document = candidate
        self._consecutive_write_failures = 0
        self._last_write_failure_reason = ""
        self._last_write_success_wall_time = candidate["written_at"]
        self._clear(DIAGNOSTIC_PERSIST_FAILED)
        for code, detail in deferred:
            self._emit(code, detail_code=detail)
        return PublishResult(
            published=True,
            revision=int(candidate["revision"]),
            persistence_state=PERSISTENCE_OK,
            reason_code="",
            document=self.document(),
        )

    # -- input validation ----------------------------------------------------

    def _validated_snapshot(self, snapshot: HealthSnapshot) -> tuple[ResourceObservation, ...]:
        if not isinstance(snapshot, HealthSnapshot):
            raise BackendHealthContractError("record() needs a HealthSnapshot")
        observed_at = _finite_number(snapshot.observed_at)
        if observed_at is None:
            raise BackendHealthContractError(f"snapshot observed_at must be a finite, non-negative wall time, got {snapshot.observed_at!r}")
        seen: set[str] = set()
        for observation in snapshot.resources:
            if not isinstance(observation, ResourceObservation):
                raise BackendHealthContractError("snapshot resources must be ResourceObservation rows")
            if _RESOURCE_ID_RE.fullmatch(str(observation.resource)) is None:
                raise BackendHealthContractError(f"invalid backend health resource id {observation.resource!r}")
            if observation.state not in BACKEND_HEALTH_STATES:
                raise BackendHealthContractError(
                    f"unknown backend health state {observation.state!r} for {observation.resource!r}; "
                    f"expected one of {BACKEND_HEALTH_STATES}"
                )
            if observation.resource in seen:
                raise BackendHealthContractError(f"duplicate resource {observation.resource!r} in one snapshot")
            seen.add(observation.resource)
        return tuple(snapshot.resources)

    # -- reduction (pure) ----------------------------------------------------

    def _reduced_document(
        self,
        previous: Mapping[str, Any],
        snapshot: HealthSnapshot,
        observations: tuple[ResourceObservation, ...],
    ) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        deferred: list[tuple[str, str]] = []
        revision = int(previous.get("revision") or 0) + 1
        observed_at = round(float(snapshot.observed_at), 3)
        resources: dict[str, Any] = json.loads(json.dumps(previous.get("resources") or {}))
        for observation in observations:
            if observation.resource not in resources and len(resources) >= BACKEND_HEALTH_MAX_RESOURCES:
                deferred.append((DIAGNOSTIC_RESOURCE_LIMIT, observation.resource))
                continue
            resources[observation.resource] = self._reduced_resource(
                resources.get(observation.resource),
                observation,
                revision=revision,
                observed_at=observed_at,
                deferred=deferred,
            )
        persistence = dict(previous.get("persistence") or {})
        document = {
            "schema_version": BACKEND_HEALTH_SCHEMA_VERSION,
            "port": self.port,
            "observer_epoch": previous["observer_epoch"],
            "observer_epoch_started_at": previous["observer_epoch_started_at"],
            "revision": revision,
            "written_at": round(self._now(), 3),
            "history_coverage": previous.get("history_coverage") or HISTORY_COVERAGE_FULL,
            "history_reset_reason": previous.get("history_reset_reason") or "",
            "writer": self._writer_identity.as_dict(),
            "persistence": {
                "total_failed_publications": int(persistence.get("total_failed_publications") or 0)
                + self._consecutive_write_failures,
                "last_failure_reason_code": self._last_write_failure_reason
                or str(persistence.get("last_failure_reason_code") or ""),
                "last_failure_wall_time": self._last_write_failure_wall_time
                or float(persistence.get("last_failure_wall_time") or 0.0),
            },
            "resources": resources,
        }
        return document, deferred

    def _reduced_resource(
        self,
        previous: Mapping[str, Any] | None,
        observation: ResourceObservation,
        *,
        revision: int,
        observed_at: float,
        deferred: list[tuple[str, str]],
    ) -> dict[str, Any]:
        prior_current = dict((previous or {}).get("current") or {})
        aggregate = dict((previous or {}).get("aggregate") or self._empty_aggregate())
        transitions = list((previous or {}).get("transitions") or [])
        # The LIFETIME count, carried across eviction, and whether it is exact or only a floor.
        # `transitions` is a bounded 128-row window, so its length stops being the total the moment
        # the 129th change is recorded: a reader told "128 state changes recorded, all of them are
        # shown" at that point is holding a number that will never move again. Both optional fields
        # are resolved by `_transition_totals`, the one owner of the presence-before-validity rules,
        # so the record path and the load path cannot drift apart on the same document.
        transitions_total, transitions_total_exact = _transition_totals(previous, len(transitions))

        reason_code, reason_ok = _normalized_token(observation.reason_code)
        if not reason_ok:
            deferred.append((DIAGNOSTIC_REASON_CODE_INVALID, observation.resource))
        recovery_outcome, recovery_ok = _normalized_token(
            observation.recovery_outcome, invalid="recovery_invalid"
        )
        if not recovery_ok:
            deferred.append((DIAGNOSTIC_RECOVERY_OUTCOME_INVALID, observation.resource))
        if observation.state == "unknown" and reason_code == "none":
            # `unknown` must always say why; an unexplained unknown is the collapse the
            # DOIT forbids, so the store supplies the one typed default rather than a blank.
            reason_code = "observation_failed"

        epoch = process_epoch_token(observation.pid, observation.process_start_identity)
        verified = epoch != UNVERIFIED_PROCESS_EPOCH
        try:
            pid = max(0, int(observation.pid))
        except (TypeError, ValueError):
            pid = 0

        aggregate["observations"] = int(aggregate.get("observations") or 0) + 1
        self._accumulate(aggregate, observation, epoch=epoch, verified=verified, deferred=deferred)

        previous_state = str(prior_current.get("state") or NO_PRIOR_STATE)
        changed = previous_state != observation.state
        if changed:
            transitions.append(
                {
                    "revision": revision,
                    "wall_time": observed_at,
                    "previous_state": previous_state,
                    "new_state": observation.state,
                    "reason_code": reason_code,
                    "process_epoch": epoch,
                    "recovery_outcome": recovery_outcome,
                }
            )
            transitions_total += 1
            if len(transitions) > BACKEND_HEALTH_MAX_TRANSITIONS:
                transitions = transitions[len(transitions) - BACKEND_HEALTH_MAX_TRANSITIONS :]

        completed = float(aggregate.get("completed_count") or 0.0)
        total_latency = float(aggregate.get("latency_total_ms") or 0.0)
        aggregate["latency_average_ms"] = round(total_latency / completed, 3) if completed > 0 else None

        return {
            "current": {
                "state": observation.state,
                "reason_code": reason_code,
                "recovery_outcome": recovery_outcome,
                "process_epoch": epoch,
                "pid": pid,
                "observed_at": observed_at,
                "since_revision": revision if changed else int(prior_current.get("since_revision") or revision),
                "since_wall_time": observed_at
                if changed
                else float(prior_current.get("since_wall_time") or observed_at),
            },
            "aggregate": aggregate,
            "transitions": transitions,
            # Retained window and lifetime count are separate facts, so a truncated list can never
            # be mistaken for the whole history -- and whether that count is exact is a third fact,
            # because a pre-existing document can only ever yield a floor.
            "transitions_total": transitions_total,
            "transitions_total_exact": transitions_total_exact,
        }

    def _accumulate(
        self,
        aggregate: dict[str, Any],
        observation: ResourceObservation,
        *,
        epoch: str,
        verified: bool,
        deferred: list[tuple[str, str]],
    ) -> None:
        """Fold one observation's peer counters into the cumulative aggregate.

        Never emits a negative delta and never invents a count: an unusable sample adds
        exactly zero and degrades coverage instead.
        """

        last_sample = dict(aggregate.get("last_sample") or self._empty_sample())
        last_verified_epoch = str(aggregate.get("last_verified_epoch") or "")

        if verified and epoch != last_verified_epoch:
            if last_verified_epoch:
                aggregate["restart_count"] = int(aggregate.get("restart_count") or 0) + 1
                if not bool(last_sample.get("counters_available")):
                    self._mark_partial(aggregate, COVERAGE_REASON_MISSED_FINAL)
            aggregate["verified_epochs"] = int(aggregate.get("verified_epochs") or 0) + 1
            aggregate["last_verified_epoch"] = epoch
            aggregate["epoch_latency_max_ms"] = None
            aggregate["epoch_latency_max_process_epoch"] = epoch
            # A new verified epoch baselines at the new process's own zero, not at the dead
            # process's final value. That is what makes a restart add the new process's work
            # instead of subtracting the old one's, so no delta can ever be negative. The
            # baseline is flagged unread until a sample is actually taken from this epoch.
            last_sample = self._empty_sample()
            last_sample["process_epoch"] = epoch

        if not verified or not observation.counters_available:
            # Keep the numeric baseline and its epoch: the counters are cumulative, so a
            # blind cycle inside one epoch loses nothing once reading resumes. Only the
            # availability flag drops, because that is what proves a lost final sample.
            last_sample["counters_available"] = False
            aggregate["last_sample"] = last_sample
            return

        sample = self._usable_sample(observation)
        if sample is None:
            deferred.append((DIAGNOSTIC_PEER_COUNTERS_INVALID, observation.resource))
            self._mark_partial(aggregate, COVERAGE_REASON_CORRUPT_COUNTERS)
            last_sample["counters_available"] = False
            aggregate["last_sample"] = last_sample
            return

        baseline_epoch = str(last_sample.get("process_epoch") or "")
        if baseline_epoch == epoch:
            rolled_back = any(
                sample[name] < float(last_sample.get(name) or 0.0)
                for name in (*_COUNTER_FIELDS, "latency_total_ms")
            )
            if rolled_back:
                # A counter that went backwards inside one verified epoch is peer data this
                # store cannot reconcile. Add zero, say so, and re-baseline below.
                self._mark_partial(aggregate, COVERAGE_REASON_ROLLBACK)
            else:
                for name in _COUNTER_FIELDS:
                    delta = sample[name] - float(last_sample.get(name) or 0.0)
                    aggregate[name] = int(aggregate.get(name) or 0) + int(delta)
                latency_delta = sample["latency_total_ms"] - float(last_sample.get("latency_total_ms") or 0.0)
                aggregate["latency_total_ms"] = round(
                    float(aggregate.get("latency_total_ms") or 0.0) + latency_delta, 3
                )

        recorded_max = aggregate.get("epoch_latency_max_ms")
        observed_max = sample["latency_max_ms"]
        aggregate["epoch_latency_max_ms"] = (
            round(observed_max, 3) if recorded_max is None else round(max(float(recorded_max), observed_max), 3)
        )
        aggregate["epoch_latency_max_process_epoch"] = epoch
        aggregate["last_sample"] = {
            "process_epoch": epoch,
            "counters_available": True,
            "request_count": sample["request_count"],
            "error_count": sample["error_count"],
            "completed_count": sample["completed_count"],
            "latency_total_ms": sample["latency_total_ms"],
        }

    def _usable_sample(self, observation: ResourceObservation) -> dict[str, float] | None:
        values: dict[str, float] = {}
        for name in (*_COUNTER_FIELDS, "latency_total_ms", "latency_max_ms"):
            number = _finite_number(getattr(observation, name))
            if number is None:
                return None
            if name in _COUNTER_FIELDS and number != float(int(number)):
                return None
            values[name] = number
        return values

    def _mark_partial(self, aggregate: dict[str, Any], reason_code: str) -> None:
        aggregate["coverage"] = AGGREGATE_COVERAGE_PARTIAL
        reasons = list(aggregate.get("coverage_reasons") or [])
        if reason_code not in reasons and len(reasons) < BACKEND_HEALTH_MAX_COVERAGE_REASONS:
            reasons.append(reason_code)
            reasons.sort()
        aggregate["coverage_reasons"] = reasons

    # -- persistence ---------------------------------------------------------

    def _read_text(self) -> tuple[str | None, str]:
        """Return ``(text, reset_reason)``; ``(None, "")`` when the file simply does not exist."""

        try:
            data = self.document_path.read_bytes()
        except FileNotFoundError:
            return None, ""
        except OSError:
            # `read_json_file` collapses "absent" and "unreadable" into one default, and this
            # owner must distinguish them -- absent is a first run, unreadable is a reset --
            # so the read is explicit here rather than routed through that helper.
            return None, RESET_HISTORY_UNREADABLE
        # Lossy decoding on purpose: invalid UTF-8 must still reach the quarantine copy as
        # evidence, and it will fail JSON validation immediately afterwards.
        return data.decode("utf-8", "replace"), ""

    def _validated_document(self, raw_text: str) -> dict[str, Any]:
        try:
            raw = json.loads(raw_text)
        except (ValueError, RecursionError) as exc:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT) from exc
        if not isinstance(raw, dict):
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        if raw.get("schema_version") != BACKEND_HEALTH_SCHEMA_VERSION:
            raise _DocumentRejected(RESET_HISTORY_SCHEMA_UNSUPPORTED)
        if raw.get("port") != self.port:
            raise _DocumentRejected(RESET_HISTORY_PORT_MISMATCH)
        epoch_id = str(raw.get("observer_epoch") or "")
        if re.fullmatch(r"[0-9a-f]{8,64}", epoch_id) is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        revision = raw.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        epoch_started_at = _finite_number(raw.get("observer_epoch_started_at"))
        written_at = _finite_number(raw.get("written_at"))
        if epoch_started_at is None or written_at is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        coverage = raw.get("history_coverage")
        if coverage not in {HISTORY_COVERAGE_FULL, HISTORY_COVERAGE_RESET}:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        reset_reason = str(raw.get("history_reset_reason") or "")
        if reset_reason and _REASON_CODE_RE.fullmatch(reset_reason) is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        if not isinstance(raw.get("writer"), dict):
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        writer = WriterIdentity.from_mapping(raw["writer"])
        persistence = raw.get("persistence")
        if not isinstance(persistence, dict):
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        resources_raw = raw.get("resources")
        if not isinstance(resources_raw, dict) or len(resources_raw) > BACKEND_HEALTH_MAX_RESOURCES:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        resources = {
            name: self._validated_resource(name, value) for name, value in sorted(resources_raw.items())
        }
        return {
            "schema_version": BACKEND_HEALTH_SCHEMA_VERSION,
            "port": self.port,
            "observer_epoch": epoch_id,
            "observer_epoch_started_at": epoch_started_at,
            "revision": revision,
            "written_at": written_at,
            "history_coverage": coverage,
            "history_reset_reason": reset_reason,
            "writer": writer.as_dict(),
            "persistence": {
                "total_failed_publications": max(0, int(persistence.get("total_failed_publications") or 0)),
                "last_failure_reason_code": str(persistence.get("last_failure_reason_code") or ""),
                "last_failure_wall_time": float(_finite_number(persistence.get("last_failure_wall_time")) or 0.0),
            },
            "resources": resources,
        }

    def _validated_resource(self, name: str, value: Any) -> dict[str, Any]:
        if _RESOURCE_ID_RE.fullmatch(str(name)) is None or not isinstance(value, dict):
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        current = value.get("current")
        aggregate = value.get("aggregate")
        transitions = value.get("transitions")
        if not isinstance(current, dict) or not isinstance(aggregate, dict) or not isinstance(transitions, list):
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        if current.get("state") not in BACKEND_HEALTH_STATES:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        if len(transitions) > BACKEND_HEALTH_MAX_TRANSITIONS:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        for row in transitions:
            if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(TRANSITION_ROW_FIELDS)):
                raise _DocumentRejected(RESET_HISTORY_CORRUPT)
            if row["new_state"] not in BACKEND_HEALTH_STATES:
                raise _DocumentRejected(RESET_HISTORY_CORRUPT)
            if row["previous_state"] not in (*BACKEND_HEALTH_STATES, NO_PRIOR_STATE):
                raise _DocumentRejected(RESET_HISTORY_CORRUPT)
            if _REASON_CODE_RE.fullmatch(str(row["reason_code"])) is None:
                raise _DocumentRejected(RESET_HISTORY_CORRUPT)
            if _REASON_CODE_RE.fullmatch(str(row["recovery_outcome"])) is None:
                raise _DocumentRejected(RESET_HISTORY_CORRUPT)
            if _PROCESS_EPOCH_RE.fullmatch(str(row["process_epoch"])) is None:
                raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        # The lifetime count has to survive a reload, or restarting the process would silently
        # reset the total back to the retained window and reintroduce the defect one boot later.
        # Both optional fields go through `_transition_totals`, which is where the presence-first
        # rules live and the reason this path and the record path can no longer answer the same
        # document differently. Neither a broken counter nor a broken flag costs HISTORY here:
        # a disagreement about a number is not a reason to throw away the rows beside it.
        total, exact = _transition_totals(value, len(transitions))
        return {
            "current": self._validated_current(current),
            "aggregate": self._validated_aggregate(aggregate),
            "transitions": [{key: row[key] for key in TRANSITION_ROW_FIELDS} for row in transitions],
            "transitions_total": int(total),
            "transitions_total_exact": bool(exact),
        }

    def _validated_current(self, current: Mapping[str, Any]) -> dict[str, Any]:
        epoch = str(current.get("process_epoch") or UNVERIFIED_PROCESS_EPOCH)
        if _PROCESS_EPOCH_RE.fullmatch(epoch) is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        reason_code, reason_ok = _normalized_token(current.get("reason_code"))
        recovery, recovery_ok = _normalized_token(current.get("recovery_outcome"), invalid="recovery_invalid")
        if not reason_ok or not recovery_ok:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        return {
            "state": current["state"],
            "reason_code": reason_code,
            "recovery_outcome": recovery,
            "process_epoch": epoch,
            "pid": max(0, int(current.get("pid") or 0)),
            "observed_at": float(_finite_number(current.get("observed_at")) or 0.0),
            "since_revision": max(0, int(current.get("since_revision") or 0)),
            "since_wall_time": float(_finite_number(current.get("since_wall_time")) or 0.0),
        }

    def _validated_aggregate(self, aggregate: Mapping[str, Any]) -> dict[str, Any]:
        coverage = aggregate.get("coverage")
        if coverage not in {AGGREGATE_COVERAGE_FULL, AGGREGATE_COVERAGE_PARTIAL}:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        reasons_raw = aggregate.get("coverage_reasons")
        if not isinstance(reasons_raw, list) or len(reasons_raw) > BACKEND_HEALTH_MAX_COVERAGE_REASONS:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        reasons = []
        for reason in reasons_raw:
            if _REASON_CODE_RE.fullmatch(str(reason)) is None:
                raise _DocumentRejected(RESET_HISTORY_CORRUPT)
            reasons.append(str(reason))
        last_verified_epoch = str(aggregate.get("last_verified_epoch") or "")
        if last_verified_epoch and _PROCESS_EPOCH_RE.fullmatch(last_verified_epoch) is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        sample_raw = aggregate.get("last_sample")
        if not isinstance(sample_raw, dict):
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        sample_epoch = str(sample_raw.get("process_epoch") or "")
        if sample_epoch and _PROCESS_EPOCH_RE.fullmatch(sample_epoch) is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        counters: dict[str, Any] = {}
        for name in (*_COUNTER_FIELDS, "latency_total_ms"):
            number = _finite_number(aggregate.get(name))
            if number is None:
                raise _DocumentRejected(RESET_HISTORY_CORRUPT)
            counters[name] = int(number) if name in _COUNTER_FIELDS else round(number, 3)
        maximum = aggregate.get("epoch_latency_max_ms")
        if maximum is not None and _finite_number(maximum) is None:
            raise _DocumentRejected(RESET_HISTORY_CORRUPT)
        return {
            "coverage": coverage,
            "coverage_reasons": reasons,
            "restart_count": max(0, int(aggregate.get("restart_count") or 0)),
            "verified_epochs": max(0, int(aggregate.get("verified_epochs") or 0)),
            "observations": max(0, int(aggregate.get("observations") or 0)),
            **counters,
            "latency_average_ms": (
                round(counters["latency_total_ms"] / counters["completed_count"], 3)
                if counters["completed_count"] > 0
                else None
            ),
            "epoch_latency_max_ms": None if maximum is None else round(float(maximum), 3),
            "epoch_latency_max_process_epoch": str(aggregate.get("epoch_latency_max_process_epoch") or ""),
            "last_verified_epoch": last_verified_epoch,
            "last_sample": {
                "process_epoch": sample_epoch,
                "counters_available": bool(sample_raw.get("counters_available")),
                "request_count": float(_finite_number(sample_raw.get("request_count")) or 0.0),
                "error_count": float(_finite_number(sample_raw.get("error_count")) or 0.0),
                "completed_count": float(_finite_number(sample_raw.get("completed_count")) or 0.0),
                "latency_total_ms": float(_finite_number(sample_raw.get("latency_total_ms")) or 0.0),
            },
        }

    def _reset_document(self, *, reason_code: str, raw_text: str | None) -> dict[str, Any]:
        """Quarantine at most one bounded copy and start a new observer epoch."""

        if raw_text is not None:
            try:
                self._writer(
                    self.quarantine_path,
                    raw_text[:BACKEND_HEALTH_QUARANTINE_MAX_BYTES],
                    mode=0o600,
                )
            except OSError as exc:
                self._emit(DIAGNOSTIC_PERSIST_FAILED, detail_code=type(exc).__name__[:48].lower())
        self._emit(DIAGNOSTIC_HISTORY_RESET, detail_code=reason_code)
        return self._empty_document(reset_reason=reason_code)

    def _writer_conflict(self) -> WriterIdentity | None:
        """Return a live foreign writer for this port, re-checking a previous conflict."""

        recorded_raw = self._document.get("writer") or {}
        try:
            recorded = WriterIdentity.from_mapping(recorded_raw)
        except _DocumentRejected:
            self._blocked_by_writer = None
            return None
        same_process = (
            recorded.stable_host_id == self._writer_identity.stable_host_id
            and recorded.boot_id == self._writer_identity.boot_id
            and recorded.pid == self._writer_identity.pid
            and recorded.process_epoch == self._writer_identity.process_epoch
        )
        foreign_host = (
            recorded.stable_host_id != self._writer_identity.stable_host_id
            or recorded.boot_id != self._writer_identity.boot_id
        )
        if same_process or foreign_host or not self._peer_is_live(recorded.pid, recorded.process_epoch):
            if self._blocked_by_writer is not None:
                # The peer that held the lease is gone. Re-read the file so this writer
                # continues that history's revision instead of rewinding it.
                self._blocked_by_writer = None
                self._clear(DIAGNOSTIC_WRITER_CONFLICT)
                self.load()
            return None
        self._blocked_by_writer = recorded
        self._emit(DIAGNOSTIC_WRITER_CONFLICT, detail_code=f"pid_{recorded.pid}")
        return recorded

    # -- diagnostics ---------------------------------------------------------

    def _emit(self, code: str, *, detail_code: str = "") -> None:
        self._episodes.emit(code, detail_code=detail_code)

    def _clear(self, code: str) -> None:
        """End one diagnostic episode so a later recurrence is reported again, once."""

        self._episodes.clear(code)

    # -- construction helpers ------------------------------------------------

    def _now(self) -> float:
        return _validated_wall_time(self._clock)

    def _empty_document(self, *, reset_reason: str) -> dict[str, Any]:
        now = round(self._now(), 3)
        return {
            "schema_version": BACKEND_HEALTH_SCHEMA_VERSION,
            "port": self.port,
            "observer_epoch": self._new_epoch_id(),
            "observer_epoch_started_at": now,
            "revision": 0,
            "written_at": now,
            "history_coverage": HISTORY_COVERAGE_RESET if reset_reason else HISTORY_COVERAGE_FULL,
            "history_reset_reason": reset_reason,
            "writer": self._writer_identity.as_dict(),
            "persistence": {
                "total_failed_publications": 0,
                "last_failure_reason_code": "",
                "last_failure_wall_time": 0.0,
            },
            "resources": {},
        }

    def _empty_aggregate(self) -> dict[str, Any]:
        return {
            "coverage": AGGREGATE_COVERAGE_FULL,
            "coverage_reasons": [],
            "restart_count": 0,
            "verified_epochs": 0,
            "observations": 0,
            "request_count": 0,
            "error_count": 0,
            "completed_count": 0,
            "latency_total_ms": 0.0,
            "latency_average_ms": None,
            "epoch_latency_max_ms": None,
            "epoch_latency_max_process_epoch": "",
            "last_verified_epoch": "",
            "last_sample": self._empty_sample(),
        }

    def _empty_sample(self) -> dict[str, Any]:
        return {
            "process_epoch": "",
            "counters_available": False,
            "request_count": 0.0,
            "error_count": 0.0,
            "completed_count": 0.0,
            "latency_total_ms": 0.0,
        }
