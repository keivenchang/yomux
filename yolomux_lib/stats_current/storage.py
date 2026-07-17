# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small current-schema-only SQLite store for original YO!stats facts."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from typing import Iterable
from typing import Iterator
from typing import Mapping
from urllib.parse import quote

from yolomux_lib.atomic_file import atomic_write_text

from . import identity


# Keep the existing YOST identity and advance beyond legacy schema 4. That
# combination makes the legacy writer's read-only header fence stop before it
# can reinterpret or mutate this intentionally incompatible schema.
APPLICATION_ID = 0x594F5354
SCHEMA_VERSION = 5
MIN_WRITER_PROTOCOL = 24
MIN_WRITER_BUILD = 2
RETENTION_SECONDS = 24 * 60 * 60
DATABASE_FILENAME = f"stats-v{SCHEMA_VERSION}.sqlite3"
WRITER_FENCE_FILENAME = "stats-writer-compat.json"
MAX_DIRTY_INTERVALS = 32
MAX_DIRTY_INTERVAL_SPAN_SECONDS = 60 * 60
MAX_PRIVATE_OBSERVATION_SOURCES = 64

_TABLES = frozenset(
    {
        "coverage_epochs",
        "migration_reconciliation",
        "observations",
        "schema_meta",
        "unavailable_spans",
        "usage_atoms",
    }
)
_COLUMNS = {
    "coverage_epochs": (
        "family", "source_id", "epoch_id", "started_at", "ended_at",
        "native_cadence_seconds", "owner_generation",
    ),
    "migration_reconciliation": (
        "migration_id", "completed_at", "source_digest", "details_json",
    ),
    "observations": (
        "event_id", "family", "source_id", "observed_at", "epoch_id", "owner_generation",
        "payload_json",
    ),
    "schema_meta": (
        "singleton", "minimum_writer_protocol", "minimum_writer_build", "source_generation",
    ),
    "unavailable_spans": (
        "family", "source_id", "epoch_id", "started_at", "ended_at",
        "native_cadence_seconds", "reason", "owner_generation",
    ),
    "usage_atoms": (
        "event_id", "direction", "modality", "cache_role", "unit", "observed_at", "payload_json",
    ),
}


class StatsCurrentError(RuntimeError):
    """Base error for a current YO!stats storage failure."""


class SchemaMismatchError(StatsCurrentError):
    """The database is not the exact schema this store understands."""


class SchemaTooNewError(StatsCurrentError):
    """The database requires a newer schema, writer protocol, or writer build."""

    def __init__(
        self,
        *,
        found_schema: int,
        supported_schema: int,
        minimum_writer_protocol: int = 0,
        minimum_writer_build: int = 0,
    ) -> None:
        super().__init__(
            f"stats schema {found_schema} requires a newer writer "
            f"(supported schema {supported_schema}, minimum protocol "
            f"{minimum_writer_protocol}, minimum build {minimum_writer_build})"
        )
        self.found_schema = found_schema
        self.supported_schema = supported_schema
        self.minimum_writer_protocol = minimum_writer_protocol
        self.minimum_writer_build = minimum_writer_build


class StorageValidationError(StatsCurrentError, ValueError):
    """A record violates the current storage contract."""


USAGE_IDENTITY_CONFLICT_STATUS = "usage_identity_conflict"


class UsageAtomIdentityConflict(StorageValidationError):
    """One usage identity was replayed with a different immutable payload."""

    def __init__(
        self,
        *,
        event_id: str,
        identity_hash: str,
        first_payload_hash: str,
        attempted_payload_hash: str,
    ) -> None:
        super().__init__("usage atom identity conflicts with stored data")
        self.event_id = event_id
        self.identity_hash = identity_hash
        self.first_payload_hash = first_payload_hash
        self.attempted_payload_hash = attempted_payload_hash


@dataclass(frozen=True)
class Observation:
    event_id: str
    family: str
    source_id: str
    observed_at: float
    epoch_id: str
    owner_generation: int
    payload: Mapping[str, object]


@dataclass(frozen=True)
class CoverageEpoch:
    family: str
    source_id: str
    epoch_id: str
    started_at: float
    ended_at: float | None
    native_cadence_seconds: float
    owner_generation: int


@dataclass(frozen=True)
class UsageAtom:
    event_id: str
    direction: str
    modality: str
    cache_role: str
    unit: str
    observed_at: float
    payload: Mapping[str, object]


@dataclass(frozen=True)
class UsageAtomTombstone:
    """Exact legacy fork-history identity that a versioned replay may remove."""

    event_id: str
    direction: str
    modality: str
    cache_role: str
    unit: str
    observed_at: float
    quantity: float
    provider: str
    model: str
    thread_id: str


@dataclass(frozen=True)
class UnavailableSpan:
    family: str
    source_id: str
    epoch_id: str
    started_at: float
    ended_at: float
    native_cadence_seconds: float
    reason: str
    owner_generation: int


def normalize_unavailable_spans(
    spans: Iterable[UnavailableSpan],
) -> tuple[UnavailableSpan, ...]:
    """Return deterministic non-overlapping portions for each family/source.

    Early migration builds could preserve overlapping coarse loss markers. Keep
    the earliest marker as the owner of an overlap and retain only uncovered
    portions of later markers, so existing schema-5 databases remain readable
    without inventing availability or rewriting source evidence at request time.
    """

    accepted: list[UnavailableSpan] = []
    source_end: dict[tuple[str, str], float] = {}
    for span in sorted(
        spans,
        key=lambda item: (
            item.family, item.source_id, item.started_at, item.ended_at,
            item.epoch_id, item.reason,
        ),
    ):
        source = (span.family, span.source_id)
        start = max(span.started_at, source_end.get(source, span.started_at))
        if start >= span.ended_at:
            continue
        item = UnavailableSpan(
            span.family, span.source_id, span.epoch_id, start, span.ended_at,
            span.native_cadence_seconds, span.reason, span.owner_generation,
        )
        previous = accepted[-1] if accepted else None
        if (
            previous is not None
            and previous.family == item.family
            and previous.source_id == item.source_id
            and item.source_id.startswith("retired-unavailable:")
            and previous.ended_at == item.started_at
            and previous.native_cadence_seconds == item.native_cadence_seconds
            and previous.reason == item.reason
        ):
            accepted[-1] = UnavailableSpan(
                previous.family, previous.source_id, previous.epoch_id,
                previous.started_at, item.ended_at, previous.native_cadence_seconds,
                previous.reason, max(previous.owner_generation, item.owner_generation),
            )
        else:
            accepted.append(item)
        source_end[source] = span.ended_at
    return tuple(accepted)


@dataclass(frozen=True)
class SchemaMetadata:
    schema_version: int
    minimum_writer_protocol: int
    minimum_writer_build: int
    source_generation: int = 0


@dataclass(frozen=True)
class MigrationReconciliation:
    migration_id: str
    completed_at: float
    source_digest: str
    details: Mapping[str, object]


@dataclass(frozen=True)
class StoreSnapshot:
    schema: SchemaMetadata
    observations: tuple[Observation, ...]
    coverage_epochs: tuple[CoverageEpoch, ...]
    usage_atoms: tuple[UsageAtom, ...]
    migration_reconciliation: tuple[MigrationReconciliation, ...]
    unavailable_spans: tuple[UnavailableSpan, ...] = ()


@dataclass(frozen=True)
class PruneResult:
    observations_deleted: int
    coverage_epochs_deleted: int
    coverage_epochs_clipped: int
    usage_atoms_deleted: int
    source_generation: int
    unavailable_spans_deleted: int = 0
    unavailable_spans_clipped: int = 0


@dataclass(frozen=True)
class AppendResult:
    source_generation: int
    observations_accepted: int
    observations_duplicate: int
    coverage_changed: int
    coverage_unchanged: int
    usage_atoms_accepted: int
    usage_atoms_duplicate: int
    unavailable_spans_accepted: int = 0
    unavailable_spans_duplicate: int = 0
    usage_attribution_conflicts: int = 0
    usage_tombstones_accepted: int = 0
    usage_tombstones_duplicate: int = 0


@dataclass(frozen=True)
class _Header:
    application_id: int
    schema_version: int
    minimum_writer_protocol: int
    minimum_writer_build: int
    source_generation: int


def _validate_text(
    value: object,
    name: str,
    *,
    maximum_bytes: int = identity.MAX_IDENTITY_BYTES,
) -> str:
    try:
        return identity.identity_text(value, name, maximum_bytes=maximum_bytes)
    except identity.IdentityValidationError as error:
        raise StorageValidationError(str(error)) from error


def _validate_timestamp(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StorageValidationError(f"{name} must be a finite timestamp")
    timestamp = float(value)
    if not math.isfinite(timestamp) or timestamp < 0:
        raise StorageValidationError(f"{name} must be a finite timestamp")
    return timestamp


def _validate_nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StorageValidationError(f"{name} must be a non-negative integer")
    return value


def _encode_json_object(value: Mapping[str, object], name: str) -> str:
    if not isinstance(value, Mapping):
        raise StorageValidationError(f"{name} must be a JSON object")
    try:
        return json.dumps(dict(value), allow_nan=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        raise StorageValidationError(f"{name} must be a JSON object") from error


def _usage_payloads(
    previous: tuple[object, object], current: tuple[object, object],
) -> tuple[dict[str, object], dict[str, object]] | None:
    if previous[0] != current[0]:
        return None
    try:
        previous_payload = json.loads(str(previous[1]))
        current_payload = json.loads(str(current[1]))
    except json.JSONDecodeError:
        return None
    if not isinstance(previous_payload, dict) or not isinstance(current_payload, dict):
        return None
    return previous_payload, current_payload


def _usage_compatible_metadata_change(
    previous: tuple[object, object], current: tuple[object, object],
) -> tuple[bool, bool]:
    payloads = _usage_payloads(previous, current)
    if payloads is None:
        return False, False
    previous_payload, current_payload = payloads
    previous_agent = previous_payload.pop("agent_id", None)
    current_agent = current_payload.pop("agent_id", None)
    previous_evidence = previous_payload.pop("model_evidence", None)
    current_evidence = current_payload.pop("model_evidence", None)
    previous_profile = previous_payload.pop("pricing_profile", None)
    current_profile = current_payload.pop("pricing_profile", None)
    changed = (
        previous_agent != current_agent
        or previous_evidence != current_evidence
        or previous_profile != current_profile
    )
    return changed and previous_payload == current_payload, previous_agent != current_agent


def _usage_unknown_model_repair(
    previous: tuple[object, object], current: tuple[object, object],
) -> tuple[str, bool] | None:
    """Return a one-way unknown-to-provider-model repair, preserving first agent ownership."""

    payloads = _usage_payloads(previous, current)
    if payloads is None:
        return None
    previous_payload, current_payload = payloads
    if str(previous_payload.get("model") or "").strip().lower() != "unknown":
        return None
    current_model = str(current_payload.get("model") or "").strip()
    if not current_model or current_model.lower() == "unknown":
        return None
    previous_comparable = dict(previous_payload)
    current_comparable = dict(current_payload)
    previous_agent = previous_comparable.pop("agent_id", None)
    current_agent = current_comparable.pop("agent_id", None)
    previous_comparable.pop("model", None)
    current_comparable.pop("model", None)
    previous_comparable.pop("model_evidence", None)
    current_comparable.pop("model_evidence", None)
    previous_comparable.pop("pricing_profile", None)
    current_comparable.pop("pricing_profile", None)
    if previous_comparable != current_comparable:
        return None
    repaired = dict(previous_payload)
    repaired["model"] = current_model
    evidence = str(current_payload.get("model_evidence") or "").strip()
    if evidence:
        repaired["model_evidence"] = evidence
    return _encode_json_object(repaired, "payload"), previous_agent != current_agent


def _decode_json_object(encoded: object, name: str) -> dict[str, object]:
    try:
        value = json.loads(str(encoded))
    except json.JSONDecodeError as error:
        raise SchemaMismatchError(f"{name} contains invalid JSON") from error
    if not isinstance(value, dict):
        raise SchemaMismatchError(f"{name} must contain a JSON object")
    return value


def _coalesced_dirty_intervals(
    values: Iterable[tuple[int | float, int | float]] | None,
) -> tuple[tuple[float, float], ...] | None:
    if values is None:
        return None
    intervals = []
    for raw_start, raw_end in values:
        start = _validate_timestamp(raw_start, "dirty interval start")
        end = _validate_timestamp(raw_end, "dirty interval end")
        if end <= start:
            raise StorageValidationError("dirty interval end must follow its start")
        intervals.append((start, end))
    if not intervals:
        return ()
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if (
        len(merged) > MAX_DIRTY_INTERVALS
        or merged[-1][1] - merged[0][0] > MAX_DIRTY_INTERVAL_SPAN_SECONDS
    ):
        return None
    return tuple(merged)


def _time_clause(intervals: tuple[tuple[float, float], ...] | None) -> tuple[str, tuple[float, ...]]:
    if intervals is None:
        return "", ()
    if not intervals:
        return " WHERE 0", ()
    clause = " WHERE " + " OR ".join(
        "(observed_at >= ? AND observed_at < ?)" for _interval in intervals
    )
    parameters = tuple(value for interval in intervals for value in interval)
    return clause, parameters


def _read_header(connection: sqlite3.Connection) -> _Header:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if schema_version > SCHEMA_VERSION:
        raise SchemaTooNewError(found_schema=schema_version, supported_schema=SCHEMA_VERSION)
    if application_id != APPLICATION_ID or schema_version != SCHEMA_VERSION:
        raise SchemaMismatchError(
            f"expected YOST schema {SCHEMA_VERSION}, found application id "
            f"{application_id:#x} schema {schema_version}"
        )
    try:
        row = connection.execute(
            "SELECT minimum_writer_protocol, minimum_writer_build, source_generation "
            "FROM schema_meta WHERE singleton = ?",
            (1,),
        ).fetchone()
    except sqlite3.Error as error:
        raise SchemaMismatchError("current schema metadata is missing") from error
    if row is None:
        raise SchemaMismatchError("current schema metadata is missing")
    return _Header(application_id, schema_version, int(row[0]), int(row[1]), int(row[2]))


def _validate_database_path(path: Path) -> None:
    if path.name != DATABASE_FILENAME:
        raise SchemaMismatchError(f"current stats database must be named {DATABASE_FILENAME}")
    if path.is_symlink():
        raise SchemaMismatchError("current stats database cannot be a symbolic link")


def _check_writer(header: _Header, writer_protocol: int, writer_build: int) -> None:
    if writer_protocol < header.minimum_writer_protocol or writer_build < header.minimum_writer_build:
        raise SchemaTooNewError(
            found_schema=header.schema_version,
            supported_schema=SCHEMA_VERSION,
            minimum_writer_protocol=header.minimum_writer_protocol,
            minimum_writer_build=header.minimum_writer_build,
        )


def _read_only_uri(path: Path, *, immutable: bool) -> str:
    # immutable prevents even SQLite journal/shared-memory sidecar creation
    # during the compatibility fence.
    suffix = "&immutable=1" if immutable else ""
    return f"file:{quote(str(path.resolve()), safe='/')}?mode=ro{suffix}"


def _observation_values(observation: Observation) -> tuple[object, ...]:
    return (
        _validate_text(
            observation.event_id, "event_id", maximum_bytes=identity.MAX_EVENT_ID_BYTES,
        ),
        _validate_text(observation.family, "family"),
        _validate_text(
            observation.source_id, "source_id",
            maximum_bytes=identity.MAX_SERIES_COMPONENT_BYTES,
        ),
        _validate_timestamp(observation.observed_at, "observed_at"),
        _validate_text(
            observation.epoch_id, "epoch_id",
            maximum_bytes=identity.MAX_SERIES_COMPONENT_BYTES,
        ),
        _validate_nonnegative_integer(observation.owner_generation, "owner_generation"),
        _encode_json_object(observation.payload, "payload"),
    )


def _coverage_values(coverage: CoverageEpoch) -> tuple[object, ...]:
    started_at = _validate_timestamp(coverage.started_at, "started_at")
    ended_at = None
    if coverage.ended_at is not None:
        ended_at = _validate_timestamp(coverage.ended_at, "ended_at")
        if ended_at < started_at:
            raise StorageValidationError("ended_at must not precede started_at")
    cadence = _validate_timestamp(coverage.native_cadence_seconds, "native_cadence_seconds")
    if cadence == 0:
        raise StorageValidationError("native_cadence_seconds must be positive")
    return (
        _validate_text(coverage.family, "family"),
        _validate_text(
            coverage.source_id, "source_id",
            maximum_bytes=identity.MAX_SERIES_COMPONENT_BYTES,
        ),
        _validate_text(
            coverage.epoch_id, "epoch_id",
            maximum_bytes=identity.MAX_SERIES_COMPONENT_BYTES,
        ),
        started_at,
        ended_at,
        cadence,
        _validate_nonnegative_integer(coverage.owner_generation, "owner_generation"),
    )


def _usage_values(atom: UsageAtom) -> tuple[object, ...]:
    return (
        _validate_text(atom.event_id, "event_id", maximum_bytes=identity.MAX_EVENT_ID_BYTES),
        _validate_text(atom.direction, "direction"),
        _validate_text(atom.modality, "modality"),
        _validate_text(atom.cache_role, "cache_role"),
        _validate_text(atom.unit, "unit"),
        _validate_timestamp(atom.observed_at, "observed_at"),
        _encode_json_object(atom.payload, "payload"),
    )


def _usage_conflict_hash(values: tuple[object, ...]) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _usage_tombstone_values(tombstone: UsageAtomTombstone) -> tuple[object, ...]:
    event_id = _validate_text(
        tombstone.event_id, "event_id", maximum_bytes=identity.MAX_EVENT_ID_BYTES,
    )
    provider = _validate_text(tombstone.provider, "provider")
    model = _validate_text(tombstone.model, "model")
    thread_id = _validate_text(
        tombstone.thread_id,
        "thread_id",
        maximum_bytes=identity.MAX_SERIES_COMPONENT_BYTES,
    )
    if provider != "openai" or not event_id.startswith(f"codex:{thread_id}:"):
        raise StorageValidationError("usage tombstone must identify Codex fork history")
    return (
        event_id,
        _validate_text(tombstone.direction, "direction"),
        _validate_text(tombstone.modality, "modality"),
        _validate_text(tombstone.cache_role, "cache_role"),
        _validate_text(tombstone.unit, "unit"),
        _validate_timestamp(tombstone.observed_at, "observed_at"),
        _validate_timestamp(tombstone.quantity, "quantity"),
        provider,
        model,
        thread_id,
    )


def _unavailable_values(span: UnavailableSpan) -> tuple[object, ...]:
    started_at = _validate_timestamp(span.started_at, "started_at")
    ended_at = _validate_timestamp(span.ended_at, "ended_at")
    if ended_at <= started_at:
        raise StorageValidationError("unavailable ended_at must follow started_at")
    cadence = _validate_timestamp(span.native_cadence_seconds, "native_cadence_seconds")
    if cadence == 0:
        raise StorageValidationError("native_cadence_seconds must be positive")
    return (
        _validate_text(span.family, "family"),
        _validate_text(
            span.source_id, "source_id",
            maximum_bytes=identity.MAX_SERIES_COMPONENT_BYTES,
        ),
        _validate_text(
            span.epoch_id, "epoch_id",
            maximum_bytes=identity.MAX_SERIES_COMPONENT_BYTES,
        ),
        started_at,
        ended_at,
        cadence,
        _validate_text(span.reason, "reason"),
        _validate_nonnegative_integer(span.owner_generation, "owner_generation"),
    )


class Store:
    """One fail-fast owner of the exact current schema and original facts."""

    def __init__(self, path: Path, connection: sqlite3.Connection, *, read_only: bool = False) -> None:
        self.path = path
        self._database: sqlite3.Connection | None = connection
        self.read_only = read_only

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        writer_protocol: int = MIN_WRITER_PROTOCOL,
        writer_build: int = MIN_WRITER_BUILD,
    ) -> Store:
        database_path = Path(path)
        protocol = _validate_nonnegative_integer(writer_protocol, "writer_protocol")
        build = _validate_nonnegative_integer(writer_build, "writer_build")
        _validate_database_path(database_path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        cls._preflight_fence(database_path, protocol, build)
        if protocol < MIN_WRITER_PROTOCOL or build < MIN_WRITER_BUILD:
            _check_writer(
                _Header(APPLICATION_ID, SCHEMA_VERSION, MIN_WRITER_PROTOCOL, MIN_WRITER_BUILD, 0),
                protocol,
                build,
            )
        is_new = not database_path.exists() or database_path.stat().st_size == 0
        if not is_new:
            cls._preflight(database_path, protocol, build)
        cls._publish_fence(database_path)
        connection = sqlite3.connect(database_path, timeout=5.0, isolation_level=None)
        try:
            if is_new:
                cls._initialize(connection)
            else:
                header = _read_header(connection)
                _check_writer(header, protocol, build)
                cls._validate_schema_shape(connection)
                cls._upgrade_current_contract(connection)
            # Compatibility is proven before changing journal metadata.  WAL
            # lets the sole writer and materializer reader progress without
            # turning a long read snapshot into writer latency.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
        except (sqlite3.Error, StatsCurrentError):
            connection.close()
            raise
        return cls(database_path, connection)

    @classmethod
    def open_reader(
        cls,
        path: str | Path,
        *,
        writer_protocol: int = MIN_WRITER_PROTOCOL,
        writer_build: int = MIN_WRITER_BUILD,
    ) -> Store:
        """Open the exact current database without publishing or accepting writes."""

        database_path = Path(path)
        protocol = _validate_nonnegative_integer(writer_protocol, "writer_protocol")
        build = _validate_nonnegative_integer(writer_build, "writer_build")
        _validate_database_path(database_path)
        cls._preflight_fence(database_path, protocol, build)
        cls._preflight(database_path, protocol, build)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                _read_only_uri(database_path, immutable=False),
                uri=True,
                timeout=5.0,
                isolation_level=None,
            )
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            header = _read_header(connection)
            _check_writer(header, protocol, build)
            cls._validate_schema_shape(connection)
        except (sqlite3.Error, StatsCurrentError):
            if connection is not None:
                connection.close()
            raise
        if connection is None:
            raise StatsCurrentError("stats store reader did not open")
        return cls(database_path, connection, read_only=True)

    @staticmethod
    def _preflight_fence(path: Path, writer_protocol: int, writer_build: int) -> None:
        fence_path = path.parent / WRITER_FENCE_FILENAME
        try:
            value = json.loads(fence_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as error:
            raise SchemaMismatchError("stats writer fence cannot be read") from error
        if not isinstance(value, dict):
            raise SchemaMismatchError("stats writer fence must be an object")
        try:
            schema = int(value["schema_version"])
            minimum_protocol = int(value["minimum_writer_protocol"])
        except (KeyError, TypeError, ValueError) as error:
            raise SchemaMismatchError("stats writer fence is malformed") from error
        if schema > SCHEMA_VERSION or minimum_protocol > writer_protocol:
            raise SchemaTooNewError(
                found_schema=schema,
                supported_schema=SCHEMA_VERSION,
                minimum_writer_protocol=minimum_protocol,
                minimum_writer_build=0,
            )
        # Schema 4 fences stored a source-revision string as minimum_writer_build.
        # They are migration input, so current code may pass them without weakening
        # the exact numeric build fence required by schema 5 and later.
        if schema < SCHEMA_VERSION:
            return
        try:
            minimum_build = int(value["minimum_writer_build"])
            application_id = int(value["application_id"])
            database_filename = value["database_filename"]
        except (KeyError, TypeError, ValueError) as error:
            raise SchemaMismatchError("current stats writer fence is malformed") from error
        if application_id != APPLICATION_ID or database_filename != DATABASE_FILENAME:
            raise SchemaMismatchError("current stats writer fence identifies a different database")
        if minimum_build > writer_build:
            raise SchemaTooNewError(
                found_schema=schema,
                supported_schema=SCHEMA_VERSION,
                minimum_writer_protocol=minimum_protocol,
                minimum_writer_build=minimum_build,
            )

    @staticmethod
    def _publish_fence(path: Path) -> None:
        payload = {
            "application_id": APPLICATION_ID,
            "database_filename": DATABASE_FILENAME,
            "schema_version": SCHEMA_VERSION,
            "minimum_writer_protocol": MIN_WRITER_PROTOCOL,
            "minimum_writer_build": MIN_WRITER_BUILD,
        }
        atomic_write_text(
            path.parent / WRITER_FENCE_FILENAME,
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
        )

    @staticmethod
    def _preflight(path: Path, writer_protocol: int, writer_build: int) -> None:
        try:
            connection = sqlite3.connect(
                _read_only_uri(path, immutable=True), uri=True, timeout=5.0, isolation_level=None,
            )
        except sqlite3.Error as error:
            raise SchemaMismatchError("stats database cannot be opened read-only") from error
        try:
            try:
                header = _read_header(connection)
                _check_writer(header, writer_protocol, writer_build)
                Store._validate_schema_shape(connection)
            except sqlite3.Error as error:
                raise SchemaMismatchError("current stats schema cannot be read") from error
        finally:
            connection.close()

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        with _transaction(connection):
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.execute(
                "CREATE TABLE schema_meta ("
                "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                "minimum_writer_protocol INTEGER NOT NULL CHECK (minimum_writer_protocol >= 0), "
                "minimum_writer_build INTEGER NOT NULL CHECK (minimum_writer_build >= 0), "
                "source_generation INTEGER NOT NULL CHECK (source_generation >= 0))"
            )
            connection.execute(
                "INSERT INTO schema_meta(singleton, minimum_writer_protocol, minimum_writer_build, "
                "source_generation) VALUES(?, ?, ?, ?)",
                (1, MIN_WRITER_PROTOCOL, MIN_WRITER_BUILD, 0),
            )
            connection.execute(
                "CREATE TABLE observations ("
                "event_id TEXT NOT NULL, family TEXT NOT NULL, source_id TEXT NOT NULL, "
                "observed_at REAL NOT NULL, "
                "epoch_id TEXT NOT NULL, owner_generation INTEGER NOT NULL CHECK (owner_generation >= 0), "
                "payload_json TEXT NOT NULL, PRIMARY KEY(family, source_id, event_id)) WITHOUT ROWID"
            )
            connection.execute("CREATE INDEX observations_time ON observations(observed_at)")
            connection.execute(
                "CREATE TABLE coverage_epochs ("
                "family TEXT NOT NULL, source_id TEXT NOT NULL, epoch_id TEXT NOT NULL, "
                "started_at REAL NOT NULL, ended_at REAL, native_cadence_seconds REAL NOT NULL "
                "CHECK (native_cadence_seconds > 0), owner_generation INTEGER NOT NULL "
                "CHECK (owner_generation >= 0), CHECK (ended_at IS NULL OR ended_at >= started_at), "
                "PRIMARY KEY(family, source_id, epoch_id)) WITHOUT ROWID"
            )
            connection.execute("CREATE INDEX coverage_epochs_end ON coverage_epochs(ended_at)")
            connection.execute(
                "CREATE TABLE unavailable_spans ("
                "family TEXT NOT NULL, source_id TEXT NOT NULL, epoch_id TEXT NOT NULL, "
                "started_at REAL NOT NULL, ended_at REAL NOT NULL, "
                "native_cadence_seconds REAL NOT NULL CHECK (native_cadence_seconds > 0), "
                "reason TEXT NOT NULL, owner_generation INTEGER NOT NULL "
                "CHECK (owner_generation >= 0), CHECK (ended_at > started_at), "
                "PRIMARY KEY(family, source_id, epoch_id, started_at, ended_at)) WITHOUT ROWID"
            )
            connection.execute("CREATE INDEX unavailable_spans_end ON unavailable_spans(ended_at)")
            connection.execute(
                "CREATE TABLE usage_atoms ("
                "event_id TEXT NOT NULL, direction TEXT NOT NULL, modality TEXT NOT NULL, "
                "cache_role TEXT NOT NULL, unit TEXT NOT NULL, observed_at REAL NOT NULL, "
                "payload_json TEXT NOT NULL, PRIMARY KEY(event_id, direction, modality, cache_role, unit)) "
                "WITHOUT ROWID"
            )
            connection.execute("CREATE INDEX usage_atoms_time ON usage_atoms(observed_at)")
            connection.execute(
                "CREATE TABLE migration_reconciliation ("
                "migration_id TEXT PRIMARY KEY, completed_at REAL NOT NULL, source_digest TEXT NOT NULL, "
                "details_json TEXT NOT NULL) WITHOUT ROWID"
            )
        Store._validate_schema_shape(connection)

    @staticmethod
    def _validate_schema_shape(connection: sqlite3.Connection) -> None:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE ?",
                ("table", "sqlite_%"),
            )
        }
        if tables != _TABLES:
            raise SchemaMismatchError(
                f"expected current tables {sorted(_TABLES)}, found {sorted(tables)}"
            )
        columns = {
            table: tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))
            for table in tables
        }
        if columns != _COLUMNS:
            raise SchemaMismatchError("current stats table columns do not match the exact schema")

    @staticmethod
    def _upgrade_current_contract(connection: sqlite3.Connection) -> None:
        """Apply current invariant repairs and embed the current writer fence."""

        header = _read_header(connection)
        if (
            header.minimum_writer_protocol >= MIN_WRITER_PROTOCOL
            and header.minimum_writer_build >= MIN_WRITER_BUILD
        ):
            return
        original: tuple[UnavailableSpan, ...] = ()
        normalized: tuple[UnavailableSpan, ...] = ()
        if header.minimum_writer_build < MIN_WRITER_BUILD:
            coverage_conflict = connection.execute(
                "SELECT 1 FROM unavailable_spans AS unavailable "
                "JOIN coverage_epochs AS coverage "
                "ON coverage.family = unavailable.family "
                "AND coverage.source_id = unavailable.source_id "
                "WHERE (coverage.ended_at IS NULL OR coverage.ended_at > unavailable.started_at) "
                "AND coverage.started_at < unavailable.ended_at LIMIT 1"
            ).fetchone()
            if coverage_conflict is not None:
                raise SchemaMismatchError(
                    "current unavailable span overlaps exact coverage; refusing lossy repair"
                )
            rows = connection.execute(
                "SELECT family, source_id, epoch_id, started_at, ended_at, "
                "native_cadence_seconds, reason, owner_generation FROM unavailable_spans "
                "ORDER BY family, source_id, started_at, ended_at, epoch_id, reason"
            ).fetchall()
            original = tuple(
                UnavailableSpan(
                    str(row[0]), str(row[1]), str(row[2]), float(row[3]),
                    float(row[4]), float(row[5]), str(row[6]), int(row[7]),
                )
                for row in rows
            )
            normalized = normalize_unavailable_spans(original)
        generation = header.source_generation
        with _transaction(connection):
            if normalized != original:
                connection.execute("DELETE FROM unavailable_spans")
                connection.executemany(
                    "INSERT INTO unavailable_spans("
                    "family, source_id, epoch_id, started_at, ended_at, "
                    "native_cadence_seconds, reason, owner_generation) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (_unavailable_values(item) for item in normalized),
                )
                generation += 1
            connection.execute(
                "UPDATE schema_meta SET minimum_writer_protocol = ?, "
                "minimum_writer_build = ?, source_generation = ? "
                "WHERE singleton = 1",
                (MIN_WRITER_PROTOCOL, MIN_WRITER_BUILD, generation),
            )

    def close(self) -> None:
        if self._database is not None:
            self._database.close()
            self._database = None

    def __enter__(self) -> Store:
        self._connection()
        return self

    def __exit__(self, _error_type: object, _error: object, _traceback: object) -> None:
        self.close()

    def _connection(self) -> sqlite3.Connection:
        if self._database is None:
            raise StatsCurrentError("stats store is closed")
        return self._database

    def append_batch(
        self,
        *,
        observations: Iterable[Observation] = (),
        coverage_epochs: Iterable[CoverageEpoch] = (),
        usage_atoms: Iterable[UsageAtom] = (),
        usage_tombstones: Iterable[UsageAtomTombstone] = (),
        unavailable_spans: Iterable[UnavailableSpan] = (),
    ) -> AppendResult:
        """Commit one deduplicated source batch and advance one generation."""

        if self.read_only:
            raise StatsCurrentError("stats store reader cannot mutate the database")

        prepared_observations = tuple(_observation_values(item) for item in observations)
        prepared_coverage = tuple(_coverage_values(item) for item in coverage_epochs)
        prepared_usage = tuple(_usage_values(item) for item in usage_atoms)
        prepared_tombstones = tuple(
            _usage_tombstone_values(item) for item in usage_tombstones
        )
        prepared_unavailable = tuple(_unavailable_values(item) for item in unavailable_spans)
        connection = self._connection()
        observations_accepted = coverage_changed = usage_accepted = unavailable_accepted = 0
        tombstones_accepted = tombstones_duplicate = usage_attribution_conflicts = 0
        with _transaction(connection):
            generation = int(connection.execute(
                "SELECT source_generation FROM schema_meta WHERE singleton = 1"
            ).fetchone()[0])
            for values in prepared_observations:
                previous = connection.execute(
                    "SELECT observed_at, epoch_id, owner_generation, payload_json FROM observations "
                    "WHERE event_id = ? AND family = ? AND source_id = ?", values[:3],
                ).fetchone()
                if previous is None:
                    connection.execute(
                        "INSERT INTO observations("
                        "event_id, family, source_id, observed_at, epoch_id, owner_generation, "
                        "payload_json) VALUES(?, ?, ?, ?, ?, ?, ?)", values,
                    )
                    observations_accepted += 1
                elif tuple(previous) != values[3:]:
                    raise StorageValidationError("observation event identity conflicts with stored data")
            for values in prepared_coverage:
                key = values[:3]
                previous = connection.execute(
                    "SELECT started_at, ended_at, native_cadence_seconds, owner_generation "
                    "FROM coverage_epochs WHERE family = ? AND source_id = ? AND epoch_id = ?",
                    key,
                ).fetchone()
                current = values[3:]
                coverage_conflict = connection.execute(
                    "SELECT 1 FROM unavailable_spans WHERE family = ? AND source_id = ? "
                    "AND ended_at > ? AND (? IS NULL OR started_at < ?) LIMIT 1",
                    (values[0], values[1], current[0], current[1], current[1]),
                ).fetchone()
                if coverage_conflict is not None:
                    raise StorageValidationError("coverage epoch overlaps an unavailable span")
                if previous is None:
                    connection.execute(
                        "INSERT INTO coverage_epochs("
                        "family, source_id, epoch_id, started_at, ended_at, native_cadence_seconds, "
                        "owner_generation) VALUES(?, ?, ?, ?, ?, ?, ?)", values,
                    )
                    coverage_changed += 1
                elif tuple(previous) != current:
                    if previous[0] != current[0] or previous[2] != current[2]:
                        raise StorageValidationError("coverage epoch start and cadence are immutable")
                    if previous[1] is not None and (current[1] is None or current[1] < previous[1]):
                        raise StorageValidationError("coverage epoch end cannot move backward")
                    if current[3] < previous[3]:
                        raise StorageValidationError("coverage owner_generation cannot move backward")
                    connection.execute(
                        "UPDATE coverage_epochs SET ended_at = ?, owner_generation = ? "
                        "WHERE family = ? AND source_id = ? AND epoch_id = ?",
                        (current[1], current[3], *key),
                    )
                    coverage_changed += 1
            for values in prepared_usage:
                previous = connection.execute(
                    "SELECT observed_at, payload_json FROM usage_atoms WHERE event_id = ? "
                    "AND direction = ? AND modality = ? AND cache_role = ? AND unit = ?",
                    values[:5],
                ).fetchone()
                if previous is None:
                    connection.execute(
                        "INSERT INTO usage_atoms("
                        "event_id, direction, modality, cache_role, unit, observed_at, payload_json) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?)", values,
                    )
                    usage_accepted += 1
                elif tuple(previous) != values[5:]:
                    repaired = _usage_unknown_model_repair(tuple(previous), values[5:])
                    compatible, agent_changed = _usage_compatible_metadata_change(
                        tuple(previous), values[5:],
                    )
                    if repaired is not None:
                        payload_json, repair_agent_changed = repaired
                        connection.execute(
                            "UPDATE usage_atoms SET payload_json = ? WHERE event_id = ? "
                            "AND direction = ? AND modality = ? AND cache_role = ? AND unit = ?",
                            (payload_json, *values[:5]),
                        )
                        usage_accepted += 1
                        usage_attribution_conflicts += int(repair_agent_changed)
                    elif compatible:
                        usage_attribution_conflicts += int(agent_changed)
                    else:
                        raise UsageAtomIdentityConflict(
                            event_id=str(values[0]),
                            identity_hash=_usage_conflict_hash(tuple(values[:5])),
                            first_payload_hash=_usage_conflict_hash(tuple(previous)),
                            attempted_payload_hash=_usage_conflict_hash(tuple(values[5:])),
                        )
            for values in prepared_tombstones:
                key = values[:5]
                previous = connection.execute(
                    "SELECT observed_at, payload_json FROM usage_atoms WHERE event_id = ? "
                    "AND direction = ? AND modality = ? AND cache_role = ? AND unit = ?",
                    key,
                ).fetchone()
                if previous is None:
                    tombstones_duplicate += 1
                    continue
                payload = _decode_json_object(previous[1], "usage atom payload")
                expected = (
                    values[5], values[6], values[7], values[8], values[9], "codex",
                )
                actual = (
                    float(previous[0]), payload.get("quantity"), payload.get("provider"),
                    payload.get("model"), payload.get("thread_id"),
                    payload.get("execution_source"),
                )
                if actual != expected:
                    raise StorageValidationError(
                        "usage tombstone conflicts with stored data"
                    )
                connection.execute(
                    "DELETE FROM usage_atoms WHERE event_id = ? AND direction = ? "
                    "AND modality = ? AND cache_role = ? AND unit = ?",
                    key,
                )
                tombstones_accepted += 1
            for values in prepared_unavailable:
                previous = connection.execute(
                    "SELECT native_cadence_seconds, reason, owner_generation FROM unavailable_spans "
                    "WHERE family = ? AND source_id = ? AND epoch_id = ? "
                    "AND started_at = ? AND ended_at = ?",
                    values[:5],
                ).fetchone()
                if previous is None:
                    coverage_conflict = connection.execute(
                        "SELECT 1 FROM coverage_epochs WHERE family = ? AND source_id = ? "
                        "AND (ended_at IS NULL OR ended_at > ?) AND started_at < ? LIMIT 1",
                        (values[0], values[1], values[3], values[4]),
                    ).fetchone()
                    if coverage_conflict is not None:
                        raise StorageValidationError("unavailable span overlaps a coverage epoch")
                    unavailable_conflict = connection.execute(
                        "SELECT 1 FROM unavailable_spans WHERE family = ? AND source_id = ? "
                        "AND ended_at > ? AND started_at < ? LIMIT 1",
                        (values[0], values[1], values[3], values[4]),
                    ).fetchone()
                    if unavailable_conflict is not None:
                        raise StorageValidationError("unavailable spans overlap")
                    connection.execute(
                        "INSERT INTO unavailable_spans("
                        "family, source_id, epoch_id, started_at, ended_at, "
                        "native_cadence_seconds, reason, owner_generation) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                        values,
                    )
                    unavailable_accepted += 1
                elif tuple(previous) != values[5:]:
                    raise StorageValidationError("unavailable span identity conflicts with stored data")
            changed = (
                observations_accepted + coverage_changed + usage_accepted
                + unavailable_accepted + tombstones_accepted
            )
            if changed:
                generation += 1
                connection.execute(
                    "UPDATE schema_meta SET source_generation = ? WHERE singleton = 1",
                    (generation,),
                )
        return AppendResult(
            generation,
            observations_accepted,
            len(prepared_observations) - observations_accepted,
            coverage_changed,
            len(prepared_coverage) - coverage_changed,
            usage_accepted,
            len(prepared_usage) - usage_accepted,
            unavailable_accepted,
            len(prepared_unavailable) - unavailable_accepted,
            usage_attribution_conflicts,
            tombstones_accepted,
            tombstones_duplicate,
        )

    def append_observation(self, observation: Observation) -> bool:
        return self.append_batch(observations=(observation,)).observations_accepted == 1

    def append_coverage_epoch(self, coverage: CoverageEpoch) -> bool:
        return self.append_batch(coverage_epochs=(coverage,)).coverage_changed == 1

    def append_usage_atom(self, atom: UsageAtom) -> bool:
        return self.append_batch(usage_atoms=(atom,)).usage_atoms_accepted == 1

    def append_unavailable_span(self, span: UnavailableSpan) -> bool:
        return self.append_batch(unavailable_spans=(span,)).unavailable_spans_accepted == 1

    def record_migration_reconciliation(self, reconciliation: MigrationReconciliation) -> bool:
        if self.read_only:
            raise StatsCurrentError("stats store reader cannot mutate the database")
        values = (
            _validate_text(reconciliation.migration_id, "migration_id"),
            _validate_timestamp(reconciliation.completed_at, "completed_at"),
            _validate_text(reconciliation.source_digest, "source_digest"),
            _encode_json_object(reconciliation.details, "details"),
        )
        connection = self._connection()
        with _transaction(connection):
            changed = connection.execute(
                "INSERT OR IGNORE INTO migration_reconciliation("
                "migration_id, completed_at, source_digest, details_json) VALUES(?, ?, ?, ?)",
                values,
            ).rowcount
        return changed == 1

    def read_snapshot(
        self,
        *,
        dirty_intervals: Iterable[tuple[int | float, int | float]] | None = None,
        private_observation_sources: int = 0,
    ) -> StoreSnapshot:
        """Read all coverage plus either full or dirty-window original facts."""

        with self.pinned_snapshot(
            dirty_intervals=dirty_intervals,
            private_observation_sources=private_observation_sources,
        ) as read:
            return read()

    @contextmanager
    def pinned_snapshot(
        self,
        *,
        dirty_intervals: Iterable[tuple[int | float, int | float]] | None = None,
        private_observation_sources: int = 0,
    ) -> Iterator[Callable[[], StoreSnapshot]]:
        """Pin one WAL generation before yielding its potentially longer row scan."""

        connection = self._connection()
        intervals = _coalesced_dirty_intervals(dirty_intervals)
        time_clause, time_parameters = _time_clause(intervals)
        private_limit = _validate_nonnegative_integer(
            private_observation_sources,
            "private_observation_sources",
        )
        if private_limit > MAX_PRIVATE_OBSERVATION_SOURCES:
            raise StorageValidationError(
                f"private_observation_sources must be at most {MAX_PRIVATE_OBSERVATION_SOURCES}"
            )
        observation_clause = time_clause
        observation_parameters = time_parameters
        if intervals is not None and private_limit:
            observation_clause += (
                " OR (family = 'browser' AND source_id IN ("
                "SELECT source_id FROM observations WHERE family = 'browser' "
                "GROUP BY source_id ORDER BY MAX(observed_at) DESC, source_id LIMIT ?))"
            )
            observation_parameters = (*time_parameters, private_limit)
        with _transaction(connection):
            header = _read_header(connection)

            def read() -> StoreSnapshot:
                observation_rows = connection.execute(
                    "SELECT event_id, family, source_id, observed_at, epoch_id, owner_generation, payload_json "
                    "FROM observations" + observation_clause
                    + " ORDER BY observed_at, family, source_id",
                    observation_parameters,
                ).fetchall()
                coverage_rows = connection.execute(
                    "SELECT family, source_id, epoch_id, started_at, ended_at, "
                    "native_cadence_seconds, owner_generation FROM coverage_epochs "
                    "ORDER BY started_at, family, source_id, epoch_id"
                ).fetchall()
                usage_rows = connection.execute(
                    "SELECT event_id, direction, modality, cache_role, unit, observed_at, payload_json "
                    "FROM usage_atoms" + time_clause
                    + " ORDER BY observed_at, event_id, direction, modality, cache_role, unit",
                    time_parameters,
                ).fetchall()
                unavailable_rows = connection.execute(
                    "SELECT family, source_id, epoch_id, started_at, ended_at, "
                    "native_cadence_seconds, reason, owner_generation FROM unavailable_spans "
                    "ORDER BY started_at, family, source_id, epoch_id"
                ).fetchall()
                reconciliation_rows = connection.execute(
                    "SELECT migration_id, completed_at, source_digest, details_json "
                    "FROM migration_reconciliation ORDER BY completed_at, migration_id"
                ).fetchall()
                return StoreSnapshot(
                    schema=SchemaMetadata(
                        header.schema_version,
                        header.minimum_writer_protocol,
                        header.minimum_writer_build,
                        header.source_generation,
                    ),
                    observations=tuple(
                        Observation(
                            str(row[0]), str(row[1]), str(row[2]), float(row[3]),
                            str(row[4]), int(row[5]),
                            _decode_json_object(row[6], "observation payload"),
                        )
                        for row in observation_rows
                    ),
                    coverage_epochs=tuple(
                        CoverageEpoch(
                            str(row[0]), str(row[1]), str(row[2]), float(row[3]),
                            None if row[4] is None else float(row[4]),
                            float(row[5]), int(row[6]),
                        )
                        for row in coverage_rows
                    ),
                    usage_atoms=tuple(
                        UsageAtom(
                            str(row[0]), str(row[1]), str(row[2]), str(row[3]),
                            str(row[4]), float(row[5]),
                            _decode_json_object(row[6], "usage atom payload"),
                        )
                        for row in usage_rows
                    ),
                    migration_reconciliation=tuple(
                        MigrationReconciliation(
                            str(row[0]), float(row[1]), str(row[2]),
                            _decode_json_object(row[3], "migration reconciliation details"),
                        )
                        for row in reconciliation_rows
                    ),
                    unavailable_spans=tuple(
                        UnavailableSpan(
                            str(row[0]), str(row[1]), str(row[2]), float(row[3]),
                            float(row[4]), float(row[5]), str(row[6]), int(row[7]),
                        )
                        for row in unavailable_rows
                    ),
                )

            yield read

    def prune(self, *, now: float) -> PruneResult:
        if self.read_only:
            raise StatsCurrentError("stats store reader cannot mutate the database")
        cutoff = _validate_timestamp(now, "now") - RETENTION_SECONDS
        connection = self._connection()
        with _transaction(connection):
            observations = connection.execute(
                "DELETE FROM observations WHERE observed_at < ?", (cutoff,)
            ).rowcount
            usage_atoms = connection.execute(
                "DELETE FROM usage_atoms WHERE observed_at < ?", (cutoff,)
            ).rowcount
            coverage_deleted = connection.execute(
                "DELETE FROM coverage_epochs WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff,),
            ).rowcount
            coverage_clipped = connection.execute(
                "UPDATE coverage_epochs SET started_at = ? "
                "WHERE started_at < ? AND (ended_at IS NULL OR ended_at >= ?)",
                (cutoff, cutoff, cutoff),
            ).rowcount
            unavailable_deleted = connection.execute(
                "DELETE FROM unavailable_spans WHERE ended_at < ?", (cutoff,)
            ).rowcount
            unavailable_clipped = connection.execute(
                "UPDATE unavailable_spans SET started_at = ? "
                "WHERE started_at < ? AND ended_at >= ?",
                (cutoff, cutoff, cutoff),
            ).rowcount
            changed = (
                observations + usage_atoms + coverage_deleted + coverage_clipped
                + unavailable_deleted + unavailable_clipped
            )
            generation = int(connection.execute(
                "SELECT source_generation FROM schema_meta WHERE singleton = 1"
            ).fetchone()[0])
            if changed:
                generation += 1
                connection.execute(
                    "UPDATE schema_meta SET source_generation = ? WHERE singleton = 1",
                    (generation,),
                )
        return PruneResult(
            observations, coverage_deleted, coverage_clipped, usage_atoms, generation,
            unavailable_deleted, unavailable_clipped,
        )


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN")
    committed = False
    try:
        yield
        connection.execute("COMMIT")
        committed = True
    finally:
        if not committed and connection.in_transaction:
            connection.execute("ROLLBACK")


def require_compatible_writer(
    path: str | Path,
    *,
    writer_protocol: int = MIN_WRITER_PROTOCOL,
    writer_build: int = MIN_WRITER_BUILD,
) -> None:
    """Check the state fence and existing header without creating or mutating files."""

    database_path = Path(path)
    protocol = _validate_nonnegative_integer(writer_protocol, "writer_protocol")
    build = _validate_nonnegative_integer(writer_build, "writer_build")
    _validate_database_path(database_path)
    Store._preflight_fence(database_path, protocol, build)
    if database_path.is_file() and database_path.stat().st_size:
        Store._preflight(database_path, protocol, build)
