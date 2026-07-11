# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import json
import math
import multiprocessing
import os
import sqlite3
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

import pytest

from yolomux_lib.chat_store import CHAT_FTS_MODE
from yolomux_lib.chat_store import CHAT_HARD_ROW_LIMIT
from yolomux_lib.chat_store import CHAT_LIKE_FALLBACK_MODE
from yolomux_lib.chat_store import CHAT_MESSAGE_BODY_MAX_BYTES
from yolomux_lib.chat_store import CHAT_SCHEMA_VERSION
from yolomux_lib.chat_store import CHAT_TYPING_LEASE_SECONDS
from yolomux_lib.chat_store import ChatStore
from yolomux_lib.chat_store import ChatStoreMigrationError
from yolomux_lib.chat_store import ChatStoreValidationError


EMOJI_MATRIX = (
    "😀",
    "👍🏽",
    "👩‍💻",
    "👨‍👩‍👧‍👦",
    "🏳️‍🌈",
    "🇺🇸",
    "1️⃣",
    "☕️",
    "مرحبا 😀",
)


def _chat_store_latency_budget_ms(*, cpu_count: int | None = None) -> float:
    cores = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)))
    return 250.0 * max(1.0, 32 / cores)


def _insert(store: ChatStore, index: int, *, username: str = "alice", timestamp: float | None = None, body: str | None = None) -> int:
    message, inserted = store.insert_message(
        username=username,
        sender_instance_id=f"instance-{username}",
        client_message_uuid=f"message-{username}-{index}",
        body=body if body is not None else f"message {index}",
        is_question=index % 2 == 0,
        created_at_utc=timestamp,
    )
    assert inserted is True
    return message.id


def _utc_timestamp(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_chat_store_latency_budget_scales_below_linux_baseline_cores():
    assert _chat_store_latency_budget_ms(cpu_count=64) == 250
    assert _chat_store_latency_budget_ms(cpu_count=32) == 250
    assert _chat_store_latency_budget_ms(cpu_count=10) == 800


def _history_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _multiprocess_chat_writer(path: str, username: str, count: int, start_event: Any, results: Any) -> None:
    try:
        store = ChatStore(Path(path))
        start_event.wait(10)
        ids = [_insert(store, index, username=username, body=f"{username} {index} 😀") for index in range(count)]
        results.put((username, ids, ""))
    except BaseException as error:
        results.put((username, [], repr(error)))


def test_chat_store_preserves_exact_unicode_and_idempotent_send(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite3", clock=lambda: 2000.0)
    body = " | ".join(EMOJI_MATRIX)
    message, inserted = store.insert_message(
        username="alice",
        sender_ip="2001:0db8::1",
        sender_instance_id="browser-a",
        client_message_uuid="uuid-a",
        body=body,
        is_question=True,
        created_at_utc=1234.5,
    )
    duplicate, duplicate_inserted = store.insert_message(
        username="alice",
        sender_instance_id="browser-a",
        client_message_uuid="uuid-a",
        body="a retry must not replace the original body",
        is_question=False,
        created_at_utc=9999,
    )

    assert inserted is True
    assert duplicate_inserted is False
    assert duplicate == message
    assert message.body == body
    assert message.created_at_utc == 1234.5
    assert message.is_question is True
    assert message.sender_ip == "2001:db8::1"
    page = store.page_before(limit=10, retention_days=365)
    assert page.messages == (message,)
    assert store.context(message_id=message.id, retention_days=365).target.body == body


def test_chat_store_writes_private_utc_dated_history_without_duplicate_retries(tmp_path):
    history_dir = tmp_path / "dated-history"
    store = ChatStore(tmp_path / "chat.sqlite3", history_dir=history_dir)
    first, inserted = store.insert_message(
        username="GuestCase",
        sender_ip="10.1.123.12",
        sender_instance_id="browser-a",
        client_message_uuid="message-a",
        body="exact 👨‍👩‍👧‍👦 مرحبا body",
        is_question=True,
        created_at_utc=_utc_timestamp("2026-07-03T23:59:59"),
    )
    second, _ = store.insert_message(
        username="GuestCase",
        sender_ip="2001:db8::1",
        sender_instance_id="browser-a",
        client_message_uuid="message-b",
        body="next UTC day",
        is_question=False,
        created_at_utc=_utc_timestamp("2026-07-04T00:00:00"),
    )
    retry, retry_inserted = store.insert_message(
        username="GuestCase",
        sender_ip="127.0.0.1",
        sender_instance_id="browser-a",
        client_message_uuid="message-a",
        body="retry body must not replace the journal",
        is_question=False,
    )

    assert inserted is True
    assert retry_inserted is False
    assert retry == first
    assert sorted(path.name for path in history_dir.glob("*.jsonl")) == ["2026-07-03.jsonl", "2026-07-04.jsonl"]
    first_records = _history_records(history_dir / "2026-07-03.jsonl")
    second_records = _history_records(history_dir / "2026-07-04.jsonl")
    assert first_records == [{
        "body": first.body,
        "client_message_uuid": first.client_message_uuid,
        "created_at_utc": first.created_at_utc,
        "id": first.id,
        "is_question": True,
        "room_id": "global",
        "sender_instance_id": "browser-a",
        "sender_ip": "10.1.123.12",
        "username": "GuestCase",
        "version": 1,
    }]
    assert second_records[0]["id"] == second.id
    assert history_dir.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in history_dir.glob("*.jsonl"))


def test_chat_store_backfills_missing_history_and_prune_rotates_exact_cutoff_day(tmp_path):
    now = _utc_timestamp("2026-07-10T12:00:00")
    path = tmp_path / "yochat.sqlite3"
    history_dir = tmp_path / "yochat-history"
    store = ChatStore(path, clock=lambda: now, history_dir=history_dir)
    _insert(store, 1, timestamp=_utc_timestamp("2026-07-02T12:00:00"), body="expired old day")
    _insert(store, 2, timestamp=_utc_timestamp("2026-07-03T11:59:59"), body="expired cutoff day")
    kept_id = _insert(store, 3, timestamp=_utc_timestamp("2026-07-03T12:00:00"), body="kept exact cutoff")
    newest_id = _insert(store, 4, timestamp=_utc_timestamp("2026-07-10T11:59:59"), body="kept newest")

    (history_dir / "2026-07-03.jsonl").unlink()
    backfilled_store = ChatStore(path, clock=lambda: now, history_dir=history_dir)
    backfilled_store.fts_mode
    assert [record["id"] for record in _history_records(history_dir / "2026-07-03.jsonl")] == [2, kept_id]

    result = backfilled_store.prune(retention_days=7)
    assert result.deleted_expired == 2
    assert not (history_dir / "2026-07-02.jsonl").exists()
    assert [record["id"] for record in _history_records(history_dir / "2026-07-03.jsonl")] == [kept_id]
    assert [record["id"] for record in _history_records(history_dir / "2026-07-10.jsonl")] == [newest_id]
    diagnostics = backfilled_store.diagnostics()
    assert diagnostics["history_files"] == 2
    assert diagnostics["history_bytes"] > 0
    assert "body" not in diagnostics


def test_chat_store_rejects_oversized_body_without_slicing_unicode(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite3")
    accepted = "😀" * (CHAT_MESSAGE_BODY_MAX_BYTES // len("😀".encode("utf-8")))
    message, inserted = store.insert_message(
        username="alice",
        sender_instance_id="browser-a",
        client_message_uuid="accepted",
        body=accepted,
        is_question=False,
    )
    assert inserted is True
    assert message.body == accepted
    with pytest.raises(ChatStoreValidationError, match="8 KiB"):
        store.insert_message(
            username="alice",
            sender_instance_id="browser-a",
            client_message_uuid="rejected",
            body=accepted + "😀",
            is_question=False,
        )
    assert [item.client_message_uuid for item in store.page_before(limit=10).messages] == ["accepted"]


def test_chat_store_person_cursor_starts_at_tail_and_is_shared_across_browsers(tmp_path):
    now = [100_000.0]
    store = ChatStore(tmp_path / "chat.sqlite3", clock=lambda: now[0])
    first_id = _insert(store, 1, timestamp=now[0] - 1)
    bootstrap = store.bootstrap(username="alice")
    assert bootstrap.first_registration is True
    assert bootstrap.latest_message_id == first_id
    assert bootstrap.read_cursor.read_up_to_id == first_id
    assert bootstrap.unread_messages == ()
    assert bootstrap.has_more_older is True

    bob_baseline = store.bootstrap(username="bob")
    assert bob_baseline.unread_messages == ()
    second_id = _insert(store, 2, username="bob", timestamp=now[0])
    unread = store.bootstrap(username="alice")
    assert [item.id for item in unread.unread_messages] == [second_id]
    assert unread.has_more_older is True
    assert store.read_up_to(username="alice", message_id=second_id).read_up_to_id == second_id
    assert store.bootstrap(username="alice").unread_messages == ()
    assert [item.id for item in store.bootstrap(username="bob").unread_messages] == [second_id]
    assert store.read_up_to(username="alice", message_id=first_id).read_up_to_id == second_id
    with pytest.raises(ChatStoreValidationError, match="tail"):
        store.read_up_to(username="alice", message_id=second_id + 1)


def test_chat_store_typing_leases_refresh_stop_expire_and_send_clears(tmp_path):
    now = [1000.0]
    store = ChatStore(tmp_path / "chat.sqlite3", clock=lambda: now[0])
    leases = store.set_typing(username="alice", browser_instance_id="browser-a", typing=True)
    assert leases[0].expires_at_utc == now[0] + CHAT_TYPING_LEASE_SECONDS
    store.set_typing(username="alice", browser_instance_id="browser-b", typing=True)
    store.set_typing(username="bob", browser_instance_id="browser-c", typing=True)
    assert [(item.username, item.browser_instance_id) for item in store.typing_snapshot()] == [
        ("alice", "browser-a"),
        ("alice", "browser-b"),
        ("bob", "browser-c"),
    ]

    store.set_typing(username="alice", browser_instance_id="browser-a", typing=False)
    assert [item.browser_instance_id for item in store.typing_snapshot()] == ["browser-b", "browser-c"]
    store.insert_message(
        username="alice",
        sender_instance_id="browser-b",
        client_message_uuid="sent",
        body="done",
        is_question=False,
    )
    assert [item.browser_instance_id for item in store.typing_snapshot()] == ["browser-c"]
    now[0] += CHAT_TYPING_LEASE_SECONDS + 0.001
    assert store.typing_snapshot() == ()


def test_chat_store_keyset_pages_context_and_tied_timestamps_are_stable(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite3", clock=lambda: 10_000.0)
    ids = [_insert(store, index, timestamp=9_999.0) for index in range(120)]
    newest_page = store.page_before(limit=50)
    assert [item.id for item in newest_page.messages] == ids[-50:]
    assert newest_page.has_more is True
    assert newest_page.older_cursor == ids[-50]
    older_page = store.page_before(before_id=newest_page.older_cursor, limit=50)
    assert [item.id for item in older_page.messages] == ids[20:70]
    assert not set(item.id for item in newest_page.messages).intersection(item.id for item in older_page.messages)
    context = store.context(message_id=ids[60], before=3, after=4)
    assert [item.id for item in context.before] == ids[57:60]
    assert context.target.id == ids[60]
    assert [item.id for item in context.after] == ids[61:65]
    assert [item.id for item in store.messages_after(after_id=ids[115])] == ids[116:]


@pytest.mark.parametrize("fts_preference", ["auto", CHAT_LIKE_FALLBACK_MODE])
def test_chat_store_search_supports_words_literal_emoji_mixed_text_and_paging(tmp_path, fts_preference):
    store = ChatStore(tmp_path / f"chat-{fts_preference}.sqlite3", fts_preference=fts_preference)
    bodies = [
        "alpha ordinary",
        "Alpha second 😀",
        "literal 👩‍💻 adjacent text",
        "100% wildcard_name",
        "mixed مرحبا 🏳️‍🌈 text",
    ]
    for index, body in enumerate(bodies):
        _insert(store, index, body=body)

    assert store.fts_mode in {CHAT_FTS_MODE, CHAT_LIKE_FALLBACK_MODE}
    assert [hit.message.body for hit in store.search(query="alpha", limit=10).hits] == bodies[1::-1]
    assert [hit.message.body for hit in store.search(query="👩‍💻 adjacent", limit=10).hits] == [bodies[2]]
    assert [hit.message.body for hit in store.search(query="🏳️‍🌈", limit=10).hits] == [bodies[4]]
    assert [hit.message.body for hit in store.search(query="100% wildcard_name", limit=10).hits] == [bodies[3]]
    first = store.search(query="alpha", limit=1)
    second = store.search(query="alpha", cursor=first.next_cursor, limit=1)
    assert first.has_more is True
    assert first.next_cursor is not None
    assert [hit.message.id for hit in first.hits + second.hits] == [2, 1]


def test_chat_store_retention_filters_queries_and_prunes_in_batches_with_row_ceiling(tmp_path):
    now = [20 * 24 * 60 * 60.0]
    store = ChatStore(tmp_path / "chat.sqlite3", clock=lambda: now[0])
    old_ids = [_insert(store, index, timestamp=now[0] - (8 * 24 * 60 * 60)) for index in range(4)]
    new_ids = [_insert(store, index + 10, timestamp=now[0] - 1) for index in range(5)]
    assert [item.id for item in store.page_before(limit=20, retention_days=7).messages] == new_ids
    assert store.context(message_id=old_ids[0], retention_days=7) is None
    result = store.prune(retention_days=7, hard_row_limit=3)
    assert result.deleted_expired == 4
    assert result.deleted_overflow == 2
    assert result.remaining_rows == 3
    assert [item.id for item in store.page_before(limit=20, retention_days=7).messages] == new_ids[-3:]
    assert store.database_size_bytes() > 0
    diagnostics = store.diagnostics()
    assert diagnostics["message_rows"] == 3
    assert diagnostics["prune_runs"] == 1
    assert diagnostics["prune_deleted_expired"] == 4
    assert diagnostics["prune_deleted_overflow"] == 2
    assert diagnostics["database_bytes"] > 0


def test_chat_store_prune_if_due_is_hourly_but_retention_reduction_is_immediate(tmp_path):
    now = [20 * 24 * 60 * 60.0]
    store = ChatStore(tmp_path / "chat.sqlite3", clock=lambda: now[0])
    expired_id = _insert(store, 1, timestamp=now[0] - (8 * 24 * 60 * 60))

    first = store.prune_if_due(retention_days=30)
    assert first.ran is True
    skipped = store.prune_if_due(retention_days=30)
    assert skipped.ran is False
    reduced = store.prune_if_due(retention_days=7, previous_retention_days=30)
    assert reduced.ran is True
    assert reduced.deleted_expired == 1
    assert store.context(message_id=expired_id, retention_days=365) is None


def test_chat_store_rejects_corrupt_and_future_schema_databases(tmp_path):
    corrupt_path = tmp_path / "corrupt.sqlite3"
    corrupt_path.write_bytes(b"not a sqlite database")
    with pytest.raises(ChatStoreMigrationError, match="initialize"):
        ChatStore(corrupt_path).fts_mode

    future_path = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(future_path)
    connection.execute(f"PRAGMA user_version = {CHAT_SCHEMA_VERSION + 1}")
    connection.close()
    with pytest.raises(ChatStoreMigrationError, match="newer"):
        ChatStore(future_path).fts_mode


def test_chat_store_migrates_v1_messages_with_empty_sender_ip(tmp_path):
    path = tmp_path / "v1.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at_utc REAL NOT NULL, room_id TEXT NOT NULL,
            username TEXT NOT NULL, sender_instance_id TEXT NOT NULL, client_message_uuid TEXT NOT NULL,
            body TEXT NOT NULL, is_question INTEGER NOT NULL CHECK (is_question IN (0, 1)),
            UNIQUE (room_id, username, sender_instance_id, client_message_uuid)
        )"""
    )
    connection.execute(
        "INSERT INTO chat_messages(created_at_utc, room_id, username, sender_instance_id, client_message_uuid, body, is_question) VALUES (1, 'global', 'guest', 'browser', 'old', 'old row', 0)"
    )
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    store = ChatStore(path, clock=lambda: 2.0)
    assert store.page_before(limit=10, retention_days=365).messages[0].sender_ip == ""
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == CHAT_SCHEMA_VERSION
    assert "sender_ip" in {row[1] for row in check.execute("PRAGMA table_info(chat_messages)")}
    check.close()


def test_chat_store_migrates_v2_browser_cursors_to_each_persons_furthest_acknowledgement(tmp_path):
    path = tmp_path / "v2.sqlite3"
    connection = sqlite3.connect(path)
    ChatStore._create_schema(connection)
    connection.execute("DROP TABLE chat_read_cursors")
    connection.execute(
        """CREATE TABLE chat_read_cursors (
            room_id TEXT NOT NULL, username TEXT NOT NULL, reader_id TEXT NOT NULL,
            read_up_to_id INTEGER NOT NULL DEFAULT 0, updated_at_utc REAL NOT NULL,
            PRIMARY KEY (room_id, username, reader_id)
        )"""
    )
    connection.executemany(
        "INSERT INTO chat_read_cursors(room_id, username, reader_id, read_up_to_id, updated_at_utc) VALUES (?, ?, ?, ?, ?)",
        [
            ("global", "alice", "browser-a", 4, 10.0),
            ("global", "alice", "browser-b", 9, 20.0),
            ("global", "bob", "browser-c", 3, 30.0),
        ],
    )
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()

    store = ChatStore(path)
    assert store.fts_mode in {CHAT_FTS_MODE, CHAT_LIKE_FALLBACK_MODE}
    check = sqlite3.connect(path)
    rows = check.execute(
        "SELECT username, read_up_to_id FROM chat_read_cursors ORDER BY username"
    ).fetchall()
    assert rows == [("alice", 9), ("bob", 3)]
    assert check.execute("PRAGMA user_version").fetchone()[0] == CHAT_SCHEMA_VERSION
    assert "reader_id" not in {row[1] for row in check.execute("PRAGMA table_info(chat_read_cursors)")}
    check.close()


def test_chat_store_empty_bootstrap_has_no_older_history(tmp_path):
    store = ChatStore(tmp_path / "empty.sqlite3")
    assert store.bootstrap(username="guest").has_more_older is False


def test_chat_store_two_processes_write_concurrently_without_loss(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_multiprocess_chat_writer, args=(str(path), username, 25, start_event, results))
        for username in ("alice", "bob")
    ]
    for process in processes:
        process.start()
    start_event.set()
    records = [results.get(timeout=20) for _process in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert all(not error for _username, _ids, error in records), records
    assert all(len(ids) == 25 for _username, ids, _error in records)
    messages = ChatStore(path).page_before(limit=100).messages
    assert len(messages) == 50
    assert {message.username for message in messages} == {"alice", "bob"}
    assert len({message.id for message in messages}) == 50


def test_chat_store_hard_ceiling_acceptance_payload_and_latency_budgets(tmp_path):
    now = 40 * 24 * 60 * 60.0
    store = ChatStore(tmp_path / "ceiling.sqlite3", clock=lambda: now)
    store.fts_mode
    connection = sqlite3.connect(store.path)
    recent_start = now - (7 * 24 * 60 * 60) + 1
    rows = (
        (
            recent_start + index,
            "global",
            "fixture-user",
            "fixture-browser",
            f"fixture-{index}",
            f"fixture row {index} alpha" + (" 👩‍💻 benchmark" if index == CHAT_HARD_ROW_LIMIT - 2 else ""),
            0,
        )
        for index in range(CHAT_HARD_ROW_LIMIT)
    )
    connection.executemany(
        """
        INSERT INTO chat_messages(created_at_utc, room_id, username, sender_instance_id, client_message_uuid, body, is_question)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()

    timings_ms = []

    started = time.perf_counter()
    bootstrap = store.bootstrap(username="new-reader", retention_days=7)
    timings_ms.append((time.perf_counter() - started) * 1000)
    assert bootstrap.first_registration is True
    assert bootstrap.unread_messages == ()
    assert bootstrap.read_cursor.read_up_to_id == CHAT_HARD_ROW_LIMIT

    started = time.perf_counter()
    page = store.page_before(limit=50, retention_days=7)
    timings_ms.append((time.perf_counter() - started) * 1000)
    assert len(page.messages) == 50

    target_id = CHAT_HARD_ROW_LIMIT - 1
    started = time.perf_counter()
    context = store.context(message_id=target_id, before=3, after=3, retention_days=7)
    timings_ms.append((time.perf_counter() - started) * 1000)
    assert context is not None and context.target.id == target_id
    assert len(context.before) <= 3 and len(context.after) <= 3

    started = time.perf_counter()
    search = store.search(query="👩‍💻 benchmark", limit=20, retention_days=7)
    timings_ms.append((time.perf_counter() - started) * 1000)
    assert [hit.message.id for hit in search.hits] == [target_id]

    store.insert_message(
        username="expired-user",
        sender_instance_id="expired-browser",
        client_message_uuid="expired-message",
        body="expired fixture",
        is_question=False,
        created_at_utc=recent_start - 2,
    )
    started = time.perf_counter()
    pruned = store.prune(retention_days=7)
    timings_ms.append((time.perf_counter() - started) * 1000)
    assert pruned.deleted_expired == 1
    assert pruned.deleted_overflow == 0
    assert pruned.remaining_rows == CHAT_HARD_ROW_LIMIT

    p95_index = max(0, math.ceil(len(timings_ms) * 0.95) - 1)
    p95_ms = sorted(timings_ms)[p95_index]
    latency_budget_ms = _chat_store_latency_budget_ms()
    assert p95_ms < latency_budget_ms, {
        "timings_ms": timings_ms,
        "p95_ms": p95_ms,
        "latency_budget_ms": latency_budget_ms,
        "cpu_count": os.cpu_count(),
    }
    assert " OFFSET " not in Path("yolomux_lib/chat_store.py").read_text(encoding="utf-8").upper()
