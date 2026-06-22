"""Shared pytest configuration + isolation for the yolomux test suite.

Point YOLOMUX_CONFIG_DIR / YOLOMUX_STATE_DIR at FRESH per-run temp dirs *before* any test module
imports `yolomux_lib.common` (which binds CONFIG_DIR / STATE_DIR / SETTINGS_PATH at import time). pytest
imports conftest.py ahead of the test modules, so this is the one place that owns the config/state
location — replacing the `os.environ.setdefault(..., "/tmp/yolomux-test-config")` lines that were
copy-pasted across ~11 test files, and ensuring no test (e.g. the login-locale picker, which writes
general.language) can leave a *persistent* shared config dir mutated across runs.
"""

import os
import socket
import tempfile

import pytest

from yolomux_lib import app as app_module

# Each process needs its OWN config/state dir. Under pytest-xdist, worker subprocesses INHERIT the
# parent's environment, so a plain setdefault makes every parallel worker share ONE YOLOMUX_CONFIG_DIR
# -> one state.json. Concurrent TmuxWebtermApp construction in different workers then prunes each
# other's session summaries out of that shared file (prune_yoagent_session_summaries keeps only its
# own sessions), a ~6% KeyError flake under `-n auto`. Give each xdist worker a distinct dir; keep
# setdefault's external override (CI/dev) for the serial / controller process.
_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER")
for _env_var, _prefix in (("YOLOMUX_CONFIG_DIR", "yolomux-test-config-"), ("YOLOMUX_STATE_DIR", "yolomux-test-state-")):
    if _xdist_worker:
        os.environ[_env_var] = tempfile.mkdtemp(prefix=f"{_prefix}{_xdist_worker}-")
    else:
        os.environ.setdefault(_env_var, tempfile.mkdtemp(prefix=_prefix))


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

_SOCKET_AVAILABILITY: tuple[bool, str] | None = None


@pytest.fixture
def no_control_socket(monkeypatch):
    monkeypatch.setattr(app_module.YolomuxControlServer, "start", lambda self: None)
    monkeypatch.setattr(app_module.YolomuxControlServer, "stop", lambda self: None)


@pytest.fixture
def isolated_yoagent_conversation_state(monkeypatch, tmp_path):
    state_dir = tmp_path / "yoagent-state"
    monkeypatch.setattr(app_module.yoagent_conversation, "YOAGENT_CONVERSATION_PATH", state_dir / "conversation.jsonl")
    monkeypatch.setattr(app_module.yoagent_conversation, "YOAGENT_CLI_STATE_PATH", state_dir / "cli-sessions.json")
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module, "EVENT_LOG_PATH", tmp_path / "events.jsonl")


def local_socket_capability() -> tuple[bool, str]:
    global _SOCKET_AVAILABILITY
    if _SOCKET_AVAILABILITY is not None:
        return _SOCKET_AVAILABILITY
    try:
        bind_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            bind_probe.bind(("127.0.0.1", 0))
        finally:
            bind_probe.close()
        left, right = socket.socketpair()
        try:
            right.sendall(b"Y")
            if left.recv(1) != b"Y":
                raise OSError("socketpair probe returned unexpected payload")
        finally:
            left.close()
            right.close()
    except (OSError, PermissionError) as exc:
        _SOCKET_AVAILABILITY = (False, f"local sockets are blocked in this sandbox: {exc}")
        return _SOCKET_AVAILABILITY
    _SOCKET_AVAILABILITY = (True, "")
    return _SOCKET_AVAILABILITY


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


def pytest_runtest_setup(item):
    if item.get_closest_marker("socket") is None:
        return
    socket_ok, reason = local_socket_capability()
    if not socket_ok:
        pytest.skip(reason)
