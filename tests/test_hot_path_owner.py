"""Item 6 (DOIT.fs-interactivity): the one hot-path index owner.

These prove that concrete change evidence (watchd, YOLOmux write/delete/mkdir/rename, uploads, editor
saves) and visibility evidence (Finder/Differ) route through ONE owner, are coalesced by indexed
root, refresh in seconds instead of at the 1800s safety TTL, decay their heat after inactivity, and
cannot starve breadth/safety reconciliation of a forever-hot root.
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from yolomux_lib import file_index
from yolomux_lib import filesystem
from yolomux_lib.filesystem import SEARCH_SKIP_DIRS
from yolomux_lib.filesystem import search as fs_search
from yolomux_lib.search import bfs_index


def _clear_registry():
    # Route through the ONE lifecycle owner so a lingering BFS worker is signalled to stop and
    # joined before its root fd is closed (codex item-10 fix), not just dropped from the map.
    file_index.clear_memory_indexes()


def _reset_lifecycle_registry():
    """Hard reset of the three lifecycle owners (registry, retiring set, pending drops) for the
    deterministic round-3 lifecycle regressions, which install and inspect these directly."""
    with file_index._REGISTRY_LOCK:
        indexes = list(file_index._REGISTRY.values()) + list(file_index._RETIRING.values())
        file_index._REGISTRY.clear()
        file_index._RETIRING.clear()
        file_index._PENDING_DROPS.clear()
    for index in indexes:
        index.stop_event.set()
        index.close_root_fd()


def _install_root_fd(index, root):
    descriptor = os.open(root, os.O_RDONLY)
    try:
        index.replace_root_fd(descriptor)
    finally:
        os.close(descriptor)


def _record_start_builds(monkeypatch):
    """Replace `_start_build` with a recorder that captures `build_reason` and never spawns a thread.

    It deliberately does NOT set `ri.building`, so successive `schedule_refreshes` calls each reach a
    decision, letting a test observe the exact build_reason sequence deterministically.
    """
    reasons: list[str] = []

    def record(ri, skip_dirs, exclude_path=None, exclude_signature="", operation="", build_reason=""):
        reasons.append(build_reason)
        # `_start_build` now reports whether a worker was installed; a recorder that stands in for a
        # successful start returns True so `schedule_refreshes` counts it.
        return True

    monkeypatch.setattr(file_index, "_start_build", record)
    return reasons


# -- heat ------------------------------------------------------------------


def test_change_evidence_heats_the_root(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS)
    assert index.hot_score == 0.0

    file_index.mark_paths_dirty([root / "a.txt"])

    assert index.hot_score > 0.0
    assert index.last_hot_at > 0.0
    assert index.dirty_paths == {(root / "a.txt").resolve()}
    _clear_registry()


def test_heat_decays_to_cold_after_inactivity(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS)
    _record_start_builds(monkeypatch)
    index.hot_score = 7.0
    index.consecutive_hot_repairs = 4
    index.last_hot_at = 1000.0
    index.last_full_build_at = 1000.0
    index.built_at = 1000.0
    index.dirty_paths = set()

    # Still within the inactivity window: heat is retained.
    file_index.schedule_refreshes(now=1000.0 + file_index.HOT_INACTIVITY_SECONDS - 1)
    assert index.hot_score == 7.0
    assert index.consecutive_hot_repairs == 4

    # Past the inactivity window with no new evidence: cold again.
    file_index.schedule_refreshes(now=1000.0 + file_index.HOT_INACTIVITY_SECONDS + 1)
    assert index.hot_score == 0.0
    assert index.consecutive_hot_repairs == 0
    _clear_registry()


# -- coalescing / seconds freshness ---------------------------------------


def test_a_burst_of_events_for_one_root_coalesces_to_one_bounded_repair(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    sub = root / "pkg"
    sub.mkdir(parents=True)
    (sub / "a.txt").write_text("a", encoding="utf-8")
    (root / "top.txt").write_text("t", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS)
    reasons = _record_start_builds(monkeypatch)

    # A burst of 20 change events for the same subtree arriving while a build is already in flight:
    # they accumulate as dirty, but must not each start a crawl.
    index.building = True
    for i in range(20):
        changed = sub / f"f{i}.txt"
        changed.write_text("x", encoding="utf-8")
        file_index.mark_paths_dirty([changed])
    assert file_index.schedule_refreshes() == 0
    assert reasons == []

    # When the build clears, the whole burst is repaired by exactly ONE incremental repair.
    index.building = False
    assert file_index.schedule_refreshes() == 1
    assert reasons == [""]
    _clear_registry()


def test_change_refreshes_in_seconds_not_at_the_safety_ttl(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS)
    reasons = _record_start_builds(monkeypatch)
    index.refresh_seconds = 1800.0
    index.last_full_build_at = 1000.0
    index.built_at = 1000.0

    # Five seconds after a full build, with no change evidence, nothing runs (far from the 1800s TTL).
    assert file_index.schedule_refreshes(now=1005.0) == 0
    assert reasons == []

    # A concrete change starts an incremental repair one second later -- seconds, not 1800s.
    changed = root / "new.txt"
    changed.write_text("n", encoding="utf-8")
    file_index.mark_paths_dirty([changed])
    assert file_index.schedule_refreshes(now=1006.0) == 1
    assert reasons == [""]
    _clear_registry()


def test_deletion_is_repaired_by_the_incremental_hot_path(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep.txt").write_text("k", encoding="utf-8")
    doomed = root / "doomed.txt"
    doomed.write_text("d", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS)
    assert {entry[1] for entry in index.entries} == {"keep.txt", "doomed.txt"}
    full_builds_before = index.full_build_count

    doomed.unlink()
    file_index.mark_paths_dirty([doomed])
    file_index._run_build(index, SEARCH_SKIP_DIRS)

    assert {entry[1] for entry in index.entries} == {"keep.txt"}
    assert index.incremental_build_count == 1
    # A deletion is repaired by the bounded incremental path, never a full recrawl of the root.
    assert index.full_build_count == full_builds_before
    _clear_registry()


# -- starvation bound ------------------------------------------------------


def test_a_forever_hot_root_still_yields_to_a_breadth_safety_refresh(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS)
    reasons = _record_start_builds(monkeypatch)
    index.last_full_build_at = 1000.0
    index.built_at = 1000.0
    hot = (root / "a.txt").resolve()

    bound = file_index.HOT_REPAIR_STARVATION_BOUND
    for _ in range(bound + 1):
        index.dirty_paths = {hot}          # perpetually hot
        index.last_hot_at = 1000.0         # recent, so heat never decays
        file_index.schedule_refreshes(now=1000.0)

    # The first `bound` ticks are incremental hot repairs; the next yields ONE lowest-priority
    # full-safety-refresh (breadth reconciliation) so deeper/missed coverage cannot be starved.
    assert reasons[:bound] == [""] * bound
    assert reasons[bound] == file_index.SAFETY_REFRESH_REASON
    # The yield supersedes the pending dirty subtrees (the full re-list covers them).
    assert index.dirty_paths == set()
    assert index.consecutive_hot_repairs == 0
    _clear_registry()


# -- producer wiring (negative-search in test form) ------------------------


def test_every_mutation_producer_routes_through_the_one_reindex_owner(tmp_path, monkeypatch):
    calls: list[tuple[str, tuple[str, ...]]] = []

    def record(raw_paths, reason="filesystem-change"):
        calls.append((reason, tuple(str(path) for path in raw_paths)))
        return []

    monkeypatch.setattr(filesystem.search, "reindex_roots_for_paths", record)
    root = tmp_path / "root"
    root.mkdir()
    target = root / "f.txt"

    filesystem.write_file(str(target), "hi")            # editor save / write funnel
    filesystem.create_directory(str(root / "d"))        # mkdir
    filesystem.rename_path(str(target), "g.txt")        # rename
    filesystem.delete_path(str(root / "g.txt"))         # delete

    reasons = {reason for reason, _paths in calls}
    assert {"fs-write", "fs-mkdir", "fs-rename", "fs-delete"} <= reasons


def test_finder_listing_routes_through_the_one_visibility_owner(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(filesystem.search, "promote_visible_path", lambda raw: seen.append(str(raw)) or [])
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")

    filesystem.list_directory(str(root))

    assert str(root) in seen


def test_visible_path_promotes_the_indexed_ancestor_frontier(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.RootIndex(root)
    _install_root_fd(index, root)
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root.resolve())] = index
    promoted: list[tuple[str, str]] = []
    monkeypatch.setattr(file_index, "request_user_visible_promotion", lambda r, d="": promoted.append((r, d)) or True)

    dispatched = filesystem.search.promote_visible_path(str(sub))

    resolved_root = str(root.resolve())
    assert resolved_root in dispatched
    assert any(entry[0] == resolved_root for entry in promoted)
    _clear_registry()


def test_change_promotes_a_pending_frontier_to_hot_change_priority(tmp_path, monkeypatch):
    _clear_registry()
    root = (tmp_path / "root").resolve()
    root.mkdir()
    changed = root / "f.txt"
    changed.write_text("f", encoding="utf-8")
    index = file_index.RootIndex(root)
    _install_root_fd(index, root)
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = index
    promotions: list[tuple[str, int, str]] = []
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "schedule_refreshes", lambda: 0)
    monkeypatch.setattr(file_index, "promote_frontier", lambda root, to_priority=0, to_reason="": promotions.append((str(root), to_priority, to_reason)) or 0)
    monkeypatch.setattr(filesystem.search, "_ensure_search_index", lambda _root, operation="": (index, {}))
    try:
        assert filesystem.search.reindex_roots_for_paths([str(changed)], reason="fs-write") == [str(root)]
    finally:
        _clear_registry()

    assert any(
        entry[1] == file_index.HOT_CHANGE_PRIORITY and entry[2] == file_index.HOT_CHANGE_REASON
        for entry in promotions
    )


# -- empty-config guard ----------------------------------------------------


def test_empty_config_reindex_short_circuits_before_any_work(tmp_path, monkeypatch):
    """Item 6 guard: with no active or persisted index root, a change-evidence batch (watchd,
    write/delete/mkdir/rename, upload) does ZERO per-path `safe_parent` normalization, dirty-mark,
    promotion, scheduling, or indexd RPC. On the empty-config server every watchd revision otherwise
    resolved each changed path only to drop it at `mark_paths_dirty`; the guard short-circuits first."""
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "empty-idx")  # no registry, no manifests
    assert file_index.any_index_roots_exist() is False

    def _forbidden(name):
        def _boom(*_a, **_k):
            raise AssertionError(f"{name} must not run with no configured index roots")

        return _boom

    monkeypatch.setattr(fs_search.paths, "safe_parent", _forbidden("safe_parent"))
    monkeypatch.setattr(file_index, "mark_paths_dirty", _forbidden("mark_paths_dirty"))
    monkeypatch.setattr(file_index, "promote_frontier", _forbidden("promote_frontier"))
    monkeypatch.setattr(file_index, "schedule_refreshes", _forbidden("schedule_refreshes"))
    monkeypatch.setattr(file_index, "request_background_owner_refresh", _forbidden("request_background_owner_refresh"))

    result = filesystem.reindex_roots_for_paths(
        [str(tmp_path / "root" / "a.txt"), str(tmp_path / "root" / "b.txt")], reason="watchd"
    )

    assert result == []
    _clear_registry()


def test_any_index_roots_exist_shares_manifest_validation_with_indexed_ancestor_roots(tmp_path, monkeypatch):
    """Guard finding #1 (codex audit): the boolean must NOT count a manifest the ancestry resolver
    would reject. A corrupt, non-dict, or relative-root manifest yields nothing from EITHER; a valid
    one yields a root from both. The two can never disagree about existence."""
    _clear_registry()
    idx = tmp_path / "idx"
    idx.mkdir()
    monkeypatch.setattr(file_index, "INDEX_DIR", idx)
    somewhere = tmp_path / "root" / "deep" / "file.txt"

    # No manifests at all -> both agree there is nothing.
    assert file_index.any_index_roots_exist() is False
    assert file_index.indexed_ancestor_roots(somewhere) == []

    # Corrupt JSON, non-dict, and relative-root manifests must ALL be ignored by both.
    (idx / "a.manifest.json").write_text("{not json", encoding="utf-8")
    (idx / "b.manifest.json").write_text("[]", encoding="utf-8")
    (idx / "c.manifest.json").write_text('{"root": "relative/path"}', encoding="utf-8")
    assert file_index.any_index_roots_exist() is False
    assert file_index.indexed_ancestor_roots(somewhere) == []

    # A valid manifest whose root is an ancestor -> both see it.
    root = tmp_path / "root"
    root.mkdir(parents=True)
    identity = file_index.root_identity(root.stat())
    (idx / "d.manifest.json").write_text(
        json.dumps({"root": str(root.resolve()), file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: identity}),
        encoding="utf-8",
    )
    assert file_index.any_index_roots_exist() is True
    assert root.resolve() in file_index.indexed_ancestor_roots(somewhere)
    _clear_registry()


def test_all_ignored_batch_with_configured_root_does_zero_work(tmp_path, monkeypatch):
    """Item 6 finding #2 (codex audit): with a configured root PRESENT, a batch whose every changed
    path is under an excluded dir (.git) does ZERO safe_parent / dirty-mark / promotion / scheduling /
    indexd RPC -- the shared-policy prefilter drops them before safe_parent, not after."""
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep.txt").write_text("k", encoding="utf-8")
    file_index.build_now(root, SEARCH_SKIP_DIRS)  # registers the root -> candidate roots non-empty
    assert file_index.any_index_roots_exist() is True

    def _forbidden(name):
        def _boom(*_a, **_k):
            raise AssertionError(f"{name} must not run for an all-ignored batch")

        return _boom

    monkeypatch.setattr(fs_search.paths, "safe_parent", _forbidden("safe_parent"))
    monkeypatch.setattr(file_index, "mark_paths_dirty", _forbidden("mark_paths_dirty"))
    monkeypatch.setattr(file_index, "promote_frontier", _forbidden("promote_frontier"))
    monkeypatch.setattr(file_index, "schedule_refreshes", _forbidden("schedule_refreshes"))
    monkeypatch.setattr(file_index, "request_background_owner_refresh", _forbidden("request_background_owner_refresh"))

    result = filesystem.reindex_roots_for_paths(
        [str(root / ".git" / "index"), str(root / ".git" / "refs" / "heads" / "main")], reason="watchd"
    )

    assert result == []
    _clear_registry()


# -- worker lifecycle (codex item-10 blocker) ------------------------------


def test_clear_memory_indexes_stops_the_bfs_worker_before_closing_its_root_fd(tmp_path):
    """Codex item-10 lifecycle blocker: `clear_memory_indexes` (called by `demote_background_owner`)
    must SIGNAL the BFS worker to stop and JOIN it before closing the root fd. A worker left running
    against a closed fd raised sqlite 'unable to open database file'. Fails against the pre-fix owner
    (stop never signalled, fd closed under the live worker)."""
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    ri = file_index.RootIndex(root)
    _install_root_fd(ri, root)
    used_after_close: list[str] = []
    started = threading.Event()

    def _worker():
        started.set()
        for _ in range(4000):  # ~20s safety bound so a regression can never hang the runner
            if ri.stop_event.is_set():
                return
            fd = ri.root_fd
            if fd is None:
                used_after_close.append("fd_none")
                return
            try:
                os.fstat(fd)
            except OSError:
                used_after_close.append("fd_closed")
                return
            time.sleep(0.005)

    thread = threading.Thread(target=_worker, name="bfs-worker-lifecycle-test")
    ri.thread = thread
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = ri
    thread.start()
    try:
        assert started.wait(2.0)
        assert thread.is_alive()

        file_index.clear_memory_indexes()

        assert ri.stop_event.is_set()  # stop was signalled
        assert not thread.is_alive()  # worker was joined, not left alive against closed ownership
        assert used_after_close == []  # the fd was never closed while the worker still ran
        assert ri.root_fd is None  # fd closed only AFTER the worker stopped
        with file_index._REGISTRY_LOCK:
            assert not file_index._REGISTRY
    finally:
        ri.stop_event.set()
        thread.join(2.0)


# ==========================================================================
# BFS index lifecycle fix — items 2/3/4: retirement, completion events,
# deferred drop, and the demotion-vs-unindex distinction.
# ==========================================================================


def _install_worker(root, *, cooperative, release, register=True):
    """Wire a RootIndex with a real build worker that uses the finalizer protocol.

    A cooperative worker exits when its `stop_event` is set; a blocked worker ignores `stop_event` and
    exits only when the external `release` event is set. Either way the worker's `finally` calls the
    ONE finalizer (`_finalize_worker_exit`), and its completion event is created cleared BEFORE the
    thread becomes visible -- exactly `_start_build`'s contract."""
    ri = file_index.RootIndex(Path(root))
    _install_root_fd(ri, root)
    ri.completion = threading.Event()  # cleared: a worker is in flight
    started = threading.Event()

    def worker():
        try:
            started.set()
            if cooperative:
                ri.stop_event.wait(20.0)
            else:
                release.wait(20.0)
        finally:
            file_index._finalize_worker_exit(ri)

    thread = threading.Thread(target=worker, name=f"lifecycle-worker-{Path(root).name}", daemon=True)
    ri.thread = thread
    if register:
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY[str(root)] = ri
    thread.start()
    assert started.wait(2.0)
    return ri, thread


def test_retirement_signals_all_then_waits_one_deadline(tmp_path, monkeypatch):
    # Item 3: a batch retirement signals+fences ALL roots BEFORE waiting on any, then waits on
    # completion events against ONE absolute batch deadline -- never N joins with N timeouts. With one
    # cooperative and TWO blocked roots, the whole clear must finish within ~one deadline (not two),
    # the aggregate must name ONLY the blocked roots as late, and a released blocked worker must then
    # complete its deferred close.
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.4)
    _clear_registry()
    coop_root = tmp_path / "coop"
    coop_root.mkdir()
    blocked_a = tmp_path / "blocked_a"
    blocked_a.mkdir()
    blocked_b = tmp_path / "blocked_b"
    blocked_b.mkdir()
    release = threading.Event()
    coop_ri, coop_thread = _install_worker(coop_root, cooperative=True, release=release)
    a_ri, a_thread = _install_worker(blocked_a, cooperative=False, release=release)
    b_ri, b_thread = _install_worker(blocked_b, cooperative=False, release=release)

    start = time.monotonic()
    result = file_index.clear_memory_indexes()
    elapsed = time.monotonic() - start

    # All three stop events were raised before any wait (the cooperative one proves it: it stopped).
    assert coop_ri.stop_event.is_set()
    assert a_ri.stop_event.is_set() and b_ri.stop_event.is_set()
    # ONE shared deadline: two blocked roots do NOT cost two timeouts.
    assert elapsed < 0.7, f"batch retirement took {elapsed:.2f}s; expected ~one 0.4s deadline"
    # Aggregate outcome names only the blocked roots as late; the cooperative one completed.
    assert set(result.requested) == {coop_root, blocked_a, blocked_b}
    assert coop_root in result.completed
    assert set(result.late) == {blocked_a, blocked_b}
    # The cooperative worker's fd is closed; the blocked ones stay OBSERVABLE with their fd still open.
    assert coop_ri.root_fd is None
    assert a_ri.root_fd is not None and b_ri.root_fd is not None
    with file_index._REGISTRY_LOCK:
        assert id(a_ri) in file_index._RETIRING and id(b_ri) in file_index._RETIRING

    # Release the blocked workers: their own finally runs the deferred close and removes them.
    release.set()
    assert a_ri.completion.wait(3.0) and b_ri.completion.wait(3.0)
    a_thread.join(3.0)
    b_thread.join(3.0)
    assert a_ri.root_fd is None and b_ri.root_fd is None
    with file_index._REGISTRY_LOCK:
        assert id(a_ri) not in file_index._RETIRING and id(b_ri) not in file_index._RETIRING
    coop_thread.join(3.0)


def test_assigned_but_not_started_worker_retires_on_eventual_start(tmp_path, monkeypatch):
    # Item 2: an index whose thread was ASSIGNED but not yet started (the set-then-start race) reports
    # INCOMPLETE at retirement and keeps its fd + durable store valid, until the worker's eventual
    # start runs its `finally` -- which then closes/removes it exactly once.
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.3)
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    ri = file_index.RootIndex(root)
    _install_root_fd(ri, root)
    ri.completion = threading.Event()  # cleared, as _start_build does before the thread is visible
    release = threading.Event()

    def worker():
        try:
            release.wait(20.0)
        finally:
            file_index._finalize_worker_exit(ri)

    thread = threading.Thread(target=worker, name="assigned-not-started", daemon=True)
    ri.thread = thread  # assigned, but NOT started yet
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = ri

    result = file_index.clear_memory_indexes()
    # Retirement reports it incomplete; its fd is still open and it stays observable.
    assert root in result.late
    assert ri.root_fd is not None
    with file_index._REGISTRY_LOCK:
        assert id(ri) in file_index._RETIRING

    # The worker eventually starts and unwinds -> its finally is the sole owner that closes/removes it.
    release.set()
    thread.start()
    assert ri.completion.wait(3.0)
    thread.join(3.0)
    assert ri.root_fd is None
    with file_index._REGISTRY_LOCK:
        assert id(ri) not in file_index._RETIRING


def test_assigned_but_not_started_worker_retires_on_start_rollback(tmp_path):
    # Item 2: the OTHER assigned-but-not-started outcome -- Thread.start FAILS. No worker `finally`
    # will run, so the start rollback owns the deferred close (it calls the same finalizer).
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    ri = file_index.RootIndex(root)
    _install_root_fd(ri, root)
    ri.completion = threading.Event()
    thread = threading.Thread(target=lambda: None, name="never-started")
    ri.thread = thread
    file_index._signal_retirement(ri)  # retirement requested while assigned-but-not-started
    assert ri.root_fd is not None  # not closed yet
    with file_index._REGISTRY_LOCK:
        assert id(ri) in file_index._RETIRING

    # The start-rollback path (invoked when Thread.start raises) releases the assigned slot and then
    # finalizes exactly once -- exactly what `_start_build`'s rollback does.
    with ri.lock:
        if ri.thread is thread:
            ri.thread = None
            ri.building = False
    file_index._finalize_worker_exit(ri)
    assert ri.root_fd is None
    assert ri.completion.is_set()
    with file_index._REGISTRY_LOCK:
        assert id(ri) not in file_index._RETIRING
    # Idempotent: a second finalize (e.g. a late duplicate) is a no-op.
    file_index._finalize_worker_exit(ri)
    assert ri.root_fd is None


def test_current_worker_retirement_does_not_self_join(tmp_path, monkeypatch):
    # Item 3: retirement requested from INSIDE the worker must never self-join (that would deadlock).
    # The aggregate honestly reports the current worker as late/incomplete, and its own unwinding
    # `finally` closes/removes it.
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.3)
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    ri = file_index.RootIndex(root)
    _install_root_fd(ri, root)
    ri.completion = threading.Event()
    result_box = {}
    proceed = threading.Event()
    done = threading.Event()

    def worker():
        try:
            # Retire from within the worker itself.
            result_box["result"] = file_index.clear_memory_indexes()
            done.set()
            proceed.wait(20.0)  # stay alive until the test has checked the aggregate
        finally:
            file_index._finalize_worker_exit(ri)

    thread = threading.Thread(target=worker, name="self-retiring-worker", daemon=True)
    ri.thread = thread
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = ri
    thread.start()

    assert done.wait(3.0), "self-retirement deadlocked (a self-join)"
    result = result_box["result"]
    assert root in result.late  # the current worker is honestly reported incomplete
    assert ri.root_fd is not None  # not closed mid-run
    with file_index._REGISTRY_LOCK:
        assert id(ri) in file_index._RETIRING

    proceed.set()  # let the worker unwind; its finally finalizes
    assert ri.completion.wait(3.0)
    thread.join(3.0)
    assert ri.root_fd is None
    with file_index._REGISTRY_LOCK:
        assert id(ri) not in file_index._RETIRING


@pytest.mark.gate_serial
def test_churn_abandon_and_restart_leaves_no_deleted_fds_and_one_generation(tmp_path, monkeypatch):
    # Churn: start a worker, abandon it while blocked, start the next, repeat >3x, release in REVERSE
    # order. Every worker must eventually retire, none may be left observable, no descriptor may leak,
    # and a final build must leave exactly ONE current generation on ONE stable DB identity.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.3)
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")

    workers = []
    for _ in range(4):
        release = threading.Event()
        ri, thread = _install_worker(root, cooperative=False, release=release)
        workers.append((ri, thread, release))
        file_index.clear_memory_indexes()  # abandon the in-flight worker; it becomes a late retiree

    # Release in reverse order; every abandoned worker must finalize and drop out of the observable set.
    for ri, thread, release in reversed(workers):
        release.set()
        assert ri.completion.wait(3.0)
        thread.join(3.0)
        assert ri.root_fd is None
    with file_index._REGISTRY_LOCK:
        assert not file_index._RETIRING, "an abandoned worker was never finalized"

    # One current generation on one stable DB identity after the churn.
    built = file_index.build_now(root, set())
    assert built.ready is True
    db_path = file_index._index_disk_path(root)
    inode = db_path.stat().st_ino
    rebuilt = file_index.build_now(root, set())
    assert rebuilt.ready is True
    assert db_path.stat().st_ino == inode  # one stable durable identity
    with sqlite3.connect(db_path) as conn:
        active = conn.execute("SELECT value FROM metadata WHERE key='active_generation'").fetchone()[0]
        gens = [row[0] for row in conn.execute("SELECT DISTINCT generation FROM entries")]
    assert gens == [int(active)], "entries carry exactly one (the current) generation"

    fd_deleted = 0
    fd_dir = Path("/proc/self/fd")
    if fd_dir.exists():
        for entry in fd_dir.iterdir():
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            if ".sqlite3" in target and target.endswith("(deleted)"):
                fd_deleted += 1
    assert fd_deleted == 0, "churn left (deleted) sqlite descriptors"


def test_unindex_defers_durable_delete_until_worker_finalizes(tmp_path, monkeypatch):
    # Item 4: unindex requests the durable delete but does NOT unlink while a worker can still publish
    # or hold the store. The DB stays present until the worker finalizes; THEN DB/WAL/SHM/manifest are
    # deleted exactly once.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    # A durable index on disk.
    file_index.build_now(root, set())
    db_path = file_index._index_disk_path(root)
    manifest_path = file_index._index_manifest_path(root)
    assert db_path.exists() and manifest_path.exists()

    # A live blocked worker now owns the root, registered in place of the built index.
    release = threading.Event()
    ri, thread = _install_worker(root, cooperative=False, release=release)

    # Unindex with a short deadline: the worker is still blocked, so the delete is DEFERRED.
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.3)
    file_index.unindex(root)
    # The durable store is STILL present -- not unlinked under the live worker.
    assert db_path.exists(), "unindex deleted the store while a worker could still hold it"
    # P1: the pending drop is a ROOT-LEVEL fact keyed by the canonical path, not a per-worker flag.
    with file_index._REGISTRY_LOCK:
        assert ri.root_key in file_index._PENDING_DROPS
        assert id(ri) in file_index._RETIRING
    # The unindex tombstone is written synchronously regardless.
    assert file_index._tombstone_path(root).exists()

    # Release the worker: its finally performs the deferred delete exactly once.
    release.set()
    assert ri.completion.wait(3.0)
    thread.join(3.0)
    assert not db_path.exists()
    assert not manifest_path.exists()
    assert not Path(f"{db_path}-wal").exists() and not Path(f"{db_path}-shm").exists()
    assert ri.root_fd is None
    with file_index._REGISTRY_LOCK:
        assert id(ri) not in file_index._RETIRING


def test_demotion_preserves_durable_index(tmp_path, monkeypatch):
    # Item 4: a clear/demotion closes memory ownership but PRESERVES the durable files (unlike unindex,
    # it sets no deferred drop). The DB and manifest survive the worker's finalization.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.3)
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    file_index.build_now(root, set())
    db_path = file_index._index_disk_path(root)
    manifest_path = file_index._index_manifest_path(root)
    assert db_path.exists() and manifest_path.exists()

    release = threading.Event()
    ri, thread = _install_worker(root, cooperative=False, release=release)
    file_index.clear_memory_indexes()  # demotion, not unindex
    # P1: a demotion records NO root-level pending drop, so the durable store is preserved.
    with file_index._REGISTRY_LOCK:
        assert ri.root_key not in file_index._PENDING_DROPS
    release.set()
    assert ri.completion.wait(3.0)
    thread.join(3.0)
    # Demotion preserves the durable index.
    assert db_path.exists(), "demotion deleted a durable index it should preserve"
    assert manifest_path.exists()


def test_concurrent_ensure_and_retire_do_not_deadlock(tmp_path, monkeypatch):
    # Item 4 lock-order invariant: no path holds RootIndex.lock then acquires the registry lock while
    # another holds the registry then RootIndex.lock. Hammer ensure/retire concurrently on shared
    # roots; the whole thing must complete well within the deadline (a lock inversion would hang).
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _clear_registry()
    roots = []
    for i in range(3):
        r = tmp_path / f"root{i}"
        r.mkdir()
        (r / "f.txt").write_text("x", encoding="utf-8")
        roots.append(r)

    stop = threading.Event()
    errors = []

    def ensurer():
        try:
            while not stop.is_set():
                for r in roots:
                    file_index.ensure_index(r, set())
        except Exception as exc:  # a real defect, surfaced -- not swallowed
            errors.append(repr(exc))

    def retirer():
        try:
            while not stop.is_set():
                file_index.clear_memory_indexes()
        except Exception as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=ensurer, daemon=True) for _ in range(3)]
    threads += [threading.Thread(target=retirer, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(5.0)
    assert all(not t.is_alive() for t in threads), "ensure/retire deadlocked (lock-order inversion)"
    assert errors == [], f"concurrent ensure/retire raised: {errors[:3]}"
    _clear_registry()


# ==========================================================================
# Round-2 P0-3 — `retiring` is a TERMINAL state; P0-4 — ONE root-level pending
# drop owner; P1-5 — an immutable per-worker lease decides who closes the fd.
# ==========================================================================


def test_retiring_is_terminal_start_build_refuses_and_never_calls_the_runner(tmp_path, monkeypatch):
    # P0-3: a scheduler that captured a RootIndex before it was retired must NOT be able to revive it.
    # `_start_build` refuses on a retired object, on an object that lost registry ownership, and when
    # the background owner cannot build -- returning False and never spawning the runner, so a cleared
    # object can never run the crawl once and report itself ready.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    runner_calls: list = []
    # Patch the runner via monkeypatch so the module's real runner is RESTORED after the test.
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", lambda *_a, **_k: runner_calls.append(_k) or True)
    try:
        ri = file_index.RootIndex(root)
        _install_root_fd(ri, root)
        with file_index._REGISTRY_LOCK:
            file_index._REGISTRY[str(root)] = ri

        # Subcase 1: retirement is terminal.
        file_index._signal_retirement(ri)  # a concurrent clear/unindex retired it (terminal)
        assert file_index._start_build(ri, set()) is False
        assert ri.assignment is None and ri.thread is None  # no worker installed
        assert ri.ready is False
        assert runner_calls == []  # the runner was never invoked on a retired object

        # Subcase 2: an object that is no longer the registry owner for its key is refused.
        _clear_registry()  # finalize the retiree
        other = file_index.RootIndex(root)
        _install_root_fd(other, root)  # NOT registered as the owner for this key
        assert file_index._start_build(other, set()) is False
        assert runner_calls == []
        other.close_root_fd()

        # Subcase 3: a demoted background owner cannot build.
        fresh = file_index.ensure_index(root, set())  # registers a legitimate owner
        _await_no_build(fresh)
        with fresh.lock:
            fresh.building = False
            fresh.ready = False
        monkeypatch.setattr(file_index, "background_owner_can_build", lambda: False)
        assert file_index._start_build(fresh, set()) is False
    finally:
        _clear_registry()


def _await_no_build(ri, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with ri.lock:
            if not ri.building and ri.thread is None:
                return
        time.sleep(0.01)


def test_ensure_index_that_lost_registry_identity_does_not_reopen_fd_or_start(tmp_path, monkeypatch):
    # P0-3 second race: a clear/unindex that lands between `ensure_index`'s registry insertion and its
    # fd reopen must leave the object WITHOUT a reopened fd and WITHOUT a started build -- otherwise the
    # fd leaks on an object absent from both _REGISTRY and _RETIRING. Deterministically simulate the
    # concurrent clear by retiring the object at the exact identity re-check `ensure_index` performs.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    runner_calls: list = []
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", lambda *_a, **_k: runner_calls.append(_k) or True)
    real_owner_is = file_index._registry_owner_is
    tripped = {"done": False}

    def racing_owner_is(ri):
        # The first identity re-check for our root stands in for a concurrent clear landing right there:
        # de-register and retire the object, then report the (now genuine) identity loss.
        if not tripped["done"] and str(ri.root) == str(root):
            tripped["done"] = True
            with file_index._REGISTRY_LOCK:
                file_index._REGISTRY.pop(str(root), None)
            file_index._signal_retirement(ri)
        return real_owner_is(ri)

    monkeypatch.setattr(file_index, "_registry_owner_is", racing_owner_is)
    try:
        ri = file_index.ensure_index(root, set())
        assert ri.root_fd is None, "ensure_index reopened an fd on an object that lost registry identity"
        assert ri.assignment is None and ri.thread is None  # no build started
        assert runner_calls == []
        with file_index._REGISTRY_LOCK:
            assert id(ri) in file_index._RETIRING  # observable, not an orphan
        file_index._finalize_worker_exit(ri)  # release the simulated retiree
        with file_index._REGISTRY_LOCK:
            assert id(ri) not in file_index._RETIRING
    finally:
        _clear_registry()


def test_unindex_after_clear_keeps_db_until_the_late_retiree_releases(tmp_path, monkeypatch):
    # P0-4: after a clear moved a blocked worker into _RETIRING, `unindex` must NOT unlink the store --
    # a late retiree can still hold it. The root-level pending drop keeps the DB until that retiree's
    # finalizer, the last owner, executes exactly one drop.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.3)
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    file_index.build_now(root, set())
    db_path = file_index._index_disk_path(root)
    assert db_path.exists()

    release = threading.Event()
    ri, thread = _install_worker(root, cooperative=False, release=release)
    file_index.clear_memory_indexes()  # the blocked worker becomes a late retiree in _RETIRING
    with file_index._REGISTRY_LOCK:
        assert id(ri) in file_index._RETIRING

    file_index.unindex(root)  # no active registry owner, but the late retiree still holds the store
    assert db_path.exists(), "unindex unlinked the store underneath a late retiree"

    release.set()  # the last owner exits -> its finalizer executes the one pending drop
    assert ri.completion.wait(3.0)
    thread.join(3.0)
    assert not db_path.exists()
    assert not file_index._index_manifest_path(root).exists()
    with file_index._REGISTRY_LOCK:
        assert id(ri) not in file_index._RETIRING


def test_four_late_retirees_keep_the_db_until_the_last_exits(tmp_path, monkeypatch):
    # P0-4: FOUR late retirees for one root, released in REVERSE order. The store must survive every
    # sibling exit and be dropped exactly once, by the LAST finalizer -- never underneath a sibling.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.3)
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    file_index.build_now(root, set())
    db_path = file_index._index_disk_path(root)
    assert db_path.exists()

    workers = []
    for _ in range(4):
        release = threading.Event()
        ri, thread = _install_worker(root, cooperative=False, release=release)
        workers.append((ri, thread, release))
        file_index.clear_memory_indexes()  # each becomes a late retiree; the previous ones remain

    file_index.unindex(root)  # one root-level pending drop, four retiring owners still holding it
    assert db_path.exists()

    for i, (ri, thread, release) in enumerate(reversed(workers)):
        release.set()
        assert ri.completion.wait(3.0)
        thread.join(3.0)
        if i < len(workers) - 1:
            assert db_path.exists(), "the store was dropped while sibling retirees still held it"
    assert not db_path.exists(), "the last finalizer must drop the store"
    with file_index._REGISTRY_LOCK:
        assert not file_index._RETIRING


def test_a_later_rebuild_supersedes_a_pending_drop(tmp_path, monkeypatch):
    # P0-4: a later successful generation explicitly SUPERSEDES a pending drop, so a rebuild that lands
    # after an unindex request keeps its freshly published store instead of having a stale finalizer
    # unlink it.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.3)
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    file_index.build_now(root, set())
    db_path = file_index._index_disk_path(root)

    release = threading.Event()
    ri, thread = _install_worker(root, cooperative=False, release=release)
    file_index.unindex(root)  # requests a drop, deferred while the blocked worker holds the store
    assert db_path.exists()

    # A later successful rebuild publishes and supersedes the pending drop intent.
    file_index.build_now(root, set())
    assert db_path.exists()

    # The old blocked worker finally exits; its finalizer must NOT drop the superseded store.
    release.set()
    assert ri.completion.wait(3.0)
    thread.join(3.0)
    assert db_path.exists(), "a stale finalizer dropped a store a later generation had republished"


def test_a_superseded_worker_does_not_close_the_successor_fd(tmp_path):
    # P1-5: identity is the IMMUTABLE lease, not the mutable fields. A worker whose lease was already
    # replaced by a successor must only set its OWN frozen completion -- never close the successor's fd
    # or drop its store.
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    ri = file_index.RootIndex(root)
    ri.retiring = True  # the object is retiring, so the close path is reachable

    # The original worker A's lease.
    a_completion = threading.Event()
    lease_a = file_index._WorkerAssignment(generation=1, thread=threading.current_thread(), completion=a_completion)

    # A successor B took the live slot with its OWN fd and lease.
    b_completion = threading.Event()
    lease_b = file_index._WorkerAssignment(generation=2, thread=threading.current_thread(), completion=b_completion)
    successor_fd = os.open(str(root), os.O_RDONLY)
    with ri.lock:
        ri.assignment = lease_b
        ri.root_fd = successor_fd

    # A finalizes on its OLD lease: it is superseded, so it closes nothing.
    file_index._finalize_worker_exit(ri, lease_a)
    assert a_completion.is_set()  # A released anyone waiting on its own frozen event
    assert not b_completion.is_set()  # A never touched the successor's event
    assert ri.assignment is lease_b  # the successor's live slot is intact
    os.fstat(successor_fd)  # the successor's fd is STILL open (would raise if A had closed it)
    with ri.lock:
        assert ri.root_fd == successor_fd

    # Now B finalizes on the matching live lease: it owns the slot and closes its fd.
    file_index._finalize_worker_exit(ri, lease_b)
    assert b_completion.is_set()
    assert ri.root_fd is None and ri.assignment is None


# --- Round-3 BFS index lifecycle regressions (P0-1..P0-3, P0-5, P0-6) -------------------------------
# Copied from the deterministic audit scratch suite (codex-root). Each proved RED against the
# pre-round-3 source and GREEN after the six-fix lifecycle rework, using events/barriers/persisted
# frontier rather than timing.


def test_ensure_revalidates_ownership_atomically_when_installing_root_fd(tmp_path, monkeypatch):
    # P0-1: `ensure_index` must open a candidate fd OUTSIDE lifecycle locks and install it through ONE
    # owner that re-verifies registry ownership under `_REGISTRY_LOCK -> ri.lock`. A clear/finalize that
    # lands after the ownership gate must NOT leave an fd on an object absent from _REGISTRY and _RETIRING.
    _reset_lifecycle_registry()
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
        _reset_lifecycle_registry()


def test_final_ownership_failure_leaves_assignment_for_finalizer(tmp_path, monkeypatch):
    # P0-2: the final ownership re-check in `_start_build` must NOT pre-clear the slot. Handing the
    # still-installed assignment to the matching finalizer is what lets it close the retiring fd and
    # remove the _RETIRING row; pre-clearing `assignment` made the finalizer skip both.
    _reset_lifecycle_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_next_bfs_generation", lambda _root: 1)
    root = tmp_path / "root"
    root.mkdir()
    index = file_index.RootIndex(root)
    _install_root_fd(index, root)
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
        _reset_lifecycle_registry()


def test_failed_thread_start_after_retirement_finalizes_installed_assignment(tmp_path, monkeypatch):
    # P0-2: a Thread.start failure that lands after a retirement must finalize the STILL-installed
    # assignment through the matching finalizer -- closing the retiring fd and removing the _RETIRING row.
    _reset_lifecycle_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_next_bfs_generation", lambda _root: 1)
    monkeypatch.setattr(file_index, "notify_background_owner_done", lambda _payload: None)
    monkeypatch.setattr(file_index, "touch_producer_heartbeat", lambda *_args, **_kwargs: None)
    root = tmp_path / "root"
    root.mkdir()
    index = file_index.RootIndex(root)
    _install_root_fd(index, root)
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
        _reset_lifecycle_registry()


def test_retirement_registration_cannot_land_after_the_worker_finalized(tmp_path, monkeypatch):
    # P0-3: marking `retiring` and inserting _RETIRING is ONE step under `_REGISTRY_LOCK -> ri.lock`.
    # A worker that finalizes at the instant of registration must not be left DEAD in _RETIRING.
    _reset_lifecycle_registry()
    root = tmp_path / "root"
    root.mkdir()
    index = file_index.RootIndex(root)
    _install_root_fd(index, root)
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
    # P0-5: EVERY successful publication (here the BFS full runner, registered) must supersede a pending
    # drop through the one publication-completion owner -- not only the DFS path.
    _reset_lifecycle_registry()
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
    _install_root_fd(old, root)
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
        _reset_lifecycle_registry()


def test_publication_cannot_supersede_an_unindex_requested_after_build_started(tmp_path, monkeypatch):
    # P0-6: a publication may supersede ONLY the pending-drop token it captured when its build began.
    # An unindex requested AFTER the build started carries a different token it must not erase.
    _reset_lifecycle_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(file_index, "CLEAR_WORKER_JOIN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", None)
    root = tmp_path / "root"
    root.mkdir()
    (root / "first.txt").write_text("first", encoding="utf-8")
    index = file_index.RootIndex(root)
    _install_root_fd(index, root)
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY[str(root)] = index

    publication_ready = threading.Event()
    resume_publication = threading.Event()
    # No-clear model: publication no longer calls `_clear_tombstone` (it never clears the durable
    # marker). Repoint the pause to `_stamp_snapshot_tombstone_identity`, the observable step publication
    # now runs BEFORE `_supersede_pending_drop`, preserving the intent: an unindex requested while a
    # publication is paused mid-completion must not have its pending drop superseded by the older build.
    real_stamp = file_index._stamp_snapshot_tombstone_identity

    def pause_before_pending_drop_supersession(candidate_root, identity, *, expected_root_identity):
        real_stamp(candidate_root, identity, expected_root_identity=expected_root_identity)
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
        _reset_lifecycle_registry()
