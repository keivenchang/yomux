# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Retention preference wiring and malformed local-time admission."""

from __future__ import annotations

from pathlib import Path

import pytest

from yomux_lib.stats_current import prune_schedule


def test_preferences_panel_offers_the_schedule_in_the_stats_section():
    source = Path("static_src/js/yomux/83_preferences_panel.js").read_text(encoding="utf-8")
    assert "preferenceSettingItem('stats.prune_at_local_time'" in source
    assert "function statsPruneLocalTimeChoices()" in source
    # The client renders the server's list; it never spells its own copy.
    assert "clientSettingsPayload?.choices?.['stats.prune_at_local_time']" in source
    assert "02:30" not in source



@pytest.mark.parametrize("value", ["²:00", "02:⁰⁰", "①:00", "02:⑩"])
def test_non_decimal_digit_preferences_use_default_schedule(value):
    assert prune_schedule.parse_local_time(value) is None
    resolved = prune_schedule.resolve_local_time(value)
    assert resolved.fell_back
    assert resolved.text == prune_schedule.DEFAULT_PRUNE_LOCAL_TIME
