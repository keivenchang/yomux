from pathlib import Path

from yolomux_lib.chat import chat_store
from yolomux_lib.login_rate_limit import LoginRateLimiter
from yolomux_lib import login_rate_limit
from yolomux_lib.observability import pricing_catalog
from yolomux_lib.search import file_index


def _host_root(root: Path, calls: list[Path]) -> Path:
    calls.append(root)
    return root / "hosts" / "host-a"


def test_m_db_chat_default_uses_host_partition_and_leaves_legacy_database_untouched(tmp_path, monkeypatch):
    state = tmp_path / "state"
    legacy = state / "yochat.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy-chat")
    calls: list[Path] = []
    monkeypatch.setattr(chat_store, "host_partitioned_state_dir", lambda root: _host_root(Path(root), calls))

    path = chat_store.default_chat_database_path(state)
    chat_store.ChatStore(path)._initialize()

    assert calls == [state]
    assert path == state / "hosts" / "host-a" / "yochat.sqlite3"
    assert path.exists()
    assert legacy.read_bytes() == b"legacy-chat"


def test_m_db_login_default_uses_host_partition_and_leaves_legacy_database_untouched(tmp_path, monkeypatch):
    state = tmp_path / "state"
    legacy = state / "login-throttle.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy-login")
    calls: list[Path] = []
    monkeypatch.setattr(login_rate_limit, "host_partitioned_state_dir", lambda root: _host_root(Path(root), calls))

    path = login_rate_limit.default_login_throttle_database_path(state)
    LoginRateLimiter(path).check_and_reserve("203.0.113.7", "user")

    assert calls == [state]
    assert path == state / "hosts" / "host-a" / "login-throttle.sqlite3"
    assert path.exists()
    assert legacy.read_bytes() == b"legacy-login"


def test_m_db_search_default_uses_host_partition_and_leaves_legacy_database_untouched(tmp_path, monkeypatch):
    state = tmp_path / "state"
    legacy = state / "search_index" / "legacy.sqlite3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy-search")
    calls: list[Path] = []
    monkeypatch.setattr(file_index, "host_partitioned_state_dir", lambda root: _host_root(Path(root), calls))

    index_dir = file_index.default_index_dir(state)
    index_dir.mkdir(parents=True)
    monkeypatch.setattr(file_index, "INDEX_DIR", index_dir)
    connection = file_index._connect_sqlite_index(tmp_path / "source")
    connection.close()

    assert calls == [state]
    assert index_dir == state / "hosts" / "host-a" / "search_index"
    assert list(index_dir.glob("*.sqlite3"))
    assert legacy.read_bytes() == b"legacy-search"


def test_m_db_pricing_default_uses_host_partition_and_leaves_legacy_database_untouched(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    legacy = cache / "model-pricing" / "pricing.sqlite3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy-pricing")
    calls: list[Path] = []
    monkeypatch.setattr(pricing_catalog, "host_partitioned_state_dir", lambda root: _host_root(Path(root), calls))

    root = pricing_catalog.default_pricing_cache_dir(cache)
    catalog = pricing_catalog.PricingCatalog(root=root)
    catalog.open()

    assert calls == [cache]
    assert root == cache / "hosts" / "host-a" / "model-pricing"
    assert (root / "pricing.sqlite3").exists()
    assert legacy.read_bytes() == b"legacy-pricing"
