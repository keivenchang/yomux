from yolomux_lib.server_lease import acquire_server_port_lease


def test_server_port_lease_allows_exactly_one_live_owner(tmp_path):
    first = acquire_server_port_lease(9123, state_dir=tmp_path)
    assert first is not None
    assert acquire_server_port_lease(9123, state_dir=tmp_path) is None

    first.release()

    second = acquire_server_port_lease(9123, state_dir=tmp_path)
    assert second is not None
    second.release()
