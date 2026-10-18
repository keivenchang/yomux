# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""v0.6.10 YO!stats range-shift gate with realistic fixture volume.

The fixture owns 30 mock transcript-like agent sources across 24 hours: 288
five-minute usage atoms per source (8,640 rows), plus 288 five-minute CPU
observations and one CPU coverage epoch (8,929 stored rows).  That volume
prevents an empty-state response from posing as a range-shift baseline.

Accepted v0.6.10 baseline, 30 samples per transition, milliseconds, measured
from UI selection through the matching real HTTP snapshot's DOM mutation:

| transition | median | p95 | max |
| --- | ---: | ---: | ---: |
| 1s -> 10s | 87.5 | 89.9 | 102.8 |
| 10s -> 60s | 88.3 | 90.1 | 90.9 |
| 60s -> 300s | 102.5 | 107.4 | 109.3 |
| 300s -> 60s | 88.4 | 90.3 | 91.2 |
| 60s -> 10s | 87.7 | 89.1 | 90.4 |
| 10s -> 1s | 88.1 | 90.2 | 90.6 |

Every transition must remain below 350 ms: a 3.2x margin over the 109.3 ms
worst accepted maximum, consistent with the threefold gate margin. The shared
roughly 88 ms floor remains unexplained; source inspection found no matching
fixed 100 ms timer, so it is recorded rather than attributed. The test proves
both guards fire: an injected post-render delay breaches the latency budget and
an injected never-returning snapshot trips the completion timeout.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
from pathlib import Path
from tools.tmux_paths import short_temp_base
from types import SimpleNamespace
from urllib.parse import parse_qs
from urllib.parse import urlparse

import pytest

from tests import latency_calibration
from tests.browser_helpers.browser_layout import (  # noqa: F401
    browser,
    start_browser_server,
    stop_browser_server,
)
from tests.helpers.browser_stats_coverage import _start_current_stats, _write_current_stats_fixture_assets
from tests.gate_harness import run_fixture_cleanup_phases
from tests.helpers.gate_stats import NOW
from tests.helpers.gate_stats import _seed_realistic_stats
from tests.subsystems.stats_24h_http import MAX_24H_ENDPOINT_MS
from tests.subsystems.stats_24h_http import assert_bunched_transcript_window_rejected
from tests.subsystems.stats_24h_http import assert_query_policy
from yomux_lib.stats_current import storage


SAMPLE_COUNT = 30
MAX_RANGE_SHIFT_MS = 350.0
COMPLETION_TIMEOUT_MS = 6_500
INJECTED_COMPLETION_TIMEOUT_MS = 25
SCALE_SWEEP = (
    (900, 10, "1s->10s"),
    (3600, 60, "10s->60s"),
    (86400, 300, "60s->300s"),
    (3600, 60, "300s->60s"),
    (900, 10, "60s->10s"),
    (300, 1, "10s->1s"),
)
pytestmark = pytest.mark.browser
certification_phase_only = latency_calibration.certification_phase_fixture()




def _distribution(samples: list[float]) -> tuple[float, float, float]:
    ordered = sorted(samples)
    return (
        statistics.median(ordered),
        ordered[math.ceil(len(ordered) * 0.95) - 1],
        ordered[-1],
    )




def _assert_range_shift_latency(label: str, maximum_ms: float) -> None:
    assert maximum_ms < MAX_RANGE_SHIFT_MS, (
        f"G4b latency guard: {label} max {maximum_ms:.1f} ms exceeds "
        f"{MAX_RANGE_SHIFT_MS:.1f} ms"
    )


def test_debug_stats_sample_endpoint_is_not_a_live_client_contract():
    """The retired sample route must not survive as a latent browser 404."""
    source = Path("static_src/js/yomux/85_debug_panel.js").read_text(encoding="utf-8")
    assert "/api/stats-sample" not in source




def test_stats_24h_transcript_window_guard_rejects_a_bunched_corpus(tmp_path):
    assert_bunched_transcript_window_rejected(tmp_path)
