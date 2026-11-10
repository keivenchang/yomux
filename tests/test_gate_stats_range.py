# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Stats source and transcript-window regression guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.browser_helpers.browser_layout import (  # noqa: F401
    browser,
    start_browser_server,
    stop_browser_server,
)
from tests.subsystems.stats_24h_http import assert_bunched_transcript_window_rejected


pytestmark = pytest.mark.browser


def test_debug_stats_sample_endpoint_is_not_a_live_client_contract():
    """The retired sample route must not survive as a latent browser 404."""
    source = Path("static_src/js/yomux/85_debug_panel.js").read_text(encoding="utf-8")
    assert "/api/stats-sample" not in source


def test_stats_24h_transcript_window_guard_rejects_a_bunched_corpus(tmp_path):
    assert_bunched_transcript_window_rejected(tmp_path)
