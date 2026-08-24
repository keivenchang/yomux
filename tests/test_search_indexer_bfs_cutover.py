"""Slice B evidence for DOIT.fs-interactivity items 1, 3, 8.

- item 3: a configured-root FULL build is routed through the breadth-first, directory-at-a-time
  engine instead of the DFS `_walk_root_with_metrics`, publishes the v5 coverage manifest, and a
  follower disk read sees layer-1 rows; the retired DFS path survives only as the no-runner
  fallback and its off-list backstop still clears `building`.
- item 1: the `indexd` scheduler obligation is reported as measured scheduled work, not the
  hard-coded demand-only idle, and is released when no root is configured.
- item 8: `SearchIndexerClient.runtime_status()` projects per-root coverage and the health observer
  classifies an absent-but-scheduled indexer as `starting` with the scheduled reason, so the
  Daemons roster no longer says "Idle - Starts on demand" while a root is configured.
"""

import os
import shutil
import sqlite3
import threading
import time

import pytest

from yolomux_lib.backend_health.observer import observed_health
from yolomux_lib.filesystem import SEARCH_SKIP_DIRS
from yolomux_lib.filesystem import search as fs_search
from yolomux_lib.search import bfs_index
from yolomux_lib.search import file_index
from yolomux_lib.search import search_indexer


@pytest.fixture(autouse=True)
def _isolated_index_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    file_index.clear_memory_indexes()
    yield
    file_index.clear_memory_indexes()


def _client(tmp_path):
    return search_indexer.SearchIndexerClient(socket_path=tmp_path / "svc" / "indexer.sock")


def _disk_rels(root):
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        return sorted(row[0] for row in conn.execute("SELECT relative_path FROM entries"))


# --------------------------------------------------------------------------
# BLOCKER A - a completed clean generation purges rows it never revisited
# --------------------------------------------------------------------------


def test_completed_generation_purges_vanished_subtree(tmp_path):
    root = tmp_path / "root"
    (root / "gone").mkdir(parents=True)
    (root / "gone" / "old.txt").write_text("x", encoding="utf-8")
    (root / "stay.txt").write_text("x", encoding="utf-8")
    first = file_index.build_now(root, SEARCH_SKIP_DIRS)
    assert any(name == "old.txt" for _p, name, _r, _s, _m in first.entries)

    shutil.rmtree(root / "gone")
    second = file_index.build_now(root, SEARCH_SKIP_DIRS)
    names = {name for _p, name, _r, _s, _m in second.entries}
    assert "old.txt" not in names
    assert "stay.txt" in names
    assert "gone/old.txt" not in _disk_rels(root)


def test_completed_generation_purges_renamed_subtree(tmp_path):
    root = tmp_path / "root"
    (root / "before").mkdir(parents=True)
    (root / "before" / "f.txt").write_text("x", encoding="utf-8")
    file_index.build_now(root, SEARCH_SKIP_DIRS)
    assert "before/f.txt" in _disk_rels(root)

    (root / "before").rename(root / "after")
    file_index.build_now(root, SEARCH_SKIP_DIRS)
    rels = _disk_rels(root)
    assert "before/f.txt" not in rels
    assert "after/f.txt" in rels


def test_truncated_generation_keeps_stale_rows(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "keep.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1)
    assert "a/keep.txt" in _disk_rels(root)

    # A truncated generation never claims full coverage, so it must NOT purge the prior snapshot.
    bfs_index.build_root_progressively(root, set(), generation=2, max_total_entries=0)
    assert "a/keep.txt" in _disk_rels(root)


def test_failed_directory_generation_keeps_stale_rows(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "keep.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1)
    assert "a/keep.txt" in _disk_rels(root)

    real_scan = bfs_index.scan_directory_once

    def _fail_deep(root_fd, root_path, directory, skip_dirs, exclude_path, **kwargs):
        if directory.name == "a":
            result = bfs_index.ScanResult()
            result.error = "scandir:OSError"
            return result
        return real_scan(root_fd, root_path, directory, skip_dirs, exclude_path, **kwargs)

    monkeypatch.setattr(bfs_index, "scan_directory_once", _fail_deep)
    # generation 2's 'a' directory fails every retry -> no full coverage -> stale rows survive.
    bfs_index.build_root_progressively(root, set(), generation=2)
    assert "a/keep.txt" in _disk_rels(root)


# --------------------------------------------------------------------------
# BLOCKER B - coverage is visible LIVE (SQLite), before the manifest is written
# --------------------------------------------------------------------------


def test_live_coverage_visible_before_completion(tmp_path):
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("x", encoding="utf-8")

    build = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with build:
        build.enqueue_startup()
        build.step()  # root committed only; the whole-crawl manifest is NOT written yet
    assert not file_index._index_manifest_path(root).exists()

    coverage = file_index.read_index_coverage(root)
    assert coverage is not None  # served from LIVE SQLite metadata, not the missing manifest
    assert coverage["published_depth"] == 1
    assert coverage["frontier_size"] >= 1
    assert coverage["full_coverage"] is False

    with build:
        build.resume()
        build.run()
    done = file_index.read_index_coverage(root)
    assert done["full_coverage"] is True


def test_coverage_falls_back_to_manifest_when_live_sqlite_unavailable(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1)
    assert file_index._index_manifest_path(root).exists()

    monkeypatch.setattr(file_index, "_coverage_from_live_sqlite", lambda _root, **_kwargs: None)
    coverage = file_index.read_index_coverage(root)
    assert coverage is not None
    assert coverage["source"] == "manifest"
    assert coverage["full_coverage"] is True


def test_coverage_read_timeout_is_small():
    # The bound itself: a status read may never inherit the 30s connect timeout that turned this
    # projection into a multi-second stall per configured root.
    assert file_index._COVERAGE_LIVE_READ_TIMEOUT_SECONDS <= 0.2


def test_coverage_read_is_bounded_during_a_concurrent_build(tmp_path):
    root = tmp_path / "root"
    for i in range(30):
        directory = root / f"d{i}"
        directory.mkdir(parents=True)
        for j in range(5):
            (directory / f"f{j}.txt").write_text("x", encoding="utf-8")

    done = threading.Event()

    def _build():
        try:
            bfs_index.build_root_progressively(root, set(), generation=1)
        finally:
            done.set()

    worker = threading.Thread(target=_build, name="concurrent-build")
    worker.start()
    try:
        deadline = time.perf_counter() + 3.0
        max_elapsed = 0.0
        reads = 0
        while not done.is_set() and time.perf_counter() < deadline:
            started = time.perf_counter()
            file_index.read_index_coverage(root)
            max_elapsed = max(max_elapsed, time.perf_counter() - started)
            reads += 1
        assert reads > 0
        # Every status read stays bounded while the writer is active -- never near the 30s timeout.
        assert max_elapsed < 1.0
    finally:
        worker.join(15)
        assert not worker.is_alive()


def test_multi_root_coverage_aggregation_is_bounded(tmp_path):
    roots = []
    for i in range(5):
        candidate = tmp_path / f"r{i}"
        candidate.mkdir()
        (candidate / "f.txt").write_text("x", encoding="utf-8")
        bfs_index.build_root_progressively(candidate, set(), generation=1)
        roots.append(str(candidate))

    client = _client(tmp_path)
    client.scheduled_roots = roots
    started = time.perf_counter()
    coverage = client.scheduled_root_coverage()
    assert time.perf_counter() - started < 2.0
    assert len(coverage) == 5
    assert all(entry["full_coverage"] for entry in coverage)


# --------------------------------------------------------------------------
# BLOCKER C - a failed scan persists its retry count for restart resume
# --------------------------------------------------------------------------


def test_failed_scan_persists_retry_for_restart_resume(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "f.txt").write_text("x", encoding="utf-8")

    real_scan = bfs_index.scan_directory_once

    def _fail_bad(root_fd, root_path, directory, skip_dirs, exclude_path, **kwargs):
        if directory.name == "bad":
            result = bfs_index.ScanResult()
            result.error = "scandir:OSError"
            return result
        return real_scan(root_fd, root_path, directory, skip_dirs, exclude_path, **kwargs)

    monkeypatch.setattr(bfs_index, "scan_directory_once", _fail_bad)
    build = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with build:
        build.enqueue_startup()
        build.step()  # scan root, enqueue /root/bad
        build.step()  # /root/bad fails -> requeue retries=1 and persist durably

    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        row = conn.execute(
            "SELECT retries FROM frontier WHERE generation = 1 AND state = 'pending' AND directory LIKE ?",
            (f"%{'bad'}",),
        ).fetchone()
    assert row is not None and int(row[0]) == 1  # durable retry count, not reset to 0

    # A fresh process resumes from the durable checkpoint at the SAME retry.
    resumed = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with resumed:
        assert resumed.resume() >= 1
        item = resumed.frontier.pop()
        assert item.directory.endswith("bad")
        assert item.retries == 1


# --------------------------------------------------------------------------
# item 3 - full-build cutover to the breadth-first engine
# --------------------------------------------------------------------------


def test_importing_search_indexer_registers_the_bfs_runner():
    assert file_index._BFS_FULL_BUILD_RUNNER is bfs_index.build_root_into_index


def test_configured_full_build_uses_bfs_not_the_dfs_walk(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "sub" / "mid.txt").write_text("x", encoding="utf-8")
    (root / "sub" / "deeper" / "deep.txt").write_text("x", encoding="utf-8")

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("configured full build must not call the DFS walk helper")

    monkeypatch.setattr(file_index, "_walk_root_with_metrics", _forbidden)

    index = file_index.build_now(root, SEARCH_SKIP_DIRS)
    assert index.ready is True
    names = {name for _p, name, _r, _s, _m in index.entries}
    assert {"top.txt", "mid.txt", "deep.txt"} <= names
    coverage = file_index.read_index_coverage(root)
    assert coverage is not None
    assert coverage["published_depth"] >= 1
    assert coverage["full_coverage"] is True
    assert coverage["active_generation"] >= 1


def test_follower_disk_read_sees_layer_one_rows_from_the_bfs_build(tmp_path):
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("x", encoding="utf-8")
    file_index.build_now(root, SEARCH_SKIP_DIRS)

    result = file_index.search_disk_index(
        root,
        SEARCH_SKIP_DIRS,
        "",
        lambda p, n, r: {"path": p, "name": n, "_sort_key": (0, 0, 0, 0, 0, n)},
        50,
        ["top"],
    )
    assert result is not None
    found, _truncated = result
    assert any(entry["name"] == "top.txt" for entry in found)


def test_bfs_full_build_off_list_exception_still_clears_building(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    index = file_index.RootIndex(root)
    index.building = True
    index.build_generation = 1
    index.active_generation = 1
    index.stop_event = threading.Event()
    root_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    index.replace_root_fd(root_descriptor)
    os.close(root_descriptor)

    def _boom(*_args, **_kwargs):
        # sqlite3.Error is off the (OSError, RuntimeError, ValueError) list _run_build catches; the
        # finally backstop must still clear `building` so schedule_refreshes does not skip forever.
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", _boom)
    try:
        with pytest.raises(sqlite3.OperationalError):
            file_index._run_build(index, SEARCH_SKIP_DIRS, generation=1)
        assert index.building is False
    finally:
        index.close_root_fd()


def test_policy_signature_change_rebuilds_without_stale_excluded_rows(tmp_path):
    root = tmp_path / "root"
    (root / "node_modules").mkdir(parents=True)
    (root / "visible.txt").write_text("ok", encoding="utf-8")
    (root / "node_modules" / "package.js").write_text("package", encoding="utf-8")
    skip_dirs = SEARCH_SKIP_DIRS - {"node_modules"}

    initial = file_index.build_now(root, skip_dirs)
    assert any(name == "package.js" for _p, name, _r, _s, _m in initial.entries)

    filtered = file_index.build_now(
        root,
        skip_dirs,
        exclude_path=lambda path: "node_modules" in path.parts,
        exclude_signature="node-modules-filter",
    )
    names = {name for _p, name, _r, _s, _m in filtered.entries}
    assert "visible.txt" in names
    assert "package.js" not in names


def test_total_entry_cap_truncates_but_stays_a_durable_partial(tmp_path, monkeypatch):
    # BFS lifecycle item 6 (codex): hitting the total-row cap now yields a v5 TYPED PARTIAL store, not
    # a deletion. History: the cap path called `_drop_persisted_index`, blanking a large root on every
    # build and leaving Quick Open permanently "Indexing...". The partial must PERSIST and stay
    # searchable; genuine over-file/over-byte STORAGE rejection is a separate concern
    # (test_file_index.py::test_persistence_can_be_disabled_or_rejected_by_file_budget).
    monkeypatch.setattr(file_index, "MAX_INDEX_FILES", 2)
    root = tmp_path / "root"
    root.mkdir()
    for name in ("one.txt", "two.txt", "three.txt"):
        (root / name).write_text(name, encoding="utf-8")
    built = file_index.build_now(root, SEARCH_SKIP_DIRS)
    assert built.truncated is True
    assert built.too_large is True
    assert len(built.entries) == 2
    # DURABLE: the SQLite database and the manifest both survive; coverage is partial, never full.
    assert built.persisted is True
    assert file_index._index_disk_path(root).exists()
    assert file_index._index_manifest_path(root).exists()


# --------------------------------------------------------------------------
# item 1 + 8 - scheduled obligation and truthful runtime_status projection
# --------------------------------------------------------------------------


def test_no_configured_roots_reports_demand_started_idle(tmp_path):
    row = _client(tmp_path).runtime_status()
    assert row["demand_started"] is True
    assert "absence_expected_reason" not in row
    assert row["scheduled_root_count"] == 0
    assert row["root_coverage"] == []


def test_configured_roots_report_scheduled_not_demand(tmp_path):
    client = _client(tmp_path)
    client.scheduled_roots = [str(tmp_path / "a"), str(tmp_path / "b")]
    client.scheduler_lease_id = "lease-1"
    row = client.runtime_status()
    assert "demand_started" not in row
    assert row["absence_expected_reason"] == search_indexer.INDEXER_SCHEDULED_ABSENCE_REASON
    assert row["scheduled_root_count"] == 2
    assert row["scheduler_leased"] is True
    assert len(row["root_coverage"]) == 2


def test_scheduled_absence_classifies_as_starting_not_demand_idle():
    # A synthetic absent row isolates the classification from live registry noise.
    scheduled = {"pid": 0, "absence_expected_reason": search_indexer.INDEXER_SCHEDULED_ABSENCE_REASON}
    state, reason = observed_health(scheduled)
    assert state == "starting"
    assert reason == search_indexer.INDEXER_SCHEDULED_ABSENCE_REASON

    demand = {"pid": 0, "demand_started": True}
    demand_state, _ = observed_health(demand)
    assert demand_state == "starting"


def test_scheduled_root_coverage_projects_measured_manifest(tmp_path):
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "f.txt").write_text("x", encoding="utf-8")
    (root / "deep" / "g.txt").write_text("x", encoding="utf-8")
    file_index.build_now(root, SEARCH_SKIP_DIRS)

    client = _client(tmp_path)
    client.scheduled_roots = [str(root)]
    coverage = client.scheduled_root_coverage()
    assert len(coverage) == 1
    assert coverage[0]["root"] == str(root)
    assert coverage[0]["lifecycle"] in {"indexed", "indexing"}
    assert coverage[0]["published_depth"] >= 1
    assert coverage[0]["entry_count"] >= 1


def test_scheduled_root_without_snapshot_reports_scheduled_lifecycle(tmp_path):
    client = _client(tmp_path)
    client.scheduled_roots = [str(tmp_path / "never-built")]
    coverage = client.scheduled_root_coverage()
    assert coverage[0]["lifecycle"] == "scheduled"
    assert coverage[0]["published_depth"] == 0


class _FakeLeaseDaemon:
    """A minimal indexd stand-in that tracks the DAEMON-side lease set, not just client fields."""

    def __init__(self):
        self.leases: dict[str, object] = {}
        self.counter = 0
        self.lease_calls = 0
        self.release_calls = 0
        self.enqueued: list[str] = []
        self.unindexed: list[str] = []
        self.fail_release = False
        self.fail_lease = False
        self.fail_unindex = False

    def request(self, payload, timeout=0.5):
        action = payload.get("action")
        if action == "lease":
            self.lease_calls += 1
            if self.fail_lease:
                return {"ok": False, "status": "unavailable"}
            # Emulate the shared acquire_client_lease owner: a still-held id is returned unchanged;
            # a missing/stale id yields a fresh lease.
            #
            # The key is `lease_id`, the one spelling the shared client actually puts on the wire
            # (LocalServiceRegistry.acquire_lease). This fake used to read `existing_lease_id`, a
            # key nothing sends -- and so did indexd's real handler, which is exactly why a healthy
            # client refreshing its lease minted a new unreapable row every time instead of
            # refreshing one. A double that models the wire with the wrong key cannot catch that:
            # it agreed with the bug rather than with the protocol.
            existing = payload.get("lease_id")
            if existing and existing in self.leases:
                return {"ok": True, "lease_id": existing}
            self.counter += 1
            lease_id = f"lease-{self.counter}"
            self.leases[lease_id] = payload.get("client_pid")
            return {"ok": True, "lease_id": lease_id}
        if action == "release":
            self.release_calls += 1
            if self.fail_release:
                return {"ok": False, "status": "unavailable"}
            self.leases.pop(payload.get("lease_id"), None)
            return {"ok": True, "released": True}
        if action == "enqueue":
            self.enqueued.append(payload.get("root"))
            return {"ok": True, "accepted": True}
        if action == "unindex":
            if self.fail_unindex:
                return {"ok": False, "status": "unavailable"}
            self.unindexed.append(payload.get("root"))
            return {"ok": True, "accepted": True}
        return {"ok": True}


def _leased_client(tmp_path):
    client = _client(tmp_path)
    daemon = _FakeLeaseDaemon()
    client.request = daemon.request
    client.ensure_started = lambda: True
    return client, daemon


def test_repeated_same_roots_hold_exactly_one_daemon_lease(tmp_path):
    client, daemon = _leased_client(tmp_path)
    roots = [str(tmp_path / "a")]
    for _ in range(3):
        client.lease_configured_roots(roots)
    # The DAEMON lease count is what matters -- refreshing three times must not leak leases.
    assert len(daemon.leases) == 1
    assert client.scheduler_lease_id in daemon.leases


def test_changed_roots_reuse_the_same_lease(tmp_path):
    client, daemon = _leased_client(tmp_path)
    client.lease_configured_roots([str(tmp_path / "a")])
    client.lease_configured_roots([str(tmp_path / "a"), str(tmp_path / "b")])
    assert len(daemon.leases) == 1
    assert client.scheduler_lease_id in daemon.leases


def test_daemon_restart_reacquires_a_lease_on_the_new_daemon(tmp_path):
    client, daemon = _leased_client(tmp_path)
    client.lease_configured_roots([str(tmp_path / "a")])
    assert len(daemon.leases) == 1
    # indexd restarts: a fresh daemon with an EMPTY lease table replaces it. The client still holds
    # the old id, but the shared owner must mint a new one so the new daemon is actually leased.
    replacement = _FakeLeaseDaemon()
    client.request = replacement.request
    client.lease_configured_roots([str(tmp_path / "a")])
    assert len(replacement.leases) == 1
    assert client.scheduler_lease_id in replacement.leases


def test_empty_roots_releases_the_daemon_lease(tmp_path):
    client, daemon = _leased_client(tmp_path)
    client.lease_configured_roots([str(tmp_path / "a")])
    assert len(daemon.leases) == 1
    client.lease_configured_roots([])
    assert daemon.release_calls == 1
    assert len(daemon.leases) == 0
    assert client.scheduler_lease_id is None
    assert client.scheduled_roots == []


def test_failed_release_preserves_the_lease_handle_for_retry(tmp_path):
    client, daemon = _leased_client(tmp_path)
    client.lease_configured_roots([str(tmp_path / "a")])
    daemon.fail_release = True
    client.lease_configured_roots([])
    # The daemon still holds the lease and the client keeps the handle to retry it.
    assert len(daemon.leases) == 1
    assert client.scheduler_lease_id is not None
    daemon.fail_release = False
    client.release_scheduler_lease()
    assert len(daemon.leases) == 0
    assert client.scheduler_lease_id is None


def test_failed_lease_does_not_pin_a_handle_and_retries_next_call(tmp_path):
    client, daemon = _leased_client(tmp_path)
    daemon.fail_lease = True
    client.lease_configured_roots([str(tmp_path / "a")])
    assert client.scheduler_lease_id is None
    daemon.fail_lease = False
    client.lease_configured_roots([str(tmp_path / "a")])
    assert client.scheduler_lease_id is not None
    assert len(daemon.leases) == 1


# --------------------------------------------------------------------------
# Fifth audit - delta-based, idempotent schedule reconciliation
# --------------------------------------------------------------------------


def test_unchanged_settings_enqueue_nothing(tmp_path):
    client, daemon = _leased_client(tmp_path)
    roots = [str(tmp_path / "a"), str(tmp_path / "b")]
    client.lease_configured_roots(roots)
    # Two configured roots enqueued exactly once each.
    assert sorted(daemon.enqueued) == sorted(roots)
    # A repeat (an unrelated setting change re-runs the reconcile) must enqueue nothing new and
    # unindex nothing -- a completed root is never re-crawled by reconciliation.
    client.lease_configured_roots(roots)
    assert sorted(daemon.enqueued) == sorted(roots)
    assert daemon.unindexed == []


def test_removed_root_is_unindexed_once_and_others_not_rebuilt(tmp_path):
    client, daemon = _leased_client(tmp_path)
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    client.lease_configured_roots([a, b])
    assert sorted(daemon.enqueued) == sorted([a, b])
    # Drop B: exactly one bounded unindex for B, and A is NOT re-enqueued/rebuilt.
    client.lease_configured_roots([a])
    assert daemon.unindexed == [b]
    assert sorted(daemon.enqueued) == sorted([a, b])  # unchanged: no A rebuild
    assert client.scheduled_roots == [a]
    assert len(daemon.leases) == 1  # lease retained while a root remains


def test_empty_set_unindexes_all_and_releases_lease(tmp_path):
    client, daemon = _leased_client(tmp_path)
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    client.lease_configured_roots([a, b])
    client.lease_configured_roots([])
    assert sorted(daemon.unindexed) == sorted([a, b])
    assert len(daemon.leases) == 0
    assert client.scheduler_lease_id is None


def test_failed_removal_is_retained_and_retried_without_dropping_lease(tmp_path):
    client, daemon = _leased_client(tmp_path)
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    client.lease_configured_roots([a, b])
    daemon.fail_unindex = True
    client.lease_configured_roots([a])
    # B's removal failed: it is retained for retry and the lease is NOT dropped.
    assert daemon.unindexed == []
    assert client._pending_removals == {b}
    assert len(daemon.leases) == 1
    # The next reconcile retries the pending removal once the daemon recovers.
    daemon.fail_unindex = False
    client.lease_configured_roots([a])
    assert daemon.unindexed == [b]
    assert client._pending_removals == set()


def test_layer_one_serves_while_a_concurrent_depth2_scan_is_held(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("x", encoding="utf-8")

    # Keep the read path "warming": never adopt the partial snapshot as ready, never auto-start a
    # competing build. The warming branch must serve the committed layer-1 SQLite itself.
    monkeypatch.setattr(file_index, "_start_build", lambda *_a, **_k: None)
    monkeypatch.setattr(file_index, "_load_disk", lambda *_a, **_k: None)

    walked: list[str] = []
    real_full_tree = fs_search._search_full_tree

    def _record_full_tree(*args, **kwargs):
        walked.append("full-tree")
        return real_full_tree(*args, **kwargs)

    monkeypatch.setattr(fs_search, "_search_full_tree", _record_full_tree)

    # A real concurrent build, blocked INSIDE the depth-2 directory scan, so the read proves it does
    # not wait on the held scan or a build lock -- not merely a read between two sequential steps.
    depth2_entered = threading.Event()
    release_depth2 = threading.Event()
    real_scan = bfs_index.scan_directory_once

    def _blocking_scan(root_fd, root_path, directory, skip_dirs, exclude_path, **kwargs):
        if directory.name == "deep":
            depth2_entered.set()
            release_depth2.wait(10)
        return real_scan(root_fd, root_path, directory, skip_dirs, exclude_path, **kwargs)

    monkeypatch.setattr(bfs_index, "scan_directory_once", _blocking_scan)

    policy = fs_search._search_index_policy(root)

    def _run_build():
        bfs_index.build_root_progressively(
            root,
            policy["skip_dirs"],
            exclude_signature=policy["exclude_signature"],
            generation=1,
        )

    worker = threading.Thread(target=_run_build, name="held-build")
    worker.start()
    try:
        assert depth2_entered.wait(10)  # root layer committed; now blocked inside the deep scan
        # (a) a name that exists only below the frontier: empty + warming + NO full-tree walk.
        below = fs_search.search_files(str(root), "buried", recursive=True)
        assert walked == []
        assert below.get("index_state") == "warming"
        assert "buried.txt" not in {entry["name"] for entry in below["files"]}
        # (b) the layer-1 file returns within the read budget, NO full-tree walk, no wait on the scan.
        started = time.perf_counter()
        layer_one = fs_search.search_files(str(root), "top", recursive=True)
        assert time.perf_counter() - started < 1.0
        assert walked == []
        assert "top.txt" in {entry["name"] for entry in layer_one["files"]}
        assert layer_one.get("index_coverage") == "partial"
    finally:
        release_depth2.set()
    worker.join(15)
    assert not worker.is_alive()
    # The released scan publishes depth 2; the deep file is now searchable, still no full-tree walk.
    completed = fs_search.search_files(str(root), "buried", recursive=True)
    assert "buried.txt" in {entry["name"] for entry in completed["files"]}
    assert walked == []


def test_clean_configured_roots_dedupes_and_drops_non_string(tmp_path):
    resolved = str(tmp_path.resolve())
    got = search_indexer.SearchIndexerClient._clean_configured_roots(
        [str(tmp_path), str(tmp_path), "", "   ", 123, None]
    )
    assert got == [resolved]


# --------------------------------------------------------------------------
# Slice C item 5 - lookup separated from crawl scheduling: a cached hit returns
# on the bounded read path without a jobd/crawler wait, carries coverage
# metadata, and asynchronously promotes a not-yet-covered scope's frontier.
# --------------------------------------------------------------------------


def _commit_layer_one_only(root):
    """Publish just the root listing (layer 1), leaving deeper layers pending -> a warming snapshot.

    Built under the SAME policy the read path resolves (skip_dirs + exclusion signature) so the
    committed snapshot actually matches the follower read, matching the existing layer-1 test.
    """
    policy = fs_search._search_index_policy(root)
    build = bfs_index.ProgressiveBuild(
        root,
        policy["skip_dirs"],
        exclude_signature=policy["exclude_signature"],
        generation=1,
    )
    with build:
        build.enqueue_startup()
        build.step()
    return build


def test_cache_hit_returns_within_budget_while_crawler_and_jobd_blocked(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "t5t.md").write_text("x", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("x", encoding="utf-8")
    _commit_layer_one_only(root)  # t5t.md is a committed layer-1 row; deep/ still pending

    # Crawler blocked: never adopt the partial snapshot as ready, never auto-start a build.
    monkeypatch.setattr(file_index, "_start_build", lambda *_a, **_k: None)
    monkeypatch.setattr(file_index, "_load_disk", lambda *_a, **_k: None)

    # jobd / owner RPCs blocked: any SYNCHRONOUS call on the query thread would hang for 30s.
    def _blocked(*_a, **_k):
        time.sleep(30)
        return {}

    monkeypatch.setattr(file_index, "request_background_index_search", _blocked)
    monkeypatch.setattr(file_index, "_BACKGROUND_OWNER_REFRESH_REQUESTER", lambda *_a, **_k: _blocked())
    file_index._PROMOTION_LAST_DISPATCH.clear()

    started = time.perf_counter()
    result = fs_search.search_files(str(root), "t5t", recursive=True)
    elapsed = time.perf_counter() - started
    # The cache hit returns on the bounded read path, NOT behind the 30s jobd/crawler block.
    assert elapsed < 2.0
    assert "t5t.md" in {entry["name"] for entry in result["files"]}
    assert result["index_state"] == "warming"
    # Explicit freshness/coverage metadata rides along, from the ONE coverage owner.
    assert result["snapshot_state"] in {"warming", "partial"}
    assert result["progressive_coverage"]["published_depth"] >= 1
    assert result["refresh_pending"] is True
    # Step 4: the first snapshot carries the baseline cursor the client seeds delta reads with.
    baseline = result["initial_cursor"]
    assert isinstance(baseline, str) and baseline

    # A delta read with that cursor stays on the SAME bounded, read-only committed-journal path: it
    # returns within budget too, without waiting behind the 30s crawler/jobd block, and the cursor
    # round-trips (same root/policy/generation -> accepted, not a rebase). This is exactly what the
    # HTTP `cursor=` param drives through `search_files`.
    started_delta = time.perf_counter()
    delta = fs_search.search_files(str(root), "t5t", recursive=True, cursor=baseline)
    assert time.perf_counter() - started_delta < 2.0
    assert "rebase_required" not in delta
    assert "files" not in delta
    assert "changes" in delta and isinstance(delta["cursor"], str) and delta["more"] is False


def test_warming_query_promotes_the_frontier_without_blocking(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("x", encoding="utf-8")
    _commit_layer_one_only(root)

    monkeypatch.setattr(file_index, "_start_build", lambda *_a, **_k: None)
    monkeypatch.setattr(file_index, "_load_disk", lambda *_a, **_k: None)
    file_index._PROMOTION_LAST_DISPATCH.clear()

    captured: list[tuple[str, dict]] = []
    dispatched = threading.Event()

    def _requester(role, payload):
        captured.append((role, payload))
        dispatched.set()
        return {"ok": True, "accepted": True, "local_owner": True}

    monkeypatch.setattr(file_index, "_BACKGROUND_OWNER_REFRESH_REQUESTER", _requester)

    # A name that exists only below the published frontier: honest empty + warming, and it promotes.
    result = fs_search.search_files(str(root), "buried", recursive=True)
    assert result["index_state"] == "warming"
    assert "buried.txt" not in {entry["name"] for entry in result["files"]}
    assert dispatched.wait(3)  # promotion dispatched OFF the query thread
    role, payload = captured[0]
    assert role == file_index.SEARCH_INDEX_ROLE
    assert payload["operation"] == "promote"
    assert payload["reason"] == file_index.USER_VISIBLE_DEMAND_REASON


def test_indexd_promote_bumps_frontier_or_kicks_unscheduled_root(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "t.txt").write_text("x", encoding="utf-8")
    _commit_layer_one_only(root)  # a/ pending at breadth priority

    indexer = search_indexer.PersistentSearchIndexer(tmp_path / "svc" / "indexer.sock")
    resp = indexer.promote(str(root))
    assert resp["ok"] and resp["promoted"] >= 1 and resp["kicked"] is False

    # A root with no snapshot has nothing to promote, so it is kicked with a startup enqueue instead.
    other = tmp_path / "other"
    other.mkdir()
    resp2 = indexer.promote(str(other))
    assert resp2["promoted"] == 0 and resp2["kicked"] is True
    assert str(other.resolve()) in indexer.pending_paths


def test_request_user_visible_promotion_is_nonblocking_and_debounced(tmp_path, monkeypatch):
    file_index._PROMOTION_LAST_DISPATCH.clear()
    started = threading.Event()
    root = tmp_path / "root"
    root.mkdir()

    def _slow(_role, _payload):
        started.set()
        time.sleep(5)
        return {}

    monkeypatch.setattr(file_index, "_BACKGROUND_OWNER_REFRESH_REQUESTER", _slow)
    t0 = time.perf_counter()
    assert file_index.request_user_visible_promotion(str(root)) is True
    assert time.perf_counter() - t0 < 0.5  # dispatch returns immediately; the RPC runs off-thread
    assert file_index.request_user_visible_promotion(str(root)) is False  # coalesced within the window
    assert started.wait(2)


def test_request_user_visible_promotion_without_owner_is_a_noop(monkeypatch):
    monkeypatch.setattr(file_index, "_BACKGROUND_OWNER_REFRESH_REQUESTER", None)
    assert file_index.request_user_visible_promotion("/x/no-owner") is False


def test_request_user_visible_promotion_carries_the_authorized_root_identity(tmp_path, monkeypatch):
    file_index._PROMOTION_LAST_DISPATCH.clear()
    root = tmp_path / "root"
    root.mkdir()
    captured = []
    delivered = threading.Event()

    def capture(_role, payload):
        captured.append(payload)
        delivered.set()
        return {}

    monkeypatch.setattr(file_index, "_BACKGROUND_OWNER_REFRESH_REQUESTER", capture)
    assert file_index.request_user_visible_promotion(str(root)) is True
    assert delivered.wait(2)
    assert captured == [
        {
            "root": str(root),
            "operation": "promote",
            "reason": file_index.USER_VISIBLE_DEMAND_REASON,
            file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: list(file_index.root_identity(root.stat())),
        }
    ]


# --------------------------------------------------------------------------
# Slice C item 7 - the 1800s safety refresh: scheduled from the clock (not a
# query), lowest priority, through the same frontier, no duplicate generations.
# --------------------------------------------------------------------------


def _ready_registered_root(tmp_path, anchor=1000.0):
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    ri = file_index.build_now(root, SEARCH_SKIP_DIRS)
    with ri.lock:
        ri.refresh_seconds = 1800.0
        ri.last_full_build_at = anchor
        ri.built_at = anchor
        ri.dirty_paths.clear()
    return root, ri


def test_ttl_deadline_fires_a_lowest_priority_safety_refresh(tmp_path, monkeypatch):
    _root, _ri = _ready_registered_root(tmp_path)
    calls: list[str] = []

    def _record_start(*_a, **kwargs):
        # `_start_build` now returns whether a worker was installed; a stub that stands in for a
        # successful start returns True so `schedule_refreshes` counts it.
        calls.append(kwargs.get("build_reason", ""))
        return True

    monkeypatch.setattr(file_index, "_start_build", _record_start)
    # Before the deadline: no refresh, driven purely by the clock and NOT by any query.
    assert file_index.schedule_refreshes(now=1000.0 + 1799) == 0
    assert calls == []
    # At the deadline: exactly one refresh, tagged as the lowest-priority safety reason.
    assert file_index.schedule_refreshes(now=1000.0 + 1801) == 1
    assert calls == [file_index.SAFETY_REFRESH_REASON]


def test_safety_refresh_does_not_duplicate_generations_while_building(tmp_path, monkeypatch):
    _root, ri = _ready_registered_root(tmp_path)
    with ri.lock:
        ri.building = True  # a build (a previous safety generation) is already in flight
    calls: list[dict] = []
    monkeypatch.setattr(file_index, "_start_build", lambda *_a, **kwargs: calls.append(kwargs))
    # Long past the deadline, but a build is running: no SECOND full generation is started.
    assert file_index.schedule_refreshes(now=1000.0 + 5000) == 0
    assert calls == []


def test_safety_refresh_reconciles_a_change_no_event_marked_dirty(tmp_path):
    # Missed-event repair: a change with NO watch event and NO dirty mark is still reconciled by the
    # full-safety-refresh re-listing, through the same frontier, without a duplicate generation.
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1)
    assert "deep/added.md" not in _disk_rels(root)

    (root / "deep" / "added.md").write_text("x", encoding="utf-8")  # the missed change
    bfs_index.build_root_progressively(root, set(), generation=2, reason=bfs_index.REASON_SAFETY)

    assert "deep/added.md" in _disk_rels(root)
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        active = conn.execute("SELECT value FROM metadata WHERE key='active_generation'").fetchone()[0]
        assert active == "2"
        # A clean completed generation leaves exactly one generation of rows: no duplicate full build.
        gens = {row[0] for row in conn.execute("SELECT DISTINCT generation FROM entries")}
        assert gens == {2}
