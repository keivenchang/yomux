# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded, read-only usage reads from OpenCode's local SQLite database."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote

from ..infra.atomic_file import atomic_write_text
from ..infra.common import STATE_DIR


DEFAULT_DATABASE_PATH = Path("~/.local/share/opencode/opencode.db").expanduser()
DEFAULT_MAX_SESSIONS = 64
DEFAULT_MAX_PARTS = 2_048
MAX_IDENTIFIER_BYTES = 256
MAX_JSON_BYTES = 1 * 1024 * 1024
DEFAULT_SESSION_MAX_AGE_SECONDS = 24 * 60 * 60
DEFAULT_SESSION_START_SKEW_SECONDS = 5 * 60
DEFAULT_FUTURE_SKEW_SECONDS = 5 * 60
DEFAULT_CURSOR_FILENAME = "stats-current-opencode-cursors.json"
MAX_CURSOR_ENTRIES = 8_192
MAX_SAFE_CURSOR_VALUE = (1 << 53) - 1

_SESSION_COLUMNS = (
    "id", "directory", "agent", "model", "time_created", "time_updated", "cost",
)
_SESSION_COUNTER_COLUMNS = (
    "tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write",
)
_MESSAGE_COLUMNS = ("id", "session_id", "time_created", "time_updated", "data")
_PART_COLUMNS = ("id", "message_id", "session_id", "time_created", "time_updated", "data")
_REQUIRED_COLUMNS = {
    "session": frozenset(_SESSION_COLUMNS),
    "message": frozenset(_MESSAGE_COLUMNS),
    "part": frozenset(_PART_COLUMNS),
}
_TOKEN_DIMENSIONS = ("input", "cache_read", "cache_write", "output", "reasoning")
_ATOM_DIMENSIONS = frozenset({"input", "cache_read", "output"})
TokenDimension = Literal["input", "cache_read", "cache_write", "output", "reasoning"]
MAX_MESSAGE_QUERY_IDS = 256
_STATE_MESSAGE_LIMIT = 32
_STATE_PART_LIMIT = 1
_STATE_INPUT_LIMIT = 1
_STATE_RECENT_SECONDS = 5 * 60
_SESSION_INPUT_COLUMNS = ("id", "session_id", "delivery", "admitted_seq", "promoted_seq", "time_created")
_STATE_REQUIRED_COLUMNS = {
    "session_input": frozenset(_SESSION_INPUT_COLUMNS),
}
_VALID_INPUT_DELIVERIES = frozenset({"queue", "steer"})
_OPENCODE_TITLE_PREFIX = "OC | "


@dataclass(frozen=True, slots=True)
class OpenCodeSession:
    """The session identity and machine-provided model metadata needed by the reader."""

    session_id: str
    directory: str
    model: str
    provider: str
    agent: str
    time_created: float
    time_updated: float
    cumulative_tokens: dict[TokenDimension, int | None] | None = None
    cost: float = 0.0


@dataclass(frozen=True, slots=True)
class OpenCodeUsageComponent:
    """One exact token dimension from one completed ``step-finish`` part."""

    event_id: str
    session_id: str
    message_id: str
    part_id: str
    observed_at: float
    dimension: TokenDimension
    tokens: int
    provider: str
    model: str
    model_evidence: str
    agent_id: str
    telemetry_complete: bool = False
    unsupported_dimensions: tuple[TokenDimension, ...] = ()
    source_revision: str = ""


@dataclass(frozen=True, slots=True)
class OpenCodeReadSuccess:
    session: OpenCodeSession
    components: tuple[OpenCodeUsageComponent, ...]

    status: Literal["ok"] = "ok"


OpenCodeSessionState = Literal["working", "paused", "idle"]
OpenCodeState = Literal["working", "paused", "idle", "unavailable"]


@dataclass(frozen=True, slots=True)
class OpenCodeStateSuccess:
    """A bounded activity fact derived from OpenCode's persisted session state."""

    session: OpenCodeSession
    state: OpenCodeSessionState
    observed_at: float
    evidence: tuple[str, ...]
    status: Literal["ok"] = "ok"


@dataclass(frozen=True, slots=True)
class OpenCodeStateUnavailable:
    reason: str
    status: Literal["unavailable"] = "unavailable"

    @property
    def state(self) -> Literal["unavailable"]:
        return "unavailable"


@dataclass(frozen=True, slots=True)
class OpenCodeStateSchemaMismatch:
    reason: str
    status: Literal["schema-mismatch"] = "schema-mismatch"

    @property
    def state(self) -> Literal["unavailable"]:
        return "unavailable"


@dataclass(frozen=True, slots=True)
class OpenCodeUnavailable:
    reason: str
    status: Literal["unavailable"] = "unavailable"


@dataclass(frozen=True, slots=True)
class OpenCodeSchemaMismatch:
    reason: str
    status: Literal["schema-mismatch"] = "schema-mismatch"


@dataclass(frozen=True, slots=True)
class OpenCodeAmbiguousSession:
    reason: str
    session_ids: tuple[str, ...]
    status: Literal["ambiguous-session"] = "ambiguous-session"


OpenCodeReadResult = OpenCodeReadSuccess | OpenCodeUnavailable | OpenCodeSchemaMismatch | OpenCodeAmbiguousSession
OpenCodeStateReadResult = OpenCodeStateSuccess | OpenCodeStateUnavailable | OpenCodeStateSchemaMismatch


@dataclass(frozen=True, slots=True)
class OpenCodeCursorState:
    """The last committed cumulative snapshot for each source component."""

    values: dict[str, int]
    epochs: dict[str, int] | None = None
    sequences: dict[str, int] | None = None
    presence: dict[str, bool] | None = None
    event_revisions: dict[str, str] | None = None
    database_identity: str = ""


class OpenCodeCursorStore:
    """Persist OpenCode cumulative cursors with a fail-closed append boundary.

    The stats writer and this collector do not share a transaction. A pending journal entry is
    therefore intentionally not guessed at after a process crash: it remains pending and callers
    receive an unavailable result instead of replaying a possibly durable atom.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = None if path is None else Path(path)
        self._lock = threading.RLock()
        self._loaded = False
        self._values: dict[str, int] = {}
        self._epochs: dict[str, int] = {}
        self._sequences: dict[str, int] = {}
        self._presence: dict[str, bool] = {}
        self._event_revisions: dict[str, str] = {}
        self._database_identity = ""
        self._pending = False
        self._pending_values: dict[str, int] | None = None
        self._pending_epochs: dict[str, int] | None = None
        self._pending_sequences: dict[str, int] | None = None
        self._pending_presence: dict[str, bool] | None = None
        self._pending_event_revisions: dict[str, str] | None = None
        self._pending_lock: AbstractContextManager[object] | None = None

    @staticmethod
    def default_path() -> Path:
        return STATE_DIR / DEFAULT_CURSOR_FILENAME

    def state(self) -> OpenCodeCursorState | OpenCodeUnavailable:
        with self._lock:
            if self._pending:
                return OpenCodeUnavailable("cursor-state-pending")
            try:
                if self.path is not None:
                    with _cursor_lock(self.path):
                        self._load_from_disk()
                        self._repair_pending_locked()
                elif not self._loaded:
                    self._loaded = True
            except OSError:
                return OpenCodeUnavailable("cursor-state-unavailable")
            except (TypeError, ValueError, json.JSONDecodeError):
                return OpenCodeUnavailable("cursor-state-malformed")
            return OpenCodeCursorState(
                dict(self._values), dict(self._epochs), dict(self._sequences), dict(self._presence),
                dict(self._event_revisions), self._database_identity,
            )

    def reset_for_database(self, database: Path) -> OpenCodeUnavailable | None:
        """Fence cursors to the current stats database incarnation.

        The cursor sidecar and statsd commit in separate stores. A database replacement can
        therefore leave a valid-looking sidecar claiming atoms that the new database does not
        contain. The database inode is stable across normal writes and changes on the atomic
        replacement used by migration/recovery, so it is a bounded incarnation identity rather
        than a mutable file-size or mtime guess.
        """
        try:
            stat = Path(database).stat()
        except OSError:
            return None
        identity = f"{stat.st_dev}:{stat.st_ino}"
        with self._lock:
            if self.path is None:
                if self._database_identity and self._database_identity != identity:
                    self._clear_committed_locked()
                self._database_identity = identity
                self._loaded = True
                return None
            try:
                with _cursor_lock(self.path):
                    self._load_from_disk()
                    self._repair_pending_locked()
                    if self._database_identity == identity:
                        return None
                    # A legacy sidecar has no database identity. Its receipts were not tied to
                    # the statsd database commit and may suppress atoms missing from that store.
                    # Preserve an empty new sidecar, but replay every legacy receipt once so
                    # statsd's own atom identity becomes the durable deduplication authority.
                    if not self._database_identity or self._database_identity != identity:
                        self._clear_committed_locked()
                    self._database_identity = identity
                    self._write_payload()
            except OSError as error:
                return OpenCodeUnavailable(str(error) or "cursor-state-unavailable")
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                return OpenCodeUnavailable(str(error) or "cursor-state-malformed")
        return None

    def _clear_committed_locked(self) -> None:
        self._values = {}
        self._epochs = {}
        self._sequences = {}
        self._presence = {}
        self._event_revisions = {}

    def prepare(
        self,
        values: dict[str, int],
        epochs: dict[str, int] | None = None,
        sequences: dict[str, int] | None = None,
        expected_values: dict[str, int] | None = None,
        expected_epochs: dict[str, int] | None = None,
        expected_sequences: dict[str, int] | None = None,
        presence: dict[str, bool] | None = None,
        expected_presence: dict[str, bool] | None = None,
        event_revisions: dict[str, str] | None = None,
        expected_event_revisions: dict[str, str] | None = None,
    ) -> None:
        with self._lock:
            if self._pending:
                raise OSError("cursor-state-pending")
            if len(values) > MAX_CURSOR_ENTRIES:
                raise OSError("cursor-state-bound-exceeded")
            normalized_epochs = _cursor_nonnegative_values(epochs or {})
            normalized_sequences = _cursor_nonnegative_values(sequences or {})
            normalized_presence = _cursor_presence_values(presence or {})
            normalized_event_revisions = _cursor_revision_values(event_revisions or {})
            if len(normalized_epochs) > MAX_CURSOR_ENTRIES or len(normalized_sequences) > MAX_CURSOR_ENTRIES:
                raise OSError("cursor-state-bound-exceeded")
            if len(normalized_presence) > MAX_CURSOR_ENTRIES:
                raise OSError("cursor-state-bound-exceeded")
            lock_context: AbstractContextManager[object] | None = None
            if self.path is not None:
                lock_context = _cursor_lock(self.path)
                lock_context.__enter__()
                try:
                    self._load_from_disk()
                    self._repair_pending_locked()
                    if (
                        expected_values is not None and self._values != expected_values
                        or expected_epochs is not None and self._epochs != expected_epochs
                        or expected_sequences is not None and self._sequences != expected_sequences
                        or expected_presence is not None and self._presence != expected_presence
                        or expected_event_revisions is not None and self._event_revisions != expected_event_revisions
                    ):
                        raise OSError("cursor-state-raced")
                except (OSError, TypeError, ValueError):
                    lock_context.__exit__(None, None, None)
                    raise
            self._pending_values = dict(values)
            self._pending_epochs = normalized_epochs
            self._pending_sequences = normalized_sequences
            self._pending_presence = normalized_presence
            self._pending_event_revisions = normalized_event_revisions
            self._pending = True
            self._pending_lock = lock_context
            try:
                self._write_payload()
            except (OSError, TypeError, ValueError):
                self._pending = False
                self._pending_values = None
                self._pending_epochs = None
                self._pending_sequences = None
                self._pending_presence = None
                self._pending_event_revisions = None
                self._pending_lock = None
                if lock_context is not None:
                    lock_context.__exit__(None, None, None)
                raise

    def commit(self) -> None:
        with self._lock:
            if not self._pending or self._pending_values is None:
                raise OSError("cursor-state-pending-missing")
            self._values = dict(self._pending_values)
            self._epochs = dict(self._pending_epochs or {})
            self._sequences = dict(self._pending_sequences or {})
            self._presence = dict(self._pending_presence or {})
            self._event_revisions = dict(self._pending_event_revisions or {})
            self._sequences = {
                key: value for key, value in self._sequences.items()
                if self._presence.get(key, True)
            }
            self._pending_values = None
            self._pending_epochs = None
            self._pending_sequences = None
            self._pending_presence = None
            self._pending_event_revisions = None
            self._pending = False
            lock_context = self._pending_lock
            self._pending_lock = None
            try:
                self._write_payload()
            finally:
                if lock_context is not None:
                    lock_context.__exit__(None, None, None)

    def rollback(self) -> None:
        with self._lock:
            self._pending_values = None
            self._pending_epochs = None
            self._pending_sequences = None
            self._pending_presence = None
            self._pending_event_revisions = None
            self._pending = False
            lock_context = self._pending_lock
            self._pending_lock = None
            try:
                self._write_payload()
            finally:
                if lock_context is not None:
                    lock_context.__exit__(None, None, None)

    def _load_from_disk(self) -> None:
        self._values = {}
        self._epochs = {}
        self._sequences = {}
        self._presence = {}
        self._event_revisions = {}
        self._database_identity = ""
        self._pending = False
        self._pending_values = None
        self._pending_epochs = None
        self._pending_sequences = None
        self._pending_presence = None
        self._pending_event_revisions = None
        if self.path is not None and self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._load_payload(payload)
            if isinstance(payload, dict):
                raw_revisions = payload.get("event_revisions")
                if isinstance(raw_revisions, dict) and len(raw_revisions) > MAX_CURSOR_ENTRIES:
                    # Older collectors could persist an unbounded revision map. Compact it on
                    # restart; statsd event IDs remain the durable replay identity.
                    self._write_payload()
        self._loaded = True

    def _repair_pending_locked(self) -> None:
        if not self._pending:
            return
        # The append receipt and this sidecar cannot share one transaction. Discarding an
        # interrupted prepare is safe because the next atom uses the same source/epoch/sequence
        # identity and statsd deduplicates a possible already-accepted replay.
        self._pending = False
        self._pending_values = None
        self._pending_epochs = None
        self._pending_sequences = None
        self._pending_event_revisions = None
        self._write_payload()

    def _load_payload(self, payload: object) -> None:
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("invalid cursor state")
        values = _cursor_values(payload.get("committed"))
        pending = payload.get("pending")
        if pending is not None and not isinstance(pending, dict):
            raise ValueError("invalid pending cursor state")
        self._values = values
        self._epochs = _cursor_nonnegative_values(payload.get("epochs") or {})
        self._sequences = _cursor_nonnegative_values(payload.get("sequences") or {})
        self._presence = _cursor_presence_values(payload.get("presence") or {})
        self._event_revisions = _cursor_revision_values(payload.get("event_revisions") or {})
        database_identity = payload.get("database_identity", "")
        if not isinstance(database_identity, str) or database_identity and not _bounded_text(database_identity):
            raise ValueError("invalid cursor database identity")
        self._database_identity = database_identity
        self._pending = pending is not None
        if pending is None:
            self._pending_values = None
            self._pending_epochs = None
            self._pending_sequences = None
            self._pending_presence = None
            self._pending_event_revisions = None
        elif isinstance(pending, dict) and "values" in pending:
            self._pending_values = _cursor_values(pending.get("values"))
            self._pending_epochs = _cursor_nonnegative_values(pending.get("epochs") or {})
            self._pending_sequences = _cursor_nonnegative_values(pending.get("sequences") or {})
            self._pending_presence = _cursor_presence_values(pending.get("presence") or {})
            self._pending_event_revisions = _cursor_revision_values(pending.get("event_revisions") or {})
        else:
            # Version-1 pending payloads were value maps. They remain recoverable, but are
            # deliberately discarded by the next state read because the receipt is uncertain.
            self._pending_values = _cursor_values(pending)
            self._pending_epochs = {}
            self._pending_sequences = {}
            self._pending_presence = {}
            self._pending_event_revisions = {}

    def _write_payload(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "committed": dict(sorted(self._values.items())),
            "epochs": dict(sorted(self._epochs.items())),
            "sequences": dict(sorted(self._sequences.items())),
            "presence": dict(sorted(self._presence.items())),
            "event_revisions": dict(sorted(self._event_revisions.items())),
            "database_identity": self._database_identity,
            "pending": None if not self._pending else {
                "values": dict(sorted((self._pending_values or {}).items())),
                "epochs": dict(sorted((self._pending_epochs or {}).items())),
                "sequences": dict(sorted((self._pending_sequences or {}).items())),
                "presence": dict(sorted((self._pending_presence or {}).items())),
                "event_revisions": dict(sorted((self._pending_event_revisions or {}).items())),
            },
        }
        atomic_write_text(self.path, json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", mode=0o600)


def _cursor_values(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or len(value) > MAX_CURSOR_ENTRIES:
        raise ValueError("invalid cursor values")
    result: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not _bounded_text(key):
            raise ValueError("invalid cursor key")
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= MAX_SAFE_CURSOR_VALUE:
            raise ValueError("invalid cursor value")
        result[key] = raw
    return result


@contextmanager
def _cursor_lock(path: Path) -> AbstractContextManager[object]:
    """Use the shared sidecar-lock convention, but fail closed instead of waiting forever."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise OSError("cursor-state-locked") from error
        try:
            os.utime(lock_path, None)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _cursor_nonnegative_values(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("invalid cursor values")
    result: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not _bounded_text(key):
            raise ValueError("invalid cursor key")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw > MAX_SAFE_CURSOR_VALUE:
            raise ValueError("invalid cursor value")
        result[key] = raw
    return result


def _cursor_presence_values(value: object) -> dict[str, bool]:
    if not isinstance(value, dict):
        raise ValueError("invalid cursor presence")
    result: dict[str, bool] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not _bounded_text(key) or not isinstance(raw, bool):
            raise ValueError("invalid cursor presence")
        result[key] = raw
    return result


def _cursor_revision_values(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("invalid cursor revisions")
    result: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not _bounded_text(key) or not _bounded_text(raw):
            raise ValueError("invalid cursor revision")
        result[key] = raw
    if len(result) <= MAX_CURSOR_ENTRIES:
        return result
    # Revision entries are only a local parse optimization. Deterministically retain the suffix;
    # a pruned event may be offered again, but statsd's event identity makes that replay a no-op.
    return dict(sorted(result.items())[-MAX_CURSOR_ENTRIES:])


def read_usage(
    database: Path = DEFAULT_DATABASE_PATH,
    *,
    session_id: str | None = None,
    directory: str | None = None,
    started_at: float | None = None,
    now: float | None = None,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    max_parts: int = DEFAULT_MAX_PARTS,
    max_session_age_seconds: float = DEFAULT_SESSION_MAX_AGE_SECONDS,
    start_skew_seconds: float = DEFAULT_SESSION_START_SKEW_SECONDS,
    future_skew_seconds: float = DEFAULT_FUTURE_SKEW_SECONDS,
) -> OpenCodeReadResult:
    """Read completed OpenCode usage without opening any credential-bearing table.

    An explicit session ID is the only unambiguous selector without a directory/start
    boundary. Unqualified matches never choose the newest row when another eligible
    session remains.
    """

    if max_sessions <= 0 or max_parts <= 0:
        raise ValueError("OpenCode SQLite bounds must be positive")
    if max_session_age_seconds <= 0 or start_skew_seconds < 0 or future_skew_seconds < 0:
        raise ValueError("OpenCode SQLite time bounds are invalid")
    if session_id is not None and not _bounded_text(session_id):
        return OpenCodeUnavailable("invalid-session-id")
    if directory is not None and not _bounded_text(directory):
        return OpenCodeUnavailable("invalid-directory")
    if session_id is None and directory is None:
        return OpenCodeAmbiguousSession("session-selector-requires-id-or-directory", ())

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{quote(str(database.expanduser().resolve(strict=False)), safe='/')}?mode=ro",
            uri=True,
            timeout=1.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        schema_error = _validate_schema(connection)
        if schema_error is not None:
            return OpenCodeSchemaMismatch(schema_error)
        selected = _select_session(
            connection,
            session_id=session_id,
            directory=directory,
            started_at=started_at,
            now=now,
            max_sessions=max_sessions,
            max_session_age_seconds=max_session_age_seconds,
            start_skew_seconds=start_skew_seconds,
            future_skew_seconds=future_skew_seconds,
        )
        if isinstance(selected, (OpenCodeUnavailable, OpenCodeAmbiguousSession)):
            return selected
        return _read_session_parts(connection, selected, max_parts=max_parts)
    except OSError:
        return OpenCodeUnavailable("database-unavailable")
    except sqlite3.OperationalError as error:
        detail = str(error).casefold()
        if "locked" in detail or "busy" in detail:
            return OpenCodeUnavailable("database-locked")
        if "not a database" in detail:
            return OpenCodeUnavailable("database-malformed")
        return OpenCodeUnavailable("database-unavailable")
    except sqlite3.DatabaseError:
        return OpenCodeUnavailable("database-malformed")
    except ValueError as error:
        if str(error) == "missing-session-counters":
            return OpenCodeSchemaMismatch("null-cumulative-columns:session")
        if str(error) == "missing-session-model":
            return OpenCodeUnavailable("model-metadata-missing")
        return OpenCodeUnavailable("malformed-row")
    finally:
        if connection is not None:
            connection.close()


def session_id_for_terminal_title(
    database: Path = DEFAULT_DATABASE_PATH,
    *,
    directory: str,
    title: str,
    now: float | None = None,
    max_session_age_seconds: float = DEFAULT_SESSION_MAX_AGE_SECONDS,
    future_skew_seconds: float = DEFAULT_FUTURE_SKEW_SECONDS,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
) -> str | None:
    """Return an ID only when one recent session owns the visible OpenCode title prefix.

    OpenCode's default TUI does not put its session ID in argv. Its terminal title does carry the
    session title, however, and the title is useful only as a bounded disambiguator: duplicate or
    missing matches remain unavailable rather than selecting the newest database row.
    """
    visible = str(title or "").strip()
    if visible.startswith(_OPENCODE_TITLE_PREFIX):
        visible = visible[len(_OPENCODE_TITLE_PREFIX):].strip()
    if not visible or len(visible.encode("utf-8")) > MAX_JSON_BYTES:
        return None
    canonical_directory = _canonical_directory(directory)
    current = float(now if now is not None else time.time())
    if not math.isfinite(current) or max_sessions <= 0 or max_session_age_seconds <= 0 or future_skew_seconds < 0:
        return None
    truncated = visible.endswith("...") or visible.endswith("…")
    if truncated:
        visible = visible[:-3] if visible.endswith("...") else visible[:-1]
    visible = visible.rstrip('"')
    if not visible:
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{quote(str(database.expanduser().resolve(strict=False)), safe='/')}?mode=ro",
            uri=True,
            timeout=1.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        if _validate_schema(connection) is not None:
            return None
        rows = connection.execute(
            'SELECT id, directory, title, time_updated FROM "session" '
            "WHERE directory IN (?, ?) AND time_updated >= ? AND time_updated <= ? "
            "ORDER BY time_updated DESC, id DESC LIMIT ?",
            (
                directory,
                canonical_directory,
                int((current - max_session_age_seconds) * 1000),
                int((current + future_skew_seconds) * 1000),
                max_sessions + 1,
            ),
        ).fetchall()
        matches: list[str] = []
        for row in rows:
            if _canonical_directory(_bounded_text(row["directory"])) != canonical_directory:
                continue
            candidate = _bounded_text(row["title"])
            if (candidate.startswith(visible) if truncated else candidate == visible):
                matches.append(_bounded_text(row["id"]))
        return matches[0] if len(matches) == 1 else None
    except (OSError, sqlite3.DatabaseError, ValueError):
        return None
    finally:
        if connection is not None:
            connection.close()


def read_state(
    database: Path = DEFAULT_DATABASE_PATH,
    *,
    session_id: str | None = None,
    directory: str | None = None,
    started_at: float | None = None,
    now: float | None = None,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    max_messages: int = _STATE_MESSAGE_LIMIT,
    max_parts: int = _STATE_PART_LIMIT,
    max_inputs: int = _STATE_INPUT_LIMIT,
    max_session_age_seconds: float = DEFAULT_SESSION_MAX_AGE_SECONDS,
    start_skew_seconds: float = DEFAULT_SESSION_START_SKEW_SECONDS,
    future_skew_seconds: float = DEFAULT_FUTURE_SKEW_SECONDS,
) -> OpenCodeStateReadResult:
    """Read one bounded OpenCode activity state without opening credential tables."""
    if max_sessions <= 0 or max_messages <= 0 or max_parts <= 0 or max_inputs <= 0:
        raise ValueError("OpenCode state bounds must be positive")
    if max_session_age_seconds <= 0 or start_skew_seconds < 0 or future_skew_seconds < 0:
        raise ValueError("OpenCode SQLite time bounds are invalid")
    if session_id is not None and not _bounded_text(session_id):
        return OpenCodeStateUnavailable("invalid-session-id")
    if directory is not None and not _bounded_text(directory):
        return OpenCodeStateUnavailable("invalid-directory")
    if session_id is None and directory is None:
        return OpenCodeStateUnavailable("state-selector-requires-id-or-directory")

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{quote(str(database.expanduser().resolve(strict=False)), safe='/')}?mode=ro",
            uri=True,
            timeout=1.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        schema_error = _validate_schema(connection)
        if schema_error is not None:
            return OpenCodeStateSchemaMismatch(schema_error)
        state_schema_error = _validate_state_schema(connection)
        if state_schema_error is not None:
            return OpenCodeStateSchemaMismatch(state_schema_error)
        selected = _select_session(
            connection,
            session_id=session_id,
            directory=directory,
            started_at=started_at,
            now=now,
            max_sessions=max_sessions,
            max_session_age_seconds=max_session_age_seconds,
            start_skew_seconds=start_skew_seconds,
            future_skew_seconds=future_skew_seconds,
        )
        if isinstance(selected, OpenCodeUnavailable):
            return OpenCodeStateUnavailable(selected.reason)
        if isinstance(selected, OpenCodeAmbiguousSession):
            return OpenCodeStateUnavailable(selected.reason)
        return _read_session_state(
            connection,
            selected,
            now=now,
            max_messages=max_messages,
            max_parts=max_parts,
            max_inputs=max_inputs,
        )
    except OSError:
        return OpenCodeStateUnavailable("database-unavailable")
    except sqlite3.OperationalError as error:
        detail = str(error).casefold()
        if "locked" in detail or "busy" in detail:
            return OpenCodeStateUnavailable("database-locked")
        if "not a database" in detail:
            return OpenCodeStateUnavailable("database-malformed")
        return OpenCodeStateUnavailable("database-unavailable")
    except sqlite3.DatabaseError:
        return OpenCodeStateUnavailable("database-malformed")
    except ValueError:
        return OpenCodeStateUnavailable("malformed-row")
    finally:
        if connection is not None:
            connection.close()


def _validate_state_schema(connection: sqlite3.Connection) -> str | None:
    for table, required in _STATE_REQUIRED_COLUMNS.items():
        rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        columns = {str(row[1]) for row in rows}
        if not columns:
            return f"missing-table:{table}"
        missing = sorted(required - columns)
        if missing:
            return f"missing-columns:{table}:{','.join(missing)}"
    return None


def _read_session_state(
    connection: sqlite3.Connection,
    session: OpenCodeSession,
    *,
    now: float | None,
    max_messages: int,
    max_parts: int,
    max_inputs: int,
) -> OpenCodeStateSuccess:
    current = float(now if now is not None else time.time())
    if not math.isfinite(current):
        raise ValueError("invalid-clock")
    evidence: list[str] = []
    observed_at = session.time_updated

    input_rows = connection.execute(
        'SELECT id, session_id, delivery, admitted_seq, promoted_seq, time_created FROM "session_input" '
        "WHERE session_id = ? AND time_created >= ? AND time_created <= ? "
        "ORDER BY time_created DESC, admitted_seq DESC, id DESC LIMIT ?",
        (
            session.session_id,
            int((current - _STATE_RECENT_SECONDS) * 1000),
            int((current + DEFAULT_FUTURE_SKEW_SECONDS) * 1000),
            max_inputs + 1,
        ),
    ).fetchall()
    if len(input_rows) > max_inputs:
        return OpenCodeStateUnavailable("state-input-bound-exceeded")
    queued_input = False
    for row in input_rows:
        if str(row["id"]) != _bounded_text(row["id"]) or str(row["session_id"]) != session.session_id:
            return OpenCodeStateUnavailable("malformed-queued-input")
        delivery = row["delivery"]
        if not isinstance(delivery, str) or delivery not in _VALID_INPUT_DELIVERIES:
            return OpenCodeStateUnavailable("invalid-queued-input-delivery")
        admitted_seq = row["admitted_seq"]
        promoted_seq = row["promoted_seq"]
        if (
            isinstance(admitted_seq, bool)
            or not isinstance(admitted_seq, int)
            or admitted_seq < 0
            or (
                promoted_seq is not None
                and (
                    isinstance(promoted_seq, bool)
                    or not isinstance(promoted_seq, int)
                    or promoted_seq < 0
                )
            )
        ):
            return OpenCodeStateUnavailable("invalid-queued-input-sequence")
        if promoted_seq is not None and promoted_seq < admitted_seq:
            return OpenCodeStateUnavailable("invalid-queued-input-promotion")
        created = _milliseconds(row["time_created"])
        if created is None or created < current - _STATE_RECENT_SECONDS or created > current + DEFAULT_FUTURE_SKEW_SECONDS:
            return OpenCodeStateUnavailable("invalid-queued-input-time")
        if delivery == "queue" and admitted_seq > 0 and promoted_seq is None:
            queued_input = True
    if queued_input:
        evidence.append("queued-input")

    running_parts = connection.execute(
        'SELECT id, message_id, session_id, time_created, time_updated, data FROM "part" '
        "WHERE session_id = ? AND json_valid(data) = 1 AND json_extract(data, '$.type') = 'tool' "
        "AND json_extract(data, '$.state.status') IN ('pending', 'running') "
        "AND time_updated >= ? AND time_updated <= ? "
        "ORDER BY time_updated DESC, id DESC LIMIT ?",
        (
            session.session_id,
            int((current - _STATE_RECENT_SECONDS) * 1000),
            int((current + DEFAULT_FUTURE_SKEW_SECONDS) * 1000),
            max_parts + 1,
        ),
    ).fetchall()
    if len(running_parts) > max_parts:
        return OpenCodeStateUnavailable("state-part-bound-exceeded")
    running_tool = False
    for row in running_parts:
        if str(row["id"]) != _bounded_text(row["id"]) or str(row["session_id"]) != session.session_id:
            return OpenCodeStateUnavailable("malformed-running-tool")
        if _milliseconds(row["time_updated"]) is None:
            return OpenCodeStateUnavailable("invalid-running-tool-time")
        _json_object(row["data"])
        running_tool = True
    if running_tool:
        evidence.append("running-tool")

    recent_messages = connection.execute(
        'SELECT m.id, m.session_id, m.time_created, m.time_updated, m.data FROM "message" AS m '
        "WHERE m.session_id = ? AND json_valid(m.data) = 1 AND json_extract(m.data, '$.role') = 'assistant' "
        "AND m.time_updated >= ? AND m.time_updated <= ? "
        "ORDER BY m.time_updated DESC, m.id DESC LIMIT ?",
        (
            session.session_id,
            int((current - _STATE_RECENT_SECONDS) * 1000),
            int((current + DEFAULT_FUTURE_SKEW_SECONDS) * 1000),
            max_messages + 1,
        ),
    ).fetchall()
    if len(recent_messages) > max_messages:
        return OpenCodeStateUnavailable("state-message-bound-exceeded")
    for row in recent_messages:
        data = _json_object(row["data"])
        completed_part = connection.execute(
            'SELECT 1 FROM "part" WHERE message_id = ? AND session_id = ? '
            "AND json_valid(data) = 1 AND json_extract(data, '$.type') = 'step-finish' LIMIT 1",
            (str(row["id"]), session.session_id),
        ).fetchone()
        if completed_part is not None or not _assistant_message_is_in_progress(data):
            # Messages are newest first. Once the newest assistant turn has a terminal marker,
            # older unfinished rows belong to a previous turn and cannot keep the session active.
            break
        if _assistant_message_is_in_progress(data):
            evidence.append("recent-assistant")
            break

    if "running-tool" in evidence or "recent-assistant" in evidence:
        return OpenCodeStateSuccess(session, "working", current, tuple(dict.fromkeys(evidence)))
    if "queued-input" in evidence:
        return OpenCodeStateSuccess(session, "paused", observed_at, tuple(dict.fromkeys(evidence)))
    # The database has no durable pause marker in the 1.18 schema. Preserve that distinction by
    # reporting idle rather than guessing paused from an old message or an absent process.
    return OpenCodeStateSuccess(session, "idle", observed_at, ())


def _assistant_message_is_in_progress(data: dict[str, object]) -> bool:
    """Return true only for an assistant row without a persisted terminal marker."""
    if data.get("role") != "assistant":
        return False
    if data.get("finish") not in (None, "") or data.get("error") is not None:
        return False
    message_time = data.get("time")
    if not isinstance(message_time, dict):
        return False
    if _milliseconds(message_time.get("created")) is None:
        return False
    return message_time.get("completed") is None


def _validate_schema(connection: sqlite3.Connection) -> str | None:
    for table, required in _REQUIRED_COLUMNS.items():
        rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        columns = {str(row[1]) for row in rows}
        if not columns:
            return f"missing-table:{table}"
        # OpenCode has added nullable session metadata columns over time. The reader only requires
        # identity, model, and timestamps; `project_id`/`path` are selected only when present in
        # the compatible schema below.
        missing = sorted(required - columns)
        if missing:
            return f"missing-columns:{table}:{','.join(missing)}"
    return None


def _select_session(
    connection: sqlite3.Connection,
    *,
    session_id: str | None,
    directory: str | None,
    started_at: float | None,
    now: float | None,
    max_sessions: int,
    max_session_age_seconds: float,
    start_skew_seconds: float,
    future_skew_seconds: float,
) -> OpenCodeSession | OpenCodeUnavailable | OpenCodeAmbiguousSession:
    session_columns = {
        str(row[1]) for row in connection.execute('PRAGMA table_info("session")').fetchall()
    }
    counter_columns = tuple(column for column in _SESSION_COUNTER_COLUMNS if column in session_columns)
    columns = ", ".join((*_SESSION_COLUMNS, *counter_columns))
    if session_id is not None:
        row = connection.execute(
            f'SELECT {columns} FROM "session" WHERE id = ? LIMIT 1',
            (session_id,),
        ).fetchone()
        if row is None:
            return OpenCodeUnavailable("session-not-found")
        session = _session_from_row(row)
        # The process-provided session ID is authoritative. A pane cwd may be stale after a
        # worktree move or may differ from OpenCode's canonical project path. Do not turn an exact
        # session selector into an unavailable read because of that auxiliary hint.
        if started_at is not None:
            if not math.isfinite(float(started_at)):
                return OpenCodeUnavailable("invalid-start-time")
            # A resumed TUI can reopen a session whose last persisted update predates this
            # process. The explicit ID remains authoritative; do not replace it with a newer
            # session selected from the pane cwd.
        return session

    if directory is None:
        return OpenCodeAmbiguousSession("session-selector-requires-id-or-directory", ())
    canonical_directory = _canonical_directory(directory)
    current = float(now if now is not None else time.time())
    if not math.isfinite(current):
        return OpenCodeUnavailable("invalid-clock")
    lower = current - max_session_age_seconds
    if started_at is not None:
        if not math.isfinite(float(started_at)):
            return OpenCodeUnavailable("invalid-start-time")
        lower = max(lower, float(started_at) - start_skew_seconds)
    upper = current + future_skew_seconds
    rows = connection.execute(
        f"SELECT {columns} FROM session "
        "WHERE directory IN (?, ?) AND time_updated >= ? AND time_updated <= ? "
        "ORDER BY time_updated DESC, id DESC LIMIT ?",
        (
            directory,
            canonical_directory,
            int(lower * 1000),
            int(upper * 1000),
            max_sessions + 1,
        ),
    ).fetchall()
    if not rows:
        return OpenCodeUnavailable("session-not-found")
    if len(rows) > max_sessions:
        return OpenCodeUnavailable("session-bound-exceeded")
    sessions = tuple(
        session for session in (_session_from_row(row) for row in rows)
        if _canonical_directory(session.directory) == canonical_directory
    )
    if len(sessions) != 1:
        return OpenCodeAmbiguousSession("multiple-eligible-sessions", tuple(item.session_id for item in sessions))
    return sessions[0]


def _read_session_parts(
    connection: sqlite3.Connection,
    session: OpenCodeSession,
    *,
    max_parts: int,
) -> OpenCodeReadResult:
    columns = ", ".join(_PART_COLUMNS)
    # Bound the event-owner rows, not all transcript parts. Tool/text rows can otherwise consume
    # the raw LIMIT and hide a later step-finish snapshot without producing an error.
    malformed = connection.execute(
        'SELECT 1 FROM "part" WHERE session_id = ? AND json_valid(data) = 0 LIMIT 1',
        (session.session_id,),
    ).fetchone()
    if malformed is not None:
        return OpenCodeUnavailable("malformed-transcript-json")
    rows = connection.execute(
        f'SELECT {columns} FROM "part" WHERE session_id = ? '
        "AND json_valid(data) = 1 AND json_extract(data, '$.type') = ? "
        "ORDER BY time_created ASC, id ASC LIMIT ?",
        (session.session_id, "step-finish", max_parts + 1),
    ).fetchall()
    step_finish_rows: list[sqlite3.Row] = []
    for row in rows:
        try:
            part_data = _json_object(row["data"])
        except ValueError:
            return OpenCodeUnavailable("malformed-transcript-json")
        step_finish_rows.append(row)
        if len(step_finish_rows) > max_parts:
            return OpenCodeUnavailable("part-bound-exceeded")
    rows = step_finish_rows
    message_ids = tuple(dict.fromkeys(str(row["message_id"]) for row in rows))
    messages = _read_messages(connection, session.session_id, message_ids)

    parsed_parts: list[tuple[sqlite3.Row, tuple[str, str, str], dict[TokenDimension, int | None]]] = []
    history_totals = {dimension: 0 for dimension in _TOKEN_DIMENSIONS}
    snapshot_dimensions: set[TokenDimension] | None = None
    for row in rows:
        message = messages.get(str(row["message_id"]))
        if message is None:
            return OpenCodeUnavailable("message-missing")
        try:
            message_data = _json_object(message["data"])
            part_data = _json_object(row["data"])
        except ValueError:
            return OpenCodeUnavailable("malformed-transcript-json")
        if message_data.get("role") != "assistant":
            return OpenCodeUnavailable("step-finish-not-assistant")
        tokens = part_data.get("tokens")
        if tokens is None:
            return OpenCodeUnavailable("incomplete-token-snapshot")
        if not isinstance(tokens, dict):
            return OpenCodeUnavailable("malformed-token-snapshot")
        metadata = _model_metadata(message_data, session)
        if metadata is None:
            return OpenCodeUnavailable("model-metadata-missing")
        provider, model, model_evidence = metadata
        try:
            dimensions = _token_dimensions(tokens)
        except ValueError:
            return OpenCodeUnavailable("malformed-token-snapshot")
        available_dimensions = {
            cast(TokenDimension, dimension)
            for dimension, value in dimensions.items()
            if value is not None
        }
        snapshot_dimensions = (
            available_dimensions
            if snapshot_dimensions is None
            else snapshot_dimensions & available_dimensions
        )
        for dimension, value in dimensions.items():
            if value is not None:
                history_totals[dimension] += value
        observed_at = _milliseconds(row["time_updated"])
        if observed_at is None:
            return OpenCodeUnavailable("invalid-part-completion-time")
        parsed_parts.append((row, (provider, model, model_evidence), dimensions))
    components: list[OpenCodeUsageComponent] = []
    if not parsed_parts:
        if any(
            value
            for dimension, value in (session.cumulative_tokens or {}).items()
            if dimension in _ATOM_DIMENSIONS
        ):
            return OpenCodeUnavailable("cumulative-history-anchor-missing")
        return OpenCodeReadSuccess(session, ())
    counters = session.cumulative_tokens or {}
    comparable_dimensions = tuple(
        dimension for dimension in _TOKEN_DIMENSIONS
        if counters.get(dimension) is not None
        and snapshot_dimensions is not None
        and dimension in snapshot_dimensions
    )
    if any(history_totals[dimension] != counters[dimension] for dimension in comparable_dimensions):
        return OpenCodeUnavailable("cumulative-anchor-mismatch")
    for dimension in _ATOM_DIMENSIONS:
        if counters.get(dimension) is not None and dimension not in (snapshot_dimensions or set()):
            return OpenCodeUnavailable("incomplete-supported-token-history")
    for row, (provider, model, model_evidence), dimensions in parsed_parts:
        observed_at = _milliseconds(row["time_updated"])
        if observed_at is None:
            return OpenCodeUnavailable("invalid-part-completion-time")
        row_id = str(row["id"])
        message_id = str(row["message_id"])
        snapshot_complete = all(value is not None for value in dimensions.values())
        unsupported_dimensions = tuple(
            cast(TokenDimension, dimension)
            for dimension in _TOKEN_DIMENSIONS
            if dimensions[cast(TokenDimension, dimension)] is None
            and counters.get(cast(TokenDimension, dimension)) is None
        )
        revision = _part_revision(row, dimensions, provider, model)
        for raw_dimension in _TOKEN_DIMENSIONS:
            dimension = cast(TokenDimension, raw_dimension)
            quantity = dimensions[dimension]
            if quantity is None:
                continue
            # If an optional counter is absent from the session row, retain the exact per-step
            # value. Only the supported dimensions are materialized downstream.
            components.append(OpenCodeUsageComponent(
                event_id=_event_id(session.session_id, row_id, dimension),
                session_id=session.session_id,
                message_id=message_id,
                part_id=row_id,
                observed_at=observed_at,
                dimension=dimension,
                tokens=quantity,
                provider=provider,
                model=model,
                model_evidence=model_evidence,
                agent_id=session.session_id,
                telemetry_complete=snapshot_complete,
                unsupported_dimensions=unsupported_dimensions,
                source_revision=revision,
            ))
    return OpenCodeReadSuccess(session, tuple(components))


def _part_revision(
    row: sqlite3.Row,
    dimensions: dict[TokenDimension, int | None],
    provider: str,
    model: str,
) -> str:
    """Fingerprint bounded source fields so a rewritten part is not mistaken for its old value."""
    payload = {
        "id": str(row["id"]),
        "message_id": str(row["message_id"]),
        "time_created": row["time_created"],
        "time_updated": row["time_updated"],
        "dimensions": dimensions,
        "provider": provider,
        "model": model,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_messages(
    connection: sqlite3.Connection,
    session_id: str,
    message_ids: tuple[str, ...],
) -> dict[str, sqlite3.Row]:
    if not message_ids:
        return {}
    columns = ", ".join(_MESSAGE_COLUMNS)
    messages: dict[str, sqlite3.Row] = {}
    for offset in range(0, len(message_ids), MAX_MESSAGE_QUERY_IDS):
        batch = message_ids[offset:offset + MAX_MESSAGE_QUERY_IDS]
        placeholders = ", ".join("?" for _ in batch)
        rows = connection.execute(
            f'SELECT {columns} FROM "message" WHERE session_id = ? AND id IN ({placeholders})',
            (session_id, *batch),
        ).fetchall()
        messages.update({str(row["id"]): row for row in rows})
    return messages


def _session_from_row(row: sqlite3.Row) -> OpenCodeSession:
    session_id = _bounded_text(row["id"])
    directory = _bounded_text(row["directory"])
    metadata = _json_object(row["model"]) if row["model"] else {}
    provider = _bounded_text(metadata.get("providerID") or metadata.get("provider_id") or "")
    model = _bounded_text(
        metadata.get("id")
        or metadata.get("modelID")
        or metadata.get("model_id")
        or ""
    )
    agent = _bounded_text(row["agent"] or "") or "unknown"
    created = _milliseconds(row["time_created"])
    updated = _milliseconds(row["time_updated"])
    cost = _cost_number(row["cost"])
    if not session_id or not directory or created is None or updated is None or cost is None:
        raise ValueError("malformed-session-row")
    cumulative_tokens = {
        dimension: _token_number(row[column])
        for dimension, column in (
            ("input", "tokens_input"),
            ("output", "tokens_output"),
            ("reasoning", "tokens_reasoning"),
            ("cache_read", "tokens_cache_read"),
            ("cache_write", "tokens_cache_write"),
        )
        if column in row.keys()
    }
    return OpenCodeSession(
        session_id, directory, model, provider, agent, created, updated, cumulative_tokens, cost,
    )


def _model_metadata(message: dict[str, object], session: OpenCodeSession) -> tuple[str, str, str] | None:
    model = message.get("model")
    model_data = model if isinstance(model, dict) else {}
    message_provider = _bounded_text(
        message.get("providerID") or message.get("provider_id")
        or model_data.get("providerID") or model_data.get("provider_id") or ""
    )
    message_model = _bounded_text(
        message.get("modelID") or message.get("model_id")
        or model_data.get("modelID") or model_data.get("model_id") or ""
    )
    provider = message_provider or session.provider
    model = message_model or session.model
    if not provider or not model:
        return None
    if message_provider and message_model:
        evidence = "message.providerID+message.modelID"
    elif message_provider:
        evidence = "message.providerID+session.model"
    elif message_model:
        evidence = "session.provider+message.modelID"
    else:
        evidence = "session.model"
    return provider, model, evidence


def _event_id(session_id: str, part_id: str, dimension: TokenDimension) -> str:
    readable = f"opencode:{session_id}:{part_id}:{dimension}"
    if len(readable.encode("utf-8")) <= 512:
        return readable
    digest = hashlib.sha256(readable.encode("utf-8")).hexdigest()
    return f"opencode:{digest}:{dimension}"


def _token_dimensions(tokens: dict[str, object]) -> dict[TokenDimension, int | None]:
    # `total` is part of OpenCode's persisted shape, but it is not a materialized dimension. Read
    # and validate it so a truncated or malformed usage object cannot masquerade as a valid step.
    _token_number(tokens.get("total"))
    cache = tokens.get("cache")
    if cache is not None and not isinstance(cache, dict):
        raise ValueError("cache-is-not-an-object")
    cache = cache if isinstance(cache, dict) else {}
    return {
        "input": _token_number(tokens.get("input")),
        "cache_read": _token_number(cache.get("read")),
        "cache_write": _token_number(cache.get("write")),
        "output": _token_number(tokens.get("output")),
        "reasoning": _token_number(tokens.get("reasoning")),
    }


def _token_number(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("token-count-is-not-numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise ValueError("token-count-is-not-a-nonnegative-integer")
    return int(number)


def _cost_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("json-value-is-too-large")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("json-value-is-not-an-object")
    return parsed


def _bounded_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return ""
    return value


def _milliseconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value) / 1000
    return number if math.isfinite(number) and number > 0 else None


def source_id_for_agent(agent_key: str) -> str:
    """Return a bounded stable source identity for one discovered pane agent."""
    value = str(agent_key or "").strip()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"opencode-agent:{digest}"


def source_id_for_session(session_id: str) -> str:
    """Return the durable source identity independent of a pane/window key."""
    digest = hashlib.sha256(str(session_id).strip().encode("utf-8")).hexdigest()[:24]
    return f"opencode-session:{digest}"


def source_id_for_directory(directory: str) -> str:
    digest = hashlib.sha256(_canonical_directory(directory).encode("utf-8")).hexdigest()[:24]
    return f"opencode-directory:{digest}"


def source_id_for_selector(*, session_id: str | None, directory: str | None, agent_key: str) -> str:
    if session_id:
        return source_id_for_session(session_id)
    if directory:
        return source_id_for_directory(directory)
    return source_id_for_agent(agent_key)


def cursor_key(session_id: str, dimension: TokenDimension) -> str:
    return ":".join(("session", session_id, dimension))


def delta_event_id(session_id: str, dimension: TokenDimension, epoch: int, sequence: int) -> str:
    readable = f"opencode-delta:{session_id}:{dimension}:{epoch}:{sequence}"
    if len(readable.encode("utf-8")) <= 512:
        return readable
    return f"opencode-delta:{hashlib.sha256(readable.encode('utf-8')).hexdigest()}"


def _canonical_directory(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve(strict=False))
    except (OSError, RuntimeError) as error:
        raise ValueError("directory-canonicalization-failed") from error


__all__ = (
    "DEFAULT_DATABASE_PATH", "DEFAULT_MAX_PARTS", "DEFAULT_MAX_SESSIONS",
    "OpenCodeAmbiguousSession", "OpenCodeReadResult", "OpenCodeReadSuccess",
    "OpenCodeSchemaMismatch", "OpenCodeSession", "OpenCodeUnavailable",
    "OpenCodeUsageComponent", "OpenCodeCursorState", "OpenCodeCursorStore",
    "OpenCodeState", "OpenCodeStateReadResult", "OpenCodeStateSchemaMismatch", "OpenCodeStateSuccess",
    "OpenCodeStateUnavailable", "OpenCodeSessionState", "TokenDimension", "read_state", "read_usage",
    "source_id_for_agent", "source_id_for_session", "source_id_for_directory",
    "source_id_for_selector", "delta_event_id",
)
