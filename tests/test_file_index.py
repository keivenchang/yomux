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
    file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
    payload = filesystem.search_files(str(tmp_path), query="deep_target", limit=50, recursive=True)
    relative_paths = [entry["relative_path"] for entry in payload["files"]]
    assert "a/b/c/deep_target.md" in relative_paths


def test_index_status_warms_and_reports_state(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
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


def test_empty_query_serves_recent_from_index_without_cold_walk(tmp_path, monkeypatch):
    # C11: an empty query on a ready full-tree index returns a capped recent slice (state "ready") instead
    # of cold-walking the tree.
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    _make_tree(tmp_path)
    file_index.build_now(tmp_path, SEARCH_SKIP_DIRS)
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
    monkeypatch.setattr(file_index, "ensure_index", lambda root, skip: _NotReady())

    def _boom(*_args, **_kwargs):
        raise AssertionError("warming index must not cold-walk")
    monkeypatch.setattr(filesystem, "_search_full_tree", _boom)
    payload = filesystem.search_files(str(tmp_path), query="", limit=50, recursive=True)
    assert payload["index_state"] == "warming"
    assert payload["files"] == []


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
