# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deferred recovery sentinels and the credential-admission regression."""
from __future__ import annotations
import pytest
def _missing(item: str, contract: str) -> None:
    assert False, f"{item} waits for its deferred user-boundary contract: {contract}"

@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P2 recovery preflight contract")
def test_p2_same_worktree_commit_is_not_recovery_drift(): _missing("F-P2", "HTTP preflight permits same-root HEAD movement")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P3 recovery dialog contract")
def test_p3_blocked_recover_is_visible_and_nonmutating(): _missing("F-P3", "DOM disabled control is visibly blocked and does not dispatch")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P4 recovery transcript contract")
def test_p4_recovery_keeps_process_discovered_transcript_identity(): _missing("F-P4", "recovered browser terminal resumes its pane transcript")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P5 pane lifecycle evidence contract")
def test_p5_deliberate_pane_close_is_not_lost(): _missing("F-P5", "matched clean lifecycle evidence removes no Lost row")
