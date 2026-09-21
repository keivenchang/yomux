"""One immutable verdict for a local background-refresh request."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RefreshOutcomeState(Enum):
    """The closed set of local background-refresh outcomes."""

    ACCEPTED_LOCAL = "accepted_local"
    PENDING_LOCAL = "pending_local"
    FALLBACK_REQUIRED = "fallback_required"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True)
class RefreshOutcome:
    """Classify one refresh request without another-server state."""

    state: RefreshOutcomeState
    coalesced: bool
    role: str
    error: str

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> "RefreshOutcome":
        coalesced = bool(result.get("coalesced"))
        role = str(result.get("role") or "")
        error = str(result.get("error") or "")
        if result.get("accepted"):
            state = RefreshOutcomeState.ACCEPTED_LOCAL
        elif result.get("deferred_acceptance"):
            state = RefreshOutcomeState.PENDING_LOCAL
        elif result.get("fallback"):
            state = RefreshOutcomeState.FALLBACK_REQUIRED
        else:
            state = RefreshOutcomeState.TERMINAL_FAILURE
        return cls(state=state, coalesced=coalesced, role=role, error=error)

    @property
    def accepted(self) -> bool:
        return self.state is RefreshOutcomeState.ACCEPTED_LOCAL

    @property
    def local(self) -> bool:
        return self.accepted

    @property
    def pending(self) -> bool:
        return self.state is RefreshOutcomeState.PENDING_LOCAL

    @property
    def fallback(self) -> bool:
        return self.state is RefreshOutcomeState.FALLBACK_REQUIRED

    @property
    def terminal(self) -> bool:
        return self.state is RefreshOutcomeState.TERMINAL_FAILURE

    @property
    def ok(self) -> bool:
        return self.accepted

    @property
    def cache_status(self) -> str:
        if self.coalesced:
            return "coalesced"
        if self.pending:
            return "pending"
        if self.fallback:
            return "fallback"
        if self.accepted:
            return "accepted"
        return "rejected"
