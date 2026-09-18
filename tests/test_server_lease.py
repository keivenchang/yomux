import json
from pathlib import Path
import subprocess
import sys
import time
import pytest

from tests.gate_harness import HttpPortLease
from tests.isolated_dev_server import build_environment
from tests.isolated_dev_server import build_paths
from tests.isolated_dev_server import start_isolated_dev_server
from tests.isolated_dev_server import stop_and_reap_daemons
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib.infra.root_paths import YolomuxRoots
from yolomux_lib.server_lease import acquire_server_port_lease
from yolomux_lib.server_lease import acquire_instance_and_port_leases
from yolomux_lib.server_lease import acquire_instance_root_lease
from yolomux_lib.server_lease import InstanceLeaseError


def test_server_port_lease_allows_exactly_one_live_owner(tmp_path):
    first = acquire_server_port_lease(9123, state_dir=tmp_path)
    assert first is not None
    record = json.loads(first.path.read_text(encoding="utf-8"))
    assert record["stable_host_id"]
    assert record["hostname"]
    assert record["boot_id"]
    assert record["process_start_identity"]
    assert record["process_start_ticks"] > 0
    assert record["instance_nonce"]
    assert acquire_server_port_lease(9123, state_dir=tmp_path) is None
    assert json.loads(first.path.read_text(encoding="utf-8")) == record

    first.release()

    second = acquire_server_port_lease(9123, state_dir=tmp_path)
    assert second is not None
    second.release()


def test_server_port_lease_is_keyed_by_host_identity(tmp_path):
    identity = HostIdentity("host-a", "host-a", "boot-a", 2, "proc:2", 2, "nonce-a", "fixture")
    lease = acquire_server_port_lease(9123, state_dir=tmp_path, host_identity=identity)
    assert lease is not None
    try:
        assert lease.path == tmp_path / "server-leases" / "host-a" / "9123.lock"
    finally:
        lease.release()


def test_server_port_lease_reuses_an_unlocked_owner_file(tmp_path):
    identity = HostIdentity("host-a", "host-a", "boot-a", 2, "proc:2", 2, "nonce-a", "fixture")
    path = tmp_path / "server-leases" / identity.stable_host_id / "9123.lock"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"old": "record"}) + "\n", encoding="utf-8")

    lease = acquire_server_port_lease(9123, state_dir=tmp_path, host_identity=identity)

    assert lease is not None
    try:
        assert json.loads(path.read_text(encoding="utf-8"))["pid"] == identity.pid
    finally:
        lease.release()


def test_instance_root_lease_blocks_a_second_server_on_another_port(tmp_path):
    first = acquire_instance_root_lease(tmp_path / "root")
    assert first is not None
    try:
        assert acquire_instance_root_lease(tmp_path / "root") is None
    finally:
        first.release()


def test_instance_root_lease_identity_covers_the_complete_path_tuple(tmp_path):
    roots = YolomuxRoots(
        config_dir=tmp_path / "config",
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
        codex_home=tmp_path / "codex",
        runtime_dir=tmp_path / "runtime",
    )
    first = acquire_instance_root_lease(roots)
    assert first is not None
    try:
        record = json.loads(first.path.read_text(encoding="utf-8"))
        assert record["paths"] == {
            "config": str(roots.config_dir),
            "state": str(roots.state_dir),
            "cache": str(roots.cache_dir),
            "runtime": str(roots.runtime_dir),
        }
        assert acquire_instance_root_lease(roots) is None
        changed = YolomuxRoots(
            config_dir=roots.config_dir,
            state_dir=roots.state_dir,
            cache_dir=tmp_path / "different-cache",
            codex_home=roots.codex_home,
            runtime_dir=roots.runtime_dir,
        )
        replacement = acquire_instance_root_lease(changed)
        assert replacement is not None
        replacement.release()
    finally:
        first.release()


def test_instance_root_lease_force_stops_verified_owner(tmp_path):
    root = tmp_path / "root"
    ready = tmp_path / "ready"
    code = (
        "import time; "
        "from pathlib import Path; "
        "from yolomux_lib.server_lease import acquire_instance_root_lease; "
        "lease = acquire_instance_root_lease(Path(__import__('sys').argv[1])); "
        "Path(__import__('sys').argv[2]).write_text('ready'); "
        "time.sleep(60)"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(root), str(ready)], cwd=Path(__file__).resolve().parents[1])
    try:
        deadline = time.monotonic() + 5.0
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        replacement = acquire_instance_root_lease(root, force=True)
        assert replacement is not None
        replacement.release()
        child.wait(timeout=5.0)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5.0)


def test_force_does_not_stop_root_owner_when_requested_port_is_foreign(tmp_path):
    roots = YolomuxRoots(
        config_dir=tmp_path / "config",
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
        codex_home=tmp_path / "codex",
        runtime_dir=tmp_path / "runtime",
    )
    ready = tmp_path / "ready"
    code = (
        "import time; "
        "from pathlib import Path; "
        "from yolomux_lib.infra.root_paths import YolomuxRoots; "
        "from yolomux_lib.server_lease import acquire_instance_root_lease; "
        "a,b,c,d,e=map(Path,__import__('sys').argv[1:6]); "
        "lease=acquire_instance_root_lease(YolomuxRoots(config_dir=a,state_dir=b,cache_dir=c,codex_home=d,runtime_dir=e)); "
        "Path(__import__('sys').argv[6]).write_text('ready'); "
        "time.sleep(60)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(roots.config_dir), str(roots.state_dir), str(roots.cache_dir), str(roots.codex_home), str(roots.runtime_dir), str(ready)],
        cwd=Path(__file__).resolve().parents[1],
    )
    foreign_port = acquire_server_port_lease(9124, state_dir=roots.runtime_dir)
    try:
        deadline = time.monotonic() + 5.0
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        with pytest.raises(InstanceLeaseError, match="requested port 9124 is owned by a different"):
            acquire_instance_and_port_leases(roots, 9124, force=True)
        assert child.poll() is None
    finally:
        if foreign_port is not None:
            foreign_port.release()
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5.0)


def test_process_level_root_lease_refuses_second_server_on_another_port(monkeypatch, tmp_path):
    source_root = Path(__file__).resolve().parents[1]
    paths = build_paths(tmp_path / "server")
    runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path / "tmux", session_count=1)
    first = None
    second = None
    try:
        first = start_isolated_dev_server("root-lease-owner", source_root, paths, runtime)
        second_port_lease = HttpPortLease.reserve()
        second_port = second_port_lease.release()
        second = subprocess.run(
            [
                sys.executable,
                "-u",
                str(source_root / "yolomux.py"),
                "--http",
                "--host",
                "127.0.0.1",
                "--port",
                str(second_port),
                "--sessions",
                runtime.sessions[0],
            ],
            cwd=source_root,
            env=build_environment(source_root, paths, runtime, second_port),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    finally:
        if first is not None:
            stop_and_reap_daemons(first)
        stop_isolated_tmux_runtime(runtime)

    assert second is not None
    assert second.returncode == 1
    assert "Another YOLOmux instance is already running" in second.stderr
