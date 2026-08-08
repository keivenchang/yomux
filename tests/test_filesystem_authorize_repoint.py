"""External filesystem-repoint proof for indexed-search metadata."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from yolomux_lib import filesystem
from yolomux_lib.filesystem import paths
from yolomux_lib.filesystem import search
from yolomux_lib.search import file_index


@pytest.fixture
def repoint_tree(monkeypatch):
    """A disposable allowed root and fake blocked target, both below /tmp."""
    with tempfile.TemporaryDirectory(prefix="yofs-race-", dir="/tmp") as raw_root:
        root = Path(raw_root)
        blocked = root / ".ssh"
        blocked.mkdir()
        monkeypatch.setenv(paths.FS_ROOTS_ENV, str(root))
        paths.invalidate_path_policy_caches()
        file_index.clear_memory_indexes()
        yield root, blocked
        file_index.clear_memory_indexes()
        paths.invalidate_path_policy_caches()


def test_indexed_search_never_reports_a_repointed_secret_realpath(repoint_tree):
    """An indexed row must not combine old metadata with a new secret target."""
    root, blocked = repoint_tree
    safe = root / "child.txt"
    safe.write_text("safe\n", encoding="utf-8")
    blocked_target = blocked / "child.txt"
    blocked_target.write_text("blocked indexed target\n", encoding="utf-8")

    policy = search._search_index_policy(root)
    index = file_index.build_now(
        root,
        policy["skip_dirs"],
        policy["exclude_path"],
        policy["exclude_signature"],
        persist_enabled=False,
    )
    assert index.ready is True

    safe.unlink()
    safe.symlink_to(blocked_target)

    payload = filesystem.search_files(str(root), "child", recursive=True)

    assert all(entry.get("realpath") != str(blocked_target) for entry in payload["files"])
