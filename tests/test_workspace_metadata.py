# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest

from yomux_lib.workspace import metadata
from tests._git_helpers import git, init_repo


@pytest.mark.parametrize("allow_network", [False, True])
def test_current_branch_enrichment_retains_all_cached_pull_requests(allow_network):
    cache = metadata.MetadataCache()
    values = [{"number": 80}, {"number": 81}]
    cache.set("github-pr-branch:example/repo:feature", values)
    cache.set("github-pr-branch-state:example/repo:feature", "ready")
    branch = {"name": "feature", "current": True}
    git_data = {
        "github_repo": {"owner": "example", "name": "repo"},
        "branch": "feature", "local_pull_request": None,
        "other_branches": {"branches": [branch]},
    }
    assert metadata.current_branch_pull_request(git_data, cache, allow_network)["number"] == 80
    metadata.enrich_branch_pull_requests(git_data, cache, allow_network)
    assert branch.get("pull_requests") == values
    assert branch["pull_request_lookup_state"] == "ready"


@pytest.mark.parametrize(("name", "rename"), [
    (" leading", False), ("trailing ", False), ('"quoted"', False),
    ("arrow -> target", False), ("renamed -> target", True),
])
def test_dirty_activity_uses_literal_porcelain_paths(tmp_path, name, rename):
    init_repo(tmp_path)
    note = tmp_path / ("original" if rename else name)
    note.write_bytes(b"original\n")
    git(tmp_path, "add", "--", note.name)
    git(tmp_path, "commit", "-m", "fixture")
    if rename:
        git(tmp_path, "mv", "--", note.name, name)
        note = tmp_path / name
    note.write_bytes(b"modified\n")
    result = metadata.git_metadata_base(str(tmp_path))
    assert result is not None
    assert result["dirty_activity_ts"] == note.stat().st_mtime
