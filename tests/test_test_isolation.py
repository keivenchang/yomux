from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_YOLOMUX_PORTS = ("8001", "7777")


def test_automated_tests_do_not_reference_live_yolomux_ports():
    offenders = []
    for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for port in LIVE_YOLOMUX_PORTS:
            if re.search(rf"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0):{port}\b|:{port}\b", text):
                offenders.append(f"{path.relative_to(REPO_ROOT)} references live YOLOmux port {port}")

    assert offenders == []


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
