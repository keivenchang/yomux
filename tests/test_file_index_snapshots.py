# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import os
from types import SimpleNamespace

import pytest

from yomux_lib.search import file_index


def publish_snapshot(root, identity, name, tombstone=""):
    metadata = {
        "version": str(file_index.INDEX_FORMAT_VERSION), "storage": "sqlite",
        "root": str(root), "skip_signature": file_index._disk_skip_signature(root, set()),
        "entries_signature": "fixture-published", "built_at": "1", "truncated": "0",
        "tombstone_identity": tombstone,
        file_index.AUTHORIZED_ROOT_IDENTITY_FIELD: file_index._root_identity_metadata_value(identity),
    }
    with file_index._sqlite_index_connection(root) as connection:
        file_index._ensure_sqlite_schema(connection)
        connection.execute("DELETE FROM entries")
        connection.execute("INSERT INTO entries(path, name, relative_path, size, mtime) VALUES (?, ?, ?, 1, 1)", (str(root / name), name, name))
        file_index._replace_sqlite_metadata(connection, metadata)


@pytest.mark.parametrize("initial_load", [False, True])
@pytest.mark.parametrize("superseded", [False, True])
def test_adoption_keeps_rows_with_their_publication_identity(tmp_path, monkeypatch, initial_load, superseded):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "indexes")
    file_index.INDEX_DIR.mkdir(mode=0o700)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    index = file_index.RootIndex(root)
    try:
        index.replace_root_fd(descriptor)
        identity = index.root_fd_identity
        publish_snapshot(root, identity, "old.txt")
        old_snapshot = file_index._load_disk(root, set(), expected_root_identity=identity)
        assert old_snapshot is not None
        if superseded:
            marker = file_index._write_tombstone(root)
            publish_snapshot(root, identity, "new.txt", marker)
        if initial_load:
            monkeypatch.setattr(file_index, "_REGISTRY", {})
            monkeypatch.setattr(file_index, "build_authorized", lambda: True)
            monkeypatch.setattr(file_index, "_load_disk", lambda *_args, **_kwargs: old_snapshot)
            monkeypatch.setattr(file_index, "_raw_snapshot_metadata", lambda *_args, **_kwargs: {})
            monkeypatch.setattr(file_index, "index_freshness", lambda *_args, **_kwargs: SimpleNamespace(state=file_index.FRESHNESS_FRESH))
            monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: None)
            monkeypatch.setattr(file_index, "_resumable_frontier_generation", lambda *_args, **_kwargs: None)
            index.close_root_fd()
            index = file_index.ensure_index(root, set(), root_fd=descriptor)
        else:
            file_index._adopt_disk_snapshot(index, old_snapshot, file_index._skip_signature(set()))
        rows, _ = file_index._servable_snapshot(index)
        assert [row[1] for row in rows] == ([] if superseded else ["old.txt"])
    finally:
        index.close_root_fd()
        os.close(descriptor)
