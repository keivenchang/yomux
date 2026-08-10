# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import socket
import threading
import time
import hashlib
import json
from pathlib import Path

import pytest
from watchfiles import Change

from yolomux_lib import watchd
from yolomux_lib.filesystem import search as search_module
from yolomux_lib import filesystem
from yolomux_lib.filesystem.paths import FilesystemAccessPolicy, FS_ACCESS_POLICY_VERSION
from yolomux_lib.watchd import PersistentWatchService
from yolomux_lib.local_services import rpc
from yolomux_lib.common import TmuxPaneInfo
from yolomux_lib.watchd_protocol import WATCHD_DESCRIPTOR_RESYNC_SECONDS
from yolomux_lib.watchd_protocol import WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS
from yolomux_lib.watchd_protocol import WATCHD_DESCRIPTOR_TTL_SECONDS
from yolomux_lib.watchd_protocol import WATCHD_SERVICE_NAME
from yolomux_lib.watchd_protocol import WATCHD_PROTOCOL_VERSION
from yolomux_lib.watchd_protocol import EffectiveWatchConfiguration
from yolomux_lib import watchd_client
from yolomux_lib.watchd_client import WatchClient
from yolomux_lib import app as app_module
from yolomux_lib.infra.state_services import ClientEventWatcherRecord
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.workspace.session_files import DEFAULT_INDEX_EXCLUDE_DIR_NAMES
from yolomux_lib.filesystem import exclusions


def _request(action: str, **fields: object) -> dict[str, object]:
    return {"action": action, "protocol_version": WATCHD_PROTOCOL_VERSION, **fields}


def _lease(service: PersistentWatchService, pid: int) -> str:
    response, _body = service.handle(_request("lease", client_pid=pid))
    assert response["ok"] is True
    return str(response["lease_id"])


def _descriptor(root: Path, *, generation: int = 1) -> dict[str, object]:
    return {
        "descriptor_generation": generation,
        "expires_at": time.monotonic() + 60.0,
        "roots": [str(root)],
        "files": [],
        "background_files": [],
        "transcripts": [],
        "repo_roots": [str(root)],
        "indexed_dirs": [],
        "skip_dirs": [],
        "settings_path": str(root / "settings.json"),
        "attention_path": str(root / "attention.json"),
        "configured_roots": [str(root)],
    }


def _wait_for_watchd_socket(socket_path: Path) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if socket_path.exists():
            return
        time.sleep(0.01)
    pytest.fail(f"watchd listener socket never appeared: {socket_path}")


def test_watchd_unions_two_leased_web_descriptors_and_preserves_the_other_on_release(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)
    first = _lease(service, 101)
    second = _lease(service, 202)

    one, _ = service.handle(_request("upsert", lease_id=first, descriptor_id="browser-1", descriptor=_descriptor(tmp_path / "one")))
    two, _ = service.handle(_request("upsert", lease_id=second, descriptor_id="browser-2", descriptor=_descriptor(tmp_path / "two")))

    assert one["changed"] is True
    assert two["changed"] is True
    assert service.effective_configuration().roots == (str(tmp_path / "one"), str(tmp_path / "two"))

    service.handle(_request("release", lease_id=first))
    assert service.effective_configuration().roots == (str(tmp_path / "two"),)


def test_watchd_identical_upsert_keeps_watch_generation_stable(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)
    lease_id = _lease(service, 101)
    descriptor = _descriptor(tmp_path / "repo")

    first, _ = service.handle(_request("upsert", lease_id=lease_id, descriptor_id="browser", descriptor=descriptor))
    second, _ = service.handle(_request("upsert", lease_id=lease_id, descriptor_id="browser", descriptor=descriptor))

    assert second["changed"] is False
    assert second["watch_generation"] == first["watch_generation"]


def test_watchd_rejects_stale_descriptor_generation(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)
    lease_id = _lease(service, 101)
    service.handle(_request("upsert", lease_id=lease_id, descriptor_id="browser", descriptor=_descriptor(tmp_path / "new", generation=2)))

    stale, _ = service.handle(_request("upsert", lease_id=lease_id, descriptor_id="browser", descriptor=_descriptor(tmp_path / "old", generation=1)))

    assert stale["ok"] is False
    assert stale["error_code"] == "stale_generation"
    assert service.effective_configuration().roots == (str(tmp_path / "new"),)


def test_watchd_overflow_publishes_one_full_revision(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    monkeypatch.setattr(service, "reconcile", lambda *args, **kwargs: service.publish_revision(kind="full", changed_paths=[], coarse=True))

    service.admit_native_changes({(2, str(tmp_path / f"file-{index}")) for index in range(65)}, watch_generation=service.watch_generation)

    assert service.revision == 1
    assert service.revisions[-1]["kind"] == "full"
    assert service.revisions[-1]["coarse"] is True


def test_watchd_wait_revision_returns_current_snapshot_to_late_joiner(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    service.publish_revision(kind="delta", changed_paths=[str(tmp_path / "changed")])
    latest = service.publish_revision(kind="state", changed_paths=[])

    response, _ = service.handle(_request("wait_revision", epoch="", after_revision=0, timeout_seconds=0.0))

    assert response["changed"] is True
    assert response["reset"] is False
    assert response["epoch"] == service.epoch
    assert response["revision"] == latest


def test_watchd_wait_revision_replays_retained_file_event_before_newer_state_event(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    changed_path = str(tmp_path / "changed.txt")
    file_revision = service.publish_revision(
        kind="delta",
        changed_paths=[changed_path],
        files_changed=[{"path": changed_path, "signature": [changed_path, "file", 4, 1]}],
    )
    state_revision = service.publish_revision(kind="state", changed_paths=[])

    first, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=0, timeout_seconds=0.0)
    )
    second, _ = service.handle(
        _request(
            "wait_revision",
            epoch=service.epoch,
            after_revision=first["revision"]["revision"],
            timeout_seconds=0.0,
        )
    )

    assert first["reset"] is False
    assert first["revision"] == file_revision
    assert second["reset"] is False
    assert second["revision"] == state_revision


def test_watchd_wait_revision_replays_retained_state_before_newer_file_event(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    changed_path = str(tmp_path / "changed.txt")
    state_revision = service.publish_revision(kind="state", changed_paths=[])
    file_revision = service.publish_revision(
        kind="delta",
        changed_paths=[changed_path],
        files_changed=[{"path": changed_path, "signature": [changed_path, "file", 4, 1]}],
    )

    first, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=0, timeout_seconds=0.0)
    )
    second, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=1, timeout_seconds=0.0)
    )

    assert first["revision"] == state_revision
    assert second["revision"] == file_revision


def test_watchd_wait_revision_identical_cursors_observe_same_immutable_revision(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    first_revision = service.publish_revision(kind="delta", changed_paths=[str(tmp_path / "one")])
    service.publish_revision(kind="state", changed_paths=[])
    start = threading.Barrier(3)
    responses = []

    def wait_from_same_cursor() -> None:
        start.wait()
        response, _ = service.handle(
            _request("wait_revision", epoch=service.epoch, after_revision=0, timeout_seconds=0.0)
        )
        responses.append(response)

    threads = [threading.Thread(target=wait_from_same_cursor) for _index in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(responses) == 2
    assert [response["revision"] for response in responses] == [first_revision, first_revision]
    assert [revision["revision"] for revision in service.revisions] == [1, 2]


def test_watchd_bridge_applies_each_retained_revision_in_order(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    changed_path = str(tmp_path / "changed.txt")
    service.publish_revision(
        kind="delta",
        changed_paths=[changed_path],
        files_changed=[{"path": changed_path, "signature": [changed_path, "file", 4, 1]}],
    )
    service.publish_revision(kind="state", changed_paths=[])
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(
        watchd_lease_id="lease",
        watchd_epoch=service.epoch,
        filesystem_roots=(str(tmp_path),),
    )
    webapp.client_watch_service.event_watcher_record = record
    applied = []
    original_apply = webapp.apply_watchd_revision
    monkeypatch.setattr(webapp, "sync_watchd_descriptors", lambda _record: True)
    monkeypatch.setattr(webapp, "publish_client_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(webapp, "mark_indexed_repo_discovery_dirty", lambda _paths: None)
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda **_kwargs: [])

    def apply_revision(current_record, revision, *, reset=False):
        result = original_apply(current_record, revision, reset=reset)
        applied.append((revision["revision"], reset))
        if len(applied) == 2:
            current_record.watchd_stop_event.set()
        return result

    class Client:
        def wait_revision(self, epoch, after_revision, timeout=2.0, *, reconfiguring=False):
            assert reconfiguring is False
            response, _ = service.handle(
                _request(
                    "wait_revision",
                    epoch=epoch,
                    after_revision=after_revision,
                    timeout_seconds=timeout,
                )
            )
            return response

        def release_lease(self, _lease_id):
            return {"ok": True}

    monkeypatch.setattr(webapp, "apply_watchd_revision", apply_revision)
    webapp.watch_client = Client()

    webapp.watchd_revision_loop(record)

    assert applied == [(1, False), (2, False)]
    assert record.watchd_revision == 2
    assert [entry["watchd_revision"] for entry in webapp.client_watch_service.filesystem_history] == [1, 2]


def test_watchd_new_epoch_invalidates_old_cursor(tmp_path):
    old = PersistentWatchService(tmp_path / "old.sock")
    new = PersistentWatchService(tmp_path / "new.sock")

    response, _ = new.handle(_request("wait_revision", epoch=old.epoch, after_revision=99, timeout_seconds=0.0))

    assert response["changed"] is True
    assert response["reset"] is True
    assert response["reset_reason"] == "epoch_changed"
    assert response["epoch"] == new.epoch


def test_watchd_wait_revision_explicitly_resets_cursor_older_than_retained_history(tmp_path, monkeypatch):
    monkeypatch.setattr(watchd, "WATCHD_HISTORY_LIMIT", 2)
    service = PersistentWatchService(tmp_path / "watchd.sock")
    (tmp_path / "repo").mkdir()
    root = str(tmp_path / "repo")
    watched_file = str(tmp_path / "repo" / "watched.txt")
    Path(watched_file).write_text("live", encoding="utf-8")
    service.configuration = EffectiveWatchConfiguration(roots=(root,), files=(watched_file,))
    service.root_signatures[watched_file] = (watched_file, "file", 4, 1)
    service.publish_revision(kind="delta", changed_paths=[str(tmp_path / "one")])
    second = service.publish_revision(kind="delta", changed_paths=[str(tmp_path / "two")])
    latest = service.publish_revision(kind="state", changed_paths=[])

    retained, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=1, timeout_seconds=0.0)
    )
    expired, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=0, timeout_seconds=0.0)
    )

    assert retained["reset"] is False
    assert retained["revision"] == second
    assert expired["changed"] is True
    assert expired["reset"] is True
    assert expired["reset_reason"] == "history_expired"
    assert expired["revision"]["revision"] == latest["revision"]
    assert expired["revision"]["token"] == latest["token"]
    assert expired["revision"]["kind"] == "full"
    assert expired["revision"]["coarse"] is True
    assert expired["revision"]["changed_paths"] == [root, watched_file]
    assert expired["revision"]["files_changed"] == [{
        "path": watched_file,
        "signature": watchd.filesystem.watch_signature(watched_file),
    }]
    assert expired["revision"]["files_changed"][0]["signature"][1] == "file"


def test_watchd_wait_revision_explicitly_resets_cursor_ahead_of_daemon(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    current = service.publish_revision(kind="state", changed_paths=[])

    response, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=99, timeout_seconds=0.2)
    )

    assert response["changed"] is True
    assert response["reset"] is True
    assert response["reset_reason"] == "cursor_ahead"
    assert response["revision"]["revision"] == current["revision"]
    assert response["revision"]["kind"] == "full"
    assert response["revision"]["coarse"] is True


def test_watchd_expired_history_reset_drives_coarse_and_open_file_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(watchd, "WATCHD_HISTORY_LIMIT", 1)
    service = PersistentWatchService(tmp_path / "watchd.sock")
    (tmp_path / "repo").mkdir()
    root = str(tmp_path / "repo")
    watched_file = str(tmp_path / "repo" / "watched.txt")
    Path(watched_file).write_text("live", encoding="utf-8")
    signature = list(watchd.filesystem.watch_signature(watched_file))
    service.configuration = EffectiveWatchConfiguration(roots=(root,), files=(watched_file,))
    service.publish_revision(
        kind="delta",
        changed_paths=[watched_file],
        files_changed=[{"path": watched_file, "signature": [watched_file, "file", 4, 1]}],
    )
    service.publish_revision(kind="state", changed_paths=[])
    response, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=0, timeout_seconds=0.0)
    )
    response = json.loads(json.dumps(response))
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(
        watchd_epoch=service.epoch,
        filesystem_roots=(root,),
        watchd_revision=0,
    )
    webapp.client_watch_service.event_watcher_record = record
    published = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event, payload, **kwargs: published.append((event, payload, kwargs)))
    monkeypatch.setattr(webapp, "mark_indexed_repo_discovery_dirty", lambda _paths: None)
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda **_kwargs: [])

    events = webapp.apply_watchd_revision(
        record,
        response["revision"],
        reset=response["reset"],
    )

    assert response["reset_reason"] == "history_expired"
    assert events == ["files_changed", "fs_changed"]
    assert record.watchd_revision == 2
    assert [entry[0] for entry in published] == ["files_changed", "fs_changed"]
    assert published[0][1]["files"] == [{"path": watched_file, "signature": signature}]
    assert published[1][1]["change_summary"] == {"roots_changed": 2, "coarse": True}


def test_watchd_reset_payload_reports_current_state_after_reconfiguration_before_the_first_barrier(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)
    old_root = tmp_path / "old"
    old_root.mkdir()
    new_root = tmp_path / "new"
    new_root.mkdir()
    lease_id = _lease(service, 101)
    service.handle(_request("upsert", lease_id=lease_id, descriptor_id="browser", descriptor=_descriptor(old_root)))
    service.native_healthy = True
    service.active_watch_generation = service.watch_generation
    stale = service.publish_revision(kind="state", changed_paths=[])
    service.handle(_request("upsert", lease_id=lease_id, descriptor_id="browser", descriptor=_descriptor(new_root, generation=2)))
    service.root_generations[str(new_root)] = 9
    service.repo_generations[str(new_root)] = 9

    response, _ = service.handle(_request("wait_revision", epoch="retired-daemon-epoch", after_revision=0, timeout_seconds=0.0))

    payload = response["revision"]
    assert response["reset_reason"] == "epoch_changed"
    assert stale["roots"] == [str(old_root)] and stale["healthy"] is True
    assert payload["roots"] == [str(new_root)]
    assert payload["watch_generation"] == service.watch_generation
    assert payload["active_watch_generation"] == service.active_watch_generation
    assert payload["watch_generation"] != payload["active_watch_generation"]
    assert payload["healthy"] is False
    assert payload["fallback"] is False
    assert payload["root_generations"] == {str(new_root): 9}
    assert payload["repo_generations"] == {str(new_root): 9}
    assert payload["revision"] == service.revision
    assert payload["token"] == f"{service.epoch}:{service.revision}"
    assert payload["kind"] == "full"
    assert payload["coarse"] is True
    assert str(new_root) in payload["changed_paths"]


def test_watchd_reset_scan_reports_exact_disk_signatures_and_never_invents_a_signature_kind(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    root = tmp_path / "repo"
    root.mkdir()
    present = root / "present.txt"
    present.write_text("present", encoding="utf-8")
    absent = root / "absent.txt"
    service.configuration = EffectiveWatchConfiguration(
        roots=(str(root),),
        files=(str(present), str(absent)),
    )
    service.root_signatures[str(present)] = (str(present), "file", 1, 1)
    service.publish_revision(kind="state", changed_paths=[])

    response, _ = service.handle(_request("wait_revision", epoch="retired-daemon-epoch", after_revision=0, timeout_seconds=0.0))

    files_changed = response["revision"]["files_changed"]
    assert files_changed == [
        {"path": str(present), "signature": watchd.filesystem.watch_signature(str(present))},
        {"path": str(absent), "signature": watchd.filesystem.watch_signature(str(absent))},
    ]
    assert [item["signature"][1] for item in files_changed] == ["file", "missing"]
    assert all(item["signature"][1] in {"file", "dir", "missing"} for item in files_changed)


def test_watchd_reset_scan_overrides_a_stale_cached_file_signature(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    root = tmp_path / "repo"
    root.mkdir()
    watched_file = root / "watched.txt"
    watched_file.write_text("current disk content", encoding="utf-8")
    service.configuration = EffectiveWatchConfiguration(roots=(str(root),), files=(str(watched_file),))
    stale_signature = (str(watched_file), "file", 1, 1)
    service.root_signatures[str(watched_file)] = stale_signature
    service.publish_revision(
        kind="delta",
        changed_paths=[str(watched_file)],
        files_changed=[{"path": str(watched_file), "signature": stale_signature}],
    )

    response, _ = service.handle(_request("wait_revision", epoch="retired-daemon-epoch", after_revision=0, timeout_seconds=0.0))

    exact = watchd.filesystem.watch_signature(str(watched_file))
    assert exact != stale_signature
    assert response["revision"]["files_changed"] == [{"path": str(watched_file), "signature": exact}]
    assert service.root_signatures[str(watched_file)] == exact


def test_watchd_reset_signature_scan_does_not_block_unrelated_status_or_revision_wait(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    root = tmp_path / "repo"
    root.mkdir()
    watched_file = root / "watched.txt"
    watched_file.write_text("watched", encoding="utf-8")
    service.configuration = EffectiveWatchConfiguration(roots=(str(root),), files=(str(watched_file),))
    service.publish_revision(kind="state", changed_paths=[])
    signature_started = threading.Event()
    release_signature = threading.Event()
    reset_finished = threading.Event()
    outcomes = {}

    def slow_signature(raw_path, **_kwargs):
        assert raw_path == str(watched_file)
        signature_started.set()
        assert release_signature.wait(1.0)
        return (str(watched_file), "file", 7, 7)

    monkeypatch.setattr(watchd.filesystem, "watch_signature", slow_signature)

    def reset_wait() -> None:
        outcomes["reset"], _ = service.handle(
            _request("wait_revision", epoch="retired-daemon-epoch", after_revision=0, timeout_seconds=0.0)
        )
        reset_finished.set()

    reset_thread = threading.Thread(target=reset_wait)
    reset_thread.start()
    try:
        assert signature_started.wait(1.0)
        status, _ = service.handle(_request("status"))
        current, _ = service.handle(
            _request("wait_revision", epoch=service.epoch, after_revision=1, timeout_seconds=0.0)
        )
        assert status["ok"] is True
        assert current["ok"] is True
        assert current["changed"] is False
        assert not reset_finished.is_set()
    finally:
        release_signature.set()
        reset_thread.join(timeout=1.0)

    assert reset_finished.is_set()
    assert outcomes["reset"]["reset_reason"] == "epoch_changed"
    assert outcomes["reset"]["revision"]["files_changed"] == [
        {"path": str(watched_file), "signature": (str(watched_file), "file", 7, 7)},
    ]


def test_watchd_wait_revision_concurrent_distinct_cursors_each_receive_their_own_next_revision(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    first = service.publish_revision(kind="delta", changed_paths=[str(tmp_path / "one")])
    second = service.publish_revision(kind="state", changed_paths=[])
    third = service.publish_revision(kind="delta", changed_paths=[str(tmp_path / "three")])
    start = threading.Barrier(4)
    observed: dict[int, dict[str, object]] = {}
    lock = threading.Lock()

    def wait_from(cursor: int) -> None:
        start.wait()
        response, _ = service.handle(
            _request("wait_revision", epoch=service.epoch, after_revision=cursor, timeout_seconds=0.0)
        )
        with lock:
            observed[cursor] = response

    threads = [threading.Thread(target=wait_from, args=(cursor,)) for cursor in (0, 1, 2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert [observed[cursor]["revision"] for cursor in (0, 1, 2)] == [first, second, third]
    assert [observed[cursor]["reset"] for cursor in (0, 1, 2)] == [False, False, False]


def test_watchd_wait_revision_returns_the_oldest_retained_revision_at_the_history_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(watchd, "WATCHD_HISTORY_LIMIT", 2)
    service = PersistentWatchService(tmp_path / "watchd.sock")
    service.publish_revision(kind="delta", changed_paths=[str(tmp_path / "one")])
    oldest_retained = service.publish_revision(kind="delta", changed_paths=[str(tmp_path / "two")])
    service.publish_revision(kind="state", changed_paths=[])

    boundary, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=1, timeout_seconds=0.0)
    )

    assert [revision["revision"] for revision in service.revisions] == [2, 3]
    assert boundary["reset"] is False
    assert boundary["reset_reason"] == ""
    assert boundary["revision"] == oldest_retained
    assert boundary["current_revision"] == 3


def test_watchd_wait_revision_at_the_current_revision_times_out_without_a_reset(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    current = service.publish_revision(kind="state", changed_paths=[])

    response, _ = service.handle(
        _request("wait_revision", epoch=service.epoch, after_revision=current["revision"], timeout_seconds=0.05)
    )

    assert response["ok"] is True
    assert response["changed"] is False
    assert response["reset"] is False
    assert response["reset_reason"] == ""
    assert response["revision"] == {}
    assert response["current_revision"] == current["revision"]


def test_watchd_shutdown_waits_for_native_owner(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    joined: list[float | None] = []

    class Worker:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            joined.append(timeout)

    service.native_worker = Worker()
    service.shutdown_watcher()

    assert service.native_stop_event.is_set()
    assert joined == [None]


def test_watchd_wait_revision_does_not_starve_status(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    started = threading.Event()
    finished = threading.Event()

    def wait() -> None:
        started.set()
        service.handle(_request("wait_revision", epoch=service.epoch, after_revision=0, timeout_seconds=0.2))
        finished.set()

    thread = threading.Thread(target=wait)
    thread.start()
    assert started.wait(1.0)
    status, _ = service.handle(_request("status"))
    assert status["ok"] is True
    assert finished.wait(1.0)
    thread.join()


def test_watchd_client_wait_revision_reserves_transport_margin_beyond_the_server_long_poll(monkeypatch):
    client = object.__new__(WatchClient)
    request_evidence = {}

    def request(payload, timeout):
        request_evidence.update(payload=payload, timeout=timeout)
        server_wait = float(payload["timeout_seconds"])
        if timeout < server_wait + 1.0:
            raise TimeoutError("normal long-poll response crossed the transport deadline")
        return {"ok": True, "changed": False}

    monkeypatch.setattr(client, "request", request)

    assert client.wait_revision("epoch", 7, timeout=2.0) == {"ok": True, "changed": False}
    assert request_evidence["payload"]["timeout_seconds"] == 2.0
    assert request_evidence["timeout"] == 3.0


def test_watchd_client_descriptor_mutations_reserve_the_same_transport_margin(monkeypatch):
    client = object.__new__(WatchClient)
    requests = []

    def request(payload, timeout):
        requests.append((payload, timeout))
        if timeout < 1.0:
            raise TimeoutError("descriptor acknowledgement crossed the composed transport deadline")
        return {"ok": True}

    monkeypatch.setattr(client, "request", request)

    assert client.upsert("lease", "browser", {"descriptor_generation": 1}) == {"ok": True}
    assert client.remove("lease", "browser") == {"ok": True}
    assert [(payload["action"], timeout) for payload, timeout in requests] == [
        ("upsert", 1.0),
        ("remove", 1.0),
    ]


def test_watchd_declares_reconfiguring_instead_of_stalling_an_in_flight_long_poll(tmp_path, monkeypatch):
    """Registering the native watch blocks every handler; a waiter must not eat it.

    ``watchfiles`` registers its recursive watch on the first advance of its
    generator, in one call that holds the interpreter lock for its whole
    duration: measured over a 63-root configuration it stops every other thread
    in watchd for 3.4 s, longer than a long poll's entire transport deadline.
    The long poll already in flight when that starts is the one the browser
    bridge holds, and it used to be answered only after the registration, i.e.
    after its client had already recorded a transport timeout.
    """
    service = PersistentWatchService(tmp_path / "watchd.sock")
    service.watch_generation = 1
    service.configuration = EffectiveWatchConfiguration(
        configured_roots=(str(tmp_path),),
        watch_paths=(str(tmp_path),),
    )
    reconciled = threading.Event()
    poll_armed = threading.Event()
    registering = threading.Event()
    release = threading.Event()
    reconcile = service.reconcile

    def gated_reconcile(**kwargs):
        # Arm the long poll after the configuration scan has published, so the
        # waiter is genuinely in flight when the blocking registration starts.
        result = reconcile(**kwargs)
        reconciled.set()
        assert poll_armed.wait(10.0)
        return result

    def fake_watch(*paths, **kwargs):
        del paths, kwargs
        registering.set()
        assert release.wait(10.0)
        service.stop_event.set()
        return
        yield  # pragma: no cover - the daemon stops before any batch is produced

    monkeypatch.setattr(service, "reconcile", gated_reconcile)
    monkeypatch.setattr(watchd, "watchfiles_watch", fake_watch)

    outcome: dict[str, object] = {}

    def long_poll(after_revision: int) -> None:
        started = time.monotonic()
        response, _body = service.handle(
            _request("wait_revision", epoch=service.epoch, after_revision=after_revision, timeout_seconds=2.0)
        )
        outcome.update(response=response, elapsed=time.monotonic() - started)

    native = threading.Thread(target=service.native_watch_loop, name="watchd-native", daemon=True)
    native.start()
    waiter = None
    try:
        assert reconciled.wait(10.0)
        waiter = threading.Thread(target=long_poll, args=(service.revision,), name="watchd-long-poll", daemon=True)
        waiter.start()
        time.sleep(0.1)
        poll_armed.set()
        assert registering.wait(10.0)
        waiter.join(timeout=5.0)
        assert not waiter.is_alive()
    finally:
        poll_armed.set()
        release.set()
        service.stop_event.set()
        native.join(timeout=10.0)
        if waiter is not None:
            waiter.join(timeout=10.0)

    response = outcome["response"]
    assert response["ok"] is True
    # The waiter must be released by the declaration, not by its own long-poll
    # deadline: expiring at 2.0 s is exactly the stall this contract removes.
    assert float(outcome["elapsed"]) < 1.5
    assert response["state"] == "reconfiguring"
    assert response["error_code"] == "native_watch_rebuild"
    assert response["retry_after_seconds"] == watchd.WATCHD_NATIVE_BUILD_RETRY_SECONDS
    assert response["changed"] is False


def test_watchd_bridge_covers_the_upsert_issued_inside_a_rebuild_it_already_caused(monkeypatch):
    """The request after a generation bump must not die on the steady-state margin.

    watchd answers the upsert that bumps the generation and only then blocks for
    the native registration, so the next request is the one that arrives while
    nothing can be answered.  Measured against a 63-root configuration that
    window is 2.9-3.5 s, and the loop's next upsert used to be armed at the 1.0 s
    steady-state margin and die there: the live soak recorded exactly one
    ``event=upsert delivery=timeout client_elapsed_ms=1001.686``.
    The client does not have to be told: an upsert it issued returned a watch
    generation the daemon has not activated, which is the window itself.
    """
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(watchd_lease_id="lease")
    rebuild_seconds = 2.5
    armed: list[tuple[str, bool]] = []

    class Client:
        def upsert(self, lease_id, descriptor_id, descriptor, *, reconfiguring=False):
            del lease_id, descriptor
            armed.append((descriptor_id, reconfiguring))
            # The daemon is unreachable for the whole registration, so only a
            # deadline that covers it can be answered at all.
            if not reconfiguring and WatchClient.transport_margin(False) < rebuild_seconds and descriptor_id != "first":
                return {"ok": False, "error": "timed out", "_transport_error": "timeout", "exception_type": "TimeoutError"}
            return {"ok": True, "watch_generation": 9, "active_watch_generation": 8}

        def remove(self, lease_id, descriptor_id, *, reconfiguring=False):
            raise AssertionError("this fixture retires no descriptor")

    webapp.watch_client = Client()
    monkeypatch.setattr(
        webapp,
        "watchd_descriptor_payloads",
        lambda: {"first": {"descriptor_generation": 1}, "second": {"descriptor_generation": 1}},
    )

    assert webapp.sync_watchd_descriptors(record) is True
    assert armed == [("first", False), ("second", True)]
    assert record.watchd_rebuild_window_open() is True
    assert WatchClient.transport_margin(True) > rebuild_seconds


def test_watchd_bridge_reports_a_timeout_outside_a_rebuild_window_unchanged(monkeypatch):
    """Widening a deadline inside a declared window may not hide a real failure."""
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(watchd_lease_id="lease", watchd_synced_generation=3, watchd_active_generation=3)
    published: list[tuple[str, object]] = []

    class Client:
        def upsert(self, lease_id, descriptor_id, descriptor, *, reconfiguring=False):
            del lease_id, descriptor_id, descriptor
            assert reconfiguring is False
            return {"ok": False, "error": "timed out", "_transport_error": "timeout", "exception_type": "TimeoutError"}

    webapp.watch_client = Client()
    monkeypatch.setattr(webapp, "watchd_descriptor_payloads", lambda: {"active": {"descriptor_generation": 1}})
    monkeypatch.setattr(webapp, "publish_watchd_failure", lambda _record, response, *, action: published.append((action, response.get("_transport_error"))))

    assert webapp.sync_watchd_descriptors(record) is False
    assert published == [("upsert", "timeout")]
    assert record.watchd_rebuild_window_open() is False


def test_watchd_client_reconfiguring_long_poll_reserves_the_declared_rebuild_margin(monkeypatch):
    client = object.__new__(WatchClient)
    requests = []

    def request(payload, timeout):
        requests.append((payload["action"], timeout))
        return {"ok": True, "state": "reconfiguring", "error_code": "native_watch_rebuild", "retry_after_seconds": 0.25}

    monkeypatch.setattr(client, "request", request)

    response = client.wait_revision("epoch", 7, timeout=2.0, reconfiguring=True)
    assert WatchClient.response_is_reconfiguring(response) is True
    assert WatchClient.reconfigure_backoff_seconds(response) == 0.25
    client.upsert("lease", "browser", {"descriptor_generation": 1}, reconfiguring=True)
    assert requests == [
        ("wait_revision", 2.0 + watchd_client.WATCHD_RECONFIGURE_TRANSPORT_MARGIN_SECONDS),
        ("upsert", watchd_client.WATCHD_RECONFIGURE_TRANSPORT_MARGIN_SECONDS),
    ]


def test_watchd_slow_file_signature_does_not_block_unrelated_upsert_or_revision_wait(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)
    watched_root = tmp_path / "watched"
    watched_root.mkdir()
    watched_file = watched_root / "watched.txt"
    watched_file.write_text("watched", encoding="utf-8")
    watched_lease = _lease(service, 101)
    unrelated_lease = _lease(service, 202)
    watched_descriptor = _descriptor(watched_root)
    watched_descriptor["files"] = [str(watched_file)]
    service.handle(_request("upsert", lease_id=watched_lease, descriptor_id="watched", descriptor=watched_descriptor))
    watched_generation = service.watch_generation
    signature_started = threading.Event()
    release_signature = threading.Event()
    native_finished = threading.Event()
    upsert_finished = threading.Event()
    wait_finished = threading.Event()
    outcomes = {}

    def slow_signature(raw_path, **_kwargs):
        assert raw_path == str(watched_file)
        signature_started.set()
        assert release_signature.wait(1.0)
        return (str(watched_file), "file", 1, 7)

    def admit_change():
        outcomes["native"] = service.admit_native_changes({(2, str(watched_file))}, watch_generation=watched_generation)
        native_finished.set()

    def upsert_unrelated():
        outcomes["upsert"], _ = service.handle(
            _request(
                "upsert",
                lease_id=unrelated_lease,
                descriptor_id="unrelated",
                descriptor=_descriptor(tmp_path / "unrelated"),
            )
        )
        upsert_finished.set()

    def wait_revision():
        outcomes["wait"], _ = service.handle(
            _request("wait_revision", epoch=service.epoch, after_revision=0, timeout_seconds=0.05)
        )
        wait_finished.set()

    monkeypatch.setattr(watchd.filesystem, "watch_signature", slow_signature)
    native_thread = threading.Thread(target=admit_change)
    upsert_thread = threading.Thread(target=upsert_unrelated)
    wait_thread = threading.Thread(target=wait_revision)
    native_thread.start()
    assert signature_started.wait(1.0)
    upsert_thread.start()
    wait_thread.start()
    try:
        upsert_completed_while_signature_blocked = upsert_finished.wait(0.2)
        wait_completed_while_signature_blocked = wait_finished.wait(0.2)
        assert upsert_completed_while_signature_blocked and wait_completed_while_signature_blocked
    finally:
        release_signature.set()
        native_thread.join(timeout=1.0)
        upsert_thread.join(timeout=1.0)
        wait_thread.join(timeout=1.0)

    assert native_finished.is_set()
    assert outcomes["native"] is None
    assert outcomes["upsert"]["ok"] is True
    assert outcomes["wait"]["ok"] is True
    assert outcomes["wait"]["changed"] is False
    assert service.revision == 0
    assert service.effective_configuration().roots == (str(tmp_path / "unrelated"), str(watched_root))


def test_watchd_slow_generation_bump_does_not_block_an_unrelated_status_request(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)
    root = tmp_path / "repo"
    nested_repo = root / "nested"
    nested_repo.mkdir(parents=True)
    settings_path = root / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    lease = _lease(service, 303)
    descriptor = _descriptor(root)
    # A repo root is never scanned, so every changed path is matched against it
    # one by one. That match loop is the generation bump this test holds open.
    descriptor["repo_roots"] = [str(nested_repo)]
    service.handle(_request("upsert", lease_id=lease, descriptor_id="watched", descriptor=descriptor))
    generation = service.watch_generation
    assert service.reconcile(reason="configuration", watch_generation=generation) is not None
    assert service.repo_generations[str(nested_repo)] == 1
    settings_path.write_text('{"changed": true}', encoding="utf-8")
    match_started = threading.Event()
    release_match = threading.Event()
    status_finished = threading.Event()
    real_path_is_within = watchd.filesystem._path_is_within
    outcomes = {}

    def slow_path_is_within(path, matched_root):
        match_started.set()
        assert release_match.wait(2.0)
        return real_path_is_within(path, matched_root)

    def reconcile_changed_settings():
        outcomes["reconcile"] = service.reconcile(reason="periodic", watch_generation=generation)

    def status():
        outcomes["status"], _ = service.handle(_request("status"))
        status_finished.set()

    monkeypatch.setattr(watchd.filesystem, "_path_is_within", slow_path_is_within)
    reconcile_thread = threading.Thread(target=reconcile_changed_settings)
    status_thread = threading.Thread(target=status)
    reconcile_thread.start()
    assert match_started.wait(2.0)
    status_thread.start()
    try:
        assert status_finished.wait(0.3), "a generation bump held the service condition across path matching"
    finally:
        release_match.set()
        reconcile_thread.join(timeout=2.0)
        status_thread.join(timeout=2.0)

    assert outcomes["status"]["ok"] is True
    assert outcomes["reconcile"] is not None
    assert service.repo_generations[str(nested_repo)] == 2


def test_watchd_listener_accepts_a_request_while_the_service_condition_is_held(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock", idle_seconds=60.0)
    idle_probe_entered = threading.Event()
    original_idle_due = service.idle_due

    def observed_idle_due() -> bool:
        idle_probe_entered.set()
        return original_idle_due()

    service.idle_due = observed_idle_due
    listener = threading.Thread(target=service.run, daemon=True)
    listener.start()
    held = threading.Event()
    release_hold = threading.Event()

    def hold_service_condition() -> None:
        with service.lock:
            held.set()
            assert release_hold.wait(3.0)

    holder = threading.Thread(target=hold_service_condition)
    try:
        _wait_for_watchd_socket(service.socket_path)
        holder.start()
        assert held.wait(2.0)
        idle_probe_entered.clear()
        # The listener owns request admission. Its maintenance probe must come
        # back from a held service condition, or nothing is accepted at all.
        assert idle_probe_entered.wait(1.0), "the listener never returned to accept() while the condition was held"
        started_at = time.monotonic()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(service.socket_path))
            envelope = rpc.new_envelope(WATCHD_SERVICE_NAME, "ping", _request("ping"))
            rpc.write_message(client, envelope, envelope.payload)
            _envelope, response, _binary, _legacy = rpc.read_message(client)
        elapsed = time.monotonic() - started_at
    finally:
        release_hold.set()
        holder.join(timeout=2.0)
        service.stop_event.set()
        listener.join(timeout=3.0)

    assert response["ok"] is True
    assert response["service"] == WATCHD_SERVICE_NAME
    assert elapsed < 0.5
    assert listener.is_alive() is False


def test_watchd_snapshot_returns_exact_opaque_watch_diff_bytes_and_uniform_product(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "changed.txt").write_text("changed", encoding="utf-8")
    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)
    lease_id = _lease(service, 101)
    service.handle(_request("upsert", lease_id=lease_id, descriptor_id="browser", descriptor=_descriptor(root)))
    service.publish_revision(kind="delta", changed_paths=[str(root / "changed.txt")])

    accepted, accepted_body = service.handle(_request("snapshot", since="stale-token", force_full=False))
    metadata, body = service.handle(_request("snapshot_product", producer_id=accepted["producer_id"], timeout_seconds=1.0))
    payload = json.loads(body)

    assert accepted["state"] == "accepted"
    assert accepted_body == b""
    assert metadata["ok"] is True
    assert metadata["source_epoch"] == service.source_epoch
    assert metadata["daemon_epoch"] == service.epoch
    assert metadata["token"] == payload["token"]
    assert metadata["product"] == {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }
    assert payload["mode"] == "full"
    assert payload["directories"][0]["path"] == str(root)
    assert payload["directories"][0]["data"]["path"] == str(root)
    service.shutdown_snapshot_worker()


def test_watchd_snapshot_accepts_cold_work_before_held_producer_and_retains_exact_bytes(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "changed.txt").write_text("changed", encoding="utf-8")
    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)
    lease_id = _lease(service, 101)
    service.handle(_request("upsert", lease_id=lease_id, descriptor_id="browser", descriptor=_descriptor(root)))
    service.publish_revision(kind="delta", changed_paths=[str(root / "changed.txt")])
    producer_entered = threading.Event()
    release_producer = threading.Event()
    returned = threading.Event()
    outcome = {}
    original_batch = watchd.filesystem.filesystem_batch_result

    def held_batch(payload):
        producer_entered.set()
        assert release_producer.wait(1.0)
        return original_batch(payload)

    def accept_snapshot():
        outcome["accepted"] = service.handle(_request("snapshot", since="stale-token", force_full=False))
        returned.set()

    monkeypatch.setattr(watchd.filesystem, "filesystem_batch_result", held_batch)
    thread = threading.Thread(target=accept_snapshot)
    thread.start()
    try:
        assert producer_entered.wait(1.0)
        assert returned.wait(0.1), "cold watchd acceptance waited for snapshot production"
        accepted, accepted_body = outcome["accepted"]
        assert accepted["ok"] is True
        assert accepted["state"] == "accepted"
        assert accepted_body == b""
        producer_id = accepted["producer_id"]
    finally:
        release_producer.set()
        thread.join(timeout=1.0)

    ready, body = service.handle(_request("snapshot_product", producer_id=producer_id, timeout_seconds=1.0))
    warm, warm_body = service.handle(_request("snapshot", since="stale-token", force_full=False))
    try:
        assert ready["state"] == "ready"
        assert ready["product"]["length"] == len(body)
        assert warm["state"] == "ready"
        assert warm_body == body
    finally:
        service.shutdown_snapshot_worker()


def test_web_client_event_loop_never_runs_filesystem_polling_fallback(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    monkeypatch.setattr(webapp.client_events, "has_demand", lambda *channels: "files" in channels)
    monkeypatch.setattr(webapp.client_events, "aggregate_channels", lambda: {"files"})
    assert not hasattr(webapp, "poll_client_events_once")
    assert not hasattr(webapp, "poll_client_file_events_once")
    assert not hasattr(webapp, "poll_client_background_file_events_once")
    assert not hasattr(webapp, "start_native_filesystem_watcher")

    def stop_after_one_iteration(_now: float, _record: ClientEventWatcherRecord | None = None) -> float:
        record.stop_event.set()
        return 0.0

    monkeypatch.setattr(webapp, "client_event_watch_sleep_seconds", stop_after_one_iteration)
    webapp.client_event_watch_loop(record)


def test_watchd_failure_episode_logs_typed_failure_once_and_bounded_recovery(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    emitted = []
    published = []
    now = iter((10.0, 11.0, 12.1, 13.0, 14.0))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(now))
    monkeypatch.setattr(app_module, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: published.append((args, kwargs)))

    response = {"ok": False, "retryable": True, "error_code": "producer_failed"}
    webapp.publish_watchd_failure(record, response, action="wait_revision")
    webapp.publish_watchd_failure(record, response, action="wait_revision")

    assert published == []
    assert emitted == []
    webapp.publish_watchd_failure(record, response, action="wait_revision")
    webapp.publish_watchd_failure(record, response, action="wait_revision")
    assert emitted == [(('warning', 'watchd', 'watchd wait_revision failed (producer_failed); retrying'), {
        'category': 'transport',
        'dedupe_key': 'watchd-failure:1',
        'request_id': 'watchd-episode-1',
        'route': 'local-service:watchd',
        'event': 'watchd_wait_revision_failure',
        'delivery': 'retrying',
    })]
    webapp.apply_watchd_revision(record, {"epoch": "epoch", "revision": 1, "healthy": True})
    assert emitted[-1][0] == ('info', 'watchd', 'watchd recovered after 4.0s and 4 failed attempt(s)')
    assert emitted[-1][1]["event"] == "watchd_recovered"
    assert emitted[-1][1]["delivery"] == "recovered:retrying"
    assert record.watchd_failure_episode == 0


def test_watchd_failure_recovered_within_grace_emits_no_failure_or_recovery(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    emitted = []
    now = iter((10.0, 11.1))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(now))
    monkeypatch.setattr(app_module, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))

    webapp.publish_watchd_failure(
        record,
        {"ok": False, "error_code": "service_unavailable"},
        action="acquire",
    )
    webapp.publish_watchd_recovery(record)

    assert emitted == []
    assert record.watchd_failure_episode == 0
    assert record.watchd_failure_count == 0
    assert record.watchd_failure_published is False


def test_watchd_transport_failure_reuses_local_service_transport_log_owner(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    emitted = []
    published = []
    monkeypatch.setattr(app_module, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: published.append((args, kwargs)))

    webapp.publish_watchd_failure(record, {"ok": False, "_transport_error": "refused"}, action="wait_revision")
    webapp.publish_watchd_failure(record, {"ok": False, "_transport_error": "refused"}, action="wait_revision")

    assert emitted == []
    assert published == []
    assert record.watchd_failure_episode == 0
    assert record.watchd_failure_count == 0


def test_watchd_unchanged_success_closes_existing_failure_episode(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(watchd_lease_id="lease", watchd_epoch="epoch")
    webapp.client_watch_service.event_watcher_record = record
    emitted = []
    # The failure is observed at 10.0 and the recovery at 11.0. This drives the real revision
    # loop, which reads the clock a fixed number of extra times per iteration to enforce
    # WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS, so the clock returns 11.0 from then on rather
    # than exhausting: the elapsed this test asserts on is unchanged, the call count is not
    # what it is testing.
    readings = iter((10.0,))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(readings, 11.0))
    monkeypatch.setattr(app_module, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))
    webapp.publish_watchd_failure(record, {"ok": False, "error_code": "deadline_expired", "retryable": True}, action="wait_revision")
    monkeypatch.setattr(webapp, "sync_watchd_descriptors", lambda _record: True)

    class Client:
        def wait_revision(self, *_args, **_kwargs):
            record.watchd_stop_event.set()
            return {"ok": True, "changed": False, "epoch": "epoch", "current_revision": 0}

        def release_lease(self, _lease_id):
            return {"ok": True}

    webapp.watch_client = Client()
    webapp.watchd_revision_loop(record)

    assert emitted == []
    assert record.watchd_failure_episode == 0


def test_watchd_unhealthy_newer_revision_does_not_claim_recovery(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    emitted = []
    now = iter((10.0, 12.1))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(now))
    monkeypatch.setattr(app_module, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *_args, **_kwargs: None)
    webapp.publish_watchd_failure(record, {"ok": False, "error_code": "producer_failed"}, action="upsert")
    webapp.publish_watchd_failure(record, {"ok": False, "error_code": "producer_failed"}, action="upsert")

    webapp.apply_watchd_revision(record, {"epoch": "epoch", "revision": 2, "healthy": False})

    assert record.watchd_failure_episode == 1
    assert [entry[1]["event"] for entry in emitted] == ["watchd_upsert_failure"]


def test_watchd_revision_outside_filesystem_roots_does_not_publish_fs_changed(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(filesystem_roots=("/repo",))
    webapp.client_watch_service.event_watcher_record = record
    published = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: published.append((args, kwargs)))

    events = webapp.apply_watchd_revision(record, {
        "epoch": "epoch",
        "revision": 2,
        "watch_generation": 1,
        "roots": ["/repo"],
        "root_generations": {"/repo": 1},
        "changed_paths": ["/tmp/yolomux-state/attention.json"],
        "healthy": True,
    })

    assert events == []
    assert published == []


def test_watchd_revision_history_retains_exact_changed_file_provenance(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(filesystem_roots=("/repo",))
    webapp.client_watch_service.event_watcher_record = record
    monkeypatch.setattr(webapp, "publish_client_event", lambda *_args, **_kwargs: None)
    files_changed = [{"path": "/repo/file.txt", "signature": ["/repo/file.txt", "file", 4, 1]}]

    webapp.apply_watchd_revision(record, {
        "epoch": "epoch",
        "revision": 3,
        "watch_generation": 2,
        "active_watch_generation": 2,
        "roots": ["/repo"],
        "root_generations": {"/repo": 2},
        "changed_paths": ["/repo/file.txt"],
        "files_changed": files_changed,
        "healthy": True,
    })
    files_changed[0]["path"] = "/mutated-after-apply"

    history = webapp.client_watch_service.filesystem_history
    assert len(history) == 1
    assert history[0]["watchd_epoch"] == "epoch"
    assert history[0]["watchd_revision"] == 3
    assert history[0]["watch_generation"] == 2
    assert history[0]["active_watch_generation"] == 2
    assert record.watchd_applied_generation == 2
    assert record.watchd_active_generation == 2
    assert history[0]["changed_paths"] == ("/repo/file.txt",)
    assert history[0]["files_changed"] == [{
        "path": "/repo/file.txt",
        "signature": ["/repo/file.txt", "file", 4, 1],
    }]


def test_watchd_polling_activation_is_a_healthy_applied_generation(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(watchd_synced_generation=2)
    webapp.client_watch_service.event_watcher_record = record
    monkeypatch.setattr(webapp, "publish_client_event", lambda *_args, **_kwargs: None)

    webapp.apply_watchd_revision(record, {
        "epoch": "epoch",
        "revision": 4,
        "watch_generation": 2,
        "active_watch_generation": 2,
        "roots": ["/repo"],
        "root_generations": {"/repo": 1},
        "changed_paths": [],
        "healthy": False,
        "fallback": True,
    })

    assert record.filesystem_healthy is True
    assert record.watchd_state == "polling"
    assert record.watchd_applied_generation == record.watchd_active_generation == record.watchd_synced_generation == 2


def test_watchd_native_filter_excludes_every_git_internal_including_control_files(tmp_path):
    """No path beneath an ignored directory is admitted, control file or not.

    This test previously required ``.git/HEAD`` and ``.git/refs/**`` to be
    admitted while ``.git/objects/**`` was excluded.  That exception is what
    turned an ignored pathname into a transport signal, and it also meant the
    daemon and the search index disagreed about the same tree.  ``.git`` is now
    ignored exactly like ``.cache`` or ``node_modules``.
    """
    service = PersistentWatchService(tmp_path / "watchd.sock")
    configuration = EffectiveWatchConfiguration(
        configured_roots=(str(tmp_path),),
        skip_dirs=(".git",),
        watch_paths=(str(tmp_path),),
    )
    watch_filter = service.native_watch_filter(configuration)

    assert watch_filter(Change.modified, str(tmp_path / ".git" / "HEAD")) is False
    assert watch_filter(Change.modified, str(tmp_path / ".git" / "refs" / "heads" / "main")) is False
    assert watch_filter(Change.modified, str(tmp_path / ".git" / "objects" / "aa" / "object")) is False
    assert watch_filter(Change.modified, str(tmp_path / "ordinary.txt")) is True
    assert watch_filter(Change.modified, str(tmp_path / "ignored.pyc")) is False


def test_watchd_native_activation_publishes_barrier_and_processes_first_event(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    target = tmp_path / "watched.txt"
    target.write_text("changed", encoding="utf-8")
    service.watch_generation = 1
    service.configuration = EffectiveWatchConfiguration(
        files=(str(target),),
        configured_roots=(str(tmp_path),),
        watch_paths=(str(tmp_path),),
    )

    def fake_watch(*paths, **kwargs):
        assert paths == (str(tmp_path),)
        assert kwargs["yield_on_timeout"] is True
        yield {(Change.modified, str(target))}
        service.stop_event.set()

    monkeypatch.setattr(watchd, "watchfiles_watch", fake_watch)
    service.native_watch_loop()

    activation = next(revision for revision in service.revisions if revision["kind"] == "state")
    changed = next(revision for revision in service.revisions if revision["kind"] == "delta")
    assert activation["watch_generation"] == activation["active_watch_generation"] == 1
    assert activation["healthy"] is True
    assert changed["changed_paths"] == [str(target)]
    assert changed["files_changed"][0]["path"] == str(target)


def _polling_generation_service(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    root = str(tmp_path)
    signature = (root, "directory", 1, 1, ())
    service.watch_generation = 2
    service.scanned_watch_generation = 1
    service.active_watch_generation = 1
    service.polling_fallback = True
    service.revision = 1
    service.configuration = EffectiveWatchConfiguration(roots=(root,), watch_paths=(root,))
    service.root_signatures = {root: signature}
    service.revisions = [{
        "epoch": service.epoch,
        "revision": 1,
        "watch_generation": 1,
        "active_watch_generation": 1,
        "kind": "state",
        "token": f"{service.epoch}:1",
        "roots": [root],
        "changed_paths": [],
        "repo_generations": {},
        "root_generations": {},
        "healthy": False,
        "fallback": True,
    }]
    return service, root, signature


@pytest.mark.parametrize("backend_state", ("unavailable", "emfile"))
def test_watchd_polling_activation_publishes_generation_barrier_without_filesystem_change(tmp_path, monkeypatch, backend_state):
    service, _root, signature = _polling_generation_service(tmp_path)
    scan_calls = 0

    def successful_scan(*_args, **_kwargs):
        nonlocal scan_calls
        scan_calls += 1
        return signature

    if backend_state == "unavailable":
        monkeypatch.setattr(watchd, "watchfiles_watch", None)
    else:
        def failed_watch(*_args, **_kwargs):
            raise OSError(24, "Too many open files")
            yield

        monkeypatch.setattr(watchd, "watchfiles_watch", failed_watch)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", successful_scan)

    def stop_after_activation(_generation, _generation_stop_event, _timeout):
        activation = service.revisions[-1]
        assert activation["kind"] == "state"
        assert activation["watch_generation"] == activation["active_watch_generation"] == 2
        assert activation["fallback"] is True
        return "stopped"

    monkeypatch.setattr(service, "_wait_watch_generation", stop_after_activation)
    service.native_watch_loop()

    activation = service.revisions[-1]
    assert activation["watch_generation"] == activation["active_watch_generation"] == 2
    assert activation["fallback"] is True
    assert scan_calls == 1
    if backend_state == "emfile":
        assert service.last_error == "[Errno 24] Too many open files"


def test_watchd_polling_fallback_publishes_explicit_file_change_before_native_retry(tmp_path, monkeypatch):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    target = tmp_path / "watched.txt"
    target.write_text("before", encoding="utf-8")
    service.watch_generation = 1
    service.configuration = EffectiveWatchConfiguration(
        files=(str(target),),
        configured_roots=(str(tmp_path),),
        watch_paths=(str(tmp_path),),
    )
    fallback_ready = threading.Event()
    file_change_ready = threading.Event()
    watcher_calls = 0
    original_publish_revision = service.publish_revision

    def failed_watch(*_args, **_kwargs):
        nonlocal watcher_calls
        watcher_calls += 1
        raise OSError(24, "Too many open files")
        yield

    def publish_revision(**kwargs):
        revision = original_publish_revision(**kwargs)
        if revision["fallback"] is True:
            fallback_ready.set()
        if revision["files_changed"]:
            file_change_ready.set()
        return revision

    monkeypatch.setattr(watchd, "watchfiles_watch", failed_watch)
    monkeypatch.setattr(watchd, "WATCHD_POLL_SECONDS", 0.01)
    monkeypatch.setattr(service, "publish_revision", publish_revision)

    service.start_watcher()
    try:
        assert fallback_ready.wait(1.0)
        file_change_ready.clear()
        target.write_text("after", encoding="utf-8")
        assert file_change_ready.wait(1.0)
        changed = service.revisions[-1]
        assert changed["kind"] == "full"
        assert changed["changed_paths"] == [str(target)]
        assert changed["files_changed"] == [{
            "path": str(target),
            "signature": watchd.filesystem.watch_signature(str(target)),
        }]
        assert watcher_calls == 1
    finally:
        service.stop_event.set()
        service.native_stop_event.set()
        service.shutdown_watcher()


def test_watchd_polling_fallback_reconfiguration_restarts_without_poll_interval_delay(tmp_path, monkeypatch):
    service, _root, signature = _polling_generation_service(tmp_path)
    fallback_ready = threading.Event()
    restarted = threading.Event()
    watcher_calls = 0
    original_activate = service._activate_watch_generation

    def failed_watch(*_args, **_kwargs):
        nonlocal watcher_calls
        watcher_calls += 1
        if watcher_calls == 2:
            restarted.set()
            service.stop_event.set()
        raise OSError(24, "Too many open files")
        yield

    def activate(*args, **kwargs):
        activated = original_activate(*args, **kwargs)
        if activated and kwargs.get("polling_fallback") is True:
            fallback_ready.set()
        return activated

    monkeypatch.setattr(watchd, "watchfiles_watch", failed_watch)
    monkeypatch.setattr(watchd, "WATCHD_POLL_SECONDS", 30.0)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", lambda *_args, **_kwargs: signature)
    monkeypatch.setattr(service, "_activate_watch_generation", activate)

    service.start_watcher()
    try:
        assert fallback_ready.wait(1.0)
        with service.lock:
            service.watch_generation += 1
            service.native_stop_event.set()
        assert restarted.wait(1.0)
    finally:
        service.stop_event.set()
        service.native_stop_event.set()
        service.shutdown_watcher()


def test_watchd_shutdown_watcher_interrupts_polling_fallback_without_global_stop(tmp_path, monkeypatch):
    service, _root, signature = _polling_generation_service(tmp_path)
    fallback_ready = threading.Event()
    shutdown_done = threading.Event()
    original_activate = service._activate_watch_generation

    def failed_watch(*_args, **_kwargs):
        raise OSError(24, "Too many open files")
        yield

    def activate(*args, **kwargs):
        activated = original_activate(*args, **kwargs)
        if activated and kwargs.get("polling_fallback") is True:
            fallback_ready.set()
        return activated

    monkeypatch.setattr(watchd, "watchfiles_watch", failed_watch)
    monkeypatch.setattr(watchd, "WATCHD_POLL_SECONDS", 30.0)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", lambda *_args, **_kwargs: signature)
    monkeypatch.setattr(service, "_activate_watch_generation", activate)

    service.start_watcher()
    assert fallback_ready.wait(1.0)
    shutdown_thread = threading.Thread(
        target=lambda: (service.shutdown_watcher(), shutdown_done.set()),
        daemon=True,
    )
    shutdown_thread.start()
    try:
        assert shutdown_done.wait(1.0)
        assert service.stop_event.is_set() is False
        assert service.native_worker is None
    finally:
        service.stop_event.set()
        service.native_stop_event.set()
        shutdown_thread.join(timeout=1.0)


@pytest.mark.parametrize(
    ("configuration_field", "changed_flag", "expects_file_row"),
    (
        ("files", None, True),
        ("background_files", None, True),
        ("settings_paths", "settings_changed", False),
        ("attention_paths", "attention_changed", False),
        ("transcripts", "transcripts_changed", False),
    ),
)
def test_watchd_reconcile_tracks_each_explicit_configuration_surface(
    tmp_path,
    configuration_field,
    changed_flag,
    expects_file_row,
):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    target = tmp_path / f"{configuration_field}.txt"
    target.write_text("before", encoding="utf-8")
    service.watch_generation = 1
    service.configuration = EffectiveWatchConfiguration(**{
        configuration_field: (str(target),),
        "configured_roots": (str(tmp_path),),
        "watch_paths": (str(tmp_path),),
    })
    service.reconcile(reason="configuration", watch_generation=1)

    target.write_text("after-content", encoding="utf-8")
    changed = service.reconcile(reason="fallback", watch_generation=1)

    assert changed is not None
    assert changed["changed_paths"] == [str(target)]
    if changed_flag is not None:
        assert changed[changed_flag] is True
    assert changed["files_changed"] == ([{
        "path": str(target),
        "signature": watchd.filesystem.watch_signature(str(target)),
    }] if expects_file_row else [])


def test_watchd_reconcile_tracks_indexed_directory_changes(tmp_path):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    indexed = tmp_path / "indexed"
    indexed.mkdir()
    service.watch_generation = 1
    service.configuration = EffectiveWatchConfiguration(
        indexed_dirs=(str(indexed),),
        configured_roots=(str(tmp_path),),
        watch_paths=(str(indexed),),
    )
    service.reconcile(reason="configuration", watch_generation=1)

    (indexed / "new.txt").write_text("new", encoding="utf-8")
    changed = service.reconcile(reason="fallback", watch_generation=1)

    assert changed is not None
    assert changed["changed_paths"] == [str(indexed)]


@pytest.mark.parametrize("directory_field", ("roots", "indexed_dirs"))
def test_watchd_coarse_reconcile_projects_ancestor_change_to_nested_repo_generation(tmp_path, directory_field):
    service = PersistentWatchService(tmp_path / "watchd.sock")
    workspace = tmp_path / "workspace"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    service.watch_generation = 1
    service.configuration = EffectiveWatchConfiguration(**{
        directory_field: (str(workspace),),
        "repo_roots": (str(repo),),
        "configured_roots": (str(tmp_path),),
        "watch_paths": (str(workspace),),
    })
    service.reconcile(reason="configuration", watch_generation=1)
    initial_repo_generation = service.repo_generations[str(repo)]

    (workspace / "new.txt").write_text("new", encoding="utf-8")
    changed = service.reconcile(reason="fallback", watch_generation=1)

    assert changed is not None
    assert changed["changed_paths"] == [str(workspace)]
    assert changed["repo_generations"] == {str(repo): initial_repo_generation + 1}


def test_watchd_initial_scan_failure_does_not_activate_polling_generation(tmp_path, monkeypatch):
    service, _root, _signature = _polling_generation_service(tmp_path)

    def fail_scan(*_args, **_kwargs):
        raise OSError("initial scan failed")

    monkeypatch.setattr(watchd, "watchfiles_watch", None)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", fail_scan)
    monkeypatch.setattr(service, "_wait_watch_generation", lambda *_args: "stopped")

    service.native_watch_loop()

    failure = service.revisions[-1]
    assert failure["watch_generation"] == 2
    assert failure["active_watch_generation"] == 1
    assert failure["healthy"] is False and failure["fallback"] is False
    assert service.scanned_watch_generation == 1
    assert service.last_error == "initial scan failed"


def test_watchd_initial_scan_failure_with_native_yield_waits_without_activation(tmp_path, monkeypatch):
    service, root, _signature = _polling_generation_service(tmp_path)
    watcher_calls = 0
    waits = 0

    def fail_scan(*_args, **_kwargs):
        raise OSError("initial native scan failed")

    def native_watch(*paths, **_kwargs):
        nonlocal watcher_calls
        watcher_calls += 1
        assert paths == (root,)
        yield set()

    def stop_after_retry_wait(_generation, _generation_stop_event, _timeout):
        nonlocal waits
        waits += 1
        assert service.scanned_watch_generation != 2
        assert service.active_watch_generation == 1
        assert service.native_healthy is False and service.polling_fallback is False
        return "stopped"

    monkeypatch.setattr(watchd, "watchfiles_watch", native_watch)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", fail_scan)
    monkeypatch.setattr(service, "_wait_watch_generation", stop_after_retry_wait)

    service.native_watch_loop()

    failure = service.revisions[-1]
    assert failure["watch_generation"] == 2 and failure["active_watch_generation"] == 1
    assert failure["healthy"] is False and failure["fallback"] is False
    assert watcher_calls == waits == 1
    assert len(service.revisions) == 2


def test_watchd_initial_scan_failure_from_stale_generation_does_not_wait(tmp_path, monkeypatch):
    service, _root, _signature = _polling_generation_service(tmp_path)
    watcher_calls = 0
    waits = 0

    def fail_after_generation_change(*_args, **_kwargs):
        with service.lock:
            service.watch_generation = 3
        service.stop_event.set()
        raise OSError("stale initial scan failed")

    def native_watch(*_args, **_kwargs):
        nonlocal watcher_calls
        watcher_calls += 1
        yield set()

    def unexpected_wait(_generation, _generation_stop_event, _timeout):
        nonlocal waits
        waits += 1
        return "stopped"

    monkeypatch.setattr(watchd, "watchfiles_watch", native_watch)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", fail_after_generation_change)
    monkeypatch.setattr(service, "_wait_watch_generation", unexpected_wait)

    service.native_watch_loop()

    assert watcher_calls == waits == 0
    assert service.active_watch_generation == 1
    assert service.scanned_watch_generation == 1
    assert len(service.revisions) == 1


def test_watchd_initial_scan_retry_is_interrupted_by_generation_change(tmp_path, monkeypatch):
    service, root, signature = _polling_generation_service(tmp_path)
    scan_failed = threading.Event()
    watcher_closed = threading.Event()
    restarted = threading.Event()
    scan_calls = 0
    watcher_calls = 0

    def fail_first_scan(*_args, **_kwargs):
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls == 1:
            scan_failed.set()
            raise OSError("initial scan failed")
        return signature

    def native_watch(*paths, **_kwargs):
        nonlocal watcher_calls
        watcher_calls += 1
        assert paths == (root,)
        if watcher_calls == 2:
            restarted.set()
            service.stop_event.set()
        try:
            yield set()
        finally:
            watcher_closed.set()

    monkeypatch.setattr(watchd, "watchfiles_watch", native_watch)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", fail_first_scan)

    service.start_watcher()
    try:
        assert scan_failed.wait(1.0)
        assert watcher_closed.wait(1.0)
        with service.lock:
            service.watch_generation += 1
            service.native_stop_event.set()
        assert restarted.wait(1.0)
    finally:
        service.stop_event.set()
        service.native_stop_event.set()
        service.shutdown_watcher()


def test_watchd_native_yield_generation_change_closes_without_wait_or_activation(tmp_path, monkeypatch):
    service, root, signature = _polling_generation_service(tmp_path)
    watcher_closed = False
    waits = 0

    def native_watch(*paths, **_kwargs):
        nonlocal watcher_closed
        assert paths == (root,)
        try:
            with service.lock:
                service.watch_generation = 3
            yield set()
        finally:
            watcher_closed = True
            service.stop_event.set()

    def unexpected_wait(_generation, _generation_stop_event, _timeout):
        nonlocal waits
        waits += 1
        return "stopped"

    monkeypatch.setattr(watchd, "watchfiles_watch", native_watch)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", lambda *_args, **_kwargs: signature)
    monkeypatch.setattr(service, "_wait_watch_generation", unexpected_wait)

    service.native_watch_loop()

    assert watcher_closed is True
    assert waits == 0
    assert service.active_watch_generation == 1
    assert service.scanned_watch_generation == 2
    assert len(service.revisions) == 1


def test_watchd_repeated_polling_scan_failure_withdraws_ready_generation(tmp_path, monkeypatch):
    service, _root, signature = _polling_generation_service(tmp_path)
    calls = 0
    waits = 0

    def fail_after_initial_scan(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return signature
        raise watchd.filesystem.FilesystemError("polling rescan failed")

    def stop_after_failed_reentry(_generation, _generation_stop_event, _timeout):
        nonlocal waits
        waits += 1
        if waits == 1:
            return "elapsed"
        failure = service.revisions[-1]
        assert failure["healthy"] is False and failure["fallback"] is False
        assert failure["active_watch_generation"] == 2
        assert service.scanned_watch_generation == 0
        return "stopped"

    monkeypatch.setattr(watchd, "watchfiles_watch", None)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", fail_after_initial_scan)
    monkeypatch.setattr(service, "_wait_watch_generation", stop_after_failed_reentry)

    service.native_watch_loop()

    activation, failure = service.revisions[-2:]
    assert activation["watch_generation"] == activation["active_watch_generation"] == 2
    assert activation["healthy"] is False and activation["fallback"] is True
    assert failure["watch_generation"] == failure["active_watch_generation"] == 2
    assert failure["healthy"] is False and failure["fallback"] is False
    assert len(service.revisions) == 3
    assert calls == 3 and waits == 2
    assert service.last_error == "polling rescan failed"


def test_watchd_native_periodic_scan_failure_requires_successful_fallback_scan(tmp_path, monkeypatch):
    service, root, signature = _polling_generation_service(tmp_path)
    calls = 0

    def fail_periodic_then_recover(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("native periodic scan failed")
        if calls == 3:
            service.stop_event.set()
        return signature

    def native_watch(*paths, **_kwargs):
        assert paths == (root,)
        service.next_reconcile_at = 0.0
        yield set()

    def continue_to_fallback_scan(_generation, _generation_stop_event, _timeout):
        failure = service.revisions[-1]
        assert failure["healthy"] is False and failure["fallback"] is False
        assert service.scanned_watch_generation == 0
        return "elapsed"

    monkeypatch.setattr(watchd, "watchfiles_watch", native_watch)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", fail_periodic_then_recover)
    monkeypatch.setattr(service, "_wait_watch_generation", continue_to_fallback_scan)

    service.native_watch_loop()

    native_activation, failure, fallback_activation = service.revisions[-3:]
    assert native_activation["healthy"] is True and native_activation["fallback"] is False
    assert failure["healthy"] is False and failure["fallback"] is False
    assert fallback_activation["healthy"] is False and fallback_activation["fallback"] is True
    assert fallback_activation["watch_generation"] == fallback_activation["active_watch_generation"] == 2
    assert service.scanned_watch_generation == 2
    assert calls == 3


def test_watchd_shutdown_watcher_interrupts_periodic_scan_error_retry(tmp_path, monkeypatch):
    service, root, signature = _polling_generation_service(tmp_path)
    scan_failed = threading.Event()
    shutdown_done = threading.Event()
    scan_calls = 0

    def fail_periodic_scan(*_args, **_kwargs):
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls == 2:
            scan_failed.set()
            raise OSError("periodic scan failed")
        return signature

    def native_watch(*paths, **_kwargs):
        assert paths == (root,)
        service.next_reconcile_at = 0.0
        yield set()

    monkeypatch.setattr(watchd, "watchfiles_watch", native_watch)
    monkeypatch.setattr(watchd.filesystem, "watch_signature", fail_periodic_scan)

    service.start_watcher()
    assert scan_failed.wait(1.0)
    shutdown_thread = threading.Thread(
        target=lambda: (service.shutdown_watcher(), shutdown_done.set()),
        daemon=True,
    )
    shutdown_thread.start()
    try:
        assert shutdown_done.wait(1.0)
        assert service.stop_event.is_set() is False
        assert service.native_worker is None
    finally:
        service.stop_event.set()
        service.native_stop_event.set()
        shutdown_thread.join(timeout=1.0)


def test_watchd_descriptor_sync_records_the_daemon_generation(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(
        watchd_lease_id="lease",
        watchd_descriptor_ids={"retired"},
        watchd_synced_generation=4,
        watchd_active_generation=4,
    )
    windows = []

    class Client:
        def upsert(self, lease_id, descriptor_id, descriptor, *, reconfiguring=False):
            assert (lease_id, descriptor_id, descriptor) == ("lease", "active", {"descriptor_generation": 1})
            windows.append(("upsert", reconfiguring))
            return {"ok": True, "watch_generation": 7, "active_watch_generation": 4}

        def remove(self, lease_id, descriptor_id, *, reconfiguring=False):
            assert (lease_id, descriptor_id) == ("lease", "retired")
            windows.append(("remove", reconfiguring))
            return {"ok": True, "watch_generation": 8, "active_watch_generation": 4}

    webapp.watch_client = Client()
    monkeypatch.setattr(webapp, "watchd_descriptor_payloads", lambda: {"active": {"descriptor_generation": 1}})

    assert webapp.sync_watchd_descriptors(record) is True
    assert record.watchd_descriptor_ids == {"active"}
    assert record.watchd_descriptor_generations == {"active": 1}
    assert record.watchd_synced_generation == 8
    assert record.watchd_active_generation == 4
    # The upsert that bumps the generation is answered before the daemon blocks;
    # every request after it is the one that has to cover the rebuild.
    assert windows == [("upsert", False), ("remove", True)]


@pytest.mark.parametrize(
    ("action", "error_code"),
    (("acquire", "unknown_lease"), ("upsert", "stale_generation"), ("remove", "upgrade_required"), ("wait_revision", "deadline_expired")),
)
def test_watchd_failure_preserves_fixed_action_and_error_code(monkeypatch, action, error_code):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    webapp.client_watch_service.event_watcher_record = record
    emitted = []
    now = iter((10.0, 12.1))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(now))
    monkeypatch.setattr(app_module, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))

    webapp.publish_watchd_failure(record, {"ok": False, "error_code": error_code}, action=action)
    webapp.publish_watchd_failure(record, {"ok": False, "error_code": error_code}, action=action)

    assert emitted[0][1]["event"] == f"watchd_{action}_failure"
    assert f"({error_code})" in emitted[0][0][2]


def test_web_starts_watchd_bridge_without_native_or_notify_thread(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    wait_entered = threading.Event()
    release = threading.Event()

    class Client:
        def acquire_lease(self):
            return {"ok": True, "lease_id": "lease", "epoch": "epoch"}

        def upsert(self, *_args):
            return {"ok": True}

        def remove(self, *_args):
            return {"ok": True}

        def wait_revision(self, *_args, **_kwargs):
            wait_entered.set()
            release.wait(1.0)
            return {"ok": True, "changed": False, "epoch": "epoch", "current_revision": 0}

        def release_lease(self, _lease_id):
            release.set()
            return {"ok": True}

    webapp.watch_client = Client()
    monkeypatch.setattr(webapp, "watchd_descriptor_payloads", lambda: {})
    monkeypatch.setattr(webapp, "start_tmux_signal_event_watcher", lambda: True)
    monkeypatch.setattr(webapp, "stop_tmux_signal_event_watcher", lambda: None)
    webapp.start_client_event_watcher()
    try:
        assert wait_entered.wait(1.0)
        thread_names = {thread.name for thread in threading.enumerate()}
        assert "watchd-revision" in thread_names
        assert "native-filesystem-watch" not in thread_names
        assert not any("notify-rs" in name for name in thread_names)
    finally:
        release.set()
        webapp.stop_client_event_watcher()


def test_local_service_record_persists_opaque_watchd_source_epoch(tmp_path):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("watchd", "yolomux_lib.watchd", "watchd.sock", WATCHD_PROTOCOL_VERSION),
    )

    record = registry._record_from_status({
        "pid": 0,
        "version": WATCHD_PROTOCOL_VERSION,
        "started_at": 1.0,
        "source_epoch": "opaque-start-identity",
    })

    assert record["source_epoch"] == "opaque-start-identity"


def test_watchd_client_registry_launches_and_relays_opaque_snapshot(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("watchd", encoding="utf-8")
    client = WatchClient(tmp_path / "services" / "watchd.sock")
    lease = client.acquire_lease()
    try:
        assert lease["ok"] is True
        descriptor = _descriptor(root)
        upsert = client.upsert(str(lease["lease_id"]), "browser", descriptor)
        assert upsert["ok"] is True
        accepted, accepted_body = client.snapshot(force_full=True)
        assert accepted["state"] == "accepted"
        assert accepted_body == b""
        metadata, body = client.snapshot_product(str(accepted["producer_id"]), timeout=1.0)
        assert metadata["ok"] is True, metadata
        assert metadata["product"]["length"] == len(body)
        assert json.loads(body)["directories"][0]["path"] == str(root)
    finally:
        if lease.get("lease_id"):
            client.release_lease(str(lease["lease_id"]))
        client.request(_request("shutdown"), timeout=1.0)


def _pane(session, window="0", pane="0", pane_id="%1", command="bash", pid=100, window_name="win"):
    return TmuxPaneInfo(
        session=session, window=window, pane=pane, pane_id=pane_id,
        target=f"{session}:{window}.{pane}", current_path="/repo", command=command,
        active=True, window_active=True, title="t", pid=pid, window_name=window_name,
    )


def test_watchd_topology_signature_moves_for_every_descriptor_relevant_tmux_change(monkeypatch):
    """The signature gates the transcript rebuild, so anything that can change the transcript set
    must move it. It may not do the expensive work it exists to avoid: one `tmux list-panes -a`
    (0.57ms CPU over 49 panes) and no process table, agent enrichment, or transcript stat."""

    webapp = app_module.TmuxWebtermApp(["alpha", "beta"], status_service_mode=True)
    baseline_panes = [_pane("alpha", pane_id="%1", pid=101), _pane("beta", pane_id="%2", pid=102)]

    def signature_for(panes, sessions=("alpha", "beta")):
        monkeypatch.setattr(app_module, "list_tmux_panes", lambda: (list(panes), None))
        webapp.sessions = list(sessions)
        return webapp.watchd_topology_signature()

    baseline = signature_for(baseline_panes)
    if True:
        assert baseline == signature_for(list(baseline_panes)), "an unchanged topology must be stable"

        cases = {
            "new session": (baseline_panes + [_pane("gamma", pane_id="%9", pid=109)], ("alpha", "beta", "gamma")),
            "new pane in an existing session": (baseline_panes + [_pane("alpha", pane="1", pane_id="%3", pid=103)], ("alpha", "beta")),
            "killed pane": (baseline_panes[:1], ("alpha", "beta")),
            "renamed session": ([_pane("alpha-renamed", pane_id="%1", pid=101), baseline_panes[1]], ("alpha-renamed", "beta")),
            "renamed window": ([_pane("alpha", pane_id="%1", pid=101, window_name="other"), baseline_panes[1]], ("alpha", "beta")),
            "pane foreground command changed": ([_pane("alpha", pane_id="%1", pid=101, command="claude"), baseline_panes[1]], ("alpha", "beta")),
            "pane process replaced": ([_pane("alpha", pane_id="%1", pid=999), baseline_panes[1]], ("alpha", "beta")),
        }
        for label, (panes, sessions) in cases.items():
            assert signature_for(panes, sessions) != baseline, f"{label} must force an immediate resync"

        # tmux unreadable must never pin the last derived set.
        monkeypatch.setattr(app_module, "list_tmux_panes", lambda: ([], "tmux list-panes failed"))
        assert webapp.watchd_topology_signature() is None


def test_watchd_transcript_paths_rebuild_on_topology_change_and_within_the_resync_interval(monkeypatch):
    """The rebuild is the ~26ms discover_sessions the revision loop used to run per revision."""

    webapp = app_module.TmuxWebtermApp(["alpha"], status_service_mode=True)
    panes = [_pane("alpha", pane_id="%1", pid=101)]
    clock = [1000.0]
    rebuilds = []

    def fake_discover(sessions, **kwargs):
        rebuilds.append(tuple(sessions))
        return {}, []

    monkeypatch.setattr(app_module, "list_tmux_panes", lambda: (list(panes), None))
    monkeypatch.setattr(app_module, "discover_sessions", fake_discover)
    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])

    if True:
        webapp.watchd_transcript_paths()
        webapp.watchd_transcript_paths()
        clock[0] += WATCHD_DESCRIPTOR_RESYNC_SECONDS - 0.1
        webapp.watchd_transcript_paths()
        assert len(rebuilds) == 1, "an unchanged topology inside the interval must not rebuild"

        # A topology change is picked up on the very next call, not after the interval.
        panes.append(_pane("alpha", pane="1", pane_id="%2", pid=102))
        webapp.watchd_transcript_paths()
        assert len(rebuilds) == 2

        # The interval is the backstop for changes the signature cannot see.
        clock[0] += WATCHD_DESCRIPTOR_RESYNC_SECONDS
        webapp.watchd_transcript_paths()
        assert len(rebuilds) == 3

        # An unreadable tmux rebuilds every call rather than serving a pinned set.
        monkeypatch.setattr(app_module, "list_tmux_panes", lambda: ([], "tmux list-panes failed"))
        webapp.watchd_transcript_paths()
        webapp.watchd_transcript_paths()
        assert len(rebuilds) == 5

    # A descriptor cannot expire because a resync was served from the memo: sync_watchd_descriptors
    # still upserts every descriptor on every revision, so expires_at is refreshed at the loop's
    # rate, not the memo's. The interval margin is the second line of defence.
    assert WATCHD_DESCRIPTOR_RESYNC_SECONDS * 2 < WATCHD_DESCRIPTOR_TTL_SECONDS
    assert WATCHD_DESCRIPTOR_RESYNC_SECONDS <= WATCHD_DESCRIPTOR_TTL_SECONDS / 6


class _PacingClient:
    """A watchd client whose every call succeeds instantly, so only the floor paces the loop."""

    def __init__(self, on_wait):
        self.on_wait = on_wait

    def acquire_lease(self):
        return {"ok": True, "lease_id": "lease", "pid": 1, "epoch": "e", "watch_generation": 1}

    def upsert(self, lease_id, descriptor_id, descriptor, *, reconfiguring=False):
        del lease_id, descriptor_id, descriptor, reconfiguring
        return {"ok": True, "watch_generation": 1, "active_watch_generation": 1}

    def remove(self, lease_id, descriptor_id, *, reconfiguring=False):
        del lease_id, descriptor_id, reconfiguring
        return {"ok": True, "watch_generation": 1, "active_watch_generation": 1}

    def wait_revision(self, epoch, revision, *, timeout, reconfiguring=False):
        del epoch, revision, timeout, reconfiguring
        self.on_wait()
        return {"ok": True, "changed": False, "revision": {}}

    def release_lease(self, lease_id):
        del lease_id
        return {"ok": True}


def test_watchd_revision_loop_floors_its_period_and_still_exits_without_paying_it(monkeypatch):
    """A revision always being ready made this loop unpaced, and its CPU is body_cpu/period.

    Making the body cheaper cannot fix that — a cheaper body re-arms sooner, so the ratio is
    scale-invariant and stalled at 43% of a core against a 30% budget. Only the floor moves it.
    The floor may not be paid on the way out, so it waits on the stop event rather than sleeping.
    """

    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    clock = [1000.0]
    waits: list[float] = []
    iterations = [0]

    def advance_body():
        # A loop body far cheaper than the floor, which is the case the floor exists for.
        iterations[0] += 1
        clock[0] += 0.003
        if iterations[0] >= 4:
            record.watchd_stop_event.set()

    real_wait = record.watchd_stop_event.wait

    def recording_wait(timeout=None):
        waits.append(timeout)
        if record.watchd_stop_event.is_set():
            return True
        clock[0] += float(timeout or 0.0)
        return False

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(record.watchd_stop_event, "wait", recording_wait)
    monkeypatch.setattr(webapp, "watchd_descriptor_payloads", lambda: {})
    monkeypatch.setattr(webapp, "publish_watchd_recovery", lambda _record: None)
    webapp.watch_client = _PacingClient(advance_body)

    webapp.watchd_revision_loop(record)

    assert iterations[0] == 4
    # The first iteration is not delayed; every later one waits out the rest of the period.
    paced = [w for w in waits if w is not None and w > 0]
    assert len(paced) == 3, waits
    for value in paced:
        assert value == pytest.approx(WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS - 0.003, abs=1e-9)

    # Shutdown does not pay the floor: the stop event short-circuits the wait it is checked on.
    assert record.watchd_stop_event.is_set()
    assert real_wait(0) is True

    # The floor holds the budget for the measured body cost. 3.07ms of CPU is what one iteration
    # of watchd_descriptor_payloads costs after the topology-signature memo.
    saturated_percent = 3.07 / (WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS * 1000.0) * 100.0
    assert saturated_percent < 30.0, f"saturated loop would be {saturated_percent:.1f}% of a core"


def test_watchd_revision_loop_stop_event_breaks_the_floor_immediately(monkeypatch):
    """A stop requested during the floor must not wait it out."""

    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord()
    clock = [500.0]
    iterations = [0]

    def advance_body():
        iterations[0] += 1
        clock[0] += 0.001
        record.watchd_stop_event.set()

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(webapp, "watchd_descriptor_payloads", lambda: {})
    monkeypatch.setattr(webapp, "publish_watchd_recovery", lambda _record: None)
    webapp.watch_client = _PacingClient(advance_body)

    started = time.monotonic()
    webapp.watchd_revision_loop(record)
    elapsed = time.monotonic() - started

    assert iterations[0] == 1
    # A real Event.wait returns the moment the event is set, so the second iteration's floor
    # costs nothing. Wall time here is real, not the fake clock.
    assert elapsed < WATCHD_REVISION_LOOP_MIN_PERIOD_SECONDS, f"shutdown paid {elapsed*1000:.1f}ms of the floor"


# Every default and user-configured ignored directory, with a representative
# nested file.  A change beneath any of these must not reach native admission,
# a reconciliation signature, a generation bump, browser history, a diagnostic
# or an indexing unit, and no ignored pathname may become a transport signal.
IGNORED_DIRECTORY_TABLE = (
    (".git", "HEAD"),
    (".git", "refs/heads/main"),
    (".hg", "store/data.i"),
    (".svn", "wc.db"),
    (".jj", "repo/op_store"),
    (".cache", "pip/http/a/b/c"),
    (".pytest_cache", "v/cache/lastfailed"),
    (".mypy_cache", "3.12/mod.data.json"),
    (".ruff_cache", "content/0123"),
    (".tox", "py312/bin/python"),
    (".venv", "lib/python3.12/site-packages/pkg/__init__.py"),
    ("venv", "bin/activate"),
    ("node_modules", "react/index.js"),
    ("__pycache__", "module.cpython-312.pyc"),
    ("dist", "bundle.min.js"),
    ("build", "lib/artifact.o"),
    (".ssh", "id_ed25519"),
    (".aws", "credentials"),
)


def _ignored_table_configuration(root: Path, skip_dirs: tuple[str, ...]) -> EffectiveWatchConfiguration:
    return EffectiveWatchConfiguration(
        roots=(str(root),),
        watch_paths=(str(root),),
        files=(),
        background_files=(),
        transcripts=(),
        repo_roots=(str(root),),
        indexed_dirs=(),
        skip_dirs=tuple(sorted(skip_dirs)),
        settings_paths=(),
        attention_paths=(),
        configured_roots=(str(root),),
    )


@pytest.mark.parametrize(("ignored_dir", "nested"), IGNORED_DIRECTORY_TABLE)
def test_watchd_never_admits_a_path_beneath_any_ignored_directory(tmp_path, ignored_dir, nested):
    """No ignored directory, including .git, may reach native admission."""

    skip_dirs = tuple(name for name, _nested in IGNORED_DIRECTORY_TABLE)
    configuration = _ignored_table_configuration(tmp_path, skip_dirs)
    service = PersistentWatchService(tmp_path / "watchd.sock")
    candidate = tmp_path / ignored_dir / nested
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("ignored", encoding="utf-8")

    verdict = service._path_verdict(candidate, configuration)

    assert verdict.excluded is True, (candidate, verdict)
    assert verdict.reason_code, verdict
    assert service._path_allowed(candidate, configuration) is False
    # The native filter is the first consumer; an ignored path must be refused
    # there too, not only after the change has already been batched.
    assert service.native_watch_filter(configuration)(Change.modified, str(candidate)) is False


@pytest.mark.parametrize(("ignored_dir", "nested"), IGNORED_DIRECTORY_TABLE)
def test_ignored_directory_change_bumps_no_generation_and_publishes_no_changed_path(tmp_path, ignored_dir, nested):
    """An ignored-only change must not become a revision or a generation bump."""

    skip_dirs = tuple(name for name, _nested in IGNORED_DIRECTORY_TABLE)
    configuration = _ignored_table_configuration(tmp_path, skip_dirs)
    service = PersistentWatchService(tmp_path / "watchd.sock")
    service.configuration = configuration
    service.watch_generation = 1
    candidate = tmp_path / ignored_dir / nested
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("ignored", encoding="utf-8")
    before_roots = dict(service.root_generations)
    before_repos = dict(service.repo_generations)

    revision = service.admit_native_changes({(Change.modified, str(candidate))}, watch_generation=1)

    assert revision is None, revision
    assert service.root_generations == before_roots
    assert service.repo_generations == before_repos


def test_watchd_still_admits_a_working_tree_file_beside_every_ignored_directory(tmp_path):
    """The exclusion owner must not swallow the real, non-ignored signal."""

    skip_dirs = tuple(name for name, _nested in IGNORED_DIRECTORY_TABLE)
    configuration = _ignored_table_configuration(tmp_path, skip_dirs)
    service = PersistentWatchService(tmp_path / "watchd.sock")
    tracked = tmp_path / "src" / "main.py"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("print(1)\n", encoding="utf-8")

    verdict = service._path_verdict(tracked, configuration)

    assert verdict.excluded is False, verdict
    assert service._path_allowed(tracked, configuration) is True
    assert service.native_watch_filter(configuration)(Change.modified, str(tracked)) is True


def test_user_configured_exclusion_is_owned_by_the_same_exclusion_owner(tmp_path):
    """A custom configured ignored directory is excluded exactly like a default one."""

    configuration = _ignored_table_configuration(tmp_path, ("my-private-vault",))
    service = PersistentWatchService(tmp_path / "watchd.sock")
    candidate = tmp_path / "my-private-vault" / "notes" / "secret.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("private", encoding="utf-8")

    verdict = service._path_verdict(candidate, configuration)

    assert verdict.excluded is True
    assert verdict.reason_code == "skip_dir"
    assert verdict.detail == "my-private-vault"
    assert service.native_watch_filter(configuration)(Change.modified, str(candidate)) is False


def test_watchd_and_the_search_index_share_one_exclusion_owner(tmp_path):
    """The daemon and the index must agree on every ignored directory."""

    skip_dirs = {name for name, _nested in IGNORED_DIRECTORY_TABLE}
    configuration = _ignored_table_configuration(tmp_path, tuple(skip_dirs))
    service = PersistentWatchService(tmp_path / "watchd.sock")
    disagreements = []
    for ignored_dir, nested in IGNORED_DIRECTORY_TABLE:
        candidate = tmp_path / ignored_dir / nested
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("ignored", encoding="utf-8")
        daemon_excluded = service._path_verdict(candidate, configuration).excluded
        index_excluded = search_module._index_path_is_excluded(
            tmp_path,
            candidate,
            skip_dirs,
            lambda _path: False,
        )
        if daemon_excluded != index_excluded:
            disagreements.append((str(candidate), daemon_excluded, index_excluded))

    assert disagreements == []


def test_watchd_descriptor_payloads_builds_skip_dirs_from_the_shared_exclusion_owner(monkeypatch):
    """The real method must run, and its skip_dirs must be the shared policy owner's answer.

    Every other test in this file monkeypatches `watchd_descriptor_payloads` away, so deleting the
    private search helper it called went undetected until a repo-wide grep found the orphan. This
    executes it for real and pins it to the one owner.
    """

    configured = {"index_exclude_dir_names": [".git", "vendorcache"], "index_exclude_paths": ["glob:**/generated/**"]}
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"file_explorer": configured}})
    # Matches this file's convention: a status-service app starts no control server to stop.
    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    payloads = webapp.watchd_descriptor_payloads()
    assert isinstance(payloads, dict)
    expected = sorted(exclusions.ExclusionPolicy.from_settings(configured, DEFAULT_INDEX_EXCLUDE_DIR_NAMES).skip_dir_names)
    assert expected == [".git", "vendorcache"], expected
    for descriptor in payloads.values():
        if "skip_dirs" in descriptor:
            assert sorted(descriptor["skip_dirs"]) == expected, descriptor["skip_dirs"]


def _apply_shared_daemon_revision_under_policy(tmp_path, monkeypatch, *, changed_under):
    """Drive a NARROW server's revision consumer against the shared-daemon union.

    watchd is a per-user daemon keyed on YOLOMUX_ROOT, so its `wait_revision` returns the
    caller-independent UNION of every co-tenant server's leased roots.  This helper co-tenants a
    broad and a narrow root in ONE daemon, publishes a change under ``changed_under`` (``"broad"``,
    ``"narrow"``, or ``"broad_only_no_narrow"``), then applies the resulting revision as the narrow
    server -- whose own filesystem authorization boundary is ONLY the narrow root.
    """

    monkeypatch.setattr("yolomux_lib.watchd.process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr("yolomux_lib.watchd.pid_is_alive", lambda pid: True)

    narrow = tmp_path / "narrow_root"
    broad = tmp_path / "broad_root"
    narrow.mkdir()
    broad.mkdir()
    secret = broad / "SECRET.txt"
    secret.write_text("BROAD-ONLY SECRET\n", encoding="utf-8")
    narrow_file = narrow / "own.txt"
    narrow_file.write_text("narrow change\n", encoding="utf-8")
    narrow_s = str(narrow.resolve())
    broad_s = str(broad.resolve())

    service = PersistentWatchService(tmp_path / "watchd.sock")
    lease_narrow = _lease(service, 1001)
    service.handle(_request("upsert", lease_id=lease_narrow, descriptor_id="browser", descriptor=_descriptor(narrow.resolve())))
    if changed_under != "broad_only_no_narrow":
        # Both tenants share the daemon, so the union carries the broad root even for the narrow waiter.
        lease_broad = _lease(service, 1002)
        service.handle(_request("upsert", lease_id=lease_broad, descriptor_id="browser", descriptor=_descriptor(broad.resolve())))
    else:
        # The narrow tenant has released; the union now carries ONLY another tenant's broad root.
        service.handle(_request("release", lease_id=lease_narrow))
        lease_broad = _lease(service, 1002)
        service.handle(_request("upsert", lease_id=lease_broad, descriptor_id="browser", descriptor=_descriptor(broad.resolve())))
    service.native_healthy = True
    service.active_watch_generation = service.watch_generation

    changed_path = str((narrow_file if changed_under == "narrow" else secret).resolve())
    service.publish_revision(kind="delta", changed_paths=[changed_path])
    response, _body = service.handle(_request("wait_revision", epoch=service.epoch, after_revision=0, timeout_seconds=0.0))
    response = json.loads(json.dumps(response))

    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(
        watchd_epoch=service.epoch,
        filesystem_roots=(narrow_s,),
        watchd_revision=0,
    )
    webapp.client_watch_service.event_watcher_record = record
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event, payload, **kwargs: published.append((event, payload)))
    monkeypatch.setattr(webapp, "mark_indexed_repo_discovery_dirty", lambda _paths: None)
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda **_kwargs: [])

    # The narrow server process is authorized ONLY for its narrow root; bind that policy for the
    # duration of the consume, exactly as the real narrow server's environment would.
    policy = FilesystemAccessPolicy(version=FS_ACCESS_POLICY_VERSION, roots=(narrow_s,))
    with filesystem.enforce_access_policy(policy):
        events = webapp.apply_watchd_revision(record, response["revision"], reset=response["reset"])

    return {
        "webapp": webapp,
        "record": record,
        "events": events,
        "published": published,
        "narrow_s": narrow_s,
        "broad_s": broad_s,
    }


def test_apply_watchd_revision_never_discloses_a_co_tenant_root_to_a_narrow_browser(tmp_path, monkeypatch):
    """LEAK regression: a narrow server's browser must not receive a broad co-tenant's root/path.

    The shared daemon returns the union ``roots=[broad, narrow]`` with ``changed_paths=[broad/SECRET]``
    to the narrow waiter.  Before scoping, ``apply_watchd_revision`` fanned an ``fs_changed`` SSE
    carrying the broad root and the broad change path to the narrow browser, and wrote the broad
    path into ``filesystem_history`` -- a policy disclosure even though S0 later refuses the content
    fetch with 403.
    """
    result = _apply_shared_daemon_revision_under_policy(tmp_path, monkeypatch, changed_under="broad")
    broad_s = result["broad_s"]
    fs_events = [payload for event, payload in result["published"] if event == "fs_changed"]
    disclosed_roots = [root for payload in fs_events for root in payload.get("roots", [])]
    assert broad_s not in disclosed_roots, f"narrow browser received broad co-tenant root: {disclosed_roots}"
    history = result["webapp"].client_watch_service.filesystem_history
    disclosed_paths = [path for entry in history for path in entry.get("changed_paths", ())]
    assert all("broad_root" not in path for path in disclosed_paths), f"filesystem_history disclosed broad path: {disclosed_paths}"
    assert result["record"].filesystem_roots == (result["narrow_s"],), result["record"].filesystem_roots


def test_apply_watchd_revision_still_delivers_a_change_under_the_servers_own_root(tmp_path, monkeypatch):
    """Positive control: scoping must NOT silence a change under the server's OWN authorized root."""
    result = _apply_shared_daemon_revision_under_policy(tmp_path, monkeypatch, changed_under="narrow")
    fs_events = [payload for event, payload in result["published"] if event == "fs_changed"]
    assert fs_events, f"own-root change produced no fs_changed: {[e for e, _ in result['published']]}"
    disclosed_roots = [root for payload in fs_events for root in payload.get("roots", [])]
    assert result["narrow_s"] in disclosed_roots, disclosed_roots
    assert result["broad_s"] not in disclosed_roots, disclosed_roots


def test_apply_watchd_revision_empty_intersection_publishes_nothing_and_does_not_wedge(tmp_path, monkeypatch):
    """Empty-intersection control: a revision touching only other tenants' roots is silent, not an error.

    No ``fs_changed`` fans out, no ``filesystem_history`` entry is written for this server, and the
    revision loop still advances the record without raising.
    """
    result = _apply_shared_daemon_revision_under_policy(tmp_path, monkeypatch, changed_under="broad_only_no_narrow")
    assert [event for event, _ in result["published"] if event == "fs_changed"] == []
    assert result["webapp"].client_watch_service.filesystem_history == []
    # The record still advanced past the applied revision, so the loop is not wedged.
    assert result["record"].watchd_revision >= 1


def test_apply_watchd_revision_broad_parent_coarse_reset_delivers_only_the_narrow_child_root(tmp_path, monkeypatch):
    """The normal broad-daemon shape: the daemon root is an ANCESTOR of the narrow server's root.

    A co-tenant watches ``/parent`` and a coarse reset reports ``changed_paths=[/parent]``; the
    narrow server authorizes only ``/parent/project``.  One-directional descendant filtering drops
    ``/parent`` (it is not beneath ``/parent/project``) and silently loses a real change to the
    server's OWN tree.  Two-direction intersection must instead SUBSTITUTE the authorized child so
    the change is still delivered -- carrying ONLY the narrow child root, never the broad ancestor.
    """

    parent = tmp_path / "workspace"
    child = parent / "project"
    child.mkdir(parents=True)
    parent_s = str(parent.resolve())
    child_s = str(child.resolve())

    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(watchd_epoch="e1", filesystem_roots=(child_s,), watchd_revision=0)
    webapp.client_watch_service.event_watcher_record = record
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event, payload, **kwargs: published.append((event, payload)))
    monkeypatch.setattr(webapp, "mark_indexed_repo_discovery_dirty", lambda _paths: None)
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda **_kwargs: [])

    # Daemon UNION carries only the broad ancestor; a coarse reset reports the ancestor path itself.
    revision = {
        "epoch": "e1",
        "revision": 1,
        "healthy": True,
        "coarse": True,
        "roots": [parent_s],
        "root_generations": {parent_s: 3},
        "repo_generations": {parent_s: 3, child_s: 5},
        "changed_paths": [parent_s],
        "files_changed": [],
        "token": "e1:1",
    }
    policy = FilesystemAccessPolicy(version=FS_ACCESS_POLICY_VERSION, roots=(child_s,))
    with filesystem.enforce_access_policy(policy):
        events = webapp.apply_watchd_revision(record, revision, reset=True)

    assert "fs_changed" in events, f"broad-parent coarse reset was silently lost: {events}"
    fs_events = [payload for event, payload in published if event == "fs_changed"]
    assert fs_events, [event for event, _ in published]
    disclosed_roots = fs_events[0]["roots"]
    assert child_s in disclosed_roots, disclosed_roots
    assert parent_s not in disclosed_roots, disclosed_roots
    # The record and history mirror only the narrow child, never the broad ancestor.
    assert record.filesystem_roots == (child_s,), record.filesystem_roots
    history_paths = list(webapp.client_watch_service.filesystem_history[-1]["changed_paths"])
    assert parent_s not in history_paths, history_paths
    assert all(path == child_s or path.startswith(child_s + "/") for path in history_paths), history_paths
    # The daemon's repo generations are re-keyed onto the authorized child; the broad ancestor key
    # is never stored.  Both the ancestor (3) and the child (5) project onto the child and compose
    # losslessly, so the stored counter is their sum, not a lossy max.
    stored_repos = webapp.client_watch_service.watchd_repo_generations
    assert parent_s not in stored_repos, stored_repos
    assert stored_repos.get(child_s) == 8, stored_repos


def test_apply_watchd_revision_child_repo_generation_increment_is_not_masked_by_a_co_tenant_parent(tmp_path, monkeypatch):
    """A child .git bump must invalidate even when an ancestor co-tenant counter dominates.

    The daemon reports a repo generation for the broad parent AND for the narrow child; both project
    onto the authorized child.  Composing them with ``max`` lets the higher parent counter mask the
    child's own increment across revisions, so a real repository change to the server's OWN tree
    never refreshes.  The composition must be lossless: the child's 5 -> 6 increment between two
    revisions must change the scoped repository signal and invalidate the child, while the broad
    parent key and the raw ``.git`` path are never stored or published.
    """

    parent = tmp_path / "workspace"
    child = parent / "project"
    child.mkdir(parents=True)
    parent_s = str(parent.resolve())
    child_s = str(child.resolve())
    raw_git = str((parent / ".git" / "index").resolve())

    webapp = app_module.TmuxWebtermApp([], status_service_mode=True)
    record = ClientEventWatcherRecord(watchd_epoch="e1", filesystem_roots=(child_s,), watchd_revision=0)
    webapp.client_watch_service.event_watcher_record = record
    # The child repo must be a known dirty-generation key for the invalidation to be observable.
    webapp.session_files_service.repo_dirty_generations[child_s] = 0
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event, payload, **kwargs: published.append((event, payload)))
    monkeypatch.setattr(webapp, "mark_indexed_repo_discovery_dirty", lambda _paths: None)
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda **_kwargs: [])

    def _revision(number, child_generation):
        # The parent counter is HIGHER and STAYS FIXED; only the child increments.  Under `max` the
        # parent's 100 dominates and the child increment vanishes; the raw `.git` path rides along in
        # changed_paths and must be filtered out (it is outside the authorized child).
        return {
            "epoch": "e1",
            "revision": number,
            "healthy": True,
            "roots": [parent_s, child_s],
            "repo_generations": {parent_s: 100, child_s: child_generation},
            "changed_paths": [raw_git],
            "files_changed": [],
            "token": f"e1:{number}",
        }

    policy = FilesystemAccessPolicy(version=FS_ACCESS_POLICY_VERSION, roots=(child_s,))
    with filesystem.enforce_access_policy(policy):
        webapp.apply_watchd_revision(record, _revision(1, 5), reset=True)
        first_stored = dict(webapp.client_watch_service.watchd_repo_generations)
        first_dirty = webapp.session_files_service.repo_dirty_generations[child_s]
        webapp.apply_watchd_revision(record, _revision(2, 6), reset=False)

    stored = webapp.client_watch_service.watchd_repo_generations
    # The child's 5 -> 6 increment MUST move the scoped repository signal (revision 1 vs revision 2).
    assert stored.get(child_s) != first_stored.get(child_s), (first_stored, stored)
    # ... and must invalidate the child a SECOND time, not stay masked at the revision-1 count.
    assert webapp.session_files_service.repo_dirty_generations[child_s] > first_dirty, (
        first_dirty,
        webapp.session_files_service.repo_dirty_generations[child_s],
    )
    # The broad parent key is never stored, and the raw .git path is never mirrored or published.
    assert parent_s not in stored, stored
    for entry in webapp.client_watch_service.filesystem_history:
        assert raw_git not in entry.get("changed_paths", ()), entry
        assert all(".git" not in path for path in entry.get("changed_paths", ())), entry
