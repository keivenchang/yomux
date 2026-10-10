# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Gate the complete host-local YO!chat storage layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from yomux_lib.chat import chat_service as chat_service_module
from yomux_lib.chat.chat_service import ChatCursorCodec
from yomux_lib.chat.chat_store import ChatStore
from yomux_lib.chat.chat_store import default_chat_database_path
from yomux_lib.infra import host_partition as host_partition_module
from yomux_lib.infra.host_identity import HostIdentity
from tests.helpers.local_service_records import FixtureHostIdentityBuilder

# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
pytestmark = pytest.mark.deprecated_python_server


def _host_identity(stable_host_id: str, display_hostname: str) -> HostIdentity:
    return FixtureHostIdentityBuilder(
        stable_host_id=stable_host_id,
        display_hostname=display_hostname,
        boot_id="fixture-boot",
        pid=4242,
        process_start_ticks=6262,
        instance_nonce=f"{stable_host_id}-instance",
        stable_host_id_source="chat gate fixture",
    ).build()


def _chat_artifact_paths(state_dir: Path) -> tuple[Path, Path, Path, Path]:
    database_path = default_chat_database_path(state_dir)
    store = ChatStore(database_path)
    cursor_secret_path = chat_service_module.default_chat_cursor_secret_path(state_dir)
    return database_path, store.history_dir, store.history_lock_path, cursor_secret_path


def test_chat_artifacts_are_partitioned_by_stable_host_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_a = _host_identity("fixture-host-a", "renamable-a.example")
    host_b = _host_identity("fixture-host-b", "renamable-b.example")

    monkeypatch.setattr(host_partition_module, "current_host_identity", lambda: host_a)
    host_a_paths = _chat_artifact_paths(tmp_path)
    monkeypatch.setattr(host_partition_module, "current_host_identity", lambda: host_b)
    host_b_paths = _chat_artifact_paths(tmp_path)

    assert host_a_paths == (
        tmp_path / "hosts" / host_a.stable_host_id / "yochat.sqlite3",
        tmp_path / "hosts" / host_a.stable_host_id / "yochat-history",
        tmp_path / "hosts" / host_a.stable_host_id / "yochat-history" / "journal",
        tmp_path / "hosts" / host_a.stable_host_id / "chat-cursor.key",
    )
    assert host_b_paths == (
        tmp_path / "hosts" / host_b.stable_host_id / "yochat.sqlite3",
        tmp_path / "hosts" / host_b.stable_host_id / "yochat-history",
        tmp_path / "hosts" / host_b.stable_host_id / "yochat-history" / "journal",
        tmp_path / "hosts" / host_b.stable_host_id / "chat-cursor.key",
    )
    assert set(host_a_paths).isdisjoint(host_b_paths)
    assert all(host_a.display_hostname not in str(path) for path in host_a_paths)
    assert all(host_b.display_hostname not in str(path) for path in host_b_paths)


def test_partitioned_chat_creation_leaves_legacy_artifacts_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_database_path = tmp_path / "yochat.sqlite3"
    legacy_history_dir = tmp_path / "yochat-history"
    legacy_cursor_secret_path = tmp_path / "chat-cursor.key"
    legacy_store = ChatStore(legacy_database_path, clock=lambda: 1_000_000.0)
    legacy_message, legacy_created = legacy_store.insert_message(
        username="legacy-user",
        sender_instance_id="legacy-browser",
        client_message_uuid="legacy-message",
        body="legacy message stays here",
        is_question=False,
    )
    assert legacy_created is True
    assert legacy_message.body == "legacy message stays here"
    ChatCursorCodec(legacy_cursor_secret_path).encode("older", 1)
    (legacy_history_dir / "legacy-marker").write_bytes(b"legacy history marker\x00\xff")
    (legacy_history_dir / "journal").write_bytes(b"legacy journal bytes\x00\xff")

    def legacy_snapshot() -> dict[Path, bytes]:
        files = [legacy_database_path, legacy_cursor_secret_path]
        files.extend(path for path in legacy_history_dir.rglob("*") if path.is_file())
        return {path.relative_to(tmp_path): path.read_bytes() for path in sorted(files)}

    before = legacy_snapshot()
    identity = _host_identity("fixture-host-a", "renamable-a.example")
    monkeypatch.setattr(host_partition_module, "current_host_identity", lambda: identity)
    database_path = default_chat_database_path(tmp_path)
    cursor_secret_path = chat_service_module.default_chat_cursor_secret_path(tmp_path)
    partitioned_store = ChatStore(database_path, clock=lambda: 1_000_001.0)
    partitioned_store.insert_message(
        username="host-user",
        sender_instance_id="host-browser",
        client_message_uuid="host-message",
        body="new host-local message",
        is_question=False,
    )
    ChatCursorCodec(cursor_secret_path).encode("older", 1)

    assert legacy_snapshot() == before
    assert [message.body for message in partitioned_store.messages_after(after_id=0)] == ["new host-local message"]
    assert database_path.is_file()
    assert partitioned_store.history_dir.is_dir()
    assert cursor_secret_path.is_file()
    assert database_path.is_relative_to(tmp_path / "hosts" / identity.stable_host_id)
    assert cursor_secret_path.is_relative_to(tmp_path / "hosts" / identity.stable_host_id)
