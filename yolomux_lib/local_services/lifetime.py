# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The one owner of every destructive local-service lifetime decision.

Four separate places used to hold the authority to SIGKILL a YOLOmux daemon, and
each fired on a different trigger: the next launcher start, a launcher exit,
boot preflight, and the CPU watchdog.  Four owners is four vocabularies, four
sets of dimensions, and four chances for one of them to act on an identity the
others would have refused.  "The next start will clean it up" is the worst of
them: it makes a *future* event the authority for a decision that has to be made
now, so a survivor of a launcher that never comes back is never resolved at all.

This module owns two halves of the same contract and nothing else:

- **Authorization** (:func:`authorize_service_destruction`) binds a destructive
  decision to stable host + boot identity, the exact PID start identity, the
  namespace the record lives in, the kind, the spawn generation or the process
  group, and the live claim.  A dimension that is missing, unreadable, or
  changed produces ZERO signals and ZERO unlinks and exactly one typed
  diagnostic naming which dimension failed.  There is no "probably ours" branch.
- **Execution** (:func:`terminate_authorized_processes`, and its one-target
  wrapper :func:`terminate_authorized_process`) performs the bounded escalation
  once authorization succeeded: SIGTERM to every graceful target, ONE bounded
  wait re-proving the SAME identities, then SIGKILL against those same proven
  identities, then a bounded wait again.  Every field it returns -- the action,
  the result, the reason, the elapsed age -- comes from what actually ran.
  Nothing is a literal, because a literal cannot report a failure.

Two things make that one owner usable by callers that previously could not
reach it, and both are the reason the fourth and fifth private escalation loops
are gone:

- **Two scopes, not one waiver.**  ``SCOPE_LOCAL_SERVICE`` is an addressable
  daemon whose record carries its own kind and generation.
  ``SCOPE_TRACKED_PROCESS_GROUP`` is a member of a web server's or a service
  leader's process group -- the boot preflight's stale orphans, the CPU
  watchdog's targets, a pool child.  Such a target structurally has no service
  kind of its own and no spawn generation, so demanding one would refuse every
  decision about it forever, which is exactly why those paths grew their own
  loops.  The group scope does not waive the generation dimension; it
  substitutes the one that class of target genuinely has -- the process group it
  shares with a leader proven from a persisted record -- and REQUIRES it,
  re-read live before any signal, refusing when it is absent, unreadable, or
  changed.
- **Three dispositions, not two.**  ``authorized`` and ``refused`` cannot
  express a record published before spawn generations existed: demanding a
  generation makes such a daemon permanently unretirable (retiring it is what
  would give it one), and waiving the demand signals a process on a proof nobody
  wrote.  ``retained`` is the third answer -- one typed row naming the absent
  dimension, no signal, no unlink, and no retry that would change it.

The liveness fence is deliberately injected rather than imported: the
zombie-aware fence lives with the process-table reader in
:mod:`yolomux_lib.local_services.registry`, and importing it here would make the
one owner depend on one of its callers.  Callers pass the same fence they use
for every other decision, so there is still exactly one fence.

The service-side half (:class:`ServiceLifetimeOwner`) answers the reciprocal
question inside the daemon: when the last valid external claim disappears, a
graceful ``stop_event`` is a *request*, not an outcome.  A daemon whose accept
loop, shutdown hook, or a stuck handler thread does not complete stays up
forever, and the only thing that used to force it was a future launcher start --
the authority this module exists to remove.  So the daemon escalates on itself,
on its own bounded timer, against its own exact PID.
"""

from __future__ import annotations

import json
import os
import signal as signal_module
import threading
import time
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from ..atomic_file import atomic_write_text
from ..host_identity import HostIdentity
from ..host_identity import LocalProcessDiagnostic
from ..host_identity import LocalProcessReason
from ..host_identity import current_host_identity
from ..host_identity import is_current_local_process
from ..host_identity import process_start_identity
from ..infra.process_claims import CLAIM_REASON_MISSING_SUPERVISOR_RECORD
from ..infra.process_claims import CLAIM_REASON_SUPERVISOR_ALIVE
from ..infra.process_claims import CLAIM_ROOT_CALLER_SHARED
from ..infra.process_claims import CLAIM_ROOT_MANAGED_PRIVATE
from ..infra.process_claims import ProcessClaimLedger


# Every dimension a destructive decision is bound to.  Named so a refusal can
# say which one failed instead of collapsing six different gaps into "refused".
DIMENSION_HOST_AND_BOOT = "host_and_boot"
DIMENSION_PROCESS_START_IDENTITY = "process_start_identity"
DIMENSION_NAMESPACE = "namespace"
DIMENSION_SERVICE_KIND = "service_kind"
DIMENSION_GENERATION = "spawn_generation"
DIMENSION_PROCESS_GROUP = "process_group"
DIMENSION_CLAIM = "claim"
# Who else still owns this target.  A record naming a supervisor that is still a
# live local process is that supervisor's to stop; anyone else signalling it is
# the exact cross-owner accident the claim ledger's `surviving_supervisor` field
# was introduced to make impossible.  Deliberately the SAME spelling as
# `infra.process_claims`, because two names for "who is keeping this alive" is
# how the two ledgers would drift apart.
DIMENSION_SURVIVING_SUPERVISOR = "surviving_supervisor"
DESTRUCTIVE_DIMENSIONS = (
    DIMENSION_HOST_AND_BOOT,
    DIMENSION_PROCESS_START_IDENTITY,
    DIMENSION_NAMESPACE,
    DIMENSION_SERVICE_KIND,
    DIMENSION_GENERATION,
    DIMENSION_CLAIM,
)

# The class of target one destructive decision is about.
#
# A LOCAL SERVICE is independently addressable: it owns a socket, a persisted
# record, and a spawn generation stamped into its own environment, so every
# dimension above is one the record itself carries.
#
# A TRACKED PROCESS GROUP MEMBER is not.  The boot preflight's stale orphans and
# the CPU watchdog's targets are members of a WEB SERVER's process group; a pool
# child of a service leader is the same shape.  Such a target holds no socket,
# no record of its own, and no spawn generation, so demanding one would refuse
# every decision about it forever -- which is how those two paths came to hold
# their OWN TERM/grace/KILL loops instead of routing through this owner.  What
# such a target DOES provably carry is the process group it shares with a leader
# whose identity was proven from a persisted record, and that group is re-read
# live before any signal.  Naming the two scopes is what lets the group scope
# supply a real substitute dimension instead of quietly waiving the one it
# cannot carry: `spawn_generation` is reported as structurally not applicable
# and `process_group` becomes REQUIRED in its place, missing or unreadable or
# changed all producing zero signals.
SCOPE_LOCAL_SERVICE = "local_service"
SCOPE_TRACKED_PROCESS_GROUP = "tracked_process_group"
DESTRUCTIVE_SCOPES = (SCOPE_LOCAL_SERVICE, SCOPE_TRACKED_PROCESS_GROUP)
GROUP_DESTRUCTIVE_DIMENSIONS = (
    DIMENSION_HOST_AND_BOOT,
    DIMENSION_PROCESS_START_IDENTITY,
    DIMENSION_NAMESPACE,
    DIMENSION_SERVICE_KIND,
    DIMENSION_PROCESS_GROUP,
    DIMENSION_SURVIVING_SUPERVISOR,
    DIMENSION_CLAIM,
)

# What a decision resolved to.  Three states, not two: a record that carries no
# spawn generation at all is neither authorized nor a failure to be retried, it
# is a target this build may never signal, and saying so with its own name is
# what stops a caller from reading it as a transient refusal.
LIFETIME_DISPOSITION_AUTHORIZED = "authorized"
LIFETIME_DISPOSITION_REFUSED = "refused"
LIFETIME_DISPOSITION_RETAINED = "retained"

# What the owner actually attempted.
LIFETIME_ACTION_NONE = "none"
LIFETIME_ACTION_TERMINATE = "terminate"
LIFETIME_ACTION_FORCE_TERMINATE = "force_terminate"

# What that attempt achieved.  `refused` may only ever describe a decision that
# stopped before any signal; it can never describe an attempt.
LIFETIME_RESULT_REFUSED = "refused"
LIFETIME_RESULT_RETAINED = "retained"
LIFETIME_RESULT_ALREADY_EXITED = "already_exited"
LIFETIME_RESULT_SIGNAL_REFUSED = "signal_refused"
LIFETIME_RESULT_CONFIRMED_EXITED = "confirmed_exited"
LIFETIME_RESULT_FORCE_CONFIRMED_EXITED = "force_confirmed_exited"
LIFETIME_RESULT_STILL_ALIVE = "still_alive"
LIFETIME_RESULT_SELF_SIGNALLED = "self_signalled"

# Why.  Identity reasons are carried through from `LocalProcessReason` unchanged
# so this module never mints a second vocabulary for the same decision.
LIFETIME_REASON_AUTHORIZED = "authorized"
LIFETIME_REASON_DIMENSION_MISSING = "dimension_missing"
LIFETIME_REASON_DIMENSION_CHANGED = "dimension_changed"
LIFETIME_REASON_GENERATION_ABSENT_RETAINED = "generation_absent_retained"
LIFETIME_REASON_SCOPE_UNKNOWN = "destructive_scope_unknown"

# The spawn generation a group-scoped record structurally cannot carry.  Written
# into the reported dimensions so a reader can tell "this class of target never
# has one, and the process group was proven instead" from "this one was supposed
# to have one and it was missing", which is a refusal.
GENERATION_NOT_APPLICABLE_GROUP_SCOPED = "not_applicable_group_scoped"
GENERATION_ABSENT_IN_RECORD = "absent_in_record"

# ONE escalation budget for a group-scoped containment: boot preflight's stale
# orphans and the CPU watchdog's tracked group.  Both used to spell their own
# (3.0s in the watchdog, 2.0s in preflight, with no force budget at all in
# either), which is how one algorithm came to run on four different clocks.
# Deliberately longer than the local-service retirement grace below: a web
# server runs user teardown -- tmux control clients, sessions, open files --
# where a daemon only has to close a socket.
GROUP_TERMINATION_GRACE_SECONDS = 3.0
GROUP_TERMINATION_FORCE_SECONDS = 2.0

# The supervisor-side budget for retiring one addressable local service.  Owned
# here beside the escalation that spends it; `registry` re-exports both names so
# existing callers and the launch-timing tests keep one definition rather than a
# second literal.
LOCAL_SERVICE_RETIRE_GRACE_SECONDS = 0.5
# A retired generation that ignores SIGTERM (wedged, not merely slow) must still
# be force-terminated rather than left running forever under a caller-shared
# root -- the same escalation contract the multi-service teardown path uses.
LOCAL_SERVICE_RETIRE_FORCE_SECONDS = 2.0

# Service-side retirement states.  `retained_by_supervisor` and `orphaned` both
# occur in normal operation, which is why the surviving-supervisor field varies.
RETIREMENT_STATE_SERVING = "serving"
RETIREMENT_STATE_REQUESTED = "graceful_stop_requested"
RETIREMENT_STATE_TERMINATED = "self_terminated"
RETIREMENT_STATE_FORCED = "self_force_terminated"
RETIREMENT_STATE_EXITED = "exited_gracefully"

SUPERVISOR_STATE_RETAINED = "retained_by_supervisor"
SUPERVISOR_STATE_ORPHANED = "orphaned"
SUPERVISOR_STATE_UNPROVEN = "supervisor_identity_unproven"

# The daemon's own escalation budget.  Deliberately longer than the supervisor's
# 0.5s observation grace: the daemon is the LAST resort, so it must not race the
# supervisor into double-signalling a process that was already exiting cleanly.
# Set by `LocalServiceRegistry._spawn` in every daemon it launches. Its presence
# in this process's own environment is the only proof a process has that it IS a
# standalone local service rather than a daemon object hosted inside a caller.
LOCAL_SERVICE_SPAWN_GENERATION_ENV = "YOLOMUX_LOCAL_SERVICE_SPAWN_GENERATION"

SERVICE_SELF_RETIRE_GRACE_SECONDS = 3.0
SERVICE_SELF_RETIRE_FORCE_SECONDS = 2.0


def service_claim_kind(service_name: str) -> str:
    """Return the one claim kind string for a local service, with no second speller."""

    return f"local-service:{str(service_name or '').strip()}"


def root_sharing_mode(*, private_root: bool) -> str:
    """Map a resolved product root onto the claim matrix's sharing mode.

    A ``YOLOMUX_ROOT`` run owns every path it uses, so exactly one launcher can
    ever be a candidate supervisor for its daemons and there is no successor to
    elect.  The default per-user runtime directory is shared by every YOLOmux
    server that user runs, so a survivor there may legitimately be inherited.
    """

    return CLAIM_ROOT_MANAGED_PRIVATE if private_root else CLAIM_ROOT_CALLER_SHARED


def service_claim_ledger(
    root: Path,
    service_name: str,
    *,
    private_root: bool,
    host_identity: HostIdentity | None = None,
) -> ProcessClaimLedger:
    """Return the shared claim ledger that owns one service kind's reap authority.

    Reuses :class:`ProcessClaimLedger` rather than adding a second ledger: the
    claim shape, the fail-closed fence, and the adoption transaction are the same
    problem the tmux control-client path already solved.
    """

    return ProcessClaimLedger(
        Path(root),
        service_claim_kind(service_name),
        host_identity=host_identity,
        root_sharing=root_sharing_mode(private_root=private_root),
    )


@dataclass(frozen=True)
class ServiceDestructionAuthorization:
    """One fail-closed answer to 'may this exact process be signalled?'.

    ``disposition`` is the stored answer and ``authorized`` is derived from it,
    because two independently settable fields for one decision is exactly the
    divergent pair that lets a refusal be reported as an authorization.
    """

    disposition: str
    pid: int
    reason: str
    failed_dimension: str
    dimensions: dict[str, Any]
    scope: str = SCOPE_LOCAL_SERVICE

    @property
    def authorized(self) -> bool:
        return self.disposition == LIFETIME_DISPOSITION_AUTHORIZED

    @property
    def retained(self) -> bool:
        """Whether this is the typed NON-destructive outcome: no signal, no unlink."""

        return self.disposition == LIFETIME_DISPOSITION_RETAINED

    @property
    def surviving_supervisor(self) -> dict[str, Any]:
        """The proven identity of the owner that keeps this target, or ``{}``.

        Read back out of the dimensions rather than stored a second time: a
        decision and the identity it was made on must be one value, or a row can
        name a supervisor the decision never actually proved.
        """

        value = self.dimensions.get(DIMENSION_SURVIVING_SUPERVISOR)
        return dict(value) if isinstance(value, dict) else {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "authorized": self.authorized,
            "disposition": self.disposition,
            "scope": self.scope,
            "pid": self.pid,
            "reason": self.reason,
            "failed_dimension": self.failed_dimension,
            "dimensions": dict(self.dimensions),
        }


def _refused(
    pid: int,
    dimension: str,
    reason: str,
    dimensions: dict[str, Any],
    scope: str = SCOPE_LOCAL_SERVICE,
) -> ServiceDestructionAuthorization:
    return ServiceDestructionAuthorization(
        disposition=LIFETIME_DISPOSITION_REFUSED,
        pid=pid,
        reason=reason,
        failed_dimension=dimension,
        dimensions=dimensions,
        scope=scope,
    )


def _retained(
    pid: int,
    dimension: str,
    reason: str,
    dimensions: dict[str, Any],
    scope: str = SCOPE_LOCAL_SERVICE,
) -> ServiceDestructionAuthorization:
    """The third outcome: a target this build may never signal, named as such.

    A record published before spawn generations existed carries none, and
    demanding one would make such a daemon permanently unretirable -- the
    deadlock being that retiring it is what would give it a generation.  The
    answer is neither to waive the dimension (which is how a survivor of an
    unknown generation used to be signalled on a proof nobody wrote) nor to
    report a transient refusal a caller will retry forever.  It is one typed row
    saying which dimension is structurally absent, with no signal and no unlink
    behind it.

    The surviving-supervisor gate resolves here for the same reason: a target
    whose owner is still alive, or whose owner's death was never proven, is not a
    decision that will succeed on the next pass -- it is one this caller may not
    make at all, and the row names the owner instead of staying silent.
    """

    return ServiceDestructionAuthorization(
        disposition=LIFETIME_DISPOSITION_RETAINED,
        pid=pid,
        reason=reason,
        failed_dimension=dimension,
        dimensions=dimensions,
        scope=scope,
    )


def authorize_service_destruction(
    record: Mapping[str, Any],
    *,
    diagnostic: LocalProcessDiagnostic,
    expected_kind: str,
    expected_namespace: str,
    live_generation_reader: Callable[[int], str | None],
    claim_state: str = "",
    require_claim: bool = False,
    scope: str = SCOPE_LOCAL_SERVICE,
    expected_process_group: int = 0,
    live_process_group_reader: Callable[[int], int | None] | None = None,
    require_supervisor_gone: bool = False,
    supervisor_diagnostic: LocalProcessDiagnostic | None = None,
) -> ServiceDestructionAuthorization:
    """Bind one destructive decision to every dimension, or refuse and name the gap.

    ``diagnostic`` must come from the caller's ONE liveness fence (the
    zombie-aware ``registry.process_record_diagnostic``), so host, boot, PID, and
    process-start identity are already proven or already refused by the time this
    runs; this function never re-derives them.  What it adds is the dimensions
    that fence did not carry: which directory the record belongs to, which kind
    it names, which spawn generation or process group the live process still
    proves, and whether a live claim backs the decision.

    ``scope`` selects WHICH proof stands in the generation slot.  Under
    ``SCOPE_LOCAL_SERVICE`` a record that carries a generation must re-prove it
    live and a record that carries none is RETAINED -- typed, non-destructive,
    zero signals.  Under ``SCOPE_TRACKED_PROCESS_GROUP`` the target is a member
    of a group whose leader was proven from a record, so the generation is
    reported as structurally not applicable and ``expected_process_group`` is
    REQUIRED and re-read live instead; a group that is missing, unreadable, or
    changed refuses.  A group-scoped record that happens to carry a generation
    still has to re-prove it.

    ``require_claim`` is what a caller-shared root turns on.  A survivor there
    may be another live server's, so a decision without a live claim behind it is
    exactly the ambiguity that must produce zero signals and one diagnostic.

    ``require_supervisor_gone`` is what a caller turns on for a record class that
    names the owner that spawned it.  A target whose recorded supervisor is still
    a live local process is that supervisor's to stop, so the decision is
    RETAINED -- zero signals, zero unlinks, one typed row carrying that
    supervisor's proven identity under ``surviving_supervisor``.  The gate is
    fail-closed in both directions: only ``may_remove_stale_record`` (the same
    property ``registry._supervisor_is_gone`` and the claim ledger already use)
    counts as "provably gone", so a supervisor that is missing, unreadable, or
    merely unprovable is retained rather than read as absent.  Callers pass
    ``supervisor_diagnostic`` from the same fence every other dimension uses; a
    bare ``pid_is_alive`` or a raw PID compare is never authority here.
    """

    pid = int(diagnostic.pid)
    recorded_kind = str(record.get("service") or "")
    recorded_namespace = str(record.get("namespace") or "")
    recorded_generation = str(record.get("spawn_generation") or "")
    dimensions: dict[str, Any] = {
        DIMENSION_HOST_AND_BOOT: diagnostic.reason.value,
        DIMENSION_PROCESS_START_IDENTITY: diagnostic.recorded_start_identity,
        DIMENSION_NAMESPACE: recorded_namespace,
        DIMENSION_SERVICE_KIND: recorded_kind,
        DIMENSION_GENERATION: recorded_generation,
        DIMENSION_CLAIM: str(claim_state or ""),
    }
    if scope not in DESTRUCTIVE_SCOPES:
        # An unknown scope is a programming error at a destructive boundary, and
        # the fail-closed answer to one is zero signals, not a default scope.
        return _refused(pid, DIMENSION_SERVICE_KIND, LIFETIME_REASON_SCOPE_UNKNOWN, dimensions, scope=str(scope))
    if not diagnostic.current:
        # An ambiguous, legacy, stale, reused, foreign, or superseded identity all
        # arrive here, and all of them get the fence's own reason rather than a
        # second name for the same refusal.
        return _refused(pid, DIMENSION_HOST_AND_BOOT, diagnostic.reason.value, dimensions, scope=scope)
    if not recorded_kind:
        return _refused(pid, DIMENSION_SERVICE_KIND, LIFETIME_REASON_DIMENSION_MISSING, dimensions, scope=scope)
    if recorded_kind != str(expected_kind or ""):
        return _refused(pid, DIMENSION_SERVICE_KIND, LIFETIME_REASON_DIMENSION_CHANGED, dimensions, scope=scope)
    if not recorded_namespace:
        return _refused(pid, DIMENSION_NAMESPACE, LIFETIME_REASON_DIMENSION_MISSING, dimensions, scope=scope)
    if recorded_namespace != str(expected_namespace or ""):
        return _refused(pid, DIMENSION_NAMESPACE, LIFETIME_REASON_DIMENSION_CHANGED, dimensions, scope=scope)
    if recorded_generation:
        observed_generation = live_generation_reader(pid)
        dimensions["observed_spawn_generation"] = observed_generation
        if observed_generation is None:
            # The generation marker is inherited environment, so an unreadable one
            # is "the proof did not complete", never "it is a different
            # generation". Unproven is always a refusal.
            return _refused(pid, DIMENSION_GENERATION, LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE.value, dimensions, scope=scope)
        if str(observed_generation) != recorded_generation:
            return _refused(pid, DIMENSION_GENERATION, LIFETIME_REASON_DIMENSION_CHANGED, dimensions, scope=scope)
    elif scope == SCOPE_LOCAL_SERVICE:
        # An addressable service with no recorded generation is the retained
        # case: this build never wrote that proof, so it may not act on it.
        dimensions[DIMENSION_GENERATION] = GENERATION_ABSENT_IN_RECORD
        return _retained(pid, DIMENSION_GENERATION, LIFETIME_REASON_GENERATION_ABSENT_RETAINED, dimensions, scope=scope)
    else:
        dimensions[DIMENSION_GENERATION] = GENERATION_NOT_APPLICABLE_GROUP_SCOPED
    if scope == SCOPE_TRACKED_PROCESS_GROUP:
        recorded_group = _coerced_process_group(record.get("pgid"))
        dimensions[DIMENSION_PROCESS_GROUP] = recorded_group
        if recorded_group <= 0 or int(expected_process_group) <= 0 or live_process_group_reader is None:
            # No group on the record, no group demanded by the caller, or no way
            # to re-read one live: three different ways to have no proof, and all
            # three must produce zero signals rather than a default group.
            return _refused(pid, DIMENSION_PROCESS_GROUP, LIFETIME_REASON_DIMENSION_MISSING, dimensions, scope=scope)
        if recorded_group != int(expected_process_group):
            return _refused(pid, DIMENSION_PROCESS_GROUP, LIFETIME_REASON_DIMENSION_CHANGED, dimensions, scope=scope)
        observed_group = live_process_group_reader(pid)
        dimensions["observed_process_group"] = observed_group
        if observed_group is None:
            return _refused(pid, DIMENSION_PROCESS_GROUP, LocalProcessReason.PROCESS_IDENTITY_UNAVAILABLE.value, dimensions, scope=scope)
        if int(observed_group) != recorded_group:
            # The target left the tracked group between the snapshot the decision
            # was made on and now. It is no longer the thing that was proven.
            return _refused(pid, DIMENSION_PROCESS_GROUP, LIFETIME_REASON_DIMENSION_CHANGED, dimensions, scope=scope)
    if require_supervisor_gone:
        if supervisor_diagnostic is None:
            # No supervisor record on this target at all: a legacy row written
            # before supervisors were stamped, or a record this caller could not
            # read. Neither is proof the owner died, and "I could not find one"
            # must never become "there is none, go ahead".
            dimensions[DIMENSION_SURVIVING_SUPERVISOR] = SUPERVISOR_STATE_UNPROVEN
            return _retained(
                pid,
                DIMENSION_SURVIVING_SUPERVISOR,
                CLAIM_REASON_MISSING_SUPERVISOR_RECORD,
                dimensions,
                scope=scope,
            )
        dimensions[DIMENSION_SURVIVING_SUPERVISOR] = supervisor_diagnostic.as_dict()
        if not supervisor_diagnostic.may_remove_stale_record:
            # Two different survivals, one outcome. `current` is a supervisor
            # that is provably still running and is named by identity in the row;
            # anything else (foreign host, previous boot, unreadable start
            # identity) is a supervisor whose death was never PROVEN, and the
            # fence's own reason is carried through rather than renamed.
            return _retained(
                pid,
                DIMENSION_SURVIVING_SUPERVISOR,
                CLAIM_REASON_SUPERVISOR_ALIVE if supervisor_diagnostic.current else supervisor_diagnostic.reason.value,
                dimensions,
                scope=scope,
            )
    if require_claim and not str(claim_state or ""):
        return _refused(pid, DIMENSION_CLAIM, LIFETIME_REASON_DIMENSION_MISSING, dimensions, scope=scope)
    return ServiceDestructionAuthorization(
        disposition=LIFETIME_DISPOSITION_AUTHORIZED,
        pid=pid,
        reason=LIFETIME_REASON_AUTHORIZED,
        failed_dimension="",
        dimensions=dimensions,
        scope=scope,
    )


def _coerced_process_group(value: object) -> int:
    """Read a recorded process group, treating anything unusable as absent."""

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class TerminationOutcome:
    """What one bounded termination actually did, measured rather than declared."""

    pid: int
    attempted_action: str
    result: str
    reason: str
    age_seconds: float
    confirmed_dead: bool
    signals: tuple[int, ...] = ()
    failed_dimension: str = ""
    error: str = ""
    target: str = ""
    # Present only on a row that was retained BECAUSE someone else still owns the
    # target, so "why was nothing done" is answerable from the row rather than
    # from the absence of a signal.
    surviving_supervisor: dict[str, Any] = field(default_factory=dict)

    @property
    def retained(self) -> bool:
        """Whether this target was deliberately left running, with no signal sent."""

        return self.result == LIFETIME_RESULT_RETAINED

    @property
    def refused(self) -> bool:
        """Whether the owner stopped because authority over this identity was not proven."""

        return self.result == LIFETIME_RESULT_REFUSED

    @property
    def unproven_authority(self) -> bool:
        """Whether this target was left alone -- refused or retained, never escalated.

        The two are different answers ("I could not prove this" versus "this
        build may never signal that") and both are reported by name, but a caller
        deciding whether it still holds authority over the REST of that target's
        group has exactly one question, and this is it.
        """

        return self.retained or self.refused

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "pid": self.pid,
            "attempted_action": self.attempted_action,
            "result": self.result,
            "reason": self.reason,
            "age_seconds": round(self.age_seconds, 6),
            "confirmed_dead": self.confirmed_dead,
            "signals": list(self.signals),
        }
        if self.target:
            row["target"] = self.target
        if self.failed_dimension:
            row["failed_dimension"] = self.failed_dimension
        if self.error:
            row["error"] = self.error
        if self.surviving_supervisor:
            row["surviving_supervisor"] = dict(self.surviving_supervisor)
        return row


@dataclass(frozen=True)
class TerminationRequest:
    """One authorized target inside a single bounded escalation.

    ``target`` is the caller's name for it ("web", "jobd", "tracked-member") and
    is carried straight through onto the outcome, so an incident report is the
    owner's measured rows rather than a second vocabulary mapped onto them.

    ``graceful_first=False`` is for a target whose graceful window has already
    elapsed under another identity -- a pool child of a leader that was just
    terminated, or a tracked group member whose leader already absorbed the
    SIGTERM.  It skips straight to the force step rather than charging the
    teardown a second graceful window the child has already effectively had.
    """

    authorization: ServiceDestructionAuthorization
    target: str = ""
    graceful_first: bool = True


def terminate_authorized_processes(
    requests: Sequence[TerminationRequest],
    *,
    still_current: Callable[[int], bool],
    identity_replaced: Callable[[int], bool] = lambda _pid: False,
    signal_process: Callable[[int, int], None] | None = None,
    grace_seconds: float,
    force_seconds: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval: float = 0.03,
) -> list[TerminationOutcome]:
    """Escalate SIGTERM to SIGKILL across ONE bounded window for N authorized identities.

    This is the primitive; :func:`terminate_authorized_process` is the
    one-element wrapper, so there is exactly one escalation in the product.

    Containing a runaway process GROUP is not N independent terminations run
    back to back: every leader must get its graceful signal before ANY of them
    is force-killed, or the last leader is still burning CPU while the first
    one's grace window is being paid for.  The CPU watchdog and the boot
    preflight each held their own TERM/grace/KILL loop for exactly that reason,
    on their own clocks and behind their own fences.  Expressing the batch here
    is what let both give up that authority without changing what they do.

    ``still_current`` re-proves the SAME identity on every poll rather than
    asking whether the PID exists: a PID recycled between the SIGTERM and the
    SIGKILL is a different process, and force-killing it is the precise accident
    every dimension in :func:`authorize_service_destruction` exists to prevent.
    """

    started = clock()
    # Resolved at CALL time, not captured as a default at definition time: a
    # default argument freezes the original `os.kill` object, so a caller (or a
    # test) that replaces `os.kill` would be silently ignored and this owner
    # would signal for real while believing it was injected.
    send_signal = os.kill if signal_process is None else signal_process
    pids = [int(request.authorization.pid) for request in requests]
    signals: list[list[int]] = [[] for _ in requests]
    outcomes: dict[int, TerminationOutcome] = {}

    def settle(
        index: int,
        action: str,
        result: str,
        reason: str,
        *,
        confirmed_dead: bool,
        failed_dimension: str = "",
        error: str = "",
        surviving_supervisor: dict[str, Any] | None = None,
    ) -> None:
        outcomes[index] = TerminationOutcome(
            pid=pids[index],
            attempted_action=action,
            result=result,
            reason=reason,
            age_seconds=clock() - started,
            confirmed_dead=confirmed_dead,
            signals=tuple(signals[index]),
            failed_dimension=failed_dimension,
            error=error,
            target=requests[index].target,
            surviving_supervisor=dict(surviving_supervisor or {}),
        )

    def yield_to_replacement(index: int) -> None:
        """A recycled PID is a DIFFERENT process, not a confirmed death.

        Without this the escalation would read "the identity I was told to stop
        is no longer there" as success and go on to remove the record and unlink
        the socket -- on behalf of a live process it never had authority over.
        """

        settle(
            index,
            LIFETIME_ACTION_TERMINATE if signals[index] else LIFETIME_ACTION_NONE,
            LIFETIME_RESULT_REFUSED,
            LocalProcessReason.PROCESS_IDENTITY_REUSED.value,
            confirmed_dead=False,
            failed_dimension=DIMENSION_PROCESS_START_IDENTITY,
        )

    def fire(index: int, signal_number: int, action: str) -> bool:
        """Send one signal, returning False once the target has been settled."""

        try:
            send_signal(pids[index], signal_number)
        except ProcessLookupError:
            settle(index, action, LIFETIME_RESULT_ALREADY_EXITED, LocalProcessReason.PROCESS_NOT_FOUND.value, confirmed_dead=True)
            return False
        except (PermissionError, OSError) as error:
            settle(
                index,
                action,
                LIFETIME_RESULT_SIGNAL_REFUSED,
                LIFETIME_REASON_AUTHORIZED,
                confirmed_dead=False,
                error=type(error).__name__,
            )
            return False
        signals[index].append(int(signal_number))
        return True

    def wait_until_gone(indices: list[int], deadline: float) -> set[int]:
        """Poll ONE shared window and return the indices provably gone within it."""

        outstanding = list(indices)
        gone: set[int] = set()
        while True:
            remaining: list[int] = []
            for index in outstanding:
                if still_current(pids[index]):
                    remaining.append(index)
                else:
                    gone.add(index)
            outstanding = remaining
            if not outstanding or clock() >= deadline:
                return gone
            sleep(poll_interval)

    # Phase 0 -- decisions that were never destructive. `refused` and `retained`
    # both end here having sent nothing; the disposition is what tells a caller
    # "I could not prove this" from "this build may never signal that".
    pending: list[int] = []
    for index, request in enumerate(requests):
        authorization = request.authorization
        if authorization.retained:
            settle(
                index,
                LIFETIME_ACTION_NONE,
                LIFETIME_RESULT_RETAINED,
                authorization.reason,
                confirmed_dead=False,
                failed_dimension=authorization.failed_dimension,
                surviving_supervisor=authorization.surviving_supervisor,
            )
            continue
        if not authorization.authorized:
            settle(
                index,
                LIFETIME_ACTION_NONE,
                LIFETIME_RESULT_REFUSED,
                authorization.reason,
                confirmed_dead=False,
                failed_dimension=authorization.failed_dimension,
            )
            continue
        if identity_replaced(pids[index]):
            yield_to_replacement(index)
            continue
        pending.append(index)

    # Phase 1 -- every graceful target is signalled before any of them waits.
    graceful = [index for index in pending if requests[index].graceful_first]
    for index in graceful:
        if not fire(index, signal_module.SIGTERM, LIFETIME_ACTION_TERMINATE):
            pending.remove(index)

    # Phase 2 -- ONE shared grace window for the whole batch. A force-only
    # target waits it out too: its leader already absorbed the SIGTERM, and
    # killing the child before that window closes is what strands a half-torn
    # group. Skipped entirely when nothing was signalled gracefully.
    if graceful:
        gone = wait_until_gone(list(pending), started + max(0.0, float(grace_seconds)))
        for index in list(pending):
            if identity_replaced(pids[index]):
                yield_to_replacement(index)
                pending.remove(index)
            elif index in gone and requests[index].graceful_first:
                settle(index, LIFETIME_ACTION_TERMINATE, LIFETIME_RESULT_CONFIRMED_EXITED, LIFETIME_REASON_AUTHORIZED, confirmed_dead=True)
                pending.remove(index)

    # Phase 3 -- force whatever survived, then ONE shared force window.
    if pending:
        forced_from = clock()
        for index in list(pending):
            if not fire(index, signal_module.SIGKILL, LIFETIME_ACTION_FORCE_TERMINATE):
                pending.remove(index)
        gone = wait_until_gone(list(pending), forced_from + max(0.0, float(force_seconds)))
        for index in pending:
            if identity_replaced(pids[index]):
                yield_to_replacement(index)
                continue
            confirmed = index in gone
            settle(
                index,
                LIFETIME_ACTION_FORCE_TERMINATE,
                LIFETIME_RESULT_FORCE_CONFIRMED_EXITED if confirmed else LIFETIME_RESULT_STILL_ALIVE,
                LIFETIME_REASON_AUTHORIZED,
                confirmed_dead=confirmed,
            )

    # Indexing rather than filtering: a target the loop above failed to settle
    # is a hole in the escalation, and it must surface as an error here instead
    # of silently vanishing from the caller's incident report.
    return [outcomes[index] for index in range(len(requests))]


def terminate_authorized_process(
    authorization: ServiceDestructionAuthorization,
    *,
    still_current: Callable[[], bool],
    identity_replaced: Callable[[], bool] = lambda: False,
    signal_process: Callable[[int, int], None] | None = None,
    grace_seconds: float,
    force_seconds: float,
    graceful_first: bool = True,
    target: str = "",
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval: float = 0.03,
) -> TerminationOutcome:
    """Escalate SIGTERM to SIGKILL against ONE authorized identity, and report what ran.

    A one-element call into :func:`terminate_authorized_processes`. It exists
    because most callers hold exactly one target and should not have to build a
    batch to say so -- not because the single case has its own algorithm.
    """

    return terminate_authorized_processes(
        [TerminationRequest(authorization=authorization, target=target, graceful_first=graceful_first)],
        still_current=lambda _pid: still_current(),
        identity_replaced=lambda _pid: identity_replaced(),
        signal_process=signal_process,
        grace_seconds=grace_seconds,
        force_seconds=force_seconds,
        clock=clock,
        sleep=sleep,
        poll_interval=poll_interval,
    )[0]


@dataclass
class ServiceLifetimeOwner:
    """The daemon-side owner of what happens when the last external claim disappears.

    Every local service routes its idle transition through
    ``runtime.claim_gated_idle_due``; this owner is what that transition *does*
    once it fires.  Setting ``stop_event`` is a request the listener may never
    honour -- a stuck handler thread, a shutdown hook that blocks, or a
    non-daemon thread at interpreter exit all leave the daemon up.  Waiting for
    the next launcher start to notice is exactly the future-restart authority the
    supervision contract forbids, so the daemon bounds its own exit instead.

    The surviving-supervisor identity is captured at construction, not read at
    escalation time: the launching parent's PID alone proves nothing once it has
    exited, and re-reading ``getppid()`` after reparenting to init would name a
    process that never supervised anything.  Capturing it while the parent is
    provably alive is what makes the later re-proof meaningful.
    """

    service_name: str
    stop_event: Any
    pid: int = 0
    supervisor_record: dict[str, Any] = field(default_factory=dict)
    grace_seconds: float = SERVICE_SELF_RETIRE_GRACE_SECONDS
    force_seconds: float = SERVICE_SELF_RETIRE_FORCE_SECONDS
    signal_process: Callable[[int, int], None] | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    host_identity: HostIdentity | None = None
    _state: str = field(default=RETIREMENT_STATE_SERVING, init=False)
    _outcome: dict[str, Any] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    @classmethod
    def for_launching_parent(
        cls,
        service_name: str,
        stop_event: Any,
        *,
        parent_pid: int,
        host_identity: HostIdentity | None = None,
        **overrides: Any,
    ) -> ServiceLifetimeOwner:
        """Capture the launching supervisor's exact identity while it is still provable."""

        identity = host_identity or current_host_identity()
        supervisor: dict[str, Any] = {}
        if int(parent_pid) > 1:
            start_identity = process_start_identity(int(parent_pid))
            if start_identity:
                supervisor = identity.process_record_fields(pid=int(parent_pid), start_identity=str(start_identity))
        return cls(
            service_name=service_name,
            stop_event=stop_event,
            pid=os.getpid(),
            supervisor_record=supervisor,
            host_identity=identity,
            **overrides,
        )

    def surviving_supervisor(self) -> dict[str, Any]:
        """Re-prove the captured supervisor identity and say, by name, who retains this daemon.

        This is the machine-readable field the contract requires: an identity
        dict when a supervisor is proven, and a typed state when there is none.
        Both outcomes occur in normal operation -- a daemon under a live server
        versus one whose server was hard-killed -- so neither branch is decorative.
        """

        if not self.supervisor_record:
            return {"state": SUPERVISOR_STATE_UNPROVEN, "identity": None, "diagnostic": None}
        diagnostic = is_current_local_process(self.supervisor_record, host_identity=self.host_identity)
        if diagnostic.current:
            return {
                "state": SUPERVISOR_STATE_RETAINED,
                "identity": dict(self.supervisor_record),
                "diagnostic": diagnostic.as_dict(),
            }
        return {
            "state": SUPERVISOR_STATE_ORPHANED,
            "identity": None,
            "diagnostic": diagnostic.as_dict(),
        }

    def status_payload(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            outcome = dict(self._outcome)
        return {
            "service": self.service_name,
            "pid": self.pid,
            "state": state,
            "grace_seconds": self.grace_seconds,
            "force_seconds": self.force_seconds,
            "surviving_supervisor": self.surviving_supervisor(),
            "retirement": outcome,
        }

    def publish(self, path: Path) -> str:
        """Persist the surviving-supervisor identity beside the socket.

        Deliberately a file rather than a field on the status RPC: the moment
        this answer matters most is when the daemon is wedged and cannot answer
        an RPC at all.  A supervisor reading the directory must still be able to
        see who retains this process and whether it has already been asked to go.

        Returns the empty string on success, or the failure's type name.  The
        caller records it; nothing here decides that a failed publish is fine.
        """

        try:
            atomic_write_text(
                Path(path),
                json.dumps(self.status_payload(), sort_keys=True, separators=(",", ":")) + "\n",
                mode=0o600,
            )
        except OSError as error:
            return type(error).__name__
        return ""

    def request_retirement(self, reason: str) -> dict[str, Any]:
        """Ask for a graceful stop AND arm the bounded escalation that guarantees it."""

        with self._lock:
            if self._thread is not None:
                return {"state": self._state, "reason": reason, "armed": False}
            self._state = RETIREMENT_STATE_REQUESTED
            self._outcome = {"reason": reason, "requested_at": self.clock()}
            thread = threading.Thread(
                target=self._escalate,
                args=(reason,),
                name=f"{self.service_name}-self-retire",
                daemon=True,
            )
            self._thread = thread
        self.stop_event.set()
        thread.start()
        return {"state": RETIREMENT_STATE_REQUESTED, "reason": reason, "armed": True}

    def note_exited_gracefully(self) -> None:
        """Record that the listener completed on its own, so escalation stands down."""

        with self._lock:
            if self._state in {RETIREMENT_STATE_TERMINATED, RETIREMENT_STATE_FORCED}:
                return
            self._state = RETIREMENT_STATE_EXITED

    def _still_running(self) -> bool:
        with self._lock:
            return self._state not in {RETIREMENT_STATE_EXITED}

    def _escalate(self, reason: str) -> None:
        """Bounded self-escalation: graceful window, self SIGTERM, then self SIGKILL.

        Every signal targets ``self.pid`` captured at construction and re-checked
        against ``os.getpid()`` immediately before firing.  A daemon that forked
        must never let a child inherit an armed timer that signals the parent's
        recycled PID, and that check is the whole defence.
        """

        deadline = self.clock() + max(0.0, float(self.grace_seconds))
        while self.clock() < deadline:
            if not self._still_running():
                return
            self.sleep(0.05)
        if not self._still_running():
            return
        if not self._fire(signal_module.SIGTERM, RETIREMENT_STATE_TERMINATED, reason):
            return
        force_deadline = self.clock() + max(0.0, float(self.force_seconds))
        while self.clock() < force_deadline:
            if not self._still_running():
                return
            self.sleep(0.05)
        if not self._still_running():
            return
        self._fire(signal_module.SIGKILL, RETIREMENT_STATE_FORCED, reason)

    def _fire(self, signal_number: int, state: str, reason: str) -> bool:
        if int(self.pid) != int(os.getpid()):
            with self._lock:
                self._outcome = {
                    "reason": reason,
                    "result": LIFETIME_RESULT_REFUSED,
                    "failed_dimension": DIMENSION_PROCESS_START_IDENTITY,
                    "recorded_pid": self.pid,
                    "observed_pid": os.getpid(),
                }
            return False
        send_signal = os.kill if self.signal_process is None else self.signal_process
        try:
            send_signal(int(self.pid), int(signal_number))
        except (ProcessLookupError, PermissionError, OSError) as error:
            with self._lock:
                self._outcome = {
                    "reason": reason,
                    "result": LIFETIME_RESULT_SIGNAL_REFUSED,
                    "signal": int(signal_number),
                    "error": type(error).__name__,
                }
            return False
        with self._lock:
            self._state = state
            self._outcome = {
                "reason": reason,
                "result": LIFETIME_RESULT_SELF_SIGNALLED,
                "signal": int(signal_number),
                "at": self.clock(),
            }
        return True
