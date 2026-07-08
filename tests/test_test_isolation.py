from pathlib import Path
import re

import conftest as suite_conftest


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_YOLOMUX_PORTS = ("7000", "7001", "7002", "7003")


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
