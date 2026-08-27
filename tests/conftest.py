"""Shared pytest configuration + isolation for the yolomux test suite.

Point YOLOMUX_CONFIG_DIR / YOLOMUX_STATE_DIR at FRESH per-run temp dirs *before* any test module
imports `yolomux_lib.common` (which binds CONFIG_DIR / STATE_DIR / SETTINGS_PATH at import time). pytest
imports conftest.py ahead of the test modules, so this is the one place that owns the config/state
location — replacing the `os.environ.setdefault(..., "/tmp/yolomux-test-config")` lines that were
copy-pasted across ~11 test files, and ensuring no test (e.g. the login-locale picker, which writes
general.language) can leave a *persistent* shared config dir mutated across runs.
"""

import importlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time

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
os.environ.setdefault("YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS", "1")


from yolomux_lib import app as app_module
from yolomux_lib import file_index
from yolomux_lib import statusd_protocol
from tools.test_plan import automatic_test_markers
from tools.test_plan import SLOWEST_FIRST_TESTS
from tools.test_plan import test_node_sort_key


@pytest.fixture
def legacy_activity_summary_enabled(monkeypatch):
    """Explicitly admit the retired synchronous algorithm for its isolated contract tests."""

    monkeypatch.setattr(
        statusd_protocol,
        "ACTIVITY_SUMMARY_ADMISSION",
        statusd_protocol.ActivitySummaryAdmission(enabled=True, reason=""),
    )


NONBROWSER_TEST_TIMEOUT_SECONDS = 180
BROWSER_TEST_TIMEOUT_SECONDS = 300
E2E_TEST_TIMEOUT_SECONDS = 600

_SOCKET_AVAILABILITY: tuple[bool, str] | None = None
_SELENIUM_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+selenium(?:\.|\s|$)|pytest\.importorskip\([\"']selenium", re.MULTILINE)


def _test_path(item) -> Path:
    return Path(str(getattr(item, "path", getattr(item, "fspath", ""))))


def _test_path_imports_selenium(path: Path) -> bool:
    if path.suffix != ".py" or not path.is_file():
        return False
    return bool(_SELENIUM_IMPORT_RE.search(path.read_text(encoding="utf-8")))


@pytest.fixture
def no_control_socket(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.YolomuxControlServer, "start", lambda self: None)
    monkeypatch.setattr(app_module.YolomuxControlServer, "stop", lambda self: None)
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations" / "session-files.json")


@pytest.fixture
def make_tmux_webterm_app(monkeypatch):
    """Build full apps with their background/control resources torn down after the test."""
    created = []

    def factory(sessions=("1",), *, dangerously_yolo=False):
        monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
        app = app_module.TmuxWebtermApp(list(sessions), dangerously_yolo=dangerously_yolo)
        created.append(app)
        return app

    yield factory

    for app in created:
        app.background_owner.stop()
        app.control_server.stop()


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
def isolated_tmux_socket(monkeypatch):
    sock_dir = Path(tempfile.mkdtemp(prefix=f"yotmux-{os.getpid()}-", dir="/tmp"))
    monkeypatch.setenv("YOLOMUX_TMUX_SOCKET", str(sock_dir / "s"))
    yield
    try:
        (sock_dir / "s").unlink()
    except FileNotFoundError:
        pass
    try:
        sock_dir.rmdir()
    except OSError:
        pass


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
    with file_index.FileIndexTestScope():
        yield


@pytest.fixture(autouse=True)
def reset_worker_reused_browser(request):
    """Run the shared browser reset for every consumer, independent of module imports."""

    yield
    if request.node.funcargs.get("browser") is None:
        return
    # Load lazily so collection of non-browser tests does not require Selenium.
    browser_layout = importlib.import_module("tests.browser_helpers.browser_layout")
    browser_layout.reset_reused_browser_after_test(request)


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = _test_path(item)
        for marker_name in automatic_test_markers(path):
            item.add_marker(getattr(pytest.mark, marker_name))

    selenium_paths = {_test_path(item) for item in items if _test_path_imports_selenium(_test_path(item))}
    for item in items:
        path = _test_path(item)
        browser_marker = item.get_closest_marker("browser")
        no_browser_marker = item.get_closest_marker("no_browser")
        if path in selenium_paths and (browser_marker is None) == (no_browser_marker is None):
            raise pytest.UsageError(
                f"{item.nodeid}: Selenium-module tests must carry exactly one of the browser or no_browser markers"
            )
        if (
            "browser" in getattr(item, "fixturenames", ())
            and browser_marker is None
        ):
            raise pytest.UsageError(f"{item.nodeid}: tests using the browser fixture must carry the browser marker")
        timeout_seconds = (
            E2E_TEST_TIMEOUT_SECONDS
            if item.get_closest_marker("e2e") is not None
            else BROWSER_TEST_TIMEOUT_SECONDS
            if item.get_closest_marker("browser") is not None
            else NONBROWSER_TEST_TIMEOUT_SECONDS
        )
        timeout_options = {"method": "thread"} if item.get_closest_marker("node_vm") is not None else {}
        item.add_marker(pytest.mark.timeout(timeout_seconds, **timeout_options), append=False)

    indexed = list(enumerate(items))

    indexed.sort(key=lambda pair: test_node_sort_key(pair[1].nodeid, pair[0]))
    items[:] = [item for _original_index, item in indexed]


def pytest_runtest_setup(item):
    if item.get_closest_marker("socket") is None:
        return
    socket_ok, reason = local_socket_capability()
    if not socket_ok:
        pytest.skip(reason)


# Defect 2 attribution. Browser reuse is one Chrome per xdist worker for the whole session, so the
# predecessors a test actually shares a browser with are decided by xdist sharding, not by file
# order -- and nothing in a normal run records that. This hook is the only route that crosses the
# test-container boundary: docker/run-tests.sh forwards a fixed environment allowlist, and
# YOLOMUX_E2E_EVIDENCE_DIR is the one path bind-mounted at an identical absolute path on both
# sides. It is off unless tools/defect2_harness.py has created the directory, so an ordinary run
# writes nothing and pays one directory check per test.
def _defect2_attribution_path():
    evidence_dir = os.environ.get("YOLOMUX_E2E_EVIDENCE_DIR", "")
    if not evidence_dir:
        return None
    directory = Path(evidence_dir) / "defect2-attempt"
    if not directory.is_dir():
        return None
    return directory / f"worker-{os.environ.get('PYTEST_XDIST_WORKER') or 'master'}.jsonl"


def pytest_runtest_logreport(report):
    if report.when != "call" and not (report.when == "setup" and report.outcome != "passed"):
        return
    # Under xdist the controller ALSO receives every worker's report, with `node` set to the
    # worker it came from. Writing there would duplicate every row into a `master` file that
    # carries no worker attribution -- which is the one thing this hook exists to record. Only
    # the process that actually ran the test writes.
    if getattr(report, "node", None) is not None:
        return
    path = _defect2_attribution_path()
    if path is None:
        return
    row = {
        "nodeid": report.nodeid,
        "worker": os.environ.get("PYTEST_XDIST_WORKER") or "master",
        "phase": report.when,
        "outcome": report.outcome,
        "duration": round(float(getattr(report, "duration", 0.0)), 6),
        "start": float(getattr(report, "start", 0.0)) or time.time(),
        "pid": os.getpid(),
    }
    # Never let attribution break the run it is observing: a full disk or a vanished mount is a
    # lost record, not a failed test.
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        return
