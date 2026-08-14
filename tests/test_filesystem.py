import fcntl
import inspect
import itertools
import json
import os
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
            Path("/dev/fd") / str(descriptor),
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
    assert filesystem.listing._resolved_symlink_target(link) == safe / "item"

    safe.rename(tmp_path / "safe-old")
    safe.symlink_to(blocked, target_is_directory=True)

    assert filesystem.listing._resolved_symlink_target(link) == blocked / "item"


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
    assert filesystem.listing._resolved_symlink_target(link) == second_target / "subdirectory" / "item"
    assert filesystem.listing._resolved_symlink_target(link) == first_target / "subdirectory" / "item"


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
    listed_link = {entry["name"]: entry for entry in payload["entries"]}["link"]

    assert swapped is True
    assert listed_link["size"] == 1
    assert listed_link["symlink_target"] == str(safe_target)
    assert listed_link["realpath"] == str(safe_target)


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

    def swap_before_repo_discovery(handle, *, operation=""):
        nonlocal swapped
        if not swapped:
            swapped = True
            target.rename(tmp_path / "safe-old.py")
            target.symlink_to(blocked_target)
        return original_pinned_repo_root(handle, operation=operation)

    monkeypatch.setattr(git_ops, "_pinned_repo_root", swap_before_repo_discovery)

    payload = git_ops.diff_file(str(target))

    assert swapped is True
    assert "FAKE_DIFF_CONSUMER_SECRET" not in payload["diff"]
    assert "+safe" in payload["diff"]


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

    filesystem_search._annotate_search_dedupe_fields(entry)

    assert state["swapped"] is True
    assert entry["realpath"] == str(safe)
    assert entry["size"] == 1
    assert entry["file_id"] != "stale"


def test_indexed_search_annotation_omits_stale_metadata_when_current_path_is_blocked(tmp_path):
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

    filesystem_search._annotate_search_dedupe_fields(entry)

    assert not {"realpath", "size", "file_id", "file_identity"} & entry.keys()


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


def test_list_directory_eagerly_returns_git_repo_info(tmp_path):
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
    assert len(calls) == 2

    assert metadata.invalidate_git_metadata_paths([repo / "tracked.txt"]) == set()
    third = git_ops.git_repo_info(repo, include_status=False)
    assert third["branch"] == "main"
    assert len(calls) == 4, "watcher-owned invalidation must make Finder recompute"


def _reset_repository_generation(root):
    git_ops._REPOSITORY_GENERATIONS.pop(str(Path(root).expanduser().resolve(strict=False)), None)


def test_private_repository_signature_distinguishes_same_commit_branch_switch_without_git_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "one")

    on_master = git_ops.private_repository_signature(repo)
    # A second branch at the SAME commit: identical tree, identical OID, different symbolic ref.
    git(repo, "branch", "other")
    git(repo, "checkout", "other")
    on_other = git_ops.private_repository_signature(repo)

    assert on_master != on_other, "an identical-tree branch switch must change the signature"
    assert on_master[1] == on_other[1], "the OID is unchanged across a same-commit branch switch"
    assert on_master[0] == "master" and on_other[0] == "other"
    # The signature is path-free: it never names a `.git` control path.
    assert not any(".git" in part for part in (*on_master, *on_other))


def test_private_repository_signature_is_unknown_for_non_repo_and_unborn(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_ops.private_repository_signature(plain) == git_ops.REPOSITORY_SIGNATURE_UNKNOWN

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    init_repo(unborn)  # a repository with a symbolic HEAD but no commit yet
    assert git_ops.private_repository_signature(unborn) == git_ops.REPOSITORY_SIGNATURE_UNKNOWN


def test_repository_generation_advances_on_identical_tree_branch_switch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "one")
    _reset_repository_generation(repo)

    first = git_ops.repository_generation(repo)
    assert first == 1
    assert git_ops.repository_generation(repo) == first, "an unchanged HEAD does not advance the generation"

    git(repo, "branch", "other")
    git(repo, "checkout", "other")
    assert git_ops.repository_generation(repo) == first + 1, "a same-commit branch switch advances the generation"
    assert git_ops.repository_generation(repo) == first + 1


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

    assert git_ops.repository_generation(tenant_b) == 0, "an unreadable repository holds a null generation"
    assert git_ops.repository_generation(tenant_a) == 1
    git(tenant_a, "branch", "other")
    git(tenant_a, "checkout", "other")
    # A's real branch switch advances A; B's malformed state neither advances nor is disturbed.
    assert git_ops.repository_generation(tenant_a) == 2
    assert git_ops.repository_generation(tenant_b) == 0


def test_repository_generation_holds_through_a_transient_unreadable_head(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _reset_repository_generation(root)
    state = {"head_oid": "c0ffee", "head_rc": 0}

    def runner(args):
        if args[:1] == ["rev-parse"]:
            return subprocess.CompletedProcess(args, state["head_rc"], f"{state['head_oid']}\n", "")
        return subprocess.CompletedProcess(args, 0, "master\n", "")

    assert git_ops.repository_generation(root, runner=runner) == 1
    # A transient failure to read HEAD is inconclusive: hold the generation.
    state["head_rc"] = 1
    assert git_ops.repository_generation(root, runner=runner) == 1
    # Recovering to the SAME signature must not flap the generation.
    state["head_rc"] = 0
    assert git_ops.repository_generation(root, runner=runner) == 1
    # A genuine HEAD change after recovery advances exactly once.
    state["head_oid"] = "beefbeef"
    assert git_ops.repository_generation(root, runner=runner) == 2


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
    assert len(calls) == 4

    now[0] = 115.0
    git_ops.git_repo_info(repo_short, include_status=False)
    git_ops.git_repo_info(repo_long, include_status=False)
    assert len(calls) == 6, "only the short-TTL repository should revalidate"


@pytest.mark.parametrize(("timeout", "expected_call_count"), [(0.001, 0), (1.0, 3)])
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
    listed_link = {entry["name"]: entry for entry in filesystem.list_directory(str(allowed))["entries"]}["link.txt"]
    assert "file_id" not in listed_link
    assert "file_identity" not in listed_link
    assert "realpath" not in listed_link


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
    def denied_list(_path, *, performance_details=None, requested_path=None, operation="list_directory"):
        del requested_path, operation
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
            next(filesystem_paths.walk_directory(root_fd, include_directory=include_directory))
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


def test_delete_path_removes_directory_tree(tmp_path):
    target = tmp_path / "dir"
    (target / "nested").mkdir(parents=True)
    (target / "nested" / "file.txt").write_text("hello", encoding="utf-8")

    result = filesystem.delete_path(str(target))

    assert result["deleted"] is True
    assert result["kind"] == "dir"
    assert not target.exists()


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


def test_path_info_returns_git_relative_path(tmp_path):
    git(tmp_path, "init")
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('hi')\n", encoding="utf-8")

    result = filesystem.path_info(str(target))

    assert result["repo_root"] == str(tmp_path)
    assert result["relative_path"] == "src/main.py"
    assert result["kind"] == "file"


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
        if args and args[0] == "add" and not swapped:
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
    assert filesystem._git_mv_if_tracked(tmp_path / "b.txt", tmp_path / "c.txt") is False


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
