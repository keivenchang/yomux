import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from yolomux_lib import auto_approve_worker
from yolomux_lib import control


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


def test_control_socket_can_release_owned_lock(tmp_path, monkeypatch):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    with tempfile.TemporaryDirectory(prefix=f"ymux-{worker}-", dir="/tmp") as short_root:
        lock_dir = tmp_path / "locks"
        control_dir = Path(short_root) / "control"
        monkeypatch.setattr(auto_approve_worker, "AUTO_APPROVE_LOCK_DIR", lock_dir)
        monkeypatch.setattr(control, "CONTROL_SOCKET_DIR", control_dir)
        script = f"""
import json
import time
from pathlib import Path
from yolomux_lib import auto_approve_worker
from yolomux_lib import control
auto_approve_worker.AUTO_APPROVE_LOCK_DIR = Path({str(lock_dir)!r})
control.CONTROL_SOCKET_DIR = Path({str(control_dir)!r})
released = False
lock = None
def handle(request):
    global released
    if request.get("action") != "disable_auto_approve":
        return {{"ok": False, "error": "unexpected action"}}
    lock.release()
    released = True
    return {{"ok": True, "session": request.get("session"), "enabled": False}}
server = control.YolomuxControlServer(handle)
server.start()
lock = auto_approve_worker.AutoApproveProcessLock("6", owner_extra=server.owner_payload())
started, owner = lock.acquire()
print(json.dumps({{"started": started, "owner": lock.owner, "pid": __import__("os").getpid()}}), flush=True)
deadline = time.time() + 30
while not released and time.time() < deadline:
    time.sleep(0.05)
server.stop()
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
            payload = json.loads(line)
            assert payload["started"] is True
            owner = payload["owner"]
            assert owner["pid"] == child.pid
            assert owner["control_socket"]
            assert auto_approve_worker.auto_approve_lock_owner("6")["pid"] == child.pid

            response = control.send_yolomux_control_request(
                owner,
                {"action": "disable_auto_approve", "session": "6", "requester": {"pid": __import__("os").getpid()}},
            )

            assert response["ok"] is True
            deadline = time.time() + 2
            while time.time() < deadline and auto_approve_worker.auto_approve_lock_owner("6") is not None:
                time.sleep(0.05)
            assert auto_approve_worker.auto_approve_lock_owner("6") is None
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
