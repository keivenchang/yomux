"""M1 of DOIT.p0.daemon-monitor: freeze the real backend service catalog and its owner matrix.

This is the OWNER MATRIX, not a second copy of an id list. `tests/test_gate_panels.py:12`
and `:227` already pin the six ids and the rendered inventory literal; those are browser
tests that assert what the panel shows. This file asserts, statically and without starting
a single service, WHICH SYMBOL OWNS each role for each service:

    demand    -- who may create the process (the `ensure_started` path)
    status    -- who builds the row that `runtime_local_services()` composes
    identity  -- where pid / started_at / version / process record come from
    metrics   -- who produces the row's `resources` (cpu_percent, rss_bytes)
    recovery  -- which primitive may clear a latched failure, and whether anything calls it

Every value below is derived from source in this tree and cross-checked against it. The
inventory itself is read out of its ONE production declaration by AST rather than retyped,
so the matrix cannot silently stop covering a real service.

M2-M7 will change several of these rows. That is the point: this test is the before-picture
and is expected to fail loudly when an owner moves, so the move is deliberate and reviewed.

Re-pointed after M2 and M3, each change verified against source here before it was accepted:

  M3 moved the inventory literal. The inline `inventory = (...)` tuple in the body of
  `runtime_local_services()` is gone; `local_service_projection.py:48` declares
  `LOCAL_SERVICE_INVENTORY` and the collector defaults to it. The AST read follows it, and a
  new assertion pins that ordered literal to exactly ONE production file, so the move cannot
  quietly become a second copy.

  M3 moved the six row producers. `runtime_local_services()` no longer assigns six local names
  into a `rows = [...]` list; `local_services_row_producers()` maps each id to the callable
  that owns its whole row, and `LocalServicesCollector.collect()` composes them in inventory
  order and refuses a producer map that does not match the inventory exactly. statsd stopped
  being the exception: its row was an inline dict literal in the projection body, and is now
  the named producer `statsd_runtime_status`, so no service's row shape lives in two places.

  M2 gave watchd a display label ("File watching") and made it essential. The exclusion from
  ESSENTIAL_LOCAL_SERVICES was a second copy of the "routine absence is not an outage" rule
  that `demand_started` already owns, and `system_status_service` checks `demand_started`
  BEFORE it consults the essential set. That ordering is what made the removal safe, so it is
  now asserted on the classifier itself: an absent watchd still classifies `idle` with
  `alerting` False while being essential, and a watchd that recorded a failure still alerts.

  M2 moved watchd's identity and metrics owners. `started_at` is no longer hardcoded 0.0 and
  `resources` is no longer hardcoded {}; both come from the persisted registry record through
  `registry_process_identity`, which is one file read plus a /proc read and issues NO RPC. The
  frozen identity divergence therefore shrinks to statsd alone -- the one service with no
  persisted process record at all, identified by a live `status` RPC.

  M4's health contract re-decided the demand/health split PER SERVICE instead of flagging all
  six. The previous freeze here said "only two of six declare demand_started" and explained it
  with a comment claiming all six were demand-started in fact. That claim was false: statsd and
  jobd are each pinned up by a background owner in this process, so their absence is a verified
  outage and neither may carry the flag. statusd and approvald genuinely are demand-scoped and
  now declare it, taking the count to four. jobd's one legitimate absence -- this process does
  not own background scheduling -- is expressed by a separate typed field with its own named
  owner, because reusing `demand_started` for it would have made a real jobd outage silent.

  M7 added statsd's BOUNDED expected-absence window and the observer's recovery path. Two rows
  declare a typed expected absence now, not one. The freeze that had to move is `declared`;
  what did NOT move is that each declaration still names the symbol deciding it, still reaches
  its token through one production constant, and still may not coexist with `demand_started`.
  The per-service loop used to hardcode jobd's constant name and resolve the owner with
  `.fget`, i.e. it required the decider to be a `@property`. statsd's decider is deliberately a
  module-level function -- the row producer already holds the one `StatsCurrentRuntime.status()`
  read it needs, and a property would cost a second status RPC per probe -- so the loop resolves
  either shape instead of forcing production into the expensive one. See `_deciding_callable`.

  M7 also exposed a HOLE in this file rather than a stale freeze. The recovery census asked for
  the literal `.retry()`, with empty parens, so `control.retry(resource)` in the observer never
  matched: the census stayed green through the whole landing while its own comment claimed to
  list "the only production modules that invoke recovery at all". The needle is `.retry(` now,
  the observer is censused as a call site, and the choke point it must go through is counted.
  The same audit found the abandoned-name net reading ten owner-shaped fields out of twelve --
  `absence_expected_owner` and `absence_expected_constant`, the two newest, were not covered,
  which is the field an abandoned name would actually come back through.

  M9 connected the recovery planner, so `recovery_wired_today` moved for the first time since
  this file was written: `{"statsd"}` became every service with a client wrapper to call, i.e.
  all six minus indexd. It is no longer a hand-maintained flag either -- it is cross-checked
  against the app's recovery map AND against each row's `recovery_client_entrypoint`, so a
  service cannot claim to be wired while nothing can reach it, and cannot be wired in silence.
  `app.py` joins `RETRY_DEFINITIONS` with the dispatcher the observer is finally constructed
  with, and the assertion that matters is that it is a DISPATCHER: by AST it calls the map and a
  dict lookup and nothing else, so `LocalServiceRegistry.retry` is still the one primitive.

  M9 also found the M7 needle still half-blind, one shape further out. `.retry(` matches an
  invocation; the app's map reaches recovery by NAMING the bound method -- `"jobd":
  self.job_client.retry,` -- so the invocation census would have gone on reporting app.py as
  recovery-free while app.py was the only module in the tree that could start a retry. The
  census has a second half now, `RETRY_ATTRIBUTE_FILES`, whose needle matches a reference.
  Measured negative control: a bare `handler = client.retry` planted in
  `local_service_projection.py` leaves the invocation census GREEN and turns the new one RED.

Nothing was relaxed to let these pass. `WatchClient.runtime_status` still has zero production
call sites, no abandoned-topology name may appear as a row or an owner, no resource may have
two owners for one role, both shims are still load-bearing, and a full projection still names
no start primitive anywhere on its path.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import inspect
import re
import shutil
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from yolomux_lib.backend_health.observer import ABSENCE_EXPECTED_REASON_FIELD

from yolomux_lib import app as app_module


# Names from the abandoned `abandoned/0.6.12` topology. None of them exist in this tree, and
# none may come back as a service id, a spawned module, or a declared owner.
ABANDONED_TOPOLOGY_NAMES = (
    "daemon",
    "storaged",
    "SUBSYSTEM_SPECS",
    "LocalProcessSupervisor",
    "LocalPersistentMuxListener",
    "runtime_service_control_executor",
    "SharedBackendSupervisorControl",
    "quick_open_search_payload",
    "StoragedSearchCoordinator",
)
ABANDONED_MODULES = ("yolomux_lib.daemon", "yolomux_lib.storaged", "yolomux_lib.storaged_process")


def _abandoned_module_is_truly_present(module: str) -> bool:
    """True only when an abandoned module resolves to REAL importable code.

    A fresh checkout has none of the `ABANDONED_MODULES` on disk, so `find_spec` returns None.
    But the real 0.7.2->0.7.3 upgrade path leaves an empty `yolomux_lib/daemon/` directory with
    NO `__init__.py` behind. Python resolves that residue as an implicit *namespace package*: a
    spec whose `origin` is None and whose `submodule_search_locations` is a `_NamespacePath`. No
    code is loadable from it. Treating that spec as "present" is what falsely failed the oracle in
    an upgraded/dirty checkout (W15). A namespace-only residue is acceptable; only a spec backed by
    a real origin (an ordinary module file, or a package with `__init__.py`) is a genuine
    violation -- an abandoned module that actually came back.
    """

    spec = importlib.util.find_spec(module)
    if spec is None:
        return False
    return spec.origin is not None

ROLES = ("demand", "status", "identity", "metrics", "recovery")

# The one process-creating primitive. Every service reaches the operating system through it.
DEMAND_PRIMITIVE = "yolomux_lib.local_services.registry:LocalServiceRegistry.ensure_started"
# The one latched-failure-clearing primitive. Every `def retry` in yolomux_lib delegates here.
RECOVERY_PRIMITIVE = "yolomux_lib.local_services.registry:LocalServiceRegistry.retry"
# The one status projection composed into /api/system-status.
COMPOSED_PROJECTION = "yolomux_lib.app:TmuxWebtermApp.runtime_local_services"
SYSTEM_STATUS_SERVICE = "yolomux_lib.app:TmuxWebtermApp.system_status_service"

# M3 split the projection into three named owners. The inventory literal lives in one module,
# the row producers in one map, and the composition in one collector.
INVENTORY_MODULE = "yolomux_lib.local_service_projection"
INVENTORY_NAME = "LOCAL_SERVICE_INVENTORY"
INVENTORY_FILE = "yolomux_lib/local_service_projection.py"
ROW_PRODUCER_MAP = "yolomux_lib.app:TmuxWebtermApp.local_services_row_producers"
SNAPSHOT_OWNER = "yolomux_lib.app:TmuxWebtermApp.local_services_snapshot"
COLLECTOR = "yolomux_lib.local_service_projection:LocalServicesCollector.collect"

# The two identity owners that read the persisted `*.service.json` process record. Both are
# registry-derived and differ only in cost: `LocalServiceRegistry.status` returns the record
# alongside a live `status` RPC, while `registry_process_identity` reads the record plus /proc
# and issues no RPC at all -- which is why a demand-scoped service must use the second one.
REGISTRY_STATUS_IDENTITY = "yolomux_lib.local_services.registry:LocalServiceRegistry.status"
REGISTRY_RECORD_IDENTITY = "yolomux_lib.local_service_projection:registry_process_identity"
REGISTRY_RECORD_IDENTITY_OWNERS = frozenset({REGISTRY_STATUS_IDENTITY, REGISTRY_RECORD_IDENTITY})

# The two attribute names that may create or lease a process. Neither may be named anywhere on
# the projection's path: a diagnostics read that starts a demand-scoped service is the exact
# defect M2 and M3 were built to avoid.
START_PRIMITIVE_ATTRIBUTES = frozenset({"ensure_started", "acquire_lease"})

# The one hop statsd's row builder delegates through, asserted rather than assumed wherever it
# is followed, so the client keeps owning statsd's identity and metrics after the M3 move.
STATSD_IDENTITY_HOP = "current_runtime = self.stats_current_runtime.status()"
STATSD_METRICS_HOP = "current_service = self.stats_current_client.runtime_status(current_service)"


@dataclass(frozen=True)
class ServiceOwners:
    """One row of the owner matrix. Owner refs are `module:qualname`."""

    # `python -m <spec_module> --serve` is what registry._spawn launches (registry.py:1175).
    spec_module: str
    # Where the code actually lives. Differs from spec_module for the two shimmed services.
    implementation_module: str
    # The client class this web process reaches the service through.
    client_owner: str
    demand_owner: str
    demand_entrypoint: str
    # The exact delegation line proving the entrypoint reaches the one primitive.
    demand_evidence: str
    status_owner: str
    # The exact expression `local_services_row_producers()` maps this service id to. Before M3
    # this was the expression `runtime_local_services()` assigned to the row's local name, and
    # statsd had none because its row was built inline; all six are named producers now.
    status_row_expression: str
    identity_owner: str
    identity_evidence: str
    # "" means this service publishes no process metrics at all.
    metrics_owner: str
    metrics_evidence: str
    recovery_owner: str
    # "" means no client-level retry wrapper exists for this service.
    recovery_client_entrypoint: str
    # Whether any production call site can actually trigger recovery today. Before M9 exactly one
    # service could, and only because a human clicked; the observer's automatic path existed but
    # was handed no control. Cross-checked below against the app's recovery map, so this is a
    # derived claim rather than a flag someone remembers to flip.
    recovery_wired_today: bool
    essential: bool
    # The STATIC claim "nothing in a running system keeps this service hot, so absence alone is
    # not a failure". True only where no background loop or lease exercises the service.
    demand_started_declared: bool
    # The DYNAMIC claim "this service IS pinned up by a named owner in this process, and that
    # owner is not engaged right now". "" means the row declares no such reason at all. A
    # service may declare this OR demand_started, never both -- the observer resolves a row
    # claiming both as `down`, and `test_no_service_declares_both_absence_excuses` pins it.
    absence_expected_reason: str
    # The exact symbol whose state decides that reason, so the claim has a named owner too.
    # Deliberately NOT constrained to one Python shape: jobd's decider is a `@property` on the
    # client that already holds the answer, statsd's is a module-level function the row producer
    # calls with the ONE `StatsCurrentRuntime.status()` read it already performed. Forcing
    # statsd's into a property would buy a uniform test at the price of a second status RPC per
    # probe, so the test resolves either shape instead. See `_deciding_callable`.
    absence_expected_owner: str
    # The production constant that spells the token, read out of source rather than retyped, so
    # the row cannot drift from the string the service actually publishes.
    absence_expected_constant: str


CATALOG: dict[str, ServiceOwners] = {
    "indexd": ServiceOwners(
        spec_module="yolomux_lib.search.search_indexer",
        implementation_module="yolomux_lib.search.search_indexer",
        client_owner="yolomux_lib.search.search_indexer:SearchIndexerClient",
        demand_owner=DEMAND_PRIMITIVE,
        demand_entrypoint="yolomux_lib.search.search_indexer:SearchIndexerClient.ensure_started",
        demand_evidence="return self.registry.ensure_started()",
        status_owner="yolomux_lib.search.search_indexer:SearchIndexerClient.runtime_status",
        status_row_expression="self.search_indexer.runtime_status",
        identity_owner=REGISTRY_STATUS_IDENTITY,
        identity_evidence="self.registry.status()",
        metrics_owner="yolomux_lib.local_services.registry:LocalServiceRegistry.resources",
        metrics_evidence='"resources": self.registry.resources(int(payload.get("pid") or 0)),',
        recovery_owner=RECOVERY_PRIMITIVE,
        # SearchIndexerClient is not a LocalServiceClient, so it inherits no retry wrapper.
        recovery_client_entrypoint="",
        # The ONE service M9 left unwired, and the map says so by omission rather than by a
        # second list: with no client wrapper to call, the only way to recover indexd from the
        # observer would be to reach into its registry, which would put a recovery entrypoint
        # outside the wrapper set this file pins. `LocalServiceRecoveryControl.retry` returns
        # False for it and touches nothing.
        recovery_wired_today=False,
        essential=True,
        demand_started_declared=True,
        absence_expected_reason="",
        absence_expected_owner="",
        absence_expected_constant="",
    ),
    "statsd": ServiceOwners(
        spec_module="yolomux_lib.stats_current.service",
        implementation_module="yolomux_lib.stats_current.service",
        client_owner="yolomux_lib.stats_current.client:StatsCurrentClient",
        demand_owner=DEMAND_PRIMITIVE,
        demand_entrypoint="yolomux_lib.stats_current.client:StatsCurrentClient.ensure_started",
        demand_evidence="started = self._transport.registry.ensure_started()",
        # M3: statsd's row was the one built inline in the projection body, so its shape lived
        # in two places. It is now a named producer like the other five, and that producer
        # delegates identity and metrics to the client through the two asserted hops above.
        status_owner="yolomux_lib.app:TmuxWebtermApp.statsd_runtime_status",
        status_row_expression="self.statsd_runtime_status",
        # statsd does not read LocalServiceRegistry.status(); its identity comes from a live
        # `status` RPC, so it carries no persisted process `record`. This is the one identity
        # divergence M2 did not resolve, and the only one left.
        identity_owner="yolomux_lib.stats_current.runtime:StatsCurrentRuntime._service_status",
        identity_evidence="response = self.client.status()",
        metrics_owner="yolomux_lib.local_services.registry:LocalServiceRegistry.resources",
        metrics_evidence='"resources": self._transport.registry.resources(pid),',
        recovery_owner=RECOVERY_PRIMITIVE,
        recovery_client_entrypoint="yolomux_lib.stats_current.client:StatsCurrentClient.retry",
        # The only service wired before M9, and by a HUMAN path: POST /api/stats-retry
        # (http_routes.py:1564 -> :436 -> stats_current/http.py:183). M9 adds the automatic
        # path -- the observer's control maps statsd to the same client wrapper -- so this
        # value is unchanged and its meaning is not: a down statsd is now retried without
        # anyone clicking anything.
        recovery_wired_today=True,
        essential=True,
        demand_started_declared=False,
        # NOT demand_started, and that is unchanged: a background loop leases statsd and appends
        # to it every second, so a steady-state absence is a verified outage.
        #
        # M7 added the one BOUNDED window where that is not yet true. Between this process
        # deciding it owns the background role and `StatsCurrentRuntime` actually holding the
        # pin, statsd legitimately does not exist yet, and the observer's first cycle used to
        # publish a false `down` for it (measured 4.025-4.033s on four isolated starts). The
        # excuse is not a boot grace period and not a timer: `statsd_pin_pending` reads the pin
        # owner's own live status and requires ALL FOUR of supervisor.alive, leased is not True,
        # failure_count == 0, and a phase in {starting, acquiring_lease, starting_scheduler}.
        # Every one of those closes a hole -- a lost election, a pin already taken, a recorded
        # failure, or a demoting/stopping/backoff/blocked process -- so the excuse cannot outlive
        # the pending start and a statsd that actually died still alarms.
        absence_expected_reason="stats_pin_pending",
        absence_expected_owner="yolomux_lib.app:statsd_pin_pending",
        absence_expected_constant="STATSD_ABSENT_WHILE_PIN_PENDING",
    ),
    "jobd": ServiceOwners(
        # Load-bearing shim: yolomux_lib/jobd.py aliases yolomux_lib.infra.jobd, and the spec
        # names the shim, so the child really launches through it.
        spec_module="yolomux_lib.jobd",
        implementation_module="yolomux_lib.infra.jobd",
        client_owner="yolomux_lib.infra.jobd:JobClient",
        demand_owner=DEMAND_PRIMITIVE,
        demand_entrypoint="yolomux_lib.local_services.client:LocalServiceClient.ensure_started",
        demand_evidence="started = self.registry.ensure_started()",
        status_owner="yolomux_lib.infra.jobd:JobClient.runtime_status",
        status_row_expression="self.job_client.runtime_status",
        identity_owner=REGISTRY_STATUS_IDENTITY,
        identity_evidence="status = self.registry.status()",
        # The only service whose metrics cover worker children as well as the broker.
        metrics_owner="yolomux_lib.local_services.registry:LocalServiceRegistry.resources_for_pids",
        metrics_evidence='"resources": self.registry.resources_for_pids(pid, worker_pids)',
        recovery_owner=RECOVERY_PRIMITIVE,
        recovery_client_entrypoint="yolomux_lib.local_services.client:LocalServiceClient.retry",
        # M9. The observer's control maps jobd to this wrapper, so a jobd that is down while
        # THIS process holds the scheduler lease is retried; a jobd absent because the lease is
        # held elsewhere still publishes `retry_blocked_scheduler_not_owned` and is not touched.
        recovery_wired_today=True,
        essential=True,
        demand_started_declared=False,
        # Pinned up by the scheduler lease, so NOT demand_started. The one expected absence is
        # the other side of that lease: this process does not own background scheduling.
        absence_expected_reason="scheduler_not_owned",
        absence_expected_owner="yolomux_lib.infra.jobd:JobClient.holds_scheduler_lease",
        absence_expected_constant="JOBD_ABSENT_WITHOUT_SCHEDULER_LEASE",
    ),
    "statusd": ServiceOwners(
        spec_module="yolomux_lib.statusd",
        implementation_module="yolomux_lib.statusd",
        client_owner="yolomux_lib.statusd_client:StatusClient",
        demand_owner=DEMAND_PRIMITIVE,
        demand_entrypoint="yolomux_lib.local_services.client:LocalServiceClient.ensure_started",
        demand_evidence="started = self.registry.ensure_started()",
        status_owner="yolomux_lib.statusd_client:StatusClient.runtime_status",
        status_row_expression="self.status_client.runtime_status",
        identity_owner=REGISTRY_STATUS_IDENTITY,
        identity_evidence="runtime = self.registry.status()",
        metrics_owner="yolomux_lib.local_services.registry:LocalServiceRegistry.resources",
        metrics_evidence='"resources": self.registry.resources(pid),',
        recovery_owner=RECOVERY_PRIMITIVE,
        recovery_client_entrypoint="yolomux_lib.local_services.client:LocalServiceClient.retry",
        # M9. Wired, and still never retried while merely resting: `demand_started` fences a
        # statusd that is absent with no recorded failure, so only a statusd that ran and
        # exited -- or that stopped answering -- reaches the ladder.
        recovery_wired_today=True,
        essential=True,
        demand_started_declared=True,
        absence_expected_reason="",
        absence_expected_owner="",
        absence_expected_constant="",
    ),
    "watchd": ServiceOwners(
        spec_module="yolomux_lib.watchd",
        implementation_module="yolomux_lib.watchd",
        client_owner="yolomux_lib.watchd_client:WatchClient",
        demand_owner=DEMAND_PRIMITIVE,
        # watchd is leased, not status-probed: acquire_lease() is what starts it.
        demand_entrypoint="yolomux_lib.local_services.registry:LocalServiceRegistry.acquire_lease",
        # acquire_lease is itself a registry method, so it calls the primitive on self.
        demand_evidence="if not self.ensure_started():",
        # The System row deliberately does NOT come from WatchClient.runtime_status; calling
        # it would demand-start a demand-scoped service from a diagnostics path.
        status_owner="yolomux_lib.app:TmuxWebtermApp.watchd_runtime_status",
        status_row_expression="self.watchd_runtime_status",
        # M2 resolved this. It used to be the in-process bridge mirror, which knows a lease PID
        # but no birth time, so started_at was hardcoded 0.0 (uptime permanently blank) and
        # resources was hardcoded {} (no CPU/memory at all). Identity now comes from the same
        # persisted registry record the other four use -- read directly, with no RPC, because
        # an RPC from a diagnostics path would demand-start a demand-scoped service.
        identity_owner=REGISTRY_RECORD_IDENTITY,
        identity_evidence="identity = local_service_projection.registry_process_identity(self.watch_client.registry)",
        metrics_owner="yolomux_lib.local_services.registry:LocalServiceRegistry.resources",
        metrics_evidence='"resources": self.watch_client.registry.resources(identity.pid),',
        recovery_owner=RECOVERY_PRIMITIVE,
        recovery_client_entrypoint="yolomux_lib.local_services.client:LocalServiceClient.retry",
        # M9. Same fence as statusd: demand-scoped absence is never retried, a recorded failure
        # is. The row is built without an RPC, so wiring recovery here does not add one.
        recovery_wired_today=True,
        # M2 made watchd essential. The exclusion was a second copy of the rule `demand_started`
        # already owns; see test_essential_services_are_now_the_whole_catalog.
        essential=True,
        demand_started_declared=True,
        absence_expected_reason="",
        absence_expected_owner="",
        absence_expected_constant="",
    ),
    "approvald": ServiceOwners(
        # Load-bearing shim, same shape as jobd.
        spec_module="yolomux_lib.approvald",
        implementation_module="yolomux_lib.approval.approvald",
        client_owner="yolomux_lib.approval.approvald:ApprovalClient",
        demand_owner=DEMAND_PRIMITIVE,
        demand_entrypoint="yolomux_lib.local_services.client:LocalServiceClient.ensure_started",
        demand_evidence="started = self.registry.ensure_started()",
        status_owner="yolomux_lib.approval.approvald:ApprovalClient.runtime_status",
        status_row_expression="self.approval_client.runtime_status",
        identity_owner=REGISTRY_STATUS_IDENTITY,
        identity_evidence="status = self.registry.status()",
        metrics_owner="yolomux_lib.local_services.registry:LocalServiceRegistry.resources",
        metrics_evidence='"resources": self.registry.resources(int(payload.get("pid") or 0)),',
        recovery_owner=RECOVERY_PRIMITIVE,
        recovery_client_entrypoint="yolomux_lib.local_services.client:LocalServiceClient.retry",
        # M9. Same fence as statusd and watchd.
        recovery_wired_today=True,
        essential=True,
        demand_started_declared=True,
        absence_expected_reason="",
        absence_expected_owner="",
        absence_expected_constant="",
    ),
}

# Every `def retry` in production, and what each one delegates to. No second recovery PRIMITIVE
# may appear beside LocalServiceRegistry.retry: every other entry here is a wrapper or a
# dispatcher that provably bottoms out in it.
#
# M9 added the fifth, `app.py`. It is the dispatcher the observer is finally constructed with --
# `LocalServiceRecoveryControl.retry(resource)` looks the resource up in
# `local_services_recovery_entrypoints()` and calls that service's own client wrapper. It starts
# nothing itself, which `RECOVERY_ENTRYPOINT_EXPRESSIONS` below proves per service.
RETRY_DEFINITIONS = {
    "yolomux_lib/local_services/registry.py": "primitive",
    "yolomux_lib/local_services/client.py": "self.registry.retry()",
    "yolomux_lib/stats_current/client.py": "self._transport.registry.retry()",
    "yolomux_lib/stats_current/http.py": "self.client.retry()",
    "yolomux_lib/app.py": "entrypoint() from local_services_recovery_entrypoints()",
}
# The app-level recovery map: service id -> the exact expression
# `local_services_recovery_entrypoints()` maps it to. Five of six; see the indexd row's
# `recovery_client_entrypoint == ""`. Read out of app.py by AST and cross-checked against the
# catalog below, so this map and the per-service `recovery_client_entrypoint` declarations
# cannot drift apart.
RECOVERY_ENTRYPOINT_EXPRESSIONS = {
    "statsd": "self.stats_current_client.retry",
    "jobd": "self.job_client.retry",
    "statusd": "self.status_client.retry",
    "watchd": "self.watch_client.retry",
    "approvald": "self.approval_client.retry",
}
RECOVERY_ENTRYPOINT_MAP = "yolomux_lib.app:TmuxWebtermApp.local_services_recovery_entrypoints"
RECOVERY_CONTROL_OWNER = "yolomux_lib.app:TmuxWebtermApp.local_services_recovery_control"
RECOVERY_CONTROL = "yolomux_lib.app:LocalServiceRecoveryControl"
# The whole public surface the observer is handed. Anything else here is a destructive operation
# waiting to be reachable from the recovery path.
RECOVERY_CONTROL_PUBLIC_SURFACE = frozenset({"retry"})
# The only production modules that invoke recovery at all. registry.py defines the primitive
# but never calls it, so it is deliberately absent here.
#
# M7 added the fifth: `backend_health/observer.py` calls `control.retry(resource)` from its one
# choke point `_issue_retry`. It is a CALL SITE, never a definition -- the observer declares no
# `def retry` (RETRY_DEFINITIONS above is what proves that) and reaches recovery only through an
# injected control, which no production caller supplies today.
#
# The needle used to be the literal `.retry()`, with empty parens. That did not match
# `control.retry(resource)`, so this census went GREEN through the whole M7 landing while its own
# docstring claim -- "the only production modules that invoke recovery at all" -- was false. The
# needle is `.retry(` now, so a call site cannot hide behind an argument list.
OBSERVER_MODULE = "yolomux_lib/backend_health/observer.py"
RETRY_CALL_SITE_FILES = frozenset({
    "yolomux_lib/local_services/client.py",
    "yolomux_lib/stats_current/client.py",
    "yolomux_lib/stats_current/http.py",
    "yolomux_lib/http_routes.py",
    OBSERVER_MODULE,
})
# The SECOND half of the census, added by M9 because the first half still could not see the
# shape M9 introduces. `.retry(` matches an INVOCATION. `app.py` reaches recovery by naming the
# bound method -- `"jobd": self.job_client.retry,` -- and calling it later through a local name,
# so the invocation needle would have gone on reporting app.py as recovery-free while app.py was
# the only thing in the tree that could start a retry. That is the same hole `.retry()` had, one
# shape further out, so the fix is a needle that matches a REFERENCE too.
#
# `\.retry\b` deliberately excludes `.retry_at`, `.retry_band` and `.retry_seconds` (app.py's
# prewarm record, server_auth's rate-limit band, tmux_signals' backoff) -- three unrelated
# attributes that merely start with the same letters. Every file below is a real recovery
# reference, and every recovery reference in the tree is below.
RETRY_ATTRIBUTE_FILES = frozenset(RETRY_CALL_SITE_FILES | {"yolomux_lib/app.py"})
RETRY_ATTRIBUTE_PATTERN = r"\.retry\b"
# The observer may reach a control exactly once, from `_issue_retry`. More than one call site in
# that module means the choke point stopped being one.
OBSERVER_RETRY_CALL_SITES = 1

REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve(ref: str) -> object:
    """Resolve a `module:qualname` owner reference to the live object."""
    module_name, separator, qualname = ref.partition(":")
    assert separator and qualname, f"owner reference must be module:qualname, got {ref!r}"
    target: object = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    return target


_MODULE_SOURCE: dict[str, tuple[str, ast.Module]] = {}


def _module_source(module_name: str) -> tuple[str, ast.Module]:
    if module_name not in _MODULE_SOURCE:
        text = Path(inspect.getfile(importlib.import_module(module_name))).read_text(encoding="utf-8")
        _MODULE_SOURCE[module_name] = (text, ast.parse(text))
    return _MODULE_SOURCE[module_name]


def _source(ref: str) -> str:
    """Return the on-disk source of a `module:qualname` definition.

    Read from the file, not from the live attribute: conftest's autouse
    `isolated_file_index_background_hooks` fixture replaces
    `SearchIndexerClient.ensure_started` with a lambda for every test, so
    `inspect.getsource` on the runtime object returns the conftest line instead of the
    production code this milestone is freezing.
    """
    module_name, separator, qualname = ref.partition(":")
    assert separator and qualname, f"owner reference must be module:qualname, got {ref!r}"
    text, tree = _module_source(module_name)
    node: ast.AST = tree
    for part in qualname.split("."):
        body = getattr(node, "body", [])
        matches = [
            child
            for child in body
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == part
        ]
        assert len(matches) == 1, f"{ref}: expected one definition of {part}, found {len(matches)}"
        node = matches[0]
    segment = ast.get_source_segment(text, node)
    assert segment, ref
    return textwrap.dedent(segment)


def _function_ast(ref: str) -> ast.FunctionDef:
    node = ast.parse(_source(ref)).body[0]
    assert isinstance(node, ast.FunctionDef), node
    return node


def _literal_assignment(scope: ast.AST, name: str) -> object:
    """Return the single literal assigned to `name`, refusing a second assignment.

    Takes any scope -- a function body or a whole module -- and accepts both plain and
    annotated assignment, because M3 moved the inventory from `inventory = (...)` inside
    `runtime_local_services()` to the module-level, annotated
    `LOCAL_SERVICE_INVENTORY: tuple[str, ...] = (...)`. The "exactly once" rule is unchanged
    and is what makes a smuggled second declaration fail here rather than pass silently.
    """
    found: list[object] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            found.append(ast.literal_eval(node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            found.append(ast.literal_eval(node.value))
    assert len(found) == 1, f"{name} must be assigned exactly once, found {len(found)}"
    return found[0]


def _service_id_expressions(source: str, service_ids: tuple[str, ...], role: str) -> dict[str, str]:
    """Map each service id to the ONE expression a `{id: callable}` map gives it, in order.

    M3 moved the status half from six `name = expression` assignments in
    `runtime_local_services()` to the map `local_services_row_producers()` returns; M9 added the
    recovery half, `local_services_recovery_entrypoints()`, which has the same shape for the
    same reason. One reader serves both: a second copy of this walk is the duplication the file
    exists to catch. The rule is unchanged either way -- a service named twice is exactly the
    two-owners-for-one-role defect. Takes source text so a poisoned tree can be fed to it as a
    permanent negative control.
    """
    function = _function_ast_from_source(source)
    producers: dict[str, str] = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or key.value not in service_ids:
                continue
            if key.value in producers:
                raise AssertionError(
                    f"{key.value} has two {role}: "
                    f"{producers[key.value]!r} and {ast.unparse(value)!r}"
                )
            producers[key.value] = ast.unparse(value)
    return producers


def _called_attributes(ref: str) -> frozenset[str]:
    """Every attribute name this definition calls.

    Reads the AST rather than the text so prose in a docstring cannot answer a question about
    behaviour -- `registry_process_identity`'s docstring names `LocalServiceRegistry.status()`
    precisely to explain why it does not call it.
    """
    node = ast.parse(_source(ref)).body[0]
    return frozenset(
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    )


def _inventory_literal_sites(inventory: tuple[str, ...]) -> frozenset[str]:
    """Every production file spelling the ordered six-id sequence out as a literal.

    Order-sensitive and tuple/list only on purpose: `ESSENTIAL_LOCAL_SERVICES` is a set of the
    same six ids in a different order and is a different fact, frozen separately. What this
    catches is the inventory ORDER being retyped somewhere the collector does not read.
    """
    sites: set[str] = set()
    for path in sorted((REPO_ROOT / "yolomux_lib").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.Tuple, ast.List)):
                continue
            constants = [element.value for element in node.elts if isinstance(element, ast.Constant)]
            if len(constants) == len(node.elts) and tuple(map(str, constants)) == inventory:
                sites.add(str(path.relative_to(REPO_ROOT)))
    return frozenset(sites)


def _function_ast_from_source(source: str) -> ast.FunctionDef:
    node = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(node, ast.FunctionDef), node
    return node


def _abandoned_names_in(values: object) -> list[str]:
    """Return every abandoned-topology name appearing as an identifier token in `values`."""
    text = " ".join(sorted(str(value) for value in values))
    return [
        name
        for name in ABANDONED_TOPOLOGY_NAMES
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)
    ]


def _inventory_literal() -> tuple[str, ...]:
    """Read the frozen inventory tuple straight out of its one production declaration.

    Re-pointed by M3: this used to AST-read `inventory = (...)` from the body of
    `runtime_local_services()`. That literal no longer exists; `LOCAL_SERVICE_INVENTORY` in
    `yolomux_lib/local_service_projection.py` is now the one declaration, and the collector
    defaults to it. Still read from source, never retyped here.
    """
    _text, tree = _module_source(INVENTORY_MODULE)
    inventory = _literal_assignment(tree, INVENTORY_NAME)
    assert isinstance(inventory, tuple), inventory
    return tuple(str(item) for item in inventory)


class _StatusClassifier:
    """`system_status_service` with no app behind it.

    Both methods are self-contained -- one reads a row dict, the other is a staticmethod -- so
    the classifier can be exercised directly without constructing an app or touching a service.
    `system_status_metric` must stay wrapped in `staticmethod`: rebound as a plain function it
    would bind `self` into the `value` parameter and quietly measure the wrong thing.
    """

    system_status_metric = staticmethod(app_module.TmuxWebtermApp.system_status_metric)
    system_status_service = app_module.TmuxWebtermApp.system_status_service


def _classify(row: dict[str, object]) -> dict[str, object]:
    return _StatusClassifier().system_status_service(dict(row))


# --------------------------------------------------------------------------------------
# Catalog coverage
# --------------------------------------------------------------------------------------


def test_owner_matrix_covers_exactly_the_frozen_inventory():
    """The matrix has a row for every service the projection ships, in order, and no extras."""
    inventory = _inventory_literal()
    assert inventory == ("indexd", "statsd", "jobd", "statusd", "watchd", "approvald"), inventory
    assert tuple(CATALOG) == inventory, (tuple(CATALOG), inventory)
    # The AST read above found the live symbol, not a lookalike in some other scope.
    live = getattr(importlib.import_module(INVENTORY_MODULE), INVENTORY_NAME)
    assert live == inventory, (live, inventory)
    # M3 moved this literal, and a move that leaves a copy behind is the recurring defect here.
    # Exactly one production file spells the ordered six-id sequence out.
    sites = _inventory_literal_sites(inventory)
    assert sites == {INVENTORY_FILE}, sorted(sites)


def test_essential_services_are_now_the_whole_catalog():
    """M2 removed watchd's exclusion, because it was a second copy of a rule the row owns.

    ESSENTIAL_LOCAL_SERVICES used to exclude watchd so its routine absence would not read as an
    outage. `demand_started` already owns that rule and `system_status_service` checks it BEFORE
    consulting this set, so the exclusion was a duplicate that could disagree -- and it also
    said, falsely, that a watchd which recorded a real failure mattered less than the other
    five. The property that made the removal safe is asserted in the next test, not assumed.
    """
    declared = frozenset(name for name, owners in CATALOG.items() if owners.essential)
    assert declared == frozenset(CATALOG), sorted(frozenset(CATALOG) - declared)
    assert app_module.ESSENTIAL_LOCAL_SERVICES == declared, (
        app_module.ESSENTIAL_LOCAL_SERVICES,
        declared,
    )


def test_an_absent_demand_started_service_is_idle_and_not_alerting_even_though_essential():
    """The property that made watchd's exclusion removable, proven on the classifier itself."""
    absent = _classify({"service": "watchd", "pid": 0, "healthy": False, "demand_started": True})
    assert absent["essential"] is True, absent
    assert absent["state"] == "idle", absent
    assert absent["reason_code"] == "not_started", absent
    assert absent["alerting"] is False, absent
    # And the rule the removal deliberately did NOT weaken: a recorded failure still alerts,
    # which is the whole reason watchd stopped being excluded from the essential set.
    failed = _classify({
        "service": "watchd",
        "pid": 0,
        "healthy": False,
        "demand_started": True,
        "last_failure": "watchd exited",
    })
    assert failed["state"] == "unavailable", failed
    assert failed["alerting"] is True, failed
    # Ordering is what keeps the two rules from overruling each other: `demand_started` is
    # consulted before the essential set, so absence-by-design is classified before essentiality
    # is even read.
    source = _source(SYSTEM_STATUS_SERVICE)
    assert source.index("demand_started = row.get") < source.index("essential = service_id in"), source


def test_every_service_has_a_display_label_owner():
    """M2 gave watchd the capability name it lacked; a raw id in the UI is a visible defect.

    The System row and the Daemons roster display this label verbatim, so a service missing
    from the map is named "watchd" in the UI while every other service gets a capability name.
    """
    labels = _literal_assignment(
        _function_ast(SYSTEM_STATUS_SERVICE), "labels"
    )
    assert isinstance(labels, dict), labels
    assert frozenset(labels) == frozenset(CATALOG), sorted(labels)
    assert labels["watchd"] == "File watching", labels["watchd"]
    # `labels.get(service_id, service_id)` would hide a re-introduction of the raw-id label, so
    # no entry may simply repeat its own id.
    same_as_id = sorted(name for name, label in labels.items() if label == name)
    assert same_as_id == [], same_as_id


# --------------------------------------------------------------------------------------
# One owner per role
# --------------------------------------------------------------------------------------


def test_every_service_declares_one_named_owner_for_every_role():
    """No role is blank by accident, and every named owner resolves to a real symbol."""
    for service, owners in CATALOG.items():
        role_owners = {
            "demand": owners.demand_owner,
            "status": owners.status_owner,
            "identity": owners.identity_owner,
            # No blanks left: watchd used to publish no process metrics at all, and M2 gave it
            # the same registry sampler the other five use.
            "metrics": owners.metrics_owner,
            "recovery": owners.recovery_owner,
        }
        assert tuple(role_owners) == ROLES, role_owners
        for role, owner in role_owners.items():
            assert owner, f"{service} has no {role} owner"
            assert _resolve(owner) is not None, (service, role, owner)
        # The client class is the seam every role above is reached through, so it must
        # resolve too -- a matrix pointing at a class that does not exist owns nothing.
        assert isinstance(_resolve(owners.client_owner), type), (service, owners.client_owner)


def test_each_service_row_has_exactly_one_status_producer():
    """`local_services_row_producers()` names each row's owner once, in inventory order."""
    source = _source(ROW_PRODUCER_MAP)
    producers = _service_id_expressions(source, tuple(CATALOG), "status producers")
    # Order matters as much as membership: the collector renders rows in this order, and it is
    # the order the panel pins. Before M3 this was the `rows = [...]` list.
    assert tuple(producers) == tuple(CATALOG), (tuple(producers), tuple(CATALOG))
    for service, owners in CATALOG.items():
        assert producers[service] == owners.status_row_expression, (
            service,
            producers[service],
            owners.status_row_expression,
        )
    # This map is not merely declared, it is the one the published projection composes.
    snapshot_source = _source(SNAPSHOT_OWNER)
    assert "LocalServicesCollector(" in snapshot_source, snapshot_source
    assert "self.local_services_row_producers," in snapshot_source, snapshot_source
    assert "return self.local_services_snapshot().payload(" in _source(COMPOSED_PROJECTION)
    # And the collector composes exactly the inventory: a service silently dropped from, or
    # smuggled into, the snapshot raises rather than rendering a projection with a hole in it.
    collector_source = _source(COLLECTOR)
    assert "for service in self.inventory:" in collector_source, collector_source
    assert "if missing or extra:" in collector_source, collector_source
    assert "raise ValueError(" in collector_source, collector_source


def test_a_second_status_producer_for_one_service_is_rejected():
    """Permanent negative control for the duplicate-owner rule."""
    poisoned = (
        "def local_services_row_producers(self):\n"
        "    return {\n"
        "        'indexd': self.search_indexer.runtime_status,\n"
        "        'statsd': self.statsd_runtime_status,\n"
        "        'indexd': self.legacy_indexer.runtime_status,\n"
        "    }\n"
    )
    with pytest.raises(AssertionError, match="indexd has two status producers"):
        _service_id_expressions(poisoned, tuple(CATALOG), "status producers")


def test_a_second_recovery_entrypoint_for_one_service_is_rejected():
    """Permanent negative control for the duplicate-owner rule on the M9 recovery map."""
    poisoned = (
        "def local_services_recovery_entrypoints(self):\n"
        "    return {\n"
        "        'statsd': self.stats_current_client.retry,\n"
        "        'jobd': self.job_client.retry,\n"
        "        'jobd': self.legacy_job_client.retry,\n"
        "    }\n"
    )
    with pytest.raises(AssertionError, match="jobd has two recovery entrypoints"):
        _service_id_expressions(poisoned, tuple(CATALOG), "recovery entrypoints")


def test_status_owner_source_proves_the_identity_and_metrics_owners():
    """Each row builder reads identity and metrics from exactly the declared owner, once."""
    for service, owners in CATALOG.items():
        status_source = _source(owners.status_owner)
        # The row names `resources` once, wherever the sampling itself happens.
        assert status_source.count('"resources":') == 1, (service, status_source.count('"resources":'))
        metrics_source = _metrics_source(service, owners)
        assert metrics_source.count('"resources":') == 1, (service, metrics_source.count('"resources":'))
        assert owners.metrics_evidence in metrics_source, (service, owners.metrics_evidence)
        identity_source = _identity_source(service, owners)
        assert owners.identity_evidence in identity_source, (service, owners.identity_evidence)


def _metrics_source(service: str, owners: ServiceOwners) -> str:
    """Where the row's `resources` are actually sampled, following one PROVEN hop.

    M3 made statsd's row a named producer in app.py, and that producer delegates the sampling
    to its client rather than copying it. The hop is asserted here rather than assumed, so the
    registry sampler stays the one metrics owner and a copied literal would not pass as one.
    """
    if service == "statsd":
        assert STATSD_METRICS_HOP in _source(owners.status_owner), service
        return _source("yolomux_lib.stats_current.client:StatsCurrentClient.runtime_status")
    return _source(owners.status_owner)


def _identity_source(service: str, owners: ServiceOwners) -> str:
    """Where the row's pid/started_at actually originate, following PROVEN hops when needed."""
    if service == "indexd":
        # runtime_status delegates identity to service_status() one line above it.
        return _source("yolomux_lib.search.search_indexer:SearchIndexerClient.service_status")
    if service == "statsd":
        # Two hops after M3, both asserted: the row builder reads the runtime's status, and the
        # runtime fills its `service` key from the live RPC that is statsd's identity owner.
        assert STATSD_IDENTITY_HOP in _source(owners.status_owner), service
        assert '"service": self._service_status(),' in _source(
            "yolomux_lib.stats_current.runtime:StatsCurrentRuntime.status"
        ), service
        return _source(owners.identity_owner)
    if service == "watchd":
        # A substring match on the evidence line is NOT sufficient here, and a negative control
        # proved it: watchd still reads the bridge mirror in this same function for lease state,
        # so the pre-M2 identity line `record = self.client_watch_service.event_watcher_record`
        # survives verbatim and would satisfy the evidence check even after an identity revert.
        # Pin the discriminator instead -- what the row PUBLISHES. pid and started_at come from
        # the record-derived `identity`, and the bridge PID appears only as the field that
        # reports a disagreement, never as the row's own pid.
        source = _source(owners.status_owner)
        assert '"pid": identity.pid,' in source, source
        assert '"started_at": identity.started_at,' in source, source
        assert '"pid": bridge_pid,' not in source, source
        assert '"started_at": bridge_pid' not in source, source
        return source
    return _source(owners.status_owner)


def test_statsd_is_the_only_service_off_the_registry_record_identity_owner():
    """M2 resolved watchd's identity divergence; statsd's is the one that remains.

    Four services read `LocalServiceRegistry.status()`, which returns the persisted process
    record alongside a live `status` RPC. watchd is demand-scoped, so a diagnostics RPC would
    start it -- it now reads the SAME persisted record through `registry_process_identity`,
    which is one file read plus a /proc read and issues no RPC at all. statsd is the only
    service still identified by a live RPC with no persisted record behind it.
    """
    divergent = frozenset(
        name
        for name, owners in CATALOG.items()
        if owners.identity_owner not in REGISTRY_RECORD_IDENTITY_OWNERS
    )
    assert divergent == {"statsd"}, divergent
    no_rpc = frozenset(
        name for name, owners in CATALOG.items() if owners.identity_owner == REGISTRY_RECORD_IDENTITY
    )
    assert no_rpc == {"watchd"}, no_rpc

    # The no-RPC read really is a record read, and really cannot probe or start the service.
    record_reader = _source(REGISTRY_RECORD_IDENTITY)
    assert "record = read_json_file(record_path, None)" in record_reader, record_reader
    called = _called_attributes(REGISTRY_RECORD_IDENTITY)
    assert "status" not in called, sorted(called)
    assert not (called & START_PRIMITIVE_ATTRIBUTES), sorted(called & START_PRIMITIVE_ATTRIBUTES)

    # statsd's identity is the live RPC, and carries no persisted record to fall back on.
    statsd_identity = _source(CATALOG["statsd"].identity_owner)
    assert "response = self.client.status()" in statsd_identity, statsd_identity
    assert "record" not in statsd_identity, statsd_identity

    watchd_source = _source("yolomux_lib.app:TmuxWebtermApp.watchd_runtime_status")
    # The two M2 hardcodes are gone, and the row publishes the record-derived values instead.
    assert '"started_at": 0.0,' not in watchd_source, watchd_source
    assert '"resources": {},' not in watchd_source, watchd_source
    assert '"started_at": identity.started_at,' in watchd_source, watchd_source
    assert '"pid": identity.pid,' in watchd_source, watchd_source
    # Unchanged and load-bearing: the row still issues no RPC of its own.
    assert "without making a status route call watchd" in watchd_source
    # WatchClient.runtime_status exists and is intentionally never reached from production.
    assert callable(_resolve("yolomux_lib.watchd_client:WatchClient.runtime_status"))
    assert _production_call_site_files("watch_client.runtime_status") == frozenset()


def test_a_full_projection_names_no_start_primitive():
    """Nothing on the projection's path may create or lease a process.

    This is the invariant M2's identity move had to satisfy: observing a demand-scoped service
    must not be what starts it. Every declared row and identity owner, plus the collector and
    both composition owners, are checked for a call to either start primitive.
    """
    checked = {COLLECTOR, COMPOSED_PROJECTION, SNAPSHOT_OWNER, ROW_PRODUCER_MAP}
    for owners in CATALOG.values():
        checked.update({owners.status_owner, owners.identity_owner})
    for ref in sorted(checked):
        starters = _called_attributes(ref) & START_PRIMITIVE_ATTRIBUTES
        assert not starters, (ref, sorted(starters))


def test_demand_role_has_one_process_creating_primitive():
    """Every entrypoint that may create a service delegates to the one registry primitive."""
    for service, owners in CATALOG.items():
        assert owners.demand_owner == DEMAND_PRIMITIVE, (service, owners.demand_owner)
        entrypoint_source = _source(owners.demand_entrypoint)
        assert owners.demand_evidence in entrypoint_source, (service, owners.demand_evidence)
    # No entrypoint may create a process any other way: _spawn is reached only from
    # ensure_started, which is the primitive every row above delegates to.
    assert "self._spawn()" in _source(DEMAND_PRIMITIVE)


def test_recovery_role_has_one_primitive_and_a_known_reachability():
    """One retry primitive, three delegating wrappers, one dispatcher, five wired services.

    Re-pointed at M7. The observer gained a recovery path and therefore a call site, and the
    invariant that had to survive is that it did NOT become a second primitive: it declares no
    `def retry`, so it is absent from `RETRY_DEFINITIONS`, and everything that does declare one
    still bottoms out in `LocalServiceRegistry.retry`.

    Re-pointed again at M9, which is the change that made the observer's path REACHABLE.
    `app.py` now declares a `def retry` -- `LocalServiceRecoveryControl`, the dispatcher the
    observer is constructed with -- so `RETRY_DEFINITIONS` gains it, and the delegation is
    asserted per service rather than asserted once in prose: every entrypoint the dispatcher can
    reach is a client wrapper this file already pins, and the dispatcher itself calls no
    registry, starts nothing, and holds no ladder.
    """
    definitions = _retry_definition_files()
    assert definitions == frozenset(RETRY_DEFINITIONS), (definitions, frozenset(RETRY_DEFINITIONS))
    assert "self.registry.retry()" in _source(
        "yolomux_lib.local_services.client:LocalServiceClient.retry"
    )
    assert "self._transport.registry.retry()" in _source(
        "yolomux_lib.stats_current.client:StatsCurrentClient.retry"
    )
    # The dispatcher reaches a service ONLY through the map, and calls nothing else.
    dispatcher = _source(f"{RECOVERY_CONTROL}.retry")
    assert "self._entrypoints().get(str(resource))" in dispatcher, dispatcher
    assert "return bool(entrypoint())" in dispatcher, dispatcher
    # By AST, so the docstring explaining why it does NOT reach a registry cannot answer the
    # question: the dispatcher calls the map and a dict lookup, and nothing else. No
    # `registry.retry`, no `ensure_started`, no second ladder.
    assert _called_attributes(f"{RECOVERY_CONTROL}.retry") == frozenset({"_entrypoints", "get"})
    assert _production_call_site_files(".retry(") == RETRY_CALL_SITE_FILES
    # The second half of the census: a REFERENCE to a retry wrapper is reaching recovery just as
    # much as an invocation is, and M9's map is written as references.
    assert _production_attribute_files(RETRY_ATTRIBUTE_PATTERN) == RETRY_ATTRIBUTE_FILES
    # The observer's call site is the one choke point and nothing else. `_issue_retry` is a
    # three-line module function precisely so this stays countable;
    # `tests/test_backend_health_recovery.py::test_zero_destructive_operations_reach_the_control_object`
    # owns the complementary rule that `retry` is the only attribute named inside it.
    observer_source = (REPO_ROOT / "yolomux_lib" / "backend_health" / "observer.py").read_text(encoding="utf-8")
    call_sites = [
        line.strip()
        for line in observer_source.splitlines()
        if ".retry(" in line and not line.strip().startswith("#")
    ]
    assert call_sites == ["return bool(control.retry(resource))"] * OBSERVER_RETRY_CALL_SITES, call_sites
    # Through the same census that produced `definitions` above, rather than a `"def retry("`
    # substring of the observer's own text: the census matches a real definition at any indent
    # (`^\s*def retry\(`), so a mention inside a docstring or a string constant cannot satisfy
    # it and a definition indented under a class cannot slip past it.
    assert OBSERVER_MODULE not in definitions, "the observer must never become a second primitive"

    for service, owners in CATALOG.items():
        assert owners.recovery_owner == RECOVERY_PRIMITIVE, (service, owners.recovery_owner)
        # Derive the wrapper from the client class rather than trusting the declaration:
        # a service that claims a retry it cannot reach is exactly the drift to catch.
        reachable = getattr(_resolve(owners.client_owner), "retry", None)
        if owners.recovery_client_entrypoint:
            assert reachable is _resolve(owners.recovery_client_entrypoint), (service, reachable)
        else:
            # indexd is the one service with no client-level retry wrapper at all: its client
            # is not a LocalServiceClient, so recovery is only reachable through the registry.
            assert service == "indexd", service
            assert reachable is None, (service, reachable)
    # M9: `{"statsd"}` became every service that has a client wrapper to call. It is DERIVED
    # from the two independent facts that make it true -- the service declares a reachable
    # wrapper, and the app's recovery map names it -- so a service cannot claim it is wired
    # while nothing can reach it, and cannot be silently wired without saying so here.
    wired = frozenset(name for name, owners in CATALOG.items() if owners.recovery_wired_today)
    assert wired == frozenset(RECOVERY_ENTRYPOINT_EXPRESSIONS), wired
    assert wired == frozenset(name for name, owners in CATALOG.items() if owners.recovery_client_entrypoint)
    assert wired == frozenset(CATALOG) - {"indexd"}, wired


def test_the_app_recovery_map_is_the_one_owner_the_observer_is_wired_to():
    """M9: the map that turned `retry_blocked_no_control` into an issued retry.

    Three separate claims, each of which was false on a live server before M9:

    1. The app declares ONE recovery map, in the same shape as the row-producer map, and every
       expression in it is that service's own client `retry` wrapper -- checked by identity
       against the catalog's `recovery_client_entrypoint`, not by reading the same string twice.
    2. The control the observer is handed exposes `retry` and NOTHING else, so a
       stop/restart/signal/unlink/reclaim/adopt is not reachable from the recovery path even by
       an attribute typo.
    3. `cli.start_backend_health_observer` actually passes it. This is the whole defect M9 fixed:
       the planner was finished, tested and correct, and no production caller supplied a control,
       so every verified-down service published `retry_blocked_no_control` forever.
    """
    entrypoints = _service_id_expressions(
        _source(RECOVERY_ENTRYPOINT_MAP), tuple(CATALOG), "recovery entrypoints"
    )
    assert entrypoints == RECOVERY_ENTRYPOINT_EXPRESSIONS, entrypoints
    # Order follows the inventory, minus the one service with no wrapper.
    assert tuple(entrypoints) == tuple(name for name in CATALOG if name != "indexd"), tuple(entrypoints)
    for service, expression in entrypoints.items():
        client_attribute, _, method = expression.rpartition(".")
        assert method == "retry", (service, expression)
        # The mapped callable IS the declared wrapper, resolved through the live client class.
        declared = _resolve(CATALOG[service].recovery_client_entrypoint)
        assert getattr(_resolve(CATALOG[service].client_owner), "retry") is declared, service
        # And it is reached off a client attribute of the app, never off a registry.
        assert client_attribute.startswith("self."), (service, expression)
        assert "registry" not in client_attribute, (service, expression)

    control = _resolve(RECOVERY_CONTROL)
    public = frozenset(name for name in vars(control) if not name.startswith("_"))
    assert public == RECOVERY_CONTROL_PUBLIC_SURFACE, sorted(public)
    assert _source(RECOVERY_CONTROL_OWNER).count("LocalServiceRecoveryControl(") == 1
    assert "self.local_services_recovery_entrypoints" in _source(RECOVERY_CONTROL_OWNER)

    cli_source = (REPO_ROOT / "yolomux_lib" / "cli.py").read_text(encoding="utf-8")
    wiring = "recovery_control=app.local_services_recovery_control(),"
    assert cli_source.count(wiring) == 1, cli_source.count(wiring)
    # In the observer construction, not somewhere else in the module that never runs.
    observer_call = _source("yolomux_lib.cli:start_backend_health_observer")
    assert wiring in observer_call, observer_call
    # The one production place a control is built. A second builder is a second ladder.
    assert _production_call_site_files("LocalServiceRecoveryControl(") == frozenset({"yolomux_lib/app.py"})


def _retry_definition_files() -> frozenset[str]:
    return frozenset(
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "yolomux_lib").rglob("*.py"))
        if re.search(r"^\s*def retry\(", path.read_text(encoding="utf-8"), re.MULTILINE)
    )


def _production_attribute_files(pattern: str) -> frozenset[str]:
    """Production files whose source NAMES the attribute `pattern` matches, called or not.

    The companion to `_production_call_site_files`, and the reason it exists is measured, not
    theoretical: the invocation needle `.retry(` cannot see `"jobd": self.job_client.retry,`,
    so a census built only on invocations would have reported the module that owns the whole
    recovery map as recovery-free. A regex rather than a substring so `\\b` can keep
    `.retry_at` / `.retry_band` / `.retry_seconds` out without an exclusion list.
    """
    needle = re.compile(pattern)
    hits: set[str] = set()
    for path in sorted((REPO_ROOT / "yolomux_lib").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if needle.search(stripped) and not stripped.startswith("def ") and not stripped.startswith("#"):
                hits.add(str(path.relative_to(REPO_ROOT)))
    return frozenset(hits)


def _production_call_site_files(needle: str) -> frozenset[str]:
    """Production files (yolomux_lib only) containing `needle` outside its own definition."""
    hits: set[str] = set()
    for path in sorted((REPO_ROOT / "yolomux_lib").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if needle in stripped and not stripped.startswith("def ") and not stripped.startswith("#"):
                hits.add(str(path.relative_to(REPO_ROOT)))
    return frozenset(hits)


# --------------------------------------------------------------------------------------
# Demand versus health, and the two shims
# --------------------------------------------------------------------------------------


def test_demand_started_is_declared_by_exactly_the_four_services_nothing_keeps_hot():
    """The Health Contract gap, closed per service rather than by flagging all six.

    This test used to freeze `declared == {"indexd", "watchd"}` and assert, in a comment, that
    "all six are demand-started in fact: nothing keeps them running but ensure_started". That
    comment was wrong, and the four remaining rows were NOT one uniform gap:

      statsd   is lazily created but a background loop keeps it hot. `StatsCurrentRuntime`
               holds a statsd lease for as long as this process is the elected background owner
               (`stats_current/runtime.py:365-368`) and the scheduler appends over RPC at the
               `cpu` family's 1s cadence (`families.py:130-134`). Flagging it `demand_started`
               would have turned a real statsd outage into silence.
      jobd     is pinned by the scheduler lease `start_for_scheduler()` takes on background
               ownership (`infra/jobd.py:1377-1385`, called at `app.py:2962`); the broker's
               idle rule refuses to retire while any lease is held (`infra/jobd.py:1330-1337`).
               Same verdict as statsd, different keeper.
      statusd  is demand-scoped. The only thing that pins it is the SSE generation lease
               (`app.py:7098-7145`), released with the last subscriber; the 60s idle-cadence
               `agent_status` collector cannot hold up a service whose own idle timeout is 60s.
      approvald is demand-scoped. Only `start_worker` creates it, and its idle rule
               (`approval/approvald.py:252`) retires it when it holds no worker record.

    So the flag lands on four services, and jobd's legitimate absence is expressed by a
    different, typed field instead -- pinned by the next test.
    """
    declared = frozenset(name for name, owners in CATALOG.items() if owners.demand_started_declared)
    assert declared == {"indexd", "watchd", "statusd", "approvald"}, declared
    for service, owners in CATALOG.items():
        status_source = _source(owners.status_owner)
        expected = 1 if owners.demand_started_declared else 0
        assert status_source.count('"demand_started"') == expected, (service, status_source)
    # The two services a background loop keeps hot stay outside the flag, permanently.
    kept_hot = frozenset(name for name, owners in CATALOG.items() if not owners.demand_started_declared)
    assert kept_hot == {"statsd", "jobd"}, kept_hot
    assert "demand_started = row.get(\"demand_started\") is True" in _source(SYSTEM_STATUS_SERVICE)


def _deciding_callable(ref: str) -> object:
    """Resolve an `absence_expected_owner` to the callable that actually decides the excuse.

    Two Python shapes are accepted, and the shape is NOT the invariant. jobd's decider is a
    `@property` on `JobClient` because the client already holds the lease id and answering costs
    an attribute read; statsd's is a module-level function because `statsd_runtime_status`
    already performed the ONE `StatsCurrentRuntime.status()` read the decision needs and a
    property would issue a second status RPC on every probe. This test used to hardcode
    `.fget`, which would have forced production into the more expensive shape to satisfy a test.
    """

    target = _resolve(ref)
    if isinstance(target, property):
        return target.fget
    return target


def test_every_typed_expected_absence_is_declared_once_and_has_a_named_owner():
    """The second, DYNAMIC excuse: one per service that can state it, with the owner deciding it.

    `absence_expected_reason` is not a second copy of `demand_started`. It says "this service is
    kept hot by a named owner in this process and that owner is not engaged right now", which is
    a fact about the process rather than about the service. Two services can say it today, and
    for each one the symbol whose state decides it is resolved and the constant that spells the
    token is read out of production source, so neither claim can become free-floating prose.

    Re-pointed at M7. jobd's `scheduler_not_owned` is a STEADY-STATE fact about a lost election.
    statsd's `stats_pin_pending` is a BOUNDED in-flight window: this process is on its way to
    taking the statsd pin and has not taken it yet, which is why the token has to be withdrawn by
    the owner's own live status rather than by a timer. The freeze below is deliberately exact --
    a third declarer, or either token changing, must fail here and be reviewed, because every one
    of these is a path by which a real outage becomes silent.
    """
    declared = {name: owners.absence_expected_reason for name, owners in CATALOG.items() if owners.absence_expected_reason}
    assert declared == {"statsd": "stats_pin_pending", "jobd": "scheduler_not_owned"}, declared
    for service, owners in CATALOG.items():
        status_source = _source(owners.status_owner)
        expected = 1 if owners.absence_expected_reason else 0
        assert status_source.count(f'"{ABSENCE_EXPECTED_REASON_FIELD}"') == expected, (service, status_source)
        if not owners.absence_expected_reason:
            assert owners.absence_expected_owner == "", service
            assert owners.absence_expected_constant == "", service
            continue
        # The deciding symbol resolves to a callable, whatever Python shape it is written in.
        assert callable(_deciding_callable(owners.absence_expected_owner)), owners.absence_expected_owner
        # And it is actually CONSULTED by the row producer. Without this the owner reference is
        # prose: a row could hardcode the token while the named decider is never called.
        qualname = owners.absence_expected_owner.partition(":")[2]
        decider = qualname.rpartition(".")[2] or qualname
        assert decider in status_source, (service, decider, status_source)
        # The declared token is the one the production constant spells, read from source rather
        # than retyped, and the row producer reaches the token through that constant.
        module_name = owners.absence_expected_owner.partition(":")[0]
        constant = _literal_assignment(_module_source(module_name)[1], owners.absence_expected_constant)
        assert constant == owners.absence_expected_reason, (service, constant, owners.absence_expected_reason)
        assert owners.absence_expected_constant in status_source, (service, status_source)
        # A row that spelled the token as a literal beside the constant would be a divergent copy
        # of the one value; the constant is the only way the string may reach the row.
        assert f'"{owners.absence_expected_reason}"' not in status_source, (service, status_source)


def test_no_service_declares_both_absence_excuses():
    """One absence, one excuse. A row claiming both is the divergent-copy defect, not a state."""
    both = sorted(
        name
        for name, owners in CATALOG.items()
        if owners.demand_started_declared and owners.absence_expected_reason
    )
    assert both == [], both
    # And the rule is enforced on the reducer, not only in this matrix: see
    # tests/test_backend_health_observer.py::test_a_row_claiming_both_absence_excuses_is_refused.
    for service, owners in CATALOG.items():
        status_source = _source(owners.status_owner)
        assert not (
            '"demand_started"' in status_source and '"absence_expected_reason"' in status_source
        ), (service, status_source)


def test_the_two_shimmed_services_launch_through_their_shim():
    """jobd and approvald spawn `python -m <shim>`, so the shim is load-bearing, not dead code."""
    for service in ("jobd", "approvald"):
        owners = CATALOG[service]
        assert owners.spec_module != owners.implementation_module, service
        shim = importlib.import_module(owners.spec_module)
        implementation = importlib.import_module(owners.implementation_module)
        assert shim is implementation, (service, shim, implementation)
    for service in ("indexd", "statsd", "statusd", "watchd"):
        owners = CATALOG[service]
        assert owners.spec_module == owners.implementation_module, service


def test_spawned_module_string_matches_the_client_spec():
    """The spec module in each client is the string registry._spawn passes to `python -m`."""
    spec_sources = {
        "indexd": "yolomux_lib.search.search_indexer:SearchIndexerClient.__init__",
        "jobd": "yolomux_lib.infra.jobd:JobClient.__init__",
        "statusd": "yolomux_lib.statusd_client:StatusClient.__init__",
        "watchd": "yolomux_lib.watchd_client:WatchClient.__init__",
        "approvald": "yolomux_lib.approval.approvald:ApprovalClient.__init__",
    }
    for service, ref in spec_sources.items():
        assert f'"{CATALOG[service].spec_module}"' in _source(ref), (service, ref)
    stats_client = importlib.import_module("yolomux_lib.stats_current.client")
    assert stats_client.SERVICE_MODULE == CATALOG["statsd"].spec_module
    assert stats_client.SERVICE_NAME == "statsd"
    assert "sys.executable" in _source(
        "yolomux_lib.local_services.registry:LocalServiceRegistry._spawn"
    )


# --------------------------------------------------------------------------------------
# Abandoned topology
# --------------------------------------------------------------------------------------


def test_no_abandoned_topology_name_is_a_service_row_or_an_owner():
    """No name from abandoned/0.6.12 may return as an id, a spawned module, or an owner."""
    surfaces: list[str] = list(_inventory_literal()) + list(CATALOG)
    for owners in CATALOG.values():
        surfaces.extend([
            owners.spec_module,
            owners.implementation_module,
            owners.client_owner,
            owners.demand_owner,
            owners.demand_entrypoint,
            owners.status_owner,
            owners.identity_owner,
            owners.metrics_owner,
            owners.recovery_owner,
            owners.recovery_client_entrypoint,
            # M4 and M7 added two more owner-shaped surfaces. They were NOT covered here when
            # they landed, which is exactly how an abandoned name comes back: through the
            # newest field, not the ones the net was written for.
            owners.absence_expected_owner,
            owners.absence_expected_constant,
        ])
    assert _abandoned_names_in(surfaces) == [], _abandoned_names_in(surfaces)
    for module in ABANDONED_MODULES:
        # Not `find_spec(module) is None`: an upgraded checkout leaves an importable namespace-only
        # residue (empty `yolomux_lib/daemon/`, no `__init__.py`) that returns a spec but no code.
        # A genuinely-present abandoned module (a real origin) is still a violation; the residue is
        # tolerated. See `_abandoned_module_is_truly_present` and the W15 regression below.
        assert not _abandoned_module_is_truly_present(module), module


def test_an_abandoned_name_in_an_inventory_is_rejected():
    """Permanent negative control for the abandoned-name rule."""
    poisoned = ["indexd", "statsd", "storaged", "jobd"]
    assert _abandoned_names_in(poisoned) == ["storaged"], _abandoned_names_in(poisoned)
    assert _abandoned_names_in(["yolomux_lib.daemon.supervisor"]) == ["daemon"]
    # Substrings of real identifiers are not hits: this rule must not fire on `storaged.products`
    # style keys or on `jobd`.
    assert _abandoned_names_in(["storagedaemonish", "jobd", "search_indexer"]) == []


@contextlib.contextmanager
def _materialized_namespace_residue(module: str):
    """Yield after making `module` resolve as a namespace-only residue, then clean up.

    Reproduces the 0.7.2->0.7.3 upgrade residue deterministically and without touching the source
    tree: create a temporary `<parent>/` directory holding an empty `daemon/` (no `__init__.py`),
    add it to the real parent package's search path, and invalidate the import caches. On exit the
    residue is removed and caches invalidated again -- no manual cache deletion, no sleeps.
    """

    package_name, _, leaf = module.rpartition(".")
    parent = importlib.import_module(package_name)
    residue_root = Path(tempfile.mkdtemp(prefix="w15-residue-"))
    (residue_root / leaf).mkdir()
    parent.__path__.append(str(residue_root))
    importlib.invalidate_caches()
    try:
        yield residue_root / leaf
    finally:
        parent.__path__.remove(str(residue_root))
        importlib.invalidate_caches()
        shutil.rmtree(residue_root)


def test_namespace_package_residue_is_tolerated_but_a_real_module_is_not():
    """W15: the abandoned-module oracle survives the upgrade residue, without weakening.

    (a) Clean checkout: none of the abandoned modules resolve to real code, so the oracle holds.
    (b) Upgraded/dirty checkout: an empty `yolomux_lib/daemon/` namespace residue is importable
        (`find_spec` returns a spec), which is exactly what falsely failed the bare
        `find_spec is None` oracle. The fixed oracle tolerates it, but a REAL
        `yolomux_lib/daemon/__init__.py` would still be a violation.
    """

    module = "yolomux_lib.daemon"
    assert module in ABANDONED_MODULES

    # (a) Clean checkout: no residue on disk, no abandoned module is truly present.
    for abandoned in ABANDONED_MODULES:
        assert not _abandoned_module_is_truly_present(abandoned), abandoned
    # The whole oracle holds in the clean state.
    test_no_abandoned_topology_name_is_a_service_row_or_an_owner()

    # (b) Upgraded/dirty checkout: materialize the namespace residue.
    with _materialized_namespace_residue(module) as residue_dir:
        # The residue is importable -- this is precisely what broke the old `find_spec is None`.
        spec = importlib.util.find_spec(module)
        assert spec is not None, "residue should resolve as a namespace package"
        assert spec.origin is None, spec.origin
        # ...but it is not truly present, so the fixed oracle still holds.
        assert not _abandoned_module_is_truly_present(module), module
        test_no_abandoned_topology_name_is_a_service_row_or_an_owner()

        # A REAL module at the same name (an ordinary origin with actual code) WOULD fail.
        (residue_dir / "__init__.py").write_text("SUPERVISOR = object()\n", encoding="utf-8")
        importlib.invalidate_caches()
        real_spec = importlib.util.find_spec(module)
        assert real_spec is not None and real_spec.origin is not None, real_spec
        assert _abandoned_module_is_truly_present(module), module
        with pytest.raises(AssertionError):
            test_no_abandoned_topology_name_is_a_service_row_or_an_owner()

    # Teardown restored the clean state: the oracle holds again.
    for abandoned in ABANDONED_MODULES:
        assert not _abandoned_module_is_truly_present(abandoned), abandoned
