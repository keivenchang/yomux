import sqlite3
import time

from yolomux_lib import file_index
from yolomux_lib import filesystem
from yolomux_lib.filesystem import SEARCH_SKIP_DIRS


def _clear_registry():
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY.clear()


def _make_tree(root):
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep_target.md").write_text("x", encoding="utf-8")
    (root / "top.txt").write_text("y", encoding="utf-8")
    skipped = root / "node_modules" / "pkg"
    skipped.mkdir(parents=True)
    (skipped / "ignored.js").write_text("z", encoding="utf-8")


def _build_filesystem_index(root):
    return file_index.build_now(
        root,
        SEARCH_SKIP_DIRS,
        exclude_path=filesystem._path_is_secret,
        exclude_signature=filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE,
    )


def test_walk_root_collects_files_and_skips_skip_dirs(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    entries, truncated = file_index.walk_root(tmp_path, SEARCH_SKIP_DIRS)
    names = {name for _path, name, _rel, _size, _mtime in entries}
    assert "deep_target.md" in names
    assert "top.txt" in names
    assert "ignored.js" not in names  # node_modules (a SEARCH_SKIP_DIRS member) is pruned
    assert truncated is False


def test_configured_excluded_subtree_is_absent_from_memory_and_sqlite(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    excluded = root / "generated-cache"
    excluded.mkdir()
    (excluded / "generated.py").write_text("generated\n", encoding="utf-8")
    (root / "source.py").write_text("source\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(filesystem.search, "settings_payload", lambda: {"settings": {"file_explorer": {"index_exclude_paths": [str(excluded)]}}})
    policy = filesystem.search._search_index_policy(root)

    built = file_index.build_now(
        root,
        SEARCH_SKIP_DIRS,
        exclude_path=policy["exclude_path"],
        exclude_signature=policy["exclude_signature"],
        max_files=policy["max_files"],
        persist_enabled=policy["persist_enabled"],
        persist_max_files=policy["persist_max_files"],
        persist_max_bytes=policy["persist_max_bytes"],
    )

    assert {entry[2] for entry in built.entries} == {"source.py"}
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        assert {row[0] for row in conn.execute("SELECT relative_path FROM entries")} == {"source.py"}


def test_configured_excluded_directory_names_are_absent_from_memory_and_sqlite(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    skipped = root / "skipme"
    skipped.mkdir()
    (skipped / "generated.py").write_text("generated\n", encoding="utf-8")
    (root / "source.py").write_text("source\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(filesystem.search, "settings_payload", lambda: {"settings": {"file_explorer": {"index_exclude_dir_names": ["skipme"]}}})
    policy = filesystem.search._search_index_policy(root)

    built = file_index.build_now(
        root,
        policy["skip_dirs"],
        exclude_path=policy["exclude_path"],
        exclude_signature=policy["exclude_signature"],
        max_files=policy["max_files"],
        persist_enabled=policy["persist_enabled"],
        persist_max_files=policy["persist_max_files"],
        persist_max_bytes=policy["persist_max_bytes"],
    )

    assert policy["skip_dirs"] == {"skipme"}
    assert {entry[2] for entry in built.entries} == {"source.py"}
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        assert {row[0] for row in conn.execute("SELECT relative_path FROM entries")} == {"source.py"}


def test_index_storage_inside_root_is_never_indexed(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "source.py").write_text("source\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", root / "runtime-index")

    built = file_index.build_now(root, SEARCH_SKIP_DIRS)

    assert {entry[2] for entry in built.entries} == {"source.py"}
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        assert {row[0] for row in conn.execute("SELECT relative_path FROM entries")} == {"source.py"}
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
    assert metadata["skip_signature"].endswith("|internal-index-dir:runtime-index")


def test_configured_glob_and_regex_exclusions_match_root_relative_paths(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    uploads = root / "project" / ".uploads"
    target = root / "project" / "target"
    uploads.mkdir(parents=True)
    target.mkdir()
    (uploads / "capture.png").write_text("image\n", encoding="utf-8")
    (target / "generated.rs").write_text("generated\n", encoding="utf-8")
    (root / "project" / "source.rs").write_text("source\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(filesystem.search, "settings_payload", lambda: {"settings": {"file_explorer": {
        "index_exclude_paths": ["glob:**/.uploads/**", r"regex:(^|/)target(?:/|$)", "regex:("],
    }}})
    policy = filesystem.search._search_index_policy(root)

    assert policy["exclude_path"](uploads) is True
    assert policy["exclude_path"](target) is True
    assert policy["exclude_path"](root / "project" / "source.rs") is False

    built = file_index.build_now(
        root,
        SEARCH_SKIP_DIRS,
        exclude_path=policy["exclude_path"],
        exclude_signature=policy["exclude_signature"],
    )

    assert {entry[2] for entry in built.entries} == {"project/source.rs"}
    assert policy["excluded_paths"] == ["glob:**/.uploads/**", "regex:(^|/)target(?:/|$)"]
    assert policy["exclude_signature"].startswith("fs-secret-v2:")


def test_index_safety_refresh_uses_a_thirty_minute_interval():
    assert file_index.INDEX_TTL_SECONDS == 30.0 * 60.0


def test_walk_root_skips_symlinked_files_and_dirs(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "target.txt").write_text("real\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
    (tmp_path / "link-dir").symlink_to(outside, target_is_directory=True)
    (tmp_path / "link-file.txt").symlink_to(real_dir / "target.txt")

    entries, truncated = file_index.walk_root(tmp_path, SEARCH_SKIP_DIRS)
    relative_paths = {rel for _path, _name, rel, _size, _mtime in entries}

    assert relative_paths == {"real/target.txt", "outside/outside.txt"}
    assert truncated is False


def test_build_persists_to_disk_and_reloads(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    built = file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    assert built.ready and built.entries
    assert file_index._index_disk_path(tmp_path).exists()
    # Drop the in-memory registry: ensure_index should seed (stale) from disk and be ready immediately.
    _clear_registry()
    reloaded = file_index.ensure_index(tmp_path, SEARCH_SKIP_DIRS)
    assert reloaded.ready
    assert any(name == "deep_target.md" for _p, name, _r, _s, _m in reloaded.entries)


def test_build_persists_sqlite_without_large_json_payload(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    built = file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    db_path = file_index._index_disk_path(tmp_path)

    assert built.ready is True
    assert db_path.suffix == ".sqlite3"
    assert db_path.exists()
    assert not file_index._legacy_index_json_path(tmp_path).exists()
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    assert count >= 2


def test_oversized_index_is_partial_in_memory_and_not_persisted_across_restart(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    monkeypatch.setattr(file_index, "MAX_INDEX_FILES", 2)
    for name in ("one.txt", "two.txt", "three.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    built = file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)

    assert built.ready is True
    assert built.truncated is True
    assert built.too_large is True
    assert len(built.entries) == 2
    assert built.persisted is False
    assert not file_index._index_disk_path(tmp_path).exists()
    _clear_registry()
    # A restart begins an asynchronous cold build. Hold that build here so this
    # persistence contract cannot race a three-file tree and turn ready before
    # the assertion observes the new RootIndex.
    monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: None)
    reloaded = file_index.ensure_index(tmp_path, SEARCH_SKIP_DIRS)
    assert reloaded.ready is False


def test_persistence_can_be_disabled_or_rejected_by_file_budget(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.txt").write_text("two", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")

    disabled = file_index.build_now(root, SEARCH_SKIP_DIRS, persist_enabled=False)
    assert disabled.ready is True
    assert disabled.persisted is False
    assert not file_index._index_disk_path(root).exists()

    _clear_registry()
    capped = file_index.build_now(root, SEARCH_SKIP_DIRS, persist_max_files=1)
    assert capped.ready is True
    assert capped.persisted is False
    assert not file_index._index_disk_path(root).exists()

    _clear_registry()
    byte_capped = file_index.build_now(root, SEARCH_SKIP_DIRS, persist_max_bytes=1)
    assert byte_capped.ready is True
    assert byte_capped.persisted is False
    assert not file_index._index_disk_path(root).exists()


def test_incremental_refresh_replaces_only_dirty_subtree_and_debounces_persistence(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    left = root / "left"
    right = root / "right"
    left.mkdir(parents=True)
    right.mkdir()
    (left / "old.txt").write_text("old", encoding="utf-8")
    (right / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS)
    initial_full_builds = index.full_build_count
    initial_writes = index.write_bytes

    (left / "old.txt").unlink()
    (left / "new.txt").write_text("new", encoding="utf-8")
    file_index.mark_path_dirty(left)
    file_index._run_build(index, SEARCH_SKIP_DIRS)

    assert {entry[2] for entry in index.entries} == {"left/new.txt", "right/keep.txt"}
    assert index.full_build_count == initial_full_builds
    assert index.incremental_build_count == 1
    assert index.persist_pending is True
    assert index.write_bytes == initial_writes

    index.last_persisted_at = time.monotonic() - file_index.PERSIST_DEBOUNCE_SECONDS - 1
    assert file_index.schedule_refreshes() == 0
    assert index.persist_pending is False
    assert index.write_bytes > initial_writes
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        assert {row[0] for row in conn.execute("SELECT relative_path FROM entries")} == {"left/new.txt", "right/keep.txt"}


def test_single_file_save_is_a_row_delta_not_a_full_index_rewrite(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    changed = root / "changed.txt"
    changed.write_text("before", encoding="utf-8")
    (root / "other.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS)

    def fail_full_rewrite(*_args, **_kwargs):
        raise AssertionError("a normal file save must not replace every SQLite row")

    def fail_full_hash(*_args, **_kwargs):
        raise AssertionError("a normal file save must not hash every indexed row")

    monkeypatch.setattr(file_index, "_replace_sqlite_entries", fail_full_rewrite)
    monkeypatch.setattr(file_index, "_entries_signature", fail_full_hash)
    changed.write_text("after-with-a-different-size", encoding="utf-8")
    file_index.mark_path_dirty(changed)
    file_index._run_build(index, SEARCH_SKIP_DIRS)

    assert index.persist_pending is True
    index.last_persisted_at = time.monotonic() - file_index.PERSIST_DEBOUNCE_SECONDS - 1
    assert file_index.schedule_refreshes() == 0
    with sqlite3.connect(file_index._index_disk_path(root)) as conn:
        assert conn.execute("SELECT size FROM entries WHERE path = ?", (str(changed),)).fetchone() == (len("after-with-a-different-size"),)


def test_root_notification_does_not_schedule_an_unbounded_incremental_rewalk(tmp_path, monkeypatch):
    _clear_registry()
    root = tmp_path / "root"
    root.mkdir()
    (root / "source.py").write_text("source\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS, persist_enabled=False)

    assert file_index.mark_path_dirty(root) == []
    assert index.dirty_paths == set()


def test_incremental_refresh_ignores_skipped_descendant_without_scanning_retained_entries(tmp_path, monkeypatch):
    _clear_registry()
    root = (tmp_path / "root").resolve()
    root.mkdir()
    (root / "source.py").write_text("source\n", encoding="utf-8")
    git_dir = root / ".git"
    git_dir.mkdir()
    fetch_head = git_dir / "FETCH_HEAD"
    fetch_head.write_text("first\n", encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    index = file_index.build_now(root, SEARCH_SKIP_DIRS, persist_enabled=False)

    fetch_head.write_text("second\n", encoding="utf-8")
    file_index.mark_path_dirty(fetch_head)
    monkeypatch.setattr(
        file_index,
        "_path_is_within",
        lambda *_args: (_ for _ in ()).throw(AssertionError("excluded descendants must not filter retained entries")),
    )
    file_index._run_build(index, SEARCH_SKIP_DIRS)

    assert [entry[2] for entry in index.entries] == ["source.py"]
    assert index.incremental_build_count == 1
    assert index.scanned_entries == 0
    assert index.ignored_entries == 1


def test_disk_index_candidate_prefilter_preserves_punctuation_free_fuzzy_filename_queries(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    (tmp_path / "2026.md").write_text("notes\n", encoding="utf-8")
    _build_filesystem_index(tmp_path)

    opened = file_index._read_sqlite_index(tmp_path, SEARCH_SKIP_DIRS, filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE)
    assert opened is not None
    conn, _metadata = opened
    try:
        names = [row[1] for row in file_index._sqlite_search_candidates(conn, ["2026md"])]
    finally:
        conn.close()

    assert names == ["2026.md"]


def test_unchanged_search_index_refresh_skips_entry_rewrite(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    built = file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    rewrite_calls = []

    def fail_entry_rewrite(*_args, **_kwargs):
        rewrite_calls.append(True)
        raise AssertionError("unchanged search-index refresh must not rewrite entries")

    monkeypatch.setattr(file_index, "_replace_sqlite_entries", fail_entry_rewrite)
    file_index._persist(built, SEARCH_SKIP_DIRS)

    assert rewrite_calls == []


def test_ensure_index_does_not_rebuild_before_refresh_interval(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    built = file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    starts = []
    monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: starts.append(True))
    monkeypatch.setattr(file_index.time, "time", lambda: built.built_at + file_index.INDEX_TTL_SECONDS - 1)

    refreshed = file_index.ensure_index(tmp_path, SEARCH_SKIP_DIRS)

    assert refreshed.ready is True
    assert starts == []


def test_search_files_uses_index_to_find_deep_file(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    _build_filesystem_index(tmp_path)
    payload = filesystem.search_files(str(tmp_path), query="deep_target", limit=50, recursive=True)
    relative_paths = [entry["relative_path"] for entry in payload["files"]]
    assert "a/b/c/deep_target.md" in relative_paths


def test_index_status_warms_and_reports_state(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    _build_filesystem_index(tmp_path)
    status = filesystem.index_status(str(tmp_path))
    assert status["root"] == str(tmp_path)
    assert status["ready"] is True
    assert status["count"] >= 2  # deep_target.md + top.txt (node_modules skipped)


def test_load_disk_rejects_stale_format_or_skip_signature(tmp_path, monkeypatch):
    # C11: an index built with a different format version or skip-dir set must NOT be reused — it rebuilds.
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    # Same skip set + version -> loads.
    assert file_index._load_disk(tmp_path, SEARCH_SKIP_DIRS) is not None
    # Different skip set -> rejected (signature mismatch).
    assert file_index._load_disk(tmp_path, SEARCH_SKIP_DIRS | {"some_other_dir"}) is None
    # Bumped format version -> rejected.
    monkeypatch.setattr(file_index, "INDEX_FORMAT_VERSION", file_index.INDEX_FORMAT_VERSION + 1)
    assert file_index._load_disk(tmp_path, SEARCH_SKIP_DIRS) is None


def test_inmemory_index_rebuilds_when_exclude_signature_changes(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    (tmp_path / "visible.txt").write_text("ok", encoding="utf-8")
    secret = tmp_path / ".ssh" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("secret", encoding="utf-8")
    skip_dirs = SEARCH_SKIP_DIRS - {".ssh"}
    initial = file_index.build_now(tmp_path, skip_dirs)
    assert any(name == "id_rsa" for _p, name, _r, _s, _m in initial.entries)

    filtered = file_index.build_now(
        tmp_path,
        skip_dirs,
        exclude_path=lambda path: ".ssh" in path.parts,
        exclude_signature="test-secret-filter",
    )

    assert filtered.signature.endswith("|exclude:test-secret-filter")
    assert any(name == "visible.txt" for _p, name, _r, _s, _m in filtered.entries)
    assert all(name != "id_rsa" for _p, name, _r, _s, _m in filtered.entries)


def test_ensure_index_drops_ready_cache_when_exclude_signature_changes(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    (tmp_path / "visible.txt").write_text("ok", encoding="utf-8")
    secret = tmp_path / ".ssh" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("secret", encoding="utf-8")
    skip_dirs = SEARCH_SKIP_DIRS - {".ssh"}
    initial = file_index.build_now(tmp_path, skip_dirs)
    assert initial.ready is True
    assert any(name == "id_rsa" for _p, name, _r, _s, _m in initial.entries)
    monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: None)

    filtered = file_index.ensure_index(
        tmp_path,
        skip_dirs,
        exclude_path=lambda path: ".ssh" in path.parts,
        exclude_signature="test-secret-filter",
    )

    assert filtered.ready is False
    assert filtered.entries == []


def test_empty_query_serves_recent_from_index_without_cold_walk(tmp_path, monkeypatch):
    # C11: an empty query on a ready full-tree index returns a capped recent slice (state "ready") instead
    # of cold-walking the tree.
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    _build_filesystem_index(tmp_path)
    payload = filesystem.search_files(str(tmp_path), query="", limit=50, recursive=True)
    assert payload["index_state"] == "ready"
    names = {entry["name"] for entry in payload["files"]}
    assert "deep_target.md" in names and "top.txt" in names
    assert "ignored.js" not in names

    # Guard the cold-walk path is not taken for an empty query: force the walk to explode if reached.
    def _boom(*_args, **_kwargs):
        raise AssertionError("empty-query search must not cold-walk a ready index")
    monkeypatch.setattr(filesystem, "_search_full_tree", _boom)
    payload2 = filesystem.search_files(str(tmp_path), query="", limit=50, recursive=True)
    assert payload2["index_state"] == "ready"


def test_empty_query_returns_warming_when_index_not_ready(tmp_path, monkeypatch):
    # C11: empty query on a not-yet-ready index returns warming + no files (no cold walk).
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)

    class _NotReady:
        ready = False
        building = True
    monkeypatch.setattr(file_index, "ensure_index", lambda *args, **kwargs: _NotReady())

    def _boom(*_args, **_kwargs):
        raise AssertionError("warming index must not cold-walk")
    monkeypatch.setattr(filesystem, "_search_full_tree", _boom)
    payload = filesystem.search_files(str(tmp_path), query="", limit=50, recursive=True)
    assert payload["index_state"] == "warming"
    assert payload["files"] == []


def test_index_status_reports_rich_state(tmp_path, monkeypatch):
    # C11 #1: the status endpoint reports the real state (ready/building/missing) + built_at/age/truncated
    # so the Finder badge shows the truth instead of guessing.
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    _build_filesystem_index(tmp_path)
    status = filesystem.index_status(str(tmp_path))
    assert status["state"] == "ready"
    assert status["ready"] is True
    assert status["built_at"] > 0
    assert status["age"] is not None and status["age"] >= 0
    assert status["truncated"] is False


def test_unindex_writes_tombstone_then_rebuild_clears_it(tmp_path, monkeypatch):
    # C11 #2: unindex leaves a tombstone; a later fresh build supersedes it and clears it.
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    file_index.unindex(tmp_path)
    assert file_index._tombstone_path(tmp_path).exists()
    assert file_index._tombstone_time(tmp_path) > 0
    file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    assert not file_index._tombstone_path(tmp_path).exists()


def test_tombstone_evicts_stale_inmemory_copy(tmp_path, monkeypatch):
    # C11 #2: a tombstone newer than an in-memory copy (another process unindexed) drops that copy so the
    # process stops serving deleted-file results.
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    ri = file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    assert ri.ready and ri.entries
    file_index.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    file_index._tombstone_path(tmp_path).write_text(str(ri.built_at + 100), encoding="utf-8")
    monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: None)  # don't mask eviction
    refreshed = file_index.ensure_index(tmp_path, SEARCH_SKIP_DIRS)
    assert refreshed.ready is False
    assert refreshed.entries == []


def test_unindex_drops_registry_and_disk(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    assert file_index._index_disk_path(tmp_path).exists()
    file_index.unindex(tmp_path)
    with file_index._REGISTRY_LOCK:
        assert str(tmp_path) not in file_index._REGISTRY
    assert not file_index._index_disk_path(tmp_path).exists()
