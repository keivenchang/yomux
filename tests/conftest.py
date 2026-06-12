"""Shared pytest configuration + isolation for the yolomux test suite.

Point YOLOMUX_CONFIG_DIR / YOLOMUX_STATE_DIR at FRESH per-run temp dirs *before* any test module
imports `yolomux_lib.common` (which binds CONFIG_DIR / STATE_DIR / SETTINGS_PATH at import time). pytest
imports conftest.py ahead of the test modules, so this is the one place that owns the config/state
location — replacing the `os.environ.setdefault(..., "/tmp/yolomux-test-config")` lines that were
copy-pasted across ~11 test files, and ensuring no test (e.g. the login-locale picker, which writes
general.language) can leave a *persistent* shared config dir mutated across runs.
"""

import os
import tempfile

# setdefault so an explicit external override (CI, a developer) still wins; otherwise use a unique
# per-run temp dir that is naturally discarded between runs.
os.environ.setdefault("YOLOMUX_CONFIG_DIR", tempfile.mkdtemp(prefix="yolomux-test-config-"))
os.environ.setdefault("YOLOMUX_STATE_DIR", tempfile.mkdtemp(prefix="yolomux-test-state-"))


SLOWEST_FIRST_TESTS = (
    "tests/test_browser_layout.py::test_dockview_drag_splits_tab_to_right_pane_and_measures_geometry",
    "tests/test_browser_layout.py::test_preview_popout_toolbar_and_state_sync",
    "tests/test_browser_layout.py::test_dockview_pinned_tab_invalid_non_pinned_target_shows_red_dashes",
    "tests/test_browser_layout.py::test_dockview_root_left_drag_shows_full_span_preview_before_drop",
    "tests/test_browser_layout.py::test_preferences_scroll_defers_passive_rerender",
    "tests/test_browser_layout.py::test_platform_controls_use_pc_glyphs",
    "tests/test_browser_layout.py::test_dockview_tab_hover_shows_session_detail_popover",
    "tests/test_browser_layout.py::test_sync_mode_quick_access_does_not_snap_back_until_explicit_input",
    "tests/test_browser_layout.py::test_dockview_tab_container_background_swaps_whole_panes",
    "tests/test_browser_layout.py::test_dockview_drag_reorders_tabs_in_same_pane",
    "tests/test_browser_layout.py::test_diff_overview_matches_actual_file_explorer_visible_rows_after_scroll",
    "tests/test_browser_layout.py::test_editor_search_button_toggles_pressed_state_with_codemirror_panel",
    "tests/test_browser_layout.py::test_dockview_first_pinned_tab_drags_after_second_pinned_tab",
    "tests/test_browser_layout.py::test_dockview_root_top_drag_preview_preserves_docked_finder_column",
    "tests/test_browser_layout.py::test_sync_mode_opens_common_repo_parent_and_expands_affected_dirs",
    "tests/test_node_suite.py::test_node_layout_suite_passes",
)

SLOWEST_FIRST_RANK = {nodeid: index for index, nodeid in enumerate(SLOWEST_FIRST_TESTS)}


def pytest_collection_modifyitems(config, items):
    indexed = list(enumerate(items))

    def sort_key(pair):
        original_index, item = pair
        base_nodeid = item.nodeid.split("[", 1)[0]
        rank = SLOWEST_FIRST_RANK.get(item.nodeid, SLOWEST_FIRST_RANK.get(base_nodeid))
        if rank is None:
            return (1, original_index)
        return (0, rank, original_index)

    indexed.sort(key=sort_key)
    items[:] = [item for _original_index, item in indexed]
