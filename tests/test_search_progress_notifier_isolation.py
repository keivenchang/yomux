"""File-index test-scope callback and timer isolation."""

from pathlib import Path
import threading
from yolomux_lib import file_index


def test_reset_clears_leaked_search_progress_notifier_and_coalescing():
    calls: list[dict] = []
    file_index.set_search_progress_notifier(lambda frame: calls.append(frame))

    file_index.notify_search_progress(Path("/leaked/root"), 1, 1, None)
    file_index.notify_search_progress(Path("/leaked/root"), 1, 2, None)
    assert len(calls) == 1
    assert file_index._SEARCH_PROGRESS_PENDING and file_index._SEARCH_PROGRESS_TIMERS
    file_index.FileIndexTestScope().cleanup()
    assert file_index._SEARCH_PROGRESS_NOTIFIER is None
    assert file_index._SEARCH_PROGRESS_TIMERS == {} and file_index._SEARCH_PROGRESS_PENDING == {}


def test_leaked_late_notify_cannot_write_or_chmod_a_foreign_host_state_dir(tmp_path):
    foreign_dir = tmp_path / "hosts" / "deadbeef" / "background-owner"
    foreign_dir.mkdir(parents=True)
    foreign_target = foreign_dir / "client-events.json"
    touched: list[dict] = []
    def leaked_notifier(frame):
        foreign_dir.chmod(0o700)
        foreign_target.write_text("leaked")
        touched.append(frame)

    file_index.set_search_progress_notifier(leaked_notifier)
    foreign_dir.chmod(0o000)
    file_index.FileIndexTestScope().cleanup()
    file_index.notify_search_progress(Path("/leaked/root"), 2, 3, None)
    foreign_dir.chmod(0o700)
    assert touched == [] and not foreign_target.exists()


def test_test_scope_waits_for_inflight_publication_then_rejects_late_delivery():
    entered, release = threading.Event(), threading.Event()
    delivered = []

    def blocking_notifier(frame):
        entered.set()
        assert release.wait(timeout=5.0) is True
        delivered.append(frame["revision"])

    file_index.set_search_progress_notifier(blocking_notifier)
    publisher = threading.Thread(target=file_index.notify_search_progress, args=(Path("/owned/root"), 1, 1, None), daemon=True)
    publisher.start()
    assert entered.wait(timeout=5.0) is True
    cleaned = threading.Event()

    def cleanup():
        file_index.FileIndexTestScope().cleanup()
        cleaned.set()

    teardown = threading.Thread(target=cleanup, daemon=True); teardown.start()
    assert teardown.is_alive() is True and cleaned.is_set() is False
    release.set()
    teardown.join(timeout=5.0); publisher.join(timeout=5.0)
    assert cleaned.is_set() is True and delivered == [1]
    file_index.notify_search_progress(Path("/owned/root"), 1, 2, None)
    assert delivered == [1]


def test_test_scope_cleanup_is_idempotent_and_body_failure_still_cleans():
    scope = file_index.FileIndexTestScope()
    scope.cleanup(); scope.cleanup()
    try:
        with scope:
            file_index.set_background_owner_checker(lambda _role: False)
            raise RuntimeError("fixture failure")
    except RuntimeError as exc:
        assert str(exc) == "fixture failure"
    else:
        raise AssertionError("fixture failure was not propagated")
    assert file_index.background_owner_can_build() is True


def test_test_scope_rejects_late_gate_and_clears_callbacks_in_order(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    delivered, errors = [], []
    real_scope_id = file_index._root_scope_id

    def blocked_scope_id(root):
        entered.set()
        assert release.wait(timeout=5.0)
        return real_scope_id(root)
    monkeypatch.setattr(file_index, "_root_scope_id", blocked_scope_id)
    file_index.set_search_progress_notifier(lambda frame: delivered.append(frame["revision"]))

    def publish():
        try:
            file_index.notify_search_progress(Path("/owned/root"), 1, 1, None)
        except BaseException as error:
            errors.append(error)
    publisher = threading.Thread(target=publish, daemon=True); publisher.start()
    assert entered.wait(timeout=5.0)
    file_index.FileIndexTestScope().cleanup(); release.set()
    publisher.join(timeout=5.0); assert not publisher.is_alive() and delivered == [] and errors == []
    observed = []
    setters = zip(file_index.FileIndexTestScope.CALLBACK_CLEAR_ORDER, (
        "set_background_owner_checker", "set_background_owner_refresh_requester",
        "set_background_index_search_requester", "set_background_owner_bytes_recorder",
        "set_background_owner_done_notifier", "set_search_progress_notifier",
    ))
    for label, name in setters:
        monkeypatch.setattr(file_index, name, lambda value, label=label: observed.append((label, value)))
    monkeypatch.setattr(file_index, "_reset_search_progress_coalescing", lambda: None); monkeypatch.setattr(file_index, "clear_memory_indexes", lambda: file_index.RetirementResult())
    file_index.FileIndexTestScope().cleanup(); assert observed == [(label, None) for label in file_index.FileIndexTestScope.CALLBACK_CLEAR_ORDER]


def test_test_scope_retries_cleanup_when_enter_cleanup_raises(monkeypatch):
    cleanup_calls = []

    def clear_memory_indexes():
        cleanup_calls.append(len(cleanup_calls) + 1)
        if len(cleanup_calls) == 1:
            raise RuntimeError("setup cleanup failed")
        return file_index.RetirementResult()
    monkeypatch.setattr(file_index, "clear_memory_indexes", clear_memory_indexes)
    try:
        with file_index.FileIndexTestScope():
            raise AssertionError("scope body must not run")
    except RuntimeError as exc: assert str(exc) == "setup cleanup failed"
    assert cleanup_calls == [1, 2]
