import threading
import time

from yolomux_lib import search_indexer
from yolomux_lib.filesystem import search


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


def test_persistent_indexer_owns_unindex_writes(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    removed = []
    monkeypatch.setattr(search_indexer.file_index, "unindex", lambda path: removed.append(path))

    assert service.unindex(str(root)) == {"ok": True, "accepted": True, "root": str(root)}
    assert removed == [root]
