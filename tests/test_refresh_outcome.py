"""W6: one immutable verdict for a background-refresh control request.

These tests pin the closed-state classifier that replaced the bag of
contradictory booleans (`ok`/`accepted`/`coalesced`/`fallback`/`local_owner`)
the owner registry used to return. Every compatibility field and
`refreshing_elsewhere` must derive from the single state, so the raw-result
shapes the registry emits classify to exactly one verdict.
"""

import dataclasses

import pytest

from yolomux_lib.infra.refresh_outcome import RefreshOutcome
from yolomux_lib.infra.refresh_outcome import RefreshOutcomeState


# The exact raw shapes `BackgroundOwnerRegistry.request_owner_refresh` emits, so
# the classifier is tested against production results, not invented dicts.
ACCEPTED_LOCAL = {"ok": True, "accepted": True, "role": "r", "local_owner": True, "fallback": False}
COALESCED_LOCAL = {"ok": True, "accepted": True, "role": "r", "local_owner": True, "fallback": False, "already_pending": True, "coalesced": True}
ACCEPTED_REMOTE = {"ok": True, "accepted": True, "role": "r", "response": {"ok": True, "accepted": True}, "fallback": False}
COALESCED_REMOTE = {"ok": True, "accepted": True, "role": "r", "fallback": False, "already_pending": True, "coalesced": True}
FALLBACK_UNRESPONSIVE = {"ok": False, "accepted": False, "role": "r", "error": "owner unresponsive", "fallback": True}
FALLBACK_DIAGNOSTIC = {"ok": False, "accepted": False, "role": "r", "error": "process_replaced", "reason_code": "process_replaced", "fallback": True}
REJECTED_TERMINAL = {"ok": False, "accepted": False, "role": "r", "fallback": False}
REJECTED_INDEXER = {"ok": False, "accepted": False, "role": "r", "error": "persistent indexer unavailable"}


def test_local_acceptance_refreshes_here_not_elsewhere():
    outcome = RefreshOutcome.from_result(ACCEPTED_LOCAL)
    assert outcome.state is RefreshOutcomeState.ACCEPTED_LOCAL
    assert outcome.accepted and outcome.ok and outcome.local_owner
    assert not outcome.fallback and not outcome.terminal
    # It refreshes in THIS process, so nothing is refreshing elsewhere.
    assert outcome.refreshing_elsewhere is False
    assert outcome.cache_status == "accepted"


def test_remote_acceptance_is_refreshing_elsewhere():
    outcome = RefreshOutcome.from_result(ACCEPTED_REMOTE)
    assert outcome.state is RefreshOutcomeState.ACCEPTED_REMOTE
    assert outcome.accepted and outcome.ok and not outcome.local_owner
    # A live remote owner took it, so serve stale bytes and say so.
    assert outcome.refreshing_elsewhere is True
    assert outcome.cache_status == "accepted"


def test_coalesced_local_and_remote_carry_the_coalesced_status():
    local = RefreshOutcome.from_result(COALESCED_LOCAL)
    assert local.state is RefreshOutcomeState.ACCEPTED_LOCAL
    assert local.coalesced and local.cache_status == "coalesced"
    assert local.refreshing_elsewhere is False
    remote = RefreshOutcome.from_result(COALESCED_REMOTE)
    assert remote.state is RefreshOutcomeState.ACCEPTED_REMOTE
    assert remote.coalesced and remote.cache_status == "coalesced"
    # A remote owner already had it pending: still refreshing elsewhere.
    assert remote.refreshing_elsewhere is True


@pytest.mark.parametrize("raw", [FALLBACK_UNRESPONSIVE, FALLBACK_DIAGNOSTIC])
def test_fallback_required_means_compute_locally(raw):
    outcome = RefreshOutcome.from_result(raw)
    assert outcome.state is RefreshOutcomeState.FALLBACK_REQUIRED
    assert outcome.fallback and not outcome.accepted and not outcome.ok
    # No owner will do it, so nobody is refreshing elsewhere -- the caller computes.
    assert outcome.refreshing_elsewhere is False
    assert outcome.cache_status == "fallback"


@pytest.mark.parametrize("raw", [REJECTED_TERMINAL, REJECTED_INDEXER, {}])
def test_rejected_or_malformed_is_terminal_never_accepted_or_fallback(raw):
    outcome = RefreshOutcome.from_result(raw)
    assert outcome.state is RefreshOutcomeState.TERMINAL_FAILURE
    assert not outcome.accepted and not outcome.ok
    assert not outcome.fallback and outcome.terminal
    assert outcome.refreshing_elsewhere is False
    assert outcome.cache_status == "rejected"


def test_accepted_never_also_falls_back():
    # A result that carries BOTH accepted and a stray fallback flag is still an
    # acceptance -- acceptance wins, so the two can never contradict downstream.
    outcome = RefreshOutcome.from_result({"accepted": True, "local_owner": True, "fallback": True})
    assert outcome.state is RefreshOutcomeState.ACCEPTED_LOCAL
    assert outcome.accepted and not outcome.fallback


def test_mixed_batch_classifies_each_result_independently():
    batch = [ACCEPTED_LOCAL, ACCEPTED_REMOTE, FALLBACK_UNRESPONSIVE, REJECTED_TERMINAL, COALESCED_REMOTE]
    states = [RefreshOutcome.from_result(raw).state for raw in batch]
    assert states == [
        RefreshOutcomeState.ACCEPTED_LOCAL,
        RefreshOutcomeState.ACCEPTED_REMOTE,
        RefreshOutcomeState.FALLBACK_REQUIRED,
        RefreshOutcomeState.TERMINAL_FAILURE,
        RefreshOutcomeState.ACCEPTED_REMOTE,
    ]
    # refreshing_elsewhere is true for exactly the remote acceptances.
    assert [RefreshOutcome.from_result(raw).refreshing_elsewhere for raw in batch] == [False, True, False, False, True]


def test_outcome_is_immutable():
    outcome = RefreshOutcome.from_result(ACCEPTED_REMOTE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.state = RefreshOutcomeState.ACCEPTED_LOCAL  # type: ignore[misc]
