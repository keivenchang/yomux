"""Cross-server attention acknowledgement sync."""

import json
import time
from http import HTTPStatus
from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module
from yolomux_lib import common
from yolomux_lib.app import ATTENTION_ACK_TTL_SECONDS


@pytest.fixture
def make_app(monkeypatch):
    created = []

    def factory():
        monkeypatch.setattr(app_module.TmuxWebtermApp, "warm_start_session_files_payload_cache", lambda self: None)
        app = app_module.TmuxWebtermApp(["1"])
        created.append(app)
        monkeypatch.setattr(app, "notify_background_client_event_followers", lambda *args, **kwargs: None)
        monkeypatch.setattr(app.background_owner, "live_generation_records", lambda: [])
        return app

    yield factory

    for app in created:
        app.background_owner.stop()
        app.control_server.stop()


def patch_shared_path(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "TMUX_AI_STATUS_PATH", tmp_path / "tmux-AI-status.json")
    monkeypatch.setattr(common, "LEGACY_ATTENTION_ACKS_PATH", tmp_path / "attention-acks.json")


def test_attention_ack_visible_to_second_instance(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    key = first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "sig-X")

    result, status = first.acknowledge_attention({"keys": [key]})

    assert status == HTTPStatus.OK
    assert result["ok"] is True
    data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert key in data["attention_acks"]["keys"]
    assert second.merge_shared_attention_acks() is True
    assert second.attention_acknowledged(key) is True


def test_attention_ack_revision_invalidates_tabber_activity_cache_key(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    monkeypatch.setattr(app, "activity_session_names", lambda _scope: (["1"], [], "configured"))
    monkeypatch.setattr(app_module, "discover_sessions", lambda _sessions: ({}, []))
    before = app.tabber_activity_source_signature()
    key = app.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "tabber-cache")

    app.acknowledge_attention({"keys": [key]})

    assert app.tabber_activity_source_signature() != before


def test_auto_approve_read_merges_peer_ack_without_event_subscriber(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    key = first.attention_ack_key("prompt", "1", "cross-port-read")
    first.acknowledge_attention({"keys": [key]})

    monkeypatch.setattr(second, "refresh_sessions", lambda maintenance=False: [])
    monkeypatch.setattr(second, "sync_auto_approve_agent_workers", lambda takeover=False: None)
    monkeypatch.setattr(second, "activity_snapshot_with_recency", lambda snapshot=None: {})
    monkeypatch.setattr(second, "prompt_and_screen_status", lambda *args, **kwargs: (
        {"visible": True, "signature": "cross-port-read", "text": "Needs input"},
        {"key": "needs-input", "text": "Needs input"},
    ))
    monkeypatch.setattr(second, "agent_window_status_payloads", lambda *args, **kwargs: [])

    payload, status = second.auto_approve_status("1")

    assert status == HTTPStatus.OK
    assert payload["prompt_attention_key"] == key
    assert payload["prompt_attention_acknowledged"] is True
    assert "attention_acks" not in payload
    assert payload["attention_ack_revision"] >= 1


def test_auto_approve_change_invalidates_local_cache_and_pushes_to_peer_without_poll(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "BACKGROUND_CLIENT_EVENTS_PATH", tmp_path / "background-client-events.json")
    first = make_app()
    second = make_app()
    _subscriber_id, subscriber_queue = second.client_events.subscribe("status", "browser-b")
    first.approval_client = SimpleNamespace(
        status_session=lambda session: [{"target": "%1", "enabled": True}] if session == "1" else [],
        stop_session=lambda session: {"ok": session == "1"},
    )
    first.auto_approve_cache_record.payload = (time.monotonic(), ({"sessions": {"1": {"enabled": True}}}, HTTPStatus.OK))
    second.auto_approve_cache_record.payload = (time.monotonic(), ({"sessions": {"1": {"enabled": True}}}, HTTPStatus.OK))
    monkeypatch.setattr(first, "auto_approve_session_status", lambda session: {"session": session, "enabled": False})
    monkeypatch.setattr(first, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        first,
        "notify_background_client_event_followers",
        lambda event_type, payload, _shared_event: second.handle_background_client_event({"event_type": event_type, "payload": payload}),
    )

    payload, status = first.set_auto_approve("1", False, persist=False)

    assert status == HTTPStatus.OK
    assert payload["enabled"] is False
    assert first.auto_approve_cache_record.payload is None
    assert second.auto_approve_cache_record.payload is None
    assert subscriber_queue.get_nowait()["type"] == "auto_approve_changed"


def test_shared_state_policy_pushes_settings_and_yoagent_conversation_to_peer(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "BACKGROUND_CLIENT_EVENTS_PATH", tmp_path / "background-client-events.json")
    first = make_app()
    second = make_app()
    _subscriber_id, subscriber_queue = second.client_events.subscribe("core,yoagent", "browser-b")
    monkeypatch.setattr(
        first,
        "notify_background_client_event_followers",
        lambda event_type, payload, _shared_event: second.handle_background_client_event({"event_type": event_type, "payload": payload}),
    )

    first.publish_background_client_event("settings_changed", {"mtime_ns": 7}, trigger="test")
    first.publish_yoagent_conversation_changed("test")

    assert [subscriber_queue.get_nowait()["type"] for _ in range(2)] == ["settings_changed", "yoagent_conversation_changed"]


def test_auto_approve_roster_read_discards_cache_from_before_peer_ack(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    key = first.attention_ack_key("prompt", "1", "cross-port-cached-read")

    monkeypatch.setattr(second, "refresh_sessions", lambda maintenance=False: [])
    monkeypatch.setattr(second, "sync_auto_approve_agent_workers", lambda takeover=False: None)
    monkeypatch.setattr(second, "activity_snapshot_with_recency", lambda snapshot=None: {})
    monkeypatch.setattr(second, "prompt_and_screen_status", lambda *args, **kwargs: (
        {"visible": True, "signature": "cross-port-cached-read", "text": "Needs input"},
        {"key": "needs-input", "text": "Needs input"},
    ))
    monkeypatch.setattr(second, "agent_window_status_payloads", lambda *args, **kwargs: [])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))

    before, before_status = second.auto_approve_status()
    first.acknowledge_attention({"keys": [key]})
    after, after_status = second.auto_approve_status()

    assert before_status == after_status == HTTPStatus.OK
    assert before["sessions"]["1"]["prompt_attention_acknowledged"] is False
    assert after["sessions"]["1"]["prompt_attention_acknowledged"] is True
    assert "attention_acks" not in after["sessions"]["1"]
    assert after["sessions"]["1"]["attention_ack_revision"] >= 1


def test_attention_ack_union_does_not_clobber_peer_keys(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    first_key = first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "K1")
    second_key = second.attention_ack_key("agent-window", "1", "0", "%2", "codex", "approval", "K2")

    now = time.time()
    first.write_shared_attention_acks_union({first_key: now})
    second.write_shared_attention_acks_union({second_key: now})

    data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert first_key in data["attention_acks"]["keys"]
    assert second_key in data["attention_acks"]["keys"]
    first.merge_shared_attention_acks()
    second.merge_shared_attention_acks()
    assert first.attention_acknowledged(first_key) is True
    assert first.attention_acknowledged(second_key) is True
    assert second.attention_acknowledged(first_key) is True
    assert second.attention_acknowledged(second_key) is True


def test_attention_ack_persists_across_new_instance(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    key = first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "persist")
    first.acknowledge_attention({"keys": [key]})

    restarted = make_app()

    assert restarted.merge_shared_attention_acks() is True
    assert restarted.attention_acknowledged(key) is True


def test_attention_instance_generation_is_shared_across_servers_and_refresh(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    args = ("1", "0", "%1", "claude", "approval", "same-visible-prompt")

    first_event = first.shared_agent_window_attention_instance_signature(*args)
    second_event = second.shared_agent_window_attention_instance_signature(*args)
    key = first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", first_event)
    first.acknowledge_attention({"keys": [key]})
    second.merge_shared_attention_acks()
    restarted = make_app()
    restarted.merge_shared_attention_acks()

    assert first_event == second_event == restarted.shared_agent_window_attention_instance_signature(*args) == "same-visible-prompt:1"
    assert restarted.attention_acknowledged(key) is True
    assert restarted.attention_acknowledged_at(key) is not None

    first.shared_agent_window_attention_instance_signature("1", "0", "%1", "claude", "idle", "")
    next_event = restarted.shared_agent_window_attention_instance_signature(*args)

    assert next_event == "same-visible-prompt:2"
    assert restarted.attention_acknowledged(first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", next_event)) is False


def test_cooldown_ack_uses_one_shared_transition_identity_across_servers(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    scope = ("8002", "1", "%27", "codex")

    first.agent_window_working_stopped_ts(*scope, "working", 100.0)
    second.agent_window_working_stopped_ts(*scope, "working", 110.0)
    assert first.agent_window_working_stopped_ts(*scope, "idle", 200.0) == 0.0
    first_stopped = first.agent_window_working_stopped_ts(*scope, "idle", 205.0)
    first_key = first.agent_window_attention_key(
        *scope, "cooldown", first.agent_window_attention_signature("cooldown", {}, first_stopped)
    )

    first.acknowledge_attention({"keys": [first_key]})
    second.merge_shared_attention_acks()
    # The peer can observe idle after the click. It must still recover the first server's
    # canonical transition instead of minting a new timestamp/key after the acknowledgement.
    second_stopped = second.agent_window_working_stopped_ts(*scope, "idle", 220.0)
    second_key = second.agent_window_attention_key(
        *scope, "cooldown", second.agent_window_attention_signature("cooldown", {}, second_stopped)
    )

    assert first_stopped == second_stopped
    assert first_key == second_key
    assert second.attention_acknowledged(second_key) is True

    first.agent_window_working_stopped_ts(*scope, "working", 300.0)
    assert first.agent_window_working_stopped_ts(*scope, "idle", 400.0) == 0.0
    next_stopped = first.agent_window_working_stopped_ts(*scope, "idle", 405.0)
    next_key = first.agent_window_attention_key(
        *scope, "cooldown", first.agent_window_attention_signature("cooldown", {}, next_stopped)
    )

    assert next_stopped != first_stopped
    assert next_key != first_key
    assert first.attention_acknowledged(next_key) is False


def test_fresh_server_hydrates_shared_cooldown_transition(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    owner = make_app()
    follower = make_app()
    scope = ("7773", "1", "%27", "claude")

    owner.agent_window_working_stopped_ts(*scope, "working", 100.0)
    assert owner.agent_window_working_stopped_ts(*scope, "idle", 200.0) == 0.0
    owner_stopped = owner.agent_window_working_stopped_ts(*scope, "idle", 205.0)
    follower_stopped = follower.agent_window_working_stopped_ts(*scope, "idle", 220.0)

    assert owner_stopped == follower_stopped == 200.0


def test_working_idle_candidate_requires_five_continuous_seconds(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    scope = ("7772", "1", "%27", "codex")

    assert app.agent_window_working_stopped_ts(*scope, "working", 100.0) == 0.0
    assert app.agent_window_working_stopped_ts(*scope, "idle", 200.0, return_pending=True) == (0.0, True)
    assert app.agent_window_working_stopped_ts(*scope, "working", 203.0, return_pending=True) == (0.0, False)
    assert app.agent_window_working_stopped_ts(*scope, "idle", 204.0, return_pending=True) == (0.0, True)
    assert app.agent_window_working_stopped_ts(*scope, "idle", 208.9, return_pending=True) == (0.0, True)
    assert app.agent_window_working_stopped_ts(*scope, "idle", 209.0, return_pending=True) == (204.0, False)


def test_fresh_follower_status_payload_preserves_shared_cooldown_ack(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    owner = make_app()
    follower = make_app()
    scope = ("1", "0", "%27", "claude")
    owner.agent_window_working_stopped_ts(*scope, "working", 100.0)
    assert owner.agent_window_working_stopped_ts(*scope, "idle", 200.0) == 0.0
    stopped_at = owner.agent_window_working_stopped_ts(*scope, "idle", 205.0)
    key = owner.agent_window_attention_key(
        *scope, "cooldown", owner.agent_window_attention_signature("cooldown", {}, stopped_at)
    )
    follower.acknowledge_attention({"keys": [key]})
    owner.merge_shared_attention_acks()
    follower.merge_shared_attention_acks()
    pane = common.PaneInfo("1", "0", "0", "%27", "%27", "/repo", "claude", True, True, "claude", 27, "claude", 27)
    info = common.SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[common.AgentInfo("1", "claude", 27, "%27", "claude", "/repo", "idle", "claude-1", None, None)],
    )
    monkeypatch.setattr(follower, "agent_window_screen_state", lambda *_args, **_kwargs: {"key": "idle", "text": "done"})

    row = follower.agent_window_status_payloads("1", info=info, discovered_sessions={"1": info}, include_path_metadata=False)[0]

    assert row["working_stopped_ts"] == stopped_at
    assert row["cooldown_attention_key"] == key
    assert row["cooldown_acknowledged"] is True
    assert owner.attention_acknowledged(key) is True


def test_agent_window_status_batch_reads_shared_instances_once(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    webapp = make_app()
    panes = [
        common.PaneInfo("1", str(index), "0", f"%{index}", f"%{index}", f"/repo/{index}", kind, True, index == 0, kind, index + 1, kind, index + 1)
        for index, kind in enumerate(("claude", "codex", "claude"))
    ]
    info = common.SessionInfo(
        session="1",
        panes=panes,
        selected_pane=panes[0],
        agents=[
            common.AgentInfo("1", kind, panes[index].pid, panes[index].target, kind, panes[index].current_path, "idle", f"{kind}-{index}", None, None)
            for index, kind in enumerate(("claude", "codex", "claude"))
        ],
    )
    monkeypatch.setattr(webapp, "agent_window_screen_state", lambda *_args, **_kwargs: {"key": "idle", "text": ""})
    reads = []
    read_shared = webapp._read_shared_tmux_ai_status_locked

    def counted_read():
        reads.append(True)
        return read_shared()

    monkeypatch.setattr(webapp, "_read_shared_tmux_ai_status_locked", counted_read)

    rows = webapp.agent_window_status_payloads("1", info=info, discovered_sessions={"1": info}, include_path_metadata=False)

    assert len(rows) == 3
    assert len(reads) == 1


def test_attention_ack_poll_publishes_once_per_revision(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    key = first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "poll")
    first.acknowledge_attention({"keys": [key]})
    published = []
    monkeypatch.setattr(second, "publish_client_event", lambda *args, **kwargs: published.append(args) or {})

    assert second.poll_attention_acks_client_event_once() == ["attention_acks_changed"]
    assert second.poll_attention_acks_client_event_once() == []
    assert len(published) == 1
    assert published[0][0] == "attention_acks_changed"
    assert published[0][1]["acknowledged"] == [key]


def test_attention_ack_background_event_publishes_scoped_key_patch(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    key = first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "fanout")
    first.acknowledge_attention({"keys": [key]})
    published = []
    monkeypatch.setattr(second, "publish_client_event", lambda *args, **kwargs: published.append((args, kwargs)) or {"id": 1})

    result = second.handle_background_client_event({"event_type": "attention_acks_changed", "payload": {"acknowledged": [key]}})

    assert result["ok"] is True
    assert result["accepted"] is True
    assert second.attention_acknowledged(key) is True
    assert published[0][0][0] == "attention_acks_changed"
    assert published[0][0][1]["acknowledged"] == [key]
    assert published[0][1]["trigger"] == "background-fanout"


def test_attention_ack_background_event_includes_missed_revision_keys(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    missed_key = first.attention_ack_key("agent-window", "8001", "0", "%1", "claude", "approval", "missed")
    event_key = first.attention_ack_key("agent-window", "8002", "1", "%2", "codex", "approval", "event")
    first.acknowledge_attention({"keys": [missed_key]})
    first.acknowledge_attention({"keys": [event_key]})
    published = []
    monkeypatch.setattr(second, "publish_client_event", lambda *args, **kwargs: published.append((args, kwargs)) or {"id": 1})

    result = second.handle_background_client_event({"event_type": "attention_acks_changed", "payload": {"acknowledged": [event_key]}})

    assert result["accepted"] is True
    assert published[0][0][0] == "attention_acks_changed"
    assert published[0][0][1]["acknowledged"] == [missed_key, event_key]


def test_duplicate_attention_ack_is_noop_and_preserves_first_timestamp(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    key = app.attention_ack_key("agent-window", "8002", "1", "%27", "codex", "needs-input", "same-event")
    published = []
    notified = []
    invalidated = []
    monkeypatch.setattr(app, "publish_client_event", lambda *args, **kwargs: published.append((args, kwargs)) or {})
    monkeypatch.setattr(app, "notify_background_client_event_followers", lambda *args, **kwargs: notified.append((args, kwargs)))
    monkeypatch.setattr(app, "invalidate_auto_approve_cache", lambda: invalidated.append(True))

    first, first_status = app.acknowledge_attention({"keys": [key]})
    first_data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    first_timestamp = first_data["attention_acks"]["keys"][key]
    first_rev = first_data["attention_acks"]["rev"]
    published.clear()
    notified.clear()
    invalidated.clear()

    duplicate, duplicate_status = app.acknowledge_attention({"keys": [key]})
    duplicate_data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))

    assert first_status == duplicate_status == HTTPStatus.OK
    assert first["changed"] is True
    assert duplicate["changed"] is False
    assert duplicate["rev"] == first_rev
    assert duplicate["acknowledged_at"][key] == first_timestamp
    assert duplicate_data["attention_acks"]["rev"] == first_rev
    assert duplicate_data["attention_acks"]["keys"][key] == first_timestamp
    assert published == []
    assert notified == []
    assert invalidated == []


def test_attention_ack_shared_file_prunes_stale_keys(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    now = time.time()
    stale_key = app.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "stale")
    fresh_key = app.attention_ack_key("agent-window", "1", "0", "%2", "codex", "approval", "fresh")
    (tmp_path / "attention-acks.json").write_text(
        json.dumps({"version": 1, "rev": 1, "keys": {stale_key: now - ATTENTION_ACK_TTL_SECONDS - 10}}),
        encoding="utf-8",
    )

    app.write_shared_attention_acks_union({fresh_key: now})

    data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert stale_key not in data["attention_acks"]["keys"]
    assert fresh_key in data["attention_acks"]["keys"]


def test_attention_ack_migrates_legacy_file_to_tmux_ai_status(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    now = time.time()
    legacy_key = app.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "legacy")
    fresh_key = app.attention_ack_key("agent-window", "1", "0", "%2", "codex", "approval", "fresh")
    (tmp_path / "attention-acks.json").write_text(
        json.dumps({"version": 1, "rev": 7, "keys": {legacy_key: now}}),
        encoding="utf-8",
    )

    app.write_shared_attention_acks_union({fresh_key: now})

    data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert legacy_key in data["attention_acks"]["keys"]
    assert fresh_key in data["attention_acks"]["keys"]
    assert data["attention_acks"]["rev"] == 8


def test_attention_ack_reads_legacy_keys_after_new_status_exists(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    now = time.time()
    legacy_key = app.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "legacy-after-new")
    (tmp_path / "tmux-AI-status.json").write_text(
        json.dumps({"version": 1, "rev": 3, "attention_acks": {"rev": 3, "keys": {}}, "stats_history": {"rev": 9, "raw_buckets": []}}),
        encoding="utf-8",
    )
    (tmp_path / "attention-acks.json").write_text(
        json.dumps({"version": 1, "rev": 10, "keys": {legacy_key: now}}),
        encoding="utf-8",
    )

    assert app.merge_shared_attention_acks() is True

    assert app.attention_acknowledged(legacy_key) is True


def test_attention_ack_merge_never_applies_stale_revision(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    now = time.time()
    stale_key = app.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "stale")
    local_key = app.attention_ack_key("agent-window", "1", "0", "%2", "codex", "approval", "local")
    (tmp_path / "tmux-AI-status.json").write_text(
        json.dumps({"version": 1, "rev": 1, "attention_acks": {"rev": 1, "keys": {stale_key: now}}}),
        encoding="utf-8",
    )
    with app.client_watch_service.lock:
        app.client_watch_service.attention_ack_rev = 2
    with app.attention_ack_lock:
        app.attention_ack_keys = {local_key: now}

    assert app.merge_shared_attention_acks() is False

    assert app.attention_acknowledged(local_key) is True
    assert app.attention_acknowledged(stale_key) is False


def test_attention_ack_timestamp_only_reack_does_not_report_changed(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    now = time.time()
    key = app.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "same-key")
    (tmp_path / "tmux-AI-status.json").write_text(
        json.dumps({"version": 1, "rev": 2, "attention_acks": {"rev": 2, "keys": {key: now + 10}}}),
        encoding="utf-8",
    )
    with app.client_watch_service.lock:
        app.client_watch_service.attention_ack_rev = 1
    with app.attention_ack_lock:
        app.attention_ack_keys = {key: now}

    assert app.merge_shared_attention_acks() is False

    with app.attention_ack_lock:
        assert app.attention_ack_keys[key] == now + 10
