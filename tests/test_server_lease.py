import json
from types import SimpleNamespace

from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib import server_lease
from yolomux_lib.server_lease import acquire_server_port_lease


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


def test_server_port_lease_reclaims_a_dead_unlocked_owner(tmp_path):
    identity = HostIdentity("host-a", "host-a", "boot-a", 2, "proc:2", 2, "nonce-a", "fixture")
    path = tmp_path / "server-leases" / identity.stable_host_id / "9123.lock"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**identity.process_record_fields(pid=99999999, start_identity="proc:1"), "port": 9123}) + "\n", encoding="utf-8")

    lease = acquire_server_port_lease(9123, state_dir=tmp_path, host_identity=identity)

    assert lease is not None
    try:
        assert lease.reclaimed is True
        assert json.loads(path.read_text(encoding="utf-8"))["pid"] == identity.pid
    finally:
        lease.release()


def test_server_port_lease_refuses_when_owner_status_is_uncertain(tmp_path, monkeypatch):
    identity = HostIdentity("host-a", "host-a", "boot-a", 2, "proc:2", 2, "nonce-a", "fixture")
    path = tmp_path / "server-leases" / identity.stable_host_id / "9123.lock"
    path.parent.mkdir(parents=True)
    record = {**identity.process_record_fields(pid=99999999, start_identity="proc:1"), "port": 9123}
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(server_lease, "lease_owner_status", lambda _record, _identity: SimpleNamespace(current=False, may_remove_stale_record=False))

    assert acquire_server_port_lease(9123, state_dir=tmp_path, host_identity=identity) is None
    assert json.loads(path.read_text(encoding="utf-8")) == record
