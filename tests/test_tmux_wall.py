from tmux_wall import is_loopback_bind_host
from tmux_wall import remote_bind_error


def test_tmux_wall_loopback_bind_detection():
    assert is_loopback_bind_host("127.0.0.1")
    assert is_loopback_bind_host("localhost")
    assert is_loopback_bind_host("::1")
    assert not is_loopback_bind_host("0.0.0.0")
    assert not is_loopback_bind_host("::")


def test_tmux_wall_rejects_remote_bind_without_explicit_flag():
    assert "no authentication" in remote_bind_error("0.0.0.0", False)
    assert remote_bind_error("0.0.0.0", True) == ""
    assert remote_bind_error("127.0.0.1", False) == ""
