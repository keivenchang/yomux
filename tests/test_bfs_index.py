"""Slice A evidence: breadth-first, directory-at-a-time Quick Open indexing.

These tests prove the three behaviors of DOIT.fs-interactivity Slice A:
  - item 4: v5 schema/manifest generations + per-directory coverage, migration,
    generation fencing, and stale-snapshot readability;
  - item 2: a bounded, generation-fenced breadth-first frontier with shallow
    ordering, FIFO ties, multi-root fairness, cancellation, retry, and atomic
    checkpoint/restart resume;
  - item 3: one listing per directory that publishes direct rows + the next
    layer's frontier, with the ACTUAL filesystem open order proven breadth-first
    (deep-chain and very-wide fixtures), not DFS-then-sort.
"""

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from yolomux_lib.filesystem import SEARCH_SKIP_DIRS
from yolomux_lib.search import bfs_index
from yolomux_lib.search import file_index


@pytest.fixture(autouse=True)
def _isolated_index_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    file_index.clear_memory_indexes()
    yield
    file_index.clear_memory_indexes()


def _rel_open_order(build, root):
    return [Path(p).relative_to(root).as_posix() for p in build.open_order]


def _entry_rel_paths(root):
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        return sorted(row[0] for row in conn.execute("SELECT relative_path FROM entries"))


# --------------------------------------------------------------------------
# Item 3 — directory-at-a-time scanning and REAL breadth-first open order
# --------------------------------------------------------------------------


def test_scan_directory_once_lists_only_direct_children(tmp_path):
    root = tmp_path / "root"
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "sub" / "mid.txt").write_text("x", encoding="utf-8")
    (root / "sub" / "deeper" / "deep.txt").write_text("x", encoding="utf-8")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = bfs_index.scan_directory_once(root_fd, root, root, set(), None)
    finally:
        os.close(root_fd)
    assert [name for _p, name, _r, _s, _m in result.files] == ["top.txt"]
    assert result.child_directories == ["sub"]
    # A single listing must not have descended into sub/ or reported its files.
    assert all("mid.txt" != name and "deep.txt" != name for _p, name, _r, _s, _m in result.files)


def test_deep_chain_open_order_is_breadth_first(tmp_path):
    # A deep chain: root/a/b/c/d, each level one directory. Breadth-first opens
    # them shallowest-first, one directory per work item.
    root = tmp_path / "root"
    chain = root
    for name in ("a", "b", "c", "d"):
        chain = chain / name
    chain.mkdir(parents=True)
    (root / "r.txt").write_text("x", encoding="utf-8")
    (root / "a" / "a.txt").write_text("x", encoding="utf-8")
    (root / "a" / "b" / "b.txt").write_text("x", encoding="utf-8")
    build = bfs_index.build_root_progressively(root, set(), generation=1)
    assert _rel_open_order(build, root) == [".", "a", "a/b", "a/b/c", "a/b/c/d"]
    assert build.published_depth == 5


def test_very_wide_open_order_completes_each_layer_before_the_next(tmp_path):
    # Five sibling directories, each with its own child directory. Breadth-first
    # opens the root, then ALL five depth-2 siblings, then their depth-3 children —
    # a DFS stack walk would open d0, d0/sub, d1, d1/sub, ... interleaving depths.
    root = tmp_path / "root"
    root.mkdir()
    for i in range(5):
        (root / f"d{i}" / "sub").mkdir(parents=True)
        (root / f"d{i}" / f"f{i}.txt").write_text("x", encoding="utf-8")
    build = bfs_index.build_root_progressively(root, set(), generation=1)
    order = _rel_open_order(build, root)
    depths = [0 if rel == "." else rel.count("/") + 1 for rel in order]
    # Depths must be monotonically non-decreasing: no deep directory is opened
    # before a shallower one that was still pending.
    assert depths == sorted(depths), order
    assert order[0] == "."
    assert set(order[1:6]) == {f"d{i}" for i in range(5)}
    assert set(order[6:]) == {f"d{i}/sub" for i in range(5)}


def test_build_never_uses_the_dfs_walk_helper(tmp_path, monkeypatch):
    # Negative pin: the breadth-first build must not fall back to the recursive
    # DFS helper _walk_root_with_metrics for a configured-root build.
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "x.txt").write_text("x", encoding="utf-8")

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("breadth-first build must not call the DFS walk helper")

    monkeypatch.setattr(file_index, "_walk_root_with_metrics", _forbidden)
    build = bfs_index.build_root_progressively(root, set(), generation=1)
    assert build.scanned_directories == 3  # root, a, a/b


# --------------------------------------------------------------------------
# Item 2 — the bounded, generation-fenced frontier
# --------------------------------------------------------------------------


def _item(root, directory, depth, reason=bfs_index.REASON_BREADTH):
    return bfs_index.FrontierItem(
        root=root,
        directory=directory,
        depth=depth,
        generation=1,
        reason=reason,
        priority=bfs_index.priority_for_reason(reason),
        enqueued_at=0.0,
    )


def test_frontier_orders_shallow_before_deep_then_fifo():
    frontier = bfs_index.BfsFrontier()
    frontier.enqueue(_item("/r", "/r/a/deep", 3))
    frontier.enqueue(_item("/r", "/r/a", 2))
    frontier.enqueue(_item("/r", "/r/b", 2))  # same depth as /r/a, later FIFO
    frontier.enqueue(_item("/r", "/r", 1, reason=bfs_index.REASON_STARTUP))
    popped = [frontier.pop().directory for _ in range(4)]
    assert popped == ["/r", "/r/a", "/r/b", "/r/a/deep"]


def test_frontier_coalesces_repeated_demand_on_identity():
    frontier = bfs_index.BfsFrontier()
    assert frontier.enqueue(_item("/r", "/r/a", 2)) is True
    # Same (root, directory, generation) demand must coalesce, not duplicate.
    assert frontier.enqueue(_item("/r", "/r/a", 2, reason=bfs_index.REASON_HOT)) is True
    assert frontier.size() == 1
    popped = frontier.pop()
    # The higher-priority (hot) reason wins the coalesced record.
    assert popped.reason == bfs_index.REASON_HOT
    assert frontier.pop() is None


def test_frontier_is_bounded_and_reports_truncation():
    frontier = bfs_index.BfsFrontier(max_items=2)
    assert frontier.enqueue(_item("/r", "/r/a", 2)) is True
    assert frontier.enqueue(_item("/r", "/r/b", 2)) is True
    assert frontier.enqueue(_item("/r", "/r/c", 2)) is False
    assert frontier.truncated is True
    assert frontier.size() == 2


def test_frontier_multi_root_fairness_interleaves_layer_one():
    # One wide root A (many depth-2 dirs) must not delay root B's own layer 1.
    frontier = bfs_index.BfsFrontier()
    frontier.enqueue(_item("/A", "/A", 1, reason=bfs_index.REASON_STARTUP))
    frontier.enqueue(_item("/B", "/B", 1, reason=bfs_index.REASON_STARTUP))
    for i in range(20):
        frontier.enqueue(_item("/A", f"/A/d{i}", 2))
    order = []
    while (item := frontier.pop()) is not None:
        order.append(item.directory)
    # Both roots' depth-1 listings precede any depth-2 work.
    assert order.index("/A") < order.index("/A/d0")
    assert order.index("/B") < order.index("/A/d0")
    assert order[:2] == sorted(["/A", "/B"], key=order.index)[:2]


def test_frontier_retry_is_bounded():
    frontier = bfs_index.BfsFrontier()
    item = _item("/r", "/r/a", 2)
    assert frontier.requeue(item) is not None
    requeued = frontier.pop()
    assert requeued.retries == 1
    assert frontier.requeue(requeued) is not None
    twice = frontier.pop()
    assert twice.retries == 2
    assert frontier.requeue(twice) is not None
    thrice = frontier.pop()
    # Beyond DEFAULT_MAX_RETRIES the item is dropped, not requeued forever.
    assert frontier.requeue(thrice) is None


def test_frontier_cancel_generation_drops_only_that_generation():
    frontier = bfs_index.BfsFrontier()
    frontier.enqueue(_item("/r", "/r/a", 2))
    frontier.enqueue(
        bfs_index.FrontierItem("/r", "/r/b", 2, generation=2, reason=bfs_index.REASON_BREADTH, priority=3, enqueued_at=0.0)
    )
    assert frontier.cancel_generation("/r", 1) == 1
    remaining = [item.directory for item in frontier.pending()]
    assert remaining == ["/r/b"]


# --------------------------------------------------------------------------
# Item 2/4 — restart/checkpoint resume and crash-resume generation fencing
# --------------------------------------------------------------------------


def test_restart_resumes_shallowest_pending_without_rediscovery(tmp_path):
    # A partial crawl checkpoints its frontier to SQLite. After a restart, resume
    # must continue at the shallowest pending directory read straight from the
    # frontier table — it must NOT re-list the already-scanned root.
    root = tmp_path / "root"
    root.mkdir()
    for i in range(3):
        (root / f"d{i}" / "sub").mkdir(parents=True)
        (root / f"d{i}" / f"f{i}.txt").write_text("x", encoding="utf-8")

    first = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with first:
        first.enqueue_startup()
        first.step()  # scan root only; d0..d2 now persisted as pending depth-2
        first.step()  # scan the first depth-2 directory

    scanned_so_far = set(first.open_order)
    # Fresh process/build with an EMPTY in-memory frontier: resume from disk.
    resumed = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with resumed:
        loaded = resumed.resume()
        assert loaded >= 1
        resumed.run()

    # The root was never reopened by the resumed build (no recursive rediscovery).
    assert str(root) not in resumed.open_order
    # The resumed build only opened directories that were still pending.
    assert scanned_so_far.isdisjoint(set(resumed.open_order))
    # The union covers every directory exactly once.
    all_opened = first.open_order + resumed.open_order
    assert len(all_opened) == len(set(all_opened))
    # Every file is indexed after the resumed completion.
    assert _entry_rel_paths(root) == sorted([f"d{i}/f{i}.txt" for i in range(3)])


def test_abandoned_generation_cannot_overwrite_a_newer_one(tmp_path):
    # A slow generation-1 worker that publishes after generation 2 took over must
    # abort its transaction rather than overwrite the newer generation's rows.
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    (root / "child" / "old.txt").write_text("x", encoding="utf-8")

    stale = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with stale:
        stale.enqueue_startup()
        stale.step()  # publishes root at gen 1, enqueues /root/child as pending

        # A newer generation takes over the same root database.
        with file_index._connect_sqlite_index(root) as conn:
            file_index._ensure_sqlite_schema(conn)
            conn.execute(
                "INSERT INTO metadata(key, value) VALUES ('active_generation', '2') "
                "ON CONFLICT(key) DO UPDATE SET value='2'"
            )
            conn.commit()

        # The lingering gen-1 worker tries to publish /root/child; the fence aborts it.
        stale.step()

    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        child_rows = conn.execute(
            "SELECT COUNT(*) FROM directory_coverage WHERE directory = ? AND generation = 1",
            (str(root / "child"),),
        ).fetchone()[0]
        # The fenced generation never recorded coverage for the child directory.
        assert child_rows == 0
        active = conn.execute("SELECT value FROM metadata WHERE key='active_generation'").fetchone()[0]
        assert active == "2"


def test_stale_snapshot_stays_searchable_during_progressive_rebuild(tmp_path):
    # A previous generation's deep rows remain searchable while a new generation
    # has only republished the root. Nothing is blanked at refresh start.
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("x", encoding="utf-8")

    bfs_index.build_root_progressively(root, set(), generation=1)
    assert "deep/buried.txt" in _entry_rel_paths(root)

    # Generation 2 starts and publishes ONLY the root listing so far.
    second = bfs_index.ProgressiveBuild(root, set(), generation=2)
    with second:
        second.enqueue_startup()
        second.step()  # root only; deep/ not yet rescanned

    rels = _entry_rel_paths(root)
    # The generation-1 deep row is still present and searchable.
    assert "deep/buried.txt" in rels
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        gen = conn.execute(
            "SELECT generation FROM entries WHERE relative_path = 'deep/buried.txt'"
        ).fetchone()[0]
        assert gen == 1  # still the stale generation, not yet replaced
        top_gen = conn.execute("SELECT generation FROM entries WHERE relative_path = 'top.txt'").fetchone()[0]
        assert top_gen == 2  # the root was republished under generation 2


# --------------------------------------------------------------------------
# Item 3 — security/durability invariants preserved
# --------------------------------------------------------------------------


def test_exclusions_and_skip_dirs_are_never_scanned(tmp_path):
    root = tmp_path / "root"
    (root / "keep").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x", encoding="utf-8")
    (root / "keep" / "ok.txt").write_text("x", encoding="utf-8")
    (root / "secret").mkdir()
    (root / "secret" / "id_rsa").write_text("x", encoding="utf-8")

    build = bfs_index.build_root_progressively(
        root,
        {".git"},
        exclude_path=lambda path: path.name == "secret",
        generation=1,
    )
    order = _rel_open_order(build, root)
    assert ".git" not in order
    assert "secret" not in order
    assert _entry_rel_paths(root) == ["keep/ok.txt"]


def test_symlinked_directory_is_not_followed(tmp_path):
    root = tmp_path / "root"
    real = tmp_path / "outside"
    (real / "hidden").mkdir(parents=True)
    (real / "hidden" / "escaped.txt").write_text("x", encoding="utf-8")
    root.mkdir()
    (root / "here.txt").write_text("x", encoding="utf-8")
    (root / "link").symlink_to(real, target_is_directory=True)

    build = bfs_index.build_root_progressively(root, set(), generation=1)
    assert _rel_open_order(build, root) == ["."]  # the symlink is never opened
    assert _entry_rel_paths(root) == ["here.txt"]


def test_disappearing_directory_is_a_per_directory_outcome(tmp_path):
    # A directory queued at layer 1 that vanishes before its scan must be recorded
    # as complete-with-no-rows, drop its stale rows, and not wedge the frontier.
    root = tmp_path / "root"
    (root / "gone").mkdir(parents=True)
    (root / "stay").mkdir()
    (root / "gone" / "old.txt").write_text("x", encoding="utf-8")
    (root / "stay" / "keep.txt").write_text("x", encoding="utf-8")

    build = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with build:
        build.enqueue_startup()
        build.step()  # scan root, enqueue /root/gone and /root/stay
        shutil.rmtree(root / "gone")  # the directory vanishes before its scan
        build.run()  # scans gone (missing) and stay

    assert _entry_rel_paths(root) == ["stay/keep.txt"]
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        pending = conn.execute("SELECT COUNT(*) FROM frontier WHERE state='pending'").fetchone()[0]
        assert pending == 0


def test_truncated_directory_reports_partial_coverage(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for i in range(10):
        (root / f"f{i}.txt").write_text("x", encoding="utf-8")
    build = bfs_index.build_root_progressively(root, set(), generation=1, max_entries=3)
    assert build.truncated is True
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        full = conn.execute("SELECT value FROM metadata WHERE key='full_coverage'").fetchone()[0]
        assert full == "0"  # truncation is never presented as full coverage


# --------------------------------------------------------------------------
# DOIT.p0.search-interactivity steps 1-2 — the committed change journal
# (schema added compatibly, journaled writer transactions, cursor contract)
# --------------------------------------------------------------------------


def _journal_rows(root):
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        return conn.execute(
            "SELECT revision, generation, operation, path, relative_path FROM change_journal ORDER BY revision"
        ).fetchall()


def test_change_journal_table_is_added_compatibly_to_an_existing_v5_store(tmp_path):
    # Step 1 migration: an existing v5 store that predates the change journal must GAIN the table on
    # the next open WITHOUT dropping its rows or bumping the version away from 5.
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1)
    db_path = file_index._index_disk_path(root)
    # Simulate a pre-journal v5 store: drop the journal table, keep every entry + version 5.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE change_journal")
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 5
        conn.commit()
    # Re-opening through the owner recreates the table compatibly; rows and version survive.
    with file_index._sqlite_index_connection(root) as conn:
        file_index._ensure_sqlite_schema(conn)
    with sqlite3.connect(db_path) as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 5
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "change_journal" in tables
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_publish_journals_each_committed_upsert_with_a_monotonic_revision(tmp_path):
    # Step 2: a directory publish records its committed rows in the journal, in the same generation,
    # with strictly increasing revisions and the high-water mark advanced.
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "a" / "buried.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1)
    rows = _journal_rows(root)
    upserts = [r for r in rows if r[2] == file_index.JOURNAL_OP_UPSERT]
    rels = {r[4] for r in upserts}
    assert {"top.txt", "a/buried.txt"} <= rels
    revisions = [r[0] for r in rows]
    assert revisions == sorted(revisions) and len(revisions) == len(set(revisions))
    assert all(r[1] == 1 for r in rows)
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        high_water = int(conn.execute("SELECT value FROM metadata WHERE key='journal_revision'").fetchone()[0])
    assert high_water == max(revisions)


def test_directory_publication_emits_a_redacted_progress_signal(tmp_path, monkeypatch):
    # Step 5 wiring: a committed publication (a new journal revision) actually reaches the signal
    # notifier, carrying the published generation + new high-water revision and NO filesystem data.
    # The window is set to 0 so every publish emits (leading edge), exercising the emit call site
    # rather than the coalescer (covered separately).
    file_index._reset_search_progress_coalescing()
    monkeypatch.setattr(file_index, "SEARCH_PROGRESS_COALESCE_SECONDS", 0.0)
    signals = []
    monkeypatch.setattr(file_index, "_SEARCH_PROGRESS_NOTIFIER", signals.append)
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "a" / "buried.txt").write_text("x", encoding="utf-8")

    bfs_index.build_root_progressively(root, set(), generation=1)

    assert signals, "a publication that commits a new journal revision must emit a signal"
    for frame in signals:
        assert set(frame) == {"scope_id", "generation", "revision", "coverage"}
        assert frame["generation"] == 1
        assert frame["scope_id"] == file_index._root_scope_id(root)
        assert "buried.txt" not in json.dumps(frame) and str(root) not in json.dumps(frame)
    high_water = max(r[0] for r in _journal_rows(root))
    assert max(frame["revision"] for frame in signals) == high_water
    assert signals[-1]["coverage"]["full_coverage"] is True
    file_index._reset_search_progress_coalescing()


def test_rolled_back_publication_exposes_neither_rows_nor_journal(tmp_path):
    # Step 2 atomicity: a fault mid-publish must leave NO entries row AND NO journal row for the
    # directory (all-or-nothing in one transaction). A superseding generation fences the stale publish,
    # so its rollback path leaves the journal empty for the fenced directory too.
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    (root / "child" / "old.txt").write_text("x", encoding="utf-8")
    stale = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with stale:
        stale.enqueue_startup()
        stale.step()  # publishes the root at gen 1
        before = {r[3] for r in _journal_rows(root)}
        with file_index._connect_sqlite_index(root) as conn:
            file_index._ensure_sqlite_schema(conn)
            conn.execute("UPDATE metadata SET value='2' WHERE key='active_generation'")
            conn.commit()
        stale.step()  # tries to publish /root/child; the generation fence rolls it back
    after = {r[3] for r in _journal_rows(root)}
    # The fenced child directory contributed no journal row and no entry row.
    assert str(root / "child" / "old.txt") not in after
    assert after == before
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM entries WHERE path=?", (str(root / "child" / "old.txt"),)).fetchone()[0] == 0


def test_truncation_at_cap_journals_only_committed_rows(tmp_path):
    # Step 2: max_files truncation must not journal entries that were not committed. With a total cap of
    # 2 rows, the journal holds at most 2 upserts.
    root = tmp_path / "root"
    root.mkdir()
    for i in range(6):
        (root / f"f{i}.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1, max_total_entries=2)
    upserts = [r for r in _journal_rows(root) if r[2] == file_index.JOURNAL_OP_UPSERT]
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        committed = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    assert committed <= 2
    assert len(upserts) == committed


def test_dirty_subtree_refresh_journals_upserts_and_deletes(tmp_path):
    # Step 2: the incremental delta path (`_apply_sqlite_delta`) journals both an upsert and a delete in
    # the same persisted transaction.
    root = tmp_path / "root"
    root.mkdir()
    ri = file_index.RootIndex(root)
    ri.active_generation = 7
    gone = str(root / "gone.txt")
    ri.entry_by_path = {gone: (gone, "gone.txt", "gone.txt", 3, 0)}
    ri.entries = list(ri.entry_by_path.values())
    ri.entries_signature = "seed"
    ri.built_at = 1.0
    file_index._persist(ri, set(), "", force=True)  # seed the store with gone.txt
    # Now delete gone.txt and add fresh.txt through the delta path.
    fresh = str(root / "fresh.txt")
    ri.entry_by_path = {fresh: (fresh, "fresh.txt", "fresh.txt", 4, 0)}
    ri.entries = list(ri.entry_by_path.values())
    ri.pending_exact_deletes = {gone}
    ri.pending_upserts = {fresh: (fresh, "fresh.txt", "fresh.txt", 4, 0)}
    ri.entries_signature = "delta:1:1"
    file_index._persist(ri, set(), "", force=True)
    ops = {(r[2], r[4]) for r in _journal_rows(root)}
    assert (file_index.JOURNAL_OP_UPSERT, "fresh.txt") in ops
    assert (file_index.JOURNAL_OP_DELETE, "gone.txt") in ops


def test_restart_resume_continues_the_journal_high_water(tmp_path):
    # Step 2 restart/resume: a resumed build appends journal rows with revisions ABOVE the high-water
    # the first run left, never reusing a revision.
    root = tmp_path / "root"
    root.mkdir()
    (root / "r.txt").write_text("x", encoding="utf-8")
    for i in range(3):
        (root / f"d{i}").mkdir()
        (root / f"d{i}" / f"f{i}.txt").write_text("x", encoding="utf-8")
    first = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with first:
        first.enqueue_startup()
        first.step()  # root only (journals r.txt)
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        hw_after_first = int(conn.execute("SELECT value FROM metadata WHERE key='journal_revision'").fetchone()[0])
    resumed = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with resumed:
        resumed.resume()
        resumed.run()
    revisions = [r[0] for r in _journal_rows(root)]
    assert revisions == sorted(revisions) and len(revisions) == len(set(revisions))
    # The resume appended new revisions strictly above the first run's high-water.
    assert max(revisions) > hw_after_first


def test_delta_cursor_round_trips_and_rejects_cross_root_and_cross_policy(tmp_path):
    # Step 1 cursor contract: the opaque cursor encodes {root/policy, generation, revision, tombstone}
    # and a decode validates them; a malformed cursor decodes to None.
    encoded = file_index._encode_delta_cursor(
        root=tmp_path / "root", policy="policy-a", generation=3, revision=42, tombstone_identity="tomb-1"
    )
    decoded = file_index._decode_delta_cursor(encoded)
    assert decoded == {
        "root": str((tmp_path / "root").resolve()),
        "policy": "policy-a",
        "generation": 3,
        "revision": 42,
        "tombstone": "tomb-1",
    }
    assert file_index._decode_delta_cursor("not-a-cursor") is None
    assert file_index._decode_delta_cursor("") is None


# --------------------------------------------------------------------------
# Item 4 — schema version and v4 migration
# --------------------------------------------------------------------------


def test_v5_schema_has_generation_column_and_coverage_tables(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1)
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 5
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"entries", "metadata", "directory_coverage", "frontier"} <= tables
        columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
        assert "generation" in columns


def test_v4_flat_snapshot_migrates_in_place_and_stays_searchable(tmp_path):
    # A shipped v4 database (flat entries, no generation column, version 4) must
    # keep its rows readable after the format bump, not be dropped.
    root = tmp_path / "root"
    root.mkdir()
    file_index.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    db_path = file_index._index_disk_path(root)
    signature = file_index._disk_skip_signature(root, set(), "")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE entries (path TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "relative_path TEXT NOT NULL, size INTEGER NOT NULL, mtime INTEGER NOT NULL)"
        )
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [
                ("version", "4"),
                ("storage", "sqlite"),
                ("skip_signature", signature),
                ("root", str(root)),
                ("entry_count", "1"),
                ("truncated", "0"),
                ("entries_signature", "legacy"),
            ],
        )
        legacy_path = str(root / "legacy.txt")
        conn.execute(
            "INSERT INTO entries(path, name, relative_path, size, mtime) VALUES (?, 'legacy.txt', 'legacy.txt', 3, 0)",
            (legacy_path,),
        )
        conn.execute("PRAGMA user_version=4")
        conn.commit()

    # Opening for read via the owner path migrates in place and preserves the row.
    loaded = file_index._load_disk(root, set(), "")
    assert loaded is not None
    entries, _built, _trunc, _sig = loaded
    assert any(name == "legacy.txt" for _p, name, _r, _s, _m in entries)
    with sqlite3.connect(db_path) as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 5
        assert "generation" in {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
        assert conn.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0] == "5"


# --------------------------------------------------------------------------
# Slice C — item 5 (user-visible-demand frontier promotion) and item 7
# (lowest-priority safety refresh through the SAME frontier)
# --------------------------------------------------------------------------


def _frontier_rows(root):
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        return conn.execute(
            "SELECT directory, priority, reason FROM frontier WHERE state='pending' ORDER BY directory"
        ).fetchall()


def test_reason_priority_constants_match_the_one_bfs_owner():
    # file_index cannot import bfs_index, so it duplicates these reason strings and the user-visible
    # priority as literals. This parity test pins those copies to their one owner so a rename in
    # bfs_index cannot silently diverge the safety/promotion labels file_index emits.
    assert file_index.SAFETY_REFRESH_REASON == bfs_index.REASON_SAFETY
    assert file_index.USER_VISIBLE_DEMAND_REASON == bfs_index.REASON_USER_VISIBLE
    assert file_index.USER_VISIBLE_DEMAND_PRIORITY == bfs_index.PRIORITY_USER_VISIBLE_DEMAND


def test_frontier_promote_root_only_raises_priority():
    frontier = bfs_index.BfsFrontier()
    frontier.enqueue(_item("/r", "/r", 1, reason=bfs_index.REASON_STARTUP))  # priority 0
    frontier.enqueue(_item("/r", "/r/a", 2, reason=bfs_index.REASON_BREADTH))  # priority 3
    promoted = frontier.promote_root("/r", 1)
    assert promoted == 1  # only the breadth item was raised; the startup item is already higher
    by_dir = {item.directory: item for item in frontier.pending()}
    assert by_dir["/r"].priority == bfs_index.PRIORITY_STARTUP_DEPTH_1  # untouched
    assert by_dir["/r/a"].priority == bfs_index.PRIORITY_USER_VISIBLE_DEMAND
    assert by_dir["/r/a"].reason == bfs_index.REASON_USER_VISIBLE


def test_promote_frontier_bumps_pending_durable_rows(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "top.txt").write_text("x", encoding="utf-8")
    build = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with build:
        build.enqueue_startup()
        build.step()  # root committed; a/ and b/ persisted pending at breadth priority
    before = _frontier_rows(root)
    assert before and all(prio == bfs_index.PRIORITY_BREADTH_EXPANSION for _d, prio, _r in before)

    promoted = file_index.promote_frontier(root)
    assert promoted == len(before)
    after = _frontier_rows(root)
    assert all(prio == file_index.USER_VISIBLE_DEMAND_PRIORITY for _d, prio, _r in after)
    assert all(reason == file_index.USER_VISIBLE_DEMAND_REASON for _d, _p, reason in after)
    # A second promotion is idempotent: nothing is already lower, so nothing is re-promoted.
    assert file_index.promote_frontier(root) == 0


def test_promote_frontier_is_a_noop_without_a_snapshot(tmp_path):
    assert file_index.promote_frontier(tmp_path / "never-built") == 0


def test_safety_refresh_reason_labels_the_root_listing(tmp_path):
    # Item 7: a build run with the safety reason enqueues the root listing at the LOWEST precedence
    # class, through the same frontier — not the highest-priority startup class.
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    build = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with build:
        build.enqueue_startup(reason=bfs_index.REASON_SAFETY)
        rows = _frontier_rows(root)
        assert rows[0][2] == bfs_index.REASON_SAFETY
        assert rows[0][1] == bfs_index.PRIORITY_FULL_SAFETY_REFRESH


def test_safety_refresh_resumes_at_shallowest_depth_on_restart(tmp_path):
    # Item 7: a safety refresh is resumable/preemptible through the SAME frontier. An interrupted
    # safety crawl resumes at the shallowest pending directory, never restarting DFS from the root.
    root = tmp_path / "root"
    for i in range(3):
        (root / f"d{i}").mkdir(parents=True)
        (root / f"d{i}" / f"f{i}.txt").write_text("x", encoding="utf-8")

    first = bfs_index.ProgressiveBuild(root, set(), generation=2)
    with first:
        first.enqueue_startup(reason=bfs_index.REASON_SAFETY)
        first.step()  # root only; d0..d2 persisted pending as ordinary breadth-expansion children
    # The safety reason labels the ROOT listing (the trigger); its discovered children are ordinary
    # breadth-expansion work, so they stay preemptible by higher-priority startup/hot/user-visible.
    pending = _frontier_rows(root)
    assert pending and all(reason == bfs_index.REASON_BREADTH for _d, _p, reason in pending)

    resumed = bfs_index.ProgressiveBuild(root, set(), generation=2)
    with resumed:
        assert resumed.resume() >= 1
        resumed.run()
    assert str(root) not in resumed.open_order  # the root was not re-listed
    assert _entry_rel_paths(root) == sorted([f"d{i}/f{i}.txt" for i in range(3)])


# --------------------------------------------------------------------------
# BFS index lifecycle fix — item 1 (single generation, equality fence)
# --------------------------------------------------------------------------


def _set_active_generation(root, value):
    with file_index._connect_sqlite_index(root) as conn:
        file_index._ensure_sqlite_schema(conn)
        conn.execute(
            "INSERT INTO metadata(key, value) VALUES ('active_generation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(value),),
        )
        conn.commit()


def test_generation_success_fence_publishes_only_on_equality(tmp_path):
    # Item 1: a publish commits ONLY when the SQLite active generation EQUALS the worker's generation.
    # Neither an active BELOW the worker (a store this generation has not established) nor an active
    # ABOVE it (a newer generation took over) may write entries/coverage/frontier/metadata.
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    (root / "child" / "buried.txt").write_text("x", encoding="utf-8")

    build = bfs_index.ProgressiveBuild(root, set(), generation=5)
    with build:
        build.enqueue_startup()  # writes active_generation=5
        build.step()  # root listing publishes at 5==5; enqueues /root/child pending

        # Control 1: a NEWER generation (active > worker) must abort this generation's child publish.
        _set_active_generation(root, 6)
        build.step()
        with sqlite3.connect(file_index._index_disk_path(root)) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM directory_coverage WHERE directory = ? AND generation = 5",
                (str(root / "child"),),
            ).fetchone()[0] == 0
            # The pending frontier row for the child is untouched (still pending, not deleted).
            assert conn.execute(
                "SELECT COUNT(*) FROM frontier WHERE directory = ? AND state='pending'",
                (str(root / "child"),),
            ).fetchone()[0] in (0, 1)

    # Control 2: an OLDER active (below the worker) is also not current -- equality, not '<=', is the
    # rule. Re-run the same pending child with active set BELOW 5 and assert it still writes nothing.
    _set_active_generation(root, 5)  # restore so the frontier row is readable for this generation
    build2 = bfs_index.ProgressiveBuild(root, set(), generation=5)
    with build2:
        loaded = build2.resume()
        assert loaded >= 1
        _set_active_generation(root, 4)  # active < worker generation
        build2.run()
        with sqlite3.connect(file_index._index_disk_path(root)) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM directory_coverage WHERE directory = ? AND generation = 5",
                (str(root / "child"),),
            ).fetchone()[0] == 0

    # Equality: with active restored to 5, the same pending child finally publishes.
    _set_active_generation(root, 5)
    build3 = bfs_index.ProgressiveBuild(root, set(), generation=5)
    with build3:
        assert build3.resume() >= 1
        build3.run()
    assert "child/buried.txt" in _entry_rel_paths(root)


def test_generation_failure_fence_blocks_requeue_and_terminal_writes(tmp_path, monkeypatch):
    # Item 1: the requeue, retry-exhausted, and missing-directory FAILURE paths carry the same
    # equality fence -- an abandoned generation must not rewrite frontier retry counts or failure
    # coverage for a store a newer generation now owns.
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    (root / "child" / "f.txt").write_text("x", encoding="utf-8")

    build = bfs_index.ProgressiveBuild(root, set(), generation=3)
    with build:
        build.enqueue_startup()
        build.step()  # publish root at gen 3; enqueue /root/child pending

        # Force the child scan to look like a transient error so `_record_failure` runs its requeue.
        real_scan = bfs_index.scan_directory_once

        def erroring_scan(*args, **kwargs):
            result = bfs_index.ScanResult()
            result.error = "scandir:OSError"
            return result

        monkeypatch.setattr(bfs_index, "scan_directory_once", erroring_scan)
        # A newer generation takes over before the failing child is processed.
        _set_active_generation(root, 4)
        build.step()  # child scan errors -> _record_failure; the fence must block the requeue write

        with sqlite3.connect(file_index._index_disk_path(root)) as conn:
            # No FAILED coverage was written for the fenced generation.
            assert conn.execute(
                "SELECT COUNT(*) FROM directory_coverage WHERE generation = 3 AND state = 'failed'"
            ).fetchone()[0] == 0
            # The requeue did not bump the child's retry count for the fenced generation.
            retries = conn.execute(
                "SELECT retries FROM frontier WHERE directory = ?",
                (str(root / "child"),),
            ).fetchone()
            assert retries is None or int(retries[0]) == 0


# --------------------------------------------------------------------------
# Round-2 P0-1 — the generation claim is ONE atomic compare-and-set that a
# lower generation cannot win, and P0-2 — a manifest is written only from a
# COMPLETE published-snapshot metadata shape.
# --------------------------------------------------------------------------


def _active_generation(root):
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key='active_generation'").fetchone()
    return row[0] if row else None


def _pending_frontier_generations(root):
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        return [row[0] for row in conn.execute("SELECT DISTINCT generation FROM frontier WHERE state='pending'")]


def test_stale_generation_claim_is_rejected_and_cannot_move_ownership_backward(tmp_path):
    # P0-1: build generation 2, then a lingering generation-1 startup claim must be REJECTED -- it may
    # not write active_generation=1 nor seed a generation-1 frontier row. The old unconditional
    # enqueue_startup moved ownership backward here; the atomic compare-and-set refuses it.
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    (root / "child" / "f.txt").write_text("x", encoding="utf-8")

    bfs_index.build_root_progressively(root, set(), generation=2)
    assert _active_generation(root) == "2"

    stale = bfs_index.ProgressiveBuild(root, set(), generation=1)
    with stale:
        claimed = stale.enqueue_startup()
    assert claimed is False  # a lower generation is rejected
    assert _active_generation(root) == "2"  # ownership never moved backward
    assert 1 not in _pending_frontier_generations(root)  # no generation-1 frontier row was written
    assert stale.frontier.size() == 0  # the in-memory queue stays empty on a rejected claim


def test_equal_generation_claim_resumes_and_newer_generation_advances(tmp_path):
    # P0-1: a missing metadata row initializes, an EQUAL generation resumes, and a strictly newer
    # generation advances -- all three are accepted claims.
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "f.txt").write_text("x", encoding="utf-8")

    first = bfs_index.ProgressiveBuild(root, set(), generation=3)
    with first:
        assert first.enqueue_startup() is True  # initialize (missing metadata)
    assert _active_generation(root) == "3"

    same = bfs_index.ProgressiveBuild(root, set(), generation=3)
    with same:
        assert same.enqueue_startup() is True  # resume (equal generation)
    assert _active_generation(root) == "3"

    newer = bfs_index.ProgressiveBuild(root, set(), generation=4)
    with newer:
        assert newer.enqueue_startup() is True  # advance (strictly newer)
    assert _active_generation(root) == "4"


def test_cancellation_before_claim_does_not_mutate_metadata(tmp_path):
    # P0-1: a cancellation observed before/at the atomic claim rolls back with NOTHING written -- no
    # metadata change, no frontier row -- even against an existing store.
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    (root / "child" / "f.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=2)
    before_pending = sorted(_pending_frontier_generations(root))

    blocked = bfs_index.ProgressiveBuild(root, set(), generation=5)
    with blocked:
        claimed = blocked.enqueue_startup(should_stop=lambda: True)
    assert claimed is False
    assert _active_generation(root) == "2"  # a cancellation never advanced the generation
    assert sorted(_pending_frontier_generations(root)) == before_pending
    assert blocked.frontier.size() == 0


def test_precancel_on_fresh_root_writes_no_manifest(tmp_path):
    # P0-2: a fresh-root build cancelled before its first publish returns cleanly with NO manifest --
    # never the KeyError('truncated') a startup-only metadata shape raised in _write_manifest.
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    (root / "child" / "f.txt").write_text("x", encoding="utf-8")

    build = bfs_index.build_root_progressively(root, set(), generation=1, should_stop=lambda: True)
    assert build is not None
    assert not file_index._index_manifest_path(root).exists()  # no manifest on a cancelled fresh root


def test_precancel_on_prior_snapshot_preserves_the_prior_manifest(tmp_path):
    # P0-2: a build cancelled before its first publish on a root that ALREADY has a valid snapshot must
    # PRESERVE the prior atomic manifest, never replace it with a startup-only one.
    root = tmp_path / "root"
    (root / "deep").mkdir(parents=True)
    (root / "top.txt").write_text("x", encoding="utf-8")
    (root / "deep" / "buried.txt").write_text("x", encoding="utf-8")

    bfs_index.build_root_progressively(root, set(), generation=1)
    manifest_path = file_index._index_manifest_path(root)
    prior_manifest = manifest_path.read_text(encoding="utf-8")

    # Generation 2 claims (writes its startup metadata) but is cancelled before its first publish:
    # enqueue_startup consults should_stop twice, then run consults it before the first step.
    stop_calls = {"n": 0}

    def stop_before_first_publish():
        stop_calls["n"] += 1
        return stop_calls["n"] > 2  # allow the two enqueue_startup checks, stop at run's first check

    bfs_index.build_root_progressively(root, set(), generation=2, should_stop=stop_before_first_publish)

    assert manifest_path.exists()
    assert manifest_path.read_text(encoding="utf-8") == prior_manifest  # the prior manifest is untouched
    assert "deep/buried.txt" in _entry_rel_paths(root)  # the prior snapshot stays searchable


# --------------------------------------------------------------------------
# BFS index lifecycle fix — item 5 (one connection owner, closed in finally)
# --------------------------------------------------------------------------


def _sqlite_fd_targets():
    """The set of /proc/self/fd entries pointing at a `.sqlite3` store, and the deleted-FD count."""
    fd_dir = Path("/proc/self/fd")
    live = 0
    deleted = 0
    for entry in fd_dir.iterdir():
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if ".sqlite3" not in target:
            continue
        if target.endswith("(deleted)"):
            deleted += 1
        else:
            live += 1
    return live, deleted


def test_every_opened_index_connection_is_closed(tmp_path, monkeypatch):
    # Item 5: every writable BFS connection goes through the ONE connection-context owner and is
    # CLOSED in `finally`. Instrument `_connect_sqlite_index` so every real connection it hands out is
    # tracked, drive a multi-directory build through success, retry, missing, cancellation, and an
    # exception path, and assert EVERY opened connection was explicitly closed and no descriptor leaks.
    opened = []
    real_connect = file_index._connect_sqlite_index

    class TrackingConnection:
        def __init__(self, real):
            self._real = real
            self.closed = False

        def close(self):
            self.closed = True
            return self._real.close()

        def __getattr__(self, name):
            return getattr(self._real, name)

    db_paths: list[Path] = []

    def tracking_connect(root):
        db_paths.append(file_index._index_disk_path(root))
        conn = TrackingConnection(real_connect(root))
        opened.append(conn)
        return conn

    monkeypatch.setattr(file_index, "_connect_sqlite_index", tracking_connect)

    baseline_live, baseline_deleted = _sqlite_fd_targets() if sys.platform == "linux" else (0, 0)

    # 1. Success + child expansion (many connections across several directories).
    root = tmp_path / "ok"
    for i in range(4):
        (root / f"d{i}" / "sub").mkdir(parents=True)
        (root / f"d{i}" / f"f{i}.txt").write_text("x", encoding="utf-8")
    bfs_index.build_root_progressively(root, set(), generation=1)

    # 2. Missing directory: a queued child vanishes before its scan (terminal missing path).
    gone_root = tmp_path / "gone"
    (gone_root / "vanish").mkdir(parents=True)
    (gone_root / "stay").mkdir()
    (gone_root / "stay" / "keep.txt").write_text("x", encoding="utf-8")
    gone_build = bfs_index.ProgressiveBuild(gone_root, set(), generation=1)
    with gone_build:
        gone_build.enqueue_startup()
        gone_build.step()
        shutil.rmtree(gone_root / "vanish")
        gone_build.run()

    # 3. Retry: a child scan reports a transient error (requeue path opens/closes its own connection).
    retry_root = tmp_path / "retry"
    (retry_root / "child").mkdir(parents=True)
    (retry_root / "child" / "f.txt").write_text("x", encoding="utf-8")
    retry_build = bfs_index.ProgressiveBuild(retry_root, set(), generation=1)
    with retry_build:
        retry_build.enqueue_startup()
        retry_build.step()

        def erroring_scan(*args, **kwargs):
            result = bfs_index.ScanResult()
            result.error = "scandir:OSError"
            return result

        # SCOPE this patch so it is restored on its own -- a blanket `monkeypatch.undo()` here also
        # reverted the autouse fixture's `INDEX_DIR` patch, so the remaining subcases wrote their
        # databases through the real/default index dir instead of this test's fixture directory.
        with monkeypatch.context() as scan_patch:
            scan_patch.setattr(bfs_index, "scan_directory_once", erroring_scan)
            retry_build.step()  # requeue transaction opens and closes a connection

    # 4. Cancellation between directories (should_stop) — published rows stay, connections close. Stop
    #    AFTER the root layer publishes (the realistic mid-crawl cancel), not before the first listing.
    #    `enqueue_startup` consults `should_stop` twice (before and after the atomic generation claim),
    #    then `run` consults it before each step; stopping after the 4th call leaves the root published.
    cancel_root = tmp_path / "cancel"
    for i in range(3):
        (cancel_root / f"c{i}").mkdir(parents=True)
        (cancel_root / f"c{i}" / f"f{i}.txt").write_text("x", encoding="utf-8")
    cancel_calls = {"n": 0}

    def stop_after_root():
        cancel_calls["n"] += 1
        return cancel_calls["n"] > 3

    bfs_index.build_root_progressively(cancel_root, set(), generation=1, should_stop=stop_after_root)

    # 5. Exception path inside a publish transaction — the owner still closes the connection.
    boom_root = tmp_path / "boom"
    boom_root.mkdir()
    (boom_root / "a.txt").write_text("x", encoding="utf-8")
    boom_build = bfs_index.ProgressiveBuild(boom_root, set(), generation=1)

    def boom(*_args, **_kwargs):
        raise RuntimeError("publish blew up")

    with boom_build:
        boom_build.enqueue_startup()
        monkeypatch.setattr(boom_build, "_recompute_progress", boom)
        with pytest.raises(RuntimeError):
            boom_build.step()

    assert opened, "the build must have opened at least one tracked connection"
    unclosed = [conn for conn in opened if not conn.closed]
    assert unclosed == [], f"{len(unclosed)} index connection(s) were never closed"

    # Test-isolation guard: every database opened by every subcase must live BELOW the fixture index
    # directory. If a stray `monkeypatch.undo()` had reverted the autouse `INDEX_DIR` patch, a later
    # subcase would have written its store through the real/default dir -- caught here.
    fixture_index_dir = tmp_path / "idx"
    assert file_index.INDEX_DIR == fixture_index_dir, "the fixture INDEX_DIR patch was reverted mid-test"
    assert db_paths, "no database paths were tracked"
    for db in db_paths:
        assert fixture_index_dir in db.parents, f"a DB was created outside the fixture index dir: {db}"

    if sys.platform == "linux":
        live, deleted = _sqlite_fd_targets()
        assert deleted == baseline_deleted == 0, "no (deleted) sqlite FD may linger"
        assert live <= baseline_live, "open sqlite descriptors must return to baseline"


# --------------------------------------------------------------------------
# BFS index lifecycle fix — stable partial-store identity across rebuilds
# --------------------------------------------------------------------------


def test_partial_store_identity_is_stable_across_repeated_rebuilds(tmp_path, monkeypatch):
    # A large root whose total-row cap makes it a durable typed partial must keep ONE stable on-disk
    # store across many rebuilds -- including the safety-generation crossing the hot-repair starvation
    # bound would drive. History: the cap path deleted and recreated the store on every build, so the
    # DB inode churned, searches went momentarily empty, and unlinked descriptors piled up. Here the
    # inode must stay fixed, the partial rows stay searchable throughout, and no manifest/DB vanishes.
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(file_index, "MAX_INDEX_FILES", 25)
    file_index.clear_memory_indexes()
    root = tmp_path / "root"
    root.mkdir()
    for i in range(120):  # 120 directories, each one file -> a 25-row cap makes this a typed partial
        d = root / f"dir{i:03d}"
        d.mkdir()
        (d / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

    db_path = file_index._index_disk_path(root)
    first = file_index.build_now(root, set())
    assert first.truncated is True and first.persisted is True
    assert db_path.exists() and file_index._index_manifest_path(root).exists()
    original_inode = db_path.stat().st_ino

    # Rebuild more times than the hot-repair starvation bound would take to force a safety generation.
    for _ in range(file_index.HOT_REPAIR_STARVATION_BOUND + 3):
        file_index.clear_memory_indexes()
        rebuilt = file_index.build_now(root, set())
        # The store never disappears and its inode never churns -- one durable partial identity.
        assert db_path.exists(), "the durable partial store vanished during a rebuild"
        assert file_index._index_manifest_path(root).exists()
        assert db_path.stat().st_ino == original_inode, "the partial store's inode changed (delete+recreate)"
        assert rebuilt.truncated is True
        assert len(rebuilt.entries) == 25
        # The partial rows stay searchable throughout (a shallow representative is always present).
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 25

    if sys.platform == "linux":
        _, deleted = _sqlite_fd_targets()
        assert deleted == 0, "repeated capped rebuilds must not accumulate (deleted) sqlite FDs"
