"""External filesystem-repoint proof for indexed-search metadata."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from yolomux_lib import filesystem
from yolomux_lib.filesystem import paths
from yolomux_lib.filesystem import search
from yolomux_lib.search import bfs_index
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


def test_delta_read_never_reports_a_repointed_secret_realpath(repoint_tree, monkeypatch, tmp_path):
    """Step 3: a streamed delta upsert is filtered + annotated IDENTICALLY to a snapshot match, so a
    row repointed at a blocked target after it was indexed can never leak the blocked realpath."""
    root, blocked = repoint_tree
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    safe = root / "child.txt"
    safe.write_text("safe\n", encoding="utf-8")
    blocked_target = blocked / "child.txt"
    blocked_target.write_text("blocked delta target\n", encoding="utf-8")

    policy = search._search_index_policy(root)
    bfs_index.build_root_progressively(
        root,
        policy["skip_dirs"],
        exclude_path=policy["exclude_path"],
        exclude_signature=policy["exclude_signature"],
        generation=1,
    )
    # Repoint the indexed row at the blocked secret AFTER it was committed to the journal.
    safe.unlink()
    safe.symlink_to(blocked_target)

    # Read from an ORIGIN cursor (revision 0) so the committed child.txt upsert is re-scanned.
    origin = file_index._encode_delta_cursor(
        root=root, policy=policy["exclude_signature"], generation=1, revision=0, tombstone_identity=""
    )
    payload = filesystem.search_files(str(root), "child", cursor=origin)

    assert "changes" in payload
    assert all(change.get("realpath") != str(blocked_target) for change in payload["changes"])


def test_delta_read_on_an_unauthorized_root_is_refused(repoint_tree):
    """Step 3: safe-root containment applies to a delta read too -- a root outside the authorized set
    fails closed before any journal is read."""
    root, _ = repoint_tree
    outside = str(root.parent)  # the authorized root's parent is not itself authorized
    with pytest.raises(paths.FilesystemError):
        filesystem.search_files(outside, "child", cursor="any-cursor")
