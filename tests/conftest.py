"""Shared pytest configuration + isolation for the yolomux test suite.

Point YOLOMUX_CONFIG_DIR / YOLOMUX_STATE_DIR at FRESH per-run temp dirs *before* any test module
imports `yolomux_lib.common` (which binds CONFIG_DIR / STATE_DIR / SETTINGS_PATH at import time). pytest
imports conftest.py ahead of the test modules, so this is the one place that owns the config/state
location — replacing the `os.environ.setdefault(..., "/tmp/yolomux-test-config")` lines that were
copy-pasted across ~11 test files, and ensuring no test (e.g. the login-locale picker, which writes
general.language) can leave a *persistent* shared config dir mutated across runs.
"""

import os
from pathlib import Path
import re
import socket
import tempfile

import pytest

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


from yolomux_lib import app as app_module
from yolomux_lib import file_index


SLOWEST_FIRST_TESTS = (
    "tests/test_browser_share.py::test_generated_share_link_mirrors_interactive_ui_surface_matrix",
    "tests/test_browser_dockview.py::test_dockview_wrapped_tab_rows_share_one_control_reserved_flex_grid",
    "tests/test_browser_dockview.py::test_differ_reopen_keeps_dragged_file_tab_home",
    "tests/test_browser_layout.py::test_mock_agent_prompt_payload_renders_ask_attention_in_live_browser",
    "tests/test_browser_dockview.py::test_dockview_yellow_window_ball_click_switches_and_acknowledges",
    "tests/test_node_suite.py::test_node_layout_suite_passes",
)

SLOWEST_FIRST_RANK = {nodeid: index for index, nodeid in enumerate(SLOWEST_FIRST_TESTS)}

_SOCKET_AVAILABILITY: tuple[bool, str] | None = None
_SELENIUM_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+selenium(?:\.|\s|$)|pytest\.importorskip\([\"']selenium", re.MULTILINE)


def _test_path(item) -> Path:
    return Path(str(getattr(item, "path", getattr(item, "fspath", ""))))


def _automatic_test_markers(path: Path) -> tuple[str, ...]:
    return ("browser", "socket") if path.name.startswith("test_browser_") else ()


def _test_path_imports_selenium(path: Path) -> bool:
    if path.suffix != ".py" or not path.is_file():
        return False
    return bool(_SELENIUM_IMPORT_RE.search(path.read_text(encoding="utf-8")))


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
    monkeypatch.setattr(app_module, "ACTIVITY_PATH", tmp_path / "activity.json")
    monkeypatch.setattr(app_module, "ACTIVITY_HEARTBEATS_PATH", tmp_path / "activity-heartbeats.jsonl")


@pytest.fixture
def isolated_tmux_socket(monkeypatch, tmp_path):
    sock_dir = tmp_path / "tmux"
    sock_dir.mkdir()
    monkeypatch.setenv("YOLOMUX_TMUX_SOCKET", str(sock_dir / "s"))


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


@pytest.fixture(autouse=True)
def isolated_file_index_background_hooks(monkeypatch):
    # The real indexer is intentionally detached and persistent. Test cases
    # use a temporary state directory that pytest removes at process exit, so
    # never leave a detached child pointed at that vanished state behind.
    monkeypatch.setattr(app_module.SearchIndexerClient, "ensure_started", lambda self: False)
    file_index.set_background_owner_checker(None)
    file_index.set_background_owner_refresh_requester(None)
    file_index.set_background_owner_bytes_recorder(None)
    file_index.set_background_owner_done_notifier(None)
    file_index.clear_memory_indexes()
    yield
    file_index.set_background_owner_checker(None)
    file_index.set_background_owner_refresh_requester(None)
    file_index.set_background_owner_bytes_recorder(None)
    file_index.set_background_owner_done_notifier(None)
    file_index.clear_memory_indexes()


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = _test_path(item)
        for marker_name in _automatic_test_markers(path):
            item.add_marker(getattr(pytest.mark, marker_name))
        if _test_path_imports_selenium(path) and item.get_closest_marker("browser") is None:
            raise pytest.UsageError(f"{path}: Selenium tests must carry the browser marker")

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
