# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The continuous backend-health observer (M4 of ``DOIT.p0.daemon-monitor.md``).

WHY THIS IS NOT A SECOND SAMPLING LOOP -- THE RECORDED DECISION
--------------------------------------------------------------
A periodic sampler already exists. ``TmuxWebtermApp.collect_current_stats_service_load``
(``app.py:2766``) is driven by the ``service_load`` stats family on a 10s visible / 60s hidden
cadence (``stats_current/families.py:153-159``) and persists exactly three numbers per service:
``running``, ``cpu_percent``, ``rss_bytes``. The DOIT is explicit that two sampling loops over
one projection is the divergent-copy defect this codebase fails on most, so the choice has to
be recorded rather than assumed.

**Decision: the observer runs BESIDE the ``service_load`` collector and does not extend it.**

Three reasons it cannot be folded into that owner:

1. ``service_load`` runs only in the process holding the stats-collector role. Health is scoped
   to the LEASED WEB PORT -- the retained store is ``STATE_DIR/backend-health/<port>.json`` and
   the port lease is what makes it single-writer. An observer that inherited the stats-collector
   role would stop observing whenever that role moved to another process, which is the exact
   "health is only computed when something else happens to be running" defect.
2. ``service_load``'s cadence is browser-visibility driven. Health needs a fixed 2.0s interval;
   folding it in would make hard-failure detection take 60 seconds whenever nobody is looking,
   which is the originating incident restated.
3. The two have different retention owners and different bounds: ``service_load`` writes numeric
   series into the stats database, health writes typed states and bounded transition rows into
   :mod:`yolomux_lib.backend_health.store`.

**Why the two cannot disagree.** Neither of them produces a row. Both consume
``TmuxWebtermApp.local_services_row_producers()`` -- the ONE map from service id to the callable
that owns that service's whole row -- and both compose it through the ONE
:class:`~yolomux_lib.local_service_projection.LocalServicesCollector`. There is no second
inventory, no second row shape, no second derivation of ``running``/``pid``/``cpu``/``rss``, and
no cached copy on either side. The observer differs in exactly two ways, both additive and both
visible here: it wraps each producer in a per-service timeout before handing the map to the
collector, and it does not ask for the process ledger or the statsd recovery events that the
HTTP projection needs. If a row's shape changes, it changes for both consumers in the same call.

WHAT ONE CYCLE DOES
-------------------
1. Enter :func:`~yolomux_lib.local_services.rpc.local_service_probe_scope` so every RPC issued
   by a row producer -- including the ones the registry issues underneath it -- is attributed to
   probe traffic and cannot enter the user-work request/latency aggregate.
2. Fan the six row producers out concurrently, bounded by
   ``BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS``. A probe that does not finish yields
   ``unknown``/``probe_timeout`` for that resource and never blocks the cycle.
3. Compose the results through ``LocalServicesCollector`` so row derivation stays with its owner.
4. Reduce each row to one typed state from ``BACKEND_HEALTH_STATES`` (see :func:`observed_health`).
5. Debounce: ``down`` and ``upgrade_required`` are immediate; every other degradation and every
   recovery needs ``BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS`` consecutive identical observations, so
   one timed-out probe cannot flicker the indicator.
6. Publish only when the stable signature changed: record one snapshot into the retained store
   and emit exactly one ``backend_health_changed`` client event (M9).

NOTHING HERE MAY START A SERVICE. The observer never names ``ensure_started`` or
``acquire_lease``; it only calls the row producers, every one of which reads status or a
persisted record. ``tests/test_backend_health_catalog.py`` asserts that for the projection path
and ``tests/test_backend_health_observer.py`` asserts it for this module.

BOUNDED NON-DESTRUCTIVE RECOVERY (M7)
-------------------------------------
A VERIFIED-DOWN service -- and nothing else -- may have ``retry`` issued for it, at most once
per backoff boundary, on the ladder ``BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS``. The ONE
operation this module is allowed to ask for is ``retry``; it is issued from the one choke point
:func:`_issue_retry` so "which methods can the observer reach on a service control" is a
question with a three-line answer instead of a code review. There is no stop, restart, signal,
socket unlink, process reclaim, or adoption anywhere on this path, and no fallback that could
become one.

Eligibility is an ALLOWLIST, not a denylist: only ``down`` with ``service_absent``, ``exited``
or ``probe_failed`` is retried. Every other cause performs zero mutations and publishes its own
bounded ``retry_blocked_<cause>`` token, so ``upgrade_required``, ``terminal_failure``,
``identity_mismatch``, ``revision_mismatch``, a demand-started service that is legitimately
absent, and jobd's ``scheduler_not_owned`` stay six distinguishable facts rather than one
"blocked" string. Collapsing them is the exact defect the health contract names.

THE STARTUP FLASH, AND WHY THE FIRST BOUNDARY IS NOT THE FENCE
--------------------------------------------------------------
The observer arms at boot before statsd is serving, so statsd used to read a verified ``down``
at every start. MEASURED on a real isolated start, port 17781 (the timeline recorded at
``app.STATSD_ABSENT_WHILE_PIN_PENDING``): the election was decided at +0.632s, the first
completed observation published ``down``/``service_absent`` at +0.635s, statsd was spawned at
+1.136s, began serving at +1.622s, and published ``ready`` at +4.696s. The flash was 4.06s.

Against that measurement the ladder's first boundary -- 1.0s from the outage, so the first
observation at or after it, ~2.6s into the boot -- lands INSIDE the flash. It is not long
enough, and lengthening it would delay every real outage to pay for one boot. Two separate
fences cover it instead:

1. ``app.statsd_pin_pending()`` now publishes ``absence_expected_reason=stats_pin_pending``
   while this process is mid-flight taking the statsd pin, so the boot window reduces to
   ``starting`` rather than ``down``. :func:`recovery_row_fence` blocks unconditionally on any
   declared ``absence_expected_reason``, so recovery cannot act inside it even if the reduced
   state were wrong.
2. ``BACKEND_HEALTH_RECOVERY_ARMING_SECONDS`` covers the residual: the interval before the pin
   owner exists at all, a boot where the excuse is withdrawn early, and every other service's
   own start. No retry is issued until the observer has been observing that long, and a service
   that is down inside the window publishes ``retry_blocked_observer_arming`` -- the flash stays
   visible in history and states why nothing was done, instead of being swallowed. 30.0s is
   7.4x the measured 4.06s flash. Boundaries that fall inside the window are not re-armed: a
   service still down when the window ends is retried at once, because by then its outage is
   real.

COUNTERS ARE DELIBERATELY NOT FED HERE
--------------------------------------
:class:`~yolomux_lib.backend_health.store.ResourceObservation` accepts request/error/latency
counters that are cumulative SINCE PROCESS START, and re-baselines them to zero on each verified
process epoch. The retained aggregate that exists today
(``local_services.rpc.local_service_traffic_snapshot``) is cumulative in the WEB process and
deliberately survives a peer restart. Feeding it in directly would add one epoch's worth of work
twice at every restart. Reconciling the two is M6/M8's job and needs its own accounting tests, so
this observer reports ``counters_available=False`` -- the store then marks coverage ``partial``
rather than publishing an invented count, which is what the contract asks for.
"""

from __future__ import annotations

import re
import threading
import traceback
from collections.abc import Callable
from collections.abc import Mapping
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
from time import monotonic as monotonic_now
from time import time as wall_clock_now
from typing import Any

from ..infra.host_identity import process_start_identity
from ..local_service_projection import LOCAL_SERVICE_INVENTORY
from ..local_service_projection import LocalServicesCollector
from ..local_services.rpc import LOCAL_SERVICE_REASON_IDENTITY_MISMATCH
from ..local_services.rpc import LOCAL_SERVICE_REASON_REVISION_MISMATCH
from ..local_services.rpc import local_service_probe_scope
from .store import DIAGNOSTIC_CYCLE_FAILED
from .store import BackendHealthDiagnostic
from .store import BackendHealthStore
from .store import DiagnosticEpisodes
from .store import HealthSnapshot
from .store import ResourceObservation


BACKEND_HEALTH_OBSERVE_SECONDS = 2.0
BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS = 0.5
# Two consecutive identical observations. One is what a single timed-out probe can produce.
BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS = 2

BACKEND_HEALTH_EVENT = "backend_health_changed"
# The event names affected resources, it is not a report. A run of the list is bounded well above
# the six-service inventory so an inventory change stays visible, and well below anything that
# could make an SSE frame large.
BACKEND_HEALTH_EVENT_MAX_RESOURCES = 16

# `down` means a verified failure and `upgrade_required` means a protocol fence: both are facts
# a second observation cannot make truer, and both are exactly what the user must see at once.
BACKEND_HEALTH_IMMEDIATE_STATES = frozenset({"down", "upgrade_required"})

# Highest severity first. `overall_state` is the first entry any resource currently holds.
# `starting` outranks `ready` on purpose -- a service that is not serving yet is not ready -- but
# it is NOT in BACKEND_HEALTH_DEGRADED_STATES, so it never raises the indicator.
BACKEND_HEALTH_STATE_SEVERITY = (
    "down",
    "upgrade_required",
    "backoff",
    "degraded",
    "unknown",
    "starting",
    "ready",
)
BACKEND_HEALTH_DEGRADED_STATES = frozenset({"down", "upgrade_required", "backoff", "degraded", "unknown"})

# Bounded reason tokens. Every one of these is already in the store's documented vocabulary; the
# observer deliberately mints no new ones, because a reason code is a contract the UI reads.
REASON_NONE = "none"
REASON_SERVICE_ABSENT = "service_absent"
REASON_PROBE_TIMEOUT = "probe_timeout"
REASON_PROBE_FAILED = "probe_failed"
REASON_START_BLOCKED = "start_blocked"
REASON_TERMINAL_FAILURE = "terminal_failure"
REASON_UPGRADE_REQUIRED = "upgrade_required"
REASON_EXITED = "exited"
# The two ways a row can be self-contradictory or unreadable about its own absence. Both are a
# contract error rather than a health state, so both resolve in the SAFE direction (`down`): a
# service that cannot say clearly why its absence is expected does not get to be excused by it.
REASON_ABSENCE_CONTRACT_CONFLICT = "absence_contract_conflict"
REASON_ABSENCE_REASON_INVALID = "absence_reason_invalid"
# The two transport failures that are a FENCE rather than an outage: the peer answered, and what
# it answered proves this client is talking to the wrong process or the wrong protocol revision.
# They arrive as the already-typed tokens `local_service_failure_reason` mints (`rpc.py:89-90`);
# reusing those constants is what keeps one vocabulary instead of a second copy here. Retrying
# either would restart a service to re-learn a fact that a restart cannot change, so recovery
# blocks on both -- which is only possible because they do not collapse into `probe_failed`.
REASON_IDENTITY_MISMATCH = LOCAL_SERVICE_REASON_IDENTITY_MISMATCH
REASON_REVISION_MISMATCH = LOCAL_SERVICE_REASON_REVISION_MISMATCH
_FENCED_TRANSPORT_REASONS = frozenset({REASON_IDENTITY_MISMATCH, REASON_REVISION_MISMATCH})

# TWO INDEPENDENT ROW FACTS, ONE RULE EACH -- DO NOT MERGE THEM AND DO NOT SET BOTH
# ---------------------------------------------------------------------------------
# `demand_started: True` is the STATIC fact that NOTHING in a running system keeps this service
#   hot. Its resting state is absent, and being absent is what it does when nobody is asking.
#   Declared by indexd, watchd, statusd and approvald.
#
# `absence_expected_reason: "<token>"` is the DYNAMIC fact that this service IS pinned up by a
#   named owner in this process, and that owner is not engaged right now. Declared by jobd,
#   whose broker is pinned by the scheduler lease `JobClient.start_for_scheduler()` holds while
#   this process owns background scheduling (`infra/jobd.py:1364-1372`, `app.py:2962`).
#
# The distinction is the whole point. A service a background loop keeps hot is NOT demand-scoped
# even though it is lazily created, and flagging it `demand_started` would make a real outage
# read as routine idleness -- the opposite failure, and the worse one. statsd is exactly that
# case: `StatsCurrentRuntime._supervise` leases it and the collector appends to it every second
# (`stats_current/runtime.py:365-368`, cadence `stats_current/families.py:130-134`), so statsd
# declares NEITHER field and its absence stays a verified `down`.
ABSENCE_EXPECTED_REASON_FIELD = "absence_expected_reason"
# The same bounded token shape the retained store accepts (`store.py:22`). A token the store
# would reject must not reach it from here.
_ABSENCE_REASON_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

PROBE_OK = "ok"
PROBE_TIMEOUT = "timeout"
PROBE_FAILED = "failed"

# How many consecutive observer cycles may be missed before the monitor is reported as stopped.
# A single skipped cycle is scheduling jitter; five in a row at a 2.0s cadence is ten seconds of a
# monitor that is not looking, which is a fact worth a banner. This lives HERE because the deadline
# is derived from `interval_seconds`, which this module owns -- a copy of it anywhere else is a
# second owner of the cadence.
BACKEND_HEALTH_LIVENESS_MISSED_CYCLES = 5
# A floor, so a pathologically small injected interval cannot make every reader look dead.
BACKEND_HEALTH_LIVENESS_FLOOR_SECONDS = 5.0
# The three typed reasons a monitor is not reporting itself alive. They are separate because the
# fixes are separate: nothing has started yet, cycles are being attempted and thrown, and cycles
# stopped being attempted at all.
BACKEND_HEALTH_NO_CYCLE_OBSERVED = "no_observer_cycle_recorded"
BACKEND_HEALTH_CYCLE_FAILING = "observer_cycles_failing"
BACKEND_HEALTH_CYCLES_STALLED = "observer_cycles_stalled"

BACKEND_HEALTH_OBSERVER_THREAD_PREFIX = "backend-health"

_INITIAL_HEALTH = ("starting", REASON_NONE)


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = int(value)
    return number if number > 0 else 0


def _positive_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if number > 0.0 else 0.0


def _absence_expected_reason(fields: Mapping[str, Any]) -> tuple[str, bool]:
    """Return ``(bounded_token, readable)`` for the row's ``absence_expected_reason``.

    An absent field and an empty string both mean "this service's absence IS a failure" and are
    readable. Anything that is not a bounded token is NOT readable: the caller fails closed on
    it, because an unparseable excuse must never be able to silence a real outage.
    """

    raw = fields.get(ABSENCE_EXPECTED_REASON_FIELD)
    if raw is None:
        return "", True
    if not isinstance(raw, str):
        return "", False
    token = raw.strip()
    if not token:
        return "", True
    if not _ABSENCE_REASON_TOKEN.match(token):
        return "", False
    return token, True


def observed_health(fields: Mapping[str, Any], probe_outcome: str = PROBE_OK) -> tuple[str, str]:
    """Reduce one collected service row plus its probe outcome to ``(state, reason_code)``.

    The five causes the health contract forbids collapsing stay five distinct reason codes.
    Absence is a failure unless the row says otherwise, and a row has exactly two ways to say
    so -- see the ``ABSENCE_EXPECTED_REASON_FIELD`` block above. Either one yields
    ``starting``, the one non-serving, non-degraded state, so it never reaches
    ``degraded_resources``; declaring BOTH is a contract error and yields ``down``.

    Both excuses are read LAST, after ``terminal_failure``, ``transport_reason``,
    ``last_failure`` and ``restart_backoff_seconds``. That ordering is the safety property: a
    service that recorded a real failure alarms whether or not its absence would otherwise have
    been expected, which is what stops a legitimately-idle service from becoming a permanently
    silent one the moment it actually breaks.

    ``upgrade_required``, ``terminal_failure`` and ``restart_backoff_seconds`` are read when the
    row carries them. Only ``indexd`` publishes a backoff today and none of the six publishes the
    other two, so those branches are reachable exactly as far as the projection is truthful --
    which is M8's extension, not something to guess at here.
    """

    if probe_outcome == PROBE_TIMEOUT:
        return "unknown", REASON_PROBE_TIMEOUT
    if probe_outcome != PROBE_OK:
        return "unknown", REASON_PROBE_FAILED
    if fields.get("upgrade_required"):
        return "upgrade_required", REASON_UPGRADE_REQUIRED
    running = _positive_int(fields.get("pid")) > 0
    transport_reason = str(fields.get("transport_reason") or "").strip()
    # A transport failure the RPC owner already typed as a fence keeps its own name. Everything
    # else stays `probe_failed`: this is not a place to re-derive a vocabulary, only to stop
    # discarding two names the contract forbids discarding.
    transport_code = transport_reason if transport_reason in _FENCED_TRANSPORT_REASONS else REASON_PROBE_FAILED
    last_failure = str(fields.get("last_failure") or "").strip()
    if running:
        if transport_reason:
            return "degraded", transport_code
        if fields.get("healthy") is False or last_failure:
            return "degraded", REASON_TERMINAL_FAILURE
        return "ready", REASON_NONE
    if fields.get("terminal_failure"):
        return "down", REASON_TERMINAL_FAILURE
    if transport_reason:
        return "down", transport_code
    if last_failure:
        return "down", REASON_EXITED
    if _positive_float(fields.get("restart_backoff_seconds")) > 0.0:
        return "backoff", REASON_START_BLOCKED
    demand_started = fields.get("demand_started") is True
    expected_reason, reason_readable = _absence_expected_reason(fields)
    if not reason_readable:
        return "down", REASON_ABSENCE_REASON_INVALID
    if demand_started and expected_reason:
        # Two different owners each claiming this absence is fine is exactly the divergent-copy
        # defect the two fields are kept apart to prevent. Neither claim is trusted.
        return "down", REASON_ABSENCE_CONTRACT_CONFLICT
    if demand_started:
        return "starting", REASON_SERVICE_ABSENT
    if expected_reason:
        return "starting", expected_reason
    return "down", REASON_SERVICE_ABSENT


# -- M7: bounded, non-destructive recovery -------------------------------------------------

# 1, 2, 4, then at most 8 seconds, measured from the start of the outage. The ladder is
# BOUNDED: four attempts spanning 15 seconds, after which the resource holds `retry_exhausted`
# until it is verified `ready` again. An unbounded 8-second ladder would keep starting a service
# that has proven it cannot stay up, forever, on every leased port -- and the operator-driven
# retry that already exists (`/api/stats-current/retry` -> `LocalServiceClient.retry`) is the
# path for a service that needs more than this.
BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)
BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS = len(BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS)
# The boot fence. See THE STARTUP FLASH in the module docstring: the first ladder boundary is
# one second and the measured statsd boot flash is longer than that, so the flash needs its own
# fence rather than a longer first boundary, which would also delay every real outage.
BACKEND_HEALTH_RECOVERY_ARMING_SECONDS = 30.0

# The observer's recovery vocabulary. Every token is a bounded `^[a-z][a-z0-9_]{0,47}$` token
# the retained store accepts verbatim, and every blocked token names ONE cause.
RECOVERY_NONE = "none"
RECOVERY_NOT_ATTEMPTED = "not_attempted"
RECOVERY_SCHEDULED = "retry_scheduled"
RECOVERY_EXHAUSTED = "retry_exhausted"
RECOVERY_RECOVERED = "recovered"
RECOVERY_BLOCKED_PREFIX = "retry_blocked_"
# Used only when a cause token is too long to compose into a bounded token. The row's own
# `reason_code` still carries the cause, so nothing is lost -- see `recovery_blocked_token`.
RECOVERY_BLOCKED_UNNAMED = "retry_blocked_reason_unbounded"

BLOCKED_NO_CONTROL = "no_control"
BLOCKED_OBSERVER_ARMING = "observer_arming"
BLOCKED_CONTROL_ERROR = "control_error"
BLOCKED_DEMAND_STARTED_ABSENT = "demand_started_absent"

# The allowlist. `down` plus one of these three is the only shape that may be retried; the
# allowlist is deliberate, so a reason code added later cannot silently become retryable.
RECOVERY_ELIGIBLE_REASONS = frozenset({REASON_SERVICE_ABSENT, REASON_EXITED, REASON_PROBE_FAILED})

_RECOVERY_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def recovery_blocked_token(cause: str) -> str:
    """Return the bounded ``retry_blocked_<cause>`` token, never a generic one."""

    token = f"{RECOVERY_BLOCKED_PREFIX}{cause}"
    return token if _RECOVERY_TOKEN.match(token) else RECOVERY_BLOCKED_UNNAMED


def recovery_row_fence(fields: Mapping[str, Any]) -> str:
    """Return the cause THE ROW ITSELF declares for never touching this service, or ``""``.

    Deliberately a SECOND, independent fence beside :func:`recovery_blocked_cause`. The reducer
    already turns a declared absence excuse into ``starting`` rather than ``down``, but that is a
    presentation decision, and the discriminator between "statusd is resting" and "statsd died"
    would then be one state name away from starting four daemons on an idle machine. This one
    reads the row's own declarations, so both the classification AND the row have to be wrong
    before a demand-started service can be retried.

    It is not a divergent copy of the excuse parser: the bounded-token read is the same
    :func:`_absence_expected_reason` owner the reducer uses. Only the question differs, and so
    does the ORDER, deliberately:

    * ``absence_expected_reason`` blocks UNCONDITIONALLY while the service is absent. It means a
      named owner in this process is not engaging the service right now -- jobd's
      ``scheduler_not_owned`` is the background-owner election this process lost. Starting jobd
      to "recover" it would fight the winner, and a recorded failure beside it does not change
      who owns scheduling.
    * ``demand_started`` blocks only when the row records NO failure. That is exactly the
      DOIT's "demand-started and LEGITIMATELY absent": a resting statusd is not an outage, but a
      demand-started service that ran and exited is verified down and its own reduced state says
      so. The reducer reads the excuses last for the mirror reason -- a recorded failure must
      still alarm. Alarming and mutating are different decisions and get different orders.
    """

    if _positive_int(fields.get("pid")) > 0:
        return ""
    token, readable = _absence_expected_reason(fields)
    if not readable:
        return REASON_ABSENCE_REASON_INVALID
    if token:
        return token
    if fields.get("demand_started") is not True:
        return ""
    return "" if _row_recorded_failure(fields) else BLOCKED_DEMAND_STARTED_ABSENT


def _row_recorded_failure(fields: Mapping[str, Any]) -> bool:
    """Whether the row itself records a failure, rather than plain absence."""

    return bool(
        str(fields.get("last_failure") or "").strip()
        or str(fields.get("transport_reason") or "").strip()
        or fields.get("terminal_failure")
    )


def recovery_blocked_cause(state: str, reason_code: str) -> str:
    """Return the exact cause that forbids retrying this resource, or ``""`` if none does.

    Read before eligibility, so a fence always wins over an outage: a service can be both
    absent AND upgrade-fenced, and restarting it would only re-learn the fence.
    """

    if state == "upgrade_required":
        return REASON_UPGRADE_REQUIRED
    if reason_code in _FENCED_TRANSPORT_REASONS:
        return reason_code
    if state == "down":
        if reason_code == REASON_TERMINAL_FAILURE:
            # The registry already latched `_terminal_failure` after repeated start exits.
            # `retry()` clears that latch, so retrying here would erase the one fence the
            # lifecycle owner set, which is the opposite of building on top of it.
            return REASON_TERMINAL_FAILURE
        if reason_code in (REASON_ABSENCE_CONTRACT_CONFLICT, REASON_ABSENCE_REASON_INVALID):
            # The row contradicted itself about its own absence. Acting on unreadable input is
            # how an automatic recovery starts services nobody asked for; fail closed instead.
            return reason_code
        return ""
    if state == "starting":
        if reason_code == REASON_SERVICE_ABSENT:
            # A demand-started service resting is not an outage. Retrying here would start
            # statusd, approvald, indexd and watchd on an idle machine every 15 seconds.
            return BLOCKED_DEMAND_STARTED_ABSENT
        if reason_code and reason_code != REASON_NONE:
            # An `absence_expected_reason` token, e.g. jobd's `scheduler_not_owned`. Retrying it
            # would fight the background-owner election this process lost.
            return reason_code
    return ""


@dataclass(frozen=True)
class RecoveryDecision:
    """What recovery did for one resource in one cycle, and why."""

    resource: str
    outcome: str
    attempted: bool = False
    attempts: int = 0
    blocked_cause: str = ""
    next_attempt_at: float = 0.0
    control_result: bool | None = None


@dataclass
class _RecoveryLadder:
    """One resource's outage ladder. Reset only by a verified return to ``ready``."""

    attempts: int = 0
    next_attempt_at: float = 0.0
    # `None`, not 0.0: a monotonic clock may legitimately read zero, and a sentinel that a real
    # reading can equal re-anchors the outage every cycle -- which would move the first boundary
    # forward forever and mean the first retry never happens.
    eligible_since: float | None = None
    outcome: str = RECOVERY_NONE
    control_result: bool | None = None


def _issue_retry(control: Any, resource: str) -> bool:
    """THE ONE PLACE a service control is touched. ``retry`` is the only name reachable here.

    Kept as a three-line module function on purpose: ``tests/test_backend_health_recovery.py``
    parses this body and asserts the set of attribute names in it is exactly ``{"retry"}``, so a
    stop/restart/kill/unlink/reclaim call cannot be added to the recovery path without a test
    failing. The runtime proof is the fake control that raises on every other attribute.
    """

    return bool(control.retry(resource))


class ServiceRecoveryPlanner:
    """Decide, per resource per cycle, whether to issue ``retry`` -- and publish why not.

    Holds no clock of its own: the caller passes the observer's injected monotonic time, so a
    test drives every boundary without sleeping.
    """

    def __init__(
        self,
        *,
        control: Any | None = None,
        backoff_seconds: tuple[float, ...] = BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS,
        max_attempts: int = BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS,
        arming_seconds: float = BACKEND_HEALTH_RECOVERY_ARMING_SECONDS,
    ) -> None:
        self._control = control
        self._backoff = tuple(max(0.0, float(value)) for value in backoff_seconds) or (
            BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS[-1],
        )
        self._max_attempts = max(0, int(max_attempts))
        self._arming_seconds = max(0.0, float(arming_seconds))
        self._lock = threading.RLock()
        self._ladders: dict[str, _RecoveryLadder] = {}
        self._armed_at: float | None = None
        self._attempts_total = 0
        self._control_errors = 0
        self._last_control_error = ""

    @property
    def enabled(self) -> bool:
        return self._control is not None

    def arm(self, now: float) -> None:
        """Stamp the observer's first cycle. The boot fence is measured from here."""

        with self._lock:
            if self._armed_at is None:
                self._armed_at = float(now)

    def armed_until(self) -> float:
        with self._lock:
            return (self._armed_at or 0.0) + self._arming_seconds

    def decide(
        self,
        resource: str,
        state: str,
        reason_code: str,
        now: float,
        fields: Mapping[str, Any] | None = None,
    ) -> RecoveryDecision:
        with self._lock:
            ladder = self._ladders.setdefault(resource, _RecoveryLadder())
            decision = self._decide_locked(resource, state, reason_code, float(now), ladder, fields or {})
            ladder.outcome = decision.outcome
            return decision

    def decide_all(
        self,
        accepted: Mapping[str, tuple[str, str]],
        now: float,
        fields: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, RecoveryDecision]:
        rows = fields or {}
        self.arm(now)
        return {
            resource: self.decide(resource, state, reason_code, now, rows.get(resource))
            for resource, (state, reason_code) in sorted(accepted.items())
        }

    def _decide_locked(
        self,
        resource: str,
        state: str,
        reason_code: str,
        now: float,
        ladder: _RecoveryLadder,
        fields: Mapping[str, Any],
    ) -> RecoveryDecision:
        if state == "ready":
            recovered = ladder.attempts > 0
            ladder.attempts = 0
            ladder.next_attempt_at = 0.0
            ladder.eligible_since = None
            ladder.control_result = None
            return RecoveryDecision(
                resource=resource,
                outcome=RECOVERY_RECOVERED if recovered else RECOVERY_NONE,
            )
        blocked = recovery_row_fence(fields) or recovery_blocked_cause(state, reason_code)
        if blocked:
            # Zero mutations, and the ladder is NOT reset: a service that flips between an
            # outage and a fence must not launder itself a fresh set of attempts by flipping.
            return RecoveryDecision(
                resource=resource,
                outcome=recovery_blocked_token(blocked),
                attempts=ladder.attempts,
                blocked_cause=blocked,
            )
        eligible = state == "down" and reason_code in RECOVERY_ELIGIBLE_REASONS
        if not eligible:
            # `unknown`, `degraded`, `backoff`, and the pre-first-observation `starting` are not
            # verified-down. The resource's own reason code says which one it is; recovery only
            # says that it did nothing.
            outcome = RECOVERY_NONE if state == "starting" and reason_code == REASON_NONE else RECOVERY_NOT_ATTEMPTED
            return RecoveryDecision(resource=resource, outcome=outcome, attempts=ladder.attempts)
        if self._control is None:
            # Recovery is not wired into this process. Saying so is the point: an absent
            # control that published `none` would be indistinguishable from a healthy service.
            return RecoveryDecision(
                resource=resource,
                outcome=recovery_blocked_token(BLOCKED_NO_CONTROL),
                attempts=ladder.attempts,
                blocked_cause=BLOCKED_NO_CONTROL,
            )
        if ladder.eligible_since is None:
            ladder.eligible_since = now
            ladder.next_attempt_at = now + self._delay(ladder.attempts)
        armed_until = (self._armed_at if self._armed_at is not None else now) + self._arming_seconds
        if now < armed_until:
            # The boot flash. Boundaries are left where they are, so a service still down when
            # the fence lifts is retried immediately rather than waiting out the ladder again.
            return RecoveryDecision(
                resource=resource,
                outcome=recovery_blocked_token(BLOCKED_OBSERVER_ARMING),
                attempts=ladder.attempts,
                blocked_cause=BLOCKED_OBSERVER_ARMING,
                next_attempt_at=ladder.next_attempt_at,
            )
        if ladder.attempts >= self._max_attempts:
            return RecoveryDecision(
                resource=resource,
                outcome=RECOVERY_EXHAUSTED,
                attempts=ladder.attempts,
                next_attempt_at=ladder.next_attempt_at,
            )
        if now < ladder.next_attempt_at:
            # ONE attempt per boundary. At a 2.0s interval and a 1.0s first boundary this branch
            # is what stops four cycles inside one 8s boundary from becoming four retries.
            return RecoveryDecision(
                resource=resource,
                outcome=RECOVERY_SCHEDULED if ladder.attempts > 0 else RECOVERY_NOT_ATTEMPTED,
                attempts=ladder.attempts,
                next_attempt_at=ladder.next_attempt_at,
            )
        ladder.attempts += 1
        self._attempts_total += 1
        # The boundary is consumed BEFORE the control is touched. A control that raises has
        # still been asked to act and may have mutated something, so it must not be asked again
        # inside the same boundary -- that is how a raising control becomes a hot retry loop.
        ladder.next_attempt_at = now + self._delay(ladder.attempts)
        try:
            result = _issue_retry(self._control, resource)
        except Exception as error:  # supervisor boundary: one resource, one recorded failure
            self._control_errors += 1
            self._last_control_error = type(error).__name__[:64]
            ladder.control_result = False
            return RecoveryDecision(
                resource=resource,
                outcome=recovery_blocked_token(BLOCKED_CONTROL_ERROR),
                attempted=True,
                attempts=ladder.attempts,
                blocked_cause=BLOCKED_CONTROL_ERROR,
                next_attempt_at=ladder.next_attempt_at,
                control_result=False,
            )
        ladder.control_result = result
        return RecoveryDecision(
            resource=resource,
            outcome=RECOVERY_SCHEDULED,
            attempted=True,
            attempts=ladder.attempts,
            next_attempt_at=ladder.next_attempt_at,
            control_result=result,
        )

    def _delay(self, attempts: int) -> float:
        index = min(max(0, attempts), len(self._backoff) - 1)
        return self._backoff[index]

    def status(self, now: float) -> dict[str, Any]:
        """Bounded recovery status for :meth:`BackendHealthObserver.state`."""

        with self._lock:
            return {
                "enabled": self._control is not None,
                "armed": self._armed_at is not None,
                "arming_seconds": self._arming_seconds,
                "arming_remaining_seconds": max(0.0, (self._armed_at or now) + self._arming_seconds - now)
                if self._armed_at is not None
                else self._arming_seconds,
                "backoff_seconds": list(self._backoff),
                "max_attempts": self._max_attempts,
                "attempts_total": self._attempts_total,
                "control_errors": self._control_errors,
                "last_control_error": self._last_control_error,
                "resources": {
                    name: {
                        "attempts": ladder.attempts,
                        "outcome": ladder.outcome,
                        "next_attempt_in_seconds": max(0.0, ladder.next_attempt_at - now)
                        if ladder.next_attempt_at > 0.0
                        else 0.0,
                        "last_control_result": ladder.control_result,
                    }
                    for name, ladder in sorted(self._ladders.items())
                },
            }


def overall_health_state(states: Mapping[str, str]) -> str:
    """Return the worst state currently held by any resource."""

    held = set(states.values())
    for state in BACKEND_HEALTH_STATE_SEVERITY:
        if state in held:
            return state
    return "starting"


@dataclass(frozen=True)
class ObservationCycle:
    """What one completed cycle did. Returned by :meth:`BackendHealthObserver.observe_once`."""

    observed_at: float
    duration_seconds: float
    states: Mapping[str, tuple[str, str]]
    probe_outcomes: Mapping[str, str]
    # (resource, state, reason_code, recovery_outcome). The recovery outcome is part of the
    # STABLE SIGNATURE because "a retry was issued" and "recovery is blocked because the peer is
    # upgrade-fenced" are health facts a user must be able to see; without it the retained row
    # would only ever record the outcome that happened to coincide with a state change.
    signature: tuple[tuple[str, str, str, str], ...]
    published: bool = False
    revision: int = 0
    persisted: bool = False
    event: Mapping[str, Any] | None = None
    probe_failures: Mapping[str, str] = field(default_factory=dict)
    recovery: Mapping[str, str] = field(default_factory=dict)
    retries_issued: tuple[str, ...] = ()


class BackendHealthObserver:
    """Observe the six local services continuously and publish typed health transitions.

    Everything time-related is injected. ``monotonic`` drives the interval, ``wall_clock`` stamps
    the snapshot, and ``wait`` is the only blocking call in the loop, so a test never sleeps.
    """

    def __init__(
        self,
        *,
        row_producers: Callable[[], Mapping[str, Callable[[], Mapping[str, Any]]]],
        store: BackendHealthStore,
        publish: Callable[[str, dict[str, Any]], Any],
        label_source: Callable[[str], str] | None = None,
        inventory: tuple[str, ...] = LOCAL_SERVICE_INVENTORY,
        interval_seconds: float = BACKEND_HEALTH_OBSERVE_SECONDS,
        probe_timeout_seconds: float = BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS,
        debounce_observations: int = BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS,
        monotonic: Callable[[], float] = monotonic_now,
        wall_clock: Callable[[], float] = wall_clock_now,
        wait: Callable[[float], bool] | None = None,
        identity_source: Callable[[int], str | None] = process_start_identity,
        on_diagnostic: Callable[[BackendHealthDiagnostic], None] | None = None,
        recovery_control: Any | None = None,
        recovery_backoff_seconds: tuple[float, ...] = BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS,
        recovery_max_attempts: int = BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS,
        recovery_arming_seconds: float = BACKEND_HEALTH_RECOVERY_ARMING_SECONDS,
    ) -> None:
        self._row_producers = row_producers
        self._store = store
        self._publish = publish
        self._label_source = label_source
        self.inventory = tuple(inventory)
        self.interval_seconds = max(0.0, float(interval_seconds))
        self.probe_timeout_seconds = max(0.0, float(probe_timeout_seconds))
        self.debounce_observations = max(1, int(debounce_observations))
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._identity_source = identity_source
        # The supervisor boundary's reporting channel. The SAME once-per-episode reporter the
        # store uses for persistence failures, so a cycle failing every `interval_seconds`
        # produces one operator log entry rather than one per interval, and so there is exactly
        # one dedup rule for backend-health faults instead of two that can drift.
        self._diagnostics = DiagnosticEpisodes(
            store.port,
            clock=wall_clock,
            on_diagnostic=on_diagnostic,
            epoch_source=lambda: str(store.document().get("observer_epoch") or ""),
        )
        # No control means no recovery: the observer never invents one, and never reaches into a
        # row producer to find something startable. An unwired process publishes
        # `retry_blocked_no_control` for a verified-down service rather than a silent `none`.
        self.recovery = ServiceRecoveryPlanner(
            control=recovery_control,
            backoff_seconds=recovery_backoff_seconds,
            max_attempts=recovery_max_attempts,
            arming_seconds=recovery_arming_seconds,
        )

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._wait = wait if wait is not None else self._wake.wait
        self._thread: threading.Thread | None = None
        self._started = False
        self._executor: ThreadPoolExecutor | None = None

        # Every resource starts `starting`, so the very first cycle answers with a typed state
        # instead of an absent one, and a recovery to `ready` still has to debounce.
        self._accepted: dict[str, tuple[str, str]] = {name: _INITIAL_HEALTH for name in self.inventory}
        self._candidates: dict[str, tuple[tuple[str, str], int]] = {}
        self._published_signature: tuple[tuple[str, str, str], ...] | None = None
        self._observations = 0
        self._cycle_failures = 0
        self._last_cycle_error = ""
        # Liveness, all MONOTONIC and all read together under `self._lock`. Attempted and
        # completed are separate counters on purpose: a cycle that throws after the probes
        # advances the first and not the second, which is a failing monitor rather than an
        # absent one, and the two used to be indistinguishable.
        self._cycles_attempted = 0
        self._cycles_completed = 0
        self._last_attempt_monotonic: float | None = None
        self._last_success_monotonic: float | None = None
        self._consecutive_cycle_failures = 0
        self._published_revision = 0
        self._published_events = 0

    # -- lifecycle -----------------------------------------------------------

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> bool:
        """Start the observer exactly once. Returns False if it was already started."""

        with self._lock:
            if self._started:
                return False
            self._started = True
            self._executor = ThreadPoolExecutor(
                max_workers=max(1, len(self.inventory)),
                thread_name_prefix=f"{BACKEND_HEALTH_OBSERVER_THREAD_PREFIX}-probe",
            )
            self._thread = threading.Thread(
                target=self._run,
                name=f"{BACKEND_HEALTH_OBSERVER_THREAD_PREFIX}-observer-{self._store.port}",
                daemon=True,
            )
            self._thread.start()
            return True

    def wake(self) -> None:
        """Cut the current interval short so the next cycle runs now."""

        self._wake.set()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the loop, join it, and retire the probe pool. Safe to call more than once.

        The pool is joined rather than abandoned: every production row producer reaches its
        service through a timeout-bounded RPC, so there is no unbounded probe to wait on, and
        an abandoned pool is exactly the thread leak the teardown proof exists to catch.
        """

        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
            executor = self._executor
            self._thread = None
            self._executor = None
        if thread is not None:
            thread.join(timeout=timeout)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.observe_once()
            except Exception as error:  # supervisor boundary: one bad cycle must not end the loop
                self._record_cycle_failure(error)
            self._await_next_cycle()

    def _record_cycle_failure(self, error: BaseException) -> None:
        """Record ONE failed cycle against this observer, WITH its cause, and report it once.

        THE DEFECT THIS EXISTS FOR. This boundary used to keep three counters and the exception's
        class name, and nothing else: the traceback was dropped on the floor and the counters were
        readable only through `liveness()`, i.e. only through an authenticated HTTP request that a
        person had to think to make. An observer whose every cycle threw therefore looked, from
        outside, exactly like an observer with nothing to say -- and the only way anyone found out
        which of the two it was, was to take a process dump of the running server. That is a
        DISCARDED failure, not a supervised one: a supervisor boundary must record the failure
        against its unit with the backtrace and keep going, and this one only did the last part.

        So the cause is preserved here, and it leaves the process through the same
        once-per-episode diagnostic channel the store's persistence failures already use. Once per
        EPISODE and not once per cycle is the whole point: the cadence is a couple of seconds, so
        a per-occurrence report would bury the operator log under the very fault it is announcing.
        A completed cycle closes the episode (`_record_cycle_completed`), so a fault that returns
        later is reported again rather than silently folded into the first one.
        """

        detail_code = type(error).__name__[:48].lower()
        with self._lock:
            self._cycle_failures += 1
            self._consecutive_cycle_failures += 1
            self._last_cycle_error = type(error).__name__[:64]
        # Outside the lock: the reporter is caller-supplied and reaches the server log ring.
        self._diagnostics.emit(
            DIAGNOSTIC_CYCLE_FAILED,
            detail_code=detail_code,
            detail_text=_bounded_traceback(error),
        )

    # -- liveness ------------------------------------------------------------

    def liveness(self) -> dict[str, Any]:
        """Is this monitor still looking? ONE immutable snapshot, taken under ONE lock.

        This lives here, not in the store, because the observer owns the cadence the deadline is
        derived from and owns the thread whose survival is the question. The store's job is
        history and persistence; giving it cycle timing as well made it a second owner of a fact
        it could not see, and it aged that fact on the WALL clock -- so a clock step backwards
        held "alive" forever and a step forwards produced a false red. Everything here is
        MONOTONIC, which is what the loop already schedules on.

        Three separate facts, because collapsing them is how a live observer gets reported as
        dead: a cycle ATTEMPTED, a cycle COMPLETED, and the typed reason the last one did not
        finish. An exception thrown after the probes but before completion advances `attempted`
        and not `completed`, and that is a failing monitor with a name -- not a stopped one.
        """

        now = self._monotonic()
        with self._lock:
            attempted = self._cycles_attempted
            completed = self._cycles_completed
            last_attempt = self._last_attempt_monotonic
            last_success = self._last_success_monotonic
            consecutive_failures = self._consecutive_cycle_failures
            last_error = self._last_cycle_error
        stale_after = max(
            BACKEND_HEALTH_LIVENESS_FLOOR_SECONDS,
            float(self.interval_seconds) * BACKEND_HEALTH_LIVENESS_MISSED_CYCLES,
        )
        # Absent, not zero: "0 seconds since the last cycle" would read as "probed this instant".
        attempt_age = None if last_attempt is None else max(0.0, now - last_attempt)
        success_age = None if last_success is None else max(0.0, now - last_success)
        # ONE ordered decision, because the three reasons are not independent and deriving them
        # separately is what let a dead monitor report a benign one.
        #
        # `consecutive_failures` is the count of failures since the last COMPLETED cycle, and
        # nothing but a completion ever clears it. So it says a failure happened; it cannot say
        # one is still happening. Attempt freshness is the fact that separates "throwing on every
        # cycle" -- a bug to go and fix -- from "not being scheduled at all", which is a thread to
        # restart, and it was already measured right here.
        #
        # The order below is the invariant. `no_observer_cycle_recorded` is honest ONLY before any
        # attempt, or while a first attempt that has not failed is still inside its deadline; once
        # the last ATTEMPT is older than the deadline the loop is not running, whether or not it
        # ever completed a cycle. Deciding the reason from the completion count first is what made
        # a monitor that died on its FIRST cycle decay, ten minutes later, into "attached, has not
        # looked yet" -- the startup state -- and disappear from every surface.
        attempting = attempt_age is not None and attempt_age <= stale_after
        completing = completed > 0 and success_age is not None and success_age <= stale_after
        if attempt_age is None:
            # Nothing has ever been attempted: attached, not yet probing.
            reason_code, alive = BACKEND_HEALTH_NO_CYCLE_OBSERVED, False
        elif completing:
            # Completing cycles inside the deadline. A failure inside that window is tolerated --
            # the deadline exists precisely to absorb a missed cycle or two.
            reason_code, alive = "", True
        elif not attempting:
            reason_code, alive = BACKEND_HEALTH_CYCLES_STALLED, False
        elif consecutive_failures > 0:
            reason_code, alive = BACKEND_HEALTH_CYCLE_FAILING, False
        else:
            # Attempting, nothing has failed, and the first cycle has not landed yet.
            reason_code, alive = BACKEND_HEALTH_NO_CYCLE_OBSERVED, False
        return {
            "alive": alive,
            "cycles_attempted": int(attempted),
            "cycles_completed": int(completed),
            "attempt_age_seconds": attempt_age,
            "cycle_age_seconds": success_age,
            "stale_after_seconds": stale_after,
            "consecutive_failures": int(consecutive_failures),
            "failure_type": last_error if consecutive_failures > 0 else "",
            "reason_code": reason_code,
        }

    def _await_next_cycle(self) -> None:
        deadline = self._monotonic() + self.interval_seconds
        while not self._stop.is_set():
            remaining = deadline - self._monotonic()
            if remaining <= 0.0:
                return
            if self._wait(remaining):
                self._wake.clear()
                return

    # -- one cycle -----------------------------------------------------------

    def observe_once(self) -> ObservationCycle:
        """Run one complete observation cycle. Never starts a service, never sleeps."""

        started_at = self._monotonic()
        # The attempt is recorded HERE, at entry, before anything can throw. `snapshot.observed_at`
        # is captured before recovery and was previously used as the heartbeat, so a cycle whose
        # bounded retries took a few seconds looked seconds old the instant it finished.
        with self._lock:
            self._cycles_attempted += 1
            self._last_attempt_monotonic = started_at
        rows, outcomes, failures = self._probe_all()
        snapshot = LocalServicesCollector(
            lambda: {name: _constant_row(row) for name, row in rows.items()},
            inventory=self.inventory,
            clock=self._wall_clock,
        ).collect()

        observed: dict[str, tuple[str, str]] = {}
        identities: dict[str, tuple[int, str]] = {}
        collected: dict[str, Mapping[str, Any]] = {}
        for row in snapshot.rows:
            observed[row.service] = observed_health(row.fields, outcomes.get(row.service, PROBE_FAILED))
            pid = row.pid if row.pid > 0 else 0
            identities[row.service] = (pid, str(self._identity_source(pid) or "") if pid > 0 else "")
            collected[row.service] = row.fields

        with self._lock:
            self._observations += 1
            accepted = self._debounced(observed)

        # Recovery decides AFTER the debounce and outside the observer lock: it acts only on an
        # accepted state, and issuing `retry` while holding the lock would put a peer process's
        # start latency inside every other reader of this observer.
        decisions = self.recovery.decide_all(accepted, now=self._monotonic(), fields=collected)
        recovery = {name: decision.outcome for name, decision in decisions.items()}
        retries = tuple(sorted(name for name, decision in decisions.items() if decision.attempted))

        with self._lock:
            signature = tuple(
                sorted(
                    (name, state, reason, recovery.get(name, RECOVERY_NONE))
                    for name, (state, reason) in accepted.items()
                )
            )
            changed = signature != self._published_signature
            revision = self._published_revision

        cycle = ObservationCycle(
            observed_at=snapshot.observed_at,
            duration_seconds=max(0.0, self._monotonic() - started_at),
            states=dict(accepted),
            probe_outcomes=dict(outcomes),
            signature=signature,
            revision=revision,
            probe_failures=dict(failures),
            recovery=recovery,
            retries_issued=retries,
        )
        # LIVENESS: the cycle COMPLETED. An UNCHANGED cycle is finished right here -- there is
        # nothing left for it to do, and a quiet healthy system is almost every cycle by design,
        # so making it wait for a publication it will never perform would report a working
        # monitor as one that has never finished anything.
        if not changed:
            self._record_cycle_completed()
            return cycle
        # A CHANGED cycle is not finished until its change is out the door. Recording completion
        # first was the defect: `store.record` on a full or unwritable disk, and the event
        # publish, both sit past this point and both can throw, so eight straight publication
        # failures with zero events published still reported `alive=true`, eight completed
        # cycles, a two-second cycle age and a single consecutive failure. The supervisor in
        # `_run` counts the raise; nothing here may claim the cycle finished before then.
        published = self._publish_change(cycle, accepted, identities, signature, recovery)
        self._record_cycle_completed()
        return published

    def _record_cycle_completed(self) -> None:
        """The ONE place a completed cycle is recorded, on the monotonic clock read NOW.

        Timed here rather than on the wall-clock `observed_at` captured before the probes, so a
        cycle whose bounded retries took a few seconds does not look seconds old the instant it
        finished. Publication is change-driven and so can never answer "is this monitor still
        running": a quiet healthy system leaves the document frozen while this counter moves.
        """

        with self._lock:
            self._cycles_completed += 1
            self._last_success_monotonic = self._monotonic()
            self._consecutive_cycle_failures = 0
            self._last_cycle_error = ""
        # A completed cycle ENDS the failure episode, so the same fault recurring later is
        # reported again instead of being counted silently into the episode that already closed.
        self._diagnostics.clear(DIAGNOSTIC_CYCLE_FAILED)

    def _probe_all(self) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
        """Run every row producer concurrently under one bounded deadline.

        A producer that does not answer inside ``probe_timeout_seconds`` yields an empty row and
        a ``timeout`` outcome; its future is abandoned to the pool rather than waited on, which is
        what keeps the cycle bounded when a service stops answering.
        """

        producers = dict(self._row_producers())
        rows: dict[str, dict[str, Any]] = {}
        outcomes: dict[str, str] = {}
        failures: dict[str, str] = {}
        executor = self._executor
        deadline = self._monotonic() + self.probe_timeout_seconds
        futures: dict[str, Future[Mapping[str, Any]]] = {}
        with local_service_probe_scope():
            if executor is None:
                # No pool (observe_once called directly, as tests and one-shot diagnostics do).
                # Producers still run inside the probe scope; the per-service bound belongs to
                # the pool, so it is reported as absent rather than silently claimed.
                for name, producer in producers.items():
                    rows[name], outcomes[name], failure = _inline_probe(name, producer)
                    if failure:
                        failures[name] = failure
                return rows, outcomes, failures
            for name, producer in producers.items():
                futures[name] = executor.submit(_probe_worker, producer)
        for name, future in futures.items():
            remaining = max(0.0, deadline - self._monotonic())
            try:
                error = future.exception(timeout=remaining)
            except TimeoutError:
                rows[name] = {"service": name, "pid": 0}
                outcomes[name] = PROBE_TIMEOUT
                continue
            if error is not None:
                # Supervisor boundary: one service's probe is one independent unit, and its
                # failure is recorded against that unit as `unknown`/`probe_failed`.
                rows[name] = {"service": name, "pid": 0}
                outcomes[name] = PROBE_FAILED
                failures[name] = type(error).__name__[:64]
                continue
            produced = future.result()
            rows[name] = dict(produced) if isinstance(produced, Mapping) else {"service": name, "pid": 0}
            outcomes[name] = PROBE_OK if isinstance(produced, Mapping) else PROBE_FAILED
        return rows, outcomes, failures

    def _debounced(self, observed: Mapping[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
        """Fold this cycle's observations into the accepted state under the debounce rule."""

        for name, candidate in observed.items():
            current = self._accepted.get(name, _INITIAL_HEALTH)
            if candidate == current:
                self._candidates.pop(name, None)
                continue
            if candidate[0] in BACKEND_HEALTH_IMMEDIATE_STATES:
                self._accepted[name] = candidate
                self._candidates.pop(name, None)
                continue
            pending, count = self._candidates.get(name, (None, 0))
            count = count + 1 if pending == candidate else 1
            if count >= self.debounce_observations:
                self._accepted[name] = candidate
                self._candidates.pop(name, None)
                continue
            self._candidates[name] = (candidate, count)
        return dict(self._accepted)

    def _publish_change(
        self,
        cycle: ObservationCycle,
        accepted: Mapping[str, tuple[str, str]],
        identities: Mapping[str, tuple[int, str]],
        signature: tuple[tuple[str, str, str, str], ...],
        recovery: Mapping[str, str],
    ) -> ObservationCycle:
        observations = tuple(
            ResourceObservation(
                resource=name,
                state=accepted[name][0],
                reason_code=accepted[name][1],
                recovery_outcome=recovery.get(name, RECOVERY_NONE),
                pid=identities.get(name, (0, ""))[0],
                process_start_identity=identities.get(name, (0, ""))[1],
                # See the module docstring: an invented counter is worse than an absent one.
                counters_available=False,
            )
            for name in sorted(accepted)
        )
        result = self._store.record(HealthSnapshot(observed_at=cycle.observed_at, resources=observations))
        # A store that cannot write keeps its previous good document and does not advance the
        # revision. The transition still happened, and health that disappears when the disk is
        # full is the failure this monitor exists to remove, so the event is still published --
        # the repeated revision plus `store.persistence_status()` is where that is visible.
        payload = self.event_payload(result.document, result.revision, accepted)
        event = self._publish(BACKEND_HEALTH_EVENT, payload)
        with self._lock:
            self._published_signature = signature
            self._published_revision = int(result.revision)
            self._published_events += 1
        return ObservationCycle(
            observed_at=cycle.observed_at,
            duration_seconds=cycle.duration_seconds,
            states=cycle.states,
            probe_outcomes=cycle.probe_outcomes,
            signature=signature,
            published=True,
            revision=int(result.revision),
            persisted=bool(result.published),
            event=event if isinstance(event, Mapping) else None,
            probe_failures=cycle.probe_failures,
            recovery=cycle.recovery,
            retries_issued=cycle.retries_issued,
        )

    # -- projections ---------------------------------------------------------

    def label(self, resource: str) -> str:
        if self._label_source is None:
            return resource
        return str(self._label_source(resource) or resource)

    def event_payload(
        self,
        document: Mapping[str, Any],
        revision: int,
        accepted: Mapping[str, tuple[str, str]],
    ) -> dict[str, Any]:
        """Build the ``backend_health_changed`` payload: exactly four keys, no history.

        ``degraded_resources`` is the shape ``local_services_alert`` (``app.py:589-612``) already
        produces, minus its free-text ``reason``. Free text can carry a path or a socket name;
        the bounded ``reason_code`` is the machine-readable half and is what the indicator reads.
        """

        degraded = [
            {
                "id": name,
                "label": self.label(name),
                "state": accepted[name][0],
                "reason_code": accepted[name][1],
            }
            for name in sorted(accepted)
            if accepted[name][0] in BACKEND_HEALTH_DEGRADED_STATES
        ]
        return {
            "epoch": str(document.get("observer_epoch") or ""),
            "revision": int(revision),
            "overall_state": overall_health_state({name: state for name, (state, _) in accepted.items()}),
            "degraded_resources": degraded[:BACKEND_HEALTH_EVENT_MAX_RESOURCES],
        }

    def state(self) -> dict[str, Any]:
        """The observer's own bounded status, typed before the first completed observation."""

        with self._lock:
            accepted = dict(self._accepted)
            observations = self._observations
            revision = self._published_revision
            cycle_failures = self._cycle_failures
            last_error = self._last_cycle_error
            published_events = self._published_events
        document = self._store.document()
        return {
            "running": self.running,
            "observations": observations,
            "published_events": published_events,
            "cycle_failures": cycle_failures,
            "last_cycle_error": last_error,
            "interval_seconds": self.interval_seconds,
            "probe_timeout_seconds": self.probe_timeout_seconds,
            "epoch": str(document.get("observer_epoch") or ""),
            "revision": revision,
            "overall_state": overall_health_state({name: state for name, (state, _) in accepted.items()}),
            "resources": {
                name: {"state": state, "reason_code": reason} for name, (state, reason) in sorted(accepted.items())
            },
            "persistence": self._store.persistence_status(),
            # Attempt counts and the next boundary are NOT in the retained store: its transition
            # row is fixed at seven fields and adding an eighth would be a schema change no
            # consumer asked for. They belong here, where the observer's own status already is.
            "recovery": self.recovery.status(self._monotonic()),
        }


BACKEND_HEALTH_CYCLE_TRACEBACK_MAX_CHARS = 2000


def _bounded_traceback(error: BaseException) -> str:
    """One bounded, single-entry formatted traceback for a caught cycle failure.

    Bounded because it is carried into a fixed-capacity operator log ring, and TAIL-biased
    because the frames that name the failing call are at the end; truncating from the front
    would keep the scheduler frames and drop the only ones worth reading.
    """

    text = "".join(traceback.format_exception(type(error), error, error.__traceback__)).strip()
    if len(text) <= BACKEND_HEALTH_CYCLE_TRACEBACK_MAX_CHARS:
        return text
    return "...(truncated)...\n" + text[-BACKEND_HEALTH_CYCLE_TRACEBACK_MAX_CHARS:]


def _constant_row(row: Mapping[str, Any]) -> Callable[[], Mapping[str, Any]]:
    """Hand an already-probed row back to the collector without re-probing it."""

    return lambda: row


def _probe_worker(producer: Callable[[], Mapping[str, Any]]) -> Mapping[str, Any]:
    """Run one row producer in a pool thread, inside its own probe scope.

    The scope is a context variable and context variables are per-thread, so it has to be
    re-entered here; the caller's scope does not reach this thread.
    """

    with local_service_probe_scope():
        return producer()


def _inline_probe(
    name: str,
    producer: Callable[[], Mapping[str, Any]],
) -> tuple[dict[str, Any], str, str]:
    try:
        produced = producer()
    except Exception as error:  # supervisor boundary: one probe, one recorded failure
        return {"service": name, "pid": 0}, PROBE_FAILED, type(error).__name__[:64]
    if not isinstance(produced, Mapping):
        return {"service": name, "pid": 0}, PROBE_FAILED, "not_a_row"
    return dict(produced), PROBE_OK, ""
