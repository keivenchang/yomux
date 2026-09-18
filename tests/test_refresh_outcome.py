"""Tests for the local background-refresh result classifier."""

import dataclasses

import pytest

from yolomux_lib.infra.refresh_outcome import RefreshOutcome
from yolomux_lib.infra.refresh_outcome import RefreshOutcomeState


ACCEPTED_LOCAL = {"ok": True, "accepted": True, "role": "r", "local": True, "fallback": False}
COALESCED_LOCAL = {
    "ok": True,
    "accepted": True,
    "role": "r",
    "local": True,
    "fallback": False,
    "already_pending": True,
    "coalesced": True,
}
PENDING_LOCAL = {
    "ok": True,
    "accepted": False,
    "role": "search-index",
    "local": True,
    "deferred_acceptance": True,
    "fallback": False,
}
FALLBACK_REQUIRED = {"ok": False, "accepted": False, "role": "r", "error": "local refresh unavailable", "fallback": True}
TERMINAL_FAILURE = {"ok": False, "accepted": False, "role": "r", "fallback": False}


def test_local_acceptance_is_the_only_accepted_state():
    outcome = RefreshOutcome.from_result(ACCEPTED_LOCAL)

    assert outcome.state is RefreshOutcomeState.ACCEPTED_LOCAL
    assert outcome.accepted and outcome.ok and outcome.local
    assert not outcome.fallback and not outcome.terminal
    assert outcome.cache_status == "accepted"


def test_coalesced_local_refresh_preserves_the_local_state():
    outcome = RefreshOutcome.from_result(COALESCED_LOCAL)

    assert outcome.state is RefreshOutcomeState.ACCEPTED_LOCAL
    assert outcome.accepted and outcome.local and outcome.coalesced
    assert outcome.cache_status == "coalesced"


def test_deferred_local_refresh_is_pending_until_the_worker_accepts_it():
    outcome = RefreshOutcome.from_result(PENDING_LOCAL)

    assert outcome.state is RefreshOutcomeState.PENDING_LOCAL
    assert outcome.pending and not outcome.accepted and not outcome.local
    assert not outcome.fallback and not outcome.terminal
    assert outcome.cache_status == "pending"


def test_fallback_required_means_the_caller_may_compute_locally():
    outcome = RefreshOutcome.from_result(FALLBACK_REQUIRED)

    assert outcome.state is RefreshOutcomeState.FALLBACK_REQUIRED
    assert outcome.fallback and not outcome.accepted and not outcome.ok
    assert outcome.cache_status == "fallback"


@pytest.mark.parametrize("raw", [TERMINAL_FAILURE, {}, {"accepted": False, "fallback": False, "error": "broken"}])
def test_rejected_or_malformed_result_is_terminal(raw):
    outcome = RefreshOutcome.from_result(raw)

    assert outcome.state is RefreshOutcomeState.TERMINAL_FAILURE
    assert not outcome.accepted and not outcome.ok and not outcome.fallback
    assert outcome.terminal
    assert outcome.cache_status == "rejected"


def test_accepted_result_wins_over_a_stray_fallback_flag():
    outcome = RefreshOutcome.from_result({"accepted": True, "local": True, "fallback": True})

    assert outcome.state is RefreshOutcomeState.ACCEPTED_LOCAL
    assert outcome.accepted and not outcome.fallback


def test_outcome_is_immutable():
    outcome = RefreshOutcome.from_result(ACCEPTED_LOCAL)

    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.state = RefreshOutcomeState.TERMINAL_FAILURE  # type: ignore[misc]
