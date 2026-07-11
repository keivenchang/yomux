import threading
import time

from yolomux_lib import search_indexer
from yolomux_lib.filesystem import search
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec


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
    assert owner.socket_path.exists() is True
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


def test_indexer_service_leases_prevent_idle_exit_then_allow_shutdown(tmp_path):
    socket_path = tmp_path / "indexer.sock"
    service = search_indexer.PersistentSearchIndexer(socket_path, idle_seconds=0.02)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = search_indexer.SearchIndexerClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    lease = client.registry.acquire_lease()
    assert lease["ok"] is True
    time.sleep(0.05)
    assert worker.is_alive() is True
    assert client.registry.release_lease(lease["lease_id"])["ok"] is True
    assert client.request({"action": "shutdown_if_idle"})["shutdown"] is True
    worker.join(timeout=1.0)
    assert worker.is_alive() is False
    assert service.socket_path.exists() is False


def test_local_service_registry_serializes_starters_and_reuses_healthy_winner(tmp_path):
    socket_path = tmp_path / "indexer.sock"
    service = search_indexer.PersistentSearchIndexer(socket_path)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    spec = LocalServiceSpec("indexd", "yolomux_lib.search_indexer", socket_path.name, search_indexer.INDEXER_PROTOCOL_VERSION)
    first = LocalServiceRegistry(tmp_path, spec, socket_path=socket_path)
    second = LocalServiceRegistry(tmp_path, spec, socket_path=socket_path)

    results = []
    starters = [threading.Thread(target=lambda registry=registry: results.append(registry.ensure_started())) for registry in (first, second)]
    for starter in starters:
        starter.start()
    for starter in starters:
        starter.join(timeout=1.0)

    assert results == [True, True]
    assert first.status()["healthy"] is True
    assert second.status()["healthy"] is True
    assert first.record_path.exists() is True
    assert first._request("shutdown", timeout=0.2) == {"ok": True}
    worker.join(timeout=1.0)


def test_local_service_registry_backoff_blocks_repeated_failed_spawns(tmp_path):
    starts = []

    class FailedProcess:
        def poll(self):
            return 1

    def failing_popen(args, **kwargs):
        starts.append((args, kwargs))
        return FailedProcess()

    now = [0.0]
    spec = LocalServiceSpec("missing", "missing.module", "missing.sock", 1)
    registry = LocalServiceRegistry(tmp_path, spec, popen=failing_popen, clock=lambda: now[0], sleep=lambda _seconds: None)

    assert registry.ensure_started() is False
    assert registry.ensure_started() is False
    assert len(starts) == 1
    now[0] = 1.0
    assert registry.ensure_started() is False
    assert len(starts) == 2


def test_local_service_registry_starts_real_indexd_and_recovers_stale_socket_record(tmp_path):
    socket_path = tmp_path / "state directory with spaces" / "indexer.sock"
    registry = LocalServiceRegistry(
        socket_path.parent,
        LocalServiceSpec("indexd", "yolomux_lib.search_indexer", socket_path.name, search_indexer.INDEXER_PROTOCOL_VERSION, idle_seconds=30.0),
        socket_path=socket_path,
    )
    socket_path.parent.mkdir(parents=True)
    socket_path.write_text("stale", encoding="utf-8")
    registry._write_record({"pid": 999_999_999, "service": "indexd"})

    assert registry.ensure_started() is True
    status = registry.status()
    assert status["healthy"] is True
    assert status["record"]["pid"] > 0
    assert status["record"]["socket"] == str(registry.socket_path)
    assert registry._request("shutdown", timeout=0.5) == {"ok": True}
    deadline = time.monotonic() + 2.0
    while registry.socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert registry.socket_path.exists() is False


def test_indexd_common_status_has_bounded_worker_schema(tmp_path):
    socket_path = tmp_path / "indexer.sock"
    service = search_indexer.PersistentSearchIndexer(socket_path)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = search_indexer.SearchIndexerClient(socket_path)
    deadline = time.monotonic() + 2.0
    while not client.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    status = client.request({"action": "status"})
    profile = client.request({"action": "profile"})
    drained = client.request({"action": "drain"})

    assert set(status) >= {"ok", "version", "pid", "started_at", "socket", "clients", "queues", "active_task", "cache", "last_success", "last_failure", "restart_backoff_seconds", "generation", "idle_seconds", "status"}
    assert status["version"] == search_indexer.INDEXER_PROTOCOL_VERSION
    assert set(status["queues"]) == {"interactive", "normal", "maintenance"}
    assert profile["profile"]["pid"] == status["pid"]
    assert drained["ok"] is True
    assert client.request({"action": "shutdown"}) == {"ok": True}
    worker.join(timeout=1.0)
