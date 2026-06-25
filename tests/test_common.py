from pathlib import Path
import signal
import subprocess

from yolomux_lib import common


def test_project_git_normalizes_missing_and_malformed_git_payloads():
    git = {"root": "/repo", "branch": "main"}

    assert common.project_git({"git": git}) is git
    assert common.project_git({"git": None}) == {}
    assert common.project_git({"git": "not-a-dict"}) == {}
    assert common.project_git({}) == {}
    assert common.project_git(None) == {}


def test_project_git_helper_owns_project_git_extraction_sites():
    root = Path(common.PROJECT_ROOT)
    offenders = []
    for relative in ("yolomux_lib/activity_summary.py", "yolomux_lib/app.py"):
        source = (root / relative).read_text(encoding="utf-8")
        if 'project.get("git")' in source or "project.get('git')" in source:
            offenders.append(relative)

    assert offenders == []


def test_terminate_process_group_waits_after_sigkill(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 12345
        waits = 0

        def poll(self):
            return None

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("cmd", timeout)
            return 0

    monkeypatch.setattr(common.os, "killpg", lambda pid, sig: calls.append(("killpg", pid, sig)))

    common.terminate_process_group(FakeProcess())

    assert calls == [
        ("killpg", 12345, signal.SIGTERM),
        ("wait", 2.0),
        ("killpg", 12345, signal.SIGKILL),
        ("wait", 2.0),
    ]
