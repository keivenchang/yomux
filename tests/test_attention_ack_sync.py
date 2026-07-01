"""Cross-server attention acknowledgement sync."""

import json
import time
from http import HTTPStatus

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


def test_attention_ack_poll_publishes_once_per_revision(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    key = first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "poll")
    first.acknowledge_attention({"keys": [key]})
    published = []
    monkeypatch.setattr(second, "publish_client_event", lambda *args, **kwargs: published.append(args) or {})

    assert second.poll_attention_acks_client_event_once() == ["auto_approve_changed"]
    assert second.poll_attention_acks_client_event_once() == []
    assert len(published) == 1


def test_attention_ack_background_event_translates_to_auto_approve_refresh(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    first = make_app()
    second = make_app()
    key = first.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "fanout")
    first.acknowledge_attention({"keys": [key]})
    published = []
    monkeypatch.setattr(second, "publish_client_event", lambda *args, **kwargs: published.append((args, kwargs)) or {"id": 1})

    result = second.handle_background_client_event({"event_type": "attention_acks_changed", "payload": {}})

    assert result["ok"] is True
    assert result["accepted"] is True
    assert second.attention_acknowledged(key) is True
    assert published[0][0][0] == "auto_approve_changed"
    assert published[0][0][1] == {"refresh": True, "trigger": "attention_ack_sync"}
    assert published[0][1]["trigger"] == "background-fanout"


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
    with app.client_watch_lock:
        app.client_watch_attention_ack_rev = 2
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
    with app.client_watch_lock:
        app.client_watch_attention_ack_rev = 1
    with app.attention_ack_lock:
        app.attention_ack_keys = {key: now}

    assert app.merge_shared_attention_acks() is False

    with app.attention_ack_lock:
        assert app.attention_ack_keys[key] == now + 10
