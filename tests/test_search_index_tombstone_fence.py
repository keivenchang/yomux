# Durable regression home for the BFS search-index lifecycle tombstone fence (0.7.3).
#
# These are the codex-root round-3 audit repros (originally staged under /tmp) for the cross-process
# unindex tombstone: publication may clear only a marker it superseded, every persisted read/adopt/
# resume/coverage surface fails closed on an explicit unindex, a restart rebuilds rather than re-adopts
# a tombstoned snapshot, and a partially-entered build/connection never leaks a descriptor.

import fcntl
import multiprocessing
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from yolomux_lib.search import file_index
from yolomux_lib.search import bfs_index
from yolomux_lib.search import search_indexer
from yolomux_lib.filesystem import search as filesystem_search


def _reset_registry():
    # P1-5 teardown: NEVER close a live worker's fd directly. The old reset closed each root fd out
    # from under a still-running build thread, so a worker opened the just-removed tmp SQLite path and
    # raised "unable to open database file" -- which `-W error::PytestUnhandledThreadExceptionWarning`
    # correctly turned into a test failure on the composed run. Instead: signal retirement through the
    # ONE lifecycle owner (advances the generation fence + cancel event), then WAIT for / finalize any
    # late worker (its own `_finalize_worker_exit` performs the deferred fd close), assert no late
    # retiree survives, and only THEN let temp state (tmp_path) disappear.
    file_index.clear_memory_indexes()
    deadline = time.monotonic() + 5.0
    while True:
        with file_index._REGISTRY_LOCK:
            retirees = list(file_index._RETIRING.values())
        if not retirees or time.monotonic() > deadline:
            break
        for index in retirees:
            thread = index.thread
            index.completion.wait(max(0.0, deadline - time.monotonic()))
            if thread is not None and thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))
            file_index._finalize_worker_exit(index)
    with file_index._REGISTRY_LOCK:
        late = [str(index.root) for index in file_index._RETIRING.values()]
        file_index._REGISTRY.clear()
        file_index._RETIRING.clear()
        file_index._PENDING_DROPS.clear()
        # P0-2: a deferred-drop retry owner is a daemon waiter; drop the observable references so one test's
        # background waiter never leaks into the next. Any still-blocked waiter is a no-op once its token is gone.
        file_index._PENDING_DROP_RETRIES.clear()
    assert not late, f"late search-index retirees survived teardown: {late}"


def test_ensure_revalidates_ownership_atomically_when_installing_root_fd(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: False)
    root = tmp_path / "root"
    root.mkdir()
    checked = threading.Event()
    resume = threading.Event()
    real_owner_is = file_index._registry_owner_is
    first = True

    def pause_after_success(index):
        nonlocal first
        result = real_owner_is(index)
        if first and result:
            first = False
            checked.set()
            assert resume.wait(3.0)
        return result

    monkeypatch.setattr(file_index, "_registry_owner_is", pause_after_success)
    result = {}

    def ensure():
        result["index"] = file_index.ensure_index(root, set())

    thread = threading.Thread(target=ensure, name="audit-ensure-race")
    thread.start()
    assert checked.wait(3.0)
    file_index.clear_memory_indexes()
    resume.set()
    thread.join(3.0)
    assert not thread.is_alive()
    index = result["index"]
    try:
        with file_index._REGISTRY_LOCK:
            assert file_index._REGISTRY.get(str(root)) is not index
            retired_still_tracked = id(index) in file_index._RETIRING
        assert (index.root_fd, retired_still_tracked) == (None, False)
    finally:
        index.close_root_fd()
        _reset_registry()


def test_final_ownership_failure_leaves_assignment_for_finalizer(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_next_bfs_generation", lambda _root: 1)
    root = tmp_path / "root"
    root.mkdir()
    index = file_index.RootIndex(root)
    index.root_fd = os.open(root, os.O_RDONLY)
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = index

    final_check = threading.Event()
    resume = threading.Event()
    real_owner_is = file_index._registry_owner_is
    calls = 0

    def pause_final_check(candidate):
        nonlocal calls
        calls += 1
        if calls == 2:
            final_check.set()
            assert resume.wait(3.0)
        return real_owner_is(candidate)

    monkeypatch.setattr(file_index, "_registry_owner_is", pause_final_check)
    result = {}

    def start():
        result["started"] = file_index._start_build(index, set())

    thread = threading.Thread(target=start, name="audit-start-race")
    thread.start()
    assert final_check.wait(3.0)
    retirement = file_index.clear_memory_indexes()
    assert root in retirement.late
    resume.set()
    thread.join(3.0)
    assert not thread.is_alive()
    try:
        assert result["started"] is False
        assert index.assignment is None
        assert index.thread is None
        with file_index._REGISTRY_LOCK:
            retired_still_tracked = id(index) in file_index._RETIRING
        assert (index.root_fd, retired_still_tracked) == (None, False)
    finally:
        file_index._finalize_worker_exit(index)
        _reset_registry()


def test_failed_thread_start_after_retirement_finalizes_installed_assignment(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_next_bfs_generation", lambda _root: 1)
    monkeypatch.setattr(file_index, "notify_background_owner_done", lambda _payload: None)
    monkeypatch.setattr(file_index, "touch_producer_heartbeat", lambda *_args, **_kwargs: None)
    root = tmp_path / "root"
    root.mkdir()
    index = file_index.RootIndex(root)
    index.root_fd = os.open(root, os.O_RDONLY)
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = index

    retirement = {}

    def fail_start(_worker, rollback):
        retirement["result"] = file_index.clear_memory_indexes()
        rollback()
        raise RuntimeError("forced Thread.start failure")

    monkeypatch.setattr(file_index, "start_thread_with_rollback", fail_start)
    try:
        with pytest.raises(RuntimeError, match="forced Thread.start failure"):
            file_index._start_build(index, set())
        assert root in retirement["result"].late
        assert index.assignment is None
        assert index.thread is None
        with file_index._REGISTRY_LOCK:
            retired_still_tracked = id(index) in file_index._RETIRING
        assert (index.root_fd, retired_still_tracked) == (None, False)
    finally:
        file_index._finalize_worker_exit(index)
        _reset_registry()


def test_indexer_restart_resumes_a_durable_partial_frontier_without_waiting_for_ttl(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("top", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("buried", encoding="utf-8")
    policy = filesystem_search._search_index_policy(root)

    build = bfs_index.ProgressiveBuild(
        root,
        policy["skip_dirs"],
        exclude_path=policy["exclude_path"],
        exclude_signature=policy["exclude_signature"],
        generation=1,
    )
    with build:
        assert build.enqueue_startup()
        build.step()
    before = file_index.read_index_coverage(root)
    assert before is not None and before["frontier_size"] > 0

    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    assert service.enqueue(str(root), [], reason=bfs_index.REASON_STARTUP)["ok"]
    service.pending_due_at[str(root.resolve())] = 0.0
    assert service.process_due() == 1
    with file_index._REGISTRY_LOCK:
        index = file_index._REGISTRY[str(root.resolve())]
    with index.lock:
        active = index.building or index.thread is not None
    after = file_index.read_index_coverage(root)
    try:
        assert after is not None
        assert active or after["frontier_size"] == 0
    finally:
        _reset_registry()


def test_tombstoned_snapshot_is_rejected_by_every_disk_read_surface(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    file_index.set_background_owner_checker(lambda _role: True)
    root = tmp_path / "root"
    root.mkdir()
    deleted = root / "deleted.txt"
    deleted.write_text("stale", encoding="utf-8")

    built = file_index.build_now(root, set())
    assert built.ready
    built_at = built.built_at
    file_index.clear_memory_indexes()
    deleted.unlink()
    file_index._tombstone_path(root).write_text(str(built_at + 100.0), encoding="utf-8")

    def make_entry(path, name, relative_path):
        return {
            "path": path,
            "name": name,
            "relative_path": relative_path,
            "_sort_key": (name,),
        }

    try:
        loaded = file_index._load_disk(root, set())
        opened = file_index._read_sqlite_index(root, set())
        if opened is not None:
            opened[0].close()
        searched = file_index.search_disk_index(root, set(), "", make_entry, 20, ["deleted"])
        recent = file_index.recent_disk_entries(root, set(), "", 20, make_entry)
        freshness = file_index.index_freshness(None, root, set(), now=built_at + 101.0)
        actual = {
            "load_disk_rejected": loaded is None,
            "read_sqlite_rejected": opened is None,
            "search_disk_rejected": searched is None,
            "recent_disk_rejected": recent is None,
            "freshness_state": freshness.state,
            "freshness_reason": freshness.reason,
        }
        assert actual == {
            "load_disk_rejected": True,
            "read_sqlite_rejected": True,
            "search_disk_rejected": True,
            "recent_disk_rejected": True,
            "freshness_state": file_index.FRESHNESS_MISSING,
            "freshness_reason": "snapshot_tombstoned",
        }
    finally:
        file_index.set_background_owner_checker(None)
        _reset_registry()


def test_retirement_registration_cannot_land_after_the_worker_finalized(tmp_path, monkeypatch):
    _reset_registry()
    root = tmp_path / "root"
    root.mkdir()
    index = file_index.RootIndex(root)
    index.root_fd = os.open(root, os.O_RDONLY)
    completion = threading.Event()
    assignment = file_index._WorkerAssignment(
        generation=1,
        thread=threading.current_thread(),
        completion=completion,
    )
    index.assignment = assignment
    index.thread = assignment.thread
    index.completion = completion

    class FinalizeBeforeRegister(dict):
        def __init__(self):
            super().__init__()
            self.triggered = False

        def __setitem__(self, key, value):
            if not self.triggered:
                self.triggered = True
                file_index._finalize_worker_exit(value, assignment)
            super().__setitem__(key, value)

    retiring = FinalizeBeforeRegister()
    monkeypatch.setattr(file_index, "_REGISTRY_LOCK", threading.RLock())
    monkeypatch.setattr(file_index, "_RETIRING", retiring)

    file_index._signal_retirement(index)
    assert completion.is_set()
    assert index.root_fd is None
    assert id(index) not in retiring


def test_successful_bfs_publication_supersedes_a_pending_drop(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", bfs_index.build_root_into_index)
    root = tmp_path / "root"
    root.mkdir()
    (root / "first.txt").write_text("first", encoding="utf-8")

    first = file_index.build_now(root, set())
    db_path = file_index._index_disk_path(root)
    assert first.ready and db_path.exists()
    file_index.clear_memory_indexes()

    old = file_index.RootIndex(root)
    old.root_fd = os.open(root, os.O_RDONLY)
    release = threading.Event()
    completion = threading.Event()

    def blocked_worker():
        try:
            release.wait(3.0)
        finally:
            file_index._finalize_worker_exit(old, assignment)

    thread = threading.Thread(target=blocked_worker, name="audit-old-retiree")
    assignment = file_index._WorkerAssignment(generation=1, thread=thread, completion=completion)
    old.assignment = assignment
    old.thread = thread
    old.completion = completion
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = old
    thread.start()

    file_index.unindex(root)
    assert db_path.exists()
    with file_index._REGISTRY_LOCK:
        assert file_index._canonical_root_key(root) in file_index._PENDING_DROPS

    real_load_disk = file_index._load_disk
    load_calls = 0

    def force_new_bfs_publication(*args, **kwargs):
        nonlocal load_calls
        load_calls += 1
        if load_calls == 1:
            return None
        return real_load_disk(*args, **kwargs)

    monkeypatch.setattr(file_index, "_load_disk", force_new_bfs_publication)
    (root / "second.txt").write_text("second", encoding="utf-8")
    successor = file_index.build_now(root, set())
    assert successor.ready and db_path.exists()

    release.set()
    completion.wait(3.0)
    thread.join(3.0)
    assert not thread.is_alive()
    assert db_path.exists()

    file_index.clear_memory_indexes()
    try:
        assert db_path.exists(), "demoting the new BFS owner executed an obsolete pending drop"
    finally:
        _reset_registry()


def test_publication_cannot_supersede_an_unindex_requested_after_build_started(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", None)
    root = tmp_path / "root"
    root.mkdir()
    (root / "first.txt").write_text("first", encoding="utf-8")
    index = file_index.RootIndex(root)
    index.root_fd = os.open(root, os.O_RDONLY)
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = index

    publication_ready = threading.Event()
    resume_publication = threading.Event()
    # Reconciled to the no-clear model: publication no longer calls `_clear_tombstone`. The pause is
    # repointed to `_stamp_snapshot_tombstone_identity` -- the observable step publication now runs
    # BEFORE `_supersede_pending_drop` -- preserving the intent: a pending drop created by an unindex
    # AFTER the build started (a different token) is not superseded, so the db is deleted.
    real_stamp = file_index._stamp_snapshot_tombstone_identity

    def pause_before_pending_drop_supersession(candidate_root, identity):
        real_stamp(candidate_root, identity)
        publication_ready.set()
        assert resume_publication.wait(3.0)

    monkeypatch.setattr(file_index, "_stamp_snapshot_tombstone_identity", pause_before_pending_drop_supersession)
    assert file_index._start_build(index, set())
    assert publication_ready.wait(3.0)
    db_path = file_index._index_disk_path(root)
    assert db_path.exists()

    file_index.unindex(root)
    with file_index._REGISTRY_LOCK:
        assert file_index._canonical_root_key(root) in file_index._PENDING_DROPS
    resume_publication.set()
    assert index.completion.wait(3.0)
    index.thread.join(3.0) if index.thread is not None else None
    try:
        assert not db_path.exists(), "publication erased an unindex intent created after the build started"
    finally:
        _reset_registry()


def test_publication_cannot_clear_a_tombstone_written_by_a_newer_unindex(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", None)
    root = tmp_path / "root"
    root.mkdir()
    (root / "first.txt").write_text("first", encoding="utf-8")
    index = file_index.RootIndex(root)
    index.root_fd = os.open(root, os.O_RDONLY)
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = index

    publication_ready = threading.Event()
    resume_publication = threading.Event()
    real_complete_publication = file_index._complete_publication

    # The pause hook now accepts the OPTIONAL `captured_tombstone_identity` kwarg publication threads in.
    # Under the no-clear model publication never touches the marker file, so the intent (a newer
    # unindex's tombstone survives the older build's publication) holds by construction; the assertion
    # still proves the durable marker is never cleared.
    def pause_before_publication_completion(candidate, *, captured_drop_token, captured_tombstone_identity=None):
        publication_ready.set()
        assert resume_publication.wait(3.0)
        real_complete_publication(
            candidate,
            captured_drop_token=captured_drop_token,
            captured_tombstone_identity=captured_tombstone_identity,
        )

    monkeypatch.setattr(file_index, "_complete_publication", pause_before_publication_completion)
    assert file_index._start_build(index, set())
    assert publication_ready.wait(3.0)

    file_index.unindex(root)
    tombstone = file_index._tombstone_path(root)
    assert tombstone.exists()
    resume_publication.set()
    assert index.completion.wait(3.0)
    try:
        assert tombstone.exists(), "publication cleared a tombstone written by a newer unindex"
    finally:
        _reset_registry()


def test_indexer_restart_finishes_the_exact_partial_generation(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("top", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("buried", encoding="utf-8")
    policy = filesystem_search._search_index_policy(root)

    build = bfs_index.ProgressiveBuild(
        root,
        policy["skip_dirs"],
        exclude_path=policy["exclude_path"],
        exclude_signature=policy["exclude_signature"],
        generation=1,
    )
    with build:
        assert build.enqueue_startup()
        build.step()
    db_path = file_index._index_disk_path(root)
    inode = db_path.stat().st_ino
    before = file_index.read_index_coverage(root)
    assert before is not None and before["active_generation"] == 1 and before["frontier_size"] > 0

    service = search_indexer.PersistentSearchIndexer(tmp_path / "indexer.sock")
    assert service.enqueue(str(root), [], reason=bfs_index.REASON_STARTUP)["ok"]
    service.pending_due_at[str(root.resolve())] = 0.0
    assert service.process_due() == 1
    with file_index._REGISTRY_LOCK:
        index = file_index._REGISTRY[str(root.resolve())]
    with index.lock:
        assignment = index.assignment
    assert assignment is not None
    assignment.thread.join(5.0)
    assert not assignment.thread.is_alive()
    after = file_index.read_index_coverage(root)
    try:
        assert after is not None
        assert after["active_generation"] == 1
        assert after["frontier_size"] == 0
        assert after["full_coverage"] is True
        assert db_path.stat().st_ino == inode
        opened = file_index._read_sqlite_index(root, policy["skip_dirs"], policy["exclude_signature"])
        assert opened is not None
        conn, _metadata = opened
        try:
            names = {row[0] for row in conn.execute("SELECT name FROM entries")}
        finally:
            conn.close()
        assert {"top.txt", "buried.txt"}.issubset(names)
    finally:
        _reset_registry()


def test_unindex_cannot_write_its_tombstone_after_a_superseding_rebuild(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", None)
    root = tmp_path / "root"
    root.mkdir()
    (root / "first.txt").write_text("first", encoding="utf-8")
    file_index.build_now(root, set())
    file_index.clear_memory_indexes()

    old = file_index.RootIndex(root)
    old.root_fd = os.open(root, os.O_RDONLY)
    release = threading.Event()
    completion = threading.Event()

    def blocked_worker():
        try:
            release.wait(5.0)
        finally:
            file_index._finalize_worker_exit(old, assignment)

    thread = threading.Thread(target=blocked_worker, name="audit-tombstone-old-retiree")
    assignment = file_index._WorkerAssignment(generation=1, thread=thread, completion=completion)
    old.assignment = assignment
    old.thread = thread
    old.completion = completion
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = old
    thread.start()

    drop_requested = threading.Event()
    real_request = file_index._request_pending_drop

    def record_request(candidate_root):
        token = real_request(candidate_root)
        drop_requested.set()
        return token

    monkeypatch.setattr(file_index, "_request_pending_drop", record_request)
    unindex_thread = threading.Thread(target=file_index.unindex, args=(root,), name="audit-unindex")
    unindex_thread.start()
    assert drop_requested.wait(3.0)

    rebuilt = file_index.build_now(root, set())
    assert rebuilt.ready
    rebuilt_at = rebuilt.built_at
    release.set()
    unindex_thread.join(3.0)
    assert not unindex_thread.is_alive()
    thread.join(3.0)
    tombstone = file_index._tombstone_path(root)
    try:
        assert not tombstone.exists() or file_index._tombstone_time(root) <= rebuilt_at, (
            "unindex wrote a stale marker after the newer build had superseded its delete token"
        )
    finally:
        _reset_registry()


def test_sqlite_connection_is_closed_when_setup_pragma_fails(tmp_path, monkeypatch):
    closed = threading.Event()

    class FailingConnection:
        def execute(self, _statement):
            raise file_index.sqlite3.OperationalError("forced pragma failure")

        def close(self):
            closed.set()

    monkeypatch.setattr(file_index, "preflight_mutable_roots", lambda **_kwargs: None)
    monkeypatch.setattr(file_index.sqlite3, "connect", lambda *_args, **_kwargs: FailingConnection())
    with pytest.raises(file_index.sqlite3.OperationalError, match="forced pragma failure"):
        with file_index._sqlite_index_connection(tmp_path / "root"):
            pass
    assert closed.is_set(), "a connection created before a failing PRAGMA was never closed"


def test_progressive_build_enter_closes_lock_fd_when_root_open_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    missing = tmp_path / "missing-root"
    build = bfs_index.ProgressiveBuild(missing, set(), generation=1)
    with pytest.raises(FileNotFoundError):
        with build:
            pass
    leaked = build._lock_fd
    try:
        assert leaked is None, "failed __enter__ retained the per-root build lock descriptor"
    finally:
        if leaked is not None:
            os.close(leaked)


def test_restart_rebuilds_and_stamps_the_current_identity_while_the_tombstone_remains(tmp_path, monkeypatch):
    # Reconciled to the no-clear identity model. The artificial built_at bump is retired, so the old
    # `restarted.built_at > tombstone_time` proof is unsatisfiable by time. Converted to a NEW-format
    # identity marker (written via `_write_tombstone`): the restart must NOT re-adopt the stale
    # pre-tombstone snapshot, must rebuild, and its snapshot must be accepted BY IDENTITY -- stamped
    # with the CURRENT tombstone identity and readable by `_read_sqlite_index` while the marker stands.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", bfs_index.build_root_into_index)
    root = tmp_path / "root"
    root.mkdir()
    stale_file = root / "deleted.txt"
    stale_file.write_text("stale", encoding="utf-8")
    (root / "kept.txt").write_text("kept", encoding="utf-8")
    built = file_index.build_now(root, set())
    assert any(name == "deleted.txt" for _path, name, _rel, _size, _mtime in built.entries)
    file_index.clear_memory_indexes()
    stale_file.unlink()

    # New-format durable identity marker (never cleared): the restart proves supersession by identity.
    identity = file_index._write_tombstone(root)
    restarted = file_index.ensure_index(root, set())
    with restarted.lock:
        assignment = restarted.assignment
    assert assignment is not None
    assignment.thread.join(5.0)
    assert not assignment.thread.is_alive()
    try:
        # The rebuild ran, is ready/searchable, and dropped the deleted file.
        assert restarted.ready
        assert all(name != "deleted.txt" for _path, name, _rel, _size, _mtime in restarted.entries)
        assert any(name == "kept.txt" for _path, name, _rel, _size, _mtime in restarted.entries)
        # The durable marker is STILL present, and the fresh snapshot is accepted by identity.
        assert file_index._tombstone_path(root).exists()
        assert file_index._current_tombstone_identity(root) == identity
        opened = file_index._read_sqlite_index(root, set())
        assert opened is not None
        conn, metadata = opened
        try:
            assert metadata.get("tombstone_identity") == identity
            names = {row[0] for row in conn.execute("SELECT name FROM entries")}
        finally:
            conn.close()
        assert "deleted.txt" not in names and "kept.txt" in names
    finally:
        _reset_registry()


def test_completed_snapshot_consumers_reject_a_tombstone(tmp_path, monkeypatch):
    """Coverage, candidate-root discovery, and persisted-child discovery all fail closed on an
    explicit unindex of a COMPLETED snapshot (the sibling consumers that bypass `_load_disk`)."""
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    file_index.set_background_owner_checker(lambda _role: True)
    parent = tmp_path / "parent"
    root = parent / "child"
    root.mkdir(parents=True)
    (root / "top.txt").write_text("top", encoding="utf-8")

    built = file_index.build_now(root, set())
    assert built.ready
    built_at = built.built_at
    file_index.clear_memory_indexes()
    file_index._tombstone_path(root).write_text(str(built_at + 100.0), encoding="utf-8")

    try:
        resolved = root.resolve()
        assert file_index.read_index_coverage(root) is None
        assert resolved not in set(file_index._iter_candidate_index_roots())
        assert resolved not in file_index.persisted_index_roots_within(parent)
    finally:
        file_index.set_background_owner_checker(None)
        _reset_registry()


def test_partial_frontier_resume_and_promote_reject_a_tombstone(tmp_path, monkeypatch):
    """A durable PARTIAL with a pending frontier neither resumes nor promotes once the snapshot is
    tombstoned: post-unindex work must start a fresh generation, never continue crawling a deleted store."""
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("top", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("buried", encoding="utf-8")
    policy = filesystem_search._search_index_policy(root)

    build = bfs_index.ProgressiveBuild(
        root,
        policy["skip_dirs"],
        exclude_path=policy["exclude_path"],
        exclude_signature=policy["exclude_signature"],
        generation=1,
    )
    with build:
        assert build.enqueue_startup()
        build.step()
    assert file_index._resumable_frontier_generation(root) == 1
    built_at = file_index._metadata_built_at(
        file_index._raw_snapshot_metadata(root, policy["skip_dirs"], policy["exclude_signature"])
    )
    file_index._tombstone_path(root).write_text(str(built_at + 100.0), encoding="utf-8")

    try:
        assert file_index._resumable_frontier_generation(root) is None
        assert file_index.promote_frontier(root) == 0
    finally:
        _reset_registry()


# --------------------------------------------------------------------------------------------------
# Cross-process repros (codex-root round-4 deterministic audit), moved in from the scratch file and
# reconciled to the no-clear identity-stamp protocol. Each was RED on the guarded-clear tree.
# --------------------------------------------------------------------------------------------------


def _hold_cross_process_index_store(index_dir, root_text, ready, release):
    file_index.INDEX_DIR = Path(index_dir)
    root = Path(root_text)
    lock_fd = os.open(file_index._build_lock_path(root), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    connection = sqlite3.connect(file_index._index_disk_path(root))
    try:
        ready.set()
        assert release.wait(5.0)
        connection.execute("SELECT COUNT(*) FROM metadata").fetchone()
    finally:
        connection.close()
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_build_started_before_cross_process_unindex_cannot_clear_new_tombstone(tmp_path, monkeypatch):
    # Repro 1: a build freezes NO tombstone identity, then a simulated second process writes a NEW
    # tombstone before the BFS runner publishes. The old build's publication stamps the OLD (empty)
    # identity, so the newer marker survives and its snapshot is rejected by identity.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "stale.txt").write_text("stale", encoding="utf-8")
    real_runner = bfs_index.build_root_into_index
    written = {}

    def unindex_in_other_process_after_assignment(*args, **kwargs):
        written["identity"] = file_index._write_tombstone(root)
        written["deletion_time"] = file_index._tombstone_time(root)
        return real_runner(*args, **kwargs)

    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", unindex_in_other_process_after_assignment)

    def _match(path_str, name, rel):
        return {"name": name, "path": path_str, "relative_path": rel, "_sort_key": (name,)}

    try:
        built = file_index.build_now(root, set())
        # P0-1 (round-3): the publication RE-VERIFIES the frozen identity against the current marker under
        # `ri.lock`. It froze the OLD (empty) identity, which a newer marker rejects, so the build lands
        # EVICTED at publication -- it never goes ready or serves the deleted row.
        assert not built.ready, "a build whose frozen identity a newer marker rejects went ready anyway"
        # The AUTHORITATIVE disk snapshot published AFTER the marker's deletion time, so a time-only fence
        # would wrongly accept it; the identity fence rejects it.
        assert file_index._metadata_built_at(file_index._authoritative_store_metadata(root)) > written["deletion_time"], (
            "the pre-unindex build published after the marker time"
        )
        assert file_index._tombstone_path(root).exists(), (
            "a build that froze the pre-unindex identity cleared a newer cross-process tombstone"
        )
        assert file_index._current_tombstone_identity(root) == written["identity"]
        assert file_index._read_sqlite_index(root, set()) is None
        assert built.published_tombstone_identity in (None, "")
        assert file_index._root_index_is_tombstoned(built)
        monkeypatch.setattr(file_index, "background_owner_can_build", lambda: False)
        evicted = file_index.ensure_index(root, set())
        assert not evicted.ready, "ensure_index kept serving a tombstoned in-memory owner"
        served, _truncated = file_index.search_index(evicted, _match, 20)
        assert all(entry["name"] != "stale.txt" for entry in served), (
            "search_index served a deleted row from a tombstoned in-memory owner"
        )
    finally:
        _reset_registry()


def test_tombstone_arriving_during_publication_is_not_deleted(tmp_path, monkeypatch):
    # Repro 2, reconciled: publication no longer reads `_tombstone_time` or unlinks the marker, so the
    # original time-check/unlink TOCTOU cannot exist. The pause is repointed to the observable step
    # publication now runs (`_stamp_snapshot_tombstone_identity`) to prove a tombstone written WHILE a
    # publication is in flight survives it.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    index = file_index.RootIndex(root)
    index.built_at = time.time()
    checked = threading.Event()
    resume = threading.Event()
    real_stamp = file_index._stamp_snapshot_tombstone_identity

    def paused_stamp(candidate_root, identity):
        checked.set()
        assert resume.wait(3.0)
        return real_stamp(candidate_root, identity)

    monkeypatch.setattr(file_index, "_stamp_snapshot_tombstone_identity", paused_stamp)
    worker = threading.Thread(
        target=file_index._complete_publication,
        kwargs={"ri": index, "captured_drop_token": None},
        name="audit-publication-stamp-race",
    )
    worker.start()
    assert checked.wait(3.0)
    identity = file_index._write_tombstone(root)
    resume.set()
    worker.join(3.0)
    try:
        assert not worker.is_alive()
        assert file_index._tombstone_path(root).exists(), (
            "publication unlinked a tombstone that arrived while it was in flight"
        )
        assert file_index._current_tombstone_identity(root) == identity
    finally:
        _reset_registry()


def test_malformed_current_tombstone_fails_closed(tmp_path, monkeypatch):
    # Repro 3: a PRESENT but unparseable marker is deletion authority, not absence. Every follower disk
    # read must fail closed on it rather than serve the deleted rows.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "deleted.txt").write_text("deleted", encoding="utf-8")
    built = file_index.build_now(root, set())
    assert built.ready
    file_index.clear_memory_indexes()
    file_index._tombstone_path(root).write_text("opaque-id\ninvalid-time\n", encoding="utf-8")
    try:
        assert file_index._read_tombstone(root) is file_index._TOMBSTONE_MALFORMED
        assert file_index._read_sqlite_index(root, set()) is None
    finally:
        _reset_registry()


def test_cross_process_unindex_does_not_unlink_store_under_live_writer(tmp_path, monkeypatch):
    # Repro 4: another process holds the per-root build lock and an open SQLite connection. This
    # process's `unindex` must DEFER the physical delete -- never unlink the database underneath the
    # live cross-process writer (P0-3).
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    (root / "indexed.txt").write_text("indexed", encoding="utf-8")
    built = file_index.build_now(root, set())
    assert built.ready
    file_index.clear_memory_indexes()
    database = file_index._index_disk_path(root)
    assert database.exists()

    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    writer = context.Process(
        target=_hold_cross_process_index_store,
        args=(str(file_index.INDEX_DIR), str(root), ready, release),
    )
    writer.start()
    assert ready.wait(3.0)
    key = file_index._canonical_root_key(root)
    retry = None
    try:
        file_index.unindex(root)
        assert database.exists(), "unindex unlinked SQLite while another process held the build lock"
        # P0-2: the deferred delete must be handed to a retry owner blocking for the build lock, not left
        # queued with nothing to complete it.
        with file_index._REGISTRY_LOCK:
            assert key in file_index._PENDING_DROPS
            retry = file_index._PENDING_DROP_RETRIES.get(key)
        assert retry is not None, "a deferred cross-process drop was left with no retry owner"
    finally:
        release.set()
        writer.join(5.0)
        if writer.is_alive():
            writer.terminate()
            writer.join(3.0)
    # The external writer released the build lock: the retry owner completes the drop OFF the request
    # thread. Wait on its settle event, then prove the physical store is gone and the retry state retired.
    assert retry.completion.wait(5.0), "the deferred-drop retry owner never settled after lock release"
    retry.thread.join(5.0)
    try:
        assert writer.exitcode == 0
        for path in [
            *file_index._sqlite_paths(root),
            file_index._index_manifest_path(root),
            file_index._producer_heartbeat_path(root),
        ]:
            assert not path.exists(), f"the deferred drop left {path.name} behind after lock release"
        with file_index._REGISTRY_LOCK:
            assert key not in file_index._PENDING_DROPS
            assert key not in file_index._PENDING_DROP_RETRIES
    finally:
        _reset_registry()


def test_pre_unindex_stamp_is_rejected_and_post_unindex_clean_generation_is_accepted(tmp_path, monkeypatch):
    # Codex acceptance metadata assertions, one composed proof of protocol #1/#2/#3:
    #   * a PRE-unindex build stamps the OLD identity (none) and is REJECTED once the marker changes;
    #   * a POST-unindex clean generation stamps the CURRENT identity and is ACCEPTED, with the
    #     tombstone STILL PRESENT (never cleared).
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", bfs_index.build_root_into_index)
    root = tmp_path / "root"
    root.mkdir()
    (root / "before.txt").write_text("before", encoding="utf-8")

    # Pre-unindex build: no tombstone frozen, so it stamps the empty identity.
    file_index.build_now(root, set())
    pre = file_index._read_sqlite_index(root, set())
    assert pre is not None
    pre_conn, pre_metadata = pre
    try:
        assert pre_metadata.get("tombstone_identity") in (None, "")
    finally:
        pre_conn.close()
    file_index.clear_memory_indexes()

    # A newer unindex writes a fresh identity marker. The pre-unindex stamp now differs, so the
    # snapshot is REJECTED even though it is physically present.
    identity = file_index._write_tombstone(root)
    assert file_index._read_sqlite_index(root, set()) is None

    # Post-unindex build: freezes the current identity, establishes a clean generation, stamps it.
    (root / "after.txt").write_text("after", encoding="utf-8")
    rebuilt = file_index.build_now(root, set())
    assert rebuilt.ready
    try:
        assert file_index._tombstone_path(root).exists(), "the durable tombstone was cleared on rebuild"
        assert file_index._current_tombstone_identity(root) == identity
        opened = file_index._read_sqlite_index(root, set())
        assert opened is not None, "a post-unindex clean generation stamped with the current identity was rejected"
        conn, metadata = opened
        try:
            assert metadata.get("tombstone_identity") == identity
            names = {row[0] for row in conn.execute("SELECT name FROM entries")}
        finally:
            conn.close()
        assert "after.txt" in names
    finally:
        _reset_registry()


# --------------------------------------------------------------------------------------------------
# codex-root round-2 re-audit findings: the in-memory serving owner, the deferred-drop retry owner,
# and sqlite/manifest identity divergence. Each is RED on the round-1 (identity-stamp) tree.
# --------------------------------------------------------------------------------------------------


def test_pre_build_remote_unindex_race_evicts_the_live_in_memory_owner(tmp_path, monkeypatch):
    # P0-1: the DISK protocol is identity-based, but the in-memory owner was time-based. A build that
    # started before another process unindexed but PUBLISHED after the marker's deletion time stamps the
    # OLD identity on disk (correctly rejected there) while keeping ready=True and serving deleted rows
    # from RAM. The in-memory owner must be judged by the SAME `_snapshot_is_tombstoned` verdict.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "stale.txt").write_text("stale", encoding="utf-8")
    real_runner = bfs_index.build_root_into_index
    written = {}

    def remote_unindex_after_assignment(*args, **kwargs):
        written["identity"] = file_index._write_tombstone(root)
        written["deletion_time"] = file_index._tombstone_time(root)
        return real_runner(*args, **kwargs)

    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", remote_unindex_after_assignment)

    def _match(path_str, name, rel):
        return {"name": name, "path": path_str, "relative_path": rel, "_sort_key": (name,)}

    try:
        built = file_index.build_now(root, set())
        # P0-1 (round-3): the publication re-verifies the frozen identity under `ri.lock` and lands EVICTED
        # rather than going ready with a stale identity.
        assert not built.ready, "the stale-identity build went ready instead of landing evicted"
        # The authoritative disk snapshot published AFTER the newer marker's deletion time, so a time-only
        # fence would wrongly accept it; the identity fence rejects it.
        assert file_index._metadata_built_at(file_index._authoritative_store_metadata(root)) > written["deletion_time"]
        # Disk read fails closed by identity.
        assert file_index._read_sqlite_index(root, set()) is None
        # Own-index freshness must NOT be FRESH for a tombstoned owner.
        freshness = file_index.index_freshness(built, root, set())
        assert freshness.state != file_index.FRESHNESS_FRESH
        assert not freshness.authoritative
        # Registry-root discovery must not advertise the invalid snapshot.
        assert root.resolve() not in set(file_index._iter_candidate_index_roots())
        # ensure_index must EVICT the tombstoned in-memory owner; disable rebuild to observe it cleanly.
        monkeypatch.setattr(file_index, "background_owner_can_build", lambda: False)
        evicted = file_index.ensure_index(root, set())
        assert not evicted.ready, "ensure_index kept a tombstoned in-memory owner ready"
        served, _ = file_index.search_index(evicted, _match, 20)
        assert all(entry["name"] != "stale.txt" for entry in served), (
            "search_index served a deleted row from a tombstoned in-memory owner"
        )
    finally:
        _reset_registry()


def test_deferred_drop_retry_is_a_noop_when_a_current_identity_build_supersedes_the_token(tmp_path, monkeypatch):
    # P0-2 opposing race: a post-unindex current-identity build supersedes the captured drop token before
    # the deferred-drop waiter acquires the build lock. The waiter must recheck the EXACT token under the
    # lock and become a no-op -- the republished store must SURVIVE.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    (root / "indexed.txt").write_text("indexed", encoding="utf-8")
    built = file_index.build_now(root, set())
    assert built.ready
    file_index.clear_memory_indexes()
    database = file_index._index_disk_path(root)
    assert database.exists()

    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    writer = context.Process(
        target=_hold_cross_process_index_store,
        args=(str(file_index.INDEX_DIR), str(root), ready, release),
    )
    writer.start()
    assert ready.wait(3.0)
    key = file_index._canonical_root_key(root)
    retry = None
    try:
        file_index.unindex(root)
        with file_index._REGISTRY_LOCK:
            token = file_index._PENDING_DROPS.get(key)
            retry = file_index._PENDING_DROP_RETRIES.get(key)
        assert token is not None and retry is not None
        # What a post-unindex current-identity build's `_complete_publication` does after its authoritative
        # sqlite commit: supersede exactly this captured token, BEFORE the waiter runs its recheck.
        file_index._supersede_pending_drop(root, token)
        with file_index._REGISTRY_LOCK:
            assert key not in file_index._PENDING_DROPS
    finally:
        release.set()
        writer.join(5.0)
        if writer.is_alive():
            writer.terminate()
            writer.join(3.0)
    assert retry.completion.wait(5.0), "the deferred-drop retry owner never settled"
    retry.thread.join(5.0)
    try:
        assert writer.exitcode == 0
        assert database.exists(), (
            "the deferred-drop waiter deleted a store a current-identity build had already superseded"
        )
    finally:
        _reset_registry()


def test_manifest_replace_failure_cannot_diverge_from_the_authoritative_sqlite_identity(tmp_path, monkeypatch):
    # P1-3: SQLite is the authoritative committed snapshot; the manifest is a derived cache. If the
    # manifest replace fails while the sqlite identity commit lands, every manifest-first reader (status
    # metadata, root discovery) must fall back to the authoritative sqlite so it agrees with what the
    # sqlite search reader serves -- no state may advertise one identity while serving another.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    (root / "kept.txt").write_text("kept", encoding="utf-8")
    file_index.build_now(root, set())
    file_index.clear_memory_indexes()

    # A newer unindex writes a fresh identity marker; the pre-unindex empty stamp now differs from it.
    identity = file_index._write_tombstone(root)
    # Simulate a build that COMMITTED the current identity to the authoritative sqlite but whose manifest
    # replace failed (the derived cache is left stale at the old empty identity).
    monkeypatch.setattr(file_index, "_reconcile_manifest_tombstone_identity", lambda _root, _value: None)
    assert file_index._stamp_snapshot_tombstone_identity(root, identity) is True

    try:
        # The sqlite search reader accepts the store (its stamp matches the marker).
        opened = file_index._read_sqlite_index(root, set())
        assert opened is not None
        opened[0].close()
        # The stale manifest alone would reject; the manifest-first consumers must fall back to sqlite.
        assert file_index._disk_snapshot_metadata(root, set()) is not None, (
            "status metadata rejected a store the sqlite search reader serves (stale manifest divergence)"
        )
        assert root.resolve() in set(file_index._iter_candidate_index_roots()), (
            "root discovery hid a store the sqlite search reader serves (stale manifest divergence)"
        )
    finally:
        _reset_registry()


def test_publication_does_not_supersede_drop_when_authoritative_sqlite_stamp_fails(tmp_path, monkeypatch):
    # P1-3: the authoritative sqlite stamp is the commit that proves a build superseded the deletion.
    # If it fails, `_complete_publication` must NOT supersede the durable-drop intent as though both the
    # sqlite and manifest stores had committed the identity.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    identity = file_index._write_tombstone(root)
    key = file_index._canonical_root_key(root)
    token = file_index._request_pending_drop(root)
    # The authoritative sqlite commit did not land (no durable store / injected metadata-commit failure).
    monkeypatch.setattr(file_index, "_stamp_snapshot_tombstone_identity", lambda _root, _identity: False)
    ri = file_index.RootIndex(root)
    try:
        file_index._complete_publication(ri, captured_drop_token=token, captured_tombstone_identity=identity)
        with file_index._REGISTRY_LOCK:
            assert file_index._PENDING_DROPS.get(key) == token, (
                "publication superseded a durable drop though the authoritative sqlite stamp did not commit"
            )
    finally:
        _reset_registry()


# --------------------------------------------------------------------------------------------------
# codex-root round-3 RESIDUAL findings: the serve-point TOCTOU, the one-shot retry owner, and the
# manifest-accepting path that never consults authoritative sqlite. Each is RED on the round-2 tree.
# --------------------------------------------------------------------------------------------------


def test_serve_re_evicts_when_a_remote_unindex_lands_after_the_readiness_check(tmp_path, monkeypatch):
    # P0-1 residual TOCTOU: `ensure_index` applies the tombstone verdict and leaves the owner ready, but
    # the live serve reads the rows LATER (`_search_files_from_safe_root` checks `index.ready`, then calls
    # `search_index`). A remote unindex landing AFTER the readiness check but BEFORE the serve must not
    # return stale rows: the ONE serving accessor re-applies the eviction verdict at read time.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "stale.txt").write_text("stale", encoding="utf-8")

    built = file_index.build_now(root, set())
    assert built.ready
    assert any(name == "stale.txt" for _p, name, _r, _s, _m in built.entries)

    def _match(path_str, name, rel):
        return {"name": name, "path": path_str, "relative_path": rel, "_sort_key": (name,)}

    # The readiness check has already passed (the owner is ready and NOT yet tombstoned). A remote process
    # now unindexes: a fresh identity marker the ready owner's frozen (empty) stamp cannot match.
    assert not file_index._root_index_is_tombstoned(built)
    file_index._write_tombstone(root)
    assert file_index._root_index_is_tombstoned(built)

    try:
        served, _truncated = file_index.search_index(built, _match, 20)
        assert served == [], "search_index served stale rows after a remote unindex landed post-readiness-check"
        assert not built.ready, "the serving accessor did not evict the tombstoned in-memory owner at read time"
        # The empty-query path (`recent_entries`) routes through the SAME serving accessor.
        recent, _recent_truncated = file_index.recent_entries(built, 20, _match)
        assert recent == [], "recent_entries served stale rows from a tombstoned in-memory owner"
    finally:
        _reset_registry()


def test_deferred_drop_retry_survives_an_unlink_failure_and_completes_on_the_next_attempt(tmp_path, monkeypatch):
    # P0-2 residual: the retry owner was one-shot and deleted the pending token BEFORE the physical unlink,
    # swallowing an unlink OSError and orphaning the store with no owner. A transient unlink failure must
    # NOT delete the token or drop the owner: both survive, and a re-armed attempt completes the deletion
    # once the fault clears.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "_PENDING_DROP_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    root = tmp_path / "root"
    root.mkdir()
    (root / "indexed.txt").write_text("indexed", encoding="utf-8")
    built = file_index.build_now(root, set())
    assert built.ready
    file_index.clear_memory_indexes()
    database = file_index._index_disk_path(root)
    assert database.exists()

    key = file_index._canonical_root_key(root)
    token = file_index._request_pending_drop(root)

    real_drop = file_index._drop_persisted_index
    first_failed = threading.Event()
    resume_second = threading.Event()
    calls = {"n": 0}

    def flaky_drop(candidate_root):
        calls["n"] += 1
        if calls["n"] == 1:
            first_failed.set()
            # A transient unlink failure that `_drop_persisted_index` reports; the files remain on disk.
            return False
        assert resume_second.wait(3.0)
        return real_drop(candidate_root)

    monkeypatch.setattr(file_index, "_drop_persisted_index", flaky_drop)
    file_index._schedule_pending_drop_retry(root)
    with file_index._REGISTRY_LOCK:
        retry = file_index._PENDING_DROP_RETRIES.get(key)
    assert retry is not None, "the deferred drop was scheduled with no retry owner"

    assert first_failed.wait(3.0)
    # The first attempt failed: the token AND a retry owner MUST still be present (not orphaned), and the
    # physical store must still exist.
    with file_index._REGISTRY_LOCK:
        assert file_index._PENDING_DROPS.get(key) == token, "an unlink failure deleted the pending-drop token"
        assert file_index._PENDING_DROP_RETRIES.get(key) is not None, "an unlink failure dropped the retry owner"
    assert database.exists(), "an unlink failure left no store yet reported the drop complete"

    # Clear the fault: the re-armed attempt (reusing the original completion event) completes the deletion.
    resume_second.set()
    assert retry.completion.wait(5.0), "the re-armed deferred-drop retry never settled"
    try:
        for path in [
            *file_index._sqlite_paths(root),
            file_index._index_manifest_path(root),
            file_index._producer_heartbeat_path(root),
        ]:
            assert not path.exists(), f"the completed retry left {path.name} behind"
        with file_index._REGISTRY_LOCK:
            assert key not in file_index._PENDING_DROPS
            assert key not in file_index._PENDING_DROP_RETRIES
    finally:
        _reset_registry()


def test_deferred_drop_retry_reschedules_on_an_open_failure_instead_of_orphaning(tmp_path, monkeypatch):
    # P0-2: an open/flock fault at the background-unit boundary must RESCHEDULE (keep the token + a retry
    # owner) rather than remove the owner and orphan the store on disk.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "_PENDING_DROP_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    root = tmp_path / "root"
    root.mkdir()
    (root / "indexed.txt").write_text("indexed", encoding="utf-8")
    built = file_index.build_now(root, set())
    assert built.ready
    file_index.clear_memory_indexes()
    database = file_index._index_disk_path(root)
    assert database.exists()

    key = file_index._canonical_root_key(root)
    token = file_index._request_pending_drop(root)

    real_lock_path = file_index._build_lock_path
    first_failed = threading.Event()
    resume_second = threading.Event()
    calls = {"n": 0}

    def flaky_lock_path(candidate_root):
        calls["n"] += 1
        if calls["n"] == 1:
            first_failed.set()
            raise OSError("forced build-lock open fault")
        assert resume_second.wait(3.0)
        return real_lock_path(candidate_root)

    monkeypatch.setattr(file_index, "_build_lock_path", flaky_lock_path)
    file_index._schedule_pending_drop_retry(root)
    assert first_failed.wait(3.0)
    with file_index._REGISTRY_LOCK:
        assert file_index._PENDING_DROPS.get(key) == token, "an open/flock fault dropped the pending-drop token"
        retry = file_index._PENDING_DROP_RETRIES.get(key)
    assert retry is not None, "an open/flock fault orphaned the drop with no retry owner"
    assert database.exists()

    resume_second.set()
    assert retry.completion.wait(5.0), "the re-armed deferred-drop retry never settled after the fault cleared"
    try:
        assert not database.exists(), "the rescheduled retry never completed the deferred drop"
        with file_index._REGISTRY_LOCK:
            assert key not in file_index._PENDING_DROPS
            assert key not in file_index._PENDING_DROP_RETRIES
    finally:
        _reset_registry()


def test_divergent_manifest_stamp_is_rejected_by_readers_agreeing_with_sqlite_search(tmp_path, monkeypatch):
    # P1-3 residual: marker T, the derived manifest stamped T (accepted by the marker), but the
    # AUTHORITATIVE sqlite store stamped a DIVERGENT identity S. Every manifest-accepting reader (status
    # metadata, root discovery) must reconcile against sqlite and REJECT -- agreeing with the sqlite search
    # reader, which rejects S. The manifest must never advertise a live snapshot the search reader rejects.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    (root / "kept.txt").write_text("kept", encoding="utf-8")
    file_index.build_now(root, set())
    file_index.clear_memory_indexes()

    # Marker T.
    identity_t = file_index._write_tombstone(root)
    # Commit a DIVERGENT identity S into the authoritative sqlite store, then stamp the derived manifest to
    # the CURRENT marker T (the divergence: the cache says superseded, the committed store does not).
    identity_s = f"{identity_t}-divergent"
    assert file_index._stamp_snapshot_tombstone_identity(root, identity_s) is True
    file_index._reconcile_manifest_tombstone_identity(root, identity_t)

    # Confirm the divergence is real on disk: manifest carries T, authoritative sqlite carries S.
    manifest = file_index._load_disk_metadata(root, set())
    assert file_index._metadata_tombstone_identity(manifest) == identity_t
    store = file_index._authoritative_store_metadata(root)
    assert file_index._metadata_tombstone_identity(store) == identity_s

    try:
        # The sqlite search reader REJECTS the store (its stamp S != marker T).
        assert file_index._read_sqlite_index(root, set()) is None
        # Every manifest-first reader must AGREE with sqlite and reject the divergent manifest.
        assert file_index._authoritative_snapshot_is_tombstoned(root, manifest) is True
        assert file_index._disk_snapshot_metadata(root, set()) is None, (
            "status metadata accepted a manifest whose identity the authoritative sqlite rejects"
        )
        assert root.resolve() not in set(file_index._iter_candidate_index_roots()), (
            "root discovery accepted a manifest the sqlite search reader rejects (divergent stamp)"
        )
    finally:
        _reset_registry()


# --------------------------------------------------------------------------------------------------
# codex-root round-4 RESIDUAL findings: the serve was not atomic with its verdict, and the retry chain
# gave up after a bounded attempt count. Each is RED on the round-3 tree.
# --------------------------------------------------------------------------------------------------


def test_serve_does_not_return_rows_republished_between_eviction_check_and_read(tmp_path, monkeypatch):
    # P0-1 residual: `_servable_snapshot` used to apply the tombstone verdict/eviction and then read the
    # rows in SEPARATE `ri.lock` acquisitions. A stale build republishing its OLD identity in that gap
    # (ready=True + rows + stale published identity) got served. The serve is now ONE atomic lock hold, so
    # a republish landing between the eviction check and the read can never make deleted rows servable.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "stale.txt").write_text("stale", encoding="utf-8")
    built = file_index.build_now(root, set())
    assert built.ready
    stale_rows = list(built.entries)
    old_identity = built.published_tombstone_identity  # "" (pre-unindex frozen identity)

    # A newer unindex: the ready owner's frozen identity no longer matches the marker.
    file_index._write_tombstone(root)
    assert file_index._root_index_is_tombstoned(built)

    def republish_old_identity():
        # Exactly what a stale build paused before publication would do: go ready again with the OLD identity.
        with built.lock:
            built.entries = stale_rows
            built.entry_by_path = {row[0]: row for row in stale_rows}
            built.ready = True
            built.built_at = 1.0
            built.published_tombstone_identity = old_identity

    real_evict = file_index._evict_tombstoned_root_index

    def evict_then_stale_republish(ri):
        result = real_evict(ri)
        if ri is built:
            # Inject a stale republish AFTER the eviction check and BEFORE the (old) separate row read.
            republish_old_identity()
        return result

    monkeypatch.setattr(file_index, "_evict_tombstoned_root_index", evict_then_stale_republish)

    def _match(path_str, name, rel):
        return {"name": name, "path": path_str, "relative_path": rel, "_sort_key": (name,)}

    try:
        served, _truncated = file_index.search_index(built, _match, 20)
        assert served == [], "serve returned rows a stale build republished between the eviction check and the read"
        # A concurrent republish can never make the rows servable either: the atomic serve re-evicts by
        # the same identity verdict on every read.
        republish_old_identity()
        served_again, _truncated2 = file_index.search_index(built, _match, 20)
        assert served_again == [], "the atomic serve served rows a stale republish had injected"
    finally:
        _reset_registry()


def test_deferred_drop_retry_never_gives_up_and_completes_after_many_failures(tmp_path, monkeypatch):
    # P0-2 residual: the retry chain used to STOP after a bounded attempt count (8), leaving the token in
    # place with no live owner -- so if the fault cleared afterward the store lingered forever. The chain
    # must retry UNBOUNDEDLY (capped backoff) so the delete always completes once the fault clears. Here the
    # unlink fails MORE than the retired 8-attempt cap, then succeeds.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "_PENDING_DROP_RETRY_BACKOFF_BASE_SECONDS", 0.005)
    monkeypatch.setattr(file_index, "_PENDING_DROP_RETRY_BACKOFF_CAP_SECONDS", 0.02)
    root = tmp_path / "root"
    root.mkdir()
    (root / "indexed.txt").write_text("indexed", encoding="utf-8")
    built = file_index.build_now(root, set())
    assert built.ready
    file_index.clear_memory_indexes()
    database = file_index._index_disk_path(root)
    assert database.exists()

    key = file_index._canonical_root_key(root)
    token = file_index._request_pending_drop(root)

    real_drop = file_index._drop_persisted_index
    fail_until = 12  # strictly more than the retired _PENDING_DROP_MAX_RETRY_ATTEMPTS = 8 cap
    calls = {"n": 0}

    def flaky_drop(candidate_root):
        calls["n"] += 1
        if calls["n"] <= fail_until:
            return False  # a persistent transient unlink failure; files remain on disk
        return real_drop(candidate_root)

    monkeypatch.setattr(file_index, "_drop_persisted_index", flaky_drop)
    file_index._schedule_pending_drop_retry(root)
    with file_index._REGISTRY_LOCK:
        retry = file_index._PENDING_DROP_RETRIES.get(key)
    assert retry is not None
    # The token survives every failure (never removed before a confirmed unlink).
    assert file_index._PENDING_DROPS.get(key) == token
    assert retry.completion.wait(10.0), "the unbounded retry never completed after the fault cleared"
    try:
        assert calls["n"] > fail_until, "the retry chain gave up before the fault cleared"
        for path in [
            *file_index._sqlite_paths(root),
            file_index._index_manifest_path(root),
            file_index._producer_heartbeat_path(root),
        ]:
            assert not path.exists(), f"the completed retry left {path.name} behind"
        with file_index._REGISTRY_LOCK:
            assert key not in file_index._PENDING_DROPS
            assert key not in file_index._PENDING_DROP_RETRIES
    finally:
        _reset_registry()


# --------------------------------------------------------------------------
# DOIT.p0.search-interactivity step 3 — fenced committed-delta reads
# (search_disk_index_delta). The tombstone/generation fence must apply to a
# delta read exactly as it does to a snapshot read: no superseded generation,
# no tombstoned store, no cross-policy cursor, no retention gap.
# --------------------------------------------------------------------------


def _delta_match_all(path, name, relative_path):
    return {"path": path, "name": name, "relative_path": relative_path}


def test_delta_read_returns_committed_changes_after_the_cursor(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "a" / "buried.txt").write_text("x", encoding="utf-8")
    try:
        build = bfs_index.ProgressiveBuild(root, set(), generation=1)
        with build:
            build.enqueue_startup()
            build.step()  # publish root -> journals top.txt
            cursor0 = file_index.current_delta_cursor(root, set(), "")
            assert cursor0 is not None
            build.step()  # publish a/ -> journals a/buried.txt
        result = file_index.search_disk_index_delta(root, set(), "", _delta_match_all, cursor0)
        assert isinstance(result, file_index.DeltaResult)
        changed = {change["path"] for change in result.changes}
        assert str(root / "a" / "buried.txt") in changed
        assert str(root / "top.txt") not in changed  # committed BEFORE the cursor
        # Reading again from the returned cursor is caught up: no changes, more=False.
        follow = file_index.search_disk_index_delta(root, set(), "", _delta_match_all, result.cursor)
        assert isinstance(follow, file_index.DeltaResult)
        assert follow.changes == [] and follow.more is False
    finally:
        _reset_registry()


def test_delta_read_rebases_on_generation_supersession(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    (root / "top.txt").write_text("x", encoding="utf-8")
    try:
        bfs_index.build_root_progressively(root, set(), generation=1)
        cursor = file_index.current_delta_cursor(root, set(), "")
        assert cursor is not None
        # A newer generation republishes the store; a cursor bound to gen 1 must never mix rows.
        bfs_index.build_root_progressively(root, set(), generation=2)
        result = file_index.search_disk_index_delta(root, set(), "", _delta_match_all, cursor)
        assert isinstance(result, file_index.DeltaRebaseRequired)
        assert result.reason == "generation_superseded"
    finally:
        _reset_registry()


def test_delta_read_rebases_when_a_tombstone_lands_after_the_cursor(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    (root / "top.txt").write_text("x", encoding="utf-8")
    try:
        bfs_index.build_root_progressively(root, set(), generation=1)
        cursor = file_index.current_delta_cursor(root, set(), "")
        assert cursor is not None
        # An explicit unindex after the cursor: the delta reader fails closed like every other read surface.
        file_index._tombstone_path(root).write_text(f"tomb-late\n{time.time() + 100.0}\n", encoding="utf-8")
        result = file_index.search_disk_index_delta(root, set(), "", _delta_match_all, cursor)
        assert isinstance(result, file_index.DeltaRebaseRequired)
        assert result.reason == "no_snapshot"
    finally:
        _reset_registry()


def test_delta_read_rejects_a_cross_policy_cursor(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    root.mkdir()
    (root / "top.txt").write_text("x", encoding="utf-8")
    try:
        bfs_index.build_root_progressively(root, set(), generation=1)
        cursor = file_index.current_delta_cursor(root, set(), "")
        assert cursor is not None
        # A cursor pinned to policy "" can never be applied under a different policy signature.
        result = file_index.search_disk_index_delta(root, {"node_modules"}, "other-policy", _delta_match_all, cursor)
        assert isinstance(result, file_index.DeltaRebaseRequired)
        assert result.reason == "cross_policy"
    finally:
        _reset_registry()


def test_delta_read_rebases_on_a_retention_gap(tmp_path, monkeypatch):
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    # Force the journal to retain a single revision so an early cursor's next revision is pruned.
    monkeypatch.setattr(file_index, "JOURNAL_RETENTION_REVISIONS", 1)
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "a" / "one.txt").write_text("x", encoding="utf-8")
    (root / "b" / "two.txt").write_text("x", encoding="utf-8")
    try:
        build = bfs_index.ProgressiveBuild(root, set(), generation=1)
        with build:
            build.enqueue_startup()
            build.step()  # publish root -> rev 1 (top.txt); cursor taken here
            cursor0 = file_index.current_delta_cursor(root, set(), "")
            assert cursor0 is not None
            build.step()  # publish one child -> rev 2, prunes rev <= 1
            build.step()  # publish the other child -> rev 3, prunes rev <= 2
        result = file_index.search_disk_index_delta(root, set(), "", _delta_match_all, cursor0)
        assert isinstance(result, file_index.DeltaRebaseRequired)
        assert result.reason == "retention_gap"
    finally:
        _reset_registry()


def test_delta_read_paginates_a_slow_client_within_the_bounded_page(tmp_path, monkeypatch):
    # DOIT step 9 bounds — slow-client PAGINATION: a client draining a large committed backlog
    # gets at most match_limit matches per response with more=True, and the returned cursor
    # advances so the next bounded request continues without re-scanning or dropping a change.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    # The shipped per-response bounds ARE the DOIT's: scan <=5,000 changes, return <=500 matches.
    assert file_index.JOURNAL_SCAN_LIMIT == 5_000
    assert file_index.JOURNAL_MATCH_LIMIT == 500
    root = tmp_path / "root"
    child = root / "many"
    child.mkdir(parents=True)
    total = 7
    for i in range(total):
        (child / f"m{i}.txt").write_text("x", encoding="utf-8")
    try:
        build = bfs_index.ProgressiveBuild(root, set(), generation=1)
        with build:
            build.enqueue_startup()
            build.step()  # publish root -> no matching files at the top level
            cursor0 = file_index.current_delta_cursor(root, set(), "")
            assert cursor0 is not None
            build.step()  # publish many/ -> journals all `total` matching files
        # A slow client can take only 3 matches per response; drain the backlog page by page.
        seen = []
        cursor = cursor0
        pages = 0
        while True:
            result = file_index.search_disk_index_delta(
                root, set(), "", _delta_match_all, cursor, match_limit=3
            )
            assert isinstance(result, file_index.DeltaResult)
            assert len(result.changes) <= 3  # never exceeds the per-response match bound
            seen.extend(change["path"] for change in result.changes)
            cursor = result.cursor
            pages += 1
            if not result.more:
                break
            assert pages < 20  # the loop terminates under the bound
        # Every committed match arrived exactly once, across bounded pages.
        assert len(seen) == total
        assert len(set(seen)) == total
        assert pages >= 3  # 7 matches / 3 per page => at least 3 bounded responses
    finally:
        _reset_registry()


def test_delta_read_scan_cap_bounds_a_sparse_backlog(tmp_path, monkeypatch):
    # DOIT step 9 bounds — the scan cap: even when few (here zero) rows match the query, a single
    # response scans at most scan_limit journal changes and reports more=True, and its cursor
    # advances past the scanned rows so the next request does not re-scan them.
    _reset_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    root = tmp_path / "root"
    child = root / "many"
    child.mkdir(parents=True)
    for i in range(7):
        (child / f"f{i}.log").write_text("x", encoding="utf-8")

    def match_none(path, name, relative_path):
        return None  # nothing in this query matches the committed changes

    try:
        build = bfs_index.ProgressiveBuild(root, set(), generation=1)
        with build:
            build.enqueue_startup()
            build.step()
            cursor0 = file_index.current_delta_cursor(root, set(), "")
            assert cursor0 is not None
            build.step()  # journals 7 committed changes, none matching this query
        result = file_index.search_disk_index_delta(
            root, set(), "", match_none, cursor0, scan_limit=3
        )
        assert isinstance(result, file_index.DeltaResult)
        assert result.changes == []  # nothing matched
        assert result.more is True  # but the scan cap stopped us mid-backlog
        # The cursor advanced past the scanned rows: the next bounded request keeps draining.
        follow = file_index.search_disk_index_delta(
            root, set(), "", match_none, result.cursor, scan_limit=3
        )
        assert isinstance(follow, file_index.DeltaResult)
        assert follow.cursor != result.cursor  # forward progress, not a re-scan of the same rows
        assert follow.more is True  # 7 changes at 3 scanned per response is still not drained
    finally:
        _reset_registry()
