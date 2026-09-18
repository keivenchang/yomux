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
from yolomux_lib import server_lease as server_lease_module
from yolomux_lib.server_lease import acquire_server_port_lease
from yolomux_lib.server_lease import acquire_instance_and_port_leases
from yolomux_lib.server_lease import acquire_instance_root_lease
from yolomux_lib.server_lease import InstanceLeaseError


def _roots(tmp_path, prefix=""):
    base = tmp_path / prefix if prefix else tmp_path
    return YolomuxRoots(
        config_dir=base / "config",
        state_dir=base / "state",
        cache_dir=base / "cache",
        codex_home=base / "codex",
        runtime_dir=base / "runtime",
    )


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
    roots = _roots(tmp_path)
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
        assert acquire_instance_root_lease(changed) is None
        disjoint = _roots(tmp_path, "different")
        replacement = acquire_instance_root_lease(disjoint)
        assert replacement is not None
        replacement.release()
    finally:
        first.release()


def test_instance_root_lease_blocks_any_shared_mutable_path(tmp_path):
    roots = _roots(tmp_path)
    first = acquire_instance_root_lease(roots)
    assert first is not None
    try:
        overlapping = YolomuxRoots(
            config_dir=tmp_path / "other-config",
            state_dir=roots.state_dir,
            cache_dir=tmp_path / "other-cache",
            codex_home=tmp_path / "other-codex",
            runtime_dir=roots.runtime_dir,
        )
        assert acquire_instance_root_lease(overlapping) is None
    finally:
        first.release()


def test_combined_lease_releases_root_when_port_is_already_owned(tmp_path):
    roots = _roots(tmp_path)
    port_owner = acquire_server_port_lease(9125, state_dir=roots.runtime_dir)
    assert port_owner is not None
    try:
        root_lease, port_lease = acquire_instance_and_port_leases(roots, 9125)
        assert root_lease is None
        assert port_lease is None

        # A failed combined acquisition must not strand a partial root claim behind the port conflict.
        replacement = acquire_instance_root_lease(roots)
        assert replacement is not None
        replacement.release()
    finally:
        port_owner.release()


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


def test_combined_force_waits_for_owner_exit_after_early_lease_release(tmp_path):
    roots = _roots(tmp_path)
    ready = tmp_path / "ready"
    released = tmp_path / "released"
    code = """import signal
import time
from pathlib import Path
from yolomux_lib.infra.root_paths import YolomuxRoots
from yolomux_lib.server_lease import acquire_instance_and_port_leases

a, b, c, d, e = map(Path, __import__('sys').argv[1:6])
ready_path = Path(__import__('sys').argv[6])
released_path = Path(__import__('sys').argv[7])
leases = acquire_instance_and_port_leases(
    YolomuxRoots(config_dir=a, state_dir=b, cache_dir=c, codex_home=d, runtime_dir=e),
    9126,
)

def release(_signum, _frame):
    leases[0].release()
    leases[1].release()
    released_path.write_text('released')
    time.sleep(60)

signal.signal(signal.SIGTERM, release)
ready_path.write_text('ready')
time.sleep(60)
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(roots.config_dir),
            str(roots.state_dir),
            str(roots.cache_dir),
            str(roots.codex_home),
            str(roots.runtime_dir),
            str(ready),
            str(released),
        ],
        cwd=Path(__file__).resolve().parents[1],
    )
    replacement = None
    try:
        deadline = time.monotonic() + 5.0
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        replacement = acquire_instance_and_port_leases(roots, 9126, force=True)
        assert replacement[0] is not None and replacement[1] is not None
        assert released.exists()
        assert child.poll() is not None
    finally:
        if replacement is not None:
            for lease in replacement:
                if lease is not None:
                    lease.release()
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5.0)


def test_force_preflights_all_blocked_paths_before_stopping_any_owner(monkeypatch, tmp_path):
    first_path = tmp_path / "first" / "instance.lock"
    second_path = tmp_path / "second" / "instance.lock"
    first_record = {"owner": "first"}
    second_record = {"owner": "second"}
    records = {first_path: first_record, second_path: second_record}
    monkeypatch.setattr(server_lease_module, "_try_lock_instance_path", lambda _path: None)
    monkeypatch.setattr(server_lease_module, "_read_unlocked_instance_record", records.get)
    monkeypatch.setattr(server_lease_module, "_verified_instance_owner", lambda record, _identity: record is first_record)
    monkeypatch.setattr(
        server_lease_module,
        "_force_stop_instance_owner",
        lambda *_args, **_kwargs: pytest.fail("force must preflight every blocker before signaling"),
    )

    with pytest.raises(InstanceLeaseError, match="cannot force-start"):
        server_lease_module._acquire_instance_locks(
            (first_path, second_path),
            force=True,
            identity=HostIdentity("host", "host", "boot", 2, "proc:2", 2, "nonce", "fixture"),
        )


def test_force_rechecks_every_verified_blocker_before_signaling_any_owner(monkeypatch, tmp_path):
    first_path = tmp_path / "first" / "instance.lock"
    second_path = tmp_path / "second" / "instance.lock"
    first_record = {"owner": "first"}
    second_record = {"owner": "second", "pid": "2"}
    changed_second = {"owner": "replacement", "pid": "3"}
    reads = {first_path: 0, second_path: 0}

    def read_record(path):
        reads[path] += 1
        if path == second_path and reads[path] > 1:
            return changed_second
        return {first_path: first_record, second_path: second_record}[path]

    signalled = []
    monkeypatch.setattr(server_lease_module, "_try_lock_instance_path", lambda _path: None)
    monkeypatch.setattr(server_lease_module, "_read_unlocked_instance_record", read_record)
    monkeypatch.setattr(
        server_lease_module,
        "_verified_instance_owner",
        lambda record, _identity: record is first_record or record is second_record,
    )
    monkeypatch.setattr(
        server_lease_module,
        "_force_stop_instance_owner",
        lambda *args, **kwargs: signalled.append((args, kwargs)) or {},
    )

    with pytest.raises(InstanceLeaseError, match="changed identity"):
        server_lease_module._acquire_instance_locks(
            (first_path, second_path),
            force=True,
            identity=HostIdentity("host", "host", "boot", 2, "proc:2", 2, "nonce", "fixture"),
        )
    assert signalled == []


def test_force_does_not_stop_root_owner_when_requested_port_is_foreign(tmp_path):
    roots = _roots(tmp_path)
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


def test_force_refuses_unverified_port_before_stopping_root_owner(monkeypatch, tmp_path):
    root_path = tmp_path / "root" / "instance.lock"
    root_record = {"owner": "root"}
    unverified_port_record = {"owner": "foreign"}
    identity = HostIdentity("host", "host", "boot", 2, "proc:2", 2, "nonce", "fixture")

    monkeypatch.setattr(server_lease_module, "_instance_path_tuple", lambda _paths: (tmp_path / "root",))
    monkeypatch.setattr(server_lease_module, "_instance_lock_paths", lambda _paths: (root_path,))
    monkeypatch.setattr(
        server_lease_module,
        "_read_unlocked_instance_record",
        lambda path: root_record if path == root_path else unverified_port_record,
    )
    monkeypatch.setattr(server_lease_module, "_verified_instance_owner", lambda record, _identity: record is root_record)
    monkeypatch.setattr(
        server_lease_module,
        "acquire_instance_root_lease",
        lambda *_args, **_kwargs: pytest.fail("must reject the foreign port before stopping the root owner"),
    )

    with pytest.raises(InstanceLeaseError, match="port 9124.*identity cannot be verified"):
        acquire_instance_and_port_leases(tmp_path / "root", 9124, force=True)


def test_force_refuses_verified_port_without_the_same_product_root(tmp_path):
    roots = _roots(tmp_path)
    foreign_port = acquire_server_port_lease(9124, state_dir=roots.runtime_dir)
    assert foreign_port is not None
    try:
        with pytest.raises(InstanceLeaseError, match="requested port 9124 is owned by a different"):
            acquire_instance_and_port_leases(roots, 9124, force=True)
        root_lease = acquire_instance_root_lease(roots)
        assert root_lease is not None
        root_lease.release()
    finally:
        foreign_port.release()


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


def test_process_level_force_replaces_existing_server(monkeypatch, tmp_path):
    source_root = Path(__file__).resolve().parents[1]
    paths = build_paths(tmp_path / "server")
    runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path / "tmux", session_count=1)
    first = None
    replacement = None
    try:
        first = start_isolated_dev_server("force-owner", source_root, paths, runtime)
        replacement = start_isolated_dev_server(
            "force-replacement",
            source_root,
            paths,
            runtime,
            port=first.port,
            force=True,
        )
        assert first.process.wait(timeout=10.0) is not None
        replacement.assert_serving()
    finally:
        if replacement is not None:
            stop_and_reap_daemons(replacement)
        elif first is not None:
            stop_and_reap_daemons(first)
        stop_isolated_tmux_runtime(runtime)


def test_process_level_force_replaces_existing_server_on_a_different_port(monkeypatch, tmp_path):
    source_root = Path(__file__).resolve().parents[1]
    paths = build_paths(tmp_path / "server")
    runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path / "tmux", session_count=1)
    first = None
    replacement = None
    try:
        first = start_isolated_dev_server("cross-port-force-owner", source_root, paths, runtime)
        replacement = start_isolated_dev_server(
            "cross-port-force-replacement",
            source_root,
            paths,
            runtime,
            force=True,
        )
        assert replacement.port != first.port
        assert first.process.wait(timeout=10.0) is not None
        replacement.assert_serving()
    finally:
        if replacement is not None:
            stop_and_reap_daemons(replacement)
        elif first is not None:
            stop_and_reap_daemons(first)
        stop_isolated_tmux_runtime(runtime)
