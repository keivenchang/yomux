import base64
import errno
import fcntl
import inspect
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from yolomux_lib import filesystem
from yolomux_lib.filesystem import search as filesystem_search
from yolomux_lib.filesystem import FilesystemError
from yolomux_lib.filesystem import io_ops as filesystem_io
from yolomux_lib.filesystem import paths as filesystem_paths
from yolomux_lib.filesystem.io_ops import read_json_file
from yolomux_lib.filesystem import git_ops
from yolomux_lib.search import bfs_index
from yolomux_lib.workspace import metadata

from _git_helpers import git, init_repo
from mock_git_repo import create_git_history_repository


def _open_descriptors_beneath(root: Path) -> dict[str, str]:
    if sys.platform == "darwin":
        descriptors = {}
        for descriptor in range(256):
            try:
                raw_path = fcntl.fcntl(
                    descriptor,
                    filesystem_paths.DARWIN_F_GETPATH,
                    b"\0" * filesystem_paths.DARWIN_PATH_BUFFER_BYTES,
                )
                target = Path(raw_path.split(b"\0", 1)[0].decode("utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if target == root or target.is_relative_to(root):
                descriptors[str(descriptor)] = str(target)
        return descriptors
    descriptor_root = next(path for path in (Path("/proc/self/fd"), Path("/dev/fd")) if path.exists())
    descriptors = {}
    for descriptor in descriptor_root.iterdir():
        try:
            target = Path(os.readlink(descriptor))
        except OSError:
            continue
        if target == root or target.is_relative_to(root):
            descriptors[descriptor.name] = str(target)
    return descriptors


def _swap_path_after_authorization(monkeypatch, requested_path, link_to_swap, replacement):
    original_ensure_path_allowed = filesystem_paths._ensure_path_allowed
    state = {"swapped": False}

    def authorize_then_swap(path, *, resolved=None):
        result = original_ensure_path_allowed(path, resolved=resolved)
        if path == requested_path and not state["swapped"]:
            state["swapped"] = True
            link_to_swap.unlink()
            link_to_swap.symlink_to(replacement, target_is_directory=replacement.is_dir())
        return result

    monkeypatch.setattr(filesystem_paths, "_ensure_path_allowed", authorize_then_swap)
    return state


def test_read_json_file_returns_default_for_missing_or_invalid_json(tmp_path):
    path = tmp_path / "state.json"

    assert read_json_file(path, {}) == {}
    path.write_text('{"ready": true}', encoding="utf-8")
    assert read_json_file(path, {}) == {"ready": True}
    path.write_text("{", encoding="utf-8")
    assert read_json_file(path, []) == []


def test_reindex_batch_skips_blocked_paths_without_starving_safe_paths(monkeypatch, caplog, tmp_path):
    caplog.set_level("INFO", logger=filesystem_search.__name__)
    project = tmp_path / "project"
    project.mkdir()
    # Register the project as the ONE candidate root so the shared prefilter covers `safe`; with no root
    # configured there is genuinely nothing to reindex and the prefilter correctly returns [].
    monkeypatch.setattr(filesystem_search.file_index, "_iter_candidate_index_roots", lambda: [project])
    # A secret sibling OUTSIDE every index root: no candidate root covers it, so the shared
    # exclusion prefilter drops it before authorization -- silently, and without a warning.
    secret = str(tmp_path / ".azure" / "token")
    # A change UNDER the index root whose parent no longer exists: admitted by the prefilter
    # (inside the root, not excluded) but unindexable at authorization time (parent gone -> 404).
    # It must be skipped with exactly ONE deduplicated diagnostic and never abort the batch.
    vanished = str(project / "gone" / "app.py")
    safe = str(project / "app.py")
    dirty = []
    monkeypatch.setattr(
        filesystem_search.file_index,
        "mark_paths_dirty",
        lambda paths, include_root, prepare_root=None: dirty.extend(paths) or {},
    )
    monkeypatch.setattr(filesystem_search.file_index, "background_owner_can_build", lambda: True)
    monkeypatch.setattr(filesystem_search.file_index, "schedule_refreshes", lambda: 0)
    filesystem_search._LOGGED_BLOCKED_REINDEX_PATHS.clear()

    assert filesystem_search.reindex_roots_for_paths([secret, vanished, safe], reason="fs-watch") == []
    assert filesystem_search.reindex_roots_for_paths([secret, vanished, safe], reason="fs-watch") == []

    # The safe path under the index root is marked dirty on every batch: neither a blocked
    # sibling nor an unindexable one starves it.
    assert dirty == [Path(safe), Path(safe)]
    # The out-of-root secret is dropped by the prefilter without any log spam.
    assert not any("token" in message for message in caplog.messages)
    # A disappearing path is diagnostic, not a release-blocking warning.
    assert [record.levelname for record in caplog.records if vanished in record.message] == ["INFO"]


def test_list_directory_returns_entries(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".git").mkdir()
    (tmp_path / "repo" / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "fixture").mkdir()
    (tmp_path / "fixture" / ".git").mkdir()
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "big.dat").write_bytes(b"\x00" * 100)

    payload = filesystem.list_directory(str(tmp_path))

    assert payload["path"] == str(tmp_path)
    assert payload["parent"] == str(tmp_path.parent)
    names = {entry["name"]: entry for entry in payload["entries"]}
    assert names["sub"]["kind"] == "dir"
    assert names["sub"]["is_repo"] is False
    assert names["repo"]["kind"] == "dir"
    assert names["repo"]["is_repo"] is True
    assert names["fixture"]["kind"] == "dir"
    assert names["fixture"]["is_repo"] is False
    assert names["file.txt"]["kind"] == "file"
    assert "is_repo" not in names["file.txt"]
    assert names["file.txt"]["size"] == len("hello")
    assert names["big.dat"]["kind"] == "file"


def test_visible_directory_scan_duplicates_pinned_dev_fd_on_macos(tmp_path, monkeypatch):
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    descriptor = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    real_open = os.open

    def guarded_open(path, *args, **kwargs):
        assert Path(path).parent != Path("/dev/fd")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    try:
        names, truncated = filesystem.listing._visible_directory_names(
            descriptor,
            resolved_parent=tmp_path,
            requested_path=tmp_path,
            include_repo_info=False,
        )
    finally:
        os.close(descriptor)

    assert names == ["hello.txt"]
    assert truncated is False


def test_listing_reuses_parent_canonicalization_for_ordinary_children(monkeypatch, tmp_path):
    (tmp_path / "first.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("second\n", encoding="utf-8")
    real = tmp_path / "real"
    (real / "first").mkdir(parents=True)
    (real / "second").mkdir()
    (tmp_path / "first-link").symlink_to(real / "first", target_is_directory=True)
    (tmp_path / "second-link").symlink_to(real / "second", target_is_directory=True)
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    (tmp_path / "blocked-link").symlink_to(blocked, target_is_directory=True)
    # Warm immutable root/secret policy caches. The measured live baseline spent most
    # of its handler time resolving every ordinary child independently.
    filesystem.list_directory(str(tmp_path))
    calls: list[Path] = []
    original = filesystem.listing.paths._normalized_scope_path

    def counting_normalize(path):
        calls.append(Path(path))
        return original(path)

    monkeypatch.setattr(filesystem.listing.paths, "_normalized_scope_path", counting_normalize)
    payload = filesystem.list_directory(str(tmp_path))

    assert {entry["name"] for entry in payload["entries"]} == {
        "first-link", "first.txt", "real", "second-link", "second.txt",
    }
    assert calls.count(tmp_path / "first.txt") == 0
    assert calls.count(tmp_path / "second.txt") == 0
    assert calls.count(tmp_path / "first-link") == 0
    assert calls.count(tmp_path / "second-link") == 0
    assert calls.count(tmp_path / "blocked-link") == 0
    assert calls.count(real) == 2


def test_symlink_target_resolution_follows_parent_replacement(tmp_path):
    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "item").write_text("safe\n", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    (blocked / "item").write_text("blocked\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(safe / "item")
    assert filesystem.listing._resolved_symlink_target(link, target_text=os.readlink(link)) == safe / "item"

    safe.rename(tmp_path / "safe-old")
    safe.symlink_to(blocked, target_is_directory=True)

    assert filesystem.listing._resolved_symlink_target(link, target_text=os.readlink(link)) == blocked / "item"


def test_symlink_target_parent_resolution_does_not_poison_later_rows(monkeypatch, tmp_path):
    first_target = tmp_path / "first-target"
    (first_target / "subdirectory").mkdir(parents=True)
    second_target = tmp_path / "second-target"
    (second_target / "subdirectory").mkdir(parents=True)
    target_root = tmp_path / "target-root"
    target_root.symlink_to(first_target, target_is_directory=True)
    target_parent = target_root / "subdirectory"
    link = tmp_path / "link"
    link.symlink_to(target_parent / "item")
    original_normalize = filesystem.listing.paths._normalized_scope_path
    raced = False

    def normalize_during_temporary_repoint(path):
        nonlocal raced
        if path == target_parent and not raced:
            raced = True
            target_root.unlink()
            target_root.symlink_to(second_target, target_is_directory=True)
            try:
                return original_normalize(path)
            finally:
                target_root.unlink()
                target_root.symlink_to(first_target, target_is_directory=True)
        return original_normalize(path)

    monkeypatch.setattr(filesystem.listing.paths, "_normalized_scope_path", normalize_during_temporary_repoint)
    target_text = os.readlink(link)
    assert filesystem.listing._resolved_symlink_target(link, target_text=target_text) == second_target / "subdirectory" / "item"
    assert filesystem.listing._resolved_symlink_target(link, target_text=target_text) == first_target / "subdirectory" / "item"


def test_listing_symlink_metadata_stays_bound_to_the_authorized_target(monkeypatch, tmp_path):
    safe = tmp_path / "safe"
    safe.mkdir()
    safe_target = safe / "item"
    safe_target.write_bytes(b"s")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "item"
    blocked_target.write_bytes(b"x" * 4096)
    link = tmp_path / "link"
    link.symlink_to(safe_target)
    original_path_is_secret = filesystem.listing.paths._path_is_secret
    swapped = False

    def swap_after_authorization(path, *, resolved=None, resolve=True):
        nonlocal swapped
        result = original_path_is_secret(path, resolved=resolved, resolve=resolve)
        if path.name == link.name and not result and not swapped:
            swapped = True
            link.unlink()
            link.symlink_to(blocked_target)
        return result

    monkeypatch.setattr(filesystem.listing.paths, "_path_is_secret", swap_after_authorization)

    payload = filesystem.list_directory(str(tmp_path))
    entries = {entry["name"]: entry for entry in payload["entries"]}

    assert swapped is True
    assert "link" not in entries


def test_listing_regular_child_repointed_to_blocked_symlink_never_leaks_metadata(monkeypatch, tmp_path):
    child = tmp_path / "item.txt"
    child.write_bytes(b"s")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_bytes(b"x" * 4096)
    original_path_is_secret = filesystem.listing.paths._path_is_secret
    swapped = False

    def swap_after_child_authorization(path, *, resolved=None, resolve=True):
        nonlocal swapped
        result = original_path_is_secret(path, resolved=resolved, resolve=resolve)
        if path.name == child.name and not result and not swapped:
            swapped = True
            child.unlink()
            child.symlink_to(blocked_target)
        return result

    monkeypatch.setattr(filesystem.listing.paths, "_path_is_secret", swap_after_child_authorization)

    payload = filesystem.list_directory(str(tmp_path))

    assert swapped is True
    assert child.name not in {entry["name"] for entry in payload["entries"]}


def test_listing_opens_regular_children_from_the_pinned_parent_generation(monkeypatch, tmp_path):
    root = tmp_path / "listed"
    root.mkdir()
    child = root / "item.txt"
    child.write_bytes(b"s")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_bytes(b"x" * 4096)
    original_path_is_secret = filesystem.listing.paths._path_is_secret
    swapped = False

    def swap_parent_after_child_authorization(path, *, resolved=None, resolve=True):
        nonlocal swapped
        result = original_path_is_secret(path, resolved=resolved, resolve=resolve)
        if resolved == child and not result and not swapped:
            swapped = True
            root.rename(tmp_path / "listed-old")
            root.mkdir()
            (root / child.name).symlink_to(blocked_target)
        return result

    monkeypatch.setattr(filesystem.listing.paths, "_path_is_secret", swap_parent_after_child_authorization)

    payload = filesystem.list_directory(str(root))
    listed_child = {entry["name"]: entry for entry in payload["entries"]}[child.name]

    assert swapped is True
    assert listed_child["size"] == 1
    assert listed_child["realpath"] == str(child)


def test_watch_signature_reports_missing_but_propagates_blocked_paths(tmp_path):
    assert filesystem.watch_signature(str(tmp_path / "missing")) == (str(tmp_path / "missing"), "missing")

    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    with pytest.raises(filesystem.FilesystemError) as error:
        filesystem.watch_signature(str(blocked))

    assert error.value.status == 403
    assert error.value.message_key == "fs.error.credentialBlocked"


def test_watch_signature_stops_child_security_work_at_requested_limit(tmp_path, monkeypatch):
    for index in range(6):
        (tmp_path / f"entry-{index}.txt").write_text(str(index), encoding="utf-8")
    observed = []
    original = filesystem.listing.paths.name_observed

    def record(operation, path):
        observed.append((operation, path.name))
        return original(operation, path)

    monkeypatch.setattr(filesystem.listing.paths, "name_observed", record)
    signature = filesystem.watch_signature(str(tmp_path), child_limit=3)

    assert len(signature[-1]) == 3
    assert len(observed) == 4  # The root is observed once; exactly three children reach the security scan.


def test_watch_signature_detects_same_size_child_edit_within_one_second(tmp_path):
    child = tmp_path / "file.txt"
    child.write_text("before", encoding="utf-8")
    first_child_mtime_ns = 1_700_000_000_100_000_000
    os.utime(child, ns=(first_child_mtime_ns, first_child_mtime_ns))
    root_stat = tmp_path.stat()
    before = filesystem.watch_signature(str(tmp_path), child_limit=3)

    child.write_text("after!", encoding="utf-8")
    second_child_mtime_ns = first_child_mtime_ns + 1
    os.utime(child, ns=(second_child_mtime_ns, second_child_mtime_ns))
    os.utime(tmp_path, ns=(root_stat.st_atime_ns, root_stat.st_mtime_ns))
    after = filesystem.watch_signature(str(tmp_path), child_limit=3)

    assert before != after
    assert before[-1][0][2] == first_child_mtime_ns
    assert after[-1][0][2] == second_child_mtime_ns


def test_filesystem_batch_watch_signature_reuses_the_directory_listing_scan(tmp_path, monkeypatch):
    for index in range(6):
        (tmp_path / f"entry-{index}.txt").write_text(str(index), encoding="utf-8")
    expected_signature = filesystem.watch_signature(
        str(tmp_path),
        child_limit=filesystem.WATCH_SIGNATURE_CHILD_LIMIT,
    )
    scans = []
    original = filesystem.listing._visible_directory_names

    def count_scan(*args, **kwargs):
        scans.append(str(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(filesystem.listing, "_visible_directory_names", count_scan)
    result = filesystem.filesystem_batch_result({
        "requests": [{
            "id": "root",
            "type": "list",
            "path": str(tmp_path),
            "trigger_counts": {"watch-diff": 1},
            "include_watch_signature": True,
        }],
        "client_scope": "browser",
        filesystem.FS_ACCESS_POLICY_FIELD: filesystem.access_policy_descriptor(),
    })

    assert len(scans) == 1
    assert result["responses"][0]["watch_signature"] == expected_signature
    assert "watch_signature" not in result["responses"][0]["payload"]


def test_filesystem_batch_reuses_directory_repo_info_once_per_repo(tmp_path, monkeypatch):
    repos = [tmp_path / "repo-a", tmp_path / "repo-b"]
    directories = []
    for repo in repos:
        repo.mkdir()
        init_repo(repo)
        directories.append(repo)
        for index in range(3):
            directory = repo / f"directory-{index}"
            directory.mkdir()
            directories.append(directory)

    calls = []
    original = git_ops.git_repo_info

    def count_repo_info(repo, include_status=True, timeout=None):
        calls.append(repo)
        return original(repo, include_status=include_status, timeout=timeout)

    monkeypatch.setattr(git_ops, "git_repo_info", count_repo_info)
    result = filesystem.filesystem_batch_result({
        "requests": [
            {
                "id": f"directory-{index}",
                "type": "info",
                "path": str(directory),
                "trigger_counts": {"explicit-user": 1},
            }
            for index, directory in enumerate(directories)
        ],
        "client_scope": "browser",
        filesystem.FS_ACCESS_POLICY_FIELD: filesystem.access_policy_descriptor(),
    })

    assert all(response["ok"] is True for response in result["responses"])
    assert calls == repos


@pytest.mark.parametrize("reader", [filesystem_io.read_file, filesystem_io.read_raw])
def test_file_readers_consume_the_authorized_target_handle(monkeypatch, tmp_path, reader):
    safe = tmp_path / "safe.txt"
    safe.write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text("FAKE_SECRET_CONTENT", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(safe)
    state = _swap_path_after_authorization(monkeypatch, link, link, blocked_target)

    payload = reader(str(link))
    content = payload["content"] if isinstance(payload, dict) else payload[0].decode("utf-8")

    assert state["swapped"] is True
    assert content == "safe"


def test_write_file_consumes_the_authorized_target_handle(monkeypatch, tmp_path):
    safe = tmp_path / "safe.txt"
    safe.write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text("FAKE_SECRET_ORIGINAL", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(safe)
    state = _swap_path_after_authorization(monkeypatch, link, link, blocked_target)

    filesystem_io.write_file(str(link), "updated")

    assert state["swapped"] is True
    assert safe.read_text(encoding="utf-8") == "updated"
    assert blocked_target.read_text(encoding="utf-8") == "FAKE_SECRET_ORIGINAL"


def test_zip_directory_consumes_the_authorized_directory_handle(monkeypatch, tmp_path):
    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "safe.txt").write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    (blocked / "fake-key").write_text("FAKE_ZIP_SECRET", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(safe, target_is_directory=True)
    state = _swap_path_after_authorization(monkeypatch, link, link, blocked)

    archive_file, _archive_size = filesystem_io.zip_directory(str(link), max_bytes=1024 * 1024)
    try:
        with zipfile.ZipFile(archive_file) as archive:
            names = archive.namelist()
            content = archive.read(next(name for name in names if name.endswith("safe.txt"))).decode("utf-8")
    finally:
        archive_file.close()

    assert state["swapped"] is True
    assert content == "safe"
    assert all(not name.endswith("fake-key") for name in names)


def test_zip_directory_keeps_each_descendant_descriptor_live_through_archive_write(monkeypatch, tmp_path):
    safe = tmp_path / "safe"
    safe.mkdir()
    member = safe / "member.txt"
    member.write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text("FAKE_ZIP_DESCENDANT_SECRET", encoding="utf-8")
    original_write = zipfile.ZipFile.write
    original_open = zipfile.ZipFile.open
    swapped = False

    def swap_member():
        nonlocal swapped
        if swapped:
            return
        swapped = True
        member.unlink()
        member.symlink_to(blocked_target)

    def swap_before_legacy_write(archive, *args, **kwargs):
        swap_member()
        return original_write(archive, *args, **kwargs)

    def swap_after_pinned_open(archive, *args, **kwargs):
        if args and str(getattr(args[0], "filename", args[0])).endswith("member.txt"):
            swap_member()
        return original_open(archive, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", swap_before_legacy_write)
    monkeypatch.setattr(zipfile.ZipFile, "open", swap_after_pinned_open)

    archive_file, _archive_size = filesystem_io.zip_directory(str(safe), max_bytes=1024 * 1024)
    try:
        with zipfile.ZipFile(archive_file) as archive:
            content = archive.read("safe/member.txt").decode("utf-8")
    finally:
        archive_file.close()

    assert swapped is True
    assert content == "safe"


def test_zip_directory_never_follows_a_repointed_descendant_directory(monkeypatch, tmp_path):
    source = tmp_path / "archive"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (nested / "safe.txt").write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_bytes = b"FAKE_ZIP_DIRECTORY_SECRET"
    (blocked / "secret.txt").write_bytes(blocked_bytes)
    original_open = filesystem_paths.os.open
    swapped = False

    def swap_before_descendant_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "nested" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            nested.rename(source / "nested-old")
            nested.symlink_to(blocked, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(filesystem_paths.os, "open", swap_before_descendant_open)

    archive_file, _archive_size = filesystem_io.zip_directory(str(source), max_bytes=1024 * 1024)
    try:
        with zipfile.ZipFile(archive_file) as archive:
            archived_bytes = b"\n".join(
                archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/")
            )
    finally:
        archive_file.close()

    assert swapped is True
    assert blocked_bytes not in archived_bytes


def test_recursive_count_and_zip_skip_every_blocked_child(tmp_path):
    source = tmp_path / "tree"
    blocked_directory = source / ".ssh"
    blocked_directory.mkdir(parents=True)
    (source / "safe.txt").write_text("safe", encoding="utf-8")
    (source / ".netrc").write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE", encoding="utf-8")
    (blocked_directory / "id_rsa").write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE", encoding="utf-8")

    count = filesystem.count_directory_files(str(source))
    archive_file, _archive_size = filesystem.zip_directory(str(source), max_bytes=1024 * 1024)
    try:
        with zipfile.ZipFile(archive_file) as archive:
            names = archive.namelist()
            archived_bytes = b"\n".join(
                archive.read(name)
                for name in names
                if not name.endswith("/")
            )
    finally:
        archive_file.close()

    assert count["files"] == 1
    assert names == ["tree/", "tree/safe.txt"]
    assert b"BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in archived_bytes


def test_search_files_consumes_the_authorized_directory_handle(monkeypatch, tmp_path):
    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "safe.txt").write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    (blocked / "fake-key").write_text("FAKE_SEARCH_SECRET", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(safe, target_is_directory=True)
    state = _swap_path_after_authorization(monkeypatch, link, link, blocked)

    payload = filesystem_search.search_files(str(link), query="safe", recursive=False)

    assert state["swapped"] is True
    assert [item["name"] for item in payload["files"]] == ["safe.txt"]


def test_descriptor_path_never_re_resolves_a_pathname_on_darwin(monkeypatch, tmp_path):
    """`descriptor_path()` feeds `git -C`, the count/zip walkers and the multi-repo scan.

    Whatever it returns is opened AGAIN by that consumer, so it must name this descriptor
    generation and nothing else.  `F_GETPATH` returns a pathname the kernel re-resolves on the
    consumer's next open: a rename or namespace replacement between the call and the consumer hands
    the consumer a different object.  Darwin cannot run here, so this proves the branch is gone --
    with the platform forced to darwin, `F_GETPATH` must not be consulted at all.
    """
    target = tmp_path / "repo"
    target.mkdir()
    refusals = []

    def refuse_fcntl(*args, **kwargs):
        refusals.append(args)
        raise AssertionError("descriptor_path() re-resolved a pathname through F_GETPATH")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(fcntl, "fcntl", refuse_fcntl)
    descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        handle = filesystem_paths.SafePathHandle(target, target, descriptor)
        descriptor_path = handle.descriptor_path()
        assert refusals == []
        assert descriptor_path.parent in set(filesystem_paths.DESCRIPTOR_PATH_ROOTS)
        # Generation-bound, not name-bound: renaming the directory out from under the pathname must
        # not change which object that pathname names.
        target.rename(tmp_path / "moved")
        assert os.stat(descriptor_path).st_ino == os.fstat(descriptor).st_ino
    finally:
        os.close(descriptor)


def test_descriptor_path_fails_closed_without_a_descriptor_bound_root(monkeypatch, tmp_path):
    """No descriptor-bound root means no authorized pathname: refuse instead of substituting one."""
    target = tmp_path / "repo"
    target.mkdir()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(filesystem_paths, "DESCRIPTOR_PATH_ROOTS", ())
    descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        handle = filesystem_paths.SafePathHandle(target, target, descriptor)
        with pytest.raises(FilesystemError) as error:
            handle.descriptor_path()
        assert error.value.status == 500
    finally:
        os.close(descriptor)


def test_multi_repo_scan_authorizes_every_child_through_the_shared_owner(monkeypatch, tmp_path):
    """The non-recursive multi-repo scan used to `os.listdir()` then `os.open()` an absolute child
    path and wrap the raw descriptor in a bare `SafePathHandle`, so that child never passed
    `_ensure_path_allowed` and never opened relative to the pinned scan-root descriptor."""
    root = tmp_path / "workspaces"
    repo = root / "alpha"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / "needle.txt").write_text("needle", encoding="utf-8")
    authorized: list[Path] = []
    original_ensure = filesystem_paths._ensure_path_allowed

    def record_authorization(path, *, resolved=None):
        authorized.append(Path(path))
        return original_ensure(path, resolved=resolved)

    monkeypatch.setattr(filesystem_paths, "_ensure_path_allowed", record_authorization)

    payload = filesystem_search.search_files(str(root), query="needle", recursive=False)

    assert [item["name"] for item in payload["files"]] == ["needle.txt"]
    assert repo in authorized, "multi-repo scan child never passed the shared authorization owner"


def test_multi_repo_scan_refuses_a_child_the_policy_blocks(monkeypatch, tmp_path):
    """A blocked child must be refused by the one policy owner, not by a name-only prefilter."""
    root = tmp_path / "workspaces"
    repo = root / "alpha"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / "needle.txt").write_text("needle", encoding="utf-8")
    original_ensure = filesystem_paths._ensure_path_allowed

    def block_the_repo(path, *, resolved=None):
        if Path(path) == repo:
            raise FilesystemError(
                "path is blocked because it may contain credentials: BLOCKED_SENTINEL_DO_NOT_EXPOSE",
                status=403,
                message_key="fs.error.credentialBlocked",
            )
        return original_ensure(path, resolved=resolved)

    monkeypatch.setattr(filesystem_paths, "_ensure_path_allowed", block_the_repo)

    payload = filesystem_search.search_files(str(root), query="needle", recursive=False)

    assert payload["files"] == []


def test_recursive_search_never_follows_a_repointed_descendant(monkeypatch, tmp_path):
    indexed = tmp_path / "indexed"
    nested = indexed / "nested"
    nested.mkdir(parents=True)
    (nested / "safe.txt").write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    (blocked / "secret.txt").write_text("FAKE_SEARCH_DESCENDANT_SECRET", encoding="utf-8")
    original_open = filesystem_paths.os.open
    swapped = False

    def swap_before_descendant_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "nested" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            nested.rename(indexed / "nested-old")
            nested.symlink_to(blocked, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(filesystem_paths.os, "open", swap_before_descendant_open)

    payload = filesystem_search.search_files(str(indexed), query="secret", recursive=True)

    assert swapped is True
    assert payload["files"] == []


def test_async_index_build_consumes_the_authorized_directory_handle(monkeypatch, tmp_path):
    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "safe.txt").write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    (blocked / "fake-key").write_text("FAKE_INDEX_SECRET", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(safe, target_is_directory=True)
    state = _swap_path_after_authorization(monkeypatch, link, link, blocked)
    filesystem_search.file_index.clear_memory_indexes()

    with filesystem_paths.safe_path(
        str(link),
        flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    ) as handle:
        index = filesystem_search.file_index.build_now(
            handle.resolved,
            set(),
            persist_enabled=False,
            root_fd=handle.descriptor,
        )

    assert state["swapped"] is True
    assert [entry[1] for entry in index.entries] == ["safe.txt"]
    filesystem_search.file_index.clear_memory_indexes()


def test_breadth_first_index_build_keeps_the_authorized_root_generation(monkeypatch, tmp_path):
    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "safe.txt").write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    (blocked / "secret.txt").write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE", encoding="utf-8")
    parked = tmp_path / "safe-authorized"
    file_index = filesystem_search.file_index
    file_index.clear_memory_indexes()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with filesystem_paths.safe_path(str(safe), flags=directory_flags, operation="search_files") as handle:
        safe.rename(parked)
        safe.symlink_to(blocked, target_is_directory=True)
        try:
            index = file_index.build_now(
                handle.resolved,
                set(),
                persist_enabled=False,
                root_fd=handle.descriptor,
                operation="search_files",
            )
        finally:
            safe.unlink()
            parked.rename(safe)

    assert [entry[1] for entry in index.entries] == ["safe.txt"]
    assert "BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in repr(index.entries)
    file_index.clear_memory_indexes()


def test_async_index_never_follows_a_repointed_descendant(monkeypatch, tmp_path):
    indexed = tmp_path / "indexed"
    nested = indexed / "nested"
    nested.mkdir(parents=True)
    (nested / "safe.txt").write_text("safe", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    (blocked / "secret.txt").write_text("FAKE_INDEX_DESCENDANT_SECRET", encoding="utf-8")
    original_open = filesystem_paths.os.open
    swapped = False

    def swap_before_descendant_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "nested" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            nested.rename(indexed / "nested-old")
            nested.symlink_to(blocked, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(filesystem_paths.os, "open", swap_before_descendant_open)
    filesystem_search.file_index.clear_memory_indexes()

    with filesystem_paths.safe_path(
        str(indexed),
        flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    ) as handle:
        index = filesystem_search.file_index.build_now(
            handle.resolved,
            set(),
            persist_enabled=False,
            root_fd=handle.descriptor,
        )

    assert swapped is True
    assert all(entry[1] != "secret.txt" for entry in index.entries)
    filesystem_search.file_index.clear_memory_indexes()


@pytest.mark.parametrize("operation", ["mkdir", "rename", "delete"])
def test_parent_mutations_consume_the_authorized_parent_handle(monkeypatch, tmp_path, operation):
    safe_parent = tmp_path / "safe"
    safe_parent.mkdir()
    blocked_parent = tmp_path / ".ssh"
    blocked_parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(safe_parent, target_is_directory=True)
    if operation == "mkdir":
        requested = alias / "created"
    else:
        requested = alias / "item"
        (safe_parent / "item").write_text("safe", encoding="utf-8")
        (blocked_parent / "item").write_text("blocked", encoding="utf-8")
    state = _swap_path_after_authorization(monkeypatch, requested, alias, blocked_parent)

    if operation == "mkdir":
        filesystem_io.create_directory(str(requested))
        assert (safe_parent / "created").is_dir()
        assert not (blocked_parent / "created").exists()
    elif operation == "rename":
        filesystem_io.rename_path(str(requested), "moved")
        assert (safe_parent / "moved").read_text(encoding="utf-8") == "safe"
        assert (blocked_parent / "item").read_text(encoding="utf-8") == "blocked"
    else:
        filesystem_io.delete_path(str(requested))
        assert not (safe_parent / "item").exists()
        assert (blocked_parent / "item").read_text(encoding="utf-8") == "blocked"
    assert state["swapped"] is True


def test_diff_final_symlink_swap_never_returns_target_file_content(monkeypatch, tmp_path):
    init_repo(tmp_path)
    safe = tmp_path / "safe.txt"
    safe.write_text("safe\n", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text("FAKE_DIFF_SECRET\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(safe)
    state = _swap_path_after_authorization(monkeypatch, link, link, blocked_target)

    payload = git_ops.diff_file(str(link))

    assert state["swapped"] is True
    assert "FAKE_DIFF_SECRET" not in payload["diff"]


def test_diff_keeps_working_file_descriptor_live_through_git_consumption(monkeypatch, tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "base")
    target.write_text("safe\n", encoding="utf-8")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text("FAKE_DIFF_CONSUMER_SECRET\n", encoding="utf-8")
    original_pinned_repo_root = git_ops._pinned_repo_root
    swapped = False

    def swap_before_repo_discovery(handle, *, deadline=None, operation=""):
        nonlocal swapped
        if not swapped:
            swapped = True
            target.rename(tmp_path / "safe-old.py")
            target.symlink_to(blocked_target)
        return original_pinned_repo_root(handle, deadline=deadline, operation=operation)

    monkeypatch.setattr(git_ops, "_pinned_repo_root", swap_before_repo_discovery)

    with pytest.raises(FilesystemError) as changed:
        git_ops.diff_file(str(target))

    assert swapped is True
    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"
    assert "FAKE_DIFF_CONSUMER_SECRET" not in str(changed.value)


def test_indexed_search_annotation_binds_realpath_and_size_to_one_descriptor(monkeypatch, tmp_path):
    safe = tmp_path / "safe.txt"
    safe.write_bytes(b"s")
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_bytes(b"x" * 4096)
    link = tmp_path / "link.txt"
    link.symlink_to(safe)
    state = _swap_path_after_authorization(monkeypatch, link, link, blocked_target)
    entry = {"path": str(link), "realpath": "stale", "size": 999, "file_id": "stale"}

    with filesystem_paths.safe_path(
        str(tmp_path),
        flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        operation="search.annotate",
    ) as root_handle:
        admitted = filesystem_search._annotate_search_dedupe_fields(
            entry,
            root=tmp_path,
            root_descriptor=root_handle.descriptor,
        )

    assert state["swapped"] is True
    assert admitted is False


def test_indexed_search_annotation_rejects_the_complete_row_when_current_path_is_blocked(tmp_path):
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_bytes(b"blocked")
    link = tmp_path / "link.txt"
    link.symlink_to(blocked_target)
    entry = {
        "path": str(link),
        "realpath": "stale",
        "size": 999,
        "file_id": "stale",
        "file_identity": "stale",
    }

    with filesystem_paths.safe_path(
        str(tmp_path),
        flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        operation="search.annotate",
    ) as root_handle:
        admitted = filesystem_search._annotate_search_dedupe_fields(
            entry,
            root=tmp_path,
            root_descriptor=root_handle.descriptor,
        )

    assert admitted is False


def test_filesystem_entrypoints_route_through_the_shared_safe_path_primitive():
    entrypoints = {
        filesystem.listing.list_directory,
        filesystem.listing.watch_signature,
        filesystem_io.read_file,
        filesystem_io.read_raw,
        filesystem_io.write_file,
        filesystem_io.delete_path,
        filesystem_io.rename_path,
        filesystem_io.create_directory,
        filesystem_io.path_info,
        filesystem_io.is_text_path,
        filesystem_io.zip_directory,
        filesystem_io.count_directory_files,
        filesystem_search.search_files,
        filesystem_search.index_status,
        filesystem_search.unindex_root,
        filesystem_search.reindex_roots_for_paths,
        git_ops._pinned_git_history_scope,
        git_ops.diff_file,
        git_ops.blame_file,
    }

    bypasses = []
    for entrypoint in entrypoints:
        source = inspect.getsource(entrypoint)
        if "paths.safe_path(" not in source and "paths.safe_parent(" not in source:
            bypasses.append(f"{entrypoint.__module__}.{entrypoint.__name__}")

    assert bypasses == []


def test_unindex_follower_derives_refreshing_elsewhere_from_the_one_verdict(tmp_path, monkeypatch):
    # W6: the follower unindex path is a background-refresh control outcome, so
    # its `ok`/`refreshing_elsewhere` must come from the single classifier, not
    # from reading the raw `accepted` boolean twice. A live remote owner that
    # accepts the unindex is refreshing elsewhere; a rejected one is not.
    monkeypatch.setattr(filesystem_search.file_index, "background_owner_can_build", lambda: False)

    monkeypatch.setattr(
        filesystem_search.file_index,
        "request_background_owner_refresh",
        lambda _payload: {"ok": True, "accepted": True, "role": "search-index", "fallback": False},
    )
    accepted = filesystem_search.unindex_root(str(tmp_path))
    assert accepted == {"root": str(tmp_path), "ok": True, "refreshing_elsewhere": True}

    monkeypatch.setattr(
        filesystem_search.file_index,
        "request_background_owner_refresh",
        lambda _payload: {"ok": False, "accepted": False, "role": "search-index", "fallback": True},
    )
    rejected = filesystem_search.unindex_root(str(tmp_path))
    assert rejected == {"root": str(tmp_path), "ok": False, "refreshing_elsewhere": False}


def test_listing_reports_stage_timings_without_exposing_entry_names(tmp_path):
    (tmp_path / "child").mkdir()
    (tmp_path / "private-name.txt").write_text("content\n", encoding="utf-8")
    performance_details = {}

    payload = filesystem.list_directory(
        str(tmp_path),
        performance_details=performance_details,
    )

    assert {entry["name"] for entry in payload["entries"]} == {"child", "private-name.txt"}
    assert {
        "validate_ms",
        "scan_ms",
        "scan_open_ms",
        "scan_iterate_ms",
        "scan_resolve_ms",
        "scan_secret_filter_ms",
        "scan_child_context_ms",
        "scan_child_open_ms",
        "scan_child_info_ms",
        "entry_loop_ms",
        "entry_lstat_ms",
        "symlink_stat_ms",
        "repo_probe_ms",
        "repo_info_ms",
        "identity_ms",
        "sort_ms",
        "assemble_ms",
        "entry_count",
        "repo_count",
        "repo_deferred_count",
    } <= performance_details.keys()
    assert all(value >= 0 for value in performance_details.values())
    assert performance_details["entry_count"] == 2
    assert "private-name.txt" not in json.dumps(performance_details, sort_keys=True)


def test_index_secret_filter_avoids_realpath_for_ordinary_walk_entries(monkeypatch, tmp_path):
    # Index walks do not follow symlinks, so their secret filter can use the
    # lexical policy and avoid one resolve() syscall per candidate.
    paths = filesystem.paths
    paths._secret_exact_paths()
    paths._secret_directories()
    monkeypatch.setattr(paths, "_normalized_scope_path", lambda _path: (_ for _ in ()).throw(AssertionError("ordinary index entries must not resolve")))

    assert paths._path_is_secret(tmp_path / "ordinary.txt", resolve=False) is False
    assert paths._path_is_secret(tmp_path / ".ssh" / "id_rsa", resolve=False) is True


def test_normalized_absolute_containment_matches_pathlib_oracle():
    paths = filesystem.paths
    alias_path = Path("/home/keivenc/dev").resolve(strict=False)
    alias_root = Path("/nfs/keivenc").resolve(strict=False)
    corpus = (
        (Path("/tmp"), Path("/")),
        (Path("/tmp"), Path("//")),
        (Path("/home/keivenc/.ssh"), Path("/home/keivenc/.ssh")),
        (Path("/home/keivenc/.ssh/id_ed25519"), Path("/home/keivenc/.ssh")),
        (Path("/home/keivenc/.sshx"), Path("/home/keivenc/.ssh")),
        (Path("/tmp/root/../outside"), Path("/tmp/root")),
        (Path("/tmp//root/child/"), Path("/tmp/root/")),
        (Path("tests/../tests/test_filesystem.py"), Path(".")),
        (alias_path, alias_root),
    )

    for candidate, root in corpus:
        assert paths._normalized_absolute_path_is_within(candidate, root) is paths._path_is_within(candidate, root)


def test_secret_classifier_matches_retained_oracle_over_adversarial_corpus(tmp_path):
    paths = filesystem.paths
    repeated_separator = Path(f"{tmp_path}//ordinary///child.txt")
    candidates = (
        tmp_path / "ordinary.txt",
        tmp_path / ".ssh" / "id_ed25519",
        tmp_path / ".sshx" / "not-secret.txt",
        tmp_path / ".config" / "gh" / "hosts.yml",
        tmp_path / ".config" / "ghx" / "hosts.yml",
        tmp_path / ".cache" / "huggingface" / "token",
        tmp_path / "safe" / ".." / ".gnupg" / "key",
        repeated_separator,
        Path("."),
        Path("..") / ".aws" / "credentials",
        Path("/home/keivenc/dev").resolve(strict=False),
    )

    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        assert paths._path_is_secret(candidate, resolve=False) is paths._path_is_secret_reference(candidate, resolve=False)
        assert paths._path_is_secret(candidate, resolved=resolved) is paths._path_is_secret_reference(candidate, resolved=resolved)
        assert paths._path_is_secret(candidate) is paths._path_is_secret_reference(candidate)


def test_secret_classifier_avoids_generic_pathlib_containment(monkeypatch, tmp_path):
    paths = filesystem.paths
    candidate = tmp_path / "ordinary.txt"
    resolved = candidate.resolve(strict=False)
    paths._compiled_secret_policy()
    monkeypatch.setattr(paths, "_path_is_within", lambda *_args: (_ for _ in ()).throw(AssertionError("hot classifier used Path.relative_to")))

    assert paths._path_is_secret(candidate, resolved=resolved) is False


def test_secret_policy_generation_and_resolution_mode_survive_symlink_retargets(monkeypatch, tmp_path):
    paths = filesystem.paths
    fake_home = tmp_path / "home"
    first_secret_target = tmp_path / "first-secret-target"
    second_secret_target = tmp_path / "second-secret-target"
    safe_target = tmp_path / "safe-target"
    for directory in (fake_home, first_secret_target, second_secret_target, safe_target):
        directory.mkdir()
    secret_alias = fake_home / ".ssh"
    secret_alias.symlink_to(first_secret_target, target_is_directory=True)
    candidate = tmp_path / "candidate"
    candidate.symlink_to(safe_target, target_is_directory=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))
    paths.invalidate_path_policy_caches()

    first_generation = paths._compiled_secret_policy().generation
    assert paths._path_is_secret(candidate, resolve=False) is False
    assert paths._path_is_secret(candidate) is False

    candidate.unlink()
    candidate.symlink_to(second_secret_target, target_is_directory=True)
    assert paths._path_is_secret(candidate, resolve=False) is False
    assert paths._path_is_secret(candidate) is False

    secret_alias.unlink()
    secret_alias.symlink_to(second_secret_target, target_is_directory=True)
    paths.invalidate_path_policy_caches()

    assert paths._compiled_secret_policy().generation > first_generation
    assert paths._path_is_secret(candidate, resolve=False) is False
    assert paths._path_is_secret(candidate) is True


def test_configured_root_generation_rejects_inflight_stale_cache_fill(monkeypatch, tmp_path):
    paths = filesystem.paths
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    alias = tmp_path / "root-alias"
    alias.symlink_to(first, target_is_directory=True)
    monkeypatch.setenv(paths.FS_ROOTS_ENV, str(alias))
    paths.invalidate_path_policy_caches()
    original_normalize = paths._normalized_scope_path
    old_resolution_ready = threading.Event()
    release_old_resolution = threading.Event()

    def pause_old_alias_resolution(path):
        result = original_normalize(path)
        if path == alias and result == first:
            old_resolution_ready.set()
            assert release_old_resolution.wait(timeout=2)
        return result

    monkeypatch.setattr(paths, "_normalized_scope_path", pause_old_alias_resolution)
    stale_result = []
    worker = threading.Thread(target=lambda: stale_result.append(paths._configured_fs_roots()))
    worker.start()
    assert old_resolution_ready.wait(timeout=2)
    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)
    paths.invalidate_path_policy_caches()
    release_old_resolution.set()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert stale_result == [(first,)]
    assert paths._configured_fs_roots() == (second,)


def test_secret_policy_generation_rejects_inflight_stale_cache_fill(monkeypatch, tmp_path):
    paths = filesystem.paths
    fake_home = tmp_path / "home"
    first = tmp_path / "first-secret"
    second = tmp_path / "second-secret"
    for directory in (fake_home, first, second):
        directory.mkdir()
    secret_alias = fake_home / ".ssh"
    secret_alias.symlink_to(first, target_is_directory=True)
    candidate = second / "generic-key"
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))
    paths.invalidate_path_policy_caches()
    original_normalize = paths._normalized_scope_path
    old_resolution_ready = threading.Event()
    release_old_resolution = threading.Event()

    def pause_old_secret_resolution(path):
        result = original_normalize(path)
        if path == secret_alias and result == first:
            old_resolution_ready.set()
            assert release_old_resolution.wait(timeout=2)
        return result

    monkeypatch.setattr(paths, "_normalized_scope_path", pause_old_secret_resolution)
    stale_policy = []
    worker = threading.Thread(target=lambda: stale_policy.append(paths._compiled_secret_policy()))
    worker.start()
    assert old_resolution_ready.wait(timeout=2)
    secret_alias.unlink()
    secret_alias.symlink_to(second, target_is_directory=True)
    paths.invalidate_path_policy_caches()
    release_old_resolution.set()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert str(first) in stale_policy[0].secret_directories
    assert paths._path_is_secret(candidate, resolved=candidate) is True


def test_secret_classifier_matches_oracle_over_generated_path_shapes(tmp_path):
    paths = filesystem.paths
    components = ("safe", ".ssh", ".sshx", ".config", "gh", "ghx", ".cache", "huggingface", "token", "..")
    for size in (1, 2, 3):
        for parts in itertools.product(components, repeat=size):
            candidate = tmp_path.joinpath(*parts)
            assert paths._path_is_secret(candidate, resolve=False) is paths._path_is_secret_reference(candidate, resolve=False)


def test_listing_does_not_cache_stale_identity_after_inode_replacement(tmp_path):
    target = tmp_path / "replace.txt"
    target.write_text("old\n", encoding="utf-8")
    before = {entry["name"]: entry for entry in filesystem.list_directory(str(tmp_path))["entries"]}["replace.txt"]
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("new\n", encoding="utf-8")
    replacement.replace(target)

    after = {entry["name"]: entry for entry in filesystem.list_directory(str(tmp_path))["entries"]}["replace.txt"]

    assert before["file_id"] != after["file_id"]
    assert after["realpath"] == str(target)


def test_filesystem_mutation_invalidates_canonical_security_policy(monkeypatch, tmp_path):
    invalidations: list[None] = []
    monkeypatch.setattr(filesystem.paths, "invalidate_path_policy_caches", lambda: invalidations.append(None))

    filesystem.write_file(str(tmp_path / "created.txt"), "created\n")

    assert invalidations == [None]


def test_list_directory_eagerly_returns_git_repo_info_by_default(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "checkout", "-b", "feature/repo-row")

    payload = filesystem.list_directory(str(tmp_path))

    entries = {entry["name"]: entry for entry in payload["entries"]}
    assert entries["repo"]["is_repo"] is True
    assert entries["repo"]["repo"]["root"] == str(repo)
    assert entries["repo"]["repo"]["name"] == "repo"
    assert entries["repo"]["repo"]["branch"] == "feature/repo-row"
    assert entries["repo"]["repo"]["detached"] is False


def test_git_repo_info_distinguishes_detached_head_from_unknown_branch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "tracked.txt").write_text("tracked\n")
    git(repo, "add", "tracked.txt")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial")
    git(repo, "checkout", "--detach")

    detached = git_ops.git_repo_info(repo, include_status=False)

    assert detached["branch"] == ""
    assert detached["detached"] is True
    assert detached["head_sha"] == git(repo, "rev-parse", "HEAD").stdout.strip()

    def failed_git(args, cwd, timeout):
        return subprocess.CompletedProcess(args, 128, "", "Git metadata unavailable")

    monkeypatch.setattr(git_ops, "git", failed_git)
    with git_ops._REPO_INFO_CACHE_LOCK:
        git_ops._REPO_INFO_CACHE.clear()

    unknown = git_ops.git_repo_info(repo, include_status=False)

    assert unknown["branch"] == ""
    assert unknown["detached"] is False
    assert unknown["head_sha"] == ""


def test_git_repo_info_does_not_collide_across_two_repos_sharing_a_recycled_descriptor_number(tmp_path):
    # Forces the real cache-collision bug red: `_REPO_INFO_CACHE` is a process-wide cache keyed
    # by `root = str(repo.expanduser().resolve(strict=False))`. Finder listing passes a pinned
    # `/dev/fd/N` descriptor path here so the existence check stays fd-safe (see `listing.py`'s
    # `inspection_path`). On Darwin, `/dev/fd/N` is a devfs node, not a symlink, so `.resolve()`
    # left `root` as the literal, unresolved `/dev/fd/N/...` string before the fix -- and the OS
    # recycles low fd numbers quickly, so two DIFFERENT repos opened at different times can land
    # on the identical fd number and therefore the identical cache key, serving one repo's cached
    # branch for a completely unrelated repo. This drives that exact collision by hand: open repo
    # A's directory, read its info through the fd-string path, close it, then open repo B's
    # directory (very likely reusing that same low fd number) and prove its info is its own, not
    # a stale hit for repo A.
    with git_ops._REPO_INFO_CACHE_LOCK:
        git_ops._REPO_INFO_CACHE.clear()
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    git(repo_a, "init")
    git(repo_a, "checkout", "-b", "branch-a")
    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    git(repo_b, "init")
    git(repo_b, "checkout", "-b", "branch-b")

    descriptor_a = os.open(repo_a, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        info_a = git_ops.git_repo_info(Path("/dev/fd") / str(descriptor_a), include_status=False)
    finally:
        os.close(descriptor_a)
    descriptor_b = os.open(repo_b, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        info_b = git_ops.git_repo_info(Path("/dev/fd") / str(descriptor_b), include_status=False)
    finally:
        os.close(descriptor_b)

    assert info_a["branch"] == "branch-a"
    assert info_b["branch"] == "branch-b"
    with git_ops._REPO_INFO_CACHE_LOCK:
        cache_keys = {key[0] for key in git_ops._REPO_INFO_CACHE}
    assert str(repo_a.resolve()) in cache_keys, "the cache key must be the resolved real path, not the raw descriptor path"
    assert str(repo_b.resolve()) in cache_keys
    assert not any(key.startswith("/dev/fd/") for key in cache_keys), "a raw fd-string must never become a cache key"


def test_list_directory_explicit_opt_out_probes_repo_markers_without_spawning_git(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    monkeypatch.setattr(
        git_ops,
        "git",
        lambda *_args, **_kwargs: pytest.fail("base directory listing must not spawn Git"),
    )
    payload = filesystem.list_directory(str(tmp_path), include_repo_info=False)

    entries = {entry["name"]: entry for entry in payload["entries"]}
    assert entries["repo"]["repo_info_deferred"] is True
    assert entries["repo"]["is_repo"] is True
    assert "repo" not in entries["repo"]
    assert entries["repo"]["mtime"] > 0


def test_git_repo_info_cache_returns_independent_values_and_watcher_invalidation(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    calls = []

    def fake_git(args, cwd, timeout):
        calls.append(tuple(args))
        if args[:2] == ["symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(args, 0, "main\n", "")
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(git_ops, "git", fake_git)
    with git_ops._REPO_INFO_CACHE_LOCK:
        git_ops._REPO_INFO_CACHE.clear()

    first = git_ops.git_repo_info(repo, include_status=False)
    first["branch"] = "mutated"
    second = git_ops.git_repo_info(repo, include_status=False)
    assert second["branch"] == "main"
    assert len(calls) == 3

    assert metadata.invalidate_git_metadata_paths([repo / "tracked.txt"]) == set()
    third = git_ops.git_repo_info(repo, include_status=False)
    assert third["branch"] == "main"
    assert len(calls) == 6, "watcher-owned invalidation must make Finder recompute"


def _reset_repository_generation(root):
    git_ops._REPOSITORY_GENERATIONS.pop(str(Path(root).expanduser().resolve(strict=False)), None)


def test_private_repository_signature_distinguishes_same_commit_branch_switch_without_git_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "one")

    _reset_repository_generation(repo)
    on_master = git_ops.pinned_repository_generation(str(repo))
    # A second branch at the SAME commit: identical tree, identical OID, different symbolic ref.
    git(repo, "branch", "other")
    git(repo, "checkout", "other")
    on_other = git_ops.pinned_repository_generation(str(repo))

    assert on_master == 1
    assert on_other == 2, "an identical-tree branch switch must change the pinned generation"


def test_private_repository_signature_is_unknown_for_non_repo_and_unborn(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    _reset_repository_generation(plain)
    assert git_ops.pinned_repository_generation(str(plain)) == 0

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    init_repo(unborn)  # a repository with a symbolic HEAD but no commit yet
    _reset_repository_generation(unborn)
    assert git_ops.pinned_repository_generation(str(unborn)) == 0


def test_repository_generation_advances_on_identical_tree_branch_switch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "one")
    _reset_repository_generation(repo)

    first = git_ops.pinned_repository_generation(str(repo))
    assert first == 1
    assert git_ops.pinned_repository_generation(str(repo)) == first, "an unchanged HEAD does not advance the generation"

    git(repo, "branch", "other")
    git(repo, "checkout", "other")
    assert git_ops.pinned_repository_generation(str(repo)) == first + 1, "a same-commit branch switch advances the generation"
    assert git_ops.pinned_repository_generation(str(repo)) == first + 1


def test_repository_generation_isolates_a_malformed_signature_between_tenants(tmp_path):
    tenant_a = tmp_path / "a"
    tenant_b = tmp_path / "b"
    tenant_a.mkdir()
    tenant_b.mkdir()
    # Tenant A is a healthy repository; tenant B is not a repository at all (malformed HEAD).
    init_repo(tenant_a)
    (tenant_a / "file.txt").write_text("a\n", encoding="utf-8")
    git(tenant_a, "add", "file.txt")
    git(tenant_a, "commit", "-m", "one")
    _reset_repository_generation(tenant_a)
    _reset_repository_generation(tenant_b)

    assert git_ops.pinned_repository_generation(str(tenant_b)) == 0, "an unreadable repository holds a null generation"
    assert git_ops.pinned_repository_generation(str(tenant_a)) == 1
    git(tenant_a, "branch", "other")
    git(tenant_a, "checkout", "other")
    # A's real branch switch advances A; B's malformed state neither advances nor is disturbed.
    assert git_ops.pinned_repository_generation(str(tenant_a)) == 2
    assert git_ops.pinned_repository_generation(str(tenant_b)) == 0


def test_repository_generation_holds_through_a_transient_unreadable_head(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _reset_repository_generation(root)
    root_text = str(root.resolve())
    signature = ("master", "c0ffee")
    assert git_ops._advance_repository_generation(root_text, signature) == 1
    # A transient failure to read HEAD is inconclusive: hold the generation.
    assert git_ops._advance_repository_generation(root_text, git_ops.REPOSITORY_SIGNATURE_UNKNOWN) == 1
    # Recovering to the SAME signature must not flap the generation.
    assert git_ops._advance_repository_generation(root_text, signature) == 1
    # A genuine HEAD change after recovery advances exactly once.
    assert git_ops._advance_repository_generation(root_text, ("master", "beefbeef")) == 2


def test_git_repo_info_cache_ttl_is_stable_and_desynchronizes_repo_roots():
    roots = [f"/workspace/repo-{index}" for index in range(64)]
    first = [git_ops._repo_info_cache_ttl_seconds(root) for root in roots]
    second = [git_ops._repo_info_cache_ttl_seconds(root) for root in roots]

    assert first == second
    assert min(first) >= git_ops.REPO_INFO_CACHE_SECONDS * 0.5
    assert max(first) <= git_ops.REPO_INFO_CACHE_SECONDS * 1.5
    assert max(first) - min(first) > git_ops.REPO_INFO_CACHE_SECONDS * 0.75


def test_git_repo_info_cache_applies_each_repo_ttl_independently(tmp_path, monkeypatch):
    repo_short = tmp_path / "repo-short"
    repo_long = tmp_path / "repo-long"
    repo_short.mkdir()
    repo_long.mkdir()
    init_repo(repo_short)
    init_repo(repo_long)
    now = [100.0]
    calls = []

    def fake_git(args, cwd, timeout):
        calls.append((tuple(args), cwd, timeout))
        if args[:2] == ["symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(args, 0, "main\n", "")
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(git_ops.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(git_ops, "git", fake_git)
    monkeypatch.setattr(
        git_ops,
        "_repo_info_cache_ttl_seconds",
        lambda root: 10.0 if root.endswith("repo-short") else 20.0,
    )
    with git_ops._REPO_INFO_CACHE_LOCK:
        git_ops._REPO_INFO_CACHE.clear()

    git_ops.git_repo_info(repo_short, include_status=False)
    git_ops.git_repo_info(repo_long, include_status=False)
    assert len(calls) == 6

    now[0] = 115.0
    git_ops.git_repo_info(repo_short, include_status=False)
    git_ops.git_repo_info(repo_long, include_status=False)
    assert len(calls) == 9, "only the short-TTL repository should revalidate"


@pytest.mark.parametrize(("timeout", "expected_call_count"), [(0.001, 0), (1.0, 4)])
def test_git_repo_info_bounds_tiny_budgets_and_subprocess_timeouts(tmp_path, monkeypatch, timeout, expected_call_count):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    calls = []

    def timeout_git(args, cwd, timeout):
        calls.append((args, cwd, timeout))
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(git_ops.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(git_ops, "git", timeout_git)
    with git_ops._REPO_INFO_CACHE_LOCK:
        git_ops._REPO_INFO_CACHE.clear()

    info = git_ops.git_repo_info(repo, include_status=False, timeout=timeout)

    assert len(calls) == expected_call_count
    assert info["branch"] == ""
    assert info["detached"] is False
    assert info["head_sha"] == ""
    assert info["upstream"] == ""
    assert info["ahead"] == 0
    assert info["behind"] == 0


def test_list_directory_bounds_slow_repo_enrichment_without_dropping_repo_row(tmp_path, monkeypatch):
    slow = tmp_path / "a-slow"
    fast = tmp_path / "z-fast"
    for repo in (slow, fast):
        repo.mkdir()
        git(repo, "init")
    now = [100.0]

    def clock():
        return now[0]

    def slow_git(args, cwd, timeout):
        now[0] += timeout
        return subprocess.CompletedProcess(args, 124, "", "timed out")

    monkeypatch.setattr(filesystem.listing.time, "monotonic", clock)
    monkeypatch.setattr(git_ops.time, "monotonic", clock)
    monkeypatch.setattr(git_ops, "git", slow_git)
    with git_ops._REPO_INFO_CACHE_LOCK:
        git_ops._REPO_INFO_CACHE.clear()

    started = time.perf_counter()
    entries = {entry["name"]: entry for entry in filesystem.list_directory(str(tmp_path))["entries"]}
    assert time.perf_counter() - started < 0.25
    repo_rows = [entries["a-slow"], entries["z-fast"]]
    assert all(entry["is_repo"] is True for entry in repo_rows)
    assert sum("repo" in entry for entry in repo_rows) == 1
    assert sum(entry.get("repo_info_deferred") is True for entry in repo_rows) == 1


def test_list_directory_returns_fifty_repo_rows_without_spawning_git(tmp_path, monkeypatch):
    repos = [tmp_path / f"repo-{index:02d}" for index in range(50)]
    for repo in repos:
        repo.mkdir()
        marker = repo / ".git"
        marker.mkdir()
        (marker / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    monkeypatch.setattr(
        git_ops,
        "git",
        lambda *_args, **_kwargs: pytest.fail("base directory listing must not spawn Git"),
    )
    started = time.perf_counter()
    entries = {
        entry["name"]: entry
        for entry in filesystem.list_directory(str(tmp_path), include_repo_info=False)["entries"]
    }
    assert time.perf_counter() - started < 0.25
    directory_rows = [entries[repo.name] for repo in repos]
    assert all(entry.get("repo_info_deferred") is True for entry in directory_rows)
    assert all(entry["is_repo"] is True for entry in directory_rows)
    assert all("repo" not in entry for entry in directory_rows)


def test_list_directory_allows_root_by_default(monkeypatch):
    monkeypatch.delenv(filesystem.FS_ROOTS_ENV, raising=False)
    payload = filesystem.list_directory("/")
    assert payload["path"] == "/"
    assert payload["parent"] is None


def test_filesystem_allowlist_env_can_narrow_scope(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory("/")
    assert info.value.status == 403


def test_filesystem_entrypoints_reject_outside_root_through_paths_validator(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    outside_file = outside / "note.txt"
    outside_file.write_text("outside\n", encoding="utf-8")
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(allowed))
    calls = []
    original = filesystem.paths._ensure_path_allowed

    def tracking_validator(path, *, resolved=None):
        calls.append(path)
        return original(path, resolved=resolved)

    monkeypatch.setattr(filesystem.paths, "_ensure_path_allowed", tracking_validator)

    cases = [
        ("listing", lambda: filesystem.list_directory(str(outside))),
        ("read", lambda: filesystem.read_file(str(outside_file))),
        ("write", lambda: filesystem.write_file(str(outside / "new.txt"), "x")),
        ("search", lambda: filesystem.search_files(str(outside), "note")),
        ("git", lambda: filesystem.diff_file(str(outside_file))),
    ]
    for name, action in cases:
        before = len(calls)
        with pytest.raises(FilesystemError) as info:
            action()
        assert info.value.status == 403
        assert len(calls) >= before + 1, name


def test_filesystem_blocks_home_secret_paths(monkeypatch, tmp_path):
    home = tmp_path / "home"
    secret = home / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(home))
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(secret))
    assert info.value.status == 403


def test_list_directory_hides_secret_entries(tmp_path):
    visible = tmp_path / "visible.txt"
    visible.write_text("ok", encoding="utf-8")
    for path in (
        tmp_path / ".ssh" / "id_rsa",
        tmp_path / ".config" / "gh" / "hosts.yml",
        tmp_path / ".config" / "git" / "config",
        tmp_path / ".config" / "gitlab-token",
        tmp_path / ".cache" / "huggingface" / "token",
        tmp_path / ".docker" / "config.json",
        tmp_path / ".ngc" / "config",
        tmp_path / ".netrc",
        tmp_path / ".npmrc",
        tmp_path / ".pypirc",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret\n", encoding="utf-8")

    names = {entry["name"] for entry in filesystem.list_directory(str(tmp_path))["entries"]}

    assert "visible.txt" in names
    assert ".ssh" not in names
    assert ".netrc" not in names
    assert ".npmrc" not in names
    assert ".pypirc" not in names
    config_names = {entry["name"] for entry in filesystem.list_directory(str(tmp_path / ".config"))["entries"]}
    assert "gh" not in config_names
    assert "git" not in config_names
    assert "gitlab-token" not in config_names
    docker_names = {entry["name"] for entry in filesystem.list_directory(str(tmp_path / ".docker"))["entries"]}
    ngc_names = {entry["name"] for entry in filesystem.list_directory(str(tmp_path / ".ngc"))["entries"]}
    assert "config.json" not in docker_names
    assert "config" not in ngc_names


def test_filesystem_blocks_symlink_escape_from_allowed_root(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret\n", encoding="utf-8")
    link = allowed / "link.txt"
    link.symlink_to(target)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(allowed))

    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(link))

    assert info.value.status == 403
    entries = {entry["name"]: entry for entry in filesystem.list_directory(str(allowed))["entries"]}
    assert "link.txt" not in entries


def test_filesystem_blocks_exact_secret_files(monkeypatch, tmp_path):
    home = tmp_path / "home"
    config_dir = home / ".config" / "yolomux"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(filesystem, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(filesystem, "AUTH_CONFIG_PATH", config_dir / "auth.yaml")
    monkeypatch.setattr(filesystem, "AUTH_COOKIE_SECRET_PATH", config_dir / "auth-cookie-secret")
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(home))
    secret_paths = [
        config_dir / "auth.yaml",
        config_dir / "auth-cookie-secret",
        home / ".config" / "gitlab-token",
        home / ".cache" / "huggingface" / "token",
        home / ".docker" / "config.json",
        home / ".ngc" / "config",
    ]
    for secret in secret_paths:
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("secret\n", encoding="utf-8")

    for secret in secret_paths:
        with pytest.raises(FilesystemError) as info:
            filesystem.read_file(str(secret))
        assert info.value.status == 403


def test_filesystem_blocks_secret_patterns_outside_home(tmp_path):
    secret_paths = [
        tmp_path / ".ssh" / "id_rsa",
        tmp_path / ".gnupg" / "private-keys-v1.d" / "key",
        tmp_path / ".aws" / "credentials",
        tmp_path / ".azure" / "accessTokens.json",
        tmp_path / ".kube" / "config",
        tmp_path / ".config" / "gh" / "hosts.yml",
        tmp_path / ".config" / "git" / "credentials",
        tmp_path / ".config" / "gitlab-token",
        tmp_path / ".cache" / "huggingface" / "token",
        tmp_path / ".docker" / "config.json",
        tmp_path / ".ngc" / "config",
        tmp_path / ".netrc",
        tmp_path / ".npmrc",
        tmp_path / ".pypirc",
    ]
    for path in secret_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret\n", encoding="utf-8")

    for path in secret_paths:
        with pytest.raises(FilesystemError) as info:
            filesystem.read_file(str(path))
        assert info.value.status == 403


def test_filesystem_allows_non_secret_docker_like_directories(tmp_path):
    compose = tmp_path / ".docker" / "compose.yaml"
    compose.parent.mkdir()
    compose.write_text("services: {}\n", encoding="utf-8")

    payload = filesystem.read_file(str(compose))

    assert payload["content"] == "services: {}\n"


def test_search_files_skips_secret_paths(tmp_path):
    visible = tmp_path / "visible-target.txt"
    visible.write_text("target\n", encoding="utf-8")
    secrets = [
        tmp_path / ".ssh" / "secret-target.txt",
        tmp_path / ".config" / "gh" / "secret-target.txt",
        tmp_path / ".config" / "gitlab-token",
        tmp_path / ".docker" / "config.json",
        tmp_path / ".ngc" / "config",
        tmp_path / ".netrc",
    ]
    for path in secrets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("target\n", encoding="utf-8")

    payload = filesystem.search_files(str(tmp_path), "target", 50, recursive=True)
    paths = {item["path"] for item in payload["files"]}

    assert str(visible) in paths
    assert all(str(path) not in paths for path in secrets)


def test_read_raw_streams_preview_media_with_mime_type(tmp_path):
    cases = [
        ("tiny.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("photo.avif", b"\x00\x00\x00 ftypavif", "image/avif"),
        ("spec.pdf", b"%PDF-1.7\n", "application/pdf"),
        ("spec", b"%PDF-1.7\n", "application/pdf"),
        ("renamed.bin", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("photo.tiff", b"II*\x00rest", "image/tiff"),
        ("photo.heic", b"\x00\x00\x00 ftypheic", "image/heic"),
        ("sound.mp3", b"ID3\x03\x00\x00", "audio/mpeg"),
        ("sound.aac", b"not-sniffed", "audio/aac"),
        ("movie.mp4", b"\x00\x00\x00 ftypmp42", "video/mp4"),
        ("book.xlsx", b"PK\x03\x04", "application/zip"),
        ("data.parquet", b"PAR1data", "application/vnd.apache.parquet"),
        ("data.sqlite", b"SQLite format 3\x00", "application/vnd.sqlite3"),
        ("archive.zip", b"PK\x03\x04", "application/zip"),
    ]
    for name, data, expected_mime in cases:
        target = tmp_path / name
        target.write_bytes(data)

        payload, mime = filesystem.read_raw(str(target))

        assert payload == data
        assert mime == expected_mime


def test_path_info_returns_sniffed_preview_mime_for_misleading_extension(tmp_path):
    target = tmp_path / "renamed.bin"
    target.write_bytes(b"\x89PNG\r\n\x1a\npayload")

    result = filesystem.path_info(str(target))

    assert result["size"] == target.stat().st_size
    assert result["preview_mime"] == "image/png"
    assert result["diff_capable"] is False


def test_package_path_info_normalizes_required_stat_permission_failure(monkeypatch):
    monkeypatch.setattr(
        filesystem.io_ops.paths,
        "_open_resolved_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError(13, "permission denied", "/restricted/item")
        ),
    )

    with pytest.raises(FilesystemError) as info:
        filesystem.path_info("/restricted/item")

    assert info.value.status == 403
    assert info.value.message_key == "fs.error.operationFailed"
    assert "permission denied" in info.value.diagnostic


def test_package_list_and_search_normalize_raw_os_failures(monkeypatch, tmp_path):
    def denied_list(_descriptor, *, resolved_parent, performance_details=None, include_repo_info=True, requested_path=None, operation="list_directory"):
        del resolved_parent, include_repo_info, requested_path, operation
        raise PermissionError(13, "list denied", str(tmp_path))

    monkeypatch.setattr(filesystem.listing, "_visible_directory_names", denied_list)
    with pytest.raises(FilesystemError) as listed:
        filesystem.list_directory(str(tmp_path))
    assert listed.value.status == 403
    assert "list denied" in listed.value.diagnostic

    monkeypatch.setattr(filesystem.search.os, "listdir", lambda _path: (_ for _ in ()).throw(OSError("search failed")))
    with pytest.raises(FilesystemError) as searched:
        filesystem.search_files(str(tmp_path), query="item")
    assert searched.value.status == 500
    assert "search failed" in searched.value.diagnostic


def test_package_walk_archive_and_write_normalize_raw_os_failures(monkeypatch, tmp_path):
    original_walk = filesystem_paths.walk_directory

    def denied_walk(_descriptor, **_kwargs):
        raise PermissionError(13, "walk denied", str(tmp_path))

    monkeypatch.setattr(filesystem_paths, "walk_directory", denied_walk)
    with pytest.raises(FilesystemError) as walked:
        filesystem.count_directory_files(str(tmp_path))
    assert walked.value.status == 403
    assert "walk denied" in walked.value.diagnostic
    monkeypatch.setattr(filesystem_paths, "walk_directory", original_walk)

    monkeypatch.setattr(filesystem.io_ops.zipfile.ZipFile, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("archive failed")))
    with pytest.raises(FilesystemError) as archived:
        filesystem.zip_directory(str(tmp_path))
    assert archived.value.status == 500
    assert "archive failed" in archived.value.diagnostic

    write_target = tmp_path / "write.txt"
    monkeypatch.setattr(
        filesystem.io_ops.os,
        "ftruncate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError(13, "write denied", str(write_target))
        ),
    )
    with pytest.raises(FilesystemError) as written:
        filesystem.write_file(str(write_target), "content")
    assert written.value.status == 403
    assert "write denied" in written.value.diagnostic


def test_walk_directory_closes_open_children_when_directory_filter_raises(tmp_path):
    (tmp_path / "a-opened").mkdir()
    (tmp_path / "b-raises").mkdir()

    def include_directory(relative):
        if relative == Path("b-raises"):
            raise RuntimeError("filter failed")
        return True

    unrelated_fd = os.open(os.devnull, os.O_RDONLY)
    root_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        assert _open_descriptors_beneath(tmp_path) == {str(root_fd): str(tmp_path)}
        with pytest.raises(RuntimeError, match="filter failed"):
            next(
                filesystem_paths.walk_directory(
                    root_fd,
                    include_directory=include_directory,
                    requested_root=tmp_path,
                    resolved_root=tmp_path,
                )
            )
    finally:
        os.close(root_fd)
    try:
        assert _open_descriptors_beneath(tmp_path) == {}
    finally:
        os.close(unrelated_fd)


def test_list_directory_repeated_success_closes_scan_descriptor(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()

    before = _open_descriptors_beneath(tmp_path)

    for _ in range(25):
        filesystem.list_directory(str(tmp_path))

    assert _open_descriptors_beneath(tmp_path) == before


def test_authorization_observer_reports_recursive_and_namespace_descriptor_boundaries(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    child = nested / "child.txt"
    child.write_text("safe\n", encoding="utf-8")
    events = []

    class Observer:
        def name_observed(self, operation, requested_path):
            events.append(("name", operation, requested_path))

        def authority_pinned(self, operation, requested_path):
            events.append(("authority", operation, requested_path))

    with filesystem_paths.observe_authorization(Observer()):
        filesystem.count_directory_files(str(tmp_path))
        filesystem.rename_path(str(child), "renamed.txt")

    renamed = nested / "renamed.txt"
    for operation, requested_path in (
        ("count_directory_files", nested),
        ("count_directory_files", child),
        ("rename_path", child),
        ("rename_path", renamed),
    ):
        assert events.index(("name", operation, requested_path)) < events.index(
            ("authority", operation, requested_path)
        )

    observed_count = len(events)
    filesystem.read_file(str(renamed))
    assert len(events) == observed_count


def test_filesystem_implementations_leave_os_error_normalization_to_package_facade():
    source_root = Path(filesystem.__file__).parent
    implementations = "\n".join((source_root / name).read_text(encoding="utf-8") for name in ("io_ops.py", "listing.py", "search.py"))
    package_source = (source_root / "__init__.py").read_text(encoding="utf-8")

    assert "FilesystemError.os_error" not in implementations
    assert "_fs_io_errors" not in implementations
    assert "_fs_io_errors" not in package_source
    assert "os.walk(" not in implementations
    assert "os.fwalk(" not in implementations
    assert implementations.count("paths.walk_directory(") == 3


def test_delete_path_refuses_configured_root(monkeypatch, tmp_path):
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(tmp_path))
    with pytest.raises(FilesystemError) as info:
        filesystem.delete_path(str(tmp_path))
    assert info.value.status == 403
    assert tmp_path.exists()


def test_list_directory_rejects_relative():
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory("relative/path")
    assert info.value.status == 400


def test_list_directory_rejects_crlf_and_nul():
    for bad in ("/etc/hosts\n", "/etc\x00", "/etc/hosts\r"):
        with pytest.raises(FilesystemError):
            filesystem.list_directory(bad)


def test_list_directory_missing(tmp_path):
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory(str(tmp_path / "does-not-exist"))
    assert info.value.status == 404
    assert info.value.message_key == "common.pathNotFound"
    assert info.value.message_params == {"path": str(tmp_path / "does-not-exist")}
    assert info.value.payload(path=str(tmp_path / "does-not-exist"))["user_message"] == {
        "key": "common.pathNotFound",
        "params": {"path": str(tmp_path / "does-not-exist")},
        "fallback": f"path not found: {tmp_path / 'does-not-exist'}",
    }


def test_list_directory_not_a_dir(tmp_path):
    file_path = tmp_path / "f.txt"
    file_path.write_text("x")
    with pytest.raises(FilesystemError) as info:
        filesystem.list_directory(str(file_path))
    assert info.value.status == 400


def test_list_directory_caps_large_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(filesystem, "MAX_DIRECTORY_ENTRIES", 2)
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    payload = filesystem.list_directory(str(tmp_path))

    assert payload["truncated"] is True
    assert payload["entry_limit"] == 2
    assert len(payload["entries"]) == 2
    assert {entry["name"] for entry in payload["entries"]}.issubset({"a.txt", "b.txt", "c.txt"})


def test_list_directory_sorts_dirs_first_then_case_insensitive_name(tmp_path):
    # Entries come back sorted dirs-first, then case-INsensitively by name (from the assembled entry
    # list, not raw os.listdir order). Mixed case + mixed kind exercises both sort keys.
    (tmp_path / "Zebra").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta.txt").write_text("b", encoding="utf-8")
    (tmp_path / "Apple.txt").write_text("a", encoding="utf-8")

    names = [entry["name"] for entry in filesystem.list_directory(str(tmp_path))["entries"]]

    assert names == ["alpha", "Zebra", "Apple.txt", "beta.txt"]


def test_search_files_returns_fuzzy_matches_and_skips_heavy_dirs_inside_repo(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hello_x_and_y.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hello_x_and_y.js").write_text("bad\n", encoding="utf-8")

    payload = filesystem.search_files(str(tmp_path), "xy", 20)

    assert payload["root"] == str(tmp_path)
    paths = [item["relative_path"] for item in payload["files"]]
    assert "src/hello_x_and_y.py" in paths
    assert "node_modules/hello_x_and_y.js" not in paths
    # search hits carry realpath + size so the client can fold symlink/mirror duplicates.
    hit = next(item for item in payload["files"] if item["relative_path"] == "src/hello_x_and_y.py")
    assert hit["realpath"] == os.path.realpath(str(tmp_path / "src" / "hello_x_and_y.py"))
    assert hit["size"] == len("print('ok')\n")


def test_search_ranking_does_not_span_absolute_root_prefix_into_filename():
    path = Path("/tmp/target-04") / "dir" / "target-00949.py"

    sort_key = filesystem._search_entry_sort_key(path, "dir/target-00949.py", ["target-04999"])

    assert sort_key is None


def test_search_files_doit_queries_require_project_doc_prefix(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "DOIT.57.md").write_text("# doit\n", encoding="utf-8")
    (tmp_path / "frontend-crates").mkdir()
    (tmp_path / "frontend-crates" / "DOIT.parser-performance-v2-audit.md").write_text("# audit\n", encoding="utf-8")
    (tmp_path / "static_src" / "js" / "yolomux").mkdir(parents=True)
    (tmp_path / "static_src" / "js" / "yolomux" / "75_dockview_layout.js").write_text("export {}\n", encoding="utf-8")

    broad = filesystem.search_files(str(tmp_path), "DOIT", 20)
    exactish = filesystem.search_files(str(tmp_path), "doit57", 20)

    broad_paths = {item["relative_path"] for item in broad["files"]}
    exactish_paths = {item["relative_path"] for item in exactish["files"]}
    assert "DOIT.57.md" in broad_paths
    assert "frontend-crates/DOIT.parser-performance-v2-audit.md" in broad_paths
    assert "static_src/js/yolomux/75_dockview_layout.js" not in broad_paths
    assert exactish_paths == {"DOIT.57.md"}


def test_search_files_non_repo_root_stays_shallow_but_indexes_child_repos(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    (root / "top.txt").write_text("top\n", encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes" / "nested.md").write_text("too deep\n", encoding="utf-8")
    (root / ".cache").mkdir()
    (root / ".cache" / "cache.txt").write_text("skip\n", encoding="utf-8")
    repo = root / "project"
    repo.mkdir()
    git(repo, "init")
    (repo / "src").mkdir()
    (repo / "src" / "deep.py").write_text("print('repo')\n", encoding="utf-8")

    payload = filesystem.search_files(str(root), "", 50)

    paths = {item["relative_path"] for item in payload["files"]}
    assert "top.txt" in paths
    assert "project/src/deep.py" in paths
    assert "notes/nested.md" not in paths
    assert ".cache/cache.txt" not in paths
    assert "project/.git/HEAD" not in paths


def test_search_files_recursive_walks_indexed_non_repo_root_but_skips_heavy_dirs(tmp_path):
    root = tmp_path / "indexed"
    root.mkdir()
    (root / "nested" / "deeper").mkdir(parents=True)
    (root / "nested" / "deeper" / "target_file.py").write_text("print('hit')\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "target_file.js").write_text("skip\n", encoding="utf-8")

    payload = filesystem.search_files(str(root), "target", 20, recursive=True)

    paths = {item["relative_path"] for item in payload["files"]}
    assert "nested/deeper/target_file.py" in paths
    assert "node_modules/target_file.js" not in paths


def test_search_files_canonicalizes_symlink_root(tmp_path):
    real_root = tmp_path / "dynamo" / "notes"
    real_root.mkdir(parents=True)
    (real_root / "DIS-2218.md").write_text("# notes\n", encoding="utf-8")
    link_root = tmp_path / "notes"
    link_root.symlink_to(real_root, target_is_directory=True)

    payload = filesystem.search_files(str(link_root), "DIS-2218", 20, recursive=True)

    assert payload["root"] == str(real_root)
    assert payload["root_realpath"] == os.path.realpath(real_root)
    assert [item["path"] for item in payload["files"]] == [str(real_root / "DIS-2218.md")]
    assert payload["files"][0]["realpath"] == os.path.realpath(real_root / "DIS-2218.md")


def test_search_files_does_not_serve_startup_metadata_before_first_directory_publish(monkeypatch, tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    target = root / "DIS-2218.md"
    target.write_text("# notes\n", encoding="utf-8")
    file_index = filesystem_search.file_index
    file_index.clear_memory_indexes()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    startup_claimed = threading.Event()
    release_build = threading.Event()

    def held_runner(build_root, skip_dirs, **options):
        build = bfs_index.ProgressiveBuild(
            build_root,
            skip_dirs,
            exclude_path=options.get("exclude_path"),
            exclude_signature=options.get("exclude_signature", ""),
            generation=options["generation"],
            operation=options.get("operation", ""),
            tombstone_identity=options.get("tombstone_identity"),
        )
        with build:
            assert build.enqueue_startup()
            startup_claimed.set()
            assert release_build.wait(10)
            build.run()
        return True

    real_search_disk_index = file_index.search_disk_index

    def search_after_startup_claim(*args, **kwargs):
        assert startup_claimed.wait(10)
        return real_search_disk_index(*args, **kwargs)

    monkeypatch.setattr(file_index, "_BFS_FULL_BUILD_RUNNER", held_runner)
    monkeypatch.setattr(file_index, "search_disk_index", search_after_startup_claim)
    try:
        payload = filesystem.search_files(str(root), "DIS-2218", 20, recursive=True)
        assert [item["path"] for item in payload["files"]] == [str(target)]
        assert payload["files"][0]["realpath"] == str(target)
        policy = filesystem_search._search_index_policy(root)
        startup_metadata = file_index._authoritative_store_metadata(root)
        assert startup_metadata is not None
        assert file_index._row_serving_snapshot_metadata(startup_metadata) is False
        assert file_index._load_disk(root, policy["skip_dirs"], policy["exclude_signature"]) is None
    finally:
        release_build.set()
        with file_index._REGISTRY_LOCK:
            index = file_index._REGISTRY[str(root)]
        assert index.completion.wait(10)

    def live_walk_forbidden(*_args, **_kwargs):
        raise AssertionError("a published layer-one snapshot must own the search result")

    monkeypatch.setattr(filesystem_search, "_search_full_tree", live_walk_forbidden)
    published = filesystem.search_files(str(root), "DIS-2218", 20, recursive=True)
    assert [item["path"] for item in published["files"]] == [str(target)]
    assert published["index_state"] == "ready"
    try:
        policy = filesystem_search._search_index_policy(root)
        published_metadata = file_index._raw_snapshot_metadata(
            root,
            policy["skip_dirs"],
            policy["exclude_signature"],
        )
        assert published_metadata is not None
        assert file_index._row_serving_snapshot_metadata(published_metadata) is True
        assert file_index._load_disk(root, policy["skip_dirs"], policy["exclude_signature"]) is not None
    finally:
        file_index.clear_memory_indexes()


def test_search_files_ranks_exact_filename_above_large_generated_sibling(tmp_path):
    root = tmp_path / "dynamo"
    logs = root / "commits" / "logs"
    target_dir = root / "notes" / "tool-calling" / "DIS-1850__jinja-spike"
    logs.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    for index in range(30):
        (logs / f"ea-1850-{index:02d}-premerge.html").write_text("generated\n", encoding="utf-8")
    target = target_dir / "DIS-1850.md"
    target.write_text("# DIS-1850\n", encoding="utf-8")

    payload = filesystem.search_files(str(root), "DIS-1850", 5, recursive=True)

    assert payload["truncated"] is False
    assert [item["relative_path"] for item in payload["files"]] == [
        "notes/tool-calling/DIS-1850__jinja-spike/DIS-1850.md",
    ]


def test_search_files_does_not_match_segments_outside_the_search_root(tmp_path):
    project = tmp_path / "home" / "keivenc" / "project"
    project.mkdir(parents=True)
    git(project, "init")
    (project / "README.md").write_text("# ok\n", encoding="utf-8")

    payload = filesystem.search_files(str(project), "hokread", 20)

    assert payload["files"] == []


def test_search_files_marks_generated_upload_names(tmp_path):
    git(tmp_path, "init")
    upload = tmp_path / "20260531-001-diagram.png"
    normal = tmp_path / "diagram.png"
    upload.write_bytes(b"png")
    normal.write_bytes(b"png")

    payload = filesystem.search_files(str(tmp_path), "diagram", 20)
    by_name = {item["name"]: item for item in payload["files"]}

    assert by_name["20260531-001-diagram.png"]["uploaded"] is True
    assert by_name["diagram.png"]["uploaded"] is False


def test_search_files_rejects_non_directory(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(FilesystemError) as info:
        filesystem.search_files(str(target), "x")
    assert info.value.status == 400


def test_read_file_returns_text(tmp_path):
    file_path = tmp_path / "note.md"
    file_path.write_text("# hello\n", encoding="utf-8")
    payload = filesystem.read_file(str(file_path))
    assert payload["content"] == "# hello\n"
    assert payload["extension"] == ".md"
    assert payload["is_text_extension"] is True
    assert payload["size"] == file_path.stat().st_size
    assert payload["mtime_ns"] == file_path.stat().st_mtime_ns


def test_read_file_reports_git_tracked(tmp_path):
    # A committed file is tracked; an untracked sibling and a file outside any repo are not.
    # The editor uses this flag to hide its blame/diff buttons for files with no git history.
    init_repo(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-q", "-m", "add")
    untracked = tmp_path / "untracked.txt"
    untracked.write_text("new\n", encoding="utf-8")
    tracked_payload = filesystem.read_file(str(tracked))
    untracked_payload = filesystem.read_file(str(untracked))
    assert tracked_payload["git_tracked"] is True
    assert tracked_payload["git_root"] == str(tmp_path)
    assert tracked_payload["git_has_history"] is False
    assert len(tracked_payload["git_history"]) == 1
    assert untracked_payload["git_root"] == str(tmp_path)
    assert untracked_payload["git_tracked"] is False


def test_read_file_reports_file_level_git_history(tmp_path):
    init_repo(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-q", "-m", "add tracked")
    tracked.write_text("two\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-q", "-m", "update tracked")

    payload = filesystem.read_file(str(tracked))

    assert payload["git_tracked"] is True
    assert payload["git_has_history"] is True
    assert [item["subject"] for item in payload["git_history"]] == ["update tracked", "add tracked"]
    assert all(item["ref"] and item["short"] for item in payload["git_history"])


def test_read_file_outside_repo_is_not_tracked(tmp_path):
    file_path = tmp_path / "loose.txt"
    file_path.write_text("loose\n", encoding="utf-8")
    payload = filesystem.read_file(str(file_path))
    assert payload["git_root"] == ""
    assert payload["git_tracked"] is False


def test_read_file_rejects_binary(tmp_path):
    file_path = tmp_path / "binary.bin"
    file_path.write_bytes(b"abc\x00def")
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(file_path))
    assert info.value.status == 415
    assert info.value.message_key == "fs.error.binary"


def test_read_file_too_large(tmp_path, monkeypatch):
    file_path = tmp_path / "big.txt"
    file_path.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(filesystem, "MAX_READ_BYTES", 10)
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(file_path))
    assert info.value.status == 413


def test_read_file_missing(tmp_path):
    with pytest.raises(FilesystemError) as info:
        filesystem.read_file(str(tmp_path / "no.txt"))
    assert info.value.status == 404


def test_write_file_creates_and_overwrites(tmp_path):
    target = tmp_path / "out.json"
    result = filesystem.write_file(str(target), '{"a": 1}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert result["size"] == len('{"a": 1}\n')
    assert result["mtime_ns"] == target.stat().st_mtime_ns

    second = filesystem.write_file(str(target), 'replaced')
    assert target.read_text(encoding="utf-8") == 'replaced'
    assert second["mtime"] >= result["mtime"]


def test_write_file_rejects_directory(tmp_path):
    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(tmp_path), "data")
    assert info.value.status == 400


def test_write_file_creates_parents(tmp_path):
    target = tmp_path / "nested" / "deep" / "file.txt"
    filesystem.write_file(str(target), "ok")
    assert target.read_text(encoding="utf-8") == "ok"


def test_write_file_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_WRITE_BYTES", 5)
    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(tmp_path / "x.txt"), "too-long")
    assert info.value.status == 413


def test_write_file_mtime_conflict(tmp_path):
    target = tmp_path / "race.txt"
    target.write_text("a", encoding="utf-8")
    stale_mtime = int(target.stat().st_mtime) - 100  # pretend the client saw an older version
    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(target), "b", expected_mtime=stale_mtime)
    assert info.value.status == 409


def test_write_file_mtime_conflict_uses_nanoseconds(tmp_path):
    target = tmp_path / "race-ns.txt"
    target.write_text("a", encoding="utf-8")
    base_ns = 1_800_000_000_123_456_000
    os.utime(target, ns=(base_ns, base_ns))
    actual_ns = base_ns + filesystem.MTIME_NS_CONFLICT_TOLERANCE + 1
    os.utime(target, ns=(actual_ns, actual_ns))

    with pytest.raises(FilesystemError) as info:
        filesystem.write_file(str(target), "b", expected_mtime=base_ns)

    assert info.value.status == 409


def test_write_file_accepts_tiny_nanosecond_mtime_drift(tmp_path):
    target = tmp_path / "race-ns-jitter.txt"
    target.write_text("a", encoding="utf-8")
    base_ns = 1_800_000_000_123_456_000
    os.utime(target, ns=(base_ns, base_ns))
    os.utime(target, ns=(base_ns + 85, base_ns + 85))

    result = filesystem.write_file(str(target), "b", expected_mtime=base_ns)

    assert result["size"] == 1


def test_write_file_accepts_legacy_second_mtime(tmp_path):
    target = tmp_path / "race-legacy.txt"
    target.write_text("a", encoding="utf-8")
    legacy_mtime = int(target.stat().st_mtime)

    result = filesystem.write_file(str(target), "b", expected_mtime=legacy_mtime)

    assert result["size"] == 1


def test_rename_path_same_directory(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("hello", encoding="utf-8")

    result = filesystem.rename_path(str(target), "new.txt")

    assert result["old_path"] == str(target)
    assert result["path"] == str(tmp_path / "new.txt")
    assert not target.exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello"


def test_rename_path_rejects_nested_name(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("hello", encoding="utf-8")

    with pytest.raises(FilesystemError) as info:
        filesystem.rename_path(str(target), "nested/new.txt")

    assert info.value.status == 400


@pytest.mark.parametrize("blocked_name", [".netrc", ".npmrc", ".pypirc", ".ssh"])
def test_rename_path_refuses_a_blocked_destination_before_mutation(monkeypatch, tmp_path, blocked_name):
    target = tmp_path / "safe.txt"
    target.write_text("safe", encoding="utf-8")
    rename_calls = []

    def record_rename(*args, **kwargs):
        rename_calls.append((args, kwargs))
        raise AssertionError("blocked destination reached os.rename")

    monkeypatch.setattr(filesystem_paths.os, "rename", record_rename)

    with pytest.raises(FilesystemError) as error:
        filesystem.rename_path(str(target), blocked_name)

    assert error.value.status == 403
    assert error.value.message_key == "fs.error.credentialBlocked"
    assert rename_calls == []
    assert target.read_text(encoding="utf-8") == "safe"


def test_delete_path_removes_directory_tree(tmp_path):
    target = tmp_path / "dir"
    (target / "nested").mkdir(parents=True)
    (target / "nested" / "file.txt").write_text("hello", encoding="utf-8")

    result = filesystem.delete_path(str(target), recursive=True)

    assert result["deleted"] is True
    assert result["kind"] == "dir"
    assert "pending" not in result
    assert not target.exists()


DELETE_ENTRY_CASES = ("file", "symlink", "empty_dir", "nonempty_dir", "missing")


def _make_delete_target(tmp_path: Path, case: str) -> Path:
    target = tmp_path / f"delete-{case}"
    if case == "file":
        target.write_text("payload", encoding="utf-8")
    elif case == "symlink":
        source = tmp_path / "symlink-source.txt"
        source.write_text("payload", encoding="utf-8")
        target.symlink_to(source)
    elif case == "empty_dir":
        target.mkdir()
    elif case == "nonempty_dir":
        (target / "child").mkdir(parents=True)
        (target / "child" / "leaf.txt").write_text("payload", encoding="utf-8")
    return target


@pytest.mark.parametrize("case", DELETE_ENTRY_CASES)
def test_non_recursive_delete_has_one_typed_result_per_entry_class(tmp_path, case):
    """One signature, one terminal result shape, and exactly one non-terminal probe result.

    `symlink` reports `kind: "file"`: today's payload calls every non-directory a file, and the
    browser reads that field.  Recorded here rather than changed, because adding `kind: "symlink"`
    is a UI-visible payload change that belongs to whoever owns the Finder row rendering.
    """
    target = _make_delete_target(tmp_path, case)

    if case == "missing":
        with pytest.raises(FilesystemError) as error:
            filesystem_io.delete_path(str(target))
        assert error.value.status == 404
        return

    result = filesystem_io.delete_path(str(target))

    if case == "nonempty_dir":
        assert result == {"path": str(target), "deleted": False, "kind": "dir", "pending": "subtree"}
        assert target.is_dir()
        assert (target / "child" / "leaf.txt").read_text(encoding="utf-8") == "payload"
    elif case == "empty_dir":
        assert result == {"path": str(target), "deleted": True, "kind": "dir"}
        assert not target.exists()
    else:
        assert result == {"path": str(target), "deleted": True, "kind": "file"}
        assert not target.is_symlink() and not target.exists()
        if case == "symlink":
            assert (tmp_path / "symlink-source.txt").exists(), "unlink followed the link"


def test_non_recursive_delete_of_a_nonempty_directory_never_scans_the_subtree(tmp_path, monkeypatch):
    """The bound proof.  A bounded unlink is bounded because it performs a FIXED number of
    destructive syscalls.  Before the split, one `delete` of a 20,000-entry tree performed 20,401
    of them under the same request, which is what disqualified `delete` from the mutation lane.
    Counting elapsed time would not prove the bound; refusing to enumerate does.
    """
    target = tmp_path / "tree"
    target.mkdir()
    for index in range(20_000):
        (target / f"entry-{index:05d}.txt").write_text("x", encoding="utf-8")
    scandir_calls = []
    unlink_calls = []
    rmdir_calls = []
    real_scandir, real_unlink, real_rmdir = os.scandir, os.unlink, os.rmdir

    def record_scandir(*args, **kwargs):
        scandir_calls.append(args)
        return real_scandir(*args, **kwargs)

    def record_unlink(*args, **kwargs):
        unlink_calls.append(args)
        return real_unlink(*args, **kwargs)

    def record_rmdir(*args, **kwargs):
        rmdir_calls.append(args)
        return real_rmdir(*args, **kwargs)

    monkeypatch.setattr(os, "scandir", record_scandir)
    monkeypatch.setattr(os, "unlink", record_unlink)
    monkeypatch.setattr(os, "rmdir", record_rmdir)

    result = filesystem_io.delete_path(str(target))

    assert result["pending"] == "subtree"
    assert result["deleted"] is False
    assert scandir_calls == [], f"non-recursive delete enumerated the subtree {len(scandir_calls)} times"
    assert unlink_calls == [], f"non-recursive delete unlinked {len(unlink_calls)} entries"
    assert len(rmdir_calls) == 1, "the bounded probe is exactly one rmdir"


def test_recursive_delete_still_removes_the_whole_subtree(tmp_path):
    target = tmp_path / "tree"
    (target / "a" / "b").mkdir(parents=True)
    (target / "a" / "b" / "leaf.txt").write_text("payload", encoding="utf-8")
    (target / "a" / "sibling.txt").write_text("payload", encoding="utf-8")

    result = filesystem_io.delete_path(str(target), recursive=True)

    assert result == {"path": str(target), "deleted": True, "kind": "dir"}
    assert not target.exists()


def test_pending_delete_probe_invalidates_nothing_and_reindexes_nothing(tmp_path, monkeypatch):
    """A probe that deleted nothing must not be reported as a mutation.

    `invalidate_path_policy_caches()` and the reindex fan-out are TERMINAL-result side effects; a
    pending probe firing them would publish a filesystem change that never happened.
    """
    target = tmp_path / "tree"
    (target / "child").mkdir(parents=True)
    (target / "child" / "leaf.txt").write_text("payload", encoding="utf-8")
    invalidations = []
    reindexes = []
    monkeypatch.setattr(filesystem_paths, "invalidate_path_policy_caches", lambda: invalidations.append(1))
    monkeypatch.setattr(filesystem, "_reindex_after_mutation", lambda candidates, reason="": reindexes.append(reason) or [])

    pending = filesystem.delete_path(str(target))

    assert pending == {"path": str(target), "deleted": False, "kind": "dir", "pending": "subtree"}
    assert "reindex_roots" not in pending
    assert invalidations == []
    assert reindexes == []

    terminal = filesystem.delete_path(str(target), recursive=True)

    assert terminal["deleted"] is True
    assert "pending" not in terminal
    assert invalidations == [1]
    assert reindexes == ["fs-delete"]


def test_delete_path_refuses_a_blocked_target_before_any_destructive_syscall(tmp_path, monkeypatch):
    blocked = tmp_path / ".ssh"
    blocked.mkdir()
    target = blocked / "id_rsa"
    target.write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE", encoding="utf-8")
    unlink_calls = []
    real_unlink = os.unlink
    monkeypatch.setattr(os, "unlink", lambda *args, **kwargs: unlink_calls.append(args) or real_unlink(*args, **kwargs))

    for recursive in (False, True):
        with pytest.raises(FilesystemError) as error:
            filesystem_io.delete_path(str(target), recursive=recursive)
        assert error.value.status == 403
    assert unlink_calls == []
    assert target.read_text(encoding="utf-8") == "BLOCKED_SENTINEL_DO_NOT_EXPOSE"


@pytest.mark.parametrize("recursive", [False, True])
def test_delete_path_consumes_the_authorized_parent_across_a_namespace_replacement(monkeypatch, tmp_path, recursive):
    """Both delete lanes keep ONE authorization owner: swapping the parent after authorization must
    not redirect either the bounded probe or the recursive walk at a blocked directory."""
    safe_parent = tmp_path / "safe"
    safe_parent.mkdir()
    (safe_parent / "item").mkdir()
    (safe_parent / "item" / "leaf.txt").write_text("safe", encoding="utf-8")
    blocked_parent = tmp_path / ".ssh"
    blocked_parent.mkdir()
    (blocked_parent / "item").mkdir()
    (blocked_parent / "item" / "leaf.txt").write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(safe_parent, target_is_directory=True)
    state = _swap_path_after_authorization(monkeypatch, alias / "item", alias, blocked_parent)

    try:
        filesystem_io.delete_path(str(alias / "item"), recursive=recursive)
    except FilesystemError:
        pass

    assert state["swapped"] is True
    assert (blocked_parent / "item" / "leaf.txt").read_text(encoding="utf-8") == "BLOCKED_SENTINEL_DO_NOT_EXPOSE"


def test_count_directory_files_counts_recursive_regular_files(tmp_path):
    target = tmp_path / "dir"
    nested = target / "nested"
    nested.mkdir(parents=True)
    (target / "a.txt").write_text("alpha", encoding="utf-8")
    (nested / "b.txt").write_text("bravo", encoding="utf-8")
    os.mkfifo(target / "pipe")
    os_symlink = getattr(os, "symlink", None)
    if os_symlink:
        os_symlink(target / "a.txt", target / "a-link.txt")

    result = filesystem.count_directory_files(str(target))

    assert result == {"path": str(target), "kind": "dir", "files": 2, "recursive": True}


def test_path_info_ignores_incomplete_git_marker_directory(tmp_path):
    root = tmp_path / "home"
    target = root / "dev" / "2025"
    target.mkdir(parents=True)
    marker = root / ".git"
    marker.mkdir()
    (marker / "config").write_text("[core]\n", encoding="utf-8")
    (marker / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    result = filesystem.path_info(str(target))

    assert result["repo_root"] == ""
    assert result["relative_path"] == ""
    assert result["repo"] is None


def test_path_info_ignores_invalid_git_marker_file(tmp_path):
    root = tmp_path / "home"
    target = root / "dev" / "2025"
    target.mkdir(parents=True)
    (root / ".git").write_text(f"gitdir: {tmp_path / 'missing-git-dir'}\n", encoding="utf-8")

    result = filesystem.path_info(str(target))

    assert result["repo_root"] == ""
    assert result["relative_path"] == ""
    assert result["repo"] is None


def test_path_info_returns_git_relative_path(tmp_path):
    git(tmp_path, "init")
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('hi')\n", encoding="utf-8")

    result = filesystem.path_info(str(target))

    assert result["repo_root"] == str(tmp_path)
    assert result["relative_path"] == "src/main.py"
    assert result["kind"] == "file"
    assert result["diff_capable"] is True
    assert result["git_tracked"] is False
    assert result["git_has_history"] is False


def test_path_info_marks_binary_content_not_diff_capable(tmp_path):
    git(tmp_path, "init")
    target = tmp_path / "looks-like-text.txt"
    target.write_bytes(b"text-prefix\x00binary")

    result = filesystem.path_info(str(target))

    assert result["repo_root"] == str(tmp_path)
    assert result["diff_capable"] is False


def test_file_identity_payloads_follow_symlinks_and_hardlinks(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("hello\n", encoding="utf-8")
    symlink = tmp_path / "alias.txt"
    symlink.symlink_to(target)
    hardlink = tmp_path / "hard.txt"
    os.link(target, hardlink)
    broken = tmp_path / "broken.txt"
    broken.symlink_to(tmp_path / "missing.txt")

    target_read = filesystem.read_file(str(target))
    symlink_read = filesystem.read_file(str(symlink))
    hardlink_info = filesystem.path_info(str(hardlink))
    entries = {entry["name"]: entry for entry in filesystem.list_directory(str(tmp_path))["entries"]}
    broken_info = filesystem.path_info(str(broken))

    assert target_read["file_id"]
    assert target_read["file_identity"] == f"id:{target_read['file_id']}"
    assert symlink_read["file_id"] == target_read["file_id"]
    assert symlink_read["realpath"] == os.path.realpath(target)
    assert entries["alias.txt"]["file_id"] == target_read["file_id"]
    assert entries["alias.txt"]["realpath"] == os.path.realpath(target)
    assert hardlink_info["file_id"] == target_read["file_id"]
    assert hardlink_info["realpath"] == os.path.realpath(hardlink)
    assert "file_id" not in broken_info
    assert "file_identity" not in broken_info


def test_diff_file_returns_git_diff_for_tracked_file(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("print('one')\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "base")
    target.write_text("print('two')\n", encoding="utf-8")

    result = filesystem.diff_file(str(target))

    assert result["repo"] == str(tmp_path)
    assert result["relative_path"] == "app.py"
    assert result["untracked"] is False
    assert result["original"] == "print('one')\n"
    assert result["working_missing"] is False
    assert "-print('one')" in result["diff"]
    assert "+print('two')" in result["diff"]


def test_diff_file_returns_no_index_diff_for_untracked_file(tmp_path):
    git(tmp_path, "init")
    target = tmp_path / "new.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = filesystem.diff_file(str(target))

    assert result["relative_path"] == "new.txt"
    assert result["untracked"] is True
    assert result["original"] == ""
    assert "+hello" in result["diff"]


def test_diff_file_returns_head_content_for_deleted_file(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "gone.txt"
    target.write_text("old\n", encoding="utf-8")
    git(tmp_path, "add", "gone.txt")
    git(tmp_path, "commit", "-m", "base")
    target.unlink()

    result = filesystem.diff_file(str(target))

    assert result["original"] == "old\n"
    assert result["working_missing"] is True
    assert "-old" in result["diff"]


def test_diff_file_supports_commit_to_commit_refs(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "one")
    older = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    target.write_text("two\n", encoding="utf-8")
    git(tmp_path, "commit", "-am", "two")
    newer = git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    result = filesystem.diff_file(str(target), from_ref=older, to_ref=newer)

    assert result["from_ref"] == older
    assert result["to_ref"] == newer
    assert result["original"] == "one\n"
    assert result["working"] == "two\n"
    assert "-one" in result["diff"]
    assert "+two" in result["diff"]


def test_diff_file_falls_back_when_requested_ref_is_unknown_in_repo(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "one")
    target.write_text("two\n", encoding="utf-8")

    result = filesystem.diff_file(str(target), from_ref="not-in-this-repo", to_ref="current")

    assert result["from_ref"] == "HEAD"
    assert result["to_ref"] == "current"
    assert "-one" in result["diff"]
    assert "+two" in result["diff"]


def test_diff_file_falls_back_when_requested_ref_order_is_invalid(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "one")
    older = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    target.write_text("two\n", encoding="utf-8")
    git(tmp_path, "commit", "-am", "two")
    newer = git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    target.write_text("three\n", encoding="utf-8")

    result = filesystem.diff_file(str(target), from_ref=newer, to_ref=older)

    assert result["from_ref"] == "HEAD"
    assert result["to_ref"] == "current"
    assert "-two" in result["diff"]
    assert "+three" in result["diff"]


def test_git_history_page_freezes_head_scope_and_constant_git_calls(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    expected = repo.git("rev-list", "--topo-order", repo.merge_sha).stdout.splitlines()
    calls = []
    original_git = git_ops._git_with_pinned_repo

    def counted_git(repo_handle, args, **kwargs):
        calls.append(tuple(args))
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", counted_git)
    first = filesystem.git_history(str(repo.root), limit=2)
    root_call_count = len(calls)
    calls.clear()
    scoped = filesystem.git_history(str(repo.scope), limit=50)
    scoped_call_count = len(calls)

    assert first["path"] == str(repo.root)
    assert first["repo"] == str(repo.root)
    assert first["relative_path"] == ""
    assert first["head"] == repo.merge_sha
    assert [item["sha"] for item in first["commits"]] == expected[:2]
    assert first["snapshot_cursor"]
    assert first["next_cursor"]
    assert first["truncated"] is False
    assert all(
        {"sha", "short", "parents", "subject", "author", "authored_at", "files", "added", "removed", "binary_files"}
        <= item.keys()
        for item in first["commits"]
    )
    assert repo.outside_sha not in {item["sha"] for item in scoped["commits"]}
    assert scoped["relative_path"] == "scope"
    assert next(item for item in scoped["commits"] if item["sha"] == repo.root_sha)["files"] > 0
    assert root_call_count == scoped_call_count
    assert root_call_count <= 4

    (repo.root / "new-head.txt").write_text("new head\n", encoding="utf-8")
    repo.git("add", "--", "new-head.txt")
    repo.git("commit", "-q", "-m", "new head after cursor")
    new_head = repo.git("rev-parse", "HEAD").stdout.strip()
    older = filesystem.git_history(str(repo.root), limit=2, cursor=first["next_cursor"])

    assert older["head"] == repo.merge_sha
    assert new_head not in {item["sha"] for item in older["commits"]}
    assert [item["sha"] for item in older["commits"]] == expected[2:4]


def test_pinned_git_view_reuses_warm_loose_objects_without_the_cross_process_writer_lock(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setattr(git_ops.tempfile, "gettempdir", lambda: str(runtime_root))
    repo = create_git_history_repository(tmp_path / "history")

    first = filesystem.git_history(str(repo.root), limit=2)

    def reject_cache_writer_lock():
        raise AssertionError("warm immutable loose-object hits must not take the cache writer lock")

    monkeypatch.setattr(git_ops, "_git_loose_object_cache_session", reject_cache_writer_lock)
    second = filesystem.git_history(str(repo.root), limit=2)

    assert [item["sha"] for item in second["commits"]] == [item["sha"] for item in first["commits"]]


def test_pinned_git_view_opens_validated_loose_object_basenames_from_the_authorized_prefix(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setattr(git_ops.tempfile, "gettempdir", lambda: str(runtime_root))
    repo = create_git_history_repository(tmp_path / "history")
    original_safe_child = git_ops.paths.safe_child

    def reject_reauthorization_of_loose_object(parent_descriptor, requested, resolved, **kwargs):
        if requested.parent.parent.name == "objects" and re.fullmatch(r"[0-9a-f]{2}", requested.parent.name):
            raise AssertionError("validated loose-object basenames must open from their authorized prefix descriptor")
        return original_safe_child(parent_descriptor, requested, resolved, **kwargs)

    monkeypatch.setattr(git_ops.paths, "safe_child", reject_reauthorization_of_loose_object)

    history = filesystem.git_history(str(repo.root), limit=1)

    assert history["commits"][0]["sha"] == repo.merge_sha


def test_git_history_snapshot_cursor_reloads_the_frozen_first_page(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    first = filesystem.git_history(str(repo.root), limit=2)
    (repo.root / "new-head.txt").write_text("new head\n", encoding="utf-8")
    repo.git("add", "--", "new-head.txt")
    repo.git("commit", "-q", "-m", "new head after snapshot")

    restored = filesystem.git_history(str(repo.root), limit=2, cursor=first["snapshot_cursor"])

    assert restored["head"] == first["head"]
    assert restored["snapshot_cursor"] == first["snapshot_cursor"]
    assert [item["sha"] for item in restored["commits"]] == [item["sha"] for item in first["commits"]]
    assert restored["next_cursor"] == first["next_cursor"]


@pytest.mark.parametrize(
    ("remote_url", "expected"),
    [
        ("git@github.com:owner/project.git", {"provider": "github", "base_url": "https://github.com/owner/project"}),
        ("ssh://git@gitlab.example.com/group/subgroup/project.git", {"provider": "gitlab", "base_url": "https://gitlab.example.com/group/subgroup/project"}),
        ("ssh://git@gitlab.example.com:2222/group/project.git", {"provider": "gitlab", "base_url": "https://gitlab.example.com/group/project"}),
        ("https://example.com/owner/project.git", None),
        ("https://token@github.com/owner/project.git", None),
        ("https://github.com/owner/project.git?token=secret", None),
    ],
)
def test_git_history_exposes_only_safe_supported_hosted_origin(tmp_path, remote_url, expected):
    repo = create_git_history_repository(tmp_path / "history")
    repo.git("remote", "add", "origin", remote_url)

    history = filesystem.git_history(str(repo.root), limit=1)

    assert history["hosted_remote"] == expected


def test_git_commit_detail_preserves_root_merge_rename_copy_binary_mode_and_hostile_paths(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    calls = []
    original_git = git_ops._git_with_pinned_repo

    def counted_git(repo_handle, args, **kwargs):
        calls.append(tuple(args))
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", counted_git)
    detail = filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)
    change_call_count = len(calls)
    files = {(item["status"], item["path"]): item for item in detail["files"]}

    assert detail["repo"] == str(repo.root)
    assert detail["scope_path"] == "scope"
    assert detail["sha"] == repo.changes_sha
    assert detail["message"] == "scoped history changes\n\nPreserve every path and count."
    renamed = files[("R", repo.renamed_to.relative_to(repo.root).as_posix())]
    assert renamed["old_path"] == repo.renamed_from.relative_to(repo.root).as_posix()
    copied = files[("C", repo.copy_target.relative_to(repo.root).as_posix())]
    assert copied["old_path"] == repo.copy_source.relative_to(repo.root).as_posix()
    assert files[("D", repo.deleted.relative_to(repo.root).as_posix())]["removed"] == 1
    binary = files[("M", repo.binary.relative_to(repo.root).as_posix())]
    assert binary["binary"] is True
    assert binary["added"] is None
    assert binary["removed"] is None
    mode_only = files[("M", repo.mode_only.relative_to(repo.root).as_posix())]
    assert mode_only["added"] == 0
    assert mode_only["removed"] == 0
    assert files[("A", repo.hostile.relative_to(repo.root).as_posix())]["added"] == 1
    assert detail["truncated"] is False

    calls.clear()
    root = filesystem.git_commit(str(repo.root), commit=repo.root_sha, head=repo.merge_sha)
    root_call_count = len(calls)
    assert root["parents"] == []
    empty_tree = subprocess.run(
        ["git", "-C", str(repo.root), "hash-object", "-t", "tree", "--stdin"],
        input="",
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    assert root["from_ref"] == empty_tree
    assert root["to_ref"] == repo.root_sha

    calls.clear()
    merge = filesystem.git_commit(str(repo.scope), commit=repo.merge_sha, head=repo.merge_sha)
    merge_call_count = len(calls)
    assert len(merge["parents"]) == 2
    assert merge["from_ref"] == merge["parents"][0] == repo.main_sha
    assert merge["to_ref"] == repo.merge_sha
    assert any(item["path"] == "scope/feature.txt" for item in merge["files"])
    assert change_call_count == root_call_count == merge_call_count
    assert change_call_count == 9


def test_git_history_rejects_stale_cursor_rewritten_repo_repoint_and_bounds(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    first = filesystem.git_history(str(repo.root), limit=1)

    with pytest.raises(FilesystemError) as malformed:
        filesystem.git_history(str(repo.root), cursor="not-a-history-cursor")
    assert malformed.value.message_key == "fs.error.gitHistoryCursor"
    with pytest.raises(FilesystemError) as cross_scope:
        filesystem.git_history(str(repo.scope), cursor=first["next_cursor"])
    assert cross_scope.value.message_key == "fs.error.gitHistoryCursor"
    with pytest.raises(FilesystemError) as unknown:
        filesystem.git_commit(str(repo.root), commit="f" * 40, head=repo.merge_sha)
    assert unknown.value.message_key == "fs.error.gitCommitUnknown"
    with pytest.raises(FilesystemError) as file_path:
        filesystem.git_history(str(repo.scope / "kept.txt"))
    assert file_path.value.message_key == "fs.error.gitHistoryDirectoryRequired"

    limited = filesystem.git_history(str(repo.root), limit=999)
    clamped = filesystem.git_history(str(repo.root), limit=0)
    assert len(limited["commits"]) <= 50
    assert len(clamped["commits"]) == 1

    monkeypatch.setattr(git_ops, "GIT_HISTORY_MAX_PAYLOAD_BYTES", 1400)
    history_bounded = filesystem.git_history(str(repo.root), limit=50)
    assert len(json.dumps(history_bounded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 1400
    assert history_bounded["truncated"] is True
    assert history_bounded["next_cursor"]

    monkeypatch.setattr(git_ops, "GIT_COMMIT_MAX_MESSAGE_BYTES", 16)
    monkeypatch.setattr(git_ops, "GIT_COMMIT_MAX_FILES", 1000)
    monkeypatch.setattr(git_ops, "GIT_COMMIT_MAX_PAYLOAD_BYTES", 1000)
    bounded = filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)
    assert len(bounded["message"].encode("utf-8")) <= 16
    assert bounded["message_truncated"] is True
    assert len(json.dumps(bounded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 1000
    assert len(bounded["files"]) < 8
    assert bounded["files_truncated"] is True
    assert bounded["truncated"] is True

    repo.git("reset", "-q", "--hard", repo.root_sha)
    with pytest.raises(FilesystemError) as stale:
        filesystem.git_history(str(repo.root), cursor=first["next_cursor"])
    assert stale.value.message_key == "fs.error.gitHistoryStale"


def test_git_history_reports_blocked_permission_timeout_and_repo_replacement(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    blocked = repo.root / ".ssh"
    blocked.mkdir()
    blocked_alias = repo.root / "blocked-link"
    blocked_alias.symlink_to(blocked, target_is_directory=True)
    with pytest.raises(FilesystemError) as blocked_path:
        filesystem.git_history(str(blocked))
    assert blocked_path.value.status == 403
    with pytest.raises(FilesystemError) as blocked_symlink:
        filesystem.git_history(str(blocked_alias))
    assert blocked_symlink.value.status == 403

    original_git = git_ops._git_with_pinned_repo

    def permission_denied(repo_handle, args, **kwargs):
        if "log" in args:
            return subprocess.CompletedProcess(args, 128, stdout=b"" if kwargs.get("binary") else "", stderr="permission denied")
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", permission_denied)
    with pytest.raises(FilesystemError) as permission:
        filesystem.git_history(str(repo.root))
    assert permission.value.status == 403
    assert permission.value.message_key == "fs.error.gitHistoryPermission"

    def timed_out(repo_handle, args, **kwargs):
        if "log" in args:
            raise subprocess.TimeoutExpired(["git", *args], kwargs["timeout"])
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", timed_out)
    with pytest.raises(FilesystemError) as timeout:
        filesystem.git_history(str(repo.root))
    assert timeout.value.status == 504
    assert timeout.value.message_key == "fs.error.gitHistoryTimeout"

    replacement = create_git_history_repository(tmp_path / "replacement")
    original_root = repo.root
    authorized_root = tmp_path / "authorized-old"
    original_pinned_repo_root = git_ops._pinned_repo_root
    replaced = False

    def replace_after_authorization(handle, *, deadline=None, operation=""):
        nonlocal replaced
        if not replaced:
            replaced = True
            original_root.rename(authorized_root)
            replacement.root.rename(original_root)
        return original_pinned_repo_root(handle, deadline=deadline, operation=operation)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", original_git)
    monkeypatch.setattr(git_ops, "_pinned_repo_root", replace_after_authorization)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(original_root))
    assert replaced is True
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_bounded_pinned_git_runner_terminates_at_output_cap(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    large = repo.root / "large.txt"
    large.write_text("x" * (256 * 1024), encoding="utf-8")
    repo.git("add", "--", "large.txt")
    repo.git("commit", "-q", "-m", "large blob")
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)

    with filesystem_paths.safe_path(str(repo.root), flags=directory_flags) as repo_handle:
        result = git_ops._git_with_pinned_repo(
            repo_handle,
            ["show", f"{head}:large.txt"],
            timeout=3.0,
            binary=True,
            max_output_bytes=128,
        )

    assert isinstance(result, git_ops.PinnedGitResult)
    assert result.stdout_truncated is True
    assert len(result.stdout) == 128
    assert result.returncode != 0


def test_git_commit_marks_counts_unavailable_when_numstat_is_truncated(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def truncate_numstat(repo_handle, args, **kwargs):
        if "diff-tree" in args and "--numstat" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=-9,
                stdout=b"",
                stderr=b"",
                stdout_truncated=True,
                killed_for_cap=True,
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", truncate_numstat)
    detail = filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert detail["files_truncated"] is True
    assert detail["truncated"] is True
    assert detail["files"]
    assert all(item["counts_available"] is False for item in detail["files"])
    assert all(item["added"] is None and item["removed"] is None for item in detail["files"])


@pytest.mark.parametrize(
    "status_output",
    [b"M\0scope/incomplete", b"M\0", b"R100\0scope/old.txt\0"],
)
def test_git_commit_rejects_status_truncated_before_first_complete_file(tmp_path, monkeypatch, status_output):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def truncate_status(repo_handle, args, **kwargs):
        if "diff-tree" in args and "--name-status" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=-9,
                stdout=status_output,
                stderr=b"",
                stdout_truncated=True,
                killed_for_cap=True,
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", truncate_status)
    with pytest.raises(FilesystemError) as too_large:
        filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert too_large.value.status == 413
    assert too_large.value.message_key == "fs.error.gitCommitTooLarge"


def test_git_commit_drops_exact_boundary_truncated_numstat_rename(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def truncate_numstat_rename(repo_handle, args, **kwargs):
        if "diff-tree" in args and "--numstat" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=-9,
                stdout=b"1\t2\t\0scope/old.txt\0",
                stderr=b"",
                stdout_truncated=True,
                killed_for_cap=True,
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", truncate_numstat_rename)
    detail = filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert detail["files_truncated"] is True
    assert all(item["counts_available"] is False for item in detail["files"])


def test_git_history_drops_in_progress_commit_when_output_is_truncated(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def truncate_second_commit(repo_handle, args, **kwargs):
        result = original_git(repo_handle, args, **kwargs)
        if "log" not in args:
            return result
        assert isinstance(result, git_ops.PinnedGitResult)
        raw = result.stdout
        assert isinstance(raw, bytes)
        second = raw.find(b"\0commit\0")
        assert second > 0
        return git_ops.PinnedGitResult(
            args=result.args,
            returncode=-9,
            stdout=raw[:second + len(b"\0commit\0") + 8],
            stderr=result.stderr,
            stdout_truncated=True,
            killed_for_cap=True,
        )

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", truncate_second_commit)
    history = filesystem.git_history(str(repo.root), limit=50)

    assert [item["sha"] for item in history["commits"]] == [repo.merge_sha]
    assert history["truncated"] is True
    assert "output_bytes" in history["truncation_reason"]
    assert history["next_cursor"]

    older = filesystem.git_history(str(repo.root), limit=50, cursor=history["next_cursor"])
    assert older["commits"]
    assert older["commits"][0]["sha"] != repo.merge_sha


@pytest.mark.parametrize("replacement_target", ["scope", "repo"])
def test_git_history_rejects_post_read_namespace_replacement(tmp_path, monkeypatch, replacement_target):
    repo = create_git_history_repository(tmp_path / "history")
    replacement = create_git_history_repository(tmp_path / "replacement")
    original_git = git_ops._git_with_pinned_repo
    replaced = False

    def replace_namespace_after_read(repo_handle, args, **kwargs):
        nonlocal replaced
        result = original_git(repo_handle, args, **kwargs)
        if "log" not in args or replaced:
            return result
        replaced = True
        if replacement_target == "scope":
            repo.scope.rename(repo.root / "scope-authorized")
            (repo.root / "scope").mkdir()
        else:
            repo.root.rename(tmp_path / "history-authorized")
            replacement.root.rename(repo.root)
        return result

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", replace_namespace_after_read)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.scope if replacement_target == "scope" else repo.root))

    assert replaced is True
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


@pytest.mark.parametrize("pointer_name", ["gitdir", "commondir"])
def test_git_history_rejects_in_place_git_control_pointer_rewrite(tmp_path, monkeypatch, pointer_name):
    repo = create_git_history_repository(tmp_path / "history")
    worktree = tmp_path / "linked"
    repo.git("worktree", "add", "-q", "-b", "linked-history", str(worktree))
    marker = worktree / ".git"
    git_dir = Path(marker.read_text(encoding="utf-8").removeprefix("gitdir: ").strip())
    pointer = marker if pointer_name == "gitdir" else git_dir / "commondir"
    original_git = git_ops._git_with_pinned_repo
    rewritten = False

    def rewrite_pointer_after_read(repo_handle, args, **kwargs):
        nonlocal rewritten
        result = original_git(repo_handle, args, **kwargs)
        if "log" in args and not rewritten:
            rewritten = True
            pointer.write_text("gitdir: /invalid\n" if pointer_name == "gitdir" else "/invalid\n", encoding="utf-8")
        return result

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", rewrite_pointer_after_read)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(worktree))

    assert rewritten is True
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


@pytest.mark.parametrize("target_name", ["git_dir", "commondir_file", "common_dir"])
def test_git_history_rejects_linked_control_target_replacement(tmp_path, monkeypatch, target_name):
    repo = create_git_history_repository(tmp_path / "history")
    worktree = tmp_path / "linked"
    repo.git("worktree", "add", "-q", "-b", "linked-target", str(worktree))
    marker = worktree / ".git"
    git_dir = Path(marker.read_text(encoding="utf-8").removeprefix("gitdir: ").strip())
    common_file = git_dir / "commondir"
    common_dir = (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
    original_git = git_ops._git_with_pinned_repo
    replaced = False

    def replace_target_after_read(repo_handle, args, **kwargs):
        nonlocal replaced
        result = original_git(repo_handle, args, **kwargs)
        if "log" not in args or replaced:
            return result
        replaced = True
        if target_name == "git_dir":
            git_dir.rename(git_dir.with_name(f"{git_dir.name}-authorized"))
            git_dir.mkdir()
        elif target_name == "commondir_file":
            content = common_file.read_bytes()
            common_file.rename(common_file.with_name("commondir-authorized"))
            common_file.write_bytes(content)
        else:
            common_dir.rename(common_dir.with_name(f"{common_dir.name}-authorized"))
            common_dir.mkdir()
        return result

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", replace_target_after_read)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(worktree))

    assert replaced is True
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


@pytest.mark.parametrize("pointer_name", ["gitdir", "commondir"])
@pytest.mark.parametrize("repoint_kind", ["replacement", "loop"])
def test_git_history_rejects_linked_control_symlink_repoint(tmp_path, monkeypatch, pointer_name, repoint_kind):
    repo = create_git_history_repository(tmp_path / "history")
    worktree = tmp_path / "linked"
    repo.git("worktree", "add", "-q", "-b", "linked-alias", str(worktree))
    marker = worktree / ".git"
    git_dir = Path(marker.read_text(encoding="utf-8").removeprefix("gitdir: ").strip())
    common_file = git_dir / "commondir"
    common_dir = (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
    alias = tmp_path / f"{pointer_name}-alias"
    replacement = tmp_path / f"{pointer_name}-replacement"
    replacement.mkdir()
    if pointer_name == "gitdir":
        alias.symlink_to(git_dir, target_is_directory=True)
        marker.write_text(f"gitdir: {alias}\n", encoding="utf-8")
    else:
        alias.symlink_to(common_dir, target_is_directory=True)
        common_file.write_text(f"{alias}\n", encoding="utf-8")
    original_git = git_ops._git_with_pinned_repo
    repointed = False

    def repoint_after_read(repo_handle, args, **kwargs):
        nonlocal repointed
        result = original_git(repo_handle, args, **kwargs)
        if "log" in args and not repointed:
            repointed = True
            alias.unlink()
            alias.symlink_to(alias if repoint_kind == "loop" else replacement, target_is_directory=True)
        return result

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", repoint_after_read)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(worktree))

    assert repointed is True
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_git_history_and_commit_reject_non_utf8_repository_path(tmp_path):
    invalid_root = tmp_path / os.fsdecode(b"history-\xff")
    repo = create_git_history_repository(invalid_root)

    for operation in (
        lambda: filesystem.git_history(str(repo.root)),
        lambda: filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha),
    ):
        with pytest.raises(FilesystemError) as invalid:
            operation()
        assert invalid.value.status == 422
        assert invalid.value.message_key == "fs.error.gitPathEncoding"


def test_git_history_stops_before_issuing_an_unusable_cursor(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    first = filesystem.git_history(str(repo.root), limit=1)
    padding = "=" * (-len(first["next_cursor"]) % 4)
    cursor_payload = json.loads(base64.urlsafe_b64decode(first["next_cursor"] + padding))
    cursor_payload["offset"] = git_ops.GIT_HISTORY_MAX_CURSOR_OFFSET
    ceiling_cursor = base64.urlsafe_b64encode(
        json.dumps(cursor_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    original_git = git_ops._git_with_pinned_repo

    def ignore_skip(repo_handle, args, **kwargs):
        if "log" in args:
            args = ["--skip=0" if arg.startswith("--skip=") else arg for arg in args]
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", ignore_skip)
    history = filesystem.git_history(str(repo.root), limit=1, cursor=ceiling_cursor)

    assert history["commits"]
    assert history["next_cursor"] == ""
    assert history["truncated"] is True
    assert "cursor_limit" in history["truncation_reason"]


def test_git_history_stops_before_issuing_a_cursor_over_the_decoder_limit(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    monkeypatch.setattr(git_ops, "GIT_HISTORY_CURSOR_MAX_BYTES", 64)

    history = filesystem.git_history(str(repo.root), limit=1)

    assert history["commits"]
    assert history["next_cursor"] == ""
    assert history["truncated"] is True
    assert "cursor_limit" in history["truncation_reason"]


def test_git_history_distinguishes_unborn_repo_from_failed_head_probe(tmp_path, monkeypatch):
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    init_repo(unborn)
    empty = filesystem.git_history(str(unborn))

    assert empty["head"] == ""
    assert empty["commits"] == []
    assert empty["next_cursor"] == ""

    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def capped_head(repo_handle, args, **kwargs):
        if "rev-parse" in args and "HEAD" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=-9,
                stdout=b"x" * kwargs["max_output_bytes"],
                stderr=b"",
                stdout_truncated=True,
                killed_for_cap=True,
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", capped_head)
    for operation, expected_key in (
        (lambda: filesystem.git_history(str(repo.root)), "fs.error.gitHistoryFailed"),
        (
            lambda: filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha),
            "fs.error.gitCommitFailed",
        ),
    ):
        with pytest.raises(FilesystemError) as failed:
            operation()
        assert failed.value.status == 500
        assert failed.value.message_key == expected_key


@pytest.mark.parametrize(
    ("stdout", "stdout_truncated"),
    [(b"unexpected", False), (b"", True)],
)
def test_git_commit_does_not_misclassify_failed_oid_probe_as_missing(
    tmp_path,
    monkeypatch,
    stdout,
    stdout_truncated,
):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def failed_probe(repo_handle, args, **kwargs):
        if "rev-parse" in args and f"{repo.merge_sha}^{{commit}}" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=1,
                stdout=stdout,
                stderr=b"",
                stdout_truncated=stdout_truncated,
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", failed_probe)
    with pytest.raises(FilesystemError) as failed:
        filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert failed.value.status == 500
    assert failed.value.message_key == "fs.error.gitCommitFailed"


def test_git_history_and_commit_ignore_ambient_graph_overrides(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    expected = repo.git("--no-replace-objects", "rev-list", "--topo-order", repo.merge_sha).stdout.splitlines()
    external_objects = tmp_path / "external-objects"
    external_objects.mkdir()
    shallow_file = tmp_path / "ambient-shallow"
    shallow_file.write_text(f"{repo.merge_sha}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(external_objects))
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(repo.root / ".git" / "objects"))
    monkeypatch.setenv("GIT_SHALLOW_FILE", str(shallow_file))

    history = filesystem.git_history(str(repo.root), limit=50)
    detail = filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert [item["sha"] for item in history["commits"]] == expected
    assert detail["sha"] == repo.changes_sha
    assert detail["to_ref"] == repo.changes_sha


def test_git_history_rejects_repository_declared_alternate_object_stores(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    external = create_git_history_repository(tmp_path / "external")
    alternates = repo.root / ".git" / "objects" / "info" / "alternates"
    alternates.write_text(f"{external.root / '.git' / 'objects'}\n", encoding="utf-8")

    with pytest.raises(FilesystemError) as unsupported:
        filesystem.git_history(str(repo.root))

    assert unsupported.value.status == 422
    assert unsupported.value.message_key == "fs.error.gitAlternateObjects"


def test_git_history_rejects_symlinked_object_directory_outside_allowed_roots(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    repo = create_git_history_repository(allowed / "history")
    external_objects = tmp_path / "external-objects"
    (repo.root / ".git" / "objects").rename(external_objects)
    (repo.root / ".git" / "objects").symlink_to(external_objects, target_is_directory=True)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(allowed))

    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.root))

    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


@pytest.mark.parametrize(
    "consumer",
    ["read", "info", "list", "diff", "blame", "rename"],
)
def test_lightweight_git_consumers_refuse_out_of_root_object_directory(tmp_path, monkeypatch, consumer):
    allowed = tmp_path / "allowed"
    repo = allowed / "repo"
    repo.mkdir(parents=True)
    init_repo(repo)
    target = repo / "item.txt"
    target.write_text("BLOCKED_SENTINEL_DO_NOT_EXPOSE\n", encoding="utf-8")
    git(repo, "add", "item.txt")
    git(repo, "commit", "-m", "BLOCKED_SENTINEL_DO_NOT_EXPOSE")
    target.write_text("safe working generation\n", encoding="utf-8")
    external_objects = tmp_path / "external-objects"
    (repo / ".git" / "objects").rename(external_objects)
    (repo / ".git" / "objects").symlink_to(external_objects, target_is_directory=True)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(allowed))

    def consume():
        if consumer == "read":
            return filesystem.read_file(str(target))
        if consumer == "info":
            return filesystem.path_info(str(target))
        if consumer == "list":
            return filesystem.list_directory(str(allowed))
        if consumer == "diff":
            return filesystem.diff_file(str(target))
        if consumer == "blame":
            return filesystem.blame_file(str(target))
        return filesystem.rename_path(str(target), "renamed.txt")

    with pytest.raises(FilesystemError) as changed:
        consume()

    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"
    assert "BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in str(changed.value)
    if consumer == "rename":
        assert target.read_text(encoding="utf-8") == "safe working generation\n"
        assert not (repo / "renamed.txt").exists()


@pytest.mark.parametrize(
    "consumer",
    ["read", "info", "list", "diff", "blame", "rename"],
)
def test_lightweight_git_consumers_reject_object_store_replacement_after_pin(tmp_path, monkeypatch, consumer):
    safe_repo = tmp_path / "safe"
    blocked_repo = tmp_path / "blocked"
    for repo, content, subject in (
        (safe_repo, "safe committed\n", "safe subject"),
        (blocked_repo, "BLOCKED_SENTINEL_DO_NOT_EXPOSE\n", "BLOCKED_SENTINEL_DO_NOT_EXPOSE"),
    ):
        repo.mkdir()
        init_repo(repo)
        (repo / "item.txt").write_text(content, encoding="utf-8")
        git(repo, "add", "item.txt")
        git(repo, "commit", "-m", subject)
    target = safe_repo / "item.txt"
    target.write_text("safe working generation\n", encoding="utf-8")
    safe_objects = safe_repo / ".git" / "objects"
    parked_objects = safe_repo / ".git" / "objects-authorized"
    blocked_objects = blocked_repo / ".git" / "objects"
    original_git = git_ops._git_with_pinned_repo
    replaced = False

    def replace_objects_after_scope_pin(repo_handle, args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            safe_objects.rename(parked_objects)
            blocked_objects.rename(safe_objects)
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", replace_objects_after_scope_pin)

    def consume():
        if consumer == "read":
            return filesystem.read_file(str(target))
        if consumer == "info":
            return filesystem.path_info(str(target))
        if consumer == "list":
            return filesystem.list_directory(str(tmp_path))
        if consumer == "diff":
            return filesystem.diff_file(str(target))
        if consumer == "blame":
            return filesystem.blame_file(str(target))
        return filesystem.rename_path(str(target), "renamed.txt")

    try:
        with pytest.raises(FilesystemError) as changed:
            consume()
        assert replaced is True
        assert changed.value.status == 409
        assert changed.value.message_key == "fs.error.gitRepositoryChanged"
        assert "BLOCKED_SENTINEL_DO_NOT_EXPOSE" not in str(changed.value)
        if consumer == "rename":
            assert target.read_text(encoding="utf-8") == "safe working generation\n"
            assert not (safe_repo / "renamed.txt").exists()
    finally:
        if replaced:
            safe_objects.rename(blocked_objects)
            parked_objects.rename(safe_objects)


def test_git_history_rejects_symlinked_loose_object_outside_allowed_roots(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    repo = create_git_history_repository(allowed / "history")
    head_object = repo.root / ".git" / "objects" / repo.merge_sha[:2] / repo.merge_sha[2:]
    external_object = tmp_path / "external-object"
    head_object.rename(external_object)
    head_object.symlink_to(external_object)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(allowed))

    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.root))

    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_git_history_rejects_symlinked_pack_file_outside_allowed_roots(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    repo = create_git_history_repository(allowed / "history")
    repo.git("gc", "--quiet")
    pack_file = next((repo.root / ".git" / "objects" / "pack").glob("*.pack"))
    external_pack = tmp_path / pack_file.name
    pack_file.rename(external_pack)
    pack_file.symlink_to(external_pack)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(allowed))

    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.root))

    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_git_history_rejects_in_place_pack_rewrite_during_read(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    repo.git("gc", "--quiet")
    pack_file = next((repo.root / ".git" / "objects" / "pack").glob("*.pack"))
    pack_file.chmod(0o644)
    original_pack = pack_file.read_bytes()
    original_git = git_ops._git_with_pinned_repo
    rewritten = False

    def rewrite_pack_after_log(repo_handle, args, **kwargs):
        nonlocal rewritten
        result = original_git(repo_handle, args, **kwargs)
        if "log" in args and not rewritten:
            rewritten = True
            pack_file.write_bytes(bytes([original_pack[0] ^ 1]) + original_pack[1:])
            pack_file.write_bytes(original_pack)
        return result

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", rewrite_pack_after_log)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.root))

    assert rewritten is True
    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_git_history_rejects_symlinked_ref_outside_allowed_roots(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    repo = create_git_history_repository(allowed / "history")
    branch = repo.git("symbolic-ref", "HEAD").stdout.strip()
    branch_ref = repo.root / ".git" / branch
    external_ref = tmp_path / "external-ref"
    branch_ref.rename(external_ref)
    branch_ref.symlink_to(external_ref)
    monkeypatch.setenv(filesystem.FS_ROOTS_ENV, str(allowed))

    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.root))

    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_git_history_probes_only_finite_object_directory_names(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")

    def reject_listdir(_path):
        raise AssertionError("Git object discovery must not materialize a directory listing")

    monkeypatch.setattr(git_ops.os, "listdir", reject_listdir)
    history = filesystem.git_history(str(repo.root), limit=1)

    assert history["commits"][0]["sha"] == repo.merge_sha


def test_git_history_hardlinks_pinned_loose_objects_without_copying_or_retaining_descriptors(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    real_snapshot = git_ops._snapshot_regular_child
    copied_loose_objects: list[str] = []

    def record_loose_object_copy(parent_handle, source_path, destination, **kwargs):
        if re.fullmatch(r"[0-9a-f]{2}", source_path.parent.name) and source_path.parent.parent.name == "objects":
            copied_loose_objects.append(str(source_path))
        return real_snapshot(parent_handle, source_path, destination, **kwargs)

    monkeypatch.setattr(git_ops, "_snapshot_regular_child", record_loose_object_copy)
    before_descriptors = len(os.listdir("/proc/self/fd"))
    history = filesystem.git_history(str(repo.root), limit=1)
    after_descriptors = len(os.listdir("/proc/self/fd"))

    assert history["commits"][0]["sha"] == repo.merge_sha
    assert copied_loose_objects == []
    assert after_descriptors == before_descriptors


def test_git_history_rejects_in_place_loose_object_rewrite_during_read(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    loose_object = repo.root / ".git" / "objects" / repo.merge_sha[:2] / repo.merge_sha[2:]
    loose_object.chmod(0o644)
    original_object = loose_object.read_bytes()
    original_git = git_ops._git_with_pinned_repo
    rewritten = False

    def rewrite_loose_object_after_log(repo_handle, args, **kwargs):
        nonlocal rewritten
        result = original_git(repo_handle, args, **kwargs)
        if "log" in args and not rewritten:
            rewritten = True
            loose_object.write_bytes(bytes([original_object[0] ^ 1]) + original_object[1:])
            loose_object.write_bytes(original_object)
        return result

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", rewrite_loose_object_after_log)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.root))

    assert rewritten is True
    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_git_history_checks_snapshot_deadline_before_each_loose_prefix_probe(tmp_path, monkeypatch):
    repo = tmp_path / "empty-history"
    repo.mkdir()
    init_repo(repo)
    original_check = git_ops.GitViewBudget.check
    original_stat = git_ops.os.stat
    check_calls = 0
    prefix_calls = 0

    def counted_check(budget):
        nonlocal check_calls
        check_calls += 1
        return original_check(budget)

    def require_check_before_prefix(path, *args, **kwargs):
        nonlocal prefix_calls
        if isinstance(path, str) and re.fullmatch(r"[0-9a-f]{2}", path):
            prefix_calls += 1
            assert check_calls >= prefix_calls
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(git_ops.GitViewBudget, "check", counted_check)
    monkeypatch.setattr(git_ops.os, "stat", require_check_before_prefix)

    history = filesystem.git_history(str(repo))

    assert history["commits"] == []
    assert prefix_calls == 256


def test_git_history_snapshot_deadline_starts_before_control_resolution(tmp_path, monkeypatch):
    repo = tmp_path / "empty-history"
    repo.mkdir()
    init_repo(repo)
    original_stat = git_ops.os.stat
    marker_probed = False

    def observe_control_probe(path, *args, **kwargs):
        nonlocal marker_probed
        result = original_stat(path, *args, **kwargs)
        if (
            path == ".git"
            and kwargs.get("dir_fd") is not None
            and any(frame.function == "_pinned_git_control" for frame in inspect.stack())
        ):
            marker_probed = True
        return result

    monkeypatch.setattr(git_ops.os, "stat", observe_control_probe)
    monkeypatch.setattr(git_ops.time, "monotonic", lambda: 11.0 if marker_probed else 0.0)

    with pytest.raises(FilesystemError) as too_large:
        filesystem.git_history(str(repo))

    assert marker_probed is True
    assert too_large.value.status == 413
    assert too_large.value.message_key == "fs.error.gitHistoryTooLarge"


def test_git_history_rechecks_build_deadline_after_final_marker_validation(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    current_time = 0.0
    original_marker_check = git_ops._ensure_git_marker_unchanged
    marker_checked = False

    def expire_after_marker_check(*args, **kwargs):
        nonlocal current_time, marker_checked
        original_marker_check(*args, **kwargs)
        if not marker_checked:
            marker_checked = True
            current_time = git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS + 1.0

    monkeypatch.setattr(git_ops.time, "monotonic", lambda: current_time)
    monkeypatch.setattr(git_ops, "_ensure_git_marker_unchanged", expire_after_marker_check)

    with pytest.raises(FilesystemError) as too_large:
        filesystem.git_history(str(repo.root), limit=1)

    assert marker_checked is True
    assert too_large.value.status == 413
    assert too_large.value.message_key == "fs.error.gitHistoryTooLarge"


def test_git_history_snapshot_deadline_starts_before_repo_discovery(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    current_time = 0.0
    original_repo_root = git_ops._pinned_repo_root

    def delayed_repo_root(*args, **kwargs):
        nonlocal current_time
        result = original_repo_root(*args, **kwargs)
        current_time = git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS + 1.0
        return result

    monkeypatch.setattr(git_ops.time, "monotonic", lambda: current_time)
    monkeypatch.setattr(git_ops, "_pinned_repo_root", delayed_repo_root)

    with pytest.raises(FilesystemError) as too_large:
        filesystem.git_history(str(repo.root), limit=1)

    assert too_large.value.status == 413
    assert too_large.value.message_key == "fs.error.gitHistoryTooLarge"


def test_git_history_checks_deadline_before_each_repo_ancestor_probe(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    nested = repo.root / "one" / "two" / "three"
    nested.mkdir(parents=True)
    original_deadline_check = git_ops._ensure_git_view_deadline
    original_safe_path = git_ops.paths.safe_path
    deadline_checks = 0
    ancestor_probes = 0
    previous_probe_checks = 0

    def counted_deadline_check(deadline):
        nonlocal deadline_checks
        deadline_checks += 1
        return original_deadline_check(deadline)

    def require_check_before_ancestor(*args, **kwargs):
        nonlocal ancestor_probes, previous_probe_checks
        if any(frame.function == "_pinned_repo_root" for frame in inspect.stack()):
            ancestor_probes += 1
            assert deadline_checks > previous_probe_checks
            previous_probe_checks = deadline_checks
        return original_safe_path(*args, **kwargs)

    monkeypatch.setattr(git_ops, "_ensure_git_view_deadline", counted_deadline_check)
    monkeypatch.setattr(git_ops.paths, "safe_path", require_check_before_ancestor)

    history = filesystem.git_history(str(nested), limit=1)

    assert history["head"] == repo.merge_sha
    assert ancestor_probes >= 4


def test_git_history_uses_a_fresh_deadline_for_retirement_validation(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    current_time = 0.0
    original_git = git_ops._git_with_pinned_repo

    def controlled_monotonic():
        return current_time

    def advance_after_log(repo_handle, args, **kwargs):
        nonlocal current_time
        result = original_git(repo_handle, args, **kwargs)
        if "log" in args:
            current_time = git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS + 1.0
        return result

    monkeypatch.setattr(git_ops.time, "monotonic", controlled_monotonic)
    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", advance_after_log)

    history = filesystem.git_history(str(repo.root), limit=1)

    assert history["commits"][0]["sha"] == repo.merge_sha


def test_git_history_bounds_scope_retirement_with_the_shared_deadline(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    current_time = 0.0
    original_git = git_ops._git_with_pinned_repo
    original_namespace_check = git_ops._ensure_pinned_namespace_unchanged
    scope_check_expired = False

    def advance_after_log(repo_handle, args, **kwargs):
        nonlocal current_time
        result = original_git(repo_handle, args, **kwargs)
        if "log" in args:
            current_time = git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS + 1.0
        return result

    def expire_during_scope_retirement(handle):
        nonlocal current_time, scope_check_expired
        original_namespace_check(handle)
        if (
            current_time > git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS
            and not scope_check_expired
            and any(frame.function == "_pinned_git_history_scope" for frame in inspect.stack())
        ):
            scope_check_expired = True
            current_time += git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS + 1.0

    monkeypatch.setattr(git_ops.time, "monotonic", lambda: current_time)
    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", advance_after_log)
    monkeypatch.setattr(git_ops, "_ensure_pinned_namespace_unchanged", expire_during_scope_retirement)

    with pytest.raises(FilesystemError) as too_large:
        filesystem.git_history(str(repo.root), limit=1)

    assert scope_check_expired is True
    assert too_large.value.status == 413
    assert too_large.value.message_key == "fs.error.gitHistoryTooLarge"


def test_git_history_bounds_pack_retirement_with_the_shared_deadline(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    repo.git("gc", "--quiet")
    current_time = 0.0
    original_git = git_ops._git_with_pinned_repo
    original_pack_check = git_ops._ensure_pinned_regular_file_unchanged
    pack_checked = False

    def controlled_monotonic():
        return current_time

    def advance_after_log(repo_handle, args, **kwargs):
        nonlocal current_time
        result = original_git(repo_handle, args, **kwargs)
        if "log" in args:
            current_time = git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS + 1.0
        return result

    def expire_during_pack_check(handle):
        nonlocal current_time, pack_checked
        original_pack_check(handle)
        if not pack_checked:
            pack_checked = True
            current_time += git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS + 1.0

    monkeypatch.setattr(git_ops.time, "monotonic", controlled_monotonic)
    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", advance_after_log)
    monkeypatch.setattr(git_ops, "_ensure_pinned_regular_file_unchanged", expire_during_pack_check)

    with pytest.raises(FilesystemError) as too_large:
        filesystem.git_history(str(repo.root), limit=1)

    assert pack_checked is True
    assert too_large.value.status == 413
    assert too_large.value.message_key == "fs.error.gitHistoryTooLarge"


@pytest.mark.parametrize(
    ("key", "value"),
    [("extensions.refStorage", "reftable"), ("extensions.unknownRequired", "true")],
)
def test_git_history_rejects_unsupported_repository_extensions(tmp_path, key, value):
    repo = create_git_history_repository(tmp_path / "history")
    repo.git("config", "core.repositoryformatversion", "1")
    repo.git("config", key, value)

    with pytest.raises(FilesystemError) as unsupported:
        filesystem.git_history(str(repo.root))

    assert unsupported.value.status == 422
    assert unsupported.value.message_key == "fs.error.gitRepositoryUnsupported"


def test_git_history_accepts_legacy_worktree_config_at_format_zero(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    repo.git("config", "extensions.worktreeConfig", "true")

    payload = filesystem.git_history(str(repo.root))

    assert payload["commits"]


def test_git_history_rejects_repository_config_include(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    included = tmp_path / "included-git-config"
    included.write_text(
        "[core]\n\trepositoryformatversion = 1\n[extensions]\n\trefstorage = reftable\n",
        encoding="utf-8",
    )
    with (repo.root / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write(f"\n[include]\n\tpath = {included}\n")

    with pytest.raises(FilesystemError) as unsupported:
        filesystem.git_history(str(repo.root))

    assert unsupported.value.status == 422
    assert unsupported.value.message_key == "fs.error.gitRepositoryUnsupported"


def test_git_history_rejects_object_directory_replacement_during_read(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    replacement = create_git_history_repository(tmp_path / "replacement")
    objects = repo.root / ".git" / "objects"
    authorized_objects = repo.root / ".git" / "objects-authorized"
    replacement_objects = replacement.root / ".git" / "objects"
    original_git = git_ops._git_with_pinned_repo
    replaced = False

    def replace_objects_after_head(repo_handle, args, **kwargs):
        nonlocal replaced
        result = original_git(repo_handle, args, **kwargs)
        if "rev-parse" in args and "HEAD" in args and not replaced:
            replaced = True
            objects.rename(authorized_objects)
            replacement_objects.rename(objects)
        return result

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", replace_objects_after_head)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.root))

    assert replaced is True
    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_git_history_cannot_consume_transient_alternate_object_store(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    external = create_git_history_repository(tmp_path / "external")
    (external.root / "external-only.txt").write_text("outside\n", encoding="utf-8")
    external.git("add", "--", "external-only.txt")
    external.git("commit", "-q", "-m", "external only")
    external_head = external.git("rev-parse", "HEAD").stdout.strip()
    branch = repo.git("symbolic-ref", "HEAD").stdout.strip()
    (repo.root / ".git" / branch).write_text(f"{external_head}\n", encoding="ascii")
    alternates = repo.root / ".git" / "objects" / "info" / "alternates"
    original_git = git_ops._git_with_pinned_repo
    injected = 0

    def transient_alternate(repo_handle, args, **kwargs):
        nonlocal injected
        alternates.write_text(f"{external.root / '.git' / 'objects'}\n", encoding="utf-8")
        injected += 1
        try:
            return original_git(repo_handle, args, **kwargs)
        finally:
            alternates.unlink()

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", transient_alternate)
    with pytest.raises(FilesystemError) as failed:
        filesystem.git_history(str(repo.root))

    assert injected >= 1
    assert failed.value.message_key == "fs.error.gitHistoryFailed"


def test_git_history_rejects_alternate_object_store_added_during_read(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    external = create_git_history_repository(tmp_path / "external")
    alternates = repo.root / ".git" / "objects" / "info" / "alternates"
    original_git = git_ops._git_with_pinned_repo
    added = False

    def add_alternate_after_head(repo_handle, args, **kwargs):
        nonlocal added
        result = original_git(repo_handle, args, **kwargs)
        if "rev-parse" in args and "HEAD" in args and not added:
            added = True
            alternates.write_text(f"{external.root / '.git' / 'objects'}\n", encoding="utf-8")
        return result

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", add_alternate_after_head)
    with pytest.raises(FilesystemError) as unsupported:
        filesystem.git_history(str(repo.root))

    assert added is True
    assert unsupported.value.status == 422
    assert unsupported.value.message_key == "fs.error.gitAlternateObjects"


def test_git_history_disables_lazy_fetch_and_uses_a_pinned_shallow_snapshot(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    expected = repo.git("--no-replace-objects", "rev-list", "--topo-order", repo.merge_sha).stdout.splitlines()
    shallow_path = repo.root / ".git" / "shallow"
    original_popen = git_ops.subprocess.Popen
    git_environments = []

    def capture_git_environment(*args, **kwargs):
        if args and args[0] and args[0][0] == "git":
            git_environments.append(dict(kwargs["env"]))
        return original_popen(*args, **kwargs)

    original_git = git_ops._git_with_pinned_repo

    def shallow_aba(repo_handle, args, **kwargs):
        if "log" not in args:
            return original_git(repo_handle, args, **kwargs)
        shallow_path.write_text(f"{repo.merge_sha}\n", encoding="utf-8")
        try:
            return original_git(repo_handle, args, **kwargs)
        finally:
            shallow_path.unlink()

    monkeypatch.setattr(git_ops.subprocess, "Popen", capture_git_environment)
    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", shallow_aba)
    history = filesystem.git_history(str(repo.root), limit=50)

    assert [item["sha"] for item in history["commits"]] == expected
    assert git_environments
    assert all(environment["GIT_NO_LAZY_FETCH"] == "1" for environment in git_environments)
    assert all(environment["GIT_SHALLOW_FILE"] for environment in git_environments)
    assert all(environment["LC_ALL"] == "C" for environment in git_environments)


def test_git_history_and_commit_reject_malformed_timestamps(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo
    parent = repo.git("show", "--no-patch", "--format=%P", repo.changes_sha).stdout.strip()

    def malformed_timestamp(repo_handle, args, **kwargs):
        if "log" in args:
            raw = b"\0".join(
                [
                    b"commit",
                    repo.merge_sha.encode(),
                    repo.merge_sha[:9].encode(),
                    repo.main_sha.encode(),
                    b"History Fixture",
                    b"not-a-timestamp",
                    b"malformed history timestamp",
                    b"\n1\t0\tscope/file.txt",
                ]
            )
            return git_ops.PinnedGitResult(args=list(args), returncode=0, stdout=raw, stderr=b"")
        if "show" in args and "--no-patch" in args:
            raw = b"\0".join(
                [
                    repo.changes_sha.encode(),
                    parent.encode(),
                    b"History Fixture",
                    b"not-a-timestamp",
                    b"malformed commit timestamp",
                    b"message",
                    b"",
                ]
            )
            return git_ops.PinnedGitResult(args=list(args), returncode=0, stdout=raw, stderr=b"")
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", malformed_timestamp)
    for operation, expected_key in (
        (lambda: filesystem.git_history(str(repo.root)), "fs.error.gitHistoryFailed"),
        (
            lambda: filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha),
            "fs.error.gitCommitFailed",
        ),
    ):
        with pytest.raises(FilesystemError) as failed:
            operation()
        assert failed.value.status == 500
        assert failed.value.message_key == expected_key


def test_git_history_rejects_changed_shallow_boundary_for_frozen_cursor(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    first = filesystem.git_history(str(repo.root), limit=1)
    (repo.root / ".git" / "shallow").write_text(f"{repo.merge_sha}\n", encoding="utf-8")

    with pytest.raises(FilesystemError) as stale:
        filesystem.git_history(str(repo.root), limit=1, cursor=first["next_cursor"])

    assert stale.value.status == 409
    assert stale.value.message_key == "fs.error.gitHistoryStale"


def test_git_history_reports_missing_cursor_head_as_stale(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    first = filesystem.git_history(str(repo.root), limit=1)
    padding = "=" * (-len(first["next_cursor"]) % 4)
    cursor_payload = json.loads(base64.urlsafe_b64decode(first["next_cursor"] + padding))
    cursor_payload["head"] = "f" * 40
    missing_cursor = base64.urlsafe_b64encode(
        json.dumps(cursor_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")

    with pytest.raises(FilesystemError) as stale:
        filesystem.git_history(str(repo.root), cursor=missing_cursor)

    assert stale.value.status == 409
    assert stale.value.message_key == "fs.error.gitHistoryStale"


def test_git_history_does_not_mask_fatal_stderr_after_output_cap(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def fatal_after_cap(repo_handle, args, **kwargs):
        if "log" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=-9,
                stdout=b"commit\0" + repo.merge_sha.encode(),
                stderr=b"fatal: corrupt object",
                stdout_truncated=True,
                killed_for_cap=True,
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", fatal_after_cap)
    with pytest.raises(FilesystemError) as failed:
        filesystem.git_history(str(repo.root))

    assert failed.value.status == 500
    assert failed.value.message_key == "fs.error.gitHistoryFailed"


@pytest.mark.parametrize(
    ("parser", "raw", "expected_key"),
    [
        (git_ops._parse_history_numstat, b"commit\0deadbeef", "fs.error.gitHistoryFailed"),
        (
            git_ops._parse_history_numstat,
            b"\0".join(
                [
                    b"commit",
                    b"a" * 40,
                    b"a" * 9,
                    b"",
                    b"author",
                    b"1",
                    b"subject",
                    b"\n1\t2\t",
                    b"old",
                ]
            ),
            "fs.error.gitHistoryFailed",
        ),
        (git_ops._parse_name_status, b"R100\0old", "fs.error.gitCommitFailed"),
        (git_ops._parse_detail_numstat, b"1\t2\t\0old", "fs.error.gitCommitFailed"),
    ],
)
def test_git_history_parsers_reject_incomplete_nontruncated_output(parser, raw, expected_key):
    with pytest.raises(FilesystemError) as failed:
        parser(raw, output_truncated=False)

    assert failed.value.status == 500
    assert failed.value.message_key == expected_key


@pytest.mark.parametrize(
    ("parser", "raw", "expected_key"),
    [
        (
            git_ops._parse_history_numstat,
            b"\0".join(
                [
                    b"commit",
                    b"a" * 40,
                    b"a" * 9,
                    b"",
                    b"author",
                    b"1",
                    b"subject",
                    b"\n1\t2\tscope/file.txt",
                ]
            ),
            "fs.error.gitHistoryFailed",
        ),
        (
            git_ops._parse_commit_metadata,
            b"\0".join([b"a" * 40, b"", b"author", b"1", b"subject", b"message"]),
            "fs.error.gitCommitFailed",
        ),
        (git_ops._parse_name_status, b"M\0scope/file.txt", "fs.error.gitCommitFailed"),
        (git_ops._parse_detail_numstat, b"1\t2\tscope/file.txt", "fs.error.gitCommitFailed"),
    ],
)
def test_git_history_parsers_reject_missing_nontruncated_terminator(parser, raw, expected_key):
    with pytest.raises(FilesystemError) as failed:
        parser(raw, output_truncated=False)

    assert failed.value.status == 500
    assert failed.value.message_key == expected_key


@pytest.mark.parametrize(
    ("parser", "raw"),
    [
        (git_ops._parse_name_status, b"M\0scope/file.txt\0M\0scope/file.txt\0"),
        (git_ops._parse_detail_numstat, b"1\t2\tscope/file.txt\0" * 2),
    ],
)
def test_git_commit_parsers_reject_duplicate_file_identities(parser, raw):
    with pytest.raises(FilesystemError) as failed:
        parser(raw, output_truncated=False)

    assert failed.value.status == 500
    assert failed.value.message_key == "fs.error.gitCommitFailed"


def test_git_history_numstat_rejects_empty_rename_endpoints():
    prefix = b"\0".join([b"commit", b"a" * 40, b"a" * 9, b"", b"author", b"1", b"subject"])

    for rename in (b"\n1\t2\t\0\0scope/new.txt\0", b"\n1\t2\t\0scope/old.txt\0\0"):
        with pytest.raises(FilesystemError) as failed:
            git_ops._parse_history_numstat(prefix + b"\0" + rename, output_truncated=False)

        assert failed.value.status == 500
        assert failed.value.message_key == "fs.error.gitHistoryFailed"


def test_git_numstat_parsers_reject_mixed_binary_markers():
    prefix = b"\0".join([b"commit", b"a" * 40, b"a" * 9, b"", b"author", b"1", b"subject"])
    cases = (
        (git_ops._parse_history_numstat, prefix + b"\0\n-\t2\tscope/file.bin\0", "fs.error.gitHistoryFailed"),
        (git_ops._parse_history_numstat, prefix + b"\0\n2\t-\tscope/file.bin\0", "fs.error.gitHistoryFailed"),
        (git_ops._parse_detail_numstat, b"-\t2\tscope/file.bin\0", "fs.error.gitCommitFailed"),
        (git_ops._parse_detail_numstat, b"2\t-\tscope/file.bin\0", "fs.error.gitCommitFailed"),
    )

    for parser, raw, expected_key in cases:
        with pytest.raises(FilesystemError) as failed:
            parser(raw, output_truncated=False)

        assert failed.value.status == 500
        assert failed.value.message_key == expected_key


def test_git_numstat_parsers_reject_negative_counts():
    prefix = b"\0".join([b"commit", b"a" * 40, b"a" * 9, b"", b"author", b"1", b"subject"])
    cases = (
        (git_ops._parse_history_numstat, prefix + b"\0\n-1\t2\tscope/file.txt\0", "fs.error.gitHistoryFailed"),
        (git_ops._parse_history_numstat, prefix + b"\0\n2\t-1\tscope/file.txt\0", "fs.error.gitHistoryFailed"),
        (git_ops._parse_detail_numstat, b"-1\t2\tscope/file.txt\0", "fs.error.gitCommitFailed"),
        (git_ops._parse_detail_numstat, b"2\t-1\tscope/file.txt\0", "fs.error.gitCommitFailed"),
    )

    for parser, raw, expected_key in cases:
        with pytest.raises(FilesystemError) as failed:
            parser(raw, output_truncated=False)

        assert failed.value.status == 500
        assert failed.value.message_key == expected_key


def test_git_commit_reports_missing_frozen_head_as_stale(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")

    with pytest.raises(FilesystemError) as stale:
        filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head="f" * 40)

    assert stale.value.status == 409
    assert stale.value.message_key == "fs.error.gitHistoryStale"


def test_git_commit_rejects_complete_status_numstat_disagreement(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def missing_numstat(repo_handle, args, **kwargs):
        if "diff-tree" in args and "--numstat" in args:
            return git_ops.PinnedGitResult(args=list(args), returncode=0, stdout=b"", stderr=b"")
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", missing_numstat)
    with pytest.raises(FilesystemError) as failed:
        filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert failed.value.status == 500
    assert failed.value.message_key == "fs.error.gitCommitFailed"


def test_git_history_rejects_invalid_direct_limit(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")

    with pytest.raises(FilesystemError) as invalid:
        filesystem.git_history(str(repo.root), limit="many")

    assert invalid.value.status == 422
    assert invalid.value.message_key == "fs.error.gitHistoryLimit"


def test_git_history_does_not_treat_real_failure_as_cap_termination(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def corrupt_history(repo_handle, args, **kwargs):
        if "log" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=128,
                stdout=b"x" * kwargs["max_output_bytes"],
                stderr=b"fatal: corrupt object",
                stdout_truncated=True,
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", corrupt_history)
    with pytest.raises(FilesystemError) as failed:
        filesystem.git_history(str(repo.root))

    assert failed.value.status == 500
    assert failed.value.message_key == "fs.error.gitHistoryFailed"


def test_git_commit_does_not_treat_real_metadata_failure_as_cap_termination(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo
    parent = repo.git("show", "--no-patch", "--format=%P", repo.changes_sha).stdout.strip()

    def corrupt_metadata(repo_handle, args, **kwargs):
        if "show" in args and "--no-patch" in args:
            raw = b"\0".join(
                [
                    repo.changes_sha.encode(),
                    parent.encode(),
                    b"History Fixture",
                    b"1",
                    b"corrupt metadata",
                    b"x" * kwargs["max_output_bytes"],
                ]
            )
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=128,
                stdout=raw[:kwargs["max_output_bytes"]],
                stderr=b"fatal: corrupt object",
                stdout_truncated=True,
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", corrupt_metadata)
    with pytest.raises(FilesystemError) as failed:
        filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert failed.value.status == 500
    assert failed.value.message_key == "fs.error.gitCommitFailed"


def test_git_commit_reports_nontruncated_metadata_failure_as_operational(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo

    def corrupt_metadata(repo_handle, args, **kwargs):
        if "show" in args and "--no-patch" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=128,
                stdout=b"",
                stderr=b"fatal: corrupt object",
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", corrupt_metadata)
    with pytest.raises(FilesystemError) as failed:
        filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert failed.value.status == 500
    assert failed.value.message_key == "fs.error.gitCommitFailed"


def test_git_history_and_commit_distinguish_merge_base_failure_from_not_ancestor(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    first = filesystem.git_history(str(repo.root), limit=1)
    original_git = git_ops._git_with_pinned_repo

    def failed_relation(repo_handle, args, **kwargs):
        if "merge-base" in args:
            return git_ops.PinnedGitResult(
                args=list(args),
                returncode=128,
                stdout=b"",
                stderr=b"fatal: corrupt relation",
            )
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", failed_relation)
    with pytest.raises(FilesystemError) as history_failed:
        filesystem.git_history(str(repo.root), cursor=first["next_cursor"])
    with pytest.raises(FilesystemError) as commit_failed:
        filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert history_failed.value.status == 500
    assert history_failed.value.message_key == "fs.error.gitHistoryFailed"
    assert commit_failed.value.status == 500
    assert commit_failed.value.message_key == "fs.error.gitCommitFailed"


def test_git_commit_bounds_status_and_numstat_rows_before_payload_construction(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    original_git = git_ops._git_with_pinned_repo
    row_count = git_ops.GIT_COMMIT_MAX_FILES + 200
    status = b"".join(f"M\0scope/file-{index:04d}.txt\0".encode() for index in range(row_count))
    numstat = b"".join(f"1\t1\tscope/file-{index:04d}.txt\0".encode() for index in range(row_count))
    parsed_status, status_truncated = git_ops._parse_name_status(status, output_truncated=False)
    parsed_counts, counts_truncated = git_ops._parse_detail_numstat(numstat, output_truncated=False)

    assert len(parsed_status) == git_ops.GIT_COMMIT_MAX_FILES
    assert len(parsed_counts) == git_ops.GIT_COMMIT_MAX_FILES
    assert status_truncated is True
    assert counts_truncated is True

    def oversized_file_list(repo_handle, args, **kwargs):
        if "diff-tree" in args and "--name-status" in args:
            return git_ops.PinnedGitResult(args=list(args), returncode=0, stdout=status, stderr=b"")
        if "diff-tree" in args and "--numstat" in args:
            return git_ops.PinnedGitResult(args=list(args), returncode=0, stdout=numstat, stderr=b"")
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", oversized_file_list)
    detail = filesystem.git_commit(str(repo.scope), commit=repo.changes_sha, head=repo.merge_sha)

    assert len(detail["files"]) == git_ops.GIT_COMMIT_MAX_FILES
    assert detail["files_truncated"] is True
    assert detail["truncated"] is True


def test_git_commit_reports_oversized_message_as_truncated_not_unknown(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    (repo.scope / "kept.txt").write_text("oversized message commit\n", encoding="utf-8")
    repo.git("add", "--", "scope/kept.txt")
    message_path = tmp_path / "message.txt"
    message_path.write_text("oversized commit\n\n" + ("x" * (256 * 1024)), encoding="utf-8")
    repo.git("-c", "commit.cleanup=verbatim", "commit", "-q", "-F", str(message_path))
    head = repo.git("rev-parse", "HEAD").stdout.strip()

    detail = filesystem.git_commit(str(repo.scope), commit=head, head=head)

    assert detail["sha"] == head
    assert detail["subject"] == "oversized commit"
    assert detail["message_truncated"] is True
    assert len(detail["message"].encode("utf-8")) <= git_ops.GIT_COMMIT_MAX_MESSAGE_BYTES


def test_git_commit_reports_oversized_subject_as_typed_too_large(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    (repo.scope / "kept.txt").write_text("oversized subject commit\n", encoding="utf-8")
    repo.git("add", "--", "scope/kept.txt")
    message_path = tmp_path / "subject.txt"
    message_path.write_text("s" * (256 * 1024), encoding="utf-8")
    repo.git("-c", "commit.cleanup=verbatim", "commit", "-q", "-F", str(message_path))
    head = repo.git("rev-parse", "HEAD").stdout.strip()

    with pytest.raises(FilesystemError) as too_large:
        filesystem.git_commit(str(repo.scope), commit=head, head=head)

    assert too_large.value.status == 413
    assert too_large.value.message_key == "fs.error.gitCommitTooLarge"


@pytest.mark.parametrize("graph_override", ["replace", "graft"])
def test_git_history_and_detail_ignore_mutable_graph_overrides(tmp_path, graph_override):
    repo = create_git_history_repository(tmp_path / "history")
    expected = repo.git("--no-replace-objects", "rev-list", "--topo-order", repo.merge_sha).stdout.splitlines()
    parent = repo.git("--no-replace-objects", "show", "--no-patch", "--format=%P", repo.main_sha).stdout.split()[0]
    first = filesystem.git_history(str(repo.root), limit=1)

    if graph_override == "replace":
        repo.git("replace", repo.main_sha, repo.root_sha)
    else:
        grafts = repo.root / ".git" / "info" / "grafts"
        grafts.write_text(f"{repo.main_sha} {repo.root_sha}\n", encoding="utf-8")

    older = filesystem.git_history(str(repo.root), limit=2, cursor=first["next_cursor"])
    detail = filesystem.git_commit(str(repo.scope), commit=repo.main_sha, head=repo.merge_sha)

    assert [item["sha"] for item in older["commits"]] == expected[1:3]
    assert detail["from_ref"] == parent


def test_git_history_rejects_git_control_replacement(tmp_path, monkeypatch):
    repo = create_git_history_repository(tmp_path / "history")
    replacement = create_git_history_repository(tmp_path / "replacement")
    original_git = git_ops._git_with_pinned_repo
    replaced = False

    def replace_control_after_pin(repo_handle, args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            (repo.root / ".git").rename(repo.root / ".git-authorized")
            (replacement.root / ".git").rename(repo.root / ".git")
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", replace_control_after_pin)
    with pytest.raises(FilesystemError) as changed:
        filesystem.git_history(str(repo.root))

    assert replaced is True
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"


def test_git_commit_rejects_non_utf8_historical_path(tmp_path):
    repo = create_git_history_repository(tmp_path / "history")
    invalid_name = os.fsdecode(b"invalid-\xff.txt")
    invalid_path = repo.scope / invalid_name
    invalid_path.write_text("invalid path bytes\n", encoding="utf-8")
    repo.git("add", "--", f"scope/{invalid_name}")
    repo.git("commit", "-q", "-m", "invalid filename bytes")
    head = repo.git("rev-parse", "HEAD").stdout.strip()

    with pytest.raises(FilesystemError) as unsupported:
        filesystem.git_commit(str(repo.scope), commit=head, head=head)

    assert unsupported.value.status == 422
    assert unsupported.value.message_key == "fs.error.gitPathEncoding"


def test_create_directory_rejects_existing_target(tmp_path):
    created = filesystem.create_directory(str(tmp_path / "new-dir"))

    assert created["kind"] == "dir"
    assert (tmp_path / "new-dir").is_dir()
    with pytest.raises(filesystem.FilesystemError) as excinfo:
        filesystem.create_directory(str(tmp_path / "new-dir"))
    assert excinfo.value.status == 409


def test_is_text_path_recognizes_known_extensions():
    for extension in filesystem.TEXT_EXTENSIONS:
        assert filesystem.is_text_path(f"/tmp/foo{extension}")
    assert filesystem.is_text_path("/tmp/.gitignore")
    assert filesystem.is_text_path("/tmp/.dockerignore")
    assert filesystem.is_text_path("/tmp/.dockerfile")
    assert filesystem.is_text_path("/tmp/Dockerfile")
    assert filesystem.is_text_path("/tmp/Makefile")
    assert filesystem.is_text_path("/tmp/LICENSE")
    assert filesystem.is_text_path("/tmp/README")
    assert filesystem.is_text_path("/tmp/foo.PY")
    assert not filesystem.is_text_path("/tmp/foo.png")
    assert not filesystem.is_text_path("/tmp/foo.PNG")
    assert not filesystem.is_text_path("/tmp/foo.exe")


def test_parse_blame_porcelain_extracts_author_pr_and_uncommitted():
    sha = "a" * 40
    sample = (
        f"{sha} 1 1 1\n"
        "author Jane Doe\n"
        "author-time 1700000000\n"
        "summary Fix the thing (#42)\n"
        "\tcode line one\n"
        f"{sha} 2 2\n"
        "\tcode line two\n"
        "0000000000000000000000000000000000000000 3 3 1\n"
        "author Not Committed Yet\n"
        "author-time 1700000001\n"
        "summary uncommitted\n"
        "\tuncommitted line\n"
    )
    lines = filesystem._parse_blame_porcelain(sample)
    assert lines["1"]["author"] == "Jane Doe"
    assert lines["1"]["pr"] == 42
    assert lines["1"]["summary"] == "Fix the thing (#42)"
    assert lines["1"]["time"] == 1700000000
    # commit headers appear once; line 2 of the same commit reuses them
    assert lines["2"]["author"] == "Jane Doe"
    assert lines["2"]["summary"] == "Fix the thing (#42)"
    # an all-zero sha is the uncommitted sentinel → "You" / "Uncommitted changes"
    assert lines["3"]["author"] == "You"
    assert lines["3"]["summary"] == "Uncommitted changes"
    assert lines["3"]["pr"] is None


def test_blame_file_on_a_tracked_repo_file():
    # AGENTS.md is committed in this repo; blame should return per-line commit info.
    repo_file = str(Path(__file__).resolve().parents[1] / "AGENTS.md")
    result = filesystem.blame_file(repo_file)
    assert result["in_repo"] is True
    assert result["lines"], "expected per-line blame for a tracked file"
    first = result["lines"]["1"]
    assert len(first["sha"]) == 40
    assert first["author"]


def test_rename_path_stages_a_tracked_file_at_its_new_name(tmp_path):
    # A tracked rename stages both names in the repository generation pinned before the namespace move.
    def run(*args):
        subprocess.run(args, cwd=str(tmp_path), check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (tmp_path / "old.txt").write_text("hi\n", encoding="utf-8")
    run("git", "add", "old.txt")
    run("git", "commit", "-qm", "init")

    result = filesystem.rename_path(str(tmp_path / "old.txt"), "new.txt")
    assert result["name"] == "new.txt"
    assert (tmp_path / "new.txt").exists()
    assert not (tmp_path / "old.txt").exists()
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "new.txt"], cwd=str(tmp_path), capture_output=True)
    assert tracked.returncode == 0, "the rename must stage the new path"


def test_rename_path_preserves_unstaged_content_and_index_flags(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "old.txt"
    target.write_text("committed\n", encoding="utf-8")
    git(tmp_path, "add", "old.txt")
    git(tmp_path, "commit", "-m", "initial")
    git(tmp_path, "update-index", "--assume-unchanged", "old.txt")
    target.write_text("working-only\n", encoding="utf-8")

    filesystem.rename_path(str(target), "new.txt")

    assert git(tmp_path, "show", ":new.txt").stdout == "committed\n"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "working-only\n"
    assert git(tmp_path, "ls-files", "-v", "new.txt").stdout.startswith("h ")
    assert not (tmp_path / ".git" / "index.lock").exists()


def test_rename_path_moves_every_tracked_directory_index_entry(tmp_path):
    init_repo(tmp_path)
    source = tmp_path / "old"
    source.mkdir()
    (source / "a.txt").write_text("a\n", encoding="utf-8")
    (source / "b.txt").write_text("b\n", encoding="utf-8")
    git(tmp_path, "add", "old")
    git(tmp_path, "commit", "-m", "initial")

    filesystem.rename_path(str(source), "new")

    assert git(tmp_path, "ls-files").stdout.splitlines() == ["new/a.txt", "new/b.txt"]
    assert not (tmp_path / ".git" / "index.lock").exists()


def test_rename_path_refuses_existing_index_lock_before_filesystem_mutation(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "old.txt"
    target.write_text("committed\n", encoding="utf-8")
    git(tmp_path, "add", "old.txt")
    git(tmp_path, "commit", "-m", "initial")
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("owned by another operation", encoding="utf-8")

    with pytest.raises(FilesystemError) as busy:
        filesystem.rename_path(str(target), "new.txt")

    assert busy.value.status == 409
    assert busy.value.message_key == "fs.error.gitRepositoryChanged"
    assert target.read_text(encoding="utf-8") == "committed\n"
    assert not (tmp_path / "new.txt").exists()
    assert lock.read_text(encoding="utf-8") == "owned by another operation"


def test_rename_path_refuses_concurrent_index_replacement_without_publishing(monkeypatch, tmp_path):
    init_repo(tmp_path)
    target = tmp_path / "old.txt"
    target.write_text("committed\n", encoding="utf-8")
    git(tmp_path, "add", "old.txt")
    git(tmp_path, "commit", "-m", "initial")
    original_prepare = git_ops.prepare_pinned_index_rename
    replacement_bytes = b"BLOCKED_SENTINEL_DO_NOT_EXPOSE"

    def replace_index(scope, new_relative):
        tracked = original_prepare(scope, new_relative)
        replacement = tmp_path / ".git" / "replacement-index"
        replacement.write_bytes(replacement_bytes)
        replacement.replace(tmp_path / ".git" / "index")
        return tracked

    monkeypatch.setattr(git_ops, "prepare_pinned_index_rename", replace_index)

    with pytest.raises(FilesystemError) as changed:
        filesystem.rename_path(str(target), "new.txt")

    assert changed.value.status == 409
    assert changed.value.message_key == "fs.error.gitRepositoryChanged"
    assert (tmp_path / ".git" / "index").read_bytes() == replacement_bytes
    assert not (tmp_path / ".git" / "index.lock").exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "committed\n"


def test_rename_git_staging_keeps_the_authorized_repository_descriptor_live(monkeypatch, tmp_path):
    safe_repo = tmp_path / "safe-repo"
    blocked_repo = tmp_path / ".ssh" / "blocked-repo"
    safe_repo.mkdir()
    blocked_repo.mkdir(parents=True)
    for repo in (safe_repo, blocked_repo):
        init_repo(repo)
        (repo / "item.txt").write_text("base\n", encoding="utf-8")
        git(repo, "add", "item.txt")
        git(repo, "commit", "-m", "base")
    (safe_repo / "item.txt").write_text("safe\n", encoding="utf-8")
    (blocked_repo / "moved.txt").write_text("blocked-untracked\n", encoding="utf-8")
    blocked_status = git(blocked_repo, "status", "--porcelain=v1").stdout.splitlines()
    parked = tmp_path / "safe-repo-parked"
    original_git = git_ops._git_with_pinned_repo
    swapped = False

    def swap_before_stage(repo_handle, args, **kwargs):
        nonlocal swapped
        if args and args[0] == "update-index" and not swapped:
            swapped = True
            safe_repo.rename(parked)
            safe_repo.symlink_to(blocked_repo, target_is_directory=True)
            try:
                return original_git(repo_handle, args, **kwargs)
            finally:
                safe_repo.unlink()
                parked.rename(safe_repo)
        return original_git(repo_handle, args, **kwargs)

    monkeypatch.setattr(git_ops, "_git_with_pinned_repo", swap_before_stage)

    filesystem_io.rename_path(str(safe_repo / "item.txt"), "moved.txt")

    assert swapped is True
    assert git(blocked_repo, "status", "--porcelain=v1").stdout.splitlines() == blocked_status


def test_rename_path_plain_rename_for_untracked_file(tmp_path):
    # No repo / untracked: a plain rename still works (git mv path returns False, caller falls back).
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = filesystem.rename_path(str(tmp_path / "a.txt"), "b.txt")
    assert result["name"] == "b.txt"
    assert (tmp_path / "b.txt").exists() and not (tmp_path / "a.txt").exists()


def test_list_directory_flags_symlinks_with_target(tmp_path):
    # symlink entries carry is_symlink + symlink_target; a symlink to a dir resolves kind=dir,
    # to a file kind=file, and a dangling link is kind=symlink-broken. Plain entries are not flagged.
    (tmp_path / "real_dir").mkdir()
    (tmp_path / "real_file.txt").write_text("hi", encoding="utf-8")
    os.symlink(tmp_path / "real_dir", tmp_path / "link_dir")
    os.symlink(tmp_path / "real_file.txt", tmp_path / "link_file")
    os.symlink(tmp_path / "nope", tmp_path / "link_broken")

    payload = filesystem.list_directory(str(tmp_path))
    by_name = {entry["name"]: entry for entry in payload["entries"]}

    assert by_name["link_dir"]["is_symlink"] is True
    assert by_name["link_dir"]["kind"] == "dir"
    assert by_name["link_dir"]["symlink_target"] == str(tmp_path / "real_dir")

    assert by_name["link_file"]["is_symlink"] is True
    assert by_name["link_file"]["kind"] == "file"
    assert by_name["link_file"]["symlink_target"] == str(tmp_path / "real_file.txt")

    assert by_name["link_broken"]["is_symlink"] is True
    assert by_name["link_broken"]["kind"] == "symlink-broken"
    assert by_name["link_broken"]["symlink_target"] == str(tmp_path / "nope")

    assert by_name["real_file.txt"]["is_symlink"] is False
    assert "symlink_target" not in by_name["real_file.txt"]


def test_lexical_path_rule_and_worker_parse_are_one_rule_split_at_the_thread_boundary(monkeypatch):
    """One rule, two entry points: the lexical half is web-safe, the parse half may block.

    `os.path.expanduser` on `~user/...` is an NSS/passwd lookup that can hang on a networked
    passwd source, so it belongs to the worker.  `parsed_request_path` is the only caller that
    adds it, and it reaches the refusals through `validate_request_path_lexical` rather than
    restating them, so acceptance and execution cannot disagree.
    """

    expansions = []
    real_expanduser = os.path.expanduser

    def recording_expanduser(path):
        expansions.append(path)
        return real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", recording_expanduser)

    # The lexical half returns the request string untouched and consults no name service.
    assert filesystem_paths.validate_request_path_lexical("~alice/repo/note.txt") == "~alice/repo/note.txt"
    assert filesystem_paths.validate_request_path_lexical("/repo/note.txt") == "/repo/note.txt"
    assert expansions == [], expansions

    # The worker half applies the same rule, then expands.
    assert filesystem_paths.parsed_request_path("~/repo/note.txt") == Path(real_expanduser("~/repo/note.txt"))
    assert expansions == ["~/repo/note.txt"], expansions

    # Every refusal is decided before the expansion, by the shared owner.
    expansions.clear()
    for raw, message_key in (
        ("", "fs.error.pathRequired"),
        (None, "fs.error.pathRequired"),
        ("~alice/bad\nname", "fs.error.pathIllegal"),
        ("~alice/bad\x00name", "fs.error.pathIllegal"),
        ("relative/note.txt", "fs.error.pathAbsolute"),
    ):
        for entry_point in (filesystem_paths.validate_request_path_lexical, filesystem_paths.parsed_request_path):
            with pytest.raises(FilesystemError) as refusal:
                entry_point(raw)
            assert refusal.value.message_key == message_key, (entry_point.__name__, raw)
    assert expansions == [], "a refused request must never reach the name service"

    # The rule has exactly one implementation: only the lexical owner raises these three.
    source = Path(filesystem_paths.__file__).read_text(encoding="utf-8")
    for message_key in ("fs.error.pathRequired", "fs.error.pathIllegal", "fs.error.pathAbsolute"):
        assert source.count(message_key) == 1, f"{message_key} has a second implementation"
    # Compiled names, not source text: the lexical owner cannot reach a name-service call at all,
    # and the expansion exists in exactly one function.
    assert "expanduser" not in filesystem_paths.validate_request_path_lexical.__code__.co_names
    assert "expanduser" in filesystem_paths.parsed_request_path.__code__.co_names
    assert "validate_request_path_lexical" in filesystem_paths.parsed_request_path.__code__.co_names


def _isolated_git_object_cache(monkeypatch, tmp_path):
    """Point the boot-local loose-object cache at a private root so one test cannot warm another."""
    root = tmp_path / "object-cache-root"
    root.mkdir()
    monkeypatch.setattr(git_ops.tempfile, "tempdir", str(root))
    return root


_LOOSE_OBJECT_NAME = re.compile(r"(?:[0-9a-f]{38}|[0-9a-f]{62})")


def _count_loose_object_source_reads(monkeypatch):
    """Count how many loose objects a view build opens out of the repository itself.

    Deliberately measured at the syscall rather than through an internal helper, so the same
    assertion is meaningful whatever the view build does underneath.
    """

    reads = {"count": 0}
    original = git_ops.os.open

    def counted(path, *args, **kwargs):
        if isinstance(path, str) and _LOOSE_OBJECT_NAME.fullmatch(path) and "dir_fd" in kwargs:
            reads["count"] += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(git_ops.os, "open", counted)
    return reads


def test_a_repeat_git_view_build_republishes_loose_objects_without_rereading_them(monkeypatch, tmp_path):
    # Reading every loose object costs ~2.4 ms over NFS, so a repository whose objects are not packed
    # spent ~12 s rebuilding the same view on every request and overran the view deadline. The second
    # build of an unchanged store must read nothing from the repository at all.
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "first")

    _isolated_git_object_cache(monkeypatch, tmp_path)
    reads = _count_loose_object_source_reads(monkeypatch)

    first = filesystem.blame_file(str(repo / "tracked.txt"))
    assert first["lines"], "expected the first build to produce blame output"
    assert reads["count"] > 0, "the first build must read the objects it caches"

    reads["count"] = 0
    second = filesystem.blame_file(str(repo / "tracked.txt"))
    assert second["lines"] == first["lines"]
    assert reads["count"] == 0, "a repeat build must not reread any loose object"


def test_loose_objects_are_cached_even_when_they_cannot_be_hardlinked(monkeypatch, tmp_path):
    # The cache lives on boot-local storage, so a repository on a network filesystem can never be
    # hardlinked into it. That EXDEV failure used to be swallowed, leaving the cache permanently empty
    # for exactly the repositories the cache exists to serve.
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "first")

    _isolated_git_object_cache(monkeypatch, tmp_path)
    real_link = os.link

    def link_without_cross_device_support(source, target, **kwargs):
        if "src_dir_fd" in kwargs:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(git_ops.os, "link", link_without_cross_device_support)
    reads = _count_loose_object_source_reads(monkeypatch)

    first = filesystem.blame_file(str(repo / "tracked.txt"))
    assert first["lines"], "expected blame output when the objects cannot be hardlinked"
    assert reads["count"] > 0

    reads["count"] = 0
    second = filesystem.blame_file(str(repo / "tracked.txt"))
    assert second["lines"] == first["lines"]
    assert reads["count"] == 0, "a copied cache entry must serve the next build"


def _git_metadata_that_always_fails(monkeypatch, message_key="fs.error.gitHistoryTooLarge"):
    """Make every Git view build for a path fail the way an oversized or racing repository does."""
    def refuse(handle, **kwargs):
        raise FilesystemError("git view refused", status=413, message_key=message_key)
    monkeypatch.setattr(git_ops, "pinned_file_git_metadata", refuse)


def test_open_returns_a_readable_file_when_git_enrichment_cannot_be_produced(monkeypatch, tmp_path):
    # Reading a file and describing its Git history are two questions, and the second must not be
    # able to answer the first. Git enrichment used to propagate out of read_file, so a repository
    # whose objects are not packed left the user unable to open a file that had already been read.
    target = tmp_path / "readable.txt"
    target.write_text("first\nsecond\n", encoding="utf-8")
    _git_metadata_that_always_fails(monkeypatch)

    payload = filesystem.read_file(str(target))

    assert payload["content"] == "first\nsecond\n"
    assert payload["size"] == len("first\nsecond\n")
    assert payload["git_enrichment"] == {"available": False, "reason": "fs.error.gitHistoryTooLarge"}
    assert payload["git_root"] == ""
    assert payload["git_history"] == []
    assert payload["git_has_history"] is False


def test_validating_a_path_survives_git_enrichment_that_cannot_be_produced(monkeypatch, tmp_path):
    # Validation decides whether Open is offered at all, so it carries the same rule as the read.
    target = tmp_path / "readable.txt"
    target.write_text("body\n", encoding="utf-8")
    _git_metadata_that_always_fails(monkeypatch)

    info = filesystem.path_info(str(target))

    assert info["kind"] == "file"
    assert info["size"] == len("body\n")
    assert info["git_enrichment"] == {"available": False, "reason": "fs.error.gitHistoryTooLarge"}
    assert info["repo_root"] == ""


@pytest.mark.parametrize("consumer", ["read", "info"])
def test_open_still_refuses_when_the_repository_says_it_is_not_the_one_we_authorized(monkeypatch, tmp_path, consumer):
    # Degrading is scoped to size and budget. A repository reporting that it changed underneath the
    # pin is how an object store swapped in from elsewhere is caught, and serving the file anyway
    # would turn that detection into a shrug.
    target = tmp_path / "readable.txt"
    target.write_text("body\n", encoding="utf-8")
    _git_metadata_that_always_fails(monkeypatch, message_key="fs.error.gitRepositoryChanged")

    with pytest.raises(FilesystemError) as caught:
        filesystem.read_file(str(target)) if consumer == "read" else filesystem.path_info(str(target))

    assert caught.value.message_key == "fs.error.gitRepositoryChanged"


def test_open_reports_git_enrichment_as_available_when_the_repository_answers(tmp_path):
    # The failure path must not become the only path: a healthy repository still decorates the read.
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "first")

    payload = filesystem.read_file(str(repo / "tracked.txt"))

    assert payload["content"] == "one\n"
    assert payload["git_enrichment"] == {"available": True, "reason": ""}
    assert payload["git_root"] == str(repo)
    assert payload["git_tracked"] is True


def test_git_enrichment_gets_a_shorter_budget_than_a_full_git_view_build(tmp_path):
    # Open must not inherit the full view-build deadline; a reader who asked for a file is not
    # asking to wait out a repository's whole object store.
    assert git_ops.GIT_OPTIONAL_METADATA_TIMEOUT_SECONDS < git_ops.GIT_VIEW_BUILD_TIMEOUT_SECONDS
