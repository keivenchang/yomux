# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Local attention acknowledgement behavior."""

from __future__ import annotations

import json
import time
from http import HTTPStatus

import pytest

from yolomux_lib import common
from yolomux_lib.app import ATTENTION_ACK_KEY_MAX_LENGTH
from yolomux_lib.app import ATTENTION_ACK_TTL_SECONDS


@pytest.fixture
def make_app(monkeypatch, make_tmux_webterm_app):
    def factory():
        app = make_tmux_webterm_app()
        app.tmux_ai_status_path = common.TMUX_AI_STATUS_PATH
        return app

    return factory


def patch_shared_path(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "TMUX_AI_STATUS_PATH", tmp_path / "tmux-AI-status.json")
    monkeypatch.setattr(common, "LEGACY_ATTENTION_ACKS_PATH", tmp_path / "attention-acks.json")


def test_attention_ack_persists_in_the_local_status_file(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    key = app.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "persist")

    result, status = app.acknowledge_attention({"keys": [key]})

    assert status == HTTPStatus.OK
    assert result["acknowledged"] == [key]
    data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert data["attention_acks"]["keys"][key] == result["acknowledged_at"][key]


def test_attention_ack_revision_invalidates_local_tabber_cache_key(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    monkeypatch.setattr(app, "activity_session_names", lambda _scope: (["1"], [], "configured"))
    before = app.tabber_activity_source_signature()
    key = app.attention_ack_key("agent-window", "1", "0", "%1", "claude", "approval", "tabber-cache")

    app.acknowledge_attention({"keys": [key]})

    assert app.tabber_activity_source_signature() != before


def test_duplicate_attention_ack_is_a_local_noop(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    key = app.attention_ack_key("agent-window", "8002", "1", "%27", "codex", "needs-input", "same-event")

    first, first_status = app.acknowledge_attention({"keys": [key]})
    duplicate, duplicate_status = app.acknowledge_attention({"keys": [key]})

    assert first_status == duplicate_status == HTTPStatus.OK
    assert first["changed"] is True
    assert duplicate["changed"] is False
    assert duplicate["rev"] == first["rev"]
    assert duplicate["acknowledged_at"][key] == first["acknowledged_at"][key]


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


def test_attention_ack_migrates_legacy_file_to_local_status(monkeypatch, tmp_path, make_app):
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
    assert data["attention_acks"]["keys"] == {legacy_key: now, fresh_key: now}
    assert data["attention_acks"]["rev"] == 8


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


@pytest.mark.parametrize(
    "parts",
    [
        ("prompt", "1", "short question?"),
        ("agent-window", "1", "0", "%1", "claude", "approval", "Approve this action?"),
    ],
)
def test_attention_ack_key_preserves_short_routing_parts(parts, monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()

    key = app.attention_ack_key(*parts)

    assert len(key.encode("utf-8")) <= ATTENTION_ACK_KEY_MAX_LENGTH
    assert app.acknowledge_attention({"keys": [key]})[1] == HTTPStatus.OK


def test_attention_ack_key_bounds_long_multibyte_text_and_avoids_prefix_collisions(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()
    prefix = "Do you want to proceed with this destructive action? " * 10
    key_one = app.attention_ack_key("prompt", "1", prefix + "yes, delete the database")
    key_two = app.attention_ack_key("prompt", "1", prefix + "yes, delete the backup instead")
    multibyte_key = app.attention_ack_key("prompt", "1", "你好请确认是否继续执行这个操作" * 20)

    assert key_one != key_two
    assert len(multibyte_key.encode("utf-8")) <= ATTENTION_ACK_KEY_MAX_LENGTH
    result, status = app.acknowledge_attention({"keys": [key_one, key_two, multibyte_key]})
    assert status == HTTPStatus.OK
    assert sorted(result["acknowledged"]) == sorted([key_one, key_two, multibyte_key])


def test_acknowledge_attention_rejects_keys_over_utf8_byte_limit(monkeypatch, tmp_path, make_app):
    patch_shared_path(monkeypatch, tmp_path)
    app = make_app()

    at_limit = "a" * ATTENTION_ACK_KEY_MAX_LENGTH
    over_limit = "a" * (ATTENTION_ACK_KEY_MAX_LENGTH + 1)
    ok_result, ok_status = app.acknowledge_attention({"keys": [at_limit]})
    bad_result, bad_status = app.acknowledge_attention({"keys": [over_limit]})

    assert ok_status == HTTPStatus.OK
    assert ok_result["acknowledged"] == [at_limit]
    assert bad_status == HTTPStatus.BAD_REQUEST
    assert "acknowledged" not in bad_result
