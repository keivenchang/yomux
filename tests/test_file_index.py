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
    initial = file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    assert any(name == "id_rsa" for _p, name, _r, _s, _m in initial.entries)

    filtered = file_index.build_now(
        tmp_path,
        SEARCH_SKIP_DIRS,
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
    initial = file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    assert initial.ready is True
    assert any(name == "id_rsa" for _p, name, _r, _s, _m in initial.entries)
    monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: None)

    filtered = file_index.ensure_index(
        tmp_path,
        SEARCH_SKIP_DIRS,
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
