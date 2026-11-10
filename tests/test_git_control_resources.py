# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import contextlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from yomux_lib.filesystem import git_ops
from yomux_lib.filesystem.errors import FilesystemError
from tests._git_helpers import git, init_repo
from tests.helpers.fixture_tools import fixture_script


@pytest.mark.parametrize("reader", ["small", "shallow", "safe_path", "safe_child"])
def test_git_control_reader_opens_fifo_without_waiting_for_writer(tmp_path, reader):
    os.mkfifo(tmp_path / "HEAD")
    os.mkfifo(tmp_path / "shallow")
    script = """
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from yomux_lib.filesystem import git_ops
from yomux_lib.filesystem import paths
from yomux_lib.filesystem.errors import FilesystemError
descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    if sys.argv[2] == 'small':
        assert git_ops._read_small_git_control_file(descriptor, 'HEAD') is None
    elif sys.argv[2] == 'shallow':
        try:
            git_ops._git_shallow_snapshot(SimpleNamespace(descriptor=descriptor), deadline=time.monotonic() + 1)
        except FilesystemError as error:
            assert error.message_key == 'fs.error.gitRepositoryChanged'
        else:
            raise AssertionError('FIFO shallow file was admitted')
    else:
        target = Path(sys.argv[1]) / 'HEAD'
        scope = paths.safe_path(str(target)) if sys.argv[2] == 'safe_path' else paths.safe_child(descriptor, target, target)
        with scope:
            pass
finally:
    os.close(descriptor)
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path), reader], check=True, timeout=5)


def test_pinned_repo_path_preserves_not_repo_error_from_admitted_body(monkeypatch, tmp_path):
    marker = object()
    monkeypatch.setattr(git_ops, "pinned_git_scope_from_handle", lambda *_args, **_kwargs: contextlib.nullcontext(marker))
    error = FilesystemError("body failed", message_key="fs.error.notGitRepo")
    with pytest.raises(FilesystemError) as caught:
        with git_ops.pinned_repo_path(SimpleNamespace(resolved=Path(tmp_path))) as scope:
            assert scope is marker
            raise error
    assert caught.value is error


@pytest.mark.parametrize("callback", ["fsmonitor", "external-diff", "textconv"])
def test_git_reads_do_not_execute_repository_callbacks(tmp_path, callback):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "note").write_bytes(b"retained history\n")
    git(repo, "add", "note")
    git(repo, "commit", "-m", "fixture")
    (repo / "note").write_bytes(b"changed history\n")
    git(repo, "commit", "-am", "changed")
    marker = tmp_path / "hook-ran"
    helper = tmp_path / "fsmonitor"
    helper.write_bytes(fixture_script(
        f"#!/usr/bin/python3\nfrom pathlib import Path\nimport sys\nPath({str(marker)!r}).touch()\nsys.stdout.buffer.write(b'token\\0')\n"
    ).encode())
    helper.chmod(0o700)
    if callback == "fsmonitor":
        git(repo, "config", "core.fsmonitor", str(helper))
        result = git_ops.git_history(str(repo))
        assert len(result["commits"]) == 2
    else:
        if callback == "external-diff":
            git(repo, "config", "diff.external", str(helper))
        else:
            git(repo, "config", "diff.fixture.textconv", str(helper))
            (repo / ".gitattributes").write_bytes(b"note diff=fixture\n")
        git_ops.diff_file(str(repo / "note"), from_ref="HEAD~1", to_ref="HEAD")
    assert not marker.exists(), f"read-only Git read executed repository-controlled {callback}"


@pytest.mark.parametrize("width", [40, 64])
@pytest.mark.parametrize("uncommitted", [False, True])
def test_blame_parser_supports_each_repository_object_format(width, uncommitted):
    sha = ("0" if uncommitted else "a") * width
    result = git_ops._parse_blame_porcelain(
        f"{sha} 1 1 1\nauthor Example\nsummary first\n\tline\n"
    )
    assert result["1"]["sha"] == sha
    assert result["1"]["author"] == ("You" if uncommitted else "Example")


def test_blame_cache_observes_atomic_replacement_with_preserved_mtime(tmp_path):
    init_repo(tmp_path)
    note = tmp_path / "note"
    note.write_bytes(b"original\n")
    git(tmp_path, "add", "note")
    git(tmp_path, "commit", "-m", "fixture")
    before = git_ops.blame_file(str(note))
    assert before["lines"]["1"]["author"] == "Test User"
    original = note.stat()
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"modified\n")
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    os.replace(replacement, note)
    after = git_ops.blame_file(str(note))
    assert after["lines"]["1"]["author"] == "You"
