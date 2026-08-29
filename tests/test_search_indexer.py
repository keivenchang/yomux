import os
import threading
import time

import pytest

from yolomux_lib import search_indexer
from yolomux_lib.filesystem import paths
from yolomux_lib.filesystem import search
from yolomux_lib.infra import batchd
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.local_services import runtime as local_service_runtime
from yolomux_lib.search import file_index


SEARCH_INDEXER_ENSURE_STARTED = search_indexer.SearchIndexerClient.ensure_started


def _access_policy(*roots):
    return paths.FilesystemAccessPolicy(
        version=paths.FS_ACCESS_POLICY_VERSION,
        roots=tuple(str(root) for root in roots),
    )


def _authorized_search_request(root, policy, *, query="needle", limit=20):
    return {
        "action": "search",
        "root": str(root),
        "query": query,
        "limit": limit,
        paths.FS_ACCESS_POLICY_FIELD: policy.descriptor(),
        file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: file_index.root_identity(root.stat()),
    }


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
    root = tmp_path / "repo"
    root.mkdir()
    policy = _access_policy(root)
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    expected = {"root": str(root), "query": "t5t.md", "files": [{"name": "t5t.md"}]}
    calls = []
    monkeypatch.setattr(
        search,
        "_search_files_from_authorized_handle",
        lambda handle, query, limit, recursive, cursor=None: calls.append(
            (handle.resolved, file_index.root_identity(handle.stat_result), query, limit, recursive, cursor)
        ) or expected,
    )

    response = service.handle(_authorized_search_request(root, policy, query="t5t.md"))

    assert calls == [(root, file_index.root_identity(root.stat()), "t5t.md", 20, True, None)]
    assert response == {"ok": True, "payload": expected}


def test_indexer_search_action_threads_the_opaque_delta_cursor(tmp_path, monkeypatch):
    # Step 4: a delta request carries an opaque cursor; the indexer read path must forward it so the
    # follower gets fenced committed journal deltas, not a fresh snapshot.
    root = tmp_path / "repo"
    root.mkdir()
    policy = _access_policy(root)
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    expected = {"root": str(root), "query": "t5t", "changes": [], "cursor": "C2", "more": False}
    calls = []
    monkeypatch.setattr(
        search,
        "_search_files_from_authorized_handle",
        lambda handle, query, limit, recursive, cursor=None: calls.append(
            (handle.resolved, query, limit, recursive, cursor)
        ) or expected,
    )

    request = _authorized_search_request(root, policy, query="t5t")
    request["cursor"] = "C1"
    response = service.handle(request)

    assert calls == [(root, "t5t", 20, True, "C1")]
    assert response == {"ok": True, "payload": expected}


@pytest.mark.parametrize("policy_scope", ["narrow", "broad"])
def test_indexd_search_refuses_a_repointed_authorized_root_generation_without_leaking_metadata(
    tmp_path,
    policy_scope,
):
    base = tmp_path / "base"
    allowed = base / "allowed"
    outside = base / "outside"
    allowed.mkdir(parents=True)
    outside.mkdir()
    (allowed / "safe.txt").write_text("safe", encoding="utf-8")
    blocked = outside / "BLOCKED_SENTINEL_DO_NOT_EXPOSE.txt"
    blocked.write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE", encoding="utf-8")
    policy = _access_policy(allowed if policy_scope == "narrow" else base)
    request = _authorized_search_request(allowed, policy, query="BLOCKED_SENTINEL")
    parked = base / "allowed-authorized-generation"
    allowed.rename(parked)
    allowed.symlink_to(outside, target_is_directory=True)
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")

    with pytest.raises(paths.FilesystemError) as caught:
        service.handle(request)

    rendered = str(caught.value)
    assert "BLOCKED_SENTINEL" not in rendered
    assert str(blocked) not in rendered
    assert all(field not in rendered for field in ("realpath", "file_id", "size"))


@pytest.mark.parametrize(
    "descriptor",
    [
        pytest.param(None, id="missing"),
        pytest.param({"version": paths.FS_ACCESS_POLICY_VERSION, "roots": "not-a-list"}, id="malformed"),
    ],
)
def test_indexd_search_refuses_missing_or_malformed_accepting_policy(tmp_path, descriptor):
    root = tmp_path / "repo"
    root.mkdir()
    request = {
        "action": "search",
        "root": str(root),
        "query": "needle",
        "limit": 20,
        file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: file_index.root_identity(root.stat()),
    }
    if descriptor is not None:
        request[paths.FS_ACCESS_POLICY_FIELD] = descriptor
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")

    with pytest.raises(paths.FilesystemError, match="filesystem access policy is unusable"):
        service.handle(request)


@pytest.mark.parametrize("identity", [None, [], [1], [1, 2, 3], [True, 2], ["1", 2]])
def test_indexd_search_refuses_missing_or_malformed_authorized_root_identity(tmp_path, identity):
    root = tmp_path / "repo"
    root.mkdir()
    request = {
        "action": "search",
        "root": str(root),
        "query": "needle",
        "limit": 20,
        paths.FS_ACCESS_POLICY_FIELD: _access_policy(root).descriptor(),
        file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: identity,
    }
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")

    with pytest.raises(paths.FilesystemError, match="authorized_root_identity_invalid"):
        service.handle(request)


def test_index_search_authority_survives_the_path_only_app_adapter(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    policy = _access_policy(root)
    authority = _authorized_search_request(root, policy)
    client = search_indexer.SearchIndexerClient(tmp_path / "indexer.sock")
    sent = []
    monkeypatch.setattr(client, "supports", lambda capability: capability == "search")
    monkeypatch.setattr(client, "request", lambda payload, timeout=0.5: sent.append((payload, timeout)) or {"ok": True})

    def path_only_adapter(payload):
        return client.search(
            str(payload.get("root") or ""),
            str(payload.get("query") or ""),
            int(payload.get("limit") or 400),
        )

    monkeypatch.setattr(file_index, "_BACKGROUND_INDEX_SEARCH_REQUESTER", path_only_adapter)

    assert file_index.request_background_index_search(authority) == {"ok": True}
    assert sent == [({**authority}, search_indexer.INDEXER_SEARCH_RPC_TIMEOUT_SECONDS)]


def test_http_search_descriptor_threads_cursor_through_the_batchd_executor(monkeypatch):
    # Step 4: the HTTP `/api/fs/search?cursor=` param reaches `filesystem.search_files` through the
    # batchd filesystem-operation descriptor. An absent cursor is a snapshot; an opaque cursor selects
    # the bounded committed-journal delta read.
    calls = []

    def _capture(path, query, limit, *, recursive, cursor):
        calls.append((path, query, limit, recursive, cursor))
        return {"root": path, "query": query, "changes": [], "cursor": "C2", "more": False}

    monkeypatch.setattr(batchd.filesystem, "search_files", _capture)

    batchd._filesystem_operation_authorized({"op": "search", "path": "/repo", "args": {"query": "t5t", "limit": 25, "recursive": True, "cursor": "C1"}})
    batchd._filesystem_operation_authorized({"op": "search", "path": "/repo", "args": {"query": "t5t", "recursive": True}})

    assert calls == [("/repo", "t5t", 25, True, "C1"), ("/repo", "t5t", 400, True, None)]


def test_search_client_deadline_is_typed_and_uses_the_bounded_search_timeout(tmp_path, monkeypatch):
    client = search_indexer.SearchIndexerClient(tmp_path / "indexer.sock")
    observed_timeouts = []

    def deadline_expired(_socket_path, _envelope, *, timeout_seconds, fallback_legacy):
        observed_timeouts.append((timeout_seconds, fallback_legacy))
        raise TimeoutError("forced indexd deadline")

    monkeypatch.setattr(search_indexer, "local_service_request", deadline_expired)
    monkeypatch.setattr(client, "supports", lambda capability: capability == "search")

    response = client.search("/repo", "needle", 20)

    assert response == {
        "ok": False,
        "status": "unavailable",
        "error_code": "deadline_expired",
        "reason": "forced indexd deadline",
    }
    assert observed_timeouts == [(search_indexer.INDEXER_SEARCH_RPC_TIMEOUT_SECONDS, True)]


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


def test_indexd_status_probe_does_not_reset_the_idle_clock(tmp_path):
    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock", idle_seconds=5.0)
    assert not service.leases
    service.last_client_at = time.monotonic() - 6.0
    assert service.idle_due() is True, "baseline: no leases and idle_seconds elapsed must already report idle"

    service.last_client_at = time.monotonic() - 6.0
    response = service.handle({"action": "status"})
    assert response["ok"] is True
    assert service.idle_due() is True, "a status probe reset the idle clock via handle()"


def test_indexd_external_status_probe_never_refreshes_demand_but_a_real_lease_does(tmp_path, monkeypatch):
    """Cross the real listener boundary (not a direct ``handle()`` call) to
    prove an external health/status poller with zero leases cannot refresh
    the idle deadline, while acquiring a real lease does.
    """
    socket_path = tmp_path / "indexer.sock"
    service = search_indexer.PersistentSearchIndexer(socket_path, idle_seconds=5.0)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    client = search_indexer.SearchIndexerClient(socket_path)
    try:
        deadline = time.monotonic() + 2.0
        while not client.healthy() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.healthy() is True

        monkeypatch.setattr(local_service_runtime, "peer_pid", lambda _connection: os.getpid() + 999_000)

        service.last_client_at = time.monotonic() - 6.0
        status_response = client.request({"action": "status"})
        assert status_response["ok"] is True
        assert service.idle_due() is True, "an external status probe with no lease refreshed the idle clock"

        lease = client.registry.acquire_lease()
        assert lease["ok"] is True
        assert service.idle_due() is False, "acquiring a real lease did not refresh demand"

        assert client.registry.release_lease(lease["lease_id"])["ok"] is True
        service.last_client_at = time.monotonic() - 6.0
        assert service.idle_due() is True, "idle grace window did not elapse after the final lease released"
    finally:
        service.stop_event.set()
        worker.join(timeout=3.0)


def test_local_service_registry_serializes_starters_and_reuses_healthy_winner(tmp_path):
    socket_path = tmp_path / "indexer.sock"
    service = search_indexer.PersistentSearchIndexer(socket_path)
    worker = threading.Thread(target=service.run, daemon=True)
    worker.start()
    spec = LocalServiceSpec("indexd", "yolomux_lib.search.search_indexer", socket_path.name, search_indexer.INDEXER_PROTOCOL_VERSION)
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
        LocalServiceSpec("indexd", "yolomux_lib.search.search_indexer", socket_path.name, search_indexer.INDEXER_PROTOCOL_VERSION, idle_seconds=30.0),
        socket_path=socket_path,
    )
    registry.socket_path.parent.mkdir(parents=True, exist_ok=True)
    registry.socket_path.write_text("stale", encoding="utf-8")
    registry._write_record({"pid": 999_999_999, "service": "indexd"})

    assert registry.ensure_started() is True, registry.status()
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
