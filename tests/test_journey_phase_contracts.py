"""Non-browser contracts for the typed mega-journey phase split."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.helpers.journey_phases import ALL_JOURNEY_CHANNELS
from tests.helpers.journey_phases import GENERATED_SHARE_PHASES
from tests.helpers.journey_phases import JourneyPhase
from tests.helpers.journey_phases import JourneySentinel
from tests.helpers.journey_phases import STATS_LOGS_PHASES
from tests.helpers.journey_phases import YOCHAT_PHASES


TARGETS = (
    (Path("tests/test_browser_layout.py"), "test_current_stats_logs_visible_polling_refresh_scroll_and_narrow_layout", STATS_LOGS_PHASES),
    (Path("tests/test_browser_layout.py"), "test_yochat_live_panel_unicode_status_search_and_emoji_geometry", YOCHAT_PHASES),
    (Path("tests/test_browser_share.py"), "test_generated_share_link_mirrors_interactive_ui_surface_matrix", None),
)


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


@pytest.mark.parametrize("phases", (STATS_LOGS_PHASES, YOCHAT_PHASES, *GENERATED_SHARE_PHASES.values()))
def test_typed_journey_phase_manifests_are_contiguous_and_complete(phases):
    sentinel = JourneySentinel(phases)
    assert all(phase.channels == ALL_JOURNEY_CHANNELS for phase in phases)
    for phase in phases:
        sentinel.enter(phase.name)
    assert sentinel.assert_complete() == tuple(phase.name for phase in phases)
    assert sentinel.manifest() == tuple(
        (phase.name, ("events", "fetches", "observations", "sockets"))
        for phase in phases
    )


def test_typed_journey_sentinel_rejects_skips_and_incomplete_manifests():
    phases = (JourneyPhase("setup", None), JourneyPhase("target", "setup"))
    sentinel = JourneySentinel(phases)
    with pytest.raises(AssertionError, match="expected 'setup', got 'target'"):
        sentinel.enter("target")
    sentinel = JourneySentinel(phases)
    sentinel.enter("setup")
    with pytest.raises(AssertionError, match="incomplete journey manifest"):
        sentinel.assert_complete()


def test_aggregate_nodes_keep_one_fixture_setup_and_visit_every_typed_phase():
    for path, name, phases in TARGETS:
        function = _function(path, name)
        source = ast.get_source_segment(path.read_text(encoding="utf-8"), function) or ""
        assert source.count("JourneySentinel(") == 1, (path, name)
        assert source.count("load_live_runtime_boot_fixture(") <= 1, (path, name)
        assert source.count("start_isolated_browser_share_app(") <= 1, (path, name)
        if phases is not None:
            for phase in phases:
                assert source.count(f'journey.enter("{phase.name}")') == 1, (path, name, phase.name)
        else:
            assert "GENERATED_SHARE_PHASES[matrix_section]" in source
            assert 'journey.enter(f"{matrix_section}-surface-matrix")' in source
        assert source.count("journey.manifest()") == 1, (path, name)
