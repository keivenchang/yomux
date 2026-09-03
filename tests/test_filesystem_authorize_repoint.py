"""Filesystem descriptor-authorization and repoint regressions."""

from __future__ import annotations

import io
import errno
import fcntl
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from yolomux_lib import filesystem
from yolomux_lib.filesystem import git_ops
from yolomux_lib.filesystem import io_ops
from yolomux_lib.filesystem import listing
from yolomux_lib.filesystem import paths
from yolomux_lib.filesystem import search
from yolomux_lib.search import bfs_index
from yolomux_lib.search import file_index

from _git_helpers import git, init_repo


BLOCKED_SENTINEL = "BLOCKED_SENTINEL_DO_NOT_EXPOSE"
PROPERTY_MATRIX_ENV = "YOLOMUX_PROPERTY_MATRIX_TSV"


def _jsonable(value):
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _record_property_matrix_row(
    scenario: str,
    adapter: str,
    coverage: str,
    expected_result,
    actual_result,
    expected_side_effects,
    actual_side_effects,
) -> None:
    output = os.environ.get(PROPERTY_MATRIX_ENV, "").strip()
    if not output:
        return
    columns = (
        scenario,
        adapter,
        coverage,
        json.dumps(_jsonable(expected_result), sort_keys=True, separators=(",", ":")),
        json.dumps(_jsonable(actual_result), sort_keys=True, separators=(",", ":")),
        json.dumps(_jsonable(expected_side_effects), sort_keys=True, separators=(",", ":")),
        json.dumps(_jsonable(actual_side_effects), sort_keys=True, separators=(",", ":")),
    )
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        header = "scenario\tadapter\tcoverage\texpected_result\tactual_result\texpected_side_effects\tactual_side_effects\n"
        payload = "\t".join(columns) + "\n"
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, header.encode("utf-8"))
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)


def _node_state(path: Path) -> dict[str, object]:
    if not path.exists() and not path.is_symlink():
        return {"kind": "missing"}
    node_stat = path.lstat()
    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path)}
    if path.is_dir():
        return {"kind": "dir", "entries": sorted(child.name for child in path.iterdir())}
    return {"kind": "file", "bytes_hex": path.read_bytes().hex(), "size": int(node_stat.st_size)}


def _policy_side_effects(root: Path, target: Path, source: Path | None, sentinel_path: Path) -> dict[str, object]:
    return {
        "target": _node_state(target),
        "source": _node_state(source) if source is not None else None,
        "moved": _node_state(root / "moved.txt"),
        "blocked_sentinel": _node_state(sentinel_path),
    }


def _expected_error(policy_case: str, status: str) -> dict[str, object]:
    if status == "400":
        message_key = "fs.error.notDirectory"
    elif status == "404":
        message_key = "common.pathNotFound"
    elif status == "409":
        message_key = "fs.error.targetExists"
    elif policy_case == "outside":
        message_key = "fs.error.outsideRoots"
    else:
        message_key = "fs.error.credentialBlocked"
    return {"status": int(status), "message_key": message_key}


def _policy_result_observation(adapter: str, result) -> dict[str, object]:
    if adapter == "read":
        return {"content_hex": result["content"].encode("utf-8").hex(), "size": result["size"]}
    if adapter == "info":
        return {key: result.get(key) for key in ("kind", "size", "file_id")}
    if adapter == "list":
        return {"entries": [
            {key: entry.get(key) for key in ("name", "kind", "size", "symlink_target") if key in entry}
            for entry in result["entries"]
        ]}
    if adapter == "count":
        return {key: result.get(key) for key in ("kind", "files", "recursive")}
    if adapter == "zip":
        return {"bytes_hex": result.hex()}
    if adapter == "search":
        return {"files": [
            {key: entry.get(key) for key in ("name", "kind", "size")}
            for entry in result.get("files", [])
        ]}
    if adapter == "index":
        return {"root": result.get("root"), "state_present": bool(result.get("state"))}
    if adapter == "diff":
        working = str(result.get("working") or "")
        return {
            "working_hex": working.encode("utf-8").hex(),
            "working_missing": bool(result.get("working_missing")),
            "blocked_sentinel_bytes": working.encode("utf-8").count(BLOCKED_SENTINEL.encode("utf-8")),
        }
    if adapter == "create":
        return {key: result.get(key) for key in ("path", "created", "kind")}
    if adapter == "write":
        path = Path(result["path"])
        return {"path": result.get("path"), "size": result.get("size"), "target_bytes_hex": path.read_bytes().hex()}
    if adapter == "rename":
        return {key: result.get(key) for key in ("path", "old_path", "name")}
    if adapter == "delete":
        return {key: result.get(key) for key in ("path", "deleted", "kind")}
    raise AssertionError(f"unknown result adapter: {adapter}")


def _expected_policy_result(policy_case: str, adapter: str, target: Path) -> dict[str, object]:
    if adapter == "read":
        return {"content_hex": b"safe\n".hex(), "size": 5}
    if adapter == "info":
        target_stat = target.stat()
        return {"kind": "file", "size": 5, "file_id": f"{target_stat.st_dev}:{target_stat.st_ino}"}
    if adapter == "list":
        name = "hardlink.txt" if policy_case == "hardlink" else "safe.txt"
        size = 5 if policy_case == "hardlink" else 4
        return {"entries": [{"name": name, "kind": "file", "size": size}]}
    if adapter == "count":
        return {"kind": "dir", "files": 1, "recursive": True}
    if adapter == "zip":
        return {"bytes_hex": (b"safe\n" if policy_case == "hardlink" else b"safe").hex()}
    if adapter == "search":
        if policy_case == "hardlink":
            return {"files": []}
        name = "hardlink.txt" if policy_case == "hardlink" else "safe.txt"
        size = 5 if policy_case == "hardlink" else 4
        return {"files": [{"name": name, "kind": "file", "size": size}]}
    if adapter == "index":
        return {"root": str(target.resolve()), "state_present": True}
    if adapter == "diff":
        return {
            "working_hex": (b"" if policy_case == "missing" else b"safe\n").hex(),
            "working_missing": policy_case == "missing",
            "blocked_sentinel_bytes": 0,
        }
    if adapter == "create":
        return {"created": True, "kind": "dir", "path": str(target)}
    if adapter == "write":
        return {"size": len(b"updated"), "path": str(target), "target_bytes_hex": b"updated".hex()}
    if adapter == "rename":
        return {"path": str(target.with_name("moved.txt")), "old_path": str(target), "name": "moved.txt"}
    if adapter == "delete":
        return {"path": str(target), "deleted": True, "kind": "file"}
    raise AssertionError(f"unknown expected adapter: {adapter}")


def _assert_policy_result(policy_case: str, adapter: str, target: Path, result) -> None:
    actual = _policy_result_observation(adapter, result)
    expected = _expected_policy_result(policy_case, adapter, target)
    assert actual == expected


def _expected_policy_side_effects(
    adapter: str,
    before: dict[str, object],
    target: Path,
) -> dict[str, object]:
    expected = json.loads(json.dumps(before))
    if adapter == "create":
        expected["target"] = {"kind": "dir", "entries": []}
    elif adapter == "write":
        updated = {"kind": "file", "bytes_hex": b"updated".hex(), "size": len(b"updated")}
        if before["target"].get("kind") != "symlink":
            expected["target"] = updated
        if before["source"] is not None:
            expected["source"] = updated
    elif adapter == "rename":
        expected["target"] = {"kind": "missing"}
        expected["moved"] = before["target"]
    elif adapter == "delete":
        expected["target"] = {"kind": "missing"}
    return expected


def _assert_and_record_property_matrix_row(
    scenario: str,
    adapter: str,
    coverage: str,
    expected_result,
    actual_result,
    expected_side_effects,
    actual_side_effects,
) -> None:
    assert _jsonable(actual_result) == _jsonable(expected_result)
    assert _jsonable(actual_side_effects) == _jsonable(expected_side_effects)
    assert BLOCKED_SENTINEL not in repr(actual_result)
    _record_property_matrix_row(
        scenario,
        adapter,
        coverage,
        expected_result,
        actual_result,
        expected_side_effects,
        actual_side_effects,
    )


def _run_operation(operation: str, target: Path):
    if operation == "read":
        return io_ops.read_file(str(target))
    if operation == "read_raw":
        return io_ops.read_raw(str(target))
    if operation == "copy":
        sink = io.BytesIO()
        result = io_ops.copy_raw_to(str(target), sink)
        return result, sink.getvalue()
    if operation == "path_info":
        return io_ops.path_info(str(target))
    if operation == "blame":
        return git_ops.blame_file(str(target))
    if operation == "count":
        return io_ops.count_directory_files(str(target))
    if operation == "write":
        return io_ops.write_file(str(target), "updated")
    if operation == "rename":
        return io_ops.rename_path(str(target), "moved.txt")
    if operation == "delete":
        return io_ops.delete_path(str(target))
    raise AssertionError(f"unknown test operation: {operation}")


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


@pytest.mark.parametrize("index_state", ["ready", "follower", "warming"])
def test_indexed_search_drops_a_complete_repointed_secret_row(repoint_tree, monkeypatch, tmp_path, index_state):
    """Every in-memory, persisted, warming, and recent result drops a row that no longer authorizes."""
    root, blocked = repoint_tree
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
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
    )
    assert index.ready is True
    if index_state == "follower":
        file_index.clear_memory_indexes()
        monkeypatch.setattr(file_index, "background_owner_can_build", lambda: False)
    elif index_state == "warming":
        index.ready = False
        monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: True)

    safe.unlink()
    safe.symlink_to(blocked_target)

    for query in ("child", ""):
        payload = filesystem.search_files(str(root), query, recursive=True)
        assert payload["files"] == []
        assert BLOCKED_SENTINEL not in repr(payload["files"])


def test_indexed_search_annotation_consumes_the_authorized_root_generation(repoint_tree, monkeypatch):
    root, _blocked = repoint_tree
    safe = root / "child.txt"
    safe.write_text("safe\n", encoding="utf-8")
    safe_identity = paths._physical_file_identity(safe, resolved=safe, stat_result=safe.stat())
    policy = search._search_index_policy(root)
    index = file_index.build_now(
        root,
        policy["skip_dirs"],
        policy["exclude_path"],
        policy["exclude_signature"],
        persist_enabled=False,
    )
    assert index.ready is True

    replacement_root = root.with_name(f"{root.name}-replacement")
    replacement_root.mkdir()
    (replacement_root / "child.txt").write_text(BLOCKED_SENTINEL, encoding="utf-8")
    authorized_root = root.with_name(f"{root.name}-authorized")
    original_annotate = search._annotate_search_dedupe_fields
    swapped = False

    def replace_root_before_annotation(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            root.rename(authorized_root)
            replacement_root.rename(root)
        return original_annotate(*args, **kwargs)

    monkeypatch.setattr(search, "_annotate_search_dedupe_fields", replace_root_before_annotation)
    payload = filesystem.search_files(str(root), "child", recursive=True)

    assert swapped is True
    assert len(payload["files"]) == 1
    assert payload["files"][0]["size"] == len("safe\n")
    assert payload["files"][0]["file_identity"] == safe_identity["file_identity"]
    assert payload["files"][0]["size"] != len(BLOCKED_SENTINEL)


@pytest.mark.parametrize(
    ("follower", "expected_state"),
    [
        pytest.param(False, "warming", id="ready"),
        pytest.param(True, "follower", id="follower-ready"),
    ],
)
def test_indexed_search_rejects_rows_from_a_replaced_root_generation(
    repoint_tree,
    monkeypatch,
    tmp_path,
    follower,
    expected_state,
):
    """A pathname reused for another directory may not inherit the prior directory's index rows."""
    root, _blocked = repoint_tree
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    stale_name = f"{BLOCKED_SENTINEL}.txt"
    (root / stale_name).write_text(BLOCKED_SENTINEL, encoding="utf-8")
    policy = search._search_index_policy(root)
    built = file_index.build_now(
        root,
        policy["skip_dirs"],
        policy["exclude_path"],
        policy["exclude_signature"],
    )
    assert built.ready is True

    if follower:
        file_index.clear_memory_indexes()
        monkeypatch.setattr(file_index, "background_owner_can_build", lambda: False)
    else:
        monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: True)

    old_root = root.with_name(f"{root.name}-old-generation")
    root.rename(old_root)
    root.mkdir()
    (root / "new.txt").write_text("new", encoding="utf-8")

    payload = filesystem.search_files(str(root), BLOCKED_SENTINEL, recursive=True)

    assert payload["files"] == []
    assert payload["index_state"] == expected_state
    assert all(BLOCKED_SENTINEL not in repr(row) for row in payload["files"])


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
    assert payload["changes"] == []
    assert BLOCKED_SENTINEL not in repr(payload["changes"])


def test_delta_read_on_an_unauthorized_root_is_refused(repoint_tree):
    """Step 3: safe-root containment applies to a delta read too -- a root outside the authorized set
    fails closed before any journal is read."""
    root, _ = repoint_tree
    outside = str(root.parent)  # the authorized root's parent is not itself authorized
    with pytest.raises(paths.FilesystemError):
        filesystem.search_files(outside, "child", cursor="any-cursor")


@pytest.mark.parametrize("dirty_kind", ["file", "subtree"])
def test_dirty_index_refresh_never_opens_a_repointed_generation(
    repoint_tree,
    monkeypatch,
    tmp_path,
    dirty_kind,
):
    root, blocked = repoint_tree
    dirty = root / ("dirty.txt" if dirty_kind == "file" else "dirty")
    if dirty_kind == "file":
        dirty.write_text("safe", encoding="utf-8")
    else:
        dirty.mkdir()
        (dirty / "safe.txt").write_text("safe", encoding="utf-8")
    blocked_target = blocked / ("secret.txt" if dirty_kind == "file" else "secrets")
    if dirty_kind == "file":
        blocked_target.write_text(BLOCKED_SENTINEL * 128, encoding="utf-8")
    else:
        blocked_target.mkdir()
        (blocked_target / "secret.txt").write_text(BLOCKED_SENTINEL * 128, encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    policy = search._search_index_policy(root)
    index = file_index.build_now(
        root,
        policy["skip_dirs"],
        policy["exclude_path"],
        policy["exclude_signature"],
        persist_enabled=False,
    )
    index.dirty_paths.add(dirty)
    parked = dirty.with_name(f"{dirty.name}-authorized")
    repointed = False
    original_authorize = paths._authorize_requested_path

    def authorize_then_repoint(requested, resolved, *, operation, observe_name=True):
        nonlocal repointed
        result = original_authorize(
            requested,
            resolved,
            operation=operation,
            observe_name=observe_name,
        )
        if requested == dirty and not repointed:
            repointed = True
            dirty.rename(parked)
            dirty.symlink_to(blocked_target, target_is_directory=blocked_target.is_dir())
        return result

    monkeypatch.setattr(paths, "_authorize_requested_path", authorize_then_repoint)

    file_index._run_build(
        index,
        policy["skip_dirs"],
        policy["exclude_path"],
        policy["exclude_signature"],
        operation="dirty_index_refresh",
    )

    assert repointed is True
    assert all(BLOCKED_SENTINEL not in repr(entry) for entry in index.entries)
    assert all(entry[3] != len(BLOCKED_SENTINEL * 128) for entry in index.entries)
    assert all("secret.txt" not in entry[2] for entry in index.entries)
    assert all(not Path(entry[0]).is_relative_to(dirty) for entry in index.entries)
    if dirty_kind == "file":
        assert blocked_target.read_text(encoding="utf-8") == BLOCKED_SENTINEL * 128
    else:
        assert (blocked_target / "secret.txt").read_text(encoding="utf-8") == BLOCKED_SENTINEL * 128


def test_dirty_index_refresh_refuses_a_regular_file_replacement_after_authorization(
    repoint_tree,
    monkeypatch,
    tmp_path,
):
    root, blocked = repoint_tree
    dirty = root / "dirty.txt"
    dirty.write_text("safe", encoding="utf-8")
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text(BLOCKED_SENTINEL * 128, encoding="utf-8")
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "index")
    policy = search._search_index_policy(root)
    index = file_index.build_now(
        root,
        policy["skip_dirs"],
        policy["exclude_path"],
        policy["exclude_signature"],
        persist_enabled=False,
    )
    index.dirty_paths.add(dirty)
    parked = dirty.with_name("dirty-authorized.txt")
    original_authorize = paths._authorize_requested_path
    swapped = False

    def authorize_then_replace(requested, resolved, *, operation, observe_name=True):
        nonlocal swapped
        result = original_authorize(
            requested,
            resolved,
            operation=operation,
            observe_name=observe_name,
        )
        if requested == dirty and not swapped:
            swapped = True
            dirty.rename(parked)
            os.link(blocked_target, dirty)
        return result

    monkeypatch.setattr(paths, "_authorize_requested_path", authorize_then_replace)
    file_index._run_build(
        index,
        policy["skip_dirs"],
        policy["exclude_path"],
        policy["exclude_signature"],
        operation="dirty_index_refresh",
    )

    assert swapped is True
    assert all(entry[3] != len(BLOCKED_SENTINEL * 128) for entry in index.entries)
    assert BLOCKED_SENTINEL not in repr(index.entries)


@pytest.mark.parametrize(
    ("operation", "final_consumer"),
    [
        pytest.param("read", "identity", id="read"),
        pytest.param("read_raw", "sniff", id="read-raw"),
        pytest.param("copy", "sniff", id="copy"),
        pytest.param("path_info", "identity", id="path-info"),
        pytest.param("blame", "blame", id="blame"),
    ],
)
def test_single_file_descriptor_stays_live_through_the_final_consumer(
    repoint_tree,
    monkeypatch,
    operation,
    final_consumer,
):
    root, blocked = repoint_tree
    target = root / "safe.txt"
    target.write_text("safe\n", encoding="utf-8")
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text(f"{BLOCKED_SENTINEL}\n{BLOCKED_SENTINEL}\n", encoding="utf-8")
    init_repo(root)
    git(root, "add", target.name)
    git(root, "commit", "-m", "safe baseline")
    parked = root / "safe-authorized.txt"
    captured_descriptors = []
    live_checks = []
    repointed = False
    original_init = paths.SafePathHandle.__init__

    def capture_target_descriptor(handle, requested, resolved, descriptor):
        original_init(handle, requested, resolved, descriptor)
        if requested == target:
            captured_descriptors.append(descriptor)

    def assert_target_descriptor_live():
        assert captured_descriptors
        os.fstat(captured_descriptors[-1])
        live_checks.append(captured_descriptors[-1])

    class RepointAfterPin:
        def name_observed(self, observed_operation, requested_path):
            del observed_operation, requested_path

        def authority_pinned(self, observed_operation, requested_path):
            nonlocal repointed
            expected = {
                "read": "read_file",
                "read_raw": "read_raw",
                "copy": "read_raw",
                "path_info": "path_info",
                "blame": "blame_file",
            }[operation]
            if observed_operation == expected and requested_path == target and not repointed:
                repointed = True
                target.rename(parked)
                target.symlink_to(blocked_target)

    monkeypatch.setattr(paths.SafePathHandle, "__init__", capture_target_descriptor)
    if final_consumer == "identity":
        original_consumer = paths._physical_file_identity

        def checked_identity(*args, **kwargs):
            assert_target_descriptor_live()
            return original_consumer(*args, **kwargs)

        monkeypatch.setattr(paths, "_physical_file_identity", checked_identity)
    elif final_consumer == "sniff":
        original_consumer = io_ops._sniff_raw_mime

        def checked_sniff(data):
            assert_target_descriptor_live()
            return original_consumer(data)

        monkeypatch.setattr(io_ops, "_sniff_raw_mime", checked_sniff)
    else:
        original_consumer = git_ops._parse_blame_porcelain

        def checked_blame(text):
            assert_target_descriptor_live()
            return original_consumer(text)

        monkeypatch.setattr(git_ops, "_parse_blame_porcelain", checked_blame)

    result = None
    error = None
    with paths.observe_authorization(RepointAfterPin()):
        try:
            result = _run_operation(operation, target)
        except paths.FilesystemError as caught:
            error = caught

    assert repointed is True
    assert target.is_symlink()
    assert blocked_target.read_text(encoding="utf-8") == f"{BLOCKED_SENTINEL}\n{BLOCKED_SENTINEL}\n"
    rendered = repr(result) if error is None else str(error)
    assert BLOCKED_SENTINEL not in rendered
    assert str(blocked_target) not in rendered
    if error is not None:
        assert (error.status, error.message_key) == (409, "fs.error.gitRepositoryChanged")
    else:
        assert live_checks == [captured_descriptors[-1]]
        if operation == "read":
            assert result["content"] == "safe\n"
        elif operation == "read_raw":
            assert result[0] == b"safe\n"
        elif operation == "copy":
            assert result[1] == b"safe\n"
        elif operation == "path_info":
            assert result["size"] == len(b"safe\n")
            parked_stat = parked.stat()
            assert result["file_id"] == f"{parked_stat.st_dev}:{parked_stat.st_ino}"
        else:
            assert len(result["lines"]) == 1
    with pytest.raises(OSError):
        os.fstat(captured_descriptors[-1])


def test_recursive_count_keeps_a_repointed_descendant_descriptor(repoint_tree):
    root, blocked = repoint_tree
    target = root / "tree"
    nested = target / "nested"
    nested.mkdir(parents=True)
    (nested / "safe.txt").write_text("safe", encoding="utf-8")
    (blocked / "first.txt").write_text(BLOCKED_SENTINEL, encoding="utf-8")
    (blocked / "second.txt").write_text(BLOCKED_SENTINEL, encoding="utf-8")
    parked = target / "nested-authorized"
    repointed = False

    class RepointAfterPin:
        def name_observed(self, operation, requested_path):
            del operation, requested_path

        def authority_pinned(self, operation, requested_path):
            nonlocal repointed
            if operation == "count_directory_files" and requested_path == nested and not repointed:
                repointed = True
                nested.rename(parked)
                nested.symlink_to(blocked, target_is_directory=True)

    with paths.observe_authorization(RepointAfterPin()):
        result = io_ops.count_directory_files(str(target))

    assert repointed is True
    assert result == {"path": str(target), "kind": "dir", "files": 1, "recursive": True}
    assert BLOCKED_SENTINEL not in repr(result)


@pytest.mark.parametrize("repoint", [False, True], ids=["blocked", "repointed"])
def test_recursive_delete_never_consumes_a_blocked_or_repointed_descendant(repoint_tree, repoint):
    root, blocked = repoint_tree
    target = root / "tree"
    target.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text(BLOCKED_SENTINEL, encoding="utf-8")
    descendant = target / ("nested" if repoint else ".ssh")
    descendant.mkdir()
    descendant_leaf = descendant / "safe.txt"
    descendant_leaf.write_text(BLOCKED_SENTINEL if not repoint else "safe", encoding="utf-8")
    parked = target / "nested-authorized"
    repointed = False

    class RepointAfterPin:
        def name_observed(self, operation, requested_path):
            del operation, requested_path

        def authority_pinned(self, operation, requested_path):
            nonlocal repointed
            if repoint and operation == "delete_path" and requested_path == descendant and not repointed:
                repointed = True
                descendant.rename(parked)
                descendant.symlink_to(blocked, target_is_directory=True)

    with paths.observe_authorization(RepointAfterPin()):
        with pytest.raises(paths.FilesystemError) as error:
            io_ops.delete_path(str(target), recursive=True)

    assert repointed is repoint
    assert BLOCKED_SENTINEL not in str(error.value)
    assert blocked_target.read_text(encoding="utf-8") == BLOCKED_SENTINEL
    if not repoint:
        assert descendant_leaf.read_text(encoding="utf-8") == BLOCKED_SENTINEL


@pytest.mark.parametrize(
    ("operation", "replacement"),
    [
        pytest.param("write", "parent", id="write-parent"),
        pytest.param("rename", "leaf", id="rename-leaf"),
        pytest.param("delete", "leaf", id="delete-leaf"),
    ],
)
def test_missing_mutation_replacement_rows_keep_the_blocked_leaf_untouched(
    repoint_tree,
    operation,
    replacement,
):
    root, blocked = repoint_tree
    safe_parent = root / "safe"
    safe_parent.mkdir()
    blocked_target = blocked / "secret.txt"
    blocked_target.write_text(BLOCKED_SENTINEL, encoding="utf-8")
    swapped = False

    if replacement == "parent":
        safe_target = safe_parent / "item.txt"
        safe_target.write_text("safe", encoding="utf-8")
        alias = root / "alias"
        alias.symlink_to(safe_parent, target_is_directory=True)
        requested = alias / safe_target.name
    else:
        requested = root / "item.txt"
        requested.write_text("safe", encoding="utf-8")
        safe_target = requested

    class ReplaceAfterPin:
        def name_observed(self, observed_operation, requested_path):
            del observed_operation, requested_path

        def authority_pinned(self, observed_operation, requested_path):
            nonlocal swapped
            expected_operation = f"{operation}_file" if operation == "write" else f"{operation}_path"
            if observed_operation != expected_operation or requested_path != requested or swapped:
                return
            swapped = True
            if replacement == "parent":
                alias.unlink()
                alias.symlink_to(blocked, target_is_directory=True)
            else:
                requested.unlink()
                requested.symlink_to(blocked_target)

    with paths.observe_authorization(ReplaceAfterPin()):
        if operation in {"rename", "delete"}:
            with pytest.raises(paths.FilesystemError) as changed:
                _run_operation(operation, requested)
            assert (changed.value.status, changed.value.message_key) == (409, "fs.error.changedOnDisk")
            result = None
        else:
            result = _run_operation(operation, requested)

    assert swapped is True
    assert BLOCKED_SENTINEL not in repr(result)
    assert blocked_target.read_text(encoding="utf-8") == BLOCKED_SENTINEL
    if operation == "write":
        assert safe_target.read_text(encoding="utf-8") == "updated"
    elif operation == "rename":
        assert requested.is_symlink()
        assert not (root / "moved.txt").exists()
    else:
        assert requested.is_symlink()


POLICY_ADAPTERS = (
    "read",
    "info",
    "list",
    "count",
    "zip",
    "search",
    "index",
    "diff",
    "create",
    "write",
    "rename",
    "delete",
)
POLICY_EXPECTATIONS = {
    "regular": {
        "read": "allow",
        "info": "allow",
        "list": "400",
        "count": "400",
        "zip": "400",
        "search": "400",
        "index": "400",
        "diff": "allow",
        "create": "409",
        "write": "allow",
        "rename": "allow",
        "delete": "allow",
    },
    "outside": {adapter: "403" for adapter in POLICY_ADAPTERS},
    "blocked": {adapter: "403" for adapter in POLICY_ADAPTERS},
    "missing": {
        "read": "404",
        "info": "404",
        "list": "404",
        "count": "404",
        "zip": "404",
        "search": "404",
        "index": "404",
        "diff": "allow",
        "create": "allow",
        "write": "allow",
        "rename": "404",
        "delete": "404",
    },
    "symlink": {**{adapter: "allow" for adapter in POLICY_ADAPTERS}, "create": "409"},
    "hardlink": {
        "read": "allow",
        "info": "allow",
        "list": "allow",
        "count": "allow",
        "zip": "allow",
        "search": "allow",
        "index": "allow",
        "diff": "allow",
        "create": "409",
        "write": "allow",
        "rename": "allow",
        "delete": "allow",
    },
}
EXISTING_POLICY_COVERAGE = {
    ("outside", "read"): (
        "tests/test_filesystem.py::test_filesystem_entrypoints_reject_outside_root_through_paths_validator"
    ),
    ("outside", "search"): (
        "tests/test_filesystem.py::test_filesystem_entrypoints_reject_outside_root_through_paths_validator"
    ),
    ("outside", "diff"): (
        "tests/test_filesystem.py::test_filesystem_entrypoints_reject_outside_root_through_paths_validator"
    ),
    ("outside", "write"): (
        "tests/test_filesystem.py::test_filesystem_entrypoints_reject_outside_root_through_paths_validator"
    ),
    ("blocked", "read"): "tests/test_filesystem.py::test_filesystem_blocks_home_secret_paths",
    ("blocked", "delete"): (
        "tests/test_filesystem.py::test_delete_path_refuses_a_blocked_target_before_any_destructive_syscall"
    ),
    ("missing", "read"): "tests/test_filesystem.py::test_read_file_missing",
    ("missing", "list"): "tests/test_filesystem.py::test_list_directory_missing",
    ("missing", "delete"): (
        "tests/test_filesystem.py::test_non_recursive_delete_has_one_typed_result_per_entry_class[missing]"
    ),
    ("symlink", "read"): (
        "tests/test_filesystem.py::test_file_identity_payloads_follow_symlinks_and_hardlinks"
    ),
    ("symlink", "list"): (
        "tests/test_filesystem.py::test_file_identity_payloads_follow_symlinks_and_hardlinks"
    ),
    ("symlink", "write"): "tests/test_filesystem.py::test_write_file_consumes_the_authorized_target_handle",
    ("symlink", "delete"): (
        "tests/test_filesystem.py::test_non_recursive_delete_has_one_typed_result_per_entry_class[symlink]"
    ),
    ("hardlink", "info"): (
        "tests/test_filesystem.py::test_file_identity_payloads_follow_symlinks_and_hardlinks"
    ),
}
POLICY_MATRIX = tuple(
    (
        policy_case,
        adapter,
        expected,
        (
            EXISTING_POLICY_COVERAGE.get((policy_case, adapter), "this-module")
        ),
    )
    for policy_case, expectations in POLICY_EXPECTATIONS.items()
    for adapter, expected in expectations.items()
)
NEW_POLICY_ROWS = [
    pytest.param(policy_case, adapter, expected, coverage, id=f"{policy_case}-{adapter}")
    for policy_case, adapter, expected, coverage in POLICY_MATRIX
]


def test_descriptor_policy_matrix_has_one_explicit_owner_for_every_finite_cell():
    expected_cells = {
        (policy_case, adapter)
        for policy_case in POLICY_EXPECTATIONS
        for adapter in POLICY_ADAPTERS
    }
    actual_cells = {(policy_case, adapter) for policy_case, adapter, _expected, _coverage in POLICY_MATRIX}

    assert actual_cells == expected_cells
    assert len(POLICY_MATRIX) == len(POLICY_EXPECTATIONS) * len(POLICY_ADAPTERS)
    for policy_case, adapter, expected, coverage in POLICY_MATRIX:
        assert coverage
        if coverage != "this-module":
            assert coverage.startswith("tests/test_filesystem.py::"), (policy_case, adapter, coverage)


DIRECTORY_POLICY_ADAPTERS = {"list", "count", "zip", "search", "index"}


def _prepare_policy_target(root: Path, blocked: Path, outside: Path, policy_case: str, adapter: str):
    is_directory = adapter in DIRECTORY_POLICY_ADAPTERS
    sentinel_path = blocked / "sentinel.txt"
    sentinel_path.write_text(BLOCKED_SENTINEL, encoding="utf-8")
    source = None

    if adapter == "diff":
        init_repo(root)
    if policy_case == "outside":
        outside.mkdir()
        if adapter == "create":
            return outside / "created", sentinel_path, source
        target = outside / ("directory" if is_directory else "outside.txt")
        if is_directory:
            target.mkdir()
            (target / "secret.txt").write_text(BLOCKED_SENTINEL, encoding="utf-8")
        else:
            target.write_text(BLOCKED_SENTINEL, encoding="utf-8")
        return target, sentinel_path, source
    if policy_case == "blocked":
        if adapter == "create":
            return blocked / "created", sentinel_path, source
        return (blocked if is_directory else sentinel_path), sentinel_path, source
    if policy_case == "missing":
        target = root / ("missing-dir" if is_directory or adapter == "create" else "missing.txt")
        if adapter == "diff":
            target.write_text("safe\n", encoding="utf-8")
            git(root, "add", target.name)
            git(root, "commit", "-m", "tracked deletion")
            target.unlink()
        return target, sentinel_path, source

    if policy_case == "regular":
        target = root / "regular.txt"
        target.write_text("safe\n", encoding="utf-8")
        return target, sentinel_path, source

    if policy_case == "hardlink" and is_directory:
        target = root / "hardlink-directory"
        target.mkdir()
        source = root / "hardlink-source.txt"
        source.write_text("safe\n", encoding="utf-8")
        os.link(source, target / "hardlink.txt")
        return target, sentinel_path, source

    if is_directory:
        source = root / "safe-directory"
        source.mkdir()
        (source / "safe.txt").write_text("safe", encoding="utf-8")
        target = root / "directory-link"
        target.symlink_to(source, target_is_directory=True)
        return target, sentinel_path, source

    source = root / "source.txt"
    source.write_text("safe\n", encoding="utf-8")
    target = root / f"{policy_case}.txt"
    if policy_case == "symlink":
        target.symlink_to(source)
    else:
        os.link(source, target)
    if adapter == "diff":
        git(root, "add", source.name, target.name)
        git(root, "commit", "-m", "tracked aliases")
    return target, sentinel_path, source


def _run_policy_adapter(adapter: str, target: Path):
    if adapter == "read":
        return filesystem.read_file(str(target))
    if adapter == "info":
        return filesystem.path_info(str(target))
    if adapter == "list":
        return filesystem.list_directory(str(target), include_repo_info=False)
    if adapter == "count":
        return filesystem.count_directory_files(str(target))
    if adapter == "zip":
        archive, _size = filesystem.zip_directory(str(target))
        try:
            with zipfile.ZipFile(archive) as opened:
                return b"".join(opened.read(name) for name in opened.namelist() if not name.endswith("/"))
        finally:
            archive.close()
    if adapter == "search":
        return filesystem.search_files(str(target), "safe", recursive=True)
    if adapter == "index":
        return filesystem.index_status(str(target))
    if adapter == "diff":
        return filesystem.diff_file(str(target))
    if adapter == "create":
        return filesystem.create_directory(str(target))
    if adapter == "write":
        return filesystem.write_file(str(target), "updated")
    if adapter == "rename":
        return filesystem.rename_path(str(target), "moved.txt")
    if adapter == "delete":
        return filesystem.delete_path(str(target))
    raise AssertionError(f"unknown policy adapter: {adapter}")


@pytest.mark.parametrize(("policy_case", "adapter", "expected", "coverage"), NEW_POLICY_ROWS)
def test_new_descriptor_policy_matrix_cells(
    repoint_tree,
    monkeypatch,
    tmp_path,
    policy_case,
    adapter,
    expected,
    coverage,
):
    root, blocked = repoint_tree
    target, sentinel_path, source = _prepare_policy_target(
        root,
        blocked,
        tmp_path / "outside",
        policy_case,
        adapter,
    )
    monkeypatch.setattr(filesystem, "_reindex_after_mutation", lambda *_args, **_kwargs: [])
    before_side_effects = _policy_side_effects(root, target, source, sentinel_path)
    expected_result = (
        _expected_error(policy_case, expected)
        if expected.isdigit()
        else _expected_policy_result(policy_case, adapter, target)
    )
    expected_side_effects = (
        before_side_effects
        if expected.isdigit()
        else _expected_policy_side_effects(adapter, before_side_effects, target)
    )

    if expected.isdigit():
        with pytest.raises(paths.FilesystemError) as error:
            _run_policy_adapter(adapter, target)
        assert error.value.status == int(expected)
        assert error.value.message_key == expected_result["message_key"]
        rendered = str(error.value)
        actual_result = {
            "status": error.value.status,
            "message_key": error.value.message_key,
        }
    else:
        result = _run_policy_adapter(adapter, target)
        rendered = repr(result)
        _assert_policy_result(policy_case, adapter, target, result)
        actual_result = _policy_result_observation(adapter, result)

    assert BLOCKED_SENTINEL not in rendered
    if expected == "allow":
        assert str(blocked) not in rendered
    assert sentinel_path.read_text(encoding="utf-8") == BLOCKED_SENTINEL
    if expected == "allow" and source is not None:
        if adapter == "write":
            assert source.read_text(encoding="utf-8") == "updated"
        elif adapter == "rename":
            assert source.exists()
            assert (root / "moved.txt").exists() or (root / "moved.txt").is_symlink()
        elif adapter == "delete":
            assert source.exists()
    actual_side_effects = _policy_side_effects(root, target, source, sentinel_path)
    _assert_and_record_property_matrix_row(
        f"policy:{policy_case}",
        adapter,
        coverage,
        expected_result,
        actual_result,
        expected_side_effects,
        actual_side_effects,
    )


@pytest.mark.parametrize(("adapter", "operation"), [
    pytest.param("read", "read_file", id="read"),
    pytest.param("info", "path_info", id="info"),
    pytest.param("list", "list_directory", id="list"),
    pytest.param("count", "count_directory_files", id="count"),
    pytest.param("zip", "zip_directory", id="zip"),
    pytest.param("search", "search_files", id="search"),
    pytest.param("index", "index_status", id="index"),
    pytest.param("diff", "diff_file", id="diff"),
    pytest.param("create", "create_directory", id="create"),
    pytest.param("write", "write_file", id="write"),
    pytest.param("rename", "rename_path", id="rename"),
    pytest.param("delete", "delete_path", id="delete"),
])
def test_parent_replacement_keeps_every_adapter_on_the_authorized_generation(
    repoint_tree,
    monkeypatch,
    adapter,
    operation,
):
    root, blocked = repoint_tree
    safe_parent = root / "safe"
    safe_parent.mkdir()
    safe_file = safe_parent / "item.txt"
    safe_file.write_text("base\n", encoding="utf-8")
    blocked_file = blocked / "item.txt"
    blocked_file.write_text(BLOCKED_SENTINEL, encoding="utf-8")
    if adapter == "diff":
        init_repo(root)
        git(root, "add", "safe/item.txt")
        git(root, "commit", "-m", "baseline")
        safe_file.write_text("safe working\n", encoding="utf-8")
    alias = root / "alias"
    alias.symlink_to(safe_parent, target_is_directory=True)
    requested = alias if adapter in DIRECTORY_POLICY_ADAPTERS else alias / safe_file.name
    if adapter == "create":
        requested = alias / "created"
    original_stat = safe_file.stat()
    repointed = False
    monkeypatch.setattr(filesystem, "_reindex_after_mutation", lambda *_args, **_kwargs: [])

    class ReplaceParentAfterPin:
        def name_observed(self, observed_operation, requested_path):
            del observed_operation, requested_path

        def authority_pinned(self, observed_operation, requested_path):
            nonlocal repointed
            if observed_operation != operation or requested_path != requested or repointed:
                return
            repointed = True
            alias.unlink()
            alias.symlink_to(blocked, target_is_directory=True)

    with paths.observe_authorization(ReplaceParentAfterPin()):
        result = _run_policy_adapter(adapter, requested)

    if adapter == "read":
        expected_result = {"content_hex": b"base\n".hex(), "size": 5}
        actual_result = {"content_hex": result["content"].encode("utf-8").hex(), "size": result["size"]}
    elif adapter == "info":
        expected_result = {
            "kind": "file",
            "size": 5,
            "file_id": f"{original_stat.st_dev}:{original_stat.st_ino}",
        }
        actual_result = {key: result[key] for key in expected_result}
    elif adapter == "list":
        expected_result = {"entries": [{"name": "item.txt", "kind": "file", "size": 5}]}
        actual_result = _policy_result_observation(adapter, result)
    elif adapter == "count":
        expected_result = {"kind": "dir", "files": 1, "recursive": True}
        actual_result = _policy_result_observation(adapter, result)
    elif adapter == "zip":
        expected_result = {"bytes_hex": b"base\n".hex()}
        actual_result = _policy_result_observation(adapter, result)
    elif adapter == "search":
        expected_result = {"files": []}
        actual_result = _policy_result_observation(adapter, result)
    elif adapter == "index":
        expected_result = {"root": str(safe_parent), "state_present": True}
        actual_result = _policy_result_observation(adapter, result)
    elif adapter == "diff":
        expected_result = {
            "working_hex": b"safe working\n".hex(),
            "working_missing": False,
            "blocked_sentinel_bytes": 0,
        }
        actual_result = _policy_result_observation(adapter, result)
    elif adapter == "create":
        expected_result = {"path": str(requested), "created": True, "kind": "dir"}
        actual_result = {key: result.get(key) for key in expected_result}
    elif adapter == "write":
        expected_result = {"path": str(requested), "size": len(b"updated"), "target_bytes_hex": b"updated".hex()}
        actual_result = {
            "path": result.get("path"),
            "size": result.get("size"),
            "target_bytes_hex": safe_file.read_bytes().hex(),
        }
    elif adapter == "rename":
        expected_result = {
            "path": str(requested.with_name("moved.txt")),
            "old_path": str(requested),
            "name": "moved.txt",
        }
        actual_result = {key: result.get(key) for key in expected_result}
    else:
        expected_result = {"path": str(requested), "deleted": True, "kind": "file"}
        actual_result = {key: result.get(key) for key in expected_result}

    assert repointed is True
    assert BLOCKED_SENTINEL not in repr(result)
    assert str(blocked) not in repr(result)
    assert alias.resolve() == blocked
    assert blocked_file.read_text(encoding="utf-8") == BLOCKED_SENTINEL
    actual_side_effects = {
        "alias": _node_state(alias),
        "safe_parent": _node_state(safe_parent),
        "safe_file": _node_state(safe_parent / "item.txt"),
        "safe_moved": _node_state(safe_parent / "moved.txt"),
        "safe_created": _node_state(safe_parent / "created"),
        "blocked_file": _node_state(blocked_file),
    }
    expected_safe_file = {"kind": "file", "bytes_hex": b"base\n".hex(), "size": 5}
    expected_entries = ["item.txt"]
    expected_moved = {"kind": "missing"}
    expected_created = {"kind": "missing"}
    if adapter == "diff":
        expected_safe_file = {"kind": "file", "bytes_hex": b"safe working\n".hex(), "size": 13}
    elif adapter == "write":
        expected_safe_file = {"kind": "file", "bytes_hex": b"updated".hex(), "size": 7}
    elif adapter == "rename":
        expected_safe_file = {"kind": "missing"}
        expected_entries = ["moved.txt"]
        expected_moved = {"kind": "file", "bytes_hex": b"base\n".hex(), "size": 5}
    elif adapter == "delete":
        expected_safe_file = {"kind": "missing"}
        expected_entries = []
    elif adapter == "create":
        expected_entries = ["created", "item.txt"]
        expected_created = {"kind": "dir", "entries": []}
    expected_side_effects = {
        "alias": {"kind": "symlink", "target": str(blocked)},
        "safe_parent": {"kind": "dir", "entries": expected_entries},
        "safe_file": expected_safe_file,
        "safe_moved": expected_moved,
        "safe_created": expected_created,
        "blocked_file": {
            "kind": "file",
            "bytes_hex": BLOCKED_SENTINEL.encode("utf-8").hex(),
            "size": len(BLOCKED_SENTINEL),
        },
    }
    _assert_and_record_property_matrix_row(
        "parent-replacement",
        adapter,
        "this-module",
        expected_result,
        actual_result,
        expected_side_effects,
        actual_side_effects,
    )


@pytest.mark.parametrize(("adapter", "operation"), [
    pytest.param("read", "read_file", id="read"),
    pytest.param("info", "path_info", id="info"),
    pytest.param("list", "list_directory", id="list"),
    pytest.param("count", "count_directory_files", id="count"),
    pytest.param("zip", "zip_directory", id="zip"),
    pytest.param("search", "search_files", id="search"),
    pytest.param("index", "index_status", id="index"),
    pytest.param("diff", "diff_file", id="diff"),
    pytest.param("create", "create_directory", id="create"),
    pytest.param("write", "write_file", id="write"),
    pytest.param("rename", "rename_path", id="rename"),
    pytest.param("delete", "delete_path", id="delete"),
])
def test_concurrent_leaf_replacement_has_one_exact_result_per_adapter(
    repoint_tree,
    monkeypatch,
    adapter,
    operation,
):
    root, blocked = repoint_tree
    is_directory = adapter in DIRECTORY_POLICY_ADAPTERS
    blocked_file = blocked / "item.txt"
    blocked_file.write_text(BLOCKED_SENTINEL, encoding="utf-8")
    blocked_directory = blocked / "directory"
    blocked_directory.mkdir()
    (blocked_directory / "secret.txt").write_text(BLOCKED_SENTINEL, encoding="utf-8")
    target = root / ("directory" if is_directory else "item.txt")
    parked = root / ("directory-authorized" if is_directory else "item-authorized.txt")
    if adapter != "create":
        if is_directory:
            target.mkdir()
            (target / "safe.txt").write_text("safe", encoding="utf-8")
        else:
            target.write_text("base\n", encoding="utf-8")
    if adapter == "diff":
        init_repo(root)
        git(root, "add", target.name)
        git(root, "commit", "-m", "baseline")
        target.write_text("safe working\n", encoding="utf-8")
    monkeypatch.setattr(filesystem, "_reindex_after_mutation", lambda *_args, **_kwargs: [])
    repointed = False

    class ReplaceLeafAfterPin:
        def name_observed(self, observed_operation, requested_path):
            del observed_operation, requested_path

        def authority_pinned(self, observed_operation, requested_path):
            nonlocal repointed
            if observed_operation != operation or requested_path != target or repointed:
                return
            repointed = True
            if adapter != "create":
                target.rename(parked)
            target.symlink_to(blocked_directory if is_directory or adapter == "create" else blocked_file)

    result = None
    error = None
    with paths.observe_authorization(ReplaceLeafAfterPin()):
        try:
            result = _run_policy_adapter(adapter, target)
        except paths.FilesystemError as caught:
            error = caught

    assert repointed is True
    rendered = str(error) if error is not None else repr(result)
    assert BLOCKED_SENTINEL not in rendered
    assert str(blocked) not in rendered
    if error is not None:
        actual_result = {"status": error.status, "message_key": error.message_key}
    elif adapter in {"write", "rename", "delete"}:
        actual_result = {key: result.get(key) for key in ("path", "old_path", "name", "size", "deleted", "kind") if key in result}
    else:
        actual_result = _policy_result_observation(adapter, result)

    if adapter == "read":
        expected_result = {"content_hex": b"base\n".hex(), "size": 5}
    elif adapter == "info":
        parked_stat = parked.stat()
        expected_result = {"kind": "file", "size": 5, "file_id": f"{parked_stat.st_dev}:{parked_stat.st_ino}"}
    elif adapter == "list":
        expected_result = {"entries": [{"name": "safe.txt", "kind": "file", "size": 4}]}
    elif adapter == "count":
        expected_result = {"kind": "dir", "files": 1, "recursive": True}
    elif adapter == "zip":
        expected_result = {"bytes_hex": b"safe".hex()}
    elif adapter == "search":
        expected_result = {"files": []}
    elif adapter == "index":
        expected_result = {"root": str(target), "state_present": True}
    elif adapter == "diff":
        expected_result = {"status": 409, "message_key": "fs.error.gitRepositoryChanged"}
    elif adapter == "create":
        expected_result = {"status": 409, "message_key": "fs.error.targetExists"}
    elif adapter == "write":
        expected_result = {"path": str(target), "size": 7}
    elif adapter == "rename":
        expected_result = {"status": 409, "message_key": "fs.error.changedOnDisk"}
    else:
        expected_result = {"status": 409, "message_key": "fs.error.changedOnDisk"}

    actual_side_effects = {
        "target": _node_state(target),
        "parked": _node_state(parked),
        "moved": _node_state(root / "moved.txt"),
        "blocked_file": _node_state(blocked_file),
        "blocked_directory": _node_state(blocked_directory),
    }
    original_parked = (
        {"kind": "dir", "entries": ["safe.txt"]}
        if is_directory
        else {"kind": "file", "bytes_hex": (b"safe working\n" if adapter == "diff" else b"base\n").hex(), "size": 13 if adapter == "diff" else 5}
    )
    expected_side_effects = {
        "target": {"kind": "symlink", "target": str(blocked_directory if is_directory or adapter == "create" else blocked_file)},
        "parked": original_parked if adapter != "create" else {"kind": "missing"},
        "moved": {"kind": "missing"},
        "blocked_file": {"kind": "file", "bytes_hex": BLOCKED_SENTINEL.encode("utf-8").hex(), "size": len(BLOCKED_SENTINEL)},
        "blocked_directory": {"kind": "dir", "entries": ["secret.txt"]},
    }
    if adapter == "write":
        expected_side_effects["parked"] = {"kind": "file", "bytes_hex": b"updated".hex(), "size": 7}
    _assert_and_record_property_matrix_row(
        "concurrent-namespace",
        adapter,
        "this-module",
        expected_result,
        actual_result,
        expected_side_effects,
        actual_side_effects,
    )


CAPABILITY_ROWS = [
    *(pytest.param("nofollow", adapter, "500", id=f"nofollow-{adapter}") for adapter in (
        "read", "count", "create", "diff", "blame", "rename"
    )),
    *(pytest.param("descriptor-roots", adapter, "500", id=f"descriptor-roots-{adapter}") for adapter in (
        "read", "count", "diff", "blame", "rename"
    )),
    pytest.param("descriptor-roots", "create", "allow", id="descriptor-roots-create-descriptor-native"),
    pytest.param("nofollow", "info", "500", id="nofollow-info"),
    pytest.param("nofollow", "list", "500", id="nofollow-list"),
    pytest.param("nofollow", "zip", "500", id="nofollow-zip"),
    pytest.param("nofollow", "search", "500", id="nofollow-search"),
    pytest.param("nofollow", "index", "500", id="nofollow-index"),
    pytest.param("nofollow", "write", "500", id="nofollow-write"),
    pytest.param("nofollow", "delete", "500", id="nofollow-delete"),
    pytest.param("descriptor-roots", "info", "500", id="descriptor-roots-info"),
    pytest.param("descriptor-roots", "list", "allow", id="descriptor-roots-list-descriptor-native"),
    pytest.param("descriptor-roots", "zip", "500", id="descriptor-roots-zip"),
    pytest.param("descriptor-roots", "search", "500", id="descriptor-roots-search"),
    pytest.param("descriptor-roots", "index", "allow", id="descriptor-roots-index-descriptor-native"),
    pytest.param("descriptor-roots", "write", "allow", id="descriptor-roots-write-descriptor-native"),
    pytest.param("descriptor-roots", "delete", "allow", id="descriptor-roots-delete-descriptor-native"),
]


def _run_capability_consumer(adapter: str, file_target: Path, directory_target: Path):
    if adapter == "read":
        return filesystem.read_file(str(file_target))
    if adapter == "info":
        return filesystem.path_info(str(file_target))
    if adapter == "list":
        return filesystem.list_directory(str(directory_target), include_repo_info=False)
    if adapter == "count":
        return filesystem.count_directory_files(str(directory_target))
    if adapter == "zip":
        archive, size = filesystem.zip_directory(str(directory_target))
        archive.close()
        return {"size": size}
    if adapter == "search":
        return filesystem.search_files(str(directory_target), "safe", recursive=True)
    if adapter == "index":
        return filesystem.index_status(str(directory_target))
    if adapter == "create":
        return filesystem.create_directory(str(directory_target / "created"))
    if adapter == "diff":
        return filesystem.diff_file(str(file_target))
    if adapter == "blame":
        return filesystem.blame_file(str(file_target))
    if adapter == "rename":
        return filesystem.rename_path(str(file_target), "moved.txt")
    if adapter == "write":
        return filesystem.write_file(str(file_target), "updated\n")
    if adapter == "delete":
        return filesystem.delete_path(str(file_target))
    raise AssertionError(f"unknown capability consumer: {adapter}")


@pytest.mark.parametrize(("capability", "adapter", "expected"), CAPABILITY_ROWS)
def test_public_consumers_fail_closed_without_required_descriptor_capabilities(
    repoint_tree,
    monkeypatch,
    capability,
    adapter,
    expected,
):
    root, blocked = repoint_tree
    repo = root / "repo"
    directory_target = repo / "directory"
    directory_target.mkdir(parents=True)
    (directory_target / "safe.txt").write_text("safe", encoding="utf-8")
    file_target = repo / "tracked.txt"
    file_target.write_text("safe\n", encoding="utf-8")
    init_repo(repo)
    git(repo, "add", "directory/safe.txt", file_target.name)
    git(repo, "commit", "-m", "tracked baseline")
    monkeypatch.setattr(filesystem, "_reindex_after_mutation", lambda *_args, **_kwargs: [])
    if capability == "nofollow":
        monkeypatch.setattr(paths.os, "O_NOFOLLOW", 0)
    else:
        monkeypatch.setattr(paths, "DESCRIPTOR_PATH_ROOTS", ())
    before_side_effects = {
        "file": _node_state(file_target),
        "directory": _node_state(directory_target),
    }
    expected_result = (
        {"status": 500, "message_key": "fs.error.operationFailed"}
        if expected == "500"
        else {"outcome": "descriptor-native allow", "adapter": adapter}
    )

    if expected == "500":
        with pytest.raises(paths.FilesystemError) as error:
            _run_capability_consumer(adapter, file_target, directory_target)
        assert (error.value.status, error.value.message_key) == (500, "fs.error.operationFailed")
        rendered = str(error.value)
        actual_result = {
            "status": error.value.status,
            "message_key": error.value.message_key,
        }
    else:
        result = _run_capability_consumer(adapter, file_target, directory_target)
        rendered = repr(result)
        actual_result = {"outcome": "descriptor-native allow", "adapter": adapter}
        if adapter == "create":
            assert (directory_target / "created").is_dir()
        elif adapter == "write":
            assert file_target.read_text(encoding="utf-8") == "updated\n"
        elif adapter == "delete":
            assert not file_target.exists()

    assert BLOCKED_SENTINEL not in rendered
    assert str(blocked) not in rendered
    if adapter not in {"write", "delete"} or expected == "500":
        assert file_target.read_text(encoding="utf-8") == "safe\n"
    actual_side_effects = {
        "file": _node_state(file_target),
        "directory": _node_state(directory_target),
    }
    expected_side_effects = json.loads(json.dumps(before_side_effects))
    if expected == "allow" and adapter == "create":
        expected_side_effects["directory"] = {"kind": "dir", "entries": ["created", "safe.txt"]}
    elif expected == "allow" and adapter == "write":
        expected_side_effects["file"] = {
            "kind": "file",
            "bytes_hex": b"updated\n".hex(),
            "size": len(b"updated\n"),
        }
    elif expected == "allow" and adapter == "delete":
        expected_side_effects["file"] = {"kind": "missing"}
    _assert_and_record_property_matrix_row(
        f"capability:{capability}",
        adapter,
        "this-module",
        expected_result,
        actual_result,
        expected_side_effects,
        actual_side_effects,
    )


@pytest.mark.parametrize("operation", ["read", "write", "path_info"], ids=["read", "write", "index-annotation"])
def test_safe_path_refuses_a_post_authorization_hardlink_replacement(repoint_tree, monkeypatch, operation):
    root, blocked = repoint_tree
    target = root / "allowed.txt"
    target.write_text("safe", encoding="utf-8")
    secret = blocked / "id_rsa"
    secret.write_text(BLOCKED_SENTINEL, encoding="utf-8")
    original_ensure = paths._ensure_path_allowed
    swapped = False

    def authorize_then_swap(path, *, resolved=None):
        nonlocal swapped
        result = original_ensure(path, resolved=resolved)
        if Path(path) == target and not swapped:
            swapped = True
            target.unlink()
            os.link(secret, target)
        return result

    monkeypatch.setattr(paths, "_ensure_path_allowed", authorize_then_swap)
    with pytest.raises(paths.FilesystemError) as changed:
        _run_operation(operation, target)

    assert swapped is True
    assert (changed.value.status, changed.value.message_key) == (409, "fs.error.changedOnDisk")
    assert secret.read_text(encoding="utf-8") == BLOCKED_SENTINEL


def test_safe_parent_refuses_a_post_authorization_directory_replacement(repoint_tree, monkeypatch):
    root, blocked_parent = repoint_tree
    parent = root / "safe-parent"
    parent.mkdir()
    target = parent / "victim.txt"
    target.write_text("safe", encoding="utf-8")
    (blocked_parent / target.name).write_text(BLOCKED_SENTINEL, encoding="utf-8")
    parked = root / "safe-parent-authorized"
    original_ensure = paths._ensure_path_allowed
    swapped = False

    def authorize_then_swap(path, *, resolved=None):
        nonlocal swapped
        result = original_ensure(path, resolved=resolved)
        if Path(path) == target and not swapped:
            swapped = True
            parent.rename(parked)
            blocked_parent.rename(parent)
        return result

    monkeypatch.setattr(paths, "_ensure_path_allowed", authorize_then_swap)
    with pytest.raises(paths.FilesystemError) as changed:
        filesystem.delete_path(str(target))

    assert swapped is True
    assert (changed.value.status, changed.value.message_key) == (409, "fs.error.changedOnDisk")
    assert (parked / target.name).read_text(encoding="utf-8") == "safe"
    assert (parent / target.name).read_text(encoding="utf-8") == BLOCKED_SENTINEL


def test_symlink_listing_fails_closed_without_link_descriptor_support(repoint_tree, monkeypatch):
    root, _blocked = repoint_tree
    first = root / "first.txt"
    first.write_text("first", encoding="utf-8")
    second = root / "SECOND_GENERATION_SENTINEL.txt"
    second.write_text("second generation bytes", encoding="utf-8")
    link = root / "link"
    link.symlink_to(first.name)
    real_open = listing.os.open
    real_readlink = listing.os.readlink
    swapped = False

    def unsupported_link_open(path, flags, *args, **kwargs):
        if path == link.name and kwargs.get("dir_fd") is not None:
            raise OSError(errno.ELOOP, "simulated platform without symlink descriptor support")
        return real_open(path, flags, *args, **kwargs)

    def forbidden_name_reopen(path, *args, **kwargs):
        nonlocal swapped
        if path == link.name and kwargs.get("dir_fd") is not None:
            swapped = True
            link.unlink()
            link.symlink_to(second.name)
        return real_readlink(path, *args, **kwargs)

    monkeypatch.setattr(listing.os, "open", unsupported_link_open)
    monkeypatch.setattr(listing.os, "readlink", forbidden_name_reopen)
    with pytest.raises(paths.FilesystemError) as unsupported:
        filesystem.list_directory(str(root), include_repo_info=False)

    assert unsupported.value.status == 500
    assert unsupported.value.message_key == "fs.error.operationFailed"
    assert swapped is False


def test_descriptor_symlink_target_fails_closed_without_empty_path_support(repoint_tree, monkeypatch):
    root, blocked = repoint_tree
    safe = root / "safe.txt"
    safe.write_text("safe", encoding="utf-8")
    link = root / "link"
    link.symlink_to(safe.name)
    parent_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    link_descriptor = os.open(
        link.name,
        paths.descriptor_open_flags(paths.metadata_descriptor_flags()),
        dir_fd=parent_descriptor,
    )
    def macos_readlink(path, *args, **kwargs):
        if path == "" and kwargs.get("dir_fd") == link_descriptor:
            raise OSError(errno.ENOTSUP, "simulated macOS empty-path readlink rejection")
        raise AssertionError("fallback must not reopen the symlink by name")

    monkeypatch.setattr(paths.os, "readlink", macos_readlink)
    try:
        with pytest.raises(paths.FilesystemError) as changed:
            paths.symlink_target_from_descriptor(link_descriptor, link)
    finally:
        os.close(link_descriptor)
        os.close(parent_descriptor)

    assert (changed.value.status, changed.value.message_key) == (500, "fs.error.operationFailed")


def test_rename_refuses_source_replacement_after_descriptor_pin(repoint_tree):
    root, blocked = repoint_tree
    source = root / "source.txt"
    source.write_text("safe", encoding="utf-8")
    replacement = blocked / "secret"
    replacement.write_text(BLOCKED_SENTINEL, encoding="utf-8")
    parked = root / "source-authorized.txt"

    class SwapAfterSourcePin:
        def __init__(self):
            self.pins = 0

        def name_observed(self, operation, requested_path):
            pass

        def authority_pinned(self, operation, requested_path):
            if operation == "rename_path" and requested_path == source:
                self.pins += 1
                if self.pins == 2:
                    source.rename(parked)
                    replacement.rename(source)

    observer = SwapAfterSourcePin()
    with paths.observe_authorization(observer), pytest.raises(paths.FilesystemError) as changed:
        filesystem.rename_path(str(source), "renamed.txt")

    assert (changed.value.status, changed.value.message_key) == (409, "fs.error.changedOnDisk")
    assert observer.pins == 2
    assert parked.read_text(encoding="utf-8") == "safe"
    assert source.read_text(encoding="utf-8") == BLOCKED_SENTINEL
    assert not (root / "renamed.txt").exists()


def test_rename_never_replaces_a_destination_created_after_the_check(repoint_tree, monkeypatch):
    root, _blocked = repoint_tree
    source = root / "source.txt"
    source.write_text("authorized-source", encoding="utf-8")
    target = root / "moved.txt"
    real_stat = io_ops.os.stat
    created = False

    def create_destination_after_check(path, *args, **kwargs):
        nonlocal created
        if path == target.name and kwargs.get("dir_fd") is not None and kwargs.get("follow_symlinks") is False and not created:
            created = True
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs["dir_fd"])
            try:
                os.write(descriptor, BLOCKED_SENTINEL.encode("utf-8"))
            finally:
                os.close(descriptor)
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(io_ops.os, "stat", create_destination_after_check)
    with pytest.raises(paths.FilesystemError) as exists:
        filesystem.rename_path(str(source), target.name)

    assert (exists.value.status, exists.value.message_key) == (409, "fs.error.targetExists")
    assert created is True
    assert source.read_text(encoding="utf-8") == "authorized-source"
    assert target.read_text(encoding="utf-8") == BLOCKED_SENTINEL


def test_git_index_publication_preserves_a_final_concurrent_replacement(repoint_tree, monkeypatch):
    root, _blocked = repoint_tree
    init_repo(root)
    target = root / "old.txt"
    target.write_text("committed\n", encoding="utf-8")
    git(root, "add", target.name)
    git(root, "commit", "-m", "baseline")
    index = root / ".git" / "index"
    replacement_bytes = BLOCKED_SENTINEL.encode("utf-8")
    original_exchange = paths.rename_exchange
    injected = False

    def inject_before_exchange(parent_descriptor, first, second):
        nonlocal injected
        if first == "index.lock" and second == "index" and not injected:
            injected = True
            replacement = root / ".git" / "replacement-index"
            replacement.write_bytes(replacement_bytes)
            replacement.replace(index)
        return original_exchange(parent_descriptor, first, second)

    monkeypatch.setattr(paths, "rename_exchange", inject_before_exchange)
    with pytest.raises(paths.FilesystemError) as changed:
        filesystem.rename_path(str(target), "new.txt")

    assert (changed.value.status, changed.value.message_key) == (409, "fs.error.gitRepositoryChanged")
    assert injected is True
    assert index.read_bytes() == replacement_bytes
    assert not (root / ".git" / "index.lock").exists()
