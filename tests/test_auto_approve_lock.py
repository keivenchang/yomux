import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from yolomux_lib import auto_approve_worker

pytestmark = pytest.mark.socket


def test_auto_approve_lock_blocks_second_process(tmp_path, monkeypatch):
    lock_dir = tmp_path / "locks"
    monkeypatch.setattr(auto_approve_worker, "AUTO_APPROVE_LOCK_DIR", lock_dir)
    script = f"""
import json
import time
from pathlib import Path
from yolomux_lib import auto_approve_worker
auto_approve_worker.AUTO_APPROVE_LOCK_DIR = Path({str(lock_dir)!r})
lock = auto_approve_worker.AutoApproveProcessLock("6")
started, owner = lock.acquire()
print(json.dumps({{"started": started, "owner": owner, "pid": __import__("os").getpid()}}), flush=True)
time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = child.stdout.readline()
        assert json.loads(line)["started"] is True

        owner = None
        deadline = time.time() + 2
        while time.time() < deadline:
            owner = auto_approve_worker.auto_approve_lock_owner("6")
            if owner:
                break
            time.sleep(0.05)

        assert owner is not None
        assert owner["target"] == "6"
        assert owner["pid"] == child.pid
        second = auto_approve_worker.AutoApproveProcessLock("6")
        started, second_owner = second.acquire()
        assert started is False
        assert second_owner["pid"] == child.pid
    finally:
        child.terminate()
        child.wait(timeout=5)
