import threading
import time

from yolomux_lib import search_indexer
from yolomux_lib.filesystem import search


SEARCH_INDEXER_ENSURE_STARTED = search_indexer.SearchIndexerClient.ensure_started


def test_persistent_indexer_coalesces_paths_then_refreshes_one_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    changed = root / "changed.py"
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    ensured = []
    refreshed = []
    monkeypatch.setattr(search, "_ensure_search_index", lambda path: ensured.append(path))
    monkeypatch.setattr(search, "reindex_roots_for_paths", lambda paths, reason: refreshed.append((paths, reason)))

    assert service.enqueue(str(root), [str(changed)], "native-watch")["accepted"] is True
    assert service.enqueue(str(root), [str(changed)], "fallback-poll")["queued_paths"] == 1
    service.pending_due_at[str(root)] = 0.0

    assert service.process_due() == 1
    assert ensured == [root]
    assert refreshed == [([str(changed)], "persistent-indexer")]


def test_persistent_indexer_unix_socket_protocol(tmp_path):
    socket_path = tmp_path / "indexer.sock"
    service = search_indexer.PersistentSearchIndexer(socket_path)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = search_indexer.SearchIndexerClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert client.healthy() is True
    assert client.request({"action": "status"})["ok"] is True
    assert client.request({"action": "shutdown"}) == {"ok": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False
    assert service.socket_path.exists() is False


def test_losing_indexer_does_not_unlink_the_owners_socket(tmp_path):
    socket_path = tmp_path / "indexer.sock"
    owner = search_indexer.PersistentSearchIndexer(socket_path)
    owner_worker = threading.Thread(target=owner.run, daemon=True)
    owner_worker.start()
    client = search_indexer.SearchIndexerClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert client.healthy() is True
    contender = search_indexer.PersistentSearchIndexer(socket_path)
    assert contender.run() == 0
    assert socket_path.exists() is True
    assert client.request({"action": "status"})["ok"] is True

    assert client.request({"action": "shutdown"}) == {"ok": True}
    owner_worker.join(timeout=2.0)
    assert owner_worker.is_alive() is False
    assert socket_path.exists() is False


def test_persistent_indexer_serves_its_ready_snapshot_to_read_only_servers(tmp_path, monkeypatch):
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    expected = {"root": "/repo", "query": "t5t.md", "files": [{"name": "t5t.md"}]}
    calls = []
    monkeypatch.setattr(search, "search_files", lambda root, query, limit, recursive: calls.append((root, query, limit, recursive)) or expected)

    response = service.handle({"action": "search", "root": "/repo", "query": "t5t.md", "limit": 20})

    assert calls == [("/repo", "t5t.md", 20, True)]
    assert response == {"ok": True, "payload": expected}


def test_search_client_replaces_legacy_peer_that_lacks_search_capability(tmp_path, monkeypatch):
    class LegacyIndexer(search_indexer.PersistentSearchIndexer):
        def handle(self, request):
            if str(request.get("action") or "") == "ping":
                return {"ok": True, "version": search_indexer.INDEXER_PROTOCOL_VERSION, "pid": 1}
            return super().handle(request)

    socket_path = tmp_path / "indexer.sock"
    legacy = LegacyIndexer(socket_path)
    worker = threading.Thread(target=legacy.run, daemon=True)
    worker.start()
    monkeypatch.setattr(search_indexer.SearchIndexerClient, "ensure_started", SEARCH_INDEXER_ENSURE_STARTED)
    client = search_indexer.SearchIndexerClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert client.healthy() is True
    assert client.supports("search") is False
    assert client._stop_legacy_indexer() is True
    assert client._start_until(lambda: client.supports("search")) is True
    assert client.supports("search") is True
    assert client.request({"action": "shutdown"}) == {"ok": True}
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


def test_persistent_indexer_owns_unindex_writes(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    removed = []
    monkeypatch.setattr(search_indexer.file_index, "unindex", lambda path: removed.append(path))

    assert service.unindex(str(root)) == {"ok": True, "accepted": True, "root": str(root)}
    assert removed == [root]


def test_persistent_indexer_applies_trailing_debounce_and_churn_backoff(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    moment = [100.0]
    monkeypatch.setattr(search_indexer.time, "monotonic", lambda: moment[0])

    service.enqueue(str(root), [], "watch")
    assert service.pending_due_at[str(root)] == 102.0
    moment[0] = 101.0
    service.enqueue(str(root), [], "watch")
    assert service.pending_due_at[str(root)] == 103.0

    service.pending_due_at[str(root)] = 0.0
    monkeypatch.setattr(search, "_ensure_search_index", lambda _root: None)
    monkeypatch.setattr(search, "reindex_roots_for_paths", lambda *_args, **_kwargs: None)
    moment[0] = 102.0
    service.process_due()
    moment[0] = 103.0
    service.enqueue(str(root), [], "watch")
    assert service.pending_due_at[str(root)] == 105.0

    service.pending_due_at[str(root)] = 0.0
    moment[0] = 104.0
    service.process_due()
    moment[0] = 105.0
    service.enqueue(str(root), [], "watch")
    assert service.pending_due_at[str(root)] == 109.0


def test_persistent_indexer_does_not_spin_idle_maintenance(tmp_path, monkeypatch):
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    scheduled = []
    monkeypatch.setattr(search_indexer.file_index, "schedule_refreshes", lambda **_kwargs: scheduled.append(True) or 0)

    service.process_due()
    service.process_due()

    assert scheduled == [True]
