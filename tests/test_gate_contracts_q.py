# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Architecture-contract sentinels deferred beyond the current release."""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-Q1 immediate READY/QUEUED/typed-error normal-read protocol.")
def test_q1_normal_reads_reply_immediately_with_one_terminal_state():
    assert False, "F-Q1 must end the original HTTP request with READY, QUEUED, or typed error"


@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-Q2 storage-key epoch/sequence progressive stream.")
def test_q2_stream_uses_storage_key_epoch_sequence_and_one_terminal():
    assert False, "F-Q2 must ack seq=0, reset on newer epoch, drop older epochs, and terminal exactly once"


@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-Q3 dropped-notification repair marker protocol.")
def test_q3_dropped_notification_records_repair_without_loading_loop():
    assert False, "F-Q3 must preserve last-known-good and repair a dropped/coalesced notification once"


@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-Q4 per-key shared-worker/fanout scheduler contract.")
def test_q4_same_key_clients_share_worker_and_interactive_reads_win_over_maintenance():
    assert False, "F-Q4 must coalesce one key, fan out by cursor, preserve LKG, and prioritize interactive work"


@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-Q5 pane-lifecycle journal and hook-preservation recovery contract.")
def test_q5_only_matched_pane_evidence_is_clean_and_user_hooks_remain_intact():
    assert False, "F-Q5 must classify absent evidence unknown/red and append hooks without replacing remain-on-exit/user hooks"


@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-Q6 revision-bound recovery preflight transaction.")
def test_q6_recovery_preflight_rejects_stale_plan_and_converges_live_terminal_in_one_render():
    assert False, "F-Q6 must reject stale submit, keep non-mutating actions inert, converge Lost/live once, and accept terminal input"
