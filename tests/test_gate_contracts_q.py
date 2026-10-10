# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deferred recovery transaction sentinel owned by the recovery queue."""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-Q6 revision-bound recovery preflight transaction.")
def test_q6_recovery_preflight_rejects_stale_plan_and_converges_live_terminal_in_one_render():
    assert False, "F-Q6 must reject stale submit, keep non-mutating actions inert, converge Lost/live once, and accept terminal input"
