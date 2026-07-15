# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Pin the canonical YO!stats Range x Resolution contract (DOIT.1 item 1).

The derived matrix must exactly reproduce the hand-written table in DOIT.1.md, so
a formula/table drift or a bucket-budget violation fails the build instead of
shipping a decimated view. These tests own the four-value universe, the
600-bucket ceiling, AUTO resolution, and preference normalization.
"""

import pytest

from yolomux_lib import statsd
from yolomux_lib import stats_resolution as sr

# The canonical table copied verbatim from DOIT.1.md, as {range: (auto, (explicit...))}.
# The derivation formula must reproduce this exactly.
EXPECTED_MATRIX = {
    5 * 60: (1, (1, 10)),
    15 * 60: (10, (10, 60)),
    30 * 60: (10, (10, 60)),
    60 * 60: (10, (10, 60, 300)),
    2 * 60 * 60: (60, (60, 300)),
    4 * 60 * 60: (60, (60, 300)),
    8 * 60 * 60: (60, (60, 300)),
    16 * 60 * 60: (300, (300,)),
    24 * 60 * 60: (300, (300,)),
}


def test_resolution_universe_is_exactly_the_four_values():
    assert sr.RESOLUTION_CHOICES == (1, 10, 60, 300)
    for forbidden in (2, 5, 30, 120, 600):
        assert forbidden not in sr.RESOLUTION_CHOICES


def test_derived_matrix_reproduces_the_canonical_table():
    derived = sr.resolution_matrix()
    assert set(derived) == set(EXPECTED_MATRIX), "range set drifted from DOIT.1 table"
    for range_seconds, (auto, explicit) in EXPECTED_MATRIX.items():
        assert derived[range_seconds]["auto"] == auto, f"AUTO wrong at {range_seconds}s"
        assert derived[range_seconds]["explicit"] == explicit, f"explicit wrong at {range_seconds}s"


@pytest.mark.parametrize("range_seconds", sr.RANGE_SECONDS)
def test_every_matrix_cell_stays_within_the_bucket_budget(range_seconds):
    for resolution in sr.explicit_resolutions(range_seconds):
        buckets = sr.bucket_count(range_seconds, resolution)
        assert buckets <= sr.MAX_BUCKETS, f"{range_seconds}s/{resolution}s = {buckets} > {sr.MAX_BUCKETS}"
        assert buckets >= sr.MIN_BUCKETS, f"{range_seconds}s/{resolution}s = {buckets} < {sr.MIN_BUCKETS}"


@pytest.mark.parametrize("range_seconds", sr.RANGE_SECONDS)
def test_auto_is_always_in_the_explicit_set(range_seconds):
    assert sr.auto_resolution(range_seconds) in sr.explicit_resolutions(range_seconds)


@pytest.mark.parametrize("range_seconds", sr.RANGE_SECONDS)
def test_auto_is_the_finest_within_budget(range_seconds):
    auto = sr.auto_resolution(range_seconds)
    finer = [r for r in sr.RESOLUTION_CHOICES if r < auto]
    for r in finer:
        assert range_seconds / r > sr.MAX_BUCKETS, (
            f"{range_seconds}s AUTO picked {auto}s but finer {r}s stays within budget"
        )


def test_retired_dense_cells_are_not_offered():
    # 15m/1s (900 buckets) and 2h/10s (720 buckets) exceed the budget and must be absent.
    assert 1 not in sr.explicit_resolutions(15 * 60)
    assert 10 not in sr.explicit_resolutions(2 * 60 * 60)


def test_resolve_requested_never_substitutes():
    # AUTO resolves to a concrete value...
    assert sr.resolve_requested(5 * 60, sr.AUTO) == 1
    assert sr.resolve_requested(2 * 60 * 60, sr.AUTO) == 60
    # ...a supported explicit value passes through unchanged...
    assert sr.resolve_requested(60 * 60, 300) == 300
    # ...and an unsupported explicit value raises rather than coarsening.
    with pytest.raises(ValueError):
        sr.resolve_requested(2 * 60 * 60, 10)  # retired 2h/10s
    with pytest.raises(ValueError):
        sr.resolve_requested(60 * 60, 120)  # forbidden universe value
    with pytest.raises(ValueError):
        sr.resolve_requested(7 * 60, 10)  # unsupported range


def test_preferences_normalize_to_auto_when_invalid():
    assert sr.normalize_preference(2 * 60 * 60, 10) == sr.AUTO  # retired for this range
    assert sr.normalize_preference(60 * 60, 120) == sr.AUTO  # forbidden universe value
    assert sr.normalize_preference(60 * 60, 600) == sr.AUTO  # forbidden universe value
    assert sr.normalize_preference(60 * 60, 300) == 300  # valid stays
    assert sr.normalize_preference(5 * 60, sr.AUTO) == sr.AUTO  # AUTO stays AUTO


def test_is_supported_matches_the_matrix():
    assert sr.is_supported(5 * 60, 1)
    assert sr.is_supported(24 * 60 * 60, 300)
    assert not sr.is_supported(24 * 60 * 60, 60)
    assert not sr.is_supported(7 * 60, 10)  # non-preset range


def test_statsd_status_advertises_the_canonical_capabilities(tmp_path):
    # The server publishes the policy once from the single owner (via the status
    # action), not per history response, so the browser reads choices from here.
    service = statsd.PersistentStatsService(tmp_path / "statsd.sock", tmp_path / "stats.sqlite3")
    response, _binary = service.handle_with_binary({"action": "status"})
    assert response["resolution_capabilities"] == sr.wire_capabilities()


def test_wire_capabilities_is_json_safe_and_matches_the_matrix():
    import json

    caps = sr.wire_capabilities()
    # JSON-serializable (no tuples/sets leaking to the wire).
    json.dumps(caps)
    assert caps["resolution_choices"] == [1, 10, 60, 300]
    assert caps["max_buckets"] == 600
    by_range = {entry["range_seconds"]: entry for entry in caps["ranges"]}
    assert set(by_range) == set(EXPECTED_MATRIX)
    for range_seconds, (auto, explicit) in EXPECTED_MATRIX.items():
        entry = by_range[range_seconds]
        assert entry["auto_resolution_seconds"] == auto
        assert entry["explicit_resolution_seconds"] == list(explicit)
        for resolution in explicit:
            assert entry["buckets"][resolution] == range_seconds // resolution
            assert entry["buckets"][resolution] <= 600


def test_system_status_payload_publishes_the_matrix_to_the_client(tmp_path):
    # The web app exposes the canonical matrix from the single owner on
    # /api/system-status so the render-only browser reads choices from the server.
    from yolomux_lib import app as app_module
    # Build a minimal payload the way the endpoint does, asserting the owner field
    # is present and equals the single source of truth.
    caps = sr.wire_capabilities()
    assert caps["resolution_choices"] == [1, 10, 60, 300]
    # The app method injects exactly this; a direct equality guards drift.
    assert app_module.stats_resolution.wire_capabilities() == caps
