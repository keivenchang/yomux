from pathlib import Path
import re
import subprocess
import sys

import conftest as suite_conftest
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_YOLOMUX_PORTS = ("7770", "7771", "7772", "7773")


def automated_test_source_paths():
    tests_root = REPO_ROOT / "tests"
    python_paths = tests_root.rglob("*.py")
    javascript_paths = tests_root.glob("*.js")
    return sorted({*python_paths, *javascript_paths})


def test_automated_tests_do_not_reference_live_yolomux_ports():
    offenders = []
    for path in automated_test_source_paths():
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for port in LIVE_YOLOMUX_PORTS:
            if re.search(rf"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0):{port}\b|:{port}\b", text):
                offenders.append(f"{path.relative_to(REPO_ROOT)} references live YOLOmux port {port}")

    assert offenders == []


def test_browser_filename_markers_and_selenium_guard_cover_selective_imports(tmp_path):
    assert suite_conftest._automatic_test_markers(Path("test_browser_selective.py")) == ("browser", "socket")
    assert suite_conftest._automatic_test_markers(Path("test_regular.py")) == ()
    selenium_test = tmp_path / "test_selective.py"
    selenium_test.write_text("from selenium.webdriver import Chrome\n", encoding="utf-8")
    assert suite_conftest._test_path_imports_selenium(selenium_test) is True


def test_selenium_guard_allows_explicitly_mixed_browser_and_nonbrowser_module(tmp_path):
    path = tmp_path / "test_mixed.py"
    path.write_text("from selenium.webdriver import Chrome\n", encoding="utf-8")

    class Item:
        def __init__(self, nodeid, markers=(), fixtures=()):
            self.path = path
            self.nodeid = nodeid
            self.markers = list(markers)
            self.fixturenames = list(fixtures)

        def add_marker(self, marker, append=True):
            if append:
                self.markers.append(marker.mark)
            else:
                self.markers.insert(0, marker.mark)

        def get_closest_marker(self, name):
            return next((marker for marker in self.markers if marker.name == name), None)

    items = [
        Item("test_mixed.py::test_unit_contract", (pytest.mark.no_browser.mark,)),
        Item("test_mixed.py::test_browser_contract", (pytest.mark.browser.mark,), ("browser",)),
    ]

    suite_conftest.pytest_collection_modifyitems(None, items)

    assert items[0].get_closest_marker("no_browser") is not None
    assert items[1].get_closest_marker("browser") is not None


def test_selenium_guard_allows_exact_selection_of_explicit_nonbrowser_item(tmp_path):
    path = tmp_path / "test_mixed.py"
    path.write_text("from selenium.webdriver import Chrome\n", encoding="utf-8")

    class Item:
        def __init__(self):
            self.path = path
            self.nodeid = "test_mixed.py::test_unit_contract"
            self.fixturenames = []

        def add_marker(self, _marker, append=True):
            del append

        def get_closest_marker(self, name):
            return pytest.mark.no_browser.mark if name == "no_browser" else None

    suite_conftest.pytest_collection_modifyitems(None, [Item()])


@pytest.mark.parametrize(
    "markers, expected",
    [
        ((), "must carry exactly one of the browser or no_browser markers"),
        (
            (pytest.mark.browser.mark, pytest.mark.no_browser.mark),
            "must carry exactly one of the browser or no_browser markers",
        ),
    ],
)
def test_selenium_guard_rejects_unowned_or_contradictory_mixed_module_item(tmp_path, markers, expected):
    path = tmp_path / "test_mixed.py"
    path.write_text("from selenium.webdriver import Chrome\n", encoding="utf-8")

    class Item:
        def __init__(self, nodeid, item_markers=(), fixtures=()):
            self.path = path
            self.nodeid = nodeid
            self.markers = list(item_markers)
            self.fixturenames = list(fixtures)

        def add_marker(self, marker, append=True):
            if append:
                self.markers.append(marker.mark)
            else:
                self.markers.insert(0, marker.mark)

        def get_closest_marker(self, name):
            return next((marker for marker in self.markers if marker.name == name), None)

    items = [
        Item("test_mixed.py::test_unit_contract", markers),
        Item("test_mixed.py::test_browser_contract", (pytest.mark.browser.mark,), ("browser",)),
    ]

    with pytest.raises(pytest.UsageError, match=expected):
        suite_conftest.pytest_collection_modifyitems(None, items)


def test_live_port_guard_scans_nested_python_and_top_level_javascript():
    paths = {path.relative_to(REPO_ROOT).as_posix() for path in automated_test_source_paths()}
    assert "tests/browser_helpers/browser_layout.py" in paths
    assert "tests/layout_url.test.js" in paths


def test_generated_share_browser_tests_use_isolated_tmux_runtime():
    source = (REPO_ROOT / "tests" / "test_browser_share.py").read_text(encoding="utf-8")
    blocks = re.findall(r"def (test_generated_share_link_[\s\S]*?)(?=\ndef test_|\Z)", source)
    assert blocks, "expected generated-share browser tests to exist"

    for block in blocks:
        name = block.split("(", 1)[0]
        assert "start_isolated_browser_share_app(" in block, f"{name} must create a private tmux/runtime fixture"
        assert 'TmuxWebtermApp(["1"], dangerously_yolo=True)' not in block, f"{name} must not target a live/default tmux session"
        assert "ensureTerminalRunning('1')" not in block, f"{name} must not open hard-coded tmux session 1"
        assert "sessions: ['1']" not in block, f"{name} must not create a share scoped to hard-coded tmux session 1"


def _spawn_fixture_page_path(worker: str, filename: str, sentinel: str) -> str:
    """Return the fixture path a fresh process picks for a fixed worker/seq/filename."""

    code = (
        "import os;"
        f"os.environ['PYTEST_XDIST_WORKER'] = {worker!r};"
        "import tests.browser_helpers.browser_layout as bl;"
        "bl._FIXTURE_PAGE_SEQ = 0;"
        f"print(bl.serve_repo_fixture_page({filename!r}, {sentinel!r}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


@pytest.mark.no_browser
def test_concurrent_lane_fixture_pages_never_collide_on_shared_worker_and_seq():
    """Two processes with identical xdist worker, seq, and filename must not share a fixture path.

    The canonical gate runs e2e/browser/non-browser as separate processes that reuse gw* worker
    names and each restart ``_FIXTURE_PAGE_SEQ`` at 0, so a bare ``<worker>-<seq>-<filename>`` path
    let one lane overwrite another's fixture at the shared REPO_ROOT -- a foreign page then loaded
    (e.g. a stats fixture whose expected bucket ``.start`` was absent, crashing the reader). The
    per-process PID+nonce namespace must keep the paths distinct even under an identical
    worker/seq/filename triple.
    """

    first = _spawn_fixture_page_path("gw0", "collision-regression.html", "sentinel-a")
    second = _spawn_fixture_page_path("gw0", "collision-regression.html", "sentinel-b")
    assert first != second, (first, second)
    assert first.endswith("collision-regression.html"), first
    assert second.endswith("collision-regression.html"), second
