# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared proof shape and catalog for user-visible asynchronous transitions.

The Python assertion can evaluate only evidence that returns from the subject runtime. Browser proofs therefore own a shorter in-page deadline that converts a missing settlement into a typed outcome before Selenium's outer script timeout; the catalog alone cannot interrupt a hung browser promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TerminalStateContract:
    contract_id: str
    owner_path: Path
    owner_token: str
    proof_path: Path
    proof_test: str
    queued_producer_function: str | None = None


TERMINAL_STATE_CONTRACTS = (
    TerminalStateContract(
        contract_id="command-dispatch-pending",
        owner_path=Path("static_src/js/yolomux/15_command_registry.js"),
        owner_token="function dispatchCommand(route, params = {}, source = null)",
        proof_path=Path("tests/test_gate_contract.py"),
        proof_test="test_k0_each_user_command_class_executes_k1_through_k5",
    ),
    TerminalStateContract(
        contract_id="differ-refreshing-elsewhere",
        owner_path=Path("static_src/js/yolomux/86_changes_editor.js"),
        owner_token="function scheduleSessionFilesProducerDeadline(destination, payload)",
        proof_path=Path("tests/test_gate_differ.py"),
        proof_test="test_mock_git_differ_pending_producer_without_publish_ends_in_visible_deadline_error",
    ),
    TerminalStateContract(
        contract_id="differ-queued-producer-completion",
        owner_path=Path("yolomux_lib/infra/state_services.py"),
        owner_token="class SessionFilesOperationLifecycle:",
        proof_path=Path("tests/test_gate_differ.py"),
        proof_test="test_mock_git_differ_queued_producer_completion_settles_every_visible_surface",
        queued_producer_function="start",
    ),
    TerminalStateContract(
        contract_id="jobd-product-operation-completion",
        owner_path=Path("yolomux_lib/app.py"),
        owner_token="def accept_jobd_product_operation(",
        proof_path=Path("tests/test_gate_differ.py"),
        proof_test="test_mock_git_differ_queued_producer_completion_settles_every_visible_surface",
        queued_producer_function="accept_jobd_product_operation",
    ),
    TerminalStateContract(
        contract_id="session-files-http-operation-completion",
        owner_path=Path("yolomux_lib/app.py"),
        owner_token="def session_files_http_payload(",
        proof_path=Path("tests/test_session_files.py"),
        proof_test="test_session_files_route_returns_operation_receipt_then_publishes_and_replays_ready",
        queued_producer_function="session_files_http_payload",
    ),
    TerminalStateContract(
        contract_id="finder-expansion-all-surfaces",
        owner_path=Path("static_src/js/yolomux/60_popovers_tabs.js"),
        owner_token="function settleDirectoryRowExpansionAcrossSurfaces(row, fullPath, entries)",
        proof_path=Path("tests/test_gate_finder.py"),
        proof_test="test_b3a_pending_expansion_settles_every_live_finder_surface",
    ),
    TerminalStateContract(
        contract_id="usage-backfill-status",
        owner_path=Path("yolomux_lib/stats_current/transcripts.py"),
        owner_token="def usage_atom_backfill_status(self) -> dict[str, object] | None:",
        proof_path=Path("tests/test_stats_current_transcripts.py"),
        proof_test="test_usage_atom_backfill_status_varies_with_committed_cursor_progress",
    ),
    TerminalStateContract(
        contract_id="transcript-scan-receipt",
        owner_path=Path("yolomux_lib/stats_current/transcripts.py"),
        owner_token="def commit(self, receipt_id: int) -> None:",
        proof_path=Path("tests/test_stats_current_transcripts.py"),
        proof_test="test_receipt_larger_than_memory_retention_commits_before_pruning",
    ),
    TerminalStateContract(
        contract_id="stats-delta-queued-producer",
        owner_path=Path("yolomux_lib/stats_current/http.py"),
        owner_token="def delta_stream(",
        proof_path=Path("tests/test_stats_current_http.py"),
        proof_test="test_delta_queued_acknowledgement_can_reach_terminal_body",
        queued_producer_function="delta_stream",
    ),
)


def assert_terminal_transition(
    *,
    contract_id: str,
    pending_observed: bool,
    terminal_observed: bool,
    evidence: Any = None,
) -> None:
    """Require returned evidence to observe both sides of an asynchronous transition."""

    if pending_observed is not True:
        raise AssertionError(
            f"{contract_id}: test never observed the pending state; evidence={evidence!r}"
        )
    if terminal_observed is not True:
        raise AssertionError(
            f"{contract_id}: pending state never reached a terminal state; evidence={evidence!r}"
        )
