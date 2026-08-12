"""One immutable verdict for a background-refresh control request.

A background-refresh request has exactly one control outcome: the local owner
took the work (or already had it pending), a live remote owner accepted it, no
owner will do it so the caller must compute locally, or the request was rejected
outright with no fallback path. The owner registry historically answered that
question with a bag of flags -- ``ok``, ``accepted``, ``coalesced``, ``fallback``,
``local_owner``, ``already_pending`` -- and every consumer re-derived its own
verdict from a different subset of them. That is how a request reported "accepted"
and "must fall back" at once, and how ``refreshing_elsewhere`` came to mean four
different things in four call sites.

This leaf classifies the raw result ONCE, on ingress, into one closed state and
DERIVES every compatibility field and ``refreshing_elsewhere`` from that state, so
no two consumers can read contradictory copies of the same judgement. It depends
on nothing in the package (stdlib only) so any layer can classify a result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RefreshOutcomeState(Enum):
    """The closed set of control outcomes for one background-refresh request."""

    # This process's owner accepted the work, or already had an equivalent
    # request pending (coalesced). Either way the refresh runs HERE.
    ACCEPTED_LOCAL = "accepted_local"
    # A live owner in ANOTHER process accepted (or coalesced) the work. It is
    # refreshing elsewhere; this process must keep serving useful stale bytes.
    ACCEPTED_REMOTE = "accepted_remote"
    # No owner will do the work (no owner wired, the owner is unresponsive, or a
    # remote owner declined). The caller must compute locally to make progress.
    FALLBACK_REQUIRED = "fallback_required"
    # The request was rejected with no fallback path (e.g. the persistent indexer
    # is unavailable). There is nothing more the caller can do for this request.
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True)
class RefreshOutcome:
    """The single classified verdict; every boolean below is derived from ``state``."""

    state: RefreshOutcomeState
    coalesced: bool
    role: str
    error: str

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> "RefreshOutcome":
        """Classify a raw owner-refresh result dict into exactly one closed state.

        ``accepted`` wins first: an accepted request never also "falls back". A
        remote acceptance is distinguished from a local one by ``local_owner``.
        Only a non-accepted result is a fallback (the owner asked us to compute)
        or a terminal failure (rejected with no fallback path).
        """
        coalesced = bool(result.get("coalesced"))
        role = str(result.get("role") or "")
        error = str(result.get("error") or "")
        if result.get("accepted"):
            state = (
                RefreshOutcomeState.ACCEPTED_LOCAL
                if result.get("local_owner")
                else RefreshOutcomeState.ACCEPTED_REMOTE
            )
        elif result.get("fallback"):
            state = RefreshOutcomeState.FALLBACK_REQUIRED
        else:
            state = RefreshOutcomeState.TERMINAL_FAILURE
        return cls(state=state, coalesced=coalesced, role=role, error=error)

    @property
    def accepted(self) -> bool:
        return self.state in (RefreshOutcomeState.ACCEPTED_LOCAL, RefreshOutcomeState.ACCEPTED_REMOTE)

    @property
    def local_owner(self) -> bool:
        return self.state is RefreshOutcomeState.ACCEPTED_LOCAL

    @property
    def fallback(self) -> bool:
        """The caller must compute locally to make progress."""
        return self.state is RefreshOutcomeState.FALLBACK_REQUIRED

    @property
    def terminal(self) -> bool:
        return self.state is RefreshOutcomeState.TERMINAL_FAILURE

    @property
    def ok(self) -> bool:
        return self.accepted

    @property
    def refreshing_elsewhere(self) -> bool:
        """A live owner in another process is refreshing this data right now.

        True ONLY for a remote acceptance: a local acceptance refreshes here, and
        neither a fallback nor a terminal failure means anyone else is working.
        """
        return self.state is RefreshOutcomeState.ACCEPTED_REMOTE

    @property
    def cache_status(self) -> str:
        """The one performance-sample label, derived from the single verdict."""
        if self.coalesced:
            return "coalesced"
        if self.fallback:
            return "fallback"
        if self.accepted:
            return "accepted"
        return "rejected"
