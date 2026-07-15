# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Concurrent, durable storage for the global YO!chat room."""

from __future__ import annotations

import ipaddress
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterator

from .atomic_file import atomic_write_text
from .atomic_file import begin_wal_migration
from .atomic_file import file_lock
from .atomic_file import open_wal_database
from .common import STATE_DIR


CHAT_DATABASE_PATH = STATE_DIR / "yochat.sqlite3"
CHAT_HISTORY_FILE_VERSION = 1
CHAT_HISTORY_FILE_SUFFIX = ".jsonl"
CHAT_SCHEMA_VERSION = 3
CHAT_ROOM_ID = "global"
CHAT_TYPING_LEASE_SECONDS = 5.0
CHAT_MESSAGE_BODY_MAX_BYTES = 8 * 1024
CHAT_DEFAULT_RETENTION_DAYS = 7
CHAT_RETENTION_MIN_DAYS = 1
CHAT_RETENTION_MAX_DAYS = 365
CHAT_HARD_ROW_LIMIT = 100_000
CHAT_PAGE_LIMIT = 50
CHAT_PAGE_LIMIT_MAX = 200
CHAT_CONTEXT_LIMIT_MAX = 25
CHAT_SEARCH_LIMIT_MAX = 100
CHAT_PRUNE_BATCH_ROWS = 1_000
CHAT_PRUNE_INTERVAL_SECONDS = 60 * 60
CHAT_BUSY_TIMEOUT_MS = 5_000
CHAT_FTS_MODE = "fts5"
CHAT_LIKE_FALLBACK_MODE = "like-fallback"


class ChatStoreError(RuntimeError):
    """Base error for a chat database operation that cannot be completed safely."""


class ChatStoreMigrationError(ChatStoreError):
    """The on-disk schema is newer, corrupt, or failed to migrate."""


class ChatStoreValidationError(ChatStoreError):
    """An input violates the storage contract."""


@dataclass(frozen=True)
class ChatMessage:
    id: int
    created_at_utc: float
    room_id: str
    username: str
    sender_ip: str
    sender_instance_id: str
    client_message_uuid: str
    body: str
    is_question: bool


@dataclass(frozen=True)
class ChatReadCursor:
    room_id: str
    username: str
    read_up_to_id: int
    updated_at_utc: float


@dataclass(frozen=True)
class ChatTypingLease:
    room_id: str
    username: str
    browser_instance_id: str
    expires_at_utc: float


@dataclass(frozen=True)
class ChatMessagePage:
    messages: tuple[ChatMessage, ...]
    older_cursor: int | None
    has_more: bool


@dataclass(frozen=True)
class ChatMessageContext:
    target: ChatMessage
    before: tuple[ChatMessage, ...]
    after: tuple[ChatMessage, ...]


@dataclass(frozen=True)
class ChatSearchHit:
    message: ChatMessage
    context: ChatMessageContext


@dataclass(frozen=True)
class ChatSearchPage:
    hits: tuple[ChatSearchHit, ...]
    next_cursor: int | None
    has_more: bool
    mode: str


@dataclass(frozen=True)
class ChatBootstrap:
    latest_message_id: int
    read_cursor: ChatReadCursor
    unread_messages: tuple[ChatMessage, ...]
    typing: tuple[ChatTypingLease, ...]
    first_registration: bool
    has_more_older: bool


@dataclass(frozen=True)
class ChatPruneResult:
    deleted_expired: int
    deleted_overflow: int
    remaining_rows: int
    ran: bool = True


def _bounded_integer(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _message_from_row(row: sqlite3.Row) -> ChatMessage:
    return ChatMessage(
        id=int(row["id"]),
        created_at_utc=float(row["created_at_utc"]),
        room_id=str(row["room_id"]),
        username=str(row["username"]),
        sender_ip=str(row["sender_ip"]),
        sender_instance_id=str(row["sender_instance_id"]),
        client_message_uuid=str(row["client_message_uuid"]),
        body=str(row["body"]),
        is_question=bool(row["is_question"]),
    )


def _cursor_from_row(row: sqlite3.Row) -> ChatReadCursor:
    return ChatReadCursor(
        room_id=str(row["room_id"]),
        username=str(row["username"]),
        read_up_to_id=int(row["read_up_to_id"]),
        updated_at_utc=float(row["updated_at_utc"]),
    )


def _typing_from_row(row: sqlite3.Row) -> ChatTypingLease:
    return ChatTypingLease(
        room_id=str(row["room_id"]),
        username=str(row["username"]),
        browser_instance_id=str(row["browser_instance_id"]),
        expires_at_utc=float(row["expires_at_utc"]),
    )


class ChatStore:
    def __init__(
        self,
        path: Path = CHAT_DATABASE_PATH,
        *,
        clock: Callable[[], float] = time.time,
        fts_preference: str = "auto",
        history_dir: Path | None = None,
    ):
        self.path = Path(path)
        self.history_dir = Path(history_dir) if history_dir is not None else self.path.parent / f"{self.path.stem}-history"
        self.history_lock_path = self.history_dir / "journal"
        self.clock = clock
        self.fts_preference = CHAT_LIKE_FALLBACK_MODE if fts_preference == CHAT_LIKE_FALLBACK_MODE else "auto"
        self._fts_mode = CHAT_LIKE_FALLBACK_MODE
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @property
    def fts_mode(self) -> str:
        self._initialize()
        return self._fts_mode

    def _raw_connection(self) -> sqlite3.Connection:
        connection = open_wal_database(self.path, CHAT_BUSY_TIMEOUT_MS, row_factory=sqlite3.Row)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._initialize()
        connection = self._raw_connection()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            connection: sqlite3.Connection | None = None
            try:
                connection = self._raw_connection()
                version = begin_wal_migration(connection)
                if version > CHAT_SCHEMA_VERSION:
                    raise ChatStoreMigrationError(
                        f"YO!chat database schema {version} is newer than supported schema {CHAT_SCHEMA_VERSION}"
                    )
                if version == 0:
                    self._create_schema(connection)
                    connection.execute(f"PRAGMA user_version = {CHAT_SCHEMA_VERSION}")
                elif version == 1:
                    connection.execute("ALTER TABLE chat_messages ADD COLUMN sender_ip TEXT NOT NULL DEFAULT ''")
                    version = 2
                if version == 2:
                    self._migrate_read_cursors_to_person(connection)
                    connection.execute(f"PRAGMA user_version = {CHAT_SCHEMA_VERSION}")
                elif version != 0 and version != CHAT_SCHEMA_VERSION:
                    raise ChatStoreMigrationError(f"unsupported YO!chat database schema {version}")
                self._fts_mode = self._ensure_search_schema(connection)
                connection.commit()
                self._backfill_history_files(connection)
                self._initialized = True
            except ChatStoreError:
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise
            except sqlite3.DatabaseError as error:
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise ChatStoreMigrationError(f"cannot initialize YO!chat database: {error}") from error
            finally:
                if connection is not None:
                    connection.close()

    @staticmethod
    def _history_record(message: ChatMessage) -> dict[str, Any]:
        return {
            "version": CHAT_HISTORY_FILE_VERSION,
            "id": message.id,
            "created_at_utc": message.created_at_utc,
            "room_id": message.room_id,
            "username": message.username,
            "sender_ip": message.sender_ip,
            "sender_instance_id": message.sender_instance_id,
            "client_message_uuid": message.client_message_uuid,
            "body": message.body,
            "is_question": message.is_question,
        }

    @staticmethod
    def _history_day(created_at_utc: float) -> str:
        return datetime.fromtimestamp(created_at_utc, timezone.utc).date().isoformat()

    def _history_path(self, created_at_utc: float) -> Path:
        return self.history_dir / f"{self._history_day(created_at_utc)}{CHAT_HISTORY_FILE_SUFFIX}"

    @staticmethod
    def _history_text(messages: list[ChatMessage]) -> str:
        return "".join(
            json.dumps(ChatStore._history_record(message), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for message in messages
        )

    def _dated_history_paths(self) -> list[Path]:
        try:
            return sorted(
                path
                for path in self.history_dir.glob(f"????-??-??{CHAT_HISTORY_FILE_SUFFIX}")
                if path.is_file()
            )
        except OSError as error:
            raise ChatStoreError(f"cannot list YO!chat history files: {error}") from error

    def _backfill_history_files(self, connection: sqlite3.Connection) -> None:
        """Merge committed rows missing from dated journals without deleting journal-only recovery data."""
        try:
            with file_lock(self.history_lock_path, dir_mode=0o700):
                rows = list(connection.execute("SELECT * FROM chat_messages ORDER BY id ASC"))
                by_day: dict[str, list[ChatMessage]] = {}
                for row in rows:
                    message = _message_from_row(row)
                    by_day.setdefault(self._history_day(message.created_at_utc), []).append(message)
                for day, messages in by_day.items():
                    target = self.history_dir / f"{day}{CHAT_HISTORY_FILE_SUFFIX}"
                    existing_lines: list[str] = []
                    existing_ids: set[int] = set()
                    try:
                        existing_lines = target.read_text(encoding="utf-8").splitlines()
                    except FileNotFoundError:
                        pass
                    for line in existing_lines:
                        try:
                            existing_ids.add(int(json.loads(line)["id"]))
                        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                            continue
                    missing = [message for message in messages if message.id not in existing_ids]
                    if not missing:
                        target.chmod(0o600)
                        continue
                    merged_text = "\n".join(existing_lines)
                    if merged_text:
                        merged_text += "\n"
                    merged_text += self._history_text(missing)
                    atomic_write_text(target, merged_text, mode=0o600)
        except OSError as error:
            raise ChatStoreError(f"cannot backfill YO!chat history files: {error}") from error

    def _prune_history_files(self, cutoff: float) -> None:
        """Apply the same exact timestamp cutoff to every dated journal already on disk."""
        try:
            with file_lock(self.history_lock_path, dir_mode=0o700):
                for path in self._dated_history_paths():
                    retained: list[str] = []
                    for line in path.read_text(encoding="utf-8").splitlines():
                        try:
                            record = json.loads(line)
                            if float(record["created_at_utc"]) >= cutoff:
                                retained.append(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                            continue
                    if retained:
                        atomic_write_text(path, "\n".join(retained) + "\n", mode=0o600)
                    else:
                        path.unlink()
        except OSError as error:
            raise ChatStoreError(f"cannot prune YO!chat history files: {error}") from error

    def _append_history_message(self, message: ChatMessage) -> None:
        """Append one committed message once; retries and concurrent servers stay idempotent."""
        target = self._history_path(message.created_at_utc)
        try:
            with file_lock(self.history_lock_path, dir_mode=0o700):
                lines: list[str] = []
                message_ids: set[int] = set()
                try:
                    lines = target.read_text(encoding="utf-8").splitlines()
                except FileNotFoundError:
                    pass
                for line in lines:
                    try:
                        record = json.loads(line)
                        message_ids.add(int(record["id"]))
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
                if message.id in message_ids:
                    target.chmod(0o600)
                    return
                lines.append(json.dumps(self._history_record(message), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                atomic_write_text(target, "\n".join(lines) + "\n", mode=0o600)
        except OSError as error:
            raise ChatStoreError(f"cannot append YO!chat history file: {error}") from error

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = (
            """CREATE TABLE chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at_utc REAL NOT NULL,
                room_id TEXT NOT NULL,
                username TEXT NOT NULL,
                sender_ip TEXT NOT NULL DEFAULT '',
                sender_instance_id TEXT NOT NULL,
                client_message_uuid TEXT NOT NULL,
                body TEXT NOT NULL,
                is_question INTEGER NOT NULL CHECK (is_question IN (0, 1)),
                UNIQUE (room_id, username, sender_instance_id, client_message_uuid)
            )""",
            "CREATE INDEX chat_messages_room_time ON chat_messages(room_id, created_at_utc, id)",
            """CREATE TABLE chat_read_cursors (
                room_id TEXT NOT NULL,
                username TEXT NOT NULL,
                read_up_to_id INTEGER NOT NULL DEFAULT 0,
                updated_at_utc REAL NOT NULL,
                PRIMARY KEY (room_id, username)
            )""",
            """CREATE TABLE chat_typing_leases (
                room_id TEXT NOT NULL,
                username TEXT NOT NULL,
                browser_instance_id TEXT NOT NULL,
                expires_at_utc REAL NOT NULL,
                updated_at_utc REAL NOT NULL,
                PRIMARY KEY (room_id, username, browser_instance_id)
            )""",
            "CREATE INDEX chat_typing_room_expiry ON chat_typing_leases(room_id, expires_at_utc)",
            """CREATE TABLE chat_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""",
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _migrate_read_cursors_to_person(connection: sqlite3.Connection) -> None:
        """Collapse legacy per-browser read cursors to each person's furthest acknowledgement."""
        legacy_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chat_read_cursors'"
        ).fetchone() is not None
        if legacy_table:
            connection.execute("ALTER TABLE chat_read_cursors RENAME TO chat_read_cursors_v2")
        connection.execute(
            """CREATE TABLE chat_read_cursors (
                room_id TEXT NOT NULL,
                username TEXT NOT NULL,
                read_up_to_id INTEGER NOT NULL DEFAULT 0,
                updated_at_utc REAL NOT NULL,
                PRIMARY KEY (room_id, username)
            )"""
        )
        if legacy_table:
            connection.execute(
                """INSERT INTO chat_read_cursors(room_id, username, read_up_to_id, updated_at_utc)
                SELECT room_id, username, MAX(read_up_to_id), MAX(updated_at_utc)
                FROM chat_read_cursors_v2
                GROUP BY room_id, username"""
            )
            connection.execute("DROP TABLE chat_read_cursors_v2")

    def _ensure_search_schema(self, connection: sqlite3.Connection) -> str:
        if self.fts_preference == CHAT_LIKE_FALLBACK_MODE:
            return CHAT_LIKE_FALLBACK_MODE
        try:
            statements = (
                """CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
                    body,
                    content='chat_messages',
                    content_rowid='id',
                    tokenize='unicode61'
                )""",
                """CREATE TRIGGER IF NOT EXISTS chat_messages_fts_insert AFTER INSERT ON chat_messages BEGIN
                    INSERT INTO chat_messages_fts(rowid, body) VALUES (new.id, new.body);
                END""",
                """CREATE TRIGGER IF NOT EXISTS chat_messages_fts_delete AFTER DELETE ON chat_messages BEGIN
                    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
                END""",
                """CREATE TRIGGER IF NOT EXISTS chat_messages_fts_update AFTER UPDATE OF body ON chat_messages BEGIN
                    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
                    INSERT INTO chat_messages_fts(rowid, body) VALUES (new.id, new.body);
                END""",
            )
            for statement in statements:
                connection.execute(statement)
            existing_rows = int(connection.execute("SELECT count(*) FROM chat_messages_fts").fetchone()[0])
            message_rows = int(connection.execute("SELECT count(*) FROM chat_messages").fetchone()[0])
            if existing_rows != message_rows:
                connection.execute("INSERT INTO chat_messages_fts(chat_messages_fts) VALUES ('rebuild')")
            return CHAT_FTS_MODE
        except sqlite3.OperationalError as error:
            if "fts5" not in str(error).lower():
                raise
            return CHAT_LIKE_FALLBACK_MODE

    @staticmethod
    def _validate_identity(value: str, field: str) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 128:
            raise ChatStoreValidationError(f"invalid {field}")
        return text

    @staticmethod
    def _validate_body(body: str) -> str:
        text = str(body)
        if not text:
            raise ChatStoreValidationError("message body is empty")
        if len(text.encode("utf-8")) > CHAT_MESSAGE_BODY_MAX_BYTES:
            raise ChatStoreValidationError("message body exceeds 8 KiB")
        return text

    @staticmethod
    def _validate_sender_ip(sender_ip: str) -> str:
        text = str(sender_ip or "").strip()
        if not text:
            return ""
        try:
            return str(ipaddress.ip_address(text))
        except ValueError as error:
            raise ChatStoreValidationError("invalid sender IP") from error

    @staticmethod
    def _retention_days(value: Any) -> int:
        return _bounded_integer(value, CHAT_DEFAULT_RETENTION_DAYS, CHAT_RETENTION_MIN_DAYS, CHAT_RETENTION_MAX_DAYS)

    def _cutoff(self, retention_days: Any) -> float:
        return self.clock() - (self._retention_days(retention_days) * 24 * 60 * 60)

    @staticmethod
    def _latest_message_id(connection: sqlite3.Connection, room_id: str, cutoff: float) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(id), 0) AS id FROM chat_messages WHERE room_id = ? AND created_at_utc >= ?",
            (room_id, cutoff),
        ).fetchone()
        return int(row["id"])

    def insert_message(
        self,
        *,
        username: str,
        sender_ip: str = "",
        sender_instance_id: str,
        client_message_uuid: str,
        body: str,
        is_question: bool,
        room_id: str = CHAT_ROOM_ID,
        created_at_utc: float | None = None,
    ) -> tuple[ChatMessage, bool]:
        clean_username = self._validate_identity(username, "username")
        clean_sender_ip = self._validate_sender_ip(sender_ip)
        clean_instance = self._validate_identity(sender_instance_id, "sender instance ID")
        clean_uuid = self._validate_identity(client_message_uuid, "client message UUID")
        clean_room = self._validate_identity(room_id, "room ID")
        clean_body = self._validate_body(body)
        timestamp = self.clock() if created_at_utc is None else float(created_at_utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM chat_messages
                WHERE room_id = ? AND username = ? AND sender_instance_id = ? AND client_message_uuid = ?
                """,
                (clean_room, clean_username, clean_instance, clean_uuid),
            ).fetchone()
            if existing is not None:
                connection.commit()
                message = _message_from_row(existing)
                self._append_history_message(message)
                return message, False
            cursor = connection.execute(
                """
                INSERT INTO chat_messages(
                    created_at_utc, room_id, username, sender_ip, sender_instance_id,
                    client_message_uuid, body, is_question
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (timestamp, clean_room, clean_username, clean_sender_ip, clean_instance, clean_uuid, clean_body, int(bool(is_question))),
            )
            message_id = int(cursor.lastrowid)
            connection.execute(
                "DELETE FROM chat_typing_leases WHERE room_id = ? AND username = ? AND browser_instance_id = ?",
                (clean_room, clean_username, clean_instance),
            )
            row = connection.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
            connection.commit()
            message = _message_from_row(row)
            self._append_history_message(message)
            return message, True

    def bootstrap(
        self,
        *,
        username: str,
        room_id: str = CHAT_ROOM_ID,
        retention_days: Any = CHAT_DEFAULT_RETENTION_DAYS,
        limit: Any = CHAT_PAGE_LIMIT,
    ) -> ChatBootstrap:
        clean_username = self._validate_identity(username, "username")
        clean_room = self._validate_identity(room_id, "room ID")
        page_limit = _bounded_integer(limit, CHAT_PAGE_LIMIT, 1, CHAT_PAGE_LIMIT_MAX)
        now = self.clock()
        cutoff = self._cutoff(retention_days)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest_id = self._latest_message_id(connection, clean_room, cutoff)
            row = connection.execute(
                "SELECT * FROM chat_read_cursors WHERE room_id = ? AND username = ?",
                (clean_room, clean_username),
            ).fetchone()
            first_registration = row is None
            if first_registration:
                connection.execute(
                    """
                    INSERT INTO chat_read_cursors(room_id, username, read_up_to_id, updated_at_utc)
                    VALUES (?, ?, ?, ?)
                    """,
                    (clean_room, clean_username, latest_id, now),
                )
                row = connection.execute(
                    "SELECT * FROM chat_read_cursors WHERE room_id = ? AND username = ?",
                    (clean_room, clean_username),
                ).fetchone()
                unread_rows: list[sqlite3.Row] = []
            else:
                unread_rows = list(
                    connection.execute(
                        """
                        SELECT * FROM chat_messages
                        WHERE room_id = ? AND id > ? AND created_at_utc >= ?
                        ORDER BY id ASC LIMIT ?
                        """,
                        (clean_room, int(row["read_up_to_id"]), cutoff, page_limit),
                    )
                )
            connection.execute("DELETE FROM chat_typing_leases WHERE room_id = ? AND expires_at_utc <= ?", (clean_room, now))
            typing_rows = list(
                connection.execute(
                    "SELECT * FROM chat_typing_leases WHERE room_id = ? AND expires_at_utc > ? ORDER BY username, browser_instance_id",
                    (clean_room, now),
                )
            )
            covered_start = int(unread_rows[0]["id"]) if unread_rows else latest_id + 1
            has_more_older = connection.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM chat_messages
                    WHERE room_id = ? AND id < ? AND created_at_utc >= ?
                ) AS present
                """,
                (clean_room, covered_start, cutoff),
            ).fetchone()["present"]
            connection.commit()
            return ChatBootstrap(
                latest_message_id=latest_id,
                read_cursor=_cursor_from_row(row),
                unread_messages=tuple(_message_from_row(item) for item in unread_rows),
                typing=tuple(_typing_from_row(item) for item in typing_rows),
                first_registration=first_registration,
                has_more_older=bool(has_more_older),
            )

    def read_up_to(
        self,
        *,
        username: str,
        message_id: Any,
        room_id: str = CHAT_ROOM_ID,
    ) -> ChatReadCursor:
        clean_username = self._validate_identity(username, "username")
        clean_room = self._validate_identity(room_id, "room ID")
        try:
            target_id = max(0, int(message_id))
        except (TypeError, ValueError) as error:
            raise ChatStoreValidationError("invalid message ID") from error
        now = self.clock()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest_row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS id FROM chat_messages WHERE room_id = ?",
                (clean_room,),
            ).fetchone()
            latest_id = int(latest_row["id"])
            if target_id > latest_id:
                connection.rollback()
                raise ChatStoreValidationError("read cursor exceeds current chat tail")
            connection.execute(
                """
                INSERT INTO chat_read_cursors(room_id, username, read_up_to_id, updated_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(room_id, username) DO UPDATE SET
                    read_up_to_id = MAX(chat_read_cursors.read_up_to_id, excluded.read_up_to_id),
                    updated_at_utc = CASE
                        WHEN excluded.read_up_to_id > chat_read_cursors.read_up_to_id THEN excluded.updated_at_utc
                        ELSE chat_read_cursors.updated_at_utc
                    END
                """,
                (clean_room, clean_username, target_id, now),
            )
            row = connection.execute(
                "SELECT * FROM chat_read_cursors WHERE room_id = ? AND username = ?",
                (clean_room, clean_username),
            ).fetchone()
            connection.commit()
            return _cursor_from_row(row)

    def set_typing(
        self,
        *,
        username: str,
        browser_instance_id: str,
        typing: bool,
        room_id: str = CHAT_ROOM_ID,
    ) -> tuple[ChatTypingLease, ...]:
        clean_username = self._validate_identity(username, "username")
        clean_instance = self._validate_identity(browser_instance_id, "browser instance ID")
        clean_room = self._validate_identity(room_id, "room ID")
        now = self.clock()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM chat_typing_leases WHERE room_id = ? AND expires_at_utc <= ?", (clean_room, now))
            if typing:
                connection.execute(
                    """
                    INSERT INTO chat_typing_leases(room_id, username, browser_instance_id, expires_at_utc, updated_at_utc)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(room_id, username, browser_instance_id) DO UPDATE SET
                        expires_at_utc = excluded.expires_at_utc,
                        updated_at_utc = excluded.updated_at_utc
                    """,
                    (clean_room, clean_username, clean_instance, now + CHAT_TYPING_LEASE_SECONDS, now),
                )
            else:
                connection.execute(
                    "DELETE FROM chat_typing_leases WHERE room_id = ? AND username = ? AND browser_instance_id = ?",
                    (clean_room, clean_username, clean_instance),
                )
            rows = list(
                connection.execute(
                    "SELECT * FROM chat_typing_leases WHERE room_id = ? AND expires_at_utc > ? ORDER BY username, browser_instance_id",
                    (clean_room, now),
                )
            )
            connection.commit()
            return tuple(_typing_from_row(row) for row in rows)

    def typing_snapshot(self, *, room_id: str = CHAT_ROOM_ID) -> tuple[ChatTypingLease, ...]:
        clean_room = self._validate_identity(room_id, "room ID")
        now = self.clock()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM chat_typing_leases WHERE room_id = ? AND expires_at_utc <= ?", (clean_room, now))
            rows = list(
                connection.execute(
                    "SELECT * FROM chat_typing_leases WHERE room_id = ? AND expires_at_utc > ? ORDER BY username, browser_instance_id",
                    (clean_room, now),
                )
            )
            connection.commit()
            return tuple(_typing_from_row(row) for row in rows)

    def page_before(
        self,
        *,
        before_id: Any = None,
        limit: Any = CHAT_PAGE_LIMIT,
        room_id: str = CHAT_ROOM_ID,
        retention_days: Any = CHAT_DEFAULT_RETENTION_DAYS,
    ) -> ChatMessagePage:
        clean_room = self._validate_identity(room_id, "room ID")
        page_limit = _bounded_integer(limit, CHAT_PAGE_LIMIT, 1, CHAT_PAGE_LIMIT_MAX)
        cutoff = self._cutoff(retention_days)
        try:
            cursor = int(before_id) if before_id is not None else 2**63 - 1
        except (TypeError, ValueError) as error:
            raise ChatStoreValidationError("invalid older cursor") from error
        with self._connection() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE room_id = ? AND id < ? AND created_at_utc >= ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (clean_room, cursor, cutoff, page_limit + 1),
                )
            )
        has_more = len(rows) > page_limit
        selected = rows[:page_limit]
        messages = tuple(_message_from_row(row) for row in reversed(selected))
        older_cursor = messages[0].id if has_more and messages else None
        return ChatMessagePage(messages=messages, older_cursor=older_cursor, has_more=has_more)

    def messages_after(
        self,
        *,
        after_id: Any,
        limit: Any = CHAT_PAGE_LIMIT_MAX,
        room_id: str = CHAT_ROOM_ID,
        retention_days: Any = CHAT_DEFAULT_RETENTION_DAYS,
    ) -> tuple[ChatMessage, ...]:
        clean_room = self._validate_identity(room_id, "room ID")
        page_limit = _bounded_integer(limit, CHAT_PAGE_LIMIT_MAX, 1, CHAT_PAGE_LIMIT_MAX)
        cutoff = self._cutoff(retention_days)
        try:
            cursor = max(0, int(after_id))
        except (TypeError, ValueError) as error:
            raise ChatStoreValidationError("invalid newer cursor") from error
        with self._connection() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE room_id = ? AND id > ? AND created_at_utc >= ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (clean_room, cursor, cutoff, page_limit),
                )
            )
        return tuple(_message_from_row(row) for row in rows)

    def context(
        self,
        *,
        message_id: Any,
        before: Any = 3,
        after: Any = 3,
        room_id: str = CHAT_ROOM_ID,
        retention_days: Any = CHAT_DEFAULT_RETENTION_DAYS,
    ) -> ChatMessageContext | None:
        clean_room = self._validate_identity(room_id, "room ID")
        before_limit = _bounded_integer(before, 3, 0, CHAT_CONTEXT_LIMIT_MAX)
        after_limit = _bounded_integer(after, 3, 0, CHAT_CONTEXT_LIMIT_MAX)
        cutoff = self._cutoff(retention_days)
        try:
            target_id = int(message_id)
        except (TypeError, ValueError) as error:
            raise ChatStoreValidationError("invalid context message ID") from error
        with self._connection() as connection:
            target_row = connection.execute(
                "SELECT * FROM chat_messages WHERE room_id = ? AND id = ? AND created_at_utc >= ?",
                (clean_room, target_id, cutoff),
            ).fetchone()
            if target_row is None:
                return None
            before_rows = list(
                connection.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE room_id = ? AND id < ? AND created_at_utc >= ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (clean_room, target_id, cutoff, before_limit),
                )
            )
            after_rows = list(
                connection.execute(
                    """
                    SELECT * FROM chat_messages
                    WHERE room_id = ? AND id > ? AND created_at_utc >= ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (clean_room, target_id, cutoff, after_limit),
                )
            )
        return ChatMessageContext(
            target=_message_from_row(target_row),
            before=tuple(_message_from_row(row) for row in reversed(before_rows)),
            after=tuple(_message_from_row(row) for row in after_rows),
        )

    @staticmethod
    def _fts_query(text: str) -> str:
        tokens = [token for token in text.replace('"', " ").split() if token]
        return " AND ".join(f'"{token}"' for token in tokens)

    def search(
        self,
        *,
        query: str,
        cursor: Any = None,
        limit: Any = 20,
        context_before: Any = 2,
        context_after: Any = 2,
        room_id: str = CHAT_ROOM_ID,
        retention_days: Any = CHAT_DEFAULT_RETENTION_DAYS,
    ) -> ChatSearchPage:
        clean_room = self._validate_identity(room_id, "room ID")
        clean_query = str(query or "").strip()
        if not clean_query:
            raise ChatStoreValidationError("search query is empty")
        search_limit = _bounded_integer(limit, 20, 1, CHAT_SEARCH_LIMIT_MAX)
        cutoff = self._cutoff(retention_days)
        try:
            before_id = int(cursor) if cursor is not None else 2**63 - 1
        except (TypeError, ValueError) as error:
            raise ChatStoreValidationError("invalid search cursor") from error
        with self._connection() as connection:
            if self.fts_mode == CHAT_FTS_MODE and self._fts_query(clean_query):
                rows = list(
                    connection.execute(
                        """
                        SELECT chat_messages.*,
                               CASE WHEN instr(lower(chat_messages.body), lower(?)) > 0 THEN 0 ELSE 1 END AS match_rank
                        FROM chat_messages
                        WHERE chat_messages.room_id = ?
                          AND chat_messages.id < ?
                          AND chat_messages.created_at_utc >= ?
                          AND (
                            instr(lower(chat_messages.body), lower(?)) > 0
                            OR chat_messages.id IN (
                                SELECT rowid FROM chat_messages_fts WHERE chat_messages_fts MATCH ?
                            )
                          )
                        ORDER BY match_rank ASC, chat_messages.id DESC
                        LIMIT ?
                        """,
                        (clean_query, clean_room, before_id, cutoff, clean_query, self._fts_query(clean_query), search_limit + 1),
                    )
                )
            else:
                escaped = clean_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = list(
                    connection.execute(
                        """
                        SELECT * FROM chat_messages
                        WHERE room_id = ? AND id < ? AND created_at_utc >= ?
                          AND body LIKE ? ESCAPE '\\'
                        ORDER BY id DESC LIMIT ?
                        """,
                        (clean_room, before_id, cutoff, f"%{escaped}%", search_limit + 1),
                    )
                )
        has_more = len(rows) > search_limit
        selected = rows[:search_limit]
        hits: list[ChatSearchHit] = []
        for row in selected:
            context = self.context(
                message_id=int(row["id"]),
                before=context_before,
                after=context_after,
                room_id=clean_room,
                retention_days=retention_days,
            )
            if context is not None:
                hits.append(ChatSearchHit(message=_message_from_row(row), context=context))
        next_cursor = hits[-1].message.id if has_more and hits else None
        return ChatSearchPage(hits=tuple(hits), next_cursor=next_cursor, has_more=has_more, mode=self.fts_mode)

    def prune(
        self,
        *,
        retention_days: Any = CHAT_DEFAULT_RETENTION_DAYS,
        hard_row_limit: Any = CHAT_HARD_ROW_LIMIT,
    ) -> ChatPruneResult:
        cutoff = self._cutoff(retention_days)
        row_limit = _bounded_integer(hard_row_limit, CHAT_HARD_ROW_LIMIT, 1, CHAT_HARD_ROW_LIMIT)
        deleted_expired = 0
        deleted_overflow = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            while True:
                cursor = connection.execute(
                    """
                    DELETE FROM chat_messages WHERE id IN (
                        SELECT id FROM chat_messages
                        WHERE created_at_utc < ?
                        ORDER BY id ASC LIMIT ?
                    )
                    """,
                    (cutoff, CHAT_PRUNE_BATCH_ROWS),
                )
                deleted_expired += max(0, cursor.rowcount)
                if cursor.rowcount < CHAT_PRUNE_BATCH_ROWS:
                    break
            row_count = int(connection.execute("SELECT count(*) FROM chat_messages").fetchone()[0])
            while row_count > row_limit:
                delete_count = min(CHAT_PRUNE_BATCH_ROWS, row_count - row_limit)
                cursor = connection.execute(
                    """
                    DELETE FROM chat_messages WHERE id IN (
                        SELECT id FROM chat_messages ORDER BY id ASC LIMIT ?
                    )
                    """,
                    (delete_count,),
                )
                removed = max(0, cursor.rowcount)
                deleted_overflow += removed
                if removed == 0:
                    break
                row_count -= removed
            connection.execute(
                """
                INSERT INTO chat_metadata(key, value) VALUES ('last_prune_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(self.clock()),),
            )
            for key, increment in (
                ("prune_runs", 1),
                ("prune_deleted_expired", deleted_expired),
                ("prune_deleted_overflow", deleted_overflow),
            ):
                connection.execute(
                    """
                    INSERT INTO chat_metadata(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = CAST(chat_metadata.value AS INTEGER) + CAST(excluded.value AS INTEGER)
                    """,
                    (key, str(increment)),
                )
            connection.commit()
            self._prune_history_files(cutoff)
        return ChatPruneResult(
            deleted_expired=deleted_expired,
            deleted_overflow=deleted_overflow,
            remaining_rows=row_count,
        )

    def prune_if_due(
        self,
        *,
        retention_days: Any = CHAT_DEFAULT_RETENTION_DAYS,
        previous_retention_days: Any = None,
    ) -> ChatPruneResult:
        current_days = self._retention_days(retention_days)
        previous_days = self._retention_days(previous_retention_days) if previous_retention_days is not None else current_days
        now = self.clock()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT value FROM chat_metadata WHERE key = 'last_prune_at'").fetchone()
            try:
                last_prune_at = float(row["value"]) if row is not None else 0.0
            except (TypeError, ValueError):
                last_prune_at = 0.0
            due = current_days < previous_days or now - last_prune_at >= CHAT_PRUNE_INTERVAL_SECONDS
            if not due:
                row_count = int(connection.execute("SELECT count(*) FROM chat_messages").fetchone()[0])
                connection.rollback()
                return ChatPruneResult(deleted_expired=0, deleted_overflow=0, remaining_rows=row_count, ran=False)
            connection.execute(
                """
                INSERT INTO chat_metadata(key, value) VALUES ('last_prune_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(now),),
            )
            connection.commit()
        return self.prune(retention_days=current_days)

    def database_size_bytes(self) -> int:
        self._initialize()
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += self.path.with_name(self.path.name + suffix).stat().st_size
            except OSError:
                continue
        return total

    def diagnostics(self) -> dict[str, Any]:
        now = self.clock()
        with self._connection() as connection:
            message_rows = int(connection.execute("SELECT count(*) FROM chat_messages").fetchone()[0])
            reader_rows = int(connection.execute("SELECT count(*) FROM chat_read_cursors").fetchone()[0])
            typing_rows = int(connection.execute(
                "SELECT count(*) FROM chat_typing_leases WHERE expires_at_utc > ?",
                (now,),
            ).fetchone()[0])
            metadata = {
                str(row["key"]): str(row["value"])
                for row in connection.execute(
                    "SELECT key, value FROM chat_metadata WHERE key IN ('last_prune_at', 'prune_runs', 'prune_deleted_expired', 'prune_deleted_overflow')"
                )
            }
        history_paths = self._dated_history_paths()
        return {
            "schema_version": CHAT_SCHEMA_VERSION,
            "search_mode": self.fts_mode,
            "database_bytes": self.database_size_bytes(),
            "history_files": len(history_paths),
            "history_bytes": sum(path.stat().st_size for path in history_paths),
            "message_rows": message_rows,
            "reader_rows": reader_rows,
            "typing_leases": typing_rows,
            "last_prune_at": float(metadata.get("last_prune_at", 0.0) or 0.0),
            "prune_runs": int(metadata.get("prune_runs", 0) or 0),
            "prune_deleted_expired": int(metadata.get("prune_deleted_expired", 0) or 0),
            "prune_deleted_overflow": int(metadata.get("prune_deleted_overflow", 0) or 0),
        }
