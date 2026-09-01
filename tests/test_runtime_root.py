from pathlib import Path

import pytest

from yolomux_lib.infra.filesystem_preflight import FilesystemClassification
from yolomux_lib.infra.filesystem_preflight import FilesystemPreflightError
from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib.infra import common
from tests.gate_harness import UNIX_SOCKET_PATH_LIMIT_BYTES


def _identity() -> HostIdentity:
    return HostIdentity("host-a", "host-a.example", "boot-a", 42, "proc:1", 1, "nonce-a", "fixture")


def test_runtime_root_prefers_xdg_and_is_scoped_to_host_and_boot(tmp_path):
    root = common.runtime_root(
        environ={"XDG_RUNTIME_DIR": str(tmp_path / "xdg-runtime")},
        identity=_identity(),
    )

    assert root == tmp_path / "xdg-runtime" / "yolomux" / "h-host-a" / "b-boot-a"


def test_runtime_root_fallback_is_scoped_to_host_and_boot(tmp_path):
    root = common.runtime_root(
        environ={},
        identity=_identity(),
        temporary_dir=tmp_path / "temporary",
        uid=1000,
    )

    assert root == tmp_path / "temporary" / "yolomux-server-1000" / "shared" / "h-host-a" / "b-boot-a"


def test_runtime_root_refuses_a_network_filesystem(tmp_path):
    with pytest.raises(FilesystemPreflightError, match="nfs4"):
        common.ensure_runtime_root(
            tmp_path / "runtime",
            classifier=lambda path: FilesystemClassification(Path(path), "nfs4", True, True),
        )


def test_runtime_root_refuses_a_symlinked_private_directory(tmp_path):
    root = tmp_path / "runtime" / "h-host" / "b-boot"
    root.parent.parent.mkdir(mode=0o700)
    root.parent.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(PermissionError, match="path is a symlink") as error:
        common.ensure_runtime_root(root)
    assert "found mode" in str(error.value)
    assert "owner uid" in str(error.value)
    assert "mode 0700" in str(error.value)


def test_runtime_root_refusal_explains_foreign_owner(tmp_path, monkeypatch):
    root = tmp_path / "runtime" / "h-host" / "b-boot"
    runtime_base = root.parent.parent
    runtime_base.mkdir(mode=0o700)
    runtime_base.chmod(0o700)
    monkeypatch.setattr(common.os, "getuid", lambda: runtime_base.stat().st_uid + 1)

    with pytest.raises(PermissionError, match="path is owned by another uid") as error:
        common.ensure_runtime_root(root)
    assert "found mode 0700" in str(error.value)
    assert "do not reuse it" in str(error.value)


def test_runtime_root_refusal_explains_non_directory(tmp_path):
    root = tmp_path / "runtime" / "h-host" / "b-boot"
    root.parent.parent.write_text("not a directory", encoding="utf-8")

    with pytest.raises(PermissionError, match="path is not a directory") as error:
        common.ensure_runtime_root(root)
    assert "found mode" in str(error.value)
    assert "replace it with a private directory" in str(error.value)


def test_runtime_root_upgrades_an_owned_loose_directory_mode(tmp_path):
    root = tmp_path / "runtime" / "h-host" / "b-boot"
    runtime_base = root.parent.parent
    runtime_base.mkdir(mode=0o755)
    runtime_base.chmod(0o755)

    assert common.ensure_runtime_root(root) == root
    assert runtime_base.stat().st_mode & 0o777 == 0o700
    assert root.parent.stat().st_mode & 0o777 == 0o700
    assert root.stat().st_mode & 0o777 == 0o700


def test_runtime_root_does_not_validate_the_world_writable_temporary_ancestor(tmp_path):
    root = tmp_path / "runtime" / "h-host" / "b-boot"
    tmp_path.chmod(0o755)
    root.parent.parent.mkdir(mode=0o700)
    root.parent.parent.chmod(0o700)

    assert common.ensure_runtime_root(root) == root


def test_runtime_root_creation_removes_only_previous_boot_siblings(tmp_path):
    root = tmp_path / "runtime" / "h-host" / "b-current"
    previous = root.parent / "b-previous"
    root.mkdir(parents=True, mode=0o700)
    previous.mkdir(mode=0o700)
    root.parent.parent.chmod(0o700)
    root.parent.chmod(0o700)
    root.chmod(0o700)
    previous.chmod(0o700)

    assert common.ensure_runtime_root(root) == root
    assert root.is_dir()
    assert not previous.exists()


def test_runtime_root_removes_an_owned_loose_previous_boot_sibling(tmp_path):
    root = tmp_path / "runtime" / "h-host" / "b-current"
    previous = root.parent / "b-previous"
    root.mkdir(parents=True, mode=0o700)
    previous.mkdir(mode=0o755)
    root.parent.parent.chmod(0o700)
    root.parent.chmod(0o700)
    root.chmod(0o700)
    previous.chmod(0o755)

    assert common.ensure_runtime_root(root) == root
    assert not previous.exists()


def test_runtime_stats_socket_path_stays_within_sockaddr_un_budget(tmp_path):
    identity = HostIdentity("a" * 32, "host.example", "b" * 36, 42, "proc:1", 1, "nonce-a", "fixture")
    root = common.runtime_root(environ={}, identity=identity, temporary_dir=Path("/tmp"), uid=1000)
    socket_path = root / "services" / "statsd.p24s6.sock"

    assert len(str(socket_path).encode()) <= UNIX_SOCKET_PATH_LIMIT_BYTES
