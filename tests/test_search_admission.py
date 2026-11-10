# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import os

import pytest

from yomux_lib.filesystem import search


@pytest.mark.parametrize("exclude_first", [False, True])
def test_direct_search_applies_exclusions_before_limit_and_reports_more(tmp_path, monkeypatch, exclude_first):
    for name in ("one.txt", "two.txt", "three.txt"):
        (tmp_path / name).write_bytes(b"fixture")
    with os.scandir(tmp_path) as entries:
        names = [entry.name for entry in entries]
    excluded = {names[0]} if exclude_first else set()
    monkeypatch.setattr(search, "_search_index_policy", lambda root: {
        "skip_dirs": set(), "excluded_paths": list(excluded),
        "exclude_path": lambda path: path.name in excluded,
    })
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = search._search_files_from_safe_root(str(tmp_path), limit=1, access_root=tmp_path, access_descriptor=descriptor, direct_only=True)
    finally:
        os.close(descriptor)
    assert len(result["files"]) == 1
    assert result["files"][0]["name"] not in excluded
    assert result["truncated"] is True
