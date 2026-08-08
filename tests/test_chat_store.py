# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import inspect
import json
import math
import multiprocessing
import re
import sqlite3
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable
from typing import NamedTuple

import pytest

from tests import latency_calibration
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


# The file that actually defines ChatStore, derived from the class under test rather than
# named by hand. `yolomux_lib/chat_store.py` is a six-line compatibility alias that rebinds
# sys.modules, so a hand-written path there scans a file containing no SQL at all and any
# query-shape assertion made against it passes for free.
CHAT_STORE_SOURCE_PATH = Path(inspect.getsourcefile(ChatStore)).resolve()

# sqlite3 invokes the progress handler once per this many virtual-machine instructions.
CHAT_STORE_WORK_STEP_GRANULARITY = 1000

# Plan lines that touch the chat_messages table itself. The word boundaries keep
# `chat_messages_fts` and `sqlite_autoindex_chat_messages_1` from matching as the table.
CHAT_MESSAGE_PLAN_LINE = re.compile(r"\bchat_messages\b")

# The access paths a chat_messages read is allowed to take. Asserting merely "not a full
# SCAN" is unfalsifiable here: the table's UNIQUE (room_id, username, sender_instance_id,
# client_message_uuid) constraint creates sqlite_autoindex_chat_messages_1, so SQLite always
# has a room_id path and never plans a bare scan, even with chat_messages_room_time deleted.
# Naming the purpose-built index is reachable, and a control that removes it goes red here.
CHAT_MESSAGE_ALLOWED_ACCESS = (
    "USING INDEX chat_messages_room_time",
    "USING COVERING INDEX chat_messages_room_time",
    "USING INTEGER PRIMARY KEY",
)

# Steps per retained row, and statements per call, allowed at the row ceiling. Measured on
# SQLite 3.45.1 running this node's own sequence at CHAT_HARD_ROW_LIMIT rows: bootstrap 5,
# page_before 22, context 27, search 43 and prune 4 steps per row, at 9/1/3/16/19
# statements. Every figure was byte-identical across repetitions at host load 20, and
# multiplying the row count by four multiplied every step count by exactly 4.00, so these
# are properties of the query plan rather than of the box.
#
# Every limit is deliberately below twice its measured value. The cheapest real complexity
# regression is doing existing work one extra time, which doubles it; limits set at 2x let
# exactly that regression through. A control that made page_before run its scan twice
# passed against an earlier 45-steps-per-row limit and fails against 30. The remaining
# 1.3-1.5x margin absorbs SQLite version drift, which moves opcode counts for an unchanged
# plan by a few percent rather than by a third.
CHAT_STORE_WORK_STEP_LIMITS = {
    "bootstrap": 7,
    "page_before": 30,
    "context": 36,
    "search": 58,
    "prune": 6,
}
CHAT_STORE_STATEMENT_LIMITS = {
    "bootstrap": 12,
    "page_before": 2,
    "context": 4,
    "search": 22,
    "prune": 26,
}


class _ChatStoreWork(NamedTuple):
    """One operation's deterministic work accounting plus its diagnostic-only timings."""

    label: str
    statements: tuple[str, ...]
    work_steps: int
    wall_ms: float
    thread_cpu_ms: float

    def evidence(self) -> dict[str, Any]:
        return {
            "op": self.label,
            "work_steps": self.work_steps,
            "statements": len(self.statements),
            "diagnostic_wall_ms": round(self.wall_ms, 3),
            "diagnostic_thread_cpu_ms": round(self.thread_cpu_ms, 3),
        }


class _ChatStoreWorkMeter:
    """Count SQLite statements and virtual-machine work steps for one chat-store operation.

    Wall time measured inside a parallel gate reports the process scheduler and the page
    cache, not the product: the same `page_before` call at the row ceiling took 365 ms on a
    cold cache and 58-72 ms warm on the same host, and the warm figure did not move when
    host load went from 5 to 20. Virtual-machine step counts are exactly reproducible for
    the same rows and query plan (byte-identical across five repetitions at load 20) and
    scale exactly with row count (25k -> 100k rows multiplied every operation's steps by
    4.00), so they carry the complexity contract that the fused wall-clock assertion was
    standing in for.

    The timings recorded here are diagnostic evidence only. They are never asserted, and
    `thread_cpu_ms` cannot replace wall latency: `bootstrap` at the row ceiling spends 26 ms
    of wall time against 7 ms of thread CPU, so 19 ms of real user-visible SQLite and file
    I/O is invisible to CPU accounting. Wall latency is certified separately, per operation,
    by `test_chat_store_operation_wall_latency_certification`.
    """

    def __init__(self, store: ChatStore, *, granularity: int = CHAT_STORE_WORK_STEP_GRANULARITY):
        self._store = store
        self._granularity = granularity
        self._open_connection = store._raw_connection
        self._statements: list[str] = []
        self._steps = 0
        store._raw_connection = self._instrumented_connection

    def _instrumented_connection(self) -> sqlite3.Connection:
        connection = self._open_connection()
        connection.set_trace_callback(self._statements.append)
        connection.set_progress_handler(self._count_step, self._granularity)
        return connection

    def _count_step(self) -> int:
        self._steps += 1
        return 0

    def detach(self) -> None:
        self._store._raw_connection = self._open_connection

    def measure(self, label: str, operation: Callable[[], Any]) -> tuple[Any, _ChatStoreWork]:
        self._statements.clear()
        self._steps = 0
        wall_started = time.perf_counter()
        cpu_started = time.thread_time()
        value = operation()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        thread_cpu_ms = (time.thread_time() - cpu_started) * 1000
        return value, _ChatStoreWork(
            label=label,
            statements=tuple(self._statements),
            work_steps=self._steps * self._granularity,
            wall_ms=wall_ms,
            thread_cpu_ms=thread_cpu_ms,
        )


def _chat_message_query_plans(store: ChatStore, statements: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Return EXPLAIN QUERY PLAN details for each traced SELECT that reads chat_messages.

    sqlite3's trace callback hands back statements with their parameters already expanded,
    so each one can be re-planned directly. SQLite's own FTS bookkeeping statements arrive
    commented out with a leading `--` and are skipped.
    """

    plans: dict[str, tuple[str, ...]] = {}
    connection = sqlite3.connect(store.path)
    try:
        for statement in statements:
            text = " ".join(statement.split())
            if not text.upper().startswith("SELECT") or "CHAT_MESSAGES" not in text.upper():
                continue
            plans[text] = tuple(str(row[3]) for row in connection.execute(f"EXPLAIN QUERY PLAN {text}"))
    finally:
        connection.close()
    return plans


def _build_chat_store_at_row_ceiling(path: Path) -> tuple[ChatStore, float, float]:
    """Fill a store with exactly CHAT_HARD_ROW_LIMIT retained rows, one carrying the search target."""

    now = 40 * 24 * 60 * 60.0
    store = ChatStore(path, clock=lambda: now)
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
    return store, now, recent_start


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


def test_chat_store_hard_ceiling_payload_and_bounded_work(tmp_path):
    """Everything the row-ceiling acceptance owes the parallel gate, minus wall time.

    This node used to end with a single fixed 250 ms ceiling applied to the maximum of five
    heterogeneous operations (ceil(5 * .95) - 1 == 4, so the "p95" was the maximum), which
    made it the most frequent red in the full gate while passing every time in isolation.
    The semantic and complexity contracts below are what actually protect users, and they
    are deterministic: work-step counts are byte-identical run to run and unaffected by host
    load. The wall-latency claim now lives in
    `test_chat_store_operation_wall_latency_certification`.
    """

    store, _now, recent_start = _build_chat_store_at_row_ceiling(tmp_path / "ceiling.sqlite3")
    meter = _ChatStoreWorkMeter(store)
    measurements: list[_ChatStoreWork] = []

    bootstrap, bootstrap_work = meter.measure("bootstrap", lambda: store.bootstrap(username="new-reader", retention_days=7))
    measurements.append(bootstrap_work)
    assert bootstrap.first_registration is True
    assert bootstrap.unread_messages == ()
    assert bootstrap.read_cursor.read_up_to_id == CHAT_HARD_ROW_LIMIT

    page, page_work = meter.measure("page_before", lambda: store.page_before(limit=50, retention_days=7))
    measurements.append(page_work)
    assert len(page.messages) == 50
    assert [item.id for item in page.messages] == list(range(CHAT_HARD_ROW_LIMIT - 49, CHAT_HARD_ROW_LIMIT + 1))

    target_id = CHAT_HARD_ROW_LIMIT - 1
    context, context_work = meter.measure(
        "context", lambda: store.context(message_id=target_id, before=3, after=3, retention_days=7)
    )
    measurements.append(context_work)
    assert context is not None and context.target.id == target_id
    assert len(context.before) <= 3 and len(context.after) <= 3

    search, search_work = meter.measure(
        "search", lambda: store.search(query="👩‍💻 benchmark", limit=20, retention_days=7)
    )
    measurements.append(search_work)
    assert [hit.message.id for hit in search.hits] == [target_id]

    store.insert_message(
        username="expired-user",
        sender_instance_id="expired-browser",
        client_message_uuid="expired-message",
        body="expired fixture",
        is_question=False,
        created_at_utc=recent_start - 2,
    )
    pruned, prune_work = meter.measure("prune", lambda: store.prune(retention_days=7))
    measurements.append(prune_work)
    assert pruned.deleted_expired == 1
    assert pruned.deleted_overflow == 0
    assert pruned.remaining_rows == CHAT_HARD_ROW_LIMIT
    assert store.diagnostics()["message_rows"] == CHAT_HARD_ROW_LIMIT

    evidence = [item.evidence() for item in measurements]

    # A meter that measured nothing would pass every bound below, so prove it is live before
    # trusting it. This is the failure mode the old ` OFFSET ` check had: it read a six-line
    # alias and could not fail.
    for work in measurements:
        assert work.work_steps > 0 and work.statements, {"dead_work_meter": work.label, "evidence": evidence}

    # Query plans are checked before the work bounds so each rule owns a control only it can
    # catch. Deleting chat_messages_room_time is caught here, because SQLite falls back to
    # the UNIQUE constraint's autoindex; making page_before run its existing scan twice
    # leaves the plan byte-identical and is caught only by the step bound below. Checked in
    # the other order the step bound fired for both and the plan rule was never exercised.
    #
    # `prune` is exempt on purpose: its expired-row delete scans by design, and it is
    # background maintenance rather than a request the user is blocked on.
    for work in measurements:
        if work.label == "prune":
            continue
        plans = _chat_message_query_plans(store, work.statements)
        assert plans, {"no_planned_statements": work.label, "statements": list(work.statements)}
        for statement, details in plans.items():
            for detail in details:
                if not CHAT_MESSAGE_PLAN_LINE.search(detail):
                    continue
                assert any(allowed in detail for allowed in CHAT_MESSAGE_ALLOWED_ACCESS), {
                    "unexpected_access_path": work.label,
                    "statement": statement,
                    "plan_line": detail,
                    "plan": list(details),
                    "allowed": list(CHAT_MESSAGE_ALLOWED_ACCESS),
                    "evidence": evidence,
                }

    for work in measurements:
        step_limit = CHAT_STORE_WORK_STEP_LIMITS[work.label] * CHAT_HARD_ROW_LIMIT
        assert work.work_steps <= step_limit, {
            "complexity_regression": work.label,
            "work_steps": work.work_steps,
            "step_limit": step_limit,
            "steps_per_row": round(work.work_steps / CHAT_HARD_ROW_LIMIT, 3),
            "evidence": evidence,
        }
        assert len(work.statements) <= CHAT_STORE_STATEMENT_LIMITS[work.label], {
            "statement_regression": work.label,
            "statements": list(work.statements),
            "evidence": evidence,
        }

    meter.detach()


def test_chat_store_paging_sql_reads_the_real_implementation_and_avoids_offset():
    """No OFFSET paging, asserted against the file that actually defines ChatStore.

    The previous form of this check read the relative path `yolomux_lib/chat_store.py`,
    which is a six-line `sys.modules` alias containing no SQL, so it could not fail and it
    additionally depended on pytest's working directory. The source path here is derived
    from the class under test, and the guard below fails if that file ever stops containing
    the store's SQL.
    """

    source = CHAT_STORE_SOURCE_PATH.read_text(encoding="utf-8")
    assert CHAT_STORE_SOURCE_PATH.name == "chat_store.py"
    assert "FROM chat_messages" in source, {"scanned_file_has_no_store_sql": str(CHAT_STORE_SOURCE_PATH)}
    assert "def page_before" in source, {"scanned_file_does_not_define_paging": str(CHAT_STORE_SOURCE_PATH)}
    assert " OFFSET " not in source.upper(), {"offset_paging_reintroduced": str(CHAT_STORE_SOURCE_PATH)}


def test_chat_store_work_meter_distinguishes_indexed_lookup_from_full_scan(tmp_path):
    """Negative control for the counter the bounded-work assertions depend on.

    If the progress handler were never installed, or counted a constant, every work bound in
    `test_chat_store_hard_ceiling_payload_and_bounded_work` would pass while measuring
    nothing. A keyset context lookup and a full body scan over the same rows must therefore
    differ by a wide, stable margin.

    The finer granularity here is the point of the test rather than an inconvenience: an
    indexed lookup over 5,000 rows costs under 1,000 virtual-machine steps, so at the
    default granularity it is genuinely below the counter's resolution and reads as zero.
    The row-ceiling node's operations are 400,000 steps and up, three orders of magnitude
    clear of that floor, which is why it can keep the cheaper default.
    """

    rows = 5_000
    store = ChatStore(tmp_path / "meter.sqlite3", clock=lambda: 10_000.0)
    store.fts_mode
    connection = sqlite3.connect(store.path)
    connection.executemany(
        """
        INSERT INTO chat_messages(created_at_utc, room_id, username, sender_instance_id, client_message_uuid, body, is_question)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (9_000.0 + index, "global", "u", "b", f"m-{index}", f"meter row {index}", 0)
            for index in range(rows)
        ),
    )
    connection.commit()
    connection.close()

    meter = _ChatStoreWorkMeter(store, granularity=10)
    _context, lookup = meter.measure("context", lambda: store.context(message_id=rows // 2, before=0, after=0, retention_days=365))
    _scan, scan = meter.measure("scan", lambda: store.search(query="row 4321", limit=1, retention_days=365))
    meter.detach()

    assert lookup.work_steps > 0 and scan.work_steps > 0, {"lookup": lookup.evidence(), "scan": scan.evidence()}
    assert scan.work_steps >= 10 * lookup.work_steps, {
        "counter_does_not_track_real_work": True,
        "indexed_lookup": lookup.evidence(),
        "full_scan": scan.evidence(),
    }


CHAT_STORE_CERTIFICATION_SAMPLES = 30
# Inherited unscaled from the fused assertion this node replaces. It was never a per
# operation figure there; the certification phase owner must ratify it per operation, and
# must decide whether p95, p99 or the maximum is the user-facing requirement.
CHAT_STORE_OPERATION_WALL_CEILING_MS = 250.0

# Admission and host fitness both come from the one owner in tests/latency_calibration.py. This
# node used to carry a private precondition on os.getloadavg()[0], a one-minute decaying estimator
# that still reports the parallel lanes the certification phase has just retired: measured on
# keivenc-linux1, 20 s after 40 spinners exited it still read 35.34 against its own 16.0 limit
# while the windowed owner measured procs_running p75 7 and cpu some-stall 0.0074 and correctly
# qualified the host. It vetoed 2 of 6 otherwise-green gate runs at 17.31 and 17.52. The ceiling
# below is unchanged and is never scaled by any host measurement.
certification_phase_only = latency_calibration.certification_phase_fixture(latency_calibration.CHAT_LATENCY_CERTIFICATION_ENV)


def test_chat_store_operation_wall_latency_certification(certification_phase_only, tmp_path, request):
    """Certify the 250 ms wall claim per operation, with enough samples to mean something.

    Requirements this unit places on the phase that runs it, none of which the parallel gate
    can supply:

    1. Exclusive host. Nothing else may run, because wall time measured next to other xdist
       workers reports their I/O and the scheduler, not this product.
    2. A quiescent disk, not merely an idle CPU. Reproducing the fused assertion's red
       needed I/O and page-cache contention: 160 spinning processes at host load 50 left it
       green, while ten workers churning large SQLite files pushed it to 578, 307 and
       1108 ms. Whoever qualifies the host must gate on storage, not just cores - which is why
       latency_calibration.HOST_QUALIFICATION_LIMITS gates on disk busy fraction and PSI io
       stall as well as on CPU. Disk in-flight depth was retired to evidence-only: on this
       kernel it counts device-mapper bios, its post-lane population inverts against real
       saturation, and os.sync() manufactures the burst the next probe would refuse on.
    3. A qualified host, decided by `certification_phase_only` before this node builds anything,
       and failed closed. The ceiling is never scaled by host load: a slower machine must not
       authorise a slower product. An unqualified host is NOT CERTIFIABLE with its raw evidence,
       never a skip and never a widened budget.
    4. Serial execution of this node alone, not inside a parallel lane.
    5. `-e YOLOMUX_CHAT_LATENCY_CERTIFICATION` in docker/run-tests.sh, per the skip reason the
       shared fixture prints. Without it this node cannot be switched on at all and it fails by
       skipping, which is the quiet failure mode this whole split exists to remove.

    Each operation is certified separately against the same ceiling, over
    CHAT_STORE_CERTIFICATION_SAMPLES samples, so the reported p95 is a real percentile of
    one distribution rather than the maximum of five different operations. Cold-cache first
    touch is recorded separately as evidence, not folded into the certified statistic: it is
    a distinct start-up claim the phase owner must decide on. Measured here at the row
    ceiling, a cold `page_before` cost 365 ms against 58-72 ms warm on the same quiet host.
    """

    store, _now, recent_start = _build_chat_store_at_row_ceiling(tmp_path / "certification.sqlite3")
    target_id = CHAT_HARD_ROW_LIMIT - 1
    expired_at = recent_start - 2
    readers = iter(range(1_000_000))

    def prune_once() -> Any:
        store.insert_message(
            username="expired-user",
            sender_instance_id="expired-browser",
            client_message_uuid=f"expired-{next(readers)}",
            body="expired fixture",
            is_question=False,
            created_at_utc=expired_at,
        )
        return store.prune(retention_days=7)

    operations: dict[str, Callable[[], Any]] = {
        "bootstrap": lambda: store.bootstrap(username=f"reader-{next(readers)}", retention_days=7),
        "page_before": lambda: store.page_before(limit=50, retention_days=7),
        "context": lambda: store.context(message_id=target_id, before=3, after=3, retention_days=7),
        "search": lambda: store.search(query="👩‍💻 benchmark", limit=20, retention_days=7),
        "prune": prune_once,
    }

    report: dict[str, dict[str, float]] = {}
    for label, operation in operations.items():
        cold_started = time.perf_counter()
        operation()
        cold_ms = (time.perf_counter() - cold_started) * 1000
        samples = []
        for _sample in range(CHAT_STORE_CERTIFICATION_SAMPLES):
            started = time.perf_counter()
            operation()
            samples.append((time.perf_counter() - started) * 1000)
        ordered = sorted(samples)
        report[label] = {
            "cold_first_touch_ms": round(cold_ms, 3),
            "p50_ms": round(ordered[max(0, math.ceil(len(ordered) * 0.50) - 1)], 3),
            "p95_ms": round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 3),
            "max_ms": round(ordered[-1], 3),
            "samples": len(ordered),
        }

    verdicts = [
        {
            **statistics,
            **latency_calibration.fixed_ceiling_verdict(
                label=f"chat store {label}",
                raw_measured_ms=statistics["p95_ms"],
                ceiling_ms=CHAT_STORE_OPERATION_WALL_CEILING_MS,
                statistic="p95",
            ),
            "operation": label,
        }
        for label, statistics in report.items()
    ]
    certified = latency_calibration.certify_verdicts(
        nodeid=request.node.nodeid,
        label="chat-store-operation-wall-latency",
        verdicts=verdicts,
        qualification=certification_phase_only,
        extra_evidence={
            "ceiling_ms": CHAT_STORE_OPERATION_WALL_CEILING_MS,
            "samples_per_operation": CHAT_STORE_CERTIFICATION_SAMPLES,
            "report": report,
        },
    )
    print(f"chat store certification: {report}; artifact={certified['artifact']}")
