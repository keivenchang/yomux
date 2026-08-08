# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Regression gates for durable tmux recovery and process-group ownership."""

from __future__ import annotations

import pytest

from yolomux_lib import tmux


@pytest.mark.xfail(strict=True, reason="F3: v0.6.10 has no recovery-row identity contract shared by live and Lost sessions.")
def test_d8_recovered_live_session_has_no_duplicate_lost_row():
    """One recovered identity renders once, even when its old Lost record names another socket."""
    rows = tmux.recovery_rows_for_test(
        live_sessions=[{"name": "fixture-recovered", "socket": "/tmp/fixture-live.sock"}],
        lost_sessions=[{"name": "fixture-recovered", "socket": "/tmp/fixture-old.sock", "state": "lost"}],
    )
    matching_rows = [row for row in rows if row["name"] == "fixture-recovered"]
    assert len(matching_rows) == 1, rows
    assert matching_rows[0]["state"] == "live", matching_rows


def test_d9_unowned_foreign_and_recycled_process_groups_are_typed_refusals():
    """No ownership evidence, another deployment, and PID/PGID reuse all fail closed with distinct codes."""
    outcomes = tmux.refuse_unowned_process_group_signals_for_test(
        deployment_id="fixture-deployment-a",
        attempts=[
            {"case": "no_record", "pgid": 7101},
            {"case": "foreign_deployment", "pgid": 7102, "owner_deployment_id": "fixture-deployment-b"},
            {"case": "recycled_pgid", "pgid": 7103, "recorded_leader_start_ticks": 100, "live_leader_start_ticks": 200},
        ],
    )
    assert [outcome["case"] for outcome in outcomes] == ["no_record", "foreign_deployment", "recycled_pgid"], outcomes
    assert all(outcome["signalled"] is False for outcome in outcomes), outcomes
    assert [outcome["reason"] for outcome in outcomes] == [
        "process_group_ownership_missing",
        "process_group_owned_by_another_deployment",
        "process_group_identity_recycled",
    ], outcomes
    assert all(outcome["reason"] != "nothing_to_kill" for outcome in outcomes), outcomes
