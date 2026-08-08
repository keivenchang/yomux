# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Host-partition regressions for state that must never roam through shared home."""

from yolomux_lib import app as app_module
from yolomux_lib.infra import common
from yolomux_lib.infra import host_partition
from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib.workspace import session_files
from yolomux_lib.yoagent import conversation


def _host(stable_host_id: str) -> HostIdentity:
    return HostIdentity(
        stable_host_id,
        f"{stable_host_id}.example",
        "boot-a",
        1000,
        "proc:1",
        1,
        "nonce-a",
        "fixture",
    )


def _for_host(monkeypatch, identity: HostIdentity, path_factory):
    monkeypatch.setattr(host_partition, "current_host_identity", lambda: identity)
    return path_factory()


def test_event_and_run_history_are_not_observed_by_another_host(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    host_a = _host("host-a")
    host_b = _host("host-b")
    state_dir.mkdir()
    (state_dir / f"events-v{common.PERSISTENT_STATE_GENERATION}.jsonl").write_text("legacy-event\n", encoding="utf-8")
    (state_dir / "run-history.json").write_text('{"host":"legacy"}\n', encoding="utf-8")
    event_a, run_a = _for_host(monkeypatch, host_a, lambda: (
        common.event_log_path(state_dir), common.run_history_path(state_dir),
    ))
    assert not event_a.exists() and not run_a.exists()
    event_a.parent.mkdir(parents=True)
    event_a.write_text("host-a-event\n", encoding="utf-8")
    run_a.write_text('{"host":"a"}\n', encoding="utf-8")
    event_b, run_b = _for_host(monkeypatch, host_b, lambda: (
        common.event_log_path(state_dir), common.run_history_path(state_dir),
    ))

    assert event_a != event_b and run_a != run_b
    assert not event_b.exists() and not run_b.exists()
    assert event_a.read_text(encoding="utf-8") == "host-a-event\n"


def test_yoagent_conversations_are_not_observed_by_another_host(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    host_a = _host("host-a")
    host_b = _host("host-b")
    legacy = state_dir / "yoagent"
    legacy.mkdir(parents=True)
    (legacy / "conversation.jsonl").write_text("legacy\n", encoding="utf-8")
    a = _for_host(monkeypatch, host_a, lambda: conversation.default_yoagent_state_dir(state_dir))
    assert not (a / "conversation.jsonl").exists()
    a.mkdir(parents=True)
    (a / "conversation.jsonl").write_text("host-a\n", encoding="utf-8")
    b = _for_host(monkeypatch, host_b, lambda: conversation.default_yoagent_state_dir(state_dir))

    assert a != b
    assert not (b / "conversation.jsonl").exists()
    assert (a / "conversation.jsonl").read_text(encoding="utf-8") == "host-a\n"


def test_repository_snapshot_cache_is_not_observed_by_another_host(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(session_files.common, "STATE_DIR", state_dir)
    legacy = state_dir / session_files._REPOSITORY_SNAPSHOT_CACHE_DIRNAME
    legacy.mkdir(parents=True)
    (legacy / "legacy.json").write_text('{"host":"legacy"}\n', encoding="utf-8")
    host_a = _host("host-a")
    host_b = _host("host-b")
    a = _for_host(monkeypatch, host_a, lambda: session_files.repository_snapshot_cache_path(repo, "main", "HEAD", 1))
    assert not a.exists()
    a.parent.mkdir(parents=True)
    a.write_text('{"host":"a"}\n', encoding="utf-8")
    b = _for_host(monkeypatch, host_b, lambda: session_files.repository_snapshot_cache_path(repo, "main", "HEAD", 1))

    assert a != b
    assert not b.exists()
    assert a.read_text(encoding="utf-8") == '{"host":"a"}\n'


def test_app_caches_stay_host_local_but_share_one_host_leader_follower_state(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    host_a = _host("host-a")
    host_b = _host("host-b")
    for directory in ("session-files-cache", "activity-cache", "background-owner"):
        (state_dir / directory).mkdir(parents=True, exist_ok=True)
    (state_dir / "background-owner" / "client-events.json").write_text('{"events":["legacy"]}\n', encoding="utf-8")
    a = _for_host(monkeypatch, host_a, lambda: (
        app_module.default_session_files_cache_dir(state_dir),
        app_module.default_tabber_activity_cache_dir(state_dir),
        app_module.default_background_client_events_path(state_dir),
    ))
    assert not a[0].exists() and not a[1].exists() and not a[2].exists()
    a[2].parent.mkdir(parents=True)
    a[2].write_text('{"events":["host-a"]}\n', encoding="utf-8")
    same_host = _for_host(monkeypatch, host_a, lambda: app_module.default_background_client_events_path(state_dir))
    b = _for_host(monkeypatch, host_b, lambda: (
        app_module.default_session_files_cache_dir(state_dir),
        app_module.default_tabber_activity_cache_dir(state_dir),
        app_module.default_background_client_events_path(state_dir),
    ))

    assert a[0] != b[0] and a[1] != b[1] and a[2] != b[2]
    assert same_host == a[2]
    assert not b[2].exists()
    assert a[2].read_text(encoding="utf-8") == '{"events":["host-a"]}\n'
