import signal
import subprocess

from yolomux_lib import common


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
