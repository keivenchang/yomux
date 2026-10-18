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
        contract_id="finder-expansion-all-surfaces",
        owner_path=Path("static_src/js/yomux/60_popovers_tabs.js"),
        owner_token="async function settleDirectoryRowExpansionAcrossSurfaces(row, fullPath, entries, options = {})",
        proof_path=Path("tests/test_gate_finder.py"),
        proof_test="test_b3a_pending_expansion_settles_every_live_finder_surface",
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
