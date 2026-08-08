from pathlib import Path
from threading import Event

import pytest

from yolomux_lib.infra.filesystem_preflight import FilesystemClassification
from yolomux_lib.infra.filesystem_preflight import FilesystemPreflightError
from yolomux_lib.infra.filesystem_preflight import classify_filesystem
from yolomux_lib.infra.filesystem_preflight import clear_filesystem_classification_cache
from yolomux_lib.infra.filesystem_preflight import preflight_mutable_roots
from yolomux_lib.infra import filesystem_preflight
from yolomux_lib.infra.atomic_file import open_wal_database
from yolomux_lib.local_services.runtime import run_local_rpc_service
from yolomux_lib.observability.pricing_catalog import PricingCatalog
from yolomux_lib.search import file_index
from yolomux_lib.stats_current.storage import DATABASE_FILENAME
from yolomux_lib.stats_current.storage import Store


def test_preflight_allows_a_known_local_ext_root(tmp_path):
    result = preflight_mutable_roots(
        wal_databases=[tmp_path / "stats.sqlite3"],
        unix_sockets=[tmp_path / "service.sock"],
        classifier=lambda path: FilesystemClassification(Path(path), "ext4", False, True),
    )

    assert [item.filesystem_type for item in result] == ["ext4", "ext4"]


def test_preflight_refuses_wal_and_socket_on_nfs_with_actionable_paths(tmp_path):
    with pytest.raises(FilesystemPreflightError, match="YOLOMUX_ALLOW_NETWORK_FILESYSTEM_MUTABLE_ROOTS=1") as raised:
        preflight_mutable_roots(
            wal_databases=[tmp_path / "stats.sqlite3"],
            unix_sockets=[tmp_path / "service.sock"],
            classifier=lambda path: FilesystemClassification(Path(path), "nfs4", True, True),
        )

    message = str(raised.value)
    assert "WAL SQLite database" in message
    assert "Unix socket" in message
    assert "nfs4" in message
    assert str(tmp_path / "stats.sqlite3") in message
    assert str(tmp_path / "service.sock") in message


def test_preflight_refuses_unknown_filesystem_and_escape_hatch_warns(tmp_path, monkeypatch):
    classifier = lambda path: FilesystemClassification(Path(path), "unknown", False, False)
    with pytest.raises(FilesystemPreflightError, match="unknown"):
        preflight_mutable_roots(wal_databases=[tmp_path / "stats.sqlite3"], classifier=classifier)

    monkeypatch.setenv("YOLOMUX_ALLOW_NETWORK_FILESYSTEM_MUTABLE_ROOTS", "1")
    with pytest.warns(RuntimeWarning, match="YOLOMUX_ALLOW_NETWORK_FILESYSTEM_MUTABLE_ROOTS=1"):
        preflight_mutable_roots(wal_databases=[tmp_path / "stats.sqlite3"], classifier=classifier)


def test_unreadable_mountinfo_is_unknown_and_refuses(tmp_path):
    clear_filesystem_classification_cache()
    result = classify_filesystem(tmp_path / "stats.sqlite3", mountinfo_path=tmp_path / "missing-mountinfo")
    assert result == FilesystemClassification(tmp_path, "unknown", False, False)
    with pytest.raises(FilesystemPreflightError, match="unknown"):
        preflight_mutable_roots(wal_databases=[tmp_path / "stats.sqlite3"], classifier=lambda _path: result)


def _network_classifier(path):
    return FilesystemClassification(Path(path), "nfs4", True, True)


def test_real_atomic_wal_helper_refuses_before_creating_database(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem_preflight, "classify_filesystem", _network_classifier)
    path = tmp_path / "chat.sqlite3"
    with pytest.raises(FilesystemPreflightError):
        open_wal_database(path, 100)
    assert not path.exists()


def test_real_search_wal_writer_refuses_before_creating_database(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem_preflight, "classify_filesystem", _network_classifier)
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path)
    with pytest.raises(FilesystemPreflightError):
        file_index._connect_sqlite_index(tmp_path / "source")
    assert not list(tmp_path.glob("*.sqlite3"))


def test_real_pricing_wal_writer_refuses_before_creating_database(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem_preflight, "classify_filesystem", _network_classifier)
    catalog = PricingCatalog(root=tmp_path)
    with pytest.raises(FilesystemPreflightError):
        catalog._connect()
    assert not (tmp_path / "pricing.sqlite3").exists()


def test_real_stats_wal_writer_refuses_before_creating_database(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem_preflight, "classify_filesystem", _network_classifier)
    path = tmp_path / DATABASE_FILENAME
    with pytest.raises(FilesystemPreflightError):
        Store.open(path)
    assert not path.exists()


def test_real_unix_socket_writer_refuses_before_bind(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem_preflight, "classify_filesystem", _network_classifier)
    socket_path = tmp_path / "service.sock"
    with pytest.raises(FilesystemPreflightError):
        run_local_rpc_service(
            socket_path=socket_path, lock_path=tmp_path / "service.lock", service_name="test",
            stop_event=Event(), handle=lambda _request, _request_binary: ({"ok": True}, b""), on_idle=lambda: True, on_client=lambda: None,
        )
    assert not socket_path.exists()
