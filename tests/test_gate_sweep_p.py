# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""User-boundary P regression sentinels deferred beyond the current release."""
from __future__ import annotations
import pytest

def _missing(item: str, contract: str) -> None:
    assert False, f"{item} waits for its deferred user-boundary contract: {contract}"

@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P1 preview passive-refresh contract")
def test_p1_passive_markdown_preview_refresh_keeps_view_state(): _missing("F-P1", "DOM scroll/view state survives passive refresh")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P2 recovery preflight contract")
def test_p2_same_worktree_commit_is_not_recovery_drift(): _missing("F-P2", "HTTP preflight permits same-root HEAD movement")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P3 recovery dialog contract")
def test_p3_blocked_recover_is_visible_and_nonmutating(): _missing("F-P3", "DOM disabled control is visibly blocked and does not dispatch")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P4 recovery transcript contract")
def test_p4_recovery_keeps_process_discovered_transcript_identity(): _missing("F-P4", "recovered browser terminal resumes its pane transcript")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P5 pane lifecycle evidence contract")
def test_p5_deliberate_pane_close_is_not_lost(): _missing("F-P5", "matched clean lifecycle evidence removes no Lost row")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P6 shared scheduler health contract")
def test_p6_dead_scheduler_cannot_advertise_healthy_products(): _missing("F-P6", "HTTP health fails while product panes cannot complete")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P7 deployment config ownership contract")
def test_p7_dev_guidance_never_points_to_production_config(): _missing("F-P7", "rendered dev path is deployment-owned")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P8 shared agent history contract")
def test_p8_dev_deployment_retains_real_agent_history(): _missing("F-P8", "dev browser reads real agent history")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P11 typed skill-directory errors")
def test_p11_unreadable_skill_dirs_are_not_empty_lists(): _missing("F-P11", "HTTP/DOM distinguishes unreadable from empty")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P12 named raw-image authentication errors")
def test_p12_native_image_preview_names_authentication_failure(): _missing("F-P12", "image DOM names authentication failure")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P13 valid Basic replay exemption")
def test_p13_repeated_valid_basic_requests_are_not_throttled(): _missing("F-P13", "repeated valid Basic API HTTP responses remain successful")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P14 narrow tab menu geometry contract")
def test_p14_narrow_tab_context_menu_remains_usable(): _missing("F-P14", "rendered menu stays in viewport with usable rows")
