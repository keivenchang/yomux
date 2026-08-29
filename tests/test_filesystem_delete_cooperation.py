"""Recursive delete interruption and partial-progress contracts."""

from __future__ import annotations

import os
import threading
import time

import pytest

from yolomux_lib import filesystem
from yolomux_lib.filesystem import FilesystemError


def _tree(tmp_path):
    target = tmp_path / "tree"
    target.mkdir()
    for name in ("first.txt", "second.txt", "third.txt"):
        (target / name).write_text(name, encoding="utf-8")
    return target


def test_recursive_delete_stops_after_a_cooperative_cancellation_and_reports_progress(tmp_path, monkeypatch):
    target = _tree(tmp_path)
    cancelled = threading.Event()
    real_unlink = os.unlink

    def cancel_after_first_unlink(*args, **kwargs):
        result = real_unlink(*args, **kwargs)
        cancelled.set()
        return result

    monkeypatch.setattr(os, "unlink", cancel_after_first_unlink)

    with pytest.raises(FilesystemError) as raised:
        filesystem.delete_path(str(target), recursive=True, cancel_event=cancelled)

    payload = raised.value.payload(path=str(target))
    assert payload["partial"] is True
    assert payload["delete_reason"] == "cancelled"
    assert payload["failed_path"] == str(target / "second.txt")
    assert payload["deleted_paths"] == [str(target / "first.txt")]
    assert (target / "first.txt").exists() is False
    assert (target / "second.txt").exists() is True
    assert (target / "third.txt").exists() is True


def test_recursive_delete_honors_an_expired_deadline_before_any_destructive_syscall(tmp_path):
    target = _tree(tmp_path)

    with pytest.raises(FilesystemError) as raised:
        filesystem.delete_path(str(target), recursive=True, deadline_monotonic=time.monotonic() - 1.0)

    payload = raised.value.payload(path=str(target))
    assert payload["partial"] is False
    assert payload["delete_reason"] == "deadline_exceeded"
    assert payload["failed_path"] == str(target)
    assert payload["deleted_paths"] == []
    assert {path.name for path in target.iterdir()} == {"first.txt", "second.txt", "third.txt"}


def test_recursive_delete_pre_mutation_cancellation_is_explicitly_non_partial(tmp_path):
    target = _tree(tmp_path)
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(FilesystemError) as raised:
        filesystem.delete_path(str(target), recursive=True, cancel_event=cancelled)

    payload = raised.value.payload(path=str(target))
    assert payload["partial"] is False
    assert payload["delete_reason"] == "cancelled"
    assert payload["failed_path"] == str(target)
    assert payload["deleted_paths"] == []
    assert {path.name for path in target.iterdir()} == {"first.txt", "second.txt", "third.txt"}


def test_recursive_delete_partial_os_failure_reindexes_and_names_full_failed_path(tmp_path, monkeypatch):
    target = _tree(tmp_path)
    invalidations = []
    reindex_requests = []
    real_unlink = os.unlink

    def fail_second_unlink(name, *args, **kwargs):
        if name == "second.txt":
            raise PermissionError("injected second-entry refusal")
        return real_unlink(name, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_second_unlink)
    monkeypatch.setattr(filesystem.paths, "invalidate_path_policy_caches", lambda: invalidations.append(True))
    monkeypatch.setattr(
        filesystem,
        "_reindex_after_mutation",
        lambda paths, reason="": reindex_requests.append((paths, reason)) or [],
    )

    with pytest.raises(FilesystemError) as raised:
        filesystem.delete_path(str(target), recursive=True)

    payload = raised.value.payload(path=str(target))
    assert payload["partial"] is True
    assert payload["delete_reason"] == "entry_failed"
    assert payload["failed_path"] == str(target / "second.txt")
    assert payload["deleted_paths"] == [str(target / "first.txt")]
    assert invalidations == [True]
    assert reindex_requests == [([str(target), str(target / "first.txt")], "fs-delete-partial")]
    assert (target / "first.txt").exists() is False
    assert (target / "second.txt").exists() is True
    assert (target / "third.txt").exists() is True


class _ControlledMonotonic:
    """Replace only `io_ops`'s view of `time`, so a deadline crossing is exact and not timed.

    Patching the real `time.monotonic` would reach pytest and every other module in this process.
    `io_ops` reads its clock through its own module attribute, so replacing that attribute controls
    the delete walk and nothing else. Everything but `monotonic` proxies to the real module.
    """

    def __init__(self, start: float) -> None:
        self.now = float(start)

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def test_an_expired_deadline_mid_walk_reports_a_partial_delete_with_its_exact_progress(
    tmp_path, monkeypatch,
):
    """The production stop path, which had no post-mutation regression at all.

    batchd hands the worker an absolute monotonic deadline and NO cancel event, so the deadline is the
    only mechanism that can stop a live recursive delete part-way. The pre-mutation deadline case and
    both cancel-event cases were covered; this one -- deadline expiring after real destructive work --
    was not, even though it is the only mid-walk stop a broker can actually cause.
    """
    target = _tree(tmp_path)
    clock = _ControlledMonotonic(1_000.0)
    deadline = 1_000.5
    monkeypatch.setattr(filesystem.io_ops, "time", clock)
    real_unlink = os.unlink

    def unlink_then_cross_the_deadline(*args, **kwargs):
        result = real_unlink(*args, **kwargs)
        clock.now = deadline
        return result

    monkeypatch.setattr(os, "unlink", unlink_then_cross_the_deadline)

    with pytest.raises(FilesystemError) as raised:
        filesystem.delete_path(str(target), recursive=True, deadline_monotonic=deadline)

    payload = raised.value.payload(path=str(target))
    assert payload["partial"] is True, "real destructive work happened; this is not a clean refusal"
    assert payload["delete_reason"] == "deadline_exceeded"
    assert payload["failed_path"] == str(target / "second.txt")
    assert payload["deleted_paths"] == [str(target / "first.txt")]
    assert (target / "first.txt").exists() is False
    assert (target / "second.txt").exists() is True
    assert (target / "third.txt").exists() is True


def test_a_mid_walk_deadline_invalidates_and_reindexes_only_what_it_actually_deleted(
    tmp_path, monkeypatch,
):
    """Partial-failure fidelity for the deadline reason, not only for an entry OSError.

    A partially deleted subtree must not leave the search index advertising files that are gone, and
    must not publish a completed-subtree invalidation either. The `entry_failed` reason was covered;
    `deadline_exceeded` reaches the same facade branch and is now pinned too.
    """
    target = _tree(tmp_path)
    invalidations = []
    reindex_requests = []
    clock = _ControlledMonotonic(2_000.0)
    deadline = 2_000.5
    monkeypatch.setattr(filesystem.io_ops, "time", clock)
    real_unlink = os.unlink

    def unlink_then_cross_the_deadline(*args, **kwargs):
        result = real_unlink(*args, **kwargs)
        clock.now = deadline
        return result

    monkeypatch.setattr(os, "unlink", unlink_then_cross_the_deadline)
    monkeypatch.setattr(filesystem.paths, "invalidate_path_policy_caches", lambda: invalidations.append(True))
    monkeypatch.setattr(
        filesystem,
        "_reindex_after_mutation",
        lambda paths, reason="": reindex_requests.append((paths, reason)) or [],
    )

    with pytest.raises(FilesystemError):
        filesystem.delete_path(str(target), recursive=True, deadline_monotonic=deadline)

    assert invalidations == [True]
    assert reindex_requests == [([str(target), str(target / "first.txt")], "fs-delete-partial")]


def test_a_pre_mutation_deadline_invalidates_and_reindexes_nothing(tmp_path, monkeypatch):
    """Negative control for the row above: a refusal that deleted nothing must publish nothing."""
    target = _tree(tmp_path)
    invalidations = []
    reindex_requests = []
    monkeypatch.setattr(filesystem.paths, "invalidate_path_policy_caches", lambda: invalidations.append(True))
    monkeypatch.setattr(
        filesystem,
        "_reindex_after_mutation",
        lambda paths, reason="": reindex_requests.append((paths, reason)) or [],
    )

    with pytest.raises(FilesystemError):
        filesystem.delete_path(str(target), recursive=True, deadline_monotonic=time.monotonic() - 1.0)

    assert invalidations == []
    assert reindex_requests == []
    assert {path.name for path in target.iterdir()} == {"first.txt", "second.txt", "third.txt"}


def test_a_recursive_delete_unlinks_symlinks_without_following_them_out_of_the_tree(tmp_path):
    """A recursive delete that followed a link would destroy authorized data outside its target.

    The behaviour is already correct; it had no regression, which for a destructive walk is the
    coverage that matters most. Both link shapes are covered: a link to a directory and a link to a
    file, each pointing outside the subtree being removed.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("must survive", encoding="utf-8")
    target = tmp_path / "tree"
    target.mkdir()
    (target / "a.txt").write_text("payload", encoding="utf-8")
    (target / "link-to-dir").symlink_to(outside)
    (target / "link-to-file").symlink_to(outside / "precious.txt")

    result = filesystem.delete_path(str(target), recursive=True)

    assert result["deleted"] is True and result["kind"] == "dir"
    assert not target.exists()
    assert outside.is_dir(), "the recursive walk followed a directory symlink out of its target"
    assert (outside / "precious.txt").read_text(encoding="utf-8") == "must survive"
