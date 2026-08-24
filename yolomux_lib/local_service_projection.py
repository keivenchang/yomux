# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One owner for the six-service local-service projection.

M2/M3 of DOIT.p0.daemon-monitor. Two things live here and nothing else. This is
deliberately NOT inside :mod:`yolomux_lib.backend_health`: that package owns the
retained history store (M5), and the collector is a separate owner that observes
nothing and retains nothing.

``registry_process_identity``
    The no-RPC identity read. It opens the persisted ``*.service.json`` record a
    registry already writes and routes it through the one central process fence
    (``registry.process_record_diagnostic``). It never pings, never calls
    ``status``, and above all never calls ``ensure_started`` -- a diagnostics read
    that starts a demand-scoped service is the exact defect M2 exists to avoid.

``LocalServicesCollector``
    The single producer of the ``/api/system-status`` ``local_services`` payload.
    Before M3 that projection was an inline function that built five rows by
    calling a client and the sixth (statsd) as a dict literal in its own body, so
    statsd's row shape lived in two places. Here every service is one named row
    producer, the derived fields (uptime, totals) are computed once, and the
    result is a frozen snapshot that callers cannot mutate.

``RetainedHealth``
    M8. The read-only join of the two things that already hold backend-health
    numbers -- the observer's retained document
    (:mod:`yolomux_lib.backend_health.store`) and the web process's own RPC ledger
    (``local_services.rpc.local_service_traffic_ledger``) -- rendered into the
    snapshot-level and per-row ``health`` blocks. It reads no file, opens no socket,
    and starts nothing: the caller hands it a document that is already in memory.

The snapshot is the schema. ``LocalServicesSnapshot.payload()`` renders the dict the
HTTP projection publishes, including ``schema_version``.

WHY ``schema_version`` IS 5
---------------------------
``static_src/js/yolomux/85_debug_panel.js`` guards the whole normalized Local-services
render on ``schema_version === <this number>`` (exact, not ``>=``), so any shape change
without a matching bump would leave the panel rendering the new payload through the old
table and silently ignoring or fabricating fields -- a mismatch nothing would report.
Each bump is one shape change, and the front-end guard moves with it in the same change:

* M3 preserved ``1``.
* M8 moved it to ``2``: every service row grew a ``health`` block and the payload grew a
  snapshot-level ``health`` block.
* W13 moved it to ``3``: the rolled-up ``alert`` summary was removed from the payload. It
  was a dead contract -- nothing in the UI read it; every consumer reads each row's own
  ``alerting``/``reason_code``. Removing a published key is a shape change, so the guard
  moves to 3 and a schema-2 payload is now refused rather than rendered through the new
  table.
* Lifecycle accounting moved it to ``4``: each health metric block now separates all verified
  process starts from unexpected restarts and demand-driven starts. Legacy replacements remain
  explicitly partial rather than being relabelled after the fact.
* Watchd bridge readiness moved it to ``5``: the runtime row now publishes `serving_state`, so a
  verified process waiting for its first bridge revision is distinct from an unhealthy daemon.

WHY THE COUNTERS SAY ``web_process``
------------------------------------
The retained store re-baselines request/error/latency counters per verified peer process
epoch, and the observer feeds it ``counters_available=False`` on purpose (see
``backend_health/observer.py``): the only cumulative counters this process owns live in
the web-process RPC ledger and would be double counted at every peer restart if they were
handed to a per-epoch delta reducer. M8 therefore does NOT feed the store. It publishes
the ledger totals under their true denominator -- ``counter_scope: "web_process"``, exact
and continuous across a peer restart, which is the property the ledger was built for --
and publishes the store's own per-epoch aggregate separately as ``retained_counters``,
downgraded to ``partial`` with reason ``counters_not_observed`` whenever no counter sample
was ever read. The store's raw ``aggregate.coverage`` reads ``full`` in exactly that state
(``_accumulate`` only marks partial at an epoch change), so publishing it verbatim would
render an aggregate of structural zeros as complete.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from types import MappingProxyType
from typing import Any
from typing import cast
from typing import TypedDict

from .filesystem.io_ops import read_json_file
from .local_services import registry as local_services_registry
from .local_services.runtime import local_service_failure_text


# The frozen inventory. This is the one place the six ids and their order are
# declared; `tests/test_gate_panels.py:12,227` pins the same six for the panel.
LOCAL_SERVICE_INVENTORY: tuple[str, ...] = ("indexd", "statsd", "jobd", "statusd", "watchd", "approvald")

# Bumping this is a browser-visible change: `85_debug_panel.js` guards the whole
# Local-services render on this exact number. M3 preserved 1 deliberately; M8 moved it to 2
# because every row grew a `health` block; W13 moved it to 3 when the dead `alert` summary
# was removed; lifecycle accounting moved it to 4; bridge readiness moved it to 5. See the module
# docstring for each shape.
LOCAL_SERVICES_SCHEMA_VERSION = 5

# How many of a resource's retained transition rows the HTTP projection publishes. The
# store keeps 128 per resource; six services times 128 rows would put ~5000 rows into
# every `/api/system-status` body to render a table that shows the recent past. The
# newest rows are the ones a reader acts on, and `transitions_truncated` says when older
# ones exist rather than pretending the list is the whole history.
SYSTEM_STATUS_MAX_TRANSITIONS = 16

# Why the row has no retained health at all. Bounded and machine-readable: "the observer
# is not attached to this process" and "the observer has never recorded this resource"
# are different facts with different fixes, and collapsing them into one absent block is
# the reason-code collapse the health contract forbids.
HEALTH_OBSERVER_UNATTACHED = "observer_unattached"
HEALTH_RESOURCE_UNOBSERVED = "resource_unobserved"

# Coverage vocabulary for the two aggregates a row publishes.
COVERAGE_FULL = "full"
COVERAGE_PARTIAL = "partial"
COVERAGE_UNAVAILABLE = "unavailable"

# The retained per-epoch aggregate has no counter sample at all: the observer publishes
# `counters_available=False`, so every retained count is a structural zero rather than a
# measured one. The store's own `coverage` cannot say this -- it only marks partial when
# an epoch change loses a final sample -- so the projection says it.
COVERAGE_COUNTERS_NOT_OBSERVED = "counters_not_observed"
# Existing schema-1 retained documents counted every process replacement but did not retain the
# expected-absence fact needed to distinguish demand starts from failures. New observations are
# classified, while this reason keeps the older portion from reading as an exact zero.
COVERAGE_LIFECYCLE_LEGACY_UNCLASSIFIED = "legacy_lifecycle_unclassified"
# The retained history starts before this web process did, so the ledger counters cover
# less time than the restart count and transitions beside them.
COVERAGE_WEB_PROCESS_SCOPE = "web_process_scope"

# The denominator of the published request/error/latency numbers.
COUNTER_SCOPE_WEB_PROCESS = "web_process"

# Traffic-ledger keys this projection reads. Named once here rather than inlined six
# times; the ledger owns their meaning (`local_services/rpc.py`).
TRAFFIC_WORK_CLASS = "work"
TRAFFIC_RESPONSE_LATENCY = "client_latency_ms"

# Reason codes for a registry-derived identity read. Bounded, machine-readable, and
# never a free-text collapse of four different outcomes into "unavailable".
IDENTITY_OK = ""
IDENTITY_NO_RECORD = "no_service_record"
IDENTITY_RECORD_UNREADABLE = "service_record_unreadable"
IDENTITY_NOT_CURRENT = "process_identity_unverified"


class LocalServiceRuntimeRow(TypedDict, total=False):
    """The common wire shape produced for one local service.

    Service-specific diagnostics remain optional fields.  Keeping this a
    ``TypedDict`` preserves the existing runtime dictionaries while giving all
    producers one named boundary that static tooling can check.
    """

    service: str
    pid: int
    started_at: float
    version: int
    healthy: bool
    serving_state: str
    last_failure: str
    resources: dict[str, Any]
    demand_started: bool
    absence_expected_reason: str
    socket: str
    clients: int
    queues: dict[str, Any]
    cache: dict[str, Any]


def local_service_runtime_row(
    service: str,
    *,
    pid: int,
    started_at: float,
    version: int | None,
    healthy: bool,
    last_failure: str,
    resources: Mapping[str, Any],
    fields_before_failure: Mapping[str, Any] | None = None,
    fields_after_failure: Mapping[str, Any] | None = None,
    fields_after_resources: Mapping[str, Any] | None = None,
) -> LocalServiceRuntimeRow:
    """Project common fields while preserving each established wire-key order."""

    row = cast(LocalServiceRuntimeRow, {
        "service": service,
        "pid": int(pid),
        "started_at": float(started_at),
    })
    if version is not None:
        row["version"] = int(version)
    row["healthy"] = bool(healthy)
    if fields_before_failure:
        row.update(fields_before_failure)
    row["last_failure"] = str(last_failure)
    if fields_after_failure:
        row.update(fields_after_failure)
    row["resources"] = dict(resources)
    if fields_after_resources:
        row.update(fields_after_resources)
    return row


def registry_runtime_row(
    service: str,
    registry: Any,
    status: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    fields_before_failure: Mapping[str, Any] | None = None,
    fields_after_failure: Mapping[str, Any] | None = None,
    fields_after_resources: Mapping[str, Any] | None = None,
    resource_pids: Sequence[int] | None = None,
    include_version: bool = True,
) -> LocalServiceRuntimeRow:
    """Adapt an already-read registry status into the shared row projection.

    The caller owns the status read.  This helper never probes, starts, leases,
    retries, or otherwise changes a daemon's lifecycle.
    """

    pid = int(payload.get("pid") or 0)
    resources = (
        registry.resources_for_pids(pid, list(resource_pids))
        if resource_pids is not None
        else registry.resources(pid)
    )
    return local_service_runtime_row(
        service,
        pid=pid,
        started_at=float(payload.get("started_at") or 0.0),
        version=int(payload.get("version") or 0) if include_version else None,
        healthy=bool(status.get("healthy")),
        last_failure=local_service_failure_text(dict(status), dict(payload)),
        resources=resources,
        fields_before_failure=fields_before_failure,
        fields_after_failure=fields_after_failure,
        fields_after_resources=fields_after_resources,
    )


@dataclass(frozen=True)
class RegistryProcessIdentity:
    """A service's process identity as proven by its persisted record alone.

    ``verified`` is true only when the record passed the same host/boot/process-start
    fence every other identity consumer in this tree uses. An unverified record
    yields pid 0, so a caller cannot accidentally sample or signal a PID that this
    host cannot prove belongs to the service.
    """

    pid: int = 0
    started_at: float = 0.0
    protocol_version: int = 0
    process_start_identity: str = ""
    verified: bool = False
    reason_code: str = IDENTITY_NO_RECORD

    # No process-epoch token is minted here on purpose. `yolomux_lib.backend_health`
    # owns that encoding for the retained history (M5); this dataclass carries the two
    # raw components it needs (`pid`, `process_start_identity`) and nothing derived, so
    # there is one owner of the epoch format rather than two that can disagree.

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "started_at": self.started_at,
            "process_start_identity": self.process_start_identity,
            "verified": self.verified,
            "reason_code": self.reason_code,
        }


def registry_process_identity(registry: Any) -> RegistryProcessIdentity:
    """Read one service's identity from its persisted record, with no RPC at all.

    This is the acceptable answer to "how is a demand-scoped service observed?".
    ``LocalServiceRegistry.status()`` would issue a ``status`` request, and a client
    ``runtime_status()`` would too; both are RPC on a diagnostics path. The durable
    record is written by the registry when it publishes a validated identity, so it
    already carries pid, process-start identity, protocol version, and started_at.
    Reading it costs one file read and one ``/proc`` stat, and cannot start anything.
    """
    record_path = registry.record_path
    record = read_json_file(record_path, None)
    if record is None:
        return RegistryProcessIdentity(reason_code=IDENTITY_NO_RECORD)
    if not isinstance(record, dict):
        return RegistryProcessIdentity(reason_code=IDENTITY_RECORD_UNREADABLE)
    diagnostic = local_services_registry.process_record_diagnostic(
        record,
        host_identity=registry.host_identity,
    )
    if not diagnostic.current:
        return RegistryProcessIdentity(
            reason_code=IDENTITY_NOT_CURRENT,
            process_start_identity=str(record.get("process_start_identity") or ""),
        )
    return RegistryProcessIdentity(
        pid=int(record.get("pid") or 0),
        started_at=_finite_float(record.get("started_at")),
        protocol_version=int(record.get("protocol_version") or 0),
        process_start_identity=str(record.get("process_start_identity") or ""),
        verified=True,
        reason_code=IDENTITY_OK,
    )


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) else 0.0


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _whole_number(value: object) -> int:
    number = _finite_number(value)
    return int(number) if number is not None else 0


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def measurement(value: object, *, state: str, reason_code: str, reason: str) -> dict[str, Any]:
    """The ONE metric envelope every System number is published in.

    A finite number is ``measured``; anything else is a null with a typed reason. This is
    the shape `TmuxWebtermApp.system_status_metric` has always emitted -- it now delegates
    here instead of building the dict itself, so the three process metrics and the M8
    health metrics cannot drift into two envelope shapes.

    The caller chooses the absent triple because only the caller knows why the number is
    missing. An average with no completed request and an average from an unattached
    observer are different facts, and `0.0` is the wrong answer to both.
    """

    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return {"state": "measured", "value": value, "reason_code": "", "reason": ""}
    return {"state": state, "value": None, "reason_code": reason_code, "reason": reason}


@dataclass(frozen=True)
class RetainedHealth:
    """The retained backend-health inputs, prepared ONCE per projection.

    Both inputs are already in memory in this process:

    ``document``
        What ``BackendHealthStore.status()`` returned -- the observer's own in-memory
        document plus the live persistence state. M8's recorded decision is that the HTTP
        request thread never opens ``STATE_DIR/backend-health/<port>.json``; the observing
        process holds that document and pushes the store in through
        ``TmuxWebtermApp.attach_backend_health_store``. The cost is one bounded deep copy
        per request instead of an open/lock/read/parse contended with the observer's 2s
        write. With no observer attached the document is empty and every row says
        ``observer_unattached`` rather than rendering zeros.

    ``traffic``
        ``local_services.rpc.local_service_traffic_snapshot()`` -- the web process's own
        per-service RPC ledger, probe traffic already separated into its own class.

    Constructed empty by default so a caller with neither input still renders a typed,
    honest block instead of raising or omitting the key.
    """

    document: Mapping[str, Any] = field(default_factory=dict)
    traffic: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    now: float = 0.0
    web_process_started_at: float = 0.0
    # The observer's own liveness snapshot, passed in BESIDE the history document rather than
    # embedded in it. The document answers "when did a service last change state"; only the
    # observer can answer "is this monitor still looking", and it answers on the monotonic clock
    # it schedules on. Empty when no observer is attached.
    liveness: Mapping[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        """True when a retained document exists at all -- attached, even if still empty."""
        return bool(self.document)

    def payload(self) -> dict[str, Any]:
        """The snapshot-level provenance block.

        Revision, age, epoch, and persistence describe the WHOLE document, so they are
        published once here rather than copied into all six rows. Six copies of one
        revision number is the divergent-copy defect, not extra convenience.
        """

        document = _mapping(self.document)
        written_at = _finite_float(document.get("written_at"))
        persistence = _mapping(document.get("persistence"))
        # Two DIFFERENT facts, published as two different fields.
        #   `age_seconds`                 -- how long since a service last CHANGED state. On a
        #                                    healthy quiet system this grows without bound, and
        #                                    that is correct: nothing has changed.
        #   `observer_cycle_age_seconds`  -- how long since the observer last LOOKED.
        # Collapsing them is what made a quiet six-service system report its own monitor as dead
        # after 30 seconds. The `alive` decision belongs to the OBSERVER, which owns the probe
        # thread and the monotonic cadence its deadline is derived from; this projection only
        # forwards what the observer published beside the document, and decides nothing.
        liveness = _mapping(self.liveness)
        cycle_age = liveness.get("cycle_age_seconds")
        cycle_age = float(cycle_age) if isinstance(cycle_age, (int, float)) and not isinstance(cycle_age, bool) else None
        return {
            "available": self.available,
            "reason_code": "" if self.available else HEALTH_OBSERVER_UNATTACHED,
            "schema_version": _whole_number(document.get("schema_version")),
            "port": _whole_number(document.get("port")),
            "observer_epoch": str(document.get("observer_epoch") or ""),
            "observer_epoch_started_at": _finite_float(document.get("observer_epoch_started_at")),
            "revision": _whole_number(document.get("revision")),
            "written_at": written_at,
            # Age, not "fresh": a reader decides what is too old. `None` when nothing was
            # ever written, because zero seconds old would be a lie about an absent write.
            "age_seconds": max(0.0, self.now - written_at) if self.available and written_at > 0 else None,
            "history_coverage": str(document.get("history_coverage") or ""),
            "history_reset_reason": str(document.get("history_reset_reason") or ""),
            "persistence_state": str(persistence.get("state") or ""),
            "persistence_reason_code": str(persistence.get("reason_code") or ""),
            "resources": len(_mapping(document.get("resources"))),
            # Liveness: is the monitor still looking? Absent (`None`) when nothing has probed
            # yet -- never `0.0`, which would read as "probed this instant".
            #
            # And absent when there is no observer AT ALL, which is a different thing again.
            # `bool(...)` and `_whole_number(...)` over an empty mapping answered `False` and `0`:
            # "we looked and it is dead", "we counted and it has completed no cycles". Nobody
            # looked and nobody counted. A bare `0` here cannot be told apart from an attached
            # observer that has genuinely completed no cycle yet -- which is a real, separate
            # state with its own reason code -- so the honest answer is the same absence
            # `observer_cycle_age_seconds` already gives, with the reason code below naming why.
            "observer_alive": bool(liveness.get("alive")) if liveness else None,
            "observer_cycle_age_seconds": cycle_age,
            "observer_cycles": _whole_number(liveness.get("cycles_completed")) if liveness else None,
            # ONE typed reason, and it is consumed: the panel renders it as the sentence under an
            # em dash. `stale_after_seconds`, `last_cycle_at` and the attempt age stay inside the
            # observer -- nothing rendered them, and an unused published field is API surface that
            # can only drift.
            "observer_liveness_reason_code": str(liveness.get("reason_code") or "") if liveness else HEALTH_OBSERVER_UNATTACHED,
        }

    def service(self, service: str) -> dict[str, Any]:
        """The per-row health block for one service id."""

        document = _mapping(self.document)
        record = _mapping(document.get("resources")).get(str(service))
        observed = isinstance(record, Mapping)
        record = _mapping(record)
        current = _mapping(record.get("current"))
        aggregate = _mapping(record.get("aggregate"))
        transitions = [dict(row) for row in (record.get("transitions") or []) if isinstance(row, Mapping)]
        # The LIFETIME count comes from the store, which is the only party that sees a transition
        # before it is evicted. Deriving it from `len(transitions)` made the "total" equal to the
        # retained window: after the 129th state change it reported 128 forever, and the panel's
        # "N state changes recorded -- all of them are shown" became false at exactly the point
        # `transitions_truncated` exists to flag. Never below the retained length, so a document
        # written before this field existed still reports at least what it can show.
        transitions_total = max(_whole_number(record.get("transitions_total")), len(transitions))
        # Whether that total is exact or a floor. A history retained before the counter existed can
        # only yield a lower bound, and a reader told "257 changes" when the truth is "at least 257"
        # has been given a number with more confidence than the data supports.
        #
        # That question has ONE owner and it is upstream: `backend_health.store._transition_totals`
        # decides, for each of the two optional fields, whether the document asserted something,
        # asserted nothing, or asserted something unreadable. Every record reaching this layer has
        # already been through it, so this layer REPUBLISHES and does not re-derive. It used to
        # re-derive, reading any non-boolean as `False` -- which is the store's answer for a CORRUPT
        # flag and the exact OPPOSITE of its answer for an ABSENT one. A downstream copy of a
        # presence-versus-validity rule is the shape that has now regressed three times in this
        # subsystem, and a copy that disagrees with its owner by construction is the fourth.
        #
        # This layer cannot import that owner. `backend_health/observer.py` imports THIS module for
        # `LOCAL_SERVICE_INVENTORY`, so the projection is the LOWER layer and the reverse import is
        # a hard cycle -- measured, not assumed:
        #     ImportError: cannot import name 'LOCAL_SERVICE_INVENTORY' from partially initialized
        #     module 'yolomux_lib.local_service_projection'
        # The owner's answer therefore arrives inside the document rather than through an import,
        # and `tests/test_local_service_projection.py` pins the two layers to the same pair for
        # every document the store can produce.
        #
        # `is True` rather than `bool(...)`: exactness is a CLAIM, and this layer will never
        # manufacture one from a value the owner did not already resolve to a real boolean. For a
        # store-normalized record it is exact identity; for anything else it fails closed.
        #
        # `observed` is the one question this layer genuinely owns. With no record at all there is
        # no history to describe, and the bare `0` beside it may not claim to be an exact count.
        transitions_total_exact = observed and record.get("transitions_total_exact") is True
        since_wall_time = _finite_float(current.get("since_wall_time"))

        if not self.available:
            unavailable_reason = HEALTH_OBSERVER_UNATTACHED
        elif not observed:
            unavailable_reason = HEALTH_RESOURCE_UNOBSERVED
        else:
            unavailable_reason = ""

        return {
            "observed": observed,
            "unavailable_reason_code": unavailable_reason,
            # The typed state vocabulary, verbatim from the store. The row's own `state`
            # (running/idle/issue/unavailable) is the legacy request-time classification and
            # is deliberately left untouched beside it; they answer different questions.
            "state": str(current.get("state") or ""),
            "reason_code": str(current.get("reason_code") or ""),
            "recovery_outcome": str(current.get("recovery_outcome") or ""),
            "process_epoch": str(current.get("process_epoch") or ""),
            "pid": _whole_number(current.get("pid")),
            "observed_at": _finite_float(current.get("observed_at")),
            "since_revision": _whole_number(current.get("since_revision")),
            "since_wall_time": since_wall_time,
            "state_age_seconds": max(0.0, self.now - since_wall_time) if observed and since_wall_time > 0 else None,
            "transitions": transitions[-SYSTEM_STATUS_MAX_TRANSITIONS:],
            "transitions_total": transitions_total,
            "transitions_total_exact": transitions_total_exact,
            # Truncated against the LIFETIME total, not the retained list: history the store
            # already evicted is older rows that exist and are not shown, which is exactly what
            # this flag means.
            "transitions_truncated": transitions_total > min(len(transitions), SYSTEM_STATUS_MAX_TRANSITIONS),
            "coverage": self._coverage(observed, aggregate),
            "metrics": self._metrics(service, observed, aggregate),
            "errors_by_reason": dict(_mapping(self._work(service).get("errors_by_reason"))),
        }

    # -- internals -----------------------------------------------------------

    def _work(self, service: str) -> Mapping[str, Any]:
        """This service's user-work RPC counters. Probe traffic is a separate class."""
        return _mapping(_mapping(self.traffic.get(str(service))).get(TRAFFIC_WORK_CLASS))

    def _coverage(self, observed: bool, aggregate: Mapping[str, Any]) -> dict[str, Any]:
        """Say exactly how complete each of the two aggregates is, and why.

        ``retained_counters`` is the store's per-epoch aggregate. Its own ``coverage`` field
        reads ``full`` while every count is a structural zero, because ``_accumulate`` only
        degrades coverage at an epoch change that lost a final sample -- never for an
        observer that reads no counters at all. Rendering that verbatim is the exact
        "partial aggregate shown as complete" defect, so an unread ``last_sample`` downgrades
        it here with a named reason.
        """

        if observed:
            retained_state = str(aggregate.get("coverage") or COVERAGE_FULL)
            retained_reasons = [str(reason) for reason in (aggregate.get("coverage_reasons") or [])]
            if _mapping(aggregate.get("last_sample")).get("counters_available") is not True:
                retained_state = COVERAGE_PARTIAL
                if COVERAGE_COUNTERS_NOT_OBSERVED not in retained_reasons:
                    retained_reasons.append(COVERAGE_COUNTERS_NOT_OBSERVED)
        else:
            retained_state = COVERAGE_UNAVAILABLE
            retained_reasons = []

        if observed:
            lifecycle_state = (
                COVERAGE_FULL
                if aggregate.get("lifecycle_classification_exact") is True
                else COVERAGE_PARTIAL
            )
            lifecycle_reasons = (
                []
                if lifecycle_state == COVERAGE_FULL
                else [COVERAGE_LIFECYCLE_LEGACY_UNCLASSIFIED]
            )
        else:
            lifecycle_state = COVERAGE_UNAVAILABLE
            lifecycle_reasons = []

        # The ledger totals are exact and continuous for this web process, including across
        # a peer restart. They are partial only against the retained history window, which
        # survives a web restart and can therefore start earlier than these counters do.
        epoch_started_at = _finite_float(_mapping(self.document).get("observer_epoch_started_at"))
        predates = bool(self.available and 0.0 < epoch_started_at < self.web_process_started_at)
        return {
            "retained_counters": retained_state,
            "retained_counter_reasons": sorted(retained_reasons),
            "lifecycle": lifecycle_state,
            "lifecycle_reasons": lifecycle_reasons,
            "counters": COVERAGE_PARTIAL if predates else COVERAGE_FULL,
            "counter_reasons": [COVERAGE_WEB_PROCESS_SCOPE] if predates else [],
            "counter_scope": COUNTER_SCOPE_WEB_PROCESS,
        }

    def _metrics(self, service: str, observed: bool, aggregate: Mapping[str, Any]) -> dict[str, Any]:
        """The numbers Keiven asked to see, each published exactly once, from one owner.

        Restarts and observations come from the retained store, which is the only thing that
        counts identity-verified epoch changes and survives a web restart. Requests, errors,
        completions, and response time come from the RPC ledger, which is the only thing that
        measured them. Neither number is republished from the other source.
        """

        work = self._work(service)
        latency = _mapping(work.get(TRAFFIC_RESPONSE_LATENCY))
        timed = _whole_number(latency.get("count"))
        unobserved = {
            "state": COVERAGE_UNAVAILABLE,
            "reason_code": HEALTH_OBSERVER_UNATTACHED if not self.available else HEALTH_RESOURCE_UNOBSERVED,
            "reason": "The health observer has not recorded this service yet",
        }
        untimed = {
            "state": COVERAGE_UNAVAILABLE,
            "reason_code": "no_completed_request",
            "reason": "No completed request has been timed in this web process",
        }
        # A service this process has never called has no ledger at all, and zero requests is
        # then the exact answer -- hence the `0` default rather than a null. This fallback
        # fires only if the ledger published something that is not a finite number.
        uncounted = {
            "state": COVERAGE_UNAVAILABLE,
            "reason_code": "counters_unreadable",
            "reason": "The local-service RPC ledger returned no usable counter",
        }
        return {
            "restart_count": measurement(aggregate.get("restart_count") if observed else None, **unobserved),
            "process_start_count": measurement(aggregate.get("process_start_count") if observed else None, **unobserved),
            "demand_start_count": measurement(aggregate.get("demand_start_count") if observed else None, **unobserved),
            "unexpected_restart_count": measurement(aggregate.get("unexpected_restart_count") if observed else None, **unobserved),
            "observations": measurement(aggregate.get("observations") if observed else None, **unobserved),
            # `accepted` is every attempt this process made, including the ones that never
            # reached the service. That is the request count a monitor has to report: an
            # attempt lost to an absent socket is exactly the failure being watched for.
            "request_count": measurement(work.get("accepted", 0), **uncounted),
            "error_count": measurement(work.get("errors", 0), **uncounted),
            "completed_count": measurement(work.get("completed", 0), **uncounted),
            # `avg_ms`/`max_ms` are published as 0.0 by the ledger when nothing completed.
            # Zero is not a measured response time, so an untimed service says so instead.
            "latency_average_ms": measurement(latency.get("avg_ms") if timed else None, **untimed),
            "latency_max_ms": measurement(latency.get("max_ms") if timed else None, **untimed),
        }


@dataclass(frozen=True)
class LocalServiceRow:
    """One immutable service row, plus the fields the collector derives itself.

    ``fields`` is the producer's whole bounded row behind a read-only view, so a
    consumer cannot mutate the snapshot it was handed. The derived members are the
    ones no per-service status builder owns: uptime comes from started_at here, once,
    rather than being added to six separate builders.
    """

    service: str
    pid: int
    started_at: float
    uptime_seconds: float | None
    cpu_percent: float | None
    rss_bytes: int | None
    fields: Mapping[str, Any]

    @property
    def running(self) -> bool:
        return self.pid > 0

    def payload(self) -> dict[str, Any]:
        """The exact row shape the HTTP projection published before M3."""
        return {**dict(self.fields), "uptime_seconds": self.uptime_seconds}


@dataclass(frozen=True)
class LocalServicesSnapshot:
    """The immutable local-services snapshot. This dataclass is the schema.

    Nothing here is optional or producer-shaped: every field is derived by the one
    collector, in one place, from the row producers it was given.
    """

    schema_version: int
    observed_at: float
    inventory: tuple[str, ...]
    rows: tuple[LocalServiceRow, ...]
    processes: int
    cpu_percent: float
    rss_bytes: int
    ledger: Mapping[str, Any] = field(default_factory=dict)
    recovery_events: tuple[Mapping[str, Any], ...] = ()

    def row(self, service: str) -> LocalServiceRow:
        for row in self.rows:
            if row.service == service:
                return row
        raise KeyError(service)

    @property
    def totals(self) -> dict[str, Any]:
        return {"processes": self.processes, "cpu_percent": self.cpu_percent, "rss_bytes": self.rss_bytes}

    def payload(
        self,
        project_row: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        health: "RetainedHealth | None" = None,
    ) -> dict[str, Any]:
        """Render the `/api/system-status` `local_services` dict.

        The typed projection (`state`/`reason_code`/`essential`/`alerting`/`metrics`)
        stays with its existing owner; this only feeds it. There is no separate `alert`
        summary: every consumer reads each row's own `alerting`/`reason_code`, and a second
        rolled-up copy of that fact was a dead contract nothing in the UI ever read.

        `health` is the snapshot-level retained-health provenance (M8). The per-row health
        block is rendered by `project_row`, which already has the same `RetainedHealth`;
        this level publishes only what describes the whole document -- revision, age, epoch,
        persistence -- so no row carries a private copy of it.
        """
        services = [project_row(row.payload()) for row in self.rows]
        return {
            "schema_version": self.schema_version,
            "inventory": self.inventory,
            "services": services,
            "totals": self.totals,
            "ledger": dict(self.ledger),
            "recovery_events": [dict(event) for event in self.recovery_events],
            "health": (health if health is not None else RetainedHealth()).payload(),
        }


class LocalServicesCollector:
    """The one caller-ready owner of the six-service projection.

    Row producers are supplied per collection rather than bound once, because the
    app resolves `self.job_client.runtime_status` and friends at call time; binding
    them at construction would freeze a client the app may replace.
    """

    def __init__(
        self,
        row_producers: Callable[[], Mapping[str, Callable[[], Mapping[str, Any]]]],
        *,
        ledger: Callable[[], Mapping[str, Any]] | None = None,
        recovery_events: Callable[[Mapping[str, Mapping[str, Any]]], Sequence[Mapping[str, Any]]] | None = None,
        inventory: tuple[str, ...] = LOCAL_SERVICE_INVENTORY,
        clock: Callable[[], float] = time.time,
    ):
        self._row_producers = row_producers
        self._ledger = ledger
        self._recovery_events = recovery_events
        self.inventory = tuple(inventory)
        self.clock = clock

    def collect(self, *, include_diagnostics: bool = True) -> LocalServicesSnapshot:
        producers = self._row_producers()
        missing = [service for service in self.inventory if service not in producers]
        extra = [service for service in producers if service not in self.inventory]
        # A service silently dropped from (or smuggled into) the snapshot is the exact
        # M3 extraction defect: the projection keeps rendering and the row is just gone.
        if missing or extra:
            raise ValueError(
                f"local-services row producers do not match the inventory "
                f"(missing={sorted(missing)}, unexpected={sorted(extra)})"
            )
        now = self.clock()
        rows: list[LocalServiceRow] = []
        for service in self.inventory:
            produced = producers[service]()
            fields = dict(produced) if isinstance(produced, Mapping) else {}
            rows.append(self._row(service, fields, now))
        snapshot_rows = tuple(rows)
        processes = sum(1 for row in snapshot_rows if row.running)
        # Keep the JSON types the projection has always published: cpu_percent is a float
        # even with no running service (sum of an empty sequence is int 0), rss is an int.
        cpu_percent = float(sum(row.cpu_percent for row in snapshot_rows if row.cpu_percent is not None))
        rss_bytes = int(sum(row.rss_bytes for row in snapshot_rows if row.rss_bytes is not None))
        rows_by_service = {row.service: row.fields for row in snapshot_rows}
        return LocalServicesSnapshot(
            schema_version=LOCAL_SERVICES_SCHEMA_VERSION,
            observed_at=now,
            inventory=self.inventory,
            rows=snapshot_rows,
            processes=processes,
            cpu_percent=cpu_percent,
            rss_bytes=rss_bytes,
            ledger=MappingProxyType(dict(self._ledger() or {})) if include_diagnostics and self._ledger is not None else MappingProxyType({}),
            recovery_events=tuple(
                MappingProxyType(dict(event))
                for event in (self._recovery_events(rows_by_service) if include_diagnostics and self._recovery_events is not None else ())
                if isinstance(event, Mapping)
            ),
        )

    @staticmethod
    def _row(service: str, fields: dict[str, Any], now: float) -> LocalServiceRow:
        # The row's own `service` key is authoritative for the payload, but the
        # inventory key is what the snapshot is addressed by; disagreement means a
        # producer was wired to the wrong slot.
        declared = str(fields.get("service") or "").strip()
        if declared and declared != service:
            raise ValueError(f"row producer for {service!r} returned a row for {declared!r}")
        fields["service"] = service
        pid = int(fields.get("pid") or 0)
        started_at = _finite_float(fields.get("started_at"))
        uptime = max(0.0, now - started_at) if pid > 0 and started_at > 0 else None
        resources = fields.get("resources") if isinstance(fields.get("resources"), Mapping) else {}
        rss = resources.get("rss_bytes")
        return LocalServiceRow(
            service=service,
            pid=pid,
            started_at=started_at,
            uptime_seconds=uptime,
            cpu_percent=_finite_number(resources.get("cpu_percent")),
            rss_bytes=rss if isinstance(rss, int) and not isinstance(rss, bool) else None,
            fields=MappingProxyType(fields),
        )
