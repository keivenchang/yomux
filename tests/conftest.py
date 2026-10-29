"""Shared pytest configuration + isolation for the yolomux test suite.

Point YOMUX_CONFIG_DIR / YOMUX_STATE_DIR at FRESH per-run temp dirs *before* any test module
imports `yomux_lib.common` (which binds CONFIG_DIR / STATE_DIR / SETTINGS_PATH at import time). pytest
imports conftest.py ahead of the test modules, so this is the one place that owns the config/state
location — replacing the `os.environ.setdefault(..., "/tmp/yomux-test-config")` lines that were
copy-pasted across ~11 test files, and ensuring no test (e.g. the login-locale picker, which writes
general.language) can leave a *persistent* shared config dir mutated across runs.
"""

import atexit
from functools import partial
import importlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import uuid

import pytest
from selenium.common.exceptions import WebDriverException
from tools.product_environment import product_env
from tools.tmux_paths import short_temp_base
from tools.product_environment import product_env_name
from tools.product_environment import product_env_names

_attribution_json_dumps = json.dumps
_attribution_path_key = pytest.StashKey[Path | None]()

# This suite's fixtures explicitly select legacy YOLOMUX_* names in several
# tests. Remove canonical ambient root aliases so those fixtures exercise the
# requested compatibility path instead of inheriting the test container's
# canonical per-worker values.
for _name in (
    "ROOT",
    "CONFIG_DIR",
    "STATE_DIR",
    "CACHE_DIR",
    "RUNTIME_DIR",
    "CODEX_HOME",
    "HOST_ARTIFACT_DIR",
    "START_LOCK_DIR",
    "TOOL_LOCK_PATH",
    "CA_DIR",
    "LOG_DIR",
):
    os.environ.pop(product_env_name(_name), None)


@pytest.fixture(autouse=True)
def clear_ambient_canonical_product_roots(monkeypatch):
    """Keep each test's legacy-root setup independent of another test's canonical overrides."""
    for _name in (
        "ROOT",
        "CONFIG_DIR",
        "STATE_DIR",
        "CACHE_DIR",
        "RUNTIME_DIR",
        "CODEX_HOME",
        "HOST_ARTIFACT_DIR",
        "START_LOCK_DIR",
        "TOOL_LOCK_PATH",
        "CA_DIR",
        "LOG_DIR",
    ):
        monkeypatch.delenv(product_env_names(_name)[0], raising=False)

from yomux_lib.infra.transient_paths import create_run_root
from yomux_lib.infra.transient_paths import remove_run_root

# Each process needs its OWN config/state dir. Under pytest-xdist, worker subprocesses INHERIT the
# parent's environment, so a plain setdefault makes every parallel worker share ONE YOMUX_CONFIG_DIR
# -> one state.json. Concurrent TmuxWebtermApp construction in different workers then prunes each
# other's session summaries out of that shared file (prune_yoagent_session_summaries keeps only its
# own sessions), a ~6% KeyError flake under `-n auto`. Give each xdist worker a distinct dir; keep
# setdefault's external override (CI/dev) for the serial / controller process.
_inherited_test_root = (product_env("TEST_ROOT", "") or "").strip()
if _inherited_test_root:
    _test_run_root = None
    _test_process_root = Path(_inherited_test_root) / f"p{os.getpid()}"
else:
    _test_run_root = create_run_root(namespace="test", owner_role="pytest-controller", temporary_base=short_temp_base().resolve())
    _test_process_root = _test_run_root.child("pytest")
    os.environ[product_env_name("TEST_ROOT")] = str(_test_run_root.path)
_test_config_dir = _test_process_root / "config"
_test_state_dir = _test_process_root / "state"
_test_process_root.mkdir(mode=0o700, exist_ok=True)
_test_config_dir.mkdir(mode=0o700, exist_ok=True)
_test_state_dir.mkdir(mode=0o700, exist_ok=True)
os.environ[product_env_name("CONFIG_DIR")] = str(_test_config_dir)
os.environ[product_env_name("STATE_DIR")] = str(_test_state_dir)
if _test_run_root is not None:
    atexit.register(partial(remove_run_root, _test_run_root))

os.environ.setdefault(product_env_name("LOCAL_SERVICE_IDLE_SECONDS"), "1")


from tools.test_plan import automatic_test_markers
from tools.test_plan import SLOWEST_FIRST_TESTS
from tools.test_plan import test_node_sort_key


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
def isolated_tmux_socket(monkeypatch):
    socket_parent = Path(product_env("TEST_ROOT", "") or "") / "tmux"
    socket_parent.mkdir(mode=0o700, exist_ok=True)
    sock_dir = Path(tempfile.mkdtemp(prefix=f"c-{os.getpid()}-{uuid.uuid4().hex[:4]}-", dir=socket_parent))
    for _name in product_env_names("TMUX_SOCKET"):
        monkeypatch.setenv(_name, str(sock_dir / "s"))
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
# YOMUX_E2E_EVIDENCE_DIR is the one path bind-mounted at an identical absolute path on both
# sides. Every gate container provisions this path; the harness reuses the same recorder.
def _defect2_attribution_path():
    evidence_dir = product_env("E2E_EVIDENCE_DIR", "") or ""
    if not evidence_dir:
        return None
    directory = Path(evidence_dir) / "defect2-attempt"
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
    _write_test_attribution(row)


def _write_test_attribution(row, path=None):
    path = path if path is not None else _defect2_attribution_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_attribution_json_dumps(row, sort_keys=True) + "\n")
    except OSError as error:
        print(f"Test attribution unavailable at {path}: {error}", file=sys.stderr)


def pytest_runtest_call(item):
    path = _defect2_attribution_path()
    item.stash[_attribution_path_key] = path
    lease = item.funcargs.get("browser")
    _write_test_attribution({"event": "start", "nodeid": item.nodeid,
                             "worker": os.environ.get("PYTEST_XDIST_WORKER") or "master",
                             "browser_generation": lease.generation if lease else None,
                             "pid": os.getpid(), "at": time.time()}, path)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.outcome == "skipped" and item.get_closest_marker("real_agent_urls") is not None:
        report.outcome = "failed"
        report.longrepr = f"{item.nodeid}: required real-client URL sample did not run: {report.longrepr}"
    if report.when != "call":
        return
    path = item.stash.get(_attribution_path_key, _defect2_attribution_path())
    if path is None:
        return
    lease = item.funcargs.get("browser")
    row = {"event": "finish", "nodeid": item.nodeid, "outcome": report.outcome,
           "worker": os.environ.get("PYTEST_XDIST_WORKER") or "master",
           "browser_generation": lease.generation if lease else None,
           "pid": os.getpid(), "at": time.time()}
    if report.user_properties:
        row["properties"] = dict(report.user_properties)
    if report.failed and lease:
        try:
            row["browser_history"] = lease.execute_script("""
              return {finder:typeof fileTreeRenderHistory === 'undefined' ? [] : fileTreeRenderHistory,
                dockview:window.__tabSwitchPerf?.fullRefreshStacks || window.__tabSwitchRefreshHistory || []};
            """)
        except WebDriverException as error:
            row["browser_history_error"] = {"reason": "webdriver_unavailable", "detail": str(error)}
    _write_test_attribution(row, path)
